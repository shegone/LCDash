"""Route-level contracts: verified-live facts are tried before document RAG,
and a question with no live-data intent falls through unchanged."""

import json
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


class CloudAdvisoryStreamLiveDataRoutingTests(unittest.TestCase):
    """The streaming endpoint (used in MAE voice mode) must try live data
    first too -- it previously skipped straight to document RAG, so a voice
    question like "how many active calls" could never get a live answer."""

    def setUp(self):
        self.client = TestClient(app)

    @staticmethod
    def _events(response):
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    @patch("app.main.stream_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_stream_returns_live_data_without_calling_document_rag_stream(
        self, live_mock, stream_mock
    ):
        live_mock.return_value = dict(LIVE_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/cloud-ai/advisory/stream",
                json={"question": "How many active calls are there right now?"},
            )

        self.assertEqual(response.status_code, 200)
        events = self._events(response)
        complete = next(event for event in events if event["type"] == "complete")
        self.assertEqual(complete["payload"]["answer"], LIVE_RESULT["answer"])
        self.assertEqual(complete["payload"]["citations"], [])
        self.assertEqual(len(complete["payload"]["data_sources"]), 1)
        self.assertTrue(any(event["type"] == "done" for event in events))
        stream_mock.assert_not_called()

    @patch("app.main.stream_cloud_advisory")
    @patch("app.main.answer_verified_live_or_none")
    def test_stream_falls_through_to_document_rag_stream_when_no_live_intent(
        self, live_mock, stream_mock
    ):
        live_mock.return_value = None
        stream_mock.return_value = iter(
            [
                {"type": "status", "stage": "retrieving"},
                {"type": "complete", "payload": dict(DOCUMENT_RESULT)},
            ]
        )
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/cloud-ai/advisory/stream",
                json={"question": "How do I configure CAD window columns?"},
            )

        self.assertEqual(response.status_code, 200)
        events = self._events(response)
        complete = next(event for event in events if event["type"] == "complete")
        self.assertEqual(complete["payload"]["answer"], DOCUMENT_RESULT["answer"])
        stream_mock.assert_called_once()


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
