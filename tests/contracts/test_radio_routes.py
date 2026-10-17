"""Mindshare Radio Intelligence: signed ingest, admin reads, audit, retention.

Contract under test: docs/MINDSHARE_RADIO_INGEST.md (v1). Everything here is
offline and synthetic: a fake S3 client, an in-memory repository, a fixed
WAV built in memory, and the contract's own golden Ed25519 vector (whose
private seed is published in the contract precisely so both sides can test).

What is proved:

* the signed-message layout against the golden vector, verbatim;
* /api/radio/ingest authenticates ONLY by signature (identity headers are
  irrelevant), fails closed when unconfigured, and validates strictly;
* every other radio route, including the page, is admin-only with a uniform
  403 and never reaches the service for anyone else;
* list/playback are audited and playback fails closed when the audit write
  fails; keyset paging, ILIKE escaping, low-confidence, and the hourly purge.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app
from app.services import radio_service
from app.services.radio_service import (
    RadioDatabaseError,
    RadioIngestError,
    RadioNotFoundError,
    RadioQueryError,
    RadioRepository,
    RadioService,
    RadioStorageError,
    build_signed_message,
    compute_low_confidence,
    escape_like_pattern,
    load_public_key,
    validate_metadata,
    validate_wav,
    verify_signature,
)

# ------------------------------------------------------------- golden vector

GOLDEN_SEED = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
)
GOLDEN_PUBLIC_KEY = "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
GOLDEN_TIMESTAMP = 1760000000
GOLDEN_SEGMENT_ID = "law_rx_test"
GOLDEN_METADATA = b'{"segment_id":"law_rx_test"}'
GOLDEN_AUDIO = b"RIFFtest"
GOLDEN_SIGNATURE = (
    "950QFKVROQjFxt0qIs+S00kgBfYEa6etdYGkd9DGQIaPuUHpmYFoZcniKxlA3mSfXaeJngm9X1zst47K2ssrBw=="
)

PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(GOLDEN_SEED)
FIXED_NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
ADMIN_EMAIL = "tedsparks@911logan.com"


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


ADMIN = _identity(ADMIN_EMAIL, "lcdash-pilot-admin")
NON_ADMINS = (
    ("supervisor", _identity("s@911logan.com", "lcdash-pilot-supervisor")),
    ("user", _identity("u@911logan.com", "lcdash-pilot-user")),
    ("dispatcher", _identity("d@911logan.com", "lcdash-pilot-dispatcher")),
    ("fire_officer", _identity("f@911logan.com", "lcdash-pilot-fire-officer")),
    ("avatar", _identity("b@911logan.com", "lcdash-pilot-avatar")),
    ("none", None),
)


def make_wav(samples: int = 800, *, channels: int = 1, rate: int = 8000, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * samples * channels * width)
    return buffer.getvalue()


def make_metadata(**overrides) -> dict:
    document = {
        "schema_version": 1,
        "segment_id": "law_rx_20261016_140500_0001",
        "channel": "law",
        "direction": "rx",
        "started_at": "2026-10-16T14:05:00+00:00",
        "ended_at": "2026-10-16T14:05:07.250000+00:00",
        "codec": "PCMU",
        "payload_type": 0,
        "sample_rate_hz": 8000,
        "pcmu_packet_count": 362,
        "transcript": "Synthetic unit check, traffic stop at the test lot.",
        "language": "en",
        "model": "synthetic-asr-1",
        "transcribed_at": "2026-10-16T14:05:09+00:00",
        "segments": [
            {
                "start": 0.0, "end": 7.2, "text": "Synthetic unit check.",
                "avg_logprob": -0.2, "no_speech_prob": 0.01,
            }
        ],
    }
    document.update(overrides)
    return document


def encode(document: dict) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def sign(timestamp: int, segment_id: str, audio: bytes, metadata: bytes) -> str:
    message = build_signed_message(timestamp, segment_id, audio, metadata)
    return base64.b64encode(PRIVATE_KEY.sign(message)).decode("ascii")


# ----------------------------------------------------------------- fakes


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.put_calls: list[dict] = []
        self.fail_put = False

    def put_object(self, **kwargs):
        if self.fail_put:
            raise RuntimeError("s3 down")
        self.put_calls.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            error = RuntimeError("NoSuchKey")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}


class FakeStore:
    """The state behind every FakeRepository a service opens."""

    def __init__(self) -> None:
        self.segments: dict[str, tuple] = {}
        self.audit: list[dict] = []
        self.schema_calls = 0
        self.purge_calls = 0
        self.purge_error: Exception | None = None
        self.fail_audit = False
        self.fail_connect = False
        self.received_at = FIXED_NOW


class FakeRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def __enter__(self):
        if self.store.fail_connect:
            raise RadioDatabaseError("down")
        return self

    def __exit__(self, *exc):
        return False

    def initialize_schema(self):
        self.store.schema_calls += 1

    def segment_exists(self, segment_id):
        return segment_id in self.store.segments

    def insert_segment(self, record, audio_key):
        if record.segment_id in self.store.segments:
            return False
        self.store.segments[record.segment_id] = (
            record.segment_id, record.channel, record.direction,
            record.started_at, record.ended_at, record.duration_seconds,
            record.transcript, record.language, record.model,
            record.low_confidence, audio_key, self.store.received_at,
        )
        return True

    def purge_expired(self):
        self.store.purge_calls += 1
        if self.store.purge_error:
            raise self.store.purge_error
        return 0

    def list_segments(self, *, channel, direction, like_pattern, cursor, limit):
        rows = list(self.store.segments.values())
        if channel:
            rows = [r for r in rows if r[1] == channel]
        if direction:
            rows = [r for r in rows if r[2] == direction]
        if like_pattern:
            needle = like_pattern[1:-1]
            plain, index = "", 0
            while index < len(needle):
                if needle[index] == "!" and index + 1 < len(needle):
                    index += 1
                plain += needle[index]
                index += 1
            rows = [r for r in rows if plain.lower() in r[6].lower()]
        rows.sort(key=lambda r: (r[3], r[0]), reverse=True)
        if cursor is not None:
            rows = [r for r in rows if (r[3], r[0]) < cursor]
        return rows[:limit]

    def get_segment(self, segment_id):
        return self.store.segments.get(segment_id)

    def channel_counts(self, hours):
        counts: dict[str, int] = {}
        for row in self.store.segments.values():
            counts[row[1]] = counts.get(row[1], 0) + 1
        return sorted(counts.items())

    def newest_received_at(self):
        times = [row[11] for row in self.store.segments.values()]
        return max(times) if times else None

    def record_audit(self, event_type, actor, segment_id, detail):
        if self.store.fail_audit:
            raise RadioDatabaseError("audit write failed")
        self.store.audit.append({
            "event_type": event_type, "actor": actor,
            "segment_id": segment_id, "detail": detail,
        })


def build_service(store=None, s3=None, clock=None):
    store = store or FakeStore()
    s3 = s3 or FakeS3()
    service = RadioService(
        bucket="test-content-bucket",
        audio_prefix="radio/audio/",
        s3_client=s3,
        repository_factory=lambda: FakeRepository(store),
        clock=clock or time.monotonic,
        utcnow=lambda: FIXED_NOW,
    )
    return service, store, s3


class FakeCursor:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, rowcount=1):
        self.calls: list[tuple] = []
        self.rowcount = rowcount
        self.commits = 0

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return FakeCursor(rowcount=self.rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def repository_with(connection):
    repository = RadioRepository(
        "postgresql://u:p@localhost/db", connect=lambda *a, **k: connection
    )
    repository.__enter__()
    return repository


# ------------------------------------------------------- pure-function tests


class GoldenVectorTests(unittest.TestCase):
    def test_contract_hashes_match(self):
        self.assertEqual(
            hashlib.sha256(GOLDEN_METADATA).hexdigest(),
            "b67cc69651e0b68ce1d17c5832020ff280730b8b4ed2ca1e29ef962fba2be714",
        )
        self.assertEqual(
            hashlib.sha256(GOLDEN_AUDIO).hexdigest(),
            "79ab531fc8e36fb4aa47a2adf03f3535e971e8dc5a8d84f7db4537cdc5535255",
        )

    def test_signed_message_layout_is_five_lines_no_trailing_newline(self):
        message = build_signed_message(
            GOLDEN_TIMESTAMP, GOLDEN_SEGMENT_ID, GOLDEN_AUDIO, GOLDEN_METADATA
        )
        self.assertEqual(
            message.decode("utf-8").split("\n"),
            [
                "lcdash-radio-v1",
                "1760000000",
                "law_rx_test",
                "79ab531fc8e36fb4aa47a2adf03f3535e971e8dc5a8d84f7db4537cdc5535255",
                "b67cc69651e0b68ce1d17c5832020ff280730b8b4ed2ca1e29ef962fba2be714",
            ],
        )
        self.assertFalse(message.endswith(b"\n"))

    def test_golden_public_key_verifies_golden_signature_verbatim(self):
        key = load_public_key(GOLDEN_PUBLIC_KEY)
        verify_signature(
            key,
            timestamp=GOLDEN_TIMESTAMP,
            signature_b64=GOLDEN_SIGNATURE,
            segment_id=GOLDEN_SEGMENT_ID,
            audio=GOLDEN_AUDIO,
            metadata=GOLDEN_METADATA,
        )

    def test_golden_signature_is_reproduced_by_the_golden_seed(self):
        self.assertEqual(
            sign(GOLDEN_TIMESTAMP, GOLDEN_SEGMENT_ID, GOLDEN_AUDIO, GOLDEN_METADATA),
            GOLDEN_SIGNATURE,
        )

    def test_any_changed_input_breaks_the_signature(self):
        key = load_public_key(GOLDEN_PUBLIC_KEY)
        good = dict(
            timestamp=GOLDEN_TIMESTAMP, signature_b64=GOLDEN_SIGNATURE,
            segment_id=GOLDEN_SEGMENT_ID, audio=GOLDEN_AUDIO,
            metadata=GOLDEN_METADATA,
        )
        for name, value in (
            ("timestamp", GOLDEN_TIMESTAMP + 1),
            ("segment_id", "law_rx_tesu"),
            ("audio", b"RIFFtesu"),
            ("metadata", b'{"segment_id":"law_rx_tesu"}'),
            ("signature_b64", "not base64!"),
            ("signature_b64", base64.b64encode(b"short").decode()),
            ("signature_b64", ""),
        ):
            with self.subTest(changed=name):
                with self.assertRaises(RadioIngestError) as caught:
                    verify_signature(key, **{**good, name: value})
                self.assertEqual(caught.exception.status_code, 401)

    def test_unusable_configured_key_is_a_config_error(self):
        for bad in ("", "not-base64!!", base64.b64encode(b"short").decode()):
            with self.subTest(bad=bad):
                with self.assertRaises(radio_service.RadioConfigError):
                    load_public_key(bad)


class MetadataAndWavTests(unittest.TestCase):
    def test_valid_metadata_normalizes_to_utc_and_computes_duration(self):
        record = validate_metadata(encode(make_metadata(
            started_at="2026-10-16T23:30:00-05:00",
            ended_at="2026-10-16T23:30:10-05:00",
        )))
        self.assertEqual(record.started_at.isoformat(), "2026-10-17T04:30:00+00:00")
        self.assertEqual(record.duration_seconds, 10.0)
        self.assertFalse(record.low_confidence)

    def test_metadata_is_rejected_strictly(self):
        cases = {
            "unknown top-level field": make_metadata(audio_file="C:/x.wav"),
            "schema_version 2": make_metadata(schema_version=2),
            "schema_version bool": make_metadata(schema_version=True),
            "short segment id": make_metadata(segment_id="short"),
            "bad channel": make_metadata(channel="Law Enforcement"),
            "bad direction": make_metadata(direction="both"),
            "naive datetime": make_metadata(started_at="2026-10-16T14:05:00"),
            "ended before started": make_metadata(ended_at="2026-10-16T14:04:00+00:00"),
            "span over 600 s": make_metadata(ended_at="2026-10-16T14:15:01+00:00"),
            "wrong codec": make_metadata(codec="PCMA"),
            "payload type 8": make_metadata(payload_type=8),
            "sample rate 16000": make_metadata(sample_rate_hz=16000),
            "negative packet count": make_metadata(pcmu_packet_count=-1),
            "transcript too long": make_metadata(transcript="x" * 20001),
            "language too long": make_metadata(language="english-us"),
            "model too long": make_metadata(model="m" * 65),
            "transcribed_at naive": make_metadata(transcribed_at="2026-10-16T14:05:09"),
            "too many segments": make_metadata(segments=[
                {"start": 0, "end": 1, "text": "a", "avg_logprob": 0, "no_speech_prob": 0}
            ] * 501),
            "segment with extra key": make_metadata(segments=[
                {"start": 0, "end": 1, "text": "a", "avg_logprob": 0,
                 "no_speech_prob": 0, "words": []}
            ]),
            "segment missing key": make_metadata(segments=[{"start": 0, "end": 1}]),
            "segment nan": make_metadata(segments=[
                {"start": 0, "end": 1, "text": "a", "avg_logprob": 0, "no_speech_prob": 0}
            ]),
        }
        # JSON cannot carry NaN through json.dumps(allow_nan=False); build the
        # raw bytes by hand for that one case.
        raw_nan = encode(make_metadata()).replace(b'"avg_logprob":-0.2', b'"avg_logprob":NaN')
        for label, document in cases.items():
            if label == "segment nan":
                continue
            with self.subTest(label):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_metadata(encode(document))
                self.assertEqual(caught.exception.status_code, 400)
        with self.assertRaises(RadioIngestError):
            validate_metadata(raw_nan)

    def test_missing_field_and_non_object_and_garbage_are_400(self):
        incomplete = make_metadata()
        del incomplete["segments"]
        for payload in (encode(incomplete), b"[]", b"not json", b"\xff\xfe"):
            with self.subTest(payload=payload[:10]):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_metadata(payload)
                self.assertEqual(caught.exception.status_code, 400)

    def test_error_details_never_echo_transcript_or_field_names_from_sender(self):
        with self.assertRaises(RadioIngestError) as caught:
            validate_metadata(encode(make_metadata(
                transcript="SECRET-TRANSCRIPT", sneaky_field="SECRET-VALUE",
            )))
        self.assertNotIn("SECRET", caught.exception.detail)
        self.assertNotIn("sneaky", caught.exception.detail)

    def test_wav_validation(self):
        validate_wav(make_wav())
        for label, payload in (
            ("empty", b""),
            ("not riff", b"ID3" + b"\x00" * 40),
            ("riff without wave", b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 40),
            ("stereo", make_wav(channels=2)),
            ("16 kHz", make_wav(rate=16000)),
            ("8-bit", make_wav(width=1)),
            ("no data chunk", make_wav()[:36]),
            ("golden placeholder", GOLDEN_AUDIO),
        ):
            with self.subTest(label):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_wav(payload)
                self.assertEqual(caught.exception.status_code, 400)

    def test_low_confidence_computation(self):
        def segment(logprob, no_speech):
            return {"avg_logprob": logprob, "no_speech_prob": no_speech}

        self.assertFalse(compute_low_confidence([]))
        self.assertFalse(compute_low_confidence([segment(-0.5, 0.1)]))
        self.assertFalse(compute_low_confidence([segment(-1.0, 0.6)]))  # strict
        self.assertTrue(compute_low_confidence([segment(-1.01, 0.1)]))
        self.assertTrue(compute_low_confidence([segment(-0.1, 0.61)]))
        self.assertTrue(compute_low_confidence(
            [segment(-0.1, 0.1), segment(-0.1, 0.1), segment(-2.0, 0.1)]
        ))

    def test_ilike_wildcards_are_escaped(self):
        self.assertEqual(escape_like_pattern("100%_off!"), "100!%!_off!!")
        self.assertEqual(escape_like_pattern("plain"), "plain")


# --------------------------------------------------- repository SQL contract


class RepositorySqlTests(unittest.TestCase):
    def test_list_uses_keyset_ilike_escape_and_bound_parameters_only(self):
        connection = FakeConnection()
        repository = repository_with(connection)
        cursor = (FIXED_NOW, "law_rx_20261016_140500_0001")
        hostile = "x'; DROP TABLE lcdash_radio.segments; --"
        repository.list_segments(
            channel="law", direction="rx",
            like_pattern=f"%{escape_like_pattern(hostile)}%",
            cursor=cursor, limit=51,
        )
        query, params = connection.calls[-1]
        self.assertIn("(started_at, segment_id) < (%s, %s)", query)
        self.assertIn("transcript ILIKE %s ESCAPE '!'", query)
        self.assertIn("ORDER BY started_at DESC, segment_id DESC LIMIT %s", query)
        self.assertNotIn("DROP", query)
        self.assertNotIn("law_rx", query)
        self.assertEqual(params[0], "law")
        self.assertEqual(params[1], "rx")
        self.assertIn("DROP TABLE", params[2])
        self.assertEqual(params[3:5], cursor)
        self.assertEqual(params[-1], 51)

    def test_list_without_filters_has_no_where_clause(self):
        connection = FakeConnection()
        repository_with(connection).list_segments(
            channel=None, direction=None, like_pattern=None, cursor=None, limit=11
        )
        query, params = connection.calls[-1]
        self.assertNotIn("WHERE", query)
        self.assertEqual(params, (11,))

    def test_insert_is_idempotent_and_reports_created_vs_duplicate(self):
        record = validate_metadata(encode(make_metadata()))
        created = repository_with(FakeConnection(rowcount=1)).insert_segment(record, "k")
        duplicate_connection = FakeConnection(rowcount=0)
        duplicate = repository_with(duplicate_connection).insert_segment(record, "k")
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertIn("ON CONFLICT (segment_id) DO NOTHING", duplicate_connection.calls[-1][0])

    def test_purge_touches_only_segments_older_than_30_days(self):
        connection = FakeConnection(rowcount=3)
        deleted = repository_with(connection).purge_expired()
        query = connection.calls[-1][0]
        self.assertEqual(deleted, 3)
        self.assertIn("DELETE FROM lcdash_radio.segments", query)
        self.assertIn("received_at < now() - interval '30 days'", query)
        self.assertNotIn("audit_events", query)

    def test_schema_file_is_idempotent_and_never_destructive(self):
        sql = (Path(radio_service.__file__).resolve().parents[2]
               / "database" / "radio_schema.sql").read_text(encoding="utf-8")
        for statement in ("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX"):
            for line in sql.splitlines():
                if line.startswith(statement):
                    self.assertIn("IF NOT EXISTS", line, line)
        self.assertNotIn("DROP ", sql.upper())
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertIn("lcdash_radio.audit_events", sql)
        self.assertIn("radio.segments.list", sql)
        self.assertIn("radio.segment.listen", sql)


# ----------------------------------------------------------- service tests


def ingest_record(service, **overrides):
    record = validate_metadata(encode(make_metadata(**overrides)))
    return record, service.ingest(record, make_wav())


class ServiceTests(unittest.TestCase):
    def test_ingest_stores_object_then_row_and_second_call_is_a_duplicate(self):
        service, store, s3 = build_service()
        record, first = ingest_record(service)
        self.assertEqual(first, "created")
        _, second = ingest_record(service)
        self.assertEqual(second, "duplicate")
        self.assertEqual(len(s3.put_calls), 1)  # a duplicate overwrites nothing
        put = s3.put_calls[0]
        self.assertEqual(put["Bucket"], "test-content-bucket")
        self.assertEqual(
            put["Key"],
            "radio/audio/law/2026/10/16/law_rx_20261016_140500_0001.wav",
        )
        self.assertEqual(put["ContentType"], "audio/wav")
        self.assertEqual(put["ServerSideEncryption"], "AES256")
        self.assertEqual(store.segments[record.segment_id][10], put["Key"])

    def test_audio_key_uses_the_utc_date(self):
        service, store, s3 = build_service()
        ingest_record(
            service, started_at="2026-10-16T23:30:00-05:00",
            ended_at="2026-10-16T23:30:05-05:00",
        )
        self.assertIn("/2026/10/17/", s3.put_calls[0]["Key"])

    def test_low_confidence_is_computed_at_ingest_and_listed(self):
        service, store, _ = build_service()
        ingest_record(service, segment_id="law_rx_confident_01")
        ingest_record(
            service, segment_id="law_rx_doubtful_01",
            started_at="2026-10-16T14:06:00+00:00",
            ended_at="2026-10-16T14:06:05+00:00",
            segments=[{"start": 0, "end": 2, "text": "mumble",
                       "avg_logprob": -1.4, "no_speech_prob": 0.1}],
        )
        items = {
            item["segment_id"]: item
            for item in service.list_segments(actor="a@x")["segments"]
        }
        self.assertFalse(items["law_rx_confident_01"]["low_confidence"])
        self.assertTrue(items["law_rx_doubtful_01"]["low_confidence"])

    def test_storage_failures_surface_as_retryable_errors(self):
        service, store, s3 = build_service()
        s3.fail_put = True
        with self.assertRaises(RadioStorageError):
            ingest_record(service)
        self.assertEqual(store.segments, {})  # no row without its object
        store.fail_connect = True
        with self.assertRaises(RadioDatabaseError):
            ingest_record(service)

    def test_schema_is_applied_once_per_process_not_per_request(self):
        service, store, _ = build_service()
        ingest_record(service)
        service.list_segments(actor="a@x")
        service.stats()
        service.fetch_audio("law_rx_20261016_140500_0001", actor="a@x")
        self.assertEqual(store.schema_calls, 1)

    def test_purge_runs_at_most_once_per_hour_per_process(self):
        now = [1000.0]
        service, store, _ = build_service(clock=lambda: now[0])
        ingest_record(service, segment_id="law_rx_purge_0001")
        self.assertEqual(store.purge_calls, 1)
        now[0] += 3599
        ingest_record(service, segment_id="law_rx_purge_0002")
        self.assertEqual(store.purge_calls, 1)
        now[0] += 2
        ingest_record(service, segment_id="law_rx_purge_0003")
        self.assertEqual(store.purge_calls, 2)

    def test_a_failed_purge_never_fails_the_ingest_and_is_still_throttled(self):
        now = [1000.0]
        service, store, _ = build_service(clock=lambda: now[0])
        store.purge_error = RadioDatabaseError("purge failed")
        _, status = ingest_record(service, segment_id="law_rx_purge_0001")
        self.assertEqual(status, "created")
        ingest_record(service, segment_id="law_rx_purge_0002")
        self.assertEqual(store.purge_calls, 1)

    def test_keyset_pagination_walks_newest_first_without_gaps_or_repeats(self):
        service, _, _ = build_service()
        ids = []
        for minute in range(7):
            segment_id = f"law_rx_page_{minute:04d}"
            ids.append(segment_id)
            ingest_record(
                service, segment_id=segment_id,
                started_at=f"2026-10-16T14:{minute:02d}:00+00:00",
                ended_at=f"2026-10-16T14:{minute:02d}:05+00:00",
            )
        # Two segments sharing a start time must still page deterministically.
        for suffix in ("a", "b"):
            segment_id = f"law_rx_tied_000{suffix}"
            ids.append(segment_id)
            ingest_record(
                service, segment_id=segment_id,
                started_at="2026-10-16T14:03:00+00:00",
                ended_at="2026-10-16T14:03:05+00:00",
            )
        seen, before, pages = [], "", 0
        while True:
            page = service.list_segments(actor="a@x", limit="4", before=before)
            seen.extend(item["segment_id"] for item in page["segments"])
            pages += 1
            if not page["has_more"]:
                self.assertIsNone(page["next_before"])
                break
            before = page["next_before"]
            self.assertTrue(before)
        self.assertEqual(sorted(seen), sorted(ids))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(pages, 3)
        times = [
            service.list_segments(actor="a@x", limit="200")["segments"][i]["started_at"]
            for i in range(len(ids))
        ]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_filters_and_wildcard_text_search(self):
        service, _, _ = build_service()
        ingest_record(service, segment_id="law_rx_text_0001",
                      transcript="Plate 100% clear")
        ingest_record(service, segment_id="ems_rx_text_0001", channel="ems",
                      direction="tx", transcript="Plate 1000 clear",
                      started_at="2026-10-16T14:06:00+00:00",
                      ended_at="2026-10-16T14:06:05+00:00")

        def ids(**filters):
            return [i["segment_id"] for i in service.list_segments(actor="a@x", **filters)["segments"]]

        self.assertEqual(ids(channel="ems"), ["ems_rx_text_0001"])
        self.assertEqual(ids(direction="rx"), ["law_rx_text_0001"])
        self.assertEqual(ids(q="PLATE 100"), ["ems_rx_text_0001", "law_rx_text_0001"])
        # A literal percent matches only the transcript that contains one.
        self.assertEqual(ids(q="100%"), ["law_rx_text_0001"])
        # An underscore is not "any character".
        self.assertEqual(ids(q="1_0"), [])

    def test_the_service_hands_the_repository_an_escaped_pattern(self):
        service, store, _ = build_service()
        seen = {}
        real = FakeRepository.list_segments

        def spy(self, **kwargs):
            seen.update(kwargs)
            return real(self, **kwargs)

        with patch.object(FakeRepository, "list_segments", spy):
            service.list_segments(actor="a@x", q="a%b_c!")
        self.assertEqual(seen["like_pattern"], "%a!%b!_c!!%")
        self.assertEqual(seen["limit"], 51)

    def test_invalid_filters_are_rejected_before_any_query(self):
        service, store, _ = build_service()
        for kwargs in (
            {"channel": "LAW"}, {"channel": "a" * 33}, {"direction": "up"},
            {"q": "x" * 201}, {"limit": "0"}, {"limit": "201"},
            {"limit": "abc"}, {"before": "garbage"},
            {"before": base64.urlsafe_b64encode(b"2026-10-16T00:00:00|x").decode()},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RadioQueryError):
                    service.list_segments(actor="a@x", **kwargs)
        self.assertEqual(store.audit, [])

    def test_listing_is_audited_with_the_actor_and_filters(self):
        service, store, _ = build_service()
        service.list_segments(actor=ADMIN_EMAIL, channel="law", q="plate")
        self.assertEqual(len(store.audit), 1)
        event = store.audit[0]
        self.assertEqual(event["event_type"], "radio.segments.list")
        self.assertEqual(event["actor"], ADMIN_EMAIL)
        self.assertEqual(event["detail"]["channel"], "law")

    def test_listing_fails_closed_when_the_audit_write_fails(self):
        service, store, _ = build_service()
        ingest_record(service)
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.list_segments(actor=ADMIN_EMAIL)

    def test_audio_available_reflects_the_15_day_audio_lifecycle(self):
        service, store, _ = build_service()
        ingest_record(service, segment_id="law_rx_fresh_0001")
        store.received_at = FIXED_NOW - timedelta(days=16)
        ingest_record(service, segment_id="law_rx_stale_0001",
                      started_at="2026-09-29T10:00:00+00:00",
                      ended_at="2026-09-29T10:00:05+00:00")
        items = {i["segment_id"]: i for i in service.list_segments(actor="a@x")["segments"]}
        self.assertTrue(items["law_rx_fresh_0001"]["audio_available"])
        self.assertFalse(items["law_rx_stale_0001"]["audio_available"])
        self.assertNotIn("audio_key", items["law_rx_fresh_0001"])

    def test_playback_returns_audio_and_records_a_listen_event(self):
        service, store, _ = build_service()
        ingest_record(service)
        audio = service.fetch_audio("law_rx_20261016_140500_0001", actor=ADMIN_EMAIL)
        self.assertEqual(audio, make_wav())
        listen = [e for e in store.audit if e["event_type"] == "radio.segment.listen"]
        self.assertEqual(len(listen), 1)
        self.assertEqual(listen[0]["actor"], ADMIN_EMAIL)
        self.assertEqual(listen[0]["segment_id"], "law_rx_20261016_140500_0001")

    def test_playback_fails_closed_when_the_audit_write_fails(self):
        service, store, _ = build_service()
        ingest_record(service)
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.fetch_audio("law_rx_20261016_140500_0001", actor=ADMIN_EMAIL)

    def test_playback_of_unknown_or_expired_audio_is_not_found(self):
        service, store, s3 = build_service()
        ingest_record(service)
        with self.assertRaises(RadioNotFoundError):
            service.fetch_audio("law_rx_never_stored", actor="a@x")
        with self.assertRaises(RadioNotFoundError):
            service.fetch_audio("../../etc/passwd", actor="a@x")
        s3.objects.clear()  # the 15-day lifecycle removed the object
        with self.assertRaises(RadioNotFoundError) as caught:
            service.fetch_audio("law_rx_20261016_140500_0001", actor="a@x")
        self.assertIn("expired", str(caught.exception))
        self.assertEqual(
            [e for e in store.audit if e["event_type"] == "radio.segment.listen"], []
        )

    def test_stats_report_channel_counts_and_newest_receipt(self):
        service, _, _ = build_service()
        self.assertEqual(
            service.stats(),
            {"window_hours": 24, "channels": [], "total": 0, "last_received_at": None},
        )
        ingest_record(service)
        stats = service.stats()
        self.assertEqual(stats["channels"], [{"channel": "law", "count": 1}])
        self.assertEqual(stats["last_received_at"], FIXED_NOW.isoformat())


# -------------------------------------------------------------- route tests


class _RouteCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
            ("radio_ingest_public_key", GOLDEN_PUBLIC_KEY),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def use_service(self, service):
        patcher = patch("app.main._radio_service", return_value=service)
        self.addCleanup(patcher.stop)
        patcher.start()


ADMIN_ROUTES = (
    ("/mindshare/radio", "page"),
    ("/api/radio/segments", "list"),
    ("/api/radio/segments?limit=not-a-number&channel=BAD", "list with bad params"),
    ("/api/radio/segments/law_rx_20261016_140500_0001/audio", "audio"),
    ("/api/radio/stats", "stats"),
)


class AdminOnlyTests(_RouteCase):
    def test_every_admin_route_denies_every_non_admin_uniformly(self):
        mock_service = MagicMock()
        self.use_service(mock_service)
        for role, identity in NON_ADMINS:
            self.sign_in_as(identity)
            for path, label in ADMIN_ROUTES:
                with self.subTest(role=role, route=label):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("audio/wav", response.headers.get("content-type", ""))
        self.assertEqual(mock_service.mock_calls, [])

    def test_admin_routes_never_fall_back_to_the_unauthenticated_path_when_identity_is_off(self):
        mock_service = MagicMock()
        self.use_service(mock_service)
        with patch.object(settings, "alb_identity_enabled", False):
            for path, label in ADMIN_ROUTES:
                with self.subTest(route=label):
                    self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(mock_service.mock_calls, [])

    def test_admin_list_passes_filters_and_the_verified_actor(self):
        mock_service = MagicMock()
        mock_service.list_segments.return_value = {
            "segments": [], "has_more": False, "next_before": None,
        }
        self.use_service(mock_service)
        self.sign_in_as(ADMIN)
        response = self.client.get(
            "/api/radio/segments",
            params={"channel": "law", "direction": "rx", "q": "plate",
                    "before": "abc", "limit": "10"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        mock_service.list_segments.assert_called_once_with(
            actor=ADMIN_EMAIL, channel="law", direction="rx", q="plate",
            before="abc", limit="10",
        )

    def test_actor_comes_from_the_verified_identity_not_from_headers(self):
        mock_service = MagicMock()
        mock_service.list_segments.return_value = {
            "segments": [], "has_more": False, "next_before": None,
        }
        self.use_service(mock_service)
        self.sign_in_as(ADMIN)
        self.client.get(
            "/api/radio/segments",
            headers={"x-auth-request-email": "forged@evil.test",
                     "cf-access-authenticated-user-email": "forged@evil.test"},
        )
        self.assertEqual(
            mock_service.list_segments.call_args.kwargs["actor"], ADMIN_EMAIL
        )

    def test_service_errors_map_to_400_404_503(self):
        mock_service = MagicMock()
        self.use_service(mock_service)
        self.sign_in_as(ADMIN)
        mock_service.list_segments.side_effect = RadioQueryError("The 'limit' value is invalid.")
        response = self.client.get("/api/radio/segments")
        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.json()["detail"])
        mock_service.list_segments.side_effect = RadioDatabaseError("x")
        self.assertEqual(self.client.get("/api/radio/segments").status_code, 503)
        mock_service.fetch_audio.side_effect = RadioNotFoundError(
            "Audio has expired or is no longer available."
        )
        response = self.client.get("/api/radio/segments/law_rx_20261016_140500_0001/audio")
        self.assertEqual(response.status_code, 404)
        self.assertIn("expired", response.json()["detail"])
        mock_service.stats.side_effect = RadioStorageError("x")
        self.assertEqual(self.client.get("/api/radio/stats").status_code, 503)

    def test_admin_sees_the_page_and_nav_entry_but_a_supervisor_does_not(self):
        self.sign_in_as(ADMIN)
        page = self.client.get("/mindshare/radio")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Machine-generated transcripts. Unverified.", page.text)
        # The page references its own script and stylesheet (cache-busted).
        self.assertIn("/static/js/lcdash-admin-radio.js?v=20261017-radio2", page.text)
        self.assertIn("/static/css/lcdash-admin-radio.css?v=20261017-radio2", page.text)
        reports = self.client.get("/reports")
        self.assertIn('href="/mindshare/radio"', reports.text)
        self.sign_in_as(_identity("s@911logan.com", "lcdash-pilot-supervisor"))
        reports = self.client.get("/reports")
        self.assertEqual(reports.status_code, 200)
        self.assertNotIn('href="/mindshare/radio"', reports.text)

    def test_admin_hub_card_links_to_radio_and_stays_separate_from_jack(self):
        self.sign_in_as(ADMIN)
        hub = self.client.get("/mindshare")
        self.assertIn('href="/mindshare/radio"', hub.text)
        self.assertIn('href="/mindshare/technical"', hub.text)
        self.assertNotIn("FUTURE MODULE", hub.text)


class PlaybackThroughRoutesTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        self.service, self.store, self.s3 = build_service()
        ingest_record(self.service)
        self.use_service(self.service)
        self.sign_in_as(ADMIN)
        self.audio_url = "/api/radio/segments/law_rx_20261016_140500_0001/audio"

    def test_audio_is_proxied_with_safe_headers_and_audited(self):
        response = self.client.get(self.audio_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, make_wav())
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["content-length"], str(len(make_wav())))
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        listen = [e for e in self.store.audit if e["event_type"] == "radio.segment.listen"]
        self.assertEqual([e["actor"] for e in listen], [ADMIN_EMAIL])

    def test_playback_fails_closed_with_503_when_the_audit_write_fails(self):
        self.store.fail_audit = True
        response = self.client.get(self.audio_url)
        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(response.content, make_wav())
        self.assertNotIn("audio/wav", response.headers.get("content-type", ""))

    def test_expired_audio_is_a_404_with_a_clear_detail(self):
        self.s3.objects.clear()
        response = self.client.get(self.audio_url)
        self.assertEqual(response.status_code, 404)
        self.assertIn("expired", response.json()["detail"])

    def test_listing_over_http_pages_and_carries_the_flags(self):
        response = self.client.get("/api/radio/segments", params={"limit": "1"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(body["segments"]), 1)
        item = body["segments"][0]
        self.assertIn("low_confidence", item)
        self.assertIn("audio_available", item)
        self.assertFalse(body["has_more"])

    def test_stats_over_http(self):
        body = self.client.get("/api/radio/stats").json()
        self.assertEqual(body["channels"], [{"channel": "law", "count": 1}])


def _multipart(metadata: bytes, audio: bytes, *, metadata_as_file: bool = True):
    if metadata_as_file:
        files = {
            "metadata": ("metadata.json", metadata, "application/json"),
            "audio": ("segment.wav", audio, "audio/wav"),
        }
        return {"files": files}
    return {
        "data": {"metadata": metadata.decode("utf-8")},
        "files": {"audio": ("segment.wav", audio, "audio/wav")},
    }


class IngestRouteTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        self.service, self.store, self.s3 = build_service()
        self.use_service(self.service)
        # The ingest caller carries no Cognito session; nothing identifies it.
        self.sign_in_as(None)
        self.audio = make_wav()
        self.metadata = encode(make_metadata())
        self.segment_id = "law_rx_20261016_140500_0001"

    def post(self, *, metadata=None, audio=None, timestamp=None, signature=None,
             headers=None, sign_over=None, metadata_as_file=True, extra_files=None):
        metadata = self.metadata if metadata is None else metadata
        audio = self.audio if audio is None else audio
        timestamp = int(time.time()) if timestamp is None else timestamp
        if signature is None:
            signed = sign_over or (self.segment_id, audio, metadata)
            signature = sign(timestamp, *signed)
        request_headers = {
            "X-Radio-Timestamp": str(timestamp),
            "X-Radio-Signature": signature,
        }
        request_headers.update(headers or {})
        kwargs = _multipart(metadata, audio, metadata_as_file=metadata_as_file)
        if extra_files:
            kwargs["files"] = {**kwargs["files"], **extra_files}
        return self.client.post("/api/radio/ingest", headers=request_headers, **kwargs)

    def test_valid_signed_segment_is_created_then_reported_as_duplicate(self):
        first = self.post()
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json(), {"status": "created", "segment_id": self.segment_id})
        second = self.post()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {"status": "duplicate", "segment_id": self.segment_id})
        self.assertEqual(len(self.s3.put_calls), 1)
        self.assertEqual(list(self.store.segments), [self.segment_id])
        self.assertEqual(
            self.s3.put_calls[0]["Key"],
            f"radio/audio/law/2026/10/16/{self.segment_id}.wav",
        )

    def test_metadata_sent_as_a_plain_form_field_is_accepted_too(self):
        response = self.post(metadata_as_file=False)
        self.assertEqual(response.status_code, 201, response.text)

    def test_unconfigured_ingest_answers_503_and_accepts_nothing(self):
        for blank in ("", "   "):
            with self.subTest(key=blank), patch.object(
                settings, "radio_ingest_public_key", blank
            ):
                response = self.post()
                self.assertEqual(response.status_code, 503)
        with patch.object(settings, "radio_ingest_public_key", "not-a-key"):
            self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.store.segments, {})
        self.assertEqual(self.s3.put_calls, [])

    def test_missing_headers_are_401_with_www_authenticate(self):
        response = self.client.post(
            "/api/radio/ingest", **_multipart(self.metadata, self.audio)
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Signature", response.headers["www-authenticate"])
        response = self.client.post(
            "/api/radio/ingest",
            headers={"X-Radio-Timestamp": str(int(time.time()))},
            **_multipart(self.metadata, self.audio),
        )
        self.assertEqual(response.status_code, 401)

    def test_wrong_signature_is_401_and_nothing_is_stored(self):
        other = Ed25519PrivateKey.generate()
        timestamp = int(time.time())
        forged = base64.b64encode(other.sign(build_signed_message(
            timestamp, self.segment_id, self.audio, self.metadata
        ))).decode()
        for label, signature in (
            ("other key", forged),
            ("garbage", "AAAA"),
            ("not base64", "%%%"),
        ):
            with self.subTest(label):
                response = self.post(timestamp=timestamp, signature=signature)
                self.assertEqual(response.status_code, 401)
                self.assertIn("www-authenticate", response.headers)
        self.assertEqual(self.store.segments, {})
        self.assertEqual(self.s3.put_calls, [])

    def test_a_signature_over_different_bytes_is_401(self):
        tampered_audio = make_wav(samples=801)
        response = self.post(
            audio=tampered_audio, sign_over=(self.segment_id, self.audio, self.metadata)
        )
        self.assertEqual(response.status_code, 401)
        other_metadata = encode(make_metadata(transcript="changed after signing"))
        response = self.post(
            metadata=other_metadata, sign_over=(self.segment_id, self.audio, self.metadata)
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.store.segments, {})

    def test_stale_future_and_malformed_timestamps_are_401(self):
        now = int(time.time())
        for label, timestamp in (("stale", now - 301 - 60), ("future", now + 301 + 60)):
            with self.subTest(label):
                self.assertEqual(self.post(timestamp=timestamp).status_code, 401)
        self.assertEqual(
            self.post(headers={"X-Radio-Timestamp": "yesterday"}).status_code, 401
        )
        self.assertEqual(self.store.segments, {})

    def test_timestamp_within_the_skew_window_is_accepted(self):
        self.assertEqual(self.post(timestamp=int(time.time()) - 240).status_code, 201)

    def test_oversize_request_is_413_by_content_length(self):
        big = b"RIFF" + b"\x00" * (radio_service.MAX_REQUEST_BYTES + 1)
        response = self.post(audio=big)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.s3.put_calls, [])

    def test_oversize_audio_part_under_the_request_cap_is_413(self):
        oversize = make_wav(samples=radio_service.MAX_AUDIO_BYTES // 2 + 16)
        self.assertGreater(len(oversize), radio_service.MAX_AUDIO_BYTES)
        self.assertLess(len(oversize), radio_service.MAX_REQUEST_BYTES)
        self.assertEqual(self.post(audio=oversize).status_code, 413)

    def test_oversize_metadata_part_is_413(self):
        padded = encode(make_metadata(transcript="x" * 20000))
        padded = padded + b" " * (radio_service.MAX_METADATA_BYTES + 1)
        for as_file in (True, False):
            with self.subTest(as_file=as_file):
                response = self.post(metadata=padded, metadata_as_file=as_file)
                self.assertEqual(response.status_code, 413)

    def test_chunked_body_without_content_length_is_still_capped(self):
        request = httpx.Request(
            "POST", "http://testserver/api/radio/ingest",
            files={
                "metadata": ("metadata.json", self.metadata, "application/json"),
                "audio": ("segment.wav", b"RIFF" + b"\x00" * (radio_service.MAX_REQUEST_BYTES + 1), "audio/wav"),
            },
        )
        body = request.read()
        chunks = [body[i:i + 65536] for i in range(0, len(body), 65536)]
        response = self.client.post(
            "/api/radio/ingest",
            content=iter(chunks),
            headers={
                "Content-Type": request.headers["content-type"],
                "X-Radio-Timestamp": str(int(time.time())),
                "X-Radio-Signature": "AAAA",
            },
        )
        self.assertEqual(response.status_code, 413)

    def test_bad_wav_is_400_even_when_correctly_signed(self):
        for label, audio in (
            ("not wav", b"ID3" + b"\x00" * 64),
            ("stereo", make_wav(channels=2)),
            ("wrong rate", make_wav(rate=16000)),
        ):
            with self.subTest(label):
                self.assertEqual(self.post(audio=audio).status_code, 400)
        self.assertEqual(self.store.segments, {})

    def test_unknown_metadata_field_is_400_and_never_echoed(self):
        metadata = encode(make_metadata(
            audio_file="C:/radio/x.wav", transcript="SECRET TRANSCRIPT TEXT"
        ))
        response = self.post(metadata=metadata)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("audio_file", response.text)

    def test_invalid_metadata_values_are_400(self):
        for label, document in (
            ("direction", make_metadata(direction="sideways")),
            ("schema", make_metadata(schema_version=2)),
            ("span", make_metadata(ended_at="2026-10-16T15:05:00+00:00")),
        ):
            with self.subTest(label):
                metadata = encode(document)
                self.assertEqual(self.post(metadata=metadata).status_code, 400)

    def test_structure_errors_are_400(self):
        only_audio = self.client.post(
            "/api/radio/ingest",
            headers={"X-Radio-Timestamp": str(int(time.time())), "X-Radio-Signature": "AAAA"},
            files={"audio": ("segment.wav", self.audio, "audio/wav")},
        )
        self.assertEqual(only_audio.status_code, 400)
        extra = self.post(extra_files={"note": ("note.txt", b"hi", "text/plain")})
        self.assertEqual(extra.status_code, 400)
        not_multipart = self.client.post(
            "/api/radio/ingest",
            headers={"X-Radio-Timestamp": str(int(time.time())), "X-Radio-Signature": "AAAA"},
            json={"metadata": "x"},
        )
        self.assertEqual(not_multipart.status_code, 400)
        audio_as_text = self.client.post(
            "/api/radio/ingest",
            headers={"X-Radio-Timestamp": str(int(time.time())), "X-Radio-Signature": "AAAA"},
            data={"audio": "RIFF", "metadata": "{}"},
            files={"pad": ("p.txt", b"", "text/plain")},
        )
        self.assertEqual(audio_as_text.status_code, 400)

    def test_storage_outage_is_503_so_the_listener_retries(self):
        self.s3.fail_put = True
        self.assertEqual(self.post().status_code, 503)
        self.s3.fail_put = False
        self.store.fail_connect = True
        self.assertEqual(self.post().status_code, 503)
        self.store.fail_connect = False
        self.assertEqual(self.post().status_code, 201)

    def test_ingest_authenticates_only_by_signature_not_by_identity(self):
        # An administrator's session does not stand in for a signature.
        self.sign_in_as(ADMIN)
        unsigned = self.client.post(
            "/api/radio/ingest", **_multipart(self.metadata, self.audio),
            headers={"x-amzn-oidc-data": "forged", "x-amzn-oidc-accesstoken": "forged"},
        )
        self.assertEqual(unsigned.status_code, 401)
        bad = self.post(signature="AAAA")
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.store.segments, {})
        # And a valid signature needs no identity at all, whatever headers
        # claim: forged identity headers neither help nor hurt.
        self.sign_in_as(None)
        ok = self.post(headers={
            "x-amzn-oidc-data": "forged", "x-auth-request-email": "forged@evil.test",
            "cf-access-authenticated-user-email": "forged@evil.test",
        })
        self.assertEqual(ok.status_code, 201, ok.text)
        # The ingest route is POST-only; the admin routes stay admin-only.
        self.assertEqual(self.client.get("/api/radio/ingest").status_code, 405)
        self.sign_in_as(_identity("s@911logan.com", "lcdash-pilot-supervisor"))
        self.assertEqual(self.client.get("/api/radio/segments").status_code, 403)

    def test_ingest_is_not_disabled_by_synthetic_disconnected_mode(self):
        self.assertEqual(settings.deployment_mode, "synthetic-disconnected")
        self.assertEqual(self.post().status_code, 201)

    def test_ingested_segment_is_then_visible_to_an_admin_only(self):
        self.assertEqual(self.post().status_code, 201)
        self.sign_in_as(_identity("s@911logan.com", "lcdash-pilot-supervisor"))
        self.assertEqual(self.client.get("/api/radio/segments").status_code, 403)
        self.sign_in_as(ADMIN)
        body = self.client.get("/api/radio/segments").json()
        self.assertEqual([i["segment_id"] for i in body["segments"]], [self.segment_id])
        self.assertEqual(
            body["segments"][0]["transcript"],
            "Synthetic unit check, traffic stop at the test lot.",
        )


class SettingsAndAssetsTests(unittest.TestCase):
    def test_radio_settings_defaults(self):
        self.assertEqual(settings.radio_bucket, "lcdash-p1-logan-use1-862772137583-content")
        self.assertEqual(settings.radio_audio_prefix, "radio/audio/")

    def test_page_script_never_uses_innerhtml(self):
        # Transcripts are untrusted machine output; only textContent may carry them.
        script = (Path(__file__).resolve().parents[2]
                  / "static" / "js" / "lcdash-admin-radio.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("outerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)
        self.assertNotIn("document.write", script)
        self.assertIn('preload = "none"', script)
        # Server text is only ever assigned through textContent.
        self.assertIn("textContent", script)

    def test_page_template_has_channel_tabs_and_card_feed(self):
        # The transcripts are a tabbed feed of cards (an <ol> built by the
        # script), not a wide table: a table clipped the transcript and play
        # columns off-screen at phone width.
        template = (Path(__file__).resolve().parents[2]
                    / "templates" / "mindshare_radio.html").read_text(encoding="utf-8")
        self.assertIn('role="tablist"', template)
        self.assertEqual(template.count('role="tab"'), 4)
        for channel, label in (("", "All channels"), ("law", "Law"),
                               ("ems", "EMS"), ("fire", "Fire")):
            self.assertIn(f'data-channel="{channel}"', template)
            self.assertIn(f'<span class="radio-tab-label">{label}</span>', template)
        self.assertIn('id="radio-tab-all"', template)
        self.assertIn('aria-selected="true"', template)
        self.assertIn('id="radio-panels"', template)
        self.assertNotIn("<table", template)
        self.assertNotIn("<audio", template)
        self.assertIn("Machine-generated transcripts. Unverified.", template)
        script = (Path(__file__).resolve().parents[2]
                  / "static" / "js" / "lcdash-admin-radio.js").read_text(encoding="utf-8")
        for needle in ('"tabpanel"', '"ol"', "ArrowRight", "ArrowLeft", "new Audio()",
                       "/api/radio/segments/", "(no speech recognized)", "Audio expired",
                       "Low confidence", "LCDashSession"):
            self.assertIn(needle, script)
        # No native <audio controls> element and no modal dialogs (comments
        # that merely mention them are not code).
        code = "\n".join(
            line for line in script.splitlines() if not line.strip().startswith("//")
        )
        self.assertNotIn('createElement("audio")', code)
        self.assertNotIn("alert(", code)
        self.assertNotIn("confirm(", code)


if __name__ == "__main__":
    unittest.main()
