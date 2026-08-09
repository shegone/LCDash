"""Make the Cognito user pool match config/approved_users.json.

Dry run by default: it prints the plan and changes nothing. Pass --apply to
execute. Read-only AWS calls are still made in a dry run, because the plan is a
diff against real pool state.

    python scripts/sync_cognito_users.py --user-pool-id us-east-1_xxxx
    python scripts/sync_cognito_users.py --user-pool-id us-east-1_xxxx --apply

The pool id can also come from LCDASH_ALB_IDENTITY_USER_POOL_ID, which is the
same value the running service is given.

Requires AWS credentials with cognito-idp admin permissions on that pool. SSO
sessions expire roughly hourly; re-authenticate before a long run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.tools.cognito_user_sync import (  # noqa: E402
    ApprovedUsersError,
    apply_plan,
    load_approved_users,
    plan_user_sync,
    read_pool_state,
    read_suppressed_addresses,
)

DEFAULT_APPROVED_USERS = REPO_ROOT / "config" / "approved_users.json"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile Cognito users against the approved-users file."
    )
    parser.add_argument(
        "--user-pool-id",
        default=os.environ.get("LCDASH_ALB_IDENTITY_USER_POOL_ID", ""),
        help="Target Cognito user pool id (or set LCDASH_ALB_IDENTITY_USER_POOL_ID).",
    )
    parser.add_argument(
        "--approved-users",
        type=Path,
        default=DEFAULT_APPROVED_USERS,
        help=f"Path to the approved-users file (default: {DEFAULT_APPROVED_USERS}).",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region of the user pool (default: us-east-1).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the plan. Without this the script only prints it.",
    )
    parser.add_argument(
        "--allow-disabling-everyone",
        action="store_true",
        help=(
            "Permit a plan that disables every enabled user. Refused by default, "
            "since that is normally a truncated approved-users file."
        ),
    )
    parser.add_argument(
        "--skip-suppression-check",
        action="store_true",
        help=(
            "Skip checking whether approved addresses are on the SES suppression "
            "list. Only useful if the caller lacks ses:GetSuppressedDestination."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.user_pool_id:
        print(
            "error: --user-pool-id is required "
            "(or set LCDASH_ALB_IDENTITY_USER_POOL_ID)",
            file=sys.stderr,
        )
        return 2

    try:
        approved = load_approved_users(args.approved_users)
    except ApprovedUsersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Approved users declared: {len(approved)}")
    for user in approved:
        print(f"  {user.email:<40} {user.role:<14} ({user.name})")

    import boto3  # imported here so --help works without AWS libraries

    client = boto3.client("cognito-idp", region_name=args.region)

    try:
        current = read_pool_state(client, args.user_pool_id)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
        print(f"error: could not read pool state: {exc}", file=sys.stderr)
        return 1

    print(f"\nUsers currently in {args.user_pool_id}: {len(current)}")
    for state in sorted(current, key=lambda item: item.email):
        flag = "enabled " if state.enabled else "DISABLED"
        groups = ",".join(sorted(state.groups)) or "-none-"
        print(f"  {state.email:<40} {flag} {state.status:<22} {groups}")

    # A suppressed address cannot receive a sign-in code, so that account cannot
    # sign in at all. Checked here because this is the one place that already
    # knows exactly which addresses are supposed to work.
    suppressed = {}
    if not args.skip_suppression_check:
        try:
            suppressed = read_suppressed_addresses(
                boto3.client("sesv2", region_name=args.region),
                (user.email for user in approved),
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"warning: could not check the SES suppression list ({exc});"
                " continuing without it",
                file=sys.stderr,
            )

    plan = plan_user_sync(
        approved,
        current,
        allow_disabling_everyone=args.allow_disabling_everyone,
        suppressed=suppressed,
    )

    for warning in plan.warnings:
        print(f"\nwarning: {warning}")

    if plan.refusals:
        print("\nREFUSED -- nothing was changed:", file=sys.stderr)
        for refusal in plan.refusals:
            print(f"  {refusal}", file=sys.stderr)
        return 1

    if plan.is_empty:
        print("\nPool already matches the approved-users file. Nothing to do.")
        return 0

    print(f"\nPlanned changes ({len(plan.actions)}):")
    for action in plan.actions:
        print(f"  {action.describe()}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to make these changes.")
        return 0

    print("\nApplying...")
    try:
        performed = apply_plan(client, args.user_pool_id, plan, approved)
    except ApprovedUsersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: apply failed partway through: {exc}", file=sys.stderr)
        print(
            "Re-run without --apply to see remaining differences; "
            "the script is idempotent.",
            file=sys.stderr,
        )
        return 1

    for line in performed:
        print(f"  done: {line}")
    print(f"\nApplied {len(performed)} change(s).")
    print(
        "New users receive a temporary password by email and must set their own "
        "password on first sign-in."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
