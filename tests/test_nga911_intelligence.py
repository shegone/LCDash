import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.nga911_intelligence_service import (
    MockNGA911IntelligenceProvider,
    NGA911ProviderError,
    get_nga911_counties,
    get_nga911_county_detail,
    get_nga911_intelligence_overview,
    get_nga911_logan_event,
    get_nga911_logan_operations,
)


class NGA911IntelligenceServiceTests(unittest.TestCase):
    def test_mock_contract_is_explicitly_synthetic(self):
        overview = MockNGA911IntelligenceProvider().get_overview()

        self.assertEqual(overview["schema_version"], "nga911-intelligence.v1")
        self.assertEqual(overview["provider_mode"], "mock")
        self.assertTrue(overview["synthetic_data"])
        self.assertIn("SYNTHETIC DATA", overview["environment_label"])
        self.assertGreater(len(overview["counties"]), 0)
        self.assertGreater(len(overview["intelligence"]), 0)
        self.assertGreater(len(overview["service_events"]), 0)

    def test_default_provider_returns_normalized_overview(self):
        overview = get_nga911_intelligence_overview()

        self.assertIn("connection", overview)
        self.assertIn("summary", overview)
        self.assertIn("capabilities", overview)

    def test_county_contract_is_isolated_and_synthetic(self):
        detail = get_nga911_county_detail("demo-logan")

        self.assertEqual(detail["schema_version"], "nga911-county-intelligence.v1")
        self.assertTrue(detail["synthetic_data"])
        self.assertEqual(detail["county"]["id"], "demo-logan")
        self.assertGreater(len(detail["psaps"]), 0)
        self.assertGreater(len(detail["call_paths"]), 0)
        self.assertEqual(len(detail["session_trend"]), 24)
        self.assertEqual(len(get_nga911_counties()), 4)

    def test_unknown_county_returns_none(self):
        self.assertIsNone(get_nga911_county_detail("not-a-county"))

    def test_director_operations_contract_has_paths_consoles_and_history(self):
        operations = get_nga911_logan_operations(14)

        self.assertEqual(operations["schema_version"], "nga911-director-operations.v1")
        self.assertTrue(operations["synthetic_data"])
        self.assertEqual(len(operations["paths"]), 5)
        self.assertEqual(len(operations["consoles"]), 6)
        self.assertEqual(len(operations["daily_history"]), 14)
        self.assertGreaterEqual(len(operations["events"]), 8)
        self.assertEqual({path["name"] for path in operations["paths"]}, {
            "Verizon Fiber", "Optimum Fiber", "FirstNet Cradlepoint",
            "Verizon Cradlepoint", "Starlink",
        })
        self.assertIn("Dylan Maples", {console["dispatcher"] for console in operations["consoles"]})
        self.assertIn("synthetic", operations["identity_note"])

    def test_history_query_is_limited_and_events_are_retrievable(self):
        self.assertEqual(len(get_nga911_logan_operations(7)["daily_history"]), 7)
        self.assertEqual(len(get_nga911_logan_operations(99)["daily_history"]), 14)
        self.assertEqual(get_nga911_logan_event("evt-logan-2401")["status"], "active")
        self.assertIsNone(get_nga911_logan_event("missing"))


class NGA911IntelligencePageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_page_is_presentation_ready_and_no_store(self):
        response = self.client.get("/nga911-intelligence")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("NGA911 Intelligence", response.text)
        self.assertIn("DEMONSTRATION - SYNTHETIC DATA", response.text)
        self.assertIn("County and PSAP Overview", response.text)
        self.assertIn("Human-authorized operations", response.text)
        self.assertIn("/static/css/lcdash-nga911.css?v=0.2.0", response.text)
        self.assertIn("/static/js/lcdash-nga911.js?v=0.1.1", response.text)

    def test_versioned_api_returns_synthetic_contract(self):
        response = self.client.get("/api/nga911/v1/intelligence/overview")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()["synthetic_data"])
        self.assertEqual(
            response.json()["schema_version"],
            "nga911-intelligence.v1",
        )

    def test_standalone_route_reuses_module_without_lcdash_navigation(self):
        response = self.client.get("/nga911")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Intelligence Platform", response.text)
        self.assertIn("Powered by the LCDash platform core", response.text)
        self.assertIn("DEMONSTRATION - SYNTHETIC DATA", response.text)
        self.assertNotIn('href="/station-alerts"', response.text)
        self.assertNotIn('href="/mindshare"', response.text)

    def test_embedded_county_page_shows_operational_detail(self):
        response = self.client.get("/nga911-intelligence/counties/demo-logan")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Logan County Demonstration", response.text)
        self.assertIn("Resilient Call Paths", response.text)
        self.assertIn("Source Confidence", response.text)
        self.assertIn("DEMONSTRATION - SYNTHETIC DATA", response.text)

    def test_standalone_county_page_keeps_standalone_shell(self):
        response = self.client.get("/nga911/counties/demo-mountain")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mountain Region Demonstration", response.text)
        self.assertIn("Powered by the LCDash platform core", response.text)
        self.assertNotIn('href="/station-alerts"', response.text)

    def test_county_apis_are_versioned_and_return_404(self):
        listing = self.client.get("/api/nga911/v1/counties")
        detail = self.client.get("/api/nga911/v1/counties/demo-valley")
        missing = self.client.get("/api/nga911/v1/counties/missing")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["schema_version"], "nga911-counties.v1")
        self.assertTrue(listing.json()["synthetic_data"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["county"]["id"], "demo-valley")
        self.assertEqual(missing.status_code, 404)

    def test_live_network_page_includes_alert_test_and_all_positions(self):
        response = self.client.get("/nga911/operations?days=14")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Logan 911 Live Network", response.text)
        self.assertIn("Run disruption test", response.text)
        self.assertIn("Enable alerts", response.text)
        self.assertIn("Verizon Fiber", response.text)
        self.assertIn("Starlink", response.text)
        self.assertIn("Position 6", response.text)
        self.assertIn("DEMONSTRATION - SYNTHETIC DATA", response.text)
        self.assertIn("lcdash-nga911-operations.js?v=0.3.0", response.text)
        self.assertIn("/static/img/nga911-official-logo.svg", response.text)
        self.assertIn("/static/img/nexis-connect-official-logo.png", response.text)

    def test_director_api_supports_history_and_event_detail(self):
        operations = self.client.get("/api/nga911/v1/director/operations?days=7")
        event = self.client.get("/api/nga911/v1/director/events/evt-logan-2387")
        missing = self.client.get("/api/nga911/v1/director/events/missing")

        self.assertEqual(operations.status_code, 200)
        self.assertEqual(operations.json()["history_days"], 7)
        self.assertEqual(len(operations.json()["daily_history"]), 7)
        self.assertEqual(event.status_code, 200)
        self.assertTrue(event.json()["synthetic_data"])
        self.assertEqual(event.json()["event"]["severity"], "critical")
        self.assertEqual(missing.status_code, 404)

    def test_event_detail_page_explains_impact_in_plain_language(self):
        response = self.client.get("/nga911/events/evt-logan-2387")

        self.assertEqual(response.status_code, 200)
        self.assertIn("What happened", response.text)
        self.assertIn("Calls affected", response.text)
        self.assertIn("Interruption timeline", response.text)
        self.assertIn("synthetic", response.text.lower())

    @patch("app.main.get_nga911_intelligence_overview")
    def test_page_explains_unconfigured_provider(self, overview):
        overview.side_effect = NGA911ProviderError("Provider not configured")

        response = self.client.get("/nga911-intelligence")

        self.assertEqual(response.status_code, 200)
        self.assertIn("PROVIDER NOT CONFIGURED", response.text)
        self.assertIn("Provider not configured", response.text)

    @patch("app.main.get_nga911_intelligence_overview")
    def test_api_returns_503_when_provider_is_unavailable(self, overview):
        overview.side_effect = NGA911ProviderError("Provider not configured")

        response = self.client.get("/api/nga911/v1/intelligence/overview")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Provider not configured")


if __name__ == "__main__":
    unittest.main()
