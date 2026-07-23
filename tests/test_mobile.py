import unittest

from fastapi.testclient import TestClient

from app.main import app


class MobileFoundationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_analytics_page_includes_mobile_navigation_and_manifest(self):
        response = self.client.get("/analytics")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="mobile-menu-button"', response.text)
        self.assertIn('id="lcdash-sidebar"', response.text)
        self.assertIn('/static/css/lcdash-mobile.css', response.text)
        self.assertIn('/static/js/lcdash-mobile.js', response.text)
        self.assertIn('/static/manifest.webmanifest', response.text)
        self.assertIn('rel="icon" type="image/png" href="/static/img/logan911-logo.png"', response.text)

    def test_manifest_is_installable_and_starts_at_dashboard(self):
        response = self.client.get("/static/manifest.webmanifest")

        self.assertEqual(response.status_code, 200)
        manifest = response.json()
        self.assertEqual(manifest["short_name"], "LCDash")
        self.assertEqual(manifest["start_url"], "/dashboard")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")

    def test_service_worker_allows_root_scope_without_cad_caching(self):
        response = self.client.get("/static/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["service-worker-allowed"], "/")
        self.assertNotIn("/api/", response.text)
        self.assertNotIn("/dashboard", response.text)


if __name__ == "__main__":
    unittest.main()
