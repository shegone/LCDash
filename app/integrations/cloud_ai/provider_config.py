"""Fail-closed, non-secret configuration for dormant cloud AI providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping

from .contracts import PollyVoice


class CloudAiMode(StrEnum):
    DISABLED = "disabled"
    ADVISORY_RAG = "advisory-rag"


ALLOWED_MODEL_IDS = frozenset(
    {
        "amazon.nova-micro-v1:0",
        "amazon.nova-lite-v1:0",
        "us.amazon.nova-pro-v1:0",
        "us.anthropic.claude-sonnet-5",
    }
)
ALLOWED_CONFIG_KEYS = frozenset(
    {
        "mode",
        "tenant_id",
        "knowledge_base_id",
        "documents_ingested",
        "generation_model_id",
        "max_output_tokens",
        "retrieval_result_limit",
        "retrieval_score_threshold",
        "allowed_s3_prefixes",
        "polly_voice",
        "transcribe_language_code",
        "voice_enabled",
        "max_input_audio_bytes",
        "max_transcript_characters",
        "max_synthesized_audio_bytes",
        "action_tools",
    }
)
TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
KNOWLEDGE_BASE_ID = re.compile(r"^[A-Z0-9]{10}$")


@dataclass(frozen=True, slots=True)
class CloudAiProviderConfig:
    mode: CloudAiMode
    tenant_id: str
    knowledge_base_id: str = ""
    documents_ingested: bool = False
    generation_model_id: str = "amazon.nova-micro-v1:0"
    max_output_tokens: int = 512
    retrieval_result_limit: int = 5
    retrieval_score_threshold: float = 0.5
    allowed_s3_prefixes: tuple[str, ...] = ()
    polly_voice: PollyVoice = PollyVoice.JOANNA
    transcribe_language_code: str = "en-US"
    voice_enabled: bool = False
    max_input_audio_bytes: int = 2_000_000
    max_transcript_characters: int = 4000
    max_synthesized_audio_bytes: int = 5_000_000
    action_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not TENANT_ID.fullmatch(self.tenant_id):
            raise ValueError("Cloud AI configuration requires a stable tenant identifier.")
        if self.action_tools:
            raise ValueError("Cloud AI providers cannot expose CAD or action tools.")
        if self.transcribe_language_code != "en-US":
            raise ValueError("The pilot Transcribe contract is restricted to en-US.")
        if not 1 <= self.max_input_audio_bytes <= 2_000_000:
            raise ValueError("max_input_audio_bytes must be between 1 and 2000000.")
        if not 1 <= self.max_transcript_characters <= 4000:
            raise ValueError("max_transcript_characters must be between 1 and 4000.")
        if not 1 <= self.max_synthesized_audio_bytes <= 5_000_000:
            raise ValueError(
                "max_synthesized_audio_bytes must be between 1 and 5000000."
            )
        if self.mode is CloudAiMode.DISABLED:
            if self.knowledge_base_id:
                raise ValueError("Disabled cloud AI configuration cannot reference a knowledge base.")
            if self.documents_ingested:
                raise ValueError("Disabled cloud AI cannot claim ingested documents.")
            return
        if self.documents_ingested and not KNOWLEDGE_BASE_ID.fullmatch(
            self.knowledge_base_id
        ):
            raise ValueError(
                "Ingested advisory RAG requires an explicit Bedrock knowledge base ID."
            )
        if self.knowledge_base_id and not KNOWLEDGE_BASE_ID.fullmatch(
            self.knowledge_base_id
        ):
            raise ValueError("The Bedrock knowledge base ID is invalid.")
        if self.generation_model_id not in ALLOWED_MODEL_IDS:
            raise ValueError("The generation model is outside the reviewed allowlist.")
        if not 64 <= self.max_output_tokens <= 1200:
            raise ValueError("max_output_tokens must be between 64 and 1200.")
        if not 1 <= self.retrieval_result_limit <= 10:
            raise ValueError("retrieval_result_limit must be between 1 and 10.")
        if not 0 <= self.retrieval_score_threshold <= 1:
            raise ValueError("retrieval_score_threshold must be between 0 and 1.")
        for prefix in self.allowed_s3_prefixes:
            if not prefix.startswith("s3://") or not prefix.endswith("/"):
                raise ValueError("Approved retrieval prefixes must be complete S3 directory URIs.")
        if self.documents_ingested and not self.allowed_s3_prefixes:
            raise ValueError("Ingested advisory RAG requires approved S3 prefixes.")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "CloudAiProviderConfig":
        unknown = set(values) - ALLOWED_CONFIG_KEYS
        if unknown:
            raise ValueError(
                "Unknown cloud AI configuration keys: " + ", ".join(sorted(unknown))
            )
        raw_tools = values.get("action_tools", ())
        if isinstance(raw_tools, str) or not isinstance(raw_tools, (list, tuple)):
            raise ValueError("action_tools must be an empty list.")
        raw_prefixes = values.get("allowed_s3_prefixes", ())
        if isinstance(raw_prefixes, str) or not isinstance(raw_prefixes, (list, tuple)):
            raise ValueError("allowed_s3_prefixes must be a list of S3 directory URIs.")
        return cls(
            mode=CloudAiMode(str(values.get("mode", CloudAiMode.DISABLED))),
            tenant_id=str(values.get("tenant_id", "")),
            knowledge_base_id=str(values.get("knowledge_base_id", "")),
            documents_ingested=values.get("documents_ingested", False) is True,
            generation_model_id=str(
                values.get("generation_model_id", "amazon.nova-micro-v1:0")
            ),
            max_output_tokens=int(values.get("max_output_tokens", 512)),
            retrieval_result_limit=int(values.get("retrieval_result_limit", 5)),
            retrieval_score_threshold=float(values.get("retrieval_score_threshold", 0.5)),
            allowed_s3_prefixes=tuple(
                str(item) for item in raw_prefixes
            ),
            polly_voice=PollyVoice(str(values.get("polly_voice", PollyVoice.JOANNA))),
            transcribe_language_code=str(
                values.get("transcribe_language_code", "en-US")
            ),
            voice_enabled=values.get("voice_enabled", False) is True,
            max_input_audio_bytes=int(values.get("max_input_audio_bytes", 2_000_000)),
            max_transcript_characters=int(
                values.get("max_transcript_characters", 4000)
            ),
            max_synthesized_audio_bytes=int(
                values.get("max_synthesized_audio_bytes", 5_000_000)
            ),
            action_tools=tuple(str(item) for item in raw_tools),
        )


def voice_for_persona(config: CloudAiProviderConfig, persona: str) -> PollyVoice:
    """Resolve the Polly voice a persona speaks with.

    JACK always uses its own distinct voice (Matthew) so the two assistants
    can never be confused, regardless of what a caller requests. Every other
    persona -- MAE, or anything unrecognized -- keeps the prior, unchanged
    behavior: the single deployment-wide operator-configured default.
    """
    if persona == "jack":
        return PollyVoice.MATTHEW
    return config.polly_voice
