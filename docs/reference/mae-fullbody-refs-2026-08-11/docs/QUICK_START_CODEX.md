# Codex Quick Start – MAE Full-Body Avatar

**Goal:** Create a full-body MetaHuman / CC4 character for MAE that matches the supplied portrait + T-pose references, then import into Unreal 5.8 on PC15 via Reallusion Auto Setup.

## Immediate Actions

1. Copy the whole `MAE_FullBody_Avatar_Export_for_Codex` folder to PC15:
   ```
   C:\MAE-Agent\avatar_refs\MAE_FullBody_2026-08-11\
   ```

2. Open **Character Creator 4** (preferred) or MetaHuman Creator.

3. Photo-match / morph:
   - Face lock → `01_Original_Portrait/MAE_original_portrait_headshot.jpg`
   - Body + clothing + pose → `02_Tpose_Front/MAE_Tpose_front.jpg`
   - Validate multi-view → `06_Turnaround_Sheet/MAE_Tpose_turnaround_sheet.png` + side/back folders

4. Export character with **Reallusion Auto Setup 2.03 + RLPlugin** into:
   ```
   C:\Users\admin\UnrealProjects\MAE_Avatar_Baseline 5.8
   ```

5. **Before any project change**: create timestamped backup under `C:\MAE-Agent\backups\`

6. In Unreal:
   - Dedicated full-body map (do not touch default map yet)
   - Retarget → Control Rig
   - Idle ↔ Talking state machine (talk only while speech audio plays)
   - Lip-sync from local audio (never delay text response)
   - Fallback to static portraits always available at:
     `E:\Projects\LCDash-AWS\static\img\mae`

7. Update handoff docs after success:
   - `docs/CURRENT_PC15_AGENT_STATE_*.md`
   - `docs/PC15_AVATAR_INVENTORY_*.md`

## Success = 
Full-body MAE that looks like the portrait, T-poses cleanly, idles calmly, talks with lip-sync, falls back safely, and never impacts .227 latency.

Full details in `README_HANDOFF_TO_CODEX.md`.
