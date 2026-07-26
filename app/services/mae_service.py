from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import settings
from app.services.analytics_database import (
    AnalyticsDatabaseError,
    AnalyticsRepository,
    get_analytics_database_status,
)
from app.services.analytics_reporting import get_analytics_overview
from app.services.cad_service import get_call_detail, simplify_call
from app.services.centralsquare import CentralSquareAPIError, CentralSquareClient
from app.services.knowledge_service import search_knowledge
from app.services.operations_service import (
    get_live_operations_snapshot,
    get_live_unit_snapshot,
)


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
- When both PostgreSQL and CentralSquare context are provided, compare them.
  PostgreSQL contains completed calls and can lag current activity; live
  CentralSquare data takes precedence for active, latest, and current facts.
- If counts differ, explain the likely reason instead of silently choosing one.
- In Logan County CAD, lower numeric priority values are more urgent:
  priorities 5 and 10 are high priority, 15 is elevated, and 30 is routine.
  Never describe priority 30 as high priority.
- Keep answers concise, practical, and suitable for a 911 supervisor.
- For procedural or configuration questions, use the supplied CentralSquare
  document passages. Cite the document title and page number in the answer.
- If the supplied documentation does not support an answer, say so plainly
  instead of inventing steps.
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
RECENT_HOURS_PATTERN = re.compile(
    r"\b(?:last|past)\s+(\d{1,3})\s*(?:h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)
LATEST_CALL_PATTERN = re.compile(
    r"\b(?:last|latest|most recent)\s+(?:call|incident)\b",
    re.IGNORECASE,
)
OPERATIONAL_PATTERN = re.compile(
    r"\b(busy|cad|call|cfs|coverage|dispatch|ems|fire|happening|"
    r"incident|law|response|staffing|station|unit|workload)\b",
    re.IGNORECASE,
)
UNIT_PATTERN = re.compile(
    r"\b(unit|units|apparatus|ambulance|medic|available|off duty|"
    r"out of service|on scene|enroute|transporting)\b",
    re.IGNORECASE,
)
COMPARISON_PATTERN = re.compile(
    r"\b(compare|compared|normal|unusual|busier|slower|trend right now)\b",
    re.IGNORECASE,
)
KNOWLEDGE_PATTERN = re.compile(
    r"\b(configure|configuration|documentation|enable|forgot|guide|how do|"
    r"how to|instructions|manual|option|procedure|screen|set up|setup|"
    r"steps|where do)\b",
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


def _hours_from_question(question: str) -> int | None:
    match = RECENT_HOURS_PATTERN.search(question)
    if not match:
        return None
    return min(max(int(match.group(1)), 1), 168)


def get_recent_database_activity(hours: int) -> dict:
    start_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        with AnalyticsRepository() as repository:
            summary = repository.fetchone(
                """
                SELECT COUNT(*), MAX(call_received_at)
                FROM lcdash_analytics.calls
                WHERE call_received_at >= %(start_at)s
                """,
                {"start_at": start_at},
            ) or (0, None)
            latest = repository.fetchone(
                """
                SELECT
                    cfs_number,
                    call_received_at,
                    incident_code,
                    incident_description,
                    priority,
                    response_agency,
                    city
                FROM lcdash_analytics.calls
                WHERE call_received_at >= %(start_at)s
                ORDER BY call_received_at DESC
                LIMIT 1
                """,
                {"start_at": start_at},
            )
    except AnalyticsDatabaseError as exc:
        return {
            "available": False,
            "hours": hours,
            "message": str(exc),
        }

    latest_call = {}
    if latest:
        latest_call = {
            "cfs_number": latest[0],
            "call_received_at": latest[1].isoformat() if latest[1] else "",
            "incident_code": latest[2],
            "incident_description": latest[3],
            "priority": latest[4],
            "agency": latest[5],
            "city": latest[6],
        }
    return {
        "available": True,
        "hours": hours,
        "completed_calls_stored": int(summary[0] or 0),
        "latest_stored_at": summary[1].isoformat() if summary[1] else "",
        "latest_completed_call": latest_call,
        "important_note": (
            "This database contains completed calls. Calls that are still active "
            "may not be included, so live CAD is also checked."
        ),
    }


def get_recent_cad_activity(
    hours: int,
    client: CentralSquareClient | None = None,
) -> dict:
    current_time = datetime.now(timezone.utc)
    search_body = {
        "RecordCreatedFrom": (current_time - timedelta(hours=hours)).isoformat(),
        "RecordCreatedTo": current_time.isoformat(),
        "OrderByField": "Created",
        "OrderByDirection": "Descending",
    }
    client = client or CentralSquareClient()
    calls_by_number: dict[str, dict] = {}
    skip = 0
    truncated = False
    max_pages = 5

    for _page in range(max_pages):
        result = client.search_cfs_core(search_body, skip=skip, limit=100)
        page_calls = result.get("cfs_cores") or result.get("CFSCore") or []
        if not isinstance(page_calls, list):
            page_calls = []

        for raw_call in page_calls:
            if not isinstance(raw_call, dict):
                continue
            cfs_number = str(raw_call.get("CFSNumber") or "")
            if cfs_number:
                calls_by_number[cfs_number] = raw_call

        if len(page_calls) < 100 or not result.get("next"):
            break
        skip += len(page_calls)
    else:
        truncated = True

    raw_calls = list(calls_by_number.values())
    calls = [
        _safe_call_context(simplify_call(call))
        for call in raw_calls
        if isinstance(call, dict)
    ]
    calls.sort(key=lambda call: call.get("call_datetime") or "", reverse=True)
    return {
        "available": True,
        "hours": hours,
        "calls_returned": len(calls),
        "latest_call": calls[0] if calls else {},
        "recent_calls": calls[:10],
        "generated_at": current_time.isoformat(),
        "result_limit": max_pages * 100,
        "truncated": truncated,
    }


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


def _safe_unit_rows(rows: list, limit: int = 100) -> list:
    allowed_fields = {
        "unit_number",
        "unit_type",
        "agency",
        "station",
        "status",
        "status_group",
        "responder",
        "cfs_number",
        "incident_code",
        "incident_description",
        "priority",
        "location",
        "last_status_time",
        "status_timer_start",
    }
    return [
        {
            key: value
            for key, value in row.items()
            if key in allowed_fields
        }
        for row in rows[:limit]
        if isinstance(row, dict)
    ]


def get_mae_unit_snapshot() -> dict:
    snapshot = get_live_unit_snapshot()
    return {
        "last_updated": snapshot.get("last_updated"),
        "roster_connected": snapshot.get("roster_connected"),
        "roster_warning": snapshot.get("roster_warning"),
        "roster_stats": snapshot.get("roster_stats"),
        "active_units": _safe_unit_rows(snapshot.get("active_units") or []),
        "operational_units": _safe_unit_rows(
            snapshot.get("operational_units") or []
        ),
        "available_units": _safe_unit_rows(snapshot.get("available_units") or []),
        "unavailable_units": _safe_unit_rows(
            snapshot.get("unavailable_units") or []
        ),
        "unknown_units": _safe_unit_rows(snapshot.get("unknown_units") or []),
    }


def _build_read_context(question: str) -> tuple[list[dict], list[dict]]:
    context: list[dict] = []
    sources: list[dict] = []
    analytics_for_comparison: dict | None = None
    cfs_match = CFS_PATTERN.search(question)
    recent_hours = _hours_from_question(question)
    wants_latest_call = bool(LATEST_CALL_PATTERN.search(question))
    wants_knowledge = bool(KNOWLEDGE_PATTERN.search(question))
    explicit_live_intent = bool(
        LIVE_PATTERN.search(question)
        or CFS_PATTERN.search(question)
        or RECENT_HOURS_PATTERN.search(question)
        or LATEST_CALL_PATTERN.search(question)
        or ANALYTICS_PATTERN.search(question)
        or COMPARISON_PATTERN.search(question)
    )
    wants_units = bool(
        UNIT_PATTERN.search(question)
        and (not wants_knowledge or explicit_live_intent)
    )
    wants_comparison = bool(COMPARISON_PATTERN.search(question))
    wants_live = bool(
        LIVE_PATTERN.search(question)
        or cfs_match
        or recent_hours
        or wants_latest_call
        or wants_units
        or wants_comparison
    )
    wants_analytics = bool(
        ANALYTICS_PATTERN.search(question)
        or recent_hours
        or wants_comparison
    )
    looks_operational = bool(OPERATIONAL_PATTERN.search(question))

    if wants_knowledge:
        passages = search_knowledge(question, limit=8)
        best_passage = passages[0] if passages else {}
        query_terms = best_passage.get("query_terms") or []
        minimum_matches = min(2, len(query_terms))
        has_direct_support = bool(
            passages
            and float(best_passage.get("coverage", 1.0)) >= 0.5
            and len(best_passage.get("matched_terms") or query_terms)
            >= minimum_matches
        )
        if has_direct_support:
            context.append(
                {
                    "source": "CentralSquare documentation library",
                    "purpose": "Read-only procedural and configuration guidance",
                    "data": {
                        "question": question,
                        "supported": True,
                        "passages": passages,
                    },
                }
            )
            seen_document_pages: set[tuple[str, int]] = set()
            for passage in passages:
                title = str(
                    passage.get("title")
                    or passage.get("file_name")
                    or "CentralSquare documentation"
                )
                page_number = int(passage.get("page_number") or 0)
                source_key = (title, page_number)
                if source_key in seen_document_pages:
                    continue
                seen_document_pages.add(source_key)
                sources.append(
                    {
                        "name": title,
                        "kind": "document",
                        "detail": (
                            f"Page {page_number}"
                            if page_number
                            else "Indexed documentation"
                        ),
                        "available": True,
                        "timestamp": passage.get("indexed_at") or "",
                    }
                )
        else:
            context.append(
                {
                    "source": "CentralSquare documentation library",
                    "purpose": "Read-only procedural and configuration guidance",
                    "data": {
                        "question": question,
                        "supported": False,
                        "passages": [],
                        "message": (
                            "No sufficiently direct passage was found in the "
                            "indexed CentralSquare manuals."
                        ),
                    },
                }
            )

    # Unknown operational wording is handled conservatively: compare a
    # historical baseline with current CAD rather than guessing from one source.
    if looks_operational and not wants_knowledge and not (
        recent_hours
        or wants_latest_call
        or cfs_match
        or wants_analytics
        or wants_live
    ):
        wants_analytics = True
        wants_live = True

    if wants_knowledge and context and not explicit_live_intent:
        wants_live = False
        wants_analytics = False

    if recent_hours or wants_latest_call:
        database_hours = recent_hours or 24
        database_activity = get_recent_database_activity(database_hours)
        context.append(
            {
                "source": "PostgreSQL recent activity",
                "purpose": f"Completed calls received in the last {database_hours} hours",
                "data": database_activity,
            }
        )
        sources.append(
            {
                "name": "PostgreSQL analytics",
                "kind": "historical",
                "detail": f"Last {database_hours} hours · completed calls",
                "available": bool(database_activity.get("available")),
                "timestamp": database_activity.get("latest_stored_at") or "",
            }
        )
    elif wants_analytics:
        period = _period_from_question(question)
        analytics = get_analytics_overview(period=period)
        analytics_for_comparison = analytics
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

    if wants_comparison:
        try:
            recent_window_hours = 3
            recent_cad = get_recent_cad_activity(recent_window_hours)
            live_snapshot = get_live_operations_snapshot()
            baseline_total = int(
                ((analytics_for_comparison or {}).get("metrics") or {}).get(
                    "total_calls"
                )
                or 0
            )
            baseline_days = {
                "24h": 1,
                "7d": 7,
                "30d": 30,
                "90d": 90,
                "365d": 365,
            }.get((analytics_for_comparison or {}).get("period_key"), 30)
            baseline_windows = max((baseline_days * 24) / recent_window_hours, 1)
            baseline_average = round(baseline_total / baseline_windows, 2)
            current_recent_calls = int(recent_cad.get("calls_returned") or 0)
            comparison_ratio = (
                round(current_recent_calls / baseline_average, 2)
                if baseline_average
                else None
            )
            context.append(
                {
                    "source": "LCDash workload comparison",
                    "purpose": (
                        "Compare current three-hour call arrivals and active calls "
                        "with the equivalent historical average"
                    ),
                    "data": {
                        "comparison_window_hours": recent_window_hours,
                        "current_calls_created": current_recent_calls,
                        "current_active_calls": (
                            live_snapshot.get("dashboard_stats") or {}
                        ).get("active_calls", 0),
                        "historical_period": (
                            analytics_for_comparison or {}
                        ).get("period_label", "Last 30 days"),
                        "historical_total_calls": baseline_total,
                        "historical_average_calls_per_3_hours": baseline_average,
                        "current_to_average_ratio": comparison_ratio,
                        "recent_cad_truncated": bool(
                            recent_cad.get("truncated")
                        ),
                        "live_generated_at": recent_cad.get("generated_at"),
                    },
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "3-hour arrivals and active operations",
                    "available": True,
                    "timestamp": recent_cad.get("generated_at") or "",
                }
            )
        except CentralSquareAPIError as exc:
            context.append(
                {
                    "source": "LCDash workload comparison",
                    "purpose": "Live workload verification",
                    "error": str(exc),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "Workload comparison",
                    "available": False,
                    "timestamp": "",
                }
            )
    elif recent_hours or wants_latest_call:
        cad_hours = recent_hours or 24
        try:
            recent_cad = get_recent_cad_activity(cad_hours)
            context.append(
                {
                    "source": "CentralSquare recent call activity",
                    "purpose": (
                        f"All calls created in the last {cad_hours} hours, "
                        "including calls that may still be active"
                    ),
                    "data": recent_cad,
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": f"Last {cad_hours} hours · all calls",
                    "available": True,
                    "timestamp": recent_cad.get("generated_at") or "",
                }
            )
        except CentralSquareAPIError as exc:
            context.append(
                {
                    "source": "CentralSquare recent call activity",
                    "purpose": f"All calls created in the last {cad_hours} hours",
                    "error": str(exc),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": f"Last {cad_hours} hours · all calls",
                    "available": False,
                    "timestamp": "",
                }
            )
    elif cfs_match:
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
    elif wants_units:
        try:
            unit_snapshot = get_mae_unit_snapshot()
            context.append(
                {
                    "source": "CentralSquare live unit roster",
                    "purpose": (
                        "Current active, available, operational, unavailable, "
                        "and unknown unit status"
                    ),
                    "data": unit_snapshot,
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "Full unit roster",
                    "available": True,
                    "timestamp": unit_snapshot.get("last_updated") or "",
                }
            )
        except CentralSquareAPIError as exc:
            context.append(
                {
                    "source": "CentralSquare live unit roster",
                    "purpose": "Current full unit status",
                    "error": str(exc),
                }
            )
            sources.append(
                {
                    "name": "CentralSquare CAD",
                    "kind": "live",
                    "detail": "Full unit roster",
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


def _context_data(context: list[dict], source_name: str) -> dict:
    for item in context:
        if item.get("source") == source_name:
            data = item.get("data")
            return data if isinstance(data, dict) else {}
    return {}


def _research_summary(sources: list[dict]) -> dict:
    source_kinds = {source.get("kind") for source in sources}
    return {
        "database_first": "historical" in source_kinds,
        "live_verified": "live" in source_kinds,
        "documentation_used": "document" in source_kinds,
        "compared_sources": (
            "historical" in source_kinds and "live" in source_kinds
        ),
    }


def _verified_recent_count_answer(
    question: str,
    context: list[dict],
    sources: list[dict],
) -> dict | None:
    hours = _hours_from_question(question)
    if hours is None or not re.search(
        r"\b(how many|number of|count)\b",
        question,
        re.IGNORECASE,
    ):
        return None

    live_data = _context_data(context, "CentralSquare recent call activity")
    database_data = _context_data(context, "PostgreSQL recent activity")
    if not live_data.get("available"):
        return None

    live_count = int(live_data.get("calls_returned") or 0)
    completed_count = (
        int(database_data.get("completed_calls_stored") or 0)
        if database_data.get("available")
        else None
    )
    truncated = bool(live_data.get("truncated"))
    count_phrase = f"at least {live_count}" if truncated else str(live_count)
    answer = (
        f"CentralSquare shows {count_phrase} calls created in the last "
        f"{hours} hours."
    )
    if completed_count is not None:
        answer += (
            f" PostgreSQL currently contains {completed_count} completed calls "
            "from that same window."
        )
        difference = live_count - completed_count
        if difference > 0:
            answer += (
                f" The {difference}-call difference can include active calls or "
                "recent calls not yet stored as completed, so the live "
                "CentralSquare count is the current all-call total."
            )
    if truncated:
        answer += " The live result reached its safety limit, so the true count may be higher."

    return {
        "answer": answer,
        "sources": sources,
        "model": "LCDash verified read tools",
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "write_access": False,
        "research": _research_summary(sources),
    }


def _verified_latest_call_answer(
    question: str,
    context: list[dict],
    sources: list[dict],
) -> dict | None:
    if not LATEST_CALL_PATTERN.search(question):
        return None

    live_data = _context_data(context, "CentralSquare recent call activity")
    latest = live_data.get("latest_call") or {}
    if not latest:
        return None

    call_time = str(latest.get("call_datetime") or "")
    local_time = call_time
    try:
        parsed_time = datetime.fromisoformat(call_time.replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            parsed_time = parsed_time.replace(tzinfo=timezone.utc)
        local_time = parsed_time.astimezone(LOCAL_TIMEZONE).strftime(
            "%m/%d/%Y %I:%M:%S %p %Z"
        )
    except ValueError:
        pass

    details = [
        str(latest.get("cfs_number") or "Unknown CFS"),
        str(latest.get("incident_description") or latest.get("incident_code") or ""),
        str(latest.get("location") or ""),
    ]
    details = [detail for detail in details if detail]
    answer = "The latest call returned by live CentralSquare is " + " — ".join(details)
    if local_time:
        answer += f". It was received at {local_time}."

    return {
        "answer": answer,
        "sources": sources,
        "model": "LCDash verified read tools",
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "write_access": False,
        "research": _research_summary(sources),
    }


def _verified_unsupported_knowledge_answer(
    question: str,
    context: list[dict],
    sources: list[dict],
) -> dict | None:
    if not KNOWLEDGE_PATTERN.search(question):
        return None
    knowledge_data = _context_data(
        context,
        "CentralSquare documentation library",
    )
    if knowledge_data.get("supported") is not False:
        return None
    return {
        "answer": (
            "I could not find a sufficiently direct passage in the indexed "
            "CentralSquare manuals to answer that safely. Try using the exact "
            "screen, field, or feature name, or review the Knowledge Library. "
            "I will not invent configuration steps."
        ),
        "sources": sources,
        "model": "LCDash verified document search",
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "write_access": False,
        "research": _research_summary(sources),
    }


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

    if (
        WRITE_ACTION_PATTERN.search(clean_question)
        and not KNOWLEDGE_PATTERN.search(clean_question)
    ):
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
    verified_answer = (
        _verified_recent_count_answer(clean_question, context, sources)
        or _verified_latest_call_answer(clean_question, context, sources)
        or _verified_unsupported_knowledge_answer(
            clean_question,
            context,
            sources,
        )
    )
    if verified_answer:
        return verified_answer
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
        "research": _research_summary(sources),
    }
