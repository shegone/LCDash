"""Safe local seams for later document and read-only CAD advisory context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Protocol, runtime_checkable

from .contracts import AdvisoryCitation
from .correction_memory import CloudAssistant


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class AdvisoryContextDenied(RuntimeError):
    """Sanitized denial that carries no provider or CAD payload."""


class AdvisoryContextKind(StrEnum):
    APPROVED_DOCUMENT = "approved-document"
    READ_ONLY_CAD = "read-only-cad"


@dataclass(frozen=True, slots=True)
class AdvisoryContextRequest:
    request_id: str
    tenant_id: str
    assistant: CloudAssistant
    question: str
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not _REQUEST_ID.fullmatch(self.request_id) or not _TENANT_ID.fullmatch(self.tenant_id):
            raise ValueError("Context request requires bounded request and tenant identifiers.")
        if not isinstance(self.assistant, CloudAssistant):
            raise ValueError("Context request assistant must be explicitly allowed.")
        if not self.question.strip() or len(self.question) > 4000:
            raise ValueError("Context questions must contain 1-4000 characters.")
        if not 0.1 <= self.timeout_seconds <= 5.0:
            raise ValueError("Context adapters require an explicit timeout of at most five seconds.")


@dataclass(frozen=True, slots=True)
class AdvisoryContextItem:
    kind: AdvisoryContextKind
    source_label: str
    summary: str
    observed_at: datetime | None = None
    citations: tuple[AdvisoryCitation, ...] = ()
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.read_only or not self.source_label.strip():
            raise ValueError("Advisory context must be labeled and read-only.")
        if not self.summary.strip() or len(self.summary) > 2000:
            raise ValueError("Advisory context summaries must contain 1-2000 characters.")
        if self.kind is AdvisoryContextKind.APPROVED_DOCUMENT and not self.citations:
            raise ValueError("Approved document context requires citations.")
        if self.kind is AdvisoryContextKind.READ_ONLY_CAD:
            if self.citations or self.observed_at is None or self.observed_at.tzinfo is None:
                raise ValueError("CAD context requires a timezone-aware observation and no document citation.")


@runtime_checkable
class ApprovedDocumentContextAdapter(Protocol):
    def retrieve(self, request: AdvisoryContextRequest) -> tuple[AdvisoryContextItem, ...]: ...


@runtime_checkable
class ReadOnlyCadContextAdapter(Protocol):
    def summarize(self, request: AdvisoryContextRequest) -> tuple[AdvisoryContextItem, ...]: ...


def validate_context_result(
    request: AdvisoryContextRequest,
    items: tuple[AdvisoryContextItem, ...],
    *,
    expected_kind: AdvisoryContextKind,
) -> tuple[AdvisoryContextItem, ...]:
    """Fail closed on unlabeled, wrong-kind, oversized, or cross-contract results."""
    if len(items) > 10:
        raise AdvisoryContextDenied("Advisory context result limit exceeded.")
    if any(item.kind is not expected_kind for item in items):
        raise AdvisoryContextDenied("Advisory context source boundary mismatch.")
    return items
