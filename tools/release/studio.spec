# PyInstaller's recipe for the built Studio: one folder, windowed.
#
# Run by tools/build_release.py, which checks what it was built from and what
# came out; build with that, not with PyInstaller by hand. What is left out
# of the folder, and why, is decided there (kept_in_bundle), so the tests can
# hold it.
#
# One folder, never one file: the Qt and PySide6 DLLs stay separate files a
# user can replace, which is what the LGPL asks of a program linked to them,
# and the reading's process, which is the exe started again, starts without
# unpacking anything (THIRD-PARTY-NOTICES.md, section 5).
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_release as release  # noqa: E402

PACKAGE = ROOT / "anno_modding_studio"

version_file = Path(workpath) / "version-info.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(release.version_resource(), encoding="utf-8")

analysis = Analysis(
    [str(ROOT / "tools" / "release" / "entry.py")],
    pathex=[str(ROOT)],
    # Every module of the package, whether or not an import names it where
    # the freezing tool looks: the self-test imports each one.
    hiddenimports=collect_submodules("anno_modding_studio"),
    datas=[
        (str(PACKAGE / "resources"), "anno_modding_studio/resources"),
        (str(PACKAGE / "locale"), "anno_modding_studio/locale"),
    ],
    excludes=list(release.EXCLUDED_MODULES),
    noarchive=False,
)
analysis.binaries = [
    entry for entry in analysis.binaries
    if release.kept_in_bundle(entry[0]) and release.from_this_python(entry[1])
]
analysis.datas = [entry for entry in analysis.datas if release.kept_in_bundle(entry[0])]

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=release.EXE_NAME,
    console=False,
    icon=str(PACKAGE / "resources" / "icon.ico"),
    version=str(version_file),
    upx=False,
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    name=release.FOLDER_NAME,
    upx=False,
)
