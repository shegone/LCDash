# MAE character model drop-in

Place the Character Creator 5 export here as `mae.glb`. The avatar runtime
(`static/js/lcdash-mae-avatar.js`) tries to load `/static/models/mae.glb` at
page load; until the file exists it logs an info line and keeps the
procedural placeholder.

Export requirements (from docs/planning/MAE_AVATAR_PLAN_2026-08-09.md):

- **glTF Binary (.glb)**, full body.
- **ARKit blendshape profile ON.** The runtime drives morph targets by their
  ARKit names (`jawOpen`, `mouthFunnel`, `eyeBlinkLeft`, ...). On load it
  logs every morph-target name it finds and warns for each expected name
  that is missing -- check the browser console after dropping a new export,
  because some CC5 presets rename the shapes and the mouth silently freezes.
- Keep the file reasonably sized for first paint (texture atlas 2K,
  mesh decimation if needed); it is served from the app container.

The FBX/USD exports for Unreal and Audio2Face live on `.15`, not here --
this directory is only what the web runtime loads.
