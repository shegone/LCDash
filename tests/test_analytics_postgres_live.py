"""The analytics dispatcher SQL against a real PostgreSQL.

Skipped unless LCDASH_TEST_PG_DSN names a server that may create and drop
databases, for example "host=127.0.0.1 port=55432 user=postgres". Run against
PostgreSQL 17.6 on 2026-10-21 and 17.11 on 2026-11-06 (production is RDS 17).
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
        add("JONATHAN ISOM", "id-isom", [("FC 1", 30)])          # excluded (supervisor)
        add("Pam Sheppard ", "id-pam", [("FC 1", 30)])           # excluded (office), any case
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
        listed = [d["call_taker"] for d in overview["dispatchers"]]
        for name in ("KIM MAYNARD", "JONATHAN ISOM", "Pam Sheppard "):
            self.assertNotIn(name, listed)
        self.assertEqual(overview["dispatcher_metrics"]["calls_with_call_taker"], len(seconds) + 3 + 1 + 1 + 4)
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
        add("DISPATCHER X", "id-x", [("FC 100", 50), ("LEASA", 150)])     # counts in both, timed 50 in both
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
            "excluded_dispatcher_identities": list(ar.EXCLUDED_DISPATCHER_IDENTITIES),
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
        # The joint call's EMS unit came 150 s in, but the fire unit at 50 s
        # was the first unit assigned, so EMS is timed at 50 too.
        self.assertEqual(pick(by["ems"]["overall"]), figures([20, 40, 80, 50]))
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

    def test_first_unit_action_reproduces_the_cad_timelines(self):
        """Synthetic versions of the four CFS timelines the audit reconstructed
        (docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md): offsets in
        seconds from the received time, unit numbers invented."""
        repository = self.repository("lcdash_an_live_c")
        now = datetime.now(timezone.utc)
        base = now - timedelta(hours=10)
        received_by = {}

        def call(cfs, minute, *, scheduled=False, agency="LCSO", desc="Synthetic"):
            received = base + timedelta(minutes=minute)
            received_by[cfs] = received
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.calls (cfs_number, call_taker, "
                "call_taker_unique_identifier, call_received_at, is_scheduled, "
                "incident_description, incident_code, priority, dispatch_agency, "
                "response_agency, source_collected_at) "
                "VALUES (%s,'DISPATCHER T','id-t',%s,%s,%s,'X','2','LCEOC',%s,%s)",
                (cfs, received, scheduled, desc, agency, now))

        def at(cfs, seconds):
            return None if seconds is None else received_by[cfs] + timedelta(seconds=seconds)

        def unit(cfs, number, *, dispatched=None, enroute=None, staged=None, on_scene=None):
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.unit_responses (cfs_number, unit_number, "
                "dispatched_at, enroute_at, staged_at, on_scene_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (cfs, number, at(cfs, dispatched), at(cfs, enroute), at(cfs, staged),
                 at(cfs, on_scene)))

        def agency(cfs, ori, *, dispatched=None, enroute=None, on_scene=None):
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.call_agency_times (cfs_number, agency_ori, "
                "dispatched_at, enroute_at, on_scene_at) VALUES (%s,%s,%s,%s,%s)",
                (cfs, ori, at(cfs, dispatched), at(cfs, enroute), at(cfs, on_scene)))

        order = {}

        def log(cfs, number, kind, seconds, description=""):
            order[cfs] = order.get(cfs, -1) + 1
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.unit_status_events (cfs_number, event_order, "
                "unit_number, status_code, status_description, event_kind, occurred_at) "
                "VALUES (%s,%s,%s,'',%s,%s,%s)",
                (cfs, order[cfs], number, description or kind, kind, at(cfs, seconds)))

        def roster(number, agency_code, unit_type=""):
            repository.connection.execute(
                "INSERT INTO lcdash_analytics.units (unit_number, agency, unit_type, "
                "station, last_seen_at) VALUES (%s,%s,%s,'',%s)",
                (number, agency_code, unit_type, now))

        for number, code, kind in (("E1", "FC 800", "FIRE ENGINE"), ("E2", "FC 800", "FIRE ENGINE"),
                                   ("F1", "DOF", ""), ("S1", "LCSO", "PATROL CAR"),
                                   ("S2", "LCSO", "PATROL CAR"), ("S3", "LCSO", "PATROL CAR"),
                                   ("M1", "LEASA", "EMS AMBULANCE")):
            roster(number, code, kind)

        # Multi-unit fire call: the fire unit's Assign at 1:58 is the first
        # action; a forestry unit's Enroute at 43:29 was all the per-agency
        # record offered.
        call("C-FIRE", 10, agency="FC 800")
        unit("C-FIRE", "E1", dispatched=118, enroute=684)
        unit("C-FIRE", "E2", enroute=450, on_scene=1235)
        unit("C-FIRE", "F1", enroute=2609, on_scene=4158)
        unit("C-FIRE", "S1", on_scene=4962)            # stacked, then on scene
        agency("C-FIRE", "WVDOF", dispatched=2609, enroute=2609, on_scene=4158)
        log("C-FIRE", "E1", "assigned", 118, "Assign")   # same event as the unit stamp
        log("C-FIRE", "S1", "stacked", 1500, "Stack")    # a later Stack changes nothing
        log("C-FIRE", "", "", 60, "Narrative")            # a status outside the five kinds
        # Stack-then-enroute: Stack at 2:17 from the command log, Enroute at
        # 20:59, Assign entered at 47:00 after the unit was on scene.
        call("C-STACK", 20)
        unit("C-STACK", "S2", enroute=1259, on_scene=2039, dispatched=2821)
        agency("C-STACK", "WVLCSO", dispatched=2821, enroute=1259, on_scene=2039)
        log("C-STACK", "S2", "stacked", 137, "Stack")
        log("C-STACK", "S2", "enroute", 1259, "Enroute")
        # Held unit: stacked at 7:59, assigned at 54:29. The hold is the unit's.
        call("C-HELD", 30)
        unit("C-HELD", "S3", dispatched=3269)
        agency("C-HELD", "WVLCSO", dispatched=3269)
        log("C-HELD", "S3", "stacked", 479, "Stack")
        log("C-HELD", "S3", "assigned", 3269, "Assign")
        # Re-assign after the first leg was complete: Enroute 1:39, Assign 7:12.
        call("C-REASSIGN", 40)
        unit("C-REASSIGN", "S1", enroute=99, dispatched=432)
        agency("C-REASSIGN", "WVLCSO", dispatched=432, enroute=99)
        # Over the cap, no stamp, scheduled, and a self-initiated zero.
        call("C-OVERCAP", 50)
        unit("C-OVERCAP", "S2", dispatched=5400)
        call("C-NOSTAMP", 60)
        unit("C-NOSTAMP", "S3", on_scene=-30)
        call("C-SCHED", 70, scheduled=True, agency="LEASA")
        unit("C-SCHED", "M1", dispatched=10, on_scene=600)
        call("C-ZERO", 80)
        unit("C-ZERO", "S1", dispatched=0)
        # Two of the call types that are never timed: one with a late unit
        # (not over the cap: the type decides first), one with no unit at
        # all (not a no-stamp), spelt with CAD's own punctuation.
        call("C-INFO", 90, desc="Information")
        unit("C-INFO", "S1", dispatched=3000)
        call("C-HANGUP", 95, desc="911 HANG-UP")
        repository.connection.commit()

        window = ar.resolve_analytics_window(period="24h")
        overview = ar._query_overview(repository, window, None)
        row = next(d for d in overview["dispatchers"] if d["call_taker"] == "DISPATCHER T")
        self.assertEqual(row["calls_entered"], 10)
        self.assertEqual(row["processing_samples"], 5)
        self.assertEqual((row["excluded_scheduled"], row["excluded_no_stamp"],
                          row["excluded_over_cap"], row["excluded_type"]), (1, 1, 1, 2))
        # 1:58 / 2:17 / 7:59 / 1:39: the CAD report's own figures.
        timed = [118, 137, 479, 99, 0]
        self.assertEqual(row["median_processing"], "1:58")
        self.assertEqual(row["average_processing"], ar._format_duration(statistics.mean(timed)))
        self.assertEqual(row["over_180_count"], 1)
        # NFPA 1225 marks: 0 and 1:39 are within 106 s; only the
        # self-initiated zero is within 64 s.
        self.assertEqual(row["within_slow_percent"], 40)
        self.assertEqual(row["within_fast_percent"], 20)
        # The two stacked calls waited 46:30 and 18:42 on their units.
        self.assertEqual((row["stacked_calls"], row["average_hold"]),
                         (2, ar._format_duration((3269 - 479 + 1259 - 137) / 2)))
        summary = overview["dispatcher_metrics"]
        self.assertEqual((summary["processing_samples"], summary["over_180_count"],
                          summary["excluded_over_cap"], summary["processing_cap_minutes"],
                          summary["stacked_calls"], summary["average_hold"],
                          summary["excluded_type"]),
                         (5, 1, 1, 60, 2, "32:36", 2))

        slow = ar._query_slow_calls(repository, window, row["dispatcher_ref"])
        self.assertEqual([(c["cfs_number"], c["processing_seconds"], c["first_action"],
                           c["first_action_by"], c["agency"], c["hold"]) for c in slow["calls"]],
                         [("C-HELD", 479, "Stacked", "S3", "LCSO", "46:30")])
        self.assertEqual(slow["total"], 1)
        self.assertEqual([(c["cfs_number"], c["processing_seconds"]) for c in slow["over_cap"]["calls"]],
                         [("C-OVERCAP", 5400)])
        self.assertEqual(slow["over_cap"]["total"], 1)

        # Response time: earliest on-scene from either record, scheduled left out.
        metrics = overview["metrics"]
        self.assertEqual(metrics["response_samples"], 2)
        self.assertEqual(metrics["response_eligible_calls"], 9)
        self.assertEqual(metrics["response_coverage_percent"], 22)
        self.assertEqual(metrics["average_response"], ar._format_duration((1235 + 2039) / 2))
        self.assertEqual(metrics["average_processing"], ar._format_duration(statistics.mean(timed)))
        m1 = next(u for u in overview["busiest_units"] if u["unit_number"] == "M1")
        self.assertEqual((m1["responses"], m1["average_response"]), (1, "—"))

        # Disciplines from the unit roster: fire and law both count the fire
        # call, and both time it to the engine's Assign at 1:58, the first
        # unit assigned; the deputy's Stack at 25:00 came later and is not
        # law's clock (the deputy was added to the call after it was already
        # dispatched). The forestry unit is unclassified.
        groups, iso = ar._discipline_metrics(repository, {
            "window_start": window.start_at, "window_end": window.end_at,
            "excluded_dispatcher_identities": list(ar.EXCLUDED_DISPATCHER_IDENTITIES)})
        by = {group["key"]: group for group in groups}
        self.assertEqual((by["fire"]["overall"]["timed"], by["fire"]["overall"]["median"]),
                         (1, "1:58"))
        self.assertEqual((by["law"]["overall"]["timed"], by["law"]["overall"]["over_180_count"]),
                         (5, 1))
        self.assertIsNone(by["ems"]["overall"])
        self.assertEqual(iso["timed"], 1)

        # The discipline filter on the dispatcher section: law keeps the
        # calls a sheriff's unit acted on (the fire call too, still timed to
        # the engine's Assign at 1:58, so it is no longer stacked or over
        # three minutes) and drops the scheduled EMS call and the hang-up;
        # fire keeps the fire call alone; EMS keeps only the scheduled
        # transport.
        law = next(d for d in ar._query_overview(repository, window, None, "law")["dispatchers"]
                   if d["call_taker"] == "DISPATCHER T")
        self.assertEqual(law["calls_entered"], 8)
        self.assertEqual(law["processing_samples"], 5)
        self.assertEqual(law["median_processing"], "1:58")
        self.assertEqual(law["over_180_count"], 1)
        self.assertEqual((law["excluded_scheduled"], law["excluded_no_stamp"],
                          law["excluded_over_cap"], law["excluded_type"]), (0, 1, 1, 1))
        self.assertEqual(law["stacked_calls"], 2)
        fire = next(d for d in ar._query_overview(repository, window, None, "fire")["dispatchers"]
                    if d["call_taker"] == "DISPATCHER T")
        self.assertEqual((fire["calls_entered"], fire["processing_samples"],
                          fire["median_processing"]), (1, 1, "1:58"))
        ems = next(d for d in ar._query_overview(repository, window, None, "ems")["dispatchers"]
                   if d["call_taker"] == "DISPATCHER T")
        self.assertEqual((ems["calls_entered"], ems["processing_samples"],
                          ems["excluded_scheduled"]), (1, 0, 1))
        law_slow = ar._query_slow_calls(repository, window, row["dispatcher_ref"], "law")
        self.assertEqual([(c["cfs_number"], c["processing_seconds"], c["first_action_by"])
                          for c in law_slow["calls"]],
                         [("C-HELD", 479, "S3")])
        # Under the law filter the fire call is timed to E1's Assign (1:58),
        # so it is not in law's "> 3 Min" list.
        self.assertEqual(law_slow["total"], 1)


if __name__ == "__main__":
    unittest.main()
