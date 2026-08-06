# AWS Move Thread Handoff

## Current verified state — 2026-08-05

The separate synthetic/disconnected Logan County cloud pilot is running in AWS
account `862772137583`, region `us-east-1`, from repository
`E:\Projects\LCDash-AWS` on branch `aws/modular-county-platform`. The current
local HEAD is `34f5154c0805196a89aebf08d4c9cc7de7cb737e`; substantial reviewed work
remains uncommitted and must not be discarded, committed, or pushed without a
separate instruction.

- Foundation stack: `lcdash-p1-logan-use1-foundation`, `UPDATE_COMPLETE`.
- Container: experimental Alpine path, immutable digest
  `sha256:fd6777aa337d845996a3063340ceeb7cb05cc5123ba2831240be7e78d8fabb10`.
  Its ECR basic scan completed with Critical/High/Medium `0/0/0`; native Python
  dependencies installed from compatible `musllinux` wheels without compiler
  failures. The release used source manifest
  `28bd5faa7c8f2a09e4977c749418d644b35b1a41ab5b6ec550ef24fef40c58e4`
  and the single guarded Alpine CodeBuild completed successfully.
- Database: private PostgreSQL 17.10 RDS database `lcdash`; the one-off
  allowlisted initializer exited `0` and initialized 52 Phase 1 analytics and
  knowledge statements. No realtime, webhook, alerting, EMS, paging, CAD, or
  operational-output schema was initialized by that task.
- ECS: one healthy task, desired/running/pending `1/1/0`; container and ALB
  target health are healthy, the deployment completed with zero failed tasks,
  and the running container reports healthy on the immutable digest above.
- P0 synthetic parity: Dashboard, Units, Map, and Heatmap now share an explicit
  `synthetic-disconnected` service boundary that returns safe empty provider
  results before any legacy CAD client initialization. The on-premises CAD read
  paths remain unchanged. Local contract validation passed `144` tests plus
  `105` subtests, and the six affected cloud page/API paths remain protected by
  Cognito without an authentication bypass.
- Public endpoint: `https://aws.logan911.com` resolves through the expected ALB,
  its TLS certificate validates for the hostname, and unauthenticated requests
  redirect to the Cognito authorization-code sign-in without exposing app
  content.
- Cognito: one enabled account for `tedsparks@911logan.com`, in only the
  `lcdash-pilot-reviewer` read-only group. Its status is
  `FORCE_CHANGE_PASSWORD`; Cognito sent the temporary credential directly by
  email. Required software-token MFA remains enabled. No password is recorded
  here or in the repository.
- Cost control: the configured monthly pilot budget remains USD 200. Budget
  notifications are advisory; they do not automatically stop public-safety or
  cloud resources.

## First login

Open `https://aws.logan911.com`, use the emailed Cognito invitation for
`tedsparks@911logan.com`, set a permanent password when prompted, and enroll the
required authenticator-app MFA. Do not share or paste any temporary password,
permanent password, or MFA seed into chat, logs, Git, or documentation.

After authentication, verify the Dashboard, Units, Map, and Heatmap pages load
without a server error and clearly present the synthetic/disconnected empty
state. Also confirm navigation remains usable and no operational or live-CAD
content appears. Record only non-secret observations and screenshots with no
credentials, MFA material, cookies, authorization codes, or session details.

## Data-import readiness and hold

The target schema and local one-way importer are ready for a separately
authorized historic analytics migration. The importer is limited to
`lcdash_analytics.calls`, `units`, `call_agency_times`, and `unit_responses`,
uses a database-enforced repeatable-read/read-only source transaction, applies
irreversible pseudonyms before target writes, removes dispatcher identifiers
and coordinates, and uses idempotent cloud upserts.

No source import has occurred. The current blocker is the separate Phase 2
human gate: approve the exact read-only source replica/account, data owner and
classification, UTC retention window and watermark, encrypted path, in-memory
pseudonymization-key custody, target/resume state, maintenance window, count and
hash evidence, freshness threshold, and cloud-only rollback authority. Never
request or record connection secrets or pseudonymization-key values.

## Absolute operational boundary

- Leave production `.227`, PC `.15`, `E:\Projects\LCDash`, live databases,
  credentials, backups, station-alert tones, and all operational services
  untouched.
- The cloud pilot remains synthetic/disconnected and non-authoritative.
- Any future CentralSquare work is inquiry-only and requires a separate named
  read-only authorization. It may search or retrieve approved normalized data
  only; it must not write CAD, create another webhook/subscription, acknowledge
  events, alter incidents or units, or poll outside an approved reconciliation
  contract.
- Never enable EMS delivery, station alerts, paging, public warnings, radio or
  ESInet actions, alert release, emergency-call routing, or any other
  operational output. AI is advisory and must never block authoritative CAD,
  dispatch, alerting, routing, radio, or tones.
- Do not retrieve, display, copy, log, commit, or place secret values in model
  prompts or handoffs.

## Architecture direction

- Shared metadata-only control plane with siloed county application cells.
- Configuration and provider interfaces instead of county code forks.
- Replaceable CAD, inference, retrieval, speech, GIS, identity, and
  authorization providers.
- Commercial AWS sandbox first, with partition-aware synthesis and explicit
  AWS GovCloud capability/fallback checks.
- AI remains advisory, tenant-bound, audited, and read-only by default.

## Read first

1. `AGENTS.md`
2. `AWS_WORKSPACE.md`
3. `.kiro/steering/`
4. `docs/planning/PACKAGE_5A_AUTHORIZATION_GATE.md`
5. `docs/planning/PHASE1_DEPLOYMENT_PREFLIGHT.md`
6. `docs/planning/PHASE2_DATA_MIGRATION_PLAN.md`
7. `infrastructure/README.md`
8. `handoffs/KIRO_LATEST.md`

## Safe next decisions

1. Complete the first Cognito login and MFA enrollment manually, without sharing
   credentials.
2. Review the cloud UI with synthetic data and record non-secret findings.
3. Keep the Phase 2 import on hold until its named human/data-owner gate is
   complete.
4. Keep live CAD inquiry access and every operational output outside the pilot
   unless separately authorized and reviewed.
