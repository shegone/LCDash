# Session handoff, 2026-11-05 (evening): dispatcher discipline filter and not-timed call types

Follows `session-handoff-2026-11-05-analytics-processing.md` (release 121,
Stack collection live). Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`; this work is **committed locally only** on top of `70356ad` (see "Commit" below). Not pushed, not built, not deployed. | 2026-11-05 |
| LCDash pilot (AWS) | Unchanged since release 121: web task definition revision 121, collector 81, image `release-4fcc6457782c`, digest `sha256:14d3f80abe58c6f3c581d34b39a9d0aca801b823f39ccf79e97f70cb70a25764`. No AWS call was made this session. | 2026-11-05 16:10 EST (previous handoff) |
| Tests | application suite and infrastructure suite results are in the "Tests" section; `tests/test_analytics_postgres_live.py` still **skipped** (no PostgreSQL reachable from this workstation). | 2026-11-05 |

## What Ted asked for

1. "can we add a filter for all the dispatcher metrics to show by
   discipline. EMS, Fire, Police"
2. "lets remove a few of the call types that count against the
   dispatchers": Roundtrip, Take Home, Transfer / Inter-Facility, Contact,
   Move-Up, Shoplifting, Complaint, 911 Hang Up, Call Through Office, 911
   Open Line, Information, Be On the Lookout.

## What changed

### Discipline filter (All / EMS / Fire / Law)

- `app/services/analytics_reporting.py`: `DISCIPLINE_FILTERS`,
  `resolve_discipline` (anything but `""`, `ems`, `fire`, `law` raises
  `AnalyticsRangeError`, a 400 on the API). `ACTION_STAMPS_SQL` now gives
  every stamp a `discipline` from the acting unit's roster agency / unit
  type (the same CASE the discipline tables use; the per-agency stamp
  falls back to its ORI), and takes an optional `WHERE stamps.discipline =
  %(discipline)s`. With a discipline, `_call_processing_sql` keeps only the
  calls that have a stamp by a unit of that discipline (`EXISTS` on the
  filtered stamps) and times each to that discipline's first action.
  Applied to the dispatcher summary row, the dispatcher rows and the
  slow-call drill-down only; the overview metrics, charts and the
  by-discipline tables stay county-wide. The snapshot carries
  `discipline_key`, `discipline_label`, `discipline_options`.
- `app/main.py`: `discipline` query parameter on `/analytics`,
  `/api/analytics/overview` and `/api/analytics/dispatcher-slow-calls`
  (the page's range-error fallback passes `discipline=""`).
- `templates/analytics.html` (`?v=20261105-discipline`): All / EMS / Fire /
  Law links under the coverage chip (they keep the period or the custom
  start/end), the heading gains ": Fire" etc., and a "Showing Fire only"
  sentence explains the population.
- `static/js/lcdash-analytics.js`: the drill-down request carries the
  same `discipline`.
- The label is "Law", not "Police", to match the existing discipline
  tables (the law roster includes the sheriff, state police and DNR).

### Not-timed call types

- `EXCLUDED_CALL_TYPES` (the twelve names above), `call_type_key`
  (uppercase, A-Z and 0-9 only) and `EXCLUDED_CALL_TYPE_KEYS`. Every
  processing query computes `excluded_type` from
  `regexp_replace(UPPER(incident_description), '[^A-Z0-9]', '', 'g') =
  ANY(%(excluded_call_types)s)`, so "911 Hang Up", "911 HANG-UP" and
  "911 HANGUP" all match. `timing_status` gains `excluded_type`, decided
  before scheduled / no stamp / over cap. These calls **still count as
  calls entered** (workload) and appear in "Not timed"; they never enter an
  average, the "> 3 Min" count or the drill-down. The discipline tables
  (`_discipline_metrics`) drop them with the same expression.
- Row shapes: dispatcher summary row 13 columns (`excluded_type` last),
  dispatcher rows 15 (`excluded_type` then `dispatcher_ref`).
- Page: the "Not timed:" line starts with "N of a call type that is not
  timed (12 types)" (the list is in the tooltip), the per-dispatcher
  "Not timed" tooltip gains the split, and the definition note lists the
  twelve types and says they still count as entered.

### Docs and tests

- `docs/ANALYTICS_FOUNDATION.md`, `docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md`
  section 4: both features defined.
- `tests/test_analytics_first_action.py`: new `DisciplineFilterTests` (query
  text, parameters, snapshot, validation, the three routes, 400), the
  not-timed call-type test, page tests for the links, heading, custom
  range, "Not timed" wording and the tooltip; `tests/test_analytics_slow_calls.py`
  (parameters, discipline on the drill-down, script version);
  `tests/contracts/test_analytics_overview_route_wiring.py` (argument
  lists now include `discipline`); the recording fakes in four files
  widened by one column; `tests/test_analytics_postgres_live.py` gains
  two excluded-type calls ("Information" with a late unit, "911 HANG-UP"
  with none) and law / fire / EMS filter expectations.

## Tests

See the commit message for the exact counts of the run made before the
commit (`python -m pytest tests/` and `python -m pytest infrastructure/tests/`).
The live PostgreSQL file is skipped here; its new expectations were
derived by hand from the synthetic timelines and have **not** run against
a database.

## Not verified

- The CAD's exact wording of the twelve natures was not read from the
  warehouse (RDS is reachable only from the pilot VPC). The matcher ignores
  case, spaces and punctuation, but an abbreviation ("BOLO", "911 HU") would
  not match. After release, Ted can compare the "Not timed: N of a call
  type" figure with the Top Incident Types list; a nature that still shows
  slow calls in the drill-down needs its spelling added to
  `EXCLUDED_CALL_TYPES` (one line, test first).
- No PostgreSQL run of the new SQL (same gap as the previous handoff).
- No signed-in page check (nothing built).

## Exact next action

1. Ted: review the twelve names against the CAD nature list and the "Law"
   label; say "push main" and "start release" if wanted. The release
   path is the guarded one in AGENTS.md (release-builder deploy, CodeBuild
   `IMAGE_TAG=release-<12 hex of HEAD>`, ECR scan against the accepted
   baseline, named change set on the foundation stack with only
   `PilotImageDigest` changing, executed only after Ted types its name).
2. Run `tests/test_analytics_postgres_live.py` against any PostgreSQL 17
   before or right after the release; fix anything it finds.
3. Still open from the previous handoff: Stack backfill of older calls,
   `HowReported`, `UnitType.Discipline` (each a separate approval).

Services were left running; nothing was changed on AWS, `.227` or `.15`.
