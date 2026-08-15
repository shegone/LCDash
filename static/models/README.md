# MAE character model drop-in

> **Shipped file: Grace, rebaked (`grace_v12.glb`), 2026-08-14.** She shipped
> once before, on the same day, and was pulled within hours: every viseme was
> visibly crooked. The cause was not the visemes but the rest pose baked in
> ahead of them -- see the commit for `scripts/bake_sumerian_host.py`. Do not
> re-bake this character with an older copy of that script.
>
> Two things measured about her that look like bugs and are not: on
> `char:mouthShape` (the inner mouth) the visemes `S`, `T`, `f` and `sil` move
> nothing, and `E`/`r`, `a`/`k`, `e`/`i` are identical. That mesh is skinned
> almost entirely to the jaw, those four sounds are made with a closed jaw,
> and those pairs share a jaw angle. It is correct. Leave it alone.


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
