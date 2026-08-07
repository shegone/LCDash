"""Retrieve-only Bedrock adapter with strict source and citation validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
import re
from threading import Lock
from typing import Any, Protocol
from urllib.parse import urlsplit

from .contracts import AdvisoryCitation, AdvisoryRagRequest, AdvisoryRagResponse


class DailyRequestBudget:
    """One shared daily generation cap for every advisory entry point.

    The whole-answer and streaming paths are constructed independently, so
    without a shared budget each would enforce its own 200-request cap and
    the effective daily limit would silently double.
    """

    def __init__(self, daily_limit: int = 200) -> None:
        if not 1 <= daily_limit <= 200:
            raise ValueError("Daily budget must be between 1 and 200 requests.")
        self._daily_limit = daily_limit
        self._lock = Lock()
        self._day = date.today()
        self._count = 0

    def reserve(self) -> bool:
        with self._lock:
            today = date.today()
            if today != self._day:
                self._day = today
                self._count = 0
            if self._count >= self._daily_limit:
                return False
            self._count += 1
            return True


class BedrockRetrieveClient(Protocol):
    def retrieve(self, **kwargs: Any) -> dict[str, Any]: ...


class BedrockConverseClient(Protocol):
    def converse(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    text: str
    score: float
    citation: AdvisoryCitation


class ApprovedBedrockRetriever:
    """Semantic retrieval only; performs no generation or operational action."""

    def __init__(self, *, client: BedrockRetrieveClient, knowledge_base_id: str,
                 tenant_id: str, allowed_s3_prefixes: tuple[str, ...],
                 result_limit: int = 5, score_threshold: float = 0.5,
                 metadata_filtering_enabled: bool = False) -> None:
        if not knowledge_base_id or not allowed_s3_prefixes:
            raise ValueError("Retriever requires a KB ID and approved S3 prefixes.")
        if any(not prefix.startswith("s3://") or not prefix.endswith("/")
               for prefix in allowed_s3_prefixes):
            raise ValueError("Retriever prefixes must be complete S3 directory URIs.")
        if not 1 <= result_limit <= 10 or not 0 <= score_threshold <= 1:
            raise ValueError("Retriever bounds are invalid.")
        self._client = client
        self._knowledge_base_id = knowledge_base_id
        self._tenant_id = tenant_id
        self._prefixes = allowed_s3_prefixes
        self._limit = result_limit
        self._threshold = score_threshold
        self._metadata_filtering_enabled = metadata_filtering_enabled

    def _persona_prefixes(self, persona: str) -> tuple[str, ...]:
        if persona == "jack":
            return tuple(prefix for prefix in self._prefixes if "/mindshare/" in prefix)
        return self._prefixes

    def retrieve(self, *, tenant_id: str, question: str,
                 persona: str = "mae",
                 roles: tuple[str, ...] = ("viewer",)) -> tuple[RetrievedPassage, ...]:
        clean = question.strip()
        if (tenant_id != self._tenant_id or persona not in {"mae", "jack"}
                or not roles or not clean or len(clean) > 4000):
            return ()
        vector_config: dict[str, Any] = {
            "numberOfResults": self._limit,
            "overrideSearchType": "SEMANTIC",
        }
        if self._metadata_filtering_enabled:
            role_items = [
                {"equals": {"key": "role", "value": role}}
                for role in sorted(set(roles))
            ]
            role_filter = role_items[0] if len(role_items) == 1 else {"orAll": role_items}
            vector_config["filter"] = {"andAll": [
                {"equals": {"key": "tenant_id", "value": tenant_id}},
                {"equals": {"key": "persona", "value": persona}},
                role_filter,
            ]}
        response = self._client.retrieve(
            knowledgeBaseId=self._knowledge_base_id,
            retrievalQuery={"text": clean},
            retrievalConfiguration={"vectorSearchConfiguration": vector_config},
        )
        passages = []
        persona_prefixes = self._persona_prefixes(persona)
        for item in response.get("retrievalResults", ()):
            uri = (((item.get("location") or {}).get("s3Location") or {}).get("uri") or "")
            if not any(uri.startswith(prefix) for prefix in persona_prefixes):
                continue
            score = float(item.get("score") or 0)
            text = str((item.get("content") or {}).get("text") or "").strip()
            if score < self._threshold or not text:
                continue
            metadata = item.get("metadata") or {}
            if self._metadata_filtering_enabled:
                if metadata.get("tenant_id") != tenant_id:
                    continue
                if metadata.get("persona") != persona:
                    continue
                if metadata.get("role") not in roles:
                    continue
            title = str(metadata.get("title") or PurePosixPath(urlsplit(uri).path).name)
            page = metadata.get("page_number")
            page = int(page) if isinstance(page, (int, float)) and page >= 1 else None
            passages.append(RetrievedPassage(
                text=text,
                score=score,
                citation=AdvisoryCitation(uri, title, page=page,
                    section=str(metadata.get("section") or ""),
                    revision=str(metadata.get("revision") or "")),
            ))
        return tuple(passages[:self._limit])


class CitationOnlyBedrockAdvisory:
    """Returns bounded retrieved excerpts verbatim; never invokes a generator."""

    def __init__(self, retriever: ApprovedBedrockRetriever) -> None:
        self._retriever = retriever

    def answer(self, request: AdvisoryRagRequest) -> AdvisoryRagResponse:
        passages = self._retriever.retrieve(
            tenant_id=request.tenant_id,
            persona=request.persona,
            roles=request.roles,
            question=request.question,
        )
        if not passages:
            return AdvisoryRagResponse.deny(
                request.request_id,
                "No approved cited source supports this request.",
            )
        answer = "\n\n".join(passage.text for passage in passages)[:6000].strip()
        citations = tuple(passage.citation for passage in passages)
        return AdvisoryRagResponse.supported(request.request_id, answer, citations)


class GroundedBedrockAdvisory:
    """Retrieve approved passages, then synthesize bounded natural-language text."""

    def __init__(self, retriever: ApprovedBedrockRetriever, *,
                 converse_client: BedrockConverseClient, model_id: str,
                 max_output_tokens: int = 400, detail_max_output_tokens: int = 1200,
                 daily_request_limit: int = 200,
                 budget: DailyRequestBudget | None = None) -> None:
        if not 64 <= max_output_tokens <= 400:
            raise ValueError("Grounded generation requires a 64-400 default token cap.")
        if not max_output_tokens <= detail_max_output_tokens <= 1200:
            raise ValueError("Detailed grounded generation is capped at 1200 tokens.")
        if not 1 <= daily_request_limit <= 200:
            raise ValueError("Grounded generation requires a 1-200 daily request cap.")
        self._retriever = retriever
        self._client = converse_client
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._detail_max_output_tokens = detail_max_output_tokens
        # A caller may inject a budget shared with another entry point (e.g.
        # the streaming advisory path) so both draw from one daily cap.
        self._budget = budget or DailyRequestBudget(daily_request_limit)

    def _reserve_request(self) -> bool:
        return self._budget.reserve()

    @staticmethod
    def _clean_answer(text: str) -> str:
        answer = re.sub(r"https?://\S+|s3://\S+", "", text, flags=re.IGNORECASE)
        answer = re.split(r"^\s*Sources\s*$", answer, maxsplit=1, flags=re.MULTILINE)[0]
        return answer.strip()[:6000]

    def answer(self, request: AdvisoryRagRequest) -> AdvisoryRagResponse:
        passages = self._retriever.retrieve(
            tenant_id=request.tenant_id, persona=request.persona,
            roles=request.roles, question=request.question,
        )
        if not passages:
            return AdvisoryRagResponse.deny(
                request.request_id, "No approved source supports this request."
            )
        if not self._reserve_request():
            return AdvisoryRagResponse.deny(
                request.request_id, "The daily advisory usage limit has been reached."
            )
        context = "\n\n".join(
            f"Approved excerpt {index}:\n{passage.text}"
            for index, passage in enumerate(passages, start=1)
        )[:12000]
        wants_detail = bool(re.search(
            r"\b(more detail|more detailed|explain fully|in depth|step[- ]by[- ]step)\b",
            request.question,
            flags=re.IGNORECASE,
        ))
        response = self._client.converse(
            modelId=self._model_id,
            system=[{"text": (
                "You are MAE/JACK, an advisory public-safety documentation assistant. "
                "Answer naturally and concisely using only the approved excerpts. "
                "Lead with a brief direct answer and offer more detail on request. "
                "Never invent facts, perform actions, or include citations, source labels, "
                "URLs, or a Sources section in the answer. If support is insufficient, say so."
            )}],
            messages=[{"role": "user", "content": [{"text": (
                f"Persona: {request.persona.upper()}\nQuestion: {request.question}\n\n{context}"
            )}]}],
            inferenceConfig={
                "maxTokens": (
                    self._detail_max_output_tokens if wants_detail
                    else self._max_output_tokens
                ),
                "temperature": 0.1,
                "topP": 0.9,
            },
        )
        blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
        answer = self._clean_answer("\n".join(str(block.get("text") or "") for block in blocks))
        if not answer:
            return AdvisoryRagResponse.deny(
                request.request_id, "The grounded advisory response was empty."
            )
        return AdvisoryRagResponse.supported(
            request.request_id,
            answer,
            tuple(passage.citation for passage in passages),
        )
