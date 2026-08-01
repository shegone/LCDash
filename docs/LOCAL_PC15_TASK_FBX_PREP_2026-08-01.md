# Local Agent Work Order - PC15 Disposable FBX Preparation

Date: 2026-08-01
Owner: PC15 local agent
Classification: PLAN and CHANGE (draft files only); no execution

## Outcome

Prepare a reviewable, fail-closed native iClone Python helper and validation
checklist for exporting the already verified disposable iClone project to FBX.
Do not execute the export or operate iClone or Unreal.

## Required context

Read these files before working:

1. `C:\project mae share\MAE Progress Handoffs\LATEST_PC15.md`
2. `C:\project mae share\MAE Progress Handoffs\iclone_create_disposable_test.py`
3. `C:\project mae share\MAE Progress Handoffs\MAE_iClone_FBX_Test.result.txt`

Known verified input project:

`C:\MAE-Agent\tests\MAE_iClone_FBX_Test.iProject`

The project was created by native iClone Python, contains a neutral female
avatar with `Female Idle_1.rlMotion`, and was verified at 77,258,714 bytes.

## Allowed work

- Read official Reallusion iClone 8 Python API documentation.
- Inspect installed iClone/Reallusion files and version information read-only.
- Draft a native `RLPy` export helper.
- Draft file-integrity and output-validation checks.
- Write non-secret findings and draft files only to:
  `C:\project mae share\MAE Progress Handoffs`

## Prohibited work

- Do not load or execute any Python script in iClone.
- Do not export FBX yet.
- Do not click or control iClone or Unreal.
- Do not open, import, save, compile, or modify the Unreal baseline.
- Do not install, remove, purchase, activate, or update software or licenses.
- Do not modify `.227`, production services, CAD, networking, credentials, or
  repository history.
- Do not guess at unlabeled GUI controls.
- Do not place secrets, raw CAD data, model files, FBX files, textures, or large
  binary assets in Git or the handoff text.

## Technical requirements for the draft helper

Create:

`C:\project mae share\MAE Progress Handoffs\iclone_export_disposable_fbx_DRAFT.py`

The helper must:

1. expose the iClone `run_script()` entry point;
2. operate only on the project already open in iClone or explicitly load only
   `C:\MAE-Agent\tests\MAE_iClone_FBX_Test.iProject`;
3. export only to `C:\MAE-Agent\tests\fbx_export`;
4. refuse to overwrite an existing FBX or output directory;
5. select exactly one avatar and fail if zero or multiple avatars exist;
6. check the FBX export license/API status before export when the installed API
   supports a read-only license check;
7. use Unreal-compatible FBX options supported by the installed iClone 8.74
   bindings, without inventing enum names or signatures;
8. catch exceptions and write a text result beside the draft script in the
   shared handoff folder;
9. report paths, statuses, and file sizes without credentials or private data;
10. stop before any Unreal action.

Do not mark an API signature verified unless supported by official Reallusion
documentation or direct read-only inspection of the installed binding. Clearly
label documentation that may be older than iClone 8.74.

## Required deliverables

In the shared handoff folder, create:

1. `iclone_export_disposable_fbx_DRAFT.py`
2. `PC15_FBX_EXPORT_RESEARCH_2026-08-01.md`
3. `LOCAL_PC15_FBX_PREP_RESULT_2026-08-01.md`

The research note must include:

- exact installed iClone and Python API versions observed;
- official documentation links used;
- verified `RLPy.RFileIO.ExportFbxFile` signature/options;
- license-check behavior and unresolved licensing questions;
- expected FBX and texture outputs;
- a proposed disposable Unreal import location;
- risks and questions for hosted Codex review.

The result note must include:

- `PASS`, `FAIL`, or `BLOCKED` for draft preparation only;
- files created and checks performed;
- confirmation that nothing was executed in iClone or Unreal;
- exact unresolved items;
- the next supervised action for hosted Codex.

## Acceptance and stopping point

PASS only when the three draft deliverables exist, the Python file passes a
syntax check outside iClone, all referenced API names are evidence-backed, and
the agent confirms no iClone/Unreal operation occurred.

Stop immediately after producing the deliverables. If the same technical issue
occurs twice, record it as BLOCKED and stop. Do not ask another model to execute
the export.
