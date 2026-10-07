"""Command line: python -m t5menu <command> ..."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .fastfile import FastFile
from .menufile import MenuEditError
from .workspace import compile_workspace, decompile
from .zone import ZoneParseError
from .fastfile import FastFileError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="t5menu", description="Black Ops 1 and MW2 July 2009 Xbox 360 menu and GSC tool")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decompile", help="fastfile (Bo1 .ff or MW2 .ffm/.ff) -> folder of editable .menu files")
    d.add_argument("fastfile", type=Path)
    d.add_argument("-o", "--out-dir", type=Path)

    c = sub.add_parser("compile", help="edited .menu folder -> patched fastfile")
    c.add_argument("folder", type=Path, help="the *_menus folder, or any file inside it")
    c.add_argument("-o", "--output", type=Path)
    c.add_argument("--signed", action="store_true",
                   help="write an encrypted IWff0100 file (signature will not match)")

    u = sub.add_parser("unpack", help="fastfile -> raw .zone")
    u.add_argument("fastfile", type=Path)
    u.add_argument("-o", "--output", type=Path)

    p = sub.add_parser("pack", help="raw .zone + original fastfile header -> fastfile")
    p.add_argument("zone", type=Path)
    p.add_argument("original", type=Path, help="fastfile to take the header (zone name) from")
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--signed", action="store_true")

    b = sub.add_parser("cbuf", help="send console commands to Bo1 or MW2 in Xenia, or PC MW2 (iw4mp.exe) (Windows)")
    b.add_argument("command", nargs="*", help='e.g. cg_fovScale 2 (leave empty for an interactive command line)')
    b.add_argument("--pid", type=int, help="Xenia process id (default: first process named *xenia*)")
    b.add_argument("--client", type=int, default=0, help="local client number (default 0)")

    m = sub.add_parser("mw2", help="MW2 July 2009 build: extract GSC / build patch_mp.ff")
    msub = m.add_subparsers(dest="mw2cmd", required=True)
    me = msub.add_parser("extract", help="MW2 fastfile (.ff/.ffm) -> <name>_gsc/ folder of scripts")
    me.add_argument("fastfile", type=Path)
    me.add_argument("-o", "--out-dir", type=Path)
    mb = msub.add_parser("build", help="edited *_gsc folder -> patch_mp.ff with the changed scripts")
    mb.add_argument("folder", type=Path, help="the *_gsc folder, or any file inside it")
    mb.add_argument("-o", "--output", type=Path)
    mb.add_argument("--all", action="store_true", help="pack every file in the folder, not just changed ones")
    mx = msub.add_parser("patch-xex", help="patch the xex (or every xex in a game folder) to load unsigned fastfiles")
    mx.add_argument("path", type=Path, help="default_mp.xex, or the game folder")
    mx.add_argument("--undo", action="store_true", help="put the original check back")

    a = ap.parse_args(argv)
    if a.cmd == "mw2":
        from .mw2 import MW2Error, build_patch, extract
        from . import xexpatch
        try:
            if a.mw2cmd == "patch-xex":
                if a.undo:
                    xexpatch.unpatch(a.path)
                elif a.path.is_dir():
                    return 0 if xexpatch.patch_game_folder(a.path) else 1
                else:
                    xexpatch.patch(a.path)
            elif a.mw2cmd == "extract":
                extract(a.fastfile, a.out_dir)
            else:
                build_patch(a.folder, a.output, include_all=a.all)
        except (MW2Error, xexpatch.XexPatchError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    if a.cmd == "cbuf":
        from .cbuf import CbufError, connect, repl
        try:
            cb = connect(a.pid, a.client)
            if a.command:
                cb.send(" ".join(a.command))
                print("sent")
            else:
                repl(cb)
        except CbufError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    try:
        from . import mw2menu
        from .mw2 import MW2Error, is_mw2_fastfile
        if a.cmd == "decompile" and is_mw2_fastfile(a.fastfile.read_bytes()[:12]):
            mw2menu.decompile(a.fastfile, a.out_dir)
        elif a.cmd == "compile" and mw2menu.is_mw2_menu_workspace(a.folder):
            mw2menu.compile_workspace(a.folder, a.output)
        elif a.cmd == "decompile":
            decompile(a.fastfile, a.out_dir)
        elif a.cmd == "compile":
            compile_workspace(a.folder, a.output, a.signed)
        elif a.cmd == "unpack":
            ff = FastFile.load(a.fastfile)
            out = a.output or a.fastfile.with_suffix(".zone")
            out.write_bytes(ff.zone)
            print(f"wrote {out} ({len(ff.zone):,} bytes)")
        elif a.cmd == "pack":
            ff = FastFile.load(a.original)
            ff.zone = a.zone.read_bytes()
            out = a.output or a.zone.with_suffix(".compiled.ff")
            out.write_bytes(ff.build(signed=a.signed))
            print(f"wrote {out}")
    except (MenuEditError, ZoneParseError, FastFileError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
