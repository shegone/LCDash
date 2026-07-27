from __future__ import annotations

import re
from typing import Any

import httpx

from app.config.settings import settings


class VoiceServiceError(Exception):
    """Raised when the private LCDash speech service cannot complete a request."""


VOICE_CHOICES = (
    {"id": "af_heart", "label": "Heart", "description": "Warm American female"},
    {"id": "af_bella", "label": "Bella", "description": "Clear American female"},
    {"id": "am_adam", "label": "Adam", "description": "Steady American male"},
    {"id": "am_michael", "label": "Michael", "description": "Direct American male"},
)


def _base_url() -> str:
    return settings.voice_base_url.rstrip("/")


def prepare_text_for_speech(text: str) -> str:
    """Apply LCDash pronunciation rules without changing displayed text."""
    prepared = re.sub(r"\bMAE\b", "May", text, flags=re.IGNORECASE)
    prepared = re.sub(r"\b9[\s-]*1[\s-]*1\b", "nine one one", prepared)
    return prepared


def get_voice_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "connected": False,
        "service": "Speaches",
        "tts": {
            "model": settings.voice_tts_model,
            "voice": settings.voice_tts_voice,
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
            health_response = client.get(f"{_base_url()}/health")
            health_response.raise_for_status()
            result["connected"] = True

            models_response = client.get(f"{_base_url()}/v1/models")
            models_response.raise_for_status()
            payload = models_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        result["detail"] = str(exc)
        return result

    model_ids = {
        str(item.get("id") or "")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    }
    result["tts"]["ready"] = settings.voice_tts_model in model_ids
    result["stt"]["ready"] = settings.voice_stt_model in model_ids
    result["installed_models"] = sorted(model_ids)
    return result


def synthesize_speech(
    text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    response_format: str = "mp3",
) -> tuple[bytes, str]:
    selected_voice = voice or settings.voice_tts_voice
    allowed_voices = {item["id"] for item in VOICE_CHOICES}
    if selected_voice not in allowed_voices:
        raise VoiceServiceError("The selected voice is not available.")

    if response_format not in {"mp3", "wav"}:
        raise VoiceServiceError("The requested audio format is not supported.")

    try:
        with httpx.Client(timeout=settings.voice_request_timeout_seconds) as client:
            response = client.post(
                f"{_base_url()}/v1/audio/speech",
                json={
                    "model": settings.voice_tts_model,
                    "voice": selected_voice,
                    "input": prepare_text_for_speech(text),
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

    media_type = "audio/mpeg" if response_format == "mp3" else "audio/wav"
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
