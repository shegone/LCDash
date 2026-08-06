"""Retrieve-only Bedrock adapter with strict source and citation validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from .contracts import AdvisoryCitation, AdvisoryRagRequest, AdvisoryRagResponse


class BedrockRetrieveClient(Protocol):
    def retrieve(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    text: str
    score: float
    citation: AdvisoryCitation


class ApprovedBedrockRetriever:
    """Semantic retrieval only; performs no generation or operational action."""

    def __init__(self, *, client: BedrockRetrieveClient, knowledge_base_id: str,
                 tenant_id: str, allowed_s3_prefixes: tuple[str, ...],
                 result_limit: int = 5, score_threshold: float = 0.5) -> None:
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

    def retrieve(self, *, tenant_id: str, question: str) -> tuple[RetrievedPassage, ...]:
        clean = question.strip()
        if tenant_id != self._tenant_id or not clean or len(clean) > 4000:
            return ()
        response = self._client.retrieve(
            knowledgeBaseId=self._knowledge_base_id,
            retrievalQuery={"text": clean},
            retrievalConfiguration={"vectorSearchConfiguration": {
                "numberOfResults": self._limit, "overrideSearchType": "SEMANTIC"
            }},
        )
        passages = []
        for item in response.get("retrievalResults", ()):
            uri = (((item.get("location") or {}).get("s3Location") or {}).get("uri") or "")
            if not any(uri.startswith(prefix) for prefix in self._prefixes):
                continue
            score = float(item.get("score") or 0)
            text = str((item.get("content") or {}).get("text") or "").strip()
            if score < self._threshold or not text:
                continue
            metadata = item.get("metadata") or {}
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
            tenant_id=request.tenant_id, question=request.question
        )
        if not passages:
            return AdvisoryRagResponse.deny(
                request.request_id,
                "No approved cited source supports this request.",
            )
        answer = "\n\n".join(passage.text for passage in passages)[:6000].strip()
        citations = tuple(passage.citation for passage in passages)
        return AdvisoryRagResponse.supported(request.request_id, answer, citations)
