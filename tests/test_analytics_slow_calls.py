"""Analytics dispatcher table: 90th percentile, the "> 3 Min" call list, and
the Average explanation.

The SQL is exercised through recording fakes (this suite has no database); the
queries were also run against a real PostgreSQL before release (see the
handoff).
"""

import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import analytics_reporting
from app.services.analytics_reporting import (
    AnalyticsReferenceError,
    SLOW_CALL_LIMIT,
    SLOW_CALL_THRESHOLD_SECONDS,
    get_dispatcher_slow_calls,
    resolve_analytics_window,
)

ROOT = Path(__file__).resolve().parents[1]


def _route_test_fixture():
    """The overview fixture the existing analytics route tests already use."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "analytics_reporting_tests", ROOT / "tests" / "test_analytics_reporting.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = module.AnalyticsOverviewRouteTests("test_analytics_overview_api_is_no_store")
    base.setUp()
    return base
REF = "0123456789abcdef"


class RecordingRepository:
    """Answers the overview queries with fixed rows and remembers every query."""

    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def initialize_schema(self):
        return None

    def fetchone(self, query, params=None):
        self.queries.append((query, params))
        if "MAX(source_collected_at)" in query:
            return (10, 0, None, None, None, 0, None, 0)
        if "calls_with_call_taker" in query:
            # calls, samples, average, median, within 90, over 180, p90,
            # excluded scheduled, no stamp, over cap, stacked, average hold
            return (4, 3, 300.0, 120.0, 1, 1, 540.0, 1, 0, 0, 0, None)
        return (0, 0, 0)

    def fetchall(self, query, params=None):
        self.queries.append((query, params))
        if "AS dispatcher_ref" in query:
            return [("A DISPATCHER", 4, 3, 300.0, 120.0, 1, 1, 540.0, 1, 0, 0, 0, None, REF)]
        if "AS agency" in query and "LIMIT 8" in query:
            return [("SLD", 10)]
        return []


class NinetiethPercentileTests(unittest.TestCase):
    def setUp(self):
        self.repository = RecordingRepository()
        window = resolve_analytics_window(period="24h")
        self.result = analytics_reporting._query_overview(self.repository, window, None)

    def test_the_summary_and_each_dispatcher_carry_a_90th_percentile(self):
        self.assertEqual(self.result["dispatcher_metrics"]["p90_processing"], "9:00")
        row = self.result["dispatchers"][0]
        self.assertEqual(row["p90_processing"], "9:00")
        self.assertEqual(row["median_processing"], "2:00")
        self.assertEqual(row["average_processing"], "5:00")

    def test_both_queries_use_the_same_population_as_the_median(self):
        sql = [q for q, _ in self.repository.queries if "p90_processing_seconds" in q]
        self.assertEqual(len(sql), 2)
        for query in sql:
            # Every aggregate reads the one timing_status column, which is
            # 'timed' only for a non-scheduled call with a unit action inside
            # the cap.
            p90 = query[query.index("PERCENTILE_CONT(0.9)"):]
            p90 = p90[:p90.index("p90_processing_seconds")]
            self.assertIn("timing_status = 'timed'", p90)
            median = query[query.index("PERCENTILE_CONT(0.5)"):]
            median = median[:median.index("median_processing_seconds")]
            self.assertIn("timing_status = 'timed'", median)
            status = query[query.index("CASE"):query.index("END AS timing_status")]
            self.assertIn("is_scheduled THEN 'scheduled'", status)
            self.assertIn("first_action_at IS NULL THEN 'no_stamp'", status)
            self.assertIn("make_interval(secs => %(processing_cap_seconds)s)", status)
            self.assertIn("THEN 'over_cap'", status)

    def test_the_dispatcher_is_named_by_an_opaque_reference(self):
        row = self.result["dispatchers"][0]
        self.assertEqual(row["dispatcher_ref"], REF)
        self.assertNotIn("unique_identifier", str(row))
        query = next(q for q, _ in self.repository.queries if "AS dispatcher_ref" in q)
        self.assertIn("sha256(convert_to(dispatcher_key, 'UTF8'))", query)

    def test_the_empty_snapshot_has_the_new_field(self):
        window = resolve_analytics_window(period="24h")
        empty = analytics_reporting._empty_overview(window, "none")
        self.assertEqual(empty["dispatcher_metrics"]["p90_processing"], "—")


class FakeSlowCallRepository:
    rows = []

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def initialize_schema(self):
        return None

    def fetchall(self, query, params=None):
        self.calls.append((query, params))
        return self.rows


class SlowCallListTests(unittest.TestCase):
    def setUp(self):
        received = datetime(2026, 10, 21, 17, 7, tzinfo=timezone.utc)
        # cfs, received, seconds, description, code, priority, agency,
        # first-action kind, party, source, timing_status, total timed,
        # total over cap, hold seconds
        FakeSlowCallRepository.rows = [
            ("CFS-1", received, 1800.0, "Welfare check", "WELCK", "3", "LEASA",
             "enroute", "MED10", "unit", "timed", 2, 0, None),
            ("CFS-2", received, 240.0, "", "MED", "", "",
             "stacked", "SYN-7", "log", "timed", 2, 0, 1122.0),
        ]
        self.repository = FakeSlowCallRepository()
        self.patches = [
            patch.object(analytics_reporting, "AnalyticsRepository", return_value=self.repository),
            patch.object(analytics_reporting, "analytics_database_is_configured", return_value=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_it_returns_the_calls_longest_first_with_plain_fields(self):
        result = get_dispatcher_slow_calls(REF, period="24h")

        self.assertTrue(result["available"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["shown"], 2)
        first, second = result["calls"]
        self.assertEqual(first["cfs_number"], "CFS-1")
        self.assertEqual(first["processing"], "30:00")
        self.assertEqual(first["incident"], "Welfare check")
        self.assertEqual(first["received_at"], "Oct 21, 13:07")
        # Missing fields read as a dash, and the code stands in for a blank description.
        self.assertEqual(second["incident"], "MED")
        self.assertEqual(second["priority"], "—")
        self.assertEqual(second["agency"], "Unknown")
        self.assertEqual(first["first_action"], "En route")
        self.assertEqual(first["first_action_by"], "MED10")
        self.assertEqual(second["first_action"], "Stacked")
        self.assertEqual((first["hold"], second["hold"]), ("", "18:42"))
        self.assertEqual(
            set(first), {"cfs_number", "received_at", "processing",
                         "processing_seconds", "incident", "priority", "agency",
                         "first_action", "first_action_by", "hold"})
        self.assertEqual(result["over_cap"], {"total": 0, "shown": 0, "calls": []})
        self.assertEqual(result["cap_minutes"], 60)

    def test_calls_over_the_cap_are_listed_apart_not_hidden(self):
        received = datetime(2026, 10, 21, 17, 7, tzinfo=timezone.utc)
        FakeSlowCallRepository.rows = [
            ("CFS-9", received, 5400.0, "Hang up", "HANG", "30", "LCSO",
             "assigned", "SO1", "unit", "over_cap", 1, 1, None),
            ("CFS-1", received, 300.0, "Alarm", "ALM", "3", "FC 1",
             "assigned", "E1", "unit", "timed", 1, 1, None),
        ]

        result = get_dispatcher_slow_calls(REF, period="24h")

        self.assertEqual([c["cfs_number"] for c in result["calls"]], ["CFS-1"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["over_cap"]["total"], 1)
        self.assertEqual(result["over_cap"]["calls"][0]["cfs_number"], "CFS-9")
        self.assertEqual(result["over_cap"]["calls"][0]["processing"], "1:30:00")

    def test_it_asks_for_exactly_the_calls_behind_the_over_3_minute_count(self):
        get_dispatcher_slow_calls(REF, period="24h")

        query, params = self.repository.calls[0]
        self.assertEqual(params["threshold_seconds"], SLOW_CALL_THRESHOLD_SECONDS)
        self.assertEqual(SLOW_CALL_THRESHOLD_SECONDS, 180)
        self.assertEqual(params["row_limit"], SLOW_CALL_LIMIT)
        self.assertEqual(params["dispatcher_ref"], REF)
        self.assertEqual(params["processing_cap_seconds"], 3600)
        # Same rules as the table's count, plus the over-cap list.
        for rule in ("timing_status IN ('timed', 'over_cap')",
                     "> %(threshold_seconds)s", "ORDER BY dispatch_seconds DESC",
                     "FROM lcdash_analytics.unit_responses",
                     "FROM lcdash_analytics.call_agency_times",
                     "DISTINCT ON (action_stamps.cfs_number)"):
            self.assertIn(rule, query)
        # The reference travels as a bound parameter, never in the SQL text.
        self.assertNotIn(REF, query)

    def test_it_never_selects_location_or_narrative(self):
        get_dispatcher_slow_calls(REF, period="24h")

        query = self.repository.calls[0][0].lower()
        for column in ("latitude", "longitude", "city", "beat", "zone", "narrative",
                       "address"):
            self.assertNotIn(column, query, column)

    def test_a_reference_that_this_page_did_not_make_is_rejected(self):
        for bad in ("", "short", "0123456789abcdeg", REF + "0", REF.upper(), "x" * 16,
                    "0123456789abcde'", None):
            with self.assertRaises(AnalyticsReferenceError, msg=repr(bad)):
                get_dispatcher_slow_calls(bad)
        self.assertEqual(self.repository.calls, [])

    def test_no_rows_is_an_empty_list_not_an_error(self):
        FakeSlowCallRepository.rows = []

        result = get_dispatcher_slow_calls(REF, period="24h")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["calls"], [])

    def test_an_unconfigured_database_gives_a_safe_empty_answer(self):
        with patch.object(analytics_reporting, "analytics_database_is_configured", return_value=False):
            result = get_dispatcher_slow_calls(REF, period="24h")

        self.assertFalse(result["available"])
        self.assertEqual(result["calls"], [])
        self.assertIn("not configured", result["message"])


class SlowCallRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.get_dispatcher_slow_calls")
    def test_the_route_passes_the_reference_and_period_and_is_no_store(self, service):
        service.return_value = {"available": True, "calls": [], "total": 0, "shown": 0}

        response = self.client.get(
            f"/api/analytics/dispatcher-slow-calls?dispatcher={REF}&period=7d")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        service.assert_called_once_with(
            REF, period="7d", start="", end="", tenant_context=None)

    @patch("app.main.get_dispatcher_slow_calls")
    def test_a_custom_range_is_forwarded(self, service):
        service.return_value = {"available": True, "calls": [], "total": 0, "shown": 0}

        self.client.get(
            f"/api/analytics/dispatcher-slow-calls?dispatcher={REF}"
            "&start=2026-10-01&end=2026-10-03")

        service.assert_called_once_with(
            REF, period="30d", start="2026-10-01", end="2026-10-03", tenant_context=None)

    @patch("app.main.get_dispatcher_slow_calls")
    def test_a_bad_reference_or_range_is_a_400(self, service):
        service.side_effect = AnalyticsReferenceError("That dispatcher reference is not valid.")
        self.assertEqual(
            self.client.get("/api/analytics/dispatcher-slow-calls?dispatcher=nope").status_code, 400)
        service.side_effect = analytics_reporting.AnalyticsRangeError("Choose both a start date and an end date.")
        self.assertEqual(
            self.client.get(f"/api/analytics/dispatcher-slow-calls?dispatcher={REF}&start=2026-10-01").status_code,
            400)


class TablePageTests(unittest.TestCase):
    def setUp(self):
        base = _route_test_fixture()
        self.overview = base.overview
        self.overview["dispatcher_metrics"]["p90_processing"] = "8:40"
        self.overview["dispatchers"][0]["p90_processing"] = "8:40"
        self.overview["dispatchers"][0]["dispatcher_ref"] = REF
        self.client = TestClient(app)
        with patch("app.main.get_analytics_overview", return_value=self.overview), \
                patch("app.main.get_analytics_database_status", return_value=base.status):
            self.html = self.client.get("/analytics?period=30d").text

    def test_the_table_has_a_90th_percentile_column(self):
        self.assertIn("90th %", self.html)
        self.assertIn("8:40", self.html)
        self.assertIn("90th percentile 8:40", self.html)

    def test_the_average_is_labelled_and_explained(self):
        self.assertIn("pulled up by long calls", self.html)
        self.assertIn("A few very long calls pull it up", self.html)
        self.assertIn("The average adds every second", self.html)

    def test_the_over_3_minute_count_opens_the_list(self):
        self.assertIn(f'data-slow-calls="{REF}"', self.html)
        self.assertIn('id="dispatcher-slow-calls"', self.html)
        self.assertIn('aria-controls="dispatcher-slow-calls"', self.html)
        self.assertIn("lcdash-analytics.js?v=20261105-stack", self.html)

    def test_a_dispatcher_with_no_slow_calls_gets_a_plain_number(self):
        self.overview["dispatchers"][0]["over_180_count"] = 0
        with patch("app.main.get_analytics_overview", return_value=self.overview), \
                patch("app.main.get_analytics_database_status", return_value={"configured": True, "connected": True, "calls_stored": 1, "unit_responses_stored": 1, "last_run": {}}):
            html = self.client.get("/analytics?period=30d").text
        self.assertNotIn("data-slow-calls", html)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "static" / "js" / "lcdash-analytics.js").read_text(encoding="utf-8")

    def test_server_text_only_goes_in_as_text(self):
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(banned, self.script, banned)

    def test_it_calls_the_documented_endpoint_with_the_reference(self):
        self.assertIn("/api/analytics/dispatcher-slow-calls?", self.script)
        self.assertIn('params.set("dispatcher", reference)', self.script)
        self.assertIn("button.dataset.slowCalls", self.script)

    def test_the_list_is_bound_before_the_charts_can_bail_out(self):
        start = self.script[self.script.index("function start()"):]
        self.assertLess(start.index("bindSlowCalls(snapshot)"), start.index("typeof Chart"))


if __name__ == "__main__":
    unittest.main()
