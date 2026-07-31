from dataclasses import dataclass
from dotenv import load_dotenv
import os
from pathlib import Path


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


@dataclass
class Settings:
    token_url: str = _env("CENTRALSQUARE_TOKEN_URL")
    cad_base_url: str = _env("CENTRALSQUARE_CAD_BASE_URL")
    system_base_url: str = _env("CENTRALSQUARE_SYSTEM_BASE_URL")
    username: str = _env("CENTRALSQUARE_USERNAME")
    password: str = _env("CENTRALSQUARE_PASSWORD")
    from_header: str = _env("CENTRALSQUARE_FROM_HEADER", "LCDash")
    debug: bool = _env("LCDASH_DEBUG", "true").lower() == "true"
    database_url: str = _env("DATABASE_URL")
    centralsquare_webhook_secret: str = _env("CENTRALSQUARE_WEBHOOK_SECRET")
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
    mae_model: str = _env("MAE_MODEL", "qwen3.5:27b")
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


settings = Settings()
