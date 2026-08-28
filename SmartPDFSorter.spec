# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the INSTALLED build (onedir).

This is the build the MSI installs. A directory layout is used deliberately:

* it starts fast -- nothing is unpacked to a temporary directory per launch
* antivirus and EDR products flag it far less often than a self-extracting EXE
* the bundled OCR runtime sits next to the executable where the app finds it
* Windows Installer can repair individual files

Output::

    dist/SmartPDFSorter/
        SmartPDFSorter.exe
        _internal/...
        ocr/                (staged separately by scripts/build_windows.ps1)

Build with::

    pyinstaller SmartPDFSorter.spec --noconfirm
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT / "packaging"))

from pyinstaller_common import (  # noqa: E402
    APP_NAME,
    data_files,
    excludes,
    hidden_imports,
    icon_path,
    read_version,
    write_version_resource,
)

VERSION = read_version(ROOT)
VERSION_RESOURCE = write_version_resource(ROOT, VERSION, portable=False)

a = Analysis(
    ["app/main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=data_files(ROOT),
    hiddenimports=hidden_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes(),
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: binaries live alongside, not inside
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # desktop application, not a console tool
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path(ROOT),
    version=VERSION_RESOURCE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
