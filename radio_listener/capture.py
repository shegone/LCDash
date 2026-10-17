#!/usr/bin/env python3
"""Listen-only RTP multicast capture for the Mindshare radio network."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import ipaddress
import json
import os
import re
import selectors
import signal
import socket
import struct
import sys
import time
import uuid
import wave
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("config.json")
MAX_CHANNELS = 24
SAMPLE_RATE = 8000
PT_PCMU = 0
STOP = False

# Health counters (see docs/MINDSHARE_RADIO_HEALTH.md). Counting only: nothing
# here touches the audio path.
STATS_WRITE_SECONDS = 30
STATS_SUMMARY_SECONDS = 300
MAX_PAYLOAD_TYPE_KEYS = 16
MAX_SOURCES_TRACKED = 1024
STATS_DIR_NAME = "status"
STATS_FILE_NAME = "capture_stats.json"


def pcmu_to_pcm(payload: bytes) -> bytes:
    """Decode G.711 mu-law payload bytes to signed little-endian PCM16."""
    pcm = bytearray(len(payload) * 2)
    for i, value in enumerate(payload):
        value = (~value) & 0xFF
        sample = ((value & 0x0F) << 3) + 0x84
        sample <<= (value & 0x70) >> 4
        sample = 0x84 - sample if value & 0x80 else sample - 0x84
        struct.pack_into("<h", pcm, i * 2, sample)
    return bytes(pcm)


def parse_rtp(packet: bytes):
    """Return (payload_type, timestamp, payload), or None for invalid RTP."""
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return None
    csrc_count = packet[0] & 0x0F
    has_extension = bool(packet[0] & 0x10)
    has_padding = bool(packet[0] & 0x20)
    offset = 12 + (4 * csrc_count)
    if len(packet) < offset:
        return None
    if has_extension:
        if len(packet) < offset + 4:
            return None
        extension_words = struct.unpack_from("!H", packet, offset + 2)[0]
        offset += 4 + (4 * extension_words)
        if len(packet) < offset:
            return None
    end = len(packet)
    if has_padding:
        padding = packet[-1]
        if padding == 0 or padding > end - offset:
            return None
        end -= padding
    return packet[1] & 0x7F, struct.unpack_from("!I", packet, 4)[0], packet[offset:end]


def load_config(path: Path):
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object.")
    config.setdefault("capture_interface", "10.5.2.12")
    config.setdefault("duration_seconds", 60)
    config.setdefault("output_directory", "./captures")
    config.setdefault("max_channels", MAX_CHANNELS)
    config.setdefault("segment_silence_seconds", 1.5)
    config.setdefault("max_segment_seconds", 120)
    channels = config.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError("Config must contain a non-empty channels array.")
    max_channels = config["max_channels"]
    if not isinstance(max_channels, int) or not 1 <= max_channels <= MAX_CHANNELS:
        raise ValueError(f"max_channels must be from 1 to {MAX_CHANNELS}.")
    if len(channels) > max_channels:
        raise ValueError(f"Config has {len(channels)} channels; limit is {max_channels}.")
    try:
        interface = ipaddress.IPv4Address(config["capture_interface"])
    except ipaddress.AddressValueError as exc:
        raise ValueError("capture_interface must be an IPv4 address.") from exc
    if not isinstance(config["duration_seconds"], int) or config["duration_seconds"] < 0:
        raise ValueError("duration_seconds must be a non-negative integer (0 means until Ctrl+C).")
    for key in ("segment_silence_seconds", "max_segment_seconds"):
        if not isinstance(config[key], (int, float)) or config[key] <= 0:
            raise ValueError(f"{key} must be greater than zero.")
    seen_names, seen_streams = set(), set()
    normalized = []
    for index, channel in enumerate(channels, 1):
        if not isinstance(channel, dict):
            raise ValueError(f"channels[{index - 1}] must be an object.")
        name = channel.get("name")
        group = channel.get("group")
        ports = channel.get("ports")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", name):
            raise ValueError(f"channels[{index - 1}].name must use letters, digits, _ or -.")
        if name.casefold() in seen_names:
            raise ValueError(f"Duplicate channel name: {name}")
        seen_names.add(name.casefold())
        try:
            address = ipaddress.IPv4Address(group)
        except (ipaddress.AddressValueError, TypeError) as exc:
            raise ValueError(f"Invalid IPv4 multicast group for {name}.") from exc
        if not address.is_multicast:
            raise ValueError(f"{name}.group is not a multicast IPv4 address.")
        if not isinstance(ports, dict) or set(ports) != {"tx", "rx"}:
            raise ValueError(f"{name}.ports must contain exactly tx and rx.")
        for direction in ("tx", "rx"):
            port = ports[direction]
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError(f"{name} {direction} port must be in the range 1..65535.")
            endpoint = (str(address), port)
            if endpoint in seen_streams:
                raise ValueError(f"Duplicate multicast endpoint {address}:{port}.")
            seen_streams.add(endpoint)
            normalized.append((name, direction, str(address), port))
    config["capture_interface"] = str(interface)
    try:
        output = Path(config["output_directory"])
    except TypeError as exc:
        raise ValueError("output_directory must be a path string.") from exc
    if not output.is_absolute():
        config["output_directory"] = str((path.resolve().parent / output).resolve())
    config["_streams"] = normalized
    return config


def open_listener(group: str, port: int, interface: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        membership = socket.inet_aton(group) + socket.inet_aton(interface)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        raise


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_from_epoch(value):
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


class ListenerStats:
    """Cheap per-listener counters: integer adds and one set insert, no I/O.

    Pure (no sockets, no clock of its own), so tests can drive it directly.
    """

    def __init__(self, channel, direction):
        self.channel, self.direction = channel, direction
        self.packets_total = 0
        self.packets_interval = 0
        self.pcmu_packets_total = 0
        self.other_payload_types = {}
        self.bytes_interval = 0
        self.sources = set()
        self.segments_closed_total = 0
        self.last_packet_wall = None

    def packet(self, size, source_ip, wall_time):
        self.packets_total += 1
        self.packets_interval += 1
        self.bytes_interval += size
        self.last_packet_wall = wall_time
        if source_ip not in self.sources and len(self.sources) < MAX_SOURCES_TRACKED:
            self.sources.add(source_ip)

    def pcmu(self):
        self.pcmu_packets_total += 1

    def other_payload(self, payload_type):
        key = str(payload_type)
        if key in self.other_payload_types:
            self.other_payload_types[key] += 1
        elif len(self.other_payload_types) < MAX_PAYLOAD_TYPE_KEYS:
            self.other_payload_types[key] = 1

    def segment_closed(self):
        self.segments_closed_total += 1

    def snapshot(self, reset_interval=True):
        row = {
            "channel": self.channel,
            "direction": self.direction,
            "packets_total": self.packets_total,
            "packets_interval": self.packets_interval,
            "pcmu_packets_total": self.pcmu_packets_total,
            "other_payload_types": dict(self.other_payload_types),
            "bytes_interval": self.bytes_interval,
            "sources_seen": len(self.sources),
            "segments_closed_total": self.segments_closed_total,
            "last_packet_at": _iso_from_epoch(self.last_packet_wall),
        }
        if reset_interval:
            self.packets_interval = 0
            self.bytes_interval = 0
        return row


class CaptureStats:
    """Schedules the 30 s stats file and the 5 minute summary lines.

    `tick()` is called from the receive loop; it only compares two numbers
    unless a write or summary is due. All clocks are passed in.
    """

    def __init__(self, listeners, mono_now, wall_now, write_seconds=STATS_WRITE_SECONDS,
                 summary_seconds=STATS_SUMMARY_SECONDS):
        self.listeners = list(listeners)
        self.write_seconds = write_seconds
        self.summary_seconds = summary_seconds
        self.started_wall = wall_now
        self.started_mono = mono_now
        self._interval_started = mono_now
        self._next_write = mono_now  # first file appears immediately
        self._next_summary = mono_now + summary_seconds
        self._last_stats_at = None
        self._summary_previous = {}

    def snapshot(self, mono_now, wall_now):
        """Build the `capture` object and reset the interval counters."""
        # stats_at never goes backwards, even if the wall clock is stepped.
        stats_at = wall_now
        if self._last_stats_at is not None and stats_at < self._last_stats_at:
            stats_at = self._last_stats_at
        self._last_stats_at = stats_at
        payload = {
            "stats_at": stats_at.isoformat(),
            "started_at": self.started_wall.isoformat(),
            "uptime_seconds": max(0, int(mono_now - self.started_mono)),
            "interval_seconds": max(0, int(round(mono_now - self._interval_started))),
            "listeners": [listener.snapshot() for listener in self.listeners],
        }
        self._interval_started = mono_now
        return payload

    def summary_lines(self):
        """One counts-only line per listener; packets_since_last is since the last summary."""
        lines = []
        for listener in self.listeners:
            key = (listener.channel, listener.direction)
            previous = self._summary_previous.get(key, 0)
            self._summary_previous[key] = listener.packets_total
            others = ",".join(
                f"{pt}:{count}" for pt, count in sorted(listener.other_payload_types.items())
            ) or "none"
            lines.append(
                f"stats channel={listener.channel} direction={listener.direction} "
                f"packets_since_last={listener.packets_total - previous} "
                f"packets_total={listener.packets_total} "
                f"pcmu_total={listener.pcmu_packets_total} other_payload_types={others} "
                f"sources={len(listener.sources)} segments_closed={listener.segments_closed_total}"
            )
        return lines

    def tick(self, mono_now, wall_clock=utc_now):
        """Return (payload_to_write_or_None, summary_lines)."""
        payload = None
        lines = []
        if mono_now >= self._next_write:
            payload = self.snapshot(mono_now, wall_clock())
            self._next_write = mono_now + self.write_seconds
        if mono_now >= self._next_summary:
            lines = self.summary_lines()
            self._next_summary = mono_now + self.summary_seconds
        return payload, lines


def write_stats_file(path: Path, payload: dict) -> None:
    """Atomic 0600 write (temp file in the same directory, then rename)."""
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.chmod(0o600)
    os.replace(temporary, path)


class Stream:
    def __init__(self, name, direction, group, port, interface, audio_dir,
                 pending_dir, silence_seconds, max_segment_seconds):
        self.name, self.direction = name, direction
        self.group, self.port, self.interface = group, port, interface
        self.socket = open_listener(group, port, interface)
        self.count = 0
        self.pcmu_count = 0
        self.bad_rtp = 0
        self.other_payloads = collections.Counter()
        self.sources = collections.Counter()
        self.wav_path = None
        self.wav = None
        self.last_rtp_timestamp = None
        self.audio_dir = audio_dir / name
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio_dir.chmod(0o700)
        self.pending_dir = pending_dir
        self.silence_seconds = float(silence_seconds)
        self.max_segment_seconds = float(max_segment_seconds)
        self.segment_started = None
        self.segment_started_utc = None
        self.last_packet_at = None
        self.segment_sources = collections.Counter()
        self.segment_packets = 0
        self.segments_written = 0
        self.stats = ListenerStats(name, direction)

    def start_segment(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.segment_id = (
            f"{self.name}_{self.direction}_"
            f"{now.strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid.uuid4().hex[:8]}"
        )
        self.wav_path = self.audio_dir / f"{self.segment_id}.wav"
        self.wav = wave.open(str(self.wav_path), "wb")
        self.wav_path.chmod(0o600)
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(SAMPLE_RATE)
        self.segment_started = time.monotonic()
        self.segment_started_utc = now
        self.segment_sources = collections.Counter()
        self.segment_packets = 0
        self.last_rtp_timestamp = None

    def finish_segment(self):
        if self.wav is None:
            return
        self.wav.close()
        self.wav = None
        metadata = {
            "segment_id": self.segment_id,
            "channel": self.name,
            "direction": self.direction,
            "multicast_group": self.group,
            "port": self.port,
            "codec": "PCMU",
            "payload_type": PT_PCMU,
            "sample_rate_hz": SAMPLE_RATE,
            "audio_file": str(self.wav_path),
            "started_at": self.segment_started_utc.isoformat(),
            "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pcmu_packet_count": self.segment_packets,
            "source_packet_counts": dict(self.segment_sources),
        }
        ready_path = self.pending_dir / f"{self.segment_id}.json"
        temporary_path = ready_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, ready_path)
        self.wav_path = None
        self.segment_started = None
        self.segment_started_utc = None
        self.last_packet_at = None
        self.last_rtp_timestamp = None
        self.segments_written += 1
        self.stats.segment_closed()

    def accept(self, packet, source):
        self.count += 1
        self.sources[source[0]] += 1
        self.stats.packet(len(packet), source[0], time.time())
        parsed = parse_rtp(packet)
        if parsed is None:
            self.bad_rtp += 1
            return
        payload_type, timestamp, payload = parsed
        if payload_type != PT_PCMU:
            self.other_payloads[str(payload_type)] += 1
            self.stats.other_payload(payload_type)
            return
        if not payload:
            return
        now = time.monotonic()
        if self.wav is not None and (
            (self.last_packet_at is not None
             and now - self.last_packet_at >= self.silence_seconds)
            or (self.segment_started is not None
                and now - self.segment_started >= self.max_segment_seconds)
        ):
            self.finish_segment()
        if self.wav is None:
            self.start_segment()
        if self.last_rtp_timestamp is not None:
            gap = (timestamp - self.last_rtp_timestamp) & 0xFFFFFFFF
            if 0 < gap <= SAMPLE_RATE:
                self.wav.writeframesraw(b"\x00\x00" * gap)
        self.wav.writeframesraw(pcmu_to_pcm(payload))
        self.last_rtp_timestamp = (timestamp + len(payload)) & 0xFFFFFFFF
        self.last_packet_at = now
        self.pcmu_count += 1
        self.stats.pcmu()
        self.segment_packets += 1
        self.segment_sources[source[0]] += 1

    def finish_if_idle(self, now):
        if self.wav is None:
            return
        idle = self.last_packet_at is not None and now - self.last_packet_at >= self.silence_seconds
        expired = self.segment_started is not None and now - self.segment_started >= self.max_segment_seconds
        if idle or expired:
            self.finish_segment()

    def close(self):
        try:
            self.finish_segment()
        finally:
            self.socket.close()

    def summary(self):
        return {
            "channel": self.name,
            "direction": self.direction,
            "group": self.group,
            "port": self.port,
            "packets_total": self.count,
            "packets_pcmu_pt0": self.pcmu_count,
            "other_payload_types": dict(self.other_payloads),
            "invalid_rtp_packets": self.bad_rtp,
            "source_packet_counts": dict(self.sources),
            "segments_written": self.segments_written,
        }


def stop_handler(signum, frame):
    global STOP
    STOP = True


def main():
    parser = argparse.ArgumentParser(description="Listen-only Mindshare RTP multicast capture.")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("-i", "--interface", help="Override multicast interface IPv4 address.")
    parser.add_argument("-d", "--duration", type=int, help="Seconds to capture; 0 runs until Ctrl+C.")
    parser.add_argument("-o", "--output", help="Override output directory.")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        interface = str(ipaddress.IPv4Address(args.interface)) if args.interface else config["capture_interface"]
        duration = args.duration if args.duration is not None else config["duration_seconds"]
        if duration < 0:
            raise ValueError("duration must be non-negative.")
        output_dir = Path(args.output or config["output_directory"])
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.chmod(0o700)
        audio_dir = output_dir / "audio"
        pending_dir = output_dir / "queue" / "pending"
        for private_dir in (
            audio_dir, output_dir / "queue", pending_dir,
            output_dir / "queue" / "processing",
            output_dir / "queue" / "failed",
            output_dir / "transcripts",
            output_dir / STATS_DIR_NAME,
        ):
            private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_dir.chmod(0o700)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    selector = selectors.DefaultSelector()
    streams = []
    try:
        for name, direction, group, port in config["_streams"]:
            stream = Stream(
                name, direction, group, port, interface, audio_dir, pending_dir,
                config["segment_silence_seconds"], config["max_segment_seconds"],
            )
            streams.append(stream)
            selector.register(stream.socket, selectors.EVENT_READ, stream)
            print(f"Listening {name} {direction.upper()} {group}:{port}", flush=True)
    except OSError as exc:
        for stream in streams:
            stream.close()
        parser.error(f"Could not join multicast listener: {exc}")

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    started = time.monotonic()
    stats_path = output_dir / STATS_DIR_NAME / STATS_FILE_NAME
    capture_stats = CaptureStats([stream.stats for stream in streams], started, utc_now())
    print(f"Capture ready: {len(streams)} listeners on {interface}; duration={duration}s", flush=True)
    try:
        while not STOP and (duration == 0 or time.monotonic() - started < duration):
            for key, _ in selector.select(timeout=0.5):
                try:
                    packet, source = key.fileobj.recvfrom(65535)
                    key.data.accept(packet, source)
                except OSError as exc:
                    print(f"Receive error on {key.data.name}/{key.data.direction}: {exc}", file=sys.stderr)
            idle_check = time.monotonic()
            for stream in streams:
                stream.finish_if_idle(idle_check)
            # Health reporting must never be able to stop audio capture.
            try:
                stats_payload, summary_lines = capture_stats.tick(idle_check)
                if stats_payload is not None:
                    write_stats_file(stats_path, stats_payload)
                for line in summary_lines:
                    print(line, flush=True)
            except Exception as exc:
                print(f"Capture stats skipped: {type(exc).__name__}", file=sys.stderr, flush=True)
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    result = {
        "interface": interface,
        "duration_seconds": round(time.monotonic() - started, 2),
        "channel_count": len(config["channels"]),
        "listener_count": len(streams),
        "streams": [stream.summary() for stream in streams],
    }
    summary_path = output_dir / f"summary_{stamp}.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary_path.chmod(0o600)
    print(json.dumps(result, indent=2), flush=True)
    print(f"Summary saved: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
