# Lean LCDash Secondary Pilot Deployment Plan

## Purpose and current authorization state

Build one independent LCDash cloud pilot in commercial AWS `us-east-1` with a
USD 200 monthly target. The pilot is secondary and non-authoritative. Existing
CentralSquare CAD remains authoritative, and the pilot cannot perform an
operational write or output.

This is a deployment plan, not deployment authorization. Phase 1 remains
**NOT AUTHORIZED** until the Package 5A gate is completed. Real operational
data remains separately **NOT AUTHORIZED** until the Phase 2 activation gate
is completed.

## Phase 1 scope: one working synthetic or disconnected system

The initial architecture intentionally favors one usable system and low cost
over availability or recovery:

- one independently authenticated HTTPS application path;
- one small application compute instance/task with no standby;
- one small single-instance PostgreSQL database with no replica;
- no backups, point-in-time recovery, snapshots, restore workflow, or disaster
  recovery;
- S3 only for approved application assets, reviewed GIS/knowledge inputs, and
  short-lived report exports--not as a backup destination;
- short-retention logs and audit evidence sized to the approved cost model;
- usage-metered Bedrock, Transcribe, Polly, and Amazon Location calls with
  quotas, timeouts, graceful failure, and budget alarms;
- synthetic fixtures and approved public/reference data only.

The exact compute, database, ingress, certificate, DNS, logging, and network
resource types remain unresolved until CDK synthesis, current pricing, and
security review prove the complete allowlist can meet the budget. Expensive
default patterns--including multi-AZ resources, standby tasks, RDS Proxy,
NAT gateways, OpenSearch, QuickSight, always-on GPU capacity, and duplicated
delivery infrastructure--are excluded unless a later gate explicitly adds them.

## Practical functional parity

| LCDash function | Phase 1 implementation | Unavailable behavior |
| --- | --- | --- |
| Independent login and role checks | Cognito or approved federation, MFA, immutable tenant context, deny-by-default module/action authorization | Deny access; never accept tenant identity from request data |
| Dashboard, Active Calls, call detail, units, operational feed | Existing UI/contracts over synthetic providers; real read-only adapter remains disabled | Show connected-empty or clearly disconnected state |
| Map | Amazon Location Maps base map rendered with MapLibre | Keep non-map pages usable if the managed map is unavailable |
| Place search/geocoding and routes | Amazon Location Places and Routes as advisory functions | Hide/disable the affected control and label the provider unavailable |
| Call/unit map overlays | Application-generated minimized GeoJSON from the selected synthetic or later read-only feed | Omit invalid, expired, or unavailable positions |
| Heatmap | Existing county-bounded pure aggregation over minimized call coordinates | Return an empty/disconnected aggregate without exposing raw records |
| County GIS overlays | Reviewed, allowlisted tenant GIS objects only | Omit missing layers from the catalog; state that the layer was not supplied; never invent boundaries |
| Analytics and dashboards | Existing aggregate queries against the pilot database | Show insufficient-history/connected-empty state |
| Reports and PDFs | Existing bounded report generation; stream or retain briefly under an approved tenant prefix | Report unavailable without sufficient approved data |
| MAE and deterministic tools | Amazon Bedrock with tenant-bound read-only tools, deterministic allowlists, audit, and graceful non-AI fallback | Disable generation while retaining ordinary dashboards and reports |
| Knowledge and JACK | Approved documents plus a budget-reviewed retrieval implementation and Bedrock | Show library/provider unavailable; no invented citations |
| Voice | Amazon Transcribe and Polly; retain speech-only pronunciation rules, including "nine one one" | Text interaction remains available; branded local voice parity is not promised |
| Health and reliability | Sanitized provider/application status and bounded CloudWatch evidence | Never reveal secrets or raw provider payloads |
| NGA911/NOVA demonstrations | Explicitly synthetic data only unless separately sourced and approved | Label synthetic or omit |
| Station alerts, EMS delay, paging, CAD messages, acknowledgements, subscriptions, public warning | Not provisioned; denied by capability policy | No tones, messages, pages, releases, writes, or callback registration |

Amazon Location replaces the general basemap and can provide Places and Routes
functions. It does not replace authoritative county, PSAP, provisioning,
municipal, ESB fire/EMS/law, hydrant, trail, address, or local-road reference
data. Those layers require a reviewed source and license before upload.

## Cost controls

The USD 200 monthly amount is an initial target, not a guarantee. Before Phase
1 approval, the exact design must have a dated estimate with explicit usage
assumptions for application hours, database size, egress, log ingestion and
retention, map requests, geocoding/routes, Bedrock tokens, transcription
minutes, and synthesized characters.

Required controls are:

- one named budget owner and notification path;
- alerts at reviewed early-warning and stop-review thresholds below USD 200;
- service quotas and application rate limits for AI, speech, maps, reports,
  collection, and logs;
- short log/export retention and no accidental high-cardinality payload logs;
- feature flags that can disable optional managed providers without disabling
  core read-only pages;
- a documented human stop/teardown decision when spending or usage departs
  from the approved estimate.

Budgets and alerts notify; they do not automatically prove or enforce a hard
spend ceiling. Any automatic shutdown mechanism must be separately reviewed so
it cannot affect the authoritative CAD system--which is outside this pilot.

## Phase 2 scope: later real read-only operational data

Only after its separate activation gate is approved may the same secondary
pilot receive real operational data through bounded read-only CentralSquare
polling. Phase 2 adds only the minimum tenant-scoped secret reference, approved
egress path, collector schedule, normalized storage, and audit needed for that
feed.

Phase 2 must preserve these boundaries:

- CentralSquare remains authoritative;
- `.227` remains untouched and remains the sole webhook/output owner;
- no second subscription or webhook registration;
- no CAD updates, messages, acknowledgements, paging, station-alert release,
  EMS delivery, public warning, or other output;
- no raw CAD payload logs or broad raw-record retention;
- reconciliation polling, deduplication, field minimization, tenant binding,
  timeouts, and conservative rate limits remain mandatory;
- failure or disconnection degrades only the cloud pilot and never blocks CAD
  or on-premises operations.

## Explicitly accepted initial limitations

The pilot has a single point of failure in its application and database. With
no backup or recovery, data and configuration may be permanently lost after a
failure or teardown. Maintenance can cause downtime. These limitations are
acceptable only for the secondary pilot and must be presented to the named
approver before authorization.

Adding redundancy, backup, restore, recovery objectives, production readiness,
or authoritative operational ownership is a new architecture and approval
decision. It is not implied by successful Phase 1 or Phase 2 testing.

## Staged stopping points

1. Complete the Package 5A Phase 1 evidence and authorization record.
2. Synthesize and review the exact lean infrastructure and cost estimate; do
   not deploy if it exceeds the authorized allowlist or target.
3. Deploy only the approved synthetic/disconnected foundation using the
   recorded time-bounded role.
4. Verify login, isolation, deny-by-default outputs, graceful provider failure,
   cost alarms, and synthetic functional parity.
5. Complete the separate Phase 2 evidence and authorization record.
6. If approved, activate bounded read-only polling and verify aggregate parity
   without transferring raw data from `.227`.

Each stopping point requires hosted review before the next state-changing step.
