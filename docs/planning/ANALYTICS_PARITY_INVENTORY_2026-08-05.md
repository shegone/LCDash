# LCDash analytics parity inventory

## Required objects

Full parity for the analytics dashboard, aggregate reports, saved widgets,
unit/call timelines, and historical trends requires five copied tables and two
target-derived views.

| Object | Required content | Main consumers |
|---|---|---|
| `calls` | CFS ID, agencies, dispatcher name/stable ID, incident and disposition categories, priority, beat/zone/city, coordinates, scheduled flag, incident/received/closed/collected timestamps | totals, trends, dispatcher workload, incident/agency mix, history, freshness |
| `units` | unit ID, agency, type, station, last-seen time | unit identity, station and discipline grouping |
| `call_agency_times` | CFS ID, agency and dispatch/enroute/stage/scene/patient/backup/leaving/transport/arrival/available/quarters milestones | call and agency response timelines |
| `unit_responses` | CFS ID, unit ID/type/station/beat and response milestones | unit timelines, busiest units/stations, response coverage |
| `saved_analytics_widgets` | ID, timestamps, creator, title, allowlisted view key, status | customized dashboard parity |
| `call_response_metrics` | derived from calls and agency milestones | processing, turnout, travel, total response |
| `unit_response_metrics` | derived from calls and unit milestones | turnout, travel, response, transport, turnaround, commitment |

`sync_state` and `sync_runs` stay cloud-owned. MAE/JACK interactions, feedback,
evaluations, and memory are separate feature-history datasets and are not
prerequisites for analytics/dashboard/report parity.

## Calculations that must match

- Total calls, unit responses, response coverage, scheduled calls, latest-data
  timestamp, and average/median processing and response intervals.
- Dispatcher grouping by stable identifier with display-name fallback; volume,
  share, average/median processing, within 90 seconds, and over 180 seconds.
  Scheduled calls and intervals beyond one hour remain excluded from processing
  samples, with the existing non-dispatcher identity exclusion preserved.
- Daily, hourly, and weekday volume rendered in `America/New_York`, including
  daylight-saving transitions; agency mix and incident-type trends.
- Busiest units and stations, station-discipline classification and coverage,
  and the exact current unit-type/agency rules.
- Unit turnout, travel, total response, scene-to-transport, transport,
  destination turnaround, and commitment; agency processing, turnout, travel,
  and total response.
- Saved widget keys `daily_volume`, `hourly_volume`, `weekday_volume`,
  `agency_mix`, `incident_types`, `dispatcher_workload`, `busiest_units`, and
  `busiest_stations`.

## Sensitive fields and hard exclusions

The operator authorized exact CFS/unit identifiers, dispatcher identifiers, and
coordinates required for parity. They remain sensitive and require a private
encrypted target, tenant-bound least-privilege reads, auditability, approved
retention/deletion, TLS, and no row values in logs or evidence.

Caller names/phones, street addresses, narratives, medical details, command
logs, raw CAD payloads, recordings, credentials, backups, binaries/models, and
alert/page/tone/dispatch or other operational-output/control records remain
excluded.

## Source and target preflight gaps

No live source connection is approved or available. Before any run, a human
must name an exact replica or database-enforced read-only account, approve the
network path, UTC window, retention, target schema/version, access roles,
operator and maintenance window, and enter source/target connection material
through an approved runtime mechanism.

The target schema must match the five table projections, keys, constraints, and
indexes, and rebuild both views from reviewed SQL. Each encrypted batch needs
per-table counts, distinct-key counts, timestamp bounds, zero duplicate keys,
zero foreign-key orphans, a manifest hash, and reject reason classes without
row content. Final delta evidence must prove the accepted freshness threshold.

## Remaining work before live preflight

1. Add synthetic fixtures for every milestone, null/out-of-order timestamps,
   scheduled and one-hour outliers, dispatcher stable-ID grouping, coordinates,
   timezone/DST, station discipline, and saved widgets.
2. Add a deterministic source-versus-target parity harness for every aggregate,
   timeline, and saved view over identical synthetic windows.
3. Verify cloud schema/index/view parity and field-level access controls.
4. Record named data-owner, security, retention, audit-review, and cloud-delete
   approvals.
5. Keep all source operations read-only and all CAD/output capabilities disabled.

No live read, export, transfer, import, CAD poll, webhook, or source change is
authorized by this inventory.
