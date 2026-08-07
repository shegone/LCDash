"""Route-level contracts: knowledge library pages and PDF serving use the
S3-backed cloud library in cloud mode, and the existing Postgres/filesystem
path unchanged on-prem."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.cloud_document_library import (
    CloudDocumentLibraryUnavailable,
    LibraryDocument,
)


SAMPLE_DOCUMENTS = (
    LibraryDocument(
        document_id="R2FybWluIEdQUyAyLjYgSW5zdGFsbGF0aW9uIEd1aWRlLnBkZg",
        title="Garmin GPS 2.6 Installation Guide",
        relative_path="Garmin GPS 2.6 Installation Guide.pdf",
        size_bytes=660049,
    ),
)


class KnowledgeLibraryPageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.cloud_document_library.list_documents")
    def test_cloud_mode_knowledge_page_lists_documents_from_s3(self, list_mock):
        list_mock.return_value = SAMPLE_DOCUMENTS
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/knowledge")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Garmin GPS 2.6 Installation Guide", response.text)
        self.assertNotIn("waiting for its first server sync", response.text)
        list_mock.assert_called_once_with("centralsquare")

    @patch("app.main.cloud_document_library.list_documents")
    def test_cloud_mode_provider_failure_falls_back_to_empty_state(self, list_mock):
        list_mock.side_effect = CloudDocumentLibraryUnavailable("boom")
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/knowledge")

        self.assertEqual(response.status_code, 200)

    @patch("app.main.cloud_document_library.list_documents")
    def test_cloud_mode_mindshare_library_lists_documents_from_s3(self, list_mock):
        list_mock.return_value = SAMPLE_DOCUMENTS
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/mindshare/library")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Garmin GPS 2.6 Installation Guide", response.text)
        list_mock.assert_called_once_with("mindshare")

    @patch("app.main.list_knowledge_documents")
    @patch("app.main.cloud_document_library.list_documents")
    def test_on_prem_mode_never_calls_the_cloud_library(
        self, cloud_list_mock, onprem_list_mock
    ):
        onprem_list_mock.return_value = []
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get("/knowledge")

        self.assertEqual(response.status_code, 200)
        cloud_list_mock.assert_not_called()
        onprem_list_mock.assert_called_once()


class KnowledgeDocumentPdfRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.cloud_document_library.fetch_document")
    def test_cloud_mode_serves_pdf_bytes_inline_by_default(self, fetch_mock):
        fetch_mock.return_value = (b"%PDF-1.4 fake", "Garmin GPS 2.6 Installation Guide.pdf")
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get(
                "/knowledge/documents/centralsquare/"
                "R2FybWluIEdQUyAyLjYgSW5zdGFsbGF0aW9uIEd1aWRlLnBkZg"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4 fake")
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        fetch_mock.assert_called_once_with(
            "centralsquare",
            "R2FybWluIEdQUyAyLjYgSW5zdGFsbGF0aW9uIEd1aWRlLnBkZg",
        )

    @patch("app.main.cloud_document_library.fetch_document")
    def test_cloud_mode_download_query_param_sets_attachment(self, fetch_mock):
        fetch_mock.return_value = (b"%PDF-1.4 fake", "Guide.pdf")
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get(
                "/knowledge/documents/centralsquare/abc?download=true"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])

    @patch("app.main.cloud_document_library.fetch_document")
    def test_cloud_mode_missing_document_is_404(self, fetch_mock):
        fetch_mock.return_value = None
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/knowledge/documents/centralsquare/nope")

        self.assertEqual(response.status_code, 404)

    @patch("app.main.get_knowledge_document_file")
    @patch("app.main.cloud_document_library.fetch_document")
    def test_on_prem_mode_never_calls_the_cloud_library_and_rejects_non_numeric_id(
        self, cloud_fetch_mock, onprem_fetch_mock
    ):
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get(
                "/knowledge/documents/centralsquare/not-a-number"
            )

        self.assertEqual(response.status_code, 404)
        cloud_fetch_mock.assert_not_called()
        onprem_fetch_mock.assert_not_called()

    @patch("app.main.get_knowledge_document_file")
    @patch("app.main.cloud_document_library.fetch_document")
    def test_on_prem_mode_still_accepts_a_numeric_id(
        self, cloud_fetch_mock, onprem_fetch_mock
    ):
        onprem_fetch_mock.return_value = None
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get("/knowledge/documents/centralsquare/42")

        self.assertEqual(response.status_code, 404)
        cloud_fetch_mock.assert_not_called()
        onprem_fetch_mock.assert_called_once_with(42, "centralsquare")


if __name__ == "__main__":
    unittest.main()
