"""Read the menu lists of an MW2 July 2009 (fastfile 0xFD) zone, keeping every field's offset.

Struct layouts come from default_mp.pdb and the load order from the game's own Load_*
functions in default_mp_dev.xex (Load_menuDef_t @ 0x821D5B18, Load_itemDef_t @ 0x821D5430,
Load_Statement @ 0x821CF338, Load_MenuEventHandlerSet @ 0x821D4A90, ...). Two pointer kinds:

    "asset-style" (XString, Statement, ExpressionSupportingData, Material, GfxImage, menuDef):
        0 = null, -1 / -2 = data follows inline, anything else = offset of data already loaded
    "plain" (event handler sets, handlers, key handlers, item pointers, type data, arrays):
        0 = null, anything else = data follows inline
"""

from __future__ import annotations

import struct

INLINE = (0xFFFFFFFF, 0xFFFFFFFE)


class MenuParseError(ValueError):
    pass


class Node(dict):
    """A struct read from the zone: values by field name, zone offsets in .off."""

    def __init__(self, kind: str, pos: int):
        super().__init__()
        self.kind = kind
        self.pos = pos
        self.off = {}


class Str(str):
    """An inline string with its zone offset (pos) and byte length (n)."""

    pos = 0
    n = 0


class Ref(int):
    """A pointer to data loaded earlier in the zone (shown read-only)."""


# (field, offset, kind, count). kind: f32 i32 u8 ptr
STRUCTS = {
    "rectDef_s": [("x", 0, "f32", 1), ("y", 4, "f32", 1), ("w", 8, "f32", 1), ("h", 12, "f32", 1),
                  ("horzAlign", 16, "u8", 1), ("vertAlign", 17, "u8", 1)],
    "windowDef_t": [("name", 0, "ptr", 1), ("rect", 4, "rectDef_s", 1), ("rectClient", 24, "rectDef_s", 1),
                    ("group", 44, "ptr", 1), ("style", 48, "i32", 1), ("border", 52, "i32", 1),
                    ("ownerDraw", 56, "i32", 1), ("ownerDrawFlags", 60, "i32", 1), ("borderSize", 64, "f32", 1),
                    ("staticFlags", 68, "i32", 1), ("dynamicFlags", 72, "i32", 4), ("nextTime", 88, "i32", 1),
                    ("foreColor", 92, "f32", 4), ("backColor", 108, "f32", 4), ("borderColor", 124, "f32", 4),
                    ("outlineColor", 140, "f32", 4), ("disableColor", 156, "f32", 4), ("background", 172, "ptr", 1)],
    "menuTransition": [("transitionType", 0, "i32", 1), ("targetField", 4, "i32", 1), ("startTime", 8, "i32", 1),
                       ("startVal", 12, "f32", 1), ("endVal", 16, "f32", 1), ("time", 20, "f32", 1),
                       ("endTriggerType", 24, "i32", 1)],
    "menuDef_t": [("window", 0, "windowDef_t", 1), ("font", 176, "ptr", 1), ("fullScreen", 180, "i32", 1),
                  ("itemCount", 184, "i32", 1), ("fontIndex", 188, "i32", 1), ("cursorItem", 192, "i32", 4),
                  ("fadeCycle", 208, "i32", 1), ("fadeClamp", 212, "f32", 1), ("fadeAmount", 216, "f32", 1),
                  ("fadeInAmount", 220, "f32", 1), ("blurRadius", 224, "f32", 1), ("onOpen", 228, "ptr", 1),
                  ("onCloseRequest", 232, "ptr", 1), ("onClose", 236, "ptr", 1), ("onESC", 240, "ptr", 1),
                  ("onKey", 244, "ptr", 1), ("visibleExp", 248, "ptr", 1), ("allowedBinding", 252, "ptr", 1),
                  ("soundName", 256, "ptr", 1), ("imageTrack", 260, "i32", 1), ("focusColor", 264, "f32", 4),
                  ("rectXExp", 280, "ptr", 1), ("rectYExp", 284, "ptr", 1), ("openSoundExp", 288, "ptr", 1),
                  ("closeSoundExp", 292, "ptr", 1), ("items", 296, "ptr", 1),
                  ("scaleTransition", 300, "menuTransition", 1), ("alphaTransition", 328, "menuTransition", 1),
                  ("xTransition", 356, "menuTransition", 1), ("yTransition", 384, "menuTransition", 1),
                  ("expressionData", 412, "ptr", 1)],
    "itemDef_s": [("window", 0, "windowDef_t", 1), ("textRect", 176, "rectDef_s", 4), ("type", 256, "i32", 1),
                  ("dataType", 260, "i32", 1), ("alignment", 264, "i32", 1), ("fontEnum", 268, "i32", 1),
                  ("textAlignMode", 272, "i32", 1), ("textalignx", 276, "f32", 1), ("textaligny", 280, "f32", 1),
                  ("textscale", 284, "f32", 1), ("textStyle", 288, "i32", 1), ("gameMsgWindowIndex", 292, "i32", 1),
                  ("gameMsgWindowMode", 296, "i32", 1), ("text", 300, "ptr", 1), ("itemFlags", 304, "i32", 1),
                  ("parent", 308, "ptr", 1), ("mouseEnterText", 312, "ptr", 1), ("mouseExitText", 316, "ptr", 1),
                  ("mouseEnter", 320, "ptr", 1), ("mouseExit", 324, "ptr", 1), ("action", 328, "ptr", 1),
                  ("accept", 332, "ptr", 1), ("onFocus", 336, "ptr", 1), ("leaveFocus", 340, "ptr", 1),
                  ("dvar", 344, "ptr", 1), ("dvarTest", 348, "ptr", 1), ("onKey", 352, "ptr", 1),
                  ("enableDvar", 356, "ptr", 1), ("dvarFlags", 360, "i32", 1), ("focusSound", 364, "ptr", 1),
                  ("special", 368, "f32", 1), ("cursorPos", 372, "i32", 4), ("typeData", 388, "ptr", 1),
                  ("imageTrack", 392, "i32", 1)]
                 + [(n, 396 + 4 * i, "ptr", 1) for i, n in enumerate(
                     ["visibleExp", "textExp", "materialExp", "disabledExp", "rectXExp", "rectYExp", "rectWExp",
                      "rectHExp", "forecolorRExp", "forecolorGExp", "forecolorBExp", "forecolorAExp",
                      "glowColorRExp", "glowColorGExp", "glowColorBExp", "glowColorAExp"])]
                 + [("glowColor", 460, "f32", 4), ("decayActive", 476, "u8", 1), ("fxBirthTime", 480, "i32", 1),
                    ("fxLetterTime", 484, "i32", 1), ("fxDecayStartTime", 488, "i32", 1),
                    ("fxDecayDuration", 492, "i32", 1), ("lastSoundPlayedTime", 496, "i32", 1)],
    "columnInfo_s": [("pos", 0, "i32", 1), ("width", 4, "i32", 1), ("maxChars", 8, "i32", 1),
                     ("alignment", 12, "i32", 1)],
    "listBoxDef_s": [("startPos", 0, "i32", 4), ("endPos", 16, "i32", 4), ("drawPadding", 32, "i32", 1),
                     ("elementWidth", 36, "f32", 1), ("elementHeight", 40, "f32", 1), ("elementStyle", 44, "i32", 1),
                     ("numColumns", 48, "i32", 1), ("columnInfo", 52, "columnInfo_s", 16),
                     ("onDoubleClick", 308, "ptr", 1), ("notselectable", 312, "i32", 1),
                     ("noScrollBars", 316, "i32", 1), ("usePaging", 320, "i32", 1), ("selectBorder", 324, "f32", 4),
                     ("selectIcon", 340, "ptr", 1)],
    "editFieldDef_s": [("minVal", 0, "f32", 1), ("maxVal", 4, "f32", 1), ("defVal", 8, "f32", 1),
                       ("range", 12, "f32", 1), ("maxChars", 16, "i32", 1), ("maxCharsGotoNext", 20, "i32", 1),
                       ("maxPaintChars", 24, "i32", 1), ("paintOffset", 28, "i32", 1)],
    "multiDef_s": [("dvarList", 0, "ptr", 32), ("dvarStr", 128, "ptr", 32), ("dvarValue", 256, "f32", 32),
                   ("count", 384, "i32", 1), ("strDef", 388, "i32", 1)],
    "newsTickerDef_s": [("feedId", 0, "i32", 1), ("speed", 4, "i32", 1), ("spacing", 8, "i32", 1),
                        ("lastTime", 12, "i32", 1), ("start", 16, "i32", 1), ("end", 20, "i32", 1),
                        ("x", 24, "f32", 1)],
    "textScrollDef_s": [("startTime", 0, "i32", 1)],
}
SIZES = {"rectDef_s": 20, "windowDef_t": 176, "menuTransition": 28, "menuDef_t": 416, "itemDef_s": 500,
         "columnInfo_s": 16, "listBoxDef_s": 344, "editFieldDef_s": 32, "multiDef_s": 392,
         "newsTickerDef_s": 28, "textScrollDef_s": 4}

EDITFIELD_TYPES = {0, 4, 9, 10, 11, 14, 16, 17, 18, 22, 23}  # Load_itemDefData_t @ 0x821D5280
TYPE_LISTBOX, TYPE_MULTI, TYPE_DVARENUM, TYPE_NEWSTICKER, TYPE_TEXTSCROLL = 6, 12, 13, 20, 21


class Parser:
    def __init__(self, zone: bytes):
        self.z = zone
        self.pos = 0
        self.strings = []  # (offset, length) of every inline string

    # ---- primitives ------------------------------------------------------------------
    def take(self, n: int) -> int:
        p = self.pos
        if p + n > len(self.z):
            raise MenuParseError(f"read past the end of the zone at 0x{p:X}")
        self.pos += n
        return p

    def u32_at(self, off):
        return struct.unpack_from(">I", self.z, off)[0]

    def cstring(self) -> Str:
        end = self.z.find(b"\0", self.pos)
        if end < 0:
            raise MenuParseError(f"unterminated string at 0x{self.pos:X}")
        s = Str(self.z[self.pos:end].decode("latin1"))
        s.pos, s.n = self.pos, end - self.pos
        self.strings.append((s.pos, s.n))
        self.pos = end + 1
        return s

    def xstring(self, ptr):
        if ptr == 0:
            return None
        if ptr in INLINE:
            return self.cstring()
        return Ref(ptr)

    def xstring_array(self, ptrs):
        return [self.xstring(p) for p in ptrs]

    def struct(self, kind: str, pos: int = None) -> Node:
        if pos is None:
            pos = self.take(SIZES[kind])
        node = Node(kind, pos)
        for name, off, k, count in STRUCTS[kind]:
            at = pos + off
            if k in ("f32", "i32", "u8", "ptr"):
                fmt, size = {"f32": (">f", 4), "i32": (">i", 4), "u8": (">B", 1), "ptr": (">I", 4)}[k]
                vals = [struct.unpack_from(fmt, self.z, at + size * i)[0] for i in range(count)]
                node[name] = vals[0] if count == 1 else vals
                node.off[name] = at
            else:
                subs = [self.struct(k, at + SIZES[k] * i) for i in range(count)]
                node[name] = subs[0] if count == 1 else subs
                node.off[name] = at
        return node

    # ---- asset-style pointers ------------------------------------------------------
    def statement_ptr(self, ptr):
        if ptr == 0:
            return None
        if ptr not in INLINE:
            return Ref(ptr)
        p = self.take(0x18)
        n, entries, supporting = self.u32_at(p), self.u32_at(p + 4), self.u32_at(p + 8)
        st = {"pos": p, "entries": [], "supportingData": None}
        if entries:
            base = self.take(12 * n)
            raw = [(base + 12 * i, *struct.unpack_from(">iII", self.z, base + 12 * i)) for i in range(n)]
            for at, typ, a, b in raw:
                e = {"pos": at, "type": typ, "a": a, "b": b}
                if typ != 0:  # operand: a = expDataType, b = value
                    if a == 2:
                        e["string"] = self.xstring(b)
                    elif a == 3:
                        e["function"] = self.statement_ptr(b)
                st["entries"].append(e)
        st["supportingData"] = self.supporting_ptr(supporting)
        return st

    def supporting_ptr(self, ptr):
        if ptr == 0:
            return None
        if ptr not in INLINE:
            return Ref(ptr)
        p = self.take(0x18)
        nf, fptr, nd, dptr, ns, sptr = struct.unpack_from(">6I", self.z, p)
        data = {"pos": p, "functions": [], "staticDvars": [], "strings": []}
        if fptr:
            base = self.take(4 * nf)
            for i in range(nf):
                data["functions"].append(self.statement_ptr(self.u32_at(base + 4 * i)))
        if dptr:
            base = self.take(4 * nd)
            for i in range(nd):
                if self.u32_at(base + 4 * i):
                    q = self.take(8)
                    data["staticDvars"].append(self.xstring(self.u32_at(q + 4)))
        if sptr:
            base = self.take(4 * ns)
            data["strings"] = self.xstring_array([self.u32_at(base + 4 * i) for i in range(ns)])
        return data

    def material_handle(self, ptr):
        if ptr == 0:
            return None
        if ptr not in INLINE:
            return Ref(ptr)
        p = self.take(0x58)
        name = self.xstring(self.u32_at(p))
        tech = self.u32_at(p + 0x40)
        if tech in INLINE:
            raise MenuParseError(f"inline technique set in material at 0x{p:X} is not supported")
        tex, const, sbits, subs = (self.u32_at(p + o) for o in (0x44, 0x48, 0x4C, 0x50))
        tex_n, const_n, sb_n, layers = self.z[p + 0x39], self.z[p + 0x3A], self.z[p + 0x3B], self.z[p + 0x3E]
        if tex == 0xFFFFFFFF:
            base = self.take(12 * tex_n)
            for i in range(tex_n):
                d = base + 12 * i
                if self.z[d + 7] == 11:
                    if self.u32_at(d + 8) in INLINE:
                        raise MenuParseError(f"inline water texture at 0x{d:X} is not supported")
                else:
                    self.image_ptr(self.u32_at(d + 8))
        if const == 0xFFFFFFFF:
            self.take(32 * const_n)
        if sbits == 0xFFFFFFFF:
            self.take(8 * sb_n)
        if subs == 0xFFFFFFFF:
            base = self.take(4 * layers)
            self.xstring_array([self.u32_at(base + 4 * i) for i in range(layers)])
        return ("material", name, p)

    def image_ptr(self, ptr):
        if ptr == 0 or ptr not in INLINE:
            return
        p = self.take(0x70)
        self.xstring(self.u32_at(p + 0x6C))
        if self.u32_at(p + 0x48):
            self.take(self.u32_at(p + 0x3C))

    def sound_ptr(self, ptr):
        if ptr in INLINE:
            raise MenuParseError(f"inline sound alias at 0x{self.pos:X} is not supported")
        return None if ptr == 0 else Ref(ptr)

    # ---- plain pointers ----------------------------------------------------------------
    def handler_set(self, ptr):
        if ptr == 0:
            return None
        p = self.take(8)
        count, arr = self.u32_at(p), self.u32_at(p + 4)
        hs = {"pos": p, "handlers": []}
        if arr:
            base = self.take(4 * count)
            for i in range(count):
                if not self.u32_at(base + 4 * i):
                    hs["handlers"].append(None)
                    continue
                h = self.take(8)
                data, etype = self.u32_at(h), self.z[h + 4]
                ev = {"pos": h, "type": etype}
                if etype == 0:
                    ev["script"] = self.xstring(data)
                elif etype == 1:
                    if data:
                        c = self.take(8)
                        ev["expression"] = self.statement_ptr(self.u32_at(c + 4))
                        ev["set"] = self.handler_set(self.u32_at(c))
                elif etype == 2:
                    ev["set"] = self.handler_set(data)
                elif etype in (3, 4, 5, 6):
                    if data:
                        c = self.take(8)
                        ev["var"] = self.xstring(self.u32_at(c))
                        ev["expression"] = self.statement_ptr(self.u32_at(c + 4))
                hs["handlers"].append(ev)
        return hs

    def key_handlers(self, ptr):
        out = []
        while ptr:
            p = self.take(12)
            key, action, nxt = struct.unpack_from(">iII", self.z, p)
            out.append({"pos": p, "key": key, "action": self.handler_set(action)})
            ptr = nxt
        return out

    def window(self, w: Node):
        w["name"] = self.xstring(w["name"])
        w["group"] = self.xstring(w["group"])
        w["background"] = self.material_handle(w["background"])

    def item(self) -> Node:
        it = self.struct("itemDef_s")
        self.window(it["window"])
        it["text"] = self.xstring(it["text"])
        for f in ("mouseEnterText", "mouseExitText", "mouseEnter", "mouseExit", "action", "accept",
                  "onFocus", "leaveFocus"):
            it[f] = self.handler_set(it[f])
        it["dvar"] = self.xstring(it["dvar"])
        it["dvarTest"] = self.xstring(it["dvarTest"])
        it["onKey"] = self.key_handlers(it["onKey"])
        it["enableDvar"] = self.xstring(it["enableDvar"])
        it["focusSound"] = self.sound_ptr(it["focusSound"])
        it["typeData"] = self.type_data(it["type"], it["typeData"])
        for name, off, k, c in STRUCTS["itemDef_s"]:
            if name.endswith("Exp"):
                it[name] = self.statement_ptr(it[name])
        it["parent"] = None
        return it

    def type_data(self, typ, ptr):
        if not ptr:
            return None
        if typ == TYPE_LISTBOX:
            lb = self.struct("listBoxDef_s")
            lb["onDoubleClick"] = self.handler_set(lb["onDoubleClick"])
            lb["selectIcon"] = self.material_handle(lb["selectIcon"])
            return lb
        if typ in EDITFIELD_TYPES:
            return self.struct("editFieldDef_s")
        if typ == TYPE_MULTI:
            m = self.struct("multiDef_s")
            m["dvarList"] = self.xstring_array(m["dvarList"])
            m["dvarStr"] = self.xstring_array(m["dvarStr"])
            return m
        if typ == TYPE_DVARENUM:
            return {"enumDvarName": self.xstring(ptr)}
        if typ == TYPE_NEWSTICKER:
            return self.struct("newsTickerDef_s")
        if typ == TYPE_TEXTSCROLL:
            return self.struct("textScrollDef_s")
        return None

    def menu(self) -> Node:
        m = self.struct("menuDef_t")
        m["expressionData"] = self.supporting_ptr(m["expressionData"])
        self.window(m["window"])
        m["font"] = self.xstring(m["font"])
        for f in ("onOpen", "onCloseRequest", "onClose", "onESC"):
            m[f] = self.handler_set(m[f])
        m["onKey"] = self.key_handlers(m["onKey"])
        m["visibleExp"] = self.statement_ptr(m["visibleExp"])
        m["allowedBinding"] = self.xstring(m["allowedBinding"])
        m["soundName"] = self.xstring(m["soundName"])
        for f in ("rectXExp", "rectYExp", "openSoundExp", "closeSoundExp"):
            m[f] = self.statement_ptr(m[f])
        items = []
        if m["items"]:
            base = self.take(4 * m["itemCount"])
            for i in range(m["itemCount"]):
                items.append(self.item() if self.u32_at(base + 4 * i) else None)
        m["items"] = items
        return m

    def menu_list(self, pos: int):
        self.pos = pos
        p = self.take(12)
        name = self.xstring(self.u32_at(p))
        count, arr = self.u32_at(p + 4), self.u32_at(p + 8)
        menus = []
        if arr:
            base = self.take(4 * count)
            for i in range(count):
                ptr = self.u32_at(base + 4 * i)
                if ptr in INLINE:
                    menus.append(self.menu())
                else:
                    menus.append(None if ptr == 0 else Ref(ptr))
        return {"pos": p, "name": name, "count_off": p + 4, "menus": menus, "end": self.pos}


def find_menu_lists(zone: bytes):
    """Every MenuList (named *.txt or *.menu) in the zone that parses cleanly and holds at least
    one inline menu, as returned by Parser.menu_list. Menus shared with an earlier list are Refs."""
    import re
    out = []
    for m in re.finditer(rb"\xff\xff\xff\xff(....)\xff\xff\xff\xff([A-Za-z0-9_/\\.\-]+\.(?:txt|menu))\x00", zone, re.S):
        try:
            ml = Parser(zone).menu_list(m.start())
        except (MenuParseError, struct.error, IndexError):
            continue
        if any(isinstance(x, Node) for x in ml["menus"]):
            out.append(ml)
    return out
