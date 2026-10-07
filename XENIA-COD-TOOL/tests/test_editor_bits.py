import tempfile
import unittest
from pathlib import Path

from t5menu import custom, highlight, testgsc
from t5menu.highlight import value_line_to_line

CALLBACK_TEXT = "CodeCallback_StartGameType()\r\n{\r\n\tlevel.x = 1;\r\n}\r\n"


class HighlightTest(unittest.TestCase):
    def test_gsc(self):
        src = 'init()\n{\n\tself iPrintLnBold( "hi" ); // c\n\twait 0.5;\n\tmaps\\mp\\_utility::f();\n}\n#include a\\b;\n'
        got = {(k, src[a:b]) for k, a, b in highlight.spans(src, "gsc", "mw2")}
        for want in [("function_def", "init"), ("keyword", "self"), ("builtin", "iPrintLnBold"),
                     ("string", '"hi"'), ("comment", "// c"), ("keyword", "wait"), ("number", "0.5"),
                     ("path", "maps\\mp\\_utility"), ("function", "f"), ("directive", "#include a\\b;")]:
            self.assertIn(want, got)

    def test_bo1_keywords(self):
        src = "f()\n{\n\twaitrealtime 1;\n}\n"
        self.assertIn(("keyword", "waitrealtime"),
                      {(k, src[a:b]) for k, a, b in highlight.spans(src, "gsc", "bo1")})
        self.assertNotIn(("keyword", "waitrealtime"),
                         {(k, src[a:b]) for k, a, b in highlight.spans(src, "gsc", "mw2")})

    def test_menu(self):
        src = 'menuDef {\n  name "x"\n  rect 0.0 -1 2 3 0 0\n  background ref:0x3000AAB5\n  action {\n    script null\n  }\n}\n'
        got = {(k, src[a:b]) for k, a, b in highlight.spans(src, "menu")}
        for want in [("keyword", "menuDef"), ("property", "name"), ("string", '"x"'), ("number", "-1"),
                     ("ref", "ref:0x3000AAB5"), ("keyword", "action"), ("builtin", "script"), ("literal", "null")]:
            self.assertIn(want, got)

    def test_value_lines(self):
        text = "// c\nmenuDef {\n\n  name x\n  // c\n  rect 1\n"
        self.assertEqual(value_line_to_line(text, 1), 2)
        self.assertEqual(value_line_to_line(text, 3), 6)


class CustomTest(unittest.TestCase):
    def test_mw2(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / testgsc.CALLBACK).parent.mkdir(parents=True)
            (ws / testgsc.CALLBACK).write_bytes(CALLBACK_TEXT.encode())
            p = custom.new_script(ws, "mw2", "mymod")
            with self.assertRaises(custom.CustomError):
                custom.new_script(ws, "mw2", "mymod")
            with self.assertRaises(custom.CustomError):
                custom.new_script(ws, "mw2", "bad name")
            files = custom.mw2_hook(ws, {"custom/mymod.gsc": p.read_bytes()}, log=lambda *_: None)
            cb = files[testgsc.CALLBACK].decode()
            self.assertIn("thread custom\\mymod::init();", cb)
            self.assertEqual(custom.mw2_hook(ws, {}, log=lambda *_: None)[testgsc.CALLBACK].decode(), cb)

    def test_bo1(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            custom.new_script(ws, "bo1", "mine")
            out = custom.bo1_merge(ws, CALLBACK_TEXT, log=lambda *_: None)
            self.assertIn("thread mine_init();", out)
            self.assertIn("mine_onPlayerSpawned()", out)
            self.assertTrue(out.index("thread mine_init") < out.index("level.x"))


if __name__ == "__main__":
    unittest.main()
