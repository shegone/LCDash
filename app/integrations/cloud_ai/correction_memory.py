"""Tenant-scoped correction-memory contracts with no persistence implementation.

This module accepts only an explicit, compact correction candidate. It has no
whole-chat, CAD-payload, credential, or automatic transcript input surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import re
from typing import Protocol, runtime_checkable

from app.core.tenancy import TENANCY_CONTRACT_VERSION, TenantContext


MAX_CORRECTION_CHARACTERS = 600
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$")
_CREDENTIAL_MARKERS = re.compile(
    r"(?i)(password\s*[:=]|api[_ -]?key\s*[:=]|secret\s*[:=]|"
    r"authorization\s*:\s*bearer|-----begin [a-z ]+private key-----|"
    r"\bAKIA[0-9A-Z]{16}\b|\beyJ[A-Za-z0-9_-]{20,}\.)"
)
_INCIDENT_IDENTIFIERS = re.compile(r"(?i)\bCFS\d{2}-\d{3,}\b")
_TRANSCRIPT_MARKERS = re.compile(r"(?im)^\s*(user|assistant|dispatcher|caller|mae|jack)\s*:")
_LIKELY_PII = re.compile(
    r"(?i)(\b(patient|caller|victim)\s+(name|address|phone)\b|"
    r"\b\d{7,15}\b|\b\d{1,6}\s+[A-Za-z0-9 .'-]+\s+(street|st|road|rd|avenue|ave|lane|ln|drive|dr)\b)"
)


class CloudAssistant(StrEnum):
    MAE = "mae"
    JACK = "jack"


class CorrectionKind(StrEnum):
    EXPLICIT_CORRECTION = "explicit-correction"
    USER_PREFERENCE = "user-preference"


class CorrectionMemoryDenied(PermissionError):
    """Sanitized fail-closed result; never contains candidate content."""


@dataclass(frozen=True, slots=True)
class CorrectionCandidate:
    correction_id: str
    tenant_id: str
    assistant: CloudAssistant
    kind: CorrectionKind
    summary: str
    created_at: datetime
    actor_reference: str
    request_reference: str
    explicit_user_submission: bool = True
    summary_only: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.correction_id):
            raise ValueError("Correction ID must be a bounded opaque identifier.")
        if not isinstance(self.assistant, CloudAssistant) or not isinstance(self.kind, CorrectionKind):
            raise ValueError("Correction assistant and kind must be explicitly allowed.")
        text = self.summary.strip()
        if not text or len(text) > MAX_CORRECTION_CHARACTERS:
            raise ValueError("Correction summaries must contain 1-600 characters.")
        if not self.explicit_user_submission or not self.summary_only:
            raise ValueError("Only explicit compact user correction summaries are accepted.")
        if "\n\n" in text or text.startswith(("{", "[")):
            raise ValueError("Whole conversations and structured payloads are prohibited.")
        if (
            _CREDENTIAL_MARKERS.search(text)
            or _INCIDENT_IDENTIFIERS.search(text)
            or _TRANSCRIPT_MARKERS.search(text)
            or _LIKELY_PII.search(text)
        ):
            raise ValueError("Sensitive, incident, credential, or transcript content is prohibited.")
        if self.created_at.tzinfo is None:
            raise ValueError("Correction timestamps must be timezone-aware.")
        if not _SAFE_ID.fullmatch(self.actor_reference) or not _SAFE_ID.fullmatch(self.request_reference):
            raise ValueError("Audit references must be bounded and non-secret.")


@dataclass(frozen=True, slots=True)
class CorrectionStoreSecurity:
    tenant_partitioned: bool
    encryption_at_rest: bool
    point_in_time_recovery: bool
    automatic_transcript_capture: bool = False


@runtime_checkable
class CorrectionMemoryRepository(Protocol):
    @property
    def security(self) -> CorrectionStoreSecurity: ...

    def put(self, candidate: CorrectionCandidate) -> None: ...

    def list_for_tenant(
        self, tenant_id: str, assistant: CloudAssistant, *, limit: int
    ) -> tuple[CorrectionCandidate, ...]: ...


def _opaque_reference(value: str) -> str:
    return "ref-" + sha256(value.encode("utf-8")).hexdigest()[:32]


def build_correction_candidate(
    context: TenantContext,
    *,
    correction_id: str,
    assistant: CloudAssistant,
    kind: CorrectionKind,
    summary: str,
    created_at: datetime | None = None,
) -> CorrectionCandidate:
    """Build from trusted identity; never accept tenant or actor from request JSON."""
    if not isinstance(context, TenantContext) or context.contract_version != TENANCY_CONTRACT_VERSION:
        raise CorrectionMemoryDenied("Trusted tenant context is required.")
    if not ({"supervisor", "administrator"} & context.roles):
        raise CorrectionMemoryDenied("Correction approval role is required.")
    return CorrectionCandidate(
        correction_id=correction_id,
        tenant_id=context.tenant_id,
        assistant=assistant,
        kind=kind,
        summary=summary.strip(),
        created_at=created_at or datetime.now(UTC),
        actor_reference=_opaque_reference(context.subject),
        request_reference=_opaque_reference(context.request_id),
    )


class TenantCorrectionMemory:
    """Authorize first, then use one injected encrypted tenant repository."""

    def __init__(self, context: TenantContext, repository: CorrectionMemoryRepository) -> None:
        if not isinstance(context, TenantContext):
            raise CorrectionMemoryDenied("Trusted tenant context is required.")
        security = repository.security
        if not (
            security.tenant_partitioned
            and security.encryption_at_rest
            and security.point_in_time_recovery
            and not security.automatic_transcript_capture
        ):
            raise CorrectionMemoryDenied("Correction persistence security gate is incomplete.")
        self._context = context
        self._repository = repository

    def save(self, candidate: CorrectionCandidate) -> None:
        if candidate.tenant_id != self._context.tenant_id:
            raise CorrectionMemoryDenied("Tenant correction boundary mismatch.")
        self._repository.put(candidate)

    def list(self, assistant: CloudAssistant, *, limit: int = 20) -> tuple[CorrectionCandidate, ...]:
        if not 1 <= limit <= 50:
            raise CorrectionMemoryDenied("Correction result limit is invalid.")
        results = self._repository.list_for_tenant(
            self._context.tenant_id, assistant, limit=limit
        )
        if any(item.tenant_id != self._context.tenant_id or item.assistant is not assistant for item in results):
            raise CorrectionMemoryDenied("Repository returned an out-of-scope correction.")
        return tuple(results)
