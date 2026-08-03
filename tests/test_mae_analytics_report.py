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
