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


def test_cloud_page_uses_visual_only_template_and_never_calls_legacy_station_source():
    with (
        patch("app.main.settings.deployment_mode", "synthetic-disconnected"),
        patch("app.main._cloud_cad_bridge_enabled", return_value=True),
        patch("app.main.build_cloud_unit_snapshot", return_value=cloud_unit_snapshot()),
        patch("app.main.get_live_station_alert_snapshot") as legacy_source,
    ):
        response = TestClient(app).get("/station-alerts?station=STA%20100")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "CLOUD READ-ONLY ALERT" in response.text
    assert "CLOUD-VISIBLE ALERT ONLY" in response.text
    assert 'href="/dashboard"' in response.text
    assert "Return to Dashboard" in response.text
    assert "lcdash-station-alerts-cloud.js" in response.text
    assert "lcdash-station-alerts.js?v=0.5.1" not in response.text
    assert "Enable Loud Alerts" not in response.text
    assert "Test Full Alert" not in response.text
    assert "/api/voice/speech" not in response.text
    legacy_source.assert_not_called()


def test_cloud_api_uses_in_memory_read_snapshot_and_removes_announcement():
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
    assert "announcement" not in payload["alerts"][0]
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


def test_cloud_script_contains_no_audio_speech_or_outbound_action_path():
    script = (ROOT / "static/js/lcdash-station-alerts-cloud.js").read_text(
        encoding="utf-8"
    )
    template = (ROOT / "templates/station_alerts_cloud.html").read_text(
        encoding="utf-8"
    )

    assert 'fetch("/api/operations/station-alerts?' in script
    for forbidden in (
        "Audio(",
        "AudioContext",
        "/api/voice/speech",
        "playDispatchTone",
        "speechSynthesis",
        "acknowledge",
        "dispatchAudio",
    ):
        assert forbidden not in script
    assert '<a id="acknowledge-station-alert"' in template
    assert 'href="/dashboard"' in template
    assert "No audio, speech, tone, page" in template


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
