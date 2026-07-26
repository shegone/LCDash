import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mae_service import ask_mae


class KnowledgePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.list_knowledge_documents")
    @patch("app.main.get_knowledge_status")
    def test_knowledge_page_lists_indexed_manuals(
        self,
        status_mock,
        documents_mock,
    ):
        status_mock.return_value = {
            "configured": True,
            "connected": True,
            "documents": 1,
            "chunks": 12,
            "index_state": {"status": "ready"},
            "message": "",
        }
        documents_mock.return_value = [
            {
                "title": "CAD Administration Guide",
                "file_name": "cad-admin.pdf",
                "page_count": 50,
                "chunk_count": 120,
                "indexed_at": "2026-07-26T12:00:00-04:00",
            }
        ]

        response = self.client.get("/knowledge")

        self.assertEqual(response.status_code, 200)
        self.assertIn("CentralSquare Knowledge Library", response.text)
        self.assertIn("CAD Administration Guide", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    @patch("app.main.list_knowledge_documents")
    @patch("app.main.get_knowledge_status")
    def test_knowledge_status_endpoint_is_not_cached(
        self,
        status_mock,
        documents_mock,
    ):
        status_mock.return_value = {
            "configured": True,
            "connected": True,
            "documents": 2,
            "chunks": 20,
            "index_state": {"status": "ready"},
            "message": "",
        }
        documents_mock.return_value = []

        response = self.client.get("/api/knowledge/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["documents"], 2)
        self.assertEqual(response.headers["cache-control"], "no-store")


class MAEKnowledgeRoutingTests(unittest.TestCase):
    @patch("app.services.mae_service.get_live_operations_snapshot")
    @patch("app.services.mae_service.get_mae_unit_snapshot")
    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.search_knowledge")
    def test_procedural_question_uses_documents_without_live_cad(
        self,
        search_mock,
        post_mock,
        unit_mock,
        operations_mock,
    ):
        search_mock.return_value = [
            {
                "title": "CAD Administration Guide",
                "file_name": "cad-admin.pdf",
                "page_number": 27,
                "content": "Open Machine Settings and select CAD Terminal ID.",
                "indexed_at": "2026-07-26T12:00:00-04:00",
                "rank": 0.8,
            }
        ]
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {
                "content": (
                    "Open Machine Settings and select CAD Terminal ID "
                    "(CAD Administration Guide, page 27)."
                )
            }
        }
        post_mock.return_value = fake_response

        result = ask_mae("How do I configure CAD terminal settings?")

        self.assertIn("CAD Administration Guide", result["answer"])
        self.assertEqual(result["sources"][0]["kind"], "document")
        self.assertEqual(result["sources"][0]["detail"], "Page 27")
        payload_text = str(post_mock.call_args.kwargs["json"])
        self.assertIn("Open Machine Settings", payload_text)
        unit_mock.assert_not_called()
        operations_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
