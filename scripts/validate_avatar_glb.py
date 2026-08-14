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
import math
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
# the right names. That does not prove a declared, correctly named, correctly
# pointed-at morph target actually carries the pose it claims to -- for that,
# the file's own per-vertex deltas have to be read and compared.
#
# CALIBRATION NOTE, learned the hard way: a morph that moves nothing, or
# that is identical to a sibling, is NOT automatically a bake defect. A mesh
# skinned to very few bones -- e.g. a real shipped inner-mouth mesh, weighted
# only to the jaw, two tongue bones, and a trace of neck, with NO lip bone --
# will legitimately collapse many visually distinct facial poses into
# identical or zero deformation, because the bones that would distinguish
# those poses simply do not influence that mesh. Two visemes sharing the
# same jaw angle produce the same inner-mouth shape; a closed-jaw sound
# produces zero inner-mouth motion. That is correct anatomy transcribed
# faithfully from the source rig, not a defect. An earlier version of this
# check did not account for this and would have failed a genuinely correct
# export -- see ``_mesh_skin_joint_counts`` and the design note in
# ``check_degenerate_and_duplicate_morphs`` for how that is told apart from
# a real defect below.

# The float32 noise floor measured on a genuinely dead morph target in a
# real export was 2.5e-7 m (0.25 microns) -- that is rounding error
# propagating through the bake, not motion. The smallest genuinely-baked
# shape measured on a richly-skinned mesh (a real head mesh, 64 skin
# joints, one per lip/cheek/jaw bone) was ~1.1mm, and even viseme_sil --
# near-rest by design -- still measured 1.9mm there. 50 microns (5e-5 m)
# sits about 200x above the noise floor and about 20x below the smallest
# real shape seen on a richly-skinned mesh, with nothing observed anywhere
# near either side of that gap. This threshold only answers "did this
# shape move at all" -- whether zero movement is EXPECTED for a given mesh
# is a separate, richness-aware question, handled in the checker below.
MORPH_DEGENERATE_THRESHOLD_M = 5e-5

# Duplicate detection: bit-identical (max per-vertex difference of exactly
# 0) is the strongest signal, but a looser match also matters -- two poses
# driven by the same bone angle produce the same delta up to floating-point
# noise without being bit-identical.
#
# Measured directly on a real file: on a richly-skinned mesh (64 joints,
# one per lip/cheek/jaw bone), every pair of distinct live visemes differs
# by at least 319 microns. On a mesh skinned to only 4 bones (jaw +
# tongue), several pairs cluster at 0-19 microns apart -- traced back to
# jaw poses themselves within a couple hundredths of a degree of each
# other in the source rig, so the mesh faithfully reproduces near-identical
# deltas for near-identical poses. 50 microns sits in the middle of that
# gap: 16x below the smallest genuinely-distinct-pose difference measured,
# and 16x above the largest same-pose difference measured. So it reliably
# separates "these are actually the same shape" from "these are different
# shapes" -- but, per the calibration note above, whether an identical pair
# on a given mesh is a DEFECT or expected anatomy still depends on that
# mesh's skinning, handled below.
MORPH_DUPLICATE_MAX_DIFF_M = 5e-5

# Which meshes get the DEGENERATE/DUPLICATE findings above treated as hard
# failures versus informational notes.
#
# CHOSEN DESIGN: measure, per mesh, how directionally diverse its own ALIVE
# morph deltas are -- the mean |cosine similarity| between every pair of
# alive morphs on that mesh, treating each morph's flattened per-vertex
# delta array as one vector. A mesh whose alive morphs point in nearly the
# same direction and differ mainly in magnitude is being driven along
# essentially one degree of freedom (e.g. jaw angle): every closed-mouth
# viseme comes out as "the same shape, scaled by how far the jaw opened",
# so identical or near-identical deltas among them are the direct,
# inevitable numerical consequence of that single shared driver -- not a
# defect. A mesh whose alive morphs point in many different directions is
# expressing many independent degrees of freedom, so an identical or dead
# pair among them is surprising and worth failing on. This needs no
# cross-mesh comparison or ranking: it is a property of one mesh's own
# morphs, so it generalises to a mesh in isolation, and to a new character.
#
# Measured directly on the two real files this fix was built against: a
# richly-detailed head mesh (64 driving bones in the source rig, full lip
# and cheek control) has mean |cosine similarity| 0.34 across its 18 alive
# morphs -- pointing in substantially different directions, i.e. many
# independent poses. An inner-mouth mesh (4 driving bones in the source
# rig -- jaw, two tongue bones, a trace of neck; no lip bone at all) has
# mean |cosine similarity| 0.92 across its 13 alive morphs -- essentially
# collinear, i.e. one dominant pose axis. FOLLOWER_COLLINEARITY_THRESHOLD
# sits at the midpoint of that 0.34-0.92 gap, giving comfortable margin on
# both sides without needing per-character tuning.
#
# Two other designs were tried first and rejected:
#   - Skin joint count, read from the glTF skin object (the first thing
#     tried, because the source rig genuinely has exactly this signal --
#     4 joints vs 64, measured directly in Blender). It turned out NOT to
#     be recoverable from the delivered GLB: the face meshes in this
#     export pipeline carry no JOINTS_0/WEIGHTS_0 attributes and reference
#     no skin at all (only body meshes -- clothing, hair -- are vertex-
#     skinned; the face is deformed entirely by pre-baked morph targets,
#     with the rig staying behind in Blender). "The validator can see
#     joints/skin weights in the glTF" is true of the SOURCE rig but not
#     of this delivered file, so a signal that is actually present in
#     every GLB this validator will ever see was needed instead.
#   - Ranking meshes by raw alive-morph count ("richest = most live
#     morphs"), i.e. treating only the top mesh (or a count-based ratio of
#     it) as strict: measured and rejected. The inner mouth has 13 of 18
#     morphs alive (72%) -- not obviously "few" -- so a count or fraction
#     cutoff either fails to separate it from a genuinely rich mesh, or
#     needs a suspiciously fine-tuned number to do so. The actual
#     anatomical fact is not that the mouth moves rarely, it is that it
#     moves the SAME WAY for many different sounds; a count metric cannot
#     see that distinction, a direction metric does directly.
FOLLOWER_COLLINEARITY_THRESHOLD = 0.6


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


def _mean_alive_cosine_similarity(
    morphs: dict[str, list[tuple[float, float, float]] | None],
    displacements: dict[str, float],
) -> float | None:
    """Mean |cosine similarity| between every pair of this mesh's ALIVE
    morph deltas, each flattened into one long vector.

    This is the richness signal ``check_degenerate_and_duplicate_morphs``
    uses to tell "this mesh legitimately collapses many poses" apart from
    "this mesh's bake actually dropped or duplicated a shape" -- see the
    design note above FOLLOWER_COLLINEARITY_THRESHOLD for the reasoning
    and the two designs (skin joints; alive-morph count) that were tried
    and rejected first.

    Returns None when fewer than two morphs are alive, or when every alive
    morph happens to have zero norm (shouldn't happen given the caller's
    own threshold, but division by zero is refused rather than guessed
    past) -- there is nothing to compare, so nothing can be said.
    """

    alive = [n for n, d in displacements.items() if d >= MORPH_DEGENERATE_THRESHOLD_M]
    if len(alive) < 2:
        return None

    norms = {}
    for name in alive:
        deltas = morphs[name]
        norms[name] = math.sqrt(sum(x * x + y * y + z * z for x, y, z in deltas))

    similarities: list[float] = []
    for i, name_a in enumerate(alive):
        norm_a = norms[name_a]
        if norm_a == 0:
            continue
        deltas_a = morphs[name_a]
        for name_b in alive[i + 1:]:
            norm_b = norms[name_b]
            if norm_b == 0:
                continue
            deltas_b = morphs[name_b]
            if len(deltas_a) != len(deltas_b):
                continue
            dot = sum(
                xa * xb + ya * yb + za * zb
                for (xa, ya, za), (xb, yb, zb) in zip(deltas_a, deltas_b)
            )
            similarities.append(abs(dot / (norm_a * norm_b)))

    if not similarities:
        return None
    return sum(similarities) / len(similarities)


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
    """Flag morph targets that carry no real (or no distinct) motion on a
    mesh that is diverse enough that the motion should be there.

    Two independent observations, both computed per-mesh:

    1. DEGENERATE -- a morph whose largest per-vertex displacement sits at
       the float32 noise floor, i.e. it moved nothing when measured.
    2. DUPLICATE -- two differently-named morphs on the same mesh whose
       measured deltas are identical or near-identical.

    Neither is a hard failure ("problem") purely on its own. Each is only
    reported as a failure on a mesh whose alive morphs point in diverse
    directions -- i.e. the mesh expresses more than one degree of freedom,
    so a dead or duplicate shape among them is surprising (see
    ``_mean_alive_cosine_similarity`` and FOLLOWER_COLLINEARITY_THRESHOLD
    above for the richness measurement and the designs that were rejected
    first). Everywhere else it is downgraded to an informational note
    (printed, not returned in the failure list).

    DEGENERATE is additionally judged PER FAMILY (see ``_morph_family``),
    not per morph. Eyeballs, eyelashes and eyebrows legitimately do not
    deform for any mouth viseme; a family that is uniformly dead on a mesh
    just means that mesh does not participate in that kind of motion at
    all, which is not by itself surprising (a head mesh's blink family
    being alive says nothing about whether its viseme family "should" be
    alive too). A dead morph only joins the reported list when a SIBLING
    in the same family is alive on that same mesh -- that is the actual
    signal that the mesh participates in this kind of deformation and
    dropped one shape. A family with only one member on a mesh (e.g.
    ``blink``, which shares no prefix with anything else) has no sibling
    to compare against, so it is never flagged either way -- this is what
    lets ``blink`` be legitimately dead on the inner mouth (plausibly does
    not move for a blink) and legitimately alive on the head, without
    naming either mesh or ``blink`` anywhere in this code.

    A mesh where EVERY measured morph is dead gets an informational note
    regardless of richness: that is indistinguishable from "this mesh does
    not deform at all" (a static prop, an eyeball, ...), which is normal,
    not a defect, and there is no sibling on the mesh to compare against
    in the first place.

    ``viseme_sil`` gets no name-based exemption anywhere in this function.
    On a directionally-diverse mesh it is judged exactly like any other
    viseme: dead alongside alive siblings is reported, same as a dead
    viseme_S would be. On a collinear/follower mesh (e.g. an inner mouth
    driven only by jaw angle) it may legitimately be dead, same as a dead
    viseme_S would be there too -- the richness/family logic already
    covers both, there is nothing viseme_sil-specific left to special-case.
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
            continue  # nothing left to usefully compare on this mesh

        # Richness gate. Fewer than two alive morphs (diversity is None)
        # means there is no evidence either way -- stays strict, the same
        # conservative default this check used before richness was taken
        # into account at all.
        diversity = _mean_alive_cosine_similarity(morphs, displacements)
        is_low_fidelity = (
            diversity is not None and diversity >= FOLLOWER_COLLINEARITY_THRESHOLD
        )

        def _report(message: str) -> None:
            if is_low_fidelity:
                print(
                    f"note (informational -- mesh '{mesh_name}'s alive morphs "
                    f"point in nearly the same direction, mean |cosine "
                    f"similarity| {diversity:.2f} >= "
                    f"{FOLLOWER_COLLINEARITY_THRESHOLD:.2f} -- consistent "
                    "with a mesh driven by one dominant degree of freedom "
                    "(e.g. jaw angle), where collapsed poses are expected): "
                    + message
                )
            else:
                problems.append(message)

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
                # among them dropped a shape, whether that is a defect (on
                # a diverse mesh) or just this particular follower mesh
                # legitimately not distinguishing these two poses
                dead.extend(dead_members)
        dead.sort()
        if dead:
            _report(
                f"mesh '{mesh_name}': morph target(s) measured essentially no "
                f"vertex movement (under {MORPH_DEGENERATE_THRESHOLD_M * 1000:.3f} mm "
                "-- the float32 noise floor) while sibling shapes on the same "
                "mesh move normally -- " + ", ".join(dead)
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
                    continue  # both already covered by the dead-family case above
                if not deltas_a:
                    continue
                max_diff = max(
                    ((xa - xb) ** 2 + (ya - yb) ** 2 + (za - zb) ** 2) ** 0.5
                    for (xa, ya, za), (xb, yb, zb) in zip(deltas_a, deltas_b)
                )
                if max_diff < MORPH_DUPLICATE_MAX_DIFF_M:
                    duplicates.append(f"{name_a} == {name_b}")
        if duplicates:
            _report(
                f"mesh '{mesh_name}': morph targets measured identical or "
                "near-identical vertex positions -- " + "; ".join(duplicates)
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
