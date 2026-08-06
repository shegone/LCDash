"""Network-free contracts for candidate cloud correction memory."""

from datetime import UTC, datetime
import unittest

from app.core.tenancy import TenantContext
from app.integrations.cloud_ai.correction_memory import (
    CloudAssistant,
    CorrectionCandidate,
    CorrectionKind,
    CorrectionMemoryDenied,
    CorrectionStoreSecurity,
    TenantCorrectionMemory,
    build_correction_candidate,
)


def context(tenant: str = "logan-synthetic", roles=frozenset({"supervisor"})):
    return TenantContext(
        tenant_id=tenant,
        subject="supervisor@example.invalid",
        identity_source="trusted-test",
        roles=roles,
        request_id="request-correction-1001",
        authenticated_at=datetime(2026, 8, 6, tzinfo=UTC),
    )


class FakeRepository:
    def __init__(self, security=None):
        self.security = security or CorrectionStoreSecurity(True, True, True)
        self.items = []

    def put(self, candidate):
        self.items.append(candidate)

    def list_for_tenant(self, tenant_id, assistant, *, limit):
        return tuple(self.items[:limit])


class CloudCorrectionMemoryTests(unittest.TestCase):
    def candidate(self, tenant="logan-synthetic", assistant=CloudAssistant.MAE):
        return build_correction_candidate(
            context(tenant),
            correction_id="correction-1001",
            assistant=assistant,
            kind=CorrectionKind.EXPLICIT_CORRECTION,
            summary="Prefer the approved county terminology in future advisory answers.",
            created_at=datetime(2026, 8, 6, tzinfo=UTC),
        )

    def test_explicit_compact_correction_is_tenant_scoped_and_audit_safe(self):
        candidate = self.candidate()

        self.assertEqual(candidate.tenant_id, "logan-synthetic")
        self.assertEqual(candidate.assistant, CloudAssistant.MAE)
        self.assertTrue(candidate.actor_reference.startswith("ref-"))
        self.assertNotIn("example.invalid", candidate.actor_reference)
        self.assertTrue(candidate.request_reference.startswith("ref-"))

    def test_both_mae_and_jack_are_explicit_targets(self):
        self.assertEqual(self.candidate(assistant=CloudAssistant.MAE).assistant.value, "mae")
        self.assertEqual(self.candidate(assistant=CloudAssistant.JACK).assistant.value, "jack")

    def test_automatic_or_full_chat_capture_has_no_accepted_shape(self):
        base = dict(
            correction_id="correction-1001",
            tenant_id="logan-synthetic",
            assistant=CloudAssistant.MAE,
            kind=CorrectionKind.EXPLICIT_CORRECTION,
            summary="User: remember this\nAssistant: captured",
            created_at=datetime(2026, 8, 6, tzinfo=UTC),
            actor_reference="ref-12345678",
            request_reference="ref-87654321",
        )
        with self.assertRaises(ValueError):
            CorrectionCandidate(**base)
        with self.assertRaises(ValueError):
            CorrectionCandidate(**{**base, "summary": "compact", "explicit_user_submission": False})

    def test_sensitive_payload_markers_fail_closed(self):
        for text in (
            '{"raw_cad": true}',
            "CFS26-24436 should be remembered",
            "password=do-not-store",
            "Authorization: Bearer token-value",
            "caller phone 3045551212",
            "patient address 100 Main Street",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                build_correction_candidate(
                    context(),
                    correction_id="correction-1001",
                    assistant=CloudAssistant.MAE,
                    kind=CorrectionKind.EXPLICIT_CORRECTION,
                    summary=text,
                )

    def test_unapproved_role_and_insecure_store_fail_before_write(self):
        with self.assertRaises(CorrectionMemoryDenied):
            build_correction_candidate(
                context(roles=frozenset({"viewer"})),
                correction_id="correction-1001",
                assistant=CloudAssistant.MAE,
                kind=CorrectionKind.USER_PREFERENCE,
                summary="Prefer short answers.",
            )
        repository = FakeRepository(CorrectionStoreSecurity(True, False, True))
        with self.assertRaises(CorrectionMemoryDenied):
            TenantCorrectionMemory(context(), repository)
        self.assertEqual(repository.items, [])

    def test_cross_tenant_save_and_repository_results_are_denied(self):
        repository = FakeRepository()
        memory = TenantCorrectionMemory(context(), repository)
        with self.assertRaises(CorrectionMemoryDenied):
            memory.save(self.candidate("northstar-fictional"))
        repository.items.append(self.candidate("northstar-fictional"))
        with self.assertRaises(CorrectionMemoryDenied):
            memory.list(CloudAssistant.MAE)


if __name__ == "__main__":
    unittest.main()
