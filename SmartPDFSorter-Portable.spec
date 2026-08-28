# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PORTABLE build (onefile).

A single EXE that can be copied onto a machine and run with no installation.
This is the *secondary* artifact -- the MSI is what clients install.

The OCR runtime is deliberately **not** bundled here. It is ~130 MB, and a
onefile build unpacks its entire payload to a temporary directory on every
launch, which would make the portable build slow to start for a capability most
portable users do not need. The portable EXE still uses OCR if it finds either:

* an ``ocr`` folder placed next to the EXE, or
* a Tesseract installation already on the machine.

Output::

    dist/SmartPDFSorter-Portable.exe

Build with::

    pyinstaller SmartPDFSorter-Portable.spec --noconfirm
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
VERSION_RESOURCE = write_version_resource(ROOT, VERSION, portable=True)

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
    a.binaries,
    a.datas,
    [],
    name=f"{APP_NAME}-Portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path(ROOT),
    version=VERSION_RESOURCE,
)
