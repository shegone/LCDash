"""Network-free contracts for the avatar's Polly viseme pipeline.

The avatar plan's pinned trap (docs/planning/MAE_AVATAR_PLAN_2026-08-09.md):
Polly's *generative* voices do not emit viseme speech marks, so the engine is
contractually neural and these tests assert it on the wire-call level. A
voice change that "sounds nicer" must fail loudly here rather than silently
freezing MAE's mouth.
"""

import base64
import io
import json
import socket
import unittest
from unittest.mock import patch

from app.integrations.cloud_ai import (
    AwsPollySpeechProvider,
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    PollySpeechRequest,
    PollyVoice,
)
from app.integrations.cloud_ai.contracts import (
    MAX_VISEME_MARKS,
    PollySpeechWithVisemes,
    PollyVisemeMark,
)
from app.integrations.cloud_ai.polly_provider import _parse_viseme_marks
from app.services.cloud_ai_streaming import synthesize_cloud_avatar_speech


class _Stream(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class _SequencedClient:
    """Returns the audio response first, then the speech-mark response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _request(text="Call 911."):
    return PollySpeechRequest(
        "synthetic-request-2001",
        "logan-synthetic",
        text,
        PollyVoice.JOANNA,
    )


def _marks_bytes(*entries) -> bytes:
    return "\n".join(json.dumps(entry) for entry in entries).encode("utf-8")


def _config() -> CloudAiProviderConfig:
    return CloudAiProviderConfig.from_mapping(
        {
            "mode": "advisory-rag",
            "tenant_id": "logan-synthetic",
            "voice_enabled": True,
            "action_tools": [],
        }
    )


class VisemeMarkParsingTests(unittest.TestCase):
    def test_documented_marks_parse_and_junk_is_dropped_not_fatal(self):
        raw = _marks_bytes(
            {"time": 0, "type": "viseme", "value": "p"},
            {"time": 120, "type": "word", "value": "call"},        # not a viseme
            {"time": 180, "type": "viseme", "value": "a"},
            {"time": 200, "type": "viseme", "value": "ZZ"},        # undocumented
            {"time": -5, "type": "viseme", "value": "t"},          # bad time
            {"time": 240, "type": "viseme", "value": "sil"},
        ) + b"\nnot-json-at-all\n"
        marks = _parse_viseme_marks(raw)
        self.assertEqual(
            marks,
            (
                PollyVisemeMark(0, "p"),
                PollyVisemeMark(180, "a"),
                PollyVisemeMark(240, "sil"),
            ),
        )

    def test_mark_count_is_bounded(self):
        raw = _marks_bytes(
            *({"time": index, "type": "viseme", "value": "a"} for index in range(MAX_VISEME_MARKS + 50))
        )
        self.assertEqual(len(_parse_viseme_marks(raw)), MAX_VISEME_MARKS)

    def test_contract_rejects_undocumented_visemes_and_unbounded_timelines(self):
        with self.assertRaises(ValueError):
            PollyVisemeMark(0, "ZZ")
        with self.assertRaises(ValueError):
            PollyVisemeMark(-1, "a")
        with self.assertRaises(ValueError):
            PollySpeechWithVisemes(b"", ())
        with self.assertRaises(ValueError):
            PollySpeechWithVisemes(
                b"mp3",
                tuple(PollyVisemeMark(0, "a") for _ in range(MAX_VISEME_MARKS + 1)),
            )


class AvatarProviderTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    def test_two_bounded_neural_calls_share_text_and_voice(self):
        """The engine pin and the same-utterance guarantee, on the wire."""
        audio_stream = _Stream(b"synthetic-mp3")
        marks_stream = _Stream(_marks_bytes({"time": 0, "type": "viseme", "value": "p"}))
        client = _SequencedClient(
            [
                {"AudioStream": audio_stream, "ContentType": "audio/mpeg"},
                {"AudioStream": marks_stream, "ContentType": "application/x-json-stream"},
            ]
        )
        provider = AwsPollySpeechProvider(client_factory=lambda: client)

        speech = provider.synthesize_with_visemes(_request())

        self.assertEqual(speech.audio_mp3, b"synthetic-mp3")
        self.assertEqual(speech.visemes, (PollyVisemeMark(0, "p"),))
        self.assertEqual(len(client.calls), 2)
        audio_call, marks_call = client.calls
        self.assertEqual(audio_call["Engine"], "neural")
        self.assertEqual(marks_call["Engine"], "neural")
        self.assertEqual(marks_call["OutputFormat"], "json")
        self.assertEqual(marks_call["SpeechMarkTypes"], ["viseme"])
        # Same text, same voice: the timeline can never describe a different
        # utterance than the one heard.
        self.assertEqual(audio_call["Text"], marks_call["Text"])
        self.assertEqual(audio_call["VoiceId"], marks_call["VoiceId"])
        self.assertTrue(audio_stream.was_closed)
        self.assertTrue(marks_stream.was_closed)
        self.network.assert_not_called()

    def test_speech_mark_stream_failures_fail_closed_and_close_streams(self):
        wrong_type = _Stream(b"{}")
        provider = AwsPollySpeechProvider(
            client_factory=lambda: _SequencedClient(
                [
                    {"AudioStream": _Stream(b"mp3"), "ContentType": "audio/mpeg"},
                    {"AudioStream": wrong_type, "ContentType": "audio/mpeg"},
                ]
            )
        )
        with self.assertRaisesRegex(RuntimeError, "polly_speech_marks_content_type_invalid"):
            provider.synthesize_with_visemes(_request())
        self.assertTrue(wrong_type.was_closed)

        with self.assertRaisesRegex(RuntimeError, "polly_speech_marks_missing"):
            AwsPollySpeechProvider(
                client_factory=lambda: _SequencedClient(
                    [{"AudioStream": _Stream(b"mp3"), "ContentType": "audio/mpeg"}, {}]
                )
            ).synthesize_with_visemes(_request())


class AvatarRuntimeTests(unittest.TestCase):
    def test_provider_without_visemes_is_unavailable_not_downgraded(self):
        class _AudioOnlyProvider:
            def synthesize(self, request):
                return b"mp3"

        runtime = CloudAiRuntime(_config(), polly=_AudioOnlyProvider())
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "polly_visemes_unavailable"):
            runtime.synthesize_with_visemes(_request())

    def test_oversize_audio_fails_the_output_limit(self):
        class _Provider:
            def synthesize_with_visemes(self, request):
                return PollySpeechWithVisemes(b"x" * 6_000_000, ())

            def synthesize(self, request):
                return b"mp3"

        runtime = CloudAiRuntime(_config(), polly=_Provider())
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "audio_output_limit"):
            runtime.synthesize_with_visemes(_request())

    def test_successful_speech_passes_through(self):
        expected = PollySpeechWithVisemes(b"mp3", (PollyVisemeMark(0, "a"),))

        class _Provider:
            def synthesize_with_visemes(self, request):
                return expected

            def synthesize(self, request):
                return b"mp3"

        runtime = CloudAiRuntime(_config(), polly=_Provider())
        self.assertIs(runtime.synthesize_with_visemes(_request()), expected)


class AvatarSpeechServiceTests(unittest.TestCase):
    def _runtime(self, speech):
        class _Provider:
            def synthesize_with_visemes(self, request):
                return speech

            def synthesize(self, request):
                return speech.audio_mp3

        return CloudAiRuntime(_config(), polly=_Provider())

    def test_payload_shape_and_voice_default(self):
        speech = PollySpeechWithVisemes(
            b"synthetic-mp3",
            (PollyVisemeMark(0, "p"), PollyVisemeMark(140, "sil")),
        )
        payload = synthesize_cloud_avatar_speech(
            self._runtime(speech),
            _config(),
            request_id="synthetic-request-3001",
            text="Hello from MAE.",
        )
        self.assertEqual(
            base64.b64decode(payload["audio_base64"]), b"synthetic-mp3"
        )
        self.assertEqual(payload["audio_format"], "mp3")
        self.assertEqual(payload["voice"], PollyVoice.JOANNA.value)
        self.assertEqual(
            payload["visemes"],
            [
                {"time_ms": 0, "viseme": "p"},
                {"time_ms": 140, "viseme": "sil"},
            ],
        )

    def test_empty_text_and_unknown_voice_fail_closed(self):
        speech = PollySpeechWithVisemes(b"mp3", ())
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "empty_speech_text"):
            synthesize_cloud_avatar_speech(
                self._runtime(speech),
                _config(),
                request_id="synthetic-request-3002",
                text="   ",
            )
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "polly_voice_not_allowed"):
            synthesize_cloud_avatar_speech(
                self._runtime(speech),
                _config(),
                request_id="synthetic-request-3003",
                text="Hello.",
                voice="Ruth",
            )


if __name__ == "__main__":
    unittest.main()
