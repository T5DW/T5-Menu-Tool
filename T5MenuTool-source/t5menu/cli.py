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
    ap = argparse.ArgumentParser(prog="t5menu", description="Black Ops 1 Xbox 360 menu tool")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decompile", help="fastfile -> folder of editable .menu files")
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

    a = ap.parse_args(argv)
    try:
        if a.cmd == "decompile":
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
    except (MenuEditError, ZoneParseError, FastFileError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
