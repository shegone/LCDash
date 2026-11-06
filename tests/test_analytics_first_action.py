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
            # scheduled, no stamp, over cap, stacked, average hold,
            # excluded call type
            return (10, 6, 200.0, 118.0, 2, 2, 600.0, 1, 2, 1, 2, 1956.0, 3)
        return (0, 0, 0)

    def fetchall(self, query, params=None):
        self.queries.append((query, params))
        if "AS dispatcher_ref" in query:
            return [("A DISPATCHER", 10, 6, 200.0, 118.0, 2, 2, 600.0, 1, 2, 1,
                     1, 1122.0, 2, "0123456789abcdef")]
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
            self.assertIn("excluded_type THEN 'excluded_type'", status)
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

    def test_the_listed_call_types_are_not_timed_but_still_counted(self):
        # Ted's list (2026-11-05, four more on 2026-11-06): natures that are
        # never dispatched as an emergency, so the interval says nothing
        # about the dispatcher. CAD carries the lock-out nature under two
        # names, and the match is exact apart from case and punctuation, so
        # both are listed.
        self.assertEqual(ar.EXCLUDED_CALL_TYPES, (
            "Roundtrip", "Take Home", "Transfer / Inter-Facility", "Contact",
            "Move-Up", "Shoplifting", "Complaint", "911 Hang Up",
            "Call Through Office", "911 Open Line", "Information",
            "Be On the Lookout", "Motorist Assist", "Public Works / Utilities",
            "Vehicle Lock Out / Lock In", "Lock Out"))
        # Matching ignores case, spaces and punctuation.
        self.assertEqual(ar.call_type_key(" 911 hang-up "), "911HANGUP")
        self.assertEqual(ar.call_type_key("TRANSFER/INTER-FACILITY"), "TRANSFERINTERFACILITY")
        self.assertIn(ar.call_type_key("Public Works/Utilities"), ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertIn(ar.call_type_key("MOTORIST ASSIST"), ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertIn(ar.call_type_key("VEHICLE LOCKOUT/LOCK-IN"), ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertIn(ar.call_type_key("LOCK-OUT"), ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertIn(ar.call_type_key("Lockout"), ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertEqual(ar.call_type_key(None), "")
        self.assertIn("TRANSFERINTERFACILITY", ar.EXCLUDED_CALL_TYPE_KEYS)
        self.assertIn("BEONTHELOOKOUT", ar.EXCLUDED_CALL_TYPE_KEYS)
        for query in self.processing_queries:
            self.assertIn(
                "regexp_replace(UPPER(incident_description), '[^A-Z0-9]', '', 'g')\n"
                "                    = ANY(%(excluded_call_types)s) AS excluded_type", query)
        for query in self.processing_queries[1:]:
            self.assertIn("COUNT(*) FILTER (WHERE timing_status = 'excluded_type')", query)
        for _, params in self.repository.queries:
            if params and "excluded_call_types" in params:
                self.assertEqual(params["excluded_call_types"], list(ar.EXCLUDED_CALL_TYPE_KEYS))
        # The count travels with the other exclusions, so Calls = timed + not timed.
        summary = self.result["dispatcher_metrics"]
        self.assertEqual(summary["excluded_type"], 3)
        self.assertEqual(summary["excluded_call_types"], list(ar.EXCLUDED_CALL_TYPES))
        self.assertEqual(self.result["dispatchers"][0]["excluded_type"], 2)
        # The discipline tables leave the same calls out.
        discipline_query = next(
            q for q, _ in self.repository.queries if "GROUPING SETS" in q)
        self.assertIn("= ANY(%(excluded_call_types)s)", discipline_query)

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
        self.assertEqual(empty["dispatcher_metrics"]["excluded_type"], 0)
        self.assertEqual(empty["dispatcher_metrics"]["excluded_call_types"],
                         list(ar.EXCLUDED_CALL_TYPES))
        self.assertEqual((empty["discipline_key"], empty["discipline_label"]),
                         ("", "All disciplines"))
        self.assertEqual([o["key"] for o in empty["discipline_options"]],
                         ["", "ems", "fire", "law"])

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

    def test_the_discipline_tables_time_every_call_to_its_first_unit_action(self):
        """A call counts in each discipline whose units acted on it, but the
        clock stops at the first unit assigned to the call, whatever that
        unit's discipline: a discipline added to a call later is not charged
        the time before it was requested."""
        repository = RecordingRepository()
        ar._discipline_metrics(repository, {
            "window_start": None, "window_end": None,
            "excluded_dispatcher_identity": ar.EXCLUDED_DISPATCHER_IDENTITY})
        query = repository.queries[0][0]
        first = query[query.index("call_first_action AS"):query.index("discipline_dispatch AS")]
        self.assertIn("MIN(discipline_stamps.stamp_at) FILTER", first)
        self.assertNotIn("discipline_stamps.discipline", first)
        self.assertIn("GROUP BY selected_calls.cfs_number", first)
        dispatch = query[query.index("discipline_dispatch AS"):query.index("timed AS")]
        self.assertIn("SELECT DISTINCT", dispatch)
        self.assertIn("call_first_action.first_action_at", dispatch)
        self.assertIn("WHERE discipline_stamps.discipline IS NOT NULL", dispatch)
        self.assertNotIn("MIN(", dispatch)


class DisciplineFilterTests(unittest.TestCase):
    """The dispatcher section can be narrowed to EMS, Fire or Law: the calls
    that discipline's units acted on, each still timed to the first unit
    action on the call by any discipline (the first unit assigned stops the
    clock). The page-level metrics stay county-wide."""

    def setUp(self):
        self.repository = RecordingRepository()
        self.window = ar.resolve_analytics_window(period="24h")
        self.result = ar._query_overview(self.repository, self.window, None, "fire")
        self.processing_queries = [
            q for q, _ in self.repository.queries if "END AS timing_status" in q]

    def test_only_the_dispatcher_queries_are_narrowed(self):
        overview_query = self.processing_queries[0]
        self.assertNotIn("%(discipline)s", overview_query)
        self.assertNotIn("EXISTS (SELECT 1 FROM action_stamps", overview_query)
        for query in self.processing_queries[1:]:
            self.assertIn(
                "AND EXISTS (SELECT 1 FROM action_stamps "
                "WHERE action_stamps.cfs_number = lcdash_analytics.calls.cfs_number "
                "AND action_stamps.discipline = %(discipline)s)", query)
            # The discipline chooses the calls only. The stamps are never
            # filtered, so the milestones (and the interval) come from the
            # first action by any unit on the call.
            self.assertNotIn("WHERE stamps.discipline", query)
            self.assertEqual(query.count("%(discipline)s"), 1)
            milestones = query[query.index("call_milestones AS"):query.index("call_processing AS")]
            self.assertNotIn("discipline", milestones)
        for _, params in self.repository.queries:
            if params and "discipline" in params:
                self.assertEqual(params["discipline"], "fire")

    def test_every_stamp_carries_the_roster_discipline(self):
        for query in self.processing_queries:
            self.assertIn("END AS discipline", query)
            self.assertIn("stamps.agency = ANY(%(ems_agencies)s)", query)
            self.assertIn("stamps.unit_type LIKE 'EMS %%' THEN 'ems'", query)
            self.assertIn("stamps.agency LIKE 'FC %%'", query)
            self.assertIn("stamps.unit_type LIKE 'FIRE %%' THEN 'fire'", query)
            self.assertIn("stamps.agency = ANY(%(law_agencies)s)", query)
            self.assertIn("stamps.unit_type IN ('PATROL CAR', 'COUNTY ADMIN') THEN 'law'", query)
            # The log and per-unit stamps read the unit roster; the
            # per-agency stamp falls back to its ORI.
            self.assertEqual(query.count("LEFT JOIN lcdash_analytics.units AS unit_record"), 2)
            self.assertIn("UPPER(agency_ori) AS agency", query)
        for _, params in self.repository.queries:
            if params and "ems_agencies" in params:
                self.assertEqual(params["ems_agencies"], list(ar.EMS_AGENCY_CODES))
                self.assertEqual(params["law_agencies"], list(ar.LAW_AGENCY_CODES))

    def test_the_snapshot_names_the_filter(self):
        self.assertEqual((self.result["discipline_key"], self.result["discipline_label"]),
                         ("fire", "Fire"))
        self.assertEqual(self.result["discipline_options"], [
            {"key": "", "label": "All disciplines"}, {"key": "ems", "label": "EMS"},
            {"key": "fire", "label": "Fire"}, {"key": "law", "label": "Law"}])

    def test_the_filter_value_is_checked(self):
        self.assertEqual(ar.resolve_discipline(" EMS "), "ems")
        self.assertEqual(ar.resolve_discipline(None), "")
        for bad in ("police", "all", "fire;drop"):
            with self.assertRaises(ar.AnalyticsRangeError):
                ar.resolve_discipline(bad)
        with self.assertRaises(ar.AnalyticsRangeError):
            ar.get_analytics_overview(period="24h", discipline="police")
        with self.assertRaises(ar.AnalyticsRangeError):
            ar.get_dispatcher_slow_calls("0123456789abcdef", period="24h", discipline="police")

    def test_the_routes_forward_the_filter(self):
        client = TestClient(app)
        with patch("app.main.get_analytics_overview") as overview:
            overview.return_value = ar._empty_overview(self.window, "none")
            self.assertEqual(client.get("/api/analytics/overview?period=7d&discipline=law").status_code, 200)
            overview.assert_called_once_with(
                period="7d", start="", end="", tenant_context=None, discipline="law")
        with patch("app.main.get_dispatcher_slow_calls") as slow:
            slow.return_value = {"available": True, "calls": [], "total": 0, "shown": 0}
            client.get("/api/analytics/dispatcher-slow-calls?dispatcher=0123456789abcdef"
                       "&period=7d&discipline=ems")
            slow.assert_called_once_with(
                "0123456789abcdef", period="7d", start="", end="", tenant_context=None,
                discipline="ems")
        with patch("app.main.get_analytics_overview") as overview, \
                patch("app.main.get_analytics_database_status",
                      return_value=_route_test_fixture().status):
            overview.return_value = ar._empty_overview(self.window, "none")
            self.assertEqual(client.get("/analytics?period=7d&discipline=fire").status_code, 200)
            overview.assert_called_once_with(
                period="7d", start="", end="", tenant_context=None, discipline="fire")

    def test_a_bad_filter_on_the_api_is_a_400(self):
        client = TestClient(app)
        with patch("app.main.get_analytics_overview",
                   side_effect=ar.AnalyticsRangeError("Choose EMS, Fire or Law, or all disciplines.")):
            response = client.get("/api/analytics/overview?period=7d&discipline=police")
        self.assertEqual(response.status_code, 400)
        self.assertIn("EMS, Fire or Law", response.json()["detail"])


class PageWordingTests(unittest.TestCase):
    def setUp(self):
        base = _route_test_fixture()
        self.overview = base.overview
        self.overview["dispatcher_metrics"].update(
            {"excluded_scheduled": 3, "excluded_no_stamp": 4, "excluded_over_cap": 5,
             "excluded_type": 6, "excluded_call_types": list(ar.EXCLUDED_CALL_TYPES),
             "processing_cap_minutes": 60, "stacked_calls": 2, "average_hold": "32:36"})
        self.overview["dispatchers"][0].update(
            {"dispatcher_ref": "0123456789abcdef", "excluded_scheduled": 1,
             "excluded_no_stamp": 2, "excluded_over_cap": 3, "excluded_type": 4,
             "stacked_calls": 1, "average_hold": "18:42"})
        self.overview["discipline_key"] = "fire"
        self.overview["discipline_label"] = "Fire"
        self.overview["discipline_options"] = [
            {"key": key, "label": label} for key, label in ar.DISCIPLINE_FILTERS]
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
        self.assertIn("Not timed: 6 of a call type that is not timed", self.html)
        self.assertIn("(16 types)</span> &middot; 3 scheduled &middot; 4 with no unit stamp", self.html)
        self.assertIn("5 over 60 minutes", self.html)
        self.assertIn("these call types, which are not dispatched as emergencies: "
                      "Roundtrip, Take Home, Transfer / Inter-Facility, Contact, Move-Up, "
                      "Shoplifting, Complaint, 911 Hang Up, Call Through Office, 911 Open Line, "
                      "Information, Be On the Lookout, Motorist Assist, Public Works / Utilities, "
                      "Vehicle Lock Out / Lock In, Lock Out. Those calls still count as entered.",
                      self.html)
        self.assertIn("38 of 40 calls", self.html)
        self.assertIn("scheduled calls are excluded", self.html)

    def test_each_dispatcher_row_shows_its_untimed_calls(self):
        self.assertIn("Not timed", self.html)
        self.assertIn('title="4 call type not timed · 1 scheduled · 2 no unit stamp · 3 over 60 min">10</td>',
                      self.html)
        self.assertIn('title="average unit hold 18:42">1</td>', self.html)
        self.assertIn(">Stacked</th>", self.html)

    def test_the_discipline_rule_and_the_unit_average_are_explained(self):
        self.assertIn("agency in the unit roster", self.html)
        self.assertIn("Call received to on scene; scheduled calls excluded", self.html)

    def test_the_discipline_filter_links_keep_the_window_and_name_the_view(self):
        self.assertIn("Call-Taker Workload and CAD Processing: Fire</h4>", self.html)
        self.assertIn("Showing Fire only: the calls that Fire units acted on, each timed to the "
                      "first unit assigned on the call by any discipline", self.html)
        self.assertIn('id="dispatcher-discipline-filter"', self.html)
        self.assertIn('class="period-link " href="/analytics?period=30d" >All</a>', self.html)
        self.assertIn('class="period-link " href="/analytics?period=30d&amp;discipline=ems" >EMS</a>',
                      self.html)
        self.assertIn('class="period-link active" href="/analytics?period=30d&amp;discipline=fire" >Fire</a>',
                      self.html)
        self.assertIn('class="period-link " href="/analytics?period=30d&amp;discipline=law" >Law</a>',
                      self.html)

    def test_a_custom_range_travels_with_the_filter(self):
        base = _route_test_fixture()
        overview = base.overview
        overview.update({"period_key": "custom", "start_date": "2026-10-01",
                         "end_date": "2026-10-03", "discipline_key": "",
                         "discipline_label": "All disciplines",
                         "discipline_options": [
                             {"key": key, "label": label} for key, label in ar.DISCIPLINE_FILTERS]})
        with patch("app.main.get_analytics_overview", return_value=overview), \
                patch("app.main.get_analytics_database_status", return_value=base.status):
            html = " ".join(TestClient(app).get(
                "/analytics?start=2026-10-01&end=2026-10-03").text.split())
        self.assertIn('class="period-link active" href="/analytics?start=2026-10-01&amp;end=2026-10-03" >All</a>',
                      html)
        self.assertIn('href="/analytics?start=2026-10-01&amp;end=2026-10-03&amp;discipline=law" >Law</a>',
                      html)
        self.assertIn("Call-Taker Workload and CAD Processing</h4>", html)
        self.assertNotIn("Showing ", html.split("DISPATCHER / CAD ENTRY METRICS")[1][:600])


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "static" / "js" / "lcdash-analytics.js").read_text(encoding="utf-8")

    def test_the_drill_down_names_the_first_action_and_lists_over_cap_calls_apart(self):
        self.assertIn('"First action"', self.script)
        self.assertIn('"Unit hold"', self.script)
        self.assertIn("call.hold", self.script)
        self.assertIn("call.first_action_by", self.script)
        # The drill-down asks for the same discipline the table shows.
        self.assertIn('if (snapshot.discipline_key) params.set("discipline", snapshot.discipline_key);',
                      self.script)
        self.assertIn("data.over_cap", self.script)
        self.assertIn("not averaged and not in the count above", self.script)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(banned, self.script, banned)


if __name__ == "__main__":
    unittest.main()
