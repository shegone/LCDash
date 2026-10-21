"""Analytics: call processing by discipline, and the ISO fire alarm-processing view.

Recording fakes here; tests/test_analytics_postgres_live.py runs the same SQL
against a real PostgreSQL when LCDASH_TEST_PG_DSN is set.
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


class FakeRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetchall(self, query, params=None):
        self.calls.append((query, params))
        return self.rows


PARAMS = {"window_start": 1, "window_end": 2, "excluded_dispatcher_identity": "KIM MAYNARD"}

# discipline, is_total, call_taker, ref, timed, average, median, p90, within fast, within slow, over 180
ROWS = [
    ("fire", 0, "SMALL ONE", "1111111111111111", 2, 80.0, 70.0, 100.0, 1, 2, 0),
    ("fire", 0, "BUSY ONE", "2222222222222222", 8, 150.0, 90.0, 400.0, 8, 7, 2),
    ("fire", 1, "BUSY ONE", None, 10, 136.0, 85.0, 380.0, 9, 9, 2),
    ("ems", 1, "BUSY ONE", None, 3, 60.0, 50.0, 90.0, 3, 3, 0),
    ("law", 0, "BUSY ONE", "2222222222222222", 4, 200.0, 150.0, 500.0, 1, 2, 2),
]


class DisciplineMetricsTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository(ROWS)
        self.groups, self.iso = ar._discipline_metrics(self.repository, dict(PARAMS))
        self.by = {group["key"]: group for group in self.groups}

    def test_fire_ems_law_in_that_order(self):
        self.assertEqual([g["key"] for g in self.groups], ["fire", "ems", "law"])
        self.assertEqual([g["label"] for g in self.groups], ["Fire", "EMS", "Law"])

    def test_dispatchers_sort_by_timed_calls_and_all_dispatchers_is_separate(self):
        fire = self.by["fire"]
        self.assertEqual([d["call_taker"] for d in fire["dispatchers"]], ["BUSY ONE", "SMALL ONE"])
        self.assertEqual(fire["overall"]["call_taker"], "All dispatchers")
        self.assertEqual(fire["overall"]["dispatcher_ref"], "")
        self.assertEqual(fire["dispatchers"][0]["dispatcher_ref"], "2222222222222222")

    def test_values_are_formatted_and_percentages_have_one_decimal(self):
        busy = self.by["fire"]["dispatchers"][0]
        self.assertEqual((busy["average"], busy["median"], busy["p90"]), ("2:30", "1:30", "6:40"))
        self.assertEqual(busy["within_fast_percent"], 100.0)
        self.assertEqual(busy["within_slow_percent"], 87.5)
        self.assertEqual(busy["over_180_count"], 2)

    def test_a_discipline_with_no_calls_is_empty_not_missing(self):
        self.assertEqual(self.by["ems"]["dispatchers"], [])
        self.assertIsNone(self.by["law"]["overall"])

    def test_the_iso_fire_view_uses_the_2016_nfpa_1221_targets(self):
        self.assertEqual(
            (ar.PROCESSING_TARGET_FAST_SECONDS, ar.PROCESSING_TARGET_FAST_PERCENT,
             ar.PROCESSING_TARGET_SLOW_SECONDS, ar.PROCESSING_TARGET_SLOW_PERCENT),
            (64, 90, 106, 95))
        iso = self.iso
        self.assertEqual(iso["timed"], 10)
        self.assertEqual(iso["fast_percent"], 90.0)
        self.assertTrue(iso["meets_fast"])
        self.assertEqual(iso["slow_percent"], 90.0)
        self.assertFalse(iso["meets_slow"])

    def test_no_fire_calls_means_no_verdict(self):
        groups, iso = ar._discipline_metrics(FakeRepository([]), dict(PARAMS))
        self.assertEqual(iso["timed"], 0)
        self.assertIsNone(iso["meets_fast"])
        self.assertIsNone(iso["meets_slow"])
        self.assertTrue(all(group["overall"] is None for group in groups))

    def test_just_under_the_target_is_not_rounded_into_meeting_it(self):
        rows = [("fire", 1, "X", None, 1000, 50.0, 50.0, 60.0, 899, 949, 0)]
        _, iso = ar._discipline_metrics(FakeRepository(rows), dict(PARAMS))
        self.assertEqual(iso["fast_percent"], 89.9)
        self.assertFalse(iso["meets_fast"])
        self.assertFalse(iso["meets_slow"])

    def test_the_query_binds_its_agency_lists_and_targets(self):
        query, params = self.repository.calls[0]
        self.assertEqual(params["fast_seconds"], 64)
        self.assertEqual(params["slow_seconds"], 106)
        self.assertEqual(params["ems_agencies"], ["LEASA"])
        for code in ("LCSO", "DPS", "LPD", "MPD", "CPD", "WLPD", "MHPD", "DNR", "WVSP"):
            self.assertIn(code, params["law_agencies"])
        self.assertIn("LIKE 'FC %%'", query)
        self.assertIn("GROUPING SETS ((discipline, dispatcher_key), (discipline))", query)
        # Same population rules as the dispatcher table.
        for rule in ("NOT is_scheduled", "INTERVAL '1 hour'", "excluded_dispatcher_identity"):
            self.assertIn(rule, query)
        # No location or narrative fields.
        for column in ("latitude", "longitude", "city", "narrative", "address"):
            self.assertNotIn(column, query.lower())

    def test_the_empty_snapshot_carries_the_new_blocks(self):
        window = ar.resolve_analytics_window(period="24h")
        empty = ar._empty_overview(window, "none")
        self.assertEqual(empty["dispatcher_disciplines"], [])
        self.assertEqual(empty["iso_fire"]["timed"], 0)
        self.assertEqual(empty["iso_fire"]["fast_seconds"], 64)


class DisciplinePageTests(unittest.TestCase):
    def setUp(self):
        self.base = _route_test_fixture()
        self.overview = self.base.overview
        groups, iso = ar._discipline_metrics(FakeRepository(ROWS), dict(PARAMS))
        self.overview["dispatcher_disciplines"] = groups
        self.overview["iso_fire"] = iso
        self.client = TestClient(app)

    def render(self):
        with patch("app.main.get_analytics_overview", return_value=self.overview), \
                patch("app.main.get_analytics_database_status", return_value=self.base.status):
            return self.client.get("/analytics?period=30d").text

    def test_three_tables_with_print_buttons(self):
        html = self.render()
        for key in ("fire", "ems", "law"):
            self.assertIn(f'id="discipline-{key}-table"', html)
            self.assertIn(f'data-print-analytics="discipline-{key}-table"', html)
        self.assertIn("Dispatcher Metrics by Discipline", html)
        self.assertEqual(html.count("Print / PDF"), 6)
        self.assertIn("SMALL ONE", html)
        self.assertIn("All dispatchers", html)

    def test_the_iso_card_shows_targets_and_the_verdicts(self):
        html = self.render()
        self.assertIn('id="iso-fire-card"', html)
        self.assertIn("Within 64 seconds", html)
        self.assertIn("Within 106 seconds", html)
        self.assertIn("target 90%", html)
        self.assertIn("target 95%", html)
        self.assertIn("meets", html)
        self.assertIn("below target", html)

    def test_the_card_states_its_limits(self):
        html = " ".join(self.render().split())
        self.assertIn("not the 911 answer time", html)
        self.assertIn("counted against the standard window", html)
        self.assertIn("management aid, not an ISO result", html)

    def test_no_fire_calls_says_so_instead_of_a_verdict(self):
        self.overview["iso_fire"] = ar._discipline_metrics(FakeRepository([]), dict(PARAMS))[1]
        html = self.render()
        self.assertIn("No timed fire calls in this period.", html)
        self.assertNotIn("below target", html)

    def test_the_section_is_absent_when_there_is_no_discipline_data(self):
        self.overview["dispatcher_disciplines"] = []
        self.assertNotIn("discipline-metrics", self.render())


if __name__ == "__main__":
    unittest.main()
