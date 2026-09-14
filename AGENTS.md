# LCDash Local Agent Charter

These instructions apply to every AI coding agent working in this repository,
including OpenHands and other offline assistants.

## Platform state (decided 2026-09-14)

**The AWS pilot is the only LCDash.** It runs in account `862772137583`,
`us-east-1`, at `https://aws.logan911.com`, from branch `main` of this
repository. The on-premises LCDash platform that used to run on `.227` is
retired: its Docker Compose stack has been stopped since 2026-09-09, its
Cloudflare tunnel is down on purpose, and its n8n monitoring workflows are
deactivated. Nothing in this repository deploys to `.227` any more.

Documents that describe the on-premises platform are kept as history and are
marked retired at the top. Do not "fix", patch, restart, or monitor anything
LCDash-related on `.227`.

## Mission

Continue the Logan County LCDash program safely and sequentially until the
approved roadmap is complete. Build a polished, futuristic, dark, Logan
County-branded operations platform while protecting emergency operations,
sensitive CAD data, credentials, and production reliability.

The agent is a development assistant, not an emergency dispatcher. It must not
make operational decisions or modify CAD, radio, call-routing, station-alert,
or public-warning records.

## Sources of truth

Read these before planning work:

1. `docs/PROJECT_ROADMAP.md`
2. `docs/PROJECT_CATCHUP_2026-09-07.md` and the newest file in `handoffs/`
3. `AWS_WORKSPACE.md` and `infrastructure/README.md`
4. The documentation for the module being changed
5. The relevant task skill in `agent-skills/`
6. The latest Git history and current working-tree status
7. Live read-only AWS state (ECS service, task definition, ALB target health,
   the web log group) when the question is what is running

Do not rely on an old chat summary when current code, tests, Git, or live
read-only status contradicts it. `docs/CURRENT_PRODUCTION_STATE_2026-07-31.md`
and `docs/SERVER_DEPLOYMENT.md` describe the retired on-premises platform.

## Machine roles

### AWS account `862772137583` - the LCDash platform

- One Fargate web task behind one public HTTPS ALB with Cognito login, one
  PostgreSQL RDS instance, Bedrock, Transcribe, Polly, and Amazon Location,
  all defined in `infrastructure/` with the AWS CDK.
- Releases follow the guarded path in "Production deployment rules" below.
- Identity Center profiles: `lcdash-phase1-deployment` runs releases and
  read-only status checks; `lcdash-sandbox-admin` is for IAM changes only.

### PC `.227` - the Hermes link's LLM host (not LCDash)

- Address: `14.1.1.227`; hostname: `lcdash-server` (historical name).
- Runs the Hermes containers (Ollama brain, ComfyUI media) and n8n for
  Hermes. That is its only role.
- The stopped LCDash containers, images, and volumes on it are retained only
  until the owner retires them deliberately; they hold CAD-derived data and
  must not be started, deleted, or copied without explicit approval.
- Must not run Unreal Engine, MetaHuman rendering, video generation, or other
  sustained avatar-rendering workloads.

### PC `.15` - avatar and video workstation

- Address: `14.1.1.15`; Windows workstation with an RTX 3090 24 GB GPU.
- Owns Unreal Engine, MetaHuman/Hadley, facial animation, lip synchronization,
  video generation, Pixel Streaming, and the 512 x 1536 portrait LED display.
- Preserve Windows and use NVIDIA Studio drivers.
- Keep the static MAE portrait as the required fallback.
- The agent running on `.15` must maintain `LATEST_PC15.md` in the shared
  `MAE Progress Handoffs` folder.
- The `.15` GPU may run local video or coding models when that does not contend
  with an active Unreal, rendering, or Pixel Streaming workload. Do not attempt
  distributed multi-GPU inference between `.227` and `.15` until a separate,
  tested design is explicitly approved.

### Primary Windows development workstation

- Repository path: `E:\Projects\LCDash-AWS`, branch `main`. This is the only
  working copy for LCDash source changes, tests, commits, and releases.
- `E:\Projects\LCDash` is the same repository checked out on the retired
  `deployment/ubuntu-nvidia-227` branch. Leave it alone; do not commit or
  deploy from it.
- Every commit is pushed to both remotes: GitHub `shegone/LCDash` and the
  NGA911 Bitbucket mirror `nga911rnd/dashboard`. `origin` carries both push
  URLs, so one `git push origin main` does it.

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

For each roadmap item:

1. Inspect the current code, documentation, tests, Git state, and relevant
   read-only system status.
2. State the intended outcome and a short sequential plan.
3. Work on one bounded feature or correction at a time.
4. Preserve unrelated user changes and never discard a dirty working tree.
5. Add or update tests for changed behavior.
6. Run focused tests, then the full relevant suite.
7. Review the diff for secrets, sensitive CAD content, unsafe writes, and
   accidental scope expansion.
8. Update the relevant documentation.
9. Commit only a coherent, tested change with a plain commit message.
10. Deploy only when the approval and production rules below are satisfied.
11. Verify the deployed behavior without printing unnecessary operational
    details.
12. Update the durable handoff before stopping.

If a task fails repeatedly, stop changing things blindly. Record the exact
failure, preserve the last known-good state, and request Codex review.

## Mandatory local-agent execution protocol

Apply this protocol before using tools or changing files. It is especially
important for local models with limited context or unreliable long-horizon
tool use.

### 1. Classify and bound the request

- First classify the request as `READ`, `PLAN`, `CHANGE`, `TEST`, `DEPLOY`, or
  `OPERATE`.
- Convert a broad request into ordered work packages. Each package must have
  one outcome, an explicit file or subsystem scope, an acceptance check, and a
  stopping point.
- Do not execute an entire project roadmap in one response. Produce the
  backlog, identify the critical path, and begin only the first safe package.
- Never mix unrelated documentation, code, infrastructure, Hermes-host
  (`.227`), and `.15` changes in one branch or work package.

### 2. Establish a checkpoint before work

Before taking action, report and retain:

- current branch and HEAD;
- clean or dirty working-tree state;
- pre-existing changed files that must be preserved;
- task classification and allowed actions;
- exact files or subsystem in scope;
- prohibited actions; and
- acceptance evidence required to finish.

If the tree is dirty, do not overwrite, revert, discard, stage, or combine the
existing changes unless the task explicitly owns them.

### 3. Use progressive discovery

- Read `AGENTS.md`, then the smallest authoritative source set needed for the
  current work package.
- Search filenames before searching file contents.
- Start with at most five targeted file reads or searches. After that, pause
  and summarize what was learned, what remains unknown, and whether more
  discovery is justified.
- Do not repeatedly search synonyms after the relevant source files are known.
- For a master roadmap, synthesize in sections. Do not attempt to read every
  repository file before producing useful output.
- Treat old notes, local remote-tracking refs, and roadmap status as possibly
  stale. Use `VERIFY` when current evidence is unavailable.

### 4. Manage context deliberately

- Keep a short working ledger in the response: `Known`, `Assumptions`,
  `Unknowns`, `Current package`, and `Next checkpoint`.
- When the task is large, complete one phase and report before continuing.
- If output or context is becoming long, stop tool use and produce a concise
  checkpoint rather than starting another discovery loop.
- Never restart the full investigation after a continuation. Reuse the
  evidence already gathered and continue from the recorded checkpoint.

### 5. Verify every material claim

- Distinguish observed facts, repository documentation, inference, and
  recommendations.
- Do not call a benchmark `PASS` until every requested acceptance check is
  shown.
- Re-read the final changed text and inspect the final diff before reporting.
- Verify that only intended files changed.
- Quote exact branch, file, and test names from tool output. Do not silently
  correct or abbreviate them in the report.
- A local remote-tracking branch is not proof of current GitHub state unless a
  safe fetch was explicitly allowed and completed.
- Do not infer what is running from the development clone. Read the ECS
  service, its task definition, and the ECR tag.

### 6. Recover from errors conservatively

- After the first failed edit or command, inspect the actual result before
  retrying.
- After a second failure of the same kind, stop that approach, preserve the
  current state, and report the blocker.
- Do not make repeated blind edits to repair formatting or syntax.
- Never weaken tests, safety checks, authentication, audit, privacy filtering,
  or fallbacks merely to obtain a passing result.

### 7. Use a fixed completion report

Every completed work package must report:

1. outcome and `PASS`, `FAIL`, or `BLOCKED`;
2. branch, HEAD, and working-tree state;
3. files changed and a concise diff summary;
4. commands or tests run and their exact results;
5. safety, privacy, and boundary checks;
6. assumptions or unverified facts;
7. whether anything was committed, pushed, deployed, installed, or operated;
8. exact next work package; and
9. whether hosted Codex or user action is required.

If the response stops before this report, the work package is not complete.

### 8. Escalate at the correct boundary

Escalate to hosted Codex before continuing when:

- the same failure occurs twice;
- the task touches production deployment, rollback, credentials, networking,
  authentication, authorization, backup policy, or public-safety outputs;
- documentation and code materially contradict each other;
- the model cannot prove that its proposed change preserves existing safety
  behavior;
- the requested scope cannot fit into one bounded, reviewable work package; or
- the model is unsure whether an action is read-only or state-changing.

Escalation is a successful safety outcome, not a failed task.

## Actions the local agent may perform autonomously

- Read repository files and non-secret documentation.
- Inspect Git status and history.
- Read live AWS state through the read-only calls the deployment profile
  permits.
- Create a feature branch or work in an explicitly assigned safe branch.
- Edit source, tests, and documentation within an isolated working copy.
- Run local unit tests, linters, formatters, and read-only diagnostics.
- Produce drafts, plans, diagrams, and handoff records.
- Commit a coherent change after required tests pass, if the operator has
  enabled local commits.

## Actions requiring explicit human approval

- Executing a CloudFormation change set, or any other change to a running
  AWS service.
- Pushing to GitHub `main` (which also mirrors to Bitbucket).
- Changing IAM policies, permission sets, or the permissions boundary;
  changing Cognito users, groups, or the app client.
- Changing Cloudflare, DNS, certificate, or security settings anywhere.
- Installing or removing software on `.227` or `.15`, or starting, stopping,
  or removing any container there.
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
   documented util-linux baseline; anything else stops the release.
5. Create a **named** change set on the foundation stack with only
   `PilotImageDigest` changing, review that its scope is the standard five
   resources (two task definitions, the service, the collector's rule and
   policy), then execute it.
6. Verify the ECS rollout completed, the ALB target is healthy, and a
   signed-in page shows the caller's name and role.
7. Rollback is a change set with the previous digest. A replaced task
   definition revision is deregistered by CloudFormation and cannot be
   re-pointed to directly.

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

- Complete and test calm idle/talking switching locally on `.15` before adding
  a network speaking-state interface.
- Run `MAE_Talk_Generic` only while MAE is speaking, then return to calm idle.
- Use the minimum authenticated state signal needed; do not send raw CAD data
  to the renderer.
- Verify the portrait framing at 512 x 1536 and 60 Hz.
- Keep video/rendering artifacts off the ordinary Git repository unless they
  are small, approved source assets. Store large Unreal and media assets in the
  approved `.15` project storage and backup path.

## Local model strategy

- Use `qwen3-coder:30b` for bounded repository exploration, coding, tests,
  code review, and implementation work. Use `qwen3.6:27b` for planning,
  general operations, vision, desktop-control, and cross-domain work.
- Start no more than four concurrent local sub-agents. Keep background work at
  two workers or fewer. Each worker needs a separate, bounded outcome; never
  give parallel workers overlapping file-edit scopes.
- Select and read the matching `agent-skills/` instruction before taking a
  task-specific action. The root charter always overrides a task skill.
- Require at least a 22K context window for OpenHands; 32K is the normal target.
- Do not assume a model is reliable because it produces fluent text. Judge it
  by tool use, diffs, tests, and repeatable task benchmarks.
- Escalate to hosted Codex when the local agent fails the same task twice,
  cannot keep tests passing, proposes unsafe production changes, loses project
  boundaries, or faces a security/architecture decision.

## Completion definition

A roadmap item is complete only when:

- the requested behavior exists;
- safety and permission boundaries are preserved;
- focused and relevant full tests pass;
- documentation is current;
- Git state is clear;
- any approved deployment is healthy and verified; and
- the handoff records what changed, what was tested, what remains, and the exact
  next action.

The whole project is not "complete" merely because every current roadmap bullet
has code. Production acceptance, security review, restore readiness, operator
testing, fallback behavior, and unresolved external dependencies must also be
documented and satisfied.

## Durable handoff

At every meaningful stopping point, write a dated handoff in `handoffs/` (see
the newest one there for the shape) and, for `.15` work, update the shared
`LATEST_PC15.md` as described in `docs/OFFLINE_AGENT_OPERATIONS.md`. Include:

- completed work;
- files, settings, or systems changed;
- tests and results;
- commit and branch;
- deployment and service state (ECS revision, image tag, digest);
- open risks or blockers;
- exact next action; and
- whether applications and services were left running.

Write a short `Codex catch-up` section so hosted Codex can resume quickly.

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
