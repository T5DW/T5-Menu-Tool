"""Live inject: write edited MW2 scripts straight into the game running in Xenia.

The game compiles a script from its rawfile asset every time a map loads, reading the asset's
RawFile struct then: { char* name; u32 compressedLen; u32 len; char* buffer } (big-endian).
So a script changed in memory is used from the next match on, with no fastfile and no
restart. The tool finds each script's RawFile by its name (the name string, then the struct
that points at it), checks that its buffer really holds a script of that length, and writes
the new text into that same buffer:
  - as plain text when it fits (the buffer holds len + 1 bytes for a plain rawfile)
  - zlib compressed (the game inflates rawfiles whose compressedLen isn't 0)
  - compressed after dropping comments and indentation (gsc.minify)
A script bigger than its buffer can't be written this way (it still goes into patch_mp.ff),
and neither can a file the game doesn't have yet, since there is no asset to write into.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass

from .cbuf import CbufError, _read_some
from .gsc import minify

# Guest ranges that hold zone memory and asset pools: the virtual heaps, the xex image
# (pools live in its .data/.bss) and the physical heap (and its two mirrors).
SCAN_RANGES = [(0x40000000, 0x80000000), (0x82000000, 0x90000000), (0xA0000000, 0xC0000000)]
PHYS_VIEWS = (0xA0000000, 0xC0000000, 0xE0000000)
CHUNK = 1 << 24
MAX_LEN = 1 << 24


class LiveError(CbufError):
    pass


@dataclass
class RawRef:
    name: str
    struct_addr: int  # guest address of the RawFile struct
    clen: int
    len: int
    buffer: int

    @property
    def capacity(self) -> int:
        return self.clen if self.clen else self.len + 1


def _ranges(mem, membase):
    """(guest start, guest end) pieces of SCAN_RANGES the process has committed."""
    regions = list(mem.regions())
    for lo, hi in SCAN_RANGES:
        glo, ghi = membase + lo, membase + hi
        if not regions:  # no region list (e.g. a test double): try the whole range
            yield lo, hi
            continue
        for base, size in regions:
            a, b = max(base, glo), min(base + size, ghi)
            if a < b:
                yield a - membase, b - membase


def _scan(mem, membase, pattern: re.Pattern, overlap: int):
    """Yield (guest address, match) for pattern over the scan ranges."""
    for lo, hi in _ranges(mem, membase):
        for off in range(lo, hi, CHUNK):
            blob = _read_some(mem, membase + off, min(CHUNK + overlap, hi - off))
            for m in pattern.finditer(blob):
                if m.start() < CHUNK:
                    yield off + m.start(), m


def _aliases(addr: int) -> set[int]:
    """The same physical memory can be addressed through three views; pointers may use any."""
    out = {addr}
    for view in PHYS_VIEWS:
        if view <= addr < view + 0x20000000:
            phys = addr - view
            for v in PHYS_VIEWS:
                out |= {v + phys, v + phys + 0x1000, v + phys - 0x1000}
    return {a for a in out if 0 < a < 1 << 32}


def _read_buffer(mem, membase, ref: RawRef) -> bytes | None:
    try:
        if ref.clen:
            data = mem.read(membase + ref.buffer, ref.clen)
            d = zlib.decompressobj()
            text = d.decompress(data)
            return text if len(text) == ref.len and d.eof else None
        data = mem.read(membase + ref.buffer, ref.len + 1)
        return data[:-1] if data[-1:] == b"\0" and b"\0" not in data[:-1] else None
    except (CbufError, zlib.error):
        return None


def find_rawfiles(mem, membase: int, names, log=print) -> dict[str, list[RawRef]]:
    """{name: [RawRef...]} for every rawfile asset in memory with one of these names."""
    names = sorted(set(names), key=len, reverse=True)
    if not names:
        return {}
    pat = re.compile(b"\0(" + b"|".join(re.escape(n.encode("latin1")) for n in names) + b")\0")
    strings: dict[int, str] = {}
    for addr, m in _scan(mem, membase, pat, 512):
        strings[addr + 1] = m.group(1).decode("latin1")
    if not strings:
        return {}
    ptrs: dict[bytes, str] = {}
    for addr, name in strings.items():
        for a in _aliases(addr):
            ptrs[struct.pack(">I", a)] = name
    out: dict[str, list[RawRef]] = {}
    keys = list(ptrs)
    for i in range(0, len(keys), 500):  # keep each regex a reasonable size
        part = keys[i:i + 500]
        pat = re.compile(b"(?=(" + b"|".join(re.escape(k) for k in part) + b"))", re.S)
        for addr, m in _scan(mem, membase, pat, 16):
            if addr & 3:
                continue
            try:
                raw = mem.read(membase + addr, 16)
            except CbufError:
                continue
            _, clen, ln, buf = struct.unpack(">IIII", raw)
            if ln >= MAX_LEN or clen >= MAX_LEN or not buf:
                continue
            ref = RawRef(ptrs[m.group(1)], addr, clen, ln, buf)
            if _read_buffer(mem, membase, ref) is not None:
                lst = out.setdefault(ref.name, [])
                if all(r.struct_addr != addr for r in lst):
                    lst.append(ref)
    return out


def zlib_exact(text: bytes, size: int) -> tuple[bytes, bytes] | None:
    """A zlib stream exactly `size` bytes long that inflates to text plus trailing spaces.

    Filling the whole slot keeps its size the same for the next inject. The stream is the
    normal deflate data, sync-flushed to a byte boundary, then stored blocks of spaces (GSC
    ignores trailing whitespace), an empty final block and the adler32. None if text doesn't fit."""
    c = zlib.compressobj(9, zlib.DEFLATED, 15)
    head = c.compress(text) + c.flush(zlib.Z_SYNC_FLUSH)
    room = size - len(head) - 9  # final empty block (5) + adler32 (4)
    if room < 0:
        return None
    blocks, pad = [], 0
    while room > 0:
        if room < 5:  # too small for another block: grow the last one instead
            if not blocks:
                return None
            n = blocks[-1] + room
            if n > 0xFFFF:
                return None
            pad += room
            blocks[-1] = n
            break
        n = min(room - 5, 0xFFFF)
        blocks.append(n)
        pad += n
        room -= 5 + n
    body = b"".join(b"\x00" + struct.pack("<HH", n, n ^ 0xFFFF) + b" " * n for n in blocks)
    full = text + b" " * pad
    out = head + body + b"\x01\x00\x00\xff\xff" + struct.pack(">I", zlib.adler32(full))
    return out, full


def encode_for(ref: RawRef, text: bytes) -> tuple[int, int, bytes, str]:
    """(compressedLen, len, bytes to write, how) that fill ref's buffer. Raises LiveError if none fit.

    The slot is always filled to its full size (plain text padded with spaces, or an exact-size
    zlib stream), so editing and injecting again later still has the same room."""
    cap = ref.capacity
    if not ref.clen and len(text) + 1 <= cap:
        full = text + b" " * (cap - 1 - len(text))
        return 0, len(full), full + b"\0", "plain"
    for src, how in ((text, "compressed"), (minify(text), "compressed, comments removed")):
        got = zlib_exact(src, cap)
        if got:
            data, full = got
            return len(data), len(full), data, how
    need = len(zlib.compress(minify(text), 9)) + 9
    raise LiveError(f"{ref.name} is too big to write into the running game (needs about {need} bytes, "
                    f"its slot holds {cap}); it still goes into patch_mp.ff, so restart "
                    "the game to load it")


def write_rawfile(mem, membase: int, ref: RawRef, text: bytes) -> str:
    clen, length, data, how = encode_for(ref, text)
    mem.write(membase + ref.buffer, data)
    mem.write(membase + ref.struct_addr + 4, struct.pack(">II", clen, length))
    ref.clen, ref.len = clen, length
    return how


def inject(cb, files: dict[str, bytes], log=print) -> tuple[list[str], list[str]]:
    """Write files into the running MW2 (cb: an attached cbuf.Cbuf). Returns (written, skipped)."""
    log("live inject: looking for the scripts in the running game...")
    refs = find_rawfiles(cb.mem, cb.membase, files, log)
    done, skipped = [], []
    for name, text in files.items():
        if name not in refs:
            skipped.append(name)
            log(f"  - {name}: not in the running game (new file), it's only in patch_mp.ff")
            continue
        try:
            hows = {write_rawfile(cb.mem, cb.membase, ref, text) for ref in refs[name]}
            done.append(name)
            log(f"  + {name} ({', '.join(sorted(hows))})")
        except LiveError as e:
            skipped.append(name)
            log(f"  - {e}")
    return done, skipped
