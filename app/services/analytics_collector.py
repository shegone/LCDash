import time
from datetime import datetime, timedelta, timezone

from app.config.settings import settings
from app.services.analytics_database import AnalyticsRepository
from app.services.analytics_models import normalize_analytics_bundle
from app.services.centralsquare import CentralSquareAPIError, CentralSquareClient
from app.services.unit_service import get_all_units


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def discover_completed_calls(
    client: CentralSquareClient,
    window_start: datetime,
    window_end: datetime,
    max_calls: int,
    page_size: int = 100,
) -> tuple[list, bool]:
    page_size = min(max(page_size, 1), 100)
    max_calls = max(max_calls, 1)
    search_body = {
        "RecordClosedFrom": _utc_datetime(window_start).isoformat(),
        "RecordClosedTo": _utc_datetime(window_end).isoformat(),
        "CurrentlyActive": False,
        "OrderByField": "Closed",
        "OrderByDirection": "Ascending",
    }
    calls_by_number = {}
    skip = 0
    truncated = False

    while len(calls_by_number) < max_calls:
        result = client.search_cfs_core(
            search_body,
            skip=skip,
            limit=page_size,
        )
        page = (
            result.get("cfs_cores")
            or result.get("CFSCore")
            or result.get("CFSCoreReadMultiple")
            or []
        )
        page = [call for call in page if isinstance(call, dict)]
        if not page:
            break

        for call in page:
            cfs_number = str(call.get("CFSNumber") or "").strip()
            if cfs_number:
                calls_by_number[cfs_number] = call
            if len(calls_by_number) >= max_calls:
                truncated = len(page) == page_size
                break

        if len(page) < page_size or len(calls_by_number) >= max_calls:
            break
        skip += page_size

    return list(calls_by_number.values()), truncated


def build_roster_map(client: CentralSquareClient) -> dict:
    try:
        roster = get_all_units(client=client)
    except CentralSquareAPIError:
        return {}

    return {
        str(unit.get("unit_number") or "").upper(): unit
        for unit in roster
        if str(unit.get("unit_number") or "").strip()
    }


def run_analytics_sync(
    repository: AnalyticsRepository | None = None,
    client: CentralSquareClient | None = None,
    now: datetime | None = None,
    lookback_hours: int | None = None,
    overlap_minutes: int | None = None,
    max_calls: int | None = None,
    request_delay_ms: int | None = None,
    sleep_function=time.sleep,
) -> dict:
    now = _utc_datetime(now or datetime.now(timezone.utc))
    lookback_hours = max(
        lookback_hours or settings.analytics_initial_lookback_hours,
        1,
    )
    overlap_minutes = max(
        settings.analytics_overlap_minutes
        if overlap_minutes is None
        else overlap_minutes,
        0,
    )
    max_calls = max(max_calls or settings.analytics_max_calls_per_run, 1)
    request_delay_ms = max(
        settings.analytics_request_delay_ms
        if request_delay_ms is None
        else request_delay_ms,
        0,
    )
    client = client or CentralSquareClient()
    repository = repository or AnalyticsRepository()

    with repository as database:
        database.initialize_schema()
        previous_sync = database.get_sync_timestamp()
        if previous_sync:
            window_start = _utc_datetime(previous_sync) - timedelta(
                minutes=overlap_minutes
            )
        else:
            window_start = now - timedelta(hours=lookback_hours)
        window_end = now
        run_id = database.start_sync_run(now, window_start, window_end)

        calls_stored = 0
        failures = []
        discovered = []
        truncated = False

        try:
            discovered, truncated = discover_completed_calls(
                client=client,
                window_start=window_start,
                window_end=window_end,
                max_calls=max_calls,
            )
            roster_by_unit = build_roster_map(client)

            for raw_call in discovered:
                cfs_number = str(raw_call.get("CFSNumber") or "").strip()
                if not cfs_number:
                    continue
                try:
                    raw_analytics = client.get_cfs_analytics(cfs_number)
                    bundle = normalize_analytics_bundle(
                        raw_call=raw_call,
                        raw_analytics=raw_analytics,
                        roster_by_unit=roster_by_unit,
                        collected_at=now,
                    )
                    database.upsert_bundle(bundle)
                    calls_stored += 1
                except (CentralSquareAPIError, ValueError) as exc:
                    failures.append(f"{cfs_number}: {type(exc).__name__}")

                if request_delay_ms:
                    sleep_function(request_delay_ms / 1000)

            status = "complete"
            if failures or truncated:
                status = "partial"
            if status == "complete":
                database.set_sync_timestamp(window_end)

            error_parts = failures[:10]
            if truncated:
                error_parts.append(
                    f"Run reached ANALYTICS_MAX_CALLS_PER_RUN={max_calls}."
                )
            database.complete_sync_run(
                run_id=run_id,
                completed_at=datetime.now(timezone.utc),
                status=status,
                calls_discovered=len(discovered),
                calls_stored=calls_stored,
                analytics_failures=len(failures),
                error_summary="; ".join(error_parts),
            )
        except Exception as exc:
            database.complete_sync_run(
                run_id=run_id,
                completed_at=datetime.now(timezone.utc),
                status="failed",
                calls_discovered=len(discovered),
                calls_stored=calls_stored,
                analytics_failures=len(failures),
                error_summary=type(exc).__name__,
            )
            raise

    return {
        "status": status,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "calls_discovered": len(discovered),
        "calls_stored": calls_stored,
        "analytics_failures": len(failures),
        "truncated": truncated,
    }
