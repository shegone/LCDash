# LCDash Project Catch-Up - September 7, 2026

This is a non-secret catch-up record covering August 9 through September 7,
2026. The most recent dated handoff documents before this one stop at
August 9 (`docs/planning/AWS_HARDENING_2026-08-09.md`,
`docs/planning/MAE_AVATAR_PLAN_2026-08-09.md`). Everything between that date
and this one was recorded only in Git commit messages on `main`; this
document consolidates it so a new session does not have to reconstruct a
month from the log.

Read `AGENTS.md` first. This document does not replace the protected
credential record, the deployment runbooks, or the Package 5A authorization
gate. Where this document and current code, tests, or live read-only status
disagree, trust the code and live status.

## Repository and branch state

- Repository: `shegone/LCDash`.
- `main` is the active development branch for the AWS platform. As of this
  record its tip is `6ab57fe` (call flow cards refresh, September 7).
- `deployment/ubuntu-nvidia-227` remains the on-premises production branch
  for the `.227` server. It has not moved since the July 31 production
  record (`docs/CURRENT_PRODUCTION_STATE_2026-07-31.md`).
- `aws/modular-county-platform` was the original AWS track branch. It
  stopped at `4051e51` (August 18) and is behind `main`. New AWS work lands
  on `main`.
- One draft pull request is open: #2, "Read-only observer access for remote
  Claude Code sessions", from `claude/lcdash-aws-v11-handoff-xw2wxk` into
  `aws/modular-county-platform`. It adds a read-only status script and IAM
  policy so a remote session can verify live ECS, ALB, log, and cost state.
  Its single live run failed at the identity check because the remote
  container carried placeholder AWS keys. It has not been merged and its
  base branch is now stale relative to `main`.

## Deployment and service state at this record

This record was written from a remote session without AWS credentials, so
no live ECS, ALB, log, or cost state was verified. The last revision named
in the repository record is task definition revision 83 of
`lcdash-p1-logan-use1-web`, cited in the August 18 comment on PR #2 as the
Rapport-enabled release under watch. Treat the running revision, task
health, and month-to-date spend as unverified until a session with the
read-only observer access or a human with console access confirms them.
Nothing in this session started, stopped, deployed, or changed any service;
everything that was running before this record was left running.

## Test state at this record

With the application dependencies (`requirements-dev.txt`) and the CDK
dependencies (`infrastructure/requirements.txt`) both installed in one
virtual environment:

- `python -m pytest tests`: 1181 passed, 958 subtests passed.
- `python -m pytest infrastructure/tests`: 142 passed, 9 subtests passed.

Without the CDK dependencies, one contract test fails on import:
`tests/contracts/test_sign_out.py::SignOutTests::test_no_unauthenticated_landing_page_is_introduced`
imports `lcdash_pilot.config`, which imports `aws_cdk`. Install both
requirement files before judging the suite.

## What changed, August 9 to September 7

### Platform posture

- **AWS is now the authoritative platform.** The commit that added the ALB
  unauthenticated asset paths (`45bc8f5`, August 14) records the change of
  posture: the earlier "disconnected pilot beside the authoritative on-prem
  system" framing no longer applies. The on-premises `.227` server still
  runs and still owns its CentralSquare webhook subscriptions; nothing in
  this period changed `.227` or `.15`.
- **Phase 1 approval window is indefinite.** On August 9 the Package 5A
  record and `infrastructure/phase1_gate_evidence.json` moved the window
  expiration to the `9999-12-31` sentinel at Ted's direction. Scope,
  exclusions, and allowlists were unchanged.
- **Three static asset paths bypass ALB authentication.** The service
  worker, web manifest, and favicon were restarting the Cognito flow while
  the user was still on the login page, so every first login failed with
  a 401. `ALB_UNAUTHENTICATED_PATHS` in `infrastructure/lcdash_pilot/config.py`
  is the whole exception: three secret-free static files, one listener
  rule, pinned by synth tests. Sign-out still lands on the app root; no
  logout landing page exists.
- **Hardening landed August 9.** See
  `docs/planning/AWS_HARDENING_2026-08-09.md` for the eleven changes
  (alarms, RDS storage ceiling and deletion protection, S3 versioning,
  Cognito and ALB deletion protection, ECR immutability, GuardDuty, ALB
  access logs).
- **Infrastructure entrypoint renamed.** The CDK app is `infrastructure/cdk_app.py`;
  the old `app.py` and its `cdk.json` pointer were removed August 18. A
  Bitbucket Pipelines configuration mirroring the GitHub Actions workflow
  was added the same day for the NGA CAD Intelligence repository copy.
- **Container base image.** The pinned `python:3.13-alpine` digest was
  bumped on September 6 because the release scan flagged OS-package
  findings that exist only in the older base. The release gate requires a
  clean scan.

### Access roles

The application now has five pilot roles, resolved from Cognito groups by
`app/core/cloud_pilot_roles.py`:

| Role | Cognito group | Shape |
| --- | --- | --- |
| user | `lcdash-pilot-user` | Allowlist. Sanitized, no PII. |
| avatar | `lcdash-pilot-avatar` | Allowlist. MAE avatar conversation surface only. Added August 9. |
| dispatcher | `lcdash-pilot-dispatcher` | Denylist. Full supervisor surface minus MAE avatar page, Mindshare/JACK, and Tools and Quality. Added August 21. |
| supervisor | `lcdash-pilot-supervisor` | Full read-only operational surface plus advisory AI. |
| admin | `lcdash-pilot-admin` | Supervisor plus pilot access administration. |

The dispatcher tier is deliberately a denylist (`app/core/dispatcher_tier.py`)
so new operational routes reach dispatchers the day they ship; extending a
blocked area means naming its path there. The user and avatar tiers stay
allowlists because they exist to withhold data. The admin invite and role
UI offers all of these roles. Older planning documents that list three
groups are out of date on this point.

### MAE avatar

The avatar went through several faces in this period. The current state is
what matters; the history explains why the code looks the way it does.

- **Phase 1 web runtime shipped August 9** at `/mae/avatar`: a three.js
  runtime driven by ARKit-named blendshape weights, Polly neural viseme
  speech marks for the mouth, console and full-body booth framings, chat
  and hold-to-talk on the existing advisory and Transcribe endpoints, and
  the avatar-only Cognito tier. Until a character file existed, MAE was
  rendered from her portrait photo set with a 2D canvas renderer. That
  portrait renderer is the permanent fallback: if `/static/models/mae.glb`
  is absent or fails to load, MAE is never a blank pane.
- **Voices settled August 11.** Ruth is MAE's voice everywhere; Stephen
  replaced Matthew for JACK; Joanna and Matthew were retired. Chat and voice
  mode use Polly's generative engine; the avatar viseme path forces neural,
  because neural is the only engine that emits viseme marks. A test pins
  that the avatar's resolved voice must be neural-capable and must not come
  from operator configuration.
- **Character pipeline tooling (August 11 to 13).** `scripts/validate_avatar_glb.py`
  checks a character export for ARKit shape names, human scale, Y-up, and
  size before it ships. A converter turns a Character Creator FBX export
  into a usable GLB (ARKit renames, pruning, Draco, texture cap).
  `static/models/README.md` records what a drop-in character must be.
- **August 14, the long day.** The converted Character Creator character
  shipped, needed a Draco decoder the runtime lacked, then rendered with
  face and clothing defects that were fixed in the converter. It was then
  replaced by Grace, an Amazon Sumerian Host (CC-BY-4.0, attribution shown
  whenever she ships). Grace shipped frozen in a T-pose, was fixed through
  five stacked bake bugs, was pulled the same day because every viseme was
  crooked, and returned after the root cause was found: the standing pose
  baked into the rest pose was rewriting every viseme. The final bake also
  restored skinning on the face meshes so procedural head motion, gaze,
  and breathing can drive bones, and stripped animation clips. A morph
  checker and a bake regression test guard the two defects that shipped.
  The shipped file is documented in `static/models/README.md`, including
  two measurements that look like bugs and are correct anatomy.
- **Speech-to-face refinements (August 14).** Co-articulation is a 60 ms
  blend window rather than a timeline shift, and loudness from an
  AnalyserNode modulates mouth opening within a modest band, with lip
  closure shapes exempt. Idle head, gaze, and breathing are posed
  procedurally and gate themselves on whether bones can actually reach the
  face meshes. No JavaScript test harness exists; this was reviewed by
  reading and needs a human to listen and watch.
- **Rapport integration (August 17 to 18).** MAE can borrow a face from
  Rapport, a third-party MetaHuman pixel-streaming service, behind two
  stack parameters that both default off: `MaeRapportEnabled` and
  `MaeRapportProjectToken`. The token is a publishable client-visible scene
  token by Rapport's own design and is deliberately a plain parameter, not
  a secret. The trial character is a MetaHuman named Vivian speaking through
  Polly Ruth. Behavior when enabled: the session starts eagerly at page
  load so she is present when the page opens, parks itself after three
  minutes of silence to stop the per-minute meter, and wakes on the next
  reply. While Rapport is primary, the local renderers are hidden but keep
  running underneath; a 20-second connect watchdog, a rejected session, or
  a failed send reveals them, and speech falls back to the full local
  pipeline for that utterance. A Logan County 911 splash covers the cold
  start and the parked state. A genuine Rapport failure removes the splash
  and hands the pane back to the local avatar chain.
- **Release lessons recorded in commits.** Any release that touches the
  avatar page's CSS or JavaScript must bump the hardcoded cache-busting
  version strings in the template, or returning browsers keep running old
  code while the deploy verifies green. Separately, the release pipeline
  has an ordering dependency: CodeBuild reads the source asset that the
  release-builder deploy uploads, so the asset must be synced before the
  build starts or the image is built from the previous commit. ECR tags
  are immutable, so a mis-built tag cannot be reused; an empty commit was
  used once to mint a fresh tag.
- **Direction conflict to resolve before more `.15` avatar work.** The
  reference package under `docs/reference/mae-fullbody-refs-2026-08-11/`
  asks for a MetaHuman, Unreal, and Pixel Streaming pipeline that the
  August 9 plan superseded in favor of client-side rendering. The Rapport
  integration then supplied a hosted MetaHuman by a different route. The
  full-fidelity booth build on `.15` is still planned but its pipeline
  should be re-decided against the current state rather than the reference
  package.

### MAE answers and CAD inquiry

- **Any call by CFS number (August 19).** When a question names a CFS
  number that is not in the active snapshot, the verified-facts path falls
  back to the read-only connector's already-allowlisted `get_call`, closed
  calls included. Two stack parameters govern it: `CloudCadCallLookupEnabled`
  turns the lookup on, and `CloudCadCommandLogsEnabled` independently
  admits command-log narrative lines into answers, so a deployment claiming
  non-CJI scope keeps the second false and can prove it from its task
  definition. No CAD request happens unless a CFS number actually misses
  the snapshot. Transport failures become honest facts, not invented
  answers.
- **County-local timestamps.** Raw ISO stamps in call-received facts and
  command-log lines render as county-local time with the zone label, the
  same presentation on-prem MAE uses.
- **Active calls carry the same detail as closed calls.** The snapshot-hit
  path emits the formatted call-received fact and the flag-gated
  command-log fact through a shared helper.
- **The phrasing model no longer drops facts.** The system prompt's
  sentence and token budget could not hold a command-log walkthrough, so
  the model silently triaged facts away. The budget is now two-tier (base
  for status answers, larger when facts carry a command log), every fact
  must appear, the log is recounted chronologically, and the answer ceiling
  was raised.
- **Push-to-talk fixes (August 9 and 11).** Cloud mode captures raw 16 kHz
  PCM because Amazon Transcribe streaming accepts only PCM and Ogg Opus. An
  empty transcript is no longer reported as an outage: it returns an empty
  result that the clients render as "I did not catch that," and denial
  messages are now actionable instead of raw internal category names. MAE
  greets whoever arrives, by name when identity is verified.

### Nexis Call Flow Cards

- `/callflow-cards` (September 6) serves the NGA911 guide-card library,
  both editions, as one self-contained page from
  `templates/nexis_callflow_cards.html`: card navigation with skill-card
  cross-links, a CPR metronome, and inline diagrams, with no external
  calls. Supervisor, dispatcher, and admin reach it; the user and avatar
  tiers do not. The page carries its own prototype ribbon stating that it
  is not for operational use pending agency authorization and medical
  review.
- September 7 added original in-house diagrams for tourniquet application,
  direct pressure, and emergency delivery, and embedded two AI-generated
  demonstration clips (tourniquet, direct pressure) as data URIs, each
  labeled "AI-generated demonstration, pending medical-accuracy review."
  The AED clip is deliberately held back until a corrected take exists;
  its slot shows the AED pad-placement diagram with the video slot open.
- Three human decisions gate this page: NGA911 authorization for
  operational use, medical-accuracy review of every diagram and clip, and
  the media-source decision (AI-generated, licensed, or filmed). Dropping
  an approved clip in is a data change, not a code change.
- The render smoke test learned a `SELF_CONTAINED_PAGES` exemption so
  inline-CSS documents skip static-stylesheet checks that cannot apply.

## Open gates and holds

These were open in the August documents and nothing in the commit history
since then closes them. Each needs a named human action.

- **Live CAD read activation in the cloud.** The Package 5A Phase 2
  checklist in `docs/planning/PACKAGE_5A_AUTHORIZATION_GATE.md` is
  unchecked: named approver and window, written vendor confirmation of
  concurrent cloud access, a vendor-scoped read-only credential entered by
  a human directly into Secrets Manager, minimization and isolation
  evidence, and confirmation that `.227` stays the sole webhook owner. The
  procedure in `docs/planning/CENTRALSQUARE_READ_SECRET_OPERATOR_PROCEDURE.md`
  is procedure only and does not authorize activation.
- **`auth.logan911.com` certificate.** Waiting on a human publishing the
  ACM validation record in Cloudflare and the certificate reaching
  `ISSUED` before the Cognito custom domain can exist
  (`docs/planning/CLOUDFLARE_ACM_DNS_HOLD.md`).
- **SES production access.** Domain verification and sandbox exit were not
  confirmed complete (`docs/planning/SES_EMAIL_SENDER_SETUP_RUNBOOK.md`).
- **Private Bedrock knowledge base ingestion of the 164 approved
  documents.** Blocked on a written PII/PHI decision and chunking sign-off
  (`docs/planning/PRIVATE_BEDROCK_KB_RAG_READINESS_2026-08-05.md`).
- **Cloud analytics freshness.** Cloud analytics and reports still run on
  the one-time August 5 import; the scheduled collector in
  `docs/planning/CLOUD_ANALYTICS_INGESTION_PLAN_2026-08-08.md` has not been
  built.
- **Call flow cards** authorization, medical review, and media-source
  decision, as above.
- **Draft PR #2** needs a decision: retarget to `main` and supply real
  observer credentials to the remote environment, or close it.

## Known on-premises items still open

From the August 1 to 6 documents, unchanged by anything in this period:

- Ollama was found listening on the `.227` LAN interface
  (`docs/planning/PC227_OLLAMA_EXPOSURE_EVIDENCE_AND_OPTIONS_2026-08-06.md`).
  Remediation requires explicit approval because it touches a running
  production service.
- `.227` takes its address by DHCP; an authorized reservation is still the
  recommended follow-up (`docs/CURRENT_PRODUCTION_STATE_2026-07-31.md`).
- The iClone motion export on `.15` produced a static pose rather than an
  idle animation (`docs/CURRENT_PC15_AGENT_STATE_2026-08-01.md`).
- The Open WebUI Computer native OpenCode adapter remains disabled pending
  an upstream fix (`docs/CURRENT_OFFLINE_AGENT_STATE_2026-08-01.md`).

## Boundaries that did not change

- CAD is inquiry-only everywhere. No write, acknowledge, dispatch, page,
  tone, subscription, or webhook capability exists in the cloud tool or
  connector registry, and none may be added without a separately designed,
  approved, and audited capability.
- Secrets never enter chat, Git, logs, prompts, or handoffs. Humans enter
  them directly into AWS Secrets Manager or the protected on-prem record.
- Production `.227`, workstation `.15`, and the Windows working copy are
  outside any AWS work unless a task explicitly authorizes an interface.
- Cloud AI is advisory and can never be a dependency of call routing, CAD,
  radio, station alerting, or any other emergency operation.
- Tests use synthetic data only.

## Codex catch-up

`main` at `6ab57fe` is green (1181 application tests, 142 infrastructure
tests) when both requirement files are installed. Since August 9 the AWS
deployment became the authoritative platform with an indefinite approval
window; five Cognito-backed roles exist including the new dispatcher and
avatar tiers; MAE's avatar is a skinned Sumerian Host character with an
optional, default-off Rapport MetaHuman front end; MAE can answer about any
call by CFS number with command logs behind their own flag; and the Nexis
Call Flow Cards prototype page exists behind a not-for-operational-use
ribbon. No live CAD activation, credential, SES, certificate, or knowledge
base gate was cleared in this period. Nothing on `.227` or `.15` was
touched.

Exact next action: verify the live AWS state that this record could not
(running revision, task and target health, spend against the USD 200
budget), either by supplying the read-only observer credentials from PR #2
to a remote session or by a human reading the console, and append the
result to this document. After that, work the open gates above in the
order listed; any further `.15` avatar work needs a pipeline decision
first.
