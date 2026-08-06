"""Offline trusted-context authorization tests for the unit service."""

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
from app.services.unit_service import get_all_units


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-unit-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-units",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class SyntheticUnitClient:
    def __init__(self, abbreviation: str = "AVL"):
        self.abbreviation = abbreviation
        self.calls = []

    def search_units(self, query, *, skip, limit):
        self.calls.append((query, skip, limit))
        return {
            "Units": [
                {
                    "UnitNumber": "SYN-UNIT-1",
                    "Status": {
                        "Description": "Vendor-specific ready state",
                        "Abbreviation": self.abbreviation,
                    },
                    "Agency": {"Abbreviation": "SYN"},
                }
            ],
            "next": None,
        }


class ForbiddenUnitClient:
    def search_units(self, query, *, skip, limit):
        raise AssertionError("client called before authorization")


class UnitServiceAuthorizationTests(unittest.TestCase):
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

    def test_trusted_logan_context_authorizes_and_applies_status_mapping(self):
        client = SyntheticUnitClient("AVL")

        units = get_all_units(
            client=client,
            tenant_context=trusted_context("logan-synthetic"),
        )

        self.assertEqual(units[0]["status"], "Available")
        self.assertEqual(client.calls, [({}, 0, 100)])
        self.assert_no_service_used()

    def test_disabled_and_cross_tenant_deny_before_client_construction_or_call(self):
        logan = load_builtin_county_profile("logan-synthetic")
        disabled = replace(logan, modules=logan.modules - {"units"})
        northstar = load_builtin_county_profile("northstar-fictional")

        for profile, error_pattern in (
            (disabled, "not enabled"),
            (northstar, "binding mismatch"),
        ):
            with self.subTest(error=error_pattern):
                with (
                    patch(
                        "app.services.unit_service.resolve_county_profile",
                        return_value=profile,
                    ),
                    patch(
                        "app.services.unit_service.CentralSquareClient",
                        side_effect=AssertionError("client constructed before denial"),
                    ) as constructor_mock,
                ):
                    for client in (None, ForbiddenUnitClient()):
                        with self.assertRaisesRegex(
                            TenantAuthorizationDenied,
                            error_pattern,
                        ):
                            get_all_units(
                                client=client,
                                tenant_context=trusted_context("logan-synthetic"),
                            )
                constructor_mock.assert_not_called()
        self.assert_no_service_used()

    def test_unknown_tenant_denies_before_client_construction_or_call(self):
        with patch(
            "app.services.unit_service.CentralSquareClient",
            side_effect=AssertionError("client constructed for unknown tenant"),
        ) as constructor_mock:
            for client in (None, ForbiddenUnitClient()):
                with self.assertRaises(CountyProfileValidationError):
                    get_all_units(
                        client=client,
                        tenant_context=trusted_context("unknown-synthetic"),
                    )
        constructor_mock.assert_not_called()
        self.assert_no_service_used()

    def test_legacy_no_context_behavior_is_unchanged(self):
        client = SyntheticUnitClient("AVL")
        with (
            patch(
                "app.services.unit_service.resolve_county_profile",
                side_effect=AssertionError("legacy path must not resolve"),
            ) as resolve_mock,
            patch(
                "app.services.unit_service.authorize_tenant_action",
                side_effect=AssertionError("legacy path must not authorize"),
            ) as authorize_mock,
        ):
            units = get_all_units(client=client)

        self.assertEqual(units[0]["status"], "Vendor-specific ready state")
        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        self.assert_no_service_used()

    def test_direct_profile_and_trusted_context_cannot_be_combined(self):
        profile = load_builtin_county_profile("logan-synthetic")

        with self.assertRaisesRegex(TenantAuthorizationDenied, "cannot be combined"):
            get_all_units(
                client=ForbiddenUnitClient(),
                county_profile=profile,
                tenant_context=trusted_context("logan-synthetic"),
            )
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
