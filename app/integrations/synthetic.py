"""Deterministic, network-free providers for tests and demonstrations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.tenancy import TenantContext
from app.integrations.contracts import (
    PROVIDER_CONTRACT_VERSION,
    AuditEvent,
    CadCapability,
    CapabilityDenied,
    InferenceCapability,
    InferenceRequest,
    InferenceResponse,
    NormalizedCall,
    NormalizedEvent,
    NormalizedUnit,
    Page,
    PageRequest,
    ProviderHealth,
    ProviderRateLimit,
    ProviderTimeout,
    RetrievalCapability,
    RetrievalResult,
    SpeechToTextCapability,
    SynthesizedSpeech,
    TenantBindingError,
    TextToSpeechCapability,
    Transcript,
)


_SENSITIVE = re.compile(
    r"(?i)\b(password|passcode|secret|token|api[_ -]?key|caller|patient|narrative)\b\s*[:=]\s*\S+"
)


def redact_text(value: str) -> str:
    return _SENSITIVE.sub(lambda match: f"{match.group(1)}=[REDACTED]", str(value))


class _SyntheticBase:
    contract_version = PROVIDER_CONTRACT_VERSION

    def __init__(
        self,
        tenant: TenantContext,
        *,
        simulated_latency_ms: int = 0,
        rate_limit: int = 100,
    ) -> None:
        self._tenant = tenant
        self._latency_ms = simulated_latency_ms
        self._rate_limit = rate_limit
        self._calls: dict[str, int] = {}
        self._audit: list[AuditEvent] = []

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit)

    def _record(self, context: TenantContext, operation: str, outcome: str, detail: str = "") -> None:
        self._audit.append(
            AuditEvent(
                tenant_id=self._tenant.tenant_id,
                provider=type(self).__name__,
                operation=operation,
                outcome=outcome,
                request_id=context.request_id,
                detail=detail,
            )
        )

    def _guard(self, context: TenantContext, operation: str, capability: Any, timeout_ms: int) -> None:
        if context.tenant_id != self._tenant.tenant_id:
            self._record(context, operation, "denied", "tenant_binding")
            raise TenantBindingError("Provider is bound to a different tenant context.")
        if capability not in self.capabilities:
            self._record(context, operation, "denied", "capability")
            raise CapabilityDenied(f"Capability denied: {capability.value}")
        if timeout_ms <= 0 or self._latency_ms > timeout_ms:
            self._record(context, operation, "timeout")
            raise ProviderTimeout("Synthetic provider deadline exceeded.")
        count = self._calls.get(operation, 0)
        if count >= self._rate_limit:
            self._record(context, operation, "rate_limited")
            raise ProviderRateLimit(retry_after_seconds=1)
        self._calls[operation] = count + 1

    def _success(self, context: TenantContext, operation: str) -> None:
        self._record(context, operation, "success")

    @staticmethod
    def _slice(items: Sequence[Any], page: PageRequest) -> Page:
        try:
            start = int(page.cursor or "0")
        except ValueError as exc:
            raise ValueError("Cursor must be an integer offset.") from exc
        end = start + page.limit
        next_cursor = str(end) if end < len(items) else None
        return Page(items=tuple(items[start:end]), next_cursor=next_cursor)


class SyntheticCadProvider(_SyntheticBase):
    def __init__(
        self,
        tenant: TenantContext,
        *,
        calls: Sequence[Mapping[str, Any]] = (),
        units: Sequence[Mapping[str, Any]] = (),
        capabilities: frozenset[CadCapability] | None = None,
        simulated_latency_ms: int = 0,
        rate_limit: int = 100,
    ) -> None:
        super().__init__(tenant, simulated_latency_ms=simulated_latency_ms, rate_limit=rate_limit)
        self.capabilities = (
            frozenset(
                {
                CadCapability.AUTHENTICATE,
                CadCapability.HEALTH,
                CadCapability.SEARCH_CALLS,
                CadCapability.GET_CALL,
                CadCapability.SEARCH_UNITS,
                CadCapability.NORMALIZE_EVENT,
                }
            )
            if capabilities is None
            else frozenset(capabilities)
        )
        self._calls_data = tuple(self._normalize_call(item) for item in calls)
        self._units_data = tuple(self._normalize_unit(item) for item in units)

    def _normalize_call(self, raw: Mapping[str, Any]) -> NormalizedCall:
        return NormalizedCall(
            tenant_id=self._tenant.tenant_id,
            cfs_number=str(raw.get("cfs_number") or ""),
            incident_code=str(raw.get("incident_code") or "UNKNOWN"),
            incident_description=redact_text(str(raw.get("incident_description") or "Unknown Incident")),
            priority=str(raw.get("priority") or ""),
            agency=str(raw.get("agency") or ""),
            status=str(raw.get("status") or "Open"),
            call_datetime=str(raw.get("call_datetime") or ""),
            location=redact_text(str(raw.get("location") or "Unknown Location")),
            assigned_units=tuple(str(item) for item in raw.get("assigned_units") or ()),
        )

    def _normalize_unit(self, raw: Mapping[str, Any]) -> NormalizedUnit:
        return NormalizedUnit(
            tenant_id=self._tenant.tenant_id,
            unit_number=str(raw.get("unit_number") or ""),
            agency=str(raw.get("agency") or ""),
            unit_type=str(raw.get("unit_type") or ""),
            status=str(raw.get("status") or "Unknown"),
            station=str(raw.get("station") or ""),
        )

    def health(self, context: TenantContext, *, timeout_ms: int) -> ProviderHealth:
        self._guard(context, "health", CadCapability.HEALTH, timeout_ms)
        self._success(context, "health")
        return ProviderHealth(True, "synthetic-cad")

    def authenticate(self, context: TenantContext, secret_reference: str, *, timeout_ms: int) -> ProviderHealth:
        self._guard(context, "authenticate", CadCapability.AUTHENTICATE, timeout_ms)
        if not secret_reference.startswith("synthetic://"):
            self._record(context, "authenticate", "denied", "secret_reference")
            raise CapabilityDenied("Synthetic provider accepts synthetic references only.")
        self._success(context, "authenticate")
        return ProviderHealth(True, "synthetic-cad")

    def search_calls(self, context: TenantContext, query: Mapping[str, Any], page: PageRequest, *, timeout_ms: int) -> Page:
        self._guard(context, "search_calls", CadCapability.SEARCH_CALLS, timeout_ms)
        agency = str(query.get("agency") or "")
        matches = tuple(item for item in self._calls_data if not agency or item.agency == agency)
        result = self._slice(matches, page)
        self._success(context, "search_calls")
        return result

    def get_call(self, context: TenantContext, cfs_number: str, *, timeout_ms: int) -> NormalizedCall:
        self._guard(context, "get_call", CadCapability.GET_CALL, timeout_ms)
        for item in self._calls_data:
            if item.cfs_number == cfs_number:
                self._success(context, "get_call")
                return item
        self._record(context, "get_call", "not_found")
        raise KeyError("Synthetic call not found.")

    def search_units(self, context: TenantContext, query: Mapping[str, Any], page: PageRequest, *, timeout_ms: int) -> Page:
        self._guard(context, "search_units", CadCapability.SEARCH_UNITS, timeout_ms)
        status = str(query.get("status") or "")
        matches = tuple(item for item in self._units_data if not status or item.status == status)
        result = self._slice(matches, page)
        self._success(context, "search_units")
        return result

    def normalize_event(self, context: TenantContext, source: str, payload: Mapping[str, Any]) -> NormalizedEvent:
        self._guard(
            context,
            "normalize_event",
            CadCapability.NORMALIZE_EVENT,
            max(self._latency_ms, 1),
        )
        event = NormalizedEvent(
            tenant_id=self._tenant.tenant_id,
            event_id=str(payload.get("event_id") or ""),
            event_type=str(payload.get("event_type") or source),
            occurred_at=str(payload.get("occurred_at") or ""),
            resource_id=str(payload.get("resource_id") or ""),
        )
        self._success(context, "normalize_event")
        return event

    def ingest_event(self, context: TenantContext, event: NormalizedEvent, *, timeout_ms: int) -> None:
        self._guard(context, "ingest_event", CadCapability.INGEST_EVENTS, timeout_ms)
        self._success(context, "ingest_event")

    def register_subscription(self, context: TenantContext, callback_reference: str, *, timeout_ms: int) -> None:
        self._guard(context, "register_subscription", CadCapability.REGISTER_SUBSCRIPTION, timeout_ms)
        self._success(context, "register_subscription")

    def update_call(self, context: TenantContext, cfs_number: str, changes: Mapping[str, Any], *, timeout_ms: int) -> None:
        self._guard(context, "update_call", CadCapability.UPDATE_CALL, timeout_ms)
        self._success(context, "update_call")

    def send_message(self, context: TenantContext, destination: str, message: str, *, timeout_ms: int) -> None:
        self._guard(context, "send_message", CadCapability.SEND_MESSAGE, timeout_ms)
        self._success(context, "send_message")

    def acknowledge(self, context: TenantContext, resource_id: str, *, timeout_ms: int) -> None:
        self._guard(context, "acknowledge", CadCapability.ACKNOWLEDGE, timeout_ms)
        self._success(context, "acknowledge")


class SyntheticInferenceProvider(_SyntheticBase):
    capabilities = frozenset(
        {InferenceCapability.CHAT, InferenceCapability.STREAMING_CHAT, InferenceCapability.EMBEDDINGS, InferenceCapability.GUARDRAILS}
    )

    def generate(self, context: TenantContext, request: InferenceRequest) -> InferenceResponse:
        self._guard(context, "generate", InferenceCapability.CHAT, request.timeout_ms)
        response = InferenceResponse(redact_text(f"Synthetic response: {request.prompt}"), "synthetic-model")
        self._success(context, "generate")
        return response

    def stream(self, context: TenantContext, request: InferenceRequest) -> Sequence[str]:
        self._guard(context, "stream", InferenceCapability.STREAMING_CHAT, request.timeout_ms)
        result = tuple(redact_text(request.prompt).split())
        self._success(context, "stream")
        return result

    def embed(self, context: TenantContext, texts: Sequence[str], *, timeout_ms: int) -> tuple[tuple[float, ...], ...]:
        self._guard(context, "embed", InferenceCapability.EMBEDDINGS, timeout_ms)
        result = tuple((float(len(redact_text(text))), 1.0) for text in texts)
        self._success(context, "embed")
        return result


class SyntheticRetrievalProvider(_SyntheticBase):
    capabilities = frozenset(
        {RetrievalCapability.SEARCH, RetrievalCapability.DOCUMENT_PASSAGES, RetrievalCapability.STATUS, RetrievalCapability.CITATIONS}
    )

    def __init__(self, tenant: TenantContext, documents: Sequence[Mapping[str, Any]], **kwargs: Any) -> None:
        super().__init__(tenant, **kwargs)
        self._documents = tuple(
            RetrievalResult(
                document_id=str(item.get("document_id") or ""),
                title=str(item.get("title") or "Synthetic document"),
                content=redact_text(str(item.get("content") or "")),
                page_number=int(item.get("page_number") or 1),
                citation=str(item.get("citation") or "Synthetic document, page 1"),
            )
            for item in documents
        )

    def search(self, context: TenantContext, query: str, page: PageRequest, *, timeout_ms: int) -> Page:
        self._guard(context, "search", RetrievalCapability.SEARCH, timeout_ms)
        matches = tuple(item for item in self._documents if query.casefold() in item.content.casefold() or query.casefold() in item.title.casefold())
        result = self._slice(matches, page)
        self._success(context, "search")
        return result

    def passages(self, context: TenantContext, document_id: str, page: PageRequest, *, timeout_ms: int) -> Page:
        self._guard(context, "passages", RetrievalCapability.DOCUMENT_PASSAGES, timeout_ms)
        result = self._slice(tuple(item for item in self._documents if item.document_id == document_id), page)
        self._success(context, "passages")
        return result

    def status(self, context: TenantContext, *, timeout_ms: int) -> ProviderHealth:
        self._guard(context, "status", RetrievalCapability.STATUS, timeout_ms)
        self._success(context, "status")
        return ProviderHealth(True, "synthetic-retrieval")

    def index(self, context: TenantContext, documents: Sequence[Mapping[str, Any]], *, timeout_ms: int) -> int:
        self._guard(context, "index", RetrievalCapability.INDEX, timeout_ms)
        self._success(context, "index")
        return len(documents)


class SyntheticSpeechToTextProvider(_SyntheticBase):
    capabilities = frozenset({SpeechToTextCapability.BATCH, SpeechToTextCapability.STREAMING})

    def transcribe(self, context: TenantContext, audio: bytes, *, language: str, timeout_ms: int) -> Transcript:
        self._guard(context, "transcribe", SpeechToTextCapability.BATCH, timeout_ms)
        result = Transcript("Synthetic transcript", language, len(audio))
        self._success(context, "transcribe")
        return result

    def stream(self, context: TenantContext, chunks: Sequence[bytes], *, language: str, timeout_ms: int) -> Sequence[Transcript]:
        self._guard(context, "stream", SpeechToTextCapability.STREAMING, timeout_ms)
        result = tuple(Transcript(f"Synthetic transcript {index + 1}", language, len(chunk)) for index, chunk in enumerate(chunks))
        self._success(context, "stream")
        return result


class SyntheticTextToSpeechProvider(_SyntheticBase):
    capabilities = frozenset({TextToSpeechCapability.SYNTHESIZE, TextToSpeechCapability.STREAMING, TextToSpeechCapability.VOICE_PROFILES})

    def synthesize(self, context: TenantContext, text: str, *, voice: str, timeout_ms: int) -> SynthesizedSpeech:
        self._guard(context, "synthesize", TextToSpeechCapability.SYNTHESIZE, timeout_ms)
        audio = f"SYNTHETIC:{redact_text(text)}".encode()
        self._success(context, "synthesize")
        return SynthesizedSpeech(audio, "audio/x-synthetic", voice)

    def stream(self, context: TenantContext, text: str, *, voice: str, timeout_ms: int) -> Sequence[bytes]:
        self._guard(context, "stream", TextToSpeechCapability.STREAMING, timeout_ms)
        result = tuple(f"SYNTHETIC:{redact_text(word)}".encode() for word in text.split())
        self._success(context, "stream")
        return result
