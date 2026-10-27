"""The file tab: one mod's file to type in, with an outline of it beside.

The XML view is an index into a mod's ops by asset, and its pane takes typing
for the ops that are one asset's own.  A pattern, an op naming several
assets, an op in a group and the group itself had nowhere to be typed, and
the order the ops run in could not be changed at all.  This tab holds the
file of the mod changes go to, whole and in order.

It decides nothing.  :mod:`core.modfile` says what the file's text is and
which op is on which lines, and makes the mod say what was typed; this lays
that out.  What is typed is text in a box until **Apply**
(``applyRequested`` to the window, which records the step), and nothing
else in the Studio has heard of it.

Three things in the editor are drawn and are not text.  The heading over
each asset's ModOps is written again at every export, so it stands on the
blank line above them where it cannot be typed over and left wrong.  And the
strip down the left marks the lines of an op the Studio could not read, and
of one that is switched off.  Beside a line, too, is the name of each asset a
number on it links to, which a click opens (:class:`.XmlPane`).

The outline is every op and group in file order, each row the tag it opens
with as the file has it, and what it comes to as the Mod changes panel says
it (:class:`.change_rows.ChangeRows`).  It is for finding your place and
seeing state: switching, reverting and writing narrow stay in the panel.  It
folds away to a strip, for whoever would rather have the width for the file.

And for moving a ModOp.  A row dragged, or moved a place up or down, moves
its lines in the text (:mod:`core.moving`): one step of the pane's undo, and
nothing applied, as the cut and paste it stands for.  The rows are then of
the text as the move left it, so moves follow one another; once anything is
typed, they are of the file as it was before, and stay until Apply.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QKeySequence,
    QPainter,
    QPolygonF,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QToolTip,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    games,
    generator,
    grouping,
    guidnames,
    keptops,
    modfile,
    moving,
    patching,
    typedops,
)
from ..core.i18n import tr, tr_stored, trn
from . import op_marks, theme
from .asset_xml import FindBar, XmlPane, where_reading_stopped
from .change_rows import SHORT_ROLE, ChangeRows, ChangesTree, changes_tree
from .help_tip import HelpTip
from .widgets import ElideItemDelegate

# The line of the file a row of the outline is on, counted from 1.
LINE_ROLE = Qt.ItemDataRole.UserRole + 40
# And what the row is, for narrowing the outline: a set of the names below.
SHOWS_ROLE = Qt.ItemDataRole.UserRole + 41
# Which of the file's things it is: its place in ``Shown.items``.
ITEM_ROLE = Qt.ItemDataRole.UserRole + 42

# What the outline can be narrowed to.
ALL, PATTERN, COMPUTED, CONDITIONAL, UNREAD, OFF, STALE = (
    "all", "pattern", "computed", "conditional", "unread", "off", "stale",
)
# How much of a row's tag its tooltip has room for.
TIP_TAG = 400


class FilePane(XmlPane):
    """A mod's file to type in: the text, with a heading drawn over each of
    the mod's other files.

    Where the mod keeps ModOps in files its patch includes, each file's
    lines follow the line that names it and are a region of the text: on a
    tint, under a heading that names the file, with a bar at the edge of
    the strip for each file they are inside.  Nothing in the text marks
    where a region starts or ends.  The pane holds that, by a cursor on the
    line that names the file, one at the start of its first line and one on
    the line after its last, so that it moves with what is typed.  A line
    typed at the end of a file's last line is the file's; one typed on the
    line after it is not.
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent, gutter=True)
        self.setObjectName("XmlPane")
        self._said: Dict[int, str] = {}
        # Each region as it was shown, with its three cursors: on the line
        # that names it, at the start of its first line, and at the start of
        # the line after its last.
        self._regions: List[tuple] = []
        self._filled: Optional[modfile.Shown] = None
        # The files whose lines are folded away under their heading.
        self._folded: set = set()
        self._depths_at: tuple = (-1, 0, [])
        self.headingClicked.connect(self._on_heading)
        self.heading_menu = self._heading_menu
        # A file's heading folds its lines on a click: the pointer says so
        # over one, which it can only do if it is told where it is.
        self.viewport().setMouseTracking(True)
        self.say_of_marks(
            tr("A warning marks a ModOp that was not read. A bar marks one switched off."),
            lambda line: self._said.get(line, ""),
        )

    def show_file(self, shown: modfile.Shown, keep_text: bool = False) -> None:
        """Put a mod's file in the pane, with its headings and its marks;
        only those, with ``keep_text``, over a text that is already the
        file's.  Qt keeps some characters its own way (a no-break space as a
        space), so a text that is the file's need not be equal to it."""
        unread, off = [], []
        self._said = {}
        for item in shown.items:
            lines = range(item.first, item.last + 1)
            if item.unread is not None and item.unread.markup:
                # A tweak tool's markup: no op, so not one that was not read.
                continue
            if item.unread is not None:
                unread.extend(lines)
                why = tr("Not read: %s", tr_stored(item.unread.why))
                self._said.update((line - 1, why) for line in lines)
            elif item.off:
                off.extend(lines)
                self._said.update(
                    (line - 1, tr("Switched off. The game does not read it.")) for line in lines
                )
        self.show_text(self.toPlainText() if keep_text else shown.text, unread, off=off)
        # A heading over each of the mod's other files, which XmlPane draws
        # on the blank line that starts it.
        self.set_headings(
            [(region.first, said_of_file(region.file)) for region in shown.regions]
        )
        self._filled = shown
        self._hold(shown.regions)
        # A file folded stays folded while the mod has it.
        self._folded &= {region.file for region in shown.regions}
        self._refold()

    # -- folding a file's lines away ------------------------------------------
    def folded(self) -> set:
        return set(self._folded)

    def fold(self, file: str, on: bool = True) -> None:
        """Fold the lines of ``file`` away under its heading, or bring them
        back.  They are in the text all the same, and Apply reads them."""
        if on == (file in self._folded):
            return
        (self._folded.add if on else self._folded.discard)(file)
        self._refold()

    def _refold(self) -> None:
        document = self.document()
        hidden = set()
        for region in self.regions():
            if region.file in self._folded and region.line < region.first <= region.last:
                # Its first line stays: the heading is drawn on it.
                hidden.update(range(region.first, region.last))
        block = document.firstBlock()
        moved = False
        while block.isValid():
            show = block.blockNumber() not in hidden
            if block.isVisible() != show:
                block.setVisible(show)
                moved = True
            block = block.next()
        if not moved:
            return
        caret = self.textCursor()
        if not caret.block().isVisible():
            # Out of what is folded, to the heading over it.
            block = caret.block()
            while block.isValid() and not block.isVisible():
                block = block.previous()
            if block.isValid():
                self.setTextCursor(QTextCursor(block))
        document.markContentsDirty(0, document.characterCount())
        self.viewport().update()
        if self._gutter is not None:
            self._gutter.update()

    def _region_of_heading(self, at: int):
        """The region whose heading is the ``at``-th the pane draws."""
        return self._regions[at][0] if 0 <= at < len(self._regions) else None

    def _on_heading(self, at: int) -> None:
        region = self._region_of_heading(at)
        if region is not None:
            self.fold(region.file, region.file not in self._folded)

    def _heading_menu(self, at: int) -> list:
        region = self._region_of_heading(at)
        if region is None:
            return []
        if region.file in self._folded:
            return [(tr("Unfold this file"), lambda: self.fold(region.file, False))]
        return [(tr("Fold this file"), lambda: self.fold(region.file))]

    def headings(self) -> List[tuple]:
        found = super().headings()
        if not self._folded:
            return found
        counted = {
            region.first - 1: region.last - region.first
            for region in self.regions() if region.file in self._folded
        }
        return [
            (line, trn(
                "%(said)s · %(n)s line folded", "%(said)s · %(n)s lines folded",
                counted[line], said=words,
            ) if line in counted else words)
            for line, words in found
        ]

    def go_to_line(self, line: int, top: bool = False) -> None:
        # Into a file that is folded: it is unfolded first.
        for region in self.regions():
            if region.file in self._folded and region.first < line <= region.last:
                self.fold(region.file, False)
        super().go_to_line(line, top)

    def _hold(self, regions) -> None:
        document = self.document()
        self._regions = []
        for region in regions:
            named = document.findBlockByNumber(region.line - 1)
            first = document.findBlockByNumber(region.first - 1)
            last = document.findBlockByNumber(region.last - 1)
            if not (named.isValid() and first.isValid() and last.isValid()):
                continue
            top = QTextCursor(first)
            # Text typed at the very start of the file is the file's.
            top.setKeepPositionOnInsert(True)
            # Held by the line after it, which every file has: the text
            # ends with the tag that closes it.  What is typed at the start
            # of that line is not the file's, and what is typed at the end
            # of the file's last line is before it, and is.
            after = QTextCursor(last.next() if last.next().isValid() else last)
            after.setKeepPositionOnInsert(True)
            self._regions.append((region, QTextCursor(named), top, after))
        self._depths_at = (-1, 0, [])
        self.viewport().update()

    def regions(self) -> list:
        """Which lines are each of the mod's other files as the text stands
        now, counted from 1: what Apply cuts the text by."""
        found = [
            replace(
                region, line=named.blockNumber() + 1, first=top.blockNumber() + 1,
                last=after.blockNumber(),
            )
            for region, named, top, after in self._regions
        ]
        if self._filled is not None and any(
            not region.line < region.first <= region.last for region in found
        ):
            # All of the text was put in at once, or a file's lines were
            # taken out with the line that names it: the cursors are in a
            # heap, and the two texts say what they no longer can.
            return modfile.regions_in(self._filled, self.toPlainText())
        return found

    def _depths(self) -> list:
        """``(first, last, how deep)`` for each region that is still a run
        of lines after the line that names it, counted from 0."""
        # Asked for every line that is painted: worked out once for the
        # text as it stands.
        at = self.document().revision()
        if self._depths_at[0] == at and self._depths_at[1] == len(self._regions):
            return self._depths_at[2]
        self._depths_at = (at, len(self._regions), self._depths_now())
        return self._depths_at[2]

    def _depths_now(self) -> list:
        found = []
        for region in self.regions():
            if region.line < region.first <= region.last:
                found.append((region.first - 1, region.last - 1))
        return [
            (first, last, sum(1 for one, two in found if one <= first and last <= two))
            for first, last in found
        ]

    def has_stripes(self) -> bool:
        return bool(self._regions)

    def stripes_at(self, line: int) -> int:
        return max(
            (deep for first, last, deep in self._depths() if first <= line <= last), default=0
        )

    def _file_heading_at(self, position) -> Optional[str]:
        """The file whose heading is under ``position`` in the pane, if
        one is."""
        at = self.heading_at(self.cursorForPosition(position).blockNumber())
        region = self._region_of_heading(at) if at is not None else None
        return region.file if region is not None else None

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        over = self._file_heading_at(event.position().toPoint()) is not None             or self.name_at(event.position()) is not None
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor
        )

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            file = self._file_heading_at(event.pos())
            if file is not None:
                QToolTip.showText(
                    event.globalPos(),
                    tr("Click to show this file's lines.") if file in self._folded
                    else tr("Click to fold this file's lines away."),
                    self.viewport(),
                )
                return True
        return super().viewportEvent(event)

    def _paint_fold_marks(self, event) -> None:
        """A triangle before each file's heading: down while its lines are
        shown, and to the right while they are folded away.  What says the
        heading is something to click."""
        marked = {
            top.blockNumber(): region.file in self._folded
            for region, _named, top, _after in self._regions
            if not top.block().text().strip()
        }
        if not marked:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.TEXT_DIM))
        left = self.document().documentMargin() + 2
        for block, box in self.lines_in_view(event):
            folded = marked.get(block.blockNumber())
            if folded is None:
                continue
            side = min(box.height(), 16.0) * 0.5
            x, y = box.left() + left, box.top() + (box.height() - side) / 2
            painter.drawPolygon(QPolygonF(
                [QPointF(x, y), QPointF(x + side * 0.75, y + side / 2), QPointF(x, y + side)]
                if folded else
                [QPointF(x, y + side * 0.15), QPointF(x + side, y + side * 0.15),
                 QPointF(x + side / 2, y + side * 0.9)]
            ))
        painter.end()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        self._paint_fold_marks(event)
        depths = self._depths()
        if not depths:
            return
        # A thin tint over the lines of each file, once for each file they
        # are inside.  Over the text and not under it, which a pane of text
        # paints for itself: thin enough to leave every colour its contrast.
        tint = QColor(theme.colour("accent"))
        tint.setAlpha(14 if theme.wash("accent") is not None else 0)
        painter = QPainter(self.viewport())
        for block, box in self.lines_in_view(event):
            at = block.blockNumber()
            for first, last, _deep in depths:
                if first <= at <= last:
                    painter.fillRect(
                        QRectF(0, box.top(), self.viewport().width(), box.height()), tint
                    )
        painter.end()


def said_of_file(file: str) -> str:
    """The heading over the lines of one of a mod's other files."""
    # Translators: a heading over part of a mod's file. %s is the path of
    # another file of the mod, whose ModOps follow.
    return tr("In %s", file)


class OutlineTree(ChangesTree):
    """The outline's tree, whose rows are dragged to move what they are.

    Qt moves no row here.  A drop says which row was let go where
    (``dropped``), the file is moved, and the rows are drawn again from it.
    ``can_drop`` is asked of the row and each place it is held over whether
    it can go there, and a place it cannot is no place to drop it.
    """

    # Which thing the row dragged is (ITEM_ROLE); the thing it goes into, or
    # None for the patch; and the thing it goes before there, or None for
    # the end of it.  Said as the drop is let go: whoever moves it moves it
    # once the drag is over.
    dropped = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.can_drop = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        # Until there is a file whose text the rows are of: FilePanel.
        self.setDragEnabled(False)

    def place(self, event) -> Optional[tuple]:
        """``(row, into, before)`` for the row dragged, let go at the
        event's point, or None where that is no place it can go."""
        rows = self.selectedItems()
        # A drag from anywhere else is none of the outline's rows.
        if event.source() is not self or len(rows) != 1 or self.can_drop is None:
            return None
        over = self.itemAt(event.position().toPoint())
        at = self.dropIndicatorPosition()
        where = QAbstractItemView.DropIndicatorPosition
        if over is None or at == where.OnViewport:
            into, before = None, None
        elif at == where.OnItem:
            into, before = over, None
        elif at == where.AboveItem:
            into, before = over.parent(), over
        else:
            into = over.parent()
            count = into.childCount() if into is not None else self.topLevelItemCount()
            index = into.indexOfChild(over) if into is not None else self.indexOfTopLevelItem(over)
            before = None
            if index + 1 < count:
                before = into.child(index + 1) if into is not None else self.topLevelItem(index + 1)
        if not self.can_drop(rows[0], into, before):
            return None
        return rows[0], into, before

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)
        if event.isAccepted() and self.place(event) is None:
            event.ignore()

    def dropEvent(self, event) -> None:
        found = self.place(event)
        # Nothing of the tree is moved by Qt: the rows are drawn again from
        # the file.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if found is not None:
            self.dropped.emit(*(
                None if row is None else row.data(0, ITEM_ROLE) for row in found
            ))


class Outline(ChangeRows):
    """Every op and group of a mod's file, in file order, as a tree."""

    # Read and gone to, not switched: that is the Mod changes panel's.
    live = False

    def __init__(self, loadout):
        self.loadout = loadout
        self.view = None
        self.tree = changes_tree(OutlineTree)
        self.tree.setHeaderLabels([tr("ModOp"), tr("Comes to")])
        # A row is the line an op opens with, and is told from the next by
        # how it starts, the type and the GUID: so it is cut on the right.
        self.tree.setItemDelegateForColumn(0, ElideItemDelegate(self.tree))
        self.tree.setIconSize(QSize(op_marks.BADGE, op_marks.HEIGHT))
        self.tree.setAccessibleName(tr("Outline of the file"))
        self._owners = None
        self._init_rows()

    def show(self, shown: Optional[modfile.Shown], stale=()) -> None:
        """Draw the rows of ``shown``; ``stale`` is the ops whose reading is
        in doubt."""
        self.tree.clear()
        if shown is None or self.view is None:
            return
        origin = shown.origin
        self._stale = {origin: set(stale)}
        steady = getattr(self.view, "steady", None)
        views = {view.uid: view for view in keptops.of(self.loadout, origin)}
        with steady() if steady is not None else _nothing():
            self._owners = grouping.owners_for(self.view, self._owners)
            ops, _rest = grouping.split(self.loadout, self.view, origin, self._owners)
            groups = {inner.uid: inner for group in ops for inner in group.every()}
            self._op_rows = set(groups)
            mono = theme.mono_font()
            open_groups: List = [self.tree]
            # The row of each of the mod's other files, which what is in
            # the file goes under; and the file the last thing was in.
            file_rows: dict = {}
            in_file = ""
            for at, item in enumerate(shown.items):
                if item.file != in_file:
                    # A group is its tags, and they are in one file.
                    in_file = item.file
                    open_groups = [file_rows.get(in_file, self.tree)]
                if item.kind == generator.SPAN_GROUP_END:
                    if len(open_groups) > 1:
                        open_groups.pop()
                    continue
                if item.kind == generator.SPAN_ASIDE:
                    # A comment of the file's: text, and no row.
                    continue
                if item.unread is not None and item.unread.markup:
                    # Nor a tweak tool's markup: what it offers is the
                    # mod's options, and the ops it switches say so.
                    continue
                parent = open_groups[-1]
                before = self._count(parent)
                shows = set()
                view = views.get(item.op.uid) if item.op is not None else None
                # The op's type, as the row's badge; and what its marks
                # say, for narrowing the outline to them.
                verb, hollow, slots = "", False, (0, 0, 0, 0)
                if view is not None:
                    verb, hollow, slots = keptops.shape(view, self.loadout.game)
                if item.unread is not None:
                    self._unread_row(parent, item.unread)
                    shows.add(UNREAD)
                elif item.op is None:
                    # A ModOp the Studio writes: one row, as it is one tag,
                    # however many of the asset's fields it holds.
                    if item.changes:
                        first = item.changes[0]
                        self._add_change(parent, first, mono, asset=self._name_of(first.guid))
                        verb = first.op
                        if len(item.changes) > 1:
                            self._row(parent, before).setText(1, trn(
                                "%(n)s change", "%(n)s changes", len(item.changes)
                            ))
                elif item.op.uid in groups:
                    self._op_row(parent, groups[item.op.uid])
                elif view is not None:
                    # One change at the node it names: its row is the change's.
                    under = list(view.changes)
                    if under:
                        self._add_change(
                            parent, under[0], mono, asset=self._name_of(under[0].guid)
                        )
                    else:
                        self._op_row(parent, grouping.OpGroup(view, ()))
                if self._count(parent) == before:
                    continue
                for name, state in zip((PATTERN, COMPUTED, CONDITIONAL), slots, strict=False):
                    if state:
                        shows.add(name)
                if item.op is not None and item.op.uid in self._stale[origin]:
                    shows.add(STALE)
                if item.off:
                    shows.add(OFF)
                row = self._row(parent, before)
                self._as_written(row, shown.opening(item), item.off, mono)
                # The badge alone, and a blank one where there is no type
                # to draw: the tag starts in the same place on every row,
                # and the strip's room is the tag's.  Its marks are in the
                # tooltip, and the tag says what it searches.
                row.setIcon(0, op_marks.icon(verb, hollow))
                row.setData(0, LINE_ROLE, item.first)
                row.setData(0, SHOWS_ROLE, sorted(shows))
                row.setData(0, ITEM_ROLE, at)
                if item.kind == generator.SPAN_GROUP:
                    row.setExpanded(True)
                    open_groups.append(row)
                elif item.kind == generator.SPAN_INCLUDE and item.op is not None:
                    row.setExpanded(True)
                    file_rows[item.op.includes] = row
        self._let_move(shown)
        self.tree.layout_columns()

    def _let_move(self, shown: modfile.Shown) -> None:
        """Each row that is a thing of the file is dragged to move it, and
        a group's and a file's row take what is dropped on them.  A row
        under one, of what an op comes to, is neither."""
        drag = Qt.ItemFlag.ItemIsDragEnabled
        drop = Qt.ItemFlag.ItemIsDropEnabled

        def walk(row) -> None:
            flags = row.flags() & ~drag & ~drop
            at = row.data(0, ITEM_ROLE)
            if at is not None:
                if moving.can_move(shown, at):
                    flags |= drag
                if moving.holds_things(shown, at):
                    flags |= drop
            row.setFlags(flags)
            for under in range(row.childCount()):
                walk(row.child(under))

        for top in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(top))

    @staticmethod
    def _count(parent) -> int:
        return parent.childCount() if isinstance(parent, QTreeWidgetItem) \
            else parent.topLevelItemCount()

    @staticmethod
    def _row(parent, at: int):
        return parent.child(at) if isinstance(parent, QTreeWidgetItem) \
            else parent.topLevelItem(at)

    @staticmethod
    def _as_written(row, tag: str, off: bool, mono) -> None:
        """A row's words: the tag its lines open with, as the file has it.

        The outline is for going about the file, so its rows read as the
        file does, and a row is found in the file by the words on both.
        What the Mod changes panel would say of the op instead is in the
        row's tooltip, under the tag in full: two ops can open alike, as two
        adding assets to the same list do, and that tells them apart.
        """
        said = [part for part in (row.text(0), row.toolTip(0)) if part]
        if len(said) == 2 and said[1].startswith(said[0]):
            said = said[1:]
        row.setText(0, tag)
        row.setFont(0, mono)
        # Cut on the right, as a line of the file is: see Outline.__init__.
        row.setData(0, SHORT_ROLE, None)
        row.setForeground(0, QColor(theme.TEXT_DIM if off else theme.TEXT))
        row.setToolTip(0, "\n\n".join((keptops.clipped(tag, TIP_TAG), *said)))

    @staticmethod
    def _unread_row(parent, entry) -> None:
        """An op the Studio could not read: what it comes to is that."""
        row = QTreeWidgetItem(parent)
        row.setText(1, tr("not read, kept as written"))
        row.setForeground(1, QColor(theme.TEXT_DIM))
        row.setToolTip(0, tr("Not read: %s", tr_stored(entry.why)))
        row.setToolTip(1, row.toolTip(0))

    def narrow(self, to: str) -> int:
        """Show only the rows that are ``to``, with the groups they are in;
        how many are shown."""
        def walk(row) -> int:
            mine = to == ALL or to in (row.data(0, SHOWS_ROLE) or ())
            under = sum(walk(row.child(at)) for at in range(row.childCount()))
            row.setHidden(not (mine or under))
            return under + (1 if mine else 0)

        return sum(
            walk(self.tree.topLevelItem(at)) for at in range(self.tree.topLevelItemCount())
        )


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *_told):
        return False


class FilePanel(QWidget):
    """One mod's file, and an outline of it.

    Which mod is the tab's own to say (:attr:`looking`), apart from which
    mod changes go to: reading what a mod says is not a decision to change
    it.  Left alone it is the mod changes go to, and follows that when it
    moves.  The file of that mod is typed in; any other is only read, until
    *Edit this file* makes changes go to it.
    """

    # Apply was pressed: the text, whole.
    applyRequested = Signal(str)
    # Make changes go to this mod, whose file is being looked at.
    editRequested = Signal(str)
    # One of the options an op in the file reads was clicked: the mod
    # whose option it is, and its name.
    optionRequested = Signal(str, str)
    # Open this asset in the asset editor.
    assetRequested = Signal(str)

    def __init__(self, loadout, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.workspace = None
        self.shown: Optional[modfile.Shown] = None
        # Asked before something typed is left: "apply", "discard" or "".
        self.ask_unapplied = None
        # Asked for the mods there are, as ``(label, origin)``, and for the
        # ops of the mod shown whose reading is in doubt: the window's to say.
        self.files_from = None
        self.stale_from = None
        # The mod whose file is shown where it is not the one changes go to,
        # or None; and the mod changes went to when that was last looked at.
        self.looking: Optional[str] = None
        self._followed: Optional[str] = None
        self._filling = False
        # The mod moved under something typed: the pane is of the file as it
        # was, until what was typed is applied, reverted or undone.
        self._moved = False
        # The file is drawn when the tab is next looked at.
        self._waiting = True
        # Which lines are what in the text as each move in the outline left
        # it, since the file was last put in the pane (see _laid); and the
        # one of those, or the file as the mod has it, the rows are of.
        # Each as ``(the pane's text, which lines are what in it)``, the
        # text as the pane has it; and the file as the mod has it they were
        # made from, as it is and as the pane has it.
        self._layouts: List[tuple] = []
        self._layouts_of: tuple = ("", "")
        self._drawn: Optional[modfile.Shown] = None
        self.outline = Outline(loadout)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        # Translators: before a list of mods, to pick whose file is shown.
        self.file_caption = QLabel(tr("&File of:"))
        top.addWidget(self.file_caption)
        self.file_box = QComboBox()
        self.file_caption.setBuddy(self.file_box)
        self.file_box.setToolTip(tr(
            "Whose file is shown. Picking a mod here does not change where your changes go."
        ))
        self.file_box.currentIndexChanged.connect(self._on_file_picked)
        top.addWidget(self.file_box)
        # Beside the picker, for the file picked there: under the help and
        # the window's buttons at the far end of the bar below, it read as
        # something else's, and a file only shown looked like one that could
        # not be typed in at all.  Shown with that bar.
        self.edit_button = QPushButton(tr("&Edit this file"))
        self.edit_button.setToolTip(tr("Makes your changes go to this mod, so its file can be typed in."))
        self.edit_button.clicked.connect(self._ask_edit)
        self.edit_button.setVisible(False)
        top.addWidget(self.edit_button)
        self.heading = QLabel("")
        self.heading.setObjectName("SubHeading")
        top.addWidget(self.heading, 1)
        top.addWidget(HelpTip(tr(
            "This is the file the mod is exported as, in the order the game "
            "runs it. Type in it, then Apply. A ModOp you did not touch stays "
            "the ModOp it was. A new one goes where you typed it, and one you "
            "left out is taken out of the mod. A comment above a ModOp is its "
            "note. The headings in grey over the mod's other files are drawn "
            "by the Studio and are not part of the text."
        ), tr("The file of a mod")))
        # The window adds what it has to say about the panel as a whole at
        # the end of this row, where it goes wherever the panel goes.
        self.top_row = top
        outer.addLayout(top)

        # Shown over a file that is only looked at.
        self.look_bar = QWidget()
        look = QHBoxLayout(self.look_bar)
        look.setContentsMargins(0, 0, 0, 0)
        look.setSpacing(8)
        self.look_note = QLabel("")
        self.look_note.setObjectName("StepHint")
        self.look_note.setWordWrap(True)
        look.addWidget(self.look_note, 1)
        self.look_bar.setVisible(False)
        outer.addWidget(self.look_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.pane = FilePane()
        self.pane.setAccessibleName(tr("The file of the mod changes go to"))
        self.pane.set_editable(True)
        self.pane.textChanged.connect(self._on_typed)
        # Typed and then undone back to the mod's own text is nothing typed.
        self.pane.document().modificationChanged.connect(lambda _typed: self._on_typed())
        self.pane.cursorPositionChanged.connect(self._follow)
        self.pane.assetClicked.connect(self.assetRequested)
        # Anno 117's ops read their mod's options: each one linked.
        self.pane.set_options(
            lambda: (self.loadout, self.origin()) if self.loadout.game == games.ANNO_117 else None
        )
        self.pane.optionClicked.connect(self.optionRequested)
        self.splitter.addWidget(self.pane)

        # The outline folds to a strip at the file's right, as the Mod
        # changes panel folds to one at the window's: whoever finds their
        # way about the file with Ctrl+F has its width for the text.
        side = QWidget()
        across = QHBoxLayout(side)
        across.setContentsMargins(0, 0, 0, 0)
        across.setSpacing(0)
        self.outline_strip = QToolButton()
        self.outline_strip.setObjectName("PanelStrip")
        self.outline_strip.setText("«")  # i18n: keep - an arrow, no word
        self.outline_strip.setToolTip(tr("Show the outline of the file  (F8)"))
        self.outline_strip.setAccessibleName(tr("Show the outline of the file"))
        self.outline_strip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.outline_strip.clicked.connect(lambda: self.toggle_outline.setChecked(True))
        self.outline_strip.setVisible(False)
        across.addWidget(self.outline_strip)
        self.outline_body = QWidget()
        across.addWidget(self.outline_body, 1)
        side_layout = QVBoxLayout(self.outline_body)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(4)
        narrow = QHBoxLayout()
        narrow.setSpacing(8)
        # Translators: before a list of which rows of an outline to show.
        self.narrow_caption = QLabel(tr("&Show:"))
        narrow.addWidget(self.narrow_caption)
        self.narrow_box = QComboBox()
        self.narrow_caption.setBuddy(self.narrow_box)
        for name, label in (
            (ALL, tr("Every ModOp")),
            (PATTERN, tr("Patterns")),
            (COMPUTED, tr("Computed")),
            (CONDITIONAL, tr("Conditional")),
            (UNREAD, tr("Not read")),
            (OFF, tr("Switched off")),
            (STALE, tr("May be out of date")),
        ):
            self.narrow_box.addItem(label, name)
        self.narrow_box.currentIndexChanged.connect(self._narrow)
        narrow.addWidget(self.narrow_box, 1)
        narrow.addWidget(HelpTip(tr(
            "Each ModOp of the file, in the order the game runs them. Double-click "
            "one to go to it. To move one, drag it, or select it and press Alt+Up "
            "or Alt+Down. That changes the text, and Apply changes the mod."
        ), tr("The outline")))
        self.outline_fold = QToolButton()
        self.outline_fold.setObjectName("PanelFold")
        self.outline_fold.setText("»")  # i18n: keep - an arrow, no word
        self.outline_fold.setToolTip(tr("Fold the outline of the file away  (F8)"))
        self.outline_fold.setAccessibleName(tr("Fold the outline of the file away"))
        self.outline_fold.clicked.connect(lambda: self.toggle_outline.setChecked(False))
        narrow.addWidget(self.outline_fold)
        side_layout.addLayout(narrow)
        # In the View menu, and on the panel as well, for when the panel is
        # in a window of its own.
        # Translators: an item of the View menu, showing or folding away
        # the list of a mod's ModOps beside its file.
        self.toggle_outline = QAction(tr("Outline of the mod file"), self)
        self.toggle_outline.setCheckable(True)
        self.toggle_outline.setChecked(True)
        self.toggle_outline.setShortcut(QKeySequence("F8"))
        self.toggle_outline.toggled.connect(self._show_outline)
        self.addAction(self.toggle_outline)
        self.outline_side = side
        # What the outline had when it was last folded away.
        self._outline_width = 0
        tree = self.outline.tree
        tree.itemActivated.connect(self._go_to_row)
        tree.itemDoubleClicked.connect(self._go_to_row)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._row_menu)
        # A row is dragged to move what it is, or moved a place at a time.
        tree.can_drop = self._can_drop
        tree.dropped.connect(self._dropped)
        for keys, step in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(keys), tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(
                lambda step=step: self.move_row(self.outline.tree.currentItem(), step)
            )
        side_layout.addWidget(tree, 1)
        self.outline_note = QLabel("")
        self.outline_note.setObjectName("StepHint")
        self.outline_note.setWordWrap(True)
        side_layout.addWidget(self.outline_note)
        self.splitter.addWidget(side)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        outer.addWidget(self.splitter, 1)

        self.find_bar = FindBar()
        outer.addWidget(self.find_bar)
        for keys, backward in (("F3", False), ("Shift+F3", True)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda backward=backward: self._find_again(backward))

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1, Qt.AlignmentFlag.AlignVCenter)
        # Translators: puts the file's text back to what the mod says now.
        self.revert_button = QPushButton(tr("Revert"))
        self.revert_button.setToolTip(tr("Puts back the file as the mod has it now."))
        self.revert_button.clicked.connect(self.revert_typed)
        bar.addWidget(self.revert_button)
        self.apply_button = QPushButton(tr("Apply"))
        self.apply_button.setObjectName("Primary")
        self.apply_button.setToolTip(tr("Changes the mod to say what you typed.  (Ctrl+Enter)"))
        self.apply_button.clicked.connect(self._ask_apply)
        bar.addWidget(self.apply_button)
        outer.addLayout(bar)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            apply_key = QShortcut(QKeySequence(keys), self)
            apply_key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            apply_key.activated.connect(self._ask_apply)
        self._sync_bar()

    def _show_outline(self, shown: bool) -> None:
        """Open the outline, or fold it to the strip at the file's right.

        Folded, the strip is all the side is, and the file has the rest;
        the width the outline had is kept to open it at again.
        """
        if shown != self.outline_body.isHidden():
            return
        sizes = self.splitter.sizes()
        if not shown and self.splitter.isVisible():
            self._outline_width = sizes[1]
        self.outline_body.setVisible(shown)
        self.outline_strip.setVisible(not shown)
        strip = self.outline_strip.sizeHint().width()
        self.outline_side.setMaximumWidth(16777215 if shown else strip)
        if not sum(sizes):
            # Not laid out yet: its stretch gives the outline its share.
            return
        if shown and not self._outline_width:
            # Never folded while it was laid out: the share it opens at.
            self._outline_width = sum(sizes) * 2 // 5
        wanted = self._outline_width if shown else strip
        self.splitter.setSizes([max(1, sum(sizes) - wanted), wanted])
        if not shown and self.outline_body.isAncestorOf(QApplication.focusWidget()):
            self.pane.setFocus()

    # -- which mod ------------------------------------------------------------
    def set_source(self, workspace) -> None:
        self.workspace = workspace
        self.outline.view = workspace
        self.pane.set_namer(guidnames.Namer(workspace) if workspace is not None else None)
        self.shown = None
        self._waiting = True

    def set_files(self, files) -> None:
        """The mods there are to show the file of, as ``(label, origin)``,
        with the one shown picked."""
        current = self.origin()
        self._filling = True
        try:
            self.file_box.clear()
            for label, origin in files:
                self.file_box.addItem(label, origin)
            at = next((n for n, (_label, origin) in enumerate(files) if origin == current), -1)
            self.file_box.setCurrentIndex(at)
        finally:
            self._filling = False
        several = len(files) > 1
        self.file_caption.setVisible(several)
        self.file_box.setVisible(several)

    def _on_file_picked(self) -> None:
        if self._filling:
            return
        picked = self.file_box.currentData()
        if picked is None or not self.look_at(str(picked)):
            # What was typed stays, and so does the file it was typed in.
            self.refresh()

    def look_at(self, origin: str) -> bool:
        """Show ``origin``'s file, whichever mod changes go to; False where
        something typed is waiting and whoever typed it would rather stay."""
        if origin == self.origin():
            return True
        if not self.settle_typed():
            return False
        editing = self.loadout.editing_origin()
        self.looking = None if origin == editing else origin
        self._followed = editing
        self.refresh()
        return True

    def origin(self) -> str:
        """The mod whose file is shown: the one picked to look at while it
        is still here, and the one changes go to otherwise."""
        editing = self.loadout.editing_origin()
        if self._followed != editing:
            # Changes go to another mod than they did: that is the file to
            # be in, whatever was being looked at.
            self._followed = editing
            self.looking = None
        looking = self.looking
        if looking is not None and looking != editing and (
            self.loadout.layer(looking) is not None if looking else self.loadout.has_own_mod()
        ):
            return looking
        self.looking = None
        return editing

    def is_typed_in(self) -> bool:
        """Whether the file shown is the one changes go to."""
        return self.origin() == self.loadout.editing_origin()

    def _ask_edit(self) -> None:
        self.editRequested.emit(self.origin())

    def _look_said(self) -> str:
        """What the bar over a file only looked at says: where changes go,
        and of a loaded mod that is not yours, why not.  One with your name
        on it that no project keeps is read like anybody else's until one
        does, and *Edit this file* offers it one (section 5.5 of the projects
        and loadouts proposal); the bar said only that changes went
        elsewhere, which is as true of somebody else's."""
        going = tr("Only shown. Your changes go to %s.", self.mod_name(self.loadout.editing_origin()))
        origin = self.origin()
        layer = self.loadout.layer(origin) if origin else None
        if layer is None or layer.patches or patching.is_mine(self.loadout, layer):
            return going
        if patching.without_a_project(self.loadout, layer):
            return tr("Only shown. %s has your name on it, but no project of yours keeps it yet. "
                      "Edit this file makes it one, and your changes go straight into it.",
                      layer.label)
        creator = (layer.creator or "").strip()
        if creator:
            return going + " " + tr(
                "%(mod)s is by %(creator)s, which is not one of your author names "
                "(Settings ▸ Authoring).", mod=layer.label, creator=creator)
        return going + " " + tr("%s names no author, so it is not one of yours.", layer.label)

    def mod_name(self, origin: Optional[str] = None) -> str:
        origin = self.origin() if origin is None else origin
        # "My Anno Mod" stays English: it is the name the mod is exported
        # under until somebody gives it another.
        return self.loadout.label_of(origin) or "My Anno Mod"

    # -- what is shown --------------------------------------------------------
    def mark_stale(self) -> None:
        """The mod may have moved: draw its file again when it is next
        looked at, or now if it is in front."""
        self._waiting = True
        # In front, as a page of the window's tabs is unless it is hidden.
        if not self.isHidden():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._waiting:
            self.refresh()

    def refresh(self) -> None:
        """Show the file of the mod changes go to as it is now -- unless
        something typed for that mod is waiting, which stays."""
        self._waiting = False
        if self.workspace is None:
            return
        if self.files_from is not None:
            self.set_files(self.files_from())
        mine = self.is_typed_in()
        if not mine and self.edit_button.hasFocus():
            self.pane.setFocus()
        self.pane.set_editable(mine)
        self.look_bar.setVisible(not mine)
        self.edit_button.setVisible(not mine)
        if not mine:
            self.look_note.setText(self._look_said())
        shown = modfile.shown(self.loadout, self.origin())
        typing = self.has_unapplied() and self.shown is not None \
            and self.shown.origin == shown.origin
        if typing:
            if shown.text != self.shown.text:
                # Undo, the Mod changes panel, an edit in Fields: the mod
                # has moved under what is being typed.
                self.say(tr(
                    "The mod changed while you were typing. Apply reads what "
                    "is here against the mod as it is now. Revert shows the "
                    "file as it is now."
                ), "warn")
                self._moved = True
                # What the moves made of the text was made of the mod as it was.
                self._layouts = []
            self.shown = shown
        else:
            self.shown = shown
            self._fill()
            self.say("")
        # With nothing to pick from, the heading is what says whose it is.
        # Emptied and not hidden where the picker says it: it is what keeps
        # the picker to the left.
        self.heading.setText(
            tr("The file of %s", self.mod_name()) if self.file_box.isHidden() else ""
        )
        self.pane.setAccessibleName(tr("The file of %s", self.mod_name()))
        self.pane.setPlaceholderText(
            tr("%s has no ModOps yet. Type them here, then Apply.", self.mod_name())
            if mine else tr("%s has no ModOps.", self.mod_name())
        )
        laid = self._laid()
        if laid is not None:
            self._drawn = laid
        elif not any(one is self._drawn for _text, one in self._layouts):
            self._drawn = self.shown
        self._draw_outline()
        self._sync_bar()

    def _draw_outline(self) -> None:
        stale = set(self.stale_from()) if self.stale_from is not None else set()
        self.outline.show(self._drawn, stale)
        self._narrow()

    def _fill(self) -> None:
        self._filling = True
        try:
            at = self.pane.textCursor().blockNumber()
            self.pane.show_file(self.shown)
            self.pane.document().setModified(False)
            self._moved = False
            if self._layouts_of[0] != self.shown.text:
                # Of another file than this.  While it is this one, a move
                # undone can still be done again, and is that move.
                self._layouts = []
            self._drawn = self.shown
            block = self.pane.document().findBlockByNumber(at)
            if block.isValid():
                self.pane.setTextCursor(QTextCursor(block))
        finally:
            self._filling = False

    def _narrow(self) -> None:
        to = self.narrow_box.currentData() or ALL
        found = self.outline.narrow(to)
        self.outline.tree.setDragEnabled(self.can_move())
        if self.has_unapplied() and not self._current():
            self.outline_note.setText(
                tr("The outline is of the file as it was applied.")
                if self._drawn is self.shown
                else tr("The outline is of the file as it was before you typed in it.")
            )
        elif to != ALL and not found:
            self.outline_note.setText(tr("No ModOp of this kind in the file."))
        else:
            self.outline_note.setText("")
        self.outline_note.setVisible(bool(self.outline_note.text()))

    # -- going about ----------------------------------------------------------
    def _go_to_row(self, row, _column=0) -> None:
        line = row.data(0, LINE_ROLE)
        if line and self._current():
            self.pane.go_to_line(int(line))
            self.pane.setFocus()

    def _follow(self) -> None:
        """The caret went into an op: select its row in the outline."""
        if self._filling or not self._current() or not self.pane.hasFocus():
            return
        item = self._drawn.item_at(self.pane.textCursor().blockNumber() + 1)
        if item is not None:
            self._select_line(item.first)

    def _select_line(self, line: int) -> None:
        """Select the row of what starts on ``line``."""
        tree = self.outline.tree

        def find(row):
            if row.data(0, LINE_ROLE) == line:
                return row
            for at in range(row.childCount()):
                found = find(row.child(at))
                if found is not None:
                    return found
            return None

        for at in range(tree.topLevelItemCount()):
            found = find(tree.topLevelItem(at))
            if found is not None:
                tree.setCurrentItem(found)
                return

    def _row_menu(self, at) -> None:
        tree = self.outline.tree
        menu = self.menu_for(tree.itemAt(at))
        if menu is None:
            return
        menu.exec(tree.viewport().mapToGlobal(at))
        menu.deleteLater()

    def menu_for(self, row) -> Optional[QMenu]:
        """The menu of a row of the outline, or None for no row."""
        if row is None or self._drawn is None:
            return None
        item = self._drawn.item_at(int(row.data(0, LINE_ROLE) or 0))
        menu = QMenu(self)
        go = menu.addAction(tr("Go to it in the file"), lambda: self._go_to_row(row))
        go.setEnabled(self._current())
        thing = row.data(0, ITEM_ROLE)
        if thing is not None:
            menu.addSeparator()
            for label, keys, step, can in (
                (tr("Move up"), "Alt+Up", -1, moving.up),
                (tr("Move down"), "Alt+Down", 1, moving.down),
            ):
                move = menu.addAction(label, lambda step=step: self.move_row(row, step))
                move.setShortcut(QKeySequence(keys))
                move.setEnabled(self.can_move() and can(self._drawn, thing) is not None)
        guids = self.reached(item)
        if guids:
            menu.addSeparator()
        for guid in guids[:12]:
            menu.addAction(
                tr("Open %s", self.workspace.display_name(guid) or guid),
                lambda guid=guid: self.assetRequested.emit(guid),
            )
        if len(guids) > 12:
            more = menu.addAction(trn(
                "And %(n)s more asset, in the Mod changes panel",
                "And %(n)s more assets, in the Mod changes panel", len(guids) - 12,
            ))
            more.setEnabled(False)
        return menu

    def reached(self, item) -> List[str]:
        """The assets what is on ``item``'s lines changes today, in order."""
        if item is None:
            return []
        changes = list(item.changes)
        if item.op is not None:
            changes = self.loadout.changes_by_op(item.op.uid)
        found: List[str] = []
        for change in changes:
            guid = grouping.asset_guid(change)
            if guid and guid not in found:
                found.append(guid)
        return found

    def show_line(self, line: int) -> bool:
        """Put the caret on ``line`` of the file as the mod has it; whether
        there is such a line to go to."""
        if not line or self.has_unapplied():
            return False
        self.pane.go_to_line(line)
        self.pane.setFocus()
        return True

    # -- moving what a row is ---------------------------------------------------
    def _laid(self) -> Optional[modfile.Shown]:
        """Which lines are what in the text as it stands: the file as the
        mod has it, where nothing is typed, or as a move in the outline left
        it, where nothing has been typed since.  None where something has:
        the text alone does not say."""
        if self.shown is None:
            return None
        if not self.has_unapplied():
            return self.shown
        if not self._layouts:
            return None
        text = self.pane.toPlainText()
        if text == self._layouts_of[1]:
            return self.shown
        return next(
            (laid for as_typed, laid in reversed(self._layouts) if as_typed == text), None
        )

    def _current(self) -> bool:
        """Whether the outline's rows are of the text as it stands."""
        laid = self._laid()
        return laid is not None and laid is self._drawn

    def can_move(self) -> bool:
        """Whether the outline's rows can be moved now: they are of the
        text as it stands, in the file changes go to, and every row is
        shown, so that a place up or down is one that can be seen."""
        return (
            self._current() and self.is_typed_in()
            and (self.narrow_box.currentData() or ALL) == ALL
        )

    @staticmethod
    def _thing(row) -> Optional[int]:
        """Which of the file's things ``row`` is, by its place in the items."""
        return None if row is None else row.data(0, ITEM_ROLE)

    def _can_drop(self, row, into, before) -> bool:
        if not self.can_move():
            return False
        at, there, ahead = self._thing(row), self._thing(into), self._thing(before)
        if at is None or (into is not None and there is None) or (
            before is not None and ahead is None
        ):
            return False
        return moving.can_go(self._drawn, at, there, ahead)

    def _dropped(self, at, into, before) -> None:
        """A row was let go: moved once the drag is over, if the rows are
        of the same text then."""
        drawn = self._drawn
        QTimer.singleShot(0, lambda: self._move_dropped(drawn, at, into, before))

    def _move_dropped(self, drawn, at, into, before) -> None:
        if drawn is self._drawn and self.can_move() and at is not None:
            self._put(moving.moved(drawn, at, into, before))

    def move_row(self, row, step: int) -> bool:
        """Move what ``row`` is one place up (``step`` -1) or down (1), as
        *Move up* and *Move down* on its menu do; whether it moved."""
        at = self._thing(row)
        if at is None or not self.can_move():
            return False
        return self._put((moving.up if step < 0 else moving.down)(self._drawn, at))

    def _put(self, made: Optional[moving.Moved]) -> bool:
        """Make the text say what a move made of it: one step of the pane's
        own undo, as the cut and paste it stands for would be, and nothing
        applied.  The rows are drawn again from it."""
        if made is None:
            return False
        laid = made.shown
        if not self._layouts:
            self._layouts_of = (self.shown.text, self.pane.toPlainText())
        self._filling = True
        try:
            self.pane.type_over(laid.text)
            self._layouts.append((self.pane.toPlainText(), laid))
            # The text is the move's already: its headings and marks.
            self.pane.show_file(laid, keep_text=True)
        finally:
            self._filling = False
        # Into a file folded away: unfolded, to show what went into it.
        for region in laid.regions:
            if region.first < made.line <= region.last:
                self.pane.fold(region.file, False)
        self._drawn = laid
        self._draw_outline()
        self._sync_bar()
        self.pane.go_to_line(made.line)
        self._select_line(made.line)
        self.say(tr("Moved in the text. Apply to change the mod."))
        return True

    # -- typing ---------------------------------------------------------------
    def _on_typed(self) -> None:
        if not self._filling:
            if self._moved and not self.has_unapplied():
                # Undone to the last key, over a mod that has moved since:
                # the text left is the old file's, and nothing typed keeps it.
                self.revert_typed()
                return
            laid = self._laid()
            if laid is not None and laid is not self._drawn:
                # Undone, or done again, to a text a move made or to the
                # mod's own: the rows and the pane's marks are of it again.
                self._filling = True
                try:
                    self.pane.show_file(laid, keep_text=True)
                finally:
                    self._filling = False
                self._drawn = laid
                self._draw_outline()
            self._sync_bar()
            self._narrow()

    def _sync_bar(self) -> None:
        typed = self.has_unapplied()
        if not typed and (self.apply_button.hasFocus() or self.revert_button.hasFocus()):
            # A button that goes grey under the keyboard hands it on to the
            # next control along, which from these is Play.
            self.pane.setFocus()
        self.apply_button.setEnabled(typed)
        self.revert_button.setEnabled(typed)

    def has_unapplied(self) -> bool:
        """Whether something typed has neither been applied nor reverted."""
        return self.shown is not None and self.pane.document().isModified()

    def typed_text(self) -> str:
        return self.pane.toPlainText()

    def revert_typed(self) -> None:
        """Put the file back to what the mod says."""
        if self.shown is None:
            return
        self._fill()
        self.say("")
        self._sync_bar()
        # The rows may be of a move that is gone with what was typed.
        self._draw_outline()

    def put_back(self, text: str) -> None:
        """An Apply of ``text`` was taken back: it is in the pane again,
        typed and not applied, over the file as the mod has it now."""
        if self.shown is None:
            return
        cursor = QTextCursor(self.pane.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        self._sync_bar()
        self._narrow()

    def settle_typed(self) -> bool:
        """Before leaving what was typed: apply it, discard it, or stay.
        False means stay."""
        if not self.has_unapplied():
            return True
        answer = self.ask_unapplied(self.mod_name()) if self.ask_unapplied else "discard"
        if answer == "apply":
            self.applyRequested.emit(self.typed_text())
            return not self.has_unapplied()
        if answer == "discard":
            self.revert_typed()
            return True
        return False

    def _ask_apply(self) -> None:
        if self.has_unapplied():
            self.applyRequested.emit(self.typed_text())

    def say(self, text: str, tone: str = "") -> None:
        """The line beside Apply: what it did, or why it did not."""
        self.status.setText(text)
        theme.tint(self.status, tone)

    def refuse(self, found: typedops.Read) -> None:
        """What was typed is not something to apply: say why, and where."""
        stopped = where_reading_stopped(found)
        if stopped:
            self.say(tr("Not applied. %s", stopped), "error")
        elif found.refused == modfile.INCLUDE_NOT_ALONE:
            self.say(tr(
                "Not applied. Line %s: this <Include> has its file's ModOps "
                "under it, so it can only name a file of this mod: no "
                "Condition, no Group around it. Type the ModOps into a "
                "Group instead.", found.line,
            ), "error")
        elif found.refused == modfile.INCLUDE_TWICE:
            self.say(tr(
                "Not applied. Line %s: another <Include> already names this "
                "file. The game reads a file once.", found.line,
            ), "error")
        elif found.refused == modfile.INCLUDE_UNREADABLE:
            self.say(tr(
                "Not applied. Line %s: the mod has this file, and it is not "
                "ModOps the Studio can read.", found.line,
            ), "error")
        elif found.refused == modfile.INCLUDE_NOT_FOLLOWED:
            self.say(tr(
                "Not applied. Line %s: the Studio cannot follow this "
                "<Include> from the mod's folder, so applied it would be "
                "gone. Name a file the mod has, or type the ModOps here.",
                found.line,
            ), "error")
        else:
            self.say(tr(
                "Not applied. Line %s: an <Include> on a Condition or in a "
                "Group is read from the mod's folder, and this mod has no "
                "folder yet. Export the mod first, or type the ModOps here.",
                found.line,
            ), "error")
        if found.line:
            self.pane.go_to_line(found.line)
        self.pane.setFocus()

    def applied(self, done: modfile.Applied, bare_groups: int = 0) -> None:
        """Say what Apply did, once the file is shown as the mod has it.
        ``bare_groups``: how many ``<Group>`` tags with nothing on them were
        typed, which the file does not keep."""
        # A bracket to the loader, read straight through: it went from the
        # text without a word, as though the typing had been lost (R3-45,
        # hands-on run 3).
        bare = trn(
            "A <Group> with no Condition or anything else on it changes nothing for "
            "the game, so it is not kept. What was in it stays where it was.",
            "%(n)s <Group> tags with no Condition or anything else on them change "
            "nothing for the game, so they are not kept. What was in them stays "
            "where it was.", bare_groups,
        ) if bare_groups else ""
        if not done.moved:
            self.say(" ".join(part for part in (tr("No change to the mod."), bare) if part))
            return
        said = []
        if done.added:
            said.append(trn("%(n)s ModOp added.", "%(n)s ModOps added.", done.added))
        if done.changed:
            said.append(trn("%(n)s ModOp changed.", "%(n)s ModOps changed.", done.changed))
        if done.dropped:
            said.append(trn("%(n)s ModOp taken out.", "%(n)s ModOps taken out.", done.dropped))
        if done.groups:
            said.append(trn(
                "%(n)s group opened, closed or asked something else.",
                "%(n)s groups opened, closed or asked something else.", done.groups,
            ))
        for file in done.made:
            # Translators: %s is the path of a file inside the mod.
            said.append(tr("%s is a new file of the mod.", file))
        for file, count in done.read_in.items():
            said.append(trn(
                "%(file)s is included, with its %(n)s ModOp.",
                "%(file)s is included, with its %(n)s ModOps.", count, file=file,
            ))
        for file in done.joined:
            said.append(tr(
                "No line names %s now, so its ModOps are in the file it was in.", file
            ))
        if done.comments:
            said.append(trn(
                "%(n)s comment added, changed or taken out.",
                "%(n)s comments added, changed or taken out.", done.comments,
            ))
        if done.spaced:
            said.append(tr("The blank lines are as you typed them."))
        if done.rearranged:
            said.append(tr("The ModOps are in the order and the files you typed them in."))
        if bare:
            said.append(bare)
        tone = ""
        if done.unread:
            tone = "warn"
            said.append(trn(
                "%(n)s ModOp in the file could not be read, and is kept as "
                "written: %(why)s.",
                "%(n)s ModOps in the file could not be read, and are kept as "
                "written. The first: %(why)s.",
                done.unread, why=tr_stored(done.why_unread[0]),
            ))
        self.say(" ".join(said), tone)

    # -- find -----------------------------------------------------------------
    def open_find(self) -> bool:
        self.find_bar.open_on(self.pane)
        return True

    def _find_again(self, backward: bool) -> None:
        if not self.find_bar.isVisible():
            self.open_find()
        else:
            self.find_bar.find(backward=backward)
