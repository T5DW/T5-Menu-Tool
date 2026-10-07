"""Syntax highlighting for the editor: GSC (MW2 and Bo1) and .menu files (both games).

spans(text, kind, game) returns (tag, start, end) character ranges; the editor turns tags into
colours. Kept free of Tk so it can be tested on its own.
"""

from __future__ import annotations

import re

GSC_KEYWORDS = {
    "if", "else", "for", "while", "foreach", "in", "switch", "case", "default", "break", "continue",
    "return", "wait", "waittill", "waittillmatch", "waittillframeend", "endon", "notify", "thread",
    "childthread", "true", "false", "undefined", "self", "level", "game", "anim", "world",
}
BO1_EXTRA = {"waitrealtime", "isdefined", "vector_scale", "prof_begin", "prof_end"}

GSC_BUILTINS = {
    # functions every script uses, in both games
    "isDefined", "isAlive", "isPlayer", "isString", "isArray", "getDvar", "getDvarInt", "getDvarFloat",
    "setDvar", "setDvarIfUninitialized", "iPrintLn", "iPrintLnBold", "println", "print", "assert",
    "assertEx", "spawn", "spawnStruct", "getEnt", "getEntArray", "getTime", "randomInt", "randomFloat",
    "randomIntRange", "randomFloatRange", "int", "float", "distance", "distanceSquared", "length",
    "vectorNormalize", "vectorToAngles", "anglesToForward", "bulletTrace", "playFx", "playSound",
    "playLocalSound", "giveWeapon", "takeWeapon", "takeAllWeapons", "switchToWeapon", "getCurrentWeapon",
    "setPlayerData", "getPlayerData", "setRank", "freezeControls", "setOrigin", "setPlayerAngles",
    "getPlayerAngles", "allowSpectateTeam", "suicide", "delete", "hide", "show", "moveTo", "rotateTo",
    "linkTo", "unlink", "setModel", "precacheModel", "precacheShader", "precacheString", "newHudElem",
    "newClientHudElem", "setText", "setShader", "setValue", "destroy", "fadeOverTime", "moveOverTime",
    "getEntityNumber", "notifyOnPlayerCommand", "setClientDvar", "openMenu", "closeMenu", "kick",
    "exitLevel", "map_restart", "setDvarIfUninitialized", "strTok", "getSubStr", "toLower", "isSubStr",
    "array_thread", "array_remove", "spawnTurret", "maxHealth", "health", "giveMaxAmmo", "setWeaponAmmoClip",
    "setWeaponAmmoStock", "visionSetNaked", "setEMPJammed", "allowAds", "allowJump", "allowSprint",
    "setMoveSpeedScale", "setClientDvars", "getGuid", "getXuid", "ent_flag", "flag", "flag_set",
}

MENU_BLOCKS = {"menuDef", "itemDef", "onOpen", "onClose", "onESC", "onCloseRequest", "action", "onFocus",
               "leaveFocus", "mouseEnter", "mouseExit", "mouseEnterText", "mouseExitText", "accept",
               "onKey", "execKey", "execKeyInt", "listBox", "multi", "editField", "newsTicker",
               "textScroll", "if", "elseif", "else", "functions", "staticDvars", "strings", "{", "}"}
MENU_EVENTS = {"script", "setLocalVarBool", "setLocalVarInt", "setLocalVarFloat", "setLocalVarString",
               "open", "close", "exec", "execnow", "uiScript", "play", "setDvar", "setfocus", "show",
               "hide", "setItemColor", "fadeIn", "fadeOut", "transition", "setcolor", "conditional",
               "key"}
MENU_LITERALS = {"null", "true", "false"}

_GSC_RE = re.compile(r"""
    (?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))
  | (?P<devblock>/\#|\#/)
  | (?P<string>&?"(?:\\.|[^"\\\n])*"?)
  | (?P<directive>^[ \t]*\#[A-Za-z_]+[^\n]*)
  | (?P<number>\b(?:0x[0-9A-Fa-f]+|\d+\.?\d*(?:e[+-]?\d+)?|\.\d+)\b)
  | (?P<path>\b[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z0-9_]+)+(?=::))
  | (?P<word>\b[A-Za-z_][A-Za-z0-9_]*\b)
""", re.S | re.M | re.X)

_MENU_RE = re.compile(r"""
    (?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))
  | (?P<string>"(?:\\.|[^"\\\n])*"?)
  | (?P<ref>\bref:0x[0-9A-Fa-f]+\b)
  | (?P<directive>^[ \t]*\#[A-Za-z_]+[^\n]*)
  | (?P<number>(?<![A-Za-z_])-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?\b)
  | (?P<word>\b[A-Za-z_][A-Za-z0-9_]*\b|[{}])
""", re.S | re.M | re.X)


def gsc_spans(text: str, game: str = "mw2"):
    keywords = GSC_KEYWORDS | (BO1_EXTRA if game == "bo1" else set())
    builtins_lower = {b.lower() for b in GSC_BUILTINS}
    for m in _GSC_RE.finditer(text):
        kind = m.lastgroup
        if kind == "word":
            w = m.group()
            nxt = text[m.end():m.end() + 40].lstrip(" \t")
            line_start = text.rfind("\n", 0, m.start()) + 1
            if w in keywords or (game == "bo1" and w.lower() in keywords):
                yield "keyword", m.start(), m.end()
            elif nxt.startswith("(") and text[line_start:m.start()].strip() == "" and _is_definition(text, m.end()):
                yield "function_def", m.start(), m.end()
            elif w.lower() in builtins_lower and nxt.startswith("("):
                yield "builtin", m.start(), m.end()
            elif nxt.startswith("::") or text[max(0, m.start() - 2):m.start()] == "::" or nxt.startswith("("):
                yield "function", m.start(), m.end()
            continue
        if kind == "devblock":
            kind = "directive"
        yield kind, m.start(), m.end()


def _is_definition(text: str, after: int) -> bool:
    """name( ... ) followed by { on the same or next line: a function definition."""
    depth, i, n = 0, after, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                rest = text[i + 1:i + 200].lstrip(" \t\r\n")
                return rest.startswith("{")
        elif c in ";{}" or (c == "\n" and depth == 0):
            return False
        i += 1
    return False


def menu_spans(text: str, game: str = "mw2"):
    for m in _MENU_RE.finditer(text):
        kind = m.lastgroup
        if kind == "word":
            w = m.group()
            line_start = text.rfind("\n", 0, m.start()) + 1
            first = text[line_start:m.start()].strip() == ""
            if w in MENU_BLOCKS and (first or w in "{}"):
                yield "keyword", m.start(), m.end()
            elif w in MENU_LITERALS:
                yield "literal", m.start(), m.end()
            elif first and w in MENU_EVENTS:
                yield "builtin", m.start(), m.end()
            elif first:
                yield "property", m.start(), m.end()
            continue
        yield kind, m.start(), m.end()


def spans(text: str, kind: str, game: str = "mw2"):
    """kind: "gsc" or "menu"."""
    return list(menu_spans(text, game) if kind == "menu" else gsc_spans(text, game))


def kind_for(path) -> str:
    s = str(path).lower()
    return "menu" if s.endswith((".menu", ".inc")) else "gsc"


def value_line_to_line(text: str, n: int) -> int:
    """The menu compiler counts "value lines" (not blank, not // comments); the file line of one."""
    count = 0
    for i, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        count += 1
        if count == n:
            return i
    return 1
