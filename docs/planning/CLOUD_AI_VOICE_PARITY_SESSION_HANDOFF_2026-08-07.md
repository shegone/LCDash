# Cloud AI/voice parity session handoff

Date: 2026-08-07
Scope: continuation handoff for a new chat thread. Covers what shipped, what
is built-but-not-deployed, what a background agent is still finishing, and
the exact next steps.

## How to use this document

Paste this file (or point the assistant at its path) into a new thread as
the starting reference. Verify live AWS/on-prem state before changing
anything — this snapshot can go stale the moment anyone deploys.

## What is LIVE right now (verified 2026-08-07)

- ECS task definition **revision 24**,
  digest `sha256:2019ecdf6a4631e5175200cb0f6404abf90bd9b23974eeb6068e452b0bba384c`,
  1/1 running, ALB healthy.
- Model: **Amazon Nova Pro** (`us.amazon.nova-pro-v1:0`). Rev 23 fixed cloud
  MAE/JACK being completely dead (the prior `us.anthropic.claude-sonnet-5`
  profile was rejected by the Bedrock runtime for this account).
- Rev 24 added: dashboard grid-collapse fix (unclosed `<h3>`/`<div>` in
  `templates/dashboard.html`'s connected branch) and a model-derived
  `/api/mae/status` label instead of a hardcoded "Claude Sonnet 5" string.
- On `.227`: Google Drive knowledge remote `lcdash-knowledge` provisioned
  (`drive.readonly`), both sync containers verified recovering. Ollama
  rebound to `127.0.0.1:11434` (was LAN-exposed on `14.1.1.227:11434`,
  unauthenticated — see `PC227_OLLAMA_EXPOSURE_EVIDENCE_AND_OPTIONS_2026-08-06.md`
  for the original finding, now resolved). cloudflared metrics pinned to
  `127.0.0.1:20241` (was `0.0.0.0`, a Docker `network_mode: host` default).
  Both changes applied to `E:\Projects\LCDash\deploy\compose.yaml` (source of
  truth) and the live `/srv/lcdash-platform/current/deploy/compose.yaml`;
  verified from off-host that both ports now refuse connections and the
  public tunnel still serves normally.

## What is BUILT LOCALLY but NOT YET DEPLOYED

Everything below is committed to disk in `E:\Projects\LCDash-AWS`, passes
`python -m pytest tests/` (**607/607 passed** as of this handoff), and CDK
synth succeeds, but has **not** gone through a build/scan/change-set/deploy
cycle. Needs the guarded release path before it reaches
`aws.logan911.com`.

### Cloud/on-prem parity fixes (from a full parity audit)
- **JACK unlocked in the UI.** `templates/mindshare_technical.html` and
  `templates/mindshare.html` had `data-approved-source="false"` hardcoded,
  disabling the question box, send button, and voice toggle even though the
  Bedrock backend (`/api/mindshare/chat`) worked correctly the whole time.
  Routes now pass `cloud_presentation_status` and the template branches on
  `advisory.ready`. This was shipped-but-broken since commit `a21c32a`.
- **Units board populated.** `build_cloud_unit_snapshot` in
  `app/services/operations_service.py` now joins each unit's
  `assignment_cfs_number` against the matching call to fill `location`,
  `incident_description`, `incident_code`, `priority`, `call_datetime` — all
  already-allowlisted `CALL_FIELDS`. Per-unit CAD timers (dispatch/enroute/
  arrival/clear) and `status_timer_start` are **not** in `UNIT_FIELDS` at
  all and can't be filled without widening the CAD read contract, so
  `templates/units.html` now hides that grid in cloud mode
  (`{% if not cloud_presentation %}`) instead of showing empty cells.
- **Map now plots incidents.** `app/main.py`'s `/map` route builds from the
  live cloud snapshot when the CAD bridge is enabled; `operations_service.py`
  now includes allowlisted `latitude`/`longitude` in the call list (previously
  detail-view only). Confirmed safe: `_address_coordinates` in
  `cloud_read_runtime.py` already rejects non-finite/out-of-range/`0,0`
  coordinates *before* they ever reach `state.calls`, so no invalid values
  ever flow through. Unit positions remain deliberately absent — only
  incidents plot. `tests/contracts/test_cloud_cad_display_bridge.py`'s
  coordinate test was updated to assert the new (deliberate) contract.

### Cloud AI: STT + streaming speech (three pieces, built via delegated agents + my own review/wiring)
1. **Amazon Transcribe streaming STT** — new
   `app/integrations/cloud_ai/transcribe_provider.py`
   (`AwsTranscribeStreamingProvider`), wired into all four `CloudAiRuntime`
   builders in `app/services/cloud_ai_service.py`. Uses the community
   `amazon-transcribe` SDK (not official AWS, but its only dependency
   `awscrt` is AWS-maintained) because batch Transcribe is contractually
   impossible here — `TranscribePushToTalkRequest` forbids persisting audio,
   and AWS batch transcription is S3-in/S3-out only. IAM:
   `transcribe:StartStreamTranscription` scoped to `us-east-1`
   (`infrastructure/lcdash_pilot/foundation_stack.py`) — the action has no
   resource-level permissions in AWS's own service reference, so
   region-lock is the tightest available scope. `/api/voice/transcribe` in
   `app/main.py` now dispatches to it via `run_in_threadpool` (the SDK is
   async internally; the route is not) and returns 503 with
   `status["stt"]["disabled_reason"]` on failure. Verified: the client
   (`asyncio.wrap_future`) binds to whichever loop is running at call time,
   not one captured at construction, so reusing the cached client across
   worker threads is safe. One low-severity, pre-existing upstream issue
   noted: `AwsCrtHttpSessionManager._connections` is an unsynchronized dict,
   so two *concurrent first-time* connections could race (at most a leaked
   connection, never a wrong transcript) — not worth blocking on for this
   scale.
2. **Sentence-streamed speech** — new `app/services/cloud_ai_streaming.py`:
   consumes Bedrock `converse_stream`, releases complete sentences as soon as
   confirmed (boundary only accepted once the character *after* the
   terminator has arrived and is whitespace — eliminates decimal/abbreviation
   cut bugs by construction), sanitizes every chunk so **source URLs can
   never reach Polly** (this is a firm project rule, enforced twice: once
   server-side per chunk, once again if a hand-crafted client request tried
   to bypass it). New routes in `app/main.py`:
   `POST /api/cloud-ai/advisory/stream` (NDJSON: `status` → `chunk`* →
   `complete`/`error` → `done`) and `POST /api/cloud-ai/speech/sentence`.
   Client side: `static/js/lcdash-mae.js` and `static/js/lcdash-mindshare.js`
   got a sequential audio queue (one `<audio>` element, synthesis for chunk
   N+1 overlaps playback of chunk N, single `AbortController` per session,
   clean interruption on a new question). Measured time-to-first-audio:
   **~2.6-3.0s, down from ~4.1s** (retrieval's 1.6s is now the dominant
   remaining cost). Both pages fall back to the whole-answer path if the
   stream 404s/405s or fails.
3. **Shared daily budget.** The two independently-constructed advisory paths
   (whole-answer `GroundedBedrockAdvisory`, streaming
   `StreamingGroundedAdvisory`) originally each enforced their own 200/day
   cap, silently doubling the effective limit. Added
   `DailyRequestBudget` to `app/integrations/cloud_ai/bedrock_retrieval.py`;
   `app/main.py` now constructs one `cloud_advisory_budget` and injects it
   into both builders. Backward compatible — omitting `budget` still gives a
   caller (e.g. standalone tests) its own private counter.

### Still in progress — one background agent running

A background agent is fixing the actual reason **MAE voice mode would not
start at all**: the browser recorder captures `audio/webm;codecs=opus`,
which Amazon Transcribe streaming cannot ingest (only `pcm` or `ogg-opus`).
Since cloud STT was never wired before tonight, `stt.ready` was always
`false` and the voice toggle button was silently dead-on-arrival since it
first shipped — not a new regression.

Scope given to the agent: a new shared `static/js/lcdash-voice-capture.js`
module doing correct Web-Audio PCM capture (ScriptProcessorNode, hand-verified
linear-interpolation resampling from whatever `audioContext.sampleRate`
actually is down to 16000 Hz — browsers do not reliably honor a requested
rate), wired into the cloud branch of `beginListeningCycle` in both
`lcdash-mae.js` and `lcdash-mindshare.js`, leaving the on-prem
MediaRecorder/webm path and the already-built sentence-streaming code
completely untouched. It was told to run `node --check` on all three files
and `python -m pytest tests/test_voice.py tests/test_mae.py` before
reporting, and to flag anything it isn't fully confident about rather than
asserting false confidence.

**When this lands:** review its diff and test output the same way the STT
and streaming-speech agents' work was reviewed earlier tonight (read the
actual source, verify claims like the resample math independently, don't
take the report at face value), then move to the build/deploy step below.

## Immediate next steps, in order

1. **Wait for / review the voice-capture agent's report.**
2. **Full local verification pass**: `python -m pytest tests/` (expect
   607+ passed), `node --check` on any touched JS, CDK synth check.
3. **Guarded release** (same path used for revs 22-24): publish a new CDK
   source asset (`cdk deploy lcdash-p1-logan-use1-release-builder --app
   "python release_builder_app.py" ...`), start CodeBuild with the new
   `release-<hash12>` tag, confirm ECR scan is `COMPLETE` with 0 findings,
   create a **named** CloudFormation change set (`--no-execute
   --change-set-name <name>`), **review its resource-change scope before
   executing** — prior releases stayed scoped to task def + service (+ task
   IAM when Bedrock permissions changed), execute, verify ECS rollout +
   ALB target health + the deployed digest, then do one MAE and one JACK
   smoke test (retrieval + a live spoken answer if voice mode is confirmed
   working).
4. **Manual click-test voice mode** in a real browser once deployed — the
   test suite only asserts JS source content, not actual browser audio
   behavior. Confirm: mic permission → recording starts → transcript comes
   back → MAE/JACK responds → speech begins within ~1s of the first
   sentence, not after the whole answer.
5. Rollback digest if anything regresses: `sha256:2019ecdf…` (current live
   rev 24) or `sha256:08f20c26…` (rev 23, Nova Pro without any of tonight's
   UI/voice work).

## Still open, lower priority

- **Your own Google OAuth client_id.** rclone's shared client_id is being
  retired "during 2026." Both the `.227` knowledge syncs and the encrypted
  offsite backups depend on it. ~10 minutes in Google Cloud Console;
  offer to walk through it screen-by-screen when convenient.
- **Analytics is permanently empty in cloud** — `Dockerfile.aws-pilot`
  never copies `scripts/analytics_worker.py` into the image and nothing
  calls `run_analytics_sync`. This is the single biggest remaining parity
  gap (analytics is the richest on-prem surface: 10 KPIs, 6 charts, a
  dispatcher mega-panel) and cascades into `/reports` (409s) and
  `/api/mae/analytics-report` (503). Needs a packaging decision, not just a
  code fix.
- **Heatmap** stays hard-empty (genuinely needs imported history, blocked on
  the analytics gap above). **NOVA chat** page calls `127.0.0.1:11434`
  (a local Ollama that doesn't exist in ECS) — always 503, needs either a
  Bedrock-backed implementation or an honest "not available in cloud" state.
  **Knowledge PDFs** 404 and **GIS reference layers** are unavailable
  because `knowledge/` and `data/gis-public` aren't in the container image.
- **`county_commission_job_api` dead-code bug** (`app/main.py`, around the
  job-status endpoint): a `return job` block is unreachable after an earlier
  `return Response(...)` — currently masked because job creation 409s
  first, so nobody's hit it. Small, isolated fix.
- **MAE avatar direction** (separate track, `.15` workstation): build the
  character in Character Creator 5 (already owned), drive with NVIDIA
  Audio2Face-3D (open-sourced, MIT, fits the RTX 3090), render via a
  three.js web runtime rather than Unreal Pixel Streaming (1 GPU = 1
  concurrent stream, and AWS GPU hosting alone would exceed the whole
  pilot budget). MetaHuman Creator's web app shuts down 2026-11-05 — do not
  start new character work there. No trustworthy MetaHuman MCP server
  exists; keep MCP out of the production avatar path entirely.

## Session-specific lessons worth not re-learning

- **Compound shell commands defeat the permission allowlist.** One plain
  command per Bash call, starting with the binary — no `echo` banners, no
  `VAR=x && ...` chains. "Allow always" saves the literal string, which
  then never matches again.
- **`defaultMode: bypassPermissions` must live in a *user-trusted* settings
  file** (`~/.claude/settings.json`), not a project file — and it only
  takes effect on sessions that *start* after the file exists. A project
  `.claude/settings.json` with the same key is silently not honored (by
  design — a checked-in repo file disabling all prompts would be a
  security hole).
- **`docker run ... rclone config` as root** rewrites the mounted
  `rclone.conf` to `root:1000`, locking out every sync container including
  offsite backups, until `chown 1000:1000` is run again. Always pass
  `--user 1000:1000` to the rclone container.
- **The `.227` `compose.yaml` has CRLF line endings** (deployed from a
  Windows repo) — a bare `sed '/pattern$/a...'` silently fails to match
  because `$` can't get past the trailing `\r`. Use Python with explicit
  `\r\n` byte sequences for scripted edits there.
- **Server clock is EDT; container logs are UTC** — a 15-minute sync loop
  that looks "stuck" at first glance may just be timezone confusion.
