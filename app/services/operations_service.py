from datetime import datetime, timezone

from app.config.settings import settings
from app.core.county_profiles import resolve_county_profile
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import authorize_tenant_action
from app.integrations.contracts import ModuleCapability
from app.services.cad_service import get_active_calls
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)
from app.services.centralsquare import CentralSquareAPIError
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
    agency_counts = {}
    high_priority_calls = 0
    on_scene_calls = 0
    oldest_call_datetime = ""

    for call in calls:
        priority = _safe_priority_level(call)

        if 1 <= priority <= 15:
            high_priority_calls += 1

        if normalize_unit_status(call.get("status") or "") == "On Scene":
            on_scene_calls += 1

        agency = call.get("agency") or "Unknown"
        agency_counts[agency] = agency_counts.get(agency, 0) + 1

        for unit in call.get("assigned_units") or []:
            unit_number = unit.get("unit_number")
            if unit_number:
                unique_units.add(unit_number)

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
        "on_scene_calls": on_scene_calls,
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


def _cloud_display_text(value) -> str:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)[:256]
    return ""


def _cloud_command_logs(item) -> list[dict[str, str]]:
    value = item.get("command_logs")
    logs = value if isinstance(value, (list, tuple)) else ()
    normalized = []
    for entry in logs[:500]:
        if not hasattr(entry, "get"):
            continue
        normalized.append(
            {
                "timestamp": _cloud_display_text(entry.get("timestamp")),
                "unit_number": _cloud_display_text(entry.get("unit_number")),
                "text": str(entry.get("text") or "")[:2000],
                "status": _cloud_display_text(entry.get("status")),
                "creator": _cloud_display_text(entry.get("creator")),
            }
        )
    return normalized


def build_cloud_operations_snapshot(state) -> dict:
    """Project minimized in-memory CAD state into the existing display model."""
    calls = []
    for item in state.calls:
        assigned_value = item.get("assigned_units")
        assigned = assigned_value if isinstance(assigned_value, (list, tuple)) else ()
        assigned_units = []
        command_logs = _cloud_command_logs(item)
        for unit in assigned:
            if not hasattr(unit, "get"):
                continue
            unit_number = _cloud_display_text(unit.get("unit_number"))
            if not unit_number:
                continue
            assigned_units.append(
                {
                    "unit_number": unit_number,
                    "unit_type": _cloud_display_text(unit.get("unit_type")),
                    "agency": _cloud_display_text(unit.get("agency")),
                    "status": _cloud_display_text(unit.get("status")) or "Assigned",
                }
            )
        calls.append(
            {
                "cfs_number": _cloud_display_text(item.get("cfs_number")),
                "incident_code": _cloud_display_text(item.get("incident_code")),
                "incident_description": _cloud_display_text(item.get("incident_description")),
                "priority": _cloud_display_text(item.get("priority")),
                "agency": _cloud_display_text(item.get("agency")),
                "status": _cloud_display_text(item.get("status")),
                "call_datetime": _cloud_display_text(item.get("call_datetime")),
                "location": _cloud_display_text(item.get("location_label")),
                "city": _cloud_display_text(item.get("city")),
                "units": ", ".join(unit["unit_number"] for unit in assigned_units),
                "assigned_units": assigned_units,
                "command_log_count": len(command_logs),
                "latest_command_log_timestamp": (
                    command_logs[-1]["timestamp"] if command_logs else ""
                ),
            }
        )
    calls = sort_dashboard_calls(calls)
    unit_rows = build_unit_board(calls)
    return {
        "last_updated": (
            state.last_success_at.isoformat()
            if state.last_success_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        "calls": calls,
        "dashboard_stats": build_dashboard_stats(calls),
        "unit_rows": unit_rows,
        "unit_stats": build_unit_board_stats(unit_rows),
    }


def build_cloud_call_detail(state, cfs_number: str) -> dict | None:
    """Return one incident using only the normalized cloud display whitelist."""
    requested_number = _cloud_display_text(cfs_number)
    if not requested_number:
        return None

    snapshot = build_cloud_operations_snapshot(state)
    for call in snapshot["calls"]:
        if call["cfs_number"] != requested_number:
            continue
        source = next(
            (
                item
                for item in state.calls
                if _cloud_display_text(item.get("cfs_number")) == requested_number
            ),
            {},
        )
        return {
            "cfs_number": call["cfs_number"],
            "incident_code": call["incident_code"],
            "priority": call["priority"],
            "agency": call["agency"],
            "status": call["status"],
            "call_datetime": call["call_datetime"],
            "incident_description": call["incident_description"],
            "location": call["location"],
            "city": call["city"],
            "units": call["units"],
            "assigned_units": [dict(unit) for unit in call["assigned_units"]],
            "command_logs": _cloud_command_logs(source),
            "latitude": source.get("latitude"),
            "longitude": source.get("longitude"),
        }
    return None


def get_live_operations_snapshot() -> dict:
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_operations_snapshot()

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


def build_cloud_unit_snapshot(state) -> dict:
    """Project only reviewed normalized unit fields into the roster display."""
    roster_units = []
    active_rows = []
    for item in state.units:
        unit_number = _cloud_display_text(item.get("unit_number"))
        if not unit_number:
            continue
        unit = {
            "unit_number": unit_number,
            "agency": _cloud_display_text(item.get("agency")),
            "unit_type": _cloud_display_text(item.get("unit_type")),
            "status": _cloud_display_text(item.get("status")),
            "station": _cloud_display_text(item.get("station")),
        }
        roster_units.append(unit)
        assignment = _cloud_display_text(item.get("assignment_cfs_number"))
        if assignment:
            active_rows.append(
                {
                    **unit,
                    "cfs_number": assignment,
                    "location": "",
                    "incident_description": "",
                }
            )
    groups = build_full_unit_roster(roster_units, active_rows)
    return {
        "last_updated": (
            state.last_success_at.isoformat()
            if state.last_success_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        "calls": [],
        "roster_connected": state.last_success_at is not None,
        "roster_warning": "" if state.last_success_at is not None else "Cloud CAD snapshot awaiting first poll.",
        "active_stats": build_unit_board_stats(groups["active_units"]),
        **groups,
    }


def get_live_unit_snapshot(
    tenant_context: TenantContext | None = None,
) -> dict:
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_unit_snapshot()

    county_profile: CountyProfile | None = None
    if tenant_context is not None:
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.UNITS,
            "read",
        )

    client = CentralSquareClient()
    calls = sort_dashboard_calls(get_active_calls(client=client))
    active_unit_rows = build_unit_board(calls)

    try:
        if county_profile is None:
            roster_units = get_all_units(client=client)
        else:
            roster_units = get_all_units(
                client=client,
                county_profile=county_profile,
            )
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
