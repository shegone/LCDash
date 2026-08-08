import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.services.mae_service as mae_service
from app.main import app
from app.services.mae_service import (
    SYSTEM_PROMPT,
    _cached_live_operations_snapshot,
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
        self.assertIn("Start voice mode", response.text)
        self.assertIn('id="mae-voice-session"', response.text)
        self.assertIn('id="mae-voice-player"', response.text)
        self.assertIn("/static/css/lcdash-mae.css", response.text)
        self.assertIn("Analytics Studio", response.text)
        self.assertIn("Busiest weekdays", response.text)
        self.assertIn("Peak hours", response.text)
        self.assertIn("Dispatcher workload", response.text)
        self.assertIn(
            "Show me a chart of the busiest days of the week for the last 30 days.",
            response.text,
        )
        self.assertIn(
            "/static/js/lcdash-mae.js?v=20260803-custom-analytics",
            response.text,
        )
        self.assertIn("/static/img/mae/mae-neutral.jpg", response.text)
        self.assertIn('alt="MAE virtual assistant"', response.text)

        stylesheet = self.client.get("/static/css/lcdash-mae.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("position: sticky", stylesheet.text)
        self.assertIn("calc(100vh - 560px)", stylesheet.text)
        self.assertIn(".mae-portrait", stylesheet.text)
        self.assertIn(".mae-avatar-assistant", stylesheet.text)

        script = self.client.get("/static/js/lcdash-mae.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("const maeRequestTimeoutMs = 130000;", script.text)
        self.assertIn("}, maeRequestTimeoutMs);", script.text)

        avatar = self.client.get("/static/img/mae/mae-neutral.jpg")
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(avatar.headers["content-type"], "image/jpeg")

    def test_mae_browser_uses_streamed_synthesize_ahead_voice(self):
        script = (Path(__file__).parents[1] / "static/js/lcdash-mae.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/api/mae/chat/stream"', script)
        self.assertIn("let synthesisChain = Promise.resolve()", script)
        self.assertIn("const audioPromise = synthesisChain.then", script)
        self.assertIn("groupedSpeech.length >= 140", script)
        self.assertIn("let alreadySpoken = false", script)

    def test_mae_write_refusal_includes_assurance_and_timing(self):
        result = ask_mae("Dispatch MED10 and close the call.")
        self.assertFalse(result["write_access"])
        self.assertEqual(result["assurance"]["authority"], "MAE safety policy")
        self.assertIn("total_ms", result["timing"])

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

    @patch("app.main.build_analytics_report", return_value=b"%PDF-synthetic")
    @patch("app.main.get_analytics_overview")
    def test_analytics_report_endpoint_returns_download_only_pdf(
        self,
        analytics_mock,
        report_mock,
    ):
        analytics_mock.return_value = {"available": True, "period_label": "Last 7 days"}

        response = self.client.post(
            "/api/mae/analytics-report",
            json={"period": "7d"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-synthetic")
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("attachment", response.headers["content-disposition"])
        analytics_mock.assert_called_once_with(period="7d")
        report_mock.assert_called_once_with(analytics_mock.return_value, "")

    @patch("app.main.get_analytics_overview", return_value={"available": False})
    def test_analytics_report_requires_available_historical_analytics(
        self,
        analytics_mock,
    ):
        response = self.client.post("/api/mae/analytics-report", json={"period": "7d"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["content-type"], "application/json")
        analytics_mock.assert_called_once_with(period="7d")

    @patch("app.main.record_mae_interaction")
    @patch("app.main.ask_mae")
    def test_chat_endpoint_accepts_question_and_history(
        self,
        ask_mock,
        audit_mock,
    ):
        ask_mock.return_value = {
            "answer": "Three active calls.",
            "sources": [],
            "write_access": False,
        }
        audit_mock.return_value = {
            "saved": True,
            "interaction_id": "11111111-1111-1111-1111-111111111111",
        }

        response = self.client.post(
            "/api/mae/chat",
            headers={
                "cf-access-authenticated-user-email": "supervisor@example.com"
            },
            json={
                "question": "How many calls are active?",
                "history": [{"role": "user", "content": "Hello"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Three active calls.")
        self.assertTrue(response.json()["audit_saved"])
        self.assertEqual(
            response.json()["interaction_id"],
            "11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        ask_mock.assert_called_once()
        audit_mock.assert_called_once()
        self.assertEqual(
            audit_mock.call_args.kwargs["user_email"],
            "supervisor@example.com",
        )

    @patch("app.main.record_mae_interaction")
    @patch("app.main.ask_mae")
    def test_stream_endpoint_emits_tokens_and_final_payload(self, ask_mock, audit_mock):
        def streamed(question, history, entities, token_callback=None):
            token_callback("Three active calls.")
            return {"answer": "Three active calls.", "sources": [], "write_access": False}

        ask_mock.side_effect = streamed
        audit_mock.return_value = {"saved": True, "interaction_id": "test-id"}
        response = self.client.post(
            "/api/mae/chat/stream",
            headers={"cf-access-authenticated-user-email": "supervisor@example.com"},
            json={"question": "How many calls are active?", "history": []},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"token"', response.text)
        self.assertIn('"type":"complete"', response.text)

    @patch("app.main.record_mae_feedback")
    def test_feedback_endpoint_records_supervisor_rating(self, feedback_mock):
        feedback_mock.return_value = {
            "saved": True,
            "interaction_id": "11111111-1111-1111-1111-111111111111",
            "rating": "helpful",
        }

        response = self.client.post(
            "/api/mae/feedback",
            headers={
                "cf-access-authenticated-user-email": "supervisor@example.com"
            },
            json={
                "interaction_id": "11111111-1111-1111-1111-111111111111",
                "rating": "helpful",
                "comment": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        self.assertEqual(
            feedback_mock.call_args.kwargs["user_email"],
            "supervisor@example.com",
        )


class MAEGuardrailTests(unittest.TestCase):
    def test_production_live_snapshot_cache_calls_operations_service_once(self):
        snapshot = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 2},
            "calls": [],
            "unit_stats": {},
            "unit_rows": [],
        }
        mae_service._LIVE_SNAPSHOT_CACHE["stored_at"] = 0.0
        mae_service._LIVE_SNAPSHOT_CACHE["value"] = None

        with (
            patch.object(mae_service.settings, "debug", False),
            patch(
                "app.services.mae_service.get_live_operations_snapshot",
                return_value=snapshot,
            ) as live_mock,
        ):
            first = _cached_live_operations_snapshot()
            second = _cached_live_operations_snapshot()

        self.assertIs(first, snapshot)
        self.assertIs(second, snapshot)
        live_mock.assert_called_once()

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
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["options"]["num_ctx"],
            8192,
        )
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["options"]["num_predict"],
            120,
        )

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
    @patch("app.services.mae_service.get_live_operations_snapshot")
    @patch("app.services.mae_service.get_latest_completed_calls_by_incident")
    def test_latest_five_calls_by_incident_type_are_verified_from_database(
        self,
        incident_calls_mock,
        live_mock,
        post_mock,
    ):
        incident_calls_mock.return_value = {
            "available": True,
            "incident_type": "chest pain",
            "requested_limit": 5,
            "calls_returned": 2,
            "latest_stored_at": "2026-08-01T16:00:00+00:00",
            "calls": [
                {
                    "cfs_number": "CFS26-51005",
                    "call_received_at": "2026-08-01T16:00:00+00:00",
                    "incident_description": "Chest Pain",
                    "priority": "15",
                    "city": "LOGAN",
                },
                {
                    "cfs_number": "CFS26-50991",
                    "call_received_at": "2026-08-01T14:30:00+00:00",
                    "incident_description": "Chest Pain / Discomfort",
                    "priority": "10",
                    "city": "CHAPMANVILLE",
                },
            ],
        }

        result = ask_mae("Show me the last five chest pain calls.")

        incident_calls_mock.assert_called_once_with("chest pain", 5)
        live_mock.assert_not_called()
        post_mock.assert_not_called()
        self.assertIn("latest 2 completed calls", result["answer"])
        self.assertIn("- 1. CFS26-51005", result["answer"])
        self.assertIn("- 2. CFS26-50991", result["answer"])
        self.assertIn("call narrative", result["answer"])
        self.assertEqual(result["sources"][0]["kind"], "historical")

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    def test_basic_call_summary_omits_command_log_details(
        self,
        call_detail_mock,
        post_mock,
    ):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-51005",
            "incident_description": "Chest Pain",
            "status": "Closed",
            "priority": "15",
            "location": "100 TEST STREET, LOGAN",
            "call_datetime": "2026-08-01T16:00:00+00:00",
            "assigned_units": [{"unit_number": "MED10", "status": "Clear"}],
            "reporter": {"name": "TEST REPORTER"},
            "command_logs": [{"text": "PRIVATE DETAIL"}],
            "raw": {},
        }

        result = ask_mae("Give me a call summary for CFS26-51005.")

        call_detail_mock.assert_called_once_with("CFS26-51005")
        post_mock.assert_not_called()
        self.assertIn("CFS26-51005", result["answer"])
        self.assertIn("MED10", result["answer"])
        self.assertNotIn("TEST REPORTER", result["answer"])
        self.assertNotIn("PRIVATE DETAIL", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    def test_detailed_call_report_includes_returned_command_log(
        self,
        call_detail_mock,
        post_mock,
    ):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-51005",
            "incident_description": "Chest Pain",
            "status": "Closed",
            "assigned_units": [],
            "reporter": {"name": "TEST REPORTER"},
            "command_logs": [{"text": "DOCUMENTED CAD EVENT"}],
            "raw": {},
        }

        result = ask_mae("Give me a detailed call report for CFS26-51005.")

        call_detail_mock.assert_called_once_with("CFS26-51005")
        post_mock.assert_not_called()
        self.assertIn("TEST REPORTER", result["answer"])
        self.assertIn("DOCUMENTED CAD EVENT", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    def test_call_narrative_is_grounded_in_returned_cad_chronology(
        self,
        call_detail_mock,
        post_mock,
    ):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-51005",
            "incident_description": "Chest Pain",
            "location": "100 TEST STREET, LOGAN",
            "call_datetime": "2026-08-01T16:00:00+00:00",
            "assigned_units": [{"unit_number": "MED10"}],
            "command_logs": [
                {
                    "timestamp": "2026-08-01T16:02:00+00:00",
                    "text": "MED10 DISPATCHED",
                },
                {
                    "timestamp": "2026-08-01T16:08:00+00:00",
                    "text": "MED10 ON SCENE",
                },
            ],
            "raw": {},
        }

        result = ask_mae("Give me a call narrative for CFS26-51005.")

        call_detail_mock.assert_called_once_with("CFS26-51005")
        post_mock.assert_not_called()
        self.assertIn("based strictly", result["answer"])
        self.assertIn("MED10 DISPATCHED", result["answer"])
        self.assertIn("MED10 ON SCENE", result["answer"])
        self.assertIn("does not infer", result["answer"])

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

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_active_call_list_places_each_call_on_separate_line(
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
                    "incident_description": "Structure Fire",
                    "status": "On Scene",
                },
                {
                    "cfs_number": "CFS26-50002",
                    "incident_description": "Medical Call",
                    "status": "Enroute",
                },
            ],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae("How many active calls are there? List them please.")

        post_mock.assert_not_called()
        self.assertIn(
            "\n\n- CFS26-50001: Structure Fire (On Scene)\n"
            "- CFS26-50002: Medical Call (Enroute)",
            result["answer"],
        )
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

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_patient_name_question_resolves_incident_and_scans_command_log(
        self,
        live_mock,
        call_detail_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "calls": [
                {
                    "cfs_number": "CFS26-24430",
                    "incident_code": "N/V",
                    "incident_description": "Nausea / Vomiting",
                },
                {
                    "cfs_number": "CFS26-24438",
                    "incident_code": "FIRE",
                    "incident_description": "Fire Other",
                },
            ]
        }
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-24430",
            "incident_description": "Nausea / Vomiting",
            "location": "244 UNIVERSITY AVENUE, LOGAN",
            "assigned_units": [{"unit_number": "MED60"}],
            "command_logs": [
                {"text": "PT HAS TROUBLE WALKING"},
                {"text": "PT KENNETH EVANS"},
            ],
            "raw": {},
        }

        result = ask_mae("The nausa call, what is the pt name?")

        call_detail_mock.assert_called_once_with("CFS26-24430")
        post_mock.assert_not_called()
        self.assertIn("Kenneth Evans", result["answer"])
        self.assertIn('"PT KENNETH EVANS"', result["answer"])
        self.assertEqual(
            result["model"],
            "LCDash verified read tools",
        )
        self.assertIn("CFS26-24430", result["entities"]["cfs_numbers"])
        self.assertIn("MED60", result["entities"]["unit_numbers"])
        self.assertIn(
            "244 UNIVERSITY AVENUE, LOGAN",
            result["entities"]["addresses"],
        )
        evidence_text = str(result["evidence"])
        self.assertIn("PT KENNETH EVANS", evidence_text)

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_recent_cad_activity")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_ambiguous_call_reference_requires_supervisor_selection(
        self,
        live_mock,
        recent_mock,
        post_mock,
    ):
        live_mock.return_value = {"calls": []}
        recent_mock.return_value = {
            "recent_calls": [
                {
                    "cfs_number": "CFS26-24430",
                    "incident_description": "Nausea / Vomiting",
                    "location": "244 UNIVERSITY AVENUE, LOGAN",
                },
                {
                    "cfs_number": "CFS26-24428",
                    "incident_description": "Nausea / Vomiting",
                    "location": "100 MAIN STREET, LOGAN",
                },
            ]
        }

        result = ask_mae("What is the patient name on the nausea call?")

        post_mock.assert_not_called()
        self.assertTrue(result["clarification_required"])
        self.assertEqual(len(result["choices"]), 2)
        self.assertIn("CFS26-24430", result["answer"])
        self.assertIn("CFS26-24428", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_command_log_followup_uses_cfs_from_conversation(
        self,
        live_mock,
        call_detail_mock,
        post_mock,
    ):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-24430",
            "incident_description": "Nausea / Vomiting",
            "assigned_units": [],
            "command_logs": [{"text": "PT KENNETH EVANS"}],
            "raw": {},
        }
        history = [
            {
                "role": "assistant",
                "content": "The Nausea call is CFS26-24430.",
            }
        ]

        result = ask_mae(
            "Use the command log information and tell me the pt name.",
            history,
        )

        live_mock.assert_not_called()
        call_detail_mock.assert_called_once_with("CFS26-24430")
        post_mock.assert_not_called()
        self.assertIn("Kenneth Evans", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_plain_language_busy_now_question_is_verified(
        self,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {
                "active_calls": 3,
                "assigned_units": 5,
                "high_priority_calls": 1,
            },
            "calls": [
                {"location": "100 MAIN ST, LOGAN"},
                {"location": "200 MAIN ST, LOGAN"},
                {"location": "300 MAIN ST, CHAPMANVILLE"},
            ],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae(
            "Are we busy right now and where is most of the activity?"
        )

        post_mock.assert_not_called()
        self.assertIn("3 active calls", result["answer"])
        self.assertIn("5 assigned units", result["answer"])
        self.assertIn("LOGAN", result["answer"])
        self.assertIn("2 active calls", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_current_operations_summary_skips_local_model(
        self,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {
                "active_calls": 2,
                "assigned_units": 3,
                "high_priority_calls": 1,
            },
            "calls": [
                {
                    "cfs_number": "CFS26-50001",
                    "incident_description": "Structure Fire",
                    "priority": "10",
                    "status": "On Scene",
                    "call_datetime": "2026-07-26T17:30:00+00:00",
                    "assigned_units": [
                        {"unit_number": "FC100"},
                        {"unit_number": "FC200"},
                    ],
                },
                {
                    "cfs_number": "CFS26-50002",
                    "incident_description": "Medical Call",
                    "priority": "15",
                    "status": "Enroute",
                    "call_datetime": "2026-07-26T18:00:00+00:00",
                    "assigned_units": [{"unit_number": "MED20"}],
                },
            ],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae(
            "Give me a concise operational summary of the current calls."
        )

        post_mock.assert_not_called()
        self.assertEqual(result["model"], "LCDash verified read tools")
        self.assertIn("2 active calls", result["answer"])
        self.assertIn("CFS26-50001", result["answer"])
        self.assertIn("FC100", result["answer"])
        self.assertIn("CFS26-50002", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_longest_tied_up_unit_uses_active_assignments_only(
        self,
        live_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 2},
            "calls": [],
            "unit_stats": {},
            "unit_rows": [
                {
                    "unit_number": "MED10",
                    "status_group": "On Scene",
                    "dispatch_time": "2026-07-26T17:30:00+00:00",
                    "cfs_number": "CFS26-50001",
                    "incident_description": "Structure Fire",
                    "location": "100 MAIN ST, LOGAN",
                },
                {
                    "unit_number": "MED20",
                    "status_group": "Enroute",
                    "dispatch_time": "2026-07-26T18:00:00+00:00",
                    "cfs_number": "CFS26-50002",
                    "incident_description": "Medical Call",
                },
            ],
        }

        result = ask_mae(
            "Which unit has been tied up the longest and what call are they working?"
        )

        post_mock.assert_not_called()
        self.assertIn("MED10", result["answer"])
        self.assertIn("CFS26-50001", result["answer"])
        self.assertIn("Structure Fire", result["answer"])
        self.assertIn("ignores stale roster timestamps", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_today_yesterday_activity")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_today_yesterday_question_uses_matching_elapsed_windows(
        self,
        analytics_mock,
        comparison_mock,
        post_mock,
    ):
        comparison_mock.return_value = {
            "available": True,
            "today_so_far": 42,
            "yesterday_same_time": 35,
            "yesterday_full_day": 61,
            "latest_stored_at": "2026-07-26T18:00:00+00:00",
        }

        result = ask_mae("Have we been busier today than yesterday?")

        analytics_mock.assert_not_called()
        post_mock.assert_not_called()
        self.assertIn("42 completed calls today", result["answer"])
        self.assertIn("35 by the same time yesterday", result["answer"])
        self.assertIn("busier by 7 calls", result["answer"])
        self.assertIn("61 completed calls", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_recent_cad_activity")
    @patch("app.services.mae_service.get_discipline_database_activity")
    @patch("app.services.mae_service.get_recent_database_activity")
    def test_discipline_question_returns_fire_ems_and_law_counts(
        self,
        database_mock,
        discipline_mock,
        cad_mock,
        post_mock,
    ):
        database_mock.return_value = {
            "available": True,
            "hours": 24,
            "completed_calls_stored": 100,
        }
        discipline_mock.return_value = {
            "available": True,
            "hours": 24,
            "completed_calls": 100,
            "fire_calls": 12,
            "ems_calls": 55,
            "law_calls": 40,
            "classified_calls": 95,
        }
        cad_mock.return_value = {
            "available": True,
            "hours": 24,
            "calls_returned": 105,
            "generated_at": "2026-07-26T18:05:00+00:00",
        }

        result = ask_mae(
            "How many calls have Fire, EMS, and Law handled in the last 24 hours?"
        )

        post_mock.assert_not_called()
        self.assertIn("Fire handled 12", result["answer"])
        self.assertIn("EMS handled 55", result["answer"])
        self.assertIn("Law handled 40", result["answer"])
        self.assertIn("5 without a classified discipline", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.search_knowledge")
    def test_api_access_question_returns_precise_grounded_steps(
        self,
        search_mock,
        post_mock,
    ):
        search_mock.return_value = [
            {
                "title": "Public Safety Suite Pro API User Guide",
                "page_number": 5,
                "coverage": 1.0,
                "query_terms": ["api", "access"],
                "matched_terms": ["api", "access"],
                "indexed_at": "2026-07-26T18:00:00+00:00",
                "text": "Manage API access in the Personnel module.",
            }
        ]

        result = ask_mae(
            "I forgot how to give somebody API access in CentralSquare. "
            "What do I do?"
        )

        post_mock.assert_not_called()
        self.assertIn("Personnel module", result["answer"])
        self.assertIn("Sign In Credentials", result["answer"])
        self.assertIn("Public Safety Suite Professional API", result["answer"])
        self.assertIn("API System User", result["answer"])
        self.assertIn("Page 5", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_repeated_active_call_question_does_not_inherit_prior_cfs(
        self,
        live_mock,
        call_detail_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "last_updated": "2026-07-26T18:05:00+00:00",
            "dashboard_stats": {"active_calls": 2},
            "calls": [
                {
                    "cfs_number": "CFS26-50001",
                    "incident_description": "Structure Fire",
                    "status": "On Scene",
                },
                {
                    "cfs_number": "CFS26-50002",
                    "incident_description": "Medical Call",
                    "status": "Enroute",
                },
            ],
            "unit_stats": {},
            "unit_rows": [],
        }

        result = ask_mae(
            "How many active calls are there? List them please.",
            history=[
                {
                    "role": "assistant",
                    "content": "Earlier we reviewed CFS26-49999.",
                }
            ],
            conversation_entities={"cfs_numbers": ["CFS26-49999"]},
        )

        call_detail_mock.assert_not_called()
        post_mock.assert_not_called()
        self.assertIn("2 active calls", result["answer"])
        self.assertIn("CFS26-50001", result["answer"])

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_dispatcher_cfs_suffix_resolves_to_live_call_summary(
        self,
        live_mock,
        call_detail_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "calls": [{"cfs_number": "CFS26-24436"}],
        }
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-24436",
            "incident_description": "Structure Fire",
            "status": "On Scene",
            "priority": "10",
            "location": "100 TEST STREET, LOGAN",
            "call_datetime": "2026-07-26T12:00:00+00:00",
            "assigned_units": [
                {"unit_number": "FC100", "status": "On Scene"}
            ],
            "command_logs": [{"text": "COMMAND ESTABLISHED"}],
            "raw": {},
        }

        result = ask_mae("Give me a complete summary of 24436.")

        call_detail_mock.assert_called_once_with("CFS26-24436")
        post_mock.assert_not_called()
        self.assertIn("CFS26-24436", result["answer"])
        self.assertIn("- Status: On Scene", result["answer"])
        self.assertIn("  - FC100: On Scene", result["answer"])
        self.assertIn(
            "- Received: 07/26/2026 08:00:00 AM EDT",
            result["answer"],
        )
        self.assertNotIn("2026-07-26T12:00:00", result["answer"])
        self.assertEqual(result["assurance"]["confidence"], "high")
        self.assertNotIn("stale warning", result["assurance"]["freshness"])

    def test_explanatory_change_sentence_is_not_a_write_request(self):
        self.assertFalse(
            mae_service._is_write_request(
                "The CFS26 prefix will not change until next year."
            )
        )
        self.assertTrue(
            mae_service._is_write_request(
                "Please change the incident address."
            )
        )

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_call_detail")
    @patch("app.services.mae_service.get_live_operations_snapshot")
    def test_dispatcher_suffix_confirmation_is_verified_not_write_refusal(
        self,
        live_mock,
        call_detail_mock,
        post_mock,
    ):
        live_mock.return_value = {
            "calls": [{"cfs_number": "CFS26-24436"}],
        }
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-24436",
            "incident_description": "Stolen Property",
            "status": "Assigned",
            "assigned_units": [],
            "command_logs": [],
            "raw": {},
        }

        result = ask_mae(
            "Do you see the call ending in 24436? That is how a dispatcher "
            "would put it in, as CFS26 will not change until next year."
        )

        call_detail_mock.assert_called_once_with("CFS26-24436")
        post_mock.assert_not_called()
        self.assertIn("Yes. I found CFS26-24436", result["answer"])
        self.assertNotIn("inquiry-only", result["answer"].lower())
        self.assertEqual(result["assurance"]["confidence"], "high")


class MAEToolCallingRoutingTests(unittest.TestCase):
    """The flag-gated tool-calling loop runs strictly between the _verified_*
    fast-paths and the plain LLM fallback, and only for operational questions.
    With the flag off, behavior must be identical to before."""

    _LIVE_SOURCE = {"name": "CentralSquare live operations", "kind": "live",
                    "detail": "snap", "available": True, "timestamp": ""}
    _DOC_SOURCE = {"name": "CentralSquare documentation", "kind": "document",
                   "detail": "doc", "available": True, "timestamp": ""}
    _TOOL_RESULT = {
        "answer": "The oldest active call is CFS26-1 with MED31 on scene.",
        "sources": [_LIVE_SOURCE],
        "model": "qwen3.6:27b",
        "generated_at": "2026-08-08T12:00:00-04:00",
        "write_access": False,
        "research": {"live_verified": True},
    }

    def _fallback_post(self):
        resp = unittest.mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": "PLAIN FALLBACK ANSWER."}}
        return resp

    @patch("app.services.mae_service.run_mae_tool_loop")
    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service._build_read_context")
    def test_flag_off_never_calls_tool_loop(self, ctx_mock, post_mock, loop_mock):
        ctx_mock.return_value = ([], [self._LIVE_SOURCE])
        post_mock.return_value = self._fallback_post()
        with patch.object(mae_service.settings, "mae_tool_calling_enabled", False):
            result = ask_mae("which incident has been open longest")
        loop_mock.assert_not_called()
        self.assertEqual(result["answer"], "PLAIN FALLBACK ANSWER.")

    @patch("app.services.mae_service.run_mae_tool_loop")
    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service._build_read_context")
    def test_flag_on_operational_uses_tool_loop_before_fallback(self, ctx_mock, post_mock, loop_mock):
        ctx_mock.return_value = ([], [self._LIVE_SOURCE])
        loop_mock.return_value = dict(self._TOOL_RESULT)
        post_mock.return_value = self._fallback_post()
        with patch.object(mae_service.settings, "mae_tool_calling_enabled", True):
            result = ask_mae("which incident has been open longest")
        loop_mock.assert_called_once()
        self.assertEqual(result["answer"], self._TOOL_RESULT["answer"])
        post_mock.assert_not_called()  # tool answer won; plain fallback skipped

    @patch("app.services.mae_service.run_mae_tool_loop")
    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service._build_read_context")
    def test_flag_on_knowledge_question_skips_tool_loop(self, ctx_mock, post_mock, loop_mock):
        ctx_mock.return_value = ([], [self._DOC_SOURCE])  # no live/historical source
        post_mock.return_value = self._fallback_post()
        with patch.object(mae_service.settings, "mae_tool_calling_enabled", True):
            result = ask_mae("how do I configure a radio channel plan")
        loop_mock.assert_not_called()
        self.assertEqual(result["answer"], "PLAIN FALLBACK ANSWER.")

    @patch("app.services.mae_service.run_mae_tool_loop")
    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service._build_read_context")
    def test_flag_on_but_loop_returns_none_falls_through(self, ctx_mock, post_mock, loop_mock):
        ctx_mock.return_value = ([], [self._LIVE_SOURCE])
        loop_mock.return_value = None  # model called no tool / errored
        post_mock.return_value = self._fallback_post()
        with patch.object(mae_service.settings, "mae_tool_calling_enabled", True):
            result = ask_mae("which incident has been open longest")
        loop_mock.assert_called_once()
        self.assertEqual(result["answer"], "PLAIN FALLBACK ANSWER.")


if __name__ == "__main__":
    unittest.main()
