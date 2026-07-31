import json
from time import perf_counter

import httpx

from app.config.settings import settings
from app.services.nga911_intelligence_service import (
    get_nga911_intelligence_overview,
    get_nga911_logan_operations,
)


class NOVAServiceError(RuntimeError):
    """Raised when NOVA cannot answer from the NGA911 intelligence layer."""


def get_nova_status() -> dict:
    try:
        response = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags",
            timeout=3.0,
        )
        response.raise_for_status()
        models = [str(item.get("name") or "") for item in response.json().get("models", [])]
        connected = True
        available = any(name == settings.mae_model or name.startswith(f"{settings.mae_model}:") for name in models)
    except (httpx.HTTPError, ValueError, TypeError):
        connected = False
        available = False
    return {
        "assistant": "NOVA",
        "connected": connected,
        "model": settings.mae_model,
        "model_available": available,
        "scope": "NGA911 Intelligence layer only",
        "write_access": False,
        "synthetic_data": True,
    }


def ask_nova(question: str, history: list[dict] | None = None) -> dict:
    started = perf_counter()
    clean_question = (question or "").strip()
    if not clean_question:
        raise NOVAServiceError("Ask NOVA an NGA911 intelligence question.")

    overview = get_nga911_intelligence_overview()
    operations = get_nga911_logan_operations(14)
    context = {
        "environment": operations["environment_label"],
        "core": operations["core"],
        "center": operations["center"],
        "network_paths": operations["paths"],
        "console_positions": operations["consoles"],
        "events_last_14_days": operations["events"],
        "daily_history": operations["daily_history"],
        "regional_summary": overview["summary"],
        "regional_findings": overview["intelligence"],
    }
    recent_history = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")[:1500]}
        for item in (history or [])[-4:]
        if str(item.get("role") or "") in {"user", "assistant"}
    ]
    messages = [{
        "role": "system",
        "content": (
            "You are NOVA, the read-only NGA911 Intelligence assistant for directors and supervisors. "
            "Answer only from the supplied NGA911 intelligence context. Explain technical conditions in plain language. "
            "When asked for a report, produce a polished report with Executive Summary, Current Health, Disruptions, "
            "Console Operations, Trends, and Recommended Human Review. Clearly say all current records are synthetic "
            "demonstration data. Never claim to route calls, modify NGCS, acknowledge alarms, or take operational action. "
            "If the context does not answer a question, say what NGA API data is still required."
        ),
    }, *recent_history, {
        "role": "user",
        "content": f"NGA911 intelligence context:\n{json.dumps(context, separators=(',', ':'))}\n\nQuestion: {clean_question}",
    }]
    try:
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": settings.mae_model,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.15, "num_ctx": 8192, "num_predict": 700},
            },
            timeout=settings.mae_request_timeout_seconds,
        )
        response.raise_for_status()
        answer = str(response.json().get("message", {}).get("content") or "").strip()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise NOVAServiceError("NOVA's local intelligence model is unavailable.") from exc
    if not answer:
        raise NOVAServiceError("NOVA returned an empty response.")
    return {
        "answer": answer,
        "assistant": "NOVA",
        "model": settings.mae_model,
        "write_access": False,
        "synthetic_data": True,
        "sources": [
            {"name": "NGA911 director operations contract", "detail": "Five paths, six positions, events, and 14-day history."},
            {"name": "NGA911 regional intelligence contract", "detail": "County roll-up and explainable findings."},
        ],
        "assurance": {"level": "supported", "label": "Intelligence-layer grounded", "detail": "Answer generated only from the current normalized NGA911 demonstration contracts."},
        "timing": {"total_ms": round((perf_counter() - started) * 1000)},
    }
