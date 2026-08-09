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

    def test_logout_expires_every_alb_session_cookie(self):
        """The ALB splits its session across numbered cookies; leaving any
        one alive can leave the session usable."""
        response = self.client.get("/logout", follow_redirects=False)
        expired = "".join(response.headers.get_list("set-cookie"))

        for index in range(4):
            self.assertIn(f"AWSELBAuthSessionCookie-{index}=", expired)
        # Attributes must mirror what the ALB set or the browser keeps the
        # original cookie.
        self.assertIn("Path=/", expired)
        self.assertIn("Secure", expired)
        self.assertIn("HttpOnly", expired)

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
