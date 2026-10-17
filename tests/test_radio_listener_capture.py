"""Tests for the health counters in radio_listener/capture.py (synthetic data, loopback only)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import socket
import struct
import sys
import wave
from pathlib import Path

import pytest

CAPTURE_PATH = Path(__file__).resolve().parents[1] / "radio_listener" / "capture.py"
_spec = importlib.util.spec_from_file_location("radio_listener_capture", CAPTURE_PATH)
capture = importlib.util.module_from_spec(_spec)
sys.modules["radio_listener_capture"] = capture
_spec.loader.exec_module(capture)

LISTENER_KEYS = {
    "channel", "direction", "packets_total", "packets_interval", "pcmu_packets_total",
    "other_payload_types", "bytes_interval", "sources_seen", "segments_closed_total",
    "last_packet_at",
}
CAPTURE_KEYS = {"stats_at", "started_at", "uptime_seconds", "interval_seconds", "listeners"}
T0 = dt.datetime(2026, 10, 17, 12, 0, 0, tzinfo=dt.timezone.utc)


def rtp(payload_type=0, timestamp=0, payload=b"\x7f" * 160, sequence=1):
    header = struct.pack("!BBHII", 0x80, payload_type, sequence, timestamp, 0x1234)
    return header + payload


# ------------------------------------------------------------------ ListenerStats


def test_listener_stats_counts_and_snapshot_shape():
    stats = capture.ListenerStats("law", "rx")
    stats.packet(172, "10.0.0.1", 1760000000.0)
    stats.packet(172, "10.0.0.1", 1760000001.0)
    stats.packet(100, "10.0.0.2", 1760000002.5)
    stats.pcmu()
    stats.pcmu()
    stats.other_payload(103)
    stats.segment_closed()
    row = stats.snapshot()
    assert set(row) == LISTENER_KEYS
    assert row["channel"] == "law" and row["direction"] == "rx"
    assert row["packets_total"] == 3 and row["packets_interval"] == 3
    assert row["pcmu_packets_total"] == 2
    assert row["other_payload_types"] == {"103": 1}
    assert row["bytes_interval"] == 444
    assert row["sources_seen"] == 2
    assert row["segments_closed_total"] == 1
    assert row["last_packet_at"] == "2025-10-09T08:53:22.500000+00:00"
    json.dumps(row)  # serializable


def test_interval_counters_reset_but_totals_do_not():
    stats = capture.ListenerStats("fire", "tx")
    for _ in range(5):
        stats.packet(10, "10.0.0.9", 1.0)
    first = stats.snapshot()
    second = stats.snapshot()
    assert first["packets_interval"] == 5 and first["bytes_interval"] == 50
    assert second["packets_interval"] == 0 and second["bytes_interval"] == 0
    assert second["packets_total"] == 5
    assert second["sources_seen"] == 1
    stats.packet(10, "10.0.0.9", 2.0)
    assert stats.snapshot()["packets_interval"] == 1


def test_no_traffic_row_has_null_last_packet():
    row = capture.ListenerStats("ems", "rx").snapshot()
    assert row["last_packet_at"] is None
    assert row["packets_total"] == 0 and row["other_payload_types"] == {}


def test_payload_type_keys_are_capped_at_sixteen():
    stats = capture.ListenerStats("law", "rx")
    for payload_type in range(40):
        stats.other_payload(payload_type)
    stats.other_payload(0)
    stats.other_payload(0)
    row = stats.snapshot()
    assert len(row["other_payload_types"]) == 16
    assert row["other_payload_types"]["0"] == 3  # an existing key keeps counting


def test_sources_tracking_is_bounded():
    stats = capture.ListenerStats("law", "rx")
    for index in range(capture.MAX_SOURCES_TRACKED + 50):
        stats.packet(1, f"10.1.{index // 256}.{index % 256}", 1.0)
    assert stats.snapshot()["sources_seen"] == capture.MAX_SOURCES_TRACKED


# ------------------------------------------------------------------ CaptureStats


def make_capture_stats(**kwargs):
    listeners = [capture.ListenerStats("law", "rx"), capture.ListenerStats("law", "tx")]
    return listeners, capture.CaptureStats(listeners, 1000.0, T0, **kwargs)


def test_capture_snapshot_matches_spec_shape_and_resets_interval():
    listeners, stats = make_capture_stats()
    listeners[0].packet(172, "10.0.0.1", 1.0)
    payload = stats.snapshot(1030.0, T0 + dt.timedelta(seconds=30))
    assert set(payload) == CAPTURE_KEYS
    assert payload["stats_at"] == "2026-10-17T12:00:30+00:00"
    assert payload["started_at"] == "2026-10-17T12:00:00+00:00"
    assert payload["uptime_seconds"] == 30
    assert payload["interval_seconds"] == 30
    assert len(payload["listeners"]) == 2
    assert payload["listeners"][0]["packets_interval"] == 1
    again = stats.snapshot(1060.0, T0 + dt.timedelta(seconds=60))
    assert again["interval_seconds"] == 30
    assert again["listeners"][0]["packets_interval"] == 0
    assert again["listeners"][0]["packets_total"] == 1


def test_stats_at_never_goes_backwards_when_the_wall_clock_steps_back():
    _, stats = make_capture_stats()
    first = stats.snapshot(1030.0, T0 + dt.timedelta(seconds=30))
    second = stats.snapshot(1060.0, T0 - dt.timedelta(hours=1))  # clock stepped back
    assert dt.datetime.fromisoformat(second["stats_at"]) >= dt.datetime.fromisoformat(first["stats_at"])
    third = stats.snapshot(1090.0, T0 + dt.timedelta(seconds=90))
    assert dt.datetime.fromisoformat(third["stats_at"]) > dt.datetime.fromisoformat(first["stats_at"])


def test_tick_writes_immediately_then_every_thirty_seconds_and_summarises_every_five_minutes():
    listeners, stats = make_capture_stats()
    wall = lambda: T0  # noqa: E731
    payload, lines = stats.tick(1000.0, wall)
    assert payload is not None and lines == []  # first file appears at once
    for mono in (1001.0, 1015.0, 1029.9):
        assert stats.tick(mono, wall) == (None, [])
    payload, lines = stats.tick(1030.0, wall)
    assert payload is not None and lines == []
    assert stats.tick(1059.0, wall) == (None, [])
    listeners[0].packet(172, "10.0.0.1", 1.0)
    payload, lines = stats.tick(1300.0, wall)
    assert payload is not None
    assert len(lines) == 2  # one summary line per listener
    assert stats.tick(1301.0, wall) == (None, [])
    payload, lines = stats.tick(1600.0, wall)
    assert payload is not None and len(lines) == 2


def test_tick_does_not_call_the_wall_clock_unless_a_write_is_due():
    _, stats = make_capture_stats()
    stats.tick(1000.0)
    calls = []
    stats.tick(1001.0, lambda: calls.append(1) or T0)
    assert calls == []


def test_summary_lines_are_counts_only():
    listeners, stats = make_capture_stats()
    for _ in range(7):
        listeners[0].packet(172, "10.9.8.7", 1.0)
        listeners[0].pcmu()
    listeners[0].other_payload(103)
    listeners[0].segment_closed()
    lines = stats.summary_lines()
    assert lines[0].startswith("stats channel=law direction=rx packets_since_last=7 packets_total=7 pcmu_total=7")
    assert "other_payload_types=103:1" in lines[0] and "segments_closed=1" in lines[0]
    assert "10.9.8.7" not in " ".join(lines)
    listeners[0].packet(172, "10.9.8.7", 2.0)
    again = stats.summary_lines()
    assert "packets_since_last=1 packets_total=8" in again[0]
    assert "other_payload_types=none" in again[1]


def test_write_stats_file_is_atomic_and_private(tmp_path):
    target = tmp_path / "status" / "capture_stats.json"
    target.parent.mkdir()
    payload = {"stats_at": "2026-10-17T12:00:00+00:00", "listeners": []}
    capture.write_stats_file(target, payload)
    capture.write_stats_file(target, {**payload, "uptime_seconds": 9})
    assert json.loads(target.read_text(encoding="utf-8"))["uptime_seconds"] == 9
    assert [path.name for path in target.parent.iterdir()] == ["capture_stats.json"]
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


def test_write_stats_file_failure_leaves_no_temp_file(tmp_path):
    target = tmp_path / "status" / "capture_stats.json"
    target.parent.mkdir()
    with pytest.raises(TypeError):
        capture.write_stats_file(target, {"bad": object()})
    assert list(target.parent.iterdir()) == []


# ------------------------------------------------------------------ audio path unchanged


class FakeSocket:
    def close(self):
        pass


@pytest.fixture
def stream_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "open_listener", lambda *args: FakeSocket())

    def make(silence=1.5, maximum=120):
        audio = tmp_path / "audio"
        pending = tmp_path / "pending"
        pending.mkdir(exist_ok=True)
        return capture.Stream("law", "rx", "235.98.99.32", 12032, "10.5.2.12", audio, pending, silence, maximum)

    return make


def read_wav(path):
    with wave.open(str(path), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, 8000)
        return handle.readframes(handle.getnframes())


def test_stream_counts_packets_and_keeps_the_audio_identical(stream_factory, tmp_path):
    stream = stream_factory()
    payloads = [bytes([value] * 160) for value in (0x7F, 0x55, 0xFF)]
    sources = [("10.0.0.1", 5000), ("10.0.0.1", 5000), ("10.0.0.2", 5000)]
    for index, (payload, source) in enumerate(zip(payloads, sources)):
        stream.accept(rtp(0, index * 160, payload, index), source)
    stream.accept(rtp(103, 0, b"\x01" * 40), ("10.0.0.3", 5000))     # undecoded payload type
    stream.accept(b"\x00\x01", ("10.0.0.4", 5000))                    # not RTP
    stream.accept(rtp(0, 480, b""), ("10.0.0.1", 5000))               # empty payload
    wav_path = stream.wav_path
    stream.finish_segment()

    # Audio: contiguous packets, so exactly the decoded payloads and nothing else.
    expected = b"".join(capture.pcmu_to_pcm(payload) for payload in payloads)
    assert read_wav(wav_path) == expected

    row = stream.stats.snapshot()
    assert row["packets_total"] == 6
    assert row["pcmu_packets_total"] == 3
    assert row["other_payload_types"] == {"103": 1}
    assert row["sources_seen"] == 4
    assert row["segments_closed_total"] == 1
    assert row["bytes_interval"] == sum(172 for _ in range(3)) + 52 + 2 + 12
    assert row["last_packet_at"] is not None
    # The existing summary still carries the original counters.
    summary = stream.summary()
    assert summary["packets_total"] == 6 and summary["packets_pcmu_pt0"] == 3
    assert summary["segments_written"] == 1


def test_stream_gap_fill_and_segment_split_are_unchanged(stream_factory):
    stream = stream_factory()
    first = bytes([0x7F] * 80)
    second = bytes([0x7F] * 80)
    stream.accept(rtp(0, 0, first), ("10.0.0.1", 1))
    stream.accept(rtp(0, 160, second), ("10.0.0.1", 1))  # 80-sample gap is filled with silence
    wav_path = stream.wav_path
    stream.finish_segment()
    expected = capture.pcmu_to_pcm(first) + b"\x00\x00" * 80 + capture.pcmu_to_pcm(second)
    assert read_wav(wav_path) == expected
    assert stream.stats.segments_closed_total == 1


def test_stream_finish_if_idle_counts_closed_segments(stream_factory):
    stream = stream_factory(silence=1.5)
    stream.accept(rtp(0, 0), ("10.0.0.1", 1))
    stream.finish_if_idle(stream.last_packet_at + 0.5)
    assert stream.stats.segments_closed_total == 0
    stream.finish_if_idle(stream.last_packet_at + 2.0)
    assert stream.stats.segments_closed_total == 1
    metadata = list((Path(stream.pending_dir)).glob("*.json"))
    assert len(metadata) == 1


# ------------------------------------------------------------------ main(), loopback only


def test_main_writes_the_stats_file_and_a_summary_still_prints(tmp_path, monkeypatch, capsys):
    """Run capture.main for one second against loopback UDP sockets standing in for multicast."""
    sockets = []

    def fake_open_listener(group, port, interface):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.setblocking(False)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target = receiver.getsockname()
        for index in range(4):
            sender.sendto(rtp(0, index * 160, bytes([0x7F] * 160), index), target)
        if sockets:  # the second listener (rx) also sees an undecoded payload type
            sender.sendto(rtp(103, 0, b"\x01" * 20), target)
        sender.close()
        sockets.append(receiver)
        return receiver

    monkeypatch.setattr(capture, "open_listener", fake_open_listener)
    original = capture.CaptureStats
    monkeypatch.setattr(capture, "CaptureStats", lambda *args: original(*args, write_seconds=0.2))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "capture_interface": "127.0.0.1",
        "output_directory": str(tmp_path / "data"),
        "channels": [{"name": "law", "group": "235.98.99.32", "ports": {"tx": 10032, "rx": 12032}}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["capture.py", "--config", str(config), "--duration", "1"])
    monkeypatch.setattr(capture, "STOP", False)
    capture.main()

    stats_file = tmp_path / "data" / "status" / "capture_stats.json"
    assert stats_file.is_file()
    payload = json.loads(stats_file.read_text(encoding="utf-8"))
    assert set(payload) == CAPTURE_KEYS
    assert {(row["channel"], row["direction"]) for row in payload["listeners"]} == {("law", "tx"), ("law", "rx")}
    by_row = {row["direction"]: row for row in payload["listeners"]}
    assert by_row["tx"]["packets_total"] == 4 and by_row["tx"]["pcmu_packets_total"] == 4
    assert by_row["rx"]["packets_total"] == 5 and by_row["rx"]["other_payload_types"] == {"103": 1}
    assert by_row["tx"]["sources_seen"] == 1 and by_row["tx"]["last_packet_at"] is not None
    # The on-exit summary keeps its existing shape and numbers.
    summaries = list((tmp_path / "data").glob("summary_*.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    by_direction = {stream["direction"]: stream for stream in summary["streams"]}
    assert by_direction["tx"]["packets_pcmu_pt0"] == 4
    assert by_direction["rx"]["other_payload_types"] == {"103": 1}
    assert "Summary saved" in capsys.readouterr().out
    if os.name == "posix":
        assert stats_file.stat().st_mode & 0o777 == 0o600
        assert stats_file.parent.stat().st_mode & 0o777 == 0o700
    for sock in sockets:
        sock.close()
