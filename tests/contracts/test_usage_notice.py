"""The confidentiality notice: shown after sign-in, accepted once per session.

What is under test is the contract in app/core/usage_notice.py:

* a person opening a page without having accepted is sent to the notice, and
  comes back to the page they asked for;
* scripts, API calls, and the event stream are never interrupted;
* every role, including the restricted tiers, can actually reach the notice
  (a deny-by-default tier that could not reach it would loop forever);
* the "next" target cannot be used to bounce someone to another site;
* the notice is a notice, not an access control: it never changes what any
  role is allowed to see.

Roles are signed in the way production does it, by patching
``app.main.resolve_alb_identity``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core import usage_notice
from app.core.alb_identity import AlbIdentity
from app.main import app

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
COOKIE = usage_notice.COOKIE_NAME
VERSION = usage_notice.NOTICE_VERSION


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


ROLES = {
    "user": _identity("v@nga911.com", "lcdash-pilot-user"),
    "avatar": _identity("booth@911logan.com", "lcdash-pilot-avatar"),
    "fire_officer": _identity("chief@911logan.com", "lcdash-pilot-fire-officer"),
    "dispatcher": _identity("d@911logan.com", "lcdash-pilot-dispatcher"),
    "supervisor": _identity("s@911logan.com", "lcdash-pilot-supervisor"),
    "admin": _identity("a@911logan.com", "lcdash-pilot-admin"),
}


class _NoticeTestCase(unittest.TestCase):
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

    def _sign_in_as(self, role: str) -> None:
        patcher = patch("app.main.resolve_alb_identity", return_value=ROLES[role])
        self.addCleanup(patcher.stop)
        patcher.start()

    def _get(self, path: str, *, accepted: bool = False, headers=None):
        client = TestClient(app)
        if accepted:
            client.cookies.set(COOKIE, VERSION)
        return client.get(path, headers=headers or HTML, follow_redirects=False)


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


class UsageNoticeHelperTests(unittest.TestCase):
    def test_next_only_ever_returns_a_local_path(self):
        for hostile in (
            "//evil.example",
            "//evil.example/dashboard",
            "/\\evil.example",
            "\\\\evil.example",
            "https://evil.example",
            "http://evil.example/x",
            "javascript:alert(1)",
            "dashboard",
            "",
            None,
            "/ok\r\nSet-Cookie: x=1",
            "/ok\nLocation: https://evil.example",
        ):
            with self.subTest(next=hostile):
                self.assertEqual(usage_notice.safe_next(hostile), "/")

    def test_next_keeps_ordinary_local_targets_and_never_loops(self):
        self.assertEqual(usage_notice.safe_next("/dashboard"), "/dashboard")
        self.assertEqual(usage_notice.safe_next("/map?x=1&y=2"), "/map?x=1&y=2")
        self.assertEqual(usage_notice.safe_next("/calls/CFS26-1"), "/calls/CFS26-1")
        for loop in ("/notice", "/notice/", "/notice?next=/dashboard"):
            with self.subTest(next=loop):
                self.assertEqual(usage_notice.safe_next(loop), "/")

    def test_exempt_paths(self):
        for path in (
            "/notice",
            "/notice/",
            "/logout",
            "/health",
            "/static/css/lcdash-core.css",
            "/api/operations/snapshot",
            "/api/integrations/centralsquare/webhooks/cfs",
        ):
            with self.subTest(path=path):
                self.assertTrue(usage_notice.is_gate_exempt(path))
        for path in ("/", "/dashboard", "/calls/CFS26-1", "/analytics", "/mae"):
            with self.subTest(path=path):
                self.assertFalse(usage_notice.is_gate_exempt(path))

    def test_only_page_navigations_are_stopped(self):
        self.assertTrue(usage_notice.wants_html_page("GET", "text/html,*/*;q=0.8"))
        self.assertFalse(usage_notice.wants_html_page("GET", "*/*"))
        self.assertFalse(usage_notice.wants_html_page("GET", "application/json"))
        self.assertFalse(usage_notice.wants_html_page("GET", "text/event-stream"))
        self.assertFalse(usage_notice.wants_html_page("GET", None))
        self.assertFalse(usage_notice.wants_html_page("POST", "text/html"))

    def test_acceptance_is_tied_to_the_current_wording(self):
        self.assertTrue(usage_notice.has_accepted(VERSION))
        self.assertFalse(usage_notice.has_accepted("an-older-version"))
        self.assertFalse(usage_notice.has_accepted(""))
        self.assertFalse(usage_notice.has_accepted(None))

    def test_the_notice_says_what_it_has_to_say(self):
        text = " ".join(usage_notice.NOTICE_PARAGRAPHS).lower()
        for phrase in (
            "confidential",
            "authorized users",
            "screenshot",
            "reproduce",
            "standards and ethics of emergency services",
            "monitored and logged",
            "disciplinary action",
            "civil or criminal",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertIn("screenshot", usage_notice.FOOTER_TEXT.lower())
        self.assertIn("confidential", usage_notice.FOOTER_TEXT.lower())


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


class UsageNoticeGateTests(_NoticeTestCase):
    def test_a_page_without_acceptance_goes_to_the_notice_and_remembers_where(self):
        self._sign_in_as("supervisor")
        response = self._get("/dashboard")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"], f"/notice?next={quote('/dashboard', safe='')}"
        )

    def test_the_query_string_survives_the_round_trip(self):
        self._sign_in_as("supervisor")
        response = self._get("/map?hours=8")
        self.assertEqual(
            response.headers["location"],
            f"/notice?next={quote('/map?hours=8', safe='')}",
        )

    def test_an_accepted_session_is_not_redirected(self):
        self._sign_in_as("supervisor")
        response = self._get("/dashboard", accepted=True)
        self.assertNotEqual(response.status_code, 303)

    def test_a_stale_acceptance_is_asked_again(self):
        self._sign_in_as("supervisor")
        client = TestClient(app)
        client.cookies.set(COOKIE, "older-wording")
        response = client.get("/dashboard", headers=HTML, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_scripts_and_apis_are_never_interrupted(self):
        self._sign_in_as("supervisor")
        for headers in ({"Accept": "*/*"}, {"Accept": "application/json"}, {}):
            for path in ("/api/identity/whoami", "/health"):
                with self.subTest(path=path, accept=headers.get("Accept")):
                    client = TestClient(app)
                    response = client.get(path, headers=headers, follow_redirects=False)
                    self.assertNotEqual(response.status_code, 303)

    def test_api_paths_are_exempt_even_when_asked_for_as_html(self):
        self._sign_in_as("supervisor")
        response = self._get("/api/identity/whoami")
        self.assertEqual(response.status_code, 200)

    def test_health_logout_and_static_are_exempt(self):
        self._sign_in_as("supervisor")
        for path in ("/health", "/logout", "/static/css/lcdash-core.css"):
            with self.subTest(path=path):
                response = self._get(path)
                self.assertNotEqual(
                    response.headers.get("location", "").split("?")[0], "/notice"
                )

    def test_the_gate_never_changes_what_a_role_may_see(self):
        """Accepting the notice must not widen access, and not accepting must
        not be what denies it: the tier gates still answer for themselves."""
        self._sign_in_as("fire_officer")
        # Accepted: the fire officer tier still refuses analytics.
        self.assertEqual(self._get("/analytics", accepted=True).status_code, 403)
        self._sign_in_as("user")
        self.assertEqual(self._get("/active-calls", accepted=True).status_code, 403)


class EveryRoleCanReachTheNoticeTests(_NoticeTestCase):
    def test_every_role_can_open_the_notice_without_looping(self):
        for role in ROLES:
            with self.subTest(role=role):
                self._sign_in_as(role)
                response = self._get("/notice?next=/dashboard")
                self.assertEqual(response.status_code, 200, role)
                self.assertIn(usage_notice.NOTICE_TITLE, response.text)

    def test_every_role_is_sent_to_the_notice_before_any_tier_gate_speaks(self):
        # Avatar accounts land on /mae/avatar; the restricted user tier would
        # 403 /analytics. Neither should be told "denied" before being shown
        # the notice.
        for role, path in (
            ("avatar", "/mae/avatar"),
            ("user", "/dashboard"),
            ("fire_officer", "/dashboard"),
            ("dispatcher", "/dashboard"),
            ("admin", "/dashboard"),
        ):
            with self.subTest(role=role):
                self._sign_in_as(role)
                response = self._get(path)
                self.assertEqual(response.status_code, 303, role)
                self.assertTrue(response.headers["location"].startswith("/notice?next="))


# --------------------------------------------------------------------------
# The notice page and accepting it
# --------------------------------------------------------------------------


class NoticePageTests(_NoticeTestCase):
    def test_page_shows_the_full_notice_and_a_required_checkbox(self):
        self._sign_in_as("supervisor")
        body = self._get("/notice?next=/units").text
        self.assertIn(usage_notice.NOTICE_TITLE.replace("–", "&#8211;"), body.replace("–", "&#8211;"))
        for paragraph in usage_notice.NOTICE_PARAGRAPHS:
            self.assertIn(paragraph.split(",")[0][:40], body)
        self.assertIn('type="checkbox"', body)
        self.assertIn("required", body)
        self.assertIn('name="next" value="/units"', body)
        self.assertIn('href="/logout"', body)

    def test_page_is_not_cached_and_carries_no_dashboard_navigation(self):
        self._sign_in_as("supervisor")
        response = self._get("/notice")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertNotIn("sidebar-navigation", response.text)

    def test_a_hostile_next_is_neutralised_on_the_page(self):
        self._sign_in_as("supervisor")
        body = self._get("/notice?next=//evil.example/x").text
        self.assertNotIn("evil.example", body)
        self.assertIn('name="next" value="/"', body)

    def test_accepting_sets_one_session_long_cookie_and_returns_to_the_page(self):
        self._sign_in_as("supervisor")
        client = TestClient(app)
        response = client.post(
            "/notice",
            data={"next": "/units", "agree": "yes"},
            headers=HTML,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/units")
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{COOKIE}={VERSION}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn(f"Max-Age={usage_notice.COOKIE_MAX_AGE_SECONDS}", cookie)
        self.assertIn("Path=/", cookie)

    def test_the_cookie_is_secure_behind_the_https_load_balancer(self):
        self._sign_in_as("supervisor")
        response = TestClient(app).post(
            "/notice",
            data={"next": "/", "agree": "yes"},
            headers={**HTML, "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_not_ticking_the_box_is_refused_and_sets_nothing(self):
        self._sign_in_as("supervisor")
        for data in ({"next": "/units"}, {"next": "/units", "agree": ""}, {"next": "/units", "agree": "no"}):
            with self.subTest(data=data):
                response = TestClient(app).post(
                    "/notice", data=data, headers=HTML, follow_redirects=False
                )
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("set-cookie", response.headers)
                self.assertIn("Please confirm", response.text)

    def test_accepting_cannot_be_used_to_leave_the_site(self):
        self._sign_in_as("supervisor")
        for hostile in ("//evil.example", "https://evil.example", "/\\evil.example"):
            with self.subTest(next=hostile):
                response = TestClient(app).post(
                    "/notice",
                    data={"next": hostile, "agree": "yes"},
                    headers=HTML,
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/")

    def test_full_flow_page_then_notice_then_page(self):
        """What a person actually experiences, end to end."""
        self._sign_in_as("supervisor")
        client = TestClient(app)
        first = client.get("/units", headers=HTML, follow_redirects=False)
        self.assertEqual(first.status_code, 303)
        notice = client.get(first.headers["location"], headers=HTML, follow_redirects=False)
        self.assertEqual(notice.status_code, 200)
        accepted = client.post(
            "/notice",
            data={"next": "/units", "agree": "yes"},
            headers=HTML,
            follow_redirects=False,
        )
        self.assertEqual(accepted.headers["location"], "/units")
        again = client.get("/units", headers=HTML, follow_redirects=False)
        self.assertNotEqual(again.status_code, 303)


# --------------------------------------------------------------------------
# The footer
# --------------------------------------------------------------------------


class FooterTests(_NoticeTestCase):
    def test_dashboard_pages_carry_the_confidentiality_line(self):
        self._sign_in_as("supervisor")
        for path in ("/dashboard", "/units", "/active-calls", "/map", "/station-alerts"):
            with self.subTest(path=path):
                response = self._get(path, accepted=True)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn("usage-footer", response.text)
                self.assertIn("CONFIDENTIAL", response.text)
                self.assertIn("Do not copy, screenshot, or distribute", response.text)

    def test_the_restricted_tiers_see_it_too(self):
        for role in ("user", "fire_officer"):
            with self.subTest(role=role):
                self._sign_in_as(role)
                response = self._get("/dashboard", accepted=True)
                self.assertEqual(response.status_code, 200, role)
                self.assertIn("CONFIDENTIAL", response.text)


if __name__ == "__main__":
    unittest.main()
