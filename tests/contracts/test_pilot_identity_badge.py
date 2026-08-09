"""The on-screen identity badge, and its agreement with authorization.

The badge exists to make one specific failure visible. When per-user identity
cannot be established, ``get_trusted_tenant_context`` degrades to a
deployment-wide context holding the least-privileged role; pages still render
and nothing errors, so an operator cannot tell a verified supervisor apart from
an anonymous fallback. That is precisely the state the pilot was in before the
``AlbIdentityEnabled`` flag was flipped, and nothing on screen said so.

The load-bearing test here is ``test_badge_role_matches_granted_role``: a badge
that computes the role independently could reassure a user they are a
supervisor while the authorizer disagrees. Both must come from
``_resolve_pilot_identity`` or the display is a lie.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import (
    get_trusted_tenant_context,
    identity_whoami_api,
    pilot_identity_badge,
    templates,
)


def _probe_app() -> FastAPI:
    """Exercise the real badge and the real dependency over one request."""

    probe = FastAPI()

    @probe.get("/badge")
    def badge_route(request: Request):
        return {
            "badge": pilot_identity_badge(request),
            "context_roles": sorted(
                (get_trusted_tenant_context(request) or _EMPTY).roles
            ),
        }

    probe.get("/whoami")(identity_whoami_api)
    return probe


class _Empty:
    roles: tuple[str, ...] = ()


_EMPTY = _Empty()


class PilotIdentityBadgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_probe_app())
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

    def _with_identity(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_supervisor_is_named_and_labelled(self):
        self._with_identity(
            AlbIdentity(
                subject="sub-1",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            )
        )
        badge = self.client.get("/badge").json()["badge"]

        self.assertTrue(badge["verified"])
        self.assertEqual(badge["name"], "ted@911logan.com")
        self.assertEqual(badge["role"], "supervisor")
        self.assertEqual(badge["role_label"], "Supervisor")

    def test_admin_is_distinguished_from_supervisor(self):
        """Two accounts must not collapse to one role -- the whole point."""
        self._with_identity(
            AlbIdentity(
                subject="sub-2",
                groups=("lcdash-pilot-admin",),
                email="tedsparks@911logan.com",
            )
        )
        badge = self.client.get("/badge").json()["badge"]

        self.assertEqual(badge["role"], "admin")
        self.assertEqual(badge["name"], "tedsparks@911logan.com")

    def test_badge_role_matches_granted_role(self):
        """The badge may never claim a role the authorizer would not grant."""
        for groups, expected in (
            (("lcdash-pilot-user",), "user"),
            (("lcdash-pilot-supervisor",), "supervisor"),
            (("lcdash-pilot-admin",), "admin"),
            # Most privileged wins when a user is in several groups.
            (("lcdash-pilot-supervisor", "lcdash-pilot-admin"), "admin"),
        ):
            with self.subTest(groups=groups):
                with patch(
                    "app.main.resolve_alb_identity",
                    return_value=AlbIdentity(subject="s", groups=groups),
                ):
                    body = self.client.get("/badge").json()

                self.assertEqual(body["badge"]["role"], expected)
                self.assertEqual(body["context_roles"], [expected])

    def test_subject_shown_when_no_email_claim(self):
        self._with_identity(AlbIdentity(subject="sub-3", groups=("lcdash-pilot-user",)))
        badge = self.client.get("/badge").json()["badge"]

        self.assertEqual(badge["name"], "sub-3")

    def test_unverified_request_is_reported_as_unverified(self):
        """A silent fallback must read as a warning, not as a signed-in user."""
        self._with_identity(None)
        badge = self.client.get("/badge").json()["badge"]

        self.assertFalse(badge["verified"])
        self.assertEqual(badge["name"], "Not identified")
        self.assertEqual(badge["role"], "user")
        self.assertIn("No verified sign-in", badge["source"])

    def test_unknown_group_is_not_shown_as_a_role(self):
        """An unrecognized claim is a denial, never a downgrade to a label."""
        self._with_identity(AlbIdentity(subject="sub-4", groups=("some-other-group",)))
        badge = self.client.get("/badge").json()["badge"]

        self.assertFalse(badge["verified"])
        self.assertEqual(badge["role"], "user")

    def test_disabled_flag_says_so_rather_than_implying_a_sign_in(self):
        with patch.object(settings, "alb_identity_enabled", False):
            badge = self.client.get("/badge").json()["badge"]

        self.assertFalse(badge["verified"])
        self.assertIn("off", badge["source"])

    def test_whoami_endpoint_reports_the_same_badge(self):
        self._with_identity(
            AlbIdentity(
                subject="sub-5",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            )
        )
        self.assertEqual(
            self.client.get("/whoami").json(),
            self.client.get("/badge").json()["badge"],
        )

    def test_badge_is_available_to_every_template(self):
        """Registered as a Jinja global, so the shared layout can call it."""
        self.assertIs(templates.env.globals.get("pilot_identity_badge"), pilot_identity_badge)

    def test_shared_layout_renders_the_real_role(self):
        """The layout's undefined-global fallback must not be the live path.

        base.html degrades to an unverified badge when the global is missing,
        so that a broken render looks wrong instead of looking signed in. This
        proves the app's own environment takes the real branch.
        """
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), headers={})
        with patch(
            "app.main.resolve_alb_identity",
            return_value=AlbIdentity(
                subject="sub-6",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            ),
        ):
            rendered = templates.env.get_template("layouts/base.html").render(
                request=request
            )

        self.assertIn('id="topbar-identity"', rendered)
        self.assertIn('data-role="supervisor"', rendered)
        self.assertIn('data-verified="true"', rendered)
        self.assertIn("ted@911logan.com", rendered)
        self.assertNotIn("Identity is not wired into this render", rendered)


if __name__ == "__main__":
    unittest.main()
