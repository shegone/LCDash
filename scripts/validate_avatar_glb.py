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
import re
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
CHUNK_BIN = 0x004E4942  # 'BIN\0'


class GlbError(Exception):
    """The file is not a GLB this script can read."""


def read_glb_chunks(path: Path) -> tuple[dict, bytes]:
    """Pull the JSON chunk AND the binary buffer chunk out of a GLB.

    The JSON-only checks below only need the schema, but the morph-payload
    checks need the actual bytes: morph target position deltas live in the
    BIN chunk, uncompressed, even when the base mesh attributes are Draco
    compressed (verified by direct measurement on real exports -- Draco only
    ever touches the primitive's own attributes, never morph targets).
    """

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
    gltf: dict | None = None
    bin_chunk = b""
    while offset + 8 <= len(raw):
        chunk_length, chunk_type = struct.unpack_from("<II", raw, offset)
        body = raw[offset + 8: offset + 8 + chunk_length]
        if chunk_type == CHUNK_JSON:
            gltf = json.loads(body.decode("utf-8"))
        elif chunk_type == CHUNK_BIN:
            bin_chunk = body
        offset += 8 + chunk_length + (-chunk_length % 4)
    if gltf is None:
        raise GlbError("no JSON chunk found in the GLB container")
    return gltf, bin_chunk


def read_glb_json(path: Path) -> dict:
    """Pull the JSON chunk out of a binary glTF container."""

    return read_glb_chunks(path)[0]


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


# --- morph target PAYLOAD checks --------------------------------------
#
# Everything above only looks at the glTF *schema*: did the exporter declare
# the right names. That is not enough -- Blender 4.4+ will happily bake an
# armature/mesh action whose ``.action`` is set but whose ``.action_slot``
# is not, and the result is a morph target that is present, correctly
# named, and pointing at a real POSITION accessor, but that moved zero (or
# duplicate) vertices when it was baked. There is no error and no warning.
# This has shipped to production once already. Catching it means reading
# the actual per-vertex deltas, not just the names.
#
# The float32 noise floor measured on a genuinely dead morph target in the
# shipped file that triggered this check was 2.5e-7 m (0.25 microns) --
# that is rounding error propagating through the bake, not motion. The
# smallest genuinely-baked shape measured on the same file was ~1.1mm, and
# even viseme_sil -- near-rest by design, see the exemption discussion
# below -- still measured 1.9mm on the mesh where it WAS baked correctly.
# 50 microns (5e-5 m) sits about 200x above the noise floor and about 20x
# below the smallest real shape seen, with nothing observed anywhere near
# either side of that gap. Wide margin on both sides means this does not
# need per-mesh or per-viseme tuning.
MORPH_DEGENERATE_THRESHOLD_M = 5e-5

# Duplicate detection: the observed failure is two shapes baked from the
# same source pose under two different names, matching to float32
# precision -- a max per-vertex difference of exactly 0. Two genuinely
# different visemes on the same mesh move different vertices by different
# amounts; even the closest real pair is not remotely close to the 50
# micron noise-floor margin used for the degenerate check above, so that
# same threshold (and the same rationale) is reused here rather than
# inventing a second unjustified number.
#
# This was checked directly against the shipped defective file, not just
# reasoned about: on char:mouthShape, ALL 13-choose-2 = 78 pairs of live
# visemes were measured. Three pairs cluster at 0/14/19 microns max
# per-vertex difference (viseme_e==viseme_i bit-identical; viseme_a/
# viseme_k and viseme_E/viseme_r a few microns apart, each moving the
# exact same 6417 vertices with cosine similarity > 0.999999998). Every
# other pair -- genuinely distinct visemes -- is at least 319 microns
# apart, 16x further than the closest of those three and >6000x the
# duplicate threshold. That is a clean, isolated cluster with nothing
# near either edge of the threshold, so 50 microns is not "too loose": a
# prior bit-exact-only check on this file found just the one identical
# pair and missed the other two, which this check catches correctly.
MORPH_DUPLICATE_MAX_DIFF_M = 5e-5


def _read_morph_delta(
    gltf: dict, bin_chunk: bytes, accessor_index: int | None
) -> list[tuple[float, float, float]] | None:
    """Read a morph target POSITION delta accessor as raw float32 VEC3s.

    Returns None -- deliberately distinct from an empty list -- whenever the
    data cannot be trusted: no accessor, a sparse accessor (valid glTF, but
    reading it as dense would silently read zeros where real sparse values
    belong), a non-embedded buffer, or bytes that do not reach as far as the
    accessor claims. Refusing beats guessing here.
    """

    if accessor_index is None:
        return None
    accessors = gltf.get("accessors", [])
    if accessor_index >= len(accessors):
        return None
    accessor = accessors[accessor_index]
    if accessor.get("sparse"):
        return None
    if accessor.get("type") != "VEC3" or accessor.get("componentType") != 5126:
        return None
    view_index = accessor.get("bufferView")
    if view_index is None:
        return None
    views = gltf.get("bufferViews", [])
    if view_index >= len(views):
        return None
    view = views[view_index]
    if view.get("buffer", 0) != 0:
        return None  # only the single embedded GLB buffer is supported here
    count = accessor.get("count", 0)
    byte_offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride") or 12
    values: list[tuple[float, float, float]] = []
    for i in range(count):
        base = byte_offset + i * stride
        if base + 12 > len(bin_chunk):
            return None
        values.append(struct.unpack_from("<fff", bin_chunk, base))
    return values


def _mesh_morph_deltas(
    gltf: dict, bin_chunk: bytes
) -> dict[str, dict[str, list[tuple[float, float, float]] | None]]:
    """Per-mesh, per-morph raw POSITION delta arrays, keyed by mesh then name.

    Keyed by mesh ``name`` (falling back to its index) because the known
    defect is per-mesh: the same viseme baked fine on one mesh and dead on
    another in the same file, so "a morph is dead" without saying which
    mesh is not actionable.
    """

    result: dict[str, dict[str, list[tuple[float, float, float]] | None]] = {}
    for mesh_index, mesh in enumerate(gltf.get("meshes", [])):
        mesh_name = mesh.get("name") or f"mesh[{mesh_index}]"
        extras = mesh.get("extras") or {}
        target_names = extras.get("targetNames") or []
        per_morph: dict[str, list[tuple[float, float, float]] | None] = {}
        for primitive in mesh.get("primitives", []):
            for i, target in enumerate(primitive.get("targets") or []):
                if i >= len(target_names):
                    continue
                name = target_names[i]
                if name in per_morph:
                    continue  # first primitive wins; exports here are single-primitive
                per_morph[name] = _read_morph_delta(gltf, bin_chunk, target.get("POSITION"))
        if per_morph:
            result[mesh_name] = per_morph
    return result


def _max_displacement(deltas: list[tuple[float, float, float]]) -> float:
    if not deltas:
        return 0.0
    return max((x * x + y * y + z * z) ** 0.5 for x, y, z in deltas)


def _morph_family(name: str) -> str:
    """Which group of morphs ``name`` belongs to, for judging "did this
    mesh plausibly participate in this KIND of deformation at all".

    ``viseme_*`` names share the explicit ``viseme`` family -- Polly's
    codes are one coherent set of mouth shapes, baked together, so if a
    mesh moves for some of them it should move (even if only slightly) for
    all of them. ARKit-style camelCase names (``jawOpen``, ``mouthClose``,
    ``eyeBlinkLeft``) are grouped by their lowercase region prefix, which
    is exactly ARKit's own naming convention (jaw/mouth/eye/brow/cheek/
    nose/tongue) and needs no extra table to encode.

    Anything left over -- notably ``blink``, a bare lowercase word with no
    ARKit or viseme sibling -- becomes its own singleton family. That is
    not a special case for ``blink`` by name; it falls out of the same
    regex for any name that doesn't share a prefix with anything else on
    the mesh. See the caller for why singleton families are never flagged.
    """

    if name.startswith("viseme_"):
        return "viseme"
    match = re.match(r"^[a-z]+", name)
    return match.group(0) if match else name


def check_degenerate_and_duplicate_morphs(gltf: dict, bin_chunk: bytes) -> list[str]:
    """Flag morph targets that were declared correctly but never really baked.

    Two independent problems, both per-mesh:

    1. DEGENERATE -- a morph whose largest per-vertex displacement sits at
       the float32 noise floor, i.e. it moves nothing. This is what an
       action baked without its action_slot looks like.
    2. DUPLICATE -- two differently-named morphs on the same mesh whose
       deltas are identical or near-identical, i.e. one shape got baked
       twice under two names (also an action_slot symptom: the second
       target reused the last real evaluation instead of its own).

    DEGENERATE is judged PER FAMILY (see ``_morph_family``), not per morph,
    and this matters a lot in practice. Eyeballs, eyelashes and eyebrows
    legitimately do not deform for any mouth viseme -- that is not a bake
    defect, it is a mesh that simply does not participate in that kind of
    motion. The naive rule "flag a dead morph if some OTHER morph on the
    mesh is alive" was tried and rejected: on a real shipped file,
    char:eyebrowsShape has ``blink`` genuinely alive (it measurably moves
    for eye blinks) while all 17 visemes are uniformly dead, and that naive
    rule would flag all 17 visemes as broken just because blink happens to
    live on the same mesh -- exactly the false positive this exists to
    avoid. Comparing within a family instead of across the whole mesh only
    flags a dead morph when a SIBLING in the same family (e.g. another
    viseme) is alive on that same mesh, which is the actual signal that the
    mesh participates in that kind of deformation and dropped one shape.
    A family with only one member (blink, on a mesh with no other
    lowercase-prefixed morphs) has no sibling to compare against, so it is
    never flagged by this check either way -- this is what lets ``blink``
    be legitimately dead on one mesh (the inner mouth, plausibly, does not
    move for a blink) and legitimately alive on another (the head) without
    naming it anywhere in this code. The tradeoff is that a truly singleton
    shape (ARKit has exactly one: ``tongueOut``) that silently fails to
    bake would not be caught by this check alone -- there is no sibling
    signal available to catch it with, short of hard-coding expectations
    per shape name, which this deliberately does not do.

    A mesh where EVERY measured morph is dead gets an informational note,
    not a problem: that is indistinguishable from "this mesh does not
    deform at all" (a static prop, an eyeball, ...), which is normal, not
    a defect.

    ``viseme_sil`` is deliberately NOT given a family-of-one exemption
    either. Within the ``viseme`` family it is treated exactly like every
    other viseme: on a mesh where the family is genuinely alive, a dead
    viseme_sil is flagged just like a dead viseme_S would be. It is
    legitimately small on some meshes (near-rest by design), but the
    measured real value (1.9mm) is comfortably above
    MORPH_DEGENERATE_THRESHOLD_M (50 microns) while a genuinely dead
    viseme_sil measured 0.25 microns -- the same 200x/20x margin that
    makes the threshold safe for every other shape applies to it too.
    viseme_sil was in fact one of the dead shapes in the shipped defect
    that motivated this check, so exempting it would have hidden exactly
    the bug this exists to catch.
    """

    problems: list[str] = []
    for mesh_name, morphs in sorted(_mesh_morph_deltas(gltf, bin_chunk).items()):
        displacements: dict[str, float] = {}
        for name, deltas in morphs.items():
            if deltas is None:
                continue  # unreadable (sparse, etc.) -- nothing defensible to say
            displacements[name] = _max_displacement(deltas)

        if displacements and all(
            d < MORPH_DEGENERATE_THRESHOLD_M for d in displacements.values()
        ):
            print(
                f"note: mesh '{mesh_name}' has no live morphs at all -- all "
                f"{len(displacements)} measured under "
                f"{MORPH_DEGENERATE_THRESHOLD_M * 1000:.3f} mm. Likely a mesh "
                "that does not participate in facial deformation (eyeballs, "
                "a static prop, ...), not a bake failure -- flagging "
                "individual shapes below would be pure noise, since there is "
                "nothing alive on this mesh to compare them against."
            )

        families: dict[str, list[str]] = {}
        for name in displacements:
            families.setdefault(_morph_family(name), []).append(name)

        dead: list[str] = []
        for members in families.values():
            if len(members) < 2:
                continue  # no sibling in this family -- nothing to compare against
            alive_members = [m for m in members if displacements[m] >= MORPH_DEGENERATE_THRESHOLD_M]
            dead_members = [m for m in members if displacements[m] < MORPH_DEGENERATE_THRESHOLD_M]
            if alive_members and dead_members:
                # a sibling in this family moves, so the mesh clearly
                # participates in this kind of deformation -- a dead one
                # among them is the bake dropping a shape, not "this mesh
                # doesn't do that"
                dead.extend(dead_members)
        dead.sort()
        if dead:
            problems.append(
                f"mesh '{mesh_name}': morph target(s) move essentially no vertices "
                f"(under {MORPH_DEGENERATE_THRESHOLD_M * 1000:.3f} mm -- the float32 "
                "noise floor, not a real shape) while sibling shapes on the same "
                "mesh move normally -- check Blender's action_slot on the bake -- "
                + ", ".join(dead)
            )

        names = sorted(displacements)
        duplicates: list[str] = []
        for a_idx, name_a in enumerate(names):
            deltas_a = morphs[name_a]
            for name_b in names[a_idx + 1:]:
                deltas_b = morphs[name_b]
                if len(deltas_a) != len(deltas_b):
                    continue
                if (displacements[name_a] < MORPH_DEGENERATE_THRESHOLD_M
                        and displacements[name_b] < MORPH_DEGENERATE_THRESHOLD_M):
                    continue  # both already reported as dead above; don't double-flag
                if not deltas_a:
                    continue
                max_diff = max(
                    ((xa - xb) ** 2 + (ya - yb) ** 2 + (za - zb) ** 2) ** 0.5
                    for (xa, ya, za), (xb, yb, zb) in zip(deltas_a, deltas_b)
                )
                if max_diff < MORPH_DUPLICATE_MAX_DIFF_M:
                    duplicates.append(f"{name_a} == {name_b}")
        if duplicates:
            problems.append(
                f"mesh '{mesh_name}': morph targets are identical or near-identical "
                "(one shape baked twice under two names) -- " + "; ".join(duplicates)
            )

    return problems


def validate(path: Path) -> list[str]:
    """Return a list of problems; empty means the export is usable."""

    problems: list[str] = []
    gltf, bin_chunk = read_glb_chunks(path)

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

    if names:
        problems.extend(check_degenerate_and_duplicate_morphs(gltf, bin_chunk))

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
