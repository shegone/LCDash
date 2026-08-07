import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.nga911_nova_service import NOVAServiceError, ask_nova, get_nova_status


class NOVAServiceTests(unittest.TestCase):
    @patch("app.services.nga911_nova_service.httpx.post")
    def test_nova_is_grounded_in_intelligence_layer(self, post_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": "All five paths are represented; one LTE path is degraded."}}
        post_mock.return_value = response

        result = ask_nova("Summarize current network health.")

        self.assertEqual(result["assistant"], "NOVA")
        self.assertTrue(result["synthetic_data"])
        self.assertFalse(result["write_access"])
        self.assertIn("five paths", result["answer"])
        request = post_mock.call_args.kwargs["json"]
        system_prompt = request["messages"][0]["content"]
        context_prompt = request["messages"][-1]["content"]
        self.assertIn("read-only NGA911 Intelligence assistant", system_prompt)
        self.assertIn("Verizon Fiber", context_prompt)
        self.assertIn("Position 6", context_prompt)
        self.assertIn("synthetic", system_prompt)

    def test_cloud_mode_reports_unavailable_without_attempting_ollama(self):
        with (
            patch.object(settings, "deployment_mode", "synthetic-disconnected"),
            patch("app.services.nga911_nova_service.httpx.get") as get_mock,
            patch("app.services.nga911_nova_service.httpx.post") as post_mock,
        ):
            status = get_nova_status()
            with self.assertRaises(NOVAServiceError):
                ask_nova("Summarize current network health.")

        get_mock.assert_not_called()
        post_mock.assert_not_called()
        self.assertFalse(status["connected"])
        self.assertFalse(status["cloud_available"])
        self.assertIn("not available in the cloud pilot", status["disabled_reason"])

    def test_on_prem_mode_still_attempts_ollama(self):
        with (
            patch.object(settings, "deployment_mode", "on-prem"),
            patch("app.services.nga911_nova_service.httpx.get") as get_mock,
        ):
            get_mock.side_effect = httpx.ConnectError("connection refused")
            status = get_nova_status()

        get_mock.assert_called_once()
        self.assertFalse(status["connected"])
        self.assertNotIn("cloud_available", status)


class NOVAPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_standalone_nova_page_has_voice_reports_and_guardrails(self):
        response = self.client.get("/nga911/nova")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Network Operations Virtual Analyst", response.text)
        self.assertIn("Ask by voice", response.text)
        self.assertIn("Build 14-day report", response.text)
        self.assertIn("NGA911 only", response.text)
        self.assertIn("Read only", response.text)
        self.assertIn("/static/js/lcdash-nova.js?v=0.1.1", response.text)
        self.assertIn("detect your natural pause automatically", response.text)
        self.assertNotIn('href="/station-alerts"', response.text)

    def test_cloud_mode_page_shows_unavailable_banner_and_disables_composer(self):
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/nga911/nova")

        self.assertEqual(response.status_code, 200)
        self.assertIn("NOVA is not available in the cloud pilot yet", response.text)
        self.assertIn('id="nova-question" rows="2" maxlength="4000" '
                       'placeholder="NOVA is not available in the cloud pilot yet" '
                       'aria-label="NOVA question" disabled', response.text)
        self.assertIn('id="nova-send" type="submit" disabled', response.text)
        self.assertIn('id="nova-voice-toggle" type="button" disabled', response.text)

    def test_on_prem_mode_page_has_no_unavailable_banner(self):
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get("/nga911/nova")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("not available in the cloud pilot", response.text)
        self.assertNotIn('id="nova-question" rows="2" maxlength="4000" '
                          'placeholder="NOVA is not available in the cloud pilot yet"', response.text)

    def test_embedded_nova_page_uses_lcdash_shell(self):
        response = self.client.get("/nga911-intelligence/nova")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/station-alerts"', response.text)
        self.assertIn('href="/nga911-intelligence/operations"', response.text)

    @patch("app.main.ask_nova")
    def test_nova_chat_endpoint_accepts_history(self, ask_mock):
        ask_mock.return_value = {
            "answer": "Synthetic director summary",
            "assistant": "NOVA",
            "synthetic_data": True,
            "write_access": False,
        }
        response = self.client.post(
            "/api/nga911/v1/nova/chat",
            json={
                "question": "Build a report.",
                "history": [{"role": "user", "content": "Use 14 days."}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["assistant"], "NOVA")
        ask_mock.assert_called_once()

    def test_cloud_mode_chat_endpoint_returns_honest_503(self):
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.post(
                "/api/nga911/v1/nova/chat",
                json={"question": "Summarize current network health.", "history": []},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("not available in the cloud pilot", response.json()["detail"])

    @patch("app.main.get_nova_status")
    def test_nova_status_is_no_store(self, status_mock):
        status_mock.return_value = {"assistant": "NOVA", "connected": True}
        response = self.client.get("/api/nga911/v1/nova/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
