"""Which theme and interface size are in effect, from the settings and Windows.

``ui.theme`` holds the schemes and builds the style sheet. This module
decides which of them to use:

- the Appearance settings: colour scheme, outlines, density, interface size
- Match Windows, which picks Marble in light mode and Granite in dark
- a Windows contrast theme, which wins over whatever scheme is picked and is
  drawn in its own colours

Interface size is Qt's own scale factor, which Qt reads once when the
application is made. So it is set from the saved value before that, and
changing it takes effect at the next start.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Optional

from . import theme

SCHEME_KEY = "colour_scheme"
OUTLINES_KEY = "outlines"
DENSITY_KEY = "density"
SIZE_KEY = "interface_size"

MATCH_WINDOWS = "windows"
SIZES = (90, 100, 110, 125, 150, 175, 200)
DEFAULT_SIZE = 100
SCALE_VARIABLE = "QT_SCALE_FACTOR"


def _saved(settings, key: str, allowed, default: str) -> str:
    value = str(settings.value(key) or "")
    return value if value in allowed else default


def saved_scheme(settings) -> str:
    """The scheme picked in Settings: a key of ``theme.SCHEMES``, or
    ``MATCH_WINDOWS``. Anything else reads as the default."""
    return _saved(settings, SCHEME_KEY, (MATCH_WINDOWS, *theme.SCHEMES), theme.DEFAULT_SCHEME)


def saved_outlines(settings) -> str:
    return _saved(settings, OUTLINES_KEY, theme.OUTLINES, theme.DEFAULT_OUTLINES)


def saved_density(settings) -> str:
    return _saved(settings, DENSITY_KEY, theme.DENSITIES, theme.DEFAULT_DENSITY)


def saved_size(settings) -> int:
    """The interface size picked in Settings, in percent."""
    try:
        size = int(settings.value(SIZE_KEY) or 0)
    except (TypeError, ValueError):
        size = 0
    return size if size in SIZES else DEFAULT_SIZE


# -- interface size -----------------------------------------------------------
def set_scale(size: int) -> Optional[tuple]:
    """Hand Qt the interface size, before the application is made.

    Returns what ``clear_scale`` needs to put the environment back, or None
    at 100%, where the environment is left alone.
    """
    if size == DEFAULT_SIZE:
        return None
    before = os.environ.get(SCALE_VARIABLE)
    os.environ[SCALE_VARIABLE] = "%g" % (size / 100)
    return (before,)


def clear_scale(token: Optional[tuple]) -> None:
    """Take the scale factor back out of the environment once Qt has read
    it, so the game does not inherit it when Play starts it."""
    if token is None:
        return
    if token[0] is None:
        os.environ.pop(SCALE_VARIABLE, None)
    else:
        os.environ[SCALE_VARIABLE] = token[0]


# -- what Windows is set to ---------------------------------------------------
def windows_is_dark() -> bool:
    """Whether Windows is in dark mode. Dark where it cannot be told."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    try:
        return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
    except AttributeError:  # Qt before 6.5
        return True


def windows_contrast_scheme() -> Optional[theme.Scheme]:
    """The scheme for the Windows contrast theme in use, or None when none is.

    Asked of Windows itself: PySide6 6.6, the oldest version supported when
    this was written, could not report a contrast preference.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class HighContrast(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD),
                        ("lpszDefaultScheme", wintypes.LPWSTR)]

        user32 = ctypes.windll.user32
        info = HighContrast()
        info.cbSize = ctypes.sizeof(HighContrast)
        # SPI_GETHIGHCONTRAST, and HCF_HIGHCONTRASTON in the answer.
        if not user32.SystemParametersInfoW(0x0042, info.cbSize, ctypes.byref(info), 0):
            return None
        if not info.dwFlags & 0x1:
            return None

        def system(index: int) -> str:
            ref = user32.GetSysColor(index)  # 0x00bbggrr
            return "#%02x%02x%02x" % (ref & 0xFF, (ref >> 8) & 0xFF, (ref >> 16) & 0xFF)

        # COLOR_WINDOW 5, WINDOWTEXT 8, GRAYTEXT 17, HIGHLIGHT 13,
        # HIGHLIGHTTEXT 14, BTNFACE 15, HOTLIGHT 26.
        return theme.system_scheme(
            window=system(5), text=system(8), gray=system(17), highlight=system(13),
            highlight_text=system(14), button=system(15), hot=system(26),
        )
    except Exception:  # noqa: BLE001 - never worth failing to start over
        return None


def resolve(choice: str) -> theme.Scheme:
    """The scheme to draw with for a saved choice."""
    contrast = windows_contrast_scheme()
    if contrast is not None:
        return contrast
    if choice == MATCH_WINDOWS:
        return theme.SCHEMES["granite" if windows_is_dark() else "marble"]
    return theme.SCHEMES.get(choice, theme.SCHEMES[theme.DEFAULT_SCHEME])


def apply_saved(app, settings) -> None:
    """Draw the app as the settings and Windows say."""
    to = resolve(saved_scheme(settings))
    outline, dense = saved_outlines(settings), saved_density(settings)
    now = theme.scheme()
    if (app.styleSheet() and to.tokens == now.tokens and to.key == now.key
            and outline == theme.outlines() and dense == theme.density()):
        return
    theme.apply(app, to, outline, dense)


class _Follower:
    """Keeps the app in step when Windows changes mode or contrast theme."""

    def __init__(self, app, settings) -> None:
        from PySide6.QtCore import QEvent, QObject, QTimer

        follower = self

        class Filter(QObject):
            def eventFilter(self, watched, event):
                if watched is app and event.type() in (
                    QEvent.Type.ThemeChange, QEvent.Type.ApplicationPaletteChange,
                ) and not follower.pending:
                    follower.pending = True
                    QTimer.singleShot(0, follower.catch_up)
                return False

        self.app, self.settings, self.pending = app, settings, False
        self.filter = Filter(app)
        app.installEventFilter(self.filter)
        with contextlib.suppress(AttributeError):  # Qt before 6.5
            app.styleHints().colorSchemeChanged.connect(lambda _scheme: self.catch_up())

    def catch_up(self) -> None:
        # ``apply_saved`` does nothing where the answer has not changed, which
        # is what ends the round of palette changes its own restyle sets off.
        try:
            apply_saved(self.app, self.settings)
        finally:
            self.pending = False


def follow_windows(app, settings) -> None:
    """Restyle whenever Windows changes what ``resolve`` would answer."""
    app._appearance_follower = _Follower(app, settings)
