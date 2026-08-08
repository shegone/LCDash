"""Route-level contracts for the flag-gated tool-calling path: it must run
strictly between the verified-live path and the document RAG fallthrough,
and stay completely inert when the feature flag is off."""

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

TOOL_RESULT = {
    "request_id": "cloud-tool-test",
    "answer": "In the last 8 hours there were 6 calls.",
    "citations": [],
    "data_sources": [
        {
            "name": "PostgreSQL analytics",
            "kind": "historical",
            "detail": "Window: Last 8 hours",
            "available": True,
            "timestamp": "2026-08-08T00:00:00Z",
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


class ToolCallingFlagOffTests(unittest.TestCase):
    """Default posture: the flag is off, so tool-calling code must never run."""

    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_flag_off_never_calls_tool_path_even_when_live_returns_none(
        self, live_mock, tool_mock, document_mock
    ):
        live_mock.return_value = None
        document_mock.return_value = dict(DOCUMENT_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", False):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How many calls in the last 8 hours?"},
            )

        self.assertEqual(response.status_code, 200)
        tool_mock.assert_not_called()
        document_mock.assert_called_once()


class ToolCallingFlagOnTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_live_path_questions_are_unaffected_by_the_flag(
        self, live_mock, tool_mock, document_mock
    ):
        live_mock.return_value = dict(LIVE_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", True):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How many active calls are there right now?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], LIVE_RESULT["answer"])
        tool_mock.assert_not_called()
        document_mock.assert_not_called()

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_flag_on_and_live_none_consults_tool_path_before_document_rag(
        self, live_mock, tool_mock, document_mock
    ):
        live_mock.return_value = None
        tool_mock.return_value = dict(TOOL_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", True):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How many calls in the last 8 hours?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], TOOL_RESULT["answer"])
        tool_mock.assert_called_once()
        document_mock.assert_not_called()

    @patch("app.main.answer_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_flag_on_and_both_live_and_tool_none_falls_through_to_document_rag(
        self, live_mock, tool_mock, document_mock
    ):
        live_mock.return_value = None
        tool_mock.return_value = None
        document_mock.return_value = dict(DOCUMENT_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", True):
            response = self.client.post(
                "/api/cloud-ai/advisory",
                json={"question": "How do I configure CAD window columns?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], DOCUMENT_RESULT["answer"])
        tool_mock.assert_called_once()
        document_mock.assert_called_once()


class ToolCallingStreamRoutingTests(unittest.TestCase):
    """The streaming endpoint (MAE voice mode) must apply the same ordering."""

    def setUp(self):
        self.client = TestClient(app)

    @staticmethod
    def _events(response):
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    @patch("app.main.stream_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_stream_flag_off_never_calls_tool_path(
        self, live_mock, tool_mock, stream_mock
    ):
        live_mock.return_value = None
        stream_mock.return_value = iter(
            [{"type": "complete", "payload": dict(DOCUMENT_RESULT)}]
        )
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", False):
            response = self.client.post(
                "/api/cloud-ai/advisory/stream",
                json={"question": "How many calls in the last 8 hours?"},
            )

        self.assertEqual(response.status_code, 200)
        tool_mock.assert_not_called()

    @patch("app.main.stream_cloud_advisory")
    @patch("app.main.answer_tool_calling_or_none")
    @patch("app.main.answer_verified_live_or_none")
    def test_stream_flag_on_and_live_none_returns_tool_result_as_single_shot(
        self, live_mock, tool_mock, stream_mock
    ):
        live_mock.return_value = None
        tool_mock.return_value = dict(TOOL_RESULT)
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"), \
             patch.object(settings, "cloud_ai_tool_calling_enabled", True):
            response = self.client.post(
                "/api/cloud-ai/advisory/stream",
                json={"question": "How many calls in the last 8 hours?"},
            )

        self.assertEqual(response.status_code, 200)
        events = self._events(response)
        complete = next(event for event in events if event["type"] == "complete")
        self.assertEqual(complete["payload"]["answer"], TOOL_RESULT["answer"])
        self.assertTrue(any(event["type"] == "done" for event in events))
        stream_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
