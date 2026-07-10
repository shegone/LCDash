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
        calls = get_active_calls()

        return {
            "connected": True,
            "active_calls": len(calls),
            "sample": calls[:3],
        }

    except CentralSquareAPIError as exc:
        return {
            "connected": False,
            "error": str(exc),
        }


@app.get("/dashboard")
def dashboard(request: Request):
    try:
        calls = get_active_calls()
        cad_status = "Connected"
        system_status = "Connected"
    except CentralSquareAPIError:
        calls = []
        cad_status = "Disconnected"
        system_status = "Unknown"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "system_status": system_status,
            "cad_status": cad_status,
            "active_calls": len(calls),
            "units": 0,
            "version": "0.3.0",
            "calls": calls,
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