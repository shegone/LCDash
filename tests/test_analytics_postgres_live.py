"""The analytics dispatcher SQL against a real PostgreSQL.

Skipped unless LCDASH_TEST_PG_DSN names a server that may create and drop
databases, for example "host=127.0.0.1 port=55432 user=postgres". Run against
PostgreSQL 17.6 on 2026-10-21 (production is RDS 17).
"""

import hashlib
import os
import statistics
import unittest
from datetime import datetime, timedelta, timezone

from app.services import analytics_reporting as ar

DSN = os.environ.get("LCDASH_TEST_PG_DSN", "")


@unittest.skipUnless(DSN, "set LCDASH_TEST_PG_DSN to run against a real PostgreSQL")
class LiveAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        cls.created = []

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as c:
            for name in cls.created:
                c.execute(f'DROP DATABASE IF EXISTS "{name}"')

    def repository(self, name):
        from app.services.analytics_database import AnalyticsRepository

        with self.psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
            c.execute(f'CREATE DATABASE "{name}"')
        self.created.append(name)
        user = DSN.split("user=")[1].split()[0]
        host = DSN.split("host=")[1].split()[0]
        port = DSN.split("port=")[1].split()[0]
        url = f"postgresql://{user}@{host}:{port}/{name}"
        repository = AnalyticsRepository(url)
        repository.__enter__()
        self.addCleanup(repository.__exit__, None, None, None)
        repository.initialize_schema()
        return repository

    def seeder(self, repository):
        now = datetime.now(timezone.utc)
        base = now - timedelta(hours=10)
        counter = {"n": 0}

        def add(taker, ident, dispatches, scheduled=False, desc="Alarm", agency="FC 1"):
            counter["n"] += 1
            cfs = f"C-{counter['n']:04d}"
            received = base + timedelta(minutes=counter["n"] * 5)
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.calls (cfs_number, call_taker, "
                "call_taker_unique_identifier, call_received_at, is_scheduled, "
                "incident_description, incident_code, priority, dispatch_agency, "
                "source_collected_at) VALUES (%s,%s,%s,%s,%s,%s,'X','2',%s,%s)",
                (cfs, taker, ident, received, scheduled, desc, agency, now))
            for ori, seconds in dispatches:
                repository.connection.execute(
                    "INSERT INTO lcdash_analytics.call_agency_times "
                    "(cfs_number, agency_ori, dispatched_at) VALUES (%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (cfs, ori, received + timedelta(seconds=seconds)))
            return cfs

        return add

    def test_percentile_reference_and_slow_calls_match_the_table(self):
        repository = self.repository("lcdash_an_live_a")
        add = self.seeder(repository)
        seconds = [20, 45, 60, 75, 90, 100, 120, 150, 180, 400, 1200, 2400]
        for value in seconds:
            add("SHANA ROSSER", "id-rosser", [("FC 1", value)])
        for _ in range(3):
            add("SHANA ROSSER", "id-rosser", [])                 # never dispatched
        add("SHANA ROSSER", "id-rosser", [("FC 1", 900)], scheduled=True)
        add("SHANA ROSSER", "id-rosser", [("FC 1", 7200)])       # over an hour
        for value in (200, 300, 280, 260):
            add("KADEN CANTRELL", "", [("LEASA", value)])        # legacy key (no identifier)
        add("KIM MAYNARD", "id-kim", [("FC 1", 600)])            # excluded identity
        repository.connection.commit()

        window = ar.resolve_analytics_window(period="24h")
        overview = ar._query_overview(repository, window, None)
        rosser = next(d for d in overview["dispatchers"] if d["call_taker"] == "SHANA ROSSER")
        fmt = ar._format_duration
        self.assertEqual(rosser["processing_samples"], 12)
        self.assertEqual(rosser["average_processing"], fmt(statistics.mean(seconds)))
        self.assertEqual(rosser["median_processing"], fmt(statistics.median(seconds)))
        self.assertEqual(
            rosser["p90_processing"],
            fmt(statistics.quantiles(seconds, n=10, method="inclusive")[8]))
        self.assertEqual(rosser["over_180_count"], 3)
        self.assertNotIn("KIM MAYNARD", [d["call_taker"] for d in overview["dispatchers"]])
        ref = hashlib.sha256(b"id-rosser").hexdigest()[:16]
        self.assertEqual(rosser["dispatcher_ref"], ref)
        cantrell = next(d for d in overview["dispatchers"] if d["call_taker"] == "KADEN CANTRELL")
        self.assertEqual(
            cantrell["dispatcher_ref"], hashlib.sha256(b"legacy:KADEN CANTRELL").hexdigest()[:16])

        slow = ar._query_slow_calls(repository, window, ref)
        self.assertEqual(slow["total"], rosser["over_180_count"])
        self.assertEqual([c["processing_seconds"] for c in slow["calls"]], [2400, 1200, 400])
        self.assertEqual(ar._query_slow_calls(repository, window, "0" * 16)["total"], 0)

    def test_discipline_breakdown_and_the_iso_fire_view(self):
        repository = self.repository("lcdash_an_live_b")
        add = self.seeder(repository)
        x_fire = [30, 50, 60, 64, 70, 100, 106, 110, 200, 400]
        for index, value in enumerate(x_fire):
            add("DISPATCHER X", "id-x", [("FC 100" if index % 2 else "fc 1000", value)])  # case-insensitive
        for value in (20, 40, 80):
            add("DISPATCHER X", "id-x", [("LEASA", value)])
        for value in (90, 300):
            add("DISPATCHER X", "id-x", [("LCSO", value)])
        for value in (40, 45, 300):
            add("DISPATCHER Y", "id-y", [("FC 200", value)])
        add("DISPATCHER X", "id-x", [("FC 100", 50), ("LEASA", 150)])     # counts in both
        add("DISPATCHER Y", "id-y", [("FC 300", 70), ("FC 400", 500)])    # first dispatch counts
        add("DISPATCHER X", "id-x", [("ZZZ", 10)])                        # no discipline
        add("DISPATCHER X", "id-x", [("FC 100", 20)], scheduled=True)     # scheduled
        add("DISPATCHER X", "id-x", [("FC 100", 7200)])                   # over an hour
        add("KIM MAYNARD", "id-kim", [("FC 100", 5)])                     # excluded identity
        repository.connection.commit()

        window = ar.resolve_analytics_window(period="24h")
        params = {
            "window_start": window.start_at,
            "window_end": window.end_at,
            "excluded_dispatcher_identity": ar.EXCLUDED_DISPATCHER_IDENTITY,
        }
        groups, iso = ar._discipline_metrics(repository, params)
        by = {group["key"]: group for group in groups}

        def figures(values):
            return {
                "timed": len(values),
                "median": ar._format_duration(statistics.median(values)),
                "within_fast_percent": round(sum(v <= 64 for v in values) / len(values) * 100, 1),
                "within_slow_percent": round(sum(v <= 106 for v in values) / len(values) * 100, 1),
                "over_180_count": sum(v > 180 for v in values),
            }

        def pick(entry):
            return {k: entry[k] for k in figures([1])}

        fire_x = x_fire + [50]
        fire_y = [40, 45, 300, 70]
        x_row = next(d for d in by["fire"]["dispatchers"] if d["call_taker"] == "DISPATCHER X")
        self.assertEqual(pick(x_row), figures(fire_x))
        self.assertEqual(pick(by["fire"]["overall"]), figures(fire_x + fire_y))
        self.assertEqual(pick(by["ems"]["overall"]), figures([20, 40, 80, 150]))
        self.assertEqual(pick(by["law"]["overall"]), figures([90, 300]))
        self.assertEqual([d["call_taker"] for d in by["fire"]["dispatchers"]],
                         ["DISPATCHER X", "DISPATCHER Y"])
        self.assertEqual(by["fire"]["overall"]["call_taker"], "All dispatchers")
        every = fire_x + fire_y
        self.assertEqual(iso["timed"], len(every))
        self.assertEqual(iso["fast_percent"], round(sum(v <= 64 for v in every) / len(every) * 100, 1))
        self.assertEqual(iso["meets_fast"], iso["fast_percent"] >= 90)
        self.assertEqual(iso["meets_slow"], iso["slow_percent"] >= 95)

        empty = dict(params,
                     window_start=datetime.now(timezone.utc) + timedelta(days=1),
                     window_end=datetime.now(timezone.utc) + timedelta(days=2))
        groups, iso = ar._discipline_metrics(repository, empty)
        self.assertEqual((iso["timed"], iso["meets_fast"]), (0, None))
        self.assertTrue(all(group["overall"] is None for group in groups))


if __name__ == "__main__":
    unittest.main()
