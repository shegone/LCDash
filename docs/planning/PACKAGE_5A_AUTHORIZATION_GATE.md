# Package 5A AWS Deployment Authorization Gate

This record separates authorization to create the Phase 1 cloud pilot from the
later authorization to connect that pilot to real read-only operational data.
Its default state is **NOT AUTHORIZED**. No AWS resource write or deployment
command is permitted until every required Phase 1 field is completed and the
Phase 1 decision is explicitly approved.

Store only non-secret evidence references here. Never paste credentials,
tokens, session data, protected records, or sensitive screenshots into this
repository.

## Recorded operator direction

The following scope decisions are recorded from the 2026-08-04 user direction.
They define the proposed pilot; they do not complete the authorization gate.

| Decision | Recorded value |
| --- | --- |
| AWS partition and region | Commercial `aws`, `us-east-1` only |
| Purpose | Independent secondary LCDash operational pilot |
| Initial monthly target | USD 200, with budgets, alerts, quotas, and usage controls |
| Availability posture | One working system; no redundancy, backups, or recovery in the initial pilot |
| Login and access path | Independent cloud login and cloud URL; no dependency on the on-premises login or path |
| Pilot application domain | `aws.logan911.com`; `logan911.com` is registered at Hostinger but authoritative DNS is delegated to Cloudflare. Exact ACM validation CNAME, issued-certificate ARN, Cloudflare application CNAME, external DNS verification, and named Cloudflare DNS operator evidence remain required. Nameserver changes, Hostinger DNS edits, and Route 53 resources are prohibited. |
| Functional scope | All practical read-only LCDash functions within the budget and documented provider limits |
| Managed providers | Amazon Bedrock, Transcribe, Polly, and Location |
| CAD authority | Existing CentralSquare CAD remains authoritative |
| Operational boundary | No CAD writes, subscriptions, acknowledgements, messages, paging, station-alert release, public warning, EMS delivery, or other operational output |
| Initial data state | Synthetic or disconnected only until the separate Phase 2 activation gate is approved |

## Phase 1 authorization record: lean cloud pilot foundation

Phase 1 may create only the independent pilot foundation described in
[`LEAN_SECONDARY_PILOT_PLAN.md`](LEAN_SECONDARY_PILOT_PLAN.md). It must not
contain a CentralSquare credential or connect to live CAD.

| Required field | Approved value or evidence reference |
| --- | --- |
| Gate ID and version | `LCDASH-AWS-P5A-PHASE1` / version 4 pre-write authorization |
| Named approver, title, and organization | Ted Sparks, Director, Logan 911 |
| Approval decision and date/time with time zone | Ted Sparks approved Phase 1 for 30 days on 2026-08-04T18:20:07-04:00 |
| Approval evidence location | Current Codex task transcript plus this durable non-secret gate record |
| AWS account ID | `862772137583`, verified by temporary SSO `sts get-caller-identity`; no session data recorded |
| Account classification | Commercial AWS, non-authoritative secondary pilot |
| Account purpose and owner | Ted Sparks, Director, Logan 911, approved use of this account for the independent Phase 1 pilot |
| AWS partition and permitted region | `aws` / `us-east-1` only |
| Resource and naming allowlist | `infrastructure/phase1_deployment_allowlist.json` and `docs/planning/PHASE1_DEPLOYMENT_PREFLIGHT.md`; [REQUIRED: human review/sign-off of exact synthesized inventory] |
| Explicitly prohibited resources/actions | Live CAD connectivity; CAD/vendor secrets; operational outputs or writes; webhook/subscription changes; access to `.227`, `.15`, `E:\Projects\LCDash`, backups, or existing credentials; resources outside `us-east-1`; unlisted actions |
| Phase 1 data scope | Synthetic fixtures, approved public data, and non-secret configuration only |
| MFA evidence location | [REQUIRED] |
| IAM Identity Center assignment evidence | [REQUIRED] |
| Independent pilot login design evidence | Cognito-managed login at `aws.logan911.com`; design: `docs/planning/PHASE1_AUTHENTICATION_MODEL.md`; offline synthesized assertions: `infrastructure/tests/test_cdk_template.py`; [REQUIRED after authorized deployment: sanitized MFA enrollment, named group assignment, unauthenticated denial, authenticated access, logout, and no-bypass evidence] |
| Billing visibility evidence location | [REQUIRED: human console evidence; no billing API or console access occurred during local preflight] |
| Budget owner, limit, alerts, and evidence | USD 200/month; proposed owner/subscriber Ted Sparks (`tedsparks@911logan.com`); forecast alert 80% / USD 160; actual stop alert 100% / USD 200; human review at actual 50% / USD 100; `PHASE1_DEPLOYMENT_PREFLIGHT.md`; [REQUIRED: confirm subscription and delivery] |
| Cost estimate and quota evidence | Local control plan only; [REQUIRED: dated AWS Pricing Calculator estimate and human quota review for exact services before deployment] |
| CloudTrail status, audit owner, and evidence | [REQUIRED] |
| Task-specific least-privilege deployment role | Proposed permission set `LCDashPhase1Deployment`, boundary `LCDashPhase1Boundary`, and local templates in `infrastructure/iam/`; review: `docs/planning/PHASE1_IAM_REVIEW.md`; [REQUIRED: IAM Access Analyzer results, human-created Identity Center assignment/role ARN, attached-policy evidence, and approval] |
| Permitted command scope | Preflight sequence is limited to separately approved bootstrap if required, then `lcdash-p1-logan-use1-certificate`, then `lcdash-p1-logan-use1-foundation`; no `--all`; `PHASE1_DEPLOYMENT_PREFLIGHT.md`; [REQUIRED: exact commands and parameter values approved] |
| First foundation service state | `PilotServiceDesiredCount=0` with `PilotImageDigest=NOT_PUBLISHED` is mandatory for initial foundation creation; service activation to `1` requires separately authorized image publication, an exact lowercase `sha256:` digest and scan evidence, reviewed update diff, and exact update command approval; mutable tags are prohibited |
| Current direct authorization | Ted Sparks directed: "yes lets build the cloud version leave the 227 and 15 alone"; recorded only as authorization for the exact time-bounded Phase 1 pre-write sequence below, not as image, activation, live-CAD, Phase 2, or protected-system authorization |
| Teardown owner and contact path | Ted Sparks, Director, Logan 911, `tedsparks@911logan.com` |
| Teardown procedure and evidence location | Reverse application-stack order and human Cloudflare DNS cleanup in `PHASE1_DEPLOYMENT_PREFLIGHT.md`; do not change nameservers or Hostinger DNS; never delete `CDKToolkit`; [REQUIRED: human review/approval] |
| Accepted pilot limitations | [REQUIRED: named acceptance of single-system outages and unrecoverable data/resource loss] |
| Approval window start and expiration | 2026-08-04T18:20:07-04:00 through 2026-09-03T18:20:07-04:00 |

## Mandatory Phase 1 conditions

- `tedsparks` / `AdministratorAccess` is temporary human bootstrap and
  oversight only. It is not a standing Kiro or Codex deployment identity.
- Deployment may use only the recorded task-specific least-privilege role,
  command allowlist, resource allowlist, region, budget, and approval window.
- Permanent IAM user access keys and long-lived exported credentials are
  prohibited. Use temporary IAM Identity Center sessions.
- The pilot must have its own authenticated cloud path. Client-supplied tenant
  identifiers must not alter the trusted tenant binding.
- Phase 1 contains no vendor secret, live CAD endpoint activation, protected
  operational data, on-premises network path, or operational output.
- Write/output capabilities remain absent or deny-by-default even when their
  inherited user-interface pages exist.
- The USD 200 amount is a target and control threshold, not proof that actual
  usage will remain below it. Deployment requires a reviewed estimate and
  alerts; exceeding the approved stop threshold requires human action.
- The initial pilot deliberately has no redundancy, backups, restore path, or
  disaster recovery. No document may describe it as production-ready,
  authoritative, highly available, or recoverable.

## Phase 1 checklist and decision

### Narrow pre-write authorization decision

The following decision authorizes only the recorded setup and dormant
foundation sequence during the existing approval window. Post-action evidence
remains pending and must be recorded after each action. It does not satisfy or
check the broader Phase 1 completion decision below.

- [x] **PHASE 1 PRE-WRITE SEQUENCE AUTHORIZED**
- [ ] **PHASE 1 PRE-WRITE SEQUENCE NOT AUTHORIZED**
- [x] IAM boundary and reviewed deployment-access setup may begin.
- [x] USD 200 budget alert configuration may be included in the reviewed foundation.
- [x] Constrained CDK bootstrap and certificate-request stack may proceed only in documented order.
- [x] Certificate deployment stops for manual Cloudflare DNS validation and `ISSUED` evidence; nameserver changes and Hostinger DNS edits are prohibited.
- [x] Foundation may use only desired count `0` and image value `NOT_PUBLISHED`.
- [ ] Image build or push authorized.
- [ ] Desired count `1` or ECS activation authorized.
- [ ] Live CAD or Phase 2 authorized.
- [ ] Access to production `.227`, PC `.15`, credentials, backups, or operational outputs authorized.

Post-action evidence state: **PENDING**.

### Broader Phase 1 completion decision

- [ ] Account, owner, partition, and `us-east-1` scope independently verified.
- [ ] MFA, IAM Identity Center, and independent application login reviewed.
- [ ] Billing access, USD 200 target, alerts, quotas, estimate, and owner verified.
- [ ] CloudTrail coverage and audit evidence location verified.
- [ ] Exact single-system resource/naming allowlist reviewed.
- [ ] Initial foundation desired count zero and separate image/activation hold reviewed.
- [ ] Task-specific least-privilege deployment role and commands reviewed.
- [ ] Synthetic/disconnected-only data boundary accepted.
- [ ] No-redundancy/no-backup/no-recovery limitations explicitly accepted.
- [ ] Teardown owner and procedure reviewed.
- [ ] Approval start and expiration recorded and currently valid.
- [ ] No-production/no-live-CAD/no-output boundary accepted.

- [ ] **PHASE 1 AUTHORIZED** for only the recorded scope and approval window.
- [ ] **PHASE 1 NOT AUTHORIZED**; no AWS write or deployment command is permitted.

Approver name: Ted Sparks, Director, Logan 911

Approver signature or authoritative approval reference: Ted Sparks approval in the current Codex task transcript

Authorization date/time and expiration: 2026-08-04T18:20:07-04:00 through 2026-09-03T18:20:07-04:00

Any missing, ambiguous, expired, or contradictory Phase 1 field leaves Phase 1
**NOT AUTHORIZED**. `cdk bootstrap`, `cdk deploy`, CloudFormation changes, and
all AWS create/update/delete actions are writes and require explicit inclusion.

## Phase 2 gate: real read-only operational-data activation

Phase 1 approval does **not** authorize Phase 2. The deployed pilot must remain
synthetic or visibly disconnected until a second authorization record is
completed. Phase 2 may enable bounded read-only polling only; it never permits
an operational write or output.

### Conditional approver direction

On 2026-08-04, Ted Sparks, Director, Logan 911, directed the project team to
proceed with Phase 2 **when appropriate**. This is recorded as conditional
authority to complete the gate and activate only after every requirement below
has supporting evidence and a reviewed read-only scope. It does not waive
vendor permission, data-handling controls, the time-bounded activation record,
or the standing prohibition on operational writes and outputs.

The Phase 2 record must include all of the following:

- [ ] Pre-activation historic snapshot and final delta-catchup contract reviewed:
  [`PHASE2_DATA_MIGRATION_PLAN.md`](PHASE2_DATA_MIGRATION_PLAN.md) and
  `infrastructure/phase2_data_migration_contract.json`. This planning reference
  is not migration or activation authorization.

- [ ] Named approver and time-bounded activation window.
- [ ] Written or operator-recorded evidence that cloud-hosted concurrent
  read-only CentralSquare access is permitted.
- [ ] Verified vendor/API rate limits, egress/IP requirements, endpoint
  allowlist, and read-only operation allowlist.
- [ ] Vendor-scoped read-only credential evidence where supported.
- [ ] Authorized-human procedure for direct secret entry into the exact
  tenant-scoped Secrets Manager secret; no AI agent receives the values.
- [ ] Task-role evidence allowing access only to that secret reference.
- [ ] Field-minimization, tenant-isolation, reconciliation, deduplication,
  timeout, redaction, and no-raw-payload-log test evidence.
- [ ] Approved operational-data classes, retention, audit owner, and legal or
  policy evidence location.
- [ ] Conservative polling limits, stop conditions, spend impact, and named
  operator able to disable the feed.
- [ ] Aggregate-only parity comparison procedure that does not transfer raw
  records from `.227`.
- [ ] Confirmation that existing CAD remains authoritative and `.227` remains
  the sole webhook and operational-output owner.
- [ ] Confirmation that CAD update, message, acknowledgement, subscription,
  paging, station-alert, EMS delivery, and public-warning capabilities remain
  denied and unprovisioned.

- [ ] **PHASE 2 ACTIVATION AUTHORIZED** for the recorded read-only scope.
- [ ] **PHASE 2 NOT AUTHORIZED**; the pilot remains synthetic/disconnected.

Until every Phase 2 field is supported by hosted/user evidence and the Phase 2
decision is explicitly approved, no credential entry, live endpoint test, or
real operational-data access is permitted.

## References

- [Lean secondary pilot plan](LEAN_SECONDARY_PILOT_PLAN.md)
- [AWS workspace boundary](../../AWS_WORKSPACE.md)
- [Durable AWS move handoff](../../handoffs/AWS_MOVE_THREAD_HANDOFF.md)
- [Package 5A task](../../.kiro/specs/aws-multicounty-platform/tasks.md)
- [Temporary SSO operator runbook](AWS_SSO_OPERATOR_RUNBOOK.md)
