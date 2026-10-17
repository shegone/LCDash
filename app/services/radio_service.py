"""Mindshare Radio Intelligence: signed ingest, storage, and admin reads.

Contract: docs/MINDSHARE_RADIO_INGEST.md (v1). The listener on the Hermes host
captures a finished radio segment, transcribes it locally, and POSTs the WAV
plus a metadata document, signed with an Ed25519 private key that exists only
on that host. This module holds everything that is not HTTP plumbing:

- the pure checks (signature, timestamp, metadata schema, WAV header,
  low-confidence rule), each raising ``RadioIngestError`` with a short,
  transcript-free ``detail``;
- ``RadioRepository``, raw psycopg3 SQL against the ``lcdash_radio`` schema;
- ``RadioService``, which orchestrates S3 + Postgres for ingest and the three
  admin reads (list, audio, stats) and records the audit trail.

This feature is inquiry-only. Nothing here writes to CAD, radio, paging, or any
operational output, and transcripts are machine generated and unverified.

Nothing in AWS holds a secret for this path: the app is configured only with
the base64 raw public key (``settings.radio_ingest_public_key``).
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

logger = logging.getLogger(__name__)

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
PURGE_INTERVAL_SECONDS = 3600
STATS_WINDOW_HOURS = 24

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
MAX_QUERY_CHARS = 200

EVENT_LIST = "radio.segments.list"
EVENT_LISTEN = "radio.segment.listen"

_METADATA_FIELDS = frozenset({
    "schema_version", "segment_id", "channel", "direction", "started_at",
    "ended_at", "codec", "payload_type", "sample_rate_hz", "pcmu_packet_count",
    "transcript", "language", "model", "transcribed_at", "segments",
})
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
    """The segment, or its audio object, does not exist (HTTP 404)."""


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
    try:
        signature = base64.b64decode(str(signature_b64 or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise _unauthorized() from None
    if len(signature) != 64:
        raise _unauthorized()
    message = build_signed_message(timestamp, segment_id, audio, metadata)
    try:
        public_key.verify(signature, message)
    except InvalidSignature:
        raise _unauthorized() from None


# ------------------------------------------------------------ metadata check


def _bad_metadata(field: str | None = None) -> RadioIngestError:
    if field:
        return RadioIngestError(400, f"Metadata field '{field}' is invalid.")
    return RadioIngestError(400, "Metadata is invalid.")


def _loads_object(metadata: bytes) -> dict:
    def _reject_constant(_name: str):
        raise ValueError("non-finite number")

    try:
        parsed = json.loads(
            metadata.decode("utf-8"), parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _bad_metadata() from None
    if not isinstance(parsed, dict):
        raise _bad_metadata()
    return parsed


def extract_segment_id(metadata: bytes) -> str:
    """The one metadata field needed before the signature can be checked."""
    segment_id = _loads_object(metadata).get("segment_id")
    if not isinstance(segment_id, str) or not SEGMENT_ID_PATTERN.fullmatch(segment_id):
        raise _bad_metadata("segment_id")
    return segment_id


def _require_int(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad_metadata(field)
    if minimum is not None and value < minimum:
        raise _bad_metadata(field)
    return value


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_metadata(field)
    number = float(value)
    if not math.isfinite(number):
        raise _bad_metadata(field)
    return number


def _require_text(value: Any, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise _bad_metadata(field)
    return value


def _require_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise _bad_metadata(field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _bad_metadata(field) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _bad_metadata(field)
    return parsed.astimezone(timezone.utc)


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


def compute_low_confidence(asr_segments) -> bool:
    """True when any ASR segment looks unreliable or like non-speech."""
    for segment in asr_segments:
        if float(segment["avg_logprob"]) < LOW_CONFIDENCE_LOGPROB:
            return True
        if float(segment["no_speech_prob"]) > LOW_CONFIDENCE_NO_SPEECH:
            return True
    return False


def validate_metadata(metadata: bytes) -> RadioSegmentRecord:
    """Strictly validate the signed metadata document (schema_version 1)."""
    document = _loads_object(metadata)
    if set(document) - _METADATA_FIELDS:
        # Unknown fields are refused so the schema cannot drift silently.
        # Names are not echoed: they came from the sender.
        raise RadioIngestError(400, "Metadata contains unknown fields.")
    missing = _METADATA_FIELDS - set(document)
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
    )


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
                asr_segments, low_confidence, audio_key
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::JSONB, %s, %s
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
            ),
        )
        created = cursor.rowcount == 1
        self._commit()
        return created

    def purge_expired(self) -> int:
        """Delete transcript rows past retention. Never touches audit_events."""
        cursor = self._execute(
            "DELETE FROM lcdash_radio.segments "
            "WHERE received_at < now() - interval '30 days'"
        )
        deleted = max(int(cursor.rowcount or 0), 0)
        self._commit()
        return deleted

    _SEGMENT_COLUMNS = (
        "segment_id, channel, direction, started_at, ended_at, duration_seconds, "
        "transcript, language, model, low_confidence, audio_key, received_at"
    )

    def list_segments(
        self,
        *,
        channel: str | None,
        direction: str | None,
        like_pattern: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> list[tuple]:
        """Newest first, keyset-paged on (started_at, segment_id).

        Every user-supplied value travels as a bound parameter; the only SQL
        text assembled here is fixed fragments.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if channel:
            clauses.append("channel = %s")
            params.append(channel)
        if direction:
            clauses.append("direction = %s")
            params.append(direction)
        if like_pattern:
            clauses.append("transcript ILIKE %s ESCAPE '!'")
            params.append(like_pattern)
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


class RadioService:
    """Orchestrates S3 + Postgres for ingest and the admin reads.

    One instance is shared per process (see ``app.main._radio_service``): the
    schema is applied once and the hourly purge throttle lives here.
    """

    def __init__(
        self,
        *,
        bucket: str,
        audio_prefix: str,
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
                        repository.initialize_schema()
                        self._schema_ready = True
        except BaseException:
            repository.__exit__(None, None, None)
            raise
        return repository

    # ---------------------------------------------------------------- ingest

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
            self._maybe_purge(repository)
            return "created" if created else "duplicate"
        finally:
            repository.__exit__(None, None, None)

    def _maybe_purge(self, repository) -> None:
        """Lazy 30-day purge, at most once per hour per process."""
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

    # ----------------------------------------------------------------- reads

    def _item(self, row: tuple) -> dict:
        (segment_id, channel, direction, started_at, ended_at, duration, transcript,
         language, model, low_confidence, audio_key, received_at) = row
        audio_available = bool(audio_key) and received_at > (
            self._utcnow() - timedelta(days=AUDIO_RETENTION_DAYS)
        )
        return {
            "segment_id": segment_id,
            "channel": channel,
            "direction": direction,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": float(duration),
            "transcript": transcript,
            "language": language,
            "model": model,
            "low_confidence": bool(low_confidence),
            "audio_available": audio_available,
            "received_at": received_at.isoformat(),
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
    ) -> dict:
        channel = str(channel or "").strip()
        direction = str(direction or "").strip()
        q = str(q or "").strip()
        before = str(before or "").strip()
        if channel and not CHANNEL_PATTERN.fullmatch(channel):
            raise RadioQueryError("The 'channel' filter is invalid.")
        if direction and direction not in DIRECTIONS:
            raise RadioQueryError("The 'direction' filter is invalid.")
        if len(q) > MAX_QUERY_CHARS:
            raise RadioQueryError("The 'q' filter is too long.")
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
            rows = repository.list_segments(
                channel=channel or None,
                direction=direction or None,
                like_pattern=f"%{escape_like_pattern(q)}%" if q else None,
                cursor=cursor,
                limit=page_size + 1,
            )
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            items = [self._item(row) for row in rows]
            # Audited in the same connection AFTER the read and BEFORE the
            # result leaves this method: if the audit write fails, the caller
            # gets an error and no transcript.
            self._audit(
                repository, EVENT_LIST, actor, None,
                {
                    "channel": channel, "direction": direction,
                    "q": q[:100], "paged": bool(before),
                    "limit": page_size, "returned": len(items),
                },
                log_extra=(
                    f"channel={channel or '*'} direction={direction or '*'} "
                    f"has_q={bool(q)} paged={bool(before)} returned={len(items)}"
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

        Fails closed: the object is fetched, then the audit event is written,
        and the bytes are only returned if that write succeeded.
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
        finally:
            repository.__exit__(None, None, None)
        channels = [{"channel": name, "count": int(count)} for name, count in counts]
        return {
            "window_hours": STATS_WINDOW_HOURS,
            "channels": channels,
            "total": sum(item["count"] for item in channels),
            "last_received_at": newest.isoformat() if newest else None,
        }

    # ----------------------------------------------------------------- audit

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
