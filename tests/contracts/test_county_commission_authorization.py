"""Offline trusted-context authorization tests for county commission reports."""

import socket
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
)
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import TenantAuthorizationDenied


def _stub_module(name: str, **attributes):
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    if name in {"reportlab", "reportlab.lib"}:
        module.__path__ = []
    sys.modules[name] = module


class _UnusedReportObject:
    def __init__(self, *args, **kwargs):
        pass


if "reportlab" not in sys.modules and find_spec("reportlab") is None:
    _stub_module("reportlab")
    _stub_module("reportlab.lib")
    _stub_module("reportlab.lib.colors")
    _stub_module("reportlab.lib.pagesizes", letter=(612, 792))
    _stub_module("reportlab.lib.styles", getSampleStyleSheet=lambda: {})
    _stub_module("reportlab.lib.units", inch=1)
    _stub_module(
        "reportlab.platypus",
        Paragraph=_UnusedReportObject,
        SimpleDocTemplate=_UnusedReportObject,
        Spacer=_UnusedReportObject,
        Table=_UnusedReportObject,
        TableStyle=_UnusedReportObject,
    )

from app.services.county_commission_report_service import (
    build_county_commission_report,
)


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-report-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-commission-report",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


def synthetic_zoneinfo(name: str):
    offsets = {
        "America/New_York": -4,
        "America/Chicago": -5,
    }
    if name not in offsets:
        raise ZoneInfoNotFoundError(name)
    return timezone(timedelta(hours=offsets[name]), name)


class SyntheticCadClient:
    def __init__(self):
        self.calls = []

    def search_cfs_core(self, search_body, skip=0, limit=100):
        self.calls.append((dict(search_body), skip, limit))
        return {"cfs_cores": []}


class ForbiddenCadClient:
    def search_cfs_core(self, search_body, skip=0, limit=100):
        raise AssertionError("CAD client called before authorization")


class CountyCommissionAuthorizationTests(unittest.TestCase):
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
        self.zoneinfo = patch(
            "app.services.county_commission_report_service.ZoneInfo",
            side_effect=synthetic_zoneinfo,
        )
        self.zoneinfo.start()
        self.addCleanup(self.zoneinfo.stop)
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_trusted_logan_authorizes_and_uses_profile_timezone(self):
        client = SyntheticCadClient()

        report = build_county_commission_report(
            "2026-06",
            client=client,
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
            tenant_context=trusted_context("logan-synthetic"),
        )

        search_body = client.calls[0][0]
        self.assertEqual(search_body["RecordCreatedFrom"], "2026-06-01T00:00:00-04:00")
        self.assertEqual(search_body["RecordCreatedTo"], "2026-07-01T00:00:00-04:00")
        self.assertFalse(report["write_access"])
        self.assert_no_service_used()

    def test_disabled_and_cross_tenant_deny_before_window_or_client(self):
        logan = load_builtin_county_profile("logan-synthetic")
        disabled = replace(
            logan,
            modules=logan.modules - {"county_commission_report"},
        )
        northstar = load_builtin_county_profile("northstar-fictional")

        for profile, error_pattern in (
            (disabled, "not enabled"),
            (northstar, "binding mismatch"),
        ):
            with self.subTest(error=error_pattern):
                with (
                    patch(
                        "app.services.county_commission_report_service.resolve_county_profile",
                        return_value=profile,
                    ),
                    patch(
                        "app.services.county_commission_report_service.resolve_report_month",
                        side_effect=AssertionError("month resolved before denial"),
                    ) as month_mock,
                    patch(
                        "app.services.county_commission_report_service.CentralSquareClient",
                        side_effect=AssertionError("client constructed before denial"),
                    ) as constructor_mock,
                ):
                    for client in (None, ForbiddenCadClient()):
                        with self.assertRaisesRegex(
                            TenantAuthorizationDenied,
                            error_pattern,
                        ):
                            build_county_commission_report(
                                "2026-06",
                                client=client,
                                tenant_context=trusted_context("logan-synthetic"),
                            )
                month_mock.assert_not_called()
                constructor_mock.assert_not_called()
        self.assert_no_service_used()

    def test_unknown_tenant_denies_before_window_or_client(self):
        with (
            patch(
                "app.services.county_commission_report_service.resolve_report_month",
                side_effect=AssertionError("month resolved for unknown tenant"),
            ) as month_mock,
            patch(
                "app.services.county_commission_report_service.CentralSquareClient",
                side_effect=AssertionError("client constructed for unknown tenant"),
            ) as constructor_mock,
        ):
            for client in (None, ForbiddenCadClient()):
                with self.assertRaises(CountyProfileValidationError):
                    build_county_commission_report(
                        "2026-06",
                        client=client,
                        tenant_context=trusted_context("unknown-synthetic"),
                    )
        month_mock.assert_not_called()
        constructor_mock.assert_not_called()
        self.assert_no_service_used()

    def test_direct_profile_and_no_context_paths_remain_compatible(self):
        profile = load_builtin_county_profile("logan-synthetic")
        for supplied_profile in (profile, None):
            with self.subTest(profile=supplied_profile is not None):
                client = SyntheticCadClient()
                with (
                    patch(
                        "app.services.county_commission_report_service.resolve_county_profile",
                        side_effect=AssertionError("legacy path must not resolve"),
                    ) as resolve_mock,
                    patch(
                        "app.services.county_commission_report_service.authorize_tenant_action",
                        side_effect=AssertionError("legacy path must not authorize"),
                    ) as authorize_mock,
                ):
                    build_county_commission_report(
                        "2026-06",
                        client=client,
                        now=datetime(2026, 8, 4, tzinfo=timezone.utc),
                        county_profile=supplied_profile,
                    )
                resolve_mock.assert_not_called()
                authorize_mock.assert_not_called()
        self.assert_no_service_used()

    def test_direct_profile_and_context_cannot_be_combined(self):
        profile = load_builtin_county_profile("logan-synthetic")

        with self.assertRaisesRegex(TenantAuthorizationDenied, "cannot be combined"):
            build_county_commission_report(
                "2026-06",
                client=ForbiddenCadClient(),
                county_profile=profile,
                tenant_context=trusted_context("logan-synthetic"),
            )
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
