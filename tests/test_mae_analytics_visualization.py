from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.mae_analytics_visualization_service import (
    build_requested_visualization,
    build_visualization,
    infer_view_key,
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


def test_view_inference_requires_explicit_visual_request():
    assert infer_view_key("What was the busiest day of the week?") is None
    assert infer_view_key("Chart the busiest day of the week") == "weekday_volume"


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
    )

    assert result == {"saved": True, "widget_id": 17}
    params = repository.fetchone.call_args.args[1]
    assert params == ("Busiest day", "weekday_volume", "supervisor@example.com")


@patch("app.main.save_widget", return_value={"saved": True, "widget_id": 9})
def test_widget_endpoint_uses_authenticated_creator(save_mock):
    response = TestClient(app).post(
        "/api/analytics/widgets",
        headers={"cf-access-authenticated-user-email": "chief@example.com"},
        json={"title": "Calls by weekday", "view_key": "weekday_volume"},
    )
    assert response.status_code == 200
    save_mock.assert_called_once_with(
        title="Calls by weekday",
        view_key="weekday_volume",
        created_by="chief@example.com",
    )


def test_widget_endpoint_rejects_unapproved_view():
    response = TestClient(app).post(
        "/api/analytics/widgets",
        json={"title": "Unsafe", "view_key": "arbitrary_sql"},
    )
    assert response.status_code == 400
