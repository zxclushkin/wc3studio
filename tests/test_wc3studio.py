"""Test suite for wc3studio.

Most tests need a Warcraft III installation; they skip themselves when none is found.
Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from wc3studio import Game, Resources, parse_mdx, render, export_obj, export_glb, read_glb
from wc3studio.anim import Animator, _slerp
from wc3studio.stormlib import StormLibError

SHEEP = r"Units\Critters\Sheep\Sheep.mdx"
FOOTMAN = r"Units\Human\Footman\Footman.mdx"


def game_or_skip(testcase=None):
    """Open the game, or skip the test.

    Raises ``unittest.SkipTest`` rather than calling ``testcase.skipTest`` because this
    is used from ``setUpClass`` too, where only the class (not an instance) is available.
    """
    try:
        return Game.autodetect()
    except StormLibError as exc:
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc


class TestMath(unittest.TestCase):
    def test_slerp_endpoints_and_midpoint(self):
        a = (0.0, 0.0, 0.0, 1.0)
        b = (0.0, 1.0, 0.0, 0.0)          # 180 degrees about Y
        start = np.asarray(_slerp(a, b, 0.0))
        end = np.asarray(_slerp(a, b, 1.0))
        self.assertTrue(np.allclose(start, a, atol=1e-6))
        self.assertTrue(np.allclose(end, b, atol=1e-6))
        mid = np.asarray(_slerp(a, b, 0.5))
        self.assertAlmostEqual(float(np.linalg.norm(mid)), 1.0, places=6)

    def test_slerp_takes_short_way_for_opposite_signs(self):
        """q and -q are the same rotation; blending must not collapse to zero."""
        a = (0.0, 0.06, 0.0, -0.998)
        b = (0.04, 0.0, -0.04, 0.998)
        mid = np.asarray(_slerp(a, b, 0.5))
        self.assertGreater(float(np.linalg.norm(mid)), 0.9)


class TestArchives(unittest.TestCase):
    def setUp(self):
        self.game = game_or_skip(self)

    def tearDown(self):
        self.game.close()

    def test_listfiles_and_models(self):
        self.assertGreater(len(self.game.names()), 1000)
        models = self.game.models()
        self.assertGreater(len(models), 100)
        self.assertTrue(all(n.lower().endswith((".mdx", ".mdl")) for n in models))

    def test_lookup_is_case_insensitive(self):
        """Models reference textures with a different case than the listfile holds."""
        upper = self.game.read(r"Textures\Footman.blp")
        lower = self.game.read(r"textures\footman.blp")
        mixed = self.game.read(r"TEXTURES\fOoTmAn.BlP")
        self.assertIsNotNone(upper)
        self.assertEqual(upper, lower)
        self.assertEqual(upper, mixed)

    def test_resolve_prefers_exact_stem(self):
        from wc3studio.pipeline import Session
        session = Session(self.game.root)
        try:
            self.assertEqual(session.resolve("sheep").lower(), SHEEP.lower())
            with self.assertRaises(LookupError):
                session.resolve("no-such-model-anywhere")
        finally:
            session.close()


class TestModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = game_or_skip(cls)
        cls.resources = Resources(cls.game)
        cls.sheep = parse_mdx(cls.game.read(SHEEP))

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_basic_structure(self):
        model = self.sheep
        self.assertEqual(model.version, 800)
        self.assertEqual(len(model.geosets), 1)
        self.assertEqual(len(model.nodes), 12)
        self.assertEqual(len(model.bones), 11)
        self.assertGreaterEqual(len(model.sequences), 5)

    def test_geoset_indices_are_in_range(self):
        for model_path in (SHEEP, FOOTMAN):
            model = parse_mdx(self.game.read(model_path))
            for geoset in model.geosets:
                self.assertEqual(len(geoset.vertices) % 3, 0)
                self.assertEqual(len(geoset.faces) % 3, 0)
                vertices = geoset.vertex_count
                self.assertGreater(vertices, 0)
                self.assertGreaterEqual(min(geoset.faces), 0)
                self.assertLess(max(geoset.faces), vertices)
                for uv in geoset.uv_sets:
                    self.assertEqual(len(uv), vertices * 2)
                self.assertLessEqual(max(geoset.matrix_indices) if geoset.matrix_indices else 0,
                                     len(model.nodes) - 1)

    def test_visibility_rule_stays_sane(self):
        """Gore/effect geosets are dropped, but never most of the model."""
        for model_path in (SHEEP, FOOTMAN, r"Buildings\Human\TownHall\TownHall.mdx"):
            model = parse_mdx(self.game.read(model_path))
            visible = set(range(len(model.geosets))) - model.pose_hidden_geosets
            shown = sum(model.geosets[i].triangle_count for i in visible)
            self.assertGreater(shown, 0, model_path)
            self.assertLessEqual(shown, model.triangle_count)

    def test_animation_moves_vertices(self):
        animator = Animator(self.sheep)
        walk = self.sheep.sequence("Walk")
        low = animator.skin(self.sheep.geosets[0], animator.node_matrices(walk["start"], 0.0))
        high = animator.skin(self.sheep.geosets[0], animator.node_matrices(walk["start"] + 233, 233.0))
        displacement = np.linalg.norm(high - low, axis=1)
        self.assertGreater(displacement.max(), 5.0, "the walk cycle should bob the body")

    def test_rest_pose_world_matrices_are_identity(self):
        """With no animation every node sits at the origin; vertices stay put."""
        animator = Animator(self.sheep)
        matrices = {node.object_id: np.eye(4) for node in self.sheep.nodes}
        raw = animator.skin(self.sheep.geosets[0], matrices)
        rest_positions = np.asarray(self.sheep.geosets[0].vertices).reshape(-1, 3)
        self.assertTrue(np.allclose(raw, rest_positions))


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = game_or_skip(cls)
        cls.resources = Resources(cls.game)

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_render_size_and_content(self):
        model = parse_mdx(self.game.read(SHEEP))
        image = render(model, self.resources, size=192)
        self.assertEqual(image.size, (192, 192))
        pixels = np.asarray(image)
        background = np.asarray([24, 26, 32])
        different = (np.abs(pixels.astype(int) - background).sum(axis=2) > 20).sum()
        self.assertGreater(different, 200, "the render looks empty")

    def test_render_without_resources(self):
        """A missing texture set must degrade to flat colours, not crash."""
        model = parse_mdx(self.game.read(SHEEP))
        image = render(model, None, size=96)
        self.assertEqual(image.size, (96, 96))


class TestExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = game_or_skip(cls)
        cls.resources = Resources(cls.game)
        cls.model = parse_mdx(cls.game.read(SHEEP))

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_obj_matches_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = export_obj(self.model, Path(tmp) / "sheep.obj", self.resources)
            self.assertEqual(stats["triangles"], self.model.triangle_count)
            self.assertEqual(stats["vertices"], self.model.geosets[0].vertex_count)

            obj_lines = (Path(tmp) / "sheep.obj").read_text(encoding="utf-8").splitlines()
            vertices = sum(1 for line in obj_lines if line.startswith("v "))
            faces = sum(1 for line in obj_lines if line.startswith("f "))
            self.assertEqual(vertices, self.model.geosets[0].vertex_count)
            self.assertEqual(faces * 3, len(self.model.geosets[0].faces))

            mtl = (Path(tmp) / "sheep.mtl").read_text(encoding="utf-8")
            texture_line = [l for l in mtl.splitlines() if l.startswith("map_Kd ")]
            self.assertTrue(texture_line, "the sheep has a texture, the MTL should map it")
            texture_path = Path(tmp) / texture_line[0].split(None, 1)[1]
            self.assertTrue(texture_path.is_file(), f"missing {texture_path}")

    def test_glb_structure_and_animation_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sheep.glb"
            stats = export_glb(self.model, target, self.resources)
            self.assertTrue(target.is_file())
            self.assertEqual(stats["animations"], len(self.model.sequences))

            document = read_glb(target)
            gltf = document.gltf
            self.assertEqual(gltf["asset"]["version"], "2.0")
            self.assertIn("skins", gltf)
            self.assertEqual(len(gltf["meshes"]), 1)

            # every bufferView stays inside the BIN chunk
            for view in gltf["bufferViews"]:
                self.assertLessEqual(view.get("byteOffset", 0) + view["byteLength"], len(document.bin))
            # POSITION accessors carry min/max, as the spec requires
            for mesh in gltf["meshes"]:
                for primitive in mesh["primitives"]:
                    accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
                    self.assertIn("min", accessor)
                    self.assertIn("max", accessor)

            # the skin must reproduce the MDX deformation
            animator = Animator(self.model)
            walk = self.model.sequence("Walk")
            anim_index = next(i for i, a in enumerate(gltf["animations"])
                              if a["name"].startswith("Walk"))
            duration = walk["end"] - walk["start"]
            mesh_node = next(i for i, node in enumerate(gltf["nodes"]) if "mesh" in node)
            for fraction in (0.0, 0.25, 0.5, 0.75):
                frame = walk["start"] + duration * fraction
                mdx = animator.skin(self.model.geosets[0],
                                    animator.node_matrices(frame, frame - walk["start"]))
                glb = document.skinned_positions(
                    mesh_node, document.posed_world_matrices(anim_index, duration * fraction / 1000.0))
                deviation = np.linalg.norm(mdx - glb, axis=1).max()
                self.assertLess(deviation, 0.5, f"glTF skinning drifts at t={fraction}")

    def test_glb_without_textures_still_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bare.glb"
            stats = export_glb(self.model, target, None)
            self.assertTrue(target.is_file())
            self.assertGreater(stats["triangles"], 0)
            document = read_glb(target)
            self.assertNotIn("images", document.gltf)

    def test_selected_sequences_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "walk.glb"
            stats = export_glb(self.model, target, self.resources, sequences=["Walk"])
            self.assertEqual(stats["animations"], 1)
            self.assertTrue(read_glb(target).gltf["animations"][0]["name"].startswith("Walk"))


class TestBlp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = game_or_skip(cls)

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_decodes_paletted_and_jpeg_textures(self):
        from wc3studio import decode_blp
        for internal in (r"Textures\Footman.blp", r"UI\Glues\SinglePlayer\HumanCampaign3D\Footman.blp"):
            data = self.game.read(internal)
            self.assertIsNotNone(data, internal)
            image = decode_blp(data)
            self.assertEqual(image.mode, "RGBA")
            self.assertGreater(image.width, 16)
            self.assertEqual(image.size[0], image.size[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
