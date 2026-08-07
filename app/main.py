import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
import json
import re
from queue import Queue
from threading import Thread
import secrets
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.core.county_branding import branding_for_tenant_context
from app.core.tenancy import TenantContext
from app.auth.oauth import get_access_token, CentralSquareAuthError
from app.services.cad_service import get_call_detail
from app.services.operations_service import (
    build_cloud_call_detail,
    build_cloud_operations_snapshot,
    build_cloud_unit_snapshot,
    build_empty_unit_snapshot,
    build_empty_operations_snapshot,
    get_live_unit_snapshot,
    get_live_operations_snapshot,
)
from app.services.map_service import (
    build_empty_map_snapshot,
    build_map_snapshot,
    get_live_map_snapshot,
)
from app.services.gis_reference_service import (
    get_reference_catalog,
    get_reference_layer,
)
from app.services.aws_map_tiles import (
    ALLOWED_TILESETS,
    LazyGeoMapsClient,
    MapTileUnavailable,
    fetch_map_tile,
)
from app.services.heatmap_service import (
    ALLOWED_HEATMAP_HOURS,
    build_empty_heatmap_snapshot,
    get_live_heatmap_snapshot,
    validate_heatmap_hours,
)
from app.services.station_alert_service import (
    build_empty_station_alert_snapshot,
    build_station_alert_snapshot,
    get_live_station_alert_snapshot,
)
from app.services.analytics_database import get_analytics_database_status
from app.services.cloud_pilot_readiness_service import get_cloud_pilot_readiness
from app.services.cloud_presentation_status import build_cloud_presentation_status
from app.services.analytics_reporting import (
    AnalyticsRangeError,
    PERIOD_OPTIONS,
    get_analytics_overview,
)
from app.services.mae_analytics_report_service import build_analytics_report
from app.services.cloud_report_service import (
    PostgresReportTemplateStore,
    ReportIntent,
    create_report_template,
    safe_template_record,
)
from app.services.county_commission_report_service import (
    CountyCommissionReportBusyError,
    build_county_commission_pdf,
    get_county_commission_job,
    start_county_commission_job,
)
from app.services.mae_analytics_visualization_service import (
    TenantWidgetIsolationError,
    list_saved_widgets,
    retire_widget,
    save_widget,
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
    get_knowledge_document_file,
    get_knowledge_status,
    list_knowledge_documents,
)
from app.services.cloud_document_library import (
    CloudDocumentLibraryUnavailable,
    build_cloud_document_library,
    content_disposition_header,
)
from app.services.mindshare_service import (
    MindshareServiceError,
    ask_mindshare,
    get_mindshare_status,
)
from app.services.mindshare_audit_service import (
    list_jack_feedback,
    record_jack_feedback,
    record_jack_interaction,
)
from app.services.mindshare_coverage_service import build_mindshare_coverage
from app.services.mindshare_evaluation_service import (
    get_mindshare_evaluation_summary,
    list_mindshare_evaluation_cases,
    run_mindshare_evaluation_case,
)
from app.services.jack_memory_service import (
    create_jack_memory_candidate,
    list_jack_memory_items,
    review_jack_memory,
)
from app.services.voice_service import (
    VOICE_CHOICES,
    VoiceServiceError,
    get_voice_status,
    synthesize_speech,
    transcribe_audio,
)
from app.services.cloud_ai_service import (
    CLOUD_POLLY_VOICES,
    CLOUD_TRANSCRIBE_AUDIO_FORMATS,
    answer_cloud_advisory,
    answer_verified_live_or_none,
    build_activated_cloud_ai_runtime,
    build_cloud_ai_config,
    build_cloud_ai_runtime,
    build_verified_live_advisory,
    cloud_ai_status,
    cloud_mode_enabled,
    synthesize_cloud_speech,
    transcribe_cloud_speech,
)
from app.integrations.cloud_ai import CloudAiRuntimeUnavailable
from app.integrations.cloud_ai.bedrock_retrieval import DailyRequestBudget
from app.services.cloud_ai_streaming import (
    build_cloud_advisory_streamer,
    iter_advisory_ndjson,
    stream_cloud_advisory,
    synthesize_cloud_sentence,
)
from app.services.centralsquare import (
    CentralSquareAPIError,
)
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)
from app.integrations.cad.cloud_read_runtime import build_cloud_cad_runtime
from app.services.realtime_service import (
    browser_event,
    event_broker,
    get_realtime_health,
    process_webhook_event,
)
from app.services.nga911_intelligence_service import (
    NGA911ProviderError,
    get_nga911_counties,
    get_nga911_county_detail,
    get_nga911_intelligence_overview,
    get_nga911_logan_event,
    get_nga911_logan_operations,
)
from app.services.nga911_nova_service import (
    NOVAServiceError,
    ask_nova,
    get_nova_status,
)

cloud_cad_runtime = build_cloud_cad_runtime(settings)
cloud_ai_config = build_cloud_ai_config(settings)
# One shared budget so the whole-answer and streaming advisory paths draw
# from a single daily generation cap rather than two independent ones.
cloud_advisory_budget = DailyRequestBudget(200)
cloud_ai_runtime = build_activated_cloud_ai_runtime(
    settings, budget=cloud_advisory_budget
)
cloud_advisory_streamer = build_cloud_advisory_streamer(
    cloud_ai_config, budget=cloud_advisory_budget
)
# Phrases pre-verified live CAD/analytics facts computed in Python; shares
# the same daily budget so it cannot create an uncounted third generation path.
cloud_verified_live_advisory = build_verified_live_advisory(
    settings, budget=cloud_advisory_budget
)
# Constructing this opens no connection; the first tile request creates the
# client. Tiles are signed server-side so no AWS credential reaches a browser.
geo_map_tile_client = LazyGeoMapsClient()
# Same source-of-truth prefixes as the Bedrock Knowledge Base
# (cloud_ai_allowed_s3_prefixes); no provider call happens at construction.
cloud_document_library = build_cloud_document_library(settings)


def _cloud_presentation_status(knowledge_status: dict | None = None):
    return build_cloud_presentation_status(
        cad_status=cloud_cad_runtime.status(),
        ai_status=cloud_ai_status(cloud_ai_config, cloud_ai_runtime),
        knowledge_status=knowledge_status or {},
    )


_CLOUD_AI_MODEL_LABELS = {
    "us.amazon.nova-pro-v1:0": "Amazon Nova Pro",
    "amazon.nova-lite-v1:0": "Amazon Nova Lite",
    "amazon.nova-micro-v1:0": "Amazon Nova Micro",
    "us.anthropic.claude-sonnet-5": "Claude Sonnet 5",
}


def _cloud_ai_model_label() -> str:
    """Describe the configured generation model without hardcoding a vendor."""
    model_id = settings.cloud_ai_generation_model_id
    return f"Grounded {_CLOUD_AI_MODEL_LABELS.get(model_id, model_id)} advisory"


def _cloud_cad_bridge_enabled() -> bool:
    status = cloud_cad_runtime.status()
    return bool(status["enabled"]) and status["mode"] == "centralsquare-read-poll"


def _current_operations_snapshot() -> dict:
    if _cloud_cad_bridge_enabled():
        return build_cloud_operations_snapshot(cloud_cad_runtime.state)
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_operations_snapshot()
    return get_live_operations_snapshot()


def _current_unit_snapshot(tenant_context: TenantContext | None = None) -> dict:
    if _cloud_cad_bridge_enabled():
        return build_cloud_unit_snapshot(cloud_cad_runtime.state)
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_unit_snapshot()
    return get_live_unit_snapshot(tenant_context=tenant_context)


@asynccontextmanager
async def application_lifespan(application: FastAPI):
    application.state.cloud_cad_runtime = cloud_cad_runtime
    cloud_cad_runtime.start()
    try:
        yield
    finally:
        await cloud_cad_runtime.stop()


app = FastAPI(
    title="LCDash",
    description="Logan County 911 Operations Dashboard",
    version="0.3.0",
    lifespan=application_lifespan,
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


class MAEAnalyticsReportRequest(BaseModel):
    period: str = Field(default="30d", pattern="^(24h|7d|30d|90d|365d)$")
    view_key: str = Field(default="", max_length=40)


class CloudReportIntentRequest(BaseModel):
    metric: str = Field(max_length=40)
    dimensions: list[str] = Field(min_length=1, max_length=3)
    period: str = Field(max_length=10)
    current_cad_fallback: bool = False


class CloudReportTemplateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    intent: CloudReportIntentRequest
    visible_to_roles: list[str] = Field(min_length=1, max_length=4)


class CloudReportExportRequest(BaseModel):
    intent: CloudReportIntentRequest
    preview_confirmed: Literal[True]


class AnalyticsWidgetRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    view_key: str = Field(min_length=3, max_length=40)


class AnalyticsWidgetRetireRequest(BaseModel):
    widget_id: int = Field(gt=0)


class CountyCommissionReportRequest(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")


class MindshareChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[MAEHistoryMessage] = Field(default_factory=list, max_length=6)


class NOVAChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[MAEHistoryMessage] = Field(default_factory=list, max_length=6)


class VoiceSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)
    voice: str = Field(default="", max_length=40)
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
    response_format: str = Field(default="mp3", pattern="^(mp3|wav)$")


class CloudAdvisoryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    persona: str = Field(default="mae", pattern="^(mae|jack)$")


class MAEFeedbackRequest(BaseModel):
    interaction_id: str = Field(min_length=36, max_length=36)
    rating: str = Field(min_length=3, max_length=30)
    comment: str = Field(default="", max_length=1000)


class MAEEvaluationRunRequest(BaseModel):
    case_id: str = Field(min_length=4, max_length=50)


class MindshareEvaluationRunRequest(BaseModel):
    case_id: str = Field(min_length=4, max_length=80)


class MindshareFeedbackRequest(BaseModel):
    interaction_id: str = Field(min_length=36, max_length=36)
    rating: str = Field(min_length=3, max_length=30)
    comment: str = Field(default="", max_length=1000)


class MAEMemoryCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    trigger_text: str = Field(min_length=3, max_length=1000)
    guidance: str = Field(min_length=3, max_length=4000)
    source_interaction_id: str = Field(default="", max_length=36)


class MAEMemoryReviewRequest(BaseModel):
    memory_id: int = Field(gt=0)
    decision: str = Field(min_length=7, max_length=20)


class JackMemoryCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    trigger_text: str = Field(min_length=3, max_length=1000)
    guidance: str = Field(min_length=3, max_length=4000)
    source_interaction_id: str = Field(default="", max_length=36)


class JackMemoryReviewRequest(BaseModel):
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


def _authorize_centralsquare_webhook(request: Request) -> None:
    configured_secret = settings.centralsquare_webhook_secret
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="CentralSquare webhook receiver is not configured.",
        )

    supplied_secret = str(
        request.headers.get("x-lcdash-webhook-secret") or ""
    ).strip()
    authorization = str(request.headers.get("authorization") or "").strip()

    if not supplied_secret and authorization.lower().startswith("basic "):
        encoded_credentials = authorization[6:].strip()
        try:
            decoded_credentials = base64.b64decode(
                encoded_credentials,
                validate=True,
            ).decode("utf-8")
            username, supplied_secret = decoded_credentials.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            username = ""
            supplied_secret = ""

        if not secrets.compare_digest(username, "lcdash"):
            supplied_secret = ""

    if not supplied_secret or not secrets.compare_digest(
        supplied_secret,
        configured_secret,
    ):
        raise HTTPException(
            status_code=401,
            detail="Webhook authentication failed.",
            headers={
                "WWW-Authenticate": 'Basic realm="LCDash CentralSquare Webhook"'
            },
        )


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


@app.get("/api/pilot/readiness")
def cloud_pilot_readiness():
    """Return the static, presentation-safe readiness view for the cloud pilot."""

    return get_cloud_pilot_readiness().to_dict()


@app.get("/api/pilot/cad-read-status")
def cloud_cad_read_status(response: Response):
    """Return only presentation-safe polling health behind ALB authentication."""
    response.headers["Cache-Control"] = "no-store"
    return dict(cloud_cad_runtime.status())


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
        snapshot = _current_operations_snapshot()
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
def operations_snapshot_api(response: Response):
    response.headers["Cache-Control"] = "no-store"

    try:
        snapshot = _current_operations_snapshot()
        presentation = _cloud_presentation_status()
        source = presentation["source"]

        return {
            "connected": source["connected"],
            "system_status": source["label"],
            "cad_status": "Connected" if source["connected"] else "Disconnected",
            "cloud_presentation_status": presentation,
            "cloud_presentation": settings.deployment_mode == "synthetic-disconnected",
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


@app.post(
    "/api/integrations/centralsquare/webhooks/{source}",
    status_code=202,
)
async def receive_centralsquare_webhook(
    source: Literal["cfs", "units"],
    request: Request,
):
    if settings.deployment_mode == "synthetic-disconnected":
        raise HTTPException(
            status_code=403,
            detail="Webhook ingestion is disabled in the cloud read-only deployment.",
        )
    _authorize_centralsquare_webhook(request)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.webhook_max_body_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="Webhook payload is too large.",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid Content-Length header.",
            ) from exc

    body = await request.body()
    if len(body) > settings.webhook_max_body_bytes:
        raise HTTPException(
            status_code=413,
            detail="Webhook payload is too large.",
        )

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Webhook payload must be valid JSON.",
        ) from exc

    result = await asyncio.to_thread(
        process_webhook_event,
        source,
        payload,
        len(body),
    )

    if not result["duplicate"]:
        await event_broker.publish(browser_event(result))

    return Response(
        content=json.dumps(
            {
                "accepted": result["accepted"],
                "duplicate": result["duplicate"],
                "persisted": result["persisted"],
            }
        ),
        status_code=202,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/operations/events")
async def operations_event_stream(request: Request):
    async def stream():
        async with event_broker.subscribe() as queue:
            yield "retry: 3000\n"
            yield 'event: ready\ndata: {"status":"connected"}\n\n'

            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=max(settings.realtime_heartbeat_seconds, 5),
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                yield (
                    "event: operations_changed\n"
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/integrations/centralsquare/health")
def centralsquare_realtime_health_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_realtime_health()


@app.get("/api/operations/active-calls")
def active_calls_api():
    try:
        snapshot = _current_operations_snapshot()
        source = _cloud_presentation_status()["source"]

        return {
            "connected": source["connected"],
            "source_status": source["state"],
            "source_label": source["label"],
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


def get_trusted_tenant_context() -> TenantContext | None:
    """Deployment/identity composition seam; never derives tenant from a request."""
    if settings.deployment_mode != "synthetic-disconnected" or not settings.tenant_id:
        return None
    try:
        return TenantContext(
            tenant_id=settings.tenant_id,
            subject="deployment-cell",
            identity_source="deployment-configuration",
            roles=frozenset({"viewer"}),
            request_id="deployment-cell",
            authenticated_at=datetime.now(timezone.utc),
        )
    except ValueError:
        return None


def _deny_unscoped_cloud_advisory_state() -> None:
    if settings.deployment_mode == "synthetic-disconnected":
        raise HTTPException(
            status_code=403,
            detail="This legacy advisory state route is unavailable in the tenant-isolated cloud deployment.",
        )


def _cloud_advisory_roles(tenant_context: TenantContext | None) -> tuple[str, ...]:
    if tenant_context is None or tenant_context.tenant_id != settings.tenant_id:
        return ("viewer",)
    return tuple(sorted(tenant_context.roles))


@app.get("/api/operations/units")
def units_api(
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"

    try:
        snapshot = _current_unit_snapshot(tenant_context=tenant_context)
        source = _cloud_presentation_status()["source"]

        return {
            "connected": source["connected"],
            "source_status": source["state"],
            "source_label": source["label"],
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
def map_api(
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"

    try:
        return get_live_map_snapshot(tenant_context=tenant_context)
    except CentralSquareAPIError as exc:
        return build_empty_map_snapshot(str(exc))


@app.get("/api/operations/map/reference")
def map_reference_catalog_api(
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    """List only reviewed, locally mounted GIS reference layers."""
    response.headers["Cache-Control"] = "private, max-age=3600"
    return get_reference_catalog(tenant_context)


@app.get("/api/operations/map/reference/{layer}")
def map_reference_layer_api(
    layer: str,
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    """Serve one minimized static GIS layer; source archives remain private."""
    reference_layer = get_reference_layer(
        layer,
        tenant_context=tenant_context,
    )
    if reference_layer is None:
        raise HTTPException(status_code=404, detail="GIS reference layer not available")

    response.headers["Cache-Control"] = "private, max-age=3600"
    return reference_layer


@app.get("/api/operations/map/tiles/{style}/{z}/{x}/{y}")
def map_tile_api(style: str, z: int, x: int, y: int):
    """Proxy one SigV4-signed Amazon Location raster tile.

    The browser cannot sign these requests without holding credentials, so
    the application signs them with the task role instead. Style names are
    resolved against a fixed allowlist and coordinates are range-checked
    before any upstream call is made.
    """
    if not cloud_mode_enabled(settings):
        raise HTTPException(
            status_code=404, detail="AWS map tiles are available in cloud mode only."
        )
    try:
        payload, content_type = fetch_map_tile(
            geo_map_tile_client, style=style, z=z, x=x, y=y
        )
    except MapTileUnavailable as exc:
        reason = str(exc)
        status = 404 if reason == "map_tile_style_not_allowed" else 502
        if reason.endswith("out_of_range"):
            status = 400
        raise HTTPException(status_code=status, detail="Map tile unavailable.") from exc
    return Response(
        content=payload,
        media_type=content_type,
        # Tiles are immutable for a given z/x/y, so they cache well. Kept
        # private because the response is served behind authentication.
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/operations/map/tile-styles")
def map_tile_styles_api(response: Response):
    """Report which AWS tile styles this deployment can serve."""
    response.headers["Cache-Control"] = "private, max-age=3600"
    available = cloud_mode_enabled(settings)
    return {
        "available": available,
        "styles": sorted(ALLOWED_TILESETS) if available else [],
    }


def _validated_heatmap_hours(hours: int) -> int:
    try:
        return validate_heatmap_hours(hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/operations/map/heatmap")
def heatmap_api(
    response: Response,
    hours: int = 8,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    selected_hours = _validated_heatmap_hours(hours)
    response.headers["Cache-Control"] = "no-store"

    try:
        return get_live_heatmap_snapshot(
            selected_hours,
            tenant_context=tenant_context,
        )
    except CentralSquareAPIError:
        return build_empty_heatmap_snapshot(selected_hours)


@app.get("/api/operations/station-alerts")
def station_alerts_api(
    response: Response,
    station: list[str] = Query(default=[]),
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"

    if settings.deployment_mode == "synthetic-disconnected":
        if not _cloud_cad_bridge_enabled():
            return build_empty_station_alert_snapshot(
                station,
                "Approved cloud assignment source unavailable.",
            )
        snapshot = build_cloud_unit_snapshot(cloud_cad_runtime.state)
        alert_data = build_station_alert_snapshot(snapshot, station)
        for alert in alert_data["alerts"]:
            alert.pop("announcement", None)
        return alert_data

    try:
        return get_live_station_alert_snapshot(station)
    except CentralSquareAPIError as exc:
        return build_empty_station_alert_snapshot(station, str(exc))


@app.get("/dashboard")
def dashboard(request: Request):
    presentation = _cloud_presentation_status()
    source = presentation["source"]
    try:
        snapshot = _current_operations_snapshot()
    except CentralSquareAPIError:
        snapshot = build_empty_operations_snapshot()

    stats = snapshot["dashboard_stats"]

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "system_status": source["label"],
            "cad_status": "Connected" if source["connected"] else "Disconnected",
            "cloud_presentation_status": presentation,
            "cloud_presentation": settings.deployment_mode == "synthetic-disconnected",
            "active_calls": stats["active_calls"],
            "assigned_units": stats["assigned_units"],
            "on_scene_calls": stats.get("on_scene_calls", 0),
            "high_priority_calls": stats["high_priority_calls"],
            "oldest_call_datetime": stats.get("oldest_call_datetime", ""),
            "agency_summary": stats["agency_summary"],
            "version": "0.3.0",
            "calls": snapshot["calls"],
            "last_updated": snapshot["last_updated"],
        },
    )


@app.get("/active-calls")
def active_calls_page(request: Request):
    presentation = _cloud_presentation_status()
    source = presentation["source"]
    try:
        snapshot = _current_operations_snapshot()
        error = None
    except CentralSquareAPIError as exc:
        snapshot = build_empty_operations_snapshot()
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
            "system_status": source["label"],
            "cad_status": "Connected" if source["connected"] else "Disconnected",
            "cloud_presentation_status": presentation,
            "error": error,
            "calls": calls,
            "active_calls": stats["active_calls"],
            "high_priority_calls": stats["high_priority_calls"],
            "agency_options": agency_options,
            "status_options": status_options,
            "cloud_presentation": settings.deployment_mode == "synthetic-disconnected",
            "last_updated": snapshot["last_updated"],
            "version": "0.3.0",
        },
    )


@app.get("/units")
def units_board(
    request: Request,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    presentation = _cloud_presentation_status()
    source = presentation["source"]
    try:
        snapshot = _current_unit_snapshot(tenant_context=tenant_context)
    except CentralSquareAPIError:
        snapshot = build_empty_unit_snapshot()

    return templates.TemplateResponse(
        request=request,
        name="units.html",
        context={
            "system_status": source["label"],
            "cad_status": source["label"],
            "cloud_presentation_status": presentation,
            "cloud_presentation": settings.deployment_mode == "synthetic-disconnected",
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
    cloud_normalized_detail = _cloud_cad_bridge_enabled()
    if cloud_normalized_detail:
        call = build_cloud_call_detail(cloud_cad_runtime.state, cfs_number)
        connected = cloud_cad_runtime.state.last_success_at is not None
        error = None if call else "Incident is not available in the current read-only snapshot."
    else:
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
        name=("call_detail_cloud.html" if cloud_normalized_detail else "call_detail.html"),
        context={
            "call": call,
            "connected": connected,
            "error": error,
            "version": "0.3.0",
        },
    )


@app.get("/map")
def gis_map(
    request: Request,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    try:
        if _cloud_cad_bridge_enabled():
            # Plot the same allowlisted read-only snapshot the dashboard shows.
            # Unit positions are intentionally absent from UNIT_FIELDS, so the
            # cloud map plots incidents only.
            snapshot = _current_operations_snapshot()
            map_data = build_map_snapshot(
                {
                    "calls": snapshot["calls"],
                    "all_units": [],
                    "roster_connected": False,
                }
            )
        else:
            map_data = get_live_map_snapshot(tenant_context=tenant_context)
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
def heatmap_page(
    request: Request,
    hours: int = 8,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    selected_hours = _validated_heatmap_hours(hours)

    try:
        heatmap_data = get_live_heatmap_snapshot(
            selected_hours,
            tenant_context=tenant_context,
        )
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
def station_alerts_page(
    request: Request,
    station: list[str] = Query(default=[]),
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    cloud_station_alerts = settings.deployment_mode == "synthetic-disconnected"
    if cloud_station_alerts:
        if _cloud_cad_bridge_enabled():
            snapshot = build_cloud_unit_snapshot(cloud_cad_runtime.state)
            alert_data = build_station_alert_snapshot(snapshot, station)
            for alert in alert_data["alerts"]:
                alert.pop("announcement", None)
        else:
            alert_data = build_empty_station_alert_snapshot(
                station,
                "Approved cloud assignment source unavailable.",
            )
    else:
        try:
            alert_data = get_live_station_alert_snapshot(station)
        except CentralSquareAPIError as exc:
            alert_data = build_empty_station_alert_snapshot(station, str(exc))

    return templates.TemplateResponse(
        request=request,
        name=("station_alerts_cloud.html" if cloud_station_alerts else "station_alerts.html"),
        context={
            "alert_data": alert_data,
            "selected_stations": alert_data.get("selected_stations", station),
            "cloud_station_alerts": cloud_station_alerts,
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
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_analytics_overview(
            period=period,
            start=start,
            end=end,
            tenant_context=tenant_context,
        )
    except AnalyticsRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/mae/analytics-report")
def mae_analytics_report_api(
    report_request: MAEAnalyticsReportRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    """Create an aggregate-only supervisor download from verified analytics."""
    try:
        snapshot = get_analytics_overview(
            period=report_request.period,
            tenant_context=tenant_context,
        )
    except AnalyticsRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not snapshot.get("available"):
        raise HTTPException(
            status_code=503,
            detail="Historical analytics are not available for this report.",
        )

    try:
        report = build_analytics_report(snapshot, report_request.view_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=report,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename=mae-analytics-report.pdf",
        },
    )


@app.get("/api/analytics/widgets")
def analytics_widgets_api(
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"items": list_saved_widgets(tenant_context=tenant_context)}
    except TenantWidgetIsolationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/analytics/widgets")
def analytics_widget_save_api(
    widget: AnalyticsWidgetRequest,
    request: Request,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    try:
        result = save_widget(
            title=widget.title,
            view_key=widget.view_key,
            created_by=_authenticated_user_email(request),
            tenant_context=tenant_context,
        )
    except TenantWidgetIsolationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(status_code=503, detail=result.get("message") or "Widget could not be saved.")
    return result


@app.post("/api/analytics/widgets/retire")
def analytics_widget_retire_api(
    widget: AnalyticsWidgetRetireRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    try:
        result = retire_widget(widget_id=widget.widget_id, tenant_context=tenant_context)
    except TenantWidgetIsolationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(status_code=404, detail=result.get("message") or "Widget not found.")
    return result


@app.get("/analytics")
def analytics_page(
    request: Request,
    period: str = "30d",
    start: str = "",
    end: str = "",
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    database_status = get_analytics_database_status()
    range_error = ""
    try:
        analytics_snapshot = get_analytics_overview(
            period=period,
            start=start,
            end=end,
            tenant_context=tenant_context,
        )
    except AnalyticsRangeError as exc:
        range_error = str(exc)
        analytics_snapshot = get_analytics_overview(
            period="30d",
            start="",
            end="",
            tenant_context=tenant_context,
        )

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "database_status": database_status,
            "analytics_snapshot": analytics_snapshot,
            "county_branding": branding_for_tenant_context(tenant_context),
            "saved_widgets": (
                list_saved_widgets(tenant_context=tenant_context)
                if tenant_context is not None
                else []
            ),
            "saved_widgets_isolated": tenant_context is not None,
            "period_options": [
                (key, value[0]) for key, value in PERIOD_OPTIONS.items()
            ],
            "range_error": range_error,
            "cloud_analytics_unpopulated": (
                settings.deployment_mode == "synthetic-disconnected"
                and analytics_snapshot.get("metrics", {}).get("total_calls", 0) == 0
            ),
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/reports")
def reports_page(request: Request):
    cloud_reporting_available = settings.deployment_mode != "synthetic-disconnected"
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "cloud_reporting_available": cloud_reporting_available,
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/reports/county-commission/jobs")
def county_commission_job_start_api(
    report_request: CountyCommissionReportRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    if settings.deployment_mode == "synthetic-disconnected":
        raise HTTPException(
            status_code=409,
            detail=(
                "County Commission reporting is unavailable until an approved "
                "historical analytics import and cloud report source are configured."
            ),
        )
    try:
        return start_county_commission_job(
            report_request.month,
            tenant_context=tenant_context,
        )
    except CountyCommissionReportBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/reports/county-commission/jobs/{job_id}")
def county_commission_job_api(
    job_id: str,
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"
    job = get_county_commission_job(
        job_id,
        tenant_context=tenant_context,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Monthly report job not found.")
    return job


def _cloud_report_intent(payload: CloudReportIntentRequest) -> ReportIntent:
    try:
        return ReportIntent(
            metric=payload.metric,
            dimensions=tuple(payload.dimensions),
            period=payload.period,
            current_cad_fallback=payload.current_cad_fallback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_cloud_report_identity(
    tenant_context: TenantContext | None, *, write: bool = False,
) -> TenantContext:
    if tenant_context is None or tenant_context.tenant_id != settings.tenant_id:
        raise HTTPException(status_code=403, detail="Trusted tenant identity is required.")
    if write and not tenant_context.roles.intersection({"supervisor", "admin"}):
        raise HTTPException(status_code=403, detail="Supervisor report permission is required.")
    return tenant_context


def _analytics_report_rows(snapshot: dict, intent: ReportIntent) -> list[dict]:
    if not snapshot.get("available"):
        return []
    if intent.metric == "call_count":
        return [{"call_count": int((snapshot.get("metrics") or {}).get("total_calls") or 0)}]
    mapping = {
        "calls_by_nature": "incident_types",
        "calls_by_hour": "hourly_volume",
        "calls_by_agency": "agency_mix",
    }
    if intent.metric in mapping:
        return [dict(row) for row in (snapshot.get(mapping[intent.metric]) or [])[:100]]
    if intent.metric == "average_response_seconds":
        return [{"average_response": (snapshot.get("metrics") or {}).get("average_response", "")}]
    if intent.metric == "unit_commitment_minutes":
        return [dict(row) for row in (snapshot.get("busiest_units") or [])[:100]]
    return []


def _current_cad_report_rows(intent: ReportIntent) -> list[dict]:
    calls = tuple(cloud_cad_runtime.state.calls)
    if intent.metric == "call_count":
        return [{"call_count": len(calls)}]
    field = {"calls_by_nature": "incident_description", "calls_by_agency": "agency"}.get(intent.metric)
    if not field:
        return []
    counts: dict[str, int] = {}
    for call in calls:
        label = str(call.get(field) or "Unspecified")[:100]
        counts[label] = counts.get(label, 0) + 1
    return [{"label": label, "count": count} for label, count in sorted(counts.items())]


def _report_preview_payload(intent: ReportIntent, context: TenantContext) -> dict:
    snapshot = get_analytics_overview(period=intent.period, tenant_context=context)
    rows = _analytics_report_rows(snapshot, intent)
    source = "analytics-database"
    freshness = str(snapshot.get("latest_data_at") or snapshot.get("generated_at") or "unavailable")
    disclaimer = "Historical analytics database; verify the displayed refresh time."
    if not rows and intent.current_cad_fallback:
        rows = _current_cad_report_rows(intent)
        source = "current-cad-read-only"
        status = cloud_cad_runtime.status()
        freshness = str(status.get("age_seconds") or "current poll")
        disclaimer = "Current read-only CAD aggregate; not a historical or authoritative report."
    return {
        "intent": {
            "metric": intent.metric, "dimensions": list(intent.dimensions),
            "period": intent.period, "current_cad_fallback": intent.current_cad_fallback,
        },
        "rows": rows[:500], "source": source, "freshness": freshness,
        "disclaimer": disclaimer, "save_requires_user_action": True,
        "export_requires_user_action": True,
    }


def _suggest_report_preview(question: str, context: TenantContext | None) -> dict | None:
    if context is None or not re.search(r"\b(report|trend|breakdown|how many)\b", question, re.I):
        return None
    lowered = question.lower()
    metric, dimensions = "call_count", ("day",)
    if "nature" in lowered or "incident type" in lowered:
        metric, dimensions = "calls_by_nature", ("nature",)
    elif "hour" in lowered or "time of day" in lowered:
        metric, dimensions = "calls_by_hour", ("hour",)
    elif "agency" in lowered:
        metric, dimensions = "calls_by_agency", ("agency",)
    elif "response" in lowered:
        metric, dimensions = "average_response_seconds", ("day",)
    period = next((value for value in ("365d", "90d", "30d", "7d", "24h") if value in lowered), "30d")
    return _report_preview_payload(
        ReportIntent(metric, dimensions, period, current_cad_fallback="current" in lowered),
        context,
    )


@app.post("/api/cloud-ai/reports/preview")
def cloud_report_preview_api(
    payload: CloudReportIntentRequest,
    tenant_context: Annotated[TenantContext | None, Depends(get_trusted_tenant_context)] = None,
):
    context = _require_cloud_report_identity(tenant_context)
    intent = _cloud_report_intent(payload)
    return _report_preview_payload(intent, context)


@app.post("/api/cloud-ai/reports/templates")
def cloud_report_template_save_api(
    payload: CloudReportTemplateRequest,
    request: Request,
    tenant_context: Annotated[TenantContext | None, Depends(get_trusted_tenant_context)] = None,
):
    context = _require_cloud_report_identity(tenant_context, write=True)
    template = create_report_template(
        tenant_id=context.tenant_id, title=payload.title,
        intent=_cloud_report_intent(payload.intent),
        author_subject=_authenticated_user_email(request) or context.subject,
        visible_to_roles=tuple(payload.visible_to_roles),
    )
    PostgresReportTemplateStore(settings.database_url).save(template)
    return safe_template_record(template)


@app.get("/api/cloud-ai/reports/templates")
def cloud_report_template_list_api(
    tenant_context: Annotated[TenantContext | None, Depends(get_trusted_tenant_context)] = None,
):
    context = _require_cloud_report_identity(tenant_context)
    templates = PostgresReportTemplateStore(settings.database_url).list_visible(
        tenant_id=context.tenant_id, roles=context.roles,
    )
    return {"templates": [safe_template_record(item) for item in templates]}


@app.post("/api/cloud-ai/reports/export")
def cloud_report_export_api(
    payload: CloudReportExportRequest,
    tenant_context: Annotated[TenantContext | None, Depends(get_trusted_tenant_context)] = None,
):
    context = _require_cloud_report_identity(tenant_context, write=True)
    intent = _cloud_report_intent(payload.intent)
    snapshot = get_analytics_overview(period=intent.period, tenant_context=context)
    if not snapshot.get("available"):
        raise HTTPException(status_code=503, detail="Historical analytics are unavailable.")
    report = build_analytics_report(snapshot, "")
    return Response(
        content=report, media_type="application/pdf",
        headers={"Cache-Control": "no-store", "Content-Disposition": "attachment; filename=mae-report.pdf"},
    )
    if not job:
        raise HTTPException(status_code=404, detail="Monthly report job not found.")
    return job


@app.get("/api/reports/county-commission/jobs/{job_id}/pdf")
def county_commission_job_pdf_api(
    job_id: str,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    job = get_county_commission_job(
        job_id,
        tenant_context=tenant_context,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Monthly report job not found.")
    if job.get("status") != "complete" or not job.get("result"):
        raise HTTPException(status_code=409, detail="Monthly report is not complete.")
    report = job["result"]
    month = report.get("month") or "monthly"
    return Response(
        content=build_county_commission_pdf(report),
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="logan-county-commission-{month}.pdf"'
            ),
        },
    )


@app.get("/mae")
def mae_page(request: Request):
    presentation = _cloud_presentation_status(get_knowledge_status())
    return templates.TemplateResponse(
        request=request,
        name="mae.html",
        context={
            "version": "0.3.0",
            "cloud_mode": cloud_mode_enabled(settings),
            "cloud_presentation_status": presentation,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/integrations/health")
def integrations_health_page(request: Request):
    presentation = _cloud_presentation_status()
    return templates.TemplateResponse(
        request=request,
        name="integrations_health.html",
        context={
            "health": get_realtime_health(),
            "cloud_presentation_status": presentation,
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mae/reliability")
def mae_reliability_page(request: Request):
    cloud_isolated = settings.deployment_mode == "synthetic-disconnected"
    return templates.TemplateResponse(
        request=request,
        name="mae_reliability.html",
        context={
            "evaluation_cases": [] if cloud_isolated else list_evaluation_cases(),
            "evaluation_summary": ({"total_runs": 0, "pass_rate": 0, "average_duration_ms": 0} if cloud_isolated else get_evaluation_summary()),
            "feedback_items": [] if cloud_isolated else list_feedback_review(),
            "memory_items": [] if cloud_isolated else list_memory_items(),
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


def _cloud_library_documents(library_key: str) -> list[dict]:
    """Adapt live S3 listings into the shape knowledge.html/mindshare_library.html
    already render. page_count/chunk_count/indexed_at have no live-listing
    equivalent (that metadata lives only in the on-prem Postgres index, which
    is never populated in cloud) and are left blank rather than fabricated.

    Fails closed to an empty list on any provider error, matching
    list_knowledge_documents()'s existing behavior -- the page falls back to
    its "waiting for sync" empty state rather than a 500.
    """
    try:
        documents = cloud_document_library.list_documents(library_key)
    except CloudDocumentLibraryUnavailable:
        return []
    return [
        {
            "document_id": document.document_id,
            "title": document.title,
            "file_name": document.relative_path,
            "is_pdf": True,
            "page_count": "",
            "chunk_count": "",
            "indexed_at": "",
        }
        for document in documents
    ]


@app.get("/knowledge")
def knowledge_page(request: Request):
    knowledge_status = get_knowledge_status()
    presentation = _cloud_presentation_status(knowledge_status)
    documents = (
        _cloud_library_documents("centralsquare")
        if cloud_mode_enabled(settings)
        else list_knowledge_documents()
    )
    return templates.TemplateResponse(
        request=request,
        name="knowledge.html",
        context={
            "knowledge_status": knowledge_status,
            "documents": documents,
            "cloud_presentation_status": presentation,
            "version": "0.3.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/nga911/v1/intelligence/overview")
def nga911_intelligence_overview_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_nga911_intelligence_overview()
    except NGA911ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/nga911/v1/counties")
def nga911_counties_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"schema_version": "nga911-counties.v1", "synthetic_data": True, "counties": get_nga911_counties()}
    except NGA911ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/nga911/v1/counties/{county_id}")
def nga911_county_api(county_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        detail = get_nga911_county_detail(county_id)
    except NGA911ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="NGA911 demonstration county not found")
    return detail


@app.get("/api/nga911/v1/director/operations")
def nga911_director_operations_api(response: Response, days: int = 14):
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_nga911_logan_operations(days)
    except NGA911ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/nga911/v1/director/events/{event_id}")
def nga911_director_event_api(event_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    event = get_nga911_logan_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="NGA911 demonstration event not found")
    return {"schema_version": "nga911-director-event.v1", "synthetic_data": True, "event": event}


@app.get("/nga911")
@app.get("/nga911-intelligence")
def nga911_intelligence_page(request: Request):
    try:
        overview = get_nga911_intelligence_overview()
    except NGA911ProviderError as exc:
        overview = {
            "synthetic_data": False,
            "environment_label": "PROVIDER NOT CONFIGURED",
            "generated_at": "",
            "connection": {
                "status": "unavailable",
                "status_label": "GOVCLOUD CONNECTION UNAVAILABLE",
            },
            "summary": {},
            "counties": [],
            "intelligence": [],
            "service_events": [],
            "capabilities": [],
            "error": str(exc),
        }

    return templates.TemplateResponse(
        request=request,
        name="nga911_intelligence.html",
        context={
            "overview": overview,
            "standalone": request.url.path == "/nga911",
            "version": "0.1.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/nga911/counties/{county_id}")
@app.get("/nga911-intelligence/counties/{county_id}")
def nga911_county_page(county_id: str, request: Request):
    try:
        detail = get_nga911_county_detail(county_id)
    except NGA911ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="NGA911 demonstration county not found")
    return templates.TemplateResponse(
        request=request,
        name="nga911_county.html",
        context={
            "detail": detail,
            "standalone": request.url.path.startswith("/nga911/counties/"),
            "version": "0.2.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/nga911/operations")
@app.get("/nga911-intelligence/operations")
def nga911_director_operations_page(request: Request, days: int = 14):
    operations = get_nga911_logan_operations(days)
    return templates.TemplateResponse(
        request=request,
        name="nga911_operations.html",
        context={"operations": operations, "standalone": request.url.path == "/nga911/operations", "version": "0.3.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/nga911/events/{event_id}")
@app.get("/nga911-intelligence/events/{event_id}")
def nga911_director_event_page(event_id: str, request: Request):
    event = get_nga911_logan_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="NGA911 demonstration event not found")
    return templates.TemplateResponse(
        request=request,
        name="nga911_event.html",
        context={"event": event, "standalone": request.url.path.startswith("/nga911/events/"), "version": "0.3.0"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/nga911/nova")
@app.get("/nga911-intelligence/nova")
def nga911_nova_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="nga911_nova.html",
        context={
            "standalone": request.url.path == "/nga911/nova",
            "version": "0.1.0",
            "cloud_mode": cloud_mode_enabled(settings),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/nga911/v1/nova/status")
def nga911_nova_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_nova_status()


@app.post("/api/nga911/v1/nova/chat")
def nga911_nova_chat_api(chat_request: NOVAChatRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return ask_nova(
            chat_request.question,
            [message.model_dump() for message in chat_request.history],
        )
    except NOVAServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/knowledge/documents/{library_key}/{document_id}")
def knowledge_document_pdf(
    library_key: Literal["centralsquare", "mindshare"],
    document_id: str,
    download: bool = False,
):
    if cloud_mode_enabled(settings):
        result = cloud_document_library.fetch_document(library_key, document_id)
        if result is None:
            raise HTTPException(status_code=404, detail="PDF document not found.")
        payload, filename = result
        return Response(
            content=payload,
            media_type="application/pdf",
            headers={
                "Content-Disposition": content_disposition_header(
                    filename, download=download
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    try:
        numeric_document_id = int(document_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="PDF document not found.")
    document = get_knowledge_document_file(numeric_document_id, library_key)
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found.")
    return FileResponse(
        path=document["path"],
        media_type="application/pdf",
        filename=document["file_name"],
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/mindshare")
def mindshare_page(request: Request):
    presentation = _cloud_presentation_status(get_knowledge_status())
    return templates.TemplateResponse(
        request=request,
        name="mindshare.html",
        context={
            "version": "0.4.0",
            "cloud_mode": cloud_mode_enabled(settings),
            "cloud_presentation_status": presentation,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/technical")
def mindshare_technical_page(request: Request):
    presentation = _cloud_presentation_status(get_knowledge_status())
    return templates.TemplateResponse(
        request=request,
        name="mindshare_technical.html",
        context={
            "version": "0.4.0",
            "cloud_mode": cloud_mode_enabled(settings),
            "cloud_presentation_status": presentation,
        },
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
    documents = (
        _cloud_library_documents("mindshare")
        if cloud_mode_enabled(settings)
        else list_knowledge_documents(library_key="mindshare")
    )
    return templates.TemplateResponse(
        request=request,
        name="mindshare_library.html",
        context={
            "knowledge_status": get_knowledge_status(
                library_key="mindshare",
                source_dir=settings.mindshare_knowledge_source_dir,
            ),
            "documents": documents,
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/reliability")
def mindshare_reliability_page(request: Request):
    cloud_isolated = settings.deployment_mode == "synthetic-disconnected"
    return templates.TemplateResponse(
        request=request,
        name="mindshare_reliability.html",
        context={
            "evaluation_cases": [] if cloud_isolated else list_mindshare_evaluation_cases(),
            "evaluation_summary": ({"total_runs": 0, "pass_rate": 0, "average_duration_ms": 0, "recent_runs": []} if cloud_isolated else get_mindshare_evaluation_summary()),
            "feedback_items": [] if cloud_isolated else list_jack_feedback(),
            "memory_items": [] if cloud_isolated else list_jack_memory_items(),
            "version": "0.4.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/mindshare/coverage")
def mindshare_coverage_page(request: Request):
    status = get_knowledge_status(
        library_key="mindshare",
        source_dir=settings.mindshare_knowledge_source_dir,
    )
    documents = list_knowledge_documents(library_key="mindshare")
    return templates.TemplateResponse(
        request=request,
        name="mindshare_coverage.html",
        context={
            "coverage": build_mindshare_coverage(documents, status),
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


@app.get("/voice")
def voice_lab_page(request: Request):
    cloud_voice = cloud_mode_enabled(settings)
    status = cloud_ai_status(cloud_ai_config, cloud_ai_runtime) if cloud_voice else None
    presentation = _cloud_presentation_status(get_knowledge_status()) if cloud_voice else None
    return templates.TemplateResponse(
        request=request,
        name="voice_lab.html",
        context={
            "voices": CLOUD_POLLY_VOICES if cloud_voice else VOICE_CHOICES,
            "default_voice": (
                cloud_ai_config.polly_voice.value
                if cloud_voice
                else settings.voice_tts_voice
            ),
            "cloud_voice": cloud_voice,
            "voice_enabled": bool(status and status["voice_enabled"]),
            "tts_enabled": bool(status and status["tts"]["ready"]),
            "stt_enabled": bool(status and status["stt"]["ready"]),
            "voice_disabled_reason": (
                status["tts"]["disabled_reason"] if status else ""
            ),
            "cloud_presentation_status": presentation,
            "version": "0.1.0",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/voice/status")
def voice_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if cloud_mode_enabled(settings):
        return cloud_ai_status(cloud_ai_config, cloud_ai_runtime)
    return get_voice_status()


@app.get("/api/cloud-ai/status")
def cloud_ai_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not cloud_mode_enabled(settings):
        raise HTTPException(status_code=404, detail="Cloud AI is not configured here.")
    return cloud_ai_status(cloud_ai_config, cloud_ai_runtime)


@app.post("/api/cloud-ai/advisory")
def cloud_ai_advisory_api(
    payload: CloudAdvisoryRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    if not cloud_mode_enabled(settings):
        raise HTTPException(status_code=404, detail="Cloud AI is not configured here.")
    question = payload.question.strip()
    result = answer_verified_live_or_none(
        cloud_verified_live_advisory,
        request_id=f"cloud-live-{secrets.token_hex(12)}",
        tenant_id=cloud_ai_config.tenant_id,
        question=question,
        cad_state=cloud_cad_runtime.state,
        cad_status=cloud_cad_runtime.status(),
        analytics_overview_fn=lambda period: get_analytics_overview(
            period=period, tenant_context=tenant_context
        ),
    )
    if result is None:
        result = answer_cloud_advisory(
            cloud_ai_runtime,
            cloud_ai_config,
            request_id=f"cloud-advisory-{secrets.token_hex(12)}",
            question=question,
            persona=payload.persona,
            roles=_cloud_advisory_roles(tenant_context),
        )
    report_preview = _suggest_report_preview(payload.question, tenant_context)
    if report_preview is not None:
        result["report_preview"] = report_preview
    return result


@app.post("/api/cloud-ai/advisory/stream")
def cloud_ai_advisory_stream_api(
    payload: CloudAdvisoryRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    if not cloud_mode_enabled(settings):
        raise HTTPException(status_code=404, detail="Cloud AI is not configured here.")
    question = payload.question.strip()
    roles = _cloud_advisory_roles(tenant_context)
    report_preview = _suggest_report_preview(payload.question, tenant_context)

    def events():
        for event in stream_cloud_advisory(
            cloud_advisory_streamer,
            cloud_ai_config,
            request_id=f"cloud-advisory-{secrets.token_hex(12)}",
            question=question,
            persona=payload.persona,
            roles=roles,
        ):
            if event.get("type") == "complete":
                result = event["payload"]
                result["interaction_id"] = ""
                result["audit_saved"] = False
                if report_preview is not None:
                    result["report_preview"] = report_preview
            yield event

    return StreamingResponse(
        iter_advisory_ndjson(events()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class CloudSentenceSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    persona: str = Field(default="mae", pattern="^(mae|jack)$")
    voice: str = Field(default="", max_length=40)


@app.post("/api/cloud-ai/speech/sentence")
def cloud_ai_sentence_speech_api(payload: CloudSentenceSpeechRequest):
    if not cloud_mode_enabled(settings):
        raise HTTPException(status_code=404, detail="Cloud AI is not configured here.")
    try:
        audio = synthesize_cloud_sentence(
            cloud_ai_runtime,
            cloud_ai_config,
            request_id=f"cloud-polly-{secrets.token_hex(12)}",
            text=payload.text,
            voice=payload.voice,
        )
    except CloudAiRuntimeUnavailable as exc:
        status = cloud_ai_status(cloud_ai_config, cloud_ai_runtime)
        raise HTTPException(
            status_code=503,
            detail=status["tts"]["disabled_reason"] or str(exc),
        ) from exc
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/voice/speech")
def voice_speech_api(
    payload: VoiceSpeechRequest,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    if cloud_mode_enabled(settings):
        if payload.response_format != "mp3":
            raise HTTPException(status_code=400, detail="Cloud voice supports MP3 only.")
        try:
            audio = synthesize_cloud_speech(
                cloud_ai_runtime,
                cloud_ai_config,
                request_id=f"cloud-polly-{secrets.token_hex(12)}",
                text=payload.text.strip(),
                voice=payload.voice or cloud_ai_config.polly_voice.value,
            )
        except CloudAiRuntimeUnavailable as exc:
            status = cloud_ai_status(cloud_ai_config, cloud_ai_runtime)
            raise HTTPException(
                status_code=503,
                detail=status["tts"]["disabled_reason"] or str(exc),
            ) from exc
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store"},
        )
    try:
        audio, media_type = synthesize_speech(
            payload.text.strip(),
            voice=payload.voice,
            speed=payload.speed,
            response_format=payload.response_format,
            tenant_context=tenant_context,
        )
    except VoiceServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=audio,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'inline; filename="lcdash-voice.{payload.response_format}"'
            ),
        },
    )


@app.post("/api/voice/transcribe")
async def voice_transcribe_api(
    file: UploadFile = File(...),
    audio_format: str = Form("webm-opus"),
    sample_rate_hz: int = Form(48000),
    duration_seconds: float = Form(30.0),
):
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="The recording is empty.")
    if len(audio) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="The recording exceeds the 20 MB beta limit.",
        )

    if cloud_mode_enabled(settings):
        try:
            transcript = await run_in_threadpool(
                transcribe_cloud_speech,
                cloud_ai_runtime,
                cloud_ai_config,
                request_id=f"cloud-stt-{secrets.token_hex(12)}",
                audio=audio,
                audio_format=audio_format,
                sample_rate_hz=sample_rate_hz,
                duration_seconds=duration_seconds,
            )
        except CloudAiRuntimeUnavailable as exc:
            status = cloud_ai_status(cloud_ai_config, cloud_ai_runtime)
            raise HTTPException(
                status_code=503,
                detail=status["stt"]["disabled_reason"] or str(exc),
            ) from exc
        return {
            "text": transcript,
            "model": "Amazon Transcribe streaming en-US",
            "stored": False,
        }

    try:
        return transcribe_audio(
            audio,
            filename=file.filename or "recording.webm",
            content_type=file.content_type or "application/octet-stream",
        )
    except VoiceServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    request: Request,
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    response.headers["Cache-Control"] = "no-store"
    if cloud_mode_enabled(settings):
        jack_question = chat_request.question.strip()
        result = answer_verified_live_or_none(
            cloud_verified_live_advisory,
            request_id=f"cloud-jack-live-{secrets.token_hex(12)}",
            tenant_id=cloud_ai_config.tenant_id,
            question=jack_question,
            cad_state=cloud_cad_runtime.state,
            cad_status=cloud_cad_runtime.status(),
            analytics_overview_fn=lambda period: get_analytics_overview(
                period=period, tenant_context=tenant_context
            ),
        )
        if result is None:
            result = answer_cloud_advisory(
                cloud_ai_runtime,
                cloud_ai_config,
                request_id=f"cloud-jack-{secrets.token_hex(12)}",
                question=jack_question,
                persona="jack",
                roles=_cloud_advisory_roles(tenant_context),
            )
        result["interaction_id"] = ""
        result["audit_saved"] = False
        report_preview = _suggest_report_preview(chat_request.question, tenant_context)
        if report_preview is not None:
            result["report_preview"] = report_preview
        return result
    _deny_unscoped_cloud_advisory_state()
    try:
        result = ask_mindshare(
            chat_request.question,
            [message.model_dump() for message in chat_request.history],
        )
        audit = record_jack_interaction(
            user_email=_authenticated_user_email(request),
            question=chat_request.question,
            result=result,
        )
        result["interaction_id"] = audit.get("interaction_id") or ""
        result["audit_saved"] = bool(audit.get("saved"))
        return result
    except MindshareServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/mindshare/chat/stream")
def mindshare_chat_stream_api(
    chat_request: MindshareChatRequest,
    request: Request,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    if cloud_mode_enabled(settings):
        jack_question = chat_request.question.strip()
        result = answer_verified_live_or_none(
            cloud_verified_live_advisory,
            request_id=f"cloud-jack-live-{secrets.token_hex(12)}",
            tenant_id=cloud_ai_config.tenant_id,
            question=jack_question,
            cad_state=cloud_cad_runtime.state,
            cad_status=cloud_cad_runtime.status(),
            analytics_overview_fn=lambda period: get_analytics_overview(
                period=period, tenant_context=tenant_context
            ),
        )
        if result is None:
            result = answer_cloud_advisory(
                cloud_ai_runtime,
                cloud_ai_config,
                request_id=f"cloud-jack-{secrets.token_hex(12)}",
                question=jack_question,
                persona="jack",
                roles=_cloud_advisory_roles(tenant_context),
            )
        result["interaction_id"] = ""
        result["audit_saved"] = False
        report_preview = _suggest_report_preview(chat_request.question, tenant_context)
        if report_preview is not None:
            result["report_preview"] = report_preview
        body = "".join(
            json.dumps(event, separators=(",", ":")) + "\n"
            for event in (
                {"type": "complete", "payload": result},
                {"type": "done"},
            )
        )
        return StreamingResponse(iter((body,)), media_type="application/x-ndjson")
    _deny_unscoped_cloud_advisory_state()
    events: Queue[dict] = Queue()
    history = [message.model_dump() for message in chat_request.history]
    user_email = _authenticated_user_email(request)

    def run() -> None:
        try:
            result = ask_mindshare(
                chat_request.question,
                history,
                token_callback=lambda token: events.put({"type": "token", "text": token}),
            )
            audit = record_jack_interaction(
                user_email=user_email,
                question=chat_request.question,
                result=result,
            )
            result["interaction_id"] = audit.get("interaction_id") or ""
            result["audit_saved"] = bool(audit.get("saved"))
            events.put({"type": "complete", "payload": result})
        except MindshareServiceError as exc:
            events.put({"type": "error", "detail": str(exc)})
        finally:
            events.put({"type": "done"})

    Thread(target=run, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            yield json.dumps(event, separators=(",", ":")) + "\n"
            if event["type"] == "done":
                break

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/mindshare/feedback")
def mindshare_feedback_api(
    feedback_request: MindshareFeedbackRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = record_jack_feedback(
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
            detail=result.get("message") or "JACK feedback could not be saved.",
        )
    return result


@app.get("/api/mindshare/memory")
def jack_memory_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if settings.deployment_mode == "synthetic-disconnected":
        return {"items": [], "available": False}
    return {"items": list_jack_memory_items()}


@app.post("/api/mindshare/memory")
def jack_memory_create_api(
    memory_request: JackMemoryCreateRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = create_jack_memory_candidate(
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
            detail=result.get("message") or "JACK memory candidate could not be saved.",
        )
    return result


@app.post("/api/mindshare/memory/review")
def jack_memory_review_api(
    memory_request: JackMemoryReviewRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = review_jack_memory(
            memory_id=memory_request.memory_id,
            decision=memory_request.decision,
            reviewed_by=_authenticated_user_email(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("saved"):
        raise HTTPException(
            status_code=404,
            detail=result.get("message") or "JACK memory candidate not found.",
        )
    return result


@app.get("/api/mindshare/evaluations")
def mindshare_evaluations_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if settings.deployment_mode == "synthetic-disconnected":
        return {"cases": [], "summary": {}, "available": False}
    return {
        "cases": list_mindshare_evaluation_cases(),
        "summary": get_mindshare_evaluation_summary(),
    }


@app.post("/api/mindshare/evaluations/run")
def mindshare_evaluation_run_api(
    evaluation_request: MindshareEvaluationRunRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        return run_mindshare_evaluation_case(
            evaluation_request.case_id,
            requested_by=_authenticated_user_email(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/mindshare/coverage")
def mindshare_coverage_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    status = get_knowledge_status(
        library_key="mindshare",
        source_dir=settings.mindshare_knowledge_source_dir,
    )
    documents = list_knowledge_documents(library_key="mindshare")
    return build_mindshare_coverage(documents, status)


@app.get("/api/mae/status")
def mae_status_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if cloud_mode_enabled(settings):
        presentation = _cloud_presentation_status(get_knowledge_status())
        return {
            "mae": "Mission Assistance Engine",
            "mode": "Inquiry only",
            "write_access": False,
            "local_ai": {
                "connected": presentation["advisory"]["ready"],
                "model": _cloud_ai_model_label(),
                "installed_models": [],
                "error": presentation["advisory"]["notice"],
            },
            "database": get_analytics_database_status(),
            "centralsquare": {
                "configured": presentation["source"]["provider_selected"],
                "connected": presentation["source"]["connected"],
                "mode": presentation["source"]["label"],
                "notice": presentation["source"]["notice"],
            },
            "knowledge": dict(presentation["knowledge"]),
            "voice": dict(presentation["voice"]),
            "tools": get_mae_tool_catalog(),
        }
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
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = ask_mae(
            chat_request.question,
            [message.model_dump() for message in chat_request.history],
            chat_request.entities.model_dump(),
            tenant_context=tenant_context,
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


@app.post("/api/mae/chat/stream")
def mae_chat_stream_api(
    chat_request: MAEChatRequest,
    request: Request,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    _deny_unscoped_cloud_advisory_state()
    events: Queue[dict] = Queue()
    history = [message.model_dump() for message in chat_request.history]
    entities = chat_request.entities.model_dump()
    user_email = _authenticated_user_email(request)

    def run() -> None:
        try:
            result = ask_mae(
                chat_request.question,
                history,
                entities,
                token_callback=lambda token: events.put({"type": "token", "text": token}),
                tenant_context=tenant_context,
            )
            audit = record_mae_interaction(
                user_email=user_email,
                question=chat_request.question,
                result=result,
            )
            result["interaction_id"] = audit.get("interaction_id") or ""
            result["audit_saved"] = bool(audit.get("saved"))
            events.put({"type": "complete", "payload": result})
        except MAEServiceError as exc:
            events.put({"type": "error", "detail": str(exc)})
        finally:
            events.put({"type": "done"})

    Thread(target=run, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            yield json.dumps(event, separators=(",", ":")) + "\n"
            if event["type"] == "done":
                break

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/mae/feedback")
def mae_feedback_api(
    feedback_request: MAEFeedbackRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
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
    if settings.deployment_mode == "synthetic-disconnected":
        return {"cases": [], "summary": {}, "available": False}
    return {
        "cases": list_evaluation_cases(),
        "summary": get_evaluation_summary(),
    }


@app.post("/api/mae/evaluations/run")
def mae_evaluation_run_api(
    evaluation_request: MAEEvaluationRunRequest,
    request: Request,
    response: Response,
    tenant_context: Annotated[
        TenantContext | None,
        Depends(get_trusted_tenant_context),
    ] = None,
):
    _deny_unscoped_cloud_advisory_state()
    response.headers["Cache-Control"] = "no-store"
    try:
        return run_evaluation_case(
            evaluation_request.case_id,
            requested_by=_authenticated_user_email(request),
            tenant_context=tenant_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/mae/feedback/review")
def mae_feedback_review_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if settings.deployment_mode == "synthetic-disconnected":
        return {"feedback": [], "available": False}
    return {"feedback": list_feedback_review()}


@app.get("/api/mae/memory")
def mae_memory_api(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if settings.deployment_mode == "synthetic-disconnected":
        return {"items": [], "available": False}
    return {"items": list_memory_items()}


@app.post("/api/mae/memory")
def mae_memory_create_api(
    memory_request: MAEMemoryCreateRequest,
    request: Request,
    response: Response,
):
    _deny_unscoped_cloud_advisory_state()
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
    _deny_unscoped_cloud_advisory_state()
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
