"""Ollama tool-calling loop for on-prem MAE.

Runs a bounded, non-streaming Ollama ``/api/chat`` conversation in which the
model may call the read-only tools in ``mae_live_tools`` to gather CAD and
analytics data before answering -- for operational questions the deterministic
``_verified_*`` fast-paths in ``mae_service`` do not cover.

Design constraints (see docs/planning/MAE_TOOL_CALLING_ONPREM_PLAN_2026-08-08.md):
- Non-streaming: Ollama tool-calling + ``stream:true`` is unreliable, so the
  loop always runs ``stream:false`` and (for the streaming endpoint) emits the
  finished answer once via ``token_callback``.
- ``think:false`` for reliable tool behavior; any stray ``<think>`` tags are
  stripped defensively.
- Every tool call is validated by the registry (unknown tool / bad args ->
  error payload, never an exception).
- Returns ``None`` -- so the caller falls through to the existing plain LLM
  fallback unchanged -- if the model calls no tool, on any Ollama error, or if
  the round cap is reached without a final answer. It never raises.

This module deliberately does NOT import ``mae_service`` (which imports it), to
avoid a circular import.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import settings
from app.services.mae_live_tools import MaeLiveToolRegistry, tool_specs

LOCAL_TIMEZONE = ZoneInfo("America/New_York")
MAX_HISTORY_MESSAGES = 8
MAX_MESSAGE_LENGTH = 4000
TOOL_LOOP_RESPONSE_TOKENS = 400

_THINKING_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)
_THINKING_TO_END = re.compile(r"<think(?:ing)?>.*$", re.IGNORECASE | re.DOTALL)
_STRAY_THINKING_TAG = re.compile(r"</?think(?:ing)?>", re.IGNORECASE)

TOOL_SYSTEM_PROMPT = """You are MAE, the Mission Assistance Engine for Logan County 911, assisting authorized supervisors.

You have read-only tools that return live CAD data and historical analytics. Use them to gather what you need, then answer.

RULES:
- Answer ONLY from what the tools return. Never guess, estimate, or recall a number from memory. If the tools do not contain the answer, say so plainly.
- You are inquiry-only. You have no tool to dispatch, acknowledge, close, page, or change anything, and you must never claim to have done so.
- Live CAD data (active calls, unit status, call detail) is authoritative for what is active, current, or latest. Analytics is completed historical data and can lag; label it as historical.
- In Logan County CAD, lower priority numbers are more urgent: 5 and 10 are high priority, 15 elevated, 30 routine. Never call priority 30 high priority.
- Calls and units are different measures; never report a unit count as a call count.
- Be concise and practical for a 911 supervisor. When listing two or more items, put each on its own line beginning with a hyphen.
"""


def _strip_thinking(text: str) -> str:
    text = _THINKING_BLOCK.sub("", text)
    text = _THINKING_TO_END.sub("", text)
    text = _STRAY_THINKING_TAG.sub("", text)
    return text.strip()


def _research_summary(sources: list[dict]) -> dict:
    kinds = {source.get("kind") for source in sources}
    return {
        "database_first": "historical" in kinds,
        "live_verified": "live" in kinds,
        "documentation_used": "document" in kinds,
        "compared_sources": "historical" in kinds and "live" in kinds,
    }


def _build_messages(question: str, history: list[dict]) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": TOOL_SYSTEM_PROMPT}]
    for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = str(item.get("content") or "")[:MAX_MESSAGE_LENGTH]
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Current local time: {datetime.now(LOCAL_TIMEZONE).isoformat()}\n"
                f"Supervisor question: {question}"
            ),
        }
    )
    return messages


def _coerce_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def run_mae_tool_loop(
    question: str,
    history: list[dict] | None = None,
    *,
    token_callback: Callable[[str], None] | None = None,
) -> dict | None:
    """Run the tool-calling loop. Returns a MAE response dict, or None to tell
    the caller to fall through to the existing plain LLM fallback."""
    clean_question = (question or "").strip()
    if not clean_question:
        return None

    registry = MaeLiveToolRegistry()
    messages = _build_messages(clean_question, history or [])
    specs = tool_specs()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"

    sources: list[dict] = []
    seen_sources: set[tuple] = set()
    tools_executed = 0

    for _round in range(settings.mae_tool_max_rounds + 1):
        payload = {
            "model": settings.mae_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "tools": specs,
            "options": {
                "temperature": 0.2,
                "num_ctx": settings.mae_tool_context_tokens,
                "num_predict": TOOL_LOOP_RESPONSE_TOKENS,
            },
        }
        try:
            response = httpx.post(
                url, json=payload, timeout=settings.mae_request_timeout_seconds
            )
            response.raise_for_status()
            message = response.json().get("message") or {}
        except (httpx.HTTPError, ValueError, TypeError):
            return None  # degrade to the plain fallback; never hang or 503 here

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            if tools_executed == 0:
                return None  # model chose no tool -> let the normal path answer
            answer = _strip_thinking(str(message.get("content") or "").strip())
            if not answer:
                return None
            if token_callback is not None:
                token_callback(answer)
            return {
                "answer": answer,
                "sources": sources,
                "model": settings.mae_model,
                "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
                "write_access": False,
                "research": _research_summary(sources),
            }

        messages.append(message)  # assistant turn verbatim, incl. tool_calls
        for call in tool_calls:
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            result = registry.execute(name, _coerce_args(fn.get("arguments")))
            tools_executed += 1
            key = (result.source.get("name"), result.source.get("detail"))
            if key not in seen_sources:
                seen_sources.add(key)
                sources.append(result.source)
            messages.append(
                {
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(result.payload, default=str),
                }
            )

    return None  # round cap reached without a final answer -> plain fallback
