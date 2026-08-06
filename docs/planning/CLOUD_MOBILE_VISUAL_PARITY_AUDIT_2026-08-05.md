# Cloud mobile and visual parity structural audit

Status: **STRUCTURAL BASELINE REVIEWED - BROWSER SCREENSHOT REGRESSION STILL REQUIRED**.

This local audit inspected shared CSS and templates only. It made no shared UI
implementation change and performed no render, server, provider, AWS, CAD,
deployment, credential, commit, or push action. The required viewport matrix is
desktop `1440`, compact desktop `1024`, tablet `768`, field phone `390`, and
narrow field phone `360` CSS pixels.

## Result by surface

| Surface | 1440 / 1024 | 768 | 390 / 360 | Structural result |
| --- | --- | --- | --- | --- |
| Dashboard | Two-column command layout reduces at 1200; status grid is bounded. | Main shell drawer and two-column KPI/status treatment apply. | Title, padding, badges, and KPI density condense at 576. | Ready for visual regression. |
| Active Calls | Flexible header/cards and normalized source banner fit. | Fact grid reduces to two columns; status items wrap. | Cards inherit reduced padding and touch targets. | Ready, but long incident/location strings need screenshot stress data. |
| Active Calls detail | Dominant map/details split uses Bootstrap `xl` columns. | Columns stack and map reduces to 48vh. | Three KPIs stack at 576; map remains at least 340px. | Ready for visual regression. |
| Units | Board cards and roster use fluid columns. | Roster becomes two columns with metadata on its own row. | Shared 44px controls apply. | Medium risk: long unit/status tokens use nowrap and need 360px stress testing. |
| Analytics | Charts are bounded; tables use responsive wrappers. | Cards/panels reduce padding and chart height. | Saved widgets retain a 320px minimum inside a narrow content area. | Medium risk at 360px with browser text scaling or long labels. |
| GIS / heatmap | Large map canvases and controls are intentionally dominant. | Both templates define their own tablet map height and scrollable tabs. | Controls become full-width/44px and maps retain a 400px template minimum. | Ready structurally; field-phone pan/zoom and legend overlap need real interaction tests. |
| Reports | Builder and report grid collapse below 900. | County sections become one column. | Shared page rule collapses report cards. | Medium risk: generated county tables lack explicit `table-responsive` wrappers; test long department names at 360px. |
| MAE | Workspace is two-column at desktop. | Hero, workspace, status, voice controls, and prompts stack below 992. | Chat height and bubbles condense below 576; prompt strip scrolls. | Ready; keyboard-open composer and evidence-card scrolling require device testing. |
| Knowledge / Mindshare | Libraries and module grids have bounded responsive layouts. | Mindshare grids collapse below 992. | Module/roadmap grids become one column; document tables are responsive. | Ready; long document titles need 360px regression. |
| Voice | Two-panel grid and roadmap are desktop-first. | Both collapse below 900. | Shared touch targets and compact hero padding apply. | Ready; disabled TTS/STT messaging must remain visible without horizontal scroll. |
| NGA | Metrics/layout/network grids have 1250/1200/980/800 breakpoints. | Dense operational paths become single-column below 700, not at 768. | Metrics and consoles collapse; controls are 44px. | **Blocked for standalone navigation below 992px.** |

## Exact blocker

`templates/layouts/nga911_base.html` loads `lcdash-mobile.css`. At widths below
`991.98px`, that stylesheet translates `.sidebar` off screen. Unlike the primary
layout, the standalone NGA layout has no `mobile-menu-button`, no
`mobile-nav-overlay`, no close control, and no `lcdash-mobile.js`. Therefore the
standalone `/nga911` family loses navigation at 768, 390, and 360. This is a P0
mobile parity defect. Repair should reuse the primary accessible drawer pattern
in a separate reviewed implementation slice.

## Remaining visual and interaction gates

1. Capture authenticated, sanitized screenshots at all five widths for every
   surface. Use synthetic or empty data only; no live identifiers or CAD detail.
2. At 390 and 360, test 200% text zoom, browser font scaling, long incident and
   unit strings, long document titles, and long report department names.
3. Verify every interactive control has a 44px target, visible keyboard focus,
   and no hover-only required action. NGA path tooltips are hover-only, although
   the linked detail remains reachable; verify this is understandable on touch.
4. Exercise mobile map pan, zoom, popup close, tab scrolling, legend visibility,
   and page scrolling without gesture trapping.
5. Exercise MAE with the software keyboard open and Voice with TTS ready/STT
   disabled. Readiness, read-only and advisory labels must remain visible.
6. Confirm tables either fit or scroll within their panel without causing whole-
   page horizontal scrolling. Reports are the highest-risk current surface.
7. Run contrast checks on muted text, disabled controls, cyan-on-panel labels,
   amber warnings, and green safety banners; structural inspection cannot prove
   WCAG contrast after compositing translucent surfaces.
8. Verify reduced-motion behavior for command pages and NGA animated simulation
   elements. The command system has a reduced-motion rule; NGA animation coverage
   needs browser confirmation.

## Acceptance rule

Mobile parity is not complete until the standalone NGA drawer blocker is fixed
and sanitized visual/interaction regression passes at 1440, 1024, 768, 390, and
360 with no clipped primary action, whole-page horizontal scroll, trapped map,
or hidden read-only/advisory boundary. No operational label may be shortened in
a way that implies live, writable, authoritative, or provider-verified state.

No server, provider, AWS, CAD, deployment, credential, permission, commit, or
push action is authorized by this audit.
