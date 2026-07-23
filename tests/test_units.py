import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.centralsquare import CentralSquareAPIError
from app.services.operations_service import (
    build_full_unit_roster,
    get_live_unit_snapshot,
)
from app.services.unit_service import (
    classify_unit,
    get_all_units,
    normalize_unit,
)


def raw_unit(unit_number: str, status: str, agency: str = "LEASA", **overrides):
    unit = {
        "UnitNumber": unit_number,
        "UnitType": {"Description": "Ambulance"},
        "Agency": {"Abbreviation": agency},
        "Responder": {"FullDescription": "Test Responder"},
        "Status": {
            "Description": status,
            "Abbreviation": status[:3].upper(),
        },
        "LastStatusTime": "2026-07-21T14:00:00Z",
        "Station": {"Description": "Station 1"},
        "IncidentInformation": {},
    }
    unit.update(overrides)
    return unit


class FakeUnitClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def search_units(self, search_body, skip=0, limit=100):
        self.calls.append((search_body, skip, limit))
        return self.pages.pop(0)


class UnitServiceTests(unittest.TestCase):
    def test_get_all_units_reads_pages_and_deduplicates_unit_numbers(self):
        first_page = [raw_unit(f"UNIT{index}", "Off Duty") for index in range(100)]
        second_page = [
            raw_unit("unit0", "Available"),
            raw_unit("MED10", "Available"),
        ]
        client = FakeUnitClient(
            [
                {"Units": first_page, "next": "next-page"},
                {"Units": second_page, "next": "unused-next-page"},
            ]
        )

        units = get_all_units(client=client)

        self.assertEqual(len(units), 101)
        self.assertEqual(client.calls, [({}, 0, 100), ({}, 100, 100)])
        deduplicated_unit = next(unit for unit in units if unit["unit_number"].upper() == "UNIT0")
        self.assertEqual(deduplicated_unit["status"], "Available")

    def test_normalize_unit_uses_verified_unit_read_fields(self):
        unit = normalize_unit(
            raw_unit(
                "MED10",
                "Available",
                IncidentInformation={
                    "CFSNumber": "CFS26-10001",
                    "IncidentCode": {"Code": "MED"},
                    "LocationDetails": "100 Main Street",
                },
            )
        )

        self.assertEqual(unit["unit_number"], "MED10")
        self.assertEqual(unit["status"], "Available")
        self.assertEqual(unit["agency"], "LEASA")
        self.assertEqual(unit["cfs_number"], "CFS26-10001")
        self.assertEqual(unit["incident_code"], "MED")

    def test_status_groups_are_conservative(self):
        available = normalize_unit(raw_unit("MED10", "Available"))
        unavailable = normalize_unit(raw_unit("MED20", "Off Duty"))
        active = normalize_unit(
            raw_unit(
                "MED30",
                "Assigned",
                IncidentInformation={"CFSNumber": "CFS26-10001"},
            )
        )
        unknown = normalize_unit(raw_unit("MED40", "Local Custom Status"))

        self.assertEqual(classify_unit(available), "available")
        self.assertEqual(classify_unit(unavailable), "unavailable")
        self.assertEqual(classify_unit(active), "active")
        self.assertEqual(classify_unit(unknown), "unknown")

    def test_active_assignment_overrides_off_duty_roster_group(self):
        roster = [normalize_unit(raw_unit("MED10", "Off Duty"))]
        active_rows = [
            {
                "unit_number": "med10",
                "status": "On Scene",
                "status_group": "On Scene",
                "priority": "15",
                "cfs_number": "CFS26-10001",
                "incident_description": "Test Incident",
            }
        ]

        groups = build_full_unit_roster(roster, active_rows)

        self.assertEqual(len(groups["active_units"]), 1)
        self.assertEqual(len(groups["operational_units"]), 0)
        self.assertEqual(len(groups["unavailable_units"]), 0)
        self.assertEqual(groups["active_units"][0]["status"], "On Scene")
        self.assertEqual(groups["active_units"][0]["roster_status"], "Off Duty")

    def test_active_status_without_cfs_uses_nonlinked_operational_group(self):
        roster = [normalize_unit(raw_unit("MED10", "Arrived At"))]

        groups = build_full_unit_roster(roster, [])

        self.assertEqual(len(groups["active_units"]), 0)
        self.assertEqual(len(groups["operational_units"]), 1)
        self.assertEqual(groups["roster_stats"]["active_units"], 1)

    @patch("app.services.operations_service.get_all_units")
    @patch("app.services.operations_service.get_active_calls")
    @patch("app.services.operations_service.CentralSquareClient")
    def test_roster_failure_keeps_active_call_units(
        self,
        client_mock,
        active_calls_mock,
        get_all_units_mock,
    ):
        client_mock.return_value = object()
        active_calls_mock.return_value = [
            {
                "priority": "15",
                "call_datetime": "2026-07-21T14:00:00Z",
                "agency": "LEASA",
                "assigned_units": [
                    {
                        "unit_number": "MED10",
                        "status": "Enroute",
                    }
                ],
                "cfs_number": "CFS26-10001",
                "incident_description": "Test Incident",
            }
        ]
        get_all_units_mock.side_effect = CentralSquareAPIError("roster unavailable")

        snapshot = get_live_unit_snapshot()

        self.assertFalse(snapshot["roster_connected"])
        self.assertEqual(len(snapshot["active_units"]), 1)
        self.assertEqual(snapshot["active_units"][0]["unit_number"], "MED10")


class UnitsPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.snapshot = {
            "last_updated": "2026-07-21T15:00:00+00:00",
            "calls": [],
            "roster_connected": True,
            "roster_warning": "",
            "active_units": [],
            "operational_units": [],
            "available_units": [
                {
                    "unit_number": "MED10",
                    "status": "Available",
                    "agency": "LEASA",
                    "unit_type": "Ambulance",
                    "station": "Station 1",
                    "responder": "",
                }
            ],
            "unavailable_units": [
                {
                    "unit_number": "MED20",
                    "status": "Off Duty",
                    "agency": "LEASA",
                    "unit_type": "Ambulance",
                    "station": "",
                }
            ],
            "unknown_units": [],
            "all_units": [],
            "active_stats": {
                "total_units": 0,
                "assigned_units": 0,
                "enroute_units": 0,
                "on_scene_units": 0,
                "transporting_units": 0,
                "cleared_units": 0,
                "unknown_units": 0,
                "status_summary": [],
                "agency_summary": [],
            },
            "roster_stats": {
                "total_units": 2,
                "active_units": 0,
                "available_units": 1,
                "unavailable_units": 1,
                "unknown_units": 0,
                "status_summary": [
                    {"status": "Available", "count": 1},
                    {"status": "Off Duty", "count": 1},
                ],
            },
        }

    @patch("app.main.get_live_unit_snapshot")
    def test_units_page_renders_available_and_off_duty_groups(self, snapshot_mock):
        snapshot_mock.return_value = self.snapshot

        response = self.client.get("/units")

        self.assertEqual(response.status_code, 200)
        self.assertIn("ON DUTY / AVAILABLE", response.text)
        self.assertIn("OFF DUTY / OUT OF SERVICE", response.text)
        self.assertIn("MED10", response.text)
        self.assertIn("MED20", response.text)

    @patch("app.main.get_live_unit_snapshot")
    def test_units_api_preserves_active_units_contract(self, snapshot_mock):
        active_unit = {
            "unit_number": "MED30",
            "status": "Enroute",
            "status_group": "Enroute",
            "position": {
                "latitude": 37.84,
                "longitude": -82.01,
                "observed_at": "2026-07-21T15:00:00Z",
            },
        }
        snapshot_mock.return_value = {
            **self.snapshot,
            "active_units": [active_unit],
            "all_units": [
                active_unit,
                *self.snapshot["available_units"],
                *self.snapshot["unavailable_units"],
            ],
            "active_stats": {
                **self.snapshot["active_stats"],
                "total_units": 1,
                "enroute_units": 1,
            },
            "roster_stats": {
                **self.snapshot["roster_stats"],
                "total_units": 3,
                "active_units": 1,
            },
        }

        response = self.client.get("/api/operations/units")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            payload["units"],
            [
                {
                    "unit_number": "MED30",
                    "status": "Enroute",
                    "status_group": "Enroute",
                }
            ],
        )
        self.assertEqual(payload["stats"]["total_units"], 1)
        self.assertEqual(len(payload["all_units"]), 3)
        self.assertEqual(payload["roster_stats"]["total_units"], 3)
        self.assertNotIn("position", str(payload).lower())
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
