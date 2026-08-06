from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "app" / "tools" / "phase2_analytics_import.py"
SPEC = importlib.util.spec_from_file_location("phase2_analytics_import", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Phase 2 analytics importer could not be loaded")
importer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = importer
SPEC.loader.exec_module(importer)


class RecordingCursor:
    def __init__(self, rows_by_table=None):
        self.rows_by_table = rows_by_table or {}
        self.commands = []
        self.rows = []

    def execute(self, sql, parameters=None):
        self.commands.append((sql, parameters))
        self.rows = next(
            (rows for table, rows in self.rows_by_table.items() if f".{table} " in sql),
            [],
        )

    def executemany(self, sql, rows):
        self.commands.append((sql, rows))

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        pass


class RecordingConnection:
    def __init__(self, cursor):
        self.value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Phase2AnalyticsImportTests(unittest.TestCase):
    def test_exact_table_and_field_allowlist(self):
        self.assertEqual(
            [plan.target for plan in importer.TABLE_PLANS],
            [
                "lcdash_analytics.calls",
                "lcdash_analytics.units",
                "lcdash_analytics.call_agency_times",
                "lcdash_analytics.unit_responses",
                "lcdash_analytics.saved_analytics_widgets",
            ],
        )
        all_sql = " ".join(plan.source_sql.lower() for plan in importer.TABLE_PLANS)
        for prohibited in (
            "sync_state", "sync_runs", "webhook", "alert", "payload",
            "narrative", "caller_name", "caller_phone", "recording",
        ):
            self.assertNotIn(prohibited, all_sql)

    def test_full_approved_call_fields_are_preserved_before_target_write(self):
        row = {field: "value" for field in importer.CALL_FIELDS}
        row.update({"latitude": 38.1, "longitude": -81.9})
        validated = importer.validate_row(importer.TABLE_PLANS[0], row)
        self.assertEqual(validated, row)
        self.assertEqual(validated["call_taker_unique_identifier"], "value")
        self.assertEqual(validated["latitude"], 38.1)
        self.assertEqual(validated["longitude"], -81.9)

    def test_join_identifiers_are_preserved_across_exact_allowlisted_tables(self):
        units = next(plan for plan in importer.TABLE_PLANS if plan.name == "units")
        responses = next(
            plan for plan in importer.TABLE_PLANS if plan.name == "unit_responses"
        )
        agency_times = next(
            plan for plan in importer.TABLE_PLANS if plan.name == "call_agency_times"
        )
        self.assertIn("unit_number", units.fields)
        self.assertEqual(units.key_fields, ("unit_number",))
        self.assertIn("cfs_number", responses.fields)
        self.assertIn("unit_number", responses.fields)
        self.assertEqual(responses.key_fields, ("cfs_number", "unit_number"))
        self.assertIn("cfs_number", agency_times.fields)

    def test_sensitive_parity_scope_does_not_expand_prohibited_categories(self):
        approved = set().union(*(set(plan.fields) for plan in importer.TABLE_PLANS))
        for prohibited in (
            "credentials",
            "secrets",
            "raw_cad_payload",
            "recording",
            "caller_name",
            "caller_phone",
            "street_address",
            "incident_narrative",
            "medical_details",
            "operational_output_records",
        ):
            self.assertNotIn(prohibited, approved)

    def test_source_transaction_is_read_only_and_target_is_idempotent(self):
        source_cursor = RecordingCursor()
        target_cursor = RecordingCursor()
        source = RecordingConnection(source_cursor)
        target = RecordingConnection(target_cursor)
        counts = importer.import_window(
            source,
            target,
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            source_cursor.commands[0][0],
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        )
        self.assertEqual(source.commits, 0)
        self.assertEqual(source.rollbacks, 1)
        self.assertEqual(target.commits, 1)
        self.assertTrue(all(value == 0 for value in counts.values()))
        for plan in importer.TABLE_PLANS:
            self.assertIn("ON CONFLICT", plan.upsert_sql)

    def test_unknown_fields_fail_closed(self):
        row = {field: None for field in importer.CALL_FIELDS}
        row["raw_cad_payload"] = "prohibited"
        with self.assertRaisesRegex(ValueError, "Unexpected source field set"):
            importer.validate_row(importer.TABLE_PLANS[0], row)


if __name__ == "__main__":
    unittest.main()
