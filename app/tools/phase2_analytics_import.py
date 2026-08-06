"""Allowlisted one-way importer for an authorized Phase 2 analytics snapshot.

This module does not discover tables, read credentials, create connections, or
write files. An authorized operator must provide already-open source and target
connections. Source access is forced into one repeatable-read, read-only
transaction before any SELECT is issued. Full approved analytics identifiers,
dispatcher fields, and coordinates are preserved for application parity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


FIELD_POLICY = "full-approved-operational-analytics-parity"
SENSITIVE_PARITY_FIELDS = (
    "cfs_number",
    "unit_number",
    "call_taker",
    "call_taker_unique_identifier",
    "latitude",
    "longitude",
)


CALL_FIELDS = (
    "cfs_number", "dispatch_agency", "response_agency", "call_taker",
    "call_taker_unique_identifier", "incident_code", "incident_description",
    "priority", "disposition_code", "disposition_description", "beat", "zone",
    "city", "latitude", "longitude", "is_scheduled", "incident_at",
    "call_received_at", "closed_at", "source_collected_at",
)
UNIT_FIELDS = (
    "unit_number", "agency", "unit_type", "station", "last_seen_at",
)
AGENCY_TIME_FIELDS = (
    "cfs_number", "agency_ori", "dispatched_at", "enroute_at", "staged_at",
    "on_scene_at", "at_patient_at", "backup_enroute_at", "backup_arrived_at",
    "leaving_at", "transporting_at", "arrived_at", "available_at",
    "in_quarters_at",
)
UNIT_RESPONSE_FIELDS = (
    "cfs_number", "unit_number", "unit_type", "station", "beat",
    "dispatched_at", "enroute_at", "staged_at", "on_scene_at",
    "at_patient_at", "backup_enroute_at", "backup_arrived_at", "leaving_at",
    "transporting_at", "arrived_at", "available_at", "in_quarters_at",
)
SAVED_WIDGET_FIELDS = (
    "widget_id", "created_at", "updated_at", "created_by", "title",
    "view_key", "status",
)


@dataclass(frozen=True, slots=True)
class TablePlan:
    name: str
    fields: tuple[str, ...]
    key_fields: tuple[str, ...]
    source_sql: str

    @property
    def target(self) -> str:
        return f"lcdash_analytics.{self.name}"

    @property
    def upsert_sql(self) -> str:
        columns = ", ".join(self.fields)
        placeholders = ", ".join(f"%({field})s" for field in self.fields)
        conflict = ", ".join(self.key_fields)
        updates = ", ".join(
            f"{field} = EXCLUDED.{field}"
            for field in self.fields
            if field not in self.key_fields
        )
        return (
            f"INSERT INTO {self.target} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        )


def _projection(alias: str, fields: tuple[str, ...]) -> str:
    return ", ".join(f"{alias}.{field}" for field in fields)


TABLE_PLANS = (
    TablePlan(
        "calls",
        CALL_FIELDS,
        ("cfs_number",),
        f"SELECT {_projection('c', CALL_FIELDS)} FROM lcdash_analytics.calls c "
        "WHERE c.source_collected_at >= %(window_start)s "
        "AND c.source_collected_at < %(window_end)s ORDER BY c.cfs_number",
    ),
    TablePlan(
        "units",
        UNIT_FIELDS,
        ("unit_number",),
        f"SELECT DISTINCT {_projection('u', UNIT_FIELDS)} "
        "FROM lcdash_analytics.units u "
        "JOIN lcdash_analytics.unit_responses r ON r.unit_number = u.unit_number "
        "JOIN lcdash_analytics.calls c ON c.cfs_number = r.cfs_number "
        "WHERE c.source_collected_at >= %(window_start)s "
        "AND c.source_collected_at < %(window_end)s ORDER BY u.unit_number",
    ),
    TablePlan(
        "call_agency_times",
        AGENCY_TIME_FIELDS,
        ("cfs_number", "agency_ori"),
        f"SELECT {_projection('a', AGENCY_TIME_FIELDS)} "
        "FROM lcdash_analytics.call_agency_times a "
        "JOIN lcdash_analytics.calls c ON c.cfs_number = a.cfs_number "
        "WHERE c.source_collected_at >= %(window_start)s "
        "AND c.source_collected_at < %(window_end)s "
        "ORDER BY a.cfs_number, a.agency_ori",
    ),
    TablePlan(
        "unit_responses",
        UNIT_RESPONSE_FIELDS,
        ("cfs_number", "unit_number"),
        f"SELECT {_projection('r', UNIT_RESPONSE_FIELDS)} "
        "FROM lcdash_analytics.unit_responses r "
        "JOIN lcdash_analytics.calls c ON c.cfs_number = r.cfs_number "
        "WHERE c.source_collected_at >= %(window_start)s "
        "AND c.source_collected_at < %(window_end)s "
        "ORDER BY r.cfs_number, r.unit_number",
    ),
    TablePlan(
        "saved_analytics_widgets",
        SAVED_WIDGET_FIELDS,
        ("widget_id",),
        f"SELECT {_projection('w', SAVED_WIDGET_FIELDS)} "
        "FROM lcdash_analytics.saved_analytics_widgets w ORDER BY w.widget_id",
    ),
)


def validate_row(plan: TablePlan, row: Mapping[str, object]) -> dict:
    """Copy only the exact approved field map before target transmission."""
    if set(row) != set(plan.fields):
        raise ValueError(f"Unexpected source field set for {plan.name}.")
    return {field: row[field] for field in plan.fields}


def import_window(
    source_connection,
    target_connection,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    """Import one authorized window and return counts without row content."""
    if window_start >= window_end:
        raise ValueError("Migration window must have a positive duration.")
    counts: dict[str, int] = {}
    source = source_connection.cursor()
    target = target_connection.cursor()
    try:
        source.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for plan in TABLE_PLANS:
            source.execute(
                plan.source_sql,
                {"window_start": window_start, "window_end": window_end},
            )
            rows = [validate_row(plan, row) for row in source]
            if rows:
                target.executemany(plan.upsert_sql, rows)
            counts[plan.target] = len(rows)
        target_connection.commit()
        source_connection.rollback()
    except Exception:
        target_connection.rollback()
        source_connection.rollback()
        raise
    finally:
        source.close()
        target.close()
    return counts
