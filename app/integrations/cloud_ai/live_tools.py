"""Read-only tool registry for MAE's Bedrock Converse tool-calling loop.

Unlike ``live_data.py`` (fixed regex intent -> fixed precomputed fact), this
module lets the model decide *which* of a small allowlisted set of read-only
tools to call and *how many times*, so it can answer questions the fixed
intent list cannot. The safety property is different but still structural:
no tool here can write, dispatch, acknowledge, or page -- those operations
have no corresponding tool, so the model cannot invoke them no matter what
it is asked. See ``FORBIDDEN_OPERATIONS`` in ``cloud_read_config.py``.

Every tool reads only the already-polled, already-sanitized CAD snapshot
(``CloudCadDisplayState.calls`` / ``.units``, the same ``CALL_FIELDS`` /
``UNIT_FIELDS`` the dashboard and map render) or the analytics overview
(``get_analytics_overview``). Nothing here makes a new CentralSquare API
call. ``search_calls``/``search_units`` add structured, server-validated
filtering over that same snapshot (see
``docs/planning/MAE_FULL_READ_API_TOOLSET_2026-08-08.md`` sections 2.1/2.4);
they do not reach the live CentralSquare search endpoints -- that would
require calling the connector directly, which this registry has no access
to (it is constructed from the polled snapshot and an analytics function
only), and is left as later work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .live_data import CFS_PATTERN, LiveDataSource

MAX_ACTIVE_CALLS = 50
MAX_DETAIL_COMMAND_LOG_ENTRIES = 40
MAX_BUSIEST_ROWS = 5
MAX_INCIDENT_TYPE_ROWS = 10
MAX_SEARCH_CALL_RESULTS = 50
MAX_SEARCH_UNIT_RESULTS = 100

_ANALYTICS_PERIOD_KEYS = {"24h", "7d", "30d", "90d", "365d"}
_MIN_HOURS = 1
_MAX_HOURS = 8784  # 366 days, matches analytics_reporting.MAX_CUSTOM_DAYS

# search_calls / search_units filter keys, each mapped to its allowed Python
# type(s). Any key in a request that is not in the relevant set is rejected
# outright -- this is the "unknown filter keys rejected" server-side gate.
_SEARCH_CALLS_STRING_FILTERS = ("incident_code", "agency", "status", "priority", "location", "unit_number")
_SEARCH_CALLS_INT_FILTERS = ("priority_min", "priority_max")
_SEARCH_CALLS_ALLOWED_KEYS = frozenset(
    _SEARCH_CALLS_STRING_FILTERS + _SEARCH_CALLS_INT_FILTERS + ("limit",)
)
_SEARCH_UNITS_STRING_FILTERS = ("agency", "unit_type", "status", "station")
_SEARCH_UNITS_ALLOWED_KEYS = frozenset(_SEARCH_UNITS_STRING_FILTERS + ("limit",))
_MAX_FILTER_STRING_LENGTH = 128


@dataclass(frozen=True, slots=True)
class LiveToolResult:
    """One tool execution: the JSON-safe payload plus its transparency source."""

    tool_name: str
    source: LiveDataSource
    payload: Mapping[str, Any]


_CALL_SUMMARY_FIELDS = (
    "cfs_number",
    "incident_code",
    "incident_description",
    "location_label",
    "city",
    "priority",
    "agency",
    "status",
    "call_datetime",
)


def _summarize_units(assigned_units: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "unit_number": str(unit.get("unit_number") or ""),
            "status": str(unit.get("status") or ""),
        }
        for unit in (assigned_units or [])
        if unit.get("unit_number")
    ]


def _summarize_call(call: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        field: call.get(field, "") for field in _CALL_SUMMARY_FIELDS
    }
    summary["assigned_units"] = _summarize_units(call.get("assigned_units") or ())
    summary["latitude"] = call.get("latitude")
    summary["longitude"] = call.get("longitude")
    reporter = call.get("reporter")
    summary["reporter"] = dict(reporter) if isinstance(reporter, Mapping) else {}
    # command_logs are intentionally omitted from the list view for token
    # budget, NOT as a data limit -- the full log is available per call via
    # get_call_detail.
    return summary


def _detail_call(call: Mapping[str, Any]) -> dict[str, Any]:
    # De-limited: include every field present in the call dict except ``raw``
    # (kept out only for context budget), so nothing pulled from CAD is hidden.
    detail: dict[str, Any] = {}
    for key in call:
        if key == "raw":
            continue
        value = call.get(key)
        if key == "assigned_units":
            detail[key] = [dict(unit) for unit in (value or ())]
        elif key == "command_logs":
            logs = tuple(value or ())
            detail[key] = [dict(entry) for entry in logs[-MAX_DETAIL_COMMAND_LOG_ENTRIES:]]
        elif key == "reporter":
            detail[key] = dict(value) if isinstance(value, Mapping) else value
        else:
            detail[key] = value
    return detail


def _top_rows(rows: Any, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        name = row.get("label") or row.get("name") or row.get("station") or row.get("unit_number")
        count = row.get("count") or row.get("total")
        if name is None:
            continue
        out.append({"name": str(name), "count": count})
        if len(out) >= limit:
            break
    return out


def _reject_unknown_keys(tool_input: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(tool_input) - allowed)
    if unknown:
        raise _ToolInputError(f"unknown filter key(s): {', '.join(unknown)}")


def _optional_str_filter(tool_input: Mapping[str, Any], key: str) -> str | None:
    value = tool_input.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _ToolInputError(f"{key} must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > _MAX_FILTER_STRING_LENGTH:
        raise _ToolInputError(f"{key} must be at most {_MAX_FILTER_STRING_LENGTH} characters")
    return value


def _optional_int_filter(tool_input: Mapping[str, Any], key: str) -> int | None:
    value = tool_input.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ToolInputError(f"{key} must be an integer")
    return value


def _optional_limit(tool_input: Mapping[str, Any], *, default: int, maximum: int) -> int:
    value = tool_input.get("limit")
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ToolInputError("limit must be an integer")
    if not (1 <= value <= maximum):
        raise _ToolInputError(f"limit must be between 1 and {maximum}")
    return value


def _contains(haystack: Any, needle: str) -> bool:
    return needle.lower() in str(haystack or "").lower()


def _equals_ci(value: Any, needle: str) -> bool:
    return str(value or "").strip().lower() == needle.strip().lower()


def _priority_as_int(priority: Any) -> int | None:
    try:
        return int(str(priority).strip())
    except (TypeError, ValueError):
        return None


class LiveToolRegistry:
    """Constructed once per request from the current snapshot; stateless calls."""

    def __init__(
        self,
        *,
        cad_state: Any,
        cad_status: Mapping[str, Any],
        analytics_overview_fn: Callable[..., Mapping[str, Any]] | None,
    ) -> None:
        self._cad_state = cad_state
        self._cad_status = cad_status
        self._analytics_overview_fn = analytics_overview_fn

    def execute(self, tool_name: str, tool_input: Mapping[str, Any]) -> LiveToolResult:
        handler = self._HANDLERS.get(tool_name)
        if handler is None:
            return LiveToolResult(
                tool_name=tool_name,
                source=LiveDataSource(
                    name="Unknown tool",
                    kind="live",
                    detail=f"No such tool: {tool_name}",
                    available=False,
                ),
                payload={"error": "unknown tool"},
            )
        try:
            return handler(self, tool_input or {})
        except _ToolInputError as error:
            return LiveToolResult(
                tool_name=tool_name,
                source=LiveDataSource(
                    name=tool_name,
                    kind="live",
                    detail=str(error),
                    available=False,
                ),
                payload={"error": str(error)},
            )

    # -- list_active_calls ------------------------------------------------

    def _list_active_calls(self, _tool_input: Mapping[str, Any]) -> LiveToolResult:
        freshness = str(self._cad_status.get("freshness") or "unknown")
        available = freshness not in {"disabled", "awaiting-success", "stale"}
        age_seconds = self._cad_status.get("age_seconds")
        timestamp = f"{age_seconds}s old" if isinstance(age_seconds, (int, float)) else freshness
        source = LiveDataSource(
            name="CentralSquare CAD (current read-only snapshot)",
            kind="live",
            detail=f"Freshness: {freshness}",
            available=available,
            timestamp=timestamp,
        )
        if not available:
            return LiveToolResult("list_active_calls", source, {"available": False, "calls": []})

        calls = tuple(self._cad_state.calls)[:MAX_ACTIVE_CALLS]
        return LiveToolResult(
            "list_active_calls",
            source,
            {"available": True, "count": len(calls), "calls": [_summarize_call(c) for c in calls]},
        )

    # -- get_call_detail ----------------------------------------------------

    def _get_call_detail(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        cfs_number = str(tool_input.get("cfs_number") or "").strip().upper()
        if not cfs_number or not CFS_PATTERN.fullmatch(cfs_number):
            raise _ToolInputError("cfs_number must match the CFSnn-nnnn(nn) pattern")

        freshness = str(self._cad_status.get("freshness") or "unknown")
        available = freshness not in {"disabled", "awaiting-success", "stale"}
        source = LiveDataSource(
            name="CentralSquare CAD (current read-only snapshot)",
            kind="live",
            detail=f"Call detail lookup: {cfs_number}",
            available=available,
        )
        if not available:
            return LiveToolResult("get_call_detail", source, {"available": False, "found": False})

        match = next(
            (
                call for call in self._cad_state.calls
                if str(call.get("cfs_number") or "").upper() == cfs_number
            ),
            None,
        )
        if match is None:
            return LiveToolResult(
                "get_call_detail", source, {"available": True, "found": False, "cfs_number": cfs_number}
            )
        payload = {"available": True, "found": True}
        payload.update(_detail_call(match))
        return LiveToolResult("get_call_detail", source, payload)

    # -- search_calls -------------------------------------------------------

    def _search_calls(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        _reject_unknown_keys(tool_input, _SEARCH_CALLS_ALLOWED_KEYS)

        incident_code = _optional_str_filter(tool_input, "incident_code")
        agency = _optional_str_filter(tool_input, "agency")
        status = _optional_str_filter(tool_input, "status")
        priority = _optional_str_filter(tool_input, "priority")
        location = _optional_str_filter(tool_input, "location")
        unit_number = _optional_str_filter(tool_input, "unit_number")
        priority_min = _optional_int_filter(tool_input, "priority_min")
        priority_max = _optional_int_filter(tool_input, "priority_max")
        if priority_min is not None and priority_max is not None and priority_min > priority_max:
            raise _ToolInputError("priority_min must be <= priority_max")
        limit = _optional_limit(tool_input, default=MAX_SEARCH_CALL_RESULTS, maximum=MAX_SEARCH_CALL_RESULTS)

        freshness = str(self._cad_status.get("freshness") or "unknown")
        available = freshness not in {"disabled", "awaiting-success", "stale"}
        source = LiveDataSource(
            name="CentralSquare CAD (current read-only snapshot)",
            kind="live",
            detail="Filtered search over the active-call snapshot",
            available=available,
        )
        if not available:
            return LiveToolResult("search_calls", source, {"available": False, "count": 0, "calls": []})

        def matches(call: Mapping[str, Any]) -> bool:
            if incident_code and not (
                _contains(call.get("incident_code"), incident_code)
                or _contains(call.get("incident_description"), incident_code)
            ):
                return False
            if agency and not _equals_ci(call.get("agency"), agency):
                return False
            if status and not _equals_ci(call.get("status"), status):
                return False
            if priority and not _equals_ci(call.get("priority"), priority):
                return False
            if location and not (
                _contains(call.get("location_label"), location) or _contains(call.get("city"), location)
            ):
                return False
            if priority_min is not None or priority_max is not None:
                call_priority = _priority_as_int(call.get("priority"))
                if call_priority is None:
                    return False
                if priority_min is not None and call_priority < priority_min:
                    return False
                if priority_max is not None and call_priority > priority_max:
                    return False
            if unit_number:
                assigned = call.get("assigned_units") or ()
                if not any(_contains(unit.get("unit_number"), unit_number) for unit in assigned):
                    return False
            return True

        matched = [call for call in self._cad_state.calls if matches(call)]
        bounded = matched[:limit]
        return LiveToolResult(
            "search_calls",
            source,
            {
                "available": True,
                "count": len(matched),
                "returned": len(bounded),
                "truncated": len(matched) > len(bounded),
                "calls": [_summarize_call(c) for c in bounded],
            },
        )

    # -- search_units ---------------------------------------------------

    def _search_units(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        _reject_unknown_keys(tool_input, _SEARCH_UNITS_ALLOWED_KEYS)

        agency = _optional_str_filter(tool_input, "agency")
        unit_type = _optional_str_filter(tool_input, "unit_type")
        status = _optional_str_filter(tool_input, "status")
        station = _optional_str_filter(tool_input, "station")
        limit = _optional_limit(tool_input, default=MAX_SEARCH_UNIT_RESULTS, maximum=MAX_SEARCH_UNIT_RESULTS)

        freshness = str(self._cad_status.get("freshness") or "unknown")
        available = freshness not in {"disabled", "awaiting-success", "stale"}
        source = LiveDataSource(
            name="CentralSquare CAD (current read-only snapshot)",
            kind="live",
            detail="Filtered search over the current unit roster",
            available=available,
        )
        if not available:
            return LiveToolResult("search_units", source, {"available": False, "count": 0, "units": []})

        def matches(unit: Mapping[str, Any]) -> bool:
            if agency and not _equals_ci(unit.get("agency"), agency):
                return False
            if unit_type and not _contains(unit.get("unit_type"), unit_type):
                return False
            if status and not _equals_ci(unit.get("status"), status):
                return False
            if station and not _contains(unit.get("station"), station):
                return False
            return True

        units = tuple(self._cad_state.units)
        matched = [unit for unit in units if matches(unit)]
        bounded = matched[:limit]
        return LiveToolResult(
            "search_units",
            source,
            {
                "available": True,
                "count": len(matched),
                "returned": len(bounded),
                "truncated": len(matched) > len(bounded),
                "units": [
                    {
                        "unit_number": str(u.get("unit_number") or ""),
                        "agency": str(u.get("agency") or ""),
                        "unit_type": str(u.get("unit_type") or ""),
                        "status": str(u.get("status") or ""),
                        "station": str(u.get("station") or ""),
                        "assignment_cfs_number": str(u.get("assignment_cfs_number") or ""),
                    }
                    for u in bounded
                ],
            },
        )

    # -- get_analytics_summary ----------------------------------------------

    def _get_analytics_summary(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        hours = tool_input.get("hours")
        period = tool_input.get("period")
        if hours is not None and period is not None:
            raise _ToolInputError("provide either hours or period, not both")
        if hours is None and period is None:
            raise _ToolInputError("one of hours or period is required")

        if hours is not None:
            try:
                hours_int = int(hours)
            except (TypeError, ValueError) as exc:
                raise _ToolInputError("hours must be an integer") from exc
            if not (_MIN_HOURS <= hours_int <= _MAX_HOURS):
                raise _ToolInputError(f"hours must be between {_MIN_HOURS} and {_MAX_HOURS}")
            window_kwargs: dict[str, Any] = {"hours": hours_int}
            window_label = f"Last {hours_int} hours"
        else:
            period_key = str(period)
            if period_key not in _ANALYTICS_PERIOD_KEYS:
                raise _ToolInputError(f"period must be one of {sorted(_ANALYTICS_PERIOD_KEYS)}")
            window_kwargs = {"period": period_key}
            window_label = period_key

        if self._analytics_overview_fn is None:
            source = LiveDataSource(
                name="PostgreSQL analytics",
                kind="historical",
                detail=f"Window: {window_label}",
                available=False,
            )
            return LiveToolResult("get_analytics_summary", source, {"available": False})

        overview = self._analytics_overview_fn(**window_kwargs)
        available = bool(overview.get("available"))
        timestamp = str(overview.get("latest_data_at") or overview.get("generated_at") or "")
        source = LiveDataSource(
            name="PostgreSQL analytics",
            kind="historical",
            detail=f"Window: {window_label}",
            available=available,
            timestamp=timestamp,
        )
        if not available:
            return LiveToolResult("get_analytics_summary", source, {"available": False})

        metrics = overview.get("metrics") or {}
        payload = {
            "available": True,
            "window": window_label,
            "metrics": dict(metrics),
            "busiest_stations": _top_rows(overview.get("busiest_stations"), MAX_BUSIEST_ROWS),
            "busiest_units": _top_rows(overview.get("busiest_units"), MAX_BUSIEST_ROWS),
            "incident_types": _top_rows(overview.get("incident_types"), MAX_INCIDENT_TYPE_ROWS),
            "latest_data_at": timestamp,
        }
        return LiveToolResult("get_analytics_summary", source, payload)

    _HANDLERS: dict[str, Callable[["LiveToolRegistry", Mapping[str, Any]], LiveToolResult]] = {}


LiveToolRegistry._HANDLERS = {
    "list_active_calls": LiveToolRegistry._list_active_calls,
    "get_call_detail": LiveToolRegistry._get_call_detail,
    "get_analytics_summary": LiveToolRegistry._get_analytics_summary,
    "search_calls": LiveToolRegistry._search_calls,
    "search_units": LiveToolRegistry._search_units,
}


class _ToolInputError(ValueError):
    """Raised for bad tool input; converted to an error payload, never raised further."""


TOOL_SPECS: tuple[Mapping[str, Any], ...] = (
    {
        "toolSpec": {
            "name": "list_active_calls",
            "description": (
                "List every call in the current read-only active-call snapshot, with "
                "cfs_number, incident description, location, priority, agency, status, "
                "call_datetime, assigned units, coordinates, and reporter/caller info. "
                "Full command-log history for a call is available via get_call_detail. "
                "Use this to answer questions about what is happening right now."
            ),
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    },
    {
        "toolSpec": {
            "name": "get_call_detail",
            "description": (
                "Get full detail for one specific active call by its CFS number "
                "(format CFSnn-nnnnnn), including coordinates, reporter/caller info "
                "(name and phone), beat/zone/city, and the full command-log history. "
                "Only works for calls currently in the active snapshot."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "cfs_number": {
                            "type": "string",
                            "description": "e.g. CFS26-25863",
                        }
                    },
                    "required": ["cfs_number"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_calls",
            "description": (
                "Search the current read-only active-call snapshot with structured "
                "filters, combinable in one call: incident_code (substring match "
                "against incident code/description), agency (exact match), status "
                "(exact match), priority (exact match), priority_min/priority_max "
                "(integer range, e.g. set priority_max to find only the highest-"
                "priority calls -- lower numbers are higher priority), location "
                "(substring match against address or city), and unit_number "
                "(matches calls with an assigned unit whose number contains this "
                "text). All filters are optional and are ANDed together; omit all "
                "of them to match every call in the snapshot. Returns a count of "
                "all matches plus up to `limit` (default and max 50) summarized "
                "call records. Unknown filter keys or wrong-typed values are "
                "rejected with an error instead of run. Use get_call_detail for "
                "the full record of one matched call."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "incident_code": {"type": "string", "maxLength": 128},
                        "agency": {"type": "string", "maxLength": 128},
                        "status": {"type": "string", "maxLength": 128},
                        "priority": {"type": "string", "maxLength": 128},
                        "priority_min": {"type": "integer"},
                        "priority_max": {"type": "integer"},
                        "location": {"type": "string", "maxLength": 128},
                        "unit_number": {"type": "string", "maxLength": 128},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_SEARCH_CALL_RESULTS,
                        },
                    },
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_units",
            "description": (
                "Search the current read-only unit roster with structured filters, "
                "combinable in one call: agency (exact match), unit_type (substring "
                "match), status (exact match), station (substring match). All "
                "filters are optional and are ANDed together; omit all of them to "
                "list the whole roster. Returns a count of all matches plus up to "
                "`limit` (default and max 100) unit records with unit_number, "
                "agency, unit_type, status, station, and assignment_cfs_number. "
                "Unknown filter keys or wrong-typed values are rejected with an "
                "error instead of run."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "agency": {"type": "string", "maxLength": 128},
                        "unit_type": {"type": "string", "maxLength": 128},
                        "status": {"type": "string", "maxLength": 128},
                        "station": {"type": "string", "maxLength": 128},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_SEARCH_UNIT_RESULTS,
                        },
                    },
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_analytics_summary",
            "description": (
                "Get historical analytics for a time window: total calls, average "
                "response time, busiest stations/units, top incident types. Provide "
                "EITHER hours (an exact integer window ending now, e.g. 8 for the "
                "last 8 hours) OR period (one of 24h, 7d, 30d, 90d, 365d) -- never both."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "hours": {"type": "integer", "minimum": 1, "maximum": 8784},
                        "period": {"type": "string", "enum": sorted(_ANALYTICS_PERIOD_KEYS)},
                    },
                }
            },
        }
    },
)
