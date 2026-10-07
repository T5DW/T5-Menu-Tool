"""MW2 (IW4) Xbox 360 fastfiles from the July 13, 2009 dev build (fastfile version 0xFD).

GSC in this build is plain-text source stored in rawfile assets and compiled by the game
when a script loads, so editing a script just means shipping a new rawfile. The game loads
`patch_mp` at startup (before common_mp) as IW's own patch zone, so the tool writes edited
and new scripts into an unsigned patch_mp.ff and leaves the stock fastfiles untouched.

Container layout (reversed from default_mp_dev.xex, DB_LoadXFile @ 0x821A99E0):

    0x00  "IWffu100" (unsigned; the stock xex needs xexpatch.py for these) or "IWff0100" (signed)
    0x08  u32 BE version, 0xFD
    0x0C  u8  unknown (1)
    0x0D  u64 BE file time
    0x15  u32 BE language mask (1 in every stock file seen)
    0x19  u32 BE streamed image count, then that many 12-byte entries
    ....  u32 BE file size, u32 BE file size
    then, unsigned: one zlib stream with the zone
          signed:   0x2000-byte chunks [IWffs100 auth header][master hash block][256 data
                    blocks][master hash block][256 data blocks]...; the data blocks joined
                    together are one zlib stream with the zone

Zone: u32 size (bytes after this 0x20 header), u32 external size, u32 block sizes[6],
then XAssetList {u32 stringCount, ptr strings, u32 assetCount, ptr assets}, the asset
array ({u32 type, ptr} each) and the assets in order. RawFile is
{ptr name, u32 compressedLen, u32 len, ptr buffer}; compressedLen 0 means the buffer holds
len + 1 bytes of plain text, otherwise compressedLen bytes of zlib data.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import time
import zlib
from pathlib import Path

MAGIC_SIGNED = b"IWff0100"
MAGIC_UNSIGNED = b"IWffu100"
AUTH_MAGIC = b"IWffs100"
VERSION = 0xFD
AUTH_CHUNK = 0x2000
AUTH_GROUP = 256
ZONE_HEADER = 0x20
BLOCK_TEMP = 0
BLOCK_VIRTUAL = 3
ASSET_RAWFILE = 0x22
PTR_INLINE = 0xFFFFFFFF

PATCH_ZONE = "patch_mp"
META_DIR = ".mw2gsc_meta"
SCRIPT_EXTS = {".gsc", ".csc", ".gsh", ".cfg", ".txt", ".atr", ".rmb", ".shock", ".graph", ".info", ".csv"}


class MW2Error(ValueError):
    pass


def is_mw2_fastfile(data: bytes) -> bool:
    return data[:8] in (MAGIC_SIGNED, MAGIC_UNSIGNED) and data[8:12] == struct.pack(">I", VERSION)


def _payload_offset(data: bytes) -> int:
    """Offset just past the plain header (where the zlib stream or IWffs100 starts)."""
    count = struct.unpack(">I", data[0x19:0x1D])[0]
    if count > 0x3800:
        raise MW2Error(f"bad streamed image count {count}")
    return 0x1D + count * 12 + 8


def load_zone(data: bytes) -> bytes:
    """Decompress an MW2 0xFD fastfile (signed or unsigned) to its raw zone."""
    if not is_mw2_fastfile(data):
        raise MW2Error("not an MW2 July 2009 fastfile (expected IWffu100/IWff0100, version 0xFD)")
    off = _payload_offset(data)
    if data[:8] == MAGIC_SIGNED:
        if data[off:off + 8] != AUTH_MAGIC:
            raise MW2Error(f"signed fastfile without {AUTH_MAGIC.decode()} header at 0x{off:X}")
        parts = []
        pos = off + AUTH_CHUNK  # skip the auth header
        while pos < len(data):
            pos += AUTH_CHUNK  # master hash block
            for _ in range(AUTH_GROUP):
                if pos >= len(data):
                    break
                parts.append(data[pos:pos + AUTH_CHUNK])
                pos += AUTH_CHUNK
        stream = b"".join(parts)
    else:
        stream = data[off:]
    try:
        d = zlib.decompressobj()
        zone = d.decompress(stream)
    except zlib.error as e:
        raise MW2Error(f"zone failed to inflate: {e}") from None
    if len(zone) < ZONE_HEADER + 16:
        raise MW2Error("zone is empty")
    size = struct.unpack(">I", zone[:4])[0]
    if size + ZONE_HEADER != len(zone):
        raise MW2Error(f"zone size mismatch: header says {size + ZONE_HEADER}, got {len(zone)}")
    return zone


_RAW_RE = re.compile(rb"\xff\xff\xff\xff(....)(....)\xff\xff\xff\xff([A-Za-z0-9_/\\.\-]{1,200})\x00", re.S)


def find_rawfiles(zone: bytes) -> dict[str, bytes]:
    """Every rawfile in the zone whose data decodes cleanly, as {name: plain bytes}.

    Rawfiles are written inline as {-1, compressedLen, len, -1, name, data}. A full zone
    walk needs every asset loader, so this scans for that shape and keeps only entries
    whose data inflates (or fits) to exactly len bytes, which rules out false matches.
    """
    out: dict[str, bytes] = {}
    for m in _RAW_RE.finditer(zone):
        clen, ln = struct.unpack(">II", m.group(1) + m.group(2))
        if ln > 0x4000000 or clen > 0x4000000:
            continue
        name = m.group(3).decode("latin1")
        start = m.end()
        if clen:
            try:
                d = zlib.decompressobj()
                text = d.decompress(zone[start:start + clen])
            except zlib.error:
                continue
            if len(text) != ln or not d.eof:
                continue
        else:
            text = zone[start:start + ln]
            if zone[start + ln:start + ln + 1] != b"\0":
                continue
        if "." not in name.rsplit("/", 1)[-1]:
            continue  # zone-name marker entries (rawfile named after the zone, empty)
        out.setdefault(name, text)
    return out


def build_rawfile_zone(files: dict[str, bytes], zone_name: str = PATCH_ZONE, compress: bool = True,
                       menus=None) -> bytes:
    """A zone holding rawfile assets, laid out the way IW's own ez_common_mp is.

    menus: optional (resolver, [(menu, plan)]) from mw2menu_emit; those menus go first, in a
    MenuList asset named ui_mp/patch_mp_menus.txt."""
    entries = list(files.items())
    entries.append((zone_name, None))  # IW zones end with an empty rawfile named after the zone
    n = len(entries) + (1 if menus else 0)
    body = bytearray()
    virtual = 8 * n  # asset array
    temp = 0x20  # what IW's linker wrote for rawfile-only zones
    asset_types = []
    if menus:
        from . import mw2menu_emit as emit
        resolver, plans = menus
        asset, temp_bytes = emit.menu_list(resolver, emit.PATCH_LIST, plans, mem=virtual)
        virtual += emit.virtual_size(asset, mem=virtual) + 0x100  # a little room for alignment
        temp = max(0x800, (temp_bytes + 0x400 + 0xFF) & ~0xFF)  # list and menu headers, material references
        body += asset
        asset_types.append(emit.ASSET_MENUFILE)
    for name, text in entries:
        nm = name.encode("latin1") + b"\0"
        if text is None:
            clen, ln, buf = 0, 0, b"\0"
        elif compress and text and len(zlib.compress(text, 9)) < len(text):
            buf = zlib.compress(text, 9)  # IW stores a rawfile plain when zlib doesn't shrink it
            clen, ln = len(buf), len(text)
        else:
            clen, ln, buf = 0, len(text), text + b"\0"
        body += struct.pack(">IIII", PTR_INLINE, clen, ln, PTR_INLINE) + nm + buf
        virtual += len(nm) + len(buf)
    asset_types += [ASSET_RAWFILE] * len(entries)
    assets = b"".join(struct.pack(">II", t, PTR_INLINE) for t in asset_types)
    xlist = struct.pack(">IIII", 0, 0, n, PTR_INLINE)
    blocks = [0] * 6
    blocks[BLOCK_TEMP] = temp
    blocks[BLOCK_VIRTUAL] = virtual
    data = xlist + assets + bytes(body)
    return struct.pack(">II6I", len(data), 0, *blocks) + data


def build_fastfile(zone: bytes) -> bytes:
    """Wrap a zone in an unsigned IWffu100 0xFD fastfile."""
    stream = zlib.compress(zone, 9)
    filetime = int((time.time() + 11644473600) * 10_000_000)
    head_len = 0x1D + 8
    total = head_len + len(stream)
    head = (MAGIC_UNSIGNED + struct.pack(">I", VERSION) + b"\x01" + struct.pack(">Q", filetime)
            + struct.pack(">II", 1, 0) + struct.pack(">II", total, total))
    assert len(head) == head_len
    return head + stream


# ---- folder workflow -------------------------------------------------------------------

def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def workspace_for(ff_path: Path, out_dir: Path = None) -> Path:
    return (out_dir or ff_path.parent) / f"{ff_path.stem.replace(' ', '_')}_gsc"


def extract(ff_path: Path, out_dir: Path = None, log=print, folder: Path = None) -> Path:
    """Fastfile -> <name>_gsc/ folder with every script/rawfile as a plain file."""
    ff_path = Path(ff_path)
    zone = load_zone(ff_path.read_bytes())
    raws = find_rawfiles(zone)
    if not raws:
        raise MW2Error(f"{ff_path.name} has no scripts or rawfiles")
    ws = Path(folder) if folder else workspace_for(ff_path, out_dir)
    (ws / META_DIR).mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, text in raws.items():
        rel = name.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            continue
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text)
        manifest[rel] = _sha1(text)
    old = {}
    mf = ws / META_DIR / "manifest.json"
    if mf.exists():  # several fastfiles can be extracted into one folder
        old = json.loads(mf.read_text())
    old.update(manifest)
    mf.write_text(json.dumps(old, indent=1, sort_keys=True))
    gsc = sum(1 for n in raws if n.endswith(".gsc"))
    log(f"{ff_path.name}: {len(raws)} rawfiles ({gsc} .gsc) -> {ws}")
    return ws


def find_workspace(path: Path) -> Path:
    path = Path(path).resolve()
    for p in [path, *path.parents]:
        if (p / META_DIR).is_dir():
            return p
    raise MW2Error(f"{path} is not inside an extracted *_gsc folder")


def changed_files(ws: Path) -> dict[str, bytes]:
    """Files in the folder that are new or differ from what was extracted."""
    manifest = json.loads((ws / META_DIR / "manifest.json").read_text())
    out = {}
    for p in sorted(ws.rglob("*")):
        if not p.is_file() or META_DIR in p.parts or p.suffix.lower() not in SCRIPT_EXTS:
            continue
        rel = p.relative_to(ws).as_posix()
        text = p.read_bytes()
        if manifest.get(rel) != _sha1(text):
            out[rel] = text
    return out


MENU_DIR = "menus"  # in the scripts folder: *.json menu specs: buttons, popups (see mw2menu_emit.patch_menus)
UI_ZONES = ("ui_mp.ffm", "ui_mp.ff")


def menu_specs(ws: Path) -> list[dict]:
    out = []
    for p in sorted((ws / MENU_DIR).glob("*.json")):
        try:
            spec = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            raise MW2Error(f"{MENU_DIR}/{p.name}: {e}") from None
        out += spec if isinstance(spec, list) else [spec]
    return out


def find_ui_zone(ws: Path) -> Path | None:
    """The stock ui_mp fastfile (the menus are copied from it): in the game folder the scripts
    folder sits in, or next to the scripts folder."""
    for folder in (ws.parent, ws):
        for name in UI_ZONES:
            for cand in (folder / name, folder / (name + ".t5menutool.bak")):
                if cand.is_file():
                    return cand
    return None


_RESOLVERS: dict = {}


def menu_resolver(ui_path: Path):
    """Menu reader for the stock ui_mp (cached: reading it takes a few seconds)."""
    from . import mw2menu_emit as emit
    key = (str(ui_path), ui_path.stat().st_mtime, ui_path.stat().st_size)
    if key not in _RESOLVERS:
        _RESOLVERS.clear()
        _RESOLVERS[key] = emit.Resolver(load_zone(ui_path.read_bytes()))
    return _RESOLVERS[key]


def build_patch(path: Path, out_path: Path = None, include_all: bool = False, log=print) -> Path:
    """Folder -> patch_mp.ff holding the edited and added scripts (and the menus/ buttons)."""
    ws = find_workspace(path)
    if include_all:
        files = {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*"))
                 if p.is_file() and META_DIR not in p.parts and p.suffix.lower() in SCRIPT_EXTS}
    else:
        files = changed_files(ws)
    specs = menu_specs(ws)
    menus = None
    if specs:
        ui = find_ui_zone(ws)
        if ui is None:
            raise MW2Error(f"the {MENU_DIR}/ folder changes menus, but ui_mp.ffm isn't in the game folder "
                           "(the menus are copied from it)")
        from .mw2menu_emit import EmitError, patch_menus
        resolver = menu_resolver(ui)
        try:
            menus = (resolver, patch_menus(resolver, specs))
        except EmitError as e:
            raise MW2Error(f"{MENU_DIR}/: {e}") from None
    if not files and not menus:
        raise MW2Error("nothing changed: edit or add a script in the folder first")
    from .custom import CustomError, mw2_hook
    try:
        files = mw2_hook(ws, files, log)
    except CustomError as e:
        raise MW2Error(str(e)) from None
    for name, text in files.items():
        if b"\0" in text:
            raise MW2Error(f"{name} contains a NUL byte")
    from .gsc import check_all
    check_all(files)
    data = build_fastfile(build_rawfile_zone(files, menus=menus))
    out = Path(out_path) if out_path else ws.parent / f"{PATCH_ZONE}.ff"
    out.write_bytes(data)
    for name in files:
        log(f"  + {name}")
    if specs:
        from .mw2menu_emit import describe
        for spec in specs:
            log(f"  + {describe(spec)}")
    log(f"wrote {out} ({len(files)} file(s){', menus' if menus else ''}). "
        "Put it in the game folder next to default_mp.xex.")
    return out
