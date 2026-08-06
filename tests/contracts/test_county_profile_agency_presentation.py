"""Offline Package 2B tests for profile-driven agency presentation."""

import socket
import sys
import types
import unittest
from dataclasses import replace
from datetime import timezone
from importlib.util import find_spec
from unittest.mock import patch

from app.core.county_presentation import agency_display_label
from app.core.county_profiles import load_builtin_county_profile

if "psycopg" not in sys.modules and find_spec("psycopg") is None:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.Error = type("Error", (Exception,), {})
    psycopg_stub.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("database access blocked")
    )
    sys.modules["psycopg"] = psycopg_stub

from app.services.analytics_reporting import get_analytics_overview


class SyntheticAnalyticsRepository:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def initialize_schema(self):
        return None

    def fetchone(self, query, params):
        if "MAX(source_collected_at)" in query:
            return (10, 0, None, None, None, 0, None, 0)
        if "calls_with_call_taker" in query:
            return (0, 0, None, None, 0, 0)
        return (0, 0, 0)

    def fetchall(self, query, params):
        if "AS agency" in query and "LIMIT 8" in query:
            return [("SLD", 10)]
        return []


class CountyProfileAgencyPresentationTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network access blocked"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network access blocked"),
            ),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_network_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_default_labels_preserve_inherited_behavior(self):
        self.assertEqual(
            agency_display_label("LCEOC"),
            "911 Center / Administrative",
        )
        self.assertEqual(agency_display_label("LEASA"), "LEASA")
        self.assert_no_network_used()

    def test_two_counties_resolve_only_their_configured_agencies(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")

        self.assertEqual(
            agency_display_label("SLD", logan),
            "Synthetic Logan Dispatch",
        )
        self.assertEqual(agency_display_label("SLD", northstar), "SLD")
        self.assertEqual(
            agency_display_label("NFC", northstar),
            "Northstar Fictional Communications",
        )
        self.assertEqual(agency_display_label("NFC", logan), "NFC")
        self.assert_no_network_used()

    def test_ambiguous_in_memory_profile_fails_closed(self):
        profile = load_builtin_county_profile("logan-synthetic")
        duplicate = dict(profile.agencies[0])
        duplicate["id"] = "duplicate-synthetic-agency"
        ambiguous_profile = replace(
            profile,
            agencies=(*profile.agencies, duplicate),
        )

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            agency_display_label("SLD", ambiguous_profile)
        self.assert_no_network_used()

    @patch(
        "app.services.analytics_reporting.AnalyticsRepository",
        return_value=SyntheticAnalyticsRepository(),
    )
    @patch(
        "app.services.analytics_reporting.analytics_database_is_configured",
        return_value=True,
    )
    def test_public_overview_uses_profile_for_agency_mix(
        self,
        configured_mock,
        repository_mock,
    ):
        profile = load_builtin_county_profile("logan-synthetic")

        overview = get_analytics_overview(
            period="24h",
            county_profile=profile,
        )

        self.assertEqual(
            overview["agency_mix"],
            [
                {
                    "label": "Synthetic Logan Dispatch",
                    "count": 10,
                    "percent": 100.0,
                }
            ],
        )
        configured_mock.assert_called_once_with()
        repository_mock.assert_called_once_with()
        self.assert_no_network_used()


if __name__ == "__main__":
    unittest.main()
