"""Contract tests for the in-app pilot access manager.

The dangerous states these tests exist to prevent are all one click wide:
an admin demoting or disabling the last enabled admin (nobody left in-app to
manage access), an admin demoting themselves (locked out one action later),
and a resurrected delete path (accounts must be disabled and retained for
audit, per scripts/sync_cognito_users.py).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.services.pilot_access_service import PilotAccessError, PilotAccessService


class _StubClientError(Exception):
    """Shaped like botocore's ClientError -- .response["Error"]["Code"] --
    without importing botocore, proving the service duck-types the check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


_CREATED = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCognitoClient:
    """In-memory cognito-idp double recording every call it receives."""

    def __init__(self, users=None, groups=None, page_size=None) -> None:
        # users: email -> dict(status=..., enabled=...)
        self.users = {
            email: {
                "status": spec.get("status", "CONFIRMED"),
                "enabled": spec.get("enabled", True),
            }
            for email, spec in (users or {}).items()
        }
        # groups: group name -> set of emails
        self.groups = {name: set(members) for name, members in (groups or {}).items()}
        self.page_size = page_size
        self.calls: list[tuple] = []

    def _user_payload(self, email, attributes_key="Attributes"):
        spec = self.users[email]
        return {
            "Username": email,
            attributes_key: [{"Name": "email", "Value": email}],
            "UserStatus": spec["status"],
            "Enabled": spec["enabled"],
            "UserCreateDate": _CREATED,
        }

    def _paginate(self, items, token, token_key):
        start = int(token or 0)
        size = self.page_size or max(len(items), 1)
        page = items[start : start + size]
        response = {"Users": page}
        if start + size < len(items):
            response[token_key] = str(start + size)
        return response

    def list_users(self, *, UserPoolId, Limit, PaginationToken=None):
        self.calls.append(("list_users", PaginationToken))
        users = [self._user_payload(email) for email in sorted(self.users)]
        return self._paginate(users, PaginationToken, "PaginationToken")

    def list_users_in_group(self, *, UserPoolId, GroupName, Limit, NextToken=None):
        self.calls.append(("list_users_in_group", GroupName, NextToken))
        members = [
            self._user_payload(email)
            for email in sorted(self.groups.get(GroupName, ()))
            if email in self.users
        ]
        return self._paginate(members, NextToken, "NextToken")

    def admin_list_groups_for_user(self, *, UserPoolId, Username):
        self.calls.append(("admin_list_groups_for_user", Username))
        names = sorted(name for name, members in self.groups.items() if Username in members)
        return {"Groups": [{"GroupName": name} for name in names]}

    def admin_get_user(self, *, UserPoolId, Username):
        self.calls.append(("admin_get_user", Username))
        return self._user_payload(Username, attributes_key="UserAttributes")

    def admin_create_user(self, *, UserPoolId, Username, **kwargs):
        self.calls.append(("admin_create_user", Username, kwargs))
        if kwargs.get("MessageAction") == "RESEND":
            if self.users.get(Username, {}).get("status") != "FORCE_CHANGE_PASSWORD":
                raise _StubClientError("UnsupportedUserStateException")
            return {}
        if Username in self.users:
            raise _StubClientError("UsernameExistsException")
        self.users[Username] = {"status": "FORCE_CHANGE_PASSWORD", "enabled": True}
        return {}

    def admin_add_user_to_group(self, *, UserPoolId, Username, GroupName):
        self.calls.append(("admin_add_user_to_group", Username, GroupName))
        self.groups.setdefault(GroupName, set()).add(Username)

    def admin_remove_user_from_group(self, *, UserPoolId, Username, GroupName):
        self.calls.append(("admin_remove_user_from_group", Username, GroupName))
        self.groups.get(GroupName, set()).discard(Username)

    def admin_disable_user(self, *, UserPoolId, Username):
        self.calls.append(("admin_disable_user", Username))
        self.users[Username]["enabled"] = False

    def admin_enable_user(self, *, UserPoolId, Username):
        self.calls.append(("admin_enable_user", Username))
        self.users[Username]["enabled"] = True


def _service(fake) -> PilotAccessService:
    return PilotAccessService("us-east-1_Example1", client=fake)


def _cohort(page_size=None) -> _FakeCognitoClient:
    """One admin, one supervisor, one invited-but-never-signed-in user."""
    return _FakeCognitoClient(
        users={
            "admin@example.test": {"status": "CONFIRMED"},
            "super@example.test": {"status": "CONFIRMED"},
            "new@example.test": {"status": "FORCE_CHANGE_PASSWORD"},
        },
        groups={
            "lcdash-pilot-admin": {"admin@example.test"},
            "lcdash-pilot-supervisor": {"super@example.test"},
            "lcdash-pilot-user": {"new@example.test"},
        },
        page_size=page_size,
    )


class PilotAccessServiceTests(unittest.TestCase):
    def test_requires_a_user_pool_id(self):
        with self.assertRaises(ValueError):
            PilotAccessService("  ", client=_FakeCognitoClient())

    def test_list_merges_groups_and_flags_pending_invites(self):
        """The review page's whole value is showing role and invite state
        together; a listing that dropped either is just the AWS console."""
        records = _service(_cohort()).list_users()
        by_email = {record["email"]: record for record in records}

        self.assertEqual(by_email["admin@example.test"]["role"], "admin")
        self.assertEqual(by_email["super@example.test"]["role"], "supervisor")
        self.assertEqual(by_email["new@example.test"]["role"], "user")
        self.assertTrue(by_email["new@example.test"]["invited_pending"])
        self.assertFalse(by_email["admin@example.test"]["invited_pending"])
        self.assertEqual(by_email["admin@example.test"]["created_at"], _CREATED.isoformat())

    def test_list_paginates_rather_than_truncating(self):
        """Cognito caps list_users at 60 per page and the pilot pool already
        holds ~21 accounts; a non-paginating list silently hides users the
        moment the cohort grows past one page."""
        records = _service(_cohort(page_size=1)).list_users()
        self.assertEqual(len(records), 3)

    def test_groupless_user_gets_blank_role_not_an_error(self):
        fake = _FakeCognitoClient(users={"lost@example.test": {}})
        records = _service(fake).list_users()
        self.assertEqual(records[0]["role"], "")

    def test_invite_creates_then_assigns_group(self):
        fake = _cohort()
        record = _service(fake).invite_user("fresh@example.test", "supervisor")

        create = next(c for c in fake.calls if c[0] == "admin_create_user")
        self.assertNotIn("MessageAction", create[2])  # custom invite email fires
        self.assertEqual(create[2]["DesiredDeliveryMediums"], ["EMAIL"])
        self.assertIn(
            ("admin_add_user_to_group", "fresh@example.test", "lcdash-pilot-supervisor"),
            fake.calls,
        )
        self.assertTrue(record["invited_pending"])
        self.assertEqual(record["status"], "FORCE_CHANGE_PASSWORD")
        self.assertEqual(record["role"], "supervisor")

    def test_invite_rejects_junk_email_and_unknown_role(self):
        service = _service(_cohort())
        for junk in ("", "not-an-email", "a@b", "spaces in@example.test", "a" * 321 + "@x.co"):
            with self.subTest(email=junk):
                with self.assertRaises(PilotAccessError):
                    service.invite_user(junk, "user")
        with self.assertRaisesRegex(PilotAccessError, "user, supervisor, admin"):
            service.invite_user("ok@example.test", "administrator")

    def test_invite_translates_existing_account_into_plain_english(self):
        """The raw UsernameExistsException names nothing the operator can act
        on; the message must say the account exists and point at resend."""
        with self.assertRaisesRegex(PilotAccessError, "already has an account"):
            _service(_cohort()).invite_user("admin@example.test", "user")

    def test_resend_translates_completed_signup(self):
        fake = _cohort()
        service = _service(fake)

        service.resend_invite("new@example.test")  # still pending: allowed
        resend = next(c for c in fake.calls if c[0] == "admin_create_user")
        self.assertEqual(resend[2]["MessageAction"], "RESEND")

        with self.assertRaisesRegex(PilotAccessError, "already completed sign-up"):
            service.resend_invite("admin@example.test")

    def test_set_role_moves_user_between_groups(self):
        fake = _cohort()
        record = _service(fake).set_role(
            "new@example.test", "supervisor", acting_subject="admin@example.test"
        )
        self.assertIn(
            ("admin_remove_user_from_group", "new@example.test", "lcdash-pilot-user"),
            fake.calls,
        )
        self.assertIn(
            ("admin_add_user_to_group", "new@example.test", "lcdash-pilot-supervisor"),
            fake.calls,
        )
        self.assertEqual(record["role"], "supervisor")
        self.assertNotIn("new@example.test", fake.groups["lcdash-pilot-user"])

    def test_demoting_the_last_enabled_admin_is_refused(self):
        """The admin role is the only in-app path to access review. Demote the
        last enabled admin and every future access change needs the AWS
        console -- exactly the manual loop this service exists to close."""
        fake = _cohort()
        with self.assertRaisesRegex(PilotAccessError, "no admin"):
            _service(fake).set_role(
                "admin@example.test", "user", acting_subject="super@example.test"
            )
        self.assertIn("admin@example.test", fake.groups["lcdash-pilot-admin"])

        # A disabled second admin does not count as cover.
        fake.users["second@example.test"] = {"status": "CONFIRMED", "enabled": False}
        fake.groups["lcdash-pilot-admin"].add("second@example.test")
        with self.assertRaisesRegex(PilotAccessError, "no admin"):
            _service(fake).set_role(
                "admin@example.test", "user", acting_subject="super@example.test"
            )

        # An enabled second admin does.
        fake.users["second@example.test"]["enabled"] = True
        record = _service(fake).set_role(
            "admin@example.test", "user", acting_subject="second@example.test"
        )
        self.assertEqual(record["role"], "user")

    def test_disabling_the_last_enabled_admin_is_refused(self):
        """Same lockout as demotion, reached through the other door."""
        fake = _cohort()
        with self.assertRaisesRegex(PilotAccessError, "no admin"):
            _service(fake).disable_user(
                "admin@example.test", acting_subject="super@example.test"
            )
        self.assertTrue(fake.users["admin@example.test"]["enabled"])

    def test_self_disable_is_refused(self):
        with self.assertRaisesRegex(PilotAccessError, "your own account"):
            _service(_cohort()).disable_user(
                "admin@example.test", acting_subject="admin@example.test"
            )

    def test_self_demotion_from_admin_is_refused_even_with_another_admin(self):
        """Self-demotion succeeds and then locks the actor out one page load
        later. Refused even when another admin exists -- the fix is to have
        that other admin make the change, and the message says so."""
        fake = _cohort()
        fake.users["second@example.test"] = {"status": "CONFIRMED", "enabled": True}
        fake.groups["lcdash-pilot-admin"].add("second@example.test")
        with self.assertRaisesRegex(PilotAccessError, "another admin"):
            _service(fake).set_role(
                "admin@example.test", "user", acting_subject="admin@example.test"
            )

    def test_admin_keeping_admin_role_is_not_a_demotion(self):
        record = _service(_cohort()).set_role(
            "admin@example.test", "admin", acting_subject="admin@example.test"
        )
        self.assertEqual(record["role"], "admin")

    def test_enable_passes_through_without_guards(self):
        fake = _cohort()
        fake.users["new@example.test"]["enabled"] = False
        _service(fake).enable_user("new@example.test")
        self.assertTrue(fake.users["new@example.test"]["enabled"])

    def test_unknown_role_rejected_on_set_role(self):
        with self.assertRaisesRegex(PilotAccessError, "user, supervisor, admin"):
            _service(_cohort()).set_role(
                "new@example.test", "viewer", acting_subject="admin@example.test"
            )

    def test_no_delete_api_exists(self):
        """Deletion is deliberately absent: disabled accounts are the audit
        trail of who could sign in during the pilot, matching the reconciler
        in scripts/sync_cognito_users.py. A delete method appearing here is a
        policy regression, not a feature."""
        service = _service(_cohort())
        self.assertFalse(hasattr(service, "delete_user"))
        self.assertFalse(hasattr(service, "remove_user"))


if __name__ == "__main__":
    unittest.main()
