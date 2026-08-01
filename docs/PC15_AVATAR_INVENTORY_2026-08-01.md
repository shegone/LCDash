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

## Live Link trial result

The installed iClone Unreal Live Link 1.38 Trial reports that its trial period
has expired. No connection, transfer, activation, or recording was attempted.
Do not purchase the paid connector solely to continue setup validation.

The no-purchase evaluation path is:

1. Keep the verified free Auto Setup 2.03 project integration.
2. Create or edit a short disposable animation in iClone.
3. Export the animation through the available FBX allowance or the user's
   existing iClone subscription rights.
4. Import through Auto Setup and retarget or sequence it in Unreal.
5. Compare the resulting animation quality and labor against Unreal-native
   MetaHuman Animator and audio-driven facial animation before deciding whether
   real-time Live Link is worth its license cost.

## Recommended first supervised workflow

1. Create a disposable five-second iClone animation without changing the MAE
   baseline project.
2. Export it to FBX and import it through the verified Auto Setup integration.
3. Retarget it to a disposable Unreal test character or sequence.
4. Evaluate the result against Unreal-native MetaHuman animation options before
   considering the paid Live Link license.

Do not change the project default map or modify the baseline MetaHuman until a
recoverable project backup has been verified.
