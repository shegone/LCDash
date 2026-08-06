"""Allowlisted Phase 1 schema initializer for a future one-off ECS RunTask."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATHS = (
    PROJECT_ROOT / "database" / "analytics_schema.sql",
    PROJECT_ROOT / "database" / "knowledge_schema.sql",
)

APPROVED_OBJECTS = frozenset(
    {
        "lcdash_analytics.calls",
        "lcdash_analytics.units",
        "lcdash_analytics.call_agency_times",
        "lcdash_analytics.unit_responses",
        "lcdash_analytics.mae_interactions",
        "lcdash_analytics.mae_feedback",
        "lcdash_analytics.jack_interactions",
        "lcdash_analytics.jack_feedback",
        "lcdash_analytics.mae_evaluation_runs",
        "lcdash_analytics.jack_evaluation_runs",
        "lcdash_analytics.mae_memory",
        "lcdash_analytics.jack_memory",
        "lcdash_analytics.saved_analytics_widgets",
        "lcdash_analytics.unit_response_metrics",
        "lcdash_analytics.call_response_metrics",
        "lcdash_knowledge.documents",
        "lcdash_knowledge.chunks",
        "lcdash_knowledge.index_state",
        "lcdash_knowledge.library_index_state",
    }
)
APPROVED_SCHEMAS = frozenset({"lcdash_analytics", "lcdash_knowledge"})
DENIED_IDENTIFIERS = frozenset(
    {
        "lcdash_analytics.sync_state",
        "lcdash_analytics.sync_runs",
        "lcdash_realtime",
        "lcdash_alerting",
        "webhook_events",
        "ems_delay_alerts",
        "ems_delay_attempts",
    }
)
QUALIFIED_OBJECT_PATTERN = re.compile(
    r"\b(lcdash_(?:analytics|knowledge|realtime|alerting)\.[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)
SCHEMA_PATTERN = re.compile(
    r"^CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+(lcdash_[a-z_][a-z0-9_]*)$",
    re.IGNORECASE,
)


class Phase1SchemaContractError(RuntimeError):
    """Raised before execution when schema input leaves the approved boundary."""


def _split_sql(source: str) -> tuple[str, ...]:
    without_comments = re.sub(r"(?m)^\s*--.*$", "", source)
    return tuple(
        statement.strip()
        for statement in without_comments.split(";")
        if statement.strip()
    )


def approved_schema_statements() -> tuple[str, ...]:
    """Return only idempotent statements whose objects are explicitly approved."""
    approved: list[str] = []
    for path in SCHEMA_PATHS:
        for statement in _split_sql(path.read_text(encoding="utf-8")):
            lowered = statement.lower()
            if any(identifier in lowered for identifier in DENIED_IDENTIFIERS):
                continue

            schema_match = SCHEMA_PATTERN.fullmatch(statement)
            if schema_match:
                if schema_match.group(1).lower() not in APPROVED_SCHEMAS:
                    raise Phase1SchemaContractError("Unapproved schema declaration.")
                approved.append(statement)
                continue

            referenced_objects = {
                match.lower() for match in QUALIFIED_OBJECT_PATTERN.findall(statement)
            }
            if not referenced_objects:
                raise Phase1SchemaContractError(
                    f"Schema statement has no qualified approved object: {path.name}"
                )
            if not referenced_objects.issubset(APPROVED_OBJECTS):
                unexpected = sorted(referenced_objects - APPROVED_OBJECTS)
                raise Phase1SchemaContractError(
                    "Schema statement references unapproved objects: "
                    + ", ".join(unexpected)
                )
            approved.append(statement)

    if not approved:
        raise Phase1SchemaContractError("No approved Phase 1 schema statements found.")
    return tuple(approved)


def initialize_phase1_schema(
    database_url: str,
    *,
    connect: Callable | None = None,
) -> int:
    """Execute the validated allowlist in one transaction and return its size."""
    if not database_url.strip():
        raise Phase1SchemaContractError("Pilot database configuration is required.")
    statements = approved_schema_statements()
    if connect is None:
        import psycopg

        connect = psycopg.connect

    connection = connect(database_url, connect_timeout=10)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(statements)


def main() -> int:
    """Future RunTask entry point; intentionally not called by web startup."""
    try:
        from app.config.settings import settings

        count = initialize_phase1_schema(settings.database_url)
    except Exception as error:
        print(f"Phase 1 schema initialization failed: {type(error).__name__}.")
        return 1
    print(f"Phase 1 schema initialized with {count} allowlisted statements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
