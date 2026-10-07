"""MW2 July 2009 menus. The round-trip tests need a stock file: set MW2_UI_FF to ui_mp.ffm."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from t5menu import mw2menu
from t5menu.menufile import MenuEditError
from t5menu.mw2 import load_zone

STOCK = os.environ.get("MW2_UI_FF")


class Operators(unittest.TestCase):
    def test_table(self):
        self.assertEqual(len(mw2menu.OPS), 177)
        self.assertEqual(mw2menu.OPS[12], "==")
        self.assertEqual(mw2menu.OPS[36], "dvarbool(")
        self.assertEqual(mw2menu.OPS[176], "getwaitpopupstatus(")
        self.assertEqual(mw2menu.OP_INDEX["dvarbool"], 36)


@unittest.skipUnless(STOCK, "set MW2_UI_FF to a stock ui_mp.ffm")
class StockFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.ff = cls.tmp / Path(STOCK).name
        shutil.copyfile(STOCK, cls.ff)
        cls.ws = mw2menu.decompile(cls.ff, log=lambda *_: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_every_value_encodes_to_its_bytes(self):
        zone = load_zone(self.ff.read_bytes())
        for lname, i, m in list(mw2menu.menu_entries(zone))[:40]:
            for _, toks, slots in mw2menu.write_menu(m, lname, i).lines:
                for t, s in zip(toks, slots):
                    if s is not None:
                        d = s.encode(t)
                        self.assertEqual(d, zone[s.offset:s.offset + len(d)], s.label)

    def _edit(self, old, new):
        p = sorted((self.ws / "menus").glob("000_*.menu"))[0]
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1))
        return p, text

    def test_edit_compiles(self):
        p, text = self._edit("  rect 0.0 0.0 640.0 480.0 0 0", "  rect 0.0 0.0 320.0 240.0 0 0")
        try:
            out = mw2menu.compile_workspace(self.ws, self.tmp / "out.ff", log=lambda *_: None)
            zone = load_zone(out.read_bytes())
            m = next(mw2menu.menu_entries(zone))[2]
            self.assertEqual(m["window"]["rect"]["w"], 320.0)
            self.assertEqual(len(zone), len(load_zone(self.ff.read_bytes())))
        finally:
            p.write_text(text)

    def test_longer_string_is_refused(self):
        p, text = self._edit('name "main"', 'name "mainx"')
        try:
            with self.assertRaises(MenuEditError):
                mw2menu.compile_workspace(self.ws, self.tmp / "out.ff", log=lambda *_: None)
        finally:
            p.write_text(text)

    def test_unchanged_folder_is_refused(self):
        with self.assertRaises(MenuEditError):
            mw2menu.compile_workspace(self.ws, self.tmp / "out.ff", log=lambda *_: None)

    def test_output_name(self):
        self.assertEqual(mw2menu.output_name("ui_mp.ffm"), "ui_mp.ff")
        self.assertEqual(mw2menu.output_name("ui_mp.ff"), "ui_mp.ff")
        self.assertEqual(mw2menu.output_name("patch_mp.ff"), "patch_mp.ff")


if __name__ == "__main__":
    unittest.main()
