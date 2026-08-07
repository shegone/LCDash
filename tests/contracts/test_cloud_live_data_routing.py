"""Route-level contracts: verified-live facts are tried before document RAG,
and a question with no live-data intent falls through unchanged."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app


LIVE_RESULT = {
    "request_id": "cloud-live-test",
    "answer": "There are currently 3 active calls.",
    "citations": [],
    "data_sources": [
        {
            "name": "CentralSquare CAD (current read-only snapshot)",
            "kind": "live",
            "detail": "Freshness: fresh",
            "available": True,
            "timestamp": "5s old",
        }
    ],
    "denied": False,
    "denial_reason": "",
    "advisory_only": True,
    "action_executed": False,
}

DOCUMENT_RESULT = {
    "request_id": "cloud-advisory-test",
    "answer": "Per the configuration guide, set the column width in Preferences.",
    "citations": [
        {
            "source_uri": "s3://bucket/doc.pdf",
            "title": "CAD Configuration Guide",
            "page": 4,
            "section": "",
            "revision": "",
        }
    ],
    "denied": False,
    "denial_reason": "",
    "advisory_only": True,
    "action_executed": False,
}


class CloudAdvisoryLiveDataRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_live_data_result_is_returned_without_calling_document_rag(
        self, live_mock, document_mock
    ):
        live_mock.return_value = dict(LIVE_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How many active calls are there right now?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], LIVE_RESULT["answer"])
        self.assertEqual(payload["citations"], [])
        self.assertEqual(len(payload["data_sources"]), 1)
        document_mock.assert_not_called()

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_no_live_intent_falls_through_to_document_rag(
        self, live_mock, document_mock
    ):
        live_mock.return_value = None
        document_mock.return_value = dict(DOCUMENT_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How do I configure CAD window columns?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], DOCUMENT_RESULT["answer"])
        self.assertEqual(len(payload["citations"]), 1)
        document_mock.assert_called_once()

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_live_data_denial_is_still_returned_directly_not_double_answered(
        self, live_mock, document_mock
    ):
        # Once facts are found, answer_verified_live_or_none owns the answer
        # even if it denies (e.g. daily budget exhausted) -- the route must
        # not then also call the document path for the same question.
        live_mock.return_value = {
            "request_id": "cloud-live-test",
            "answer": "",
            "citations": [],
            "data_sources": [],
            "denied": True,
            "denial_reason": "The daily advisory usage limit has been reached.",
            "advisory_only": True,
            "action_executed": False,
        }
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How many active calls are there right now?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["denied"])
        document_mock.assert_not_called()


class JackAdvisoryLiveDataRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_jack_chat_tries_live_data_before_document_rag(
        self, live_mock, document_mock
    ):
        live_mock.return_value = dict(LIVE_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/mindshare/chat",
                json={"question": "How many units are active right now?", "history": []},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], LIVE_RESULT["answer"])
        document_mock.assert_not_called()

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_jack_chat_falls_through_when_no_live_intent(
        self, live_mock, document_mock
    ):
        live_mock.return_value = None
        document_mock.return_value = dict(DOCUMENT_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/mindshare/chat",
                json={"question": "How do I configure a radio channel plan?", "history": []},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], DOCUMENT_RESULT["answer"])
        document_mock.assert_called_once()
        self.assertEqual(document_mock.call_args.kwargs["persona"], "jack")


if __name__ == "__main__":
    unittest.main()
