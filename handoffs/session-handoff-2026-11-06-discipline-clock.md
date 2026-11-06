# Session handoff, 2026-11-06: discipline views stop the clock at the first unit assigned

Follows `session-handoff-2026-11-06-release-123.md` (releases 122 and
123). Facts first; everything below was read from Git, the test runners
and a local PostgreSQL 17.11. No AWS state was changed in this stretch.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`; the handoff for release 123 (`3b80e60`) is pushed to GitHub and Bitbucket. This change is a **local commit on top of it, not pushed** (see "Exact next action"). | 2026-11-06 |
| LCDash pilot (AWS) | **Release 123 still live** (web revision 123, collector 83, digest `sha256:2fcfb972...`). This change is not deployed; the live Fire / EMS / Law views still time a call to that discipline's own first action. | 2026-11-06 |
| Tests | Focused: `tests/test_analytics_first_action.py`, `tests/test_analytics_slow_calls.py`, `tests/test_analytics.py` and `tests/test_analytics_postgres_live.py` (3 live tests against PostgreSQL 17.11) all pass. Full: `python -m pytest tests/` 2089 passed, 6 skipped, 2623 subtests; `python -m pytest infrastructure/tests/` 151 passed, 9 subtests. | 2026-11-06 |
| Local PostgreSQL | Started again from the session scratchpad for the live test; stopped at the end of the session. | 2026-11-06 |

## What changed and why

Ted checked a call the Fire view listed as far over three minutes. It was a
medical call: the medic was assigned about two minutes after the call was
created, a deputy a few minutes later, and the fire units about half an
hour in, after the medic and deputy were on scene. The Fire view (release
123) timed the call from creation to the first **fire** unit action, so it
charged the dispatcher with the whole half hour, although the fire request
was handled within about a minute of being made. The warehouse holds no
per-agency "requested" or "added" time, so there is nothing to start a
discipline's own clock from.

Ted's rule, applied in this change: **the first unit assigned on the call
stops the clock, whatever that unit's discipline.** A discipline view
chooses which calls count (the calls that discipline's units acted on); it
no longer changes how a call is timed.

Code (`app/services/analytics_reporting.py`):

- `_call_processing_sql`: the discipline no longer filters `action_stamps`
  (the `__STAMP_FILTER__` placeholder is gone). The `EXISTS` on
  `selected_calls` now carries the discipline test
  (`AND action_stamps.discipline = %(discipline)s`), so the population is
  unchanged and the milestones come from every stamp on the call. The
  dispatcher table, its KPIs and the "> 3 Min" drill-down all go through
  this builder.
- `_discipline_metrics` (the by-discipline tables and the ISO fire view):
  a new `call_first_action` CTE takes the first stamp by any unit per call;
  `discipline_dispatch` is now a `SELECT DISTINCT` of (call, discipline)
  joined to it, instead of a per-discipline `MIN`.
- Comments, docstrings, `docs/ANALYTICS_FOUNDATION.md`, the audit document
  and the two notes on `templates/analytics.html` say the new rule.

Consequences on the page: in the Fire, EMS and Law views and the
by-discipline tables, a call where a second discipline was added later is
now timed to the first unit on the call, so the figure is the same in
every view the call appears in. A call's hold (Stack to dispatch) is also
the same in every view. The All view is unchanged.

## Tests

- `tests/test_analytics_first_action.py`: `DisciplineFilterTests` now
  asserts the discipline appears once in the query (in the `EXISTS`), that
  `WHERE stamps.discipline` is gone and that `call_milestones` carries no
  discipline condition; a new test pins the `call_first_action` CTE and
  the `DISTINCT` join; the page-wording test follows the new note.
- `tests/test_analytics_slow_calls.py`: the drill-down query is checked the
  same way.
- `tests/test_analytics_postgres_live.py` (synthetic data, real
  PostgreSQL): the joint fire-and-EMS call is timed at 50 s in EMS too;
  on the synthetic fire call, law is now timed to the engine's Assign
  (1:58), so law's over-three-minute count drops from 2 to 1, the law
  filter's median is 1:58, its stacked count is 2 and its slow-call list
  holds only the held call.

## Not verified

- Not deployed, so not seen on the live page. After release the Fire view
  of that call should show the medic's two minutes, not the half hour.
- The CAD `CallDateTime` the collector stores as `call_received_at`
  matched the report's "Report Time" on the checked call, a round minute
  48 s before the command log's "New CFS" entry; on the four earlier
  reports the two agreed. Every interval on that call is therefore 48 s
  longer than the log supports. Whether `CallDateTime` is an editable
  field in CAD, and whether the log's "New CFS" stamp should be the call
  start instead, is an open question for Ted and CentralSquare; changing
  it is a collector change and needs an approval package.
- Whether the CAD per-agency rows expose a received or added time (which
  would allow a true per-discipline clock) was not checked: it needs a
  look at a live payload.

## Exact next action

1. Ted decides whether to push and release. Release path exactly as in
   `session-handoff-2026-11-06-release-123.md` ("Release path as
   executed"): push, release-builder deploy, CodeBuild with
   `IMAGE_TAG=release-<12 hex>`, ECR scan with an **empty** accepted
   list, named change set with only `PilotImageDigest` changing, execute
   only after Ted types the name, verify.
2. After release, Ted checks the Fire view of the call that started this
   and the by-discipline tables.
3. Open items unchanged: Stack backfill of older calls, `HowReported`,
   `UnitType.Discipline`, the AGENTS.md note about the empty accepted ECR
   list and the live PostgreSQL test for reporting SQL.
