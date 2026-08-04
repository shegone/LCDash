"""Stable version 1 provider contracts shared by local and managed adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.core.tenancy import TenantContext


PROVIDER_CONTRACT_VERSION = "1.0"


class ModuleCapability(StrEnum):
    SUPERVISOR_DASHBOARD = "supervisor_dashboard"
    CENTRALSQUARE_OPERATIONS = "centralsquare_operations"
    ACTIVE_CALLS = "active_calls"
    CALL_DETAIL = "call_detail"
    UNITS = "units"
    REALTIME_RECONCILIATION = "realtime_reconciliation"
    ANALYTICS = "analytics"
    REPORTS = "reports"
    COUNTY_COMMISSION_REPORT = "county_commission_report"
    HEATMAP = "heatmap"
    GIS = "gis"
    MAE = "mae"
    MAE_ANALYTICS_REPORTS = "mae_analytics_reports"
    MAE_MEMORY_EVALUATION = "mae_memory_evaluation"
    JACK = "jack"
    JACK_MEMORY_EVALUATION = "jack_memory_evaluation"
    KNOWLEDGE = "knowledge"
    KNOWLEDGE_INDEXING = "knowledge_indexing"
    MINDSHARE_RADIO_INTELLIGENCE = "mindshare_radio_intelligence"
    VOICE = "voice"
    AVATAR = "avatar"
    MOBILE = "mobile"
    STATION_ALERTS = "station_alerts"
    EMS_DELAY = "ems_delay"
    CAD_MESSAGES = "cad_messages"
    REALTIME_WEBHOOKS = "realtime_webhooks"
    PAGING = "paging"
    PUBLIC_WARNING = "public_warning"
    NGA911 = "nga911"
    NOVA = "nova"


class CadCapability(StrEnum):
    AUTHENTICATE = "authenticate"
    HEALTH = "health"
    SEARCH_CALLS = "search_calls"
    GET_CALL = "get_call"
    SEARCH_UNITS = "search_units"
    NORMALIZE_EVENT = "normalize_event"
    INGEST_EVENTS = "ingest_events"
    REGISTER_SUBSCRIPTION = "register_subscription"
    UPDATE_CALL = "update_call"
    SEND_MESSAGE = "send_message"
    ACKNOWLEDGE = "acknowledge"


class InferenceCapability(StrEnum):
    CHAT = "chat"
    STREAMING_CHAT = "streaming_chat"
    EMBEDDINGS = "embeddings"
    TOOL_USE = "tool_use"
    GUARDRAILS = "guardrails"


class RetrievalCapability(StrEnum):
    SEARCH = "search"
    DOCUMENT_PASSAGES = "document_passages"
    INDEX = "index"
    STATUS = "status"
    CITATIONS = "citations"
    APPROVED_MEMORY = "approved_memory"


class SpeechToTextCapability(StrEnum):
    BATCH = "batch"
    STREAMING = "streaming"
    LANGUAGE_DETECTION = "language_detection"
    CUSTOM_VOCABULARY = "custom_vocabulary"
    DIARIZATION = "diarization"


class TextToSpeechCapability(StrEnum):
    SYNTHESIZE = "synthesize"
    STREAMING = "streaming"
    SSML = "ssml"
    LEXICONS = "lexicons"
    VOICE_PROFILES = "voice_profiles"


class ProviderError(RuntimeError):
    """Sanitized provider failure safe for application boundaries."""


class CapabilityDenied(ProviderError):
    pass


class TenantBindingError(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderRateLimit(ProviderError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("Synthetic provider rate limit exceeded.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("Page limit must be between 1 and 100.")


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[Any, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    provider: str
    contract_version: str = PROVIDER_CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class NormalizedCall:
    tenant_id: str
    cfs_number: str
    incident_code: str
    incident_description: str
    priority: str
    agency: str
    status: str
    call_datetime: str
    location: str
    assigned_units: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedUnit:
    tenant_id: str
    unit_number: str
    agency: str
    unit_type: str
    status: str
    station: str = ""


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    tenant_id: str
    event_id: str
    event_type: str
    occurred_at: str
    resource_id: str


@dataclass(frozen=True, slots=True)
class AuditEvent:
    tenant_id: str
    provider: str
    operation: str
    outcome: str
    request_id: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    prompt: str
    timeout_ms: int = 5_000
    tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    text: str
    model: str
    finish_reason: str = "complete"


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    document_id: str
    title: str
    content: str
    page_number: int
    citation: str


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    audio: bytes
    media_type: str
    voice: str


@runtime_checkable
class CadProvider(Protocol):
    contract_version: str
    capabilities: frozenset[CadCapability]

    def health(self, context: TenantContext, *, timeout_ms: int) -> ProviderHealth: ...
    def authenticate(self, context: TenantContext, secret_reference: str, *, timeout_ms: int) -> ProviderHealth: ...
    def search_calls(self, context: TenantContext, query: Mapping[str, Any], page: PageRequest, *, timeout_ms: int) -> Page: ...
    def get_call(self, context: TenantContext, cfs_number: str, *, timeout_ms: int) -> NormalizedCall: ...
    def search_units(self, context: TenantContext, query: Mapping[str, Any], page: PageRequest, *, timeout_ms: int) -> Page: ...
    def normalize_event(self, context: TenantContext, source: str, payload: Mapping[str, Any]) -> NormalizedEvent: ...
    def ingest_event(self, context: TenantContext, event: NormalizedEvent, *, timeout_ms: int) -> None: ...
    def register_subscription(self, context: TenantContext, callback_reference: str, *, timeout_ms: int) -> None: ...
    def update_call(self, context: TenantContext, cfs_number: str, changes: Mapping[str, Any], *, timeout_ms: int) -> None: ...
    def send_message(self, context: TenantContext, destination: str, message: str, *, timeout_ms: int) -> None: ...
    def acknowledge(self, context: TenantContext, resource_id: str, *, timeout_ms: int) -> None: ...


@runtime_checkable
class InferenceProvider(Protocol):
    contract_version: str
    capabilities: frozenset[InferenceCapability]

    def generate(self, context: TenantContext, request: InferenceRequest) -> InferenceResponse: ...
    def stream(self, context: TenantContext, request: InferenceRequest) -> Sequence[str]: ...
    def embed(self, context: TenantContext, texts: Sequence[str], *, timeout_ms: int) -> tuple[tuple[float, ...], ...]: ...


@runtime_checkable
class RetrievalProvider(Protocol):
    contract_version: str
    capabilities: frozenset[RetrievalCapability]

    def search(self, context: TenantContext, query: str, page: PageRequest, *, timeout_ms: int) -> Page: ...
    def passages(self, context: TenantContext, document_id: str, page: PageRequest, *, timeout_ms: int) -> Page: ...
    def status(self, context: TenantContext, *, timeout_ms: int) -> ProviderHealth: ...
    def index(self, context: TenantContext, documents: Sequence[Mapping[str, Any]], *, timeout_ms: int) -> int: ...


@runtime_checkable
class SpeechToTextProvider(Protocol):
    contract_version: str
    capabilities: frozenset[SpeechToTextCapability]

    def transcribe(self, context: TenantContext, audio: bytes, *, language: str, timeout_ms: int) -> Transcript: ...
    def stream(self, context: TenantContext, chunks: Sequence[bytes], *, language: str, timeout_ms: int) -> Sequence[Transcript]: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    contract_version: str
    capabilities: frozenset[TextToSpeechCapability]

    def synthesize(self, context: TenantContext, text: str, *, voice: str, timeout_ms: int) -> SynthesizedSpeech: ...
    def stream(self, context: TenantContext, text: str, *, voice: str, timeout_ms: int) -> Sequence[bytes]: ...
