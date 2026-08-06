"""Cloud DB-first analytics retrieval with a narrow, read-only CAD fallback seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


CAD_FALLBACK_FIELDS = frozenset(
    {
        "cfs_number", "incident_code", "incident_description", "priority",
        "agency", "status", "call_datetime", "location_label",
        "assigned_units", "unit_number", "unit_type", "station",
        "assignment_cfs_number",
    }
)
CAD_FALLBACK_OPERATIONS = frozenset({"search_calls", "get_call", "search_units"})
HISTORICAL_QUERY_KINDS = frozenset({"historical", "trend", "report", "backfill"})


class AnalyticsDatabaseReader(Protocol):
    def read(self, tenant_id: str, query_kind: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CurrentCadReader(Protocol):
    def read_current(
        self, tenant_id: str, operation: str, parameters: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AnalyticsSourceResult:
    data: tuple[Mapping[str, Any], ...]
    source: str
    freshness: str
    observed_at: str
    fallback_used: bool
    denial: str = ""


def _rows(value: object) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for row in value if isinstance(row, Mapping)) if isinstance(value, (list, tuple)) else ()


def _freshness(snapshot: Mapping[str, Any]) -> str:
    label = str(snapshot.get("freshness") or "unknown")
    return label if label in {"current", "stale", "empty", "unknown"} else "unknown"


def retrieve_cloud_analytics(
    *,
    tenant_id: str,
    query_kind: str,
    parameters: Mapping[str, Any],
    database: AnalyticsDatabaseReader,
    cad: CurrentCadReader | None = None,
    cad_operation: str = "",
    current_answer_required: bool = False,
    timeout_seconds: float = 3.0,
) -> AnalyticsSourceResult:
    """Read the tenant database first and consult CAD only for a current answer."""
    if not tenant_id or not query_kind:
        raise ValueError("Trusted tenant and query kind are required.")
    if not 0.1 <= timeout_seconds <= 5.0:
        raise ValueError("CAD fallback timeout must be between 0.1 and 5 seconds.")

    stored = database.read(tenant_id, query_kind, dict(parameters))
    if str(stored.get("tenant_id") or "") != tenant_id:
        return AnalyticsSourceResult((), "cloud_database", "unknown", "", False, "tenant_mismatch")
    stored_rows = _rows(stored.get("rows"))
    stored_freshness = _freshness(stored)
    if stored_rows and (stored_freshness == "current" or not current_answer_required):
        return AnalyticsSourceResult(
            stored_rows, "cloud_database", stored_freshness,
            str(stored.get("observed_at") or ""), False,
        )

    if not current_answer_required:
        return AnalyticsSourceResult(stored_rows, "cloud_database", stored_freshness, str(stored.get("observed_at") or ""), False)
    if query_kind in HISTORICAL_QUERY_KINDS:
        return AnalyticsSourceResult(stored_rows, "cloud_database", stored_freshness, str(stored.get("observed_at") or ""), False, "historical_cad_fallback_denied")
    if cad is None or cad_operation not in CAD_FALLBACK_OPERATIONS:
        return AnalyticsSourceResult(stored_rows, "cloud_database", stored_freshness, str(stored.get("observed_at") or ""), False, "cad_fallback_unavailable")

    live = cad.read_current(tenant_id, cad_operation, dict(parameters), timeout_seconds=timeout_seconds)
    if str(live.get("tenant_id") or "") != tenant_id:
        return AnalyticsSourceResult((), "read_only_cad", "unknown", "", True, "tenant_mismatch")
    observed_at = str(live.get("observed_at") or "")
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        return AnalyticsSourceResult((), "read_only_cad", "unknown", "", True, "invalid_freshness")
    minimized = tuple(
        {key: value for key, value in row.items() if key in CAD_FALLBACK_FIELDS}
        for row in _rows(live.get("rows"))
    )
    return AnalyticsSourceResult(minimized, "read_only_cad", _freshness(live), observed_at, True)
