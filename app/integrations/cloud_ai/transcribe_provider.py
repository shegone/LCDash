"""Bounded Amazon Transcribe streaming adapter for push-to-talk speech."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
from typing import Any, Protocol

from .contracts import PushToTalkAudioFormat, TranscribePushToTalkRequest


APPROVED_REGION = "us-east-1"
# Amazon Transcribe streaming rejects audio events larger than 32 KB.
MAX_AUDIO_CHUNK_BYTES = 16_384
# Amazon Transcribe streaming accepts pcm, ogg-opus, and flac only. WebM Opus,
# the browser MediaRecorder default, has no streaming encoding and fails closed.
MEDIA_ENCODINGS = {
    PushToTalkAudioFormat.PCM: "pcm",
    PushToTalkAudioFormat.OGG_OPUS: "ogg-opus",
}


class TranscribeStreamingClient(Protocol):
    async def start_stream_transcription(self, **kwargs: Any) -> Any: ...


def build_transcribe_streaming_client() -> TranscribeStreamingClient:
    """Create the regional streaming client without opening a stream."""
    from amazon_transcribe.client import (
        TranscribeStreamingClient as _TranscribeStreamingClient,
    )

    return _TranscribeStreamingClient(region=APPROVED_REGION)


def _media_encoding(audio_format: PushToTalkAudioFormat) -> str:
    encoding = MEDIA_ENCODINGS.get(audio_format)
    if encoding is None:
        raise RuntimeError("transcribe_media_encoding_unsupported")
    return encoding


def _final_segments(event: Any) -> list[str]:
    """Read one transcript event without retaining or emitting its text."""
    transcript = getattr(event, "transcript", None)
    results = getattr(transcript, "results", None) or ()
    segments: list[str] = []
    for result in results:
        if getattr(result, "is_partial", False):
            continue
        alternatives = getattr(result, "alternatives", None) or ()
        for alternative in alternatives[:1]:
            text = str(getattr(alternative, "transcript", "") or "").strip()
            if text:
                segments.append(text)
    return segments


class AwsTranscribeStreamingProvider:
    """Transcribe one bounded push-to-talk clip with no persistence or tools.

    Amazon Transcribe streaming is an HTTP/2 bidirectional event stream and its
    SDK is asynchronous, but ``CloudTranscribeProvider.transcribe`` is
    synchronous. Each request therefore runs on a dedicated worker thread with
    its own private event loop, so the adapter never re-enters, blocks, or
    deadlocks the caller's event loop.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], TranscribeStreamingClient] = (
            build_transcribe_streaming_client
        ),
        max_transcript_characters: int = 4000,
        max_audio_chunk_bytes: int = MAX_AUDIO_CHUNK_BYTES,
        timeout_seconds: float = 20.0,
        max_concurrent_streams: int = 4,
    ) -> None:
        if not 1 <= max_transcript_characters <= 4000:
            raise ValueError(
                "Transcript limit must be between 1 and 4000 characters."
            )
        if not 1 <= max_audio_chunk_bytes <= 32_768:
            raise ValueError("Audio chunk size must be between 1 and 32768 bytes.")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("Transcribe timeout must be between 1 and 60 seconds.")
        if not 1 <= max_concurrent_streams <= 8:
            raise ValueError("Concurrent stream limit must be between 1 and 8.")
        self._client_factory = client_factory
        self._max_transcript_characters = max_transcript_characters
        self._max_audio_chunk_bytes = max_audio_chunk_bytes
        self._timeout_seconds = float(timeout_seconds)
        self._max_concurrent_streams = max_concurrent_streams
        self._lock = threading.Lock()
        self._client: TranscribeStreamingClient | None = None
        self._workers: ThreadPoolExecutor | None = None

    def _client_for_request(self) -> TranscribeStreamingClient:
        with self._lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    def _workers_for_request(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._workers is None:
                self._workers = ThreadPoolExecutor(
                    max_workers=self._max_concurrent_streams,
                    thread_name_prefix="cloud-transcribe",
                )
            return self._workers

    def transcribe(self, request: TranscribePushToTalkRequest, audio: bytes) -> str:
        encoding = _media_encoding(request.audio_format)
        if not isinstance(audio, bytes) or not audio:
            raise RuntimeError("transcribe_audio_empty")
        future: Future[str] = self._workers_for_request().submit(
            self._run_stream, request, audio, encoding
        )
        try:
            transcript = future.result(timeout=self._timeout_seconds + 5)
        except FutureTimeout as exc:
            future.cancel()
            raise RuntimeError("transcribe_stream_timeout") from exc
        if not transcript:
            raise RuntimeError("transcribe_transcript_empty")
        if len(transcript) > self._max_transcript_characters:
            raise RuntimeError("transcribe_transcript_limit")
        return transcript

    def _run_stream(
        self,
        request: TranscribePushToTalkRequest,
        audio: bytes,
        encoding: str,
    ) -> str:
        """Own a private event loop for the lifetime of this one stream."""
        return asyncio.run(self._stream_transcript(request, audio, encoding))

    async def _stream_transcript(
        self,
        request: TranscribePushToTalkRequest,
        audio: bytes,
        encoding: str,
    ) -> str:
        try:
            return await asyncio.wait_for(
                self._collect_transcript(request, audio, encoding),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("transcribe_stream_timeout") from exc

    async def _collect_transcript(
        self,
        request: TranscribePushToTalkRequest,
        audio: bytes,
        encoding: str,
    ) -> str:
        client = self._client_for_request()
        stream = await client.start_stream_transcription(
            language_code=request.language_code,
            media_sample_rate_hz=request.sample_rate_hz,
            media_encoding=encoding,
        )
        writer = asyncio.ensure_future(self._send_audio(stream, audio))
        # Retrieve any writer failure so a cancelled send never warns at exit.
        writer.add_done_callback(lambda task: task.cancelled() or task.exception())
        try:
            segments: list[str] = []
            length = 0
            async for event in stream.output_stream:
                for segment in _final_segments(event):
                    length += len(segment) + 1
                    if length > self._max_transcript_characters:
                        raise RuntimeError("transcribe_transcript_limit")
                    segments.append(segment)
            await writer
            return " ".join(segments).strip()
        finally:
            writer.cancel()
            self._release_stream(stream)

    async def _send_audio(self, stream: Any, audio: bytes) -> None:
        step = self._max_audio_chunk_bytes
        for start in range(0, len(audio), step):
            await stream.input_stream.send_audio_event(
                audio_chunk=audio[start : start + step]
            )
        await stream.input_stream.end_stream()

    @staticmethod
    def _release_stream(stream: Any) -> None:
        """Close the request body so a failed stream cannot stay open."""
        body = getattr(getattr(stream, "input_stream", None), "_input_stream", None)
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
