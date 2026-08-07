import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.voice_service import VOICE_CHOICES, prepare_text_for_speech


class VoicePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_voice_lab_page(self):
        response = self.client.get("/voice")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Voice Lab", response.text)
        self.assertIn("Give MAE a voice", response.text)
        self.assertIn("RTX 3090 local engine", response.text)
        self.assertIn("Nicole", response.text)
        self.assertIn("Fenrir", response.text)
        self.assertIn("/static/js/lcdash-voice.js", response.text)

    def test_local_voice_catalog(self):
        voice_ids = {voice["id"] for voice in VOICE_CHOICES}
        self.assertEqual(
            voice_ids,
            {
                "mae-synthetic-female",
                "jack-synthetic-southern-male",
                "af_heart",
                "af_bella",
                "af_nicole",
                "af_sarah",
                "af_kore",
                "am_adam",
                "am_fenrir",
                "am_michael",
                "am_puck",
            },
        )

    @patch("app.main.get_voice_status")
    def test_voice_status_endpoint(self, get_status):
        get_status.return_value = {
            "connected": True,
            "tts": {"ready": True},
            "jack_tts": {"ready": True},
            "stt": {"ready": True},
        }
        response = self.client.get("/api/voice/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["connected"])

    @patch("app.main.get_voice_status")
    def test_voice_status_endpoint_accepts_a_persona_and_rejects_unknown_ones(
        self, get_status
    ):
        # /api/voice/status is the endpoint both MAE's and JACK's frontends
        # poll to learn which Polly voice to ask for; it must accept a
        # persona for either assistant and reject anything else outright,
        # the same way the sibling advisory/speech request bodies do.
        get_status.return_value = {
            "connected": True,
            "tts": {"ready": True},
            "jack_tts": {"ready": True},
            "stt": {"ready": True},
        }
        for persona in ("mae", "jack"):
            response = self.client.get(f"/api/voice/status?persona={persona}")
            self.assertEqual(response.status_code, 200)

        rejected = self.client.get("/api/voice/status?persona=bogus")
        self.assertEqual(rejected.status_code, 422)

    @patch("app.main.synthesize_speech")
    def test_speech_endpoint(self, synthesize):
        synthesize.return_value = (b"audio", "audio/mpeg")
        response = self.client.post(
            "/api/voice/speech",
            json={
                "text": "MAE is ready.",
                "voice": "af_heart",
                "speed": 1.0,
                "response_format": "mp3",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"audio")
        self.assertEqual(response.headers["content-type"], "audio/mpeg")

    @patch("app.main.synthesize_cloud_sentence")
    def test_cloud_sentence_speech_endpoint_forwards_persona_per_assistant(
        self, synthesize
    ):
        # End-to-end regression test for the reported bug: hits the exact
        # HTTP contract both MAE's and JACK's "Listen" buttons call and
        # confirms the persona each browser sends actually reaches the
        # voice resolver, instead of being accepted and silently dropped.
        synthesize.return_value = b"synthetic-mp3"
        with patch("app.main.settings.deployment_mode", "synthetic-disconnected"):
            jack_response = self.client.post(
                "/api/cloud-ai/speech/sentence",
                json={"text": "Copy that.", "persona": "jack", "voice": ""},
            )
            mae_response = self.client.post(
                "/api/cloud-ai/speech/sentence",
                json={"text": "Copy that.", "persona": "mae", "voice": ""},
            )
            rejected = self.client.post(
                "/api/cloud-ai/speech/sentence",
                json={"text": "Copy that.", "persona": "bogus", "voice": ""},
            )

        self.assertEqual(jack_response.status_code, 200)
        self.assertEqual(mae_response.status_code, 200)
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(synthesize.call_args_list[0].kwargs["persona"], "jack")
        self.assertEqual(synthesize.call_args_list[1].kwargs["persona"], "mae")

    def test_lcdash_pronunciation_rules(self):
        self.assertEqual(
            prepare_text_for_speech(
                "MAE supports Logan County 911 and a transferred 9-1-1 call."
            ),
            "May supports Logan County nine one one and a transferred nine one one call.",
        )
        self.assertEqual(
            prepare_text_for_speech("NGA911 protects 911 calls."),
            "N G A nine one one protects nine one one calls.",
        )
        self.assertEqual(
            prepare_text_for_speech(
                "Dispatch time is 1523. At 08:05, the unit updated."
            ),
            "Dispatch time is fifteen twenty-three. At zero eight oh five, the unit updated.",
        )
        self.assertEqual(
            prepare_text_for_speech("The call was received at 15:00."),
            "The call was received at fifteen hundred.",
        )
        self.assertEqual(
            prepare_text_for_speech(
                "## **Executive Summary**\n* **Network:** NGA911 is healthy.\n"
                "1. Review the [event details](https://example.test/event)."
            ),
            "Executive Summary. Network: N G A nine one one is healthy. Review the event details.",
        )

    def test_jack_uses_fixed_voice_and_prevents_stale_playback(self):
        script = (Path(__file__).parents[1] / "static/js/lcdash-mindshare.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(': "jack-synthetic-southern-male"', script)
        self.assertIn("speed: cloudMode ? 1.0 : 0.92", script)
        self.assertIn("controller.abort()", script)
        self.assertIn("status.jack_tts.ready", script)
        self.assertIn("let synthesisChain = Promise.resolve()", script)
        self.assertIn("const audioPromise = synthesisChain.then", script)
        self.assertIn("const SPEECH_GROUP_TARGET = 180", script)
        self.assertIn("group.length >= SPEECH_GROUP_TARGET", script)
        self.assertIn("function answerForSpeech(text)", script)
        self.assertIn("answerForSpeech(text)", script)
        self.assertIn("/knowledge/documents/mindshare/${item.document_id}", script)

    def test_live_stt_uses_cpu_to_avoid_jack_gpu_contention(self):
        compose = (Path(__file__).parents[1] / "deploy/compose.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("WHISPER__INFERENCE_DEVICE: cpu", compose)
        self.assertIn("WHISPER__COMPUTE_TYPE: int8", compose)
        self.assertIn("WHISPER__CPU_THREADS: 8", compose)


if __name__ == "__main__":
    unittest.main()
