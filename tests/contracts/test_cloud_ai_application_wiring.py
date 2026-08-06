"""Network-free contracts for cloud-only AI and voice application wiring."""

import ast
from pathlib import Path
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.integrations.cloud_ai import CloudAiRuntimeUnavailable
from app.services.cloud_ai_service import (
    CLOUD_POLLY_VOICES,
    answer_cloud_advisory,
    build_cloud_ai_config,
    build_cloud_ai_runtime,
    build_citation_only_runtime,
    cloud_ai_status,
    cloud_mode_enabled,
    synthesize_cloud_speech,
)


ROOT = Path(__file__).resolve().parents[2]


def _settings(**overrides):
    values = {
        "tenant_id": "logan-synthetic",
        "deployment_mode": "synthetic-disconnected",
        "cloud_ai_mode": "disabled",
        "cloud_ai_knowledge_base_id": "",
        "cloud_ai_documents_ingested": False,
        "cloud_ai_generation_model_id": "amazon.nova-micro-v1:0",
        "cloud_ai_max_output_tokens": 512,
        "cloud_ai_retrieval_result_limit": 5,
        "cloud_ai_retrieval_score_threshold": 0.5,
        "cloud_ai_allowed_s3_prefixes": (
            "s3://private/tenants/logan-synthetic/approved/",
        ),
        "cloud_ai_polly_voice": "Joanna",
        "cloud_ai_voice_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CloudAiApplicationWiringTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(
            socket.socket, "connect", side_effect=AssertionError("network blocked")
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    def test_cloud_release_is_advisory_action_free_and_offers_both_voices(self):
        settings = _settings(cloud_ai_mode="advisory-rag", cloud_ai_voice_enabled=True)
        config = build_cloud_ai_config(settings)
        runtime = build_cloud_ai_runtime(settings)
        status = cloud_ai_status(config, runtime)
        self.assertTrue(cloud_mode_enabled(settings))
        self.assertFalse(status["documents_ingested"])
        self.assertTrue(status["voice_enabled"])
        self.assertTrue(status["tts"]["ready"])
        self.assertFalse(status["stt"]["ready"])
        self.assertFalse(status["action_tools_enabled"])
        self.assertIn("not ingested", status["disabled_reason"])
        self.assertEqual(
            {voice["id"] for voice in CLOUD_POLLY_VOICES}, {"Matthew", "Joanna"}
        )
        self.network.assert_not_called()

    def test_advisory_and_voice_fail_closed_before_document_gate(self):
        settings = _settings()
        runtime = build_cloud_ai_runtime(settings)
        response = answer_cloud_advisory(
            runtime,
            build_cloud_ai_config(settings),
            request_id="request-cloud-1001",
            question="What does the approved manual say?",
        )
        self.assertTrue(response["denied"])
        self.assertTrue(response["advisory_only"])
        self.assertFalse(response["action_executed"])
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "polly_unavailable"):
            synthesize_cloud_speech(
                runtime,
                build_cloud_ai_config(settings),
                request_id="request-cloud-1002",
                text="Call 911.",
                voice="Matthew",
            )
        self.network.assert_not_called()

    def test_on_prem_mode_remains_on_the_legacy_branch(self):
        self.assertFalse(cloud_mode_enabled(_settings(deployment_mode="on-prem")))
        tree = ast.parse((ROOT / "app/main.py").read_text(encoding="utf-8"))
        source = ast.unparse(tree)
        self.assertIn("if cloud_mode_enabled(settings):", source)
        self.assertIn("return get_voice_status()", source)
        self.assertIn("synthesize_speech", source)
        self.assertIn("transcribe_audio", source)

    def test_citation_only_runtime_is_dormant_until_ingestion_gate(self):
        class Client:
            calls = 0

            def retrieve(self, **kwargs):
                self.calls += 1
                return {"retrievalResults": []}

        client = Client()
        runtime = build_citation_only_runtime(_settings(), retrieve_client=client)
        response = answer_cloud_advisory(
            runtime,
            build_cloud_ai_config(_settings()),
            request_id="request-cloud-1003",
            question="What is approved?",
        )
        self.assertTrue(response["denied"])
        self.assertEqual(client.calls, 0)

    def test_cloud_template_enables_voices_and_names_document_gate(self):
        template = (ROOT / "templates/voice_lab.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/lcdash-voice.js").read_text(encoding="utf-8")
        self.assertIn("Matthew and Joanna are enabled", template)
        self.assertIn("cloud_voice and not tts_enabled", template)
        self.assertIn("cloud_voice and not stt_enabled", template)
        self.assertIn("ttsReady = !cloudMode", script)
        self.assertIn("sttReady = !cloudMode", script)
        self.assertIn("speakButton.disabled = !ttsReady", script)
        self.assertIn("recordButton.disabled = !sttReady", script)
        self.assertIn("No CAD, dispatch, paging, alert, radio, or ESInet tools", template)


if __name__ == "__main__":
    unittest.main()
