"""Offline tenant-isolation contracts for county-commission report jobs."""

import socket
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import patch

from app.core.county_profiles import load_builtin_county_profile
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

from app.services import county_commission_report_service as service


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-report-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-report-job",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class CapturedThread:
    instances = []

    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True


class ForbiddenLock:
    def __enter__(self):
        raise AssertionError("job lock entered before denial")

    def __exit__(self, exc_type, exc, traceback):
        return False


class CountyCommissionJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        service._JOBS.clear()
        service._ACTIVE_BY_MONTH.clear()
        CapturedThread.instances.clear()
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
                "app.services.county_commission_report_service.CentralSquareClient",
                side_effect=AssertionError("CAD client constructed"),
            ),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)
        self.zoneinfo = patch(
            "app.services.county_commission_report_service.ZoneInfo",
            return_value=timezone.utc,
        )
        self.zoneinfo.start()
        self.addCleanup(self.zoneinfo.stop)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()
        service._JOBS.clear()
        service._ACTIVE_BY_MONTH.clear()

    def assert_no_external_access(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_trusted_start_owns_job_and_captures_exact_context_without_thread_run(self):
        context = trusted_context("logan-synthetic")
        with patch(
            "app.services.county_commission_report_service.Thread",
            CapturedThread,
        ):
            public_job = service.start_county_commission_job(
                "2026-06",
                tenant_context=context,
            )

        internal = service._JOBS[public_job["job_id"]]
        self.assertEqual(internal["_owner_key"], "logan-synthetic")
        self.assertIs(internal["_tenant_context"], context)
        self.assertNotIn("_owner_key", public_job)
        self.assertNotIn("_tenant_context", public_job)
        self.assertEqual(
            service._ACTIVE_BY_MONTH[("logan-synthetic", "2026-06")],
            public_job["job_id"],
        )
        self.assertEqual(len(CapturedThread.instances), 1)
        thread = CapturedThread.instances[0]
        self.assertTrue(thread.started)
        self.assertIs(thread.args[2], context)
        self.assert_no_external_access()

    def test_cross_tenant_lookup_is_indistinguishable_from_missing(self):
        logan = trusted_context("logan-synthetic")
        northstar = trusted_context("northstar-fictional")
        northstar_profile = load_builtin_county_profile("northstar-fictional")
        northstar_profile = replace(
            northstar_profile,
            modules=northstar_profile.modules | {"county_commission_report"},
        )
        with patch(
            "app.services.county_commission_report_service.Thread",
            CapturedThread,
        ):
            job = service.start_county_commission_job(
                "2026-06",
                tenant_context=logan,
            )

        original_resolver = service.resolve_county_profile
        with patch(
            "app.services.county_commission_report_service.resolve_county_profile",
            side_effect=lambda context: (
                northstar_profile
                if context.tenant_id == "northstar-fictional"
                else original_resolver(context)
            ),
        ):
            self.assertIsNone(
                service.get_county_commission_job(
                    job["job_id"],
                    tenant_context=northstar,
                )
            )
            self.assertIsNone(
                service.get_county_commission_job(
                    "missing-job",
                    tenant_context=northstar,
                )
            )
        self.assert_no_external_access()

    def test_denied_start_precedes_month_lock_thread_and_client(self):
        context = trusted_context("logan-synthetic")
        profile = load_builtin_county_profile("logan-synthetic")
        disabled = replace(
            profile,
            modules=profile.modules - {"county_commission_report"},
        )
        with (
            patch(
                "app.services.county_commission_report_service.resolve_county_profile",
                return_value=disabled,
            ),
            patch(
                "app.services.county_commission_report_service.resolve_report_month",
                side_effect=AssertionError("month resolved before denial"),
            ) as month_mock,
            patch.object(service, "_JOB_LOCK", ForbiddenLock()),
            patch(
                "app.services.county_commission_report_service.Thread",
                side_effect=AssertionError("thread created before denial"),
            ) as thread_mock,
        ):
            with self.assertRaises(TenantAuthorizationDenied):
                service.start_county_commission_job(
                    "2026-06",
                    tenant_context=context,
                )
        month_mock.assert_not_called()
        thread_mock.assert_not_called()
        self.assertEqual(service._JOBS, {})
        self.assert_no_external_access()

    def test_active_month_keys_are_tenant_scoped(self):
        logan = trusted_context("logan-synthetic")
        northstar = trusted_context("northstar-fictional")
        northstar_profile = load_builtin_county_profile("northstar-fictional")
        northstar_profile = replace(
            northstar_profile,
            modules=northstar_profile.modules | {"county_commission_report"},
        )
        original_resolver = service.resolve_county_profile
        with (
            patch(
                "app.services.county_commission_report_service.resolve_county_profile",
                side_effect=lambda context: (
                    northstar_profile
                    if context.tenant_id == "northstar-fictional"
                    else original_resolver(context)
                ),
            ),
            patch(
                "app.services.county_commission_report_service.Thread",
                CapturedThread,
            ),
        ):
            logan_job = service.start_county_commission_job(
                "2026-06",
                tenant_context=logan,
            )
            northstar_job = service.start_county_commission_job(
                "2026-06",
                tenant_context=northstar,
            )

        self.assertNotEqual(logan_job["job_id"], northstar_job["job_id"])
        self.assertEqual(
            set(service._ACTIVE_BY_MONTH),
            {
                ("logan-synthetic", "2026-06"),
                ("northstar-fictional", "2026-06"),
            },
        )
        self.assert_no_external_access()

    def test_worker_forwards_exact_context_and_sanitizes_authorization_failure(self):
        context = trusted_context("logan-synthetic")
        job_id = "synthetic-job"
        service._JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "started_at": "",
            "completed_at": "",
            "message": "",
            "_owner_key": "logan-synthetic",
            "_tenant_context": context,
        }
        service._ACTIVE_BY_MONTH[("logan-synthetic", "2026-06")] = job_id

        def deny(*args, **kwargs):
            self.assertIs(kwargs["tenant_context"], context)
            raise TenantAuthorizationDenied("synthetic denial")

        with patch(
            "app.services.county_commission_report_service.build_county_commission_report",
            side_effect=deny,
        ):
            service._run_job(job_id, "2026-06", context)

        internal = service._JOBS[job_id]
        self.assertEqual(internal["status"], "failed")
        self.assertEqual(internal["message"], "The monthly report could not be completed.")
        self.assertNotIn(("logan-synthetic", "2026-06"), service._ACTIVE_BY_MONTH)
        self.assert_no_external_access()

    def test_legacy_none_keeps_unscoped_lookup_and_call_shapes(self):
        with (
            patch(
                "app.services.county_commission_report_service.resolve_county_profile",
                side_effect=AssertionError("legacy path resolved tenant"),
            ) as resolve_mock,
            patch(
                "app.services.county_commission_report_service.authorize_tenant_action",
                side_effect=AssertionError("legacy path authorized tenant"),
            ) as authorize_mock,
            patch(
                "app.services.county_commission_report_service.Thread",
                CapturedThread,
            ),
        ):
            public_job = service.start_county_commission_job("2026-06")
            retrieved = service.get_county_commission_job(public_job["job_id"])

        resolve_mock.assert_not_called()
        authorize_mock.assert_not_called()
        self.assertEqual(retrieved["job_id"], public_job["job_id"])
        self.assertEqual(CapturedThread.instances[0].args[2], None)
        self.assert_no_external_access()


if __name__ == "__main__":
    unittest.main()
