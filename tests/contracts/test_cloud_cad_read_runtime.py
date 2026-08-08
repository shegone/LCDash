"""Network-free runtime wiring tests for cloud CAD polling."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.integrations.cad.cloud_read_config import CENTRALSQUARE_SECRET_ARN_PREFIX
from app.integrations.cad.cloud_read_runtime import (
    CloudCadReadPoller,
    SecretsManagerCredentialProvider,
    build_cloud_cad_runtime,
)


SECRET_ARN = CENTRALSQUARE_SECRET_ARN_PREFIX + "-Ab12Cd"


class FakeConnector:
    def __init__(self):
        self.call_count = 0

    def search_calls(self, body, *, skip, limit):
        self.call_count += 1
        return {
            "cfs_cores": [
                {
                    "cfs_number": "synthetic-1",
                    "priority": "1",
                    "raw_sensitive_field": "must-not-survive",
                }
            ]
        }

    def search_units(self, body, *, skip, limit):
        self.call_count += 1
        return {
            "units": [
                {
                    "unit_number": "SYN1",
                    "status": "Available",
                    "raw_sensitive_field": "must-not-survive",
                }
            ]
        }


class CloudCadReadRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_default_constructs_no_provider_and_makes_no_calls(self):
        settings = SimpleNamespace(cloud_cad_enabled=False)
        runtime = build_cloud_cad_runtime(settings)
        runtime.start()
        await runtime.poll_once()
        await runtime.stop()
        status = runtime.status()
        self.assertEqual(status["freshness"], "disabled")
        self.assertEqual(status["call_count"], 0)
        self.assertEqual(
            status["operation_counts"],
            {
                "search_calls": 0,
                "get_call": 0,
                "search_units": 0,
                "get_configurations": 0,
            },
        )

    async def test_single_poller_normalizes_only_display_fields_and_tracks_freshness(self):
        connector = FakeConnector()
        now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        runtime = CloudCadReadPoller(
            connector, enabled=True, poll_seconds=30, clock=lambda: now
        )
        await runtime.poll_once()
        self.assertEqual(connector.call_count, 2)
        self.assertEqual(runtime.state.calls[0]["cfs_number"], "synthetic-1")
        self.assertEqual(runtime.state.units[0]["unit_number"], "SYN1")
        self.assertNotIn("raw_sensitive_field", runtime.state.calls[0])
        self.assertNotIn("raw_sensitive_field", runtime.state.units[0])
        self.assertEqual(runtime.state.status(now=now)["freshness"], "current")
        self.assertEqual(
            runtime.state.status(now=now + timedelta(seconds=121))["freshness"],
            "stale",
        )
        self.assertEqual(
            runtime.status(now=now)["operation_counts"],
            {
                "search_calls": 1,
                "get_call": 0,
                "search_units": 1,
                "get_configurations": 0,
            },
        )

    async def test_status_schema_cannot_expose_payload_or_connection_material(self):
        connector = FakeConnector()
        runtime = CloudCadReadPoller(connector, enabled=True, poll_seconds=30)
        await runtime.poll_once()
        status = dict(runtime.status())
        self.assertEqual(
            set(status),
            {
                "enabled",
                "mode",
                "freshness",
                "age_seconds",
                "error_code",
                "call_count",
                "unit_count",
                "operation_counts",
            },
        )
        rendered = json.dumps(status).lower()
        for forbidden in (
            "synthetic-1",
            "syn1",
            "raw_sensitive",
            "http",
            "secret",
            "token",
            "authorization",
            "header",
            "address",
            "narrative",
            "cfs_number",
            "unit_number",
        ):
            self.assertNotIn(forbidden, rendered)

    async def test_exception_text_is_reduced_to_a_fixed_error_code(self):
        connector = FakeConnector()
        connector.search_calls = Mock(
            side_effect=RuntimeError("token secret address raw-payload")
        )
        runtime = CloudCadReadPoller(connector, enabled=True, poll_seconds=30)
        await runtime.poll_once()
        rendered = json.dumps(dict(runtime.status())).lower()
        self.assertIn('"error_code": "poll_failed"', rendered)
        self.assertNotIn("raw-payload", rendered)
        self.assertNotIn("address", rendered)

    async def test_lifecycle_rejects_a_second_live_poller_task(self):
        runtime = CloudCadReadPoller(FakeConnector(), enabled=True, poll_seconds=30)
        runtime.start()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            runtime.start()
        await runtime.stop()

    def test_secret_provider_hard_allowlists_one_exact_arn(self):
        client = Mock()
        client.get_secret_value.return_value = {
            "SecretString": '{"username":"synthetic-user","password":"synthetic-password"}'
        }
        provider = SecretsManagerCredentialProvider(SECRET_ARN, client=client)
        with self.assertRaisesRegex(ValueError, "exact allowlist"):
            provider.get_credentials(CENTRALSQUARE_SECRET_ARN_PREFIX + "-Other1")
        client.get_secret_value.assert_not_called()
        credentials = provider.get_credentials(SECRET_ARN)
        client.get_secret_value.assert_called_once_with(SecretId=SECRET_ARN)
        self.assertEqual(credentials.username, "synthetic-user")

    def test_secret_json_rejects_every_unreviewed_key(self):
        client = Mock()
        client.get_secret_value.return_value = {
            "SecretString": '{"username":"u","password":"p","token":"forbidden"}'
        }
        provider = SecretsManagerCredentialProvider(SECRET_ARN, client=client)
        with self.assertRaisesRegex(ValueError, "only the reviewed"):
            provider.get_credentials(SECRET_ARN)

    def test_search_recent_calls_paginates_and_dedupes_raw_calls(self):
        first_page = [{"CFSNumber": f"CFS26-{index:05d}"} for index in range(100)]
        second_page = [
            {"CFSNumber": "CFS26-00000", "PrimaryResponseAgency": {"Abbreviation": "FIRE"}},
            {"CFSNumber": "CFS26-99999"},
        ]
        pages = [
            {"cfs_cores": first_page, "next": "more"},
            {"cfs_cores": second_page, "next": "misleading"},
        ]
        seen = []

        class PagingConnector:
            def search_calls(self, body, *, skip, limit):
                seen.append((dict(body), skip, limit))
                return pages[len(seen) - 1]

        now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        runtime = CloudCadReadPoller(
            PagingConnector(), enabled=True, poll_seconds=30, clock=lambda: now
        )

        raw_calls = runtime.search_recent_calls(8, now=now)

        # 100 unique from page one plus one new number; the duplicate is replaced.
        self.assertEqual(len(raw_calls), 101)
        self.assertEqual([entry[1] for entry in seen], [0, 100])
        self.assertEqual([entry[2] for entry in seen], [100, 100])
        body = seen[0][0]
        self.assertEqual(body["RecordCreatedFrom"], "2026-08-05T04:00:00+00:00")
        self.assertEqual(body["RecordCreatedTo"], "2026-08-05T12:00:00+00:00")
        self.assertEqual(body["OrderByField"], "Created")
        self.assertEqual(body["OrderByDirection"], "Descending")
        updated = next(call for call in raw_calls if call["CFSNumber"] == "CFS26-00000")
        self.assertEqual(updated["PrimaryResponseAgency"]["Abbreviation"], "FIRE")
        self.assertEqual(
            runtime.status(now=now)["operation_counts"]["search_calls"], 2
        )

    def test_search_recent_calls_returns_empty_when_disabled(self):
        settings = SimpleNamespace(cloud_cad_enabled=False)
        runtime = build_cloud_cad_runtime(settings)
        self.assertEqual(runtime.search_recent_calls(8), [])

    def test_status_endpoint_is_no_store_and_uses_only_fixed_view_model(self):
        source = (Path(__file__).parents[2] / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('@app.get("/api/pilot/cad-read-status")', source)
        self.assertIn('response.headers["Cache-Control"] = "no-store"', source)
        self.assertIn("return dict(cloud_cad_runtime.status())", source)


if __name__ == "__main__":
    unittest.main()
