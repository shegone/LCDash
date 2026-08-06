"""Network-free contracts for future cloud advisory context adapters."""

from datetime import UTC, datetime
import unittest

from app.integrations.cloud_ai.advisory_context import (
    AdvisoryContextDenied,
    AdvisoryContextItem,
    AdvisoryContextKind,
    AdvisoryContextRequest,
    validate_context_result,
)
from app.integrations.cloud_ai.contracts import AdvisoryCitation
from app.integrations.cloud_ai.correction_memory import CloudAssistant


class CloudAdvisoryContextTests(unittest.TestCase):
    def request(self):
        return AdvisoryContextRequest(
            request_id="request-context-1001",
            tenant_id="logan-synthetic",
            assistant=CloudAssistant.MAE,
            question="What does the approved procedure say?",
            timeout_seconds=2.0,
        )

    def test_document_context_requires_approved_citation(self):
        citation = AdvisoryCitation(
            "s3://approved-private/tenant/logan-synthetic/manual.pdf",
            "Approved manual",
            page=4,
        )
        item = AdvisoryContextItem(
            AdvisoryContextKind.APPROVED_DOCUMENT,
            "Approved private documents",
            "The bounded retrieved passage summary.",
            citations=(citation,),
        )
        self.assertEqual(
            validate_context_result(
                self.request(), (item,), expected_kind=AdvisoryContextKind.APPROVED_DOCUMENT
            ),
            (item,),
        )
        with self.assertRaises(ValueError):
            AdvisoryContextItem(
                AdvisoryContextKind.APPROVED_DOCUMENT,
                "Approved private documents",
                "Uncited text",
            )

    def test_read_only_cad_context_requires_label_and_freshness_not_document_citation(self):
        item = AdvisoryContextItem(
            AdvisoryContextKind.READ_ONLY_CAD,
            "CentralSquare read-only snapshot",
            "Synthetic bounded operational summary.",
            observed_at=datetime(2026, 8, 6, tzinfo=UTC),
        )
        self.assertTrue(item.read_only)
        self.assertEqual(item.citations, ())

    def test_timeout_and_source_boundary_fail_closed(self):
        with self.assertRaises(ValueError):
            AdvisoryContextRequest(
                "request-context-1001",
                "logan-synthetic",
                CloudAssistant.JACK,
                "question",
                timeout_seconds=5.1,
            )
        cad = AdvisoryContextItem(
            AdvisoryContextKind.READ_ONLY_CAD,
            "Read-only CAD snapshot",
            "Synthetic bounded summary.",
            observed_at=datetime(2026, 8, 6, tzinfo=UTC),
        )
        with self.assertRaises(AdvisoryContextDenied):
            validate_context_result(
                self.request(), (cad,), expected_kind=AdvisoryContextKind.APPROVED_DOCUMENT
            )
        with self.assertRaises(ValueError):
            AdvisoryContextRequest(
                "short", "other_tenant", CloudAssistant.JACK, "question"
            )

    def test_request_supports_both_cloud_assistants_without_action_tools(self):
        mae = self.request()
        jack = AdvisoryContextRequest(
            "request-context-1002",
            "logan-synthetic",
            CloudAssistant.JACK,
            "What does the approved radio guide say?",
        )
        self.assertEqual({mae.assistant, jack.assistant}, {CloudAssistant.MAE, CloudAssistant.JACK})
        self.assertFalse(hasattr(mae, "allowed_tools"))


if __name__ == "__main__":
    unittest.main()
