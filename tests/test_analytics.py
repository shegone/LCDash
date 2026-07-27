import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.analytics_collector import (
    discover_completed_calls,
    run_analytics_sync,
)
from app.services.analytics_models import normalize_analytics_bundle
from app.services.centralsquare import CentralSquareAPIError


NOW = datetime(2026, 7, 23, 16, 0, 0, tzinfo=timezone.utc)


def completed_call(cfs_number="CFS26-40001"):
    return {
        "CFSNumber": cfs_number,
        "DispatchAgency": {"Abbreviation": "LOGAN911"},
        "PrimaryResponseAgency": {"Abbreviation": "LEASA"},
        "CallTaker": {
            "CallSign": "EOC 6",
            "Username": "dispatcher6",
        },
        "IncidentDateTime": "2026-07-23T14:59:30Z",
        "CallDateTime": "2026-07-23T15:00:00Z",
        "IncidentCode": [
            {
                "IsPrimary": True,
                "IncidentCode": {
                    "Code": "UNCON",
                    "Description": "Unconscious / Syncope",
                },
            }
        ],
        "Priority": {"Level": "15"},
        "Disposition": [
            {
                "Disposition": {
                    "Code": "TRN",
                    "Description": "Transported",
                }
            }
        ],
        "Beat": {"Description": "BEAT 1"},
        "Zone": {"Description": "ZONE A"},
        "Address": {
            "Street": "PRIVATE STREET ADDRESS",
            "City": "LOGAN",
            "Latitude": 37.848765,
            "Longitude": -81.993456,
        },
        "Reporter": {
            "First": "PRIVATE",
            "Last": "CALLER",
            "FromPhoneNumber": "3045551212",
        },
        "CommandLog": [{"Text": "PRIVATE COMMAND NOTE"}],
        "RapidSOS": {"AdditionalInformation": "PRIVATE RAPIDSOS"},
        "IsScheduledCall": False,
    }


def cfs_analytics():
    return {
        "CallTimes": [
            {
                "AgencyORI": "WVLEASA",
                "Dispatched": "2026-07-23T15:01:00Z",
                "Enroute": "2026-07-23T15:02:00Z",
                "OnScene": "2026-07-23T15:08:00Z",
                "Transporting": "2026-07-23T15:25:00Z",
                "ArrivedAt": "2026-07-23T15:40:00Z",
                "Available": "2026-07-23T15:55:00Z",
            }
        ],
        "Unit": [
            {
                "UnitNumber": "MED10",
                "UnitType": {"Description": "Ambulance"},
                "Beat": {"Description": "BEAT 1"},
                "UnitTimes": {
                    "Dispatched": "2026-07-23T15:01:00Z",
                    "Enroute": "2026-07-23T15:02:00Z",
                    "OnScene": "2026-07-23T15:08:00Z",
                    "Transporting": "2026-07-23T15:25:00Z",
                    "ArrivedAt": "2026-07-23T15:40:00Z",
                    "Available": "2026-07-23T15:55:00Z",
                },
            }
        ],
    }


class FakeSearchClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.searches = []

    def search_cfs_core(self, body, skip=0, limit=100):
        self.searches.append((body, skip, limit))
        return self.pages.pop(0)


class FakeCollectorClient:
    def __init__(self, calls, analytics_by_cfs):
        self.calls = calls
        self.analytics_by_cfs = analytics_by_cfs
        self.searches = []
        self.analytics_requests = []

    def search_cfs_core(self, body, skip=0, limit=100):
        self.searches.append((body, skip, limit))
        return {"cfs_cores": list(self.calls)}

    def get_cfs_analytics(self, cfs_number):
        self.analytics_requests.append(cfs_number)
        value = self.analytics_by_cfs[cfs_number]
        if isinstance(value, Exception):
            raise value
        return value


class FakeRepository:
    def __init__(self, previous_sync=None):
        self.previous_sync = previous_sync
        self.initialized = False
        self.started = []
        self.completed = []
        self.bundles = []
        self.sync_timestamps = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def initialize_schema(self):
        self.initialized = True

    def get_sync_timestamp(self):
        return self.previous_sync

    def start_sync_run(self, started_at, window_start, window_end):
        self.started.append((started_at, window_start, window_end))
        return 1

    def complete_sync_run(self, **values):
        self.completed.append(values)

    def upsert_bundle(self, bundle):
        self.bundles.append(bundle)

    def set_sync_timestamp(self, timestamp):
        self.sync_timestamps.append(timestamp)


class AnalyticsModelTests(unittest.TestCase):
    def test_bundle_normalizes_times_station_and_rounded_coordinates(self):
        bundle = normalize_analytics_bundle(
            completed_call(),
            cfs_analytics(),
            roster_by_unit={
                "MED10": {
                    "unit_number": "MED10",
                    "agency": "LEASA",
                    "unit_type": "Medic",
                    "station": "ASTA 10",
                }
            },
            collected_at=NOW,
        )

        self.assertEqual(bundle["call"]["cfs_number"], "CFS26-40001")
        self.assertEqual(bundle["call"]["call_taker"], "EOC 6")
        self.assertEqual(bundle["call"]["latitude"], 37.8488)
        self.assertEqual(bundle["call"]["longitude"], -81.9935)
        self.assertEqual(bundle["call"]["closed_at"].hour, 15)
        self.assertEqual(bundle["call"]["closed_at"].minute, 55)
        self.assertEqual(bundle["call_times"][0]["agency_ori"], "WVLEASA")
        self.assertEqual(bundle["unit_responses"][0]["station"], "ASTA 10")
        self.assertEqual(bundle["units"][0]["agency"], "LEASA")

    def test_bundle_excludes_sensitive_and_raw_cad_content(self):
        bundle = normalize_analytics_bundle(
            completed_call(),
            cfs_analytics(),
            collected_at=NOW,
        )
        serialized = str(bundle).lower()

        for forbidden in (
            "private street address",
            "private caller",
            "3045551212",
            "private command note",
            "private rapidsos",
            "reporter",
            "commandlog",
            "rapidsos",
            "raw",
        ):
            self.assertNotIn(forbidden, serialized)


class AnalyticsCollectorTests(unittest.TestCase):
    def test_completed_search_uses_closed_window_and_paginates(self):
        first_page = [completed_call(f"CFS{index}") for index in range(100)]
        second_page = [completed_call("CFS100")]
        client = FakeSearchClient(
            [
                {"cfs_cores": first_page},
                {"cfs_cores": second_page},
            ]
        )

        calls, truncated = discover_completed_calls(
            client,
            datetime(2026, 7, 22, tzinfo=timezone.utc),
            NOW,
            max_calls=250,
        )

        self.assertEqual(len(calls), 101)
        self.assertFalse(truncated)
        body = client.searches[0][0]
        self.assertFalse(body["CurrentlyActive"])
        self.assertEqual(body["OrderByField"], "Closed")
        self.assertEqual(body["OrderByDirection"], "Ascending")
        self.assertIn("RecordClosedFrom", body)
        self.assertIn("RecordClosedTo", body)
        self.assertEqual(client.searches[1][1], 100)

    @patch("app.services.analytics_collector.build_roster_map")
    def test_successful_sync_upserts_and_advances_high_water_mark(self, roster_mock):
        roster_mock.return_value = {
            "MED10": {"agency": "LEASA", "station": "ASTA 10"}
        }
        repository = FakeRepository()
        client = FakeCollectorClient(
            [completed_call()],
            {"CFS26-40001": cfs_analytics()},
        )

        result = run_analytics_sync(
            repository=repository,
            client=client,
            now=NOW,
            lookback_hours=24,
            request_delay_ms=0,
        )

        self.assertTrue(repository.initialized)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["calls_stored"], 1)
        self.assertEqual(len(repository.bundles), 1)
        self.assertEqual(repository.sync_timestamps, [NOW])
        self.assertEqual(client.analytics_requests, ["CFS26-40001"])

    @patch("app.services.analytics_collector.build_roster_map")
    def test_failed_analytics_does_not_advance_high_water_mark(self, roster_mock):
        roster_mock.return_value = {}
        repository = FakeRepository()
        client = FakeCollectorClient(
            [completed_call()],
            {"CFS26-40001": CentralSquareAPIError("temporary failure")},
        )

        result = run_analytics_sync(
            repository=repository,
            client=client,
            now=NOW,
            request_delay_ms=0,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["analytics_failures"], 1)
        self.assertEqual(repository.sync_timestamps, [])
        self.assertEqual(repository.completed[0]["status"], "partial")

    def test_schema_contains_idempotent_keys_and_metric_views(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "database"
            / "analytics_schema.sql"
        )
        schema = schema_path.read_text(encoding="utf-8").lower()

        self.assertIn("cfs_number text primary key", schema)
        self.assertIn("primary key (cfs_number, unit_number)", schema)
        self.assertIn("unit_response_metrics", schema)
        self.assertIn("call_response_metrics", schema)
        self.assertIn("call_taker text not null default ''", schema)
        self.assertNotIn("caller_name", schema)
        self.assertNotIn("phone_number", schema)
        self.assertNotIn("street_address", schema)


class AnalyticsPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.get_analytics_database_status")
    def test_analytics_page_is_available_before_database_setup(self, status_mock):
        status_mock.return_value = {
            "configured": False,
            "connected": False,
            "calls_stored": 0,
            "unit_responses_stored": 0,
            "last_run": {},
            "message": "PostgreSQL analytics is not configured on this machine.",
        }

        response = self.client.get("/analytics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Operations Analytics", response.text)
        self.assertIn("READY FOR DATABASE SETUP", response.text)
        self.assertIn("Privacy-Minimized", response.text)

    @patch("app.main.get_analytics_database_status")
    def test_status_api_is_sanitized_and_no_store(self, status_mock):
        status_mock.return_value = {
            "configured": True,
            "connected": True,
            "calls_stored": 10,
            "unit_responses_stored": 15,
            "last_run": {"status": "complete"},
        }

        response = self.client.get("/api/analytics/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["calls_stored"], 10)
        self.assertNotIn("database_url", response.text.lower())
        self.assertNotIn("password", response.text.lower())


if __name__ == "__main__":
    unittest.main()
