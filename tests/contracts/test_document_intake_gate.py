"""Synthetic-only tests for the local manifest-driven document intake gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from app.tools.document_intake_gate import (
    BUCKET_NAME,
    IntakeManifestError,
    evaluate_document_intake,
)


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"


class DocumentIntakeGateTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.addCleanup(self.temp.cleanup)
        self.test_root = Path(self.temp.name)
        self.repository = self.test_root / "repo"
        self.package = self.repository / "work/document-intake-staging"
        self.package.mkdir(parents=True)
        self.network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("network access blocked"),
        )
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def write_manifest(self, files, admission=None):
        manifest = {
            "schema_version": "lcdash.document-intake.v1",
            "manifest_id": "synthetic-intake-0001",
            "package_root": "work/document-intake-staging",
            "admission": admission or {
                "decision": "approved",
                "approval_id": "synthetic-approval-0001",
                "signed_by": "synthetic-reviewer",
                "signed_at": "2026-08-05T09:00:00-04:00",
                "signature": "synthetic-signature-0001",
                "protected_data_present": False,
                "classification_decision": "no-protected-data-approved",
            },
            "files": files,
        }
        path = self.repository / "intake.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def staged_item(self, name="synthetic-manual.pdf", content=b"synthetic pdf fixture"):
        path = self.package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "staged_path": name,
            "destination_key": (
                "tenants/logan-synthetic/document-library/"
                "mindshare/current/synthetic-manual.pdf"
            ),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "category": "current-manual",
            "malware_scan": "passed",
            "secret_scan": "passed",
            "human_approved": True,
        }

    def evaluate(self, manifest):
        return evaluate_document_intake(manifest, repository_root=self.repository)

    def test_valid_package_produces_dry_run_only_and_ingestion_eligibility(self):
        report = self.evaluate(self.write_manifest([self.staged_item()]))
        self.assertTrue(report.valid)
        self.assertFalse(report.upload_authorized)
        self.assertTrue(report.later_ingestion_eligible)
        self.assertEqual(len(report.dry_run_upload_plan), 1)
        item = report.dry_run_upload_plan[0]
        self.assertEqual(item.bucket, BUCKET_NAME)
        self.assertTrue(item.destination_key.endswith("synthetic-manual.pdf"))
        self.network_mock.assert_not_called()

    def test_hash_size_prefix_type_and_scan_fail_closed(self):
        mutations = (
            ({"sha256": "0" * 64}, "sha256 does not match"),
            ({"bytes": 1}, "size does not match"),
            ({"destination_key": "other-tenant/manual.pdf"}, "outside approved"),
            ({"destination_key": "tenants/logan-synthetic/document-library/mindshare/current/tool.exe"}, "file type"),
            ({"malware_scan": "pending"}, "malware scan"),
            ({"secret_scan": "failed"}, "secret scan"),
        )
        for mutation, message in mutations:
            with self.subTest(mutation=mutation):
                item = self.staged_item()
                item.update(mutation)
                report = self.evaluate(self.write_manifest([item]))
                self.assertFalse(report.valid)
                self.assertFalse(report.later_ingestion_eligible)
                self.assertEqual(report.dry_run_upload_plan, ())
                self.assertIn(message, " ".join(report.errors))

    def test_hard_exclusions_cover_required_categories_and_paths(self):
        exclusions = (
            ("password-manual.pdf", "current-manual"),
            ("backup-manual.pdf", "current-manual"),
            ("manual.pdf", "raw-cad"),
            ("manual.pdf", "recordings"),
            ("model-file.pdf", "current-manual"),
            ("station-alert-procedure.pdf", "current-manual"),
        )
        for name, category in exclusions:
            with self.subTest(name=name, category=category):
                item = self.staged_item(name=name)
                item["category"] = category
                item["destination_key"] = (
                    "tenants/logan-synthetic/document-library/mindshare/current/" + name
                )
                report = self.evaluate(self.write_manifest([item]))
                self.assertFalse(report.valid)
                self.assertFalse(report.upload_authorized)

        renamed = self.staged_item(name="password-source.pdf")
        renamed["destination_key"] = (
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "safe-looking-name.pdf"
        )
        report = self.evaluate(self.write_manifest([renamed]))
        self.assertFalse(report.valid)
        self.assertIn("hard exclusion", " ".join(report.errors))

    def test_prefix_specific_file_types_fail_closed(self):
        for prefix, name in (
            (
                "tenants/logan-synthetic/document-library/centralsquare/current/",
                "synthetic.docx",
            ),
            (
                "tenants/logan-synthetic/document-library/manifests/approved/",
                "synthetic.pdf",
            ),
            (
                "tenants/logan-synthetic/document-library/mindshare/software-catalog/",
                "synthetic.md",
            ),
        ):
            with self.subTest(prefix=prefix):
                item = self.staged_item(name=name)
                item["destination_key"] = prefix + name
                report = self.evaluate(self.write_manifest([item]))
                self.assertFalse(report.valid)
                self.assertIn("destination prefix", " ".join(report.errors))

    def test_signed_admission_and_protected_data_decision_are_mandatory(self):
        base = {
            "decision": "approved",
            "approval_id": "synthetic-approval-0001",
            "signed_by": "synthetic-reviewer",
            "signed_at": "2026-08-05T09:00:00-04:00",
            "signature": "synthetic-signature-0001",
            "protected_data_present": False,
            "classification_decision": "no-protected-data-approved",
        }
        for mutation, expected in (
            ({"decision": "pending"}, "must be approved"),
            ({"signature": ""}, "signature"),
            ({"protected_data_present": True}, "explicitly false"),
            ({"classification_decision": "unknown"}, "classification approval"),
            ({"signed_at": "2026-08-05"}, "timezone"),
        ):
            with self.subTest(mutation=mutation):
                admission = {**base, **mutation}
                report = self.evaluate(
                    self.write_manifest([self.staged_item()], admission=admission)
                )
                self.assertFalse(report.valid)
                self.assertIn(expected, " ".join(report.errors))

    def test_no_discovery_and_no_path_escape(self):
        unlisted = self.package / "unlisted-synthetic.pdf"
        unlisted.write_bytes(b"not inspected")
        report = self.evaluate(self.write_manifest([self.staged_item()]))
        self.assertTrue(report.valid)
        self.assertNotIn("unlisted", json.dumps(report.to_dict()))

        outside = self.test_root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(IntakeManifestError, "inside"):
            evaluate_document_intake(outside, repository_root=self.repository)
        self.network_mock.assert_not_called()

    def test_schema_is_closed_and_declares_dry_gate_fields(self):
        schema = json.loads(
            (ROOT / "config/document_intake.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["package_root"]["const"], "work/document-intake-staging")
        admission = schema["properties"]["admission"]
        self.assertIn("signature", admission["required"])
        self.assertEqual(admission["properties"]["protected_data_present"]["const"], False)


if __name__ == "__main__":
    unittest.main()
