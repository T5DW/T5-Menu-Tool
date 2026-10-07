"""Adding menus (rebuild.py / menutext.py). Needs T5MENU_TEST_FF like test_roundtrip.py."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.setrecursionlimit(20000)

from t5menu.fastfile import FastFile  # noqa: E402
from t5menu.menufile import find_menu_lists, write_menu  # noqa: E402
from t5menu.menutext import read_menu  # noqa: E402
from t5menu.parser import MenuParser, Node  # noqa: E402
from t5menu.rebuild import (MenuWriter, RebuildError, VirtualMap, custom_menus, set_custom_menus,  # noqa: E402
                            sync_counts)

FF = os.environ.get("T5MENU_TEST_FF")


@unittest.skipUnless(FF and Path(FF).exists(), "set T5MENU_TEST_FF to a Bo1 360 ui_mp.ff")
class Rebuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ff = FastFile.load(FF)
        if cls.ff.retail:
            raise unittest.SkipTest("adding menus needs the 0x1D7 file")
        cls.vm = VirtualMap(cls.ff.zone)
        cls.stock = [m for m in cls.vm.lists[0]["menus"] if isinstance(m, Node)]
        # menus that load no material inline (those become refs to the stock copy when written)
        cls.plain = [m for m in cls.stock[:80]
                     if " material " not in write_menu(cls.ff.zone, m, "x", 0).text()]

    def new_menu(self, name, index=-1):
        m = self.stock[index]
        text = write_menu(self.ff.zone, m, "x", 0).text()
        text = text.replace('name "%s"' % m["window"]["name"].decode(), 'name "%s"' % name, 1)
        return read_menu(text, name, self.vm.material_ref)

    def test_stock_menus_write_back_identically(self):
        for m in self.plain[:40]:
            w = MenuWriter(self.ff.zone, self.vm)
            w.write_struct(m, {})
            self.assertEqual(bytes(w.out), self.ff.zone[m.pos:m.pos + len(w.out)])

    def test_text_round_trip(self):
        for m in self.plain[:25]:
            text = write_menu(self.ff.zone, m, "x", 0).text()
            node = read_menu(text, "", self.vm.material_ref)
            sync_counts(node)
            w = MenuWriter(self.ff.zone, self.vm)
            w.write_struct(node, {})
            p = MenuParser(bytes(w.out))
            again = p.one("menuDef_t", {})
            self.assertEqual(p.r.pos, len(w.out))
            self.assertEqual(write_menu(bytes(w.out), again, "x", 0).text(), text)

    def test_add_and_re_add(self):
        zone = self.ff.zone
        new = self.new_menu("t5menu_test_a")
        new["items"] = new["items"][:2]  # structure can change
        out = set_custom_menus(zone, [new], self.vm)
        lists = find_menu_lists(out)
        self.assertEqual(lists[-1]["name"], b"ui_mp/menus.txt")
        self.assertEqual(lists[-1].end, len(out))
        self.assertEqual(lists[0]["name"], b"ui_mp/menuz.txt")
        added = [m for m in lists[-1]["menus"] if isinstance(m, Node)]
        self.assertEqual([m["window"]["name"] for m in added], [b"t5menu_test_a"])
        self.assertEqual(added[0]["itemCount"], 2)
        # everything after the first localized string's name is where it was
        start = zone.index(b"\0", zone.index(b"\0", 0x34 + 8 * 619 + 8) + 1)
        self.assertEqual(out[start:len(zone)], zone[start:len(zone)].replace(b"ui_mp/menus.txt", b"ui_mp/menuz.txt", 1))
        # a second round keeps the first added menu and adds another
        vm2 = VirtualMap(out)
        self.assertEqual(len(custom_menus(vm2)), 1)
        out2 = set_custom_menus(out, custom_menus(vm2) + [self.new_menu("t5menu_test_b", -2)], vm2)
        names = [m["window"]["name"] for m in find_menu_lists(out2)[-1]["menus"] if isinstance(m, Node)]
        self.assertEqual(names, [b"t5menu_test_a", b"t5menu_test_b"])
        self.assertEqual(FastFile.parse(FastFile(self.ff.header, out2).build()).zone, out2)

    def test_duplicate_name_rejected(self):
        dup = self.new_menu(self.stock[0]["window"]["name"].decode())
        with self.assertRaises(RebuildError):
            set_custom_menus(self.ff.zone, [dup], self.vm)


if __name__ == "__main__":
    unittest.main()
