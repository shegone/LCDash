# Session handoff, 2026-11-05: Analytics "call processing" audit and fix (local commit only)

Follows `session-handoff-2026-10-26-release-120.md`. Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`; this work is a local commit on top of `51756ce` (not pushed; nothing built or deployed). | 2026-11-05 |
| LCDash pilot (AWS) | unchanged: foundation stack `UPDATE_COMPLETE` (2026-10-26), web task definition revision 120, digest `sha256:4d02e77f96ca0414b02e06ce25ad04f6243d760a0af4adaee41173d9111d45d1`; `AnalyticsCollectorEnabled=true`, schedule 30 min; collector runs today all `complete`, 0 failures. | 2026-11-05 (read-only) |
| Tests | application: `python -m pytest tests/` 2074 passed, 6 skipped, 2623 subtests passed; infrastructure: `python -m pytest infrastructure/tests/` 151 passed, 9 subtests passed. `tests/test_analytics_postgres_live.py` (3 tests, now including the four CFS timelines) **skipped**: no PostgreSQL reachable from this session. | 2026-11-05 |
| Decisions open | listed in `docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md` section 1.4 (definition, data-model change, cap, benchmarks, scheduled exclusion, duplicate identity). Ted's yardstick (2026-11-05): call added to CAD → someone added to the call, i.e. first Assign **or Stack**; Stack needs the command-log collection in audit section 5 (schema change, awaiting explicit approval). | 2026-11-05 |

## What was found

The dispatcher "CAD processing" interval ended at
`MIN(call_agency_times.dispatched_at)`, the per-agency "Dispatched" stamp,
and never read the per-unit record. A re-assign after arrival, a second leg,
or another unit's En route therefore ended the interval: the four compared
calls read 43:29 / 47:00 / 54:29 / 7:12 where the CAD report says 1:58 /
20:59 (2:17 to Stack) / 54:29 (7:59 to Stack) / 1:39. Live 90-day figures
showed the same for every dispatcher (35% of calls timed, 39% of timed calls
"over 3 min"), the three discipline tables were empty for the whole period
(classification compared agency ORIs with roster codes), response coverage
was 21% (per-agency on-scene only, scheduled calls included), busiest-unit
averages ran 37-48 minutes (scheduled transports included), and the slow-call
"Agency" column always read "911 Center / Administrative". Full list,
evidence and options: the audit document above.

Verified from the vendor's public OpenAPI document: `cfs_analytics` has **no
Stack stamp**; Stack exists only in the `cfs_core` command log
(`CFSCommand`: Timestamp, UnitNumber, Status, plus narrative fields that must
never be stored). `Reporter.HowReported` and `UnitType.Discipline` are also
available and not collected.

## What changed (commit on `main`, local)

- `app/services/analytics_reporting.py`: one shared CTE chain
  (`_call_processing_sql`, `ACTION_STAMPS_SQL`) gives every call
  `first_action_at` = earliest Assigned / En route / Staged / On scene from
  `unit_responses` ∪ `call_agency_times`, `first_on_scene_at`, and a
  `timing_status` of `timed` / `scheduled` / `no_stamp` / `over_cap`
  (`PROCESSING_CAP_SECONDS = 3600`). Overview processing, dispatcher summary,
  dispatcher rows, discipline metrics and the slow-call list all use it.
  Response averages and coverage exclude scheduled calls and use both
  records; busiest-unit average excludes scheduled calls; disciplines
  classify by unit roster agency / unit type with ORI fallback; the slow-call
  list returns the responding agency, the first action (event + unit or
  agency) and a separate `over_cap` list; exclusion counts are returned for
  the summary and each dispatcher.
- `templates/analytics.html`, `static/js/lcdash-analytics.js`
  (`?v=20261105-first-action`): definitions, the Stack limitation, a "Not
  timed" column with the split in its tooltip, a "Not timed:" summary line,
  the drill-down's "First action" column and its over-cap section.
- `docs/ANALYTICS_FOUNDATION.md`: definitions updated.
- `docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md`: new.
- Tests: `tests/test_analytics_first_action.py` (new, 14),
  `tests/test_analytics_slow_calls.py`, `tests/test_analytics_disciplines.py`
  updated for the new row shapes and rules (so were the two contract fakes in
  `tests/contracts/test_analytics_overview_authorization.py` and
  `tests/contracts/test_county_profile_agency_presentation.py`);
  `tests/test_analytics_postgres_live.py`
  gains `test_first_unit_action_reproduces_the_cad_timelines` (multi-unit
  fire, stack-then-enroute, held unit, re-assign after arrival, over-cap,
  no-stamp, scheduled, self-initiated zero; plus response, busiest-unit and
  discipline checks).

Not changed: warehouse schema, collector, normaliser, infrastructure,
anything on `.227` or `.15`.

## Not verified

- The new SQL has not run against a PostgreSQL. `pip install pgserver` (an
  embedded server for the live test) was blocked by the session's permission
  policy; no Docker or local server exists on this workstation. Run
  `LCDASH_TEST_PG_DSN="host=127.0.0.1 port=55432 user=postgres" python -m pytest tests/test_analytics_postgres_live.py -q`
  against any PostgreSQL 17 (as on 2026-10-21) before release. This is the
  main open risk; the query text was reviewed line by line.
- No signed-in page check of the new build (nothing was built).
- The warehouse rows for the four calls were not read (RDS is only reachable
  from the pilot VPC); the mechanism is established from the code and the
  page's API, see audit section 1.2.

## Exact next action

1. Ted: read audit section 1.4 and answer the six decisions (the code
   implements the recommended defaults; any change is a small edit).
2. Run the live PostgreSQL test file as above; fix anything it finds.
3. Release through the guarded path (AGENTS.md "Production deployment
   rules"): push `main` to both remotes (Ted's yes), full suites, release
   builder, CodeBuild with `IMAGE_TAG=release-<12 hex>`, ECR scan against
   the accepted baseline, named change set with only `PilotImageDigest`
   changing, execute only after Ted types the change-set name, verify ECS
   rollout, ALB target, signed-in page, and then compare the four CFS numbers
   in the drill-down with the audit table (expected 1:58, 20:59, 54:29,
   1:39; the first and last leave the "> 3 Min" list).
4. If Ted approves audit section 5, plan the `unit_status_events` table
   (command-log status entries only), `calls.how_reported`,
   `units.discipline`, and the option-C metric with a separate unit hold
   column, as its own work package.

Services were left running; nothing was stopped, started or changed on AWS,
`.227` or `.15`. The SSO device-code login was approved by Ted for read-only
checks only.
