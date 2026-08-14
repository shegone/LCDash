#!/usr/bin/env python3
"""Check a MAE character export before it goes anywhere near the app.

    python scripts/validate_avatar_glb.py path/to/mae.glb

Answers the three questions that decide whether an export is usable, and
that are otherwise only discoverable by shipping it and squinting at a
browser console:

1. Are the ARKit blendshapes there, spelled the way the runtime drives
   them? The runtime looks morph targets up BY NAME, so ``jawOpen`` works
   and ``Jaw_Open`` silently does nothing -- a beautiful character whose
   face never moves. Character Creator can export the right shapes under
   the wrong names depending on the profile chosen at export time.
2. Is it human-scaled and the right way up? The camera framings assume
   metres, Y-up, feet near the origin, ~1.6-1.7 m tall. A model authored
   in centimetres fills the pane with an ankle.
3. Is it small enough to ship? The file goes inside the container image
   and is downloaded by every browser that opens the page, including a
   booth laptop on hotel wifi.

Pure standard library on purpose: this has to run on the workstation doing
the export, which has Character Creator and Blender but no project
virtualenv.

Exit status is 0 when the export is usable, 1 when it is not.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

# The 52 ARKit shapes, spelled as the runtime and Audio2Face expect.
ARKIT_52 = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft",
    "mouthStretchRight", "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper", "mouthPressLeft", "mouthPressRight",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
)

# What the browser runtime actually drives today. Everything else is for
# Audio2Face later, so a missing one of these is fatal rather than untidy.
REQUIRED_NOW = (
    "jawOpen", "mouthClose", "mouthFunnel", "mouthPucker",
    "mouthSmileLeft", "mouthSmileRight", "mouthPressLeft", "mouthPressRight",
    "mouthRollLower", "tongueOut", "eyeBlinkLeft", "eyeBlinkRight",
    "browInnerUp",
)

# The SECOND rig contract the runtime accepts: one morph per Polly viseme
# code plus a combined blink, as produced by baking a Sumerian Host's
# bone-driven viseme poses into shape keys (scripts/bake_sumerian_host.py
# in the session history; the pose clips in the Hosts' lipsync.glb are
# literally named with Polly's codes). A model honouring this contract is
# validated against it INSTEAD of ARKit.
POLLY_VISEME_CODES = (
    "p", "t", "S", "T", "f", "k", "i", "r", "s", "u",
    "@", "a", "e", "E", "o", "O", "sil",
)
REQUIRED_NATIVE_VISEME = tuple(
    f"viseme_{code}" for code in POLLY_VISEME_CODES
) + ("blink",)

MAX_RECOMMENDED_BYTES = 30 * 1024 * 1024
GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A


class GlbError(Exception):
    """The file is not a GLB this script can read."""


def read_glb_json(path: Path) -> dict:
    """Pull the JSON chunk out of a binary glTF container."""

    raw = path.read_bytes()
    if len(raw) < 12:
        raise GlbError("file is too short to be a GLB")
    magic, version, _length = struct.unpack_from("<III", raw, 0)
    if magic != GLB_MAGIC:
        raise GlbError(
            "not a binary glTF. A '.gltf' text file plus loose buffers and "
            "textures will not work -- re-export as GLB (binary, embedded)."
        )
    if version != 2:
        raise GlbError(f"glTF major version {version}; the runtime expects 2")

    offset = 12
    while offset + 8 <= len(raw):
        chunk_length, chunk_type = struct.unpack_from("<II", raw, offset)
        body = raw[offset + 8: offset + 8 + chunk_length]
        if chunk_type == CHUNK_JSON:
            return json.loads(body.decode("utf-8"))
        offset += 8 + chunk_length + (-chunk_length % 4)
    raise GlbError("no JSON chunk found in the GLB container")


def morph_target_names(gltf: dict) -> set[str]:
    """Every morph target name the file declares.

    glTF keeps morph target names in ``mesh.extras.targetNames`` -- they are
    metadata, not part of the core schema, and some exporters drop them. A
    file with morph targets but no names is unusable here even though it
    opens fine in a viewer, so that case is reported distinctly.
    """

    names: set[str] = set()
    for mesh in gltf.get("meshes", []):
        extras = mesh.get("extras") or {}
        for name in extras.get("targetNames") or []:
            if isinstance(name, str):
                names.add(name)
    return names


def has_unnamed_targets(gltf: dict) -> bool:
    for mesh in gltf.get("meshes", []):
        extras = mesh.get("extras") or {}
        named = len(extras.get("targetNames") or [])
        for primitive in mesh.get("primitives", []):
            if len(primitive.get("targets") or []) > named:
                return True
    return False


def estimated_height(gltf: dict) -> float | None:
    """Model height in file units, from POSITION accessor bounds.

    Accessor min/max are mandatory for POSITION, so this needs no buffer
    decoding. It ignores node transforms, which is fine for the question
    being asked: metres versus centimetres is a factor of 100.
    """

    accessors = gltf.get("accessors", [])
    lo = None
    hi = None
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            index = (primitive.get("attributes") or {}).get("POSITION")
            if index is None or index >= len(accessors):
                continue
            accessor = accessors[index]
            amin, amax = accessor.get("min"), accessor.get("max")
            if not amin or not amax or len(amin) < 2 or len(amax) < 2:
                continue
            lo = amin[1] if lo is None else min(lo, amin[1])
            hi = amax[1] if hi is None else max(hi, amax[1])
    if lo is None or hi is None:
        return None
    return hi - lo


def validate(path: Path) -> list[str]:
    """Return a list of problems; empty means the export is usable."""

    problems: list[str] = []
    gltf = read_glb_json(path)

    names = morph_target_names(gltf)
    if not names:
        if has_unnamed_targets(gltf):
            problems.append(
                "morph targets exist but carry NO NAMES (mesh.extras.targetNames "
                "is missing). The runtime drives shapes by name, so this file "
                "cannot animate. Re-export with an exporter that preserves "
                "blendshape names."
            )
        else:
            problems.append(
                "no morph targets at all -- the export had blendshapes turned "
                "off. In Character Creator, export with the ARKit/ExpressionPlus "
                "facial profile enabled."
            )
    elif "viseme_sil" in names or "viseme_p" in names:
        missing_native = [n for n in REQUIRED_NATIVE_VISEME if n not in names]
        if missing_native:
            problems.append(
                "native-viseme model is missing morphs the runtime drives: "
                + ", ".join(missing_native)
                + ". Each Polly viseme code needs its morph, or that sound "
                "will not move her mouth."
            )
    else:
        missing_now = [n for n in REQUIRED_NOW if n not in names]
        if missing_now:
            problems.append(
                "missing ARKit shapes the runtime drives today: "
                + ", ".join(missing_now)
                + ". These exact spellings are required; a renamed equivalent "
                "will load and silently never move."
            )
        missing_all = [n for n in ARKIT_52 if n not in names]
        if missing_all and not missing_now:
            print(
                f"note: {len(missing_all)} of the 52 ARKit shapes are absent. "
                "The browser runtime does not use them, but Audio2Face will "
                f"later. Missing: {', '.join(missing_all[:8])}"
                + (" ..." if len(missing_all) > 8 else "")
            )

    height = estimated_height(gltf)
    if height is None:
        print("note: no POSITION bounds found; scale could not be checked.")
    elif not 1.2 <= height <= 2.2:
        hint = " (looks like centimetres -- scale by 0.01)" if height > 50 else ""
        problems.append(
            f"model is {height:.2f} units tall; expected roughly 1.6-1.7 "
            f"(metres, Y-up){hint}."
        )

    size = path.stat().st_size
    if size > MAX_RECOMMENDED_BYTES:
        problems.append(
            f"file is {size / 1024 / 1024:.1f} MB, over the ~30 MB guideline. "
            "It ships inside the container image and downloads to every "
            "browser; reduce texture size or decimate."
        )

    print(f"file:            {path}")
    print(f"size:            {size / 1024 / 1024:.1f} MB")
    print(f"morph targets:   {len(names)} named"
          + (f" ({len(ARKIT_52) - len([n for n in ARKIT_52 if n not in names])}/52 ARKit)" if names else ""))
    print(f"height:          {'unknown' if height is None else f'{height:.2f} units'}")
    print(f"meshes:          {len(gltf.get('meshes', []))}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    path = Path(argv[1])
    if not path.is_file():
        print(f"no such file: {path}")
        return 1
    try:
        problems = validate(path)
    except GlbError as exc:
        print(f"\nUNUSABLE: {exc}")
        return 1

    if problems:
        print("\nUNUSABLE -- fix before dropping into static/models/mae.glb:")
        for problem in problems:
            print(f"  * {problem}")
        return 1
    print("\nOK: this export is usable. Copy it to static/models/mae.glb.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
