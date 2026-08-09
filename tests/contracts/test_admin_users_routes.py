"""The user-administration routes: admin-only, fail closed, honest errors.

Every route here mutates or reveals the pilot's account roster, so the gate is
the load-bearing part: only a VERIFIED admin passes, and everyone else --
supervisor, user, unverifiable -- gets the same 403 without learning whether
the capability exists. The service behind the routes is covered in
test_pilot_access_service.py; what is proved here is the plumbing and the gate.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app
from app.services.pilot_access_service import PilotAccessError


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


class AdminUsersRouteTests(unittest.TestCase):
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
        self.service = MagicMock()

    def _sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _with_service(self):
        patcher = patch("app.main._pilot_access_service", return_value=self.service)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_every_route_denies_non_admins_uniformly(self):
        """Supervisor, user, and unverifiable all get the same 403."""
        self._with_service()
        routes = (
            ("GET", "/admin/users", None),
            ("GET", "/api/admin/users", None),
            ("POST", "/api/admin/users", {"email": "a@b.test", "role": "user"}),
            ("POST", "/api/admin/users/role", {"email": "a@b.test", "role": "user"}),
            ("POST", "/api/admin/users/disable", {"email": "a@b.test"}),
            ("POST", "/api/admin/users/enable", {"email": "a@b.test"}),
            ("POST", "/api/admin/users/resend-invite", {"email": "a@b.test"}),
        )
        for identity in (
            _identity("s@911logan.com", "lcdash-pilot-supervisor"),
            _identity("u@911logan.com", "lcdash-pilot-user"),
            None,
        ):
            self._sign_in_as(identity)
            for method, path, body in routes:
                with self.subTest(identity=identity and identity.email, path=path):
                    response = self.client.request(method, path, json=body)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(
                        response.json()["detail"], "Administrator sign-in is required."
                    )
        self.service.list_users.assert_not_called()
        self.service.invite_user.assert_not_called()

    def test_admin_lists_users(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.list_users.return_value = [{"email": "a@b.test"}]
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"users": [{"email": "a@b.test"}]})

    def test_invite_returns_record(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.invite_user.return_value = {"email": "new@911logan.com"}
        response = self.client.post(
            "/api/admin/users", json={"email": "new@911logan.com", "role": "supervisor"}
        )
        self.assertEqual(response.status_code, 200)
        self.service.invite_user.assert_called_once_with("new@911logan.com", "supervisor")

    def test_role_change_and_disable_carry_the_acting_admin(self):
        """Self-protection rules live in the service; they only work if the
        routes pass WHO is acting, from the verified identity."""
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.set_role.return_value = {"email": "x@911logan.com"}
        self.client.post(
            "/api/admin/users/role", json={"email": "x@911logan.com", "role": "user"}
        )
        self.service.set_role.assert_called_once_with(
            "x@911logan.com", "user", acting_subject="tedsparks@911logan.com"
        )
        self.client.post("/api/admin/users/disable", json={"email": "x@911logan.com"})
        self.service.disable_user.assert_called_once_with(
            "x@911logan.com", acting_subject="tedsparks@911logan.com"
        )

    def test_service_refusals_surface_as_400_with_the_reason(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.disable_user.side_effect = PilotAccessError(
            "Disabling this account would leave the pilot with no admin."
        )
        response = self.client.post(
            "/api/admin/users/disable", json={"email": "tedsparks@911logan.com"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no admin", response.json()["detail"])

    def test_admin_page_renders_and_nav_link_is_admin_only(self):
        """The sidebar advertises User Admin to admins alone. The server
        403s everyone else regardless -- the nav gate is about not
        advertising a door that will not open."""
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        page = self.client.get("/admin/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn("admin-invite-form", page.text)
        self.assertIn("User Admin", page.text)

        self._sign_in_as(_identity("s@911logan.com", "lcdash-pilot-supervisor"))
        reports = self.client.get("/reports")
        self.assertEqual(reports.status_code, 200)
        self.assertNotIn("User Admin", reports.text)

    def test_unconfigured_pool_is_503_not_a_crash(self):
        """No pool id (identity off, or a misdeployed env) must read as
        'not configured', never as a stack trace or an empty user list."""
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        with patch.object(settings, "alb_identity_user_pool_id", ""):
            response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"])
        # (In production a blank pool id would already fail closed at the
        # verifier -- resolve_alb_identity refuses an unconfigured deployment
        # -- so this 503 is the defense-in-depth layer behind that gate. The
        # mocked verifier here deliberately skips the first gate to reach it.)


if __name__ == "__main__":
    unittest.main()
