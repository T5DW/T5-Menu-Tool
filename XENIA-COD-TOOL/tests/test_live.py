import struct
import unittest
import zlib
from pathlib import Path

from t5menu import live, testgsc

from test_cbuf import FakeXenia

STOCK_CB = Path("/mnt/project-files/extracted/common_mp/maps/mp/gametypes/_callbacksetup.gsc")


class FakeCb:
    def __init__(self, mem, membase):
        self.mem, self.membase = mem, membase


def game_with(files: dict[str, tuple[bytes, bool]], membase=0x100000000):
    """Guest memory holding rawfile assets: names in zone memory, structs in a pool."""
    mem = FakeXenia()
    zone = bytearray(b"\0" * 0x10)
    pool = bytearray()
    zone_base, pool_base = 0xA1000000, 0x82E00000
    for name, (text, packed) in files.items():
        name_addr = zone_base + len(zone)
        zone += name.encode() + b"\0"
        buf_addr = zone_base + len(zone)
        data = zlib.compress(text, 9) if packed else text + b"\0"
        zone += data + b"\0" * 7
        pool += struct.pack(">IIII", name_addr, len(data) if packed else 0, len(text), buf_addr)
    mem.map(membase + zone_base, zone)
    mem.map(membase + pool_base, pool + b"\0" * 64)
    return FakeCb(mem, membase)


def read_back(cb, name):
    ref = live.find_rawfiles(cb.mem, cb.membase, [name])[name][0]
    return live._read_buffer(cb.mem, cb.membase, ref), ref


class LiveTest(unittest.TestCase):
    def test_find_and_write_compressed(self):
        text = b"// comment line\n" * 100 + b"main()\n{\n}\n"
        cb = game_with({"maps/mp/a.gsc": (text, True), "maps/mp/b.gsc": (b"b()\n{\n}\n", False)})
        new = b"main()\n{\n\tthread x();\n}\n"
        done, skipped = live.inject(cb, {"maps/mp/a.gsc": new, "maps/mp/new.gsc": b"x()\n{\n}\n"},
                                    log=lambda *_: None)
        self.assertEqual((done, skipped), (["maps/mp/a.gsc"], ["maps/mp/new.gsc"]))
        got, ref = read_back(cb, "maps/mp/a.gsc")
        self.assertEqual(got.rstrip(b" "), new)
        cap = len(zlib.compress(text, 9))
        self.assertEqual(ref.capacity, cap)  # slot stays the same size for the next inject

    def test_plain_slot(self):
        cb = game_with({"x.cfg": (b"set a 1\n" * 10, False)})
        live.inject(cb, {"x.cfg": b"set b 2\n"}, log=lambda *_: None)
        got, ref = read_back(cb, "x.cfg")
        self.assertEqual(got.rstrip(b" "), b"set b 2\n")
        self.assertEqual(ref.clen, 0)

    def test_too_big(self):
        cb = game_with({"x.gsc": (b"a()\n{\n}\n", False)})
        done, skipped = live.inject(cb, {"x.gsc": bytes(range(256)) * 40}, log=lambda *_: None)
        self.assertEqual(skipped, ["x.gsc"])

    @unittest.skipUnless(STOCK_CB.exists(), "needs the extracted MW2 scripts")
    def test_mw2_helper_fits(self):
        stock = STOCK_CB.read_bytes()
        painter = (STOCK_CB.parents[3] / testgsc.MW2_HELPER).read_bytes()
        cb = game_with({testgsc.CALLBACK: (stock, True), testgsc.MW2_HELPER: (painter, True)})
        files = testgsc.mw2_files(stock.decode("latin1"))
        done, skipped = live.inject(cb, files, log=lambda *_: None)
        self.assertEqual(skipped, [])
        got, _ = read_back(cb, testgsc.CALLBACK)
        self.assertIn(testgsc.MW2_HOOK.encode(), got)


if __name__ == "__main__":
    unittest.main()
