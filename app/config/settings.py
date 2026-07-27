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
    ollama_base_url: str = _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    mae_model: str = _env("MAE_MODEL", "qwen3:8b")
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
        "Systran/faster-distil-whisper-small.en",
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
