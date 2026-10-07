"""Round-trip tests. Set T5MENU_TEST_FF to a Bo1 Xbox 360 ui_mp.ff to run them:

    T5MENU_TEST_FF=path/to/ui_mp.ff python -m unittest discover tests
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.setrecursionlimit(20000)

from t5menu.fastfile import FastFile  # noqa: E402
from t5menu.menufile import MenuEditError, apply_edits, find_menu_lists, menu_entries, write_menu  # noqa: E402

FF = os.environ.get("T5MENU_TEST_FF")


@unittest.skipUnless(FF and Path(FF).exists(), "set T5MENU_TEST_FF to a Bo1 360 ui_mp.ff")
class RoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ff = FastFile.load(FF)
        cls.lists = find_menu_lists(cls.ff.zone)
        cls.menus = list(menu_entries(cls.lists))

    def test_container_roundtrip(self):
        for signed in (False, True):
            again = FastFile.parse(self.ff.build(signed=signed))
            self.assertEqual(again.zone, self.ff.zone)

    def test_unchanged_text_changes_nothing(self):
        zone = bytearray(self.ff.zone)
        for ln, i, m in self.menus[:40]:
            w = write_menu(self.ff.zone, m, ln, i)
            self.assertEqual(apply_edits(zone, w, w.text()), 0)
        self.assertEqual(bytes(zone), self.ff.zone)

    def test_edit_is_patched_and_reparses(self):
        ln, i, m = self.menus[0]
        w = write_menu(self.ff.zone, m, ln, i)
        text = w.text()
        edited = text.replace("  focusColor ", "  focusColor 0.5 0.5 0.5 1.0 //", 1)
        line = next(l for l in text.splitlines() if l.strip().startswith("focusColor "))
        edited = text.replace(line, line.split("focusColor")[0] + "focusColor 0.5 0.5 0.5 1.0", 1)
        zone = bytearray(self.ff.zone)
        self.assertGreater(apply_edits(zone, w, edited), 0)
        lists = find_menu_lists(bytes(zone))
        m2 = list(menu_entries(lists))[0][2]
        self.assertEqual(m2["focusColor"], [0.5, 0.5, 0.5, 1.0])

    def test_growing_a_string_is_rejected(self):
        ln, i, m = self.menus[0]
        w = write_menu(self.ff.zone, m, ln, i)
        text = w.text()
        line = next(l for l in text.splitlines() if l.strip().startswith('name "'))
        bad = text.replace(line, line[:-1] + 'xxxxxxxx"', 1)
        with self.assertRaises(MenuEditError):
            apply_edits(bytearray(self.ff.zone), w, bad)


if __name__ == "__main__":
    unittest.main()
