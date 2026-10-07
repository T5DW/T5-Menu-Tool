"""MW2 July 2009 menus: decompile ui_mp.ffm / ui.ffm to .menu text and patch edits back.

Works the same way as the Bo1 side (menufile.py): the text is a dump of the zone structs,
and compiling regenerates the dump from the stock zone and writes every value that changed
at the zone offset it came from. So the layout never moves and:

* numbers, colours, rects, flags, alignments and expression constants change freely;
* operators in expressions can be swapped for others (`==` to `!=`, `dvarbool` to `dvarint`);
* strings keep their length. Script actions (`script "..."`) and item `text` may get
  shorter (padded with spaces);
* lines can't be added or removed, and "ref:0x..." values (data shared with earlier
  menus) are read-only.

The result is the whole zone in an unsigned IWffu100 fastfile. The game opens D:\\<zone>.ff
before D:\\<zone>.ffm, so ui_mp.ff next to default_mp.xex replaces the stock ui_mp.ffm
without touching it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import sys
from pathlib import Path

from . import __version__
from .menufile import MenuEditError, Slot, Writer, apply_edits, fmt_float, quote
from .mw2 import _payload_offset, is_mw2_fastfile, load_zone, MAGIC_UNSIGNED
from .mw2menu_parse import STRUCTS, MenuParseError, Node, Ref, Str, find_menu_lists

META_DIR = ".mw2menu_meta"
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

# operationEnum from default_mp.pdb, written the way .menu source writes them
_OP_SYMBOLS = {0: "noop", 1: ")", 2: "*", 3: "/", 4: "%", 5: "+", 6: "-", 7: "!", 8: "<", 9: "<=", 10: ">",
               11: ">=", 12: "==", 13: "!=", 14: "&&", 15: "||", 16: "(", 17: ",", 18: "&", 19: "|", 20: "~",
               21: "<<", 22: ">>"}
_OP_NAMES = (  # OP_STATICDVARINT .. OP_GET_WAIT_POPUP_STATUS, lowercased without OP_ and _
    "staticdvarint staticdvarbool staticdvarfloat staticdvarstring toint tostring tofloat sin cos min "
    "max milliseconds dvarint dvarbool dvarfloat dvarstring stat uiactive flashbanged usingvehicle "
    "missilecam scoped scopedthermal scoreboardvisible inkillcam inkillcamnpc playerfield getplayerperk "
    "selectinglocation selectingdirection teamfield otherteamfield marinesfield opforfield menuisopen "
    "writingdata inlobby inprivateparty privatepartyhost privatepartyhostinlobby aloneinparty adsjavelin "
    "weaplockblink weapattacktop weapattackdirect weaplocking weaplocked weaplocktooclose "
    "weaplockscreenposx weaplockscreenposy secondsastime tablelookup tablelookupbyrow tablegetrownum "
    "localizestring localvarint localvarbool localvarfloat localvarstring timeleft secondsascountdown "
    "gamemsgwndactive gametypename gametype gametypedescription score friendsonline following "
    "spectatingfree statrangebitsset keybinding actionslotusable hudfade maxplayers acceptinginvite "
    "isintermission gamehost partyhasmissingmappack partymissingmappackerror anynewmappacks amiselected "
    "partystatusstring attachedcontrollercount issplitscreenonlinepossible splitscreenplayercount "
    "getplayerdata getplayerdatasplitscreen experienceforlevel levelforexperience isitemunlocked "
    "isitemunlockedsplitscreen debugprint getplayerdataanybooltrue weaponclassnew weaponname isreloading "
    "savegameavailable unlockeditemcount unlockeditemcountsplitscreen unlockeditem "
    "unlockeditemsplitscreen mailsubject mailfrom mailreceived mailbody maillootlocalized mailgivesloot "
    "anynewmail mailtimetofollowup mailloottype mailranlottery lotterylootlocalized radarisjammed "
    "radarjamintensity radarisenabled empjammed playerads weaponheatactive weaponheatvalue "
    "weaponheatoverheated splashtext splashdescription splashmaterial splashhasicon splashrownum "
    "getfocusedname getfocusedx getfocusedy getfocusedw getfocusedh getitemdefx getitemdefy getitemdefw "
    "getitemdefh playlistfield scoreboardexternalmutenotice clientmatchdata clientmatchdatadef "
    "getmapname getmapimage getmapcustom getmigrationstatus getplayercardinfo isofflineprofileselected "
    "coopplayerfield iscoop getpartystatus getsearchparams gettimeplayed isselectedplayerfriend "
    "getcharbyindex getplayerprofiledata isprofilesignedin getwaitpopupstatus"
).split()
OPS = dict(_OP_SYMBOLS)
OPS.update({23 + i: n + "(" for i, n in enumerate(_OP_NAMES)})  # function calls, closed by ")"
assert len(OPS) == 177
OP_INDEX = {n: i for i, n in OPS.items()}
OP_INDEX.update({n[:-1]: i for i, n in OPS.items() if n.endswith("(") and len(n) > 1})

EVENT_NAMES = {0: "script", 1: "if", 2: "else", 3: "setLocalVarBool", 4: "setLocalVarInt",
               5: "setLocalVarFloat", 6: "setLocalVarString"}

# runtime-only fields, kept as they are and not shown
HIDDEN = {
    ("windowDef_t", "nextTime"), ("menuDef_t", "cursorItem"), ("menuDef_t", "expressionData"),
    ("itemDef_s", "cursorPos"), ("itemDef_s", "parent"), ("itemDef_s", "fxBirthTime"),
    ("itemDef_s", "fxLetterTime"), ("itemDef_s", "fxDecayStartTime"), ("itemDef_s", "fxDecayDuration"),
    ("itemDef_s", "lastSoundPlayedTime"), ("listBoxDef_s", "startPos"), ("listBoxDef_s", "endPos"),
    ("editFieldDef_s", "paintOffset"), ("newsTickerDef_s", "lastTime"), ("newsTickerDef_s", "start"),
    ("newsTickerDef_s", "end"), ("textScrollDef_s", "startTime"),
}
# fields that size or select other data
READONLY = {("menuDef_t", "itemCount"), ("itemDef_s", "type"), ("listBoxDef_s", "numColumns"),
            ("multiDef_s", "count")}
PAD_OK = {"script", "text"}  # strings that may get shorter (padded with spaces)


class OpSlot(Slot):
    def encode(self, token):
        if token not in OP_INDEX:
            raise MenuEditError(f"{self.label}: unknown operator {token!r}")
        return struct.pack(">i", OP_INDEX[token])


# ---------------------------------------------------------------------------------------
# writer

def _ref(v) -> str:
    return "ref:0x%08X" % int(v)


class MW2Writer(Writer):
    def string(self, indent, key, value, label, pad=False):
        tok, slot = self.string_token(value, label, pad)
        self.line(indent, [key, tok], [None, slot])

    def string_token(self, value, label, pad=False):
        if value is None:
            return "null", None
        if isinstance(value, Ref):
            return _ref(value), None
        return quote(value.encode("latin1")), Slot("string", value.pos, value.n, pad, label)

    def scalars(self, indent, node: Node, path):
        """Every plain number field of node, one line each (small structs on one line)."""
        for fname, off, kind, count in STRUCTS[node.kind]:
            if (node.kind, fname) in HIDDEN or kind == "ptr":
                continue
            label = "/".join(path + [fname])
            if kind in ("f32", "i32", "u8"):
                vals = node[fname] if count > 1 else [node[fname]]
                size = 1 if kind == "u8" else 4
                ro = (node.kind, fname) in READONLY
                toks, slots = [fname], [None]
                for i, v in enumerate(vals):
                    toks.append(fmt_float(v) if kind == "f32" else str(v))
                    slots.append(None if ro else Slot(kind, node.off[fname] + size * i, label=label))
                self.line(indent, toks, slots)
            elif kind in ("rectDef_s", "menuTransition"):
                subs = node[fname] if count > 1 else [node[fname]]
                for sub in subs:
                    self.flat(indent, fname, sub, path)
            elif kind == "columnInfo_s":
                for sub in node[fname][:max(0, min(16, node["numColumns"]))]:
                    self.flat(indent, "column", sub, path)

    def flat(self, indent, key, node: Node, path):
        toks, slots = [key], [None]
        for fname, off, kind, count in STRUCTS[node.kind]:
            v = node[fname]
            toks.append(fmt_float(v) if kind == "f32" else str(v))
            slots.append(Slot(kind, node.off[fname], label="/".join(path + [key, fname])))
        self.line(indent, toks, slots)

    def window(self, indent, w: Node, path):
        self.string(indent, "name", w["name"], "/".join(path + ["name"]))
        self.string(indent, "group", w["group"], "/".join(path + ["group"]))
        self.material(indent, "background", w["background"])
        self.scalars(indent, w, path)

    def material(self, indent, key, value):
        if value is None:
            self.line(indent, [key, "null"])
        elif isinstance(value, Ref):
            self.line(indent, [key, _ref(value)])
        else:
            name = value[1]
            self.line(indent, [key, "material", "null" if name is None else
                               _ref(name) if isinstance(name, Ref) else quote(name.encode("latin1"))])

    # -- expressions -----------------------------------------------------------------
    def exp_tokens(self, st, label, later):
        """Tokens for one statement; function operands are listed as later lines."""
        if st is None:
            return ["null"], [None]
        if isinstance(st, Ref):
            return [_ref(st)], [None]
        if not st["entries"]:
            return ["empty"], [None]
        toks, slots = [], []
        for e in st["entries"]:
            if e["type"] == 0:
                toks.append(OPS.get(e["a"], f"op{e['a']}"))
                slots.append(OpSlot("op", e["pos"] + 4, label=label))
            elif e["a"] == 0:
                toks.append(str(struct.unpack(">i", struct.pack(">I", e["b"]))[0]))
                slots.append(Slot("i32", e["pos"] + 8, label=label))
            elif e["a"] == 1:
                toks.append(fmt_float(struct.unpack(">f", struct.pack(">I", e["b"]))[0]))
                slots.append(Slot("f32", e["pos"] + 8, label=label))
            elif e["a"] == 2:
                tok, slot = self.string_token(e["string"], label)
                toks.append(tok)
                slots.append(slot)
            else:
                toks.append(f"function#{len(later)}")
                slots.append(None)
                later.append(e.get("function"))
        return toks, slots

    def expression(self, indent, key, st, label, tail=None):
        later = []
        toks, slots = self.exp_tokens(st, label, later)
        tail = tail or []
        self.line(indent, [key] + toks + tail, [None] + slots + [None] * len(tail))
        for i, f in enumerate(later):
            self.expression(indent + 1, f"function#{i}", f, f"{label}/function#{i}")

    # -- event handlers --------------------------------------------------------------
    def handler_set(self, indent, key, hs, path):
        if hs is None:
            return
        self.line(indent, [key, "{"])
        self.handlers(indent + 1, hs, path + [key])
        self.line(indent, ["}"])

    def handlers(self, indent, hs, path):
        for i, ev in enumerate(hs["handlers"]):
            label = "/".join(path + [str(i)])
            if ev is None:
                self.line(indent, ["nothing"])
                continue
            t = ev["type"]
            name = EVENT_NAMES.get(t, f"event{t}")
            if t == 0:
                self.string(indent, name, ev.get("script"), label, pad=True)
            elif t in (1, 2):
                if t == 1:
                    if "expression" not in ev:
                        self.line(indent, ["if", "null"])
                        continue
                    self.expression(indent, "if", ev["expression"], label, ["{"])
                else:
                    self.line(indent, ["else", "{"])
                if ev.get("set") is not None:
                    self.handlers(indent + 1, ev["set"], path + [str(i)])
                self.line(indent, ["}"])
            else:
                if "var" not in ev:
                    self.line(indent, [name, "null"])
                    continue
                tok, slot = self.string_token(ev["var"], label)
                later = []
                etoks, eslots = self.exp_tokens(ev["expression"], label, later)
                self.line(indent, [name, tok] + etoks, [None, slot] + eslots)
                for k, f in enumerate(later):
                    self.expression(indent + 1, f"function#{k}", f, f"{label}/function#{k}")

    def key_handlers(self, indent, keys, path):
        for k in keys:
            self.line(indent, ["execKey", str(k["key"]), "{"], [None, Slot("i32", k["pos"], label="/".join(path + ["execKey"])), None])
            if k["action"] is not None:
                self.handlers(indent + 1, k["action"], path + ["execKey"])
            self.line(indent, ["}"])

    # -- menus and items -------------------------------------------------------------
    def menu(self, m: Node, path):
        self.line(0, ["menuDef", "{"])
        self.window(1, m["window"], path)
        self.string(1, "font", m["font"], "/".join(path + ["font"]))
        self.scalars(1, m, path)
        for f in ("onOpen", "onCloseRequest", "onClose", "onESC"):
            self.handler_set(1, f, m[f], path)
        self.key_handlers(1, m["onKey"], path)
        self.expression(1, "visibleExp", m["visibleExp"], "/".join(path + ["visibleExp"]))
        self.string(1, "allowedBinding", m["allowedBinding"], "/".join(path + ["allowedBinding"]))
        self.string(1, "soundName", m["soundName"], "/".join(path + ["soundName"]))
        for f in ("rectXExp", "rectYExp", "openSoundExp", "closeSoundExp"):
            if m[f] is not None:
                self.expression(1, f, m[f], "/".join(path + [f]))
        for i, it in enumerate(m["items"]):
            if it is None:
                self.line(1, ["itemDef", "null"])
            else:
                self.item(1, it, path + [f"item{i}"])
        self.line(0, ["}"])

    def item(self, indent, it: Node, path):
        self.line(indent, ["itemDef", "{"])
        ind = indent + 1
        self.window(ind, it["window"], path)
        self.string(ind, "text", it["text"], "/".join(path + ["text"]), pad=True)
        self.scalars(ind, it, path)
        for f in ("mouseEnterText", "mouseExitText", "mouseEnter", "mouseExit", "action", "accept",
                  "onFocus", "leaveFocus"):
            self.handler_set(ind, f, it[f], path)
        for f in ("dvar", "dvarTest", "enableDvar"):
            self.string(ind, f, it[f], "/".join(path + [f]))
        self.key_handlers(ind, it["onKey"], path)
        if it["focusSound"] is not None:
            self.line(ind, ["focusSound", _ref(it["focusSound"])])
        td = it["typeData"]
        if td is not None:
            self.type_data(ind, it["type"], td, path + ["typeData"])
        for fname, off, kind, count in STRUCTS["itemDef_s"]:
            if fname.endswith("Exp") and it[fname] is not None:
                self.expression(ind, fname, it[fname], "/".join(path + [fname]))
        self.line(indent, ["}"])

    def type_data(self, indent, typ, td, path):
        if isinstance(td, Node) and td.kind == "listBoxDef_s":
            self.line(indent, ["listBox", "{"])
            self.scalars(indent + 1, td, path)
            self.handler_set(indent + 1, "onDoubleClick", td["onDoubleClick"], path)
            self.material(indent + 1, "selectIcon", td["selectIcon"])
            self.line(indent, ["}"])
        elif isinstance(td, Node) and td.kind == "multiDef_s":
            self.line(indent, ["multi", "{"])
            self.scalars(indent + 1, td, path)
            for i in range(max(0, min(32, td["count"]))):
                label = "/".join(path + [f"entry{i}"])
                name_tok, name_slot = self.string_token(td["dvarList"][i], label)
                if td["strDef"]:
                    v_tok, v_slot = self.string_token(td["dvarStr"][i], label)
                else:
                    v_tok = fmt_float(td["dvarValue"][i])
                    v_slot = Slot("f32", td.off["dvarValue"] + 4 * i, label=label)
                self.line(indent + 1, ["entry", name_tok, v_tok], [None, name_slot, v_slot])
            self.line(indent, ["}"])
        elif isinstance(td, Node):
            key = {"editFieldDef_s": "editField", "newsTickerDef_s": "newsTicker",
                   "textScrollDef_s": "textScroll"}[td.kind]
            self.line(indent, [key, "{"])
            self.scalars(indent + 1, td, path)
            self.line(indent, ["}"])
        elif isinstance(td, dict) and "enumDvarName" in td:
            self.string(indent, "enumDvarName", td["enumDvarName"], "/".join(path + ["enumDvarName"]))


def menu_name(m) -> str:
    n = m["window"]["name"]
    return str(n) if isinstance(n, Str) else "unnamed"


def write_menu(m, list_name: str, index: int) -> MW2Writer:
    w = MW2Writer()
    w.line(0, ["//", f"menu {index} of {list_name} (MW2 July 2009)"])
    w.line(0, ["//", "Change values in place: numbers, colours, operators, and strings of the same length."])
    w.menu(m, [f"{list_name}#{index}"])
    return w


def menu_entries(zone: bytes):
    """(list name, index, menu) for every inline menu in the zone."""
    lists = find_menu_lists(zone)
    for ml in lists:
        lname = str(ml["name"]) if isinstance(ml["name"], Str) else "(shared name)"
        for i, m in enumerate(ml["menus"]):
            if isinstance(m, Node):
                yield lname, i, m


# ---------------------------------------------------------------------------------------
# fastfile

def build_like(original: bytes, zone: bytes) -> bytes:
    """zone in an unsigned IWffu100 fastfile with the original's header fields."""
    import zlib
    off = _payload_offset(original)
    head = bytearray(MAGIC_UNSIGNED + original[8:off])
    stream = zlib.compress(zone, 9)
    total = len(head) + len(stream)
    struct.pack_into(">II", head, off - 8, total, total)
    return bytes(head) + stream


# ---------------------------------------------------------------------------------------
# folder workflow

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "unnamed"


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def is_mw2_menu_workspace(path: Path) -> bool:
    try:
        find_workspace(path)
        return True
    except MenuEditError:
        return False


def workspace_for(ff_path: Path, out_dir: Path = None) -> Path:
    return (out_dir or ff_path.parent) / f"{ff_path.stem.replace(' ', '_')}_menus"


def decompile(ff_path: Path, out_dir: Path = None, log=print) -> Path:
    ff_path = Path(ff_path)
    raw = ff_path.read_bytes()
    zone = load_zone(raw)
    try:
        entries = list(menu_entries(zone))
    except MenuParseError as e:
        raise MenuEditError(f"{ff_path.name}: {e}") from None
    if not entries:
        raise MenuEditError(f"{ff_path.name} has no menus")
    ws = workspace_for(ff_path, out_dir)
    meta = ws / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ff_path, meta / "original.ff")
    files = {}
    folders = {}  # list name -> folder name, unique even when two lists share a file name
    for list_name, index, m in entries:
        if list_name not in folders:
            base = name = _safe(Path(list_name).stem)
            k = 2
            while name in folders.values():
                name, k = f"{base}_{k}", k + 1
            folders[list_name] = name
        folder = ws / folders[list_name]
        folder.mkdir(parents=True, exist_ok=True)
        rel = f"{folder.name}/{index:03d}_{_safe(menu_name(m))}.menu"
        text = write_menu(m, list_name, index).text()
        (ws / rel).write_text(text, encoding="utf-8", newline="\n")
        files[rel] = {"list": list_name, "index": index, "sha1": _sha1(text.encode("utf-8"))}
    (meta / "meta.json").write_text(json.dumps({
        "tool": "t5menu-mw2", "version": __version__, "source": ff_path.name,
        "ff_sha1": _sha1(raw), "files": files}, indent=1), encoding="utf-8")
    log(f"{ff_path.name}: wrote {len(files)} menus to {ws}")
    return ws


def find_workspace(path: Path) -> Path:
    path = Path(path).resolve()
    for p in [path, *path.parents]:
        if (p / META_DIR / "meta.json").exists():
            return p
    raise MenuEditError(f"{path} is not inside an MW2 *_menus folder (no {META_DIR}/meta.json found)")


def output_name(source: str) -> str:
    """The file the game loads for this fastfile: ui_mp.ffm -> ui_mp.ff, patch_mp.ff -> patch_mp.ff,
    and any other <name>.ff -> <name>.ff (edit any fastfile, get the same name back)."""
    from .gamedirs import loadable_name
    return loadable_name(source)


def compile_workspace(path: Path, out_path: Path = None, log=print) -> Path:
    ws = find_workspace(path)
    meta = json.loads((ws / META_DIR / "meta.json").read_text(encoding="utf-8"))
    raw = (ws / META_DIR / "original.ff").read_bytes()
    if _sha1(raw) != meta["ff_sha1"]:
        raise MenuEditError("the saved original fastfile does not match meta.json")
    edited = {}
    for rel, info in meta["files"].items():
        p = ws / rel
        if not p.exists():
            log(f"warning: {rel} is missing, keeping the original menu")
            continue
        text = p.read_text(encoding="utf-8")
        if _sha1(text.encode("utf-8")) != info["sha1"]:
            edited[(info["list"], info["index"])] = (rel, text)
    known = {(ws / rel).resolve() for rel in meta["files"]}
    for p in ws.rglob("*.menu"):
        if META_DIR not in p.parts and p.resolve() not in known:
            log(f"warning: {p.relative_to(ws).as_posix()} is a new file; MW2 menus can only be edited "
                f"in place for now, so it was skipped")
    if not edited:
        raise MenuEditError("no menu files were changed")
    stock = load_zone(raw)
    zone = bytearray(stock)
    changed = 0
    for list_name, index, m in menu_entries(stock):
        if (list_name, index) not in edited:
            continue
        rel, text = edited[(list_name, index)]
        n = apply_edits(zone, write_menu(m, list_name, index), text, rel)
        log(f"{rel}: {n} value(s) changed")
        changed += n
    if out_path:
        out = Path(out_path)
    else:
        from .gamedirs import compiled_folder
        out = compiled_folder(ws.parent) / output_name(meta["source"])
    out.write_bytes(build_like(raw, bytes(zone)))
    log(f"wrote {out} ({changed} change(s), unsigned IWffu100)")
    if not out_path:
        log(f"copy {out.name} from the Compiled folder into the game folder (next to the xex) to load it")
    return out
