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

The checked-in deployment definition contains the viewer and reviewer groups.
The administrator group is reserved by the local contract but must be treated
as unavailable unless a read-only live inspection confirms that the exact group
exists. Do not create it during person onboarding. No role permits any CAD read,
query, acknowledgement, write, paging, alert, dispatch, or other operational
action. Missing, malformed, mixed-with-unknown, and unrecognized groups deny
access.

## Before adding a person

1. Confirm the written authorization names the person, application role,
   approver, business reason, review date, and removal date.
2. Confirm the AWS console shows account `862772137583`, region `us-east-1`, and
   the expected pilot user pool. Stop on any mismatch.
3. Inspect only pool, app-client, domain, and group configuration. Do not open a
   user list for discovery or export users. Confirm administrator-created users,
   required software-token MFA, and the exact target group.
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
4. Have the person complete first sign-in and software-token MFA enrollment on
   their own device. The operator must not observe or retain the MFA seed or
   one-time codes.
5. Ask the person to verify only the expected synthetic pilot pages. Do not test
   CAD, station alerts, paging, dispatch, public warning, or operational output.

## Record safe evidence and review

Record the person's approved identifier, assigned application role, approver,
completion time, review/removal date, and a pass/fail result for sign-in and
synthetic access. Do not record any authentication secret or session value.
Periodically confirm the approval remains current and remove access promptly
when it expires, following a separately authorized offboarding procedure.
