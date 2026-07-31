import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.nga911_nova_service import ask_nova


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

    @patch("app.main.get_nova_status")
    def test_nova_status_is_no_store(self, status_mock):
        status_mock.return_value = {"assistant": "NOVA", "connected": True}
        response = self.client.get("/api/nga911/v1/nova/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
