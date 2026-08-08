# MAE Full Read-API Toolset — Design — 2026-08-08

**Owner directive:** MAE may use *any* query/information obtainable from the
CentralSquare **READ** API — not just today's fixed tools — and bring it into
answers, reports, AND analytics. No field is withheld (sensitive/patient
included; supervisor-only access). **HARD BOUNDARY: every query is READ only.
CAD writes / dispatch / acknowledge / page / subscribe stay permanently off.**

This design assumes the parallel "de-limiting" change (removing the field
stripping and 50-row caps from today's snapshot-only tools) has landed; the
de-limited tools are the base we extend. It supersedes the Phase-1 snapshot-only
scope of `MAE_TOOL_CALLING_PLAN_2026-08-08.md` §1.2 by adding **live read-API
query tools** (that plan's deferred "Phase 2").

---

## 1. The available READ-API surface (enumerated)

**Confirmed allowlisted read operations** (structural allowlist, no write
counterpart exists in the connector):

| Op | HTTP | Cloud method | On-prem method |
|---|---|---|---|
| `search_calls` | POST `/cfs_core/search` | `cloud_read_connector.py:274` | `centralsquare.py:27` `search_cfs_core` |
| `get_call` | GET `/cfs_core/{CFSNumber}` | `:283` | `:53` `get_cfs_core` |
| `search_units` | POST `/units/search` | `:295` | `:40` `search_units` (+ `unit_service.get_all_units:294` paginates) |
| `get_configurations` | GET `/configurations` | `:304` | `:22` `get_system_config` |
| `get_call_analytics` | GET `/cfs_analytics/{CFSNumber}` | *not yet in cloud connector* | `:57` `get_cfs_analytics` (unit times per CFS) |

Cloud allowlist enforcement: `cloud_read_config.py:166` `allowed_operations` =
`(authenticate, health, search_calls, get_call, search_units)`. **Gap:**
`get_configurations` is implemented (`connector:304`) but omitted from that
tuple, and `get_call_analytics` has no cloud method at all. Both are read-only
and should be added to the allowlist tuple + connector (Phase 3). `run_command`
(`centralsquare.py:61`), `put` (`:103`), and everything in
`FORBIDDEN_OPERATIONS` (`cloud_read_config.py:89`: acknowledge, dispatch,
register_subscription, send_alert/message/page, trigger_tone, update_call) are
**never** given a tool.

**`POST /cfs_core/search` body parameters.**
*Confirmed in real callers:* `RecordCreatedFrom`, `RecordCreatedTo`,
`OrderByField` (`"Created"`), `OrderByDirection` (`"Descending"`) —
`heatmap_service.py:108-113`; `CurrentlyActive: true` —
`cloud_read_runtime.py:410`. Pagination `skip`/`limit` (1–100) —
`connector:262-272`, `centralsquare.py:34-37`.
*Confirmed from vendor Swagger notes* (`API_KNOWLEDGE_BASE.md:176-193`):
`DispatchAgencies`, `ResponseAgencies`, `RecordCreatedFrom/To`,
`RecordClosedFrom/To`, `RecordUpdatedFrom/To`, `CurrentlyActive`,
`IncidentCode`, `Location`, `Beat`, `Zone`, `CaseAssociation`, `OrderByField`,
`OrderByDirection`, `Responder`, `Unit`.
*Returned fields* (`API_KNOWLEDGE_BASE.md:194-225`): CFSNumber, DispatchAgency,
IncidentDateTime, CallTaker, CallDateTime, PrimaryResponseAgency, Reporter,
Address (incl. lat/long), Beat, Zone, IncidentCode, Priority, Disposition,
Unit, Name, CommandLog, ProQA, RapidSOS, TextHistory, IsScheduledCall,
NearestCrossStreet, etc. Priority/status/agency filtering are not first-class
body params — priority/disposition are **post-filtered server-side** from the
returned records (marked *inferred*).

**Analytics read surface** (`analytics_reporting.py`): `get_analytics_overview(
period, start, end, county_profile, tenant_context, *, hours)` (`:899`).
Windows via `resolve_analytics_window` (`:65`): presets `24h/7d/30d/90d/365d`
(`PERIOD_OPTIONS:25`), custom `start`/`end` (≤366 days, `:112`), or exact
`hours` 1–8784 (`:82`). Overview returns metrics, dispatcher_metrics + per-
dispatcher rows, daily/hourly/weekday volume, agency_mix, incident_types,
busiest_units, busiest_stations, station_discipline (`:866-896`).

---

## 2. The comprehensive tool set

One shared JSON-schema `TOOL_SPECS` definition; two executors implementing the
same contract:
- **Cloud** (`live_tools.py`): today reads only the polled snapshot
  (`CloudCadDisplayState`, `cloud_read_runtime.py:90`). New search/roster/
  config/analytics-per-CFS tools call the **connector** directly
  (`CloudCentralSquareReadConnector`) via a per-request instance, reusing the
  existing token/backoff/allowlist path.
- **On-prem** (new `app/services/mae_live_tools.py`, mirroring `live_tools.py`):
  executor calls `CentralSquareClient`/`get_all_units`/`get_analytics_overview`
  directly. (Today on-prem MAE has only the `mae_tool_registry.py` *catalog* and
  regex intents in `mae_service.py:1104`; this adds the executable registry.)

Both executors normalize through the existing `cloud_read_runtime._normalize_
calls/_normalize_units` (`:289`, `:320`) / `simplify_call`, returning **all
normalized fields including sensitive/reporter/coordinates/command-log narrative**
per the directive. The only stripped item is the giant raw upstream blob
(`raw`) — token-budget only, not a data restriction.

### 2.1 `search_calls` (flexible, replaces snapshot-only `list_active_calls`)
Purpose: query historical/active CFS records by any confirmed dimension.
```json
{"type":"object","properties":{
 "currently_active":{"type":"boolean"},
 "created_from":{"type":"string","format":"date-time"},
 "created_to":{"type":"string","format":"date-time"},
 "closed_from":{"type":"string","format":"date-time"},
 "closed_to":{"type":"string","format":"date-time"},
 "incident_code":{"type":"string","maxLength":32},
 "dispatch_agencies":{"type":"array","items":{"type":"string"},"maxItems":10},
 "response_agencies":{"type":"array","items":{"type":"string"},"maxItems":10},
 "beat":{"type":"string","maxLength":32},
 "zone":{"type":"string","maxLength":32},
 "location":{"type":"string","maxLength":128},
 "unit":{"type":"string","maxLength":16},
 "order_by":{"type":"string","enum":["Created","Closed","Updated"]},
 "order_dir":{"type":"string","enum":["Ascending","Descending"]},
 "priority":{"type":"string","maxLength":8},
 "status":{"type":"string","maxLength":32},
 "limit":{"type":"integer","minimum":1,"maximum":100},
 "page":{"type":"integer","minimum":0,"maximum":19}}}
```
Maps to `search_calls`/`search_cfs_core`. **Validation:** at least one time
bound OR `currently_active` required (reject unbounded scans); window ≤ 92 days
(`created_to − created_from`); datetimes ISO-8601 → UTC; strings match
`^[\x20-\x7e]{1,128}$`; `priority`/`status` applied as post-filters on returned
records. **Bounds:** `limit`≤100, `page`≤19 → hard 2,000-record ceiling
(mirrors `MAX_HEATMAP_PAGES=20`, `heatmap_service.py:22`). Returns normalized
call records (all fields).

### 2.2 `get_call_detail` (exists, de-limited)
Input `{"cfs_number":"CFSnn-nnnnnn"}` validated by `CFS_PATTERN` /
`_CFS_NUMBER` (`connector:32`). Cloud: prefer snapshot; on miss, call
connector `get_call`. On-prem: `get_cfs_core`. Returns full normalized detail
incl. coordinates + full command-log (cap 500 entries, `runtime:213`).

### 2.3 `get_call_analytics` (new)
Input same CFS schema → `get_cfs_analytics` (`centralsquare.py:57`, GET
`/cfs_analytics/{n}`): per-unit dispatch/enroute/on-scene times for one CFS.
Requires the cloud-connector addition noted in §1. Single record, no paging.

### 2.4 `search_units` (flexible, replaces snapshot-only unit listing)
```json
{"type":"object","properties":{
 "agency":{"type":"string","maxLength":16},
 "unit_type":{"type":"string","maxLength":32},
 "status":{"type":"string","maxLength":32},
 "station":{"type":"string","maxLength":32},
 "active_only":{"type":"boolean"},
 "limit":{"type":"integer","minimum":1,"maximum":100},
 "page":{"type":"integer","minimum":0,"maximum":9}}}
```
Maps to `search_units`; on-prem may use `get_all_units` for full roster.
Post-filter by agency/type/status/station. Bound 1,000 units (10 pages).

### 2.5 `get_configurations` (new tool)
Input `{"configuration": <identifier>}`, validated `isidentifier()` & ≤64
(`connector:305`). Maps to `get_configurations`/`get_system_config`. Enables
lookups like `CADUnitStatus`, `IncidentType` (`API_KNOWLEDGE_BASE.md:77-98`).

### 2.6 `get_analytics_summary` (exists, de-limited)
Keep the `hours` XOR `period` schema (`live_tools.py:315`), but drop the top-5/
top-10 row caps so full `dispatchers`, `agency_mix`, `station_discipline`, and
custom `start`/`end` ranges reach the model. Maps to `get_analytics_overview`.

---

## 3. Feeding answers, reports, AND analytics

- **Chat answers:** unchanged loop (`tool_calling_advisory.py:138`), now over
  the wider `TOOL_SPECS`.
- **Analytics reports:** `/api/mae/analytics-report` (`main.py:1290`) →
  `build_analytics_report(snapshot, view_key)` (`mae_analytics_report_service.py:37`)
  currently takes only a `get_analytics_overview` snapshot. Add an optional
  `search_calls`-derived aggregate appendix (counts by incident_code/agency over
  the report window) so live CAD queries surface in the PDF, clearly labeled
  "current read-only CAD aggregate — not the authoritative historical record"
  (reuse the freshness-notice pattern, `cloud_report_service.py:71`).
- **Report planning:** `cloud_report_service.ReportIntent` (`:26`) already
  allowlists metrics/dimensions/periods with a 500-row cap (`:73`) and a
  `current_cad_fallback` hook (`:68`). Wire `search_calls` as the
  `CurrentCadAggregateSource.query_current` implementation — the model proposes
  a `ReportIntent`, the server (not the model) runs the bounded query. This is
  where live CAD data enters saved/templated reports.

The tools return structured JSON, so the same tool output feeds a chat
sentence, a report table, or an analytics aggregate without a separate path.

---

## 4. Safety — read-only structural guarantee

1. **Allowlist is the enforcement point.** Every tool maps to a method that
   exists only for reads. There is no `run_command`/`put`/dispatch tool, so the
   model cannot emit one regardless of prompt (`tool_calling_advisory.py:9-12`).
   Keep `run_command`/`put` out of any registry; unit-test that
   `FORBIDDEN_OPERATIONS` names never appear as tool names.
2. **Validation before every call.** Reject unbounded searches (require a time
   bound or `currently_active`), cap windows (≤92 days calls / ≤366 days
   analytics, `analytics_reporting.py:112`), sanitize every string
   (`_SAFE_FROM`/`_CFS_NUMBER`, `connector:31-32`), enforce `isidentifier()`
   config names. Bad input → error payload to the model, never a raised
   exception (`live_tools.py:135-147`).
3. **Cost caps.** `limit`≤100 (vendor max, `cloud_read_config.py:47`);
   `page`≤19 (calls) / ≤9 (units); `max_tool_rounds=5` (`advisory:29`); shared
   `DailyRequestBudget(200)`; connector 15s timeout + bounded 3-attempt backoff
   (`connector:29-30,154`). Temperature 0; answer-from-tools-only prompt.
4. **Access control.** Supervisor-only: gate the wider tool set behind the
   existing tenant authorization (`authorize_tenant_action(... ModuleCapability
   .ANALYTICS/ACTIVE_CALLS ...)`, e.g. `analytics_reporting.py:914`) and a new
   `cloud_ai_tool_calling_enabled` + role check before the registry is built.
5. **Transparency + audit.** Every tool call already logs name + result size
   (no payload bodies, `advisory:192`) and surfaces a `data_sources` chip.

---

## 5. Phased plan, tests, risks

**Phase A (no new HTTP):** de-limit existing 3 tools; add `get_configurations`
over the polled config cache if available. **Phase B:** add flexible
`search_calls` + `search_units` over the connector (cloud) and
`CentralSquareClient` (on-prem); build the on-prem `mae_live_tools.py`
executor. **Phase C:** add `get_call_analytics`; extend the connector allowlist
tuple (`cloud_read_config.py:166`) with `get_configurations`,
`get_call_analytics`. **Phase D:** report/analytics wiring (§3).

**Tests** (stdlib `unittest`, repo style): schema/validation (reject unbounded
window, over-long window, bad CFS, non-identifier config); bounds (page/limit
ceilings, 92-day cap); structural safety (no forbidden op is a tool name;
executor has no path to `run_command`/`put`); executor parity (cloud vs on-prem
same contract) with a mock transport/`CentralSquareClient`; report appendix
labeling. Extend `test_live_tools.py`, `test_tool_calling_advisory.py`,
`test_cloud_cad_read_connector.py`.

**Risks:** (1) *Over-broad queries* — model requests a huge window → mitigated
by required bound + 92-day cap + 2,000-record ceiling. (2) *Latency* — live
search adds a CentralSquare round-trip per tool call on top of Bedrock rounds;
5-round cap and page ceilings bound worst case; prefer the polled snapshot for
"right now" questions. (3) *Token budget* — full un-stripped records are large;
keep `raw` stripped, cap records surfaced per round, keep answer ≤400 tokens.
(4) *Wrong aggregation* — failure mode is "plausible but wrong," not a write;
contained by read-only registry, temp 0, source chips, advisory-only banner.
(5) *Vendor filter uncertainty* — priority/status post-filtered until confirmed
against live Swagger; mark clearly and verify before enabling.
