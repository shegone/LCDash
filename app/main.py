from fastapi import FastAPI
from app.config.settings import settings
from app.auth.oauth import get_access_token, CentralSquareAuthError
from app.services.centralsquare import CentralSquareClient, CentralSquareAPIError

app = FastAPI(
    title="LCDash",
    description="Logan County 911 Operations Dashboard",
    version="0.2.0"
)


@app.get("/")
def home():
    return {
        "application": "LCDash",
        "version": "0.2.0",
        "status": "Running"
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
        "debug": settings.debug
    }


@app.get("/auth-test")
def auth_test():
    try:
        token = get_access_token()
        return {
            "authenticated": True,
            "token_received": bool(token),
            "token_preview": token[:12] + "..."
        }
    except CentralSquareAuthError as exc:
        return {
            "authenticated": False,
            "error": str(exc)
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
            "sample": statuses[:3]
        }

    except CentralSquareAPIError as exc:
        return {
            "connected": False,
            "error": str(exc)
        }