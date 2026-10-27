"""Application bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .core import dataversion, places
from .core.changes import LOADOUT_SUFFIX
from .ui import appearance, help_tip, language, one_studio, theme, widgets
from .ui.main_window import MainWindow, open_settings

APP_ID = "AnnoModdingStudio.Studio"
RESOURCES = Path(__file__).parent / "resources"


def app_icon() -> QIcon:
    icon = QIcon()
    for name in ("icon.ico", "icon.png"):
        path = RESOURCES / name
        if path.exists():
            icon.addFile(str(path))
    return icon


def _claim_taskbar_identity() -> None:
    """Stop Windows filing our window under a generic python.exe entry.

    Without an explicit AppUserModelID the shell groups the window with the
    interpreter and shows the Python icon on the taskbar.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001 - cosmetic only, never worth failing over
        pass


def open_given(window, path: Path) -> None:
    """Open a file the Studio was started with, or a second launch handed
    over: a loadout as a loadout, anything else as the game's assets.xml.
    A loadout opened from Explorer was read as an assets.xml, and said it
    was not whole XML."""
    if path.suffix.lower() == LOADOUT_SUFFIX:
        window.load_loadout(path)
    else:
        window.game_files.open_assets(path)


def start_application(argv) -> QApplication:
    """The application, set up as the Studio has it before any window is
    made.  The self-test (:mod:`.self_test`) starts the same one."""
    # The settings of a Studio that kept them under the roaming app data,
    # moved beside the library before anything reads them.  Here, and not
    # in open_settings, which every test window opens.
    places.adopt_former_settings()
    # Interface size is Qt's scale factor, which Qt reads once, here.
    settings = open_settings()
    scale = appearance.set_scale(appearance.saved_size(settings))
    app = QApplication(argv)
    appearance.clear_scale(scale)
    app.setApplicationName("Anno Modding Studio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AnnoModdingStudio")
    app.setDesktopFileName(APP_ID)
    app.setStyle("Fusion")
    # In the colours and style picked in Settings, before any window exists,
    # so nothing is laid out in one style and then restyled.
    appearance.apply_saved(app, settings)
    appearance.follow_windows(app, settings)
    help_tip.keep_tooltips_open(app)
    theme.keep_focus_marks_for_the_keyboard(app)
    widgets.wheel_passes_by_choices(app)
    # Win+V's clipboard history opens beside the field being typed in.
    widgets.caret_follows_focus(app)
    # Text from mods and mod.io shows as text, never as HTML.
    widgets.external_text_stays_plain(app)
    # And in the language picked in Settings, for the same reason: every
    # sentence is translated as it is made, so nothing may be made before it.
    language.apply(app, settings)
    app.setWindowIcon(app_icon())
    return app


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _claim_taskbar_identity()
    app = start_application(argv)

    # Optional convenience: `python -m anno_modding_studio <assets.xml>`, or
    # a loadout -- which is how Explorer opens one with the Studio.
    given = Path(argv[1]) if len(argv) > 1 and Path(argv[1]).is_file() else None

    # One Studio over one set of folders: a second hands over to the first.
    lock = one_studio.Lock(one_studio.lock_path())
    name = one_studio.server_name(lock.path)
    if not lock.take():
        answered = one_studio.hand_over(name, str(given.resolve()) if given else "")
        if answered is None:
            one_studio.say_already_open()
        elif answered and answered != __version__:
            one_studio.say_another_version_is_open(answered)
        return 0
    # Which version used the folders last, before this one writes itself in:
    # the version this was updated from, if it was.
    previous = dataversion.last_used_by()
    # A build older than the data it finds opens nothing: it could misread
    # what a newer one wrote, and write its misreading back.
    if not one_studio.may_use_data():
        lock.release()
        return 1
    # Its own folder made by now, with the lock in it: where Windows really
    # keeps it is asked before anything is linked into a mods folder.
    if not one_studio.carry_on_if_kept_in_a_package():
        lock.release()
        return 1
    # A save the last Studio left half done is completed or undone before
    # anything reads the files it was saving.
    one_studio.finish_saves()
    one_studio.say_if_beside_is_unused()
    one_studio.say_if_wine_makes_no_junctions(open_settings())
    shown: list = []

    def handed(message: str) -> None:
        # Before the window is up it comes to the front by itself.
        if not shown:
            return
        one_studio.come_forward(shown[0])
        if message and Path(message).is_file():
            open_given(shown[0], Path(message))

    # The application holds the server; the lock is held until the end.
    one_studio.listen(name, handed, app)

    window = MainWindow()
    window.show()
    shown.append(window)
    # Whether a newer Studio is out, asked behind the window once it is up.
    window.studio_release.after_start(previous)

    if given is not None:
        open_given(window, given)

    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
