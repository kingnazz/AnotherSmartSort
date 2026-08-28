# Third-Party Notices

Smart PDF Sorter is built on open-source software. This file lists every
third-party component distributed with the application, its licence, and where
to obtain its source.

The **installed (MSI) build additionally redistributes a Tesseract OCR runtime**
so that scanned documents can be read on a clean machine without the user
installing anything. That redistribution is covered by the Apache License 2.0
below.

Last reviewed: 2026-08-13 for Smart PDF Sorter 1.0.0.

---

## Bundled with the installed application

### Tesseract OCR

- **Version:** 5.4.0.20240606 (Windows x64 build)
- **Licence:** Apache License, Version 2.0
- **Copyright:** Copyright (C) 2006 Google Inc., and contributors
- **Home page:** https://github.com/tesseract-ocr/tesseract
- **Binary distribution:** https://digi.bib.uni-mannheim.de/tesseract/
  (built and published by the University of Mannheim; pinned by SHA-256 in
  `scripts/fetch_ocr_runtime.py`)

Files redistributed: `tesseract.exe`, the dynamic libraries it imports, and the
`eng` and `osd` trained-data files.

> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at
>
>     http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing, software
> distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
> WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
> License for the specific language governing permissions and limitations under
> the License.

#### Tesseract trained data (`eng.traineddata`, `osd.traineddata`)

- **Licence:** Apache License, Version 2.0
- **Source:** https://github.com/tesseract-ocr/tessdata

### Leptonica

Tesseract depends on Leptonica, which is redistributed as `libleptonica-*.dll`.

- **Licence:** BSD 2-Clause License
- **Copyright:** Copyright (C) 2001-2016 Leptonica. All rights reserved.
- **Home page:** http://www.leptonica.org/

> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are met:
> 1. Redistributions of source code must retain the above copyright notice,
>    this list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
> AND ANY EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED.

### Support libraries redistributed with Tesseract

The Tesseract Windows build links a set of standard imaging and text libraries,
which are redistributed alongside it. Each remains under its own permissive
licence:

| Library | Licence |
|---------|---------|
| libpng | PNG Reference Library License (zlib-like) |
| libjpeg / libjpeg-turbo | IJG License / BSD 3-Clause |
| libtiff | libtiff License (MIT-like) |
| libwebp | BSD 3-Clause |
| OpenJPEG | BSD 2-Clause |
| zlib / libdeflate / zstd / bzip2 / brotli | zlib / MIT / BSD |
| libarchive | BSD 2-Clause |
| ICU (`libicu*`) | Unicode License (ICU) |
| GLib, gettext (`libglib`, `libintl`, `libiconv`) | GNU LGPL v2.1 or later |
| HarfBuzz | MIT (Old) |
| FreeType | FreeType License (BSD-style) or GPL v2 |
| Cairo, Pango | GNU LGPL v2.1 |
| Fontconfig | MIT-style |
| libwinpthread, libgcc, libstdc++ (MinGW-w64 runtime) | GPL v3 with GCC Runtime Library Exception / MIT |

The GNU LGPL components are dynamically linked and unmodified, so their
relinking requirement is satisfied: replacement DLLs can be dropped into the
application's `ocr` folder.

---

## Bundled with every build

### Python

- **Licence:** Python Software Foundation License Version 2
- **Home page:** https://www.python.org/
- **Copyright:** Copyright (C) 2001-2024 Python Software Foundation

### PyMuPDF and MuPDF

- **Licence:** GNU Affero General Public License v3.0, or a commercial licence
  from Artifex Software, Inc.
- **Home page:** https://pymupdf.readthedocs.io/ · https://mupdf.com/
- **Copyright:** Copyright (C) 2004-2024 Artifex Software, Inc.

> **Important licensing note.** PyMuPDF/MuPDF is AGPL v3. Distributing Smart PDF
> Sorter to third parties under the AGPL requires making the complete
> corresponding source of this application available to its recipients on the
> same terms. If Smart PDF Sorter is to be distributed as proprietary software,
> obtain a commercial MuPDF licence from Artifex, or replace PyMuPDF with a
> permissively licensed PDF engine. This is a business decision that must be
> settled before wide client distribution — see IMPLEMENTATION_STATUS.md.

### Qt for Python (PySide6) and Qt

- **Licence:** GNU Lesser General Public License v3.0 (LGPL v3), or a commercial
  Qt licence from The Qt Company
- **Home page:** https://www.qt.io/qt-for-python
- **Copyright:** Copyright (C) 2024 The Qt Company Ltd.

Qt is used unmodified and linked dynamically. Under LGPL v3 the user must be
able to replace the Qt libraries: in the installed build these are ordinary DLLs
in the application's `_internal` folder and can be substituted.

### Pillow

- **Licence:** MIT-CMU License
- **Home page:** https://python-pillow.org/
- **Copyright:** Copyright (C) 2010-2024 by Jeffrey A. Clark and contributors

### openpyxl

- **Licence:** MIT License
- **Home page:** https://openpyxl.readthedocs.io/
- **Copyright:** Copyright (C) 2010 openpyxl

### Requests

- **Licence:** Apache License, Version 2.0
- **Home page:** https://requests.readthedocs.io/
- **Copyright:** Copyright 2019 Kenneth Reitz

### keyring

- **Licence:** MIT License
- **Home page:** https://github.com/jaraco/keyring

### PyInstaller (build-time only)

- **Licence:** GPL v2 with a bootloader exception permitting the distribution of
  packaged applications under any licence
- **Home page:** https://pyinstaller.org/

### WiX Toolset (build-time only)

- **Licence:** Microsoft Reciprocal License (MS-RL)
- **Home page:** https://wixtoolset.org/

---

## Obtaining source code

Source for the components above is available from the home pages listed. For
components whose licences require a written offer of source, contact the
publisher of Smart PDF Sorter.
