"""Direct, read-only CentralSquare county commission monthly reports."""
from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import datetime, timezone
from io import BytesIO
import re
from threading import Lock, Thread
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.county_profiles import resolve_county_profile
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import (
    TenantAuthorizationDenied,
    authorize_tenant_action,
)
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)
from app.integrations.contracts import ModuleCapability
from app.services.centralsquare import CentralSquareAPIError


DEFAULT_TIMEZONE_NAME = "America/New_York"
MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
MAX_PAGES = 100
PAGE_SIZE = 100

FIRE_AGENCIES = (
    ("FC 100", "Henlawson"),
    ("FC 200", "Man #2"),
    ("FC 300", "Chapmanville"),
    ("FC 400", "Lake"),
    ("FC 500", "Sharples"),
    ("FC 600", "Harts"),
    ("FC 700", "Cora"),
    ("FC 800", "Main Island Creek"),
    ("FC 900", "Verdunville"),
    ("FC 1000", "City Of Logan"),
    ("FC 1100", "Buffalo Creek"),
    ("FC 1200", "Town Of Man"),
)
LAW_AGENCIES = (
    ("LCSO", "Logan SO"),
    ("DPS", "Logan State Police"),
    ("LPD", "Logan City Police"),
    ("MPD", "Man Police"),
    ("CPD", "Chapmanville Police"),
    ("WLPD", "West Logan Police"),
    ("MHPD", "Mitchell Heights PD"),
)
EMS_AGENCIES = (("LEASA", "LEASA"),)
REPORT_AGENCY_CODES = {
    code for code, _label in FIRE_AGENCIES + LAW_AGENCIES + EMS_AGENCIES
}


class CountyCommissionReportError(Exception):
    """Raised when the aggregate report cannot be completed safely."""


class CountyCommissionReportBusyError(CountyCommissionReportError):
    """Raised when another monthly report is already querying CentralSquare."""


def _report_timezone(county_profile: CountyProfile | None = None) -> ZoneInfo:
    timezone_name = (
        county_profile.timezone
        if county_profile is not None
        else DEFAULT_TIMEZONE_NAME
    )
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("County profile timezone is unavailable.") from exc


def resolve_report_month(
    month: str,
    now: datetime | None = None,
    county_profile: CountyProfile | None = None,
) -> dict:
    match = MONTH_PATTERN.fullmatch(str(month or "").strip())
    if not match:
        raise ValueError("Month must use YYYY-MM format.")
    year, month_number = int(match.group(1)), int(match.group(2))
    if not 1 <= month_number <= 12:
        raise ValueError("Month must use YYYY-MM format.")
    local_timezone = _report_timezone(county_profile)
    start_at = datetime(year, month_number, 1, tzinfo=local_timezone)
    if month_number == 12:
        end_at = datetime(year + 1, 1, 1, tzinfo=local_timezone)
    else:
        end_at = datetime(year, month_number + 1, 1, tzinfo=local_timezone)
    current_month = (now or datetime.now(local_timezone)).astimezone(
        local_timezone
    ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_at > current_month:
        raise ValueError("A future month cannot be reported.")
    return {
        "key": f"{year:04d}-{month_number:02d}",
        "label": start_at.strftime("%B %Y"),
        "start_at": start_at,
        "end_at": end_at,
    }


def _page_calls(result: dict) -> list[dict]:
    rows = (
        result.get("cfs_cores")
        or result.get("CFSCore")
        or result.get("CFSCoreReadMultiple")
        or []
    )
    return [row for row in rows if isinstance(row, dict)]


def _unit_agency_code(unit: dict) -> str:
    agency = unit.get("Agency") or {}
    if isinstance(agency, dict):
        value = (
            agency.get("Abbreviation")
            or agency.get("Name")
            or agency.get("Description")
            or ""
        )
    else:
        value = agency
    return str(value or "").strip().upper()


def _rows(mapping: tuple[tuple[str, str], ...], counts: Counter) -> list[dict]:
    return [
        {"agency_code": code, "department": label, "runs": int(counts[code])}
        for code, label in mapping
    ]


def build_county_commission_report(
    month: str,
    *,
    client: CentralSquareClient | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    now: datetime | None = None,
    county_profile: CountyProfile | None = None,
    tenant_context: TenantContext | None = None,
) -> dict:
    """Query monthly CFS pages and count assigned-unit runs by agency."""
    if tenant_context is not None:
        if county_profile is not None:
            raise TenantAuthorizationDenied(
                "Trusted context and direct county profile cannot be combined."
            )
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.COUNTY_COMMISSION_REPORT,
            "read",
        )

    window = resolve_report_month(
        month,
        now=now,
        county_profile=county_profile,
    )
    client = client or CentralSquareClient()
    search_body = {
        "RecordCreatedFrom": window["start_at"].isoformat(),
        "RecordCreatedTo": window["end_at"].isoformat(),
        "OrderByField": "Created",
        "OrderByDirection": "Ascending",
    }
    counts: Counter[str] = Counter()
    seen_cfs: set[str] = set()
    pages_scanned = 0

    try:
        for page_index in range(MAX_PAGES):
            result = client.search_cfs_core(
                search_body,
                skip=page_index * PAGE_SIZE,
                limit=PAGE_SIZE,
            )
            page = _page_calls(result)
            pages_scanned += 1
            for call in page:
                cfs_number = str(call.get("CFSNumber") or "").strip()
                if not cfs_number or cfs_number in seen_cfs:
                    continue
                seen_cfs.add(cfs_number)
                for unit in call.get("Unit") or []:
                    if not isinstance(unit, dict):
                        continue
                    agency_code = _unit_agency_code(unit)
                    if agency_code:
                        counts[agency_code] += 1
            if progress_callback:
                progress_callback(pages_scanned, len(seen_cfs))
            if len(page) < PAGE_SIZE:
                break
        else:
            raise CountyCommissionReportError(
                "The monthly query exceeded the safe page limit."
            )
    except CentralSquareAPIError as exc:
        raise CountyCommissionReportError(
            "CentralSquare could not complete the monthly report query."
        ) from exc

    fire = _rows(FIRE_AGENCIES, counts)
    law = _rows(LAW_AGENCIES, counts)
    ems = _rows(EMS_AGENCIES, counts)
    excluded_assignments = sum(
        count for code, count in counts.items() if code not in REPORT_AGENCY_CODES
    )
    return {
        "available": True,
        "report_key": "county_commission_monthly",
        "title": "Logan County 911 County Commission Monthly Report",
        "month": window["key"],
        "month_label": window["label"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "CentralSquare cfs_core/search (read only)",
        "definition": "Assigned-unit runs grouped by agency",
        "phone_totals_included": False,
        "fire": fire,
        "fire_total": sum(row["runs"] for row in fire),
        "law": law,
        "law_total": sum(row["runs"] for row in law),
        "ems": ems,
        "leasa_total": sum(row["runs"] for row in ems),
        "quality": {
            "pages_scanned": pages_scanned,
            "records_deduplicated": len(seen_cfs),
            "report_assignments_counted": sum(
                count for code, count in counts.items() if code in REPORT_AGENCY_CODES
            ),
            "excluded_assignments": int(excluded_assignments),
        },
        "write_access": False,
    }


def build_county_commission_pdf(report: dict) -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=42,
        leftMargin=42,
        topMargin=38,
        bottomMargin=38,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Logan County 911", styles["Title"]),
        Paragraph("County Commission Monthly Report", styles["Heading2"]),
        Paragraph(report.get("month_label") or "Selected month", styles["Heading1"]),
        Spacer(1, 14),
    ]

    def report_table(title: str, rows: list[dict], total_label: str, color: str):
        data = [["Department", "Run Totals"]]
        data.extend(
            [[row["department"], f'{int(row["runs"]):,}'] for row in rows]
        )
        data.append([total_label, f'{sum(row["runs"] for row in rows):,}'])
        table = Table(data, colWidths=[4.5 * inch, 1.6 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(color)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9aa8b6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#eef3f7")]),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([Paragraph(title, styles["Heading3"]), table, Spacer(1, 12)])

    report_table("Fire Departments", report.get("fire") or [], "FIRE TOTAL", "#b84a48")
    report_table("Law Enforcement", report.get("law") or [], "LAW TOTAL", "#4f81bd")
    report_table("Emergency Medical Services", report.get("ems") or [], "LEASA TOTAL", "#84a946")
    story.extend([
        Spacer(1, 5),
        Paragraph(
            "Source: CentralSquare read-only monthly CFS query. Run totals count assigned units by agency. Phone-system totals are intentionally excluded.",
            styles["BodyText"],
        ),
    ])
    document.build(story)
    return output.getvalue()


_JOB_LOCK = Lock()
_JOBS: OrderedDict[str, dict] = OrderedDict()
_ACTIVE_BY_MONTH: dict[tuple[str, str], str] = {}
_MAX_RETAINED_JOBS = 20
_LEGACY_OWNER = ""


def _public_job(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key not in {"internal_error", "_owner_key", "_tenant_context"}
    }


def _run_job(
    job_id: str,
    month: str,
    tenant_context: TenantContext | None = None,
):
    def progress(pages: int, records: int):
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["pages_scanned"] = pages
                job["records_scanned"] = records

    with _JOB_LOCK:
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if tenant_context is None:
            result = build_county_commission_report(
                month,
                progress_callback=progress,
            )
        else:
            result = build_county_commission_report(
                month,
                progress_callback=progress,
                tenant_context=tenant_context,
            )
        with _JOB_LOCK:
            _JOBS[job_id].update({
                "status": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result": result,
                "pages_scanned": result["quality"]["pages_scanned"],
                "records_scanned": result["quality"]["records_deduplicated"],
            })
    except (CountyCommissionReportError, TenantAuthorizationDenied, ValueError):
        with _JOB_LOCK:
            _JOBS[job_id].update({
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "message": "The monthly report could not be completed.",
            })
    finally:
        with _JOB_LOCK:
            owner_month = (_JOBS[job_id]["_owner_key"], month)
            if _ACTIVE_BY_MONTH.get(owner_month) == job_id:
                _ACTIVE_BY_MONTH.pop(owner_month, None)


def start_county_commission_job(
    month: str,
    tenant_context: TenantContext | None = None,
) -> dict:
    county_profile: CountyProfile | None = None
    owner_key = _LEGACY_OWNER
    if tenant_context is not None:
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.COUNTY_COMMISSION_REPORT,
            "read",
        )
        owner_key = county_profile.tenant_id

    if county_profile is None:
        window = resolve_report_month(month)
    else:
        window = resolve_report_month(month, county_profile=county_profile)
    month_key = window["key"]
    owner_month = (owner_key, month_key)
    with _JOB_LOCK:
        active_id = _ACTIVE_BY_MONTH.get(owner_month)
        if active_id and active_id in _JOBS:
            return _public_job(_JOBS[active_id])
        if any(
            job.get("_owner_key") == owner_key
            and job.get("status") in {"queued", "running"}
            for job in _JOBS.values()
        ):
            raise CountyCommissionReportBusyError(
                "Another monthly report is already running."
            )
        job_id = str(uuid4())
        job = {
            "job_id": job_id,
            "report_key": "county_commission_monthly",
            "month": month_key,
            "month_label": window["label"],
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": "",
            "completed_at": "",
            "pages_scanned": 0,
            "records_scanned": 0,
            "message": "",
            "result": None,
            "_owner_key": owner_key,
            "_tenant_context": tenant_context,
        }
        _JOBS[job_id] = job
        _ACTIVE_BY_MONTH[owner_month] = job_id
        while len(_JOBS) > _MAX_RETAINED_JOBS:
            oldest_id, _oldest = _JOBS.popitem(last=False)
            for active_month, active_job_id in list(_ACTIVE_BY_MONTH.items()):
                if active_job_id == oldest_id:
                    _ACTIVE_BY_MONTH.pop(active_month, None)
    Thread(
        target=_run_job,
        args=(job_id, month_key, tenant_context),
        daemon=True,
    ).start()
    return _public_job(job)


def get_county_commission_job(
    job_id: str,
    tenant_context: TenantContext | None = None,
) -> dict | None:
    owner_key: str | None = None
    if tenant_context is not None:
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.COUNTY_COMMISSION_REPORT,
            "read",
        )
        owner_key = county_profile.tenant_id

    with _JOB_LOCK:
        job = _JOBS.get(str(job_id or ""))
        if job is not None and owner_key is not None:
            if job.get("_owner_key") != owner_key:
                return None
        return _public_job(job) if job else None
