"""Network-free tests for the dormant cloud inbound CAD configuration gate."""

import socket
import unittest
from unittest.mock import patch

from app.integrations.cad.cloud_read_config import (
    CALL_FIELDS,
    CENTRALSQUARE_SECRET_ARN_PREFIX,
    CENTRALSQUARE_SECRET_JSON_KEYS,
    CENTRALSQUARE_SECRET_NAME,
    CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_HOST,
    CENTRALSQUARE_DOCUMENTED_MAX_PAGE_SIZE,
    CENTRALSQUARE_DOCUMENTED_READ_PATHS,
    CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
    UNIT_FIELDS,
    CloudCadMode,
    CloudCadReadConfig,
)


class CloudCadReadConfigTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def live_values(self) -> dict:
        return {
            "mode": "centralsquare-read-poll",
            "tenant_id": "logan-synthetic",
            "secret_reference": CENTRALSQUARE_SECRET_ARN_PREFIX + "-synthetic",
            "token_url": "https://token.synthetic.invalid/api/token",
            "cad_base_url": "https://cad.synthetic.invalid/api/cad/v1",
            "system_base_url": "https://system.synthetic.invalid/api/system/v1",
            "poll_seconds": 30,
            "reconciliation_overlap_seconds": 120,
            "webhooks_enabled": False,
        }

    def test_synthetic_mode_is_empty_and_not_activation_ready(self):
        config = CloudCadReadConfig(
            mode=CloudCadMode.SYNTHETIC_DISCONNECTED,
            tenant_id="logan-synthetic",
        )
        self.assertFalse(config.activation_ready)
        self.assertEqual(config.secret_reference, "")
        self.network_mock.assert_not_called()

    def test_secret_metadata_contract_contains_no_values(self):
        self.assertEqual(
            CENTRALSQUARE_SECRET_NAME,
            "lcdash-p1-logan-use1/centralsquare/read-only",
        )
        self.assertEqual(CENTRALSQUARE_SECRET_JSON_KEYS, ("username", "password"))
        self.assertIn(":862772137583:secret:", CENTRALSQUARE_SECRET_ARN_PREFIX)
        self.assertNotIn("credential", CENTRALSQUARE_SECRET_ARN_PREFIX.lower())

    def test_read_poll_contract_is_minimized_and_has_no_write_operations(self):
        config = CloudCadReadConfig.from_mapping(self.live_values())
        self.assertTrue(config.activation_ready)
        self.assertEqual(config.data_minimization["calls"], CALL_FIELDS)
        self.assertEqual(config.data_minimization["units"], UNIT_FIELDS)
        self.assertNotIn("update_call", config.allowed_operations)
        self.assertNotIn("register_subscription", config.allowed_operations)
        self.assertIn("acknowledge", config.forbidden_operations)
        self.assertFalse(config.webhooks_enabled)
        self.network_mock.assert_not_called()

    def test_vendor_documented_v1_endpoint_envelope_is_exact_and_read_only(self):
        values = self.live_values()
        values.update(
            {
                "token_url": CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
                "cad_base_url": CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
                "system_base_url": CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
            }
        )
        config = CloudCadReadConfig.from_mapping(values)

        self.assertEqual(
            CENTRALSQUARE_DOCUMENTED_HOST,
            "api-wv-logan-911.centralsquarecloudgov.com",
        )
        self.assertEqual(config.token_url, CENTRALSQUARE_DOCUMENTED_TOKEN_URL)
        self.assertEqual(config.cad_base_url, CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL)
        self.assertEqual(config.system_base_url, CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL)
        self.assertEqual(CENTRALSQUARE_DOCUMENTED_MAX_PAGE_SIZE, 100)
        self.assertEqual(
            CENTRALSQUARE_DOCUMENTED_READ_PATHS,
            (
                ("POST", "/cfs_core/search"),
                ("GET", "/cfs_core/{CFSNumber}"),
                ("POST", "/units/search"),
                ("GET", "/configurations"),
            ),
        )
        self.assertFalse(
            any(
                method in {"PUT", "PATCH", "DELETE"}
                or any(
                    word in path.lower()
                    for word in (
                        "ack", "dispatch", "alert", "page", "tone",
                        "subscription", "message", "command",
                    )
                )
                for method, path in CENTRALSQUARE_DOCUMENTED_READ_PATHS
            )
        )
        self.network_mock.assert_not_called()

    def test_live_fields_are_forbidden_in_synthetic_mode(self):
        values = self.live_values()
        values["mode"] = "synthetic-disconnected"
        with self.assertRaisesRegex(ValueError, "cannot contain live CAD"):
            CloudCadReadConfig.from_mapping(values)

    def test_secret_value_shaped_or_unknown_configuration_is_rejected(self):
        values = self.live_values()
        values["password"] = "synthetic-placeholder"
        with self.assertRaisesRegex(ValueError, "Unknown cloud CAD configuration keys"):
            CloudCadReadConfig.from_mapping(values)

    def test_webhook_and_unbounded_polling_are_rejected(self):
        values = self.live_values()
        values["webhooks_enabled"] = True
        with self.assertRaisesRegex(ValueError, "webhook activation is not authorized"):
            CloudCadReadConfig.from_mapping(values)
        values = self.live_values()
        values["poll_seconds"] = 5
        with self.assertRaisesRegex(ValueError, "between 15 and 300"):
            CloudCadReadConfig.from_mapping(values)

    def test_endpoint_validation_rejects_unsafe_destinations(self):
        for endpoint in (
            "http://cad.synthetic.invalid/api",
            "https://user:pass@cad.synthetic.invalid/api",
            "https://127.0.0.1/api",
            "https://localhost/api",
            "https://cad.synthetic.invalid/api?token=fixture",
        ):
            with self.subTest(endpoint=endpoint):
                values = self.live_values()
                values["cad_base_url"] = endpoint
                with self.assertRaises(ValueError):
                    CloudCadReadConfig.from_mapping(values)
        self.network_mock.assert_not_called()

    def test_tenant_region_and_reconciliation_fail_closed(self):
        values = self.live_values()
        values["tenant_id"] = "../../other-county"
        with self.assertRaisesRegex(ValueError, "stable tenant identifier"):
            CloudCadReadConfig.from_mapping(values)
        values = self.live_values()
        values["secret_reference"] = "arn:aws:secretsmanager:us-west-2:111111111111:secret:test"
        with self.assertRaisesRegex(ValueError, "tenant-scoped"):
            CloudCadReadConfig.from_mapping(values)
        values = self.live_values()
        values["reconciliation_overlap_seconds"] = 10
        with self.assertRaisesRegex(ValueError, "at least one poll"):
            CloudCadReadConfig.from_mapping(values)


if __name__ == "__main__":
    unittest.main()
