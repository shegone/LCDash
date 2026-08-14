"""The export checker must fail the exports that actually go wrong.

A validator nobody trusts is worse than none, so each case here is a real
failure mode of a Character Creator export rather than a synthetic one:
blendshapes turned off, the right shapes under the wrong names, morph
targets whose names the exporter dropped, and a model authored in
centimetres.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.validate_avatar_glb import (
    ARKIT_52,
    GlbError,
    check_degenerate_and_duplicate_morphs,
    estimated_height,
    morph_target_names,
    read_glb_chunks,
    read_glb_json,
    validate,
)


def _glb(gltf: dict, pad_to: int = 0) -> bytes:
    body = json.dumps(gltf).encode("utf-8")
    body += b" " * (-len(body) % 4)
    chunk = struct.pack("<II", len(body), 0x4E4F534A) + body
    total = 12 + len(chunk)
    out = struct.pack("<III", 0x46546C67, 2, total) + chunk
    if pad_to > len(out):
        out += b"\0" * (pad_to - len(out))
    return out


def _mesh(target_names, primitive_targets=None, height=1.65):
    primitive = {
        "attributes": {"POSITION": 0},
        "targets": primitive_targets if primitive_targets is not None
        else [{} for _ in target_names],
    }
    return {
        "meshes": [{"extras": {"targetNames": list(target_names)},
                    "primitives": [primitive]}],
        "accessors": [{"min": [-0.3, 0.0, -0.2], "max": [0.3, height, 0.2]}],
    }


def _glb_with_bin(gltf: dict, bin_data: bytes = b"", pad_to: int = 0) -> bytes:
    """Same container as ``_glb`` but with a real BIN chunk attached.

    Morph target deltas live in the BIN chunk in real exports (uncompressed,
    even though the base mesh attributes are Draco-compressed), so the
    degenerate/duplicate morph checks need a GLB that actually carries one.
    """
    json_body = json.dumps(gltf).encode("utf-8")
    json_body += b" " * (-len(json_body) % 4)
    chunks = struct.pack("<II", len(json_body), 0x4E4F534A) + json_body

    if bin_data:
        bin_body = bin_data + b"\0" * (-len(bin_data) % 4)
        chunks += struct.pack("<II", len(bin_body), 0x004E4942) + bin_body

    total = 12 + len(chunks)
    out = struct.pack("<III", 0x46546C67, 2, total) + chunks
    if pad_to > len(out):
        out += b"\0" * (pad_to - len(out))
    return out


def _pack_vec3_list(vectors) -> bytes:
    return b"".join(struct.pack("<fff", *v) for v in vectors)


def _gltf_with_meshes(mesh_specs, height=1.65):
    """Build a multi-mesh glTF with real, uncompressed morph delta accessors.

    ``mesh_specs`` is a list of ``(mesh_name, morphs)`` pairs, where
    ``morphs`` maps target name -> list of (x, y, z) per-vertex deltas (all
    the same length within one morph). Returns ``(gltf, bin_bytes)``; wrap
    with ``_glb_with_bin`` to get an actual file.
    """
    buffer_bytes = bytearray()
    accessors: list[dict] = []
    buffer_views: list[dict] = []
    meshes: list[dict] = []
    for mesh_name, morphs in mesh_specs:
        base_accessor_index = len(accessors)
        # Base POSITION: Draco-compressed in real exports, so no bufferView
        # here either -- only the min/max bounds the scale check needs.
        accessors.append({"min": [-0.3, 0.0, -0.2], "max": [0.3, height, 0.2]})
        names = list(morphs)
        targets = []
        for name in names:
            data = _pack_vec3_list(morphs[name])
            offset = len(buffer_bytes)
            buffer_bytes += data
            view_index = len(buffer_views)
            buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
            accessor_index = len(accessors)
            accessors.append({
                "bufferView": view_index,
                "componentType": 5126,
                "count": len(morphs[name]),
                "type": "VEC3",
            })
            targets.append({"POSITION": accessor_index})
        meshes.append({
            "name": mesh_name,
            "extras": {"targetNames": names},
            "primitives": [{"attributes": {"POSITION": base_accessor_index}, "targets": targets}],
        })
    gltf = {
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffer_bytes)}],
    }
    return gltf, bytes(buffer_bytes)


def _healthy_native_viseme_morphs(vertex_count=5):
    """A full, correctly-baked native-viseme rig: every shape distinct and
    well above the noise floor, none duplicated -- the case that must PASS.
    """
    names = [f"viseme_{c}" for c in (
        "p", "t", "S", "T", "f", "k", "i", "r", "s", "u",
        "@", "a", "e", "E", "o", "O", "sil",
    )] + ["blink"]
    morphs = {}
    for idx, name in enumerate(names):
        # A few mm, distinct per shape (matches magnitudes actually measured
        # on real exports), so no two healthy shapes are anywhere near the
        # duplicate threshold of each other. Directions are scattered (not
        # all parallel) so this fixture's mean pairwise cosine similarity
        # lands well under FOLLOWER_COLLINEARITY_THRESHOLD (measured here:
        # ~0.46), matching a richly-detailed mesh like a real head mesh
        # (~0.34) rather than a single-degree-of-freedom follower mesh like
        # a real inner mouth (~0.92) -- a validate() call on this fixture
        # must be judged strictly, not downgraded to informational notes.
        base = 0.002 + idx * 0.0005
        angle = idx * 47  # irregular step avoids accidental periodicity
        dx = math.cos(math.radians(angle))
        dy = math.sin(math.radians(angle))
        dz = math.cos(math.radians(angle * 2))
        morphs[name] = [(base * dx, base * dy, base * dz) for _ in range(vertex_count)]
    return morphs


def _collinear_viseme_morphs(vertex_count=5):
    """A synthetic jaw-follower-style mesh: every alive morph points in
    exactly the same direction and differs only in magnitude, the way a
    real inner-mouth mesh driven by a single degree of freedom (jaw angle)
    does. Two visemes sharing a magnitude collapse to the identical shape;
    visemes given the smallest magnitude collapse to (near) zero. Unlike
    ``_healthy_native_viseme_morphs``, this fixture's mean pairwise cosine
    similarity is 1.0 -- well over FOLLOWER_COLLINEARITY_THRESHOLD -- on
    purpose, to exercise the "expected collapse" side of the richness gate.
    """
    names = [f"viseme_{c}" for c in (
        "p", "t", "S", "T", "f", "k", "i", "r", "s", "u",
        "@", "a", "e", "E", "o", "O", "sil",
    )] + ["blink"]
    morphs = {}
    for idx, name in enumerate(names):
        base = 0.002 + idx * 0.0005
        morphs[name] = [(base, base * 0.5, 0.0) for _ in range(vertex_count)]
    return morphs


class _Written(unittest.TestCase):
    def write(self, data: bytes, name="mae.glb") -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_bytes(data)
        return path


class ContainerTests(_Written):
    def test_a_text_gltf_is_rejected_with_a_usable_reason(self):
        path = self.write(b'{"asset": {"version": "2.0"}}', name="mae.gltf")
        with self.assertRaises(GlbError) as caught:
            read_glb_json(path)
        self.assertIn("GLB", str(caught.exception))

    def test_json_chunk_is_recovered_from_a_valid_container(self):
        gltf = _mesh(ARKIT_52)
        self.assertEqual(len(morph_target_names(read_glb_json(
            self.write(_glb(gltf))))), 52)


class ExportFailureTests(_Written):
    def test_a_full_arkit_export_passes(self):
        problems = validate(self.write(_glb(_mesh(ARKIT_52))))
        self.assertEqual(problems, [])

    def test_blendshapes_turned_off_is_caught(self):
        gltf = _mesh([])
        gltf["meshes"][0]["primitives"][0]["targets"] = []
        problems = validate(self.write(_glb(gltf)))
        self.assertTrue(any("no morph targets" in p for p in problems), problems)

    def test_right_shapes_under_the_wrong_names_are_caught(self):
        """The failure that looks like success: it loads and never moves."""
        renamed = [n.replace("jawOpen", "Jaw_Open").replace("mouthClose", "Mouth_Close")
                   for n in ARKIT_52]
        problems = validate(self.write(_glb(_mesh(renamed))))
        self.assertTrue(problems)
        joined = " ".join(problems)
        self.assertIn("jawOpen", joined)
        self.assertIn("mouthClose", joined)

    def test_targets_whose_names_the_exporter_dropped_are_caught(self):
        gltf = _mesh([], primitive_targets=[{} for _ in range(52)])
        problems = validate(self.write(_glb(gltf)))
        self.assertTrue(any("NO NAMES" in p for p in problems), problems)

    def test_centimetre_scale_is_caught_with_the_fix(self):
        problems = validate(self.write(_glb(_mesh(ARKIT_52, height=165.0))))
        self.assertTrue(any("centimetres" in p for p in problems), problems)

    def test_oversize_file_is_flagged_but_only_on_size(self):
        big = _glb(_mesh(ARKIT_52), pad_to=31 * 1024 * 1024)
        problems = validate(self.write(big))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("MB", problems[0])

    def test_height_is_read_from_position_bounds(self):
        self.assertAlmostEqual(
            estimated_height(_mesh(ARKIT_52, height=1.7)), 1.7, places=3)


class MorphPayloadTests(_Written):
    """The Blender action/action_slot trap: a morph target that is declared
    correctly (right name, real accessor) but was never actually baked, so
    it either moves nothing or duplicates a sibling shape. This has shipped
    to production twice now -- these tests are the guard against a third.
    """

    def test_a_fully_healthy_native_viseme_model_passes(self):
        """Guard against false positives: a real, correctly-baked rig must
        not be flagged just because the checks exist."""
        morphs = _healthy_native_viseme_morphs()
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        self.assertEqual(problems, [])

    def test_a_dead_morph_is_flagged_by_mesh_and_name(self):
        morphs = _healthy_native_viseme_morphs()
        vertex_count = len(morphs["viseme_T"])
        # Float32 noise floor, not a real shape -- exactly what an
        # action baked without its action_slot produces.
        morphs["viseme_T"] = [(1e-7, 0.0, 0.0)] * vertex_count
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertIn("head", joined)
        self.assertIn("viseme_T", joined)

    def test_a_dead_viseme_sil_is_not_exempted(self):
        """viseme_sil is legitimately small when real (near-rest by design),
        but it was one of the shapes that shipped dead in production, so a
        blanket exemption is exactly wrong -- confirm it still gets caught.
        """
        morphs = _healthy_native_viseme_morphs()
        vertex_count = len(morphs["viseme_sil"])
        morphs["viseme_sil"] = [(2.5e-7, 0.0, 0.0)] * vertex_count
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        self.assertTrue(any("viseme_sil" in p for p in problems), problems)

    def test_a_duplicate_morph_pair_is_flagged_by_name(self):
        """The other half of the real defect: one shape baked twice under
        two names, bit-identical."""
        morphs = _healthy_native_viseme_morphs()
        morphs["viseme_i"] = morphs["viseme_e"]
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertIn("viseme_e", joined)
        self.assertIn("viseme_i", joined)
        self.assertIn("identical", joined)

    def test_a_near_but_not_bit_identical_pair_is_still_flagged(self):
        """A near-duplicate (tiny float noise between two bakes of the same
        source pose) must be caught too, not just an exact bitwise match."""
        morphs = _healthy_native_viseme_morphs()
        nudged = [(x + 1e-8, y, z) for x, y, z in morphs["viseme_e"]]
        morphs["viseme_i"] = nudged
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertIn("viseme_e", joined)
        self.assertIn("viseme_i", joined)

    def test_defect_is_reported_per_mesh_not_globally(self):
        """The real bug was per-mesh (head fine, inner mouth dead on the
        same visemes) -- the message must implicate only the broken mesh."""
        healthy = _healthy_native_viseme_morphs()
        broken = _healthy_native_viseme_morphs()
        vertex_count = len(broken["viseme_k"])
        broken["viseme_k"] = [(1e-7, 0.0, 0.0)] * vertex_count
        gltf, bin_data = _gltf_with_meshes([("head", healthy), ("inner_mouth", broken)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertIn("inner_mouth", joined)
        self.assertIn("viseme_k", joined)
        self.assertNotIn("'head'", joined)

    def test_check_function_reads_deltas_without_a_draco_decoder(self):
        """Direct check of the payload function: morph deltas are read from
        the plain-float32 BIN chunk with no Draco involved, matching how
        these exports actually lay out morph target data."""
        morphs = _healthy_native_viseme_morphs()
        morphs["viseme_o"] = morphs["viseme_a"]
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        gltf2, bin_chunk = read_glb_chunks(self.write(_glb_with_bin(gltf, bin_data)))
        problems = check_degenerate_and_duplicate_morphs(gltf2, bin_chunk)
        joined = " ".join(problems)
        self.assertIn("viseme_a", joined)
        self.assertIn("viseme_o", joined)

    def test_mesh_with_no_live_morphs_is_not_flagged_but_a_mixed_mesh_is(self):
        """The distinction the whole fix rests on: a mesh with NO live
        morphs at all (it simply doesn't deform this way -- eyeballs,
        eyebrows) must not be flagged, while a mesh with SOME dead morphs
        alongside live siblings (the bake actually dropped a shape) must
        still be caught."""
        inert = {name: [(1e-7, 0.0, 0.0)] * 5 for name in _healthy_native_viseme_morphs()}
        mixed = _healthy_native_viseme_morphs()
        vertex_count = len(mixed["viseme_p"])
        mixed["viseme_p"] = [(1e-7, 0.0, 0.0)] * vertex_count
        gltf, bin_data = _gltf_with_meshes([("eyebrows", inert), ("mouth", mixed)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertNotIn("eyebrows", joined)
        self.assertIn("mouth", joined)
        self.assertIn("viseme_p", joined)

    def test_a_mesh_with_one_alive_singleton_and_a_dead_family_is_not_flagged(self):
        """Reproduces the real false positive this fix targets: on a real
        shipped file, char:eyebrowsShape has ``blink`` genuinely alive
        (eyebrows measurably move for a blink) while all 17 visemes are
        uniformly dead -- because eyebrows simply do not move for mouth
        sounds, not because the bake failed. The naive "flag a dead morph
        if anything else on the mesh is alive" rule was tried and produces
        exactly this false positive; the family-based rule must not."""
        morphs = {
            name: [(1e-7, 0.0, 0.0)] * 5
            for name in _healthy_native_viseme_morphs()
            if name != "blink"
        }
        morphs["blink"] = [(1.05e-4, 0.0, 0.0)] * 5  # genuinely alive, small but real
        gltf, bin_data = _gltf_with_meshes([("eyebrows", morphs)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        self.assertEqual(problems, [])

    def test_a_mesh_with_zero_live_morphs_gets_a_note_not_a_problem(self):
        inert = {name: [(1e-7, 0.0, 0.0)] * 5 for name in _healthy_native_viseme_morphs()}
        gltf, bin_data = _gltf_with_meshes([("eyeballs", inert)])
        path = self.write(_glb_with_bin(gltf, bin_data))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gltf2, bin_chunk = read_glb_chunks(path)
            problems = check_degenerate_and_duplicate_morphs(gltf2, bin_chunk)
        self.assertEqual(problems, [])
        self.assertIn("no live morphs at all", buf.getvalue())

    def test_low_fidelity_follower_mesh_is_not_flagged_but_a_rich_mesh_is(self):
        """The core distinction the richness fix rests on: a mesh whose
        alive morphs point in nearly the same direction (one dominant
        degree of freedom, like a real jaw/tongue-only inner mouth) must
        not be failed for collapsing several visemes to the same or a
        near-zero shape, while a directionally-diverse (richly-detailed)
        mesh doing the exact same thing must still be caught."""
        rich = _healthy_native_viseme_morphs()
        vertex_count = len(rich["viseme_T"])
        rich["viseme_T"] = [(1e-7, 0.0, 0.0)] * vertex_count  # genuine dead shape

        follower = _collinear_viseme_morphs()
        for name in ("viseme_S", "viseme_T", "viseme_f", "viseme_sil"):
            follower[name] = [(3e-7, 0.0, 0.0)] * len(follower[name])
        follower["viseme_i"] = follower["viseme_e"]  # same driving pose, faithfully transcribed

        gltf, bin_data = _gltf_with_meshes([("head", rich), ("mouth", follower)])
        problems = validate(self.write(_glb_with_bin(gltf, bin_data)))
        joined = " ".join(problems)
        self.assertIn("head", joined)
        self.assertIn("viseme_T", joined)
        self.assertNotIn("mouth", joined)

    def test_low_fidelity_mesh_findings_become_informational_notes(self):
        follower = _collinear_viseme_morphs()
        follower["viseme_S"] = [(3e-7, 0.0, 0.0)] * len(follower["viseme_S"])
        gltf, bin_data = _gltf_with_meshes([("mouth", follower)])
        path = self.write(_glb_with_bin(gltf, bin_data))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gltf2, bin_chunk = read_glb_chunks(path)
            problems = check_degenerate_and_duplicate_morphs(gltf2, bin_chunk)
        self.assertEqual(problems, [])
        self.assertIn("viseme_S", buf.getvalue())
        self.assertIn("cosine similarity", buf.getvalue())

    def test_sparse_accessor_is_treated_as_unreadable_not_zero(self):
        """A sparse morph accessor is valid glTF; reading it as dense would
        silently report a real shape as dead. It must be refused instead."""
        morphs = _healthy_native_viseme_morphs()
        gltf, bin_data = _gltf_with_meshes([("head", morphs)])
        # Mark one morph's POSITION accessor as sparse -- no actual sparse
        # payload needed, the point is that it must not be read as dense.
        target = gltf["meshes"][0]["primitives"][0]["targets"][0]
        gltf["accessors"][target["POSITION"]]["sparse"] = {
            "count": 1,
            "indices": {"bufferView": 0, "componentType": 5123},
            "values": {"bufferView": 0},
        }
        problems = check_degenerate_and_duplicate_morphs(gltf, bin_data)
        # The sparse morph must not appear as "dead" -- it was never measured.
        first_name = list(morphs)[0]
        for problem in problems:
            if "essentially no vertices" in problem:
                self.assertNotIn(first_name, problem)


if __name__ == "__main__":
    unittest.main()
