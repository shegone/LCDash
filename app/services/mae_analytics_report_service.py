"""Create download-only, aggregate MAE analytics PDF reports."""
from __future__ import annotations

from io import BytesIO
from textwrap import shorten

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.mae_analytics_visualization_service import (
    build_visualization,
    validate_view_key,
)


def _chart(title: str, rows: list[dict], limit: int = 8) -> Drawing:
    drawing = Drawing(468, 220)
    drawing.add(String(0, 202, title, fontName="Helvetica-Bold", fontSize=11))
    selected = rows[-limit:]
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 34, 42, 410, 140
    chart.data = [[int(row.get("count") or 0) for row in selected]]
    chart.categoryAxis.categoryNames = [shorten(str(row.get("label") or "Unknown"), width=24, placeholder="...") for row in selected]
    chart.categoryAxis.labels.angle, chart.categoryAxis.labels.fontSize = 30, 7
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.bars[0].fillColor = colors.HexColor("#36c2c9")
    drawing.add(chart)
    return drawing


def build_analytics_report(snapshot: dict, view_key: str = "") -> bytes:
    """Return a PDF containing only verified aggregate analytics values."""
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=letter, rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42)
    styles = getSampleStyleSheet()
    metrics = snapshot.get("metrics") or {}
    story = [
        Paragraph("Logan County 911 - MAE Analytics Report", styles["Title"]),
        Paragraph("Supervisor-requested aggregate analytics report", styles["Italic"]), Spacer(1, 12),
        Paragraph(f"Reporting window: {snapshot.get('period_label') or 'Selected period'}", styles["BodyText"]),
        Paragraph(f"Generated: {(snapshot.get('generated_at') or '').replace('T', ' ')}", styles["BodyText"]), Spacer(1, 12),
    ]
    rows = [["Metric", "Verified value"], ["Total calls", str(metrics.get("total_calls") or 0)], ["Unit responses", str(metrics.get("unit_responses") or 0)], ["Average CAD processing", str(metrics.get("average_processing") or "Not available")], ["Average response", str(metrics.get("average_response") or "Not available")], ["Median response", str(metrics.get("median_response") or "Not available")]]
    table = Table(rows, colWidths=[2.7 * inch, 3.6 * inch])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16344a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#a0aec0")), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#edf2f7")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("PADDING", (0, 0), (-1, -1), 6)]))
    charts = []
    if view_key:
        safe_key = validate_view_key(view_key)
        spec = build_visualization(snapshot, safe_key)
        charts.append(
            _chart(
                spec["title"],
                [
                    {"label": point["label"], "count": point["value"]}
                    for point in spec["points"][:12]
                ],
                limit=12,
            )
        )
    else:
        charts.extend([
            _chart("Recent Daily Call Volume", snapshot.get("daily_volume") or [], limit=10),
            _chart("Calls by Agency", snapshot.get("agency_mix") or []),
            _chart("Top Incident Types", snapshot.get("incident_types") or []),
        ])
    story.extend([Paragraph("Verified Overview", styles["Heading2"]), table, Spacer(1, 14), *charts, Spacer(1, 8), Paragraph("Source: LCDash PostgreSQL analytics. This report uses aggregate historical analytics only. It does not contain caller, address, narrative, recording, credential, or CAD write data.", styles["BodyText"])])
    document.build(story)
    return output.getvalue()
