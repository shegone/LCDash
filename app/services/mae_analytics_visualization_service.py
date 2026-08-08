"""Build safe, aggregate-only MAE analytics visualizations and saved widgets."""
from __future__ import annotations

import re

from app.services.analytics_database import AnalyticsDatabaseError, AnalyticsRepository


VISUAL_REQUEST_PATTERN = re.compile(
    r"\b(chart|graph|plot|visual(?:ize|ization)?|dashboard widget)\b",
    re.IGNORECASE,
)

VIEW_CATALOG = {
    "daily_volume": {
        "title": "Calls by Day",
        "source_key": "daily_volume",
        "label_key": "label",
        "value_key": "count",
        "chart_type": "line",
        "aliases": ("day", "daily", "date", "trend", "volume"),
    },
    "hourly_volume": {
        "title": "Calls by Hour",
        "source_key": "hourly_volume",
        "label_key": "label",
        "value_key": "count",
        "chart_type": "bar",
        "aliases": ("hour", "hourly", "time of day"),
    },
    "weekday_volume": {
        "title": "Calls by Day of Week",
        "source_key": "weekday_volume",
        "label_key": "label",
        "value_key": "count",
        "chart_type": "bar",
        "aliases": (
            "weekday",
            "weekdays",
            "day of week",
            "days of week",
            "day of the week",
            "days of the week",
            "busiest day",
            "busiest days",
        ),
    },
    "agency_mix": {
        "title": "Calls by Agency",
        "source_key": "agency_mix",
        "label_key": "label",
        "value_key": "count",
        "chart_type": "doughnut",
        "aliases": ("agency", "agencies", "department"),
    },
    "incident_types": {
        "title": "Top Incident Types",
        "source_key": "incident_types",
        "label_key": "label",
        "value_key": "count",
        "chart_type": "bar",
        "aliases": ("incident", "call type", "nature", "complaint"),
    },
    "dispatcher_workload": {
        "title": "Dispatcher Call-Taker Workload",
        "source_key": "dispatchers",
        "label_key": "call_taker",
        "value_key": "calls_entered",
        "chart_type": "bar",
        "aliases": ("dispatcher", "call taker", "call-taker"),
    },
    "busiest_units": {
        "title": "Busiest Units",
        "source_key": "busiest_units",
        "label_key": "unit_number",
        "value_key": "responses",
        "chart_type": "bar",
        "aliases": ("unit", "units", "apparatus"),
    },
    "busiest_stations": {
        "title": "Busiest Stations",
        "source_key": "busiest_stations",
        "label_key": "station",
        "value_key": "calls",
        "chart_type": "bar",
        "aliases": ("station", "stations"),
    },
}


def validate_view_key(view_key: str) -> str:
    normalized = str(view_key or "").strip().lower()
    if normalized not in VIEW_CATALOG:
        raise ValueError("Unsupported analytics chart.")
    return normalized


def infer_view_key(question: str) -> str | None:
    """Return an allowlisted view only for an explicit visual request."""
    lowered = str(question or "").lower()
    if not VISUAL_REQUEST_PATTERN.search(lowered):
        return None
    matches: list[tuple[int, str]] = []
    for view_key, definition in VIEW_CATALOG.items():
        score = sum(1 for alias in definition["aliases"] if alias in lowered)
        if score:
            matches.append((score, view_key))
    if not matches:
        return "daily_volume"
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def build_visualization(snapshot: dict, view_key: str) -> dict:
    """Build a browser-safe chart spec from an existing aggregate snapshot."""
    safe_key = validate_view_key(view_key)
    definition = VIEW_CATALOG[safe_key]
    rows = snapshot.get(definition["source_key"]) or []
    points = [
        {
            "label": str(row.get(definition["label_key"]) or "Unknown")[:100],
            "value": max(int(row.get(definition["value_key"]) or 0), 0),
        }
        for row in rows[:30]
        if isinstance(row, dict)
    ]
    return {
        "view_key": safe_key,
        "title": definition["title"],
        "chart_type": definition["chart_type"],
        "points": points,
        "period_key": snapshot.get("period_key") or "30d",
        "period_label": snapshot.get("period_label") or "Selected period",
        "generated_at": snapshot.get("generated_at") or "",
        "source": "PostgreSQL analytics",
        "aggregate_only": True,
    }


def build_requested_visualization(question: str, context: list[dict]) -> dict | None:
    view_key = infer_view_key(question)
    if not view_key:
        return None
    for item in context:
        if item.get("source") == "PostgreSQL analytics":
            snapshot = item.get("data") or {}
            if snapshot.get("available"):
                return build_visualization(snapshot, view_key)
    return None


def list_saved_widgets() -> list[dict]:
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            rows = repository.fetchall(
                """
                SELECT widget_id, title, view_key, created_by, created_at
                FROM lcdash_analytics.saved_analytics_widgets
                WHERE status = 'active'
                ORDER BY created_at DESC
                LIMIT 24
                """
            )
    except AnalyticsDatabaseError:
        return []
    return [
        {
            "widget_id": int(row[0]),
            "title": row[1],
            "view_key": row[2],
            "created_by": row[3],
            "created_at": row[4].isoformat() if row[4] else "",
        }
        for row in rows
    ]


def save_widget(*, title: str, view_key: str, created_by: str) -> dict:
    safe_key = validate_view_key(view_key)
    clean_title = str(title or "").strip() or VIEW_CATALOG[safe_key]["title"]
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            row = repository.fetchone(
                """
                INSERT INTO lcdash_analytics.saved_analytics_widgets
                    (title, view_key, created_by)
                VALUES (%s, %s, %s)
                RETURNING widget_id
                """,
                (clean_title[:200], safe_key, str(created_by or "")[:320]),
            )
            repository._commit()
        return {"saved": True, "widget_id": int(row[0])}
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "message": str(exc)}


def retire_widget(*, widget_id: int) -> dict:
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            row = repository.fetchone(
                """
                UPDATE lcdash_analytics.saved_analytics_widgets
                SET status = 'retired', updated_at = NOW()
                WHERE widget_id = %s AND status = 'active'
                RETURNING widget_id
                """,
                (widget_id,),
            )
            repository._commit()
        return {"saved": bool(row), "widget_id": int(row[0]) if row else widget_id}
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "message": str(exc)}
