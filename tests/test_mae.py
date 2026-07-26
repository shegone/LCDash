import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mae_service import SYSTEM_PROMPT, ask_mae


class MAEPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_mae_page_has_identity_guardrail_and_chat_assets(self):
        response = self.client.get("/mae")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mission Assistance Engine", response.text)
        self.assertIn("Inquiry-only mode", response.text)
        self.assertIn("/static/css/lcdash-mae.css", response.text)
        self.assertIn("/static/js/lcdash-mae.js", response.text)

    @patch("app.main.get_mae_status")
    def test_status_endpoint_reports_inquiry_only(self, status_mock):
        status_mock.return_value = {
            "mode": "Inquiry only",
            "write_access": False,
        }

        response = self.client.get("/api/mae/status")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["write_access"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    @patch("app.main.ask_mae")
    def test_chat_endpoint_accepts_question_and_history(self, ask_mock):
        ask_mock.return_value = {
            "answer": "Three active calls.",
            "sources": [],
            "write_access": False,
        }

        response = self.client.post(
            "/api/mae/chat",
            json={
                "question": "How many calls are active?",
                "history": [{"role": "user", "content": "Hello"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Three active calls.")
        self.assertEqual(response.headers["cache-control"], "no-store")
        ask_mock.assert_called_once()


class MAEGuardrailTests(unittest.TestCase):
    def test_system_prompt_uses_logan_priority_direction(self):
        self.assertIn("lower numeric priority values are more urgent", SYSTEM_PROMPT)
        self.assertIn("Never describe priority 30 as high priority", SYSTEM_PROMPT)

    def test_write_request_is_refused_without_data_or_model_calls(self):
        with (
            patch("app.services.mae_service._build_read_context") as context_mock,
            patch("app.services.mae_service.httpx.post") as post_mock,
        ):
            result = ask_mae("Dispatch MED10 and close the call.")

        self.assertFalse(result["write_access"])
        self.assertIn("inquiry-only", result["answer"].lower())
        context_mock.assert_not_called()
        post_mock.assert_not_called()

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_historical_question_uses_database_context(
        self,
        analytics_mock,
        post_mock,
    ):
        analytics_mock.return_value = {
            "available": True,
            "period_label": "Last 7 days",
            "latest_data_at": "2026-07-26T12:00:00-04:00",
            "metrics": {"total_calls": 42},
        }
        fake_response = unittest.mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {"content": "There were 42 calls."}
        }
        post_mock.return_value = fake_response

        result = ask_mae("How many calls were there last week?")

        analytics_mock.assert_called_once_with(period="7d")
        self.assertEqual(result["answer"], "There were 42 calls.")
        self.assertEqual(result["sources"][0]["kind"], "historical")


if __name__ == "__main__":
    unittest.main()
