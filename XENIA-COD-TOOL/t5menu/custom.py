"""Custom GSC: your own scripts, kept in the scripts folder under custom/ and started for you.

MW2: custom/<name>.gsc is a new script file. It goes into patch_mp.ff like any other script,
and Compile adds `thread custom\\<name>::init();` to CodeCallback_StartGameType in the copy of
_callbacksetup.gsc that goes into patch_mp.ff (the file on disk isn't changed), so init()
runs at the start of every match.

Bo1: Bo1 fastfiles can't take new script files, so Compile appends custom/<name>.gsc to
_callbacksetup.gsc (in the built fastfile only) and calls <name>_init() from
CodeCallback_StartGameType. Give Bo1 custom functions names that start with the script's name,
so they don't clash with the stock ones.
"""

from __future__ import annotations

import re
from pathlib import Path

from .testgsc import CALLBACK, HookError, add_hook

CUSTOM_DIR = "custom"
MARK = "// T5MenuTool custom"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")

MW2_TEMPLATE = """// {name}.gsc: your own MW2 script.
// init() runs at the start of every match (T5MenuTool calls it from _callbacksetup.gsc).
#include common_scripts\\utility;
#include maps\\mp\\_utility;

init()
{{
	level thread onPlayerConnect();
}}

onPlayerConnect()
{{
	for ( ;; )
	{{
		level waittill( "connected", player );
		player thread onPlayerSpawned();
	}}
}}

onPlayerSpawned()
{{
	self endon( "disconnect" );
	for ( ;; )
	{{
		self waittill( "spawned_player" );
		self iPrintLnBold( "^2{name}^7 is running" );
	}}
}}
"""

BO1_TEMPLATE = """// {name}.gsc: your own Bo1 script.
// {name}_init() runs at the start of every match. Bo1 can't load new script files, so
// T5MenuTool adds this file to the end of _callbacksetup.gsc when it compiles: start every
// function name with {name}_ so it can't clash with the stock ones.

{name}_init()
{{
	level thread {name}_onPlayerConnect();
}}

{name}_onPlayerConnect()
{{
	for ( ;; )
	{{
		level waittill( "connected", player );
		player thread {name}_onPlayerSpawned();
	}}
}}

{name}_onPlayerSpawned()
{{
	self endon( "disconnect" );
	for ( ;; )
	{{
		self waittill( "spawned_player" );
		self iPrintLnBold( "^2{name}^7 is running" );
	}}
}}
"""


class CustomError(ValueError):
    pass


def new_script(ws: Path, game: str, name: str) -> Path:
    """Create custom/<name>.gsc from the template. Returns its path."""
    name = name.strip().removesuffix(".gsc")
    if not NAME_RE.match(name):
        raise CustomError("script names can use letters, digits and _ only, and can't start with a digit")
    p = Path(ws) / CUSTOM_DIR / f"{name}.gsc"
    if p.exists():
        raise CustomError(f"{CUSTOM_DIR}/{name}.gsc already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((MW2_TEMPLATE if game == "mw2" else BO1_TEMPLATE).format(name=name), newline="\r\n")
    return p


def custom_scripts(ws: Path) -> list[Path]:
    d = Path(ws) / CUSTOM_DIR
    return sorted(p for p in d.glob("*.gsc") if NAME_RE.match(p.stem)) if d.is_dir() else []


def _has_func(text: str, name: str) -> bool:
    return re.search(r"^\s*" + re.escape(name) + r"\s*\(\s*\)", text, re.M) is not None


def _callback_text(ws: Path, files: dict) -> str:
    if CALLBACK in files:
        return files[CALLBACK].decode("latin1")
    p = Path(ws) / CALLBACK
    if not p.exists():
        raise CustomError(f"{CALLBACK} isn't in the scripts folder; extract the scripts first")
    return p.read_bytes().decode("latin1")


def mw2_hook(ws: Path, files: dict, log=print) -> dict:
    """files plus the hooked _callbacksetup.gsc when there are custom scripts (MW2)."""
    scripts = [p for p in custom_scripts(ws)]
    if not scripts:
        return files
    text = _callback_text(ws, files)
    hooked = 0
    for p in scripts:
        src = p.read_bytes().decode("latin1")
        if not _has_func(src, "init"):
            log(f"  ! {CUSTOM_DIR}/{p.name} has no init(), so nothing starts it")
            continue
        try:
            text = add_hook(text, f"thread {CUSTOM_DIR}\\{p.stem}::init();")
        except HookError as e:
            raise CustomError(str(e)) from None
        hooked += 1
    if hooked:
        files = dict(files)
        files[CALLBACK] = text.encode("latin1")
        for p in scripts:
            files.setdefault(f"{CUSTOM_DIR}/{p.name}", p.read_bytes())
    return files


def bo1_merge(ws: Path, callback_text: str, log=print) -> str:
    """_callbacksetup.gsc with every custom script appended and started (Bo1)."""
    text = callback_text
    nl = "\r\n" if "\r\n" in text else "\n"
    for p in custom_scripts(ws):
        src = p.read_bytes().decode("latin1").replace("\r\n", "\n")
        init = f"{p.stem}_init"
        if not _has_func(src, init):
            raise CustomError(f"{CUSTOM_DIR}/{p.name}: Bo1 custom scripts start from {init}()")
        includes = [ln.strip() for ln in src.split("\n") if ln.strip().startswith("#include")]
        body = "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#include"))
        for inc in includes:
            if inc not in text:
                text = inc + nl + text
        text = text.rstrip() + nl + nl + f"// ---- {CUSTOM_DIR}/{p.name} {MARK}" + nl + body.replace("\n", nl)
        try:
            text = add_hook(text, f"thread {init}();")
        except HookError as e:
            raise CustomError(str(e)) from None
        log(f"  + {CUSTOM_DIR}/{p.name} (added to {CALLBACK})")
    return text
