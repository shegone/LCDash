from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.auth.oauth import get_access_token, CentralSquareAuthError
from app.services.cad_service import get_call_detail
from app.services.operations_service import (
    build_empty_unit_snapshot,
    build_empty_operations_snapshot,
    get_live_unit_snapshot,
    get_live_operations_snapshot,
)
from app.services.map_service import (
    build_empty_map_snapshot,
    get_live_map_snapshot,
)
from app.services.heatmap_service import (
    ALLOWED_HEATMAP_HOURS,
    build_empty_heatmap_snapshot,
    get_live_heatmap_snapshot,
    validate_heatmap_hours,
)
from app.services.station_alert_service import (
    build_empty_station_alert_snapshot,
    get_live_station_alert_snapshot,
)
from app.services.analytics_database import get_analytics_database_status
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


@app.middleware("http")
async def prevent_stale_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


def _units_without_positions(units: list) -> list:
    sanitized_units = []

    for unit in units:
        sanitized_unit = dict(unit)
        sanitized_unit.pop("position", None)
        sanitized_units.append(sanitized_unit)

    return sanitized_units


@app.get("/")
def home():
    return RedirectResponse(url="/dashboard", status_code=307)


@app.get("/health")
def health():
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
def units_api(response: Response):
    response.headers["Cache-Control"] = "no-store"

    try:
        snapshot = get_live_unit_snapshot()

        return {
            "connected": True,
            "roster_connected": snapshot["roster_connected"],
            "roster_warning": snapshot["roster_warning"],
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["active_stats"],
            "units": _units_without_positions(snapshot["active_units"]),
            "roster_stats": snapshot["roster_stats"],
            "all_units": _units_without_positions(snapshot["all_units"]),
            "active_units": _units_without_positions(snapshot["active_units"]),
            "operational_units": _units_without_positions(snapshot["operational_units"]),
            "available_units": _units_without_positions(snapshot["available_units"]),
            "unavailable_units": _units_without_positions(snapshot["unavailable_units"]),
            "unknown_units": _units_without_positions(snapshot["unknown_units"]),
        }
    except CentralSquareAPIError as exc:
        snapshot = build_empty_unit_snapshot()

        return {
            "connected": False,
            "roster_connected": False,
            "roster_warning": snapshot["roster_warning"],
            "error": str(exc),
            "last_updated": snapshot["last_updated"],
            "stats": snapshot["active_stats"],
            "units": _units_without_positions(snapshot["active_units"]),
            "roster_stats": snapshot["roster_stats"],
            "all_units": _units_without_positions(snapshot["all_units"]),
            "active_units": _units_without_positions(snapshot["active_units"]),
            "operational_units": _units_without_positions(snapshot["operational_units"]),
            "available_units": _units_without_positions(snapshot["available_units"]),
            "unavailable_units": _units_without_positions(snapshot["unavailable_units"]),
            "unknown_units": _units_without_positions(snapshot["unknown_units"]),
        }


@app.get("/api/operations/map")
def map_api(response: Response):
    response.headers["Cache-Control"] = "no-store"

    try:
        return get_live_map_snapshot()
    except CentralSquareAPIError as exc:
        return build_empty_map_snapshot(str(exc))


def _validated_heatmap_hours(hours: int) -> int:
    try:
        return validate_heatmap_hours(hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/operations/map/heatmap")
def heatmap_api(response: Response, hours: int = 8):
    selected_hours = _validated_heatmap_hours(hours)
    response.headers["Cache-Control"] = "no-store"

    try:
        return get_live_heatmap_snapshot(selected_hours)
    except CentralSquareAPIError:
        return build_empty_heatmap_snapshot(selected_hours)


@app.get("/api/operations/station-alerts")
def station_alerts_api(response: Response, station: str = ""):
    response.headers["Cache-Control"] = "no-store"

    try:
        return get_live_station_alert_snapshot(station)
    except CentralSquareAPIError as exc:
        return build_empty_station_alert_snapshot(station, str(exc))


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


@app.get("/active-calls")
def active_calls_page(request: Request):
    try:
        snapshot = get_live_operations_snapshot()
        cad_status = "Connected"
        system_status = "Connected"
        error = None
    except CentralSquareAPIError as exc:
        snapshot = build_empty_operations_snapshot()
        cad_status = "Disconnected"
        system_status = "Unknown"
        error = str(exc)

    calls = snapshot["calls"]
    stats = snapshot["dashboard_stats"]

    agency_options = sorted(
        {
            call.get("agency")
            for call in calls
            if call.get("agency")
        }
    )
    status_options = sorted(
        {
            call.get("status")
            for call in calls
            if call.get("status")
        }
    )

    return templates.TemplateResponse(
        request=request,
        name="active_calls.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "error": error,
            "calls": calls,
            "active_calls": stats["active_calls"],
            "high_priority_calls": stats["high_priority_calls"],
            "agency_options": agency_options,
            "status_options": status_options,
            "last_updated": snapshot["last_updated"],
            "version": "0.3.0",
        },
    )


@app.get("/units")
def units_board(request: Request):
    try:
        snapshot = get_live_unit_snapshot()
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        snapshot = build_empty_unit_snapshot()
        cad_status = "Disconnected"
        system_status = "Unknown"

    return templates.TemplateResponse(
        request=request,
        name="units.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "calls": snapshot["calls"],
            "roster_connected": snapshot["roster_connected"],
            "roster_warning": snapshot["roster_warning"],
            "unit_rows": snapshot["active_units"],
            "operational_units": snapshot["operational_units"],
            "available_units": snapshot["available_units"],
            "unavailable_units": snapshot["unavailable_units"],
            "unknown_units": snapshot["unknown_units"],
            "stats": snapshot["roster_stats"],
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


@app.get("/map")
def gis_map(request: Request):
    try:
        map_data = get_live_map_snapshot()
    except CentralSquareAPIError as exc:
        map_data = build_empty_map_snapshot(str(exc))

    features = map_data["features"]
    call_features = [
        feature
        for feature in features
        if feature.get("properties", {}).get("kind") == "call"
    ]
    unit_features = [
        feature
        for feature in features
        if feature.get("properties", {}).get("kind") == "unit"
    ]

    return templates.TemplateResponse(
        request=request,
        name="map.html",
        context={
            "map_data": map_data,
            "summary": map_data["summary"],
            "call_features": call_features,
            "unit_features": unit_features,
            "cad_status": "Connected" if map_data["cad_connected"] else "Disconnected",
            "system_status": "Connected" if map_data["cad_connected"] else "Unknown",
            "last_updated": map_data["generated_at"],
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/map/heatmap")
def heatmap_page(request: Request, hours: int = 8):
    selected_hours = _validated_heatmap_hours(hours)

    try:
        heatmap_data = get_live_heatmap_snapshot(selected_hours)
    except CentralSquareAPIError:
        heatmap_data = build_empty_heatmap_snapshot(selected_hours)

    return templates.TemplateResponse(
        request=request,
        name="heatmap.html",
        context={
            "heatmap_data": heatmap_data,
            "summary": heatmap_data["summary"],
            "selected_hours": selected_hours,
            "allowed_hours": ALLOWED_HEATMAP_HOURS,
            "cad_status": "Connected" if heatmap_data["cad_connected"] else "Disconnected",
            "system_status": "Connected" if heatmap_data["cad_connected"] else "Unknown",
            "last_updated": heatmap_data["generated_at"],
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/station-alerts")
def station_alerts_page(request: Request, station: str = ""):
    try:
        alert_data = get_live_station_alert_snapshot(station)
    except CentralSquareAPIError as exc:
        alert_data = build_empty_station_alert_snapshot(station, str(exc))

    return templates.TemplateResponse(
        request=request,
        name="station_alerts.html",
        context={
            "alert_data": alert_data,
            "selected_station": station,
            "cad_status": "Connected" if alert_data["connected"] else "Disconnected",
            "system_status": "Connected" if alert_data["connected"] else "Unknown",
            "last_updated": alert_data["generated_at"],
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/analytics/status")
def analytics_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_analytics_database_status()


@app.get("/analytics")
def analytics_page(request: Request):
    database_status = get_analytics_database_status()
    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "database_status": database_status,
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )
