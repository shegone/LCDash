"""Bedrock Converse tool-calling loop for MAE, over the read-only tool registry.

Unlike ``verified_live_advisory.py`` (a fixed fact list the model may only
phrase), this lets the model choose which of the allowlisted read-only tools
in ``live_tools.py`` to call, and how many times, before answering. The
safety property shifts accordingly: a wrong answer here can be a misreading
of real data, not just a phrasing error. The mitigations are: read-only
tools only (no write/dispatch tool exists to call), temperature 0, a hard
cap on tool rounds, an explicit instruction to answer only from tool
results, and -- the key rule -- if the model never actually calls a tool,
this returns ``None`` rather than answering from its own memory, so the
caller falls through to the document-citation path instead.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .contracts import REQUEST_ID, TENANT_ID
from .live_data import LiveDataSource
from .live_tools import TOOL_SPECS, LiveToolRegistry
from .verified_live_advisory import (
    MAX_VERIFIED_ANSWER_CHARACTERS,
    VerifiedLiveResponse,
)

DEFAULT_MAX_TOOL_ROUNDS = 5

# Tool names and result sizes only -- never CAD payload bodies -- so the
# loop is auditable from CloudWatch without call data landing in logs.
logger = logging.getLogger(__name__)


def _validate_request_identity(request_id: str, tenant_id: str) -> None:
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("A bounded non-secret request identifier is required.")
    if not TENANT_ID.fullmatch(tenant_id):
        raise ValueError("A stable tenant identifier is required.")


class BedrockConverseClient(Protocol):
    def converse(self, **kwargs: Any) -> dict[str, Any]: ...


_SYSTEM_PROMPT = (
    "You are MAE, a 911-dispatch operations assistant with read-only tools "
    "over the current CAD snapshot and historical analytics. Use the tools "
    "to gather whatever data you need before answering. Answer ONLY from "
    "what the tools return -- never guess, estimate, or recall a number "
    "from your own training. If the tools do not contain the answer, say so "
    "plainly rather than guessing. You cannot dispatch, acknowledge, page, "
    "or change any call or unit status -- you have no tool for that and "
    "must never claim to have done it. Keep the answer to a few sentences."
)


def _text_from_message(message: dict[str, Any]) -> str:
    blocks = message.get("content") or []
    return "\n".join(str(block.get("text") or "") for block in blocks if "text" in block).strip()


def _tool_use_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = message.get("content") or []
    return [block["toolUse"] for block in blocks if "toolUse" in block]


class ToolCallingLiveAdvisory:
    """Runs the tool-calling loop; returns ``None`` if no tool was ever called."""

    def __init__(
        self,
        *,
        converse_client: BedrockConverseClient,
        model_id: str,
        budget: Any = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        max_output_tokens: int = 400,
    ) -> None:
        if not 1 <= max_tool_rounds <= 10:
            raise ValueError("max_tool_rounds must be between 1 and 10.")
        if not 32 <= max_output_tokens <= 1000:
            raise ValueError("Tool-calling answers are capped at 32-1000 tokens.")
        self._client = converse_client
        self._model_id = model_id
        self._budget = budget
        self._max_tool_rounds = max_tool_rounds
        self._max_output_tokens = max_output_tokens

    def _reserve_request(self) -> bool:
        if self._budget is None:
            return True
        return self._budget.reserve()

    def answer(
        self,
        *,
        request_id: str,
        tenant_id: str,
        question: str,
        registry: LiveToolRegistry,
    ) -> VerifiedLiveResponse | None:
        """``registry`` must be built fresh per request from the current CAD
        snapshot/analytics function -- callers own that freshness, this class
        only runs the conversation loop over whatever registry it is given.
        """
        _validate_request_identity(request_id, tenant_id)
        question = str(question or "").strip()
        if not question:
            return None

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": question}]}
        ]
        tool_config = {"tools": list(TOOL_SPECS)}
        data_sources: list[LiveDataSource] = []
        seen_sources: set[tuple[str, str]] = set()
        tools_executed = 0

        for _round in range(self._max_tool_rounds + 1):
            if not self._reserve_request():
                return VerifiedLiveResponse.deny(
                    request_id, "The daily advisory usage limit has been reached."
                )
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": _SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={
                    "maxTokens": self._max_output_tokens,
                    "temperature": 0.0,
                    "topP": 1.0,
                },
            )
            output_message = ((response.get("output") or {}).get("message")) or {}
            messages.append(output_message)
            stop_reason = response.get("stopReason")

            if stop_reason != "tool_use":
                if tools_executed == 0:
                    logger.info(
                        "cloud-ai tool loop %s answered without tools; falling through",
                        request_id,
                    )
                    return None
                logger.info(
                    "cloud-ai tool loop %s finished rounds=%d tools=%d",
                    request_id,
                    _round + 1,
                    tools_executed,
                )
                answer = _text_from_message(output_message)[:MAX_VERIFIED_ANSWER_CHARACTERS]
                if not answer:
                    return VerifiedLiveResponse.deny(
                        request_id, "The tool-calling response was empty."
                    )
                return VerifiedLiveResponse.supported(
                    request_id, answer, tuple(data_sources)
                )

            tool_uses = _tool_use_blocks(output_message)
            if not tool_uses:
                # Model claimed tool_use but sent no toolUse block -- treat as
                # a dead end rather than looping forever.
                return None

            result_blocks: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                result = registry.execute(
                    tool_use.get("name", ""), tool_use.get("input") or {}
                )
                tools_executed += 1
                logger.info(
                    "cloud-ai tool call %s round=%d tool=%s ok=%s payload_keys=%d",
                    request_id,
                    _round + 1,
                    result.tool_name,
                    "error" not in result.payload,
                    len(result.payload),
                )
                source_key = (result.source.name, result.source.detail)
                if source_key not in seen_sources:
                    seen_sources.add(source_key)
                    data_sources.append(result.source)
                result_blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use.get("toolUseId", ""),
                            "content": [{"json": dict(result.payload)}],
                        }
                    }
                )
            messages.append({"role": "user", "content": result_blocks})

        # Exhausted max_tool_rounds without a final answer.
        if tools_executed == 0:
            return None
        return VerifiedLiveResponse.deny(
            request_id, "Reached the tool-call limit before finishing this answer."
        )
