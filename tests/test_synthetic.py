"""Game-independent tests: a small MDX model is built in memory and pushed through the
whole pipeline (parse -> render -> OBJ -> GLB -> read back).

This is what CI actually exercises, since the game archives are not available there.
"""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from wc3studio import parse_mdx, read_glb, render
from wc3studio.anim import Animator
from wc3studio.gltf import export_glb
from wc3studio.objexport import export_obj


# --------------------------------------------------------------------------- builder

def chunk(tag: str, payload: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack("<I", len(payload)) + payload


def fixed(text: str, size: int) -> bytes:
    raw = text.encode("utf-8")[: size - 1]
    return raw + b"\x00" * (size - len(raw))


def quat_z(degrees: float) -> tuple:
    half = math.radians(degrees) / 2.0
    return (0.0, 0.0, math.sin(half), math.cos(half))


def build_mdx(rotation_degrees: float = 90.0) -> bytes:
    """A minimal but fully valid v800 model: one triangle, one bone, one animation."""
    pivot = (5.0, 0.0, 0.0)
    vertices = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    normals = [(0.0, 0.0, 1.0)] * 3
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    faces = [0, 1, 2]

    # --- VERS / MODL / SEQS
    vers = chunk("VERS", struct.pack("<I", 800))
    modl = chunk("MODL", fixed("TestTriangle", 80) + fixed("", 260)
                 + struct.pack("<7f", 0, 0, 0, 10, 10, 0, 0.0) + struct.pack("<I", 0))
    seq = (fixed("Stand", 80) + struct.pack("<2I", 0, 1000)
           + struct.pack("<f", 0.0) + struct.pack("<I", 0) + struct.pack("<f", 0.0)
           + struct.pack("<I", 0) + struct.pack("<7f", 0, 0, 0, 10, 10, 0, 0.0))
    seqs = chunk("SEQS", seq)

    # --- TEXS (268 bytes per texture)
    texs = chunk("TEXS", struct.pack("<I", 0) + fixed(r"Textures\Test.blp", 260)
                 + struct.pack("<I", 0))

    # --- MTLS: one material with one layer, size includes its own 4 bytes
    layer = struct.pack("<6I", 28, 0, 0, 0, 0xFFFFFFFF, 0) + struct.pack("<f", 1.0)
    material = struct.pack("<I", 48) + struct.pack("<i", 0) + struct.pack("<I", 0)
    material += b"LAYS" + struct.pack("<I", 1) + layer
    mtls = chunk("MTLS", material)

    # --- GEOS
    geo = bytearray()
    geo += b"VRTX" + struct.pack("<I", len(vertices))
    geo += struct.pack(f"<{len(vertices) * 3}f", *[c for vertex in vertices for c in vertex])
    geo += b"NRMS" + struct.pack("<I", len(normals))
    geo += struct.pack(f"<{len(normals) * 3}f", *[c for normal in normals for c in normal])
    geo += b"PTYP" + struct.pack("<I", 1) + struct.pack("<I", 4)          # triangles
    geo += b"PCNT" + struct.pack("<I", 1) + struct.pack("<I", len(faces))
    geo += b"PVTX" + struct.pack("<I", len(faces)) + struct.pack("<3H", *faces)
    geo += b"GNDX" + struct.pack("<I", len(vertices)) + bytes([0] * len(vertices))
    geo += b"MTGC" + struct.pack("<I", 1) + struct.pack("<I", 1)          # one group of 1
    geo += b"MATS" + struct.pack("<I", 1) + struct.pack("<I", 0)          # bone 0
    geo += struct.pack("<3I", 0, 0, 0)                                    # materialId, group, flags
    geo += struct.pack("<7f", 0, 0, 0, 10, 10, 0, 0.0)                    # extent
    geo += struct.pack("<I", 0)                                           # no per-sequence extents
    geo += b"UVAS" + struct.pack("<I", 1)
    geo += b"UVBS" + struct.pack("<I", len(uvs))
    geo += struct.pack(f"<{len(uvs) * 2}f", *[c for uv in uvs for c in uv])
    geos = chunk("GEOS", struct.pack("<I", len(geo) + 4) + bytes(geo))

    # --- BONE: KGTR so the skeleton actually animates, then geosetId/geosetAnimId
    keys = [(0, (0.0, 0.0, 0.0)), (1000, (0.0, 0.0, 0.0))]
    kgtr = b"KGTR" + struct.pack("<I", len(keys)) + struct.pack("<I", 1) + struct.pack("<i", -1)
    for frame, value in keys:
        kgtr += struct.pack("<I", frame) + struct.pack("<3f", *value)
    kgrt = b"KGRT" + struct.pack("<I", 2) + struct.pack("<I", 1) + struct.pack("<i", -1)
    kgrt += struct.pack("<I", 0) + struct.pack("<4f", *quat_z(0.0))
    kgrt += struct.pack("<I", 1000) + struct.pack("<4f", *quat_z(rotation_degrees))
    generic = fixed("Bone0", 80) + struct.pack("<iiI", 0, -1, 0) + kgtr + kgrt
    bone_entry = struct.pack("<I", 96 + len(kgtr) + len(kgrt)) + generic
    bone_entry += struct.pack("<2i", -1, -1)
    bones = chunk("BONE", bone_entry)

    # --- PIVT
    pivt = chunk("PIVT", struct.pack("<3f", *pivot))

    return b"MDLX" + vers + modl + seqs + texs + mtls + geos + bones + pivt


# --------------------------------------------------------------------------- tests

class TestSyntheticModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build_mdx()
        cls.model = parse_mdx(cls.data)

    def test_parses_all_chunks(self):
        model = self.model
        self.assertEqual(model.version, 800)
        self.assertEqual(model.name, "TestTriangle")
        self.assertEqual(len(model.geosets), 1)
        self.assertEqual(len(model.materials), 1)
        self.assertEqual(len(model.textures), 1)
        self.assertEqual(len(model.nodes), 1)
        self.assertEqual(len(model.pivots), 1)
        self.assertEqual([s["name"] for s in model.sequences], ["Stand"])
        self.assertEqual(model.triangle_count, 1)

    def test_geometry_is_intact(self):
        geoset = self.model.geosets[0]
        self.assertEqual(geoset.vertex_count, 3)
        self.assertEqual(list(geoset.faces), [0, 1, 2])
        self.assertEqual(len(geoset.uv_sets[0]), 6)
        self.assertEqual(list(geoset.vertex_groups), [0, 0, 0])

    def test_rest_pose_equals_bind_pose(self):
        animator = Animator(self.model)
        at_rest = animator.skin(self.model.geosets[0], animator.node_matrices(0.0, 0.0))
        raw = np.asarray(self.model.geosets[0].vertices).reshape(-1, 3)
        self.assertTrue(np.allclose(at_rest, raw, atol=1e-6))

    def test_rotation_happens_around_the_pivot(self):
        """Bone pivot is (5,0,0); rotating 90 degrees about Z must move the origin vertex
        to (5,-5,0) -- i.e. the pivot, not the model origin, is the rotation centre."""
        model = parse_mdx(build_mdx(rotation_degrees=90.0))
        animator = Animator(model)
        posed = animator.skin(model.geosets[0], animator.node_matrices(1000.0, 1000.0))
        self.assertTrue(np.allclose(posed[0], (5.0, -5.0, 0.0), atol=1e-4), posed[0])
        self.assertTrue(np.allclose(posed[1], (5.0, 5.0, 0.0), atol=1e-4), posed[1])

    def test_render_produces_pixels(self):
        image = render(self.model, None, size=96, shadow=False)
        self.assertEqual(image.size, (96, 96))
        pixels = np.asarray(image).astype(int)
        painted = (np.abs(pixels - np.asarray([24, 26, 32])).sum(axis=2) > 20).sum()
        self.assertGreater(painted, 50)


class TestSyntheticExports(unittest.TestCase):
    def test_obj_round_trip(self):
        model = parse_mdx(build_mdx())
        with tempfile.TemporaryDirectory() as tmp:
            stats = export_obj(model, Path(tmp) / "tri.obj", None)
            self.assertEqual(stats["triangles"], 1)
            text = (Path(tmp) / "tri.obj").read_text(encoding="utf-8")
            self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("v ")), 3)
            self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("vt ")), 3)
            self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("f ")), 1)
            self.assertTrue((Path(tmp) / "tri.mtl").is_file())

    def test_glb_keeps_skin_and_animation(self):
        model = parse_mdx(build_mdx(rotation_degrees=90.0))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tri.glb"
            stats = export_glb(model, target, None)
            self.assertEqual(stats["animations"], 1)
            self.assertEqual(stats["triangles"], 1)

            document = read_glb(target)
            self.assertIn("skins", document.gltf)
            self.assertEqual(len(document.gltf["skins"][0]["joints"]), 1)
            self.assertEqual(len(document.gltf["animations"]), 1)

            # the exported animation must reproduce the pivot rotation exactly
            animator = Animator(model)
            mesh_node = next(i for i, node in enumerate(document.gltf["nodes"]) if "mesh" in node)
            for frame in (0.0, 250.0, 500.0, 1000.0):
                expected = animator.skin(model.geosets[0], animator.node_matrices(frame, frame))
                got = document.skinned_positions(
                    mesh_node, document.posed_world_matrices(0, frame / 1000.0))
                deviation = float(np.linalg.norm(expected - got, axis=1).max())
                self.assertLess(deviation, 0.05, f"glTF skinning drifts at frame {frame}")

    def test_glb_is_a_valid_container(self):
        model = parse_mdx(build_mdx())
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tri.glb"
            export_glb(model, target, None)
            raw = target.read_bytes()
            self.assertEqual(raw[:4], b"glTF")
            self.assertEqual(struct.unpack_from("<I", raw, 4)[0], 2)
            self.assertEqual(struct.unpack_from("<I", raw, 8)[0], len(raw))
            document = read_glb(target)
            for view in document.gltf["bufferViews"]:
                self.assertLessEqual(view.get("byteOffset", 0) + view["byteLength"], len(document.bin))


if __name__ == "__main__":
    unittest.main(verbosity=2)
