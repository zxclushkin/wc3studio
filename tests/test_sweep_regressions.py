"""Regressions for the bugs found by the full-archive sweep.

Three of them: additive-only models were dropped entirely, textures baked to an
artist's absolute path were never resolved, and the camera framed the union of several
sequences instead of the one being played.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio.render import FILTER_ADDITIVE, collect


def _session_or_skip():
    from wc3studio import Game
    from wc3studio.pipeline import Session
    from wc3studio.stormlib import StormLibError
    try:
        Game.autodetect().close()
    except StormLibError as exc:
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc
    return Session(None)


class TestAdditiveOnlyModels(unittest.TestCase):
    """189 models draw only additive layers: they used to render as nothing."""

    def setUp(self):
        self.session = _session_or_skip()

    def tearDown(self):
        self.session.close()

    def test_wisp_still_has_draw_items(self):
        path = self.session.resolve(r"Units\NightElf\Wisp\Wisp.mdx")
        model = self.session.load(path)
        animator = self.session.animator(path)
        frame = model.sequences[0]["start"]
        items = collect(model, self.session.resources, animator=animator, frame=frame)
        self.assertTrue(items, "an all-additive model must not be dropped")
        self.assertTrue(all(item["filter_mode"] == FILTER_ADDITIVE for item in items))

    def test_boneyard_still_has_draw_items(self):
        path = self.session.resolve(r"Buildings\Undead\BoneYard\BoneYard.mdx")
        model = self.session.load(path)
        items = collect(model, self.session.resources)
        self.assertTrue(items)

    def test_normal_model_keeps_skipping_glow_planes(self):
        path = self.session.resolve("footman")
        model = self.session.load(path)
        items = collect(model, self.session.resources)
        self.assertTrue(items)
        self.assertTrue(any(item["filter_mode"] != FILTER_ADDITIVE for item in items))


class TestTextureFallback(unittest.TestCase):
    """Two models point at ``\\\\guldan\\drive1\\...``; the files live in Textures\\."""

    def setUp(self):
        self.session = _session_or_skip()

    def tearDown(self):
        self.session.close()

    def test_dev_path_resolves_by_base_name(self):
        from wc3studio import parse_mdx
        data = self.session.game.read(r"Units\Human\HeroBloodElf\HeroBloodSphere.MDX")
        self.assertIsNotNone(data, "the model itself must be in the archives")
        model = parse_mdx(data)
        dev_paths = [texture.path for texture in model.textures
                     if texture.path.startswith("\\\\")]
        self.assertTrue(dev_paths, "this model is the known dev-path case")
        image = self.session.resources.image(dev_paths[0])
        self.assertIsNotNone(image, "the fallback must find the archive copy")
        self.assertIn(dev_paths[0], self.session.resources.fallbacks)

    def test_unknown_texture_still_reports_missing(self):
        self.assertIsNone(self.session.resources.image(r"Textures\NoSuchTextureHere.blp"))


class TestCamera(unittest.TestCase):
    def test_near_plane_does_not_clip_small_units(self):
        from wc3studio.glview import near_plane
        # mini icons have a radius around 0.002; the old 0.05 floor was behind them
        self.assertLess(near_plane(0.02), 0.05)
        self.assertAlmostEqual(near_plane(100.0), 2.0)
        self.assertGreaterEqual(near_plane(0.0), 1e-4)

    def test_camera_frames_the_current_sequence_not_the_union(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        session = _session_or_skip()
        try:
            from wc3studio.glview import ModelView
            view = ModelView(session)
            path = session.resolve(r"Abilities\Spells\NightElf\FanOfKnives\FanOfKnivesCaster.mdx")
            view.set_model(path)
            current = float(view.camera["radius"])

            # radius of the union over every sequence start, computed the old way
            model = view.model
            low = np.full(3, np.inf)
            high = np.full(3, -np.inf)
            for sequence in model.sequences:
                for item in collect(model, session.resources, animator=view.animator,
                                    frame=sequence["start"], local_time=0.0):
                    points = item["pos"]
                    if len(points):
                        low = np.minimum(low, points.min(axis=0))
                        high = np.maximum(high, points.max(axis=0))
            union = float(np.linalg.norm(high - low) / 2.0 * 1.14)
            self.assertLess(current, union * 0.5,
                            "the camera must not be stretched by other sequences")
            view.deleteLater()
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
