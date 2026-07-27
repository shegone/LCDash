from datetime import datetime, timedelta, timezone
from threading import Lock

from app.auth.oauth import CentralSquareAuthError
from app.services.cad_service import get_active_calls
from app.services.centralsquare import CentralSquareAPIError, CentralSquareClient
from app.services.operations_service import (
    build_full_unit_roster,
    build_unit_board,
    sort_dashboard_calls,
)
from app.services.unit_service import get_all_units


STATION_ROSTER_CACHE_SECONDS = 60
STATION_CLIENT_CACHE_SECONDS = 15 * 60

_roster_cache_lock = Lock()
_client_cache_lock = Lock()
_roster_cache = {
    "units": [],
    "updated_at": None,
}
_client_cache = {
    "client": None,
    "created_at": None,
    "retry_after": None,
    "error": "",
}


def _safe_text(value) -> str:
    return str(value or "").strip()


def _station_key(value) -> str:
    return _safe_text(value).casefold()


def _selected_station_names(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = [value]

    selected = []
    seen = set()
    for candidate in candidates:
        station_name = _safe_text(candidate)
        station_key = _station_key(station_name)
        if not station_key or station_key in seen:
            continue
        seen.add(station_key)
        selected.append(station_name)
    return selected


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        cleaned = str(value)
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def build_station_catalog(units: list) -> list:
    stations = {}

    for unit in units:
        station_name = _safe_text(unit.get("station"))
        unit_number = _safe_text(unit.get("unit_number"))
        if not station_name or not unit_number:
            continue

        key = _station_key(station_name)
        station = stations.setdefault(
            key,
            {
                "name": station_name,
                "unit_numbers": set(),
                "agencies": set(),
            },
        )
        station["unit_numbers"].add(unit_number)
        agency = _safe_text(unit.get("agency"))
        if agency:
            station["agencies"].add(agency)

    return sorted(
        [
            {
                "name": station["name"],
                "unit_count": len(station["unit_numbers"]),
                "agencies": sorted(station["agencies"]),
            }
            for station in stations.values()
        ],
        key=lambda station: station["name"].casefold(),
    )


def _station_units(all_units: list, selected_stations) -> list:
    selected_keys = {
        _station_key(station)
        for station in _selected_station_names(selected_stations)
    }
    if not selected_keys:
        return []

    return sorted(
        [
            unit
            for unit in all_units
            if _station_key(unit.get("station")) in selected_keys
        ],
        key=lambda unit: (
            _station_key(unit.get("station")),
            _safe_text(unit.get("unit_number")).casefold(),
        ),
    )


def _station_unit_sort_key(unit: dict) -> tuple:
    status = _safe_text(unit.get("status")).casefold()

    if "available" in status:
        rank = 0
    elif unit.get("cfs_number") or any(
        active_status in status
        for active_status in (
            "assigned",
            "dispatch",
            "enroute",
            "en route",
            "scene",
            "arriv",
            "transport",
        )
    ):
        rank = 1
    elif any(
        unavailable_status in status
        for unavailable_status in (
            "off duty",
            "out of service",
            "unavailable",
            "mechanical",
            "maintenance",
        )
    ):
        rank = 2
    else:
        rank = 3

    return rank, _safe_text(unit.get("unit_number")).casefold()


def _assignment_rows(unit: dict) -> list:
    assignments = unit.get("assignments") or []
    if assignments:
        return [assignment for assignment in assignments if isinstance(assignment, dict)]

    if unit.get("cfs_number"):
        return [unit]

    return []


def _alert_event_time(assignments: list, station_units: list, call: dict) -> str:
    candidates = []

    for assignment in assignments:
        candidates.append(assignment.get("dispatch_time"))

    candidates.extend(unit.get("last_assigned_time") for unit in station_units)
    candidates.append(call.get("call_datetime"))

    valid = [_safe_text(value) for value in candidates if _safe_text(value)]
    if not valid:
        return ""

    return max(valid, key=_parse_datetime)


def build_station_alert_snapshot(unit_snapshot: dict, selected_stations=None) -> dict:
    all_units = unit_snapshot.get("all_units") or []
    calls = unit_snapshot.get("calls") or []
    selected_station_names = _selected_station_names(selected_stations)
    station_units = _station_units(all_units, selected_station_names)
    station_unit_numbers = {
        _safe_text(unit.get("unit_number")).upper()
        for unit in station_units
        if _safe_text(unit.get("unit_number"))
    }

    calls_by_number = {
        _safe_text(call.get("cfs_number")): call
        for call in calls
        if _safe_text(call.get("cfs_number"))
    }
    grouped_assignments = {}

    for unit in station_units:
        for assignment in _assignment_rows(unit):
            cfs_number = _safe_text(assignment.get("cfs_number"))
            unit_number = _safe_text(assignment.get("unit_number"))
            if not cfs_number or not unit_number:
                continue
            grouped_assignments.setdefault(cfs_number, []).append(assignment)

    alerts = []
    for cfs_number, assignments in grouped_assignments.items():
        call = calls_by_number.get(cfs_number) or {}
        assigned_numbers = sorted(
            {
                _safe_text(assignment.get("unit_number"))
                for assignment in assignments
                if _safe_text(assignment.get("unit_number")).upper() in station_unit_numbers
            }
        )
        assigned_number_keys = {number.upper() for number in assigned_numbers}
        related_station_units = [
            unit
            for unit in station_units
            if _safe_text(unit.get("unit_number")).upper() in assigned_number_keys
        ]
        alert_station_names = sorted(
            _selected_station_names(
                [
                    unit.get("station")
                    for unit in related_station_units
                ]
            ),
            key=str.casefold,
        )
        event_time = _alert_event_time(assignments, related_station_units, call)
        event_id = "|".join(
            [
                ",".join(_station_key(station) for station in alert_station_names),
                cfs_number,
                ",".join(assigned_numbers),
                event_time,
            ]
        )

        alerts.append(
            {
                "event_id": event_id,
                "cfs_number": cfs_number,
                "incident_code": _safe_text(call.get("incident_code") or assignments[0].get("incident_code")),
                "incident_description": _safe_text(call.get("incident_description") or assignments[0].get("incident_description")) or "CAD Dispatch",
                "priority": _safe_text(call.get("priority") or assignments[0].get("priority")),
                "location": _safe_text(call.get("location") or assignments[0].get("location")) or "Location unavailable",
                "call_datetime": _safe_text(call.get("call_datetime") or assignments[0].get("call_datetime")),
                "dispatch_datetime": event_time,
                "status": _safe_text(call.get("status") or assignments[0].get("call_status")) or "Open",
                "unit_numbers": assigned_numbers,
                "station_names": alert_station_names,
                "latitude": call.get("latitude"),
                "longitude": call.get("longitude"),
            }
        )

    alerts = sorted(
        alerts,
        key=lambda alert: _parse_datetime(alert.get("dispatch_datetime")),
        reverse=True,
    )

    sanitized_station_units = [
        {
            "unit_number": _safe_text(unit.get("unit_number")),
            "agency": _safe_text(unit.get("agency")),
            "unit_type": _safe_text(unit.get("unit_type")),
            "station": _safe_text(unit.get("station")),
            "status": _safe_text(unit.get("roster_status") or unit.get("status")) or "Unknown",
            "cfs_number": _safe_text(unit.get("cfs_number")),
        }
        for unit in station_units
    ]
    sanitized_station_units.sort(key=_station_unit_sort_key)

    return {
        "connected": True,
        "roster_connected": bool(unit_snapshot.get("roster_connected", True)),
        "roster_warning": _safe_text(unit_snapshot.get("roster_warning")),
        "generated_at": _safe_text(unit_snapshot.get("last_updated")) or datetime.now(timezone.utc).isoformat(),
        "selected_station": selected_station_names[0] if selected_station_names else "",
        "selected_stations": selected_station_names,
        "stations": build_station_catalog(all_units),
        "station_units": sanitized_station_units,
        "alerts": alerts,
    }


def build_empty_station_alert_snapshot(selected_stations=None, error: str = "") -> dict:
    selected_station_names = _selected_station_names(selected_stations)
    return {
        "connected": False,
        "roster_connected": False,
        "roster_warning": "Station roster unavailable.",
        "error": _safe_text(error),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_station": selected_station_names[0] if selected_station_names else "",
        "selected_stations": selected_station_names,
        "stations": [],
        "station_units": [],
        "alerts": [],
    }


def _cached_roster(client: CentralSquareClient, now: datetime) -> tuple[list, str]:
    with _roster_cache_lock:
        cached_units = list(_roster_cache["units"])
        cached_at = _roster_cache["updated_at"]
        cache_is_fresh = (
            cached_at is not None
            and now - cached_at < timedelta(seconds=STATION_ROSTER_CACHE_SECONDS)
        )
        if cache_is_fresh:
            return cached_units, ""

        try:
            roster_units = get_all_units(client=client)
        except CentralSquareAPIError:
            if cached_units:
                return cached_units, "Using the most recent cached station roster."
            raise

        _roster_cache["units"] = list(roster_units)
        _roster_cache["updated_at"] = now
        return roster_units, ""


def _cached_client(now: datetime) -> CentralSquareClient:
    with _client_cache_lock:
        client = _client_cache["client"]
        created_at = _client_cache["created_at"]
        retry_after = _client_cache["retry_after"]
        if retry_after is not None and now < retry_after:
            raise CentralSquareAPIError(
                _client_cache["error"] or "CentralSquare authentication is temporarily unavailable."
            )

        cache_is_fresh = (
            client is not None
            and created_at is not None
            and now - created_at < timedelta(seconds=STATION_CLIENT_CACHE_SECONDS)
        )
        if cache_is_fresh:
            return client

        try:
            client = CentralSquareClient()
        except CentralSquareAuthError as exc:
            _client_cache["retry_after"] = now + timedelta(seconds=60)
            _client_cache["error"] = str(exc)
            raise CentralSquareAPIError(str(exc)) from exc

        _client_cache["client"] = client
        _client_cache["created_at"] = now
        _client_cache["retry_after"] = None
        _client_cache["error"] = ""
        return client


def get_live_station_alert_snapshot(selected_stations=None) -> dict:
    now = datetime.now(timezone.utc)
    client = _cached_client(now)
    calls = sort_dashboard_calls(get_active_calls(client=client))
    active_unit_rows = build_unit_board(calls)
    roster_units, roster_warning = _cached_roster(client, now)
    groups = build_full_unit_roster(roster_units, active_unit_rows)
    unit_snapshot = {
        "last_updated": now.isoformat(),
        "calls": calls,
        "roster_connected": True,
        "roster_warning": roster_warning,
        **groups,
    }
    return build_station_alert_snapshot(unit_snapshot, selected_stations)
