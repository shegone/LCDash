"""Fail-closed cloud-mode wiring for advisory AI and optional voice."""

from __future__ import annotations

from typing import Any
from functools import cached_property

import boto3

from app.config.settings import Settings
from app.integrations.cloud_ai import (
    AdvisoryRagRequest,
    AwsPollySpeechProvider,
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    CloudPollyProvider,
    CloudTranscribeProvider,
    PollySpeechRequest,
    PollyVoice,
    TranscribePushToTalkRequest,
)
from app.integrations.cloud_ai.contracts import PushToTalkAudioFormat
from app.integrations.cloud_ai.bedrock_retrieval import (
    ApprovedBedrockRetriever,
    BedrockRetrieveClient,
    CitationOnlyBedrockAdvisory,
    DailyRequestBudget,
    GroundedBedrockAdvisory,
)
from app.integrations.cloud_ai.transcribe_provider import (
    MEDIA_ENCODINGS,
    AwsTranscribeStreamingProvider,
)


CLOUD_TRANSCRIBE_AUDIO_FORMATS = tuple(
    audio_format.value for audio_format in MEDIA_ENCODINGS
)


CLOUD_POLLY_VOICES = (
    {
        "id": PollyVoice.JOANNA.value,
        "label": "Joanna",
        "description": "AWS Polly neural American female",
    },
    {
        "id": PollyVoice.MATTHEW.value,
        "label": "Matthew",
        "description": "AWS Polly neural American male",
    },
)


def cloud_mode_enabled(settings: Settings) -> bool:
    """Keep the legacy on-prem voice stack on its existing code path."""
    return settings.deployment_mode == "synthetic-disconnected"


def build_cloud_ai_config(settings: Settings) -> CloudAiProviderConfig:
    return CloudAiProviderConfig.from_mapping(
        {
            "mode": settings.cloud_ai_mode,
            "tenant_id": settings.tenant_id,
            "knowledge_base_id": settings.cloud_ai_knowledge_base_id,
            "documents_ingested": settings.cloud_ai_documents_ingested,
            "generation_model_id": settings.cloud_ai_generation_model_id,
            "max_output_tokens": settings.cloud_ai_max_output_tokens,
            "retrieval_result_limit": settings.cloud_ai_retrieval_result_limit,
            "retrieval_score_threshold": settings.cloud_ai_retrieval_score_threshold,
            "allowed_s3_prefixes": settings.cloud_ai_allowed_s3_prefixes,
            "polly_voice": settings.cloud_ai_polly_voice,
            "transcribe_language_code": "en-US",
            "voice_enabled": settings.cloud_ai_voice_enabled,
            "action_tools": [],
        }
    )


def _build_polly_provider(
    config: CloudAiProviderConfig,
) -> CloudPollyProvider | None:
    if config.mode.value == "advisory-rag" and config.voice_enabled:
        return AwsPollySpeechProvider(
            max_audio_bytes=config.max_synthesized_audio_bytes,
        )
    return None


def _build_transcribe_provider(
    config: CloudAiProviderConfig,
) -> CloudTranscribeProvider | None:
    if config.mode.value == "advisory-rag" and config.voice_enabled:
        return AwsTranscribeStreamingProvider(
            max_transcript_characters=config.max_transcript_characters,
        )
    return None


def build_cloud_ai_runtime(settings: Settings) -> CloudAiRuntime:
    """Wire optional voice without making a managed-service request at startup."""
    config = build_cloud_ai_config(settings)
    return CloudAiRuntime(
        config,
        transcribe=_build_transcribe_provider(config),
        polly=_build_polly_provider(config),
    )


class LazyBedrockRetrieveClient:
    """Create the Bedrock Agent Runtime client only on the first user request."""

    @cached_property
    def _client(self):
        return boto3.client("bedrock-agent-runtime", region_name="us-east-1")

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.retrieve(**kwargs)


class LazyBedrockConverseClient:
    """Create the Bedrock Runtime client only on the first grounded request."""

    @cached_property
    def _client(self):
        return boto3.client("bedrock-runtime", region_name="us-east-1")

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.converse(**kwargs)


def build_activated_cloud_ai_runtime(
    settings: Settings, *, budget: DailyRequestBudget | None = None
) -> CloudAiRuntime:
    """Wire bounded grounded RAG without provider calls at startup.

    Pass the same ``budget`` given to the streaming advisory path so both
    draw from one daily generation cap instead of two independent ones.
    """
    config = build_cloud_ai_config(settings)
    if not config.documents_ingested:
        return CloudAiRuntime(
            config,
            transcribe=_build_transcribe_provider(config),
            polly=_build_polly_provider(config),
        )
    retriever = ApprovedBedrockRetriever(
        client=LazyBedrockRetrieveClient(),
        knowledge_base_id=config.knowledge_base_id,
        tenant_id=config.tenant_id,
        allowed_s3_prefixes=config.allowed_s3_prefixes,
        result_limit=config.retrieval_result_limit,
        score_threshold=config.retrieval_score_threshold,
        metadata_filtering_enabled=False,
    )
    return CloudAiRuntime(
        config,
        advisory=GroundedBedrockAdvisory(
            retriever,
            converse_client=LazyBedrockConverseClient(),
            model_id=config.generation_model_id,
            max_output_tokens=min(config.max_output_tokens, 400),
            detail_max_output_tokens=1200,
            daily_request_limit=200,
            budget=budget,
        ),
        transcribe=_build_transcribe_provider(config),
        polly=_build_polly_provider(config),
    )


def build_citation_only_runtime(
    settings: Settings, *, retrieve_client: BedrockRetrieveClient
) -> CloudAiRuntime:
    """Future-gated Retrieve-only wiring; constructing it performs no API call."""
    config = build_cloud_ai_config(settings)
    if not config.documents_ingested:
        return CloudAiRuntime(
            config,
            transcribe=_build_transcribe_provider(config),
            polly=_build_polly_provider(config),
        )
    retriever = ApprovedBedrockRetriever(
        client=retrieve_client,
        knowledge_base_id=config.knowledge_base_id,
        tenant_id=config.tenant_id,
        allowed_s3_prefixes=config.allowed_s3_prefixes,
        result_limit=config.retrieval_result_limit,
        score_threshold=config.retrieval_score_threshold,
        metadata_filtering_enabled=False,
    )
    return CloudAiRuntime(
        config,
        advisory=CitationOnlyBedrockAdvisory(retriever),
        transcribe=_build_transcribe_provider(config),
        polly=_build_polly_provider(config),
    )


def cloud_ai_status(
    config: CloudAiProviderConfig, runtime: CloudAiRuntime
) -> dict[str, Any]:
    status = runtime.status
    documents_ready = bool(config.documents_ingested and status.rag_available)
    reason = ""
    if not documents_ready:
        reason = "Approved documents are not ingested."
    tts_reason = (
        ""
        if status.tts_ready
        else "Cloud text-to-speech is unavailable pending provider configuration."
    )
    stt_reason = (
        ""
        if status.stt_ready
        else "Cloud speech-to-text is unavailable pending provider configuration."
    )
    return {
        "provider": "aws-managed-advisory",
        "cloud_mode": True,
        "advisory_only": True,
        "action_tools_enabled": False,
        "persistence_enabled": False,
        "documents_ingested": config.documents_ingested,
        "rag_available": status.rag_available,
        "voice_enabled": status.voice_enabled,
        "connected": status.voice_enabled,
        "disabled_reason": reason,
        "voices": list(CLOUD_POLLY_VOICES),
        "tts": {
            "ready": status.tts_ready,
            "disabled_reason": tts_reason,
            "voice": config.polly_voice.value,
            "model": "Amazon Polly neural",
        },
        "stt": {
            "ready": status.stt_ready,
            "disabled_reason": stt_reason,
            "model": "Amazon Transcribe streaming en-US",
        },
    }


def answer_cloud_advisory(
    runtime: CloudAiRuntime,
    config: CloudAiProviderConfig,
    *,
    request_id: str,
    question: str,
    persona: str = "mae",
    roles: tuple[str, ...] = ("viewer",),
) -> dict[str, Any]:
    response = runtime.answer(
        AdvisoryRagRequest(
            request_id,
            config.tenant_id,
            question,
            persona=persona,
            roles=roles,
            allowed_tools=(),
        )
    )
    return {
        "request_id": response.request_id,
        "answer": response.answer,
        "citations": [
            {
                "source_uri": citation.source_uri,
                "title": citation.title,
                "page": citation.page,
                "section": citation.section,
                "revision": citation.revision,
            }
            for citation in response.citations
        ],
        "denied": response.denied,
        "denial_reason": response.denial_reason,
        "advisory_only": response.advisory_only,
        "action_executed": response.action_executed,
    }


def synthesize_cloud_speech(
    runtime: CloudAiRuntime,
    config: CloudAiProviderConfig,
    *,
    request_id: str,
    text: str,
    voice: str,
) -> bytes:
    try:
        selected_voice = PollyVoice(voice)
    except ValueError as exc:
        raise CloudAiRuntimeUnavailable("polly_voice_not_allowed") from exc
    return runtime.synthesize(
        PollySpeechRequest(request_id, config.tenant_id, text, selected_voice)
    )


def transcribe_cloud_speech(
    runtime: CloudAiRuntime,
    config: CloudAiProviderConfig,
    *,
    request_id: str,
    audio: bytes,
    audio_format: str,
    sample_rate_hz: int,
    duration_seconds: float,
) -> str:
    """Transcribe one bounded push-to-talk clip; never persists or logs it.

    This call blocks while the Transcribe stream completes, so an async caller
    must dispatch it through a worker thread rather than awaiting it inline.
    """
    try:
        selected_format = PushToTalkAudioFormat(audio_format)
    except ValueError as exc:
        raise CloudAiRuntimeUnavailable("transcribe_format_not_allowed") from exc
    if selected_format.value not in CLOUD_TRANSCRIBE_AUDIO_FORMATS:
        raise CloudAiRuntimeUnavailable("transcribe_format_not_allowed")
    try:
        push_to_talk = TranscribePushToTalkRequest(
            request_id,
            config.tenant_id,
            selected_format,
            int(sample_rate_hz),
            float(duration_seconds),
            language_code=config.transcribe_language_code,
        )
    except (TypeError, ValueError) as exc:
        raise CloudAiRuntimeUnavailable("transcribe_request_not_allowed") from exc
    return runtime.transcribe(push_to_talk, audio)
