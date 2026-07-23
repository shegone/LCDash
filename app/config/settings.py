from dataclasses import dataclass
from dotenv import load_dotenv
import os


load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    token_url: str = os.getenv("CENTRALSQUARE_TOKEN_URL", "")
    cad_base_url: str = os.getenv("CENTRALSQUARE_CAD_BASE_URL", "")
    system_base_url: str = os.getenv("CENTRALSQUARE_SYSTEM_BASE_URL", "")
    username: str = os.getenv("CENTRALSQUARE_USERNAME", "")
    password: str = os.getenv("CENTRALSQUARE_PASSWORD", "")
    from_header: str = os.getenv("CENTRALSQUARE_FROM_HEADER", "LCDash")
    debug: bool = os.getenv("LCDASH_DEBUG", "true").lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "")
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


settings = Settings()
