"""Verified live-data facts for MAE/JACK: current CAD status and analytics KPIs.

This module computes exact facts in Python from data the application already
holds -- the continuously-polled read-only CAD snapshot and the analytics
database -- and never lets a model free-associate over raw records. A model
is only ever asked to phrase a small, fixed set of already-computed facts
(see ``verified_live_advisory.py``), so a wrong answer here would have to be
a phrasing error, not an invented number.

Nothing in this module calls a CAD write/dispatch operation; it only reads
the same allowlisted, sanitized fields the dashboard and map already use
(``CALL_FIELDS`` / ``UNIT_FIELDS`` in ``cloud_read_config.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping, Sequence


CFS_PATTERN = re.compile(r"\bCFS\d{2}-\d{4,6}\b", re.IGNORECASE)

_ACTIVE_CALL_TERMS = re.compile(
    r"\b(active call|how many calls?|current call|call volume|call count)s?\b",
    re.IGNORECASE,
)
_UNIT_STATUS_TERMS = re.compile(
    r"\b(unit status|units? (available|active|on scene|out)|how many units?)\b",
    re.IGNORECASE,
)
_TOTAL_CALLS_TERMS = re.compile(
    r"\b(total calls?|calls? (today|yesterday|this week|this month|so far))\b",
    re.IGNORECASE,
)
_RESPONSE_TIME_TERMS = re.compile(
    r"\b(average|typical|mean) response( time)?\b", re.IGNORECASE
)
_BUSIEST_TERMS = re.compile(
    r"\bbusiest (station|unit|incident type)\b", re.IGNORECASE
)

_PERIOD_TERMS = {
    "24h": re.compile(r"\btoday|last 24 hours?|past day\b", re.IGNORECASE),
    "7d": re.compile(r"\bthis week|last (7|seven) days?|past week\b", re.IGNORECASE),
    "30d": re.compile(r"\bthis month|last (30|thirty) days?|past month\b", re.IGNORECASE),
}
_DEFAULT_PERIOD = "24h"


@dataclass(frozen=True, slots=True)
class LiveDataSource:
    """Transparency entry shown to the user, mirroring the on-prem pattern."""

    name: str
    kind: str  # "live" or "historical"
    detail: str
    available: bool
    timestamp: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedFact:
    """One exact, code-computed fact a model may phrase but never invent."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class LiveDataIntent:
    wants_active_calls: bool = False
    wants_unit_status: bool = False
    wants_call_detail: bool = False
    wants_totals: bool = False
    wants_response_time: bool = False
    wants_busiest: bool = False
    target_cfs_number: str = ""
    period: str = _DEFAULT_PERIOD

    @property
    def wants_cad(self) -> bool:
        return self.wants_active_calls or self.wants_unit_status or self.wants_call_detail

    @property
    def wants_analytics(self) -> bool:
        return self.wants_totals or self.wants_response_time or self.wants_busiest


def detect_live_data_intent(question: str) -> LiveDataIntent:
    """Bounded keyword detection, not an attempt to cover every phrasing.

    Deliberately smaller than on-prem's mature pattern set -- this only
    recognizes clear, unambiguous asks. Anything it misses falls through to
    the existing document-citation path, which is the safe default.
    """
    clean = str(question or "")
    cfs_match = CFS_PATTERN.search(clean)
    target_cfs = cfs_match.group(0).upper() if cfs_match else ""

    period = _DEFAULT_PERIOD
    for key, pattern in _PERIOD_TERMS.items():
        if pattern.search(clean):
            period = key
            break

    return LiveDataIntent(
        wants_active_calls=bool(_ACTIVE_CALL_TERMS.search(clean)) and not target_cfs,
        wants_unit_status=bool(_UNIT_STATUS_TERMS.search(clean)),
        wants_call_detail=bool(target_cfs),
        wants_totals=bool(_TOTAL_CALLS_TERMS.search(clean)),
        wants_response_time=bool(_RESPONSE_TIME_TERMS.search(clean)),
        wants_busiest=bool(_BUSIEST_TERMS.search(clean)),
        target_cfs_number=target_cfs,
        period=period,
    )


def _count_by_field(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get(field) or "Unspecified").strip() or "Unspecified"
        counts[label] = counts.get(label, 0) + 1
    return counts


def compute_cad_facts(
    intent: LiveDataIntent,
    *,
    cad_state: Any,
    cad_status: Mapping[str, Any],
) -> tuple[tuple[VerifiedFact, ...], tuple[LiveDataSource, ...]]:
    """Compute facts from the already-polled, in-memory CAD snapshot.

    Reads only ``cad_state.calls``/``cad_state.units`` -- the same
    normalized, allowlisted tuples the dashboard and map already render.
    Performs no new CAD API call.
    """
    if not intent.wants_cad:
        return (), ()

    freshness = str(cad_status.get("freshness") or "unknown")
    age_seconds = cad_status.get("age_seconds")
    available = freshness not in {"disabled", "awaiting-success", "stale"}
    timestamp = f"{age_seconds}s old" if isinstance(age_seconds, (int, float)) else freshness

    source = LiveDataSource(
        name="CentralSquare CAD (current read-only snapshot)",
        kind="live",
        detail=f"Freshness: {freshness}",
        available=available,
        timestamp=timestamp,
    )
    if not available:
        return (), (source,)

    facts: list[VerifiedFact] = []
    calls = tuple(cad_state.calls)
    units = tuple(cad_state.units)

    if intent.wants_active_calls:
        facts.append(VerifiedFact("Currently active calls", str(len(calls))))
        by_agency = _count_by_field(calls, "agency")
        if by_agency:
            breakdown = ", ".join(
                f"{label}: {count}" for label, count in sorted(by_agency.items())
            )
            facts.append(VerifiedFact("Active calls by agency", breakdown))

    if intent.wants_unit_status:
        facts.append(VerifiedFact("Units in current roster", str(len(units))))
        by_status = _count_by_field(units, "status")
        if by_status:
            breakdown = ", ".join(
                f"{label}: {count}" for label, count in sorted(by_status.items())
            )
            facts.append(VerifiedFact("Units by status", breakdown))

    if intent.wants_call_detail and intent.target_cfs_number:
        match = next(
            (
                call for call in calls
                if str(call.get("cfs_number") or "").upper() == intent.target_cfs_number
            ),
            None,
        )
        if match is None:
            facts.append(
                VerifiedFact(
                    f"Call {intent.target_cfs_number}",
                    "Not in the current active-call snapshot.",
                )
            )
        else:
            for label, field in (
                ("Incident", "incident_description"),
                ("Priority", "priority"),
                ("Status", "status"),
                ("Location", "location_label"),
                ("Assigned units", "assigned_units"),
            ):
                value = match.get(field)
                if value not in (None, "", (), []):
                    facts.append(VerifiedFact(f"{intent.target_cfs_number} {label}", str(value)))

    return tuple(facts), (source,)


def compute_analytics_facts(
    intent: LiveDataIntent,
    *,
    overview: Mapping[str, Any],
) -> tuple[tuple[VerifiedFact, ...], tuple[LiveDataSource, ...]]:
    """Compute facts from an already-fetched ``get_analytics_overview()`` result."""
    if not intent.wants_analytics:
        return (), ()

    available = bool(overview.get("available"))
    timestamp = str(overview.get("latest_data_at") or overview.get("generated_at") or "")
    source = LiveDataSource(
        name="PostgreSQL analytics",
        kind="historical",
        detail=f"Period: {intent.period}",
        available=available,
        timestamp=timestamp,
    )
    if not available:
        return (), (source,)

    metrics = overview.get("metrics") or {}
    facts: list[VerifiedFact] = []

    if intent.wants_totals:
        total = metrics.get("total_calls")
        if total is not None:
            facts.append(VerifiedFact(f"Total calls ({intent.period})", str(total)))

    if intent.wants_response_time:
        average = metrics.get("average_response")
        if average not in (None, ""):
            facts.append(VerifiedFact(f"Average response time ({intent.period})", str(average)))

    if intent.wants_busiest:
        for label, key in (
            ("Busiest station", "busiest_stations"),
            ("Busiest unit", "busiest_units"),
            ("Top incident type", "incident_types"),
        ):
            rows = overview.get(key) or []
            if rows and isinstance(rows[0], Mapping):
                top = rows[0]
                name = top.get("label") or top.get("name") or top.get("station") or top.get("unit_number")
                count = top.get("count") or top.get("total")
                if name is not None:
                    detail = f"{name}" + (f" ({count})" if count is not None else "")
                    facts.append(VerifiedFact(f"{label} ({intent.period})", detail))

    return tuple(facts), (source,)


def build_live_data_facts(
    question: str,
    *,
    cad_state: Any,
    cad_status: Mapping[str, Any],
    analytics_overview_fn: Callable[[str], Mapping[str, Any]] | None = None,
) -> tuple[tuple[VerifiedFact, ...], tuple[LiveDataSource, ...]]:
    """Detect intent and compute every relevant fact for one question.

    ``analytics_overview_fn`` is called only if the question actually needs
    analytics data, so a purely operational question never touches the
    database.
    """
    intent = detect_live_data_intent(question)
    cad_facts, cad_sources = compute_cad_facts(
        intent, cad_state=cad_state, cad_status=cad_status
    )
    analytics_facts: tuple[VerifiedFact, ...] = ()
    analytics_sources: tuple[LiveDataSource, ...] = ()
    if intent.wants_analytics and analytics_overview_fn is not None:
        overview = analytics_overview_fn(intent.period)
        analytics_facts, analytics_sources = compute_analytics_facts(
            intent, overview=overview
        )
    return cad_facts + analytics_facts, cad_sources + analytics_sources
