"""CAD processing = call received to the earliest unit action on the call.

Background: docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md. The
page used to read only the per-agency "Dispatched" stamp, so a later Assign
(a re-assign after arrival) or another unit's Enroute ended the interval and
the per-unit record was never consulted. These tests pin the query text, the
result shapes and the page wording with recording fakes; the same SQL runs
against a real PostgreSQL in tests/test_analytics_postgres_live.py with the
four reconstructed timelines.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import analytics_reporting as ar

ROOT = Path(__file__).resolve().parents[1]


def _route_test_fixture():
    spec = importlib.util.spec_from_file_location(
        "analytics_reporting_tests", ROOT / "tests" / "test_analytics_reporting.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = module.AnalyticsOverviewRouteTests("test_analytics_overview_api_is_no_store")
    base.setUp()
    return base


class RecordingRepository:
    def __init__(self):
        self.queries = []

    def fetchone(self, query, params=None):
        self.queries.append((query, params))
        if "MAX(source_collected_at)" in query:
            # total, unit responses, avg processing, avg response, median
            # response, response samples, latest, scheduled
            return (12, 20, 118.0, 900.0, 700.0, 6, None, 2)
        if "calls_with_call_taker" in query:
            # calls, timed, average, median, within 90, over 180, p90,
            # scheduled, no stamp, over cap, stacked, average hold
            return (10, 6, 200.0, 118.0, 2, 2, 600.0, 1, 2, 1, 2, 1956.0)
        return (0, 0, 0)

    def fetchall(self, query, params=None):
        self.queries.append((query, params))
        if "AS dispatcher_ref" in query:
            return [("A DISPATCHER", 10, 6, 200.0, 118.0, 2, 2, 600.0, 1, 2, 1,
                     1, 1122.0, "0123456789abcdef")]
        return []


class ProcessingDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.repository = RecordingRepository()
        self.window = ar.resolve_analytics_window(period="24h")
        self.result = ar._query_overview(self.repository, self.window, None)
        self.processing_queries = [
            q for q, _ in self.repository.queries if "END AS timing_status" in q]

    def test_every_processing_query_reads_all_three_timing_records(self):
        # overview metrics, dispatcher summary, dispatcher rows
        self.assertEqual(len(self.processing_queries), 3)
        for query in self.processing_queries:
            self.assertIn("FROM lcdash_analytics.call_agency_times", query)
            self.assertIn("FROM lcdash_analytics.unit_responses", query)
            self.assertIn("FROM lcdash_analytics.unit_status_events", query)
            for kind in ("'assigned', 1", "'enroute', 2", "'staged', 3", "'on_scene', 4"):
                self.assertIn(kind, query)
            # Stack ends the interval (someone was added to the call) and is
            # named before an Assign at the same second.
            self.assertIn("WHEN 'stacked' THEN 0", query)
            self.assertIn(
                "event_kind IN ('stacked', 'assigned', 'enroute', 'staged', 'on_scene')", query)
            # The hold is Stack to the first non-Stack action, never negative.
            self.assertIn("WHERE action_stamps.kind <> 'stacked'", query)
            self.assertIn("first_dispatch_at > call_milestones.first_action_at", query)
            self.assertIn("END AS hold_seconds", query)
            self.assertIn(
                "MIN(action_stamps.stamp_at) FILTER (\n"
                "                    WHERE action_stamps.stamp_at >= selected_calls.call_received_at\n"
                "                ) AS first_action_at", query)
            self.assertNotIn("first_dispatched_at", query)

    def test_the_timing_status_buckets_are_the_only_population_rule(self):
        for query in self.processing_queries:
            status = query[query.index("CASE"):query.index("END AS timing_status")]
            self.assertIn("is_scheduled THEN 'scheduled'", status)
            self.assertIn("first_action_at IS NULL THEN 'no_stamp'", status)
            self.assertIn("make_interval(secs => %(processing_cap_seconds)s)", status)
            self.assertIn("THEN 'over_cap'", status)
            self.assertIn("ELSE 'timed'", status)
        for _, params in self.repository.queries:
            if params and "processing_cap_seconds" in params:
                self.assertEqual(params["processing_cap_seconds"], 3600)
        self.assertEqual(ar.PROCESSING_CAP_MINUTES, 60)

    def test_only_the_dispatcher_queries_filter_on_the_call_taker(self):
        overview_query = self.processing_queries[0]
        self.assertNotIn("excluded_dispatcher_identity", overview_query)
        for query in self.processing_queries[1:]:
            self.assertIn("UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s", query)

    def test_response_time_uses_the_earliest_on_scene_and_skips_scheduled_calls(self):
        query = self.processing_queries[0]
        self.assertIn("action_stamps.kind = 'on_scene'", query)
        self.assertIn("FILTER (WHERE first_on_scene_at IS NOT NULL AND NOT is_scheduled)", query)
        self.assertIn("AVG(processing_seconds) FILTER (WHERE timing_status = 'timed')", query)
        metrics = self.result["metrics"]
        self.assertEqual(metrics["response_samples"], 6)
        self.assertEqual(metrics["response_eligible_calls"], 10)
        self.assertEqual(metrics["response_coverage_percent"], 60)
        self.assertEqual(metrics["scheduled_calls"], 2)
        self.assertEqual(metrics["average_processing"], "1:58")

    def test_busiest_units_leave_scheduled_calls_out_of_the_average(self):
        query = next(q for q, _ in self.repository.queries if "AS response_count" in q)
        self.assertIn("NOT selected_calls.is_scheduled", query)

    def test_exclusions_are_counted_for_the_summary_and_each_dispatcher(self):
        summary = self.result["dispatcher_metrics"]
        self.assertEqual((summary["processing_samples"], summary["over_180_count"]), (6, 2))
        self.assertEqual((summary["excluded_scheduled"], summary["excluded_no_stamp"],
                          summary["excluded_over_cap"], summary["processing_cap_minutes"]),
                         (1, 2, 1, 60))
        row = self.result["dispatchers"][0]
        self.assertEqual(row["dispatcher_ref"], "0123456789abcdef")
        self.assertEqual((row["excluded_scheduled"], row["excluded_no_stamp"],
                          row["excluded_over_cap"]), (1, 2, 1))
        self.assertEqual(row["within_90_percent"], 33)

    def test_stacked_calls_and_the_unit_hold_are_reported_apart(self):
        summary = self.result["dispatcher_metrics"]
        self.assertEqual((summary["stacked_calls"], summary["average_hold"]), (2, "32:36"))
        row = self.result["dispatchers"][0]
        self.assertEqual((row["stacked_calls"], row["average_hold"]), (1, "18:42"))
        for query in self.processing_queries[1:]:
            self.assertIn("timing_status = 'timed' AND hold_seconds > 0", query)
            self.assertIn("AS average_hold_seconds", query)

    def test_the_empty_snapshot_carries_the_new_fields(self):
        empty = ar._empty_overview(self.window, "none")
        self.assertEqual(empty["metrics"]["response_eligible_calls"], 0)
        self.assertEqual(empty["dispatcher_metrics"]["excluded_over_cap"], 0)
        self.assertEqual(empty["dispatcher_metrics"]["processing_cap_minutes"], 60)
        self.assertEqual((empty["dispatcher_metrics"]["stacked_calls"],
                          empty["dispatcher_metrics"]["average_hold"]), (0, "—"))

    def test_no_location_or_narrative_columns(self):
        for query, _ in self.repository.queries:
            if "END AS timing_status" not in query:
                continue
            for column in ("latitude", "longitude", "city", "narrative", "address"):
                self.assertNotIn(column, query.lower(), column)


class SharedSqlTests(unittest.TestCase):
    def test_extra_columns_travel_into_the_call_rows(self):
        sql = ar._call_processing_sql(dispatcher_calls=True, extra_columns=",\n priority")
        self.assertIn("source_collected_at,\n priority", sql)
        self.assertIn("selected_calls.*", sql)

    def test_first_action_labels(self):
        self.assertEqual(ar.FIRST_ACTION_LABELS, {
            "stacked": "Stacked", "assigned": "Assigned", "enroute": "En route",
            "staged": "Staged", "on_scene": "On scene"})

    def test_the_discipline_query_times_a_stack_by_the_stacked_units_discipline(self):
        repository = RecordingRepository()
        ar._discipline_metrics(repository, {
            "window_start": None, "window_end": None,
            "excluded_dispatcher_identity": ar.EXCLUDED_DISPATCHER_IDENTITY})
        query = repository.queries[0][0]
        self.assertIn("FROM lcdash_analytics.unit_status_events AS status_event", query)
        self.assertIn("ON unit_record.unit_number = status_event.unit_number", query)
        self.assertIn("IN ('stacked', 'assigned', 'enroute', 'staged', 'on_scene')", query)


class PageWordingTests(unittest.TestCase):
    def setUp(self):
        base = _route_test_fixture()
        self.overview = base.overview
        self.overview["dispatcher_metrics"].update(
            {"excluded_scheduled": 3, "excluded_no_stamp": 4, "excluded_over_cap": 5,
             "processing_cap_minutes": 60, "stacked_calls": 2, "average_hold": "32:36"})
        self.overview["dispatchers"][0].update(
            {"dispatcher_ref": "0123456789abcdef", "excluded_scheduled": 1,
             "excluded_no_stamp": 2, "excluded_over_cap": 3,
             "stacked_calls": 1, "average_hold": "18:42"})
        self.overview["metrics"].update(
            {"response_samples": 38, "response_eligible_calls": 40})
        # The discipline section renders only when the server sent its groups.
        self.overview["dispatcher_disciplines"] = [
            {"key": key, "label": label, "overall": None, "dispatchers": []}
            for key, label in ar.DISCIPLINES]
        self.overview["iso_fire"] = ar._empty_overview(
            ar.resolve_analytics_window(period="24h"), "")["iso_fire"]
        client = TestClient(app)
        with patch("app.main.get_analytics_overview", return_value=self.overview), \
                patch("app.main.get_analytics_database_status", return_value=base.status):
            self.html = " ".join(client.get("/analytics?period=30d").text.split())

    def test_the_definition_and_its_limits_are_stated(self):
        self.assertIn("earliest action by any unit of any agency on the call", self.html)
        self.assertIn("A Stack (the call queued on a busy unit) ends the interval like an Assign",
                      self.html)
        self.assertIn("Stacked first: 2 timed calls were queued on a busy unit, average hold 32:36",
                      self.html)
        self.assertIn("Not timed: 3 scheduled &middot; 4 with no unit stamp", self.html)
        self.assertIn("5 over 60 minutes", self.html)
        self.assertIn("38 of 40 calls", self.html)
        self.assertIn("scheduled calls are excluded", self.html)

    def test_each_dispatcher_row_shows_its_untimed_calls(self):
        self.assertIn("Not timed", self.html)
        self.assertIn('title="1 scheduled · 2 no unit stamp · 3 over 60 min">6</td>', self.html)
        self.assertIn('title="average unit hold 18:42">1</td>', self.html)
        self.assertIn(">Stacked</th>", self.html)

    def test_the_discipline_rule_and_the_unit_average_are_explained(self):
        self.assertIn("agency in the unit roster", self.html)
        self.assertIn("Call received to on scene; scheduled calls excluded", self.html)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "static" / "js" / "lcdash-analytics.js").read_text(encoding="utf-8")

    def test_the_drill_down_names_the_first_action_and_lists_over_cap_calls_apart(self):
        self.assertIn('"First action"', self.script)
        self.assertIn('"Unit hold"', self.script)
        self.assertIn("call.hold", self.script)
        self.assertIn("call.first_action_by", self.script)
        self.assertIn("data.over_cap", self.script)
        self.assertIn("not averaged and not in the count above", self.script)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(banned, self.script, banned)


if __name__ == "__main__":
    unittest.main()
