"""Offline Package 2B tests for profile-derived presentation branding."""

import socket
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.county_branding import branding_for_tenant_context, county_branding
from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
)
from app.core.tenancy import TenantContext


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-branding-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-branding",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class CountyProfileBrandingConfigurationTests(unittest.TestCase):
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

    def assert_no_network_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_no_profile_returns_immutable_inherited_snapshot(self):
        branding = county_branding()

        self.assertEqual(dict(branding), {})
        with self.assertRaises(TypeError):
            branding["accent_color"] = "#000000"
        self.assert_no_network_used()

    def test_synthetic_counties_receive_isolated_immutable_branding(self):
        logan = county_branding(load_builtin_county_profile("logan-synthetic"))
        northstar = county_branding(load_builtin_county_profile("northstar-fictional"))

        self.assertEqual(logan["short_name"], "Logan Synthetic")
        self.assertEqual(northstar["short_name"], "Northstar Fictional")
        self.assertNotEqual(logan["accent_color"], northstar["accent_color"])
        with self.assertRaises(TypeError):
            logan["accent_color"] = northstar["accent_color"]
        self.assert_no_network_used()

    def test_malformed_in_memory_branding_fails_closed(self):
        profile = load_builtin_county_profile("logan-synthetic")
        malformed = replace(
            profile,
            branding={"short_name": "Unsafe", "logo_asset": "https://example.test/logo.svg"},
        )

        with self.assertRaisesRegex(ValueError, "incomplete or unsupported"):
            county_branding(malformed)
        self.assert_no_network_used()

    def test_trusted_context_composes_matching_immutable_branding(self):
        logan_context = trusted_context("logan-synthetic")
        northstar_context = trusted_context("northstar-fictional")

        logan = branding_for_tenant_context(logan_context)
        northstar = branding_for_tenant_context(northstar_context)

        self.assertEqual(logan["short_name"], "Logan Synthetic")
        self.assertEqual(northstar["short_name"], "Northstar Fictional")
        self.assertNotEqual(logan, northstar)
        with self.assertRaises(TypeError):
            logan["short_name"] = "Changed"
        self.assert_no_network_used()

    def test_no_context_composition_returns_exact_legacy_mapping(self):
        self.assertIs(branding_for_tenant_context(), county_branding())
        self.assertEqual(dict(branding_for_tenant_context()), {})
        self.assert_no_network_used()

    def test_unknown_and_cross_tenant_branding_fail_closed(self):
        with self.assertRaises(CountyProfileValidationError):
            branding_for_tenant_context(trusted_context("unknown-synthetic"))

        northstar = load_builtin_county_profile("northstar-fictional")
        with patch(
            "app.core.county_branding.resolve_county_profile",
            return_value=northstar,
        ):
            with self.assertRaisesRegex(
                CountyProfileValidationError,
                "tenant binding mismatch",
            ):
                branding_for_tenant_context(trusted_context("logan-synthetic"))
        self.assert_no_network_used()


if __name__ == "__main__":
    unittest.main()
