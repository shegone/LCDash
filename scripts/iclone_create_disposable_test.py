r"""Create a disposable iClone project for the PC15 FBX pipeline test.

Run this file from iClone 8 with Script > Load Python. It intentionally writes
only to C:\MAE-Agent\tests and does not export FBX or interact with Unreal.
"""

from datetime import datetime
from pathlib import Path
import traceback

import RLPy


TEST_DIR = Path(r"C:\MAE-Agent\tests")
PROJECT_PATH = TEST_DIR / "MAE_iClone_FBX_Test.iProject"
RESULT_PATH = Path(__file__).resolve().with_name("MAE_iClone_FBX_Test.result.txt")
AVATAR_PATH = Path(
    r"C:\Users\Public\Documents\Reallusion\Reallusion Templates"
    r"\Actor\Character\Base\Neutral_F.ccAvatar"
)
MOTION_PATH = Path(
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


def run_test():
    lines = [
        "MAE iClone disposable project test",
        "Started: " + datetime.now().isoformat(timespec="seconds"),
        "Scope: local iClone project creation only; no FBX export or Unreal changes",
    ]

    try:
        TEST_DIR.mkdir(parents=True, exist_ok=True)

        for label, source_path in (("Avatar", AVATAR_PATH), ("Motion", MOTION_PATH)):
            if not source_path.is_file():
                raise FileNotFoundError(f"{label} source not found: {source_path}")
            lines.append(f"PASS: {label} source exists")

        existing_avatars = RLPy.RScene.GetAvatars()
        if existing_avatars:
            avatar = existing_avatars[-1]
            lines.append(
                f"PASS: Reusing avatar from the prior stopped test "
                f"({type(avatar).__name__})"
            )
        else:
            avatar = RLPy.RFileIO.LoadObject(str(AVATAR_PATH), True)
            if avatar is None:
                raise RuntimeError("RLPy.RFileIO.LoadObject returned no avatar")
            lines.append(f"PASS: Avatar loaded ({type(avatar).__name__})")

        # iClone 8.74's installed binding does not accept a numeric RTime
        # constructor or expose SetValue, despite older API examples. Use the
        # application's own timeline-start object for version compatibility.
        start_time = RLPy.RGlobal.GetStartTime()
        lines.append(f"PASS: Timeline start acquired ({type(start_time).__name__})")

        motion_status = RLPy.RFileIO.LoadMotion(
            str(MOTION_PATH), start_time, avatar
        )
        if not status_succeeded(motion_status):
            raise RuntimeError(f"LoadMotion failed with status: {motion_status}")
        lines.append(f"PASS: Motion loaded (status={motion_status})")

        save_status = RLPy.RFileIO.SaveProject(str(PROJECT_PATH))
        if not status_succeeded(save_status):
            raise RuntimeError(f"SaveProject failed with status: {save_status}")
        lines.append(f"PASS: Project save requested (status={save_status})")

        if not PROJECT_PATH.is_file():
            raise RuntimeError("SaveProject returned success but the project file is absent")

        lines.extend(
            [
                f"PASS: Project exists ({PROJECT_PATH.stat().st_size} bytes)",
                f"Project: {PROJECT_PATH}",
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
        "MAE Disposable Project Test",
        outcome + "<br><br>Result file:<br>" + str(RESULT_PATH),
        RLPy.EMsgButton_Ok,
    )


def run_script():
    """Entry point required by iClone's Script > Load Python command."""
    run_test()
