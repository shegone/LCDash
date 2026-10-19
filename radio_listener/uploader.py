#!/usr/bin/env python3
"""Signed uploader for finished Mindshare radio segments.

Runs on the listener host (.227). It scans the transcripts written by
transcribe.py, and for each one it POSTs the audio plus a whitelisted metadata
document to the LCDash ingest endpoint, signed with an Ed25519 key that never
leaves this host. See docs/MINDSHARE_RADIO_INGEST.md for the contract.

A second, independent loop sends a signed health heartbeat every 60 s to
/api/radio/heartbeat (docs/MINDSHARE_RADIO_HEALTH.md) and stores the reply's
transcriber glossary in <root>/status/glossary.json.

Standard library only. Signing shells out to `openssl pkeyutl` (OpenSSL 3).
Nothing here transmits on the radio network or touches CAD. Transcript text,
audio and glossary text are never written to the log.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SIGNING_PREFIX = "lcdash-radio-v1"
USER_AGENT = "mindshare-radio-uploader/1"
HTTP_TIMEOUT_SECONDS = 30
OPENSSL_TIMEOUT_SECONDS = 20
BACKOFF_START_SECONDS = 5
BACKOFF_MAX_SECONDS = 300
RETENTION_INTERVAL_SECONDS = 3600
MAX_METADATA_BYTES = 64 * 1024
SEGMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")

HEARTBEAT_PREFIX = "lcdash-radio-heartbeat-v1"
HEARTBEAT_PATH = "/api/radio/heartbeat"
HEARTBEAT_SECONDS = 60
HEARTBEAT_READ_LIMIT = 16384
MAX_HEARTBEAT_BYTES = 16 * 1024
STALE_CAPTURE_SECONDS = 120
MAX_STATS_FILE_BYTES = 256 * 1024
MAX_HEARTBEAT_LISTENERS = 64
MAX_PAYLOAD_TYPE_KEYS = 16
STATUS_DIR_NAME = "status"
CAPTURE_STATS_FILE = "capture_stats.json"
GLOSSARY_FILE = "glossary.json"
GLOSSARY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MAX_HOTWORDS_CHARS = 1000
MAX_PROMPT_CHARS = 1300

DEFAULT_UPLOAD_CONFIG = {
    "endpoint": "https://aws.logan911.com/api/radio/ingest",
    "private_key": "/opt/mindshare-radio-capture/keys/ingest_ed25519.pem",
    "poll_seconds": 5,
    "max_bytes": 4194304,
    "local_retention_days": {"audio": 15, "transcripts": 30},
    "skip_segment_prefixes": ["pipeline_check_"],
    "heartbeat_endpoint": None,  # None: derived from `endpoint` (same host, /api/radio/heartbeat)
    "heartbeat_seconds": HEARTBEAT_SECONDS,
}

# The only transcript fields that may leave this host. Everything else in the
# transcript JSON (audio_file, source_packet_counts, multicast_group, port,
# language_probability, ...) is dropped by construction.
SCALAR_FIELDS = (
    "segment_id",
    "channel",
    "direction",
    "started_at",
    "ended_at",
    "codec",
    "payload_type",
    "sample_rate_hz",
    "pcmu_packet_count",
    "transcript",
    "language",
    "model",
    "transcribed_at",
)
SEGMENT_FIELDS = ("start", "end", "text", "avg_logprob", "no_speech_prob")
# Optional who-is-talking object (docs/MINDSHARE_RADIO_UNITS.md). Only these
# keys, each re-validated against the contract's pattern, may leave this host.
SIGNAL_FIELD_PATTERNS = {
    "radio_id": re.compile(r"[0-9]{1,10}"),
    "talkgroup": re.compile(r"G[0-9]{1,6}"),
    "console_label": re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}"),
}
METADATA_FIELDS = ("schema_version",) + SCALAR_FIELDS + ("segments", "signal")

STOP = threading.Event()


class LocalReject(Exception):
    """A segment that can never be uploaded as-is (permanent, local)."""


class SigningError(Exception):
    """Signing failed; the segment stays queued and is retried."""


def log(message: str) -> None:
    print(message, flush=True)


def stop_handler(signum, frame):
    STOP.set()


# ---------------------------------------------------------------- configuration


def validate_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme == "https" and parsed.hostname:
        return endpoint
    if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
        return endpoint
    raise ValueError("endpoint must be https (http is allowed only for 127.0.0.1)")


def derive_heartbeat_endpoint(endpoint: str) -> str:
    """Same scheme, host and port as the ingest endpoint, at /api/radio/heartbeat."""
    parsed = urllib.parse.urlparse(endpoint)
    return urllib.parse.urlunparse(parsed._replace(path=HEARTBEAT_PATH, params="", query="", fragment=""))


def heartbeat_url(upload: dict) -> str:
    return str(upload.get("heartbeat_endpoint") or derive_heartbeat_endpoint(upload["endpoint"]))


def load_configs(config_path: Path, upload_path: Path) -> tuple[Path, dict]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    root = Path(config["output_directory"])
    upload = dict(DEFAULT_UPLOAD_CONFIG)
    upload.update(json.loads(Path(upload_path).read_text(encoding="utf-8")))
    upload["endpoint"] = validate_endpoint(str(upload["endpoint"]))
    upload["poll_seconds"] = max(1, int(upload["poll_seconds"]))
    upload["max_bytes"] = int(upload["max_bytes"])
    upload["skip_segment_prefixes"] = [str(p) for p in upload.get("skip_segment_prefixes") or []]
    upload["heartbeat_endpoint"] = validate_endpoint(heartbeat_url(upload))
    upload["heartbeat_seconds"] = max(1, int(upload["heartbeat_seconds"]))
    return root, upload


# ---------------------------------------------------------------- files / markers


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def private_write(path: Path, contents: str) -> None:
    """Atomic 0600 write: temp file in the same directory, then rename."""
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.chmod(0o600)
    os.replace(temporary, path)


def marker_path(root: Path, segment_id: str, suffix: str) -> Path:
    return Path(root) / "uploaded" / f"{segment_id}.{suffix}"


def is_skipped(segment_id: str, prefixes) -> bool:
    return any(prefix and segment_id.startswith(prefix) for prefix in prefixes)


def has_final_marker(root: Path, segment_id: str) -> bool:
    return (
        marker_path(root, segment_id, "done").exists()
        or marker_path(root, segment_id, "rejected").exists()
    )


def write_done_marker(root: Path, segment_id: str, status: str) -> None:
    private_write(
        marker_path(root, segment_id, "done"),
        json.dumps({
            "segment_id": segment_id,
            "status": status,
            "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }),
    )


def write_rejected_marker(root: Path, segment_id: str, status, detail: str) -> None:
    private_write(
        marker_path(root, segment_id, "rejected"),
        json.dumps({
            "segment_id": segment_id,
            "status": status,
            "detail": str(detail)[:200],
            "rejected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }),
    )


def _queued_entries(root: Path, prefixes) -> list[tuple]:
    """(mtime, name, path) for transcripts still to upload, oldest first."""
    transcripts = Path(root) / "transcripts"
    pending = []
    for path in transcripts.glob("*.json"):
        segment_id = path.stem
        if is_skipped(segment_id, prefixes) or has_final_marker(root, segment_id):
            continue
        try:
            pending.append((path.stat().st_mtime, path.name, path))
        except FileNotFoundError:
            continue
    pending.sort()
    return pending


def queued_transcripts(root: Path, prefixes) -> list[Path]:
    """Transcript files still to upload, oldest first."""
    return [item[2] for item in _queued_entries(root, prefixes)]


def queue_summary(root: Path, prefixes, now: float) -> tuple[int, int | None]:
    """(queued segment count, age in seconds of the oldest one or None)."""
    entries = _queued_entries(root, prefixes)
    if not entries:
        return 0, None
    return len(entries), max(0, int(now - entries[0][0]))


# ---------------------------------------------------------------- metadata


def sanitize_signal(value) -> dict | None:
    """Keep only valid radio_id / talkgroup / console_label strings; None when nothing is left."""
    if not isinstance(value, dict):
        return None
    clean = {}
    for key, pattern in SIGNAL_FIELD_PATTERNS.items():
        item = value.get(key)
        if isinstance(item, str) and pattern.fullmatch(item):
            clean[key] = item
    return clean or None


def build_metadata(transcript: dict) -> dict:
    """Whitelist the transcript JSON down to the contract's metadata fields."""
    if not isinstance(transcript, dict):
        raise LocalReject("transcript file is not a JSON object")
    version = transcript.get("schema_version", 1)
    if version != 1:
        raise LocalReject("unsupported schema_version")
    metadata = {"schema_version": 1}
    missing = [name for name in SCALAR_FIELDS if name not in transcript]
    if missing or not isinstance(transcript.get("segments"), list):
        raise LocalReject("transcript is missing required fields")
    for name in SCALAR_FIELDS:
        metadata[name] = transcript[name]
    metadata["segments"] = [
        {key: item[key] for key in SEGMENT_FIELDS if key in item}
        for item in transcript["segments"]
        if isinstance(item, dict)
    ]
    signal = sanitize_signal(transcript.get("signal"))
    if signal:
        metadata["signal"] = signal
    return metadata


def serialize_metadata(metadata: dict) -> bytes:
    """Serialize exactly once; these are the bytes that are signed and sent."""
    return json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def resolve_audio(root: Path, audio_file) -> Path:
    """Same containment rule as transcribe.py: the WAV must live under <root>/audio."""
    audio_path = Path(str(audio_file))
    expected_root = (Path(root) / "audio").resolve()
    if expected_root not in audio_path.resolve().parents:
        raise LocalReject("audio file is outside the configured audio directory")
    return audio_path


def read_audio(root: Path, audio_file, max_bytes: int) -> bytes:
    audio_path = resolve_audio(root, audio_file)
    try:
        size = audio_path.stat().st_size
    except FileNotFoundError as exc:
        raise LocalReject("audio file is missing") from exc
    if size > max_bytes:
        raise LocalReject("audio exceeds the maximum upload size")
    data = audio_path.read_bytes()
    if len(data) > max_bytes:
        raise LocalReject("audio exceeds the maximum upload size")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LocalReject("audio is not a RIFF/WAVE file")
    return data


# ---------------------------------------------------------------- signing


def build_signing_message(timestamp, segment_id: str, audio_bytes: bytes, metadata_bytes: bytes) -> bytes:
    return "\n".join([
        SIGNING_PREFIX,
        str(int(timestamp)),
        segment_id,
        hashlib.sha256(audio_bytes).hexdigest(),
        hashlib.sha256(metadata_bytes).hexdigest(),
    ]).encode("utf-8")


def sign_message(private_key: Path, message: bytes, work_dir: Path | None = None) -> str:
    """Ed25519-sign `message` with openssl; returns base64 of the signature.

    The message goes through a 0600 temp file (deleted afterwards) because
    `-rawin` needs a seekable input. The directory must be writable under the
    service sandbox, so the caller passes one inside the data root.
    """
    fd, name = tempfile.mkstemp(prefix=".sign-", suffix=".msg", dir=str(work_dir) if work_dir else None)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(message)
        try:
            result = subprocess.run(
                ["openssl", "pkeyutl", "-sign", "-inkey", str(private_key), "-rawin", "-in", name],
                capture_output=True,
                timeout=OPENSSL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SigningError(f"openssl could not run: {type(exc).__name__}") from exc
        if result.returncode != 0 or not result.stdout:
            raise SigningError(f"openssl signing failed (exit {result.returncode})")
        return base64.b64encode(result.stdout).decode("ascii")
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- HTTP


def build_multipart(metadata_bytes: bytes, audio_bytes: bytes, segment_id: str, boundary: str | None = None):
    boundary = boundary or secrets.token_hex(16)
    delimiter = f"--{boundary}".encode("ascii")
    body = b"".join([
        delimiter, b"\r\n",
        b'Content-Disposition: form-data; name="metadata"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        metadata_bytes, b"\r\n",
        delimiter, b"\r\n",
        f'Content-Disposition: form-data; name="audio"; filename="{segment_id}.wav"\r\n'.encode("ascii"),
        b"Content-Type: audio/wav\r\n\r\n",
        audio_bytes, b"\r\n",
        delimiter, b"--\r\n",
    ])
    return body, f"multipart/form-data; boundary={boundary}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def classify_status(status) -> str:
    """'done' (200/201), 'rejected' (400/413, permanent), else 'retry'."""
    if status in (200, 201):
        return "done"
    if status in (400, 413):
        return "rejected"
    return "retry"


def backoff_delay(failures: int) -> int:
    """5 s doubling to a 5 minute ceiling. `failures` is the count so far."""
    return min(BACKOFF_START_SECONDS * (2 ** max(0, failures)), BACKOFF_MAX_SECONDS)


def _response_detail(raw: bytes) -> str:
    try:
        return str(json.loads(raw.decode("utf-8")).get("detail", ""))[:200]
    except Exception:
        return ""


def _http_post(endpoint: str, body: bytes, headers: dict, read_limit: int):
    """POST once, never following redirects. Returns (status, response_json_or_None, detail);
    status None on network error. TLS is verified by urllib for https endpoints."""
    validate_endpoint(endpoint)
    request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(read_limit)
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(4096)
        except Exception:
            raw = b""
        return exc.code, None, _response_detail(raw)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, type(reason).__name__
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = None
    return status, parsed, ""


def post_segment(endpoint: str, body: bytes, content_type: str, timestamp: int, signature: str):
    """POST once. Returns (status, response_json_or_None, detail); status None on network error."""
    return _http_post(endpoint, body, {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "User-Agent": USER_AGENT,
        "X-Radio-Timestamp": str(int(timestamp)),
        "X-Radio-Signature": signature,
    }, 4096)


def post_heartbeat(endpoint: str, body: bytes, timestamp: int, signature: str):
    """POST one heartbeat. Same return shape and redirect/TLS rules as post_segment."""
    return _http_post(endpoint, body, {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "User-Agent": USER_AGENT,
        "X-Radio-Timestamp": str(int(timestamp)),
        "X-Radio-Signature": signature,
    }, HEARTBEAT_READ_LIMIT)


# ---------------------------------------------------------------- processing


def process_segment(root: Path, upload: dict, transcript_path: Path, now=time.time) -> str:
    """Upload one transcript. Returns 'done', 'rejected' or 'retry'."""
    segment_id = transcript_path.stem
    root = Path(root)
    try:
        if not SEGMENT_ID_PATTERN.match(segment_id):
            raise LocalReject("segment id does not match the contract pattern")
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LocalReject("transcript file is not valid JSON") from exc
        if isinstance(transcript, dict) and transcript.get("segment_id") != segment_id:
            raise LocalReject("segment id does not match the file name")
        metadata = build_metadata(transcript)
        metadata_bytes = serialize_metadata(metadata)
        if len(metadata_bytes) > MAX_METADATA_BYTES:
            raise LocalReject("metadata exceeds 64 KiB")
        audio_bytes = read_audio(root, transcript.get("audio_file", ""), upload["max_bytes"])
    except LocalReject as reject:
        write_rejected_marker(root, segment_id, "local", str(reject))
        log(f"segment_id={segment_id} status=local-rejected bytes=0")
        return "rejected"

    body, content_type = build_multipart(metadata_bytes, audio_bytes, segment_id)
    timestamp = int(now())
    message = build_signing_message(timestamp, segment_id, audio_bytes, metadata_bytes)
    try:
        signature = sign_message(upload["private_key"], message, root / "uploaded")
    except SigningError as exc:
        log(f"segment_id={segment_id} status=sign-error bytes={len(body)} error={exc}")
        return "retry"

    status, parsed, detail = post_segment(upload["endpoint"], body, content_type, timestamp, signature)
    log(f"segment_id={segment_id} status={status if status is not None else 'network-error'} bytes={len(body)}")
    outcome = classify_status(status)
    if outcome == "done":
        result = parsed.get("status") if isinstance(parsed, dict) else None
        write_done_marker(root, segment_id, str(result or status))
    elif outcome == "rejected":
        write_rejected_marker(root, segment_id, status, detail)
    return outcome


def process_queue(root: Path, upload: dict, stop=STOP, now=time.time, stats=None) -> dict:
    """One pass over the queue. Stops at the first retryable failure so order is kept."""
    counts = {"done": 0, "rejected": 0, "retry": 0}
    for path in queued_transcripts(root, upload["skip_segment_prefixes"]):
        if stop.is_set():
            break
        try:
            outcome = process_segment(root, upload, path, now=now)
        except Exception as exc:  # unexpected I/O problem: keep the segment queued
            log(f"segment_id={path.stem} status=error bytes=0 error={type(exc).__name__}")
            outcome = "retry"
        counts[outcome] += 1
        if stats is not None:
            stats.record(outcome, now())
        if outcome == "retry":
            break
    return counts


# ---------------------------------------------------------------- heartbeat


class UploaderStats:
    """The uploader's own counters (reset when the process restarts); thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.rejected_total = 0
        self.last_success_epoch = None

    def record(self, outcome: str, when: float) -> None:
        with self._lock:
            if outcome == "rejected":
                self.rejected_total += 1
            elif outcome == "done":
                self.last_success_epoch = when

    def snapshot(self) -> tuple[int, str | None]:
        with self._lock:
            last = self.last_success_epoch
            return self.rejected_total, _iso_from_epoch(last)


def _iso_from_epoch(value) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).replace(microsecond=0).isoformat()


def build_heartbeat_signing_message(timestamp, body: bytes) -> bytes:
    return "\n".join([
        HEARTBEAT_PREFIX,
        str(int(timestamp)),
        hashlib.sha256(body).hexdigest(),
    ]).encode("utf-8")


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return 0
    return max(0, int(value))


def _parse_iso(value):
    """Aware datetime for an ISO-8601 string with an offset, else None."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _iso_or_none(value) -> str | None:
    return value if _parse_iso(value) is not None else None


def empty_capture() -> dict:
    return {"stats_at": None, "started_at": None, "uptime_seconds": 0,
            "interval_seconds": 0, "listeners": []}


def _clean_listener(row):
    if not isinstance(row, dict):
        return None
    channel, direction = row.get("channel"), row.get("direction")
    if not isinstance(channel, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", channel):
        return None
    if direction not in ("rx", "tx"):
        return None
    other = {}
    raw_other = row.get("other_payload_types")
    if isinstance(raw_other, dict):
        for key, count in raw_other.items():
            if len(other) >= MAX_PAYLOAD_TYPE_KEYS:
                break
            if isinstance(key, str) and re.fullmatch(r"[0-9]{1,3}", key):
                other[key] = _count(count)
    return {
        "channel": channel,
        "direction": direction,
        "packets_total": _count(row.get("packets_total")),
        "packets_interval": _count(row.get("packets_interval")),
        "pcmu_packets_total": _count(row.get("pcmu_packets_total")),
        "other_payload_types": other,
        "bytes_interval": _count(row.get("bytes_interval")),
        "sources_seen": _count(row.get("sources_seen")),
        "segments_closed_total": _count(row.get("segments_closed_total")),
        "last_packet_at": _iso_or_none(row.get("last_packet_at")),
    }


def read_capture_stats(root: Path) -> dict:
    """capture_stats.json reduced to the heartbeat's `capture` shape; empty if missing or corrupt."""
    path = Path(root) / STATUS_DIR_NAME / CAPTURE_STATS_FILE
    try:
        if path.stat().st_size > MAX_STATS_FILE_BYTES:
            return empty_capture()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_capture()
    if not isinstance(raw, dict):
        return empty_capture()
    listeners = []
    if isinstance(raw.get("listeners"), list):
        for row in raw["listeners"][:MAX_HEARTBEAT_LISTENERS]:
            cleaned = _clean_listener(row)
            if cleaned is not None:
                listeners.append(cleaned)
    return {
        "stats_at": _iso_or_none(raw.get("stats_at")),
        "started_at": _iso_or_none(raw.get("started_at")),
        "uptime_seconds": _count(raw.get("uptime_seconds")),
        "interval_seconds": _count(raw.get("interval_seconds")),
        "listeners": listeners,
    }


def _count_json_files(directory: Path) -> int:
    try:
        return sum(1 for _ in Path(directory).glob("*.json"))
    except OSError:
        return 0


def current_glossary_version(root: Path) -> str | None:
    """Version in <root>/status/glossary.json, or None if missing, corrupt or invalid."""
    try:
        data = json.loads((Path(root) / STATUS_DIR_NAME / GLOSSARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, str) and GLOSSARY_VERSION_PATTERN.match(version):
        return version
    return None


def build_heartbeat(root: Path, upload: dict, stats: UploaderStats | None, now: float) -> dict:
    """Heartbeat body (schema_version 1). Counts and timestamps only: no text, audio or IPs."""
    root = Path(root)
    now_dt = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    capture = read_capture_stats(root)
    stats_at = _parse_iso(capture["stats_at"])
    if stats_at is None or (now_dt - stats_at).total_seconds() > STALE_CAPTURE_SECONDS:
        # Stale (or never written): send no listener rows but keep stats_at as is,
        # so the server can see how old the data is.
        capture["listeners"] = []
    queued, oldest_age = queue_summary(root, upload.get("skip_segment_prefixes") or [], now)
    rejected_total, last_success_at = stats.snapshot() if stats is not None else (0, None)
    queue_dir = root / "queue"
    return {
        "schema_version": 1,
        "sent_at": now_dt.replace(microsecond=0).isoformat(),
        "capture": capture,
        "transcriber": {
            "pending": _count_json_files(queue_dir / "pending") + _count_json_files(queue_dir / "processing"),
            "failed": _count_json_files(queue_dir / "failed"),
        },
        "uploader": {
            "queued": queued,
            "oldest_queued_age_seconds": oldest_age,
            "rejected_total": rejected_total,
            "last_success_at": last_success_at,
        },
        "glossary_version": current_glossary_version(root),
    }


def serialize_heartbeat(heartbeat: dict) -> bytes:
    """Serialize once; these bytes are signed and sent. Trims listener rows to fit 16 KiB."""
    body = serialize_metadata(heartbeat)
    listeners = heartbeat["capture"]["listeners"]
    while len(body) > MAX_HEARTBEAT_BYTES and listeners:
        listeners.pop()
        body = serialize_metadata(heartbeat)
    return body


def apply_glossary_reply(root: Path, reply) -> str | None:
    """Store the reply's glossary when its version differs. Returns the new version, else None."""
    glossary = reply.get("glossary") if isinstance(reply, dict) else None
    if not isinstance(glossary, dict):
        return None
    version = glossary.get("version")
    if not isinstance(version, str) or not GLOSSARY_VERSION_PATTERN.match(version):
        return None
    if version == current_glossary_version(root):
        return None
    hotwords, prompt = glossary.get("hotwords"), glossary.get("prompt")
    if not isinstance(hotwords, str) or not isinstance(prompt, str):
        return None
    status_dir = Path(root) / STATUS_DIR_NAME
    ensure_private_dir(status_dir)
    private_write(status_dir / GLOSSARY_FILE, json.dumps({
        "version": version,
        "hotwords": re.sub(r"[\x00-\x1f\x7f]+", " ", hotwords).strip()[:MAX_HOTWORDS_CHARS],
        "prompt": re.sub(r"[\x00-\x1f\x7f]+", " ", prompt).strip()[:MAX_PROMPT_CHARS],
    }))
    return version


class HeartbeatSender:
    """Independent heartbeat loop: its own thread, timing and backoff.

    Nothing here shares a failure path with segment uploads: it never touches
    the segment queue markers, and segment uploads never wait for it.
    """

    def __init__(self, root: Path, upload: dict, stats: UploaderStats | None = None,
                 stop=None, now=time.time, poster=None, signer=None):
        self.root = Path(root)
        self.upload = upload
        self.stats = stats
        self.stop = stop if stop is not None else STOP
        self.now = now
        self.poster = poster
        self.signer = signer
        self.failures = 0
        self._last_logged = object()

    def _log_status(self, status) -> None:
        if status != self._last_logged:
            self._last_logged = status
            log(f"heartbeat status={status}")

    def send_once(self) -> bool:
        """Build, sign and POST one heartbeat. True only for a 200 reply. Never raises."""
        poster = self.poster or post_heartbeat
        signer = self.signer or sign_message
        try:
            when = self.now()
            body = serialize_heartbeat(build_heartbeat(self.root, self.upload, self.stats, when))
            timestamp = int(when)
            message = build_heartbeat_signing_message(timestamp, body)
            status_dir = self.root / STATUS_DIR_NAME
            ensure_private_dir(status_dir)
            signature = signer(self.upload["private_key"], message, status_dir)
            status, parsed, _detail = poster(heartbeat_url(self.upload), body, timestamp, signature)
        except SigningError:
            self._log_status("sign-error")
            return False
        except Exception as exc:  # keep the loop alive whatever happens
            self._log_status(f"error-{type(exc).__name__}")
            return False
        self._log_status(status if status is not None else "network-error")
        if status != 200:
            return False
        try:
            updated = apply_glossary_reply(self.root, parsed)
        except Exception as exc:
            log(f"heartbeat glossary-error={type(exc).__name__}")
        else:
            if updated:
                log(f"heartbeat glossary updated version={updated}")
        return True

    def run(self) -> None:
        interval = float(self.upload.get("heartbeat_seconds") or HEARTBEAT_SECONDS)
        while not self.stop.is_set():
            if self.send_once():
                self.failures = 0
                delay = interval
            else:
                # Own backoff: 5 s doubling, but never slower than the normal cadence,
                # so heartbeats resume within a minute of the server returning.
                delay = min(backoff_delay(self.failures), interval)
                self.failures += 1
            self.stop.wait(delay)


# ---------------------------------------------------------------- local retention


def _inside(path: Path, directory: Path) -> bool:
    try:
        return directory.resolve() in path.resolve().parents
    except OSError:
        return False


def run_retention(root: Path, retention: dict, now: float | None = None, dry_run: bool = False) -> list[Path]:
    """Delete old local copies, but only for segments already marked .done.

    Audio older than retention['audio'] days; transcript JSONs and .done
    markers older than retention['transcripts'] days. Never touches anything
    outside <root>/audio, <root>/transcripts and <root>/uploaded.
    """
    root = Path(root)
    now = time.time() if now is None else now
    audio_dir, transcript_dir, uploaded_dir = root / "audio", root / "transcripts", root / "uploaded"
    audio_cutoff = now - float(retention["audio"]) * 86400
    transcript_cutoff = now - float(retention["transcripts"]) * 86400
    removed: list[Path] = []

    def remove(path: Path) -> None:
        if not dry_run:
            path.unlink(missing_ok=True)
        removed.append(path)

    def done(segment_id: str) -> bool:
        return marker_path(root, segment_id, "done").is_file()

    if audio_dir.is_dir():
        for path in audio_dir.rglob("*.wav"):
            if path.is_symlink() or not path.is_file() or not _inside(path, audio_dir):
                continue
            if path.stat().st_mtime < audio_cutoff and done(path.stem):
                remove(path)

    gone: set[str] = set()
    if transcript_dir.is_dir():
        for path in transcript_dir.glob("*.json"):
            if path.is_symlink() or not path.is_file() or not _inside(path, transcript_dir):
                continue
            if path.stat().st_mtime < transcript_cutoff and done(path.stem):
                remove(path)
                gone.add(path.stem)

    if uploaded_dir.is_dir():
        for path in uploaded_dir.glob("*.done"):
            if path.is_symlink() or not path.is_file() or not _inside(path, uploaded_dir):
                continue
            segment_id = path.stem
            if path.stat().st_mtime >= transcript_cutoff:
                continue
            # Keep the marker while its transcript still exists, or it would be re-uploaded.
            if (transcript_dir / f"{segment_id}.json").exists() and segment_id not in gone:
                continue
            remove(path)
    return removed


# ---------------------------------------------------------------- main loop


def parse_args(argv=None):
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Upload finished radio segments to LCDash.")
    parser.add_argument("--config", default=str(here / "config.json"))
    parser.add_argument("--upload-config", default=str(here / "upload_config.json"))
    parser.add_argument("--once", action="store_true", help="process the queue once and exit")
    parser.add_argument("--heartbeat-once", action="store_true",
                        help="send one health heartbeat, print its status and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what local retention would delete, then exit; uploads nothing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        root, upload = load_configs(Path(args.config), Path(args.upload_config))
        for name in ("transcripts", "uploaded", STATUS_DIR_NAME):
            ensure_private_dir(root / name)
    except Exception as exc:
        raise SystemExit(f"Cannot initialize uploader: {exc}") from exc

    retention = upload.get("local_retention_days")
    if args.dry_run:
        if not retention:
            log("Local retention is not enabled in the upload config; nothing to do.")
            return 0
        for path in run_retention(root, retention, dry_run=True):
            log(f"would delete {path}")
        return 0

    if args.heartbeat_once:
        return 0 if HeartbeatSender(root, upload, UploaderStats()).send_once() else 1

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    log(f"Uploader ready; endpoint={upload['endpoint']} poll={upload['poll_seconds']}s "
        f"heartbeat={upload['heartbeat_seconds']}s")

    stats = UploaderStats()
    heartbeat_thread = None
    if not args.once:
        heartbeat_thread = threading.Thread(
            target=HeartbeatSender(root, upload, stats).run, name="heartbeat", daemon=True)
        heartbeat_thread.start()

    failures = 0
    last_retention = None
    exit_code = 0
    while not STOP.is_set():
        counts = process_queue(root, upload, stats=stats)
        if counts["retry"]:
            delay = backoff_delay(failures)
            failures += 1
            exit_code = 1
            log(f"upload deferred; retrying in {delay}s")
        else:
            failures = 0
            exit_code = 0
            delay = upload["poll_seconds"]

        if retention and (last_retention is None or time.monotonic() - last_retention >= RETENTION_INTERVAL_SECONDS):
            last_retention = time.monotonic()
            try:
                removed = run_retention(root, retention)
                if removed:
                    log(f"retention removed {len(removed)} local files")
            except Exception as exc:
                log(f"retention error: {type(exc).__name__}")

        if args.once:
            break
        STOP.wait(delay)
    if heartbeat_thread is not None:
        heartbeat_thread.join(timeout=5)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
