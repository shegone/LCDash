# Session handoff, 2026-11-05: Analytics "call processing" audit and fix (local commit only)

Follows `session-handoff-2026-10-26-release-120.md`. Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main` at `a747596` (`3d182c5` first-action metric, `5d07c98` yardstick note, `a747596` Stack collection), **pushed to GitHub and Bitbucket on 2026-11-05** at Ted's "Yes, push main". Nothing built or deployed; the release-builder has not run for this commit. | 2026-11-05 |
| LCDash pilot (AWS) | **Released.** Foundation stack `UPDATE_COMPLETE` 20:39:58Z (15:39 EST) via change set `release-4fcc645-20261105` (Ted typed the name; executed 20:36:59Z). Web task definition **revision 121**, collector revision 81, both image tag `release-4fcc6457782c`, digest `sha256:14d3f80abe58c6f3c581d34b39a9d0aca801b823f39ccf79e97f70cb70a25764`. ECS rollout COMPLETED 1/1; ALB target `10.42.1.175:8000` healthy; `/active-calls` 302 to Cognito; 55 web-log events after the rollout, 0 errors. Of 23 stack parameters only `PilotImageDigest` changed; five resources modified. | 2026-11-05 15:42 EST |
| Rollback target | change set with digest `sha256:4d02e77f96ca0414b02e06ce25ad04f6243d760a0af4adaee41173d9111d45d1` (revision 120). | |
| Release build | CodeBuild `lcdash-p1-logan-use1-release-builder:1a3a5a1a-e419-4e87-9bbb-7935d7a0193b`, SUCCEEDED 14:52:53 to 14:53:43 EST; `source_asset_sha256` `e6814cee9d258fe1018a75285b099e244068e1f4f58d431aa8f43c91bb10aa8e` (matches the release-builder deploy output). | 2026-11-05 |
| ECR scan (new digest) | COMPLETE 14:57 EST: 9 findings, exactly the accepted baseline (util-linux 2.42.1-r0 CVE-2026-27456/53612/53613/53614/76642/78408/78409/78410, zlib 1.3.2-r0 CVE-2026-85091). | 2026-11-05 |
| `unit_status_events` on the pilot | **Confirmed in use**: the first collector run on revision 81 (task created 16:07:06 EST, stopped 16:08:04, exit 0) logged `status: complete, calls_stored: 26, analytics_failures: 0` for window 18:37Z to 21:07Z. `upsert_bundle` writes the status rows for every stored call and a database error would have failed the run, so the table exists and accepts rows. Row count not read (RDS is reachable only from the VPC); Ted can read `unit_status_events_stored` from `/api/analytics/status` when signed in. | 2026-11-05 16:10 EST |
| Tests | after the Stack commit: application `python -m pytest tests/` 2078 passed, 6 skipped, 2623 subtests passed; infrastructure `python -m pytest infrastructure/tests/` 151 passed, 9 subtests passed. `tests/test_analytics_postgres_live.py` (3 tests, including the four CFS timelines with Stack) **skipped**: no PostgreSQL reachable from this session. | 2026-11-05 |
| Decisions open | listed in `docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md` section 1.4 (definition, data-model change, cap, benchmarks, scheduled exclusion, duplicate identity). Ted's yardstick (2026-11-05): call added to CAD → someone added to the call, i.e. first Assign **or Stack**. Ted approved the command-log collection ("Yes, add Stack") the same day; implemented, see below. Still open: `HowReported`, `UnitType.Discipline`, the backfill of older calls, cap, benchmarks, duplicate identity. | 2026-11-05 |

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

### Stack collection (second commit, approved by Ted 2026-11-05)

- `database/analytics_schema.sql`: new table
  `lcdash_analytics.unit_status_events (cfs_number, event_order,
  unit_number, status_code, status_description, event_kind, occurred_at)`
  plus an index; created idempotently by `initialize_schema()` on the next
  app start / collector run (no CDK or RDS change).
- `app/services/analytics_models.py`: `classify_status_event` and
  `_status_events` read the `cfs_core` `CommandLog[]` rows that have a
  `Status` and a `Timestamp` (the collector already received them in every
  `cfs_core/search` result; no new API call). `Text`, `Creator`,
  `Destination` and `Tag` are never read. `event_kind` is decided by the
  status's `ConsiderAs*` flags, else its wording; any status word starting
  `STACK` is `stacked`, "Unstack" is not, "Arrived" alone is nothing.
- `app/services/analytics_database.py`: `upsert_bundle` replaces the
  call's status events; `status()` reports `unit_status_events_stored`.
- `app/tools/phase1_schema_initializer.py`: the new table is added to
  `APPROVED_OBJECTS` (the Phase 1 schema allowlist), with the approval
  date in a comment.
- `app/services/analytics_reporting.py`: `ACTION_STAMPS_SQL` adds the
  status events (Stack ranked before an Assign at the same second);
  `first_dispatch_at` and `hold_seconds` (Stack → first non-Stack action)
  on every call row; dispatcher summary and rows gain `stacked_calls` and
  `average_hold` (summary row 12 columns, dispatcher rows 14, ref last);
  discipline tables include the status events by the stacked unit's roster
  discipline; the drill-down returns `hold` and labels "Stacked".
- `templates/analytics.html` (`?v=20261105-stack`), `static/js/lcdash-analytics.js`:
  "Stacked" column (average hold in the tooltip), "Stacked first" summary
  line, "Unit hold" drill-down column, note rewritten.
- Tests: `tests/test_analytics.py` (status-event normalisation, narrative
  exclusion, classification, schema), `tests/test_analytics_first_action.py`,
  `tests/test_analytics_slow_calls.py`, the two contract fakes, and
  `tests/test_analytics_postgres_live.py` (expects 1:58 / 2:17 / 7:59 /
  1:39, holds 18:42 and 46:30, law 5 timed).

Not changed: collector loop, infrastructure, anything on `.227` or `.15`.
Older calls are not backfilled: Stack appears only for calls collected
after the release (audit section 5 item 5).

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
3. Released (see the state table). Still Ted's: the signed-in page check
   (name and role render; the dispatcher table shows the "Stacked" column
   and the "Stacked first" line; the four CFS numbers in the drill-down
   read 1:58 / 20:59 / 54:29 / 1:39 — the older two stay at their first
   Assign because they were collected before Stack existed).
4. After the release, confirm on the pilot that `unit_status_events` fills
   (`/api/analytics/status` → `unit_status_events_stored` rising with each
   collector run) and that a known stacked call shows "Stacked" with a
   hold in the drill-down. If the live CAD's Stack status is worded
   differently from "Stack"/"STACKED", `classify_status_event` needs that
   word (one-line change, test first).
5. Separate work packages, each needing Ted's approval: the bounded
   backfill of closed calls (audit section 5 item 5), `calls.how_reported`
   and `units.discipline` (items 2 and 3).

## Release notes learned today

- The foundation CDK app is `python cdk_app.py` (the default in
  `infrastructure/cdk.json`), not `python app.py` as the 2026-08-08 plan
  wrote; the first prepare attempt failed in synth and created nothing, so
  the change-set name stayed free. The release-builder app is
  `python release_builder_app.py`.
- A collector run that starts in the minute the change set executes still
  uses the old task definition revision; the new table is created by the
  first run on the new revision, about 30 minutes later.

Services were left running; nothing was stopped, started or changed on AWS,
`.227` or `.15`. The SSO device-code login was approved by Ted for read-only
checks only.
