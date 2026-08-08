"""Network-free contracts for the read-only MAE tool registry.

These tools are the boundary that keeps tool-calling read-only: this file
asserts the allowlist holds (no raw/reporter fields leak, no write tool
exists) and that bad input never raises into the caller.
"""

import unittest

from app.integrations.cloud_ai.live_tools import LiveToolRegistry, TOOL_SPECS


def _call(**overrides):
    call = {
        "cfs_number": "CFS26-25863",
        "incident_code": "MEDICAL",
        "incident_description": "Medical Call",
        "location_label": "314 HUDGINS STREET",
        "city": "LOGAN",
        "priority": 20,
        "agency": "LEASA",
        "status": "On Scene",
        "call_datetime": "2026-08-07T17:05:32.641419Z",
        "latitude": 37.85,
        "longitude": -81.99,
        "assigned_units": ({"unit_number": "MED31", "status": "Assigned"},),
        "command_logs": (
            {
                "timestamp": "2026-08-07T17:06:00Z",
                "unit_number": "MED31",
                "status": "Enroute",
                "text": "Responding",
            },
        ),
        "raw": {"Should": "NeverLeak"},
        "reporter": {"Name": "Should Never Leak"},
    }
    call.update(overrides)
    return call


class _CadState:
    def __init__(self, calls):
        self.calls = tuple(calls)
        self.units = ()


def _registry(calls=(), freshness="fresh", analytics_overview_fn=None):
    return LiveToolRegistry(
        cad_state=_CadState(calls),
        cad_status={"freshness": freshness, "age_seconds": 5},
        analytics_overview_fn=analytics_overview_fn,
    )


class ToolSpecsTests(unittest.TestCase):
    def test_exactly_three_read_only_tools_and_no_write_tool(self):
        names = {spec["toolSpec"]["name"] for spec in TOOL_SPECS}
        self.assertEqual(
            names, {"list_active_calls", "get_call_detail", "get_analytics_summary"}
        )
        for spec in TOOL_SPECS:
            blob = str(spec).lower()
            for forbidden in ("dispatch", "acknowledge", "page", "write", "update_call"):
                self.assertNotIn(forbidden, blob)


class ListActiveCallsTests(unittest.TestCase):
    def test_returns_only_allowlisted_fields(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("list_active_calls", {})
        self.assertEqual(result.tool_name, "list_active_calls")
        self.assertTrue(result.payload["available"])
        call = result.payload["calls"][0]
        self.assertNotIn("raw", call)
        self.assertNotIn("reporter", call)
        self.assertNotIn("command_logs", call)
        self.assertNotIn("latitude", call)
        self.assertEqual(call["cfs_number"], "CFS26-25863")
        self.assertEqual(call["assigned_units"], [{"unit_number": "MED31", "status": "Assigned"}])

    def test_unavailable_snapshot_returns_empty_without_error(self):
        registry = _registry(calls=[_call()], freshness="stale")
        result = registry.execute("list_active_calls", {})
        self.assertFalse(result.payload["available"])
        self.assertEqual(result.payload["calls"], [])

    def test_bounded_at_fifty_calls(self):
        registry = _registry(calls=[_call(cfs_number=f"CFS26-{i:05d}") for i in range(75)])
        result = registry.execute("list_active_calls", {})
        self.assertEqual(len(result.payload["calls"]), 50)


class GetCallDetailTests(unittest.TestCase):
    def test_found_call_includes_coordinates_and_command_log(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("get_call_detail", {"cfs_number": "cfs26-25863"})
        self.assertTrue(result.payload["found"])
        self.assertEqual(result.payload["latitude"], 37.85)
        self.assertEqual(len(result.payload["command_logs"]), 1)
        self.assertNotIn("raw", result.payload)
        self.assertNotIn("reporter", result.payload)

    def test_not_found_reports_cleanly(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("get_call_detail", {"cfs_number": "CFS26-99999"})
        self.assertFalse(result.payload["found"])

    def test_invalid_cfs_format_is_an_error_payload_not_an_exception(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("get_call_detail", {"cfs_number": "not-a-cfs-number"})
        self.assertIn("error", result.payload)

    def test_missing_cfs_number_is_an_error_payload(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("get_call_detail", {})
        self.assertIn("error", result.payload)

    def test_command_log_bounded_to_last_twenty(self):
        logs = tuple(
            {"timestamp": f"t{i}", "unit_number": "MED31", "status": "s", "text": "x"}
            for i in range(30)
        )
        registry = _registry(calls=[_call(command_logs=logs)])
        result = registry.execute("get_call_detail", {"cfs_number": "CFS26-25863"})
        self.assertEqual(len(result.payload["command_logs"]), 20)
        self.assertEqual(result.payload["command_logs"][-1]["timestamp"], "t29")


class GetAnalyticsSummaryTests(unittest.TestCase):
    def test_hours_and_period_are_mutually_exclusive(self):
        registry = _registry(analytics_overview_fn=lambda **_: {"available": False})
        result = registry.execute("get_analytics_summary", {"hours": 8, "period": "24h"})
        self.assertIn("error", result.payload)

    def test_requires_one_of_hours_or_period(self):
        registry = _registry(analytics_overview_fn=lambda **_: {"available": False})
        result = registry.execute("get_analytics_summary", {})
        self.assertIn("error", result.payload)

    def test_hours_out_of_bounds_is_an_error(self):
        registry = _registry(analytics_overview_fn=lambda **_: {"available": False})
        result = registry.execute("get_analytics_summary", {"hours": 0})
        self.assertIn("error", result.payload)
        result = registry.execute("get_analytics_summary", {"hours": 999999})
        self.assertIn("error", result.payload)

    def test_invalid_period_key_is_an_error(self):
        registry = _registry(analytics_overview_fn=lambda **_: {"available": False})
        result = registry.execute("get_analytics_summary", {"period": "12h"})
        self.assertIn("error", result.payload)

    def test_hours_is_forwarded_to_the_overview_function(self):
        seen = {}

        def overview_fn(**kwargs):
            seen.update(kwargs)
            return {
                "available": True,
                "metrics": {"total_calls": 12},
                "busiest_stations": [],
                "busiest_units": [],
                "incident_types": [],
                "latest_data_at": "2026-08-08T00:00:00Z",
            }

        registry = _registry(analytics_overview_fn=overview_fn)
        result = registry.execute("get_analytics_summary", {"hours": 8})
        self.assertEqual(seen, {"hours": 8})
        self.assertEqual(result.payload["metrics"]["total_calls"], 12)
        self.assertEqual(result.payload["window"], "Last 8 hours")

    def test_unavailable_overview_reports_cleanly(self):
        registry = _registry(analytics_overview_fn=lambda **_: {"available": False})
        result = registry.execute("get_analytics_summary", {"period": "24h"})
        self.assertFalse(result.payload["available"])

    def test_busiest_rows_are_bounded(self):
        many_rows = [{"label": f"Station {i}", "count": i} for i in range(20)]

        def overview_fn(**_kwargs):
            return {
                "available": True,
                "metrics": {},
                "busiest_stations": many_rows,
                "busiest_units": many_rows,
                "incident_types": many_rows,
                "latest_data_at": "",
            }

        registry = _registry(analytics_overview_fn=overview_fn)
        result = registry.execute("get_analytics_summary", {"period": "24h"})
        self.assertEqual(len(result.payload["busiest_stations"]), 5)
        self.assertEqual(len(result.payload["busiest_units"]), 5)
        self.assertEqual(len(result.payload["incident_types"]), 10)


class UnknownToolTests(unittest.TestCase):
    def test_unknown_tool_name_is_an_error_payload_not_an_exception(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("dispatch_unit", {"unit": "MED31"})
        self.assertIn("error", result.payload)
        self.assertFalse(result.source.available)


if __name__ == "__main__":
    unittest.main()
