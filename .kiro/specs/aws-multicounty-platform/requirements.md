# Requirements: AWS Multi-County LCDash Platform

## R1 - Separate AWS program

When AWS development begins, the system shall use a separate worktree, branch,
deployment pipeline, AWS environment, data stores, and secrets so production
`.227` remains unchanged and available.

Acceptance criteria:

- No AWS task connects to or deploys to `.227`.
- AWS infrastructure is created only from the AWS branch after review.
- The first environment uses synthetic data until a live-read approval gate.

## R2 - County template, not county forks

When a county is onboarded, the platform shall generate its deployment from a
versioned county profile and reusable infrastructure constructs without
copying the application into a county-specific code fork.

The profile shall define non-secret branding, timezone, CAD provider,
capabilities, agencies, unit/status mappings, GIS sources, identity federation,
retention, AI policy, voice profile, modules, alert permissions, and region.

## R3 - Replaceable CAD providers

The application shall define a versioned CAD provider protocol for token
acquisition, health, call search/detail, unit search, event ingestion, and
declared optional write capabilities.

All existing CentralSquare behavior shall move behind a CentralSquare adapter
without changing normalized outputs. A synthetic adapter shall support tests
and demonstrations. Future CAD adapters shall be implementable without
changing dashboard modules.

## R4 - Tenant isolation

Every county shall have an immutable tenant identifier and isolated secrets,
encryption, data, queues, logs, backups, and deployment boundaries. The default
production topology shall be a county silo managed by a shared control plane.

An authenticated user shall never be able to access another county merely by
changing a URL, request field, token claim, object key, or database query.

Tenant context shall be derived from trusted identity and deployment bindings,
not from a client-supplied county identifier. A deny-by-default authorization
service shall enforce tenant, role, module, action, data class, and resource at
the API, repository, object-storage, queue, report, cache, and AI-tool layers.

## R5 - CentralSquare credentials

When Logan County live-read testing is approved, the AWS Logan cell shall use
the existing CentralSquare API credential pair from a Logan-specific Secrets
Manager secret. Secret values shall be entered outside Kiro and source control.

Concurrent use, rate limits, source-IP expectations, hosting permission, and
webhook behavior shall be verified before activation. The first activation
shall be bounded polling with conservative rate limits and no subscription or
write operations.

## R6 - Functional parity

The AWS Logan sandbox shall support the approved on-prem feature set through
provider interfaces: supervisor dashboard, active calls, call detail, units,
analytics, reports, GIS, MAE, JACK, knowledge retrieval, and optional voice.

Station alerts, EMS delivery, CAD messages, webhook registration, and other
operational outputs shall be disabled until separately accepted.

## R7 - Managed AWS improvements

The design shall evaluate and use appropriate managed services for container
hosting, database, object storage, AI, retrieval, speech, maps, identity,
authorization, configuration, queues, orchestration, observability, security,
backup, and delivery. Every managed dependency shall remain behind a provider
or capability boundary where regional or county requirements can differ.

## R8 - Reliable delivery

Every application release shall be built from GitHub, tested, scanned, stored
in ECR, deployed through infrastructure as code, health checked, and capable of
automatic rollback. Long-lived AWS keys shall not be stored in GitHub.

## R9 - Security and audit

The platform shall use least-privilege IAM roles, KMS encryption, private
subnets, controlled egress, WAF, federated MFA, tenant-aware authorization,
CloudTrail, application audit events, vulnerability scanning, and alarms.

Logs shall not contain credentials, raw CAD payloads, caller details, medical
narratives, or model prompts containing protected incident data unless a later
approved retention policy explicitly requires a minimized field.

## R10 - AI governance

AI inference shall use a replaceable provider. Bedrock models, Knowledge Bases,
Guardrails, and AgentCore shall be adopted only where supported and useful.
Tools shall be deterministic, allowlisted, tenant-bound, read-only by default,
audited, and subject to schema validation.

AI output shall never directly change CAD or release an operational alert.

## R11 - Region and partition portability

Infrastructure shall synthesize for commercial AWS and AWS GovCloud (US)
without hardcoded partition ARNs. Region capability checks shall select
supported alternatives when a managed service, model, or voice is unavailable.

Commercial sandbox success shall not be presented as GovCloud readiness.
A versioned capability registry shall be tested during CDK synthesis for every
target region and partition. Unsupported capabilities must select an approved
fallback or fail synthesis; they may not silently disappear at deployment.

## R12 - Recovery and continuity

Each county shall have multi-AZ service design, database point-in-time
recovery, versioned object storage, encrypted backups, restore procedures,
failure alarms, and tested recovery objectives. Cross-region DR is a later
profile option, not an assumption.

## R13 - Cost and quotas

Before deployment, Kiro shall produce an itemized monthly estimate, quota list,
and adjustable cost controls for sandbox, single-county production, and a
five-county template. Cost allocation tags shall include tenant, environment,
module, owner, and data classification.

## R14 - Controlled Kiro execution

Kiro shall implement one bounded task at a time in this worktree, run required
checks, and produce a non-secret handoff. AWS write access and deployments
require a task-specific approval and restricted role.
