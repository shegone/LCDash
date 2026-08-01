# LCDash Local Agent Charter

These instructions apply to every AI coding agent working in this repository,
including OpenHands and other offline assistants.

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
2. `docs/CURRENT_PRODUCTION_STATE_2026-07-31.md`
3. `docs/SERVER_DEPLOYMENT.md`
4. The documentation for the module being changed
5. The latest Git history and current working-tree status
6. The cross-PC files described in `docs/OFFLINE_AGENT_OPERATIONS.md`

Do not rely on an old chat summary when current code, tests, Git, or live
read-only status contradicts it.

## Machine roles

### PC `.227` - production application and AI server

- Address: `14.1.1.227`; hostname: `lcdash-server`.
- Owns production LCDash, PostgreSQL, CentralSquare integration, MAE, Ollama,
  Open WebUI, speech services, analytics, knowledge services, and backups.
- Remains the control and data node.
- Must not run Unreal Engine, MetaHuman rendering, video generation, or other
  sustained avatar-rendering workloads.
- Local coding-model inference may run here only within established resource
  limits and must yield to production MAE, speech, CAD, database, backup, and
  alert workloads.

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

- Repository path: `E:\Projects\LCDash`.
- Use this working copy for normal source changes, tests, commits, and the
  existing deployment workflow.

## Hard architecture boundaries

- Keep the on-premises LCDash/local MAE platform separate from the AWS GovCloud
  NGA911 upgrade path unless an interface or migration task is explicitly
  authorized.
- Cloud intelligence must never become a dependency of call routing, CAD,
  ESInet, radio, station alerting, or other emergency operations.
- Keep MAE, Mindshare Technical Assistant, Mindshare Radio Intelligence, NOVA,
  Station Alerts, and CentralSquare Operations separately permissioned.
- Keep the animated avatar optional. Text, audio, operational data, and safety
  controls must remain usable when `.15` or the renderer is unavailable.

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
- Never mix unrelated documentation, code, infrastructure, `.227`, and `.15`
  changes in one branch or work package.

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
- Do not infer production state from the development clone.

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
- Create a feature branch or work in an explicitly assigned safe branch.
- Edit source, tests, and documentation within an isolated working copy.
- Run local unit tests, linters, formatters, and read-only diagnostics.
- Produce drafts, plans, diagrams, and handoff records.
- Commit a coherent change after required tests pass, if the operator has
  enabled local commits.

## Actions requiring explicit human approval

- Deploying to `.227` or changing a running service.
- Pushing to GitHub `main` or the production deployment branch.
- Changing firewall, SSH, Cloudflare, DNS, DHCP, network, operating-system,
  driver, firmware, Docker daemon, or security settings.
- Installing or removing software on `.227` or `.15`.
- Creating, rotating, reading, copying, or exposing credentials, tokens,
  passwords, keys, OAuth records, or the protected credential record.
- Enabling live EMS delay delivery, station announcements, paging, CAD writes,
  acknowledgments, or any other operational output.
- Modifying CentralSquare subscriptions or webhook security.
- Changing backup scope, retention, encryption, or restore procedures.
- Connecting `.227` and `.15` through a new network service.
- Uploading public-safety data or source documents to an external service.
- Deleting data, histories, backups, releases, branches, or working copies.

Use confirmation mode. Never run an offline agent in unrestricted
always-approve mode against production systems.

## Production deployment rules

- The Windows working tree must be clean.
- Local and GitHub commits must match the intended release.
- Required tests must pass before deployment.
- Use `scripts/deploy_server.ps1`; do not improvise a second deployment path.
- Preserve automatic rollback behavior.
- Verify service health afterward.
- Do not display secrets or raw CAD payloads in logs or handoffs.
- Documentation-only commits normally do not require a production restart.

## Public-safety and privacy rules

- MAE remains inquiry-only unless a future write capability is separately
  designed, approved, authenticated, audited, tested, and documented.
- Treat live CAD data, addresses, narratives, medical details, caller data,
  recordings, and identifiers as sensitive.
- Use synthetic data in tests and demonstrations.
- Do not place raw CAD payloads, credentials, recordings, model files, or
  protected records in GitHub or shared handoffs.
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

- Prefer a coding-focused local model such as Qwen3-Coder 30B for routine code
  work and a second reasoning/tool-use model such as gpt-oss 20B for review.
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

The whole project is not “complete” merely because every current roadmap bullet
has code. Production acceptance, security review, restore readiness, operator
testing, fallback behavior, and unresolved external dependencies must also be
documented and satisfied.

## Durable handoff

At every meaningful stopping point, update the appropriate latest-status file
and create a dated snapshot as described in
`docs/OFFLINE_AGENT_OPERATIONS.md`. Include:

- completed work;
- files, settings, or systems changed;
- tests and results;
- commit and branch;
- deployment and service state;
- open risks or blockers;
- exact next action; and
- whether applications and services were left running.

Write a short `Codex catch-up` section so hosted Codex can resume quickly.
