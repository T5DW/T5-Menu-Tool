import struct
import tempfile
import unittest
from pathlib import Path

from t5menu import gamedirs, gsc, testgsc

STOCK = """// Callback Setup\r
CodeCallback_StartGameType()\r
{\r
\tif(!isDefined(level.gametypestarted) || !level.gametypestarted)\r
\t{\r
\t\t[[level.callbackStartGameType]]();\r
\t}\r
}\r
"""


class TestScriptTest(unittest.TestCase):
    def folder(self):
        d = Path(tempfile.mkdtemp())
        cb = d / testgsc.CALLBACK
        cb.parent.mkdir(parents=True)
        cb.write_bytes(STOCK.encode())
        return d, cb

    def test_mw2(self):
        d, cb = self.folder()
        testgsc.install(d, "mw2", log=lambda *_: None)
        testgsc.install(d, "mw2", log=lambda *_: None)
        text = cb.read_bytes().decode()
        self.assertEqual(text.count(testgsc.MW2_HOOK), 1)
        self.assertIn("{\r\n\t" + testgsc.MW2_HOOK, text)
        spam = (d / testgsc.MW2_HELPER).read_bytes()
        self.assertIn(b'iPrintLnBold( "test" )', spam)
        self.assertIn(b"t5mt_unlockall", spam)
        self.assertIn(b"i < 10", spam)
        self.assertIn(b"wait 0.5", spam)
        self.assertEqual(gsc.check("spam", spam) + gsc.check("cb", cb.read_bytes()), [])

    def test_bo1_keeps_one_file(self):
        d, cb = self.folder()
        testgsc.install(d, "bo1", log=lambda *_: None)
        testgsc.install(d, "bo1", log=lambda *_: None)
        text = cb.read_bytes().decode()
        self.assertEqual(text.count("testSpamInit()\r\n{"), 1)
        self.assertEqual(text.count(testgsc.BO1_HOOK), 1)
        self.assertFalse((d / testgsc.MW2_HELPER).exists())
        self.assertEqual(gsc.check("cb", cb.read_bytes()), [])

    def test_mw2_upgrades_old_hook(self):
        d, cb = self.folder()
        cb.write_bytes(testgsc.add_hook(STOCK, testgsc.MW2_OLD_HOOK).encode())
        old = d / testgsc.MW2_OLD_SCRIPT
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"// T5MenuTool test script")
        testgsc.install(d, "mw2", log=lambda *_: None)
        text = cb.read_bytes().decode()
        self.assertNotIn(testgsc.MW2_OLD_HOOK, text)
        self.assertIn(testgsc.MW2_HOOK, text)
        self.assertFalse(old.exists())

    def test_no_callback(self):
        with self.assertRaises(testgsc.HookError):
            testgsc.add_hook("main() {}", "x();")


class GameDirTest(unittest.TestCase):
    def test_identify(self):
        d = Path(tempfile.mkdtemp())
        (d / "default_mp.xex").write_bytes(b"XEX2")
        self.assertIsNone(gamedirs.identify(d))
        (d / "common_mp.ff").write_bytes(b"IWff0100" + struct.pack(">I", 0x1D9))
        self.assertEqual(gamedirs.identify(d).game, "bo1")
        (d / "common_mp.ffm").write_bytes(b"IWffu100" + struct.pack(">I", 0xFD))
        self.assertEqual(gamedirs.identify(d).game, "mw2")

    def test_gsc_folder(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(gamedirs.gsc_folder(d), d / "gsc")
        self.assertTrue((d / "gsc").is_dir())


if __name__ == "__main__":
    unittest.main()
