"""The status bar's recent messages: what it said before the line it shows
now, with when, and the button beside it that lists them.

The status line says one thing at a time, and the last to say something
wins.  A sentence about what you did, "Exported to …" or "Undid …", was
often replaced within seconds by one from the background: a reading's
"After that edit, …", what Play would make the loadout, the game's loader
asked.  The change drew the eye, and what had been there was gone.  The
line still says the newest.  :class:`MessageHistory` keeps the ones before
it, :class:`HistoryButton` says how many came since you last looked, and
:class:`RecentMessagesDialog` lists them, each with the links it had.
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import date_text, number, tr, trn
from . import theme
from .widgets import (
    flash_button_feedback,
    show_in_folder,
    show_plain,
    show_rich,
    tab_as_laid_out,
    wrappable_path,
)

# How many messages are kept, the newest first: a few minutes of a busy
# session, when a reading, Play and the loader each say something.
KEPT = 30
# The keys that open the list from anywhere in the window.
SHORTCUT = QKeySequence("Ctrl+Shift+M")
# The button's face: a clock, and the count of messages not yet looked at.
GLYPH = "◷"  # i18n: keep - a clock face, no word

# A link a message offered: its words, its tooltip, and what it does.
Link = Tuple[str, str, Callable[[], None]]


@dataclass
class Said:
    """One message the status line showed: its words, the folder its *Show
    in folder* opened, and when it was last shown.  What ``say`` was given,
    so that whatever it offered beside the message is offered again."""

    text: str
    folder: Optional[Path] = None
    when: float = 0.0
    # The message's own link, ``(label, callback)``: *Show them* after the
    # ModOps a reading moved, say.
    link: Optional[Tuple[str, Callable[[], None]]] = None

    def links(self) -> List[Link]:
        """What the message had beside it in the status bar."""
        links: List[Link] = []
        if self.link is not None:
            label, action = self.link
            links.append((label, label, action))
        if self.folder is not None:
            folder = self.folder
            links.append((tr("Show in folder"), str(folder), lambda: show_in_folder(folder)))
        return links


class MessageHistory:
    """The messages the status line showed, the newest first, and how many
    of them came since the list was last looked at.

    The same words said again, with nothing between, are one message, shown
    at the later time: a reading that says "Every ModOp comes to what it
    did." after each edit filled the list with it.  An empty line, the
    message taken away, is not a message."""

    def __init__(self, kept: int = KEPT, clock: Callable[[], float] = time.time) -> None:
        self._kept = kept
        self._clock = clock
        self._said: List[Said] = []
        self.unseen = 0

    def add(self, text: str, folder=None, link=None) -> None:
        if not text:
            return
        folder = Path(folder) if folder else None
        newest = self._said[0] if self._said else None
        if newest is not None and newest.text == text:
            newest.folder = folder
            newest.link = link
            newest.when = self._clock()
            return
        self._said.insert(0, Said(text, folder, self._clock(), link))
        del self._said[self._kept:]
        self.unseen = min(self.unseen + 1, len(self._said))

    def entries(self) -> List[Said]:
        return list(self._said)

    def seen(self) -> None:
        self.unseen = 0


def shown_time(when: float, now: Optional[float] = None) -> str:
    """When a message was shown, to the second: messages that replace each
    other do so within seconds.  The day as well, for one before today."""
    moment = time.localtime(when)
    clock = time.strftime("%H:%M:%S", moment)
    today = time.localtime(time.time() if now is None else now)
    if moment[:3] == today[:3]:
        return clock
    # Translators: when a status bar message was shown, on a day before
    # today. %(day)s is a date such as "05 Sep", %(time)s a time of day.
    return tr("%(day)s, %(time)s", day=date_text(when, year=False), time=clock)


def copied(said: List[Said]) -> str:
    """The messages as Copy all puts them on the clipboard: each with its
    time, and the folder it named on a line under it."""
    lines = []
    for entry in said:
        lines.append("%s  %s" % (shown_time(entry.when), entry.text))
        if entry.folder is not None:
            lines.append("    %s" % entry.folder)
    return "\n".join(lines) + "\n" if lines else ""


class HistoryButton(QToolButton):
    """The status bar's button for the recent messages, at its left end: a
    clock, with the count of messages that came since the list was last
    opened.  The count is a number beside the clock, in the bar's own
    colour, and the tooltip says it in words."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusHistory")
        self.setAccessibleName(tr("Recent messages"))
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.show_unseen(0)

    def show_unseen(self, count: int) -> None:
        keys = SHORTCUT.toString(QKeySequence.SequenceFormat.NativeText)
        if count:
            face = "%s %s" % (GLYPH, number(count))  # i18n: keep - a glyph and a count
            # Translators: the tooltip of the status bar's clock button.
            # %(keys)s is its shortcut, such as Ctrl+Shift+M.
            tip = trn("Recent messages: one new  (%(keys)s)",
                      "Recent messages: %(n)s new  (%(keys)s)", count, keys=keys)
        else:
            face = GLYPH
            tip = tr("Recent messages  (%s)", keys)
        self.setText(face)
        self.setToolTip(tip)
        self.setAccessibleDescription(tip)


class RecentMessagesDialog(QDialog):
    """The status bar's messages, the newest first: when each was shown,
    all of its words, and the links it had beside it.  ``said`` is
    :meth:`MessageHistory.entries`."""

    def __init__(self, said: List[Said], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Recent messages"))
        self.resize(680, 440)
        self.said = list(said)
        self.links: List[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        if not self.said:
            empty = QLabel(tr(
                "The messages the status bar shows are kept here, the newest first."))
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch(1)
        else:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 0, 8, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            grid.setColumnStretch(1, 1)
            for row, entry in enumerate(self.said):
                self._add_row(grid, row, entry)
            grid.setRowStretch(len(self.said), 1)
            area.setWidget(page)
            layout.addWidget(area, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("&Close"))
        self.copy_button = buttons.addButton(
            tr("Copy &all"), QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.setToolTip(tr("Copies every message in the list, with its time."))
        self.copy_button.setEnabled(bool(self.said))
        self.copy_button.clicked.connect(self.copy_all)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        layout.addWidget(buttons)

    def _add_row(self, grid: QGridLayout, row: int, entry: Said) -> None:
        when = QLabel(shown_time(entry.when))
        theme.tint(when, "text-dim")
        grid.addWidget(when, row, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        words = QLabel()
        words.setWordWrap(True)
        words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # A path in it wraps at its separators, where it would otherwise
        # hold the whole list as wide as itself.
        show_plain(words, wrappable_path(entry.text))
        column.addWidget(words)
        for label, tip, action in entry.links():
            link = QLabel()
            # Written as it is shown, as the status line's link is.
            show_rich(link, '<a href="link">%s</a>' % html.escape(label))
            link.setTextInteractionFlags(
                Qt.TextInteractionFlag.LinksAccessibleByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
            link.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            link.setAccessibleName(label)
            if tip:
                link.setToolTip(tip)
            # The list goes first: what a link shows is in the window
            # behind it, Show it's rows above all, which it kept covered.
            link.linkActivated.connect(lambda _key, action=action: (self.accept(), action()))
            column.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
            self.links.append(link)
        grid.addLayout(column, row, 1)

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(copied(self.said))
        flash_button_feedback(self.copy_button)
