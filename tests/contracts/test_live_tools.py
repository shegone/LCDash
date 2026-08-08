"""Network-free contracts for the read-only MAE tool registry.

These tools are the boundary that keeps tool-calling read-only: this file
asserts no write tool exists and that bad input never raises into the caller.
Reporter/caller info and full command logs are deliberately exposed (Ted:
"do not limit any pulled information"); only ``raw`` is stripped for budget.
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
        "reporter": {"name": "Jane Caller", "phone": "3045551212"},
    }
    call.update(overrides)
    return call


def _unit(**overrides):
    unit = {
        "unit_number": "MED31",
        "agency": "LEASA",
        "unit_type": "Medic",
        "status": "Available",
        "station": "Station 3",
        "assignment_cfs_number": "",
    }
    unit.update(overrides)
    return unit


class _CadState:
    def __init__(self, calls, units=()):
        self.calls = tuple(calls)
        self.units = tuple(units)


def _registry(calls=(), units=(), freshness="fresh", analytics_overview_fn=None):
    return LiveToolRegistry(
        cad_state=_CadState(calls, units),
        cad_status={"freshness": freshness, "age_seconds": 5},
        analytics_overview_fn=analytics_overview_fn,
    )


class ToolSpecsTests(unittest.TestCase):
    def test_expected_read_only_tools_and_no_write_tool(self):
        names = {spec["toolSpec"]["name"] for spec in TOOL_SPECS}
        self.assertEqual(
            names,
            {
                "list_active_calls",
                "get_call_detail",
                "get_analytics_summary",
                "search_calls",
                "search_units",
            },
        )
        for spec in TOOL_SPECS:
            blob = str(spec).lower()
            for forbidden in (
                "dispatch",
                "acknowledge",
                "page",
                "write",
                "update_call",
                "run_command",
                "subscribe",
                "trigger_tone",
                "send_alert",
                "send_message",
            ):
                self.assertNotIn(forbidden, blob)


class ListActiveCallsTests(unittest.TestCase):
    def test_returns_only_allowlisted_fields(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("list_active_calls", {})
        self.assertEqual(result.tool_name, "list_active_calls")
        self.assertTrue(result.payload["available"])
        call = result.payload["calls"][0]
        self.assertNotIn("raw", call)
        # command_logs omitted from the list view for token budget only.
        self.assertNotIn("command_logs", call)
        # Reporter/caller info and coordinates are deliberately included.
        self.assertEqual(call["reporter"], {"name": "Jane Caller", "phone": "3045551212"})
        self.assertEqual(call["latitude"], 37.85)
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
    def test_found_call_includes_coordinates_command_log_and_reporter(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("get_call_detail", {"cfs_number": "cfs26-25863"})
        self.assertTrue(result.payload["found"])
        self.assertEqual(result.payload["latitude"], 37.85)
        self.assertEqual(len(result.payload["command_logs"]), 1)
        # De-limited: reporter/caller info is now exposed via get_call_detail.
        self.assertEqual(
            result.payload["reporter"], {"name": "Jane Caller", "phone": "3045551212"}
        )
        # raw is still stripped for context budget.
        self.assertNotIn("raw", result.payload)

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

    def test_command_log_bounded_to_last_forty(self):
        logs = tuple(
            {"timestamp": f"t{i}", "unit_number": "MED31", "status": "s", "text": "x"}
            for i in range(50)
        )
        registry = _registry(calls=[_call(command_logs=logs)])
        result = registry.execute("get_call_detail", {"cfs_number": "CFS26-25863"})
        self.assertEqual(len(result.payload["command_logs"]), 40)
        self.assertEqual(result.payload["command_logs"][-1]["timestamp"], "t49")


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


class SearchCallsTests(unittest.TestCase):
    def test_no_filters_returns_everything(self):
        registry = _registry(calls=[_call(), _call(cfs_number="CFS26-00001", agency="LCSO")])
        result = registry.execute("search_calls", {})
        self.assertTrue(result.payload["available"])
        self.assertEqual(result.payload["count"], 2)
        self.assertEqual(len(result.payload["calls"]), 2)
        self.assertFalse(result.payload["truncated"])

    def test_filters_by_agency_exact_case_insensitive(self):
        registry = _registry(calls=[_call(agency="LEASA"), _call(cfs_number="CFS26-00001", agency="LCSO")])
        result = registry.execute("search_calls", {"agency": "leasa"})
        self.assertEqual(result.payload["count"], 1)
        self.assertEqual(result.payload["calls"][0]["agency"], "LEASA")

    def test_filters_by_incident_code_substring(self):
        registry = _registry(
            calls=[
                _call(incident_code="MEDICAL", incident_description="Medical Call"),
                _call(cfs_number="CFS26-00001", incident_code="TRAFFIC", incident_description="MVA"),
            ]
        )
        result = registry.execute("search_calls", {"incident_code": "med"})
        self.assertEqual(result.payload["count"], 1)

    def test_filters_by_location_substring_matches_label_or_city(self):
        registry = _registry(
            calls=[
                _call(location_label="314 HUDGINS STREET", city="LOGAN"),
                _call(cfs_number="CFS26-00001", location_label="1 MAIN ST", city="CHAPMANVILLE"),
            ]
        )
        result = registry.execute("search_calls", {"location": "chapman"})
        self.assertEqual(result.payload["count"], 1)
        self.assertEqual(result.payload["calls"][0]["city"], "CHAPMANVILLE")

    def test_filters_by_unit_number_substring(self):
        registry = _registry(
            calls=[
                _call(assigned_units=({"unit_number": "MED31", "status": "Assigned"},)),
                _call(cfs_number="CFS26-00001", assigned_units=({"unit_number": "ENG12", "status": "Assigned"},)),
            ]
        )
        result = registry.execute("search_calls", {"unit_number": "med"})
        self.assertEqual(result.payload["count"], 1)

    def test_priority_range_filters_high_priority_only(self):
        registry = _registry(
            calls=[
                _call(priority=1),
                _call(cfs_number="CFS26-00001", priority=20),
                _call(cfs_number="CFS26-00002", priority="not-a-number"),
            ]
        )
        result = registry.execute("search_calls", {"priority_max": 5})
        self.assertEqual(result.payload["count"], 1)
        self.assertEqual(result.payload["calls"][0]["cfs_number"], "CFS26-25863")

    def test_priority_min_greater_than_max_is_rejected(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("search_calls", {"priority_min": 10, "priority_max": 1})
        self.assertIn("error", result.payload)

    def test_combined_filters_are_anded(self):
        registry = _registry(
            calls=[
                _call(agency="LEASA", status="On Scene"),
                _call(cfs_number="CFS26-00001", agency="LEASA", status="Dispatched"),
                _call(cfs_number="CFS26-00002", agency="LCSO", status="On Scene"),
            ]
        )
        result = registry.execute("search_calls", {"agency": "LEASA", "status": "On Scene"})
        self.assertEqual(result.payload["count"], 1)
        self.assertEqual(result.payload["calls"][0]["cfs_number"], "CFS26-25863")

    def test_unknown_filter_key_is_rejected(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("search_calls", {"dispatch_now": True})
        self.assertIn("error", result.payload)

    def test_wrong_typed_filter_is_rejected(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("search_calls", {"agency": 123})
        self.assertIn("error", result.payload)
        result = registry.execute("search_calls", {"priority_min": "high"})
        self.assertIn("error", result.payload)

    def test_limit_bounds_are_enforced(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("search_calls", {"limit": 0})
        self.assertIn("error", result.payload)
        result = registry.execute("search_calls", {"limit": 51})
        self.assertIn("error", result.payload)

    def test_results_bounded_by_limit_with_truncation_flag(self):
        registry = _registry(calls=[_call(cfs_number=f"CFS26-{i:05d}") for i in range(75)])
        result = registry.execute("search_calls", {})
        self.assertEqual(result.payload["count"], 75)
        self.assertEqual(len(result.payload["calls"]), 50)
        self.assertTrue(result.payload["truncated"])

    def test_unavailable_snapshot_returns_empty_without_error(self):
        registry = _registry(calls=[_call()], freshness="stale")
        result = registry.execute("search_calls", {"agency": "LEASA"})
        self.assertFalse(result.payload["available"])
        self.assertEqual(result.payload["calls"], [])


class SearchUnitsTests(unittest.TestCase):
    def test_no_filters_returns_whole_roster(self):
        registry = _registry(units=[_unit(), _unit(unit_number="ENG12", agency="LCSO")])
        result = registry.execute("search_units", {})
        self.assertTrue(result.payload["available"])
        self.assertEqual(result.payload["count"], 2)

    def test_filters_by_agency_exact_and_status(self):
        registry = _registry(
            units=[
                _unit(unit_number="MED31", agency="LEASA", status="Available"),
                _unit(unit_number="MED32", agency="LEASA", status="Out of Service"),
                _unit(unit_number="ENG12", agency="LCSO", status="Available"),
            ]
        )
        result = registry.execute("search_units", {"agency": "leasa", "status": "available"})
        self.assertEqual(result.payload["count"], 1)
        self.assertEqual(result.payload["units"][0]["unit_number"], "MED31")

    def test_filters_by_unit_type_and_station_substring(self):
        registry = _registry(
            units=[
                _unit(unit_type="Medic Unit", station="Station 3"),
                _unit(unit_number="ENG12", unit_type="Engine", station="Station 7"),
            ]
        )
        result = registry.execute("search_units", {"unit_type": "medic", "station": "3"})
        self.assertEqual(result.payload["count"], 1)

    def test_unknown_filter_key_is_rejected(self):
        registry = _registry(units=[_unit()])
        result = registry.execute("search_units", {"dispatch": True})
        self.assertIn("error", result.payload)

    def test_wrong_typed_filter_is_rejected(self):
        registry = _registry(units=[_unit()])
        result = registry.execute("search_units", {"station": 12})
        self.assertIn("error", result.payload)

    def test_limit_bounds_are_enforced(self):
        registry = _registry(units=[_unit()])
        result = registry.execute("search_units", {"limit": 0})
        self.assertIn("error", result.payload)
        result = registry.execute("search_units", {"limit": 101})
        self.assertIn("error", result.payload)

    def test_results_bounded_by_limit_with_truncation_flag(self):
        registry = _registry(units=[_unit(unit_number=f"U{i:04d}") for i in range(150)])
        result = registry.execute("search_units", {})
        self.assertEqual(result.payload["count"], 150)
        self.assertEqual(len(result.payload["units"]), 100)
        self.assertTrue(result.payload["truncated"])

    def test_unavailable_snapshot_returns_empty_without_error(self):
        registry = _registry(units=[_unit()], freshness="stale")
        result = registry.execute("search_units", {})
        self.assertFalse(result.payload["available"])
        self.assertEqual(result.payload["units"], [])


class UnknownToolTests(unittest.TestCase):
    def test_unknown_tool_name_is_an_error_payload_not_an_exception(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("dispatch_unit", {"unit": "MED31"})
        self.assertIn("error", result.payload)
        self.assertFalse(result.source.available)


if __name__ == "__main__":
    unittest.main()
