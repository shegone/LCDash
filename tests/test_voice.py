import unittest
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
            "stt": {"ready": True},
        }
        response = self.client.get("/api/voice/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["connected"])

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
                "## **Executive Summary**\n* **Network:** NGA911 is healthy.\n"
                "1. Review the [event details](https://example.test/event)."
            ),
            "Executive Summary. Network: N G A nine one one is healthy. Review the event details.",
        )


if __name__ == "__main__":
    unittest.main()
