import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class VoicePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_voice_lab_page(self):
        response = self.client.get("/voice")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Voice Lab", response.text)
        self.assertIn("Give MAE a voice", response.text)
        self.assertIn("/static/js/lcdash-voice.js", response.text)

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


if __name__ == "__main__":
    unittest.main()
