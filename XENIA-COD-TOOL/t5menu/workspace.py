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
from .parser import Node, ZStr
from .rebuild import RENAMED_LIST, STOCK_LIST, RebuildError, VirtualMap, custom_menus, set_custom_menus

IMAGES_FOLDER = "images"  # images/<menu name>.png = background of that added menu
IMAGES_README = """Pictures for the menus you add (not the stock ones).

Name a picture after the menu it belongs to, e.g. for a menu with
    name "Xenia-Cod-Project-Menu"
save  images/Xenia-Cod-Project-Menu.png  (or .jpg). On compile it becomes that menu's
full-screen background. 16:9 pictures fill the screen. Needs Pillow (pip install pillow).

Pictures already compiled into the fastfile stay when this folder is empty; to remove one,
delete the itemDef named "xcp_auto_background" from that menu's .menu file.
"""
NEW_FOLDER = "new_menus"  # where menus added by the tool live (and where new ones can go)

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
    custom = _custom_list(ff.zone, lists)
    for list_name, index, m in menu_entries(lists):
        folder = ws / _folder(list_name, custom)
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
    images = ws / IMAGES_FOLDER
    images.mkdir(exist_ok=True)
    if not (images / "README.txt").exists():
        (images / "README.txt").write_text(IMAGES_README, encoding="utf-8")
    log(f"wrote {count} menus to {ws}")
    return ws


def _custom_list(zone, lists):
    """Name of the list holding menus a previous compile added, if any (it is the last list,
    sits at the very end of the zone and carries the stock name)."""
    if len(lists) > 1 and lists[-1].type_name == "MenuList" and lists[-1].end == len(zone) \
            and lists[-1].pos != lists[-2].end:
        return menu_list_name(lists[-1])
    return None


def menu_list_name(ml):
    return ml["name"].decode("latin1") if isinstance(ml["name"], ZStr) else "(shared name)"


def _folder(list_name, custom):
    if custom and list_name == custom:
        return NEW_FOLDER
    if custom and list_name == RENAMED_LIST.decode():
        list_name = STOCK_LIST.decode()  # the stock list, renamed by the rebuild
    return _safe(Path(list_name).stem if list_name.endswith(".txt") else list_name)


def _finder(ws: Path, menu_file: Path):
    """Resolve image "file.png" paths: next to the .menu file, then in the folder root,
    then in its images/ folder (or an absolute path)."""
    def find(name):
        for base in (menu_file.parent, ws, ws / "images"):
            p = (base / name)
            if p.is_file():
                return str(p)
        return name if Path(name).is_absolute() and Path(name).is_file() else None
    return find


PICTURE_TYPES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga")


def _picture_for(ws: Path, menu_name: str):
    """images/<menu name>.<png|jpg|...> (name match ignores case), or None."""
    folder = ws / IMAGES_FOLDER
    if not folder.is_dir():
        return None
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in PICTURE_TYPES and p.stem.lower() == menu_name.lower():
            return str(p)
    return None


def find_workspace(path: Path) -> Path:
    path = Path(path).resolve()
    for p in [path] + list(path.parents):
        if (p / META_DIR / "meta.json").exists():
            return p
    raise MenuEditError(f"{path} is not inside a t5menu folder (no {META_DIR}/meta.json found)")


def compile_workspace(path: Path, out_path: Path = None, signed: bool = False, log=print) -> Path:
    """Patch edited stock menus in place, and (re)build the list of added menus from every
    .menu file the original didn't have plus the ones added before."""
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
    known = {(ws / rel).resolve() for rel in meta["files"]}
    new_files = sorted(p for p in ws.rglob("*.menu")
                       if META_DIR not in p.parts and p.resolve() not in known)
    if not edited and not new_files:
        log("no menu files were changed")

    zone = bytearray(ff.zone)
    lists = find_menu_lists(ff.zone)
    custom = _custom_list(ff.zone, lists)
    changed = 0
    custom_edits = {}
    for list_name, index, m in menu_entries(lists):
        key = (list_name, index)
        if key not in edited:
            continue
        rel, text = edited[key]
        if custom and list_name == custom:
            custom_edits[index] = (rel, text)  # added menus are rewritten whole, see below
            continue
        original = write_menu(ff.zone, m, list_name, index)
        n = apply_edits(zone, original, text, rel)
        log(f"{rel}: {n} value(s) changed")
        changed += n

    custom_count = len([m for ml in lists[-1:] if custom for m in (ml["menus"] or []) if isinstance(m, Node)])
    if new_files or custom_edits or custom_count:
        from .menutext import read_menu, set_auto_background

        try:
            vm = VirtualMap(bytes(zone))
            menus = []
            stock_count = max(l["menuCount"] for l in vm.lists)
            for k, m in enumerate(custom_menus(vm)):
                index = stock_count + k
                if index in custom_edits:
                    rel, text = custom_edits[index]
                    m = read_menu(text, rel, vm.material_ref, _finder(ws, ws / rel))
                    log(f"{rel}: rewritten")
                menus.append(m)
            for p in new_files:
                rel = p.relative_to(ws).as_posix()
                menus.append(read_menu(p.read_text(encoding="utf-8"), rel, vm.material_ref, _finder(ws, p)))
                log(f"{rel}: new menu '{menus[-1]['window']['name'].decode('latin1')}' added")
            for m in menus:  # images/<menu name>.png becomes that menu's background
                name = m["window"]["name"]
                name = name.decode("latin1") if isinstance(name, (bytes, bytearray)) else str(name)
                pic = _picture_for(ws, name)
                set_auto_background(m, pic)
                if pic:
                    log(f"images/{Path(pic).name}: background of '{name}'")
            zone = bytearray(set_custom_menus(bytes(zone), menus, vm))
        except RebuildError as e:
            raise MenuEditError(f"can't add menus: {e}") from None
        changed += len(new_files) + len(custom_edits)

    ff.zone = bytes(zone)
    to_compiled = out_path is None
    if to_compiled:
        from .gamedirs import compiled_folder, loadable_name
        out_path = compiled_folder(ws.parent) / loadable_name(meta["source"])
    else:
        out_path = Path(out_path)
    out_path.write_bytes(ff.build(signed=signed))
    log(f"wrote {out_path} ({changed} change(s), {'signed' if signed else 'unsigned'} IWff"
        f"{'0' if signed else 'u'}100)")
    if to_compiled:
        log(f"copy {out_path.name} from the Compiled folder into the game folder to load it")
    return out_path
