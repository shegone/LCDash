"""Network-free contracts for the bounded Amazon Polly adapter."""

import io
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


class _Stream(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _request(voice=PollyVoice.JOANNA, text="Call 911."):
    return PollySpeechRequest(
        "synthetic-request-1001",
        "logan-synthetic",
        text,
        voice,
    )


class CloudPollyProviderTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    def test_adapter_is_lazy_and_uses_exact_bounded_request(self):
        stream = _Stream(b"synthetic-mp3")
        client = _Client({"AudioStream": stream, "ContentType": "audio/mpeg"})
        factory_calls = []
        provider = AwsPollySpeechProvider(
            client_factory=lambda: factory_calls.append(True) or client
        )
        self.assertEqual(factory_calls, [])

        self.assertEqual(provider.synthesize(_request()), b"synthetic-mp3")
        self.assertEqual(factory_calls, [True])
        self.assertEqual(
            client.calls,
            [
                {
                    "Engine": "neural",
                    "OutputFormat": "mp3",
                    "Text": "Call nine one one.",
                    "TextType": "text",
                    "VoiceId": "Joanna",
                }
            ],
        )
        self.assertTrue(stream.was_closed)
        self.network.assert_not_called()

    def test_both_reviewed_voices_are_forwarded_exactly(self):
        for voice in (PollyVoice.JOANNA, PollyVoice.MATTHEW):
            stream = _Stream(b"mp3")
            client = _Client({"AudioStream": stream})
            provider = AwsPollySpeechProvider(client_factory=lambda: client)
            provider.synthesize(_request(voice=voice))
            self.assertEqual(client.calls[0]["VoiceId"], voice.value)

    def test_missing_empty_and_oversize_audio_fail_closed_and_close_stream(self):
        wrong_type_stream = _Stream(b"mp3")
        with self.assertRaisesRegex(RuntimeError, "polly_content_type_invalid"):
            AwsPollySpeechProvider(
                client_factory=lambda: _Client(
                    {"AudioStream": wrong_type_stream, "ContentType": "audio/pcm"}
                )
            ).synthesize(_request())
        self.assertTrue(wrong_type_stream.was_closed)
        with self.assertRaisesRegex(RuntimeError, "polly_audio_stream_missing"):
            AwsPollySpeechProvider(
                client_factory=lambda: _Client({})
            ).synthesize(_request())

        for value, category in ((b"", "polly_audio_empty"), (b"1234", "polly_audio_limit")):
            stream = _Stream(value)
            provider = AwsPollySpeechProvider(
                client_factory=lambda: _Client({"AudioStream": stream}),
                max_audio_bytes=3,
            )
            with self.assertRaisesRegex(RuntimeError, category):
                provider.synthesize(_request())
            self.assertTrue(stream.was_closed)

    def test_runtime_sanitizes_provider_failure(self):
        client = _Client(RuntimeError("provider payload must not escape"))
        provider = AwsPollySpeechProvider(client_factory=lambda: client)
        config = CloudAiProviderConfig.from_mapping(
            {
                "mode": "advisory-rag",
                "tenant_id": "logan-synthetic",
                "voice_enabled": True,
                "action_tools": [],
            }
        )
        runtime = CloudAiRuntime(config, polly=provider)
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "polly_provider_failed") as caught:
            runtime.synthesize(_request())
        self.assertNotIn("provider payload", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
