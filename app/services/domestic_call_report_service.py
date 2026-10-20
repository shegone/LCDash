"""Domestic-related call percentage, straight from CentralSquare, read only.

A call counts as domestic-related when its primary incident code (the call
type) has a code, abbreviation, or description containing one of the match
terms -- by default ``domestic``. The matched codes are always returned with
their counts, so the definition can be checked against the real code table
instead of trusted.

What this does and does not do:

* The only CentralSquare request made is ``POST /cfs_core/search``, one calendar
  month at a time, through the pilot's own allowlisted read-only connector and
  its own Secrets Manager credential. Nothing is written anywhere.
* Classification happens here, on the rows returned. It deliberately does not
  send an ``IncidentCode`` filter: CentralSquare's firewall has been seen
  refusing that parameter (HTTP 200 with an HTML page), and counting a month
  needs the denominator anyway.
* Only counts leave this module. No call number, address, narrative, caller, or
  patient detail is kept after a call has been counted.
* It is deliberately gentle: one job at a time, sequential pages with a short
  pause, a page cap per month, and finished past months are remembered for a day
  so a re-run only re-reads the current month.

The same functions back the command-line script
(``scripts/domestic_call_percentage.py``) so the two cannot disagree.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from threading import Lock, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

REPORT_KEY = "domestic_calls_year_to_date"
TIMEZONE_NAME = "America/New_York"
DEFAULT_TERMS = ("domestic",)
PAGE_SIZE = 100
# Per calendar month. Logan County is far below this (a few thousand calls a
# month); reaching it means something is wrong, so stop rather than loop.
MAX_PAGES_PER_MONTH = 200
PAGE_DELAY_SECONDS = 0.2
PAST_MONTH_CACHE_SECONDS = 24 * 60 * 60
MAX_OTHER_CODES_SHOWN = 15
_MAX_RETAINED_JOBS = 5

Search = Callable[..., Any]


class DomesticReportError(Exception):
    """Raised when the report cannot be completed safely."""


class DomesticReportBusyError(DomesticReportError):
    """Raised when another report is already reading CentralSquare."""


# --------------------------------------------------------------------------
# Pure helpers (no I/O)
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _code_fields(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """(code, abbreviation, description) of one attached incident code."""

    inner = row.get("IncidentCode")
    incident = inner if isinstance(inner, Mapping) else row
    return (
        _text(incident.get("Code")),
        _text(incident.get("Abbreviation")),
        _text(
            incident.get("Description")
            or incident.get("Name")
            or incident.get("FullDescription")
        ),
    )


def incident_rows(call: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = call.get("IncidentCode") or []
    if not isinstance(rows, list):
        rows = [rows]
    return [row for row in rows if isinstance(row, Mapping)]


def primary_incident_row(call: Mapping[str, Any]) -> Mapping[str, Any] | None:
    rows = incident_rows(call)
    if not rows:
        return None
    return next((row for row in rows if row.get("IsPrimary")), rows[0])


def code_label(row: Mapping[str, Any] | None) -> str:
    """A stable, readable label for counting: ``CODE - Description``."""

    if row is None:
        return "(no incident code)"
    code, abbreviation, description = _code_fields(row)
    head = code or abbreviation or "(unlabelled)"
    return f"{head} - {description}" if description else head


def matches(row: Mapping[str, Any] | None, terms: tuple[str, ...]) -> bool:
    if row is None:
        return False
    haystack = " ".join(_code_fields(row)).lower()
    return any(term.lower() in haystack for term in terms)


def page_calls(result: Any) -> list[Mapping[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    rows = (
        result.get("cfs_cores")
        or result.get("CFSCore")
        or result.get("CFSCoreReadMultiple")
        or result.get("calls")
        or result.get("items")
        or []
    )
    return [row for row in rows if isinstance(row, Mapping)]


def month_windows(start: datetime, end: datetime):
    """Consecutive [start, end) windows, each at most one calendar month."""

    cursor = start
    while cursor < end:
        year, month = (
            (cursor.year + 1, 1) if cursor.month == 12 else (cursor.year, cursor.month + 1)
        )
        boundary = cursor.replace(
            year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        yield cursor, min(boundary, end)
        cursor = boundary


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


# --------------------------------------------------------------------------
# Reading CentralSquare
# --------------------------------------------------------------------------


def scan_month(
    search: Search,
    start: datetime,
    end: datetime,
    terms: tuple[str, ...] = DEFAULT_TERMS,
    *,
    any_incident: bool = False,
    on_page: Callable[[int, int], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Count one window. Returns aggregates only; never retains a call."""

    body = {
        "RecordCreatedFrom": start.isoformat(),
        "RecordCreatedTo": end.isoformat(),
        "OrderByField": "Created",
        "OrderByDirection": "Ascending",
    }
    seen: set[str] = set()
    matched: Counter[str] = Counter()
    other: Counter[str] = Counter()
    total = domestic = pages = 0

    for page_index in range(MAX_PAGES_PER_MONTH):
        page = page_calls(
            search(body, skip=page_index * PAGE_SIZE, limit=PAGE_SIZE)
        )
        pages += 1
        for call in page:
            cfs = _text(call.get("CFSNumber") or call.get("cfs_number"))
            if not cfs or cfs in seen:
                continue
            seen.add(cfs)
            primary = primary_incident_row(call)
            if any_incident:
                hits = [row for row in incident_rows(call) if matches(row, terms)]
                is_domestic = bool(hits)
                label = code_label(hits[0]) if hits else code_label(primary)
            else:
                is_domestic = matches(primary, terms)
                label = code_label(primary)
            total += 1
            if is_domestic:
                domestic += 1
                matched[label] += 1
            else:
                other[label] += 1
        if on_page:
            on_page(pages, len(seen))
        if len(page) < PAGE_SIZE:
            break
        sleep(PAGE_DELAY_SECONDS)
    else:
        raise DomesticReportError(
            f"{start.strftime('%Y-%m')} exceeded {MAX_PAGES_PER_MONTH} pages; stopping."
        )

    return {
        "month": start.strftime("%Y-%m"),
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "total_calls": total,
        "domestic_calls": domestic,
        "percent_domestic": _percent(domestic, total),
        "pages_scanned": pages,
        "matched": dict(matched),
        "other": dict(other),
    }


def summarize(
    search: Search,
    start: datetime,
    end: datetime,
    terms: tuple[str, ...] = DEFAULT_TERMS,
    *,
    any_incident: bool = False,
    now: datetime | None = None,
    month_cache: dict | None = None,
    cache_key: str = "",
    progress: Callable[[dict], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """The whole range, month by month, as aggregates only.

    ``month_cache`` (optional) remembers finished past months for
    ``PAST_MONTH_CACHE_SECONDS``; the month containing ``now`` is always read
    fresh because it is still changing.
    """

    now = now or datetime.now(start.tzinfo)
    months: list[dict] = []
    matched_total: Counter[str] = Counter()
    other_total: Counter[str] = Counter()
    pages_scanned = reused = 0
    running_pages = running_calls = 0

    for window_start, window_end in month_windows(start, end):
        key = (cache_key, window_start.strftime("%Y-%m"), terms, any_incident)
        finished = window_end <= now
        cached = month_cache.get(key) if month_cache is not None else None
        if (
            cached is not None
            and finished
            and time.time() - cached[0] < PAST_MONTH_CACHE_SECONDS
        ):
            month = dict(cached[1])
            was_reused = True
            reused += 1
        else:
            base_pages, base_calls = running_pages, running_calls
            month = scan_month(
                search,
                window_start,
                window_end,
                terms,
                any_incident=any_incident,
                on_page=(
                    (lambda pages, calls, bp=base_pages, bc=base_calls, label=window_start.strftime("%Y-%m"):
                        progress({"month": label, "pages": bp + pages, "calls": bc + calls, "done": False}))
                    if progress
                    else None
                ),
                sleep=sleep,
            )
            was_reused = False
            pages_scanned += month["pages_scanned"]
            if month_cache is not None and finished:
                month_cache[key] = (time.time(), dict(month))
        running_pages += month["pages_scanned"] if not was_reused else 0
        running_calls += month["total_calls"]
        matched_total.update(month["matched"])
        other_total.update(month["other"])
        months.append(
            {
                "month": month["month"],
                "month_label": window_start.strftime("%B %Y"),
                "from": month["from"],
                "to": month["to"],
                "total_calls": month["total_calls"],
                "domestic_calls": month["domestic_calls"],
                "percent_domestic": month["percent_domestic"],
                "in_progress": not finished,
                "reused": was_reused,
            }
        )
        if progress:
            progress(
                {"month": month["month"], "pages": running_pages, "calls": running_calls, "done": True}
            )

    total_calls = sum(m["total_calls"] for m in months)
    domestic_calls = sum(m["domestic_calls"] for m in months)
    return {
        "report_key": REPORT_KEY,
        "title": "Domestic-Related Calls",
        "from": start.date().isoformat(),
        "through": (end - timedelta(days=1)).date().isoformat(),
        "timezone": str(start.tzinfo),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "match_terms": list(terms),
        "counted_by": "any attached incident code" if any_incident else "primary incident code",
        "total_calls": total_calls,
        "domestic_calls": domestic_calls,
        "percent_domestic": _percent(domestic_calls, total_calls),
        "months": months,
        "matched_codes": [
            {"code": label, "calls": count} for label, count in matched_total.most_common()
        ],
        "other_codes": [
            {"code": label, "calls": count}
            for label, count in other_total.most_common(MAX_OTHER_CODES_SHOWN)
        ],
        "other_code_kinds": len(other_total),
        "quality": {
            "pages_scanned": pages_scanned,
            "calls_deduplicated": total_calls,
            "months_reused_from_earlier_run": reused,
        },
    }


def year_to_date_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """January 1 of this year through the end of today, New York time."""

    tz = ZoneInfo(TIMEZONE_NAME)
    now = (now or datetime.now(tz)).astimezone(tz)
    start = datetime(now.year, 1, 1, tzinfo=tz)
    tomorrow = date.fromordinal(now.date().toordinal() + 1)
    return start, datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)


# --------------------------------------------------------------------------
# Background job (one at a time, in memory)
# --------------------------------------------------------------------------

_JOBS: "OrderedDict[str, dict]" = OrderedDict()
_JOB_LOCK = Lock()
_MONTH_CACHE: dict = {}


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _run(job_id: str, connector_provider: Any, tenant_id: str) -> None:
    def progress(update: dict) -> None:
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["current_month"] = update["month"]
                job["pages_scanned"] = update["pages"]
                job["records_scanned"] = update["calls"]

    with _JOB_LOCK:
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        connector = getattr(connector_provider, "connector", connector_provider)
        if connector is None:
            raise DomesticReportError("Live CAD is not enabled for this tenant.")
        start, end = year_to_date_window()
        result = summarize(
            connector.search_calls,
            start,
            end,
            month_cache=_MONTH_CACHE,
            cache_key=tenant_id,
            progress=progress,
        )
        with _JOB_LOCK:
            _JOBS[job_id].update(
                {
                    "status": "complete",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                }
            )
    except Exception as exc:  # noqa: BLE001 - nothing here may leak to the browser
        # Class name only: connector errors can carry vendor response text.
        logger.warning("Domestic call report failed: %s", type(exc).__name__)
        with _JOB_LOCK:
            _JOBS[job_id].update(
                {
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "message": (
                        "The report could not be completed. CentralSquare may be "
                        "unavailable or rate limiting; try again in a few minutes."
                    ),
                }
            )


def start_domestic_job(connector_provider: Any, tenant_id: str) -> dict:
    """Start a run, or return the one already running for this tenant."""

    with _JOB_LOCK:
        for job in _JOBS.values():
            if job["status"] in {"queued", "running"}:
                if job["_tenant_id"] == tenant_id:
                    return _public(job)
                raise DomesticReportBusyError("Another report is already running.")
        job_id = str(uuid4())
        job = {
            "job_id": job_id,
            "report_key": REPORT_KEY,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": "",
            "completed_at": "",
            "current_month": "",
            "pages_scanned": 0,
            "records_scanned": 0,
            "message": "",
            "result": None,
            "_tenant_id": tenant_id,
        }
        _JOBS[job_id] = job
        while len(_JOBS) > _MAX_RETAINED_JOBS:
            _JOBS.popitem(last=False)
    Thread(target=_run, args=(job_id, connector_provider, tenant_id), daemon=True).start()
    return _public(job)


def get_domestic_job(job_id: str, tenant_id: str) -> dict | None:
    with _JOB_LOCK:
        job = _JOBS.get(str(job_id or ""))
        if job is None or job["_tenant_id"] != tenant_id:
            return None
        return _public(job)
