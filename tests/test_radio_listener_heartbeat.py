"""Tests for the heartbeat and glossary exchange in radio_listener/uploader.py.

Synthetic data only; the "server" is a loopback HTTP server on 127.0.0.1.
"""

from __future__ import annotations

import base64
import datetime as dt
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
_spec = importlib.util.spec_from_file_location("radio_listener_uploader_heartbeat", UPLOADER_PATH)
uploader = importlib.util.module_from_spec(_spec)
sys.modules["radio_listener_uploader_heartbeat"] = uploader
_spec.loader.exec_module(uploader)

# Same seed as the ingest golden vector in docs/MINDSHARE_RADIO_INGEST.md.
GOLDEN_SEED_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
GOLDEN_TIMESTAMP = 1760000000
GOLDEN_BODY = b'{"schema_version":1}'
GOLDEN_BODY_SHA = "a9d5f6d002d956b8af5787a05e0ca000d45c03977ffa54ee8fbed719fed5fd23"
GOLDEN_MESSAGE = f"lcdash-radio-heartbeat-v1\n1760000000\n{GOLDEN_BODY_SHA}".encode("ascii")
# Ed25519 is deterministic: the signature of GOLDEN_MESSAGE under the golden seed.
GOLDEN_HEARTBEAT_SIGNATURE = (
    "fasKho2p65fvf+qCEE706U5cWiBwlVVrpc1W7H3hnxX3EWGB4uVOxxeHQK63qleGeQA2OFXwlxU8/M0HozzTAA=="
)

NOW = dt.datetime(2026, 10, 17, 12, 0, 0, tzinfo=dt.timezone.utc).timestamp()
NOW_DT = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
HOTWORDS = "Zebulon Quarry, Mt. Hanover, Engine 9"
PROMPT = "Public safety radio traffic. Names and terms that may be spoken: " + HOTWORDS + "."

BODY_KEYS = {"schema_version", "sent_at", "capture", "transcriber", "uploader", "glossary_version"}
CAPTURE_KEYS = {"stats_at", "started_at", "uptime_seconds", "interval_seconds", "listeners"}
LISTENER_KEYS = {
    "channel", "direction", "packets_total", "packets_interval", "pcmu_packets_total",
    "other_payload_types", "bytes_interval", "sources_seen", "segments_closed_total", "last_packet_at",
}


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
    for name in ("audio/law", "transcripts", "uploaded", "status", "queue/pending", "queue/processing",
                 "queue/failed"):
        (root / name).mkdir(parents=True)
    return root


def listener_row(**overrides):
    row = {
        "channel": "law", "direction": "rx", "packets_total": 5000, "packets_interval": 120,
        "pcmu_packets_total": 4900, "other_payload_types": {}, "bytes_interval": 20640,
        "sources_seen": 3, "segments_closed_total": 17, "last_packet_at": "2026-10-17T11:59:58+00:00",
    }
    row.update(overrides)
    return row


def write_capture_stats(root: Path, *, age_seconds: float = 10, listeners=None, extra=None):
    stats_at = (NOW_DT - dt.timedelta(seconds=age_seconds)).isoformat()
    payload = {
        "stats_at": stats_at, "started_at": "2026-10-17T08:00:00+00:00",
        "uptime_seconds": 14400, "interval_seconds": 30,
        "listeners": [listener_row(), listener_row(direction="tx", packets_interval=0)]
        if listeners is None else listeners,
    }
    payload.update(extra or {})
    (root / "status").mkdir(parents=True, exist_ok=True)
    (root / "status" / "capture_stats.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def write_transcript(root: Path, name: str, age_seconds: float = 0.0) -> Path:
    path = root / "transcripts" / f"{name}.json"
    path.write_text("{}", encoding="utf-8")
    stamp = NOW - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def upload_settings(endpoint: str, **extra) -> dict:
    settings = dict(uploader.DEFAULT_UPLOAD_CONFIG)
    settings.update({"endpoint": endpoint, "private_key": "unused.pem"})
    settings.update(extra)
    return settings


def glossary_reply(version="abc123def456", full=True):
    reply = {"version": version}
    if full:
        reply.update({"hotwords": HOTWORDS, "prompt": PROMPT})
    return {"status": "ok", "server_time": "2026-10-17T12:00:01+00:00", "glossary": reply}


class FakeServer:
    """Loopback server; `responder(request) -> (status, payload, extra_headers)`."""

    def __init__(self, responder=None):
        self.requests = []
        self.responder = responder or (lambda request: (200, glossary_reply(full=False), {}))
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                request = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                           "body": body}
                owner.requests.append(request)
                status, payload, extra = owner.responder(request)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def endpoint(self) -> str:
        return self.base + "/api/radio/ingest"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def fake_sign(monkeypatch):
    monkeypatch.setattr(uploader, "sign_message", lambda *args, **kwargs: "c2lnbmF0dXJl")


def sender_for(root, server, stats=None, **extra):
    return uploader.HeartbeatSender(root, upload_settings(server.endpoint, **extra), stats, now=lambda: NOW)


# ------------------------------------------------------------------ signing


def test_heartbeat_signing_message_format():
    assert hashlib.sha256(GOLDEN_BODY).hexdigest() == GOLDEN_BODY_SHA
    message = uploader.build_heartbeat_signing_message(GOLDEN_TIMESTAMP, GOLDEN_BODY)
    assert message == GOLDEN_MESSAGE
    assert message.split(b"\n") == [b"lcdash-radio-heartbeat-v1", b"1760000000", GOLDEN_BODY_SHA.encode()]
    assert not message.endswith(b"\n")
    assert uploader.HEARTBEAT_PREFIX != uploader.SIGNING_PREFIX  # not interchangeable with ingest


@needs_openssl
def test_heartbeat_signature_matches_golden_vector_and_verifies_with_openssl(tmp_path):
    key = golden_key_pem(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    signature = uploader.sign_message(key, GOLDEN_MESSAGE, work)
    assert signature == GOLDEN_HEARTBEAT_SIGNATURE
    assert list(work.iterdir()) == []

    # Derive the expected signature independently with openssl itself, then verify it.
    message_file = tmp_path / "message.bin"
    message_file.write_bytes(GOLDEN_MESSAGE)
    direct = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(message_file)],
        capture_output=True, check=True)
    assert base64.b64encode(direct.stdout).decode("ascii") == signature
    public = tmp_path / "public.pem"
    public.write_bytes(subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout"],
                                      capture_output=True, check=True).stdout)
    signature_file = tmp_path / "signature.bin"
    signature_file.write_bytes(base64.b64decode(signature))
    verified = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public), "-rawin",
         "-in", str(message_file), "-sigfile", str(signature_file)],
        capture_output=True, text=True)
    assert verified.returncode == 0, verified.stdout + verified.stderr


@needs_openssl
def test_signature_on_the_wire_verifies_against_the_sent_body(tmp_path):
    root = make_root(tmp_path)
    write_capture_stats(root)
    key = golden_key_pem(tmp_path)
    with FakeServer() as server:
        sender = uploader.HeartbeatSender(root, upload_settings(server.endpoint, private_key=str(key)),
                                          None, now=lambda: NOW)
        assert sender.send_once() is True
    request = server.requests[0]
    timestamp = int(request["headers"]["x-radio-timestamp"])
    message = uploader.build_heartbeat_signing_message(timestamp, request["body"])
    work = tmp_path / "verify"
    work.mkdir()
    assert uploader.sign_message(key, message, work) == request["headers"]["x-radio-signature"]
    assert list((root / "status").glob(".sign-*")) == []


# ------------------------------------------------------------------ endpoint configuration


def test_heartbeat_endpoint_is_derived_from_the_ingest_endpoint():
    derive = uploader.derive_heartbeat_endpoint
    assert derive("https://aws.logan911.com/api/radio/ingest") == "https://aws.logan911.com/api/radio/heartbeat"
    assert derive("http://127.0.0.1:8080/x/y?z=1#f") == "http://127.0.0.1:8080/api/radio/heartbeat"
    assert derive("https://host.example:8443/") == "https://host.example:8443/api/radio/heartbeat"


def test_load_configs_default_explicit_and_invalid_heartbeat_endpoint(tmp_path):
    here = UPLOADER_PATH.parent
    _, upload = uploader.load_configs(here / "config.json", here / "upload_config.json")
    assert upload["heartbeat_endpoint"] == "https://aws.logan911.com/api/radio/heartbeat"
    assert upload["heartbeat_seconds"] == 60

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(tmp_path / "data")}))

    def load(extra):
        path = tmp_path / "upload.json"
        path.write_text(json.dumps({"endpoint": "https://aws.logan911.com/api/radio/ingest", **extra}))
        return uploader.load_configs(config, path)[1]

    assert load({"heartbeat_endpoint": "https://other.example/hb"})["heartbeat_endpoint"] == "https://other.example/hb"
    assert load({"heartbeat_seconds": 0})["heartbeat_seconds"] == 1
    with pytest.raises(ValueError):
        load({"heartbeat_endpoint": "http://aws.logan911.com/api/radio/heartbeat"})


def test_heartbeat_url_follows_the_ingest_endpoint_when_not_configured():
    assert uploader.heartbeat_url({"endpoint": "http://127.0.0.1:9/api/radio/ingest"}) == \
        "http://127.0.0.1:9/api/radio/heartbeat"
    assert uploader.heartbeat_url({"endpoint": "http://127.0.0.1:9/i", "heartbeat_endpoint": "http://127.0.0.1:9/hb"}) \
        == "http://127.0.0.1:9/hb"


def test_post_heartbeat_refuses_plain_http_to_non_loopback_hosts():
    with pytest.raises(ValueError):
        uploader.post_heartbeat("http://aws.logan911.com/api/radio/heartbeat", b"{}", 1, "c2ln")


# ------------------------------------------------------------------ body


def test_heartbeat_body_matches_the_spec_shape(tmp_path):
    root = make_root(tmp_path)
    write_capture_stats(root)
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), uploader.UploaderStats(), NOW)
    assert set(body) == BODY_KEYS
    assert body["schema_version"] == 1
    assert body["sent_at"] == "2026-10-17T12:00:00+00:00"
    assert set(body["capture"]) == CAPTURE_KEYS
    assert body["capture"]["uptime_seconds"] == 14400 and body["capture"]["interval_seconds"] == 30
    assert len(body["capture"]["listeners"]) == 2
    assert all(set(row) == LISTENER_KEYS for row in body["capture"]["listeners"])
    assert body["transcriber"] == {"pending": 0, "failed": 0}
    assert body["uploader"] == {"queued": 0, "oldest_queued_age_seconds": None,
                                "rejected_total": 0, "last_success_at": None}
    assert body["glossary_version"] is None
    assert len(uploader.serialize_heartbeat(body)) < uploader.MAX_HEARTBEAT_BYTES


def test_unknown_and_sensitive_keys_in_the_stats_file_never_reach_the_body(tmp_path):
    root = make_root(tmp_path)
    row = listener_row(source_ips=["10.0.0.1"], group="235.98.99.32", transcript="secret words")
    write_capture_stats(root, listeners=[row, "not a row", {"channel": "bad name!", "direction": "rx"},
                                         {"channel": "law", "direction": "sideways"}],
                        extra={"hostname": "lcdash-server", "multicast_group": "235.98.99.32"})
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    text = json.dumps(body)
    for forbidden in ("10.0.0.1", "235.98.99.32", "secret words", "lcdash-server", "source_ips"):
        assert forbidden not in text
    assert set(body["capture"]) == CAPTURE_KEYS
    assert len(body["capture"]["listeners"]) == 1 and set(body["capture"]["listeners"][0]) == LISTENER_KEYS


def test_numbers_are_sanitized_to_non_negative_ints(tmp_path):
    root = make_root(tmp_path)
    write_capture_stats(root, listeners=[listener_row(
        packets_total=-5, packets_interval=True, pcmu_packets_total="7", bytes_interval=12.9,
        sources_seen=float("inf"), segments_closed_total=None,
        other_payload_types={"103": 4, "x": 1, "1000": 2, "5": -3, "6": 2.0}, last_packet_at="yesterday")])
    row = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)["capture"]["listeners"][0]
    assert row["packets_total"] == 0 and row["packets_interval"] == 0 and row["pcmu_packets_total"] == 0
    assert row["bytes_interval"] == 12 and row["sources_seen"] == 0 and row["segments_closed_total"] == 0
    assert row["other_payload_types"] == {"103": 4, "5": 0, "6": 2}
    assert row["last_packet_at"] is None


def test_payload_type_keys_and_listener_rows_are_capped(tmp_path):
    root = make_root(tmp_path)
    many_types = {str(index): index + 1 for index in range(40)}
    rows = [listener_row(channel=f"ch{index}", other_payload_types=many_types) for index in range(100)]
    write_capture_stats(root, listeners=rows)
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert len(body["capture"]["listeners"]) == uploader.MAX_HEARTBEAT_LISTENERS
    assert all(len(row["other_payload_types"]) == 16 for row in body["capture"]["listeners"])
    assert len(uploader.serialize_heartbeat(body)) <= uploader.MAX_HEARTBEAT_BYTES


def test_serialize_heartbeat_trims_listener_rows_to_fit_16_kib(tmp_path):
    root = make_root(tmp_path)
    many_types = {str(index): 10**9 for index in range(16)}
    write_capture_stats(root, listeners=[listener_row(channel=f"c{i}", other_payload_types=many_types)
                                         for i in range(64)])
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    full = len(uploader.serialize_metadata(body))
    assert full > uploader.MAX_HEARTBEAT_BYTES  # the untrimmed body would be refused with 413
    data = uploader.serialize_heartbeat(body)
    assert len(data) <= uploader.MAX_HEARTBEAT_BYTES
    assert 0 < len(json.loads(data)["capture"]["listeners"]) < 64


def test_serialization_is_deterministic():
    one = uploader.serialize_heartbeat({"capture": {"listeners": []}, "b": 1, "a": 2})
    assert one == b'{"a":2,"b":1,"capture":{"listeners":[]}}'


# ------------------------------------------------------------------ stale and missing stats


def test_fresh_stats_keep_their_listener_rows(tmp_path):
    root = make_root(tmp_path)
    write_capture_stats(root, age_seconds=uploader.STALE_CAPTURE_SECONDS)  # exactly 120 s is still fresh
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert len(body["capture"]["listeners"]) == 2


def test_stale_stats_send_empty_listeners_but_keep_stats_at(tmp_path):
    root = make_root(tmp_path)
    payload = write_capture_stats(root, age_seconds=121)
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert body["capture"]["listeners"] == []
    assert body["capture"]["stats_at"] == payload["stats_at"]
    assert body["capture"]["uptime_seconds"] == 14400  # everything else is sent as written


@pytest.mark.parametrize("contents", [None, "", "{not json", "[]", '{"stats_at": "garbage", "listeners": [1]}'])
def test_missing_or_corrupt_stats_file_gives_an_empty_capture_and_never_fails(tmp_path, contents):
    root = make_root(tmp_path)
    if contents is not None:
        (root / "status" / "capture_stats.json").write_text(contents, encoding="utf-8")
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert body["capture"]["listeners"] == [] and body["capture"]["stats_at"] is None
    assert set(body["capture"]) == CAPTURE_KEYS


def test_stats_file_without_a_timezone_is_treated_as_stale(tmp_path):
    root = make_root(tmp_path)
    write_capture_stats(root, extra={"stats_at": "2026-10-17T11:59:59"})
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert body["capture"]["listeners"] == [] and body["capture"]["stats_at"] is None


def test_oversized_stats_file_is_ignored(tmp_path):
    root = make_root(tmp_path)
    (root / "status" / "capture_stats.json").write_text(" " * (uploader.MAX_STATS_FILE_BYTES + 1))
    assert uploader.read_capture_stats(root) == uploader.empty_capture()


# ------------------------------------------------------------------ queues and own counters


def test_queue_depths_and_oldest_age(tmp_path):
    root = make_root(tmp_path)
    for name in ("a1.json", "a2.json"):
        (root / "queue" / "pending" / name).write_text("{}")
    (root / "queue" / "pending" / "half.json.tmp").write_text("{}")  # not a job
    (root / "queue" / "processing" / "b1.json").write_text("{}")
    for name in ("f1.json", "f2.json", "f3.json"):
        (root / "queue" / "failed" / name).write_text("{}")
    write_transcript(root, "law_rx_20261017T115000_000000Z_aaaa1111", age_seconds=900)
    write_transcript(root, "law_rx_20261017T115900_000000Z_bbbb2222", age_seconds=60)
    write_transcript(root, "pipeline_check_law_20261017", age_seconds=5000)  # skipped prefix
    done = write_transcript(root, "law_rx_20261017T110000_000000Z_cccc3333", age_seconds=5000)
    uploader.write_done_marker(root, done.stem, "created")
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert body["transcriber"] == {"pending": 3, "failed": 3}
    assert body["uploader"]["queued"] == 2
    assert body["uploader"]["oldest_queued_age_seconds"] == 900


def test_missing_queue_directories_count_as_zero(tmp_path):
    root = tmp_path / "bare"
    (root / "transcripts").mkdir(parents=True)
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), None, NOW)
    assert body["transcriber"] == {"pending": 0, "failed": 0}
    assert body["uploader"]["queued"] == 0 and body["uploader"]["oldest_queued_age_seconds"] is None


def test_uploader_counters_follow_segment_outcomes(tmp_path, fake_sign):
    root = make_root(tmp_path)

    def segment(segment_id, status_text="ok"):
        audio = root / "audio" / "law" / f"{segment_id}.wav"
        audio.write_bytes(b"RIFF" + (52).to_bytes(4, "little") + b"WAVE" + b"\x00" * 16)
        transcript = {
            "segment_id": segment_id, "channel": "law", "direction": "rx", "codec": "PCMU", "payload_type": 0,
            "sample_rate_hz": 8000, "audio_file": str(audio), "started_at": "2026-10-17T11:00:00+00:00",
            "ended_at": "2026-10-17T11:00:03+00:00", "pcmu_packet_count": 150, "transcript": status_text,
            "language": "en", "model": "large-v3", "transcribed_at": "2026-10-17T11:00:05+00:00",
            "segments": [],
        }
        (root / "transcripts" / f"{segment_id}.json").write_text(json.dumps(transcript))

    segment("law_rx_20261017T110000_000000Z_aaaa1111")
    segment("law_rx_20261017T110100_000000Z_bbbb2222")
    stats = uploader.UploaderStats()
    statuses = iter([201, 400])
    with FakeServer(lambda request: (next(statuses), {"status": "created", "detail": "no"}, {})) as server:
        counts = uploader.process_queue(root, upload_settings(server.endpoint), stop=threading.Event(),
                                        now=lambda: NOW, stats=stats)
    assert counts["done"] == 1 and counts["rejected"] == 1
    rejected_total, last_success_at = stats.snapshot()
    assert rejected_total == 1 and last_success_at == "2026-10-17T12:00:00+00:00"
    body = uploader.build_heartbeat(root, upload_settings("http://127.0.0.1:9/x"), stats, NOW)
    assert body["uploader"]["rejected_total"] == 1
    assert body["uploader"]["last_success_at"] == "2026-10-17T12:00:00+00:00"
    assert body["uploader"]["queued"] == 0


def test_retry_outcomes_do_not_touch_the_counters():
    stats = uploader.UploaderStats()
    stats.record("retry", 5.0)
    assert stats.snapshot() == (0, None)
    stats.record("rejected", 6.0)
    stats.record("done", 7.0)
    assert stats.snapshot() == (1, "1970-01-01T00:00:07+00:00")


# ------------------------------------------------------------------ the exchange


def test_heartbeat_request_headers_path_and_body_digest(tmp_path, fake_sign):
    root = make_root(tmp_path)
    write_capture_stats(root)
    with FakeServer() as server:
        assert sender_for(root, server).send_once() is True
    assert len(server.requests) == 1
    request = server.requests[0]
    headers = request["headers"]
    assert request["path"] == "/api/radio/heartbeat"
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"] == "mindshare-radio-uploader/1"
    assert headers["x-radio-timestamp"] == str(int(NOW))
    assert headers["x-radio-signature"] == "c2lnbmF0dXJl"
    body = json.loads(request["body"])
    assert set(body) == BODY_KEYS and len(request["body"]) <= uploader.MAX_HEARTBEAT_BYTES
    assert request["body"] == uploader.serialize_metadata(body)  # the signed bytes are the sent bytes


def test_the_signed_message_uses_the_exact_body_bytes(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    captured = {}

    def signer(key, message, work_dir):
        captured["message"] = message
        captured["work_dir"] = work_dir
        return "c2lnbmF0dXJl"

    monkeypatch.setattr(uploader, "sign_message", signer)
    with FakeServer() as server:
        assert sender_for(root, server).send_once() is True
    body = server.requests[0]["body"]
    assert captured["message"] == f"lcdash-radio-heartbeat-v1\n{int(NOW)}\n{hashlib.sha256(body).hexdigest()}".encode()
    assert Path(captured["work_dir"]) == root / "status"


def test_glossary_reply_is_written_atomically_and_privately(tmp_path, fake_sign, capsys):
    root = make_root(tmp_path)
    with FakeServer(lambda request: (200, glossary_reply(), {})) as server:
        assert sender_for(root, server).send_once() is True
    target = root / "status" / "glossary.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": "abc123def456", "hotwords": HOTWORDS, "prompt": PROMPT}
    assert [path.name for path in (root / "status").iterdir()] == ["glossary.json"]
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr()
    assert "glossary updated version=abc123def456" in out.out
    for text in ("Zebulon", "Hanover", "Engine 9", "Public safety"):
        assert text not in out.out + out.err  # glossary text is never logged


def test_second_heartbeat_reports_the_version_and_an_unchanged_version_is_not_rewritten(tmp_path, fake_sign):
    root = make_root(tmp_path)

    def responder(request):
        sent = json.loads(request["body"])["glossary_version"]
        return 200, glossary_reply(full=sent != "abc123def456"), {}

    with FakeServer(responder) as server:
        sender = sender_for(root, server)
        assert sender.send_once() and sender.send_once()
    versions = [json.loads(request["body"])["glossary_version"] for request in server.requests]
    assert versions == [None, "abc123def456"]
    target = root / "status" / "glossary.json"
    assert json.loads(target.read_text())["hotwords"] == HOTWORDS
    first_mtime = target.stat().st_mtime_ns
    # A reply that is version-only and equal changes nothing.
    with FakeServer(lambda request: (200, glossary_reply(full=False), {})) as server:
        assert sender_for(root, server).send_once()
    assert target.stat().st_mtime_ns == first_mtime


def test_new_version_replaces_the_glossary(tmp_path, fake_sign):
    root = make_root(tmp_path)
    replies = iter([glossary_reply("v-one"), glossary_reply("v-two")])
    with FakeServer(lambda request: (200, next(replies), {})) as server:
        sender = sender_for(root, server)
        sender.send_once()
        sender.send_once()
    assert json.loads((root / "status" / "glossary.json").read_text())["version"] == "v-two"
    assert [json.loads(r["body"])["glossary_version"] for r in server.requests] == [None, "v-one"]


def test_empty_glossary_is_written_so_the_transcriber_stops_using_old_hints(tmp_path):
    root = make_root(tmp_path)
    uploader.apply_glossary_reply(root, glossary_reply("abc123def456"))
    reply = {"glossary": {"version": "empty", "hotwords": "", "prompt": ""}}
    assert uploader.apply_glossary_reply(root, reply) == "empty"
    assert json.loads((root / "status" / "glossary.json").read_text()) == {
        "version": "empty", "hotwords": "", "prompt": ""}


@pytest.mark.parametrize("reply", [
    None, [], {}, {"glossary": None}, {"glossary": []}, {"glossary": {}},
    {"glossary": {"version": 5, "hotwords": "x", "prompt": "y"}},
    {"glossary": {"version": "has space", "hotwords": "x", "prompt": "y"}},
    {"glossary": {"version": "v" * 33, "hotwords": "x", "prompt": "y"}},
    {"glossary": {"version": "newver"}},                                    # different version, no text
    {"glossary": {"version": "newver", "hotwords": ["x"], "prompt": "y"}},
    {"glossary": {"version": "newver", "hotwords": "x", "prompt": 3}},
])
def test_malformed_glossary_replies_change_nothing(tmp_path, reply):
    root = make_root(tmp_path)
    assert uploader.apply_glossary_reply(root, reply) is None
    assert not (root / "status" / "glossary.json").exists()


def test_glossary_text_is_cleaned_and_capped(tmp_path):
    root = make_root(tmp_path)
    reply = {"glossary": {"version": "v9", "hotwords": "A\nB\x00" + "x" * 5000, "prompt": "p\r\n" + "y" * 5000}}
    assert uploader.apply_glossary_reply(root, reply) == "v9"
    stored = json.loads((root / "status" / "glossary.json").read_text())
    assert "\n" not in stored["hotwords"] and "\x00" not in stored["hotwords"]
    assert len(stored["hotwords"]) <= uploader.MAX_HOTWORDS_CHARS
    assert len(stored["prompt"]) <= uploader.MAX_PROMPT_CHARS


def test_the_written_glossary_is_what_the_transcriber_reads(tmp_path):
    transcribe_spec = importlib.util.spec_from_file_location(
        "radio_listener_transcribe_hb", UPLOADER_PATH.with_name("transcribe.py"))
    transcribe = importlib.util.module_from_spec(transcribe_spec)
    transcribe_spec.loader.exec_module(transcribe)
    root = make_root(tmp_path)
    uploader.apply_glossary_reply(root, glossary_reply("abc123def456"))
    glossary = transcribe.GlossaryCache(root / "status" / "glossary.json").get()
    assert glossary == {"version": "abc123def456", "hotwords": HOTWORDS, "prompt": PROMPT}


def test_glossary_write_failure_does_not_fail_the_heartbeat(tmp_path, fake_sign, monkeypatch, capsys):
    root = make_root(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(uploader, "private_write", broken)
    with FakeServer(lambda request: (200, glossary_reply(), {})) as server:
        assert sender_for(root, server).send_once() is True
    assert "glossary-error=OSError" in capsys.readouterr().out


# ------------------------------------------------------------------ failures and independence


@pytest.mark.parametrize("status", [400, 401, 413, 500, 503])
def test_non_200_replies_are_failures_and_write_nothing(tmp_path, fake_sign, status):
    root = make_root(tmp_path)
    with FakeServer(lambda request: (status, glossary_reply(), {})) as server:
        assert sender_for(root, server).send_once() is False
    assert not (root / "status" / "glossary.json").exists()


def test_network_error_signing_error_and_poster_exception_are_failures_not_crashes(tmp_path, fake_sign, capsys):
    root = make_root(tmp_path)
    with FakeServer() as server:
        endpoint = server.endpoint
    assert uploader.HeartbeatSender(root, upload_settings(endpoint), None, now=lambda: NOW).send_once() is False
    assert "heartbeat status=network-error" in capsys.readouterr().out

    def raising(*args):
        raise RuntimeError("boom")

    sender = uploader.HeartbeatSender(root, upload_settings(endpoint), None, now=lambda: NOW, poster=raising)
    assert sender.send_once() is False
    assert "heartbeat status=error-RuntimeError" in capsys.readouterr().out

    def bad_signer(*args):
        raise uploader.SigningError("no key")

    sender = uploader.HeartbeatSender(root, upload_settings(endpoint), None, now=lambda: NOW, signer=bad_signer)
    assert sender.send_once() is False
    assert "heartbeat status=sign-error" in capsys.readouterr().out


def test_redirects_are_not_followed(tmp_path, fake_sign):
    root = make_root(tmp_path)
    with FakeServer() as elsewhere:
        target = elsewhere.base + "/api/radio/heartbeat"
        with FakeServer(lambda request: (302, {}, {"Location": target})) as server:
            assert sender_for(root, server).send_once() is False
    assert len(server.requests) == 1
    assert elsewhere.requests == []


def test_heartbeat_status_is_logged_only_when_it_changes(tmp_path, fake_sign, capsys):
    root = make_root(tmp_path)
    statuses = iter([200, 200, 503, 503, 200])
    with FakeServer(lambda request: (next(statuses), glossary_reply(full=False), {})) as server:
        sender = sender_for(root, server)
        for _ in range(5):
            sender.send_once()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("heartbeat status=")]
    assert lines == ["heartbeat status=200", "heartbeat status=503", "heartbeat status=200"]


class StepStop:
    """A stop event that records the requested waits and stops after `limit` of them."""

    def __init__(self, limit):
        self.limit, self.delays = limit, []

    def is_set(self):
        return len(self.delays) >= self.limit

    def wait(self, delay):
        self.delays.append(delay)


def test_run_uses_its_own_backoff_and_resets_after_success(tmp_path):
    root = make_root(tmp_path)
    outcomes = iter([None, None, None, None, None, 200, None])

    def poster(url, body, timestamp, signature):
        status = next(outcomes)
        return status, {"glossary": {"version": "empty"}} if status else None, ""

    stop = StepStop(7)
    sender = uploader.HeartbeatSender(root, upload_settings("http://127.0.0.1:9/x"), None, stop=stop,
                                      now=lambda: NOW, poster=poster, signer=lambda *a: "c2ln")
    sender.run()
    # failures: 5, 10, 20, 40, 60 (capped at the 60 s cadence); success: 60; failure restarts at 5.
    assert stop.delays == [5, 10, 20, 40, 60, 60, 5]


def test_run_honours_the_configured_interval(tmp_path):
    root = make_root(tmp_path)
    stop = StepStop(2)
    sender = uploader.HeartbeatSender(
        root, upload_settings("http://127.0.0.1:9/x", heartbeat_seconds=17), None, stop=stop, now=lambda: NOW,
        poster=lambda *a: (200, {"glossary": {"version": "empty"}}, ""), signer=lambda *a: "c2ln")
    sender.run()
    assert stop.delays == [17, 17]


def test_segment_uploads_and_heartbeats_do_not_block_or_fail_each_other(tmp_path, fake_sign):
    root = make_root(tmp_path)
    audio = root / "audio" / "law" / "law_rx_20261017T110000_000000Z_aaaa1111.wav"
    audio.write_bytes(b"RIFF" + (52).to_bytes(4, "little") + b"WAVE" + b"\x00" * 16)
    segment_id = audio.stem
    (root / "transcripts" / f"{segment_id}.json").write_text(json.dumps({
        "segment_id": segment_id, "channel": "law", "direction": "rx", "codec": "PCMU", "payload_type": 0,
        "sample_rate_hz": 8000, "audio_file": str(audio), "started_at": "2026-10-17T11:00:00+00:00",
        "ended_at": "2026-10-17T11:00:03+00:00", "pcmu_packet_count": 150,
        "transcript": "synthetic", "language": "en", "model": "large-v3",
        "transcribed_at": "2026-10-17T11:00:05+00:00", "segments": []}))

    # 1. The segment endpoint is down (503); heartbeats still succeed, and vice versa.
    with FakeServer(lambda request: (503, {"detail": "down"}, {})) as ingest, FakeServer() as heartbeat_server:
        settings = upload_settings(ingest.endpoint, heartbeat_endpoint=heartbeat_server.base + "/api/radio/heartbeat")
        assert uploader.process_queue(root, settings, stop=threading.Event())["retry"] == 1
        sender = uploader.HeartbeatSender(root, settings, None, now=lambda: NOW)
        assert sender.send_once() is True
        assert len(heartbeat_server.requests) == 1
    body = json.loads(heartbeat_server.requests[0]["body"])
    assert body["uploader"]["queued"] == 1  # the queued segment is reported, not hidden

    # 2. The heartbeat endpoint is down; the segment still uploads and keeps its final marker.
    with FakeServer(lambda request: (201, {"status": "created"}, {})) as ingest, \
            FakeServer(lambda request: (503, {}, {})) as heartbeat_server:
        settings = upload_settings(ingest.endpoint, heartbeat_endpoint=heartbeat_server.base + "/api/radio/heartbeat")
        assert uploader.HeartbeatSender(root, settings, None, now=lambda: NOW).send_once() is False
        assert uploader.process_queue(root, settings, stop=threading.Event())["done"] == 1
    assert uploader.marker_path(root, segment_id, "done").exists()


def test_a_slow_segment_upload_does_not_delay_the_heartbeat_thread(tmp_path, fake_sign):
    root = make_root(tmp_path)
    release = threading.Event()
    heartbeat_seen = threading.Event()

    def slow_segment_server(request):
        release.wait(timeout=10)  # holds the segment upload open
        return 201, {"status": "created"}, {}

    def heartbeat_responder(request):
        heartbeat_seen.set()
        return 200, glossary_reply(full=False), {}

    audio = root / "audio" / "law" / "law_rx_20261017T110000_000000Z_aaaa1111.wav"
    audio.write_bytes(b"RIFF" + (52).to_bytes(4, "little") + b"WAVE" + b"\x00" * 16)
    segment_id = audio.stem
    (root / "transcripts" / f"{segment_id}.json").write_text(json.dumps({
        "segment_id": segment_id, "channel": "law", "direction": "rx", "codec": "PCMU", "payload_type": 0,
        "sample_rate_hz": 8000, "audio_file": str(audio), "started_at": "2026-10-17T11:00:00+00:00",
        "ended_at": "2026-10-17T11:00:03+00:00", "pcmu_packet_count": 150, "transcript": "synthetic",
        "language": "en", "model": "large-v3", "transcribed_at": "2026-10-17T11:00:05+00:00", "segments": []}))

    class ThreadingFake(FakeServer):
        def __init__(self, responder):
            super().__init__(responder)
            from http.server import ThreadingHTTPServer
            handler = self.server.RequestHandlerClass
            self.server.server_close()
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    with ThreadingFake(slow_segment_server) as ingest, ThreadingFake(heartbeat_responder) as heartbeat_server:
        settings = upload_settings(ingest.endpoint, heartbeat_endpoint=heartbeat_server.base + "/api/radio/heartbeat",
                                   heartbeat_seconds=0.05)
        stop = threading.Event()
        worker = threading.Thread(target=uploader.process_queue, args=(root, settings, stop), daemon=True)
        worker.start()
        beat = threading.Thread(target=uploader.HeartbeatSender(root, settings, None, stop=stop).run, daemon=True)
        beat.start()
        try:
            assert heartbeat_seen.wait(timeout=5), "heartbeat was blocked behind the segment upload"
            assert worker.is_alive()  # the segment upload is still in flight
        finally:
            release.set()
            stop.set()
            worker.join(timeout=10)
            beat.join(timeout=10)
    assert len(heartbeat_server.requests) >= 1


# ------------------------------------------------------------------ command line


def test_heartbeat_once_flag_sends_one_heartbeat_and_exits(tmp_path, fake_sign, capsys):
    root = make_root(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root)}))
    with FakeServer(lambda request: (200, glossary_reply(), {})) as server:
        upload = tmp_path / "upload_config.json"
        upload.write_text(json.dumps({"endpoint": server.endpoint, "private_key": "unused.pem"}))
        assert uploader.main(["--config", str(config), "--upload-config", str(upload), "--heartbeat-once"]) == 0
    assert [r["path"] for r in server.requests] == ["/api/radio/heartbeat"]
    assert (root / "status" / "glossary.json").is_file()
    assert "Zebulon" not in capsys.readouterr().out


def test_heartbeat_once_returns_nonzero_when_the_server_is_unreachable(tmp_path, fake_sign):
    root = make_root(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root)}))
    with FakeServer() as server:
        endpoint = server.endpoint
    upload = tmp_path / "upload_config.json"
    upload.write_text(json.dumps({"endpoint": endpoint, "private_key": "unused.pem"}))
    assert uploader.main(["--config", str(config), "--upload-config", str(upload), "--heartbeat-once"]) == 1


def test_main_creates_the_status_directory(tmp_path, fake_sign):
    root = tmp_path / "fresh"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root)}))
    upload = tmp_path / "upload_config.json"
    upload.write_text(json.dumps({"endpoint": "http://127.0.0.1:9/api/radio/ingest"}))
    uploader.main(["--config", str(config), "--upload-config", str(upload), "--dry-run"])
    assert (root / "status").is_dir() and (root / "transcripts").is_dir() and (root / "uploaded").is_dir()


def test_once_mode_does_not_start_the_heartbeat_thread(tmp_path, fake_sign):
    root = make_root(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root)}))
    with FakeServer() as ingest, FakeServer() as heartbeat_server:
        upload = tmp_path / "upload_config.json"
        upload.write_text(json.dumps({
            "endpoint": ingest.endpoint, "private_key": "unused.pem",
            "heartbeat_endpoint": heartbeat_server.base + "/api/radio/heartbeat"}))
        assert uploader.main(["--config", str(config), "--upload-config", str(upload), "--once"]) == 0
    assert heartbeat_server.requests == [] and ingest.requests == []
    time.sleep(0.05)
    assert [t.name for t in threading.enumerate() if t.name == "heartbeat"] == []
