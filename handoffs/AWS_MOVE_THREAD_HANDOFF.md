# AWS Move Thread Handoff

## Objective

Build a separate, improved, fully AWS-hosted LCDash platform that can serve as
a modular template for additional counties and different CAD vendors. Use Kiro
for bounded implementation packages while hosted Codex acts as architecture,
security, review, and deployment gatekeeper.

## Workspace

- Repository worktree: `E:\Projects\LCDash-AWS`
- Branch: `aws/modular-county-platform`
- Planning baseline commit: `fc97da6`
- Source production repository: `E:\Projects\LCDash`
- Production server: `.227`

## Absolute boundaries

- Leave `.227`, `.15`, the on-prem deployment branch, database, backups,
  credentials, and operational services unchanged.
- Do not retrieve, display, copy, log, commit, or place secret values in model
  prompts or handoffs.
- Existing CentralSquare credentials may be reused only at the later approved
  read-only activation gate, through direct authorized entry into a Logan-
  specific AWS Secrets Manager secret.
- Do not create or change AWS resources until the human deployment-authorization
  package is complete and a task-specific role is approved.
- Begin with synthetic data. Do not register another CAD webhook or enable CAD
  writes, EMS delivery, station alerts, paging, or any operational output.

## Completed

- Created the separate AWS worktree and branch.
- Added Kiro product, technical, structure, and security steering.
- Added requirements, architecture design, and phased work packages.
- Kiro completed Package 0 architecture review through a concise chat pass.
- Hosted Codex adjudicated the findings.
- Added ADRs covering county-cell tenancy, tenant authorization, CAD read-only
  enforcement, county data isolation, webhook single-writer behavior, and the
  AWS region/partition capability registry.
- Validated the planning files for formatting and secret-like values.
- Committed the planning baseline as `fc97da6`.
- No AWS resources, deployment, push, live CAD, `.227`, or `.15` access occurred.

## Architecture direction

- Shared metadata-only control plane with siloed county application cells.
- Configuration and provider interfaces instead of county code forks.
- One county workload isolation boundary where required, including its own
  secrets, KMS keys, database, storage, queues, logs, and backups.
- Replaceable CAD, inference, retrieval, speech, GIS, identity, and
  authorization providers.
- Commercial AWS sandbox first, with partition-aware synthesis and explicit
  AWS GovCloud capability/fallback checks from the beginning.
- AI remains advisory, tenant-bound, audited, and read-only by default.

## Read first

1. `AGENTS.md`
2. `AWS_WORKSPACE.md`
3. `.kiro/steering/`
4. `.kiro/specs/aws-multicounty-platform/requirements.md`
5. `.kiro/specs/aws-multicounty-platform/design.md`
6. `.kiro/specs/aws-multicounty-platform/tasks.md`
7. `docs/architecture/adr/`
8. `handoffs/KIRO_LATEST.md`

## Next package

Assign Kiro Package 1A only: inventory the existing direct integrations and
prepare synthetic characterization tests. It must not contact live services or
change application behavior. Hosted Codex must inspect Kiro's output, verify
that no protected data or secrets were captured, and approve the result before
Package 1B begins.
