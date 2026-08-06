# LCDash cloud full-screen visual parity program

Status: **LOCAL IMPLEMENTATION PROGRAM - NO DEPLOYMENT AUTHORIZED**

## Objective and evidence basis

The on-prem LCDash interface is the visual and interaction source of truth. The
cloud copy should match its operational hierarchy, density, typography, spacing,
map treatment, unit/status panels, and dark command-center composition wherever
the cloud has an approved capability. The cloud must retain its separate Cognito
authentication, cloud source/readiness labels, tenant boundary, and one-way
read-only CAD behavior.

The initial Active Calls comparison is grounded in the on-prem screen captured by
the root reviewer and reduced to a sanitized layout specification. No live call
identifier, address, person, phone number, narrative, unit identifier, coordinate,
or screenshot containing operational data is copied into this document.

## Shared command-center system

`static/css/lcdash-command-center.css` is the shared parity layer loaded by the
base layout after the established core stylesheet. It aliases the existing LCDash
palette and defines common command-page typography, panel headings, source and
safety banners, spacing, radii, touch targets, and reduced-motion behavior.

All screen slices should converge on these primitives:

- page eyebrow, title, subtitle, source freshness, and safety boundary;
- compact hero with status and two to four KPI tiles;
- glass panels with cyan structure, amber headings, green verified state, and red
  failure state;
- stable label/value rows, status pills, unit/resource cards, and chronological
  timelines;
- map panels that remain dominant when location is the main operational question;
- one canonical server/client incident-card representation per presentation mode;
- explicit empty, awaiting-first-snapshot, stale-snapshot, and unavailable states;
- no cloud control that implies a write, acknowledgement, dispatch, alert, or
  operational-output authority.

## Prioritized screen inventory

| Priority | Screen and route | Current parity state | Required next slice |
|---|---|---|---|
| P0 | Shared shell, navigation, topbar | Common base exists; tokens are split across core and large inline blocks | Consolidate command tokens/components, preserve Cognito and mobile navigation |
| P0 | Dashboard `/dashboard` | Strong on-prem wallboard, but cloud server cards and 30-second client refresh can select the legacy card hierarchy | Pass explicit cloud presentation into initial render and hydration; use one canonical cloud card renderer |
| P0 | Active Calls `/active-calls` | Best current shared surface: filters, density, three-column cards, stale/verified handling | Reuse shared tokens; keep normalized badge and no broad coordinates |
| P0 | Call detail `/calls/{cfs_number}` | First parity slice implemented locally | Review hero with priority/elapsed/units, dominant map-left/details-right, compact units/log cards, and intentional sensitive-field omissions |
| P0 | Units `/units` | Layout is mature, but cloud can claim Connected/Live/Full while awaiting or stale | Drive labels from shared source presentation; say normalized read-only snapshot and preserve unknown groups |
| P1 | GIS Map `/map` | On-prem interaction is substantially present; cloud synthetic mode is empty and does not consume the normalized bridge | Keep empty until a separate broad-coordinate privacy gate; do not weaken detail-only coordinates |
| P1 | Heatmap `/map/heatmap` | Visual parity exists, cloud correctly fails closed without approved history | Back only with approved imported aggregate history; never enable direct cloud CAD history searches |
| P1 | Analytics `/analytics` | Rich chart parity is present but data-dependent | Fix saved-widget tenant isolation before activation; then connect approved cloud history |
| P1 | Reports `/reports` | Workflow parity exists, but jobs are memory-only and direct-CAD dependent | Add tenant-aware durable jobs/artifacts, portable labels and filenames, and approved cloud history source |
| P1 | MAE `/mae` | Strong on-prem assistant composition with cloud readiness banner | Keep inquiry-only; make suggested prompts and status cards reflect only available cloud retrieval/data capabilities |
| P1 | Mindshare `/mindshare` and subroutes | Mature specialist UI; cloud knowledge can be retrieve-only | Separate product identity from provider state; retain citation, coverage, and no-action labels |
| P1 | Knowledge `/knowledge` and document detail | Library hierarchy exists; cloud ingestion/readiness now has approved source metadata | Match on-prem density while preserving tenant prefixes, citation-only retrieval, and document authorization |
| P1 | Voice `/voice` | Local Polly readiness slice complete; Transcribe intentionally disabled | Use provider-aware TTS/STT controls, keep no-persistence notice, and require one-time smoke authorization |
| P2 | Integration Health `/integrations/health` | Operational status composition exists | Normalize provider/source badges and distinguish configured, verified-on-use, stale, and unavailable |
| P2 | MAE/Mindshare reliability and coverage routes | Quality screens exist | Apply shared panels/tokens after primary operational screens; retain evidence labels |
| P2 | Station Alerts `/station-alerts` | On-prem operational output surface has no cloud authority | Preserve disabled/non-authoritative presentation only; do not pursue functional parity |
| P2 | NGA911 Intelligence routes | Separate statewide intelligence product family | Align shell/tokens where useful without merging scope or implying CAD/ESInet authority |

## Active Calls detail sanitized comparison

The local cloud template now follows the reviewed composition:

1. Command header: incident description/code/reference and status, followed by
   Priority, Elapsed, and Units KPI tiles.
2. Main body: dominant Location and conditional read-only Leaflet map on the left;
   Incident Details on the right.
3. Secondary right rail: normalized Assigned Units and chronological Command Log.
4. Privacy difference: reporter/caller, RapidSOS, ProQA, raw payload, responder,
   and unapproved timing fields remain omitted. This is an intentional whitelist
   boundary, not a visual-parity defect.
5. Safety difference: a persistent normalized/read-only banner remains visible.

## Staged implementation sequence

### Stage 0 - Safety and baseline

- Freeze exact route/template/script inventory and sanitized reference captures.
- Record current cloud source modes and fields permitted on each screen.
- Add structural template contracts before moving layout.

### Stage 1 - Shared system

- Move repeated palette, spacing, headings, KPI, status, timeline, and panel rules
  into `lcdash-command-center.css`.
- Add reusable Jinja components for page headers, source/safety banners, KPI tiles,
  status pills, and normalized empty states.
- Keep page-specific geometry in page stylesheets.

### Stage 2 - Operational screens

1. Dashboard server/client card parity and source semantics.
2. Active Calls list and detail, including map/no-map and log/no-log states.
3. Units source semantics, density, and status-group parity.
4. GIS Map only after the separate coordinate/privacy capability decision.

### Stage 3 - Analytics and reporting

- Fix widget tenant isolation first.
- Define one approved cloud historical-data repository used by analytics, heatmap,
  and reports; direct CAD history remains prohibited.
- Make report jobs durable and tenant-bound before enabling cloud generation.

### Stage 4 - Intelligence and knowledge

- Apply common hierarchy to MAE, Mindshare, Knowledge, integration health, and
  reliability screens while preserving capability-specific readiness and citations.
- Do not display controls for unavailable tools or action domains.

### Stage 5 - Voice

- Deploy only after its separate review. TTS and STT controls remain independently
  provider-aware; Transcribe stays disabled until separately implemented.

### Stage 6 - Mobile field-phone parity

- Recheck every screen at desktop, tablet, and phone breakpoints after desktop
  parity is stable.
- Use 44-pixel minimum touch targets, condensed one-column command headers/KPIs,
  non-overlapping sticky navigation, readable type/contrast, and horizontally safe
  tables and filters.
- Maps must retain a useful minimum height and accessible controls. Command logs
  must scroll within a bounded panel without trapping the page.
- Never collapse or hide cloud source, freshness, authentication, or read-only
  safety labels on small screens.

## Verification matrix for every slice

- structural template contract for hierarchy and required safety labels;
- mapped/unmapped, populated/empty, fresh/stale/unavailable synthetic fixtures;
- server render and client hydration parity;
- no forbidden or newly exposed fields in HTML or API projection;
- keyboard focus order, skip link, accessible names, contrast, and reduced motion;
- visual regression at 1440x900, 1024x768, 768x1024, 390x844, and 360x800;
- touch review for filters, navigation, map controls, timelines, and primary buttons;
- relevant focused contracts plus full local suite before any image build;
- no deployment until a separate immutable-image and CloudFormation authorization.

## Current blockers and non-parity boundaries

- Broad cloud map coordinates remain intentionally prohibited by the current
  detail-only coordinate package.
- Heatmap, analytics, and reports need an approved imported historical source.
- Analytics widgets need tenant isolation.
- Reporter/caller, RapidSOS, ProQA, raw CAD, and other sensitive detail fields are
  not approved for cloud display.
- Station alerts and every operational output remain disabled and non-authoritative.

This program authorizes no deployment, AWS write, CAD write or new query, live
resource change, commit, or push.
