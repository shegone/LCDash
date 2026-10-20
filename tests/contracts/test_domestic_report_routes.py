"""The domestic-calls report on the Reports page: who may run it, and how.

Starting it reads a year of CentralSquare calls, so it is limited to the same
roles that may save reports (supervisor, dispatcher, admin). Every other tier is
stopped by its own path gate, which is proved here against the real app. All
data is synthetic; the CAD connector is a fake that records what it is asked.
"""

from __future__ import annotations

import json
import unittest
from collections import OrderedDict
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core import usage_notice
from app.core.alb_identity import AlbIdentity
from app.main import app
from app.services import domestic_call_report_service as svc

HTML = {"Accept": "text/html"}
ROLES = {
    "user": ("v@nga911.com", "lcdash-pilot-user"),
    "avatar": ("booth@911logan.com", "lcdash-pilot-avatar"),
    "fire_officer": ("chief@911logan.com", "lcdash-pilot-fire-officer"),
    "dispatcher": ("d@911logan.com", "lcdash-pilot-dispatcher"),
    "supervisor": ("s@911logan.com", "lcdash-pilot-supervisor"),
    "admin": ("a@911logan.com", "lcdash-pilot-admin"),
}
START = "/api/reports/domestic-calls/jobs"


def _identity(role):
    email, group = ROLES[role]
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


def _call(cfs, code, description):
    return {
        "CFSNumber": cfs,
        "IncidentCode": [{"IsPrimary": True, "IncidentCode": {"Code": code, "Description": description}}],
        "Reporter": {"FreeformFullName": "Jane Caller"},
        "Address": {"Street": "141 Stratton Street"},
    }


class FakeConnector:
    def __init__(self, calls_by_month):
        self.calls_by_month = calls_by_month
        self.requests = []

    def search_calls(self, body, *, skip=0, limit=100):
        self.requests.append((body, skip, limit))
        rows = self.calls_by_month.get(body["RecordCreatedFrom"][:7], [])
        return {"cfs_cores": rows[skip: skip + limit]}


class FailingConnector:
    def search_calls(self, body, *, skip=0, limit=100):
        raise RuntimeError("vendor said: secret detail 141 Stratton Street")


class FakeProvider:
    def __init__(self, connector):
        self.connector = connector


class _InlineThread:
    """Run the job to completion inside start(), so tests need no waiting."""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _all_months_calls():
    # Every month of the year so far returns the same small, known set.
    return {
        f"2026-{m:02d}": [
            _call(f"C{m}-1", "DV", "Domestic Violence"),
            _call(f"C{m}-2", "MED", "Medical Alarm"),
            _call(f"C{m}-3", "DD", "Domestic Disturbance"),
            _call(f"C{m}-4", "TH", "Theft"),
        ]
        for m in range(1, 13)
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("cloud_cad_enabled", True),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
        ):
            self._patch(settings, name, value)
        self._patch(svc, "_JOBS", OrderedDict())
        self._patch(svc, "_MONTH_CACHE", {})
        self._patch(svc, "PAGE_DELAY_SECONDS", 0)
        self._patch(svc, "Thread", _InlineThread)
        self.client.cookies.set(usage_notice.COOKIE_NAME, usage_notice.NOTICE_VERSION)
        self.connector = FakeConnector(_all_months_calls())
        self._patch_target("app.main.cloud_cad_connector_provider", FakeProvider(self.connector))

    def _patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _patch_target(self, target, value):
        patcher = patch(target, value)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _sign_in_as(self, role):
        self._return_identity(_identity(role))

    def _return_identity(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _run_report(self, role="supervisor"):
        self._sign_in_as(role)
        started = self.client.post(START, json={})
        self.assertEqual(started.status_code, 200, started.text)
        return self.client.get(f"{START}/{started.json()['job_id']}")


class WhoMayRunItTests(_Base):
    def test_supervisor_dispatcher_and_admin_can_run_it(self):
        for role in ("supervisor", "dispatcher", "admin"):
            with self.subTest(role=role):
                svc._JOBS.clear()
                done = self._run_report(role)
                self.assertEqual(done.status_code, 200)
                self.assertEqual(done.json()["status"], "complete")

    def test_every_other_tier_is_stopped_before_anything_is_read(self):
        for role in ("user", "avatar", "fire_officer"):
            with self.subTest(role=role):
                self.connector.requests.clear()
                self._sign_in_as(role)
                self.assertEqual(self.client.post(START, json={}).status_code, 403)
                self.assertEqual(self.client.get(f"{START}/anything").status_code, 403)
                self.assertEqual(self.connector.requests, [])

    def test_an_unverified_caller_cannot_start_a_run(self):
        self._return_identity(None)
        response = self.client.post(START, json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.connector.requests, [])

    def test_it_refuses_when_live_cad_reads_are_not_enabled(self):
        self._patch(settings, "cloud_cad_enabled", False)
        self._sign_in_as("supervisor")
        response = self.client.post(START, json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.connector.requests, [])


class RunTests(_Base):
    def test_a_run_reports_counts_and_only_counts(self):
        done = self._run_report().json()
        result = done["result"]
        months = len(result["months"])
        self.assertGreaterEqual(months, 1)
        self.assertEqual(result["total_calls"], 4 * months)
        self.assertEqual(result["domestic_calls"], 2 * months)
        self.assertEqual(result["percent_domestic"], 50.0)
        self.assertEqual(
            {c["code"] for c in result["matched_codes"]},
            {"DV - Domestic Violence", "DD - Domestic Disturbance"},
        )
        blob = json.dumps(done)
        for private in ("Jane Caller", "141 Stratton", "CFSNumber", '"C1-1"'):
            self.assertNotIn(private, blob)

    def test_only_search_requests_are_made(self):
        self._run_report()
        self.assertTrue(self.connector.requests)
        for body, _skip, limit in self.connector.requests:
            self.assertEqual(
                set(body), {"RecordCreatedFrom", "RecordCreatedTo", "OrderByField", "OrderByDirection"}
            )
            self.assertLessEqual(limit, 100)

    def test_a_second_run_rereads_only_the_current_month(self):
        first = self._run_report().json()["result"]
        self.connector.requests.clear()
        svc._JOBS.clear()
        second = self._run_report().json()["result"]
        read = {r[0]["RecordCreatedFrom"][:7] for r in self.connector.requests}
        self.assertEqual(len(read), 1)  # just the month still in progress
        self.assertEqual(second["total_calls"], first["total_calls"])
        self.assertEqual(second["quality"]["months_reused_from_earlier_run"], len(second["months"]) - 1)

    def test_starting_while_a_run_is_active_joins_it_instead_of_starting_another(self):
        self._patch(svc, "Thread", type("NoRun", (), {"__init__": lambda self, **k: None, "start": lambda self: None}))
        self._sign_in_as("supervisor")
        first = self.client.post(START, json={}).json()
        second = self.client.post(START, json={}).json()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["status"], "queued")

    def test_a_failure_shows_a_plain_message_and_leaks_nothing(self):
        self._patch_target("app.main.cloud_cad_connector_provider", FakeProvider(FailingConnector()))
        done = self._run_report().json()
        self.assertEqual(done["status"], "failed")
        self.assertIn("could not be completed", done["message"])
        self.assertNotIn("Stratton", json.dumps(done))
        self.assertNotIn("vendor said", json.dumps(done))

    def test_a_disabled_connector_fails_cleanly(self):
        self._patch_target("app.main.cloud_cad_connector_provider", FakeProvider(None))
        self.assertEqual(self._run_report().json()["status"], "failed")

    def test_a_job_is_visible_only_to_its_own_tenant(self):
        self._sign_in_as("supervisor")
        job_id = self.client.post(START, json={}).json()["job_id"]
        self.assertIsNone(svc.get_domestic_job(job_id, "some-other-county"))
        self.assertIsNotNone(svc.get_domestic_job(job_id, "logan-synthetic"))

    def test_an_unknown_job_is_404(self):
        self._sign_in_as("supervisor")
        self.assertEqual(self.client.get(f"{START}/no-such-job").status_code, 404)


class ReportsPageTests(_Base):
    def test_page_offers_the_report_when_live_cad_is_enabled(self):
        self._sign_in_as("supervisor")
        body = self.client.get("/reports", headers=HTML).text
        self.assertIn("Domestic-Related Calls", body)
        self.assertIn("domestic-report-form", body)
        self.assertIn("lcdash-domestic-report.js", body)

    def test_page_hides_it_when_live_cad_is_not_enabled(self):
        self._patch(settings, "cloud_cad_enabled", False)
        self._sign_in_as("supervisor")
        body = self.client.get("/reports", headers=HTML).text
        self.assertNotIn("domestic-report-form", body)
        self.assertNotIn("lcdash-domestic-report.js", body)

    def test_the_county_commission_report_is_untouched(self):
        self._sign_in_as("supervisor")
        body = self.client.get("/reports", headers=HTML).text
        self.assertIn("Monthly CAD Run Report", body)


if __name__ == "__main__":
    unittest.main()
