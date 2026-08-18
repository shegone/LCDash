# Remote session observer access — 2026-08-18

Remote Claude Code sessions (claude.ai/code) run in a fresh container with no
standing access to the pilot. This document sets up the minimum needed for a
session to independently verify what the v10 thread had to verify by hand:
which task-definition revision is live, whether the running task's image
digest matches the release, target health, error lines in the web log, and
month-to-date spend against the USD 200 pilot budget.

The access is deliberately read-only. Deployments, stack changes, secrets,
and anything operational stay with the human-gated CodeBuild/CDK path, per
`AWS_WORKSPACE.md`. Nothing here can touch `.227`, `.15`, or live CAD.

## What gets created

- IAM user `lcdash-claude-observer` in pilot account `862772137583`, with the
  inline policy `infrastructure/iam/LCDashObserverReadOnlyPolicy.json`:
  describe-only ECS/ELB/ECR/CloudFormation, log reads scoped to
  `/lcdash/*` log groups, and Cost Explorer / budget reads. No IAM, no
  writes, no Secrets Manager, no RDS data access, no Cognito.

## One-time setup (about five minutes)

1. Sign into the AWS console on the pilot account and open **CloudShell**
   (us-east-1).
2. Run:

   ```bash
   git clone --depth 1 --branch aws/modular-county-platform \
     https://github.com/shegone/LCDash.git
   bash LCDash/infrastructure/tools/create_observer.sh
   ```

3. The script prints one access key pair. Copy the two values into the
   Claude Code environment settings for this repository
   (claude.ai/code → environment → environment variables):

   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_DEFAULT_REGION` = `us-east-1`

   Close CloudShell afterwards; the secret must not be pasted into chat,
   Git, logs, or anywhere else. Re-running the script later rotates the key.

4. Optional, for browser-level checks of the public endpoint: add
   `aws.logan911.com` to the environment's network allowlist. Without it,
   sessions can still do every AWS-API check; they just cannot curl the ALB
   redirect. (`*.amazonaws.com` is already reachable through the proxy.)

## What a session does with it

```bash
pip install boto3   # containers start clean
python infrastructure/tools/pilot_status.py            # human-readable
python infrastructure/tools/pilot_status.py --json     # for diffing
python infrastructure/tools/pilot_status.py --log-minutes 720   # overnight scan
```

The script refuses to run if the credentials resolve to any account other
than `862772137583`, and exits non-zero when it aborts, so a wrong key in
the environment fails loudly instead of reporting someone else's ECS.

## Revoking

Delete the user's access keys (or the user) in IAM, and clear the two
environment variables from the Claude Code environment settings. Nothing
else references the observer.
