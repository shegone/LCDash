"""Mindshare radio v3: listener heartbeat, hint words, glossary, listener health.

Contract under test: docs/MINDSHARE_RADIO_HEALTH.md. Offline and synthetic, on
the in-memory fakes from test_radio_routes.py (the fake repository reproduces
the real one's transaction semantics, so "no audit record, no change" is
provable for hint words).

What is proved:

* the heartbeat signed message against a golden vector built from the same
  published seed as the ingest vector (the vector is documented in
  ``HeartbeatGoldenVectorTests``), and that the two message domains cannot be
  swapped for one another;
* POST /api/radio/heartbeat authenticates ONLY by signature, fails closed when
  unconfigured, verifies the signature over the raw bytes BEFORE parsing,
  validates the schema strictly, never echoes request content, never touches
  ``segments``, stores one row, and writes exactly one content-free
  ``radio-heartbeat ok`` log line per success and none per failure;
* the glossary: ordering, case-insensitive de-duplication, the 600 character
  cap, a stable version, the version-only reply, and the empty glossary;
* the admin hint-word API: access matrix, validation after the access check,
  duplicate and cap conflicts, audit rows, fail-closed writes;
* ``GET /api/radio/stats`` ``listener``: reporting true/false/null;
* the schema file: new tables, the widened audit constraint (fresh and
  upgrade), and the tier modules keeping every new path closed.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import re
import time
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config.settings import settings
from app.core import radio_access
from app.services import radio_service, radio_text
from app.services.radio_service import (
    RadioConflictError,
    RadioDatabaseError,
    RadioIngestError,
    RadioNotFoundError,
    RadioQueryError,
    build_heartbeat_signed_message,
    build_signed_message,
    load_public_key,
    validate_heartbeat,
    verify_heartbeat_signature,
    verify_signature,
)

from .test_radio_corrections import ScriptedConnection
from .test_radio_routes import (
    ADMIN,
    ADMIN_EMAIL,
    DENIED_CALLERS,
    FIXED_NOW,
    GOLDEN_PUBLIC_KEY,
    GOLDEN_SEED,
    HUMAN_ROLES,
    NON_ADMIN_HUMANS,
    PRIVATE_KEY,
    FakeRepository,
    FakeStore,
    _identity,
    _RouteCase,
    build_service,
    ingest_record,
    repository_with,
)

SCHEMA_SQL = (
    Path(radio_service.__file__).resolve().parents[2] / "database" / "radio_schema.sql"
).read_text(encoding="utf-8")

# ---------------------------------------------------------- heartbeat fixtures

LOG_LINE = re.compile(
    r"^radio-heartbeat ok listeners=\d+ packets_interval=\d+ undecoded=\d+$"
)


def make_heartbeat(**overrides) -> dict:
    document = {
        "schema_version": 1,
        "sent_at": "2026-10-16T15:00:00+00:00",
        "capture": {
            "stats_at": "2026-10-16T14:59:45+00:00",
            "started_at": "2026-10-16T08:00:00+00:00",
            "uptime_seconds": 25200,
            "interval_seconds": 60,
            "listeners": [
                {
                    "channel": "law", "direction": "rx",
                    "packets_total": 120000, "packets_interval": 3412,
                    "pcmu_packets_total": 119000,
                    "other_payload_types": {"103": 12},
                    "bytes_interval": 545920, "sources_seen": 2,
                    "segments_closed_total": 41,
                    "last_packet_at": "2026-10-16T14:59:58+00:00",
                },
                {
                    "channel": "fire", "direction": "rx",
                    "packets_total": 800, "packets_interval": 0,
                    "pcmu_packets_total": 800, "other_payload_types": {},
                    "bytes_interval": 0, "sources_seen": 1,
                    "segments_closed_total": 3, "last_packet_at": None,
                },
            ],
        },
        "transcriber": {"pending": 1, "failed": 0},
        "uploader": {
            "queued": 2, "oldest_queued_age_seconds": 14, "rejected_total": 0,
            "last_success_at": "2026-10-16T14:59:30+00:00",
        },
        "glossary_version": None,
    }
    document.update(overrides)
    return document


def mutated(document: dict, *path, value) -> dict:
    """A deep copy of ``document`` with the value at ``path`` replaced.

    ``value`` of the sentinel ``DELETE`` removes the key instead.
    """
    clone = copy.deepcopy(document)
    node = clone
    for step in path[:-1]:
        node = node[step]
    if value is DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return clone


DELETE = object()


def hb_encode(document: dict) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def hb_sign(timestamp: int, body: bytes) -> str:
    return base64.b64encode(
        PRIVATE_KEY.sign(build_heartbeat_signed_message(timestamp, body))
    ).decode("ascii")


# ------------------------------------------------------------ golden vector

# The heartbeat vector reuses the published ingest seed so both sides can test
# with one key. Message (three lines, single "\n", no trailing newline):
#
#   lcdash-radio-heartbeat-v1
#   1760000000
#   a9d5f6d002d956b8af5787a05e0ca000d45c03977ffa54ee8fbed719fed5fd23
#
# where the last line is sha256 of the body b'{"schema_version":1}'.
HB_GOLDEN_TIMESTAMP = 1760000000
HB_GOLDEN_BODY = b'{"schema_version":1}'
HB_GOLDEN_BODY_SHA256 = "a9d5f6d002d956b8af5787a05e0ca000d45c03977ffa54ee8fbed719fed5fd23"
HB_GOLDEN_SIGNATURE = (
    "fasKho2p65fvf+qCEE706U5cWiBwlVVrpc1W7H3hnxX3EWGB4uVOxxeHQK63qleGeQA2OFXwlxU8/M0HozzTAA=="
)


class HeartbeatGoldenVectorTests(unittest.TestCase):
    def test_golden_seed_is_the_published_ingest_seed(self):
        self.assertEqual(
            GOLDEN_SEED,
            bytes.fromhex(
                "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
            ),
        )

    def test_body_hash_matches_the_documented_vector(self):
        self.assertEqual(
            hashlib.sha256(HB_GOLDEN_BODY).hexdigest(), HB_GOLDEN_BODY_SHA256
        )

    def test_signed_message_layout_is_three_lines_no_trailing_newline(self):
        message = build_heartbeat_signed_message(HB_GOLDEN_TIMESTAMP, HB_GOLDEN_BODY)
        self.assertEqual(
            message.decode("utf-8").split("\n"),
            ["lcdash-radio-heartbeat-v1", "1760000000", HB_GOLDEN_BODY_SHA256],
        )
        self.assertFalse(message.endswith(b"\n"))

    def test_golden_public_key_verifies_the_golden_signature_verbatim(self):
        verify_heartbeat_signature(
            load_public_key(GOLDEN_PUBLIC_KEY),
            timestamp=HB_GOLDEN_TIMESTAMP,
            signature_b64=HB_GOLDEN_SIGNATURE,
            body=HB_GOLDEN_BODY,
        )

    def test_golden_signature_is_reproduced_by_the_golden_seed(self):
        self.assertEqual(hb_sign(HB_GOLDEN_TIMESTAMP, HB_GOLDEN_BODY), HB_GOLDEN_SIGNATURE)
        key = Ed25519PrivateKey.from_private_bytes(GOLDEN_SEED)
        message = build_heartbeat_signed_message(HB_GOLDEN_TIMESTAMP, HB_GOLDEN_BODY)
        self.assertEqual(
            base64.b64encode(key.sign(message)).decode(), HB_GOLDEN_SIGNATURE
        )

    def test_any_changed_input_breaks_the_signature(self):
        key = load_public_key(GOLDEN_PUBLIC_KEY)
        good = dict(
            timestamp=HB_GOLDEN_TIMESTAMP, signature_b64=HB_GOLDEN_SIGNATURE,
            body=HB_GOLDEN_BODY,
        )
        for name, value in (
            ("timestamp", HB_GOLDEN_TIMESTAMP + 1),
            ("body", b'{"schema_version":2}'),
            ("body", HB_GOLDEN_BODY + b" "),
            ("signature_b64", "not base64!"),
            ("signature_b64", base64.b64encode(b"short").decode()),
            ("signature_b64", ""),
            ("signature_b64", None),
        ):
            with self.subTest(changed=name, value=value):
                with self.assertRaises(RadioIngestError) as caught:
                    verify_heartbeat_signature(key, **{**good, name: value})
                self.assertEqual(caught.exception.status_code, 401)

    def test_ingest_and_heartbeat_message_domains_cannot_be_swapped(self):
        key = load_public_key(GOLDEN_PUBLIC_KEY)
        # A signature over an ingest-shaped message is no heartbeat signature,
        # and a heartbeat signature is no ingest signature.
        ingest_message = build_signed_message(
            HB_GOLDEN_TIMESTAMP, "law_rx_test", b"", HB_GOLDEN_BODY
        )
        ingest_signature = base64.b64encode(PRIVATE_KEY.sign(ingest_message)).decode()
        with self.assertRaises(RadioIngestError):
            verify_heartbeat_signature(
                key, timestamp=HB_GOLDEN_TIMESTAMP,
                signature_b64=ingest_signature, body=HB_GOLDEN_BODY,
            )
        with self.assertRaises(RadioIngestError):
            verify_signature(
                key, timestamp=HB_GOLDEN_TIMESTAMP,
                signature_b64=HB_GOLDEN_SIGNATURE, segment_id="law_rx_test",
                audio=b"", metadata=HB_GOLDEN_BODY,
            )


# -------------------------------------------------------- schema validation


class HeartbeatValidationTests(unittest.TestCase):
    def test_a_valid_heartbeat_normalizes(self):
        stored = validate_heartbeat(hb_encode(make_heartbeat(
            sent_at="2026-10-16T10:00:00-05:00",
            glossary_version="abc123def456",
        )))
        self.assertEqual(stored["sent_at"], "2026-10-16T15:00:00+00:00")
        self.assertEqual(stored["glossary_version"], "abc123def456")
        self.assertEqual(stored["capture"]["listeners"][0]["other_payload_types"], {"103": 12})
        self.assertIsNone(stored["capture"]["listeners"][1]["last_packet_at"])
        self.assertEqual(stored["uploader"]["oldest_queued_age_seconds"], 14)

    def test_nulls_and_an_absent_glossary_version_are_accepted(self):
        document = make_heartbeat()
        document["capture"]["stats_at"] = None
        document["capture"]["started_at"] = None
        document["uploader"]["oldest_queued_age_seconds"] = None
        document["uploader"]["last_success_at"] = None
        del document["glossary_version"]
        stored = validate_heartbeat(hb_encode(document))
        self.assertIsNone(stored["capture"]["stats_at"])
        self.assertIsNone(stored["uploader"]["oldest_queued_age_seconds"])
        self.assertIsNone(stored["glossary_version"])

    def test_head_stall_and_parked_fields_are_optional_and_normalized(self):
        # 2026-10-23: an older uploader omits the three fields; absent = quiet.
        stored = validate_heartbeat(hb_encode(make_heartbeat()))
        self.assertEqual(
            {k: stored["uploader"][k] for k in ("head_failing_since", "head_attempts", "parked_total")},
            {"head_failing_since": None, "head_attempts": 0, "parked_total": 0},
        )
        stalled = make_heartbeat()
        stalled["uploader"].update({
            "head_failing_since": "2026-10-23T03:45:21-04:00",
            "head_attempts": 7,
            "parked_total": 1,
        })
        stored = validate_heartbeat(hb_encode(stalled))
        self.assertEqual(stored["uploader"]["head_failing_since"], "2026-10-23T07:45:21+00:00")
        self.assertEqual(stored["uploader"]["head_attempts"], 7)
        self.assertEqual(stored["uploader"]["parked_total"], 1)

    def test_zero_listeners_is_valid_and_so_is_the_maximum(self):
        empty = mutated(make_heartbeat(), "capture", "listeners", value=[])
        self.assertEqual(validate_heartbeat(hb_encode(empty))["capture"]["listeners"], [])
        row = make_heartbeat()["capture"]["listeners"][0]
        full = mutated(make_heartbeat(), "capture", "listeners", value=[row] * 64)
        self.assertEqual(len(validate_heartbeat(hb_encode(full))["capture"]["listeners"]), 64)

    def invalid_cases(self) -> dict[str, dict]:
        base = make_heartbeat()
        row = base["capture"]["listeners"][0]
        return {
            "unknown top-level key": {**base, "hostname": "lcdash-server"},
            "unknown capture key": mutated(base, "capture", "ip", value="10.0.0.1"),
            "unknown listener key": mutated(base, "capture", "listeners", 0, "source_ip", value="x"),
            "unknown transcriber key": mutated(base, "transcriber", "text", value="x"),
            "unknown uploader key": mutated(base, "uploader", "path", value="x"),
            "schema_version 2": {**base, "schema_version": 2},
            "schema_version bool": {**base, "schema_version": True},
            "schema_version missing": mutated(base, "schema_version", value=DELETE),
            "sent_at missing": mutated(base, "sent_at", value=DELETE),
            "sent_at naive": {**base, "sent_at": "2026-10-16T15:00:00"},
            "sent_at not a string": {**base, "sent_at": 1760000000},
            "sent_at garbage": {**base, "sent_at": "yesterday"},
            "sent_at too long": {**base, "sent_at": "2026-10-16T15:00:00+00:00" + " " * 60},
            "capture missing": mutated(base, "capture", value=DELETE),
            "capture not an object": {**base, "capture": []},
            "capture field missing": mutated(base, "capture", "uptime_seconds", value=DELETE),
            "listeners not a list": mutated(base, "capture", "listeners", value={}),
            "65 listeners": mutated(base, "capture", "listeners", value=[row] * 65),
            "listener not an object": mutated(base, "capture", "listeners", value=["x"]),
            "listener field missing": mutated(base, "capture", "listeners", 0, "bytes_interval", value=DELETE),
            "bad channel": mutated(base, "capture", "listeners", 0, "channel", value="Law Enforcement"),
            "channel too long": mutated(base, "capture", "listeners", 0, "channel", value="a" * 33),
            "bad direction": mutated(base, "capture", "listeners", 0, "direction", value="both"),
            "negative count": mutated(base, "capture", "listeners", 0, "packets_interval", value=-1),
            "float count": mutated(base, "capture", "listeners", 0, "packets_total", value=1.5),
            "bool count": mutated(base, "capture", "listeners", 0, "sources_seen", value=True),
            "string count": mutated(base, "capture", "listeners", 0, "bytes_interval", value="5"),
            "absurd count": mutated(base, "capture", "listeners", 0, "packets_total", value=10**18),
            "null where a count is required": mutated(base, "capture", "uptime_seconds", value=None),
            "17 other payload types": mutated(
                base, "capture", "listeners", 0, "other_payload_types",
                value={str(n): 1 for n in range(17)},
            ),
            "other payload types not an object": mutated(base, "capture", "listeners", 0, "other_payload_types", value=[1]),
            "payload type 128": mutated(base, "capture", "listeners", 0, "other_payload_types", value={"128": 1}),
            "payload type leading zero": mutated(base, "capture", "listeners", 0, "other_payload_types", value={"03": 1}),
            "payload type not numeric": mutated(base, "capture", "listeners", 0, "other_payload_types", value={"pcmu": 1}),
            "payload type count negative": mutated(base, "capture", "listeners", 0, "other_payload_types", value={"103": -5}),
            "last_packet_at naive": mutated(base, "capture", "listeners", 0, "last_packet_at", value="2026-10-16T14:59:58"),
            "transcriber missing": mutated(base, "transcriber", value=DELETE),
            "transcriber field missing": mutated(base, "transcriber", "failed", value=DELETE),
            "transcriber negative": mutated(base, "transcriber", "pending", value=-1),
            "uploader missing": mutated(base, "uploader", value=DELETE),
            "uploader field missing": mutated(base, "uploader", "rejected_total", value=DELETE),
            "uploader age negative": mutated(base, "uploader", "oldest_queued_age_seconds", value=-1),
            "uploader last_success naive": mutated(base, "uploader", "last_success_at", value="2026-10-16T14:59:30"),
            "uploader head_attempts negative": mutated(base, "uploader", "head_attempts", value=-1),
            "uploader head_attempts not an int": mutated(base, "uploader", "head_attempts", value="3"),
            "uploader parked_total negative": mutated(base, "uploader", "parked_total", value=-1),
            "uploader head_failing_since naive": mutated(base, "uploader", "head_failing_since", value="2026-10-23T07:45:21"),
            "uploader unknown key": mutated(base, "uploader", "parked", value=1),
            "glossary_version too long": {**base, "glossary_version": "v" * 33},
            "glossary_version not a string": {**base, "glossary_version": 7},
        }

    def test_every_invalid_shape_is_a_400_that_echoes_nothing(self):
        for label, document in self.invalid_cases().items():
            with self.subTest(label):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_heartbeat(hb_encode(document))
                self.assertEqual(caught.exception.status_code, 400)
                detail = caught.exception.detail
                for fragment in ("hostname", "source_ip", "10.0.0.1", "lcdash-server",
                                 "Law Enforcement", "yesterday"):
                    self.assertNotIn(fragment, detail)

    def test_non_objects_garbage_and_non_finite_numbers_are_400(self):
        good = hb_encode(make_heartbeat())
        for label, payload in (
            ("array", b"[]"), ("string", b'"x"'), ("null", b"null"), ("empty", b""),
            ("garbage", b"not json"), ("bad utf-8", b"\xff\xfe"),
            ("NaN", good.replace(b'"pending":1', b'"pending":NaN')),
            ("Infinity", good.replace(b'"pending":1', b'"pending":Infinity')),
            ("deep", b"[" * 5000),
        ):
            with self.subTest(label):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_heartbeat(payload)
                self.assertEqual(caught.exception.status_code, 400)


# ------------------------------------------------------------ glossary build


class GlossaryBuildTests(unittest.TestCase):
    def test_hints_then_watch_phrases_then_fix_replacements_in_order(self):
        glossary = radio_text.build_glossary(
            ["Zanesfield", "Quincy"], ["structure fire", "Quincy"], ["Logan County"]
        )
        self.assertEqual(
            glossary["hotwords"], "Zanesfield, Quincy, structure fire, Logan County"
        )
        self.assertEqual(
            glossary["prompt"],
            "Public safety radio traffic. Names and terms that may be spoken: "
            "Zanesfield, Quincy, structure fire, Logan County.",
        )

    def test_duplicates_collapse_case_insensitively_keeping_the_first_spelling(self):
        glossary = radio_text.build_glossary(
            ["Bellefontaine", "bellefontaine"], ["BELLEFONTAINE", "West  Liberty"],
            ["west liberty", "  ", None, ""],
        )
        self.assertEqual(glossary["hotwords"], "Bellefontaine, West Liberty")

    def test_the_string_never_exceeds_600_characters_and_stops_at_the_first_misfit(self):
        words = [f"{n:02d}" + "x" * 8 for n in range(100)]  # ten characters each
        glossary = radio_text.build_glossary(words, [], [])
        hotwords = glossary["hotwords"]
        self.assertLessEqual(len(hotwords), 600)
        # 50 items of 10 characters plus 49 separators is 598; a 51st would be 610.
        self.assertEqual(hotwords.count(", ") + 1, 50)
        self.assertEqual(len(hotwords), 598)
        self.assertTrue(hotwords.endswith(words[49]))
        # A later, shorter item must not jump the queue past the misfit.
        queue = radio_text.build_glossary(["a" * 590, "b" * 20, "cc"], [], [])
        self.assertEqual(queue["hotwords"], "a" * 590)

    def test_exactly_600_characters_fits(self):
        glossary = radio_text.build_glossary(["a" * 300, "b" * 298], [], [])
        self.assertEqual(len(glossary["hotwords"]), 600)
        smaller = radio_text.build_glossary(["a" * 300, "b" * 299], [], [])
        self.assertEqual(smaller["hotwords"], "a" * 300)

    def test_a_single_item_longer_than_the_cap_yields_the_empty_glossary(self):
        self.assertEqual(
            radio_text.build_glossary(["a" * 601], [], []),
            {"version": "empty", "hotwords": "", "prompt": ""},
        )

    def test_version_is_the_first_12_hex_of_sha256_and_is_stable(self):
        first = radio_text.build_glossary(["Zanesfield"], ["structure fire"], [])
        again = radio_text.build_glossary(["Zanesfield"], ["structure fire"], [])
        expected = hashlib.sha256(
            b"Zanesfield, structure fire"
        ).hexdigest()[:12]
        self.assertEqual(first["version"], expected)
        self.assertEqual(first, again)
        self.assertRegex(first["version"], r"^[0-9a-f]{12}$")

    def test_version_changes_with_content_and_order(self):
        a = radio_text.build_glossary(["one", "two"], [], [])
        b = radio_text.build_glossary(["two", "one"], [], [])
        c = radio_text.build_glossary(["one", "two", "three"], [], [])
        self.assertEqual(len({a["version"], b["version"], c["version"]}), 3)

    def test_empty_glossary(self):
        self.assertEqual(
            radio_text.build_glossary([], [], []),
            {"version": "empty", "hotwords": "", "prompt": ""},
        )
        self.assertEqual(
            radio_text.build_glossary(["", " "], [None], []),
            {"version": "empty", "hotwords": "", "prompt": ""},
        )


class GlossaryFromStoreTests(unittest.TestCase):
    """The service feeds the three live sources into the builder."""

    def test_sources_are_hints_enabled_watch_phrases_and_fix_replacements(self):
        service, store, _ = build_service()
        service.add_hint({"phrase": "Zanesfield"}, actor=ADMIN_EMAIL)
        service.add_watch_term({"phrase": "structure fire"}, actor=ADMIN_EMAIL)
        off = service.add_watch_term({"phrase": "disabled phrase"}, actor=ADMIN_EMAIL)
        service.update_watch_term(off["term_id"], {"enabled": False}, actor=ADMIN_EMAIL)
        service.add_replacement(
            {"find": "log in", "replace": "Logan"}, actor=ADMIN_EMAIL
        )
        service.add_replacement(
            {"find": "uh", "replace": ""}, actor=ADMIN_EMAIL
        )
        reply = service.record_heartbeat(validate_heartbeat(hb_encode(make_heartbeat())))
        self.assertEqual(
            reply["glossary"]["hotwords"], "Zanesfield, structure fire, Logan"
        )
        self.assertNotIn("disabled phrase", reply["glossary"]["hotwords"])


# ---------------------------------------------------------- heartbeat route


class _HeartbeatCase(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        self.service, self.store, self.s3 = build_service()
        self.use_service(self.service)
        # The listener carries no Cognito session; nothing identifies it.
        self.sign_in_as(None)
        self.body = hb_encode(make_heartbeat())

    def post(self, body=None, *, timestamp=None, signature=None, headers=None,
             sign_over=None, content=None):
        body = self.body if body is None else body
        timestamp = int(time.time()) if timestamp is None else timestamp
        if signature is None:
            signature = hb_sign(timestamp, body if sign_over is None else sign_over)
        request_headers = {
            "X-Radio-Timestamp": str(timestamp),
            "X-Radio-Signature": signature,
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        return self.client.post(
            "/api/radio/heartbeat",
            content=body if content is None else content,
            headers=request_headers,
        )


class HeartbeatRouteTests(_HeartbeatCase):
    def test_a_valid_signed_heartbeat_is_stored_and_answered(self):
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        reply = response.json()
        self.assertEqual(set(reply), {"status", "server_time", "glossary"})
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(reply["server_time"], FIXED_NOW.isoformat())
        # Nothing configured yet: the empty glossary, full form (the listener
        # sent glossary_version null, which is not "empty").
        self.assertEqual(
            reply["glossary"], {"version": "empty", "hotwords": "", "prompt": ""}
        )
        payload, received_at = self.store.listener
        self.assertEqual(received_at, FIXED_NOW)
        self.assertEqual(payload, validate_heartbeat(self.body))

    def test_each_heartbeat_replaces_the_one_row(self):
        for pending in (1, 2, 3):
            body = hb_encode(mutated(make_heartbeat(), "transcriber", "pending", value=pending))
            with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
                self.assertEqual(self.post(body).status_code, 200)
        self.assertEqual(self.store.listener_writes, 3)
        self.assertEqual(self.store.listener[0]["transcriber"]["pending"], 3)

    def test_the_reply_carries_the_full_glossary_then_only_the_version(self):
        self.service.add_hint({"phrase": "Zanesfield"}, actor=ADMIN_EMAIL)
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            first = self.post().json()["glossary"]
        self.assertEqual(first["hotwords"], "Zanesfield")
        self.assertTrue(first["prompt"].endswith("Zanesfield."))
        self.assertRegex(first["version"], r"^[0-9a-f]{12}$")

        known = hb_encode(make_heartbeat(glossary_version=first["version"]))
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            same = self.post(known).json()["glossary"]
        self.assertEqual(same, {"version": first["version"]})

        stale = hb_encode(make_heartbeat(glossary_version="000000000000"))
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            changed = self.post(stale).json()["glossary"]
        self.assertEqual(changed, first)

        # A new hint changes the version the next heartbeat sees.
        self.service.add_hint({"phrase": "Quincy"}, actor=ADMIN_EMAIL)
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            after = self.post(known).json()["glossary"]
        self.assertEqual(after["hotwords"], "Zanesfield, Quincy")
        self.assertNotEqual(after["version"], first["version"])

    def test_version_only_reply_also_applies_to_the_empty_glossary(self):
        body = hb_encode(make_heartbeat(glossary_version="empty"))
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post(body).json()["glossary"], {"version": "empty"})

    def test_the_glossary_is_capped_at_600_characters_over_http(self):
        for index in range(15):
            self.service.add_hint(
                {"phrase": f"{index:02d}" + "w" * 58}, actor=ADMIN_EMAIL
            )
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            glossary = self.post().json()["glossary"]
        self.assertLessEqual(len(glossary["hotwords"]), 600)
        self.assertEqual(glossary["hotwords"].count(", ") + 1, 9)

    def test_unconfigured_heartbeat_answers_503_and_stores_nothing(self):
        for blank in ("", "   "):
            with self.subTest(key=blank), patch.object(
                settings, "radio_ingest_public_key", blank
            ):
                self.assertEqual(self.post().status_code, 503)
        with patch.object(settings, "radio_ingest_public_key", "not-a-key"):
            self.assertEqual(self.post().status_code, 503)
        self.assertIsNone(self.store.listener)

    def test_missing_headers_are_401_with_www_authenticate(self):
        response = self.client.post(
            "/api/radio/heartbeat", content=self.body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Signature", response.headers["www-authenticate"])
        response = self.client.post(
            "/api/radio/heartbeat", content=self.body,
            headers={"X-Radio-Timestamp": str(int(time.time()))},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.listener)

    def test_a_wrong_or_malformed_signature_is_401(self):
        other = Ed25519PrivateKey.generate()
        timestamp = int(time.time())
        forged = base64.b64encode(other.sign(
            build_heartbeat_signed_message(timestamp, self.body)
        )).decode()
        for label, signature in (
            ("other key", forged), ("garbage", "AAAA"), ("not base64", "%%%"),
        ):
            with self.subTest(label):
                response = self.post(timestamp=timestamp, signature=signature)
                self.assertEqual(response.status_code, 401)
                self.assertIn("www-authenticate", response.headers)
        self.assertIsNone(self.store.listener)

    def test_a_tampered_body_or_timestamp_is_401(self):
        tampered = hb_encode(mutated(make_heartbeat(), "transcriber", "failed", value=99))
        self.assertEqual(self.post(tampered, sign_over=self.body).status_code, 401)
        timestamp = int(time.time())
        signature = hb_sign(timestamp, self.body)
        response = self.post(
            timestamp=timestamp + 1, signature=signature
        )
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.listener)

    def test_an_ingest_signature_is_not_a_heartbeat_signature(self):
        timestamp = int(time.time())
        ingest_style = base64.b64encode(PRIVATE_KEY.sign(
            build_signed_message(timestamp, "law_rx_test", b"", self.body)
        )).decode()
        self.assertEqual(
            self.post(timestamp=timestamp, signature=ingest_style).status_code, 401
        )

    def test_stale_future_and_malformed_timestamps_are_401(self):
        now = int(time.time())
        for label, timestamp in (("stale", now - 360), ("future", now + 360)):
            with self.subTest(label):
                self.assertEqual(self.post(timestamp=timestamp).status_code, 401)
        self.assertEqual(
            self.post(headers={"X-Radio-Timestamp": "yesterday"}).status_code, 401
        )
        self.assertIsNone(self.store.listener)

    def test_a_timestamp_within_the_skew_window_is_accepted(self):
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post(timestamp=int(time.time()) - 240).status_code, 200)

    def test_the_signature_is_checked_before_the_body_is_parsed(self):
        garbage = b"{{ definitely not json"
        # Unsigned garbage is a 401, never a 400: the parser is not reached.
        self.assertEqual(self.post(garbage, signature="AAAA").status_code, 401)
        # The same garbage with a valid signature is a 400.
        self.assertEqual(self.post(garbage).status_code, 400)

    def test_oversize_requests_are_413(self):
        big = b" " * (radio_service.MAX_HEARTBEAT_BYTES + 1)
        self.assertEqual(self.post(big).status_code, 413)  # by Content-Length
        chunks = [big[i:i + 4096] for i in range(0, len(big), 4096)]
        response = self.client.post(  # no Content-Length: streamed and capped
            "/api/radio/heartbeat",
            content=iter(chunks),
            headers={
                "X-Radio-Timestamp": str(int(time.time())),
                "X-Radio-Signature": "AAAA",
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertIsNone(self.store.listener)

    def test_a_body_of_exactly_16_kib_is_accepted(self):
        padded = self.body + b" " * (radio_service.MAX_HEARTBEAT_BYTES - len(self.body))
        self.assertEqual(len(padded), 16 * 1024)
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post(padded).status_code, 200)

    def test_bad_json_and_bad_schema_are_400_and_nothing_is_echoed(self):
        sneaky = mutated(
            make_heartbeat(), "capture", "listeners", 0, "source_ip", value="198.51.100.9"
        )
        for label, body in (
            ("not json", b"not json"), ("array", b"[]"), ("empty", b""),
            ("unknown key", hb_encode(sneaky)),
            ("bad value", hb_encode(mutated(make_heartbeat(), "transcriber", "pending", value=-1))),
        ):
            with self.subTest(label):
                response = self.post(body)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("198.51.100.9", response.text)
                self.assertNotIn("source_ip", response.text)
        self.assertIsNone(self.store.listener)

    def test_caps_are_enforced_over_http(self):
        # The smallest legal row, so 65 of them still fit inside the 16 KiB
        # body cap and it is the listener cap that refuses them (a full-size
        # row is about 330 bytes, so 64 real rows can exceed the body cap).
        row = {
            "channel": "a", "direction": "rx", "packets_total": 0,
            "packets_interval": 0, "pcmu_packets_total": 0,
            "other_payload_types": {}, "bytes_interval": 0, "sources_seen": 0,
            "segments_closed_total": 0, "last_packet_at": None,
        }
        sixty_four = hb_encode(mutated(make_heartbeat(), "capture", "listeners", value=[row] * 64))
        self.assertLess(len(sixty_four), radio_service.MAX_HEARTBEAT_BYTES)
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post(sixty_four).status_code, 200)
        for label, document in (
            ("65 listeners", mutated(make_heartbeat(), "capture", "listeners", value=[row] * 65)),
            ("17 payload types", mutated(
                make_heartbeat(), "capture", "listeners", 0, "other_payload_types",
                value={str(n): 1 for n in range(17)})),
            ("long glossary_version", make_heartbeat(glossary_version="v" * 33)),
        ):
            with self.subTest(label):
                self.assertEqual(self.post(hb_encode(document)).status_code, 400)

    def test_identity_is_ignored_in_both_directions(self):
        # A signed-in administrator's session does not stand in for a signature.
        self.sign_in_as(ADMIN)
        unsigned = self.client.post(
            "/api/radio/heartbeat", content=self.body,
            headers={"x-amzn-oidc-data": "forged", "x-amzn-oidc-accesstoken": "forged"},
        )
        self.assertEqual(unsigned.status_code, 401)
        self.assertEqual(self.post(signature="AAAA").status_code, 401)
        self.assertIsNone(self.store.listener)
        # A valid signature needs no identity, whatever the headers claim, and
        # works the same whoever the (irrelevant) session belongs to.
        for identity in (None, ADMIN):
            self.sign_in_as(identity)
            with self.subTest(identity=identity), self.assertLogs(
                radio_service.HEARTBEAT_LOGGER_NAME, "INFO"
            ):
                ok = self.post(headers={
                    "x-amzn-oidc-data": "forged",
                    "x-auth-request-email": "forged@evil.test",
                    "cf-access-authenticated-user-email": "forged@evil.test",
                })
            self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.client.get("/api/radio/heartbeat").status_code, 405)

    def test_a_restricted_tier_identity_on_the_request_is_refused_like_ingest(self):
        # The listener never carries a session. If a request does arrive with a
        # restricted-tier identity (avatar kiosk, user), the tier gate refuses
        # it before the route runs, exactly as for /api/radio/ingest.
        self.sign_in_as(_identity("b@911logan.com", "lcdash-pilot-avatar"))
        with self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
            self.assertEqual(self.post().status_code, 403)
        self.sign_in_as(_identity("u@911logan.com", "lcdash-pilot-user"))
        with self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
            self.assertEqual(self.post().status_code, 403)
        self.assertIsNone(self.store.listener)

    def test_it_is_not_disabled_by_synthetic_disconnected_mode(self):
        self.assertEqual(settings.deployment_mode, "synthetic-disconnected")
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post().status_code, 200)

    def test_a_heartbeat_never_touches_segments_or_the_audit_trail(self):
        ingest_record(self.service)
        before = dict(self.store.segments)
        audit_before = list(self.store.audit)
        puts_before = len(self.s3.put_calls)

        def refuse(*_args, **_kwargs):
            raise AssertionError("a heartbeat must not touch segments")

        with patch.multiple(
            FakeRepository,
            segment_exists=refuse, insert_segment=refuse, purge_expired=refuse,
            list_segments=refuse, get_segment=refuse, channel_counts=refuse,
            newest_received_at=refuse, save_correction=refuse,
            clear_correction=refuse, insert_alert=refuse,
        ), self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.segments, before)
        self.assertEqual(self.store.audit, audit_before)  # heartbeats are not audited
        self.assertEqual(len(self.s3.put_calls), puts_before)

    def test_storage_failures_are_503_and_never_crash_or_log_ok(self):
        for label, arrange in (
            ("listener write", lambda: setattr(self.store, "fail_listener_write", True)),
            ("database down", lambda: setattr(self.store, "fail_connect", True)),
            ("glossary read", lambda: setattr(self.store, "fail_hint_read", True)),
        ):
            with self.subTest(label):
                self.store.fail_listener_write = False
                self.store.fail_connect = False
                self.store.fail_hint_read = False
                arrange()
                with self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
                    response = self.post()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("Zanesfield", response.text)
                self.assertIsNone(self.store.listener)
        self.store.fail_listener_write = False
        self.store.fail_connect = False
        self.store.fail_hint_read = False
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO"):
            self.assertEqual(self.post().status_code, 200)

    def test_an_unexpected_error_building_the_glossary_is_503_not_a_crash(self):
        with patch.object(
            FakeRepository, "list_hints", side_effect=ValueError("boom")
        ), self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("boom", response.text)
        self.assertIsNone(self.store.listener)


class HeartbeatLogLineTests(_HeartbeatCase):
    def messages(self, logs):
        return [record.getMessage() for record in logs.records]

    def test_exactly_one_content_free_line_per_success(self):
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO") as logs:
            self.assertEqual(self.post().status_code, 200)
        (line,) = self.messages(logs)
        self.assertRegex(line, LOG_LINE)
        # 3412 packets in the minute across the two channels, 12 undecoded.
        self.assertEqual(
            line, "radio-heartbeat ok listeners=2 packets_interval=3412 undecoded=12"
        )

    def test_the_line_carries_no_channel_names_text_addresses_or_ids(self):
        with self.assertLogs(radio_service.HEARTBEAT_LOGGER_NAME, "INFO") as logs:
            self.post(headers={"X-Forwarded-For": "203.0.113.7"})
        (line,) = self.messages(logs)
        for forbidden in ("law", "fire", "203.0.113.7", "testclient", "127.0.0.1",
                          "Zanesfield", "@", "http"):
            self.assertNotIn(forbidden, line)

    def test_no_line_on_any_refusal(self):
        timestamp = int(time.time())
        refused = (
            ("unsigned", lambda: self.client.post("/api/radio/heartbeat", content=self.body)),
            ("bad signature", lambda: self.post(signature="AAAA")),
            ("stale", lambda: self.post(timestamp=timestamp - 999)),
            ("bad json", lambda: self.post(b"{{")),
            ("bad schema", lambda: self.post(hb_encode(make_heartbeat(schema_version=9)))),
            ("oversize", lambda: self.post(b" " * 20000)),
        )
        for label, send in refused:
            with self.subTest(label), self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
                self.assertGreaterEqual(send().status_code, 400)
        with patch.object(settings, "radio_ingest_public_key", ""):
            with self.assertNoLogs(radio_service.HEARTBEAT_LOGGER_NAME):
                self.assertEqual(self.post().status_code, 503)

    def test_the_line_reaches_stdout_without_any_root_logging_setup(self):
        # The app configures no root logger, so an ordinary INFO record would
        # be dropped; the dedicated heartbeat logger must still emit.
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.service.record_heartbeat(validate_heartbeat(self.body))
        self.assertEqual(
            buffer.getvalue().splitlines(),
            ["radio-heartbeat ok listeners=2 packets_interval=3412 undecoded=12"],
        )

    def test_the_heartbeat_logger_is_configured_once(self):
        radio_service._heartbeat_logger()
        radio_service._heartbeat_logger()
        logger = radio_service.logging.getLogger(radio_service.HEARTBEAT_LOGGER_NAME)
        # (pytest attaches its own capture handlers; count only ours.)
        ours = [h for h in logger.handlers if isinstance(h, radio_service._StdoutHandler)]
        self.assertEqual(len(ours), 1)
        self.assertFalse(logger.propagate)


# ------------------------------------------------------------ hint-word API


class HintServiceTests(unittest.TestCase):
    def test_add_list_remove_and_audit(self):
        service, store, _ = build_service()
        created = service.add_hint({"phrase": "  Zanesfield "}, actor=ADMIN_EMAIL)
        self.assertEqual(
            created,
            {"hint_id": 1, "phrase": "Zanesfield", "created_by": ADMIN_EMAIL,
             "created_at": FIXED_NOW.isoformat()},
        )
        listing = service.list_hints()
        self.assertEqual(listing["max_hints"], 200)
        self.assertEqual([h["phrase"] for h in listing["hints"]], ["Zanesfield"])
        self.assertEqual(
            service.remove_hint("1", actor=ADMIN_EMAIL), {"hint_id": 1, "removed": True}
        )
        self.assertEqual(service.list_hints()["hints"], [])
        self.assertEqual(
            [(e["event_type"], e["actor"], e["detail"]) for e in store.audit],
            [
                ("radio.hint.add", ADMIN_EMAIL, {"hint_id": 1, "phrase": "Zanesfield"}),
                ("radio.hint.remove", ADMIN_EMAIL, {"hint_id": 1, "phrase": "Zanesfield"}),
            ],
        )

    def test_phrase_validation(self):
        service, store, _ = build_service()
        for label, payload in (
            ("missing", {}), ("not a string", {"phrase": 5}), ("null", {"phrase": None}),
            ("empty", {"phrase": ""}), ("blank", {"phrase": "   "}),
            ("one character", {"phrase": "a"}),
            ("61 characters", {"phrase": "a" * 61}),
            ("newline", {"phrase": "two\nlines"}), ("tab", {"phrase": "a\tb"}),
            ("carriage return", {"phrase": "a\rb"}), ("nul", {"phrase": "a\x00b"}),
            ("escape", {"phrase": "a\x1bb"}), ("bidi override", {"phrase": "ab‮cd"}),
            ("extra key", {"phrase": "Zanesfield", "weight": 5}),
            ("array body", ["Zanesfield"]), ("string body", "Zanesfield"),
        ):
            with self.subTest(label):
                with self.assertRaises(RadioQueryError):
                    service.add_hint(payload, actor=ADMIN_EMAIL)
        self.assertEqual(store.hints, [])
        self.assertEqual(store.audit, [])

    def test_length_boundaries_are_inclusive(self):
        service, _, _ = build_service()
        self.assertEqual(service.add_hint({"phrase": "ab"}, actor="a@x")["phrase"], "ab")
        long = service.add_hint({"phrase": "c" * 60}, actor="a@x")
        self.assertEqual(len(long["phrase"]), 60)

    def test_duplicates_are_conflicts_case_insensitively(self):
        service, store, _ = build_service()
        service.add_hint({"phrase": "Zanesfield"}, actor="a@x")
        for phrase in ("Zanesfield", "ZANESFIELD", " zanesfield "):
            with self.subTest(phrase=phrase):
                with self.assertRaises(RadioConflictError):
                    service.add_hint({"phrase": phrase}, actor="a@x")
        self.assertEqual(len(store.hints), 1)
        self.assertEqual(len(store.audit), 1)

    def test_at_most_200_hints(self):
        service, store, _ = build_service()
        for index in range(200):
            service.add_hint({"phrase": f"hint {index:03d}"}, actor="a@x")
        with self.assertRaises(RadioConflictError) as caught:
            service.add_hint({"phrase": "one too many"}, actor="a@x")
        self.assertIn("200", str(caught.exception))
        self.assertEqual(len(store.hints), 200)
        self.assertEqual(len(store.audit), 200)

    def test_remove_unknown_or_malformed_ids_are_not_found(self):
        service, store, _ = build_service()
        for value in ("999", "abc", "0", "-1", "", "1.5", "1; DROP TABLE x"):
            with self.subTest(value=value):
                with self.assertRaises(RadioNotFoundError):
                    service.remove_hint(value, actor="a@x")
        self.assertEqual(store.audit, [])

    def test_writes_fail_closed_when_the_audit_write_fails(self):
        service, store, _ = build_service()
        service.add_hint({"phrase": "Zanesfield"}, actor="a@x")
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.add_hint({"phrase": "Quincy"}, actor="a@x")
        with self.assertRaises(RadioDatabaseError):
            service.remove_hint("1", actor="a@x")
        self.assertEqual([h["phrase"] for h in store.hints], ["Zanesfield"])

    def test_the_actor_is_recorded_on_the_row_and_the_audit_event(self):
        service, store, _ = build_service()
        service.add_hint({"phrase": "Zanesfield"}, actor="who@911logan.com")
        self.assertEqual(store.hints[0]["created_by"], "who@911logan.com")
        self.assertEqual(store.audit[0]["actor"], "who@911logan.com")
        self.assertIsNone(store.audit[0]["segment_id"])


class HintRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        self.use_service(self.service)
        self.sign_in_as(ADMIN)

    def test_add_list_remove_over_http(self):
        created = self.client.post("/api/radio/hints", json={"phrase": "Zanesfield"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.headers["cache-control"], "private, no-store")
        hint = created.json()
        self.assertEqual(hint["phrase"], "Zanesfield")
        self.assertEqual(hint["created_by"], ADMIN_EMAIL)
        listing = self.client.get("/api/radio/hints")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["max_hints"], 200)
        self.assertEqual(len(listing.json()["hints"]), 1)
        removed = self.client.delete(f"/api/radio/hints/{hint['hint_id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json(), {"hint_id": hint["hint_id"], "removed": True})
        self.assertEqual(self.client.get("/api/radio/hints").json()["hints"], [])
        self.assertEqual(
            [e["event_type"] for e in self.store.audit],
            ["radio.hint.add", "radio.hint.remove"],
        )

    def test_error_statuses(self):
        post = lambda body: self.client.post("/api/radio/hints", json=body)
        self.assertEqual(post({"phrase": ""}).status_code, 400)
        self.assertEqual(post({"phrase": "a"}).status_code, 400)
        self.assertEqual(post({"phrase": "x" * 61}).status_code, 400)
        self.assertEqual(post({"phrase": "two\nlines"}).status_code, 400)
        self.assertEqual(post({"phrase": "ok", "extra": 1}).status_code, 400)
        self.assertEqual(post({"phrase": "Zanesfield"}).status_code, 201)
        self.assertEqual(post({"phrase": "zanesfield"}).status_code, 409)
        self.assertEqual(self.client.delete("/api/radio/hints/999").status_code, 404)
        self.assertEqual(self.client.delete("/api/radio/hints/abc").status_code, 404)
        self.assertEqual(
            self.client.post("/api/radio/hints", content=b"{",
                             headers={"content-type": "application/json"}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/radio/hints", data={"phrase": "x"}).status_code, 415)
        self.assertEqual(
            self.client.post(
                "/api/radio/hints",
                content=b'{"phrase":"' + b"x" * (130 * 1024) + b'"}',
                headers={"content-type": "application/json"},
            ).status_code, 413)
        self.assertEqual(len(self.store.hints), 1)

    def test_a_cross_site_browser_write_is_refused(self):
        headers = {"sec-fetch-site": "cross-site"}
        self.assertEqual(
            self.client.post("/api/radio/hints", json={"phrase": "Zanesfield"},
                             headers=headers).status_code, 403)
        self.assertEqual(self.client.delete("/api/radio/hints/1", headers=headers).status_code, 403)
        self.assertEqual(self.store.hints, [])

    def test_a_failed_audit_write_is_503_and_nothing_changes(self):
        self.store.fail_audit = True
        response = self.client.post("/api/radio/hints", json={"phrase": "Zanesfield"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.hints, [])
        self.assertNotIn("Zanesfield", response.text)

    def test_only_admins_reach_the_hint_routes_and_the_service_is_never_called(self):
        routes = (
            ("GET", "/api/radio/hints", None),
            ("POST", "/api/radio/hints", {"phrase": "Zanesfield"}),
            ("DELETE", "/api/radio/hints/1", None),
        )
        mock_service = MagicMock()
        self.use_service(mock_service)
        callers = list(DENIED_CALLERS) + list(NON_ADMIN_HUMANS)
        garbage = {"content": b"{{ not json", "headers": {"content-type": "text/plain"}}
        for role, identity in callers:
            self.sign_in_as(identity)
            for method, path, body in routes:
                with self.subTest(role=role, method=method):
                    good = self.client.request(method, path, **({"json": body} if body else {}))
                    bad = self.client.request(method, path, **garbage)
                    self.assertEqual(good.status_code, 403)
                    self.assertEqual(bad.status_code, 403)
                    self.assertEqual(good.json(), bad.json())
        self.assertEqual(mock_service.mock_calls, [])

    def test_a_new_hint_reaches_the_next_heartbeat_glossary(self):
        self.client.post("/api/radio/hints", json={"phrase": "Zanesfield"})
        reply = self.service.record_heartbeat(
            validate_heartbeat(hb_encode(make_heartbeat()))
        )
        self.assertEqual(reply["glossary"]["hotwords"], "Zanesfield")
        self.client.delete("/api/radio/hints/1")
        reply = self.service.record_heartbeat(
            validate_heartbeat(hb_encode(make_heartbeat()))
        )
        self.assertEqual(reply["glossary"]["version"], "empty")


# ----------------------------------------------------- stats: listener block


class ListenerStatsTests(unittest.TestCase):
    def service_with_heartbeat(self, *, age_seconds):
        service, store, _ = build_service()
        stored = validate_heartbeat(hb_encode(make_heartbeat()))
        store.listener = (stored, FIXED_NOW - timedelta(seconds=age_seconds))
        return service

    def test_null_when_no_heartbeat_has_ever_arrived(self):
        service, _, _ = build_service()
        self.assertIsNone(service.stats()["listener"])

    def test_reporting_while_the_last_heartbeat_is_at_most_180_seconds_old(self):
        for age in (0, 59, 179, 180):
            with self.subTest(age=age):
                listener = self.service_with_heartbeat(age_seconds=age).stats()["listener"]
                self.assertTrue(listener["reporting"])
                self.assertEqual(listener["age_seconds"], age)

    def test_not_reporting_after_180_seconds(self):
        for age in (181, 600, 86400 * 3):
            with self.subTest(age=age):
                listener = self.service_with_heartbeat(age_seconds=age).stats()["listener"]
                self.assertFalse(listener["reporting"])
                self.assertEqual(listener["age_seconds"], age)

    def test_a_heartbeat_dated_in_the_future_is_age_zero_not_negative(self):
        service, store, _ = build_service()
        store.listener = (
            validate_heartbeat(hb_encode(make_heartbeat())),
            FIXED_NOW + timedelta(seconds=30),
        )
        listener = service.stats()["listener"]
        self.assertEqual(listener["age_seconds"], 0)
        self.assertTrue(listener["reporting"])

    def test_the_shape(self):
        listener = self.service_with_heartbeat(age_seconds=45).stats()["listener"]
        self.assertEqual(
            set(listener),
            {"reporting", "last_heartbeat_at", "age_seconds", "capture_uptime_seconds",
             "capture_stats_at", "listeners", "transcriber", "uploader"},
        )
        self.assertEqual(
            listener["last_heartbeat_at"],
            (FIXED_NOW - timedelta(seconds=45)).isoformat(),
        )
        self.assertEqual(listener["capture_uptime_seconds"], 25200)
        self.assertEqual(listener["capture_stats_at"], "2026-10-16T14:59:45+00:00")
        self.assertEqual(listener["transcriber"], {"pending": 1, "failed": 0})
        self.assertEqual(
            listener["uploader"],
            {"queued": 2, "oldest_queued_age_seconds": 14, "rejected_total": 0,
             "last_success_at": "2026-10-16T14:59:30+00:00",
             "head_failing_since": None, "head_attempts": 0, "parked_total": 0},
        )
        self.assertEqual(
            listener["listeners"],
            [
                {"channel": "law", "direction": "rx", "packets_interval": 3412,
                 "interval_seconds": 60, "other_payload_types": {"103": 12},
                 "last_packet_at": "2026-10-16T14:59:58+00:00"},
                {"channel": "fire", "direction": "rx", "packets_interval": 0,
                 "interval_seconds": 60, "other_payload_types": {},
                 "last_packet_at": None},
            ],
        )

    def test_no_hostnames_addresses_or_text_are_exposed(self):
        listener = self.service_with_heartbeat(age_seconds=1).stats()["listener"]
        text = json.dumps(listener)
        for forbidden in ("hostname", "source", "address", "ip", "transcript", "text"):
            self.assertNotRegex(text, rf'"{forbidden}[a-z_]*"')

    def test_a_malformed_stored_payload_degrades_instead_of_failing_stats(self):
        service, store, _ = build_service()
        store.listener = ({"capture": "oops", "transcriber": None}, FIXED_NOW)
        listener = service.stats()["listener"]
        self.assertTrue(listener["reporting"])
        self.assertEqual(listener["listeners"], [])
        self.assertEqual(listener["transcriber"], {})

    def test_a_heartbeat_then_stats_round_trip(self):
        service, store, _ = build_service()
        service.record_heartbeat(validate_heartbeat(hb_encode(make_heartbeat())))
        listener = service.stats()["listener"]
        self.assertTrue(listener["reporting"])
        self.assertEqual(listener["age_seconds"], 0)
        self.assertEqual(listener["listeners"][0]["channel"], "law")


class ListenerStatsRouteTests(_RouteCase):
    def test_stats_over_http_carries_the_block_for_every_human_role(self):
        service, store, _ = build_service()
        store.listener = (
            validate_heartbeat(hb_encode(make_heartbeat())),
            FIXED_NOW - timedelta(seconds=10),
        )
        self.use_service(service)
        for role, identity in HUMAN_ROLES:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                response = self.client.get("/api/radio/stats")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                listener = response.json()["listener"]
                self.assertTrue(listener["reporting"])
                self.assertEqual(listener["age_seconds"], 10)

    def test_null_over_http_when_the_listener_has_never_reported(self):
        service, _, _ = build_service()
        self.use_service(service)
        self.sign_in_as(ADMIN)
        self.assertIsNone(self.client.get("/api/radio/stats").json()["listener"])

    def test_the_avatar_and_unidentified_callers_still_get_403(self):
        service, _, _ = build_service()
        self.use_service(service)
        for role, identity in DENIED_CALLERS:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/api/radio/stats").status_code, 403)


# ----------------------------------------------------- repository SQL contract


class RepositorySqlTests(unittest.TestCase):
    def test_hint_statements_are_bound_and_case_insensitively_unique(self):
        connection = ScriptedConnection(results=[[(1, "Zanesfield", "a@x", FIXED_NOW)]])
        row = repository_with(connection).insert_hint("Zanesfield'; DROP TABLE x; --", "a@x")
        query, params = connection.calls[0]
        self.assertIn("INSERT INTO lcdash_radio.hints", query)
        self.assertIn("ON CONFLICT ((lower(phrase))) DO NOTHING", query)
        self.assertIn("RETURNING hint_id, phrase, created_by, created_at", query)
        self.assertNotIn("DROP", query)
        self.assertEqual(params, ("Zanesfield'; DROP TABLE x; --", "a@x"))
        self.assertEqual(row[0], 1)
        # No commit here: the service's audit write commits both together.
        self.assertEqual(connection.commits, 0)

    def test_delete_list_and_count_hints(self):
        connection = ScriptedConnection(results=[[(4, "Quincy", "a@x", FIXED_NOW)], [], [(7,)]])
        repository = repository_with(connection)
        self.assertEqual(repository.delete_hint(4)[1], "Quincy")
        self.assertIn("DELETE FROM lcdash_radio.hints WHERE hint_id = %s", connection.calls[0][0])
        self.assertEqual(connection.calls[0][1], (4,))
        self.assertEqual(repository.list_hints(), [])
        self.assertIn("ORDER BY hint_id", connection.calls[1][0])
        self.assertEqual(repository.count_hints(), 7)
        self.assertEqual(connection.commits, 0)

    def test_upsert_listener_status_is_one_idempotent_row_and_commits(self):
        connection = ScriptedConnection()
        repository_with(connection).upsert_listener_status('{"a":1}', FIXED_NOW)
        query, params = connection.calls[0]
        self.assertIn("INSERT INTO lcdash_radio.listener_status (id, payload, received_at)", query)
        self.assertIn("VALUES (1, %s::JSONB, %s)", query)
        self.assertIn("ON CONFLICT (id) DO UPDATE SET", query)
        self.assertEqual(params, ('{"a":1}', FIXED_NOW))
        self.assertEqual(connection.commits, 1)
        self.assertNotIn("segments", query)

    def test_get_listener_status(self):
        payload = {"capture": {}}
        connection = ScriptedConnection(results=[[(payload, FIXED_NOW)], [({"x": 1}.__repr__().replace("'", '"'), FIXED_NOW)], []])
        repository = repository_with(connection)
        self.assertEqual(repository.get_listener_status(), (payload, FIXED_NOW))
        self.assertIn("WHERE id = 1", connection.calls[0][0])
        # A driver that hands back JSON text is decoded too.
        self.assertEqual(repository.get_listener_status(), ({"x": 1}, FIXED_NOW))
        self.assertIsNone(repository.get_listener_status())


# ----------------------------------------------------------- schema contract


def _inline_constraint_events() -> set[str]:
    start = SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS lcdash_radio.audit_events")
    end = SCHEMA_SQL.index("CREATE INDEX IF NOT EXISTS radio_audit_created_idx")
    return set(re.findall(r"'(radio\.[a-z_.]+)'", SCHEMA_SQL[start:end]))


def _do_block() -> str:
    return SCHEMA_SQL[SCHEMA_SQL.index("DO $$"):]


def _guard_would_widen(existing_definition: str) -> bool:
    """Evaluate the DO block's guard against a constraint definition.

    The block widens only while ``position(<newest event> IN definition) = 0``.
    """
    match = re.search(r"position\('([a-z_.]+)' IN pg_get_constraintdef\(oid\)\) = 0", _do_block())
    assert match, "the DO block must be guarded on the newest event"
    return match.group(1) not in existing_definition


V1_DEFINITION = (
    "CHECK ((event_type = ANY (ARRAY['radio.segments.list'::text, "
    "'radio.segment.listen'::text])))"
)
V2_DEFINITION = (
    "CHECK ((event_type = ANY (ARRAY['radio.segments.list'::text, "
    "'radio.segment.listen'::text, 'radio.segment.correct'::text, "
    "'radio.segment.correct_revert'::text, 'radio.fix.add'::text, "
    "'radio.fix.remove'::text, 'radio.watch.add'::text, "
    "'radio.watch.change'::text, 'radio.watch.remove'::text, "
    "'radio.alert.dismiss'::text])))"
)


V3_DEFINITION = (
    "CHECK ((event_type = ANY (ARRAY['radio.segments.list'::text, "
    "'radio.segment.listen'::text, 'radio.segment.correct'::text, "
    "'radio.segment.correct_revert'::text, 'radio.fix.add'::text, "
    "'radio.fix.remove'::text, 'radio.watch.add'::text, "
    "'radio.watch.change'::text, 'radio.watch.remove'::text, "
    "'radio.alert.dismiss'::text, 'radio.hint.add'::text, "
    "'radio.hint.remove'::text])))"
)


class SchemaV3Tests(unittest.TestCase):
    ALL_EVENTS = {
        radio_service.EVENT_LIST, radio_service.EVENT_LISTEN, radio_service.EVENT_CORRECT,
        radio_service.EVENT_CORRECT_REVERT, radio_service.EVENT_FIX_ADD,
        radio_service.EVENT_FIX_REMOVE, radio_service.EVENT_WATCH_ADD,
        radio_service.EVENT_WATCH_CHANGE, radio_service.EVENT_WATCH_REMOVE,
        radio_service.EVENT_ALERT_DISMISS, radio_service.EVENT_HINT_ADD,
        radio_service.EVENT_HINT_REMOVE, radio_service.EVENT_UNIT_SET,
        radio_service.EVENT_UNIT_REMOVE,
    }

    def test_the_advisory_lock_is_still_the_first_statement(self):
        statements = [
            line for line in SCHEMA_SQL.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        self.assertEqual(statements[0], "SELECT pg_advisory_xact_lock(727452301);")
        self.assertEqual(statements[1], "CREATE SCHEMA IF NOT EXISTS lcdash_radio;")

    def test_listener_status_is_a_guarded_one_row_table(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS lcdash_radio.listener_status (", SCHEMA_SQL)
        block = SCHEMA_SQL[SCHEMA_SQL.index("lcdash_radio.listener_status ("):]
        block = block[:block.index(");")]
        self.assertIn("id SMALLINT PRIMARY KEY CHECK (id = 1)", block)
        self.assertIn("payload JSONB NOT NULL", block)
        self.assertIn("received_at TIMESTAMPTZ NOT NULL", block)
        # Nothing about who sent it or what was said gets its own column.
        columns = re.findall(r"^\s+([a-z_]+) [A-Z]", block, re.MULTILINE)
        self.assertEqual(columns, ["id", "payload", "received_at"])

    def test_hints_table_and_case_insensitive_unique_index(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS lcdash_radio.hints (", SCHEMA_SQL)
        block = SCHEMA_SQL[SCHEMA_SQL.index("lcdash_radio.hints ("):]
        block = block[:block.index(");")]
        for column in ("hint_id BIGSERIAL PRIMARY KEY", "phrase TEXT NOT NULL",
                       "created_by TEXT NOT NULL",
                       "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"):
            self.assertIn(column, block)
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS radio_hints_phrase_idx\n"
            "    ON lcdash_radio.hints (lower(phrase));",
            SCHEMA_SQL,
        )

    def test_every_new_object_is_idempotent_and_nothing_destructive_was_added(self):
        for line in SCHEMA_SQL.splitlines():
            if line.startswith(("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX",
                                "CREATE UNIQUE INDEX")):
                self.assertIn("IF NOT EXISTS", line, line)
        upper = SCHEMA_SQL.upper()
        self.assertEqual(upper.count("DROP "), 1)
        self.assertIn("DROP CONSTRAINT RADIO_AUDIT_EVENT_TYPE_CHECK", upper)
        for forbidden in ("DROP TABLE", "DROP SCHEMA", "DROP COLUMN", "DROP INDEX",
                          "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, upper)
        # No cascading foreign key was added (corrections, alerts x2 only).
        self.assertEqual(SCHEMA_SQL.count("ON DELETE CASCADE"), 3)

    def test_the_inline_constraint_lists_every_event(self):
        self.assertEqual(_inline_constraint_events(), self.ALL_EVENTS)

    def test_the_widening_block_lists_exactly_the_same_events(self):
        block = _do_block()
        widened = set(re.findall(r"'(radio\.[a-z_.]+)'", block[block.index("ADD CONSTRAINT"):]))
        self.assertEqual(widened, self.ALL_EVENTS)
        self.assertEqual(widened, _inline_constraint_events())

    def test_the_widening_block_is_guarded_and_names_only_the_audit_constraint(self):
        block = _do_block()
        self.assertEqual(block.count("DROP CONSTRAINT"), 1)
        self.assertEqual(block.count("ADD CONSTRAINT"), 1)
        self.assertIn("conname = 'radio_audit_event_type_check'", block)
        self.assertIn("conrelid = 'lcdash_radio.audit_events'::regclass", block)
        # The guard looks for the newest event (v4 unit names).
        self.assertIn("radio.unit.remove", block.split("THEN")[0])

    def test_upgrade_from_the_current_schema_widens_once_and_only_when_needed(self):
        # v1 (two events), v2 and the v3 constraint (hint events, no unit
        # events) all lack the newest event, so the guard fires and the block
        # installs the full list.
        self.assertTrue(_guard_would_widen(V1_DEFINITION))
        self.assertTrue(_guard_would_widen(V2_DEFINITION))
        self.assertTrue(_guard_would_widen(V3_DEFINITION))
        # A database that already has the full list (fresh, or after one
        # upgrade) is left alone, so re-applying the file is a no-op.
        full = "CHECK ((event_type = ANY (ARRAY[" + ", ".join(
            f"'{event}'::text" for event in sorted(self.ALL_EVENTS)
        ) + "])))"
        self.assertFalse(_guard_would_widen(full))

    def test_the_v2_events_are_still_accepted_after_the_upgrade(self):
        v2_events = set(re.findall(r"'(radio\.[a-z_.]+)'", V2_DEFINITION))
        self.assertTrue(v2_events <= _inline_constraint_events())
        self.assertEqual(
            _inline_constraint_events() - v2_events,
            {"radio.hint.add", "radio.hint.remove",
             "radio.unit.set", "radio.unit.remove"},
        )

    def test_the_v3_events_are_still_accepted_after_the_upgrade(self):
        v3_events = set(re.findall(r"'(radio\.[a-z_.]+)'", V3_DEFINITION))
        self.assertTrue(v3_events <= _inline_constraint_events())
        self.assertEqual(
            _inline_constraint_events() - v3_events,
            {"radio.unit.set", "radio.unit.remove"},
        )

    def test_the_service_applies_the_whole_file_once_per_process(self):
        self.assertEqual(
            radio_service.SCHEMA_PATH.read_text(encoding="utf-8"), SCHEMA_SQL
        )
        service, store, _ = build_service()
        service.add_hint({"phrase": "Zanesfield"}, actor="a@x")
        service.record_heartbeat(validate_heartbeat(hb_encode(make_heartbeat())))
        service.stats()
        self.assertEqual(store.schema_calls, 1)


# ------------------------------------------------------------ tier closure


class TierClosureTests(unittest.TestCase):
    def test_the_tier_helper_never_admits_the_new_paths(self):
        for path in (
            "/api/radio/heartbeat", "/api/radio/heartbeat/", "/api/radio/heartbeat?x=1",
            "/api/radio/hints", "/api/radio/hints/", "/api/radio/hints/1",
            "/api/radio/hints/1/other", "/api/radio/hints/abc",
        ):
            with self.subTest(path=path):
                self.assertFalse(radio_access.is_radio_human_path(path))
                self.assertFalse(radio_access.is_radio_page(path))

    def test_no_module_level_tier_list_names_the_new_paths(self):
        from app.core import avatar_tier, dispatcher_tier, fire_officer_tier, sanitized_tier

        lists = []
        for module in (avatar_tier, dispatcher_tier, fire_officer_tier, sanitized_tier):
            for name in dir(module):
                value = getattr(module, name)
                if name.startswith("_") and isinstance(value, (tuple, frozenset, set, list)):
                    lists.extend(str(item) for item in value)
        self.assertFalse(
            [item for item in lists if "/api/radio/hints" in item or "/api/radio/heartbeat" in item]
        )


if __name__ == "__main__":
    unittest.main()
