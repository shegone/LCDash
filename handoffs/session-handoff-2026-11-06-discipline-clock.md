# Session handoff, 2026-11-06: discipline views stop the clock at the first unit assigned

Follows `session-handoff-2026-11-06-release-123.md` (releases 122 and
123). Facts first; everything below was read from Git, the test runners
and a local PostgreSQL 17.11. No AWS state was changed in this stretch.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main` at `c689892` (this change), pushed to GitHub and Bitbucket. The handoff update that records release 124 is a later local commit. | 2026-11-06 10:25 EST |
| LCDash pilot (AWS) | **Release 124 live.** Foundation stack updated via change set `release-c689892-20261106` (Ted typed the name; executed 15:34:16Z, 10:34 EST; `UPDATE_COMPLETE`). Web task definition **revision 124**, collector **revision 84**, both image tag `release-c6898920b59e`, digest `sha256:4818358bdea974ee83e7bfcb55a71c6429fd6df5055a7ac3d953838de7d7a0fe`. Of 23 stack parameters only `PilotImageDigest` changed; the standard five resources were modified. Rollback target: release 123's digest `sha256:2fcfb9727e4dd49688a7b6e17c6fc44f9d05a09b1dccf05d49e6f8adf3a02fe0`. | 2026-11-06 10:40 EST |
| Release 124 build | CodeBuild `lcdash-p1-logan-use1-release-builder:93e69eca-8793-4ce6-83eb-5b25d323706c`, SUCCEEDED 10:29:33 to 10:30:19 EST; `source_asset_sha256` `57fbf34d299cfb0d2d8b16402e5b2e81216d32b56e239dccbfd93ada5498376a` (matches the release-builder deploy output); base `python:3.13-alpine@sha256:4d511039...` unchanged. | 2026-11-06 |
| ECR scan (release 124) | COMPLETE: **zero findings**. | 2026-11-06 |
| Release 124 verification | ECS service `lcdash-p1-logan-use1-web`: PRIMARY deployment on revision 124, rollout COMPLETED 10:36:54 EST, 1 of 1 running. ALB target `10.42.1.200:8000` healthy. `/`, `/active-calls` and `/analytics?discipline=fire` answer 302 to the Cognito domain. Web log group since 15:34:16Z: 111 events, 0 errors, 0 warnings. The first collector run on revision 84 (next :07 / :37 EST slot) was not waited for. | 2026-11-06 10:40 EST |
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

- The signed-in Fire view of the call that started this was not checked
  by the agent (it has no signed-in session); it should now show the
  medic's two minutes, not the half hour. Ted's check.
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

## Later the same day: three more not-timed call types (release 125)

At Ted's request Motorist Assist, Public Works / Utilities and Vehicle
Lock Out / Lock In were added to `EXCLUDED_CALL_TYPES` (fifteen natures
now). List-only change: no SQL changed, the page derives the names and
the count from the list. Tests, `docs/ANALYTICS_FOUNDATION.md` and the
audit document follow. Commit `5a4cdf2`, pushed to both remotes. The CAD
spelling of the three natures was not read from the warehouse (same
caveat as the first twelve).

**Release 125 live.** Change set `release-5a4cdf2-20261106` (Ted typed
the name; executed 16:05:54Z, 11:05 EST; `UPDATE_COMPLETE`). Web task
definition **revision 125**, collector **revision 85**, image tag
`release-5a4cdf24fffe`, digest
`sha256:d5c7a299715f43475b6aab90bd763b7a1cbe46637c21a2fb40cfd5ad5a8d91c6`.
Build `lcdash-p1-logan-use1-release-builder:380d6125-49c7-4621-8dd6-dc07c634798d`
SUCCEEDED 10:53:22 to 10:54:15 EST, `source_asset_sha256`
`d76af9ced383f2e49b4ca3ffb6eda23b84c1292058b458c8d69c8e72ab1f0b74`
(matches the release-builder deploy output); base image unchanged. ECR
scan COMPLETE 10:55 EST, zero findings. Of 23 stack parameters only
`PilotImageDigest` changed; the standard five resources were modified.
Verification: PRIMARY deployment on revision 125, rollout COMPLETED
11:08:46 EST, 1 of 1 running; ALB target `10.42.1.214:8000` healthy; `/`,
`/active-calls` and `/analytics?discipline=fire` answer 302 to the Cognito
domain; web log since 16:05:54Z: 73 events, 0 errors, 0 warnings. The
first collector run on revision 85 was not waited for. Rollback target:
release 124's digest `sha256:4818358b...`.

## Held for the next release: "Lock Out" added (sixteen not-timed types)

After release 125 Ted found CAD carries the lock-out nature under two
names, "Vehicle Lock Out / Lock In" and "Lock Out". The matcher is exact
apart from case, spaces and punctuation, so release 125 only caught the
first. `EXCLUDED_CALL_TYPES` now lists both (sixteen natures: the first
twelve plus Motorist Assist, Public Works / Utilities, Vehicle Lock Out /
Lock In and Lock Out). An intermediate local commit (`025652d`) had
briefly taken Vehicle Lock Out / Lock In off the list on a misreading of
the request; it is superseded by the commit that follows it and was never
pushed or released. Tests, docs and this note follow. **Not pushed and
not released** when written: release 125 (live) still has the
fifteen-type list until the next release goes out.

## Held for the next release: "≤ 60 Sec" column in, "Stacked" column out

At Ted's request the dispatcher table no longer shows the "Stacked"
column (the "Stacked first" summary line and the drill-down's unit hold
remain, and the API still returns `stacked_calls` / `average_hold`), and
a "≤ 60 Sec" column (share of timed calls dispatched within 60 seconds)
sits beside "≤ 90 Sec". The "Within 90 seconds" tile's subline also
shows the 60-second share. Implementation: one more aggregate,
`within_60_seconds`, appended to `processing_aggregates_sql` (so the
existing column positions did not move; the dispatcher ref moved from
index 14 to 15), `within_60_percent` on each row, the summary and the
empty overview. The live PostgreSQL test expects 20 % on the synthetic
timelines (only the self-initiated zero is under 60 s). Reporting SQL
changed, so the live test was run (PostgreSQL 17.11, 3 passed). Local
commit, **not pushed and not released**.

## Exact next action

1. Ted checks the Fire view of the call that started this and the
   by-discipline tables, and that the "Not timed" count now shows 15
   types and rises for the three new natures (release 125).
2. Push this handoff commit when Ted says so (`git push origin main`).
3. Open items unchanged: Stack backfill of older calls, `HowReported`,
   `UnitType.Discipline`, the AGENTS.md note about the empty accepted ECR
   list and the live PostgreSQL test for reporting SQL.
