from dataclasses import dataclass, field
from dotenv import load_dotenv
import os
from pathlib import Path
from urllib.parse import quote


load_dotenv()


def _env(name: str, default: str = "") -> str:
    file_name = os.getenv(f"{name}_FILE", "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError:
            return default
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_list(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in _env(name, default).split(",")
        if item.strip()
    )


def _database_url() -> str:
    """Build the pilot DSN from ECS-injected parts; preserve local legacy use."""
    deployment_mode = _env("LCDASH_DEPLOYMENT_MODE").strip().lower()
    legacy_url = _env("DATABASE_URL").strip()
    if deployment_mode != "synthetic-disconnected":
        return legacy_url

    if legacy_url:
        raise ValueError(
            "DATABASE_URL is not accepted in the synthetic-disconnected pilot; "
            "use separate LCDASH_DATABASE_* values."
        )

    names = (
        "LCDASH_DATABASE_HOST",
        "LCDASH_DATABASE_PORT",
        "LCDASH_DATABASE_NAME",
        "LCDASH_DATABASE_USERNAME",
        "LCDASH_DATABASE_PASSWORD",
    )
    values = {name: _env(name).strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(
            "Missing required synthetic pilot database settings: "
            + ", ".join(missing)
        )

    host = values["LCDASH_DATABASE_HOST"]
    if any(character.isspace() for character in host) or any(
        marker in host for marker in ("://", "/", "@")
    ):
        raise ValueError("LCDASH_DATABASE_HOST is not a valid database hostname.")
    try:
        port = int(values["LCDASH_DATABASE_PORT"])
    except ValueError as error:
        raise ValueError("LCDASH_DATABASE_PORT must be an integer.") from error
    if not 1 <= port <= 65535:
        raise ValueError("LCDASH_DATABASE_PORT must be between 1 and 65535.")

    username = quote(values["LCDASH_DATABASE_USERNAME"], safe="")
    password = quote(values["LCDASH_DATABASE_PASSWORD"], safe="")
    database_name = quote(values["LCDASH_DATABASE_NAME"], safe="")
    return f"postgresql://{username}:{password}@{host}:{port}/{database_name}"


@dataclass
class Settings:
    token_url: str = _env("CENTRALSQUARE_TOKEN_URL")
    cad_base_url: str = _env("CENTRALSQUARE_CAD_BASE_URL")
    system_base_url: str = _env("CENTRALSQUARE_SYSTEM_BASE_URL")
    username: str = _env("CENTRALSQUARE_USERNAME")
    password: str = field(default=_env("CENTRALSQUARE_PASSWORD"), repr=False)
    from_header: str = _env("CENTRALSQUARE_FROM_HEADER", "LCDash")
    debug: bool = _env("LCDASH_DEBUG", "true").lower() == "true"
    deployment_mode: str = _env("LCDASH_DEPLOYMENT_MODE", "on-prem").strip().lower()
    tenant_id: str = _env("LCDASH_TENANT", "logan-synthetic").strip().lower()
    # Per-user identity recovered from ALB-signed OIDC headers. Ships dormant:
    # until this is enabled every request keeps the previous deployment-wide
    # viewer identity, so turning it on is a deliberate, reversible step.
    alb_identity_enabled: bool = _env_bool("LCDASH_ALB_IDENTITY_ENABLED", False)
    alb_identity_region: str = _env("LCDASH_ALB_IDENTITY_REGION", "us-east-1").strip()
    alb_identity_load_balancer_arn: str = _env(
        "LCDASH_ALB_IDENTITY_LOAD_BALANCER_ARN"
    ).strip()
    alb_identity_user_pool_id: str = _env("LCDASH_ALB_IDENTITY_USER_POOL_ID").strip()
    alb_identity_client_id: str = _env("LCDASH_ALB_IDENTITY_CLIENT_ID").strip()
    # Cognito hosted-UI origin and the post-sign-out landing page. Sign-out
    # needs BOTH: expiring the ALB session cookie only ends the ALB's session,
    # while Cognito's own session would silently sign the next person straight
    # back in without a password. Blank disables the sign-out control rather
    # than offering a button that half-works.
    alb_identity_hosted_ui_url: str = _env(
        "LCDASH_ALB_IDENTITY_HOSTED_UI_URL"
    ).strip().rstrip("/")
    alb_identity_signed_out_url: str = _env(
        "LCDASH_ALB_IDENTITY_SIGNED_OUT_URL"
    ).strip()
    # The ALB's session cookie name, supplied by the stack that configures it.
    # The default is AWS's, but this deployment sets a custom name -- sign-out
    # first shipped expiring the AWS default and therefore deleted nothing, so
    # this value must come from the same constant the listener uses.
    alb_identity_session_cookie: str = _env(
        "LCDASH_ALB_IDENTITY_SESSION_COOKIE", "AWSELBAuthSessionCookie"
    ).strip()
    cloud_cad_enabled: bool = _env_bool("LCDASH_CLOUD_CAD_ENABLED", False)
    cloud_cad_mode: str = _env(
        "LCDASH_CLOUD_CAD_MODE", "synthetic-disconnected"
    ).strip().lower()
    cloud_cad_secret_arn: str = _env("LCDASH_CLOUD_CAD_SECRET_ARN").strip()
    cloud_cad_poll_seconds: int = _env_int("LCDASH_CLOUD_CAD_POLL_SECONDS", 30)
    cloud_cad_reconciliation_overlap_seconds: int = _env_int(
        "LCDASH_CLOUD_CAD_RECONCILIATION_OVERLAP_SECONDS", 120
    )
    cloud_ai_mode: str = _env("LCDASH_CLOUD_AI_MODE", "advisory-rag").strip().lower()
    cloud_ai_knowledge_base_id: str = _env(
        "LCDASH_CLOUD_AI_KNOWLEDGE_BASE_ID"
    ).strip()
    cloud_ai_documents_ingested: bool = _env_bool(
        "LCDASH_CLOUD_AI_DOCUMENTS_INGESTED", False
    )
    cloud_ai_generation_model_id: str = _env(
        "LCDASH_CLOUD_AI_GENERATION_MODEL_ID", "amazon.nova-micro-v1:0"
    ).strip()
    cloud_ai_max_output_tokens: int = _env_int(
        "LCDASH_CLOUD_AI_MAX_OUTPUT_TOKENS", 512
    )
    cloud_ai_retrieval_result_limit: int = _env_int(
        "LCDASH_CLOUD_AI_RETRIEVAL_RESULT_LIMIT", 5
    )
    cloud_ai_retrieval_score_threshold: float = _env_float(
        "LCDASH_CLOUD_AI_RETRIEVAL_SCORE_THRESHOLD", 0.5
    )
    cloud_ai_allowed_s3_prefixes: tuple[str, ...] = _env_list(
        "LCDASH_CLOUD_AI_ALLOWED_S3_PREFIXES",
        (
            "s3://lcdash-p1-logan-use1-862772137583-document-library/"
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/,"
            "s3://lcdash-p1-logan-use1-862772137583-document-library/"
            "tenants/logan-synthetic/document-library/centralsquare/current/"
            "onprem-approved-164-2026-08-05/"
        ),
    )
    # The Bedrock data source that indexes ADMIN-UPLOADED documents (the two
    # cloud-uploads prefixes), as opposed to the two out-of-band data sources
    # holding the approved on-prem sets. Blank means the uploads feature is
    # not provisioned: upload pages still render but sync reports itself
    # unconfigured rather than guessing at a data source.
    cloud_ai_uploads_data_source_id: str = _env(
        "LCDASH_CLOUD_AI_UPLOADS_DATA_SOURCE_ID"
    ).strip()
    # Where admin knowledge uploads land. County-specific values live HERE
    # (settings is the one sanctioned home for them -- see the county-profile
    # contract test); the upload service takes these as plain arguments.
    # jack's prefix must contain "/mindshare/" (persona filter) and its first
    # segment after document-library/ must differ from "mindshare" (library
    # key collision) -- see knowledge_upload_service's module docstring.
    knowledge_upload_bucket: str = _env(
        "LCDASH_KNOWLEDGE_UPLOAD_BUCKET",
        "lcdash-p1-logan-use1-862772137583-document-library",
    ).strip()
    knowledge_upload_mae_prefix: str = _env(
        "LCDASH_KNOWLEDGE_UPLOAD_MAE_PREFIX",
        "tenants/logan-synthetic/document-library/mae-uploads/current/",
    ).strip()
    knowledge_upload_jack_prefix: str = _env(
        "LCDASH_KNOWLEDGE_UPLOAD_JACK_PREFIX",
        "tenants/logan-synthetic/document-library/jack-uploads/mindshare/current/",
    ).strip()
    cloud_ai_polly_voice: str = _env(
        "LCDASH_CLOUD_AI_POLLY_VOICE", "Ruth"
    ).strip()
    cloud_ai_voice_enabled: bool = _env_bool(
        "LCDASH_CLOUD_AI_VOICE_ENABLED", True
    )
    cloud_ai_tool_calling_enabled: bool = _env_bool(
        "LCDASH_CLOUD_AI_TOOL_CALLING_ENABLED", False
    )
    cloud_ai_tool_model_id: str = _env(
        "LCDASH_CLOUD_AI_TOOL_MODEL_ID"
    ).strip()
    database_url: str = field(default_factory=_database_url, repr=False)
    centralsquare_webhook_secret: str = field(
        default=_env("CENTRALSQUARE_WEBHOOK_SECRET"),
        repr=False,
    )
    webhook_max_body_bytes: int = _env_int(
        "WEBHOOK_MAX_BODY_BYTES",
        1048576,
    )
    realtime_heartbeat_seconds: int = _env_int(
        "REALTIME_HEARTBEAT_SECONDS",
        15,
    )
    analytics_initial_lookback_hours: int = _env_int(
        "ANALYTICS_INITIAL_LOOKBACK_HOURS",
        24,
    )
    analytics_overlap_minutes: int = _env_int(
        "ANALYTICS_OVERLAP_MINUTES",
        120,
    )
    analytics_max_calls_per_run: int = _env_int(
        "ANALYTICS_MAX_CALLS_PER_RUN",
        250,
    )
    analytics_request_delay_ms: int = _env_int(
        "ANALYTICS_REQUEST_DELAY_MS",
        100,
    )
    ems_supervisor_unit_numbers: tuple[str, ...] = _env_list(
        "EMS_SUPERVISOR_UNIT_NUMBERS",
        "EMS101,EMS102,EMS103,EMS104,EMS105,EMS106,EMS107,EMS108,EMS109",
    )
    ems_delay_alert_enabled: bool = _env_bool(
        "EMS_DELAY_ALERT_ENABLED",
        False,
    )
    ems_delay_alert_mode: str = _env(
        "EMS_DELAY_ALERT_MODE",
        "dry_run",
    ).strip().lower()
    ems_delay_threshold_minutes: int = _env_int(
        "EMS_DELAY_THRESHOLD_MINUTES",
        30,
    )
    ems_delay_repeat_minutes: int = _env_int(
        "EMS_DELAY_REPEAT_MINUTES",
        30,
    )
    ems_delay_poll_seconds: int = _env_int(
        "EMS_DELAY_POLL_SECONDS",
        60,
    )
    ems_delay_run_command_id: int = _env_int(
        "EMS_DELAY_RUN_COMMAND_ID",
        96,
    )
    ems_delay_message_type_id: int = _env_int(
        "EMS_DELAY_MESSAGE_TYPE_ID",
        16,
    )
    ems_delay_message_type_description: str = _env(
        "EMS_DELAY_MESSAGE_TYPE_DESCRIPTION",
        "LCDash MAE EMS Delay Alert",
    )
    ems_delay_transfer_codes: tuple[str, ...] = _env_list(
        "EMS_DELAY_TRANSFER_CODES",
        "TRANSFER,911TRANS",
    )
    ems_delay_scheduled_codes: tuple[str, ...] = _env_list(
        "EMS_DELAY_SCHEDULED_CODES",
        "PRESCHED",
    )
    ems_response_agencies: tuple[str, ...] = _env_list(
        "EMS_RESPONSE_AGENCIES",
        "LEASA",
    )
    ems_unit_prefixes: tuple[str, ...] = _env_list(
        "EMS_UNIT_PREFIXES",
        "MED,EMS",
    )
    nga911_provider_mode: str = _env(
        "NGA911_PROVIDER_MODE",
        "mock",
    )
    ollama_base_url: str = _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    mae_model: str = _env("MAE_MODEL", "qwen3.6:27b")
    mae_request_timeout_seconds: int = _env_int(
        "MAE_REQUEST_TIMEOUT_SECONDS",
        120,
    )
    knowledge_source_dir: str = _env(
        "KNOWLEDGE_SOURCE_DIR",
        "knowledge/centralsquare",
    )
    mindshare_knowledge_source_dir: str = _env(
        "MINDSHARE_KNOWLEDGE_SOURCE_DIR",
        "knowledge/mindshare",
    )
    gis_reference_dir: str = _env(
        "GIS_REFERENCE_DIR",
        "data/gis-public",
    )
    knowledge_index_interval_seconds: int = _env_int(
        "KNOWLEDGE_INDEX_INTERVAL_SECONDS",
        3600,
    )
    mae_embedding_model: str = _env(
        "MAE_EMBEDDING_MODEL",
        "nomic-embed-text",
    )
    mae_embedding_timeout_seconds: int = _env_int(
        "MAE_EMBEDDING_TIMEOUT_SECONDS",
        45,
    )
    voice_base_url: str = _env(
        "VOICE_BASE_URL",
        "http://127.0.0.1:8001",
    )
    voice_tts_model: str = _env(
        "VOICE_TTS_MODEL",
        "speaches-ai/Kokoro-82M-v1.0-ONNX",
    )
    voice_tts_voice: str = _env("VOICE_TTS_VOICE", "af_heart")
    voice_qwen_tts_base_url: str = _env(
        "VOICE_QWEN_TTS_BASE_URL",
        "http://127.0.0.1:8003",
    )
    voice_qwen_tts_model: str = _env(
        "VOICE_QWEN_TTS_MODEL",
        "lcdash-qwen3-tts-mae",
    )
    voice_qwen_tts_voice: str = _env(
        "VOICE_QWEN_TTS_VOICE",
        "mae-synthetic-female",
    )
    voice_jack_tts_base_url: str = _env(
        "VOICE_JACK_TTS_BASE_URL",
        "http://127.0.0.1:8005",
    )
    voice_jack_tts_model: str = _env(
        "VOICE_JACK_TTS_MODEL",
        "lcdash-qwen3-tts-jack",
    )
    voice_stt_model: str = _env(
        "VOICE_STT_MODEL",
        "deepdml/faster-whisper-large-v3-turbo-ct2",
    )
    voice_request_timeout_seconds: int = _env_int(
        "VOICE_REQUEST_TIMEOUT_SECONDS",
        180,
    )
    knowledge_semantic_candidates: int = _env_int(
        "KNOWLEDGE_SEMANTIC_CANDIDATES",
        5000,
    )
    # Rapport.cloud's pixel-streamed MetaHuman avatar, layered over (not
    # replacing) the local three.js/portrait renderer chain on /mae/avatar.
    # Ships dormant: the project-token below is publishable and safe to sit
    # in page HTML by Rapport's own design, but it is still plumbed through
    # env rather than hardcoded so rotating it is a config change, not a
    # code change. mae_avatar_page() in app/main.py ANDs this with a
    # non-empty token before it ever reaches the template -- a flag left on
    # with no token configured must render nothing, not a broken widget.
    mae_rapport_enabled: bool = _env_bool("MAE_RAPPORT_ENABLED", False)
    mae_rapport_project_token: str = _env("MAE_RAPPORT_PROJECT_TOKEN").strip()


settings = Settings()
