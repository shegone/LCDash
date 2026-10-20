"""Mindshare radio v4: who is talking (radio ID) and admin-managed unit names.

Contract under test: docs/MINDSHARE_RADIO_UNITS.md ("Server") and the optional
``signal`` field of docs/MINDSHARE_RADIO_INGEST.md. Offline and synthetic, on
the in-memory fakes from test_radio_routes.py (a repository that reproduces the
real one's transaction semantics, so "no audit record, no change" is provable
for every write). Radio IDs, talkgroups, console labels and unit names here are
invented.

What is proved:

* ingest accepts an absent or valid ``signal`` and refuses anything else with
  a 400 that never echoes the sender's text; the three values are stored;
* the schema file adds the columns, the index, the ``units`` table and the two
  audit events idempotently, with the advisory lock still first;
* the units API (list, name, rename, clear) is admin only with a uniform 403
  before any validation, audited, and fails closed;
* list items carry radio_id / talkgroup / console_label / unit_name; the
  ``radio_id`` filter is exact; ``q`` finds a radio ID prefix and a unit name,
  with bound parameters only; the 60 second name cache is dropped on change;
* a possible-noise segment still carries its radio_id.
"""

from __future__ import annotations

import re
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core import radio_access
from app.services import radio_service
from app.services.radio_service import (
    RadioConflictError,
    RadioDatabaseError,
    RadioIngestError,
    RadioNotFoundError,
    RadioQueryError,
    escape_like_pattern,
    validate_metadata,
)

from .test_radio_routes import (
    ADMIN,
    ADMIN_EMAIL,
    DENIED_CALLERS,
    FIXED_NOW,
    HUMAN_ROLES,
    NON_ADMIN_HUMANS,
    SEGMENT_ID,
    FakeConnection,
    _multipart,
    _RouteCase,
    build_service,
    encode,
    ingest_record,
    make_metadata,
    make_wav,
    repository_with,
    sign,
)

SCHEMA_SQL = (
    Path(radio_service.__file__).resolve().parents[2] / "database" / "radio_schema.sql"
).read_text(encoding="utf-8")

ACTOR = ADMIN_EMAIL
FULL_SIGNAL = {"radio_id": "98426", "talkgroup": "G4407", "console_label": "Position2"}


def signal_metadata(signal, **overrides):
    return make_metadata(signal=signal, **overrides)


def listed(service, **filters):
    return {
        item["segment_id"]: item
        for item in service.list_segments(actor=ACTOR, **filters)["segments"]
    }


def add_heard(service, segment_id, radio_id, *, minute=0, **overrides):
    """Ingest one synthetic segment heard from ``radio_id``."""
    started = f"2026-10-16T14:{minute:02d}:00+00:00"
    ended = f"2026-10-16T14:{minute:02d}:05+00:00"
    signal = overrides.pop("signal", {"radio_id": radio_id} if radio_id else None)
    document = make_metadata(
        segment_id=segment_id, started_at=started, ended_at=ended, **overrides
    )
    if signal is not None:
        document["signal"] = signal
    record = validate_metadata(encode(document))
    assert service.ingest(record, make_wav()) == "created"
    return record


# ------------------------------------------------------------ ingest: signal


class SignalValidationTests(unittest.TestCase):
    def test_a_segment_without_signal_has_no_radio_fields(self):
        record = validate_metadata(encode(make_metadata()))
        self.assertIsNone(record.radio_id)
        self.assertIsNone(record.talkgroup)
        self.assertIsNone(record.console_label)

    def test_a_full_signal_is_carried_on_the_record(self):
        record = validate_metadata(encode(signal_metadata(FULL_SIGNAL)))
        self.assertEqual(record.radio_id, "98426")
        self.assertEqual(record.talkgroup, "G4407")
        self.assertEqual(record.console_label, "Position2")

    def test_each_key_is_optional_on_its_own(self):
        for signal, expected in (
            ({"radio_id": "440593"}, ("440593", None, None)),
            ({"talkgroup": "G4405"}, (None, "G4405", None)),
            ({"console_label": "maxplus"}, (None, None, "maxplus")),
            ({"radio_id": "98426", "talkgroup": "G4407"}, ("98426", "G4407", None)),
        ):
            with self.subTest(signal=signal):
                record = validate_metadata(encode(signal_metadata(signal)))
                self.assertEqual(
                    (record.radio_id, record.talkgroup, record.console_label), expected
                )

    def test_an_empty_signal_object_means_nothing_was_found(self):
        record = validate_metadata(encode(signal_metadata({})))
        self.assertEqual((record.radio_id, record.talkgroup, record.console_label),
                         (None, None, None))

    def test_the_pattern_boundaries_are_accepted(self):
        record = validate_metadata(encode(signal_metadata({
            "radio_id": "1234567890", "talkgroup": "G123456",
            "console_label": "A" + "b" * 31,
        })))
        self.assertEqual(record.radio_id, "1234567890")
        self.assertEqual(record.talkgroup, "G123456")
        self.assertEqual(len(record.console_label), 32)

    def test_unknown_keys_non_objects_and_bad_values_are_400(self):
        bad = {
            "unknown key": {"radio_id": "98426", "unit": "Medic 4"},
            "only an unknown key": {"callsign": "x"},
            "unknown key with empty known": {"srcaddr": "10.0.0.1"},
            "null": None,
            "list": [],
            "string": "98426",
            "number": 98426,
            "radio_id int": {"radio_id": 98426},
            "radio_id null": {"radio_id": None},
            "radio_id empty": {"radio_id": ""},
            "radio_id letters": {"radio_id": "98A26"},
            "radio_id 11 digits": {"radio_id": "12345678901"},
            "radio_id trailing newline": {"radio_id": "98426\n"},
            "radio_id leading space": {"radio_id": " 98426"},
            "radio_id arabic digits": {"radio_id": "\u0669\u0668\u0664\u0662\u0666"},
            "radio_id wildcard": {"radio_id": "98%"},
            "talkgroup lowercase": {"talkgroup": "g4407"},
            "talkgroup no prefix": {"talkgroup": "4407"},
            "talkgroup 7 digits": {"talkgroup": "G1234567"},
            "talkgroup bare G": {"talkgroup": "G"},
            "talkgroup newline": {"talkgroup": "G4407\n"},
            "label leading space": {"console_label": " Position2"},
            "label 33 chars": {"console_label": "A" * 33},
            "label punctuation": {"console_label": "a;b"},
            "label quote": {"console_label": "a'b"},
            "label non-ascii": {"console_label": "Posici\u00f3n2"},
            "label control": {"console_label": "Pos\u0000tion"},
            "label newline": {"console_label": "Position2\n"},
            "label empty": {"console_label": ""},
            "label list": {"console_label": ["Position2"]},
        }
        for label, signal in bad.items():
            with self.subTest(label):
                with self.assertRaises(RadioIngestError) as caught:
                    validate_metadata(encode(signal_metadata(signal)))
                self.assertEqual(caught.exception.status_code, 400)
                # Only the server's own field name; nothing the sender wrote.
                self.assertEqual(
                    caught.exception.detail, "Metadata field 'signal' is invalid."
                )

    def test_signal_does_not_change_the_other_rules(self):
        # schema_version stays 1 and the other fields stay required/strict.
        with self.assertRaises(RadioIngestError):
            validate_metadata(encode(signal_metadata(FULL_SIGNAL, schema_version=2)))
        with self.assertRaises(RadioIngestError):
            validate_metadata(encode(signal_metadata(FULL_SIGNAL, audio_file="C:/x")))
        document = signal_metadata(FULL_SIGNAL)
        del document["segments"]
        with self.assertRaises(RadioIngestError):
            validate_metadata(encode(document))

    def test_signal_is_the_only_optional_metadata_field(self):
        optional = radio_service._METADATA_FIELDS - radio_service._METADATA_REQUIRED
        self.assertEqual(optional, {"signal"})


class SignalStorageTests(unittest.TestCase):
    def test_ingest_stores_the_signal_on_the_row(self):
        service, store, _ = build_service()
        ingest_record(service, signal=FULL_SIGNAL)
        row = store.segments[SEGMENT_ID]
        self.assertEqual(row[16:19], ("98426", "G4407", "Position2"))

    def test_ingest_without_signal_stores_nulls(self):
        service, store, _ = build_service()
        ingest_record(service)
        self.assertEqual(store.segments[SEGMENT_ID][16:19], (None, None, None))

    def test_the_insert_binds_the_signal_values_and_names_the_columns(self):
        record = validate_metadata(encode(signal_metadata(FULL_SIGNAL)))
        connection = FakeConnection(rowcount=1)
        repository_with(connection).insert_segment(record, "k")
        query, params = connection.calls[-1]
        self.assertIn("radio_id, talkgroup, console_label", query)
        self.assertEqual(query.count("%s"), len(params))
        self.assertEqual(params[-3:], ("98426", "G4407", "Position2"))
        self.assertNotIn("98426", query)

    def test_the_select_contract_ends_with_the_signal_columns(self):
        columns = [c.strip() for c in radio_service.RadioRepository._SEGMENT_COLUMNS.split(",")]
        self.assertEqual(len(columns), 19)
        self.assertEqual(columns[16:], ["radio_id", "talkgroup", "console_label"])


class SignalIngestRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, self.s3 = build_service()
        self.use_service(self.service)
        self.sign_in_as(None)

    def post(self, metadata):
        timestamp = int(time.time())
        segment_id = "law_rx_20261016_140500_0001"
        headers = {
            "X-Radio-Timestamp": str(timestamp),
            "X-Radio-Signature": sign(timestamp, segment_id, make_wav(), metadata),
        }
        return self.client.post(
            "/api/radio/ingest", headers=headers, **_multipart(metadata, make_wav())
        )

    def test_a_signed_segment_with_a_signal_is_stored_with_its_radio_id(self):
        response = self.post(encode(signal_metadata(FULL_SIGNAL)))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.store.segments[SEGMENT_ID][16:19],
                         ("98426", "G4407", "Position2"))

    def test_a_signed_segment_without_a_signal_still_works(self):
        response = self.post(encode(make_metadata()))
        self.assertEqual(response.status_code, 201, response.text)

    def test_a_bad_signal_is_a_400_and_nothing_is_stored(self):
        for signal in ({"radio_id": "x"}, {"callsign": "Medic 4"}, "98426", None):
            with self.subTest(signal=signal):
                response = self.post(encode(signal_metadata(signal)))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["detail"], "Metadata field 'signal' is invalid."
                )
                self.assertNotIn("Medic", response.text)
        self.assertEqual(self.store.segments, {})
        self.assertEqual(self.s3.put_calls, [])


# ------------------------------------------------------------ schema contract


class SchemaV4Tests(unittest.TestCase):
    def test_the_advisory_lock_is_still_first(self):
        statements = [
            line for line in SCHEMA_SQL.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        self.assertEqual(statements[0], "SELECT pg_advisory_xact_lock(727452301);")
        self.assertEqual(statements[1], "CREATE SCHEMA IF NOT EXISTS lcdash_radio;")

    def test_the_three_signal_columns_are_added_idempotently(self):
        for column in ("radio_id TEXT", "talkgroup TEXT", "console_label TEXT"):
            self.assertIn(
                f"ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS {column};",
                SCHEMA_SQL,
            )

    def test_the_radio_id_index_is_partial_and_newest_first(self):
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS radio_segments_radio_id_idx\n"
            "    ON lcdash_radio.segments (radio_id, started_at DESC)\n"
            "    WHERE radio_id IS NOT NULL;",
            SCHEMA_SQL,
        )

    def test_the_units_table_has_the_specified_columns_and_checks(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS lcdash_radio.units (", SCHEMA_SQL)
        block = SCHEMA_SQL[SCHEMA_SQL.index("lcdash_radio.units ("):]
        block = block[:block.index(");")]
        self.assertIn(
            "radio_id TEXT PRIMARY KEY CHECK (radio_id ~ '^[0-9]{1,10}$')", block
        )
        self.assertIn("name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60)", block)
        self.assertIn("updated_by TEXT NOT NULL", block)
        self.assertIn("updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()", block)
        columns = re.findall(r"^\s+([a-z_]+) [A-Z]", block, re.MULTILINE)
        self.assertEqual(columns, ["radio_id", "name", "updated_by", "updated_at"])

    def test_nothing_destructive_was_added_and_there_is_still_one_drop(self):
        for line in SCHEMA_SQL.splitlines():
            if line.startswith(("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX",
                                "CREATE UNIQUE INDEX")):
                self.assertIn("IF NOT EXISTS", line, line)
        upper = SCHEMA_SQL.upper()
        self.assertEqual(upper.count("DROP "), 1)
        # Two DO blocks: the audit-constraint widening and the v5 one-time relabel
        # (an UPDATE of channel names, guarded by a marker row). Neither deletes.
        self.assertEqual(SCHEMA_SQL.count("DO $$"), 2)
        for forbidden in ("DROP TABLE", "DROP SCHEMA", "DROP COLUMN", "DROP INDEX",
                          "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, upper)
        # The units table has no foreign key to segments: removing a name never
        # touches a transcript, and a purge never touches a name.
        block = SCHEMA_SQL[SCHEMA_SQL.index("lcdash_radio.units ("):]
        self.assertNotIn("REFERENCES", block[:block.index(");")])

    def test_the_audit_constraint_lists_both_new_events_inline_and_in_the_block(self):
        # Inline (fresh databases) and in the widening block; the newest event
        # is also the one the guard looks for.
        self.assertEqual(SCHEMA_SQL.count("'radio.unit.set'"), 2)
        self.assertEqual(SCHEMA_SQL.count("'radio.unit.remove'"), 3)
        self.assertEqual(radio_service.EVENT_UNIT_SET, "radio.unit.set")
        self.assertEqual(radio_service.EVENT_UNIT_REMOVE, "radio.unit.remove")

    def test_the_widening_guard_looks_for_the_newest_event(self):
        block = SCHEMA_SQL[SCHEMA_SQL.index("DO $$"):]
        self.assertIn(
            "position('radio.unit.remove' IN pg_get_constraintdef(oid)) = 0", block
        )
        self.assertNotIn("position('radio.hint.remove'", block)

    def test_the_do_block_is_still_the_last_statement(self):
        tail = SCHEMA_SQL[SCHEMA_SQL.index("DO $$"):]
        self.assertTrue(tail.rstrip().endswith("$$;"))
        self.assertNotIn("CREATE ", tail)

    def test_the_service_applies_the_file_once_per_process(self):
        service, store, _ = build_service()
        service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        service.list_units()
        service.list_segments(actor=ACTOR)
        self.assertEqual(store.schema_calls, 1)


# --------------------------------------------------------------- repository SQL


class RepositorySqlTests(unittest.TestCase):
    def list_query(self, **kwargs):
        connection = FakeConnection()
        base = dict(channel=None, direction=None, like_pattern=None, cursor=None, limit=51)
        base.update(kwargs)
        repository_with(connection).list_segments(**base)
        return connection.calls[-1]

    def test_the_radio_id_filter_is_an_exact_bound_parameter(self):
        query, params = self.list_query(radio_id="98426")
        self.assertIn("WHERE radio_id = %s ", query)
        self.assertNotIn("98426", query)
        self.assertEqual(params, ("98426", 51))

    def test_search_is_one_or_group_of_bound_parameters(self):
        hostile = "x'; DROP TABLE lcdash_radio.units; --"
        query, params = self.list_query(
            like_pattern=f"%{escape_like_pattern(hostile)}%",
            id_prefix="98%",
            unit_radio_ids=["98426", "98427"],
            channel="ems",
            radio_id="98426",
        )
        self.assertIn(
            "(COALESCE(corrected_text, transcript) ILIKE %s ESCAPE '!' "
            "OR radio_id LIKE %s ESCAPE '!' OR radio_id = ANY(%s::TEXT[]))",
            query,
        )
        self.assertNotIn("DROP", query)
        self.assertNotIn("98427", query)
        self.assertEqual(query.count("%s"), len(params))
        self.assertEqual(params[0], "ems")
        self.assertEqual(params[1], "98426")
        self.assertIn("DROP TABLE", params[2])
        self.assertEqual(params[3], "98%")
        self.assertEqual(params[4], ["98426", "98427"])
        self.assertEqual(params[-1], 51)

    def test_without_the_new_arguments_the_text_search_is_unchanged(self):
        query, params = self.list_query(like_pattern="%plate%")
        self.assertIn("WHERE COALESCE(corrected_text, transcript) ILIKE %s ESCAPE '!' ", query)
        self.assertNotIn(" OR ", query)
        self.assertEqual(params, ("%plate%", 51))

    def test_unit_statements_bind_every_value(self):
        connection = FakeConnection()
        repository = repository_with(connection)
        repository.upsert_unit("98426", "Medic'; DROP TABLE x; --", "a@x", FIXED_NOW)
        query, params = connection.calls[-1]
        self.assertIn("ON CONFLICT (radio_id) DO UPDATE SET", query)
        self.assertNotIn("DROP", query)
        self.assertNotIn("98426", query)
        self.assertEqual(params[0], "98426")
        self.assertIn("DROP TABLE", params[1])
        repository.delete_unit("98426")
        query, params = connection.calls[-1]
        self.assertIn("DELETE FROM lcdash_radio.units WHERE radio_id = %s", query)
        self.assertEqual(params, ("98426",))
        repository.seen_radio_ids(FIXED_NOW, 500)
        query, params = connection.calls[-1]
        self.assertIn("WHERE radio_id IS NOT NULL GROUP BY radio_id", query)
        self.assertEqual(params, (FIXED_NOW, 500))
        repository.radio_id_activity(["98426"], FIXED_NOW)
        query, params = connection.calls[-1]
        self.assertIn("radio_id = ANY(%s::TEXT[])", query)
        self.assertEqual(params, (FIXED_NOW, ["98426"]))

    def test_unit_writes_do_not_commit_on_their_own(self):
        connection = FakeConnection()
        repository = repository_with(connection)
        repository.upsert_unit("98426", "Medic 4", "a@x", FIXED_NOW)
        repository.delete_unit("98426")
        # The service commits through record_audit, so a failed audit rolls the
        # change back with it.
        self.assertEqual(connection.commits, 0)

    def test_an_empty_activity_lookup_runs_no_query(self):
        connection = FakeConnection()
        self.assertEqual(repository_with(connection).radio_id_activity([], FIXED_NOW), [])
        self.assertEqual(connection.calls, [])


# --------------------------------------------------------------- units service


class UnitServiceTests(unittest.TestCase):
    def setUp(self):
        self.service, self.store, _ = build_service()

    def test_naming_a_unit_creates_it_and_returns_the_item(self):
        item, created = self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertTrue(created)
        self.assertEqual(item["radio_id"], "98426")
        self.assertEqual(item["name"], "Medic 4")
        self.assertEqual(item["segments_30d"], 0)
        self.assertIsNone(item["last_seen_at"])
        self.assertEqual(item["updated_by"], ACTOR)
        self.assertEqual(self.store.units["98426"]["name"], "Medic 4")

    def test_renaming_updates_in_place(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        item, created = self.service.set_unit(
            "98426", {"name": "Medic 4 Reserve"}, actor="other@911logan.com"
        )
        self.assertFalse(created)
        self.assertEqual(item["name"], "Medic 4 Reserve")
        self.assertEqual(item["updated_by"], "other@911logan.com")
        self.assertEqual(len(self.store.units), 1)

    def test_the_name_is_stripped_and_kept_as_typed(self):
        item, _ = self.service.set_unit("5", {"name": "  Engine 12 \u00b7 Station 3  "}, actor=ACTOR)
        self.assertEqual(item["name"], "Engine 12 \u00b7 Station 3")

    def test_a_name_at_the_limits_is_accepted(self):
        self.service.set_unit("1", {"name": "A"}, actor=ACTOR)
        self.service.set_unit("2", {"name": "B" * 60}, actor=ACTOR)

    def test_bad_names_are_rejected_and_nothing_changes(self):
        bad = (
            {"name": ""}, {"name": "   "}, {"name": "x" * 61}, {"name": 4},
            {"name": None}, {"name": ["Medic 4"]}, {},
            {"name": "Medic\n4"}, {"name": "Medic\t4"}, {"name": "Medic\r4"},
            {"name": "Medic\x004"}, {"name": "Medic\x1b[31m4"},
            {"name": "Medic \u202e4"},
            {"name": "Medic 4", "extra": 1}, {"nombre": "Medic 4"},
            [], "Medic 4", None, 4,
        )
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(RadioQueryError):
                    self.service.set_unit("98426", payload, actor=ACTOR)
        self.assertEqual(self.store.units, {})
        self.assertEqual(self.store.audit, [])

    def test_bad_radio_ids_are_rejected(self):
        for radio_id in ("", "abc", "98A26", "12345678901", "98426\n", " 98426",
                         "\u0669\u0668", "-5", "9.5", "98426/1", None):
            with self.subTest(radio_id=radio_id):
                with self.assertRaises(RadioQueryError):
                    self.service.set_unit(radio_id, {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(self.store.units, {})

    def test_the_unit_limit_blocks_a_new_name_but_not_a_rename(self):
        with patch.object(radio_service, "MAX_UNITS", 2):
            self.service.set_unit("1", {"name": "One"}, actor=ACTOR)
            self.service.set_unit("2", {"name": "Two"}, actor=ACTOR)
            with self.assertRaises(RadioConflictError):
                self.service.set_unit("3", {"name": "Three"}, actor=ACTOR)
            self.assertNotIn("3", self.store.units)
            item, created = self.service.set_unit("2", {"name": "Deux"}, actor=ACTOR)
            self.assertFalse(created)
            self.assertEqual(self.service.list_units()["max_units"], 2)
        self.assertEqual(radio_service.MAX_UNITS, 2000)

    def test_setting_and_clearing_are_audited_with_the_actor(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.service.set_unit("98426", {"name": "Medic 5"}, actor=ACTOR)
        self.service.remove_unit("98426", actor=ACTOR)
        events = self.store.audit
        self.assertEqual(
            [e["event_type"] for e in events],
            ["radio.unit.set", "radio.unit.set", "radio.unit.remove"],
        )
        self.assertTrue(all(e["actor"] == ACTOR and e["segment_id"] is None for e in events))
        self.assertEqual(events[0]["detail"], {
            "radio_id": "98426", "name": "Medic 4", "previous_name": None, "created": True,
        })
        self.assertEqual(events[1]["detail"]["previous_name"], "Medic 4")
        self.assertFalse(events[1]["detail"]["created"])
        self.assertEqual(events[2]["detail"], {"radio_id": "98426", "name": "Medic 5"})

    def test_a_failed_audit_write_fails_closed_for_set_and_remove(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            self.service.set_unit("98426", {"name": "Medic 9"}, actor=ACTOR)
        with self.assertRaises(RadioDatabaseError):
            self.service.set_unit("55555", {"name": "Brush 2"}, actor=ACTOR)
        with self.assertRaises(RadioDatabaseError):
            self.service.remove_unit("98426", actor=ACTOR)
        self.assertEqual({k: v["name"] for k, v in self.store.units.items()},
                         {"98426": "Medic 4"})
        self.assertEqual(len(self.store.audit), 1)

    def test_a_failed_write_leaves_the_name_cache_alone(self):
        ingest_record(self.service, signal={"radio_id": "98426"})
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(listed(self.service)[SEGMENT_ID]["unit_name"], "Medic 4")
        self.store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            self.service.set_unit("98426", {"name": "Medic 9"}, actor=ACTOR)
        self.store.fail_audit = False
        self.assertEqual(listed(self.service)[SEGMENT_ID]["unit_name"], "Medic 4")

    def test_clearing_removes_the_name_only(self):
        ingest_record(self.service, signal={"radio_id": "98426"})
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(
            self.service.remove_unit("98426", actor=ACTOR),
            {"radio_id": "98426", "removed": True},
        )
        self.assertEqual(self.store.units, {})
        item = listed(self.service)[SEGMENT_ID]
        self.assertEqual(item["radio_id"], "98426")
        self.assertIsNone(item["unit_name"])

    def test_clearing_an_unnamed_or_malformed_id_is_not_found(self):
        for radio_id in ("98426", "abc", "", "12345678901", "98426\n"):
            with self.subTest(radio_id=radio_id):
                with self.assertRaises(RadioNotFoundError):
                    self.service.remove_unit(radio_id, actor=ACTOR)
        self.assertEqual(self.store.audit, [])

    def test_the_segment_purge_never_touches_unit_names(self):
        ingest_record(self.service, signal={"radio_id": "98426"})
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.store.db_now = FIXED_NOW + timedelta(days=90)
        self.store.received_at = FIXED_NOW
        repository = self.service._open()
        try:
            self.assertEqual(repository.purge_expired(), 1)
        finally:
            repository.__exit__(None, None, None)
        self.assertEqual(self.store.segments, {})
        self.assertIn("98426", self.store.units)


class UnitListTests(unittest.TestCase):
    def setUp(self):
        self.service, self.store, _ = build_service()
        # Heard 14:00-14:20 on 2026-10-16, one hour before FIXED_NOW's day end.
        add_heard(self.service, "heard_a_0001", "98426", minute=0)
        add_heard(self.service, "heard_a_0002", "98426", minute=10)
        add_heard(self.service, "heard_b_0001", "440593", minute=20)
        add_heard(self.service, "no_signal_0001", None, minute=30)

    def listing(self):
        return self.service.list_units()

    def by_id(self):
        return {u["radio_id"]: u for u in self.listing()["units"]}

    def test_seen_ids_are_listed_most_recent_first_with_counts(self):
        units = self.listing()["units"]
        self.assertEqual([u["radio_id"] for u in units], ["440593", "98426"])
        self.assertEqual(units[1]["segments_30d"], 2)
        self.assertEqual(units[1]["last_seen_at"], "2026-10-16T14:10:00+00:00")
        self.assertEqual(units[0]["segments_30d"], 1)
        self.assertTrue(all(u["name"] is None for u in units))
        self.assertEqual(self.listing()["max_units"], 2000)

    def test_a_segment_without_a_radio_id_is_not_a_unit(self):
        self.assertNotIn(None, self.by_id())
        self.assertEqual(len(self.listing()["units"]), 2)

    def test_named_and_seen_ids_merge(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        units = self.by_id()
        self.assertEqual(units["98426"]["name"], "Medic 4")
        self.assertEqual(units["98426"]["segments_30d"], 2)
        self.assertIsNone(units["440593"]["name"])

    def test_a_named_unit_never_heard_is_listed_last_with_zero(self):
        self.service.set_unit("77777", {"name": "Brush 2"}, actor=ACTOR)
        units = self.listing()["units"]
        self.assertEqual(units[-1]["radio_id"], "77777")
        self.assertEqual(units[-1]["name"], "Brush 2")
        self.assertEqual(units[-1]["segments_30d"], 0)
        self.assertIsNone(units[-1]["last_seen_at"])
        self.assertEqual(len(units), 3)

    def test_each_id_appears_once_and_the_row_shape_is_exact(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        units = self.listing()["units"]
        self.assertEqual(len({u["radio_id"] for u in units}), len(units))
        for unit in units:
            self.assertEqual(
                set(unit), {"radio_id", "name", "segments_30d", "last_seen_at"}
            )

    def test_only_segments_in_the_last_30_days_are_counted(self):
        self.store.db_now = FIXED_NOW + timedelta(days=40)
        units = self.by_id()
        self.assertEqual(units["98426"]["segments_30d"], 0)
        # Still listed: it was heard, and the last-heard time is kept.
        self.assertEqual(units["98426"]["last_seen_at"], "2026-10-16T14:10:00+00:00")

    def test_at_most_500_heard_ids_are_listed_but_named_ones_always_are(self):
        with patch.object(radio_service, "MAX_SEEN_UNITS", 1):
            self.assertEqual([u["radio_id"] for u in self.listing()["units"]], ["440593"])
            self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
            units = self.by_id()
        # 440593 is the single most recent heard ID; 98426 is named, so it is
        # listed too, with its true count rather than zero.
        self.assertEqual(set(units), {"440593", "98426"})
        self.assertEqual(units["98426"]["segments_30d"], 2)
        self.assertEqual(units["98426"]["last_seen_at"], "2026-10-16T14:10:00+00:00")

    def test_listing_units_is_not_audited(self):
        self.listing()
        self.assertEqual(self.store.audit, [])


# ------------------------------------------------------- items, filter, search


class ItemFieldTests(unittest.TestCase):
    def setUp(self):
        self.service, self.store, _ = build_service()

    def test_items_carry_the_signal_and_the_unit_name(self):
        ingest_record(self.service, signal=FULL_SIGNAL)
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        item = listed(self.service)[SEGMENT_ID]
        self.assertEqual(item["radio_id"], "98426")
        self.assertEqual(item["talkgroup"], "G4407")
        self.assertEqual(item["console_label"], "Position2")
        self.assertEqual(item["unit_name"], "Medic 4")

    def test_an_unnamed_or_unsignalled_segment_has_nulls(self):
        ingest_record(self.service, segment_id="old_row_0001")
        ingest_record(self.service, segment_id="unnamed_0001",
                      signal={"radio_id": "440593"},
                      started_at="2026-10-16T14:06:00+00:00",
                      ended_at="2026-10-16T14:06:05+00:00")
        items = listed(self.service)
        self.assertEqual(
            (items["old_row_0001"]["radio_id"], items["old_row_0001"]["talkgroup"],
             items["old_row_0001"]["console_label"], items["old_row_0001"]["unit_name"]),
            (None, None, None, None),
        )
        self.assertEqual(items["unnamed_0001"]["radio_id"], "440593")
        self.assertIsNone(items["unnamed_0001"]["unit_name"])

    def test_a_console_stream_has_a_label_and_no_unit_name(self):
        ingest_record(self.service, signal={"console_label": "Position2"})
        item = listed(self.service)[SEGMENT_ID]
        self.assertEqual(item["console_label"], "Position2")
        self.assertIsNone(item["radio_id"])
        self.assertIsNone(item["unit_name"])

    def test_a_possible_noise_segment_still_carries_its_radio_id(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        ingest_record(
            self.service, signal=FULL_SIGNAL, transcript="Thanks for watching!",
            ended_at="2026-10-16T14:05:01+00:00",
        )
        item = listed(self.service)[SEGMENT_ID]
        self.assertIs(item["possible_noise"], True)
        self.assertEqual(item["text"], "Thanks for watching!")
        self.assertEqual(item["radio_id"], "98426")
        self.assertEqual(item["talkgroup"], "G4407")
        self.assertEqual(item["unit_name"], "Medic 4")

    def test_corrections_and_reverts_keep_the_signal_and_name(self):
        ingest_record(self.service, signal=FULL_SIGNAL)
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        corrected = self.service.correct_segment(SEGMENT_ID, "Copy.", actor=ACTOR)
        reverted = self.service.revert_correction(SEGMENT_ID, actor=ACTOR)
        for item in (corrected, reverted, listed(self.service)[SEGMENT_ID]):
            self.assertEqual(item["radio_id"], "98426")
            self.assertEqual(item["unit_name"], "Medic 4")
            self.assertEqual(item["console_label"], "Position2")

    def test_a_unit_name_outage_does_not_fail_a_correction(self):
        ingest_record(self.service, signal=FULL_SIGNAL)
        self.store.fail_unit_read = True
        item = self.service.correct_segment(SEGMENT_ID, "Copy.", actor=ACTOR)
        self.assertEqual(item["text"], "Copy.")
        self.assertEqual(item["radio_id"], "98426")
        self.assertIsNone(item["unit_name"])
        # A listing is audited and exact, so it fails rather than hide names.
        with self.assertRaises(RadioDatabaseError):
            self.service.list_segments(actor=ACTOR)


class FilterAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.service, self.store, _ = build_service()
        add_heard(self.service, "seg_medic_0001", "98426", minute=1, transcript="Enroute now.")
        add_heard(self.service, "seg_medic_0002", "98426", minute=2, transcript="On scene.")
        add_heard(self.service, "seg_eng_0001", "440593", minute=3, transcript="Engine copy.")
        add_heard(self.service, "seg_other_0001", "9842", minute=4, transcript="Plate check.")
        add_heard(self.service, "seg_plain_0001", None, minute=5,
                  transcript="Medic four is mentioned here.")
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.service.set_unit("440593", {"name": "Engine 12 (Station_3)"}, actor=ACTOR)

    def ids(self, **filters):
        return [
            item["segment_id"]
            for item in self.service.list_segments(actor=ACTOR, **filters)["segments"]
        ]

    def test_the_radio_id_filter_is_exact(self):
        self.assertEqual(self.ids(radio_id="98426"), ["seg_medic_0002", "seg_medic_0001"])
        self.assertEqual(self.ids(radio_id="9842"), ["seg_other_0001"])
        self.assertEqual(self.ids(radio_id="12345"), [])
        self.assertEqual(self.ids(radio_id=" 440593 "), ["seg_eng_0001"])

    def test_the_radio_id_filter_combines_with_the_other_filters(self):
        self.assertEqual(self.ids(radio_id="98426", q="scene"), ["seg_medic_0002"])
        self.assertEqual(self.ids(radio_id="98426", channel="ems"), [])
        self.assertEqual(self.ids(radio_id="98426", channel="law"),
                         ["seg_medic_0002", "seg_medic_0001"])

    def test_bad_radio_id_filters_are_400_before_any_query(self):
        audited = len(self.store.audit)
        for value in ("abc", "98A26", "12345678901", "98426%", "9842'", "-1", "9 8",
                      "\u0669\u0668"):
            with self.subTest(value=value):
                with self.assertRaises(RadioQueryError):
                    self.service.list_segments(actor=ACTOR, radio_id=value)
        self.assertEqual(self.store.list_calls, [])
        self.assertEqual(len(self.store.audit), audited)

    def test_q_finds_a_radio_id_by_prefix(self):
        self.assertEqual(self.ids(q="98426"), ["seg_medic_0002", "seg_medic_0001"])
        self.assertEqual(self.ids(q="9842"),
                         ["seg_other_0001", "seg_medic_0002", "seg_medic_0001"])
        self.assertEqual(self.ids(q="4405"), ["seg_eng_0001"])
        # A prefix, not a substring: the ID 98426 does not contain "426" at
        # the start, and no transcript says it.
        self.assertEqual(self.ids(q="426"), [])

    def test_q_finds_a_unit_name_case_insensitively_as_a_substring(self):
        self.assertEqual(self.ids(q="medic 4"),
                         ["seg_medic_0002", "seg_medic_0001"])
        self.assertEqual(self.ids(q="MEDIC"),
                         ["seg_plain_0001", "seg_medic_0002", "seg_medic_0001"])
        self.assertEqual(self.ids(q="engine 12"), ["seg_eng_0001"])

    def test_q_still_searches_the_transcript(self):
        self.assertEqual(self.ids(q="plate"), ["seg_other_0001"])
        self.assertEqual(self.ids(q="four is mentioned"), ["seg_plain_0001"])

    def test_wildcards_in_q_are_literal_for_names_and_ids(self):
        # An underscore in the name is literal; "_" is not "any character".
        self.assertEqual(self.ids(q="station_3"), ["seg_eng_0001"])
        self.assertEqual(self.ids(q="station 3"), [])
        self.assertEqual(self.ids(q="%"), [])
        self.assertEqual(self.ids(q="98%"), [])
        self.assertEqual(self.ids(q="9_426"), [])

    def test_the_service_hands_the_repository_bound_values_only(self):
        seen = {}
        real = type(self.service._open()).list_segments

        def spy(repository, **kwargs):
            seen.update(kwargs)
            return real(repository, **kwargs)

        from .test_radio_routes import FakeRepository

        with patch.object(FakeRepository, "list_segments", spy):
            self.service.list_segments(actor=ACTOR, q="9842", radio_id="98426")
        self.assertEqual(seen["radio_id"], "98426")
        self.assertEqual(seen["id_prefix"], "9842%")
        self.assertEqual(seen["unit_radio_ids"], None)
        self.assertEqual(seen["like_pattern"], "%9842%")
        with patch.object(FakeRepository, "list_segments", spy):
            self.service.list_segments(actor=ACTOR, q="Medic")
        self.assertIsNone(seen["id_prefix"])
        self.assertEqual(seen["unit_radio_ids"], ["98426"])
        with patch.object(FakeRepository, "list_segments", spy):
            self.service.list_segments(actor=ACTOR, q="zzz")
        self.assertIsNone(seen["unit_radio_ids"])

    def test_the_listing_audit_records_the_radio_id_filter(self):
        self.store.audit.clear()
        self.service.list_segments(actor=ACTOR, radio_id="98426")
        self.assertEqual(len(self.store.audit), 1)
        self.assertEqual(self.store.audit[0]["detail"]["radio_id"], "98426")

    def test_pagination_works_with_the_filter(self):
        first = self.service.list_segments(actor=ACTOR, radio_id="98426", limit="1")
        self.assertTrue(first["has_more"])
        second = self.service.list_segments(
            actor=ACTOR, radio_id="98426", limit="1", before=first["next_before"]
        )
        self.assertEqual([i["segment_id"] for i in first["segments"]], ["seg_medic_0002"])
        self.assertEqual([i["segment_id"] for i in second["segments"]], ["seg_medic_0001"])
        self.assertFalse(second["has_more"])


# --------------------------------------------------------------- name cache


class UnitNameCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.service, self.store, _ = build_service(clock=lambda: self.now[0])
        ingest_record(self.service, signal={"radio_id": "98426"})

    def name(self):
        return listed(self.service)[SEGMENT_ID]["unit_name"]

    def test_names_are_read_once_per_60_seconds(self):
        self.assertIsNone(self.name())
        self.assertIsNone(self.name())
        self.assertEqual(self.store.unit_reads, 1)
        self.now[0] += 59
        self.name()
        self.assertEqual(self.store.unit_reads, 1)
        self.now[0] += 2
        self.name()
        self.assertEqual(self.store.unit_reads, 2)

    def test_a_change_made_elsewhere_shows_up_after_the_cache_expires(self):
        self.assertIsNone(self.name())
        self.store.units["98426"] = {"name": "Medic 4", "updated_by": "x",
                                     "updated_at": FIXED_NOW}
        self.assertIsNone(self.name())  # still cached
        self.now[0] += 61
        self.assertEqual(self.name(), "Medic 4")

    def test_setting_a_name_drops_the_cache_at_once(self):
        self.assertIsNone(self.name())
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(self.name(), "Medic 4")
        self.service.set_unit("98426", {"name": "Medic 5"}, actor=ACTOR)
        self.assertEqual(self.name(), "Medic 5")

    def test_clearing_a_name_drops_the_cache_at_once(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(self.name(), "Medic 4")
        self.service.remove_unit("98426", actor=ACTOR)
        self.assertIsNone(self.name())

    def test_search_by_name_uses_the_same_cache(self):
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        reads = self.store.unit_reads
        for _ in range(3):
            self.service.list_segments(actor=ACTOR, q="medic")
        self.assertLessEqual(self.store.unit_reads - reads, 1)

    def test_the_cache_is_not_shared_between_service_instances(self):
        other, _, _ = build_service(self.store, clock=lambda: self.now[0])
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(self.name(), "Medic 4")
        self.assertEqual(listed(other)[SEGMENT_ID]["unit_name"], "Medic 4")


# ---------------------------------------------------------------- HTTP routes


class UnitRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        self.use_service(self.service)
        self.sign_in_as(ADMIN)

    def test_name_list_rename_and_clear_over_http(self):
        created = self.client.put("/api/radio/units/98426", json={"name": "Medic 4"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.headers["cache-control"], "private, no-store")
        body = created.json()
        self.assertEqual(body["radio_id"], "98426")
        self.assertEqual(body["name"], "Medic 4")
        self.assertEqual(body["updated_by"], ADMIN_EMAIL)
        renamed = self.client.put("/api/radio/units/98426", json={"name": "Medic 5"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Medic 5")
        listing = self.client.get("/api/radio/units")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.headers["cache-control"], "private, no-store")
        self.assertEqual(listing.json()["max_units"], 2000)
        self.assertEqual(
            listing.json()["units"],
            [{"radio_id": "98426", "name": "Medic 5", "segments_30d": 0,
              "last_seen_at": None}],
        )
        removed = self.client.delete("/api/radio/units/98426")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json(), {"radio_id": "98426", "removed": True})
        self.assertEqual(self.client.get("/api/radio/units").json()["units"], [])
        self.assertEqual(
            [e["event_type"] for e in self.store.audit],
            ["radio.unit.set", "radio.unit.set", "radio.unit.remove"],
        )

    def test_errors_map_to_400_404_409_415_and_503(self):
        put = lambda path, body: self.client.put(path, json=body)
        self.assertEqual(put("/api/radio/units/98426", {"name": ""}).status_code, 400)
        self.assertEqual(put("/api/radio/units/98426", {"name": "x" * 61}).status_code, 400)
        self.assertEqual(put("/api/radio/units/98426", {"name": "a\nb"}).status_code, 400)
        self.assertEqual(put("/api/radio/units/98426", {"name": "a", "x": 1}).status_code, 400)
        self.assertEqual(put("/api/radio/units/98426", []).status_code, 400)
        self.assertEqual(put("/api/radio/units/abc", {"name": "a"}).status_code, 400)
        self.assertEqual(put("/api/radio/units/12345678901", {"name": "a"}).status_code, 400)
        self.assertEqual(
            self.client.put("/api/radio/units/98426", content=b"{",
                            headers={"content-type": "application/json"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.put("/api/radio/units/98426", data={"name": "a"}).status_code, 415
        )
        self.assertEqual(self.client.delete("/api/radio/units/98426").status_code, 404)
        self.assertEqual(self.client.delete("/api/radio/units/abc").status_code, 404)
        with patch.object(radio_service, "MAX_UNITS", 1):
            self.assertEqual(put("/api/radio/units/1", {"name": "One"}).status_code, 201)
            conflict = put("/api/radio/units/2", {"name": "Two"})
            self.assertEqual(conflict.status_code, 409)
            self.assertIn("At most 1", conflict.json()["detail"])
        self.assertEqual(set(self.store.units), {"1"})

    def test_a_cross_site_write_is_refused_and_changes_nothing(self):
        headers = {"sec-fetch-site": "cross-site"}
        self.assertEqual(
            self.client.put("/api/radio/units/98426", json={"name": "Medic 4"},
                            headers=headers).status_code, 403)
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.assertEqual(
            self.client.delete("/api/radio/units/98426", headers=headers).status_code, 403)
        self.assertEqual(self.store.units["98426"]["name"], "Medic 4")

    def test_a_failed_audit_write_is_503_and_nothing_changes(self):
        self.store.fail_audit = True
        response = self.client.put("/api/radio/units/98426", json={"name": "Medic 4"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Medic", response.text)
        self.assertEqual(self.store.units, {})
        self.store.fail_audit = False
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)
        self.store.fail_audit = True
        self.assertEqual(self.client.delete("/api/radio/units/98426").status_code, 503)
        self.assertIn("98426", self.store.units)

    def test_an_unavailable_database_is_503_for_the_listing(self):
        self.store.fail_connect = True
        self.assertEqual(self.client.get("/api/radio/units").status_code, 503)

    def test_the_actor_is_the_verified_identity(self):
        self.client.put("/api/radio/units/98426", json={"name": "Medic 4"},
                        headers={"X-Forwarded-User": "someone@else.com"})
        self.assertEqual(self.store.units["98426"]["updated_by"], ADMIN_EMAIL)
        self.assertEqual(self.store.audit[0]["actor"], ADMIN_EMAIL)


class UnitAccessMatrixTests(_RouteCase):
    ROUTES = (
        ("GET", "/api/radio/units", None),
        ("PUT", "/api/radio/units/98426", {"name": "Medic 4"}),
        ("PUT", "/api/radio/units/abc", {"name": ""}),
        ("DELETE", "/api/radio/units/98426", None),
        ("DELETE", "/api/radio/units/abc", None),
    )

    def test_only_admins_reach_the_unit_routes_and_the_service_is_never_called(self):
        mock_service = MagicMock()
        self.use_service(mock_service)
        garbage = {"content": b"{{ not json", "headers": {"content-type": "text/plain"}}
        for role, identity in list(NON_ADMIN_HUMANS) + list(DENIED_CALLERS):
            self.sign_in_as(identity)
            for method, path, body in self.ROUTES:
                with self.subTest(role=role, method=method, path=path):
                    good = self.client.request(method, path, **({"json": body} if body else {}))
                    bad = self.client.request(method, path, **garbage)
                    # Uniform 403 before any validation: a bad ID, a bad body
                    # and a good request are indistinguishable.
                    self.assertEqual(good.status_code, 403)
                    self.assertEqual(bad.status_code, 403)
                    self.assertEqual(good.json(), bad.json())
        self.assertEqual(mock_service.mock_calls, [])

    def test_avatar_and_unidentified_callers_are_denied_even_with_hostile_input(self):
        mock_service = MagicMock()
        self.use_service(mock_service)
        for role, identity in DENIED_CALLERS:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                self.assertEqual(
                    self.client.get("/api/radio/units?x=" + "a" * 5000).status_code, 403)
                self.assertEqual(
                    self.client.put("/api/radio/units/%00%27", json={"name": "x" * 9000}
                                    ).status_code, 403)
        self.assertEqual(mock_service.mock_calls, [])

    def test_an_unrecognized_group_is_denied_like_everyone_else(self):
        from .test_radio_routes import _identity

        mock_service = MagicMock()
        self.use_service(mock_service)
        self.sign_in_as(_identity("x@911logan.com", "some-other-group"))
        for method, path, body in self.ROUTES:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, **({"json": body} if body else {}))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(mock_service.mock_calls, [])

    def test_the_admin_can_use_every_route(self):
        service, store, _ = build_service()
        self.use_service(service)
        self.sign_in_as(ADMIN)
        self.assertEqual(self.client.get("/api/radio/units").status_code, 200)
        self.assertEqual(
            self.client.put("/api/radio/units/98426", json={"name": "Medic 4"}).status_code, 201)
        self.assertEqual(self.client.delete("/api/radio/units/98426").status_code, 200)


class HumanFeedFieldsTests(_RouteCase):
    """Names and radio IDs are for every human role; the avatar sees nothing."""

    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        self.use_service(self.service)
        ingest_record(self.service, signal=FULL_SIGNAL)
        self.service.set_unit("98426", {"name": "Medic 4"}, actor=ACTOR)

    def test_every_human_role_sees_the_id_and_the_name_and_can_filter(self):
        for role, identity in HUMAN_ROLES:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                listing = self.client.get("/api/radio/segments?radio_id=98426")
                self.assertEqual(listing.status_code, 200, listing.text)
                item = listing.json()["segments"][0]
                self.assertEqual(item["radio_id"], "98426")
                self.assertEqual(item["talkgroup"], "G4407")
                self.assertEqual(item["console_label"], "Position2")
                self.assertEqual(item["unit_name"], "Medic 4")
                found = self.client.get("/api/radio/segments?q=medic")
                self.assertEqual(len(found.json()["segments"]), 1)
                none = self.client.get("/api/radio/segments?radio_id=11111")
                self.assertEqual(none.json()["segments"], [])

    def test_a_bad_filter_is_400_for_a_human_and_403_for_the_denied(self):
        for role, identity in HUMAN_ROLES:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                self.assertEqual(
                    self.client.get("/api/radio/segments?radio_id=abc").status_code, 400)
        for role, identity in DENIED_CALLERS:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                self.assertEqual(
                    self.client.get("/api/radio/segments?radio_id=abc").status_code, 403)
                self.assertEqual(
                    self.client.get("/api/radio/segments?radio_id=98426").status_code, 403)

    def test_a_correction_response_carries_the_unit_name(self):
        self.sign_in_as(HUMAN_ROLES[2][1])  # a plain user
        response = self.client.put(
            f"/api/radio/segments/{SEGMENT_ID}/correction", json={"text": "Copy."}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unit_name"], "Medic 4")
        self.assertEqual(response.json()["radio_id"], "98426")


# --------------------------------------------------------------- tier closure


class UnitTierClosureTests(unittest.TestCase):
    PATHS = (
        "/api/radio/units", "/api/radio/units/", "/api/radio/units/98426",
        "/api/radio/units/98426/other", "/api/radio/units/abc",
        "/api/radio/units?radio_id=98426",
    )

    def test_the_tier_helper_never_admits_the_unit_routes(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertFalse(radio_access.is_radio_human_path(path))
                self.assertFalse(radio_access.is_radio_page(path))

    def test_no_module_level_tier_list_names_the_unit_routes(self):
        from app.core import avatar_tier, dispatcher_tier, fire_officer_tier, sanitized_tier

        lists = []
        for module in (avatar_tier, dispatcher_tier, fire_officer_tier, sanitized_tier):
            for name in dir(module):
                value = getattr(module, name)
                if name.startswith("_") and isinstance(value, (tuple, frozenset, set, list)):
                    lists.extend(str(item) for item in value)
        self.assertFalse([item for item in lists if "/api/radio/units" in item])

    def test_the_restricted_tiers_deny_the_unit_routes_by_default(self):
        from app.core import avatar_tier, dispatcher_tier, fire_officer_tier, sanitized_tier

        for path in self.PATHS:
            with self.subTest(path=path):
                # Deny-by-default tiers never allow them; the dispatcher tier
                # allows by default, so the route's own admin gate answers.
                self.assertFalse(sanitized_tier.is_path_allowed_for_user(path))
                self.assertFalse(fire_officer_tier.is_path_allowed_for_fire_officer(path))
                self.assertFalse(avatar_tier.is_path_allowed_for_avatar(path))
        for path in self.PATHS[:3]:
            self.assertFalse(dispatcher_tier.is_path_blocked_for_dispatcher(path))


if __name__ == "__main__":
    unittest.main()
