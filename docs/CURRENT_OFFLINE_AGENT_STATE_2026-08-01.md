# Current Offline Agent State

Date: 2026-08-01

## Phase A outcome

The first Open WebUI Computer pilot is deployed on `.227` and available through
the existing protected Open WebUI portal as the `cptr/LCDash` model/workspace.
It provides a persistent chat agent backed by the real isolated development
clone.

This deployment did not grant access to the production checkout, production
secrets, databases, backups, Docker control, raw CAD payloads, or `.15`.

## Live components

- Container: `lcdash-open-webui-computer`
- Computer version: `0.9.20`
- Host binding: `127.0.0.1:8020`
- Private service address used by Open WebUI:
  `http://open-webui-computer:8000/v1`
- Persistent application data:
  `lcdash-platform_lcdash_open_webui_computer_data`
- Only writable project mount:
  `/srv/lcdash-data/agent-workspaces/LCDash:/workspace/LCDash`
- Default workspace model: `qwen3.5:27b`
- OpenCode CLI: `1.18.11`, pinned by release checksum in the derived Computer
  image

The Computer base image is pinned by digest. OpenCode is also pinned by version
and SHA-256 checksum. Neither container has the Docker socket or production
secret directory mounted.

## User access

Open the existing protected Open WebUI site and choose `cptr/LCDash` from the
model selector. This routes the conversation into the isolated Computer
workspace while preserving Open WebUI conversation lineage through the
configured custom headers.

Direct Computer port `8020` is loopback-only and is not published to the LAN or
Internet.

## Initialization and credentials

`deploy/configure-open-webui-computer.py` performs repeatable initialization:

- signs in or creates the local Computer administrator;
- connects Computer to Ollama over the private Docker network;
- registers `/workspace/LCDash`;
- creates or reuses the protected Computer gateway credential;
- adds the Computer gateway to Open WebUI;
- confirms both Computer and Open WebUI can discover the workspace;
- optionally performs a harmless `AGENTS.md` read-only agent test.

The gateway key is stored only at
`/srv/lcdash-platform/secrets/open_webui_computer_gateway_key` with mode `600`.
It is not mounted into the development workspace or committed to Git.

During initial validation, a shell-quoting error allowed the first newly created
gateway token to appear in diagnostic output. That token was immediately
revoked before the gateway was connected to Open WebUI. A replacement was
created and stored without printing it. The revoked token must never be reused.

## Validation completed

- Docker Compose configuration passed server-side validation.
- LCDash web health returned HTTP 200 after each deployment.
- Existing Open WebUI health returned HTTP 200.
- Computer root returned HTTP 200 through loopback.
- Computer mounts were inspected and contained only `/data` and the isolated
  LCDash development clone.
- The Computer gateway discovered `cptr/LCDash`.
- Existing Open WebUI discovered `cptr/LCDash`.
- The local Computer agent used `qwen3.5:27b`, read the first line of
  `AGENTS.md`, returned the expected project heading, and did not modify Git
  state.
- OpenCode `1.18.11` ran directly inside the Computer workspace, used local
  `qwen3.5:27b`, returned the expected project heading, and did not modify Git
  state.
- The development clone was synchronized with GitHub after deployment.

## OpenCode native-adapter finding

Computer detects OpenCode and can discover its Ollama models. Direct OpenCode
CLI operation passes. Computer 0.9.20's native OpenCode session adapter,
however, fails while decoding the OpenCode event stream before inference. The
same failure occurred with current OpenCode `1.18.11` and the contemporary
OpenCode `1.1.51`, which isolates the problem to the Computer adapter rather
than the installed OpenCode release.

The native OpenCode profile is therefore disabled so users do not select a
known-broken backend. OpenCode remains installed and usable from the Computer
terminal. Computer's built-in agent remains the tested portal default. Re-test
the native adapter after a newer stable Computer release is available.

## Current boundaries

- Computer is treated like SSH access and remains behind the protected Open
  WebUI path.
- Gateway requests run unattended/full approval, so the gateway is restricted
  to the isolated development clone.
- Production deployments remain explicit and rollback-capable.
- Credentials remain outside chat, model memory, Git, and handoffs.
- Local AI remains advisory and cannot control or block CAD, dispatch, ESInet,
  radio, emergency routing, alert release, station tones, or other emergency
  operations.
- `.15` remains the Windows/Unreal/MetaHuman/iClone/video workstation. The user
  is installing the licensed Reallusion iClone package; `.15` integration will
  be addressed in a later phase.

## Next work

1. Run the expanded acceptance suite against `cptr/LCDash` in disposable
   branches.
2. Measure correctness, tool reliability, latency, context behavior, GPU use,
   handoff quality, and boundary compliance.
3. Add a user-friendly workspace status panel or model description in Open
   WebUI if the selector alone is not sufficiently clear.
4. Add durable automatic stopping-point handoffs.
5. Inventory `.15` after iClone installation and prepare the separately named
   `MAE Avatar 01` Windows workspace.
