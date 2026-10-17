"""Tests for the icon library: the naming rule (no game) and the real join (game)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio.icons import DISABLED_DIR, disabled_path


class TestDisabledPath(unittest.TestCase):
    def test_btn_becomes_disbtn(self):
        result = disabled_path(r"ReplaceableTextures\CommandButtons\BTNFootman.blp")
        self.assertEqual(result, DISABLED_DIR + r"\DISBTNFootman.blp")

    def test_passive_button(self):
        result = disabled_path(r"ReplaceableTextures\PassiveButtons\PASBTNRegenerationAura.blp")
        self.assertEqual(result, DISABLED_DIR + r"\DISPASBTNRegenerationAura.blp")

    def test_missing_prefix_is_added(self):
        result = disabled_path(r"ReplaceableTextures\CommandButtons\HolyBolt.blp")
        self.assertTrue(result.endswith(r"\DISBTNHolyBolt.blp"))

    def test_empty(self):
        self.assertEqual(disabled_path(""), "")


# --------------------------------------------------------------------------- real game

def _index_or_skip():
    from wc3studio import Game
    from wc3studio.icons import build_index
    from wc3studio.stormlib import StormLibError
    from wc3studio.tables import TableDatabase
    try:
        game = Game.autodetect()
    except StormLibError as exc:
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc
    index = build_index(game, TableDatabase(game))
    return game, index


class TestRealIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game, cls.index = _index_or_skip()

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_index_size_and_kinds(self):
        stats = self.index.stats()
        self.assertGreater(stats["total"], 1500)
        for kind in ("unit", "ability", "upgrade", "item"):
            self.assertGreater(stats["kinds"][kind]["records"], 0, kind)
            self.assertEqual(stats["kinds"][kind]["missing_files"], 0,
                             f"{kind} icons must exist in the archives")

    def test_unit_icon_and_model(self):
        record = self.index.by_id("hfoo")
        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "unit")
        self.assertIn("BTNFootman", record.icon)
        self.assertTrue(record.disabled.endswith("DISBTNFootman.blp"))
        self.assertIn("footman", record.model.lower())
        self.assertEqual(record.name, "Пехотинец")

    def test_ability_icon(self):
        record = self.index.by_id("AHhb")
        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "ability")
        self.assertIn("BTNHolyBolt", record.icon)
        self.assertTrue(record.name)

    def test_icon_for_model(self):
        record = self.index.for_model(r"Units\Human\Footman\Footman.mdx")
        self.assertIsNotNone(record)
        self.assertEqual(record.id, "hfoo")
        # doodads and effects legitimately have no command button
        self.assertIsNone(self.index.for_model(r"Objects\Spawnmodels\Human\HumanBlood\HumanBloodFootman.mdx"))

    def test_export_writes_png_and_index(self):
        from wc3studio import Resources
        from wc3studio.icons import export
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "icons"
            stats = export(self.index, Resources(self.game), out, kinds=["unit"], limit=5)
            self.assertEqual(stats["records"], 5)
            self.assertGreater(stats["written"], 0)
            pngs = list(out.rglob("*.png"))
            self.assertEqual(len(pngs), stats["written"])
            payload = json.loads((out / "icons.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["records"]), 5)
            first = payload["records"][0]
            self.assertTrue(first["icon_png"].endswith(".png"))
            self.assertTrue((out / first["icon_png"]).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
