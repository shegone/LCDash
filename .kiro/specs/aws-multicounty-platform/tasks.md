# Tasks: AWS Multi-County LCDash Platform

Kiro works only the task explicitly assigned by hosted Codex. A checked task
means its code, tests, documentation, diff review, and handoff were accepted.

## Package 0 - Workspace and architecture baseline

- [x] Create the separate AWS worktree and branch.
- [x] Add AWS product, technology, structure, and security steering.
- [x] Add requirements, design, and phased tasks.
- [x] Kiro review: analyze these artifacts for contradictions, missing county
  isolation controls, unsupported AWS assumptions, and tasks that are too
  large. Write findings only; do not edit application code or use AWS tools.
- [x] Hosted Codex adjudicates Kiro findings and records architecture decision
  records for CDK, tenancy, database isolation, identity, and deployment.

Acceptance: planning artifacts are internally consistent and contain no
credentials or AWS writes.

## Package 1A - Inventory and characterization baseline

- [x] Inventory existing direct imports and calls to CentralSquare, Ollama,
  knowledge, speech, GIS, database, and identity services.
- [x] Add characterization tests for current normalized calls, units,
  analytics, MAE/JACK tools, and error handling.

Acceptance: the dependency inventory is complete, existing behavior is captured
by synthetic characterization tests, and no live service is contacted.

## Package 1B - Provider protocols and synthetic providers

- [x] Define versioned `TenantContext`, `CountyProfile`, `CadProvider`,
  `InferenceProvider`, `RetrievalProvider`, and speech provider protocols.
- [x] Add synthetic providers and provider contract tests.

Implementation completed by Kiro and accepted by hosted Codex on 2026-08-04.

Acceptance: contract tests cover normalization, capability denial, tenant
binding, timeouts, pagination, rate limits, redaction, and audit behavior.

## Package 1C - CentralSquare adapter migration

- [x] Move CentralSquare access behind its adapter without changing existing
  behavior or the on-prem Docker configuration inherited in this branch.

Implementation completed by Kiro and accepted by hosted Codex on 2026-08-04.

Acceptance: full existing suite plus new contract tests pass; no live service
is contacted.

## Package 2 - County profiles and feature boundaries

- [ ] Define and validate the non-secret county-profile JSON schema.
- [ ] Add Logan synthetic and second-county synthetic fixtures.
- [ ] Move county-specific agency, unit, pronunciation, timezone, branding,
  GIS, and module settings out of shared logic where safe.
- [ ] Add deny-by-default module capability and operational-output controls.
- [ ] Add cross-tenant negative tests for APIs, database access, files, queues,
  AI tools, reports, and caches.

Acceptance: two synthetic counties run from one codebase and cross-tenant
tests fail closed.

## Package 3A - CDK foundation and capability registry, synth only

- [ ] Initialize Python CDK under `infrastructure/` with pinned dependencies.
- [ ] Define the versioned commercial/GovCloud region-capability registry and
  approved fallbacks; fail synthesis for unsupported required capabilities.
- [ ] Implement the partition-aware foundation stack with assertion, policy,
  tagging, budget, quota, and removal-policy tests.

Acceptance: foundation synthesis passes for `us-east-1` and a GovCloud context;
no credentials, account IDs, or deployed resources.

## Package 3B - County data and application stacks, synth only

- [ ] Implement county-data and county-app stacks one at a time.
- [ ] Require CDK assertions and policy/security checks for each stack before
  beginning the next stack.

Acceptance: county stacks synthesize independently and enforce county-specific
keys, secrets, storage, database, queues, logs, and task roles.

## Package 3C - Identity and authorization stack, synth only

- [ ] Implement federation and immutable tenant-context bindings.
- [ ] Implement the deny-by-default authorization provider boundary and
  cross-tenant policy tests.

Acceptance: client-supplied tenant identifiers cannot change authorization and
all cross-tenant tests fail closed.

## Package 3D - Observability, delivery, optional AI, and cost, synth only

- [ ] Implement observability, delivery, and optional AI stacks one at a time
  with assertion and policy gates between stacks.
- [ ] Produce sandbox, single-county production, and five-county cost models.
- [ ] Run tests and `cdk synth`; do not deploy.

Acceptance: deterministic synthesis for `us-east-1` and a GovCloud context;
no credentials, account IDs, or deployed resources.

## Package 4 - CI/CD and supply chain

- [ ] Define exact GitHub OIDC trust conditions and protected environments.
- [ ] Add CI for Python tests, JavaScript checks, CDK tests/synth, dependency
  review, secret scanning, SBOM, container build, and image scanning.
- [ ] Add CodePipeline/CodeBuild/ECR/ECS blue-green design and smoke tests.
- [ ] Prove automatic rollback with a synthetic failing release.

Acceptance: pipeline templates pass policy review; enabling AWS connections
and deployment remains a separate approval.

## Package 5A - Human deployment authorization gate

- [ ] Obtain explicit approval for AWS writes and a sandbox deployment role.
- [ ] Confirm billing visibility, budgets, MFA, CloudTrail, and account purpose.
- [ ] Record the named approver, account classification, permitted resources,
  budget owner, rollback owner, evidence locations, and expiration of approval.

Acceptance: all approval artifacts exist before any deployment command is
permitted. Kiro remains unable to deploy without a separately assigned role.

## Package 5B - Synthetic AWS sandbox deployment

- [ ] Deploy a synthetic Logan county cell with no CentralSquare secret.

Acceptance: deployment uses synthetic data and the approved, time-bounded role;
no vendor secret, webhook, live CAD, or operational output exists.

## Package 5C - Synthetic sandbox verification

- [ ] Verify networking, identity, tenant isolation, autoscaling, alarms,
  backup, restore, and rollback.

Acceptance: AWS sandbox passes synthetic acceptance and cost alarms; `.227`
is unchanged.

## Package 6 - Managed provider evaluations

- [ ] Bedrock model quality/latency/cost evaluation using synthetic prompts.
- [ ] Bedrock retrieval evaluation against approved synthetic documents.
- [ ] AgentCore gateway/runtime/identity/observability feasibility review.
- [ ] Transcribe streaming evaluation with synthetic supervisor audio.
- [ ] Polly streaming voice evaluation, including pronunciation rules.
- [ ] Amazon Location base-map/geocode/route evaluation using synthetic
  addresses and approved public GIS only.

Acceptance: provider scorecards select defaults and fallbacks by region; no
protected data is sent during evaluation.

## Package 7 - Logan CentralSquare read-only activation

- [ ] Obtain written or operator-recorded confirmation of concurrent credential
  use, API rate limits, cloud-hosting permission, egress/IP expectations, and
  multiple-subscription behavior.
- [ ] Authorized human enters the existing credential values directly into the
  Logan Secrets Manager secret; Kiro and Codex never receive them.
- [ ] Enable bounded polling only with conservative limits and full audit.
- [ ] Verify field minimization, deduplication, reconciliation, token handling,
  and no raw-payload logs.
- [ ] Compare aggregate results with `.227` without transferring raw records.

Acceptance: read-only parity is proven and `.227` remains the sole operational
webhook/output owner.

## Package 8 - Multi-county template acceptance

- [ ] Provision a second synthetic county cell from configuration only.
- [ ] Demonstrate different CAD adapter, branding, roles, GIS, retention,
  modules, model policy, and voice profile.
- [ ] Exercise onboarding, upgrade, rollback, backup, restore, offboarding,
  cost allocation, and tenant-isolation evidence.

Acceptance: no application fork; county-specific resources and data remain
isolated while the control plane reports metadata-only health.

## Package 9 - GovCloud and production readiness

- [ ] Produce a current service/model/region gap matrix for GovCloud East and
  West, including Bedrock, AgentCore, voices, identity, security, CI/CD, and
  third-party connectivity.
- [ ] Validate contractual, CJIS, records-retention, audit, incident-response,
  support, RTO/RPO, procurement, and data-residency requirements.
- [ ] Run Well-Architected, threat-model, penetration-test, disaster-recovery,
  and operational acceptance reviews.
- [ ] Create a single-writer/fencing and cutover design before enabling any
  operational output.

Acceptance: production authorization is documented. No production promotion
is implied by completing the commercial sandbox.
