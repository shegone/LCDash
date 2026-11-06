# Analytics "call processing" audit, 2026-11-05

Scope: the Analytics page (`/analytics`) Dispatcher / CAD Entry section
("CAD processing", "> 3 Min", slow-call drill-down), the discipline tables,
and every other figure on the page. Trigger: the page's "over 3 minutes from
call received to assigned" counts did not agree with CentralSquare CFS "Unit
Response Times" reports. Four reports for one call-taker were compared locally;
no report content (names, phones, addresses, narrative) is reproduced here,
only CFS numbers, unit numbers and timestamps, which the page itself shows.

Decisions Ted must make are collected in section 1.4. The change implemented
today is the safest default; it needs no warehouse, collector or
infrastructure change.

## 1. Decision: what should "dispatcher call processing" measure?

### 1.1 What the CAD data can say

Verified from the vendor's public OpenAPI document
(`https://api-wv-logan-911.centralsquarecloudgov.com/api/cad/v1/openapi.json`,
read without credentials on 2026-11-05) and from the code:

| Source | Carries | Collected today |
| --- | --- | --- |
| `GET /cfs_analytics/{CFSNumber}` → `CallTimes[]` (one per `AgencyORI`) | Dispatched, Enroute, Staged, OnScene, AtPatient, BackupEnroute, BackupArrived, Leaving, Transporting, ArrivedAt, Available, InQuarters | yes → `call_agency_times` |
| same → `Unit[]` (one per unit) `UnitTimes` | the same twelve stamps per unit; `UnitType.Discipline` | stamps yes → `unit_responses`; `Discipline` no |
| `cfs_core` record → `CommandLog[]` (`CFSCommand`: Timestamp, Creator, UnitNumber, Text, Status, Destination, Tag) | every unit status change including **Stack**, with its timestamp | no (deliberately excluded: `Text` is narrative) |
| `cfs_core` record → `Reporter.HowReported` | how the call came in (911, phone, radio, officer…) | no |
| `cfs_core` record → `Unit[].IsStacked` | current stacked flag only | no |
| `cfs_core` record → `CallDateTime` | CAD call-creation time ("New CFS" / "Report Time" on the report; identical in all four reports) | yes → `call_received_at` |

So: **`cfs_analytics` has no Stack stamp; Stack exists only in the command
log**, as a status entry with a unit number and timestamp. The 911 answer
time is not in CAD at all (it is an NGA911 figure).

How CentralSquare itself reports the call: the report's call-level "Assigned"
is the first unit Assign, or, when a unit went straight to Enroute, that
Enroute marked `*` (derived). It never uses a later re-Assign.

### 1.2 What the four calls show (timeline only)

| CFS | Received (CAD) | Unit events (from the report) | CAD report "Assigned" | Page showed | Page used |
| --- | --- | --- | --- | --- | --- |
| 26-33052 fire | 18:54:00 | FC800A Assign 18:55:58, E83 Enroute 19:01:30, FC800A Enroute 19:05:24, E83 On scene 19:14:35, DOF5650 Enroute 19:37:29, SO20 Stack 19:55:03, SO20 On scene 20:16:42 | 18:55:58 (1:58) | 43:29 | DOF5650 Enroute 19:37:29, the only per-agency `Dispatched` stamp at or after the received time (fire unit's Assign never reached `call_agency_times`) |
| 26-34950 theft, radioed in by the unit | 10:21:58 | SO14 Stack 10:24:15, Enroute 10:42:57, On scene 10:55:57, Assign 11:08:59, Complete 11:09:02 | 10:42:57 `*` (20:59) | 47:00 | the 11:08:59 Assign, entered after the unit was already on scene |
| 26-36232 911 hang-up | 10:44:10 | SO22 Stack 10:52:09 (unit asked to keep it stacked), Assign 11:38:39, Complete 11:39:47 | 11:38:39 (54:29) | 54:29 | the Assign; agrees with CAD, but 46 of the 54 minutes were unit hold |
| 26-36335 MVA | 07:50:49 | SO20 Enroute 07:52:28, Complete 07:56:22, Assign 07:58:01, Complete 08:00:27 | 07:52:28 `*` (1:39) | 7:12 | the 07:58:01 Assign, a second leg after the first was complete |

The page value equals the received time plus the displayed interval in every
case, so `call_received_at` is right and the defect is in which stamp ends
the interval. The reason the fire unit's 18:55:58 Assign was missed cannot be
proven without the stored rows (the warehouse is only reachable from the
pilot VPC), but the mechanism is clear: the query reads only
`call_agency_times.dispatched_at`, one row per `AgencyORI`, and
`normalize_analytics_bundle` drops a `CallTimes` row whose `AgencyORI` is
blank. Whatever the fire agency's row held, the per-unit record for FC800A
in `unit_responses` is never consulted. Reading both records fixes it
regardless of which explanation holds.

### 1.3 Options

**Metric definition**

| Option | Definition | 33052 | 34950 | 36232 | 36335 | Needs |
| --- | --- | --- | --- | --- | --- | --- |
| A (status quo) | received → `MIN(call_agency_times.dispatched_at)` | 43:29 | 47:00 | 54:29 | 7:12 | nothing |
| **B (implemented today)** | received → earliest Assigned / En route / Staged / On scene by **any unit or agency**, from `unit_responses` ∪ `call_agency_times` | **1:58** | 20:59 | 54:29 | **1:39** | reporting SQL only |
| C (recommended end state) | received → earliest **Stack**, Assign or En route by any unit, from a new privacy-minimised unit-status-event table built from the command log (timestamp, unit, status only; no text, no creator) | 1:58 | **2:17** | **7:59** | 1:39 | Ted's approval of a schema + collector change (section 5) |
| D | received → first En route only | 7:30 | 20:59 | 55:37 | 1:39 | nothing; rejected: measures turnout, not the call-taker |

B matches CentralSquare's own report on three of four calls and on the fourth
(36232) agrees with CAD's "Assigned" but still charges unit hold time, which
no definition can separate without Stack. C is what a 911 centre actually
wants: the call-taker's interval ends when a unit is first tied to the call
(stacked, assigned or self-dispatched); what follows is unit availability.

**Stacked / held calls (until Stack is collected)**

| Option | Effect | Verdict |
| --- | --- | --- |
| 1. Charge to the call-taker, say so | averages and ">3 min" counts for law calls stay inflated | **chosen as the honest default**; the page now states it and the drill-down shows which unit event ended the interval, so a supervisor can tell a hold from a slow entry in one click |
| 2. Lower the cap (for example 15 minutes) | hides held calls from averages; also hides a real 20-minute entry delay | rejected: the cap is a visibility problem already (section 2.7) |
| 3. Heuristic "likely held" (low priority and long interval) | guesses | rejected: never invent an event |
| 4. Collect Stack (option C) | correct | recommended next step |

**Self-initiated / radioed-in calls** (34950 was radioed in by the responding
unit): with `HowReported` collected (a code, no caller data) these can be
left out of call-taker timing or shown separately. Not possible today.

### 1.4 Decisions for Ted

**Yardstick stated by Ted on 2026-11-05:** the public judges the centre on
the time from the call being added to CAD to the time someone is added to
the call. In CAD terms that is `CallDateTime` (the warehouse's
`call_received_at`, the report's "Received") to the first **Assign or
Stack** of any unit, or the first En route / Staged / On scene when a unit
skipped Assign (the report marks that `*`). Definition B (implemented)
matches this exactly except that it cannot see Stack, because Stack exists
only in the `cfs_core` command log, which the collector already receives
in every `cfs_core/search` result and the normaliser discards. Collecting
it (section 5, items 1 and 4; no new API call) makes 34950 read 2:17 and
36232 read 7:59, and lets the unit's own hold be shown apart. That is the
remaining step to the lowest *accurate* time; nothing else in the data can
shorten the interval honestly. **Ted approved the schema change on
2026-11-05 ("Yes, add Stack")**; section 5 records what was implemented.

1. Accept definition B now (received → earliest unit action, any agency or
   unit, Assigned/En route/Staged/On scene). Implemented; can be reverted.
2. Approve the data-model change in section 5 (unit status events from the
   command log, `HowReported` code, `UnitType.Discipline`) so option C and
   a separate "unit hold time" become possible. **Approved 2026-11-05 for
   the status events (Stack); implemented as section 5 items 1 and 4.**
   `HowReported` and `UnitType.Discipline` remain open.
3. Keep the 60-minute cap on averaged intervals (page now shows how many
   calls it removed, and lists them in the drill-down marked "not averaged")
   or choose another value.
4. Local benchmarks: the page's own 90-second and 3-minute lines are kept;
   NFPA 1221 (64 s / 106 s) remains in the discipline tables. Say if the
   dispatcher table should move to the NFPA figures.
5. Scheduled calls are now excluded from response-time averages and the
   busiest-unit average (they were not). Confirm.
6. Two dispatcher rows share the same initials (one with 1,241 calls, one
   with 377 over 90 days). If that is one person under two CAD identities,
   say so; the rows can be merged by the CAD identifier only with a named
   mapping, which the agent must not guess.

## 2. Verified facts

Sources: code on `main` at `51756ce`, the live page read through the
signed-in session (GET only, 2026-11-05, 90-day and 30-day windows),
read-only AWS state through `lcdash-phase1-deployment`, and the vendor
OpenAPI document.

### 2.1 What populates the warehouse

- Foundation stack `lcdash-p1-logan-use1-foundation` is `UPDATE_COMPLETE`
  (2026-10-26); parameters `AnalyticsCollectorEnabled=true`,
  `AnalyticsCollectorScheduleMinutes=30`; web task definition revision 120.
- The scheduled Fargate task runs `python -m app.tools.cloud_analytics_collector`
  → `run_analytics_sync` → `cfs_core/search` (closed calls) then
  `cfs_analytics/{cfs}` per call → `normalize_analytics_bundle` →
  `upsert_bundle`. Log group `/lcdash/lcdash-p1-logan-use1/analytics-collector`
  shows a run every 30 minutes today, every one `status: complete`,
  `analytics_failures: 0` (3 to 24 calls per run, counts only).
- Older history came from the authorised Phase 2 import of the on-premises
  warehouse, which used the same normaliser, so the row shape is identical.
- `events:DescribeRule` and `cloudformation:ListStacks` are not in the
  deployment permission set (describe-stacks by name is).

### 2.2 Live figures (90 days, 2026-08-07 to 2026-11-05)

- 10,590 calls; 12,581 unit responses; 669 scheduled.
- Dispatcher section: 9,977 attributed calls, **3,471 timed (35%)**, average
  4:10, median 2:12, p90 9:25, 41% within 90 s, **1,366 over 3 min (39% of
  timed)**. Every dispatcher shows 32-57% within 90 s. These are not one
  person's numbers; the definition is wrong for everyone.
- One dispatcher shows 0:00 average and 100% within 90 s from a single call
  (a unit stamp equal to the received time: a self-initiated call).
- Discipline tables: fire, EMS and law all "No timed … calls" for the whole
  90 days (`iso_fire.timed = 0`), although 187 FC 1000, 114 FC 300, 5,463
  LEASA and 1,567 LCSO calls exist.
- Overview: "Call processing" 5:19 (no exclusions at all), average response
  14:07, median 6:02, **response coverage 21%**.
- Busiest units: seven MED units with 800-930 responses each and average
  "response" of 37 to 48 minutes.
- Station/discipline quality: 12,581 of 12,581 responses classified (100%)
  by unit roster agency and unit type; 167 without a station.
- Slow-call drill-down for the call-taker in question: 110 calls over 3 min
  in 90 days; the "Agency" column reads "911 Center / Administrative" for
  all 110.

## 3. Defects found

| # | Defect | Where | Effect | Fix today |
| --- | --- | --- | --- | --- |
| 1 | First dispatch = `MIN(call_agency_times.dispatched_at)`; per-unit stamps ignored; a later Assign (re-assign after arrival, second leg) or another unit's Enroute wins | `_query_overview` ×3, `_discipline_metrics`, `_query_slow_calls` | 33052: 43:29 for 1:58; 36335: 7:12 for 1:39; 34950: 47:00 for 20:59 | earliest of Assigned/En route/Staged/On scene over both tables |
| 2 | Unit hold (stacked, "keep it stacked on me") charged to the call-taker | same | 36232: 54:29 for 7:59; law calls generally | cannot fix without Stack; stated on the page, decision 2 |
| 3 | Stack not collected | `TIME_FIELDS`, schema | 2 | not in `cfs_analytics`; command log plan, section 5 |
| 4 | Discipline classification compares `agency_ori` with roster codes (`'FC %'`, `LEASA`, `LCSO`…); ORIs do not match | `_discipline_metrics` | all three tables empty for 90 days; ISO fire view silent | classify by the responding unit's roster agency / unit type (what station-discipline already does with 100% coverage), ORI kept as fallback |
| 5 | Only 35% of calls timed, exclusions invisible | dispatcher table | supervisors cannot see whether a row is 10 calls or 500 | `timing_status` per call: timed / scheduled / no_stamp / over_cap, counts in the summary and per row, "Not timed" column |
| 6 | 60-minute cap silently removes calls from both the count and the drill-down | `_query_slow_calls` | real held calls disappear | over-cap calls listed in the drill-down marked "not averaged"; count shown |
| 7 | Overview "Call processing" has no scheduled exclusion and no cap | `_query_overview` metrics row | 5:19 vs 4:10 for the same data | same `timed` rule everywhere |
| 8 | Response time uses only per-agency on-scene; scheduled calls included | metrics row | 21% coverage; averages pulled up by transports | earliest on-scene from both tables; scheduled excluded; coverage = samples / non-scheduled calls |
| 9 | Busiest-unit "Avg Response" includes scheduled transports | unit_rows | MED units 37-48 min | scheduled excluded, header says so |
| 10 | Slow-call "Agency" = `dispatch_agency` (always the 911 centre) | `_query_slow_calls` | useless column | responding agency (same field as Calls by Agency) |
| 11 | Drill-down does not say which event ended the interval | `_query_slow_calls`, JS | cannot audit against CAD | "First action" column: event and unit (or agency) |
| 12 | `within_90` filters differ from the other aggregates (no NULL / cap test) | dispatcher queries | harmless today (≤ 90 s implies inside the cap) but fragile | one `timing_status` column feeds every aggregate |
| 13 | API `agency_mix` returns the raw `LCEOC` label through `/api/analytics/overview` while the page maps it | `_agency_display_label` with a county profile | cosmetic | not changed (out of scope; noted) |

Not defects, but limits to state: `call_received_at` is CAD creation, not
911 answer; CAD has no 911 answer time. Self-initiated calls time at 0 s and
count as "within 90 s".

## 4. Definitions now on the page

- **CAD processing**: `call_received_at` (CAD `CallDateTime`) to the
  earliest of `dispatched_at`, `enroute_at`, `staged_at`, `on_scene_at` at
  or after the received time, across `unit_responses` and
  `call_agency_times` for the call.
- **Timed**: call type not on the not-timed list, not scheduled, has such
  a stamp, interval ≤ 60 minutes. Only timed calls enter the average,
  median, 90th percentile, "≤ 90 s" and "> 3 Min". The four excluded
  buckets are counted in the summary line, in the "Not timed" column
  (tooltip gives the split) and over-cap calls are listed in the
  drill-down as "not averaged".
- **Not-timed call types** (added 2026-11-05 at Ted's request; Motorist
  Assist and Public Works / Utilities on 2026-11-06; Vehicle Lock Out /
  Lock In was added and removed the same day and is timed): Roundtrip,
  Take Home, Transfer / Inter-Facility, Contact, Move-Up, Shoplifting,
  Complaint, 911 Hang Up, Call Through Office, 911 Open Line, Information,
  Be On the Lookout, Motorist Assist, Public Works / Utilities. Matched on
  `incident_description` ignoring case,
  spaces and punctuation (`EXCLUDED_CALL_TYPES`); the calls still count as
  entered and are left out of the discipline tables as well. The CAD's
  exact wording of these natures was not read from the warehouse; if a
  nature is spelt differently there (an abbreviation rather than the full
  name), the list needs that spelling too and the page's "Not timed"
  summary will show the count staying low.
- **Discipline filter** (added 2026-11-05): the dispatcher section can be
  narrowed to EMS, Fire or Law. The filtered population is the calls with
  at least one stamp by a unit of that discipline (roster rule above,
  ORI fallback); the same timed rule and not-timed buckets apply. The
  overview metrics and charts are not filtered.
- **Discipline timing** (changed 2026-11-06): in the filtered view and in
  the by-discipline tables a call is timed to the first unit action on the
  call by **any** discipline, not to the filtered discipline's own first
  action. The first unit assigned stops the clock. Until 2026-11-06 the
  interval ended at that discipline's first action, which charged a
  discipline added to a call later (fire requested after a medic was on
  scene, for example) with the whole wait before it was requested; the
  warehouse holds no per-agency "requested" time, so the first unit on the
  call is the only defensible end point.
- **By discipline**: a call counts once per discipline whose units acted on
  it; discipline from the unit roster agency / unit type (EMS: LEASA or
  `EMS *` unit type; fire: `FC *` or `FIRE *`; law: sheriff and police
  codes, `PATROL CAR`, `COUNTY ADMIN`), ORI as fallback; interval to the
  earliest action by any unit on the call (above); same timed rule.
- **Response**: received to the earliest on-scene stamp from either table;
  scheduled calls excluded; coverage = calls with a sample / non-scheduled
  calls.
- **Busiest units, Avg Response**: unchanged formula
  (`unit_response_metrics.total_response_seconds`) with scheduled calls
  excluded.
- **Drill-down**: same population as "> 3 Min", plus a separate list of
  over-cap calls; each row names the first action (event and unit/agency)
  and the responding agency.

Expected effect on the four calls: 1:58, 20:59, 54:29 (listed, with "Assigned
SO22" shown so the hold is recognisable), 1:39. Two of the four leave the
"> 3 Min" list; the other two stay until Stack is collected.

## 5. Data-model plan (items 1 and 4 approved and implemented 2026-11-05)

Goal: option C and a separate "unit hold time", plus self-initiated-call
handling, without adding narrative or caller data to the warehouse.

1. **Done.** New table `lcdash_analytics.unit_status_events (cfs_number,
   event_order, unit_number, status_code, status_description, event_kind,
   occurred_at)` populated by `normalize_analytics_bundle` from the
   `cfs_core` `CommandLog[]` entries that have a `Status` and a
   `Timestamp` (`database/analytics_schema.sql`,
   `app/services/analytics_models.py`, `AnalyticsRepository.upsert_bundle`).
   `event_kind` is `stacked` / `assigned` / `enroute` / `staged` /
   `on_scene` / `''`, decided by the status's `ConsiderAs*` flags when
   present and by its wording otherwise; Stack has no flag, so any status
   word beginning `STACK` (CAD's "Stack", "STACKED") is `stacked`, and
   "Unstack" is not. `Text`, `Creator`, `Destination` and `Tag` are
   **never** read (`tests/test_analytics.py` proves it). No new API call:
   the collector already received the command log in every
   `cfs_core/search` result and discarded it. Rows exist only for calls
   collected after this release; earlier calls have no Stack (see 5).
2. `calls.how_reported` (text code from `Reporter.HowReported.Description`,
   nothing else from `Reporter`).
3. `units.discipline` from `UnitType.Discipline` in `cfs_analytics`.
4. **Done** (except the self-initiated part, which needs item 2).
   Reporting: processing = received → earliest of Stack, Assign, En route,
   Staged or On scene from `unit_status_events` ∪ `unit_responses` ∪
   `call_agency_times` (`ACTION_STAMPS_SQL`); `hold_seconds` = that first
   action → the first non-Stack action when the call was stacked first,
   shown as the "Stacked" column (count, with the average hold in the
   tooltip), the "Stacked first" summary line and the drill-down's "Unit
   hold" column; it is never in the processing figures. The discipline
   tables time a Stack by the stacked unit's roster discipline.
5. **Open.** Backfill: the collector's overlap re-scan only covers recent
   calls, so the 90-day page shows Stack only for calls collected after
   the release. History would need a bounded re-read of closed calls
   through the reviewed read connector (read-only, rate-limited; one
   `cfs_core/{cfs}` read per call, no `cfs_analytics` call), to be sized
   and approved separately.
6. **Done** for the status events: normaliser excludes `Text` / `Creator`
   / `Destination` / `Tag`; schema idempotent; the live PostgreSQL test
   now expects 1:58 / 2:17 / 7:59 / 1:39 with holds of 18:42 and 46:30.

Infrastructure impact: none beyond a normal release (no new resource types;
the collector task definition is already in the standard change-set scope).

## 6. Verification status

- Recording-fake tests cover the query text, the result shapes and the page.
- `tests/test_analytics_postgres_live.py` carries the four synthetic
  timelines (stack-then-enroute, held unit, re-assign after arrival,
  multi-unit) and the discipline / response / busiest-unit rules against a
  real PostgreSQL; it runs only with `LCDASH_TEST_PG_DSN`. No PostgreSQL
  was reachable from this session (no Docker, no local server; installing
  an embedded one was blocked by the permission policy), so the SQL is
  reviewed, not executed, until Ted runs that file or the change is on the
  pilot. This is the main open risk.
- The live warehouse was not read directly; the page's API was.
