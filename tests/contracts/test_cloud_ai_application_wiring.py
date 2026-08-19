"""Network-free contracts for cloud-only AI and voice application wiring."""

import ast
from pathlib import Path
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.integrations.cloud_ai import (
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    PollyVoice,
)
from app.services.cloud_ai_service import (
    CLOUD_POLLY_VOICES,
    CLOUD_TRANSCRIBE_AUDIO_FORMATS,
    answer_cloud_advisory,
    build_cloud_ai_config,
    build_cloud_ai_runtime,
    build_citation_only_runtime,
    cloud_ai_status,
    cloud_mode_enabled,
    synthesize_cloud_speech,
    transcribe_cloud_speech,
)
from app.services.cloud_ai_streaming import synthesize_cloud_sentence


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
        "cloud_ai_polly_voice": "Ruth",
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
        self.assertTrue(status["stt"]["ready"])
        self.assertFalse(status["action_tools_enabled"])
        self.assertIn("not ingested", status["disabled_reason"])
        self.assertEqual(
            {voice["id"] for voice in CLOUD_POLLY_VOICES}, {"Ruth", "Stephen"}
        )
        self.network.assert_not_called()

    def test_status_reports_jacks_own_voice_without_disturbing_maes(self):
        # Regression test: the /api/voice/status and /api/cloud-ai/status
        # endpoints used to call cloud_ai_status() with no persona at all, so
        # every caller -- MAE's page or JACK's -- received the identical,
        # single configured voice. JACK's frontend trusted that value
        # verbatim, so it ended up asking Polly for MAE's voice.
        settings = _settings(cloud_ai_mode="advisory-rag", cloud_ai_voice_enabled=True)
        config = build_cloud_ai_config(settings)
        runtime = build_cloud_ai_runtime(settings)
        # The default persona (what every pre-fix caller effectively used)
        # and an explicit "mae" persona must keep reporting the exact same,
        # unchanged voice.
        self.assertEqual(cloud_ai_status(config, runtime)["tts"]["voice"], "Ruth")
        self.assertEqual(
            cloud_ai_status(config, runtime, persona="mae")["tts"]["voice"],
            "Ruth",
        )
        # JACK must get its own, different voice from that same status call.
        self.assertEqual(
            cloud_ai_status(config, runtime, persona="jack")["tts"]["voice"],
            "Stephen",
        )
        self.network.assert_not_called()

    def test_jack_sentence_speech_is_pinned_to_its_own_voice(self):
        # Regression test for the reported bug: JACK's Listen button played
        # audio in MAE's voice. synthesize_cloud_sentence() backs the
        # /api/cloud-ai/speech/sentence endpoint both personas' "Listen"
        # buttons call; it used to accept a "persona" field and silently
        # discard it, trusting the caller-supplied "voice" unconditionally.
        settings = _settings(cloud_ai_mode="advisory-rag", cloud_ai_voice_enabled=True)
        config = build_cloud_ai_config(settings)

        class _RecordingPolly:
            def __init__(self):
                self.requests = []

            def synthesize(self, request):
                self.requests.append(request)
                return b"synthetic-mp3"

        polly = _RecordingPolly()
        runtime = CloudAiRuntime(config, polly=polly)

        # MAE keeps today's exact behavior: an explicit voice is honored...
        synthesize_cloud_sentence(
            runtime,
            config,
            request_id="request-cloud-2001",
            text="Call 911.",
            voice="Stephen",
            persona="mae",
        )
        self.assertEqual(polly.requests[-1].voice, PollyVoice.STEPHEN)
        # ...and an empty voice still falls back to the configured default.
        synthesize_cloud_sentence(
            runtime,
            config,
            request_id="request-cloud-2002",
            text="Call 911.",
            voice="",
            persona="mae",
        )
        self.assertEqual(polly.requests[-1].voice, PollyVoice.RUTH)

        # JACK always gets Stephen when no voice is supplied (the cold-start
        # path, before the browser has fetched a status response)...
        synthesize_cloud_sentence(
            runtime,
            config,
            request_id="request-cloud-2003",
            text="Call 911.",
            voice="",
            persona="jack",
        )
        self.assertEqual(polly.requests[-1].voice, PollyVoice.STEPHEN)
        # ...and also when a caller explicitly sends MAE's voice alongside
        # persona=jack -- the exact failure mode that shipped, since the
        # status endpoint used to hand every caller the same voice string.
        synthesize_cloud_sentence(
            runtime,
            config,
            request_id="request-cloud-2004",
            text="Call 911.",
            voice="Ruth",
            persona="jack",
        )
        self.assertEqual(polly.requests[-1].voice, PollyVoice.STEPHEN)
        self.network.assert_not_called()

    def test_cloud_transcription_is_wired_but_rejects_unstreamable_audio(self):
        settings = _settings(cloud_ai_mode="advisory-rag", cloud_ai_voice_enabled=True)
        config = build_cloud_ai_config(settings)
        runtime = build_cloud_ai_runtime(settings)
        self.assertTrue(runtime.status.stt_ready)
        self.assertEqual(set(CLOUD_TRANSCRIBE_AUDIO_FORMATS), {"pcm", "ogg-opus"})
        with self.assertRaisesRegex(
            CloudAiRuntimeUnavailable, "transcribe_format_not_allowed"
        ):
            transcribe_cloud_speech(
                runtime,
                config,
                request_id="request-cloud-1004",
                audio=b"synthetic-audio",
                audio_format="webm-opus",
                sample_rate_hz=48000,
                duration_seconds=2.0,
            )
        with self.assertRaisesRegex(
            CloudAiRuntimeUnavailable, "transcribe_request_not_allowed"
        ):
            transcribe_cloud_speech(
                runtime,
                config,
                request_id="request-cloud-1005",
                audio=b"synthetic-audio",
                audio_format="pcm",
                sample_rate_hz=16000,
                duration_seconds=45.0,
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
                voice="Ruth",
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
        self.assertIn("Ruth and Stephen are enabled", template)
        self.assertIn("cloud_voice and not tts_enabled", template)
        self.assertIn("cloud_voice and not stt_enabled", template)
        self.assertIn("ttsReady = !cloudMode", script)
        self.assertIn("sttReady = !cloudMode", script)
        self.assertIn("speakButton.disabled = !ttsReady", script)
        self.assertIn("recordButton.disabled = !sttReady", script)
        self.assertIn("No CAD, dispatch, paging, alert, radio, or ESInet tools", template)


class BuildCallLookupFnTests(unittest.TestCase):
    """The get_call wrapper the verified-facts path uses for CFS lookups."""

    def test_returns_none_without_a_connector_provider(self):
        from app.services.cloud_ai_service import build_call_lookup_fn

        self.assertIsNone(build_call_lookup_fn(None))

    def test_connector_is_resolved_lazily_not_at_wrap_time(self):
        from app.services.cloud_ai_service import build_call_lookup_fn

        class ExplodingProvider:
            @property
            def connector(self):
                raise AssertionError("connector built before a lookup was needed")

        # Wrapping must not touch the lazy connector; only calling may.
        lookup = build_call_lookup_fn(ExplodingProvider())
        self.assertTrue(callable(lookup))

    def test_disabled_connector_reports_error_status(self):
        from app.services.cloud_ai_service import build_call_lookup_fn

        provider = SimpleNamespace(connector=None)
        lookup = build_call_lookup_fn(provider)
        self.assertEqual(lookup("CFS26-27243")["status"], "error")

    def test_404_maps_to_not_found_and_other_errors_to_error(self):
        from app.integrations.cad.cloud_read_connector import CloudCadConnectorError
        from app.services.cloud_ai_service import build_call_lookup_fn

        class Connector:
            def __init__(self, error):
                self._error = error

            def get_call(self, cfs_number):
                raise self._error

        missing = CloudCadConnectorError("not_found", "get_call", status_code=404)
        lookup = build_call_lookup_fn(SimpleNamespace(connector=Connector(missing)))
        self.assertEqual(lookup("CFS26-27243")["status"], "not_found")

        outage = CloudCadConnectorError("upstream_unavailable", "get_call", status_code=503)
        lookup = build_call_lookup_fn(SimpleNamespace(connector=Connector(outage)))
        self.assertEqual(lookup("CFS26-27243")["status"], "error")

    def test_raw_call_is_normalized_to_the_snapshot_shape(self):
        from app.services.cloud_ai_service import build_call_lookup_fn

        raw = {
            "CFSNumber": "CFS26-27243",
            "Priority": {"Level": "30"},
            "CallDateTime": "2026-08-19T02:25:18Z",
            "CommandLog": [
                {"Timestamp": "02:25", "Text": "Complaint reported at the ER."}
            ],
        }

        class Connector:
            def get_call(self, cfs_number):
                return raw

        lookup = build_call_lookup_fn(SimpleNamespace(connector=Connector()))
        result = lookup("CFS26-27243")
        self.assertEqual(result["status"], "ok")
        call = result["call"]
        self.assertEqual(call["cfs_number"], "CFS26-27243")
        self.assertEqual(call["priority"], "30")
        self.assertEqual(call["command_logs"][0]["text"], "Complaint reported at the ER.")


if __name__ == "__main__":
    unittest.main()
