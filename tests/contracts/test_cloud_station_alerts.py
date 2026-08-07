from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[2]


def cloud_unit_snapshot():
    return {
        "last_updated": "2026-08-06T16:00:00+00:00",
        "roster_connected": True,
        "roster_warning": "",
        "calls": [
            {
                "cfs_number": "SYNTHETIC-1001",
                "incident_code": "FIRE",
                "incident_description": "Synthetic assignment",
                "priority": "2",
                "location": "Approved display location",
                "call_datetime": "2026-08-06T15:59:00+00:00",
                "status": "Open",
            }
        ],
        "all_units": [
            {
                "unit_number": "SYN1",
                "station": "STA 100",
                "agency": "FIRE",
                "unit_type": "Engine",
                "status": "Assigned",
                "roster_status": "Assigned",
                "cfs_number": "SYNTHETIC-1001",
                "last_assigned_time": "2026-08-06T16:00:00+00:00",
                "assignments": [
                    {
                        "unit_number": "SYN1",
                        "cfs_number": "SYNTHETIC-1001",
                        "dispatch_time": "2026-08-06T16:00:00+00:00",
                    }
                ],
            }
        ],
    }


def test_cloud_page_uses_supplementary_alert_template_and_never_calls_legacy_station_source():
    with (
        patch("app.main.settings.deployment_mode", "synthetic-disconnected"),
        patch("app.main._cloud_cad_bridge_enabled", return_value=True),
        patch("app.main.build_cloud_unit_snapshot", return_value=cloud_unit_snapshot()),
        patch("app.main.get_live_station_alert_snapshot") as legacy_source,
    ):
        response = TestClient(app).get("/station-alerts?station=STA%20100")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "SUPPLEMENTARY ALERT DISPLAY" in response.text
    assert "not a substitute for your station's primary dispatch notification" in response.text
    assert '<button id="close-station-alert"' in response.text
    assert "Close Alert" in response.text
    assert "lcdash-station-alerts-cloud.js" in response.text
    assert "lcdash-station-alerts.js?v=0.5.1" not in response.text
    # Advisory audio parity is deliberately approved for this page (Ted,
    # 2026-08-07) as a supplementary fire-station display -- the real
    # on-prem dispatch/tone system stays authoritative and unchanged. See
    # docs/planning/CLOUD_UI_FULL_PARITY_PROGRAM_2026-08-06.md and the
    # override recorded in memory (lcdash-cloud-station-alerts-decision).
    assert "Enable Loud Alerts" in response.text
    assert "Test Full Alert" in response.text
    # Cloud speech still never calls on-prem's local Speaches endpoint.
    assert "/api/voice/speech" not in response.text
    legacy_source.assert_not_called()


def test_cloud_api_uses_in_memory_read_snapshot_and_includes_announcement():
    with (
        patch("app.main.settings.deployment_mode", "synthetic-disconnected"),
        patch("app.main._cloud_cad_bridge_enabled", return_value=True),
        patch("app.main.build_cloud_unit_snapshot", return_value=cloud_unit_snapshot()),
        patch("app.main.get_live_station_alert_snapshot") as legacy_source,
    ):
        response = TestClient(app).get(
            "/api/operations/station-alerts?station=STA%20100"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["selected_stations"] == ["STA 100"]
    assert payload["alerts"][0]["event_id"]
    announcement = payload["alerts"][0]["announcement"]
    assert announcement.startswith(
        "Station 100, respond to Approved display location for a synthetic assignment."
    )
    legacy_source.assert_not_called()


def test_cloud_api_fails_closed_without_approved_read_source():
    with (
        patch("app.main.settings.deployment_mode", "synthetic-disconnected"),
        patch("app.main._cloud_cad_bridge_enabled", return_value=False),
        patch("app.main.build_cloud_unit_snapshot") as cloud_snapshot,
        patch("app.main.get_live_station_alert_snapshot") as legacy_source,
    ):
        response = TestClient(app).get(
            "/api/operations/station-alerts?station=STA%20100"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["alerts"] == []
    assert payload["selected_stations"] == ["STA 100"]
    assert payload["error"] == "Approved cloud assignment source unavailable."
    cloud_snapshot.assert_not_called()
    legacy_source.assert_not_called()


def test_cloud_script_has_fake_tone_and_advisory_speech_but_no_cad_write_path():
    script = (ROOT / "static/js/lcdash-station-alerts-cloud.js").read_text(
        encoding="utf-8"
    )
    template = (ROOT / "templates/station_alerts_cloud.html").read_text(
        encoding="utf-8"
    )

    assert 'fetch("/api/operations/station-alerts?' in script
    # The tone is synthesized entirely client-side (a WAV built from sine
    # segments) -- no audio asset files, no real dispatch hardware, no CAD
    # dependency at all.
    for expected in (
        "createToneWave",
        "dispatchAudio",
        "playDispatchTone",
        "new Audio(",
    ):
        assert expected in script
    # Cloud speech reuses MAE's advisory-only Amazon Polly endpoint, never
    # on-prem's local Speaches service, and never the browser's native
    # speechSynthesis API.
    assert '/api/cloud-ai/speech/sentence' in script
    assert "/api/voice/speech" not in script
    assert "speechSynthesis" not in script
    # Street view/map are plain outbound links to Google's own site, never
    # an embedded Maps/Street View widget -- the embedded APIs bill per
    # load, a plain <a href> to google.com does not.
    assert "setLocationLinks" in script
    assert '"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint="' in script
    # Just one click link for street view -- no separate "Open Google Map"
    # button; the address itself is already auto-displayed above it.
    assert 'id="alert-google-maps"' not in template
    assert 'id="alert-street-view"' in template
    assert 'target="_blank"' in template
    # No CAD write, dispatch, page, or acknowledgment vocabulary anywhere in
    # the script or template -- this stays a read-only, advisory display.
    for forbidden in ("acknowledge", "dispatch(", "AudioContext"):
        assert forbidden not in script
    assert 'id="close-station-alert"' in template
    # "acknowledge" only appears disclaiming that closing the popup does not
    # acknowledge anything -- no control is named or labeled as one.
    assert 'id="acknowledge-station-alert"' not in template
    assert "does not acknowledge" in template.lower()


def test_cloud_alert_overlay_flashes_red_like_on_prem():
    template = (ROOT / "templates/station_alerts_cloud.html").read_text(
        encoding="utf-8"
    )
    assert "alert-screen-flash" in template
    assert "alert-banner-flash" in template
    assert "alert-pulse" in template
    assert "prefers-reduced-motion" in template
    assert "advisory display only" in template.lower()


def test_on_prem_page_still_uses_existing_full_alert_path():
    with (
        patch("app.main.settings.deployment_mode", "on-prem"),
        patch("app.main.get_live_station_alert_snapshot") as legacy_source,
    ):
        legacy_source.return_value = {
            "connected": False,
            "generated_at": "2026-08-06T16:00:00+00:00",
            "selected_stations": [],
            "stations": [],
            "station_units": [],
            "alerts": [],
        }
        response = TestClient(app).get("/station-alerts")

    assert response.status_code == 200
    assert "Enable Loud Alerts" in response.text
    assert "Test Full Alert" in response.text
    assert "lcdash-station-alerts.js?v=0.5.1" in response.text
    assert "lcdash-station-alerts-cloud.js" not in response.text
    legacy_source.assert_called_once_with([])
