"""Black Ops 1 (T5) Xbox 360 fastfile container: unpack to a raw zone and pack it back.

Layout (reversed from CoDMP_systemlink.xex, DB loader around 0x82264350):

    0x000  "IWff0100" (signed) or "IWffu100" (unsigned)
    0x008  u32 BE version, must be 0x1D7
    0x00C  u32 BE unknown (2 in retail ui_mp.ff), kept as-is
    0x010  "IWffs101" auth header magic (present in both signed and unsigned files)
    0x018  u32 reserved
    0x01C  char zoneName[32]      must match the zone the game asks for
    0x03C  u8   rsaSig[256]       only verified for signed files
    0x13C  blocks: [u32 BE size][size bytes], ... , u32 0 terminator, zero padding

Blocks are dealt round-robin to 4 streams. Each stream is an independent zlib stream,
sync-flushed after every block. The decompressed zone is the concatenation of every
block's output in file order: block 0 carries the 0x24-byte XFile header, later blocks
carry 0x7FC0 bytes each, and the last 4 blocks only finish their streams.

Signed files XOR each stream byte n with pad[n & 0x3FF], where the 1 KB pad is
AES-256-CTR(key, IV = zoneName[0:16], little-endian counter) applied to zeros.
Unsigned files skip the XOR and the RSA check at the end of the load.

Retail (title-updated) files use a newer layout, version 0x1D9 with "PHEEBs71" at 0x10. The header
size and block framing are the same, but:

    - every block is its own raw deflate stream (0xBFC0 bytes out, block 0 = the 0x24-byte XFile
      header), with no trailing finish blocks;
    - each block is encrypted with Salsa20 (same 32-byte key). Blocks are dealt round-robin to
      4 streams; each stream's IV is the first 8 bytes of its current 20-byte slot in a ring of
      50 slots per stream, laid out [slot][stream]. The ring starts filled with the zone name
      (each character repeated 4 times). After a block, the stream moves to its next slot and
      XORs the SHA-1 of the decrypted (still compressed) block into it.

The RSA signature covers that ring, so an edited retail file can never match its signature.
"""

import struct
import zlib

import hashlib

try:
    from Crypto.Cipher import AES, Salsa20
except ImportError:  # pycryptodome is optional; fall back to the pure-Python ciphers
    AES = Salsa20 = None

MAGIC_SIGNED = b"IWff0100"
MAGIC_UNSIGNED = b"IWffu100"
AUTH_MAGIC = b"IWffs101"
VERSION = 0x1D7
RETAIL_VERSION = 0x1D9
RETAIL_AUTH_MAGIC = b"PHEEBs71"
RETAIL_CHUNK_SIZE = 0xBFC0
RETAIL_RING = 50
HEADER_SIZE = 0x13C
STREAM_COUNT = 4
CHUNK_SIZE = 0x7FC0
XFILE_SIZE = 0x24
KEY = bytes.fromhex("1ac1d12d527c59b40eca619120ff8217ccff09cd16896f81b829c7f52793405d")


class FastFileError(ValueError):
    pass


def make_pad(zone_name: bytes) -> bytes:
    iv = bytearray(zone_name[:16].ljust(16, b"\0"))
    if AES is not None:
        aes = AES.new(KEY, AES.MODE_ECB)
    else:
        from .aes import AESEncryptor
        aes = AESEncryptor(KEY)
    out = bytearray()
    for _ in range(0x400 // 16):
        out += aes.encrypt(bytes(iv))
        for j in range(16):
            iv[j] = (iv[j] + 1) & 0xFF
            if iv[j]:
                break
    return bytes(out)


def _xor(data: bytes, pad: bytes, start: int) -> bytes:
    # Expand the pad to cover the block, then XOR as big integers (fast in CPython).
    phase = start & 0x3FF
    reps = (phase + len(data)) // 0x400 + 1
    stream = (pad * reps)[phase : phase + len(data)]
    n = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(n, "big")


def _salsa20(key, iv, data):
    if Salsa20 is not None:
        return Salsa20.new(key, iv).encrypt(data)
    from .salsa20 import salsa20_xor
    return salsa20_xor(key, iv, data)


class _IvChain:
    """Per-stream Salsa20 IVs of a retail fastfile (see the module docstring)."""

    def __init__(self, zone_name: bytes):
        name = zone_name.split(b"\0")[0][:31] or b"\0"
        ring = bytearray(RETAIL_RING * STREAM_COUNT * 20)
        for i in range(0, len(ring), 4):
            ring[i : i + 4] = bytes([name[(i // 4) % len(name)]]) * 4
        self.ring = ring
        self.slot = [0] * STREAM_COUNT

    def _offset(self, stream):
        return (self.slot[stream] * STREAM_COUNT + stream) * 20

    def iv(self, stream):
        o = self._offset(stream)
        return bytes(self.ring[o : o + 8])

    def advance(self, stream, plain_block):
        digest = hashlib.sha1(plain_block).digest()
        self.slot[stream] = (self.slot[stream] + 1) % RETAIL_RING
        o = self._offset(stream)
        for i in range(20):
            self.ring[o + i] ^= digest[i]


class FastFile:
    def __init__(self, header: bytes, zone: bytes, retail_blocks=None):
        self.header = bytearray(header)
        self.zone = zone
        # Retail files: the original compressed blocks with the zone ranges they inflate to,
        # so a rebuild can keep every untouched block byte for byte.
        self.retail_blocks = retail_blocks
        self.original_zone = zone

    @property
    def retail(self) -> bool:
        return self.retail_blocks is not None

    @property
    def signed(self) -> bool:
        return bytes(self.header[:8]) == MAGIC_SIGNED

    @property
    def zone_name(self) -> str:
        return bytes(self.header[0x1C:0x3C]).split(b"\0")[0].decode("latin1")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.parse(f.read())

    @classmethod
    def parse(cls, data: bytes):
        if len(data) < HEADER_SIZE:
            raise FastFileError("file is too small to be a fastfile")
        magic = data[:8]
        if magic not in (MAGIC_SIGNED, MAGIC_UNSIGNED):
            raise FastFileError(f"not a Bo1 Xbox 360 fastfile (magic {magic!r})")
        version = struct.unpack(">I", data[8:12])[0]
        if version == RETAIL_VERSION and data[0x10:0x18] == RETAIL_AUTH_MAGIC:
            return cls._parse_retail(data)
        if version != VERSION:
            raise FastFileError(f"fastfile version 0x{version:X}, expected 0x{VERSION:X} or "
                                f"0x{RETAIL_VERSION:X} (Bo1 Xbox 360)")
        if data[0x10:0x18] != AUTH_MAGIC:
            raise FastFileError(f"unexpected auth header magic {data[0x10:0x18]!r}")
        header = data[:HEADER_SIZE]
        signed = magic == MAGIC_SIGNED
        pad = make_pad(data[0x1C:0x3C]) if signed else None
        counters = [0] * STREAM_COUNT
        inflaters = [zlib.decompressobj() for _ in range(STREAM_COUNT)]
        out = []
        off = HEADER_SIZE
        index = 0
        while off + 4 <= len(data):
            size = struct.unpack(">I", data[off : off + 4])[0]
            if size == 0:
                break
            off += 4
            block = data[off : off + size]
            if len(block) != size:
                raise FastFileError(f"truncated block {index} at 0x{off - 4:X}")
            off += size
            s = index % STREAM_COUNT
            if pad is not None:
                block = _xor(block, pad, counters[s])
            counters[s] += size
            try:
                out.append(inflaters[s].decompress(block))
            except zlib.error as e:
                raise FastFileError(f"block {index} (stream {s}) failed to inflate: {e}") from None
            index += 1
        zone = b"".join(out)
        if len(zone) < XFILE_SIZE:
            raise FastFileError("zone is empty")
        expected = struct.unpack(">I", zone[:4])[0] + XFILE_SIZE
        if expected != len(zone):
            raise FastFileError(f"zone size mismatch: XFile says {expected}, got {len(zone)}")
        return cls(header, zone)

    @classmethod
    def _parse_retail(cls, data: bytes):
        if data[:8] != MAGIC_SIGNED:
            raise FastFileError("unsigned retail fastfiles are not supported")
        header = data[:HEADER_SIZE]
        chain = _IvChain(data[0x1C:0x3C])
        out = []
        blocks = []
        pos = 0
        off = HEADER_SIZE
        index = 0
        while off + 4 <= len(data):
            size = struct.unpack(">I", data[off : off + 4])[0]
            if size == 0:
                break
            block = data[off + 4 : off + 4 + size]
            if len(block) != size:
                raise FastFileError(f"truncated block {index} at 0x{off:X}")
            off += 4 + size
            s = index % STREAM_COUNT
            plain = _salsa20(KEY, chain.iv(s), block)
            chain.advance(s, plain)
            inflater = zlib.decompressobj(-15)
            try:
                chunk = inflater.decompress(plain)
            except zlib.error as e:
                raise FastFileError(f"block {index} failed to inflate: {e}") from None
            if not inflater.eof:
                raise FastFileError(f"block {index} is not a complete deflate stream")
            out.append(chunk)
            blocks.append((pos, len(chunk), plain))
            pos += len(chunk)
            index += 1
        zone = b"".join(out)
        if len(zone) < XFILE_SIZE:
            raise FastFileError("zone is empty")
        expected = struct.unpack(">I", zone[:4])[0] + XFILE_SIZE
        if expected != len(zone):
            raise FastFileError(f"zone size mismatch: XFile says {expected}, got {len(zone)}")
        ff = cls(header, zone, blocks)
        ff.retail_tail = len(data) - off  # zero terminator + padding, kept as-is
        return ff

    def _build_retail(self, level: int) -> bytes:
        zone = bytearray(self.zone)
        struct.pack_into(">I", zone, 0, len(zone) - XFILE_SIZE)
        old = {start: (size, plain) for start, size, plain in self.retail_blocks}
        chunks = [(0, XFILE_SIZE)]
        for i in range(XFILE_SIZE, len(zone), RETAIL_CHUNK_SIZE):
            chunks.append((i, min(RETAIL_CHUNK_SIZE, len(zone) - i)))
        original = self.original_zone
        chain = _IvChain(bytes(self.header[0x1C:0x3C]))
        out = bytearray(self.header)
        for index, (start, size) in enumerate(chunks):
            data = bytes(zone[start : start + size])
            kept = old.get(start)
            if kept and kept[0] == size and original[start : start + size] == data:
                plain = kept[1]
            else:
                c = zlib.compressobj(level, zlib.DEFLATED, -15)
                plain = c.compress(data) + c.flush()
            s = index % STREAM_COUNT
            out += struct.pack(">I", len(plain)) + _salsa20(KEY, chain.iv(s), plain)
            chain.advance(s, plain)
        out.extend(bytes(max(self.retail_tail, 4)))
        return bytes(out)

    def build(self, signed: bool = False, level: int = 9) -> bytes:
        """Repack. Retail files are always rebuilt in their own (signed, Salsa20) format: blocks
        whose data did not change are kept byte for byte and re-encrypted, since the IVs chain.

        Repack the zone. signed=False writes an IWffu100 file the game loads without
        decryption or an RSA check. signed=True re-encrypts but keeps the original
        signature, which will not match edited data."""
        if self.retail:
            return self._build_retail(level)
        zone = bytearray(self.zone)
        struct.pack_into(">I", zone, 0, len(zone) - XFILE_SIZE)
        header = bytearray(self.header)
        header[:8] = MAGIC_SIGNED if signed else MAGIC_UNSIGNED
        pad = make_pad(bytes(header[0x1C:0x3C])) if signed else None

        chunks = [bytes(zone[:XFILE_SIZE])]
        for i in range(XFILE_SIZE, len(zone), CHUNK_SIZE):
            chunks.append(bytes(zone[i : i + CHUNK_SIZE]))

        deflaters = [zlib.compressobj(level) for _ in range(STREAM_COUNT)]
        counters = [0] * STREAM_COUNT
        out = bytearray(header)

        def emit(stream, payload):
            if pad is not None:
                payload = _xor(payload, pad, counters[stream])
            counters[stream] += len(payload)
            out.extend(struct.pack(">I", len(payload)))
            out.extend(payload)

        for i, chunk in enumerate(chunks):
            s = i % STREAM_COUNT
            emit(s, deflaters[s].compress(chunk) + deflaters[s].flush(zlib.Z_SYNC_FLUSH))
        # Finish the streams in the order the game reads them next.
        start = len(chunks) % STREAM_COUNT
        for k in range(STREAM_COUNT):
            s = (start + k) % STREAM_COUNT
            emit(s, deflaters[s].flush(zlib.Z_FINISH))
        out.extend(b"\0\0\0\0")
        while len(out) % 0x40:
            out.append(0)
        return bytes(out)
