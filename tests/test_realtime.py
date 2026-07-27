import base64
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.realtime_service import (
    WebhookEventDeduplicator,
    browser_event,
    canonical_webhook_payload,
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
    def test_receiver_rejects_invalid_json_and_scalar_json(self):
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
        self.assertEqual(scalar.status_code, 422)

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
        self.assertIn("const REFRESH_SECONDS = 30", script.text)


if __name__ == "__main__":
    unittest.main()
