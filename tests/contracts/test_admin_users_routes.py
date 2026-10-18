"""The user-administration routes: admin-only, fail closed, honest errors.

Every route here mutates or reveals the pilot's account roster, so the gate is
the load-bearing part: only a VERIFIED admin passes, and everyone else --
supervisor, user, unverifiable -- gets the same 403 without learning whether
the capability exists. The service behind the routes is covered in
test_pilot_access_service.py; what is proved here is the plumbing and the gate.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app
from app.services.pilot_access_service import (
    PilotAccessError,
    PilotAccessNotFoundError,
    PilotAccessService,
)

_STATIC = Path(__file__).resolve().parents[2] / "static"


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
            ("POST", "/api/admin/users/reset-password", {"email": "a@b.test"}),
            (
                "POST",
                "/api/admin/users/delete",
                {"email": "a@b.test", "confirm_email": "a@b.test"},
            ),
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
                    # Supervisors and unverifiable callers are stopped by the
                    # admin gate. The restricted `user` role never reaches it:
                    # the sanitized-tier path allowlist denies admin paths
                    # first, which is a stricter layer, not a weaker one.
                    self.assertIn(
                        response.json()["detail"],
                        {
                            "Administrator sign-in is required.",
                            "This view is not available on your account. "
                            "Ask an administrator if you need access.",
                        },
                    )
        self.service.list_users.assert_not_called()
        self.service.invite_user.assert_not_called()

    def test_delete_and_reset_deny_every_non_admin_role_before_validation(self):
        """The 403 must win over body validation: a non-admin sending garbage,
        an empty body, or a valid body learns nothing about the route."""
        self._with_service()
        bodies = (
            {"json": {"email": "a@b.test", "confirm_email": "a@b.test"}},
            {"json": {}},
            {"json": {"email": 5}},
            {"content": b"this is not json", "headers": {"Content-Type": "application/json"}},
            {"content": b""},
        )
        identities = [
            _identity("x@911logan.com", f"lcdash-pilot-{group}")
            for group in ("user", "supervisor", "dispatcher", "fire-officer", "avatar")
        ] + [None]
        for identity in identities:
            self._sign_in_as(identity)
            for path in ("/api/admin/users/reset-password", "/api/admin/users/delete"):
                for body in bodies:
                    with self.subTest(identity=identity and identity.groups, path=path, body=body):
                        response = self.client.post(path, **body)
                        self.assertEqual(response.status_code, 403)
        self.service.reset_password.assert_not_called()
        self.service.delete_user.assert_not_called()

    def test_admin_with_a_garbage_body_gets_a_readable_validation_error(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        for path, body in (
            ("/api/admin/users/reset-password", {"json": {}}),
            ("/api/admin/users/delete", {"json": {"email": "a@b.test"}}),
            ("/api/admin/users/delete", {"content": b"not json"}),
        ):
            with self.subTest(path=path, body=body):
                response = self.client.post(path, **body)
                self.assertEqual(response.status_code, 422)
                self.assertIsInstance(response.json()["detail"], str)
        self.service.reset_password.assert_not_called()
        self.service.delete_user.assert_not_called()

    def test_reset_password_route_carries_the_acting_admin_and_returns_method(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        self.service.reset_password.return_value = {
            "email": "x@911logan.com", "method": "reset_code_sent",
        }
        response = self.client.post(
            "/api/admin/users/reset-password", json={"email": "x@911logan.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"email": "x@911logan.com", "method": "reset_code_sent"}
        )
        self.service.reset_password.assert_called_once_with(
            "x@911logan.com", acting_subject="tedsparks@911logan.com"
        )

    def test_delete_route_carries_the_acting_admin_and_confirmation(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        response = self.client.post(
            "/api/admin/users/delete",
            json={"email": "x@911logan.com", "confirm_email": "x@911logan.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.service.delete_user.assert_called_once_with(
            "x@911logan.com", "x@911logan.com", acting_subject="tedsparks@911logan.com"
        )

    def test_delete_and_reset_refusals_map_to_400_and_missing_users_to_404(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        for method, path, body in (
            ("delete_user", "/api/admin/users/delete",
             {"email": "x@911logan.com", "confirm_email": "x@911logan.com"}),
            ("reset_password", "/api/admin/users/reset-password", {"email": "x@911logan.com"}),
        ):
            with self.subTest(path=path):
                getattr(self.service, method).side_effect = PilotAccessError(
                    "This change would leave the pilot with no admin."
                )
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("no admin", response.json()["detail"])

                getattr(self.service, method).side_effect = PilotAccessNotFoundError(
                    "No account exists for x@911logan.com."
                )
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 404)
                self.assertIn("No account exists", response.json()["detail"])

    def test_real_service_behind_the_routes_never_handles_a_password(self):
        """End to end through the real service with a recording Cognito
        double: nothing sent to Cognito, returned, or logged mentions a
        password value, and the guardrails hold through the HTTP layer."""
        cognito = MagicMock()
        cognito.admin_get_user.return_value = {
            "Username": "x@911logan.com",
            "UserStatus": "CONFIRMED",
            "Enabled": True,
            "UserAttributes": [{"Name": "email", "Value": "x@911logan.com"}],
        }
        cognito.admin_list_groups_for_user.return_value = {
            "Groups": [{"GroupName": "lcdash-pilot-supervisor"}]
        }
        service = PilotAccessService("us-east-1_Example1", client=cognito)
        patcher = patch("app.main._pilot_access_service", return_value=service)
        self.addCleanup(patcher.stop)
        patcher.start()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))

        with self.assertLogs("app.services.pilot_access_service", level="INFO") as logs:
            reset = self.client.post(
                "/api/admin/users/reset-password", json={"email": "x@911logan.com"}
            )
            deleted = self.client.post(
                "/api/admin/users/delete",
                json={"email": "x@911logan.com", "confirm_email": "X@911logan.com"},
            )
        self.assertEqual(reset.json(), {"email": "x@911logan.com", "method": "reset_code_sent"})
        self.assertEqual(deleted.json(), {"ok": True})
        cognito.admin_reset_user_password.assert_called_once_with(
            UserPoolId="us-east-1_Example1", Username="x@911logan.com"
        )
        cognito.admin_delete_user.assert_called_once_with(
            UserPoolId="us-east-1_Example1", Username="x@911logan.com"
        )
        cognito.admin_set_user_password.assert_not_called()
        self.assertEqual(len(logs.output), 2)
        for line in logs.output:
            self.assertIn("acting_subject=tedsparks@911logan.com", line)
            self.assertNotIn("password=", line.lower())

        # Own account is refused through the same stack.
        own = self.client.post(
            "/api/admin/users/delete",
            json={"email": "tedsparks@911logan.com", "confirm_email": "tedsparks@911logan.com"},
        )
        self.assertEqual(own.status_code, 400)
        self.assertIn("own account", own.json()["detail"])

    def test_page_exposes_delete_and_reset_hooks_and_the_signed_in_email(self):
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))
        page = self.client.get("/admin/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-current-user-email="tedsparks@911logan.com"', page.text)
        self.assertIn("20261017-admin-delete-reset", page.text)
        self.assertNotIn("Disable, never delete", page.text)

        script = (_STATIC / "js" / "lcdash-admin-users.js").read_text(encoding="utf-8")
        for hook in (
            "/api/admin/users/reset-password",
            "/api/admin/users/delete",
            "Reset password",
            "Delete permanently",
            "Type the user's email to confirm",
            "This permanently removes the account and cannot be undone.",
            "You will not see the password.",
            "currentUserEmail",
            "confirm_email",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, script)
        # Server-supplied text is only ever written with textContent. Comments
        # may mention the banned names; only executable lines are checked.
        script = "\n".join(
            line for line in script.splitlines() if not line.lstrip().startswith("//")
        )
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                       "window.confirm", "alert("):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, script)

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

    def test_avatar_role_is_assignable_and_offered_in_the_invite_form(self):
        """The avatar-only tier must be reachable from User Admin.

        The service already validates roles against COGNITO_GROUP_ROLE_MAP,
        so the backend accepted "avatar" from the day the role map grew --
        but the invite dropdown and row selects hardcode their choices, and
        a role the UI never offers is a tier no admin can actually grant.
        Shipped missing on the first avatar release; Ted caught it, not the
        suite. Both halves are pinned here.
        """
        self._with_service()
        self._sign_in_as(_identity("tedsparks@911logan.com", "lcdash-pilot-admin"))

        self.service.invite_user.return_value = {"email": "booth@911logan.com"}
        response = self.client.post(
            "/api/admin/users", json={"email": "booth@911logan.com", "role": "avatar"}
        )
        self.assertEqual(response.status_code, 200)
        self.service.invite_user.assert_called_once_with("booth@911logan.com", "avatar")

        self.service.set_role.return_value = {"email": "booth@911logan.com"}
        self.client.post(
            "/api/admin/users/role", json={"email": "booth@911logan.com", "role": "avatar"}
        )
        self.service.set_role.assert_called_once_with(
            "booth@911logan.com", "avatar", acting_subject="tedsparks@911logan.com"
        )

        page = self.client.get("/admin/users")
        self.assertIn('value="avatar"', page.text)

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
