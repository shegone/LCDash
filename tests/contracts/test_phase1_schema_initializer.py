import unittest

from app.tools.phase1_schema_initializer import (
    APPROVED_OBJECTS,
    DENIED_IDENTIFIERS,
    approved_schema_statements,
    initialize_phase1_schema,
)


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement):
        self.statements.append(statement)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class Phase1SchemaInitializerTests(unittest.TestCase):
    def test_plan_contains_only_approved_synthetic_objects(self):
        plan = "\n".join(approved_schema_statements()).lower()
        for approved in (
            "lcdash_analytics.calls",
            "lcdash_analytics.mae_interactions",
            "lcdash_knowledge.documents",
            "lcdash_knowledge.chunks",
        ):
            self.assertIn(approved, plan)
            self.assertIn(approved, APPROVED_OBJECTS)
        for denied in DENIED_IDENTIFIERS:
            self.assertNotIn(denied, plan)

    def test_plan_excludes_ingestion_realtime_alert_and_output_terms(self):
        plan = "\n".join(approved_schema_statements()).lower()
        for denied in (
            "sync_state",
            "sync_runs",
            "lcdash_realtime",
            "webhook_events",
            "lcdash_alerting",
            "ems_delay",
            "paging",
            "station_alert",
            "cad_message",
            "acknowledgement",
            "subscription",
        ):
            self.assertNotIn(denied, plan)

    def test_all_mutating_schema_statements_are_idempotent(self):
        for statement in approved_schema_statements():
            normalized = " ".join(statement.upper().split())
            self.assertTrue(
                "IF NOT EXISTS" in normalized
                or "CREATE OR REPLACE VIEW" in normalized
                or "ON CONFLICT" in normalized,
                normalized[:120],
            )

    def test_initializer_executes_validated_plan_in_one_transaction(self):
        connection = FakeConnection()
        calls = []

        def connect(database_url, *, connect_timeout):
            calls.append((database_url, connect_timeout))
            return connection

        count = initialize_phase1_schema(
            "postgresql://synthetic:redacted@db.internal:5432/lcdash",
            connect=connect,
        )

        self.assertEqual(calls, [("postgresql://synthetic:redacted@db.internal:5432/lcdash", 10)])
        self.assertEqual(count, len(connection.statements))
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)

    def test_initializer_is_not_wired_into_web_startup(self):
        from pathlib import Path

        main_source = Path("app/main.py").read_text(encoding="utf-8")
        self.assertNotIn("phase1_schema_initializer", main_source)
        self.assertNotIn("initialize_phase1_schema", main_source)


if __name__ == "__main__":
    unittest.main()
