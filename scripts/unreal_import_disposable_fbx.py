r"""Import the disposable PC15 FBX into an isolated Unreal test folder.

Run only inside the MAE_Avatar_Baseline Unreal Editor project. The script
refuses to run when the destination already exists and never alters existing
assets. It writes a plain-text result to the shared MAE handoff folder.
"""

from datetime import datetime
from pathlib import Path
import traceback

import unreal


SOURCE_FBX = Path(
    r"C:\MAE-Agent\tests\fbx_export\MAE_iClone_FBX_Test_Unreal.fbx"
)
DESTINATION = "/Game/MAE_Test/DisposableFBX_20260801"
EXPECTED_PROJECT = "MAE_Avatar_Baseline"
RESULT_PATH = Path(
    r"C:\project mae share\MAE Progress Handoffs"
    r"\MAE_Unreal_FBX_Import.result.txt"
)


def write_result(lines):
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_import():
    lines = [
        "MAE Unreal disposable FBX import",
        "Started: " + datetime.now().isoformat(timespec="seconds"),
        f"Destination: {DESTINATION}",
    ]

    try:
        project_name = unreal.Paths.get_base_filename(
            unreal.Paths.get_project_file_path()
        )
        if project_name != EXPECTED_PROJECT:
            raise RuntimeError(
                f"Wrong Unreal project: expected {EXPECTED_PROJECT}, got {project_name}"
            )
        lines.append(f"PASS: Correct project ({project_name})")

        if not SOURCE_FBX.is_file() or SOURCE_FBX.stat().st_size == 0:
            raise FileNotFoundError(f"Source FBX missing or empty: {SOURCE_FBX}")
        lines.append(f"PASS: Source FBX exists ({SOURCE_FBX.stat().st_size} bytes)")

        if unreal.EditorAssetLibrary.does_directory_exist(DESTINATION):
            raise FileExistsError(
                f"Refusing to modify existing Unreal destination: {DESTINATION}"
            )

        options = unreal.FbxImportUI()
        options.set_editor_property("automated_import_should_detect_type", False)
        options.set_editor_property(
            "mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH
        )
        options.set_editor_property("import_as_skeletal", True)
        options.set_editor_property("import_mesh", True)
        options.set_editor_property("import_animations", True)
        options.set_editor_property("import_materials", True)
        options.set_editor_property("import_textures", True)
        options.set_editor_property("create_physics_asset", True)

        skeletal_data = options.get_editor_property("skeletal_mesh_import_data")
        skeletal_data.set_editor_property("import_morph_targets", True)

        task = unreal.AssetImportTask()
        task.set_editor_property("filename", str(SOURCE_FBX))
        task.set_editor_property("destination_path", DESTINATION)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", False)
        task.set_editor_property("replace_existing_settings", False)
        task.set_editor_property("save", True)
        task.set_editor_property("options", options)

        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

        imported_paths = list(task.get_editor_property("imported_object_paths"))
        if not imported_paths:
            raise RuntimeError("Unreal reported no imported object paths")

        destination_assets = list(
            unreal.EditorAssetLibrary.list_assets(
                DESTINATION, recursive=True, include_folder=False
            )
        )
        if not destination_assets:
            raise RuntimeError("Import returned paths but destination contains no assets")

        lines.append(f"PASS: Imported object paths ({len(imported_paths)})")
        lines.extend(f"IMPORTED: {path}" for path in imported_paths)
        lines.append(f"PASS: Destination assets ({len(destination_assets)})")
        lines.extend(f"ASSET: {path}" for path in destination_assets)
        lines.extend(
            [
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
    unreal.log("MAE disposable FBX import result: " + lines[-2])
    unreal.log("MAE result file: " + str(RESULT_PATH))


run_import()
