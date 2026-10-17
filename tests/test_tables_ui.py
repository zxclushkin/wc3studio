"""Tests for the data tables and the interface layer.

The first half builds both formats in memory, so it runs anywhere (CI included).
The rest checks the real archives and skips when the game is not installed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio.tables import Table, parse_ini, parse_sylk
from wc3studio.ui import UiLibrary, build_contact_sheet, parse_fdf

SYLK = """ID;PWXL;N;E
B;X3;Y3;D0
C;X1;Y1;K"id"
C;X2;K"name"
C;X3;K"hp"
C;X1;Y2;K"hfoo"
C;X2;K"Foot""man"
C;X3;K420
C;X1;Y3;K"ogru"
C;X2;K"Grunt"
C;X3;K700
E
"""

INI = """// units
[Footman]
Name=Пехотинец
HP=420
[Grunt]
Name=Бугай
HP=700
Extra=1
"""

FDF = r'''
// -- includes ---------------------------------------------------
IncludeFile "UI\FrameDef\Base.fdf",

/* a block comment that mentions
   Frame "FAKE" "NotReal" { } and must be ignored */

Frame "BACKDROP" "MyTemplate" {
    BackdropTileBackground,
    BackdropBackground  "UI\Widgets\Bg.blp",
    Text "SOME_STRING",
    Texture {
        File "ConsoleTexture01",
        TexCoord 0, 1, 0, 0.125,
        Anchor TOPLEFT,0,0,
    }
}

Frame "TEXT" "MyText" INHERITS "MyTemplate" {
    SetAllPoints,
}

Frame "TEXT" "InheritsChildren" INHERITS WITHCHILDREN "MyTemplate" {
    Width 0.5,
}

Frame"TEXT" "NoSpaceBeforeBrace"{
    Height 0.25,
}

String "MyString" INHERITS "BaseString" {
    Font "Fonts\FRIZQT__.TTF",
}

StringList {
    HELLO   "Привет",
    BYE     "Пока",
}
'''


class TestSylk(unittest.TestCase):
    def setUp(self):
        self.table = parse_sylk(SYLK, "test.slk")

    def test_columns_and_rows(self):
        self.assertEqual(self.table.columns, ["id", "name", "hp"])
        self.assertEqual(self.table.row_count, 2)
        self.assertEqual(self.table.rows[0][0], "hfoo")
        self.assertEqual(self.table.rows[1][2], "700")

    def test_row_number_only_appears_when_it_changes(self):
        """Cells without a Y belong to the previous row - the format's main trap."""
        self.assertEqual(self.table.row_count, 2)
        self.assertEqual(self.table.rows[0][1], 'Foot"man', "quoted quotes must unescape")

    def test_where_and_select(self):
        only_footman = self.table.where(hp="420")
        self.assertEqual(only_footman.row_count, 1)
        self.assertEqual(only_footman.rows[0][0], "hfoo")
        slim = only_footman.select(["id", "hp"])
        self.assertEqual(slim.columns, ["id", "hp"])
        self.assertEqual(slim.rows, [["hfoo", "420"]])

    def test_csv_round_trip(self):
        import csv
        import io
        rows = list(csv.reader(io.StringIO(self.table.to_csv())))
        self.assertEqual(rows[0], ["id", "name", "hp"])
        self.assertEqual(len(rows), 3)

    def test_json_is_valid(self):
        import json
        payload = json.loads(self.table.to_json())
        self.assertEqual(payload["columns"], ["id", "name", "hp"])
        self.assertEqual(payload["rows"][1], ["ogru", "Grunt", "700"])

    def test_missing_column_raises(self):
        with self.assertRaises(KeyError):
            self.table.column("nope")


class TestIni(unittest.TestCase):
    def test_sections_become_rows(self):
        table = parse_ini(INI, "units.txt")
        self.assertEqual(table.row_count, 2)
        self.assertEqual(table.columns[0], "section")
        self.assertIn("Name", table.columns)
        self.assertEqual(table.rows[0][0], "Footman")
        name_index = table.index_of("Name")
        self.assertEqual(table.rows[0][name_index], "Пехотинец")

    def test_sparse_keys_are_kept(self):
        table = parse_ini(INI, "units.txt")
        self.assertIn("Extra", table.columns)
        self.assertEqual(table.column("Extra"), ["", "1"])

    def test_empty_text(self):
        table = parse_ini("", "empty.txt")
        self.assertEqual(table.row_count, 0)


class TestFdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = parse_fdf(FDF, "test.fdf")

    def test_parses_without_warnings(self):
        self.assertEqual(self.document.warnings, [])

    def test_includes(self):
        self.assertEqual(self.document.includes, [r"UI\FrameDef\Base.fdf"])

    def test_comments_are_ignored(self):
        names = [frame.name for frame in self.document.all_frames()]
        self.assertNotIn("NotReal", names)

    def test_frame_types_and_inheritance(self):
        by_name = self.document.frames_named()
        self.assertEqual(by_name["mytemplate"].kind, "BACKDROP")
        self.assertEqual(by_name["mytext"].inherits, "MyTemplate")
        self.assertEqual(by_name["inheritschildren"].inherits, "MyTemplate")
        self.assertEqual(by_name["nospacebeforebrace"].kind, "TEXT")

    def test_properties_and_children(self):
        template = self.document.frames_named()["mytemplate"]
        self.assertEqual(template.properties["BackdropBackground"], r"UI\Widgets\Bg.blp")
        self.assertEqual(template.properties["Text"], "SOME_STRING")
        self.assertEqual(len(template.children), 1)
        texture = template.children[0]
        self.assertEqual(texture.kind, "Texture")
        self.assertEqual(texture.properties["File"], "ConsoleTexture01")
        self.assertEqual(texture.properties["TexCoord"], "0, 1, 0, 0.125")

    def test_string_list(self):
        self.assertEqual(self.document.strings["HELLO"], "Привет")
        self.assertEqual(self.document.strings["BYE"], "Пока")
        self.assertEqual(len(self.document.strings), 2)

    def test_texture_references(self):
        template = self.document.frames_named()["mytemplate"]
        self.assertEqual(template.texture_references(), [r"UI\Widgets\Bg.blp"])
        # the logical name lives on the child Texture block
        self.assertEqual(template.children[0].decorated_names(), ["ConsoleTexture01"])

    def test_walk_visits_children(self):
        kinds = [frame.kind for frame in self.document.all_frames()]
        self.assertIn("Texture", kinds)
        self.assertIn("String", kinds)


class TestContactSheet(unittest.TestCase):
    def test_builds_a_grid(self):
        from PIL import Image
        images = [Image.new("RGB", (32, 32), (index * 10, 40, 80)) for index in range(7)]
        sheet = build_contact_sheet(images, [f"tile{index}" for index in range(7)],
                                    tile=48, columns=3)
        self.assertEqual(sheet.mode, "RGB")
        self.assertGreater(sheet.width, 3 * 48)
        self.assertGreater(sheet.height, 2 * 48)

    def test_empty_input_does_not_crash(self):
        sheet = build_contact_sheet([], [])
        self.assertEqual(sheet.size, (128, 128))


# --------------------------------------------------------------------------- real data

def game_or_skip():
    from wc3studio import Game
    from wc3studio.stormlib import StormLibError
    try:
        return Game.autodetect()
    except StormLibError as exc:
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc


class TestRealTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from wc3studio.tables import TableDatabase
        cls.game = game_or_skip()
        cls.db = TableDatabase(cls.game)

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_finds_many_tables(self):
        summary = self.db.tables_summary()
        self.assertGreater(len(summary), 100)
        kinds = {kind for _name, kind, _rows, _cols in summary}
        self.assertEqual(kinds, {"slk", "ini"})

    def test_unit_balance_shape(self):
        table = self.db.load("UnitBalance")
        self.assertEqual(table.row_count, 812)
        self.assertGreater(table.column_count, 50)
        self.assertIn("goldcost", [column.lower() for column in table.columns])

    def test_friendly_name_prefers_the_base_table(self):
        table = self.db.load("UnitData")
        self.assertTrue(table.source.lower().startswith("units\\"),
                        f"resolved to {table.source}, expected the base game table")

    def test_joined_unit_database(self):
        units = self.db.units()
        self.assertEqual(units.row_count, 812)
        self.assertIn("name", units.columns)
        footman = units.find_row("hfoo")
        self.assertIsNotNone(footman)
        record = dict(zip(units.columns, footman))
        self.assertEqual(record["hp"], "420")
        self.assertEqual(record["gold"], "135")
        self.assertTrue(record["name"], "localised unit name should be filled in")

    def test_localised_unit_names(self):
        name = self.db.unit_name("hfoo")
        self.assertTrue(name)
        self.assertNotEqual(name.lower(), "hfoo")

    def test_strings_table(self):
        strings = self.db.strings
        self.assertGreater(len(strings), 100)
        self.assertIn("name", {key.lower() for key in strings["hfoo"]})


class TestRealInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = game_or_skip()
        cls.lib = UiLibrary(cls.game)

    @classmethod
    def tearDownClass(cls):
        cls.game.close()

    def test_documents_parse_cleanly(self):
        documents = self.lib.documents()
        self.assertGreater(len(documents), 50)
        warnings = 0
        frames = 0
        for path in documents:
            document = self.lib.load(path)
            warnings += len(document.warnings)
            frames += len(document.all_frames())
        self.assertEqual(warnings, 0, "every shipped FDF must parse without warnings")
        self.assertGreater(frames, 1000)

    def test_frame_index_and_inheritance(self):
        index = self.lib.index()
        self.assertGreater(len(index), 500)
        details = self.lib.resolve("StandardHeavyBackdropTemplate")
        self.assertEqual(details["kind"], "BACKDROP")
        self.assertIn("BackdropBackground", details["properties"])
        self.assertTrue(details["textures"], "the template references backdrop textures")

    def test_missing_frame_raises(self):
        with self.assertRaises(KeyError):
            self.lib.resolve("ThisFrameDoesNotExist")

    def test_localised_ui_strings(self):
        strings = self.lib.strings()
        self.assertGreater(len(strings), 500)
        values = " ".join(list(strings.values())[:200])
        self.assertTrue(any(ord(char) > 0x400 for char in values),
                        "the localisation should contain non-latin text")

    def test_ui_artwork_inventory(self):
        assets = self.lib.assets()
        self.assertGreater(len(assets), 500)
        self.assertTrue(all(item["path"].lower().startswith("ui\\") for item in assets))

    def test_reference_resolution(self):
        # a reference that exists resolves, an invented one does not
        resolved = self.lib.resolve_reference(r"UI\Widgets\HeavyBorderBackGround.blp")
        self.assertIsNone(resolved)
        some = self.lib.assets()[0]["path"]
        self.assertEqual(self.lib.resolve_reference(some), some)


if __name__ == "__main__":
    unittest.main(verbosity=2)
