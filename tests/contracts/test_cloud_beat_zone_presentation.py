from pathlib import Path
from datetime import datetime, timezone

from app.integrations.cad.cloud_read_runtime import CloudCadDisplayState, _normalize_calls
from app.services.cloud_analytics_source_policy import retrieve_cloud_analytics
from app.services.operations_service import build_cloud_operations_snapshot


ROOT = Path(__file__).parents[2]


def test_cloud_payload_and_ui_do_not_publish_beat_or_zone():
    state = CloudCadDisplayState(
        calls=({"cfs_number": "CFS-1", "beat": "B1", "zone": "Z1"},),
        last_success_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    snapshot = build_cloud_operations_snapshot(state)
    assert "beat" not in snapshot["calls"][0]
    assert "zone" not in snapshot["calls"][0]
    for relative in (
        "templates/components/incident_card.html",
        "templates/call_detail_cloud.html",
        "static/js/lcdash-dashboard.js",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8").lower()
        assert "call.beat" not in source
        assert "call.zone" not in source


def test_internal_normalization_remains_backward_compatible():
    calls = _normalize_calls([{"CFSNumber": "1", "Beat": {"Description": "B1"}, "Zone": {"Description": "Z1"}}])
    assert calls[0]["beat"] == "B1"
    assert calls[0]["zone"] == "Z1"


def test_legacy_on_prem_normalization_fields_are_unchanged():
    source = (ROOT / "app/services/analytics_models.py").read_text(encoding="utf-8")
    assert '"beat": _dropdown_text(raw_call.get("Beat")' in source
    assert '"zone": _dropdown_text(raw_call.get("Zone")' in source


def test_cloud_analytics_cad_fallback_drops_beat_and_zone():
    class Database:
        def read(self, tenant_id, query_kind, parameters):
            return {
                "tenant_id": tenant_id,
                "rows": [],
                "freshness": "stale",
                "observed_at": "2026-08-06T12:00:00Z",
            }

    class Cad:
        def read_current(self, tenant_id, operation, parameters, *, timeout_seconds):
            return {
                "tenant_id": tenant_id,
                "rows": [
                    {
                        "cfs_number": "CFS-1",
                        "status": "Open",
                        "beat": "B1",
                        "zone": "Z1",
                    }
                ],
                "freshness": "current",
                "observed_at": "2026-08-06T12:00:01Z",
            }

    result = retrieve_cloud_analytics(
        tenant_id="logan-synthetic",
        query_kind="current_calls",
        parameters={},
        database=Database(),
        cad=Cad(),
        cad_operation="search_calls",
        current_answer_required=True,
    )

    assert result.data == ({"cfs_number": "CFS-1", "status": "Open"},)


def test_historical_import_keeps_internal_beat_and_zone_columns():
    source = (ROOT / "app/tools/phase2_analytics_import.py").read_text(encoding="utf-8")
    assert '"priority", "disposition_code", "disposition_description", "beat", "zone"' in source
    assert '"cfs_number", "unit_number", "unit_type", "station", "beat"' in source


def test_cloud_header_has_compact_responsive_structure():
    source = (ROOT / "templates/active_calls.html").read_text(encoding="utf-8")
    assert "active-calls-status-cloud" in source
    assert 'class="active-calls-source-summary"' in source
    assert "active-calls-status-kpis" in source
    assert "grid-template-columns: repeat(5" in source
    assert 'class="active-calls-page-heading ' in source
    assert 'class="active-calls-view-label text-end"' in source
    assert "@media (max-width: 1199px)" in source
    assert "@media (max-width: 767px)" in source
    assert "@media (max-width: 479px)" in source
    assert ".active-calls-status-kpis .active-calls-status-item:last-child" in source


def test_cloud_header_preserves_read_only_and_source_labels():
    source = (ROOT / "templates/active_calls.html").read_text(encoding="utf-8")
    assert "READ-ONLY CLOUD VIEW" in source
    assert "Normalized approved fields only" in source
    assert "CENTRALSQUARE CAD" in source
    assert "{{ cloud_presentation_status.source.label }}" in source
    assert "{{ cloud_presentation_status.source.notice }}" in source
