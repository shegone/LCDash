"""Offline contract tests for deny-by-default tenant authorization."""

import socket
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.county_profiles import load_builtin_county_profile
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import (
    TenantAuthorizationDenied,
    authorize_tenant_action,
)
from app.integrations.contracts import ModuleCapability


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-request",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class TenantAuthorizationTests(unittest.TestCase):
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
            patch("httpx.get", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.post", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_enabled_non_operational_read_pair_is_allowed(self):
        profile = load_builtin_county_profile("logan-synthetic")

        self.assertTrue(
            authorize_tenant_action(
                trusted_context("logan-synthetic"),
                profile,
                ModuleCapability.GIS,
                "read",
            )
        )
        self.assert_no_service_used()

    def test_cross_tenant_context_is_denied(self):
        northstar = load_builtin_county_profile("northstar-fictional")

        with self.assertRaisesRegex(TenantAuthorizationDenied, "binding mismatch"):
            authorize_tenant_action(
                trusted_context("logan-synthetic"),
                northstar,
                ModuleCapability.GIS,
                "read",
            )
        self.assert_no_service_used()

    def test_disabled_and_unknown_pairs_are_denied(self):
        northstar = load_builtin_county_profile("northstar-fictional")
        context = trusted_context("northstar-fictional")

        for capability, action in (
            (ModuleCapability.CENTRALSQUARE_OPERATIONS, "read"),
            ("not-a-module", "read"),
            (ModuleCapability.GIS, "not-an-action"),
        ):
            with self.subTest(capability=capability, action=action):
                with self.assertRaises(TenantAuthorizationDenied):
                    authorize_tenant_action(context, northstar, capability, action)
        self.assert_no_service_used()

    def test_operational_outputs_are_denied_even_if_profile_enables_them(self):
        profile = load_builtin_county_profile("logan-synthetic")
        operational_pairs = {
            ModuleCapability.CAD_MESSAGES.value: "send",
            ModuleCapability.REALTIME_WEBHOOKS.value: "activate",
            ModuleCapability.PAGING.value: "page",
            ModuleCapability.PUBLIC_WARNING.value: "release",
            ModuleCapability.STATION_ALERTS.value: "release",
            ModuleCapability.EMS_DELAY.value: "page",
        }
        unsafe_profile = replace(
            profile,
            modules=profile.modules | operational_pairs.keys(),
        )
        context = trusted_context("logan-synthetic")

        for capability, action in operational_pairs.items():
            with self.subTest(capability=capability, action=action):
                with self.assertRaisesRegex(
                    TenantAuthorizationDenied,
                    "Operational output",
                ):
                    authorize_tenant_action(context, unsafe_profile, capability, action)
        self.assert_no_service_used()

    def test_cad_write_actions_are_unconditionally_denied(self):
        profile = load_builtin_county_profile("logan-synthetic")
        context = trusted_context("logan-synthetic")

        for action in (
            "update",
            "send",
            "acknowledge",
            "register_subscription",
            "write",
        ):
            with self.subTest(action=action):
                with self.assertRaisesRegex(
                    TenantAuthorizationDenied,
                    "Write or operational",
                ):
                    authorize_tenant_action(
                        context,
                        profile,
                        ModuleCapability.CENTRALSQUARE_OPERATIONS,
                        action,
                    )
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
