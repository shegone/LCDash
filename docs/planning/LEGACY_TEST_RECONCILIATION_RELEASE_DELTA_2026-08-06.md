# Legacy test reconciliation release delta

Status: **LOCAL RELEASE-READY TEST DELTA - NO DEPLOYMENT AUTHORIZED**

## Outcome

The 21 legacy application failures were triaged against the reviewed cloud
source-truth, tenant-isolation, advisory-only, and simulation boundaries. All
were stale tests or fixture-order pollution; no product safety gate was weakened
and no current product regression was found.

Updated expectations now:

- mock the current operations snapshot abstraction and supply an explicit
  verified-read presentation fixture for connected Active Calls behavior;
- expect browser update-channel language rather than treating browser streaming
  as proof of CAD connectivity;
- expect polished fail-closed GIS and heatmap unavailable states;
- forward trusted tenant context through analytics, reports, MAE analytics, and
  reliability test doubles;
- expect browser-only NGA network simulation and review language, with no alert
  permission or operational acknowledgment claim;
- import timezone-sensitive services without temporarily replacing
  `zoneinfo.ZoneInfo`, eliminating full-suite order pollution that could leave
  module constants set to UTC.

## Verification

Exact command:

`python -m pytest -q tests infrastructure/tests`

Result:

- **676 passed**
- **1 skipped**
- **204 subtests passed**
- **1 non-blocking FastAPI/httpx deprecation warning**

The previously accepted cloud and infrastructure safety contracts remain green.
No implementation behavior was relaxed to obtain this result.

## Separate on-prem evidence

`PC227_OLLAMA_EXPOSURE_EVIDENCE_AND_OPTIONS_2026-08-06.md` records the verified
`.227` LAN binding, the documentation discrepancy, evidence limits, and four
non-destructive remediation choices for approval. No `.227` networking,
firewall, authentication, service, or Compose change was made.

No AWS resource, provider, CAD, production output, commit, or push action is part
of this delta.
