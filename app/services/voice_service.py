from __future__ import annotations

import re
from typing import Any

import httpx

from app.config.settings import settings
from app.core.county_profiles import resolve_county_profile
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import (
    TenantAuthorizationDenied,
    authorize_tenant_action,
)
from app.integrations.contracts import ModuleCapability


class VoiceServiceError(Exception):
    """Raised when the private LCDash speech service cannot complete a request."""


VOICE_CHOICES = (
    {
        "id": "mae-synthetic-female",
        "label": "MAE",
        "description": "Expressive synthetic American female (Qwen3-TTS)",
    },
    {
        "id": "jack-synthetic-southern-male",
        "label": "JACK",
        "description": "Mature, steady Southern American male (Qwen3-TTS)",
    },
    {"id": "af_heart", "label": "Heart", "description": "Warm American female"},
    {"id": "af_bella", "label": "Bella", "description": "Clear American female"},
    {"id": "af_nicole", "label": "Nicole", "description": "Natural American female"},
    {"id": "af_sarah", "label": "Sarah", "description": "Calm American female"},
    {"id": "af_kore", "label": "Kore", "description": "Confident American female"},
    {"id": "am_adam", "label": "Adam", "description": "Steady American male"},
    {"id": "am_fenrir", "label": "Fenrir", "description": "Expressive American male"},
    {"id": "am_michael", "label": "Michael", "description": "Direct American male"},
    {"id": "am_puck", "label": "Puck", "description": "Conversational American male"},
)

_WORDS_UNDER_TWENTY = (
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
)
_TENS_WORDS = ("", "", "twenty", "thirty", "forty", "fifty")
_PREFIXED_TIME_PATTERN = re.compile(
    r"\b(?P<prefix>(?:dispatch\s+)?time(?:\s+(?:is|was))?\s*[:]?|at)\s+"
    r"(?P<hour>[01]\d|2[0-3])(?::?)(?P<minute>[0-5]\d)\b",
    flags=re.IGNORECASE,
)
_COLON_TIME_PATTERN = re.compile(
    r"\b(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)\b"
)


def _base_url() -> str:
    return settings.voice_base_url.rstrip("/")


def _tts_base_url(voice: str) -> str:
    if voice == "jack-synthetic-southern-male":
        return settings.voice_jack_tts_base_url.rstrip("/")
    if voice in {
        settings.voice_qwen_tts_voice,
    }:
        return settings.voice_qwen_tts_base_url.rstrip("/")
    return _base_url()


def _tts_model(voice: str) -> str:
    if voice == "jack-synthetic-southern-male":
        return settings.voice_jack_tts_model
    if voice in {
        settings.voice_qwen_tts_voice,
    }:
        return settings.voice_qwen_tts_model
    return settings.voice_tts_model


def _spoken_number(value: int) -> str:
    if value < 20:
        return _WORDS_UNDER_TWENTY[value]
    tens, remainder = divmod(value, 10)
    return (
        _TENS_WORDS[tens]
        if not remainder
        else f"{_TENS_WORDS[tens]}-{_WORDS_UNDER_TWENTY[remainder]}"
    )


def spoken_24_hour_time(hour: int, minute: int) -> str:
    """Write an HHMM value as unambiguous speech, for example 15:23."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Hour and minute must form a valid 24-hour time.")
    hour_phrase = (
        f"zero {_spoken_number(hour)}"
        if hour < 10
        else _spoken_number(hour)
    )
    if minute == 0:
        return f"{hour_phrase} hundred"
    if minute < 10:
        return f"{hour_phrase} oh {_spoken_number(minute)}"
    return f"{hour_phrase} {_spoken_number(minute)}"


def _expand_time_match(match: re.Match) -> str:
    return (
        f"{match.group('prefix')} "
        f"{spoken_24_hour_time(int(match.group('hour')), int(match.group('minute')))}"
    )


def _pronunciation_911(county_profile: CountyProfile | None) -> str:
    if county_profile is None:
        return "nine one one"

    pronunciation = county_profile.voice_profile.get("pronunciation_911")
    if pronunciation != "nine one one":
        raise ValueError("County profile must pronounce 911 as nine one one.")
    return pronunciation


def prepare_text_for_speech(
    text: str,
    county_profile: CountyProfile | None = None,
) -> str:
    """Apply LCDash pronunciation rules without changing displayed text."""
    pronunciation_911 = _pronunciation_911(county_profile)
    prepared = str(text or "")
    prepared = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", prepared)
    prepared = re.sub(r"(?m)^\s*[-*+]\s+", "", prepared)
    prepared = re.sub(r"(?m)^\s*\d+[.)]\s+", "", prepared)
    prepared = re.sub(r"[*_`~]", "", prepared)
    prepared = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", prepared)
    prepared = re.sub(r"\s*\n+\s*", ". ", prepared)
    prepared = re.sub(r"([.!?])\.\s+", r"\1 ", prepared)
    prepared = re.sub(r"\s+", " ", prepared).strip()
    prepared = re.sub(r"\bMAE\b", "May", prepared, flags=re.IGNORECASE)
    prepared = re.sub(
        r"\bNGA[\s-]*9[\s-]*1[\s-]*1\b",
        f"N G A {pronunciation_911}",
        prepared,
        flags=re.IGNORECASE,
    )
    prepared = re.sub(
        r"\b9[\s-]*1[\s-]*1\b",
        pronunciation_911,
        prepared,
    )
    prepared = _PREFIXED_TIME_PATTERN.sub(_expand_time_match, prepared)
    prepared = _COLON_TIME_PATTERN.sub(
        lambda match: spoken_24_hour_time(
            int(match.group("hour")), int(match.group("minute"))
        ),
        prepared,
    )
    return prepared


def get_voice_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "connected": False,
        "service": "Qwen3-TTS and Speaches",
        "tts": {
            "model": _tts_model(settings.voice_tts_voice),
            "voice": settings.voice_tts_voice,
            "ready": False,
        },
        "jack_tts": {
            "model": _tts_model("jack-synthetic-southern-male"),
            "voice": "jack-synthetic-southern-male",
            "ready": False,
        },
        "stt": {
            "model": settings.voice_stt_model,
            "ready": False,
        },
        "voices": list(VOICE_CHOICES),
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            stt_health_response = client.get(f"{_base_url()}/health")
            stt_health_response.raise_for_status()
            tts_health_response = client.get(
                f"{_tts_base_url(settings.voice_tts_voice)}/health"
            )
            tts_health_response.raise_for_status()
            tts_models_response = client.get(
                f"{_tts_base_url(settings.voice_tts_voice)}/v1/models"
            )
            tts_models_response.raise_for_status()
            jack_models_response = client.get(
                f"{_tts_base_url('jack-synthetic-southern-male')}/v1/models"
            )
            jack_models_response.raise_for_status()
            stt_models_response = client.get(f"{_base_url()}/v1/models")
            stt_models_response.raise_for_status()
            tts_payload = tts_models_response.json()
            jack_payload = jack_models_response.json()
            stt_payload = stt_models_response.json()
            result["connected"] = True
    except (httpx.HTTPError, ValueError) as exc:
        result["detail"] = str(exc)
        return result

    model_ids = {
        str(item.get("id") or "")
        for item in tts_payload.get("data", [])
        if isinstance(item, dict)
    }
    stt_model_ids = {
        str(item.get("id") or "")
        for item in stt_payload.get("data", [])
        if isinstance(item, dict)
    }
    jack_model_ids = {
        str(item.get("id") or "")
        for item in jack_payload.get("data", [])
        if isinstance(item, dict)
    }
    result["tts"]["ready"] = _tts_model(settings.voice_tts_voice) in model_ids
    result["jack_tts"]["ready"] = (
        _tts_model("jack-synthetic-southern-male") in jack_model_ids
    )
    result["stt"]["ready"] = settings.voice_stt_model in stt_model_ids
    result["installed_models"] = sorted(model_ids | stt_model_ids)
    return result


def synthesize_speech(
    text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    response_format: str = "mp3",
    county_profile: CountyProfile | None = None,
    tenant_context: TenantContext | None = None,
) -> tuple[bytes, str]:
    if tenant_context is not None:
        if county_profile is not None:
            raise TenantAuthorizationDenied(
                "Trusted context and direct county profile cannot be combined."
            )
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.VOICE,
            "read",
        )

    selected_voice = voice or settings.voice_tts_voice
    allowed_voices = {item["id"] for item in VOICE_CHOICES}
    if selected_voice not in allowed_voices:
        raise VoiceServiceError("The selected voice is not available.")

    if response_format not in {"mp3", "wav"}:
        raise VoiceServiceError("The requested audio format is not supported.")

    try:
        with httpx.Client(timeout=settings.voice_request_timeout_seconds) as client:
            response = client.post(
                f"{_tts_base_url(selected_voice)}/v1/audio/speech",
                json={
                    "model": _tts_model(selected_voice),
                    "voice": selected_voice,
                    "input": prepare_text_for_speech(text, county_profile),
                    "response_format": response_format,
                    "speed": speed,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise VoiceServiceError(
            f"Speech generation was rejected by the local engine: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise VoiceServiceError(
            "The local speech engine is unavailable."
        ) from exc

    response_media_type = response.headers.get("content-type", "").split(";", 1)[0]
    media_type = response_media_type or (
        "audio/mpeg" if response_format == "mp3" else "audio/wav"
    )
    return response.content, media_type


def transcribe_audio(
    audio: bytes,
    *,
    filename: str,
    content_type: str,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=settings.voice_request_timeout_seconds) as client:
            response = client.post(
                f"{_base_url()}/v1/audio/transcriptions",
                data={
                    "model": settings.voice_stt_model,
                    "language": "en",
                    "response_format": "json",
                },
                files={
                    "file": (
                        filename or "recording.webm",
                        audio,
                        content_type or "application/octet-stream",
                    )
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise VoiceServiceError(
            f"Transcription was rejected by the local engine: {detail}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise VoiceServiceError(
            "The local transcription engine is unavailable."
        ) from exc

    return {
        "text": str(payload.get("text") or "").strip(),
        "model": settings.voice_stt_model,
        "stored": False,
    }
