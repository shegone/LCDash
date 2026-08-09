"""Knowledge-upload routes: admin-only, fail closed, honest errors.

Service behavior is covered in test_knowledge_upload_service.py; what is
proved here is the plumbing: the admin gate on every route, multipart upload
reaching the service with the verified uploader identity, service refusals
surfacing with their reasons, and the sync route distinguishing
"already running" (409) from plain refusals (400).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app
from app.services.knowledge_upload_service import KnowledgeUploadError


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


class AdminKnowledgeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()
        self.service = MagicMock()

    def _sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _with_service(self):
        patcher = patch(
            "app.main._knowledge_upload_service", return_value=self.service
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_every_route_denies_non_admins_uniformly(self):
        self._with_service()
        routes = (
            ("GET", "/admin/knowledge", None, None),
            ("GET", "/api/admin/knowledge/documents", None, None),
            (
                "POST", "/api/admin/knowledge/documents",
                None, {"file": ("a.pdf", b"%PDF-x", "application/pdf")},
            ),
            (
                "POST", "/api/admin/knowledge/documents/remove",
                {"destination": "mae", "document_id": "eA"}, None,
            ),
            ("POST", "/api/admin/knowledge/sync", None, None),
        )
        for identity in (
            _identity("s@911logan.com", "lcdash-pilot-supervisor"),
            _identity("u@911logan.com", "lcdash-pilot-user"),
            None,
        ):
            self._sign_in_as(identity)
            for method, path, body, files in routes:
                with self.subTest(identity=identity and identity.email, path=path):
                    response = self.client.request(
                        method, path, json=body,
                        files=files, data={"destination": "mae"} if files else None,
                    )
                    self.assertEqual(response.status_code, 403)
        self.service.upload_document.assert_not_called()
        self.service.list_documents.assert_not_called()

    def test_list_bundles_documents_and_ingestion_status(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.list_documents.return_value = [{"filename": "a.pdf"}]
        self.service.ingestion_status.return_value = {"job_id": "j1", "status": "COMPLETE"}
        body = self.client.get("/api/admin/knowledge/documents").json()
        self.assertEqual(body["documents"], [{"filename": "a.pdf"}])
        self.assertEqual(body["ingestion"]["status"], "COMPLETE")

    def test_unconfigured_sync_status_does_not_hide_the_document_list(self):
        """Sync being unprovisioned (blank data source id) must degrade to
        NEVER_RUN, not 4xx away the whole page."""
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.list_documents.return_value = []
        self.service.ingestion_status.side_effect = KnowledgeUploadError("not configured")
        body = self.client.get("/api/admin/knowledge/documents").json()
        self.assertEqual(body["documents"], [])
        self.assertEqual(body["ingestion"], {"job_id": "", "status": "NEVER_RUN"})

    def test_upload_reaches_service_with_verified_uploader(self):
        """Attribution comes from the verified identity, never from the form."""
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.upload_document.return_value = {"filename": "sop.pdf"}
        response = self.client.post(
            "/api/admin/knowledge/documents",
            files={"file": ("sop.pdf", b"%PDF-1.7 fake", "application/pdf")},
            data={"destination": "jack"},
        )
        self.assertEqual(response.status_code, 200)
        self.service.upload_document.assert_called_once_with(
            "jack", "sop.pdf", b"%PDF-1.7 fake",
            uploaded_by="tedsparks@911logan.com",
        )

    def test_service_refusal_surfaces_as_400_with_reason(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.upload_document.side_effect = KnowledgeUploadError(
            "Only PDF documents can be uploaded."
        )
        response = self.client.post(
            "/api/admin/knowledge/documents",
            files={"file": ("x.exe", b"MZ", "application/octet-stream")},
            data={"destination": "mae"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF", response.json()["detail"])

    def test_remove_carries_the_acting_admin(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.remove_document.return_value = {"removed": True}
        self.client.post(
            "/api/admin/knowledge/documents/remove",
            json={"destination": "mae", "document_id": "eA"},
        )
        self.service.remove_document.assert_called_once_with(
            "mae", "eA", removed_by="tedsparks@911logan.com"
        )

    def test_sync_conflict_is_409_other_refusals_400(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.start_ingestion.side_effect = KnowledgeUploadError(
            "An ingestion sync is already running; wait for it to finish."
        )
        self.assertEqual(
            self.client.post("/api/admin/knowledge/sync").status_code, 409
        )
        self.service.start_ingestion.side_effect = KnowledgeUploadError("not configured")
        self.assertEqual(
            self.client.post("/api/admin/knowledge/sync").status_code, 400
        )

    def test_listing_failure_is_502_not_an_empty_page(self):
        """An S3 outage must read as an outage -- an empty list would tell the
        admin their uploads are gone."""
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.list_documents.side_effect = KnowledgeUploadError(
            "Uploaded document listing failed."
        )
        response = self.client.get("/api/admin/knowledge/documents")
        self.assertEqual(response.status_code, 502)

    def test_nav_shows_knowledge_uploads_to_admin_only(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        page = self.client.get("/admin/knowledge")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Knowledge Uploads", page.text)

        self._sign_in_as(_identity("s@911logan.com", "lcdash-pilot-supervisor"))
        reports = self.client.get("/reports")
        self.assertNotIn("Knowledge Uploads", reports.text)


if __name__ == "__main__":
    unittest.main()
