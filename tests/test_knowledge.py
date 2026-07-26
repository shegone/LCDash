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
                "matched_terms": ["cad", "terminal"],
                "query_terms": ["cad", "terminal"],
                "coverage": 1.0,
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

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.search_knowledge")
    def test_instructional_set_question_is_researched_not_refused(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "CAD Configuration Guide",
                "file_name": "cad-config.pdf",
                "page_number": 10,
                "content": "Set the CAD Terminal ID in Machine Settings.",
                "indexed_at": "",
                "rank": 1.0,
                "matched_terms": ["cad", "terminal"],
                "query_terms": ["cad", "terminal"],
                "coverage": 1.0,
            }
        ]
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {
                "content": "Use Machine Settings (CAD Configuration Guide, page 10)."
            }
        }
        post_mock.return_value = fake_response

        result = ask_mae("Where do I set the CAD Terminal ID machine setting?")

        self.assertNotIn("currently inquiry-only", result["answer"])
        self.assertEqual(result["sources"][0]["kind"], "document")

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.search_knowledge")
    def test_weak_document_match_returns_safe_not_found_answer(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "GPS Guide",
                "file_name": "gps.pdf",
                "page_number": 4,
                "content": "Configure a GPS device port.",
                "indexed_at": "",
                "rank": 0.4,
                "matched_terms": ["machine"],
                "query_terms": ["cad", "terminal", "machine"],
                "coverage": 0.3333,
            }
        ]

        result = ask_mae("Where do I set the CAD Terminal ID machine setting?")

        self.assertIn("could not find", result["answer"].lower())
        self.assertIn("will not invent", result["answer"].lower())
        self.assertEqual(result["sources"], [])
        post_mock.assert_not_called()

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.search_knowledge")
    def test_document_followup_reuses_previous_user_subject(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "CAD Windows Quick Reference Guide",
                "file_name": "cad-windows.pdf",
                "page_number": 2,
                "content": (
                    "On the CAD Unit Screen, select Edit Columns or "
                    "Edit/Create Filter."
                ),
                "indexed_at": "",
                "rank": 1.0,
                "matched_terms": ["cad", "window", "columns", "filters"],
                "query_terms": ["cad", "window", "columns", "filters"],
                "coverage": 1.0,
            }
        ]
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {
                "content": (
                    "The option is on the CAD Unit Screen "
                    "(CAD Windows Quick Reference Guide, page 2)."
                )
            }
        }
        post_mock.return_value = fake_response
        history = [
            {
                "role": "user",
                "content": "How do I configure CAD window columns and filters?",
            },
            {
                "role": "assistant",
                "content": "Use Edit Columns or Edit/Create Filter.",
            },
        ]

        result = ask_mae("Where is that option?", history)

        search_question = search_mock.call_args.args[0]
        self.assertIn("configure CAD window columns", search_question)
        self.assertIn("Where is that option", search_question)
        self.assertIn("CAD Unit Screen", result["answer"])
        self.assertEqual(result["sources"][0]["kind"], "document")


if __name__ == "__main__":
    unittest.main()
