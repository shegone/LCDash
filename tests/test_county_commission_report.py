from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.main import app
from app.services.county_commission_report_service import (
    CountyCommissionReportBusyError,
    build_county_commission_pdf,
    build_county_commission_report,
    resolve_report_month,
)


EXPECTED_JUNE_COUNTS = {
    "FC 100": 33,
    "FC 200": 36,
    "FC 300": 97,
    "FC 400": 27,
    "FC 500": 0,
    "FC 600": 5,
    "FC 700": 62,
    "FC 800": 16,
    "FC 900": 44,
    "FC 1000": 135,
    "FC 1100": 30,
    "FC 1200": 14,
    "LCSO": 794,
    "DPS": 308,
    "LPD": 385,
    "MPD": 31,
    "CPD": 132,
    "WLPD": 0,
    "MHPD": 1,
    "LEASA": 1985,
}


def unit(agency_code):
    return {"Agency": {"Abbreviation": agency_code}}


class FakeCentralSquareClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def search_cfs_core(self, search_body, skip=0, limit=100):
        self.calls.append((search_body, skip, limit))
        index = skip // limit
        return {"cfs_cores": self.pages[index] if index < len(self.pages) else []}


def june_result():
    report_units = []
    for agency_code, count in EXPECTED_JUNE_COUNTS.items():
        report_units.extend(unit(agency_code) for _ in range(count))
    report_units.extend([unit("DNR"), unit("DOF")])
    client = FakeCentralSquareClient([[{"CFSNumber": "JUNE-1", "Unit": report_units}]])
    return build_county_commission_report(
        "2026-06",
        client=client,
        now=datetime(2026, 8, 3, tzinfo=ZoneInfo("America/New_York")),
    ), client


def test_month_window_uses_local_inclusive_exclusive_boundaries():
    window = resolve_report_month(
        "2026-06",
        now=datetime(2026, 8, 3, tzinfo=ZoneInfo("America/New_York")),
    )
    assert window["label"] == "June 2026"
    assert window["start_at"].isoformat() == "2026-06-01T00:00:00-04:00"
    assert window["end_at"].isoformat() == "2026-07-01T00:00:00-04:00"


def test_future_month_is_rejected():
    with pytest.raises(ValueError, match="future"):
        resolve_report_month(
            "2026-09",
            now=datetime(2026, 8, 3, tzinfo=ZoneInfo("America/New_York")),
        )


def test_direct_query_reconciles_known_june_workbook_totals():
    report, client = june_result()

    assert report["fire_total"] == 499
    assert report["law_total"] == 1651
    assert report["leasa_total"] == 1985
    assert report["phone_totals_included"] is False
    assert report["quality"]["excluded_assignments"] == 2
    assert report["write_access"] is False
    assert report["fire"][0] == {
        "agency_code": "FC 100",
        "department": "Henlawson",
        "runs": 33,
    }
    search_body, skip, limit = client.calls[0]
    assert search_body["RecordCreatedFrom"] == "2026-06-01T00:00:00-04:00"
    assert search_body["RecordCreatedTo"] == "2026-07-01T00:00:00-04:00"
    assert "CurrentlyActive" not in search_body
    assert (skip, limit) == (0, 100)


def test_duplicate_cfs_is_not_counted_twice_across_pages():
    first_page = [
        {"CFSNumber": f"CFS-{index}", "Unit": [unit("LEASA")]}
        for index in range(100)
    ]
    second_page = [
        {"CFSNumber": "CFS-0", "Unit": [unit("LEASA")]},
        {"CFSNumber": "CFS-100", "Unit": [unit("LEASA")]},
    ]
    result = build_county_commission_report(
        "2026-06",
        client=FakeCentralSquareClient([first_page, second_page]),
        now=datetime(2026, 8, 3, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result["leasa_total"] == 101
    assert result["quality"]["records_deduplicated"] == 101
    assert result["quality"]["pages_scanned"] == 2


def test_county_commission_pdf_is_aggregate_and_readable():
    report, _client = june_result()
    pdf = build_county_commission_pdf(report)
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )
    assert pdf.startswith(b"%PDF")
    assert "County Commission Monthly Report" in text
    assert "FIRE TOTAL" in text
    assert "1,985" in text
    assert "Phone-system totals are intentionally excluded" in " ".join(text.split())


def test_reports_page_lists_county_commission_template():
    response = TestClient(app).get("/reports")
    assert response.status_code == 200
    assert "Pre-Built Reports" in response.text
    assert "County Commission Report" in response.text
    assert "Phone totals" in response.text
    assert "/static/js/lcdash-reports.js" in response.text


@patch("app.main.start_county_commission_job")
def test_start_report_job_endpoint(start_mock):
    start_mock.return_value = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "month": "2026-06",
        "status": "queued",
    }
    response = TestClient(app).post(
        "/api/reports/county-commission/jobs",
        json={"month": "2026-06"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    start_mock.assert_called_once_with("2026-06")


@patch("app.main.start_county_commission_job")
def test_start_report_job_returns_busy_status(start_mock):
    start_mock.side_effect = CountyCommissionReportBusyError(
        "Another monthly report is already running."
    )
    response = TestClient(app).post(
        "/api/reports/county-commission/jobs",
        json={"month": "2026-06"},
    )
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


@patch("app.main.get_county_commission_job")
def test_completed_report_job_downloads_pdf(job_mock):
    report, _client = june_result()
    job_mock.return_value = {"status": "complete", "result": report}
    response = TestClient(app).get(
        "/api/reports/county-commission/jobs/job-1/pdf"
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"
    assert "2026-06" in response.headers["content-disposition"]
