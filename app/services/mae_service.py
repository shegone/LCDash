from datetime import datetime
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import settings
from app.services.analytics_database import get_analytics_database_status
from app.services.analytics_reporting import get_analytics_overview
from app.services.cad_service import get_call_detail
from app.services.centralsquare import CentralSquareAPIError
from app.services.operations_service import get_live_operations_snapshot


LOCAL_TIMEZONE = ZoneInfo("America/New_York")
MAX_HISTORY_MESSAGES = 8
MAX_MESSAGE_LENGTH = 4000
MAX_CONTEXT_CHARACTERS = 24000

SYSTEM_PROMPT = """You are MAE, the Mission Assistance Engine for Logan County 911.
You assist authorized supervisors with operational awareness and analysis.

NON-NEGOTIABLE SAFETY AND AUTHORITY RULES:
- You are inquiry-only. Never add, update, delete, dispatch, acknowledge, close,
  or otherwise change anything in CentralSquare CAD or any connected system.
- You only receive data from named read-only LCDash tools.
- Never claim that you performed an operational action.
- If asked to perform a write action, clearly say that MAE is currently
  inquiry-only and cannot perform it.
- Treat provided operational data as sensitive. Do not invent missing facts.
- Clearly distinguish live CAD data from historical PostgreSQL analytics.
- Mention when data is unavailable, stale, incomplete, or not returned.
- In Logan County CAD, lower numeric priority values are more urgent:
  priorities 5 and 10 are high priority, 15 is elevated, and 30 is routine.
  Never describe priority 30 as high priority.
- Keep answers concise, practical, and suitable for a 911 supervisor.
- When answering from supplied data, include the relevant time range or
  generated time when it helps interpretation.
"""

WRITE_ACTION_PATTERN = re.compile(
    r"\b(add|assign|cancel|change|close|create|delete|dispatch|edit|"
    r"enter|mark|modify|remove|send|set|update|write)\b",
    re.IGNORECASE,
)
CFS_PATTERN = re.compile(r"\bCFS(?:\d{2})?[- ]?\d{3,}\b", re.IGNORECASE)
LIVE_PATTERN = re.compile(
    r"\b(active|available|current|currently|live|now|on scene|"
    r"enroute|transporting|unit status|right now)\b",
    re.IGNORECASE,
)
ANALYTICS_PATTERN = re.compile(
    r"\b(analytics|average|busiest|calls by|historical|history|how many|"
    r"last \d+ (?:hours?|days?)|month|past|report|response time|"
    r"statistics|stats|trend|week|year|yesterday)\b",
    re.IGNORECASE,
)
OPERATIONAL_PATTERN = re.compile(
    r"\b(cad|call|cfs|dispatch|ems|fire|incident|law|station|unit)\b",
    re.IGNORECASE,
)


class MAEServiceError(Exception):
    """Raised when MAE cannot complete a read-only inquiry."""


def _period_from_question(question: str) -> str:
    lowered = question.lower()
    if "last 24" in lowered or "past 24" in lowered or "today" in lowered:
        return "24h"
    if "week" in lowered or "7 day" in lowered:
        return "7d"
    if "year" in lowered or "12 month" in lowered or "365 day" in lowered:
        return "365d"
    if "90 day" in lowered or "quarter" in lowered:
        return "90d"
    return "30d"


def _trim_rows(value: Any, limit: int = 20) -> Any:
    if isinstance(value, list):
        return [_trim_rows(item, limit) for item in value[:limit]]
    if isinstance(value, dict):
        return {key: _trim_rows(item, limit) for key, item in value.items()}
    return value


def _safe_call_context(call: dict, detailed: bool = False) -> dict:
    result = {
        "cfs_number": call.get("cfs_number"),
        "incident_code": call.get("incident_code"),
        "incident_description": call.get("incident_description"),
        "location": call.get("location"),
        "priority": call.get("priority"),
        "agency": call.get("agency"),
        "status": call.get("status"),
        "call_taker": call.get("call_taker"),
        "call_datetime": call.get("call_datetime"),
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "assigned_units": _trim_rows(call.get("assigned_units") or [], 20),
    }
    if detailed:
        result.update(
            {
                "command_logs": _trim_rows(call.get("command_logs") or [], 30),
                "reporter": call.get("reporter") or {},
                "rapidsos": (call.get("raw") or {}).get("RapidSOS") or {},
                "proqa": (call.get("raw") or {}).get("ProQA") or {},
            }
        )
    return result


def _build_read_context(question: str) -> tuple[list[dict], list[dict]]:
    context: list[dict] = []
    sources: list[dict] = []
    cfs_match = CFS_PATTERN.search(question)
    wants_live = bool(LIVE_PATTERN.search(question) or cfs_match)
    wants_analytics = bool(ANALYTICS_PATTERN.search(question))
    looks_operational = bool(OPERATIONAL_PATTERN.search(question))

    if wants_analytics:
        period = _period_from_question(question)
        analytics = get_analytics_overview(period=period)
        context.append(
            {
                "source": "PostgreSQL analytics",
                "purpose": "Historical completed-call analysis",
                "data": _trim_rows(analytics),
            }
        )
        sources.append(
            {
                "name": "PostgreSQL analytics",
                "kind": "historical",
                "detail": analytics.get("period_label", period),
                "available": bool(analytics.get("available")),
                "timestamp": analytics.get("latest_data_at") or "",
            }
        )
        if not analytics.get("available") and looks_operational:
            wants_live = True

    if cfs_match:
        cfs_number = cfs_match.group(0).upper().replace(" ", "-")
        try:
            call = get_call_detail(cfs_number)
            context.append(
                {
                    "source": "CentralSquare live CFS detail",
                    "purpose": f"Current detail for {cfs_number}",
                    "data": _safe_call_context(call, detailed=True),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": cfs_number,
                    "available": True,
                    "timestamp": call.get("call_datetime") or "",
                }
            )
        except CentralSquareAPIError as exc:
            context.append(
                {
                    "source": "CentralSquare live CFS detail",
                    "purpose": f"Current detail for {cfs_number}",
                    "error": str(exc),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": cfs_number,
                    "available": False,
                    "timestamp": "",
                }
            )
    elif wants_live:
        try:
            snapshot = get_live_operations_snapshot()
            context.append(
                {
                    "source": "CentralSquare live operations",
                    "purpose": "Current active calls and assigned units",
                    "data": {
                        "last_updated": snapshot.get("last_updated"),
                        "dashboard_stats": snapshot.get("dashboard_stats"),
                        "calls": [
                            _safe_call_context(call)
                            for call in (snapshot.get("calls") or [])[:25]
                        ],
                        "unit_stats": snapshot.get("unit_stats"),
                        "unit_rows": _trim_rows(snapshot.get("unit_rows") or [], 100),
                    },
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "Active operations snapshot",
                    "available": True,
                    "timestamp": snapshot.get("last_updated") or "",
                }
            )
        except CentralSquareAPIError as exc:
            context.append(
                {
                    "source": "CentralSquare live operations",
                    "purpose": "Current active calls and assigned units",
                    "error": str(exc),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "Active operations snapshot",
                    "available": False,
                    "timestamp": "",
                }
            )

    return context, sources


def _ollama_messages(
    question: str,
    history: list[dict],
    context: list[dict],
) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in history[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = str(item.get("content") or "")[:MAX_MESSAGE_LENGTH]
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    context_json = json.dumps(context, ensure_ascii=False, default=str)
    if len(context_json) > MAX_CONTEXT_CHARACTERS:
        context_json = context_json[:MAX_CONTEXT_CHARACTERS] + "\n[context truncated]"

    messages.append(
        {
            "role": "user",
            "content": (
                f"Current local time: {datetime.now(LOCAL_TIMEZONE).isoformat()}\n"
                f"Read-only source context:\n{context_json}\n\n"
                f"Supervisor question: {question}\n\n"
                "Answer the question using the source context when relevant. "
                "Do not imply access to data that is not present."
            ),
        }
    )
    return messages


def get_mae_status() -> dict:
    model_names: list[str] = []
    ai_error = ""
    try:
        response = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags",
            timeout=5,
        )
        response.raise_for_status()
        model_names = [
            model.get("name", "")
            for model in response.json().get("models", [])
            if model.get("name")
        ]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        ai_error = str(exc)

    database = get_analytics_database_status()
    return {
        "mae": "Mission Assistance Engine",
        "mode": "Inquiry only",
        "write_access": False,
        "local_ai": {
            "connected": bool(model_names),
            "model": settings.mae_model,
            "installed_models": model_names,
            "error": ai_error,
        },
        "database": database,
        "centralsquare": {
            "configured": all(
                (
                    settings.token_url,
                    settings.cad_base_url,
                    settings.username,
                    settings.password,
                )
            ),
            "mode": "Read only through approved LCDash functions",
        },
    }


def ask_mae(question: str, history: list[dict] | None = None) -> dict:
    clean_question = (question or "").strip()
    if not clean_question:
        raise MAEServiceError("Please enter a question for MAE.")
    if len(clean_question) > MAX_MESSAGE_LENGTH:
        raise MAEServiceError(
            f"Questions may contain at most {MAX_MESSAGE_LENGTH} characters."
        )

    if WRITE_ACTION_PATTERN.search(clean_question):
        return {
            "answer": (
                "MAE is currently inquiry-only. I can research, summarize, and "
                "explain CAD or analytics information, but I cannot add, change, "
                "dispatch, close, or delete anything in CentralSquare."
            ),
            "sources": [],
            "model": settings.mae_model,
            "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
            "write_access": False,
        }

    context, sources = _build_read_context(clean_question)
    payload = {
        "model": settings.mae_model,
        "messages": _ollama_messages(clean_question, history or [], context),
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 4096,
        },
    }

    try:
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=settings.mae_request_timeout_seconds,
        )
        response.raise_for_status()
        answer = str(response.json().get("message", {}).get("content") or "").strip()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise MAEServiceError(f"The local MAE model is unavailable: {exc}") from exc

    if not answer:
        raise MAEServiceError("The local MAE model returned an empty response.")

    return {
        "answer": answer,
        "sources": sources,
        "model": settings.mae_model,
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "write_access": False,
    }
