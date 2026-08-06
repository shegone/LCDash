"""Synthetic and network-free cloud CentralSquare read preflight tests."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from app.tools.cloud_cad_read_preflight import (
    CloudCadPreflightError,
    evaluate_cloud_cad_read_preflight,
)


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:862772137583:secret:"
    "lcdash-p1-logan-use1/centralsquare/read-only-Ab12Cd"
)


class CloudCadReadPreflightTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.addCleanup(self.temp.cleanup)
        self.repository = Path(self.temp.name) / "repo"
        self.repository.mkdir()
        self.network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def manifest(self):
        hosts = {
            "token": "token.synthetic-public.org",
            "cad": "cad.synthetic-public.org",
            "system": "system.synthetic-public.org",
        }
        return {
            "schema_version": "lcdash.cloud-cad-read-preflight.v1",
            "manifest_id": "synthetic-cad-preflight-0001",
            "binding": {
                "account_id": "862772137583",
                "region": "us-east-1",
                "tenant_id": "logan-synthetic",
                "provider": "centralsquare",
                "mode": "centralsquare-read-poll",
            },
            "secret_reference": SECRET_ARN,
            "endpoints": {
                key: {
                    "url": f"https://{host}/api/{key}",
                    "approved_hostname": host,
                    "tls_review_reference": f"synthetic-tls-review-{key}",
                }
                for key, host in hosts.items()
            },
            "polling": {
                "poll_seconds": 30,
                "reconciliation_overlap_seconds": 120,
                "webhooks_enabled": False,
            },
            "vendor_evidence": {
                "approval_reference": "synthetic-vendor-approval-0001",
                "approved_at": "2026-08-05T13:00:00Z",
                "approved_hostnames": list(hosts.values()),
                "commercial_aws_access_allowed": True,
                "concurrent_use_allowed": True,
                "polling_allowed": True,
                "source_ip_requirement": "none",
                "rate_limit_requests_per_minute": 60,
                "token_lifetime_seconds": 900,
                "maximum_page_size": 100,
                "evidence_owner": "synthetic-vendor-reviewer",
            },
        }

    def evaluate(self, payload):
        path = self.repository / "preflight.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return evaluate_cloud_cad_read_preflight(path, repository_root=self.repository)

    def test_complete_metadata_is_ready_for_review_but_never_authorized(self):
        report = self.evaluate(self.manifest())
        self.assertTrue(report.ready_for_activation_review)
        self.assertFalse(report.activation_authorized)
        self.assertEqual(report.secret_reference, SECRET_ARN)
        self.assertEqual(report.poll_seconds, 30)
        self.assertFalse(report.webhooks_enabled)
        self.assertEqual(len(report.approved_hostnames), 3)
        self.network_mock.assert_not_called()

    def test_secret_tenant_account_and_region_binding_fail_closed(self):
        mutations = (
            (("secret_reference",), "arn:aws:secretsmanager:us-west-2:111111111111:secret:test-Ab12Cd", "secret_reference"),
            (("binding", "tenant_id"), "other-county", "tenant_id"),
            (("binding", "account_id"), "111111111111", "account_id"),
            (("binding", "region"), "us-west-2", "region"),
        )
        for path, value, expected in mutations:
            with self.subTest(path=path):
                payload = self.manifest()
                if len(path) == 1:
                    payload[path[0]] = value
                else:
                    payload[path[0]][path[1]] = value
                report = self.evaluate(payload)
                self.assertFalse(report.ready_for_activation_review)
                self.assertFalse(report.activation_authorized)
                self.assertIn(expected, " ".join(report.errors))

    def test_endpoints_reject_http_query_fragment_ip_local_and_unapproved_host(self):
        urls = (
            "http://cad.synthetic-public.org/api",
            "https://cad.synthetic-public.org/api?key=fixture",
            "https://cad.synthetic-public.org/api#fragment",
            "https://127.0.0.1/api",
            "https://localhost/api",
            "https://cad.internal/api",
            "https://other.synthetic-public.org/api",
            "https://cad.synthetic-public.org:bad/api",
        )
        for url in urls:
            with self.subTest(url=url):
                payload = self.manifest()
                payload["endpoints"]["cad"]["url"] = url
                report = self.evaluate(payload)
                self.assertFalse(report.ready_for_activation_review)
        self.network_mock.assert_not_called()

    def test_vendor_documentation_routes_are_not_runtime_api_endpoints(self):
        documentation_candidates = {
            "cad": (
                "https://api-wv-logan-911.centralsquarecloudgov.com/"
                "api/cad/v1/docs#/"
            ),
            "system": (
                "https://api-wv-logan-911.centralsquarecloudgov.com/"
                "api/system/v1/docs#/"
            ),
        }

        for endpoint_name, url in documentation_candidates.items():
            with self.subTest(endpoint=endpoint_name):
                payload = self.manifest()
                payload["endpoints"][endpoint_name]["url"] = url
                payload["endpoints"][endpoint_name]["approved_hostname"] = (
                    "api-wv-logan-911.centralsquarecloudgov.com"
                )
                report = self.evaluate(payload)
                self.assertFalse(report.ready_for_activation_review)
                self.assertFalse(report.activation_authorized)
                self.assertIn("query or fragment", " ".join(report.errors))

        self.network_mock.assert_not_called()

    def test_poll_reconciliation_and_webhook_limits_are_inherited(self):
        for field, value, expected in (
            ("poll_seconds", 5, "between 15 and 300"),
            ("reconciliation_overlap_seconds", 10, "at least one poll"),
            ("reconciliation_overlap_seconds", 901, "at most 900"),
            ("webhooks_enabled", True, "webhook"),
        ):
            with self.subTest(field=field):
                payload = self.manifest()
                payload["polling"][field] = value
                report = self.evaluate(payload)
                self.assertFalse(report.ready_for_activation_review)
                self.assertIn(expected, " ".join(report.errors).lower())

    def test_vendor_allowlist_rate_concurrency_and_source_ip_evidence_are_required(self):
        mutations = (
            ("approved_hostnames", [], "approved_hostnames"),
            ("commercial_aws_access_allowed", False, "commercial_aws"),
            ("concurrent_use_allowed", False, "concurrent_use"),
            ("polling_allowed", False, "polling_allowed"),
            ("rate_limit_requests_per_minute", 0, "rate_limit"),
            ("source_ip_requirement", "unknown", "source_ip"),
        )
        for field, value, expected in mutations:
            with self.subTest(field=field):
                payload = self.manifest()
                payload["vendor_evidence"][field] = value
                report = self.evaluate(payload)
                self.assertFalse(report.ready_for_activation_review)
                self.assertIn(expected, " ".join(report.errors))

    def test_write_ack_dispatch_alert_page_tone_and_values_are_forbidden(self):
        for field in (
            "write_enabled", "acknowledgement_enabled", "dispatch_mode",
            "alert_enabled", "page_enabled", "tone_enabled", "password",
        ):
            with self.subTest(field=field):
                payload = self.manifest()
                payload["polling"][field] = False
                with self.assertRaisesRegex(CloudCadPreflightError, "forbidden"):
                    self.evaluate(payload)
        self.network_mock.assert_not_called()

    def test_manifest_path_and_schema_are_closed(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(CloudCadPreflightError, "inside"):
            evaluate_cloud_cad_read_preflight(outside, repository_root=self.repository)
        schema = json.loads(
            (ROOT / "config/cloud_cad_read_preflight.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("vendor_evidence", schema["required"])


if __name__ == "__main__":
    unittest.main()
