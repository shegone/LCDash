from contextlib import AbstractContextManager
from pathlib import Path

import psycopg

from app.config.settings import settings


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "analytics_schema.sql"
SYNC_STATE_KEY = "completed_calls_through"


class AnalyticsDatabaseError(Exception):
    """Raised when the LCDash analytics database is unavailable."""


def analytics_database_is_configured(database_url: str | None = None) -> bool:
    value = (database_url if database_url is not None else settings.database_url).strip()
    return bool(value and "change_me" not in value)


class AnalyticsRepository(AbstractContextManager):
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url if database_url is not None else settings.database_url
        self.connection = None

    def __enter__(self):
        if not analytics_database_is_configured(self.database_url):
            raise AnalyticsDatabaseError(
                "DATABASE_URL is not configured for LCDash analytics."
            )
        try:
            self.connection = psycopg.connect(self.database_url, connect_timeout=10)
            return self
        except psycopg.Error as exc:
            raise AnalyticsDatabaseError(
                "LCDash could not connect to the PostgreSQL analytics database."
            ) from exc

    def __exit__(self, exc_type, exc_value, traceback):
        if self.connection is not None:
            if exc_type is not None:
                self.connection.rollback()
            self.connection.close()
            self.connection = None
        return False

    def _execute(self, query: str, params=None):
        if self.connection is None:
            raise AnalyticsDatabaseError("Analytics repository is not connected.")
        try:
            return self.connection.execute(query, params)
        except psycopg.Error as exc:
            self.connection.rollback()
            raise AnalyticsDatabaseError(
                "A PostgreSQL analytics operation failed."
            ) from exc

    def _commit(self):
        if self.connection is not None:
            self.connection.commit()

    def initialize_schema(self):
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self._execute(schema_sql)
        self._commit()

    def get_sync_timestamp(self):
        row = self._execute(
            """
            SELECT state_timestamp
            FROM lcdash_analytics.sync_state
            WHERE state_key = %s
            """,
            (SYNC_STATE_KEY,),
        ).fetchone()
        return row[0] if row else None

    def set_sync_timestamp(self, timestamp):
        self._execute(
            """
            INSERT INTO lcdash_analytics.sync_state (
                state_key,
                state_timestamp,
                updated_at
            )
            VALUES (%s, %s, NOW())
            ON CONFLICT (state_key) DO UPDATE SET
                state_timestamp = EXCLUDED.state_timestamp,
                updated_at = NOW()
            """,
            (SYNC_STATE_KEY, timestamp),
        )
        self._commit()

    def start_sync_run(self, started_at, window_start, window_end) -> int:
        row = self._execute(
            """
            INSERT INTO lcdash_analytics.sync_runs (
                started_at,
                window_start,
                window_end,
                status
            )
            VALUES (%s, %s, %s, 'running')
            RETURNING run_id
            """,
            (started_at, window_start, window_end),
        ).fetchone()
        self._commit()
        return int(row[0])

    def complete_sync_run(
        self,
        run_id: int,
        completed_at,
        status: str,
        calls_discovered: int,
        calls_stored: int,
        analytics_failures: int,
        error_summary: str = "",
    ):
        self._execute(
            """
            UPDATE lcdash_analytics.sync_runs
            SET
                completed_at = %s,
                status = %s,
                calls_discovered = %s,
                calls_stored = %s,
                analytics_failures = %s,
                error_summary = %s
            WHERE run_id = %s
            """,
            (
                completed_at,
                status,
                calls_discovered,
                calls_stored,
                analytics_failures,
                error_summary[:1000],
                run_id,
            ),
        )
        self._commit()

    def upsert_bundle(self, bundle: dict):
        call = bundle["call"]
        self._execute(
            """
            INSERT INTO lcdash_analytics.calls (
                cfs_number,
                dispatch_agency,
                response_agency,
                incident_code,
                incident_description,
                priority,
                disposition_code,
                disposition_description,
                beat,
                zone,
                city,
                latitude,
                longitude,
                is_scheduled,
                incident_at,
                call_received_at,
                closed_at,
                source_collected_at
            )
            VALUES (
                %(cfs_number)s,
                %(dispatch_agency)s,
                %(response_agency)s,
                %(incident_code)s,
                %(incident_description)s,
                %(priority)s,
                %(disposition_code)s,
                %(disposition_description)s,
                %(beat)s,
                %(zone)s,
                %(city)s,
                %(latitude)s,
                %(longitude)s,
                %(is_scheduled)s,
                %(incident_at)s,
                %(call_received_at)s,
                %(closed_at)s,
                %(source_collected_at)s
            )
            ON CONFLICT (cfs_number) DO UPDATE SET
                dispatch_agency = EXCLUDED.dispatch_agency,
                response_agency = EXCLUDED.response_agency,
                incident_code = EXCLUDED.incident_code,
                incident_description = EXCLUDED.incident_description,
                priority = EXCLUDED.priority,
                disposition_code = EXCLUDED.disposition_code,
                disposition_description = EXCLUDED.disposition_description,
                beat = EXCLUDED.beat,
                zone = EXCLUDED.zone,
                city = EXCLUDED.city,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                is_scheduled = EXCLUDED.is_scheduled,
                incident_at = EXCLUDED.incident_at,
                call_received_at = EXCLUDED.call_received_at,
                closed_at = EXCLUDED.closed_at,
                source_collected_at = EXCLUDED.source_collected_at,
                updated_at = NOW()
            """,
            call,
        )

        self._execute(
            "DELETE FROM lcdash_analytics.call_agency_times WHERE cfs_number = %s",
            (call["cfs_number"],),
        )
        self._execute(
            "DELETE FROM lcdash_analytics.unit_responses WHERE cfs_number = %s",
            (call["cfs_number"],),
        )

        for row in bundle["call_times"]:
            self._execute(
                """
                INSERT INTO lcdash_analytics.call_agency_times (
                    cfs_number,
                    agency_ori,
                    dispatched_at,
                    enroute_at,
                    staged_at,
                    on_scene_at,
                    at_patient_at,
                    backup_enroute_at,
                    backup_arrived_at,
                    leaving_at,
                    transporting_at,
                    arrived_at,
                    available_at,
                    in_quarters_at
                )
                VALUES (
                    %(cfs_number)s,
                    %(agency_ori)s,
                    %(dispatched_at)s,
                    %(enroute_at)s,
                    %(staged_at)s,
                    %(on_scene_at)s,
                    %(at_patient_at)s,
                    %(backup_enroute_at)s,
                    %(backup_arrived_at)s,
                    %(leaving_at)s,
                    %(transporting_at)s,
                    %(arrived_at)s,
                    %(available_at)s,
                    %(in_quarters_at)s
                )
                """,
                row,
            )

        for row in bundle["unit_responses"]:
            self._execute(
                """
                INSERT INTO lcdash_analytics.unit_responses (
                    cfs_number,
                    unit_number,
                    unit_type,
                    station,
                    beat,
                    dispatched_at,
                    enroute_at,
                    staged_at,
                    on_scene_at,
                    at_patient_at,
                    backup_enroute_at,
                    backup_arrived_at,
                    leaving_at,
                    transporting_at,
                    arrived_at,
                    available_at,
                    in_quarters_at
                )
                VALUES (
                    %(cfs_number)s,
                    %(unit_number)s,
                    %(unit_type)s,
                    %(station)s,
                    %(beat)s,
                    %(dispatched_at)s,
                    %(enroute_at)s,
                    %(staged_at)s,
                    %(on_scene_at)s,
                    %(at_patient_at)s,
                    %(backup_enroute_at)s,
                    %(backup_arrived_at)s,
                    %(leaving_at)s,
                    %(transporting_at)s,
                    %(arrived_at)s,
                    %(available_at)s,
                    %(in_quarters_at)s
                )
                """,
                row,
            )

        for unit in bundle["units"]:
            self._execute(
                """
                INSERT INTO lcdash_analytics.units (
                    unit_number,
                    agency,
                    unit_type,
                    station,
                    last_seen_at
                )
                VALUES (
                    %(unit_number)s,
                    %(agency)s,
                    %(unit_type)s,
                    %(station)s,
                    %(last_seen_at)s
                )
                ON CONFLICT (unit_number) DO UPDATE SET
                    agency = CASE
                        WHEN EXCLUDED.agency <> '' THEN EXCLUDED.agency
                        ELSE lcdash_analytics.units.agency
                    END,
                    unit_type = CASE
                        WHEN EXCLUDED.unit_type <> '' THEN EXCLUDED.unit_type
                        ELSE lcdash_analytics.units.unit_type
                    END,
                    station = CASE
                        WHEN EXCLUDED.station <> '' THEN EXCLUDED.station
                        ELSE lcdash_analytics.units.station
                    END,
                    last_seen_at = EXCLUDED.last_seen_at,
                    updated_at = NOW()
                """,
                unit,
            )

        self._commit()

    def status(self) -> dict:
        calls = self._execute(
            "SELECT COUNT(*) FROM lcdash_analytics.calls"
        ).fetchone()[0]
        responses = self._execute(
            "SELECT COUNT(*) FROM lcdash_analytics.unit_responses"
        ).fetchone()[0]
        last_run = self._execute(
            """
            SELECT completed_at, status, calls_discovered, calls_stored, analytics_failures
            FROM lcdash_analytics.sync_runs
            ORDER BY run_id DESC
            LIMIT 1
            """
        ).fetchone()
        return {
            "configured": True,
            "connected": True,
            "calls_stored": int(calls),
            "unit_responses_stored": int(responses),
            "last_run": {
                "completed_at": last_run[0].isoformat() if last_run and last_run[0] else "",
                "status": last_run[1] if last_run else "",
                "calls_discovered": int(last_run[2]) if last_run else 0,
                "calls_stored": int(last_run[3]) if last_run else 0,
                "analytics_failures": int(last_run[4]) if last_run else 0,
            },
        }


def get_analytics_database_status() -> dict:
    if not analytics_database_is_configured():
        return {
            "configured": False,
            "connected": False,
            "calls_stored": 0,
            "unit_responses_stored": 0,
            "last_run": {},
            "message": "PostgreSQL analytics is not configured on this machine.",
        }

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            return repository.status()
    except AnalyticsDatabaseError:
        return {
            "configured": True,
            "connected": False,
            "calls_stored": 0,
            "unit_responses_stored": 0,
            "last_run": {},
            "message": "PostgreSQL analytics is configured but unavailable.",
        }
