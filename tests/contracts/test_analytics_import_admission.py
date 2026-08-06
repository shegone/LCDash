"""Synthetic, network-free analytics import admission tests."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from app.tools.analytics_import_admission import (
    APPROVED_FIELD_POLICY,
    APPROVED_SENSITIVE_PARITY_FIELDS,
    APPROVED_DERIVED_VIEWS,
    APPROVED_FIELDS,
    APPROVED_KEYS,
    APPROVED_TABLES,
    AnalyticsAdmissionError,
    evaluate_analytics_import_admission,
)


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
CHECKSUM = "a" * 64


class AnalyticsImportAdmissionTests(unittest.TestCase):
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
        return {
            "schema_version": "lcdash.analytics-import-admission.v1",
            "manifest_id": "synthetic-export-0001",
            "source": {
                "mode": "database-enforced-read-only",
                "transaction": "repeatable-read-read-only",
                "identity_reference": "synthetic-readonly-role-reference",
                "authoritative": True,
                "preservation_required": True,
            },
            "target": {
                "account_id": "862772137583",
                "region": "us-east-1",
                "tenant_id": "logan-synthetic",
                "database": "lcdash",
                "schema": "lcdash_analytics",
                "identity_arn": "arn:aws:iam::862772137583:role/synthetic-analytics-import",
                "encrypted": True,
                "tls_required": True,
            },
            "window": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-02-01T00:00:00Z",
                "consistent_watermark": "2026-02-01T00:00:00Z",
                "timezone": "UTC",
            },
            "tables": [
                {
                    "name": name,
                    "fields": list(APPROVED_FIELDS[name]),
                    "key_fields": list(APPROVED_KEYS[name]),
                    "row_count": 3,
                    "primary_key_distinct_count": 3,
                    "checksum_sha256": CHECKSUM,
                    "minimum_timestamp": "2026-01-01T00:00:00Z",
                    "maximum_timestamp": "2026-01-31T23:59:59Z",
                }
                for name in APPROVED_TABLES
            ],
            "derived_views": [
                {
                    "name": name,
                    "mode": "derive-on-target-not-copied",
                    "definition_checksum_sha256": CHECKSUM,
                }
                for name in APPROVED_DERIVED_VIEWS
            ],
            "export_evidence": {
                "manifest_checksum_sha256": CHECKSUM,
                "generated_at": "2026-02-01T00:05:00Z",
                "generator_reference": "synthetic-exporter-v1",
                "rejected_row_count": 0,
            },
        }

    def write(self, payload):
        path = self.repository / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def evaluate(self, payload):
        return evaluate_analytics_import_admission(
            self.write(payload), repository_root=self.repository
        )

    def test_exact_allowlist_emits_dry_run_and_parity_checklist(self):
        report = self.evaluate(self.manifest())
        self.assertTrue(report.valid)
        self.assertFalse(report.execution_authorized)
        self.assertEqual(
            [item.logical_table for item in report.dry_run_migration_plan],
            list(APPROVED_TABLES),
        )
        checklist = " ".join(report.post_import_parity_checklist).lower()
        for term in ("row count", "checksum", "orphan", "watermark", "read-only", "cad writes"):
            self.assertIn(term, checklist)
        self.network_mock.assert_not_called()

    def test_admission_preserves_explicitly_authorized_parity_fields(self):
        self.assertEqual(
            APPROVED_FIELD_POLICY,
            "full-approved-operational-analytics-parity",
        )
        calls = APPROVED_FIELDS["lcdash_analytics.calls"]
        for field in (
            "cfs_number",
            "call_taker",
            "call_taker_unique_identifier",
            "latitude",
            "longitude",
        ):
            self.assertIn(field, calls)
        self.assertIn(
            "unit_number",
            APPROVED_FIELDS["lcdash_analytics.units"],
        )
        self.assertIn(
            "unit_number",
            APPROVED_FIELDS["lcdash_analytics.unit_responses"],
        )
        self.assertEqual(
            set(APPROVED_SENSITIVE_PARITY_FIELDS),
            {
                "cfs_number",
                "unit_number",
                "call_taker",
                "call_taker_unique_identifier",
                "latitude",
                "longitude",
            },
        )

    def test_table_view_and_field_allowlists_fail_closed(self):
        for mutation, expected in (
            (("table", "lcdash_alerting.pages"), "table allowlist"),
            (("view", "lcdash_analytics.unreviewed_view"), "view allowlist"),
            (("field", "raw_cad_payload"), "prohibited field category"),
        ):
            with self.subTest(mutation=mutation):
                payload = self.manifest()
                kind, value = mutation
                if kind == "table":
                    payload["tables"][0]["name"] = value
                elif kind == "view":
                    payload["derived_views"][0]["name"] = value
                else:
                    payload["tables"][0]["fields"].append(value)
                if kind == "field":
                    with self.assertRaisesRegex(AnalyticsAdmissionError, expected):
                        self.evaluate(payload)
                else:
                    report = self.evaluate(payload)
                    self.assertFalse(report.valid)
                    self.assertIn(expected, " ".join(report.errors))

    def test_source_target_window_count_and_checksum_evidence_are_required(self):
        mutations = (
            (("source", "mode"), "unverified", "source mode"),
            (("target", "account_id"), "111111111111", "target account_id"),
            (("window", "timezone"), "America/New_York", "timezone"),
            (("table", "row_count"), 4, "distinct count"),
            (("table", "checksum_sha256"), "bad", "checksum"),
        )
        for (section, key), value, expected in mutations:
            with self.subTest(section=section, key=key):
                payload = self.manifest()
                target = payload["tables"][0] if section == "table" else payload[section]
                target[key] = value
                report = self.evaluate(payload)
                self.assertFalse(report.valid)
                self.assertFalse(report.execution_authorized)
                self.assertEqual(report.dry_run_migration_plan, ())
                self.assertIn(expected, " ".join(report.errors))

    def test_prohibited_manifest_categories_are_rejected_without_echo(self):
        for field in (
            "credentials", "backup_path", "binary_package", "model_file",
            "operational_output", "raw_cad_payload", "recording",
        ):
            with self.subTest(field=field):
                payload = self.manifest()
                payload["export_evidence"][field] = "synthetic"
                with self.assertRaisesRegex(AnalyticsAdmissionError, "prohibited"):
                    self.evaluate(payload)

    def test_manifest_path_must_stay_inside_explicit_repository(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(AnalyticsAdmissionError, "inside"):
            evaluate_analytics_import_admission(
                outside, repository_root=self.repository
            )
        self.network_mock.assert_not_called()

    def test_existing_importer_is_not_invoked_or_modified(self):
        with patch(
            "app.tools.phase2_analytics_import.import_window",
            side_effect=AssertionError("importer invoked by admission gate"),
        ) as importer:
            report = self.evaluate(self.manifest())
        self.assertTrue(report.valid)
        importer.assert_not_called()

    def test_schema_is_closed_and_requires_evidence_sections(self):
        schema = json.loads(
            (ROOT / "config/analytics_import_admission.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        for required in ("source", "target", "window", "tables", "derived_views", "export_evidence"):
            self.assertIn(required, schema["required"])


if __name__ == "__main__":
    unittest.main()
