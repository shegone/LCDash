"""Deny-by-default application role contract for selected cloud-pilot users."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable


class PilotAuthorizationDenied(PermissionError):
    """Sanitized denial for an absent, unknown, or unauthorized pilot role."""


class PilotRole(StrEnum):
    """The pilot roles.

    ``USER`` is the restricted tier that the sanitized build targets.
    ``SUPERVISOR`` is unrestricted access to operational data, including
    patient and reporter PII. ``ADMIN`` adds pilot access review on top and
    nothing else -- it confers no AWS, Cognito, or tenant authority.

    ``AVATAR`` is not on that ladder: it is the conversation-only surface
    from the avatar plan (docs/planning/MAE_AVATAR_PLAN_2026-08-09.md).
    An avatar-group account can talk to MAE's face at ``/mae/avatar`` and
    reach nothing else -- no dashboard, no CAD payloads beyond what MAE says
    aloud. Enforcement is app/core/avatar_tier.py's deny-by-default path
    allowlist, the same machinery that enforces the ``user`` tier.

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
    DISPATCHER = "dispatcher"
    ADMIN = "admin"
    AVATAR = "avatar"


COGNITO_GROUP_ROLE_MAP = {
    "lcdash-pilot-user": PilotRole.USER,
    "lcdash-pilot-supervisor": PilotRole.SUPERVISOR,
    "lcdash-pilot-dispatcher": PilotRole.DISPATCHER,
    "lcdash-pilot-admin": PilotRole.ADMIN,
    "lcdash-pilot-avatar": PilotRole.AVATAR,
}

# Lower binds tighter. Mirrors the Cognito group precedence in
# infrastructure/lcdash_pilot/foundation_stack.py, though this map -- not
# Cognito's -- is what actually resolves a user's effective role.
#
# AVATAR binds loosest on purpose: the group exists for accounts that hold
# nothing else, so if someone is ALSO in a dashboard group, the dashboard
# role wins and they reach the avatar page through its permission instead.
ROLE_PRECEDENCE = {
    PilotRole.USER: 30,
    PilotRole.SUPERVISOR: 20,
    # Between supervisor and user: someone in both the supervisor and
    # dispatcher groups is a supervisor; someone in dispatcher and user
    # groups is a dispatcher.
    PilotRole.DISPATCHER: 25,
    PilotRole.ADMIN: 10,
    PilotRole.AVATAR: 40,
}

# The restricted tier, as scoped by Ted on 2026-08-09: the dashboard, station
# alerts, the GIS map, and the heat map -- addresses, call types, and
# responding unit numbers, with no route to call detail. Analytics and the
# document library were in this set while the tier was hypothetical; both were
# excluded when it was actually specified, so they are gone rather than left
# here contradicting the thing that enforces them.
#
# ENFORCEMENT DOES NOT LIVE HERE. app/core/sanitized_tier.py holds the path
# allowlist and the field allowlist that the application actually applies;
# this map is the vocabulary, not the gate. Keep the two in agreement.
USER_PERMISSIONS = frozenset(
    {
        "pilot.readiness.view",
        "dashboard.synthetic.view",
        "station.alerts.view",
        "map.view",
    }
)
SUPERVISOR_PERMISSIONS = USER_PERMISSIONS | {
    "analytics.review.view",
    "documents.review.view",
    "rag.advisory.query",
    "voice.advisory.use",
    "avatar.converse",
}
ADMIN_PERMISSIONS = SUPERVISOR_PERMISSIONS | {
    "pilot.access.review",
}

# Dispatcher (scoped by Ted, 2026-08-20): everything a supervisor has --
# live CAD, call detail, analytics, reports, knowledge, MAE herself --
# except MAE's avatar page, Mindshare/JACK, and the Tools & Quality
# section. Enforcement lives in app/core/dispatcher_tier.py (a denylist,
# not an allowlist -- see that module for why); this set is the
# vocabulary, kept in agreement.
DISPATCHER_PERMISSIONS = SUPERVISOR_PERMISSIONS - {"avatar.converse"}

# The avatar surface and nothing else. Deliberately NOT a superset of
# USER_PERMISSIONS: an avatar account gets no dashboard, no map, no alerts.
# ``user`` deliberately lacks avatar.converse -- MAE answers from live CAD,
# which is exactly what that tier exists to withhold.
AVATAR_PERMISSIONS = frozenset({"avatar.converse"})

ROLE_PERMISSIONS = {
    PilotRole.USER: USER_PERMISSIONS,
    PilotRole.SUPERVISOR: SUPERVISOR_PERMISSIONS,
    PilotRole.DISPATCHER: DISPATCHER_PERMISSIONS,
    PilotRole.ADMIN: ADMIN_PERMISSIONS,
    PilotRole.AVATAR: AVATAR_PERMISSIONS,
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
