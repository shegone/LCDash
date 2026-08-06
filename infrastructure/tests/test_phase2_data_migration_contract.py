import importlib.util
import json
from pathlib import Path
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
initializer_path = REPOSITORY_ROOT / "app" / "tools" / "phase1_schema_initializer.py"
initializer_spec = importlib.util.spec_from_file_location(
    "lcdash_phase1_schema_contract", initializer_path
)
if initializer_spec is None or initializer_spec.loader is None:
    raise RuntimeError("Phase 1 schema initializer could not be loaded")
initializer = importlib.util.module_from_spec(initializer_spec)
initializer_spec.loader.exec_module(initializer)
APPROVED_OBJECTS = initializer.APPROVED_OBJECTS
DENIED_IDENTIFIERS = initializer.DENIED_IDENTIFIERS


ROOT = Path(__file__).resolve().parents[1]


class Phase2DataMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(
            (ROOT / "phase2_data_migration_contract.json").read_text(encoding="utf-8")
        )
        cls.plan = (
            REPOSITORY_ROOT / "docs" / "planning" / "PHASE2_DATA_MIGRATION_PLAN.md"
        ).read_text(encoding="utf-8")

    def test_contract_is_phase2_and_not_authorized(self):
        self.assertEqual(self.contract["phase"], "PHASE_2_PRE_ACTIVATION")
        self.assertEqual(self.contract["authorization_status"], "NOT_AUTHORIZED")
        self.assertFalse(self.contract["live_cad_ingestion_authorized"])
        self.assertFalse(self.contract["service_activation_authorized"])
        self.assertIn("PLAN ONLY - NOT AUTHORIZED", self.plan)

    def test_targets_are_only_approved_phase1_base_tables(self):
        targets = set(self.contract["approved_target_tables"])
        self.assertTrue(targets)
        self.assertTrue(targets.issubset(APPROVED_OBJECTS))
        self.assertTrue(targets.isdisjoint(DENIED_IDENTIFIERS))
        self.assertTrue(all(not name.endswith("_metrics") for name in targets))
        self.assertEqual(set(self.contract["idempotency_keys"]), targets)

    def test_operational_and_realtime_objects_are_explicitly_excluded(self):
        excluded = set(self.contract["excluded_objects"])
        self.assertTrue(DENIED_IDENTIFIERS.issubset(excluded))
        for required in (
            "cad_messages",
            "acknowledgements",
            "subscriptions",
            "paging",
            "station_alerts",
            "public_warning",
        ):
            self.assertIn(required, excluded)

    def test_source_is_read_only_and_rollback_is_cloud_only(self):
        self.assertEqual(self.contract["source_mode"], "READ_ONLY_REPEATABLE_SNAPSHOT")
        self.assertTrue(self.contract["source_preservation_required"])
        self.assertTrue(self.contract["historic_snapshot"]["source_transaction_read_only"])
        self.assertTrue(self.contract["final_delta_catchup"]["source_remains_read_only"])
        rollback = self.contract["rollback"]
        self.assertFalse(rollback["source_changes_allowed"])
        self.assertTrue(rollback["delete_cloud_copy_only"])
        self.assertTrue(rollback["cloud_delete_requires_separate_authorization"])

    def test_secrets_and_transport_fail_closed(self):
        secrets = self.contract["secrets"]
        self.assertTrue(secrets["entry_by_authorized_human_only"])
        self.assertFalse(secrets["agent_access_allowed"])
        self.assertFalse(secrets["repository_storage_allowed"])
        transport = self.contract["transport"]
        self.assertTrue(transport["encryption_in_transit_required"])
        self.assertTrue(transport["plaintext_transfer_prohibited"])

    def test_full_parity_scope_and_freshness_evidence_are_mandatory(self):
        fields = self.contract["field_scope"]
        self.assertEqual(fields["policy"], "FULL_APPROVED_ANALYTICS_PARITY")
        self.assertFalse(fields["free_text_allowed"])
        self.assertIn("preserve", fields["calls"]["cfs_number"])
        self.assertIn("preserve", fields["calls"]["call_taker_unique_identifier"])
        self.assertIn("preserve", fields["calls"]["latitude"])
        self.assertIn("preserve", fields["calls"]["longitude"])
        self.assertTrue(fields["identifiers_and_coordinates_are_sensitive"])
        self.assertTrue(fields["private_encrypted_target_required"])
        prohibited = set(fields["prohibited_source_fields"])
        self.assertTrue(
            {"raw_cad_payload", "recording", "credentials", "secrets"}.issubset(
                prohibited
            )
        )
        delta = self.contract["final_delta_catchup"]
        self.assertTrue(delta["required"])
        self.assertTrue(delta["activation_requires_freshness_evidence"])
        evidence = " ".join(self.contract["integrity_evidence"])
        for required in ("row counts", "primary-key", "orphan", "duplicate-key", "watermark"):
            self.assertIn(required, evidence)


if __name__ == "__main__":
    unittest.main()
