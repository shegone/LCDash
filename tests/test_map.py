import unittest
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app, geo_map_tile_client
from app.services.aws_map_tiles import MapTileUnavailable
from app.services.map_service import (
    build_empty_map_snapshot,
    build_map_snapshot,
    location_freshness,
    valid_coordinates,
)
from app.services.gis_reference_service import get_reference_layer
from app.config.settings import settings
from app.services.unit_service import normalize_unit


NOW = datetime(2026, 7, 22, 15, 0, 0, tzinfo=timezone.utc)


def unit_snapshot(calls=None, units=None):
    return {
        "last_updated": NOW.isoformat(),
        "calls": calls or [],
        "roster_connected": True,
        "roster_warning": "",
        "all_units": units or [],
    }


class MapServiceTests(unittest.TestCase):
    def test_coordinate_validation_rejects_invalid_and_zero_pairs(self):
        self.assertTrue(valid_coordinates(37.84, -82.01))
        self.assertFalse(valid_coordinates(0, 0))
        self.assertFalse(valid_coordinates(100, -82.01))
        self.assertFalse(valid_coordinates("bad", -82.01))

    def test_normalize_unit_prefers_unit_location_when_timestamps_tie(self):
        unit = normalize_unit(
            {
                "UnitNumber": "MED10",
                "Status": {"Description": "Available"},
                "LastLocationUpdateTime": "2026-07-22T14:59:30Z",
                "AVL": {
                    "Latitude": 37.80,
                    "Longitude": -82.00,
                    "Speed": 12.5,
                    "Direction": 90.0,
                    "AVLSource": "Mobile",
                },
                "UnitLocation": {
                    "Latitude": 37.81,
                    "Longitude": -82.01,
                },
            }
        )

        self.assertEqual(unit["position"]["source"], "unit_location")
        self.assertEqual(unit["position"]["latitude"], 37.81)

    def test_location_freshness_boundaries(self):
        self.assertEqual(
            location_freshness("2026-07-22T14:58:00Z", now=NOW)["freshness"],
            "fresh",
        )
        self.assertEqual(
            location_freshness("2026-07-22T14:55:00Z", now=NOW)["freshness"],
            "aging",
        )
        self.assertEqual(
            location_freshness("2026-07-22T14:50:00Z", now=NOW)["freshness"],
            "stale",
        )
        self.assertEqual(
            location_freshness("2026-07-22T14:40:00Z", now=NOW)["freshness"],
            "expired",
        )

    def test_map_snapshot_is_sanitized_geojson(self):
        calls = [
            {
                "cfs_number": "CFS26-10001",
                "incident_code": "MED",
                "incident_description": "Medical Emergency",
                "priority": "15",
                "agency": "LEASA",
                "status": "Open",
                "location": "100 Main Street",
                "latitude": 37.84,
                "longitude": -82.01,
                "reporter": {"name": "Private Caller", "phone": "555-1212"},
                "raw": {"CommandLog": [{"Text": "Private note"}]},
            }
        ]
        units = [
            {
                "unit_number": "MED10",
                "agency": "LEASA",
                "unit_type": "Ambulance",
                "status": "Available",
                "station": "Station 1",
                "responder": "Private Responder",
                "position": {
                    "latitude": 37.85,
                    "longitude": -82.02,
                    "source": "avl",
                    "observed_at": "2026-07-22T14:59:30Z",
                },
            }
        ]

        result = build_map_snapshot(unit_snapshot(calls, units), now=NOW)
        serialized = str(result).lower()

        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(result["summary"]["mapped_calls"], 1)
        self.assertEqual(result["summary"]["mapped_units"], 1)
        self.assertEqual(result["features"][0]["geometry"]["coordinates"], [-82.01, 37.84])
        self.assertNotIn("reporter", serialized)
        self.assertNotIn("phone", serialized)
        self.assertNotIn("responder", serialized)
        self.assertNotIn("commandlog", serialized)
        self.assertNotIn("private caller", serialized)

    def test_stale_and_off_duty_units_are_not_mapped(self):
        units = [
            {
                "unit_number": "MED10",
                "status": "Available",
                "position": {
                    "latitude": 37.85,
                    "longitude": -82.02,
                    "source": "avl",
                    "observed_at": "2026-07-22T14:50:00Z",
                },
            },
            {
                "unit_number": "MED20",
                "status": "Off Duty",
                "position": {
                    "latitude": 37.86,
                    "longitude": -82.03,
                    "source": "avl",
                    "observed_at": "2026-07-22T14:59:30Z",
                },
            },
        ]

        result = build_map_snapshot(unit_snapshot(units=units), now=NOW)

        self.assertEqual(result["summary"]["mapped_units"], 0)
        self.assertEqual(result["summary"]["stale_units"], 1)
        self.assertEqual(result["summary"]["excluded_units"], 1)
        self.assertEqual(result["features"], [])


class GISReferenceLayerTests(unittest.TestCase):
    def test_reference_layer_removes_unapproved_source_properties(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original_directory = settings.gis_reference_dir
            settings.gis_reference_dir = temporary_directory
            try:
                Path(temporary_directory, "roads.geojson").write_text(
                    json.dumps(
                        {
                            "type": "FeatureCollection",
                            "features": [
                                {
                                    "type": "Feature",
                                    "geometry": {
                                        "type": "LineString",
                                        "coordinates": [[-82.01, 37.84], [-82.02, 37.85]],
                                    },
                                    "properties": {
                                        "LSt_Name": "Main",
                                        "RoadClass": "Local",
                                        "NGUID": "source-id-not-for-browser",
                                        "ESN_L": "private-routing-data",
                                    },
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                layer = get_reference_layer("roads")
            finally:
                settings.gis_reference_dir = original_directory

        self.assertEqual(layer["type"], "FeatureCollection")
        self.assertEqual(layer["features"][0]["properties"], {"name": "Main", "class": "Local"})
        self.assertNotIn("NGUID", str(layer))
        self.assertNotIn("ESN_L", str(layer))


class MapPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.map_data = build_map_snapshot(
            unit_snapshot(
                calls=[
                    {
                        "cfs_number": "CFS26-10001",
                        "incident_code": "MED",
                        "incident_description": "Medical Emergency",
                        "priority": "15",
                        "agency": "LEASA",
                        "status": "Open",
                        "location": "100 Main Street",
                        "latitude": 37.84,
                        "longitude": -82.01,
                    }
                ]
            ),
            now=NOW,
        )

    @patch("app.main.get_live_map_snapshot")
    def test_map_page_renders_gis_controls(self, snapshot_mock):
        snapshot_mock.return_value = self.map_data

        response = self.client.get("/map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("GIS Map", response.text)
        self.assertIn("operations-map", response.text)
        self.assertIn("show-calls", response.text)
        self.assertIn("lcdash-map.js", response.text)
        self.assertIn('aria-label="GIS map views"', response.text)
        self.assertIn('aria-current="page"', response.text)
        self.assertIn('aria-labelledby="map-filter-heading"', response.text)
        self.assertIn('for="map-agency-filter"', response.text)
        self.assertIn('for="map-priority-filter"', response.text)
        self.assertIn('for="map-status-filter"', response.text)
        self.assertIn('id="map-filter-status"', response.text)
        self.assertIn('aria-live="polite"', response.text)
        self.assertIn('role="region"', response.text)
        self.assertIn('tabindex="0"', response.text)
        self.assertIn(":focus-visible", response.text)
        self.assertIn("min-height: 44px", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.css", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.js", response.text)
        self.assertNotIn("unpkg.com", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    @patch("app.main.get_live_map_snapshot")
    def test_map_api_is_no_store_and_preserves_minimized_contract(self, snapshot_mock):
        snapshot_mock.return_value = self.map_data

        response = self.client.get("/api/operations/map")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(payload["type"], "FeatureCollection")
        self.assertNotIn("raw", str(payload).lower())

    def test_unavailable_reference_layer_returns_not_found(self):
        response = self.client.get("/api/operations/map/reference/roads")

        self.assertEqual(response.status_code, 404)

    def test_tile_styles_report_unavailable_outside_cloud_mode(self):
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get("/api/operations/map/tile-styles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"available": False, "styles": []})

    def test_tile_styles_report_satellite_in_cloud_mode(self):
        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/api/operations/map/tile-styles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"available": True, "styles": ["satellite"]})

    def test_tile_route_is_not_found_outside_cloud_mode(self):
        with patch.object(settings, "deployment_mode", "on-prem"):
            response = self.client.get("/api/operations/map/tiles/satellite/1/0/0")

        self.assertEqual(response.status_code, 404)

    @patch("app.main.fetch_map_tile")
    def test_tile_route_returns_signed_tile_bytes_in_cloud_mode(self, fetch_mock):
        fetch_mock.return_value = (b"\x89PNG-bytes", "image/png")

        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/api/operations/map/tiles/satellite/4/2/1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\x89PNG-bytes")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "private, max-age=86400")
        fetch_mock.assert_called_once_with(
            geo_map_tile_client, style="satellite", z=4, x=2, y=1
        )

    @patch("app.main.fetch_map_tile")
    def test_tile_route_rejects_unapproved_style_without_leaking_detail(
        self, fetch_mock
    ):
        fetch_mock.side_effect = MapTileUnavailable("map_tile_style_not_allowed")

        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/api/operations/map/tiles/vector.basemap/1/0/0")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Map tile unavailable.")

    @patch("app.main.fetch_map_tile")
    def test_tile_route_maps_out_of_range_to_bad_request(self, fetch_mock):
        fetch_mock.side_effect = MapTileUnavailable("map_tile_zoom_out_of_range")

        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/api/operations/map/tiles/satellite/99/0/0")

        self.assertEqual(response.status_code, 400)

    @patch("app.main.fetch_map_tile")
    def test_tile_route_maps_provider_failure_to_bad_gateway(self, fetch_mock):
        fetch_mock.side_effect = MapTileUnavailable("map_tile_request_failed")

        with patch.object(settings, "deployment_mode", "synthetic-disconnected"):
            response = self.client.get("/api/operations/map/tiles/satellite/1/0/0")

        self.assertEqual(response.status_code, 502)

    @patch("app.main.get_live_map_snapshot")
    def test_disconnected_map_page_keeps_empty_map_shell(self, snapshot_mock):
        snapshot_mock.return_value = build_empty_map_snapshot("CAD unavailable")

        response = self.client.get("/map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Operational map source unavailable", response.text)
        self.assertIn("operations-map", response.text)
        self.assertIn('role="status"', response.text)

    @patch("app.main.get_live_map_snapshot")
    def test_sidebar_links_to_gis_map(self, snapshot_mock):
        snapshot_mock.return_value = self.map_data
        response = self.client.get("/map")
        self.assertIn('href="/map"', response.text)

    @patch("app.main.get_call_detail")
    def test_incident_map_safely_encodes_cad_text(self, call_detail_mock):
        call_detail_mock.return_value = {
            "cfs_number": "CFS26-10001",
            "incident_code": "MED",
            "incident_description": "Test </script><script>alert(1)</script>",
            "priority": "15",
            "status": "Open",
            "location": "100 Main Street",
            "latitude": 37.84,
            "longitude": -82.01,
            "agency": "LEASA",
            "call_taker": "",
            "call_datetime": "2026-07-22T14:00:00Z",
            "assigned_units": [],
            "command_logs": [],
            "reporter": {},
            "raw": {},
        }

        response = self.client.get("/calls/CFS26-10001")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("</script><script>alert(1)</script>", response.text)
        self.assertIn("incident-map-data", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.css", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.js", response.text)
        self.assertNotIn("unpkg.com", response.text)


if __name__ == "__main__":
    unittest.main()
