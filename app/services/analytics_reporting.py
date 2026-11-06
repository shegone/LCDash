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
# CAD processing runs from the CAD call-received time to the earliest action
# by any unit of any agency on the call (assigned, en route, staged or on
# scene), read from both the per-agency and the per-unit timing records.
# Intervals over the cap are held or queued calls rather than call-taker
# work: they are kept out of the averages but stay visible as a count and in
# the slow-call drill-down. CAD's Stack event (a call queued on a busy unit)
# comes from the command log in unit_status_events and ends the interval
# like an Assign; the wait from that Stack to the first Assign / En route is
# the unit's hold, reported apart (docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md).
PROCESSING_CAP_SECONDS = 3600
PROCESSING_CAP_MINUTES = PROCESSING_CAP_SECONDS // 60
FIRST_ACTION_LABELS = {
    "stacked": "Stacked",
    "assigned": "Assigned",
    "enroute": "En route",
    "staged": "Staged",
    "on_scene": "On scene",
}
# The dispatcher section can be narrowed to one discipline: the calls that
# discipline's units responded to (the same unit-roster rule the discipline
# tables use). The filter chooses the calls only; each is still timed to the
# first unit action on the call, whatever that unit's discipline, because the
# first unit assigned is what stops the dispatcher's clock (decided
# 2026-11-06).
DISCIPLINE_FILTERS = (
    ("", "All disciplines"),
    ("ems", "EMS"),
    ("fire", "Fire"),
    ("law", "Law"),
)
DISCIPLINE_FILTER_LABELS = dict(DISCIPLINE_FILTERS)
# Call types that are never dispatched as an emergency (administrative or
# scheduled transport natures, information and lookout entries, hang-ups
# and open lines), so their "received to first unit action" interval says
# nothing about the dispatcher. They stay in the call counts and are
# reported as not timed. Ted's list, 2026-11-05; Motorist Assist, Public
# Works / Utilities, Vehicle Lock Out / Lock In and Lock Out added 2026-11-06
# (CAD carries the lock-out nature under both names). Matching ignores case,
# spaces and punctuation ("911 Hang Up" = "911 HANG-UP" = "911 HANGUP") but
# is otherwise exact, so each CAD spelling needs its own entry.
EXCLUDED_CALL_TYPES = (
    "Roundtrip",
    "Take Home",
    "Transfer / Inter-Facility",
    "Contact",
    "Move-Up",
    "Shoplifting",
    "Complaint",
    "911 Hang Up",
    "Call Through Office",
    "911 Open Line",
    "Information",
    "Be On the Lookout",
    "Motorist Assist",
    "Public Works / Utilities",
    "Vehicle Lock Out / Lock In",
    "Lock Out",
)


def call_type_key(value: str | None) -> str:
    """The comparison form of a CAD incident description: A-Z and 0-9 only."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


EXCLUDED_CALL_TYPE_KEYS = tuple(call_type_key(name) for name in EXCLUDED_CALL_TYPES)


def resolve_discipline(value: str | None) -> str:
    """Normalise a discipline filter; '' means every discipline."""
    key = str(value or "").strip().lower()
    if key not in DISCIPLINE_FILTER_LABELS:
        raise AnalyticsRangeError("Choose EMS, Fire or Law, or all disciplines.")
    return key


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


# Every dispatch-related stamp CAD recorded for a call, one row per stamp,
# from the per-agency record (CallTimes), the per-unit record (UnitTimes)
# and the command log's status events (unit_status_events, the only place
# Stack appears). ``stamp_rank`` orders stamps that share a timestamp (an
# "Assigned" derived from an "En route" carries the same time; a Stack at
# the same second as an Assign is named first). ``discipline`` is the acting
# unit's roster agency / unit type (ems, fire, law or ''), with the per-agency
# ORI as the fallback. It is never filtered here: a discipline view narrows
# the calls (``_call_processing_sql``), and every call is timed to its first
# stamp by any unit.
ACTION_STAMPS_SQL = """
        action_stamps AS (
            SELECT
                stamps.cfs_number,
                stamps.party,
                stamps.source,
                stamps.stamp_at,
                stamps.kind,
                stamps.stamp_rank,
                stamps.discipline
            FROM (
                SELECT
                    raw_stamps.cfs_number,
                    raw_stamps.party,
                    raw_stamps.source,
                    raw_stamps.stamp_at,
                    raw_stamps.kind,
                    raw_stamps.stamp_rank,
                    CASE
                        WHEN raw_stamps.agency = ANY(%(ems_agencies)s)
                          OR raw_stamps.unit_type LIKE 'EMS %%' THEN 'ems'
                        WHEN raw_stamps.agency LIKE 'FC %%'
                          OR raw_stamps.unit_type LIKE 'FIRE %%' THEN 'fire'
                        WHEN raw_stamps.agency = ANY(%(law_agencies)s)
                          OR raw_stamps.unit_type IN ('PATROL CAR', 'COUNTY ADMIN') THEN 'law'
                        ELSE ''
                    END AS discipline
                FROM (
                SELECT
                    status_event.cfs_number,
                    status_event.unit_number AS party,
                    'log' AS source,
                    status_event.occurred_at AS stamp_at,
                    status_event.event_kind AS kind,
                    CASE status_event.event_kind
                        WHEN 'stacked' THEN 0
                        WHEN 'assigned' THEN 1
                        WHEN 'enroute' THEN 2
                        WHEN 'staged' THEN 3
                        ELSE 4
                    END AS stamp_rank,
                    UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')) AS agency,
                    UPPER(COALESCE(NULLIF(unit_record.unit_type, ''), '')) AS unit_type
                FROM lcdash_analytics.unit_status_events AS status_event
                LEFT JOIN lcdash_analytics.units AS unit_record
                    ON unit_record.unit_number = status_event.unit_number
                WHERE status_event.event_kind IN ('stacked', 'assigned', 'enroute', 'staged', 'on_scene')
                UNION ALL
                SELECT
                    timing.cfs_number,
                    timing.party,
                    timing.source,
                    stamp.stamp_at,
                    stamp.kind,
                    stamp.stamp_rank,
                    timing.agency,
                    timing.unit_type
                FROM (
                    SELECT
                        cfs_number,
                        agency_ori AS party,
                        'agency' AS source,
                        UPPER(agency_ori) AS agency,
                        '' AS unit_type,
                        dispatched_at,
                        enroute_at,
                        staged_at,
                        on_scene_at
                    FROM lcdash_analytics.call_agency_times
                    UNION ALL
                    SELECT
                        unit_response.cfs_number,
                        unit_response.unit_number,
                        'unit',
                        UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')),
                        UPPER(COALESCE(
                            NULLIF(unit_record.unit_type, ''),
                            NULLIF(unit_response.unit_type, ''),
                            ''
                        )),
                        unit_response.dispatched_at,
                        unit_response.enroute_at,
                        unit_response.staged_at,
                        unit_response.on_scene_at
                    FROM lcdash_analytics.unit_responses AS unit_response
                    LEFT JOIN lcdash_analytics.units AS unit_record
                        ON unit_record.unit_number = unit_response.unit_number
                ) AS timing
                CROSS JOIN LATERAL (
                    VALUES
                        (timing.dispatched_at, 'assigned', 1),
                        (timing.enroute_at, 'enroute', 2),
                        (timing.staged_at, 'staged', 3),
                        (timing.on_scene_at, 'on_scene', 4)
                ) AS stamp(stamp_at, kind, stamp_rank)
                WHERE stamp.stamp_at IS NOT NULL
                ) AS raw_stamps
            ) AS stamps
        )"""


def _call_processing_sql(
    *,
    dispatcher_calls: bool,
    extra_columns: str = "",
    discipline: str = "",
) -> str:
    """CTEs ending in ``call_processing``: one row per completed call in the window.

    Each row carries ``first_action_at`` (the earliest unit action at or
    after the received time: Stack, Assign, En route, Staged or On scene),
    ``first_dispatch_at`` (the earliest of those other than Stack),
    ``hold_seconds`` (Stack to that dispatch when the call was stacked first;
    the unit's wait, not the dispatcher's), ``first_on_scene_at``,
    ``processing_seconds`` and ``timing_status``: ``timed``, ``scheduled``,
    ``no_stamp`` (no unit action at or after the received time) or
    ``over_cap``. Only ``timed`` rows enter any average; the other three are
    counted so the exclusions are visible on the page, as is
    ``excluded_type`` (an EXCLUDED_CALL_TYPES nature). ``dispatcher_calls``
    restricts the population to calls with a call taker, minus the excluded
    identity. ``discipline`` (ems, fire or law) keeps only the calls that
    discipline's units acted on; the milestones still come from every stamp
    on the call, so the interval ends at the first unit action by any
    discipline (the first unit assigned stops the clock, even when the
    filtered discipline was added to the call later). Bound parameters:
    window_start, window_end,
    processing_cap_seconds, ems_agencies, law_agencies, excluded_call_types
    and, when used, excluded_dispatcher_identity and discipline.
    """
    taker_filter = ""
    if dispatcher_calls:
        taker_filter = (
            "AND BTRIM(call_taker) <> '' "
            "AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s"
        )
    discipline_filter = ""
    if discipline:
        discipline_filter = (
            "AND EXISTS (SELECT 1 FROM action_stamps "
            "WHERE action_stamps.cfs_number = lcdash_analytics.calls.cfs_number "
            "AND action_stamps.discipline = %(discipline)s)"
        )
    return f"""
        WITH {ACTION_STAMPS_SQL},
        selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                COALESCE(
                    NULLIF(call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(call_taker)
                ) AS dispatcher_key,
                call_received_at,
                is_scheduled,
                regexp_replace(UPPER(incident_description), '[^A-Z0-9]', '', 'g')
                    = ANY(%(excluded_call_types)s) AS excluded_type,
                source_collected_at{extra_columns}
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              {taker_filter}
              {discipline_filter}
        ),
        call_milestones AS (
            SELECT
                selected_calls.cfs_number,
                MIN(action_stamps.stamp_at) FILTER (
                    WHERE action_stamps.stamp_at >= selected_calls.call_received_at
                ) AS first_action_at,
                MIN(action_stamps.stamp_at) FILTER (
                    WHERE action_stamps.kind <> 'stacked'
                      AND action_stamps.stamp_at >= selected_calls.call_received_at
                ) AS first_dispatch_at,
                MIN(action_stamps.stamp_at) FILTER (
                    WHERE action_stamps.kind = 'on_scene'
                      AND action_stamps.stamp_at >= selected_calls.call_received_at
                ) AS first_on_scene_at
            FROM selected_calls
            LEFT JOIN action_stamps
                ON action_stamps.cfs_number = selected_calls.cfs_number
            GROUP BY selected_calls.cfs_number
        ),
        call_processing AS (
            SELECT
                selected_calls.*,
                call_milestones.first_action_at,
                call_milestones.first_dispatch_at,
                call_milestones.first_on_scene_at,
                EXTRACT(
                    EPOCH FROM call_milestones.first_action_at - selected_calls.call_received_at
                ) AS processing_seconds,
                CASE
                    WHEN call_milestones.first_dispatch_at > call_milestones.first_action_at
                    THEN EXTRACT(
                        EPOCH FROM call_milestones.first_dispatch_at
                            - call_milestones.first_action_at
                    )
                END AS hold_seconds,
                CASE
                    WHEN selected_calls.excluded_type THEN 'excluded_type'
                    WHEN selected_calls.is_scheduled THEN 'scheduled'
                    WHEN call_milestones.first_action_at IS NULL THEN 'no_stamp'
                    WHEN call_milestones.first_action_at
                        > selected_calls.call_received_at
                          + make_interval(secs => %(processing_cap_seconds)s)
                        THEN 'over_cap'
                    ELSE 'timed'
                END AS timing_status
            FROM selected_calls
            JOIN call_milestones
                ON call_milestones.cfs_number = selected_calls.cfs_number
        )
    """


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
            "response_samples": 0,
            "response_eligible_calls": 0,
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
            "within_60_percent": 0,
            "within_90_percent": 0,
            "over_180_count": 0,
            "excluded_scheduled": 0,
            "excluded_no_stamp": 0,
            "excluded_over_cap": 0,
            "excluded_type": 0,
            "excluded_call_types": list(EXCLUDED_CALL_TYPES),
            "processing_cap_minutes": PROCESSING_CAP_MINUTES,
            "stacked_calls": 0,
            "average_hold": "—",
        },
        "dispatchers": [],
        "dispatcher_disciplines": [],
        "discipline_key": "",
        "discipline_label": DISCIPLINE_FILTER_LABELS[""],
        "discipline_options": [
            {"key": key, "label": label} for key, label in DISCIPLINE_FILTERS],
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
    discipline: str = "",
) -> dict:
    params = {
        "window_start": window.start_at,
        "window_end": window.end_at,
        "excluded_dispatcher_identity": EXCLUDED_DISPATCHER_IDENTITY,
        "processing_cap_seconds": PROCESSING_CAP_SECONDS,
        "ems_agencies": list(EMS_AGENCY_CODES),
        "law_agencies": list(LAW_AGENCY_CODES),
        "excluded_call_types": list(EXCLUDED_CALL_TYPE_KEYS),
        "discipline": discipline,
    }

    # Response time: received to the earliest on-scene stamp from either
    # record; scheduled calls (transports booked ahead) are left out.
    metrics_row = repository.fetchone(
        _call_processing_sql(dispatcher_calls=False)
        + """
        SELECT
            COUNT(*) AS total_calls,
            (
                SELECT COUNT(*)
                FROM lcdash_analytics.unit_responses AS unit_response
                JOIN selected_calls
                    ON selected_calls.cfs_number = unit_response.cfs_number
            ) AS unit_responses,
            AVG(processing_seconds) FILTER (WHERE timing_status = 'timed')
                AS average_processing_seconds,
            AVG(EXTRACT(EPOCH FROM first_on_scene_at - call_received_at))
                FILTER (WHERE first_on_scene_at IS NOT NULL AND NOT is_scheduled)
                AS average_response_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM first_on_scene_at - call_received_at)
            ) FILTER (WHERE first_on_scene_at IS NOT NULL AND NOT is_scheduled)
                AS median_response_seconds,
            COUNT(*) FILTER (WHERE first_on_scene_at IS NOT NULL AND NOT is_scheduled)
                AS response_samples,
            MAX(source_collected_at) AS latest_data_at,
            COUNT(*) FILTER (WHERE is_scheduled) AS scheduled_calls
        FROM call_processing
        """,
        params,
    ) or (0, 0, None, None, None, 0, None, 0)

    total_calls = int(metrics_row[0] or 0)
    scheduled_calls = int(metrics_row[7] or 0)
    response_samples = int(metrics_row[5] or 0)
    response_eligible_calls = max(total_calls - scheduled_calls, 0)
    response_coverage = (
        round((response_samples / response_eligible_calls) * 100)
        if response_eligible_calls
        else 0
    )

    # One timing_status column feeds every aggregate, so the count, the
    # average, the percentiles and the "≤ 90 s" / "> 3 min" figures always
    # describe the same calls.
    processing_aggregates_sql = """
            COUNT(*) FILTER (WHERE timing_status = 'timed')
                AS processing_samples,
            AVG(processing_seconds) FILTER (WHERE timing_status = 'timed')
                AS average_processing_seconds,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY processing_seconds)
                FILTER (WHERE timing_status = 'timed')
                AS median_processing_seconds,
            COUNT(*) FILTER (
                WHERE timing_status = 'timed' AND processing_seconds <= 90
            ) AS within_90_seconds,
            COUNT(*) FILTER (
                WHERE timing_status = 'timed' AND processing_seconds > 180
            ) AS over_180_seconds,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY processing_seconds)
                FILTER (WHERE timing_status = 'timed')
                AS p90_processing_seconds,
            COUNT(*) FILTER (WHERE timing_status = 'scheduled')
                AS excluded_scheduled,
            COUNT(*) FILTER (WHERE timing_status = 'no_stamp')
                AS excluded_no_stamp,
            COUNT(*) FILTER (WHERE timing_status = 'over_cap')
                AS excluded_over_cap,
            COUNT(*) FILTER (WHERE timing_status = 'timed' AND hold_seconds > 0)
                AS stacked_calls,
            AVG(hold_seconds) FILTER (WHERE timing_status = 'timed' AND hold_seconds > 0)
                AS average_hold_seconds,
            COUNT(*) FILTER (WHERE timing_status = 'excluded_type')
                AS excluded_type,
            COUNT(*) FILTER (
                WHERE timing_status = 'timed' AND processing_seconds <= 60
            ) AS within_60_seconds
    """

    # The dispatcher section alone follows the discipline filter: the calls
    # that discipline's units acted on, each timed to the first unit action
    # on the call. The page-level metrics and charts stay county-wide.
    dispatcher_summary_row = repository.fetchone(
        _call_processing_sql(dispatcher_calls=True, discipline=discipline)
        + """
        SELECT
            COUNT(*) AS calls_with_call_taker,
        """
        + processing_aggregates_sql
        + """
        FROM call_processing
        """,
        params,
    ) or (0, 0, None, None, 0, 0, None, 0, 0, 0, 0, None, 0, 0)

    dispatcher_rows = repository.fetchall(
        _call_processing_sql(dispatcher_calls=True, discipline=discipline)
        + """
        SELECT
            MAX(call_taker) AS call_taker,
            COUNT(*) AS calls_entered,
        """
        + processing_aggregates_sql
        + """
            ,
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
        # taker, calls, timed, average, median, within 90, over 180, p90,
        # scheduled, no stamp, over cap, stacked, average hold, excluded
        # type, within 60, ref
        calls_entered = int(row[1] or 0)
        processing_samples = int(row[2] or 0)
        within_90 = int(row[5] or 0)
        over_180 = int(row[6] or 0)
        within_60 = int(row[14] or 0)
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
                "dispatcher_ref": str(row[15] or ""),
                "processing_samples": processing_samples,
                "within_60_percent": round(
                    (within_60 / processing_samples) * 100
                ) if processing_samples else 0,
                "within_90_percent": round(
                    (within_90 / processing_samples) * 100
                ) if processing_samples else 0,
                "over_180_count": over_180,
                "excluded_scheduled": int(row[8] or 0),
                "excluded_no_stamp": int(row[9] or 0),
                "excluded_over_cap": int(row[10] or 0),
                # Timed calls whose first action was a Stack, and the mean
                # wait from that Stack to the first Assign / En route.
                "stacked_calls": int(row[11] or 0),
                "average_hold": _format_duration(row[12]),
                "excluded_type": int(row[13] or 0),
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
        "within_60_percent": round(
            (int(dispatcher_summary_row[13] or 0) / dispatcher_processing_samples) * 100
        ) if dispatcher_processing_samples else 0,
        "within_90_percent": round(
            (int(dispatcher_summary_row[4] or 0) / dispatcher_processing_samples) * 100
        ) if dispatcher_processing_samples else 0,
        "over_180_count": int(dispatcher_summary_row[5] or 0),
        "excluded_scheduled": int(dispatcher_summary_row[7] or 0),
        "excluded_no_stamp": int(dispatcher_summary_row[8] or 0),
        "excluded_over_cap": int(dispatcher_summary_row[9] or 0),
        "excluded_type": int(dispatcher_summary_row[12] or 0),
        "excluded_call_types": list(EXCLUDED_CALL_TYPES),
        "processing_cap_minutes": PROCESSING_CAP_MINUTES,
        "stacked_calls": int(dispatcher_summary_row[10] or 0),
        "average_hold": _format_duration(dispatcher_summary_row[11]),
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

    # Scheduled calls (transports booked ahead) would otherwise dominate the
    # unit's received-to-on-scene average.
    unit_rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT cfs_number, is_scheduled
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
                FILTER (
                    WHERE response_metrics.total_response_seconds IS NOT NULL
                      AND NOT selected_calls.is_scheduled
                )
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
            "response_samples": response_samples,
            "response_eligible_calls": response_eligible_calls,
            "scheduled_calls": scheduled_calls,
        },
        "dispatcher_metrics": dispatcher_metrics,
        "dispatchers": dispatchers,
        "dispatcher_disciplines": dispatcher_disciplines,
        "discipline_key": discipline,
        "discipline_label": DISCIPLINE_FILTER_LABELS[discipline],
        "discipline_options": [
            {"key": key, "label": label} for key, label in DISCIPLINE_FILTERS],
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

    A call counts once in each discipline whose units acted on it, measured
    from the CAD "call received" time to the earliest action (stacked,
    assigned, en route, staged or on scene) by any unit on the call, so a
    dispatcher can be compared on law, EMS and fire separately. The first
    unit assigned stops the clock for every discipline the call counts in:
    a discipline added to a call later is not charged the time before it was
    requested, which the warehouse does not hold. The discipline comes
    from the responding unit's roster agency or unit type (the same rule the
    station-discipline chart uses, which classifies every response); the
    per-agency record's ORI is a fallback. The same rules as the main table
    apply: EXCLUDED_CALL_TYPES natures, scheduled calls, calls with no unit
    stamp and intervals over the cap are left out.
    """
    rows = repository.fetchall(
        """
        WITH selected_calls AS (
            SELECT
                cfs_number,
                call_taker,
                COALESCE(
                    NULLIF(call_taker_unique_identifier, ''),
                    'legacy:' || UPPER(call_taker)
                ) AS dispatcher_key,
                call_received_at,
                is_scheduled
            FROM lcdash_analytics.calls
            WHERE call_received_at >= %(window_start)s
              AND call_received_at < %(window_end)s
              AND BTRIM(call_taker) <> ''
              AND UPPER(BTRIM(call_taker)) <> %(excluded_dispatcher_identity)s
              AND NOT (
                  regexp_replace(UPPER(incident_description), '[^A-Z0-9]', '', 'g')
                      = ANY(%(excluded_call_types)s)
              )
        ),
        discipline_stamps AS (
            SELECT
                timing.cfs_number,
                stamp.stamp_at,
                CASE
                    WHEN timing.agency = ANY(%(ems_agencies)s)
                      OR timing.unit_type LIKE 'EMS %%' THEN 'ems'
                    WHEN timing.agency LIKE 'FC %%'
                      OR timing.unit_type LIKE 'FIRE %%' THEN 'fire'
                    WHEN timing.agency = ANY(%(law_agencies)s)
                      OR timing.unit_type IN ('PATROL CAR', 'COUNTY ADMIN') THEN 'law'
                END AS discipline
            FROM (
                SELECT
                    cfs_number,
                    UPPER(agency_ori) AS agency,
                    '' AS unit_type,
                    dispatched_at,
                    enroute_at,
                    staged_at,
                    on_scene_at
                FROM lcdash_analytics.call_agency_times
                UNION ALL
                SELECT
                    unit_response.cfs_number,
                    UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')),
                    UPPER(COALESCE(
                        NULLIF(unit_record.unit_type, ''),
                        NULLIF(unit_response.unit_type, ''),
                        ''
                    )),
                    unit_response.dispatched_at,
                    unit_response.enroute_at,
                    unit_response.staged_at,
                    unit_response.on_scene_at
                FROM lcdash_analytics.unit_responses AS unit_response
                LEFT JOIN lcdash_analytics.units AS unit_record
                    ON unit_record.unit_number = unit_response.unit_number
                UNION ALL
                SELECT
                    status_event.cfs_number,
                    UPPER(COALESCE(NULLIF(unit_record.agency, ''), '')),
                    UPPER(COALESCE(NULLIF(unit_record.unit_type, ''), '')),
                    status_event.occurred_at,
                    NULL::timestamptz,
                    NULL::timestamptz,
                    NULL::timestamptz
                FROM lcdash_analytics.unit_status_events AS status_event
                LEFT JOIN lcdash_analytics.units AS unit_record
                    ON unit_record.unit_number = status_event.unit_number
                WHERE status_event.event_kind IN ('stacked', 'assigned', 'enroute', 'staged', 'on_scene')
            ) AS timing
            CROSS JOIN LATERAL (
                VALUES
                    (timing.dispatched_at),
                    (timing.enroute_at),
                    (timing.staged_at),
                    (timing.on_scene_at)
            ) AS stamp(stamp_at)
            WHERE stamp.stamp_at IS NOT NULL
        ),
        call_first_action AS (
            SELECT
                selected_calls.cfs_number,
                MIN(discipline_stamps.stamp_at) FILTER (
                    WHERE discipline_stamps.stamp_at >= selected_calls.call_received_at
                ) AS first_action_at
            FROM selected_calls
            JOIN discipline_stamps
                ON discipline_stamps.cfs_number = selected_calls.cfs_number
            GROUP BY selected_calls.cfs_number
        ),
        discipline_dispatch AS (
            SELECT DISTINCT
                selected_calls.cfs_number,
                selected_calls.call_taker,
                selected_calls.dispatcher_key,
                selected_calls.call_received_at,
                selected_calls.is_scheduled,
                discipline_stamps.discipline,
                call_first_action.first_action_at
            FROM selected_calls
            JOIN discipline_stamps
                ON discipline_stamps.cfs_number = selected_calls.cfs_number
            JOIN call_first_action
                ON call_first_action.cfs_number = selected_calls.cfs_number
            WHERE discipline_stamps.discipline IS NOT NULL
        ),
        timed AS (
            SELECT
                discipline,
                dispatcher_key,
                call_taker,
                EXTRACT(EPOCH FROM first_action_at - call_received_at) AS seconds
            FROM discipline_dispatch
            WHERE first_action_at IS NOT NULL
              AND NOT is_scheduled
              AND first_action_at <= call_received_at
                  + make_interval(secs => %(processing_cap_seconds)s)
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
            "processing_cap_seconds": PROCESSING_CAP_SECONDS,
            **params,
            "ems_agencies": list(EMS_AGENCY_CODES),
            "law_agencies": list(LAW_AGENCY_CODES),
            "excluded_call_types": list(EXCLUDED_CALL_TYPE_KEYS),
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
    discipline: str = "",
) -> dict:
    """Calls the dispatcher entered that took over three minutes to a unit action.

    ``calls`` is the same population as the "> 3 Min" count in the dispatcher
    table (scheduled calls and intervals over the cap excluded). Calls over
    the cap are returned separately in ``over_cap`` so nothing is hidden;
    they are not in any average. Each row names the first unit action (the
    event and the unit or agency) and, for a stacked call, the unit's hold,
    so the interval can be checked against the CAD report. Only fields the
    analytics store already shows elsewhere (CFS
    number, incident type, priority, responding agency, unit number and the
    times); no location and no narrative.
    """
    rows = repository.fetchall(
        _call_processing_sql(
            dispatcher_calls=True,
            discipline=discipline,
            extra_columns=(
                ",\n                incident_code,"
                "\n                incident_description,"
                "\n                priority,"
                "\n                response_agency,"
                "\n                dispatch_agency"
            ),
        )
        + """
        , first_actions AS (
            SELECT DISTINCT ON (action_stamps.cfs_number)
                action_stamps.cfs_number,
                action_stamps.kind,
                action_stamps.party,
                action_stamps.source
            FROM action_stamps
            JOIN selected_calls
                ON selected_calls.cfs_number = action_stamps.cfs_number
            WHERE action_stamps.stamp_at >= selected_calls.call_received_at
            ORDER BY
                action_stamps.cfs_number,
                action_stamps.stamp_at,
                action_stamps.stamp_rank,
                (action_stamps.source = 'unit') DESC
        )
        SELECT
            call_processing.cfs_number,
            call_processing.call_received_at,
            call_processing.processing_seconds AS dispatch_seconds,
            call_processing.incident_description,
            call_processing.incident_code,
            call_processing.priority,
            COALESCE(
                NULLIF(call_processing.response_agency, ''),
                NULLIF(call_processing.dispatch_agency, ''),
                ''
            ) AS agency,
            first_actions.kind AS first_action_kind,
            first_actions.party AS first_action_party,
            first_actions.source AS first_action_source,
            call_processing.timing_status,
            COUNT(*) FILTER (WHERE call_processing.timing_status = 'timed') OVER ()
                AS total_timed,
            COUNT(*) FILTER (WHERE call_processing.timing_status = 'over_cap') OVER ()
                AS total_over_cap,
            call_processing.hold_seconds
        FROM call_processing
        LEFT JOIN first_actions
            ON first_actions.cfs_number = call_processing.cfs_number
        WHERE LEFT(
                encode(sha256(convert_to(call_processing.dispatcher_key, 'UTF8')), 'hex'),
                16
              ) = %(dispatcher_ref)s
          AND call_processing.timing_status IN ('timed', 'over_cap')
          AND call_processing.processing_seconds > %(threshold_seconds)s
        ORDER BY dispatch_seconds DESC, call_received_at DESC
        LIMIT %(row_limit)s
        """,
        {
            "window_start": window.start_at,
            "window_end": window.end_at,
            "excluded_dispatcher_identity": EXCLUDED_DISPATCHER_IDENTITY,
            "processing_cap_seconds": PROCESSING_CAP_SECONDS,
            "ems_agencies": list(EMS_AGENCY_CODES),
            "law_agencies": list(LAW_AGENCY_CODES),
            "excluded_call_types": list(EXCLUDED_CALL_TYPE_KEYS),
            "discipline": discipline,
            "dispatcher_ref": dispatcher_ref,
            "threshold_seconds": SLOW_CALL_THRESHOLD_SECONDS,
            "row_limit": SLOW_CALL_LIMIT,
        },
    )
    calls = []
    over_cap_calls = []
    for row in rows:
        received = row[1]
        kind = str(row[7] or "")
        entry = {
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
            "first_action": FIRST_ACTION_LABELS.get(kind, kind.replace("_", " ").title()),
            "first_action_by": str(row[8] or "").strip(),
            # Stack to the first Assign / En route, when the call was stacked
            # first: the unit's wait, shown apart from the dispatcher's time.
            "hold": _format_duration(row[13]) if row[13] else "",
        }
        if str(row[10] or "") == "over_cap":
            over_cap_calls.append(entry)
        else:
            calls.append(entry)
    total = int(rows[0][11] or 0) if rows else 0
    total_over_cap = int(rows[0][12] or 0) if rows else 0
    return {
        "threshold_seconds": SLOW_CALL_THRESHOLD_SECONDS,
        "cap_minutes": PROCESSING_CAP_MINUTES,
        "total": total,
        "shown": len(calls),
        "calls": calls,
        "over_cap": {
            "total": total_over_cap,
            "shown": len(over_cap_calls),
            "calls": over_cap_calls,
        },
    }


def get_dispatcher_slow_calls(
    dispatcher_ref: str,
    period: str = DEFAULT_PERIOD,
    start: str = "",
    end: str = "",
    tenant_context: TenantContext | None = None,
    discipline: str = "",
) -> dict:
    """The calls behind a dispatcher's "> 3 Min" count, longest first."""
    if not DISPATCHER_REF_PATTERN.fullmatch(str(dispatcher_ref or "")):
        raise AnalyticsReferenceError("That dispatcher reference is not valid.")
    discipline = resolve_discipline(discipline)

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
        "cap_minutes": PROCESSING_CAP_MINUTES,
        "total": 0,
        "shown": 0,
        "calls": [],
        "over_cap": {"total": 0, "shown": 0, "calls": []},
    }
    if not analytics_database_is_configured():
        return {**empty, "message": "PostgreSQL analytics is not configured on this machine."}
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            result = _query_slow_calls(repository, window, dispatcher_ref, discipline)
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
    discipline: str = "",
) -> dict:
    discipline = resolve_discipline(discipline)
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
            return _query_overview(repository, window, county_profile, discipline)
    except AnalyticsDatabaseError:
        return _empty_overview(
            window,
            "PostgreSQL analytics is configured but unavailable.",
        )
