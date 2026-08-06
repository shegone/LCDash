"""Offline county-specific heatmap geometry contracts."""

import socket
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
    validate_heatmap_configuration,
)
from app.services.heatmap_service import build_heatmap_snapshot


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def raw_call(cfs_number, latitude, longitude):
    return {
        "CFSNumber": cfs_number,
        "CallDateTime": "2026-08-04T11:00:00Z",
        "Address": {
            "Latitude": latitude,
            "Longitude": longitude,
            "Street": "Synthetic location",
        },
        "PrimaryResponseAgency": {"Abbreviation": "SYN"},
    }


class ForbiddenCalls:
    def __iter__(self):
        raise AssertionError("raw calls iterated before geometry validation")


class CountyProfileHeatmapConfigurationTests(unittest.TestCase):
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

    def assert_no_network(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_logan_configuration_is_exactly_legacy_geometry(self):
        logan = load_builtin_county_profile("logan-synthetic")
        calls = [raw_call("LOGAN-1", 37.845, -82.015)]

        legacy = build_heatmap_snapshot(calls, hours=2, now=NOW)
        configured = build_heatmap_snapshot(
            calls,
            hours=2,
            now=NOW,
            county_profile=logan,
        )

        self.assertEqual(configured, legacy)
        self.assertEqual(
            dict(logan.heatmap_configuration),
            {
                "min_latitude": 37.4,
                "max_latitude": 38.4,
                "min_longitude": -82.6,
                "max_longitude": -81.4,
                "grid_degrees": 0.01,
            },
        )
        with self.assertRaises(TypeError):
            logan.heatmap_configuration["grid_degrees"] = 1.0
        self.assert_no_network()

    def test_northstar_uses_distinct_synthetic_bounds_and_grid(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")
        calls = [raw_call("NORTHSTAR-1", 45.0, -94.0)]

        legacy = build_heatmap_snapshot(calls, hours=2, now=NOW)
        logan_result = build_heatmap_snapshot(
            calls,
            hours=2,
            now=NOW,
            county_profile=logan,
        )
        northstar_result = build_heatmap_snapshot(
            calls,
            hours=2,
            now=NOW,
            county_profile=northstar,
        )

        self.assertEqual(legacy["summary"]["mapped_calls"], 0)
        self.assertEqual(logan_result["summary"]["mapped_calls"], 0)
        self.assertEqual(northstar_result["summary"]["mapped_calls"], 1)
        self.assertEqual(
            northstar_result["features"][0]["geometry"]["coordinates"],
            [-93.99, 45.01],
        )
        self.assert_no_network()

    def test_malformed_in_memory_geometry_fails_before_raw_call_iteration(self):
        profile = load_builtin_county_profile("logan-synthetic")
        invalid_configurations = (
            {**profile.heatmap_configuration, "grid_degrees": True},
            {**profile.heatmap_configuration, "grid_degrees": float("nan")},
            {**profile.heatmap_configuration, "max_latitude": float("inf")},
            {**profile.heatmap_configuration, "min_latitude": -91},
            {**profile.heatmap_configuration, "max_longitude": 181},
            {**profile.heatmap_configuration, "min_latitude": 38.4},
            {**profile.heatmap_configuration, "grid_degrees": 0},
            {**profile.heatmap_configuration, "grid_degrees": 0.0001},
            {**profile.heatmap_configuration, "grid_degrees": 2.0},
            {
                "min_latitude": 0.0,
                "max_latitude": 90.0,
                "min_longitude": 0.0,
                "max_longitude": 180.0,
                "grid_degrees": 0.001,
            },
        )
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                malformed = replace(
                    profile,
                    heatmap_configuration=configuration,
                )
                with self.assertRaises(CountyProfileValidationError):
                    build_heatmap_snapshot(
                        ForbiddenCalls(),
                        hours=2,
                        now=NOW,
                        county_profile=malformed,
                    )
        self.assert_no_network()

    def test_missing_unknown_and_wrong_shape_configuration_fail_closed(self):
        valid = dict(
            load_builtin_county_profile("logan-synthetic").heatmap_configuration
        )
        missing = dict(valid)
        missing.pop("grid_degrees")
        unknown = {**valid, "radius_miles": 1}
        for configuration in (missing, unknown, [], None):
            with self.subTest(configuration=configuration):
                with self.assertRaises(CountyProfileValidationError):
                    validate_heatmap_configuration(configuration)
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
