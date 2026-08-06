"""Network-free structural baseline for the mobile visual parity program."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = "docs/planning/CLOUD_MOBILE_VISUAL_PARITY_AUDIT_2026-08-05.md"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_primary_shell_has_mobile_viewport_drawer_and_touch_baseline():
    base = _read("templates/layouts/base.html")
    mobile = _read("static/css/lcdash-mobile.css")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in base
    assert "/static/css/lcdash-mobile.css" in base
    assert 'id="mobile-menu-button"' in base
    assert 'id="mobile-nav-overlay"' in base
    assert "/static/js/lcdash-mobile.js" in base
    assert "@media (max-width: 991.98px)" in mobile
    assert "@media (max-width: 575.98px)" in mobile
    assert "min-height: 44px" in mobile


def test_standalone_nga_shell_has_the_same_reachable_mobile_drawer():
    base = _read("templates/layouts/nga911_base.html")
    assert 'id="lcdash-sidebar"' in base
    assert 'id="mobile-menu-button"' in base
    assert 'id="mobile-nav-close"' in base
    assert 'id="mobile-nav-overlay"' in base
    assert 'aria-controls="lcdash-sidebar"' in base
    assert "/static/js/lcdash-mobile.js" in base
    assert "Network Simulation" in base


def test_operational_maps_have_phone_specific_height_and_touch_controls():
    mobile = _read("static/css/lcdash-mobile.css")
    map_template = _read("templates/map.html")
    heatmap_template = _read("templates/heatmap.html")
    detail = _read("templates/call_detail_cloud.html")
    assert "#operations-map" in mobile
    assert "#incident-map" in mobile
    assert "@media (max-width: 767px)" in map_template
    assert "@media (max-width: 767px)" in heatmap_template
    assert "min-height: 44px" in map_template
    assert "min-height: 44px" in heatmap_template
    assert "@media (max-width: 575.98px)" in detail


def test_dense_tables_use_or_document_a_horizontal_strategy():
    for template in ("analytics.html", "knowledge.html", "mindshare_library.html", "nga911_intelligence.html"):
        text = _read(f"templates/{template}")
        if "<table" in text:
            assert "table-responsive" in text, template
    report_css = _read("static/css/lcdash-reports.css")
    assert "@media (max-width: 900px)" in report_css
    assert ".county-report-grid { grid-template-columns: 1fr; }" in report_css


def test_audit_covers_every_requested_surface_and_viewport():
    audit = _read(AUDIT)
    for viewport in ("1440", "1024", "768", "390", "360"):
        assert viewport in audit
    for surface in (
        "Dashboard", "Active Calls", "Units", "Analytics", "GIS / heatmap",
        "Reports", "MAE", "Knowledge / Mindshare", "Voice", "NGA",
    ):
        assert surface in audit
    assert "read-only and advisory labels" in audit
    assert "No server, provider, AWS, CAD, deployment" in audit
