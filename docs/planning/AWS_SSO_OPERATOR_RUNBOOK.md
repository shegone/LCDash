# Temporary AWS SSO Operator Runbook

Use this runbook to operate an already-verified temporary IAM Identity Center
(SSO) profile safely. It does not grant deployment authority. Package 5A must
be fully approved and unexpired before any AWS write command is run.

## Identity separation

- `tedsparks` / `AdministratorAccess` is a human bootstrap and oversight
  identity only. Never use it as a standing Kiro or Codex deployment identity.
- Kiro or Codex deployments require the task-specific least-privilege profile
  and role recorded in the approved Package 5A gate.
- Permanent access keys, IAM user keys, long-lived credentials, exported
  credentials, and credentials stored in Git, prompts, logs, or handoffs are
  prohibited.
- Keep the local AWS SSO configuration and cache outside this repository. Do
  not copy cache files, tokens, browser codes, or session output into evidence.

## Operator record

Complete this non-secret record for each authorized session.

| Required field | Session value or evidence reference |
| --- | --- |
| Package 5A gate ID/version | [REQUIRED] |
| Gate approval start/expiration | [REQUIRED] |
| Human operator | [REQUIRED] |
| Expected AWS account ID/classification/purpose | [REQUIRED] |
| Expected partition and region | [REQUIRED] |
| Human bootstrap profile name | [REQUIRED] |
| Task-specific deployment profile and role | [REQUIRED for writes] |
| Exact approved command list | [REQUIRED for writes] |
| Evidence location | [REQUIRED: sanitized, non-secret reference] |

## Safe session procedure

1. Open the approved Package 5A gate and confirm every required field is
   complete, the decision is **AUTHORIZED**, and the approval has not expired.
   If not, stop; only non-mutating identity verification is permitted.
2. A human starts a temporary session with:

   ```powershell
   aws sso login --profile <human-bootstrap-profile>
   ```

   Complete MFA in the trusted sign-in flow. Never relay the browser code,
   token, or session material to an agent or store it in repository evidence.
3. Verify the caller without changing AWS state:

   ```powershell
   aws sts get-caller-identity --profile <profile>
   aws configure list --profile <profile>
   ```

   Compare the returned account and role with the approved operator record.
   Record only a sanitized evidence reference. A mismatch is a stop condition.
4. For an approved write, switch to the recorded task-specific deployment
   profile. Do not give Kiro or Codex the human `AdministratorAccess` profile.
   Set the approved region explicitly for the process:

   ```powershell
   $env:AWS_PROFILE = '<task-specific-deployment-profile>'
   $env:AWS_REGION = '<approved-region>'
   $env:AWS_DEFAULT_REGION = '<approved-region>'
   aws sts get-caller-identity
   ```

5. Recheck the gate immediately before execution. Run only the exact approved
   command list against the permitted account, region, names, and resource
   types. Treat `cdk bootstrap`, deployments, and every create/update/delete
   action as writes. Stop for any prompt, drift, unexpected resource, expanded
   permission, cost warning, or command not named in the gate.
6. Capture only sanitized evidence: gate ID, time, operator, account ID, assumed
   role, region, command name, result, and evidence location. Never capture
   credentials, tokens, raw templates containing secrets, or protected data.
7. When finished, close the temporary session:

   ```powershell
   aws sso logout
   Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
   Remove-Item Env:AWS_REGION -ErrorAction SilentlyContinue
   Remove-Item Env:AWS_DEFAULT_REGION -ErrorAction SilentlyContinue
   ```

   Note that `aws sso logout` removes locally cached SSO sessions for all
   profiles; coordinate first if another approved session is active.

## Stop conditions

Stop without running an AWS write if any of these conditions is true:

- Package 5A is missing, incomplete, not authorized, expired, or outside scope.
- The account, role, region, command, resource, data class, or operator differs
  from the authorization record.
- The task-specific deployment role is absent or broader than its approved
  least-privilege policy.
- MFA, Identity Center assignment, billing/budget, or CloudTrail evidence is
  unavailable or contradictory.
- Rollback ownership or procedure is unclear.
- The task could reach production `.227`, workstation `.15`,
  `E:\Projects\LCDash`, live CAD, credentials, backups, operational data,
  webhooks, paging, station alerts, or any operational output.
- The session is expired, a permanent key is requested, or output may expose a
  credential or protected record.

Escalate the discrepancy to the named approver. Do not widen permissions,
change identity settings, create a replacement role, or improvise a command.

## References

- [Package 5A authorization gate](PACKAGE_5A_AUTHORIZATION_GATE.md)
- [AWS workspace boundary](../../AWS_WORKSPACE.md)
- [Durable AWS move handoff](../../handoffs/AWS_MOVE_THREAD_HANDOFF.md)
