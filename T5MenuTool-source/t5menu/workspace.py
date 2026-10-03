"""Drop-in workflow: fastfile -> folder of .menu files -> patched fastfile."""

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .fastfile import FastFile
from .menufile import MenuEditError, apply_edits, find_menu_lists, menu_entries, menu_name, write_menu

META_DIR = ".t5menu_meta"
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "unnamed"


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def workspace_for(ff_path: Path, out_dir: Path = None) -> Path:
    return (out_dir or ff_path.parent) / f"{ff_path.stem}_menus"


def decompile(ff_path: Path, out_dir: Path = None, log=print) -> Path:
    ff_path = Path(ff_path)
    raw = ff_path.read_bytes()
    ff = FastFile.parse(raw)
    log(f"{ff_path.name}: zone '{ff.zone_name}', {len(ff.zone):,} bytes, "
        f"{'signed' if ff.signed else 'unsigned'}")
    lists = find_menu_lists(ff.zone)
    if not lists:
        raise MenuEditError(f"{ff_path.name} has no menus")
    ws = workspace_for(ff_path, out_dir)
    meta = ws / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ff_path, meta / "original.ff")
    files = {}
    count = 0
    for list_name, index, m in menu_entries(lists):
        folder = ws / _safe(Path(list_name).stem if list_name.endswith(".txt") else list_name)
        folder.mkdir(parents=True, exist_ok=True)
        rel = f"{folder.name}/{index:03d}_{_safe(menu_name(m))}.menu"
        text = write_menu(ff.zone, m, list_name, index).text()
        (ws / rel).write_text(text, encoding="utf-8", newline="\n")
        files[rel] = {"list": list_name, "index": index, "sha1": _sha1(text.encode("utf-8"))}
        count += 1
    (meta / "meta.json").write_text(json.dumps({
        "tool": "t5menu", "version": __version__, "source": ff_path.name,
        "zone": ff.zone_name, "ff_sha1": _sha1(raw), "files": files,
    }, indent=1), encoding="utf-8")
    log(f"wrote {count} menus to {ws}")
    return ws


def find_workspace(path: Path) -> Path:
    path = Path(path).resolve()
    for p in [path] + list(path.parents):
        if (p / META_DIR / "meta.json").exists():
            return p
    raise MenuEditError(f"{path} is not inside a t5menu folder (no {META_DIR}/meta.json found)")


def compile_workspace(path: Path, out_path: Path = None, signed: bool = False, log=print) -> Path:
    ws = find_workspace(path)
    meta = json.loads((ws / META_DIR / "meta.json").read_text(encoding="utf-8"))
    raw = (ws / META_DIR / "original.ff").read_bytes()
    if _sha1(raw) != meta["ff_sha1"]:
        raise MenuEditError("the saved original fastfile does not match meta.json")
    ff = FastFile.parse(raw)

    edited = {}
    for rel, info in meta["files"].items():
        p = ws / rel
        if not p.exists():
            log(f"warning: {rel} is missing, keeping the original menu")
            continue
        text = p.read_text(encoding="utf-8")
        if _sha1(text.encode("utf-8")) != info["sha1"]:
            edited[(info["list"], info["index"])] = (rel, text)
    if not edited:
        log("no menu files were changed")

    zone = bytearray(ff.zone)
    changed = 0
    if edited:
        lists = find_menu_lists(ff.zone)
        for list_name, index, m in menu_entries(lists):
            key = (list_name, index)
            if key not in edited:
                continue
            rel, text = edited[key]
            original = write_menu(ff.zone, m, list_name, index)
            n = apply_edits(zone, original, text, rel)
            log(f"{rel}: {n} value(s) changed")
            changed += n
    ff.zone = bytes(zone)
    out_path = Path(out_path) if out_path else ws.parent / f"{Path(meta['source']).stem}.compiled.ff"
    out_path.write_bytes(ff.build(signed=signed))
    log(f"wrote {out_path} ({changed} value(s) changed, {'signed' if signed else 'unsigned'} IWff"
        f"{'0' if signed else 'u'}100)")
    return out_path
