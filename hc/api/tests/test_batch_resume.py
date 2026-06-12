from __future__ import annotations

from datetime import timedelta as td

from django.utils.timezone import now

from hc.api.models import Check, Flip
from hc.test import BaseTestCase


class BatchResumeTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = "/api/v3/checks/batch/resume"

        self.check1 = Check.objects.create(
            project=self.project, name="Check 1", status="paused"
        )
        self.check2 = Check.objects.create(
            project=self.project, name="Check 2", status="paused"
        )
        self.check3 = Check.objects.create(
            project=self.project, name="Check 3", status="paused"
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

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.status, "new")
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.status, "new")

        # Check 3 should still be paused
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.status, "paused")

        # Flips should be created
        self.assertEqual(Flip.objects.count(), 2)
        for flip in Flip.objects.all():
            self.assertEqual(flip.old_status, "paused")
            self.assertEqual(flip.new_status, "new")
            self.assertTrue(flip.processed)

    def test_it_resumes_by_tags(self) -> None:
        self.check1.tags = "production"
        self.check1.save()
        self.check2.tags = "production"
        self.check2.save()
        self.check3.tags = "staging"
        self.check3.save()

        r = self.post({"tags": ["production"]})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.status, "new")
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.status, "paused")

    def test_it_resumes_all(self) -> None:
        r = self.post({"all": True})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)

        for check in [self.check1, self.check2, self.check3]:
            check.refresh_from_db()
            self.assertEqual(check.status, "new")

    def test_it_rejects_non_paused_checks(self) -> None:
        self.check1.status = "up"
        self.check1.save()

        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 409)

        # Check 2 should NOT have been modified (transactional rollback)
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.status, "paused")

        # No flips should exist (rolled back)
        self.assertFalse(Flip.objects.exists())

    def test_it_clears_last_start_last_ping_alert_after(self) -> None:
        self.check1.last_start = now()
        self.check1.last_ping = now()
        self.check1.alert_after = now() + td(hours=1)
        self.check1.save()

        r = self.post({"uuids": [str(self.check1.code)]})
        self.assertEqual(r.status_code, 200)

        self.check1.refresh_from_db()
        self.assertIsNone(self.check1.last_start)
        self.assertIsNone(self.check1.last_ping)
        self.assertIsNone(self.check1.alert_after)

    def test_it_handles_options(self) -> None:
        r = self.client.options(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertIn("POST", r["Access-Control-Allow-Methods"])

    def test_it_only_allows_post(self) -> None:
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 405)

    def test_it_validates_ownership(self) -> None:
        check = Check.objects.create(project=self.bobs_project, status="paused")
        r = self.post({"uuids": [str(check.code)]})
        self.assertEqual(r.status_code, 404)

    def test_it_handles_missing_uuid(self) -> None:
        r = self.post({"uuids": ["07c2f548-9850-4b27-af5d-6c9dc157ec02"]})
        self.assertEqual(r.status_code, 404)

    def test_it_handles_missing_api_key(self) -> None:
        r = self.csrf_client.post(
            self.url, {"all": True}, content_type="application/json"
        )
        self.assertEqual(r.status_code, 401)

    def test_it_rejects_readonly_key(self) -> None:
        self.project.api_key_readonly = "R" * 32
        self.project.save()

        r = self.post({"all": True}, api_key="R" * 32)
        self.assertEqual(r.status_code, 401)

    def test_it_rejects_non_dict_post_body(self) -> None:
        r = self.csrf_client.post(self.url, "123", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_it_rejects_no_selector(self) -> None:
        r = self.post({})
        self.assertEqual(r.status_code, 400)
