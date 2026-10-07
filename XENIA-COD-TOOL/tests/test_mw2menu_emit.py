import struct
import unittest
from pathlib import Path

from t5menu import mw2, mw2menu_emit as E
from t5menu.mw2menu_parse import Parser, Ref

UI_MP = [p for d in ("/mnt/project-files/uploads/hearth", "/root/.claude/uploads")
         for p in Path(d).glob("**/*ui_mp.ffm")] if Path("/mnt/project-files").exists() else []


def _str(r, v):
    return None if v is None else r.string(v).decode() if isinstance(v, Ref) else str(v)


@unittest.skipUnless(UI_MP, "no stock MW2 ui_mp.ffm here")
class EmitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.z = mw2.load_zone(UI_MP[0].read_bytes())
        cls.r = E.Resolver(cls.z)

    def test_lines_up(self):
        self.assertEqual(self.r.base, 0xF54)
        self.assertEqual(len(self.r.menus()), 269)

    def test_button(self):
        spec = {"menu": "menu_gamesetup_systemlink", "after": "systemlink_startmatch",
                "copy": "systemlink_setupmatch", "name": "systemlink_test", "text": "Test",
                "action": ['"exec" "echo test" ; ']}
        plans = E.patch_menus(self.r, [spec])
        mem = 8 * 3
        asset = E.menu_list_asset(self.r, E.PATCH_LIST, plans, mem=mem)
        E.virtual_size(asset, mem=mem)  # parses back to its exact length
        back = Parser(asset).menu_list(0)
        self.assertEqual(str(back["name"]), E.PATCH_LIST)
        items = back["menus"][0]["items"]
        names = [_str(None, it["window"]["name"]) if not isinstance(it["window"]["name"], Ref) else "ref"
                 for it in items]
        i = names.index("systemlink_test")
        self.assertEqual(names[i - 1], "systemlink_startmatch")
        self.assertEqual(names[i + 1], "systemlink_setupmatch")
        self.assertEqual(str(items[i]["text"]), "Test")
        self.assertEqual(items[i]["window"]["rect"]["y"], 48.0)
        self.assertEqual(items[i + 1]["window"]["rect"]["y"], 68.0)
        self.assertEqual(str(items[i]["action"]["handlers"][0]["script"]), '"exec" "echo test" ; ')
        # nothing in the copy points back into ui_mp: every pointer to earlier data is an
        # expression inside the asset itself
        for it in items:
            for k in ("text", "dvar"):
                self.assertNotIsInstance(it[k], Ref)

    def test_patch_notes(self):
        from t5menu import patchnotes
        plans = E.patch_menus(self.r, patchnotes.specs())
        mem = 8 * 3
        asset, temp = E.menu_list(self.r, E.PATCH_LIST, plans, mem=mem)
        E.virtual_size(asset, mem=mem)
        self.assertGreater(temp, 416 * 4)
        back = Parser(asset).menu_list(0)
        names = [str(m["window"]["name"]) for m in back["menus"]]
        self.assertEqual(names, [patchnotes.PAGE1, patchnotes.PAGE2, patchnotes.UNLOCK_POPUP, patchnotes.PLAY_MENU,
                                 patchnotes.SETTINGS, patchnotes.QM_SEARCH, patchnotes.QM_HOST, "main",
                                 patchnotes.LOBBY])
        page1 = back["menus"][0]
        texts = [str(it["text"]) for it in page1["items"] if it["text"] is not None]
        self.assertIn("Xenia Cod Project 10/4/2026 (MW2)", texts)
        self.assertIn("- Adds Patch Notes Button - Wyatt", texts)
        self.assertTrue(all(it["visibleExp"] is None for it in page1["items"]))
        main = back["menus"][7]
        items = [it for it in main["items"] if it is not None]
        by_name = {str(it["window"]["name"]): it for it in items if not isinstance(it["window"]["name"], Ref)}
        self.assertNotIn("system_link", by_name)
        ys = {n: by_name[n]["window"]["rect"]["y"] for n in
              ("button_xboxlive", "t5mt_play", "t5mt_unlockall", "t5mt_patchnotes",
               "button_main_options", "button_main_singleplayer")}
        self.assertEqual(ys, {"button_xboxlive": 48.0, "t5mt_play": 68.0, "t5mt_unlockall": 88.0,
                              "t5mt_patchnotes": 108.0, "button_main_options": 128.0,
                              "button_main_singleplayer": 148.0})
        # popup items are placed from rectClient, relative to the popup's own rect
        panel = page1["items"][1]["window"]
        self.assertEqual((panel["rect"]["x"], panel["rectClient"]["x"]), (-427.0, 0.0))
        lobby = back["menus"][8]
        self.assertIn(patchnotes.LOBBY_TITLE, [str(it["text"]) for it in lobby["items"] if it is not None])
        lnames = [str(it["window"]["name"]) for it in lobby["items"]
                  if it is not None and not isinstance(it["window"]["name"], Ref) and it["window"]["name"] is not None]
        i = lnames.index("systemlink_startmatch")
        self.assertEqual(lnames[i + 1:i + 6], ["t5mt_play_modes", "t5mt_settings", "t5mt_bots_tdm", "t5mt_bots_ffa",
                                               "systemlink_setupmatch"])
        play = back["menus"][3]
        rows = [it for it in play["items"] if it is not None and it["type"] == 1]
        self.assertEqual([str(it["text"]) for it in rows], ["TDM", "FFA", "SND", "GUN GAME"])
        self.assertEqual([it["window"]["rect"]["y"] for it in rows], [28.0, 48.0, 68.0, 88.0])
        script = "".join(str(h["script"]) for h in rows[0]["action"]["handlers"])
        self.assertIn('"exec" "localservers ; wait 120 ; t5mt_quickmatch"', script)
        self.assertIn('"setdvar" "ui_gametype" "war"', script)
        settings = back["menus"][4]
        sliders = [it for it in settings["items"] if it is not None and it["type"] == 10]
        self.assertEqual([(str(it["dvar"]), it["typeData"]["minVal"], it["typeData"]["maxVal"]) for it in sliders],
                         [("cg_fov", 65.0, 110.0), ("cg_fovScale", 1.0, 2.0)])
        handlers = main["onOpen"]["handlers"]
        self.assertEqual(handlers[-1]["type"], 1)  # the added if ( ! dvarbool( ... ) )

    def test_expression(self):
        from t5menu.mw2menu import OP_INDEX
        e = E.parse_expression('( ! dvarbool( "seen" ) )')
        self.assertEqual(e, [(0, OP_INDEX["("], 0), (0, OP_INDEX["!"], 0), (0, OP_INDEX["dvarbool("], 0),
                             (1, 2, b"seen"), (0, OP_INDEX[")"], 0)])
        self.assertEqual(E.parse_expression('! dvarbool( "seen" )'), e)
        with self.assertRaises(E.EmitError):
            E.parse_expression("nosuchfunc( 1 )")


if __name__ == "__main__":
    unittest.main()
