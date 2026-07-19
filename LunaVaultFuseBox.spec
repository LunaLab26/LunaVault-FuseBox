# -*- mode: python ; coding: utf-8 -*-

import os
import re

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[('src/assets', 'assets'), ('luts', 'luts')],
    hiddenimports=['PySide6.QtXml', 'PySide6.QtNetwork', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtMultimedia', 'numpy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6', 'PyQt5', 'tkinter'],
    noarchive=False,
    optimize=0,
)

# --- Do not ship glibc-family libraries -----------------------------------
# libc, libm, libmvec and friends are provided by every Linux system and are
# ABI-locked to the host's libc. Bundling our build machine's copies causes
# "version `GLIBC_ABI_...' not found" crashes at startup on systems whose libc
# differs (e.g. the Steam Deck). Dropping them lets the frozen app load the
# target's own matching copies. No-op on Windows/macOS builds (names won't
# match). See build47 field report for the libmvec.so.1 failure this fixes.
_GLIBC_FAMILY = re.compile(
    r'^(ld-linux.*|libc|libm|libmvec|libpthread|libdl|librt|libresolv'
    r'|libutil|libnsl|libBrokenLocale|libanl|libcrypt)\.so'
)
a.binaries = [
    b for b in a.binaries
    if not _GLIBC_FAMILY.match(os.path.basename(b[0]))
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LunaVaultFuseBox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['src/assets/lunavault.png'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LunaVaultFuseBox',
)
