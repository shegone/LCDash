"""Tests for radio_listener/uploader.py (synthetic data only, no external network)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

UPLOADER_PATH = Path(__file__).resolve().parents[1] / "radio_listener" / "uploader.py"
_spec = importlib.util.spec_from_file_location("radio_listener_uploader", UPLOADER_PATH)
uploader = importlib.util.module_from_spec(_spec)
sys.modules["radio_listener_uploader"] = uploader
_spec.loader.exec_module(uploader)

# Golden vector from docs/MINDSHARE_RADIO_INGEST.md
GOLDEN_SEED_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
GOLDEN_TIMESTAMP = 1760000000
GOLDEN_SEGMENT_ID = "law_rx_test"
GOLDEN_METADATA = b'{"segment_id":"law_rx_test"}'
GOLDEN_AUDIO = b"RIFFtest"
GOLDEN_METADATA_SHA = "b67cc69651e0b68ce1d17c5832020ff280730b8b4ed2ca1e29ef962fba2be714"
GOLDEN_AUDIO_SHA = "79ab531fc8e36fb4aa47a2adf03f3535e971e8dc5a8d84f7db4537cdc5535255"
GOLDEN_SIGNATURE = (
    "950QFKVROQjFxt0qIs+S00kgBfYEa6etdYGkd9DGQIaPuUHpmYFoZcniKxlA3mSfXaeJngm9X1zst47K2ssrBw=="
)

SYNTHETIC_TEXT = "synthetic test transmission alpha bravo"
FORBIDDEN_FIELDS = (
    "audio_file", "source_packet_counts", "multicast_group", "port", "language_probability",
)


# ------------------------------------------------------------------ helpers


def openssl_has_ed25519() -> bool:
    exe = shutil.which("openssl")
    if not exe:
        return False
    try:
        result = subprocess.run([exe, "list", "-public-key-algorithms"],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return "ED25519" in result.stdout.upper()


needs_openssl = pytest.mark.skipif(not openssl_has_ed25519(), reason="openssl with Ed25519 not available")


def golden_key_pem(directory: Path) -> Path:
    der = bytes.fromhex("302e020100300506032b657004220420") + bytes.fromhex(GOLDEN_SEED_HEX)
    body = base64.encodebytes(der).decode("ascii")
    path = directory / "golden_ed25519.pem"
    path.write_text(f"-----BEGIN PRIVATE KEY-----\n{body}-----END PRIVATE KEY-----\n", encoding="ascii")
    return path


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for name in ("audio/law", "transcripts", "uploaded"):
        (root / name).mkdir(parents=True)
    return root


def wav_bytes(extra: int = 16) -> bytes:
    return b"RIFF" + (36 + extra).to_bytes(4, "little") + b"WAVE" + b"\x00" * extra


def write_segment(root: Path, segment_id: str, *, text: str = SYNTHETIC_TEXT, age_days: float = 0.0,
                  audio: bytes | None = None, extra: dict | None = None) -> Path:
    audio_path = root / "audio" / "law" / f"{segment_id}.wav"
    audio_path.write_bytes(wav_bytes() if audio is None else audio)
    transcript = {
        "segment_id": segment_id,
        "channel": "law",
        "direction": "rx",
        "multicast_group": "235.98.99.32",
        "port": 12032,
        "codec": "PCMU",
        "payload_type": 0,
        "sample_rate_hz": 8000,
        "audio_file": str(audio_path),
        "started_at": "2026-10-16T12:00:00+00:00",
        "ended_at": "2026-10-16T12:00:05+00:00",
        "pcmu_packet_count": 250,
        "source_packet_counts": {"10.0.0.1": 250},
        "transcript": text,
        "language": "en",
        "language_probability": 1.0,
        "model": "large-v3",
        "transcribed_at": "2026-10-16T12:00:09+00:00",
        "segments": [{"start": 0.0, "end": 2.5, "text": text, "avg_logprob": -0.3,
                      "no_speech_prob": 0.01, "tokens": [1, 2, 3], "seek": 0}],
    }
    transcript.update(extra or {})
    path = root / "transcripts" / f"{segment_id}.json"
    path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    if age_days:
        stamp = time.time() - age_days * 86400
        for target in (path, audio_path):
            os.utime(target, (stamp, stamp))
    return path


class FakeIngest:
    """Loopback server that records requests and answers from a status script."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status = owner.statuses.pop(0) if len(owner.statuses) > 1 else owner.statuses[0]
                if status == 201:
                    payload = {"status": "created"}
                elif status == 200:
                    payload = {"status": "duplicate"}
                else:
                    payload = {"detail": "scripted failure"}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/radio/ingest"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def upload_settings(endpoint: str, key: str = "unused.pem") -> dict:
    settings = dict(uploader.DEFAULT_UPLOAD_CONFIG)
    settings.update({"endpoint": endpoint, "private_key": key})
    return settings


@pytest.fixture
def fake_sign(monkeypatch):
    monkeypatch.setattr(uploader, "sign_message", lambda *args, **kwargs: "c2lnbmF0dXJl")


def split_multipart(body: bytes, content_type: str) -> dict:
    boundary = content_type.split("boundary=", 1)[1].encode()
    parts = {}
    for chunk in body.split(b"--" + boundary)[1:-1]:
        head, _, payload = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        assert payload.endswith(b"\r\n")
        headers = head.decode().split("\r\n")
        name = headers[0].split('name="', 1)[1].split('"', 1)[0]
        parts[name] = {"headers": headers, "payload": payload[:-2]}
    return parts


# ------------------------------------------------------------------ metadata


def test_metadata_whitelist_drops_forbidden_and_unknown_fields(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3",
                         extra={"surprise_field": "x"})
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert set(metadata) <= set(uploader.METADATA_FIELDS)
    assert metadata["schema_version"] == 1
    for name in FORBIDDEN_FIELDS + ("surprise_field",):
        assert name not in metadata
    assert set(metadata["segments"][0]) == {"start", "end", "text", "avg_logprob", "no_speech_prob"}
    serialized = uploader.serialize_metadata(metadata).decode("utf-8")
    assert "235.98.99.32" not in serialized and "10.0.0.1" not in serialized
    assert str(tmp_path) not in serialized


def test_metadata_missing_required_field_is_rejected(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["model"]
    with pytest.raises(uploader.LocalReject):
        uploader.build_metadata(data)


def test_serialization_is_deterministic_and_compact():
    one = uploader.serialize_metadata({"b": 1, "a": "café", "c": [1, {"z": 0, "y": 1}]})
    two = uploader.serialize_metadata({"c": [1, {"y": 1, "z": 0}], "a": "café", "b": 1})
    assert one == two
    assert one == '{"a":"café","b":1,"c":[1,{"y":1,"z":0}]}'.encode("utf-8")
    assert uploader.serialize_metadata({"segment_id": "law_rx_test"}) == GOLDEN_METADATA


# ------------------------------------------------------------------ signing


def test_signing_message_matches_golden_vector():
    assert hashlib.sha256(GOLDEN_METADATA).hexdigest() == GOLDEN_METADATA_SHA
    assert hashlib.sha256(GOLDEN_AUDIO).hexdigest() == GOLDEN_AUDIO_SHA
    message = uploader.build_signing_message(
        GOLDEN_TIMESTAMP, GOLDEN_SEGMENT_ID, GOLDEN_AUDIO, GOLDEN_METADATA)
    assert message == (
        "lcdash-radio-v1\n1760000000\nlaw_rx_test\n"
        f"{GOLDEN_AUDIO_SHA}\n{GOLDEN_METADATA_SHA}"
    ).encode("utf-8")
    assert not message.endswith(b"\n")


@needs_openssl
def test_openssl_signature_matches_golden_vector(tmp_path):
    key = golden_key_pem(tmp_path)
    message = uploader.build_signing_message(
        GOLDEN_TIMESTAMP, GOLDEN_SEGMENT_ID, GOLDEN_AUDIO, GOLDEN_METADATA)
    work = tmp_path / "work"
    work.mkdir()
    assert uploader.sign_message(key, message, work) == GOLDEN_SIGNATURE
    assert list(work.iterdir()) == []  # the message temp file is deleted


def test_sign_message_failure_raises_and_cleans_up(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(uploader.SigningError):
        uploader.sign_message(tmp_path / "no_such_key.pem", b"message", work)
    assert list(work.iterdir()) == []


# ------------------------------------------------------------------ multipart


def test_multipart_structure():
    body, content_type = uploader.build_multipart(
        GOLDEN_METADATA, GOLDEN_AUDIO, GOLDEN_SEGMENT_ID, boundary="testboundary")
    assert content_type == "multipart/form-data; boundary=testboundary"
    parts = split_multipart(body, content_type)
    assert list(parts) == ["metadata", "audio"]
    assert parts["metadata"]["payload"] == GOLDEN_METADATA
    assert "Content-Type: application/json" in parts["metadata"]["headers"]
    assert parts["audio"]["payload"] == GOLDEN_AUDIO
    assert 'filename="law_rx_test.wav"' in parts["audio"]["headers"][0]
    assert "Content-Type: audio/wav" in parts["audio"]["headers"]
    assert body.endswith(b"--testboundary--\r\n")


# ------------------------------------------------------------------ markers, skip, backoff


def test_queue_skips_markers_and_prefixes_and_orders_oldest_first(tmp_path):
    root = make_root(tmp_path)
    newest = write_segment(root, "law_rx_20261016T120300Z_ccc333", age_days=0.1)
    oldest = write_segment(root, "law_rx_20261016T120100Z_aaa111", age_days=0.3)
    done = write_segment(root, "law_rx_20261016T120200Z_bbb222", age_days=0.2)
    rejected = write_segment(root, "law_rx_20261016T120250Z_ddd444", age_days=0.25)
    write_segment(root, "pipeline_check_law_20261016")
    uploader.write_done_marker(root, done.stem, "created")
    uploader.write_rejected_marker(root, rejected.stem, 400, "bad")
    queue = uploader.queued_transcripts(root, ["pipeline_check_"])
    assert [path.stem for path in queue] == [oldest.stem, newest.stem]


def test_skip_prefix_logic():
    assert uploader.is_skipped("pipeline_check_x", ["pipeline_check_"])
    assert not uploader.is_skipped("law_rx_pipeline_check_x", ["pipeline_check_"])
    assert not uploader.is_skipped("anything_goes", [])
    assert not uploader.is_skipped("anything_goes", [""])


def test_marker_files_are_private(tmp_path):
    root = make_root(tmp_path)
    uploader.write_done_marker(root, "law_rx_20261016T120000Z_a1b2c3", "created")
    marker = uploader.marker_path(root, "law_rx_20261016T120000Z_a1b2c3", "done")
    assert marker.exists()
    assert not list((root / "uploaded").glob("*.tmp"))
    if os.name == "posix":
        assert marker.stat().st_mode & 0o777 == 0o600


def test_backoff_schedule():
    assert [uploader.backoff_delay(n) for n in range(9)] == [5, 10, 20, 40, 80, 160, 300, 300, 300]


def test_classify_status():
    assert uploader.classify_status(201) == "done"
    assert uploader.classify_status(200) == "done"
    assert uploader.classify_status(400) == "rejected"
    assert uploader.classify_status(413) == "rejected"
    for status in (401, 403, 404, 429, 500, 503, None):
        assert uploader.classify_status(status) == "retry"


def test_endpoint_validation():
    assert uploader.validate_endpoint("https://aws.logan911.com/api/radio/ingest")
    assert uploader.validate_endpoint("http://127.0.0.1:8080/x")
    for bad in ("http://aws.logan911.com/api/radio/ingest", "http://localhost:8080/x",
                "ftp://127.0.0.1/x", "aws.logan911.com", "https://"):
        with pytest.raises(ValueError):
            uploader.validate_endpoint(bad)


def test_load_configs_reads_repo_files():
    here = UPLOADER_PATH.parent
    root, upload = uploader.load_configs(here / "config.json", here / "upload_config.json")
    assert root == Path("/var/lib/mindshare-radio-capture/data")
    assert upload["endpoint"].startswith("https://")
    assert upload["local_retention_days"] == {"audio": 15, "transcripts": 30}
    assert upload["skip_segment_prefixes"] == ["pipeline_check_"]


# ------------------------------------------------------------------ traversal


def test_audio_traversal_rejected(tmp_path):
    root = make_root(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(wav_bytes())
    inside = root / "audio" / "law" / "ok_segment_1.wav"
    inside.write_bytes(wav_bytes())
    with pytest.raises(uploader.LocalReject):
        uploader.resolve_audio(root, outside)
    with pytest.raises(uploader.LocalReject):
        uploader.resolve_audio(root, root / "audio" / ".." / ".." / "outside.wav")
    with pytest.raises(uploader.LocalReject):
        uploader.resolve_audio(root, root / "audio")
    assert uploader.resolve_audio(root, inside) == inside


def test_traversing_transcript_is_rejected_locally_and_nothing_is_sent(tmp_path, fake_sign):
    root = make_root(tmp_path)
    outside = tmp_path / "secret.wav"
    outside.write_bytes(wav_bytes())
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    path = write_segment(root, segment_id, extra={"audio_file": str(outside)})
    with FakeIngest([201]) as server:
        outcome = uploader.process_segment(root, upload_settings(server.endpoint), path)
    assert outcome == "rejected"
    assert server.requests == []
    marker = json.loads(uploader.marker_path(root, segment_id, "rejected").read_text())
    assert marker["status"] == "local"
    assert SYNTHETIC_TEXT not in json.dumps(marker)


@pytest.mark.parametrize("audio", [b"not a wav at all", b"RIFF\x00\x00\x00\x00AVI \x00\x00"])
def test_non_wav_audio_is_rejected_locally(tmp_path, fake_sign, audio):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    path = write_segment(root, segment_id, audio=audio)
    with FakeIngest([201]) as server:
        assert uploader.process_segment(root, upload_settings(server.endpoint), path) == "rejected"
    assert server.requests == []


def test_oversize_audio_is_rejected_locally(tmp_path, fake_sign):
    root = make_root(tmp_path)
    path = write_segment(root, "law_rx_20261016T120000Z_a1b2c3", audio=wav_bytes(extra=2000))
    settings = upload_settings("http://127.0.0.1:9/x")
    settings["max_bytes"] = 1024
    assert uploader.process_segment(root, settings, path) == "rejected"


# ------------------------------------------------------------------ retention


def test_retention_deletes_only_old_uploaded_files(tmp_path):
    root = make_root(tmp_path)
    ids = {
        "old_done": "law_rx_20260901T000000Z_old001",
        "old_pending": "law_rx_20260901T000100Z_old002",
        "mid_done": "law_rx_20260920T000000Z_mid001",
        "new_done": "law_rx_20261015T000000Z_new001",
    }
    ages = {"old_done": 40, "old_pending": 40, "mid_done": 20, "new_done": 1}
    for key, segment_id in ids.items():
        write_segment(root, segment_id, age_days=ages[key])
        if key != "old_pending":
            uploader.write_done_marker(root, segment_id, "created")
            marker = uploader.marker_path(root, segment_id, "done")
            stamp = time.time() - ages[key] * 86400
            os.utime(marker, (stamp, stamp))
    # unrelated files outside the three managed directories must be untouched
    (root / "queue" / "pending").mkdir(parents=True)
    bystander = root / "queue" / "pending" / "old_job.json"
    bystander.write_text("{}")
    os.utime(bystander, (1, 1))
    stray = root / "old_stray.wav"
    stray.write_bytes(wav_bytes())
    os.utime(stray, (1, 1))

    def audio(key):
        return root / "audio" / "law" / f"{ids[key]}.wav"

    def transcript(key):
        return root / "transcripts" / f"{ids[key]}.json"

    def marker(key):
        return uploader.marker_path(root, ids[key], "done")

    removed = uploader.run_retention(root, {"audio": 15, "transcripts": 30})
    assert not audio("old_done").exists() and not transcript("old_done").exists()
    assert not marker("old_done").exists()
    assert audio("old_pending").exists() and transcript("old_pending").exists()  # never uploaded
    assert not audio("mid_done").exists()                       # audio older than 15 days
    assert transcript("mid_done").exists() and marker("mid_done").exists()  # transcript under 30 days
    assert audio("new_done").exists() and transcript("new_done").exists() and marker("new_done").exists()
    assert bystander.exists() and stray.exists()
    assert len(removed) == 4


def test_retention_dry_run_deletes_nothing(tmp_path):
    root = make_root(tmp_path)
    segment_id = "law_rx_20260901T000000Z_old001"
    write_segment(root, segment_id, age_days=40)
    uploader.write_done_marker(root, segment_id, "created")
    stamp = time.time() - 40 * 86400
    os.utime(uploader.marker_path(root, segment_id, "done"), (stamp, stamp))
    removed = uploader.run_retention(root, {"audio": 15, "transcripts": 30}, dry_run=True)
    assert len(removed) == 3
    assert (root / "audio" / "law" / f"{segment_id}.wav").exists()
    assert (root / "transcripts" / f"{segment_id}.json").exists()
    assert uploader.marker_path(root, segment_id, "done").exists()


def test_dry_run_flag_prints_and_exits_without_uploading(tmp_path, capsys):
    root = make_root(tmp_path)
    segment_id = "law_rx_20260901T000000Z_old001"
    write_segment(root, segment_id, age_days=40)
    uploader.write_done_marker(root, segment_id, "created")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root)}))
    upload = tmp_path / "upload_config.json"
    upload.write_text(json.dumps({"endpoint": "http://127.0.0.1:9/x"}))
    assert uploader.main(["--config", str(config), "--upload-config", str(upload), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would delete" in out and segment_id in out
    assert (root / "audio" / "law" / f"{segment_id}.wav").exists()


# ------------------------------------------------------------------ HTTP end to end


@pytest.mark.parametrize("status,marker_suffix", [(201, "done"), (200, "done"), (400, "rejected"), (413, "rejected")])
def test_final_statuses_write_markers(tmp_path, fake_sign, capsys, status, marker_suffix):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    path = write_segment(root, segment_id)
    with FakeIngest([status]) as server:
        outcome = uploader.process_segment(root, upload_settings(server.endpoint), path)
    assert outcome == ("done" if marker_suffix == "done" else "rejected")
    assert uploader.marker_path(root, segment_id, marker_suffix).exists()
    other = "rejected" if marker_suffix == "done" else "done"
    assert not uploader.marker_path(root, segment_id, other).exists()
    assert len(server.requests) == 1
    out = capsys.readouterr().out
    assert f"segment_id={segment_id} status={status}" in out
    assert SYNTHETIC_TEXT not in out
    if marker_suffix == "rejected":
        detail = json.loads(uploader.marker_path(root, segment_id, "rejected").read_text())
        assert detail["status"] == status and detail["detail"] == "scripted failure"
    # the segment is not queued again
    assert uploader.queued_transcripts(root, []) == []


@pytest.mark.parametrize("status", [503, 500, 401])
def test_retryable_statuses_leave_segment_queued(tmp_path, fake_sign, status):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    path = write_segment(root, segment_id)
    with FakeIngest([status]) as server:
        outcome = uploader.process_segment(root, upload_settings(server.endpoint), path)
    assert outcome == "retry"
    assert not uploader.marker_path(root, segment_id, "done").exists()
    assert not uploader.marker_path(root, segment_id, "rejected").exists()
    assert uploader.queued_transcripts(root, []) == [path]


def test_network_error_is_retry(tmp_path, fake_sign):
    root = make_root(tmp_path)
    path = write_segment(root, "law_rx_20261016T120000Z_a1b2c3")
    with FakeIngest([201]) as server:
        endpoint = server.endpoint
    assert uploader.process_segment(root, upload_settings(endpoint), path) == "retry"
    assert uploader.queued_transcripts(root, []) == [path]


def test_queue_stops_at_first_retry_then_recovers(tmp_path, fake_sign):
    root = make_root(tmp_path)
    first = write_segment(root, "law_rx_20261016T120000Z_aaa111", age_days=0.2)
    second = write_segment(root, "law_rx_20261016T120100Z_bbb222", age_days=0.1)
    with FakeIngest([503, 201]) as server:
        settings = upload_settings(server.endpoint)
        counts = uploader.process_queue(root, settings, stop=threading.Event())
        assert counts == {"done": 0, "rejected": 0, "retry": 1, "parked": 0}
        assert len(server.requests) == 1
        counts = uploader.process_queue(root, settings, stop=threading.Event())
        assert counts == {"done": 2, "rejected": 0, "retry": 0, "parked": 0}
    assert uploader.marker_path(root, first.stem, "done").exists()
    assert uploader.marker_path(root, second.stem, "done").exists()


def test_request_headers_body_and_pipeline_check_skip(tmp_path, fake_sign):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    write_segment(root, segment_id)
    write_segment(root, "pipeline_check_law_20261016")
    with FakeIngest([201]) as server:
        counts = uploader.process_queue(root, upload_settings(server.endpoint), stop=threading.Event())
    assert counts["done"] == 1 and len(server.requests) == 1
    request = server.requests[0]
    headers = {key.lower(): value for key, value in request["headers"].items()}
    assert request["path"] == "/api/radio/ingest"
    assert headers["user-agent"] == "mindshare-radio-uploader/1"
    assert abs(int(headers["x-radio-timestamp"]) - time.time()) < 60
    assert headers["x-radio-signature"] == "c2lnbmF0dXJl"
    parts = split_multipart(request["body"], headers["content-type"])
    metadata = json.loads(parts["metadata"]["payload"])
    assert metadata["segment_id"] == segment_id and metadata["schema_version"] == 1
    assert not set(FORBIDDEN_FIELDS) & set(metadata)
    assert parts["metadata"]["payload"] == uploader.serialize_metadata(metadata)
    assert parts["audio"]["payload"][:4] == b"RIFF"
    assert f'filename="{segment_id}.wav"' in parts["audio"]["headers"][0]


@needs_openssl
def test_signature_on_the_wire_verifies_against_signed_bytes(tmp_path):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    path = write_segment(root, segment_id)
    key = golden_key_pem(tmp_path)
    with FakeIngest([201]) as server:
        outcome = uploader.process_segment(root, upload_settings(server.endpoint, str(key)), path)
    assert outcome == "done"
    request = server.requests[0]
    headers = {k.lower(): v for k, v in request["headers"].items()}
    parts = split_multipart(request["body"], headers["content-type"])
    message = uploader.build_signing_message(
        int(headers["x-radio-timestamp"]), segment_id, parts["audio"]["payload"], parts["metadata"]["payload"])
    work = tmp_path / "verify"
    work.mkdir()
    # Ed25519 is deterministic, so re-signing the received bytes must match the header.
    assert uploader.sign_message(key, message, work) == headers["x-radio-signature"]
    assert list((root / "uploaded").glob(".sign-*")) == []


# ------------------------------------------------------------------ optional `signal` (who is talking)


def test_signal_is_included_with_only_the_three_allowed_keys(tmp_path):
    extra = {"signal": {"radio_id": "98426", "talkgroup": "G4407", "console_label": "Position2",
                        "frequency": "851.0125", "caller_name": "synthetic", "signal_conflicts": 2}}
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3", extra=extra)
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert metadata["signal"] == {"radio_id": "98426", "talkgroup": "G4407", "console_label": "Position2"}
    assert set(metadata) <= set(uploader.METADATA_FIELDS)
    serialized = uploader.serialize_metadata(metadata).decode("utf-8")
    for leaked in ("frequency", "851.0125", "caller_name", "signal_conflicts"):
        assert leaked not in serialized
    for name in FORBIDDEN_FIELDS:
        assert name not in metadata


def test_signal_with_only_a_radio_id_is_kept_as_is(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3",
                         extra={"signal": {"radio_id": "98426"}})
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert metadata["signal"] == {"radio_id": "98426"}


def test_signal_absent_means_no_signal_key(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3")
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert "signal" not in metadata


@pytest.mark.parametrize("bad", [
    {"radio_id": "9842x"}, {"radio_id": ""}, {"radio_id": "12345678901"}, {"radio_id": 98426},
    {"radio_id": "98426\n"}, {"radio_id": " 98426"}, {"radio_id": "٩٨"},
    {"talkgroup": "g4407"}, {"talkgroup": "G"}, {"talkgroup": "G1234567"}, {"talkgroup": "4407"},
    {"console_label": ""}, {"console_label": " lead"}, {"console_label": "A" * 33},
    {"console_label": "Pos;ition"}, {"console_label": "café"}, {"console_label": ["a"]},
    {"console_label": None}, {"unknown": "x"}, {}, [], "98426", 5, None,
])
def test_invalid_signal_is_dropped_entirely(tmp_path, bad):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3", extra={"signal": bad})
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert "signal" not in metadata


def test_signal_keys_are_revalidated_one_by_one(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3",
                         extra={"signal": {"radio_id": "98426!", "talkgroup": "G4407",
                                           "console_label": "bad;label"}})
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    assert metadata["signal"] == {"talkgroup": "G4407"}


def test_signal_is_part_of_the_signed_deterministic_metadata_bytes(tmp_path):
    path = write_segment(make_root(tmp_path), "law_rx_20261016T120000Z_a1b2c3",
                         extra={"signal": {"talkgroup": "G4407", "radio_id": "98426"}})
    metadata = uploader.build_metadata(json.loads(path.read_text(encoding="utf-8")))
    body = uploader.serialize_metadata(metadata)
    assert b'"signal":{"radio_id":"98426","talkgroup":"G4407"}' in body
    assert body == uploader.serialize_metadata(json.loads(body.decode("utf-8")))


def test_uploaded_request_carries_the_sanitized_signal(tmp_path, fake_sign):
    root = make_root(tmp_path)
    segment_id = "law_rx_20261016T120000Z_a1b2c3"
    write_segment(root, segment_id, extra={"signal": {"radio_id": "98426", "talkgroup": "G4407", "rssi": "-70"}})
    with FakeIngest([201]) as ingest:
        outcome = uploader.process_segment(root, upload_settings(ingest.endpoint),
                                           root / "transcripts" / f"{segment_id}.json")
    assert outcome == "done"
    request = ingest.requests[0]
    parts = split_multipart(request["body"], request["headers"]["Content-Type"])
    metadata = json.loads(parts["metadata"]["payload"])
    assert metadata["signal"] == {"radio_id": "98426", "talkgroup": "G4407"}
    assert parts["metadata"]["payload"] == uploader.serialize_metadata(metadata)
    assert b"rssi" not in request["body"]


# ------------------------------------------------- parking a poisoned head segment (2026-10-23)


def _parking_settings(endpoint: str, limit: int) -> dict:
    settings = upload_settings(endpoint)
    settings["park_after_server_errors"] = limit
    return settings


def test_parked_marker_is_final_and_retention_keeps_its_audio(tmp_path):
    root = make_root(tmp_path)
    segment_id = "ems_tx_20261023T074358Z_54ebe071"
    path = write_segment(root, segment_id, age_days=40)
    uploader.write_parked_marker(root, segment_id, 500, 12, time.time() - 3600)
    marker = uploader.marker_path(root, segment_id, "parked")
    assert marker.exists()
    assert uploader.has_final_marker(root, segment_id)
    assert uploader.queued_transcripts(root, []) == []
    assert uploader.count_parked(root) == 1
    body = json.loads(marker.read_text())
    assert body["attempts"] == 12 and body["status"] == 500 and body["failing_since"]
    # Retention only removes segments marked .done: a parked one keeps its files.
    removed = uploader.run_retention(root, {"audio": 15, "transcripts": 30})
    assert removed == []
    assert path.exists()
    # Re-queue by hand = delete the marker (documented in radio_listener/README.md).
    marker.unlink()
    assert uploader.queued_transcripts(root, []) == [path]
    assert uploader.count_parked(root) == 0


def test_head_segment_is_parked_after_n_server_errors_and_the_queue_moves_on(tmp_path, fake_sign):
    root = make_root(tmp_path)
    first = write_segment(root, "law_rx_20261016T120000Z_aaa111", age_days=0.2)
    second = write_segment(root, "law_rx_20261016T120100Z_bbb222", age_days=0.1)
    stats = uploader.UploaderStats()
    with FakeIngest([500, 500, 500, 201]) as server:
        settings = _parking_settings(server.endpoint, 3)
        for expected_attempts in (1, 2):
            counts = uploader.process_queue(root, settings, stop=threading.Event(), stats=stats)
            assert counts == {"done": 0, "rejected": 0, "retry": 1, "parked": 0}
            assert stats.head_snapshot()[1] == expected_attempts
            assert stats.head_snapshot()[0] is not None
        # Third 5xx reaches the limit: parked, and the pass continues to the next segment.
        counts = uploader.process_queue(root, settings, stop=threading.Event(), stats=stats)
        assert counts == {"done": 1, "rejected": 0, "retry": 0, "parked": 1}
        assert [r["headers"]["x-radio-segment-id"] if "x-radio-segment-id" in r["headers"] else None
                for r in server.requests].count(None) in (0, 4)
        assert len(server.requests) == 4
    assert uploader.marker_path(root, first.stem, "parked").exists()
    assert not uploader.marker_path(root, first.stem, "done").exists()
    assert uploader.marker_path(root, second.stem, "done").exists()
    assert uploader.queued_transcripts(root, []) == []
    assert stats.head_snapshot() == (None, 0)
    assert stats.parked_this_run == 1
    assert uploader.count_parked(root) == 1


def test_only_5xx_replies_count_toward_parking(tmp_path, fake_sign):
    root = make_root(tmp_path)
    path = write_segment(root, "law_rx_20261016T120000Z_aaa111")
    stats = uploader.UploaderStats()
    # A network error (endpoint gone) is not the segment's fault.
    with FakeIngest([201]) as server:
        endpoint = server.endpoint
    settings = _parking_settings(endpoint, 1)
    assert uploader.process_segment(root, settings, path, stats=stats) == "retry"
    assert stats.head_snapshot() == (None, 0)
    # A permanent 4xx is rejected, never parked.
    with FakeIngest([413]) as server:
        assert uploader.process_segment(root, _parking_settings(server.endpoint, 1), path, stats=stats) == "rejected"
    assert uploader.marker_path(root, path.stem, "rejected").exists()
    assert not uploader.marker_path(root, path.stem, "parked").exists()


def test_parking_is_off_when_the_limit_is_zero_or_stats_are_absent(tmp_path, fake_sign):
    root = make_root(tmp_path)
    path = write_segment(root, "law_rx_20261016T120000Z_aaa111")
    with FakeIngest([500]) as server:
        off = _parking_settings(server.endpoint, 0)
        stats = uploader.UploaderStats()
        for _ in range(3):
            assert uploader.process_segment(root, off, path, stats=stats) == "retry"
        assert stats.head_snapshot()[1] == 3
        # No stats object (e.g. a one-off call): plain retry, nothing counted.
        assert uploader.process_segment(root, _parking_settings(server.endpoint, 1), path) == "retry"
    assert not uploader.marker_path(root, path.stem, "parked").exists()
    assert uploader.queued_transcripts(root, []) == [path]


def test_a_different_head_segment_restarts_the_count(tmp_path, fake_sign):
    root = make_root(tmp_path)
    first = write_segment(root, "law_rx_20261016T120000Z_aaa111", age_days=0.2)
    second = write_segment(root, "law_rx_20261016T120100Z_bbb222", age_days=0.1)
    stats = uploader.UploaderStats()
    with FakeIngest([500]) as server:
        settings = _parking_settings(server.endpoint, 3)
        assert uploader.process_segment(root, settings, first, stats=stats) == "retry"
        assert uploader.process_segment(root, settings, first, stats=stats) == "retry"
        assert stats.head_snapshot()[1] == 2
        assert uploader.process_segment(root, settings, second, stats=stats) == "retry"
        assert stats.head_snapshot()[1] == 1
    assert uploader.count_parked(root) == 0


def test_load_configs_normalizes_the_parking_limit(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(tmp_path / "data")}))
    for raw, expected in ((None, 12), ("7", 7), (-3, 0), (0, 0)):
        upload = {"endpoint": "https://aws.logan911.com/api/radio/ingest"}
        if raw is not None:
            upload["park_after_server_errors"] = raw
        path = tmp_path / "upload.json"
        path.write_text(json.dumps(upload))
        assert uploader.load_configs(config, path)[1]["park_after_server_errors"] == expected
