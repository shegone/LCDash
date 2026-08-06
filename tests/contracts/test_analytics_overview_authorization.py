"""Offline trusted-context authorization tests for analytics overview."""

import socket
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import patch

from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
)
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import TenantAuthorizationDenied

if "psycopg" not in sys.modules and find_spec("psycopg") is None:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.Error = type("Error", (Exception,), {})
    psycopg_stub.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("database access blocked")
    )
    sys.modules["psycopg"] = psycopg_stub

from app.services.analytics_reporting import get_analytics_overview


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-analyst",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-analytics",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class SyntheticAnalyticsRepository:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def initialize_schema(self):
        return None

    def fetchone(self, query, params):
        if "MAX(source_collected_at)" in query:
            return (10, 0, None, None, None, 0, None, 0)
        if "calls_with_call_taker" in query:
            return (0, 0, None, None, 0, 0)
        return (0, 0, 0)

    def fetchall(self, query, params):
        if "AS agency" in query and "LIMIT 8" in query:
            return [("SLD", 10)]
        return []


class AnalyticsOverviewAuthorizationTests(unittest.TestCase):
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
            patch("psycopg.connect", side_effect=AssertionError("database access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    @patch(
        "app.services.analytics_reporting.AnalyticsRepository",
        return_value=SyntheticAnalyticsRepository(),
    )
    @patch(
        "app.services.analytics_reporting.analytics_database_is_configured",
        return_value=True,
    )
    def test_trusted_logan_context_authorizes_before_fake_repository(
        self,
        configured_mock,
        repository_mock,
    ):
        overview = get_analytics_overview(
            period="24h",
            tenant_context=trusted_context("logan-synthetic"),
        )

        self.assertEqual(
            overview["agency_mix"][0]["label"],
            "Synthetic Logan Dispatch",
        )
        configured_mock.assert_called_once_with()
        repository_mock.assert_called_once_with()
        self.assert_no_service_used()

    def test_disabled_and_cross_tenant_deny_before_repository_construction(self):
        logan = load_builtin_county_profile("logan-synthetic")
        disabled = replace(logan, modules=logan.modules - {"analytics"})
        northstar = load_builtin_county_profile("northstar-fictional")

        for profile, error_pattern in (
            (disabled, "not enabled"),
            (northstar, "binding mismatch"),
        ):
            with self.subTest(error=error_pattern):
                with (
                    patch(
                        "app.services.analytics_reporting.resolve_county_profile",
                        return_value=profile,
                    ),
                    patch(
                        "app.services.analytics_reporting.AnalyticsRepository",
                        side_effect=AssertionError("repository constructed before denial"),
                    ) as repository_mock,
                    patch(
                        "app.services.analytics_reporting.analytics_database_is_configured",
                        side_effect=AssertionError("database checked before denial"),
                    ) as configured_mock,
                ):
                    with self.assertRaisesRegex(
                        TenantAuthorizationDenied,
                        error_pattern,
                    ):
                        get_analytics_overview(
                            tenant_context=trusted_context("logan-synthetic"),
                        )
                repository_mock.assert_not_called()
                configured_mock.assert_not_called()
        self.assert_no_service_used()

    def test_unknown_tenant_denies_before_repository_construction(self):
        with (
            patch(
                "app.services.analytics_reporting.AnalyticsRepository",
                side_effect=AssertionError("repository constructed for unknown tenant"),
            ) as repository_mock,
            patch(
                "app.services.analytics_reporting.analytics_database_is_configured",
                side_effect=AssertionError("database checked for unknown tenant"),
            ) as configured_mock,
        ):
            with self.assertRaises(CountyProfileValidationError):
                get_analytics_overview(
                    tenant_context=trusted_context("unknown-synthetic"),
                )
        repository_mock.assert_not_called()
        configured_mock.assert_not_called()
        self.assert_no_service_used()

    def test_legacy_no_context_path_does_not_resolve_or_authorize(self):
        with (
            patch(
                "app.services.analytics_reporting.resolve_county_profile",
                side_effect=AssertionError("legacy path must not resolve"),
            ) as resolve_mock,
            patch(
                "app.services.analytics_reporting.authorize_tenant_action",
                side_effect=AssertionError("legacy path must not authorize"),
            ) as authorize_mock,
            patch(
                "app.services.analytics_reporting.analytics_database_is_configured",
                return_value=False,
            ),
        ):
            overview = get_analytics_overview(period="24h")

        self.assertFalse(overview["available"])
        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
