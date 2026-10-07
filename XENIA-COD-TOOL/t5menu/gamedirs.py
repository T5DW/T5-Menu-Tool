"""Find the MW2 and Bo1 Xbox 360 game folders on this PC (the folders Xenia runs the xex from).

A game folder is one that holds a .xex and the game's fastfiles. Which game it is comes from
the fastfiles themselves: MW2 July 2009 files are version 0xFD, Bo1 files 0x1D7 / 0x1D9.

Where we look, in order:
  1. the folder of the xex a running Xenia was started with (its command line)
  2. the folders saved in the tool's settings
  3. Desktop, Downloads, Documents, Games folders in your user folder, and the top few
     levels of every drive, skipping Windows and program folders
"""

from __future__ import annotations

import os
import re
import string
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MW2_VERSION = 0xFD
BO1_VERSIONS = (0x1D7, 0x1D9)
FASTFILES = ("common_mp.ffm", "common_mp.ff", "ui_mp.ffm", "ui_mp.ff", "code_post_gfx_mp.ffm",
             "code_post_gfx_mp.ff", "patch_mp.ff", "mp_nuked.ff", "frontend.ff")
GSC_FOLDER = "gsc"
COMPILED_FOLDER = "Compiled"
SKIP = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "appdata",
        "system volume information", "node_modules", ".git", "msocache", "perflogs", "recovery",
        "steamapps", "xboxgames", "windowsapps"}


@dataclass(frozen=True)
class GameDir:
    game: str  # "mw2" or "bo1"
    path: Path
    xex: str  # the xex file name found there

    def __str__(self):
        return f"{self.game.upper()}: {self.path} ({self.xex})"


def fastfile_game(path: Path) -> str | None:
    """'mw2' / 'bo1' from a fastfile's version, None if it is neither."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return None
    if len(head) < 12 or not head.startswith(b"IWff"):
        return None
    version = struct.unpack(">I", head[8:12])[0]
    if version == MW2_VERSION:
        return "mw2"
    if version in BO1_VERSIONS:
        return "bo1"
    return None


def identify(folder: Path) -> GameDir | None:
    """The game in this folder, if it has a .xex and fastfiles of a known version."""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    xexes = sorted(n for n in names if n.lower().endswith(".xex"))
    if not xexes:
        return None
    lower = {n.lower(): n for n in names}
    candidates = [lower[f] for f in FASTFILES if f in lower]
    candidates += sorted(n for n in names if n.lower().endswith((".ff", ".ffm")) and n not in candidates)[:20]
    for name in candidates:
        game = fastfile_game(folder / name)
        if game:
            xex = next((x for x in xexes if x.lower().startswith(("default_mp", "codmp"))), xexes[0])
            return GameDir(game, folder, xex)
    return None


def _walk(root: Path, depth: int, deadline: float):
    """Folders under root up to depth levels, skipping system and hidden ones."""
    stack = [(root, 0)]
    while stack:
        if time.monotonic() > deadline:
            return
        d, level = stack.pop()
        yield d
        if level >= depth:
            continue
        try:
            with os.scandir(d) as it:
                subs = [e.path for e in it if e.is_dir(follow_symlinks=False)
                        and e.name.lower() not in SKIP and not e.name.startswith(".")]
        except OSError:
            continue
        stack.extend((Path(p), level + 1) for p in reversed(sorted(subs)))


def xenia_xex_folders() -> list[Path]:
    """Folders of the .xex files running Xenia processes were started with (Windows only)."""
    out = []
    if sys.platform != "win32":
        return out
    from .cbuf import find_xenia_pids
    for pid, _ in find_xenia_pids():
        line = process_command_line(pid) or ""
        for m in re.finditer(r'"([^"]+\.xex)"|(\S+\.xex)', line, re.I):
            p = Path(m.group(1) or m.group(2))
            if p.parent.is_dir():
                out.append(p.parent)
    return out


def process_command_line(pid: int) -> str | None:
    """Another process's command line (NtQueryInformationProcess class 60, Windows 8.1+)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        size = ctypes.c_ulong(0)
        buf = ctypes.create_string_buffer(0x10000)
        st = ntdll.NtQueryInformationProcess(wintypes.HANDLE(h), 60, buf, len(buf), ctypes.byref(size))
        if st != 0:
            return None

        class US(ctypes.Structure):
            _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

        us = US.from_buffer(buf)
        return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else None
    finally:
        k32.CloseHandle(h)


def search_roots() -> list[tuple[Path, int]]:
    """(folder, depth) pairs to search, most likely first."""
    home = Path.home()
    roots = [(home / n, 4) for n in ("Desktop", "Downloads", "Documents", "Games", "OneDrive/Desktop",
                                      "OneDrive/Documents")]
    roots.append((home, 2))
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if letter not in "AB" and drive.exists():
                roots.append((drive, 3))
    else:
        roots.append((Path("/"), 3))
    return roots


def find_game_dirs(known: list[str] = (), time_limit: float = 20.0, log=None) -> dict[str, list[GameDir]]:
    """{'mw2': [...], 'bo1': [...]}: every game folder found, the most likely one first."""
    found: dict[str, list[GameDir]] = {"mw2": [], "bo1": []}
    seen = set()

    def consider(folder: Path):
        try:
            key = os.path.normcase(str(Path(folder).resolve()))
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        g = identify(Path(folder))
        if g:
            found[g.game].append(g)
            if log:
                log(f"found {g}")

    for folder in xenia_xex_folders():
        consider(folder)
    for folder in known:
        if folder:
            consider(Path(folder))
    deadline = time.monotonic() + time_limit
    for root, depth in search_roots():
        if not root.is_dir():
            continue
        for d in _walk(root, depth, deadline):
            consider(d)
        if time.monotonic() > deadline:
            if log:
                log("stopped searching (time limit); use Browse if your game folder wasn't found")
            break
    return found


def gsc_folder(game_dir: Path) -> Path:
    """<game folder>/gsc, created if missing: where that game's scripts are edited."""
    p = Path(game_dir) / GSC_FOLDER
    p.mkdir(exist_ok=True)
    return p


def loadable_name(source: str) -> str:
    """The file the game actually loads, for a fastfile that was decompiled.

    ui_mp.ffm -> ui_mp.ff   (the .ff is loaded instead of the stock .ffm)
    ui_mp.ff  -> ui_mp.ff   (Bo1)
    patch_mp.ff -> patch_mp.ff   (MW2 patch zone)
    anything.ff -> anything.ff   (edit any fastfile, get the same name back)
    """
    return f"{Path(source).stem}.ff"


def compiled_folder(game_dir: Path) -> Path:
    """<game folder>/Compiled, created if missing: where compiled fastfiles are written."""
    p = Path(game_dir) / COMPILED_FOLDER
    p.mkdir(parents=True, exist_ok=True)
    return p
