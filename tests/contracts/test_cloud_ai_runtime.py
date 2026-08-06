"""Network-free tests for the disabled cloud AI runtime boundary."""

import socket
import unittest
from unittest.mock import patch

from app.integrations.cloud_ai import (
    AdvisoryCitation,
    AdvisoryRagRequest,
    AdvisoryRagResponse,
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    PollySpeechRequest,
    PollyVoice,
    TranscribePushToTalkRequest,
)
from app.integrations.cloud_ai.contracts import PushToTalkAudioFormat


class _Advisory:
    calls = 0

    def answer(self, request):
        self.calls += 1
        return AdvisoryRagResponse.supported(
            request.request_id,
            "Use the cited approved procedure.",
            (AdvisoryCitation("s3://approved/manual.pdf", "Manual", page=1),),
        )


class _Transcribe:
    def transcribe(self, request, audio):
        return "  ask about nine one one\x00  "


class _Polly:
    def synthesize(self, request):
        assert request.spoken_text == "Call nine one one."
        return b"mp3"


class CloudAiRuntimeTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(
            socket.socket, "connect", side_effect=AssertionError("network access blocked")
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    def config(self, **overrides):
        values = {
            "mode": "advisory-rag",
            "tenant_id": "logan-synthetic",
            "knowledge_base_id": "AB12CD34EF",
            "documents_ingested": True,
            "allowed_s3_prefixes": ("s3://approved/tenant/",),
            "voice_enabled": True,
            "action_tools": [],
        }
        values.update(overrides)
        return CloudAiProviderConfig.from_mapping(values)

    def test_defaults_disable_ai_voice_tools_and_persistence(self):
        runtime = CloudAiRuntime(
            CloudAiProviderConfig.from_mapping(
                {"mode": "disabled", "tenant_id": "logan-synthetic", "action_tools": []}
            )
        )
        self.assertFalse(runtime.status.advisory_enabled)
        self.assertFalse(runtime.status.rag_available)
        self.assertFalse(runtime.status.voice_enabled)
        self.assertFalse(runtime.status.tts_ready)
        self.assertFalse(runtime.status.stt_ready)
        self.assertFalse(runtime.status.persistence_enabled)
        self.assertFalse(runtime.status.action_tools_enabled)
        denied = runtime.answer(
            AdvisoryRagRequest("request-1001", "logan-synthetic", "Question?")
        )
        self.assertTrue(denied.denied)
        self.network.assert_not_called()

    def test_rag_denies_without_ingested_documents_and_never_calls_provider(self):
        provider = _Advisory()
        runtime = CloudAiRuntime(
            self.config(documents_ingested=False), advisory=provider
        )
        denied = runtime.answer(
            AdvisoryRagRequest("request-1002", "logan-synthetic", "Question?")
        )
        self.assertEqual(denied.denial_reason, "Approved documents are not ingested.")
        self.assertEqual(provider.calls, 0)

    def test_voice_can_be_enabled_before_documents_without_action_tools(self):
        runtime = CloudAiRuntime(
            self.config(documents_ingested=False), transcribe=_Transcribe(), polly=_Polly()
        )
        self.assertTrue(runtime.status.voice_enabled)
        self.assertTrue(runtime.status.tts_ready)
        self.assertTrue(runtime.status.stt_ready)
        speech = PollySpeechRequest(
            "request-1010", "logan-synthetic", "Call 911.", PollyVoice.JOANNA
        )
        self.assertEqual(runtime.synthesize(speech), b"mp3")
        self.network.assert_not_called()

    def test_readiness_requires_each_injected_provider(self):
        no_providers = CloudAiRuntime(self.config())
        self.assertFalse(no_providers.status.voice_enabled)
        self.assertFalse(no_providers.status.tts_ready)
        self.assertFalse(no_providers.status.stt_ready)

        polly_only = CloudAiRuntime(self.config(), polly=_Polly())
        self.assertTrue(polly_only.status.voice_enabled)
        self.assertTrue(polly_only.status.tts_ready)
        self.assertFalse(polly_only.status.stt_ready)
        self.network.assert_not_called()

    def test_advisory_answer_is_cited_bounded_and_action_free(self):
        runtime = CloudAiRuntime(self.config(), advisory=_Advisory())
        response = runtime.answer(
            AdvisoryRagRequest("request-1003", "logan-synthetic", "Question?")
        )
        self.assertFalse(response.denied)
        self.assertTrue(response.advisory_only)
        self.assertFalse(response.action_executed)
        self.assertEqual(len(response.citations), 1)

    def test_voice_is_selectable_sanitized_bounded_and_nonpersistent(self):
        runtime = CloudAiRuntime(
            self.config(), transcribe=_Transcribe(), polly=_Polly()
        )
        transcribe_request = TranscribePushToTalkRequest(
            "request-1004",
            "logan-synthetic",
            PushToTalkAudioFormat.PCM,
            16000,
            1,
        )
        self.assertEqual(
            runtime.transcribe(transcribe_request, b"pcm"), "ask about nine one one"
        )
        for voice in (PollyVoice.MATTHEW, PollyVoice.JOANNA):
            speech = PollySpeechRequest(
                "request-1005", "logan-synthetic", "Call 911.", voice
            )
            self.assertEqual(runtime.synthesize(speech), b"mp3")
            self.assertFalse(speech.persist_audio)
        self.assertFalse(transcribe_request.persist_audio)
        self.assertFalse(transcribe_request.persist_transcript)

    def test_explicit_audio_and_transcript_limits_fail_with_sanitized_categories(self):
        runtime = CloudAiRuntime(
            self.config(max_input_audio_bytes=3),
            transcribe=_Transcribe(),
            polly=_Polly(),
        )
        request = TranscribePushToTalkRequest(
            "request-1006",
            "logan-synthetic",
            PushToTalkAudioFormat.PCM,
            16000,
            1,
        )
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "audio_input_limit"):
            runtime.transcribe(request, b"four")
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
