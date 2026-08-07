"""Network-free contracts for the bounded Amazon Transcribe streaming adapter."""

import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.integrations.cloud_ai import (
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    TranscribePushToTalkRequest,
)
from app.integrations.cloud_ai.contracts import PushToTalkAudioFormat
from app.integrations.cloud_ai.transcribe_provider import (
    MAX_AUDIO_CHUNK_BYTES,
    AwsTranscribeStreamingProvider,
)


def _event(*segments):
    """Build one transcript event shaped like the streaming SDK model."""
    return SimpleNamespace(
        transcript=SimpleNamespace(
            results=[
                SimpleNamespace(
                    is_partial=partial,
                    alternatives=[SimpleNamespace(transcript=text)],
                )
                for text, partial in segments
            ]
        )
    )


class _InputStream:
    def __init__(self):
        self.chunks = []
        self.ended = False

    async def send_audio_event(self, audio_chunk):
        self.chunks.append(audio_chunk)

    async def end_stream(self):
        self.ended = True


class _OutputStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        event = self._events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class _Client:
    def __init__(self, events):
        self.calls = []
        self.events = events
        self.stream = None

    async def start_stream_transcription(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.events, Exception):
            raise self.events
        self.stream = SimpleNamespace(
            input_stream=_InputStream(),
            output_stream=_OutputStream(self.events),
        )
        return self.stream


def _request(
    audio_format=PushToTalkAudioFormat.PCM,
    sample_rate_hz=16000,
    duration_seconds=2.0,
):
    return TranscribePushToTalkRequest(
        "synthetic-request-2001",
        "logan-synthetic",
        audio_format,
        sample_rate_hz,
        duration_seconds,
    )


def _config():
    return CloudAiProviderConfig.from_mapping(
        {
            "mode": "advisory-rag",
            "tenant_id": "logan-synthetic",
            "voice_enabled": True,
            "action_tools": [],
        }
    )


class CloudTranscribeProviderTests(unittest.TestCase):
    def setUp(self):
        # Each stream runs on its own event loop, and loop start-up uses a
        # loopback self-pipe, so only off-box connections are treated as
        # network access.
        self.blocked = []
        connect = socket.socket.connect

        def guarded(sock, address):
            host = address[0] if isinstance(address, tuple) else address
            if host not in ("127.0.0.1", "::1", "localhost"):
                self.blocked.append(host)
                raise AssertionError("network access blocked")
            return connect(sock, address)

        network = patch.object(socket.socket, "connect", guarded)
        network.start()
        self.addCleanup(network.stop)

    def test_adapter_is_lazy_and_uses_exact_bounded_stream_settings(self):
        client = _Client(
            [
                _event(("dropped partial", True)),
                _event(("Unit twelve", False)),
                _event(("is en route", False)),
            ]
        )
        factory_calls = []
        provider = AwsTranscribeStreamingProvider(
            client_factory=lambda: factory_calls.append(True) or client
        )
        self.assertEqual(factory_calls, [])

        transcript = provider.transcribe(_request(), b"a" * 40_000)

        self.assertEqual(transcript, "Unit twelve is en route")
        self.assertEqual(factory_calls, [True])
        self.assertEqual(
            client.calls,
            [
                {
                    "language_code": "en-US",
                    "media_sample_rate_hz": 16000,
                    "media_encoding": "pcm",
                }
            ],
        )
        self.assertTrue(client.stream.input_stream.ended)
        self.assertEqual(b"".join(client.stream.input_stream.chunks), b"a" * 40_000)
        self.assertTrue(
            all(
                len(chunk) <= MAX_AUDIO_CHUNK_BYTES
                for chunk in client.stream.input_stream.chunks
            )
        )
        self.assertEqual(self.blocked, [])

    def test_ogg_opus_is_forwarded_and_webm_opus_fails_closed(self):
        client = _Client([_event(("Copy", False))])
        provider = AwsTranscribeStreamingProvider(client_factory=lambda: client)
        provider.transcribe(
            _request(PushToTalkAudioFormat.OGG_OPUS, sample_rate_hz=48000), b"opus"
        )
        self.assertEqual(client.calls[0]["media_encoding"], "ogg-opus")

        unused = _Client([])
        with self.assertRaisesRegex(
            RuntimeError, "transcribe_media_encoding_unsupported"
        ):
            AwsTranscribeStreamingProvider(
                client_factory=lambda: unused
            ).transcribe(
                _request(PushToTalkAudioFormat.WEBM_OPUS, sample_rate_hz=48000),
                b"webm",
            )
        self.assertEqual(unused.calls, [])

    def test_empty_and_oversize_transcripts_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "transcribe_audio_empty"):
            AwsTranscribeStreamingProvider(
                client_factory=lambda: _Client([])
            ).transcribe(_request(), b"")

        with self.assertRaisesRegex(RuntimeError, "transcribe_transcript_empty"):
            AwsTranscribeStreamingProvider(
                client_factory=lambda: _Client([_event(("   ", False))])
            ).transcribe(_request(), b"audio")

        with self.assertRaisesRegex(RuntimeError, "transcribe_transcript_limit"):
            AwsTranscribeStreamingProvider(
                client_factory=lambda: _Client([_event(("far too long", False))]),
                max_transcript_characters=4,
            ).transcribe(_request(), b"audio")

    def test_runtime_sanitizes_provider_failure_and_gates_on_voice(self):
        provider = AwsTranscribeStreamingProvider(
            client_factory=lambda: _Client(
                RuntimeError("provider payload must not escape")
            )
        )
        runtime = CloudAiRuntime(_config(), transcribe=provider)
        self.assertTrue(runtime.status.stt_ready)
        with self.assertRaisesRegex(
            CloudAiRuntimeUnavailable, "transcribe_provider_failed"
        ) as caught:
            runtime.transcribe(_request(), b"audio")
        self.assertNotIn("provider payload", str(caught.exception))

        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "transcribe_unavailable"):
            CloudAiRuntime(_config()).transcribe(_request(), b"audio")

    def test_stream_timeout_fails_closed_without_hanging_the_caller(self):
        class _NeverEnds:
            def __aiter__(self):
                return self

            async def __anext__(self):
                import asyncio

                await asyncio.sleep(30)
                raise StopAsyncIteration

        class _StallingClient:
            calls = []

            async def start_stream_transcription(self, **kwargs):
                return SimpleNamespace(
                    input_stream=_InputStream(), output_stream=_NeverEnds()
                )

        provider = AwsTranscribeStreamingProvider(
            client_factory=_StallingClient, timeout_seconds=1
        )
        with self.assertRaisesRegex(RuntimeError, "transcribe_stream_timeout"):
            provider.transcribe(_request(), b"audio")


if __name__ == "__main__":
    unittest.main()
