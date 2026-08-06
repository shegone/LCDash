"""Offline Package 2B tests for profile-driven GIS layer selection."""

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config.settings import settings
from app.core.county_profiles import load_builtin_county_profile
from app.services.gis_reference_service import (
    available_reference_layers,
    get_reference_layer,
)


class CountyProfileGisConfigurationTests(unittest.TestCase):
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

    def _write_synthetic_layers(self, directory: str) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-82.0, 37.8], [-82.1, 37.9]],
                    },
                    "properties": {
                        "LSt_Name": "Synthetic Road",
                        "RoadClass": "Fixture",
                    },
                }
            ],
        }
        Path(directory, "roads.geojson").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        Path(directory, "psap_boundary.geojson").write_text(
            json.dumps({**payload, "features": []}),
            encoding="utf-8",
        )

    def test_default_layer_catalog_preserves_inherited_behavior(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_synthetic_layers(temporary_directory)
            with patch.object(settings, "gis_reference_dir", temporary_directory):
                layer_ids = {
                    item["id"] for item in available_reference_layers()
                }

        self.assertEqual(layer_ids, {"roads", "psap"})
        self.assert_no_network_used()

    def test_profile_allows_only_configured_reviewed_local_layers(self):
        profile = load_builtin_county_profile("logan-synthetic")
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_synthetic_layers(temporary_directory)
            with patch.object(settings, "gis_reference_dir", temporary_directory):
                layer_ids = {
                    item["id"]
                    for item in available_reference_layers(profile)
                }
                roads = get_reference_layer("roads", profile)
                psap = get_reference_layer("psap", profile)

        self.assertEqual(layer_ids, {"roads"})
        self.assertEqual(roads["layer"], "roads")
        self.assertIsNone(psap)
        self.assert_no_network_used()

    def test_managed_map_profile_does_not_authorize_local_reference_files(self):
        profile = load_builtin_county_profile("northstar-fictional")
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._write_synthetic_layers(temporary_directory)
            with patch.object(settings, "gis_reference_dir", temporary_directory):
                catalog = available_reference_layers(profile)
                roads = get_reference_layer("roads", profile)

        self.assertEqual(catalog, [])
        self.assertIsNone(roads)
        self.assert_no_network_used()


if __name__ == "__main__":
    unittest.main()
