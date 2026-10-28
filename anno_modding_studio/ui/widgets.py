"""Small shared widgets, so the same control behaves the same way everywhere."""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTreeWidgetItem,
    QWidget,
)

from ..core import scripts
from ..core.i18n import TERM_END, TERM_START, TERM_WORDS, join_list, tr, trn, unmarked
from . import theme


@contextmanager
def busy_cursor():
    """The wait cursor while the block runs, and the one before it back
    however the block ends.

    Set and restored by hand, a block that returned or raised on a path the
    ``restoreOverrideCursor`` was not on left the cursor spinning over a
    window that was waiting for nothing.  An exception caught outside the
    ``with`` finds the cursor already back, so a message box about it does
    not open under a wait cursor either.
    """
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


# What a QProgressBar can count to: its range is a signed thirty-two bits.
_BAR_MOST = 2 ** 31 - 1


def show_progress(bar, done: int, total: int) -> None:
    """``done`` of ``total`` on ``bar``, in whole kilobytes (or megabytes, and
    so on) once the total is more than the bar can count -- a download past
    two gigabytes is.  ``total`` 0 is "how much is not known yet"."""
    scale = 1
    while total // scale > _BAR_MOST:
        scale *= 1024
    bar.setRange(0, total // scale)
    bar.setValue(done // scale)


def open_on_disk(path) -> None:
    """Open ``path`` the way double-clicking it in Explorer would: a folder
    in a window of its own, a file in whatever program opens its kind."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _select_in_explorer(path) -> None:
    explorer = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "explorer.exe")
    subprocess.Popen([explorer, "/select,", str(path)])


def show_in_folder(path) -> None:
    """Show where ``path`` is, in Explorer: a folder opened, a file picked
    out in its folder.  Where it is not there, the nearest folder above it
    that is."""
    path = Path(path)
    if path.is_file() and sys.platform == "win32":
        _select_in_explorer(path)
        return
    while not path.is_dir() and path.parent != path:
        path = path.parent
    open_on_disk(path)


# A program that opens a .lua file by running it rather than showing it:
# lua.exe, wlua.exe, luajit.exe, lua54.exe and the like, which is what Lua for
# Windows makes the file's kind open with.
_RUNS_LUA = re.compile(r"^w?lua(jit)?[\d.]*$", re.IGNORECASE)


def _opened_by(suffix: str) -> str:
    """The program Windows opens a file ending in ``suffix`` with, or ""
    where there is none or it will not say."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    found = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(found))
    try:
        # ASSOCSTR_EXECUTABLE (2) for the default verb; S_OK is 0.
        answer = ctypes.windll.shlwapi.AssocQueryStringW(
            0, 2, suffix, None, found, ctypes.byref(size)
        )
    except (AttributeError, OSError):
        return ""
    return found.value if answer == 0 else ""


def open_script(path, folder) -> bool:
    """Open a mod's script to be read, in whatever the user opens Lua with,
    but only a Lua file inside the mod's ``folder``
    (:func:`core.scripts.may_open`): the path is the mod's to name, and
    Windows runs a file of another kind.  Whether it was opened.

    Where Lua itself is what opens a .lua file, opening it would run the
    mod's script outside the game.  Then it goes to the kind's *edit* verb,
    and where there is none, Explorer shows it instead.
    """
    if not scripts.may_open(path, folder):
        return False
    if not _RUNS_LUA.match(Path(_opened_by(".lua")).stem):
        open_on_disk(path)
        return True
    try:
        os.startfile(str(path), "edit")
    except OSError:
        _select_in_explorer(path)
    return True


# -- text from outside stays text -------------------------------------------------
# How many ``studio_rich_text`` blocks are open: while one is, a label is left
# to read its text as Qt would.
_RICH_ALLOWED = [0]


class _PlainLabels(QObject):
    """Makes each label still in ``AutoText`` plain, as it is first polished."""

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.Polish
            and isinstance(watched, QLabel)
            and watched.textFormat() == Qt.TextFormat.AutoText
            and not _RICH_ALLOWED[0]
        ):
            watched.setTextFormat(Qt.TextFormat.PlainText)
        return False


def external_text_stays_plain(app) -> None:
    """From now on, every label in ``app`` shows its text as text, unless the
    Studio asked for rich text where it made the label.

    Qt reads a label's text as HTML when its first line looks like it --
    ``AutoText`` -- and so does each message box and tooltip, since each is a
    label too.  Much of what the Studio shows comes from elsewhere: a mod's
    name, a modinfo option's label, a mod.io collection's summary.  A name
    holding ``<img src="file://host/share/a.png">`` had Qt fetch that file as
    soon as the label was drawn -- over SMB, for a UNC path, which can give
    away the user's Windows password hash -- and a link in a message box was
    one click from opening.  Escaping each value where it is shown would be a
    rule for hundreds of places, with one missed being enough.

    So it is done once, here: every widget is polished before it is first
    shown, and a label still in ``AutoText`` then becomes plain.  A label that
    reads as plain now shows the same.  The Studio's own markup asks for rich
    text where it is made (:func:`show_rich`), with every value from outside
    through ``html.escape``; Qt's own rich boxes are opened inside
    :func:`studio_rich_text`.
    """
    if getattr(app, "_plain_labels", None) is not None:
        return
    app._plain_labels = _PlainLabels(app)
    app.installEventFilter(app._plain_labels)


@contextmanager
def studio_rich_text():
    """Leave labels polished inside this block to read their text as Qt
    would: for a box Qt builds itself out of the Studio's own markup, such as
    ``QMessageBox.about``.  Nothing from outside may be in it."""
    _RICH_ALLOWED[0] += 1
    try:
        yield
    finally:
        _RICH_ALLOWED[0] -= 1


def show_rich(label, markup: str) -> None:
    """Put the Studio's own rich text in ``label``: markup it wrote, with each
    value from outside already through ``html.escape``.  Every other label is
    plain (:func:`external_text_stays_plain`)."""
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(markup)


def show_plain(label, text: str) -> None:
    """Put plain text in a label that :func:`show_rich` also writes to."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)


def rich_label(markup: str, parent=None) -> QLabel:
    """A new label showing the Studio's own rich text (:func:`show_rich`)."""
    label = QLabel(parent)
    show_rich(label, markup)
    return label


class FlowLayout(QLayout):
    """A row that wraps: each item at its own size, left to right, and onto
    the next line when the width runs out.

    A ``QHBoxLayout`` of buttons squeezes them below the width their text
    needs once there are more than fit, and Qt clips the text rather than
    the button -- so a card with four things to offer showed "ove them into
    the libra".  Here a button is always as wide as its label, and the row
    is as tall as it takes.  A hidden item takes no room.
    """

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._lay(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._lay(rect, test=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _lay(self, rect, test: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            after = x + hint.width() + self._space
            if after - self._space > area.right() + 1 and line > 0:
                x = area.x()
                y += line + self._space
                after = x + hint.width() + self._space
                line = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = after
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


class _LineHeight(QObject):
    """Keeps a widget exactly ``lines`` lines of its own text tall."""

    def __init__(self, widget, lines: int, pad: int):
        super().__init__(widget)
        self._widget = widget
        self._lines = lines
        self._pad = pad
        widget.installEventFilter(self)
        self.apply()

    def apply(self) -> None:
        spacing = self._widget.fontMetrics().lineSpacing()
        self._widget.setFixedHeight(self._lines * spacing + self._pad)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._widget and event.type() == QEvent.Type.FontChange:
            self.apply()
        return False


class _TextLines(QObject):
    """Keeps a text box exactly tall enough to show ``lines`` lines whole."""

    def __init__(self, edit, lines: int):
        super().__init__(edit)
        self._edit = edit
        self._lines = lines
        edit.installEventFilter(self)
        self.apply()

    def apply(self) -> None:
        edit = self._edit
        # The style sheet's border and padding are the box's contents
        # margins once it is polished, and the document keeps a margin of
        # its own inside them. One pixel more, or the last line is a pixel
        # short of whole and the box scrolls to show it.
        edit.ensurePolished()
        margins = edit.contentsMargins()
        edit.setFixedHeight(
            self._lines * edit.fontMetrics().lineSpacing()
            + margins.top() + margins.bottom()
            + 2 * int(edit.document().documentMargin()) + 1
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is self._edit and event.type() in (
            QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.Polish,
        ):
            # After the event: the margins it brings are not in place yet.
            QTimer.singleShot(0, self, self.apply)
        return False


class TextCap(QObject):
    """Holds what is typed or pasted into a text box to ``limit``
    characters, as a line edit's maximum length does for one line.

    What goes past it is the end of what was just put in, so a paste keeps
    what fits.  Text set from outside while :meth:`setting` is left as it
    is, however long: it is somebody's, and cutting it short as it is shown
    would lose the rest the next time the box is written back. It can then
    be shortened, but not lengthened.
    """

    def __init__(self, edit, limit: int):
        super().__init__(edit)
        self._edit = edit
        self.limit = limit
        self._quiet = False
        edit.document().contentsChange.connect(self._changed)

    @contextlib.contextmanager
    def setting(self):
        self._quiet = True
        try:
            yield
        finally:
            self._quiet = False

    def _changed(self, position: int, removed: int, added: int) -> None:
        if self._quiet or added <= removed:
            return
        document = self._edit.document()
        length = document.characterCount() - 1
        over = length - max(self.limit, length - added + removed)
        if over <= 0:
            return
        # Into an empty box, Qt counts the paragraph's end among what was
        # added: one past the text, which the cursor refused, and a paste
        # there was never cut (R3-1, hands-on run 3).
        end = min(position + added, length)
        # After the change has been laid out: taking text out of the
        # document while it says what went into it moves what it says.
        QTimer.singleShot(0, self, lambda: self._cut(end - over, end))

    def _cut(self, start: int, end: int) -> None:
        cursor = QTextCursor(self._edit.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()


class _WrapHeight(QObject):
    """Holds a word-wrapped label at least as tall as its text needs."""

    def __init__(self, label):
        super().__init__(label)
        self._label = label
        label.installEventFilter(self)

    def apply(self) -> None:
        label = self._label
        if label.width() > 0:
            # Measured without the minimum it holds: a label's height for a
            # width is never less than that, so one measured while narrow
            # kept the lines it needed then once it was wide, and centred
            # its text in them.
            label.setMinimumHeight(0)
            label.setMinimumHeight(label.heightForWidth(label.width()))

    def eventFilter(self, watched, event) -> bool:
        if watched is self._label and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.FontChange,
        ):
            self.apply()
        return False


def keep_wrapped_text_whole(root) -> list:
    """Stop every word-wrapped label under ``root`` losing its last lines.

    Qt's layouts work out how tall wrapped text is from the width they expect
    to give it, and nested box layouts side by side can then give it a
    different one -- so the label is laid out shorter than its own text, and
    the bottom lines simply are not drawn.  Each label here instead holds a
    minimum height of whatever its *actual* width needs, re-measured when it is
    resized or its font changes; the layout honours that on its next pass.

    Returns the keepers: call ``apply()`` on them after changing a label's
    text, which moves no geometry and so tells nothing on its own.
    """
    return [_WrapHeight(label) for label in root.findChildren(QLabel) if label.wordWrap()]


def hold_lines(widget, lines: int = 2, pad: int = 4):
    """Fix ``widget``'s height at ``lines`` lines of text, and keep it there.

    A fixed height is what stops a label whose text comes and goes from walking
    the layout above it.  Measured once, it was measured in the font of the
    moment -- and changing the text size afterwards clipped the second line.
    """
    _LineHeight(widget, lines, pad)
    return widget


def hold_text_lines(edit, lines: int):
    """Fix a text box's height at ``lines`` whole lines, and keep it there
    through a change of text size or density.

    A height in pixels was a guess at the padding: the Export tab's
    Description box had 70, three lines needed 78, and moving the cursor to
    the third line scrolled the first out of sight.
    """
    _TextLines(edit, lines)
    return edit


def single_line(view):
    """Stop a one-line-per-row view dropping whole words instead of cutting them.

    ``QTableView`` word-wraps by default, and its rows here are one line tall.
    Those two together are not "wrap": they are *a word that does not fit
    disappears entirely*.  The style lays the text out wrapped, the word too
    long for the line goes to a second line nobody has the height to draw, and
    what is left is the line before it plus an ellipsis -- so a mod called
    "Copy Pools AP ConstructionCategoryBuildings" reads as "Copy Pools AP …" in
    a column with room for half of the missing word.

    Off, the same column cuts mid-word and shows every pixel it has room for,
    which is what a reader of a truncated name actually wants: enough of it to
    recognise, and the whole of it on hover.

    ``QTreeView`` and ``QListView`` already default to off, so this is about
    tables; it is a function rather than a rule anybody has to remember because
    the two tables that had it right were right by accident of having said so.
    """
    view.setWordWrap(False)
    return view


def rows_table(headings, *, one_at_a_time: bool = True) -> QTableWidget:
    """A table whose rows are read and picked whole, and not typed in.

    One line a row (``single_line``), no row numbers, every other row shaded,
    and ``one_at_a_time`` row picked. Tab leaves it and the arrow keys move in
    it: a table moves through its cells on Tab, round and round, unless it
    is told not to.
    """
    table = QTableWidget(0, len(headings))
    table.setTabKeyNavigation(False)
    single_line(table)
    table.setHorizontalHeaderLabels(list(headings))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    if one_at_a_time:
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    fit_columns(table)
    return table


def item_text_room(option):
    """``(rect, frame)``: where the style puts a cell's text, and what it
    takes off each side of that before laying the text out.

    The frame is what ``QCommonStyle::viewItemDrawText`` takes: the focus
    frame's margin and one more.  Asked here by whatever elides or paints
    beside a cell's text, so that they agree with the style and each other.
    """
    widget = option.widget
    style = widget.style() if widget is not None else QApplication.style()
    area = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, widget)
    frame = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, option, widget) + 1
    return area, frame


class ElideItemDelegate(QStyledItemDelegate):
    """Elides a cell's text against its real column width.

    Setting ``textElideMode`` alone is not enough: with a stylesheet active, an
    item paints through ``QStyleSheetStyle``, which computes its own eliding --
    and for a long string in a narrow column, that computation can collapse the
    text to a bare ellipsis instead of keeping as much of it as actually fits.
    Eliding here, with the column's real pixel width in hand, is what
    guarantees the "as much as fits" behaviour a reader expects.

    Installing one also turns word wrap off on the view it is installed on, via
    :func:`single_line`.  The two belong together: eliding a cell's text is a
    statement that the row is one line tall, and a wrapping view undoes the
    work here by eliding a second time through a layout that drops whole words.
    """

    def __init__(self, parent=None, mode=Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._mode = mode
        if isinstance(parent, QAbstractItemView):
            single_line(parent)

    def _available(self, option) -> int:
        """How many pixels the style will really give this cell's text.

        Guessed at a flat 10 before, which is close to right and not right:
        the style takes the item rule's padding *and* a focus-frame margin of
        its own, and a couple of pixels out means the text elided here is a
        couple of pixels too wide, so the style quietly elides it again --
        losing a character for nothing.  Asked of the style instead, the elide
        below is the only one that happens.
        """
        area, frame = item_text_room(option)
        return area.width() - 2 * frame

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = self._mode
        available = self._available(option)
        if option.text and available > 24:
            metrics = QFontMetrics(option.font)
            option.text = metrics.elidedText(option.text, self._mode, available)


def listing_table(headings, name: str) -> QTableWidget:
    """A table a dialog lists findings in: a row each, picked whole, read
    and never edited, each cell one line elided to its column. ``headings``
    are the column heads, translated; ``name`` is what a screen reader calls
    it. Tab leaves it for the next control; the arrows move in it."""
    table = QTableWidget(0, len(headings))
    table.setTabKeyNavigation(False)
    table.setAccessibleName(name)
    table.setHorizontalHeaderLabels(list(headings))
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    for column in range(len(headings)):
        table.setItemDelegateForColumn(column, ElideItemDelegate(table))
    fit_columns(table)
    return table


# The narrowest a column held to its view's width is left: a heading's first
# letters and an ellipsis.
FIT_MINIMUM = 48


def _share_out(weights: dict, width: int, floors: dict) -> dict:
    """``width`` pixels shared out by ``weights``, in their order, none
    narrower than its floor in ``floors``.

    A column whose share is under its floor is held at it and the others
    share what is left, so the widths still add up to ``width``: raised to
    the floor and nothing taken back, unequal shares ran past the edge of a
    view they had room in.  Only where ``width`` cannot give each column
    its floor do they come to more.
    """
    free = dict(weights)
    floored = []
    while True:
        left = width - sum(floors[logical] for logical in floored)
        total = sum(free.values())
        # Under one pixel above the floor counts as on it: a rounded edge
        # can take that pixel off.
        low = [
            logical for logical, weight in free.items()
            if not total or left * weight / total < floors[logical] + 1
        ]
        if not low:
            break
        floored += low
        for logical in low:
            del free[logical]
    sizes = {logical: floors[logical] for logical in floored}
    # Each edge rounded where it falls, so no one column takes all the
    # pixels the others' rounding left over.
    edge = 0
    running = 0.0
    for logical, weight in free.items():
        running += weight
        right = round(left * running / total)
        sizes[logical] = right - edge
        edge = right
    return {logical: sizes[logical] for logical in weights}


class FitColumns(QObject):
    """A view's columns held to its width, so it never scrolls sideways.

    Dragging a column's edge out pushed the columns after it past the right
    edge, and a tree of fields scrolled sideways to reach them: nobody wants
    to, and the room was there all along in the columns beside it. So a
    column dragged wider takes the room from the columns after it, nearest
    first, and one dragged narrower gives it to the next. The last one's
    right edge is the view's, so it is widened by moving the edge before it.

    A wider or narrower view shares the change out by each column's share
    of the width: a column dragged wide stays wide in proportion. ``keep``
    are the columns that hold their pixels instead, such as a column of
    names whose width was picked to read them by. ``shares`` are the
    columns' starting proportions where the widths they were built with
    are not them, and :meth:`shares` and :meth:`set_shares` read and set
    them again, for a view that remembers where it was left. ``floors``
    are the least some columns may be, where the header's least is not
    enough to read them by.

    Columns sized to their contents, or fixed, keep the width they have and
    the rest share what is left. A stretched column becomes one more that
    shares: the stretching was for filling the width, which this does. It
    takes over once the view is first shown, from the columns as they were
    set up by then, and only a view with a column the mouse can drag.
    """

    def __init__(self, view, shares=None, keep=(), floors=None):
        super().__init__(view)
        self.view = view
        self._given = dict(shares or {})
        self._keep = set(keep)
        self._floors = dict(floors or {})
        # Each shared column's weight and each kept one's pixels, once taken
        # over: None until then, and empty for a view this leaves alone.
        self._weights = None
        self._kept = {}
        self._laying = False
        # The header whose drags this follows, once it has taken over.
        self._listening = None
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(self.fit)
        view.viewport().installEventFilter(self)

    @property
    def header(self):
        """Asked each time: a sortable table is given a header of its own."""
        view = self.view
        return view.horizontalHeader() if hasattr(view, "horizontalHeader") else view.header()

    def _recount(self, *_counts) -> None:
        """Columns added or taken away: read them again as they are now."""
        self._weights = None
        self._kept = {}
        self.fit()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.fit()
        return False

    def _floor(self, logical) -> int:
        """The least ``logical`` may be: its own floor, or the header's."""
        return max(self._floors.get(logical, 0), self.header.minimumSectionSize())

    def _floors_of(self, columns) -> dict:
        return {logical: self._floor(logical) for logical in columns}

    def shares(self) -> dict:
        """What of the width each shared column takes, for remembering."""
        weights = self._given if self._weights is None else self._weights
        total = sum(weights.values())
        return {logical: weight / total for logical, weight in weights.items()} if total else {}

    def set_shares(self, shares) -> None:
        """Share the width out as ``shares`` say, as if dragged there."""
        if self._weights is None:
            self._given = dict(shares)
            return
        if not self._weights:
            return
        self._weights = {
            logical: shares.get(logical, weight / sum(self._weights.values()))
            for logical, weight in self._weights.items()
        }
        self.fit()

    def _shown(self) -> list:
        """The columns on show, left to right."""
        header = self.header
        return [
            logical
            for logical in (header.logicalIndex(visual) for visual in range(header.count()))
            if not header.isSectionHidden(logical)
        ]

    def _shared(self) -> list:
        """The columns this lays out, left to right."""
        return [
            logical for logical in self._shown()
            if logical in self._weights or logical in self._kept
        ]

    def _available(self) -> int:
        """The width the shared columns have between them."""
        return self.view.viewport().width() - sum(
            self.header.sectionSize(logical)
            for logical in self._shown()
            if logical not in self._weights and logical not in self._kept
        )

    def _take_over(self) -> bool:
        """Read the columns as they were set up; whether this is laying them
        out now."""
        if self._weights is not None:
            return bool(self._weights or self._kept)
        if not self.view.isVisible() or self.view.viewport().width() <= 0:
            return False
        header = self.header
        Mode = QHeaderView.ResizeMode
        shown = self._shown()
        stretched = [
            logical for logical in shown if header.sectionResizeMode(logical) == Mode.Stretch
        ]
        dragged = [
            logical for logical in shown if header.sectionResizeMode(logical) == Mode.Interactive
        ]
        if header.stretchLastSection() and dragged and dragged[-1] == shown[-1]:
            stretched.append(dragged.pop())
        if not dragged:
            # Nothing the mouse can drag past the edge: Qt's own stretching
            # already fills the width.
            self._weights = {}
            return False
        shared = [logical for logical in shown if logical in dragged or logical in stretched]
        if self._listening is not header:
            self._listening = header
            header.sectionResized.connect(self._resized)
            header.sectionCountChanged.connect(self._recount)
        self._laying = True
        try:
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(max(header.minimumSectionSize(), FIT_MINIMUM))
            for logical in stretched:
                header.setSectionResizeMode(logical, Mode.Interactive)
        finally:
            self._laying = False
        keep = [logical for logical in shared if logical in self._keep]
        if len(keep) == len(shared):
            keep = []
        self._kept = {logical: header.sectionSize(logical) for logical in keep}
        rest = [logical for logical in shared if logical not in keep]
        if self._given:
            self._weights = {logical: self._given.get(logical, 1.0 / len(rest)) for logical in rest}
        else:
            # A stretched column was as wide as the room the others left;
            # its own width is stale until the header next lays itself out.
            others = sum(header.sectionSize(logical) for logical in shared if logical not in stretched)
            room = (self.view.viewport().width() - sum(
                header.sectionSize(logical) for logical in shown if logical not in shared
            ) - others) / max(1, len(stretched))
            self._weights = {
                logical: max(self._floor(logical), room) if logical in stretched
                else header.sectionSize(logical)
                for logical in rest
            }
        return True

    def fit(self) -> None:
        """Lay the columns out across the view's width."""
        if self._laying or not self._take_over():
            return
        shared = self._shared()
        floors = self._floors_of(shared)
        kept = {logical: self._kept[logical] for logical in shared if logical in self._kept}
        rest = [logical for logical in shared if logical in self._weights]
        available = self._available()
        # The kept columns give way only when the others are down to their
        # narrowest, and get their width back when the room comes back.
        room = available - sum(floors[logical] for logical in rest)
        held = sum(kept.values())
        if held > room and held:
            kept = _share_out(kept, room, floors)
        sizes = dict(kept)
        sizes.update(_share_out(
            {logical: self._weights[logical] for logical in rest},
            available - sum(kept.values()), floors,
        ))
        self._set(sizes)

    def _set(self, sizes) -> None:
        self._laying = True
        try:
            for logical, width in sizes.items():
                if self.header.sectionSize(logical) != width:
                    self.header.resizeSection(logical, width)
        finally:
            self._laying = False

    def _resized(self, logical: int, old: int, new: int) -> None:
        """A column dragged: the room comes from, or goes to, the ones after it."""
        if self._laying or (not self._weights and not self._kept):
            return
        shared = self._shared()
        if logical not in shared:
            # A column sized to its contents has changed its mind. Laid out
            # once the header has finished: in the middle of sizing its
            # columns, it puts back any other width set.
            self._refit.start()
            return
        after = shared[shared.index(logical) + 1:]
        sizes = {}
        if after and new < self._floor(logical):
            # The header holds a drag to its own least, not to a column's.
            new = sizes[logical] = self._floor(logical)
        delta = new - old
        if not after:
            # The last one's right edge is the view's: undone, and the
            # shares left as they were rather than read back in pixels.
            self._set({logical: old})
            return
        if delta > 0:
            taken = 0
            for other in after:
                width = self.header.sectionSize(other)
                bite = min(max(0, width - self._floor(other)), delta - taken)
                if bite:
                    sizes[other] = width - bite
                    taken += bite
                if taken == delta:
                    break
            if taken < delta:
                sizes[logical] = old + taken
        elif delta < 0:
            sizes[after[0]] = self.header.sectionSize(after[0]) - delta
        self._set(sizes)
        for column in shared:
            if column in self._kept:
                self._kept[column] = self.header.sectionSize(column)
            else:
                self._weights[column] = self.header.sectionSize(column)


def fit_columns(view, shares=None, keep=(), floors=None) -> FitColumns:
    """Hold ``view``'s columns to its width: see :class:`FitColumns`. Kept as
    the view's ``column_fit``."""
    view.column_fit = FitColumns(view, shares, keep, floors)
    return view.column_fit


def flash_button_feedback(button: QPushButton, duration_ms: int = 1200) -> None:
    """Briefly relabel ``button`` to confirm a silent action (e.g. a clipboard copy).

    A Copy button that only ever changes the clipboard gives no sign it did
    anything -- this swaps in a confirmation label for a moment, then restores
    the button's own text, wherever the click came from.  ``duration_ms`` is
    how long the label stays; the app leaves it be, and the tests shorten it.

    The button stays enabled throughout. Disabling it for the flash threw the
    keyboard focus to whatever came next, so someone who pressed it with the
    keyboard lost their place.
    """
    # Its own text, remembered once: a second press during the flash must
    # not take "Copied!" for it.
    original = button.property("flash_original")
    if original is None:
        original = button.text()
        button.setProperty("flash_original", original)
    button.setText(tr("Copied!"))
    count = int(button.property("flash_count") or 0) + 1
    button.setProperty("flash_count", count)

    def _restore() -> None:
        try:
            # Only the latest press puts the text back.
            if int(button.property("flash_count") or 0) != count:
                return
            button.setText(original)
            button.setProperty("flash_original", None)
        except RuntimeError:
            pass  # the button (or its window) was closed before the timer fired

    QTimer.singleShot(duration_ms, _restore)


class _LeastSize(QObject):
    """Holds a dialog to the larger of a least size and its layout's: see
    :func:`least_size`."""

    def __init__(self, dialog, floor: QSize) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._floor = floor
        dialog.installEventFilter(self)
        self.apply()

    def apply(self) -> None:
        dialog = self._dialog
        least = QSize(self._floor)
        layout = dialog.layout()
        if layout is not None:
            least = least.expandedTo(layout.totalMinimumSize())
        screen = dialog.screen()
        if screen is not None:
            least = least.boundedTo(screen.availableGeometry().size())
        if dialog.minimumSize() != least:
            dialog.setMinimumSize(least)

    def eventFilter(self, watched, event):
        # Polish comes before the first show sizes the dialog.
        if event.type() in (QEvent.Type.Polish, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.apply()
        return False


def least_size(dialog, width: int, height: int) -> None:
    """Never let ``dialog`` be smaller than ``width`` by ``height``, nor than
    what its layout needs.

    A size set with setMinimumSize alone stands in for the layout's: Qt then
    lets the dialog be smaller than its contents. It opens no taller or wider
    than two thirds of the screen, so at a large interface size on a small
    screen the contents were squashed, buttons cut at both ends and fields
    half their height. Kept up as the contents change, and never larger than
    the screen.
    """
    _LeastSize(dialog, QSize(width, height))


def keep_size(dialog, settings, key: str, first: QSize) -> None:
    """Open ``dialog`` at the size it was closed at last time -- ``first``
    the first time -- and write down the size it closes at, under ``key``.

    Never larger than the screen it opens on, which may not be the one it
    was made that size on, and never smaller than its least size.
    ``settings`` None keeps nothing: a dialog made with no window behind it.
    """
    kept = None
    if settings is not None:
        try:
            width, height = (int(part) for part in str(settings.value(key) or "").split(","))
        except ValueError:
            pass
        else:
            if width > 0 and height > 0:
                kept = QSize(width, height)
    size = (kept or first).expandedTo(dialog.minimumSize())
    screen = (dialog.parentWidget() or dialog).screen()
    if screen is not None:
        size = size.boundedTo(screen.availableGeometry().size())
    dialog.resize(size)
    if settings is None:
        return

    def remember(*_) -> None:
        # Under a maximised dialog is the size it goes back to.
        rect = dialog.normalGeometry() if dialog.isMaximized() else dialog.geometry()
        settings.setValue(key, "%d,%d" % (rect.width(), rect.height()))

    dialog.finished.connect(remember)


class _EnterActivates(QObject):
    """Turns Enter on a row into what double-click does, and stops it there."""

    def __init__(self, view, slot=None) -> None:
        super().__init__(view)
        self._view = view
        self._slot = slot

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return False
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return False
        view = self._view
        index = view.currentIndex()
        if not index.isValid() or view.state() == QAbstractItemView.State.EditingState:
            return False
        if self._slot is not None:
            self._slot(index)
        else:
            view.activated.emit(index)
        return True


class _WheelPassesBy(QObject):
    """Sends a wheel tick over a combo box or spin box on to what is behind it."""

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, _WHEEL_PASSES):
            return False
        # Ignored and filtered out, the tick goes on to the parent as one the
        # control did not want: the scroll area behind it scrolls.
        event.ignore()
        return True


def wheel_passes_by_choices(app) -> None:
    """Keeps the mouse wheel off combo boxes and spin boxes, everywhere.

    Scrolling a dialog, a tick that came under a combo box changed the
    setting in it, and the dialog stopped scrolling there.  On a combo box
    whose every choice asks something -- Currently editing -- each tick was
    a question.  A choice is made with a click or the keyboard; the wheel
    scrolls.  A combo box's open list still scrolls: it is a list.
    """
    if getattr(app, "_wheel_filter", None) is not None:
        return
    app._wheel_filter = _WheelPassesBy(app)
    app.installEventFilter(app._wheel_filter)


_WHEEL_PASSES = (QComboBox, QAbstractSpinBox)


def caret_follows_focus(app) -> None:
    """Keeps a Windows caret where the text cursor is, in every text field.

    The clipboard history (Win+V), the emoji panel and Magnifier's "follow
    the keyboard" find the text cursor by the window's system caret. Qt draws
    its own and makes an invisible system one only while the input panel is
    asked for, which a click on a field already focused did and nothing else
    did: so Win+V opened in the corner of the screen, far from the field. It
    is asked for whenever a field that takes text has the focus, and let go
    when the focus leaves for anything else. Qt keeps the caret on the text
    cursor from then on.
    """
    if sys.platform != "win32" or getattr(app, "_caret_follows", False):
        return
    app._caret_follows = True
    from PySide6.QtGui import QGuiApplication, QInputMethod

    def focus_moved(_focus) -> None:
        method = QGuiApplication.inputMethod()
        if QInputMethod.queryFocusObject(Qt.InputMethodQuery.ImEnabled, None):
            method.show()
        else:
            method.hide()

    app.focusObjectChanged.connect(focus_moved)


# What a cell sorts as, where its text is not it: a number written with its
# thousands apart, a size in MB, a date.  See sort_as.
SORT_ROLE = Qt.ItemDataRole.UserRole + 97
_DIGITS = re.compile(r"(\d+)")
# Which way a column is sorted, written after its heading.  Not the header's
# own sort indicator: shown, that makes room for an arrow in every heading,
# and a table of ten narrow columns no longer fits its window.
_ARROWS = {Qt.SortOrder.AscendingOrder: " ▲", Qt.SortOrder.DescendingOrder: " ▼"}


def _headed(text: str, arrow: str = "") -> str:
    """A heading with ``arrow`` after it, in place of any it had."""
    for old in _ARROWS.values():
        text = text.removesuffix(old)
    return text + arrow


def sort_as(item, value):
    """Have ``item`` sort as ``value`` and not as the text it shows."""
    item.setData(SORT_ROLE, value)
    return item


def _natural(text: str) -> tuple:
    """Text as it sorts for a reader: without case, and "v2" before "v10"."""
    return tuple(
        (0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk)
        for chunk in _DIGITS.split(text.casefold()) if chunk
    )


def _sort_key(value, text: str, ticked=None):
    """What a cell sorts by: ``value`` where :func:`sort_as` gave it one,
    else its text -- or None for a cell with nothing in it."""
    if isinstance(value, (int, float)):
        return (0, value, ())
    text = value if isinstance(value, str) else text
    if not text.strip():
        if value is None and ticked is not None:
            # A column of ticks: the ticked ones first.
            return (0, ticked != Qt.CheckState.Checked, ())
        return None
    return (1, 0, _natural(text.strip()))


class _HeaderSort(QObject):
    """A table sorted by the column whose heading is clicked.

    A click sorts by the column, the next turns it round, and the third puts
    the rows back as they were filled -- which in most tables here is an
    order that says something, load order being one.

    The rows are not moved, only drawn in another order: the vertical header
    is what is rearranged.  Every table here answers "which row" by its
    number -- into the list it was filled from -- and a sort that moved the
    items would have each of them answering for another row.
    """

    def __init__(self, table, columns=None, under=None):
        super().__init__(table)
        self.table = table
        self.columns = None if columns is None else set(columns)
        self.under = under
        self.column = -1
        self.order = Qt.SortOrder.AscendingOrder
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._clicked)
        # A table filled again is drawn in the order it was sorted in.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.apply)
        model = table.model()
        for changed in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            changed.connect(lambda *_: self._timer.start())
        # Whether this took the table's stripes away -- see _stripes.  A row
        # hidden or shown again is resized, to nothing or back: asked once
        # the hiding is done.
        self._unstriped = False
        self._striping = QTimer(self)
        self._striping.setSingleShot(True)
        self._striping.setInterval(0)
        self._striping.timeout.connect(self._stripes)
        table.verticalHeader().sectionResized.connect(lambda *_: self._striping.start())

    def _clicked(self, column: int) -> None:
        if self.columns is not None and column not in self.columns:
            return
        if column != self.column:
            self.sort(column, Qt.SortOrder.AscendingOrder)
        elif self.order == Qt.SortOrder.AscendingOrder:
            self.sort(column, Qt.SortOrder.DescendingOrder)
        else:
            self.sort(-1)

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        """Sort by ``column``; -1 is the order the table was filled in."""
        self.column, self.order = column, order
        self._show()
        self.apply()

    def _show(self) -> None:
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setText(_headed(
                    item.text(), _ARROWS[self.order] if column == self.column else "",
                ))

    def _key(self, row: int):
        """What a row sorts by, or None for a cell with nothing in it."""
        item = self.table.item(row, self.column)
        if item is None:
            return None
        return _sort_key(
            item.data(SORT_ROLE), item.text(),
            # By whether it has a tick: every item is checkable by its flags.
            None if item.data(Qt.ItemDataRole.CheckStateRole) is None else item.checkState(),
        )

    def apply(self) -> None:
        self._timer.stop()
        rows = range(self.table.rowCount())
        if self.column < 0 or self.column >= self.table.columnCount():
            wanted = list(rows)
        else:
            keys = {row: self._key(row) for row in rows}
            wanted = sorted(
                (row for row in rows if keys[row] is not None),
                key=keys.get, reverse=self.order == Qt.SortOrder.DescendingOrder,
            )
            # Nothing to sort by is last, whichever way round.
            wanted.extend(row for row in rows if keys[row] is None)
        if self.under is not None:
            wanted = _grouped(wanted, self.under)
        vertical = self.table.verticalHeader()
        self.table.setUpdatesEnabled(False)
        try:
            for position, row in enumerate(wanted):
                at = vertical.visualIndex(row)
                if at != position:
                    vertical.moveSection(at, position)
            self._stripes()
        finally:
            self.table.setUpdatesEnabled(True)

    def _stripes(self) -> None:
        """Take the table's stripes away while its rows are drawn in another
        order and some are hidden, and give them back after.

        Qt's table, striped and with rows hidden, works out where to start
        drawing by adding up the rows' heights in the order they were filled
        and counting that in the order they are drawn: every row drawn above
        the first one shown in fill order is skipped.  A filtered list,
        sorted, had its first rows blank -- no tick, no text -- though a
        click there still ticked them.  Unstriped, it starts where it should.
        """
        self._striping.stop()
        vertical = self.table.verticalHeader()
        meet = vertical.sectionsHidden() and any(
            vertical.logicalIndex(position) != position for position in range(vertical.count())
        )
        if meet and self.table.alternatingRowColors():
            self._unstriped = True
            self.table.setAlternatingRowColors(False)
        elif not meet and self._unstriped:
            self._unstriped = False
            self.table.setAlternatingRowColors(True)


def _grouped(rows, under) -> list:
    """``rows`` in their order, each one ``under`` names another row for
    moved to straight below that row, among the others below it in the same
    order: a row is drawn as part of the one above it, however it sorts."""
    present = set(rows)
    below = {}
    tops = []
    for row in rows:
        above = under(row)
        if above is None or above == row or above not in present:
            tops.append(row)
        else:
            below.setdefault(above, []).append(row)
    found = []
    waiting = list(reversed(tops))
    while waiting:
        row = waiting.pop()
        found.append(row)
        waiting.extend(reversed(below.get(row, ())))
    return found


class HeaderTick(QCheckBox):
    """The box in a table's heading over its first column, of ticks, in place of
    a *Tick all* and a *Tick none* under the table.  Ticked where every row
    it would tick is, empty where no row is, and a dash between; clicked,
    it ticks those rows, or unticks every row where they all are already.

    ``state`` says how the rows stand, as a ``Qt.CheckState``, and
    ``tick(on)`` ticks or unticks them.  A table that changes its rows
    without a signal -- filled again, filtered -- calls :meth:`sync`.

    A box of its own and not one drawn by the heading: it takes focus, and
    Space ticks it, as every other box does.  Left in the column, where the
    rows' boxes are, with room after it for the arrow a sort by the ticks
    puts in the heading.
    """

    def __init__(self, table, state, tick, name: str):
        header = table.horizontalHeader()
        super().__init__(header.viewport())
        self.table = table
        self._state, self._tick = state, tick
        self.setTristate(True)
        self.setAccessibleName(name)
        self.setToolTip(name)
        heading = table.horizontalHeaderItem(0)
        if heading is not None:
            heading.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        arrow = QFontMetrics(header.font()).horizontalAdvance(_ARROWS[Qt.SortOrder.AscendingOrder])
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, self.sizeHint().width() + arrow + 3 * self.MARGIN)
        header.sectionResized.connect(lambda *_: self._place())
        header.geometriesChanged.connect(self._place)
        table.horizontalScrollBar().valueChanged.connect(lambda *_: self._place())
        table.itemChanged.connect(lambda *_: self.sync())
        self._place()
        self.sync()

    # Between the column's left edge and the box: where a row's box is.
    MARGIN = 4

    def nextCheckState(self) -> None:
        self.tick(self.checkState() != Qt.CheckState.Checked)

    def tick(self, on: bool) -> None:
        """Tick the rows it ticks, or untick them, as a click would."""
        self._tick(on)
        self.sync()

    def sync(self) -> None:
        """Show how the rows stand now."""
        was = self.blockSignals(True)
        try:
            self.setCheckState(self._state())
        finally:
            self.blockSignals(was)

    def _place(self) -> None:
        header = self.table.horizontalHeader()
        size = self.sizeHint()
        self.setGeometry(
            header.sectionViewportPosition(0) + self.MARGIN,
            max(0, (header.height() - size.height()) // 2),
            size.width(), size.height(),
        )


def ticks_state(table, rows, takes) -> Qt.CheckState:
    """How the ticks of ``rows`` of ``table`` stand for a :class:`HeaderTick`:
    unticked where none of them is, ticked where every one of ``takes`` --
    the rows it would tick -- is, and partly between."""
    ticked = {row for row in rows if table.item(row, 0).checkState() == Qt.CheckState.Checked}
    if not ticked:
        return Qt.CheckState.Unchecked
    if all(row in ticked for row in takes):
        return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked


def tick_shown(table, name: str, after=None) -> HeaderTick:
    """A :class:`HeaderTick` over the rows of ``table`` a filter leaves
    shown, its ticks in the first column: ticked where every one of them is,
    and ticking or unticking them, while a hidden row keeps its tick -- the
    filter narrows the list, not the choice.  ``after`` is called once the
    rows are ticked, the table having said nothing of it."""

    def shown() -> list:
        return [row for row in range(table.rowCount()) if not table.isRowHidden(row)]

    def state():
        rows = shown()
        return ticks_state(table, rows, rows)

    def tick(on: bool) -> None:
        table.blockSignals(True)
        for row in shown():
            table.item(row, 0).setCheckState(
                Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            )
        table.blockSignals(False)
        if after is not None:
            after()

    return HeaderTick(table, state, tick, name)


def sortable(table, columns=None, under=None) -> _HeaderSort:
    """Let a click on a heading of ``table`` sort by that column: up, down,
    and on the third click back to the order it was filled in.

    ``columns`` narrows it to the columns worth sorting by; left out, it is
    all of them.  A cell sorts by its text unless :func:`sort_as` gave it
    something else to sort as.  ``under`` is, for a row, the row it is drawn
    straight below whichever way the table is sorted, or None: the rows below
    one row are sorted among themselves.
    """
    return _HeaderSort(table, columns, under)


class _TreeSort(QObject):
    """:class:`_HeaderSort` for a tree that is a flat list: its top-level
    items, which a tree has no way to draw in another order than they are
    in, are put in that order.  Each is known by itself and not by where it
    is, so nothing answers for another row."""

    _PLACE = SORT_ROLE + 1

    def __init__(self, tree, columns=None):
        super().__init__(tree)
        self.tree = tree
        self.columns = None if columns is None else set(columns)
        self.column = -1
        self.order = Qt.SortOrder.AscendingOrder
        self._moving = False
        header = tree.header()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._clicked)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.apply)
        tree.model().rowsInserted.connect(self._filled)

    def _filled(self, parent, *_rows) -> None:
        if not self._moving and not parent.isValid():
            self._timer.start()

    _clicked = _HeaderSort._clicked
    sort = _HeaderSort.sort

    def _show(self) -> None:
        item = self.tree.headerItem()
        for column in range(self.tree.columnCount()):
            item.setText(column, _headed(
                item.text(column), _ARROWS[self.order] if column == self.column else "",
            ))

    def apply(self) -> None:
        self._timer.stop()
        tree = self.tree
        items = [tree.topLevelItem(at) for at in range(tree.topLevelItemCount())]
        # Where each was put when the tree was filled: what "unsorted" is.
        placed = [item.data(0, self._PLACE) for item in items]
        after = max((place for place in placed if place is not None), default=-1)
        for item, place in zip(items, placed, strict=True):
            if place is None:
                after += 1
                item.setData(0, self._PLACE, after)
        wanted = sorted(items, key=lambda item: item.data(0, self._PLACE))
        if 0 <= self.column < tree.columnCount():
            keys = {
                id(item): _sort_key(item.data(self.column, SORT_ROLE), item.text(self.column))
                for item in wanted
            }
            valued = sorted(
                (item for item in wanted if keys[id(item)] is not None),
                key=lambda item: keys[id(item)],
                reverse=self.order == Qt.SortOrder.DescendingOrder,
            )
            wanted = valued + [item for item in wanted if keys[id(item)] is None]
        if wanted == items:
            return
        current = tree.currentItem()
        chosen = tree.selectedItems()
        open_ = [item for item in items if item.isExpanded()]
        self._moving = True
        tree.setUpdatesEnabled(False)
        try:
            for _ in items:
                tree.takeTopLevelItem(0)
            tree.addTopLevelItems(wanted)
            for item in open_:
                item.setExpanded(True)
            if current is not None:
                tree.setCurrentItem(current)
            for item in chosen:
                item.setSelected(True)
        finally:
            tree.setUpdatesEnabled(True)
            self._moving = False


def sortable_tree(tree, columns=None) -> _TreeSort:
    """:func:`sortable` for a tree used as a flat list."""
    return _TreeSort(tree, columns)


def heading_row(parent, text: str) -> QTreeWidgetItem:
    """A row of a tree that heads the rows under it, across every column
    and in the heavier weight."""
    item = QTreeWidgetItem(parent, [text])
    item.setFirstColumnSpanned(True)
    font = item.font(0)
    font.setWeight(font.Weight.DemiBold)
    item.setFont(0, font)
    return item


def enter_activates(view, slot=None):
    """Make Enter on ``view``'s current row do what double-click does.

    Connect the action to ``activated`` or ``itemActivated``, which a
    double-click emits too. Qt emits it for Enter as well, and then passes
    the key on, where a dialog's default button takes it: Enter on a row
    would open the row and close the dialog. This keeps the key at the view.

    ``slot`` is for a view where the keyboard needs its own answer. It is
    called with the current index, and nothing is emitted.
    """
    view.installEventFilter(_EnterActivates(view, slot))
    return view


class ElidingLabel(QLabel):
    """A QLabel that elides its text to fit, instead of forcing its layout wider.

    A plain QLabel's minimumSizeHint grows with its text -- harmless for a
    short, fixed caption, but for text that reflects live state (a full file
    path, say) it means every character typed or appended drags the label's
    whole layout, and the window along with it, wider to keep showing all of
    it unclipped. This elides against whatever width the layout actually gives
    it and keeps the untruncated string available as the tooltip.
    """

    def __init__(self, mode=Qt.TextElideMode.ElideMiddle, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._full_text = ""
        self.setMinimumWidth(0)

    def full_text(self) -> str:
        return self._full_text

    def setFullText(self, text: str) -> None:
        self._full_text = text
        self._apply_elision()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def minimumSizeHint(self) -> QSize:
        # Deliberately not width-of-current-text: that would feed the elided
        # text's own (already-clamped-to-current-width) width back in as a
        # requirement, nudging the layout a little wider on every resize.
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full_text, self._mode, self.width()))


# Qt offers a line break *before* a backslash and none after it, so a Windows
# path in any wrapped label -- every QMessageBox is one -- comes apart as "C:"
# alone on the first line with the whole of the rest on the second, and a path
# deep enough to need a third line has nowhere left to break at all.  A
# zero-width space after each separator puts the opportunity where a reader
# expects it, at the end of a folder name, and takes the one before the
# separator out of the running because a later fit always wins.
PATH_BREAK = "\u200b"


class DropHint(QWidget):
    """Says, over the whole of ``parent``, that what is being dragged over it
    can be let go here: a veil, a dashed frame in the accent, and ``text``.

    The whole window takes the drop, so the whole window is marked.  Hidden
    until :meth:`cover` is called, and nothing the mouse can reach: a drag
    goes through it to the widgets underneath, as before.
    """

    MARGIN = 16

    def __init__(self, text: str, parent) -> None:
        super().__init__(parent)
        self.text = text
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def cover(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        veil = QColor(theme.colour("bg"))
        veil.setAlphaF(0.82)
        painter.fillRect(self.rect(), veil)
        frame = self.rect().adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)
        pen = QPen(QColor(theme.colour("accent")), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(frame, 8, 8)
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_PX + 8)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(theme.colour("text")))
        painter.drawText(frame, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.text)


# Around a value in a box's text that :func:`named` set apart. Characters of
# Unicode's private use area, so neither a name nor a translation holds one.
_NAMED_START, _NAMED_END = "", ""


def named(value) -> str:
    """``value`` set apart in a question box's text: a mod's, a loadout's or
    a collection's name, which somebody else wrote and which runs on with
    the sentence around it.

    Only for text a box shows through :func:`show_box_text` (``ask``,
    ``choose`` and ``tell`` do), which sets it in the heavier weight.  It is
    put in through ``tr``'s values, so no sentence a translator sees holds
    markup.  Anywhere else the marks would show: a window title, a status
    line or a tooltip takes the name as it is.
    """
    return "%s%s%s" % (_NAMED_START, value, _NAMED_END)


def box_markup(text: str) -> str:
    """``text`` as the rich text :func:`show_box_text` shows: every character
    of it escaped, line breaks and runs of spaces kept, each value
    :func:`named` marked in the heavier of the two weights, and each keyword
    ``tr_kw`` kept made a link to its meaning (``help_tip.KeywordTips``)."""
    import html
    from urllib.parse import quote

    escaped = html.escape(text, quote=False)
    escaped = re.sub(r" {2,}", lambda run: "&nbsp;" * len(run.group()), escaped)
    escaped = escaped.replace("\n", "<br>")
    escaped = re.sub(
        TERM_START + "([^" + TERM_WORDS + "]*)" + TERM_WORDS + "([^" + TERM_END + "]*)" + TERM_END,
        lambda term: '<a href="term:%s">%s</a>' % (quote(term.group(1)), term.group(2)),
        escaped,
    )
    return escaped.replace(_NAMED_START, '<span style="font-weight:600">').replace(
        _NAMED_END, "</span>")


def plain_text(text: str) -> str:
    """Text made for a box, as plain words: no name set apart and no keyword
    marked. For where the same words go somewhere plain, such as a screen
    reader's description."""
    return unmarked(text).replace(_NAMED_START, "").replace(_NAMED_END, "")


def show_box_text(box, text: str) -> None:
    """Put ``text`` in a message box, with the names in it in the heavier
    weight (:func:`named`) and its keywords pointing at their meaning.

    The box reads it as rich text it was told is rich, so nothing in a name
    is ever taken for markup (:func:`external_text_stays_plain`): all of it
    is escaped first, and the only markup is the weight put round a name and
    the link round a keyword, whose meaning opens beside it and goes nowhere.
    """
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(box_markup(text))
    if terms_in_text(text):
        from .help_tip import KeywordTips

        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            KeywordTips.watch(label, text)


def terms_in_text(text: str) -> list:
    """The keys of the keywords ``tr_kw`` kept in ``text``, in order."""
    return re.findall(TERM_START + "([^" + TERM_WORDS + "]*)" + TERM_WORDS, text)


def tip(button, text: str) -> None:
    """What clicking ``button`` leads to, on it: its tooltip, and its
    description for a screen reader, which reads it as the button takes the
    focus.  A box's text says what the question is about, and each answer's
    tip what that answer does."""
    if text:
        button.setToolTip(text)
        button.setAccessibleDescription(text)


def ask(parent, title: str, text: str, action: str, *, cancel: str = "",
        default_action: bool = True, action_tip: str = "", cancel_tip: str = "") -> bool:
    """Ask a question whose answer is an action, and say whether to do it.

    The button that does it carries the action's name (``action``), so the
    box reads without its text: "Restore" and "Cancel", where it said "Yes"
    and "Cancel". ``cancel`` names the other button where declining is also
    an action ("Keep them"). The default is the action unless it destroys
    something, when ``default_action`` is False.  ``action_tip`` and
    ``cancel_tip`` say more of what each leads to (:func:`tip`).
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    show_box_text(box, text)
    go = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    stay = (box.addButton(cancel, QMessageBox.ButtonRole.RejectRole) if cancel
            else box.addButton(QMessageBox.StandardButton.Cancel))
    tip(go, action_tip)
    tip(stay, cancel_tip)
    box.setDefaultButton(go if default_action else stay)
    box.setEscapeButton(stay)
    return answer_of(box) is go


def choose(parent, title: str, text: str, choices, *, cancel: str = "", default: int = 0,
           icon=QMessageBox.Icon.Question, check=None, check_said: str = "") -> int:
    """Ask which of several actions to take: the place in ``choices`` of
    the one clicked, or -1 for Cancel.

    ``choices`` is ``[(label, tip), ...]``.  Each button is named for its
    action, and its tip (:func:`tip`) says what it leads to, so ``text``
    says what the question is about and stops: a sentence or two, with no
    sentence for each button.  ``cancel`` names the button that declines
    where declining is an action too.  ``default`` is the place of the
    default button, or -1 for Cancel where every choice changes something
    that is hard to take back.  ``check`` is a checkbox to show under the
    question, which the caller reads afterwards, and ``check_said`` what
    ticking it leads to, in a line beside it.
    """
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    show_box_text(box, text)
    buttons = []
    for label, said in choices:
        button = box.addButton(label, QMessageBox.ButtonRole.AcceptRole)
        tip(button, said)
        buttons.append(button)
    stay = (box.addButton(cancel, QMessageBox.ButtonRole.RejectRole) if cancel
            else box.addButton(QMessageBox.StandardButton.Cancel))
    if check is not None:
        box.setCheckBox(check)
    if check_said:
        box.setInformativeText(check_said)
    box.setDefaultButton(buttons[default] if 0 <= default < len(buttons) else stay)
    box.setEscapeButton(stay)
    clicked = answer_of(box)
    return next((at for at, button in enumerate(buttons) if button is clicked), -1)


def tell(parent, title: str, text: str, *, warning: bool = False) -> None:
    """Say ``text`` in a box with one button, with the names in it in the
    heavier weight (:func:`named`)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    show_box_text(box, text)
    box.addButton(QMessageBox.StandardButton.Ok)
    answer_of(box)


def answer_of(box):
    """The button ``box`` was answered with. Apart from ``ask`` so that a test
    can answer every question in one place."""
    box.exec()
    return box.clickedButton()


def laid_out(layout) -> list:
    """The widgets directly in ``layout``, in the order it lays them out; not
    those in a layout inside it, and none while the layout is on no widget.

    Found by ``indexOf`` among the children of the widget the layout is on,
    never through ``layout.itemAt``. A layout's items are not QObjects, and
    PySide keeps the wrapper ``itemAt`` gives for as long as the layout
    lives, still registered at the item's address. Qt deletes items without
    telling it: a button box makes its items again when a button is added or
    hidden, and when the application's style sheet changes, as it does with
    every theme. A Qt object made later where an item was is then taken for
    the item's wrapper, and connecting a signal to it read through a null
    pointer -- the suite's access violations in ``XmlHighlighter`` (18 Oct).
    """
    holder = layout.parentWidget()
    if holder is None:
        return []
    found = [
        child for child in holder.findChildren(
            QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
        if layout.indexOf(child) >= 0
    ]
    found.sort(key=layout.indexOf)
    return found


def tab_as_laid_out(box) -> None:
    """Make Tab go through a ``QDialogButtonBox``'s buttons in the order they
    are laid out.

    A button box keeps the order its buttons were made in. So Cancel, made
    with the box, came before the button added beside it, which Windows lays
    out to its left. Call it once the box has all its buttons.

    The box takes Tab while the order is set. Qt leaves the order alone where
    it holds already by going round the box, and with nothing in the box to
    stop on, Publish then Cancel holds by going round from Cancel.
    """
    buttons = laid_out(box.layout())
    was = box.focusPolicy()
    box.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    for first, then in itertools.pairwise(buttons):
        QWidget.setTabOrder(first, then)
    box.setFocusPolicy(was)


def add_detours(dialog, box, detours) -> None:
    """Buttons on ``box`` that leave ``dialog`` for another way to the same
    end: the mods it adds, got from somewhere else.  Each closes the dialog
    with ``dialog.detour`` set to what it is to call, for whoever opened it
    to call once it is shut -- see ``LibraryController.add_mods``.  With no
    detours, ``dialog.detour`` is None all the same.

    ``detours`` is ``(label, tooltip, call)`` for each.  Called before
    :func:`tab_as_laid_out`, which has to see every button.
    """
    dialog.detour = None
    for label, tip, call in detours:
        # ResetRole for where Windows lays it out: at the far left, apart
        # from the dialog's own Import and Cancel. The box does nothing of
        # its own for the role -- no accepted, no rejected -- so the click is
        # all there is.
        button = box.addButton(label, QDialogButtonBox.ButtonRole.ResetRole)
        button.setToolTip(tip)
        button.clicked.connect(lambda _checked=False, call=call: _take_detour(dialog, call))


def _take_detour(dialog, call) -> None:
    dialog.detour = call
    dialog.reject()


def _tab_stops(widget) -> list:
    """``widget`` and the controls in it that Tab stops on, in the order made."""
    return [
        each for each in (widget, *widget.findChildren(QWidget))
        if each.focusPolicy() & Qt.FocusPolicy.TabFocus and each.focusProxy() is None
    ]


def tab_after(before, widget):
    """Put ``widget``, and the controls in it, next after ``before`` in Tab
    order. Give back the last of them, for the next to go after; ``before``
    if none takes Tab.

    A widget added once its window is built goes to the end of the window's
    Tab order, wherever it is laid out: the Start page's games were reached
    after everything in the Mod changes panel.
    """
    for stop in _tab_stops(widget):
        QWidget.setTabOrder(before, stop)
        before = stop
    return before


def add_in_tab_order(layout, widget) -> None:
    """Add ``widget`` to the end of ``layout``, and to Tab order after the
    last control already in it."""
    before = None
    for there in laid_out(layout):
        stops = _tab_stops(there)
        if stops:
            before = stops[-1]
    layout.addWidget(widget)
    if before is not None:
        tab_after(before, widget)


def filter_box(placeholder: str, name: str, on_text) -> QLineEdit:
    """A box that narrows a list as it is typed into: ``placeholder`` in it
    while empty, ``name`` for a screen reader, ``on_text`` called with the
    text at each change."""
    box = QLineEdit()
    box.setPlaceholderText(placeholder)
    box.setAccessibleName(name)
    box.setClearButtonEnabled(True)
    box.textChanged.connect(on_text)
    return box


def shown_row(form, caption: str, value) -> QLabel:
    """Add a row to ``form`` whose value is only read: ``caption`` beside
    ``value``, a label or a layout of them.

    ``QFormLayout.addRow`` makes the value its caption's buddy, which tells a
    screen reader the caption names a field, and there is no field here.
    """
    label = QLabel(caption)
    form.addRow(label, value)
    label.setBuddy(None)
    return label


def listing(names) -> str:
    """"A", "A and B", "A, B and 3 more"."""
    names = list(names)
    if len(names) <= 2:
        return join_list(names)
    return trn(
        "%(first)s, %(second)s and %(n)s more",
        "%(first)s, %(second)s and %(n)s more",
        len(names) - 2, first=names[0], second=names[1],
    )


def wrappable_path(path) -> str:
    """``path`` as text that wraps at its separators rather than after "C:".

    For display only: the breaks are real characters, so what comes back is
    for showing, never for reopening, comparing or copying back out.
    """
    text = str(path)
    for separator in ("\\", "/"):
        text = text.replace(separator, separator + PATH_BREAK)
    return text


def problem_footer(dialog, on_ok) -> tuple:
    """``(row, problem, buttons)``: what holds a dialog's OK, said beside it
    in the warning tone, and its OK and Cancel -- OK calling ``on_ok``,
    which accepts the dialog once it has done what OK does."""
    row = QHBoxLayout()
    problem = QLabel("")
    problem.setWordWrap(True)
    theme.tint(problem, "warn")
    row.addWidget(problem, 1)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(on_ok)
    buttons.rejected.connect(dialog.reject)
    row.addWidget(buttons, 0, Qt.AlignmentFlag.AlignBottom)
    return row, problem, buttons
