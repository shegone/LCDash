# AWS hardening — 2026-08-09 (approved by Ted, executed same day)

Six single-resource API changes, all outside the sanctioned five-resource
release scope, each individually approved by Ted in-session and verified by
read-back. None are in CloudFormation templates — if a stack ever recreates
these resources, re-apply by hand from this record.

| # | Change | Resource | Verified state after |
|---|--------|----------|----------------------|
| 1 | SNS alert topic + email subscription | `arn:aws:sns:us-east-1:862772137583:lcdash-p1-alerts` → tedsparks@911logan.com | Created; subscription requires email confirmation |
| 2 | Six CloudWatch alarms → topic | `lcdash-p1-elb-auth-failure` (ELBAuthFailure ≥3/5min), `lcdash-p1-elb-5xx` (≥5/5min), `lcdash-p1-target-5xx` (≥5/5min), `lcdash-p1-no-healthy-target` (HealthyHostCount <1 for 5min), `lcdash-p1-rds-free-storage` (<5 GB), `lcdash-p1-rds-cpu` (>80% for 15min) | All six exist, all actions point at the topic |
| 3 | RDS storage-autoscaling ceiling | `lcdash-p1-logan-use1-db` | `MaxAllocatedStorage` 20 → 100 GB, applied immediately, no pending |
| 4 | S3 versioning + lifecycle | `lcdash-p1-logan-use1-862772137583-document-library` | Versioning Enabled; noncurrent versions expire at 30 days; incomplete multipart uploads aborted at 7 |
| 5 | Cognito deletion protection | pool `us-east-1_MYGfceA7q` | ACTIVE. Update built from a full describe snapshot to dodge the update-user-pool reset-to-defaults trap; MFA ON, custom email wording, SES sender, invite template, admin-only recovery, min length 10 all verified intact after |
| 6 | ECR scan-on-push + immutable tags | `lcdash-p1-logan-use1-web` | `scanOnPush: true`, `imageTagMutability: IMMUTABLE` |

## Notes and consequences

- **The "site down" alarm is HealthyHostCount, not ECS RunningTaskCount** —
  RunningTaskCount needs Container Insights, which is off. Same signal.
- **This alarm will email on deploys** until `minimumHealthyPercent` is raised
  from 0 to 100 (proposed, not yet approved): today's deploys may dip to zero
  healthy targets, and five consecutive sub-1 minutes trips it.
- **ECR immutability means a same-commit rebuild is rejected** (its
  `release-<12hex>` tag already exists). A re-release needs a new commit.
  This is the immutable-build doctrine, now enforced by the registry.
- **update-user-pool trap, for the record:** it resets every parameter you do
  not pass. The applied input was generated from `describe-user-pool` +
  `get-user-pool-mfa-config` and preserved all mutable settings explicitly.
  Do the same for any future pool change.

## Proposed but NOT executed (awaiting approval)

- `minimumHealthyPercent` 0 → 100 and deregistration delay 300s → 30s
  (make-before-break deploys; bundle with next release)
- ALB access logs to S3; ALB deletion protection
- GuardDuty (~$5/mo)
