# Current PC15 Agent State

Date: 2026-08-01

## Outcome

The Windows AI/video workstation at `14.1.1.15` is connected to the protected
Open WebUI portal on `.227` as the separate model/workspace
`mae15.cptr/MAE_Avatar_Baseline 5.8`.

The PC15 Computer workspace is:

`C:\Users\admin\UnrealProjects\MAE_Avatar_Baseline 5.8`

The gateway is reachable from `.227` at `http://14.1.1.15:8000/v1` and requires
its private gateway key. The credential is not stored in Git or this handoff.

## Installed and verified

- Open WebUI Computer `0.9.20`
- Ollama models hosted by `.227`, including `qwen3.5:27b`
- Default PC15 chat model: `lcdash/qwen3.5:27b`
- OpenCode `1.18.11`
- Open Computer Use `0.3.1`
- Python 3.12, Git, Node.js LTS, and Playwright MCP
- Windows OpenSSH for controlled administration from the project workstation
- iClone 8.74, Character Creator 4.72, and Unreal Engine 5.8

## Windows control

Computer Tool Server ID: `windows_control`

Command:

`C:\MAE-Agent\cptr-venv\Scripts\python.exe`

Argument:

`C:\MAE-Agent\scripts\pc15_windows_control_proxy.py`

The wrapper removes encoded screenshot blocks that previously overwhelmed the
local model while retaining all nine Windows-control tools and bounded
accessibility data. The canonical source is
`scripts/pc15_windows_control_proxy.py` in LCDash.

## Acceptance results

- Computer discovered all nine Windows-control tools.
- Direct PC15 chat correctly inspected iClone without taking an action.
- `.227` Open WebUI discovered the PC15 workspace.
- The gateway chat listed the Unreal project's top-level contents.
- The end-to-end `.227` chat called `windows_control`, identified
  `iClone 8 - DefProject.iProject`, and returned visible controls without
  clicking, typing, saving, or modifying anything.

## Important boundaries

- Select `mae15.cptr/MAE_Avatar_Baseline 5.8` for PC15 work.
- Select `cptr/LCDash` only for the isolated Linux LCDash development clone.
- Keep the on-prem LCDash/MAE work separate from the future AWS GovCloud
  `NGA911 MAE Upgrade` path.
- Never place gateway keys, passwords, CAD credentials, or raw CAD payloads in
  Git, handoffs, model memory, or chat transcripts.
- Public-safety AI remains advisory and must never block emergency operations
  or authoritative station-alert tones.

## Next work

1. Complete a cold-start PC15 autostart test after the open creative projects
   have been saved; the non-disruptive scheduled-task test already passes.
2. Establish a reversible Unreal/iClone working-copy and backup routine before
   allowing autonomous project edits.
3. Completed: install iClone Live Link 1.38 Trial and validate Auto Setup
   All-in-One 2.03 for UE 5.8 against a verified pre-install backup. See
   `PC15_AVATAR_INVENTORY_2026-08-01.md`.
4. Completed: native iClone Python created the isolated test project
   `C:\MAE-Agent\tests\MAE_iClone_FBX_Test.iProject`. It loaded the neutral
   female avatar and idle motion and verified the saved 77,258,714-byte file.
   The helper is `scripts/iclone_create_disposable_test.py`; it does not export
   FBX or change Unreal.
5. Next: export that disposable project to FBX and import it through Auto Setup
   before considering the paid Live Link license.
6. Begin supervised avatar/video tasks, then expand autonomy only after each
   action class has a repeatable validation and rollback path.
