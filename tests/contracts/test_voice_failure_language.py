"""Silence is not an outage, and internal categories are not user-facing.

Both properties were violated live on 2026-08-11: a tester's push-to-talk
clips came back 503 Service Unavailable several times before one worked.
Nothing was down -- the runtime raised ``transcript_output_limit`` for an
EMPTY transcript (a short press, a quiet start), the route turned every
``CloudAiRuntimeUnavailable`` into a 503, and the raw category string was
rendered as the on-screen message.

The failure was also undiagnosable after the fact: the sanitized path
logged nothing, so "the tester said nothing" and "Amazon Transcribe broke"
left identical evidence. The category alone names no audio, no transcript,
and no person, so it is now logged.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.integrations.cloud_ai import (
    CloudAiNoSpeechDetected,
    CloudAiRuntimeUnavailable,
)
from app.main import SPEECH_UNAVAILABLE_DETAIL, app


def _clip() -> dict:
    # Comfortably over the client's 2000-byte floor so the request is the
    # realistic shape: a real clip that simply held no speech.
    return {"file": ("mae-question.pcm", io.BytesIO(b"\x00\x01" * 4000), "application/octet-stream")}


class _CloudVoiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()


class NoSpeechIsNotAnOutageTests(_CloudVoiceTestCase):
    def test_empty_transcript_is_a_success_with_no_text(self):
        """The browser already says "I did not catch that" for empty text.

        Answering 503 instead told a tester holding the button a beat too
        short that the system was down.
        """
        with patch(
            "app.main.transcribe_cloud_speech",
            side_effect=CloudAiNoSpeechDetected("no_speech_detected"),
        ):
            response = self.client.post("/api/voice/transcribe", files=_clip())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "")
        self.assertIs(response.json()["stored"], False)

    def test_real_denials_still_fail_closed(self):
        """No-speech must not become a blanket 'always 200'.

        ``CloudAiNoSpeechDetected`` subclasses the unavailable category, so
        the ordering of the two except-blocks is what keeps genuine
        failures failing. If they were ever reordered, this fails.
        """
        for category in (
            "transcribe_unavailable",
            "audio_input_limit",
            "transcribe_provider_failed",
            "transcript_output_limit",
            "tenant_not_authorized",
        ):
            with self.subTest(category=category):
                with patch(
                    "app.main.transcribe_cloud_speech",
                    side_effect=CloudAiRuntimeUnavailable(category),
                ):
                    response = self.client.post("/api/voice/transcribe", files=_clip())
                self.assertEqual(response.status_code, 503)


class InternalCategoriesStayInternalTests(_CloudVoiceTestCase):
    def test_transcribe_denial_says_something_human_and_logs_the_category(self):
        with patch(
            "app.main.transcribe_cloud_speech",
            side_effect=CloudAiRuntimeUnavailable("transcript_output_limit"),
        ):
            with self.assertLogs("app.main", level="WARNING") as logged:
                response = self.client.post("/api/voice/transcribe", files=_clip())

        detail = response.json()["detail"]
        self.assertNotIn("transcript_output_limit", detail)
        self.assertNotIn("_", detail, f"detail reads like an identifier: {detail!r}")
        self.assertIn("try again", detail.lower())
        # ...but an operator can still tell what happened.
        self.assertTrue(
            any("transcript_output_limit" in line for line in logged.output),
            "the denial category must be logged or the next one is undiagnosable",
        )

    def test_speech_denials_say_something_human_and_log_the_category(self):
        """The same leak shipped on both speech routes.

        ``polly_provider_failed`` was visible in the browser console on the
        avatar surface; neither route should render an identifier.
        """
        for route, target, body in (
            (
                "/api/mae/avatar/speech",
                "app.main.synthesize_cloud_avatar_speech",
                {"text": "Hello."},
            ),
            (
                "/api/cloud-ai/speech/sentence",
                "app.main.synthesize_cloud_sentence",
                {"text": "Hello.", "persona": "mae"},
            ),
        ):
            with self.subTest(route=route):
                with patch(target, side_effect=CloudAiRuntimeUnavailable("polly_provider_failed")):
                    with self.assertLogs("app.main", level="WARNING") as logged:
                        response = self.client.post(route, json=body)
                self.assertEqual(response.status_code, 503)
                detail = response.json()["detail"]
                self.assertNotIn("polly_provider_failed", detail)
                self.assertEqual(detail, SPEECH_UNAVAILABLE_DETAIL)
                self.assertTrue(
                    any("polly_provider_failed" in line for line in logged.output)
                )


if __name__ == "__main__":
    unittest.main()
