"""The ``avatar`` tier: MAE's conversation surface, and nothing else.

Mirror of the sanitized-tier suite for the avatar plan's access class
(docs/planning/MAE_AVATAR_PLAN_2026-08-09.md). One gate is under test: the
deny-by-default path allowlist. There is no field-sanitizing half because
nothing on the avatar allowlist returns CAD structures -- and a test below
pins that property so a future allowlist edit cannot quietly change it.

``test_every_unlisted_route_is_denied_for_avatar`` walks the app's real route
table, so a route added six months from now is denied for avatar accounts the
day it ships or this suite fails.

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
from app.core import avatar_tier
from app.core.alb_identity import AlbIdentity
from app.main import app


# Paths the avatar tier exists to reach, in the form a browser requests them.
ALLOWED_PATHS = (
    "/mae/avatar",
    "/api/identity/whoami",
    "/health",
    "/logout",
    "/api/cloud-ai/status",
)

# A representative slice of everything else: the dashboard, the raw CAD and
# analytics APIs behind it, MAE's dashboard page, and admin. The full
# guarantee comes from the route-table sweep; this list exists so a failure
# names the doors that matter most.
DENIED_PATHS = (
    "/dashboard",
    "/active-calls",
    "/units",
    "/calls/CFS26-1234",
    "/map",
    "/station-alerts",
    "/analytics",
    "/mae",
    "/mae/reliability",
    "/admin/users",
    "/api/operations/snapshot",
    "/api/operations/active-calls",
    "/api/operations/units",
    "/api/operations/map",
    "/api/operations/station-alerts",
    "/api/analytics/overview",
    "/api/mae/chat",
    "/api/mae/memory",
    "/api/cloud-ai/speech/sentence",
    "/api/knowledge/status",
)

DENIAL_DETAIL = (
    "This account is for talking with MAE only. "
    "Ask an administrator if you need more access."
)

_PATH_PARAM_RE = re.compile(r"\{[^{}]+\}")


def _concrete_path(path: str) -> str:
    counter = itertools.count(1)
    return _PATH_PARAM_RE.sub(lambda _match: f"TEST-VALUE-{next(counter)}", path)


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


AVATAR = _identity("booth-demo@911logan.com", "lcdash-pilot-avatar")
USER = _identity("vendor@nga911.com", "lcdash-pilot-user")
SUPERVISOR = _identity("s@911logan.com", "lcdash-pilot-supervisor")


class _AvatarTierTestCase(unittest.TestCase):
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


class AvatarAllowlistUnitTests(unittest.TestCase):
    def test_restricts_only_the_avatar_role(self):
        self.assertTrue(avatar_tier.restricts("avatar"))
        for role in ("user", "supervisor", "admin", "", None):
            with self.subTest(role=role):
                self.assertFalse(avatar_tier.restricts(role))

    def test_normalization_matches_the_sanitized_tier(self):
        self.assertTrue(avatar_tier.is_path_allowed_for_avatar("/mae/avatar"))
        self.assertTrue(avatar_tier.is_path_allowed_for_avatar("/mae/avatar/"))
        self.assertTrue(avatar_tier.is_path_allowed_for_avatar("/mae/avatar?kiosk=1"))
        self.assertTrue(avatar_tier.is_path_allowed_for_avatar("/static/js/x.js"))
        self.assertFalse(avatar_tier.is_path_allowed_for_avatar("/dashboard"))
        # Empty normalizes to "/", which is allowed on purpose: the root
        # route is where avatar accounts get redirected to /mae/avatar.
        self.assertTrue(avatar_tier.is_path_allowed_for_avatar(""))
        self.assertFalse(avatar_tier.is_path_allowed_for_avatar("/mae"))

    def test_no_raw_data_api_is_on_the_avatar_allowlist(self):
        """The tier's whole premise: MAE talks, the browser never gets feeds.

        The avatar allowlist has no field-sanitizing half, so this pins the
        invariant that makes that acceptable. If someone allowlists an
        operations, analytics, or admin path for the avatar tier, they are
        signing up to build sanitization for it -- and this test makes that
        decision loud instead of silent.
        """
        for path in avatar_tier._AVATAR_ALLOWED_EXACT:
            with self.subTest(path=path):
                for forbidden in (
                    "/api/operations/",
                    "/api/analytics/",
                    "/api/admin/",
                    "/api/reports/",
                    "/api/nga911/",
                    "/api/mindshare/",
                ):
                    self.assertFalse(
                        path.startswith(forbidden),
                        f"{path} would hand the avatar tier a raw data feed",
                    )


class AvatarPathGateTests(_AvatarTierTestCase):
    def test_every_allowed_path_is_reachable(self):
        """A gate that denies everything is trivially safe and useless."""
        self._sign_in_as(AVATAR)
        for path in ALLOWED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertNotEqual(
                    response.status_code, 403, f"{path} was denied to the avatar tier"
                )

    def test_root_redirects_avatar_accounts_to_the_avatar_page(self):
        self._sign_in_as(AVATAR)
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/mae/avatar")

    def test_root_still_sends_dashboard_roles_to_the_dashboard(self):
        self._sign_in_as(SUPERVISOR)
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")

    def test_representative_denied_paths_are_denied(self):
        self._sign_in_as(AVATAR)
        for path in DENIED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], DENIAL_DETAIL)

    def test_every_unlisted_route_is_denied_for_avatar(self):
        """Deny-by-default, proved against the app's real route table."""
        self._sign_in_as(AVATAR)

        checked = 0
        for path in sorted({getattr(route, "path", "") for route in app.routes}):
            if not path:
                continue
            concrete = _concrete_path(path)
            if avatar_tier.is_path_allowed_for_avatar(concrete):
                continue
            with self.subTest(path=path, concrete=concrete):
                response = self.client.get(concrete, follow_redirects=False)
                self.assertIn(
                    response.status_code,
                    (403, 404),
                    f"{path} is not on the avatar allowlist but was not denied "
                    f"(got {response.status_code} for {concrete})",
                )
            checked += 1

        self.assertGreater(
            checked, 40, "route enumeration collapsed -- the sweep proves nothing"
        )

    def test_avatar_gate_does_not_narrow_other_roles(self):
        """The two tier middlewares must stay independent.

        A supervisor keeps the dashboard, and the restricted user tier is
        still refused the avatar page by its own gate -- MAE narrates live
        CAD, which is exactly what that tier exists to withhold.
        """
        self._sign_in_as(SUPERVISOR)
        response = self.client.get("/mae/avatar", follow_redirects=False)
        self.assertEqual(response.status_code, 200)

    def test_user_tier_is_still_denied_the_avatar_page(self):
        self._sign_in_as(USER)
        response = self.client.get("/mae/avatar", follow_redirects=False)
        self.assertEqual(response.status_code, 403)


class AvatarSpeechRouteTests(_AvatarTierTestCase):
    def test_speech_route_returns_the_service_payload(self):
        self._sign_in_as(AVATAR)
        payload = {
            "audio_base64": "c3ludGhldGljLW1wMw==",
            "audio_format": "mp3",
            "voice": "Ruth",
            "visemes": [{"time_ms": 0, "viseme": "p"}],
        }
        with patch("app.main.synthesize_cloud_avatar_speech", return_value=payload):
            response = self.client.post(
                "/api/mae/avatar/speech", json={"text": "Hello from MAE."}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_speech_route_fails_closed_when_the_runtime_is_unavailable(self):
        from app.integrations.cloud_ai import CloudAiRuntimeUnavailable

        self._sign_in_as(AVATAR)
        with patch(
            "app.main.synthesize_cloud_avatar_speech",
            side_effect=CloudAiRuntimeUnavailable("polly_visemes_unavailable"),
        ):
            response = self.client.post(
                "/api/mae/avatar/speech", json={"text": "Hello."}
            )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
