# LCDash Phase 1 infrastructure

This directory defines the synthetic/disconnected Phase 1 pilot only. It is
not deployment authorization. Package 5A must be completed before `cdk
bootstrap`, `cdk deploy`, or any other AWS write.

## Deployed pilot status — 2026-08-05

The separately authorized Logan synthetic pilot is now deployed in account
`862772137583`, region `us-east-1`. The foundation stack is `UPDATE_COMPLETE`;
one ECS task is healthy at desired/running/pending `1/1/0`; the private database
has its 52-statement allowlisted Phase 1 schema; and the ALB endpoint is
`https://aws.logan911.com`. The deployed experimental Alpine image is pinned to
`sha256:fd6777aa337d845996a3063340ceeb7cb05cc5123ba2831240be7e78d8fabb10`
and its ECR basic scan reported Critical/High/Medium `0/0/0`.

The P0 synthetic parity release uses source manifest
`28bd5faa7c8f2a09e4977c749418d644b35b1a41ab5b6ec550ef24fef40c58e4`.
Its guarded Alpine CodeBuild, local container health check, immutable ECR push,
scan, CloudFormation update, ECS rollout, ALB target check, and recent log review
all passed. Dashboard, Units, Map, and Heatmap share an explicit
`synthetic-disconnected` service boundary that returns safe empty results before
legacy CAD initialization; existing on-premises CAD read behavior is preserved.

Unauthenticated requests redirect to Cognito. The sole current account is the
read-only reviewer `tedsparks@911logan.com`, with required software-token MFA.
For first login, open `https://aws.logan911.com`, follow the Cognito invitation,
set a permanent password, and enroll an authenticator app. Never place a
password or MFA seed in chat, logs, Git, or documentation.

After signing in, verify the Dashboard, Units, Map, and Heatmap pages load and
show the expected synthetic/disconnected empty state without a server error.
Confirm ordinary navigation works and that no live-CAD or operational content is
present. Share only non-secret findings; never share credentials, MFA seeds,
cookies, authorization codes, or session details.

No source-data import has occurred. Phase 2 remains gated by the exact source
read-only, classification, retention, encryption, pseudonymization, integrity,
freshness, and rollback approvals in `docs/planning/PHASE2_DATA_MIGRATION_PLAN.md`.
Any later CAD access is inquiry-only and separately authorized. CAD writes,
webhooks, subscriptions, acknowledgements, alert release, EMS delivery, station
alerts, paging, public warnings, and every other operational output remain
strictly prohibited.

The stack is deliberately one working system:

- one Fargate web task behind one public HTTPS ALB;
- one single-AZ PostgreSQL RDS instance;
- no NAT gateway, autoscaling, standby task, database replica, backup, final
  snapshot, webhook, collector, CAD secret, or operational-output service;
- ALB-enforced Cognito login, private tenant content, short-retention logs, a USD 200
  budget, and optional CloudTrail when account-level coverage is absent;
- Bedrock, Transcribe, Polly, and Amazon Location permissions bounded to the
  application task role.

`logan911.com` is registered at Hostinger, but its authoritative nameservers
delegate DNS to Cloudflare. The pilot hostname is fixed to
`aws.logan911.com`; both the ACM validation CNAME and later application CNAME
must be managed in Cloudflare. Do not change nameservers and do not add or edit
DNS records in Hostinger. Neither stack creates a Route 53 hosted zone or DNS
record, and neither performs an account or DNS lookup.

Certificate and application deployment are deliberately separate:

- `lcdash-p1-logan-use1-certificate` requests one ACM public certificate using
  DNS validation. Its deployment waits for a human to copy ACM's validation
  CNAME name and value into authoritative Cloudflare DNS and confirm that ACM
  reports `ISSUED`.
- `lcdash-p1-logan-use1-foundation` accepts only the already-issued
  `CertificateArn`. It therefore does not create a certificate and cannot wait
  on certificate DNS validation. It outputs the ALB hostname that a human must
  later enter in Cloudflare as the `aws` application CNAME.

The foundation callback and logout URLs are fixed to `aws.logan911.com`.
Budget owner/subscriber and approved Bedrock resource ARNs remain
CloudFormation parameters.

The Phase 1 application image contract is `Dockerfile.aws-pilot` at the
repository root. It uses the fixed non-root identity `10001:10001`, exposes
port 8000, checks only `/health`, and packages only the application, Logan
synthetic profile/schema, database SQL, static files, and templates. The ECS
task keeps the image root filesystem read-only and mounts task-local ephemeral
storage at `/tmp` for temporary, home, and cache writes. The image does not
copy on-premises deployment scripts, documentation, environment files, CAD
credentials, backup material, or operational-output configuration.

The foundation parameter `PilotServiceDesiredCount` accepts only `0` or `1`
and defaults to `0`. Initial foundation creation must explicitly retain zero so
the ECR repository exists without ECS trying to pull an unpublished image. A
later value of one requires separate authorization after the reviewed image is
published and its immutable digest and scan evidence are recorded. This stack
does not build or publish the image, and it does not authorize activation.
`PilotImageDigest` defaults to the dormant `NOT_PUBLISHED` placeholder. A
CloudFormation rule prevents that placeholder from being used with desired
count one, and the only activation form accepted is `sha256:` followed by 64
lowercase hexadecimal characters. The task definition joins the ECR repository
URI to that digest with `@`; mutable tags are not an activation path.

For the pilot database, ECS injects `LCDASH_DATABASE_USERNAME` and
`LCDASH_DATABASE_PASSWORD` from the generated RDS secret through the task
execution role. The RDS endpoint, port, and database name are non-secret task
environment values. The application assembles the PostgreSQL URL in memory,
redacts it from settings representations, and fails startup if any pilot value
is missing or if legacy `DATABASE_URL` is supplied. `DATABASE_URL` remains
available only outside `synthetic-disconnected` mode for existing local
development. The application task role has no Secrets Manager read permission.

Schema creation is also separate from web startup. The pilot image contains the
independently runnable module `app.tools.phase1_schema_initializer`, which reads
the same fail-closed database settings and validates every SQL statement against
an explicit Phase 1 object allowlist before opening a connection. It includes
synthetic analytics, MAE/JACK audit and evaluation, and approved knowledge or
reference-document objects. It excludes collector sync state, realtime events,
webhooks, alerting, EMS, paging, subscriptions, CAD messages, acknowledgements,
and operational-output objects.

After separate deployment authorization, an operator may use the web task
definition for one ECS `RunTask` with the container command overridden to:

```text
python -m app.tools.phase1_schema_initializer
```

That future task must complete successfully before the service is accepted. Do
not add this command to the web container startup, and do not run the legacy
analytics or realtime initialization scripts for Phase 1.

The ALB `authenticate-cognito` action uses the AWS-supported confidential
client pattern. Cognito generates a client secret because the load balancer,
not browser JavaScript or the FastAPI application, performs the server-side
authorization-code exchange. Authorization code is the only OAuth grant;
implicit and client-credentials flows are absent. The secret is managed by
Cognito/ALB, is not put in Secrets Manager or the task environment, and is not
an application, CAD, vendor, or tenant secret. The listener authenticates before
forwarding and uses a one-hour session. Cognito requires TOTP MFA, a 14-character
password with every character class, verified-email-only recovery, 15-minute
access/ID tokens, one-day rotating refresh tokens, and revocation.

`lcdash-pilot-viewer` and `lcdash-pilot-reviewer` are named application groups
without IAM roles. No identity pool or browser AWS credentials exist. The task
has one fixed `logan-synthetic` tenant binding; request values, group names, and
Cognito claims cannot select another tenant. The full design and required
acceptance evidence are in `docs/planning/PHASE1_AUTHENTICATION_MODEL.md`.

Both S3 buckets use `DESTROY` plus automatic object deletion, and ECR uses
`DESTROY` plus empty-on-delete so the authorized teardown can complete. CDK
implements S3 automatic deletion with a generated custom-resource Lambda. That
Lambda is teardown plumbing only: it is not an application endpoint, worker,
provider, operational output, or public-safety function.

## Offline checks

These checks need only the Python standard library:

```powershell
python -m unittest discover infrastructure/tests -v
python -m py_compile infrastructure/app.py infrastructure/lcdash_pilot/config.py infrastructure/lcdash_pilot/certificate_stack.py infrastructure/lcdash_pilot/foundation_stack.py
```

The CDK template assertion module skips automatically when `aws-cdk-lib` is
not installed. After dependency installation is separately approved, run the
same test command and then synthesize locally with explicit dummy context:

```powershell
cd infrastructure
cdk synth --context account=111111111111 --context region=us-east-1
```

Do not use `cdk deploy`, `cdk bootstrap`, or an actual account identifier until
the authorization gate records the exact role, account, commands, stack names,
and approval window.

## Later operator sequence after authorization

These are review instructions, not authorization to run them now:

1. Confirm Package 5A records the approved account, role, region, commands,
   stack names, approval window, Cloudflare DNS operator, and rollback owner.
2. Synthesize and review both stacks locally. Deploy only
   `lcdash-p1-logan-use1-certificate` first.
3. In ACM, copy the exact DNS validation CNAME name and value. In authoritative
   Cloudflare DNS for `logan911.com`, add that CNAME exactly. Keep it DNS-only.
   Do not change nameservers, edit Hostinger DNS, create an A record, redirect,
   or Route 53 zone. Independently verify the public CNAME and wait
   until ACM reports the certificate as `ISSUED`.
4. Record the issued certificate ARN. Deploy
   `lcdash-p1-logan-use1-foundation` with that ARN supplied as the
   `CertificateArn` parameter. A pending or wrong certificate must stop review;
   do not bypass HTTPS.
5. Read the legacy-named foundation output `HostingerApplicationCnameTarget`.
   In Cloudflare, create a DNS-only `aws` CNAME pointing to that exact ALB DNS
   hostname. Independently
   verify public DNS, HTTPS hostname/certificate matching, and Cognito callback
   behavior before pilot acceptance.
6. Keep both CNAME records under human DNS change control. Teardown must remove
   or update the application CNAME deliberately; CDK will not alter Cloudflare.
