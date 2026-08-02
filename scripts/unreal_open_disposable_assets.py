r"""Open the disposable skeletal mesh and animation for visual inspection."""

from pathlib import Path
import traceback

import unreal


MESH_PATH = (
    "/Game/MAE_Test/DisposableFBX_20260801/"
    "MAE_iClone_FBX_Test_Unreal.MAE_iClone_FBX_Test_Unreal"
)
ANIMATION_PATH = (
    "/Game/MAE_Test/DisposableFBX_20260801/"
    "MAE_iClone_FBX_Test_Unreal_Anim.MAE_iClone_FBX_Test_Unreal_Anim"
)
RESULT_PATH = Path(
    r"C:\project mae share\MAE Progress Handoffs"
    r"\MAE_Unreal_Open_Assets.result.txt"
)


def run():
    lines = ["MAE Unreal asset inspection opener"]
    try:
        assets = []
        for label, asset_path in (("Mesh", MESH_PATH), ("Animation", ANIMATION_PATH)):
            asset = unreal.load_asset(asset_path)
            if asset is None:
                raise RuntimeError(f"{label} asset not found: {asset_path}")
            assets.append(asset)
            lines.append(f"PASS: Loaded {label}: {asset_path}")

        subsystem = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)
        subsystem.open_editor_for_assets(assets)
        lines.extend(["PASS: Asset editors opened", "RESULT: PASS"])
    except Exception as exc:
        lines.extend(
            [
                f"FAIL: {type(exc).__name__}: {exc}",
                traceback.format_exc().rstrip(),
                "RESULT: FAIL",
            ]
        )

    RESULT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


run()
