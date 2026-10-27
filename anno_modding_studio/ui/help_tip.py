"""The ``?`` beside a label, and tooltips that stay until you are done.

``HelpTip`` holds the explanation that sits one step away from a label: the
mechanism, why it matters, and the edge cases (docs/standards/design-standards.md).
It differs from a tooltip in the ways a tooltip fails:

- the keyboard reaches it: Tab focuses it, Space, Enter or F1 opens it, and
  Esc closes it
- it never times out, and its text can be selected and copied
- it opens beside the icon, where the focus and a magnifier already are
- a screen reader reads it without opening it, from its accessible
  description

``keep_tooltips_open`` does the smaller job for plain tooltips. Qt hides a
tooltip after ten seconds or so, which is less than the longer ones take to
read. The filter shows the same text with no expiry. A tooltip still hides
when the mouse leaves or a key is pressed. It also wraps the text: Qt shows
a plain tooltip's line as one line, however wide. And it has a row of a list
wait as long for its tooltip as the first one did, where Qt would show it at
once after another.
"""

from __future__ import annotations

import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QTabBar,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import GUIDE as GUIDE_PATH
from .. import source_url
from ..core.i18n import tr, tr_noop
from ..core.keywords import meaning
from . import theme, widgets

# Help that more than one screen shows. Each explanation is written once.
CREATOR_HELP = tr_noop(
    "This name goes in the modinfo of every mod you export. Mods with this "
    "author name are yours, and the Studio edits them directly. Other mods "
    "get a [[patch]]."
)

# The Settings dialog's line under it.
ALIASES_HELP = tr_noop(
    "Other author names you have published mods under, separated by commas. "
    "Mods with one of these names are yours too. An update to one of them "
    "keeps the name it has. New mods get the author name above."
)

# The Settings dialog's Library box and the Start tab's line for it.
LIBRARY_HELP = tr_noop(
    "Mods the Studio installs are kept here: mods from mod.io, mods "
    "from a collection, and the builds Play makes of your own mods. "
    "Your mods folder holds a link to each one, so the folder shows "
    "what the game loads. Avoid a folder under Documents, because a "
    "synced folder can give the game a placeholder file. The drive "
    "the game is on is a good choice."
)

# The Export tab's destination, and Studio folders' row for exported mods.
EXPORT_HELP = tr_noop(
    "Three ways out for the mod picked. In your game: Add to loadout picks "
    "which of your other loadouts have it, and Update builds it again for "
    "them once you have changed it. On "
    "mod.io: signing in, linking the mod to its page, and publishing its "
    "next version. As files: a folder, a zip or the XML, for sharing some "
    "other way. Play tests this loadout on its own: it builds your mods "
    "into the Studio's library and makes this loadout what the game loads."
)

# The Restore dialog, and Studio folders' row for the backups.
BACKUPS_HELP = tr_noop(
    "Every export and every Play first copies the files it is about to "
    "overwrite. Restoring puts those files back as they were: the ModOp "
    "patches and the modinfo beside them, which are the only files the "
    "Studio can overwrite. Other files in the mods folder are not changed. "
    "The newest twenty copies are kept. The first copy of every file is "
    "kept for good, so what it was before the Studio first wrote it is "
    "always here."
)

# Loading from the game files, and Studio folders' row for what it extracts.
EXTRACTED_HELP = tr_noop(
    "The Studio reads the game's .rda archives directly and extracts only "
    "the files it needs. You don't have to extract the game's files "
    "yourself or work out which archive is current. They are kept in the "
    "Studio's own folder, and if they are deleted, loading from the game "
    "files extracts them again."
)

# How a mod is read. The dialog for one mod and the one for a folder of mods
# both offer this.
KEEP_INERT_HELP = tr_noop(
    "A wide replace of a list deletes every entry it did not retype. The base "
    "game leaves the amount off a good that costs nothing, so removing such an "
    "entry changes nothing in the game, and it is not listed among what the "
    "ModOp comes to unless you tick this. The ModOp is exported as written "
    "either way."
)

# About how many characters the panel fits on a line before it wraps.
WRAP_CHARACTERS = 45
# And a plain tooltip, which has no panel to be the width of.
TIP_WRAP_CHARACTERS = 80
HOVER_DELAY_MS = 350
# No tooltip is read for a day. Qt takes the time in milliseconds.
NEVER = 24 * 60 * 60 * 1000

# The HelpTip whose panel is open, if one is: there is one panel at a time.
_SHOWING: list = []

GUIDE = Path(__file__).resolve().parents[2] / GUIDE_PATH


def open_guide() -> None:
    """Open the user guide with whatever the system opens it with, or show
    its folder where that fails.

    A built Studio has no guide beside its package, and Windows nothing
    that opens one, and one installed with pip has none either: they open
    the guide of their own release on GitHub, in the web browser, with its
    pictures."""
    if getattr(sys, "frozen", False) or not GUIDE.is_file():
        QDesktopServices.openUrl(QUrl(source_url(GUIDE_PATH)))
        return
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(GUIDE))):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(GUIDE.parent)))


def _plain(label: str) -> str:
    """A label without its mnemonic mark or trailing colon."""
    return re.sub(r"&(?!&)", "", label).rstrip(": ").replace("&&", "&")


class _Panel(QFrame):
    """The floating panel: wrapped, selectable text, and maybe a link."""

    def __init__(self, text: str, guide: bool, parent: QWidget, legend=None) -> None:
        # A tooltip window never takes the focus, so the icon keeps it and
        # Esc still reaches the icon.
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("HelpPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        plain = widgets.plain_text(text)
        if widgets.terms_in_text(text):
            # Its keywords point at their meaning, as a box's do.
            widgets.show_rich(self.body, widgets.box_markup(text))
            self.body.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            KeywordTips.watch(self.body, text)
        else:
            widgets.show_plain(self.body, text)
        metrics = self.body.fontMetrics()
        wrap = metrics.averageCharWidth() * WRAP_CHARACTERS
        widest = max(metrics.horizontalAdvance(line) for line in plain.split("\n"))
        self.body.setFixedWidth(min(wrap, widest + 2))
        layout.addWidget(self.body)
        if legend:
            # Pictures with their words, a row each; a gap where the legend
            # has one.
            grid = QGridLayout()
            grid.setContentsMargins(0, 2, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(3)
            for row, entry in enumerate(legend):
                if entry is None:
                    grid.setRowMinimumHeight(row, 6)
                    continue
                image, words = entry
                shown = QLabel()
                shown.setPixmap(image)
                grid.addWidget(shown, row, 0, Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(QLabel(words), row, 1)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)
        if guide:
            more = QLabel()
            more.setTextFormat(Qt.TextFormat.RichText)
            more.setText('<a href="guide">%s</a>' % tr("More in the guide"))  # i18n: keep - markup
            more.linkActivated.connect(lambda _href: open_guide())
            layout.addWidget(more)


class HelpTip(QAbstractButton):
    """A circled question mark that opens ``text`` in a floating panel.

    ``label`` is the label it explains, for its accessible name. ``guide``
    ends the panel with a link to the user guide, for a topic that needs
    more than the panel should hold.
    """

    def __init__(self, text: str, label: str = "", guide: bool = False, parent=None,
                 legend=None) -> None:
        super().__init__(parent)
        self._text = text
        self._guide = guide
        # Called as the panel opens, for ``[(picture, words), ...]`` drawn in
        # the colours of the theme in effect then.
        self._legend = legend
        self._panel = None
        self._pinned = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(tr("Help: %s", _plain(label)) if label else tr("Help"))
        self.setAccessibleDescription(described(text))
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.setInterval(HOVER_DELAY_MS)
        self._hover.timeout.connect(self._open_on_hover)
        # While open from a hover: has the mouse left both icon and panel?
        self._watch = QTimer(self)
        self._watch.setInterval(150)
        self._watch.timeout.connect(self._close_if_left)
        self.clicked.connect(self.toggle)

    def help_text(self) -> str:
        return self._text

    def is_open(self) -> bool:
        return self._panel is not None

    def sizeHint(self) -> QSize:
        side = self.fontMetrics().height()
        return QSize(side, side)

    def paintEvent(self, _event) -> None:
        lit = self.hasFocus() or self.underMouse() or self.is_open()
        image = theme.indicators()["help_hover" if lit else "help"]
        painter = QPainter(self)
        side = min(self.width(), self.height())
        QSvgRenderer(image).render(
            painter, QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        )
        painter.end()

    # -- opening and closing ---------------------------------------------------
    def open(self, pinned: bool = True) -> None:
        """Show the panel beside the icon. Pinned, it stays until closed."""
        self._hover.stop()
        self._pinned = pinned or self._pinned
        if self._panel is None:
            # Another's goes first.  Each one opened as the mouse came to
            # it, and down a column of ? one panel lay over the last.
            for other in list(_SHOWING):
                if other is not self:
                    try:
                        other.close_panel()
                    except RuntimeError:
                        _SHOWING.remove(other)  # its widget is already gone
            _SHOWING[:] = [self]
            self._panel = _Panel(
                self._text, self._guide, self, self._legend() if self._legend else None
            )
            self._panel.adjustSize()
            self._panel.move(self._place(self._panel.size()))
            self._panel.show()
            self.update()
        if self._pinned:
            self._watch.stop()
        else:
            self._watch.start()

    def close_panel(self) -> None:
        self._hover.stop()
        self._watch.stop()
        self._pinned = False
        if self in _SHOWING:
            _SHOWING.remove(self)
        if self._panel is not None:
            self._panel.hide()
            self._panel.deleteLater()
            self._panel = None
            self.update()

    def toggle(self) -> None:
        # A click on a panel the hover opened pins it. A second closes it.
        if self.is_open() and self._pinned:
            self.close_panel()
        else:
            self.open(pinned=True)

    def _place(self, size: QSize) -> QPoint:
        """Just below and right of the icon, moved to stay on its screen."""
        at = self.mapToGlobal(QPoint(self.width() + 4, self.height() + 2))
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center()))
        if screen is None:
            return at
        room = screen.availableGeometry()
        if at.x() + size.width() > room.right():
            at.setX(max(room.left(), room.right() - size.width()))
        if at.y() + size.height() > room.bottom():
            at.setY(self.mapToGlobal(QPoint(0, 0)).y() - size.height() - 2)
        return at

    def _open_on_hover(self) -> None:
        if self.underMouse():
            self.open(pinned=False)

    def _close_if_left(self) -> None:
        if self._panel is None or self._pinned:
            return
        at = QCursor.pos()
        icon = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        panel = self._panel.geometry()
        # The gap between them, so crossing it does not close the panel: the
        # icon out to the panel's nearest point.  The rectangle round both
        # was used for this, and with the panel below and right of the icon
        # it held everything below the icon -- the next row's ? among it, so
        # going down a column of them left every panel open.
        centre = icon.center()
        nearest = QPoint(min(max(centre.x(), panel.left()), panel.right()),
                         min(max(centre.y(), panel.top()), panel.bottom()))
        gap = icon.united(QRect(nearest, QSize(1, 1)))
        if not any(area.adjusted(-4, -4, 4, 4).contains(at) for area in (icon, panel, gap)):
            self.close_panel()

    # -- events -----------------------------------------------------------------
    def enterEvent(self, event) -> None:
        if not self.is_open():
            self._hover.start()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.stop()
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F1):
            self.toggle()
        elif key == Qt.Key.Key_Escape and self.is_open():
            self.close_panel()
        else:
            super().keyPressEvent(event)  # Space clicks, as on any button
            return
        event.accept()

    def focusOutEvent(self, event) -> None:
        self.close_panel()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:
        self.close_panel()
        super().hideEvent(event)


def described(text: str) -> str:
    """``text`` as a screen reader is given it: plain words, and after them
    the meaning of each keyword it marks, once each. The keywords' panels
    open from the mouse, and their meaning is read here instead."""
    meanings = []
    for key in widgets.terms_in_text(text):
        said = meaning(key)
        if said and said not in meanings:
            meanings.append(said)
    return "\n\n".join([widgets.plain_text(text), *meanings])


def _term_of(href: str) -> str:
    """The keyword a link of ``widgets.box_markup`` points at, or ""."""
    from urllib.parse import unquote

    return unquote(href[len("term:"):]) if href.startswith("term:") else ""


class KeywordTips(QObject):
    """The meaning of each keyword in a label, in a panel beside it: while
    the mouse rests on the keyword, or pinned there by a click on it, or by
    Enter where the keyboard reaches its link.

    It is the label's ``HelpTip`` for its terms. The panel is the same kind,
    opens after the same delay, stays while the mouse is on the keyword or
    on the panel, and closes with Esc. A screen reader gets the meanings from
    the label's description (:func:`described`).
    """

    @classmethod
    def watch(cls, label: QLabel, text: str) -> KeywordTips:
        """Watch ``label``, showing ``text`` made by ``widgets.box_markup``."""
        tips = getattr(label, "_keyword_tips", None)
        if tips is None:
            tips = cls(label)
            label._keyword_tips = tips
        # A message box's label opens its links in the browser. A keyword's
        # link goes nowhere: it is the key to a meaning.
        label.setOpenExternalLinks(False)
        label.setAccessibleDescription(described(text))
        return tips

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self.label = label
        self.panel: Optional[_Panel] = None
        self.key = ""
        self.pinned = False
        # The keyword the mouse is on, or "".
        self._on = ""
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.setInterval(HOVER_DELAY_MS)
        self._hover.timeout.connect(self._open_hovered)
        self._watch = QTimer(self)
        self._watch.setInterval(150)
        self._watch.timeout.connect(self._close_if_left)
        label.linkHovered.connect(self._hovered)
        label.linkActivated.connect(self._activated)
        label.installEventFilter(self)

    def is_open(self) -> bool:
        return self.panel is not None

    def open(self, key: str, pinned: bool, at: QPoint) -> None:
        """Show what ``key`` means, at ``at`` on the screen, kept till closed
        where ``pinned``."""
        said = meaning(key)
        if not said:
            return
        self.close()
        self.key, self.pinned = key, pinned
        self.panel = _Panel(said, False, self.label)
        self.panel.adjustSize()
        self.panel.move(self._place(at, self.panel.size()))
        self.panel.show()
        if not pinned:
            self._watch.start()

    def close(self) -> None:
        self._hover.stop()
        self._watch.stop()
        self.pinned = False
        if self.panel is not None:
            self.panel.hide()
            self.panel.deleteLater()
            self.panel = None

    def _place(self, at: QPoint, size: QSize) -> QPoint:
        """Below and right of ``at``, moved to stay on its screen -- and above
        it where below would cover a button of the label's window: in a
        message box the buttons are under its text, and the pointer on its
        way to one went into the panel, which then stayed open over it
        (R3-8, hands-on run 3)."""
        place = QPoint(at.x() + 12, at.y() + 16)
        screen = QGuiApplication.screenAt(at)
        room = screen.availableGeometry() if screen is not None else None
        if room is not None and place.x() + size.width() > room.right():
            place.setX(max(room.left(), room.right() - size.width()))
        above = at.y() - size.height() - 4
        off_screen = room is not None and place.y() + size.height() > room.bottom()
        room_above = room is None or above >= room.top()
        if off_screen or (room_above and self._covers_a_button(QRect(place, size))):
            place.setY(above)
        return place

    def _covers_a_button(self, area: QRect) -> bool:
        """Whether ``area``, on the screen, is over a button of the label's
        window."""
        window = self.label.window()
        for button in window.findChildren(QAbstractButton):
            if button.isVisible() and QRect(button.mapToGlobal(QPoint(0, 0)), button.size()).intersects(area):
                return True
        return False

    def _hovered(self, href: str) -> None:
        self._on = _term_of(href)
        if self._on and not (self.panel is not None and self.key == self._on):
            self._hover.start()
        elif not self._on:
            self._hover.stop()

    def _open_hovered(self) -> None:
        if self._on:
            self.open(self._on, pinned=False, at=QCursor.pos())

    def _activated(self, href: str) -> None:
        key = _term_of(href)
        if not key:
            return
        if self.panel is not None and self.key == key and self.pinned:
            self.close()
            return
        # From the keyboard, the mouse may be anywhere: under the label then.
        at = (QCursor.pos() if self.label.underMouse()
              else self.label.mapToGlobal(QPoint(0, self.label.height())))
        self.open(key, pinned=True, at=at)

    def _close_if_left(self) -> None:
        if self.panel is None or self.pinned or self._on:
            return
        if not self.panel.geometry().adjusted(-8, -8, 8, 8).contains(QCursor.pos()):
            self.close()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Leave:
            # Leaving the label leaves its links, which Qt does not always say.
            self._on = ""
            self._hover.stop()
        elif kind == QEvent.Type.Hide:
            self.close()
        elif (kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
              and self.panel is not None):
            self.close()
            return True
        return False


def wrapped(text: str) -> str:
    """A plain tooltip with its long lines broken at a word.

    Qt wraps a tooltip only where it is rich text, so a sentence of forty
    words was a strip across the whole screen.  A line keeps its indent on
    the lines it runs on to, and a word longer than a line -- a path -- is
    left whole.  Rich text loses nothing by it: a line break is a space there.
    """
    lines = []
    for line in text.split("\n"):
        if len(line) <= TIP_WRAP_CHARACTERS:
            lines.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        lines.extend(textwrap.wrap(
            line, TIP_WRAP_CHARACTERS, subsequent_indent=indent,
            break_long_words=False, break_on_hyphens=False,
        ) or [line])
    return "\n".join(lines)


# What ends Qt's showing the next tooltip at once: after one of these it
# waits the whole time again by itself, and so nothing is left waiting here.
_BUSY = (
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease,
    QEvent.Type.Wheel,
    QEvent.Type.KeyPress,
)


class _TooltipFilter(QObject):
    """Shows each plain tooltip itself, wrapped and with no expiry.

    And makes a row of a list or tree wait its turn.  Once Qt has shown one
    tooltip it shows the next at once, for two seconds: right for a toolbar,
    where the next button is the next thing to ask about.  Down a list it is
    a tooltip for every row the mouse crosses.  So a row's tooltip always
    takes the whole wait.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # The cell whose tooltip is up, and when Qt last had one shown: its
        # two seconds of showing the next at once run from there.
        self._shown = None
        self._last = None
        # The cell waiting its turn.
        self._pending = None
        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.timeout.connect(self._show_pending)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind != QEvent.Type.ToolTip:
            if kind in _BUSY and self._last is not None:
                self._last = None
                self._forget()
            return False
        if not isinstance(watched, QWidget):
            return False
        text, rect, index = self._tip(watched, event.pos())
        if not text:
            # Left to Qt: the event goes on to the parent, which may have one.
            self._forget()
            return False
        cell = (watched, QPersistentModelIndex(index)) if index is not None else None
        soon = self._soon()
        self._last = time.monotonic()
        if cell is not None and soon and not (cell == self._shown and QToolTip.isVisible()):
            # Qt asked without waiting.  The one that is up goes, and this
            # one is shown when the mouse has stayed on it as long as on any.
            QToolTip.hideText()
            self._shown = None
            if cell != self._pending:
                self._pending = cell
                self._wait.start(self._delay(QStyle.StyleHint.SH_ToolTip_WakeUpDelay, 700))
            return True
        self._forget()
        self._shown = cell
        QToolTip.showText(event.globalPos(), wrapped(text), watched, rect, NEVER)
        return True

    def _soon(self) -> bool:
        """Whether Qt is still showing tooltips without a wait."""
        if self._last is None:
            return False
        asleep = self._delay(QStyle.StyleHint.SH_ToolTip_FallAsleepDelay, 2000)
        return (time.monotonic() - self._last) * 1000 < asleep + 100

    @staticmethod
    def _delay(hint, otherwise: int) -> int:
        style = QApplication.style()
        return style.styleHint(hint) if style is not None else otherwise

    def _forget(self) -> None:
        self._pending = None
        self._wait.stop()

    def _show_pending(self) -> None:
        """The wait is over: the tooltip, if the mouse is still on its cell."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        widget, index = pending
        try:
            if not index.isValid() or not widget.isVisible():
                return
            at = QCursor.pos()
            text, rect, found = self._tip(widget, widget.mapFromGlobal(at))
        except RuntimeError:
            # The list went away while its row waited.
            return
        if not text or found is None or QPersistentModelIndex(found) != index:
            return
        self._shown = pending
        QToolTip.showText(at, wrapped(text), widget, rect, NEVER)

    @staticmethod
    def _tip(widget: QWidget, pos: QPoint):
        """The tooltip at ``pos`` in ``widget``, the area it is for, and the
        cell of a list or tree it belongs to, or None."""
        view = widget.parent()
        if isinstance(view, QAbstractItemView) and widget is view.viewport():
            # Items keep theirs in the model, where the widget's own is not.
            index = view.indexAt(pos)
            if index.isValid():
                text = index.data(Qt.ItemDataRole.ToolTipRole)
                if text:
                    return str(text), view.visualRect(index), index
        if isinstance(widget, QTabBar):
            tab = widget.tabAt(pos)
            if tab >= 0 and widget.tabToolTip(tab):
                return widget.tabToolTip(tab), widget.tabRect(tab), None
        return widget.toolTip(), QRect(), None


def keep_tooltips_open(app) -> None:
    """From now on, no tooltip in ``app`` times out."""
    if getattr(app, "_tooltip_filter", None) is None:
        app._tooltip_filter = _TooltipFilter(app)
        app.installEventFilter(app._tooltip_filter)
