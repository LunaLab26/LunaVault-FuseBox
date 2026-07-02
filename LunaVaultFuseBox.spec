# -*- mode: python ; coding: utf-8 -*-
# LunaVault FuseBox v1.4 — PyInstaller spec (onedir, for LGPL relinking)

a = Analysis(
    ['src\\main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src\\assets', 'assets'),
        ('luts',        'luts'),
        ('licenses',    'licenses'),
        ('LICENSE',     '.'),
    ],
    hiddenimports=[
        'PySide6.QtXml',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtMultimedia',
        'numpy',
    ],
    excludes=[
        'PyQt6',
        'PyQt5',
        'tkinter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
    optimize=0,
)
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
    icon='src\\assets\\lunavault.ico',
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
