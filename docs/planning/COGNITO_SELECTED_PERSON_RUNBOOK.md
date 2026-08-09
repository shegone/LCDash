# Cognito selected-person access runbook

This procedure is for a separately authorized operator adding one named person
to the synthetic, non-operational LCDash cloud pilot. It does not authorize the
work itself. Never record or share passwords, one-time codes, MFA seeds, tokens,
cookies, recovery details, or client secrets.

## Current role contract

| Application role | Exact Cognito group | Authority |
| --- | --- | --- |
| Viewer | `lcdash-pilot-viewer` | View approved synthetic dashboard, readiness, analytics, and documents. |
| Supervisor | `lcdash-pilot-reviewer` | Viewer access plus read-only review and advisory RAG/voice use. |
| Administrator | `lcdash-pilot-administrator` | Application access review only; no Cognito, AWS, tenant, or operational administration. |

The checked-in deployment definition now contains all three groups, including
`lcdash-pilot-administrator`; a test asserts that the deployed group names match
`COGNITO_GROUP_ROLE_MAP` exactly. Still confirm by read-only inspection that the
exact group exists in the live pool before assigning it, since the group is
created by a stack update that may not yet have been applied. Do not create a
group by hand during onboarding. No role permits any CAD read, query,
acknowledgement, write, paging, alert, dispatch, or other operational action.
Missing, malformed, mixed-with-unknown, and unrecognized groups deny access.

## Preferred method: the approved-users file

Onboarding by hand is error-prone and leaves no reviewable record of who has
access. Prefer amending `config/approved_users.json` and running the reconciler,
so the change is visible in a diff and repeatable:

```
python scripts/sync_cognito_users.py --user-pool-id <pool-id>
python scripts/sync_cognito_users.py --user-pool-id <pool-id> --apply
```

The first form is a dry run and changes nothing; read its plan before applying.
The script never chooses, sets, prints, or transmits a password -- Cognito
generates and delivers the temporary credential directly to the person, exactly
as in the manual procedure below. Removing someone from the file disables their
account on the next run rather than deleting it, so the record survives for
audit. A plan that would disable every enabled account is refused, because that
is the signature of a truncated file rather than an intended change.

The manual steps below remain valid and are the fallback when the reconciler
cannot be run.

## Before adding a person

1. Confirm the written authorization names the person, application role,
   approver, business reason, review date, and removal date.
2. Confirm the AWS console shows account `862772137583`, region `us-east-1`, and
   the expected pilot user pool. Stop on any mismatch.
3. Inspect only pool, app-client, domain, and group configuration. Do not open a
   user list for discovery or export users. Confirm administrator-created users,
   required MFA by emailed one-time code, and the exact target group.
4. Use viewer unless the approval explicitly requires supervisor. Administrator
   requires separate approval and a pre-existing exact group.

## Add the selected person

1. In Amazon Cognito, open the confirmed pilot user pool and choose **Create
   user**. Enter only the approved person's email-based sign-in identifier.
2. Let Cognito generate and privately deliver the temporary sign-in material.
   Do not choose, view, copy, paste, screenshot, log, or send it yourself.
3. Add the person to exactly one approved group from the table. Do not create a
   group, attach an IAM role, add custom attributes, or change pool/client/MFA
   settings as part of onboarding.
4. Have the person complete first sign-in on their own device: they set their own
   password, then confirm the one-time code emailed to their approved address.
   There is no authenticator app to install and no MFA seed to enroll. The
   operator must not observe or retain the temporary credential or any one-time
   code. Note that account recovery is administrator-only, so a locked-out user
   needs an administrator-driven reset rather than self-service.
5. Ask the person to verify only the expected synthetic pilot pages. Do not test
   CAD, station alerts, paging, dispatch, public warning, or operational output.

## Record safe evidence and review

Record the person's approved identifier, assigned application role, approver,
completion time, review/removal date, and a pass/fail result for sign-in and
synthetic access. Do not record any authentication secret or session value.
Periodically confirm the approval remains current and remove access promptly
when it expires, following a separately authorized offboarding procedure.
