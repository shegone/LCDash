"""Offline tests for the selected-user cloud-pilot role contract."""

import unittest

from app.core.cloud_pilot_roles import (
    COGNITO_GROUP_ROLE_MAP,
    ROLE_PERMISSIONS,
    PilotAuthorizationDenied,
    PilotRole,
    authorize_pilot_permission,
    resolve_pilot_role,
)


class CloudPilotRoleContractTests(unittest.TestCase):
    def test_deployed_group_names_map_to_user_and_supervisor(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-user"]),
            PilotRole.USER,
        )
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-supervisor"]),
            PilotRole.SUPERVISOR,
        )

    def test_admin_requires_the_exact_group_name(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-admin"]),
            PilotRole.ADMIN,
        )
        with self.assertRaises(PilotAuthorizationDenied):
            resolve_pilot_role(["admin"])

    def test_highest_exact_role_wins_for_multiple_approved_groups(self):
        self.assertEqual(
            resolve_pilot_role(
                ["lcdash-pilot-user", "lcdash-pilot-supervisor"]
            ),
            PilotRole.SUPERVISOR,
        )

    def test_missing_malformed_and_unknown_groups_are_denied(self):
        for groups in (
            [],
            "lcdash-pilot-user",
            ["unknown"],
            ["lcdash-pilot-user", "unknown"],
            [None],
        ):
            with self.subTest(groups=groups):
                with self.assertRaises(PilotAuthorizationDenied):
                    resolve_pilot_role(groups)

    def test_permissions_are_explicit_and_role_scoped(self):
        self.assertEqual(
            authorize_pilot_permission(
                ["lcdash-pilot-user"], "pilot.readiness.view"
            ),
            PilotRole.USER,
        )
        with self.assertRaises(PilotAuthorizationDenied):
            authorize_pilot_permission(
                ["lcdash-pilot-user"], "rag.advisory.query"
            )
        self.assertEqual(
            authorize_pilot_permission(
                ["lcdash-pilot-supervisor"], "rag.advisory.query"
            ),
            PilotRole.SUPERVISOR,
        )

    def test_no_role_has_any_cad_permission(self):
        self.assertEqual(set(COGNITO_GROUP_ROLE_MAP.values()), set(PilotRole))
        for role, permissions in ROLE_PERMISSIONS.items():
            with self.subTest(role=role):
                self.assertFalse(any(item.startswith("cad.") for item in permissions))
                for permission in ("cad.read", "cad.query", "cad.send", "cad.update"):
                    with self.assertRaises(PilotAuthorizationDenied):
                        authorize_pilot_permission(
                            [
                                group
                                for group, mapped_role in COGNITO_GROUP_ROLE_MAP.items()
                                if mapped_role is role
                            ],
                            permission,
                        )

    def test_unknown_permissions_are_denied_for_admin(self):
        for permission in ("", "aws.manage", "users.create", "station-alert.release"):
            with self.subTest(permission=permission):
                with self.assertRaises(PilotAuthorizationDenied):
                    authorize_pilot_permission(
                        ["lcdash-pilot-admin"], permission
                    )

    def test_avatar_group_maps_to_the_avatar_role(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-avatar"]),
            PilotRole.AVATAR,
        )

    def test_any_dashboard_role_wins_over_avatar(self):
        """The avatar group binds loosest by design.

        The group exists for accounts that hold nothing else; someone also in
        a dashboard group keeps their dashboard role and reaches the avatar
        page through its permission instead of being narrowed to it.
        """
        for group, expected in (
            ("lcdash-pilot-user", PilotRole.USER),
            ("lcdash-pilot-supervisor", PilotRole.SUPERVISOR),
            ("lcdash-pilot-admin", PilotRole.ADMIN),
        ):
            with self.subTest(group=group):
                self.assertEqual(
                    resolve_pilot_role(["lcdash-pilot-avatar", group]),
                    expected,
                )

    def test_avatar_permission_is_conversation_only(self):
        self.assertEqual(
            authorize_pilot_permission(["lcdash-pilot-avatar"], "avatar.converse"),
            PilotRole.AVATAR,
        )
        # An avatar account gets nothing the dashboard tiers have...
        for permission in (
            "dashboard.synthetic.view",
            "map.view",
            "station.alerts.view",
            "analytics.review.view",
            "pilot.access.review",
        ):
            with self.subTest(permission=permission):
                with self.assertRaises(PilotAuthorizationDenied):
                    authorize_pilot_permission(["lcdash-pilot-avatar"], permission)
        # ...the restricted user tier does not gain the conversation (MAE
        # narrates live CAD, which is what that tier exists to withhold)...
        with self.assertRaises(PilotAuthorizationDenied):
            authorize_pilot_permission(["lcdash-pilot-user"], "avatar.converse")
        # ...and dashboard roles above it do get the conversation.
        for group in ("lcdash-pilot-supervisor", "lcdash-pilot-admin"):
            with self.subTest(group=group):
                authorize_pilot_permission([group], "avatar.converse")


if __name__ == "__main__":
    unittest.main()
