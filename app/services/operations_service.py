from datetime import datetime, timezone

from app.services.cad_service import get_active_calls
from app.services.centralsquare import CentralSquareAPIError, CentralSquareClient
from app.services.unit_service import classify_unit, get_all_units


def _safe_priority_level(call: dict) -> int:
    try:
        return int(call.get("priority") or 999)
    except (TypeError, ValueError):
        return 999


def _parse_call_datetime(value: str) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)

    try:
        cleaned_value = str(value)

        if cleaned_value.endswith("Z"):
            cleaned_value = cleaned_value.replace("Z", "+00:00")

        parsed_value = datetime.fromisoformat(cleaned_value)

        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)

        return parsed_value

    except (TypeError, ValueError):
        return datetime.max.replace(tzinfo=timezone.utc)


def sort_dashboard_calls(calls: list) -> list:
    return sorted(
        calls,
        key=lambda call: (
            _safe_priority_level(call),
            _parse_call_datetime(call.get("call_datetime")),
        ),
    )


def build_dashboard_stats(calls: list) -> dict:
    unique_units = set()
    on_scene_units = set()
    agency_counts = {}
    high_priority_calls = 0
    oldest_call_datetime = ""

    for call in calls:
        priority = _safe_priority_level(call)

        if 1 <= priority <= 15:
            high_priority_calls += 1

        agency = call.get("agency") or "Unknown"
        agency_counts[agency] = agency_counts.get(agency, 0) + 1

        for unit in call.get("assigned_units") or []:
            unit_number = unit.get("unit_number")
            if unit_number:
                unique_units.add(unit_number)
                if normalize_unit_status(unit.get("status") or "") == "On Scene":
                    on_scene_units.add(unit_number)

        call_datetime = call.get("call_datetime") or ""
        if call_datetime and (
            not oldest_call_datetime
            or _parse_call_datetime(call_datetime)
            < _parse_call_datetime(oldest_call_datetime)
        ):
            oldest_call_datetime = call_datetime

    agency_summary = [
        {"agency": agency, "count": count}
        for agency, count in sorted(
            agency_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    return {
        "active_calls": len(calls),
        "assigned_units": len(unique_units),
        "on_scene_units": len(on_scene_units),
        "high_priority_calls": high_priority_calls,
        "oldest_call_datetime": oldest_call_datetime,
        "agency_summary": agency_summary,
    }


def normalize_unit_status(status: str) -> str:
    normalized_status = (status or "").lower()

    if "transport" in normalized_status:
        return "Transporting"

    if "scene" in normalized_status or "arriv" in normalized_status:
        return "On Scene"

    if "route" in normalized_status:
        return "Enroute"

    if "clear" in normalized_status or "complete" in normalized_status:
        return "Cleared"

    if "assign" in normalized_status or "dispatch" in normalized_status:
        return "Assigned"

    return status or "Unknown"


def unit_status_rank(status: str) -> int:
    normalized_status = normalize_unit_status(status)

    ranks = {
        "Transporting": 1,
        "Enroute": 2,
        "On Scene": 3,
        "Assigned": 4,
        "Cleared": 8,
        "Unknown": 9,
    }

    return ranks.get(normalized_status, 9)


def build_unit_board(calls: list) -> list:
    unit_rows = []

    for call in calls:
        assigned_units = call.get("assigned_units") or []

        for unit in assigned_units:
            unit_number = unit.get("unit_number") or ""
            status = unit.get("status") or "Unknown"
            status_group = normalize_unit_status(status)

            unit_rows.append(
                {
                    "unit_number": unit_number,
                    "unit_type": unit.get("unit_type") or "",
                    "agency": unit.get("agency") or call.get("agency") or "",
                    "status": status,
                    "status_group": status_group,
                    "responder": unit.get("responder") or "",
                    "dispatch_time": unit.get("dispatch_time") or "",
                    "enroute_time": unit.get("enroute_time") or "",
                    "arrival_time": unit.get("arrival_time") or "",
                    "transport_time": unit.get("transport_time") or "",
                    "clear_time": unit.get("clear_time") or "",
                    "status_timer_start": unit.get("status_timer_start") or "",
                    "cfs_number": call.get("cfs_number") or "",
                    "incident_code": call.get("incident_code") or "",
                    "incident_description": call.get("incident_description") or "",
                    "location": call.get("location") or "",
                    "priority": call.get("priority") or "",
                    "call_datetime": call.get("call_datetime") or "",
                    "call_status": call.get("status") or "",
                }
            )

    return sorted(
        unit_rows,
        key=lambda unit: (
            unit_status_rank(unit.get("status")),
            _safe_priority_level({"priority": unit.get("priority")}),
            unit.get("unit_number") or "",
        ),
    )


def build_unit_board_stats(unit_rows: list) -> dict:
    status_counts = {
        "Assigned": 0,
        "Enroute": 0,
        "On Scene": 0,
        "Transporting": 0,
        "Cleared": 0,
        "Unknown": 0,
    }

    agency_counts = {}

    for unit in unit_rows:
        status_group = unit.get("status_group") or "Unknown"

        if status_group not in status_counts:
            status_group = "Unknown"

        status_counts[status_group] += 1

        agency = unit.get("agency") or "Unknown"
        agency_counts[agency] = agency_counts.get(agency, 0) + 1

    status_summary = [
        {"status": status, "count": count}
        for status, count in status_counts.items()
        if count > 0
    ]

    agency_summary = [
        {"agency": agency, "count": count}
        for agency, count in sorted(
            agency_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    return {
        "total_units": len(unit_rows),
        "assigned_units": status_counts["Assigned"],
        "enroute_units": status_counts["Enroute"],
        "on_scene_units": status_counts["On Scene"],
        "transporting_units": status_counts["Transporting"],
        "cleared_units": status_counts["Cleared"],
        "unknown_units": status_counts["Unknown"],
        "status_summary": status_summary,
        "agency_summary": agency_summary,
    }


def build_empty_operations_snapshot() -> dict:
    calls = []
    unit_rows = []

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "calls": calls,
        "dashboard_stats": build_dashboard_stats(calls),
        "unit_rows": unit_rows,
        "unit_stats": build_unit_board_stats(unit_rows),
    }


def get_live_operations_snapshot() -> dict:
    calls = sort_dashboard_calls(get_active_calls())
    unit_rows = build_unit_board(calls)

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "calls": calls,
        "dashboard_stats": build_dashboard_stats(calls),
        "unit_rows": unit_rows,
        "unit_stats": build_unit_board_stats(unit_rows),
    }


def build_full_unit_roster(roster_units: list, active_unit_rows: list) -> dict:
    assignments_by_unit = {}

    for assignment in active_unit_rows:
        unit_number = assignment.get("unit_number") or ""
        if not unit_number:
            continue

        assignments_by_unit.setdefault(unit_number.upper(), []).append(assignment)

    roster_by_unit = {}

    for unit in roster_units:
        unit_number = unit.get("unit_number") or ""
        if not unit_number:
            continue

        roster_by_unit.setdefault(unit_number.upper(), unit)

    groups = {
        "active_units": [],
        "operational_units": [],
        "available_units": [],
        "unavailable_units": [],
        "unknown_units": [],
    }

    for unit_key, unit in roster_by_unit.items():
        assignments = assignments_by_unit.pop(unit_key, [])

        if assignments:
            primary_assignment = assignments[0]
            merged_unit = {
                **unit,
                **primary_assignment,
                "roster_status": unit.get("status") or "Unknown",
                "roster_last_status_time": unit.get("last_status_time") or "",
                "assignments": assignments,
            }

            for metadata_field in ("unit_type", "agency", "responder"):
                if not merged_unit.get(metadata_field):
                    merged_unit[metadata_field] = unit.get(metadata_field) or ""

            groups["active_units"].append(merged_unit)
            continue

        group_name = classify_unit(unit)

        if group_name == "active":
            groups["operational_units"].append(
                {
                    **unit,
                    "assignments": [],
                }
            )
            continue

        groups[f"{group_name}_units"].append(
            {
                **unit,
                "assignments": [],
            }
        )

    for assignments in assignments_by_unit.values():
        primary_assignment = assignments[0]
        groups["active_units"].append(
            {
                **primary_assignment,
                "roster_status": "Not returned by unit roster",
                "roster_last_status_time": "",
                "assignments": assignments,
            }
        )

    groups["active_units"] = sorted(
        groups["active_units"],
        key=lambda unit: (
            unit_status_rank(unit.get("status")),
            _safe_priority_level({"priority": unit.get("priority")}),
            unit.get("unit_number") or "",
        ),
    )

    for group_name in (
        "operational_units",
        "available_units",
        "unavailable_units",
        "unknown_units",
    ):
        groups[group_name] = sorted(
            groups[group_name],
            key=lambda unit: (
                unit.get("agency") or "",
                unit.get("unit_number") or "",
            ),
        )

    all_units = (
        groups["active_units"]
        + groups["operational_units"]
        + groups["available_units"]
        + groups["unavailable_units"]
        + groups["unknown_units"]
    )

    status_counts = {}
    for unit in all_units:
        status = unit.get("roster_status") or unit.get("status") or "Unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    groups["all_units"] = all_units
    groups["roster_stats"] = {
        "total_units": len(all_units),
        "active_units": len(groups["active_units"]) + len(groups["operational_units"]),
        "assigned_units": len(groups["active_units"]),
        "operational_units": len(groups["operational_units"]),
        "available_units": len(groups["available_units"]),
        "unavailable_units": len(groups["unavailable_units"]),
        "unknown_units": len(groups["unknown_units"]),
        "status_summary": [
            {"status": status, "count": count}
            for status, count in sorted(
                status_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    }

    return groups


def build_empty_unit_snapshot() -> dict:
    groups = build_full_unit_roster([], [])

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "calls": [],
        "roster_connected": False,
        "roster_warning": "Full unit roster unavailable.",
        "active_stats": build_unit_board_stats(groups["active_units"]),
        **groups,
    }


def get_live_unit_snapshot() -> dict:
    client = CentralSquareClient()
    calls = sort_dashboard_calls(get_active_calls(client=client))
    active_unit_rows = build_unit_board(calls)

    try:
        roster_units = get_all_units(client=client)
        roster_connected = True
        roster_warning = ""
    except CentralSquareAPIError:
        roster_units = []
        roster_connected = False
        roster_warning = "Full unit roster unavailable; showing active-call units only."

    groups = build_full_unit_roster(roster_units, active_unit_rows)

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "calls": calls,
        "roster_connected": roster_connected,
        "roster_warning": roster_warning,
        "active_stats": build_unit_board_stats(groups["active_units"]),
        **groups,
    }
