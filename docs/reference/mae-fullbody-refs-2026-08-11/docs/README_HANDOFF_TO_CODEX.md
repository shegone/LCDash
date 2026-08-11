# MAE Full-Body Avatar – Export Package for Codex / Local PC15 Work

**Date:** 2026-08-11  
**Source:** Grok (LCDash MetaHuman skill)  
**Purpose:** Start full-body MetaHuman / Character Creator avatar for MAE (and other LCDash AIs)  
**Target machine:** PC15 (Windows, RTX 3090) – Unreal 5.8 project `MAE_Avatar_Baseline 5.8`  
**Production rule:** Avatar is optional presentation layer only. Static portraits remain the mandatory fallback. Never block text/audio/ops on .227.

**Local static portrait path (production fallback):**  
`E:\Projects\LCDash-AWS\static\img\mae`  
(This is the live folder Codex / the Unreal project must fall back to when Pixel Streaming or the full-body renderer is unavailable.)

---

## Package Contents

```
MAE_FullBody_Avatar_Export_for_Codex/
├── 01_Original_Portrait/
│   ├── MAE_original_portrait_headshot.jpg   ← ground-truth face identity
│   └── MAE_face_cutout_clean.png            ← clean alpha cutout of face+headset
├── 02_Tpose_Front/
│   └── MAE_Tpose_front.jpg                  ← primary T-pose front (rigging start)
├── 03_Tpose_Side/
│   └── MAE_Tpose_side.jpg                   ← side profile T-pose
├── 04_Tpose_Back/
│   └── MAE_Tpose_back.jpg                   ← rear T-pose
├── 05_Apose_Idle/
│   └── MAE_Apose_idle.jpg                   ← neutral A-pose for idle state
├── 06_Turnaround_Sheet/
│   └── MAE_Tpose_turnaround_sheet.png       ← front+side+back composite
└── docs/
    └── README_HANDOFF_TO_CODEX.md           ← this file
```

All images: pure white seamless background, even studio lighting, photoreal, high detail for photo-matching / texture reference.

---

## Visual Identity Targets (MAE)

- **Hair:** Shoulder-length wavy auburn / reddish-brown, middle part  
- **Eyes:** Hazel-green, subtle eyeliner  
- **Face:** Calm professional expression, soft nude lips  
- **Headset:** Black single-ear boom mic (keep visible)  
- **Clothing:** Dark navy long-sleeve button-up collared shirt + matching navy tailored trousers + black belt + black closed-toe heels  
- **Body:** Average young-adult female proportions suitable for MetaHuman / CC4 standard skeleton

**Face ground truth** = `01_Original_Portrait/MAE_original_portrait_headshot.jpg`  
(also compare against any existing production portraits already in `E:\Projects\LCDash-AWS\static\img\mae`)  

Use the full-body views only for body proportions, clothing, multi-angle reference, and T-pose topology.  
Slight generative drift on face is expected; always re-anchor to the original headshot (and the production statics) when sculpting/morphing.

---

## Recommended Pipeline (from LCDash MetaHuman skill)

1. **Character Creator 4 (preferred) or MetaHuman Creator**
   - Import / photo-match the T-pose front + original portrait.
   - Build or morph a full-body character that matches the identity above.
   - Use the turnaround sheet for front/side/back accuracy.
   - Keep clothing simple (navy shirt + pants) so Auto Setup retargeting stays clean. Later variants can add jackets etc.

2. **iClone 8 (optional performance prep)**
   - Create basic idle + talking facial/body performance if useful.
   - Export FBX or use Live Link (license permitting).

3. **Import into Unreal 5.8 on PC15**
   - Project: `C:\Users\admin\UnrealProjects\MAE_Avatar_Baseline 5.8`
   - Use already-verified **Reallusion Auto Setup 2.03 + RLPlugin**.
   - Place in a dedicated full-body map (do not change project default map yet).
   - Backup first: `C:\MAE-Agent\backups\` with timestamp.

4. **Unreal work**
   - Retarget to MetaHuman / Control Rig skeleton.
   - Build idle ↔ talking animation blueprints.
   - Talking animation runs **only** while MAE speech audio is playing, then returns to calm idle.
   - Lip-sync / expression driven from local speech audio generated on .227 (viseme or audio-driven). Never delay text response.
   - Output: Pixel Streaming **or** direct render to the 512×1536 portrait LED at 60 Hz.
   - Automatic fallback to the static portraits in `E:\Projects\LCDash-AWS\static\img\mae` when renderer/stream/PC15 is unavailable.

5. **Safety / Isolation**
   - No CAD payloads, credentials, or sensitive records in the Unreal project.
   - Avatar work never touches production CAD writes, station alerts, radio, or live emergency data.
   - Prefer FBX export/import or Unreal-native MetaHuman Animator until Live Link license is renewed.

---

## Acceptance Criteria for “Done” Full-Body Avatar

- [ ] Full-body MetaHuman (or CC4 character) that clearly matches MAE visual identity.
- [ ] Reliable idle + talking states with natural lip-sync driven by local speech audio.
- [ ] Automatic fallback to existing static portraits in `E:\Projects\LCDash-AWS\static\img\mae`.
- [ ] Portrait framing correct for 512×1536 @ 60 Hz.
- [ ] Zero impact on MAE response latency or operational reliability on 14.1.1.227.
- [ ] Project is clean, versioned, and backed up before structural changes.

---

## How Codex Should Start Locally

1. Unpack this entire folder onto PC15 (e.g. under `C:\MAE-Agent\avatar_refs\MAE_FullBody_2026-08-11\`).
2. Open Character Creator 4 (or MetaHuman Creator).
3. Load `02_Tpose_Front/MAE_Tpose_front.jpg` as primary photo reference + `01_Original_Portrait/MAE_original_portrait_headshot.jpg` for face lock.
4. Use `06_Turnaround_Sheet` and individual side/back for multi-view validation.
5. Export the finished character via the verified Auto Setup path into the existing Unreal project.
6. After first successful import + retarget, update:
   - `docs/CURRENT_PC15_AGENT_STATE_*.md`
   - `docs/PC15_AVATAR_INVENTORY_*.md`
   - and create a timestamped backup.

---

## Notes / Known Limitations of this Export

- These are **image references only**, not a rigged mesh or .mhproj / .ccproj file.  
  Actual character creation + skinning + Control Rig still happens on PC15.
- Face identity is as close as the image model could achieve from a single headshot; always prefer the original portrait for final face DNA / texture bake.
- Clothing is a clean professional navy set. If a different outfit (lab coat, casual, etc.) is desired later, regenerate variants from these base poses.
- No 3D model, FBX, or Unreal asset is included — that is the work Codex + human operator will produce on PC15.

---

## Contact / Next Iteration

If you need:
- higher resolution (4K) versions,
- additional angles / expressions / clothing variants,
- a different body type or male variant for other AIs,
- or a pre-labeled FBX dummy,

just request a new export package from Grok with the LCDash MetaHuman skill active.

**End of handoff.**  
Work in small reversible packages. State outcome + acceptance check + rollback before any structural Unreal change.
