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

        return datetime.fromisoformat(cleaned_value)

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