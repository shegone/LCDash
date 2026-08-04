# Kiro Package 1C completion report

STATUS: PASS (accepted by hosted Codex on 2026-08-04)

## Outcome

Package 1C is complete. Inherited CentralSquare read access now sits behind a
concrete `CentralSquareCadAdapter` implementing the accepted version 1
`CadProvider` contract. The inherited `app/services/centralsquare.py` HTTP/OAuth
client remains unchanged as the low-level transport, preserving on-premises
configuration, request shapes, timeout defaults, Docker behavior, and test
seams. Existing read consumers retain their `CentralSquareClient` local symbol
as a compatibility alias to the adapter, so external API semantics and mocks
remain stable.

The adapter exposes normalized provider reads and a raw read compatibility shim.
It does not expose `run_command`. Event ingestion, subscription registration,
call updates, messages, and acknowledgments remain explicit provider methods
that deny by default. The inherited EMS command path and subscription scripts
remain separate on the low-level operational transport and were not enabled,
invoked, or changed.

## Checkpoint and working tree

- Branch: `aws/modular-county-platform`
- Starting and current HEAD: `f81fbedc893416da43bceac07abff5d9d440c257`
- Starting state: clean; no pre-existing changes
- Final state: uncommitted Package 1C files only
- Classification: `CHANGE + TEST`, local isolated AWS worktree

## Files changed

- Added `app/integrations/cad/centralsquare.py`.
- Updated `app/integrations/cad/__init__.py` to publish the adapter.
- Changed only the CentralSquare client import seam in:
  - `app/main.py`
  - `app/services/analytics_collector.py`
  - `app/services/cad_service.py`
  - `app/services/county_commission_report_service.py`
  - `app/services/heatmap_service.py`
  - `app/services/mae_service.py`
  - `app/services/operations_service.py`
  - `app/services/station_alert_service.py`
  - `app/services/unit_service.py`
  - `scripts/backfill_dispatcher_names.py`
  - `scripts/inspect_subscription_agencies.py`
- Added `tests/contracts/test_centralsquare_adapter.py`.
- Updated `.kiro/specs/aws-multicounty-platform/tasks.md` to record Package 1C
  implementation complete while leaving hosted acceptance open.
- Replaced `handoffs/KIRO_LATEST.md` with this report and added
  `handoffs/KIRO_PACKAGE_1C_2026-08-04.md`.

`app/auth/oauth.py`, `app/services/centralsquare.py`, settings, Docker files,
templates, routes, operational services, subscription/output scripts, and
deployment definitions were not changed. The two changed scripts are read-only
inspection/backfill consumers and received the same import-only adapter seam.

## Adapter design and compatibility

- `CentralSquareReadTransport` describes only the inherited raw read methods.
  Default adapter construction creates the unchanged inherited client, so its
  OAuth and environment configuration behavior remains intact.
- `legacy_tenant_context()` provides an internal trusted Logan server binding;
  provider calls reject a different tenant. Request IDs may vary within the
  same bound deployment tenant.
- Provider call/detail and unit methods return the immutable normalized models
  accepted in Package 1B. They reuse `simplify_call()` and `normalize_unit()`
  internally, preventing parallel normalization drift.
- Provider pagination maps an opaque integer cursor to the inherited
  `skip`/`limit` contract and honors the transport's `next` signal.
- Provider-facing transport timeouts and HTTP 429 failures translate to
  sanitized `ProviderTimeout` and `ProviderRateLimit` errors with minimized
  audit outcomes. Other failures become a generic `ProviderError`.
- Raw compatibility methods preserve current method names, arguments, and raw
  response shapes for inherited normalized services, analytics collection,
  reports, heatmaps, MAE, station-alert preparation, and diagnostics.
- The adapter capability set contains authentication, health, call search,
  call detail, unit search, and event normalization only. It omits event
  ingestion and every CAD write/subscription/output capability by default.

## Commands and exact results

- Package 1C focused test:
  `python -m unittest tests.contracts.test_centralsquare_adapter -v`:
  `Ran 6 tests in 0.007s` and `OK`.
- Combined feasible baseline:
  `python -m unittest tests.test_aws_package_1a_characterization tests.contracts.test_provider_contracts tests.contracts.test_centralsquare_adapter -v`:
  latest run `Ran 23 tests in 0.012s` and `OK`.
- `python -m py_compile` for the adapter and every changed application module:
  exit code `0`, no errors.

The first focused run had five passing tests and one import error because its
wiring assertion loaded unrelated analytics code requiring the workstation's
missing `psycopg` package. The test was corrected to inspect the exact source
import seams without loading unrelated database/runtime dependencies; the
second focused run passed all six. Nothing was installed.

## Acceptance evidence

1. Normalization parity: adapter call fields are compared directly with the
   output of the inherited `simplify_call`; unit normalization reuses the
   inherited `normalize_unit` implementation.
2. Minimization: provider models omit raw payloads, reporter data, phone data,
   narratives, and command-log content while preserving required normalized
   operational fields.
3. Pagination: two call pages and one unit page prove exact stable offsets,
   limits, non-overlap, and terminal cursors.
4. Timeout/error translation: a synthetic wrapped transport timeout becomes a
   sanitized `ProviderTimeout`; synthetic HTTP 429 becomes
   `ProviderRateLimit` with deterministic retry metadata.
5. Tenant binding: a different immutable tenant context fails before transport
   use and records a minimized denial.
6. Capability denial: subscription, update, message, and acknowledgment methods
   fail closed and record capability denials; no `run_command` exists on the
   adapter.
7. Compatibility: fake transport tests prove raw search, unit, detail, and
   analytics signatures and response identities remain unchanged.
8. Consumer migration: source assertions prove every named read consumer and
   read-only inspection/backfill script uses the adapter alias while EMS command
   delivery and subscription registration stay on the separate inherited transport.
9. No network: every focused test blocks socket, HTTP GET/POST/PUT/stream, and
   HTTP client entry points and asserts they were unused.

## Safety, privacy, and boundary review

- Synthetic fixtures only; no live CAD payload, protected record, credential,
  operational address, or real identifier was used.
- No access to `E:\Projects\LCDash`, `.227`, `.15`, live CAD, credentials,
  backups, operational data, or operational outputs occurred.
- No AWS CLI/API/CDK/deployment, webhook registration, CAD write, subscription,
  EMS delivery, paging, station alert, or public-warning action occurred.
- No settings, secrets, Docker, deployment, or inherited transport behavior was
  changed.
- Nothing was committed, pushed, merged, deployed, installed, or operated.

## Assumptions and unverified facts

The adapter is verified against a fake transport only. It does not prove vendor
permission, live API compatibility, cloud egress behavior, concurrent credential
use, rate limits, or webhook behavior. The compatibility tenant is an internal
single-county bridge; Package 2 and later identity work must replace it with
trusted deployment/federation bindings before multi-county activation.

The workstation still lacks the broader inherited suite dependencies documented
in earlier handoffs (`pytest`, `psycopg`, and timezone data). The complete
feasible Package 1A+1B+1C standard-library baseline passed; no dependency was
installed to expand the environment.

## Hosted Codex acceptance

Hosted Codex independently reviewed the adapter, every migrated import seam,
the separated operational transports, focused tests, and the durable handoff.
The combined Package 1A+1B+1C rerun completed with `Ran 23 tests in 0.030s`
and `OK`; changed modules compiled; diff and secret scans were clean; and no
live network entry point was used. Package 1C is accepted.

## Exact next package and gate

Stop here. Package 2 is next in the roadmap, but Kiro must not begin county
profiles or feature boundaries without a new bounded assignment. AWS resource
creation must wait for the documented Package 5A account, role, budget, logging,
rollback, and approval evidence.

## Codex catch-up

Package 1C was accepted by hosted Codex after independent adapter, import-seam,
operational-boundary, test, compilation, diff, and secret reviews. The adapter
keeps the inherited transport and raw behavior intact, adds provider
normalization/error/audit boundaries, and denies operational capabilities. The
combined feasible suite passes 23/23 with fail-closed network sentinels.
