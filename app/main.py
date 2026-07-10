from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.auth.oauth import get_access_token, CentralSquareAuthError
from app.services.cad_service import get_active_calls, get_call_detail
from app.services.centralsquare import (
    CentralSquareClient,
    CentralSquareAPIError,
)

app = FastAPI(
    title="LCDash",
    description="Logan County 911 Operations Dashboard",
    version="0.3.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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


def _sort_dashboard_calls(calls: list) -> list:
    return sorted(
        calls,
        key=lambda call: (
            _safe_priority_level(call),
            _parse_call_datetime(call.get("call_datetime")),
        ),
    )


def _build_dashboard_stats(calls: list) -> dict:
    unique_units = set()
    agency_counts = {}
    high_priority_calls = 0

    for call in calls:
        priority = _safe_priority_level(call)

        if priority <= 15:
            high_priority_calls += 1

        agency = call.get("agency") or "Unknown"
        agency_counts[agency] = agency_counts.get(agency, 0) + 1

        for unit in call.get("assigned_units") or []:
            unit_number = unit.get("unit_number")
            if unit_number:
                unique_units.add(unit_number)

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
        "high_priority_calls": high_priority_calls,
        "agency_summary": agency_summary,
    }


def _normalize_unit_status(status: str) -> str:
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


def _unit_status_rank(status: str) -> int:
    normalized_status = _normalize_unit_status(status)

    ranks = {
        "Transporting": 1,
        "Enroute": 2,
        "On Scene": 3,
        "Assigned": 4,
        "Cleared": 8,
        "Unknown": 9,
    }

    return ranks.get(normalized_status, 9)


def _build_unit_board(calls: list) -> list:
    unit_rows = []

    for call in calls:
        assigned_units = call.get("assigned_units") or []

        for unit in assigned_units:
            unit_number = unit.get("unit_number") or ""
            status = unit.get("status") or "Unknown"
            status_group = _normalize_unit_status(status)

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
            _unit_status_rank(unit.get("status")),
            _safe_priority_level({"priority": unit.get("priority")}),
            unit.get("unit_number") or "",
        ),
    )


def _build_unit_board_stats(unit_rows: list) -> dict:
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


@app.get("/")
def home():
    return {
        "application": "LCDash",
        "version": "0.3.0",
        "status": "Running",
    }


@app.get("/config-test")
def config_test():
    return {
        "token_url_loaded": bool(settings.token_url),
        "cad_base_url_loaded": bool(settings.cad_base_url),
        "system_base_url_loaded": bool(settings.system_base_url),
        "username_loaded": bool(settings.username),
        "password_loaded": bool(settings.password),
        "from_header": settings.from_header,
        "debug": settings.debug,
    }


@app.get("/auth-test")
def auth_test():
    try:
        token = get_access_token()
        return {
            "authenticated": True,
            "token_received": True,
            "token_preview": token[:12] + "...",
        }
    except CentralSquareAuthError as exc:
        return {
            "authenticated": False,
            "error": str(exc),
        }


@app.get("/system-test")
def system_test():
    try:
        client = CentralSquareClient()
        result = client.get_system_config("CADUnitStatus")
        statuses = result.get("CADUnitStatus", [])

        return {
            "connected": True,
            "configuration": "CADUnitStatus",
            "records_returned": len(statuses),
            "sample": statuses[:3],
        }

    except CentralSquareAPIError as exc:
        return {
            "connected": False,
            "error": str(exc),
        }


@app.get("/active-calls-test")
def active_calls_test():
    try:
        calls = _sort_dashboard_calls(get_active_calls())
        stats = _build_dashboard_stats(calls)

        return {
            "connected": True,
            "active_calls": len(calls),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "sample": calls[:3],
        }

    except CentralSquareAPIError as exc:
        return {
            "connected": False,
            "error": str(exc),
        }


@app.get("/dashboard")
def dashboard(request: Request):
    last_updated = datetime.now(timezone.utc).isoformat()

    try:
        calls = _sort_dashboard_calls(get_active_calls())
        stats = _build_dashboard_stats(calls)
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        calls = []
        stats = _build_dashboard_stats(calls)
        cad_status = "Disconnected"
        system_status = "Unknown"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "active_calls": stats["active_calls"],
            "assigned_units": stats["assigned_units"],
            "high_priority_calls": stats["high_priority_calls"],
            "agency_summary": stats["agency_summary"],
            "version": "0.3.0",
            "calls": calls,
            "last_updated": last_updated,
        },
    )


@app.get("/units")
def units_board(request: Request):
    last_updated = datetime.now(timezone.utc).isoformat()

    try:
        calls = _sort_dashboard_calls(get_active_calls())
        unit_rows = _build_unit_board(calls)
        stats = _build_unit_board_stats(unit_rows)
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        calls = []
        unit_rows = []
        stats = _build_unit_board_stats(unit_rows)
        cad_status = "Disconnected"
        system_status = "Unknown"

    return templates.TemplateResponse(
        request=request,
        name="units.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "calls": calls,
            "unit_rows": unit_rows,
            "stats": stats,
            "last_updated": last_updated,
            "version": "0.3.0",
        },
    )


@app.get("/calls/{cfs_number}")
def call_detail(request: Request, cfs_number: str):
    try:
        call = get_call_detail(cfs_number)
        connected = True
        error = None
    except CentralSquareAPIError as exc:
        call = None
        connected = False
        error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="call_detail.html",
        context={
            "call": call,
            "connected": connected,
            "error": error,
            "version": "0.3.0",
        },
    )