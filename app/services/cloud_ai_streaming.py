"""Incremental cloud advisory generation so speech can start on sentence one.

The existing whole-answer path in ``app.services.cloud_ai_service`` is left
untouched. This module adds a parallel streaming path built on the Bedrock
``converse_stream`` API, plus the sentence chunking and speech sanitisation the
browser needs to synthesise audio one sentence at a time.

Nothing here performs a network call at import time; the Bedrock client is
created lazily on the first user request, mirroring ``LazyBedrockConverseClient``.
No audio, transcript, question, or retrieved passage text is persisted or logged.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from functools import cached_property
from typing import Any, Protocol
import json
import re

import boto3

from app.integrations.cloud_ai import (
    AdvisoryRagRequest,
    AdvisoryRagResponse,
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    PollySpeechRequest,
    PollyVoice,
    voice_for_persona,
)
from app.integrations.cloud_ai.provider_config import CloudAiMode
from app.integrations.cloud_ai.bedrock_retrieval import (
    ApprovedBedrockRetriever,
    DailyRequestBudget,
)
from app.services.voice_service import spoken_24_hour_time


# Polly is contractually bounded to 1-3000 characters per request. Stay under
# that so the time expansion in ``sanitize_spoken_text`` cannot push a chunk
# past the limit.
MAX_SENTENCE_SPEECH_CHARACTERS = 2600
# First chunk goes out as soon as it is complete; later chunks are grouped so
# the cadence stays natural and the Polly call count stays bounded.
DEFAULT_GROUP_TARGET_CHARACTERS = 180
MAX_STREAM_CHUNKS = 80

_STREAM_ERROR_KEYS = (
    "internalServerException",
    "modelStreamErrorException",
    "validationException",
    "throttlingException",
    "serviceUnavailableException",
)

_ADVISORY_SYSTEM_PROMPT = (
    "You are MAE/JACK, an advisory public-safety documentation assistant. "
    "Answer naturally and concisely using only the approved excerpts. "
    "Lead with a brief direct answer and offer more detail on request. "
    "Never invent facts, perform actions, or include citations, source labels, "
    "URLs, or a Sources section in the answer. If support is insufficient, say so."
)
_DETAIL_REQUEST = re.compile(
    r"\b(more detail|more detailed|explain fully|in depth|step[- ]by[- ]step)\b",
    flags=re.IGNORECASE,
)


class CloudStreamingUnavailable(RuntimeError):
    """Sanitized fail-closed category; never carries a provider payload."""


class BedrockConverseStreamClient(Protocol):
    def converse_stream(self, **kwargs: Any) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Sentence chunking
# ---------------------------------------------------------------------------

_TERMINATORS = ".!?"
_CLOSERS = "\"')]}»”’"

# Lower-case, dot stripped. A trailing period on one of these is treated as
# part of the token rather than the end of a sentence.
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "rev", "gen", "adm", "col", "maj",
        "sgt", "lt", "capt", "cpt", "cmdr", "ofc", "det", "dep", "supt", "insp",
        "st", "ave", "rd", "blvd", "hwy", "ln", "ct", "apt", "ste", "bldg",
        "sta", "stn", "rm", "unit", "bat", "eng", "med", "sq",
        "no", "nos", "dept", "div", "est", "approx", "fig", "figs", "sec",
        "secs", "art", "ch", "chap", "p", "pp", "vol", "eds", "attn", "ref",
        "inc", "corp", "co", "ltd", "llc", "assn", "govt", "ext",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
        "nov", "dec",
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
        "vs", "etc", "al", "cf", "viz", "resp", "e.g", "i.e", "a.m", "p.m",
        "u.s", "u.s.a", "u.k",
    }
)
_LIST_MARKER_LINE = re.compile(r"\s*(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,4})\Z")
_DOTTED_ACRONYM = re.compile(r"(?:[a-z]\.)+[a-z]\Z")


def _letter_count(text: str) -> int:
    return sum(1 for char in text if char.isalpha())


def _clean_token(token: str) -> str:
    return token.strip().lower().lstrip("([{\"'“‘")


def _is_abbreviation(token: str) -> bool:
    clean = _clean_token(token)
    if not clean:
        return False
    if clean in _ABBREVIATIONS:
        return True
    if len(clean) == 1 and clean.isalpha():
        return True
    return bool(_DOTTED_ACRONYM.fullmatch(clean))


def _is_soft_stop(buffer: str, dot_index: int, end: int) -> bool:
    """True when a period is part of a token rather than a sentence end."""
    prefix = buffer[:dot_index]
    tokens = prefix.split()
    token = tokens[-1] if tokens else ""
    clean = _clean_token(token)
    if _is_abbreviation(token):
        return True
    line = prefix.rsplit("\n", 1)[-1]
    if token and _LIST_MARKER_LINE.fullmatch(line):
        # "1. Check the valve" / "a. Close the main" list markers.
        return True
    tail = buffer[end:].lstrip()
    if not tail:
        return False
    if tail[0].islower():
        # A lower-case continuation almost always means the period belonged to
        # an abbreviation this list does not know about.
        return True
    if tail[0].isdigit() and clean.isalpha() and len(clean) <= 4:
        # "Sta. 3", "Rm. 12", "Ch. 5" - a short word followed by a number.
        return True
    return False


def _boundary_index(buffer: str, *, final: bool) -> int | None:
    """Index just past a confirmed sentence end, or None while still growing."""
    length = len(buffer)
    index = 0
    while index < length:
        char = buffer[index]
        if char == "\n":
            return index + 1
        if char in _TERMINATORS:
            end = index + 1
            while end < length and buffer[end] in _TERMINATORS:
                end += 1
            while end < length and buffer[end] in _CLOSERS:
                end += 1
            if end >= length:
                # A decimal such as "3.5" or a mid-token dot cannot be ruled out
                # until the next character arrives.
                return length if final else None
            if not buffer[end].isspace():
                index = end
                continue
            if char == "." and _is_soft_stop(buffer, index, end):
                index = end
                continue
            return end
        index += 1
    return length if (final and buffer.strip()) else None


def _split_oversized(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind(" ")
        if cut < limit // 4:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return [part for part in parts if part]


class SentenceChunker:
    """Turn streamed deltas into complete, speakable chunks.

    ``feed`` never returns a chunk until the sentence end is confirmed by a
    following whitespace character, so a chunk can never be cut mid-word,
    mid-decimal, or mid-abbreviation. ``flush`` releases whatever is left.
    """

    def __init__(
        self,
        *,
        group_target_chars: int = DEFAULT_GROUP_TARGET_CHARACTERS,
        max_chunk_chars: int = MAX_SENTENCE_SPEECH_CHARACTERS,
        first_chunk_min_chars: int = 0,
    ) -> None:
        if group_target_chars < 1 or max_chunk_chars < 32:
            raise ValueError("Sentence chunking bounds are invalid.")
        self._group_target = group_target_chars
        self._max_chunk = max_chunk_chars
        self._first_min = max(0, first_chunk_min_chars)
        self._buffer = ""
        self._carry = ""
        self._group = ""
        self._emitted = 0

    def feed(self, text: str) -> list[str]:
        return self._process(str(text or ""), final=False)

    def flush(self) -> list[str]:
        return self._process("", final=True)

    def _process(self, text: str, *, final: bool) -> list[str]:
        self._buffer += text
        if final and self._carry:
            self._buffer = f"{self._carry} {self._buffer}".strip()
            self._carry = ""
        sentences: list[str] = []
        while self._buffer:
            index = _boundary_index(self._buffer, final=final)
            if index is None:
                break
            piece = self._buffer[:index].strip()
            self._buffer = self._buffer[index:].lstrip()
            if not piece:
                continue
            if self._carry:
                piece = f"{self._carry} {piece}".strip()
                self._carry = ""
            if _letter_count(piece) < 2 and not final:
                # A bare list marker or stray punctuation line; hold it back so
                # it is spoken with the text that follows.
                self._carry = piece
                continue
            sentences.append(piece)
        return self._drain(sentences, final=final)

    def _drain(self, sentences: Iterable[str], *, final: bool) -> list[str]:
        ready: list[str] = []
        for sentence in sentences:
            if self._emitted == 0 and not self._group and len(sentence) >= self._first_min:
                ready.append(sentence)
                self._emitted += 1
                continue
            self._group = f"{self._group} {sentence}".strip()
            if len(self._group) >= self._group_target:
                ready.append(self._group)
                self._group = ""
                self._emitted += 1
        if final and self._group:
            ready.append(self._group)
            self._group = ""
            self._emitted += 1
        chunks: list[str] = []
        for item in ready:
            chunks.extend(_split_oversized(item, self._max_chunk))
        return chunks


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

_SOURCES_HEADING = re.compile(r"^\s*sources\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_SCHEME_URL = re.compile(r"\b(?:https?|s3|ftp|file)://\S+", re.IGNORECASE)
_BARE_HOST = re.compile(
    r"\b(?:www\.\S+|[\w-]+\.(?:com|org|net|gov|edu|io|us|mil))\b", re.IGNORECASE
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PAGE_BRACKET = re.compile(r"\[[^\]]*?(?:page|p\.)\s*\d+[^\]]*?\]", re.IGNORECASE)
_NUMERIC_BRACKET = re.compile(r"\[\s*\d+(?:\s*[,-]\s*\d+)*\s*\]")
_SOURCE_PAREN = re.compile(r"\((?:source|citation|see)\b[^)]*\)", re.IGNORECASE)
_MARKDOWN_MARKS = re.compile(r"[*_`#>|]+")
_BULLET_PREFIX = re.compile(r"^\s*[-•–]\s+", re.MULTILINE)
# A compact or colon-separated 24-hour time (e.g. "13:40") is ambiguous to a
# TTS engine -- expand it to unambiguous speech ("thirteen forty") before
# Polly ever sees it, reusing the same wording on-prem's voice pipeline uses.
_PREFIXED_TIME_PATTERN = re.compile(
    r"\b(?P<prefix>(?:dispatch\s+)?time(?:\s+(?:is|was))?\s*[:]?|at)\s+"
    r"(?P<hour>[01]\d|2[0-3])(?::?)(?P<minute>[0-5]\d)\b",
    flags=re.IGNORECASE,
)
_COLON_TIME_PATTERN = re.compile(r"\b(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)\b")
_WHITESPACE = re.compile(r"\s+")


def clean_display_text(text: str) -> str:
    """Match the whole-answer path: no URLs and no trailing Sources block."""
    cleaned = _SCHEME_URL.sub("", str(text or ""))
    return _SOURCES_HEADING.split(cleaned, maxsplit=1)[0].strip()


def is_sources_heading(text: str) -> bool:
    return bool(_SOURCES_HEADING.fullmatch(str(text or "").strip()))


def _feedable_limit(text: str, *, final: bool = False) -> tuple[int, bool]:
    """How much accumulated text is safe to chunk, and whether Sources began.

    A trailing partial line that could still turn into a ``Sources`` heading is
    withheld, so a source list can never be handed to the speech queue.
    """
    match = _SOURCES_HEADING.search(text)
    if match:
        return match.start(), True
    if final:
        return len(text), False
    index = text.rfind("\n")
    if index < 0:
        return len(text), False
    candidate = text[index:].strip().rstrip(":").lower()
    if "sources".startswith(candidate):
        return index, False
    return len(text), False


def sanitize_spoken_text(text: str) -> str:
    """Everything Polly is ever asked to say goes through this first.

    Source URLs, S3 URIs, bare hostnames, e-mail addresses, bracketed citation
    markers, and any Sources block are removed. This is a firm project rule:
    Polly must never speak a source URL.
    """
    spoken = _SOURCES_HEADING.split(str(text or ""), maxsplit=1)[0]
    spoken = _SCHEME_URL.sub(" ", spoken)
    spoken = _EMAIL.sub(" ", spoken)
    spoken = _BARE_HOST.sub(" ", spoken)
    spoken = _PAGE_BRACKET.sub(" ", spoken)
    spoken = _NUMERIC_BRACKET.sub(" ", spoken)
    spoken = _SOURCE_PAREN.sub(" ", spoken)
    spoken = _BULLET_PREFIX.sub("", spoken)
    spoken = _MARKDOWN_MARKS.sub(" ", spoken)
    spoken = _WHITESPACE.sub(" ", spoken).strip()
    spoken = _PREFIXED_TIME_PATTERN.sub(
        lambda match: f"{match.group('prefix')} "
        f"{spoken_24_hour_time(int(match.group('hour')), int(match.group('minute')))}",
        spoken,
    )
    spoken = _COLON_TIME_PATTERN.sub(
        lambda match: spoken_24_hour_time(
            int(match.group("hour")), int(match.group("minute"))
        ),
        spoken,
    )
    if len(spoken) > MAX_SENTENCE_SPEECH_CHARACTERS:
        spoken = spoken[:MAX_SENTENCE_SPEECH_CHARACTERS]
        cut = spoken.rfind(" ")
        if cut > MAX_SENTENCE_SPEECH_CHARACTERS // 2:
            spoken = spoken[:cut]
    return spoken.strip()


def split_answer_for_speech(
    text: str, *, group_target_chars: int = DEFAULT_GROUP_TARGET_CHARACTERS
) -> list[str]:
    """Chunk an already-complete answer, for the read-aloud path."""
    chunker = SentenceChunker(group_target_chars=group_target_chars)
    chunks = chunker.feed(str(text or ""))
    chunks.extend(chunker.flush())
    return [chunk for chunk in chunks if chunk]


# ---------------------------------------------------------------------------
# Bedrock streaming
# ---------------------------------------------------------------------------


class LazyBedrockConverseStreamClient:
    """Create the Bedrock Runtime client only on the first streamed request."""

    def __init__(self, *, region_name: str = "us-east-1") -> None:
        self._region_name = region_name

    @cached_property
    def _client(self):
        return boto3.client("bedrock-runtime", region_name=self._region_name)

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.converse_stream(**kwargs)


def iter_converse_stream_text(response: dict[str, Any]) -> Iterator[str]:
    """Yield raw text deltas from a ``converse_stream`` response."""
    stream = response.get("stream")
    if stream is None:
        return
    for event in stream:
        if not isinstance(event, dict):
            continue
        for key in _STREAM_ERROR_KEYS:
            if key in event:
                raise CloudStreamingUnavailable("advisory_stream_provider_error")
        delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
        text = delta.get("text")
        if text:
            yield str(text)


class _LazyBedrockRetrieveClient:
    """Local twin of the service-layer lazy retrieve client."""

    def __init__(self, *, region_name: str = "us-east-1") -> None:
        self._region_name = region_name

    @cached_property
    def _client(self):
        return boto3.client("bedrock-agent-runtime", region_name=self._region_name)

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.retrieve(**kwargs)


class StreamingGroundedAdvisory:
    """Retrieve approved passages, then stream bounded generated sentences.

    Mirrors ``GroundedBedrockAdvisory`` prompt, bounds, and daily cap so a
    streamed answer is the same answer the whole-answer path would produce.
    """

    def __init__(
        self,
        retriever: ApprovedBedrockRetriever,
        *,
        converse_client: BedrockConverseStreamClient,
        model_id: str,
        max_output_tokens: int = 400,
        detail_max_output_tokens: int = 1200,
        daily_request_limit: int = 200,
        group_target_chars: int = DEFAULT_GROUP_TARGET_CHARACTERS,
        budget: DailyRequestBudget | None = None,
    ) -> None:
        if not 64 <= max_output_tokens <= 400:
            raise ValueError("Streamed generation requires a 64-400 default token cap.")
        if not max_output_tokens <= detail_max_output_tokens <= 1200:
            raise ValueError("Detailed streamed generation is capped at 1200 tokens.")
        if not 1 <= daily_request_limit <= 200:
            raise ValueError("Streamed generation requires a 1-200 daily request cap.")
        self._retriever = retriever
        self._client = converse_client
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._detail_max_output_tokens = detail_max_output_tokens
        self._group_target_chars = group_target_chars
        # A shared budget keeps the streamed and whole-answer paths drawing
        # from one daily cap instead of two independent 200-request limits.
        self._budget = budget or DailyRequestBudget(daily_request_limit)

    def _reserve_request(self) -> bool:
        return self._budget.reserve()

    def stream(self, request: AdvisoryRagRequest) -> Iterator[dict[str, Any]]:
        yield {"type": "status", "stage": "retrieving"}
        passages = self._retriever.retrieve(
            tenant_id=request.tenant_id,
            persona=request.persona,
            roles=request.roles,
            question=request.question,
        )
        if not passages:
            yield {"type": "denied", "reason": "No approved source supports this request."}
            return
        if not self._reserve_request():
            yield {
                "type": "denied",
                "reason": "The daily advisory usage limit has been reached.",
            }
            return
        citations = tuple(passage.citation for passage in passages)
        context = "\n\n".join(
            f"Approved excerpt {index}:\n{passage.text}"
            for index, passage in enumerate(passages, start=1)
        )[:12000]
        wants_detail = bool(_DETAIL_REQUEST.search(request.question))
        yield {"type": "status", "stage": "generating"}

        chunker = SentenceChunker(group_target_chars=self._group_target_chars)
        collected = ""
        fed = 0
        emitted = 0
        sources_found = False
        halted = False

        def _pending(pieces: list[str]) -> Iterator[dict[str, Any]]:
            nonlocal emitted, halted
            for piece in pieces:
                if halted or emitted >= MAX_STREAM_CHUNKS:
                    return
                if is_sources_heading(piece):
                    halted = True
                    return
                display = clean_display_text(piece)
                speech = sanitize_spoken_text(piece)
                if not display and not speech:
                    continue
                yield {
                    "type": "chunk",
                    "index": emitted,
                    "text": display,
                    "speech": speech,
                }
                emitted += 1

        try:
            response = self._client.converse_stream(
                modelId=self._model_id,
                system=[{"text": _ADVISORY_SYSTEM_PROMPT}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": (
                                    f"Persona: {request.persona.upper()}\n"
                                    f"Question: {request.question}\n\n{context}"
                                )
                            }
                        ],
                    }
                ],
                inferenceConfig={
                    "maxTokens": (
                        self._detail_max_output_tokens
                        if wants_detail
                        else self._max_output_tokens
                    ),
                    "temperature": 0.1,
                    "topP": 0.9,
                },
            )
            for delta in iter_converse_stream_text(response):
                collected += delta
                if sources_found:
                    continue
                limit, sources_found = _feedable_limit(collected)
                if limit > fed:
                    yield from _pending(chunker.feed(collected[fed:limit]))
                    fed = limit
            if not sources_found:
                limit, sources_found = _feedable_limit(collected, final=True)
                if limit > fed:
                    yield from _pending(chunker.feed(collected[fed:limit]))
                    fed = limit
            yield from _pending(chunker.flush())
        except CloudStreamingUnavailable:
            raise
        except Exception as exc:  # provider payloads never leave this frame
            raise CloudStreamingUnavailable("advisory_stream_failed") from exc

        answer = clean_display_text(collected)[:6000].strip()
        if not answer:
            yield {"type": "denied", "reason": "The grounded advisory response was empty."}
            return
        yield {"type": "answer", "answer": answer, "citations": citations}


# ---------------------------------------------------------------------------
# Wiring and the main-thread facing API
# ---------------------------------------------------------------------------


def build_cloud_advisory_streamer(
    config: CloudAiProviderConfig,
    *,
    region_name: str = "us-east-1",
    budget: DailyRequestBudget | None = None,
) -> StreamingGroundedAdvisory | None:
    """Return a streamer, or None when streaming is not available.

    Constructing this performs no provider call, so it is safe at import time.
    Pass the same ``budget`` given to the whole-answer
    ``GroundedBedrockAdvisory`` so both paths draw from one daily cap.
    """
    if config.mode is not CloudAiMode.ADVISORY_RAG or not config.documents_ingested:
        return None
    retriever = ApprovedBedrockRetriever(
        client=_LazyBedrockRetrieveClient(region_name=region_name),
        knowledge_base_id=config.knowledge_base_id,
        tenant_id=config.tenant_id,
        allowed_s3_prefixes=config.allowed_s3_prefixes,
        result_limit=config.retrieval_result_limit,
        score_threshold=config.retrieval_score_threshold,
        metadata_filtering_enabled=False,
    )
    return StreamingGroundedAdvisory(
        retriever,
        converse_client=LazyBedrockConverseStreamClient(region_name=region_name),
        model_id=config.generation_model_id,
        max_output_tokens=min(config.max_output_tokens, 400),
        detail_max_output_tokens=1200,
        daily_request_limit=200,
        budget=budget,
    )


def _denied_payload(request_id: str, reason: str) -> dict[str, Any]:
    clean_reason = str(reason or "").strip() or "No approved source supports this request."
    response = AdvisoryRagResponse.deny(request_id, clean_reason)
    return {
        "request_id": response.request_id,
        "answer": "",
        "citations": [],
        "denied": True,
        "denial_reason": response.denial_reason,
        "advisory_only": True,
        "action_executed": False,
    }


def _supported_payload(
    request_id: str, answer: str, citations: tuple[Any, ...]
) -> dict[str, Any]:
    try:
        response = AdvisoryRagResponse.supported(request_id, answer, citations)
    except ValueError:
        return _denied_payload(
            request_id, "No approved cited source supports this request."
        )
    return {
        "request_id": response.request_id,
        "answer": response.answer,
        "citations": [
            {
                "source_uri": citation.source_uri,
                "title": citation.title,
                "page": citation.page,
                "section": citation.section,
                "revision": citation.revision,
            }
            for citation in response.citations
        ],
        "denied": False,
        "denial_reason": "",
        "advisory_only": True,
        "action_executed": False,
    }


def stream_cloud_advisory(
    streamer: StreamingGroundedAdvisory | None,
    config: CloudAiProviderConfig,
    *,
    request_id: str,
    question: str,
    persona: str = "mae",
    roles: tuple[str, ...] = ("viewer",),
) -> Iterator[dict[str, Any]]:
    """Yield NDJSON-ready advisory events, terminating with ``done``.

    Event types:
      ``status``   {"stage": "retrieving"|"generating"}
      ``chunk``    {"index": int, "text": display, "speech": sanitized-for-Polly}
      ``complete`` {"payload": <same shape as answer_cloud_advisory()>}
      ``error``    {"detail": sanitized message}
      ``done``     terminal marker

    Never raises: every failure is reported as an ``error`` event so the client
    can fall back to the whole-answer path.
    """
    clean_question = str(question or "").strip()
    try:
        if streamer is None:
            yield {"type": "error", "detail": "Streaming advisory generation is unavailable."}
            return
        if config.mode is not CloudAiMode.ADVISORY_RAG or not config.documents_ingested:
            yield {
                "type": "complete",
                "payload": _denied_payload(
                    request_id, "Approved documents are not ingested."
                ),
            }
            return
        request = AdvisoryRagRequest(
            request_id,
            config.tenant_id,
            clean_question,
            persona=persona,
            roles=roles,
            allowed_tools=(),
        )
        for event in streamer.stream(request):
            kind = event.get("type")
            if kind in {"status", "chunk"}:
                yield event
            elif kind == "denied":
                yield {
                    "type": "complete",
                    "payload": _denied_payload(request_id, str(event.get("reason") or "")),
                }
                return
            elif kind == "answer":
                yield {
                    "type": "complete",
                    "payload": _supported_payload(
                        request_id,
                        str(event.get("answer") or ""),
                        tuple(event.get("citations") or ()),
                    ),
                }
                return
        yield {"type": "error", "detail": "The advisory stream ended before an answer."}
    except (CloudStreamingUnavailable, CloudAiRuntimeUnavailable):
        yield {"type": "error", "detail": "The advisory stream is unavailable."}
    except ValueError:
        yield {"type": "error", "detail": "The advisory request was rejected."}
    except Exception:  # sanitized: provider details never reach the browser
        yield {"type": "error", "detail": "The advisory stream could not be completed."}


def iter_advisory_ndjson(events: Iterable[dict[str, Any]]) -> Iterator[str]:
    """Serialize advisory events as newline-delimited JSON, ending with done."""
    terminated = False
    for event in events:
        yield json.dumps(event, separators=(",", ":")) + "\n"
        if event.get("type") in {"complete", "error"}:
            terminated = True
    if not terminated:
        yield json.dumps({"type": "error", "detail": "The advisory stream ended early."},
                         separators=(",", ":")) + "\n"
    yield json.dumps({"type": "done"}, separators=(",", ":")) + "\n"


def synthesize_cloud_sentence(
    runtime: CloudAiRuntime,
    config: CloudAiProviderConfig,
    *,
    request_id: str,
    text: str,
    voice: str = "",
    persona: str = "mae",
) -> bytes:
    """Synthesize one sanitized sentence. Source URLs can never reach Polly.

    JACK is pinned to its own voice (see ``voice_for_persona``) regardless of
    what ``voice`` a caller supplies, so a stale or mistaken client value can
    never make JACK speak in MAE's voice. Every other persona keeps the prior
    behavior: an explicit ``voice`` wins, otherwise the configured default.
    """
    spoken = sanitize_spoken_text(text)
    if not spoken:
        raise CloudAiRuntimeUnavailable("empty_speech_text")
    default_voice = voice_for_persona(config, persona)
    if persona == "jack":
        selected_voice = default_voice
    else:
        try:
            selected_voice = PollyVoice(voice) if voice else default_voice
        except ValueError as exc:
            raise CloudAiRuntimeUnavailable("polly_voice_not_allowed") from exc
    return runtime.synthesize(
        PollySpeechRequest(request_id, config.tenant_id, spoken, selected_voice)
    )
