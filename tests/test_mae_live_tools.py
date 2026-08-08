"""Contracts for the read-only MAE tool registry (app/services/mae_live_tools.py).

Network-free: the underlying read functions are patched. These assert the
registry's guarantees -- the giant ``raw`` blob is stripped, sizes are bounded,
bad/unknown calls become error payloads (never exceptions), and CAD errors are
caught -- since this registry is the structural read-only boundary for the
tool-calling loop.
"""

import unittest
from unittest.mock import patch

import app.services.mae_live_tools as live_tools
from app.services.centralsquare import CentralSquareAPIError
from app.services.mae_live_tools import MaeLiveToolRegistry, tool_specs


def _call(**overrides):
    call = {
        "cfs_number": "CFS26-25863",
        "incident_code": "MEDICAL",
        "incident_description": "Medical Call",
        "location": "314 HUDGINS STREET",
        "priority": "20",
        "agency": "LEASA",
        "status": "On Scene",
        "call_datetime": "2026-08-07T17:05:32Z",
        "is_scheduled": False,
        "assigned_units": [{"unit_number": "MED31", "status": "Assigned"}],
        "command_logs": [
            {"timestamp": "t1", "unit_number": "MED31", "status": "Enroute", "text": "responding"}
        ],
        "reporter": {"name": "Jane Caller"},
        "raw": {"HUGE": "x" * 5000},
    }
    call.update(overrides)
    return call


class ToolSpecTests(unittest.TestCase):
    def test_exactly_four_read_tools_and_no_write_tool(self):
        names = {s["function"]["name"] for s in tool_specs()}
        self.assertEqual(
            names,
            {"list_active_calls", "get_call_detail", "get_unit_status", "get_analytics_summary"},
        )
        blob = str(tool_specs()).lower()
        for forbidden in ("dispatch", "acknowledge", "close", "page", "run_command", "update"):
            self.assertNotIn(forbidden, blob)


class ListActiveCallsTests(unittest.TestCase):
    @patch("app.services.mae_live_tools.get_live_operations_snapshot")
    def test_strips_raw_and_omits_command_logs_from_list(self, snap_mock):
        snap_mock.return_value = {
            "last_updated": "2026-08-07T17:00:00Z",
            "dashboard_stats": {"active_calls": 1},
            "calls": [_call()],
        }
        result = MaeLiveToolRegistry().execute("list_active_calls", {})
        call = result.payload["calls"][0]
        self.assertNotIn("raw", call)
        self.assertNotIn("command_logs", call)  # available via get_call_detail, omitted here for budget
        self.assertEqual(call["cfs_number"], "CFS26-25863")
        self.assertEqual(call["assigned_units"], [{"unit_number": "MED31", "status": "Assigned"}])
        self.assertEqual(result.payload["count"], 1)

    @patch("app.services.mae_live_tools.get_live_operations_snapshot")
    def test_bounded_to_fifty_calls(self, snap_mock):
        snap_mock.return_value = {
            "calls": [_call(cfs_number=f"CFS26-{i:05d}") for i in range(80)]
        }
        result = MaeLiveToolRegistry().execute("list_active_calls", {})
        self.assertEqual(len(result.payload["calls"]), 50)


class GetCallDetailTests(unittest.TestCase):
    @patch("app.services.mae_live_tools.get_call_detail")
    def test_keeps_sensitive_detail_but_strips_raw(self, detail_mock):
        detail_mock.return_value = _call()
        result = MaeLiveToolRegistry().execute("get_call_detail", {"cfs_number": "cfs26-25863"})
        self.assertTrue(result.payload["found"])
        self.assertNotIn("raw", result.payload)
        # Ted's data policy: narrative + reporter are intentionally retained.
        self.assertIn("command_logs", result.payload)
        self.assertIn("reporter", result.payload)

    @patch("app.services.mae_live_tools.get_call_detail")
    def test_command_logs_bounded_to_last_forty(self, detail_mock):
        logs = [{"timestamp": f"t{i}", "text": "x"} for i in range(60)]
        detail_mock.return_value = _call(command_logs=logs)
        result = MaeLiveToolRegistry().execute("get_call_detail", {"cfs_number": "CFS26-25863"})
        self.assertEqual(len(result.payload["command_logs"]), 40)
        self.assertTrue(result.payload["command_logs_truncated"])
        self.assertEqual(result.payload["command_logs"][-1]["timestamp"], "t59")

    def test_invalid_cfs_is_error_payload_not_exception(self):
        result = MaeLiveToolRegistry().execute("get_call_detail", {"cfs_number": "nope"})
        self.assertIn("error", result.payload)

    def test_missing_cfs_is_error_payload(self):
        result = MaeLiveToolRegistry().execute("get_call_detail", {})
        self.assertIn("error", result.payload)

    @patch("app.services.mae_live_tools.get_call_detail")
    def test_cad_error_is_caught_as_error_payload(self, detail_mock):
        detail_mock.side_effect = CentralSquareAPIError("not found")
        result = MaeLiveToolRegistry().execute("get_call_detail", {"cfs_number": "CFS26-99999"})
        self.assertEqual(result.payload, {"error": "cad read failed"})
        self.assertFalse(result.source["available"])


class GetUnitStatusTests(unittest.TestCase):
    @patch("app.services.mae_live_tools.get_live_unit_snapshot")
    def test_strips_raw_from_unit_groups(self, snap_mock):
        snap_mock.return_value = {
            "last_updated": "2026-08-07T17:00:00Z",
            "roster_connected": True,
            "active_stats": {"count": 1},
            "active_units": [{"unit_number": "MED31", "status": "On Scene", "raw": {"x": 1}}],
            "available_units": [],
            "unavailable_units": [],
            "unknown_units": [],
        }
        result = MaeLiveToolRegistry().execute("get_unit_status", {})
        self.assertNotIn("raw", result.payload["active_units"][0])
        self.assertEqual(result.payload["active_units"][0]["unit_number"], "MED31")


class GetAnalyticsSummaryTests(unittest.TestCase):
    @patch("app.services.mae_live_tools.get_analytics_overview")
    def test_valid_period_passthrough(self, overview_mock):
        overview_mock.return_value = {
            "available": True,
            "period_label": "Last 7 days",
            "metrics": {"total_calls": 42},
            "busiest_units": [{"unit_number": "MED31", "responses": 10}],
            "busiest_stations": [],
            "latest_data_at": "2026-08-07T12:00:00Z",
        }
        result = MaeLiveToolRegistry().execute("get_analytics_summary", {"period": "7d"})
        overview_mock.assert_called_once_with(period="7d")
        self.assertTrue(result.payload["available"])
        self.assertEqual(result.payload["metrics"]["total_calls"], 42)
        self.assertEqual(result.source["kind"], "historical")

    def test_invalid_period_is_error_payload(self):
        result = MaeLiveToolRegistry().execute("get_analytics_summary", {"period": "12h"})
        self.assertIn("error", result.payload)

    @patch("app.services.mae_live_tools.get_analytics_overview")
    def test_unavailable_reports_cleanly(self, overview_mock):
        overview_mock.return_value = {"available": False}
        result = MaeLiveToolRegistry().execute("get_analytics_summary", {"period": "30d"})
        self.assertFalse(result.payload["available"])


class RegistryGuardTests(unittest.TestCase):
    def test_unknown_tool_is_error_payload(self):
        result = MaeLiveToolRegistry().execute("dispatch_unit", {"unit": "MED31"})
        self.assertEqual(result.payload, {"error": "unknown tool"})
        self.assertFalse(result.source["available"])

    @patch("app.services.mae_live_tools.get_live_operations_snapshot")
    def test_unexpected_exception_never_propagates(self, snap_mock):
        snap_mock.side_effect = RuntimeError("boom")
        result = MaeLiveToolRegistry().execute("list_active_calls", {})
        self.assertEqual(result.payload, {"error": "tool failed"})


if __name__ == "__main__":
    unittest.main()
