"""Network-free contracts for the read-only MAE tool registry.

These tools are the boundary that keeps tool-calling read-only: this file
asserts no write tool exists and that bad input never raises into the caller.
Reporter/caller info and full command logs are deliberately exposed (Ted:
"do not limit any pulled information"); only ``raw`` is stripped for budget.
"""

import unittest

from app.integrations.cad.cloud_read_config import (
    CloudCadMode,
    CloudCadReadConfig,
    FORBIDDEN_OPERATIONS,
)
from app.integrations.cad.cloud_read_connector import CloudCadConnectorError
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


def _registry(calls=(), units=(), freshness="fresh", analytics_overview_fn=None, cad_connector=None):
    return LiveToolRegistry(
        cad_state=_CadState(calls, units),
        cad_status={"freshness": freshness, "age_seconds": 5},
        analytics_overview_fn=analytics_overview_fn,
        cad_connector=cad_connector,
    )


def _raw_cad_call(cfs_number: str, **overrides) -> dict:
    raw = {
        "CFSNumber": cfs_number,
        "IncidentCode": {"Code": "MEDICAL", "Description": "Medical Call"},
        "PrimaryResponseAgency": {"Abbreviation": "LEASA"},
        "CallDateTime": "2026-08-01T12:00:00Z",
        "Address": {"FullAddress": "314 HUDGINS STREET", "City": "LOGAN"},
    }
    raw.update(overrides)
    return raw


class _FakeCadConnector:
    """Records calls; returns pre-baked pages/results or raises pre-baked errors."""

    def __init__(self, *, search_pages=None, config_result=None, search_error=None, config_error=None):
        self._search_pages = list(search_pages or [])
        self._config_result = config_result
        self._search_error = search_error
        self._config_error = config_error
        self.search_calls_requests: list[dict] = []
        self.get_configurations_requests: list[str] = []

    def search_calls(self, body, *, skip=0, limit=100):
        self.search_calls_requests.append({"body": dict(body), "skip": skip, "limit": limit})
        if self._search_error is not None:
            raise self._search_error
        page = self._search_pages.pop(0) if self._search_pages else []
        return {"cfs_cores": page}

    def get_configurations(self, configuration):
        self.get_configurations_requests.append(configuration)
        if self._config_error is not None:
            raise self._config_error
        return self._config_result


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
                "search_call_history",
                "get_cad_configurations",
            },
        )
        for spec in TOOL_SPECS:
            blob = str(spec).lower()
            for forbidden in (
                "dispatch",
                "acknowledge",
                "write",
                "update_call",
                "run_command",
                "subscribe",
                "trigger_tone",
                "send_alert",
                "send_message",
                "send_page",
            ):
                self.assertNotIn(forbidden, blob)

    def test_no_forbidden_operation_name_appears_in_any_tool_spec(self):
        for spec in TOOL_SPECS:
            blob = str(spec).lower()
            for forbidden in FORBIDDEN_OPERATIONS:
                self.assertNotIn(forbidden.lower(), blob)

    def test_get_configurations_is_allowlisted_and_no_forbidden_op_is(self):
        config = CloudCadReadConfig(mode=CloudCadMode.SYNTHETIC_DISCONNECTED, tenant_id="lcso-wv")
        allowed = config.allowed_operations
        self.assertIn("get_configurations", allowed)
        for forbidden in FORBIDDEN_OPERATIONS:
            self.assertNotIn(forbidden, allowed)


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


class SearchCallHistoryTests(unittest.TestCase):
    def test_missing_created_from_is_an_error(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute("search_call_history", {"created_to": "2026-08-02T00:00:00Z"})
        self.assertIn("error", result.payload)

    def test_missing_created_to_is_an_error(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute("search_call_history", {"created_from": "2026-08-01T00:00:00Z"})
        self.assertIn("error", result.payload)

    def test_invalid_timestamp_is_an_error(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "search_call_history",
            {"created_from": "not-a-date", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertIn("error", result.payload)

    def test_inverted_window_is_rejected(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-02T00:00:00Z", "created_to": "2026-08-01T00:00:00Z"},
        )
        self.assertIn("error", result.payload)

    def test_equal_from_and_to_is_rejected(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-01T00:00:00Z"},
        )
        self.assertIn("error", result.payload)

    def test_window_over_92_days_is_rejected(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-01-01T00:00:00Z", "created_to": "2026-08-01T00:00:00Z"},
        )
        self.assertIn("error", result.payload)

    def test_window_of_exactly_92_days_is_accepted(self):
        connector = _FakeCadConnector(search_pages=[[]])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-05-01T00:00:00Z", "created_to": "2026-08-01T00:00:00Z"},
        )
        self.assertNotIn("error", result.payload)
        self.assertTrue(result.payload["available"])

    def test_accepts_trailing_z_and_offset_timestamps(self):
        connector = _FakeCadConnector(search_pages=[[]])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {
                "created_from": "2026-08-01T00:00:00+00:00",
                "created_to": "2026-08-02T00:00:00Z",
            },
        )
        self.assertTrue(result.payload["available"])

    def test_connector_none_reports_unavailable_without_error(self):
        registry = _registry(cad_connector=None)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertFalse(result.payload["available"])
        self.assertIn("unavailable", result.payload["error"])
        self.assertEqual(result.payload["calls"], [])

    def test_successful_paginated_search_stops_on_short_page(self):
        page_one = [_raw_cad_call(f"CFS26-{i:05d}") for i in range(100)]
        page_two = [_raw_cad_call(f"CFS26-{i:05d}") for i in range(100, 130)]
        connector = _FakeCadConnector(search_pages=[page_one, page_two])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertTrue(result.payload["available"])
        self.assertEqual(result.payload["count"], 130)
        self.assertEqual(result.payload["returned"], 130)
        self.assertFalse(result.payload["truncated"])
        self.assertEqual(len(connector.search_calls_requests), 2)
        # Search body uses the confirmed heatmap_service key names.
        body = connector.search_calls_requests[0]["body"]
        self.assertEqual(body["OrderByField"], "Created")
        self.assertEqual(body["OrderByDirection"], "Descending")
        self.assertIn("RecordCreatedFrom", body)
        self.assertIn("RecordCreatedTo", body)

    def test_dedupes_by_cfs_number_across_pages(self):
        # page_one must be a full 100-row page or the loop treats it as the
        # last (short) page and never fetches page_two.
        fillers = [_raw_cad_call(f"CFS26-FILL{i:03d}") for i in range(98)]
        page_one = fillers + [_raw_cad_call("CFS26-00001"), _raw_cad_call("CFS26-00002")]
        page_two = [_raw_cad_call("CFS26-00002"), _raw_cad_call("CFS26-00003")]
        connector = _FakeCadConnector(search_pages=[page_one, page_two])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        # 98 filler + 00001 + 00002 + 00003 (00002 deduped across pages).
        self.assertEqual(result.payload["count"], 101)
        self.assertEqual(len(connector.search_calls_requests), 2)

    def test_row_cap_truncates_mid_page(self):
        page = [_raw_cad_call(f"CFS26-{i:05d}") for i in range(100)]
        connector = _FakeCadConnector(search_pages=[page])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {
                "created_from": "2026-08-01T00:00:00Z",
                "created_to": "2026-08-02T00:00:00Z",
                "limit": 5,
            },
        )
        self.assertEqual(result.payload["count"], 5)
        self.assertTrue(result.payload["truncated"])
        self.assertEqual(len(connector.search_calls_requests), 1)

    def test_page_cap_truncates_after_five_full_pages(self):
        # Each of the 5 pages is a full 100-row page (so the loop never sees
        # a "short" page and never naturally stops), but every page repeats
        # the same 40 CFS numbers, so the row cap (200) is never hit either
        # -- the only thing that stops the search is MAX_HISTORY_PAGES.
        one_page = ([_raw_cad_call(f"CFS26-{i:05d}") for i in range(40)] * 3)[:100]
        connector = _FakeCadConnector(search_pages=[list(one_page) for _ in range(5)])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertEqual(result.payload["count"], 40)
        self.assertTrue(result.payload["truncated"])
        self.assertEqual(len(connector.search_calls_requests), 5)

    def test_post_filters_apply_after_fetch_and_reduce_returned_not_count(self):
        page = [
            _raw_cad_call("CFS26-00001", **{"PrimaryResponseAgency": {"Abbreviation": "LEASA"}}),
            _raw_cad_call("CFS26-00002", **{"PrimaryResponseAgency": {"Abbreviation": "LCSO"}}),
        ]
        connector = _FakeCadConnector(search_pages=[page])
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {
                "created_from": "2026-08-01T00:00:00Z",
                "created_to": "2026-08-02T00:00:00Z",
                "agency": "LEASA",
            },
        )
        self.assertEqual(result.payload["count"], 2)
        self.assertEqual(result.payload["returned"], 1)
        self.assertFalse(result.payload["truncated"])

    def test_connector_error_is_an_error_payload_not_an_exception(self):
        connector = _FakeCadConnector(search_error=CloudCadConnectorError("upstream_rejected", "search_calls"))
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertFalse(result.payload["available"])
        self.assertIn("error", result.payload)

    def test_unexpected_exception_is_an_error_payload_not_raised(self):
        connector = _FakeCadConnector(search_error=RuntimeError("boom"))
        registry = _registry(cad_connector=connector)
        result = registry.execute(
            "search_call_history",
            {"created_from": "2026-08-01T00:00:00Z", "created_to": "2026-08-02T00:00:00Z"},
        )
        self.assertFalse(result.payload["available"])
        self.assertIn("error", result.payload)

    def test_unknown_filter_key_is_rejected(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "search_call_history",
            {
                "created_from": "2026-08-01T00:00:00Z",
                "created_to": "2026-08-02T00:00:00Z",
                "dispatch_now": True,
            },
        )
        self.assertIn("error", result.payload)


class GetCadConfigurationsTests(unittest.TestCase):
    def test_happy_path_returns_result(self):
        connector = _FakeCadConnector(config_result={"Values": [{"Code": "AVAIL"}, {"Code": "OOS"}]})
        registry = _registry(cad_connector=connector)
        result = registry.execute("get_cad_configurations", {"configuration": "CADUnitStatus"})
        self.assertTrue(result.payload["available"])
        self.assertEqual(result.payload["configuration"], "CADUnitStatus")
        self.assertFalse(result.payload["truncated"])
        self.assertEqual(connector.get_configurations_requests, ["CADUnitStatus"])

    def test_large_nested_list_is_capped_and_flagged(self):
        many = {"Values": [{"Code": f"C{i}"} for i in range(500)]}
        connector = _FakeCadConnector(config_result=many)
        registry = _registry(cad_connector=connector)
        result = registry.execute("get_cad_configurations", {"configuration": "IncidentType"})
        self.assertTrue(result.payload["truncated"])
        self.assertEqual(len(result.payload["result"]["Values"]), 200)

    def test_invalid_configuration_name_is_an_error_payload(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        for bad in ("", "1bad", "way-too-long-" * 10, "has space"):
            result = registry.execute("get_cad_configurations", {"configuration": bad})
            self.assertIn("error", result.payload, msg=bad)

    def test_missing_configuration_is_an_error_payload(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute("get_cad_configurations", {})
        self.assertIn("error", result.payload)

    def test_connector_none_reports_unavailable_without_error(self):
        registry = _registry(cad_connector=None)
        result = registry.execute("get_cad_configurations", {"configuration": "CADUnitStatus"})
        self.assertFalse(result.payload["available"])
        self.assertIn("unavailable", result.payload["error"])

    def test_connector_error_is_an_error_payload_not_an_exception(self):
        connector = _FakeCadConnector(config_error=CloudCadConnectorError("upstream_rejected", "get_configurations"))
        registry = _registry(cad_connector=connector)
        result = registry.execute("get_cad_configurations", {"configuration": "CADUnitStatus"})
        self.assertFalse(result.payload["available"])
        self.assertIn("error", result.payload)

    def test_unknown_filter_key_is_rejected(self):
        registry = _registry(cad_connector=_FakeCadConnector())
        result = registry.execute(
            "get_cad_configurations", {"configuration": "CADUnitStatus", "extra": True}
        )
        self.assertIn("error", result.payload)


class UnknownToolTests(unittest.TestCase):
    def test_unknown_tool_name_is_an_error_payload_not_an_exception(self):
        registry = _registry(calls=[_call()])
        result = registry.execute("dispatch_unit", {"unit": "MED31"})
        self.assertIn("error", result.payload)
        self.assertFalse(result.source.available)


if __name__ == "__main__":
    unittest.main()
