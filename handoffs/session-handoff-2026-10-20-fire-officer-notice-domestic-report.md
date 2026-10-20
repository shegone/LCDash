# Session handoff, 2026-10-20: fire officer role, confidentiality notice, domestic-calls report

## Headline

Three changes shipped to the AWS pilot, each through the guarded release path.
Ted confirmed the domestic-calls report worked for what he needed. The
CentralSquare report-import work was explored and then set aside at his request.

## What shipped

| Commit | Change | ECS web revision | Image digest (prefix) |
| --- | --- | --- | --- |
| `57f1b67` | `fire_officer` role: live operations with full call detail, no analytics, reports, knowledge, MAE, Mindshare or tools | 107 | `sha256:f9f68ace` |
| `d43224c` | Confidentiality notice accepted after sign-in, footer on every dashboard page | 108 | `sha256:d0beeb89` |
| `564d1d5` | Domestic-Related Calls report on Pre-Built Reports | 114 | `sha256:1ebf4a96` |

(Revisions 109 to 113 belong to the radio work in between.)

- Change sets executed: `fire-officer-57f1b676bdf2`, `notice-d43224ce8c6b`,
  `domestic-report-564d1d5da5cc`. The last was executed only after Ted typed
  its exact name, per the current charter.
- Each scan showed only findings Ted accepted on 2026-10-17 (util-linux
  baseline plus CVE-2026-53612/53613/53614, zlib CVE-2026-85091).
- Tests at HEAD `564d1d5`: application and infrastructure suites 2,130 passed.
- Rollback target for the latest release: digest `sha256:d0beeb89...` (revision
  108 image). Rollback is a change set with that digest.

## Fire officer role

- Allowlist tier in `app/core/fire_officer_tier.py`, deny by default, no field
  reduction. Cognito group `lcdash-pilot-fire-officer` (precedence 18) created by
  the foundation stack; allowlist count for `UserPoolGroup` raised from 5 to 6.
- Reaches dashboard, active calls, call detail, units, station alerts and the
  live map. Heat map and Call Flow Cards stay closed to it.
- It is not field-sanitized, so it sees caller and narrative detail. Assign it
  only to people entitled to that.

## Confidentiality notice

- Cognito's classic hosted login has no text field, so the notice is shown at
  the first page the app controls. Wording lives in `app/core/usage_notice.py`;
  changing `NOTICE_VERSION` makes everyone accept again.
- It is a notice, not an access control. Wording has not been reviewed by
  counsel; Ted was told to have the county attorney approve it.

## Domestic-calls report

- `app/services/domestic_call_report_service.py`; routes
  `POST/GET /api/reports/domestic-calls/jobs`; card and report on `/reports`.
- Reads CentralSquare month by month (`POST /cfs_core/search` only) through the
  pilot's own read-only connector and Secrets Manager credential. Counts a call
  as domestic when its primary call type contains "domestic" (DV and DVNA in the
  Incident Codes list). Aggregates only; supervisor, dispatcher and admin.
- Finished months are cached in memory for 24 hours; a redeploy clears the cache.
- The first real run was the heaviest CAD read the pilot has made. Vendor rate
  limits for that volume were not confirmed.
- `scripts/domestic_call_percentage.py` runs the same report from a workstation;
  it is not needed for normal use.

## CentralSquare report import (set aside, nothing built)

Ted supplied exported reports (zip holding `report.json`). Learned:

- Format: `{"report": {title, main_entity, read_only, definition (XML), description,
  category, share_level}, "schema_version": "220"}`; the definition is a
  `<zreport>` document with fields, labels, groups, sorts, aggregates and a filter.
- Main entity `cad_incidents`; primary call type field
  `cad_incidents.incident_code.codedescription`; date ranges use
  `<range type="Recent"/>` with a start constant and `8000-12-31` as an open end.
- Never seen in any export: a text "contains" filter, a count aggregate, a
  crosstab, a month field. One more export of a crosstab with a contains filter,
  custom date range and count would settle these. Ted said it is not needed now.

## Not LCDash

Ted asked whether the radio listener could be affecting Sound Communications
ticket 3771 (cloud recorder not archiving to the on-premises Eventide recorder,
Eventide ticket COM-47296). Findings: the listener only listens to multicast on
`10.5.2.12` and sends HTTPS to the dashboard; port 4024 appears nowhere in this
repository. The recorder failure predates the radio uploader (installed
2026-10-17; last good archive 2026-10-09). The screenshot shows TCP 4024 timing
out to the on-premises recorder's public address; its nmap run did not scan 4024
and its traceroute used 4042. Matt Mayhorn has the firewall question.
Not verified: when capture first started on `.227` (SSH from this workstation
was refused during the session).

## Open items

- CentralSquare: revocation of the old read credential and webhook secret, and
  the Google OAuth client, remain from the 2026-09-14 handoff.
- Fire officer accounts: none were created in this session.
- Services were left running; nothing was stopped or disabled.
