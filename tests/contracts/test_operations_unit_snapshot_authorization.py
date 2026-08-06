"""Offline authorization contracts for the live unit snapshot boundary."""

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
from app.services.operations_service import get_live_unit_snapshot


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-unit-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-unit-snapshot",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class OperationsUnitSnapshotAuthorizationTests(unittest.TestCase):
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

    def test_trusted_context_authorizes_before_client_and_passes_profile(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        client = object()
        order = []

        with (
            patch(
                "app.services.operations_service.resolve_county_profile",
                side_effect=lambda supplied: order.append("resolve") or profile,
            ),
            patch(
                "app.services.operations_service.authorize_tenant_action",
                side_effect=lambda *args: order.append("authorize") or True,
            ) as authorize_mock,
            patch(
                "app.services.operations_service.CentralSquareClient",
                side_effect=lambda: order.append("client") or client,
            ),
            patch(
                "app.services.operations_service.get_active_calls",
                side_effect=lambda **kwargs: order.append("active") or [],
            ),
            patch(
                "app.services.operations_service.get_all_units",
                side_effect=lambda **kwargs: order.append("roster") or [],
            ) as roster_mock,
        ):
            snapshot = get_live_unit_snapshot(tenant_context=context)

        self.assertEqual(order, ["resolve", "authorize", "client", "active", "roster"])
        authorize_mock.assert_called_once_with(
            context,
            profile,
            ModuleCapability.UNITS,
            "read",
        )
        roster_mock.assert_called_once_with(client=client, county_profile=profile)
        self.assertTrue(snapshot["roster_connected"])
        self.assert_no_network()

    def test_denials_happen_before_client_or_data_access(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        disabled = replace(
            profile,
            modules=frozenset(
                module
                for module in profile.modules
                if module != ModuleCapability.UNITS.value
            ),
        )
        cross_tenant = load_builtin_county_profile("northstar-fictional")

        for denied_profile in (disabled, cross_tenant):
            with self.subTest(profile=denied_profile.tenant_id, modules=denied_profile.modules):
                with (
                    patch(
                        "app.services.operations_service.resolve_county_profile",
                        return_value=denied_profile,
                    ),
                    patch(
                        "app.services.operations_service.CentralSquareClient",
                        side_effect=AssertionError("client constructed before denial"),
                    ) as constructor_mock,
                    patch(
                        "app.services.operations_service.get_active_calls",
                        side_effect=AssertionError("active calls read before denial"),
                    ) as active_mock,
                    patch(
                        "app.services.operations_service.get_all_units",
                        side_effect=AssertionError("roster read before denial"),
                    ) as roster_mock,
                ):
                    with self.assertRaises(TenantAuthorizationDenied):
                        get_live_unit_snapshot(tenant_context=context)
                constructor_mock.assert_not_called()
                active_mock.assert_not_called()
                roster_mock.assert_not_called()
        self.assert_no_network()

    def test_unknown_tenant_denies_before_client_or_data_access(self):
        context = trusted_context("unknown-synthetic")
        with (
            patch(
                "app.services.operations_service.CentralSquareClient",
                side_effect=AssertionError("client constructed for unknown tenant"),
            ) as constructor_mock,
            patch("app.services.operations_service.get_active_calls") as active_mock,
            patch("app.services.operations_service.get_all_units") as roster_mock,
        ):
            with self.assertRaises(CountyProfileValidationError):
                get_live_unit_snapshot(tenant_context=context)
        constructor_mock.assert_not_called()
        active_mock.assert_not_called()
        roster_mock.assert_not_called()
        self.assert_no_network()

    def test_no_context_preserves_legacy_roster_call_shape(self):
        client = object()
        with (
            patch(
                "app.services.operations_service.resolve_county_profile",
                side_effect=AssertionError("legacy path must not resolve"),
            ) as resolve_mock,
            patch(
                "app.services.operations_service.authorize_tenant_action",
                side_effect=AssertionError("legacy path must not authorize"),
            ) as authorize_mock,
            patch(
                "app.services.operations_service.CentralSquareClient",
                return_value=client,
            ),
            patch("app.services.operations_service.get_active_calls", return_value=[]),
            patch("app.services.operations_service.get_all_units", return_value=[]) as roster_mock,
        ):
            snapshot = get_live_unit_snapshot()

        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        roster_mock.assert_called_once_with(client=client)
        self.assertTrue(snapshot["roster_connected"])
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
