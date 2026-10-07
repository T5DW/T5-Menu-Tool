"""GSC helpers and Bo1 GSC (synthetic zones; no game files needed)."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from t5menu import fastfile, gsc, t5gsc  # noqa: E402

SCRIPT = (b"// Callback Setup\n#include maps\\mp\\_utility;\n\n/* long\n comment */\n"
          b"CodeCallback_StartGameType()\n{\n\t// start\n\tlevel.x = \"a // not a comment\";\n}\n") * 5


def t5_fastfile(files: dict) -> bytes:
    """A minimal 0x1D7 Bo1 fastfile whose zone holds the given rawfiles inline."""
    body = b"".join(struct.pack(">III", 0xFFFFFFFF, len(t), 0xFFFFFFFF) + n.encode() + b"\0" + t + b"\0"
                    for n, t in files.items())
    zone = struct.pack(">I", len(body) + 0x1C) + bytes(0x20) + body
    header = bytearray(fastfile.HEADER_SIZE)
    header[:8] = fastfile.MAGIC_UNSIGNED
    header[8:12] = struct.pack(">I", fastfile.VERSION)
    header[0x10:0x18] = fastfile.AUTH_MAGIC
    header[0x1C:0x1C + 9] = b"common_mp"
    return fastfile.FastFile(bytes(header), zone).build(signed=False)


class Helpers(unittest.TestCase):
    def test_check_finds_problems(self):
        self.assertEqual(gsc.check("a.gsc", SCRIPT), [])
        bad = gsc.check("a.gsc", b"init()\n{\n\tif (x\n}\n")
        self.assertTrue(any("never closed" in p or "unexpected" in p for p in bad))
        self.assertTrue(gsc.check("a.gsc", b'x = "abc;\n'))

    def test_minify_keeps_code_and_strings(self):
        m = gsc.minify(SCRIPT)
        self.assertLess(len(m), len(SCRIPT))
        self.assertIn(b'"a // not a comment"', m)
        self.assertNotIn(b"Callback Setup", m)
        self.assertIn(b"#include maps\\mp\\_utility;", m)
        self.assertEqual(gsc.check("m.gsc", m), [])

    def test_inject_backs_up_and_restores(self):
        with tempfile.TemporaryDirectory() as d:
            game, built = Path(d) / "game", Path(d) / "built.ff"
            game.mkdir()
            (game / "common_mp.ff").write_bytes(b"stock")
            built.write_bytes(b"mod")
            gsc.inject(built, game, "common_mp.ff", log=lambda *_: None)
            built.write_bytes(b"mod2")
            gsc.inject(built, game, "common_mp.ff", log=lambda *_: None)
            self.assertEqual((game / "common_mp.ff").read_bytes(), b"mod2")
            self.assertEqual((game / ("common_mp.ff" + gsc.BACKUP_SUFFIX)).read_bytes(), b"stock")
            gsc.uninject(game, "common_mp.ff", log=lambda *_: None)
            self.assertEqual((game / "common_mp.ff").read_bytes(), b"stock")


class Bo1(unittest.TestCase):
    def test_extract_edit_build(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "common_mp.ff"
            src.write_bytes(t5_fastfile({"maps/mp/gametypes/_callbacksetup.gsc": SCRIPT,
                                         "maps/mp/other.gsc": b"main()\n{\n}\n"}))
            ws = t5gsc.extract(src, log=lambda *_: None)
            f = ws / "maps/mp/gametypes/_callbacksetup.gsc"
            # longer than the original: only fits once minified
            f.write_bytes(f.read_bytes() + b"// " + b"x" * 40 + b"\nmymod()\n{\n\tlevel.y = 1;\n}\n")
            out = t5gsc.build(ws, log=lambda *_: None)
            zone = fastfile.FastFile.load(out).zone
            raws = t5gsc.find_rawfiles(zone)
            start, ln = raws["maps/mp/gametypes/_callbacksetup.gsc"]
            self.assertEqual(ln, len(SCRIPT))
            self.assertIn(b"mymod()", zone[start:start + ln])
            self.assertEqual(len(zone), len(fastfile.FastFile.load(src).zone))

    def test_new_file_and_too_big_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "common_mp.ff"
            src.write_bytes(t5_fastfile({"maps/mp/a.gsc": b"main()\n{\n}\n"}))
            ws = t5gsc.extract(src, log=lambda *_: None)
            (ws / "maps/mp/a.gsc").write_bytes(b"main()\n{\n\tlevel.zzzzzzzzzzzzzzzz = 1;\n}\n")
            with self.assertRaises(gsc.GscError):
                t5gsc.build(ws, log=lambda *_: None)
            (ws / "maps/mp/a.gsc").write_bytes(b"main()\n{\n}\n")
            (ws / "maps/mp/new.gsc").write_bytes(b"x()\n{\n}\n")
            with self.assertRaises(gsc.GscError):
                t5gsc.build(ws, log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
