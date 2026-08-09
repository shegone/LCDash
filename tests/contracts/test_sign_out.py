"""Signing out must end BOTH sessions, or it ends neither in practice.

The trap this pins: expiring the ALB's session cookie looks like a sign-out,
but the ALB simply re-authenticates against Cognito, whose own session is
still valid, and the user lands back inside without a password. On a shared
dispatch workstation that is worse than having no button, because it looks
like it worked. So /logout expires the ALB cookies AND hands off to Cognito's
logout endpoint.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app


class SignOutTests(unittest.TestCase):
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
            (
                "alb_identity_hosted_ui_url",
                "https://lcdash-p1-logan-use1-20260804.auth.us-east-1.amazoncognito.com",
            ),
            ("alb_identity_signed_out_url", "https://aws.logan911.com/"),
            ("alb_identity_session_cookie", "LCDashPilotAuth"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_logout_redirects_to_cognito_with_client_and_return_url(self):
        response = self.client.get("/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        location = urlsplit(response.headers["location"])
        self.assertEqual(
            location.netloc,
            "lcdash-p1-logan-use1-20260804.auth.us-east-1.amazoncognito.com",
        )
        self.assertEqual(location.path, "/logout")
        query = parse_qs(location.query)
        self.assertEqual(query["client_id"], ["1example23456789"])
        self.assertEqual(query["logout_uri"], ["https://aws.logan911.com/"])

    def test_logout_expires_the_configured_cookie_name_not_the_aws_default(self):
        """The bug that made sign-out do nothing: this deployment sets a
        custom session_cookie_name, and the first version expired AWS's
        default name, so it deleted cookies that never existed."""
        response = self.client.get("/logout", follow_redirects=False)
        expired = "".join(response.headers.get_list("set-cookie"))

        for index in range(4):
            self.assertIn(f"LCDashPilotAuth-{index}=", expired)
        self.assertNotIn("AWSELBAuthSessionCookie", expired)
        self.assertIn("Path=/", expired)
        self.assertIn("Secure", expired)
        self.assertIn("HttpOnly", expired)

    def test_logout_expires_both_host_only_and_domain_scoped_cookies(self):
        """A browser treats these as different cookies; expiring only one
        identity can leave the session alive."""
        response = self.client.get("/logout", follow_redirects=False)
        cookies = response.headers.get_list("set-cookie")

        host_only = [c for c in cookies if "LCDashPilotAuth-0=" in c and "Domain=" not in c]
        domain_scoped = [c for c in cookies if "LCDashPilotAuth-0=" in c and "Domain=" in c]
        self.assertTrue(host_only)
        self.assertTrue(domain_scoped)

    def test_logout_also_clears_the_in_flight_login_nonce(self):
        """AWSALBAuthNonce is written while a login is in flight and its name
        is fixed by AWS regardless of session_cookie_name. Nothing is in
        flight at sign-out, so leaving one behind is stale state a later
        login has no reason to inherit."""
        response = self.client.get("/logout", follow_redirects=False)
        expired = "".join(response.headers.get_list("set-cookie"))
        self.assertIn("AWSALBAuthNonce=", expired)

    def test_cookie_name_follows_settings(self):
        """Name comes from the stack, so app and listener cannot drift."""
        with patch.object(settings, "alb_identity_session_cookie", "SomeOtherName"):
            response = self.client.get("/logout", follow_redirects=False)
        expired = "".join(response.headers.get_list("set-cookie"))
        self.assertIn("SomeOtherName-0=", expired)

    def test_no_unauthenticated_landing_page_is_introduced(self):
        """AWS suggests a dedicated UNauthenticated logout landing page, which
        would need an ElasticLoadBalancingV2::ListenerRule --
        phase1_deployment_allowlist.json prohibits that type deliberately, so
        this ALB keeps exactly one path and every path authenticates.
        Sign-out therefore lands on the app root, where the ALB finds no
        session and shows the login page. If a landing page is ever wanted,
        the prohibition is the decision to revisit first, with Ted."""
        import json
        from pathlib import Path

        allowlist = json.loads(
            (Path(__file__).parents[2] / "infrastructure" / "phase1_deployment_allowlist.json")
            .read_text(encoding="utf-8")
        )
        self.assertIn(
            "AWS::ElasticLoadBalancingV2::ListenerRule",
            allowlist["prohibited_resource_types"],
        )
        # No bypass route may exist in the app either: a landing page the ALB
        # still authenticates is unreachable after sign-out, so shipping one
        # would only be dead code implying a bypass that isn't there.
        self.assertNotIn(
            "/signed-out",
            (Path(__file__).parents[2] / "app" / "main.py").read_text(encoding="utf-8"),
        )

    def test_logout_is_never_cached(self):
        response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_unconfigured_signout_refuses_rather_than_half_working(self):
        with patch.object(settings, "alb_identity_hosted_ui_url", ""):
            response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 503)

    def test_button_shows_only_for_a_verified_session(self):
        with patch(
            "app.main.resolve_alb_identity",
            return_value=AlbIdentity(
                subject="sub-1",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            ),
        ):
            signed_in = self.client.get("/reports")
        self.assertIn('href="/logout"', signed_in.text)
        self.assertIn("Sign out", signed_in.text)

        with patch("app.main.resolve_alb_identity", return_value=None):
            anonymous = self.client.get("/reports")
        self.assertNotIn('href="/logout"', anonymous.text)


if __name__ == "__main__":
    unittest.main()


class SessionEndDetectionTests(unittest.TestCase):
    """Background traffic must not drive its own login.

    Diagnosed from CloudWatch on 2026-08-09: ELBAuthFailure non-zero with
    ELBAuthError zero, which AWS defines as an IdP denial or an authorization
    code redeemed more than once. Cognito was accepting the code, so codes
    were being used twice -- the dashboard's auto-reconnecting EventSource and
    the station-alert poll were each starting their own login flow alongside
    the user's real navigation, clobbering each other's nonce. The user got a
    bare 401 after a perfectly good MFA code and had to retype the URL.
    """

    def _read(self, relative: str) -> str:
        from pathlib import Path

        return (Path(__file__).parents[2] / relative).read_text(encoding="utf-8")

    def test_helper_stops_work_and_shows_a_banner(self):
        helper = self._read("static/js/lcdash-session.js")
        self.assertIn("opaqueredirect", helper)
        self.assertIn("/oauth2/", helper)
        self.assertIn("Your session has ended", helper)
        self.assertIn("Sign in again", helper)
        # It must never navigate on its own; the reload is the user's choice.
        self.assertNotIn("window.location.assign", helper)
        self.assertNotIn("window.location.href =", helper)

    def test_helper_loads_before_any_page_script(self):
        base = self._read("templates/layouts/base.html")
        self.assertIn("lcdash-session.js", base)
        self.assertLess(
            base.index("lcdash-session.js"),
            base.index("{% block content %}"),
            "pollers must be able to register a stop before they start",
        )

    def test_the_reconnecting_event_source_is_closed_on_session_end(self):
        dashboard = self._read("static/js/lcdash-dashboard.js")
        self.assertIn("LCDashSession.onEnd", dashboard)
        self.assertIn("realtimeSource.close()", dashboard)
        # readyState 2 is CLOSED: the browser gave up reconnecting.
        self.assertIn("readyState === 2", dashboard)

    def test_polling_checks_the_response_and_stops(self):
        alerts = self._read("static/js/lcdash-station-alerts-cloud.js")
        self.assertIn("LCDashSession.check(response)", alerts)
        self.assertIn("clearInterval(pollTimer)", alerts)
