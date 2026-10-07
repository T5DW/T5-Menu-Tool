"""Black Ops 1 (T5) Xbox 360 GSC: extract scripts from a fastfile and patch edited ones back.

Bo1 also compiles GSC from source at load time, so scripts sit in rawfile assets as plain
text: {ptr name, i32 len, ptr buffer}, followed inline by the name and len + 1 bytes of
text. Zone data is referenced by offset from later assets, so nothing after a script may
move. An edited script is therefore written back in place, into the stock script's own
space: if it is longer than the original it is minified first (comments, indentation and
blank lines dropped, see gsc.minify), and whatever space is left is filled with spaces.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

from .fastfile import FastFile
from .gsc import GscError, check_all, minify

META_DIR = ".bo1gsc_meta"
from .custom import CUSTOM_DIR, CustomError, bo1_merge, custom_scripts  # noqa: E402
from .testgsc import CALLBACK  # noqa: E402
PTR_INLINE = 0xFFFFFFFF
SCRIPT_EXTS = {".gsc", ".csc", ".gsh", ".cfg", ".txt", ".atr", ".csv", ".vision"}

# {-1, len, -1, name\0, text[len], \0}
_RAW_RE = re.compile(rb"\xff\xff\xff\xff(....)\xff\xff\xff\xff([A-Za-z0-9_/\\.\-]{1,200}\.[A-Za-z0-9]{1,8})\x00", re.S)


def find_rawfiles(zone: bytes) -> dict[str, tuple[int, int]]:
    """{name: (text offset, len)} for every inline rawfile whose text ends in a NUL at len."""
    out = {}
    for m in _RAW_RE.finditer(zone):
        ln = struct.unpack(">I", m.group(1))[0]
        start = m.end()
        if ln > 0x4000000 or start + ln >= len(zone) or zone[start + ln] != 0:
            continue
        if b"\0" in zone[start:start + ln]:
            continue
        out.setdefault(m.group(2).decode("latin1"), (start, ln))
    return out


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def workspace_for(ff_path: Path, out_dir: Path = None) -> Path:
    return (out_dir or ff_path.parent) / f"{ff_path.stem.replace(' ', '_')}_bo1gsc"


def extract(ff_path: Path, out_dir: Path = None, log=print, folder: Path = None) -> Path:
    ff_path = Path(ff_path)
    ff = FastFile.load(ff_path)
    raws = find_rawfiles(ff.zone)
    if not raws:
        raise GscError(f"{ff_path.name} has no scripts")
    ws = Path(folder) if folder else workspace_for(ff_path, out_dir)
    meta = ws / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (start, ln) in raws.items():
        rel = name.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            continue
        text = ff.zone[start:start + ln]
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text)
        manifest[rel] = {"sha1": _sha1(text), "name": name, "len": ln}
    (meta / "manifest.json").write_text(json.dumps({"source": str(ff_path.resolve()), "files": manifest},
                                                   indent=1, sort_keys=True))
    gsc = sum(1 for n in raws if n.endswith(".gsc"))
    log(f"{ff_path.name}: {len(raws)} rawfiles ({gsc} .gsc) -> {ws}")
    return ws


def find_workspace(path: Path) -> Path:
    path = Path(path).resolve()
    for p in [path, *path.parents]:
        if (p / META_DIR).is_dir():
            return p
    raise GscError(f"{path} is not inside an extracted *_bo1gsc folder")


def source_of(ws: Path) -> Path:
    return Path(json.loads((Path(ws) / META_DIR / "manifest.json").read_text())["source"])


def fit(name: str, text: bytes, room: int) -> bytes:
    """text padded to exactly `room` bytes, minified first if it is too long."""
    if len(text) > room:
        text = minify(text)
    if len(text) > room:
        raise GscError(f"{name} is {len(text)} bytes even after removing comments and indentation, "
                       f"but the stock script only has room for {room}. Move some code into another "
                       f"script or make it shorter.")
    return text + b" " * (room - len(text))


def build(path: Path, ff_path: Path = None, out_path: Path = None, log=print) -> Path:
    """Folder -> rebuilt copy of the stock fastfile with the edited scripts patched in."""
    ws = find_workspace(path)
    files = json.loads((ws / META_DIR / "manifest.json").read_text())
    manifest = files["files"]
    ff_path = Path(ff_path) if ff_path else Path(files["source"])
    if not ff_path.exists():
        raise GscError(f"stock fastfile not found: {ff_path} (pick it again)")
    edited = {}
    for p in sorted(ws.rglob("*")):
        if not p.is_file() or META_DIR in p.parts or p.suffix.lower() not in SCRIPT_EXTS:
            continue
        rel = p.relative_to(ws).as_posix()
        data = p.read_bytes()
        info = manifest.get(rel)
        if info is None and rel.startswith(CUSTOM_DIR + "/"):
            continue  # custom scripts are added to _callbacksetup.gsc below
        if info is None:
            raise GscError(f"{rel} is a new file. Bo1 fastfiles can only change scripts that are already "
                           f"in them; put the code into an existing script instead.")
        if info["sha1"] != _sha1(data):
            edited[rel] = data
    if custom_scripts(ws):
        if CALLBACK not in manifest:
            raise GscError(f"custom scripts need {CALLBACK}, which isn't in this fastfile")
        base = edited.get(CALLBACK) or (ws / CALLBACK).read_bytes()
        try:
            edited[CALLBACK] = bo1_merge(ws, base.decode("latin1"), log).encode("latin1")
        except CustomError as e:
            raise GscError(str(e)) from None
    if not edited:
        raise GscError("nothing changed: edit a script in the folder first")
    check_all(edited)
    ff = FastFile.load(ff_path)
    raws = find_rawfiles(ff.zone)
    zone = bytearray(ff.zone)
    for rel, data in edited.items():
        name = manifest[rel]["name"]
        if name not in raws:
            raise GscError(f"{name} is not in {ff_path.name}")
        start, ln = raws[name]
        if b"\0" in data:
            raise GscError(f"{rel} contains a NUL byte")
        new = fit(rel, data, ln)
        zone[start:start + ln] = new
        log(f"  * {rel} ({len(data)} bytes{', minified to fit' if len(data) > ln else ''})")
    ff.zone = bytes(zone)
    out = Path(out_path) if out_path else ws.parent / f"{ff_path.stem}.gsc.ff"
    out.write_bytes(ff.build(signed=False))
    log(f"wrote {out}")
    return out
