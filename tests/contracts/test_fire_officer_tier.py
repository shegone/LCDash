"""The ``fire_officer`` tier: live operations with full call detail, nothing else.

Fire officers see the WHOLE call -- so unlike the sanitized ``user`` tier there
is no field reduction to test, and a test below pins that the payloads are NOT
reduced. What is under test is the other half: the deny-by-default path
allowlist that keeps this role out of analytics, reports, the knowledge
library, MAE, Mindshare/JACK, the tools pages, and admin.

``test_every_unlisted_route_is_denied_for_fire_officer`` walks the app's real
route table, so a route added later is denied for this role the day it ships or
this suite fails. ``test_every_link_a_fire_officer_is_shown_is_reachable``
renders each allowed page and checks that every internal link on it is one the
gate would let through, so the sidebar cannot offer a door that answers 403.

Roles are signed in the way production does it, by patching
``app.main.resolve_alb_identity`` to return the Cognito group claim the ALB
would have verified.
"""

from __future__ import annotations

import itertools
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core import fire_officer_tier, sanitized_tier
from app.core.alb_identity import AlbIdentity
from app.core.cloud_pilot_roles import (
    ROLE_PERMISSIONS,
    PilotRole,
    resolve_pilot_role,
)
from app.main import app

from .test_sanitized_tier import (
    _full_map_snapshot,
    _full_operations_snapshot,
    _full_station_alert_snapshot,
)

# Pages and APIs the role exists to reach, in the form a browser requests them.
ALLOWED_PATHS = (
    "/dashboard",
    "/active-calls",
    "/units",
    "/calls/CFS26-1234",
    "/map",
    "/station-alerts",
    "/api/operations/snapshot",
    "/api/operations/active-calls",
    "/api/operations/units",
    "/api/operations/map",
    "/api/operations/map/tile-styles",
    "/api/operations/station-alerts",
    "/api/identity/whoami",
    "/health",
    "/logout",
)

# Everything the role is NOT for. The route-table sweep gives the full
# guarantee; this list exists so a failure names the doors that matter most.
DENIED_PATHS = (
    "/analytics",
    "/api/analytics/overview",
    "/api/analytics/widgets",
    "/reports",
    "/api/reports/county-commission/jobs/job-1",
    "/map/heatmap",
    "/api/operations/map/heatmap",
    "/callflow-cards",
    "/knowledge",
    "/api/knowledge/status",
    "/mae",
    "/mae/avatar",
    "/mae/reliability",
    "/api/mae/chat",
    "/api/mae/status",
    "/mindshare",
    "/mindshare/technical",
    "/api/mindshare/status",
    "/nga911-intelligence",
    "/api/nga911/v1/counties",
    "/voice",
    "/api/voice/status",
    "/api/cloud-ai/advisory",
    "/integrations/health",
    "/api/integrations/centralsquare/health",
    "/admin/users",
    "/api/admin/users",
    "/api/pilot/readiness",
)

DENIAL_DETAIL = (
    "This view is not part of the fire officer role. "
    "Ask an administrator if you need access."
)

_PATH_PARAM_RE = re.compile(r"\{[^{}]+\}")
_HREF_RE = re.compile(r"""href\s*=\s*["'](/[^"'#?\s]*)""")


def _concrete_path(path: str) -> str:
    counter = itertools.count(1)
    return _PATH_PARAM_RE.sub(lambda _match: f"TEST-VALUE-{next(counter)}", path)


def _identity(email: str, *groups: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=tuple(groups), email=email)


FIRE_OFFICER = _identity("chief@911logan.com", "lcdash-pilot-fire-officer")
USER = _identity("vendor@nga911.com", "lcdash-pilot-user")
DISPATCHER = _identity("d@911logan.com", "lcdash-pilot-dispatcher")
SUPERVISOR = _identity("s@911logan.com", "lcdash-pilot-supervisor")
ADMIN = _identity("a@911logan.com", "lcdash-pilot-admin")


class _FireOfficerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def _sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _mock_data_sources(self) -> None:
        for target, value in (
            ("app.main._current_operations_snapshot", _full_operations_snapshot()),
            ("app.main.get_live_map_snapshot", _full_map_snapshot()),
            ("app.main.get_live_station_alert_snapshot", _full_station_alert_snapshot()),
            ("app.main.build_station_alert_snapshot", _full_station_alert_snapshot()),
            ("app.main._cloud_cad_bridge_enabled", True),
        ):
            patcher = patch(target, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()


# --------------------------------------------------------------------------
# The role contract
# --------------------------------------------------------------------------


class FireOfficerRoleContractTests(unittest.TestCase):
    def test_group_maps_to_the_fire_officer_role(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-fire-officer"]),
            PilotRole.FIRE_OFFICER,
        )
        self.assertEqual(str(PilotRole.FIRE_OFFICER), "fire_officer")

    def test_wider_roles_outrank_fire_officer_and_it_outranks_user_and_avatar(self):
        for group, expected in (
            ("lcdash-pilot-admin", PilotRole.ADMIN),
            ("lcdash-pilot-supervisor", PilotRole.SUPERVISOR),
            ("lcdash-pilot-dispatcher", PilotRole.DISPATCHER),
            ("lcdash-pilot-user", PilotRole.FIRE_OFFICER),
            ("lcdash-pilot-avatar", PilotRole.FIRE_OFFICER),
        ):
            with self.subTest(group=group):
                self.assertEqual(
                    resolve_pilot_role(["lcdash-pilot-fire-officer", group]),
                    expected,
                )

    def test_permissions_are_the_four_operational_views_and_nothing_more(self):
        self.assertEqual(
            ROLE_PERMISSIONS[PilotRole.FIRE_OFFICER],
            frozenset(
                {
                    "pilot.readiness.view",
                    "dashboard.synthetic.view",
                    "station.alerts.view",
                    "map.view",
                }
            ),
        )
        for forbidden in (
            "analytics.review.view",
            "documents.review.view",
            "rag.advisory.query",
            "voice.advisory.use",
            "avatar.converse",
            "pilot.access.review",
        ):
            self.assertNotIn(forbidden, ROLE_PERMISSIONS[PilotRole.FIRE_OFFICER])


class FireOfficerAllowlistUnitTests(unittest.TestCase):
    def test_restricts_only_the_fire_officer_role(self):
        self.assertTrue(fire_officer_tier.restricts("fire_officer"))
        self.assertTrue(fire_officer_tier.restricts(PilotRole.FIRE_OFFICER))
        for role in ("user", "supervisor", "dispatcher", "admin", "avatar", "", None):
            with self.subTest(role=role):
                self.assertFalse(fire_officer_tier.restricts(role))

    def test_fire_officer_is_not_field_sanitized(self):
        """Seeing the whole call is the point; do not reduce it."""
        self.assertFalse(sanitized_tier.restricts("fire_officer"))

    def test_path_normalization_matches_the_other_tiers(self):
        allowed = fire_officer_tier.is_path_allowed_for_fire_officer
        self.assertTrue(allowed("/dashboard"))
        self.assertTrue(allowed("/dashboard/"))
        self.assertTrue(allowed("/dashboard?x=1"))
        self.assertTrue(allowed("/calls/CFS26-1234"))
        self.assertTrue(allowed("/static/js/x.js"))
        self.assertTrue(allowed(""))  # "/" redirects to the dashboard
        self.assertFalse(allowed("/analytics"))
        self.assertFalse(allowed("/map/heatmap"))
        self.assertFalse(allowed("/callflow-cards"))
        self.assertFalse(allowed("/calls"))
        self.assertFalse(allowed("/mae"))

    def test_no_analytics_knowledge_ai_or_admin_path_is_on_the_allowlist(self):
        """The role's whole premise: operations only.

        If someone lists one of these for fire officers they are changing what
        the role *is*, and this test makes that decision loud.
        """
        forbidden = (
            "/analytics",
            "/api/analytics/",
            "/reports",
            "/api/reports/",
            "/knowledge",
            "/api/knowledge/",
            "/mae",
            "/api/mae/",
            "/mindshare",
            "/api/mindshare/",
            "/nga911",
            "/api/nga911/",
            "/voice",
            "/api/voice/",
            "/api/cloud-ai/",
            "/integrations",
            "/api/integrations/",
            "/admin",
            "/api/admin/",
            "/callflow-cards",
            "/map/heatmap",
            "/api/operations/map/heatmap",
        )
        listed = set(fire_officer_tier._FIRE_OFFICER_ALLOWED_EXACT) | set(
            fire_officer_tier._FIRE_OFFICER_ALLOWED_PREFIXES
        )
        for path in listed:
            with self.subTest(path=path):
                for prefix in forbidden:
                    self.assertFalse(
                        path.startswith(prefix),
                        f"{path} would open {prefix} to the fire officer role",
                    )


# --------------------------------------------------------------------------
# The path gate
# --------------------------------------------------------------------------


class FireOfficerPathGateTests(_FireOfficerTestCase):
    def test_every_allowed_path_is_reachable(self):
        """A gate that denies everything is trivially safe and useless."""
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        for path in ALLOWED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertNotEqual(
                    response.status_code, 403, f"{path} was denied to fire officers"
                )

    def test_root_sends_fire_officers_to_the_dashboard(self):
        self._sign_in_as(FIRE_OFFICER)
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")

    def test_representative_denied_paths_are_denied(self):
        self._sign_in_as(FIRE_OFFICER)
        for path in DENIED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], DENIAL_DETAIL)

    def test_post_routes_are_denied_too(self):
        self._sign_in_as(FIRE_OFFICER)
        for path in (
            "/api/mae/chat",
            "/api/cloud-ai/advisory",
            "/api/mindshare/chat",
            "/api/analytics/widgets",
            "/api/reports/county-commission/jobs",
            "/api/admin/users",
        ):
            with self.subTest(path=path):
                response = self.client.post(path, json={}, follow_redirects=False)
                self.assertEqual(response.status_code, 403)

    def test_every_unlisted_route_is_denied_for_fire_officer(self):
        """Deny-by-default, proved against the app's real route table."""
        self._sign_in_as(FIRE_OFFICER)

        checked = 0
        for path in sorted({getattr(route, "path", "") for route in app.routes}):
            if not path:
                continue
            concrete = _concrete_path(path)
            if fire_officer_tier.is_path_allowed_for_fire_officer(concrete):
                continue
            with self.subTest(path=path, concrete=concrete):
                response = self.client.get(concrete, follow_redirects=False)
                self.assertIn(
                    response.status_code,
                    (403, 404),
                    f"{path} is not on the fire officer allowlist but was not "
                    f"denied (got {response.status_code} for {concrete})",
                )
            checked += 1

        self.assertGreater(
            checked, 40, "route enumeration collapsed -- the sweep proves nothing"
        )

    def test_query_strings_and_trailing_slashes_do_not_open_a_path(self):
        self._sign_in_as(FIRE_OFFICER)
        for path in (
            "/analytics?next=/dashboard",
            "/analytics/",
            "/map/heatmap?hours=8",
            "/mae/?x=/dashboard",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)

    def test_other_roles_are_not_narrowed_by_this_gate(self):
        """The tier middlewares must stay independent of each other."""
        self._mock_data_sources()
        for identity in (SUPERVISOR, ADMIN, DISPATCHER):
            with self.subTest(role=identity.groups[0]):
                self._sign_in_as(identity)
                for path in ("/analytics", "/callflow-cards", "/map/heatmap"):
                    response = self.client.get(path, follow_redirects=False)
                    self.assertNotEqual(response.status_code, 403, path)

    def test_user_tier_is_unchanged(self):
        """Fire officer is a separate tier; the sanitized user tier keeps its
        own, narrower gate (no active calls, no units, no call detail)."""
        self._mock_data_sources()
        self._sign_in_as(USER)
        for path in ("/active-calls", "/units", "/calls/CFS26-1234"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)


# --------------------------------------------------------------------------
# Full call detail: the data is NOT reduced
# --------------------------------------------------------------------------


class FireOfficerSeesFullCallsTests(_FireOfficerTestCase):
    def test_snapshot_is_unreduced_for_fire_officer_and_reduced_for_user(self):
        self._mock_data_sources()

        self._sign_in_as(FIRE_OFFICER)
        full = self.client.get("/api/operations/snapshot")
        self.assertEqual(full.status_code, 200)
        body = full.json()
        self.assertNotIn("sanitized_view", body)
        call = body["calls"][0]
        self.assertEqual(call["cfs_number"], "CFS26-1234")
        self.assertEqual(call["reporter_name"], "Jane Caller")
        self.assertEqual(call["reporter_phone"], "304-555-0101")
        self.assertEqual(call["narrative"], "Caller states her husband is unresponsive")
        self.assertEqual(len(call["command_logs"]), 1)
        self.assertEqual(call["assigned_units"][0]["unit_number"], "M1")
        self.assertIn("agency_summary", body["dashboard_stats"])

        self._sign_in_as(USER)
        reduced = self.client.get("/api/operations/snapshot").json()
        self.assertTrue(reduced["sanitized_view"])
        self.assertNotIn("reporter_name", reduced["calls"][0])

    def test_map_and_station_alerts_are_unreduced_for_fire_officer(self):
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)

        map_body = self.client.get("/api/operations/map").json()
        props = map_body["features"][0]["properties"]
        self.assertEqual(props["cfs_number"], "CFS26-1234")
        self.assertEqual(props["detail_url"], "/calls/CFS26-1234")

        alerts = self.client.get("/api/operations/station-alerts").json()
        self.assertEqual(alerts["alerts"][0]["cfs_number"], "CFS26-1234")
        self.assertIn("announcement", alerts["alerts"][0])

    def test_dashboard_page_shows_the_call_and_links_to_its_detail(self):
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        body = self.client.get("/dashboard").text
        self.assertIn("CFS26-1234", body)
        self.assertIn("/calls/CFS26-1234", body)
        self.assertNotIn("Restricted Operations View", body)

    def test_call_detail_page_is_served_not_denied(self):
        """The user tier gets 403 here; fire officers get the page itself.

        With no incident in the (empty) runtime snapshot the page renders its
        "not available" state, which is enough to prove the gate let the
        request through to the real route rather than answering for it.
        """
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        response = self.client.get("/calls/CFS26-1234")
        self.assertEqual(response.status_code, 200)
        self._sign_in_as(USER)
        self.assertEqual(self.client.get("/calls/CFS26-1234").status_code, 403)


# --------------------------------------------------------------------------
# What the page offers must match what the gate allows
# --------------------------------------------------------------------------


class FireOfficerNavigationTests(_FireOfficerTestCase):
    HIDDEN_ENTRIES = (
        "/analytics",
        "/reports",
        "/nga911-intelligence",
        "/mae",
        "/mae/avatar",
        "/mae/reliability",
        "/mindshare",
        "/knowledge",
        "/integrations/health",
        "/voice",
        "/callflow-cards",
        "/map/heatmap",
        "/admin/users",
        "/admin/knowledge",
    )

    def test_sidebar_offers_operations_only(self):
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        body = self.client.get("/dashboard").text
        for path in ("/dashboard", "/active-calls", "/units", "/station-alerts", "/map"):
            with self.subTest(offered=path):
                self.assertIn(f'href="{path}"', body)
        for path in self.HIDDEN_ENTRIES:
            with self.subTest(hidden=path):
                self.assertNotIn(f'href="{path}"', body)
        self.assertNotIn("Intelligence", body)
        self.assertNotIn("Tools &amp; Quality", body)
        self.assertNotIn("Tools & Quality", body)

    def test_map_page_hides_the_recent_activity_tab(self):
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        body = self.client.get("/map").text
        self.assertNotIn("/map/heatmap", body)
        self.assertNotIn("Recent Activity", body)

        # Other roles keep it.
        self._sign_in_as(SUPERVISOR)
        self.assertIn("/map/heatmap", self.client.get("/map").text)

    def test_every_link_a_fire_officer_is_shown_is_reachable(self):
        """No dead doors: every internal link on every allowed page passes the
        gate. Catches a template edit that links to a page the role cannot
        open."""
        self._mock_data_sources()
        self._sign_in_as(FIRE_OFFICER)
        for page in ("/dashboard", "/active-calls", "/units", "/map", "/station-alerts"):
            body = self.client.get(page).text
            for link in sorted(set(_HREF_RE.findall(body))):
                with self.subTest(page=page, link=link):
                    self.assertTrue(
                        fire_officer_tier.is_path_allowed_for_fire_officer(link),
                        f"{page} links to {link}, which the fire officer role "
                        f"cannot open",
                    )

    def test_badge_reads_fire_officer(self):
        self._sign_in_as(FIRE_OFFICER)
        badge = self.client.get("/api/identity/whoami").json()
        self.assertEqual(badge["role"], "fire_officer")
        self.assertEqual(badge["role_label"], "Fire Officer")
        self.assertTrue(badge["verified"])


if __name__ == "__main__":
    unittest.main()
