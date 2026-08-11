"""Bounded Amazon Polly adapter for optional advisory speech."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from .contracts import (
    MAX_VISEME_MARKS,
    POLLY_VISEMES,
    PollySpeechRequest,
    PollySpeechWithVisemes,
    PollyVisemeMark,
    polly_engine_for_voice,
    supports_neural_engine,
)


APPROVED_REGION = "us-east-1"

# Speech marks are newline-delimited JSON, a few dozen bytes per phoneme;
# a bounded read keeps a malfunctioning stream from growing without limit.
MAX_SPEECH_MARK_BYTES = 1_000_000


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
        return self._synthesize_audio(request, polly_engine_for_voice(request.voice))

    def _synthesize_audio(self, request: PollySpeechRequest, engine: str) -> bytes:
        response = self._client_for_request().synthesize_speech(
            Engine=engine,
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

    def synthesize_with_visemes(
        self, request: PollySpeechRequest
    ) -> PollySpeechWithVisemes:
        """One utterance plus its viseme timeline, in two bounded Polly calls.

        Both calls are forced onto the neural engine regardless of the
        voice's normal chat engine: neural is the only Polly engine that
        emits viseme speech marks, so a generative voice used for chat
        (Ruth, Stephen) would otherwise silently freeze the avatar's mouth
        if this call reused the chat engine. The guard below exists for a
        future voice that lacks neural support entirely -- today's voices
        (Ruth, Stephen) both support it, so the same voice identity speaks
        everywhere: chat renders it on generative, the avatar renders it on
        neural. The speech-mark call reuses the exact text and voice of the
        audio call so the timeline can never describe a different utterance
        than the one heard.
        """

        if not supports_neural_engine(request.voice):
            raise RuntimeError("polly_viseme_voice_not_neural")
        audio = self._synthesize_audio(request, "neural")
        response = self._client_for_request().synthesize_speech(
            Engine="neural",
            OutputFormat="json",
            SpeechMarkTypes=["viseme"],
            Text=request.spoken_text,
            TextType="text",
            VoiceId=request.voice.value,
        )
        stream = response.get("AudioStream")
        if stream is None or not hasattr(stream, "read"):
            raise RuntimeError("polly_speech_marks_missing")
        try:
            content_type = response.get("ContentType")
            if content_type not in (None, "application/x-json-stream"):
                raise RuntimeError("polly_speech_marks_content_type_invalid")
            marks_raw = stream.read(MAX_SPEECH_MARK_BYTES + 1)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not isinstance(marks_raw, bytes):
            raise RuntimeError("polly_speech_marks_invalid")
        if len(marks_raw) > MAX_SPEECH_MARK_BYTES:
            raise RuntimeError("polly_speech_marks_limit")
        return PollySpeechWithVisemes(audio, _parse_viseme_marks(marks_raw))


def _parse_viseme_marks(marks_raw: bytes) -> tuple[PollyVisemeMark, ...]:
    """Parse Polly's newline-delimited speech-mark JSON into bounded marks.

    A malformed line or an undocumented viseme value is dropped rather than
    failing the whole utterance: the audio is already synthesized, and a
    mouth that misses one phoneme beats an avatar that refuses to speak.
    """

    marks: list[PollyVisemeMark] = []
    for line in marks_raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "viseme":
            continue
        time_ms = entry.get("time")
        viseme = entry.get("value")
        if not isinstance(time_ms, int) or not 0 <= time_ms <= 600_000:
            continue
        if viseme not in POLLY_VISEMES:
            continue
        marks.append(PollyVisemeMark(time_ms, viseme))
        if len(marks) >= MAX_VISEME_MARKS:
            break
    return tuple(marks)
