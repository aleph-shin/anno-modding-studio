"""The Studio checking itself, with nobody watching: ``--self-test``.

A built Studio is a different program from the source it was built from.
What the freezing tool left out -- a module only imported inside a
function, the icons, the German, Qt's own catalog, the platform plugin --
shows only when that part is reached, and the reading process is the exe
starting itself again, which no test of the source runs.  So the exe can be
asked to check all of it and say whether it passed, by its exit code:
``AnnoModdingStudio.exe --self-test``, or ``python -m anno_modding_studio
--self-test`` from the source.  The release build and its workflow run it
on every build (``tools/build_release.py``).

It imports every module of the package, finds its resources and both
catalogs, builds the main window off screen, opens and closes the About
box, and starts one reading process and waits for its answer.  Each step
says ``ok`` or what went wrong on standard output, where there is one; the
exit code is 0 only when every step passed.

Nothing it does reaches the user's own data: before anything of the
Studio's is imported, every folder the Studio keeps something in is named
inside a temporary folder, which is removed at the end.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, List

ARGUMENT = "--self-test"

# Each variable that names a folder or file the Studio keeps something in,
# and what it is named in the self-test's own folder.  The same as the test
# suite's (tests/_isolation.py), and one more: the one folder, so that
# nothing a variable here does not name lands in the user's.
_NAMED = {
    "ANNO_STUDIO_FOLDER": "data",
    "ANNO_STUDIO_HOME": "home",
    "ANNO_STUDIO_DOCUMENTS": "documents",
    "ANNO_STUDIO_SETTINGS": "settings.ini",
    "ANNO_STUDIO_CACHE": "cache",
    "ANNO_STUDIO_LIBRARY": "library",
    "ANNO_STUDIO_SECRETS": "secrets.bin",
    "ANNO_STUDIO_MODIO": "modio",
    "ANNO_STUDIO_MODIO_USERS": "modio-users",
}
# And what it does not do: run the game's loader, look through the mods on
# this PC, or read mods again behind the window.
_SWITCHED_OFF = ("ANNO_STUDIO_LOADER", "ANNO_STUDIO_ELSEWHERE", "ANNO_STUDIO_REREAD")

# A sentence every catalog translates, to tell one that loaded from one that
# did not; looked up, not shown, so not marked for the catalogs.
_PROBE = "&Close"

# The smallest game file an index is built from, for the reading's process.
_ASSETS = (
    "<AssetList><Groups><Group><Assets>"
    "<Asset><Template>Product</Template><Values><Standard>"
    "<GUID>1010017</GUID><Name>Timber</Name></Standard></Values></Asset>"
    "</Assets></Group></Groups></AssetList>"
)


def _say(line: str) -> None:
    # A built Studio has no console: there is somewhere to write only when
    # whoever started it is reading.
    if sys.stdout is not None:
        print(line, flush=True)


def _isolate(folder: Path) -> None:
    for name, inside in _NAMED.items():
        os.environ[name] = str(folder / inside)
    for name in _SWITCHED_OFF:
        os.environ[name] = "none"
    # Off screen unless whoever runs it asks for the screen.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _every_module() -> str:
    import importlib
    import pkgutil

    import anno_modding_studio

    names = [
        found.name for found in pkgutil.walk_packages(
            anno_modding_studio.__path__, anno_modding_studio.__name__ + "."
        )
    ]
    for name in names:
        importlib.import_module(name)
    # Fewer would mean the build left some out, which walking it cannot see.
    if len(names) < 100:
        raise AssertionError("only %d modules found" % len(names))
    return "%d modules" % len(names)


def _resources() -> str:
    from .app import RESOURCES
    from .ui import theme

    logo = theme.logo_path()
    wanted = [RESOURCES / "icon.ico", RESOURCES / "icon.png", logo,
              logo.with_name("NOTICE.md")]
    missing = [str(path) for path in wanted if not path.is_file()]
    if missing:
        raise AssertionError("missing: %s" % ", ".join(missing))
    return "icons, mod.io logos and their notice"


def _catalogs() -> str:
    from PySide6.QtCore import QLibraryInfo, QTranslator

    from .core import i18n

    codes = [code for code, _label in i18n.available() if code != i18n.SOURCE]
    if not codes:
        raise AssertionError("no catalog in %s" % i18n.LOCALE_DIR)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    try:
        for code in codes:
            if not i18n.install(code) or i18n.tr(_PROBE) == _PROBE:
                raise AssertionError("the %s catalog translates nothing" % code)
            if not QTranslator().load("qtbase_%s" % code, folder):
                raise AssertionError("no qtbase_%s.qm in %s" % (code, folder))
    finally:
        i18n.install(i18n.SOURCE)
    return "the Studio's and Qt's, for %s" % ", ".join(codes)


def _window_and_about(app) -> Callable[[], str]:
    def step() -> str:
        from . import __version__
        from .ui.about_dialog import AboutDialog
        from .ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        app.processEvents()
        about = AboutDialog(window)
        about.show()
        app.processEvents()
        if __version__ not in about.version_label.text():
            raise AssertionError("the About box does not name %s" % __version__)
        about.close()
        window.close()
        app.processEvents()
        window.deleteLater()
        app.processEvents()
        return "the main window and the About box, %s" % app.platformName()

    return step


def _reading(folder: Path) -> Callable[[], str]:
    def step() -> str:
        from .core import index, reading_process, stale

        source = folder / "assets.xml"
        source.write_text(_ASSETS, encoding="utf-8")
        db = folder / "assets.sqlite"
        index.build_index(source, db)
        opened = index.AssetIndex(db, source)
        try:
            request = reading_process.request_for(stale.Snapshot(), opened)
            answer = reading_process.Reading(request).run()
        finally:
            opened.close()
        if answer != {}:
            raise AssertionError("the reading answered %r" % (answer,))
        return "an answer from %s" % " ".join(reading_process.command())

    return step


def main(argv=None) -> int:
    """Run every step, and 0 only where every one passed."""
    folder = Path(tempfile.mkdtemp(prefix="ams-self-test-"))
    _isolate(folder)
    from . import __version__
    from .app import start_application

    _say("Anno Modding Studio %s self-test%s" % (
        __version__, " (built)" if getattr(sys, "frozen", False) else "",
    ))
    failed: List[str] = []

    def run(name: str, step: Callable[[], str]) -> None:
        try:
            _say("ok    %s: %s" % (name, step()))
        except Exception as exc:  # noqa: BLE001 - each step's failure is the report
            failed.append(name)
            _say("FAIL  %s: %s: %s" % (name, type(exc).__name__, exc))
            if sys.stdout is not None:
                traceback.print_exc(file=sys.stdout)

    try:
        run("imports", _every_module)
        run("resources", _resources)
        run("catalogs", _catalogs)
        app = start_application([sys.executable])
        run("window", _window_and_about(app))
        run("reading", _reading(folder))
        app.quit()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    _say("passed" if not failed else "failed: %s" % ", ".join(failed))
    return 1 if failed else 0
