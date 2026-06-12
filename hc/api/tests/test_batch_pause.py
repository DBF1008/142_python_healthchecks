from __future__ import annotations

from datetime import timedelta as td

from django.utils.timezone import now

from hc.api.models import Check, Channel, Flip
from hc.test import BaseTestCase


class BatchPauseTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = "/api/v3/checks/batch/pause"

        self.check1 = Check.objects.create(
            project=self.project, name="Check 1", status="up"
        )
        self.check2 = Check.objects.create(
            project=self.project, name="Check 2", status="up"
        )
        self.check3 = Check.objects.create(
            project=self.project, name="Check 3", status="up"
        )

    def post(self, data: dict, api_key: str = "X" * 32):
        return self.csrf_client.post(
            self.url, data, content_type="application/json", HTTP_X_API_KEY=api_key
        )

    def test_it_works(self) -> None:
        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids})

        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)
        self.assertEqual(len(doc["checks"]), 2)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.status, "paused")
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.status, "paused")

        # Check 3 should not be affected
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.status, "up")

        # Flips should be created for both checks
        self.assertEqual(Flip.objects.count(), 2)
        for flip in Flip.objects.all():
            self.assertEqual(flip.new_status, "paused")
            self.assertTrue(flip.processed)

    def test_it_pauses_by_tags(self) -> None:
        self.check1.tags = "production web"
        self.check1.save()
        self.check2.tags = "production api"
        self.check2.save()
        self.check3.tags = "staging"
        self.check3.save()

        r = self.post({"tags": ["production"]})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.status, "paused")
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.status, "paused")
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.status, "up")

    def test_it_pauses_all(self) -> None:
        r = self.post({"all": True})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)

        for check in [self.check1, self.check2, self.check3]:
            check.refresh_from_db()
            self.assertEqual(check.status, "paused")

    def test_it_is_idempotent(self) -> None:
        self.check1.status = "paused"
        self.check1.save()
        self.check2.status = "paused"
        self.check2.save()

        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 200)

        # No flips should be created since both were already paused
        self.assertFalse(Flip.objects.exists())

    def test_it_handles_mixed_paused_and_up(self) -> None:
        self.check1.status = "paused"
        self.check1.save()
        # check2 is "up"

        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 200)

        # Only one flip should be created (for check2 which was "up")
        self.assertEqual(Flip.objects.count(), 1)
        self.assertEqual(Flip.objects.first().owner_id, self.check2.id)

    def test_it_handles_options(self) -> None:
        r = self.client.options(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertIn("POST", r["Access-Control-Allow-Methods"])

    def test_it_only_allows_post(self) -> None:
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 405)

    def test_it_validates_ownership(self) -> None:
        check = Check.objects.create(project=self.bobs_project, status="up")
        r = self.post({"uuids": [str(check.code)]})
        self.assertEqual(r.status_code, 404)

    def test_it_handles_missing_uuid(self) -> None:
        r = self.post({"uuids": ["07c2f548-9850-4b27-af5d-6c9dc157ec02"]})
        self.assertEqual(r.status_code, 404)

    def test_it_handles_invalid_uuid(self) -> None:
        r = self.post({"uuids": ["not-a-uuid"]})
        self.assertEqual(r.status_code, 400)

    def test_it_rejects_multiple_selectors(self) -> None:
        r = self.post({"uuids": [str(self.check1.code)], "tags": ["foo"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("exactly one", r.json()["error"])

    def test_it_rejects_no_selector(self) -> None:
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertIn("exactly one", r.json()["error"])

    def test_it_rejects_readonly_key(self) -> None:
        # Create a readonly key for the project
        self.project.api_key_readonly = "R" * 32
        self.project.save()

        r = self.post({"all": True}, api_key="R" * 32)
        self.assertEqual(r.status_code, 401)

    def test_it_handles_missing_api_key(self) -> None:
        r = self.csrf_client.post(
            self.url, {"all": True}, content_type="application/json"
        )
        self.assertEqual(r.status_code, 401)

    def test_it_handles_empty_tags(self) -> None:
        r = self.post({"tags": []})
        self.assertEqual(r.status_code, 400)

    def test_it_handles_no_matching_checks(self) -> None:
        r = self.post({"tags": ["nonexistent"]})
        self.assertEqual(r.status_code, 400)

    def test_it_clears_next_nag_date(self) -> None:
        self.profile.nag_period = td(hours=1)
        self.profile.next_nag_date = now() + td(minutes=30)
        self.profile.save()

        self.post({"all": True})

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.next_nag_date)

    def test_it_returns_correct_count(self) -> None:
        r = self.post({"all": True})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)
        self.assertEqual(len(doc["checks"]), 3)

    def test_it_accepts_api_key_in_body(self) -> None:
        payload = {"api_key": "X" * 32, "all": True}
        r = self.csrf_client.post(self.url, payload, content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_it_rejects_non_dict_post_body(self) -> None:
        r = self.csrf_client.post(self.url, "123", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_it_clears_last_start_and_alert_after(self) -> None:
        self.check1.last_start = now()
        self.check1.alert_after = self.check1.last_start + td(hours=1)
        self.check1.save()

        uuids = [str(self.check1.code)]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 200)

        self.check1.refresh_from_db()
        self.assertIsNone(self.check1.last_start)
        self.assertIsNone(self.check1.alert_after)

    def test_tags_use_and_semantics(self) -> None:
        self.check1.tags = "production web"
        self.check1.save()
        self.check2.tags = "production api"
        self.check2.save()

        # Require both "production" and "web" — only check1 matches
        r = self.post({"tags": ["production", "web"]})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 1)
