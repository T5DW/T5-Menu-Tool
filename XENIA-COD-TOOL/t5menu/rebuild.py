"""Add new menus to a T5 Xbox 360 ui_mp zone without moving any existing data.

Why this works:

* Pointers between assets are "refs": offsets into the virtual memory block the zone is
  loaded into. Refs only ever point backwards, so data appended at the END of the zone can
  reference anything before it, and nothing before it has to change.
* The asset list itself lives at the start of the virtual block. Adding one entry (8 bytes)
  would push every later virtual offset (and so every ref in the zone) back by 8. We cancel
  that out by shortening the first localized string by 8 characters: from the end of that
  string on, the virtual layout is byte-for-byte what it was.
* The new asset is a menu list named like the stock one ("ui_mp/menus.txt"), which the
  game loads by name. The stock list is renamed in place ("ui_mp/menuz.txt"). The new
  list points at every stock menu (by ref to its slot in the stock list, the same trick
  the stock zone uses for its small gamesetup_systemlink list) and then holds the new
  menus inline.
* A new menu is written by walking a parsed menu tree, so it can be a clone of a stock
  menu with any changes: longer strings, more or fewer items, new actions. Refs inside a
  clone still point at the stock data, which stays where it was.

Menu headers (menuDef_t) and the MenuList header are loaded into the temp block and copied
into the menu pool, so they take no virtual space; inline materials and images are assets
too (their headers go to temp, image pixels to the physical block). VirtualMap models this
and is checked against the refs in the zone before anything is written.
"""

import bisect
import collections
import copy
import struct

from . import schema
from .fastfile import XFILE_SIZE
from .menufile import MENULIST_TYPE, find_menu_lists, menu_entries
from .parser import SCALAR_FMT, MenuParser, Node, ZStr, struct_size, zstr
from .schema import STRUCTS
from .zone import PTR_INLINE, ZoneParseError, is_inline, read_header

LOCALIZE_TYPE = 24
XFILE_ASSET_COUNT = 0x2C
XFILE_BLOCKS = 0x08
VIRTUAL_BLOCK = 4
PHYSICAL_BLOCK = 5
PIXEL_ALIGN = 4096
TEMP_STRUCTS = {"MenuList", "menuDef_t"}
STOCK_LIST = b"ui_mp/menus.txt"
RENAMED_LIST = b"ui_mp/menuz.txt"
# The first localized string gives up 8 characters. Known strings get a hand-written
# shorter version, anything else is trimmed at the end.
SHORTER = {
    b"You must have at least one Ops and one Communist class":
    b"Need at least one Ops and one Communist class.",
}


class RebuildError(ValueError):
    pass


def ref(voff):
    """Encode a virtual-block offset as a zone pointer."""
    return ((VIRTUAL_BLOCK << 29) | voff) + 1


def ref_target(ptr):
    return (ptr - 1) & 0x1FFFFFFF


# ---------------------------------------------------------------------------------
# virtual layout


class _MappingParser(MenuParser):
    """MenuParser that records every allocation: (stream pos, alignment, block, tag)."""

    def __init__(self, zone):
        super().__init__(zone)
        self.segs = []
        self.depth = 0

    def ev(self, align, tag, block="v"):
        self.segs.append((self.r.pos, align, block, tag))

    def read_struct(self, name):
        if self.depth == 0:  # embedded structs are part of their parent's allocation
            self.ev(8 if name in schema.ALIGN8 else 4, name, "t" if name in TEMP_STRUCTS else "v")
        self.depth += 1
        try:
            return super().read_struct(name)
        finally:
            self.depth -= 1

    def ptr_array(self, count, name, ctx):
        if count:
            self.ev(4, "ptrs")
        return super().ptr_array(count, name, ctx)

    def rpn_array(self, count):
        if count:
            self.ev(4, "rpn")
        r = self.r
        entries = []
        for _ in range(count):
            pos = r.pos
            entries.append({"pos": pos, "type": r.i32(), "dataType": r.i32(), "value": r.u32()})
        for e in entries:
            if e["type"] == 0 and e["dataType"] == 2 and is_inline(e["value"]):
                start = r.pos
                self.ev(1, "string")
                s = r.cstring()
                self.strings.append((start, len(s)))
                e["string"] = zstr(s, start)
        return entries

    def follow(self, ptr, target, parent, ctx):
        if ptr and is_inline(ptr) and target in ("string", "char32", "cellString"):
            self.ev(1, target)
        return super().follow(ptr, target, parent, ctx)

    def material(self):
        r, z = self.r, self.zone
        start = r.pos
        self.ev(4, "material", "t")
        r.skip(self.MATERIAL_SIZE)
        name_ptr = struct.unpack_from(">I", z, start)[0]
        tex_count, const_count, sb_count = z[start + 0x67], z[start + 0x68], z[start + 0x69]
        tech, tex, const, sb = struct.unpack_from(">4I", z, start + 0x70)
        name = None
        if is_inline(name_ptr):
            self.ev(1, "matname")
            name = r.cstring()
        if is_inline(tex):
            defs = r.pos
            self.ev(4, "texdefs")
            r.skip(16 * tex_count)
            for i in range(tex_count):
                if is_inline(struct.unpack_from(">I", z, defs + 16 * i + 12)[0]):
                    self.image()
        if is_inline(const):
            self.ev(16, "consts")
            r.skip(32 * const_count)
        if is_inline(sb):
            self.ev(4, "statebits")
            r.skip(8 * sb_count)
        return ("material", name.decode("latin1") if name else None, start, r.pos)

    def image(self):
        r, z = self.r, self.zone
        start = r.pos
        self.ev(4, "image", "t")
        r.skip(self.IMAGE_HEADER)
        size = struct.unpack_from(">I", z, start + 0x38)[0]
        data_ptr = struct.unpack_from(">I", z, start + 0x48)[0]
        name_ptr = struct.unpack_from(">I", z, start + 0x94)[0]
        if is_inline(name_ptr):
            self.ev(1, "imgname")
            r.cstring()
        if is_inline(data_ptr):
            self.ev(4096, "pixels", "p")
            self.pixel_ranges.append((r.pos, size))
            r.skip(size)
            self.ev(1, "after-pixels")


class VirtualMap:
    """Maps zone (stream) offsets inside the menu lists to virtual-block offsets."""

    def __init__(self, zone):
        self.zone = zone
        xfile, _, assets, data_start = read_header(zone)
        self.lists = self._parse(zone)
        self.segs.sort(key=lambda e: e[0])
        self.pos = [s[0] for s in self.segs]
        self.base = self._fit()
        self.table = self._build(self.base)
        self.check()

    def _parse(self, zone):
        """Map the stock lists (back to back); a list the rebuilder appended earlier is kept
        apart in self.extra."""
        found = find_menu_lists(zone)
        if any(n.type_name != "MenuList" for n in found):
            raise RebuildError("standalone menu assets are not supported")
        run = 1
        while run < len(found) and found[run].pos == found[run - 1].end:
            run += 1
        self.extra = None
        if run < len(found):
            extra = found[run]
            if run != len(found) - 1 or bytes(extra["name"] or b"") != STOCK_LIST or extra.end != len(zone):
                raise RebuildError("unexpected menu list layout")
            self.extra = extra
        self.extra_pixels = (0, 0)  # (bytes, images) of pixel data in the appended list
        if self.extra is not None:
            ep = MenuParser(zone)
            ep.parse_menulist(self.extra.pos)
            self.extra_pixels = (sum(n for _, n in ep.pixel_ranges), len(ep.pixel_ranges))
        p = _MappingParser(zone)
        out = []
        for node in found[:run]:
            out.append(p.parse_menulist(node.pos))
        self.segs = p.segs
        return out

    def _build(self, v0):
        v, out, prev, block = v0, [], None, "v"
        for p, align, b, _ in self.segs:
            if prev is not None and block == "v":
                v += p - prev
            prev, block = p, b
            if b == "v":
                v += -v % align
                out.append(v)
            else:
                out.append(None)
        return out

    def v_of(self, pos, table=None):
        table = table or self.table
        i = bisect.bisect_right(self.pos, pos) - 1
        if i < 0 or table[i] is None:
            raise RebuildError(f"zone offset 0x{pos:X} is not in the virtual block")
        return table[i] + (pos - self.pos[i])

    def _own_filename_truth(self):
        """(stream pos, true virtual offset) for menus whose inline filename string is the
        target of most of the refs in that menu: the game shares one copy per file."""
        out = []
        for _, _, m in menu_entries(self.lists):
            inline, refs = [], collections.Counter()

            def walk(x):
                if isinstance(x, Node):
                    for k, v in x.items():
                        if k == "filename":
                            if isinstance(v, ZStr):
                                inline.append(v)
                            elif isinstance(v, tuple) and v[0] == "ref":
                                refs[v[1]] += 1
                        else:
                            walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)

            walk(m)
            if inline and refs:
                top, count = refs.most_common(1)[0]
                if count >= 3:
                    out.append((inline[0].pos, ref_target(top)))
        return out

    def _fit(self):
        """Find the virtual offset of the first list: line up the filename strings first, then
        pick among the candidates (which differ only before the first realignment) the one
        that puts the most refs on allocation starts."""
        truth = self._own_filename_truth()
        if len(truth) < 10:
            raise RebuildError("not enough reference points to map the zone")
        cands = set()
        for r in range(16):
            t = self._build(r)
            err, n = collections.Counter(tv - self.v_of(p, t) for p, tv in truth).most_common(1)[0]
            if n >= 0.8 * len(truth):
                cands.add(r + err)
        if not cands:
            raise RebuildError("could not work out the zone's memory layout")
        targets = self._ref_targets()
        best = max(cands, key=lambda c: (self._hits(self._build(c), targets)[0], -c))
        return best

    def _ref_targets(self):
        targets = []

        def walk(x):
            if isinstance(x, Node):
                for v in x.values():
                    if isinstance(v, tuple) and v and v[0] == "ref":
                        targets.append(v[1])
                    else:
                        walk(v)
            elif isinstance(x, list):
                for v in x:
                    if isinstance(v, dict) and "dataType" in v and "type" in v:
                        if v["type"] == 0 and v["dataType"] == 2 and v["value"] and not is_inline(v["value"]):
                            targets.append(v["value"])
                    elif isinstance(v, tuple) and v and v[0] == "ref":
                        targets.append(v[1])
                    else:
                        walk(v)

        for node in self.lists:
            walk(node)
        return [ref_target(t) for t in targets if t >> 29 == VIRTUAL_BLOCK]

    def _hits(self, table, targets):
        starts = set(v for v in table if v is not None)
        lo = min(v for v in table if v is not None)
        inside = [t for t in targets if t >= lo]
        return sum(t in starts for t in inside), len(inside)

    def check(self):
        """Refs into the menu lists must land on allocations (the few that don't point at
        pointer slots inside structs, e.g. an inline material's slot), and the stock zone's
        own slot refs (its small gamesetup_systemlink list) must match ours."""
        good, total = self._hits(self.table, self._ref_targets())
        self.hit_rate = good / max(total, 1)
        if total < 1000 or self.hit_rate < 0.9:
            raise RebuildError(f"memory map check failed ({good}/{total} refs line up)")
        big = max(range(len(self.lists)), key=lambda i: self.lists[i]["menuCount"])
        slots = {self.slot_ref(big, i) for i in range(self.lists[big]["menuCount"])}
        for i, node in enumerate(self.lists):
            if i == big or not isinstance(node["menus"], list):
                continue
            for m in node["menus"]:
                if isinstance(m, tuple) and m[0] == "ref" and m[1] not in slots:
                    raise RebuildError(f"menu slot ref 0x{m[1]:08X} doesn't match the memory map")

    def material_slots(self):
        """{material name (or name ref for nameless ones): ref to a pointer slot that holds it}.
        Lets new menus use any material a stock menu loads inline."""
        if getattr(self, "_mats", None) is None:
            mats = {}

            def walk(x):
                if isinstance(x, Node):
                    for k, v in x.items():
                        if isinstance(v, tuple) and v and v[0] == "material":
                            key = v[1] or struct.unpack_from(">I", self.zone, v[2])[0]
                            if key not in mats:
                                mats[key] = ref(self.v_of(x.offsets[k]))
                        elif isinstance(v, (Node, list)):
                            walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)

            for node in self.lists:
                walk(node)
            self._mats = mats
        return self._mats

    def material_ref(self, key):
        """For a material named in a new menu's text: a ref to a stock slot holding it, or
        (for an image added by an earlier compile) the material itself, to embed again."""
        r = self.material_slots().get(key)
        if r is None and self.extra is not None:
            found = []

            def walk(x):
                if isinstance(x, Node):
                    for v in x.values():
                        if isinstance(v, tuple) and v and v[0] == "material" and v[1] == key:
                            found.append(v)
                        elif isinstance(v, (Node, list)):
                            walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)

            walk(self.extra)
            return found[0] if found else None
        return r

    def material_template(self, name):
        """(start, end) in the zone of a stock inline material."""
        found = []

        def walk(x):
            if isinstance(x, Node):
                for v in x.values():
                    if isinstance(v, tuple) and v and v[0] == "material" and v[1] == name:
                        found.append((v[2], v[3]))
                    elif isinstance(v, (Node, list)):
                        walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        for node in self.lists:
            walk(node)
            if found:
                return found[0]
        raise RebuildError(f"stock material {name} not found")

    def slot_ref(self, list_index, menu_index):
        """Ref to the pointer slot of a menu in a stock list (the game reads the menu
        pointer stored there)."""
        node = self.lists[list_index]
        if not is_inline(node.ptrs["menus"]):
            raise RebuildError("stock menu list has no inline menu array")
        name = node["name"]
        array_pos = node.pos + 12 + (len(name) + 1 if isinstance(name, ZStr) else 0)
        return ref(self.v_of(array_pos) + 4 * menu_index)


# ---------------------------------------------------------------------------------
# writing menus


_LAYOUT = {}


def layout(type_name):
    """[(field, kind, target, count, offset in struct)], mirroring MenuParser.read_struct."""
    if type_name not in _LAYOUT:
        out, pos = [], 0
        for fname, kind, target, count, _ in STRUCTS[type_name]:
            if kind == "u64" and pos % 8:
                pos += 8 - pos % 8
            out.append((fname, kind, target, count, pos))
            if kind == "pad":
                pos += count
            elif kind in SCALAR_FMT:
                pos += struct.calcsize(SCALAR_FMT[kind]) * count
            elif kind == "struct":
                pos += struct_size(target) * count
            else:
                pos += 4 * count
        _LAYOUT[type_name] = out
    return _LAYOUT[type_name]


class MenuWriter:
    """Serialises a (possibly edited) parsed menu tree to zone stream bytes.

    Nodes keep the zone offsets they were parsed from; each struct starts as a copy of its
    original bytes and every field is rewritten from the node, so padding and fields the
    tool does not model survive. Pointers become: None -> null, Node/str/list -> inline,
    ("ref", x) -> x. An inline material in the original becomes a ref to the original's
    pointer slot, so the material is not loaded (and registered) a second time.
    """

    def __init__(self, zone, vmap: VirtualMap):
        self.zone, self.vmap = zone, vmap
        self.out = bytearray()
        # materials at or after this zone offset belong to an earlier appended list: they are
        # written again inline (that list is being replaced), not referenced
        self.extra_start = vmap.extra.pos if vmap.extra is not None else len(zone)
        self.pixel_bytes = 0  # image pixel data written (goes to the physical block)
        self.images = 0
        self.image_names = set()

    # -- helpers
    def u32(self, v):
        self.out += struct.pack(">I", v & 0xFFFFFFFF)

    def _ptr_value(self, value, field_pos):
        if value is None or (isinstance(value, list) and not value):
            return 0
        if isinstance(value, tuple):
            if value[0] == "ref":
                return value[1]
            if value[0] == "newimage" or (value[0] == "material" and value[2] >= self.extra_start):
                return PTR_INLINE
            if value[0] == "material":
                if field_pos is None:
                    raise RebuildError("an inline material can only be reused from a parsed menu")
                return ref(self.vmap.v_of(field_pos))
        return PTR_INLINE

    def struct_bytes(self, node):
        """Struct bytes: a copy of the original (parsed nodes) or zeros (new nodes), with
        every field rewritten from the node."""
        size = struct_size(node.type_name)
        parsed = getattr(node, "pos", None) is not None
        buf = bytearray(self.zone[node.pos:node.pos + size]) if parsed else bytearray(size)
        self._fill(node, buf, 0, node.pos if parsed else None)
        return buf

    def _fill(self, node, buf, at, zpos):
        """Write node's fields into buf at offset `at`; zpos is node's zone offset (or None)."""
        for fname, kind, target, count, rel in layout(node.type_name):
            if kind == "struct":
                subs = node[fname] if count > 1 else [node[fname]]
                size = struct_size(target)
                for k, sub in enumerate(subs):
                    o = rel + k * size
                    self._fill(sub, buf, at + o, None if zpos is None else zpos + o)
            elif kind in SCALAR_FMT:
                vals = node[fname] if count > 1 else [node[fname]]
                step = struct.calcsize(SCALAR_FMT[kind])
                for k, v in enumerate(vals):
                    struct.pack_into(SCALAR_FMT[kind], buf, at + rel + k * step, v)
            elif kind == "ptr" and target != "never":
                vals = node[fname] if count > 1 else [node[fname]]
                for k, v in enumerate(vals):
                    fp = None if zpos is None else zpos + rel + 4 * k
                    struct.pack_into(">I", buf, at + rel + 4 * k, self._ptr_value(v, fp))

    # -- tree walk (mirrors MenuParser's load order)
    def write_struct(self, node, ctx):
        self.out += self.struct_bytes(node)
        self.load(node, ctx)

    def load(self, node, ctx):
        if getattr(node, "noload", False):
            return
        if node.type_name == "itemDef_s":
            ctx = dict(ctx, item=node)
        elif node.type_name == "listBoxDef_s":
            ctx = dict(ctx, listbox=node)
        for fname, kind, target, count, _ in STRUCTS[node.type_name]:
            if kind == "struct":
                v = node[fname]
                for sub in (v if isinstance(v, list) else [v]):
                    self.load(sub, ctx)
            elif kind == "ptr" and target != "never":
                vals = node[fname] if count > 1 else [node[fname]]
                for v in vals:
                    self.follow(v, target, ctx)

    def follow(self, v, target, ctx):
        if isinstance(v, tuple) and v[0] == "newimage":
            self.new_image(v[1])
            return
        if isinstance(v, tuple) and v[0] == "material" and v[2] >= self.extra_start:
            self.count_pixels(self.zone[v[2]:v[3]])
            self.out += self.zone[v[2]:v[3]]
            return
        if v is None or (isinstance(v, tuple) and v[0] in ("ref", "material")):
            return
        if isinstance(v, tuple) and v[0] == "bytes":
            _, start, n = v
            self.out += self.zone[start:start + n]
            return
        if isinstance(v, tuple) and v[0] == "newbytes":
            _, data, n = v
            self.out += data.ljust(n, b"\0")
            return
        if isinstance(v, (bytes, str)):
            s = v.encode("latin1") if isinstance(v, str) else bytes(v)
            if b"\0" in s:
                raise RebuildError("strings can't contain NUL")
            self.out += s + b"\0"
            return
        if isinstance(v, list) and not v:
            return
        if target in ("menuArray", "itemArray", "animStateArray"):
            for p in v:
                self.u32(0 if p is None else p[1] if isinstance(p, tuple) else PTR_INLINE)
            for p in v:
                if isinstance(p, Node):
                    self.write_struct(p, ctx)
            return
        if target == "rpnArray":
            for e in v:
                self.out += struct.pack(">ii", e["type"], e["dataType"])
                if e["type"] == 0 and e["dataType"] == 2 and "string" in e:
                    self.u32(PTR_INLINE)
                else:
                    self.u32(e["value"])
            for e in v:
                if e["type"] == 0 and e["dataType"] == 2 and "string" in e:
                    self.follow(e["string"], "string", ctx)
            return
        if target in ("rowArray", "cellArray"):
            for n in v:
                self.out += self.struct_bytes(n)
            for n in v:
                self.load(n, ctx)
            return
        if isinstance(v, Node):
            self.write_struct(v, ctx)
            return
        raise RebuildError(f"can't write {target} value {v!r}")


def _image_name(path, taken):
    import re
    from pathlib import Path

    base = "xcp_" + (re.sub(r"[^a-z0-9_]+", "_", Path(path).stem.lower()).strip("_") or "image")
    name, k = base, 2
    while name in taken:
        name, k = f"{base}_{k}", k + 1
    taken.add(name)
    return name.encode()


def _new_image(self, path):
    from . import images

    try:
        template = self.vmap.material_template(images.TEMPLATE)
        name = _image_name(path, self.image_names)
        data = images.material_bytes(self.zone, template, name, images.encode_dxt1_tiled(images.load_picture(path)))
    except images.ImageError as e:
        raise RebuildError(str(e)) from None
    self.count_pixels(data)
    self.out += data


def _count_pixels(self, material: bytes):
    """Count the pixel data of an inline material (one inline image with inline pixels)."""
    from . import images

    if material[0x67] != 1 or struct.unpack_from(">I", material, 0x74)[0] != PTR_INLINE:
        return
    p = material.index(b"\0", images.MATERIAL_SIZE) + 1 if struct.unpack_from(">I", material, 0)[0] == PTR_INLINE \
        else images.MATERIAL_SIZE
    img = p + 16
    if struct.unpack_from(">I", material, img + 0x48)[0] == PTR_INLINE:
        self.pixel_bytes += struct.unpack_from(">I", material, img + 0x38)[0]
        self.images += 1


MenuWriter.new_image = _new_image
MenuWriter.count_pixels = _count_pixels


def sync_counts(node):
    """Make count fields match the arrays they size (after items/tokens were added)."""
    if isinstance(node, list):
        for x in node:
            sync_counts(x)
        return
    if not isinstance(node, Node):
        return
    t = node.type_name
    if t == "menuDef_t" and isinstance(node["items"], list):
        node["itemCount"] = len(node["items"])
    elif t == "MenuList" and isinstance(node["menus"], list):
        node["menuCount"] = len(node["menus"])
    elif t == "ExpressionStatement" and (isinstance(node["rpn"], list) or node["rpn"] is None):
        node["numRpn"] = len(node["rpn"] or [])
    elif t == "UIAnimInfo" and isinstance(node["animStates"], list):
        node["animStateCount"] = len(node["animStates"])
    for v in node.values():
        if isinstance(v, (Node, list)):
            sync_counts(v)


# ---------------------------------------------------------------------------------
# the zone edit


def _shorter(value: bytes) -> bytes:
    if value in SHORTER:
        return SHORTER[value]
    if len(value) < 9:
        raise RebuildError("first localized string is too short to make room")
    return value[:-8]


def custom_menus(vmap: VirtualMap):
    """The menus a previous rebuild added (inline entries of the appended list)."""
    if vmap.extra is None:
        return []
    return [m for m in vmap.extra["menus"] if isinstance(m, Node)]


def set_custom_menus(zone: bytes, new_menus, vmap: VirtualMap = None) -> bytes:
    """Return a zone whose loaded menu list is the stock menus plus `new_menus` (menuDef_t
    nodes: parsed, edited clones, or read from text). On a zone that was rebuilt before,
    the previously added menus are replaced by `new_menus`."""
    vmap = vmap or VirtualMap(zone)
    xfile, strings, assets, data_start = read_header(zone)
    stock = vmap.lists
    big = max(range(len(stock)), key=lambda i: stock[i]["menuCount"])
    big_node = stock[big]
    big_name = bytes(big_node["name"] or b"")
    rebuilt = vmap.extra is not None
    if big_name != (RENAMED_LIST if rebuilt else STOCK_LIST):
        raise RebuildError(f"main menu list is {big_name!r}, expected {STOCK_LIST!r}")
    names = {bytes(m["window"]["name"]) for _, _, m in menu_entries(stock) if isinstance(m["window"]["name"], ZStr)}
    for m in new_menus:
        n = m["window"]["name"]
        if not isinstance(n, (bytes, str)):
            raise RebuildError("new menus need an inline name")
        n = n.encode("latin1") if isinstance(n, str) else bytes(n)
        if n in names:
            raise RebuildError(f"a menu named {n.decode('latin1')} already exists")
        names.add(n)

    # the new menu list: every stock menu by slot ref, then the new menus inline
    menus = [("ref", vmap.slot_ref(big, i)) for i in range(big_node["menuCount"])] + list(new_menus)
    w = MenuWriter(zone, vmap)
    w.u32(PTR_INLINE)  # name
    w.u32(len(menus))
    w.u32(PTR_INLINE)  # menus
    w.out += STOCK_LIST + b"\0"
    for m in new_menus:
        sync_counts(m)
    w.follow(menus, "menuArray", {})
    appended = bytes(w.out)
    blocks = list(xfile["blockSize"])

    if rebuilt:
        old = len(zone) - vmap.extra.pos
        old_pix, old_images = vmap.extra_pixels
        out = bytearray(zone[:vmap.extra.pos])
        blocks[VIRTUAL_BLOCK] -= old - old_pix + VIRTUAL_SLACK
        blocks[PHYSICAL_BLOCK] -= old_pix + PIXEL_ALIGN * old_images
    else:
        if strings:
            raise RebuildError("zones with script strings are not supported")
        if not assets or assets[0][0] != LOCALIZE_TYPE or not is_inline(assets[0][1]):
            raise RebuildError("expected the zone to start with a localized string")
        out = bytearray(zone)
        # rename the stock list in place (same length)
        name_pos = big_node.pos + 12
        out[name_pos:name_pos + len(RENAMED_LIST)] = RENAMED_LIST
        # the first localized string gives up 8 bytes (LocalizeEntry: value, name; both inline)...
        value_ptr, name_ptr = struct.unpack_from(">II", zone, data_start)
        if not (is_inline(value_ptr) and is_inline(name_ptr)):
            raise RebuildError("first localized string is not inline")
        vstart = data_start + 8
        vend = zone.index(b"\0", vstart)
        shorter = _shorter(zone[vstart:vend])
        if len(shorter) != vend - vstart - 8:
            raise RebuildError("shortened string has the wrong length")
        out[vstart:vend] = shorter
        # ...to make room for one more asset entry (menulist, inline) at the end of the array
        out[data_start:data_start] = struct.pack(">II", MENULIST_TYPE, PTR_INLINE)
        struct.pack_into(">I", out, XFILE_ASSET_COUNT, len(assets) + 1)

    out += appended
    # the virtual block grows by what we appended (menu headers go to the temp block, so
    # this over-estimates), plus slack for alignment
    # (image pixels go to the physical block instead, each 4 KB aligned)
    blocks[VIRTUAL_BLOCK] += len(appended) - w.pixel_bytes + VIRTUAL_SLACK
    blocks[PHYSICAL_BLOCK] += w.pixel_bytes + PIXEL_ALIGN * w.images
    for b in (VIRTUAL_BLOCK, PHYSICAL_BLOCK):
        struct.pack_into(">I", out, XFILE_BLOCKS + 4 * b, blocks[b])
    struct.pack_into(">I", out, 0, len(out) - XFILE_SIZE)
    return bytes(out)


def add_menus(zone: bytes, new_menus, vmap: VirtualMap = None) -> bytes:
    """Add `new_menus` to the ones already added (if any)."""
    vmap = vmap or VirtualMap(zone)
    return set_custom_menus(zone, custom_menus(vmap) + list(new_menus), vmap)


VIRTUAL_SLACK = 0x1000


def clone_menu(lists, name: str, new_name: str) -> Node:
    """Deep copy of a stock menu, renamed. Edit the copy freely before add_menus."""
    for _, _, m in menu_entries(lists):
        n = m["window"]["name"]
        if isinstance(n, ZStr) and n == name.encode():
            c = copy.deepcopy(m)
            c["window"]["name"] = new_name.encode()
            return c
    raise RebuildError(f"no menu named {name}")
