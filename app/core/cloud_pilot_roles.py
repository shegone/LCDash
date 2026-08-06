"""Deny-by-default application role contract for selected cloud-pilot users."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable


class PilotAuthorizationDenied(PermissionError):
    """Sanitized denial for an absent, unknown, or unauthorized pilot role."""


class PilotRole(StrEnum):
    VIEWER = "viewer"
    SUPERVISOR = "supervisor"
    ADMINISTRATOR = "administrator"


COGNITO_GROUP_ROLE_MAP = {
    "lcdash-pilot-viewer": PilotRole.VIEWER,
    "lcdash-pilot-reviewer": PilotRole.SUPERVISOR,
    "lcdash-pilot-administrator": PilotRole.ADMINISTRATOR,
}

ROLE_PRECEDENCE = {
    PilotRole.VIEWER: 30,
    PilotRole.SUPERVISOR: 20,
    PilotRole.ADMINISTRATOR: 10,
}

VIEWER_PERMISSIONS = frozenset(
    {
        "pilot.readiness.view",
        "dashboard.synthetic.view",
        "analytics.synthetic.view",
        "documents.approved.view",
    }
)
SUPERVISOR_PERMISSIONS = VIEWER_PERMISSIONS | {
    "analytics.review.view",
    "documents.review.view",
    "rag.advisory.query",
    "voice.advisory.use",
}
ADMINISTRATOR_PERMISSIONS = SUPERVISOR_PERMISSIONS | {
    "pilot.access.review",
}

ROLE_PERMISSIONS = {
    PilotRole.VIEWER: VIEWER_PERMISSIONS,
    PilotRole.SUPERVISOR: SUPERVISOR_PERMISSIONS,
    PilotRole.ADMINISTRATOR: ADMINISTRATOR_PERMISSIONS,
}


def resolve_pilot_role(cognito_groups: Iterable[str]) -> PilotRole:
    """Resolve exact trusted Cognito group claims; reject unrecognized claims."""

    if isinstance(cognito_groups, (str, bytes)):
        raise PilotAuthorizationDenied("Trusted Cognito group claims are required.")

    groups = tuple(cognito_groups)
    if not groups or any(not isinstance(group, str) for group in groups):
        raise PilotAuthorizationDenied("Trusted Cognito group claims are required.")

    unknown_groups = set(groups) - COGNITO_GROUP_ROLE_MAP.keys()
    if unknown_groups:
        raise PilotAuthorizationDenied("Unrecognized pilot group claim.")

    roles = {COGNITO_GROUP_ROLE_MAP[group] for group in groups}
    return min(roles, key=ROLE_PRECEDENCE.__getitem__)


def authorize_pilot_permission(
    cognito_groups: Iterable[str],
    permission: str,
) -> PilotRole:
    """Return the resolved role only for an explicitly allowlisted permission."""

    permission_name = str(permission or "").strip().lower()
    if not permission_name or permission_name.startswith("cad."):
        raise PilotAuthorizationDenied("Permission is not allowed for the cloud pilot.")

    role = resolve_pilot_role(cognito_groups)
    if permission_name not in ROLE_PERMISSIONS[role]:
        raise PilotAuthorizationDenied("Permission is not allowed for this pilot role.")
    return role
