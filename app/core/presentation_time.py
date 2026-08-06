"""Authoritative cloud presentation timezone helpers; source values stay unchanged."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


PRESENTATION_TIMEZONE_NAME = "America/New_York"
PRESENTATION_TIMEZONE = ZoneInfo(PRESENTATION_TIMEZONE_NAME)


def parse_source_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Source timestamp must include an offset.")
    return parsed


def eastern_display_timestamp(value: str) -> str:
    """Return an Eastern display value without changing the source timestamp."""
    return parse_source_timestamp(value).astimezone(PRESENTATION_TIMEZONE).isoformat()


def source_and_eastern_display(value: str) -> dict[str, str]:
    return {"source_timestamp": value, "display_timestamp": eastern_display_timestamp(value)}
