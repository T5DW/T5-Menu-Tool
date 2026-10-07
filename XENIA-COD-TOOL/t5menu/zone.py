"""Sequential reader for a T5 Xbox 360 zone (big-endian, packed stream, 32-bit pointers).

Pointer values in the stream:
    0           null
    0xFFFFFFFF  data follows inline in the stream
    0xFFFFFFFE  "insert": an alias slot, data follows inline
    other       offset into an already loaded block (not followed here)
"""

import struct

PTR_NULL = 0
PTR_INLINE = 0xFFFFFFFF
PTR_INSERT = 0xFFFFFFFE


class ZoneParseError(ValueError):
    pass


def is_inline(ptr: int) -> bool:
    return ptr in (PTR_INLINE, PTR_INSERT)


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def need(self, n):
        if self.pos + n > len(self.data):
            raise ZoneParseError(f"read past end of zone at 0x{self.pos:X}")

    def u32(self):
        self.need(4)
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self):
        self.need(4)
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self):
        self.need(4)
        v = struct.unpack_from(">f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u8(self):
        self.need(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u64(self):
        self.need(8)
        v = struct.unpack_from(">Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def skip(self, n):
        self.need(n)
        self.pos += n

    def cstring(self):
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise ZoneParseError(f"unterminated string at 0x{self.pos:X}")
        s = self.data[self.pos : end]
        self.pos = end + 1
        return s


def read_header(zone: bytes):
    """Returns (xfile dict, script strings, asset list [(type, headerPtr)], data start offset)."""
    r = Reader(zone)
    size, external = r.u32(), r.u32()
    blocks = [r.u32() for _ in range(7)]
    string_count, string_ptr = r.u32(), r.u32()
    asset_count, asset_ptr = r.u32(), r.u32()
    strings = []
    if string_count and is_inline(string_ptr):
        ptrs = [r.u32() for _ in range(string_count)]
        for p in ptrs:
            strings.append(r.cstring().decode("latin1") if is_inline(p) else None)
    assets = []
    if asset_count and is_inline(asset_ptr):
        assets = [(r.u32(), r.u32()) for _ in range(asset_count)]
    xfile = {"size": size, "externalSize": external, "blockSize": blocks}
    return xfile, strings, assets, r.pos
