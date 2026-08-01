# PC15 Avatar Workstation Inventory

Date: 2026-08-01

This was a read-only inventory. iClone, Character Creator, and Unreal Editor
were not closed or modified.

## Workstation

- Host: `AI-AVATAR` / `14.1.1.15`
- OS: Windows 11 Pro
- GPU: NVIDIA GeForce RTX 3090, 24 GB VRAM
- NVIDIA driver: `610.88`
- System drive free space at inventory: approximately 1.56 TB

Windows Management Instrumentation reported 4 GB for the adapter because its
legacy field cannot represent the full RTX 3090 memory size. `nvidia-smi`
reported the correct 24,576 MiB.

## Creative software

- Unreal Engine 5.8
- iClone 8.74
- Character Creator 4.72
- Headshot 2.02
- Video Mocap 1.04
- Blender Pipeline 2.4
- MetaTailor Pipeline 1.01
- Omniverse Connector 1.02
- Reallusion Hub 5.62

iClone, Character Creator, and Unreal Editor were all running during the
inventory.

## Unreal project

Project:

`C:\Users\admin\UnrealProjects\MAE_Avatar_Baseline 5.8`

Project file:

`MAE_Avatar_Baseline.uproject`

Project signals:

- Engine association: 5.8
- 673 `.uasset` files
- One map: `Content\MAE_FullBody_Test.umap`
- MetaHuman content: 489 files
- MC Sample content: 42 files
- MetaHuman, Live Link, Live Link Control Rig, and Apple ARKit Face Support are
  enabled in the project descriptor.
- The configured game default remains Unreal's Open World template rather than
  the MAE test map.
- No project-local `Plugins` integration was present.

## Reallusion bridge readiness

Initial inventory found the built-in Unreal Live Link and MetaHuman plugins but
no Reallusion/iClone Unreal plugin. The bridge was then installed and validated
as described below.

Reallusion's current UE 5.8 path requires:

- iClone Live Link Plug-in for Unreal 1.38 or later, installed to iClone through
  Reallusion Hub; and
- Auto Setup for Unreal All-in-One 2.03 or later, copied into the Unreal
  project.

The installed iClone 8.74 and Character Creator 4.72 meet Reallusion's stated
minimum versions for this bridge.

## Bridge installation completed

- iClone Unreal Live Link 1.38 Trial is installed in iClone 8.
- The signed Reallusion Auto Setup 2.03 installer was verified before use.
- A pre-install project backup was created at:
  `C:\MAE-Agent\backups\MAE_Avatar_Baseline_pre_autosetup_20260801-191947`
- The backup contains 681 project files totaling approximately 1.38 GB.
- The UE 5.8 Auto Setup package was copied into the project only after a
  collision check returned zero existing-file conflicts.
- All 549 copied package files passed presence and size verification.
- Installed project plugin: `Plugins\RLPlugin`, version 2.03, engine 5.8.0.
- Unreal reopened successfully and logged
  `LogPluginManager: Mounting Project plugin RLPlugin`.
- No missing-module, incompatible-plugin, or plugin-load failure was found.
- The unrelated PIX capture warning in the Unreal log does not indicate an
  RLPlugin load failure.

## Recommended first supervised workflow

1. Open iClone with the installed Live Link 1.38 Trial.
2. Use `MAE_FullBody_Test` for a short observation-only Live Link connection
   test, then record a disposable five-second motion take.
3. Evaluate the trial's practical and licensing limits before purchasing the
   paid Live Link license.

Do not change the project default map or modify the baseline MetaHuman until a
recoverable project backup has been verified.
