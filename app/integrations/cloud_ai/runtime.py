"""Disabled-by-default orchestration for advisory cloud AI and optional voice."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import (
    AdvisoryRagRequest,
    AdvisoryRagResponse,
    CloudAdvisoryProvider,
    CloudPollyProvider,
    CloudTranscribeProvider,
    PollySpeechRequest,
    TranscribePushToTalkRequest,
)
from .provider_config import CloudAiMode, CloudAiProviderConfig


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class CloudAiRuntimeUnavailable(RuntimeError):
    """Sanitized fail-closed category; never includes provider payloads."""


@dataclass(frozen=True, slots=True)
class CloudAiRuntimeStatus:
    advisory_enabled: bool
    rag_available: bool
    voice_enabled: bool
    tts_ready: bool
    stt_ready: bool
    persistence_enabled: bool = False
    action_tools_enabled: bool = False


class CloudAiRuntime:
    """Coordinates injected providers without exposing tools or persistence."""

    def __init__(
        self,
        config: CloudAiProviderConfig,
        *,
        advisory: CloudAdvisoryProvider | None = None,
        transcribe: CloudTranscribeProvider | None = None,
        polly: CloudPollyProvider | None = None,
    ) -> None:
        self._config = config
        self._advisory = advisory
        self._transcribe = transcribe
        self._polly = polly

    @property
    def status(self) -> CloudAiRuntimeStatus:
        advisory_enabled = self._config.mode is CloudAiMode.ADVISORY_RAG
        documents_ready = advisory_enabled and self._config.documents_ingested
        voice_eligible = advisory_enabled and self._config.voice_enabled
        tts_ready = voice_eligible and self._polly is not None
        stt_ready = voice_eligible and self._transcribe is not None
        return CloudAiRuntimeStatus(
            advisory_enabled=advisory_enabled,
            rag_available=documents_ready,
            voice_enabled=tts_ready or stt_ready,
            tts_ready=tts_ready,
            stt_ready=stt_ready,
        )

    def answer(self, request: AdvisoryRagRequest) -> AdvisoryRagResponse:
        if request.tenant_id != self._config.tenant_id:
            return AdvisoryRagResponse.deny(request.request_id, "Tenant is not authorized.")
        if self._config.mode is CloudAiMode.DISABLED:
            return AdvisoryRagResponse.deny(
                request.request_id, "Cloud advisory service is disabled."
            )
        if not self._config.documents_ingested:
            return AdvisoryRagResponse.deny(
                request.request_id, "Approved documents are not ingested."
            )
        if self._advisory is None:
            return AdvisoryRagResponse.deny(
                request.request_id, "Cloud advisory provider is unavailable."
            )
        try:
            return self._advisory.answer(request)
        except Exception as exc:
            raise CloudAiRuntimeUnavailable("advisory_provider_failed") from exc

    def transcribe(
        self, request: TranscribePushToTalkRequest, audio: bytes
    ) -> str:
        if request.tenant_id != self._config.tenant_id:
            raise CloudAiRuntimeUnavailable("tenant_not_authorized")
        if (
            not self._config.voice_enabled
            or self._transcribe is None
        ):
            raise CloudAiRuntimeUnavailable("transcribe_unavailable")
        if not audio or len(audio) > self._config.max_input_audio_bytes:
            raise CloudAiRuntimeUnavailable("audio_input_limit")
        try:
            transcript = _CONTROL_CHARACTERS.sub("", self._transcribe.transcribe(request, audio)).strip()
        except Exception as exc:
            raise CloudAiRuntimeUnavailable("transcribe_provider_failed") from exc
        if not transcript or len(transcript) > self._config.max_transcript_characters:
            raise CloudAiRuntimeUnavailable("transcript_output_limit")
        return transcript

    def synthesize(self, request: PollySpeechRequest) -> bytes:
        if request.tenant_id != self._config.tenant_id:
            raise CloudAiRuntimeUnavailable("tenant_not_authorized")
        if (
            not self._config.voice_enabled
            or self._polly is None
        ):
            raise CloudAiRuntimeUnavailable("polly_unavailable")
        try:
            audio = self._polly.synthesize(request)
        except Exception as exc:
            raise CloudAiRuntimeUnavailable("polly_provider_failed") from exc
        if not audio or len(audio) > self._config.max_synthesized_audio_bytes:
            raise CloudAiRuntimeUnavailable("audio_output_limit")
        return audio
