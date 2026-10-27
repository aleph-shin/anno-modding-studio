"""Keep the suite out of the developer's own files, whichever runner starts it.

This used to live only in conftest.py -- and conftest is a pytest feature. The
docs/development.md documents ``python -m unittest discover -s tests``, which never loads it,
so that command ran the suite against the real Studio home and cache: it wrote
loadout files into Documents/AnnoModdingStudio/loadouts (the "My Anno Mod" and
"Silent Mod" ones) and built indexes into the real cache, where pruning then
treated the developer's own indexes as dead.

Imported first by every test module and by conftest.py, so it applies however
the suite is started. Settings were already redirected per module; the home
and the cache were not.

Each variable is only invented when nobody has set it, and only then is a
temporary folder made for it -- a run that names its own folders leaves no
empty ones behind.

Nobody but this module, that is. A process started by one that imported it
inherits what it invented: a worker of a parallel run (``tools/check.py``)
is started by the pytest that hands it its tests. Taking those as set from
outside, every worker shared one home, one cache and one library, and each
emptied the loadouts the others were saving. So what is invented is named in
``ANNO_STUDIO_SUITE_MADE``, and a process finding a variable named there
invents its own.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MADE = "ANNO_STUDIO_SUITE_MADE"
_made_before = set(filter(None, os.environ.get(MADE, "").split(os.pathsep)))
_made: set = set()


def _own(name: str, prefix: str) -> None:
    """A temporary folder in ``name``, unless one was named from outside."""
    if not os.environ.get(name) or name in _made_before:
        os.environ[name] = tempfile.mkdtemp(prefix=prefix)
        _made.add(name)


# No one folder for everything, whatever the developer keeps beside their
# checkout: each of the Studio's folders is named apart below, and anything
# not named must not land in theirs.
os.environ["ANNO_STUDIO_FOLDER"] = "none"
_own("ANNO_STUDIO_HOME", "ams-home-")
# And Documents itself, where each game keeps a folder of its own with its
# mods in it: the developer's own was read as the game's, by a test that named
# no other, and written to by a script.
_own("ANNO_STUDIO_DOCUMENTS", "ams-documents-")
# Always a settings file of the suite's own, emptied between tests.  One named
# from outside -- a scratch script's, left set in the shell -- used to be taken
# and, being somebody else's, never emptied: state leaked from test to test and
# seven tests failed for it.  It is left alone now, and not written to either.
OUTSIDE_SETTINGS = os.environ.get("ANNO_STUDIO_SETTINGS", "")
os.environ["ANNO_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="ams-settings-"), "settings.ini"
)
_own("ANNO_STUDIO_CACHE", "ams-cache-")
# The game's own mod.io download cache is one of the folders a mods-folder read
# takes in, and on a machine that has used the in-game browser it holds real
# mods; a test reading a folder of its own must not find those in it.
_own("ANNO_STUDIO_MODIO", "ams-modio-")
# And who the game's client is signed in as, which decides what the Studio
# says about unsubscribing.
_own("ANNO_STUDIO_MODIO_USERS", "ams-modio-users-")
# And the local app data, where the Studio keeps what it keeps for itself
# (places.local_home): one of its data folders, holding the version that last
# used it and the lock that says a Studio is open. Windows always sets it, so
# it is always replaced. Left as it was, every run wrote the developer's own
# data-version.json, and a test that writes a version there and reads it back
# read another run's.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ams-local-")
# And the library, where what the Studio installs lives: a test that installs
# a mod must not junction it into a real mods folder from a real library.
_own("ANNO_STUDIO_LIBRARY", "ams-library-")
os.environ[MADE] = os.pathsep.join(sorted(_made | _made_before))
# And the game's own loader, found in an installed VS Code extension: a window
# would otherwise run it in the background after every edit. The tests that
# are about it find it themselves.
os.environ.setdefault("ANNO_STUDIO_LOADER", "none")
# And the scan of every mod on this PC for the GUIDs each creates: a test has
# no business reading the real mods folders, and one set of its own asks.
os.environ.setdefault("ANNO_STUDIO_ELSEWHERE", "none")
# And the reading again of mods a change in what loads has left in doubt: a
# thread started behind a test's back, and words on rows it did not ask for.
# The tests that are about it ask.
os.environ.setdefault("ANNO_STUDIO_REREAD", "none")


def forget_settings() -> None:
    """Empty the suite's settings file, so no test starts where another left off.

    One file serves the whole run, and a window remembers into it as it goes:
    who you are, the folders you last used, recent loadouts, its geometry.  Left
    there, the next test's window starts out knowing them.  The one that showed
    it: a test that makes a patch with no creator set accepts the name the
    Studio suggests -- the Windows user's -- and every window after it believed
    that was who was running the tests, so a later test about a mod of "your
    own" found somebody else's.

    Registered as a test's first cleanup, so it runs after the window is gone.
    """
    from PySide6.QtCore import QSettings

    settings = QSettings(os.environ["ANNO_STUDIO_SETTINGS"], QSettings.Format.IniFormat)
    settings.clear()
    settings.sync()


def forget_loadouts() -> None:
    """Empty the suite's loadouts folder, so no test finds a loadout another
    left there.

    One home serves the whole run, and a window saves into it by name: a
    test that names its loadout "My Own Charcoal" found one of that name
    there after another test that had, was given "My Own Charcoal (2)", and
    passed or failed by which had run first.
    """
    import contextlib
    import shutil

    # And its projects folder, which a saved loadout keeps its mods in.
    for folder in ("loadouts", "projects"):
        kept = os.path.join(os.environ["ANNO_STUDIO_HOME"], folder)
        if os.path.isdir(kept):
            for name in os.listdir(kept):
                path = os.path.join(kept, name)
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        os.remove(path)


def forget_styling() -> None:
    """Leave the suite's one application as a test finds it: in the default
    theme, with no style sheet.

    A test that tries a colour scheme and puts back the scheme there was
    leaves the application styled where it had not been. Text is then 14 px
    where it was the platform's 9 pt, and a test that measures a layout by
    its text passed or failed by whether such a test had run before it.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None or not app.styleSheet():
        return
    from anno_modding_studio.ui import theme

    theme.apply(app, theme.SCHEMES[theme.DEFAULT_SCHEME], theme.DEFAULT_OUTLINES, theme.DEFAULT_DENSITY)
    app.setStyleSheet("")


def forget_keys() -> None:
    """Let go of a Shift or Ctrl a test left held.

    ``QTest.keyClick(widget, key, modifier)`` leaves the application holding
    the modifier after the click, and a list then selects as it does with the
    key down: ``selectRow`` after a Shift+F10 in another test selected
    nothing, and two tests of the mod.io updates failed by what ran before
    them.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QWidget

    if QApplication.instance() is None:
        return
    if QGuiApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier:
        return
    from PySide6.QtTest import QTest

    QTest.keyRelease(QWidget(), Qt.Key.Key_Shift)


# Every modal prompt the Studio opens through a static convenience function.
# Opened for real in a test, each one waits for a click nobody will make, and
# the run hangs without saying where.
_MODAL = {
    "QInputDialog": ("getText", "getItem", "getInt", "getDouble", "getMultiLineText"),
    "QFileDialog": (
        "getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory",
    ),
    "QMessageBox": ("question", "warning", "information", "critical"),
}


def refuse_real_dialogs() -> None:
    """Make a real modal prompt fail the test that opens it, instead of hanging.

    Tests that expect a prompt answer it themselves, by putting their own
    function in its place and restoring what was there -- which is this, so
    the guard comes back after them.  One that is not answered was always
    either a hang or a test quietly leaning on something an earlier test left
    behind: the creator prompt was skipped only because a previous test had
    saved a name, until :func:`forget_settings` stopped settings outliving
    their test.
    """
    from PySide6 import QtWidgets

    for class_name, functions in _MODAL.items():
        owner = getattr(QtWidgets, class_name)
        for function in functions:
            label = "%s.%s" % (class_name, function)

            def refuse(*_args, _label=label, **_kwargs):
                raise AssertionError(
                    "%s opened a real dialog, which waits for a click nobody "
                    "makes; answer it in the test" % _label
                )

            setattr(owner, function, staticmethod(refuse))

    # And the ones built by hand, for the sake of their own buttons: a box made
    # with QMessageBox(parent) waits in exec() exactly as the static ones do.
    # The Studio puts each behind a method of its own so a test can answer it.
    def refuse_exec(*_args, **_kwargs):
        raise AssertionError(
            "QMessageBox.exec opened a real dialog, which waits for a click "
            "nobody makes; answer the method that asks it in the test"
        )

    QtWidgets.QMessageBox.exec = refuse_exec


refuse_real_dialogs()


# The game itself. Play starts it through its store, and asks Windows whether
# it is already running: a test must do neither for real -- one would start
# Anno on the developer's machine, and the other would fail every Play in the
# suite while they happen to be playing. Started addresses are kept, so a
# test can say what would have been started.
STARTED = []


def keep_the_game_out() -> None:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from anno_modding_studio.core import launch

    launch._processes = lambda: []

    def start(address):
        STARTED.append(address)
        return bool(address)

    launch.start = start

    # And the loader's log, which is the real game's on this machine: a test
    # that wants one names its own.
    from anno_modding_studio.core import loaderlog

    loaderlog.log_file = lambda game: None


keep_the_game_out()


# Every window a test makes, gone when the test is. ``close()`` only hides a
# window, and ``deleteLater()`` only queues its deletion for an event loop no
# test runs -- and the suite holds on to every finished test, and so to its
# ``self.window``. Measured before this: 136 MainWindows and 64,144 widgets
# alive by the end of test_onboarding, each one restyled again by every
# ``theme.apply()``, which made one test take 66 seconds and the restyle tests
# 43% of the whole run. Done here rather than in each test's tearDown so a new
# test cannot forget it, and here rather than in conftest.py so unittest gets
# it too.
def _delete_what_the_test_made(before) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget in before:
            continue
        # A worker still running when its parent goes takes the process down
        # with it ("destroyed while thread is still running").
        for thread in widget.findChildren(QThread):
            if thread.isRunning():
                thread.wait(10_000)
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _sweep_windows_after_each_test() -> None:
    import sys
    import unittest

    run = unittest.TestCase.run

    def run_then_sweep(self, result=None):
        app = None
        if "PySide6.QtWidgets" in sys.modules:
            app = sys.modules["PySide6.QtWidgets"].QApplication.instance()
        # What was there before the test is the class's, made in setUpClass,
        # and stays for the next test.
        before = set(app.topLevelWidgets()) if app is not None else set()
        try:
            return run(self, result)
        finally:
            if "PySide6.QtWidgets" in sys.modules:
                _delete_what_the_test_made(before)

    unittest.TestCase.run = run_then_sweep


_sweep_windows_after_each_test()


def box_text(box) -> str:
    """A message box's text as it was written: the markup
    ``widgets.show_box_text`` puts round it taken off again, so a test reads
    the sentence and not the HTML.  A name set apart with ``widgets.named``
    comes back without its marks, and a keyword without its link."""
    import html
    import re

    from PySide6.QtCore import Qt

    text = box.text()
    if box.textFormat() != Qt.TextFormat.RichText:
        return text
    text = re.sub(r"</?(?:span|a)\b[^>]*>", "", text).replace("<br>", "\n").replace("&nbsp;", " ")
    return html.unescape(text)


class answering:
    """Answer every question the Studio asks through ``widgets.ask``.

        with _isolation.answering(True) as asked:
            window.output.export_mine()
        self.assertEqual(asked.titles, ["Export your changes?"])

    ``reply`` is True for the button that does the thing and False for the
    other one, or a function of ``(title, text)`` returning either. A question
    of several actions is answered by a button's text, without its mnemonic.
    What was asked is kept: ``titles``, ``texts`` and ``buttons``.

    A box with one button asks nothing (``widgets.tell``): it is closed with
    that button, and kept apart in ``told``, as ``(title, text)``.
    """

    def __init__(self, reply=True):
        self.reply = reply
        self.titles, self.texts, self.buttons = [], [], []
        self.told = []
        self._patch = None

    @property
    def called(self) -> bool:
        return bool(self.titles)

    def _answer(self, box):
        from PySide6.QtWidgets import QMessageBox

        named = {button.text().replace("&", ""): button for button in box.buttons()}
        text = box_text(box)
        if len(named) == 1:
            self.told.append((box.windowTitle(), text))
            return box.buttons()[0]
        self.titles.append(box.windowTitle())
        self.texts.append(text)
        self.buttons.append(list(named))
        reply = self.reply(box.windowTitle(), text) if callable(self.reply) else self.reply
        if isinstance(reply, str):
            return named[reply]
        role = QMessageBox.ButtonRole.AcceptRole if reply else QMessageBox.ButtonRole.RejectRole
        return next(button for button in box.buttons() if box.buttonRole(button) == role)

    def start(self):
        """Answer from here on, until :meth:`stop`: for a ``setUp``."""
        from unittest import mock

        from anno_modding_studio.ui import widgets

        self._patch = mock.patch.object(widgets, "answer_of", self._answer)
        self._patch.start()
        return self

    def stop(self) -> None:
        self._patch.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
