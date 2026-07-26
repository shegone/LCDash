import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mindshare_service import ask_mindshare
from scripts.index_knowledge import _is_supported_document


class MindsharePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_mindshare_hub_separates_technical_and_radio_modules(self):
        response = self.client.get("/mindshare")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Technical Assistant", response.text)
        self.assertIn("Radio Intelligence", response.text)
        self.assertIn("FUTURE MODULE", response.text)

    def test_mindshare_technical_page_has_separate_chat_assets(self):
        response = self.client.get("/mindshare/technical")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mindshare Technical Assistant", response.text)
        self.assertIn("Documentation-only mode", response.text)
        self.assertIn("/static/js/lcdash-mindshare.js", response.text)

    @patch("app.main.list_knowledge_documents")
    @patch("app.main.get_knowledge_status")
    def test_mindshare_library_uses_mindshare_partition(
        self,
        status_mock,
        documents_mock,
    ):
        status_mock.return_value = {
            "configured": True,
            "connected": True,
            "documents": 1,
            "chunks": 25,
            "index_state": {"status": "complete"},
            "drive_sync": {"status": "complete"},
        }
        documents_mock.return_value = [
            {
                "title": "MRI2 Manual",
                "file_name": "mri2.pdf",
                "page_count": 50,
                "chunk_count": 100,
                "indexed_at": "2026-07-26T12:00:00Z",
            }
        ]

        response = self.client.get("/mindshare/library")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mindshare Technical Library", response.text)
        self.assertIn("MRI2 Manual", response.text)
        self.assertEqual(
            status_mock.call_args.kwargs["library_key"],
            "mindshare",
        )
        self.assertEqual(
            documents_mock.call_args.kwargs["library_key"],
            "mindshare",
        )

    def test_radio_page_is_explicitly_inactive(self):
        response = self.client.get("/mindshare/radio")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Not connected", response.text)
        self.assertIn("No radio traffic is being captured", response.text)

    @patch("app.main.ask_mindshare")
    def test_chat_endpoint_is_separate_from_mae(self, ask_mock):
        ask_mock.return_value = {
            "answer": "Use the documented MRI2 update procedure.",
            "sources": [],
            "evidence": [],
        }

        response = self.client.post(
            "/api/mindshare/chat",
            json={
                "question": "How do I update MRI2?",
                "history": [{"role": "user", "content": "Hello"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("MRI2", response.json()["answer"])
        ask_mock.assert_called_once()


class MindshareServiceTests(unittest.TestCase):
    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_supported_answer_uses_only_mindshare_library(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "Mindshare Radio Interface 2 Manual",
                "file_name": "mri2.pdf",
                "page_number": 40,
                "content": "Back up the configuration before software update.",
                "coverage": 0.75,
                "semantic_score": 0.7,
                "hybrid_score": 0.7,
                "matched_terms": ["update", "mri2"],
                "retrieval": ["keyword", "semantic"],
            }
        ]
        post_mock.return_value = Mock(
            raise_for_status=Mock(),
            json=Mock(
                return_value={
                    "message": {
                        "content": (
                            "Back up the configuration first "
                            "[Mindshare Radio Interface 2 Manual, page 40]."
                        )
                    }
                }
            ),
        )

        result = ask_mindshare("How do I update MRI2?")

        self.assertIn("Back up", result["answer"])
        self.assertEqual(
            search_mock.call_args.kwargs["library_key"],
            "mindshare",
        )
        self.assertNotIn("CentralSquare", post_mock.call_args.kwargs["json"])

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_unsupported_question_is_not_sent_to_model(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = []

        result = ask_mindshare("What undocumented port should I use?")

        self.assertIn("will not guess", result["answer"])
        post_mock.assert_not_called()

    def test_indexer_accepts_technical_text_and_blocks_secrets(self):
        with TemporaryDirectory() as directory:
            files = {}
            for name in (
                "radio.cfg",
                "procedure.docx",
                "passwords.txt",
                "firmware.img",
            ):
                path = Path(directory) / name
                path.write_text("test", encoding="utf-8")
                files[name] = path

            self.assertTrue(_is_supported_document(files["radio.cfg"]))
            self.assertTrue(_is_supported_document(files["procedure.docx"]))
            self.assertFalse(_is_supported_document(files["passwords.txt"]))
            self.assertFalse(_is_supported_document(files["firmware.img"]))


if __name__ == "__main__":
    unittest.main()
