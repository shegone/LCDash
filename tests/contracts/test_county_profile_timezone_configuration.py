"""Offline Package 2B tests for profile-driven report timezones."""

import socket
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from app.core.county_profiles import load_builtin_county_profile


def _stub_module(name: str, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    if name in {"reportlab", "reportlab.lib"}:
        module.__path__ = []
    sys.modules.setdefault(name, module)


class _UnusedReportObject:
    def __init__(self, *args, **kwargs):
        pass


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

from app.services.county_commission_report_service import resolve_report_month


def synthetic_zoneinfo(name: str):
    offsets = {
        "America/New_York": -4,
        "America/Chicago": -5,
    }
    if name not in offsets:
        raise ZoneInfoNotFoundError(name)
    return timezone(timedelta(hours=offsets[name]), name)


class CountyProfileTimezoneConfigurationTests(unittest.TestCase):
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
        self.zoneinfo = patch(
            "app.services.county_commission_report_service.ZoneInfo",
            side_effect=synthetic_zoneinfo,
        )
        self.zoneinfo_mock = self.zoneinfo.start()
        self.addCleanup(self.zoneinfo.stop)
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_network_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_no_profile_preserves_inherited_new_york_timezone(self):
        window = resolve_report_month(
            "2026-06",
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )

        self.assertEqual(window["start_at"].utcoffset(), timedelta(hours=-4))
        self.zoneinfo_mock.assert_called_once_with("America/New_York")
        self.assert_no_network_used()

    def test_logan_and_northstar_use_distinct_configured_timezones(self):
        logan = load_builtin_county_profile("logan-synthetic")
        northstar = load_builtin_county_profile("northstar-fictional")

        logan_window = resolve_report_month(
            "2026-06",
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
            county_profile=logan,
        )
        northstar_window = resolve_report_month(
            "2026-06",
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
            county_profile=northstar,
        )

        self.assertEqual(logan_window["start_at"].utcoffset(), timedelta(hours=-4))
        self.assertEqual(northstar_window["start_at"].utcoffset(), timedelta(hours=-5))
        self.assert_no_network_used()

    def test_unavailable_in_memory_timezone_fails_closed(self):
        profile = load_builtin_county_profile("logan-synthetic")
        invalid_profile = replace(profile, timezone="Invalid/Unavailable")

        with self.assertRaisesRegex(ValueError, "timezone is unavailable"):
            resolve_report_month(
                "2026-06",
                now=datetime(2026, 8, 4, tzinfo=timezone.utc),
                county_profile=invalid_profile,
            )
        self.assert_no_network_used()


if __name__ == "__main__":
    unittest.main()
