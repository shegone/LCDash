import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.nga911_intelligence_service import (
    MockNGA911IntelligenceProvider,
    NGA911ProviderError,
    get_nga911_intelligence_overview,
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
        self.assertIn("/static/css/lcdash-nga911.css?v=0.1.0", response.text)
        self.assertIn("/static/js/lcdash-nga911.js?v=0.1.0", response.text)

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
