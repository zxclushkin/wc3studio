"""Tests for the OpenGL viewport.

The camera maths is pure numpy and runs anywhere; the widget itself needs PySide6
and a graphics context, so those checks only run when both are available.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio.render import look_at_matrix as look_at
from wc3studio.render import perspective_matrix as perspective


class TestCameraMath(unittest.TestCase):
    def test_look_at_is_a_rigid_transform(self):
        view = look_at([10.0, 0.0, 0.0], [0.0, 0.0, 0.0])
        rotation = view[:3, :3]
        np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
        self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0, places=9)

    def test_look_at_puts_the_target_on_the_negative_z_axis(self):
        eye = np.array([0.0, -10.0, 5.0])
        target = np.array([0.0, 0.0, 5.0])
        view = look_at(eye, target)
        homogeneous = np.append(target, 1.0)
        camera_space = view @ homogeneous
        self.assertAlmostEqual(camera_space[0], 0.0, places=6)
        self.assertAlmostEqual(camera_space[1], 0.0, places=6)
        self.assertLess(camera_space[2], 0.0, "the target must be in front of the camera")

    def test_perspective_maps_near_and_far_to_ndc(self):
        near, far = 1.0, 100.0
        projection = perspective(focal=1.0, aspect=1.0, near=near, far=far)

        def ndc(z: float) -> float:
            clip = projection @ np.array([0.0, 0.0, z, 1.0])
            return clip[2] / clip[3]

        self.assertAlmostEqual(ndc(-near), -1.0, places=6)
        self.assertAlmostEqual(ndc(-far), 1.0, places=6)

    def test_perspective_keeps_the_centre_centred(self):
        projection = perspective(focal=1.0, aspect=1.0, near=0.1, far=50.0)
        clip = projection @ np.array([0.0, 0.0, -5.0, 1.0])
        self.assertAlmostEqual(clip[0] / clip[3], 0.0, places=9)
        self.assertAlmostEqual(clip[1] / clip[3], 0.0, places=9)


class TestOptionalQt(unittest.TestCase):
    def test_qt_modules_import_or_are_absent(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from wc3studio import glview, gui_qt  # noqa: F401
        self.assertTrue(hasattr(glview, "ModelView"))
        self.assertTrue(hasattr(gui_qt, "MainWindow"))


def _view_or_skip():
    """A ModelView without showing it: no GL context, but the state is real."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        raise unittest.SkipTest("PySide6 is not installed")
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as exc:  # noqa: BLE001 - no display in CI
            raise unittest.SkipTest(f"no Qt platform: {exc}") from exc
    from wc3studio.pipeline import Session
    from wc3studio.stormlib import StormLibError
    from wc3studio.glview import ModelView
    try:
        session = Session(None)
    except StormLibError as exc:
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc
    return ModelView(session), session


class TestViewState(unittest.TestCase):
    """Regressions from the bug hunt: state that used to be missing or stale."""

    def setUp(self):
        self.view, self.session = _view_or_skip()

    def tearDown(self):
        self.view.deleteLater()
        self.session.close()

    def test_uploaded_frame_is_initialised(self):
        """It was missing, so paintGL raised AttributeError and the view went blank."""
        self.assertIsNone(self.view._uploaded_frame)
        self.assertIsNone(self.view._white)
        self.assertEqual(self.view.textures, {})

    def test_all_geosets_refits_the_camera(self):
        """Toggling it used to change the flag but keep the old framing and items."""
        path = self.session.resolve(r"Buildings\Human\TownHall\TownHall.mdx")
        self.view.set_model(path)
        pose_radius = float(self.view.camera["radius"])
        self.view.set_all_geosets(True)
        self.assertTrue(self.view.all_geosets)
        self.assertNotEqual(float(self.view.camera["radius"]), pose_radius,
                            "forcing every geoset on must refit the camera")
        self.assertIsNone(self.view._uploaded_frame, "the frame must be re-uploaded")

    def test_model_without_geometry_is_reported(self):
        path = self.session.resolve(r"Abilities\Spells\Undead\AntiMagicShell\AntiMagicShell.mdx")
        self.view.set_model(path)
        self.assertFalse(self.view.has_geometry(),
                         "target-only models have no triangles and must say so")

    def test_real_unit_has_geometry_and_a_camera(self):
        path = self.session.resolve("knight")
        self.view.set_model(path)
        self.assertEqual(self.view.model_path, path)
        self.assertIsNotNone(self.view.camera)
        self.assertTrue(self.view.model.geosets)


if __name__ == "__main__":
    unittest.main(verbosity=2)
