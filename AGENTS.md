# LCDash Agent Charter

These instructions apply to every AI coding agent working in this repository.

## Platform state

**The AWS pilot is the only LCDash.** It runs in account `862772137583`,
`us-east-1`, at `https://aws.logan911.com`, from branch `main`. The
on-premises platform on `.227` was retired and deleted on 2026-09-14
(containers, images, volumes, secrets, recordings, Cloudflare tunnel, Access
and DNS); a final database dump archive exists and must not be touched.
Nothing here deploys to `.227`. On-premises documents are history, marked
retired.

## Mission

Continue the Logan County LCDash program safely and sequentially until the
approved roadmap is complete: a polished, futuristic, dark, Logan County
branded operations platform that protects emergency operations, sensitive CAD
data, credentials and production reliability.

The agent is a development assistant, not an emergency dispatcher. It must not
make operational decisions or modify CAD, radio, call-routing, station-alert
or public-warning records.

## Sources of truth

Read what the task needs: `docs/PROJECT_ROADMAP.md`, the newest file in
`handoffs/`, `AWS_WORKSPACE.md`, `infrastructure/README.md`, the module's docs
(radio: `docs/MINDSHARE_RADIO_*.md`,
`handoffs/session-handoff-2026-10-17-radio.md`), the matching `agent-skills/`
skill, and Git state.

Current code, tests, Git and live read-only AWS state beat any old summary.
Do not infer what is running from the development clone; read the ECS service,
task definition and ECR tag.

## Machine roles

### AWS account `862772137583` - the LCDash platform

- Fargate web task behind an HTTPS ALB with Cognito login, PostgreSQL RDS,
  Bedrock, Transcribe, Polly and Amazon Location, defined in
  `infrastructure/` with the AWS CDK.
- Identity Center profiles: `lcdash-phase1-deployment` runs releases and
  read-only status checks; `lcdash-sandbox-admin` is for IAM changes only.
- Login: `aws sso login --sso-session lcdash --use-device-code --no-browser`;
  in Git Bash set `MSYS_NO_PATHCONV=1`.

### PC `.227` - Hermes and Mindshare radio listener host (not LCDash)

- `14.1.1.227`, hostname `lcdash-server`. Runs the Hermes containers (Ollama
  brain, ComfyUI), n8n for Hermes, and the Mindshare radio listener (decided
  2026-10-16): three systemd services under `/opt/mindshare-radio-capture` for
  listen-only multicast capture, local faster-whisper transcription and a
  signed upload to the AWS pilot. Source: `radio_listener/`.
- Must not run Unreal, MetaHuman, video generation or other sustained
  avatar-rendering workloads.

### PC `.15` - avatar and video workstation

- `14.1.1.15`; Windows, RTX 3090. Owns Unreal/MetaHuman, Pixel Streaming and
  the 512 x 1536 portrait LED display. Preserve Windows; use NVIDIA Studio
  drivers. Its agent maintains `LATEST_PC15.md` in the shared `MAE Progress
  Handoffs` folder (see `docs/OFFLINE_AGENT_OPERATIONS.md`).

### Primary Windows development workstation

- `E:\Projects\LCDash-AWS` on `main` is the only working copy for changes,
  tests, commits and releases; leave `E:\Projects\LCDash` alone.
- One `git push origin main` reaches both remotes (GitHub `shegone/LCDash`,
  Bitbucket `nga911rnd/dashboard`).

## Hard architecture boundaries

- Cloud intelligence must never become a dependency of call routing, CAD,
  ESInet, radio, station alerting, or other emergency operations.
- The pilot stays synthetic and disconnected from live CAD until the Package
  5A gate in `docs/planning/PACKAGE_5A_AUTHORIZATION_GATE.md` is cleared by a
  named human. CAD access, when authorized, is inquiry-only.
- Keep MAE, Mindshare Technical Assistant, Mindshare Radio Intelligence, NOVA,
  Station Alerts, and CentralSquare Operations separately permissioned.
- Keep the animated avatar optional. Text, audio, operational data, and safety
  controls must remain usable when `.15` or the renderer is unavailable.
- Infrastructure changes go through `infrastructure/` and the Phase 1
  deployment allowlist. A resource type the allowlist prohibits needs a
  documented, human-approved exception there, never a silent edit.

## Work loop

Per roadmap item: inspect current state; state the outcome and a short plan;
change one bounded thing at a time; add or update tests; run focused, then full
relevant tests; review the diff for secrets, sensitive CAD content, unsafe
writes and scope creep; update docs; commit only a coherent, tested change;
deploy only under the rules below.

## Execution protocol

- Before acting, report branch, HEAD and pre-existing changed files. Never overwrite, revert, discard, stage or combine
  pre-existing changes unless the task owns them. Do not mix unrelated docs,
  code, infrastructure, `.227` and `.15` work in one work package.
- Verify claims against tool output and quote exact branch, file and test
  names. A local remote-tracking ref is not proof of remote state.
- After a failed command, inspect the result before retrying. After the same
  failure twice, stop, preserve state and report the blocker. Never weaken
  tests, safety checks, authentication, audit, privacy filtering or fallbacks
  to get a pass.
- Escalate to the owner when a task touches production deployment, rollback,
  credentials, networking, authentication, authorization, backup policy or
  public-safety outputs; when docs and code materially contradict; when you
  cannot show a change keeps existing safety behavior; or when unsure whether
  an action is read-only. Escalation is a successful safety outcome.

## Agents and models

Workers and subagents run as cheaper Sonnet models, at most four at once, with
non-overlapping edit scopes. This charter overrides any skill.

## Approvals

Autonomous: read files and non-secret docs; inspect Git; read live AWS state
through the profile's read-only calls; work in a feature or assigned safe
branch; edit source, tests and docs; run local tests and read-only
diagnostics; draft plans and handoffs; commit a tested change if the operator
has enabled local commits.

Explicit human approval is required for:

- Executing a CloudFormation change set, or any other change to a running AWS
  service.
- Pushing to GitHub `main` (which also mirrors to Bitbucket).
- Changing IAM policies, permission sets, or the permissions boundary;
  changing Cognito users, groups, or the app client.
- Changing Cloudflare, DNS, certificate, or security settings anywhere.
- Installing or removing software on `.227` or `.15`, or starting, stopping,
  or removing any container there. The Mindshare radio services on `.227` are
  authorised for this project; everything else on `.227` and `.15` still needs
  approval.
- Creating, rotating, reading, copying, or exposing credentials, tokens,
  passwords, keys, OAuth records, or the protected credential record.
- Enabling live EMS delay delivery, station announcements, paging, CAD writes,
  acknowledgments, or any other operational output.
- Activating live CAD reads, or modifying CentralSquare subscriptions or
  webhook security.
- Changing backup scope, retention, encryption, or restore procedures.
- Connecting `.227` and `.15` through a new network service.
- Uploading public-safety data or source documents to an external service.
- Deleting data, histories, backups, releases, branches, or working copies.

Use confirmation mode. Never run an offline agent in unrestricted
always-approve mode against production systems.

## Production deployment rules

The pilot is released only through the guarded path; there is no second one.

1. The working tree is clean, the commit is on `main`, and it is pushed to
   both remotes.
2. The full test suite passes (`python -m pytest tests/` and
   `python -m pytest infrastructure/tests/`).
3. Deploy the release-builder stack so CodeBuild reads the source asset for
   this commit, then start a build with `IMAGE_TAG=release-<first 12 hex of
   HEAD>` and wait for `SUCCEEDED`.
4. Read the ECR scan for the new digest. The only accepted findings are the
   util-linux baseline and the zlib finding accepted 2026-10-17; anything else
   stops the release.
5. Create a **named** change set on the foundation stack with only
   `PilotImageDigest` changing (plus any reviewed infrastructure change),
   review that its scope is the standard five resources (two task definitions,
   the service, the collector's rule and policy) plus any such change, then
   execute it only after the owner types the exact change-set name.
6. Verify the ECS rollout completed, the ALB target is healthy, and a
   signed-in page shows the caller's name and role.
7. Rollback is a change set with the previous digest. A replaced task
   definition revision is deregistered by CloudFormation and cannot be
   re-pointed to directly.

Run CDK as `npx --yes aws-cdk@2.1145.0 ... --method=prepare-change-set
--change-set-name <name>`.

Dependencies are pinned: `requirements.txt` for direct packages and
`constraints.txt` for the full set. A bump is a deliberate one-line change
followed by the full suite and the release path above, never an unpinned
rebuild.

## Public-safety and privacy rules

- MAE remains inquiry-only unless a future write capability is separately
  designed, approved, authenticated, audited, tested, and documented.
- Treat live CAD data, addresses, narratives, medical details, caller data,
  recordings, and identifiers as sensitive.
- Use synthetic data in tests and demonstrations.
- Do not place raw CAD payloads, credentials, recordings, model files, or
  protected records in GitHub, Bitbucket, or shared handoffs.
- Separate verified facts, reported experience, recommendations, and legal
  questions in documents.
- Never invent a patient condition, incident event, unit action, or outcome.

## Station-alert rules

- Alert tones are authoritative.
- MAE speech may begin only after tones finish.
- Speech must never delay, interrupt, or block tones.
- Retain visual and silent fallbacks.
- Keep restricted and unverified narrative fields out of announcements.

## Avatar and video rules

- The static MAE portrait is the required fallback; the avatar stays optional.
- Run `MAE_Talk_Generic` only while MAE is speaking, then return to calm idle;
  test this locally on `.15` before adding a network speaking-state interface.
- Use the minimum authenticated state signal; send no raw CAD data to the
  renderer.
- Verify framing at 512 x 1536 and 60 Hz.
- Keep large media assets off ordinary Git; use the approved `.15` storage.

## Completion and handoff

An item is complete only when the behavior exists, safety and permission
boundaries hold, focused and full tests pass, docs are current, Git is clear,
and any approved deployment is verified healthy. The project is not complete
merely because every bullet has code: production acceptance, security review,
restore readiness, operator testing and fallbacks must also be satisfied.

At each meaningful stopping point write a dated handoff in `handoffs/`
(follow the newest): work done, tests, commit and branch, ECS revision, image
tag and digest, open risks, the exact next action, and whether services were
left running.

# AWS Guidance

- Prefer the AWS MCP Server for AWS interactions -- it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the AWS CLI
  directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over general
  knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use hyphens
  instead.

## AWS Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret, credential,
  API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST NOT
  hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with `asm-exec`
  so the secret resolves at runtime without entering context.
