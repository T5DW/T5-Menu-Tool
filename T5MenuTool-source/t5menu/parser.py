"""Parse menu lists out of a T5 Xbox 360 zone, keeping the zone offset of every field.

Load order mirrors the game's (and OpenAssetTools') zone loader: a struct is read whole,
then its pointers are followed in field order (depth first, embedded structs included).
Arrays of structs are read whole before any element's pointers are followed.
"""

import struct

from . import schema
from .schema import STRUCTS
from .zone import Reader, ZoneParseError, is_inline

SCALAR_SIZE = {"i32": 4, "u32": 4, "f32": 4, "u8": 1, "u64": 8, "pad": 1}
SCALAR_FMT = {"i32": ">i", "u32": ">I", "f32": ">f", "u8": ">B", "u64": ">Q"}


def struct_size(name):
    size = 0
    for fname, kind, target, count, _ in STRUCTS[name]:
        if kind == "u64":
            size = (size + 7) & ~7
        if kind in SCALAR_SIZE:
            size += SCALAR_SIZE[kind] * count
        elif kind == "struct":
            size += struct_size(target) * count
        else:
            size += 4 * count
    if name in schema.ALIGN8:
        size = (size + 7) & ~7
    return size


class ZStr(bytes):
    """An inline string read from the zone; pos is the zone offset of its first byte."""

    pos = 0


def zstr(data, pos):
    s = ZStr(data)
    s.pos = pos
    return s


class Node(dict):
    """A parsed struct. Values live in the dict; meta data in attributes."""

    def __init__(self, type_name, pos):
        super().__init__()
        self.type_name = type_name
        self.pos = pos
        self.offsets = {}  # field -> zone offset (or list of offsets for arrays)
        self.ptrs = {}  # pointer field -> raw pointer value (or list)


class MenuParser:
    def __init__(self, zone: bytes):
        self.zone = zone
        self.r = Reader(zone)
        self.strings = []  # (offset, length) of every inline string read, for patching
        self.trail = []  # (struct name, offset) in read order, for error reports
        self.pixel_ranges = []  # (offset, size) of inline image pixel data

    # ---- struct reading -------------------------------------------------------
    def read_struct(self, name):
        r = self.r
        start = r.pos
        node = Node(name, start)
        self.trail.append((name, start))
        for fname, kind, target, count, flag in STRUCTS[name]:
            if kind == "u64" and (r.pos - start) % 8:
                r.skip(8 - (r.pos - start) % 8)
            if kind == "pad":
                r.skip(count)
                continue
            if kind in SCALAR_FMT:
                size = SCALAR_SIZE[kind]
                offs, vals = [], []
                for _ in range(count):
                    r.need(size)
                    offs.append(r.pos)
                    vals.append(struct.unpack_from(SCALAR_FMT[kind], self.zone, r.pos)[0])
                    r.pos += size
                node[fname] = vals[0] if count == 1 else vals
                node.offsets[fname] = offs[0] if count == 1 else offs
            elif kind == "struct":
                subs = [self.read_struct(target) for _ in range(count)]
                for s in subs:
                    s.noload = flag == "noload"
                node[fname] = subs[0] if count == 1 else subs
            else:  # ptr
                offs = [r.pos + 4 * i for i in range(count)]
                vals = [r.u32() for _ in range(count)]
                node.ptrs[fname] = vals[0] if count == 1 else vals
                node.offsets[fname] = offs[0] if count == 1 else offs
                node[fname] = None if count == 1 else [None] * count
        if name in schema.ALIGN8 and (r.pos - start) % 8:
            r.skip(8 - (r.pos - start) % 8)
        expected = struct_size(name)
        if r.pos - start != expected:
            raise ZoneParseError(f"{name} size mismatch {r.pos - start} vs {expected}")
        return node

    def load(self, node, ctx):
        """Follow node's pointers in field order. ctx carries the enclosing item/listbox."""
        if getattr(node, "noload", False):
            return
        if node.type_name == "itemDef_s":
            ctx = dict(ctx, item=node)
        elif node.type_name == "listBoxDef_s":
            ctx = dict(ctx, listbox=node)
        for fname, kind, target, count, flag in STRUCTS[node.type_name]:
            if kind == "struct":
                v = node[fname]
                for sub in (v if isinstance(v, list) else [v]):
                    self.load(sub, ctx)
            elif kind == "ptr":
                if count == 1:
                    node[fname] = self.follow(node.ptrs[fname], target, node, ctx)
                else:
                    node[fname] = [self.follow(p, target, node, ctx) for p in node.ptrs[fname]]

    def follow(self, ptr, target, parent, ctx):
        if ptr == 0 or target == "never":
            return None
        if not is_inline(ptr):
            return ("ref", ptr)
        r = self.r
        if target == "string":
            start = r.pos
            s = r.cstring()
            self.strings.append((start, len(s)))
            return zstr(s, start)
        if target == "material":
            return self.material()
        if target == "menuArray":
            return self.ptr_array(parent["menuCount"], "menuDef_t", ctx)
        if target == "itemArray":
            return self.ptr_array(parent["itemCount"], "itemDef_s", ctx)
        if target == "animStateArray":
            return self.ptr_array(parent["animStateCount"], "animParamsDef_t", ctx)
        if target == "rpnArray":
            return self.rpn_array(parent["numRpn"])
        if target == "itemTypeData":
            t = parent["type"]
            if t in schema.TEXTDEF_TYPES:
                return self.one("textDef_s", ctx)
            if t in schema.IMAGEDEF_TYPES:
                return self.one("imageDef_s", ctx)
            if t in schema.BLANKBUTTON_TYPES:
                return self.one("focusItemDef_s", ctx)
            if t in schema.OWNERDRAW_TYPES:
                return self.one("ownerDrawDef_s", ctx)
            return ("ref", ptr)
        if target == "textTypeData":
            t = ctx["item"]["type"]
            if t in schema.FOCUS_TYPES:
                return self.one("focusItemDef_s", ctx)
            if t in schema.GAMEMSG_TYPES:
                return self.one("gameMsgDef_s", ctx)
            return ("ref", ptr)
        if target == "focusTypeData":
            t = ctx["item"]["type"]
            if t in schema.LISTBOX_TYPES:
                return self.one("listBoxDef_s", ctx)
            if t in schema.MULTI_TYPES:
                return self.one("multiDef_s", ctx)
            if t in schema.EDITFIELD_TYPES:
                return self.one("editFieldDef_s", ctx)
            if t in schema.ENUMDVAR_TYPES:
                return self.one("enumDvarDef_s", ctx)
            return ("ref", ptr)
        if target == "rowArray":
            return self.struct_array(parent["maxRows"], "MenuRow", ctx)
        if target == "cellArray":
            return self.struct_array(ctx["listbox"]["numColumns"], "MenuCell", ctx)
        if target == "char32":
            start = r.pos
            r.skip(32)
            return ("bytes", start, 32)
        if target == "cellString":
            n = parent["maxChars"]
            start = r.pos
            r.skip(n)
            return ("bytes", start, n)
        if target in STRUCTS:
            return self.one(target, ctx)
        raise ZoneParseError(f"unknown pointer target {target}")

    # Material / GfxImage are kept opaque: we only need their extent in the stream.
    # 360 layout (from ui_mp.ff): Material is 0x80 bytes, MaterialInfo.name first,
    # counts at +0x67..+0x69, then techniqueSet/textureTable/constantTable/stateBitsTable
    # pointers at +0x70. MaterialTextureDef is 16 bytes with the image pointer last.
    # An inline GfxImage is a 0x9C-byte header, then its name string when the name
    # pointer at +0x94 is inline, then pixel data (when the pointer at +0x48 is inline)
    # whose size is the u32 at +0x38.
    MATERIAL_SIZE = 0x80
    IMAGE_HEADER = 0x9C

    def material(self):
        r = self.r
        start = r.pos
        r.skip(self.MATERIAL_SIZE)
        z = self.zone
        name_ptr = struct.unpack_from(">I", z, start)[0]
        tex_count, const_count, sb_count = z[start + 0x67], z[start + 0x68], z[start + 0x69]
        tech, tex, const, sb = struct.unpack_from(">4I", z, start + 0x70)
        name = r.cstring() if is_inline(name_ptr) else None
        if is_inline(tech):
            raise ZoneParseError(f"inline technique set in material at 0x{start:X} is not supported")
        if is_inline(tex):
            defs = r.pos
            r.skip(16 * tex_count)
            for i in range(tex_count):
                semantic = z[defs + 16 * i + 7]
                img = struct.unpack_from(">I", z, defs + 16 * i + 12)[0]
                if is_inline(img):
                    if semantic == 11:
                        raise ZoneParseError(f"inline water texture at 0x{defs:X} is not supported")
                    self.image()
        if is_inline(const):
            r.skip(32 * const_count)
        if is_inline(sb):
            r.skip(8 * sb_count)
        return ("material", name.decode("latin1") if name else None, start, r.pos)

    def image(self):
        r = self.r
        start = r.pos
        r.skip(self.IMAGE_HEADER)
        size = struct.unpack_from(">I", self.zone, start + 0x38)[0]
        data_ptr = struct.unpack_from(">I", self.zone, start + 0x48)[0]
        name_ptr = struct.unpack_from(">I", self.zone, start + 0x94)[0]
        if is_inline(name_ptr):
            r.cstring()
        if is_inline(data_ptr):
            self.pixel_ranges.append((r.pos, size))
            r.skip(size)

    def one(self, name, ctx):
        node = self.read_struct(name)
        self.load(node, ctx)
        return node

    def ptr_array(self, count, name, ctx):
        r = self.r
        ptrs = [r.u32() for _ in range(count)]
        out = []
        for p in ptrs:
            out.append(self.one(name, ctx) if is_inline(p) else (None if p == 0 else ("ref", p)))
        return out

    def struct_array(self, count, name, ctx):
        nodes = [self.read_struct(name) for _ in range(count)]
        for n in nodes:
            self.load(n, ctx)
        return nodes

    def rpn_array(self, count):
        r = self.r
        entries = []
        for _ in range(count):
            pos = r.pos
            entries.append({"pos": pos, "type": r.i32(), "dataType": r.i32(), "value": r.u32()})
        for e in entries:
            if e["type"] == 0 and e["dataType"] == 2 and is_inline(e["value"]):
                start = r.pos
                s = r.cstring()
                self.strings.append((start, len(s)))
                e["string"] = zstr(s, start)
        return entries

    # ---- entry point -----------------------------------------------------------
    def parse_menulist(self, pos):
        self.r.pos = pos
        node = self.read_struct("MenuList")
        self.load(node, {})
        node.end = self.r.pos
        return node
