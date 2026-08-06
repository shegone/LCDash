from pathlib import Path

from app.core.presentation_time import eastern_display_timestamp, source_and_eastern_display


ROOT = Path(__file__).parents[2]


def test_winter_utc_converts_to_est():
    assert eastern_display_timestamp("2026-01-15T17:00:00Z") == "2026-01-15T12:00:00-05:00"


def test_summer_utc_converts_to_edt():
    assert eastern_display_timestamp("2026-07-15T16:00:00Z") == "2026-07-15T12:00:00-04:00"


def test_source_timestamp_is_preserved_alongside_display_value():
    result = source_and_eastern_display("2026-07-15T16:00:00Z")
    assert result["source_timestamp"] == "2026-07-15T16:00:00Z"
    assert result["display_timestamp"].endswith("-04:00")


def test_cloud_ui_formatters_pin_new_york_instead_of_browser_timezone():
    for path in (
        "static/js/lcdash-time.js",
        "static/js/lcdash-station-alerts-cloud.js",
        "static/js/lcdash-analytics.js",
        "static/js/lcdash-reports.js",
    ):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert 'America/New_York' in source
