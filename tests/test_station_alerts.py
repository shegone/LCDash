import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import station_alert_service
from app.services.station_alert_service import (
    build_empty_station_alert_snapshot,
    build_station_alert_snapshot,
)


NOW = datetime(2026, 7, 22, 16, 0, 0, tzinfo=timezone.utc)


def station_snapshot():
    return {
        "last_updated": NOW.isoformat(),
        "roster_connected": True,
        "roster_warning": "",
        "calls": [
            {
                "cfs_number": "CFS26-30001",
                "incident_code": "STRUCT",
                "incident_description": "Structure Fire",
                "priority": "10",
                "location": "100 MAIN STREET, LOGAN",
                "call_datetime": "2026-07-22T15:55:00Z",
                "status": "Open",
                "latitude": 37.848,
                "longitude": -81.994,
                "reporter": {"name": "Private Caller", "phone": "3045551212"},
                "command_logs": [{"text": "Private command note"}],
                "raw": {"RapidSOS": {"secret": "Private RapidSOS data"}},
            },
            {
                "cfs_number": "CFS26-30002",
                "incident_code": "MED",
                "incident_description": "Medical Emergency",
                "priority": "15",
                "location": "200 SECOND AVENUE",
                "call_datetime": "2026-07-22T15:56:00Z",
                "status": "Open",
                "latitude": 37.85,
                "longitude": -81.99,
            },
        ],
        "all_units": [
            {
                "unit_number": "ENG1",
                "station": "STA 100",
                "agency": "FIRE",
                "unit_type": "Engine",
                "status": "Enroute",
                "roster_status": "Enroute",
                "cfs_number": "CFS26-30001",
                "last_assigned_time": "2026-07-22T15:57:00Z",
                "assignments": [
                    {
                        "unit_number": "ENG1",
                        "cfs_number": "CFS26-30001",
                        "dispatch_time": "2026-07-22T15:57:00Z",
                        "incident_code": "STRUCT",
                    }
                ],
            },
            {
                "unit_number": "TNK1",
                "station": "sta 100",
                "agency": "FIRE",
                "unit_type": "Tanker",
                "status": "Assigned",
                "roster_status": "Assigned",
                "cfs_number": "CFS26-30001",
                "last_assigned_time": "2026-07-22T15:57:10Z",
                "assignments": [
                    {
                        "unit_number": "TNK1",
                        "cfs_number": "CFS26-30001",
                        "dispatch_time": "2026-07-22T15:57:10Z",
                    }
                ],
            },
            {
                "unit_number": "MED1",
                "station": "STA 200",
                "agency": "EMS",
                "unit_type": "Ambulance",
                "status": "On Scene",
                "roster_status": "On Scene",
                "cfs_number": "CFS26-30002",
                "last_assigned_time": "2026-07-22T15:58:00Z",
                "assignments": [
                    {
                        "unit_number": "MED1",
                        "cfs_number": "CFS26-30002",
                        "dispatch_time": "2026-07-22T15:58:00Z",
                    }
                ],
            },
            {
                "unit_number": "ENG2",
                "station": "STA 100",
                "agency": "FIRE",
                "unit_type": "Engine",
                "status": "Available",
                "roster_status": "Available",
                "cfs_number": "",
                "assignments": [],
            },
        ],
    }


class StationAlertServiceTests(unittest.TestCase):
    def tearDown(self):
        station_alert_service._client_cache.update(
            {
                "client": None,
                "created_at": None,
                "retry_after": None,
                "error": "",
            }
        )

    def test_catalog_is_unique_case_insensitive_and_sorted(self):
        result = build_station_alert_snapshot(station_snapshot(), "STA 100")

        self.assertEqual(
            [station["name"].lower() for station in result["stations"]],
            ["sta 100", "sta 200"],
        )
        self.assertEqual(result["stations"][0]["unit_count"], 3)

    def test_selected_station_groups_units_into_one_alert_per_cfs(self):
        result = build_station_alert_snapshot(station_snapshot(), "STA 100")

        self.assertEqual(len(result["station_units"]), 3)
        self.assertEqual(result["station_units"][0]["unit_number"], "ENG2")
        self.assertEqual(result["station_units"][0]["status"], "Available")
        self.assertEqual(len(result["alerts"]), 1)
        alert = result["alerts"][0]
        self.assertEqual(alert["cfs_number"], "CFS26-30001")
        self.assertEqual(alert["unit_numbers"], ["ENG1", "TNK1"])
        self.assertEqual(alert["dispatch_datetime"], "2026-07-22T15:57:10Z")
        self.assertNotIn("MED1", str(result))

    def test_station_alert_payload_excludes_private_call_fields(self):
        result = build_station_alert_snapshot(station_snapshot(), "STA 100")
        serialized = str(result).lower()

        for forbidden in (
            "private caller",
            "3045551212",
            "private command note",
            "private rapidsos data",
            "reporter",
            "command_logs",
            "raw",
        ):
            self.assertNotIn(forbidden, serialized)

    @patch("app.services.station_alert_service.CentralSquareClient")
    def test_station_polling_reuses_authenticated_client(self, client_class):
        first = station_alert_service._cached_client(NOW)
        second = station_alert_service._cached_client(NOW)

        self.assertIs(first, second)
        client_class.assert_called_once_with()


class StationAlertPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.alert_data = build_station_alert_snapshot(station_snapshot(), "STA 100")

    @patch("app.main.get_live_station_alert_snapshot")
    def test_page_has_selector_sound_test_overlay_and_no_store(self, snapshot_mock):
        snapshot_mock.return_value = self.alert_data

        response = self.client.get("/station-alerts?station=STA%20100")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Fire & EMS Station Alerts", response.text)
        self.assertIn("station-selector", response.text)
        self.assertIn("Enable Loud Alerts", response.text)
        self.assertIn("Test Two-Tone Alert", response.text)
        self.assertIn("dispatch-alert-overlay", response.text)
        self.assertIn("lcdash-station-alerts.js", response.text)
        self.assertIn("STA 100", response.text)

    @patch("app.main.get_live_station_alert_snapshot")
    def test_api_returns_sanitized_station_snapshot_and_no_store(self, snapshot_mock):
        snapshot_mock.return_value = self.alert_data

        response = self.client.get("/api/operations/station-alerts?station=STA%20100")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["selected_station"], "STA 100")
        self.assertNotIn("Private Caller", response.text)
        self.assertNotIn("3045551212", response.text)

    @patch("app.main.get_live_station_alert_snapshot")
    def test_disconnected_page_remains_available(self, snapshot_mock):
        snapshot_mock.return_value = build_empty_station_alert_snapshot(
            "STA 100",
            "CAD unavailable",
        )

        response = self.client.get("/station-alerts?station=STA%20100")

        self.assertEqual(response.status_code, 200)
        self.assertIn("DISCONNECTED", response.text)
        self.assertIn("Station Alerts", response.text)


if __name__ == "__main__":
    unittest.main()
