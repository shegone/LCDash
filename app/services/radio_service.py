"""Mindshare Radio Intelligence: signed ingest, storage, reads, and corrections.

Contract: docs/MINDSHARE_RADIO_INGEST.md (v1) and
docs/MINDSHARE_RADIO_CORRECTIONS.md (v2: human corrections kept for a year,
admin find-and-replace fixes, keyword alerts, access for every human role).
The listener on the Hermes host
captures a finished radio segment, transcribes it locally, and POSTs the WAV
plus a metadata document, signed with an Ed25519 private key that exists only
on that host. This module holds everything that is not HTTP plumbing:

- the pure checks (signature, timestamp, metadata schema, WAV header,
  low-confidence rule), each raising ``RadioIngestError`` with a short,
  transcript-free ``detail``;
- ``RadioRepository``, raw psycopg3 SQL against the ``lcdash_radio`` schema;
- ``RadioService``, which orchestrates S3 + Postgres for ingest, the reads
  (list, audio, stats, alerts), corrections, fixes and watch terms, and
  records the audit trail. Every write is audited in the same transaction and
  fails closed: no audit record, no change.

This feature is inquiry-only. Nothing here writes to CAD, radio, paging, or any
operational output, and transcripts are machine generated and unverified.

Nothing in AWS holds a secret for this path: the app is configured only with
the base64 raw public key (``settings.radio_ingest_public_key``).

v3 (docs/MINDSHARE_RADIO_HEALTH.md) adds the signed listener heartbeat (same
key, a different signed-message prefix), the one-row ``listener_status`` it
upserts, the ``listener`` block of ``stats()``, and the admin-managed hint
words that, with the watch phrases and fixes, form the glossary returned in
the heartbeat reply.

v4 (docs/MINDSHARE_RADIO_UNITS.md) adds who is talking: the optional ingest
``signal`` object (radio ID, talkgroup, console label read from the radio
signalling by the listener), the admin-managed ``units`` table that names a
radio ID, the ``radio_id`` list filter, and the radio ID / unit name search.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import struct
import sys
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config.settings import settings
from app.services import radio_text
from app.services.radio_text import FixRule, FixSet, TextRuleError

logger = logging.getLogger(__name__)

# The one log line the silent-listener alarm counts. The application does not
# configure the root logger (uvicorn only configures its own), so an INFO
# record from ``logger`` above would be dropped before it reached the web log
# group. This dedicated logger writes the bare message to stdout, where the
# container log driver picks it up, and does not propagate (no duplicates if a
# root handler is ever added). Idempotent across reloads.
HEARTBEAT_LOGGER_NAME = "lcdash.radio.heartbeat"
HEARTBEAT_LOG_PREFIX = "radio-heartbeat ok"


class _StdoutHandler(logging.StreamHandler):
    """A stream handler that always writes to the *current* ``sys.stdout``."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def _heartbeat_logger() -> logging.Logger:
    """The heartbeat logger, with its stdout handler attached exactly once."""
    heartbeat_log = logging.getLogger(HEARTBEAT_LOGGER_NAME)
    if not any(isinstance(item, _StdoutHandler) for item in heartbeat_log.handlers):
        heartbeat_log.setLevel(logging.INFO)
        heartbeat_log.propagate = False
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        heartbeat_log.addHandler(handler)
    return heartbeat_log


_heartbeat_logger()  # configured at import so the first heartbeat is never lost


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "radio_schema.sql"

# ------------------------------------------------------------------ contract

SIGNATURE_DOMAIN = "lcdash-radio-v1"
MAX_CLOCK_SKEW_SECONDS = 300
MAX_METADATA_BYTES = 64 * 1024
MAX_AUDIO_BYTES = 4 * 1024 * 1024
# Two parts plus multipart boundaries and part headers.
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + MAX_METADATA_BYTES + 16 * 1024

SEGMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")
CHANNEL_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
DIRECTIONS = ("tx", "rx")
MAX_SEGMENT_SPAN_SECONDS = 600
MAX_TRANSCRIPT_CHARS = 20000
MAX_LANGUAGE_CHARS = 8
MAX_MODEL_CHARS = 64
MAX_ASR_SEGMENTS = 500

LOW_CONFIDENCE_LOGPROB = -1.0
LOW_CONFIDENCE_NO_SPEECH = 0.6

AUDIO_RETENTION_DAYS = 15
TRANSCRIPT_RETENTION_DAYS = 30
# A segment a person corrected keeps its audio and corrected transcript this
# long, extended on every edit.
CORRECTED_RETENTION_DAYS = 365
DEFAULT_CORRECTED_PREFIX = "radio/corrected/"
FIX_CACHE_SECONDS = 60
ALERT_WINDOW_HOURS = 24
MAX_ALERTS = 50
SNIPPET_CHARS = 300
PURGE_INTERVAL_SECONDS = 3600
STATS_WINDOW_HOURS = 24

# v3: listener heartbeat (docs/MINDSHARE_RADIO_HEALTH.md).
HEARTBEAT_SIGNATURE_DOMAIN = "lcdash-radio-heartbeat-v1"
MAX_HEARTBEAT_BYTES = 16 * 1024
MAX_HEARTBEAT_LISTENERS = 64
MAX_OTHER_PAYLOAD_TYPES = 16
MAX_GLOSSARY_VERSION_CHARS = 32
MAX_HEARTBEAT_COUNTER = 10**15
MAX_RTP_PAYLOAD_TYPE = 127
# The listener counts as reporting while its last heartbeat is this recent
# (three missed 60-second beats).
LISTENER_REPORTING_SECONDS = 180

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
MAX_QUERY_CHARS = 200

EVENT_LIST = "radio.segments.list"
EVENT_LISTEN = "radio.segment.listen"
EVENT_CORRECT = "radio.segment.correct"
EVENT_CORRECT_REVERT = "radio.segment.correct_revert"
EVENT_FIX_ADD = "radio.fix.add"
EVENT_FIX_REMOVE = "radio.fix.remove"
EVENT_WATCH_ADD = "radio.watch.add"
EVENT_WATCH_CHANGE = "radio.watch.change"
EVENT_WATCH_REMOVE = "radio.watch.remove"
EVENT_ALERT_DISMISS = "radio.alert.dismiss"
EVENT_HINT_ADD = "radio.hint.add"
EVENT_HINT_REMOVE = "radio.hint.remove"
EVENT_UNIT_SET = "radio.unit.set"
EVENT_UNIT_REMOVE = "radio.unit.remove"

# v4: who is talking (docs/MINDSHARE_RADIO_UNITS.md).
RADIO_ID_PATTERN = re.compile(r"[0-9]{1,10}")
TALKGROUP_PATTERN = re.compile(r"G[0-9]{1,6}")
CONSOLE_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}")
MAX_UNITS = 2000
MIN_UNIT_NAME_CHARS = 1
MAX_UNIT_NAME_CHARS = 60
MAX_SEEN_UNITS = 500
UNIT_ACTIVITY_DAYS = 30
UNIT_CACHE_SECONDS = 60

_METADATA_FIELDS = frozenset({
    "schema_version", "segment_id", "channel", "direction", "started_at",
    "ended_at", "codec", "payload_type", "sample_rate_hz", "pcmu_packet_count",
    "transcript", "language", "model", "transcribed_at", "segments", "signal",
})
# ``signal`` is the one optional field (v4); every other field is required.
_METADATA_REQUIRED = _METADATA_FIELDS - {"signal"}
_SIGNAL_PATTERNS = {
    "radio_id": RADIO_ID_PATTERN,
    "talkgroup": TALKGROUP_PATTERN,
    "console_label": CONSOLE_LABEL_PATTERN,
}
_ASR_SEGMENT_FIELDS = frozenset({
    "start", "end", "text", "avg_logprob", "no_speech_prob",
})

# ------------------------------------------------------------------- errors


class RadioServiceError(Exception):
    """Radio storage is unavailable (database or object store); retryable."""


class RadioDatabaseError(RadioServiceError):
    """The radio Postgres schema could not be reached or a statement failed."""


class RadioStorageError(RadioServiceError):
    """The audio object store could not be reached or refused an operation."""


class RadioConfigError(RadioServiceError):
    """The ingest public key is configured but unusable."""


class RadioIngestError(Exception):
    """A refused ingest, with an HTTP status and a short transcript-free detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RadioQueryError(ValueError):
    """A list request with an invalid filter, cursor, or limit (HTTP 400)."""


class RadioNotFoundError(Exception):
    """The segment, rule, term or alert (or its audio) does not exist (HTTP 404)."""


class RadioConflictError(Exception):
    """A duplicate or over-limit write (HTTP 409). The message is caller-safe."""


# ----------------------------------------------------------- signature check


def build_signed_message(
    timestamp: int, segment_id: str, audio: bytes, metadata: bytes
) -> bytes:
    """The exact bytes the listener signs: five lines joined by one ``\\n``."""
    return "\n".join((
        SIGNATURE_DOMAIN,
        str(int(timestamp)),
        segment_id,
        hashlib.sha256(audio).hexdigest(),
        hashlib.sha256(metadata).hexdigest(),
    )).encode("utf-8")


def load_public_key(public_key_b64: str) -> Ed25519PublicKey:
    """Parse the configured base64 raw 32-byte Ed25519 public key."""
    try:
        raw = base64.b64decode(str(public_key_b64 or "").strip(), validate=True)
        if len(raw) != 32:
            raise ValueError("wrong length")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (binascii.Error, ValueError) as exc:
        raise RadioConfigError("The radio ingest public key is not usable.") from exc


def _unauthorized() -> RadioIngestError:
    return RadioIngestError(401, "Signature verification failed.")


def parse_timestamp(header_value: str | None, *, now: float) -> int:
    """Return the signed timestamp, or raise 401 when absent, odd, or stale."""
    text = str(header_value or "").strip()
    if not re.fullmatch(r"[0-9]{1,12}", text):
        raise _unauthorized()
    timestamp = int(text)
    if abs(now - timestamp) > MAX_CLOCK_SKEW_SECONDS:
        raise _unauthorized()
    return timestamp


def verify_message_signature(
    public_key: Ed25519PublicKey, signature_b64: str | None, message: bytes
) -> None:
    """Raise 401 unless the base64 Ed25519 signature covers exactly ``message``.

    The one check behind both machine routes (segment ingest and heartbeat):
    each builds its own signed message, with its own domain prefix, and hands
    it here.
    """
    try:
        signature = base64.b64decode(str(signature_b64 or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise _unauthorized() from None
    if len(signature) != 64:
        raise _unauthorized()
    try:
        public_key.verify(signature, message)
    except InvalidSignature:
        raise _unauthorized() from None


def verify_signature(
    public_key: Ed25519PublicKey,
    *,
    timestamp: int,
    signature_b64: str | None,
    segment_id: str,
    audio: bytes,
    metadata: bytes,
) -> None:
    """Raise 401 unless the Ed25519 signature covers exactly these bytes."""
    verify_message_signature(
        public_key,
        signature_b64,
        build_signed_message(timestamp, segment_id, audio, metadata),
    )


def build_heartbeat_signed_message(timestamp: int, body: bytes) -> bytes:
    """The exact bytes the listener signs for a heartbeat: three lines.

    ``lcdash-radio-heartbeat-v1``, the timestamp as decimal text, and the
    lowercase sha256 hex of the raw request body, joined by single ``\\n`` with
    no trailing newline.
    """
    return "\n".join((
        HEARTBEAT_SIGNATURE_DOMAIN,
        str(int(timestamp)),
        hashlib.sha256(body).hexdigest(),
    )).encode("utf-8")


def verify_heartbeat_signature(
    public_key: Ed25519PublicKey,
    *,
    timestamp: int,
    signature_b64: str | None,
    body: bytes,
) -> None:
    """Raise 401 unless the signature covers exactly this heartbeat body."""
    verify_message_signature(
        public_key, signature_b64, build_heartbeat_signed_message(timestamp, body)
    )


# ------------------------------------------------------------ metadata check


def _bad_metadata(field: str | None = None) -> RadioIngestError:
    if field:
        return RadioIngestError(400, f"Metadata field '{field}' is invalid.")
    return RadioIngestError(400, "Metadata is invalid.")


def _loads_object(metadata: bytes, *, bad: Callable[..., Exception] = _bad_metadata) -> dict:
    def _reject_constant(_name: str):
        raise ValueError("non-finite number")

    try:
        parsed = json.loads(
            metadata.decode("utf-8"), parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise bad() from None
    if not isinstance(parsed, dict):
        raise bad()
    return parsed


def extract_segment_id(metadata: bytes) -> str:
    """The one metadata field needed before the signature can be checked."""
    segment_id = _loads_object(metadata).get("segment_id")
    if not isinstance(segment_id, str) or not SEGMENT_ID_PATTERN.fullmatch(segment_id):
        raise _bad_metadata("segment_id")
    return segment_id


def _require_int(
    value: Any, field: str, *, minimum: int | None = None,
    maximum: int | None = None, bad: Callable[..., Exception] = _bad_metadata,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise bad(field)
    if minimum is not None and value < minimum:
        raise bad(field)
    if maximum is not None and value > maximum:
        raise bad(field)
    return value


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_metadata(field)
    number = float(value)
    if not math.isfinite(number):
        raise _bad_metadata(field)
    return number


def _require_text(
    value: Any, field: str, *, max_chars: int, bad: Callable[..., Exception] = _bad_metadata
) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise bad(field)
    return value


def _require_datetime(
    value: Any, field: str, *, bad: Callable[..., Exception] = _bad_metadata
) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise bad(field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise bad(field) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise bad(field)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise bad(field) from None


@dataclass(frozen=True)
class RadioSegmentRecord:
    """A fully validated ingest, ready to store."""

    segment_id: str
    channel: str
    direction: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    codec: str
    payload_type: int
    sample_rate_hz: int
    pcmu_packet_count: int
    transcript: str
    language: str
    model: str
    transcribed_at: datetime
    asr_segments: tuple[dict, ...]
    low_confidence: bool
    # v4: read from the radio signalling by the listener; None when not seen.
    radio_id: str | None = None
    talkgroup: str | None = None
    console_label: str | None = None


def compute_low_confidence(asr_segments) -> bool:
    """True when any ASR segment looks unreliable or like non-speech."""
    for segment in asr_segments:
        if float(segment["avg_logprob"]) < LOW_CONFIDENCE_LOGPROB:
            return True
        if float(segment["no_speech_prob"]) > LOW_CONFIDENCE_NO_SPEECH:
            return True
    return False


def validate_signal(value: Any) -> dict[str, str]:
    """The optional ``signal`` object: known keys only, each matching its pattern.

    Unknown keys, a non-object, a non-string value or a value that does not fit
    its pattern are all a 400 that names only the server-chosen field
    ``signal`` (never the sender's key or value). ``fullmatch`` is used so a
    trailing newline cannot slip past ``$``. An empty object means nothing was
    found and is accepted.
    """
    if not isinstance(value, dict) or set(value) - set(_SIGNAL_PATTERNS):
        raise _bad_metadata("signal")
    clean: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not _SIGNAL_PATTERNS[key].fullmatch(item):
            raise _bad_metadata("signal")
        clean[key] = item
    return clean


def validate_metadata(metadata: bytes) -> RadioSegmentRecord:
    """Strictly validate the signed metadata document (schema_version 1)."""
    document = _loads_object(metadata)
    if set(document) - _METADATA_FIELDS:
        # Unknown fields are refused so the schema cannot drift silently.
        # Names are not echoed: they came from the sender.
        raise RadioIngestError(400, "Metadata contains unknown fields.")
    missing = _METADATA_REQUIRED - set(document)
    if missing:
        raise _bad_metadata(sorted(missing)[0])

    if _require_int(document["schema_version"], "schema_version") != 1:
        raise _bad_metadata("schema_version")

    segment_id = document["segment_id"]
    if not isinstance(segment_id, str) or not SEGMENT_ID_PATTERN.fullmatch(segment_id):
        raise _bad_metadata("segment_id")
    channel = document["channel"]
    if not isinstance(channel, str) or not CHANNEL_PATTERN.fullmatch(channel):
        raise _bad_metadata("channel")
    direction = document["direction"]
    if direction not in DIRECTIONS:
        raise _bad_metadata("direction")

    started_at = _require_datetime(document["started_at"], "started_at")
    ended_at = _require_datetime(document["ended_at"], "ended_at")
    span = (ended_at - started_at).total_seconds()
    if span < 0 or span > MAX_SEGMENT_SPAN_SECONDS:
        raise _bad_metadata("ended_at")

    if document["codec"] != "PCMU":
        raise _bad_metadata("codec")
    if _require_int(document["payload_type"], "payload_type") != 0:
        raise _bad_metadata("payload_type")
    if _require_int(document["sample_rate_hz"], "sample_rate_hz") != 8000:
        raise _bad_metadata("sample_rate_hz")
    packet_count = _require_int(
        document["pcmu_packet_count"], "pcmu_packet_count", minimum=0
    )

    transcript = _require_text(
        document["transcript"], "transcript", max_chars=MAX_TRANSCRIPT_CHARS
    )
    language = _require_text(document["language"], "language", max_chars=MAX_LANGUAGE_CHARS)
    model = _require_text(document["model"], "model", max_chars=MAX_MODEL_CHARS)
    transcribed_at = _require_datetime(document["transcribed_at"], "transcribed_at")

    raw_segments = document["segments"]
    if not isinstance(raw_segments, list) or len(raw_segments) > MAX_ASR_SEGMENTS:
        raise _bad_metadata("segments")
    asr_segments: list[dict] = []
    for item in raw_segments:
        if not isinstance(item, dict) or set(item) != _ASR_SEGMENT_FIELDS:
            raise _bad_metadata("segments")
        asr_segments.append({
            "start": _require_number(item["start"], "segments"),
            "end": _require_number(item["end"], "segments"),
            "text": _require_text(item["text"], "segments", max_chars=MAX_TRANSCRIPT_CHARS),
            "avg_logprob": _require_number(item["avg_logprob"], "segments"),
            "no_speech_prob": _require_number(item["no_speech_prob"], "segments"),
        })

    signal = validate_signal(document["signal"]) if "signal" in document else {}

    return RadioSegmentRecord(
        segment_id=segment_id,
        channel=channel,
        direction=direction,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=round(span, 3),
        codec="PCMU",
        payload_type=0,
        sample_rate_hz=8000,
        pcmu_packet_count=packet_count,
        transcript=transcript,
        language=language,
        model=model,
        transcribed_at=transcribed_at,
        asr_segments=tuple(asr_segments),
        low_confidence=compute_low_confidence(asr_segments),
        radio_id=signal.get("radio_id"),
        talkgroup=signal.get("talkgroup"),
        console_label=signal.get("console_label"),
    )


# ------------------------------------------------------- heartbeat (v3) check

_HEARTBEAT_FIELDS = frozenset({
    "schema_version", "sent_at", "capture", "transcriber", "uploader",
    "glossary_version",
})
_HEARTBEAT_REQUIRED = _HEARTBEAT_FIELDS - {"glossary_version"}
_CAPTURE_FIELDS = frozenset({
    "stats_at", "started_at", "uptime_seconds", "interval_seconds", "listeners",
})
_LISTENER_FIELDS = frozenset({
    "channel", "direction", "packets_total", "packets_interval",
    "pcmu_packets_total", "other_payload_types", "bytes_interval",
    "sources_seen", "segments_closed_total", "last_packet_at",
})
_TRANSCRIBER_FIELDS = frozenset({"pending", "failed"})
_UPLOADER_FIELDS = frozenset({
    "queued", "oldest_queued_age_seconds", "rejected_total", "last_success_at",
})
_PAYLOAD_TYPE_KEY = re.compile(r"^(?:0|[1-9][0-9]{0,2})$")


def _bad_heartbeat(field: str | None = None) -> RadioIngestError:
    # ``field`` is always a server-chosen constant, never sender text.
    if field:
        return RadioIngestError(400, f"Heartbeat field '{field}' is invalid.")
    return RadioIngestError(400, "Heartbeat is invalid.")


def _hb_object(value: Any, allowed: frozenset[str], field: str) -> dict:
    """A nested heartbeat object with exactly the known keys (all required)."""
    if not isinstance(value, dict) or set(value) != allowed:
        raise _bad_heartbeat(field)
    return value


def _hb_count(value: Any, field: str) -> int:
    return _require_int(
        value, field, minimum=0, maximum=MAX_HEARTBEAT_COUNTER, bad=_bad_heartbeat
    )


def _hb_optional_count(value: Any, field: str) -> int | None:
    return None if value is None else _hb_count(value, field)


def _hb_optional_time(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _require_datetime(value, field, bad=_bad_heartbeat).isoformat()


def validate_heartbeat(body: bytes) -> dict:
    """Strictly validate a signature-verified heartbeat (schema_version 1).

    Returns the normalized document that is stored and later reported: counts
    are plain non-negative ints, times are UTC ISO strings. Unknown keys at any
    level are refused (names are never echoed), so the schema cannot drift
    silently and nothing but the documented counters can be stored.
    """
    document = _loads_object(body, bad=_bad_heartbeat)
    if set(document) - _HEARTBEAT_FIELDS:
        raise RadioIngestError(400, "Heartbeat contains unknown fields.")
    missing = _HEARTBEAT_REQUIRED - set(document)
    if missing:
        raise _bad_heartbeat(sorted(missing)[0])

    if _require_int(document["schema_version"], "schema_version", bad=_bad_heartbeat) != 1:
        raise _bad_heartbeat("schema_version")
    sent_at = _require_datetime(document["sent_at"], "sent_at", bad=_bad_heartbeat)

    capture = _hb_object(document["capture"], _CAPTURE_FIELDS, "capture")
    raw_listeners = capture["listeners"]
    if not isinstance(raw_listeners, list) or len(raw_listeners) > MAX_HEARTBEAT_LISTENERS:
        raise _bad_heartbeat("capture.listeners")
    listeners: list[dict] = []
    for item in raw_listeners:
        row = _hb_object(item, _LISTENER_FIELDS, "capture.listeners")
        channel = row["channel"]
        if not isinstance(channel, str) or not CHANNEL_PATTERN.fullmatch(channel):
            raise _bad_heartbeat("capture.listeners")
        if row["direction"] not in DIRECTIONS:
            raise _bad_heartbeat("capture.listeners")
        raw_other = row["other_payload_types"]
        if not isinstance(raw_other, dict) or len(raw_other) > MAX_OTHER_PAYLOAD_TYPES:
            raise _bad_heartbeat("capture.listeners")
        other: dict[str, int] = {}
        for key, value in raw_other.items():
            if (
                not isinstance(key, str)
                or not _PAYLOAD_TYPE_KEY.fullmatch(key)
                or int(key) > MAX_RTP_PAYLOAD_TYPE
            ):
                raise _bad_heartbeat("capture.listeners")
            other[key] = _hb_count(value, "capture.listeners")
        listeners.append({
            "channel": channel,
            "direction": row["direction"],
            "packets_total": _hb_count(row["packets_total"], "capture.listeners"),
            "packets_interval": _hb_count(row["packets_interval"], "capture.listeners"),
            "pcmu_packets_total": _hb_count(row["pcmu_packets_total"], "capture.listeners"),
            "other_payload_types": other,
            "bytes_interval": _hb_count(row["bytes_interval"], "capture.listeners"),
            "sources_seen": _hb_count(row["sources_seen"], "capture.listeners"),
            "segments_closed_total": _hb_count(
                row["segments_closed_total"], "capture.listeners"
            ),
            "last_packet_at": _hb_optional_time(row["last_packet_at"], "capture.listeners"),
        })

    transcriber = _hb_object(document["transcriber"], _TRANSCRIBER_FIELDS, "transcriber")
    uploader = _hb_object(document["uploader"], _UPLOADER_FIELDS, "uploader")

    glossary_version = document.get("glossary_version")
    if glossary_version is not None:
        glossary_version = _require_text(
            glossary_version, "glossary_version",
            max_chars=MAX_GLOSSARY_VERSION_CHARS, bad=_bad_heartbeat,
        )

    return {
        "schema_version": 1,
        "sent_at": sent_at.isoformat(),
        "capture": {
            "stats_at": _hb_optional_time(capture["stats_at"], "capture.stats_at"),
            "started_at": _hb_optional_time(capture["started_at"], "capture.started_at"),
            "uptime_seconds": _hb_count(capture["uptime_seconds"], "capture.uptime_seconds"),
            "interval_seconds": _hb_count(
                capture["interval_seconds"], "capture.interval_seconds"
            ),
            "listeners": listeners,
        },
        "transcriber": {
            "pending": _hb_count(transcriber["pending"], "transcriber"),
            "failed": _hb_count(transcriber["failed"], "transcriber"),
        },
        "uploader": {
            "queued": _hb_count(uploader["queued"], "uploader"),
            "oldest_queued_age_seconds": _hb_optional_count(
                uploader["oldest_queued_age_seconds"], "uploader"
            ),
            "rejected_total": _hb_count(uploader["rejected_total"], "uploader"),
            "last_success_at": _hb_optional_time(uploader["last_success_at"], "uploader"),
        },
        "glossary_version": glossary_version,
    }


# ----------------------------------------------------------------- WAV check


def validate_wav(audio: bytes) -> None:
    """Require a mono PCM16 8000 Hz WAV: RIFF/WAVE, a fmt chunk, a data chunk."""
    if not audio:
        raise RadioIngestError(400, "Audio part is empty.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise RadioIngestError(413, "Audio part is too large.")
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RadioIngestError(400, "Audio must be a WAV file.")

    fmt: tuple[int, int, int, int] | None = None
    has_data = False
    position = 12
    while position + 8 <= len(audio):
        chunk_id = audio[position:position + 4]
        (size,) = struct.unpack_from("<I", audio, position + 4)
        body = position + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(audio):
                raise RadioIngestError(400, "Audio WAV header is invalid.")
            tag, channels, rate, _byte_rate, _align, bits = struct.unpack_from(
                "<HHIIHH", audio, body
            )
            fmt = (tag, channels, rate, bits)
        elif chunk_id == b"data":
            has_data = True
            break
        position = body + size + (size & 1)

    if fmt is None or not has_data:
        raise RadioIngestError(400, "Audio WAV header is invalid.")
    if fmt != (1, 1, 8000, 16):
        raise RadioIngestError(400, "Audio must be mono PCM16 at 8000 Hz.")


# --------------------------------------------------------------- list helpers


def escape_like_pattern(text: str) -> str:
    """Escape ``!`` ``%`` ``_`` so user text matches literally in ILIKE.

    The SQL uses ``ESCAPE '!'`` (not a backslash, which depends on
    standard_conforming_strings).
    """
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def encode_cursor(started_at: datetime, segment_id: str) -> str:
    raw = f"{started_at.astimezone(timezone.utc).isoformat()}|{segment_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        stamp, segment_id = raw.split("|", 1)
        started_at = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError, binascii.Error):
        raise RadioQueryError("The 'before' cursor is invalid.") from None
    if started_at.tzinfo is None or not SEGMENT_ID_PATTERN.fullmatch(segment_id):
        raise RadioQueryError("The 'before' cursor is invalid.")
    return started_at, segment_id


def build_audio_key(
    prefix: str, channel: str, started_at: datetime, segment_id: str
) -> str:
    """``<prefix><channel>/<yyyy>/<mm>/<dd>/<segment_id>.wav`` (UTC date)."""
    clean = str(prefix or "").strip().lstrip("/")
    if clean and not clean.endswith("/"):
        clean += "/"
    day = started_at.astimezone(timezone.utc)
    return f"{clean}{channel}/{day:%Y}/{day:%m}/{day:%d}/{segment_id}.wav"


def _aws_error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", "") or "")
    return ""


# ---------------------------------------------------------------- repository


def radio_database_is_configured(database_url: str | None = None) -> bool:
    value = (database_url if database_url is not None else settings.database_url).strip()
    return bool(value and "change_me" not in value)


class RadioRepository(AbstractContextManager):
    """Raw-SQL psycopg3 access to ``lcdash_radio``. One connection per use."""

    def __init__(self, database_url: str | None = None, *, connect: Callable | None = None):
        self.database_url = (
            database_url if database_url is not None else settings.database_url
        )
        self._connect = connect or psycopg.connect
        self.connection = None

    def __enter__(self):
        if not radio_database_is_configured(self.database_url):
            raise RadioDatabaseError("DATABASE_URL is not configured for radio storage.")
        try:
            self.connection = self._connect(self.database_url, connect_timeout=10)
        except psycopg.Error as exc:
            raise RadioDatabaseError("Radio storage could not connect to PostgreSQL.") from exc
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.connection is not None:
            if exc_type is not None:
                try:
                    self.connection.rollback()
                except psycopg.Error:
                    pass
            self.connection.close()
            self.connection = None
        return False

    def _execute(self, query: str, params=None):
        if self.connection is None:
            raise RadioDatabaseError("Radio repository is not connected.")
        try:
            return self.connection.execute(query, params)
        except psycopg.Error as exc:
            self.connection.rollback()
            raise RadioDatabaseError("A PostgreSQL radio operation failed.") from exc

    def _commit(self) -> None:
        if self.connection is not None:
            try:
                self.connection.commit()
            except psycopg.Error as exc:
                raise RadioDatabaseError("A PostgreSQL radio commit failed.") from exc

    def initialize_schema(self) -> None:
        self._execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._commit()

    def segment_exists(self, segment_id: str) -> bool:
        row = self._execute(
            "SELECT 1 FROM lcdash_radio.segments WHERE segment_id = %s",
            (segment_id,),
        ).fetchone()
        return row is not None

    def insert_segment(self, record: RadioSegmentRecord, audio_key: str) -> bool:
        """Insert idempotently. True when a row was created, False if it existed."""
        cursor = self._execute(
            """
            INSERT INTO lcdash_radio.segments (
                segment_id, channel, direction, started_at, ended_at,
                duration_seconds, codec, payload_type, sample_rate_hz,
                pcmu_packet_count, transcript, language, model, transcribed_at,
                asr_segments, low_confidence, audio_key,
                radio_id, talkgroup, console_label
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::JSONB, %s, %s, %s, %s, %s
            )
            ON CONFLICT (segment_id) DO NOTHING
            """,
            (
                record.segment_id, record.channel, record.direction,
                record.started_at, record.ended_at, record.duration_seconds,
                record.codec, record.payload_type, record.sample_rate_hz,
                record.pcmu_packet_count, record.transcript, record.language,
                record.model, record.transcribed_at,
                json.dumps(list(record.asr_segments), ensure_ascii=False),
                record.low_confidence, audio_key,
                record.radio_id, record.talkgroup, record.console_label,
            ),
        )
        created = cursor.rowcount == 1
        self._commit()
        return created

    def purge_expired(self) -> int:
        """Delete transcript rows past retention. Never touches audit_events.

        A segment a person corrected carries ``retain_until``; it survives the
        30-day rule until that date passes.
        """
        cursor = self._execute(
            "DELETE FROM lcdash_radio.segments "
            "WHERE received_at < now() - interval '30 days' "
            "AND (retain_until IS NULL OR retain_until < now())"
        )
        deleted = max(int(cursor.rowcount or 0), 0)
        self._commit()
        return deleted

    # Column order is the row contract used by RadioService._item: indexes
    # 0-11 are the v1 columns, 12-15 the v2 correction columns, 16-18 the v4
    # signal columns.
    _SEGMENT_COLUMNS = (
        "segment_id, channel, direction, started_at, ended_at, duration_seconds, "
        "transcript, language, model, low_confidence, audio_key, received_at, "
        "corrected_text, corrected_by, corrected_at, retain_until, "
        "radio_id, talkgroup, console_label"
    )

    def list_segments(
        self,
        *,
        channel: str | None,
        direction: str | None,
        like_pattern: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
        radio_id: str | None = None,
        id_prefix: str | None = None,
        unit_radio_ids: list[str] | None = None,
    ) -> list[tuple]:
        """Newest first, keyset-paged on (started_at, segment_id).

        Every user-supplied value travels as a bound parameter; the only SQL
        text assembled here is fixed fragments. ``radio_id`` is an exact
        filter. The free-text search (``like_pattern``) matches the transcript
        and, when given, a radio ID prefix (``id_prefix`` is an escaped LIKE
        pattern) or any ID in ``unit_radio_ids`` (the IDs whose unit name
        matched), all in one OR group.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if channel:
            clauses.append("channel = %s")
            params.append(channel)
        if direction:
            clauses.append("direction = %s")
            params.append(direction)
        if radio_id:
            clauses.append("radio_id = %s")
            params.append(radio_id)
        if like_pattern:
            # Search what people read: the correction when there is one.
            alternatives = ["COALESCE(corrected_text, transcript) ILIKE %s ESCAPE '!'"]
            params.append(like_pattern)
            if id_prefix:
                alternatives.append("radio_id LIKE %s ESCAPE '!'")
                params.append(id_prefix)
            if unit_radio_ids:
                alternatives.append("radio_id = ANY(%s::TEXT[])")
                params.append(list(unit_radio_ids))
            clauses.append(
                alternatives[0] if len(alternatives) == 1
                else "(" + " OR ".join(alternatives) + ")"
            )
        if cursor is not None:
            clauses.append("(started_at, segment_id) < (%s, %s)")
            params.extend(cursor)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        params.append(limit)
        return self._execute(
            f"SELECT {self._SEGMENT_COLUMNS} FROM lcdash_radio.segments "
            f"{where}ORDER BY started_at DESC, segment_id DESC LIMIT %s",
            tuple(params),
        ).fetchall()

    def get_segment(self, segment_id: str) -> tuple | None:
        return self._execute(
            f"SELECT {self._SEGMENT_COLUMNS} FROM lcdash_radio.segments "
            "WHERE segment_id = %s",
            (segment_id,),
        ).fetchone()

    def channel_counts(self, hours: int) -> list[tuple]:
        return self._execute(
            "SELECT channel, COUNT(*) FROM lcdash_radio.segments "
            "WHERE started_at >= now() - make_interval(hours => %s) "
            "GROUP BY channel ORDER BY channel",
            (hours,),
        ).fetchall()

    def newest_received_at(self):
        row = self._execute(
            "SELECT MAX(received_at) FROM lcdash_radio.segments"
        ).fetchone()
        return row[0] if row else None

    # ----------------------------------------------------- corrections (v2)
    #
    # The write methods in this section deliberately do NOT commit. The
    # service follows each with ``record_audit`` (which commits), so a change
    # and its audit row land together or not at all: if the audit write fails,
    # ``_execute`` rolls the whole transaction back and the change never
    # happened.

    def save_correction(
        self,
        segment_id: str,
        new_text: str,
        actor: str,
        now: datetime,
        retain_until: datetime,
        new_audio_key: str | None,
    ) -> tuple | None:
        """Set the correction, append a history row, return the updated row."""
        current = self._execute(
            "SELECT corrected_text, transcript FROM lcdash_radio.segments "
            "WHERE segment_id = %s FOR UPDATE",
            (segment_id,),
        ).fetchone()
        if current is None:
            return None
        previous = current[0] if current[0] is not None else current[1]
        self._execute(
            "INSERT INTO lcdash_radio.corrections "
            "(segment_id, previous_text, new_text, actor, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (segment_id, previous, new_text, actor[:320], now),
        )
        self._execute(
            "UPDATE lcdash_radio.segments SET corrected_text = %s, "
            "corrected_by = %s, corrected_at = %s, retain_until = %s, "
            "audio_key = COALESCE(%s, audio_key) WHERE segment_id = %s",
            (new_text, actor[:320], now, retain_until, new_audio_key, segment_id),
        )
        return self.get_segment(segment_id)

    def clear_correction(
        self, segment_id: str, actor: str, now: datetime
    ) -> tuple[tuple, bool] | None:
        """Revert to the machine transcript. ``(row, changed)``; None if missing.

        Retention already granted (``retain_until``) is left alone: once kept,
        kept until that date.
        """
        current = self._execute(
            "SELECT corrected_text FROM lcdash_radio.segments "
            "WHERE segment_id = %s FOR UPDATE",
            (segment_id,),
        ).fetchone()
        if current is None:
            return None
        if current[0] is None:
            return self.get_segment(segment_id), False
        self._execute(
            "INSERT INTO lcdash_radio.corrections "
            "(segment_id, previous_text, new_text, actor, created_at) "
            "VALUES (%s, %s, NULL, %s, %s)",
            (segment_id, current[0], actor[:320], now),
        )
        self._execute(
            "UPDATE lcdash_radio.segments SET corrected_text = NULL, "
            "corrected_by = NULL, corrected_at = NULL WHERE segment_id = %s",
            (segment_id,),
        )
        return self.get_segment(segment_id), True

    # --------------------------------------------------------- fixes (v2)

    def list_replacements(self) -> list[tuple]:
        return self._execute(
            "SELECT replacement_id, find_text, replace_text, whole_word, "
            "created_by, created_at FROM lcdash_radio.replacements "
            "ORDER BY replacement_id"
        ).fetchall()

    def count_replacements(self) -> int:
        return int(self._execute(
            "SELECT COUNT(*) FROM lcdash_radio.replacements"
        ).fetchone()[0])

    def insert_replacement(
        self, find_text: str, replace_text: str, whole_word: bool, actor: str
    ) -> tuple | None:
        """None when a rule with the same find text (any case) already exists."""
        return self._execute(
            "INSERT INTO lcdash_radio.replacements "
            "(find_text, replace_text, whole_word, created_by) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT ((lower(find_text))) DO NOTHING "
            "RETURNING replacement_id, find_text, replace_text, whole_word, "
            "created_by, created_at",
            (find_text, replace_text, whole_word, actor[:320]),
        ).fetchone()

    def delete_replacement(self, replacement_id: int) -> tuple | None:
        return self._execute(
            "DELETE FROM lcdash_radio.replacements WHERE replacement_id = %s "
            "RETURNING replacement_id, find_text, replace_text, whole_word, "
            "created_by, created_at",
            (replacement_id,),
        ).fetchone()

    # --------------------------------------------------- watch terms (v2)

    _TERM_COLUMNS = "term_id, phrase, channels, enabled, created_by, created_at"

    def list_watch_terms(self) -> list[tuple]:
        return self._execute(
            f"SELECT {self._TERM_COLUMNS} FROM lcdash_radio.watch_terms "
            "ORDER BY term_id"
        ).fetchall()

    def list_enabled_watch_terms(self) -> list[tuple]:
        """``(term_id, phrase, channels)`` for every enabled term."""
        return self._execute(
            "SELECT term_id, phrase, channels FROM lcdash_radio.watch_terms "
            "WHERE enabled ORDER BY term_id"
        ).fetchall()

    def count_watch_terms(self) -> int:
        return int(self._execute(
            "SELECT COUNT(*) FROM lcdash_radio.watch_terms"
        ).fetchone()[0])

    def insert_watch_term(
        self, phrase: str, channels: list[str], enabled: bool, actor: str
    ) -> tuple | None:
        """None when the phrase already exists (case-insensitive)."""
        return self._execute(
            "INSERT INTO lcdash_radio.watch_terms "
            "(phrase, channels, enabled, created_by) "
            "VALUES (%s, %s::TEXT[], %s, %s) ON CONFLICT ((lower(phrase))) DO NOTHING "
            f"RETURNING {self._TERM_COLUMNS}",
            (phrase, channels, enabled, actor[:320]),
        ).fetchone()

    def update_watch_term(
        self, term_id: int, *, enabled: bool | None, channels: list[str] | None
    ) -> tuple | None:
        """Change only the supplied fields; None when the term is missing."""
        assignments: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            assignments.append("enabled = %s")
            params.append(enabled)
        if channels is not None:
            assignments.append("channels = %s::TEXT[]")
            params.append(channels)
        if not assignments:
            raise RadioDatabaseError("No watch term change was supplied.")
        params.append(term_id)
        return self._execute(
            f"UPDATE lcdash_radio.watch_terms SET {', '.join(assignments)} "
            f"WHERE term_id = %s RETURNING {self._TERM_COLUMNS}",
            tuple(params),
        ).fetchone()

    def delete_watch_term(self, term_id: int) -> tuple | None:
        return self._execute(
            "DELETE FROM lcdash_radio.watch_terms WHERE term_id = %s "
            f"RETURNING {self._TERM_COLUMNS}",
            (term_id,),
        ).fetchone()

    # ------------------------------------------------------- alerts (v2)

    def insert_alert(self, segment_id: str, term_id: int, phrase: str) -> bool:
        """One alert per (segment, term). True when a new alert was created."""
        cursor = self._execute(
            "INSERT INTO lcdash_radio.alerts (segment_id, term_id, phrase) "
            "VALUES (%s, %s, %s) ON CONFLICT (segment_id, term_id) DO NOTHING",
            (segment_id, term_id, phrase),
        )
        created = cursor.rowcount == 1
        self._commit()
        return created

    def list_open_alerts(self, since: datetime, limit: int) -> list[tuple]:
        """Undismissed alerts created at or after ``since``, newest first."""
        return self._execute(
            "SELECT a.alert_id, a.segment_id, a.phrase, a.created_at, "
            "s.channel, s.direction, s.started_at, s.transcript, s.audio_key, "
            "s.received_at, s.corrected_text, s.retain_until "
            "FROM lcdash_radio.alerts a "
            "JOIN lcdash_radio.segments s ON s.segment_id = a.segment_id "
            "WHERE a.dismissed_at IS NULL AND a.created_at >= %s "
            "ORDER BY a.created_at DESC, a.alert_id DESC LIMIT %s",
            (since, limit),
        ).fetchall()

    def dismiss_alert(
        self, alert_id: int, actor: str, now: datetime
    ) -> tuple[str, str] | None:
        """``('dismissed' | 'already', segment_id)``; None when no such alert."""
        row = self._execute(
            "UPDATE lcdash_radio.alerts SET dismissed_by = %s, dismissed_at = %s "
            "WHERE alert_id = %s AND dismissed_at IS NULL RETURNING segment_id",
            (actor[:320], now, alert_id),
        ).fetchone()
        if row is not None:
            return "dismissed", row[0]
        existing = self._execute(
            "SELECT segment_id FROM lcdash_radio.alerts WHERE alert_id = %s",
            (alert_id,),
        ).fetchone()
        return ("already", existing[0]) if existing is not None else None

    # --------------------------------------------- hint words and glossary (v3)

    _HINT_COLUMNS = "hint_id, phrase, created_by, created_at"

    def list_hints(self) -> list[tuple]:
        return self._execute(
            f"SELECT {self._HINT_COLUMNS} FROM lcdash_radio.hints ORDER BY hint_id"
        ).fetchall()

    def count_hints(self) -> int:
        return int(self._execute(
            "SELECT COUNT(*) FROM lcdash_radio.hints"
        ).fetchone()[0])

    def insert_hint(self, phrase: str, actor: str) -> tuple | None:
        """None when the phrase already exists (case-insensitive)."""
        return self._execute(
            "INSERT INTO lcdash_radio.hints (phrase, created_by) "
            "VALUES (%s, %s) ON CONFLICT ((lower(phrase))) DO NOTHING "
            f"RETURNING {self._HINT_COLUMNS}",
            (phrase, actor[:320]),
        ).fetchone()

    def delete_hint(self, hint_id: int) -> tuple | None:
        return self._execute(
            "DELETE FROM lcdash_radio.hints WHERE hint_id = %s "
            f"RETURNING {self._HINT_COLUMNS}",
            (hint_id,),
        ).fetchone()

    # ------------------------------------------------ unit names (v4)

    _UNIT_COLUMNS = "radio_id, name, updated_by, updated_at"

    def list_units(self) -> list[tuple]:
        """Every named unit as ``(radio_id, name, updated_by, updated_at)``."""
        return self._execute(
            f"SELECT {self._UNIT_COLUMNS} FROM lcdash_radio.units ORDER BY radio_id"
        ).fetchall()

    def count_units(self) -> int:
        return int(self._execute(
            "SELECT COUNT(*) FROM lcdash_radio.units"
        ).fetchone()[0])

    def get_unit(self, radio_id: str) -> tuple | None:
        return self._execute(
            f"SELECT {self._UNIT_COLUMNS} FROM lcdash_radio.units WHERE radio_id = %s",
            (radio_id,),
        ).fetchone()

    def upsert_unit(
        self, radio_id: str, name: str, actor: str, now: datetime
    ) -> tuple:
        """Create or rename a unit; returns the stored row. Does not commit."""
        return self._execute(
            "INSERT INTO lcdash_radio.units (radio_id, name, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (radio_id) DO UPDATE SET "
            "name = EXCLUDED.name, updated_by = EXCLUDED.updated_by, "
            "updated_at = EXCLUDED.updated_at "
            f"RETURNING {self._UNIT_COLUMNS}",
            (radio_id, name, actor[:320], now),
        ).fetchone()

    def delete_unit(self, radio_id: str) -> tuple | None:
        """Remove the name only; segments keep their radio ID. Does not commit."""
        return self._execute(
            "DELETE FROM lcdash_radio.units WHERE radio_id = %s "
            f"RETURNING {self._UNIT_COLUMNS}",
            (radio_id,),
        ).fetchone()

    def seen_radio_ids(self, since: datetime, limit: int) -> list[tuple]:
        """Distinct radio IDs heard on segments, most recent first.

        ``(radio_id, segments_since, last_seen_at)``; ``segments_since`` counts
        only segments that started at or after ``since``.
        """
        return self._execute(
            "SELECT radio_id, COUNT(*) FILTER (WHERE started_at >= %s), "
            "MAX(started_at) FROM lcdash_radio.segments "
            "WHERE radio_id IS NOT NULL GROUP BY radio_id "
            "ORDER BY MAX(started_at) DESC, radio_id LIMIT %s",
            (since, limit),
        ).fetchall()

    def radio_id_activity(self, radio_ids: list[str], since: datetime) -> list[tuple]:
        """Same row shape as ``seen_radio_ids`` for the given IDs only."""
        if not radio_ids:
            return []
        return self._execute(
            "SELECT radio_id, COUNT(*) FILTER (WHERE started_at >= %s), "
            "MAX(started_at) FROM lcdash_radio.segments "
            "WHERE radio_id = ANY(%s::TEXT[]) GROUP BY radio_id",
            (since, list(radio_ids)),
        ).fetchall()

    # ------------------------------------------------- listener status (v3)

    def upsert_listener_status(self, payload_json: str, received_at: datetime) -> None:
        """Replace the one-row listener status. Commits (heartbeats are not audited)."""
        self._execute(
            "INSERT INTO lcdash_radio.listener_status (id, payload, received_at) "
            "VALUES (1, %s::JSONB, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "payload = EXCLUDED.payload, received_at = EXCLUDED.received_at",
            (payload_json, received_at),
        )
        self._commit()

    def get_listener_status(self) -> tuple[dict, datetime] | None:
        """``(payload, received_at)`` of the last heartbeat, or None if never."""
        row = self._execute(
            "SELECT payload, received_at FROM lcdash_radio.listener_status WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        return payload, row[1]

    def record_audit(
        self, event_type: str, actor: str, segment_id: str | None, detail: dict
    ) -> None:
        self._execute(
            "INSERT INTO lcdash_radio.audit_events "
            "(event_type, actor, segment_id, detail) VALUES (%s, %s, %s, %s::JSONB)",
            (
                event_type, actor[:320], segment_id,
                json.dumps(detail, ensure_ascii=False, default=str),
            ),
        )
        self._commit()


# ------------------------------------------------------------------- service


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_id(value: object) -> int:
    """A numeric path id, or 'not found' (the id cannot exist)."""
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[0-9]{1,18}", text):
        raise RadioNotFoundError("Not found.")
    number = int(text)
    if number < 1:
        raise RadioNotFoundError("Not found.")
    return number


def _require_object(payload: object, allowed: frozenset[str]) -> dict:
    """A JSON object whose keys are all known (unknown keys are a 400)."""
    if not isinstance(payload, dict):
        raise RadioQueryError("The request body must be a JSON object.")
    if set(payload) - allowed:
        raise RadioQueryError("The request body contains unknown fields.")
    return payload


def _rule_error(error: TextRuleError) -> RadioQueryError:
    return RadioQueryError(str(error))


class RadioService:
    """Orchestrates S3 + Postgres for ingest, reads, corrections and alerts.

    One instance is shared per process (see ``app.main._radio_service``): the
    schema is applied once, the hourly purge throttle lives here, and so does
    the 60-second cache of the replacement fixes.
    """

    def __init__(
        self,
        *,
        bucket: str,
        audio_prefix: str,
        corrected_prefix: str = DEFAULT_CORRECTED_PREFIX,
        region: str = "us-east-1",
        s3_client=None,
        repository_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("Radio storage requires a bucket.")
        self._bucket = bucket
        self._audio_prefix = audio_prefix
        clean_corrected = str(corrected_prefix or "").strip().lstrip("/")
        if not clean_corrected:
            clean_corrected = DEFAULT_CORRECTED_PREFIX
        if not clean_corrected.endswith("/"):
            clean_corrected += "/"
        self._corrected_prefix = clean_corrected
        self._region = region
        # Injected in tests; built lazily otherwise so a request that never
        # touches audio never pays for credential resolution.
        self._s3 = s3_client
        self._repository_factory = repository_factory or RadioRepository
        self._clock = clock
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._purge_lock = threading.Lock()
        self._last_purge: float | None = None
        self._fix_lock = threading.Lock()
        self._fix_cache: tuple[float, FixSet] | None = None
        self._unit_lock = threading.Lock()
        self._unit_cache: tuple[float, dict[str, str]] | None = None

    @property
    def s3(self):
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    def _open(self):
        """Open a repository, applying the schema once per process."""
        repository = self._repository_factory()
        repository.__enter__()
        try:
            if not self._schema_ready:
                with self._schema_lock:
                    if not self._schema_ready:
                        # A task starting during a rolling deploy can deadlock with
                        # another task's open write (verified on real PostgreSQL);
                        # the loser is rolled back, so applying again is safe.
                        for attempt in range(3):
                            try:
                                repository.initialize_schema()
                                break
                            except RadioDatabaseError as exc:
                                if attempt < 2 and isinstance(
                                    exc.__cause__, psycopg.errors.DeadlockDetected
                                ):
                                    time.sleep(0.5 * (attempt + 1))
                                    continue
                                raise
                        self._schema_ready = True
        except BaseException:
            repository.__exit__(None, None, None)
            raise
        return repository

    # ------------------------------------------------------------------ fixes

    def _fixes(self, repository) -> FixSet:
        """The compiled replacement fixes, cached in-process for 60 seconds."""
        now = self._clock()
        with self._fix_lock:
            cached = self._fix_cache
            if cached is not None and now - cached[0] < FIX_CACHE_SECONDS:
                return cached[1]
        rules = [
            FixRule(row[1], row[2], bool(row[3]))
            for row in repository.list_replacements()
        ]
        fixes = FixSet(rules)
        with self._fix_lock:
            self._fix_cache = (now, fixes)
        return fixes

    def _drop_fix_cache(self) -> None:
        with self._fix_lock:
            self._fix_cache = None

    # ------------------------------------------------------------- unit names

    def _unit_names(self, repository) -> dict[str, str]:
        """``{radio_id: name}``, cached in-process for 60 seconds.

        Dropped whenever a name is set or removed through this process, so the
        admin who saved a name sees it at once; another process catches up
        within the cache window.
        """
        now = self._clock()
        with self._unit_lock:
            cached = self._unit_cache
            if cached is not None and now - cached[0] < UNIT_CACHE_SECONDS:
                return cached[1]
        names = {row[0]: row[1] for row in repository.list_units()}
        with self._unit_lock:
            self._unit_cache = (now, names)
        return names

    def _safe_unit_names(self, repository) -> dict[str, str]:
        """Names for display after a committed write: never fail the response."""
        try:
            return self._unit_names(repository)
        except Exception as error:  # noqa: BLE001
            logger.warning("radio unit names unavailable: %s", type(error).__name__)
            return {}

    def _drop_unit_cache(self) -> None:
        with self._unit_lock:
            self._unit_cache = None

    # ----------------------------------------------------------------- ingest

    def ingest(self, record: RadioSegmentRecord, audio: bytes) -> str:
        """Store a validated, signature-verified segment. 'created' or 'duplicate'."""
        repository = self._open()
        try:
            if repository.segment_exists(record.segment_id):
                self._maybe_purge(repository)
                return "duplicate"
            key = build_audio_key(
                self._audio_prefix, record.channel, record.started_at, record.segment_id
            )
            try:
                self.s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=audio,
                    ContentType="audio/wav",
                    ServerSideEncryption="AES256",
                )
            except Exception as error:
                logger.warning("radio-ingest put_object failed key=%s: %s", key, error)
                raise RadioStorageError("Radio audio storage failed.") from error
            created = repository.insert_segment(record, key)
            logger.info(
                "radio-ingest %s segment=%s channel=%s direction=%s low_confidence=%s",
                "created" if created else "duplicate",
                record.segment_id, record.channel, record.direction,
                record.low_confidence,
            )
            if created:
                # Keyword alerts are advisory and must never fail an ingest.
                self._match_new_segment(repository, record)
            self._maybe_purge(repository)
            return "created" if created else "duplicate"
        finally:
            repository.__exit__(None, None, None)

    def _match_new_segment(self, repository, record: RadioSegmentRecord) -> None:
        try:
            text = self._fixes(repository).apply(record.transcript)
            if radio_text.is_possible_noise(
                text, record.duration_seconds, record.low_confidence, False
            ):
                # Boilerplate the transcriber invents from background noise
                # must not raise a keyword alert. The segment is stored and
                # shown as usual; a later human correction is still checked.
                logger.info(
                    "radio-alerts skipped possible noise segment=%s",
                    record.segment_id,
                )
                return
            self._evaluate_alerts(
                repository, segment_id=record.segment_id,
                channel=record.channel, text=text,
            )
        except Exception as error:  # noqa: BLE001 - matching must never fail an ingest
            logger.warning(
                "radio-alerts matching failed at ingest segment=%s: %s",
                record.segment_id, type(error).__name__,
            )

    def _evaluate_alerts(
        self, repository, *, segment_id: str, channel: str, text: str
    ) -> int:
        """Raise one alert per (segment, enabled matching term). Returns new count.

        A failure on one term is logged and skipped so the others still run;
        nothing here ever raises into an ingest or a correction.
        """
        if not text:
            return 0
        created = 0
        try:
            terms = repository.list_enabled_watch_terms()
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "radio-alerts could not load watch terms segment=%s: %s",
                segment_id, type(error).__name__,
            )
            return 0
        for term_id, phrase, channels in terms:
            try:
                if not radio_text.term_applies_to_channel(channels, channel):
                    continue
                if not radio_text.find_match(radio_text.compile_phrase(phrase), text):
                    continue
                if repository.insert_alert(segment_id, term_id, phrase):
                    created += 1
                    logger.info(
                        "radio-alert segment=%s term=%s channel=%s",
                        segment_id, term_id, channel,
                    )
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "radio-alerts match failed segment=%s term=%s: %s",
                    segment_id, term_id, type(error).__name__,
                )
        return created

    def _maybe_purge(self, repository) -> None:
        """Lazy 30-day purge (respecting retain_until), at most once per hour."""
        with self._purge_lock:
            now = self._clock()
            if (
                self._last_purge is not None
                and now - self._last_purge < PURGE_INTERVAL_SECONDS
            ):
                return
            self._last_purge = now
        try:
            deleted = repository.purge_expired()
        except RadioDatabaseError as error:
            logger.warning("radio-ingest purge failed: %s", error)
            return
        if deleted:
            logger.info("radio-ingest purged %d expired transcript rows", deleted)

    # ------------------------------------------------------------------ reads

    def _audio_available(
        self, audio_key: str | None, received_at: datetime,
        retain_until: datetime | None,
    ) -> bool:
        """Whether the WAV should still exist.

        A key under the corrected prefix lives until ``retain_until``; a key
        under the normal prefix lives 15 days from receipt.
        """
        if not audio_key:
            return False
        now = self._utcnow()
        if audio_key.startswith(self._corrected_prefix):
            return retain_until is not None and retain_until > now
        return received_at > now - timedelta(days=AUDIO_RETENTION_DAYS)

    def _item(
        self, row: tuple, fixes: FixSet, unit_names: dict[str, str] | None = None
    ) -> dict:
        (segment_id, channel, direction, started_at, ended_at, duration, transcript,
         language, model, low_confidence, audio_key, received_at,
         corrected_text, corrected_by, corrected_at, retain_until,
         radio_id, talkgroup, console_label) = row
        corrected = corrected_text is not None
        # A human correction wins outright; fixes only ever touch machine text.
        text = corrected_text if corrected else fixes.apply(transcript)
        return {
            "segment_id": segment_id,
            "channel": channel,
            "direction": direction,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": float(duration),
            "text": text,
            # Alias of ``text`` so clients written for v1 keep working.
            "transcript": text,
            "machine_transcript": transcript,
            "corrected": corrected,
            "corrected_by": corrected_by if corrected else None,
            "corrected_at": _iso(corrected_at) if corrected else None,
            "retained_until": _iso(retain_until),
            "language": language,
            "model": model,
            "low_confidence": bool(low_confidence),
            # Display only: the text itself is never altered or removed.
            "possible_noise": radio_text.is_possible_noise(
                text, float(duration), bool(low_confidence), corrected
            ),
            "audio_available": self._audio_available(
                audio_key, received_at, retain_until
            ),
            "received_at": received_at.isoformat(),
            # v4: who is talking. NULL for rows stored before v4 and when the
            # listener saw no signalling. A possible-noise segment keeps its
            # radio_id: the ID comes from the signalling, not the transcript.
            "radio_id": radio_id,
            "talkgroup": talkgroup,
            "console_label": console_label,
            "unit_name": (unit_names or {}).get(radio_id) if radio_id else None,
        }

    def list_segments(
        self,
        *,
        actor: str,
        channel: str = "",
        direction: str = "",
        q: str = "",
        before: str = "",
        limit: str | int = "",
        radio_id: str = "",
    ) -> dict:
        channel = str(channel or "").strip()
        direction = str(direction or "").strip()
        q = str(q or "").strip()
        before = str(before or "").strip()
        radio_id = str(radio_id or "").strip()
        if channel and not CHANNEL_PATTERN.fullmatch(channel):
            raise RadioQueryError("The 'channel' filter is invalid.")
        if direction and direction not in DIRECTIONS:
            raise RadioQueryError("The 'direction' filter is invalid.")
        if radio_id and not RADIO_ID_PATTERN.fullmatch(radio_id):
            raise RadioQueryError("The 'radio_id' filter is invalid.")
        if len(q) > MAX_QUERY_CHARS:
            raise RadioQueryError("The 'q' filter is too long.")
        if any(ord(char) < 32 for char in q):
            raise RadioQueryError("The 'q' filter contains invalid characters.")
        if str(limit).strip() == "":
            page_size = DEFAULT_LIST_LIMIT
        else:
            try:
                page_size = int(str(limit).strip())
            except ValueError:
                raise RadioQueryError("The 'limit' value is invalid.") from None
            if not 1 <= page_size <= MAX_LIST_LIMIT:
                raise RadioQueryError(
                    f"The 'limit' value must be between 1 and {MAX_LIST_LIMIT}."
                )
        cursor = decode_cursor(before) if before else None

        repository = self._open()
        try:
            unit_names = self._unit_names(repository)
            id_prefix = None
            unit_radio_ids: list[str] | None = None
            if q:
                # The search also finds a radio ID (by prefix) and a unit name
                # (case-insensitive substring, resolved from the cached names
                # to radio IDs; the database only ever sees bound values).
                if RADIO_ID_PATTERN.fullmatch(q):
                    id_prefix = f"{escape_like_pattern(q)}%"
                needle = q.casefold()
                matched = sorted(
                    unit_id for unit_id, name in unit_names.items()
                    if needle in name.casefold()
                )
                unit_radio_ids = matched or None
            rows = repository.list_segments(
                channel=channel or None,
                direction=direction or None,
                like_pattern=f"%{escape_like_pattern(q)}%" if q else None,
                cursor=cursor,
                limit=page_size + 1,
                radio_id=radio_id or None,
                id_prefix=id_prefix,
                unit_radio_ids=unit_radio_ids,
            )
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            fixes = self._fixes(repository)
            items = [self._item(row, fixes, unit_names) for row in rows]
            # Audited in the same connection AFTER the read and BEFORE the
            # result leaves this method: if the audit write fails, the caller
            # gets an error and no transcript.
            self._audit(
                repository, EVENT_LIST, actor, None,
                {
                    "channel": channel, "direction": direction,
                    "q": q[:100], "radio_id": radio_id, "paged": bool(before),
                    "limit": page_size, "returned": len(items),
                },
                log_extra=(
                    f"channel={channel or '*'} direction={direction or '*'} "
                    f"radio_id={radio_id or '*'} has_q={bool(q)} "
                    f"paged={bool(before)} returned={len(items)}"
                ),
            )
        finally:
            repository.__exit__(None, None, None)
        next_before = (
            encode_cursor(rows[-1][3], rows[-1][0]) if has_more and rows else None
        )
        return {"segments": items, "has_more": has_more, "next_before": next_before}

    def fetch_audio(self, segment_id: str, *, actor: str) -> bytes:
        """Return the WAV bytes for one segment, after auditing the listen.

        Works for both prefixes (``audio_key`` says which). Fails closed: the
        object is fetched, then the audit event is written, and the bytes are
        only returned if that write succeeded.
        """
        if not SEGMENT_ID_PATTERN.fullmatch(str(segment_id or "")):
            raise RadioNotFoundError("Segment not found.")
        repository = self._open()
        try:
            row = repository.get_segment(segment_id)
            if row is None:
                raise RadioNotFoundError("Segment not found.")
            audio_key = row[10]
            if not audio_key:
                raise RadioNotFoundError("Audio has expired or is no longer available.")
            try:
                response = self.s3.get_object(Bucket=self._bucket, Key=audio_key)
                audio = response["Body"].read()
            except Exception as error:
                if _aws_error_code(error) in ("NoSuchKey", "404", "NotFound"):
                    raise RadioNotFoundError(
                        "Audio has expired or is no longer available."
                    ) from error
                logger.warning("radio get_object failed key=%s: %s", audio_key, error)
                raise RadioStorageError("Radio audio storage failed.") from error
            self._audit(
                repository, EVENT_LISTEN, actor, segment_id,
                {"channel": row[1], "direction": row[2]},
                log_extra=f"channel={row[1]}",
            )
        finally:
            repository.__exit__(None, None, None)
        return audio

    def stats(self) -> dict:
        repository = self._open()
        try:
            counts = repository.channel_counts(STATS_WINDOW_HOURS)
            newest = repository.newest_received_at()
            listener_row = repository.get_listener_status()
        finally:
            repository.__exit__(None, None, None)
        channels = [{"channel": name, "count": int(count)} for name, count in counts]
        return {
            "window_hours": STATS_WINDOW_HOURS,
            "channels": channels,
            "total": sum(item["count"] for item in channels),
            "last_received_at": newest.isoformat() if newest else None,
            "listener": self._listener_summary(listener_row),
        }

    def _listener_summary(self, row: tuple[dict, datetime] | None) -> dict | None:
        """The page's view of the last heartbeat; None when none ever arrived.

        ``reporting`` is true while the heartbeat is at most
        ``LISTENER_REPORTING_SECONDS`` old. Only counters and times: no
        hostnames, no addresses, no text (none are ever stored).
        """
        if row is None:
            return None
        payload, received_at = row
        payload = payload if isinstance(payload, dict) else {}
        capture = payload.get("capture") if isinstance(payload.get("capture"), dict) else {}
        interval_seconds = capture.get("interval_seconds")
        listeners = [
            {
                "channel": item.get("channel"),
                "direction": item.get("direction"),
                "packets_interval": item.get("packets_interval"),
                "interval_seconds": interval_seconds,
                "other_payload_types": item.get("other_payload_types") or {},
                "last_packet_at": item.get("last_packet_at"),
            }
            for item in (capture.get("listeners") or [])
            if isinstance(item, dict)
        ]
        age = max(0, int((self._utcnow() - received_at).total_seconds()))
        return {
            "reporting": age <= LISTENER_REPORTING_SECONDS,
            "last_heartbeat_at": received_at.isoformat(),
            "age_seconds": age,
            "capture_uptime_seconds": capture.get("uptime_seconds"),
            "capture_stats_at": capture.get("stats_at"),
            "listeners": listeners,
            "transcriber": payload.get("transcriber") or {},
            "uploader": payload.get("uploader") or {},
        }

    # -------------------------------------------------------------- heartbeat

    def _build_glossary(self, repository) -> dict:
        """Hints, then enabled watch phrases, then the fixes' replacement text."""
        hints = [row[1] for row in repository.list_hints()]
        watch = [row[1] for row in repository.list_enabled_watch_terms()]
        replacements = [row[2] for row in repository.list_replacements()]
        return radio_text.build_glossary(hints, watch, replacements)

    def record_heartbeat(self, heartbeat: dict) -> dict:
        """Store a validated, signature-verified heartbeat; return the reply body.

        Builds the glossary first and stores second, inside one repository
        session, so a success means both worked. Any failure is a
        ``RadioServiceError`` (HTTP 503) and nothing is stored or logged as ok;
        the listener simply tries again in a minute. A heartbeat never reads or
        writes ``segments`` and is not audited (60-second noise).
        """
        try:
            repository = self._open()
            try:
                glossary = self._build_glossary(repository)
                received_at = self._utcnow()
                repository.upsert_listener_status(
                    json.dumps(heartbeat, ensure_ascii=False), received_at
                )
            finally:
                repository.__exit__(None, None, None)
        except RadioServiceError:
            raise
        except Exception as error:  # noqa: BLE001 - a heartbeat must never crash the app
            logger.warning("radio-heartbeat failed: %s", type(error).__name__)
            raise RadioDatabaseError("The heartbeat could not be stored.") from error

        listeners = heartbeat["capture"]["listeners"]
        _heartbeat_logger().info(
            "%s listeners=%d packets_interval=%d undecoded=%d",
            HEARTBEAT_LOG_PREFIX,
            len(listeners),
            sum(item["packets_interval"] for item in listeners),
            sum(sum(item["other_payload_types"].values()) for item in listeners),
        )
        if heartbeat.get("glossary_version") == glossary["version"]:
            reply_glossary = {"version": glossary["version"]}
        else:
            reply_glossary = glossary
        return {
            "status": "ok",
            "server_time": received_at.isoformat(),
            "glossary": reply_glossary,
        }

    # ------------------------------------------------------------- hint words

    @staticmethod
    def _hint_item(row: tuple) -> dict:
        return {
            "hint_id": row[0],
            "phrase": row[1],
            "created_by": row[2],
            "created_at": _iso(row[3]),
        }

    def list_hints(self) -> dict:
        repository = self._open()
        try:
            rows = repository.list_hints()
        finally:
            repository.__exit__(None, None, None)
        return {
            "hints": [self._hint_item(row) for row in rows],
            "max_hints": radio_text.MAX_HINTS,
        }

    def add_hint(self, payload: object, *, actor: str) -> dict:
        body = _require_object(payload, frozenset({"phrase"}))
        try:
            phrase = radio_text.clean_single_line(
                body.get("phrase"), label="phrase",
                minimum=radio_text.MIN_HINT_CHARS,
                maximum=radio_text.MAX_HINT_CHARS,
            )
        except TextRuleError as error:
            raise _rule_error(error) from None

        repository = self._open()
        try:
            if repository.count_hints() >= radio_text.MAX_HINTS:
                raise RadioConflictError(
                    f"At most {radio_text.MAX_HINTS} hint words are allowed."
                )
            row = repository.insert_hint(phrase, actor)
            if row is None:
                raise RadioConflictError("That hint word already exists.")
            self._audit(
                repository, EVENT_HINT_ADD, actor, None,
                {"hint_id": row[0], "phrase": phrase},
                log_extra=f"hint={row[0]}",
            )
        finally:
            repository.__exit__(None, None, None)
        return self._hint_item(row)

    def remove_hint(self, hint_id: object, *, actor: str) -> dict:
        number = _parse_id(hint_id)
        repository = self._open()
        try:
            row = repository.delete_hint(number)
            if row is None:
                raise RadioNotFoundError("Hint word not found.")
            self._audit(
                repository, EVENT_HINT_REMOVE, actor, None,
                {"hint_id": number, "phrase": row[1]},
                log_extra=f"hint={number}",
            )
        finally:
            repository.__exit__(None, None, None)
        return {"hint_id": number, "removed": True}

    # ------------------------------------------------------------- unit names

    @staticmethod
    def _unit_item(radio_id: str, name: str | None, activity: tuple | None) -> dict:
        _id, segments, last_seen = activity if activity else (radio_id, 0, None)
        return {
            "radio_id": radio_id,
            "name": name,
            "segments_30d": int(segments or 0),
            "last_seen_at": _iso(last_seen),
        }

    def list_units(self) -> dict:
        """Named units merged with the radio IDs heard on recent segments.

        Every named unit appears (an unheard one with zero segments); up to 500
        heard IDs appear too, most recently heard first. Not audited per call,
        like the other admin reads.
        """
        since = self._utcnow() - timedelta(days=UNIT_ACTIVITY_DAYS)
        repository = self._open()
        try:
            names = {row[0]: row[1] for row in repository.list_units()}
            seen = repository.seen_radio_ids(since, MAX_SEEN_UNITS)
            seen_ids = {row[0] for row in seen}
            extra = [unit_id for unit_id in names if unit_id not in seen_ids]
            seen = list(seen) + list(repository.radio_id_activity(extra, since))
        finally:
            repository.__exit__(None, None, None)
        activity = {row[0]: row for row in seen}

        def recency(unit_id: str) -> tuple:
            # Most recently heard first; never-heard (named only) units last.
            last = activity[unit_id][2] if unit_id in activity else None
            return (last is None, -last.timestamp() if last is not None else 0.0, unit_id)

        ordered = sorted(set(names) | set(activity), key=recency)
        return {
            "units": [
                self._unit_item(unit_id, names.get(unit_id), activity.get(unit_id))
                for unit_id in ordered
            ],
            "max_units": MAX_UNITS,
        }

    def set_unit(self, radio_id: object, payload: object, *, actor: str) -> tuple[dict, bool]:
        """Name (or rename) a radio ID. Returns ``(item, created)``."""
        body = _require_object(payload, frozenset({"name"}))
        clean_id = str(radio_id if radio_id is not None else "")
        if not RADIO_ID_PATTERN.fullmatch(clean_id):
            raise RadioQueryError("The radio ID must be 1 to 10 digits.")
        try:
            name = radio_text.clean_single_line(
                body.get("name"), label="name",
                minimum=MIN_UNIT_NAME_CHARS, maximum=MAX_UNIT_NAME_CHARS,
            )
        except TextRuleError as error:
            raise _rule_error(error) from None

        now = self._utcnow()
        since = now - timedelta(days=UNIT_ACTIVITY_DAYS)
        repository = self._open()
        try:
            existing = repository.get_unit(clean_id)
            if existing is None and repository.count_units() >= MAX_UNITS:
                raise RadioConflictError(f"At most {MAX_UNITS} units can be named.")
            row = repository.upsert_unit(clean_id, name, actor, now)
            activity = repository.radio_id_activity([clean_id], since)
            self._audit(
                repository, EVENT_UNIT_SET, actor, None,
                {
                    "radio_id": clean_id, "name": name,
                    "previous_name": existing[1] if existing is not None else None,
                    "created": existing is None,
                },
                log_extra=f"radio_id={clean_id} created={existing is None}",
            )
        finally:
            repository.__exit__(None, None, None)
        self._drop_unit_cache()
        item = self._unit_item(clean_id, row[1], activity[0] if activity else None)
        item["updated_by"] = row[2]
        item["updated_at"] = _iso(row[3])
        return item, existing is None

    def remove_unit(self, radio_id: object, *, actor: str) -> dict:
        """Remove a unit's name only; its segments keep the radio ID."""
        clean_id = str(radio_id if radio_id is not None else "")
        if not RADIO_ID_PATTERN.fullmatch(clean_id):
            raise RadioNotFoundError("Unit not found.")
        repository = self._open()
        try:
            row = repository.delete_unit(clean_id)
            if row is None:
                raise RadioNotFoundError("Unit not found.")
            self._audit(
                repository, EVENT_UNIT_REMOVE, actor, None,
                {"radio_id": clean_id, "name": row[1]},
                log_extra=f"radio_id={clean_id}",
            )
        finally:
            repository.__exit__(None, None, None)
        self._drop_unit_cache()
        return {"radio_id": clean_id, "removed": True}

    # ------------------------------------------------------------ corrections

    def correct_segment(self, segment_id: str, text: object, *, actor: str) -> dict:
        """Save a human correction; keep the audio for a year; re-check alerts.

        Last write wins; the corrections table keeps every version. Audited in
        the same transaction as the change (fail closed).
        """
        try:
            clean = radio_text.clean_correction_text(text)
        except TextRuleError as error:
            raise _rule_error(error) from None
        if not SEGMENT_ID_PATTERN.fullmatch(str(segment_id or "")):
            raise RadioNotFoundError("Segment not found.")

        repository = self._open()
        try:
            row = repository.get_segment(segment_id)
            if row is None:
                raise RadioNotFoundError("Segment not found.")
            now = self._utcnow()
            retain_until = now + timedelta(days=CORRECTED_RETENTION_DAYS)
            first_correction = row[12] is None and row[15] is None
            new_key = self._copy_for_retention(row, now)
            updated = repository.save_correction(
                segment_id, clean, actor, now, retain_until, new_key
            )
            if updated is None:  # purged between the read and the write
                raise RadioNotFoundError("Segment not found.")
            self._audit(
                repository, EVENT_CORRECT, actor, segment_id,
                {
                    "channel": row[1], "direction": row[2],
                    "chars": len(clean), "first_correction": first_correction,
                    "audio_retained": bool(new_key),
                },
                log_extra=(
                    f"channel={row[1]} chars={len(clean)} "
                    f"audio_retained={bool(new_key)}"
                ),
            )
            # The correction is saved. Watch terms are checked against the
            # corrected text; a failure here is logged, never surfaced.
            self._evaluate_alerts(
                repository, segment_id=segment_id, channel=row[1], text=clean
            )
            item = self._item(
                updated, self._safe_fixes(repository),
                self._safe_unit_names(repository),
            )
        finally:
            repository.__exit__(None, None, None)
        return item

    def revert_correction(self, segment_id: str, *, actor: str) -> dict:
        """Clear a correction (history keeps both versions). A no-op if none."""
        if not SEGMENT_ID_PATTERN.fullmatch(str(segment_id or "")):
            raise RadioNotFoundError("Segment not found.")
        repository = self._open()
        try:
            result = repository.clear_correction(segment_id, actor, self._utcnow())
            if result is None:
                raise RadioNotFoundError("Segment not found.")
            updated, changed = result
            if changed:
                self._audit(
                    repository, EVENT_CORRECT_REVERT, actor, segment_id,
                    {"channel": updated[1], "direction": updated[2]},
                    log_extra=f"channel={updated[1]}",
                )
            item = self._item(
                updated, self._safe_fixes(repository),
                self._safe_unit_names(repository),
            )
        finally:
            repository.__exit__(None, None, None)
        return item

    def _safe_fixes(self, repository) -> FixSet:
        """Fixes for display after a committed write: never fail the response."""
        try:
            return self._fixes(repository)
        except Exception as error:  # noqa: BLE001
            logger.warning("radio fixes unavailable: %s", type(error).__name__)
            return radio_text.EMPTY_FIXES

    def _copy_for_retention(self, row: tuple, now: datetime) -> str | None:
        """On the first correction, copy the WAV to the corrected prefix.

        Returns the new key, or None when there is nothing to copy (no audio,
        already under the corrected prefix, or the audio is already gone, in
        which case the text is still saved). Any other storage failure raises
        so nothing is changed.
        """
        segment_id, channel = row[0], row[1]
        started_at, audio_key, received_at = row[3], row[10], row[11]
        if not audio_key or audio_key.startswith(self._corrected_prefix):
            return None
        if received_at <= now - timedelta(days=AUDIO_RETENTION_DAYS):
            return None
        destination = build_audio_key(
            self._corrected_prefix, channel, started_at, segment_id
        )
        try:
            self.s3.copy_object(
                Bucket=self._bucket,
                Key=destination,
                CopySource={"Bucket": self._bucket, "Key": audio_key},
                ServerSideEncryption="AES256",
                ContentType="audio/wav",
                MetadataDirective="REPLACE",
            )
        except Exception as error:
            if _aws_error_code(error) in ("NoSuchKey", "404", "NotFound"):
                return None
            logger.warning(
                "radio copy_object failed key=%s: %s", destination, error
            )
            raise RadioStorageError("Radio audio storage failed.") from error
        return destination

    # ------------------------------------------------------------------ fixes

    @staticmethod
    def _rule_item(row: tuple) -> dict:
        return {
            "replacement_id": row[0],
            "find": row[1],
            "replace": row[2],
            "whole_word": bool(row[3]),
            "created_by": row[4],
            "created_at": _iso(row[5]),
        }

    def list_replacements(self) -> dict:
        repository = self._open()
        try:
            rows = repository.list_replacements()
        finally:
            repository.__exit__(None, None, None)
        return {
            "replacements": [self._rule_item(row) for row in rows],
            "max_rules": radio_text.MAX_FIX_RULES,
        }

    def add_replacement(self, payload: object, *, actor: str) -> dict:
        body = _require_object(payload, frozenset({"find", "replace", "whole_word"}))
        try:
            find = radio_text.clean_single_line(
                body.get("find"), label="find", minimum=1,
                maximum=radio_text.MAX_FIND_CHARS,
            )
            replace = radio_text.clean_replacement(body.get("replace", ""))
        except TextRuleError as error:
            raise _rule_error(error) from None
        whole_word = body.get("whole_word", True)
        if not isinstance(whole_word, bool):
            raise RadioQueryError("The 'whole_word' value must be true or false.")
        if find == replace:
            raise RadioQueryError("The 'replace' value must differ from 'find'.")

        repository = self._open()
        try:
            if repository.count_replacements() >= radio_text.MAX_FIX_RULES:
                raise RadioConflictError(
                    f"At most {radio_text.MAX_FIX_RULES} fixes are allowed."
                )
            row = repository.insert_replacement(find, replace, whole_word, actor)
            if row is None:
                raise RadioConflictError("A fix for that text already exists.")
            self._audit(
                repository, EVENT_FIX_ADD, actor, None,
                {"replacement_id": row[0], "find": find, "replace": replace,
                 "whole_word": whole_word},
                log_extra=f"replacement={row[0]}",
            )
        finally:
            repository.__exit__(None, None, None)
        self._drop_fix_cache()
        return self._rule_item(row)

    def remove_replacement(self, replacement_id: object, *, actor: str) -> dict:
        number = _parse_id(replacement_id)
        repository = self._open()
        try:
            row = repository.delete_replacement(number)
            if row is None:
                raise RadioNotFoundError("Fix not found.")
            self._audit(
                repository, EVENT_FIX_REMOVE, actor, None,
                {"replacement_id": row[0], "find": row[1], "replace": row[2],
                 "whole_word": bool(row[3])},
                log_extra=f"replacement={row[0]}",
            )
        finally:
            repository.__exit__(None, None, None)
        self._drop_fix_cache()
        return {"replacement_id": number, "removed": True}

    # ------------------------------------------------------------ watch terms

    @staticmethod
    def _term_item(row: tuple) -> dict:
        return {
            "term_id": row[0],
            "phrase": row[1],
            "channels": list(row[2] or []),
            "enabled": bool(row[3]),
            "created_by": row[4],
            "created_at": _iso(row[5]),
        }

    def list_watch_terms(self) -> dict:
        repository = self._open()
        try:
            rows = repository.list_watch_terms()
        finally:
            repository.__exit__(None, None, None)
        return {
            "terms": [self._term_item(row) for row in rows],
            "max_terms": radio_text.MAX_WATCH_TERMS,
        }

    def add_watch_term(self, payload: object, *, actor: str) -> dict:
        body = _require_object(payload, frozenset({"phrase", "channels", "enabled"}))
        try:
            phrase = radio_text.clean_single_line(
                body.get("phrase"), label="phrase",
                minimum=radio_text.MIN_PHRASE_CHARS,
                maximum=radio_text.MAX_PHRASE_CHARS,
            )
            channels = radio_text.clean_channels(body.get("channels"))
        except TextRuleError as error:
            raise _rule_error(error) from None
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RadioQueryError("The 'enabled' value must be true or false.")

        repository = self._open()
        try:
            if repository.count_watch_terms() >= radio_text.MAX_WATCH_TERMS:
                raise RadioConflictError(
                    f"At most {radio_text.MAX_WATCH_TERMS} watch terms are allowed."
                )
            row = repository.insert_watch_term(phrase, channels, enabled, actor)
            if row is None:
                raise RadioConflictError("That watch term already exists.")
            self._audit(
                repository, EVENT_WATCH_ADD, actor, None,
                {"term_id": row[0], "phrase": phrase, "channels": channels,
                 "enabled": enabled},
                log_extra=f"term={row[0]}",
            )
        finally:
            repository.__exit__(None, None, None)
        return self._term_item(row)

    def update_watch_term(self, term_id: object, payload: object, *, actor: str) -> dict:
        number = _parse_id(term_id)
        body = _require_object(payload, frozenset({"enabled", "channels"}))
        if not body:
            raise RadioQueryError("Supply 'enabled' and/or 'channels'.")
        enabled = None
        channels = None
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise RadioQueryError("The 'enabled' value must be true or false.")
            enabled = body["enabled"]
        if "channels" in body:
            try:
                channels = radio_text.clean_channels(body["channels"])
            except TextRuleError as error:
                raise _rule_error(error) from None

        repository = self._open()
        try:
            row = repository.update_watch_term(
                number, enabled=enabled, channels=channels
            )
            if row is None:
                raise RadioNotFoundError("Watch term not found.")
            detail: dict[str, Any] = {"term_id": number}
            if enabled is not None:
                detail["enabled"] = enabled
            if channels is not None:
                detail["channels"] = channels
            self._audit(
                repository, EVENT_WATCH_CHANGE, actor, None, detail,
                log_extra=f"term={number}",
            )
        finally:
            repository.__exit__(None, None, None)
        return self._term_item(row)

    def remove_watch_term(self, term_id: object, *, actor: str) -> dict:
        number = _parse_id(term_id)
        repository = self._open()
        try:
            row = repository.delete_watch_term(number)
            if row is None:
                raise RadioNotFoundError("Watch term not found.")
            self._audit(
                repository, EVENT_WATCH_REMOVE, actor, None,
                {"term_id": number, "phrase": row[1]},
                log_extra=f"term={number}",
            )
        finally:
            repository.__exit__(None, None, None)
        return {"term_id": number, "removed": True}

    # ----------------------------------------------------------------- alerts

    def list_alerts(self) -> dict:
        """Undismissed alerts from the last 24 hours, newest first, max 50.

        Not audited per call: the dashboard polls this every 15 seconds.
        Transcript access is still audited where audio is played and where the
        full list is fetched.
        """
        repository = self._open()
        try:
            since = self._utcnow() - timedelta(hours=ALERT_WINDOW_HOURS)
            rows = repository.list_open_alerts(since, MAX_ALERTS)
            fixes = self._fixes(repository)
        finally:
            repository.__exit__(None, None, None)
        alerts = []
        for (alert_id, segment_id, phrase, created_at, channel, direction,
             started_at, transcript, audio_key, received_at, corrected_text,
             retain_until) in rows:
            corrected = corrected_text is not None
            text = corrected_text if corrected else fixes.apply(transcript)
            alerts.append({
                "alert_id": alert_id,
                "segment_id": segment_id,
                "channel": channel,
                "direction": direction,
                "started_at": started_at.isoformat(),
                "phrase": phrase,
                "snippet": radio_text.make_snippet(text, SNIPPET_CHARS),
                "created_at": created_at.isoformat(),
                "audio_available": self._audio_available(
                    audio_key, received_at, retain_until
                ),
                "text_corrected": corrected,
            })
        return {"alerts": alerts, "window_hours": ALERT_WINDOW_HOURS}

    def dismiss_alert(self, alert_id: object, *, actor: str) -> dict:
        """Dismiss for everyone. Visual state only, not an acknowledgement."""
        number = _parse_id(alert_id)
        repository = self._open()
        try:
            result = repository.dismiss_alert(number, actor, self._utcnow())
            if result is None:
                raise RadioNotFoundError("Alert not found.")
            state, segment_id = result
            if state == "dismissed":
                self._audit(
                    repository, EVENT_ALERT_DISMISS, actor, segment_id,
                    {"alert_id": number},
                    log_extra=f"alert={number}",
                )
        finally:
            repository.__exit__(None, None, None)
        return {"alert_id": number, "dismissed": True}

    # ------------------------------------------------------------------ audit

    @staticmethod
    def _audit(
        repository, event_type: str, actor: str, segment_id: str | None,
        detail: dict, *, log_extra: str = "",
    ) -> None:
        try:
            repository.record_audit(event_type, actor, segment_id, detail)
        except RadioDatabaseError:
            logger.error(
                "radio-audit FAILED event=%s actor=%s segment=%s",
                event_type, actor, segment_id or "-",
            )
            raise
        logger.info(
            "radio-audit event=%s actor=%s segment=%s %s",
            event_type, actor, segment_id or "-", log_extra,
        )
