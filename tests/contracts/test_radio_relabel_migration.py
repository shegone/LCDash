"""The one-time relabel of DMR audio captured as ems_north_vhf / fire_north_vhf.

The static checks always run. The live checks run against a real PostgreSQL
when LCDASH_TEST_PG_DSN names a server that may create and drop databases
(for example "host=127.0.0.1 port=55432 user=postgres"); they were run against
PostgreSQL 17.6 on 2026-10-20 (production is RDS 17).
"""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = (ROOT / "database" / "radio_schema.sql").read_text(encoding="utf-8")
MARKER = "relabel-dmr-2026-10-20"
CUTOFF = "2026-10-20 20:30:13+00"


class StaticMigrationTests(unittest.TestCase):
    def test_it_runs_once_behind_a_marker_row(self):
        self.assertEqual(SCHEMA.count(f"'{MARKER}'"), 2)
        self.assertIn("IF NOT EXISTS (", SCHEMA.split("-- v5: one-time relabel")[1])
        self.assertIn("CREATE TABLE IF NOT EXISTS lcdash_radio.schema_migrations", SCHEMA)

    def test_only_rows_before_the_listener_config_fix_move(self):
        block = SCHEMA.split("-- v5: one-time relabel")[1]
        self.assertEqual(block.count(f"started_at < TIMESTAMPTZ '{CUTOFF}'"), 2)
        for old, new in (("ems_north_vhf", "ems_dmr"), ("fire_north_vhf", "fire_dmr")):
            self.assertRegex(
                block,
                rf"SET channel = '{new}'\s+WHERE channel = '{old}'\s+AND started_at <")

    def test_it_changes_nothing_else(self):
        block = SCHEMA.split("-- v5: one-time relabel")[1]
        updates = re.findall(r"UPDATE (\S+)", block)
        self.assertEqual(updates, ["lcdash_radio.segments"] * 2)
        for banned in ("DELETE", "DROP ", "TRUNCATE", "audio_key"):
            self.assertNotIn(banned, block.split("DO $$")[1], banned)

    def test_the_audit_constraint_is_untouched(self):
        # The migration must not widen the audit event list (other tests pin it).
        self.assertEqual(SCHEMA.count("'radio.unit.remove'"), 3)
        self.assertNotIn("radio.segments.relabel", SCHEMA)


DSN = os.environ.get("LCDASH_TEST_PG_DSN", "")


@unittest.skipUnless(DSN, "set LCDASH_TEST_PG_DSN to run against a real PostgreSQL")
class LiveMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        # The pre-migration schema is the file without its v5 block.
        text = SCHEMA.replace("\r\n", "\n")
        start = text.index("-- v5: marker rows")
        end = text.index("-- Databases created before v2")
        cls.old = (text[:start] + text[end:]).split("\n-- v5: one-time relabel")[0] + "\n"
        cls.created: list[str] = []

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as c:
            for name in cls.created:
                c.execute(f'DROP DATABASE IF EXISTS "{name}"')

    def database(self, name):
        with self.psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
            c.execute(f'CREATE DATABASE "{name}"')
        self.created.append(name)
        return f"{DSN} dbname={name}"

    def apply(self, dsn, sql):
        with self.psycopg.connect(dsn) as c:
            c.execute(sql)

    def seed(self, dsn, rows):
        with self.psycopg.connect(dsn, autocommit=True) as c:
            for segment_id, channel, started in rows:
                c.execute(
                    "INSERT INTO lcdash_radio.segments (segment_id, channel, direction, "
                    "started_at, ended_at, codec, payload_type, sample_rate_hz, audio_key) "
                    "VALUES (%s, %s, 'rx', %s, %s, 'pcmu', 0, 8000, %s)",
                    (segment_id, channel, started, started, f"radio/audio/{channel}/{segment_id}.wav"))

    def counts(self, dsn):
        with self.psycopg.connect(dsn) as c:
            return dict(c.execute(
                "SELECT channel, count(*) FROM lcdash_radio.segments GROUP BY channel").fetchall())

    def markers(self, dsn):
        with self.psycopg.connect(dsn) as c:
            return c.execute(
                "SELECT name, detail FROM lcdash_radio.schema_migrations").fetchall()

    def test_upgrade_moves_only_early_rows_once(self):
        dsn = self.database("lcdash_relabel_a")
        self.apply(dsn, self.old)
        self.seed(dsn, [
            ("e1", "ems_north_vhf", "2026-10-20T19:10:00+00"),
            ("e2", "ems_north_vhf", "2026-10-20T20:29:59+00"),
            ("f1", "fire_north_vhf", "2026-10-20T19:15:00+00"),
            ("f2", "fire_north_vhf", "2026-10-20T20:30:13+00"),
            ("l1", "law", "2026-10-20T19:00:00+00"),
        ])
        self.apply(dsn, SCHEMA)
        self.assertEqual(
            self.counts(dsn),
            {"ems_dmr": 2, "fire_dmr": 1, "fire_north_vhf": 1, "law": 1})
        self.assertEqual(
            self.markers(dsn),
            [(MARKER, {"ems_north_vhf_to_ems_dmr": 2, "fire_north_vhf_to_fire_dmr": 1})])
        # Later rows under the old names are real and must survive any re-run.
        self.seed(dsn, [("e3", "ems_north_vhf", "2026-10-20T19:20:00+00")])
        snapshot = self.counts(dsn)
        self.apply(dsn, SCHEMA)
        self.apply(dsn, SCHEMA)
        self.assertEqual(self.counts(dsn), snapshot)
        self.assertEqual(len(self.markers(dsn)), 1)

    def test_a_fresh_database_records_zero_moves(self):
        dsn = self.database("lcdash_relabel_b")
        self.apply(dsn, SCHEMA)
        self.apply(dsn, SCHEMA)
        self.assertEqual(
            self.markers(dsn),
            [(MARKER, {"ems_north_vhf_to_ems_dmr": 0, "fire_north_vhf_to_fire_dmr": 0})])

    def test_a_failure_rolls_the_relabel_back(self):
        dsn = self.database("lcdash_relabel_c")
        self.apply(dsn, self.old)
        self.seed(dsn, [("e1", "ems_north_vhf", "2026-10-20T19:10:00+00")])
        with self.assertRaises(self.psycopg.Error):
            self.apply(dsn, SCHEMA + "\nSELECT 1/0;\n")
        self.assertEqual(self.counts(dsn), {"ems_north_vhf": 1})


if __name__ == "__main__":
    unittest.main()
