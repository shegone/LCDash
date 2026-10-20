"""Domestic-call report service and CLI, against a fake CentralSquare search.

All data is synthetic. What matters: only searches are made, paging is right,
each call is counted once, classification follows the incident code text, past
months are reused but the current month is always re-read, and nothing but
aggregates ever leaves the module.
"""

import contextlib
import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services import domestic_call_report_service as svc
from scripts import domestic_call_percentage as cli

TZ = ZoneInfo("America/New_York")
NO_SLEEP = lambda _seconds: None  # noqa: E731


def call(cfs, code, description, *, extra=None):
    rows = [{"IsPrimary": True, "IncidentCode": {"Code": code, "Description": description}}]
    rows += extra or []
    return {
        "CFSNumber": cfs,
        "IncidentCode": rows,
        # Present so a test can prove none of it leaks into any output.
        "Reporter": {"FreeformFullName": "Jane Caller"},
        "Address": {"Street": "141 Stratton Street"},
    }


class FakeSearch:
    """Serves calls by RecordCreatedFrom month, honouring skip/limit."""

    def __init__(self, by_month):
        self.by_month = by_month
        self.requests = []

    def __call__(self, body, skip=0, limit=100):
        self.requests.append((body, skip, limit))
        rows = self.by_month.get(body["RecordCreatedFrom"][:7], [])
        return {"cfs_cores": rows[skip: skip + limit]}


def d(y, m, day=1):
    return datetime(y, m, day, tzinfo=TZ)


class WindowTests(unittest.TestCase):
    def test_windows_are_calendar_months_and_cover_the_range_exactly(self):
        windows = list(svc.month_windows(d(2026, 1), d(2026, 10, 21)))
        self.assertEqual(len(windows), 10)
        self.assertEqual(windows[0][0], d(2026, 1))
        self.assertEqual(windows[0][1], d(2026, 2))
        self.assertEqual(windows[-1][1], d(2026, 10, 21))
        for (_, a_end), (b_start, _) in zip(windows, windows[1:]):
            self.assertEqual(a_end, b_start)

    def test_year_boundary(self):
        windows = list(svc.month_windows(d(2025, 12), d(2026, 2)))
        self.assertEqual([w[0].strftime("%Y-%m") for w in windows], ["2025-12", "2026-01"])

    def test_year_to_date_window_runs_january_first_through_end_of_today(self):
        start, end = svc.year_to_date_window(d(2026, 10, 20))
        self.assertEqual(start, d(2026, 1, 1))
        self.assertEqual(end, d(2026, 10, 21))


class ClassificationTests(unittest.TestCase):
    def test_match_is_on_code_abbreviation_or_description_case_insensitively(self):
        row = {"IncidentCode": {"Code": "DV1", "Abbreviation": "DOM", "Description": "Domestic Violence"}}
        for term in ("domestic", "dom", "dv1", "VIOLENCE"):
            with self.subTest(term=term):
                self.assertTrue(svc.matches(row, (term,)))
        self.assertFalse(svc.matches(row, ("burglary",)))
        self.assertFalse(svc.matches(None, ("domestic",)))

    def test_primary_row_is_preferred_over_first(self):
        c = {"IncidentCode": [
            {"IsPrimary": False, "IncidentCode": {"Code": "A", "Description": "Welfare Check"}},
            {"IsPrimary": True, "IncidentCode": {"Code": "B", "Description": "Domestic Disturbance"}},
        ]}
        self.assertEqual(svc.code_label(svc.primary_incident_row(c)), "B - Domestic Disturbance")

    def test_no_incident_code_is_counted_but_never_domestic(self):
        self.assertIsNone(svc.primary_incident_row({"CFSNumber": "X"}))
        self.assertEqual(svc.code_label(None), "(no incident code)")

    def test_page_calls_tolerates_every_known_envelope_and_junk(self):
        for key in ("cfs_cores", "CFSCore", "CFSCoreReadMultiple", "calls", "items"):
            self.assertEqual(len(svc.page_calls({key: [{"a": 1}, "junk"]})), 1)
        self.assertEqual(svc.page_calls(None), [])
        self.assertEqual(svc.page_calls("<html>"), [])


class SummaryTests(unittest.TestCase):
    START, END = d(2026, 1), d(2026, 3)
    NOW = d(2026, 10, 20)

    def run_summary(self, search, **kwargs):
        return svc.summarize(search, self.START, self.END, now=self.NOW, sleep=NO_SLEEP, **kwargs)

    def test_percentages_are_per_month_and_overall(self):
        search = FakeSearch({
            "2026-01": [call("C1", "DV", "Domestic Violence"), call("C2", "MED", "Medical Alarm"),
                        call("C3", "DD", "Domestic Disturbance"), call("C4", "TH", "Theft")],
            "2026-02": [call("C5", "DV", "Domestic Violence"), call("C6", "MED", "Medical Alarm")],
        })
        result = self.run_summary(search)
        self.assertEqual([m["total_calls"] for m in result["months"]], [4, 2])
        self.assertEqual([m["domestic_calls"] for m in result["months"]], [2, 1])
        self.assertEqual([m["percent_domestic"] for m in result["months"]], [50.0, 50.0])
        self.assertEqual((result["total_calls"], result["domestic_calls"], result["percent_domestic"]), (6, 3, 50.0))
        self.assertEqual(
            {m["code"]: m["calls"] for m in result["matched_codes"]},
            {"DV - Domestic Violence": 2, "DD - Domestic Disturbance": 1},
        )
        self.assertEqual(result["through"], "2026-02-28")
        self.assertEqual(result["months"][0]["month_label"], "January 2026")

    def test_not_counted_codes_are_listed_so_the_definition_can_be_checked(self):
        search = FakeSearch({"2026-01": [call("C1", "FT", "Family Trouble"), call("C2", "FT", "Family Trouble"),
                                         call("C3", "DV", "Domestic Violence")]})
        result = self.run_summary(search)
        self.assertEqual(result["other_codes"], [{"code": "FT - Family Trouble", "calls": 2}])
        self.assertEqual(result["other_code_kinds"], 1)

    def test_pages_through_more_than_one_page_and_counts_each_call_once(self):
        calls = [call(f"C{i}", "MED", "Medical Alarm") for i in range(250)]
        calls += [calls[0], calls[1]]  # the same CFS returned twice
        search = FakeSearch({"2026-01": calls})
        result = svc.summarize(search, self.START, d(2026, 2), now=self.NOW, sleep=NO_SLEEP)
        self.assertEqual(result["total_calls"], 250)
        self.assertEqual([r[1] for r in search.requests], [0, 100, 200])
        self.assertTrue(all(r[2] == 100 for r in search.requests))

    def test_any_incident_counts_a_domestic_secondary_code_primary_does_not(self):
        secondary = {"IsPrimary": False, "IncidentCode": {"Code": "DV", "Description": "Domestic Violence"}}
        search = FakeSearch({"2026-01": [call("C1", "ASLT", "Assault", extra=[secondary])]})
        one_month = (self.START, d(2026, 2))
        self.assertEqual(svc.summarize(search, *one_month, now=self.NOW, sleep=NO_SLEEP)["domestic_calls"], 0)
        self.assertEqual(
            svc.summarize(search, *one_month, any_incident=True, now=self.NOW, sleep=NO_SLEEP)["domestic_calls"], 1)

    def test_empty_range_has_no_percentage_rather_than_a_division_error(self):
        result = self.run_summary(FakeSearch({}))
        self.assertEqual(result["total_calls"], 0)
        self.assertIsNone(result["percent_domestic"])

    def test_the_page_cap_stops_a_runaway_loop(self):
        def endless(body, skip=0, limit=100):
            return {"cfs_cores": [call(f"{skip}-{i}", "MED", "Medical Alarm") for i in range(100)]}

        with self.assertRaises(svc.DomesticReportError):
            svc.summarize(endless, self.START, d(2026, 2), now=self.NOW, sleep=NO_SLEEP)

    def test_only_searches_are_made_and_no_call_data_reaches_the_result(self):
        search = FakeSearch({"2026-01": [call("C1", "DV", "Domestic Violence")]})
        result = svc.summarize(search, self.START, d(2026, 2), now=self.NOW, sleep=NO_SLEEP)
        self.assertTrue(all(
            set(r[0]) == {"RecordCreatedFrom", "RecordCreatedTo", "OrderByField", "OrderByDirection"}
            for r in search.requests))
        blob = json.dumps(result)
        for private in ("Jane Caller", "141 Stratton", '"C1"', "CFS"):
            self.assertNotIn(private, blob)

    def test_a_pause_separates_pages_but_not_the_last_one(self):
        pauses = []
        search = FakeSearch({"2026-01": [call(f"C{i}", "MED", "x") for i in range(150)]})
        svc.summarize(search, self.START, d(2026, 2), now=self.NOW, sleep=pauses.append)
        self.assertEqual(pauses, [svc.PAGE_DELAY_SECONDS])


class CacheTests(unittest.TestCase):
    START, END = d(2026, 1), d(2026, 3)

    def test_finished_months_are_reused_and_the_current_month_never_is(self):
        search = FakeSearch({"2026-01": [call("C1", "DV", "Domestic Violence")],
                             "2026-02": [call("C2", "DV", "Domestic Violence")]})
        cache = {}
        # "Now" is inside February: January is finished, February is not.
        now = d(2026, 2, 15)
        first = svc.summarize(search, self.START, self.END, now=now, month_cache=cache, cache_key="t", sleep=NO_SLEEP)
        self.assertEqual(len(search.requests), 2)
        self.assertEqual([m["reused"] for m in first["months"]], [False, False])
        self.assertEqual([m["in_progress"] for m in first["months"]], [False, True])

        search.requests.clear()
        second = svc.summarize(search, self.START, self.END, now=now, month_cache=cache, cache_key="t", sleep=NO_SLEEP)
        self.assertEqual([r[0]["RecordCreatedFrom"][:7] for r in search.requests], ["2026-02"])
        self.assertEqual([m["reused"] for m in second["months"]], [True, False])
        self.assertEqual(second["total_calls"], first["total_calls"])
        self.assertEqual(second["quality"]["months_reused_from_earlier_run"], 1)

    def test_cache_is_per_tenant_and_per_definition_and_expires(self):
        search = FakeSearch({"2026-01": [call("C1", "DV", "Domestic Violence")]})
        cache = {}
        now = d(2026, 10, 20)
        one = (self.START, d(2026, 2))
        svc.summarize(search, *one, now=now, month_cache=cache, cache_key="a", sleep=NO_SLEEP)
        n = len(search.requests)
        svc.summarize(search, *one, now=now, month_cache=cache, cache_key="b", sleep=NO_SLEEP)
        self.assertEqual(len(search.requests), n + 1)  # other tenant: read again
        svc.summarize(search, *one, terms=("family",), now=now, month_cache=cache, cache_key="a", sleep=NO_SLEEP)
        self.assertEqual(len(search.requests), n + 2)  # other definition: read again
        with patch.object(svc, "PAST_MONTH_CACHE_SECONDS", -1):
            svc.summarize(search, *one, now=now, month_cache=cache, cache_key="a", sleep=NO_SLEEP)
        self.assertEqual(len(search.requests), n + 3)  # expired: read again


class CommandLineTests(unittest.TestCase):
    NOW = datetime(2026, 10, 20, 9, 0, tzinfo=TZ)

    def _run(self, argv, search):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch.object(svc, "PAGE_DELAY_SECONDS", 0):
            code = cli.main(argv, search=search, now=self.NOW)
        return code, out.getvalue()

    def test_default_range_is_january_first_through_today(self):
        search = FakeSearch({"2026-01": [call("C1", "DV", "Domestic Violence")]})
        code, out = self._run([], search)
        self.assertEqual(code, 0)
        self.assertEqual(search.requests[0][0]["RecordCreatedFrom"][:10], "2026-01-01")
        self.assertEqual(search.requests[-1][0]["RecordCreatedTo"][:10], "2026-10-21")
        self.assertIn("TOTAL", out)
        self.assertIn("DV - Domestic Violence", out)
        self.assertIn("month in progress", out)

    def test_json_output_and_list_codes(self):
        search = FakeSearch({"2026-01": [call("C1", "MED", "Medical Alarm")]})
        _, out = self._run(["--json", "--from", "2026-01-01", "--to", "2026-02-01"], search)
        self.assertEqual(json.loads(out)["other_codes"], [{"code": "MED - Medical Alarm", "calls": 1}])
        _, text = self._run(["--list-codes", "--from", "2026-01-01", "--to", "2026-02-01"], search)
        self.assertIn("NOT counted", text)

    def test_backwards_range_is_refused(self):
        code, _ = self._run(["--from", "2026-05-01", "--to", "2026-04-01"], FakeSearch({}))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
