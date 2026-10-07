# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

root = Path(SPECPATH)

# When built with a conda-forge Windows Python, tkinter's Tcl/Tk DLLs live in <prefix>/Library/bin
# and the Tcl/Tk script dirs in <prefix>/Library/lib, which PyInstaller's default tkinter hook does
# not pick up. Bundle them explicitly. These paths simply don't exist on a normal python.org build,
# so the guards below make the spec a no-op there (build.ps1 on real Windows still works).
prefix = Path(sys.base_prefix)
lib_bin = prefix / "Library" / "bin"
lib_lib = prefix / "Library" / "lib"

binaries = []
for dll in ("tcl86t.dll", "tk86t.dll", "zlib1.dll", "zlib.dll",
            "vcruntime140.dll", "vcruntime140_1.dll"):
    p = lib_bin / dll
    if p.exists():
        binaries.append((str(p), "."))

datas = []
for name in ("tcl8.6", "tk8.6", "tcl8"):
    d = lib_lib / name
    if d.is_dir():
        datas.append((str(d), f"lib/{name}"))

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["tkinterdnd2", "Crypto.Cipher.AES"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "rthook_tk.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="T5MenuTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
