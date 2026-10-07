import struct
import unittest

from t5menu.cbuf import _BODY, _BODY_OFFSET, _PROLOGUE, PROFILES, Cbuf, CbufError, Memory


class FakeXenia(Memory):
    """Sparse stand-in for the Xenia process: a few mapped regions."""

    def __init__(self):
        self.regs = {}

    def map(self, addr, data):
        self.regs[addr] = bytearray(data)

    def _find(self, addr, size):
        for base, buf in self.regs.items():
            if base <= addr and addr + size <= base + len(buf):
                return buf, addr - base
        raise CbufError(f"unmapped {addr:#x}")

    def read(self, addr, size):
        buf, o = self._find(addr, size)
        return bytes(buf[o:o + size])

    def write(self, addr, data):
        buf, o = self._find(addr, len(data))
        buf[o:o + len(data)] = data

    def regions(self):
        return ((b, len(d)) for b, d in sorted(self.regs.items()))


def fake_game(membase, profile, maxsize=0x100, used=0):
    mem = FakeXenia()
    code = bytearray(profile.signature + b"\0" * 4 + _BODY + b"\0" * 0x40)
    code[0x7C:0x80] = profile.lis
    mem.map(membase + profile.cbuf_add_text, code)
    buf_guest = 0x83500000
    mem.map(membase + profile.cmd_text, struct.pack(">Iii", buf_guest, maxsize, used))
    mem.map(membase + buf_guest, b"x" * used + b"\0" * (maxsize - used))
    return mem, buf_guest


class CbufTest(unittest.TestCase):
    def test_send_systemlink_build(self):
        p = PROFILES[0]
        mem, buf = fake_game(0x100000000, p)
        cb = Cbuf.attach(mem, log=lambda *_: None)
        self.assertIs(cb.profile, p)
        cb.send("cg_fovScale 2", wait=0)
        self.assertEqual(mem.read(0x100000000 + buf, 15), b"cg_fovScale 2\n\0")
        self.assertEqual(cb.state()[2], 14)

    def test_appends_after_pending_text(self):
        p = PROFILES[1]
        mem, buf = fake_game(0x100000000, p, used=5)
        cb = Cbuf.attach(mem, log=lambda *_: None)
        self.assertIs(cb.profile, p)
        cb.send("say hi", wait=0)
        self.assertEqual(mem.read(0x100000000 + buf, 13), b"xxxxxsay hi\n\0")
        self.assertEqual(cb.state()[2], 12)

    def test_scan_finds_unusual_membase(self):
        p = PROFILES[1]
        mem, _ = fake_game(0x7f0000000, p)
        cb = Cbuf.attach(mem, log=lambda *_: None)
        self.assertEqual(cb.membase, 0x7f0000000)
        self.assertEqual(_BODY_OFFSET, len(_PROLOGUE) + 4)

    def test_full_buffer(self):
        mem, _ = fake_game(0x100000000, PROFILES[0], maxsize=8)
        cb = Cbuf.attach(mem, log=lambda *_: None)
        with self.assertRaises(CbufError):
            cb.send("cg_fovScale 2", wait=0)

    def test_custom_commands(self):
        from t5menu.cbuf import expand
        self.assertEqual(expand('playername "T5 DW"'), 'name "T5 DW"')
        self.assertEqual(expand("cg_fov 90;menureload"), "cg_fov 90 ; closemenu main ; openmenu main")
        self.assertEqual(expand("menureload xenia_settings"), "closemenu xenia_settings ; openmenu xenia_settings")
        with self.assertRaises(CbufError):
            expand("playername")

    def test_not_running(self):
        with self.assertRaises(CbufError):
            Cbuf.attach(FakeXenia(), log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()


class MW2Test(unittest.TestCase):
    def setUp(self):
        self.p = next(p for p in PROFILES if p.game == "mw2")
        self.mem, self.buf = fake_game(0x100000000, self.p)
        self.mem.map(0x100000000 + self.p.dev_script_flag, b"\0")
        self.logs = []
        self.cb = Cbuf.attach(self.mem, log=self.logs.append)

    def test_detects_mw2(self):
        self.assertIs(self.cb.profile, self.p)
        self.assertEqual(self.p.lis, bytes.fromhex("3d2082fc"))

    def test_addbot(self):
        self.cb.send("addbot 3", wait=0)
        want = b"set developer_script 1 ; set scr_testclients 3\n\0"
        self.assertEqual(self.mem.read(0x100000000 + self.buf, len(want)), want)
        self.assertTrue(any("new match" in l for l in self.logs))
        self.mem.write(0x100000000 + self.p.dev_script_flag, b"\1")
        self.cb.send("addbot", wait=0)
        self.assertTrue(any("join within" in l for l in self.logs))

    def test_addbot_limits(self):
        from t5menu.cbuf import expand
        with self.assertRaises(CbufError):
            expand("addbot 40", "mw2")
        with self.assertRaises(CbufError):
            expand("addbot 2", "bo1")


def _ppc(op, rd, ra, imm):
    return struct.pack(">I", op << 26 | rd << 21 | ra << 16 | (imm & 0xFFFF))


class GenericTest(unittest.TestCase):
    """A build the tool has no addresses for: found from Cbuf_AddText's own code."""

    def test_unknown_xex(self):
        from t5menu import cbuf
        base, img = 0x100000000, 0x82000000
        fn, msg, cmd, buf = 0x82100000, 0x82030000, 0x82F00010, 0x40100000
        image = bytearray(0x200000)
        image[msg - img:msg - img + len(cbuf.OVERFLOW_MSG)] = cbuf.OVERFLOW_MSG
        code = (struct.pack(">I", cbuf.MFLR_R12) + bytes(20) + _ppc(14, 3, 0, 0x1B)
                + _ppc(15, 9, 0, (cmd + 0x8000) >> 16) + _ppc(14, 8, 9, cmd & 0xFFFF)
                + _ppc(15, 11, 0, (msg + 0x8000) >> 16) + _ppc(14, 4, 11, msg & 0xFFFF))
        image[fn - img:fn - img + len(code)] = code
        mem = FakeXenia()
        mem.map(base + img, image)
        mem.map(base + cmd, struct.pack(">Iii", buf, 0x10000, 0))
        mem.map(base + buf, bytes(0x10000))
        saved = list(cbuf.PROFILES)
        cbuf.PROFILES[:] = []
        try:
            cb = Cbuf.attach(mem, log=lambda *_: None)
        finally:
            cbuf.PROFILES[:] = saved
        self.assertEqual((cb.profile.cmd_text, cb.profile.game, cb.profile.cbuf_add_text), (cmd, "mw2", fn))
        cb.send("say hi", wait=0)
        self.assertEqual(mem.read(base + buf, 8), b"say hi\n\0")

    def test_pc_finder(self):
        from t5menu.cbuf import OVERFLOW_MSG, find_pc_cbuf_add_text
        base = 0x400000
        image = bytearray(0x10000)
        image[0x8000:0x8000 + len(OVERFLOW_MSG)] = OVERFLOW_MSG
        fn = 0x1000
        image[fn - 1] = 0xCC
        image[fn + 0x40:fn + 0x45] = b"\x68" + struct.pack("<I", base + 0x8000)  # push msg
        for site in (0x3000, 0x4000, 0x5000):  # three callers
            image[site:site + 5] = b"\xe8" + struct.pack("<i", fn - (site + 5))
        self.assertEqual(find_pc_cbuf_add_text(bytes(image), base), base + fn)

    def test_addbot_iw4x(self):
        from t5menu.cbuf import expand
        self.assertEqual(expand("addbot 4", "iw4x"), "spawnBot 4")
        with self.assertRaises(CbufError):
            expand("addbot", "mw2pc")
