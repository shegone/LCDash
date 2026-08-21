"""The ``dispatcher`` tier: a supervisor with three doors closed.

Mirror of the avatar-tier suite for the dispatcher role (scoped by Ted,
2026-08-20): everything a supervisor reaches except MAE's avatar page,
Mindshare/JACK, and the Tools & Quality section. Unlike the two allowlist
tiers this gate is a DENYLIST -- dispatcher trust matches supervisor, so a
new operational route should be reachable the day it ships -- and the sweep
below is therefore inverted: every route the denylist names must actually
deny, and a representative slice of everything else must stay open.

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
from app.core import dispatcher_tier
from app.core.alb_identity import AlbIdentity
from app.main import app


# The supervisor surface a dispatcher keeps, in the form a browser requests it.
ALLOWED_PATHS = (
    "/dashboard",
    "/active-calls",
    "/units",
    "/station-alerts",
    "/map",
    "/map/heatmap",
    "/analytics",
    "/reports",
    "/nga911-intelligence",
    "/mae",
    "/knowledge",
    "/logout",
    "/health",
    "/api/identity/whoami",
    "/api/operations/snapshot",
    "/api/cloud-ai/status",
    "/api/mae/status",
    "/api/voice/speech",
    "/api/voice/transcribe",
)

# The three closed areas, page and API alike. The full guarantee comes from
# the route-table sweep; this list exists so a failure names the doors that
# matter most.
DENIED_PATHS = (
    "/mae/avatar",
    "/api/mae/avatar/speech",
    "/mindshare",
    "/mindshare/technical",
    "/mindshare/jack-hines",
    "/mindshare/library",
    "/mindshare/reliability",
    "/mindshare/coverage",
    "/mindshare/radio",
    "/api/mindshare/status",
    "/api/mindshare/chat",
    "/api/mindshare/memory",
    "/integrations/health",
    "/api/integrations/centralsquare/health",
    "/voice",
    "/api/voice/status",
    "/mae/reliability",
    "/api/mae/evaluations",
    "/api/mae/evaluations/run",
    "/api/mae/feedback/review",
    "/api/mae/memory/review",
    "/admin/users",
    "/admin/knowledge",
)

DENIAL_DETAIL = (
    "This view is not part of the dispatcher role. "
    "Ask an administrator if you need access."
)

_PATH_PARAM_RE = re.compile(r"\{[^{}]+\}")


def _concrete_path(path: str) -> str:
    counter = itertools.count(1)
    return _PATH_PARAM_RE.sub(lambda _match: f"TEST-VALUE-{next(counter)}", path)


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


DISPATCHER = _identity("dispatch@911logan.com", "lcdash-pilot-dispatcher")
SUPERVISOR = _identity("s@911logan.com", "lcdash-pilot-supervisor")
USER = _identity("vendor@nga911.com", "lcdash-pilot-user")


class _DispatcherTierTestCase(unittest.TestCase):
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


class DispatcherDenylistUnitTests(unittest.TestCase):
    def test_restricts_only_the_dispatcher_role(self):
        self.assertTrue(dispatcher_tier.restricts("dispatcher"))
        for role in ("user", "supervisor", "admin", "avatar", "", None):
            with self.subTest(role=role):
                self.assertFalse(dispatcher_tier.restricts(role))

    def test_normalization_matches_the_other_tiers(self):
        blocked = dispatcher_tier.is_path_blocked_for_dispatcher
        self.assertTrue(blocked("/mae/avatar"))
        self.assertTrue(blocked("/mae/avatar/"))
        self.assertTrue(blocked("/mae/avatar?kiosk=1"))
        self.assertTrue(blocked("/mindshare/jack-hines"))
        self.assertFalse(blocked("/mae"))
        self.assertFalse(blocked("/dashboard"))
        # Call detail is a supervisor surface dispatchers keep. (Its offline
        # HTTP behavior depends on CAD credentials, so the guarantee is
        # asserted here at the gate rather than through TestClient.)
        self.assertFalse(blocked("/calls/CFS26-1234"))
        self.assertFalse(blocked(""))

    def test_shared_mae_endpoints_stay_open(self):
        """The MAE assistant page must keep working for dispatchers.

        These endpoints serve the allowed /mae page (and its voice loop)
        just as much as any blocked page, so the denylist must not name
        them. If one moves behind a blocked area, remove it here first.
        """
        blocked = dispatcher_tier.is_path_blocked_for_dispatcher
        for path in (
            "/api/cloud-ai/advisory",
            "/api/cloud-ai/advisory/stream",
            "/api/cloud-ai/speech/sentence",
            "/api/voice/speech",
            "/api/voice/transcribe",
            "/api/mae/chat",
            "/api/mae/chat/stream",
            "/api/mae/feedback",
            "/api/mae/memory",
        ):
            with self.subTest(path=path):
                self.assertFalse(blocked(path), f"{path} would break the /mae page")


class DispatcherPathGateTests(_DispatcherTierTestCase):
    def test_every_allowed_path_is_reachable(self):
        """A gate that denies everything is trivially safe and useless."""
        self._sign_in_as(DISPATCHER)
        for path in ALLOWED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertNotEqual(
                    response.status_code,
                    403,
                    f"{path} was denied to the dispatcher tier",
                )

    def test_root_sends_dispatchers_to_the_dashboard(self):
        self._sign_in_as(DISPATCHER)
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")

    def test_every_denied_path_is_denied(self):
        self._sign_in_as(DISPATCHER)
        for path in DENIED_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], DENIAL_DETAIL)

    def test_every_route_on_the_denylist_actually_denies(self):
        """Walk the app's real route table: anything the denylist matches
        must come back 403 for a dispatcher, so a blocked-area route added
        later is denied the day it ships or this sweep fails."""
        self._sign_in_as(DISPATCHER)

        checked = 0
        for path in sorted({getattr(route, "path", "") for route in app.routes}):
            if not path:
                continue
            concrete = _concrete_path(path)
            if not dispatcher_tier.is_path_blocked_for_dispatcher(concrete):
                continue
            with self.subTest(path=path, concrete=concrete):
                response = self.client.get(concrete, follow_redirects=False)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"{path} matches the dispatcher denylist but was not denied "
                    f"(got {response.status_code} for {concrete})",
                )
            checked += 1

        self.assertGreater(
            checked, 15, "denylist sweep collapsed -- it proves nothing"
        )

    def test_dispatcher_gate_does_not_narrow_other_roles(self):
        """A supervisor keeps every door this tier closes."""
        self._sign_in_as(SUPERVISOR)
        for path in ("/mae/avatar", "/mindshare", "/voice", "/integrations/health",
                     "/mae/reliability"):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200)

    def test_user_tier_is_still_denied_everything_it_was(self):
        """The user tier's own allowlist keeps ruling user accounts."""
        self._sign_in_as(USER)
        for path in ("/active-calls", "/mindshare", "/mae/avatar"):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
