"""Offline Package 2B tests for profile-driven unit status configuration."""

import socket
import unittest
from unittest.mock import patch

from app.core.county_profiles import load_builtin_county_profile
from app.services.unit_service import get_all_units, normalize_unit


class SyntheticUnitClient:
    def __init__(self, status_abbreviation: str):
        self.status_abbreviation = status_abbreviation

    def search_units(self, query, *, skip, limit):
        return {
            "Units": [
                {
                    "UnitNumber": "SYN-UNIT-1",
                    "Status": {
                        "Description": "Vendor-specific ready state",
                        "Abbreviation": self.status_abbreviation,
                    },
                    "Agency": {"Abbreviation": "SYN"},
                }
            ],
            "next": None,
        }


class CountyProfileUnitConfigurationTests(unittest.TestCase):
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

    def test_default_normalization_preserves_inherited_status_behavior(self):
        normalized = normalize_unit(
            {
                "UnitNumber": "SYN-UNIT-1",
                "Status": {
                    "Description": "Vendor-specific ready state",
                    "Abbreviation": "AVL",
                },
            }
        )

        self.assertEqual(normalized["status"], "Vendor-specific ready state")
        self.assert_no_network_used()

    def test_logan_profile_translates_its_synthetic_status_code(self):
        profile = load_builtin_county_profile("logan-synthetic")

        units = get_all_units(
            client=SyntheticUnitClient("AVL"),
            county_profile=profile,
        )

        self.assertEqual(units[0]["status"], "Available")
        self.assert_no_network_used()

    def test_two_counties_use_configuration_without_shared_logic_fork(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")

        logan_units = get_all_units(
            client=SyntheticUnitClient("ENR"),
            county_profile=logan,
        )
        northstar_units = get_all_units(
            client=SyntheticUnitClient("RESPONDING"),
            county_profile=northstar,
        )

        self.assertEqual(logan_units[0]["status"], "Enroute")
        self.assertEqual(northstar_units[0]["status"], "Enroute")
        self.assert_no_network_used()


if __name__ == "__main__":
    unittest.main()
