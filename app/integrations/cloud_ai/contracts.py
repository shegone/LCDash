"""Typed network-free contracts for advisory RAG, Transcribe, and Polly."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
import re


PROHIBITED_ACTION_DOMAINS = frozenset(
    {
        "cad",
        "dispatch",
        "acknowledgement",
        "paging",
        "station-alert",
        "public-warning",
        "radio",
        "esinet",
        "ems-delivery",
    }
)
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
MAX_ADVISORY_ANSWER_CHARACTERS = 6000
MAX_ADVISORY_CITATIONS = 10
MAX_TRANSCRIPT_CHARACTERS = 4000


class PollyVoice(StrEnum):
    RUTH = "Ruth"
    STEPHEN = "Stephen"


# The Polly engine used for plain (non-avatar) chat speech. Both current
# voices are generative-only for chat; the avatar's viseme path is
# engine-forced to neural regardless of this mapping -- see
# polly_provider.synthesize_with_visemes.
POLLY_CHAT_ENGINES: dict[PollyVoice, str] = {
    PollyVoice.RUTH: "generative",
    PollyVoice.STEPHEN: "generative",
}

# Voices that support Polly's neural engine, the only engine that emits
# viseme speech marks. Both current voices support neural; this set exists
# so a future voice that lacks neural support entirely fails the avatar
# path loudly (RuntimeError) instead of silently freezing the mouth.
NEURAL_CAPABLE_POLLY_VOICES: frozenset[PollyVoice] = frozenset(
    {PollyVoice.RUTH, PollyVoice.STEPHEN}
)


def polly_engine_for_voice(voice: PollyVoice) -> str:
    """Resolve the Polly engine used for plain (non-avatar) chat speech."""
    return POLLY_CHAT_ENGINES[voice]


def supports_neural_engine(voice: PollyVoice) -> bool:
    """Whether a voice can be synthesized on Polly's neural engine.

    The avatar's viseme path requires this: generative voices do not emit
    viseme speech marks, so the avatar always renders on neural regardless
    of the engine a voice normally uses for chat.
    """
    return voice in NEURAL_CAPABLE_POLLY_VOICES


# Polly's documented viseme inventory for en-US speech marks. The avatar's
# mouth animation maps these to ARKit blendshapes client-side; an identifier
# outside this set is a contract violation, not a new mouth shape.
POLLY_VISEMES = frozenset(
    {"p", "t", "S", "T", "f", "k", "i", "r", "s", "u", "@", "a", "e", "E", "o", "O", "sil"}
)
# Roughly 2400 phonemes fit in the 3000-character text bound; this leaves
# headroom without letting a malfunctioning stream grow without limit.
MAX_VISEME_MARKS = 4000


class PushToTalkAudioFormat(StrEnum):
    PCM = "pcm"
    OGG_OPUS = "ogg-opus"
    WEBM_OPUS = "webm-opus"


def _validate_request_identity(request_id: str, tenant_id: str) -> None:
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("A bounded non-secret request identifier is required.")
    if not TENANT_ID.fullmatch(tenant_id):
        raise ValueError("A stable tenant identifier is required.")


def prepare_polly_text(text: str) -> str:
    """Apply the mandatory 911 pronunciation without changing display text."""
    prepared = re.sub(
        r"\bNGA[\s-]*9[\s-]*1[\s-]*1\b",
        "N G A nine one one",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    return re.sub(r"\b9[\s-]*1[\s-]*1\b", "nine one one", prepared)


@dataclass(frozen=True, slots=True)
class AdvisoryCitation:
    source_uri: str
    title: str
    page: int | None = None
    section: str = ""
    revision: str = ""

    def __post_init__(self) -> None:
        parsed = urlsplit(self.source_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError("Citations require a complete S3 source URI.")
        if not self.title.strip():
            raise ValueError("Citations require a document title.")
        if self.page is not None and self.page < 1:
            raise ValueError("Citation page numbers are one-based.")


@dataclass(frozen=True, slots=True)
class AdvisoryRagRequest:
    request_id: str
    tenant_id: str
    question: str
    allowed_tools: tuple[str, ...] = ()
    persona: str = "mae"
    roles: tuple[str, ...] = ("user",)

    def __post_init__(self) -> None:
        _validate_request_identity(self.request_id, self.tenant_id)
        if not self.question.strip() or len(self.question) > 4000:
            raise ValueError("The advisory question must contain 1-4000 characters.")
        if self.persona not in {"mae", "jack"}:
            raise ValueError("The advisory persona must be MAE or JACK.")
        if not self.roles or any(not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", role) for role in self.roles):
            raise ValueError("Advisory requests require bounded trusted roles.")
        if self.allowed_tools:
            raise ValueError("Advisory RAG cannot expose CAD or action tools.")


@dataclass(frozen=True, slots=True)
class AdvisoryRagResponse:
    request_id: str
    answer: str
    citations: tuple[AdvisoryCitation, ...]
    denied: bool
    denial_reason: str = ""
    advisory_only: bool = True
    action_executed: bool = False

    def __post_init__(self) -> None:
        if not REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("Response request identifier is invalid.")
        if not self.advisory_only or self.action_executed:
            raise ValueError("Cloud AI responses must remain advisory and action-free.")
        if self.denied:
            if self.citations or self.answer.strip() or not self.denial_reason.strip():
                raise ValueError("Denied responses contain only a sanitized denial reason.")
            return
        if not self.answer.strip() or not self.citations or self.denial_reason:
            raise ValueError("Supported advisory answers require text and citations.")
        if len(self.answer) > MAX_ADVISORY_ANSWER_CHARACTERS:
            raise ValueError("Advisory answers exceed the explicit output limit.")
        if len(self.citations) > MAX_ADVISORY_CITATIONS:
            raise ValueError("Advisory answers exceed the citation limit.")

    @classmethod
    def supported(
        cls,
        request_id: str,
        answer: str,
        citations: tuple[AdvisoryCitation, ...],
    ) -> "AdvisoryRagResponse":
        return cls(request_id, answer, citations, denied=False)

    @classmethod
    def deny(cls, request_id: str, reason: str) -> "AdvisoryRagResponse":
        return cls(request_id, "", (), denied=True, denial_reason=reason)


@dataclass(frozen=True, slots=True)
class TranscribePushToTalkRequest:
    request_id: str
    tenant_id: str
    audio_format: PushToTalkAudioFormat
    sample_rate_hz: int
    declared_duration_seconds: float
    language_code: str = "en-US"
    persist_audio: bool = False
    persist_transcript: bool = False

    def __post_init__(self) -> None:
        _validate_request_identity(self.request_id, self.tenant_id)
        if self.language_code != "en-US":
            raise ValueError("Push-to-talk is restricted to en-US.")
        if self.audio_format is PushToTalkAudioFormat.PCM:
            if self.sample_rate_hz not in {8000, 16000}:
                raise ValueError("PCM push-to-talk requires 8 kHz or 16 kHz audio.")
        elif self.sample_rate_hz != 48000:
            raise ValueError("Opus push-to-talk requires 48 kHz audio.")
        if not 0 < self.declared_duration_seconds <= 30:
            raise ValueError("Push-to-talk duration must be at most 30 seconds.")
        if self.persist_audio or self.persist_transcript:
            raise ValueError("Pilot voice requests cannot persist audio or transcripts.")


@dataclass(frozen=True, slots=True)
class PollySpeechRequest:
    request_id: str
    tenant_id: str
    display_text: str
    voice: PollyVoice
    engine: str = "neural"
    output_format: str = "mp3"
    persist_audio: bool = False

    def __post_init__(self) -> None:
        _validate_request_identity(self.request_id, self.tenant_id)
        if not self.display_text.strip() or len(self.display_text) > 3000:
            raise ValueError("Polly text must contain 1-3000 characters.")
        if self.engine != "neural" or self.output_format != "mp3":
            raise ValueError("The pilot Polly contract is neural MP3 only.")
        if self.persist_audio:
            raise ValueError("Pilot synthesized audio cannot be persisted.")

    @property
    def spoken_text(self) -> str:
        return prepare_polly_text(self.display_text)


@dataclass(frozen=True, slots=True)
class PollyVisemeMark:
    """One Polly viseme speech mark: when (audio milliseconds) and which shape."""

    time_ms: int
    viseme: str

    def __post_init__(self) -> None:
        if not isinstance(self.time_ms, int) or not 0 <= self.time_ms <= 600_000:
            raise ValueError("Viseme timestamps must be bounded non-negative milliseconds.")
        if self.viseme not in POLLY_VISEMES:
            raise ValueError("Viseme identifier is not in Polly's documented set.")


@dataclass(frozen=True, slots=True)
class PollySpeechWithVisemes:
    """One synthesized utterance and the mouth-shape timeline that drives it."""

    audio_mp3: bytes
    visemes: tuple[PollyVisemeMark, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.audio_mp3, bytes) or not self.audio_mp3:
            raise ValueError("Avatar speech requires non-empty MP3 audio.")
        if len(self.visemes) > MAX_VISEME_MARKS:
            raise ValueError("Viseme timeline exceeds the bounded mark count.")


@runtime_checkable
class CloudAdvisoryProvider(Protocol):
    def answer(self, request: AdvisoryRagRequest) -> AdvisoryRagResponse: ...


@runtime_checkable
class CloudTranscribeProvider(Protocol):
    def transcribe(self, request: TranscribePushToTalkRequest, audio: bytes) -> str: ...


@runtime_checkable
class CloudPollyProvider(Protocol):
    def synthesize(self, request: PollySpeechRequest) -> bytes: ...


@runtime_checkable
class CloudPollyAvatarProvider(CloudPollyProvider, Protocol):
    """A Polly provider that can also return the viseme timeline.

    Separate from ``CloudPollyProvider`` so existing sentence-speech fakes and
    wiring keep working unchanged; the runtime probes for this capability and
    fails closed when the injected provider lacks it.
    """

    def synthesize_with_visemes(
        self, request: PollySpeechRequest
    ) -> PollySpeechWithVisemes: ...
