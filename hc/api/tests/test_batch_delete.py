from __future__ import annotations

from hc.api.models import Check
from hc.test import BaseTestCase


class BatchDeleteTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = "/api/v3/checks/batch/delete"

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
        code1 = self.check1.code
        code2 = self.check2.code
        uuids = [str(code1), str(code2)]
        r = self.post({"uuids": uuids})

        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 2)

        # Deleted checks should be gone
        self.assertFalse(Check.objects.filter(code=code1).exists())
        self.assertFalse(Check.objects.filter(code=code2).exists())

        # Check 3 should still exist
        self.assertTrue(Check.objects.filter(code=self.check3.code).exists())

    def test_it_deletes_by_tags(self) -> None:
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

        self.assertFalse(Check.objects.filter(code=self.check1.code).exists())
        self.assertFalse(Check.objects.filter(code=self.check2.code).exists())
        self.assertTrue(Check.objects.filter(code=self.check3.code).exists())

    def test_it_deletes_all(self) -> None:
        r = self.post({"all": True})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual(doc["count"], 3)

        self.assertEqual(
            Check.objects.filter(project=self.project).count(), 0
        )

    def test_it_returns_check_dicts(self) -> None:
        uuids = [str(self.check1.code)]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 200)

        doc = r.json()
        self.assertEqual(doc["count"], 1)
        self.assertEqual(len(doc["checks"]), 1)
        self.assertEqual(doc["checks"][0]["name"], "Check 1")

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

    def test_it_is_transactional_on_missing_uuid(self) -> None:
        """If one UUID in the list is missing, none should be deleted."""
        uuids = [str(self.check1.code), "07c2f548-9850-4b27-af5d-6c9dc157ec02"]
        r = self.post({"uuids": uuids})
        self.assertEqual(r.status_code, 404)

        # check1 should NOT have been deleted
        self.assertTrue(Check.objects.filter(code=self.check1.code).exists())

    def test_it_rejects_non_dict_post_body(self) -> None:
        r = self.csrf_client.post(self.url, "123", content_type="application/json")
        self.assertEqual(r.status_code, 400)
