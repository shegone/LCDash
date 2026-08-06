"""Tenant-safe, DB-first report planning and reusable template contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
import json
import re
import uuid


ALLOWED_METRICS = frozenset({
    "call_count", "average_response_seconds", "unit_commitment_minutes",
    "calls_by_nature", "calls_by_hour", "calls_by_agency",
})
ALLOWED_DIMENSIONS = frozenset({
    "day", "hour", "nature", "agency", "jurisdiction", "disposition",
})
ALLOWED_PERIODS = frozenset({"24h", "7d", "30d", "90d", "365d"})
ALLOWED_VISIBILITY = frozenset({"viewer", "dispatcher", "supervisor", "admin"})
_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'()/-]{2,99}$")


@dataclass(frozen=True, slots=True)
class ReportIntent:
    metric: str
    dimensions: tuple[str, ...]
    period: str
    current_cad_fallback: bool = False

    def __post_init__(self) -> None:
        if self.metric not in ALLOWED_METRICS:
            raise ValueError("Report metric is not allowlisted.")
        if self.period not in ALLOWED_PERIODS:
            raise ValueError("Report period is not allowlisted.")
        if not self.dimensions or any(item not in ALLOWED_DIMENSIONS for item in self.dimensions):
            raise ValueError("Report dimensions are not allowlisted.")
        if len(set(self.dimensions)) != len(self.dimensions) or len(self.dimensions) > 3:
            raise ValueError("Report dimensions must be unique and bounded.")


@dataclass(frozen=True, slots=True)
class ReportPreview:
    intent: ReportIntent
    rows: tuple[Mapping[str, Any], ...]
    source: str
    generated_at: str
    freshness_notice: str
    save_requires_user_action: bool = True
    export_requires_user_action: bool = True


class AggregateAnalyticsSource(Protocol):
    def query(self, tenant_id: str, intent: ReportIntent) -> tuple[Mapping[str, Any], ...]: ...


class CurrentCadAggregateSource(Protocol):
    def query_current(self, tenant_id: str, intent: ReportIntent) -> tuple[Mapping[str, Any], ...]: ...


def build_report_preview(*, tenant_id: str, intent: ReportIntent,
                         analytics: AggregateAnalyticsSource,
                         current_cad: CurrentCadAggregateSource | None = None) -> ReportPreview:
    rows = analytics.query(tenant_id, intent)
    source = "analytics-database"
    notice = "Historical analytics database; verify the displayed refresh time."
    if not rows and intent.current_cad_fallback and current_cad is not None:
        rows = current_cad.query_current(tenant_id, intent)
        source = "current-cad-read-only"
        notice = "Current read-only CAD aggregate; not a historical or authoritative report."
    return ReportPreview(
        intent=intent, rows=tuple(rows[:500]), source=source,
        generated_at=datetime.now(timezone.utc).isoformat(), freshness_notice=notice,
    )


@dataclass(frozen=True, slots=True)
class ReportTemplate:
    template_id: str
    tenant_id: str
    title: str
    intent: ReportIntent
    author_subject: str
    created_at: str
    version: int
    visible_to_roles: tuple[str, ...]


def create_report_template(*, tenant_id: str, title: str, intent: ReportIntent,
                           author_subject: str, visible_to_roles: tuple[str, ...]) -> ReportTemplate:
    if not _TITLE.fullmatch(title.strip()):
        raise ValueError("Template title is invalid.")
    roles = tuple(sorted(set(visible_to_roles)))
    if not roles or any(role not in ALLOWED_VISIBILITY for role in roles):
        raise ValueError("Template visibility contains an unauthorized role.")
    if not tenant_id or not author_subject:
        raise ValueError("Template requires trusted tenant and author identity.")
    return ReportTemplate(
        template_id=str(uuid.uuid4()), tenant_id=tenant_id, title=title.strip(),
        intent=intent, author_subject=author_subject,
        created_at=datetime.now(timezone.utc).isoformat(), version=1,
        visible_to_roles=roles,
    )


def safe_template_record(template: ReportTemplate) -> dict[str, Any]:
    """Persist definitions and audit-safe identity metadata, never report results."""
    record = asdict(template)
    if set(record) != {
        "template_id", "tenant_id", "title", "intent", "author_subject",
        "created_at", "version", "visible_to_roles",
    }:
        raise ValueError("Unsafe template fields are not permitted.")
    return record


def template_visible(template: ReportTemplate, *, tenant_id: str,
                     roles: frozenset[str]) -> bool:
    return template.tenant_id == tenant_id and bool(roles.intersection(template.visible_to_roles))


class PostgresReportTemplateStore:
    """Persist safe template definitions in the existing tenant analytics DB."""

    def __init__(self, database_url: str, *, connect=None) -> None:
        if not database_url.strip():
            raise ValueError("Report template storage requires the analytics database.")
        if connect is None:
            import psycopg
            connect = psycopg.connect
        self._database_url = database_url
        self._connect = connect

    def save(self, template: ReportTemplate) -> None:
        record = safe_template_record(template)
        with self._connect(self._database_url, connect_timeout=10) as connection:
            connection.execute(
                """INSERT INTO lcdash_analytics.report_templates
                   (template_id, tenant_id, title, metric, dimensions, period,
                    current_cad_fallback, author_subject, created_at, version,
                    visible_to_roles)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    record["template_id"], record["tenant_id"], record["title"],
                    record["intent"]["metric"], json.dumps(record["intent"]["dimensions"]),
                    record["intent"]["period"], record["intent"]["current_cad_fallback"],
                    record["author_subject"], record["created_at"], record["version"],
                    json.dumps(record["visible_to_roles"]),
                ),
            )

    def list_visible(self, *, tenant_id: str,
                     roles: frozenset[str]) -> tuple[ReportTemplate, ...]:
        with self._connect(self._database_url, connect_timeout=10) as connection:
            rows = connection.execute(
                """SELECT template_id, tenant_id, title, metric, dimensions, period,
                          current_cad_fallback, author_subject, created_at, version,
                          visible_to_roles
                   FROM lcdash_analytics.report_templates
                   WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 100""",
                (tenant_id,),
            ).fetchall()
        templates = tuple(ReportTemplate(
            template_id=str(row[0]), tenant_id=str(row[1]), title=str(row[2]),
            intent=ReportIntent(str(row[3]), tuple(row[4]), str(row[5]), bool(row[6])),
            author_subject=str(row[7]), created_at=row[8].isoformat(), version=int(row[9]),
            visible_to_roles=tuple(row[10]),
        ) for row in rows)
        return tuple(item for item in templates if template_visible(
            item, tenant_id=tenant_id, roles=roles
        ))
