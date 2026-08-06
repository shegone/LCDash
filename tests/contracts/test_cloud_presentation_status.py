from pathlib import Path

import pytest

from app.services.cloud_presentation_status import build_cloud_presentation_status


REPO_ROOT = Path(__file__).resolve().parents[2]


def build_status(**cad_overrides):
    cad_status = {
        "enabled": False,
        "mode": "synthetic-disconnected",
        "freshness": "disabled",
        "error_code": "",
        "age_seconds": None,
        "call_count": 0,
        "unit_count": 0,
    }
    cad_status.update(cad_overrides)
    return build_cloud_presentation_status(
        cad_status=cad_status,
        ai_status={
            "documents_ingested": False,
            "rag_available": False,
            "voice_enabled": False,
        },
        knowledge_status={"documents": 0},
    )


@pytest.mark.parametrize(
    ("overrides", "state", "connected", "may_display"),
    [
        ({}, "disconnected", False, False),
        (
            {"enabled": True, "mode": "other", "freshness": "current"},
            "unverified-disabled",
            False,
            False,
        ),
        (
            {
                "enabled": True,
                "mode": "centralsquare-read-poll",
                "freshness": "current",
            },
            "verified-current",
            True,
            True,
        ),
        (
            {
                "enabled": True,
                "mode": "centralsquare-read-poll",
                "freshness": "current",
                "error_code": "read_failed",
            },
            "last-verified",
            False,
            True,
        ),
        (
            {
                "enabled": True,
                "mode": "centralsquare-read-poll",
                "freshness": "stale",
            },
            "stale",
            False,
            True,
        ),
        (
            {
                "enabled": True,
                "mode": "centralsquare-read-poll",
                "freshness": "awaiting-first-success",
            },
            "awaiting-success",
            False,
            False,
        ),
    ],
)
def test_source_claims_fail_closed(overrides, state, connected, may_display):
    source = build_status(**overrides)["source"]

    assert source["state"] == state
    assert source["connected"] is connected
    assert source["may_display_snapshot"] is may_display


def test_unavailable_features_keep_controls_visible_with_notices():
    presentation = build_status()

    assert presentation["knowledge"]["label"] == "NOT INGESTED"
    assert presentation["advisory"]["controls_visible"] is True
    assert "fail closed" in presentation["advisory"]["notice"]
    assert presentation["voice"]["controls_visible"] is True
    assert "not configured" in presentation["voice"]["notice"].lower()


def test_voice_presentation_separates_tts_from_conversational_readiness():
    presentation = build_cloud_presentation_status(
        cad_status={"enabled": False},
        ai_status={
            "documents_ingested": False,
            "rag_available": False,
            "voice_enabled": True,
            "tts": {"ready": True},
            "stt": {"ready": False},
        },
        knowledge_status={"documents": 0},
    )

    assert presentation["voice"]["tts_ready"] is True
    assert presentation["voice"]["stt_ready"] is False
    assert presentation["voice"]["conversation_ready"] is False
    assert presentation["voice"]["label"] == "TTS READY - VERIFIED ON USE"
    assert "microphone mode remains disabled" in presentation["voice"]["notice"]


def test_all_cloud_surfaces_consume_shared_presentation_status():
    surfaces = [
        "dashboard.html",
        "active_calls.html",
        "integrations_health.html",
        "mae.html",
        "knowledge.html",
        "voice_lab.html",
    ]

    for template_name in surfaces:
        template = (REPO_ROOT / "templates" / template_name).read_text(encoding="utf-8")
        assert "cloud_presentation_status" in template


def test_cloud_labels_do_not_conflate_browser_channel_with_cad_evidence():
    dashboard = (REPO_ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    integrations = (REPO_ROOT / "templates" / "integrations_health.html").read_text(
        encoding="utf-8"
    )
    dashboard_js = (REPO_ROOT / "static" / "js" / "lcdash-dashboard.js").read_text(
        encoding="utf-8"
    )
    integrations_js = (
        REPO_ROOT / "static" / "js" / "lcdash-integrations.js"
    ).read_text(encoding="utf-8")

    combined = "\n".join((dashboard, integrations, dashboard_js, integrations_js))
    assert "LIVE CAD DATA" not in combined
    assert "Live Supervisor View" not in combined
    assert '"STREAMING"' not in combined
    assert "BROWSER STREAM" not in combined
    assert "BROWSER UPDATE CHANNEL" in combined
