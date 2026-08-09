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


@dataclass
class FakeRawResponse:
    """A reply whose body is not JSON, like the httpx response the WAF produces."""

    status_code: int
    text: str
    headers: dict[str, str] | None = None

    def __post_init__(self):
        self.headers = self.headers or {}

    def json(self):
        raise ValueError("not json")


# Verbatim shape of the block the CentralSquare F5 WAF returned on 2026-08-08
# for documented-but-refused filter parameters (IncidentCode, Beat): a SUCCESS
# status carrying an HTML page.
WAF_HTML = (
    "<html><head><title>Request Rejected</title></head><body>The requested URL "
    "was rejected. Please consult with your administrator.<br><br>Your support "
    "ID is: 10968924947214390874</body></html>"
)


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

    def test_html_on_success_status_is_diagnosed_without_polluting_the_error(self):
        """The WAF-block shape: HTTP 200 whose body is an HTML rejection page.

        Two properties must hold at once. The diagnostics hook receives enough
        to see the cause in a log -- status, content type, and the page text.
        The EXCEPTION stays sanitized, because it can surface to callers and to
        the AI advisory: no body, no chained traceback.
        """
        seen = []
        connector, _, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeRawResponse(200, WAF_HTML, {"Content-Type": "text/html; charset=utf-8"}),
            ],
            enabled=True,
            diagnostics=seen.append,
        )
        with self.assertRaises(CloudCadConnectorError) as captured:
            connector.search_calls({"IncidentCode": "MOVEUP"})

        error = captured.exception
        self.assertEqual(error.code, "invalid_json_response")
        # The status now travels with the error, so a caller can tell a
        # disguised 200-block from genuinely malformed data.
        self.assertEqual(error.status_code, 200)
        self.assertIsNone(error.__cause__)
        rendered = json.dumps(dict(error.to_dict())) + str(error)
        self.assertNotIn("Request Rejected", rendered)
        self.assertNotIn("support ID", rendered)

        [detail] = seen
        self.assertEqual(detail["operation"], "search_calls")
        self.assertEqual(detail["status_code"], 200)
        self.assertIn("text/html", detail["content_type"])
        self.assertEqual(detail["body_length"], len(WAF_HTML))
        self.assertIn("Request Rejected", detail["body_prefix"])

    def test_truncated_json_body_is_never_quoted_into_diagnostics(self):
        """A body that CLAIMS to be JSON but fails to parse is usually cut-off
        real data. Call records must not leak into logs, so only the length and
        headers are reported for those."""
        seen = []
        connector, _, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeRawResponse(
                    200,
                    '{"cfs_cores": [{"reporter": "SENSITIVE-NAME", "location":',
                    {"content-type": "application/json"},
                ),
            ],
            enabled=True,
            diagnostics=seen.append,
        )
        with self.assertRaises(CloudCadConnectorError):
            connector.search_calls({})
        [detail] = seen
        self.assertNotIn("body_prefix", detail)
        self.assertNotIn("SENSITIVE-NAME", json.dumps(dict(detail)))
        self.assertEqual(detail["content_type"], "application/json")
        self.assertGreater(detail["body_length"], 0)

    def test_token_endpoint_diagnostics_never_include_a_body(self):
        seen = []
        connector, _, _ = self.connector(
            [FakeRawResponse(200, WAF_HTML, {"content-type": "text/html"})],
            enabled=True,
            diagnostics=seen.append,
        )
        with self.assertRaises(CloudCadConnectorError) as captured:
            connector.search_calls({})
        self.assertEqual(captured.exception.code, "invalid_token_response")
        [detail] = seen
        self.assertEqual(detail["operation"], "authenticate")
        self.assertNotIn("body_prefix", detail)

    def test_a_failing_diagnostics_hook_never_displaces_the_real_error(self):
        def broken_hook(detail):
            raise RuntimeError("hook exploded")

        connector, _, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeRawResponse(200, WAF_HTML, {"content-type": "text/html"}),
            ],
            enabled=True,
            diagnostics=broken_hook,
        )
        with self.assertRaises(CloudCadConnectorError) as captured:
            connector.search_calls({})
        self.assertEqual(captured.exception.code, "invalid_json_response")

    def test_no_diagnostics_hook_still_raises_the_sanitized_error(self):
        connector, _, _ = self.connector(
            [
                FakeResponse(200, {"access_token": "synthetic-token", "expires_in": 900}),
                FakeRawResponse(200, WAF_HTML, {"content-type": "text/html"}),
            ],
            enabled=True,
        )
        with self.assertRaises(CloudCadConnectorError) as captured:
            connector.search_calls({})
        self.assertEqual(captured.exception.code, "invalid_json_response")

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
