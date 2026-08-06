"""Offline trusted-context authorization tests for live map snapshots."""

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
from app.services.map_service import get_live_map_snapshot
from app.services.operations_service import get_live_unit_snapshot


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-map-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-map",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


def empty_unit_snapshot() -> dict:
    return {
        "calls": [],
        "all_units": [],
        "roster_connected": True,
        "roster_warning": "",
    }


class MapServiceAuthorizationTests(unittest.TestCase):
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
            patch(
                "app.services.operations_service.CentralSquareClient",
                side_effect=AssertionError("CAD client constructed"),
            ),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_external_access(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_trusted_context_authorizes_gis_then_calls_before_unit_boundary(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        order = []

        with (
            patch(
                "app.services.map_service.resolve_county_profile",
                side_effect=lambda supplied: order.append("resolve") or profile,
            ),
            patch(
                "app.services.map_service.authorize_tenant_action",
                side_effect=lambda *args: order.append(args[2].value) or True,
            ) as authorize_mock,
            patch(
                "app.services.map_service.get_live_unit_snapshot",
                side_effect=lambda **kwargs: order.append("units") or empty_unit_snapshot(),
            ) as unit_mock,
        ):
            snapshot = get_live_map_snapshot(tenant_context=context)

        self.assertEqual(order, ["resolve", "gis", "active_calls", "units"])
        self.assertEqual(authorize_mock.call_count, 2)
        unit_mock.assert_called_once_with(tenant_context=context)
        self.assertIs(unit_mock.call_args.kwargs["tenant_context"], context)
        self.assertEqual(snapshot["features"], [])
        self.assert_no_external_access()

    def test_gis_and_active_calls_denials_precede_unit_snapshot(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        disabled_profiles = (
            replace(profile, modules=profile.modules - {"gis"}),
            replace(profile, modules=profile.modules - {"active_calls"}),
        )

        for disabled in disabled_profiles:
            with self.subTest(modules=disabled.modules):
                with (
                    patch(
                        "app.services.map_service.resolve_county_profile",
                        return_value=disabled,
                    ),
                    patch(
                        "app.services.map_service.get_live_unit_snapshot",
                        side_effect=AssertionError("unit snapshot called before denial"),
                    ) as unit_mock,
                ):
                    with self.assertRaises(TenantAuthorizationDenied):
                        get_live_map_snapshot(tenant_context=context)
                unit_mock.assert_not_called()
        self.assert_no_external_access()

    def test_units_denial_occurs_in_protected_snapshot_before_client(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        disabled = replace(profile, modules=profile.modules - {"units"})

        with (
            patch(
                "app.services.map_service.resolve_county_profile",
                return_value=disabled,
            ),
            patch(
                "app.services.operations_service.resolve_county_profile",
                return_value=disabled,
            ),
            patch(
                "app.services.map_service.get_live_unit_snapshot",
                side_effect=get_live_unit_snapshot,
            ),
        ):
            with self.assertRaises(TenantAuthorizationDenied):
                get_live_map_snapshot(tenant_context=context)
        self.assert_no_external_access()

    def test_cross_and_unknown_tenants_fail_before_unit_snapshot(self):
        context = trusted_context("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")
        with (
            patch(
                "app.services.map_service.resolve_county_profile",
                return_value=northstar,
            ),
            patch(
                "app.services.map_service.get_live_unit_snapshot",
                side_effect=AssertionError("unit snapshot called cross tenant"),
            ) as unit_mock,
        ):
            with self.assertRaises(TenantAuthorizationDenied):
                get_live_map_snapshot(tenant_context=context)
        unit_mock.assert_not_called()

        with patch(
            "app.services.map_service.get_live_unit_snapshot",
            side_effect=AssertionError("unit snapshot called for unknown tenant"),
        ) as unknown_unit_mock:
            with self.assertRaises(CountyProfileValidationError):
                get_live_map_snapshot(
                    tenant_context=trusted_context("unknown-synthetic")
                )
        unknown_unit_mock.assert_not_called()
        self.assert_no_external_access()

    def test_no_context_preserves_exact_legacy_snapshot_call(self):
        with (
            patch(
                "app.services.map_service.resolve_county_profile",
                side_effect=AssertionError("legacy path resolved tenant"),
            ) as resolve_mock,
            patch(
                "app.services.map_service.authorize_tenant_action",
                side_effect=AssertionError("legacy path authorized tenant"),
            ) as authorize_mock,
            patch(
                "app.services.map_service.get_live_unit_snapshot",
                return_value=empty_unit_snapshot(),
            ) as unit_mock,
        ):
            snapshot = get_live_map_snapshot()

        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        unit_mock.assert_called_once_with()
        self.assertEqual(snapshot["features"], [])
        self.assert_no_external_access()


if __name__ == "__main__":
    unittest.main()
