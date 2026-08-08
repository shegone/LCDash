# MAE Tool-Calling Upgrade Plan — 2026-08-08

**Goal:** Let cloud MAE answer operational questions the fixed regex-intent
path cannot, by giving the model a small allowlisted set of **read-only
tools** it can call through the Bedrock Converse API (`toolConfig`), with
every guardrail enforced server-side in Python, never by the model.

**Requested by:** Ted, 2026-08-08. Implementation is delegated to a
lighter model; this plan is written to be executed without additional
context. Read the whole plan before writing code.

---

## 1. Non-negotiable boundaries (read first)

1. **No CAD writes, ever.** No tool in the registry may dispatch,
   acknowledge, page, change status, subscribe, or tone. The registry is
   the enforcement point: if a write tool does not exist, the model cannot
   call it. Do not add "just in case" tools. (See project memory
   `lcdash-safety-boundaries`.)
2. **Tools read the same data the dashboard already shows.** Phase 1 tools
   read the continuously-polled read-only CAD snapshot
   (`cloud_cad_runtime.state`) and the analytics database — the exact same
   sanitized `simplify_call()` output the dashboard, map, and existing
   live-data path consume. Phase 1 does **not** add new direct
   CentralSquare HTTP calls from the answer loop. (The snapshot *is*
   CentralSquare data, arriving through the existing verified read-only
   subscription; a direct historical `search_cfs_core` query tool is
   explicitly deferred to Phase 2 if the snapshot proves insufficient.)
3. **The existing verified-live path stays first and unchanged.** Today's
   known-good regex-intent answers (`answer_verified_live_or_none`) must
   keep answering exactly as they do now. The tool loop only runs when
   that path returns `None`. No behavior change for questions that work
   today.
4. **Feature-flagged off by default.** New env var
   `LCDASH_CLOUD_AI_TOOL_CALLING_ENABLED` (default `"false"`). The ECS
   task definition change to enable it is a separate, deliberate step
   after live verification.
5. **Answer transparency.** Every tool the model called must surface in
   the response's `data_sources` (the "Live data" chips the frontend
   already renders), e.g. `name="CAD snapshot — active calls"`,
   `kind="live"`. A tool-loop answer with zero tool calls must fall
   through to the document/RAG path, not answer from model memory.

---

## 2. Current architecture (verified against source 2026-08-08)

- `/api/cloud-ai/advisory` → `cloud_ai_advisory_api`, `app/main.py:2161`.
  Flow: `answer_verified_live_or_none(...)` (`main.py:2173-2183`) → if
  `None`, `answer_cloud_advisory(...)` RAG path (`main.py:2184-2192`).
- Streaming twin: `cloud_ai_advisory_stream_api`, `app/main.py:2198` —
  same live-first check at `:2209-2219`, synthetic single-shot NDJSON if
  live answered, else `converse_stream` RAG.
- `app/integrations/cloud_ai/verified_live_advisory.py` —
  `VerifiedLiveAdvisory(converse_client, model_id, max_output_tokens=200,
  budget=None)`; uses `client.converse(...)` with `temperature 0.0`, no
  `toolConfig`. This is the pattern to copy.
- `app/integrations/cloud_ai/live_data.py` — `detect_live_data_intent`,
  `build_live_data_facts`, `_PERIOD_TERMS` (24h/7d/30d only),
  `LiveDataSource`, `VerifiedFact` dataclasses.
- Bedrock clients: `LazyBedrockConverseClient` →
  `boto3.client("bedrock-runtime", region_name="us-east-1")`,
  `app/services/cloud_ai_service.py:132`. Budget:
  one shared `DailyRequestBudget(200)`, `main.py:195-210`.
- Analytics: `resolve_analytics_window(period, start, end, now)`,
  `app/services/analytics_reporting.py:61`; `PERIOD_OPTIONS`
  24h/7d/30d/90d/365d; `get_analytics_overview(period, start, end, ...)`
  `:876`.
- CAD snapshot shape: `simplify_call()` output,
  `app/services/cad_service.py:534-563` — keys `cfs_number,
  incident_code, incident_description, location, priority, agency, units,
  status, call_taker, call_datetime, incident_datetime, is_scheduled,
  latitude, longitude, assigned_units, command_logs, reporter, raw`.
- Generation model env: `LCDASH_CLOUD_AI_GENERATION_MODEL_ID`
  (`settings.py:125`, default `amazon.nova-micro-v1:0`).

---

## 3. Deliverables

### 3.1 New module: `app/integrations/cloud_ai/live_tools.py`

A tool registry + executor. No boto3 imports here; pure Python over data
passed in (same dependency-injection style as `live_data.py`).

```python
@dataclass(frozen=True, slots=True)
class LiveToolResult:
    tool_name: str
    source: LiveDataSource          # reuse from live_data.py
    payload: dict                   # JSON-safe, bounded
```

Three tools, each a method on a `LiveToolRegistry` class constructed with
`cad_state`, `cad_status`, and `analytics_overview_fn` (mirroring
`build_live_data_facts`'s inputs):

| Tool name | Input schema | Behavior | Output bound |
|---|---|---|---|
| `list_active_calls` | `{}` (no args) | Return every call in the snapshot, but ONLY fields: cfs_number, incident_code, incident_description, location, city, priority, agency, status, call_datetime, assigned unit numbers+statuses. **Strip** `raw`, `reporter`, `command_logs`, coordinates. | max 50 calls |
| `get_call_detail` | `{"cfs_number": "CFS26-12345"}` (validate against `CFS_PATTERN` from live_data.py) | Find that call in the snapshot; return the list fields **plus** latitude/longitude and the last 20 `command_logs` entries (timestamp, unit_number, status, text only). Not found → `{"found": false}`. | 1 call, 20 log entries |
| `get_analytics_summary` | `{"hours": int 1-8784}` OR `{"period": "24h"\|"7d"\|"30d"\|"90d"\|"365d"}` — exactly one required | Call `analytics_overview_fn` with the resolved window; return `metrics`, `busiest_stations` (top 5), `busiest_units` (top 5), `incident_types` (top 10), `latest_data_at`. If analytics unavailable, `{"available": false}`. | fixed |

Registry rules (enforced in code, unit-tested):
- Unknown tool name → return an error payload to the model
  (`{"error": "unknown tool"}`), never raise into the request handler.
- Input validation failures → same pattern.
- Every executed tool appends a `LiveDataSource` describing what was read
  and the snapshot timestamp.

### 3.2 Extend `resolve_analytics_window` for arbitrary hours

`app/services/analytics_reporting.py`:
- Add keyword-only `hours: int | None = None` to
  `resolve_analytics_window`. When given: window = `now - timedelta(hours=hours)`
  → `now`, `key=f"{hours}h"`, `label=f"Last {hours} hours"`. Validate
  `1 <= hours <= 8784` else `AnalyticsRangeError`. Mutually exclusive
  with `period`/`start`/`end` (raise if combined).
- Thread `hours` through `get_analytics_overview(...)` the same way
  `period` flows today.
- This also finally gives honest "last 8 hours" precision — the deferred
  analytics discussion item. Do NOT change `_PERIOD_TERMS` in
  `live_data.py`; the regex path keeps its presets, the tool path gets
  precision.

### 3.3 New module: `app/integrations/cloud_ai/tool_calling_advisory.py`

`ToolCallingLiveAdvisory`, modeled directly on `VerifiedLiveAdvisory`:

- `__init__(*, converse_client, model_id, registry_factory, budget=None,
  max_tool_rounds=5, max_output_tokens=400)`.
  `registry_factory` is a callable returning a fresh `LiveToolRegistry`
  per request (so each request sees the current snapshot).
- `answer(*, request_id, tenant_id, question) -> ToolCallingResponse | None`
- The Converse loop:
  1. `client.converse(modelId=..., system=[...], messages=[...],
     toolConfig={"tools": [...3 toolSpecs...]},
     inferenceConfig={"maxTokens": ..., "temperature": 0.0, "topP": 1.0})`
  2. While `stopReason == "tool_use"` and rounds < `max_tool_rounds`:
     execute each requested tool via the registry, append `toolResult`
     blocks, converse again.
  3. On final text: if **zero tools were executed**, return `None`
     (falls through to RAG — see boundary #5). Otherwise return answer +
     accumulated `data_sources`.
- System prompt requirements (write it in the module, constant):
  advisory-only; answer ONLY from tool results; if tools don't contain
  the answer say so; never guess numbers; keep answers under 800 chars
  (match `_MAX_ANSWER_LENGTH` behavior: truncate + ellipsis).
- Budget: consume the shared `DailyRequestBudget` **once per converse
  round-trip**, not once per question.
- Every model/tool exchange logged at INFO with `request_id` (no payload
  bodies in logs — names + sizes only).

### 3.4 Settings + wiring

- `app/config/settings.py`: add `LCDASH_CLOUD_AI_TOOL_CALLING_ENABLED`
  (bool-ish string, default `"false"`) and
  `LCDASH_CLOUD_AI_TOOL_MODEL_ID` (default: empty → fall back to
  `LCDASH_CLOUD_AI_GENERATION_MODEL_ID`). Nova Micro is weak for tool
  use; production will set this to the Nova Pro ID already used
  elsewhere (`us.amazon.nova-pro-v1:0` — confirm exact ID in the ECS task
  def before deploy).
- `app/services/cloud_ai_service.py`: add
  `build_tool_calling_advisory(settings, *, budget, cad_runtime,
  analytics_overview_fn)` mirroring `build_verified_live_advisory`.
- `app/main.py`, both `cloud_ai_advisory_api` and
  `cloud_ai_advisory_stream_api`: insert between the live check and the
  RAG fallthrough:

  ```
  live answer? → return it                        (unchanged)
  tool calling enabled AND tool answer? → return it   (new)
  RAG document path                                (unchanged)
  ```

  In the stream endpoint, emit the tool answer the same synthetic
  single-shot NDJSON way the live path does at `main.py:2220-2241`.
- Response shape must reuse the existing advisory response contract so
  the frontend needs **no changes** (chips render from `data_sources`
  already). Verify against `contracts.py` before inventing fields.

### 3.5 Tests (stdlib unittest style, no pytest fixtures — match repo)

New `tests/contracts/test_live_tools.py`:
- registry returns only allowlisted fields (assert `raw`, `reporter`
  absent from `list_active_calls` output)
- `get_call_detail` validates CFS format, handles not-found
- `get_analytics_summary` hours/period mutual exclusion; hours bounds
- unknown tool + bad input return error payloads, never raise

New `tests/contracts/test_tool_calling_advisory.py` (mock converse
client, the pattern in `test_verified_live_advisory.py`):
- happy path: one tool_use round → toolResult → final text; data_sources
  populated
- `max_tool_rounds` cap enforced
- zero-tool answer returns `None`
- budget consumed per round-trip
- answer truncation at 800 chars

New `tests/contracts/test_cloud_tool_calling_routing.py` (pattern:
`test_cloud_live_data_routing.py`):
- flag off → RAG path untouched, tool code never constructed
- flag on: live-path questions still answered by live path (regression
  guard for boundary #3)
- flag on + live returns None → tool path consulted before RAG
- stream endpoint parity

`tests/test_analytics_reporting.py` (existing file — add):
- `resolve_analytics_window(hours=8)` window math, label, key
- bounds + mutual-exclusion errors

Run the full suite (`python -m pytest tests/ -q`) — expect the current
full-pass count plus new tests, zero regressions.

### 3.6 Docs

Append a short section to this file after implementation:
what shipped, rev number, digest, verification notes.

---

## 4. Ship pipeline (same guarded path as revs 25-35)

1. Commit on `aws/modular-county-platform`.
2. Sync source asset:
   `cd infrastructure && AWS_PROFILE=lcdash-sandbox-admin npx cdk deploy
   lcdash-p1-logan-use1-release-builder --app "python
   release_builder_app.py" --context account=862772137583 --context
   region=us-east-1 --require-approval never`
3. Build: `aws codebuild start-build --project-name
   lcdash-p1-logan-use1-release-builder
   --environment-variables-override
   name=IMAGE_TAG,value=release-<hash12>,type=PLAINTEXT` (hash12 = first
   12 chars of HEAD). Poll `batch-get-builds` until SUCCEEDED; read
   `IMAGE_DIGEST` from `exportedEnvironmentVariables`.
4. ECR scan: `aws ecr describe-image-scan-findings --repository-name
   lcdash-p1-logan-use1-web --image-id imageTag=release-<hash12>` —
   require COMPLETE, zero findings.
5. Change set: `npx cdk deploy lcdash-p1-logan-use1-foundation --app
   "python app.py" --context account=862772137583 --context
   region=us-east-1 --parameters PilotImageDigest=<new digest>
   --method=prepare-change-set --change-set-name <name>
   --require-approval never`. **Review scope**: must touch only
   `AWS::ECS::Service` + `AWS::ECS::TaskDefinition` (env-var additions
   ride the task definition). Anything else → STOP and ask Ted.
6. Execute, poll stack to UPDATE_COMPLETE, confirm rollout COMPLETED and
   running task digest matches.
7. First deploy ships with the flag **off** — verify no behavior change
   live. Then a second change set flips
   `LCDASH_CLOUD_AI_TOOL_CALLING_ENABLED=true` (and sets
   `LCDASH_CLOUD_AI_TOOL_MODEL_ID`) and is verified with the question
   list below.

## 5. Live verification checklist (after enabling)

Ask MAE, in the browser, logged in:
1. "How many calls in the last 8 hours?" → tool path, honest 8-hour
   window, chip shows analytics source. (Was the deferred precision gap.)
2. "Which active call has been waiting the longest and which units are on
   it?" → snapshot tools, cross-checked against the dashboard feed.
3. "What's the command log history on <a real active CFS>?" →
   `get_call_detail`, entries match Command View.
4. A regex-path question ("how many active calls right now") → still
   answered instantly by the old live path (check response is unchanged).
5. A knowledge question ("how do I configure the Kenwood radio
   interface?") → still goes to the citation/RAG path.
6. A question the tools can't answer ("what's the weather?") → honest
   refusal, no invented numbers.
7. Voice: "Listen" on a tool answer speaks correctly (times expand via
   the server-side sanitizer — verify by ear).

## 6. Risks / known tradeoffs

- **Failure mode shifts** from "can't answer" to "plausible but wrong
  aggregation." Contained by: read-only registry, temperature 0, answer
  from-tools-only prompt, source chips, advisory-only banner. MAE remains
  decision support, not a system of record.
- **Latency**: each tool round is a Bedrock round-trip; cap at 5 rounds.
  Expect 3-8s worst case on multi-tool questions. Acceptable for chat;
  the stream endpoint's synthetic single-shot emit already handles the
  UX.
- **Budget**: tool rounds consume the shared 200/day budget faster.
  Monitor; raise the budget only as a deliberate follow-up.
- **Nova Micro tool-use quality**: do not enable the flag with the
  default micro model; set the Pro model ID at enable time.
