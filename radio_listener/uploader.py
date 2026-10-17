#!/usr/bin/env python3
"""Signed uploader for finished Mindshare radio segments.

Runs on the listener host (.227). It scans the transcripts written by
transcribe.py, and for each one it POSTs the audio plus a whitelisted metadata
document to the LCDash ingest endpoint, signed with an Ed25519 key that never
leaves this host. See docs/MINDSHARE_RADIO_INGEST.md for the contract.

Standard library only. Signing shells out to `openssl pkeyutl` (OpenSSL 3).
Nothing here transmits on the radio network or touches CAD. Transcript text and
audio are never written to the log.
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

DEFAULT_UPLOAD_CONFIG = {
    "endpoint": "https://aws.logan911.com/api/radio/ingest",
    "private_key": "/opt/mindshare-radio-capture/keys/ingest_ed25519.pem",
    "poll_seconds": 5,
    "max_bytes": 4194304,
    "local_retention_days": {"audio": 15, "transcripts": 30},
    "skip_segment_prefixes": ["pipeline_check_"],
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
METADATA_FIELDS = ("schema_version",) + SCALAR_FIELDS + ("segments",)

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


def load_configs(config_path: Path, upload_path: Path) -> tuple[Path, dict]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    root = Path(config["output_directory"])
    upload = dict(DEFAULT_UPLOAD_CONFIG)
    upload.update(json.loads(Path(upload_path).read_text(encoding="utf-8")))
    upload["endpoint"] = validate_endpoint(str(upload["endpoint"]))
    upload["poll_seconds"] = max(1, int(upload["poll_seconds"]))
    upload["max_bytes"] = int(upload["max_bytes"])
    upload["skip_segment_prefixes"] = [str(p) for p in upload.get("skip_segment_prefixes") or []]
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


def queued_transcripts(root: Path, prefixes) -> list[Path]:
    """Transcript files still to upload, oldest first."""
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
    return [item[2] for item in pending]


# ---------------------------------------------------------------- metadata


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


def post_segment(endpoint: str, body: bytes, content_type: str, timestamp: int, signature: str):
    """POST once. Returns (status, response_json_or_None, detail); status None on network error."""
    validate_endpoint(endpoint)
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "User-Agent": USER_AGENT,
            "X-Radio-Timestamp": str(int(timestamp)),
            "X-Radio-Signature": signature,
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(4096)
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


def process_queue(root: Path, upload: dict, stop=STOP, now=time.time) -> dict:
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
        if outcome == "retry":
            break
    return counts


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
    parser.add_argument("--dry-run", action="store_true",
                        help="print what local retention would delete, then exit; uploads nothing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        root, upload = load_configs(Path(args.config), Path(args.upload_config))
        for name in ("transcripts", "uploaded"):
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

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    log(f"Uploader ready; endpoint={upload['endpoint']} poll={upload['poll_seconds']}s")

    failures = 0
    last_retention = None
    exit_code = 0
    while not STOP.is_set():
        counts = process_queue(root, upload)
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
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
