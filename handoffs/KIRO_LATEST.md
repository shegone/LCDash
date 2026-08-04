# Kiro Package 1A completion report

STATUS: PASS (accepted by hosted Codex on 2026-08-04)

## Outcome

Package 1A is complete. The inherited direct service dependencies are recorded
in `docs/AWS_PACKAGE_1A_DEPENDENCY_INVENTORY.md`. A five-test synthetic
characterization baseline now freezes current normalized call, unit, analytics,
MAE/JACK read-only tool, and roster-error behavior. Fail-closed sentinels prove
that the tests do not use sockets, HTTP clients, or PostgreSQL connections.
Application code and behavior were not changed.

## Checkpoint and working tree

- Branch: `aws/modular-county-platform`
- Starting and current HEAD: `5edbf8c0d4419de053160c1cf920fdc75982f625`
- Starting state: clean; no pre-existing changes
- Final state: uncommitted Package 1A documentation and test changes only
- Classification: `CHANGE + TEST`, local isolated AWS worktree

## Files changed

- Added `docs/AWS_PACKAGE_1A_DEPENDENCY_INVENTORY.md`.
- Added `tests/test_aws_package_1a_characterization.py`.
- Replaced `handoffs/KIRO_LATEST.md` with this fixed completion report.
- Added `handoffs/KIRO_PACKAGE_1A_2026-08-04.md` as the durable snapshot.

No file under `app/`, `scripts/`, `deploy/`, `database/`, `static/`, or
`templates/` changed.

## Tests and exact results

- `python -m pytest tests/test_aws_package_1a_characterization.py -q`
  could not run: `No module named pytest`. Nothing was installed.
- Initial `unittest` import could not run because this workstation has no
  installed `psycopg`. The test now supplies a test-only import stub when the
  package is absent; any connection attempt still fails closed.
- `python -m unittest tests.test_aws_package_1a_characterization -v`:
  `Ran 5 tests in 0.003s` and `OK`.
- A selected inherited-suite attempt could not load because the workstation
  also lacks timezone data: `ZoneInfoNotFoundError: 'No time zone found with
  key America/New_York'`. The same command first used a test-process-only
  psycopg stub; it made no connection. Per the two-failure safety rule, no
  further environment workarounds or installs were attempted.

The passing Package 1A tests cover:

1. deterministic normalized CFS call fields and unit status timing;
2. paginated unit-service contract, normalization, and conservative grouping;
3. minimized analytics normalization with no raw narrative copy;
4. MAE read-only catalog and JACK credential refusal before retrieval/model use;
5. roster failure fallback while preserving the active-unit contract.

## Safety, privacy, and boundary review

- Synthetic fixtures only; no raw CAD payload, protected record, credential,
  address, or operational identifier was used.
- Socket, HTTP, and psycopg calls are blocked and asserted unused in every new
  test.
- No access to `E:\Projects\LCDash`, `.227`, `.15`, live CAD, backups,
  credentials, operational data, or operational outputs occurred.
- No AWS CLI, CDK deploy, AWS API, web request, webhook, CAD write,
  subscription, EMS delivery, paging, station alert, or public-warning action
  occurred.
- No software was installed. Nothing was committed, pushed, merged, deployed,
  or operated.
- Final diff review must be completed by hosted Codex before acceptance.

## Kiro recommended advanced AWS migration path

Preserve every LCDash capability behind versioned, tenant-bound provider and
module contracts, then deploy county-silo application cells with a metadata-only
shared control plane. The strongest advanced path is:

- Application and delivery: containerized FastAPI web and bounded workers on
  multi-AZ ECS/Fargate behind CloudFront, WAF, and ALB; ECR immutable images;
  CDK; GitHub OIDC and CodePipeline/CodeBuild/CodeDeploy blue-green releases.
- Database structure: Aurora PostgreSQL-compatible or RDS PostgreSQL with RDS
  Proxy, one county database cell and KMS key per isolation boundary, retained
  `tenant_id` fail-visible columns, PITR/AWS Backup, and expand-contract
  migrations. Keep PostgreSQL/pgvector-compatible repository fallbacks.
- STT: Amazon Transcribe streaming and batch providers, with the inherited
  Parakeet/local provider retained where region, partition, vocabulary,
  latency, or accuracy requirements are unmet.
- TTS: Amazon Polly streaming provider with pronunciation lexicons and SSML;
  retain Qwen/Kokoro/local branded-voice providers where voice or partition
  parity is unavailable. Speech remains optional and outside authoritative
  station tones until separately accepted.
- AI/inference: Amazon Bedrock Converse/ConverseStream behind an
  `InferenceProvider`, with Guardrails plus deterministic validation. Evaluate
  AgentCore runtime, gateway, identity, memory, and observability only after
  tool contracts are stable. Retain Ollama/local inference as the regional,
  partition, outage, privacy, or model-quality fallback.
- Knowledge/retrieval: versioned approved documents in county KMS-encrypted S3;
  Bedrock Knowledge Bases where supported, with Aurora PostgreSQL/pgvector or
  an OpenSearch-compatible provider as fallback. Preserve lexical search,
  citations, document partitioning, supervisor-approved memory, minimization,
  and re-index workflows.
- GIS: Amazon Location for base maps, geocoding, routing, and optional
  geofencing, overlaid with authoritative county GIS in versioned encrypted S3.
  Retain the local GeoJSON/private-tile provider when Amazon Location is
  unavailable or county-authoritative data must stay independent.
- Identity/authorization: federated Cognito or county IdP login with MFA;
  immutable tenant claims bound by deployment; one deny-by-default
  `TenantAuthorizationService`, optionally implemented with Verified
  Permissions. Keep an application policy-engine fallback where GovCloud
  feature parity is insufficient. Never trust client-selected tenant IDs.
- Analytics/reporting: normalized events through EventBridge/SQS/Step Functions
  and bounded ECS collectors into county PostgreSQL; S3/Glue/Athena/QuickSight
  only for approved de-identified aggregate planes. Preserve existing PDF,
  dashboard, historical, audit, evaluation, and county-report outputs through
  normalized repositories and report providers.
- Operational modules: preserve CAD operations, units, realtime reconciliation,
  MAE, JACK, NOVA/NGA911, reports, GIS, voice/avatar, station-alert, EMS-delay,
  paging, and public-warning capability definitions. CAD begins bounded
  read-only polling. Webhooks and every output/write interface remain absent or
  denied until vendor confirmation, tenant/capability policy, authentication,
  audit, synthetic contract tests, single-writer fencing, rollback, and named
  human authorization are complete. AI remains advisory and cannot block or
  directly trigger CAD, call routing, radio, ESInet, tones, paging, or alerts.
- Platform services: AppConfig for non-secret county profiles/capabilities;
  Secrets Manager and county KMS keys; private subnets/endpoints and controlled
  egress; CloudWatch, ADOT/X-Ray, CloudTrail, Config, GuardDuty, Security Hub,
  Inspector, alarms, budgets, quotas, and tested restore/rollback evidence.

Commercial and GovCloud service/model/voice availability must be verified from
current authoritative AWS documentation during later packages. A versioned
region/partition capability registry must choose an approved provider fallback
or fail synthesis; unsupported capabilities must never silently disappear.
This is advisory only and creates no Package 1B+ implementation or AWS resource.

## Assumptions and unverified facts

The inventory is based on a local source scan of Python under `app/`, `scripts/`,
and `deploy/`. Runtime configuration and service availability were intentionally
not inspected. AWS recommendations above are architecture advice; current
commercial/GovCloud availability remains an explicit authoritative-verification
item for later work.

## Hosted Codex acceptance

Hosted Codex independently reviewed every Package 1A artifact, compared the
inventory with targeted source searches across all seven required dependency
areas, reran the fail-closed characterization suite, checked changed-file scope,
and scanned the artifacts for secret-like values. The rerun completed with
`Ran 5 tests in 0.003s` and `OK`; the secret-pattern scan reported no matches;
and no application or infrastructure file changed. Package 1A is accepted.

## Exact next package and gate

Stop here. Package 1B is the next planned package, but Kiro must not begin it
without a new bounded assignment from hosted Codex.
The human AWS deployment authorization gate remains incomplete, so no AWS
resource creation or deployment command is permitted.

## Codex catch-up

Package 1A was accepted by hosted Codex after independent diff, source-inventory,
test, scope, and secret reviews. The focused standard-library suite passes 5/5
with explicit live-service blockers. The default environment lacks pytest,
psycopg, and timezone data, so the inherited suite was not runnable and nothing
was installed. Application behavior is unchanged. Package 1B requires a new
bounded assignment; do not infer authorization for Package 1B or AWS.
