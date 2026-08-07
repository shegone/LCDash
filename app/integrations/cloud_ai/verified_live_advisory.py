"""Phrase pre-verified live CAD/analytics facts; never let a model invent one.

The facts a response is built from are computed in Python by
``live_data.py`` before this class ever runs. The model's only job is to
turn a short, fixed list of facts into one natural sentence -- it is
explicitly instructed not to add, infer, or estimate any number beyond what
it was given, and it never sees a raw CAD record or database row.

This is a deliberately different safety shape from the document-citation
path (``bedrock_retrieval.py``): that path requires citing an approved
document because the model is free to interpret document text. This path
requires no citation because the model is not given anything to
interpret -- the numbers were already fixed by application code before the
call was made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import REQUEST_ID, TENANT_ID
from .live_data import LiveDataSource, VerifiedFact


def _validate_request_identity(request_id: str, tenant_id: str) -> None:
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("A bounded non-secret request identifier is required.")
    if not TENANT_ID.fullmatch(tenant_id):
        raise ValueError("A stable tenant identifier is required.")


MAX_VERIFIED_ANSWER_CHARACTERS = 800


class BedrockConverseClient(Protocol):
    def converse(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class VerifiedLiveResponse:
    request_id: str
    answer: str
    data_sources: tuple[LiveDataSource, ...]
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
            if self.answer.strip() or not self.denial_reason.strip():
                raise ValueError("Denied responses contain only a sanitized denial reason.")
            return
        if not self.answer.strip() or self.denial_reason:
            raise ValueError("Supported verified-live answers require text.")
        if len(self.answer) > MAX_VERIFIED_ANSWER_CHARACTERS:
            raise ValueError("Verified-live answers exceed the explicit output limit.")

    @classmethod
    def supported(
        cls, request_id: str, answer: str, data_sources: tuple[LiveDataSource, ...]
    ) -> "VerifiedLiveResponse":
        return cls(request_id, answer, data_sources, denied=False)

    @classmethod
    def deny(cls, request_id: str, reason: str) -> "VerifiedLiveResponse":
        return cls(request_id, "", (), denied=True, denial_reason=reason)


_SYSTEM_PROMPT = (
    "You are MAE/JACK, phrasing a short status update from a fixed list of "
    "already-verified facts. Use only the facts given to you. Never add, "
    "infer, estimate, round, or restate a number that is not explicitly "
    "listed. Do not mention documents, citations, or sources. Do not offer "
    "to take any action. If a fact says data is unavailable, say so plainly "
    "rather than guessing. Keep the answer to two or three sentences."
)


class VerifiedLiveAdvisory:
    """Turn a fixed fact list into one bounded sentence. No citation, no CAD write."""

    def __init__(
        self,
        *,
        converse_client: BedrockConverseClient,
        model_id: str,
        max_output_tokens: int = 200,
        budget: Any = None,
    ) -> None:
        if not 32 <= max_output_tokens <= 300:
            raise ValueError("Verified-live phrasing is capped at 32-300 tokens.")
        self._client = converse_client
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._budget = budget

    def _reserve_request(self) -> bool:
        if self._budget is None:
            return True
        return self._budget.reserve()

    def answer(
        self,
        *,
        request_id: str,
        tenant_id: str,
        facts: tuple[VerifiedFact, ...],
        data_sources: tuple[LiveDataSource, ...],
    ) -> VerifiedLiveResponse:
        _validate_request_identity(request_id, tenant_id)
        if not facts:
            return VerifiedLiveResponse.deny(
                request_id, "No verified live data matched this question."
            )
        if not self._reserve_request():
            return VerifiedLiveResponse.deny(
                request_id, "The daily advisory usage limit has been reached."
            )
        fact_lines = "\n".join(f"- {fact.label}: {fact.value}" for fact in facts)
        response = self._client.converse(
            modelId=self._model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": f"Verified facts:\n{fact_lines}"}],
                }
            ],
            inferenceConfig={
                "maxTokens": self._max_output_tokens,
                "temperature": 0.0,
                "topP": 1.0,
            },
        )
        blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
        answer = "\n".join(str(block.get("text") or "") for block in blocks).strip()
        answer = answer[:MAX_VERIFIED_ANSWER_CHARACTERS]
        if not answer:
            return VerifiedLiveResponse.deny(
                request_id, "The verified-live response was empty."
            )
        return VerifiedLiveResponse.supported(request_id, answer, data_sources)
