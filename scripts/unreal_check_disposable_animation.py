r"""Read-only key-change analysis for the disposable imported animation."""

from pathlib import Path
import traceback

import unreal


ANIMATION_PATH = (
    "/Game/MAE_Test/DisposableFBX_20260801/"
    "MAE_iClone_FBX_Test_Unreal_Anim.MAE_iClone_FBX_Test_Unreal_Anim"
)
RESULT_PATH = Path(
    r"C:\project mae share\MAE Progress Handoffs"
    r"\MAE_Unreal_Animation_Check.result.txt"
)
EPSILON = 0.00001


def vector_changed(first, value):
    return any(
        abs(getattr(value, component) - getattr(first, component)) > EPSILON
        for component in ("x", "y", "z")
    )


def quaternion_changed(first, value):
    return any(
        abs(getattr(value, component) - getattr(first, component)) > EPSILON
        for component in ("x", "y", "z", "w")
    )


def values_change(values, comparator):
    if len(values) < 2:
        return False
    first = values[0]
    return any(comparator(first, value) for value in values[1:])


def run():
    lines = ["MAE Unreal disposable animation key check", "Scope: read-only"]
    try:
        animation = unreal.load_asset(ANIMATION_PATH)
        if animation is None:
            raise RuntimeError(f"Animation asset not found: {ANIMATION_PATH}")

        track_names = list(unreal.AnimationLibrary.get_animation_track_names(animation))
        lines.extend(
            [
                f"Frames: {unreal.AnimationLibrary.get_num_frames(animation)}",
                f"Keys: {unreal.AnimationLibrary.get_num_keys(animation)}",
                f"LengthSeconds: {unreal.AnimationLibrary.get_sequence_length(animation)}",
                f"BoneTracks: {len(track_names)}",
            ]
        )

        changed_tracks = []
        static_tracks = []
        for track_name in track_names:
            positions, rotations, scales = unreal.AnimationLibrary.get_raw_track_data(
                animation, track_name
            )
            changed = (
                values_change(positions, vector_changed)
                or values_change(rotations, quaternion_changed)
                or values_change(scales, vector_changed)
            )
            if changed:
                changed_tracks.append(str(track_name))
            else:
                static_tracks.append(str(track_name))

        lines.append(f"ChangingTracks: {len(changed_tracks)}")
        lines.append(f"StaticTracks: {len(static_tracks)}")
        lines.extend(f"CHANGING: {name}" for name in changed_tracks[:40])
        if changed_tracks:
            lines.append("RESULT: MOTION_PRESENT")
        else:
            lines.append("RESULT: STATIC_POSE_ONLY")
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
