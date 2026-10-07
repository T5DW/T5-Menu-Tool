"""Read a .menu text file (the decompiled format) back into a menu tree.

This is the reverse of menufile.Writer and is used for NEW menus, which the rebuilder
writes from scratch: unlike in-place edits, a new menu can have any number of items,
handlers and expression tokens, and strings of any length.

Fields the text leaves out (runtime-only state, 'next' links) are rebuilt: runtime fields
start at 0 and chains are linked in the order their blocks appear. Count fields
(itemCount, numRpn, ...) are recomputed when the menu is written.
"""

import struct

from . import opcodes, schema
from .menufile import HIDDEN, MenuEditError, parse_text, unquote
from .parser import Node
from .schema import STRUCTS

SCALARS = ("i32", "u32", "f32", "u8", "u64", "pad")
LIST_CHILD = {"menuArray": ("menuDef", "menuDef_t"), "itemArray": ("itemDef", "itemDef_s"),
              "animStateArray": ("animState", "animParamsDef_t"), "rowArray": ("row", "MenuRow"),
              "cellArray": ("cell", "MenuCell")}


# Hidden runtime fields that are not 0 in the stock menus.
RUNTIME_DEFAULTS = {("menuDef_t", "cursorItem"): -1, ("menuDef_t", "fadeTimeCounter"): -1,
                    ("menuDef_t", "slideTimeCounter"): -1}


def new_node(type_name):
    n = Node(type_name, None)
    for fname, kind, target, count, _ in STRUCTS[type_name]:
        if kind == "pad":
            continue
        if kind in SCALARS:
            d = RUNTIME_DEFAULTS.get((type_name, fname), 0)
            n[fname] = d if count == 1 else [d] * count
        elif kind == "struct":
            n[fname] = new_node(target) if count == 1 else [new_node(target) for _ in range(count)]
        else:
            n[fname] = None if count == 1 else [None] * count
            n.ptrs[fname] = 0 if count == 1 else [0] * count
    return n


def all_scalar(type_name):
    return all(k in SCALARS for _, k, _, _, _ in STRUCTS[type_name])


class MenuTextReader:
    def __init__(self, text, filename="", material_ref=None, find_file=None):
        self.find_file = find_file
        self.lines = parse_text(text)
        self.i = 0
        self.filename = filename
        self.material_ref = material_ref

    # -- cursor
    def err(self, msg):
        line = " ".join(self.lines[self.i])[:80] if self.i < len(self.lines) else "end of file"
        return MenuEditError(f"{self.filename}: value line {self.i + 1} ('{line}'): {msg}")

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def take(self, key=None):
        toks = self.peek()
        if toks is None:
            raise self.err("unexpected end of file")
        if key is not None and toks[0] != key:
            raise self.err(f"expected '{key}'")
        self.i += 1
        return toks

    def close(self):
        if self.take()[0] != "}":
            self.i -= 1
            raise self.err("expected '}'")

    # -- values
    def scalar(self, kind, tok):
        try:
            if kind == "f32":
                if tok.startswith("f32:"):
                    return struct.unpack(">f", struct.pack(">I", int(tok[4:], 16)))[0]
                return float(tok)
            v = int(tok, 0)
            if kind == "u8" and not 0 <= v <= 255:
                raise ValueError("must be 0-255")
            return v
        except ValueError as e:
            raise self.err(f"bad {kind} value {tok!r} ({e})") from None

    def string(self, tok):
        if tok == "null":
            return None
        if tok.startswith("ref:"):
            return ("ref", int(tok[4:], 16))
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            s = unquote(tok)
            if b"\0" in s:
                raise self.err("strings can't contain \\x00")
            return s
        raise self.err(f"expected a quoted string, null or ref:, got {tok}")

    # -- structure
    def menu(self):
        toks = self.take("menuDef")
        if toks[1:] != ["{"]:
            raise self.err("expected 'menuDef {'")
        m = self.block("menuDef_t", {})
        if self.peek() is not None:
            raise self.err("unexpected text after the menu")
        return m

    def block(self, type_name, ctx):
        """Fields of a struct whose opening '{' was already read, up to and including '}'."""
        node = new_node(type_name)
        self.fields(node, ctx)
        self.close()
        return node

    def one_line(self, type_name, toks):
        node = new_node(type_name)
        vals = toks[1:]
        k = 0
        for fname, kind, _, count, _ in STRUCTS[type_name]:
            if kind == "pad" or (type_name, fname) in HIDDEN:
                continue
            got = []
            for _ in range(count):
                if k >= len(vals):
                    raise self.err("not enough values")
                got.append(self.scalar(kind, vals[k]))
                k += 1
            node[fname] = got[0] if count == 1 else got
        if k != len(vals):
            raise self.err("too many values")
        return node

    def struct_value(self, key, type_name, ctx):
        toks = self.take(key)
        if all_scalar(type_name):
            return self.one_line(type_name, toks)
        if toks[1:] != ["{"]:
            raise self.err(f"expected '{key} {{'")
        return self.block(type_name, ctx)

    def fields(self, node, ctx):
        t = node.type_name
        if t == "itemDef_s":
            ctx = dict(ctx, item=node)
        elif t == "listBoxDef_s":
            ctx = dict(ctx, listbox=node)
        for fname, kind, target, count, flag in STRUCTS[t]:
            if kind == "pad" or (t, fname) in HIDDEN:
                continue
            if kind == "struct":
                if flag == "noload":
                    continue
                subs = []
                for _ in range(count):
                    if target == "ExpressionStatement":
                        subs.append(self.expression(fname, embedded=True))
                    else:
                        subs.append(self.struct_value(fname, target, ctx))
                node[fname] = subs[0] if count == 1 else subs
                continue
            if kind == "ptr":
                if fname == "next" or target == "never":
                    continue
                if count == 1:
                    node[fname] = self.pointer(fname, target, node, ctx)
                else:
                    node[fname] = [self.pointer(fname, target, node, ctx) for _ in range(count)]
                continue
            toks = self.take(fname)
            vals = [self.scalar(kind, v) for v in toks[1:]]
            if len(vals) != count:
                raise self.err(f"expected {count} value(s)")
            node[fname] = vals[0] if count == 1 else vals

    def target_struct(self, target, parent, ctx):
        """Which struct a pointer field points at (mirrors MenuParser.follow)."""
        if target == "itemTypeData":
            ty = parent["type"]
            for types, name in ((schema.TEXTDEF_TYPES, "textDef_s"), (schema.IMAGEDEF_TYPES, "imageDef_s"),
                                (schema.BLANKBUTTON_TYPES, "focusItemDef_s"),
                                (schema.OWNERDRAW_TYPES, "ownerDrawDef_s")):
                if ty in types:
                    return name
            return None
        if target == "textTypeData":
            ty = ctx["item"]["type"]
            if ty in schema.FOCUS_TYPES:
                return "focusItemDef_s"
            if ty in schema.GAMEMSG_TYPES:
                return "gameMsgDef_s"
            return None
        if target == "focusTypeData":
            ty = ctx["item"]["type"]
            for types, name in ((schema.LISTBOX_TYPES, "listBoxDef_s"), (schema.MULTI_TYPES, "multiDef_s"),
                                (schema.EDITFIELD_TYPES, "editFieldDef_s"),
                                (schema.ENUMDVAR_TYPES, "enumDvarDef_s")):
                if ty in types:
                    return name
            return None
        return target if target in STRUCTS else None

    def pointer(self, fname, target, parent, ctx):
        toks = self.peek()
        if toks is None or toks[0] != fname:
            raise self.err(f"expected '{fname}'")
        arg = toks[1] if len(toks) > 1 else ""
        if target == "string":
            self.take()
            return self.string(arg)
        if arg == "null" and len(toks) == 2:
            self.take()
            return None
        if arg.startswith("ref:") and len(toks) == 2:
            self.take()
            return ("ref", int(arg[4:], 16))
        if target == "material":
            self.take()
            if arg == "image" and len(toks) == 3:  # a custom picture: image "file.png"
                path = unquote(toks[2]).decode("utf-8", "replace")
                found = self.find_file(path) if self.find_file else path
                if found is None:
                    self.i -= 1
                    raise self.err(f"image file {path!r} not found")
                return ("newimage", found)
            if arg != "material" or len(toks) != 3:
                self.i -= 1
                raise self.err('expected null, ref:0x..., material "name" or image "file.png"')
            key = int(toks[2][4:], 16) if toks[2].startswith("ref:") else unquote(toks[2]).decode("latin1")
            r = self.material_ref(key) if self.material_ref else None
            if r is None:
                self.i -= 1
                raise self.err("that material isn't loaded inline by any stock menu; use the ref: of "
                               "an item that already shows it, or image \"file.png\"")
            return r if isinstance(r, tuple) else ("ref", r)
        if target in ("char32", "cellString"):
            self.take()
            data = self.string(arg)
            n = 32 if target == "char32" else parent["maxChars"]
            if not isinstance(data, bytes):
                raise self.err("expected a quoted string")
            if len(data) >= n:
                raise self.err(f"at most {n - 1} characters fit here")
            return ("newbytes", data, n)
        if target in LIST_CHILD:
            child_key, child_type = LIST_CHILD[target]
            self.take()
            if toks[1:] != ["{"]:
                raise self.err(f"expected '{fname} {{'")
            out = []
            while self.peek() and self.peek()[0] != "}":
                c = self.peek()
                if c[0] != child_key:
                    raise self.err(f"expected '{child_key}'")
                if c[1:] == ["null"]:
                    self.take()
                    out.append(None)
                elif len(c) == 2 and c[1].startswith("ref:"):
                    self.take()
                    out.append(("ref", int(c[1][4:], 16)))
                else:
                    out.append(self.struct_value(child_key, child_type, ctx))
            self.close()
            return out
        if target == "ExpressionStatement":
            return self.expression(fname, embedded=False)
        sname = self.target_struct(target, parent, ctx)
        if sname is None:
            raise self.err(f"this item type has no '{fname}' data; use null")
        first = self.struct_value(fname, sname, ctx)
        if "next" not in first.ptrs:
            return first
        chain = [first]
        while self.peek() and self.peek()[0] == fname and self.peek()[1:] == ["{"]:
            chain.append(self.struct_value(fname, sname, ctx))
        for a, b in zip(chain, chain[1:]):
            a["next"] = b
        nxt = self.peek()
        if nxt and nxt[0] == fname and len(nxt) == 2 and nxt[1].startswith("ref:"):
            self.take()
            chain[-1]["next"] = ("ref", int(nxt[1][4:], 16))
        return first

    def expression(self, key, embedded):
        toks = self.take(key)
        node = new_node("ExpressionStatement")
        if toks[1:] == ["none"]:
            return node
        if toks[1:] != ["{"]:
            raise self.err(f"expected '{key} none' or '{key} {{'")
        node["filename"] = self.string(self.take("filename")[1])
        node["line"] = self.scalar("i32", self.take("line")[1])
        rpn = self.take("rpn")[1:]
        if rpn == ["null"]:
            node["rpn"] = None
        elif rpn[:1] == ["shared"]:  # rpn shared ref:0x... <count>
            if len(rpn) != 3 or not rpn[1].startswith("ref:"):
                raise self.err("expected 'rpn shared ref:0x... <count>'")
            node["rpn"] = ("ref", int(rpn[1][4:], 16))
            node["numRpn"] = self.scalar("i32", rpn[2])
        else:
            node["rpn"] = [self.rpn_entry(t) for t in rpn]
        self.close()
        return node

    def rpn_entry(self, tok):
        if tok == "end":
            return {"type": 3, "dataType": 0, "value": 0}
        if tok == "null":
            return {"type": 0, "dataType": 2, "value": 0}
        if tok.startswith("ref:"):
            return {"type": 0, "dataType": 2, "value": int(tok[4:], 16)}
        if tok.startswith('"'):
            return {"type": 0, "dataType": 2, "value": 0xFFFFFFFF, "string": self.string(tok)}
        if tok.startswith("raw:"):
            _, t, dt, v = tok.split(":")
            return {"type": int(t), "dataType": int(dt), "value": int(v, 16)}
        try:
            v = int(tok, 10)
            return {"type": 0, "dataType": 0, "value": v & 0xFFFFFFFF}
        except ValueError:
            pass
        if tok.startswith("f32:") or any(c in tok for c in ".eE") and tok[:1] in "-0123456789.":
            try:
                v = self.scalar("f32", tok)
                return {"type": 0, "dataType": 1, "value": struct.unpack(">I", struct.pack(">f", v))[0]}
            except MenuEditError:
                pass
        try:
            return {"type": 1, "dataType": opcodes.command_index(tok), "value": 0}
        except ValueError as e:
            raise self.err(str(e)) from None


def read_menu(text, filename="", material_ref=None, find_file=None) -> Node:
    """find_file(path) -> real path (or None) resolves image "file.png" lines."""
    return MenuTextReader(text, filename, material_ref, find_file).menu()


def read_item(text, filename="", material_ref=None, find_file=None) -> Node:
    """One 'itemDef { ... }' block -> itemDef_s node."""
    r = MenuTextReader(text, filename, material_ref, find_file)
    node = r.struct_value("itemDef", "itemDef_s", {})
    if r.peek() is not None:
        raise r.err("unexpected text after the item")
    return node


AUTO_BACKGROUND = "xcp_auto_background"  # window name of the item an images/ picture becomes
_AUTO_BG_ITEM = """itemDef {
  window {
    name "%s"
    rect -107.0 0.0 854.0 480.0 0 0
    rectClient -107.0 0.0 854.0 480.0 0 0
    group null
    style 3
    border 0
    modal 0
    frameSides 0
    frameTexSize 0.0
    frameSize 0.0
    ownerDraw 0
    ownerDrawFlags 0
    borderSize 1.0
    staticFlags 1048576
    dynamicFlags 65540 65540 65540 65540
    foreColor 1.0 1.0 1.0 1.0
    backColor 0.0 0.0 0.0 0.0
    borderColor 0.0 0.0 0.0 0.0
    outlineColor 0.0 0.0 0.0 0.0
    rotation 0.0
    background image %s
  }
  type 0
  dataType 0
  imageTrack 0
  dvar null
  dvarTest null
  enableDvar null
  dvarFlags 0
  typeData null
  rectExpData null
  visibleExp none
  showBits 0x0000000000000000
  hideBits 0xFFFFFFFFFFFFFFFF
  forecolorAExp none
  ui3dWindowId -1
  onEvent null
  animInfo null
}
"""


def background_item(picture_path) -> Node:
    """A full-screen item showing a picture, set up like the stock main-menu background."""
    q = '"' + str(picture_path).replace("\\", "\\\\").replace('"', '\\"') + '"'
    return read_item(_AUTO_BG_ITEM % (AUTO_BACKGROUND, q), "background", None, lambda p: p)


def set_auto_background(menu: Node, picture_path) -> None:
    """Replace any earlier auto background item with one for picture_path, first in the menu
    so it is drawn behind everything else. With no picture the menu is left as it is, so a
    background added on an earlier compile survives decompiling (delete its itemDef to drop it)."""
    if not picture_path:
        return
    def is_auto(it):
        if not isinstance(it, Node):
            return False
        name = it["window"]["name"]
        if isinstance(name, str):
            name = name.encode("latin1")
        return isinstance(name, bytes) and name == AUTO_BACKGROUND.encode()  # refs never match
    items = [it for it in (menu["items"] or []) if not is_auto(it)]
    items.insert(0, background_item(picture_path))
    menu["items"] = items
