"""MW2 July 2009 (fastfile 0xFD) tests. The synthetic ones always run; set MW2_TEST_FF to a
stock fastfile such as common_mp.ffm to also run the extract/build round trip on it:

    MW2_TEST_FF=path/to/common_mp.ffm python -m unittest discover tests
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from t5menu import mw2  # noqa: E402

FF = os.environ.get("MW2_TEST_FF")


class Synthetic(unittest.TestCase):
    files = {
        "maps/mp/gametypes/_callbacksetup.gsc": b"CodeCallback_StartGameType()\n{\n}\n" * 40,
        "mp/tiny.cfg": b"set a 1\n",  # zlib would grow it, so it is stored plain
    }

    def test_zone_layout(self):
        zone = mw2.build_rawfile_zone(self.files)
        size, ext, *blocks = struct.unpack(">II6I", zone[:0x20])
        self.assertEqual(size + 0x20, len(zone))
        self.assertEqual(blocks[0], 0x20)
        count = struct.unpack(">I", zone[0x28:0x2C])[0]
        self.assertEqual(count, 3)  # two files + the zone-name marker
        # block 3 = asset array + names + data, as in IW's ez_common_mp
        self.assertEqual(blocks[3], len(zone) - 0x20 - 16 - 16 * count)

    def test_fastfile_round_trip(self):
        ff = mw2.build_fastfile(mw2.build_rawfile_zone(self.files))
        self.assertTrue(mw2.is_mw2_fastfile(ff))
        self.assertEqual(ff[:8], mw2.MAGIC_UNSIGNED)
        self.assertEqual(struct.unpack(">I", ff[0x1D:0x21])[0], len(ff))
        self.assertEqual(mw2.find_rawfiles(mw2.load_zone(ff)), self.files)

    def test_folder_builds_only_changes(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "common_mp.ff"
            src.write_bytes(mw2.build_fastfile(mw2.build_rawfile_zone(self.files)))
            ws = mw2.extract(src, log=lambda *_: None)
            with self.assertRaises(mw2.MW2Error):
                mw2.build_patch(ws, log=lambda *_: None)
            edited = ws / "maps/mp/gametypes/_callbacksetup.gsc"
            edited.write_bytes(b"// edited\n" + edited.read_bytes())
            (ws / "maps/mp/mymod.gsc").write_bytes(b"init()\n{\n}\n")
            out = mw2.build_patch(edited, log=lambda *_: None)
            self.assertEqual(out.name, "patch_mp.ff")
            got = mw2.find_rawfiles(mw2.load_zone(out.read_bytes()))
            self.assertEqual(set(got), {"maps/mp/gametypes/_callbacksetup.gsc", "maps/mp/mymod.gsc"})
            self.assertTrue(got["maps/mp/gametypes/_callbacksetup.gsc"].startswith(b"// edited"))


@unittest.skipUnless(FF and Path(FF).exists(), "set MW2_TEST_FF to a stock MW2 0xFD fastfile")
class StockFile(unittest.TestCase):
    def test_extract_and_rebuild(self):
        raws = mw2.find_rawfiles(mw2.load_zone(Path(FF).read_bytes()))
        self.assertTrue(any(n.endswith(".gsc") for n in raws))
        ff = mw2.build_fastfile(mw2.build_rawfile_zone(raws))
        self.assertEqual(mw2.find_rawfiles(mw2.load_zone(ff)), raws)


if __name__ == "__main__":
    unittest.main()
