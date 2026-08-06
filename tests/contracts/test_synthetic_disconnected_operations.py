import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.heatmap_service import get_live_heatmap_snapshot
from app.services.map_service import get_live_map_snapshot
from app.services.operations_service import (
    get_live_operations_snapshot,
    get_live_unit_snapshot,
)


SYNTHETIC_PAGES = (
    "/dashboard",
    "/active-calls",
    "/units",
    "/map",
    "/map/heatmap",
    "/station-alerts",
    "/analytics",
    "/reports",
    "/mae",
    "/integrations/health",
    "/mae/reliability",
    "/knowledge",
    "/nga911",
    "/nga911-intelligence",
    "/nga911/operations",
    "/nga911/nova",
    "/mindshare",
    "/mindshare/technical",
    "/mindshare/jack-hines",
    "/mindshare/library",
    "/mindshare/reliability",
    "/mindshare/coverage",
    "/mindshare/radio",
    "/voice",
)

SYNTHETIC_APIS = (
    "/api/pilot/readiness",
    "/api/operations/snapshot",
    "/api/operations/active-calls",
    "/api/operations/units",
    "/api/operations/map",
    "/api/operations/map/heatmap",
    "/api/operations/station-alerts",
    "/api/analytics/status",
    "/api/analytics/overview",
    "/api/analytics/widgets",
    "/api/voice/status",
    "/api/knowledge/status",
    "/api/mindshare/status",
    "/api/mindshare/knowledge/status",
    "/api/mindshare/memory",
    "/api/mindshare/evaluations",
    "/api/mindshare/coverage",
    "/api/mae/status",
    "/api/mae/tools",
    "/api/mae/evaluations",
    "/api/mae/feedback/review",
    "/api/mae/memory",
    "/api/nga911/v1/intelligence/overview",
    "/api/nga911/v1/counties",
    "/api/nga911/v1/director/operations",
    "/api/nga911/v1/nova/status",
)


class SyntheticDisconnectedOperationsTests(unittest.TestCase):
    def test_synthetic_dashboard_never_initializes_the_cad_client(self):
        with (
            patch.object(settings, "deployment_mode", "synthetic-disconnected"),
            patch(
                "app.services.operations_service.get_active_calls"
            ) as get_active_calls,
        ):
            snapshot = get_live_operations_snapshot()
            response = TestClient(app).get("/dashboard")

        get_active_calls.assert_not_called()
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(snapshot["dashboard_stats"]["active_calls"], 0)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Internal Server Error", response.text)

    def test_on_prem_operations_snapshot_preserves_the_cad_read_path(self):
        with (
            patch.object(settings, "deployment_mode", "on-prem"),
            patch(
                "app.services.operations_service.get_active_calls",
                return_value=[],
            ) as get_active_calls,
        ):
            snapshot = get_live_operations_snapshot()

        get_active_calls.assert_called_once_with()
        self.assertEqual(snapshot["calls"], [])

    def test_synthetic_units_map_and_heatmap_never_initialize_cad(self):
        with (
            patch.object(settings, "deployment_mode", "synthetic-disconnected"),
            patch(
                "app.services.operations_service.CentralSquareClient"
            ) as operations_client,
            patch(
                "app.services.heatmap_service.CentralSquareClient"
            ) as heatmap_client,
        ):
            units = get_live_unit_snapshot()
            map_data = get_live_map_snapshot()
            heatmap = get_live_heatmap_snapshot(8)

        operations_client.assert_not_called()
        heatmap_client.assert_not_called()
        self.assertEqual(units["all_units"], [])
        self.assertFalse(map_data["cad_connected"])
        self.assertEqual(map_data["features"], [])
        self.assertFalse(heatmap["cad_connected"])
        self.assertEqual(heatmap["features"], [])

    def test_synthetic_units_map_and_heatmap_routes_return_200_without_cad(self):
        client = TestClient(app)
        routes = (
            "/units",
            "/map",
            "/map/heatmap",
            "/api/operations/units",
            "/api/operations/map",
            "/api/operations/map/heatmap",
        )
        with (
            patch.object(settings, "deployment_mode", "synthetic-disconnected"),
            patch(
                "app.services.operations_service.CentralSquareClient"
            ) as operations_client,
            patch(
                "app.services.heatmap_service.CentralSquareClient"
            ) as heatmap_client,
        ):
            responses = {route: client.get(route) for route in routes}

        operations_client.assert_not_called()
        heatmap_client.assert_not_called()
        for route, response in responses.items():
            with self.subTest(route=route):
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Internal Server Error", response.text)

    def test_all_release_routes_avoid_5xx_and_cad_initialization(self):
        client = TestClient(app)
        with (
            patch.object(settings, "deployment_mode", "synthetic-disconnected"),
            patch(
                "app.services.operations_service.CentralSquareClient"
            ) as operations_client,
            patch(
                "app.services.heatmap_service.CentralSquareClient"
            ) as heatmap_client,
        ):
            responses = {
                route: client.get(route)
                for route in (*SYNTHETIC_PAGES, *SYNTHETIC_APIS)
            }

        operations_client.assert_not_called()
        heatmap_client.assert_not_called()
        for route, response in responses.items():
            with self.subTest(route=route):
                self.assertLess(response.status_code, 500)
                self.assertNotIn("Internal Server Error", response.text)


if __name__ == "__main__":
    unittest.main()
