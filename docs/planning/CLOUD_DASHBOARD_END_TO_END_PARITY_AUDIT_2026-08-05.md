# Cloud dashboard end-to-end parity audit — 2026-08-05

## Decision

Status: **CORE UI AVAILABLE; SAFETY-STATE PARITY NOT YET ACCEPTABLE**.

The authenticated cloud dashboard is healthy and its principal navigation and
read-only pages load. Active Calls and call detail now have strong display
parity for the approved normalized field set. The next release should not add a
feature. It should make provider provenance, disconnected behavior, and
advisory-feature availability truthful and fail closed across every page.

This was a read-only audit. No AWS resource, application, permission, secret,
CAD setting, document, AI model, speech provider, or on-premises system was
created, changed, invoked, or accessed. No incident values or private document
details are reproduced here.

## Evidence reviewed

- Existing authenticated reviewer session at `https://aws.logan911.com`
- Direct UI review of Dashboard, Active Calls, one call detail, Analytics,
  Pre-Built Reports, Integration Health, Knowledge Library, MAE, and Voice Lab
- Unauthenticated request behavior for the dashboard entry point
- Read-only Cognito user/group status
- Read-only ECS service/task-definition state, load-balancer target health, and
  sanitized CloudWatch application access/error logs
- Existing parity, CAD-boundary, analytics, and knowledge-search planning
  records in this repository

Observed cloud release:

- Image digest:
  `sha256:a1c471d414bf1260531c08ab632b3ef95d39586697e361e54d7948c84d535847`
- Task definition: `lcdash-p1-logan-use1-web:16`
- ECS desired/running/pending: `1/1/0`
- Deployment rollout: `COMPLETED`, failed tasks `0`
- Load-balancer target: `healthy`
- Recent sanitized log query: no `ERROR`, `Exception`, or `Traceback` matches

## Feature-by-feature result

| Area | Result | Read-only evidence |
|---|---|---|
| Authentication | Pass | Unauthenticated dashboard request returned a Cognito redirect. The enabled user is `CONFIRMED` and belongs only to `lcdash-pilot-reviewer`. Authenticated pages loaded without a bypass. |
| Navigation and dashboard cards | Partial | Navigation exposes the expected Operations, Intelligence, and Quality areas. Current-metrics, agency-summary, and incident-feed cards render, but source/state labels conflict with the documented boundary. |
| Active Calls | Pass for display; partial for provenance | Search, five filters/sorts, clear-filter control, normalized incident cards, priority/status/agency/unit fields, and links to detail are present. No CAD-changing control was observed or exercised. |
| Call detail | Pass for approved display | A normalized read-only incident summary and assigned-units section opened successfully with no alert or error. No acknowledge, dispatch, page, tone, or update control was present. |
| Analytics | Partial | The PostgreSQL-connected page loads date filters, standard periods, print/PDF controls, and the expected daily, hourly, agency, dispatcher, incident, weekday, unit, and station sections. Historical-data parity remains unproved because the approved import has not occurred. |
| Reports | Partial | County Commission and discipline report sections render, but the visible PDF link is a `#` placeholder and report correctness cannot be proven without approved history. |
| CAD polling/integration health | Fail for consistent truthfulness | The dashboard and Active Calls say connected/live; Integration Health says browser stream streaming and reconciliation active, while MAE says CentralSquare not configured. Deployed environment simultaneously declares `synthetic-disconnected`, enables cloud CAD polling, and references a read-only secret. |
| Knowledge Library | Safe but incomplete | The authenticated page loads with an Available Manuals heading and an Ask MAE route, but exposes zero document links and no clear unavailable/not-ingested explanation. |
| MAE advisory UI | Fail closed behavior incomplete | Status settles to Local AI Offline, historical database Connected, and CentralSquare Not configured, while the question box and voice-mode control remain enabled. Nothing was submitted. |
| Voice Lab | Fail closed behavior incomplete | Page says Voice Stack Ready and leaves Generate/Play and Record enabled even though the deployed knowledge state says documents are not ingested and the broader advisory gate is incomplete. Nothing was generated or recorded. |

## Prioritized gaps

### P0 — provider provenance and safety-state contradiction

The deployed UI reports **Connected**, **Streaming**, and **Live CAD Data** and
shows current incident cards. Integration Health also reports a streaming
browser connection and 30-second reconciliation. At the same time:

- `LCDASH_DEPLOYMENT_MODE=synthetic-disconnected`
- `LCDASH_CLOUD_CAD_ENABLED=true`
- `LCDASH_CLOUD_CAD_MODE=centralsquare-read-poll`
- MAE reports CentralSquare **Not configured**
- the current handoff and inbound-CAD assessment still describe the cloud pilot
  as synthetic/disconnected and not authorized for live activation
- the later Active Calls release record describes authenticated live cards and
  a read-only cloud CAD integration

These records do not establish one unambiguous, current source-of-truth. A
reviewer cannot tell whether the cards are synthetic fixtures, an authorized
read-only cloud feed, or a partial activation whose documentation was not
closed. That is an operational-safety and governance gap even if the connector
itself is read-only.

Required resolution: name the currently authorized provider mode and approval
record. Until that review is complete, the application must not infer
`Connected` merely because a snapshot returned records. A configuration or
authorization mismatch must present **SOURCE UNVERIFIED — READ-ONLY PILOT** (or
the stricter disconnected state) and must not call the data live.

### P1 — advisory and voice controls do not follow availability state

MAE accurately reports Local AI Offline and CentralSquare Not configured, but
its text and voice controls remain enabled. Voice Lab reports Ready and exposes
generation/recording controls while knowledge documents are explicitly not
ingested and KB approval is still pending. The UI invites actions that are not
currently approved or are expected to fail.

Required resolution: derive all MAE, RAG, Polly, and transcription controls from
one server-side presentation-safe capability record. Disable unavailable or
unapproved controls, state the reason plainly, preserve ordinary dashboard and
report access, and never perform a provider call merely to discover readiness.

### P1 — knowledge library lacks an explicit safe empty state

The library route loads, but no manuals are linked and the page does not explain
that ingestion is unapproved/not completed. The Ask MAE link leads to an offline
assistant. This can be mistaken for a loading or permissions defect.

Required resolution: show **Private library not ingested — approval required**,
document count `0`, last-ingestion `Never`, and disabled citation search. Do not
show invented manuals or upload/ingestion controls to the reviewer role.

### P1 — analytics/report presence exceeds proven data parity

The analytics surface is broad and healthy, but no approved historic import has
occurred. A connected database and rendered charts do not prove parity with the
documented on-prem calculations. The Reports page also exposes a placeholder
PDF link.

Required resolution: add an explicit synthetic/insufficient-history banner,
data-through timestamp, approved row-window/count status, and disable or label
report outputs that cannot yet be generated. Complete the aggregate-only parity
harness after the separate migration gate; do not import data as part of this
release.

### P2 — health page vocabulary is internally inconsistent

Integration Health mixes `WEBHOOK RECEIVER NOT CONFIGURED`, `BROWSER STREAM
STREAMING`, `DELIVERY METADATA AVAILABLE`, and `30-SECOND BACKUP` without naming
the actual source class or explaining whether the stream is browser-only SSE,
synthetic events, or provider delivery. The dashboard then interprets it as CAD
connectivity.

Required resolution: separate application/browser transport health from CAD
provider health. Suggested fields are `application_health`, `browser_sse`,
`provider_mode`, `provider_authorization`, `polling_health`, `data_freshness`,
and `source_classification`.

### P3 — minor delivery polish

Sanitized access logs include a `404` for `/favicon.ico`. This does not impair
the dashboard and should not displace the safety-state work.

## Recommended next safe release package

Name: **Cloud Source Truth and Advisory Availability Release**.

Bounded scope:

1. Introduce one immutable presentation-safe runtime state with an allowlisted
   provider mode: `synthetic-disconnected`, `authorized-read-poll`, or
   `unverified-disabled`.
2. Require the active provider, authorization marker, tenant profile, and
   deployment mode to agree. Any mismatch becomes `unverified-disabled`.
3. Feed Dashboard, Active Calls, call detail, Integration Health, MAE,
   Knowledge Library, and Voice Lab from that same state.
4. Replace ambiguous `live`, `connected`, and `streaming` claims with distinct
   source, poll, freshness, and browser-transport labels.
5. Disable MAE/RAG/speech/transcription controls when their feature is
   unapproved or unavailable; include a plain-language reason and make no
   discovery invocation.
6. Add explicit empty-state banners for knowledge, analytics, and reports.
7. Add contract tests proving that mismatched state cannot claim live CAD,
   cannot enable advisory/voice controls, cannot expose operational actions,
   and cannot initialize legacy/on-prem CAD paths.
8. Verify the exact container locally and in the isolated builder. If a later
   deployment is authorized, review a change set limited to the immutable image
   digest/task-definition/service pointer and repeat authenticated UI evidence.

Explicit exclusions:

- no CAD permission, endpoint, polling-rate, secret, webhook, or source change
- no AI, retrieval, speech, or transcription invocation
- no document ingestion or AWS knowledge resource creation
- no analytics import
- no station alert, EMS, page, tone, public-warning, radio, ESInet, dispatch,
  acknowledgment, or CAD write capability
- no on-premises access

## Release gate

Before implementing or deploying this package, the orchestration manager should
first obtain a written answer to one question:

> Which current approval record authorizes the deployed cloud dashboard's data
> source: synthetic/disconnected fixtures or bounded live CentralSquare
> read-only polling?

If that cannot be answered from a signed record, treat the source as unverified
and select the fail-closed presentation. This audit does not authorize a code
change or deployment.
