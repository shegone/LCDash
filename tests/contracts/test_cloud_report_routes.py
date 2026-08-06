from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.tenancy import TenantContext
from app.main import app, get_trusted_tenant_context


def _context(*roles):
    return TenantContext(
        tenant_id="logan-synthetic", subject="synthetic-user",
        identity_source="test", roles=frozenset(roles), request_id="request-test",
        authenticated_at=datetime.now(timezone.utc),
    )


def test_preview_is_aggregate_db_first_and_marks_user_actions():
    app.dependency_overrides[get_trusted_tenant_context] = lambda: _context("viewer")
    try:
        with patch("app.main.get_analytics_overview", return_value={
            "available": True, "latest_data_at": "2026-08-06T12:00:00Z",
            "metrics": {"total_calls": 12},
        }), patch("app.main._current_cad_report_rows") as cad:
            response = TestClient(app).post("/api/cloud-ai/reports/preview", json={
                "metric": "call_count", "dimensions": ["day"], "period": "30d",
                "current_cad_fallback": True,
            })
        assert response.status_code == 200
        assert response.json()["rows"] == [{"call_count": 12}]
        assert response.json()["source"] == "analytics-database"
        assert response.json()["save_requires_user_action"] is True
        assert response.json()["export_requires_user_action"] is True
        cad.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_non_allowlisted_fields_fail_before_any_source_call():
    app.dependency_overrides[get_trusted_tenant_context] = lambda: _context("viewer")
    try:
        with patch("app.main.get_analytics_overview") as analytics:
            response = TestClient(app).post("/api/cloud-ai/reports/preview", json={
                "metric": "raw_cad_payload", "dimensions": ["caller_name"],
                "period": "30d",
            })
        assert response.status_code == 400
        analytics.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_viewer_cannot_save_or_export():
    app.dependency_overrides[get_trusted_tenant_context] = lambda: _context("viewer")
    try:
        client = TestClient(app)
        template = client.post("/api/cloud-ai/reports/templates", json={
            "title": "Calls by nature",
            "intent": {"metric": "calls_by_nature", "dimensions": ["nature"], "period": "30d"},
            "visible_to_roles": ["viewer"],
        })
        export = client.post("/api/cloud-ai/reports/export", json={
            "intent": {"metric": "call_count", "dimensions": ["day"], "period": "30d"},
            "preview_confirmed": True,
        })
        assert template.status_code == 403
        assert export.status_code == 403
    finally:
        app.dependency_overrides.clear()
