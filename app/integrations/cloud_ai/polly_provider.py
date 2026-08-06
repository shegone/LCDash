"""Bounded Amazon Polly adapter for optional advisory speech."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .contracts import PollySpeechRequest


APPROVED_REGION = "us-east-1"


class PollyClient(Protocol):
    def synthesize_speech(self, **kwargs: Any) -> dict[str, Any]: ...


def build_polly_client() -> PollyClient:
    """Create the regional client without making a Polly API request."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "polly",
        region_name=APPROVED_REGION,
        config=Config(
            connect_timeout=3,
            read_timeout=15,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ),
    )


class AwsPollySpeechProvider:
    """Synthesize one bounded MP3 response without persistence or action tools."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], PollyClient] = build_polly_client,
        max_audio_bytes: int = 5_000_000,
    ) -> None:
        if not 1 <= max_audio_bytes <= 5_000_000:
            raise ValueError("Polly output limit must be between 1 and 5000000 bytes.")
        self._client_factory = client_factory
        self._max_audio_bytes = max_audio_bytes
        self._client: PollyClient | None = None

    def _client_for_request(self) -> PollyClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def synthesize(self, request: PollySpeechRequest) -> bytes:
        response = self._client_for_request().synthesize_speech(
            Engine="neural",
            OutputFormat="mp3",
            Text=request.spoken_text,
            TextType="text",
            VoiceId=request.voice.value,
        )
        stream = response.get("AudioStream")
        if stream is None or not hasattr(stream, "read"):
            raise RuntimeError("polly_audio_stream_missing")
        try:
            content_type = response.get("ContentType")
            if content_type not in (None, "audio/mpeg"):
                raise RuntimeError("polly_content_type_invalid")
            audio = stream.read(self._max_audio_bytes + 1)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not isinstance(audio, bytes) or not audio:
            raise RuntimeError("polly_audio_empty")
        if len(audio) > self._max_audio_bytes:
            raise RuntimeError("polly_audio_limit")
        return audio
