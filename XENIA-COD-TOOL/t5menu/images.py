"""Custom background images for new menus.

The stock menus load their full-screen backgrounds as inline materials, e.g.
menu_mp_background_main2: a 0x80-byte Material, its name, one texture def, a 0x9C-byte
GfxImage header (Xenos texture fetch constant inside, little-endian dwords), and
1024x1024 DXT1 pixels in the Xbox 360's tiled layout with 16-bit byte swapping. A custom
image copies that template and swaps in new names and new pixels.

Needs Pillow to read the picture (pip install pillow).
"""

import struct
import zlib

TEMPLATE = "menu_mp_background_main2"
SIZE = 1024  # template texture is 1024x1024 DXT1 (no alpha)
PIXEL_BYTES = SIZE * SIZE // 2
IMAGE_HEADER = 0x9C
MATERIAL_SIZE = 0x80


class ImageError(ValueError):
    pass


def tiled_offset(x, y, w, bpb):
    """Xenos XGAddress2DTiledOffset: (x, y) and width w in blocks, bpb bytes per block.
    Returns the block index in the tiled layout."""
    aw = (w + 31) & ~31
    lb = (bpb >> 2) + ((bpb >> 1) >> (bpb >> 2))
    macro = ((x >> 5) + (y >> 5) * (aw >> 5)) << (lb + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << lb
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + lb)) + ((y & 1) << 4)
    return (((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7)
            + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> lb


def _565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _rgb(c):
    return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)


def dxt1_block(px):
    """16 (r, g, b) pixels -> 8-byte DXT1 block (little-endian, 4-colour mode)."""
    # endpoints: the two pixels farthest apart along the box's main diagonal
    lo = [min(p[i] for p in px) for i in range(3)]
    hi = [max(p[i] for p in px) for i in range(3)]
    inset = [(h - l) >> 4 for l, h in zip(lo, hi)]
    lo = [l + d for l, d in zip(lo, inset)]
    hi = [h - d for h, d in zip(hi, inset)]
    # pick the diagonal that fits the pixels best (flip channels that anti-correlate)
    cr = sum((p[0] - (lo[0] + hi[0]) / 2) * (p[1] - (lo[1] + hi[1]) / 2) for p in px)
    cb = sum((p[2] - (lo[2] + hi[2]) / 2) * (p[1] - (lo[1] + hi[1]) / 2) for p in px)
    if cr < 0:
        lo[0], hi[0] = hi[0], lo[0]
    if cb < 0:
        lo[2], hi[2] = hi[2], lo[2]
    c0, c1 = _565(*hi), _565(*lo)
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    if c0 < c1:
        c0, c1 = c1, c0
    a, b = _rgb(c0), _rgb(c1)
    pal = [a, b, tuple((2 * x + y) // 3 for x, y in zip(a, b)), tuple((x + 2 * y) // 3 for x, y in zip(a, b))]
    bits = 0
    for i, p in enumerate(px):
        best = min(range(4), key=lambda k: (p[0] - pal[k][0]) ** 2 + (p[1] - pal[k][1]) ** 2 + (p[2] - pal[k][2]) ** 2)
        bits |= best << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def encode_dxt1_tiled(img):
    """PIL RGB image (SIZE x SIZE) -> 360 texture bytes (tiled, 16-bit swapped DXT1)."""
    data = img.tobytes()
    bw = SIZE // 4
    out = bytearray(PIXEL_BYTES)
    for by in range(bw):
        rows = [data[(by * 4 + r) * SIZE * 3:(by * 4 + r + 1) * SIZE * 3] for r in range(4)]
        for bx in range(bw):
            px = []
            for r in range(4):
                row = rows[r]
                o = bx * 12
                px += [(row[o], row[o + 1], row[o + 2]), (row[o + 3], row[o + 4], row[o + 5]),
                       (row[o + 6], row[o + 7], row[o + 8]), (row[o + 9], row[o + 10], row[o + 11])]
            blk = dxt1_block(px)
            o = tiled_offset(bx, by, bw, 8) * 8
            out[o:o + 8] = bytes((blk[1], blk[0], blk[3], blk[2], blk[5], blk[4], blk[7], blk[6]))
    return bytes(out)


def load_picture(path):
    try:
        from PIL import Image
    except ImportError:
        raise ImageError("custom images need Pillow: run  pip install pillow") from None
    try:
        img = Image.open(path).convert("RGB")
    except OSError as e:
        raise ImageError(f"can't read image {path}: {e}") from None
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def material_bytes(zone, template, name: bytes, pixels: bytes):
    """Stream bytes of a new inline material + image, modelled on the template material
    (start, end) in the zone. Returns the bytes."""
    start, end = template
    z = zone
    name_ptr = struct.unpack_from(">I", z, start)[0]
    if name_ptr != 0xFFFFFFFF or z[start + 0x67] != 1:
        raise ImageError("unexpected template material")
    p = z.index(b"\0", start + MATERIAL_SIZE) + 1  # after the template's name
    texdef = bytearray(z[p:p + 16])
    img = bytearray(z[p + 16:p + 16 + IMAGE_HEADER])
    size = struct.unpack_from(">I", img, 0x38)[0]
    if size != PIXEL_BYTES or struct.unpack_from(">I", img, 0x48)[0] != 0xFFFFFFFF:
        raise ImageError("unexpected template image")
    img_name_inline = struct.unpack_from(">I", img, 0x94)[0] == 0xFFFFFFFF
    q = p + 16 + IMAGE_HEADER
    if img_name_inline:
        q = z.index(b"\0", q) + 1
    statebits = z[q + size:end]
    if len(statebits) != 8:
        raise ImageError("unexpected template layout")
    # new image: its own (inline) name, and a different hash so it isn't taken for the stock one
    struct.pack_into(">I", img, 0x94, 0xFFFFFFFF)
    struct.pack_into(">I", img, 0x98, zlib.crc32(name) or 1)
    out = bytearray(z[start:start + MATERIAL_SIZE])
    out += name + b"\0"
    out += texdef
    out += img + name + b"\0"
    out += pixels
    out += statebits
    return bytes(out)
