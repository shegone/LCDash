from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
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
from app.services.analytics_reporting import (
    AnalyticsRangeError,
    PERIOD_OPTIONS,
    get_analytics_overview,
)
from app.services.mae_service import (
    MAEServiceError,
    ask_mae,
    get_mae_status,
)
from app.services.mae_audit_service import (
    record_mae_feedback,
    record_mae_interaction,
)
from app.services.mae_evaluation_service import (
    get_evaluation_summary,
    list_evaluation_cases,
    list_feedback_review,
    run_evaluation_case,
)
from app.services.mae_memory_service import (
    create_memory_candidate,
    list_memory_items,
    review_memory,
)
from app.services.mae_tool_registry import get_mae_tool_catalog
from app.services.knowledge_service import (
    get_knowledge_status,
    list_knowledge_documents,
)
from app.services.mindshare_service import (
    MindshareServiceError,
    ask_mindshare,
    get_mindshare_status,
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


class MAEHistoryMessage(BaseModel):
    role: str
    content: str = Field(max_length=4000)


class MAEConversationEntities(BaseModel):
    cfs_numbers: list[str] = Field(default_factory=list, max_length=10)
    unit_numbers: list[str] = Field(default_factory=list, max_length=10)
    stations: list[str] = Field(default_factory=list, max_length=10)
    addresses: list[str] = Field(default_factory=list, max_length=5)
    incidents: list[str] = Field(default_factory=list, max_length=5)


class MAEChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[MAEHistoryMessage] = Field(default_factory=list, max_length=8)
    entities: MAEConversationEntities = Field(
        default_factory=MAEConversationEntities
    )


class MindshareChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[MAEHistoryMessage] = Field(default_factory=list, max_length=6)


class MAEFeedbackRequest(BaseModel):
    interaction_id: str = Field(min_length=36, max_length=36)
    rating: str = Field(min_length=3, max_length=30)
    comment: str = Field(default="", max_length=1000)


class MAEEvaluationRunRequest(BaseModel):
    case_id: str = Field(min_length=4, max_length=50)


class MAEMemoryCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    trigger_text: str = Field(min_length=3, max_length=1000)
    guidance: str = Field(min_length=3, max_length=4000)
    source_interaction_id: str = Field(default="", max_length=36)


class MAEMemoryReviewRequest(BaseModel):
    memory_id: int = Field(gt=0)
    decision: str = Field(min_length=7, max_length=20)


def _authenticated_user_email(request: Request) -> str:
    for header_name in (
        "cf-access-authenticated-user-email",
        "x-auth-request-email",
        "x-forwarded-user",
    ):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            return value
    return "local-session"


@app.middleware("http")
async def prevent_stale_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    if request.url.path == "/static/service-worker.js":
        response.headers["Service-Worker-Allowed"] = "/"
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


@app.get("/api/analytics/overview")
def analytics_overview_api(
    response: Response,
    period: str = "30d",
    start: str = "",
    end: str = "",
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_analytics_overview(period=period, start=start, end=end)
    except AnalyticsRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/analytics")
def analytics_page(
    request: Request,
    period: str = "30d",
    start: str = "",
    end: str = "",
):
    database_status = get_analytics_database_status()
    range_error = ""
    try:
        analytics_snapshot = get_analytics_overview(
            period=period,
            start=start,
            end=end,
        )
    except AnalyticsRangeError as exc:
        range_error = str(exc)
        analytics_snapshot = get_analytics_overview(
            period="30d",
            start="",
            end="",
        )

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "database_status": database_status,
            "analytics_snapshot": analytics_snapshot,
            "period_options": [
                (key, value[0]) for key, value in PERIOD_OPTIONS.items()
            ],
            "range_error": range_error,
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mae")
def mae_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mae.html",
        context={
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mae/reliability")
def mae_reliability_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mae_reliability.html",
        context={
            "evaluation_cases": list_evaluation_cases(),
            "evaluation_summary": get_evaluation_summary(),
            "feedback_items": list_feedback_review(),
            "memory_items": list_memory_items(),
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/knowledge")
def knowledge_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="knowledge.html",
        context={
            "knowledge_status": get_knowledge_status(),
            "documents": list_knowledge_documents(),
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare")
def mindshare_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mindshare.html",
        context={"version": "0.4.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/technical")
def mindshare_technical_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mindshare_technical.html",
        context={"version": "0.4.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/jack-hines")
def mindshare_jack_hines_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mindshare_jack_hines.html",
        context={"version": "0.4.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/library")
def mindshare_library_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mindshare_library.html",
        context={
            "knowledge_status": get_knowledge_status(
                library_key="mindshare",
                source_dir=settings.mindshare_knowledge_source_dir,
            ),
            "documents": list_knowledge_documents(
                library_key="mindshare",
            ),
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/radio")
def mindshare_radio_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mindshare_radio.html",
        context={"version": "0.4.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/knowledge/status")
def knowledge_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    status = get_knowledge_status()
    status["document_list"] = list_knowledge_documents()
    return status


@app.get("/api/mindshare/status")
def mindshare_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_mindshare_status()


@app.get("/api/mindshare/knowledge/status")
def mindshare_knowledge_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    status = get_knowledge_status(
        library_key="mindshare",
        source_dir=settings.mindshare_knowledge_source_dir,
    )
    status["document_list"] = list_knowledge_documents(
        library_key="mindshare",
    )
    return status


@app.post("/api/mindshare/chat")
def mindshare_chat_api(
    chat_request: MindshareChatRequest,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return ask_mindshare(
            chat_request.question,
            [message.model_dump() for message in chat_request.history],
        )
    except MindshareServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/mae/status")
def mae_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_mae_status()


@app.get("/api/mae/tools")
def mae_tools_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_mae_tool_catalog()


@app.post("/api/mae/chat")
def mae_chat_api(
    chat_request: MAEChatRequest,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = ask_mae(
            chat_request.question,
            [message.model_dump() for message in chat_request.history],
            chat_request.entities.model_dump(),
        )
        audit = record_mae_interaction(
            user_email=_authenticated_user_email(request),
            question=chat_request.question,
            result=result,
        )
        result["interaction_id"] = audit.get("interaction_id") or ""
        result["audit_saved"] = bool(audit.get("saved"))
        return result
    except MAEServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/mae/feedback")
def mae_feedback_api(
    feedback_request: MAEFeedbackRequest,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = record_mae_feedback(
            interaction_id=feedback_request.interaction_id,
            user_email=_authenticated_user_email(request),
            rating=feedback_request.rating,
            comment=feedback_request.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(
            status_code=503,
            detail=result.get("message") or "MAE feedback could not be saved.",
        )
    return result


@app.get("/api/mae/evaluations")
def mae_evaluations_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "cases": list_evaluation_cases(),
        "summary": get_evaluation_summary(),
    }


@app.post("/api/mae/evaluations/run")
def mae_evaluation_run_api(
    evaluation_request: MAEEvaluationRunRequest,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return run_evaluation_case(
            evaluation_request.case_id,
            requested_by=_authenticated_user_email(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/mae/feedback/review")
def mae_feedback_review_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"feedback": list_feedback_review()}


@app.get("/api/mae/memory")
def mae_memory_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"items": list_memory_items()}


@app.post("/api/mae/memory")
def mae_memory_create_api(
    memory_request: MAEMemoryCreateRequest,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = create_memory_candidate(
            title=memory_request.title,
            trigger_text=memory_request.trigger_text,
            guidance=memory_request.guidance,
            created_by=_authenticated_user_email(request),
            source_interaction_id=memory_request.source_interaction_id or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(
            status_code=503,
            detail=result.get("message") or "Memory candidate could not be saved.",
        )
    return result


@app.post("/api/mae/memory/review")
def mae_memory_review_api(
    memory_request: MAEMemoryReviewRequest,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = review_memory(
            memory_id=memory_request.memory_id,
            decision=memory_request.decision,
            reviewed_by=_authenticated_user_email(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(
            status_code=404,
            detail=result.get("message") or "Memory candidate not found.",
        )
    return result
