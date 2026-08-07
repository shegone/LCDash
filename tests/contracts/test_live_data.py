"""Network-free contracts for verified live CAD/analytics fact computation."""

import unittest
from types import SimpleNamespace

from app.integrations.cloud_ai.live_data import (
    LiveDataIntent,
    LiveDataSource,
    VerifiedFact,
    build_live_data_facts,
    compute_analytics_facts,
    compute_cad_facts,
    detect_live_data_intent,
)


def _cad_state(calls=(), units=()):
    return SimpleNamespace(calls=tuple(calls), units=tuple(units))


class DetectLiveDataIntentTests(unittest.TestCase):
    def test_active_call_question_sets_the_active_calls_flag(self):
        intent = detect_live_data_intent("How many active calls are there right now?")
        self.assertTrue(intent.wants_active_calls)
        self.assertFalse(intent.wants_call_detail)

    def test_cfs_reference_sets_call_detail_and_not_active_calls(self):
        intent = detect_live_data_intent("What is the status of CFS26-01234?")
        self.assertEqual(intent.target_cfs_number, "CFS26-01234")
        self.assertTrue(intent.wants_call_detail)
        self.assertFalse(intent.wants_active_calls)

    def test_unit_status_question_sets_the_unit_flag(self):
        intent = detect_live_data_intent("How many units are available right now?")
        self.assertTrue(intent.wants_unit_status)

    def test_totals_and_response_time_and_busiest_are_independent(self):
        self.assertTrue(detect_live_data_intent("How many total calls today?").wants_totals)
        self.assertTrue(
            detect_live_data_intent("What is the average response time this week?")
            .wants_response_time
        )
        self.assertTrue(
            detect_live_data_intent("What is the busiest station this month?").wants_busiest
        )

    def test_period_defaults_and_recognizes_explicit_windows(self):
        self.assertEqual(detect_live_data_intent("calls today").period, "24h")
        self.assertEqual(detect_live_data_intent("calls this week").period, "7d")
        self.assertEqual(detect_live_data_intent("calls this month").period, "30d")
        self.assertEqual(detect_live_data_intent("unrelated question").period, "24h")

    def test_unrelated_question_sets_no_flags(self):
        intent = detect_live_data_intent("How do I configure CAD window columns?")
        self.assertFalse(intent.wants_cad)
        self.assertFalse(intent.wants_analytics)

    def test_wants_cad_and_wants_analytics_are_derived_correctly(self):
        self.assertTrue(LiveDataIntent(wants_active_calls=True).wants_cad)
        self.assertTrue(LiveDataIntent(wants_unit_status=True).wants_cad)
        self.assertTrue(LiveDataIntent(wants_call_detail=True).wants_cad)
        self.assertFalse(LiveDataIntent().wants_cad)
        self.assertTrue(LiveDataIntent(wants_totals=True).wants_analytics)
        self.assertTrue(LiveDataIntent(wants_response_time=True).wants_analytics)
        self.assertTrue(LiveDataIntent(wants_busiest=True).wants_analytics)
        self.assertFalse(LiveDataIntent().wants_analytics)


class ComputeCadFactsTests(unittest.TestCase):
    def test_returns_nothing_when_intent_does_not_want_cad(self):
        facts, sources = compute_cad_facts(
            LiveDataIntent(), cad_state=_cad_state(), cad_status={"freshness": "fresh"}
        )
        self.assertEqual(facts, ())
        self.assertEqual(sources, ())

    def test_stale_snapshot_yields_an_unavailable_source_and_no_facts(self):
        intent = LiveDataIntent(wants_active_calls=True)
        facts, sources = compute_cad_facts(
            intent,
            cad_state=_cad_state(calls=[{"cfs_number": "CFS26-0001"}]),
            cad_status={"freshness": "stale", "age_seconds": 900},
        )
        self.assertEqual(facts, ())
        self.assertEqual(len(sources), 1)
        self.assertFalse(sources[0].available)

    def test_active_call_count_and_agency_breakdown(self):
        intent = LiveDataIntent(wants_active_calls=True)
        calls = [
            {"cfs_number": "CFS26-0001", "agency": "Fire"},
            {"cfs_number": "CFS26-0002", "agency": "EMS"},
            {"cfs_number": "CFS26-0003", "agency": "Fire"},
        ]
        facts, sources = compute_cad_facts(
            intent,
            cad_state=_cad_state(calls=calls),
            cad_status={"freshness": "fresh", "age_seconds": 5},
        )
        labels = {fact.label: fact.value for fact in facts}
        self.assertEqual(labels["Currently active calls"], "3")
        self.assertIn("EMS: 1", labels["Active calls by agency"])
        self.assertIn("Fire: 2", labels["Active calls by agency"])
        self.assertTrue(sources[0].available)

    def test_unit_status_breakdown(self):
        intent = LiveDataIntent(wants_unit_status=True)
        units = [
            {"unit_number": "MED10", "status": "Available"},
            {"unit_number": "MED11", "status": "Active"},
            {"unit_number": "MED12", "status": "Available"},
        ]
        facts, _ = compute_cad_facts(
            intent,
            cad_state=_cad_state(units=units),
            cad_status={"freshness": "fresh", "age_seconds": 5},
        )
        labels = {fact.label: fact.value for fact in facts}
        self.assertEqual(labels["Units in current roster"], "3")
        self.assertIn("Available: 2", labels["Units by status"])
        self.assertIn("Active: 1", labels["Units by status"])

    def test_call_detail_lookup_finds_a_matching_active_call(self):
        intent = LiveDataIntent(
            wants_call_detail=True, target_cfs_number="CFS26-0002"
        )
        calls = [
            {"cfs_number": "CFS26-0001", "incident_description": "MVA"},
            {
                "cfs_number": "CFS26-0002",
                "incident_description": "Structure fire",
                "priority": "1",
                "status": "Active",
                "location_label": "123 Main St",
                "assigned_units": "ENG1, ENG2",
            },
        ]
        facts, _ = compute_cad_facts(
            intent,
            cad_state=_cad_state(calls=calls),
            cad_status={"freshness": "fresh", "age_seconds": 5},
        )
        labels = {fact.label: fact.value for fact in facts}
        self.assertEqual(labels["CFS26-0002 Incident"], "Structure fire")
        self.assertEqual(labels["CFS26-0002 Priority"], "1")
        self.assertNotIn("CFS26-0001 Incident", labels)

    def test_call_detail_lookup_reports_a_clear_miss_rather_than_silence(self):
        intent = LiveDataIntent(
            wants_call_detail=True, target_cfs_number="CFS26-9999"
        )
        facts, _ = compute_cad_facts(
            intent,
            cad_state=_cad_state(calls=[{"cfs_number": "CFS26-0001"}]),
            cad_status={"freshness": "fresh", "age_seconds": 5},
        )
        self.assertEqual(len(facts), 1)
        self.assertIn("not in the current active-call snapshot", facts[0].value.lower())

    def test_never_reads_forbidden_fields_or_synthesizes_write_capability(self):
        # Only allowlisted CALL_FIELDS/UNIT_FIELDS keys are ever read by this
        # module; verify a raw/forbidden key never leaks into a fact value.
        intent = LiveDataIntent(
            wants_call_detail=True, target_cfs_number="CFS26-0001"
        )
        calls = [
            {
                "cfs_number": "CFS26-0001",
                "incident_description": "Test",
                "caller_phone_number": "555-0100",
                "narrative_raw": "SECRET NARRATIVE",
            }
        ]
        facts, _ = compute_cad_facts(
            intent,
            cad_state=_cad_state(calls=calls),
            cad_status={"freshness": "fresh", "age_seconds": 5},
        )
        rendered = " ".join(f"{fact.label} {fact.value}" for fact in facts)
        self.assertNotIn("555-0100", rendered)
        self.assertNotIn("SECRET NARRATIVE", rendered)


class ComputeAnalyticsFactsTests(unittest.TestCase):
    def test_returns_nothing_when_intent_does_not_want_analytics(self):
        facts, sources = compute_analytics_facts(LiveDataIntent(), overview={})
        self.assertEqual(facts, ())
        self.assertEqual(sources, ())

    def test_unavailable_overview_yields_no_facts(self):
        intent = LiveDataIntent(wants_totals=True)
        facts, sources = compute_analytics_facts(
            intent, overview={"available": False}
        )
        self.assertEqual(facts, ())
        self.assertFalse(sources[0].available)

    def test_totals_response_time_and_busiest_are_extracted(self):
        intent = LiveDataIntent(
            wants_totals=True, wants_response_time=True, wants_busiest=True, period="7d"
        )
        overview = {
            "available": True,
            "latest_data_at": "2026-08-07T00:00:00Z",
            "metrics": {"total_calls": 42, "average_response": "6m 12s"},
            "busiest_stations": [{"label": "Station 3", "count": 12}],
            "busiest_units": [{"unit_number": "ENG1", "count": 9}],
            "incident_types": [{"label": "MVA", "count": 20}],
        }
        facts, sources = compute_analytics_facts(intent, overview=overview)
        labels = {fact.label: fact.value for fact in facts}
        self.assertEqual(labels["Total calls (7d)"], "42")
        self.assertEqual(labels["Average response time (7d)"], "6m 12s")
        self.assertIn("Station 3", labels["Busiest station (7d)"])
        self.assertIn("ENG1", labels["Busiest unit (7d)"])
        self.assertIn("MVA", labels["Top incident type (7d)"])
        self.assertTrue(sources[0].available)


class BuildLiveDataFactsTests(unittest.TestCase):
    def test_pure_analytics_question_never_touches_cad_and_calls_overview_once(self):
        calls_made = []

        def overview_fn(period):
            calls_made.append(period)
            return {
                "available": True,
                "metrics": {"total_calls": 10, "average_response": "5m"},
            }

        facts, sources = build_live_data_facts(
            "How many total calls today?",
            cad_state=_cad_state(),
            cad_status={"freshness": "fresh", "age_seconds": 1},
            analytics_overview_fn=overview_fn,
        )
        self.assertEqual(calls_made, ["24h"])
        self.assertTrue(any(fact.label.startswith("Total calls") for fact in facts))
        self.assertTrue(all(source.kind == "historical" for source in sources))

    def test_pure_cad_question_never_calls_the_analytics_function(self):
        called = []
        facts, sources = build_live_data_facts(
            "How many active calls are there right now?",
            cad_state=_cad_state(calls=[{"cfs_number": "CFS26-0001", "agency": "Fire"}]),
            cad_status={"freshness": "fresh", "age_seconds": 1},
            analytics_overview_fn=lambda period: called.append(period) or {},
        )
        self.assertEqual(called, [])
        self.assertTrue(any(fact.label == "Currently active calls" for fact in facts))

    def test_unrelated_question_returns_no_facts_and_no_sources(self):
        facts, sources = build_live_data_facts(
            "How do I configure CAD window columns?",
            cad_state=_cad_state(),
            cad_status={"freshness": "fresh", "age_seconds": 1},
            analytics_overview_fn=lambda period: {},
        )
        self.assertEqual(facts, ())
        self.assertEqual(sources, ())

    def test_missing_analytics_function_does_not_raise(self):
        facts, sources = build_live_data_facts(
            "What is the average response time this week?",
            cad_state=_cad_state(),
            cad_status={"freshness": "fresh", "age_seconds": 1},
            analytics_overview_fn=None,
        )
        self.assertEqual(facts, ())
        self.assertEqual(sources, ())


if __name__ == "__main__":
    unittest.main()
