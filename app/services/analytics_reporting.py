from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from app.core.county_profiles import resolve_county_profile
from app.core.county_presentation import (
    INHERITED_AGENCY_DISPLAY_LABELS as AGENCY_DISPLAY_LABELS,
    agency_display_label,
)
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import (
    TenantAuthorizationDenied,
    authorize_tenant_action,
)
from app.integrations.contracts import ModuleCapability
from app.services.analytics_database import (
    AnalyticsDatabaseError,
    AnalyticsRepository,
    analytics_database_is_configured,
)


LOCAL_TIMEZONE = ZoneInfo("America/New_York")
PERIOD_OPTIONS = {
    "24h": ("Last 24 hours", timedelta(hours=24)),
    "7d": ("Last 7 days", timedelta(days=7)),
    "30d": ("Last 30 days", timedelta(days=30)),
    "90d": ("Last 90 days", timedelta(days=90)),
    "365d": ("Last 12 months", timedelta(days=365)),
}
DEFAULT_PERIOD = "30d"
MAX_CUSTOM_DAYS = 366
# This person is an ambulance-service employee, not a Logan County dispatcher.
# Keep the historical call records, but exclude the exact normalized identity
# from Dispatcher / CAD Entry workload and processing-time reporting.
EXCLUDED_DISPATCHER_IDENTITY = "KIM MAYNARD"


class AnalyticsRangeError(ValueError):
    """Raised when an analytics date range is invalid."""


def _agency_display_label(
    value: object,
    county_profile: CountyProfile | None = None,
) -> str:
    return agency_display_label(value, county_profile)


@dataclass(frozen=True)
class AnalyticsWindow:
    key: str
    label: str
    start_at: datetime
    end_at: datetime
    start_date: str
    end_date: str


MIN_CUSTOM_HOURS = 1
MAX_CUSTOM_HOURS = MAX_CUSTOM_DAYS * 24


def resolve_analytics_window(
    period: str = DEFAULT_PERIOD,
    start: str = "",
    end: str = "",
    now: datetime | None = None,
    *,
    hours: int | None = None,
) -> AnalyticsWindow:
    local_now = now or datetime.now(LOCAL_TIMEZONE)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=LOCAL_TIMEZONE)
    else:
        local_now = local_now.astimezone(LOCAL_TIMEZONE)

    start = (start or "").strip()
    end = (end or "").strip()

    if hours is not None:
        if start or end:
            raise AnalyticsRangeError("hours cannot be combined with a custom start/end range.")
        if not (MIN_CUSTOM_HOURS <= hours <= MAX_CUSTOM_HOURS):
            raise AnalyticsRangeError(
                f"hours must be between {MIN_CUSTOM_HOURS} and {MAX_CUSTOM_HOURS}."
            )
        start_at = local_now - timedelta(hours=hours)
        return AnalyticsWindow(
            key=f"{hours}h",
            label=f"Last {hours} hours",
            start_at=start_at.astimezone(timezone.utc),
            end_at=local_now.astimezone(timezone.utc),
            start_date=start_at.date().isoformat(),
            end_date=local_now.date().isoformat(),
        )

    if start or end:
        if not start or not end:
            raise AnalyticsRangeError("Choose both a start date and an end date.")
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except ValueError as exc:
            raise AnalyticsRangeError("Use valid calendar dates.") from exc

        if end_day < start_day:
            raise AnalyticsRangeError("The end date must be on or after the start date.")
        if end_day > local_now.date():
            raise AnalyticsRangeError("The end date cannot be in the future.")
        if (end_day - start_day).days + 1 > MAX_CUSTOM_DAYS:
            raise AnalyticsRangeError(
                f"Custom ranges may include at most {MAX_CUSTOM_DAYS} days."
            )

        start_at = datetime.combine(start_day, time.min, LOCAL_TIMEZONE)
        end_at = datetime.combine(end_day + timedelta(days=1), time.min, LOCAL_TIMEZONE)
        label = (
            f"{start_day.strftime('%b')} {start_day.day}, {start_day.year}"
            f" – {end_day.strftime('%b')} {end_day.day}, {end_day.year}"
        )
        return AnalyticsWindow(
            key="custom",
            label=label,
            start_at=start_at.astimezone(timezone.utc),
            end_at=end_at.astimezone(timezone.utc),
            start_date=start_day.isoformat(),
            end_date=end_day.isoformat(),
        )

    period_key = period if period in PERIOD_OPTIONS else DEFAULT_PERIOD
    label, duration = PERIOD_OPTIONS[period_key]
    start_at = local_now - duration
    return AnalyticsWindow(
        key=period_key,
        label=label,
        start_at=start_at.astimezone(timezone.utc),
        end_at=local_now.astimezone(timezone.utc),
        start_date=start_at.date().isoformat(),
        end_date=local_now.date().isoformat(),
    )


def _number(value, default=0):
    if value is None:
        return default
    return float(value)


def _format_duration(seconds) -> str:
    if seconds is None:
        return "—"
    total_seconds = max(int(round(float(seconds))), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _empty_overview(window: AnalyticsWindow, message: str) -> dict:
    return {
        "available": False,
        "message": message,
        "period_key": window.key,
        "period_label": window.label,
        "start_date": window.start_date,
        "end_date": window.end_date,
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "latest_data_at": "",
        "metrics": {
            "total_calls": 0,
            "unit_responses": 0,
            "average_processing": "—",
            "average_response": "—",
            "median_response": "—",
            "response_coverage_percent": 0,
            "scheduled_calls": 0,
        },
        "dispatcher_metrics": {
            "calls_with_call_taker": 0,
            "coverage_percent": 0,
            "busiest_call_taker": "—",
            "busiest_call_count": 0,
            "average_processing": "—",
            "median_processing": "—",
            "p90_processing": "—",
            "processing_samples": 0,
            "within_90_percent": 0,
            "over_180_count": 0,
        },
        "dispatchers": [],
        "dispatcher_disciplines": [],
        "iso_fire": {
            "timed": 0,
            "fast_seconds": PROCESSING_TARGET_FAST_SECONDS,
            "fast_target_percent": PROCESSING_TARGET_FAST_PERCENT,
            "fast_percent": 0.0,
            "slow_seconds": PROCESSING_TARGET_SLOW_SECONDS,
            "slow_target_percent": PROCESSING_TARGET_SLOW_PERCENT,
            "slow_percent": 0.0,
            "meets_fast": None,
            "meets_slow": None,
        },
        "daily_volume": [],
        "hourly_volume": [],
        "weekday_volume": [],
        "agency_mix": [],
        "incident_types": [],
        "busiest_units": [],
        "busiest_stations": [],
        "station_discipline": [],
        "station_discipline_groups": [],
        "station_discipline_quality": {
            "classified_responses": 0,
            "total_responses": 0,
            "coverage_percent": 0,
            "unassigned_station_responses": 0,
        },
    }


def _query_overview(
    repository: AnalyticsRepository,
    window: AnalyticsWindow,
    county_profile: CountyProfile | None = None,
) -> dict:
    params = {
        "window_start": window.start_at,
        "window_end": window.end_at,
        "excluded_dispatcher_identity": EXCLUDED_DISPATCHER_IDENTITY,
    }

    metrics_row = repository.fetchone(
        """
        WITH selected_calls AS (
            SELECT *
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
        ),
        call_milestones AS (
            SELECT
                selected_calls.cfs_number,
                selected_calls.call_received_at,
                selected_calls.source_collected_at,
                selected_calls.is_scheduled,
                MIN(agency_times.dispatched_at) FILTER (
                    WHERE agency_times.dispatched_at >= selected_calls.call_received_at
                ) AS first_dispatched_at,
                MIN(agency_times.on_scene_at) FILTER (
                    WHERE agency_times.on_scene_at >= selected_calls.call_received_at
                ) AS first_on_scene_at
            FROM selected_calls
            LEFT JOIN lcdash_analytics.call_agency_times AS agency_times
                ON agency_times.cfs_number = selected_calls.cfs_number
            GROUP BY
                selected_calls.cfs_number,
                selected_calls.call_received_at,
                selected_calls.source_collected_at,
                selected_calls.is_scheduled
        )
        SELECT
            COUNT(*) AS total_calls,
            (
                SELECT COUNT(*)
                FROM lcdash_analytics.unit_responses AS unit_response
                JOIN selected_calls
                    ON selected_calls.cfs_number = unit_response.cfs_number
            ) AS unit_responses,
            AVG(EXTRACT(EPOCH FROM first_dispatched_at - call_received_at))
                FILTER (WHERE first_dispatched_at IS NOT NULL) AS average_processing_seconds,
            AVG(EXTRACT(EPOCH FROM first_on_scene_at - call_received_at))
                FILTER (WHERE first_on_scene_at IS NOT NULL) AS average_response_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_on_scene_at - call_received_at)
            ) FILTER (WHERE first_on_scene_at IS NOT NULL) AS median_response_seconds,
            COUNT(*) FILTER (WHERE first_on_scene_at IS NOT NULL) AS response_samples,
            MAX(source_collected_at) AS latest_data_at,
            COUNT(*) FILTER (WHERE is_scheduled) AS scheduled_calls
        FROM call_milestones
        """,
        params,
    ) or (0, 0, None, None, None, 0, None, 0)

    total_calls = int(metrics_row[0] or 0)
    response_samples = int(metrics_row[5] or 0)
    response_coverage = (
        round((response_samples / total_calls) * 100) if total_calls else 0
    )

    dispatcher_summary_row = repository.fetchone(
        """
        WITH selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                call_taker_unique_identifier,
                call_received_at,
                is_scheduled
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              AND BTRIM(call_taker) <> ''
              AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s
        ),
        call_processing AS (
            SELECT
                selected_calls.cfs_number,
                selected_calls.call_taker,
                COALESCE(
                    NULLIF(selected_calls.call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(selected_calls.call_taker)
                ) AS dispatcher_key,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                MIN(agency_times.dispatched_at) FILTER (
                    WHERE agency_times.dispatched_at >= selected_calls.call_received_at
                ) AS first_dispatched_at
            FROM selected_calls
            LEFT JOIN lcdash_analytics.call_agency_times AS agency_times
                ON agency_times.cfs_number = selected_calls.cfs_number
            GROUP BY
                selected_calls.cfs_number,
                selected_calls.call_taker,
                selected_calls.call_taker_unique_identifier,
                selected_calls.call_received_at,
                selected_calls.is_scheduled
        )
        SELECT
            COUNT(*) AS calls_with_call_taker,
            COUNT(*) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS processing_samples,
            AVG(EXTRACT(EPOCH FROM first_dispatched_at - call_received_at))
                FILTER (
                    WHERE first_dispatched_at IS NOT NULL
                      AND NOT is_scheduled
                      AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
                )
                AS average_processing_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
            ) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS median_processing_seconds,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM first_dispatched_at - call_received_at) <= 90
                  AND NOT is_scheduled
            ) AS within_90_seconds,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM first_dispatched_at - call_received_at) > 180
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            ) AS over_180_seconds,
            PERCENTILE_CONT(0.9) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
            ) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS p90_processing_seconds
        FROM call_processing
        """,
        params,
    ) or (0, 0, None, None, 0, 0, None)

    dispatcher_rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                call_taker_unique_identifier,
                call_received_at,
                is_scheduled
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              AND BTRIM(call_taker) <> ''
              AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s
        ),
        call_processing AS (
            SELECT
                selected_calls.cfs_number,
                selected_calls.call_taker,
                COALESCE(
                    NULLIF(selected_calls.call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(selected_calls.call_taker)
                ) AS dispatcher_key,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                MIN(agency_times.dispatched_at) FILTER (
                    WHERE agency_times.dispatched_at >= selected_calls.call_received_at
                ) AS first_dispatched_at
            FROM selected_calls
            LEFT JOIN lcdash_analytics.call_agency_times AS agency_times
                ON agency_times.cfs_number = selected_calls.cfs_number
            GROUP BY
                selected_calls.cfs_number,
                selected_calls.call_taker,
                selected_calls.call_taker_unique_identifier,
                selected_calls.call_received_at,
                selected_calls.is_scheduled
        )
        SELECT
            MAX(call_taker) AS call_taker,
            COUNT(*) AS calls_entered,
            COUNT(*) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS processing_samples,
            AVG(EXTRACT(EPOCH FROM first_dispatched_at - call_received_at))
                FILTER (
                    WHERE first_dispatched_at IS NOT NULL
                      AND NOT is_scheduled
                      AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
                )
                AS average_processing_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
            ) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS median_processing_seconds,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM first_dispatched_at - call_received_at) <= 90
                  AND NOT is_scheduled
            ) AS within_90_seconds,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM first_dispatched_at - call_received_at) > 180
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            ) AS over_180_seconds,
            PERCENTILE_CONT(0.9) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
            ) FILTER (
                WHERE first_dispatched_at IS NOT NULL
                  AND NOT is_scheduled
                  AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
            )
                AS p90_processing_seconds,
            LEFT(
                encode(sha256(convert_to(dispatcher_key, 'UTF8')), 'hex'),
                16
            ) AS dispatcher_ref
        FROM call_processing
        GROUP BY dispatcher_key
        ORDER BY calls_entered DESC, MAX(call_taker)
        LIMIT 30
        """,
        params,
    )
    calls_with_call_taker = int(dispatcher_summary_row[0] or 0)
    dispatcher_processing_samples = int(dispatcher_summary_row[1] or 0)
    dispatchers = []
    for row in dispatcher_rows:
        calls_entered = int(row[1] or 0)
        processing_samples = int(row[2] or 0)
        within_90 = int(row[5] or 0)
        over_180 = int(row[6] or 0)
        dispatchers.append(
            {
                "call_taker": row[0],
                "calls_entered": calls_entered,
                "share_percent": round(
                    (calls_entered / calls_with_call_taker) * 100,
                    1,
                ) if calls_with_call_taker else 0,
                "average_processing": _format_duration(row[3]),
                "median_processing": _format_duration(row[4]),
                "p90_processing": _format_duration(row[7]),
                # An opaque handle for the slow-call list; the CAD identifier
                # itself never leaves the server.
                "dispatcher_ref": str(row[8] or ""),
                "processing_samples": processing_samples,
                "within_90_percent": round(
                    (within_90 / processing_samples) * 100
                ) if processing_samples else 0,
                "over_180_count": over_180,
            }
        )

    dispatcher_disciplines, iso_fire = _discipline_metrics(repository, params)

    dispatcher_metrics = {
        "calls_with_call_taker": calls_with_call_taker,
        "coverage_percent": round(
            (calls_with_call_taker / total_calls) * 100
        ) if total_calls else 0,
        "busiest_call_taker": dispatchers[0]["call_taker"] if dispatchers else "—",
        "busiest_call_count": dispatchers[0]["calls_entered"] if dispatchers else 0,
        "average_processing": _format_duration(dispatcher_summary_row[2]),
        "median_processing": _format_duration(dispatcher_summary_row[3]),
        "p90_processing": _format_duration(dispatcher_summary_row[6]),
        "processing_samples": dispatcher_processing_samples,
        "within_90_percent": round(
            (int(dispatcher_summary_row[4] or 0) / dispatcher_processing_samples) * 100
        ) if dispatcher_processing_samples else 0,
        "over_180_count": int(dispatcher_summary_row[5] or 0),
    }

    daily_rows = repository.fetchall(
        """
        SELECT
            (call_received_at AT TIME ZONE 'America/New_York')::date AS local_day,
            COUNT(*) AS call_count
        FROM lcdash_analytics.calls
        WHERE call_received_at >= %(window_start)s
          AND call_received_at < %(window_end)s
        GROUP BY local_day
        ORDER BY local_day
        """,
        params,
    )
    daily_lookup = {row[0]: int(row[1]) for row in daily_rows}
    start_day = window.start_at.astimezone(LOCAL_TIMEZONE).date()
    end_day = (window.end_at.astimezone(LOCAL_TIMEZONE) - timedelta(microseconds=1)).date()
    daily_volume = []
    cursor_day = start_day
    while cursor_day <= end_day:
        daily_volume.append(
            {
                "date": cursor_day.isoformat(),
                "label": cursor_day.strftime("%b %d"),
                "count": daily_lookup.get(cursor_day, 0),
            }
        )
        cursor_day += timedelta(days=1)

    hourly_rows = repository.fetchall(
        """
        SELECT
            EXTRACT(HOUR FROM call_received_at AT TIME ZONE 'America/New_York')::integer
                AS local_hour,
            COUNT(*) AS call_count
        FROM lcdash_analytics.calls
        WHERE call_received_at >= %(window_start)s
          AND call_received_at < %(window_end)s
        GROUP BY local_hour
        ORDER BY local_hour
        """,
        params,
    )
    hourly_lookup = {int(row[0]): int(row[1]) for row in hourly_rows}
    hourly_volume = [
        {
            "hour": hour,
            "label": f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}",
            "count": hourly_lookup.get(hour, 0),
        }
        for hour in range(24)
    ]

    weekday_rows = repository.fetchall(
        """
        SELECT
            EXTRACT(DOW FROM call_received_at AT TIME ZONE 'America/New_York')::integer
                AS local_weekday,
            COUNT(*) AS call_count
        FROM lcdash_analytics.calls
        WHERE call_received_at >= %(window_start)s
          AND call_received_at < %(window_end)s
        GROUP BY local_weekday
        ORDER BY local_weekday
        """,
        params,
    )
    weekday_lookup = {int(row[0]): int(row[1]) for row in weekday_rows}
    weekday_labels = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ]
    weekday_volume = [
        {
            "weekday": weekday,
            "label": weekday_labels[weekday],
            "count": weekday_lookup.get(weekday, 0),
        }
        for weekday in range(7)
    ]

    agency_rows = repository.fetchall(
        """
        SELECT
            COALESCE(
                NULLIF(response_agency, ''),
                NULLIF(dispatch_agency, ''),
                'Unknown'
            ) AS agency,
            COUNT(*) AS call_count
        FROM lcdash_analytics.calls
        WHERE call_received_at >= %(window_start)s
          AND call_received_at < %(window_end)s
        GROUP BY agency
        ORDER BY call_count DESC, agency
        LIMIT 8
        """,
        params,
    )
    agency_mix = [
        {
            "label": _agency_display_label(row[0], county_profile),
            "count": int(row[1]),
            "percent": round((int(row[1]) / total_calls) * 100, 1)
            if total_calls
            else 0,
        }
        for row in agency_rows
    ]

    incident_rows = repository.fetchall(
        """
        SELECT
            COALESCE(
                NULLIF(incident_description, ''),
                NULLIF(incident_code, ''),
                'Unknown'
            ) AS incident_type,
            COUNT(*) AS call_count
        FROM lcdash_analytics.calls
        WHERE call_received_at >= %(window_start)s
          AND call_received_at < %(window_end)s
        GROUP BY incident_type
        ORDER BY call_count DESC, incident_type
        LIMIT 10
        """,
        params,
    )
    incident_types = [
        {
            "label": row[0],
            "count": int(row[1]),
            "percent": round((int(row[1]) / total_calls) * 100, 1)
            if total_calls
            else 0,
        }
        for row in incident_rows
    ]

    unit_rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT cfs_number
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
        )
        SELECT
            unit_response.unit_number,
            COALESCE(
                NULLIF(unit_record.station, ''),
                NULLIF(unit_response.station, ''),
                'Unassigned'
            ) AS station,
            COUNT(DISTINCT unit_response.cfs_number) AS response_count,
            AVG(response_metrics.total_response_seconds)
                FILTER (WHERE response_metrics.total_response_seconds IS NOT NULL)
                AS average_response_seconds
        FROM selected_calls
        JOIN lcdash_analytics.unit_responses AS unit_response
            ON unit_response.cfs_number = selected_calls.cfs_number
        LEFT JOIN lcdash_analytics.units AS unit_record
            ON unit_record.unit_number = unit_response.unit_number
        LEFT JOIN lcdash_analytics.unit_response_metrics AS response_metrics
            ON response_metrics.cfs_number = unit_response.cfs_number
           AND response_metrics.unit_number = unit_response.unit_number
        GROUP BY 1, 2
        ORDER BY response_count DESC, unit_response.unit_number
        LIMIT 10
        """,
        params,
    )
    busiest_units = [
        {
            "unit_number": row[0],
            "station": row[1],
            "responses": int(row[2]),
            "average_response": _format_duration(row[3]),
        }
        for row in unit_rows
    ]

    station_rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT cfs_number
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
        ),
        station_responses AS (
            SELECT
                COALESCE(
                    NULLIF(unit_record.station, ''),
                    NULLIF(unit_response.station, ''),
                    'Unassigned'
                ) AS station,
                unit_response.cfs_number
            FROM selected_calls
            JOIN lcdash_analytics.unit_responses AS unit_response
                ON unit_response.cfs_number = selected_calls.cfs_number
            LEFT JOIN lcdash_analytics.units AS unit_record
                ON unit_record.unit_number = unit_response.unit_number
        )
        SELECT station, COUNT(DISTINCT cfs_number) AS call_count
        FROM station_responses
        WHERE station <> 'Unassigned'
        GROUP BY station
        ORDER BY call_count DESC, station
        LIMIT 10
        """,
        params,
    )
    busiest_stations = [
        {"station": row[0], "calls": int(row[1])}
        for row in station_rows
    ]

    station_discipline_rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT cfs_number
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
        ),
        normalized_responses AS (
            SELECT
                COALESCE(
                    NULLIF(unit_record.station, ''),
                    NULLIF(unit_response.station, ''),
                    'Unassigned'
                ) AS station,
                UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')) AS agency,
                UPPER(COALESCE(
                    NULLIF(unit_record.unit_type, ''),
                    NULLIF(unit_response.unit_type, ''),
                    ''
                )) AS unit_type,
                unit_response.cfs_number
            FROM selected_calls
            JOIN lcdash_analytics.unit_responses AS unit_response
                ON unit_response.cfs_number = selected_calls.cfs_number
            LEFT JOIN lcdash_analytics.units AS unit_record
                ON unit_record.unit_number = unit_response.unit_number
        ),
        classified_responses AS (
            SELECT
                station,
                agency,
                cfs_number,
                CASE
                    WHEN unit_type LIKE 'EMS %%' OR agency = 'LEASA' THEN 'EMS'
                    WHEN unit_type LIKE 'FIRE %%' OR agency LIKE 'FC %%' THEN 'Fire'
                    WHEN unit_type IN ('PATROL CAR', 'COUNTY ADMIN')
                      OR agency IN ('CPD', 'DNR', 'DPS', 'LCSO', 'LPD', 'MPD', 'WVSP')
                        THEN 'Law'
                    ELSE NULL
                END AS discipline
            FROM normalized_responses
        ),
        labeled_responses AS (
            SELECT
                CASE
                    WHEN discipline = 'Fire' AND agency LIKE 'FC %%' THEN agency
                    ELSE station
                END AS station,
                cfs_number,
                discipline
            FROM classified_responses
        )
        SELECT
            station,
            COUNT(DISTINCT cfs_number) FILTER (WHERE discipline = 'Law') AS law_calls,
            COUNT(DISTINCT cfs_number) FILTER (WHERE discipline = 'EMS') AS ems_calls,
            COUNT(DISTINCT cfs_number) FILTER (WHERE discipline = 'Fire') AS fire_calls,
            COUNT(DISTINCT cfs_number) FILTER (WHERE discipline IS NOT NULL) AS total_calls
        FROM labeled_responses
        WHERE station <> 'Unassigned'
          AND discipline IS NOT NULL
        GROUP BY station
        """,
        params,
    )
    station_discipline = [
        {
            "station": row[0],
            "law": int(row[1] or 0),
            "ems": int(row[2] or 0),
            "fire": int(row[3] or 0),
            "total": int(row[4] or 0),
        }
        for row in station_discipline_rows
    ]
    discipline_order = {"Law": 0, "EMS": 1, "Fire": 2}
    discipline_field = {"Law": "law", "EMS": "ems", "Fire": "fire"}
    for station in station_discipline:
        station["discipline"] = max(
            discipline_order,
            key=lambda discipline: (
                station[discipline_field[discipline]],
                -discipline_order[discipline],
            ),
        )

    def natural_station_key(value: str):
        return [
            int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", value)
        ]

    station_discipline.sort(
        key=lambda station: (
            discipline_order[station["discipline"]],
            natural_station_key(station["station"]),
        )
    )
    station_discipline_groups = [
        {
            "discipline": discipline,
            "stations": [
                station
                for station in station_discipline
                if station["discipline"] == discipline
            ],
        }
        for discipline in discipline_order
    ]
    station_discipline_groups = [
        group for group in station_discipline_groups if group["stations"]
    ]

    station_quality_row = repository.fetchone(
        """
        WITH selected_calls AS (
            SELECT cfs_number
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
        ),
        normalized_responses AS (
            SELECT
                COALESCE(
                    NULLIF(unit_record.station, ''),
                    NULLIF(unit_response.station, ''),
                    'Unassigned'
                ) AS station,
                UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')) AS agency,
                UPPER(COALESCE(
                    NULLIF(unit_record.unit_type, ''),
                    NULLIF(unit_response.unit_type, ''),
                    ''
                )) AS unit_type
            FROM selected_calls
            JOIN lcdash_analytics.unit_responses AS unit_response
                ON unit_response.cfs_number = selected_calls.cfs_number
            LEFT JOIN lcdash_analytics.units AS unit_record
                ON unit_record.unit_number = unit_response.unit_number
        )
        SELECT
            COUNT(*) AS total_responses,
            COUNT(*) FILTER (
                WHERE unit_type LIKE 'EMS %%'
                   OR agency = 'LEASA'
                   OR unit_type LIKE 'FIRE %%'
                   OR agency LIKE 'FC %%'
                   OR unit_type IN ('PATROL CAR', 'COUNTY ADMIN')
                   OR agency IN ('CPD', 'DNR', 'DPS', 'LCSO', 'LPD', 'MPD', 'WVSP')
            ) AS classified_responses,
            COUNT(*) FILTER (WHERE station = 'Unassigned')
                AS unassigned_station_responses
        FROM normalized_responses
        """,
        params,
    ) or (0, 0, 0)
    total_station_responses = int(station_quality_row[0] or 0)
    classified_station_responses = int(station_quality_row[1] or 0)
    station_discipline_quality = {
        "classified_responses": classified_station_responses,
        "total_responses": total_station_responses,
        "coverage_percent": round(
            (classified_station_responses / total_station_responses) * 100
        ) if total_station_responses else 0,
        "unassigned_station_responses": int(station_quality_row[2] or 0),
    }

    latest_data = metrics_row[6]
    return {
        "available": True,
        "message": "",
        "period_key": window.key,
        "period_label": window.label,
        "start_date": window.start_date,
        "end_date": window.end_date,
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "latest_data_at": latest_data.astimezone(LOCAL_TIMEZONE).isoformat() if latest_data else "",
        "metrics": {
            "total_calls": total_calls,
            "unit_responses": int(metrics_row[1] or 0),
            "average_processing": _format_duration(metrics_row[2]),
            "average_response": _format_duration(metrics_row[3]),
            "median_response": _format_duration(metrics_row[4]),
            "response_coverage_percent": response_coverage,
            "scheduled_calls": int(metrics_row[7] or 0),
        },
        "dispatcher_metrics": dispatcher_metrics,
        "dispatchers": dispatchers,
        "dispatcher_disciplines": dispatcher_disciplines,
        "iso_fire": iso_fire,
        "daily_volume": daily_volume,
        "hourly_volume": hourly_volume,
        "weekday_volume": weekday_volume,
        "agency_mix": agency_mix,
        "incident_types": incident_types,
        "busiest_units": busiest_units,
        "busiest_stations": busiest_stations,
        "station_discipline": station_discipline,
        "station_discipline_groups": station_discipline_groups,
        "station_discipline_quality": station_discipline_quality,
    }


# Alarm-processing benchmarks the ISO fire rating looks at (NFPA 1221, 2016
# edition; the same figures appear in NFPA 1710): 90% of alarms processed in 64
# seconds and 95% in 106 seconds. The older 2013 edition used 80% in 60 seconds.
# NFPA lets some calls run longer (medical-protocol questioning, language or
# TTY relay, hazmat, technical rescue, possible criminal activity, text, an
# unclear location); CAD does not mark those, so they are counted against the
# standard window and the view reads conservatively.
PROCESSING_TARGET_FAST_SECONDS = 64
PROCESSING_TARGET_FAST_PERCENT = 90
PROCESSING_TARGET_SLOW_SECONDS = 106
PROCESSING_TARGET_SLOW_PERCENT = 95

# Which agencies count as which discipline when a call is dispatched. Fire is
# every "FC n" department; EMS is LEASA; law is the sheriff and the state, city
# and town police agencies (the union of the lists the analytics page and the
# county commission report already use).
DISCIPLINES = (("fire", "Fire"), ("ems", "EMS"), ("law", "Law"))
EMS_AGENCY_CODES = ("LEASA",)
LAW_AGENCY_CODES = ("CPD", "DNR", "DPS", "LCSO", "LPD", "MHPD", "MPD", "WLPD", "WVSP")

SLOW_CALL_THRESHOLD_SECONDS = 180
SLOW_CALL_LIMIT = 100
DISPATCHER_REF_PATTERN = re.compile(r"^[0-9a-f]{16}$")


class AnalyticsReferenceError(ValueError):
    """The dispatcher reference is not one this page produces."""


def _discipline_metrics(
    repository: AnalyticsRepository,
    params: dict,
) -> tuple[list[dict], dict]:
    """Call processing time by discipline, for every dispatcher and for all.

    A call counts once in each discipline that was dispatched to it, measured
    from the CAD "call received" time to the first dispatch of that discipline,
    so a dispatcher can be compared on law, EMS and fire separately. The same
    rules as the main table apply: scheduled calls and intervals over one hour
    are left out.
    """
    rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                call_taker_unique_identifier,
                call_received_at,
                is_scheduled
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              AND BTRIM(call_taker) <> ''
              AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s
        ),
        classified_times AS (
            SELECT
                cfs_number,
                dispatched_at,
                CASE
                    WHEN UPPER(agency_ori) LIKE 'FC %%' THEN 'fire'
                    WHEN UPPER(agency_ori) = ANY(%(ems_agencies)s) THEN 'ems'
                    WHEN UPPER(agency_ori) = ANY(%(law_agencies)s) THEN 'law'
                END AS discipline
            FROM lcdash_analytics.call_agency_times
        ),
        discipline_dispatch AS (
            SELECT
                selected_calls.cfs_number,
                selected_calls.call_taker,
                COALESCE(
                    NULLIF(selected_calls.call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(selected_calls.call_taker)
                ) AS dispatcher_key,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                classified_times.discipline,
                MIN(classified_times.dispatched_at) FILTER (
                    WHERE classified_times.dispatched_at >= selected_calls.call_received_at
                ) AS first_dispatched_at
            FROM selected_calls
            JOIN classified_times
                ON classified_times.cfs_number = selected_calls.cfs_number
            WHERE classified_times.discipline IS NOT NULL
            GROUP BY
                selected_calls.cfs_number,
                selected_calls.call_taker,
                selected_calls.call_taker_unique_identifier,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                classified_times.discipline
        ),
        timed AS (
            SELECT
                discipline,
                dispatcher_key,
                call_taker,
                EXTRACT(EPOCH FROM first_dispatched_at - call_received_at) AS seconds
            FROM discipline_dispatch
            WHERE first_dispatched_at IS NOT NULL
              AND NOT is_scheduled
              AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
        )
        SELECT
            discipline,
            GROUPING(dispatcher_key) AS is_total,
            MAX(call_taker) AS call_taker,
            LEFT(encode(sha256(convert_to(dispatcher_key, 'UTF8')), 'hex'), 16)
                AS dispatcher_ref,
            COUNT(*) AS timed_calls,
            AVG(seconds) AS average_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY seconds) AS median_seconds,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY seconds) AS p90_seconds,
            COUNT(*) FILTER (WHERE seconds <= %(fast_seconds)s) AS within_fast,
            COUNT(*) FILTER (WHERE seconds <= %(slow_seconds)s) AS within_slow,
            COUNT(*) FILTER (WHERE seconds > 180) AS over_180
        FROM timed
        GROUP BY GROUPING SETS ((discipline, dispatcher_key), (discipline))
        """,
        {
            **params,
            "ems_agencies": list(EMS_AGENCY_CODES),
            "law_agencies": list(LAW_AGENCY_CODES),
            "fast_seconds": PROCESSING_TARGET_FAST_SECONDS,
            "slow_seconds": PROCESSING_TARGET_SLOW_SECONDS,
        },
    )

    def percent(part, whole) -> float:
        return round((int(part or 0) / whole) * 100, 1) if whole else 0.0

    groups = {key: {"key": key, "label": label, "overall": None, "dispatchers": []}
              for key, label in DISCIPLINES}
    for row in rows:
        group = groups.get(row[0])
        if group is None:
            continue
        timed = int(row[4] or 0)
        entry = {
            "call_taker": "All dispatchers" if row[1] else str(row[2] or ""),
            "dispatcher_ref": "" if row[1] else str(row[3] or ""),
            "timed": timed,
            "average": _format_duration(row[5]),
            "median": _format_duration(row[6]),
            "p90": _format_duration(row[7]),
            "within_fast_percent": percent(row[8], timed),
            "within_slow_percent": percent(row[9], timed),
            "over_180_count": int(row[10] or 0),
        }
        if row[1]:
            group["overall"] = entry
        else:
            group["dispatchers"].append(entry)
    for group in groups.values():
        group["dispatchers"].sort(key=lambda item: (-item["timed"], item["call_taker"]))

    fire = groups["fire"]["overall"]
    iso_fire = {
        "timed": fire["timed"] if fire else 0,
        "fast_seconds": PROCESSING_TARGET_FAST_SECONDS,
        "fast_target_percent": PROCESSING_TARGET_FAST_PERCENT,
        "fast_percent": fire["within_fast_percent"] if fire else 0.0,
        "slow_seconds": PROCESSING_TARGET_SLOW_SECONDS,
        "slow_target_percent": PROCESSING_TARGET_SLOW_PERCENT,
        "slow_percent": fire["within_slow_percent"] if fire else 0.0,
        "meets_fast": (
            fire["within_fast_percent"] >= PROCESSING_TARGET_FAST_PERCENT if fire else None
        ),
        "meets_slow": (
            fire["within_slow_percent"] >= PROCESSING_TARGET_SLOW_PERCENT if fire else None
        ),
    }
    return [groups[key] for key, _label in DISCIPLINES], iso_fire


def _query_slow_calls(
    repository: AnalyticsRepository,
    window: AnalyticsWindow,
    dispatcher_ref: str,
) -> dict:
    """Calls the dispatcher entered that took over three minutes to dispatch.

    Same population as the "> 3 Min" count in the dispatcher table: scheduled
    calls and intervals over one hour are excluded. Only fields the analytics
    store already shows elsewhere (CFS number, incident type, priority, agency
    and the times); no location and no narrative.
    """
    rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                call_taker_unique_identifier,
                call_received_at,
                is_scheduled,
                incident_code,
                incident_description,
                priority,
                dispatch_agency
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              AND BTRIM(call_taker) <> ''
              AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s
        ),
        call_processing AS (
            SELECT
                selected_calls.cfs_number,
                COALESCE(
                    NULLIF(selected_calls.call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(selected_calls.call_taker)
                ) AS dispatcher_key,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                selected_calls.incident_code,
                selected_calls.incident_description,
                selected_calls.priority,
                selected_calls.dispatch_agency,
                MIN(agency_times.dispatched_at) FILTER (
                    WHERE agency_times.dispatched_at >= selected_calls.call_received_at
                ) AS first_dispatched_at
            FROM selected_calls
            LEFT JOIN lcdash_analytics.call_agency_times AS agency_times
                ON agency_times.cfs_number = selected_calls.cfs_number
            GROUP BY
                selected_calls.cfs_number,
                selected_calls.call_taker,
                selected_calls.call_taker_unique_identifier,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                selected_calls.incident_code,
                selected_calls.incident_description,
                selected_calls.priority,
                selected_calls.dispatch_agency
        )
        SELECT
            cfs_number,
            call_received_at,
            EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
                AS dispatch_seconds,
            incident_description,
            incident_code,
            priority,
            dispatch_agency,
            COUNT(*) OVER () AS total_matches
        FROM call_processing
        WHERE LEFT(
                encode(sha256(convert_to(dispatcher_key, 'UTF8')), 'hex'),
                16
              ) = %(dispatcher_ref)s
          AND first_dispatched_at IS NOT NULL
          AND NOT is_scheduled
          AND first_dispatched_at <= call_received_at + INTERVAL '1 hour'
          AND EXTRACT(EPOCH FROM first_dispatched_at - call_received_at)
              > %(threshold_seconds)s
        ORDER BY dispatch_seconds DESC, call_received_at DESC
        LIMIT %(row_limit)s
        """,
        {
            "window_start": window.start_at,
            "window_end": window.end_at,
            "excluded_dispatcher_identity": EXCLUDED_DISPATCHER_IDENTITY,
            "dispatcher_ref": dispatcher_ref,
            "threshold_seconds": SLOW_CALL_THRESHOLD_SECONDS,
            "row_limit": SLOW_CALL_LIMIT,
        },
    )
    calls = []
    for row in rows:
        received = row[1]
        calls.append(
            {
                "cfs_number": str(row[0] or ""),
                "received_at": (
                    received.astimezone(LOCAL_TIMEZONE).strftime("%b %d, %H:%M")
                    if received is not None
                    else "—"
                ),
                "processing": _format_duration(row[2]),
                "processing_seconds": int(round(float(row[2]))) if row[2] is not None else None,
                "incident": str(row[3] or row[4] or "").strip() or "—",
                "priority": str(row[5] or "").strip() or "—",
                "agency": _agency_display_label(str(row[6] or "")) or "—",
            }
        )
    total = int(rows[0][7]) if rows else 0
    return {
        "threshold_seconds": SLOW_CALL_THRESHOLD_SECONDS,
        "total": total,
        "shown": len(calls),
        "calls": calls,
    }


def get_dispatcher_slow_calls(
    dispatcher_ref: str,
    period: str = DEFAULT_PERIOD,
    start: str = "",
    end: str = "",
    tenant_context: TenantContext | None = None,
) -> dict:
    """The calls behind a dispatcher's "> 3 Min" count, longest first."""
    if not DISPATCHER_REF_PATTERN.fullmatch(str(dispatcher_ref or "")):
        raise AnalyticsReferenceError("That dispatcher reference is not valid.")

    if tenant_context is not None:
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.ANALYTICS,
            "read",
        )

    window = resolve_analytics_window(period=period, start=start, end=end)
    empty = {
        "available": False,
        "period_label": window.label,
        "threshold_seconds": SLOW_CALL_THRESHOLD_SECONDS,
        "total": 0,
        "shown": 0,
        "calls": [],
    }
    if not analytics_database_is_configured():
        return {**empty, "message": "PostgreSQL analytics is not configured on this machine."}
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            result = _query_slow_calls(repository, window, dispatcher_ref)
    except AnalyticsDatabaseError:
        return {**empty, "message": "PostgreSQL analytics is configured but unavailable."}
    return {"available": True, "period_label": window.label, **result}


def get_analytics_overview(
    period: str = DEFAULT_PERIOD,
    start: str = "",
    end: str = "",
    county_profile: CountyProfile | None = None,
    tenant_context: TenantContext | None = None,
    *,
    hours: int | None = None,
) -> dict:
    if tenant_context is not None:
        if county_profile is not None:
            raise TenantAuthorizationDenied(
                "Trusted context and direct county profile cannot be combined."
            )
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.ANALYTICS,
            "read",
        )

    window = resolve_analytics_window(period=period, start=start, end=end, hours=hours)

    if not analytics_database_is_configured():
        return _empty_overview(
            window,
            "PostgreSQL analytics is not configured on this machine.",
        )

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            return _query_overview(repository, window, county_profile)
    except AnalyticsDatabaseError:
        return _empty_overview(
            window,
            "PostgreSQL analytics is configured but unavailable.",
        )
