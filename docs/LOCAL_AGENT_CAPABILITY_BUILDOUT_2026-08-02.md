# Local Agent Capability Buildout - 2026-08-02

## Completed - PASS

- Added the `agent-skills/` library and made the root `AGENTS.md` require the
  relevant skill before task-specific work.
- Defined model routing: `qwen3-coder:30b` for bounded code/test/review work;
  `qwen3.6:27b` for planning, general operations, vision, desktop control, and
  cross-domain work.
- Enabled Open WebUI design-phase local-agent capacity on PC `.227`:
  four foreground sub-agents, two background workers, twelve iterations per
  worker, and bounded worker output.
- Enabled hybrid retrieval, enriched document metadata indexing, and the
  opt-in knowledge-base command tool.
- Installed Visual Studio Code 1.130.0 and Continue 2.0.0 on PC `.15`.

## Deployment and verification

- Deployment commit: `c72177f` on `deployment/ubuntu-nvidia-227`.
- The approved deployment script rebuilt and restarted the stack.
- LCDash web, PostgreSQL, speech, Ollama, Open Terminal, Open WebUI, and Open
  WebUI Computer all returned healthy/running status after deployment.
- `qwen3-coder:30b` returned `AGENT_STACK_READY` after the restart.
- Qwen 3.6 27B, Qwen3-Coder 30B, and the Qwen 3.5 rollback models remain
  installed on `.227`.

## PC15 Continue status

Continue is installed but intentionally not yet pointed at the PC15 Computer
gateway. The local gateway requires its private credential; this buildout did
not read, copy, or place that credential in a new application configuration.
The next package is to add Continue through an approved protected connection
method and confirm `qwen3-coder:30b` can perform a harmless workspace read.

## Boundaries retained

- No raw CAD data, passwords, keys, or secret-file contents were recorded.
- No new public port was opened.
- No CAD, dispatch, station-alert, radio, or public-warning behavior changed.
- The on-prem LCDash/MAE path remains separate from the future GovCloud path.

## Codex catch-up

The design-phase local agent stack is now more capable and has reusable skill
instructions. Continue is installed on PC15 but needs a credential-safe
connection to its already-protected Computer gateway before it can use the
shared coding model.
