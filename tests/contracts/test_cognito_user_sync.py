"""Contracts for reconciling Cognito users against the approved-users file.

The planning half is pure, so everything here runs without boto3 or
credentials. The apply half is exercised against a recording fake client, which
is enough to pin the exact API calls made -- the point being that a script with
admin rights over the pool should never surprise anyone about what it does.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.cloud_pilot_roles import COGNITO_GROUP_ROLE_MAP, PilotRole
from app.tools.cognito_user_sync import (
    MANAGED_GROUPS,
    ROLE_TO_GROUP,
    ApprovedUser,
    ApprovedUsersError,
    CognitoUserState,
    apply_plan,
    load_approved_users,
    parse_approved_users,
    plan_user_sync,
    read_suppressed_addresses,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _kinds(plan) -> list[tuple[str, str, str | None]]:
    return [(action.kind, action.email, action.group) for action in plan.actions]


class ApprovedUsersFileTests(unittest.TestCase):
    def test_the_checked_in_file_is_valid(self):
        users = load_approved_users(REPO_ROOT / "config" / "approved_users.json")
        self.assertTrue(users)
        self.assertTrue(all(user.group in MANAGED_GROUPS for user in users))

    def test_every_role_has_exactly_one_group(self):
        self.assertEqual(set(ROLE_TO_GROUP), set(PilotRole))
        self.assertEqual(set(ROLE_TO_GROUP.values()), set(COGNITO_GROUP_ROLE_MAP))

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ApprovedUsersError):
            parse_approved_users(
                {"users": [{"email": "a@b.com", "name": "A", "role": "superuser"}]}
            )

    def test_missing_name_is_rejected(self):
        """Governance requires every account to be named."""
        with self.assertRaises(ApprovedUsersError):
            parse_approved_users(
                {"users": [{"email": "a@b.com", "name": "", "role": "user"}]}
            )

    def test_malformed_email_is_rejected(self):
        for bad in ("not-an-email", "a@b", "a b@c.com", "a@b.com,c@d.com", ""):
            with self.subTest(email=bad):
                with self.assertRaises(ApprovedUsersError):
                    parse_approved_users(
                        {"users": [{"email": bad, "name": "A", "role": "user"}]}
                    )

    def test_duplicate_email_is_rejected(self):
        with self.assertRaises(ApprovedUsersError):
            parse_approved_users(
                {
                    "users": [
                        {"email": "a@b.com", "name": "A", "role": "user"},
                        {"email": "A@B.com", "name": "A again", "role": "supervisor"},
                    ]
                }
            )

    def test_empty_user_list_is_rejected(self):
        with self.assertRaises(ApprovedUsersError):
            parse_approved_users({"users": []})

    def test_missing_file_is_reported_clearly(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ApprovedUsersError):
                load_approved_users(Path(tmp) / "absent.json")

    def test_invalid_json_is_reported_clearly(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ApprovedUsersError):
                load_approved_users(path)

    def test_email_is_normalized_to_lowercase(self):
        users = parse_approved_users(
            {"users": [{"email": "Ted@911Logan.com", "name": "T", "role": "user"}]}
        )
        self.assertEqual(users[0].email, "ted@911logan.com")


class PlanTests(unittest.TestCase):
    def _user(self, email="new@911logan.com"):
        return ApprovedUser(email=email, name="New Person", role=PilotRole.USER)

    def test_absent_user_is_created_and_added_to_their_group(self):
        plan = plan_user_sync([self._user()], [])
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(
            _kinds(plan),
            [
                ("create_user", "new@911logan.com", "lcdash-pilot-user"),
                ("add_to_group", "new@911logan.com", "lcdash-pilot-user"),
            ],
        )

    def test_matching_state_produces_no_actions(self):
        plan = plan_user_sync(
            [self._user()],
            [
                CognitoUserState(
                    email="new@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                )
            ],
        )
        self.assertTrue(plan.is_empty)
        self.assertTrue(plan.safe_to_apply)

    def test_role_change_swaps_exactly_one_group(self):
        promoted = ApprovedUser(
            email="tester@911logan.com", name="Tester", role=PilotRole.SUPERVISOR
        )
        plan = plan_user_sync(
            [promoted],
            [
                CognitoUserState(
                    email="tester@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                )
            ],
        )
        self.assertEqual(
            _kinds(plan),
            [
                ("remove_from_group", "tester@911logan.com", "lcdash-pilot-user"),
                ("add_to_group", "tester@911logan.com", "lcdash-pilot-supervisor"),
            ],
        )

    def test_multiple_managed_groups_are_reduced_to_the_approved_one(self):
        """The single-group rule is repaired, not merely reported."""
        plan = plan_user_sync(
            [self._user("drift@911logan.com")],
            [
                CognitoUserState(
                    email="drift@911logan.com",
                    enabled=True,
                    groups=frozenset(
                        {
                            "lcdash-pilot-user",
                            "lcdash-pilot-supervisor",
                            "lcdash-pilot-admin",
                        }
                    ),
                )
            ],
        )
        removals = {
            action.group for action in plan.actions if action.kind == "remove_from_group"
        }
        self.assertEqual(
            removals, {"lcdash-pilot-supervisor", "lcdash-pilot-admin"}
        )
        self.assertNotIn(
            "add_to_group", [action.kind for action in plan.actions]
        )

    def test_disabled_but_approved_user_is_re_enabled(self):
        plan = plan_user_sync(
            [self._user("back@911logan.com")],
            [
                CognitoUserState(
                    email="back@911logan.com",
                    enabled=False,
                    groups=frozenset({"lcdash-pilot-user"}),
                )
            ],
        )
        self.assertEqual(
            _kinds(plan), [("enable_user", "back@911logan.com", None)]
        )

    def test_removed_user_is_disabled_never_deleted(self):
        plan = plan_user_sync(
            [self._user("keep@911logan.com")],
            [
                CognitoUserState(
                    email="keep@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
                CognitoUserState(
                    email="gone@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-supervisor"}),
                ),
            ],
        )
        self.assertEqual(
            _kinds(plan), [("disable_user", "gone@911logan.com", None)]
        )
        self.assertNotIn(
            "delete", " ".join(action.kind for action in plan.actions)
        )

    def test_already_disabled_removed_user_needs_no_action(self):
        plan = plan_user_sync(
            [self._user("keep@911logan.com")],
            [
                CognitoUserState(
                    email="keep@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
                CognitoUserState(
                    email="gone@911logan.com", enabled=False, groups=frozenset()
                ),
            ],
        )
        self.assertTrue(plan.is_empty)

    def test_unrecognized_group_is_flagged_not_silently_removed(self):
        """An unknown group makes resolve_pilot_role deny; that needs a human."""
        plan = plan_user_sync(
            [self._user("odd@911logan.com")],
            [
                CognitoUserState(
                    email="odd@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user", "some-other-group"}),
                )
            ],
        )
        self.assertTrue(plan.is_empty)
        self.assertTrue(any("some-other-group" in w for w in plan.warnings))
        self.assertTrue(any("deny" in w for w in plan.warnings))

    def test_pool_state_email_casing_is_matched_case_insensitively(self):
        plan = plan_user_sync(
            [self._user("mixed@911logan.com")],
            [
                CognitoUserState(
                    email="Mixed@911Logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                )
            ],
        )
        self.assertTrue(plan.is_empty)


class SuppressionCheckTests(unittest.TestCase):
    """A suppressed address means that user cannot sign in at all.

    Every sign-in code goes through SES, so suppression is a silent lockout: the
    account looks healthy in Cognito and the only symptom is the person saying no
    code arrived. These tests pin that it surfaces, and that it surfaces as a
    warning rather than blocking provisioning.
    """

    def _user(self, email="a@911logan.com"):
        return ApprovedUser(email=email, name="A", role=PilotRole.USER)

    def test_suppressed_approved_user_is_reported_with_the_reason(self):
        plan = plan_user_sync(
            [self._user()], [], suppressed={"a@911logan.com": "BOUNCE"}
        )
        self.assertTrue(any("suppression list" in w for w in plan.warnings))
        self.assertTrue(any("BOUNCE" in w for w in plan.warnings))
        self.assertTrue(any("cannot sign in" in w for w in plan.warnings))

    def test_suppression_warns_but_does_not_block_provisioning(self):
        """The account should still be created and grouped correctly."""
        plan = plan_user_sync(
            [self._user()], [], suppressed={"a@911logan.com": "COMPLAINT"}
        )
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(
            _kinds(plan),
            [
                ("create_user", "a@911logan.com", "lcdash-pilot-user"),
                ("add_to_group", "a@911logan.com", "lcdash-pilot-user"),
            ],
        )

    def test_suppressed_address_not_in_the_approved_list_is_ignored(self):
        """Someone else's suppressed address is not this tool's business."""
        plan = plan_user_sync(
            [self._user()], [], suppressed={"stranger@example.com": "BOUNCE"}
        )
        self.assertEqual(plan.warnings, ())

    def test_suppression_matching_is_case_insensitive(self):
        plan = plan_user_sync(
            [self._user("mixed@911logan.com")],
            [],
            suppressed={"Mixed@911Logan.com": "BOUNCE"},
        )
        self.assertTrue(any("mixed@911logan.com" in w for w in plan.warnings))

    def test_no_suppression_data_produces_no_warnings(self):
        self.assertEqual(plan_user_sync([self._user()], []).warnings, ())


class SuppressionReaderTests(unittest.TestCase):
    def test_not_found_means_healthy_and_is_not_reported(self):
        class _NotFoundException(Exception):
            pass

        class _Client:
            def get_suppressed_destination(self, **kwargs):
                raise _NotFoundException("address not found")

        self.assertEqual(
            read_suppressed_addresses(_Client(), ["a@911logan.com"]), {}
        )

    def test_suppressed_address_returns_its_reason(self):
        class _Client:
            def get_suppressed_destination(self, **kwargs):
                return {
                    "SuppressedDestination": {
                        "EmailAddress": kwargs["EmailAddress"],
                        "Reason": "BOUNCE",
                    }
                }

        self.assertEqual(
            read_suppressed_addresses(_Client(), ["A@911Logan.com"]),
            {"a@911logan.com": "BOUNCE"},
        )

    def test_an_unexpected_api_error_never_breaks_the_reconciler(self):
        """Diagnostics must not stop the tool from doing its actual job."""

        class _Client:
            def get_suppressed_destination(self, **kwargs):
                raise RuntimeError("AccessDeniedException")

        with self.assertLogs("app.tools.cognito_user_sync", level="WARNING"):
            self.assertEqual(
                read_suppressed_addresses(_Client(), ["a@911logan.com"]), {}
            )


class LockoutGuardTests(unittest.TestCase):
    def test_plan_that_disables_every_enabled_user_is_refused(self):
        """The signature of a truncated approved-users file."""
        plan = plan_user_sync(
            [ApprovedUser(email="fresh@911logan.com", name="F", role=PilotRole.USER)],
            [
                CognitoUserState(
                    email="a@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
                CognitoUserState(
                    email="b@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-supervisor"}),
                ),
            ],
        )
        self.assertFalse(plan.safe_to_apply)
        self.assertTrue(any("2 of 2" in r for r in plan.refusals))
        self.assertTrue(any("most of them" in r for r in plan.refusals))

    def test_lockout_can_be_overridden_deliberately(self):
        plan = plan_user_sync(
            [ApprovedUser(email="fresh@911logan.com", name="F", role=PilotRole.USER)],
            [
                CognitoUserState(
                    email="a@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                )
            ],
            allow_disabling_everyone=True,
        )
        self.assertTrue(plan.safe_to_apply)

    def test_disabling_most_but_not_all_users_is_refused(self):
        """Regression: the first version of this guard only fired at 100%.

        Run against the real pool -- 20 users, one name in the approved file --
        it planned 19 disables without complaint, because the single surviving
        account meant "not everyone". A guard that only trips at 100% does not
        protect against 95%, which is the case that actually happened.
        """
        approved = [
            ApprovedUser(email="keep@911logan.com", name="K", role=PilotRole.ADMIN)
        ]
        current = [
            CognitoUserState(
                email="keep@911logan.com",
                enabled=True,
                groups=frozenset({"lcdash-pilot-admin"}),
            )
        ] + [
            CognitoUserState(
                email=f"other{index}@911logan.com",
                enabled=True,
                groups=frozenset(),
            )
            for index in range(19)
        ]
        plan = plan_user_sync(approved, current)
        self.assertFalse(plan.safe_to_apply)
        self.assertTrue(any("19 of 20" in r for r in plan.refusals))

    def test_disabling_a_minority_of_users_is_allowed(self):
        """Ordinary offboarding must not need an override flag."""
        approved = [
            ApprovedUser(
                email=f"keep{index}@911logan.com", name="K", role=PilotRole.USER
            )
            for index in range(8)
        ]
        current = [
            CognitoUserState(
                email=f"keep{index}@911logan.com",
                enabled=True,
                groups=frozenset({"lcdash-pilot-user"}),
            )
            for index in range(8)
        ] + [
            CognitoUserState(
                email="gone@911logan.com", enabled=True, groups=frozenset()
            )
        ]
        plan = plan_user_sync(approved, current)
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(_kinds(plan), [("disable_user", "gone@911logan.com", None)])

    def test_disabling_some_but_not_all_users_is_allowed(self):
        plan = plan_user_sync(
            [ApprovedUser(email="a@911logan.com", name="A", role=PilotRole.USER)],
            [
                CognitoUserState(
                    email="a@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
                CognitoUserState(
                    email="b@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
            ],
        )
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(_kinds(plan), [("disable_user", "b@911logan.com", None)])

    def test_empty_approved_list_refuses_before_planning(self):
        plan = plan_user_sync([], [])
        self.assertFalse(plan.safe_to_apply)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            return {}

        return record


class ApplyTests(unittest.TestCase):
    def test_apply_makes_exactly_the_expected_api_calls(self):
        user = ApprovedUser(
            email="new@911logan.com", name="New Person", role=PilotRole.SUPERVISOR
        )
        plan = plan_user_sync([user], [])
        client = _RecordingClient()

        performed = apply_plan(client, "us-east-1_Example1", plan, [user])

        self.assertEqual(len(performed), 2)
        self.assertEqual(
            [name for name, _ in client.calls],
            ["admin_create_user", "admin_add_user_to_group"],
        )

        _, create = client.calls[0]
        self.assertEqual(create["Username"], "new@911logan.com")
        attributes = {item["Name"]: item["Value"] for item in create["UserAttributes"]}
        self.assertEqual(attributes["email"], "new@911logan.com")
        self.assertEqual(attributes["email_verified"], "true")
        self.assertEqual(attributes["name"], "New Person")
        # No password is ever set or transmitted by this script.
        self.assertNotIn("TemporaryPassword", create)

        _, grouping = client.calls[1]
        self.assertEqual(grouping["GroupName"], "lcdash-pilot-supervisor")

    def test_apply_refuses_a_plan_carrying_a_refusal(self):
        plan = plan_user_sync([], [])
        client = _RecordingClient()
        with self.assertRaises(ApprovedUsersError):
            apply_plan(client, "us-east-1_Example1", plan, [])
        self.assertEqual(client.calls, [])

    def test_apply_never_calls_a_delete_operation(self):
        user = ApprovedUser(email="a@911logan.com", name="A", role=PilotRole.USER)
        plan = plan_user_sync(
            [user],
            [
                CognitoUserState(
                    email="a@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
                CognitoUserState(
                    email="b@911logan.com",
                    enabled=True,
                    groups=frozenset({"lcdash-pilot-user"}),
                ),
            ],
        )
        client = _RecordingClient()
        apply_plan(client, "us-east-1_Example1", plan, [user])
        called = [name for name, _ in client.calls]
        self.assertEqual(called, ["admin_disable_user"])
        self.assertFalse(any("delete" in name for name in called))


class ScriptSafetyTests(unittest.TestCase):
    def test_script_is_dry_run_unless_apply_is_passed(self):
        source = (REPO_ROOT / "scripts" / "sync_cognito_users.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--apply"', source)
        self.assertIn("if not args.apply:", source)
        self.assertIn("Dry run", source)

    def test_script_never_sets_or_prints_a_password(self):
        source = (REPO_ROOT / "scripts" / "sync_cognito_users.py").read_text(
            encoding="utf-8"
        )
        module = (REPO_ROOT / "app" / "tools" / "cognito_user_sync.py").read_text(
            encoding="utf-8"
        )
        for text in (source, module):
            self.assertNotIn("TemporaryPassword", text)
            self.assertNotIn("admin_set_user_password", text)


if __name__ == "__main__":
    unittest.main()
