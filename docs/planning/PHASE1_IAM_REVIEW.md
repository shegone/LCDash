# Phase 1 IAM policy-template review

Status: **LOCAL TEMPLATES ONLY - NOT CREATED OR ATTACHED**

On 2026-08-04, IAM Access Analyzer validation of
`LCDashPhase1Boundary.json` initially returned three `INVALID_ACTION` errors for
nonexistent Budgets action names. The template was narrowed to the valid
`budgets:ViewBudget` and `budgets:ModifyBudget` permission families and then
revalidated. The deployment policy and trust/assignment still require their
own review and evidence before use.
The same validation reported that tag conditions are unsupported for batch
secret reads, so `BatchGetSecretValue` is now denied unconditionally in its own
statement; this strengthens rather than broadens the boundary.

These files are review inputs, not authority:

- `infrastructure/iam/LCDashPhase1Boundary.json`
- `infrastructure/iam/LCDashPhase1DeploymentRolePolicy.json`
- `infrastructure/iam/LCDashPhase1DeploymentTrustModel.json`

No IAM role, permission set, policy, assignment, access key, or AWS resource was
created by this work. A human IAM administrator must review these documents,
run IAM Access Analyzer policy validation in the intended account, resolve all
findings, and separately authorize any creation or attachment.

## Intended layered model

1. A temporary IAM Identity Center assignment provides the operator session.
   The proposed permission set is `LCDashPhase1Deployment`, with MFA, a maximum
   one-hour session, no access keys, no service principal, and no cross-account
   trust. The final AWS-reserved role ARN and assigned human/group are unknown
   until a human creates and assigns the permission set.
2. `LCDashPhase1DeploymentRolePolicy.json` permits the operator to act only on
   `CDKToolkit`, `lcdash-p1-logan-use1-certificate`, and
   `lcdash-p1-logan-use1-foundation`; assume only the account/region-specific
   CDK roles; and, when separately authorized, create the default-qualifier
   bootstrap assets.
3. `LCDashPhase1Boundary.json` is the proposed maximum-permission boundary for
   the CDK bootstrap roles, CloudFormation execution role, and Phase 1-created
   roles. A boundary grants no access by itself. It allows only services used by
   the synthesized inventory and denies other regions, Route 53/DNS, protected
   remote-management paths, IAM users/access keys, account/organization/billing
   administration, operational messaging, and arbitrary `iam:PassRole`.
4. Exact synth, diff, tags, names, resource counts, and deployment order remain
   mandatory controls because IAM cannot express every CloudFormation resource
   property or pre-creation physical name.

The policy name `LCDashPhase1Boundary` is now fixed in the local preflight
allowlist, but that does not approve or create it.

## Required human trust configuration

The operator role must come from an IAM Identity Center permission-set
assignment in account `862772137583`; do not hand-author a broadly trusted IAM
role. Record only sanitized references to the permission set, assignment,
AWS-reserved role ARN, MFA/session configuration, attached policy, and boundary.
No Kiro/Codex principal, public principal, external account, application service,
or long-lived IAM user may assume the operator role.

The final principal ARN is deliberately `null` in the trust-model JSON. It must
not be guessed. A human fills the evidence reference only after Identity Center
creates the account-specific AWS-reserved role.

## Hard denies and protected scope

Both policy layers deny or omit:

- all regions other than `us-east-1` where `aws:RequestedRegion` applies;
- Route 53, Route 53 Domains, Direct Connect, Global Accelerator, Network
  Manager, remote SSM sessions/commands, API invocation, and operational
  messaging/email actions;
- IAM users, access keys, login profiles, groups, SAML/OIDC providers, arbitrary
  roles/policies, and arbitrary role passing;
- Organizations, account administration, payment, billing administration,
  purchases, CUR creation, and support-plan actions;
- `.227`, PC `.15`, live CAD, credentials, backups, DNS, and operational outputs.

Filesystem and on-premises systems are outside IAM's control. Their protection
also depends on the standing repository/operator boundary and absence of network
paths or credentials.

## Residual bootstrap privileges requiring explicit acceptance

Modern CDK bootstrap is inherently privileged. Even with these templates, a
human must accept and monitor the following residual capabilities:

- CloudFormation can create/update/delete resources inside the three named
  stacks and invokes service APIs through its execution role.
- The boundary contains service-level wildcards for the allowlisted regional
  services because several create/list/tag APIs do not support useful resource
  ARNs before creation. This can constrain service and region, but not every
  property, size, subnet, endpoint, or physical name.
- Unrelated S3 buckets are explicitly denied. Reading Secrets Manager values is
  denied unless the secret carries `Project=LCDash-AWS`; the synthesized RDS
  secret has that fixed stack tag. Human review must verify this tag remains on
  the exact generated secret and that no unrelated secret receives that tag.
- Bootstrap creates IAM roles and inline/managed policies under
  `cdk-hnb659fds-*`, an S3 asset bucket, an ECR asset repository, and the SSM
  bootstrap-version parameter. The operator can pass only the named CDK/Phase 1
  role patterns to CloudFormation, ECS tasks, and Lambda.
- CloudFormation-generated provider resources for S3 auto-delete require Lambda
  and IAM operations. They are bounded by account/region, stack review, role
  prefixes, and the permissions boundary, but generated names are not fully
  predictable before synth.
- Some global services do not carry `aws:RequestedRegion`. They require explicit
  action/resource denies and human review; the region deny is not sufficient by
  itself.
- `iam:CreateServiceLinkedRole` is intentionally absent. If ECS, ELB, RDS, or
  another allowlisted service lacks its required service-linked role, deployment
  must stop; a human must review and separately authorize that account-level IAM
  change rather than broadening the template during deployment.
- The boundary denies removing itself from CDK/Phase 1 roles. Application
  teardown may therefore require a separately authorized IAM administrator for
  final role cleanup; the deployment operator must not bypass the boundary.
- IAM policies cannot restrict Hostinger, local machines, live CAD, or a person
  using a separate administrator session. Organizational controls and operator
  procedure remain necessary.

If IAM Access Analyzer reports an error, if bootstrap requires an unlisted
action/resource, or if CDK proposes a different role, qualifier, stack, type,
name, region, or tag, stop. Do not broaden a wildcard merely to make deployment
work. Update this review package and obtain a new human decision.

## Later human validation sequence

1. Confirm the account/region, current CDK bootstrap state, organization SCPs,
   and whether the named boundary already exists.
2. Validate both IAM policy JSON documents with IAM Access Analyzer without
   exposing session data; record sanitized findings and resolutions.
3. Compare every allowed service/action with the current synthesized inventory
   and the current reviewed CDK bootstrap template/version.
4. Create or update the boundary and Identity Center permission set only under
   separate IAM-change authorization. Attach the boundary to every CDK-created
   role through the approved bootstrap option.
5. Use the temporary human assignment for exact stack-scoped synth/diff/deploy;
   never give the role to an agent or service principal.
6. After the approval window, remove the assignment. Retain or remove the policy
   objects only under separate IAM lifecycle approval; never delete CDKToolkit
   as part of application teardown.
