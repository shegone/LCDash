import unittest
from unittest.mock import patch

from app.services.mae_audit_service import (
    record_mae_feedback,
    record_mae_interaction,
)


class FakeAuditRepository:
    def __init__(self, interaction_exists=True):
        self.interaction_exists = interaction_exists
        self.executions = []
        self.committed = False
        self.schema_initialized = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def initialize_schema(self):
        self.schema_initialized = True

    def _execute(self, query, params=None):
        self.executions.append((query, params))

    def _commit(self):
        self.committed = True

    def fetchone(self, query, params=None):
        self.executions.append((query, params))
        return (params[0],) if self.interaction_exists else None


class MAEAuditTests(unittest.TestCase):
    def test_interaction_is_saved_with_sources_evidence_and_entities(self):
        repository = FakeAuditRepository()
        with patch(
            "app.services.mae_audit_service.AnalyticsRepository",
            return_value=repository,
        ):
            result = record_mae_interaction(
                user_email="supervisor@example.com",
                question="What is active?",
                result={
                    "answer": "Three calls.",
                    "model": "LCDash verified read tools",
                    "sources": [{"name": "CentralSquare CAD"}],
                    "evidence": [{"source": "CentralSquare live operations"}],
                    "entities": {"cfs_numbers": ["CFS26-10001"]},
                    "write_access": False,
                },
            )

        self.assertTrue(result["saved"])
        self.assertTrue(result["interaction_id"])
        self.assertTrue(repository.schema_initialized)
        self.assertTrue(repository.committed)
        self.assertEqual(len(repository.executions), 1)

    def test_feedback_is_saved_for_existing_interaction(self):
        repository = FakeAuditRepository()
        with patch(
            "app.services.mae_audit_service.AnalyticsRepository",
            return_value=repository,
        ):
            result = record_mae_feedback(
                interaction_id="11111111-1111-1111-1111-111111111111",
                user_email="supervisor@example.com",
                rating="helpful",
            )

        self.assertTrue(result["saved"])
        self.assertEqual(result["rating"], "helpful")
        self.assertTrue(repository.committed)

    def test_feedback_rejects_unknown_rating(self):
        with self.assertRaises(ValueError):
            record_mae_feedback(
                interaction_id="11111111-1111-1111-1111-111111111111",
                user_email="supervisor@example.com",
                rating="delete_the_answer",
            )


if __name__ == "__main__":
    unittest.main()
