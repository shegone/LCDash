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
    def test_deployed_group_names_map_to_viewer_and_supervisor(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-viewer"]),
            PilotRole.VIEWER,
        )
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-reviewer"]),
            PilotRole.SUPERVISOR,
        )

    def test_administrator_requires_exact_reserved_group(self):
        self.assertEqual(
            resolve_pilot_role(["lcdash-pilot-administrator"]),
            PilotRole.ADMINISTRATOR,
        )
        with self.assertRaises(PilotAuthorizationDenied):
            resolve_pilot_role(["administrator"])

    def test_highest_exact_role_wins_for_multiple_approved_groups(self):
        self.assertEqual(
            resolve_pilot_role(
                ["lcdash-pilot-viewer", "lcdash-pilot-reviewer"]
            ),
            PilotRole.SUPERVISOR,
        )

    def test_missing_malformed_and_unknown_groups_are_denied(self):
        for groups in (
            [],
            "lcdash-pilot-viewer",
            ["unknown"],
            ["lcdash-pilot-viewer", "unknown"],
            [None],
        ):
            with self.subTest(groups=groups):
                with self.assertRaises(PilotAuthorizationDenied):
                    resolve_pilot_role(groups)

    def test_permissions_are_explicit_and_role_scoped(self):
        self.assertEqual(
            authorize_pilot_permission(
                ["lcdash-pilot-viewer"], "pilot.readiness.view"
            ),
            PilotRole.VIEWER,
        )
        with self.assertRaises(PilotAuthorizationDenied):
            authorize_pilot_permission(
                ["lcdash-pilot-viewer"], "rag.advisory.query"
            )
        self.assertEqual(
            authorize_pilot_permission(
                ["lcdash-pilot-reviewer"], "rag.advisory.query"
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

    def test_unknown_permissions_are_denied_for_administrator(self):
        for permission in ("", "aws.manage", "users.create", "station-alert.release"):
            with self.subTest(permission=permission):
                with self.assertRaises(PilotAuthorizationDenied):
                    authorize_pilot_permission(
                        ["lcdash-pilot-administrator"], permission
                    )


if __name__ == "__main__":
    unittest.main()
