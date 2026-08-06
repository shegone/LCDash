from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import TenantContext
from app.main import app, get_trusted_tenant_context
from app.services.mae_analytics_visualization_service import (
    TenantWidgetIsolationError,
    build_requested_visualization,
    build_visualization,
    infer_view_key,
    list_saved_widgets,
    retire_widget,
    save_widget,
)


def _snapshot():
    return {
        "available": True,
        "period_key": "30d",
        "period_label": "Last 30 days",
        "generated_at": "2026-08-03T12:00:00-04:00",
        "daily_volume": [{"label": "Aug 03", "count": 12}],
        "weekday_volume": [{"label": "Sunday", "count": 20}],
        "agency_mix": [{"label": "LEASA", "count": 8}],
    }


def _tenant(tenant_id: str = "logan-synthetic") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="analytics-reviewer",
        identity_source="test-trusted-context",
        roles=frozenset({"lcdash-pilot-reviewer"}),
        request_id=f"{tenant_id}-widgets",
        authenticated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


def test_view_inference_requires_explicit_visual_request():
    assert infer_view_key("What was the busiest day of the week?") is None
    assert infer_view_key("Chart the busiest day of the week") == "weekday_volume"
    assert infer_view_key(
        "Show me a chart of the busiest days of the week for the last 30 days."
    ) == "weekday_volume"


def test_visualization_uses_only_allowlisted_aggregate_points():
    result = build_visualization(_snapshot(), "agency_mix")
    assert result["view_key"] == "agency_mix"
    assert result["points"] == [{"label": "LEASA", "value": 8}]
    assert result["aggregate_only"] is True
    assert "query" not in result


def test_requested_visualization_uses_postgresql_analytics_context():
    result = build_requested_visualization(
        "Show a chart of calls by day",
        [{"source": "PostgreSQL analytics", "data": _snapshot()}],
    )
    assert result["view_key"] == "daily_volume"


def test_unknown_view_is_rejected():
    with pytest.raises(ValueError, match="Unsupported analytics chart"):
        build_visualization(_snapshot(), "write_any_sql")


@patch("app.services.mae_analytics_visualization_service.AnalyticsRepository")
def test_saved_widget_stores_only_safe_configuration(repository_class):
    repository = MagicMock()
    repository.__enter__.return_value = repository
    repository.fetchone.return_value = (17,)
    repository_class.return_value = repository

    result = save_widget(
        title="Busiest day",
        view_key="weekday_volume",
        created_by="supervisor@example.com",
        tenant_context=_tenant(),
    )

    assert result == {"saved": True, "widget_id": 17}
    params = repository.fetchone.call_args.args[1]
    assert params == (
        "logan-synthetic",
        "Busiest day",
        "weekday_volume",
        "supervisor@example.com",
    )


@patch("app.main.save_widget", return_value={"saved": True, "widget_id": 9})
def test_widget_endpoint_uses_authenticated_creator(save_mock):
    tenant = _tenant()
    app.dependency_overrides[get_trusted_tenant_context] = lambda: tenant
    try:
        response = TestClient(app).post(
            "/api/analytics/widgets",
            headers={"cf-access-authenticated-user-email": "chief@example.com"},
            json={"title": "Calls by weekday", "view_key": "weekday_volume"},
        )
    finally:
        app.dependency_overrides.pop(get_trusted_tenant_context, None)
    assert response.status_code == 200
    save_mock.assert_called_once_with(
        title="Calls by weekday",
        view_key="weekday_volume",
        created_by="chief@example.com",
        tenant_context=tenant,
    )


def test_widget_endpoint_rejects_unapproved_view():
    app.dependency_overrides[get_trusted_tenant_context] = _tenant
    try:
        response = TestClient(app).post(
            "/api/analytics/widgets",
            json={"title": "Unsafe", "view_key": "arbitrary_sql"},
        )
    finally:
        app.dependency_overrides.pop(get_trusted_tenant_context, None)
    assert response.status_code == 400


def test_widget_endpoints_fail_closed_without_trusted_tenant():
    client = TestClient(app)
    assert client.get("/api/analytics/widgets").status_code == 503
    assert client.post(
        "/api/analytics/widgets",
        json={"title": "Calls", "view_key": "daily_volume"},
    ).status_code == 503
    assert client.post(
        "/api/analytics/widgets/retire",
        json={"widget_id": 1},
    ).status_code == 503


@patch("app.services.mae_analytics_visualization_service.AnalyticsRepository")
def test_widget_list_is_scoped_to_trusted_tenant(repository_class):
    repository = MagicMock()
    repository.__enter__.return_value = repository
    repository.fetchall.return_value = []
    repository_class.return_value = repository

    assert list_saved_widgets(tenant_context=_tenant("northstar-fictional")) == []
    query, params = repository.fetchall.call_args.args
    assert "WHERE tenant_id = %s" in query
    assert params == ("northstar-fictional",)


@patch("app.services.mae_analytics_visualization_service.AnalyticsRepository")
def test_widget_retire_cannot_cross_tenant_boundary(repository_class):
    repository = MagicMock()
    repository.__enter__.return_value = repository
    repository.fetchone.return_value = None
    repository_class.return_value = repository

    result = retire_widget(widget_id=17, tenant_context=_tenant("northstar-fictional"))
    assert result == {"saved": False, "widget_id": 17}
    query, params = repository.fetchone.call_args.args
    assert "widget_id = %s AND tenant_id = %s" in query
    assert params == (17, "northstar-fictional")


def test_widget_service_rejects_missing_tenant_before_database_access():
    with pytest.raises(TenantWidgetIsolationError, match="Trusted tenant context"):
        list_saved_widgets(tenant_context=None)
