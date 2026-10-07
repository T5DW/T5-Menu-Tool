import struct
import tempfile
import unittest
from pathlib import Path

from t5menu import xexpatch as x


def fake_xex(applied, compressed=True) -> bytes:
    """A minimal XEX2: one optional header (file format info) and the image data at 0x1000.
    applied: True / False for every patch, or a list with one bool per patch."""
    if isinstance(applied, bool):
        applied = [applied] * len(x.PATCHES)
    image = b"\x11" * 0x300
    for p, a in zip(x.PATCHES, applied):
        image += p.prefix + (p.patched if a else p.original) + b"\x22" * 0x40
    image += b"\x22" * 0x100
    hdr = bytearray(0x1000)
    hdr[:4] = b"XEX2"
    hdr[8:12] = struct.pack(">I", 0x1000)
    hdr[0x14:0x18] = struct.pack(">I", 1)
    hdr[0x18:0x20] = struct.pack(">II", 0x3FF, 0x100)
    if compressed:  # basic compression: two blocks, zeros between them dropped from the file
        a = 0x200
        hdr[0x100:0x118] = struct.pack(">IHHIIII", 8 + 16, 0, 1, a, 0x40, len(image) - a, 0)
        return bytes(hdr) + image[:a] + image[a:]
    hdr[0x100:0x108] = struct.pack(">IHH", 8, 0, 0)
    return bytes(hdr) + image


class XexPatchTest(unittest.TestCase):
    def test_patch_and_undo(self):
        for compressed in (True, False):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "default_mp.xex"
                p.write_bytes(fake_xex(False, compressed))
                self.assertEqual(x.status(p), "unpatched")
                self.assertTrue(x.patch(p, log=lambda *_: None))
                self.assertEqual(x.status(p), "patched")
                self.assertFalse(x.patch(p, log=lambda *_: None))
                self.assertEqual(p.with_name(p.name + x.BACKUP_SUFFIX).read_bytes(), fake_xex(False, compressed))
                self.assertEqual(p.read_bytes(), fake_xex(True, compressed))
                x.unpatch(p, log=lambda *_: None)
                self.assertEqual(x.status(p), "unpatched")

    def test_partly_patched(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "default_mp.xex"
            p.write_bytes(fake_xex([True, False, False, False]))  # what 0.5.1 left
            self.assertEqual(x.status(p), "partly patched")
            self.assertTrue(x.patch(p, log=lambda *_: None))
            self.assertEqual(p.read_bytes(), fake_xex(True))

    def test_folder_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "default_mp.xex").write_bytes(fake_xex(False))
            (Path(d) / "other.xex").write_bytes(b"XEX2" + bytes(0x200))
            logs = []
            ok = x.patch_game_folder(Path(d), log=logs.append)
            self.assertEqual([p.name for p in ok], ["default_mp.xex"])
            with self.assertRaises(x.XexPatchError):
                x.status(Path(d) / "other.xex")

    def test_real_xex(self):
        for p in Path("/mnt/project-files/uploads/hearth").glob("*default_mp*xex"):
            self.assertIn(x.status(p), ("patched", "unpatched"))
            return
        self.skipTest("no MW2 xex here")


if __name__ == "__main__":
    unittest.main()
