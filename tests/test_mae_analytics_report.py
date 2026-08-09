from io import BytesIO

from pypdf import PdfReader

from app.services.mae_analytics_report_service import build_analytics_report


def test_aggregate_analytics_report_is_a_readable_pdf():
    report = build_analytics_report(
        {
            "period_label": "Last 7 days",
            "generated_at": "2026-08-03T10:00:00-04:00",
            "metrics": {
                "total_calls": 42,
                "unit_responses": 66,
                "average_processing": "00:01:10",
                "average_response": "00:06:20",
                "median_response": "00:05:45",
            },
            "daily_volume": [
                {"label": "Aug 01", "count": 12},
                {"label": "Aug 02", "count": 30},
            ],
            "agency_mix": [{"label": "911 Center / Administrative", "count": 22}],
            "incident_types": [{"label": "Medical Call", "count": 18}],
        }
    )

    assert report.startswith(b"%PDF")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(report)).pages)
    assert "MAE Analytics Report" in text
    assert "Total calls" in text
    assert "aggregate historical analytics only" in text


def test_report_heading_carries_the_county_logo_and_survives_without_it():
    """Ted asked for the dashboard's Logan 911 logo beside the PDF heading.

    The logo is decoration: a missing or unreadable file must degrade to the
    text-only heading, never block the report."""
    from unittest.mock import patch

    from app.services import mae_analytics_report_service as service

    snapshot = {
        "metrics": {},
        "daily_volume": [{"label": "Aug 01", "count": 1}],
        "agency_mix": [{"label": "911 Center", "count": 1}],
        "incident_types": [{"label": "Medical Call", "count": 1}],
    }

    with_logo = build_analytics_report(snapshot)
    images = [
        image
        for page in PdfReader(BytesIO(with_logo)).pages
        for image in page.images
    ]
    assert images, "expected the county logo embedded in the PDF"

    with patch.object(service, "_LOGO_PATH", service._LOGO_PATH.with_name("missing.png")):
        without_logo = build_analytics_report(snapshot)
    assert without_logo.startswith(b"%PDF")
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(without_logo)).pages
    )
    assert "MAE Analytics Report" in text
    assert not [
        image
        for page in PdfReader(BytesIO(without_logo)).pages
        for image in page.images
    ]
