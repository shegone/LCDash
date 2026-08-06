"""Synthetic, network-free cloud AI and voice provider contract tests."""

import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from app.integrations.cloud_ai import (
    AdvisoryCitation,
    AdvisoryRagRequest,
    AdvisoryRagResponse,
    CloudAiProviderConfig,
    CloudAdvisoryProvider,
    CloudPollyProvider,
    CloudTranscribeProvider,
    PollySpeechRequest,
    PollyVoice,
    TranscribePushToTalkRequest,
)
from app.integrations.cloud_ai.contracts import PushToTalkAudioFormat


ROOT = Path(__file__).resolve().parents[2]


class CloudAiProviderBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def live_config(self, **overrides):
        values = {
            "mode": "advisory-rag",
            "tenant_id": "logan-synthetic",
            "knowledge_base_id": "AB12CD34EF",
            "generation_model_id": "amazon.nova-micro-v1:0",
            "max_output_tokens": 512,
            "retrieval_result_limit": 5,
            "polly_voice": "Joanna",
            "transcribe_language_code": "en-US",
            "action_tools": [],
        }
        values.update(overrides)
        return values

    def test_schema_and_typed_config_are_bounded_and_action_free(self):
        schema = json.loads(
            (ROOT / "config/cloud_ai_provider.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["action_tools"]["maxItems"], 0)
        self.assertEqual(
            schema["properties"]["polly_voice"]["enum"], ["Matthew", "Joanna"]
        )
        config = CloudAiProviderConfig.from_mapping(self.live_config())
        self.assertEqual(config.max_output_tokens, 512)
        self.assertEqual(config.action_tools, ())
        self.network_mock.assert_not_called()

    def test_config_rejects_actions_secrets_unknown_models_and_unbounded_tokens(self):
        cases = (
            ({"action_tools": ["cad.update_call"]}, "action tools"),
            ({"password": "synthetic-not-a-secret"}, "Unknown"),
            ({"generation_model_id": "unreviewed.synthetic"}, "allowlist"),
            ({"max_output_tokens": 1201}, "between 64 and 1200"),
        )
        for override, pattern in cases:
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, pattern):
                    CloudAiProviderConfig.from_mapping(self.live_config(**override))
        self.network_mock.assert_not_called()

    def test_supported_advisory_answer_requires_a_real_citation(self):
        citation = AdvisoryCitation(
            source_uri="s3://synthetic-bucket/approved/manual.pdf",
            title="Synthetic Manual",
            page=7,
            section="Synthetic procedure",
            revision="test-only",
        )
        response = AdvisoryRagResponse.supported(
            "request-0001", "Synthetic supported answer.", (citation,)
        )
        self.assertTrue(response.advisory_only)
        self.assertFalse(response.action_executed)
        with self.assertRaisesRegex(ValueError, "require text and citations"):
            AdvisoryRagResponse.supported("request-0002", "Unsupported answer", ())

    def test_typed_interfaces_accept_synthetic_action_free_providers(self):
        class SyntheticAdvisory:
            def answer(self, request):
                return AdvisoryRagResponse.deny(
                    request.request_id, "Synthetic provider has no approved sources."
                )

        class SyntheticTranscribe:
            def transcribe(self, request, audio):
                return "synthetic transcript" if audio else ""

        class SyntheticPolly:
            def synthesize(self, request):
                return b"synthetic-audio"

        self.assertIsInstance(SyntheticAdvisory(), CloudAdvisoryProvider)
        self.assertIsInstance(SyntheticTranscribe(), CloudTranscribeProvider)
        self.assertIsInstance(SyntheticPolly(), CloudPollyProvider)
        self.network_mock.assert_not_called()

    def test_denial_is_sanitized_and_cannot_claim_an_action(self):
        response = AdvisoryRagResponse.deny(
            "request-0003", "No approved cited source supports this request."
        )
        self.assertTrue(response.denied)
        self.assertEqual(response.answer, "")
        self.assertEqual(response.citations, ())
        with self.assertRaisesRegex(ValueError, "advisory and action-free"):
            AdvisoryRagResponse(
                "request-0004", "", (), True, "Denied.", action_executed=True
            )

    def test_rag_request_has_no_tool_surface(self):
        request = AdvisoryRagRequest(
            "request-0005", "logan-synthetic", "What does the synthetic manual say?"
        )
        self.assertEqual(request.allowed_tools, ())
        for tool in ("cad.update_call", "dispatch.unit", "station-alert.release"):
            with self.subTest(tool=tool):
                with self.assertRaisesRegex(ValueError, "cannot expose"):
                    AdvisoryRagRequest(
                        "request-0006", "logan-synthetic", "Do something", (tool,)
                    )

    def test_push_to_talk_is_bounded_and_non_persistent(self):
        request = TranscribePushToTalkRequest(
            "request-0007",
            "logan-synthetic",
            PushToTalkAudioFormat.PCM,
            16000,
            12.5,
        )
        self.assertFalse(request.persist_audio)
        self.assertFalse(request.persist_transcript)
        with self.assertRaisesRegex(ValueError, "at most 30 seconds"):
            TranscribePushToTalkRequest(
                "request-0008",
                "logan-synthetic",
                PushToTalkAudioFormat.PCM,
                16000,
                31,
            )

    def test_polly_voices_and_nine_one_one_pronunciation_are_exact(self):
        for voice in (PollyVoice.MATTHEW, PollyVoice.JOANNA):
            request = PollySpeechRequest(
                "request-0009",
                "logan-synthetic",
                "NGA911 supports 9-1-1; never say 911 as a whole number.",
                voice,
            )
            self.assertEqual(request.engine, "neural")
            self.assertFalse(request.persist_audio)
            self.assertEqual(
                request.spoken_text,
                "N G A nine one one supports nine one one; never say nine one one as a whole number.",
            )
        with self.assertRaises(ValueError):
            PollyVoice("UnreviewedVoice")
        self.network_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
