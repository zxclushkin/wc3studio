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

from wc3studio.glview import look_at, perspective


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
