# Kiro Package 2A completion report

STATUS: PASS (pending hosted Codex review)

## Outcome

Bounded Package 2A is complete: only the first two Package 2 bullets were
implemented. The repository now has a versioned Draft 2020-12 non-secret county
profile JSON Schema, a deterministic Logan synthetic profile, a clearly
fictional second-county profile, and a standard-library offline loader that
validates security-critical schema invariants before constructing the accepted
immutable `CountyProfile`.

Both fixtures advertise the complete `ModuleCapability` catalog while enabling
different safe module subsets. They differ through configuration across CAD
provider, branding, timezone, region, agencies, unit/status mappings, GIS,
identity metadata, retention, AI provider policy, voice, enabled modules, and
preview/dry-run permissions. No county-specific runtime fork was added.

Package 2B/2C work did not begin: no county-specific application logic moved,
no module authorization enforcement was added, and no cross-tenant storage/API
tests were introduced.

## Checkpoint and working tree

- Branch: `aws/modular-county-platform`
- Starting and current HEAD: `14a1f19bdcf2c0c81962cae4c3ea6fb0f7394951`
- Starting state: clean; no pre-existing changes
- Final state: uncommitted Package 2A files only
- Classification: `CHANGE + TEST`, local isolated AWS worktree

## Files changed

- Added `config/counties/schema.json`.
- Added `config/counties/logan-synthetic.json`.
- Added `config/counties/northstar-fictional.json`.
- Added `app/core/county_profiles.py`.
- Added `tests/contracts/test_county_profiles.py`.
- Updated `.kiro/specs/aws-multicounty-platform/tasks.md` to record only the
  first two Package 2 bullets implemented while leaving acceptance open.
- Replaced `handoffs/KIRO_LATEST.md` with this report and added
  `handoffs/KIRO_PACKAGE_2A_2026-08-04.md`.

No existing application service, route, template, script, provider, Docker
file, deployment definition, setting, database file, or infrastructure code
changed.

## Schema and fixture design

- Schema and contract versions are fixed at `1.0`; unknown top-level and nested
  fields fail closed.
- The schema's required fields exactly equal the accepted `CountyProfile`
  dataclass fields.
- The module/capability enum exactly equals all 30 current
  `ModuleCapability` values, including operational capabilities that remain
  disabled in the fixtures' enabled module sets.
- Recursive property-name rules and the loader reject password, passcode,
  secret, token, API-key, credential, and private-key shaped keys. No profile
  field can contain or reference a credential.
- Nested definitions cover branding, agencies and disciplines, status mappings,
  GIS sources, identity metadata, bounded retention, advisory-only AI,
  allowlisted read tools, optional voice, and preview/dry-run permissions.
- Voice requires the pronunciation text `nine one one`.
- AI requires `advisory_only: true` and `protected_data_allowed: false`.
- Alert permissions are limited to station/paging/public-warning previews and
  EMS-delay dry run. Neither fixture enables station alerts, EMS delay, CAD
  messages, realtime webhooks, paging, or public warning as runtime modules.
- Logan data is synthetic and generic. Northstar County is explicitly fictional.
  Neither fixture contains an operational address, identifier, endpoint, secret,
  account, or protected record.

## Offline loader

`app/core/county_profiles.py` uses only `json`, `pathlib`, regular expressions,
and the existing immutable contracts. It rejects missing, unknown, duplicate,
malformed, unsafe, secret-shaped, or version-mismatched data and converts valid
arrays/maps into the frozen `CountyProfile` representation. Built-in profile
names must be stable identifiers, preventing path selection outside the approved
fixture directory.

No JSON Schema dependency was installed. The schema is a standalone standards
artifact; the loader mirrors its security-critical constraints so local tests
remain deterministic in the existing environment.

## Commands and exact results

- `python -m json.tool` parsed each of `schema.json`,
  `logan-synthetic.json`, and `northstar-fictional.json`: exit code `0`.
- Direct loader smoke check returned `logan-synthetic` and
  `northstar-fictional`: exit code `0`.
- Focused command
  `python -m unittest tests.contracts.test_county_profiles -v`:
  latest run `Ran 8 tests in 0.108s` and `OK`.
- Combined feasible command
  `python -m unittest tests.test_aws_package_1a_characterization tests.contracts.test_provider_contracts tests.contracts.test_centralsquare_adapter tests.contracts.test_county_profiles -v`:
  latest run `Ran 31 tests in 0.030s` and `OK`.

The first focused run had seven passing tests and one assertion mismatch: a
root password-shaped field was rejected as an unknown field before receiving
the more specific secret-key error. Validation order was corrected so recursive
secret detection runs first; the second focused run passed all eight. No safety
rule or acceptance criterion was weakened.

## Acceptance evidence

1. Both JSON fixtures load into deeply immutable `CountyProfile` objects.
2. Schema required fields exactly match the dataclass fields.
3. Schema capability enum and each fixture capability list exactly match every
   current `ModuleCapability`, with no duplicates.
4. Enabled modules are strict safe subsets of declared capabilities and omit
   all operational output modules.
5. Recursive tests reject root and nested password, credential-reference,
   API-key, and access-token shaped keys.
6. Missing required fields, unknown nested fields, unsafe alert-release
   permissions, non-advisory AI, protected-data AI, and incorrect 911
   pronunciation all fail closed.
7. The profiles differ in all requested configuration dimensions while using
   the same dataclass, parser, schema, and application code.
8. A source scan asserts neither synthetic tenant identifier appears in Python
   under `app/`, proving configuration rather than county-specific code forks.
9. Every focused test blocks socket and HTTP entry points and asserts none were
   called.

## Safety, privacy, and boundary review

- Synthetic configuration only; no raw/live CAD payload, protected record,
  credential, operational address, or real operational identifier was used.
- No access to `E:\Projects\LCDash`, `.227`, `.15`, live CAD, credentials,
  backups, operational data, or operational outputs occurred.
- No AWS CLI/API/CDK/deployment, webhook, CAD write, subscription, EMS delivery,
  paging, station alert, or public-warning action occurred.
- No dependency or software was installed. Nothing was committed, pushed,
  merged, deployed, or operated.

## Assumptions and unverified facts

The fixtures demonstrate configuration breadth and parsing only. They do not
activate modules, create tenants, enforce permissions, prove cross-tenant data
isolation, select current AWS services, or establish legal/operational policy.
`county_authoritative` is a permitted future metadata classification in the
schema; both current fixtures use only `public_synthetic` GIS data.

The broader inherited suite dependency limits documented in prior handoffs
remain unchanged. The complete feasible Package 1A+1B+1C+2A standard-library
baseline passed without installing anything.

## Exact next package and gate

Stop here. Hosted Codex must inspect and accept only the first two Package 2
bullets. The next bounded work would be Package 2B for moving selected
county-specific configuration safely, but Kiro must not begin it without a new
assignment. Module enforcement and cross-tenant boundary tests remain later
Package 2 work. No AWS resource creation is authorized until Package 5A.

## Codex catch-up

Review `config/counties/`, `app/core/county_profiles.py`, and
`tests/contracts/test_county_profiles.py` at HEAD
`14a1f19bdcf2c0c81962cae4c3ea6fb0f7394951`. The focused suite passes 8/8 and
the combined feasible suite passes 31/31 with network sentinels. The new parser
is unused by inherited runtime code, so application behavior is unchanged.
Accept the first two Package 2 bullets or request a bounded correction only; do
not infer authorization for Package 2B/2C, AWS, live data, or deployment.
