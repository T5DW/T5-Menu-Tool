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
"""

import struct
import zlib

try:
    from Crypto.Cipher import AES
except ImportError:  # pycryptodome is optional; fall back to the pure-Python cipher
    AES = None

MAGIC_SIGNED = b"IWff0100"
MAGIC_UNSIGNED = b"IWffu100"
AUTH_MAGIC = b"IWffs101"
VERSION = 0x1D7
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


class FastFile:
    def __init__(self, header: bytes, zone: bytes):
        self.header = bytearray(header)
        self.zone = zone

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
        if version != VERSION:
            raise FastFileError(f"fastfile version 0x{version:X}, expected 0x{VERSION:X} (Bo1 Xbox 360)")
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

    def build(self, signed: bool = False, level: int = 9) -> bytes:
        """Repack the zone. signed=False writes an IWffu100 file the game loads without
        decryption or an RSA check. signed=True re-encrypts but keeps the original
        signature, which will not match edited data."""
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
