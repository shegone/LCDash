# Offline Agent Architecture Research

Date: 2026-08-01

## Decision

Adopt **Open WebUI Computer** as the primary local project workspace while
retaining the existing Open WebUI portal, Open Terminal, Ollama, isolated Git
clone, and hosted Codex escalation path.

This is a material improvement over the original plan. Open Terminal remains a
useful quick shell, but Open WebUI Computer is much closer to the requested
Codex-style experience: one chat-oriented workspace with real project files,
an editor, persistent terminals, Git, resumable coding-agent sessions, mobile
access, project instructions, skills, scheduled work, and cross-device
continuation.

The recommended design uses the two RTX 3090 systems as independent workers.
It does not attempt to pool their VRAM over the LAN. Production `.227` remains
the service and general-development host; Windows PC `.15` remains the
Unreal/MetaHuman/video and Windows-automation host.

## Target operator experience

The existing Open WebUI remains the single friendly front door. It should offer
clearly named workspaces such as:

- `LCDash Development` - isolated writable clone on `.227`
- `MAE Avatar 01` - Windows, Unreal, MetaHuman, and video workspace on `.15`
- `LCDash Operations - Read Only` - optional later operational inspection

An operator can begin in the Open WebUI chat, open the project workspace, see
files and Git changes, use a terminal when needed, and resume the same coding
agent from another PC or phone. Routine work remains local. Hosted Codex stays
available in the same overall workflow for difficult architecture, security,
production, or recovery work.

## Recommended architecture

```text
                         Cloudflare Access or Tailscale
                                      |
                               Open WebUI on .227
                                  /          \
                 cptr/LCDash Development    cptr/MAE Avatar 01
                         |                         |
             Open WebUI Computer on .227  Open WebUI Computer on .15
                         |                         |
             isolated LCDash Git clone      real Windows project files
             OpenCode coding backend        OpenCode + supervised GUI tools
             Ollama local models            LM Studio or Ollama models
                         |                         |
             production remains separate    Unreal/MetaHuman remains local
```

### `.227` role

- Keep Open WebUI as the primary portal.
- Keep Open Terminal for fast one-off shell work.
- Add Open WebUI Computer with only the isolated development clone mounted
  writable during the pilot.
- Use OpenCode as the first native coding-agent backend.
- Keep Ollama as the initial model server.
- Never mount production secrets, backups, databases, Docker control, or the
  deployed checkout into the development workspace.
- Give production services priority over model inference.

### `.15` role

- Install Open WebUI Computer directly on Windows so it can work with real
  PowerShell, Git, project files, and Unreal/MetaHuman tooling.
- Add an authenticated local model server. LM Studio is the most user-friendly
  Windows choice; Ollama remains a good simpler alternative.
- Add DOM/accessibility-based browser automation first.
- Add native Windows/Unreal control only as an explicit, supervised capability
  with a visible on/off boundary.
- Unload or pause large models while Unreal rendering needs the RTX 3090.

## Product comparison

| Option | Best quality | Limitation for this project | Recommended role |
| --- | --- | --- | --- |
| Open WebUI Computer | Real folders, editor, Git, terminals, persistent chat, native coding-agent sessions, mobile access, skills, schedules, and an OpenAI-compatible workspace gateway | Newer source-available product; one trust domain rather than strong multi-user isolation | Primary workspace |
| Existing Open Terminal | Simple and already integrated with Open WebUI | Terminal-oriented; less durable project/session experience | Keep for quick tasks |
| LibreChat | Polished multi-user ChatGPT-style UI, agents, MCP, code execution | Adds another portal and does not match Computer's unified real-workspace workflow | Best fallback frontend |
| AnythingLLM | Friendly no-code agents, RAG, MCP, scheduled tasks | Better knowledge-chat product than full coding workstation | Optional knowledge portal only |
| OpenHands | Strong sandboxed software-agent environment | Heavier separate UI and persistence model | Benchmark/sandbox fallback |
| Goose | Capable desktop/CLI agent with broad MCP support and Ollama providers | Not a native Computer coding backend | Secondary workflow/MCP candidate |
| New Open Interpreter | Broad local execution, sandboxing, skills, MCP, ACP, and multiple harnesses | Newer and broader than needed for the first pilot | Benchmark after OpenCode |
| Cline | Strong supervised coding inside VS Code | IDE-centered rather than the requested single chat workspace | Useful on `.15` for visual work |
| Aider | Stable, efficient terminal pair programmer | Narrower autonomous workflow | Focused fallback |

## Coding-agent decision

Start with **OpenCode** because Open WebUI Computer supports it as a native
backend with resumable sessions, it works with local models, and it provides
fine-grained tool permissions. Benchmark it against the new Rust Open
Interpreter, Goose, Qwen Code, and OpenHands on the same acceptance suite.

Do not require one harness to win every category. A role-based result is more
useful:

- OpenCode for primary repository work
- Open Interpreter or Goose for broad machine workflows if they test better
- Cline for human-supervised VS Code and Unreal-adjacent changes
- Aider for focused patches when a simpler tool is safer
- Hosted Codex for escalation and difficult cross-system work

## Local model plan

The installed `qwen3.5:27b` remains a strong general, vision, reasoning, coding,
and tool-use baseline. Test it first rather than replacing it based on model
rankings alone.

Benchmark these additional candidates:

| Model | Likely role | Reason to test |
| --- | --- | --- |
| Qwen3.5 27B | General agent and vision | Already installed; broad current capability |
| Qwen3-Coder 30B-A3B | Primary coding candidate | Agentic coding and tool-use focus with low active parameter count |
| Devstral Small 2 24B | Coding and visual workstation candidate | Agentic coding, vision, and a practical single-GPU target |
| gpt-oss-20b | Fast reasoning/review candidate | Low active parameter count, tool use, adjustable reasoning |
| GLM-4.7-Flash | Coding-agent candidate | Current Ollama coding-agent recommendation within a 24 GB-class budget |

Use at least a practical 32K context during the initial pilot and test 64K where
GPU memory and latency remain acceptable. Ollama recommends 64K for coding
agents, but production reliability and measured task completion take precedence
over a nominal context number.

Do not distribute one model across `.227` and `.15` over the normal LAN. Route
complete jobs to one machine or the other. Open WebUI can connect to both model
servers and route by model/workspace name.

## Browser and Windows control

Use the most structured interface available:

1. API or purpose-built project tool
2. Browser DOM/accessibility snapshot
3. Browser screenshot/vision fallback
4. Native desktop accessibility
5. Pixel/vision-only desktop control as a last resort

Recommended browser tools are Playwright MCP and `agent-browser`. Both expose
structured page state; `agent-browser` also provides action policies,
confirmation categories, stored profiles, screenshots, and an observability
view.

For `.15`, pilot `open-codex-computer-use` only for supervised Windows and
Unreal work. The CUA/trycua ecosystem is a stronger option when a disposable
virtual Windows environment is appropriate. No general desktop-control tool may
release alerts, affect live CAD, trigger station tones, or become part of an
emergency workflow.

## Durable memory and Codex catch-up

Use transparent, auditable memory before adding a memory database:

- root `AGENTS.md` for hard project and safety rules
- Git-tracked current-state and dated handoff documents
- `.cptr` workspace chats and project state for local continuation
- `LATEST_PC15.md` and `LATEST_PC227.md` for cross-machine exchange
- an automatic end-of-task handoff containing changes, validation, risks, Git
  commit, and next action
- Open WebUI per-user memory only for benign preferences

Open WebUI Computer automatically recognizes common project instruction and
memory files and discovers project-local agent skills. This lets local tools and
hosted Codex share a readable context format.

Mem0 is a good later option for conversational preference recall. Graphiti is a
powerful temporal knowledge graph but is unnecessary operational complexity for
the first deployment. Neither should store raw CAD narratives or secrets.

## Secrets and broad access

The user should not need to repeatedly type credentials, but the model should
not memorize or print them. Give the agent a narrow credential-broker tool that
retrieves and injects an approved secret directly into a process without
returning the value to chat, memory, logs, or model context.

For the initial pilot, keep the current protected server secret directory and
Windows protected credential storage. Consider OpenBao later if identity-based
policies, dynamic credentials, leases, and centralized audit justify the added
administration.

Never place credentials in:

- Open WebUI memory
- Computer skills or workspace instructions
- `AGENTS.md`
- Git
- shared handoff files
- chat transcripts

## Observability and evaluation

Expand the existing ten-task benchmark into a twenty-task suite before granting
broader access. It should measure:

- repository comprehension and scoped edits
- tests, linting, dirty-file handling, and rollback awareness
- correct tool choice and recovery from failed commands
- browser work using DOM/accessibility state
- `.15` Windows and Unreal observation in supervised mode
- cross-machine handoff accuracy
- secret and raw-CAD refusal behavior
- production-boundary compliance
- latency, GPU memory, context length, and completion rate
- correct escalation to hosted Codex

Use deterministic local checks first. Add a self-hosted Arize Phoenix instance
later for sanitized traces, datasets, experiments, and regression evaluation.
Do not send raw CAD content, credentials, or unrestricted terminal output to the
trace store.

## Security boundaries

- Treat Open WebUI Computer like SSH access to the host.
- Keep it behind Cloudflare Access or Tailscale; never expose a raw service port
  to the internet.
- Computer is one trust domain and is not a substitute for per-user OS
  isolation.
- Start `.227` with only the isolated development clone mounted.
- Gateway calls, bots, and scheduled jobs can run unattended/full approval;
  enable them initially only for read-only status and documentation tasks.
- Production deployment remains an explicit, logged workflow with validation
  and rollback.
- Local AI remains advisory and cannot block or control CAD, dispatch, ESInet,
  radio, alert release, emergency routing, or station tones.

## Phased implementation

### Phase A - `.227` pilot

1. Install Open WebUI Computer alongside the existing stack.
2. Bind it to loopback/private Docker networking and keep external access behind
   the existing protected portal.
3. Mount only `/srv/lcdash-data/agent-workspaces/LCDash` as the writable project.
4. Connect the existing Ollama service and `qwen3.5:27b`.
5. Enable OpenCode as the first native coding backend.
6. Register the Computer workspace gateway in Open WebUI.
7. Run the acceptance benchmark in a disposable branch.

### Phase B - `.15` Windows workspace

1. Install Open WebUI Computer directly on Windows.
2. Install authenticated LM Studio headless service or Ollama.
3. Add the `mae-avatar-01` project and handoff folders.
4. Validate PowerShell, Git, files, and browser automation.
5. Pilot Windows/Unreal control under direct supervision with a visible off
   switch.
6. Register `.15` as a separately named Computer workspace in `.227` Open WebUI.

### Phase C - measured model and harness selection

1. Run the same twenty tasks across the shortlisted models and harnesses.
2. Record correctness, interventions, speed, context failures, and VRAM use.
3. Select the best combination per role.
4. Keep a hosted-model option for tasks that fail local acceptance criteria.

### Phase D - controlled expansion

1. Automate durable end-of-task handoffs.
2. Add the narrow credential broker.
3. Add sanitized Phoenix tracing and regression evaluation.
4. Add read-only operations inspection.
5. Add scheduled status work or messaging notifications only after unattended
   execution has its own acceptance test.

## Acceptance gates

Phase A is successful when the user can open one friendly portal, choose the
LCDash workspace, ask for a bounded change, watch the local agent inspect and
edit the real clone, review its Git diff and tests, leave, and later resume from
another device without losing project context.

Phase B is successful when the same experience works for `.15`, including a
correct durable handoff, without disrupting Unreal rendering or exposing broad
Windows control when it is not explicitly enabled.

No phase grants automatic authority over production deployments, credentials,
CAD data, station alerting, emergency operations, backups, network policy, or
external communications.

## Primary sources

- Open WebUI Computer overview:
  https://docs.openwebui.com/ecosystem/computer/
- Computer workspaces:
  https://docs.openwebui.com/ecosystem/computer/workspace/
- Computer skills and memory:
  https://docs.openwebui.com/ecosystem/computer/ai/skills-and-memory/
- Computer coding-agent backends:
  https://docs.openwebui.com/ecosystem/computer/ai/coding-agents/
- Computer approvals and plan mode:
  https://docs.openwebui.com/ecosystem/computer/ai/approvals-and-plan-mode/
- Computer subagents:
  https://docs.openwebui.com/ecosystem/computer/ai/subagents/
- Computer Open WebUI gateway:
  https://docs.openwebui.com/ecosystem/computer/automate/open-webui/
- Computer FAQ and trust model:
  https://docs.openwebui.com/ecosystem/computer/faq/
- OpenCode agents and web application:
  https://opencode.ai/docs/agents
  https://dev.opencode.ai/docs/web/
- New Open Interpreter:
  https://github.com/openinterpreter/openinterpreter
- Goose:
  https://github.com/aaif-goose/goose
- OpenHands local models:
  https://docs.openhands.dev/openhands/usage/llms/local-llms
- Qwen Code:
  https://qwenlm.github.io/qwen-code-docs/
- Qwen3.5 27B:
  https://huggingface.co/Qwen/Qwen3.5-27B
- Qwen3-Coder 30B-A3B:
  https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
- Devstral Small 2 24B:
  https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512
- gpt-oss-20b:
  https://developers.openai.com/api/docs/models/gpt-oss-20b
- Ollama coding-agent launch guidance:
  https://ollama.com/blog/launch
- LM Studio headless mode and LM Link:
  https://lmstudio.ai/docs/developer/core/headless
  https://lmstudio.ai/docs/lmlink/basics/faq
- Playwright MCP:
  https://github.com/microsoft/playwright-mcp
- agent-browser:
  https://github.com/vercel-labs/agent-browser
- open-codex-computer-use:
  https://github.com/iFurySt/open-codex-computer-use
- CUA:
  https://github.com/trycua/cua
- Mem0 open source:
  https://docs.mem0.ai/open-source/overview
- Graphiti:
  https://help.getzep.com/graphiti/getting-started/welcome
- OpenBao:
  https://openbao.org/docs/what-is-openbao/
- Arize Phoenix:
  https://arize.com/docs/phoenix

