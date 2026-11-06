# Session handoff, 2026-11-06: releases 122 and 123 (discipline filter, not-timed call types, base image bump, filter fix)

Follows `session-handoff-2026-11-05-dispatcher-filters.md` (the feature
work, `0d78d88`). Facts first; everything below was read from Git, the
test runners, a local PostgreSQL 17.11 and the AWS account through the
`lcdash-phase1-deployment` profile during the two releases.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main` at `4a987a1` (`0d78d88` features, `17e8f4c` / `cbd79e7` handoff, `8361e01` base image bump, `4a987a1` discipline-filter fix), pushed to GitHub and Bitbucket. Working tree clean apart from this handoff. | 2026-11-06 01:05 EST |
| LCDash pilot (AWS) | **Release 123 live.** Foundation stack updated via change set `release-4a987a1-20261106` (Ted typed the name; executed 06:12:27Z, 01:12 EST). Web task definition **revision 123**, collector **revision 83**, both image tag `release-4a987a1b9ced`, digest `sha256:2fcfb9727e4dd49688a7b6e17c6fc44f9d05a09b1dccf05d49e6f8adf3a02fe0`. Verification figures: see "Release 123 verification" below. Of 23 stack parameters only `PilotImageDigest` changed; the five standard resources were modified. | 2026-11-06 |
| Rollback target | change set with digest `sha256:1c29a90c7bac9fc4ddc63ad95dbb30144a81ad042d180d46dd0ba021ada7b1ba` (revision 122, `release-8361e011e8da`; its unfiltered views work, its EMS / Fire / Law views do not). Revision 121 (`sha256:14d3f80a...`) is the pre-feature fallback. | |
| Release 123 build | CodeBuild `lcdash-p1-logan-use1-release-builder:5c459282-1b81-422b-8cb7-3f4d7536603c`, SUCCEEDED 01:09:11 to 01:10:02 EST; `source_asset_sha256` `b6cce6a4b9ff7dd7fe1dd5a2e687042d8ba2f2bbb35a017907cbe30db2e42b42` (matches the release-builder deploy output); base `python:3.13-alpine@sha256:4d511039...`. | 2026-11-06 |
| ECR scan (release 123) | COMPLETE 01:10:13 EST: **zero findings**. | 2026-11-06 |
| Release 122 (superseded) | Change set `release-8361e01-20261106` executed 05:10:16Z (00:10 EST), `UPDATE_COMPLETE` 05:13:16Z; web revision 122, collector 82, digest `sha256:1c29a90c...`; build `c0b45b80...` SUCCEEDED, source asset `74f05228...`; scan zero findings; rollout COMPLETED, target healthy, 53 web events / 0 errors; the 00:37 EST collector run on revision 82 completed (9 calls stored, 0 failures). Superseded by 123 within an hour because of the filter bug below. | 2026-11-06 |
| Tests | On `4a987a1`: `python -m pytest tests/` 2088 passed, 6 skipped, 2623 subtests; `python -m pytest infrastructure/tests/` 151 passed, 9 subtests; `tests/test_analytics_postgres_live.py` **3 passed against PostgreSQL 17.11** (first run ever of its filter expectations on a real server). | 2026-11-06 01:00 EST |

## What is live

1. **Dispatcher discipline filter** (All / EMS / Fire / Law) on the
   dispatcher KPIs, table and slow-call drill-down; county-wide charts
   and the by-discipline tables unchanged. `?discipline=ems|fire|law`
   on `/analytics`, `/api/analytics/overview`,
   `/api/analytics/dispatcher-slow-calls`; other values are a 400.
2. **Twelve not-timed call types** (Roundtrip, Take Home, Transfer /
   Inter-Facility, Contact, Move-Up, Shoplifting, Complaint, 911 Hang
   Up, Call Through Office, 911 Open Line, Information, Be On the
   Lookout): still counted as calls entered, never averaged, shown under
   "Not timed", dropped from the discipline tables.
   Definitions: `docs/ANALYTICS_FOUNDATION.md`,
   `docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md` section 4,
   and the previous handoff.
3. **Base image bump** (`8361e01`, one line in `Dockerfile.aws-pilot`):
   `python:3.13-alpine` pin moved from `sha256:7415fbc3...` to
   `sha256:4d511039677430640754cd1417737349067dfcdaba4dcf7e576334516f9f4c30`
   (Alpine 3.24.4, musl 1.2.6-r3, zlib 1.3.2-r1; the index published
   2026-11-05 23:08Z, identical digest on Docker Hub and `public.ecr.aws`).
4. **Discipline-filter fix and error logging** (`4a987a1`), see the
   incident below.

## Incident: release 122's filtered views failed (fixed in 123)

Ted opened the Analytics page after release 122 and saw "Historical
analytics are temporarily unavailable / PostgreSQL analytics is
configured but unavailable." on the Fire view.

- **Cause.** In `ACTION_STAMPS_SQL` (0d78d88) the discipline CASE was
  computed in the SELECT over the `stamps` subquery, but the optional
  filter was `WHERE stamps.discipline = %(discipline)s`. PostgreSQL 17
  rejects it: SQLSTATE 42703 `column stamps.discipline does not exist`.
  Every EMS / Fire / Law query failed; the unfiltered views never add the
  filter and kept working (the web log shows the 24h table rendering and
  a "> 3 Min" drill-down click at 05:38Z). The collector on the same
  image kept storing calls (it does not run the reporting SQL).
- **Why tests passed.** The unit suites use recording fakes that never
  parse SQL. `tests/test_analytics_postgres_live.py` would have caught
  it (its filter expectations fail on the released SQL), but it had
  never run on a real server: the gap named in both previous handoffs.
- **Why it was silent.** `AnalyticsRepository._execute` wrapped the
  psycopg error with no log line.
- **Fix (`4a987a1`).** The CASE now sits in a derived table
  (`raw_stamps` -> `stamps`) so the filter references a real column;
  `_execute` logs the SQLSTATE and the server's primary message at
  WARNING (never the statement or values) before raising.
- **Verification before re-release.** With Ted's approval the
  PostgreSQL 17.11 Windows binaries (EDB zip, 381 MB) were downloaded to
  the session scratchpad and run as a user-owned cluster on
  127.0.0.1:55432 (no installation). Against it: the live test 3 passed
  on the fix; the released `8361e01` SQL reproduced the 42703 on the
  filtered query and passed unfiltered; the fixed SQL passed both. Full
  suites green. The cluster was stopped at the end of the session; the
  binaries remain in the scratchpad only.

## Release 123 verification (01:20 EST)

- Foundation stack `UPDATE_COMPLETE` 06:15:28Z (01:15 EST); events show
  exactly the five expected resources (two task definitions replaced,
  the service, the collector's schedule rule and events policy), old
  revisions 122 / 82 deregistered by CloudFormation.
- Web service: PRIMARY deployment on task definition revision 123,
  rollout COMPLETED 01:15:04 EST, 1 of 1 running, "has reached a steady
  state".
- ALB target `10.42.0.209:8000` healthy; `/`, `/active-calls` and
  `/analytics?discipline=fire` 302 to Cognito.
- Web log since 06:12Z: 53 events, 0 ERROR / Traceback / Exception, 0
  "PostgreSQL analytics operation failed" warnings (the new log line),
  clean Uvicorn startup.
- Collector revision 83 carries the same image; its first run was due
  at 01:37 EST and was not waited for (the fix does not touch the
  collector's code path; revision 82's runs on the same base completed
  cleanly).
- Signed-in check of the Fire view: Ted's, after this handoff.

## Why the base image moved during release 122

The first build of the day (`release-cbd79e7fe322`, build `f8ea922c`,
digest `sha256:da625968...`) scanned with the nine accepted baseline
findings plus one new HIGH: CVE-2026-107891, musl 1.2.6-r2, CVSS 8.1
(integer overflows in musl `regcomp` with an untrusted regular
expression; NVD published 2026-11-05 16:17Z). Per AGENTS.md rule 4 the
release stopped before any change set. Both the release-121 build and
that build pulled the identical pinned base `7415fbc3`, so the running
pilot carried the same musl; the finding was not caused by the feature
commit. Reading `lib/apk/db/installed` from the first layer of the new
`python:3.13-alpine` amd64 image (downloaded to the scratchpad, never
executed) showed Alpine 3.24.4 with musl 1.2.6-r3, the Alpine fix. Ted
chose "Bump base, rebuild"; the bump was treated as a dependency change
under the charter (one-line pin edit, full suites, commit, push,
rebuild, rescan). The `release-cbd79e7fe322` image remains in ECR,
unused; no change set was ever created for it. Assessment, not proof:
LCDash compiles regular expressions with CPython's `re` engine, which
does not call musl `regcomp`.

## Release path as executed (for the next release)

1. Tree clean, `main` pushed to both remotes (`git ls-remote` on both).
   Park untracked drafts outside the tree first.
2. Full suites green, **and** `LCDASH_TEST_PG_DSN="host=127.0.0.1
   port=55432 user=postgres" python -m pytest tests/test_analytics_postgres_live.py`
   against a local PostgreSQL 17 whenever reporting SQL changed.
3. `cd infrastructure && npx --yes aws-cdk@2.1145.0 deploy
   lcdash-p1-logan-use1-release-builder --app "python
   release_builder_app.py" --context account=862772137583 --context
   region=us-east-1 --require-approval never --progress events` (note the
   `ReleaseSourceAssetHash` output).
4. `aws codebuild start-build --project-name
   lcdash-p1-logan-use1-release-builder --environment-variables-override
   name=IMAGE_TAG,value=release-<12 hex>,type=PLAINTEXT`; wait for
   SUCCEEDED; the `image-digest-report.json` line in the build log gives
   the digest and must show the same `source_asset_sha256`.
5. `aws ecr describe-image-scan-findings` on the digest (COMPLETE in
   about two minutes). The accepted list is now **empty**: any finding
   stops the release.
6. `npx --yes aws-cdk@2.1145.0 deploy lcdash-p1-logan-use1-foundation
   --app "python cdk_app.py" ... --parameters PilotImageDigest=<digest>
   --method=prepare-change-set --change-set-name release-<7 hex>-<date>`;
   `describe-change-set` must show five Modify changes and a parameter
   diff of `PilotImageDigest` only; execute only after Ted types the
   name.
7. Verify: stack `UPDATE_COMPLETE`, service rollout COMPLETED on the new
   revision, target healthy, 302 to Cognito, web log errors 0, a
   filtered Analytics view rendering, first collector run on the new
   revision complete.

The `lcdash-phase1-deployment` role cannot call `ecr:BatchGetImage`,
`events:ListRules` or `rds:DescribeDBInstances` (denied during
verification; nothing needed them). The collector runs at :07 and :37
past the hour EST, 50 to 60 seconds each.

## Local PostgreSQL for the live test (how to repeat)

Binaries: `postgresql-17.11-5-windows-x64-binaries.zip` from
`get.enterprisedb.com` (link on the EDB "download binaries" page), unzip
`pgsql/bin`, `pgsql/lib`, `pgsql/share`; then `initdb -D data -U postgres
-A trust -E UTF8 --locale=C`, `pg_ctl -D data -o "-p 55432 -c
listen_addresses=127.0.0.1" -l postgres.log -w start`, run the test with
the DSN above, `pg_ctl -D data stop`. Trust auth on loopback only, test
databases only; keep it out of the repo.

## Not verified

- The CAD's exact wording of the twelve natures (the matcher ignores
  case, spaces and punctuation; an abbreviation such as "BOLO" would not
  match). If the "Not timed" count for those types looks low, add the
  spelling to `EXCLUDED_CALL_TYPES` in
  `app/services/analytics_reporting.py` (one line, test first).

## Exact next action

1. Ted: sign in, open Analytics, try Fire / EMS / Law and the "> 3 Min"
   drill-down under a filter; compare "Not timed" with the Top Incident
   Types list.
2. Consider recording in AGENTS.md that the ECR accepted-findings list
   is now empty and that reporting-SQL changes require the live
   PostgreSQL test before release (owner's edit; this session did not
   touch AGENTS.md).
3. Still open from earlier handoffs: Stack backfill of older calls,
   `HowReported`, `UnitType.Discipline` (each a separate approval).

Services were left running; nothing was changed on `.227` or `.15`.
Scratchpad files (deploy and change-set logs, the base-image layer used
for the package check, the PostgreSQL binaries and data directory) are
outside the repo and hold no CAD data.
