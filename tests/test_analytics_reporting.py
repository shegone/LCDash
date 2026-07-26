import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app
from app.services.analytics_reporting import (
    AnalyticsRangeError,
    get_analytics_overview,
    resolve_analytics_window,
)


class AnalyticsWindowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 26, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    def test_preset_window_uses_requested_duration(self):
        window = resolve_analytics_window(period="7d", now=self.now)

        self.assertEqual(window.key, "7d")
        self.assertEqual(window.label, "Last 7 days")
        self.assertEqual((window.end_at - window.start_at).days, 7)

    def test_custom_range_is_inclusive_of_end_date(self):
        window = resolve_analytics_window(
            start="2026-07-01",
            end="2026-07-03",
            now=self.now,
        )

        self.assertEqual(window.key, "custom")
        self.assertEqual(window.start_date, "2026-07-01")
        self.assertEqual(window.end_date, "2026-07-03")
        self.assertEqual(
            window.end_at.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
            "2026-07-04",
        )

    def test_custom_range_requires_both_dates(self):
        with self.assertRaises(AnalyticsRangeError):
            resolve_analytics_window(start="2026-07-01", now=self.now)

    def test_custom_range_rejects_future_end_date(self):
        with self.assertRaises(AnalyticsRangeError):
            resolve_analytics_window(
                start="2026-07-01",
                end="2026-07-27",
                now=self.now,
            )

    @patch("app.services.analytics_reporting.analytics_database_is_configured")
    def test_unconfigured_database_returns_safe_empty_snapshot(self, configured_mock):
        configured_mock.return_value = False

        result = get_analytics_overview(period="30d")

        self.assertFalse(result["available"])
        self.assertEqual(result["metrics"]["total_calls"], 0)
        self.assertEqual(result["station_discipline"], [])
        self.assertEqual(result["station_discipline_groups"], [])
        self.assertEqual(
            result["station_discipline_quality"]["coverage_percent"],
            0,
        )
        self.assertNotIn("password", str(result).lower())
        self.assertNotIn("database_url", str(result).lower())


class AnalyticsOverviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.status = {
            "configured": True,
            "connected": True,
            "calls_stored": 42,
            "unit_responses_stored": 80,
            "last_run": {"status": "complete"},
        }
        self.overview = {
            "available": True,
            "message": "",
            "period_key": "30d",
            "period_label": "Last 30 days",
            "start_date": "2026-06-27",
            "end_date": "2026-07-26",
            "generated_at": "2026-07-26T12:00:00-04:00",
            "latest_data_at": "2026-07-26T15:55:00+00:00",
            "metrics": {
                "total_calls": 42,
                "unit_responses": 80,
                "average_processing": "1:32",
                "average_response": "8:14",
                "median_response": "7:48",
                "response_coverage_percent": 91,
                "scheduled_calls": 2,
            },
            "daily_volume": [{"date": "2026-07-26", "label": "Jul 26", "count": 42}],
            "hourly_volume": [{"hour": 0, "label": "12 AM", "count": 2}],
            "agency_mix": [{"label": "LEASA", "count": 42, "percent": 100.0}],
            "incident_types": [{"label": "Medical", "count": 20, "percent": 47.6}],
            "busiest_units": [
                {
                    "unit_number": "MED10",
                    "station": "Station 1",
                    "responses": 18,
                    "average_response": "7:50",
                }
            ],
            "busiest_stations": [{"station": "Station 1", "calls": 18}],
            "station_discipline": [
                {
                    "station": "Station 1",
                    "law": 4,
                    "ems": 12,
                    "fire": 2,
                    "total": 18,
                    "discipline": "EMS",
                }
            ],
            "station_discipline_groups": [
                {
                    "discipline": "EMS",
                    "stations": [
                        {
                            "station": "Station 1",
                            "law": 4,
                            "ems": 12,
                            "fire": 2,
                            "total": 18,
                            "discipline": "EMS",
                        }
                    ],
                }
            ],
            "station_discipline_quality": {
                "classified_responses": 80,
                "total_responses": 80,
                "coverage_percent": 100,
                "unassigned_station_responses": 0,
            },
        }

    @patch("app.main.get_analytics_overview")
    @patch("app.main.get_analytics_database_status")
    def test_analytics_page_renders_real_reporting_sections(
        self,
        status_mock,
        overview_mock,
    ):
        status_mock.return_value = self.status
        overview_mock.return_value = self.overview

        response = self.client.get("/analytics?period=30d")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Supervisor Operations Review", response.text)
        self.assertIn("Calls by Day", response.text)
        self.assertIn("Top Incident Types", response.text)
        self.assertIn("Busiest Units", response.text)
        self.assertIn("Calls by Station: Law, EMS, and Fire", response.text)
        self.assertIn("station-discipline-chart", response.text)
        self.assertIn("100% discipline coverage", response.text)
        self.assertIn('class="station-group-row"', response.text)
        self.assertIn("discipline-ems", response.text)
        self.assertIn('data-print-analytics="busiest-units-table"', response.text)
        self.assertIn('data-print-analytics="station-discipline-table"', response.text)
        self.assertEqual(response.text.count("Print / PDF"), 2)
        self.assertIn("MED10", response.text)
        self.assertIn("/static/js/chart.umd.min.js", response.text)
        self.assertNotIn("cdn.jsdelivr.net/npm/chart.js", response.text)
        self.assertIn("/static/js/lcdash-analytics.js", response.text)
        overview_mock.assert_called_once_with(period="30d", start="", end="")

    @patch("app.main.get_analytics_overview")
    def test_analytics_overview_api_is_no_store(self, overview_mock):
        overview_mock.return_value = self.overview

        response = self.client.get("/api/analytics/overview?period=7d")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["metrics"]["total_calls"], 42)
        overview_mock.assert_called_once_with(period="7d", start="", end="")

    def test_analytics_overview_api_rejects_invalid_custom_range(self):
        response = self.client.get("/api/analytics/overview?start=2026-07-20")

        self.assertEqual(response.status_code, 400)
        self.assertIn("both a start date", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
