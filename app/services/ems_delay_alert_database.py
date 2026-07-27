from contextlib import AbstractContextManager
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from app.config.settings import settings


class EMSDelayAlertDatabaseError(Exception):
    """Raised when EMS delayed-call alert state cannot be stored."""


class EMSDelayAlertRepository(AbstractContextManager):
    def __init__(self, database_url: str | None = None):
        self.database_url = (
            database_url
            if database_url is not None
            else settings.database_url
        )
        self.connection = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.connection is not None:
            if exc_type is not None:
                self.connection.rollback()
            self.connection.close()
            self.connection = None
        return False

    def _connect(self):
        if self.connection is not None:
            return
        if not self.database_url:
            raise EMSDelayAlertDatabaseError(
                "DATABASE_URL is not configured for EMS delay alerts."
            )
        try:
            self.connection = psycopg.connect(
                self.database_url,
                connect_timeout=10,
            )
        except psycopg.Error as exc:
            raise EMSDelayAlertDatabaseError(
                "LCDash could not connect to the EMS alert database."
            ) from exc

    def _execute(self, query: str, params=None):
        self._connect()
        try:
            return self.connection.execute(query, params)
        except psycopg.Error as exc:
            self.connection.rollback()
            raise EMSDelayAlertDatabaseError(
                "An EMS delayed-call database operation failed."
            ) from exc

    def _commit(self):
        self.connection.commit()

    def initialize_schema(self):
        self._execute(
            """
            CREATE SCHEMA IF NOT EXISTS lcdash_alerting;

            CREATE TABLE IF NOT EXISTS lcdash_alerting.ems_delay_alerts (
                cfs_number TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                reference_time TIMESTAMPTZ NOT NULL,
                eligible_at TIMESTAMPTZ NOT NULL,
                alert_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'waiting',
                last_observed_at TIMESTAMPTZ NOT NULL,
                last_notification_at TIMESTAMPTZ,
                next_notification_at TIMESTAMPTZ,
                resolved_at TIMESTAMPTZ,
                resolution_reason TEXT NOT NULL DEFAULT '',
                last_delivery_status TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS lcdash_alerting.ems_delay_attempts (
                attempt_id BIGSERIAL PRIMARY KEY,
                cfs_number TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                attempted_at TIMESTAMPTZ NOT NULL,
                delivery_mode TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                recipient_snapshot JSONB NOT NULL DEFAULT '[]'::JSONB,
                message TEXT NOT NULL DEFAULT '',
                error_summary TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_ems_delay_alerts_status_due
                ON lcdash_alerting.ems_delay_alerts (
                    status,
                    next_notification_at
                );

            CREATE INDEX IF NOT EXISTS idx_ems_delay_attempts_cfs
                ON lcdash_alerting.ems_delay_attempts (
                    cfs_number,
                    attempted_at DESC
                );
            """
        )
        self._commit()

    def observe_candidate(self, candidate: dict, observed_at: datetime) -> dict:
        row = self._execute(
            """
            INSERT INTO lcdash_alerting.ems_delay_alerts (
                cfs_number,
                alert_type,
                reference_time,
                eligible_at,
                status,
                last_observed_at,
                next_notification_at
            )
            VALUES (%s, %s, %s, %s, 'waiting', %s, %s)
            ON CONFLICT (cfs_number) DO UPDATE SET
                alert_type = EXCLUDED.alert_type,
                reference_time = EXCLUDED.reference_time,
                eligible_at = EXCLUDED.eligible_at,
                last_observed_at = EXCLUDED.last_observed_at,
                status = CASE
                    WHEN lcdash_alerting.ems_delay_alerts.status = 'resolved'
                    THEN 'waiting'
                    ELSE lcdash_alerting.ems_delay_alerts.status
                END,
                resolved_at = NULL,
                resolution_reason = '',
                updated_at = NOW()
            RETURNING alert_count, next_notification_at, status
            """,
            (
                candidate["cfs_number"],
                candidate["alert_type"],
                candidate["reference_time"],
                candidate["eligible_at"],
                observed_at,
                candidate["eligible_at"],
            ),
        ).fetchone()
        self._commit()
        return {
            "alert_count": int(row[0] or 0),
            "next_notification_at": row[1],
            "status": row[2],
        }

    def record_dry_run(
        self,
        candidate: dict,
        *,
        sequence_number: int,
        recipients: list[dict],
        message: str,
        observed_at: datetime,
        repeat_minutes: int,
    ):
        next_notification_at = observed_at + timedelta(
            minutes=repeat_minutes
        )
        self._execute(
            """
            UPDATE lcdash_alerting.ems_delay_alerts
            SET
                alert_count = %s,
                status = 'dry_run',
                last_observed_at = %s,
                last_notification_at = %s,
                next_notification_at = %s,
                last_delivery_status = 'dry_run',
                last_error = '',
                updated_at = NOW()
            WHERE cfs_number = %s
            """,
            (
                sequence_number,
                observed_at,
                observed_at,
                next_notification_at,
                candidate["cfs_number"],
            ),
        )
        self._execute(
            """
            INSERT INTO lcdash_alerting.ems_delay_attempts (
                cfs_number,
                sequence_number,
                attempted_at,
                delivery_mode,
                delivery_status,
                recipient_snapshot,
                message
            )
            VALUES (%s, %s, %s, 'dry_run', 'would_send', %s, %s)
            """,
            (
                candidate["cfs_number"],
                sequence_number,
                observed_at,
                Jsonb(recipients),
                message,
            ),
        )
        self._commit()

    def record_delivery_issue(
        self,
        candidate: dict,
        *,
        observed_at: datetime,
        issue: str,
        sequence_number: int | None = None,
        recipients: list[dict] | None = None,
        message: str = "",
    ):
        self._execute(
            """
            UPDATE lcdash_alerting.ems_delay_alerts
            SET
                status = 'error',
                last_observed_at = %s,
                last_delivery_status = 'not_sent',
                last_error = %s,
                next_notification_at = %s,
                updated_at = NOW()
            WHERE cfs_number = %s
            """,
            (
                observed_at,
                issue[:1000],
                observed_at + timedelta(minutes=5),
                candidate["cfs_number"],
            ),
        )
        if sequence_number is not None:
            self._execute(
                """
                INSERT INTO lcdash_alerting.ems_delay_attempts (
                    cfs_number,
                    sequence_number,
                    attempted_at,
                    delivery_mode,
                    delivery_status,
                    recipient_snapshot,
                    message,
                    error_summary
                )
                VALUES (%s, %s, %s, 'live', 'failed', %s, %s, %s)
                """,
                (
                    candidate["cfs_number"],
                    sequence_number,
                    observed_at,
                    Jsonb(recipients or []),
                    message,
                    issue[:1000],
                ),
            )
        self._commit()

    def record_live_delivery(
        self,
        candidate: dict,
        *,
        sequence_number: int,
        recipients: list[dict],
        message: str,
        observed_at: datetime,
        repeat_minutes: int,
        delivery_results: list[dict],
    ):
        successful_results = [
            result
            for result in delivery_results
            if result.get("delivery_status") == "sent"
        ]
        delivery_status = (
            "sent"
            if len(successful_results) == len(delivery_results)
            else "partial"
        )
        error_summary = "; ".join(
            result.get("error") or "Unknown paging failure"
            for result in delivery_results
            if result.get("delivery_status") != "sent"
        )
        next_notification_at = observed_at + timedelta(
            minutes=repeat_minutes
        )

        self._execute(
            """
            UPDATE lcdash_alerting.ems_delay_alerts
            SET
                alert_count = %s,
                status = 'live',
                last_observed_at = %s,
                last_notification_at = %s,
                next_notification_at = %s,
                last_delivery_status = %s,
                last_error = %s,
                updated_at = NOW()
            WHERE cfs_number = %s
            """,
            (
                sequence_number,
                observed_at,
                observed_at,
                next_notification_at,
                delivery_status,
                error_summary[:1000],
                candidate["cfs_number"],
            ),
        )
        self._execute(
            """
            INSERT INTO lcdash_alerting.ems_delay_attempts (
                cfs_number,
                sequence_number,
                attempted_at,
                delivery_mode,
                delivery_status,
                recipient_snapshot,
                message,
                error_summary
            )
            VALUES (%s, %s, %s, 'live', %s, %s, %s, %s)
            """,
            (
                candidate["cfs_number"],
                sequence_number,
                observed_at,
                delivery_status,
                Jsonb(
                    {
                        "recipients": recipients,
                        "delivery_results": delivery_results,
                    }
                ),
                message,
                error_summary[:1000],
            ),
        )
        self._commit()

    def resolve_alert(
        self,
        cfs_number: str,
        *,
        resolved_at: datetime,
        reason: str,
    ) -> bool:
        result = self._execute(
            """
            UPDATE lcdash_alerting.ems_delay_alerts
            SET
                status = 'resolved',
                resolved_at = %s,
                resolution_reason = %s,
                next_notification_at = NULL,
                updated_at = NOW()
            WHERE cfs_number = %s
              AND status <> 'resolved'
            """,
            (resolved_at, reason[:500], cfs_number),
        )
        self._commit()
        return result.rowcount > 0

    def resolve_missing_alerts(
        self,
        observed_cfs_numbers: set[str],
        *,
        resolved_at: datetime,
        reason: str,
    ) -> int:
        if observed_cfs_numbers:
            result = self._execute(
                """
                UPDATE lcdash_alerting.ems_delay_alerts
                SET
                    status = 'resolved',
                    resolved_at = %s,
                    resolution_reason = %s,
                    next_notification_at = NULL,
                    updated_at = NOW()
                WHERE status <> 'resolved'
                  AND NOT (cfs_number = ANY(%s))
                """,
                (
                    resolved_at,
                    reason[:500],
                    list(observed_cfs_numbers),
                ),
            )
        else:
            result = self._execute(
                """
                UPDATE lcdash_alerting.ems_delay_alerts
                SET
                    status = 'resolved',
                    resolved_at = %s,
                    resolution_reason = %s,
                    next_notification_at = NULL,
                    updated_at = NOW()
                WHERE status <> 'resolved'
                """,
                (resolved_at, reason[:500]),
            )
        self._commit()
        return result.rowcount
