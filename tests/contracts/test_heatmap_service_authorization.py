"""Offline trusted-context authorization tests for live heatmaps."""

import socket
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
)
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import TenantAuthorizationDenied
from app.integrations.contracts import ModuleCapability
from app.services.heatmap_service import get_live_heatmap_snapshot


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-heatmap-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-heatmap",
        authenticated_at=NOW,
    )


class SyntheticHeatmapClient:
    def __init__(self, order=None):
        self.order = order
        self.calls = []

    def search_cfs_core(self, query, *, skip, limit):
        if self.order is not None:
            self.order.append("search")
        self.calls.append((query, skip, limit))
        return {"cfs_cores": [], "next": None}


class HeatmapServiceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network access blocked"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network access blocked"),
            ),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_network(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_trusted_preflight_orders_heatmap_then_active_calls_before_validation_and_search(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        order = []
        client = SyntheticHeatmapClient(order)

        with (
            patch(
                "app.services.heatmap_service.resolve_county_profile",
                side_effect=lambda supplied: order.append("resolve") or profile,
            ),
            patch(
                "app.services.heatmap_service.authorize_tenant_action",
                side_effect=lambda *args: order.append(args[2].value) or True,
            ),
            patch(
                "app.services.heatmap_service.validate_heatmap_hours",
                side_effect=lambda hours: order.append("validate") or hours,
            ),
        ):
            snapshot = get_live_heatmap_snapshot(
                8,
                client=client,
                now=NOW,
                tenant_context=context,
            )

        self.assertEqual(
            order,
            ["resolve", "heatmap", "active_calls", "validate", "search", "validate"],
        )
        self.assertEqual(snapshot["features"], [])
        self.assert_no_network()

    def test_disabled_capabilities_deny_before_validation_or_client(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        for capability in ("heatmap", "active_calls"):
            disabled = replace(profile, modules=profile.modules - {capability})
            with self.subTest(capability=capability):
                with (
                    patch(
                        "app.services.heatmap_service.resolve_county_profile",
                        return_value=disabled,
                    ),
                    patch(
                        "app.services.heatmap_service.validate_heatmap_hours",
                        side_effect=AssertionError("validation reached before denial"),
                    ) as validation_mock,
                    patch(
                        "app.services.heatmap_service.CentralSquareClient",
                        side_effect=AssertionError("client constructed before denial"),
                    ) as constructor_mock,
                ):
                    with self.assertRaises(TenantAuthorizationDenied):
                        get_live_heatmap_snapshot(
                            8,
                            client=SyntheticHeatmapClient(),
                            tenant_context=context,
                        )
                validation_mock.assert_not_called()
                constructor_mock.assert_not_called()
        self.assert_no_network()

    def test_trusted_resolved_profile_reaches_only_pure_aggregation(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        client = SyntheticHeatmapClient()
        expected = {"type": "FeatureCollection", "features": []}

        with (
            patch(
                "app.services.heatmap_service.resolve_county_profile",
                return_value=profile,
            ),
            patch(
                "app.services.heatmap_service.build_heatmap_snapshot",
                return_value=expected,
            ) as build_mock,
        ):
            result = get_live_heatmap_snapshot(
                8,
                client=client,
                now=NOW,
                tenant_context=context,
            )

        self.assertIs(result, expected)
        self.assertEqual(len(client.calls), 1)
        self.assertIs(build_mock.call_args.kwargs["county_profile"], profile)
        self.assert_no_network()

    def test_cross_and_unknown_tenants_deny_before_client(self):
        context = trusted_context("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")
        with patch(
            "app.services.heatmap_service.resolve_county_profile",
            return_value=northstar,
        ):
            with self.assertRaises(TenantAuthorizationDenied):
                get_live_heatmap_snapshot(
                    8,
                    client=SyntheticHeatmapClient(),
                    tenant_context=context,
                )

        with self.assertRaises(CountyProfileValidationError):
            get_live_heatmap_snapshot(
                8,
                client=SyntheticHeatmapClient(),
                tenant_context=trusted_context("unknown-synthetic"),
            )
        self.assert_no_network()

    def test_no_context_preserves_positional_injected_client_behavior(self):
        client = SyntheticHeatmapClient()
        with (
            patch(
                "app.services.heatmap_service.resolve_county_profile",
                side_effect=AssertionError("legacy path resolved tenant"),
            ) as resolve_mock,
            patch(
                "app.services.heatmap_service.authorize_tenant_action",
                side_effect=AssertionError("legacy path authorized tenant"),
            ) as authorize_mock,
        ):
            snapshot = get_live_heatmap_snapshot(8, client, NOW)

        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(snapshot["window"]["hours"], 8)
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
