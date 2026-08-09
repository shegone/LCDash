"""Deny-by-default application role contract for selected cloud-pilot users."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable


class PilotAuthorizationDenied(PermissionError):
    """Sanitized denial for an absent, unknown, or unauthorized pilot role."""


class PilotRole(StrEnum):
    """The three pilot roles, least to most privileged.

    ``USER`` is the restricted tier that the sanitized build targets.
    ``SUPERVISOR`` is unrestricted access to operational data, including
    patient and reporter PII. ``ADMIN`` adds pilot access review on top and
    nothing else -- it confers no AWS, Cognito, or tenant authority.

    ``ADMIN`` is spelled "admin", not "administrator", because several existing
    authorization checks already test for "admin" (see
    ``_require_cloud_report_identity`` in app/main.py and ALLOWED_VISIBILITY in
    app/services/cloud_report_service.py). While every request carried a
    hardcoded "viewer" this mismatch was unreachable; the moment real per-user
    roles were switched on, an administrator would have been denied report
    writes by a check meant to permit them.
    """

    USER = "user"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


COGNITO_GROUP_ROLE_MAP = {
    "lcdash-pilot-user": PilotRole.USER,
    "lcdash-pilot-supervisor": PilotRole.SUPERVISOR,
    "lcdash-pilot-admin": PilotRole.ADMIN,
}

# Lower binds tighter. Mirrors the Cognito group precedence in
# infrastructure/lcdash_pilot/foundation_stack.py, though this map -- not
# Cognito's -- is what actually resolves a user's effective role.
ROLE_PRECEDENCE = {
    PilotRole.USER: 30,
    PilotRole.SUPERVISOR: 20,
    PilotRole.ADMIN: 10,
}

# The restricted tier. When the sanitized build lands, this is the set that
# narrows; supervisor and admin are deliberately unrestricted.
USER_PERMISSIONS = frozenset(
    {
        "pilot.readiness.view",
        "dashboard.synthetic.view",
        "analytics.synthetic.view",
        "documents.approved.view",
    }
)
SUPERVISOR_PERMISSIONS = USER_PERMISSIONS | {
    "analytics.review.view",
    "documents.review.view",
    "rag.advisory.query",
    "voice.advisory.use",
}
ADMIN_PERMISSIONS = SUPERVISOR_PERMISSIONS | {
    "pilot.access.review",
}

ROLE_PERMISSIONS = {
    PilotRole.USER: USER_PERMISSIONS,
    PilotRole.SUPERVISOR: SUPERVISOR_PERMISSIONS,
    PilotRole.ADMIN: ADMIN_PERMISSIONS,
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
