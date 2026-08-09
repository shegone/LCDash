"""Reconcile the Cognito user pool against a declared list of approved users.

Provisioning was previously console-only, with no record in the repository of
who should have access. This turns the written policy in
``docs/planning/PHASE1_AUTHENTICATION_MODEL.md`` -- every account named,
administrator-created, assigned to exactly one approved group, reviewed, and
removed when no longer needed -- into a file that can be reviewed in a diff and
a plan that can be applied repeatedly.

The planning half is pure and has no AWS dependency, so the interesting
behaviour (who gets created, who loses a group, who gets disabled, and which
plans are refused outright) is testable without credentials.

Two deliberate choices:

* Removed users are **disabled, not deleted**. Disabling revokes access
  immediately while preserving the account for later audit.
* A plan that would disable every remaining enabled user is **refused**. That is
  the signature of an empty or truncated approved-users file, and applying it
  would lock everyone out of the dashboard at once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from app.core.cloud_pilot_roles import COGNITO_GROUP_ROLE_MAP, PilotRole

# The pool sets UsernameAttributes=["email"], so a user's email *is* their
# username. Keep that assumption in one place.
USERNAME_IS_EMAIL = True

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")

ActionKind = Literal[
    "create_user",
    "add_to_group",
    "remove_from_group",
    "enable_user",
    "disable_user",
]


class ApprovedUsersError(ValueError):
    """Raised for a malformed or unsafe approved-users declaration."""


def _role_to_group() -> dict[PilotRole, str]:
    """Invert the group/role map, refusing an ambiguous inversion."""

    inverted: dict[PilotRole, str] = {}
    for group, role in COGNITO_GROUP_ROLE_MAP.items():
        if role in inverted:
            raise ApprovedUsersError(
                f"Role {role} maps to more than one Cognito group; cannot invert."
            )
        inverted[role] = group
    return inverted


ROLE_TO_GROUP = _role_to_group()
MANAGED_GROUPS = frozenset(COGNITO_GROUP_ROLE_MAP)


@dataclass(frozen=True, slots=True)
class ApprovedUser:
    email: str
    name: str
    role: PilotRole

    @property
    def group(self) -> str:
        return ROLE_TO_GROUP[self.role]


@dataclass(frozen=True, slots=True)
class CognitoUserState:
    """Current state of one pool user, as read from Cognito."""

    email: str
    enabled: bool
    groups: frozenset[str] = frozenset()
    status: str = ""


@dataclass(frozen=True, slots=True)
class SyncAction:
    kind: ActionKind
    email: str
    group: str | None = None
    reason: str = ""

    def describe(self) -> str:
        if self.group:
            return f"{self.kind}: {self.email} [{self.group}] -- {self.reason}"
        return f"{self.kind}: {self.email} -- {self.reason}"


@dataclass(frozen=True, slots=True)
class SyncPlan:
    actions: tuple[SyncAction, ...] = ()
    warnings: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()

    @property
    def safe_to_apply(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.actions


def load_approved_users(path: str | Path) -> tuple[ApprovedUser, ...]:
    """Parse and validate the approved-users file."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApprovedUsersError(f"Approved-users file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ApprovedUsersError(f"Approved-users file is not valid JSON: {exc}") from exc
    return parse_approved_users(raw)


def parse_approved_users(document: Any) -> tuple[ApprovedUser, ...]:
    if not isinstance(document, Mapping):
        raise ApprovedUsersError("Approved-users document must be a JSON object.")
    entries = document.get("users")
    if not isinstance(entries, list) or not entries:
        raise ApprovedUsersError("Approved-users document must list at least one user.")

    users: list[ApprovedUser] = []
    seen: set[str] = set()
    valid_roles = {str(role) for role in PilotRole}

    for index, entry in enumerate(entries):
        where = f"users[{index}]"
        if not isinstance(entry, Mapping):
            raise ApprovedUsersError(f"{where} must be an object.")

        email = str(entry.get("email") or "").strip().lower()
        if not _EMAIL.fullmatch(email):
            raise ApprovedUsersError(f"{where}.email is not a valid address: {email!r}")
        if email in seen:
            raise ApprovedUsersError(f"{where}.email is listed more than once: {email}")
        seen.add(email)

        name = str(entry.get("name") or "").strip()
        if not name:
            # The governance model requires every account to be named.
            raise ApprovedUsersError(f"{where}.name is required.")

        role_name = str(entry.get("role") or "").strip().lower()
        if role_name not in valid_roles:
            raise ApprovedUsersError(
                f"{where}.role must be one of {sorted(valid_roles)}, got {role_name!r}"
            )

        users.append(ApprovedUser(email=email, name=name, role=PilotRole(role_name)))

    return tuple(users)


def plan_user_sync(
    approved: Iterable[ApprovedUser],
    current: Iterable[CognitoUserState],
    *,
    allow_disabling_everyone: bool = False,
) -> SyncPlan:
    """Diff the declared list against pool state and return an ordered plan."""

    approved_by_email = {user.email: user for user in approved}
    current_by_email = {state.email.strip().lower(): state for state in current}

    actions: list[SyncAction] = []
    warnings: list[str] = []
    refusals: list[str] = []

    if not approved_by_email:
        refusals.append("No approved users were declared; refusing to plan.")
        return SyncPlan(refusals=tuple(refusals))

    for email, user in sorted(approved_by_email.items()):
        state = current_by_email.get(email)
        if state is None:
            actions.append(
                SyncAction(
                    kind="create_user",
                    email=email,
                    group=user.group,
                    reason=f"approved as {user.role}",
                )
            )
            actions.append(
                SyncAction(
                    kind="add_to_group",
                    email=email,
                    group=user.group,
                    reason=f"approved as {user.role}",
                )
            )
            continue

        if not state.enabled:
            actions.append(
                SyncAction(
                    kind="enable_user",
                    email=email,
                    reason="present in the approved list but disabled in the pool",
                )
            )

        # Exactly one managed group, per the single-group governance rule.
        managed = state.groups & MANAGED_GROUPS
        for stale in sorted(managed - {user.group}):
            actions.append(
                SyncAction(
                    kind="remove_from_group",
                    email=email,
                    group=stale,
                    reason=f"approved role is {user.role}",
                )
            )
        if user.group not in state.groups:
            actions.append(
                SyncAction(
                    kind="add_to_group",
                    email=email,
                    group=user.group,
                    reason=f"approved as {user.role}",
                )
            )

        unmanaged = state.groups - MANAGED_GROUPS
        if unmanaged:
            # Left alone rather than removed: an unrecognized group causes
            # resolve_pilot_role to deny the user outright, so this is a
            # lockout that needs a human decision, not a silent cleanup.
            warnings.append(
                f"{email} is in unrecognized group(s) {sorted(unmanaged)}; "
                "resolve_pilot_role will deny this user until that is resolved."
            )

    removed = sorted(set(current_by_email) - set(approved_by_email))
    would_disable = [
        email for email in removed if current_by_email[email].enabled
    ]
    for email in would_disable:
        actions.append(
            SyncAction(
                kind="disable_user",
                email=email,
                reason="not present in the approved list",
            )
        )

    enabled_now = [
        email for email, state in current_by_email.items() if state.enabled
    ]
    if (
        enabled_now
        and set(would_disable) == set(enabled_now)
        and not allow_disabling_everyone
    ):
        refusals.append(
            f"This plan would disable every enabled user ({len(enabled_now)}), "
            "locking everyone out. That usually means the approved-users file is "
            "empty or truncated. Re-run with allow_disabling_everyone if intended."
        )

    return SyncPlan(
        actions=tuple(actions),
        warnings=tuple(warnings),
        refusals=tuple(refusals),
    )


# --------------------------------------------------------------------------
# AWS-facing half. Imported lazily by the CLI so the planning logic above
# stays usable, and testable, without boto3 or credentials.
# --------------------------------------------------------------------------


def read_pool_state(client: Any, user_pool_id: str) -> tuple[CognitoUserState, ...]:
    """Read every user and their group membership from the pool."""

    states: list[CognitoUserState] = []
    paginator = client.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=user_pool_id):
        for user in page.get("Users", ()):
            username = str(user.get("Username") or "")
            attributes = {
                str(item.get("Name")): str(item.get("Value") or "")
                for item in user.get("Attributes", ())
            }
            email = (attributes.get("email") or username).strip().lower()
            groups = _read_user_groups(client, user_pool_id, username)
            states.append(
                CognitoUserState(
                    email=email,
                    enabled=bool(user.get("Enabled", False)),
                    groups=frozenset(groups),
                    status=str(user.get("UserStatus") or ""),
                )
            )
    return tuple(states)


def _read_user_groups(client: Any, user_pool_id: str, username: str) -> list[str]:
    groups: list[str] = []
    paginator = client.get_paginator("admin_list_groups_for_user")
    for page in paginator.paginate(Username=username, UserPoolId=user_pool_id):
        groups.extend(
            str(group.get("GroupName") or "") for group in page.get("Groups", ())
        )
    return [group for group in groups if group]


def apply_plan(
    client: Any,
    user_pool_id: str,
    plan: SyncPlan,
    approved: Iterable[ApprovedUser],
) -> tuple[str, ...]:
    """Execute an approved plan. Refuses to run a plan with any refusal."""

    if not plan.safe_to_apply:
        raise ApprovedUsersError(
            "Refusing to apply: " + "; ".join(plan.refusals)
        )

    names = {user.email: user.name for user in approved}
    performed: list[str] = []

    for action in plan.actions:
        if action.kind == "create_user":
            client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=action.email,
                UserAttributes=[
                    {"Name": "email", "Value": action.email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": names.get(action.email, action.email)},
                ],
                DesiredDeliveryMediums=["EMAIL"],
            )
        elif action.kind == "add_to_group":
            client.admin_add_user_to_group(
                UserPoolId=user_pool_id,
                Username=action.email,
                GroupName=action.group,
            )
        elif action.kind == "remove_from_group":
            client.admin_remove_user_from_group(
                UserPoolId=user_pool_id,
                Username=action.email,
                GroupName=action.group,
            )
        elif action.kind == "enable_user":
            client.admin_enable_user(
                UserPoolId=user_pool_id, Username=action.email
            )
        elif action.kind == "disable_user":
            client.admin_disable_user(
                UserPoolId=user_pool_id, Username=action.email
            )
        else:  # pragma: no cover - ActionKind is exhaustive
            raise ApprovedUsersError(f"Unknown action kind: {action.kind}")
        performed.append(action.describe())

    return tuple(performed)
