from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.auth.oauth import get_access_token, CentralSquareAuthError
from app.services.cad_service import get_call_detail
from app.services.operations_service import (
    build_empty_operations_snapshot,
    get_live_operations_snapshot,
)
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
        snapshot = get_live_operations_snapshot()
        calls = snapshot["calls"]

        return {
            "connected": True,
            "active_calls": len(calls),
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["dashboard_stats"],
            "sample": calls[:3],
        }

    except CentralSquareAPIError as exc:
        return {
            "connected": False,
            "error": str(exc),
        }


@app.get("/api/operations/snapshot")
def operations_snapshot_api():
    try:
        snapshot = get_live_operations_snapshot()

        return {
            "connected": True,
            "system_status": "Connected",
            "cad_status": "Connected",
            **snapshot,
        }

    except CentralSquareAPIError as exc:
        snapshot = build_empty_operations_snapshot()

        return {
            "connected": False,
            "system_status": "Unknown",
            "cad_status": "Disconnected",
            "error": str(exc),
            **snapshot,
        }


@app.get("/api/operations/active-calls")
def active_calls_api():
    try:
        snapshot = get_live_operations_snapshot()

        return {
            "connected": True,
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["dashboard_stats"],
            "calls": snapshot["calls"],
        }

    except CentralSquareAPIError as exc:
        snapshot = build_empty_operations_snapshot()

        return {
            "connected": False,
            "error": str(exc),
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["dashboard_stats"],
            "calls": snapshot["calls"],
        }


@app.get("/api/operations/units")
def units_api():
    try:
        snapshot = get_live_operations_snapshot()

        return {
            "connected": True,
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["unit_stats"],
            "units": snapshot["unit_rows"],
        }

    except CentralSquareAPIError as exc:
        snapshot = build_empty_operations_snapshot()

        return {
            "connected": False,
            "error": str(exc),
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["unit_stats"],
            "units": snapshot["unit_rows"],
        }


@app.get("/dashboard")
def dashboard(request: Request):
    try:
        snapshot = get_live_operations_snapshot()
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        snapshot = build_empty_operations_snapshot()
        cad_status = "Disconnected"
        system_status = "Unknown"

    stats = snapshot["dashboard_stats"]

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
            "calls": snapshot["calls"],
            "last_updated": snapshot["last_updated"],
        },
    )


@app.get("/units")
def units_board(request: Request):
    try:
        snapshot = get_live_operations_snapshot()
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        snapshot = build_empty_operations_snapshot()
        cad_status = "Disconnected"
        system_status = "Unknown"

    return templates.TemplateResponse(
        request=request,
        name="units.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "calls": snapshot["calls"],
            "unit_rows": snapshot["unit_rows"],
            "stats": snapshot["unit_stats"],
            "last_updated": snapshot["last_updated"],
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