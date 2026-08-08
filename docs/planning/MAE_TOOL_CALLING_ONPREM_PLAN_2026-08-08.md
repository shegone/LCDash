# On-Prem MAE Tool-Calling Plan — 2026-08-08

**Goal:** Give the on-prem MAE (Qwen3.6 27B via local Ollama on .227) the
ability to call **read-only** tools to gather CAD/analytics data mid-answer,
so it can answer operational questions the fixed `_verified_*` regex path
cannot — the same capability just shipped for the cloud pilot, but built
natively against Ollama's tool API and held to a higher bar because **.227 is
the live production dispatch system, not a pilot.**

**Branch:** all work on `deployment/ubuntu-nvidia-227`, worktree
`E:/Projects/LCDash`. Deploy is archive-push via `scripts/deploy_server.ps1`
(git archive HEAD → scp → `deploy-lcdash.sh` → `docker compose up -d --build`),
with health-gated auto-rollback. Tests are NOT auto-gated — run pytest
manually before deploy.

---

## Data-scope decision (Ted, 2026-08-08)

Ted explicitly directed that MAE **may surface all CAD data, including
sensitive/patient details** (command-log narrative, patient names, reporter
info), on BOTH on-prem and cloud. Rationale: MAE is an internal advisory tool
for authorized 911 staff who already see this data on the dashboard and in the
CAD system itself; this is operational PHI use within the covered entity, not a
new external disclosure. Therefore: **no PHI redaction.** The adversarial
review's finding #1 (strip command_logs) and #3-scope (restrict to active CFS)
are intentionally NOT applied. Operational FYI given to Ted: voice mode can
speak answers aloud, so patient details could be heard in a shared room.

The remaining review findings (#2 analytics read path, #3 error handling, #4
context/token budget incl. stripping the duplicate `raw` blob, #5 failure
modes) ARE correctness bugs independent of the data policy and are retained
below.

## 1. Non-negotiable safety boundaries (production, safety-of-life)

1. **No CAD writes, ever.** `CentralSquareClient` exposes write methods
   (`run_command`, `put`) — the read-only boundary today is *by convention*
   (which functions MAE calls). The tool registry must make that boundary
   *structural*: tools wrap only specific read functions
   (`get_active_calls` / `get_call_detail` / `get_live_unit_snapshot` /
   `get_analytics_overview`). No tool may reach `run_command`/`put`. No
   dispatch/acknowledge/page/tone tool exists, so the model cannot invoke one.
   NOTE: `get_analytics_overview` calls `initialize_schema()` (idempotent DDL
   on the analytics Postgres, not CAD) — wrap a path that skips it, or accept
   and document the idempotent DDL. It touches no CAD write route.
2. **Validate every tool call before executing it.** Reject any tool name not
   in the allowlist and any arguments that fail the tool's JSON schema —
   return an error payload to the model, never raise, never execute an
   unvalidated call. Qwen3 can hallucinate tool names/args; the guard is code,
   not the model.
3. **The existing `_verified_*` answers stay first and byte-for-byte
   unchanged.** Every known-good deterministic answer (active calls, CFS
   detail, totals, etc.) keeps returning exactly as today. Tool-calling only
   runs when every `_verified_*` function returns None — i.e. it replaces the
   *plain LLM fallback*, nothing else.
4. **Feature-flagged off by default.** `MAE_TOOL_CALLING_ENABLED` (default
   `false`). Ship dormant, smoke-test on the live box, then enable in a second
   deploy. No behavior change until deliberately turned on.
5. **Advisory only.** Same read-only framing MAE already carries; the answer
   surfaces which tools/sources were used.
6. **Bounded + timed.** Hard cap on tool rounds (5). Explicit httpx timeout on
   every Ollama call so a tool loop can never hang a dispatcher's request.

## 2. Current architecture (verified 2026-08-08)

- `ask_mae()` (`app/services/mae_service.py:3128`): write-guard → routing →
  `_build_read_context` → ~21 `_verified_*` functions (first non-None wins, no
  LLM) → **plain LLM fallback** (Ollama `/api/chat`, `think:False`, temp 0.2,
  streaming keyed on `token_callback`).
- Read functions to wrap:
  - `operations_service.get_live_operations_snapshot()` → active calls +
    dashboard stats + unit rows.
  - `cad_service.get_active_calls()` / `cad_service.get_call_detail(cfs)` →
    `simplify_call` dicts (has `command_logs`, `assigned_units`, `raw`,
    `reporter` — must strip `raw`/`reporter` like cloud does).
  - `operations_service.get_live_unit_snapshot()` → unit roster/status.
  - `analytics_reporting.get_analytics_overview(period, start, end)` → totals,
    response times, busiest stations/units.
- `mae_tool_registry.py`: descriptive catalog only (`READ_ONLY_TOOLS`,
  `READ_ONLY_CAD_OPERATIONS`, `get_mae_tool_catalog()`), nothing callable.
- Endpoints (`app/main.py`): `POST /api/mae/chat` (non-streaming, 1664),
  `POST /api/mae/chat/stream` (streaming via thread+Queue NDJSON, 1689).
- Tests: `unittest` under pytest, `TestClient(app)`, Ollama mocked by patching
  `app.services.mae_service.httpx.post`; data functions patched directly.

## 3. Ollama tool contract (Ollama 0.32.5, qwen3.6:27b — caps include "tools")

- **Request**: add `tools: [{type:"function", function:{name, description,
  parameters:<JSON schema>}}]` to the `/api/chat` payload.
- **Response**: `message.tool_calls[].function.{name, arguments}` where
  `arguments` is a parsed **object** (not a stringified blob).
- **Feed back**: append the assistant message verbatim (with `tool_calls`),
  then one `{"role":"tool","tool_name":<name>,"content":<json string>}` per
  call, in order; re-POST.
- **Streaming + tools is unreliable** → run the tool loop with `stream:false`.
- **`think:false`** for reliable tool behavior; on `/api/chat` with
  `think:false` no `<think>` tags should leak into content (still strip
  defensively — mirrors the bug we hit on cloud/Nova).
- **Smoke-test on the live build first** (see §7 step 1): confirm (a)
  `arguments` arrives as an object and (b) `tool_name` is accepted on
  `role:"tool"` messages. These are the two contract points most likely to
  differ on this specific build; the whole design assumes them.

## 4. Design

### 4.1 New module `app/services/mae_live_tools.py`
A callable read-only registry (distinct from the descriptive
`mae_tool_registry.py`, which stays as the status catalog). Contents:
- `TOOL_SPECS`: the Ollama `tools` array — `list_active_calls`,
  `get_call_detail(cfs_number)`, `get_unit_status`,
  `get_analytics_summary(period)` — tight descriptions, strict JSON schemas.
- `LiveToolRegistry` built per request; `execute(name, args) -> LiveToolResult`
  (tool_name, source dict, JSON-safe bounded payload). Strips `raw`/`reporter`;
  bounds list sizes (≤50 calls, ≤20 command-log entries, top-5 busiest);
  validates `cfs_number` against the CFS regex; unknown tool / bad args →
  `{"error": ...}` payload, never raises.
- Reuses the existing read functions verbatim — no new CAD access, no new
  network routes. `get_analytics_summary` uses the existing `period` presets
  (24h/7d/30d/…); arbitrary-hour precision is out of scope for v1 (can port
  the cloud `hours=` work later).

### 4.2 New module `app/services/mae_tool_loop.py`
`run_tool_calling_answer(question, context, history, *, model, ollama_url,
registry, token_callback=None) -> dict | None`:
- Non-streaming Ollama loop (`stream:false`, `think:false`), max 5 rounds.
- System prompt: answer only from tool results; never guess a number; you have
  no write/dispatch tool and must never claim to have acted; keep it to a few
  sentences. (Reuse the safety tone of the existing `SYSTEM_PROMPT`.)
- Validates each `tool_call` (name in registry, args vs schema) before exec.
- Accumulates `sources` from executed tools (dedup); strips any stray
  `<think>` tags from the final content defensively.
- **Returns `None` if the model calls zero tools** → caller falls through to
  the existing plain LLM fallback unchanged (so a non-operational question
  never gets a worse answer than today).
- Explicit httpx timeout (e.g. connect 5s / read 60s).

### 4.3 Wiring in `ask_mae`
Insert one branch between the `_verified_*` chain and the plain LLM fallback:
```
verified answer?            -> return it            (UNCHANGED)
flag on AND operational-intent question -> run_tool_calling_answer; if not None, return it   (NEW)
plain LLM fallback (context-stuffed)    -> unchanged
```
"Operational-intent" reuses the intent flags `_build_read_context` already
computes (`wants_live`/`wants_analytics`/`wants_units`/`target_cfs_number`).
**Knowledge questions keep the current RAG-context path untouched** — v1 does
not turn document search into a tool (possible future increment).
- Non-streaming endpoint: return the dict as today.
- Streaming endpoint: run the loop non-streamed (tools+stream unreliable), then
  emit the final answer as a single `complete` event (like the cloud path).
  Token-by-token streaming of the final turn is a later enhancement.

### 4.4 Settings
`app/config/settings.py`: add `mae_tool_calling_enabled` (env
`MAE_TOOL_CALLING_ENABLED`, default false). Model stays `MAE_MODEL`
(qwen3.6:27b); no separate tool-model needed — it already has the `tools`
capability. Add an optional `mae_tool_max_rounds` (default 5).

### 4.5 Tests (`tests/`, unittest + pytest, mock `httpx.post`)
- `test_mae_live_tools.py`: registry returns only allowlisted fields (assert
  `raw`/`reporter` absent), CFS validation, unknown-tool/bad-arg error
  payloads, bounded sizes, analytics period passthrough.
- `test_mae_tool_loop.py`: mock a multi-round `httpx.post` (tool_calls round →
  final); zero-tool → None; round cap enforced; write/unknown tool never
  executed; `<think>` stripped; timeout path handled; sources accumulated.
- `test_mae.py` additions: flag-off leaves `ask_mae` behavior identical
  (regression guard); flag-on operational-miss routes to the loop; verified
  answers still win; knowledge path unchanged.
- Run the full MAE suite + `test_active_calls.py`; expect zero regressions.

## 5. Rollout (guarded, production)

1. Implement on `deployment/ubuntu-nvidia-227`; run the full pytest MAE suite
   locally; commit.
2. **Ship flag-OFF first**: push branch, `scripts/deploy_server.ps1`. Verify
   MAE behaves exactly as before (health gate + a manual smoke question). This
   proves the deploy is safe with zero behavior change.
3. **Live Ollama smoke test** (on .227, read-only): a tiny script/curl hitting
   `/api/chat` with one tool to confirm `arguments`-as-object and `tool_name`
   acceptance on this exact build before trusting the loop.
4. **Enable**: set `MAE_TOOL_CALLING_ENABLED=true` in the on-prem env
   (compose env / secrets), redeploy. Watch the health gate.
5. Live verification checklist (§6). If anything is wrong, the deploy script
   auto-rolls-back on health failure; manual rollback = `mv previous current`
   + rebuild.

## 6. Live verification checklist (after enabling)
1. Operational-miss question that verified funcs can't do ("which incident has
   been open longest and who's on it?") → correct tool answer with sources.
2. CFS detail follow-up ("what's the command-log history on <real CFS>?") →
   matches the call view.
3. A `_verified_*` question ("how many active calls") → still the instant
   deterministic answer, NOT the loop.
4. A knowledge question → unchanged RAG/document answer.
5. An unanswerable question ("weather tomorrow") → honest refusal, no invented
   data, no claim of action.
6. A write-shaped question ("dispatch a unit to …") → still the inquiry-only
   refusal; confirm no tool is even offered that could act.
7. Latency check: multi-tool answers complete in a reasonable time for a
   dispatcher (watch for the 27B model's tool-round cost; tune cap/timeout).

## 7. Risks / tradeoffs
- **Failure mode shifts** from "can't answer" to "plausible but wrong
  aggregation." Contained by read-only registry, arg/name validation,
  answer-from-tools-only prompt, sources shown, advisory framing.
- **Latency**: 27B model, multiple non-streamed tool rounds — slower than the
  instant verified answers. Cap rounds; keep the verified fast-paths first.
- **No token streaming on tool answers** in v1 (tools+stream unreliable) —
  acceptable; final-turn streaming can come later.
- **Build-specific contract drift** — mitigated by the §7-step-3 smoke test
  before enabling.
- **Production blast radius** — mitigated by flag-off-first, health-gated
  auto-rollback, and manual rollback path.
