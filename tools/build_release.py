"""Build the Studio for Windows: the one-folder program, and the zip that
goes on GitHub Releases.

    python tools/build_release.py               all of the steps below
    python tools/build_release.py freeze        the program's folder, checked
    python tools/build_release.py self-test     the program checking itself
    python tools/build_release.py package       notices, zip and checksum

Run it with the Python and the packages ``requirements-release.txt`` pins,
installed from it with ``pip install --require-hashes``: it stops when what
is installed differs, unless ``--unlocked`` says a trial build may.  The
release workflow (``.github/workflows/release.yml``) runs the three steps
on a tag; docs/development.md, "Making a release", says the rest.

What comes out, in ``dist/``:

- ``AnnoModdingStudio/``, the program: ``AnnoModdingStudio.exe`` beside its
  ``_internal`` folder, with the notices of THIRD-PARTY-NOTICES.md, section
  5, added at its root.
- ``AnnoModdingStudio-<version>-windows-x64.zip``, that folder's contents,
  and ``.sha256`` beside it.

The freeze is ``tools/release/studio.spec``.  This decides what of Qt it
leaves out (:func:`kept_in_bundle`): Qt Virtual Keyboard, which is GPL-only,
Qt PDF, the image formats the Studio never reads, the network plugins of a
Studio whose only use of QtNetwork is a pipe on this PC, and Qt's catalogs
for languages the Studio has none of.  After the freeze it fails on any of
those still in the folder (:func:`check_bundle`).

The notices for Qt's own third-party code (``licenses/Qt``) are copied from
``tools/release/licenses/Qt/``, which holds them for the Qt version the lock
pins.  Without them, or with notices for another Qt version than the one
frozen, the zip is not made, unless ``--allow-missing-notices`` says a trial
build may.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "tools" / "release"
LOCK = ROOT / "requirements-release.txt"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "release"

EXE_NAME = "AnnoModdingStudio"
FOLDER_NAME = "AnnoModdingStudio"
# The oldest Python the release is built with: its Windows builds ship
# OpenSSL 3, and its zipfile refuses overlapping entries (CVE-2024-0450).
PYTHON = (3, 12)
# The Python whose incorporated-software notices tools/release/licenses
# holds; a build with another is refused until they are refreshed.
NOTICES_PYTHON = (3, 13)

# Modules the program never imports, which a hook or the standard library
# could otherwise bring in.
EXCLUDED_MODULES = (
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "lib2to3",
    "pydoc_data", "test",
    # The Studio uses lxml.etree, and none of the rest lxml's hook collects.
    "lxml.objectify", "lxml.html", "lxml.isoschematron", "lxml.sax", "lxml.builder",
)

# Qt's plugins, by folder: the ones kept, and every other in those folders
# goes.  Platforms: Windows, and off screen for the self-test.  Image
# formats: the icon and the SVGs the Studio draws.  Nothing for input
# methods (the GPL-only virtual keyboard), TLS or network information (the
# Studio's HTTP is Python's; QtNetwork is only a local pipe), touch input
# or Windows' native style (the Studio draws in Fusion).
KEPT_PLUGINS = {
    "platforms": {"qwindows.dll", "qoffscreen.dll"},
    "imageformats": {"qico.dll", "qsvg.dll"},
    "iconengines": {"qsvgicon.dll"},
    "platforminputcontexts": set(),
    "generic": set(),
    "tls": set(),
    "networkinformation": set(),
    "styles": set(),
}

# What must never be in the folder, by file name: GPL-only or unneeded Qt
# modules, Mesa's software OpenGL, and Tcl/Tk.
FORBIDDEN = (
    "Qt6VirtualKeyboard*", "Qt6Pdf*", "Qt6Quick*", "Qt6Qml*", "Qt6WebEngine*",
    "Qt6Charts*", "Qt6DataVisualization*", "Qt6Graphs*", "Qt6NetworkAuth*",
    "Qt6Multimedia*", "Qt6Designer*", "qtvirtualkeyboardplugin.dll", "qpdf.dll",
    "opengl32sw.dll", "tcl*.dll", "tk*.dll", "_tkinter.pyd",
    # Another program's OpenSSL, found on the PATH for Qt's TLS plugin, which
    # is left out: Git's, on the machine this was first built on and on
    # GitHub's runners.  Python's own is libssl-3.dll and libcrypto-3.dll.
    "libssl-3-x64.dll", "libcrypto-3-x64.dll",
)


# -- versions -----------------------------------------------------------------
def version() -> str:
    """The Studio's version, read where it is written."""
    text = (ROOT / "anno_modding_studio" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if found is None:
        raise SystemExit("no __version__ in anno_modding_studio/__init__.py")
    return found.group(1)


def _package_text(name: str) -> str:
    text = (ROOT / "anno_modding_studio" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^%s = "([^"]+)"' % name, text, re.MULTILINE)
    if found is None:
        raise SystemExit("no %s in anno_modding_studio/__init__.py" % name)
    return found.group(1)


def repo_url() -> str:
    return _package_text("REPO_URL")


def guide() -> str:
    """The user guide's path in the repository."""
    return _package_text("GUIDE")


def zip_name(of: str) -> str:
    """The release zip's name: the update check finds it by this."""
    return "%s-%s-windows-x64.zip" % (FOLDER_NAME, of)


def tag_matches(tag: str, of: str) -> bool:
    """Whether a pushed tag, ``v<version>``, names this version."""
    return tag == "v" + of


def version_resource(of: Optional[str] = None) -> str:
    """The exe's Windows version resource, in the form PyInstaller reads."""
    of = of or version()
    numbers = (*(int(part) for part in of.split(".")), 0)[:4]
    strings = {
        "CompanyName": "Anno Modding Studio",
        "FileDescription": "Anno Modding Studio",
        "FileVersion": of,
        "InternalName": EXE_NAME,
        "LegalCopyright": "GNU GPL v3 or later",
        "OriginalFilename": EXE_NAME + ".exe",
        "ProductName": "Anno Modding Studio",
        "ProductVersion": of,
    }
    table = ",\n".join(
        "          StringStruct(%r, %r)" % item for item in strings.items()
    )
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        "    filevers=%(n)r, prodvers=%(n)r, mask=0x3f, flags=0x0,\n"
        "    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n%(table)s])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n"
    ) % {"n": numbers, "table": table}


def locked() -> Dict[str, str]:
    """What the lock pins: each package's normalised name, and its version."""
    pins = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\;]+)", line)
        if found:
            pins[_normalised(found.group(1))] = found.group(2)
    return pins


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


# -- what goes into the folder -------------------------------------------------
def kept_in_bundle(dest: str) -> bool:
    """Whether a file the freeze collected stays, by where it goes in the
    folder: ``PySide6/plugins/<kind>/<file>`` and Qt's ``.qm`` catalogs are
    decided here, everything else is kept."""
    parts = dest.replace("\\", "/").split("/")
    name = parts[-1]
    if any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN):
        return False
    if "plugins" in parts[:-1]:
        kind = parts[parts.index("plugins") + 1] if len(parts) > parts.index("plugins") + 2 else ""
        if kind in KEPT_PLUGINS:
            return name in KEPT_PLUGINS[kind]
    if name.endswith(".qm") and "translations" in parts[:-1]:
        return name in {"qtbase_%s.qm" % code for code in studio_languages()}
    return True


def from_this_python(source: str) -> bool:
    """Whether a binary the freeze collected comes from the Python building
    it or the packages installed into it, and not from some other program
    on the PATH, whose licence nobody checked."""
    path = os.path.normcase(os.path.abspath(source))
    homes = {os.path.normcase(os.path.abspath(home)) for home in (sys.prefix, sys.base_prefix)}
    return any(path.startswith(home + os.sep) for home in homes)


def studio_languages() -> List[str]:
    """The languages the Studio has a catalog for: Qt's own catalog is kept
    for each of them and no other."""
    return sorted(path.stem for path in (ROOT / "anno_modding_studio" / "locale").glob("*.po"))


def check_bundle(folder: Path) -> List[str]:
    """What is wrong with a frozen folder: what must not be in it and is,
    and what must be and is not."""
    problems = []
    for path in folder.rglob("*"):
        if path.is_file() and not kept_in_bundle(path.relative_to(folder).as_posix()):
            problems.append("should not be there: %s" % path.relative_to(folder))
    inside = folder / "_internal"
    wanted = [
        folder / (EXE_NAME + ".exe"),
        inside / "anno_modding_studio" / "resources" / "icon.ico",
        inside / "anno_modding_studio" / "resources" / "modio" / "NOTICE.md",
    ]
    wanted += [inside / "anno_modding_studio" / "locale" / ("%s.po" % code)
               for code in studio_languages()]
    wanted += [inside / "PySide6" / "translations" / ("qtbase_%s.qm" % code)
               for code in studio_languages()]
    problems += ["missing: %s" % path.relative_to(folder) for path in wanted if not path.is_file()]
    return problems


# -- the notices -----------------------------------------------------------------
def mit_notices() -> Dict[str, str]:
    """The two MIT notices THIRD-PARTY-NOTICES.md quotes, section 1, by the
    file each goes in: the one copy of each is that file's."""
    text = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)```", text, re.DOTALL)
    loader = [block for block in blocks if "Alexander Guettler" in block]
    imya = [block for block in blocks if "iModYourAnno" in block]
    if len(loader) != 1 or len(imya) != 1:
        raise SystemExit("THIRD-PARTY-NOTICES.md does not quote the two MIT notices once each")
    return {"MIT-anno-mod-loader.txt": loader[0], "MIT-iModYourAnno.txt": imya[0]}


def _licence_file(distribution: str, name: str) -> Path:
    files = importlib.metadata.files(distribution) or []
    for file in files:
        if file.name == name and "licenses" in file.parts:
            return Path(str(file.locate()))
    raise SystemExit("%s has no licenses/%s" % (distribution, name))


def sources_text(of: str) -> str:
    """SOURCES.txt: where the source of each part of the build is, at the
    version built."""
    from lxml import etree
    from PySide6 import __version__ as pyside
    from PySide6.QtCore import qVersion

    url = repo_url()
    qt = qVersion()
    qt_minor = ".".join(qt.split(".")[:2])
    lxml = ".".join(str(n) for n in etree.LXML_VERSION[:3])
    libxml2 = ".".join(str(n) for n in etree.LIBXML_VERSION)
    libxslt = ".".join(str(n) for n in etree.LIBXSLT_VERSION)
    iconv = ".".join(str(n) for n in etree.ICONV_COMPILED_VERSION)
    py = platform.python_version()
    pyinstaller = installed("pyinstaller") or "?"
    title = "Where the source of each part of Anno Modding Studio %s is" % of
    return "\n".join((
        title,
        "=" * len(title),
        "",
        "Each part, at the version in this build. GPLv3 section 6(d): the",
        "Studio's source is offered from the same place as this zip, its",
        "release on GitHub.",
        "",
        "Anno Modding Studio %s (GPL-3.0-or-later)" % of,
        "  %s/tree/v%s" % (url, of),
        "  and as a zip and a tar.gz on %s/releases/tag/v%s" % (url, of),
        "",
        "Qt for Python, PySide6 and shiboken6, %s (LGPL-3.0-only)" % pyside,
        "  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-%s-src/" % pyside,
        "",
        "Qt %s (LGPL-3.0-only)" % qt,
        "  https://download.qt.io/official_releases/qt/%s/%s/single/" % (qt_minor, qt),
        "",
        "lxml %s (BSD-3-Clause), with libxml2 %s, libxslt %s and zlib inside it" % (
            lxml, libxml2, libxslt),
        "  https://pypi.org/project/lxml/%s/#files" % lxml,
        "",
        "GNU libiconv %s, inside lxml (LGPL-2.1-or-later)" % iconv,
        "  https://ftp.gnu.org/gnu/libiconv/libiconv-%s.tar.gz" % iconv,
        "",
        "Python %s (PSF-2.0)" % py,
        "  https://www.python.org/ftp/python/%s/Python-%s.tar.xz" % (py, py),
        "",
        "PyInstaller %s, its bootloader and run-time hooks" % pyinstaller,
        "(GPL-2.0-or-later with the bootloader exception, and Apache-2.0)",
        "  https://pypi.org/project/pyinstaller/%s/#files" % pyinstaller,
        "",
    ))


def notices_qt_version(folder: Path) -> Optional[str]:
    """The Qt major.minor the README of a folder of Qt notices names, or None."""
    readme = folder / "README.txt"
    if not readme.is_file():
        return None
    found = re.search(r"^Qt version: (\d+\.\d+)$", readme.read_text(encoding="utf-8"), re.MULTILINE)
    return found.group(1) if found else None


def stale_qt_notices(folder: Path, qt: str) -> Optional[str]:
    """Why a folder of Qt notices is not for Qt ``qt``, as a sentence, or None.
    Third-party versions and copyright years move with each Qt minor."""
    built = ".".join(qt.split(".")[:2])
    named = notices_qt_version(folder)
    if named == built:
        return None
    return "the notices in %s are for Qt %s, and this build carries Qt %s: save them again " \
        "from https://doc.qt.io/qt-%s/licenses-used-in-qt.html and change the README's " \
        "\"Qt version\" line" % (folder.relative_to(ROOT), named or "an unnamed version", qt, built)


def add_notices(folder: Path, of: str, allow_missing: bool) -> List[str]:
    """Put THIRD-PARTY-NOTICES.md section 5's files at the folder's root.
    What could not be added, as sentences."""
    missing = []
    url = repo_url()
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE.txt")
    shutil.copyfile(ROOT / "THIRD-PARTY-NOTICES.md", folder / "THIRD-PARTY-NOTICES.txt")
    readme = (RELEASE / "README.txt").read_text(encoding="utf-8").format(
        version=of,
        readme_url="%s/blob/v%s/%s" % (url, of, guide()),
        tag_url="%s/tree/v%s" % (url, of),
        releases_url="%s/releases" % url,
    )
    _write_text(folder / "README.txt", readme)
    _write_text(folder / "SOURCES.txt", sources_text(of))

    licenses = folder / "licenses"
    if licenses.exists():
        shutil.rmtree(licenses)
    licenses.mkdir()
    shutil.copyfile(ROOT / "LICENSE", licenses / "GPL-3.0.txt")
    for path in sorted((RELEASE / "licenses").iterdir()):
        if path.is_file():
            shutil.copyfile(path, licenses / path.name)
    for name, text in mit_notices().items():
        _write_text(licenses / name, text)
    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    if python_licence.is_file():
        shutil.copyfile(python_licence, licenses / "Python-LICENSE.txt")
    else:
        missing.append("the build interpreter has no LICENSE.txt at %s" % python_licence)
    shutil.copyfile(_licence_file("lxml", "LICENSE.txt"), licenses / "lxml-LICENSE.txt")
    shutil.copyfile(_licence_file("lxml", "LICENSES.txt"), licenses / "lxml-LICENSES.txt")
    shutil.copyfile(_licence_file("pyinstaller", "COPYING.txt"), licenses / "PyInstaller-COPYING.txt")

    qt = RELEASE / "licenses" / "Qt"
    notices = [path for path in sorted(qt.glob("*")) if path.is_file()] if qt.is_dir() else []
    if notices:
        (licenses / "Qt").mkdir()
        for path in notices:
            shutil.copyfile(path, licenses / "Qt" / path.name)
        from PySide6.QtCore import qVersion

        stale = stale_qt_notices(qt, qVersion())
        if stale:
            missing.append(stale)
    else:
        missing.append(
            "no notices for Qt's third-party code in %s: save them there from "
            "https://doc.qt.io/qt-6/licenses-used-in-qt.html for the Qt version "
            "the lock pins" % qt.relative_to(ROOT)
        )
    if missing and not allow_missing:
        raise SystemExit("not packaged:\n- " + "\n- ".join(missing))
    return missing


def _write_text(path: Path, text: str) -> None:
    # Notepad reads either; CRLF is what Windows' own text files have.
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


# -- the zip --------------------------------------------------------------------
def make_zip(folder: Path, target: Path) -> str:
    """Zip the folder's contents at the zip's root, in a fixed order, and
    write ``<zip>.sha256`` beside it.  The digest."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    # The time every entry says: the commit's, where git gives one, so two
    # builds of one commit differ only where the freeze does.  In UTC, as
    # _commit_time's 1980 floor is: local time made the bytes depend on the
    # machine's time zone, and turned the floor into 1979 west of UTC.
    stamp = time.gmtime(_commit_time())[:6]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(info, path.read_bytes(), compresslevel=9)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    # The form sha256sum checks; Get-FileHash shows the same digest.
    Path(str(target) + ".sha256").write_text(
        "%s *%s\n" % (digest, target.name), encoding="ascii", newline="\n"
    )
    return digest


def _commit_time() -> float:
    try:
        done = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        )
        return max(float(done.stdout.strip()), 315532800.0)  # zip's dates start in 1980
    except (OSError, ValueError, subprocess.CalledProcessError):
        return time.time()


# -- the steps ------------------------------------------------------------------
def check_environment(unlocked: bool) -> List[str]:
    """What is wrong with the Python this runs in, for a release build."""
    problems = []
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        problems.append("the release is built on 64-bit Windows")
    if sys.version_info[:2] < PYTHON:
        problems.append("Python %d.%d or newer builds the release, for OpenSSL 3" % PYTHON)
    if sys.version_info[:2] != NOTICES_PYTHON:
        problems.append(
            "tools/release/licenses/Python-incorporated.txt is Python %d.%d's: "
            "refresh it for this Python, or build with that one" % NOTICES_PYTHON
        )
    if installed("PySide6-Addons") is not None:
        problems.append(
            "PySide6-Addons is installed, and the freeze would take Qt Virtual "
            "Keyboard and Qt PDF from it: build in an environment with only "
            "requirements-release.txt"
        )
    broken = qt_import_error()
    if broken is not None:
        problems.append(
            "PySide6's QtCore does not import here (%s), and the freeze would "
            "leave shiboken6, Qt's plugins and its catalogs out: reinstall "
            "PySide6-Essentials from the lock. Uninstalling PySide6-Addons "
            "deletes PySide6/__init__.py, which every PySide6 wheel installs" % broken
        )
    if not unlocked:
        for name, pinned in locked().items():
            found = installed(name)
            if found != pinned:
                problems.append("%s is %s here, and the lock pins %s" % (name, found, pinned))
    return problems


def qt_import_error() -> Optional[str]:
    """Why PySide6's QtCore does not import in this Python, or None.  The
    freezing tool's Qt hooks import it to find Qt, and without it they
    collect nothing but the modules, warning as they go."""
    try:
        import PySide6.QtCore  # noqa: F401
    except ImportError as error:
        return str(error)
    return None


def freeze(unlocked: bool) -> Path:
    problems = check_environment(unlocked)
    if problems:
        raise SystemExit("not built:\n- " + "\n- ".join(problems))
    folder = DIST / FOLDER_NAME
    if folder.exists():
        shutil.rmtree(folder)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(WORK),
         str(RELEASE / "studio.spec")],
        cwd=ROOT, check=True,
    )
    problems = check_bundle(folder)
    if problems:
        raise SystemExit("the folder is not as it should be:\n- " + "\n- ".join(problems))
    print("frozen: %s (%s MB)" % (folder, _megabytes(_size(folder))))
    return folder


def self_test(folder: Path) -> None:
    """Run the program's own check, and fail with what it said."""
    exe = folder / (EXE_NAME + ".exe")
    if not exe.is_file():
        raise SystemExit("nothing to test: %s is not there" % exe)
    done = subprocess.run(
        [str(exe), "--self-test"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    print(done.stdout, end="")
    if done.returncode != 0:
        print(done.stderr, end="", file=sys.stderr)
        raise SystemExit("the self-test failed (exit code %d)" % done.returncode)


def release_notes(of: str, digest: str) -> str:
    """The draft release's text, which the owner adds what changed to
    before publishing it."""
    url = repo_url()
    return "\n".join((
        "Anno Modding Studio %s, a beta, for Windows." % of,
        "",
        "**To run it**, download `%s`, unzip it into a folder of your own and "
        "start `AnnoModdingStudio.exe`. The build isn't signed, so Windows "
        "SmartScreen warns about it: choose **More info**, then **Run anyway**. "
        "The [user guide](%s/blob/v%s/%s#install-and-run) says more, and "
        "how to update." % (zip_name(of), url, of, guide()),
        "",
        "**SHA-256** of the zip: `%s`" % digest,
        "",
        "**Source:** this version's source is attached below, and is at "
        "%s/tree/v%s. `SOURCES.txt` in the zip says where the source of each "
        "part of the build is." % (url, of),
        "",
        "## What changed",
        "",
        "<!-- Written by hand before this draft is published. -->",
        "",
    ))


def package(folder: Path, allow_missing: bool) -> Path:
    of = version()
    missing = add_notices(folder, of, allow_missing)
    target = DIST / zip_name(of)
    digest = make_zip(folder, target)
    (DIST / "release-notes.md").write_text(release_notes(of, digest), encoding="utf-8")
    print("zip: %s, %s MB, sha256 %s" % (target, _megabytes(target.stat().st_size), digest))
    for sentence in missing:
        print("NOT FOR RELEASE: %s" % sentence)
    return target


def _size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def _megabytes(size: int) -> str:
    return "%.1f" % (size / 1024 / 1024)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", nargs="?", default="all",
                        choices=("all", "freeze", "self-test", "package", "check-tag"))
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""),
                        help="for check-tag: the tag pushed (default: GITHUB_REF_NAME)")
    parser.add_argument("--unlocked", action="store_true",
                        help="build from whatever is installed, for a trial build")
    parser.add_argument("--allow-missing-notices", action="store_true",
                        help="make the zip without every notice, for a trial build")
    args = parser.parse_args(argv)
    folder = DIST / FOLDER_NAME
    if args.step == "check-tag":
        if not tag_matches(args.tag, version()):
            raise SystemExit("the tag %r does not name version %s" % (args.tag, version()))
        print("tag %s is version %s" % (args.tag, version()))
        return 0
    if args.step in ("all", "freeze"):
        folder = freeze(args.unlocked)
    if args.step in ("all", "self-test"):
        self_test(folder)
    if args.step in ("all", "package"):
        package(folder, args.allow_missing_notices)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
