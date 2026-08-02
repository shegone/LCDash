r"""Export the disposable MAE iClone test avatar to an Unreal-ready FBX.

Run this file manually from iClone 8 with Script > Load Python only after
opening C:\MAE-Agent\tests\MAE_iClone_FBX_Test.iProject. The script refuses
to overwrite an existing FBX, requires exactly one avatar in the scene, checks
the FBX export license, and never opens or changes Unreal Engine.
"""

from datetime import datetime
from pathlib import Path
import traceback

import RLPy


TEST_DIR = Path(r"C:\MAE-Agent\tests")
SOURCE_PROJECT = TEST_DIR / "MAE_iClone_FBX_Test.iProject"
EXPORT_DIR = TEST_DIR / "fbx_export"
FBX_PATH = EXPORT_DIR / "MAE_iClone_FBX_Test_Unreal.fbx"
RESULT_PATH = TEST_DIR / "MAE_iClone_FBX_Export.result.txt"
INCLUDE_MOTION_PATH = Path(
    r"C:\Users\Public\Documents\Reallusion\Reallusion Templates"
    r"\Animation\Motion\2.Human Female\Idle\Female Idle_1.rlMotion"
)


def write_result(lines):
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def status_succeeded(status):
    """Compare RLPy status values without assuming their Python representation."""
    try:
        return status == RLPy.RStatus.Success
    except Exception:
        return "success" in str(status).lower()


def run_export():
    lines = [
        "MAE iClone disposable FBX export",
        "Started: " + datetime.now().isoformat(timespec="seconds"),
        "Scope: one local test FBX only; no Unreal changes",
    ]

    try:
        if not SOURCE_PROJECT.is_file():
            raise FileNotFoundError(f"Disposable project not found: {SOURCE_PROJECT}")
        if not INCLUDE_MOTION_PATH.is_file():
            raise FileNotFoundError(f"Include-motion file not found: {INCLUDE_MOTION_PATH}")
        if FBX_PATH.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing export: {FBX_PATH}"
            )

        avatars = RLPy.RScene.GetAvatars()
        if len(avatars) != 1:
            raise RuntimeError(
                "Expected exactly one avatar in the open disposable project; "
                f"found {len(avatars)}"
            )
        avatar = avatars[0]
        lines.append(f"PASS: Exactly one avatar found ({type(avatar).__name__})")

        license_status = RLPy.RFileIO.CheckExportFbxHasLicense(avatar)
        if not status_succeeded(license_status):
            raise RuntimeError(
                "iClone reports that this avatar does not have a usable FBX "
                f"export license (status={license_status})"
            )
        lines.append(f"PASS: FBX export license check (status={license_status})")

        export_option = RLPy.EExportFbxOptions__None
        export_option2 = RLPy.EExportFbxOptions2__None
        export_option3 = RLPy.EExportFbxOptions3__None

        export_option |= RLPy.EExportFbxOptions_AutoSkinRigidMesh
        export_option |= RLPy.EExportFbxOptions_ExportRootMotion
        export_option |= RLPy.EExportFbxOptions_ZeroMotionRoot
        export_option |= (
            RLPy.EExportFbxOptions_ExportPbrTextureAsImageInFormatDirectory
        )
        export_option |= RLPy.EExportFbxOptions_InverseNormalY

        export_option2 |= RLPy.EExportFbxOptions2_UnrealEngine4BoneAxis
        export_option2 |= RLPy.EExportFbxOptions2_RenameDuplicateBoneName
        export_option2 |= RLPy.EExportFbxOptions2_RenameDuplicateMaterialName
        export_option2 |= RLPy.EExportFbxOptions2_RenameTransparencyWithPostFix
        export_option2 |= RLPy.EExportFbxOptions2_RenameBoneRootToGameType
        export_option2 |= RLPy.EExportFbxOptions2_RenameBoneToLowerCase
        export_option2 |= RLPy.EExportFbxOptions2_ResetBoneScale
        export_option2 |= RLPy.EExportFbxOptions2_ResetSelfillumination
        export_option2 |= RLPy.EExportFbxOptions2_ExtraWordForUnityAndUnreal
        export_option2 |= RLPy.EExportFbxOptions2_BakeMouthOpenMotionToMesh
        export_option2 |= RLPy.EExportFbxOptions2_UnrealIkBone
        export_option2 |= RLPy.EExportFbxOptions2_UnrealPreset

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        export_status = RLPy.RFileIO.ExportFbxFile(
            avatar,
            str(FBX_PATH),
            export_option,
            export_option2,
            export_option3,
            RLPy.EExportTextureSize_Original,
            RLPy.EExportTextureFormat_Default,
            str(INCLUDE_MOTION_PATH),
        )
        if not status_succeeded(export_status):
            raise RuntimeError(f"ExportFbxFile failed with status: {export_status}")
        if not FBX_PATH.is_file() or FBX_PATH.stat().st_size == 0:
            raise RuntimeError(
                "ExportFbxFile returned success but no non-empty FBX was created"
            )

        lines.extend(
            [
                f"PASS: FBX export completed (status={export_status})",
                f"PASS: FBX exists ({FBX_PATH.stat().st_size} bytes)",
                f"FBX: {FBX_PATH}",
                "RESULT: PASS",
                "Completed: " + datetime.now().isoformat(timespec="seconds"),
            ]
        )
    except Exception as exc:
        lines.extend(
            [
                f"FAIL: {type(exc).__name__}: {exc}",
                traceback.format_exc().rstrip(),
                "RESULT: FAIL",
                "Completed: " + datetime.now().isoformat(timespec="seconds"),
            ]
        )

    write_result(lines)
    outcome = lines[-2] if len(lines) >= 2 else "RESULT: UNKNOWN"
    RLPy.RUi.ShowMessageBox(
        "MAE Disposable FBX Export",
        outcome + "<br><br>Result file:<br>" + str(RESULT_PATH),
        RLPy.EMsgButton_Ok,
    )


def run_script():
    """Entry point required by iClone's Script > Load Python command."""
    run_export()
