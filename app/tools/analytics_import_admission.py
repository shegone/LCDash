"""Manifest-only admission gate for a future one-way analytics import."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping

from app.tools.phase2_analytics_import import (
    FIELD_POLICY,
    SENSITIVE_PARITY_FIELDS,
    TABLE_PLANS,
)


APPROVED_TABLES = tuple(plan.target for plan in TABLE_PLANS)
APPROVED_FIELDS = {plan.target: plan.fields for plan in TABLE_PLANS}
APPROVED_KEYS = {plan.target: plan.key_fields for plan in TABLE_PLANS}
APPROVED_FIELD_POLICY = FIELD_POLICY
APPROVED_SENSITIVE_PARITY_FIELDS = SENSITIVE_PARITY_FIELDS
APPROVED_DERIVED_VIEWS = (
    "lcdash_analytics.call_response_metrics",
    "lcdash_analytics.unit_response_metrics",
)
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{5,255}$")
TARGET_ROLE = re.compile(
    r"^arn:aws:iam::862772137583:role/[A-Za-z0-9+=,.@_-]{1,128}$"
)
PROHIBITED_TERMS = (
    "credential", "password", "secret", "token", "api_key", "api-key",
    "private_key", "private-key", "backup", "database_dump", "recovery_bundle",
    "binary", "executable", "firmware", "model_file", "model-file",
    "operational_output", "operational-output", "control_record", "control-record",
    "raw_cad_payload", "raw-cad-payload", "recording", "audio", "transcript",
    "webhook_body", "webhook-body", "station_alert", "station-alert",
    "public_warning", "public-warning", "acknowledgement", "dispatch_command",
    "paging", "esinet", "radio_command",
)


class AnalyticsAdmissionError(ValueError):
    """Raised for an unsafe or malformed admission manifest."""


@dataclass(frozen=True, slots=True)
class TableMigrationPlan:
    logical_table: str
    fields: tuple[str, ...]
    key_fields: tuple[str, ...]
    expected_rows: int
    source_checksum_sha256: str
    operation: str = "idempotent-upsert"


@dataclass(frozen=True, slots=True)
class AnalyticsAdmissionReport:
    manifest_id: str
    valid: bool
    errors: tuple[str, ...]
    dry_run_migration_plan: tuple[TableMigrationPlan, ...]
    post_import_parity_checklist: tuple[str, ...]
    execution_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dry_run_migration_plan"] = [
            asdict(item) for item in self.dry_run_migration_plan
        ]
        return payload


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_utc(value: object, field: str, errors: list[str]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError
        return parsed
    except ValueError:
        errors.append(f"{field} must be an ISO-8601 UTC timestamp")
        return None


def _contains_prohibited(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            any(term in str(key).lower() for term in PROHIBITED_TERMS)
            or _contains_prohibited(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_prohibited(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        return normalized in {term.replace("-", "_") for term in PROHIBITED_TERMS}
    return False


def _closed_object(
    value: object,
    *,
    name: str,
    allowed: set[str],
    errors: list[str],
) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    unknown = set(value) - allowed
    if unknown:
        errors.append(f"{name} contains unknown fields")
    return value


def evaluate_analytics_import_admission(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> AnalyticsAdmissionReport:
    """Evaluate one explicit manifest without reading an export or connecting."""
    root = Path(repository_root).resolve(strict=True)
    path = Path(manifest_path).resolve(strict=True)
    if not _inside(root, path) or path.suffix.lower() != ".json":
        raise AnalyticsAdmissionError("manifest must be a JSON file inside LCDash-AWS")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AnalyticsAdmissionError("manifest must be a JSON object")
    if _contains_prohibited(payload):
        raise AnalyticsAdmissionError("manifest contains a prohibited field category")
    allowed_top = {
        "schema_version", "manifest_id", "source", "target", "window",
        "tables", "derived_views", "export_evidence",
    }
    if set(payload) - allowed_top:
        raise AnalyticsAdmissionError("manifest contains unknown top-level fields")

    errors: list[str] = []
    manifest_id = str(payload.get("manifest_id", ""))
    if payload.get("schema_version") != "lcdash.analytics-import-admission.v1":
        errors.append("unsupported schema_version")
    if not SAFE_REFERENCE.fullmatch(manifest_id):
        errors.append("manifest_id is invalid")

    source = _closed_object(
        payload.get("source"),
        name="source",
        allowed={"mode", "transaction", "identity_reference", "authoritative", "preservation_required"},
        errors=errors,
    )
    if source.get("mode") != "database-enforced-read-only":
        errors.append("source mode must be database-enforced-read-only")
    if source.get("transaction") != "repeatable-read-read-only":
        errors.append("source transaction evidence must be repeatable-read-read-only")
    if source.get("authoritative") is not True or source.get("preservation_required") is not True:
        errors.append("source authority and preservation evidence are required")
    if not SAFE_REFERENCE.fullmatch(str(source.get("identity_reference", ""))):
        errors.append("non-secret source identity reference is required")

    target = _closed_object(
        payload.get("target"),
        name="target",
        allowed={"account_id", "region", "tenant_id", "database", "schema", "identity_arn", "encrypted", "tls_required"},
        errors=errors,
    )
    expected_target = {
        "account_id": "862772137583",
        "region": "us-east-1",
        "tenant_id": "logan-synthetic",
        "database": "lcdash",
        "schema": "lcdash_analytics",
    }
    for key, expected in expected_target.items():
        if target.get(key) != expected:
            errors.append(f"target {key} does not match the approved identity")
    if not TARGET_ROLE.fullmatch(str(target.get("identity_arn", ""))):
        errors.append("target identity ARN is invalid")
    if target.get("encrypted") is not True or target.get("tls_required") is not True:
        errors.append("target encryption and TLS evidence are required")

    window = _closed_object(
        payload.get("window"),
        name="window",
        allowed={"start", "end", "consistent_watermark", "timezone"},
        errors=errors,
    )
    start = _parse_utc(window.get("start"), "window start", errors)
    end = _parse_utc(window.get("end"), "window end", errors)
    watermark = _parse_utc(window.get("consistent_watermark"), "consistent watermark", errors)
    if window.get("timezone") != "UTC":
        errors.append("window timezone must be UTC")
    if start and end and start >= end:
        errors.append("migration window must have positive duration")
    if start and end and watermark and not start <= watermark <= end:
        errors.append("consistent watermark must fall inside the migration window")

    evidence = _closed_object(
        payload.get("export_evidence"),
        name="export_evidence",
        allowed={"manifest_checksum_sha256", "generated_at", "generator_reference", "rejected_row_count"},
        errors=errors,
    )
    if not SHA256.fullmatch(str(evidence.get("manifest_checksum_sha256", ""))):
        errors.append("export manifest checksum is invalid")
    _parse_utc(evidence.get("generated_at"), "export generated_at", errors)
    if not SAFE_REFERENCE.fullmatch(str(evidence.get("generator_reference", ""))):
        errors.append("non-secret export generator reference is required")
    rejected = evidence.get("rejected_row_count")
    if not isinstance(rejected, int) or isinstance(rejected, bool) or rejected < 0:
        errors.append("rejected_row_count must be a nonnegative integer")

    tables = payload.get("tables")
    if not isinstance(tables, list):
        tables = []
        errors.append("tables must be a list")
    table_plans: list[TableMigrationPlan] = []
    observed_tables: set[str] = set()
    for index, item in enumerate(tables):
        label = f"tables[{index}]"
        table = _closed_object(
            item,
            name=label,
            allowed={"name", "fields", "key_fields", "row_count", "primary_key_distinct_count", "checksum_sha256", "minimum_timestamp", "maximum_timestamp"},
            errors=errors,
        )
        name = str(table.get("name", ""))
        if name not in APPROVED_TABLES:
            errors.append(f"{label} is outside the approved table allowlist")
            continue
        if name in observed_tables:
            errors.append(f"{label} duplicates an approved table")
            continue
        observed_tables.add(name)
        if tuple(table.get("fields", ())) != APPROVED_FIELDS[name]:
            errors.append(f"{label} fields do not match the exact approved projection")
        if tuple(table.get("key_fields", ())) != APPROVED_KEYS[name]:
            errors.append(f"{label} key fields do not match the approved identity")
        row_count = table.get("row_count")
        distinct = table.get("primary_key_distinct_count")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            errors.append(f"{label} row_count must be nonnegative")
        if distinct != row_count:
            errors.append(f"{label} primary-key distinct count must equal row count")
        checksum = str(table.get("checksum_sha256", ""))
        if not SHA256.fullmatch(checksum):
            errors.append(f"{label} checksum is invalid")
        for bound in ("minimum_timestamp", "maximum_timestamp"):
            _parse_utc(table.get(bound), f"{label} {bound}", errors)
        if (
            isinstance(row_count, int)
            and not isinstance(row_count, bool)
            and row_count >= 0
            and SHA256.fullmatch(checksum)
        ):
            table_plans.append(
                TableMigrationPlan(
                    name,
                    APPROVED_FIELDS[name],
                    APPROVED_KEYS[name],
                    row_count,
                    checksum,
                )
            )
    if observed_tables != set(APPROVED_TABLES):
        errors.append("manifest must include every approved table exactly once")

    views = payload.get("derived_views")
    if not isinstance(views, list):
        views = []
        errors.append("derived_views must be a list")
    observed_views: set[str] = set()
    for index, item in enumerate(views):
        label = f"derived_views[{index}]"
        view = _closed_object(
            item,
            name=label,
            allowed={"name", "mode", "definition_checksum_sha256"},
            errors=errors,
        )
        name = str(view.get("name", ""))
        if name not in APPROVED_DERIVED_VIEWS:
            errors.append(f"{label} is outside the approved view allowlist")
            continue
        observed_views.add(name)
        if view.get("mode") != "derive-on-target-not-copied":
            errors.append(f"{label} must be derived on target")
        if not SHA256.fullmatch(str(view.get("definition_checksum_sha256", ""))):
            errors.append(f"{label} definition checksum is invalid")
    if observed_views != set(APPROVED_DERIVED_VIEWS) or len(views) != len(APPROVED_DERIVED_VIEWS):
        errors.append("manifest must include each approved derived view exactly once")

    valid = not errors and len(table_plans) == len(APPROVED_TABLES)
    checklist = (
        "Recount every target table and match the admitted source row count.",
        "Match primary-key distinct counts and prove duplicate-key counts are zero.",
        "Recompute deterministic target checksums using the admitted field order.",
        "Compare per-table minimum and maximum timestamps to the admitted window.",
        "Prove calls-to-agency-times and calls-to-unit-responses orphan counts are zero.",
        "Rebuild both approved metric views and verify their definition checksums.",
        "Compare final source watermark to target maximum source_collected_at and accepted lag.",
        "Record sanitized rejected-row count and reason classes without row content.",
        "Confirm the source remained read-only and authoritative throughout the run.",
        "Keep CAD writes, webhooks, acknowledgements, paging, alerts, and outputs disabled.",
    )
    return AnalyticsAdmissionReport(
        manifest_id=manifest_id,
        valid=valid,
        errors=tuple(errors),
        dry_run_migration_plan=tuple(table_plans) if valid else (),
        post_import_parity_checklist=checklist,
    )
