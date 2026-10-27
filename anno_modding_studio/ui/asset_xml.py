"""The asset editor's XML view: one asset, a tab for each mod that reaches it.

The Fields view answers "what is this asset?".  Somebody who reads the game's
assets.xml and writes XPath by hand asks something else: which ops, of which
mod, made it so.  This view shows that.  A strip of tabs in load order: the
base game, then every loaded mod that reaches the asset.  A mod's tab has two
panes, the ModOps it writes for this asset and the asset as it leaves it, so
the right pane of one tab is what the left pane of the next is applied to.
The last tab's asset is what the game loads.

It decides nothing.  :mod:`core.xmlview` says which mods, with which text;
this lays that out, marks what a later mod writes over, and finds text in it.

The tab of the mod changes go to takes typing, in either pane: its ModOps for
the asset, or the asset as it leaves it.  Typing in one holds the other shut
until Apply or Revert, since two typed panes would be two answers to one
question.  What is typed is handed over whole on **Apply**
(``applyRequested``) and is the mod's from then on only as
:mod:`core.typedops` read it; until then it is text in a box, and nothing
else in the Studio has heard of it.  A pane that is only read sits on
the panel's own surface (``XmlPane`` in the theme) and the one typed in on a
field's; the tab of the mod changes go to carries a pencil where the others
carry a lock; and Apply and Revert are under that tab alone.  Those three, and
the words in each pane's accessible name, tell the one from the others.

The ModOps pane is always typed in: what is typed is what is stored, and
Apply says how many ModOps that was.  The asset pane is typed in wherever
there is an asset: what is typed there is read over the asset as shown where
it cannot be read as the whole of what the mod does.
"""

from __future__ import annotations

import bisect
import re
from typing import Callable, Dict, List, NamedTuple, Optional

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPolygonF,
    QShortcut,
    QTextBlockUserData,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextDocumentLayout,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabBar,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core import declaring, games, guidnames, keptops, typedops, xmlview
from ..core.i18n import join_list, tr, tr_stored, trn
from . import op_marks, theme
from .help_tip import HelpTip
from .xml_view import XmlDialog, XmlHighlighter


def said_of_reverted(count: int) -> str:
    """What is said of ``count`` ops that reach the asset shown and whose
    change to it was reverted, after the sentence that counts the ops
    reaching it.  The same in Fields as here."""
    # Reverting the change does not take the asset out of the op, and the
    # asset shown is made of the changes.
    # Translators: "Mod changes" is the name of the Studio's panel.
    return trn(
        "The change %(n)s of them made to this asset was reverted, but the "
        "ModOp is written all the same and is in the Mod changes panel: the "
        "game still makes the change, and the asset shown is without it.",
        "The changes %(n)s of them made to this asset were reverted, but the "
        "ModOps are written all the same and are in the Mod changes panel: the "
        "game still makes the changes, and the asset shown is without them.",
        count,
    )


def where_reading_stopped(found: typedops.Read) -> str:
    """Why typed ModOps were not read, with the line it is on: they are not
    XML, or text stands beside the elements inside one.  "" for neither.
    The same here, in the file tab and in the pattern dialog, which each
    had a copy of their own that had drifted apart."""
    if found.error:
        return tr(
            "Line %(line)s, column %(column)s: %(error)s",
            line=found.line, column=found.column, error=found.error,
        )
    if found.loose:
        return tr(
            "Line %(line)s: “%(said)s” is text beside the elements inside a "
            "ModOp. It comes to no change, so take it out or make it a comment.",
            line=found.line, said=found.loose,
        )
    return ""


# One level of indentation, as the Studio writes ModOps.
INDENT = "  "
# The strip beside an asset, where its marked lines are marked.
GUTTER = 16
# Keys pressed on the way to another: no use of the pane by themselves.
MODIFIER_KEYS = (
    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
)
# How many assets are named beside one line; the rest are counted.
MOST_NAMED = 3
# How long typing has to stop for before the names are worked out again, in
# milliseconds.
NAMING_PAUSE = 300


class Named(NamedTuple):
    """An asset a number on a line links to: its GUID, what is drawn (with
    the "+ " of one a mod adds), its name alone, and its tooltip.  Or a
    setting the line reads: ``guid`` is then :data:`OPTION` and the name it
    is read by, ``$range``."""

    guid: str
    name: str
    plain: str
    tip: str


# What an entry beside a line that links to a setting starts with: no GUID
# does.
OPTION = "$"


class _Named(QTextBlockUserData):
    """The assets a line's numbers link to, each a :class:`Named`, held by
    the line itself so that they stay with it while the text above is typed
    in; and where on the line each of those numbers is, ``(start,
    length)``, which the highlighter underlines."""

    def __init__(self, names, spans=()):
        super().__init__()
        self.names = names
        self.spans = list(spans)


class _Roomy(QPlainTextDocumentLayout):
    """The layout of a pane's text, as wide as its widest line or as far as
    the names beside a line reach, whichever is further: the pane scrolls
    that far, so that a line longer than the pane can be scrolled to where
    its names are drawn after it and not over it."""

    def __init__(self, document):
        super().__init__(document)
        self.reach = 0.0

    def documentSize(self) -> QSizeF:
        size = super().documentSize()
        return QSizeF(max(size.width(), self.reach), size.height())


# A tag, from its ``<`` to its ``>`` or to the end of what is read: whether
# it closes, its name, and what is between.  A quoted value is read whole, so
# that the ``>`` of an XPath in a Path (``[Amount > 0]``) does not end it.
_TAG = re.compile(
    r"<(/?)((?:[^\W\d]|:)[^\s/<>\"'=]*)"
    r"((?:[^<>\"']|\"[^\"]*(?:\"|\Z)|'[^']*(?:'|\Z))*)(>|\Z)"
)
# And everything else that starts with ``<``: comments, CDATA, ``<?...?>``
# and ``<!...>``, each to its end or to the end of what is read.
_MARKUP = re.compile(
    r"<!--.*?(?:-->|\Z)|<!\[CDATA\[.*?(?:\]\]>|\Z)|<\?.*?(?:\?>|\Z)|<![^>]*(?:>|\Z)|"
    + _TAG.pattern,
    re.S,
)


def _lead(line: str) -> str:
    """The spaces and tabs ``line`` starts with."""
    return line[: len(line) - len(line.lstrip(" \t"))]


def _quote_open(between: str) -> bool:
    """Whether ``between``, what follows a tag's name, ends in a quoted value."""
    quote = ""
    for char in between:
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
    return bool(quote)


def _tag_to(text: str, end: int):
    """The tag that ``text`` has from its last ``<`` to ``end``, ended there
    by ``>`` or still being typed; None where that is no tag, or is in a
    comment or CDATA."""
    start = text.rfind("<", 0, end)
    if start < 0:
        return None
    for opens, closes in (("<!--", "-->"), ("<![CDATA[", "]]>")):
        at = text.rfind(opens, 0, start)
        if at >= 0 and text.find(closes, at + len(opens), start) < 0:
            return None
    match = _TAG.match(text, start, end)
    return match if match and match.end() == end else None


def _unclosed(text: str):
    """The match of the innermost element ``text`` leaves open; None where
    every one is closed, or ``text`` ends inside a tag or a comment."""
    open_: list = []
    for match in _MARKUP.finditer(text):
        if match.end() == len(text) and not match.group(0).endswith(">"):
            return None
        name = match.group(2)
        if name is None or match.group(3).rstrip().endswith("/"):
            continue
        if not match.group(1):
            open_.append(match)
            continue
        # An end tag closes its element and any left open inside it; one
        # with no element open by its name closes nothing.
        for depth in range(len(open_) - 1, -1, -1):
            if open_[depth].group(2) == name:
                del open_[depth:]
                break
    return open_[-1] if open_ else None


class _Gutter(QWidget):
    """The strip down the left of a pane of an asset: a bar beside each line
    the mod changed, and the warning glyph beside one a later mod writes
    over.  It is shape and place that say it, with the shading; the words are
    in its tooltip and under the panes."""

    def __init__(self, pane: XmlPane):
        super().__init__(pane)
        self.pane = pane
        self.setToolTip(tr("A bar marks a line this mod changed."))
        # Asked with the line under the pointer, counted from 0, where the
        # strip says something of each line and not one thing of them all.
        self.tip_for: Optional[Callable[[int], str]] = None

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.tip_for is not None:
            block = self.pane.cursorForPosition(QPoint(0, event.pos().y())).block()
            said = self.tip_for(block.blockNumber())
            if said:
                QToolTip.showText(event.globalPos(), said, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def sizeHint(self) -> QSize:
        return QSize(GUTTER, 0)

    def paintEvent(self, event) -> None:
        pane = self.pane
        marks = pane.marks()
        badges = pane.badges()
        asides = pane.asides()
        striped = pane.has_stripes()
        if not marks and not badges and not asides and not striped:
            return
        painter = QPainter(self)
        block = pane.firstVisibleBlock()
        offset = pane.contentOffset()
        while block.isValid():
            box = pane.blockBoundingGeometry(block).translated(offset)
            if box.top() > event.rect().bottom():
                break
            stripes = pane.stripes_at(block.blockNumber()) if striped else 0
            if stripes and block.isVisible():
                # One bar for each file the line is inside, at the very
                # edge: how deep is said without moving the text in.
                for at in range(min(stripes, 3)):
                    painter.fillRect(
                        QRect(at * 3, int(box.top()), 2, int(box.height()) + 1),
                        QColor(theme.colour("accent")),
                    )
            kind = marks.get(block.blockNumber())
            if kind is not None and block.isVisible():
                top, height = int(box.top()), int(box.height())
                if kind == "over":
                    side = min(height, GUTTER - 2)
                    theme.icon("warn").paint(
                        painter, QRect(1, top + (height - side) // 2, side, side)
                    )
                elif kind == "beyond":
                    painter.fillRect(QRect(0, top, 3, height), QColor(theme.colour("accent")))
                else:
                    painter.fillRect(QRect(5, top, 4, height), QColor(
                        theme.TEXT_DIM if kind == "off" else theme.CHANGED
                    ))
            badge = badges.get(block.blockNumber())
            if badge is not None and block.isVisible():
                top, height = int(box.top()), int(box.height())
                side = min(height, GUTTER - 4)
                badge.paint(painter, QRect(4, top + (height - side) // 2, side, side))
            elif block.blockNumber() in asides and block.isVisible():
                # An arrow out of the pane: what is on this line goes
                # somewhere else.  A shape, so that it is not colour alone.
                top, height = int(box.top()), int(box.height())
                side = min(height, GUTTER - 6)
                left, middle = 4, top + height / 2
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.colour("accent")))
                painter.drawPolygon(QPolygonF([
                    QPointF(left, middle - side / 2), QPointF(left + side, middle),
                    QPointF(left, middle + side / 2),
                ]))
                painter.restore()
            block = block.next()
        painter.end()


class XmlPane(QPlainTextEdit):
    """Some XML to read: laid out as written, never wrapped.

    One that is typed in (:meth:`set_editable`) indents as an editor of XML
    does: Tab and Shift+Tab move the line, or every line selected, a level in
    or out, and Enter starts the next line where this one starts, or a level
    deeper after an opening tag.  And it closes tags: ``>`` that ends an
    opening tag puts its end tag after the caret, and ``</`` is finished
    with the name of the innermost element left open, a line with nothing
    else on it going back to that element's indent.  A ``>`` in a quoted
    value, as an XPath in a Path can have, is only a ``>``.  None of it is
    needed for what is stored, which Apply lays out anew; a pane that is
    half a tag is one Apply cannot read, and these keep it whole.  Tab is
    then no way out of the pane, so Esc is: after it, the next Tab or
    Shift+Tab moves on as it does anywhere else.  A pane that is only read
    never takes Tab at all.

    And a pane come to by Tab is passed through until it is used: the next
    Tab or Shift+Tab moves on, as after Esc.  Somebody tabbing round the
    window went into the pane and stayed there, indenting its first line
    with each Tab (H2-17).  A key that types or moves the caret, or a click,
    is using it, and Tab indents from then on.

    Given a :class:`guidnames.Namer` (:meth:`set_namer`), it says beside
    each line the name of each asset a number on it links to: drawn, dim,
    after the end of the line, so that it is never selected or copied.  A
    name is clicked to open its asset (``assetClicked``), and the line's
    menu has the same, for whoever does not use the mouse.  Worked out
    again once typing stops; a line being typed in has none until then.
    """

    # "Show in Fields view" was asked for at this line, counted from 1.
    fieldRequested = Signal(int)
    # A heading the pane draws was clicked: which, counted from 0 in the
    # order they were given.
    headingClicked = Signal(int)
    # The name of an asset beside a line was clicked, or opened from the
    # menu: its GUID.
    assetClicked = Signal(str)
    # A setting beside a line was clicked, or opened from the menu: whose
    # it is and its name (:func:`core.declaring.resolved`).
    optionClicked = Signal(str, str)

    def __init__(self, to_fields: bool = False, parent=None, gutter: Optional[bool] = None):
        super().__init__(parent)
        self.setObjectName("XmlPane")
        # Laid out to scroll as far as the names beside the lines reach.
        document = QTextDocument(self)
        self._room = _Roomy(document)
        document.setDocumentLayout(self._room)
        self.setDocument(document)
        # An asset's pane has the strip its marked lines are marked in, and
        # so has any other that asks for one.
        self._gutter: Optional[_Gutter] = None
        self._lines: list = []
        if to_fields if gutter is None else gutter:
            self._gutter = _Gutter(self)
            self.setViewportMargins(GUTTER, 0, 0, 0)
            self.updateRequest.connect(self._on_update)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Read-only, and still a place the keyboard can be: the caret is what
        # find moves, and what Shift+arrows select from.
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        # Tab moves on to the next control; a pane nobody types in has no use
        # for it.
        self.setTabChangesFocus(True)
        self._to_fields = to_fields
        self._marked: List[int] = []
        self._changed: List[int] = []
        self._off: List[int] = []
        self._band: List[int] = []
        # Each heading the pane draws, by a cursor on the blank line it is
        # drawn on, so that it stays with what it is over while the text
        # above is typed in; and each badge in the strip, the same way.
        self._headings: List[tuple] = []
        self._badges: List[tuple] = []
        # And what is said at the end of a line, of what starts on it.
        self._asides: List[tuple] = []
        # Asked for what to add to the menu of a heading, by which it is:
        # ``(label, what to do)`` for each, with None for a label that only
        # says something.
        self.heading_menu: Optional[Callable[[int], list]] = None
        self._select_only = self.textInteractionFlags()
        # Esc was pressed: the next Tab leaves the pane and does not indent.
        self._leaving = False
        # What names the assets the numbers link to, and when it next does.
        self._namer: Optional[guidnames.Namer] = None
        # And what says which mod's settings the ``$names`` in the text are:
        # asked, it gives the loadout and the mod whose ops the text is, or
        # None where the text reads none.
        self._options: Optional[Callable[[], Optional[tuple]]] = None
        self._naming = QTimer(self)
        self._naming.setSingleShot(True)
        self._naming.setInterval(NAMING_PAUSE)
        self._naming.timeout.connect(self.name_assets)
        self.document().contentsChange.connect(self._on_contents_change)
        # The name under the pointer, as ``(line, guid)``, and the one a
        # press went down on.
        self._hovered: Optional[tuple] = None
        self._pressed: Optional[str] = None
        theme.monospace(self)
        self._highlighter = XmlHighlighter(self.document())
        # Set while the highlighter is told which numbers are links, which
        # Qt reports as a change to the text, and is none.
        self._underlining = False
        theme.notifier().changed.connect(self._paint_marks)
        self.cursorPositionChanged.connect(self._on_caret)

    def set_editable(self, on: bool) -> None:
        """Make this a pane that is typed in, or one that is read."""
        if on == (not self.isReadOnly()):
            return
        self.setReadOnly(not on)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction if on else self._select_only
        )
        # Typed in, Tab indents (keyPressEvent); only read, it moves on.
        self.setTabChangesFocus(not on)
        self._leaving = False
        self.setAccessibleDescription(
            tr("Once you type or move the cursor here, Tab indents. To leave with "
               "the keyboard, press Esc and then Tab.") if on else ""
        )
        # The surface is the theme's, by whether it is read-only.
        theme.repolish(self)

    def keyPressEvent(self, event) -> None:
        if self.isReadOnly():
            super().keyPressEvent(event)
            return
        key = event.key()
        plain = not event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        )
        if key == Qt.Key.Key_Escape:
            self._leaving = True
            event.accept()
            return
        if key in MODIFIER_KEYS:
            # Shift on its way to Shift+Tab is not the pane being used.
            super().keyPressEvent(event)
            return
        leaving, self._leaving = self._leaving, False
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and plain:
            back = key == Qt.Key.Key_Backtab or bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            if leaving:
                # The scroll area's own: a text box that is typed in says it
                # has no next control, and so the focus never left it.
                QAbstractScrollArea.focusNextPrevChild(self, not back)
            else:
                self._indent(back)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            self._new_line()
            self.ensureCursorVisible()
            return
        # Ctrl alone is a shortcut; with Alt it is AltGr, which types.
        control = event.modifiers() & Qt.KeyboardModifier.ControlModifier and not (
            event.modifiers() & Qt.KeyboardModifier.AltModifier
        )
        if event.text() in (">", "/") and not control and self._close_tag(event.text()):
            self.ensureCursorVisible()
            return
        super().keyPressEvent(event)

    def _new_line(self) -> None:
        """Enter: the next line where this one starts, or a level deeper
        after an opening tag; between ``<A>`` and ``</A>``, a line for what
        goes in them, with ``</A>`` on the next."""
        cursor = self.textCursor()
        line = cursor.block().text()
        indent = _lead(line)
        if cursor.hasSelection():
            cursor.insertText("\n" + indent)
            return
        before = line[: cursor.positionInBlock()].rstrip()
        after = line[cursor.positionInBlock():]
        tag = None
        if before.endswith(">"):
            tag = _tag_to(self.toPlainText(), cursor.block().position() + len(before))
        # Not where the ``>`` is in a quoted value, and the tag goes on.
        if (
            tag is None or not tag.group(4) or tag.group(1)
            or tag.group(3).rstrip().endswith("/")
        ):
            cursor.insertText("\n" + indent)
            return
        # As deep as the line the tag starts on, which a tag written over
        # more than one line need not end on.
        indent = _lead(self.document().findBlock(tag.start()).text())
        name = tag.group(2)
        rest = after.lstrip(" \t")
        cursor.beginEditBlock()
        cursor.insertText("\n" + indent + INDENT)
        if re.match(r"</%s\s*>" % re.escape(name), rest):
            # The spaces before the end tag go with the line it moves to.
            inside = cursor.position()
            cursor.movePosition(
                QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor,
                len(after) - len(rest),
            )
            cursor.insertText("\n" + indent)
            cursor.setPosition(inside)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _close_tag(self, typed: str) -> bool:
        """Type ``typed``, ``>`` or ``/``, with the end tag it calls for:
        ``>`` that ends an opening tag puts ``</Name>`` after the caret, and
        ``/`` after ``<`` closes the innermost element left open, a line of
        its own taking that element's indent.  Each is one step of undo with
        what was typed.  False, and nothing typed, where it calls for none."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        at = cursor.position()
        text = self.toPlainText()
        block = cursor.block()
        line = block.text()
        column = cursor.positionInBlock()
        if typed == ">":
            tag = _tag_to(text, at)
            if (
                tag is None or tag.group(4) or tag.group(1)
                or _quote_open(tag.group(3)) or tag.group(3).endswith("/")
                # Typed into a tag that goes on, or before what is not a tag.
                or line[column:].strip()[:1] not in ("", "<")
            ):
                return False
            cursor.beginEditBlock()
            cursor.insertText(">")
            cursor.insertText("</%s>" % tag.group(2))
            cursor.setPosition(at + 1)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        if not text[:at].endswith("<"):
            return False
        opener = _unclosed(text[: at - 1])
        if opener is None:
            return False
        cursor.beginEditBlock()
        if not line[: column - 1].strip():
            # Alone on its line: as deep as the line the element starts on.
            cursor.setPosition(block.position())
            cursor.setPosition(block.position() + column - 1, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(_lead(self.document().findBlock(opener.start()).text()))
            cursor.movePosition(QTextCursor.MoveOperation.Right)
        cursor.insertText("/%s>" % opener.group(2))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        # Come to by Tab or Shift+Tab: passed through, until it is used.
        self._leaving = not self.isReadOnly() and event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        )

    def focusOutEvent(self, event) -> None:
        self._leaving = False
        super().focusOutEvent(event)

    def mousePressEvent(self, event) -> None:
        # A click in the pane is using it.
        self._leaving = False
        named = self.name_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if named is not None:
            # A name is no part of the text: the caret stays where it was.
            self._pressed = named[0]
            event.accept()
            return
        self._pressed = None
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.name_at(event.position()) is not None:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if not self._names_shown():
            return
        named = self.name_at(event.position())
        line = self.cursorForPosition(event.position().toPoint()).blockNumber()
        hovered = (line, named[0]) if named is not None else None
        if hovered != self._hovered:
            self._hovered = hovered
            self.viewport().update()
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if named is not None else Qt.CursorShape.IBeamCursor
        )

    def leaveEvent(self, event) -> None:
        if self._hovered is not None:
            self._hovered = None
            self.viewport().update()
        super().leaveEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            named = self.name_at(event.pos())
            if named is not None:
                QToolTip.showText(
                    event.globalPos(),
                    tr("%s. Click to open it in the asset editor.", named[1]),
                    self.viewport(),
                )
                return True
        return super().viewportEvent(event)

    def _indent(self, back: bool) -> None:
        """Move the caret's line, or every line the selection touches, one
        level in or out -- as one step of the pane's own undo."""
        cursor = self.textCursor()
        if not cursor.hasSelection() and not back:
            cursor.insertText(INDENT)
            return
        document = self.document()
        first = document.findBlock(cursor.selectionStart())
        last = document.findBlock(cursor.selectionEnd())
        # A selection that ends at the start of a line does not include it.
        if cursor.hasSelection() and last.position() == cursor.selectionEnd() and last != first:
            last = last.previous()
        cursor.beginEditBlock()
        block = first
        while block.isValid():
            at = QTextCursor(block)
            if back:
                text = block.text()
                drop = 1 if text.startswith("\t") else (
                    len(INDENT) if text.startswith(INDENT) else (1 if text.startswith(" ") else 0)
                )
                for _ in range(drop):
                    at.deleteChar()
            elif block.text().strip():
                at.insertText(INDENT)
            if block == last:
                break
            block = block.next()
        cursor.endEditBlock()

    def show_text(self, text: str, marked=(), changed=(), off=(), band=()) -> None:
        """Put ``text`` in the pane, keeping the place it was scrolled to
        where the text is the same length or longer.  ``changed`` are the
        lines the mod changed and ``marked`` those a later mod writes over,
        counted from 1; ``off``, those of something switched off; ``band``,
        those of an op that is about more than the pane is."""
        if text != self.toPlainText():
            bar = self.verticalScrollBar()
            place = bar.value()
            self.setPlainText(text)
            bar.setValue(min(place, bar.maximum()))
        self._marked = list(marked)
        self._changed = list(changed)
        self._off = list(off)
        self._band = list(band)
        self._headings, self._badges, self._asides = [], [], []
        self._paint_marks()
        if self._namer is not None:
            # Text from the mod: the assets there are may have moved with it.
            self._namer.forget()
            self.name_assets()
        elif self._options is not None:
            self.name_assets()
        elif self._room.reach:
            self._make_room()

    def set_headings(self, headings=(), badges=()) -> None:
        """Draw ``headings``, each ``(line, words)``, on the blank lines
        they name, counted from 1: said over what follows, and no part of
        the text.  ``badges`` is ``(line, icon)`` for the strip beside it."""
        document = self.document()
        self._headings = []
        for line, words in headings:
            block = document.findBlockByNumber(line - 1)
            if block.isValid():
                self._headings.append((QTextCursor(block), words))
        self._badges = []
        for line, icon in badges:
            block = document.findBlockByNumber(line - 1)
            if block.isValid():
                self._badges.append((QTextCursor(block), icon))
        self.viewport().update()
        if self._gutter is not None:
            self._gutter.update()

    def set_asides(self, asides=()) -> None:
        """Say ``asides``, each ``(line, words, last)`` with the lines
        counted from 1, of what starts on ``line`` and ends on ``last``: no
        part of the text.  The line it starts on is marked in the strip, and
        the words are at the right-hand end of the one it ends on, which is
        the short one.  Each stays with its lines while the text above is
        typed in."""
        document = self.document()
        self._asides = []
        for line, words, last in asides:
            block = document.findBlockByNumber(line - 1)
            end = document.findBlockByNumber(last - 1)
            if block.isValid():
                self._asides.append((
                    QTextCursor(block), words, QTextCursor(end if end.isValid() else block),
                ))
        self._make_room()
        self.viewport().update()
        if self._gutter is not None:
            self._gutter.update()

    # -- the assets the numbers link to ----------------------------------------
    def set_namer(self, namer: Optional[guidnames.Namer]) -> None:
        """Say beside each line what its numbers link to, as ``namer`` has
        it; or, given None, nothing."""
        self._namer = namer
        if namer is not None:
            # The pointer says where a name can be clicked.
            self.viewport().setMouseTracking(True)
        self.name_assets()

    def set_options(self, whose: Optional[Callable[[], Optional[tuple]]]) -> None:
        """Say beside each line which of a mod's settings it reads, and
        link each: ``whose`` gives ``(loadout, origin)`` of the mod whose
        ops the text is, or None where the text reads none.  Given None,
        nothing."""
        self._options = whose
        if self._options is not None:
            self.viewport().setMouseTracking(True)
        self.name_assets()

    def _names_shown(self) -> bool:
        return self._namer is not None or self._options is not None

    def _settings_on(self, text: str) -> list:
        """``(where, Named)`` for each setting ``text`` reads: each line is
        given each once, by :meth:`name_assets`."""
        if self._options is None:
            return []
        whose = self._options()
        if whose is None:
            return []
        loadout, origin = whose
        found = []
        for one in declaring.named_in(text):
            reference = declaring.resolved(loadout, origin, one.name)
            if reference is None:
                continue
            option = reference.option
            if option is None:
                # Translators: beside a line, a setting it reads that its mod
                # does not declare; clicked, it is declared.
                shown = "\u2699 " + tr("$%s, not declared", reference.name)
                tip = tr("$%s is not declared.", reference.name)
            else:
                shown = "\u2699 " + (option.label or option.name)
                tip = tr("$%(name)s, an option of %(mod)s. Click to open it.",
                         name=reference.name, mod=self._mod_label(loadout, reference.origin))
            found.append((one.start, Named(
                OPTION + reference.origin + "\n" + reference.name, shown, shown[2:], tip,
            )))
        return found

    @staticmethod
    def _mod_label(loadout, origin: str) -> str:
        return loadout.label_of(origin) or tr("your mod")

    def _on_contents_change(self, position: int, _removed: int, added: int) -> None:
        if not self._names_shown() or self._underlining:
            return
        # What was said of a line being typed in may be of a number no
        # longer there: nothing is said of it until typing stops.
        document = self.document()
        block, last = document.findBlock(position), document.findBlock(position + added)
        while block.isValid():
            if block.userData() is not None:
                block.setUserData(None)
            if block == last:
                break
            block = block.next()
        self._naming.start()

    def name_assets(self) -> None:
        """Work out what each line's numbers link to, for the text as it is."""
        self._naming.stop()
        text = self.toPlainText() if self._names_shown() else ""
        by_line: Dict[int, list] = {}
        spans: Dict[int, list] = {}
        starts = [0] + [at + 1 for at, char in enumerate(text) if char == "\n"]

        def line_of(offset: int) -> int:
            return bisect.bisect_right(starts, offset) - 1

        for mention in guidnames.mentions(text) if self._namer is not None else ():
            row = self._namer.row(mention)
            if row is None:
                continue
            line = line_of(mention.start)
            spans.setdefault(line, []).append((mention.start - starts[line], mention.end - mention.start))
            named = by_line.setdefault(line, [])
            if all(one.guid != mention.guid for one in named):
                tip = "%s  ·  %s" % (row.label, row.template) if row.template else row.label
                plain = guidnames.name_of(row)
                named.append(Named(
                    mention.guid, ("+ " + plain) if row.is_new else plain, plain, tip,
                ))
        # A line's settings before its assets: fewer, and what decides
        # whether the line does anything at all.
        settings_first: Dict[int, list] = {}
        for start, one in self._settings_on(text):
            named = settings_first.setdefault(line_of(start), [])
            if all(other.guid != one.guid for other in named):
                named.append(one)
        for line, named in settings_first.items():
            by_line[line] = named + by_line.get(line, [])
        block = self.document().firstBlock()
        while block.isValid():
            number = block.blockNumber()
            found = by_line.get(number)
            was = getattr(block.userData(), "spans", [])
            if found:
                block.setUserData(_Named(found, spans.get(number, ())))
            elif block.userData() is not None:
                block.setUserData(None)
            if was != spans.get(number, []):
                # Which of its numbers are links: underlined again.
                self._underlining = True
                try:
                    self._highlighter.rehighlightBlock(block)
                finally:
                    self._underlining = False
            block = block.next()
        self._make_room()
        self.viewport().update()

    def _make_room(self) -> None:
        """Let the pane scroll far enough right for each line's names, and
        what is said at its end, to be drawn after it: the names with the
        gap they are drawn with kept before and after, and the chip of what
        is said a gap after the line, or after the names, at the edge."""
        metrics = QFontMetricsF(self._names_font())
        gap = max(metrics.horizontalAdvance("  "), self.fontMetrics().horizontalAdvance("  "))
        margin = self.document().documentMargin()
        asides = {end.blockNumber(): words for _cursor, words, end in self._asides}
        reach = 0.0
        block = self.document().firstBlock()
        while block.isValid():
            data = block.userData()
            named = isinstance(data, _Named) and bool(data.names)
            aside = asides.get(block.blockNumber())
            if named or aside:
                # Laid out, as a line is only once it is first drawn.
                self._room.blockBoundingRect(block)
                layout = block.layout()
                need = margin + (layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0.0)
                if named:
                    need += 3 * gap + self._laid(
                        metrics, data.names[:MOST_NAMED], len(data.names)
                    )[1]
                if aside:
                    need += (8 if named else gap + 4) + self._aside_wide(aside)
                reach = max(reach, need + 1)
            block = block.next()
        if reach != self._room.reach:
            self._room.reach = reach
            self._room.documentSizeChanged.emit(self._room.documentSize())

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and (self._names_shown() or self._asides):
            # The names, and what is said, are drawn in the pane's font: as
            # wide as it says.
            self._make_room()

    def named_assets(self) -> dict:
        """What is said beside each line, by line from 0: ``(guid, name)``
        for each asset."""
        found = {}
        block = self.document().firstBlock()
        while block.isValid():
            data = block.userData()
            if isinstance(data, _Named):
                found[block.blockNumber()] = [(one.guid, one.name) for one in data.names]
            block = block.next()
        return found

    def _names_font(self):
        font = self.font()
        font.setItalic(True)
        return font

    def _aside_wide(self, aside: str) -> float:
        """How wide the chip ``aside`` is drawn on is."""
        indent = self.fontMetrics().horizontalAdvance("  ")
        return QFontMetricsF(self._names_font()).horizontalAdvance(aside) + 2 * indent

    def _aside_chip(self, block, box: QRectF, aside: str) -> Optional[QRectF]:
        """The chip ``aside`` is drawn on at the end of ``block``, or None
        where it is not drawn.

        At the right edge of the pane, where it is seen wherever the pane is
        scrolled to, and after the end of the line where that is in view.
        Over the end of the line where that is out of view, or too near the
        edge for the chip, so not while the caret is on it: the line being
        typed is read whole.  The pane scrolls far enough (:class:`_Roomy`)
        for the chip to be after the line."""
        indent = self.fontMetrics().horizontalAdvance("  ")
        wide = self._aside_wide(aside)
        left = self.viewport().width() - wide - 4
        layout = block.layout()
        written = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0.0
        ends = box.left() + self.document().documentMargin() + written
        if ends <= left - indent or block != self.textCursor().block():
            return QRectF(left, box.top(), wide, box.height())
        return None

    def _laid(self, metrics: QFontMetricsF, names: list, total: int) -> tuple:
        """``(more, wide)`` for ``names`` of the ``total`` a line has drawn
        beside it: what counts the rest, and how wide it all is."""
        rest = total - len(names)
        # Translators: after the names of some assets beside a line, how
        # many more its numbers link to.
        more = trn("+%(n)s more", "+%(n)s more", rest) if rest else ""
        dot = metrics.horizontalAdvance(" · ")
        wide = sum(metrics.horizontalAdvance(one.name) for one in names) \
            + dot * max(0, len(names) - 1)
        if more:
            wide += (dot if names else 0) + metrics.horizontalAdvance(more)
        return more, wide

    def _name_boxes(self, block, box: QRectF) -> tuple:
        """``(boxes, chip)`` for the names drawn beside ``block``: ``(where,
        guid, name, tip)`` for each, with ``guid`` None for the count of
        those there was no room for; and the chip they are drawn on, or None.

        After the end of the line, where that is in view: as many of them
        as there is room for there, and never over the line.  A ModOp's line
        is often longer than the pane is wide, and its GUIDs are the ones
        most worth naming: where its end is out of view, or too near the
        edge for one name after it, they are at the pane's right-hand edge,
        on a chip over the line, as what is said of an op that goes
        elsewhere is -- and not while the caret is on the line, which is
        read whole while it is typed in.  The pane scrolls far enough
        (:class:`_Roomy`) for every line's names to be after it."""
        data = block.userData()
        if not isinstance(data, _Named) or not data.names or not block.isVisible():
            return [], None
        metrics = QFontMetricsF(self._names_font())
        layout = block.layout()
        written = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0.0
        gap = metrics.horizontalAdvance("  ")
        dot = metrics.horizontalAdvance(" · ")
        after = box.left() + self.document().documentMargin() + written + 2 * gap
        # Short of what is said at the line's end, where there is that.
        aside = next((words for _cursor, words, end in self._asides if end.block() == block), "")
        said = self._aside_chip(block, box, aside) if aside else None
        edge = said.left() - 4 if said is not None else self.viewport().width()
        right = edge - gap
        names = list(data.names[:MOST_NAMED])

        def laid(names: list) -> tuple:
            return self._laid(metrics, names, len(data.names))

        more, wide = laid(names)
        chip = None
        left = after
        if after + wide > right and block != self.textCursor().block():
            # The line ends in view: the names after it that there is room
            # for, the rest counted.
            fitted = list(names)
            while fitted and after + laid(fitted)[1] > right:
                fitted.pop()
            if fitted:
                names = fitted
                more, wide = laid(names)
        if after + wide > right and block != self.textCursor().block():
            # Pinned, over two thirds of the pane at most: what it covers
            # is the end of a line that runs past the pane, or so near its
            # edge that no name is seen after it.
            room = edge - max(gap, self.viewport().width() / 3)
            while names and wide + 2 * gap > room:
                names.pop()
                more, wide = laid(names)
            if wide + 2 * gap > room:
                return [], None
            left = edge - wide - gap
            # Up to the chip of what is said, where that is: no sliver of
            # the line between the two.
            chip = QRectF(
                left - gap, box.top(),
                wide + 2 * gap + (said.left() - edge if said is not None else 0), box.height(),
            )
        boxes = []
        for one in names:
            wide = metrics.horizontalAdvance(one.name)
            boxes.append((QRectF(left, box.top(), wide, box.height()), one.guid, one.name, one.tip))
            left += wide + dot
        if more:
            boxes.append((
                QRectF(left, box.top(), metrics.horizontalAdvance(more), box.height()),
                None, more, "",
            ))
        return boxes, chip

    def name_at(self, point) -> Optional[tuple]:
        """``(guid, tip)`` of the name drawn at ``point`` in the viewport."""
        if not self._names_shown():
            return None
        block = self.cursorForPosition(QPoint(0, int(point.y()))).block()
        if not block.isValid():
            return None
        box = self.blockBoundingGeometry(block).translated(self.contentOffset())
        if not box.top() <= point.y() <= box.bottom():
            return None
        for where, guid, _name, tip in self._name_boxes(block, box)[0]:
            if guid is not None and where.contains(QPointF(point)):
                return guid, tip
        return None

    def _paint_names(self, painter: QPainter, event) -> None:
        font = self._names_font()
        under = self._names_font()
        under.setUnderline(True)
        dot = QFontMetricsF(font).horizontalAdvance(" · ")
        for block, box in self.lines_in_view(event):
            boxes, chip = self._name_boxes(block, box)
            if chip is not None:
                painter.fillRect(chip, QColor(theme.RAISED))
            for at, (where, guid, name, _tip) in enumerate(boxes):
                painter.setFont(font)
                painter.setPen(QColor(theme.TEXT_DIM))
                if at:
                    painter.drawText(
                        QRectF(where.left() - dot, where.top(), dot, where.height()),
                        Qt.AlignmentFlag.AlignCenter, "·",
                    )
                if guid is not None and guid.startswith(OPTION):
                    # A setting, in the colour it has in the text.
                    painter.setPen(QColor(theme.SYN_OPTION))
                if guid is not None and self._hovered == (block.blockNumber(), guid):
                    # Underlined under the pointer: a link, by shape and not
                    # by colour alone.
                    painter.setFont(under)
                    painter.setPen(QColor(theme.colour("accent")))
                painter.drawText(where, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)

    def _names_on(self, block) -> list:
        data = block.userData() if block.isValid() else None
        return list(data.names) if isinstance(data, _Named) else []

    def _on_caret(self) -> None:
        # What is said at the end of a line makes way for the caret, and so
        # do the names held at the pane's edge.
        if self._asides or self._names_shown():
            self.viewport().update()

    def has_stripes(self) -> bool:
        """Whether any line has bars at the edge of the strip."""
        return False

    def stripes_at(self, line: int) -> int:
        """How many bars the strip draws at its edge beside ``line``,
        counted from 0."""
        return 0

    def asides(self) -> dict:
        """What is said of what starts on each line, by line from 0."""
        return {cursor.blockNumber(): words for cursor, words, _end in self._asides}

    def type_over(self, text: str) -> None:
        """Make the pane say ``text`` as though it had been typed: one step
        of the pane's own undo, which changes only the part that differs, so
        that what is drawn over the rest stays with it."""
        have = self.toPlainText()
        if have == text:
            return
        most = min(len(have), len(text))
        head = 0
        while head < most and have[head] == text[head]:
            head += 1
        tail = 0
        while tail < most - head and have[len(have) - 1 - tail] == text[len(text) - 1 - tail]:
            tail += 1

        def place(at: int) -> int:
            # The document counts as Qt does, in units of UTF-16.
            return len(have[:at].encode("utf-16-le")) // 2

        cursor = QTextCursor(self.document())
        cursor.setPosition(place(head))
        cursor.setPosition(place(len(have) - tail), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text[head:len(text) - tail])

    def headings(self) -> List[tuple]:
        """``(line, words)`` for each heading as it stands now, the line
        counted from 0: only where that line is still blank."""
        return [
            (cursor.blockNumber(), words) for cursor, words in self._headings
            if not cursor.block().text().strip()
        ]

    def badges(self) -> dict:
        """The badges in the strip as they stand now, by line from 0."""
        return {cursor.blockNumber(): icon for cursor, icon in self._badges}

    def heading_at(self, line: int) -> Optional[int]:
        """Which heading is drawn on ``line``, counted from 0: its place
        among those given, or None."""
        for at, (cursor, _words) in enumerate(self._headings):
            if cursor.blockNumber() == line and not cursor.block().text().strip():
                return at
        return None

    def lines_in_view(self, event):
        """``(block, where it is drawn)`` for each line a paint is of."""
        offset = self.contentOffset()
        block = self.firstVisibleBlock()
        while block.isValid():
            box = self.blockBoundingGeometry(block).translated(offset)
            if box.top() > event.rect().bottom():
                break
            if block.isVisible():
                yield block, box
            block = block.next()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        drawn = dict(self.headings())
        asides = {end.blockNumber(): words for _cursor, words, end in self._asides}
        if not drawn and not asides and not self._names_shown():
            return
        painter = QPainter(self.viewport())
        if self._names_shown():
            self._paint_names(painter, event)
        painter.setPen(QColor(theme.TEXT_DIM))
        font = self.font()
        font.setItalic(True)
        painter.setFont(font)
        indent = self.fontMetrics().horizontalAdvance("  ")
        for block, box in self.lines_in_view(event):
            words = drawn.get(block.blockNumber())
            if words and block.isVisible():
                painter.drawText(
                    QRectF(box.left() + indent + self.document().documentMargin(), box.top(),
                           max(0.0, box.width() - indent), box.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, words,
                )
            aside = asides.get(block.blockNumber())
            chip = self._aside_chip(block, box, aside) if aside and block.isVisible() else None
            if chip is not None:
                painter.fillRect(chip, QColor(theme.wash("accent", 0.14) or theme.RAISED))
                painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, aside)
        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        pressed, self._pressed = self._pressed, None
        if pressed is not None:
            named = self.name_at(event.position())
            if named is not None and named[0] == pressed:
                self._open_named(pressed)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self.textCursor().hasSelection():
            return
        at = self.heading_at(
            self.cursorForPosition(event.position().toPoint()).blockNumber()
        )
        if at is not None:
            self.headingClicked.emit(at)

    def _paint_marks(self) -> None:
        """Shade the marked lines, full width, and hold each by a cursor so
        its mark stays with it while the text above is typed in.  The words
        that say what the marks mean are under the panes: this says where."""
        selections = []
        self._lines = []
        over = set(self._marked)
        off = set(self._off) - over - set(self._changed)
        band = set(self._band) - over - off - set(self._changed)
        tinted = QColor(theme.wash("accent", 0.14) or theme.RAISED)
        for line in sorted(over | set(self._changed) | off | band):
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(tinted if line in band else QColor(theme.RAISED))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            selections.append(selection)
            self._lines.append((
                QTextCursor(block),
                "over" if line in over else "off" if line in off
                else "beyond" if line in band else "changed",
            ))
        self.setExtraSelections(selections)
        if self._gutter is not None:
            self._gutter.update()

    def marks(self) -> dict:
        """The marked lines as they stand now: line, counted from 0, to
        "changed", "over", "off" or "beyond"."""
        return {cursor.blockNumber(): kind for cursor, kind in self._lines}

    def say_of_marks(self, said: str, tip_for: Optional[Callable[[int], str]] = None) -> None:
        """What the strip's marks mean in this pane: in a sentence, and where
        each line has something of its own to say, asked of ``tip_for`` with
        the line counted from 0."""
        if self._gutter is not None:
            self._gutter.setToolTip(said)
            self._gutter.tip_for = tip_for

    def _on_update(self, rect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), GUTTER, rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._gutter is not None:
            inside = self.contentsRect()
            self._gutter.setGeometry(inside.left(), inside.top(), GUTTER, inside.height())

    def bring_marks_into_view(self) -> None:
        """Scroll to the first marked line where none of them can be seen.
        The caret stays where it is."""
        lines = sorted(self.marks())
        if not lines:
            return
        bar = self.verticalScrollBar()
        first, page = bar.value(), max(1, bar.pageStep())
        if any(first <= line < first + page for line in lines):
            return
        bar.setValue(max(0, lines[0] - page // 3))

    def go_to_line(self, line: int, top: bool = False) -> None:
        """Select ``line``, counted from 1, and bring it into view: in the
        middle of the pane, or near its top where ``top`` is, for the first
        line of a block whose rest is below it.

        Selected from its end back to its start, so the caret is where the
        line starts.  With the caret at the end, a long line was scrolled
        across to its last character, past the tags that show what it is in.
        """
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.setPosition(block.position(), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        if top:
            # Lines do not wrap here, so the bar counts lines.  Two above it
            # say what the block is under.
            self.verticalScrollBar().setValue(max(0, block.blockNumber() - 2))
        else:
            self.centerCursor()

    def contextMenuEvent(self, event) -> None:
        menu = self.menu_at(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _open_named(self, guid: str) -> None:
        """What an entry beside a line links to, opened: an asset, or a
        mod's setting."""
        if guid.startswith(OPTION):
            origin, _line, name = guid[len(OPTION):].partition("\n")
            self.optionClicked.emit(origin, name)
        else:
            self.assetClicked.emit(guid)

    def menu_at(self, pos) -> QMenu:
        """The menu for a click at ``pos`` in the viewport, or for the menu
        key with the caret there."""
        menu = self.createStandardContextMenu()
        # What the line's numbers link to, as beside it: the way to open one
        # with the keyboard, and the name for whoever cannot see it drawn.
        named = self._names_on(self.cursorForPosition(pos).block())
        first = menu.actions()[0] if menu.actions() else None
        for one in named:
            if one.guid.startswith(OPTION):
                # Translators: %s is the label of a mod's option a line reads.
                action = QAction(tr("Option %s\u2026", one.plain), menu)
            else:
                # Translators: %s is the name of an asset a number links to.
                action = QAction(tr("Open %s", one.plain), menu)
            action.triggered.connect(lambda _on=False, guid=one.guid: self._open_named(guid))
            menu.insertAction(first, action)
        if named and first is not None:
            menu.insertSeparator(first)
        at = self.heading_at(self.cursorForPosition(pos).blockNumber())
        if at is not None and self.heading_menu is not None:
            menu.addSeparator()
            for label, do in self.heading_menu(at):
                action = menu.addAction(label, do) if do is not None else menu.addAction(label)
                action.setEnabled(do is not None)
        if self._to_fields and self.toPlainText():
            line = self.cursorForPosition(pos).blockNumber() + 1
            menu.addSeparator()
            menu.addAction(
                tr("Show in Fields view"), lambda: self.fieldRequested.emit(line)
            )
        return menu


class FindBar(QWidget):
    """Find text in a pane: a box, the next and the one before, and a count."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pane: Optional[XmlPane] = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.box = QLineEdit()
        self.box.setPlaceholderText(tr("Find in this pane…"))
        self.box.setAccessibleName(tr("Find in this pane"))
        self.box.setClearButtonEnabled(True)
        self.box.textChanged.connect(lambda _text: self.find(again=False))
        self.box.returnPressed.connect(lambda: self.find())
        row.addWidget(self.box, 1)
        self.count = QLabel("")
        self.count.setObjectName("StepHint")
        row.addWidget(self.count)
        # Translators: goes to the match before this one.
        self.previous_button = QPushButton(tr("Previous"))
        self.previous_button.clicked.connect(lambda: self.find(backward=True))
        row.addWidget(self.previous_button)
        # Translators: goes to the next match.
        self.next_button = QPushButton(tr("Next"))
        self.next_button.clicked.connect(lambda: self.find())
        row.addWidget(self.next_button)
        self.close_button = QPushButton(tr("Close"))
        self.close_button.clicked.connect(self.close_bar)
        row.addWidget(self.close_button)
        self.setVisible(False)

    def open_on(self, pane: XmlPane) -> None:
        self.pane = pane
        chosen = pane.textCursor().selectedText()
        # One line of it: a selection across lines comes with Qt's
        # paragraph separators in it, and no line holds one.
        if chosen and chr(0x2029) not in chosen:
            self.box.setText(chosen)
        self.setVisible(True)
        self.box.setFocus()
        self.box.selectAll()
        self.find(again=False)

    def close_bar(self) -> None:
        self.setVisible(False)
        if self.pane is not None:
            self.pane.setFocus()
        self.closed.emit()

    def find(self, backward: bool = False, again: bool = True) -> bool:
        """Go to the next match, or the one before; around the end.  With
        ``again`` off the match the caret is on counts, which is what typing
        one more letter of it wants."""
        needle = self.box.text()
        pane = self.pane
        if pane is None or not needle:
            self.count.setText("")
            return False
        total = pane.toPlainText().lower().count(needle.lower())
        self.count.setText(
            trn("%(n)s match", "%(n)s matches", total) if total else tr("No matches")
        )
        if not total:
            return False
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if not again:
            cursor = pane.textCursor()
            cursor.setPosition(cursor.selectionStart())
            pane.setTextCursor(cursor)
        if pane.find(needle, flags):
            return True
        # Around the end, as every editor's find does.
        cursor = pane.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
        )
        pane.setTextCursor(cursor)
        return pane.find(needle, flags)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(event)


# The two panes that are typed in, as applyRequested names them.
MODOPS, ASSET = "modops", "asset"


class AssetXmlView(QWidget):
    """One asset as XML: a tab for the base game and for each mod reaching it."""

    # Show this ModOp path in the Fields view.
    fieldRequested = Signal(str)
    # Apply was pressed: which pane was typed in (MODOPS or ASSET), and its
    # text, whole.
    applyRequested = Signal(str, str)
    # From the heading over an op that reaches beyond the asset: go to it in
    # its mod's file (the mod, "op" or "unread", and which), or open one of
    # the assets it reaches.
    fileRequested = Signal(str, str, object)
    assetRequested = Signal(str)
    # A setting an op reads was clicked: whose and which.
    optionRequested = Signal(str, str)

    def __init__(self, loadout, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.workspace = None
        self.namer: Optional[guidnames.Namer] = None
        self.guid: Optional[str] = None
        self.turns: List[xmlview.Turn] = []
        # Asked of each mod's turn: the loader's verdict on that mod, where
        # the Export tab holds one that is still about the mod as it is.
        self.loader_verdict: Optional[Callable] = None
        self._filling = False
        # Which asset of which mod the typing pane holds, and the text it was
        # handed: what Revert puts back, and what says the mod has moved under
        # what is being typed.
        self._typing_for: Optional[tuple] = None
        self._typing_from = ""
        self._asset_from = ""
        # Whether each pane's own text reads back, and so may be typed in.
        self._can_type = False
        self._can_type_asset = False
        # The headings as each tab words them, before "Updates on Apply".
        self._headings = ("", "")
        # Which pane was last said to hold the other shut.
        self._held_by = ""
        # The shown tab's ops by the line each starts on; None until asked.
        self._op_lines: Optional[list] = None
        # The ops in the typing pane that reach beyond the asset, as the
        # pane was handed them: what is drawn over each, and which it is.
        self._beyond: List[xmlview.Beyond] = []
        self._reverted: set = set()
        self._beyond_marks: tuple = ((), (), ())
        # The text an undone Apply put back in a pane, while it is there.
        self._put_back: Optional[str] = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.strip = QTabBar()
        self.strip.setObjectName("ModStrip")
        self.strip.setAccessibleName(tr("Mods that change this asset, in load order"))
        self.strip.setExpanding(False)
        # Names are whole: mods share their first words, and a strip of
        # names cut short is a strip of the same name.  It scrolls instead.
        self.strip.setElideMode(Qt.TextElideMode.ElideNone)
        self.strip.setUsesScrollButtons(True)
        self.strip.setDrawBase(False)
        self.strip.currentChanged.connect(self._on_tab)
        outer.addWidget(self.strip)

        self.empty = QLabel(tr(
            "Each mod that changes the asset gets a tab here, in load order, "
            "with the ModOps it writes and the asset it leaves."
        ))
        self.empty.setObjectName("StepHint")
        self.empty.setWordWrap(True)
        # At the top, under the line that says to pick an asset: with nothing
        # else on show it would otherwise drift to the middle of the panel.
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self.empty, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("XmlPanes")
        self.splitter.setChildrenCollapsible(False)
        self.modops_side, self.modops_heading, self.modops = self._side(to_fields=False)
        self.asset_side, self.asset_heading, self.asset = self._side(to_fields=True)
        self.asset.fieldRequested.connect(self._to_fields)
        # The ModOps of the mod changes go to have a pane of their own, in the
        # same place: it keeps what is typed while another tab is looked at.
        self.typing = XmlPane(gutter=True)
        banded = tr(
            "A tinted band marks a ModOp that reaches other assets too. Its heading says which."
        )
        # An arrow in the strip says for itself where its op goes.
        self.typing.say_of_marks(
            banded, lambda line: self.typing.asides().get(line) or banded
        )
        self.typing.headingClicked.connect(self._open_beyond_menu)
        self.typing.heading_menu = self._beyond_menu
        self.typing.setAccessibleName(tr("ModOps for this asset"))
        self.typing.setVisible(False)
        self.typing.textChanged.connect(self._on_typed)
        self.modops_side.layout().addWidget(self.typing, 1)
        # And so has the asset as that mod leaves it.
        self.asset_typing = XmlPane(to_fields=True)
        self.asset_typing.setAccessibleName(tr("The asset after this mod"))
        self.asset_typing.setVisible(False)
        self.asset_typing.textChanged.connect(self._on_typed)
        self.asset_typing.fieldRequested.connect(self._to_fields)
        self.asset_side.layout().addWidget(self.asset_typing, 1)
        # A name beside a line opens the asset it names.
        for pane in (self.modops, self.asset, self.typing, self.asset_typing):
            pane.assetClicked.connect(self.assetRequested)
        # And a setting one of the ops reads opens that option: the shown
        # mod's, or the one changes go to.
        self.modops.set_options(lambda: self._whose(self.current()))
        self.typing.set_options(lambda: self._whose(self.current(), editing=True))
        for pane in (self.modops, self.typing):
            pane.optionClicked.connect(self.optionRequested)
        # The caret on an op shows the node it lands on.
        for pane in (self.modops, self.typing):
            pane.cursorPositionChanged.connect(lambda pane=pane: self._follow(pane))
        # Named from the start, and again for each tab by whose XML it holds.
        self.modops.setAccessibleName(tr("ModOps for this asset, read-only"))
        self.asset.setAccessibleName(tr("The asset in the game files, read-only"))
        self.splitter.addWidget(self.modops_side)
        self.splitter.addWidget(self.asset_side)
        self.splitter.setSizes([1, 1])
        outer.addWidget(self.splitter, 1)

        self.find_bar = FindBar()
        outer.addWidget(self.find_bar)
        # Listed in the Keyboard shortcuts dialog from its own table
        # (shortcuts_dialog.WIDGET_KEYS), with the bar's other keys.
        for keys, backward in (("F3", False), ("Shift+F3", True)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda backward=backward: self._find_again(backward))

        # Under the tab of the mod changes go to, and no other: what Apply
        # did, or why the pane is not typed in, and the two buttons.
        self.bar = QWidget()
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1, Qt.AlignmentFlag.AlignVCenter)
        self.rewrite_help = HelpTip(tr(
            "Apply keeps each ModOp as you typed it and reads it to show what "
            "it comes to. A ModOp you did not touch stays where it is in the "
            "mod, with its note. A comment above a ModOp is its note. A ModOp "
            "left out is taken out of the mod. Type in the asset instead, and "
            "the Studio works out narrow ModOps for what you changed."
        ), tr("What Apply does"))
        bar.addWidget(self.rewrite_help)
        # Translators: puts the ModOps pane back to what the mod says now.
        self.revert_button = QPushButton(tr("Revert"))
        self.revert_button.setToolTip(tr("Puts back the ModOps the mod has now."))
        self.revert_button.clicked.connect(self.revert_typed)
        bar.addWidget(self.revert_button)
        self.apply_button = QPushButton(tr("Apply"))
        self.apply_button.setObjectName("Primary")
        self.apply_button.setToolTip(tr("Changes the mod to say what you typed.  (Ctrl+Enter)"))
        self.apply_button.clicked.connect(self._ask_apply)
        bar.addWidget(self.apply_button)
        outer.addWidget(self.bar)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            apply_key = QShortcut(QKeySequence(keys), self)
            apply_key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            apply_key.activated.connect(self._ask_apply)

        also = QHBoxLayout()
        also.setSpacing(8)
        self.also_label = QLabel("")
        self.also_label.setObjectName("StepHint")
        self.also_label.setWordWrap(True)
        also.addWidget(self.also_label, 1)
        self.also_button = QPushButton(tr("Show XML…"))
        self.also_button.clicked.connect(self._show_also)
        also.addWidget(self.also_button)
        outer.addLayout(also)

        # What makes the asset shown differ from what was written, or from
        # what the game has.  Each is a sentence with the warning glyph.
        self.contested_label = self._warning()
        self.unread_label = self._warning()
        self.loader_label = self._warning()
        for label in (self.contested_label, self.unread_label, self.loader_label):
            outer.addWidget(label)
        self.clear()

    def _side(self, to_fields: bool):
        side = QWidget()
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        heading = QLabel("")
        heading.setObjectName("SubHeading")
        column.addWidget(heading)
        pane = XmlPane(to_fields=to_fields)
        column.addWidget(pane, 1)
        return side, heading, pane

    def _warning(self) -> QLabel:
        label = QLabel("")
        label.setWordWrap(True)
        theme.tint(label, "warn")
        label.setVisible(False)
        return label

    # -- what is shown ------------------------------------------------------
    def set_source(self, workspace) -> None:
        self.workspace = workspace
        # One for the four panes: what one has asked, the others need not.
        self.namer = guidnames.Namer(workspace) if workspace is not None else None
        for pane in (self.modops, self.asset, self.typing, self.asset_typing):
            pane.set_namer(self.namer)
        self.clear()

    def clear(self) -> None:
        self.guid = None
        self.turns = []
        self._typing_for = None
        self._fill_tabs("")
        self._show_turn()

    def show_asset(self, guid: Optional[str], editing: str) -> None:
        """Show ``guid`` mod by mod, on the tab that was open if its mod still
        has one and on the mod changes go to if not."""
        if self.workspace is None or guid is None:
            self.clear()
            return
        open_now = self.current() if guid == self.guid else None
        # The tab of the mod changes go to follows it to another mod.
        keep = editing if open_now is None or open_now.editing else open_now.origin
        another = guid != self.guid
        self.guid = guid
        if self.namer is not None:
            # Every op here is about this asset: its name beside each says
            # nothing the tab does not.
            self.namer.skip = frozenset({str(guid)})
        self.turns = xmlview.turns(self.workspace, guid, editing)
        self._fill_tabs(keep)
        self._show_turn()
        if another:
            self.asset_in_front().bring_marks_into_view()

    def show_mod(self, origin: str) -> bool:
        """Open ``origin``'s tab; whether it has one."""
        for at, turn in enumerate(self.turns):
            if turn.origin == origin:
                self.strip.setCurrentIndex(at)
                return True
        return False

    def show_change(self, origin: str, key) -> bool:
        """Open ``origin``'s tab at a change of that mod's, ``key``: the op
        it comes of at the top of the ModOps, and the node it sets in the
        asset beside them.  Whether the tab is there."""
        if not self.show_mod(origin):
            return False
        turn = self.current()
        change = self.loadout.changes.get(tuple(key))
        if change is None:
            return True
        line = xmlview.line_for(turn, change)
        if line is not None:
            self.asset_in_front().go_to_line(line)
        # Not over what somebody has typed and not applied: their caret
        # stays where they left it.
        if not (turn.editing and self.has_unapplied()):
            op = xmlview.shown_op_line(self.loadout, turn, change)
            if op is not None:
                (self.typing if turn.editing else self.modops).go_to_line(op, top=True)
        return True

    def _whose(self, turn: Optional[xmlview.Turn], editing: bool = False) -> Optional[tuple]:
        """``(loadout, origin)`` of the mod whose ops a pane holds, where
        its ``$names`` are options: Anno 117's."""
        if self.loadout.game != games.ANNO_117:
            return None
        if editing:
            return self.loadout, self.loadout.editing_origin()
        if turn is None or turn.origin is None:
            return None
        return self.loadout, turn.origin

    def current(self) -> Optional[xmlview.Turn]:
        at = self.strip.currentIndex()
        return self.turns[at] if 0 <= at < len(self.turns) else None

    def name_of(self, turn: xmlview.Turn) -> str:
        if turn.origin is None:
            return tr("Base game")
        # "My Anno Mod" stays English: it is the name the mod is exported
        # under until somebody gives it another.
        return self.loadout.label_of(turn.origin) or "My Anno Mod"

    def _editing_name(self) -> str:
        return next((self.name_of(turn) for turn in self.turns if turn.editing), "")

    def _fill_tabs(self, keep) -> None:
        self._filling = True
        try:
            while self.strip.count():
                self.strip.removeTab(0)
            chosen = 0
            for at, turn in enumerate(self.turns):
                name = self.name_of(turn)
                self.strip.addTab(name)
                self.strip.setTabToolTip(at, name)
                if turn.editing:
                    self.strip.setTabIcon(at, theme.glyph("tab_edit"))
                    self.strip.setTabToolTip(at, tr("%s. You are editing this mod.", name))
                    self.strip.setAccessibleTabName(at, tr("%s, the mod you are editing", name))
                elif turn.contested:
                    self.strip.setTabIcon(at, theme.icon("warn"))
                    self.strip.setTabToolTip(at, tr(
                        "%(mod)s. Loads after %(yours)s and writes over what it sets.",
                        mod=name, yours=self._editing_name(),
                    ))
                    self.strip.setAccessibleTabName(
                        at, tr("%s, read-only, writes over what yours sets", name)
                    )
                else:
                    self.strip.setTabIcon(at, theme.glyph("tab_locked"))
                    self.strip.setAccessibleTabName(at, tr("%s, read-only", name))
                if turn.origin == keep:
                    chosen = at
            self.strip.setCurrentIndex(chosen)
        finally:
            self._filling = False

    def _on_tab(self, _at: int) -> None:
        if not self._filling:
            self._show_turn()
            self.asset_in_front().bring_marks_into_view()

    def _marked_lines(self, turn: xmlview.Turn) -> List[int]:
        lines = []
        for path in turn.contested:
            line = xmlview.line_of(turn, path)
            if line is not None:
                lines.append(line)
        return lines

    def _show_turn(self) -> None:
        turn = self.current()
        shown = turn is not None
        self._op_lines = None
        self.strip.setVisible(shown)
        self.splitter.setVisible(shown)
        self.empty.setVisible(not shown)
        if turn is None:
            self.modops.show_text("")
            self.asset.show_text("")
            self.find_bar.setVisible(False)
            for widget in (self.also_label, self.also_button, self.contested_label,
                           self.unread_label, self.loader_label, self.bar):
                widget.setVisible(False)
            return

        name = self.name_of(turn)
        self.modops_side.setVisible(not turn.is_game)
        if turn.is_game:
            self.asset_heading.setText(tr("The asset in the game files"))
            self.asset.setAccessibleName(tr("The asset in the game files, read-only"))
            self.asset.setPlaceholderText(tr(
                "The game files have no asset with this GUID. A mod adds it."
            ))
        else:
            # A turn that is only read shows every op of the mod that reaches
            # the asset.  The one typed in holds the asset's own, and names
            # the rest under it: an op that reaches other assets too is not
            # this pane's to type over.
            self.modops_heading.setText(tr("ModOps of %s that reach this asset", name))
            self.modops.setAccessibleName(
                tr("ModOps of %s that reach this asset, read-only", name)
            )
            self.modops.setPlaceholderText(tr("%s does not change this asset.", name))
            self.typing.setAccessibleName(tr("ModOps of %s that reach this asset", name))
            heading = (
                tr("The asset after %s, which the game loads", name) if turn.last
                else tr("The asset after %s", name)
            )
            self.asset_heading.setText(heading)
            self.asset.setAccessibleName(tr("%s, read-only", heading))
            self.asset.setPlaceholderText(tr("No mod has added this asset yet."))
            self.asset_typing.setAccessibleName(heading)
            self.asset_typing.setPlaceholderText(tr("No mod has added this asset yet."))
        self._headings = (self.modops_heading.text(), self.asset_heading.text())
        self.modops.setVisible(not turn.editing)
        self.typing.setVisible(turn.editing)
        self.asset.setVisible(not turn.editing)
        self.asset_typing.setVisible(turn.editing)
        self.bar.setVisible(turn.editing)
        if turn.editing:
            self._hand_over(turn, name)
        else:
            self.modops.show_text(turn.whole)
            self.asset.show_text(turn.asset, self._marked_lines(turn), turn.changed)
        if self.find_bar.isVisible():
            if not self.find_bar.pane.isVisible():
                self.find_bar.pane = self.asset_in_front()
            self.find_bar.find(again=False)
        self._say_also(turn, name)
        self._say_warnings(turn, name)

    # -- typing ---------------------------------------------------------------
    def asset_in_front(self) -> XmlPane:
        """The asset pane on show: the one typed in on the tab of the mod
        changes go to, the one only read on the others."""
        turn = self.current()
        return self.asset_typing if turn is not None and turn.editing else self.asset

    def _fill(self, pane: XmlPane, text: str, marked=(), changed=()) -> None:
        """Put the mod's own text in a typing pane: nothing typed."""
        self._filling = True
        try:
            if pane is self.typing:
                band, headings, badges = self._beyond_marks
                pane.show_text(text, band=band)
                pane.set_headings(headings, badges)
            else:
                pane.show_text(text, marked, changed)
            pane.document().setModified(False)
        finally:
            self._filling = False

    # -- ops that reach beyond the asset --------------------------------------
    def _mark_beyond(self, turn: xmlview.Turn) -> None:
        """Work out what is drawn over each op of ``turn``'s ModOps that is
        not the asset's own: the band it is on, the heading that says what
        it reaches, and its badge in the strip."""
        self._beyond = list(turn.beyond)
        self._reverted = {id(op) for op in turn.reverted}
        views = (
            {view.uid: view for view in keptops.of(self.loadout, turn.origin)}
            if any(item.op is not None for item in self._beyond) else {}
        )
        band, headings, badges = [], [], []
        for item in self._beyond:
            # The blank line above it is where its heading is drawn.
            top = max(1, item.first - 1)
            band.extend(range(top, item.last + 1))
            headings.append((top, self._beyond_words(item)))
            view = views.get(item.op.uid) if item.op is not None else None
            if view is not None:
                verb, shipped_off, _slots = keptops.shape(view, self.loadout.game)
                badges.append((item.first, op_marks.icon(verb, shipped_off)))
        self._beyond_marks = (band, headings, badges)

    def _others(self, item: xmlview.Beyond) -> List[str]:
        """The other assets an op in the pane reaches, as it was last read."""
        if item.op is None:
            return []
        return [guid for guid in dict.fromkeys(str(one) for one in item.op.matched)
                if guid != self.guid]

    def _beyond_words(self, item: xmlview.Beyond) -> str:
        """The heading over an op that is not the asset's own: what kind of
        op it is, and how far it reaches."""
        if item.unread is not None:
            # Translators: the heading over a ModOp in a pane of ModOps.
            return tr("Not read · aimed at this asset, and not at it alone")
        op = item.op
        bits = []
        if op.searches:
            # Translators: a ModOp that finds its assets by searching.
            bits.append(tr("Pattern"))
        if op.within:
            runs = not typedops.in_idle_group(self.loadout, op)
            bits.append(tr("In a group that runs") if runs
                        else tr("In a group that does not run today"))
            asked = xmlview.group_condition(self.loadout, op)
            if asked:
                # Translators: %s is a group's condition as its mod wrote it.
                bits.append(tr("Condition %s", asked))
        others = len(self._others(item))
        if others:
            bits.append(trn(
                "Reaches this asset and %(n)s other", "Reaches this asset and %(n)s others",
                others,
            ))
        elif not op.within:
            bits.append(tr("Not this asset's alone"))
        if id(op) in self._reverted:
            bits.append(tr("Its change to this asset was reverted"))
        return " · ".join(bits)

    def _beyond_menu(self, at: int) -> list:
        """What the heading of the ``at``-th such op offers: its place in
        the mod's file, and the other assets it reaches."""
        turn = self.current()
        if turn is None or turn.origin is None or not 0 <= at < len(self._beyond):
            return []
        item = self._beyond[at]
        origin = turn.origin
        found = []
        if item.op is not None:
            found.append((
                tr("Show in file"),
                lambda: self.fileRequested.emit(origin, "op", item.op.uid),
            ))
            if item.op.within:
                found.append((
                    tr("Show its group in file"),
                    lambda: self.fileRequested.emit(origin, "op", item.op.within[-1]),
                ))
        elif item.unread is not None:
            found.append((
                tr("Show in file"),
                lambda: self.fileRequested.emit(origin, "unread", item.unread.order),
            ))
        others = self._others(item)
        name = self.workspace.display_name if self.workspace is not None else str
        for guid in others[:12]:
            found.append((
                tr("Open %s", name(guid) or guid),
                lambda guid=guid: self.assetRequested.emit(guid),
            ))
        if len(others) > 12:
            found.append((trn(
                "And %(n)s more asset, in the Mod changes panel",
                "And %(n)s more assets, in the Mod changes panel", len(others) - 12,
            ), None))
        return found

    def _open_beyond_menu(self, at: int) -> None:
        offered = self._beyond_menu(at)
        if not offered:
            return
        menu = QMenu(self)
        for label, do in offered:
            action = menu.addAction(label, do) if do is not None else menu.addAction(label)
            action.setEnabled(do is not None)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def _going_words(self, one: typedops.Going) -> str:
        """Where an op typed here goes, that is not this asset's own."""
        if one.search:
            # Translators: said of a ModOp that finds its assets by searching.
            return tr("Goes to what its search finds")
        others = [guid for guid in one.assets if guid != self.guid]
        if len(one.assets) == 1:
            name = self.workspace.display_name(others[0]) if self.workspace is not None else ""
            # Translators: said of a ModOp typed on one asset's page that is
            # for another. %s is the name of that asset.
            return tr("Goes to %s", name or others[0])
        if len(others) < len(one.assets):
            return trn(
                "Goes to this asset and %(n)s other", "Goes to this asset and %(n)s others",
                len(others),
            )
        return trn(
            "Goes to %(n)s other asset", "Goes to %(n)s other assets", len(others),
        )

    def _mark_going(self, in_modops: bool) -> None:
        """Say, beside each op typed in the ModOps pane that is not this
        asset's own, where it goes: before Apply, from what the op says."""
        turn = self.current()
        if not in_modops or turn is None or turn.origin is None or self.workspace is None:
            self.typing.set_asides(())
            self.apply_button.setAccessibleDescription("")
            return
        found = typedops.goes(
            self.workspace, self.guid, turn.origin, self.typing.toPlainText(),
            self._typing_from,
        )
        if found is None:
            # Half typed, and not XML this moment: what was said stays with
            # the lines it was said of.
            return
        said = [(one.line, self._going_words(one), one.last) for one in found]
        self.typing.set_asides(said)
        # The same for whoever does not see the pane, on the way to Apply.
        self.apply_button.setAccessibleDescription(" ".join(
            # Translators: read out before Apply, once for each ModOp typed
            # that goes to another asset. %(where)s is a sentence such as
            # "Goes to Bakery".
            tr("Line %(line)s: %(where)s.", line=line, where=words)
            for line, words, _last in said
        ))

    def _apply_words(self, in_modops: bool) -> str:
        """What the Apply button says: how far what was typed reaches, where
        that is further than this asset."""
        turn = self.current()
        if not in_modops or turn is None or turn.origin is None or self.workspace is None:
            return tr("Apply")
        reach = typedops.reach(
            self.workspace, self.guid, turn.origin, self.typing.toPlainText(),
            self._typing_from,
        )
        if reach is None:
            return tr("Apply")
        if reach.search:
            # Translators: a button. What was typed changes a ModOp that finds
            # its assets by searching, and how many is not known yet.
            return tr("Apply to a search")
        if len(reach.assets) > 1:
            return trn("Apply to %(n)s asset", "Apply to %(n)s assets", len(reach.assets))
        return tr("Apply")

    def _hand_over(self, turn: xmlview.Turn, name: str) -> None:
        """Give the typing panes the mod's ModOps for the asset and the asset
        as it leaves it -- unless one holds something typed for that same
        asset and mod, which stays."""
        holding = (self.guid, turn.origin)
        marked = self._marked_lines(turn)
        if self._typing_for == holding and self.has_unapplied():
            # Something else may have changed the mod meanwhile: undo, the
            # changes list, a pattern. What is typed is still the whole of it,
            # and the pane that was not typed in follows the mod.
            in_asset = self.typed_kind() == ASSET
            moved = (turn.asset != self._asset_from) if in_asset else (
                turn.modops != self._typing_from)
            self._typing_from, self._asset_from = turn.modops, turn.asset
            if in_asset:
                self._mark_beyond(turn)
                self._fill(self.typing, turn.modops)
            else:
                self._fill(self.asset_typing, turn.asset, marked, turn.changed)
            if moved:
                self.say(tr(
                    "The mod's ModOps for this asset changed while you were "
                    "typing. Apply replaces them with what is here. Revert "
                    "shows what they are now."
                ), "warn")
            self._sync_bar()
            return
        self._typing_for = holding
        self._typing_from, self._asset_from = turn.modops, turn.asset
        self._mark_beyond(turn)
        self._fill(self.typing, turn.modops)
        self._fill(self.asset_typing, turn.asset, marked, turn.changed)
        # What is typed is what is stored, so there is nothing to ask of
        # the text before it is typed over.
        self._can_type = True
        self._can_type_asset = bool(turn.asset) and typedops.can_type_asset(
            self.workspace, self.guid, turn.origin
        )
        self.typing.setPlaceholderText(tr(
            "%s does not change this asset yet. Type ModOps here, then Apply.", name
        ))
        self.say("")
        self._sync_bar()

    def _on_typed(self) -> None:
        if not self._filling:
            self._sync_bar()

    def _sync_bar(self) -> None:
        """Apply and Revert wait for something typed, and typing in one pane
        holds the other shut."""
        in_modops = self.typing.document().isModified()
        in_asset = self.asset_typing.document().isModified()
        self.typing.set_editable(self._can_type and not in_asset)
        self.asset_typing.set_editable(self._can_type_asset and not in_modops)
        typed = in_modops or in_asset
        if not typed and (self.apply_button.hasFocus() or self.revert_button.hasFocus()):
            # A button that goes grey under the keyboard has it handed on by
            # Qt to the next control along, and from these that is out of the
            # view: Play in the window's tab bar, where Enter starts the game.
            # To the strip of mods and not a pane, as Revert asset sends it: a
            # pane that can be typed in keeps Ctrl+Z for its own text, and the
            # next one is for undoing the Apply.
            (self.strip if self.strip.isVisible() else self).setFocus()
        self.apply_button.setEnabled(typed)
        self.apply_button.setText(self._apply_words(in_modops))
        self._mark_going(in_modops)
        self.revert_button.setEnabled(typed)
        self._say_held(in_modops, in_asset)

    def _say_held(self, in_modops: bool, in_asset: bool) -> None:
        """Say, where it is looked for, why the pane not typed in went shut:
        over that pane, in its tooltip, and once on the line beside Apply."""
        turn = self.current()
        if turn is None or not turn.editing:
            return
        modops, asset = self._headings
        def waits(heading: str) -> str:
            # Translators: %s is a pane's heading, while the other pane is typed in.
            return tr("%s. Updates on Apply.", heading)

        self.modops_heading.setText(waits(modops) if in_asset else modops)
        self.asset_heading.setText(waits(asset) if in_modops else asset)
        held = tr("Shut while the other pane is typed in. Apply or Revert opens it.")
        self.typing.setToolTip(held if in_asset else "")
        self.asset_typing.setToolTip(held if in_modops else "")
        by = ASSET if in_asset else (MODOPS if in_modops else "")
        if by == self._held_by:
            return
        self._held_by = by
        if by == ASSET and self._can_type:
            self.say(tr(
                "Typing in the asset. Apply works out the ModOps from it, so "
                "the ModOps pane is shut until Apply or Revert."
            ))
        elif by == MODOPS and self._can_type_asset:
            self.say(tr(
                "Typing ModOps. Apply works out the asset from them, so the "
                "asset pane is shut until Apply or Revert."
            ))

    def _follow(self, pane: XmlPane) -> None:
        """The caret went onto an op: show the node it lands on, in the
        asset beside it.  Only for a caret somebody moved, and only while the
        ModOps are the mod's and not half typed."""
        turn = self.current()
        if (self._filling or turn is None or turn.is_game or not pane.hasFocus()
                or pane.document().isModified() or self.asset_typing.document().isModified()):
            return
        if self._op_lines is None:
            found = []
            for change in xmlview.own_changes(self.workspace, self.guid, turn.origin):
                line = xmlview.op_line(self.loadout, turn, change.key)
                if line is not None:
                    found.append((line, change))
            # And an op kept as written, by the first thing it comes to here.
            for op in turn.own_ops:
                line = xmlview.kept_line(self.loadout, turn, op.uid)
                under = [
                    change for change in self.loadout.changes_by_op(op.uid)
                    if change.guid == self.guid
                ]
                if line is not None and under:
                    found.append((line, under[0]))
            self._op_lines = sorted(found, key=lambda entry: entry[0])
        at = pane.textCursor().blockNumber() + 1
        on = [change for line, change in self._op_lines if line <= at]
        if not on:
            return
        target = xmlview.line_for(turn, on[-1])
        if target is not None:
            self.asset_in_front().go_to_line(target)

    def has_unapplied(self) -> bool:
        """Whether something typed has neither been applied nor reverted."""
        return self._typing_for is not None and (
            self.typing.document().isModified() or self.asset_typing.document().isModified()
        )

    def typed_kind(self) -> str:
        """Which pane was typed in: ``ASSET`` or ``MODOPS``."""
        return ASSET if self.asset_typing.document().isModified() else MODOPS

    def typed_text(self) -> str:
        pane = self.asset_typing if self.typed_kind() == ASSET else self.typing
        return pane.toPlainText()

    def typed_from(self) -> str:
        """The text the typing started from: the mod's ModOps as written."""
        return self._typing_from

    def revert_typed(self) -> None:
        """Put the typing panes back to what the mod says."""
        turn = self.current()
        marked = self._marked_lines(turn) if turn is not None and turn.editing else ()
        self._fill(self.typing, self._typing_from)
        self._fill(
            self.asset_typing, self._asset_from, marked,
            turn.changed if turn is not None and turn.editing else (),
        )
        self.say("")
        self._sync_bar()

    def put_back(self, kind: str, guid: str, origin: str, text: str) -> bool:
        """Put ``text`` back in the pane it was typed in, as text typed and
        not applied: for an Apply just undone.  Only where that asset is
        shown on the tab of that mod with nothing typed; whether it was."""
        turn = self.current()
        if (
            turn is None or not turn.editing or self.guid != guid
            or turn.origin != origin or self.has_unapplied()
        ):
            return False
        in_asset = kind == ASSET
        pane = self.asset_typing if in_asset else self.typing
        if not (self._can_type_asset if in_asset else self._can_type):
            return False
        if pane.toPlainText() == text:
            return False
        pane.type_over(text)
        self._put_back = text
        self.say(tr("What you typed is back in the pane, not applied."))
        return True

    def drop_put_back(self) -> bool:
        """Before another step of undo or redo: take the text an undone
        Apply put back for no longer typed, where nobody has typed in it
        since.  Redone, it is the mod's; and a step further back is not
        made under text that nobody is typing.  Whether it was dropped."""
        text, self._put_back = self._put_back, None
        if text is None or not self.has_unapplied() or self.typed_text() != text:
            return False
        self.revert_typed()
        return True

    def _ask_apply(self) -> None:
        if self.has_unapplied():
            self.applyRequested.emit(self.typed_kind(), self.typed_text())

    def say(self, text: str, tone: str = "") -> None:
        """The line beside Apply: what it did, or why it did not."""
        self.status.setText(text)
        theme.tint(self.status, tone)

    def refuse(self, found: typedops.Read, kind: str = MODOPS) -> None:
        """What was typed is not something to apply: say why, and where the
        reader stopped if it stopped.  Nothing was applied."""
        pane = self.asset_typing if kind == ASSET else self.typing
        stopped = where_reading_stopped(found)
        if stopped:
            # Loose text, applied, would be carried and come to nothing: it is
            # still in the pane, and the caret goes to it.  A comment is kept.
            self.say(tr("Not applied. %s", stopped), "error")
            pane.go_to_line(found.line)
        elif found.refused == typedops.NOT_AN_ASSET:
            self.say(tr("Not applied. The outermost element has to be <Asset>."), "error")
        elif found.refused == typedops.ANOTHER_GUID:
            self.say(tr(
                "Not applied. The GUID is what makes this the asset it is. To "
                "change another asset, open that one."
            ), "error")
        elif found.refused == typedops.OUTSIDE_VALUES:
            self.say(tr(
                "Not applied. Only what is inside <Values> can be changed here."
            ), "error")
        elif found.refused == typedops.NO_VALUES:
            self.say(tr("Not applied. The asset has no <Values>."), "error")
        elif found.refused_at:
            # One part of what was typed has no narrow form, and with it the
            # rest stays unapplied too.  Both are said, and which part: by
            # its path, and by the caret going to it where it can be found.
            self.say(tr(
                "Not applied, none of it. The change at %(path)s cannot be "
                "written as a narrow ModOp: %(why)s. Make that change in "
                "Fields, or type the ModOp for it.",
                path=found.refused_at, why=tr_stored(found.refused),
            ), "error")
            line = typedops.line_typed(pane.toPlainText(), found.refused_at)
            if line:
                pane.go_to_line(line)
        else:
            self.say(tr(
                "Not applied, none of it. A change in it cannot be written as "
                "a narrow ModOp: %s. Make that change in Fields, or type the "
                "ModOp for it.", tr_stored(found.refused),
            ), "error")
        pane.setFocus()

    def applied(self, found: typedops.Read, done: typedops.Applied, name_of_asset,
                not_all: bool = False) -> None:
        """Say what Apply did, once the panes show the mod as it is now.
        ``not_all``: the asset typed is not quite the asset the mod leaves."""
        said = []
        written = done.kept + done.retuned + done.added
        if not done.moved and not done.settled:
            said.append(tr("No change to the mod."))
        elif found.written:
            # Kept as typed, and in the pane: how many of them are new or
            # typed over, and how many were taken out.  It says which it
            # counts, since the pane holds the ones left alone as well.  What
            # went in and is not the pane's to hold is said apart, further on.
            stored = done.retuned + done.added
            if stored:
                said.append(trn(
                    "%(n)s new or changed ModOp kept as typed.",
                    "%(n)s new or changed ModOps kept as typed.", stored,
                ))
            if done.dropped:
                said.append(trn(
                    "%(n)s ModOp taken out.", "%(n)s ModOps taken out.", done.dropped,
                ))
        elif written or not (done.kept_written or done.carried):
            said.append(trn(
                "Read as %(n)s change.", "Read as %(n)s changes.", written,
            ))
        narrow = (found.ops - done.settled - done.reaching - done.unread
                  - done.kept_written - done.carried)
        if written > narrow > 0 and not found.written:
            said.append(tr("Each is written as its own ModOp."))
        if done.kept_written and done.moved:
            said.append(trn(
                "%(n)s ModOp is kept as written.", "%(n)s ModOps are kept as written.",
                done.kept_written,
            ))
        if done.settled:
            said.append(trn(
                "%(n)s ModOp changes nothing, because the asset already reads that way.",
                "%(n)s ModOps change nothing, because the asset already reads that way.",
                done.settled,
            ))
        # What was typed here and has left the pane: an op that does not
        # reach this asset.  Where it went is said by the name of the panel
        # it is a row in, which may be anywhere on the screen or shut.  A
        # group's own tag goes in as one more and is no ModOp, so it is not
        # counted: the number is the ModOps to look for.
        beside = done.reaching - done.groups
        if beside:
            # Translators: "Mod changes" is the name of the Studio's panel.
            said.append(trn(
                "%(n)s ModOp does not reach this asset, so it is applied and "
                "listed in the Mod changes panel, not in this pane.",
                "%(n)s ModOps do not reach this asset, so they are applied "
                "and listed in the Mod changes panel, not in this pane.",
                beside,
            ))
        elif done.groups:
            # Translators: "Mod changes" is the name of the Studio's panel.
            said.append(trn(
                "%(n)s Group is applied and listed in the Mod changes panel, not in this pane.",
                "%(n)s Groups are applied and listed in the Mod changes panel, not in this pane.",
                done.groups,
            ))
        if done.why_unread:
            said.append(trn(
                "%(n)s ModOp could not be read and is carried as typed: %(why)s.",
                "%(n)s ModOps could not be read and are carried as typed: %(why)s.",
                len(done.why_unread), why=tr_stored(done.why_unread[0]),
            ))
        if done.unread:
            # Translators: "Mod changes" is the name of the Studio's panel.
            said.append(trn(
                "%(n)s of them does not name this asset, and is listed in the "
                "Mod changes panel, not in this pane.",
                "%(n)s of them do not name this asset, and are listed in "
                "the Mod changes panel, not in this pane.",
                done.unread,
            ))
        if done.elsewhere:
            said.append(trn(
                "%(n)s other asset was changed too: %(assets)s.",
                "%(n)s other assets were changed too: %(assets)s.",
                len(done.elsewhere),
                assets=join_list(name_of_asset(guid) for guid in done.elsewhere),
            ))
        if found.unnoticed:
            # Taken out by hand, so written: what the reader knows of such an
            # entry is said, and left to whoever took it out.
            said.append(trn(
                "%(n)s entry you took out only names an asset and sets nothing, so "
                "the game may not notice it gone. The removal is written all the same.",
                "%(n)s entries you took out only name an asset and set nothing, so "
                "the game may not notice them gone. The removals are written all the same.",
                found.unnoticed,
            ))
        if found.stray:
            # In either pane: between two ops, or between a node's fields.
            # It is gone from the pane by now, and this is where that is said.
            said.append(tr("Text typed between elements was left out."))
        elif not_all:
            said.append(tr(
                "The asset is not all as you typed it. It is shown as the mod's "
                "ModOps leave it."
            ))
        self.say(" ".join(said), "warn" if done.why_unread else "")

    def _say_also(self, turn: xmlview.Turn, name: str) -> None:
        said = []
        # What reaches the asset and is not in the pane with the rest: a
        # group kept whole, which is typed over in the mod's file. A pane
        # that is only read has them all.
        beside = [
            op for op in turn.reaching if all(op is not item.op for item in turn.beyond)
        ] if turn.editing else []
        # A group kept whole can be reverted too: it is said with the groups,
        # and not counted again as a kept ModOp.
        reverted = [op for op in turn.reverted if all(op is not other for other in beside)]
        if beside:
            said.append(trn(
                "%(n)s group of %(mod)s kept whole reaches this asset.",
                "%(n)s groups of %(mod)s kept whole reach this asset.",
                len(beside), mod=name,
            ))
            if len(turn.reverted) > len(reverted):
                said.append(said_of_reverted(len(turn.reverted) - len(reverted)))
        if reverted:
            # In the pane, each under a heading that says so where the pane
            # is typed in, and under none where it is only read: counted
            # here for what follows to be about.
            said.append(trn(
                "%(n)s kept ModOp of %(mod)s reaches this asset.",
                "%(n)s kept ModOps of %(mod)s reach this asset.",
                len(reverted), mod=name,
            ))
            said.append(said_of_reverted(len(reverted)))
        # Sentences, each whole.
        self.also_label.setText(" ".join(said))
        self.also_label.setToolTip(
            tr("A group kept whole is typed over in the Mod file tab.") if beside else ""
        )
        self.also_label.setVisible(bool(said))
        self.also_button.setVisible(bool(beside))

    def _fields(self, paths) -> str:
        return join_list(sorted({path.rstrip("/").rsplit("/", 1)[-1] for path in paths}))

    def _say_warnings(self, turn: xmlview.Turn, name: str) -> None:
        contested = ""
        if turn.contested and turn.editing:
            contested = trn(
                "A mod that loads after %(mod)s writes over %(n)s field it sets: %(fields)s.",
                "Mods that load after %(mod)s write over %(n)s fields it sets: %(fields)s.",
                len(turn.contested), mod=name, fields=self._fields(turn.contested),
            )
        elif turn.contested:
            contested = trn(
                "Writes over %(n)s field %(mod)s sets: %(fields)s.",
                "Writes over %(n)s fields %(mod)s sets: %(fields)s.",
                len(turn.contested), mod=self._editing_name(),
                fields=self._fields(turn.contested),
            )
        self.contested_label.setText(contested)
        self.contested_label.setVisible(bool(contested))

        unread = ""
        if turn.unread:
            unread = tr(
                "What an unread ModOp does is not shown, so the asset here "
                "may differ from the game's."
            )
        self.unread_label.setText(unread)
        self.unread_label.setVisible(bool(unread))

        differs = ""
        verdict = None
        if self.loader_verdict is not None and turn.origin is not None:
            verdict = self.loader_verdict(turn.origin)
        if verdict is not None and any(
            found.get("guid") == self.guid for found in verdict.differing
        ):
            differs = tr(
                "The game's loader left this asset different from what is "
                "shown. The Export tab has the comparison."
            )
        self.loader_label.setText(differs)
        self.loader_label.setVisible(bool(differs))

    def _show_also(self) -> None:
        turn = self.current()
        if turn is None:
            return
        dialog = XmlDialog(
            tr("What else of %s reaches this asset, as the Studio writes it",
               self.name_of(turn)),
            xmlview.also_xml(self.loadout, turn),
            tr("A ModOp kept as written or an unread one can reach other assets too, so it is "
               "not among this asset's own ModOps."),
            parent=self,
        )
        dialog.exec()
        dialog.deleteLater()

    # -- finding ------------------------------------------------------------
    def _pane_in_use(self) -> XmlPane:
        """The pane find works in: the one with the focus, else the one it was
        last opened on, else the asset."""
        for pane in (self.modops, self.typing, self.asset, self.asset_typing):
            if pane.hasFocus() and pane.isVisible():
                return pane
        held = self.find_bar.pane
        if held is not None and held.isVisible():
            return held
        return self.asset_in_front()

    def open_find(self) -> bool:
        """Open the find bar on the pane in use; False with nothing to find in."""
        if self.current() is None:
            return False
        self.find_bar.open_on(self._pane_in_use())
        return True

    def _find_again(self, backward: bool) -> None:
        if not self.find_bar.isVisible():
            self.open_find()
        else:
            self.find_bar.find(backward=backward)

    # -- to a field and back --------------------------------------------------
    def reveal(self, path: str, key=None) -> bool:
        """Go to the field at ModOp ``path`` on the tab of the mod changes go
        to: its line in the asset, and the op that writes it where ``key`` is
        a change of that mod's.  False where the asset has no such node."""
        at = next((i for i, turn in enumerate(self.turns) if turn.editing), None)
        if at is None:
            return False
        self.strip.setCurrentIndex(at)
        turn = self.turns[at]
        line = xmlview.line_of(turn, path)
        if line is not None:
            self.asset_in_front().go_to_line(line)
            self.asset_in_front().setFocus()
        if key is not None and not self.has_unapplied():
            op = xmlview.op_line(self.loadout, turn, tuple(key))
            if op is not None:
                self.typing.go_to_line(op)
        return line is not None

    def _to_fields(self, line: int) -> None:
        turn = self.current()
        path = xmlview.path_at(turn, line) if turn is not None else None
        if path:
            self.fieldRequested.emit(path)
