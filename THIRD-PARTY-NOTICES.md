# Third-party notices

Copyright (C) 2026 aleph-shin

Anno Modding Studio is free software under the GNU General Public License,
version 3 or (at your option) any later version. The full text is in
[LICENSE](LICENSE). It comes with ABSOLUTELY NO WARRANTY.

This file lists the work of other people that the Studio contains, runs on, or
uses, and the terms each comes under. [docs/credits.md](docs/credits.md) thanks
the projects the Studio learned from.

Checked 26 Oct 2026, against PySide6 6.12.0, lxml 6.1.3 and Python 3.13.

## 1. Code ported into the Studio

Two modules are ports of MIT-licensed code. MIT is compatible with the GPL, and
its one condition is that its notice goes with the code. Each module is under
`GPL-3.0-or-later AND MIT`.

### The mod loader's XML operations

`anno_modding_studio/core/applying.py` is a function-by-function Python port of
`libs/xml-operations/src/xml_operations.cc` in
[jakobharder/anno-mod-loader](https://github.com/jakobharder/anno-mod-loader),
at `5a4464d` on `main` (Anno 117) and `8326b7c` on `stable/anno1800` (Anno 1800).
It keeps the loader's warning texts word for word. A few smaller pieces
elsewhere follow the loader's source the same way: how it reads an op's type,
splits a GUID list and finds an asset, and how it reads a condition's `#mod` and
`$option` names.

The loader's licence, from its `LICENSE`:

```text
MIT License

Copyright (c) 2019 Alexander Guettler
              2022 Jakob Harder (mostly xml_operations)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### iModYourAnno's tweak export

`anno_modding_studio/core/imya.py` ports iModYourAnno's export of a mod's
tweaks (`TweakerFile.Export`, `TweakExporter`, `XmlPatchParser`), from
[anno-mods/iModYourAnno](https://github.com/anno-mods/iModYourAnno), so both
tools write the same files.

iModYourAnno's licence, from its `LICENSE`, whole:

```text
# License

This project is not affiliated in any way with Ubisoft.

Anno 1800 is a trademark of Ubisoft Entertainment in the US and/or other countries.
Anno is a trademark of Ubisoft GmbH in the US and/or other countries.

## iModYourAnno

                               The MIT License (MIT)

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies
or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

The licence names no copyright holder. The copyright is held by the
iModYourAnno contributors.

## 2. Files that are not under the GPL

### The mod.io logos

The files in `anno_modding_studio/resources/modio/` are mod.io's logos. They
are **not** covered by the GNU GPL. They are trademarks of mod.io Pty Ltd,
included to meet mod.io's API Access Terms (clauses 2.3 and 3.3,
<https://mod.io/legal/apiterms>). Those terms grant a non-transferable,
non-sublicensable licence to use them to show that the program uses the mod.io
API.

If you redistribute or modify this program, your right to use the logos comes
from your own agreement with mod.io, not from this licence. Don't reuse them for
anything else. Remove them if you don't use the mod.io API.
[resources/modio/NOTICE.md](anno_modding_studio/resources/modio/NOTICE.md) says
the same beside the files.

### The community GUID register

The register of GUID ranges at
[anno-mods/GuidRanges](https://github.com/anno-mods/GuidRanges) carries no
licence, so the Studio does not include a copy. It fetches the register from
GitHub the first time a New asset dialog needs it, or when you press
*Update now* in Settings, and keeps that copy on your PC only. The code holds
two numbers from it, the safe span and the *Personal use* range.

## 3. What the Studio runs on

Installed from source, the Studio uses these as they are installed on your PC.
`pip` installs them, each under its own licence, and the source tree contains
none of them. The frozen build carries them (section 5).

| Component | Licence | How the Studio uses it |
|---|---|---|
| Python (CPython) | PSF-2.0, with the licences of the software it incorporates | The interpreter and its standard library |
| Qt 6: Core, Gui, Widgets, Svg, Network | LGPL-3.0-only | The interface. Network is only the local channel that keeps one Studio open |
| PySide6 and shiboken6 (Qt for Python) | LGPL-3.0-only, or GPL-2.0-only or GPL-3.0-only | Python's bindings for Qt. The Studio uses them under the LGPLv3 |
| lxml | BSD-3-Clause, with parts under the PSF-2.0 and ElementTree licences | Reading and writing XML |
| libxml2, libxslt, libexslt | MIT | Inside lxml's wheels |
| zlib | zlib | Inside lxml's wheels, and in Qt |
| GNU libiconv | LGPL-2.1-or-later | Inside lxml's wheels |
| OpenSSL 3 | Apache-2.0 | Python's `https`, for mod.io, GitHub and the Marketplace |
| libffi, SQLite, bzip2, xz, expat, libmpdec | MIT, public domain and BSD-style | Parts of the Python build |
| Microsoft Visual C++ runtime | Microsoft's redistributable terms | Parts of the Python and Qt builds |
| PyInstaller's bootloader and run-time hooks | GPL-2.0-or-later with the bootloader exception, and Apache-2.0 | Start the Windows zip's Studio. In the zip only |

The GPLv3 Studio can use LGPLv3 Qt and PySide6, and LGPL-2.1 libiconv. Each
library stays under its own licence, and you can replace it with your own build
of the same version.

The Studio also runs programs from your own installation, which it never
copies or ships: the game, Steam or Ubisoft Connect to start it, and the
`xmltest` loader check inside the *Anno Modding Tools* extension for VS Code
(MIT, © Jakob Harder).

## 4. Game data

No game data is distributed. The Studio reads the game files from your own
installation, on your computer, and keeps what it reads there.

## 5. For packaging: what the frozen zip must carry

The Windows zip on GitHub Releases is a one-folder frozen build. It contains
Python, Qt, PySide6 and lxml themselves, so it must carry their notices and
offer their source. Keep it one-folder: the Qt and PySide6 DLLs then stay
separate files a user can replace, which is what the LGPL asks of a program
linked to a library. The PySide6 wheels hold none of the LGPL or GPL texts,
only Qt's commercial licence, so the build has to add them.

At the root of the zip:

- [ ] `LICENSE.txt`: a copy of [LICENSE](LICENSE).
- [ ] `THIRD-PARTY-NOTICES.txt`: a copy of this file.
- [ ] `SOURCES.txt`: the exact version and download address of each source below.

In `licenses/`:

- [ ] `GPL-3.0.txt`: a copy of [LICENSE](LICENSE). The LGPLv3 builds on it, so
      Qt and PySide6 need it as well.
- [ ] `LGPL-3.0.txt`: <https://www.gnu.org/licenses/lgpl-3.0.txt>, for Qt,
      PySide6 and shiboken6.
- [ ] `LGPL-2.1.txt`: <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt>,
      for libiconv inside lxml.
- [ ] `Apache-2.0.txt`: <https://www.apache.org/licenses/LICENSE-2.0.txt>, for
      OpenSSL 3 and, if PyInstaller is used, its run-time hooks.
- [ ] `zlib.txt`: the licence at <https://www.zlib.net/zlib_license.html>.
- [ ] `Python-LICENSE.txt`: the build interpreter's own `LICENSE.txt`,
      unchanged. It holds the PSF licence, the conditions for the Windows build
      and the Microsoft runtime, and the bzip2, libffi and OpenSSL licences.
- [ ] `Python-incorporated.txt`: the "Licenses and Acknowledgements for
      Incorporated Software" section of <https://docs.python.org/3/license.html>,
      for the build's Python version. It covers expat, libmpdec, HACL* and the
      rest compiled into Python.
- [ ] `lxml-LICENSE.txt` and `lxml-LICENSES.txt`: both files from
      `lxml-<version>.dist-info/licenses/`.
- [ ] `MIT-anno-mod-loader.txt` and `MIT-iModYourAnno.txt`: the two notices in
      section 1, as quoted.
- [ ] `Qt/`: Qt's own third-party notices for the Qt DLLs and plugins in the
      zip, from Qt's SBOM or <https://doc.qt.io/qt-6/licenses-used-in-qt.html>
      for the exact Qt version. On Windows they include zlib, libpng, HarfBuzz,
      PCRE2, double-conversion and md4c, plus libjpeg, libtiff and libwebp if
      those image plugins are shipped.
- [ ] The freezing tool's own licence for what it puts into the zip. For
      PyInstaller that is its `COPYING.txt`, as `PyInstaller-COPYING.txt`
      (GPL-2.0-or-later with the bootloader exception, and Apache-2.0 for the
      run-time hooks).

Only if the file is in the zip:

- [ ] `opengl32sw.dll` (Mesa, MIT): add Mesa's notice, or leave the DLL out.
- [ ] `Qt6VirtualKeyboard*` (GPL-3.0-only) and `Qt6Pdf*` (PDFium, BSD): leave
      these out. Build from `PySide6-Essentials` and exclude the
      `platforminputcontexts` virtual keyboard plugin and the `qpdf` image
      plugin. Then check that no `Qt6VirtualKeyboard*`, `Qt6Pdf*`, `Qt6Quick*`
      or `Qt6Qml*` file is in the folder.

`anno_modding_studio/resources/modio/NOTICE.md` goes into the zip with the
logos beside it.

`SOURCES.txt` names:

- [ ] the Studio's tagged source on GitHub, which GitHub attaches to the release
- [ ] the PySide6 and shiboken6 source for the version shipped, from
      <https://download.qt.io/official_releases/QtForPython/>
- [ ] the Qt source for the same Qt version, from <https://download.qt.io/>
- [ ] the lxml source distribution on PyPI for the version shipped, and the
      libiconv version it was built with, from <https://ftp.gnu.org/gnu/libiconv/>
- [ ] the CPython source for the build's Python version, from
      <https://www.python.org/downloads/source/>

A link to someone else's server is allowed, but the release stays responsible
for the source if that server goes away. To be safe, attach the PySide6 and
lxml source archives to the release as well.

`tools/build_release.py` adds all of the above when it packages the zip
([development.md](docs/development.md#making-a-release)). The texts no
installed package carries are kept in `tools/release/licenses/`; the rest are
copied from the LICENSE, this file, the build interpreter and the installed
wheels. It refuses to package while `tools/release/licenses/Qt/` is empty, or holds
the notices for another Qt version than the one it freezes.

A trial build on 25 Oct 2026 from PySide6-Essentials 6.12.0, lxml 6.1.3,
PyInstaller 6.22.3 and Python 3.13 held these of Qt, as the first build on 18 Oct,
from 6.11.2, did: `Qt6Core`, `Qt6Gui`,
`Qt6Widgets`, `Qt6Svg` and `Qt6Network`; the `qwindows` and `qoffscreen`
platform plugins, the `qico` and `qsvg` image formats and the `qsvgicon` icon
engine; and `qtbase_de.qm`. No `opengl32sw.dll`, no `Qt6VirtualKeyboard*`,
`Qt6Pdf*`, `Qt6Quick*` or `Qt6Qml*`, and no TLS, input-method or other image
plugin: the build leaves them out, and fails if one is there. So Qt's notices
are needed for those five modules and their plugins only, and not for libjpeg,
libtiff or libwebp. Of lxml, only `etree` and `_elementpath` are in it.
