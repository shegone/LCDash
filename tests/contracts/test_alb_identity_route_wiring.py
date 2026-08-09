"""End-to-end wiring for per-user identity through the FastAPI dependency.

The verification logic is covered in test_alb_identity.py. What is proved here
is the plumbing either side of it: that FastAPI actually injects the request
into ``get_trusted_tenant_context``, that a verified identity becomes the
context's subject and role, and that every failure mode lands on the
least-privileged fallback rather than on an elevated role.

Without this, a signature error in the dependency signature would leave the
feature permanently inert -- safe, but silently doing nothing.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.core.tenancy import TenantContext
from app.main import get_trusted_tenant_context


def _probe_app() -> FastAPI:
    """A minimal app exercising the real dependency, not a copy of it."""

    probe = FastAPI()

    @probe.get("/probe")
    def probe_route(
        context: TenantContext | None = Depends(get_trusted_tenant_context),
    ):
        if context is None:
            return {"context": None}
        return {
            "tenant_id": context.tenant_id,
            "subject": context.subject,
            "identity_source": context.identity_source,
            "roles": sorted(context.roles),
        }

    return probe


class AlbIdentityRouteWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_probe_app())
        # The pilot context the cloud task actually runs with.
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

    def test_request_is_injected_and_reviewer_group_becomes_supervisor(self):
        """Proves FastAPI injects the request; otherwise this stays 'viewer'."""
        resolver = self._with_identity(
            AlbIdentity(subject="user-1", groups=("lcdash-pilot-supervisor",))
        )
        body = self.client.get("/probe").json()

        self.assertEqual(body["subject"], "user-1")
        self.assertEqual(body["roles"], ["supervisor"])
        self.assertEqual(body["identity_source"], "alb-cognito")
        # The tenant binding is still deployment configuration, not the request.
        self.assertEqual(body["tenant_id"], "logan-synthetic")
        # The dependency really did receive request headers to hand over.
        self.assertTrue(resolver.called)
        forwarded_headers = resolver.call_args.args[0]
        self.assertIn("host", {key.lower() for key in forwarded_headers.keys()})

    def test_viewer_group_stays_viewer(self):
        self._with_identity(
            AlbIdentity(subject="user-2", groups=("lcdash-pilot-user",))
        )
        body = self.client.get("/probe").json()
        self.assertEqual(body["roles"], ["user"])
        self.assertEqual(body["subject"], "user-2")

    def test_unverifiable_headers_fall_back_to_least_privilege(self):
        self._with_identity(None)
        body = self.client.get("/probe").json()
        self.assertEqual(body["roles"], ["user"])
        self.assertEqual(body["identity_source"], "deployment-configuration")
        self.assertEqual(body["subject"], "deployment-cell")

    def test_unrecognized_group_does_not_elevate(self):
        """An unknown group denies the identity; it must not become supervisor."""
        self._with_identity(
            AlbIdentity(subject="user-3", groups=("lcdash-pilot-rogue",))
        )
        body = self.client.get("/probe").json()
        self.assertEqual(body["roles"], ["user"])
        self.assertEqual(body["identity_source"], "deployment-configuration")

    def test_disabled_flag_ignores_headers_entirely(self):
        resolver = self._with_identity(
            AlbIdentity(subject="user-4", groups=("lcdash-pilot-supervisor",))
        )
        with patch.object(settings, "alb_identity_enabled", False):
            body = self.client.get("/probe").json()
        self.assertEqual(body["roles"], ["user"])
        self.assertEqual(body["identity_source"], "deployment-configuration")
        # Not merely ignored downstream -- never consulted at all.
        self.assertFalse(resolver.called)

    def test_administrator_group_resolves_to_administrator(self):
        self._with_identity(
            AlbIdentity(subject="user-5", groups=("lcdash-pilot-admin",))
        )
        body = self.client.get("/probe").json()
        self.assertEqual(body["roles"], ["admin"])


if __name__ == "__main__":
    unittest.main()
