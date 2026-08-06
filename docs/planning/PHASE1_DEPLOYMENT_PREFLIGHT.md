# Phase 1 deployment preflight package

Status: **AUTHORIZED_TO_BEGIN_PHASE1_PREWRITE**

This status records time-bounded human authorization to begin only the exact
Phase 1 pre-write sequence: reviewed IAM boundary/deployment-access setup, USD
200 budget alerts, constrained CDK bootstrap, certificate-request stack,
manual Cloudflare DNS validation hold, and the foundation stack with
`PilotServiceDesiredCount=0` and `PilotImageDigest=NOT_PUBLISHED`. It does not
authorize an image build or push, service activation, live CAD, Phase 2, `.227`,
PC `.15`, credentials disclosure, or operational outputs. Each step still
requires its documented stop conditions and sanitized evidence.

## Local gate verifier

Run the deterministic, read-only verifier from the repository root:

```powershell
python -m infrastructure.tools.verify_phase1_gate
python -m infrastructure.tools.verify_phase1_gate --json
```

Exit code `0` means either the full evidence set passes or the exact pre-write
authorization is recorded with post-action evidence explicitly pending. Exit
code `2` means the gate is blocked by missing or inconsistent authorization
records. The verifier does not call AWS, CDK, DNS, CAD, or any network service,
and it never changes an authorization decision. It only verifies a decision
already recorded by the designated human approver. Evidence fields must contain
references only and must never contain credentials, tokens, passwords, or
secret values.

After a temporary AWS session is separately authorized and established, the
read-only readiness checker may verify the caller target and current resource
states without making changes:

```powershell
python -m infrastructure.tools.check_aws_readiness --profile <TEMPORARY-PROFILE>
python -m infrastructure.tools.check_aws_readiness --profile <TEMPORARY-PROFILE> --certificate-arn <REVIEWED-ACM-ARN>
python -m infrastructure.tools.check_aws_readiness --aws-executable "<FULL-PATH-TO-AWS.EXE>" --profile <TEMPORARY-PROFILE>
```

It invokes only caller-identity, local region configuration, stack-description,
and optional certificate-description reads. Its report is sanitized and never
prints caller ARNs, profile names, raw AWS responses, or command errors. A READY
report is evidence for review only; it does not authorize bootstrap, deployment,
DNS, image publication, or activation.
On Windows the checker resolves `aws.exe`, `aws.cmd`, then `aws` through PATH
without using a command shell. If the temporary session's AWS CLI is installed
outside that PATH, `--aws-executable` supplies its reviewed full path explicitly.

Before any separately authorized image build, generate and review the local
source manifest from the repository root:

```powershell
python -m infrastructure.tools.generate_container_release_manifest > <REVIEWED-MANIFEST-PATH>
```

The generator performs no Docker, AWS, or network action. It hashes the exact
approved Dockerfile, requirements file, application/runtime directories, Logan
synthetic profile/schema, and database SQL inputs in deterministic path order.
It excludes environment files, documentation, scripts, tests, backups,
credentials, secret-like files, symlinks, and every path outside the repository
allowlist. The initial `ecr_image_digest` remains null and status remains
`SOURCE_REVIEW_ONLY_NOT_BUILT`; a later authorized release record must link the
reviewed source-manifest hash to the independently observed immutable ECR digest.

This package fixes and records the narrowly authorized pre-write scope. It does
not broaden that permission or supply credentials. The machine-readable source is
`infrastructure/phase1_deployment_allowlist.json`.

## Fixed target and stack order

- Account: `862772137583`
- Partition: `aws`
- Region: `us-east-1`
- Application stacks, in the only permitted order:
  1. `lcdash-p1-logan-use1-certificate`
  2. `lcdash-p1-logan-use1-foundation`

No wildcard stack deployment, `--all`, another account, another region, or
another stack is permitted. The certificate stack must finish with an issued
certificate before the foundation is attempted.

## Bootstrap is a separate prerequisite

Bootstrap is not one of the two application stacks and is not implicitly
authorized. A human must first determine whether the target already has a
sufficient `CDKToolkit` stack. If bootstrap is absent or too old, Package 5A
must separately record approval for `aws://862772137583/us-east-1`, the default
qualifier `hnb659fds`, termination protection, the exact task-specific operator
role, and an organization-approved permissions-boundary policy name.

The proposed boundary name is `LCDashPhase1Boundary`, with local-only policy and
trust-model templates documented in `PHASE1_IAM_REVIEW.md`. They have not been
created, attached, or AWS-validated, so human IAM review remains a hard stop.
Cross-account trust, wildcard trust, omission of the boundary, deletion of
`CDKToolkit`, and bootstrap outside the target account/region are prohibited.
The later reviewed command must have this exact shape:

```text
cdk bootstrap aws://862772137583/us-east-1 --qualifier hnb659fds --custom-permissions-boundary LCDashPhase1Boundary --termination-protection
```

Do not run it until IAM Access Analyzer review, the permission-set assignment,
the exact bootstrap template/version, and the command are approved. This package
does not create a deployment role, IAM policy, permissions boundary, or
bootstrap stack.

## Reviewed deployment sequence

All commands below are templates for later human approval, not commands to run
now. Use a pinned, reviewed CDK CLI and the recorded temporary IAM Identity
Center profile. Never export long-lived credentials.

1. Human verifies account, assumed role, MFA, region, approval time window,
   billing access, current `CDKToolkit` state, and clean reviewed source commit.
2. Run offline tests, synth both stacks with explicit account/region context,
   review each template against the JSON resource inventory, and run an exact
   stack-scoped diff. Any unexpected resource or replacement stops the process.
3. If separately required and authorized, perform the constrained bootstrap
   above, then verify its status and termination protection.
   The reviewed modern bootstrap template applies `LCDashPhase1Boundary` to the
   exact `cdk-hnb659fds-cfn-exec-role-862772137583-us-east-1` role. Its exact
   file-publishing, image-publishing, lookup, and deployment-action helper roles
   do not carry a permissions boundary, so the operator boundary permits those
   four exact qualifier-scoped roles while continuing to require the boundary
   on every `lcdash-p1-logan-use1-*` role and the CDK execution role. The
   bootstrap template uses inline policies; it does not require creation of a
   customer-managed IAM policy. Managed-policy attachment remains restricted to
   the exact qualifier role scope.

   If a failed first bootstrap is retained at `ROLLBACK_COMPLETE`, stop. Under
   separate authorization, first publish and verify the corrected boundary,
   then disable termination protection on only `CDKToolkit`, delete only that
   failed stack, wait for confirmed absence, and only then rerun the constrained
   bootstrap. Never broaden to an administrator session as a recovery method.
4. Deploy only the certificate request:

   ```text
   cdk deploy lcdash-p1-logan-use1-certificate --context account=862772137583 --context region=us-east-1 --require-approval never
   ```

   The `--require-approval never` flag is acceptable only after the template,
   diff, resource allowlist, and exact command have received human approval; it
   does not waive approval.
5. Human copies the exact ACM validation CNAME into authoritative Cloudflare
   DNS, keeps it DNS-only, independently verifies public DNS, and waits for ACM
   status `ISSUED`. Hostinger remains the registrar only: do not change
   nameservers or edit Hostinger DNS. No Route 53 resource may be created.
6. Human records the issued ARN and approves every non-secret foundation
   parameter: `CertificateArn`, `CognitoDomainPrefix`, `BudgetOwner`,
   `BudgetSubscriberEmail`, `Owner`, `CostCenter`, `Expiration`,
   `ApprovedBedrockResourceArns`, `CreatePilotCloudTrail`, and
   `PilotServiceDesiredCount=0` and `PilotImageDigest=NOT_PUBLISHED`. Parameter values
   must not contain credentials, tokens, CAD endpoints, or protected data.
7. Review a stack-scoped foundation diff, then deploy only:

   ```text
   cdk deploy lcdash-p1-logan-use1-foundation --context account=862772137583 --context region=us-east-1 --parameters PilotServiceDesiredCount=0 --parameters PilotImageDigest=NOT_PUBLISHED --parameters <REVIEWED-NON-SECRET-PARAMETERS> --require-approval never
   ```

   The first foundation deployment must use zero. It creates ECR and the single
   ECS service definition without starting a task. A value of one is not an
   authorized substitute for this first step.
8. Under a separate authorization, build and publish the reviewed pilot image
   to the created repository using a separately documented process. Image
   publication is outside this package. Record its immutable digest and scan
   evidence without recording credentials.
9. Only after that evidence is reviewed may a separately approved stack update
   set `PilotServiceDesiredCount=1` and `PilotImageDigest=sha256:<64 lowercase
   hexadecimal characters>`. Review the exact update diff first. The placeholder,
   tags such as `pilot` or `latest`, malformed or uppercase digests, and values
   above one are rejected; autoscaling remains absent.
10. Human copies the legacy-named `HostingerApplicationCnameTarget` output to a
   DNS-only Cloudflare `aws` CNAME, then verifies DNS, HTTPS
   hostname/certificate matching, Cognito redirect,
   unauthenticated denial, MFA, fixed tenant, health, logs, and budget.

## Resource, name, and tag stop conditions

The JSON allowlist records every synthesized CloudFormation type and count.
Only those types and counts are reviewable. The Lambda function and
`Custom::S3AutoDeleteObjects` entries are CDK teardown plumbing for the two
emptyable buckets, not application functions. CloudTrail resources are
conditioned on the reviewed `CreatePilotCloudTrail` decision.

Application names must use `lcdash-p1-logan-use1`; the exact exceptions are the
two Cognito application groups and AWS/CDK-generated physical names. Foundation
resources must carry the fixed tags in the JSON plus reviewed `Owner`,
`BudgetOwner`, `CostCenter`, and `Expiration` parameter tags. The certificate
stack currently synthesizes no explicit tags; the empty tag map is recorded as
the exact reviewed state rather than silently claiming tags that do not exist.

Any extra resource, changed count, unlisted name, missing required tag, Route 53
record, identity pool, NAT gateway, listener bypass, live-CAD setting, backup,
replica, operational output, or resource outside the account/region is a stop.

## Conservative USD 200 monthly control plan

This is a control plan, not a verified price quote. No live Pricing API, Cost
Explorer, account bill, quota, or existing-resource check was performed in this
local-only task. Before deployment, a human with billing visibility must create
or attach a dated AWS Pricing Calculator estimate for the exact Fargate, ALB,
RDS, S3, ECR, logs, Cognito, Bedrock, Transcribe, Polly, Location, data-transfer,
and optional CloudTrail assumptions.

- Monthly target and stop threshold: USD 200.
- Existing CDK budget alert: forecasted spend at 80 percent (USD 160).
- Existing CDK budget alert: actual spend at 100 percent (USD 200).
- Human review checkpoint: actual spend at 50 percent (USD 100), checked in the
  Billing console or Cost Explorer. This is procedural because no additional
  alarm resource is authorized in the current template.
- Ted Sparks / `tedsparks@911logan.com` is the proposed budget owner/subscriber;
  the human must confirm the address and subscription delivery.
- The budget does not stop services automatically. At USD 200 actual or an
  unexplained forecast above USD 200, the named human operator must stop new
  use, investigate by service/tag, and decide whether to scale down or teardown.
- No Savings Plan, Reserved Instance, paid support change, billing view, Cost
  Anomaly Detection resource, or quota increase is authorized by this package.

## Post-deployment evidence

Record sanitized references only:

- CloudFormation success and exact stack/resource inventory;
- issued certificate and public DNS resolution, without private account data;
- initial foundation evidence showing desired count zero, followed by separate
  image digest/scan and activation-update approval evidence;
- ALB HTTPS/authentication/no-bypass behavior and one healthy synthetic task
  only after the separately reviewed activation update;
- Cognito MFA and named-group evidence without tokens, codes, cookies, secrets,
  passwords, or personal recovery details;
- fixed `logan-synthetic` tenant and no live CAD/operational output;
- budget status, alert subscription, current actual/forecast, and cost owner;
- log retention, optional CloudTrail decision, RDS no-backup posture, and expiry;
- successful one-off allowlisted schema initializer, if separately run.

## Teardown sequence

Teardown requires separate human approval and supervision:

1. Stop pilot use and record final sanitized evidence.
2. Human removes or disables the Cloudflare `aws` application CNAME without
   changing nameservers or Hostinger DNS.
3. Destroy `lcdash-p1-logan-use1-foundation` only; verify the service, RDS,
   generated database secret, buckets, ECR, Cognito, ALB, VPC, budget, logs, and
   optional trail resources are gone. Data is intentionally unrecoverable.
4. Destroy `lcdash-p1-logan-use1-certificate` only after the foundation no longer
   references the certificate.
5. Human removes the ACM validation CNAME from Cloudflare after certificate
   deletion is confirmed.
6. Do not destroy `CDKToolkit` under this package. Review residual tagged costs
   and DNS independently.
