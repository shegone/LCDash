import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mae_service import (
    SYSTEM_PROMPT,
    _hours_from_question,
    ask_mae,
)


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
    def test_recent_hour_phrase_is_parsed_exactly(self):
        self.assertEqual(_hours_from_question("calls in the last 3 hrs"), 3)
        self.assertEqual(_hours_from_question("past 12 hours"), 12)
        self.assertIsNone(_hours_from_question("calls last month"))

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

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_recent_cad_activity")
    @patch("app.services.mae_service.get_recent_database_activity")
    def test_three_hour_question_uses_exact_database_and_cad_windows(
        self,
        database_mock,
        cad_mock,
        post_mock,
    ):
        database_mock.return_value = {
            "available": True,
            "hours": 3,
            "completed_calls_stored": 8,
            "latest_stored_at": "2026-07-26T18:00:00+00:00",
        }
        cad_mock.return_value = {
            "available": True,
            "hours": 3,
            "calls_returned": 9,
            "generated_at": "2026-07-26T18:05:00+00:00",
        }
        fake_response = unittest.mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {"content": "Nine calls were created in the last 3 hours."}
        }
        post_mock.return_value = fake_response

        result = ask_mae("How many calls in the last 3 hrs?")

        database_mock.assert_called_once_with(3)
        cad_mock.assert_called_once_with(3)
        post_mock.assert_not_called()
        self.assertEqual(len(result["sources"]), 2)
        self.assertIn("Last 3 hours", result["sources"][1]["detail"])
        self.assertIn("9 calls", result["answer"])
        self.assertIn("8 completed calls", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_recent_cad_activity")
    @patch("app.services.mae_service.get_recent_database_activity")
    def test_latest_call_question_uses_recent_sources(
        self,
        database_mock,
        cad_mock,
        post_mock,
    ):
        database_mock.return_value = {"available": True, "hours": 24}
        cad_mock.return_value = {
            "available": True,
            "hours": 24,
            "latest_call": {"cfs_number": "CFS26-50001"},
        }
        fake_response = unittest.mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {"content": "The latest call is CFS26-50001."}
        }
        post_mock.return_value = fake_response

        result = ask_mae("What was the last call made?")

        database_mock.assert_not_called()
        cad_mock.assert_called_once_with(24)
        post_mock.assert_not_called()
        self.assertIn("CFS26-50001", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_recent_cad_activity")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_comparison_question_checks_database_and_live_cad(
        self,
        analytics_mock,
        live_mock,
        recent_cad_mock,
        post_mock,
    ):
        analytics_mock.return_value = {
            "available": True,
            "period_key": "30d",
            "period_label": "Last 30 days",
            "latest_data_at": "2026-07-26T18:00:00+00:00",
            "metrics": {"total_calls": 500},
        }
        recent_cad_mock.return_value = {
            "available": True,
            "hours": 3,
            "calls_returned": 5,
            "generated_at": "2026-07-26T18:05:00+00:00",
            "truncated": False,
        }
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 9},
            "calls": [],
            "unit_stats": {},
            "unit_rows": [],
        }
        fake_response = unittest.mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {"content": "Current workload is above the historical baseline."}
        }
        post_mock.return_value = fake_response

        result = ask_mae("Are we busier than normal right now?")

        analytics_mock.assert_called_once_with(period="30d")
        recent_cad_mock.assert_called_once_with(3)
        live_mock.assert_called_once()
        post_mock.assert_not_called()
        self.assertIn("5 calls created", result["answer"])
        self.assertIn("9 active calls", result["answer"])
        self.assertTrue(result["research"]["database_first"])
        self.assertTrue(result["research"]["live_verified"])
        self.assertTrue(result["research"]["compared_sources"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_mae_unit_snapshot")
    def test_unit_question_uses_full_live_roster(self, units_mock, post_mock):
        units_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "roster_stats": {"available_units": 4},
            "available_units": [{"unit_number": "MED10"}],
        }
        fake_response = unittest.mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "message": {"content": "Four units are available."}
        }
        post_mock.return_value = fake_response

        result = ask_mae("Which units are available right now?")

        units_mock.assert_called_once()
        self.assertTrue(result["research"]["live_verified"])
        self.assertFalse(result["research"]["database_first"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_current_active_call_count_is_live_only_and_deterministic(
        self,
        analytics_mock,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 8},
            "calls": [
                {
                    "cfs_number": "CFS26-50001",
                    "incident_description": "Test call",
                    "status": "On Scene",
                }
            ],
            "unit_stats": {"total_units": 12},
            "unit_rows": [],
        }

        result = ask_mae("How many calls are active at the moment?")

        analytics_mock.assert_not_called()
        live_mock.assert_called_once()
        post_mock.assert_not_called()
        self.assertIn("8 active calls", result["answer"])
        self.assertEqual(
            [source["kind"] for source in result["sources"]],
            ["live"],
        )

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_plural_calls_in_progress_returns_verified_live_list(
        self,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 2},
            "calls": [
                {
                    "cfs_number": "CFS26-50001",
                    "incident_description": "First test",
                    "status": "Enroute",
                },
                {
                    "cfs_number": "CFS26-50002",
                    "incident_description": "Second test",
                    "status": "On Scene",
                },
            ],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae("Name the calls in progress.")

        post_mock.assert_not_called()
        self.assertIn("2 active calls", result["answer"])
        self.assertIn("CFS26-50001", result["answer"])
        self.assertIn("CFS26-50002", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_count_challenge_refreshes_previous_active_call_subject(
        self,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 8},
            "calls": [],
            "unit_stats": {},
            "unit_rows": [],
        }
        history = [
            {
                "role": "user",
                "content": "How many calls are active at the moment?",
            },
            {"role": "assistant", "content": "There are 5 active calls."},
        ]

        result = ask_mae(
            "Why did you say 5 when CAD shows 8?",
            history,
        )

        post_mock.assert_not_called()
        self.assertIn("8 active calls", result["answer"])
        self.assertIn("prior total", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    @patch("app.services.mae_service.get_mae_unit_snapshot")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_combined_unit_and_call_count_uses_both_live_snapshots(
        self,
        analytics_mock,
        units_mock,
        live_mock,
        post_mock,
    ):
        units_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "roster_stats": {"active_units": 18},
        }
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 8},
            "calls": [],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae(
            "How many units are currently active, and how many active calls "
            "are they assigned to?"
        )

        analytics_mock.assert_not_called()
        units_mock.assert_called_once()
        live_mock.assert_called_once()
        post_mock.assert_not_called()
        self.assertIn("18 active units", result["answer"])
        self.assertIn("8 active calls", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_completed_call_count_is_deterministic_historical_only(
        self,
        analytics_mock,
        post_mock,
    ):
        analytics_mock.return_value = {
            "available": True,
            "period_key": "7d",
            "period_label": "Last 7 days",
            "latest_data_at": "2026-07-26T18:00:00+00:00",
            "metrics": {"total_calls": 42},
        }

        result = ask_mae(
            "How many completed calls were recorded during the last 7 days?"
        )

        post_mock.assert_not_called()
        self.assertIn("42 completed calls", result["answer"])
        self.assertIn("historical", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_recent_cad_activity")
    def test_latest_call_followup_uses_cfs_from_assistant_history(
        self,
        recent_cad_mock,
        call_detail_mock,
        post_mock,
    ):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-50001",
            "incident_description": "Test call",
            "status": "On Scene",
            "assigned_units": [],
            "raw": {},
        }
        history = [
            {"role": "user", "content": "What was the latest call?"},
            {
                "role": "assistant",
                "content": "The latest call is CFS26-50001.",
            },
        ]

        result = ask_mae("Is that call still active?", history)

        recent_cad_mock.assert_not_called()
        call_detail_mock.assert_called_once_with("CFS26-50001")
        post_mock.assert_not_called()
        self.assertIn("On Scene", result["answer"])
        self.assertIn("remains active", result["answer"])


if __name__ == "__main__":
    unittest.main()
