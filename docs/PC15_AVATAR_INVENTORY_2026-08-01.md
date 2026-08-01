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

The built-in Unreal Live Link and MetaHuman plugins are installed, but no
Reallusion/iClone Unreal plugin was found at the project or UE 5.8 engine level.

Reallusion's current UE 5.8 path requires:

- iClone Live Link Plug-in for Unreal 1.38 or later, installed to iClone through
  Reallusion Hub; and
- Auto Setup for Unreal All-in-One 2.03 or later, copied into the Unreal
  project.

The installed iClone 8.74 and Character Creator 4.72 meet Reallusion's stated
minimum versions for this bridge.

## Recommended first supervised workflow

1. Save and back up the current Unreal project and iClone project.
2. Install iClone Live Link through Reallusion Hub.
3. Download Auto Setup All-in-One 2.03 for UE 5.8.
4. Close Unreal Editor before copying the Auto Setup plugin into the project.
5. Reopen the project and verify plugin loading before enabling any autonomous
   changes.
6. Use `MAE_FullBody_Test` for a short observation-only Live Link connection
   test, then record a disposable five-second motion take.

Do not change the project default map or modify the baseline MetaHuman until a
recoverable project backup has been verified.
