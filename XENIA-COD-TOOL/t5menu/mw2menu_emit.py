"""Write MW2 July 2009 menus into a new zone (patch_mp), so a menu can be changed without
rebuilding ui_mp.

A menu in the stock zone points back at data loaded earlier in that zone (shared strings,
expressions, materials). Those pointers mean nothing in another zone, so the writer copies the
menu with everything it points at written out again inline:
  - strings, expressions and expression data: found through a replay of the game's block
    allocator over the stock menu lists (_BlockSim), then copied
  - materials: written as references by name (",name"), the way IW's own zones point at a
    material that lives in another zone; the game links them to the real ones when ui_mp loads

The menus go into ui_mp/patch_mp_menus.txt, the list the front end loads after
ui_mp/menus.txt. With the xex patch (xexpatch.py) a menu in that list replaces the stock menu
of the same name.
"""

from __future__ import annotations

import bisect
import re
import struct
import sys

from .mw2menu_parse import (EDITFIELD_TYPES, INLINE, TYPE_DVARENUM, TYPE_LISTBOX, TYPE_MULTI, TYPE_NEWSTICKER,
                            TYPE_TEXTSCROLL, MenuParseError, Node, Parser, Ref, find_menu_lists)

PTR = 0xFFFFFFFF
ASSET_MENUFILE = 0x17
PATCH_LIST = "ui_mp/patch_mp_menus.txt"


class EmitError(ValueError):
    pass


def u32(b, off):
    return struct.unpack_from(">I", b, off)[0]


# ---------------------------------------------------------------------------------------
# where the stock zone's pointers lead

class _BlockSim(Parser):
    """Parser that also replays the loader's allocations in the virtual block (block 3), giving
    the block offset of every struct and string, so a pointer to earlier data can be followed
    back to its place in the zone. Asset headers (menus, materials, images) go to the temp
    block and use no virtual memory; -2 pointers get a 4-byte alias slot first."""

    def __init__(self, zone):
        super().__init__(zone)
        self.mem = 0
        self.starts = {}  # zone offset -> block offset
        self.sizes = {}
        self.align = 4
        self.temp = False
        self.string_mem = set()  # block offsets where strings start

    def alloc(self, n, align=4, at=None):
        self.mem = (self.mem + align - 1) & ~(align - 1)
        if at is not None:
            self.starts[at] = self.mem
            self.sizes[at] = n
        r = self.mem
        self.mem += n
        return r

    def take(self, n):
        p = Parser.take(self, n)
        if self.temp:
            self.temp = False
        else:
            a, self.align = self.align, 4
            self.alloc(n, a, p)
        return p

    def cstring(self):
        start = self.pos
        s = Parser.cstring(self)
        self.string_mem.add(self.mem)
        self.starts[start] = self.mem
        self.sizes[start] = self.pos - start
        self.mem += self.pos - start
        return s

    def alias(self, ptr):
        if ptr == 0xFFFFFFFE:
            self.alloc(4, 4)

    def material_handle(self, ptr):
        if ptr == 0 or ptr not in INLINE:
            return Parser.material_handle(self, ptr)
        self.alias(ptr)
        self.temp = True
        p = self.take(0x58)
        name = self.xstring(self.u32_at(p))
        if self.u32_at(p + 0x40) in INLINE:
            raise MenuParseError("inline technique set")
        tex, const, sbits, subs = (self.u32_at(p + o) for o in (0x44, 0x48, 0x4C, 0x50))
        tex_n, const_n, sb_n, layers = self.z[p + 0x39], self.z[p + 0x3A], self.z[p + 0x3B], self.z[p + 0x3E]
        if tex == PTR:
            base = self.take(12 * tex_n)
            for i in range(tex_n):
                d = base + 12 * i
                if self.z[d + 7] == 11:
                    if self.u32_at(d + 8) in INLINE:
                        raise MenuParseError("inline water texture")
                else:
                    self.image_ptr(self.u32_at(d + 8))
        if const == PTR:
            self.align = 16
            self.take(32 * const_n)
        if sbits == PTR:
            self.take(8 * sb_n)
        if subs == PTR:
            base = self.take(4 * layers)
            self.xstring_array([self.u32_at(base + 4 * i) for i in range(layers)])
        return ("material", name, p)

    def image_ptr(self, ptr):
        if ptr == 0 or ptr not in INLINE:
            return
        self.alias(ptr)
        self.temp = True
        p = self.take(0x70)
        self.xstring(self.u32_at(p + 0x6C))
        if self.u32_at(p + 0x48):
            self.temp = True
            self.take(self.u32_at(p + 0x3C))  # pixels go to the physical block

    def menu(self):
        self.temp = True
        return Parser.menu(self)

    def menu_list(self, pos):
        self.pos = pos
        self.temp = True
        p = self.take(12)
        name = self.xstring(self.u32_at(p))
        count, arr = self.u32_at(p + 4), self.u32_at(p + 8)
        menus = []
        if arr:
            base = self.take(4 * count)
            for i in range(count):
                ptr = self.u32_at(base + 4 * i)
                if ptr in INLINE:
                    self.alias(ptr)
                    menus.append(self.menu())
                else:
                    menus.append(None if ptr == 0 else Ref(ptr))
        return {"pos": p, "name": name, "menus": menus, "end": self.pos}


def _walk_nodes(x, fn):
    if isinstance(x, Node):
        fn(x)
        for v in x.values():
            _walk_nodes(v, fn)
    elif isinstance(x, dict):
        for v in x.values():
            _walk_nodes(v, fn)
    elif isinstance(x, list):
        for v in x:
            _walk_nodes(v, fn)


class Resolver:
    """Follows the stock zone's pointers to earlier data back to zone offsets."""

    def __init__(self, zone: bytes):
        self.z = zone
        lists = find_menu_lists(zone)
        if not lists:
            raise EmitError("no menu lists in this zone")
        positions = sorted(ml["pos"] for ml in lists)
        sim = _BlockSim(zone)
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
        self.lists = []
        for pos in positions:  # the lists follow each other in the zone; replay them in order
            if self.lists and pos != sim.pos:
                break
            self.lists.append(sim.menu_list(pos))
        self.mem_keys = sorted((m, p) for p, m in sim.starts.items())
        self._keys = [m for m, _ in self.mem_keys]
        self.sizes = sim.sizes
        self.base = self._find_base(sim.string_mem)
        # zone offset of every material pointer field -> its material's name (inline ones)
        self.material_fields = {}

        def note(node):
            for key in ("background", "selectIcon"):
                v = node.get(key)
                if isinstance(v, tuple) and v and v[0] == "material":
                    self.material_fields[node.off[key]] = v[1]
        for ml in self.lists:
            _walk_nodes(ml["menus"], note)

    def _find_base(self, string_mem) -> int:
        """Block offset of the first menu list (what came before it in block 3), found from
        the string pointers in the menus: they must all land on the start of a string."""
        refs = []

        def grab(node):
            for k in ("name", "text", "group"):
                v = node.get(k)
                if isinstance(v, Ref) and len(refs) < 200:
                    refs.append((int(v) - 1) & 0x0FFFFFFF)
        for ml in self.lists:
            _walk_nodes(ml["menus"], grab)
        if not refs:
            return 0
        for m in sorted(string_mem):
            base = refs[0] - m
            if base < 0:
                break
            if all(r - base in string_mem for r in refs):
                return base
        raise EmitError("couldn't line up the zone's pointers with its data")

    def _inside(self, m) -> bool:
        i = bisect.bisect_right(self._keys, m) - 1
        if i < 0:
            return False
        k, p = self.mem_keys[i]
        return m - k < self.sizes[p]

    def zpos(self, ref: int) -> int:
        """Zone offset that a pointer to earlier data points at."""
        v = int(ref) - 1
        if v >> 28 != 3:
            raise EmitError(f"pointer 0x{int(ref):08X} is outside the virtual block")
        m = (v & 0x0FFFFFFF) - self.base
        i = bisect.bisect_right(self._keys, m) - 1
        if i < 0:
            raise EmitError(f"pointer 0x{int(ref):08X} is before the menus")
        k, p = self.mem_keys[i]
        if m - k >= self.sizes[p]:
            raise EmitError(f"pointer 0x{int(ref):08X} doesn't point at menu data")
        return p + (m - k)

    def string(self, ref) -> bytes:
        p = self.zpos(ref)
        return self.z[p:self.z.index(b"\0", p)]

    def material(self, ref, depth=0) -> str:
        """Name of the material a material pointer points at (a pointer to an earlier field
        that holds the material, or to another such pointer)."""
        if depth > 50:
            raise EmitError("material pointer loop")
        p = self.zpos(ref)
        if p in self.material_fields:
            name = self.material_fields[p]
            return self.string(name).decode("latin1") if isinstance(name, Ref) else str(name)
        v = u32(self.z, p)
        if v in INLINE or v == 0:
            raise EmitError(f"material pointer 0x{int(ref):08X} leads nowhere")
        return self.material(v, depth + 1)

    def menus(self) -> dict[str, Node]:
        """{name: menu} for every inline menu in the zone's lists."""
        out = {}
        for ml in self.lists:
            for m in ml["menus"]:
                if isinstance(m, Node):
                    n = m["window"]["name"]
                    name = self.string(n).decode("latin1") if isinstance(n, Ref) else str(n)
                    out.setdefault(name, m)
        return out


# ---------------------------------------------------------------------------------------
# writing

class Emitter:
    """Copies menus out of a stock zone with every pointer to earlier data written inline.

    It walks the stock data in the game's load order (the same order as Parser), copying
    each struct and string, so self.pos follows the stock zone and self.out is the new data.
    A pointer to earlier data jumps to where that data is and copies it from there.

    Expressions and expression data are shared (each expression points back at its menu's
    expression data, whose functions are expressions again), so those are written once and
    pointed at afterwards. self.mem replays the loader's virtual-block allocator over the new
    data (as _BlockSim does) to know where each one lands; mem is where the asset starts."""

    def __init__(self, resolver: Resolver, mem: int = 0):
        self.r = resolver
        self.z = resolver.z
        self.pos = 0
        self.out = bytearray()
        self.mem = mem
        self.placed = {}  # stock zone offset of an expression / expression data -> new block offset
        self.skipping = False
        self.temp_bytes = 0  # temp-block bytes the loader needs (headers)
        self.sd_mem = None  # block offset of the current menu's expression data (for new expressions)

    # ---- primitives
    def alloc(self, n, temp=False) -> int:
        if temp:
            self.temp_bytes += (n + 3) & ~3
            return -1
        self.mem = (self.mem + 3) & ~3
        m = self.mem
        self.mem += n
        return m

    def take(self, n, temp=False):
        p = self.pos
        if p + n > len(self.z):
            raise EmitError(f"read past the end of the zone at 0x{p:X}")
        self.pos += n
        o = len(self.out)
        self.out += self.z[p:p + n]
        self.alloc(n, temp)
        return p, o

    def add(self, data: bytes, temp=False):
        o = len(self.out)
        self.out += data
        self.alloc(len(data), temp)
        return o

    @staticmethod
    def ref(mem: int) -> int:
        return (0x30000000 | mem) + 1

    def put(self, o, v):
        struct.pack_into(">I", self.out, o, v & 0xFFFFFFFF)

    def skip(self, fn, *args):
        """Walk stock data without writing it (to step over data that is being replaced)."""
        keep, self.out = self.out, bytearray()
        state = self.mem, self.skipping, dict(self.placed), self.temp_bytes
        self.skipping = True
        try:
            fn(*args)
        finally:
            self.out = keep
            self.mem, self.skipping, self.placed, self.temp_bytes = state

    def at(self, zpos, fn, *args):
        """Walk the data at zpos (where a pointer to earlier data leads), then come back."""
        keep, self.pos = self.pos, zpos
        try:
            fn(*args)
        finally:
            self.pos = keep

    def raw_string(self, text: bytes):
        self.out += text + b"\0"
        self.mem += len(text) + 1  # strings aren't aligned

    # ---- asset-style pointers (0 null, -1/-2 inline, else earlier data)
    def xstring(self, ptr, o, override=None):
        if override is not None:
            if ptr in INLINE:
                self.skip(self._cstring)
            if override is False:
                self.put(o, 0)
                return
            self.put(o, PTR)
            self.raw_string(override.encode("latin1") if isinstance(override, str) else override)
            return
        if ptr == 0:
            return
        self.put(o, PTR)
        if ptr in INLINE:
            self._cstring()
        else:
            self.raw_string(self.r.string(ptr))

    def _cstring(self):
        end = self.z.index(b"\0", self.pos)
        self.raw_string(self.z[self.pos:end])
        self.pos = end + 1

    def shared(self, ptr, o, walk):
        """An expression or expression data pointer: inline the first time, then point at it."""
        if ptr == 0:
            return
        zp = self.pos if ptr in INLINE else self.r.zpos(ptr)
        if zp in self.placed:
            if ptr in INLINE:
                self.skip(walk)  # the stock zone has it inline here again; step over it
            self.put(o, self.ref(self.placed[zp]))
            return
        self.put(o, PTR)
        if ptr in INLINE:
            walk()
        else:
            self.at(zp, walk)

    def statement(self, ptr, o):
        self.shared(ptr, o, self._statement)

    def _place(self):
        """Note where the struct at self.pos lands (it is 4-aligned)."""
        self.placed[self.pos] = (self.mem + 3) & ~3

    def _statement(self):
        self._place()
        s, o = self.take(0x18)
        n, entries, supporting = u32(self.z, s), u32(self.z, s + 4), u32(self.z, s + 8)
        if entries:
            self.put(o + 4, PTR)
            base, ob = self.take(12 * n)
            for i in range(n):
                typ, a, b = struct.unpack_from(">iII", self.z, base + 12 * i)
                if typ != 0:
                    if a == 2:
                        self.xstring(b, ob + 12 * i + 8)
                    elif a == 3:
                        self.statement(b, ob + 12 * i + 8)
        self.supporting(supporting, o + 8)

    def supporting(self, ptr, o):
        self.shared(ptr, o, self._supporting)

    def _supporting(self):
        self._place()
        s, o = self.take(0x18)
        nf, fptr, nd, dptr, ns, sptr = struct.unpack_from(">6I", self.z, s)
        if fptr:
            base, ob = self.take(4 * nf)
            for i in range(nf):
                self.statement(u32(self.z, base + 4 * i), ob + 4 * i)
        if dptr:
            base, ob = self.take(4 * nd)
            for i in range(nd):
                if u32(self.z, base + 4 * i):
                    q, oq = self.take(8)
                    self.xstring(u32(self.z, q + 4), oq + 4)
        if sptr:
            base, ob = self.take(4 * ns)
            for i in range(ns):
                self.xstring(u32(self.z, base + 4 * i), ob + 4 * i)

    def material(self, ptr, o):
        if ptr == 0:
            return
        if ptr in INLINE:
            p = Parser(self.z)
            p.pos = self.pos
            _, name, _ = p.material_handle(ptr)
            self.pos = p.pos
            name = self.r.string(name).decode("latin1") if isinstance(name, Ref) else str(name)
        else:
            name = self.r.material(ptr)
        self.put(o, PTR)
        self.add(struct.pack(">I", PTR) + bytes(0x54), temp=True)  # the header goes to temp
        self.raw_string(b"," + name.lstrip(",").encode("latin1"))

    # ---- plain pointers (0 null, else inline)
    def handler_set(self, ptr, o=None, extra=None):
        """Copy a stock handler set; extra: new handlers (see handlers()) added at its end."""
        if not ptr:
            return
        s, so = self.take(8)
        count, arr = u32(self.z, s), u32(self.z, s + 4)
        if extra:
            if not arr:
                raise EmitError("can't add to an empty event handler set")
            struct.pack_into(">I", self.out, so, count + len(extra))
        if not arr:
            return
        base, ob = self.take(4 * count)
        if extra:
            self.add(struct.pack(">I", PTR) * len(extra))
        for i in range(count):
            if not u32(self.z, base + 4 * i):
                continue
            h, oh = self.take(8)
            data, etype = u32(self.z, h), self.z[h + 4]
            if etype == 0:
                self.xstring(data, oh)
            elif etype == 1:
                if data:
                    c, oc = self.take(8)
                    self.statement(u32(self.z, c + 4), oc + 4)
                    self.handler_set(u32(self.z, c))
            elif etype == 2:
                self.handler_set(data)
            elif etype in (3, 4, 5, 6):
                if data:
                    c, oc = self.take(8)
                    self.xstring(u32(self.z, c), oc)
                    self.statement(u32(self.z, c + 4), oc + 4)
        for h in extra or ():
            self._handler(h)

    def key_handlers(self, ptr):
        while ptr:
            p, o = self.take(12)
            self.handler_set(u32(self.z, p + 4))
            ptr = u32(self.z, p + 8)

    def handlers(self, items: list):
        """A new event handler set. Each item is a script line (str), or
        {"if": expression, "then": [items]} for a condition (the stock `if ( ... ) { }`)."""
        self.add(struct.pack(">II", len(items), PTR))
        self.add(struct.pack(">I", PTR) * len(items))
        for h in items:
            self._handler(h)

    scripts = handlers

    def _handler(self, h):
        if isinstance(h, str):
            self.add(struct.pack(">IB3x", PTR, 0))
            self.raw_string(h.encode("latin1"))
        elif isinstance(h, dict) and "if" in h:
            then = list(h.get("then", []))
            self.add(struct.pack(">IB3x", PTR, 1))
            self.add(struct.pack(">II", PTR if then else 0, PTR))  # handler set, expression
            self.new_statement(h["if"])
            if then:
                self.handlers(then)
        else:
            raise EmitError(f"event handlers are script lines or {{\"if\": ..., \"then\": [...]}}, not {h!r}")

    def new_statement(self, text: str):
        """A new expression, from menu source like ( ! dvarbool( "name" ) )."""
        entries = parse_expression(text)
        sd = self.ref(self.sd_mem) if self.sd_mem is not None else 0
        self.add(struct.pack(">6I", len(entries), PTR, sd, 0, 0, 0))
        self.add(b"".join(struct.pack(">iiI", t, a, PTR if isinstance(b, bytes) else b & 0xFFFFFFFF)
                          for t, a, b in entries))
        for t, a, b in entries:
            if isinstance(b, bytes):
                self.raw_string(b)

    def drop_statement(self, ptr, o):
        """Leave an expression out (null), stepping over its data if it is inline here."""
        if ptr in INLINE:
            def walk():
                self.out += bytes(4)  # a scratch slot for the pointer
                self.statement(ptr, len(self.out) - 4)
            self.skip(walk)
        self.put(o, 0)

    def window(self, s, o, name=None):
        self.xstring(u32(self.z, s), o, name)
        self.xstring(u32(self.z, s + 44), o + 44)
        self.material(u32(self.z, s + 172), o + 172)

    # ---- items and menus
    HANDLERS = {"mouseEnterText": 312, "mouseExitText": 316, "mouseEnter": 320, "mouseExit": 324,
                "action": 328, "accept": 332, "onFocus": 336, "leaveFocus": 340}

    def item(self, ov: dict = None):
        """One itemDef at self.pos. ov: name / text (str, or False for none), any handler set
        name (list of handlers, see handlers()), "f32"/"i32": {offset: value} number overrides,
        dvar: the item's dvar (a slider's), edit: {offset: float} in its slider data,
        no_exp: leave out every expression (visibleExp, rectYExp, ...), no_dvar: drop the dvar
        show/enable conditions."""
        ov = ov or {}
        s, o = self.take(500)
        self.window(s, o, ov.get("name"))
        self.xstring(u32(self.z, s + 300), o + 300, ov.get("text"))
        for key, off in self.HANDLERS.items():
            ptr = u32(self.z, s + off)
            if key in ov:
                if ptr:
                    self.skip(self.handler_set, ptr)
                if ov[key]:
                    self.put(o + off, PTR)
                    self.handlers(ov[key])
                else:
                    self.put(o + off, 0)
            else:
                self.handler_set(ptr)
        no_dvar = ov.get("no_dvar")
        self.xstring(u32(self.z, s + 344), o + 344, False if no_dvar else ov.get("dvar"))
        self.xstring(u32(self.z, s + 348), o + 348, False if no_dvar else None)
        self.key_handlers(u32(self.z, s + 352))
        self.xstring(u32(self.z, s + 356), o + 356, False if no_dvar else None)
        if no_dvar:
            self.put(o + 360, 0)
        if u32(self.z, s + 364) not in INLINE:
            self.put(o + 364, 0)  # focus sound: a sound in ui_mp can't be pointed at; none
        else:
            raise EmitError("inline focus sound isn't supported")
        self.type_data(u32(self.z, s + 256), u32(self.z, s + 388), o + 388, ov.get("edit"))
        for i in range(16):
            if ov.get("no_exp"):
                self.drop_statement(u32(self.z, s + 396 + 4 * i), o + 396 + 4 * i)
            else:
                self.statement(u32(self.z, s + 396 + 4 * i), o + 396 + 4 * i)
        for kind, vals in (("f32", ov.get("f32", {})), ("i32", ov.get("i32", {}))):
            for off, v in vals.items():
                struct.pack_into(">f" if kind == "f32" else ">i", self.out, o + off, v)

    def type_data(self, typ, ptr, o, edit=None):
        """edit: {offset: float} overrides in an edit field / slider's data (0 min, 4 max, 8 default)."""
        if not ptr:
            if edit:
                raise EmitError("this item has no slider / edit field data to change")
            return
        if typ == TYPE_LISTBOX:
            s, so = self.take(344)
            self.handler_set(u32(self.z, s + 308))
            self.material(u32(self.z, s + 340), so + 340)
        elif typ in EDITFIELD_TYPES:
            _, so = self.take(32)
            for off, v in (edit or {}).items():
                struct.pack_into(">f", self.out, so + off, v)
        elif typ == TYPE_MULTI:
            s, so = self.take(392)
            for i in range(64):
                self.xstring(u32(self.z, s + 4 * i), so + 4 * i)
        elif typ == TYPE_DVARENUM:
            self.xstring(ptr, o)
        elif typ == TYPE_NEWSTICKER:
            self.take(28)
        elif typ == TYPE_TEXTSCROLL:
            self.take(4)

    MENU_HANDLERS = {"onOpen": 228, "onCloseRequest": 232, "onClose": 236, "onESC": 240}

    def menu(self, menu: Node, plan=None, mov: dict = None) -> bytes:
        """The menu's bytes. plan: the new item list as [(stock item index, overrides)];
        default: every stock item unchanged. mov: menu overrides: name; onOpen / onClose /
        onCloseRequest / onESC (new handler list, [] for none) or the same name with a + to
        add handlers at the end of the stock ones; no_exp (leave out the menu's expressions);
        "f32": {offset: value} in the menu header."""
        mov = mov or {}
        self.pos = menu.pos
        start = len(self.out)
        s, o = self.take(416, temp=True)  # the menu header goes to temp
        sd = u32(self.z, s + 412)
        sd_pos = self.pos if sd in INLINE else (self.r.zpos(sd) if sd else None)
        self.supporting(sd, o + 412)
        self.sd_mem = self.placed.get(sd_pos)
        self.window(s, o, mov.get("name"))
        self.xstring(u32(self.z, s + 176), o + 176)
        for key, off in self.MENU_HANDLERS.items():
            ptr = u32(self.z, s + off)
            if key in mov:
                if ptr:
                    self.skip(self.handler_set, ptr)
                if mov[key]:
                    self.put(o + off, PTR)
                    self.handlers(mov[key])
                else:
                    self.put(o + off, 0)
            elif mov.get(key + "+"):
                if ptr:
                    self.handler_set(ptr, extra=mov[key + "+"])
                else:
                    self.put(o + off, PTR)
                    self.handlers(mov[key + "+"])
            else:
                self.handler_set(ptr)
        self.key_handlers(u32(self.z, s + 244))
        drop = self.drop_statement if mov.get("no_exp") else self.statement
        drop(u32(self.z, s + 248), o + 248)
        self.xstring(u32(self.z, s + 252), o + 252)
        self.xstring(u32(self.z, s + 256), o + 256)
        for off in (280, 284, 288, 292):
            drop(u32(self.z, s + off), o + off)
        for off, v in mov.get("f32", {}).items():
            struct.pack_into(">f", self.out, o + off, v)
        self.put(o + 296, PTR)
        count = u32(self.z, s + 184)
        if not u32(self.z, s + 296) or not count:
            if plan:
                raise EmitError("this menu has no items to copy from")
            return bytes(self.out[start:])
        arr = self.pos
        self.pos += 4 * count
        item_pos = []
        for i in range(count):  # find where each stock item is (they follow the array)
            if u32(self.z, arr + 4 * i):
                item_pos.append(self.pos)
                self.skip(self.item)
            else:
                item_pos.append(None)
        if plan is None:
            plan = [(i, None) for i in range(count)]
        struct.pack_into(">i", self.out, o + 184, len(plan))
        self.add(b"".join(struct.pack(">I", PTR if item_pos[i] is not None else 0) for i, _ in plan))
        for i, ov in plan:
            if item_pos[i] is not None:
                self.at(item_pos[i], self.item, ov)
        return bytes(self.out[start:])


def menu_list_asset(resolver: Resolver, name: str, menus: list, mem: int = 0) -> bytes:
    """A MenuList asset's inline data, starting at block offset mem: {name, count, menus},
    the name, the pointer array, then each menu. menus: [(menu Node, item plan or None)]."""
    return menu_list(resolver, name, menus, mem)[0]


def menu_list(resolver: Resolver, name: str, menus: list, mem: int = 0) -> tuple[bytes, int]:
    """menu_list_asset, plus the temp-block bytes the loader needs for it.
    menus: [(menu, plan)] or [(menu, plan, menu overrides)]."""
    em = Emitter(resolver, mem)
    em.add(struct.pack(">III", PTR, len(menus), PTR if menus else 0), temp=True)
    em.raw_string(name.encode("latin1"))
    if menus:
        em.add(struct.pack(">I", PTR) * len(menus))
    for entry in menus:
        menu, plan, mov = (tuple(entry) + (None,))[:3]
        em.menu(menu, plan, mov)
    return bytes(em.out), em.temp_bytes


def virtual_size(asset: bytes, mem: int = 0) -> int:
    """Virtual-block bytes a MenuList asset needs (from the same allocator replay), after
    checking that it parses back to its exact length."""
    sim = _BlockSim(asset)
    sim.mem = mem
    sim.menu_list(0)
    if sim.pos != len(asset):
        raise EmitError(f"menu list doesn't parse back cleanly (stopped at 0x{sim.pos:X} of 0x{len(asset):X})")
    return sim.mem - mem


# ---------------------------------------------------------------------------------------
# new expressions

_EXP_TOKEN = re.compile(r'\s*(?:"((?:\\.|[^"\\])*)"|(-?(?:\d+\.\d*|\.\d+))|(-?\d+)|([A-Za-z_][A-Za-z0-9_]*\s*\()'
                        r'|(&&|\|\||==|!=|<=|>=|<<|>>|[()!,*/%+\-<>&|~]))')


def parse_expression(text: str) -> list:
    """Menu expression source -> [(type, a, b)] entries as the stock zones store them: operators
    (type 0, a = operation), int / float operands (type 1, a = 0 / 1) and strings (a = 2, b the
    bytes). Like the stock ones, a leading ( is kept and its closing ) left off."""
    from .mw2menu import OP_INDEX
    text = text.strip()
    out, pos = [], 0
    while pos < len(text):
        m = _EXP_TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise EmitError(f"can't read the expression {text!r} at {text[pos:pos + 12]!r}")
        pos = m.end()
        string, flt, num, func, op = m.groups()
        if string is not None:
            out.append((1, 2, string.replace('\\"', '"').encode("latin1")))
        elif flt is not None:
            out.append((1, 1, struct.unpack(">I", struct.pack(">f", float(flt)))[0]))
        elif num is not None:
            out.append((1, 0, int(num)))
        else:
            name = (func[:-1].strip().lower() + "(") if func else op
            if name not in OP_INDEX:
                raise EmitError(f"unknown expression function or operator {name!r}")
            out.append((0, OP_INDEX[name], 0))
    if not out:
        raise EmitError("empty expression")
    opener, closer = OP_INDEX["("], OP_INDEX[")"]
    depth, wrapped = 0, False
    for k, (t, a, _) in enumerate(out):
        if t == 0 and (a == opener or a >= 23):  # ( and function calls open a group
            depth += 1
        elif t == 0 and a == closer:
            depth -= 1
            if depth == 0:
                wrapped = out[0] == (0, opener, 0) and k == len(out) - 1
                break
    if wrapped:
        out.pop()  # the stock compiler keeps the outer ( and leaves its ) off
    else:
        out.insert(0, (0, opener, 0))
    return out


# ---------------------------------------------------------------------------------------
# added buttons

def _name(resolver, v):
    if v is None:
        return None
    return resolver.string(v).decode("latin1") if isinstance(v, Ref) else str(v)


def _rect(x, y, w, h, origin=(0.0, 0.0)) -> dict:
    """f32 overrides for an item's rect (screen) and rectClient (relative to the menu's rect,
    at origin: the game places items from rectClient)."""
    return {4: x, 8: y, 12: w, 16: h, 24: x - origin[0], 28: y - origin[1], 32: w, 36: h}


def _same_column(r, top) -> bool:
    return r["vertAlign"] == top["vertAlign"] and r["horzAlign"] == top["horzAlign"]


def button_plan(resolver: Resolver, menu: Node, spec, removes=()) -> list:
    """Item plan that adds buttons to a stock menu and removes others. spec: one button spec or
    a list of them:
        after   name of the button the new one goes under
        copy    name of the button whose look and focus behaviour it copies (default: after)
        name    the new item's name;  text  its label;  action  list of script lines
    New buttons go in the rows under `after` (several with the same `after` go in the order
    given), and everything below them in that column (the buttons and the icons and lines next
    to them) moves down. removes: names of items to leave out; what was below them moves up."""
    specs = spec if isinstance(spec, list) else [spec]
    items = menu["items"]
    names = [_name(resolver, it["window"]["name"]) if it is not None else None for it in items]
    groups: dict[int, list] = {}
    for sp in specs:
        for key in (sp["after"], sp.get("copy", sp["after"])):
            if key not in names:
                raise EmitError(f"no item named {key!r} in this menu")
        groups.setdefault(names.index(sp["after"]), []).append(sp)
    gone = set()
    for n in removes:
        if n not in names:
            raise EmitError(f"no item named {n!r} in this menu to remove")
        gone.add(names.index(n))
    if gone & set(groups):
        raise EmitError("a button can't go under an item that is removed")
    moves = []  # (rect of the reference item, from y, rows of height, +1 add / -1 remove)
    for after, group in groups.items():
        top = items[after]["window"]["rect"]
        moves.append((top, top["y"] + top["h"], top["h"] * len(group)))
    for i in gone:
        r = items[i]["window"]["rect"]
        moves.append((r, r["y"] + r["h"], -r["h"]))

    def shift(r, skip=None):
        """How far a stock item at rect r moves down."""
        return sum(d for top, y, d in moves if top is not skip and _same_column(r, top) and r["y"] >= y)

    plan = []
    for i, it in enumerate(items):
        if i in gone:
            continue
        ov = None
        if it is not None and i not in groups:
            r = it["window"]["rect"]
            d = shift(r)
            if d:
                ov = {"f32": {8: r["y"] + d, 28: it["window"]["rectClient"]["y"] + d}}
        if it is not None and i in groups:
            r = it["window"]["rect"]
            d = shift(r, skip=r)
            if d:
                ov = {"f32": {8: r["y"] + d, 28: it["window"]["rectClient"]["y"] + d}}
        plan.append((i, ov))
        if i in groups:
            top = items[i]["window"]["rect"]
            client = items[i]["window"]["rectClient"]
            row = top["h"]
            base = top["y"] + shift(top, skip=top)
            for j, sp in enumerate(groups[i]):
                c = items[names.index(sp.get("copy", sp["after"]))]["window"]
                y = base + row * (j + 1)
                plan.append((names.index(sp.get("copy", sp["after"])),
                             {"name": sp.get("name", "t5mt_button"), "text": sp.get("text", "Button"),
                              "action": list(sp.get("action", [])),
                              "f32": {4: top["x"], 8: y, 24: client["x"], 28: y - top["y"] + client["y"]}}))
    return plan


# ---------------------------------------------------------------------------------------
# new popups

POPUP_TEMPLATE = "leavelobbywarning"  # a stock yes/no popup: its pieces are copied
_P_DIM, _P_PANEL, _P_TITLE, _P_BUTTON, _P_SMALL = 0, 2, 17, 19, 21
FULL_W, FULL_H = 854.0, 480.0  # the whole 16:9 screen, in the menus' centred 640x480 units


def popup_plan(resolver: Resolver, spec: dict):
    """(template menu, item plan, menu overrides) for a new popup menu, from a spec:
        popup    the new menu's name (open it with "open" "<name>")
        title    the heading;  page  small text at the top right (optional)
        lines    the text, one line each
        buttons  [{"text", "action", "name"}]; action is a list of script lines
        onOpen   extra handlers run when it opens (it always focuses the first button)
        fullscreen  true: fills the screen (default); false: a box sized to the text, `width` wide
    It is drawn from pieces of the stock leavelobbywarning popup: the dimmed screen, a panel,
    the heading bar, text lines and the popup buttons."""
    menus = resolver.menus()
    if POPUP_TEMPLATE not in menus:
        raise EmitError(f"the stock {POPUP_TEMPLATE} menu isn't in ui_mp (popups are copied from it)")
    menu = menus[POPUP_TEMPLATE]
    items = menu["items"]
    if len(items) <= _P_SMALL or any(items[i] is None for i in (_P_DIM, _P_PANEL, _P_TITLE, _P_BUTTON, _P_SMALL)) \
            or items[_P_BUTTON]["type"] != 1:
        raise EmitError(f"the stock {POPUP_TEMPLATE} menu isn't laid out as expected")
    name = spec.get("popup")
    if not name or not re.match(r"^[A-Za-z0-9_]+$", name):
        raise EmitError(f"popup name {name!r}: use letters, digits and _")
    lines = [str(x) for x in spec.get("lines", [])]
    buttons = spec.get("buttons") or [{"text": "Close", "action": ['"play" "mouse_click" ; "close" "self" ; ']}]
    full = spec.get("fullscreen", True)
    if full:
        W, H = FULL_W, FULL_H
        head, line_h, btn_h, btn_w, margin = 44.0, 30.0, 24.0, 320.0, 80.0
        title_scale, line_scale, btn_scale, page_scale = 0.5, 0.4, 0.4, 0.3
    else:
        W = float(spec.get("width", 520))
        head, line_h, btn_h, margin = 24.0, 20.0, 20.0, 16.0
        btn_w = W - 8
        title_scale, line_scale, btn_scale, page_scale = 0.375, 0.3, 0.375, 0.25
        H = head + 10 + line_h * len(lines) + 10 + btn_h * len(buttons) + 10
    x0, y0 = -W / 2, -round(H / 2)
    org = (x0, y0)
    panel = tuple(spec.get("color", (0.13, 0.14, 0.17, 0.97)))
    plan = [
        (_P_DIM, {"no_exp": True, "f32": {**_rect(-854.0, -480.0, 1708.0, 960.0, org), 120: 0.6}}),
        (_P_PANEL, {"no_exp": True, "f32": {**_rect(x0, y0, W, H, org), 108: panel[0], 112: panel[1],
                                           116: panel[2], 120: panel[3]}}),
        (_P_TITLE, {"no_exp": True, "no_dvar": True, "text": spec.get("title", ""),
                    "f32": {**_rect(x0, y0, W, head, org), 276: 0.0, 280: 0.0, 284: title_scale},
                    "i32": {272: 9 if full else 5}}),
    ]
    if spec.get("page"):
        plan.append((_P_SMALL, {"no_exp": True, "no_dvar": True, "text": spec["page"],
                                "i32": {272: 10}, "f32": {**_rect(x0, y0, W - 16, head, org), 276: 0.0, 280: 0.0,
                                                          284: page_scale, 104: 0.6}}))
    y = y0 + head + (24 if full else 10)
    for text in lines:
        plan.append((_P_SMALL, {"no_exp": True, "no_dvar": True, "text": text or " ",
                                "i32": {272: 4}, "f32": {**_rect(x0 + margin, y, W - 2 * margin, line_h, org),
                                                         276: 0.0, 280: 0.0, 284: line_scale}}))
        y += line_h
    y = (y0 + H - 24 - btn_h * len(buttons)) if full else y + 10
    for k, b in enumerate(buttons):
        bx = -btn_w / 2
        plan.append((_P_BUTTON, {"no_exp": True, "no_dvar": True, "name": b.get("name", f"{name}_button{k}"),
                                 "text": b.get("text", "OK"), "action": list(b.get("action", [])),
                                 "i32": {272: 5}, "f32": {**_rect(bx, y, btn_w, btn_h, org), 276: 0.0, 280: 0.0,
                                                          284: btn_scale}}))
        y += btn_h
    mov = {"name": name, "onOpen+": list(spec.get("onOpen", [])) + ['"focusFirst" ; '], "no_exp": True,
           "f32": {4: x0, 8: y0, 12: W, 16: H, 120: 0.0}}
    return menu, plan, mov


MENU_KEYS = ("onOpen", "onClose", "onCloseRequest", "onESC")


def clone_plan(resolver: Resolver, spec: dict):
    """(stock menu, item plan, menu overrides) for a new menu that copies a stock one's look:
        menu     the new menu's name;  from  the stock menu to copy (e.g. "controls")
        title    new text for the stock title item (`title_item`, default 6)
        keep     indices of stock items copied as they are (background, title, edges, hints)
        rows     the list, top to bottom: {"copy": index of a stock button, "text", "action",
                 "name", "with": {"copy": index of a stock slider, "dvar", "min", "max",
                 "default", "action"}}; rows start at y `top` (default 28), `row` (20) apart
        onOpen / onClose / onESC ...   handlers (replacing the stock ones), as for stock menus"""
    menus = resolver.menus()
    src, name = spec.get("from"), spec.get("menu")
    if src not in menus:
        raise EmitError(f"no menu named {src!r} in ui_mp to copy")
    if not name or not re.match(r"^[A-Za-z0-9_]+$", name):
        raise EmitError(f"menu name {name!r}: use letters, digits and _")
    if name in menus:
        raise EmitError(f"menu {name!r}: ui_mp already has a menu with that name")
    menu = menus[src]
    items = menu["items"]

    def stock(i):
        if not isinstance(i, int) or not 0 <= i < len(items) or items[i] is None:
            raise EmitError(f"{name}: {src} has no item {i!r}")
        return items[i]
    title_item = spec.get("title_item", 6)
    keep = sorted(set(spec.get("keep", [])) | ({title_item} if "title" in spec else set()))
    plan = []
    for i in keep:
        stock(i)
        plan.append((i, {"text": str(spec["title"])} if i == title_item and "title" in spec else None))
    top, row = float(spec.get("top", 28)), float(spec.get("row", 20))

    def placed(i, y, ov):
        w = stock(i)["window"]
        return (i, {**ov, "f32": {8: y, 28: y - w["rect"]["y"] + w["rectClient"]["y"]}})
    for k, r in enumerate(spec.get("rows", [])):
        y = top + row * k
        ov = {"name": r.get("name", f"{name}_row{k}"), "text": str(r.get("text", "")),
              "action": list(r.get("action", []))}
        plan.append(placed(r["copy"], y, ov))
        w = r.get("with")
        if w:
            sov = {"dvar": w["dvar"]} if w.get("dvar") else {}
            edit = {off: float(w[key]) for off, key in ((0, "min"), (4, "max"), (8, "default")) if key in w}
            if edit:
                sov["edit"] = edit
            if "action" in w:
                sov["action"] = list(w["action"])
            plan.append(placed(w["copy"], y, sov))
    mov = {"name": name}
    for key in MENU_KEYS:
        for k in (key, key + "+"):
            if k in spec:
                mov[k] = list(spec[k])
    return menu, plan, mov


def patch_menus(resolver: Resolver, specs: list[dict]) -> list:
    """[(menu, plan, menu overrides)] for patch_mp_menus.txt from specs. A spec is one of:
        {"popup": name, ...}                    a new popup (see popup_plan)
        {"menu": name, "from": stock menu, ...}  a new menu in a stock menu's style (see clone_plan)
        {"menu": name, "after": ..., ...}        an added button (see button_plan)
        {"menu": name, "onOpen+": [...], ...}   handlers added to (key+) or replacing (key) one
                                                 of a stock menu's onOpen / onClose /
                                                 onCloseRequest / onESC
        {"menu": name, "text": {old: new}}       new text for stock items, found by their name
                                                 or their stock text (e.g. "@MENU_START_GAME_CAPS")
        {"menu": name, "action": {old: [...]}}   a new action (script lines) for stock items, found
                                                 the same way
    Several specs may target one menu."""
    menus = resolver.menus()
    by_menu: dict[str, dict] = {}
    out = []
    for spec in specs:
        if "from" in spec:
            out.append(clone_plan(resolver, spec))
            continue
        if "popup" in spec:
            if spec["popup"] in menus:
                raise EmitError(f"popup {spec['popup']!r}: ui_mp already has a menu with that name")
            out.append(popup_plan(resolver, spec))
            continue
        if spec.get("menu") not in menus:
            raise EmitError(f"no menu named {spec.get('menu')!r} in ui_mp")
        g = by_menu.setdefault(spec["menu"], {"buttons": [], "mov": {}})
        if "after" in spec:
            g["buttons"].append(spec)
        if "remove" in spec:
            rm = spec["remove"]
            g.setdefault("removes", []).extend([rm] if isinstance(rm, str) else list(rm))
        if "text" in spec and "after" not in spec:
            if not isinstance(spec["text"], dict):
                raise EmitError(f"{spec['menu']}: \"text\" is {{\"item name or stock text\": \"new text\"}}")
            g.setdefault("texts", {}).update(spec["text"])
        if "action" in spec and "after" not in spec:
            if not isinstance(spec["action"], dict):
                raise EmitError(f"{spec['menu']}: \"action\" is {{\"item name or stock text\": [script lines]}}")
            g.setdefault("actions", {}).update(spec["action"])
        for key in MENU_KEYS:
            for k in (key, key + "+"):
                if k in spec:
                    g["mov"].setdefault(k, [])
                    g["mov"][k] += list(spec[k])
        if "after" not in spec and "remove" not in spec and "text" not in spec and "action" not in spec and \
                not any(k in spec for key in MENU_KEYS for k in (key, key + "+")):
            raise EmitError(f"{spec['menu']}: a menu spec needs \"after\" (a button), \"remove\", \"text\" or "
                            "an event like \"onOpen+\"")
    for name, g in by_menu.items():
        menu = menus[name]
        plan = (button_plan(resolver, menu, g["buttons"], g.get("removes", ()))
                if g["buttons"] or g.get("removes") else None)
        if g.get("texts"):
            plan = _retext(resolver, menu, plan, g["texts"])
        if g.get("actions"):
            plan = _retext(resolver, menu, plan, g["actions"], key="action")
        out.append((menu, plan, g["mov"] or None))
    return out


def _retext(resolver: Resolver, menu: Node, plan, texts: dict, key: str = "text") -> list:
    """plan with new text (or with key "action", a new action) on the stock items named (or
    labelled) like the keys of texts."""
    items = menu["items"]
    if plan is None:
        plan = [(i, None) for i in range(len(items))]

    def label(v):
        return None if v is None else resolver.string(v).decode("latin1") if isinstance(v, Ref) else str(v)
    done = set()
    out = []
    for i, ov in plan:
        it = items[i]
        # only the stock item itself, not a copy of it made for a new button
        if it is not None and not (ov and "name" in ov):
            for k in (label(it["window"]["name"]), label(it["text"])):
                if k in texts:
                    ov = {**(ov or {}), key: str(texts[k]) if key == "text" else list(texts[k])}
                    done.add(k)
                    break
        out.append((i, ov))
    missing = [k for k in texts if k not in done]
    if missing:
        raise EmitError(f"no item named or labelled {missing[0]!r} in this menu")
    return out


def describe(spec: dict) -> str:
    """One log line for a spec."""
    if "from" in spec:
        return f"menu {spec.get('menu')} (\"{spec.get('title', '')}\", {len(spec.get('rows', []))} rows, looks like {spec['from']})"
    if "popup" in spec:
        return f"popup {spec['popup']} (\"{spec.get('title', '')}\", {len(spec.get('lines', []))} lines)"
    if "after" in spec:
        return f"button \"{spec.get('text')}\" in {spec.get('menu')} (under {spec.get('after')})"
    if "action" in spec and isinstance(spec["action"], dict):
        return "new action in " + str(spec.get("menu")) + ": " + ", ".join(spec["action"])
    if "text" in spec and isinstance(spec["text"], dict):
        return "new text in " + str(spec.get("menu")) + ": " + ", ".join(f'"{v}"' for v in spec["text"].values())
    if "remove" in spec:
        rm = spec["remove"]
        return f"removed {', '.join([rm] if isinstance(rm, str) else rm)} from {spec.get('menu')}"
    keys = [k for key in MENU_KEYS for k in (key, key + "+") if k in spec]
    return f"{', '.join(keys)} in {spec.get('menu')}"
