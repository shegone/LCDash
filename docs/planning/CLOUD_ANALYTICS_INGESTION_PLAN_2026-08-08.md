# Cloud Analytics Ingestion Plan — LCDash AWS Pilot

**Date:** 2026-08-08
**Scope:** Stand up accurate CAD analytics ingestion so `lcdash_analytics.*` in the cloud RDS is populated with real historical call data. Investigation + design only.
**Goal (Ted):** "Very accurate real data" behind cloud Analytics, Reports, and the DB-backed heat map — which are empty today.

---

## 1. How it works on-prem

On-prem runs a dedicated long-lived worker container, not an in-app loop.

- `deploy/compose.yaml:109-129` defines the `analytics-worker` service: `command: ["python", "scripts/analytics_worker.py"]`, `restart: unless-stopped`, sharing the web app's env anchor and secrets, with `ANALYTICS_SYNC_INTERVAL_SECONDS: "300"`.
- `scripts/analytics_worker.py:23-44` is an infinite loop: call `run_analytics_sync()` every ~300s, log `status`/`calls_processed`, swallow `AnalyticsDatabaseError`/`CentralSquareAPIError`, sleep the remainder of the interval. `scripts/sync_analytics.py:35-47` is the one-shot CLI equivalent (`--hours`, `--max-calls`) used for backfills.
- The engine is `app/services/analytics_collector.py`:
  - `run_analytics_sync()` (lines 84-201) resolves an incremental window. If a prior sync exists it starts at `previous_sync - overlap_minutes` (lines 117-121); otherwise `now - lookback_hours` (lines 122-123). `window_end = now` (line 124).
  - `discover_completed_calls()` (lines 20-68) queries CentralSquare `cfs_core/search` with `RecordClosedFrom/To`, `CurrentlyActive: False`, ordered by `Closed` ascending, paging (≤100/page) and de-duplicating by `CFSNumber` into `calls_by_number` (lines 36-59) up to `max_calls`.
  - For each call it fetches per-call analytics via `client.get_cfs_analytics(cfs_number)` (line 146), normalizes (line 147), and `database.upsert_bundle(bundle)` (line 153). A `request_delay_ms` sleep throttles between calls (lines 158-159).
- **Pull client:** `CentralSquareCadAdapter` (aliased `CentralSquareClient`) at `analytics_collector.py:7-9`, whose transport is `app/services/centralsquare.py:CentralSquareClient`. Auth is OAuth password-grant via `get_access_token()` (`centralsquare.py:13`), reading `CENTRALSQUARE_*` env (`app/config/settings.py:100-105`). The analytics endpoint `get_cfs_analytics` hits `{cad_base_url}/cfs_analytics/{cfs}` (`centralsquare.py:57-59`).
- **Write path / accuracy mechanisms** (`app/services/analytics_database.py`):
  - `AnalyticsRepository` connects with `settings.database_url` (line 24, 33). `initialize_schema()` (lines 69-72) applies `database/analytics_schema.sql` idempotently on every run.
  - **Idempotent upsert:** `upsert_bundle()` (lines 153-356) does `INSERT ... ON CONFLICT (cfs_number) DO UPDATE` on `calls` (line 201), then **deletes + re-inserts** child rows for `call_agency_times` and `unit_responses` (lines 227-234) so re-processing a call cannot double-count. `units` upserts on `unit_number` (line 337).
  - **Dedup key:** `cfs_number` is the natural primary key end-to-end (`analytics_schema.sql:4`, child PKs at lines 60, 82).
  - **Watermark + overlap:** `sync_state` row `completed_calls_through` (lines 74-100) is advanced (line 165) **only on `status == "complete"`** — partial/failed runs do not move the watermark, so gaps are re-scanned next run. `overlap_minutes` (default 120) re-reads a trailing window each cycle to catch late-closed calls.
  - **Run ledger:** `sync_runs` (lines 102-151) records discovered/stored/failure counts per run for verification.
  - **Timezone:** everything is normalized to UTC. `_utc_datetime` (collector 14-17) and `_parse_datetime` (`analytics_models.py:24-37`) coerce naive/`Z` timestamps to tz-aware UTC; all schema columns are `TIMESTAMPTZ` (`analytics_schema.sql:20-23`). Eastern display is applied only at the presentation layer.
- **Settings (`ANALYTICS_*`, `app/config/settings.py:173-188`):** `ANALYTICS_INITIAL_LOOKBACK_HOURS` (24), `ANALYTICS_OVERLAP_MINUTES` (120), `ANALYTICS_MAX_CALLS_PER_RUN` (250), `ANALYTICS_REQUEST_DELAY_MS` (100).

## 2. Why cloud is empty

Three independent blockers; all must be cleared.

1. **The worker code isn't in the image.** `Dockerfile.aws-pilot:23-28` copies `app`, `config/counties`, `database`, `static`, `templates` — **no `scripts/`** (confirmed: `Dockerfile.aws-pilot` has no `scripts` COPY; the on-prem `Dockerfile:19` has `COPY scripts ./scripts`). The engine `app/services/analytics_collector.py` *is* in the image (under `app/`), but no entrypoint invokes it.
2. **Nothing runs it.** The cloud image CMD is uvicorn only (`Dockerfile.aws-pilot:37`). The foundation web task runs a single web container (`foundation_stack.py:126-205`) — no worker container, sidecar, EventBridge schedule, or scheduled Fargate task. `run_analytics_sync` / `analytics_worker` are referenced nowhere in `app/` runtime startup (grep clean). The foundation stack comment states it outright: "the collector cannot run here yet" (`foundation_stack.py:244-249`).
3. **The cloud CAD path cannot fetch analytics.** Cloud CAD is a fail-closed read-poll bridge for **active calls only**. `LCDASH_CLOUD_CAD_MODE=centralsquare-read-poll` (`foundation_stack.py:161`) drives `CloudCentralSquareReadConnector`, which exposes `search_calls`, `get_call`, `search_units`, `get_configurations` — but **not `get_cfs_analytics`** (`cloud_read_connector.py:274-312`). The collector's core dependency (`analytics_collector.py:146`) has no cloud equivalent wired. Credentials come from the reviewed read-only secret via `SecretsManagerCredentialProvider` (`cloud_read_runtime.py:562-567`), and the web task role can read exactly that one secret ARN (`foundation_stack.py:110-115`).

**Database:** cloud uses RDS PostgreSQL 17 (`foundation_stack.py:222-255`), `deletion_protection=True` / `RemovalPolicy.RETAIN` (lines 252-253) precisely because it is meant to hold imported history. The app builds `database_url` from `LCDASH_DATABASE_*` env + Secrets Manager (`settings.py:52-94`; `foundation_stack.py:259-275`); note synthetic-disconnected mode **rejects a raw `DATABASE_URL`** (`settings.py:56-62`). The RDS lives in `PRIVATE_ISOLATED` subnets reachable only from inside the VPC (lines 236-238, 300).

**What already exists:** a dormant Phase 2 one-way importer (`analytics_import_stack.py`) — KMS-encrypted S3 staging, an inert Fargate task definition (`command: python -m app.tools.phase2_analytics_import_runtime`, lines 245-270) that decrypts a staged bundle and loads `calls/units/call_agency_times/unit_responses/saved_analytics_widgets` (`phase2_analytics_import.py:87-134`). It has **no CAD/source permissions by design** (lines 127-130, 253) and never auto-starts (line 19).

## 3. Options to run ingestion in cloud

**(a) Scheduled Fargate task (EventBridge → RunTask) running the collector.**
Re-use the collector against CentralSquare on a schedule (e.g. every 5 min). Tradeoffs: matches on-prem semantics exactly (same `run_analytics_sync`, same watermark/overlap); isolates worker failures from the web task; scales CPU independently. Cost: needs `get_cfs_analytics` added to the cloud read connector (or the collector pointed at the legacy `CentralSquareClient`), the read-only secret ARN granted to a **new** worker task role, `scripts/` (or a `-m app...` entrypoint) added to the image, and CDK for the schedule/role/SG. This is the only option that keeps cloud data **self-updating and accurate over time**.

**(b) Sidecar/loop inside the existing web service.**
Add a second container to the web task def (or an asyncio loop in `app.main`). Tradeoffs: cheapest CDK change; but couples worker crashes/CPU to the user-facing app, and a `desired_count` of 0/1 (`foundation_stack.py:513-523`) means ingestion stops whenever the site is scaled down. Weakest operationally.

**(c) On-prem export → encrypted S3 → one-way import (already scaffolded).**
Run the collector on-prem (where it already works and has full CAD analytics access), export `lcdash_analytics.*`, encrypt, stage to S3, and run `Phase2AnalyticsImportStack` once. Tradeoffs: **zero new cloud CAD surface** (respects the read-only boundary most strictly — importer has no CAD perms), highest accuracy (source is the authoritative on-prem warehouse), fastest to a populated pilot. But it is a **snapshot**, not live — data goes stale until re-imported.

**Recommendation:** **(c) for the backfill, then (a) for incremental.** Import the authoritative on-prem history now to make the pilot accurate immediately, then stand up the scheduled Fargate collector so cloud stays current without repeated manual exports. Avoid (b). For (a), extend the reviewed read connector with `get_cfs_analytics` under the same envelope rather than introducing the legacy client into cloud, so the read-only allowlist/auth stays the single reviewed path; the worker reuses the exact same read-only secret.

## 4. Accuracy requirements

- **Completeness (no missed calls):** rely on the watermark-only-on-complete rule (`analytics_collector.py:161-165`) plus `overlap_minutes=120` to re-scan late-closing calls. For the pilot, widen overlap (e.g. 180) and raise `ANALYTICS_MAX_CALLS_PER_RUN` during backfill so a busy window is never silently `truncated` (a truncated run stays `partial` and holds the watermark — collector lines 162, 168-171). Verify `sync_runs.analytics_failures == 0`.
- **No double-counting:** guaranteed by `cfs_number` PK + delete-then-insert of child rows (`analytics_database.py:201, 227-234`). Idempotency holds whether data arrives via import (c) or collector (a) — both write the same tables/keys, so switching from backfill to incremental cannot duplicate.
- **Timezones:** already correct — UTC `TIMESTAMPTZ` throughout; keep Eastern strictly at presentation.
- **Backfill depth:** decide with Ted. For a heat map and trend reports, 12–24 months is typical. Backfill via option (c) export (unbounded, one shot) or, if using (a) to backfill, loop `sync_analytics.py --hours <N> --max-calls <large>` walking backward.
- **Gaps to note:** `closed_at` is derived as the max of unit/agency completion times (`analytics_models.py:149-160, 264`), not a direct CAD field — spot-check it against on-prem. `latitude/longitude` are `NUMERIC(9,4)` (`analytics_schema.sql:17-18`, rounded at `analytics_models.py:138`): ~11 m precision, adequate for a heat map but not pinpoint. The importer path (c) must carry the same normalization the collector applies, or import pre-normalized rows straight from on-prem `lcdash_analytics.*` (which `phase2_analytics_import.py:87-134` does).

## 5. Phased implementation plan

**Phase A — Backfill (option c), days 1-2.**
1. On-prem: confirm `lcdash_analytics.*` is current (run `scripts/sync_analytics.py`), export the tables in `TABLE_PLANS` (`phase2_analytics_import.py:87-134`) to the bundle schema, encrypt (AES-GCM), record plaintext SHA-256.
2. Deploy `Phase2AnalyticsImportStack` (currently dormant), stage the `.json.enc` to the approved S3 prefix, run the one-off importer task once with the reviewed parameters. Verify against §5 checks, then scale importer back to inert.

**Phase B — Incremental collector (option a), days 3-5.**
3. Image: add `COPY scripts ./scripts` to `Dockerfile.aws-pilot` (or add a module entrypoint under `app/`), publish a new immutable digest.
4. Code: add `get_cfs_analytics` to `CloudCentralSquareReadConnector` (`cloud_read_connector.py`) within the existing endpoint envelope, and give the collector a cloud transport path so it uses the reviewed connector + read-only secret (no legacy `DATABASE_URL`, no new CAD auth).
5. CDK (new `AnalyticsCollectorStack` or additions to foundation): a Fargate task def running `analytics_worker`/`sync_analytics`, a **dedicated task role** granted `secretsmanager:GetSecretValue` on the read-only CAD secret ARN **and** the RDS secret, an SG mirroring the app SG (443 out, DNS, 5432 to RDS — cf. `analytics_import_stack.py:174-223`), and an EventBridge rule (rate 5 min) → `RunTask`. Set `ANALYTICS_*` env to match on-prem.
6. Cut over: let the collector's overlap re-scan bridge any gap between the Phase A snapshot and first incremental run.

**Verification against on-prem.** Compare `SELECT COUNT(*)` on `calls`, `unit_responses`, `call_agency_times` cloud vs on-prem for a fixed closed-date window; spot-check 10-20 `cfs_number`s field-by-field (times, lat/long, disposition, call_taker); confirm `sync_runs` shows `status=complete, analytics_failures=0`; confirm the heat map and Reports render non-empty.

**Risks.** (1) Adding `get_cfs_analytics` to the cloud connector widens the reviewed read surface — must go through the same secret/endpoint review as the read-poll bridge. (2) Backfill volume can hit CentralSquare rate limits — keep `ANALYTICS_REQUEST_DELAY_MS` and cap `max_calls` per run. (3) RDS is `deletion_protection=True`/`RETAIN` — safe, but schema drift between import and collector must be avoided (both call the same `analytics_schema.sql`, so keep the image in sync). (4) Never enable any CAD write capability — collector and importer are read/ingest only; keep `CadCapability` writes denied (`centralsquare.py` adapter guards).
