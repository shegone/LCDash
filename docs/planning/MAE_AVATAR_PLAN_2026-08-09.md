# MAE Avatar — build plan (2026-08-09)

Ted's vision, in his words: a **dispatcher copilot at the console**, and a
**full-body avatar someone can talk to at a trade show**. Same MAE, same brain,
different frames. This plan gets there in stages that each ship something
usable, starting from what we already own: the CC5/iClone licenses and RTX 3090
on `.15`, the cloud MAE backend (Bedrock Nova Pro tool-calling, live CAD tools,
knowledge base, Polly), and the deny-by-default path-allowlist machinery just
live-verified in the sanitized tier.

## Ground rules (from the 2026-08-06 research, still true)

- **Character is built in Character Creator 5, not MetaHuman Creator** (web
  app shuts down 2026-11-05). CC5 exports the 52 ARKit blendshapes that make
  one asset drive everything: three.js, Unreal, Audio2Face.
- **No server-side GPU rendering in AWS.** g5 pixel streaming is ~$734/mo per
  concurrent stream — the browser (or the trade-show laptop) renders; the
  cloud serves the asset and drives it.
- **Polly NEURAL voices only** (Joanna/Matthew class). Generative voices do
  not emit visemes; lip-sync dies. This is a pinned constraint, test-worthy.
- **Vendor avatar clouds egress audio** (Rapport/Soul Machines/HeyGen) —
  CJIS/PII question the moment MAE speaks about live calls. Everything in
  this plan stays inside the AWS account / county network.
- **MetaHuman EULA** bars using its output to train AI models — moot since we
  are not using MetaHuman, but recorded here because "MetaHuman-style" keeps
  coming up in conversation.

## Architecture (one sentence)

A glTF character rendered by the client's own GPU (browser three.js for the
console; the trade-show laptop for the booth), driven by a blendshape+audio
stream whose *source* upgrades over time — Polly visemes first (pure cloud,
$0), Audio2Face-3D on `.15` later — against the existing MAE backend
unchanged.

```
                    ┌── console dispatcher: browser tab, head-and-shoulders
  clients ──────────┼── station display: browser fullscreen (existing TV pattern)
                    └── trade show: laptop → 512x1536 portrait screen, full body
        ▲ audio + ARKit blendshape weights (WebSocket)
  cloud (ECS, existing service): /mae/avatar routes
        Bedrock Nova Pro + live CAD tools + KB   → text
        Polly neural                              → audio + viseme marks
        [phase 3 alternative source: .15 Audio2Face-3D → full facial weights]
```

## Access model — reuse the tier machinery

- New Cognito group `lcdash-pilot-avatar`: its path allowlist is ONLY the
  avatar routes. Someone in this group can talk to MAE's face and reach
  nothing else — no dashboard, no CAD payloads beyond what MAE says aloud.
- Dashboard roles (admin/supervisor) get a role-gated nav link to
  `/mae/avatar`. The `user` tier does NOT get the link: MAE reads live CAD,
  which is exactly what that tier exists to withhold.
- Same pool, same ALB, same identity verification, zero new infrastructure.
  A separate hostname would need a second ALB or a ListenerRule (prohibited
  by the deployment allowlist); the path allowlist delivers the same
  separation without either.

## Phases

**Phase 0 — the character (Ted, on `.15`, starts now).**
Build MAE in CC5. Deliverables: the character with 52 ARKit blendshapes
verified, exported twice — glTF/GLB (web runtime) and FBX/USD (Unreal,
Audio2Face). Full body from day one (trade show needs it; the console just
frames the shoulders). I build against a placeholder head in parallel, so
Phase 1 does not wait on this.

**Phase 1 — cloud web runtime (the next code project).**
`/mae/avatar` on the existing service: three.js glTF viewer, chat +
push-to-talk (existing Transcribe wiring), MAE tool-calling backend as-is,
Polly neural audio + viseme marks mapped to ARKit mouth shapes client-side,
plus idle motion (blink/sway/gaze) so she is never a statue. The
`lcdash-pilot-avatar` group and the role-gated dashboard link ship here.
Exit test: a dispatcher asks "what's waiting longest?" and MAE answers it,
aloud, with her face moving credibly, from live CAD.

**Phase 2 — dispatcher copilot ergonomics.**
What makes it a *console* tool: barge-in (talk over her, she stops),
hands-free trigger worth evaluating carefully in a room full of radio
traffic, a compact always-on-top framing, and the station-display fullscreen
mode reusing the alerts pattern. Scope decided after real dispatchers use
Phase 1.

**Phase 3 — Audio2Face-3D on `.15` (the fidelity upgrade).**
A2F-3D (open-source, MIT, ~3 GB VRAM — the 3090 is in-spec) turns Polly's
audio into full facial performance: co-articulated lips, brow, emotion.
Runs as a service on `.15`; the runtime just switches its weight-stream
source. **Network reality:** `.15` is on the county LAN. On-prem consoles
reach it directly; cloud/external users stay on the Polly-viseme tier until
we decide whether relaying weights through the cloud is worth it. The web
runtime is built in Phase 1 so this is a source swap, not a rewrite.

**Phase 4 — the trade-show rig.**
Ted's laptop renders full-body MAE on the foldable 512x1536 portrait screen
(roughly life-size standing figure). Same web runtime in kiosk fullscreen —
or, if we want to show off, the CC5 character in Unreal on the laptop (it
renders locally, so the $734/mo cloud objection does not apply).
**Hard rule: the booth runs on SYNTHETIC data only.** A public kiosk
narrating live Logan County CAD is a PII breach with an audience. The
synthetic-disconnected mode the cloud app already has is exactly the right
demo backend. Booth account: an `avatar`-group login on a dedicated demo
identity, so a walk-up stranger is talking to something that can reach
nothing real.

## Costs

Phases 0–2: effectively $0 beyond existing spend — Bedrock/Polly per-use
pennies, no new AWS resources. Phase 3: electricity on `.15`. Phase 4:
whatever the booth costs; no cloud delta. The expensive path (server-side
pixel streaming) is deliberately not on the roadmap; if the fidelity bar
ever demands it, it is a bolt-on for specific events, not a rebuild.

## Traps, pinned now

- Polly generative voices ≠ visemes. A voice change that "sounds nicer" will
  silently freeze her mouth. Pin the voice ID in config with a comment and a
  test.
- CC5 must export with ARKit blendshape profile ON — verify the 52 names in
  the glTF before building mappings; some export presets rename them.
- The avatar group's allowlist must include the MAE *speech* endpoints but
  NOT the raw CAD/analytics APIs those tools use server-side — the model
  talks, the browser never sees the data feeds.
- Trade-show laptop is a shared/exposed machine: no county credentials cached
  in the browser profile; demo identity only.
- MCP stays out of the production avatar path (dev-time authoring only) —
  standing decision from the research.

## Open questions (decide by end of Phase 1)

1. Voice: keep MAE's current Polly neural voice, or audition alternatives
   *within neural* before her face fixes an identity to it?
2. Does the fire-station TV get the avatar, or is that noise for a display
   whose job is tones and addresses? (Lean: not v1.)
3. Phase 3 relay: is cloud-user A2F fidelity worth streaming weights out of
   `.15`, or does Polly-viseme quality hold for remote users?
