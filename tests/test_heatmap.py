import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.heatmap_service import (
    _get_historical_calls,
    build_empty_heatmap_snapshot,
    build_heatmap_snapshot,
    get_live_heatmap_snapshot,
    validate_heatmap_hours,
)
from app.services.centralsquare import CentralSquareClient


NOW = datetime(2026, 7, 22, 15, 0, 0, tzinfo=timezone.utc)


def raw_call(
    cfs_number: str,
    call_datetime: str = "2026-07-22T14:00:00Z",
    latitude=37.845,
    longitude=-82.015,
    agency="LEASA",
    **overrides,
):
    call = {
        "CFSNumber": cfs_number,
        "CallDateTime": call_datetime,
        "Address": {
            "Latitude": latitude,
            "Longitude": longitude,
            "Street": "Private exact address",
        },
        "PrimaryResponseAgency": {"Abbreviation": agency},
        "Reporter": {
            "FreeformFullName": "Private Caller",
            "FromPhoneNumber": "3045551212",
        },
        "CommandLog": [{"Text": "Private command note"}],
        "RapidSOS": {"AdditionalInformation": "Private RapidSOS data"},
    }
    call.update(overrides)
    return call


class FakeHistoricalClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def search_cfs_core(self, body, skip=0, limit=100):
        self.calls.append((body, skip, limit))
        return self.pages.pop(0)


class HeatmapServiceTests(unittest.TestCase):
    def test_allowed_hours_are_strict(self):
        for hours in (2, 8, 12, 24):
            self.assertEqual(validate_heatmap_hours(hours), hours)
        for hours in (-1, 0, 1, 6, 48):
            with self.assertRaises(ValueError):
                validate_heatmap_hours(hours)

    def test_client_search_remains_compatible_and_clamps_pagination(self):
        client = CentralSquareClient.__new__(CentralSquareClient)
        client.post = Mock(return_value={"cfs_cores": []})

        result = client.search_cfs_core({"CurrentlyActive": True}, skip=-1, limit=500)

        self.assertEqual(result, {"cfs_cores": []})
        self.assertEqual(client.post.call_args.kwargs["params"], {"skip": 0, "limit": 100})
        self.assertEqual(client.post.call_args.kwargs["json"], {"CurrentlyActive": True})

    def test_pagination_stops_on_short_page_despite_misleading_next(self):
        client = FakeHistoricalClient(
            [{"cfs_cores": [raw_call("CFS1")], "next": "misleading-next"}]
        )

        calls = _get_historical_calls(client, {"RecordCreatedFrom": "start"})

        self.assertEqual(len(calls), 1)
        self.assertEqual(client.calls, [({"RecordCreatedFrom": "start"}, 0, 100)])

    def test_pagination_deduplicates_cfs_numbers(self):
        first_page = [raw_call(f"CFS{index}") for index in range(100)]
        updated_duplicate = raw_call("CFS0", agency="FIRE")
        client = FakeHistoricalClient(
            [
                {"cfs_cores": first_page, "next": "next"},
                {"cfs_cores": [updated_duplicate], "next": "misleading"},
            ]
        )

        calls = _get_historical_calls(client, {})

        self.assertEqual(len(calls), 100)
        self.assertEqual(calls[0]["PrimaryResponseAgency"]["Abbreviation"], "FIRE")
        self.assertEqual(client.calls, [({}, 0, 100), ({}, 100, 100)])

    def test_live_search_uses_exact_utc_created_window(self):
        client = FakeHistoricalClient([{"cfs_cores": [], "next": "misleading"}])

        result = get_live_heatmap_snapshot(8, client=client, now=NOW)

        body = client.calls[0][0]
        self.assertEqual(body["RecordCreatedFrom"], "2026-07-22T07:00:00+00:00")
        self.assertEqual(body["RecordCreatedTo"], "2026-07-22T15:00:00+00:00")
        self.assertEqual(body["OrderByField"], "Created")
        self.assertEqual(body["OrderByDirection"], "Descending")
        self.assertEqual(result["window"]["hours"], 8)

    def test_grid_aggregation_includes_isolated_locations(self):
        calls = [
            raw_call("CFS1", latitude=37.841, longitude=-82.011, agency="LEASA"),
            raw_call("CFS2", latitude=37.842, longitude=-82.012, agency="LEASA"),
            raw_call("CFS3", latitude=37.843, longitude=-82.013, agency="FIRE"),
            raw_call("CFS4", latitude=37.901, longitude=-82.101, agency="LAW"),
        ]

        result = build_heatmap_snapshot(calls, hours=2, now=NOW)

        self.assertEqual(result["summary"]["within_window_calls"], 4)
        self.assertEqual(result["summary"]["mapped_calls"], 4)
        self.assertEqual(result["summary"]["displayed_calls"], 4)
        self.assertEqual(result["summary"]["displayed_cells"], 2)
        self.assertEqual(result["summary"]["not_mapped_calls"], 0)
        self.assertEqual(len(result["features"]), 2)
        counts = sorted(feature["properties"]["count"] for feature in result["features"])
        self.assertEqual(counts, [1, 3])
        grouped = next(feature for feature in result["features"] if feature["properties"]["count"] == 3)
        self.assertEqual(grouped["properties"]["agency_counts"], {"LEASA": 2, "FIRE": 1})

    def test_mixed_agency_calls_are_available_for_blending_and_filtering(self):
        calls = [
            raw_call("LE1", agency="LEASA"),
            raw_call("FIRE1", agency="FIRE"),
        ]

        result = build_heatmap_snapshot(calls, hours=2, now=NOW)

        self.assertEqual(result["summary"]["displayed_cells"], 1)
        self.assertEqual(result["features"][0]["properties"]["count"], 2)
        self.assertEqual(
            result["features"][0]["properties"]["agency_counts"],
            {"LEASA": 1, "FIRE": 1},
        )
        self.assertEqual(result["agencies"], ["FIRE", "LEASA"])

    def test_time_boundaries_are_inclusive_and_bad_locations_are_counted(self):
        calls = [
            raw_call("START", call_datetime="2026-07-22T13:00:00Z"),
            raw_call("END", call_datetime="2026-07-22T15:00:00Z"),
            raw_call("OLD", call_datetime="2026-07-22T12:59:59Z"),
            raw_call("BADTIME", call_datetime="not-a-time"),
            raw_call("BADCOORD", latitude=0, longitude=0),
            raw_call("OUTLIER", latitude=40.0, longitude=-80.0),
        ]

        result = build_heatmap_snapshot(calls, hours=2, now=NOW)

        self.assertEqual(result["summary"]["within_window_calls"], 4)
        self.assertEqual(result["summary"]["outside_window_calls"], 1)
        self.assertEqual(result["summary"]["invalid_time_calls"], 1)
        self.assertEqual(result["summary"]["unmapped_calls"], 1)
        self.assertEqual(result["summary"]["outside_extent_calls"], 1)

    def test_payload_contains_no_individual_incident_data(self):
        result = build_heatmap_snapshot(
            [raw_call("CFS1"), raw_call("CFS2")],
            hours=2,
            now=NOW,
        )
        serialized = str(result).lower()

        for forbidden in (
            "cfs1",
            "cfs2",
            "private exact address",
            "private caller",
            "3045551212",
            "private command note",
            "private rapidsos data",
            "reporter",
            "commandlog",
            "raw",
            "detail_url",
        ):
            self.assertNotIn(forbidden, serialized)


class HeatmapPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.heatmap_data = build_heatmap_snapshot(
            [raw_call("CFS1"), raw_call("CFS2")],
            hours=8,
            now=NOW,
        )

    @patch("app.main.get_live_heatmap_snapshot")
    def test_page_has_time_controls_tabs_and_no_store(self, snapshot_mock):
        snapshot_mock.return_value = self.heatmap_data

        response = self.client.get("/map/heatmap?hours=8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Historical Call Heat Map", response.text)
        for hours in (2, 8, 12, 24):
            self.assertIn(f"hours={hours}", response.text)
        self.assertIn("Live Operations", response.text)
        self.assertIn("Recent Activity", response.text)
        self.assertIn("lcdash-heatmap.js", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.css", response.text)
        self.assertIn("/static/vendor/leaflet/leaflet.js", response.text)
        self.assertIn("/static/vendor/leaflet-heat/leaflet-heat.js", response.text)
        self.assertNotIn("unpkg.com", response.text)
        self.assertIn("Blended Heat", response.text)
        self.assertIn("Individual Calls", response.text)
        self.assertIn('aria-label="GIS map views"', response.text)
        self.assertIn('aria-labelledby="heatmap-window-heading"', response.text)
        self.assertIn('aria-labelledby="heatmap-controls-heading"', response.text)
        self.assertIn('for="heatmap-agency-filter"', response.text)
        self.assertIn('id="heatmap-filter-status"', response.text)
        self.assertIn('aria-live="polite"', response.text)
        self.assertIn('aria-pressed="true"', response.text)
        self.assertIn('role="region"', response.text)
        self.assertIn('tabindex="0"', response.text)
        self.assertIn(":focus-visible", response.text)
        self.assertIn("min-height: 44px", response.text)
        self.assertNotIn("CFS1", response.text)
        self.assertNotIn("Private exact address", response.text)

    def test_invalid_hours_return_400_without_api_call(self):
        with patch("app.main.get_live_heatmap_snapshot") as snapshot_mock:
            response = self.client.get("/api/operations/map/heatmap?hours=6")

        self.assertEqual(response.status_code, 400)
        snapshot_mock.assert_not_called()

    @patch("app.main.get_live_heatmap_snapshot")
    def test_heatmap_api_is_aggregate_only_and_no_store(self, snapshot_mock):
        snapshot_mock.return_value = self.heatmap_data

        response = self.client.get("/api/operations/map/heatmap?hours=8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["type"], "FeatureCollection")
        self.assertNotIn("cfs", response.text.lower())
        self.assertNotIn("address", response.text.lower())

    @patch("app.main.get_live_heatmap_snapshot")
    def test_disconnected_page_has_clear_state(self, snapshot_mock):
        snapshot_mock.return_value = build_empty_heatmap_snapshot(8)

        response = self.client.get("/map/heatmap?hours=8")

        self.assertIn("Historical activity source unavailable", response.text)
        self.assertIn("No approved imported historical dataset", response.text)
        self.assertIn('role="status"', response.text)

    @patch("app.main.get_live_map_snapshot")
    def test_live_map_links_to_recent_activity(self, map_snapshot_mock):
        map_snapshot_mock.return_value = {
            "type": "FeatureCollection",
            "generated_at": NOW.isoformat(),
            "cad_connected": True,
            "roster_connected": True,
            "roster_warning": "",
            "summary": {
                "total_calls": 0,
                "mapped_calls": 0,
                "unmapped_calls": 0,
                "total_units": 0,
                "mapped_units": 0,
                "unmapped_units": 0,
                "stale_units": 0,
                "excluded_units": 0,
            },
            "features": [],
        }

        response = self.client.get("/map")
        self.assertIn("/map/heatmap?hours=8", response.text)


if __name__ == "__main__":
    unittest.main()
