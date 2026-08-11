"""The export checker must fail the exports that actually go wrong.

A validator nobody trusts is worse than none, so each case here is a real
failure mode of a Character Creator export rather than a synthetic one:
blendshapes turned off, the right shapes under the wrong names, morph
targets whose names the exporter dropped, and a model authored in
centimetres.
"""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.validate_avatar_glb import (
    ARKIT_52,
    GlbError,
    estimated_height,
    morph_target_names,
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


if __name__ == "__main__":
    unittest.main()
