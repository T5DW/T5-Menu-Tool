# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=["tkinterdnd2", "Crypto.Cipher.AES"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
