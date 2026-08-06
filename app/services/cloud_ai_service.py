"""Fail-closed cloud-mode wiring for advisory AI and optional voice."""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.integrations.cloud_ai import (
    AdvisoryRagRequest,
    AwsPollySpeechProvider,
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    CloudPollyProvider,
    PollySpeechRequest,
    PollyVoice,
)
from app.integrations.cloud_ai.bedrock_retrieval import (
    ApprovedBedrockRetriever,
    BedrockRetrieveClient,
    CitationOnlyBedrockAdvisory,
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


def build_cloud_ai_runtime(settings: Settings) -> CloudAiRuntime:
    """Wire optional TTS without making a managed-service request at startup."""
    config = build_cloud_ai_config(settings)
    # Transcribe remains intentionally absent pending its separate provider gate.
    return CloudAiRuntime(config, polly=_build_polly_provider(config))


def build_citation_only_runtime(
    settings: Settings, *, retrieve_client: BedrockRetrieveClient
) -> CloudAiRuntime:
    """Future-gated Retrieve-only wiring; constructing it performs no API call."""
    config = build_cloud_ai_config(settings)
    if not config.documents_ingested:
        return CloudAiRuntime(config, polly=_build_polly_provider(config))
    retriever = ApprovedBedrockRetriever(
        client=retrieve_client,
        knowledge_base_id=config.knowledge_base_id,
        tenant_id=config.tenant_id,
        allowed_s3_prefixes=config.allowed_s3_prefixes,
        result_limit=config.retrieval_result_limit,
        score_threshold=config.retrieval_score_threshold,
    )
    return CloudAiRuntime(
        config,
        advisory=CitationOnlyBedrockAdvisory(retriever),
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
        else "Cloud transcription remains disabled pending a separate activation review."
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
) -> dict[str, Any]:
    response = runtime.answer(
        AdvisoryRagRequest(request_id, config.tenant_id, question, allowed_tools=())
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
