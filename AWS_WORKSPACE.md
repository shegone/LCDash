# LCDash AWS Multi-County Workspace

This worktree is the separate AWS development track for LCDash. It starts from
the known-good on-premises code but has a different deployment objective.

## Workspace identity

- Path: `E:\Projects\LCDash-AWS`
- Branch: `aws/modular-county-platform`
- Initial target: a non-production Logan County sandbox in `us-east-1`
- Long-term target: a partition-aware county template that can be deployed in
  standard AWS or AWS GovCloud (US), subject to service and contractual review

## Non-negotiable boundary

The production server at `.227`, its deployment branch, its secrets, its
database, and its running services are outside this workspace. Nothing in this
workspace may deploy to, connect to, or reconfigure `.227`.

The AWS sandbox starts with synthetic data. Reuse of Logan County's existing
CentralSquare API credentials is a later, separately approved read-only
integration step. The values must be entered directly into AWS Secrets Manager
by an authorized human or approved broker; they must never be copied into Git,
Kiro prompts, logs, handoffs, build output, or model memory.

## Kiro operating model

Kiro performs bounded implementation tasks from
`.kiro/specs/aws-multicounty-platform/tasks.md`. Hosted Codex is the
orchestration and review gate for architecture, security, credentials, AWS
account changes, deployments, and any operational public-safety behavior.

Until a task explicitly says otherwise, Kiro may:

- inspect and edit this worktree;
- create tests, documentation, CDK code, and synthetic fixtures;
- run local tests, linters, security scans, and `cdk synth`;
- prepare diffs and a handoff for review.

Kiro may not:

- read or modify `E:\Projects\LCDash`;
- access `.227`, `.15`, live CAD, protected credentials, or backups;
- create, update, or delete AWS resources;
- run `cdk deploy`, Terraform apply, CloudFormation deployment, or AWS write
  commands;
- push protected branches or merge pull requests;
- enable webhooks, CAD writes, paging, station alerts, or other operational
  outputs.

## Start here

1. Read `AGENTS.md`.
2. Read `.kiro/steering/product.md`, `tech.md`, `structure.md`, and
   `security-boundaries.md`.
3. Read the three files under `.kiro/specs/aws-multicounty-platform/`.
4. Work only the single task assigned by the orchestration manager.
5. Record results in `handoffs/KIRO_LATEST.md` using synthetic evidence only.

