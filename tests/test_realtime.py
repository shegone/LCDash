import base64
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.realtime_service import (
    WebhookEventDeduplicator,
    browser_event,
    canonical_webhook_payload,
    get_realtime_health,
    webhook_event_id,
)


class RealtimeServiceTests(unittest.TestCase):
    def test_canonical_payload_and_hash_ignore_object_key_order(self):
        first = {"CFSNumber": "CFS26-1", "Status": {"Description": "Open"}}
        second = {"Status": {"Description": "Open"}, "CFSNumber": "CFS26-1"}

        self.assertEqual(
            canonical_webhook_payload(first),
            canonical_webhook_payload(second),
        )
        self.assertEqual(
            webhook_event_id("cfs", first),
            webhook_event_id("cfs", second),
        )
        self.assertNotEqual(
            webhook_event_id("cfs", first),
            webhook_event_id("units", first),
        )

    def test_deduplicator_recognizes_repeated_events_and_bounds_cache(self):
        deduplicator = WebhookEventDeduplicator(max_events=2)

        self.assertFalse(deduplicator.check_and_store("one"))
        self.assertTrue(deduplicator.check_and_store("one"))
        self.assertFalse(deduplicator.check_and_store("two"))
        self.assertFalse(deduplicator.check_and_store("three"))
        self.assertFalse(deduplicator.check_and_store("one"))

    def test_browser_event_contains_no_cad_payload_or_event_hash(self):
        event = {
            "source": "cfs",
            "received_at": "2026-07-27T12:00:00+00:00",
            "event_id": "secret-event-hash",
            "payload": {"Reporter": {"Name": "Private Person"}},
        }

        self.assertEqual(
            browser_event(event),
            {
                "source": "cfs",
                "received_at": "2026-07-27T12:00:00+00:00",
            },
        )

    @patch(
        "app.services.realtime_service.analytics_database_is_configured",
        return_value=True,
    )
    @patch(
        "app.services.realtime_service.RealtimeRepository.get_delivery_summary"
    )
    @patch(
        "app.services.realtime_service.settings.centralsquare_webhook_secret",
        "configured-secret",
    )
    def test_health_summary_reports_metadata_without_payloads(
        self,
        delivery_summary_mock,
        database_configured_mock,
    ):
        received_at = datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc)
        delivery_summary_mock.return_value = [
            ("cfs", 3, 1, received_at, received_at),
        ]

        health = get_realtime_health("postgresql://configured")

        self.assertEqual(health["status"], "ready")
        self.assertTrue(health["receiver_configured"])
        self.assertTrue(health["database_available"])
        self.assertEqual(health["sources"]["cfs"]["unique_events"], 3)
        self.assertEqual(health["sources"]["cfs"]["total_deliveries"], 4)
        self.assertEqual(health["sources"]["units"]["status"], "awaiting")
        self.assertNotIn("payload", health["sources"]["cfs"])
        self.assertNotIn("secret", str(health).lower())
        database_configured_mock.assert_called_once_with(
            "postgresql://configured"
        )


class RealtimeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @staticmethod
    def _basic_auth(secret):
        encoded = base64.b64encode(
            f"lcdash:{secret}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    @patch("app.main.settings.centralsquare_webhook_secret", "")
    def test_receiver_is_unavailable_until_secret_is_configured(self):
        response = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            json={"CFSNumber": "CFS26-1"},
        )

        self.assertEqual(response.status_code, 503)

    @patch("app.main.settings.centralsquare_webhook_secret", "test-secret")
    def test_receiver_rejects_invalid_credentials(self):
        response = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            json={"CFSNumber": "CFS26-1"},
            headers=self._basic_auth("wrong-secret"),
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["www-authenticate"])

    @patch("app.main.event_broker.publish", new_callable=AsyncMock)
    @patch("app.main.process_webhook_event")
    @patch("app.main.settings.centralsquare_webhook_secret", "test-secret")
    def test_receiver_accepts_event_and_notifies_browser(
        self,
        process_mock,
        publish_mock,
    ):
        process_mock.return_value = {
            "accepted": True,
            "duplicate": False,
            "persisted": True,
            "event_id": "not-returned",
            "source": "cfs",
            "received_at": "2026-07-27T12:00:00+00:00",
        }

        response = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            json={"CFSNumber": "CFS26-1"},
            headers=self._basic_auth("test-secret"),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {"accepted": True, "duplicate": False, "persisted": True},
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("not-returned", response.text)
        publish_mock.assert_awaited_once_with(
            {
                "source": "cfs",
                "received_at": "2026-07-27T12:00:00+00:00",
            }
        )

    @patch("app.main.event_broker.publish", new_callable=AsyncMock)
    @patch("app.main.process_webhook_event")
    @patch("app.main.settings.centralsquare_webhook_secret", "test-secret")
    def test_duplicate_event_does_not_notify_browser(
        self,
        process_mock,
        publish_mock,
    ):
        process_mock.return_value = {
            "accepted": True,
            "duplicate": True,
            "persisted": False,
            "event_id": "not-returned",
            "source": "units",
            "received_at": "2026-07-27T12:00:00+00:00",
        }

        response = self.client.post(
            "/api/integrations/centralsquare/webhooks/units",
            json={"UnitNumber": "MED10"},
            headers={"X-LCDash-Webhook-Secret": "test-secret"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["duplicate"])
        publish_mock.assert_not_awaited()

    @patch("app.main.settings.centralsquare_webhook_secret", "test-secret")
    @patch("app.main.event_broker.publish", new_callable=AsyncMock)
    @patch("app.main.process_webhook_event")
    def test_receiver_rejects_invalid_json_and_accepts_scalar_json(
        self,
        process_mock,
        publish_mock,
    ):
        process_mock.return_value = {
            "accepted": True,
            "duplicate": False,
            "persisted": True,
            "event_id": "not-returned",
            "source": "cfs",
            "received_at": "2026-07-29T19:45:00+00:00",
        }
        invalid = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            content=b"{bad-json",
            headers={
                **self._basic_auth("test-secret"),
                "Content-Type": "application/json",
            },
        )
        scalar = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            content=b'"not-an-event"',
            headers={
                **self._basic_auth("test-secret"),
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(scalar.status_code, 202)
        process_mock.assert_called_once_with(
            "cfs",
            "not-an-event",
            len(b'"not-an-event"'),
        )
        publish_mock.assert_awaited_once()

    @patch("app.main.settings.webhook_max_body_bytes", 10)
    @patch("app.main.settings.centralsquare_webhook_secret", "test-secret")
    def test_receiver_rejects_oversized_payload(self):
        response = self.client.post(
            "/api/integrations/centralsquare/webhooks/cfs",
            content=b'{"value":"too large"}',
            headers={
                **self._basic_auth("test-secret"),
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 413)

    def test_event_stream_route_and_dashboard_eventsource_are_present(self):
        route = next(
            route
            for route in app.routes
            if getattr(route, "path", "") == "/api/operations/events"
        )
        self.assertIn("GET", route.methods)

        script = self.client.get("/static/js/lcdash-dashboard.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn('new EventSource("/api/operations/events")', script.text)
        self.assertIn('"operations_changed"', script.text)
        self.assertIn('"UPDATE CHANNEL"', script.text)
        self.assertIn('"30S BACKUP"', script.text)
        self.assertIn("Application update ", script.text)
        self.assertIn("const REFRESH_SECONDS = 30", script.text)

    @patch("app.main.get_realtime_health")
    def test_integration_health_api_and_page_are_metadata_only(
        self,
        health_mock,
    ):
        health_mock.return_value = {
            "status": "ready",
            "status_label": "Ready",
            "receiver_configured": True,
            "database_configured": True,
            "database_available": True,
            "metadata_only": True,
            "reconciliation_poll_seconds": 30,
            "generated_at": "2026-07-27T12:30:00+00:00",
            "sources": {
                "cfs": {
                    "source": "cfs",
                    "label": "Calls for Service",
                    "status": "observed",
                    "status_label": "Delivery observed",
                    "delivery_observed": True,
                    "unique_events": 2,
                    "duplicate_deliveries": 1,
                    "total_deliveries": 3,
                    "latest_unique_event": "2026-07-27T12:29:00+00:00",
                    "latest_delivery": "2026-07-27T12:29:00+00:00",
                },
                "units": {
                    "source": "units",
                    "label": "Unit Updates",
                    "status": "awaiting",
                    "status_label": "Awaiting first event",
                    "delivery_observed": False,
                    "unique_events": 0,
                    "duplicate_deliveries": 0,
                    "total_deliveries": 0,
                    "latest_unique_event": "",
                    "latest_delivery": "",
                },
            },
        }

        api_response = self.client.get(
            "/api/integrations/centralsquare/health"
        )
        page_response = self.client.get("/integrations/health")
        script_response = self.client.get(
            "/static/js/lcdash-integrations.js"
        )

        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.headers["cache-control"], "no-store")
        self.assertTrue(api_response.json()["metadata_only"])
        self.assertNotIn("payload", api_response.text.lower())
        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(page_response.headers["cache-control"], "no-store")
        self.assertIn("Integration Health", page_response.text)
        self.assertIn("Calls for Service", page_response.text)
        self.assertIn("Awaiting first event", page_response.text)
        self.assertIn("/integrations/health", page_response.text)
        self.assertEqual(script_response.status_code, 200)
        self.assertIn(
            'new EventSource(EVENTS_URL)',
            script_response.text,
        )
        self.assertIn(
            '"/api/integrations/centralsquare/health"',
            script_response.text,
        )


if __name__ == "__main__":
    unittest.main()
