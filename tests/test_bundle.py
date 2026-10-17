"""Tests for the asset bundle builder.

The zip helper is game-independent; the bundle itself is checked against the real
archives and skips when the game is not installed.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio.bundle import BundleOptions, build_bundle, zip_folder


class TestZipFolder(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bundle"
            (root / "models" / "Units").mkdir(parents=True)
            (root / "models" / "Units" / "a.obj").write_text("v 0 0 0\n", encoding="utf-8")
            (root / "manifest.json").write_text('{"ok": true}', encoding="utf-8")

            archive = Path(tmp) / "bundle.zip"
            size = zip_folder(root, archive)
            self.assertGreater(size, 0)
            self.assertTrue(archive.is_file())

            with zipfile.ZipFile(archive) as handle:
                names = handle.namelist()
                self.assertIn("bundle/manifest.json", names)
                self.assertIn("bundle/models/Units/a.obj", names)
                self.assertEqual(json.loads(handle.read("bundle/manifest.json")), {"ok": True})


class TestBundleOptions(unittest.TestCase):
    def test_unknown_formats_are_dropped(self):
        options = BundleOptions(out="x", formats={"models", "nonsense"}).normalised()
        self.assertEqual(options.formats, {"models"})
        self.assertIsInstance(options.out, Path)

    def test_empty_formats_fall_back(self):
        options = BundleOptions(out="x", formats=set()).normalised()
        self.assertEqual(options.formats, {"models", "glb", "sounds"})


class TestRealBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from wc3studio import Game
        from wc3studio.pipeline import Session
        from wc3studio.stormlib import StormLibError
        try:
            game = Game.autodetect()
        except StormLibError as exc:
            raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc
        cls.session = Session(game.root)
        game.close()
        cls.session = Session(cls.session.root)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_bundle_with_models_and_glb(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            options = BundleOptions(out=out, formats={"models", "glb"},
                                    model_filter=r"Units\Critters", limit=3)
            stats = build_bundle(self.session, options)

            self.assertEqual(stats["counts"]["models"], 3)
            self.assertEqual(stats["counts"]["glb"], 3)
            self.assertEqual(stats["errors"], [])

            obj_files = list(out.rglob("*.obj"))
            glb_files = list(out.rglob("*.glb"))
            self.assertEqual(len(obj_files), 3)
            self.assertEqual(len(glb_files), 3)
            self.assertTrue(any(out.rglob("*.mtl")))
            self.assertTrue(any(out.rglob("*.png")), "textures should be exported too")

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["models"], 3)
            self.assertGreater(manifest["bytes"], 0)

    def test_glb_in_bundle_contains_animations(self):
        from wc3studio.gltf import read_glb
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            options = BundleOptions(out=out, formats={"glb"},
                                    model_filter=r"Units\Critters\Sheep", limit=1)
            build_bundle(self.session, options)
            glb = next(out.rglob("*.glb"))
            document = read_glb(glb)
            self.assertTrue(document.gltf.get("animations"), "the glb must carry animations")
            self.assertIn("skins", document.gltf)

    def test_sounds_land_next_to_their_model(self):
        """MDX does not reference audio: a unit's voice lines sit in its folder."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            options = BundleOptions(out=out, formats={"sounds", "glb"},
                                    model_filter=r"Units\Human\Footman", limit=1)
            stats = build_bundle(self.session, options)

            footman = out / "Units" / "Human" / "Footman"
            wavs = sorted(footman.glob("*.wav"))
            self.assertEqual(len(wavs), 18, [path.name for path in wavs])
            self.assertEqual(stats["counts"]["sounds"], 18)
            self.assertIn("FootmanDeath.wav", [path.name for path in wavs])
            self.assertTrue(any("Yes" in path.name for path in wavs))

            # the audio must be the real thing, not an empty stub
            self.assertGreater(wavs[0].stat().st_size, 1024)

    def test_index_lists_everything_from_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            options = BundleOptions(out=out, formats={"models", "glb", "sounds"},
                                    model_filter=r"Units\Human\Footman", limit=1)
            build_bundle(self.session, options)

            records = json.loads((out / "models.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["model"], r"Units\Human\Footman\Footman.mdx")
            self.assertIn("Footman.obj", record["files"])
            self.assertIn("Footman.glb", record["files"])
            self.assertIn(r"Units\Human\Footman\Footman_Portrait.mdx", [record["portrait"]])
            self.assertTrue(record["textures"], "the model references textures")
            self.assertEqual(len(record["sounds"]), 18)
            self.assertEqual(record["sound_count"], 18)
            self.assertGreater(record["triangles"], 100)
            self.assertGreater(record["bones"], 5)
            self.assertIn("Stand - 1", record["animations"])

    def test_bundle_with_raw_tables_and_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            options = BundleOptions(out=out, formats={"raw", "tables"},
                                    model_filter=r"Units\Critters\Sheep", limit=1,
                                    archive=True)
            stats = build_bundle(self.session, options)
            self.assertGreaterEqual(stats["counts"]["raw"], 1)
            self.assertGreater(stats["counts"]["tables"], 100)
            self.assertTrue(list(out.rglob("*.mdx")))
            self.assertTrue(list((out / "tables").rglob("*.csv")))

            archive = Path(stats["archive"])
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as handle:
                self.assertTrue(any(name.endswith("manifest.json") for name in handle.namelist()))
                self.assertTrue(any(name.endswith(".mdx") for name in handle.namelist()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
