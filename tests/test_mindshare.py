import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mindshare_service import _evidence, _focus_results, ask_mindshare
from scripts.index_knowledge import _is_supported_document


class MindsharePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_mindshare_hub_separates_technical_and_radio_modules(self):
        response = self.client.get("/mindshare")

        self.assertEqual(response.status_code, 200)
        self.assertIn("JACK Technical Assistant", response.text)
        self.assertIn("Radio Intelligence", response.text)
        self.assertIn("FUTURE MODULE", response.text)

    def test_mindshare_technical_page_has_separate_chat_assets(self):
        response = self.client.get("/mindshare/technical")

        self.assertEqual(response.status_code, 200)
        self.assertIn("JACK", response.text)
        self.assertIn("About the real Jack Hines", response.text)
        self.assertIn("/mindshare/jack-hines", response.text)
        self.assertNotIn("memorial voice", response.text)
        self.assertNotIn("named in memory of", response.text)
        self.assertIn("Start voice mode", response.text)
        self.assertIn('id="jack-voice-session"', response.text)
        self.assertIn('id="jack-voice-player"', response.text)
        self.assertIn("/static/js/lcdash-mindshare.js", response.text)

    def test_jack_hines_tribute_page_is_separate_from_assistant(self):
        response = self.client.get("/mindshare/jack-hines")

        self.assertEqual(response.status_code, 200)
        self.assertIn("John Joseph", response.text)
        self.assertIn("1947", response.text)
        self.assertIn("2025", response.text)
        self.assertIn("/static/img/jack-hines.jpg", response.text)
        self.assertIn("family obituary", response.text)
        self.assertIn("Why the technical assistant carries his name", response.text)
        self.assertIn("/mindshare/technical", response.text)

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
                "document_id": 11,
                "title": "MRI2 Manual",
                "file_name": "mri2.pdf",
                "page_count": 50,
                "chunk_count": 100,
                "indexed_at": "2026-07-26T12:00:00Z",
                "is_pdf": True,
            }
        ]

        response = self.client.get("/mindshare/library")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mindshare Technical Library", response.text)
        self.assertIn("MRI2 Manual", response.text)
        self.assertIn("/knowledge/documents/mindshare/11", response.text)
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

    @patch("app.main.ask_mindshare")
    def test_stream_endpoint_emits_tokens_and_final_payload(self, ask_mock):
        def streamed(question, history, token_callback=None):
            token_callback("Use the documented procedure.")
            return {"answer": "Use the documented procedure.", "sources": [], "evidence": []}

        ask_mock.side_effect = streamed
        response = self.client.post(
            "/api/mindshare/chat/stream",
            json={"question": "How do I update MRI2?", "history": []},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"token"', response.text)
        self.assertIn('"type":"complete"', response.text)


class MindshareServiceTests(unittest.TestCase):
    @patch("app.services.mindshare_service.httpx.stream")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_supported_answer_streams_ollama_tokens(
        self, search_mock, stream_mock
    ):
        search_mock.return_value = [{
            "title": "Mindshare Radio Interface 2 Manual",
            "file_name": "mri2.pdf",
            "page_number": 40,
            "content": "Back up the configuration before software update.",
            "coverage": 0.75,
            "semantic_score": 0.7,
            "hybrid_score": 0.7,
            "matched_terms": ["update", "mri2"],
            "retrieval": ["keyword", "semantic"],
        }]
        stream_response = MagicMock()
        stream_response.iter_lines.return_value = [
            '{"message":{"content":"Back up "}}',
            '{"message":{"content":"first."},"done":true}',
        ]
        stream_mock.return_value.__enter__.return_value = stream_response
        tokens = []

        result = ask_mindshare(
            "How do I update MRI2?", token_callback=tokens.append
        )

        self.assertEqual(tokens, ["Back up ", "first."])
        self.assertEqual(result["answer"], "Back up first.")
        self.assertTrue(stream_mock.call_args.kwargs["json"]["stream"])

    def test_evidence_keeps_the_approved_pdf_document_id(self):
        evidence = _evidence([{
            "document_id": 42,
            "title": "Mindshare Radio Interface 2 Manual",
            "file_name": "mri2.pdf",
            "page_number": 40,
            "content": "Back up the configuration first.",
        }])
        self.assertEqual(evidence[0]["document_id"], 42)

    def test_named_product_prioritizes_title_match_over_incidental_mention(self):
        results = [
            {
                "title": "CAD Alerting Gateway Manual",
                "file_name": "cag.pdf",
                "content": "The gateway can connect to an MRI2.",
            },
            {
                "title": "Mindshare Radio Interface 2 Manual",
                "file_name": "mri2.pdf",
                "content": "MRI2 configuration and service information.",
            },
        ]

        focused = _focus_results("How do I update MRI2?", results)

        self.assertEqual(focused[0]["file_name"], "mri2.pdf")

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
        options = post_mock.call_args.kwargs["json"]["options"]
        self.assertEqual(options["num_ctx"], 3072)
        self.assertEqual(options["num_predict"], 110)

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_unsupported_question_is_not_sent_to_model(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = []

        result = ask_mindshare("What undocumented port should I use?")

        self.assertIn("will not invent", result["answer"])
        post_mock.assert_not_called()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_mri_definition_is_derived_from_mindshare_documents(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "document_id": 17,
                "title": "Mindshare Radio Interface Manual",
                "file_name": "mindshare-radio-interface.pdf",
                "page_number": 5,
                "content": "The Mindshare Radio Interface provides radio connectivity.",
                "coverage": 1.0,
                "semantic_score": 0.8,
                "hybrid_score": 0.8,
                "matched_terms": ["mindshare", "radio", "interface"],
            }
        ]

        result = ask_mindshare("What does MRI stand for?")

        self.assertEqual(
            result["answer"],
            "In the Mindshare product context, MRI stands for Mindshare Radio Interface.",
        )
        self.assertEqual(result["model"], "Mindshare documented product glossary")
        self.assertEqual(result["assurance"]["level"], "high")
        self.assertEqual(result["evidence"][0]["document_id"], 17)
        self.assertEqual(
            search_mock.call_args.args[0], "Mindshare Radio Interface"
        )
        post_mock.assert_not_called()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge", return_value=[])
    def test_safe_general_technical_question_uses_labeled_model_guidance(
        self,
        search_mock,
        post_mock,
    ):
        post_mock.return_value = Mock(
            raise_for_status=Mock(),
            json=Mock(
                return_value={
                    "message": {
                        "content": "A router directs traffic between networks."
                    }
                }
            ),
        )

        result = ask_mindshare("What does a network router do?")

        self.assertIn("General technical guidance:", result["answer"])
        self.assertEqual(result["assurance"]["level"], "general")
        self.assertEqual(result["evidence"], [])
        self.assertIn("general technical knowledge", result["sources"][0]["name"].lower())
        self.assertIn(
            "not a mindshare documented procedure",
            result["sources"][0]["detail"].lower(),
        )
        self.assertIn("Start with `General technical guidance:`", post_mock.call_args.kwargs["json"]["messages"][0]["content"])
        search_mock.assert_called_once()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge", return_value=[])
    def test_undocumented_configuration_question_remains_refused(
        self,
        search_mock,
        post_mock,
    ):
        result = ask_mindshare("What port should I configure for the gateway?")

        self.assertIn("could not find", result["answer"])
        post_mock.assert_not_called()
        search_mock.assert_called_once()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_password_request_is_stopped_before_document_search(
        self,
        search_mock,
        post_mock,
    ):
        result = ask_mindshare("Give me the administrator password.")

        self.assertIn("cannot provide", result["answer"])
        self.assertEqual(result["assurance"]["level"], "limited")
        self.assertFalse(result["write_access"])
        search_mock.assert_not_called()
        post_mock.assert_not_called()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_direct_change_request_is_stopped_as_read_only(
        self,
        search_mock,
        post_mock,
    ):
        result = ask_mindshare("Change the MRI2 multicast address for me now.")

        self.assertIn("read-only", result["answer"])
        self.assertFalse(result["write_access"])
        search_mock.assert_not_called()
        post_mock.assert_not_called()

    @patch("app.services.mindshare_service.httpx.post")
    @patch("app.services.mindshare_service.search_knowledge")
    def test_named_product_does_not_blend_other_product_families(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "Mindshare Radio Interface Manual",
                "file_name": "MS0102_MRI_v114.pdf",
                "page_number": 13,
                "content": "First-generation MRI console audio settings.",
                "coverage": 0.8,
                "semantic_score": 0.8,
                "hybrid_score": 0.8,
                "matched_terms": ["audio", "mri"],
                "retrieval": ["keyword", "semantic"],
            },
            {
                "title": "Mindshare Radio Interface 2 Manual",
                "file_name": "MS0127_MRI2_v105.pdf",
                "page_number": 40,
                "content": "MRI2-specific audio troubleshooting.",
                "coverage": 0.7,
                "semantic_score": 0.7,
                "hybrid_score": 0.7,
                "matched_terms": ["audio", "mri2"],
                "retrieval": ["keyword", "semantic"],
            },
        ]
        post_mock.return_value = Mock(
            raise_for_status=Mock(),
            json=Mock(
                return_value={
                    "message": {
                        "content": (
                            "Use the MRI2-specific procedure "
                            "[Mindshare Radio Interface 2 Manual, page 40]."
                        )
                    }
                }
            ),
        )

        ask_mindshare("Our MRI2 has lost console audio. What should I check?")

        model_messages = post_mock.call_args.kwargs["json"]["messages"]
        supplied_context = model_messages[-1]["content"]
        self.assertIn("MRI2-specific audio troubleshooting", supplied_context)
        self.assertNotIn("First-generation MRI", supplied_context)

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
