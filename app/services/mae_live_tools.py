"""Callable read-only tool registry for MAE's Ollama tool-calling loop.

This is the executable counterpart to ``mae_tool_registry.py`` (which stays a
descriptive status catalog). Each tool wraps an EXISTING read-only service
function -- no new CAD access, no new network routes, and structurally no way
to reach a CAD write: ``CentralSquareClient.run_command``/``put`` are never
imported or referenced here, and no dispatch/acknowledge/page tool exists, so
the model cannot invoke one no matter what it is asked.

Data policy (Ted, 2026-08-08): MAE may surface all CAD data including
sensitive/patient details to authorized supervisors, so tool payloads are NOT
PHI-redacted. The only field stripped is ``raw`` (the entire unprocessed CAD
record) -- purely a context-budget measure, since it duplicates the structured
fields and would overflow the model's context window and truncate results.

Every failure (unknown tool, bad arguments, CAD error) returns an error
payload; nothing here raises into the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.services.analytics_reporting import (
    DEFAULT_PERIOD,
    PERIOD_OPTIONS,
    get_analytics_overview,
)
from app.services.cad_service import get_call_detail
from app.services.centralsquare import CentralSquareAPIError
from app.services.operations_service import (
    get_live_operations_snapshot,
    get_live_unit_snapshot,
)

LOCAL_TIMEZONE = ZoneInfo("America/New_York")
CFS_PATTERN = re.compile(r"^CFS\d{2}-\d{4,6}$", re.IGNORECASE)

MAX_ACTIVE_CALLS = 50
MAX_COMMAND_LOG_ENTRIES = 40
MAX_UNITS_PER_GROUP = 60
MAX_BUSIEST_ROWS = 10

# Lean per-call fields for the list tool. command_logs and reporter are
# intentionally omitted HERE (not for privacy -- they are available in full via
# get_call_detail) to keep a 50-call list within the context budget.
_CALL_SUMMARY_FIELDS = (
    "cfs_number",
    "incident_code",
    "incident_description",
    "location",
    "priority",
    "agency",
    "status",
    "call_datetime",
    "is_scheduled",
)


@dataclass(frozen=True)
class LiveToolResult:
    tool_name: str
    source: dict
    payload: dict


class _ToolInputError(ValueError):
    """Bad tool input; converted to an error payload, never raised onward."""


def _now_iso() -> str:
    return datetime.now(LOCAL_TIMEZONE).isoformat()


def _summarize_units(assigned_units: Any) -> list[dict]:
    units = assigned_units if isinstance(assigned_units, list) else []
    out = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        number = unit.get("unit_number")
        if not number:
            continue
        out.append(
            {
                "unit_number": number,
                "status": unit.get("status_group") or unit.get("status") or "",
            }
        )
    return out


def _summarize_call(call: dict) -> dict:
    summary = {field: call.get(field) for field in _CALL_SUMMARY_FIELDS}
    summary["assigned_units"] = _summarize_units(call.get("assigned_units"))
    return summary


def _detail_call(call: dict) -> dict:
    """Full call detail minus the giant duplicate ``raw`` record.

    Keeps command_logs, reporter, and all narrative -- Ted's data policy allows
    sensitive detail here. Only ``raw`` is dropped, for context budget.
    """
    detail = {key: value for key, value in call.items() if key != "raw"}
    logs = detail.get("command_logs")
    if isinstance(logs, list) and len(logs) > MAX_COMMAND_LOG_ENTRIES:
        detail["command_logs"] = logs[-MAX_COMMAND_LOG_ENTRIES:]
        detail["command_logs_truncated"] = True
    return detail


def _strip_units(units: Any) -> list[dict]:
    rows = units if isinstance(units, list) else []
    out = []
    for unit in rows[:MAX_UNITS_PER_GROUP]:
        if not isinstance(unit, dict):
            continue
        out.append({key: value for key, value in unit.items() if key != "raw"})
    return out


def _top_rows(rows: Any) -> list[dict]:
    out = []
    for row in (rows if isinstance(rows, list) else [])[:MAX_BUSIEST_ROWS]:
        if isinstance(row, dict):
            out.append({key: value for key, value in row.items() if key != "raw"})
    return out


class MaeLiveToolRegistry:
    """Built per request; stateless read-only tool execution."""

    def execute(self, tool_name: str, tool_input: Any) -> LiveToolResult:
        handler = self._HANDLERS.get(tool_name)
        if handler is None:
            return LiveToolResult(
                tool_name=tool_name,
                source=self._source("Unknown tool", "live", f"No such tool: {tool_name}", False),
                payload={"error": "unknown tool"},
            )
        args = tool_input if isinstance(tool_input, dict) else {}
        try:
            return handler(self, args)
        except _ToolInputError as error:
            return LiveToolResult(
                tool_name=tool_name,
                source=self._source(tool_name, "live", str(error), False),
                payload={"error": str(error)},
            )
        except CentralSquareAPIError as error:
            return LiveToolResult(
                tool_name=tool_name,
                source=self._source(tool_name, "live", f"CAD read error: {error}", False),
                payload={"error": "cad read failed"},
            )
        except Exception as error:  # never let a tool crash the request
            return LiveToolResult(
                tool_name=tool_name,
                source=self._source(tool_name, "live", f"Tool error: {error}", False),
                payload={"error": "tool failed"},
            )

    @staticmethod
    def _source(name: str, kind: str, detail: str, available: bool, timestamp: str = "") -> dict:
        return {
            "name": name,
            "kind": kind,
            "detail": detail,
            "available": available,
            "timestamp": timestamp,
        }

    # -- tools --------------------------------------------------------------

    def _list_active_calls(self, _args: dict) -> LiveToolResult:
        snapshot = get_live_operations_snapshot()
        calls = [c for c in (snapshot.get("calls") or []) if isinstance(c, dict)][:MAX_ACTIVE_CALLS]
        source = self._source(
            "CentralSquare live operations",
            "live",
            "Current active-call snapshot",
            True,
            str(snapshot.get("last_updated") or ""),
        )
        return LiveToolResult(
            "list_active_calls",
            source,
            {
                "count": len(calls),
                "dashboard_stats": snapshot.get("dashboard_stats"),
                "calls": [_summarize_call(c) for c in calls],
            },
        )

    def _get_call_detail(self, args: dict) -> LiveToolResult:
        cfs_number = str(args.get("cfs_number") or "").strip().upper()
        if not cfs_number or not CFS_PATTERN.match(cfs_number):
            raise _ToolInputError("cfs_number must look like CFS26-12345")
        call = get_call_detail(cfs_number)  # raises CentralSquareAPIError if absent
        source = self._source(
            "CentralSquare live CFS detail",
            "live",
            f"Call detail: {cfs_number}",
            True,
            _now_iso(),
        )
        if not isinstance(call, dict) or not call.get("cfs_number"):
            return LiveToolResult(
                "get_call_detail", source, {"found": False, "cfs_number": cfs_number}
            )
        payload = {"found": True}
        payload.update(_detail_call(call))
        return LiveToolResult("get_call_detail", source, payload)

    def _get_unit_status(self, _args: dict) -> LiveToolResult:
        snapshot = get_live_unit_snapshot()
        source = self._source(
            "CentralSquare live unit roster",
            "live",
            "Current unit roster/status",
            bool(snapshot.get("roster_connected", True)),
            str(snapshot.get("last_updated") or ""),
        )
        return LiveToolResult(
            "get_unit_status",
            source,
            {
                "roster_connected": snapshot.get("roster_connected"),
                "roster_warning": snapshot.get("roster_warning"),
                "active_stats": snapshot.get("active_stats"),
                "active_units": _strip_units(snapshot.get("active_units")),
                "available_units": _strip_units(snapshot.get("available_units")),
                "unavailable_units": _strip_units(snapshot.get("unavailable_units")),
                "unknown_units": _strip_units(snapshot.get("unknown_units")),
            },
        )

    def _get_analytics_summary(self, args: dict) -> LiveToolResult:
        period = str(args.get("period") or DEFAULT_PERIOD).strip()
        if period not in PERIOD_OPTIONS:
            raise _ToolInputError(
                f"period must be one of {sorted(PERIOD_OPTIONS)}"
            )
        overview = get_analytics_overview(period=period)
        available = bool(overview.get("available"))
        source = self._source(
            "PostgreSQL analytics",
            "historical",
            f"Analytics ({period})",
            available,
            str(overview.get("latest_data_at") or ""),
        )
        if not available:
            return LiveToolResult(
                "get_analytics_summary",
                source,
                {"available": False, "period": period},
            )
        return LiveToolResult(
            "get_analytics_summary",
            source,
            {
                "available": True,
                "period": period,
                "period_label": overview.get("period_label"),
                "metrics": overview.get("metrics"),
                "busiest_units": _top_rows(overview.get("busiest_units")),
                "busiest_stations": _top_rows(overview.get("busiest_stations")),
                "latest_data_at": overview.get("latest_data_at"),
            },
        )

    _HANDLERS: dict[str, Callable[["MaeLiveToolRegistry", dict], LiveToolResult]] = {}


MaeLiveToolRegistry._HANDLERS = {
    "list_active_calls": MaeLiveToolRegistry._list_active_calls,
    "get_call_detail": MaeLiveToolRegistry._get_call_detail,
    "get_unit_status": MaeLiveToolRegistry._get_unit_status,
    "get_analytics_summary": MaeLiveToolRegistry._get_analytics_summary,
}


def tool_specs() -> list[dict]:
    """Ollama /api/chat `tools` array for the read-only MAE tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "list_active_calls",
                "description": (
                    "List the current active CAD calls (CFS number, incident type, "
                    "location, priority, agency, status, time, assigned units) plus "
                    "dashboard totals. Use for anything about what is happening now. "
                    "For a single call's full detail or command log, use get_call_detail."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_call_detail",
                "description": (
                    "Full detail for one call by CFS number, including command-log "
                    "narrative and reporter info. Works for active or historical calls."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cfs_number": {
                            "type": "string",
                            "description": "e.g. CFS26-25863",
                        }
                    },
                    "required": ["cfs_number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_unit_status",
                "description": (
                    "Current unit roster and status: active, available, unavailable, "
                    "and unknown units. Use for questions about units/crews right now."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_analytics_summary",
                "description": (
                    "Historical analytics from PostgreSQL for a period: total calls, "
                    "average/median response times, busiest units and stations. This is "
                    "completed historical data, not live activity."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "period": {
                            "type": "string",
                            "enum": sorted(PERIOD_OPTIONS),
                            "description": "Time window; default 30d",
                        }
                    },
                },
            },
        },
    ]
