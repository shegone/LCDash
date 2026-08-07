# Cloud AI/voice parity deployment result

Date: 2026-08-07, overnight session following on from
`CLOUD_AI_VOICE_PARITY_SESSION_HANDOFF_2026-08-07.md`. That document is now
superseded by this one for anything it disagreed with — it was written before
tonight's wiring, IAM fix, and deploy. Keep it for history only.

## What changed since the prior handoff

1. **Cloud voice capture was wired in.** `static/js/lcdash-voice-capture.js`
   (16 kHz mono PCM16 for Amazon Transcribe) existed but was never called from
   either voice page — cloud voice mode was dead-on-arrival regardless of
   everything else being correct. Both `lcdash-mae.js` and
   `lcdash-mindshare.js` now: retain the microphone's source node instead of
   discarding it, branch `beginListeningCycle` on cloud/on-prem to start
   `LCDashVoiceCapture` instead of `MediaRecorder`, fix the level monitor's
   capture-liveness check (it read `mediaRecorder.state`, always `undefined`
   in cloud mode, so the natural-pause detector silently never ran and a clip
   could never end), and declare `audio_format`/`sample_rate_hz`/
   `duration_seconds` on the transcribe request so the PCM contract is met
   instead of the server's `webm-opus`/48000 default. Both templates load the
   capture script with a cache-buster bump.
2. **A real deploy blocker was found and fixed.**
   `app/services/cloud_ai_streaming.py` calls Bedrock `converse_stream`,
   which needs `bedrock:InvokeModelWithResponseStream` — a different IAM
   action from `InvokeModel`. The task role didn't have it, and
   `infrastructure/tests/test_offline_policy.py` actively asserted it must
   never appear. That assertion was a least-privilege guardrail written
   before streaming existed (the same pattern used for
   `transcribe:StartStreamTranscription`), not a permanent ban — confirmed by
   checking the commit that introduced it. Granted on the identical resource
   ARNs and region condition as the existing `InvokeModel` statement; flipped
   the test to require it instead of forbid it.
3. **Four stale test assertions from the previous session's JS rewrite were
   fixed** (speech-grouping threshold, a renamed variable, a broadened URL
   regex, a reformatted voice ternary) — all cases where the underlying
   safety property was intact or strengthened and only the literal source
   string had drifted.
4. **`county_commission_job_api`** computed its job status and returned
   nothing (a tenant-context refactor dropped both the 404 guard and the
   `return`). Restored, matching on-prem.
5. **NOVA got an honest unavailable state** instead of a silent 503/timeout.
   It calls a local Ollama instance that only exists on-prem. Every existing
   Bedrock advisory path in this codebase is retrieval-and-citation-enforced
   by construction, so a full NOVA rebuild needs its own reviewed advisory
   class — out of scope for tonight. `get_nova_status`/`ask_nova`
   short-circuit in cloud mode with no network attempt; the page renders a
   clear banner and disables the composer server-side.
6. **A real data-loss clock was found and defused.** The only off-instance
   copy of the 2026-08-05 historical-analytics import
   (`analytics-history-20260805T201931Z-2165c7c3e886892a.json.enc`, the RDS
   instance's actual data) sat in an S3 prefix with a 3-day lifecycle
   expiry — it would have been deleted 2026-08-08 ~20:19 UTC. Copied
   byte-for-byte to `retained/tenants/logan-synthetic/historical-analytics/…`
   in the same bucket, outside the expiring prefix filter, KMS encryption
   preserved. Original left untouched (still expires on schedule; the
   `retained/` copy is now the durable one).
7. **The "analytics is permanently empty" claim in the prior handoff was
   stale, not current.** It was written before the 2026-08-05 13:11 UTC
   import ran. Two independent sources (the importer's own CloudWatch log
   group, and the previously-cited sanitized JSON) agree: `calls: 1769,
   unit_responses: 2051, call_agency_times: 729, units: 104,
   saved_analytics_widgets: 1`. Live RDS status is `available`, created the
   same day the import ran, never replaced since. Web-service logs since
   then show consistent 200s on `/analytics` with zero DB connectivity or
   schema errors in the retained 7-day CloudWatch window. What actually
   remains broken is *ongoing* freshness — nothing re-syncs it, and
   attempting that hits real blockers (see Still Open below). The data
   itself is not empty.

## What is LIVE right now (deployed and verified 2026-08-07, ~07:10 UTC)

- ECS task definition **revision 25**,
  digest `sha256:fb582ddceddc721769e40e5c613cd113149ba6a167fb1f5a2047b3831c7eaa14`
  (ECR tag `release-08d4fa9949b0`, source commit `08d4fa9949b0d26b979ab31e9e2880d59b63fe95`).
- Deployed via a named, reviewed CloudFormation change set
  (`lcdash-voice-parity-08d4fa9949b0`), created with `--no-execute`, its full
  resource-change scope read and confirmed (task role IAM policy modify, ECS
  service modify, ECS task-definition modify-with-replacement — the last is
  normal CFN behavior for any task-def content change, not a real teardown)
  before execution. Stack reached `UPDATE_COMPLETE` with no rollback.
- ECS: single PRIMARY deployment, `rolloutState COMPLETED`, 1/1 running,
  running task's container digest confirmed matching by direct
  `describe-tasks` call (not assumed from the task-def pointer).
- ALB target for the new task is `healthy`; the old rev-24 task drained
  normally.
- Both new IAM grants confirmed via `iam simulate-principal-policy` against
  their actual scoped resource ARNs and region condition context (not just
  read from the template) — `allowed` for both
  `bedrock:InvokeModelWithResponseStream` and
  `transcribe:StartStreamTranscription`.
- CloudWatch logs for the new task since startup: clean `Uvicorn running`,
  steady `GET /health 200 OK`, **zero** ERROR/Exception/Traceback/CRITICAL
  lines.
- ECR scan of the deployed image: `COMPLETE`, zero findings at every
  severity.
- All 740 local tests pass. CDK synth verified clean against the new IAM
  statement before deploy.

## What is NOT verified yet — needs a human with a browser

The whole app sits behind Cognito auth at the ALB. I could not click through
MAE/JACK voice mode, and could not directly exercise a real
`StartStreamTranscription` or `converse_stream` call — infrastructure
readiness (IAM, image, health) is confirmed; the actual first live use of
each is not. **Manual test needed:** open `aws.logan911.com/mae` (and
`/mindshare-technical` for JACK), start voice mode, speak a question, confirm
a transcript comes back and MAE/JACK responds, and confirm speech begins
within about a second of the first sentence rather than after the whole
answer. If voice mode doesn't work, check the browser console first — the
capture module requires `AudioContext`/`ScriptProcessorNode`, which all
current major browsers support, but this was never exercised against the
real Transcribe service before tonight.

Rollback digests if anything regresses:
`sha256:2019ecdf6a4631e5175200cb0f6404abf90bd9b23974eeb6068e452b0bba384c`
(rev 24, prior live) or look up rev 23 (Nova Pro without tonight's or
yesterday's UI/voice work) if a deeper rollback is ever needed.

## Deliberately NOT done tonight, and why

- **RDS `backup_retention=0` / `deletion_protection=False` / `removal_policy
  DESTROY` were left unchanged.** These looked like a data-loss gap and I
  drafted a fix, then found `infrastructure/tests/test_offline_policy.py`
  and `infrastructure/approved_shape.json` both explicitly require this
  exact posture (`database_backup_days: 0`, `database_final_snapshot:
  false`) as a reviewed architectural decision for this disposable
  pilot-phase tenant. Overriding it would repeat, in the opposite direction,
  the same mistake almost made with the streaming-permission test earlier
  tonight. The S3 backup made in item 6 above is the actual mitigation that
  belongs here — if the RDS instance is ever lost, on-prem remains the
  source of truth and the retained encrypted export can re-seed it (see
  "Still open" below for the cost of a full re-sync).
- **GIS reference layers were not shipped to cloud**, despite being an
  obvious-looking "just add a COPY line" fix. It is a deliberate
  data-sensitivity boundary: `.gitignore` excludes both raw and generated
  layers, a manifest gates GIS behind "a separate reviewed GIS import and
  tenant-data classification process," and the deployed tenant's own
  synthetic profile doesn't even declare the same layer set the real files
  contain. The current empty-state degrades gracefully (`/map` still plots
  incidents; only the layer control is empty). Do not resolve this by
  copying `/srv/lcdash-data/gis-public` without a real classification
  decision.
- **Knowledge PDFs still 404, and were not fixed tonight.** The right fix is
  serving from the two S3 prefixes already ingested into the Bedrock KB
  (164 objects, 240 MB, a signed approval gate 176 sibling files failed) —
  not baking the directory into the image, which doesn't exist on this
  machine anyway and would silently re-admit files that failed review.
  Needs a new S3 read path and IAM grant (~5-8 hours); see
  `document_library_stack.py`, which already defines a dormant read role for
  exactly this bucket.
- **NOVA's full Bedrock rebuild was not done**, only the honest-unavailable
  stopgap (see item 5 above). The rebuild needs a new grounded-but-uncited
  advisory class, which is a policy decision (the project's standing rule is
  "never present uncited model knowledge as an approved manual answer"), not
  something to freelance overnight.

## Still open, lower priority

- **Analytics freshness.** The data is real (item 7 above) but frozen at
  2026-08-05 and getting staler. A full live sync needs three things that
  don't exist yet: CentralSquare credentials plumbed into the ECS task (the
  cloud connector only resolves them via Secrets Manager for the read-only
  CAD poll, not for the analytics collector), a `/cfs_analytics` endpoint
  added to the reviewed cloud connector allowlist (the collector needs
  `get_cfs_analytics()`, which the cloud connector doesn't implement at
  all), and an amendment to `approved_shape.json`'s `collector_count: 0`.
  That's a review cycle, not an evening. A cheaper partial option exists —
  driving `calls`/volume/agency-mix from a closed-call CAD search already
  inside the allowlist — but it would leave response-time and unit-workload
  panels blank and duplicates data the warehouse already holds better. If
  the snapshot is ever needed refreshed without live sync, the already-built
  one-time import pipeline (`analytics_import_stack.py`) can be re-run.
- **Your own Google OAuth client_id** for `.227`'s Drive knowledge sync and
  offsite backups — rclone's shared client_id retires during 2026.
- **`.227` Ollama exposure and cloudflared metrics binding** — already fixed
  2026-08-06/07, see the prior handoff for detail; nothing further needed.
- **MAE avatar direction** (separate track, `.15` workstation) — see
  `mae-avatar-direction` memory, unchanged tonight.

## Session-specific lessons worth not re-learning

- **A test asserting `assertNotIn` for a permission/action is not
  automatically a hard security wall — check the commit that introduced it.**
  Twice tonight the same shape of question came up (Bedrock streaming IAM,
  RDS backup posture) and the answers were opposite: one was a stale
  least-privilege guardrail from before a feature existed (fix the code and
  the test together), the other was a deliberately reviewed, tested,
  documented architecture decision (`approved_shape.json` is the tell — if a
  posture is asserted there as well as in a test, it's deliberate).
- **A subagent's own hard boundary against executing infrastructure
  mutations from prompt text should not be routed around, even under broad
  delegated authority.** It refused correctly; the right response was to run
  the already-reviewed, already-authorized command directly through the
  main session's own tool access (which is gated by the actual harness
  permission system and this project's committed `.claude/settings.json`
  allowlist), not to find a way to make the subagent comply.
- **`iam simulate-principal-policy` without `--resource-arns` tests against
  the implicit wildcard resource.** A resource-scoped grant will correctly
  show `implicitDeny` in that shape even when it's genuinely working — pass
  the actual resource ARN and any required context keys (e.g.
  `aws:RequestedRegion`) before concluding a permission didn't take.
- **Git Bash on Windows silently mangles any Bash argument starting with
  `/`** (MSYS path conversion) — `aws logs describe-log-groups
  --log-group-name-prefix "/lcdash"` fails with a regex-validation error
  that has nothing to do with the actual problem. Prefix the command with
  `MSYS_NO_PATHCONV=1`.
