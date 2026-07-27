import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.cad_service import simplify_call
from app.services.centralsquare import CentralSquareAPIError
from app.services.operations_service import build_dashboard_stats


class ActiveCallsPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.snapshot = {
            "last_updated": "2026-07-21T15:00:00+00:00",
            "calls": [
                {
                    "cfs_number": "CFS26-10001",
                    "incident_code": "TEST",
                    "incident_description": "Test Incident",
                    "location": "100 Main Street, Logan",
                    "priority": "15",
                    "agency": "LEASA",
                    "units": "MED10",
                    "status": "On Scene",
                    "call_taker": "EOC 1",
                    "call_datetime": "2026-07-21T14:30:00Z",
                    "assigned_units": [{"unit_number": "MED10"}],
                }
            ],
            "dashboard_stats": {
                "active_calls": 1,
                "assigned_units": 1,
                "high_priority_calls": 1,
                "agency_summary": [{"agency": "LEASA", "count": 1}],
            },
            "unit_rows": [],
            "unit_stats": {},
        }

    @patch("app.main.get_live_operations_snapshot")
    def test_active_calls_page_renders_normalized_call(self, snapshot_mock):
        snapshot_mock.return_value = self.snapshot

        response = self.client.get("/active-calls")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Active Calls", response.text)
        self.assertIn("CFS26-10001", response.text)
        self.assertIn("Test Incident", response.text)
        self.assertIn("MED10", response.text)
        self.assertIn('data-agency="leasa"', response.text)
        self.assertIn('href="/calls/CFS26-10001"', response.text)

    @patch("app.main.get_live_operations_snapshot")
    def test_active_calls_page_has_disconnected_state(self, snapshot_mock):
        snapshot_mock.side_effect = CentralSquareAPIError("test outage")

        response = self.client.get("/active-calls")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Disconnected", response.text)
        self.assertIn("active-call list could not be loaded", response.text)
        self.assertNotIn("test outage", response.text)

    def test_sidebar_links_to_active_calls_page(self):
        with patch("app.main.get_live_operations_snapshot", return_value=self.snapshot):
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/active-calls"', response.text)

    @patch("app.main.get_live_operations_snapshot")
    def test_dashboard_uses_background_snapshot_refresh(self, snapshot_mock):
        snapshot_mock.return_value = self.snapshot

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/js/lcdash-dashboard.js', response.text)
        self.assertIn('id="dashboard-freshness"', response.text)
        self.assertIn('id="realtime-status-value"', response.text)
        self.assertIn('id="realtime-last-event"', response.text)
        self.assertIn('id="active-calls-value"', response.text)
        self.assertIn('id="incident-feed-content"', response.text)
        self.assertIn('data-cfs-number="CFS26-10001"', response.text)
        self.assertIn('rel="noopener"', response.text)
        self.assertNotIn("window.location.reload()", response.text)

    @patch("app.main.get_live_operations_snapshot")
    def test_operations_snapshot_is_no_store(self, snapshot_mock):
        snapshot_mock.return_value = self.snapshot

        response = self.client.get("/api/operations/snapshot")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()["connected"])

    def test_dashboard_refresh_script_keeps_last_known_data(self):
        response = self.client.get("/static/js/lcdash-dashboard.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn('"/api/operations/snapshot"', response.text)
        self.assertIn("AbortController", response.text)
        self.assertIn(
            'LCDashTime.updateElementFromCadTime("last-updated"',
            response.text,
        )
        self.assertIn("function callFingerprint(call)", response.text)
        self.assertIn("existing?.dataset.callFingerprint === fingerprint", response.text)
        self.assertIn("grid.insertBefore(column, cursor)", response.text)
        self.assertIn("last known data", response.text)
        self.assertIn('"STREAMING"', response.text)
        self.assertIn('"30S BACKUP"', response.text)
        self.assertIn("handleRealtimeEvent", response.text)
        self.assertNotIn("window.location.reload", response.text)

    @patch("app.main.get_live_operations_snapshot")
    def test_active_calls_page_distinguishes_connected_empty_state(self, snapshot_mock):
        empty_snapshot = {
            **self.snapshot,
            "calls": [],
            "dashboard_stats": {
                **self.snapshot["dashboard_stats"],
                "active_calls": 0,
                "assigned_units": 0,
                "high_priority_calls": 0,
                "agency_summary": [],
            },
        }
        snapshot_mock.return_value = empty_snapshot

        response = self.client.get("/active-calls")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No active calls returned from CAD", response.text)
        self.assertNotIn("active-call list could not be loaded", response.text)

    @patch("app.main.get_live_operations_snapshot")
    def test_active_calls_page_escapes_cad_text(self, snapshot_mock):
        unsafe_snapshot = {
            **self.snapshot,
            "calls": [
                {
                    **self.snapshot["calls"][0],
                    "incident_description": '<script>alert("test")</script>',
                }
            ],
        }
        snapshot_mock.return_value = unsafe_snapshot

        response = self.client.get("/active-calls")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<script>alert("test")</script>', response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_call_status_uses_latest_timestamped_command_log_status(self):
        raw_call = {
            "CFSNumber": "CFS26-10002",
            "CommandLog": [
                {
                    "Timestamp": "2026-07-21T14:00:00Z",
                    "Status": {"Description": "Assigned"},
                },
                {
                    "Timestamp": "2026-07-21T14:10:00Z",
                    "Status": {"Description": "On Scene"},
                },
            ],
        }

        simplified = simplify_call(raw_call)

        self.assertEqual(simplified["status"], "On Scene")

    def test_high_priority_count_excludes_zero_and_unknown_priorities(self):
        stats = build_dashboard_stats(
            [
                {"priority": "0", "assigned_units": []},
                {"priority": "15", "assigned_units": []},
                {"priority": "unknown", "assigned_units": []},
            ]
        )

        self.assertEqual(stats["high_priority_calls"], 1)

    def test_dashboard_stats_include_on_scene_calls_and_oldest_call(self):
        stats = build_dashboard_stats(
            [
                {
                    "call_datetime": "2026-07-21T14:30:00Z",
                    "priority": "20",
                    "status": "On Scene",
                    "assigned_units": [
                        {"unit_number": "MED10", "status": "On Scene"},
                        {"unit_number": "MED20", "status": "Enroute"},
                    ],
                },
                {
                    "call_datetime": "2026-07-21T13:00:00Z",
                    "priority": "30",
                    "status": "Enroute",
                    "assigned_units": [
                        {"unit_number": "MED10", "status": "On Scene"},
                    ],
                },
            ]
        )

        self.assertEqual(stats["assigned_units"], 2)
        self.assertEqual(stats["on_scene_calls"], 1)
        self.assertEqual(
            stats["oldest_call_datetime"],
            "2026-07-21T13:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
