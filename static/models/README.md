# MAE character model drop-in

> **This slot is deliberately empty as of 2026-08-14.** Grace (an Amazon
> Sumerian Host) shipped in task def :73, rendered correctly, and was pulled
> the same day: the lip sync read badly enough that Ted's call was that no
> character beats that character. The page is back on the animated portraits,
> which is the documented fallback below, not a degraded mode. The shipped
> file is recoverable from git at `42f236a`, and `grace_v11.glb` is still on
> `.15`. Do not re-add a character here until the lip sync is measured — a new
> model does not fix a pipeline fault, and which of the two it is has not been
> established yet.


Put the character here as **`mae.glb`**. That is the whole handoff: the
avatar runtime (`static/js/lcdash-mae-avatar.js`) requests
`/static/models/mae.glb` on every page load, and the moment the file exists
it swaps from the animated portrait photos to the 3D character. No code
change, no config, no redeploy beyond shipping the file in the image.

If the load fails or the file is absent, the portrait renderer stays. That
fallback is deliberate and permanent — MAE is never a blank screen.

## What the file must contain

**Format — glTF Binary (`.glb`), one self-contained file.** Not `.gltf` +
loose files, not FBX, not USD. Textures embedded. The runtime loads exactly
one URL.

**The 52 ARKit blendshapes, named exactly.** This is the part that breaks
most often and the part that matters most: the runtime drives morph targets
*by name*, so `jawOpen` works and `Jaw_Open`, `jawopen`, or `CC_Base_JawOpen`
silently do nothing. In Character Creator, this means exporting with the
**ARKit blendshape profile ON**; some presets rename the shapes.

Load-bearing names (the mouth ones are what lip sync uses):

- Jaw and mouth: `jawOpen`, `mouthClose`, `mouthFunnel`, `mouthPucker`,
  `mouthSmileLeft`, `mouthSmileRight`, `mouthPressLeft`, `mouthPressRight`,
  `mouthRollLower`, `tongueOut`
- Eyes and brows: `eyeBlinkLeft`, `eyeBlinkRight`, `browInnerUp`

The other ARKit shapes are welcome and unused today; Audio2Face drives them
in Phase 3, so export the full 52 rather than a subset.

**You do not have to guess whether the export is right.** On load the
runtime logs every morph-target name it finds and prints a warning for each
expected name that is missing. Open the browser console on `/mae/avatar`
after dropping in a new file — a silent console means the rig is good, and
a wall of "missing ARKit shape" warnings means the export profile was off.

## Scale, orientation, and pose

The camera framings assume a human-scaled figure:

- **Metres, Y-up, facing +Z**, feet at the origin (`y = 0`).
- Roughly **1.6–1.7 m tall**, head around `y = 1.6`. The console framing
  points a 26° lens at `y = 1.6`; a model in centimetres will fill the pane
  with an ankle.
- **Full body**, standing, arms down, neutral face. The trade-show screen
  shows all of her; the console just frames the shoulders up.
- Mouth **closed** in the rest pose. Every viseme is a displacement from
  rest, so a rest pose with the mouth open reads as permanently slack-jawed.

## Practical limits

- Aim for **under ~30 MB**. The file ships inside the container image and is
  downloaded by every browser that opens the page, including the booth
  laptop on hotel wifi. 2K texture atlas, decimate if needed.
- Single mesh or a few — every mesh carrying morph targets gets driven, so
  a face split across several meshes is fine.

## Licensing, if the character is bought rather than built

Check the licence permits **commercial use and public display** before it
goes anywhere near the booth: a trade-show kiosk is public performance, and
some marketplace characters are personal-use only. Also confirm it is not
derived from MetaHuman output — the MetaHuman EULA bars using it to train
or test AI models, and MAE is the face of an AI product.

## What is NOT needed here

FBX/USD exports for Unreal and Audio2Face live on `.15`, not in this repo.
This directory is only what the browser loads.

Background and the phase plan: `docs/planning/MAE_AVATAR_PLAN_2026-08-09.md`.
