"""Where you have been, for the two tabs that are about one asset.

The asset editor and the reference graph are two views of the same thing: the
asset in front of you.  Each used to keep its own idea of which asset that was
-- open one by searching and the graph went on showing the last one it drew --
and only the graph had a history.  So there is one trail now, shared, and both
tabs walk it: back in either takes both back.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..core.i18n import tr
from . import theme

# The main window's tabs, in order.
# Finding an asset and editing it are one step: search lives inside the editor.
# The file of the mod changes go to comes straight after the editor: the two
# are where a mod is written, by asset and by file.
TAB_SOURCE, TAB_EDIT, TAB_FILE, TAB_REFS, TAB_GENERATE = 0, 1, 2, 3, 4


class AssetTrail(QObject):
    """The assets looked at, in order, with a place in the list -- a browser's."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: List[str] = []
        self._pos = -1

    @property
    def current(self) -> Optional[str]:
        if 0 <= self._pos < len(self._history):
            return self._history[self._pos]
        return None

    def visit(self, guid) -> bool:
        """Go to ``guid``; whether that was anywhere new.

        Anything visited after stepping back is a new branch, so the forward
        trail it replaces goes -- exactly as a browser does it.  Arriving where
        you already are is not a visit, which is what lets a tab reloading its
        own asset, or catching up with the other tab, leave the trail alone.
        """
        guid = str(guid)
        if guid == self.current:
            return False
        del self._history[self._pos + 1:]
        self._history.append(guid)
        self._pos = len(self._history) - 1
        self.changed.emit()
        return True

    def can_go_back(self) -> bool:
        return self._pos > 0

    def can_go_forward(self) -> bool:
        return 0 <= self._pos < len(self._history) - 1

    def back(self) -> Optional[str]:
        """Step back; the asset arrived at, or ``None`` with nowhere to go."""
        if not self.can_go_back():
            return None
        self._pos -= 1
        self.changed.emit()
        return self.current

    def forward(self) -> Optional[str]:
        if not self.can_go_forward():
            return None
        self._pos += 1
        self.changed.emit()
        return self.current

    @property
    def place(self) -> int:
        """Where in the list it stands, to come back to with :meth:`return_to`."""
        return self._pos

    def return_to(self, place: int) -> None:
        """Stand at ``place`` again: a step that was taken and then called
        off -- what was typed for the asset being left was kept -- is a step
        that did not happen, and the buttons have to say so."""
        if place == self._pos or not -1 <= place < len(self._history):
            return
        self._pos = place
        self.changed.emit()

    def clear(self) -> None:
        self._history = []
        self._pos = -1
        self.changed.emit()


def arrow_icon(pointing: int) -> QIcon:
    """A drawn arrow, ``-1`` for back and ``1`` for forward.

    Drawn rather than typed: "←" in the body font is a hairline with a head
    you have to lean in to see, and at arm's length it read as a dash.  This
    is a heavy stroke with a head as tall as the shaft is long.
    """
    icon = QIcon()
    for mode, colour in (
        (QIcon.Mode.Normal, QColor(theme.TEXT)),
        (QIcon.Mode.Active, QColor(theme.TEXT)),
        (QIcon.Mode.Disabled, QColor(theme.TEXT_DIM).darker(150)),
    ):
        pixmap = QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(colour, 2.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        def x(value: float) -> float:
            # Drawn pointing left, mirrored for forward.
            return value if pointing < 0 else 24 - value

        path = QPainterPath(QPointF(x(19.5), 12))
        path.lineTo(QPointF(x(4.5), 12))
        path.moveTo(QPointF(x(11.5), 5))
        path.lineTo(QPointF(x(4.5), 12))
        path.lineTo(QPointF(x(11.5), 19))
        painter.drawPath(path)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


class NavButtons(QWidget):
    """Back and forward over a trail, plus Alt+← and Alt+→ inside ``host``.

    The buttons only ask; the tab they sit on decides what going back means
    for it -- the graph redraws, the editor opens the asset.
    """

    backRequested = Signal()
    forwardRequested = Signal()

    def __init__(self, trail: AssetTrail, host: QWidget = None, parent=None):
        super().__init__(parent)
        self.trail = trail
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.back_button = self._button(
            -1, tr("Back", context="navigation"),
            tr("Back to the asset before  (Alt+←, or the mouse's back button)"),
        )
        self.back_button.clicked.connect(self.backRequested.emit)
        layout.addWidget(self.back_button)
        self.forward_button = self._button(
            1, tr("Forward", context="navigation"),
            tr("Forward again  (Alt+→, or the mouse's forward button)"),
        )
        self.forward_button.clicked.connect(self.forwardRequested.emit)
        layout.addWidget(self.forward_button)

        if host is not None:
            for keys, signal, name in (
                ("Alt+Left", self.backRequested, tr("Back to the asset before")),
                ("Alt+Right", self.forwardRequested, tr("Forward again")),
            ):
                shortcut = QShortcut(QKeySequence(keys), host)
                # What the Keyboard shortcuts dialog lists it as.
                shortcut.setProperty("shortcut_name", name)
                # Only while that tab has the focus: the other tab walks the
                # same trail with its own pair.
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(signal.emit)

        trail.changed.connect(self.sync)
        theme.notifier().changed.connect(self._retheme)
        self.sync()

    def walk(self, trail: AssetTrail) -> None:
        """Stand on ``trail`` from now on, and say what it allows."""
        self.trail.changed.disconnect(self.sync)
        self.trail = trail
        trail.changed.connect(self.sync)
        self.sync()

    def _retheme(self) -> None:
        # The arrows are drawn in the scheme's colours when they are made.
        self.back_button.setIcon(arrow_icon(-1))
        self.forward_button.setIcon(arrow_icon(1))

    def _button(self, pointing: int, name: str, tip: str) -> QPushButton:
        button = QPushButton("")
        button.setObjectName("NavButton")
        button.setIcon(arrow_icon(pointing))
        button.setIconSize(QSize(22, 22))
        button.setFixedWidth(44)
        button.setToolTip(tip)
        button.setAccessibleName(name)
        return button

    def sync(self) -> None:
        self.back_button.setEnabled(self.trail.can_go_back())
        self.forward_button.setEnabled(self.trail.can_go_forward())
