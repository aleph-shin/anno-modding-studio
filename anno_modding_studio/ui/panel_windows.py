"""Panels in windows of their own, for a second monitor.

Everything the Studio shows was inside one window: the tabs on the left of a
splitter and the Mod changes panel on its right.  So the list of changes cost
the tabs a third of their width wherever it was open, and a second monitor
was no help, since a window stretched over two screens puts its divider
wherever the proportions land.

A panel that can be moved has a window here, held as ``window.panels``.  It
is the one widget throughout: taken out of the main window's layout into a
window of its own, and put back when that window is closed.  Everything the
main window holds on to goes on working, and there is no second copy to
keep in step.  Not a ``QDockWidget``: the Mod changes panel was one, and a
dock keeps whatever width it was given when the window is resized.

The window belongs to the main window, so it stays above it, is minimised
with it and has no entry of its own in the taskbar.  It answers the main
window's shortcuts, by holding the same actions: Ctrl+Z after unticking a
change over there would otherwise do nothing.  A tool window would be handed
them by Qt, and is not used, because maximised it covers the taskbar.

What the main window does about a panel leaving and coming back is its own
business, and it says so when it adds the panel (:meth:`PanelWindows.add`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from .navigation import TAB_EDIT, TAB_FILE, TAB_REFS
from .shortcuts_dialog import NAME as SHORTCUT_NAME

# The panels that can be out, by the key each is remembered under.
CHANGES = "changes"
# The tabs that can be, by their place.
EDITOR = "editor"
FILE = "file"
REFERENCES = "references"
TAB_PANELS = {TAB_EDIT: EDITOR, TAB_FILE: FILE, TAB_REFS: REFERENCES}

# The least a window opens at where its panel had no size to go by.
LEAST = QSize(420, 480)


def on_a_screen(rect: QRect, screens: List[QRect]) -> bool:
    """Whether a window at ``rect`` is somewhere it would be found: its
    middle is on one of ``screens``.  A window remembered on a monitor that
    has since been unplugged is not."""
    return any(screen.contains(rect.center()) for screen in screens)


def _as_text(rect: QRect) -> str:
    return "%d,%d,%d,%d" % (rect.x(), rect.y(), rect.width(), rect.height())


def _as_rect(text) -> Optional[QRect]:
    try:
        x, y, width, height = (int(part) for part in str(text or "").split(","))
    except ValueError:
        return None
    return QRect(x, y, width, height) if width > 0 and height > 0 else None


class PanelWindow(QWidget):
    """A window of a panel's own.  Closing it sends the panel back."""

    closed = Signal()

    def __init__(self, owner: QWidget, title: str):
        super().__init__(owner, Qt.WindowType.Window)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # The owner's keys, answered here as they are there: the ones it
        # answers as a window, and not a key that is one widget's own.
        self.addActions([
            action for action in owner.findChildren(QAction)
            if not action.shortcut().isEmpty()
            and action.shortcutContext() == Qt.ShortcutContext.WindowShortcut
        ])

    def hold(self, panel: QWidget) -> None:
        self.layout().addWidget(panel)
        panel.show()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        event.accept()


class AwayPage(QWidget):
    """What a tab shows while its panel is in a window of its own: where
    the panel is, and the way back.  The tab stays in the strip, since the
    tabs are known by their place and a tab that vanished would take the
    obvious way back with it."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        # Translators: %s is a tab's name, as in "References".
        said = QLabel(tr("%s is in a separate window.", title))
        said.setObjectName("StepHint")
        said.setWordWrap(True)
        layout.addWidget(said)
        self.back_button = QPushButton(tr("Move it back"))
        layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)


class MoveButton(QToolButton):
    """The button on a panel that moves it to a window of its own.

    It is the panel's, and is not shown while the panel is out: the way
    back from there is to close the window, and a button beside the
    window's own that did the same was one thing said twice.

    A tool button, which the theme draws as it draws a push button
    (``QToolButton#PanelMove``).  With no rule of its own it was a darker
    square that did nothing under the mouse, and beside *Edit this asset*
    it did not look like something to click.  A push button itself is not
    the answer: one in the Mod changes panel's top row has the panel laid
    out once more after it is first shown, under a list sized by hand.
    """

    def __init__(self, action: QAction, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelMove")
        self.setText("↗")  # i18n: keep - an arrow, no word
        keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        # Translators: %s is the keys that do it, as in "Shift+F9".
        self.setToolTip(tr("Move to a separate window  (%s)", keys))
        # Translators: %s is a panel's name, as in "Mod changes".
        self.setAccessibleName(tr("Move %s to a separate window", title))
        self.clicked.connect(action.toggle)
        # Hidden and not shown, which would make a window of a button
        # that is not on its panel yet.
        action.toggled.connect(self.setHidden)


@dataclass
class _Seat:
    """One panel that can be out, and how the main window lets it go."""

    key: str
    title: str
    panel: QWidget
    # Called before the panel is taken, and to take it back.
    leave: Callable[[], None]
    enter: Callable[[], None]
    action: QAction
    window: Optional[PanelWindow] = None
    out: bool = False
    # Out when the Studio was last closed, on a screen that is not there
    # now: in the main window until it is moved, and remembered as out.
    waiting: bool = False


class PanelWindows:
    """Which panels are in windows of their own; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        self._seats: Dict[str, _Seat] = {}

    def add(
        self, key: str, title: str, panel: QWidget, shortcut: str,
        leave: Callable[[], None], enter: Callable[[], None],
    ) -> QAction:
        """Make ``panel`` one that can be moved, and give back the action
        that moves it: checked while it is out.

        ``leave`` is called before the panel is taken out of the main
        window, and ``enter`` to put it back in its place there.
        """
        action = QAction(title, self.window)
        action.setCheckable(True)
        action.setShortcut(QKeySequence(shortcut))
        # The menu it is in says the rest; the list of shortcuts does not.
        # Translators: a row in the list of keyboard shortcuts. %s is a
        # panel's name, as in "Mod changes".
        action.setProperty(SHORTCUT_NAME, tr("%s in a separate window", title))
        action.toggled.connect(
            lambda out, key=key: self.take_out(key) if out else self.put_back(key)
        )
        self._seats[key] = _Seat(key, title, panel, leave, enter, action)
        return action

    def button(self, key: str) -> MoveButton:
        seat = self._seats[key]
        return MoveButton(seat.action, seat.title)

    def is_out(self, key: Optional[str]) -> bool:
        seat = self._seats.get(key or "")
        return seat is not None and seat.out

    def window_of(self, key: str) -> Optional[PanelWindow]:
        """The window ``key``'s panel is in, while it is out."""
        seat = self._seats.get(key)
        return seat.window if seat is not None and seat.out else None

    def panel_out(self, key: str) -> Optional[QWidget]:
        """``key``'s panel, while it is out."""
        seat = self._seats.get(key)
        return seat.panel if seat is not None and seat.out else None

    def has_keyboard(self) -> bool:
        """Whether the window in front is a panel's own."""
        active = QApplication.activeWindow()
        return any(seat.out and seat.window is active for seat in self._seats.values())

    # -- out and back ---------------------------------------------------------
    def take_out(self, key: str) -> None:
        seat = self._seats[key]
        if seat.out:
            return
        seat.out, seat.waiting = True, False
        where = self._place(seat)
        seat.leave()
        if seat.window is None:
            seat.window = PanelWindow(self.window, seat.title)
            seat.window.closed.connect(lambda key=key: self.put_back(key))
        seat.window.hold(seat.panel)
        seat.window.setGeometry(where)
        if self._was_maximised(seat):
            seat.window.showMaximized()
        else:
            seat.window.show()
        seat.window.activateWindow()
        # Last, so that whoever hears of it finds the panel where it is.
        seat.action.setChecked(True)

    def put_back(self, key: str) -> None:
        seat = self._seats[key]
        if not seat.out:
            seat.waiting = False
            return
        seat.out = False
        self._remember_place(seat)
        had_keyboard = seat.window.isActiveWindow()
        seat.enter()
        seat.window.hide()
        if had_keyboard:
            self.window.activateWindow()
        seat.action.setChecked(False)

    def bring_forward(self, key: Optional[str], keyboard: bool = True) -> bool:
        """Put the window of ``key``'s panel in front, with the keyboard
        unless it is to stay where it is; False where the panel is not out."""
        window = self.window_of(key or "")
        if window is None:
            return False
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        if keyboard:
            window.activateWindow()
        return True

    # -- where a window goes --------------------------------------------------
    def _screens(self) -> List[QRect]:
        return [screen.availableGeometry() for screen in QGuiApplication.screens()]

    def _remembered(self, seat: _Seat) -> Optional[QRect]:
        return _as_rect(self.window.settings.value("%sWindow" % seat.key))

    def _was_maximised(self, seat: _Seat) -> bool:
        kept = self.window.settings.value("%sWindowMaximised" % seat.key, "")
        return str(kept or "") == "yes" and self._found(seat)

    def _found(self, seat: _Seat) -> bool:
        """Whether the place remembered for the window is on a screen."""
        kept = self._remembered(seat)
        return kept is not None and on_a_screen(kept, self._screens())

    def _place(self, seat: _Seat) -> QRect:
        """Where the window opens: where it was last, while that is on a
        screen that is there, and over where the panel is otherwise."""
        least = seat.panel.minimumSizeHint().expandedTo(LEAST)
        if self._found(seat):
            kept = self._remembered(seat)
            kept.setSize(kept.size().expandedTo(least))
            return kept
        main = self.window
        size = seat.panel.size() if seat.panel.isVisible() else QSize(0, 0)
        size = size.expandedTo(least)
        inside = main.centralWidget() or main
        corner = inside.mapToGlobal(inside.rect().topRight())
        return QRect(corner.x() - size.width(), corner.y(), size.width(), size.height())

    def _remember_place(self, seat: _Seat) -> None:
        window = seat.window
        if window is None:
            return
        maximised = window.isMaximized()
        # Under a maximised window is the size it goes back to.
        rect = window.normalGeometry() if maximised else window.geometry()
        settings = self.window.settings
        settings.setValue("%sWindow" % seat.key, _as_text(rect))
        settings.setValue("%sWindowMaximised" % seat.key, "yes" if maximised else "no")

    # -- between launches -----------------------------------------------------
    def remember(self) -> None:
        """Write down which panels are out, and where."""
        for seat in self._seats.values():
            if seat.out:
                self._remember_place(seat)
            self.window.settings.setValue(
                "%sOut" % seat.key, "yes" if seat.out or seat.waiting else "no"
            )

    def restore(self) -> None:
        """Put out again each panel that was out when the Studio closed.

        Not one whose window was on a screen that has gone: opened on
        whatever screen is left it would land on top of the main window,
        unasked.  It stays in the main window, and is still remembered as
        out, and where, for the day the screen is back.
        """
        for seat in self._seats.values():
            if str(self.window.settings.value("%sOut" % seat.key, "") or "") != "yes":
                continue
            if self._found(seat):
                self.take_out(seat.key)
            else:
                seat.waiting = True

    def put_all_back(self) -> None:
        """Every panel in the main window again, as the Studio closes.
        What :meth:`remember` wrote is left as it is."""
        for seat in self._seats.values():
            waiting = seat.waiting
            self.put_back(seat.key)
            seat.waiting = waiting
