"""Read-only tool registry for MAE's Bedrock Converse tool-calling loop.

Unlike ``live_data.py`` (fixed regex intent -> fixed precomputed fact), this
module lets the model decide *which* of a small allowlisted set of read-only
tools to call and *how many times*, so it can answer questions the fixed
intent list cannot. The safety property is different but still structural:
no tool here can write, dispatch, acknowledge, or page -- those operations
have no corresponding tool, so the model cannot invoke them no matter what
it is asked. See ``FORBIDDEN_OPERATIONS`` in ``cloud_read_config.py``.

Most tools read only the already-polled, already-sanitized CAD snapshot
(``CloudCadDisplayState.calls`` / ``.units``, the same ``CALL_FIELDS`` /
``UNIT_FIELDS`` the dashboard and map render) or the analytics overview
(``get_analytics_overview``); those make no new CentralSquare API call.
``search_calls``/``search_units`` add structured, server-validated filtering
over that same snapshot (see
``docs/planning/MAE_FULL_READ_API_TOOLSET_2026-08-08.md`` sections 2.1/2.4).

Two tools are the exception and DO make a live CentralSquare read:
``search_call_history`` (POST ``/cfs_core/search`` via the injected
``cad_connector``, time-bounded to at most 92 days and capped pages/rows)
and ``get_cad_configurations`` (GET ``/configurations``). Both call only
allowlisted, structurally read-only connector methods
(``CloudCentralSquareReadConnector.search_calls`` /
``.get_configurations``, ``cloud_read_config.py``'s ``allowed_operations``)
and degrade to a clean error payload -- never an exception -- when no
connector is injected (``cad_connector=None``, e.g. cloud CAD disabled for
the tenant) or when the connector call itself fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from app.integrations.cad.cloud_read_connector import CloudCadConnectorError
from app.integrations.cad.cloud_read_runtime import _items, _normalize_calls

from .live_data import CFS_PATTERN, LiveDataSource

MAX_ACTIVE_CALLS = 50
MAX_DETAIL_COMMAND_LOG_ENTRIES = 40
MAX_BUSIEST_ROWS = 5
MAX_INCIDENT_TYPE_ROWS = 10
MAX_SEARCH_CALL_RESULTS = 50
MAX_SEARCH_UNIT_RESULTS = 100

# search_call_history: live CentralSquare CFS search, bounded to keep worst-
# case latency/cost predictable (mirrors heatmap_service.MAX_HEATMAP_PAGES).
MAX_HISTORY_WINDOW_DAYS = 92
MAX_HISTORY_PAGES = 5
HISTORY_PAGE_SIZE = 100
MAX_HISTORY_RESULTS_DEFAULT = 200

# get_cad_configurations: cap any list nested in the upstream configuration
# payload so a large lookup table doesn't blow the tool-response token budget.
MAX_CONFIG_LIST_ITEMS = 200

_UNAVAILABLE_MESSAGE = "live CAD query is unavailable"

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

# search_call_history filter keys. These ARE native POST /cfs_core/search
# body parameters (see docs/API_KNOWLEDGE_BASE.md "Verified CAD Endpoint
# Detail" section, ~lines 176-192) and are sent to CAD so filtering happens
# server-side, before the row cap -- not after it.
_SEARCH_HISTORY_NATIVE_STRING_FILTERS = (
    "incident_code",
    "location",
    "beat",
    "zone",
    "unit",
    "responder",
    "dispatch_agency",
    "response_agency",
)
_SEARCH_HISTORY_NATIVE_FILTER_TO_KEY = {
    "incident_code": "IncidentCode",
    "location": "Location",
    "beat": "Beat",
    "zone": "Zone",
    "unit": "Unit",
    "responder": "Responder",
}
# status/priority are NOT in the documented native parameter list -- they
# are applied as post-filters over the rows the connector returns, same as
# search_calls does over the snapshot. See module docstring on
# _search_call_history for the accuracy implication (undercounting when the
# fetch was truncated).
_SEARCH_HISTORY_POST_FILTERS = ("status", "priority")
_SEARCH_HISTORY_ALLOWED_KEYS = frozenset(
    _SEARCH_HISTORY_NATIVE_STRING_FILTERS
    + _SEARCH_HISTORY_POST_FILTERS
    + ("created_from", "created_to", "closed_from", "closed_to", "limit")
)
_GET_CONFIGURATIONS_ALLOWED_KEYS = frozenset({"configuration"})


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


def _parse_iso_datetime(tool_input: Mapping[str, Any], key: str) -> datetime:
    value = tool_input.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _ToolInputError(f"{key} is required and must be an ISO-8601 timestamp string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _ToolInputError(f"{key} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_iso_datetime(tool_input: Mapping[str, Any], key: str) -> datetime | None:
    value = tool_input.get(key)
    if value is None:
        return None
    return _parse_iso_datetime(tool_input, key)


def _bound_configuration_value(value: Any) -> tuple[Any, bool]:
    """Recursively cap any list found in a configuration payload.

    Returns (bounded_value, truncated) so the caller can surface one clear
    truncation flag without guessing at the upstream payload's shape.
    """
    if isinstance(value, list):
        bounded = value[:MAX_CONFIG_LIST_ITEMS]
        truncated = len(value) > len(bounded)
        out = []
        for item in bounded:
            item_bounded, item_truncated = _bound_configuration_value(item)
            out.append(item_bounded)
            truncated = truncated or item_truncated
        return out, truncated
    if isinstance(value, Mapping):
        out_map: dict[str, Any] = {}
        truncated = False
        for key, sub_value in value.items():
            sub_bounded, sub_truncated = _bound_configuration_value(sub_value)
            out_map[str(key)] = sub_bounded
            truncated = truncated or sub_truncated
        return out_map, truncated
    return value, False


class LiveToolRegistry:
    """Constructed once per request from the current snapshot; stateless calls."""

    def __init__(
        self,
        *,
        cad_state: Any,
        cad_status: Mapping[str, Any],
        analytics_overview_fn: Callable[..., Mapping[str, Any]] | None,
        cad_connector: Any = None,
    ) -> None:
        self._cad_state = cad_state
        self._cad_status = cad_status
        self._analytics_overview_fn = analytics_overview_fn
        # Optional: the authenticated read-only CentralSquare connector
        # (``CloudCentralSquareReadConnector``), used only by
        # search_call_history/get_cad_configurations to reach the live CAD
        # system. Every other tool here still reads only the polled snapshot.
        # None means live CAD querying is unavailable (disabled tenant, or no
        # connector injected) -- both new tools degrade to a clean error
        # payload rather than raising.
        self._cad_connector = cad_connector

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

    # -- search_call_history (live connector) --------------------------------

    def _search_call_history(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        _reject_unknown_keys(tool_input, _SEARCH_HISTORY_ALLOWED_KEYS)

        created_from = _parse_iso_datetime(tool_input, "created_from")
        created_to = _parse_iso_datetime(tool_input, "created_to")
        if created_from >= created_to:
            raise _ToolInputError("created_from must be before created_to")
        if created_to - created_from > timedelta(days=MAX_HISTORY_WINDOW_DAYS):
            raise _ToolInputError(f"window must be at most {MAX_HISTORY_WINDOW_DAYS} days")

        closed_from = _optional_iso_datetime(tool_input, "closed_from")
        closed_to = _optional_iso_datetime(tool_input, "closed_to")
        if (closed_from is None) != (closed_to is None):
            raise _ToolInputError("closed_from and closed_to must be provided together")
        if closed_from is not None and closed_to is not None:
            if closed_from >= closed_to:
                raise _ToolInputError("closed_from must be before closed_to")
            if closed_to - closed_from > timedelta(days=MAX_HISTORY_WINDOW_DAYS):
                raise _ToolInputError(f"closed window must be at most {MAX_HISTORY_WINDOW_DAYS} days")

        native_filters: dict[str, str] = {}
        for arg_name, api_key in _SEARCH_HISTORY_NATIVE_FILTER_TO_KEY.items():
            value = _optional_str_filter(tool_input, arg_name)
            if value is not None:
                native_filters[api_key] = value
        dispatch_agency = _optional_str_filter(tool_input, "dispatch_agency")
        response_agency = _optional_str_filter(tool_input, "response_agency")

        # Not native -- applied as post-filters below, after the fetch.
        status = _optional_str_filter(tool_input, "status")
        priority = _optional_str_filter(tool_input, "priority")
        limit = _optional_limit(
            tool_input, default=MAX_HISTORY_RESULTS_DEFAULT, maximum=MAX_HISTORY_RESULTS_DEFAULT
        )

        window = {"from": created_from.isoformat(), "to": created_to.isoformat()}

        if self._cad_connector is None:
            source = LiveDataSource(
                name="CentralSquare CAD (live historical search)",
                kind="live",
                detail="No live CAD connector is configured for this tenant",
                available=False,
            )
            return LiveToolResult(
                "search_call_history",
                source,
                {
                    "available": False,
                    "error": _UNAVAILABLE_MESSAGE,
                    "count": 0,
                    "returned": 0,
                    "truncated": False,
                    "window": window,
                    "calls": [],
                },
            )

        body: dict[str, Any] = {
            "RecordCreatedFrom": window["from"],
            "RecordCreatedTo": window["to"],
            "OrderByField": "Created",
            "OrderByDirection": "Descending",
        }
        if closed_from is not None and closed_to is not None:
            body["RecordClosedFrom"] = closed_from.isoformat()
            body["RecordClosedTo"] = closed_to.isoformat()
        body.update(native_filters)
        if dispatch_agency is not None:
            body["DispatchAgencies"] = [dispatch_agency]
        if response_agency is not None:
            body["ResponseAgencies"] = [response_agency]

        calls_by_number: dict[str, Mapping[str, Any]] = {}
        truncated = False
        skip = 0
        try:
            for _page_number in range(MAX_HISTORY_PAGES):
                result = self._cad_connector.search_calls(body, skip=skip, limit=HISTORY_PAGE_SIZE)
                page_calls = _items(result, ("cfs_cores", "CFSCore", "calls", "items"))
                for raw_call in page_calls:
                    cfs_number = str(raw_call.get("CFSNumber") or raw_call.get("cfs_number") or "")
                    if not cfs_number:
                        continue
                    if cfs_number not in calls_by_number:
                        calls_by_number[cfs_number] = raw_call
                    if len(calls_by_number) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
                page_len = len(page_calls)
                if page_len < HISTORY_PAGE_SIZE:
                    break
                skip += page_len
            else:
                # Exhausted the page cap without a short page -- more rows may
                # exist upstream than we retrieved.
                truncated = True
        except CloudCadConnectorError as error:
            source = LiveDataSource(
                name="CentralSquare CAD (live historical search)",
                kind="live",
                detail=f"search_calls failed: {error.code}",
                available=False,
            )
            return LiveToolResult(
                "search_call_history",
                source,
                {"available": False, "error": f"CAD history query failed: {error.code}", "window": window},
            )
        except Exception:
            source = LiveDataSource(
                name="CentralSquare CAD (live historical search)",
                kind="live",
                detail="Unexpected error running the historical search",
                available=False,
            )
            return LiveToolResult(
                "search_call_history",
                source,
                {"available": False, "error": "unexpected error querying CAD history", "window": window},
            )

        normalized = _normalize_calls(list(calls_by_number.values()))

        def matches(call: Mapping[str, Any]) -> bool:
            if status and not _equals_ci(call.get("status"), status):
                return False
            if priority and not _equals_ci(call.get("priority"), priority):
                return False
            return True

        post_filter_used = bool(status or priority)
        filtered = [call for call in normalized if matches(call)] if post_filter_used else normalized
        source = LiveDataSource(
            name="CentralSquare CAD (live historical search)",
            kind="live",
            detail=f"search_calls window {window['from']} to {window['to']}",
            available=True,
        )
        payload = {
            "available": True,
            "count": len(calls_by_number),
            "returned": len(filtered),
            "truncated": truncated,
            "window": window,
            "calls": [_summarize_call(c) for c in filtered],
        }
        if post_filter_used and truncated:
            payload["post_filter_warning"] = (
                "status/priority filtered after a truncated fetch; counts may be incomplete"
            )
        return LiveToolResult("search_call_history", source, payload)

    # -- get_cad_configurations (live connector) -----------------------------

    def _get_cad_configurations(self, tool_input: Mapping[str, Any]) -> LiveToolResult:
        _reject_unknown_keys(tool_input, _GET_CONFIGURATIONS_ALLOWED_KEYS)

        configuration = tool_input.get("configuration")
        if (
            not isinstance(configuration, str)
            or not configuration
            or len(configuration) > 64
            or not configuration.isidentifier()
        ):
            raise _ToolInputError(
                "configuration must be a non-empty identifier of at most 64 characters"
            )

        if self._cad_connector is None:
            source = LiveDataSource(
                name="CentralSquare CAD (live configuration lookup)",
                kind="live",
                detail="No live CAD connector is configured for this tenant",
                available=False,
            )
            return LiveToolResult(
                "get_cad_configurations",
                source,
                {"available": False, "error": _UNAVAILABLE_MESSAGE, "configuration": configuration},
            )

        try:
            result = self._cad_connector.get_configurations(configuration)
        except CloudCadConnectorError as error:
            source = LiveDataSource(
                name="CentralSquare CAD (live configuration lookup)",
                kind="live",
                detail=f"get_configurations failed: {error.code}",
                available=False,
            )
            return LiveToolResult(
                "get_cad_configurations",
                source,
                {
                    "available": False,
                    "error": f"CAD configuration query failed: {error.code}",
                    "configuration": configuration,
                },
            )
        except Exception:
            source = LiveDataSource(
                name="CentralSquare CAD (live configuration lookup)",
                kind="live",
                detail="Unexpected error fetching configuration",
                available=False,
            )
            return LiveToolResult(
                "get_cad_configurations",
                source,
                {
                    "available": False,
                    "error": "unexpected error querying CAD configuration",
                    "configuration": configuration,
                },
            )

        bounded_result, truncated = _bound_configuration_value(result)
        source = LiveDataSource(
            name="CentralSquare CAD (live configuration lookup)",
            kind="live",
            detail=f"get_configurations {configuration}",
            available=True,
        )
        return LiveToolResult(
            "get_cad_configurations",
            source,
            {
                "available": True,
                "configuration": configuration,
                "truncated": truncated,
                "result": bounded_result,
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
    "search_call_history": LiveToolRegistry._search_call_history,
    "get_cad_configurations": LiveToolRegistry._get_cad_configurations,
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
    {
        "toolSpec": {
            "name": "search_call_history",
            "description": (
                "Search HISTORICAL Calls-For-Service by making a live query to the "
                "CentralSquare CAD system -- NOT the current snapshot, and NOT free: "
                "this makes a real network call to CAD every time it is used. Use "
                "list_active_calls/search_calls (over the already-polled snapshot) "
                "for anything about what is happening right now; use this tool only "
                "when the question is genuinely about the past (a specific date, a "
                "closed/older call, or a historical count/lookup that the current "
                "snapshot cannot answer). Requires created_from AND created_to "
                "(ISO-8601 timestamps, e.g. 2026-08-01T00:00:00Z); the window is "
                "capped at 92 days and an unbounded query is always rejected. "
                "closed_from/closed_to (both required together, same ISO-8601 "
                "format, also capped at 92 days) additionally restrict to calls "
                "closed in that window. "
                "incident_code, location, beat, zone, unit, responder, "
                "dispatch_agency, and response_agency are NATIVE CentralSquare "
                "search parameters -- CAD itself filters on them BEFORE the row "
                "cap is applied, so the cap applies to matching calls, not to the "
                "first N calls of any type. This is what makes them accurate for "
                "questions like \"how many medical calls in the last 30 days\": use "
                "incident_code, not a post-filter, for that. dispatch_agency and "
                "response_agency are each sent to CAD as a single-element list "
                "(DispatchAgencies/ResponseAgencies). "
                "status and priority are NOT native CentralSquare search "
                "parameters -- they are applied as post-filters over the rows CAD "
                "returns for the time window (and any native filters above), "
                "which means they can UNDERCOUNT if the fetch itself was truncated "
                "(see `truncated`): the true count of matching calls may be higher "
                "than what `returned` shows. When a post-filter is combined with a "
                "truncated fetch, the response includes a `post_filter_warning` "
                "field -- treat any status/priority count as a lower bound, not an "
                "authoritative total, whenever that warning is present. Results "
                "are deduplicated by CFS number, paginated up to 5 pages of 100 rows "
                "each, and capped at `limit` (default and max 200) total rows."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "created_from": {
                            "type": "string",
                            "description": "ISO-8601 window start, e.g. 2026-08-01T00:00:00Z",
                        },
                        "created_to": {
                            "type": "string",
                            "description": "ISO-8601 window end, e.g. 2026-08-02T00:00:00Z",
                        },
                        "closed_from": {
                            "type": "string",
                            "description": "Optional ISO-8601 closed-window start; must be paired with closed_to",
                        },
                        "closed_to": {
                            "type": "string",
                            "description": "Optional ISO-8601 closed-window end; must be paired with closed_from",
                        },
                        "incident_code": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Native filter (IncidentCode)",
                        },
                        "location": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Native filter (Location)",
                        },
                        "beat": {"type": "string", "maxLength": 128, "description": "Native filter (Beat)"},
                        "zone": {"type": "string", "maxLength": 128, "description": "Native filter (Zone)"},
                        "unit": {"type": "string", "maxLength": 128, "description": "Native filter (Unit)"},
                        "responder": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Native filter (Responder)",
                        },
                        "dispatch_agency": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Native filter, sent as a single-element list (DispatchAgencies)",
                        },
                        "response_agency": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Native filter, sent as a single-element list (ResponseAgencies)",
                        },
                        "status": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Post-filter only (not native); may undercount if fetch was truncated",
                        },
                        "priority": {
                            "type": "string",
                            "maxLength": 128,
                            "description": "Post-filter only (not native); may undercount if fetch was truncated",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_HISTORY_RESULTS_DEFAULT,
                        },
                    },
                    "required": ["created_from", "created_to"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_cad_configurations",
            "description": (
                "Look up one named CentralSquare CAD system configuration/lookup "
                "table (e.g. CADUnitStatus, IncidentType) by making a live query to "
                "the CAD system -- NOT the current snapshot. Use this only when the "
                "question is about CAD's own reference/configuration data (what "
                "status codes exist, what incident types are defined, etc.), not "
                "about a specific call or unit. `configuration` must be a valid "
                "identifier (letters/digits/underscore, not starting with a digit) "
                "of at most 64 characters. Any list nested in the result is capped "
                "at 200 entries, with `truncated` set to true if anything was cut."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "configuration": {
                            "type": "string",
                            "maxLength": 64,
                            "description": "e.g. CADUnitStatus, IncidentType",
                        },
                    },
                    "required": ["configuration"],
                }
            },
        }
    },
)
