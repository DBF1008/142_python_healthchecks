from __future__ import annotations

from datetime import timedelta as td

from hc.api.models import Check, Channel
from hc.test import BaseTestCase


class BatchUpdateTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = "/api/v3/checks/batch/update"

        self.check1 = Check.objects.create(
            project=self.project, name="Check 1", status="up"
        )
        self.check2 = Check.objects.create(
            project=self.project, name="Check 2", status="up"
        )
        self.check3 = Check.objects.create(
            project=self.project, name="Check 3", status="up"
        )

        self.channel1 = Channel.objects.create(
            project=self.project, name="Channel 1", kind="email"
        )
        self.channel2 = Channel.objects.create(
            project=self.project, name="Channel 2", kind="webhook"
        )

    def post(self, data: dict, api_key: str = "X" * 32):
        return self.csrf_client.post(
            self.url, data, content_type="application/json", HTTP_X_API_KEY=api_key
        )

    def test_it_updates_grace(self) -> None:
        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids, "grace": 3600})

        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.grace, td(seconds=3600))
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.grace, td(seconds=3600))

        # Check 3 should be unchanged
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.grace, td(hours=1))  # default

    def test_it_updates_timeout(self) -> None:
        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids, "timeout": 7200})

        self.assertEqual(r.status_code, 200)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.timeout, td(seconds=7200))
        self.check2.refresh_from_db()
        self.assertEqual(self.check2.timeout, td(seconds=7200))

    def test_it_updates_grace_and_timeout(self) -> None:
        r = self.post({"all": True, "grace": 1800, "timeout": 3600})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)

        for check in [self.check1, self.check2, self.check3]:
            check.refresh_from_db()
            self.assertEqual(check.grace, td(seconds=1800))
            self.assertEqual(check.timeout, td(seconds=3600))

    def test_it_updates_channels(self) -> None:
        uuids = [str(self.check1.code), str(self.check2.code)]
        r = self.post({"uuids": uuids, "channels": str(self.channel1.code)})

        self.assertEqual(r.status_code, 200)

        self.assertEqual(self.check1.channel_set.count(), 1)
        self.assertIn(self.channel1, self.check1.channel_set.all())
        self.assertEqual(self.check2.channel_set.count(), 1)
        self.assertIn(self.channel1, self.check2.channel_set.all())

    def test_it_updates_channels_and_timing(self) -> None:
        r = self.post(
            {
                "all": True,
                "channels": str(self.channel1.code),
                "grace": 1200,
                "timeout": 2400,
            }
        )
        self.assertEqual(r.status_code, 200)

        for check in [self.check1, self.check2, self.check3]:
            check.refresh_from_db()
            self.assertEqual(check.grace, td(seconds=1200))
            self.assertEqual(check.timeout, td(seconds=2400))
            self.assertEqual(check.channel_set.count(), 1)

    def test_it_updates_by_tags(self) -> None:
        self.check1.tags = "production"
        self.check1.save()
        self.check2.tags = "production"
        self.check2.save()
        self.check3.tags = "staging"
        self.check3.save()

        r = self.post({"tags": ["production"], "grace": 600})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)

        self.check1.refresh_from_db()
        self.assertEqual(self.check1.grace, td(seconds=600))
        self.check3.refresh_from_db()
        self.assertEqual(self.check3.grace, td(hours=1))  # default

    def test_it_updates_all(self) -> None:
        r = self.post({"all": True, "grace": 900})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)

        for check in [self.check1, self.check2, self.check3]:
            check.refresh_from_db()
            self.assertEqual(check.grace, td(seconds=900))

    def test_it_handles_asterisk_channels(self) -> None:
        r = self.post({"all": True, "channels": "*"})
        self.assertEqual(r.status_code, 200)

        for check in [self.check1, self.check2, self.check3]:
            self.assertEqual(check.channel_set.count(), 2)

    def test_it_handles_empty_channels(self) -> None:
        # First assign channels
        self.check1.channel_set.add(self.channel1)
        self.check2.channel_set.add(self.channel2)

        r = self.post({"all": True, "channels": ""})
        self.assertEqual(r.status_code, 200)

        for check in [self.check1, self.check2, self.check3]:
            self.assertEqual(check.channel_set.count(), 0)

    def test_it_rejects_bad_channel(self) -> None:
        original_grace = self.check1.grace

        r = self.post(
            {"all": True, "channels": "nonexistent-channel-uuid", "grace": 600}
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid channel", r.json()["error"])

        # No changes should have been applied (transactional rollback)
        self.check1.refresh_from_db()
        self.assertEqual(self.check1.grace, original_grace)

    def test_it_rejects_no_update_fields(self) -> None:
        r = self.post({"all": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least one", r.json()["error"])

    def test_it_handles_options(self) -> None:
        r = self.client.options(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertIn("POST", r["Access-Control-Allow-Methods"])

    def test_it_only_allows_post(self) -> None:
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 405)

    def test_it_validates_ownership(self) -> None:
        check = Check.objects.create(project=self.bobs_project, status="up")
        r = self.post({"uuids": [str(check.code)], "grace": 600})
        self.assertEqual(r.status_code, 404)

    def test_it_handles_missing_uuid(self) -> None:
        r = self.post(
            {"uuids": ["07c2f548-9850-4b27-af5d-6c9dc157ec02"], "grace": 600}
        )
        self.assertEqual(r.status_code, 404)

    def test_it_rejects_out_of_range_timeout(self) -> None:
        r = self.post({"all": True, "timeout": 30})  # Below minimum of 60
        self.assertEqual(r.status_code, 400)

    def test_it_rejects_out_of_range_grace(self) -> None:
        r = self.post({"all": True, "grace": 30})  # Below minimum of 60
        self.assertEqual(r.status_code, 400)

    def test_it_handles_missing_api_key(self) -> None:
        r = self.csrf_client.post(
            self.url, {"all": True, "grace": 600}, content_type="application/json"
        )
        self.assertEqual(r.status_code, 401)

    def test_it_rejects_readonly_key(self) -> None:
        self.project.api_key_readonly = "R" * 32
        self.project.save()

        r = self.post({"all": True, "grace": 600}, api_key="R" * 32)
        self.assertEqual(r.status_code, 401)

    def test_it_rejects_non_dict_post_body(self) -> None:
        r = self.csrf_client.post(self.url, "123", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_it_accepts_api_key_in_body(self) -> None:
        payload = {"api_key": "X" * 32, "all": True, "grace": 600}
        r = self.csrf_client.post(self.url, payload, content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_it_updates_channels_by_name(self) -> None:
        r = self.post({"all": True, "channels": "Channel 1"})
        self.assertEqual(r.status_code, 200)

        for check in [self.check1, self.check2, self.check3]:
            self.assertEqual(check.channel_set.count(), 1)
            self.assertIn(self.channel1, check.channel_set.all())
