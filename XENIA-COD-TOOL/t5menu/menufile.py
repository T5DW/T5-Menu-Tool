"""Decompile T5 Xbox 360 menus to editable text, and patch edits back into the zone.

The text is a faithful dump of the zone structures, one menu per file. Compiling walks
the original zone again, regenerates the same token stream and patches every value that
changed at the zone offset it came from. That keeps the zone layout byte-identical, so:

* numbers, colours, rects, flags and expression constants can be changed freely;
* inline strings can be changed if they keep their length. Script actions and display
  text may also get shorter (they are padded with spaces);
* structure (adding items, handlers, expression tokens) cannot change in place; new menus
  are written whole by rebuild.py instead (see menutext.py);
* "ref:0x..." values point at data loaded earlier in the zone and are read-only.
"""

import math
import re
import struct
import sys

from . import opcodes
from .parser import MenuParser, Node, ZStr
from .schema import STRUCTS
from .zone import ZoneParseError, is_inline, read_header

MENULIST_TYPE = 22  # 360 asset type ids (PC OpenAssetTools enum + 1 here)
MENU_TYPE = 23

# Runtime-only fields, not written to the text (kept as-is on compile; new menus get the
# values every stock menu has, see menutext.RUNTIME_DEFAULTS). windowDef_t.dynamicFlags is
# shown: it holds the initial visible flag (4) and differs between items.
HIDDEN = {
    ("windowDef_t", "nextTime"),
    ("menuDef_t", "cursorItem"), ("menuDef_t", "fadeTimeCounter"), ("menuDef_t", "slideTimeCounter"),
    ("listBoxDef_s", "cursorPos"), ("listBoxDef_s", "startPos"), ("listBoxDef_s", "endPos"),
    ("editFieldDef_s", "cursorPos"), ("editFieldDef_s", "paintOffset"),
    ("UIAnimInfo", "animating"), ("UIAnimInfo", "animStartTime"), ("UIAnimInfo", "animDuration"),
    ("itemDef_s", "parent"),
}
# Fields that size other data; changing them would change the layout.
READONLY = {
    ("menuDef_t", "itemCount"), ("MenuList", "menuCount"), ("ExpressionStatement", "numRpn"),
    ("listBoxDef_s", "numColumns"), ("listBoxDef_s", "maxRows"), ("MenuCell", "maxChars"),
    ("UIAnimInfo", "animStateCount"), ("itemDef_s", "type"),
}
# Strings that may get shorter (padded with trailing spaces).
PAD_OK = {("GenericEventScript", "action"), ("textDef_s", "text")}


class MenuEditError(ValueError):
    pass


# ---------------------------------------------------------------------------------
# value formatting


def fmt_float(v):
    if math.isnan(v) or math.isinf(v):
        return "f32:0x%08X" % struct.unpack(">I", struct.pack(">f", v))[0]
    for digits in range(1, 10):
        s = "%.*g" % (digits, v)
        if struct.pack(">f", float(s)) == struct.pack(">f", v):
            break
    if "e" in s and 1e-4 <= abs(v) < 1e9:
        s = ("%.10f" % float(s)).rstrip("0")
    if not re.search(r"[.eE]", s):
        s += ".0"
    elif s.endswith("."):
        s += "0"
    return s


def quote(b: bytes) -> str:
    out = ['"']
    for c in b:
        ch = chr(c)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= c < 127:
            out.append(ch)
        else:
            out.append("\\x%02X" % c)
    out.append('"')
    return "".join(out)


def unquote(tok: str) -> bytes:
    assert tok[0] == '"' and tok[-1] == '"'
    body = tok[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            nxt = body[i + 1]
            if nxt == "n":
                out.append(10)
            elif nxt == "t":
                out.append(9)
            elif nxt == "x":
                out.append(int(body[i + 2 : i + 4], 16))
                i += 2
            else:
                out.append(ord(nxt))
            i += 2
        else:
            out.extend(ch.encode("latin1"))
            i += 1
    return bytes(out)


TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')


def tokenize_line(line: str):
    return TOKEN_RE.findall(line)


# ---------------------------------------------------------------------------------
# slots: where each editable token lives in the zone


class Slot:
    __slots__ = ("kind", "offset", "size", "pad_ok", "label")

    def __init__(self, kind, offset, size=0, pad_ok=False, label=""):
        self.kind, self.offset, self.size, self.pad_ok, self.label = kind, offset, size, pad_ok, label

    def encode(self, token: str) -> bytes:
        k = self.kind
        try:
            if k == "i32":
                return struct.pack(">i", int(token, 0))
            if k == "u32":
                return struct.pack(">I", int(token, 0) & 0xFFFFFFFF)
            if k == "u8":
                v = int(token, 0)
                if not 0 <= v <= 255:
                    raise ValueError("must be 0-255")
                return bytes([v])
            if k == "u64":
                return struct.pack(">Q", int(token, 0) & 0xFFFFFFFFFFFFFFFF)
            if k == "op":
                return struct.pack(">i", opcodes.command_index(token))
            if k == "f32":
                if token.startswith("f32:"):
                    return struct.pack(">I", int(token[4:], 16))
                return struct.pack(">f", float(token))
        except (ValueError, struct.error) as e:
            raise MenuEditError(f"{self.label}: bad value {token!r} ({e})") from None
        if k in ("string", "bytes"):
            if not (token.startswith('"') and token.endswith('"') and len(token) >= 2):
                raise MenuEditError(f"{self.label}: expected a quoted string, got {token}")
            data = unquote(token)
            if b"\0" in data:
                raise MenuEditError(f"{self.label}: strings cannot contain \\x00")
            if k == "bytes":
                if len(data) >= self.size:
                    raise MenuEditError(f"{self.label}: at most {self.size - 1} characters fit here")
                return data.ljust(self.size, b"\0")
            if len(data) > self.size:
                raise MenuEditError(
                    f"{self.label}: new text is {len(data)} characters, the original space is "
                    f"{self.size}. Growing strings is not supported yet; shorten it.")
            if len(data) < self.size:
                if not self.pad_ok:
                    raise MenuEditError(
                        f"{self.label}: this string must stay exactly {self.size} characters "
                        f"(only script actions and display text can get shorter)")
                data = data.ljust(self.size, b" ")
            return data
        raise MenuEditError(f"{self.label}: value is read-only")


# ---------------------------------------------------------------------------------
# writer


class Writer:
    def __init__(self):
        self.lines = []  # (indent, tokens:list[str], slots:list[Slot|None])

    def line(self, indent, tokens, slots=None):
        self.lines.append((indent, tokens, slots or [None] * len(tokens)))

    def text(self):
        out = []
        for indent, tokens, _ in self.lines:
            out.append("  " * indent + " ".join(tokens))
        return "\n".join(out) + "\n"

    # -- emitters ------------------------------------------------------------
    def node(self, indent, key, node: Node, path):
        spec = STRUCTS[node.type_name]
        if all(k in ("i32", "u32", "f32", "u8", "u64", "pad") for _, k, _, _, _ in spec):
            # Small all-scalar structs (rects, columns) go on one line.
            toks, slots = [key], [None]
            for fname, kind, _, count, _ in spec:
                if kind == "pad" or (node.type_name, fname) in HIDDEN:
                    continue
                offs, vals = node.offsets[fname], node[fname]
                if count == 1:
                    offs, vals = [offs], [vals]
                ro = (node.type_name, fname) in READONLY
                for o, v in zip(offs, vals):
                    toks.append(fmt_float(v) if kind == "f32" else str(v))
                    slots.append(None if ro else Slot(kind, o, label="/".join(path + [key, fname])))
            self.line(indent, toks, slots)
            return
        self.line(indent, [key, "{"])
        self.fields(indent + 1, node, path + [key])
        self.line(indent, ["}"])

    def fields(self, indent, node: Node, path):
        t = node.type_name
        for fname, kind, target, count, flag in STRUCTS[t]:
            if kind == "pad" or (t, fname) in HIDDEN:
                continue
            label = "/".join(path + [fname])
            if kind == "struct":
                if flag == "noload":
                    continue
                v = node[fname]
                for sub in (v if isinstance(v, list) else [v]):
                    if sub.type_name == "ExpressionStatement":
                        self.expression(indent, fname, sub, path)
                    else:
                        self.node(indent, fname, sub, path)
                continue
            if kind == "ptr":
                if fname == "next":
                    continue
                if count == 1:
                    self.pointer(indent, fname, node[fname], node, target, path, label)
                else:
                    for i, v in enumerate(node[fname]):
                        self.pointer(indent, fname, v, node, target, path, f"{label}[{i}]", offset=node.offsets[fname][i])
                continue
            # scalar
            offs = node.offsets[fname]
            vals = node[fname]
            if count == 1:
                offs, vals = [offs], [vals]
            toks, slots = [fname], [None]
            ro = (t, fname) in READONLY
            for o, v in zip(offs, vals):
                toks.append(fmt_float(v) if kind == "f32" else ("0x%016X" % v if kind == "u64" else str(v)))
                slots.append(None if ro else Slot(kind, o, label=label))
            self.line(indent, toks, slots)

    def string_token(self, value, owner_type, fname, label):
        if value is None:
            return "null", None
        if isinstance(value, tuple) and value[0] == "ref":
            return "ref:0x%08X" % value[1], None
        if isinstance(value, ZStr):
            pad = (owner_type, fname) in PAD_OK
            return quote(value), Slot("string", value.pos, len(value), pad, label)
        raise TypeError(value)

    def pointer(self, indent, fname, value, owner, target, path, label, offset=None):
        t = owner.type_name
        if target == "string":
            tok, slot = self.string_token(value, t, fname, label)
            self.line(indent, [fname, tok], [None, slot])
            return
        if value is None:
            self.line(indent, [fname, "null"])
            return
        if isinstance(value, tuple):
            if value[0] == "ref":
                self.line(indent, [fname, "ref:0x%08X" % value[1]])
            elif value[0] == "material":
                if value[1]:
                    name_tok = quote(value[1].encode("latin1"))
                else:  # name shared with an earlier asset: show the name's ref
                    name_tok = "ref:0x%08X" % struct.unpack_from(">I", self.zone, value[2])[0]
                self.line(indent, [fname, "material", name_tok])
            elif value[0] == "bytes":
                _, start, n = value
                raw = owner_bytes = self.zone[start : start + n]
                s = owner_bytes.split(b"\0")[0]
                self.line(indent, [fname, quote(s)], [None, Slot("bytes", start, n, label=label)])
            return
        if isinstance(value, list):
            child_key = {"menuArray": "menuDef", "itemArray": "itemDef", "animStateArray": "animState",
                         "rowArray": "row", "cellArray": "cell"}.get(target, fname)
            self.line(indent, [fname, "{"])
            for i, v in enumerate(value):
                if isinstance(v, Node):
                    self.node(indent + 1, child_key, v, path + [f"{fname}[{i}]"])
                elif v is None:
                    self.line(indent + 1, [child_key, "null"])
                else:
                    self.line(indent + 1, [child_key, "ref:0x%08X" % v[1]])
            self.line(indent, ["}"])
            return
        if isinstance(value, Node):
            if value.type_name == "ExpressionStatement":
                self.expression(indent, fname, value, path)
                return
            # flatten "next" chains into repeated blocks
            chain = [value]
            while "next" in chain[-1].ptrs and isinstance(chain[-1]["next"], Node):
                chain.append(chain[-1]["next"])
            for i, n in enumerate(chain):
                self.node(indent, fname, n, path + ([f"{fname}#{i}"] if len(chain) > 1 else []))
            tail = chain[-1].get("next") if "next" in chain[-1].ptrs else None
            if isinstance(tail, tuple):
                self.line(indent, [fname, "ref:0x%08X" % tail[1]])
            return
        raise TypeError(f"unexpected pointer value {value!r}")

    def expression(self, indent, key, exp: Node, path):
        label = "/".join(path + [key])
        if exp["numRpn"] == 0 and exp["filename"] is None:
            self.line(indent, [key, "none"])
            return
        self.line(indent, [key, "{"])
        fn_tok, fn_slot = self.string_token(exp["filename"], "ExpressionStatement", "filename", label + "/filename")
        self.line(indent + 1, ["filename", fn_tok], [None, fn_slot])
        self.line(indent + 1, ["line", str(exp["line"])], [None, Slot("i32", exp.offsets["line"], label=label + "/line")])
        rpn = exp["rpn"]
        if isinstance(rpn, list):
            toks, slots = ["rpn"], [None]
            for e in rpn:
                t, dt, val = e["type"], e["dataType"], e["value"]
                pos = e["pos"]
                if t == 0:
                    if dt == 0:
                        toks.append(str(struct.unpack(">i", struct.pack(">I", val))[0]))
                        slots.append(Slot("i32", pos + 8, label=label + "/rpn"))
                    elif dt == 1:
                        toks.append(fmt_float(struct.unpack(">f", struct.pack(">I", val))[0]))
                        slots.append(Slot("f32", pos + 8, label=label + "/rpn"))
                    elif "string" in e:
                        toks.append(quote(e["string"]))
                        slots.append(Slot("string", e["string"].pos, len(e["string"]), False, label + "/rpn"))
                    elif val == 0:
                        toks.append("null")
                        slots.append(None)
                    else:
                        toks.append("ref:0x%08X" % val)
                        slots.append(None)
                elif t == 1:
                    toks.append(opcodes.command_name(dt))
                    slots.append(Slot("op", pos + 4, label=label + "/rpn"))
                elif t == 3:
                    toks.append("end")
                    slots.append(None)
                else:
                    toks.append("raw:%d:%d:0x%X" % (t, dt, val))
                    slots.append(None)
            self.line(indent + 1, toks, slots)
            infix = rpn_to_infix(rpn)
            if infix:
                self.line(indent + 1, ["//", infix])
        elif rpn is None:
            self.line(indent + 1, ["rpn", "null"])
        else:
            # a shared token array: the count is part of the value
            self.line(indent + 1, ["rpn", "shared", "ref:0x%08X" % rpn[1], str(exp["numRpn"])])
        self.line(indent, ["}"])


def rpn_to_infix(rpn):
    """Best-effort readable form; the rpn line is what gets compiled."""
    stack = []
    try:
        for e in rpn:
            t, dt = e["type"], e["dataType"]
            if t == 0:
                if dt == 0:
                    stack.append(str(struct.unpack(">i", struct.pack(">I", e["value"]))[0]))
                elif dt == 1:
                    stack.append(fmt_float(struct.unpack(">f", struct.pack(">I", e["value"]))[0]))
                elif "string" in e:
                    stack.append(quote(e["string"]))
                else:
                    stack.append("<shared string>")
            elif t == 1:
                name = opcodes.command_name(dt)
                if dt == 0x12:  # comma: join arguments
                    b, a = stack.pop(), stack.pop()
                    stack.append(f"{a}, {b}")
                elif dt == 0x07:
                    stack.append(f"-{stack.pop()}")
                elif dt == 0x08:
                    stack.append(f"!{stack.pop()}")
                elif dt in opcodes.OPERATORS and dt not in (0x00, 0x01, 0x11):
                    b, a = stack.pop(), stack.pop()
                    stack.append(f"({a} {name} {b})")
                elif name.endswith("()"):
                    args = stack.pop() if stack else ""
                    stack.append(f"{name[:-2]}({args})")
            elif t == 3:
                break
        return " ; ".join(stack) if stack else ""
    except IndexError:
        return ""


# ---------------------------------------------------------------------------------
# zone level


def find_menu_lists(zone: bytes):
    """Return parsed MenuList nodes (and standalone menuDef_t assets) in zone order.

    Lists are usually back to back; one added by the rebuilder sits at the end of the zone,
    so after a list that isn't followed by the next one, search on from there."""
    xfile, _, assets, data_start = read_header(zone)
    types = [t for t, _ in assets if t in (MENULIST_TYPE, MENU_TYPE)]
    if not types:
        return []
    pat = re.compile(rb"\xff\xff\xff\xff[\x00-\x0f][\x00-\xff]{3}\xff\xff\xff\xff[\x20-\x7e]{2,}\x00")

    def parse(t, pos, searching=False):
        p = MenuParser(zone)
        if t == MENULIST_TYPE:
            node = p.parse_menulist(pos)
            name = node["name"]
            if isinstance(name, ZStr) and not (name and all(32 <= c < 127 for c in name)):
                raise ZoneParseError("bad menu list name")
            for ptr in (node.ptrs["name"], node.ptrs["menus"]):
                if ptr and not is_inline(ptr) and ptr >> 29 != 4:  # refs are into the virtual block
                    raise ZoneParseError("bad menu list pointer")
            if searching and not node["menuCount"]:
                raise ZoneParseError("empty menu list")
        else:
            p.r.pos = pos
            node = p.one("menuDef_t", {})
            node.end = p.r.pos
        return node

    def first(t, start):
        for m in pat.finditer(zone, start):
            try:
                return parse(t, m.start(), searching=True)
            except (ZoneParseError, KeyError, IndexError, struct.error, RecursionError):
                continue
        raise ZoneParseError("could not locate the menu lists in this zone")

    out = []
    for t in types:
        node = None
        if out:
            try:
                node = parse(t, out[-1].end)
            except (ZoneParseError, KeyError, IndexError, struct.error, RecursionError):
                node = None
        if node is None:
            node = first(t, out[-1].end if out else data_start)
        out.append(node)
    return out


def menu_entries(lists):
    """Yield (list name, index in list, menuDef node) for every inline menu."""
    for ml in lists:
        if ml.type_name == "menuDef_t":
            yield "(standalone)", 0, ml
            continue
        lname = ml["name"].decode("latin1") if isinstance(ml["name"], ZStr) else "(shared name)"
        for i, m in enumerate(ml["menus"] or []):
            if isinstance(m, Node):
                yield lname, i, m


def menu_name(m: Node) -> str:
    n = m["window"]["name"]
    return n.decode("latin1") if isinstance(n, ZStr) else "unnamed"


def write_menu(zone: bytes, m: Node, list_name: str, index: int) -> Writer:
    w = Writer()
    w.zone = zone
    w.line(0, ["//", f"menu {index} of {list_name}"])
    w.line(0, ["//", "Edit values in place; see README for what can change."])
    w.node(0, "menuDef", m, [f"{list_name}#{index}"])
    return w


def parse_text(text: str):
    """Edited file -> list of token lists (comments and blank lines dropped)."""
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("//"):
            continue
        out.append(tokenize_line(s))
    return out


def apply_edits(zone: bytearray, original: Writer, edited_text: str, filename: str = ""):
    """Patch zone with every value that differs between original and edited text.
    Returns the number of values changed."""
    orig = [(toks, slots) for _, toks, slots in original.lines if toks[0] != "//"]
    new = parse_text(edited_text)
    if len(new) != len(orig):
        first = next((i for i, ((otoks, _), ntoks) in enumerate(zip(orig, new)) if ntoks[0] != otoks[0]),
                     min(len(new), len(orig)))
        what = (f"'{' '.join(new[first])[:60]}'" if first < len(new) else "the end of the file")
        raise MenuEditError(
            f"{filename}: value line {first + 1} ({what}) is where the file stops matching the menu: "
            f"it has {len(new)} lines of values but the menu has {len(orig)}. "
            "Adding or removing lines is not supported yet; only change values.")
    changed = 0
    for lineno, ((otoks, slots), ntoks) in enumerate(zip(orig, new), 1):
        if ntoks == otoks:
            continue
        if len(ntoks) != len(otoks) or ntoks[0] != otoks[0]:
            raise MenuEditError(
                f"{filename}: value line {lineno} changed shape: expected '{' '.join(otoks)[:80]}', "
                f"got '{' '.join(ntoks)[:80]}'")
        for ot, nt, slot in zip(otoks, ntoks, slots):
            if ot == nt:
                continue
            if slot is None:
                raise MenuEditError(f"{filename}: value line {lineno} ('{otoks[0]}'): '{ot}' is read-only")
            data = slot.encode(nt)
            if bytes(zone[slot.offset : slot.offset + len(data)]) != data:
                zone[slot.offset : slot.offset + len(data)] = data
                changed += 1
    return changed
