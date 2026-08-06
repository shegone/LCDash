"""Network-free contract tests for the dormant cloud CAD connector."""

from collections import deque
from dataclasses import dataclass
import json
import socket
import unittest
from unittest.mock import Mock, patch

from app.integrations.cad.cloud_read_config import (
    CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
    CENTRALSQUARE_SECRET_ARN_PREFIX,
    CloudCadReadConfig,
)
from app.integrations.cad.cloud_read_connector import (
    CentralSquareCredentials,
    CloudCadConnectorError,
    CloudCentralSquareReadConnector,
)


@dataclass
class FakeResponse:
    status_code: int
    payload: object
    headers: dict[str, str] | None = None

    def __post_init__(self):
        self.headers = self.headers or {}

    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.responses.popleft()


class CloudCadReadConnectorTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(
            socket.socket, "connect", side_effect=AssertionError("network blocked")
        )
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.secret = Mock()
        self.secret.get_credentials.return_value = CentralSquareCredentials(
            "synthetic-user", "synthetic-password"
        )

    @staticmethod
    def config(**updates):
        values = {
            "mode": "centralsquare-read-poll",
            "tenant_id": "logan-synthetic",
            "secret_reference": CENTRALSQUARE_SECRET_ARN_PREFIX + "-synthetic",
            "token_url": CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
            "cad_base_url": CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
            "system_base_url": CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
            "poll_seconds": 30,
            "reconciliation_overlap_seconds": 120,
            "webhooks_enabled": False,
        }
        values.update(updates)
        return CloudCadReadConfig.from_mapping(values)

    def connector(self, responses, **kwargs):
        transport = FakeTransport(responses)
        sleeper = Mock()
        connector = CloudCentralSquareReadConnector(
            self.config(),
            from_header="lcdash-cloud-pilot",
            secret_provider=self.secret,
            transport=transport,
            sleeper=sleeper,
            **kwargs,
        )
        return connector, transport, sleeper

    def test_disabled_by_default_before_secret_or_transport(self):
        connector, transport, _ = self.connector([])
        with self.assertRaisesRegex(CloudCadConnectorError, "connector_disabled"):
            connector.search_calls({"CurrentlyActive": True})
        self.secret.get_credentials.assert_not_called()
        self.assertEqual(transport.requests, [])

    def test_exact_token_flow_from_header_and_search_allowlist(self):
        connector, transport, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeResponse(200, {"cfs_cores": []}),
            ],
            enabled=True,
        )
        self.assertEqual(
            connector.search_calls({"CurrentlyActive": True}, skip=0, limit=100),
            {"cfs_cores": []},
        )
        token_request, search_request = transport.requests
        self.assertEqual((token_request.method, token_request.url), ("POST", CENTRALSQUARE_DOCUMENTED_TOKEN_URL))
        self.assertEqual(token_request.form["grant_type"], "password")
        self.assertEqual(
            (search_request.method, search_request.url),
            ("POST", f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/cfs_core/search"),
        )
        self.assertEqual(search_request.headers["From"], "lcdash-cloud-pilot")
        self.assertEqual(search_request.query, {"skip": 0, "limit": 100})
        self.assertNotIn(
            "password",
            json.dumps(
                {
                    "headers": dict(search_request.headers),
                    "query": dict(search_request.query),
                    "json_body": dict(search_request.json_body),
                }
            ).lower(),
        )
        self.network_mock.assert_not_called()

    def test_all_four_and_only_four_operations_generate_reviewed_paths(self):
        connector, transport, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeResponse(200, {}),
                FakeResponse(200, {}),
                FakeResponse(200, {}),
                FakeResponse(200, {}),
            ],
            enabled=True,
        )
        connector.search_calls({}, limit=10)
        connector.get_call("2026-0001")
        connector.search_units({}, limit=10)
        connector.get_configurations("CADUnitStatus")
        generated = {(item.method, item.url) for item in transport.requests[1:]}
        self.assertEqual(
            generated,
            {
                ("POST", f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/cfs_core/search"),
                ("GET", f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/cfs_core/2026-0001"),
                ("POST", f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/units/search"),
                ("GET", f"{CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL}/configurations"),
            },
        )
        self.assertFalse(hasattr(connector, "request"))
        for forbidden in ("update_call", "acknowledge", "dispatch", "send_alert", "page", "trigger_tone", "register_subscription"):
            self.assertFalse(hasattr(connector, forbidden))

    def test_token_is_cached_until_vendor_expiry_refresh_window(self):
        now = [1_000.0]
        connector, transport, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "token-one", "expires_in": 900}),
                FakeResponse(200, {"cfs_cores": []}),
                FakeResponse(200, {"units": []}),
                FakeResponse(200, {"access_token": "token-two", "expires_in": 900}),
                FakeResponse(200, {"units": []}),
            ],
            enabled=True,
            clock=lambda: now[0],
        )
        connector.search_calls({})
        connector.search_units({})
        self.assertEqual(sum(request.url == CENTRALSQUARE_DOCUMENTED_TOKEN_URL for request in transport.requests), 1)
        now[0] = 1_841.0
        connector.search_units({})
        self.assertEqual(sum(request.url == CENTRALSQUARE_DOCUMENTED_TOKEN_URL for request in transport.requests), 2)

    def test_pagination_and_path_inputs_fail_before_transport(self):
        connector, transport, _ = self.connector([], enabled=True)
        for call in (
            lambda: connector.search_calls({}, limit=101),
            lambda: connector.search_units({}, skip=-1),
            lambda: connector.get_call("../unsafe"),
            lambda: connector.get_configurations("bad/value"),
        ):
            with self.assertRaises(ValueError):
                call()
        self.secret.get_credentials.assert_not_called()
        self.assertEqual(transport.requests, [])

    def test_429_and_server_errors_use_bounded_backoff(self):
        connector, transport, sleeper = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeResponse(429, {}, {"retry-after": "2"}),
                FakeResponse(503, {}),
                FakeResponse(200, {"units": []}),
            ],
            enabled=True,
        )
        self.assertEqual(connector.search_units({}), {"units": []})
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [2.0, 0.5])
        self.assertEqual(len(transport.requests), 4)

    def test_errors_are_structured_and_do_not_expose_payload_or_secret(self):
        connector, _, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeResponse(403, {"detail": "raw-sensitive-upstream-detail"}),
            ],
            enabled=True,
        )
        with self.assertRaises(CloudCadConnectorError) as captured:
            connector.get_configurations("CADUnitStatus")
        rendered = json.dumps(dict(captured.exception.to_dict())) + str(captured.exception)
        self.assertNotIn("raw-sensitive", rendered)
        self.assertNotIn("synthetic-password", rendered)
        self.assertNotIn("synthetic-token", rendered)
        self.assertEqual(captured.exception.status_code, 403)
        self.assertIsNone(captured.exception.__cause__)

    def test_exact_endpoint_and_polling_envelope_is_mandatory(self):
        transport = FakeTransport([])
        for config in (
            self.config(token_url="https://other.invalid/api/token"),
            self.config(poll_seconds=31),
            self.config(reconciliation_overlap_seconds=121),
        ):
            with self.assertRaises(ValueError):
                CloudCentralSquareReadConnector(
                    config,
                    from_header="lcdash-cloud-pilot",
                    secret_provider=self.secret,
                    transport=transport,
                )
        self.secret.get_credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
