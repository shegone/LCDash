"""Cognito-backed pilot access management for the in-app admin review page.

This service is the runtime counterpart to scripts/sync_cognito_users.py. The
sync script still disables and retains accounts. This service originally did
the same and deliberately had no delete method; by owner decision on
2026-10-17 (Ted Sparks, pilot owner) admins may now DELETE users and RESET
passwords from the admin users page. Disable remains the way to keep the record
while blocking sign-in.

Guardrails that survive that decision:

* delete requires the operator to retype the target's email, refuses the acting
  admin's own account, and refuses to remove the last enabled admin;
* reset refuses the acting admin's own account and disabled accounts;
* the app NEVER sees, sets, generates, returns, or logs a password. Cognito
  emails the temporary password or verification code straight to the user;
* each delete and reset writes one info log line naming the target and the
  acting admin. Those lines are the audit trail, so they carry nothing else.
"""

from __future__ import annotations

import logging
import re

from app.core.cloud_pilot_roles import COGNITO_GROUP_ROLE_MAP, PilotRole

logger = logging.getLogger(__name__)

# The inverse of the trusted group->role map: role -> Cognito group name.
# Derived rather than restated so a renamed pilot group cannot leave writes
# and reads disagreeing about which group means which role.
_ROLE_GROUP_MAP = {role.value: group for group, role in COGNITO_GROUP_ROLE_MAP.items()}

# Conservative shape check, not RFC 5322: one @, a non-empty local part, and a
# dotted domain. The goal is to reject obvious junk before it becomes a
# Cognito username forever (usernames are immutable), not to adjudicate every
# address the RFC permits. 320 is the RFC's own overall length ceiling.
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
_EMAIL_MAX_LENGTH = 320


class PilotAccessError(ValueError):
    """A refused access change, with a message safe to show the operator."""


class PilotAccessNotFoundError(PilotAccessError):
    """The target account does not exist (routes map this to HTTP 404)."""


def _aws_error_code(error: Exception) -> str:
    """Read the Cognito error code off a client exception, if it has one.

    Structured this way -- duck-typed on ``.response["Error"]["Code"]`` --
    instead of importing botocore's ClientError, so tests can raise a plain
    stub exception with the same shape and so this module stays importable
    without botocore installed.
    """
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        return str(code or "")
    return ""


class PilotAccessService:
    """List, invite, re-role, disable, enable, reset, and delete the pilot's users.

    Delete and password reset exist by owner decision 2026-10-17; see the
    module docstring for the guardrails that came with them.

    All authorization happens before this class: only an authenticated pilot
    admin reaches the endpoints that call it (permission
    ``pilot.access.review``). What lives here are the guardrails that keep an
    authorized admin from breaking the pilot with one click -- chiefly, never
    removing the last enabled admin, and never letting an admin lock
    themselves out.
    """

    def __init__(self, user_pool_id: str, *, region: str = "us-east-1", client=None) -> None:
        if not str(user_pool_id or "").strip():
            raise ValueError("Pilot access management requires the Cognito user pool id.")
        self._user_pool_id = user_pool_id
        self._region = region
        # Injected in tests; built lazily otherwise (same rationale as
        # LazyCloudCadConnector in app/services/cloud_ai_service.py: the boto3
        # client resolves credentials on construction, and pages that never
        # touch access review should never pay that cost or need those creds).
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("cognito-idp", region_name=self._region)
        return self._client

    # ------------------------------------------------------------------ reads

    def list_users(self) -> list[dict]:
        """Every pool user with their resolved pilot role, invite state, and status."""
        records: list[dict] = []
        token = None
        while True:
            kwargs = {"UserPoolId": self._user_pool_id, "Limit": 60}
            if token:
                kwargs["PaginationToken"] = token
            page = self.client.list_users(**kwargs)
            for user in page.get("Users", []):
                records.append(self._record_from_user(user))
            token = page.get("PaginationToken")
            if not token:
                break
        return records

    def _record_from_user(self, user: dict) -> dict:
        attributes = {
            item.get("Name"): item.get("Value", "")
            for item in user.get("Attributes", user.get("UserAttributes", []))
        }
        email = attributes.get("email", "")
        status = str(user.get("UserStatus", ""))
        created = user.get("UserCreateDate")
        return {
            "email": email,
            "status": status,
            "enabled": bool(user.get("Enabled", False)),
            "role": self._role_of(user.get("Username", "") or email),
            "created_at": created.isoformat() if created is not None else "",
            # FORCE_CHANGE_PASSWORD means the temporary-password invite went
            # out but the person has never signed in -- the state the review
            # page needs to surface so someone chases the invite, not the user.
            "invited_pending": status == "FORCE_CHANGE_PASSWORD",
        }

    def _groups_of(self, username: str) -> list[str]:
        response = self.client.admin_list_groups_for_user(
            UserPoolId=self._user_pool_id, Username=username
        )
        return [item.get("GroupName", "") for item in response.get("Groups", [])]

    def _role_of(self, username: str) -> str:
        # A user in zero known groups renders as a blank role rather than
        # erroring: the sync script can legitimately leave a disabled user
        # groupless, and the review page must still be able to show them.
        roles = [
            COGNITO_GROUP_ROLE_MAP[group].value
            for group in self._groups_of(username)
            if group in COGNITO_GROUP_ROLE_MAP
        ]
        return roles[0] if roles else ""

    def _record_for(self, email: str) -> dict:
        user = self.client.admin_get_user(UserPoolId=self._user_pool_id, Username=email)
        return self._record_from_user(user)

    # ----------------------------------------------------------------- writes

    def invite_user(self, email: str, role: str) -> dict:
        email = self._validated_email(email)
        group = self._validated_group(role)
        try:
            self.client.admin_create_user(
                UserPoolId=self._user_pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    # Pre-verified because the invite email itself proves
                    # deliverability; without this, ALB sign-in would demand a
                    # verification round-trip the pilot cohort was never given.
                    {"Name": "email_verified", "Value": "true"},
                ],
                DesiredDeliveryMediums=["EMAIL"],
                # No MessageAction: the default path is what fires the pool's
                # custom LCDash invite email (see the CustomMessage setup) --
                # SUPPRESS would create a silent account nobody can enter.
            )
        except Exception as error:
            if _aws_error_code(error) == "UsernameExistsException":
                raise PilotAccessError(
                    f"{email} already has an account in this pilot. "
                    "Use resend invite or change their role instead."
                ) from error
            raise
        self.client.admin_add_user_to_group(
            UserPoolId=self._user_pool_id, Username=email, GroupName=group
        )
        logger.info("pilot-access invite_user target=%s role=%s", email, role)
        return {
            "email": email,
            "status": "FORCE_CHANGE_PASSWORD",
            "enabled": True,
            "role": role,
            "created_at": "",
            "invited_pending": True,
        }

    def resend_invite(self, email: str) -> None:
        """Re-send the temporary-password invite to a user who never signed in.

        No pre-check of the user's status: Cognito is the authority on whether
        a resend is meaningful, and racing our own read against it just adds a
        second failure mode. We only translate its refusal into plain English.
        """
        email = self._validated_email(email)
        try:
            self.client.admin_create_user(
                UserPoolId=self._user_pool_id,
                Username=email,
                MessageAction="RESEND",
                DesiredDeliveryMediums=["EMAIL"],
            )
        except Exception as error:
            if _aws_error_code(error) == "UnsupportedUserStateException":
                raise PilotAccessError(
                    f"{email} has already completed sign-up; there is no invite "
                    "to resend."
                ) from error
            raise
        logger.info("pilot-access resend_invite target=%s", email)

    def set_role(self, email: str, role: str, *, acting_subject: str) -> dict:
        email = self._validated_email(email)
        target_group = self._validated_group(role)
        current_groups = [
            group for group in self._groups_of(email) if group in COGNITO_GROUP_ROLE_MAP
        ]
        currently_admin = _ROLE_GROUP_MAP[PilotRole.ADMIN.value] in current_groups

        if currently_admin and role != PilotRole.ADMIN.value:
            # An admin demoting themselves succeeds -- and then the very next
            # page load denies them access review, with no one left in-app to
            # undo it if they were the only admin. Make a second admin do it.
            if acting_subject == email:
                raise PilotAccessError(
                    "You cannot remove your own admin role; have another admin "
                    "make this change."
                )
            if not self._other_enabled_admin_exists(excluding=email):
                raise PilotAccessError(
                    "This change would leave the pilot with no admin. Promote "
                    "another admin first."
                )

        for group in current_groups:
            if group != target_group:
                self.client.admin_remove_user_from_group(
                    UserPoolId=self._user_pool_id, Username=email, GroupName=group
                )
        if target_group not in current_groups:
            self.client.admin_add_user_to_group(
                UserPoolId=self._user_pool_id, Username=email, GroupName=target_group
            )
        logger.info(
            "pilot-access set_role target=%s role=%s acting_subject=%s",
            email, role, acting_subject,
        )
        return self._record_for(email)

    def disable_user(self, email: str, *, acting_subject: str) -> None:
        email = self._validated_email(email)
        if acting_subject == email:
            raise PilotAccessError(
                "You cannot disable your own account; have another admin do it."
            )
        admin_group = _ROLE_GROUP_MAP[PilotRole.ADMIN.value]
        if admin_group in self._groups_of(email) and not self._other_enabled_admin_exists(
            excluding=email
        ):
            raise PilotAccessError(
                "This change would leave the pilot with no admin. Promote "
                "another admin first."
            )
        self.client.admin_disable_user(UserPoolId=self._user_pool_id, Username=email)
        logger.info(
            "pilot-access disable_user target=%s acting_subject=%s",
            email, acting_subject,
        )

    def enable_user(self, email: str) -> None:
        # Re-enabling can only restore access someone already had; it cannot
        # strand the pilot, so no guard is needed here.
        email = self._validated_email(email)
        self.client.admin_enable_user(UserPoolId=self._user_pool_id, Username=email)
        logger.info("pilot-access enable_user target=%s", email)

    def reset_password(self, email: str, *, acting_subject: str) -> dict:
        """Start a password reset for ``email``; the app never touches the password.

        A user who never signed in (FORCE_CHANGE_PASSWORD) has no password to
        reset, so the invitation is re-sent and Cognito emails a NEW temporary
        password. Anyone else gets AdminResetUserPassword: Cognito emails a
        verification code and the user sets a new password at next sign-in.
        Either way the secret travels Cognito -> user's inbox, never through
        this service, its return value, or its logs.
        """
        email = self._validated_email(email)
        if self._same_account(acting_subject, email):
            raise PilotAccessError(
                'You cannot reset your own password here; use "Forgot password" '
                "on the sign-in page."
            )
        user = self._existing_user(email)
        if not user.get("Enabled", False):
            raise PilotAccessError("Enable the account first, then reset the password.")

        if str(user.get("UserStatus", "")) == "FORCE_CHANGE_PASSWORD":
            method = "invitation_resent"
            try:
                self.resend_invite(email)
            except PilotAccessError:
                raise
            except Exception as error:
                self._raise_cognito_failure(error, email, "resend the invitation")
        else:
            method = "reset_code_sent"
            try:
                self.client.admin_reset_user_password(
                    UserPoolId=self._user_pool_id, Username=email
                )
            except Exception as error:
                if _aws_error_code(error) in ("NotAuthorizedException", "UnsupportedUserStateException"):
                    # This pool has no self-service account recovery (email is
                    # the MFA channel, and Cognito will not also use it for
                    # recovery), and AdminResetUserPassword requires it. Say so
                    # instead of the generic "current state" text.
                    raise PilotAccessError(
                        f"Password reset by emailed code is not available for {email} "
                        "on this user pool. To give them a new password: delete the "
                        "account, then invite the same email address again."
                    ) from error
                self._raise_cognito_failure(error, email, "reset the password")
        logger.info(
            "pilot-access reset_password target=%s method=%s acting_subject=%s",
            email, method, acting_subject,
        )
        return {"email": email, "method": method}

    def delete_user(self, email: str, confirm_email: str, *, acting_subject: str) -> None:
        """Permanently delete ``email``'s account (owner decision 2026-10-17).

        Guardrails: the operator must retype the email, may not delete their
        own account, and may not remove the last enabled admin. A repeat on an
        already-deleted user is a not-found error, never a crash.
        """
        email = self._validated_email(email)
        if str(confirm_email or "").strip().lower() != email.lower():
            raise PilotAccessError("Type the user's email address to confirm.")
        if self._same_account(acting_subject, email):
            raise PilotAccessError(
                "You cannot delete your own account; have another admin do it."
            )
        self._existing_user(email)
        groups = self._groups_of(email)
        admin_group = _ROLE_GROUP_MAP[PilotRole.ADMIN.value]
        if admin_group in groups and not self._other_enabled_admin_exists(excluding=email):
            raise PilotAccessError(
                "This change would leave the pilot with no admin. Promote "
                "another admin first."
            )
        role = next(
            (
                COGNITO_GROUP_ROLE_MAP[group].value
                for group in groups
                if group in COGNITO_GROUP_ROLE_MAP
            ),
            "",
        )
        try:
            self.client.admin_delete_user(UserPoolId=self._user_pool_id, Username=email)
        except Exception as error:
            if _aws_error_code(error) == "UserNotFoundException":
                raise PilotAccessNotFoundError(f"No account exists for {email}.") from error
            self._raise_cognito_failure(error, email, "delete the account")
        logger.info(
            "pilot-access delete_user target=%s role=%s acting_subject=%s",
            email, role, acting_subject,
        )

    # ----------------------------------------------------------------- guards

    @staticmethod
    def _same_account(acting_subject: str, email: str) -> bool:
        return str(acting_subject or "").strip().lower() == email.lower()

    def _existing_user(self, email: str) -> dict:
        """Cognito's record for ``email``, or a not-found error the operator can read."""
        try:
            return self.client.admin_get_user(UserPoolId=self._user_pool_id, Username=email)
        except Exception as error:
            if _aws_error_code(error) == "UserNotFoundException":
                raise PilotAccessNotFoundError(f"No account exists for {email}.") from error
            raise

    @staticmethod
    def _raise_cognito_failure(error: Exception, email: str, action: str) -> None:
        """Raise a short, safe PilotAccessError for a Cognito error (call inside ``except``).

        Only the error CODE ever reaches the operator, never AWS's free text.
        An exception with no Cognito code (a network fault, a bug) is re-raised
        unchanged so it fails loudly instead of being disguised.
        """
        code = _aws_error_code(error)
        if code == "UserNotFoundException":
            raise PilotAccessNotFoundError(f"No account exists for {email}.") from error
        if code == "InvalidParameterException":
            raise PilotAccessError(
                f"Cognito cannot {action} for {email}: the account has no "
                "verified email address or phone to send to."
            ) from error
        if code in ("LimitExceededException", "TooManyRequestsException"):
            raise PilotAccessError(
                f"Cognito is limiting requests for {email} right now. Wait a "
                "few minutes and try again."
            ) from error
        if code in ("NotAuthorizedException", "UnsupportedUserStateException"):
            raise PilotAccessError(
                f"Cognito refused to {action} for {email} in its current state."
            ) from error
        if code:
            raise PilotAccessError(
                f"Cognito could not {action} for {email} ({code})."
            ) from error
        raise error

    def _other_enabled_admin_exists(self, *, excluding: str) -> bool:
        """True when some enabled admin other than ``excluding`` exists.

        This is the last-admin guard: the pilot's only in-app path to access
        review is the admin role, so removing or disabling the final enabled
        admin means the next access change requires the AWS console. Checked
        against live pool state on every mutation rather than cached, because
        two admins editing at once is exactly when the guard matters.
        """
        admin_group = _ROLE_GROUP_MAP[PilotRole.ADMIN.value]
        token = None
        while True:
            kwargs = {
                "UserPoolId": self._user_pool_id,
                "GroupName": admin_group,
                "Limit": 60,
            }
            if token:
                kwargs["NextToken"] = token
            page = self.client.list_users_in_group(**kwargs)
            for user in page.get("Users", []):
                if not user.get("Enabled", False):
                    continue
                attributes = {
                    item.get("Name"): item.get("Value", "")
                    for item in user.get("Attributes", [])
                }
                identity = attributes.get("email") or user.get("Username", "")
                if identity != excluding:
                    return True
            token = page.get("NextToken")
            if not token:
                return False

    @staticmethod
    def _validated_email(email: str) -> str:
        candidate = str(email or "").strip()
        if (
            not candidate
            or len(candidate) > _EMAIL_MAX_LENGTH
            or not _EMAIL.fullmatch(candidate)
        ):
            raise PilotAccessError(
                "That does not look like a valid email address. Check it and "
                "try again."
            )
        return candidate

    @staticmethod
    def _validated_group(role: str) -> str:
        group = _ROLE_GROUP_MAP.get(str(role or "").strip())
        if group is None:
            raise PilotAccessError(
                "Role must be one of: user, supervisor, admin."
            )
        return group
