"""The rows of a change list: a ModOp, an asset, a change, and how each is drawn.

The Mod changes panel draws them for the whole loadout
(:mod:`.changes_dock`), and the pattern editor for the one op being written
(:mod:`.pattern_dialog`): what the editor shows under an op is the rows that
op has in the panel once it is accepted, made by the same code.  So the rows
are here, apart from either -- the tree they go in, what a row says and
wears, and its tooltip -- and each of the two keeps only what is its own:
the panel its mods, its filter, its menus and its boxes' switching.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QHeaderView,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..core import declaring, effective, grouping, keptops, modops, patching
from ..core.changes import is_own_mod
from ..core.i18n import tr, tr_stored, trn
from . import op_marks, theme
from .widgets import ElideItemDelegate, fit_columns

# The asset a change is about; kept under its old name because half the
# window imports it by that.
open_guid = grouping.asset_guid

GUID_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
OPEN_ROLE = Qt.ItemDataRole.UserRole + 3   # the asset a row should open
KIND_ROLE = Qt.ItemDataRole.UserRole + 4   # "mod" | "op" | "group" | "asset" | "change"
ORIGIN_ROLE = Qt.ItemDataRole.UserRole + 5  # which mod a row belongs to
OP_ROLE = Qt.ItemDataRole.UserRole + 6    # the uid of the op a row is
FIND_ROLE = Qt.ItemDataRole.UserRole + 7    # what the filter box matches on
PART_ROLE = Qt.ItemDataRole.UserRole + 8    # a group inside an op: its op and path
GOTO_ROLE = Qt.ItemDataRole.UserRole + 9    # the mod row a bundled mod's row stands for
UNBUILT_ROLE = Qt.ItemDataRole.UserRole + 10  # a folded mod whose rows are not made yet
UNREAD_ROLE = Qt.ItemDataRole.UserRole + 11   # an unread op's place among its mod's ops
STATUS_ROLE = Qt.ItemDataRole.UserRole + 12   # a row's warning, drawn left of its arrow
# On a row that starts with a name: the same row with the asset's internal
# name left off, or "" when it has none to leave off. See NameFirstDelegate.
SHORT_ROLE = Qt.ItemDataRole.UserRole + 13


# How the tree's two columns split the width when nobody has said otherwise,
# and the least either of them is allowed to become. The Change column was a
# fixed 150px, which it kept whatever the panel was given and whatever the
# header was dragged to -- so widening the panel only ever widened the names,
# and dragging the divider left just opened a gap on the right. What goes in
# that column has grown since (a resolved name beside an addition, what kind of
# asset is being made), and it flexes now like every other column in the app.
CHANGE_SHARE = 0.3
MIN_CHANGE = 90
MIN_NAME = 140


# The most a tooltip here shows of something that can be any length: a block
# of XML, a mod's own comment, a list of names.  A tooltip is read at a
# glance beside the row, and the whole of each is one step away -- under the
# row, in the XML view, or in the mod's options.
TIP_LINES = 10
TIP_LINE = 140
TIP_NAMES = 3


def first_lines(text: str, most: int = TIP_LINES) -> str:
    """The start of ``text`` for a tooltip, saying how much it leaves out."""
    lines = [
        line if len(line) <= TIP_LINE else line[: TIP_LINE - 1] + "\u2026"
        for line in (text or "").split("\n")
    ]
    if len(lines) <= most:
        return "\n".join(lines)
    left = len(lines) - most
    return "\n".join([
        *lines[:most],
        # Translators: the last line of a tooltip that shows only the start
        # of something long.
        trn("\u2026 and %(n)s more line", "\u2026 and %(n)s more lines", left),
    ])


# The two shapes :func:`core.paths.path_for` writes: an entry picked out by one
# of its own child values, and one picked out by position when nothing inside it
# was distinguishing.
_KEYED = re.compile(r"^([\w.-]+)\[([A-Za-z_][\w.-]*)\s*=\s*['\"](.*)['\"]\]$")
_NTH = re.compile(r"^([\w.-]+)\[(\d+)\]$")


def compact_step(step: str) -> str:
    """Shorten one path step for display, saying which kind of step it is.

    ``Item[Product='120031']`` is *the entry whose Product is 120031* and
    ``Item[2]`` is *the second entry, whatever it holds*.  Those are not the
    same claim -- the second breaks when the game patch reorders a list, which
    is why the row is painted amber -- and rendering both as ``Item(120031)``
    made them indistinguishable while also throwing away the one word saying
    what the number counts.
    """
    step = step.strip()
    match = _KEYED.match(step)
    if match is not None:
        key, value = match.group(2), match.group(3).strip()
        # The parent step already names the list, so the tag would only repeat
        # it; what identifies the entry is which of its fields was matched.
        return "%s %s" % (key, value) if value else match.group(1)
    match = _NTH.match(step)
    if match is not None:
        return "%s #%s" % (match.group(1), match.group(2))
    return step


def field_label(path: str, name=None) -> str:
    """A readable, identifying label for a ModOp path.

    ``/Values/FactoryUpgrade/ProductivityUpgrade/Value`` becomes
    ``FactoryUpgrade › ProductivityUpgrade › Value`` rather than just ``Value``.
    The whole path is kept -- overflow is handled by eliding from the left at
    paint time, which preserves the identifying tail.

    ``name``, when given, is asked what a keyed entry's GUID is called, and an
    entry it knows is shown by that name alone, so a cost reads as *Marble*
    rather than as *Ingredient 2178*.  The list is a summary, so it reads the
    way the game does; the tooltip keeps the exact path.
    """
    steps = [step for step in path.split("/") if step]
    if steps and steps[0] == "Values":
        steps = steps[1:]
    if not steps:
        # A change with no path anchors on the asset itself: a new asset.
        return tr("the asset itself")
    return " › ".join(_shown_step(step, name) for step in steps)


def _shown_step(step: str, name) -> str:
    if name is not None:
        match = _KEYED.match(step.strip())
        if match is not None:
            value = match.group(3).strip()
            found = name(value) if value.isdigit() else ""
            if found:
                return found
    return compact_step(step)


def named_path(path: str, name) -> str:
    """``field_label`` with the GUIDs in it resolved, for a tooltip.

    Not for the column: a resolved name is three or four times the width of the
    GUID it replaces, and a path with two of them in would be all name and no
    path.  Hovering is where there is room for both.

    ``name`` is asked for one GUID at a time and may return "" for anything it
    does not recognise, which is most numbers -- plenty of these are amounts and
    indices rather than references.
    """
    steps = [step for step in path.split("/") if step]
    if steps and steps[0] == "Values":
        steps = steps[1:]
    if not steps:
        return tr("the asset itself")

    spelt = []
    for step in steps:
        shown = compact_step(step)
        match = _KEYED.match(step.strip())
        if match is not None:
            value = match.group(3).strip()
            found = name(value) if value.isdigit() else ""
            if found and found != value:
                shown = "%s %s (%s)" % (match.group(2), found, value)
        spelt.append(shown)
    return " › ".join(spelt)


class NameFirstDelegate(ElideItemDelegate):
    """Cuts the first column's text to fit, keeping the part that says which
    row it is.

    A row that is a path is told from the next by its tail, the leaf tag and
    what is put there, so it is cut from the left: a change's, a ModOp's.
    Every row was, once every row was a path.  But a mod, an asset and a
    pattern's match start with a name, and cut from the left they lost it:
    "…er  (agriculture_arctic_04 (Bear Hunter)) > FactoryBase > CycleTime"
    for the Bear Hunter, and at a narrow panel nearly every row an ellipsis
    and the end of a bracket.

    A row that starts with a name says so by carrying ``SHORT_ROLE``.  Short
    of room it first gives up the asset's internal name, which is in
    brackets after the one the game shows, and is then cut in the middle: the
    start of the name stays, and so does the field at the end.  The whole of
    it is in the row's tooltip.
    """

    def initStyleOption(self, option, index):
        short = index.data(SHORT_ROLE)
        if short is None:
            super().initStyleOption(option, index)
            return
        QStyledItemDelegate.initStyleOption(self, option, index)
        option.textElideMode = Qt.TextElideMode.ElideMiddle
        available = self._available(option)
        if not option.text or available <= 24:
            return
        metrics = QFontMetrics(option.font)
        text = option.text
        if short and metrics.horizontalAdvance(text) > available:
            text = short
        option.text = metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, available)


class ChangesTree(QTreeWidget):
    """Tree whose two columns share the width, in the proportion last dragged.

    Both of them share it: a panel made wider gives room to the names *and* to
    what is being done to them, and dragging the divider moves the boundary
    rather than leaving a strip of nothing on the right. The proportion, not
    the pixel width, is what is kept -- the panel lives in a splitter the user
    drags all day, and a column that held its pixels across that would be back
    to being fixed in every way that shows.

    The columns are held to the width by :func:`.widgets.fit_columns`, as
    every view's are. The tree had a way of its own, which read a drag of
    the Change column's right edge as a new boundary where every other view
    undoes it: that edge is the tree's.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        fit_columns(
            self,
            shares={0: 1.0 - CHANGE_SHARE, 1: CHANGE_SHARE},
            floors={0: MIN_NAME, 1: MIN_CHANGE},
        )

    def drawBranches(self, painter, rect, index) -> None:
        """The arrows, and left of a row's arrow the warning it has to give.

        A row that is a ModOp has its type's badge where a warning glyph
        used to be, and every such row sits at least one level in, under its
        mod: so the warning takes the last of that room before the arrow,
        and the row keeps both.
        """
        super().drawBranches(painter, rect, index)
        tone = index.data(STATUS_ROLE)
        if not tone:
            return
        step = self.indentation()
        # The arrow's own cell is the last one; the glyph goes in the one
        # before it, or in the arrow's where the row has no children.
        cells = 1 if self.model().hasChildren(index) else 0
        side = min(step, rect.height(), 16)
        left = rect.right() + 1 - step * cells - side - (step - side) // 2
        if left < rect.left():
            return
        top = rect.top() + (rect.height() - side) // 2
        theme.icon(tone).paint(painter, left, top, side, side)

    def share(self) -> float:
        """What of the width the Change column is taking, for remembering."""
        return self.column_fit.shares().get(1, CHANGE_SHARE)

    def set_share(self, share) -> None:
        try:
            share = float(share)
        except (TypeError, ValueError):
            return
        if 0.0 < share < 1.0:
            self.column_fit.set_shares({0: 1.0 - share, 1: share})

    def layout_columns(self) -> None:
        self.column_fit.fit()


def changes_tree(kind=ChangesTree) -> ChangesTree:
    """A tree set up to hold these rows: its two columns, how each cuts a
    row short of room, and the size of the badge a ModOp's row wears.
    ``kind`` is the tree's class, a ChangesTree or one made from it."""
    tree = kind()
    tree.setIconSize(op_marks.icon_size())
    tree.setColumnCount(2)
    tree.setHeaderLabels([
        tr("Asset / field"),
        # Translators: a column heading; the noun, what is done to a field.
        tr("Change", context="noun"),
    ])
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    tree.setExpandsOnDoubleClick(False)
    header = tree.header()
    # Both interactive: Fixed on the Change column was what made dragging
    # the divider produce dead space instead of a wider column.
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(min(MIN_CHANGE, MIN_NAME))
    # A path is identified by its tail, the leaf tag, and so is what is
    # done to it: the new value of a replace, the count that ends a
    # tally.  A row that starts with a name is not: NameFirstDelegate.
    tree.setItemDelegateForColumn(
        0, NameFirstDelegate(tree, mode=Qt.TextElideMode.ElideLeft)
    )
    tree.setItemDelegateForColumn(
        1, ElideItemDelegate(tree, mode=Qt.TextElideMode.ElideLeft)
    )
    return tree


class ChangeRows:
    """What draws the rows, for a widget that shows some: mixed into it.

    The widget has ``loadout``, whose ops and changes the rows are of, and
    ``view``, the workspace that names an asset, or None before there is
    one; :meth:`_init_rows` sets up the rest.  The rows of an op and of
    everything else come from :mod:`core.grouping`, and go under whatever
    row the widget hands :meth:`_add_op` and :meth:`_add_group`.
    """

    # Whether the rows are the loadout's own, to act on: each has the box
    # that switches it, and says what activating it opens.  Off for rows
    # drawn of something not in the loadout yet, which are only read.
    live = True

    def _init_rows(self) -> None:
        # Readings a change in what loads has left in doubt (core.stale):
        # the ops of each such mod it could reach, whether the window is
        # reading them again, and the ops the last such reading moved.
        self._stale = {}
        self.reading_again = False
        self.moved = set()
        self._collapsed = set()
        # Groups inside an op start shut, so the op reads as a list of
        # them -- one per iModYourAnno switch -- and open one at a time.
        self._opened_parts = set()
        # The ops that have a row of their own, and of those the groups
        # kept as groups, by op: see _op_of and _add_op.
        self._op_rows: set = set()
        self._scope_rows: dict = {}
        self._clashes = {}
        self._names = {}
        self._short = {}

    def _remember_text(self, item, extra: str = "") -> None:
        """What this row answers to, stashed once at build time.

        Built from what the row shows plus what it stands for: the exact path
        and GUID behind a label that has resolved them to names, so a search
        for ``1010196`` finds the row that reads *Marble* and the other way
        round. Worked out here because a filter runs over every row in the
        tree and the tree is rebuilt after every keystroke that lands.
        """
        said = "%s %s %s" % (item.text(0), item.text(1), extra)
        item.setData(0, FIND_ROLE, said.lower())

    def _add_op(self, parent, op_group, mono) -> None:
        """One kept op, with the changes it currently comes to underneath it.

        Above the assets rather than among them: an op is one thing the mod
        does, and the row that says so is more use than the four hundred rows
        that say it again per building.
        """
        node = self._op_row(parent, op_group)
        op = op_group.view.op
        for part in op_group.parts:
            self._add_part(node, part, op_group, (), mono)
        if self._one_change_each(op_group):
            # The same edit to each of several assets: a row for each asset
            # says all there is, and a row over it for the asset would say
            # its name twice.
            for group in op_group.groups:
                change = group.assets[0].changes[0]
                # And where the op's own row has said which field, only that.
                same = change.path.rstrip("/") == op.expression.rstrip("/")
                self._add_change(node, change, mono, asset=group.label, field=not same)
        elif self._one_asset_named(op_group):
            # Its row names the asset already: a row for the asset between
            # it and the changes would say it a second time.
            for change in op_group.groups[0].assets[0].changes:
                self._add_change(node, change, mono)
        else:
            for group in op_group.groups:
                self._add_group(node, group, mono)
        # What is in a group kept as a group, each a row as it would be bare.
        for held in op_group.inside:
            self._add_op(node, held, mono)
        node.setExpanded(op_group.uid not in self._collapsed)

    def _op_row(self, parent, op_group):
        """The row of one op or group, alone: what it is, drawn and in
        words, and what it comes to today.  Nothing is put under it."""
        node = QTreeWidgetItem(parent)
        node.setData(0, KIND_ROLE, "op")
        node.setData(0, OP_ROLE, op_group.uid)
        node.setData(0, ORIGIN_ROLE, op_group.view.origin)
        op = op_group.view.op
        verb, shipped_off, slots = keptops.shape(op_group.view, self.loadout.game)
        words = self._op_words(op_group, verb)
        aimed = self._op_assets(op_group)
        if aimed is not None:
            # Which asset it is about before what it does there: the rows
            # under it say the asset again, but an op folded shut, or one of
            # a mod's dozens, read as a field of nobody's.
            named, brief = aimed
            node.setData(0, SHORT_ROLE, brief and " \u203a ".join((brief, words)))
            words = " \u203a ".join((named, words))
        node.setText(0, words)
        # What the op is, drawn before its words: see ui.op_marks.
        node.setIcon(0, op_marks.icon(verb, shipped_off, slots))
        # And what each mark comes to today, said beside it; then how much
        # the op changes, unless a mark has counted that already.
        found = keptops.marks(op_group.view, self.loadout.game)
        bits = [mark.said for mark in found if mark.said]
        # Whether what is under it can be gone by, before what it is.
        if op.uid in self._stale.get(op.origin, ()):
            bits.insert(0, self._stale_words())
        elif op.uid in self.moved:
            bits.insert(0, tr("changed when read again"))
        if not any(mark.counts for mark in found):
            # Beside a mark, only the count: the mark has said what it is.
            bits.append(
                op_group.caption() if not found or op.scope
                else trn("%(n)s change", "%(n)s changes", len(op_group.changes))
            )
        node.setText(1, " \u00b7 ".join(bits))
        if op.includes:
            # A line that names one of the mod's files, and no ModOp: what
            # there is to count is what the file holds.
            held = sum(
                1 for other in self.loadout.ops
                if other.origin == op.origin and other.file == op.includes and not other.scope
            ) + sum(
                1 for entry in self.loadout.not_read(op.origin)
                if entry.file == op.includes
            )
            node.setText(1, trn(
                "a file of the mod, with %(n)s ModOp", "a file of the mod, with %(n)s ModOps",
                held,
            ))
        node.setToolTip(1, "\n".join(mark.tip for mark in found))
        # The drawing, in words, for whoever does not see it.
        node.setData(
            0, Qt.ItemDataRole.AccessibleDescriptionRole,
            " ".join(mark.tip for mark in found),
        )
        self._paint_op(node, op_group.view.op)
        tip = self._op_tip(op_group.view)
        if aimed is not None:
            # In full, whatever the row had room for.
            tip = "\n\n".join(bit for bit in (self._op_assets_said(op), tip) if bit)
        node.setToolTip(0, tip)
        if self.live and not op.includes:
            # The box is the op's own, not a summary of the rows underneath:
            # a kept op applies to every asset it matches or to none, so its
            # changes have no boxes and this one switches the whole thing.
            node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            node.setCheckState(
                0,
                Qt.CheckState.Checked
                if op_group.view.op.active
                else Qt.CheckState.Unchecked,
            )
        elif op.includes:
            # Nothing to switch: its file's ops are the mod's either way.
            node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self._remember_text(node, op_group.view.expression)
        if op.scope:
            self._scope_rows[op.uid] = node
            if op.tool:
                # Found by what the mod's options call it, and by what its
                # file does.
                self._remember_text(node, "%s %s %s" % (
                    op_group.view.expression, op.tool.get("title", ""), op.tool.get("id", ""),
                ))
        return node

    @classmethod
    def _one_asset_named(cls, op_group) -> bool:
        """Whether an op's changes are all in the one asset its row names."""
        groups = op_group.groups
        return (
            cls._names_assets(op_group)
            and len(op_group.view.op.matched) == 1
            and len(groups) == 1
            and not groups[0].is_cluster
            and groups[0].lead == op_group.view.op.matched[0]
        )

    @staticmethod
    def _names_assets(op_group) -> bool:
        """Whether an op's row says where it lands as a path in the assets
        it names: not a search, a group, a whole asset or a file of the mod,
        whose words are something else (:meth:`_op_words`)."""
        op = op_group.view.op
        if op.scope or op.includes or op.searches or not op.expression or not op.matched:
            return False
        if op.source.lstrip().startswith(("<Group", "<group", "<Asset")):
            return False
        made = [change for change in op_group.view.changes if change.creates_asset]
        return not (made and len(made) == len(op_group.view.changes))

    def _op_assets(self, op_group):
        """``(what to call the assets an op names, the same shorter)`` for
        the front of its row, or ``None`` where the row names none: the one
        asset by its name, several by how many.  The shorter is "" where
        there is none, as ``SHORT_ROLE`` has it."""
        if not self._names_assets(op_group):
            return None
        matched = op_group.view.op.matched
        if len(matched) > 1:
            # Before the field it changes in each: "3 assets > Cost > Amount = 2500".
            return trn("%(n)s asset", "%(n)s assets", len(matched)), ""
        named = self._name_of(matched[0]) or matched[0]
        return named, self._without_internal(matched[0], named)

    def _op_assets_said(self, op) -> str:
        """The assets an op names, in full, for its tooltip: three, then
        how many more."""
        names = [self._name_of(guid) or guid for guid in op.matched[:3]]
        more = len(op.matched) - len(names)
        if more:
            names.append(trn("and %(n)s more", "and %(n)s more", more))
        return "\n".join(names)

    def _op_words(self, op_group, verb: str) -> str:
        """A kept op's row, in words: where it lands and what it puts there.

        Its type is the badge before the words, so the words leave it out:
        ``Building > Radius = 25`` and not ``Building > Radius: replace
        Radius = 25``.
        """
        op = op_group.view.op
        # Each option it asks by its label: the label is what its author
        # named the part of the mod it switches.
        what = declaring.with_labels(self.loadout, op.origin, tr_stored(op_group.label()))
        if op.scope or op.source.lstrip().startswith(("<Group", "<group", "<Asset")):
            return what
        made = [change for change in op_group.view.changes if change.creates_asset]
        if made and len(made) == len(op_group.view.changes) and not op.searches:
            # All it does is add assets, at a place its path names: the
            # assets are what there is to say, and not the place.
            # Without the mark a new asset's name has in a list of assets:
            # the words say it is new.
            first = self._name_of(effective.new_asset_guid(made[0])).lstrip("+ ")
            if first and len(made) == 1:
                # Translators: a ModOp's row. %s is the name of an asset.
                return tr("new asset %s", first)
            if first:
                return trn(
                    "%(n)s new asset, the first %(first)s",
                    "%(n)s new assets, the first %(first)s", len(made), first=first,
                )
        if verb == "remove":
            what = ""
        elif verb and what.startswith(verb + ", "):
            what = what[len(verb) + 2:]
        elif verb and what.startswith(verb + " "):
            what = what[len(verb) + 1:]
        if op.searches or not op.expression:
            # A search has no one place to name: what it matches is in its
            # tooltip, and how many beside it.
            return what or op.expression
        where = field_label(op.expression, self._short_name)
        if not what:
            return where
        last = op.expression.rstrip("/").rsplit("/", 1)[-1]
        if what == last:
            # A whole node put in its place: "Building: Building" said it twice.
            return where
        if last and what.startswith(last + " = "):
            # The field is the node it names: said once.
            return where + what[len(last):]
        # Translators: a ModOp's row: "Building > Radius: 2 fields".
        return tr("%(where)s: %(what)s", where=where, what=what)

    @staticmethod
    def _one_change_each(op_group) -> bool:
        """Whether an op comes to one change in each of several assets."""
        groups = op_group.groups
        return (
            not op_group.parts
            and len(groups) > 1
            and all(not group.is_cluster and len(group.changes) == 1 for group in groups)
        )

    def _add_part(self, parent, part, op_group, prefix, mono) -> None:
        """A group inside an op that is a group kept whole: a row of its own,
        with what is in it underneath.

        No box. The group goes back out as part of its op, exactly as
        written, so there is nothing here to switch on its own; whether it runs
        is the mod's own switch -- iModYourAnno's, for most of these -- which is
        in the mod's options.
        """
        path = (*prefix, part.label)
        node = QTreeWidgetItem(parent)
        node.setData(0, KIND_ROLE, "part")
        node.setData(0, PART_ROLE, "\x1f".join((op_group.uid, *path)))
        node.setData(0, ORIGIN_ROLE, op_group.view.origin)
        node.setData(0, SHORT_ROLE, "")
        node.setText(0, tr_stored(part.shown))
        node.setText(1, part.caption())
        node.setForeground(1, QColor(theme.TEXT_DIM))
        if part.off:
            node.setForeground(0, QColor(theme.TEXT_DIM))
        lines = [tr_stored(part.shown)]
        if part.title:
            lines.append(tr("%s in the mod's file", tr_stored(part.label)))
        said = ["\n".join(lines), tr(
            "Inside %s, and written back out as part of it, as the mod has it.",
            keptops.clipped(tr_stored(op_group.label())),
        )]
        said.extend(self._switch_notes(part.off, part.unswitched, part.also))
        node.setToolTip(0, "\n\n".join(said))
        self._remember_text(node, "%s %s %s" % (part.shown, part.label, tr_stored(part.shown)))
        for inner in part.parts:
            self._add_part(node, inner, op_group, path, mono)
        for group in part.groups:
            self._add_group(node, group, mono)
        node.setExpanded(node.data(0, PART_ROLE) in self._opened_parts)

    @staticmethod
    def _switch_notes(off: bool, unswitched: bool, also) -> list:
        """What there is to say of a group a tweak tool finds by name and the
        mod's switches for it, a paragraph each: of a group inside one kept
        whole, and of one kept as a group with ops in it alike."""
        said = []
        if off and unswitched:
            said.append(tr(
                "Switched off, and none of the mod's options switches it on: "
                "the mod tags it for iModYourAnno but offers no switch for it. "
                "Probably a mistake in the mod. Check whether another group's "
                "switch carries this group's name."
            ))
        elif off:
            said.append(tr(
                "Switched off in the mod, so nothing in it runs. Where it is one "
                "of the mod's options, %s on the mod's row switches it.",
                tr("Mod options…"),
            ))
        elif unswitched:
            said.append(tr(
                "The mod tags it for iModYourAnno but offers no switch for it, "
                "so it stops running once the mod's options have been set "
                "there. Probably a mistake in the mod. Check whether another "
                "group's switch carries this group's name."
            ))
        if also:
            names = "; ".join(also[:TIP_NAMES]) + ("; \u2026" if len(also) > TIP_NAMES else "")
            said.append(trn(
                "The mod has one more switch naming this group: %(names)s. "
                "Only the first switch decides whether it runs, so that one "
                "does nothing. Probably a mistake in the mod.",
                "The mod has %(n)s more switches naming this group: "
                "%(names)s. Only the first switch decides whether it runs, so "
                "those do nothing. Probably a mistake in the mod.",
                len(also), names=names,
            ))
        return said

    def _tool_notes(self, op) -> list:
        """The same of a group kept as a group, which says it of itself
        (:attr:`~..core.changes.Op.tool`)."""
        tool = op.tool or {}
        if not op.scope or not tool.get("id"):
            return []
        said = []
        if tool.get("title"):
            # Its row has the name the mod's options give it.
            said.append("\n".join((
                tr("%s in the mod's file", 'ModOpID="%s"' % tool["id"]),
                tr("One of the mod's options: %s on the mod's row switches it.",
                   tr("Mod options…")),
            )))
        off = modops.described(op.source, self.loadout.game).skip
        said.extend(self._switch_notes(
            off, bool(tool.get("unswitched")), tuple(tool.get("also") or ())
        ))
        return said

    def _stale_words(self) -> str:
        """Beside a row whose reading is in doubt."""
        return tr("being read again") if self.reading_again else tr("may be out of date")

    def _op_tip(self, view) -> str:
        """An op's tooltip: what its row has no room for, a short paragraph
        each.

        Not what the row says already -- how much it changes is beside it --
        nor what is so of every op: that it is written back out as the ModOp
        it came from.  Only an op that is not says how it is written.
        """
        op = view.op
        said = []
        if op.note:
            said.append(first_lines(op.note.strip(), 3))
        if op.uid in self._stale.get(op.origin, ()):
            said.append(tr(
                "What is under it may be out of date: what loads before its "
                "mod has changed since it was read. The export is not affected."
            ))
        elif op.uid in self.moved:
            said.append(tr(
                "Its mod was read again because what loads before it "
                "changed, and this ModOp comes to something else than it did."
            ))
        marks = [mark.tip for mark in keptops.marks(view, self.loadout.game)]
        if op.expression and not op.scope:
            # Where it lands, as written: the row has it by its names, and a
            # pattern not at all.  Cut to fit, since a pattern can be a
            # paragraph; the whole of it is what double-clicking opens.
            marks.append(("  " if marks else "") + keptops.clipped(op.expression))
        if marks:
            said.append("\n".join(marks))
        said.extend(self._tool_notes(op))
        extra = keptops.claims_more(view, self.loadout.game) if self._yours(op.origin) else 0
        if extra:
            said.append(trn(
                "It replaces %(n)s field it leaves as it was, so another mod "
                "that changes that field loses to this one. Narrow ModOps "
                "would claim only what it changes.",
                "It replaces %(n)s fields it leaves as they were, so another "
                "mod that changes one of them loses to this one. Narrow ModOps "
                "would claim only what it changes.",
                extra,
            ))
        if op.kept:
            said.append(tr("Kept as the ModOp it came from, because %s.", tr_stored(op.kept)))
        if view.whole:
            pass
        elif op.active:
            said.append(tr(
                "Written out as one narrow ModOp per asset, because it was %s.",
                view.reason,
            ))
        else:
            said.append(tr(
                "Switched off, so it is written as a comment the game does not read."
            ))
        if view.caveat:
            said.append(tr("The changes shown underneath are not exact: %s.", view.caveat))
        if self.live and self._can_edit(op):
            said.append(tr("Double-click or press Enter to edit it."))
        return "\n\n".join(said)

    @staticmethod
    def _can_edit(op) -> bool:
        """Whether an op's row opens it in the pattern editor to be changed:
        one ModOp aimed by a search.  See ui.pattern_dialog."""
        return bool(op is not None and op.can_expand and op.searches and not op.scope)

    @staticmethod
    def _one_origin(changes) -> str:
        """The mod a set of changes all belong to, or "" if they disagree."""
        found = {change.origin for change in changes}
        return found.pop() if len(found) == 1 else ""

    def _add_group(self, parent, group, mono) -> None:
        node = QTreeWidgetItem(parent)
        node.setData(0, KIND_ROLE, "group")
        node.setData(0, ORIGIN_ROLE, self._one_origin(group.changes))
        node.setData(
            0, SHORT_ROLE,
            "" if group.is_cluster else self._without_internal(group.lead, group.label),
        )
        node.setText(0, group.label)
        node.setText(1, group.caption())
        node.setForeground(1, QColor(theme.TEXT_DIM))
        node.setToolTip(0, self._group_tip(group))
        self._remember_text(node, " ".join(asset.guid for asset in group.assets))
        if self.live:
            node.setFlags(
                node.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
        node.setData(0, OPEN_ROLE, group.lead)

        if group.is_cluster:
            for asset in group.assets:
                branch = QTreeWidgetItem(node)
                branch.setData(0, KIND_ROLE, "asset")
                branch.setData(0, ORIGIN_ROLE, self._one_origin(asset.changes))
                branch.setData(0, SHORT_ROLE, self._without_internal(asset.guid, asset.label))
                branch.setText(0, asset.label)
                branch.setText(1, trn("%(n)s change", "%(n)s changes", len(asset.changes)))
                branch.setForeground(1, QColor(theme.TEXT_DIM))
                branch.setData(0, OPEN_ROLE, asset.guid)
                branch.setToolTip(
                    0, "%s\nGUID %s" % (asset.label, asset.guid)  # i18n: keep - GUID
                )
                self._remember_text(branch, asset.guid)
                if self.live:
                    branch.setFlags(
                        branch.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsAutoTristate
                    )
                for change in asset.changes:
                    self._add_change(branch, change, mono)
                branch.setExpanded(True)
        else:
            for change in group.assets[0].changes:
                self._add_change(node, change, mono)

        node.setExpanded(group.lead not in self._collapsed)

    def _add_change(self, parent, change, mono, asset: str = "", field: bool = True) -> None:
        """A change's row.  ``asset`` is the name of its asset, for a row that
        has no row for the asset over it, and ``field`` whether it still has
        to say which field: see :meth:`_add_op`."""
        item = QTreeWidgetItem(parent)
        item.setData(0, KIND_ROLE, "change")
        item.setData(0, GUID_ROLE, change.guid)
        item.setData(0, PATH_ROLE, change.path)
        item.setData(0, KEY_ROLE, list(change.key))
        # Which mod it belongs to, on the row itself. A mod row carries this
        # too, but there is only one of those when more than one mod is loaded
        # -- and the right-click menu has to work in a loadout with one.
        item.setData(0, ORIGIN_ROLE, change.origin)
        # A new-asset op is anchored on a neighbour, so its own GUID is the one
        # to open -- opening the anchor would show the wrong asset.
        item.setData(0, OPEN_ROLE, open_guid(change))

        # A bare tag is useless here: dozens of unrelated fields are called
        # "Value". Show enough of the path to identify which one.
        #
        # A creating op has no path at all -- it anchors on a neighbour -- so
        # "the asset itself" is all field_label can make of it, which named the
        # wrong asset's row and told you nothing about the new one. The asset
        # being made is what the row is about, so that is what it says.
        if change.creates_asset:
            item.setText(
                0, effective.new_asset_name(change) or field_label(change.path)
            )
            # The new asset's name, where every other row here is a path.
            item.setData(0, SHORT_ROLE, "")
        else:
            item.setText(0, field_label(change.path, self._short_name))
        if asset:
            # What the row falls back to in a narrow panel: the asset by the
            # name the game shows alone, in front of the same field.
            brief = self._without_internal(open_guid(change), asset)
        if asset and field:
            # The asset, then the field in it, as the steps of a path are joined.
            item.setData(0, SHORT_ROLE, brief and " \u203a ".join((brief, item.text(0))))
            item.setText(0, " \u203a ".join((asset, item.text(0))))
        elif asset:
            item.setData(0, SHORT_ROLE, brief)
            item.setText(0, asset)
        item.setFont(0, mono)
        tip = keptops.clipped(change.describe_target(), TIP_LINE)
        if asset:
            # In full, whatever the row had room for.
            tip = asset + "\n" + tip
        if change.positional:
            tip = keptops.clipped(change.path, TIP_LINE) + "\n\n" + tr(
                "Positional path. It may break on a game patch."
            )
        # The same path with its GUIDs looked up. Only worth a line when it says
        # something the row does not already say.
        spelt = named_path(change.path, self._name_of)
        if spelt != field_label(change.path):
            tip += "\n\n" + spelt
        # Which search it came out of, unless that op's row is over this one
        # and says so itself.
        under = bool(change.op_uid) and change.op_uid in self._op_rows
        came_from = "" if under else keptops.describe(self.loadout, change)
        if came_from:
            tip += "\n\n" + came_from
        if change.exception_of:
            excepted = self.loadout.op(change.exception_of)
            tip += "\n\n" + tr(
                "An exception to the ModOp %s: written after it, so on this "
                "asset this value wins.",
                keptops.clipped(excepted.expression) if excepted is not None else "",
            )
        item.setToolTip(0, tip + self._clash_tip(change))

        item.setText(1, effective.change_summary(change, self._short_name))
        item.setToolTip(
            1,
            first_lines(effective.describe_payload(change))
            if change.payload
            else tr("Another mod already set this value. This mod sets it too.")
            if change.restated
            else first_lines(tr_stored(change.summary) or change.describe_change()),
        )
        # The raw path and the asset's own GUID beside what the row shows: the
        # label has resolved both to names, and a GUID is exactly what somebody
        # who has one in hand will paste into the filter box.
        self._remember_text(item, "%s %s %s" % (change.path, change.guid, asset))
        alone = self._op_of(change)
        if not self.live:
            # Read, not switched: no box.
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        elif alone is not None:
            # The whole of what one ModOp does, and so that op's row: the box
            # is the op's.  See keptops.stands_alone.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0, Qt.CheckState.Checked if alone.active else Qt.CheckState.Unchecked
            )
        elif change.op_uid and self.loadout.op(change.op_uid) is not None:
            # One of a kept op's answers. It is not a thing that can be off on
            # its own: the op applies to every asset it matches or to none,
            # and "except this one" is a further op, not a hole in this one.
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setToolTip(
                0,
                item.toolTip(0) + "\n\n" + tr(
                    "Part of a ModOp kept as written. Switch that ModOp off, "
                    "not this one change."
                ),
            )
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0, Qt.CheckState.Checked if change.active else Qt.CheckState.Unchecked
            )
        self._paint(item, change)

    def _op_of(self, change):
        """The op a change's row stands for, when the row is the whole of it:
        the kept op it hangs off, where that is drawn as this one row and has no
        row of its own.  ``None`` for any other change."""
        if not change.op_uid or change.op_uid in self._op_rows:
            return None
        return self.loadout.op(change.op_uid)

    def _name_of(self, guid: str) -> str:
        """What a number in a path is called, or "" when it is not a GUID.

        Cached for the life of a redraw: a mods folder is thousands of rows and
        the same handful of goods turn up in most of them.  Anything the file
        has never heard of answers "" rather than "GUID 4" -- most numbers in a
        path are amounts and indices, not references.
        """
        if guid in self._names:
            return self._names[guid]
        found = ""
        if self.view is not None and self.view.guid_exists(guid):
            said = self.view.display_name(guid)
            if said and not said.startswith("GUID "):
                found = said
        self._names[guid] = found
        return found

    def _short_name(self, guid: str) -> str:
        """What a GUID in a path is called in game, for the row itself.

        The localised name alone, without the internal one beside it that
        :meth:`_name_of` gives the tooltip: a row is a summary, and the tooltip
        still spells the path out exactly.
        """
        if guid in self._short:
            return self._short[guid]
        found = ""
        if self.view is not None and self.view.guid_exists(guid):
            row = self.view.asset_row(guid)
            if row is not None:
                found = row.display or row.name or ""
        self._short[guid] = found
        return found

    def _without_internal(self, guid: str, label: str) -> str:
        """An asset's label with its internal name left off, or "" when the
        label has none: *Bear Hunter* for *Bear Hunter  (agriculture_arctic_04
        (Bear Hunter))*.  See :attr:`core.index.AssetRow.label`, which puts
        the internal name in brackets after the one the game shows."""
        short = self._short_name(guid)
        at = label.find(short + "  (") if short else -1
        # At the very start, or after the "+ " a new asset's label opens with.
        if at < 0 or label[:at] not in ("", "+ ") or not label.endswith(")"):
            return ""
        return label[: at + len(short)]

    def _paint(self, item, change) -> None:
        """A switched-off change is greyed, so the list reads at a glance."""
        clash = self._clashes.get(change.key)
        if not change.active:
            # Marked, so a glyph it wore while it was on comes off with it.
            theme.mark(item, "text-dim", 0)
            item.setForeground(1, QColor(theme.TEXT_DIM))
            return
        item.setForeground(1, QColor(self._colour_for(change)))
        if clash is not None and not clash.is_harmless:
            # Contested, so the row says which of the two the game will read.
            # The one that loses is greyed the way a switched-off change is,
            # because in play that is exactly what it amounts to.
            beaten = clash.winner.key != change.key
            theme.mark(item, "text-dim" if beaten else "warn", 0)
            if beaten:
                item.setForeground(1, QColor(theme.TEXT_DIM))
        elif change.positional:
            theme.mark(item, "warn", 0)
        else:
            theme.mark(item, "text", 0)
        # The warning glyph a mark gives is drawn left of the row's arrow --
        # see ChangesTree.drawBranches -- so that a row that is a ModOp can
        # wear its type's badge as well: one kept that is this one change, or
        # a narrow one the Studio writes.  A row under an op with a row of
        # its own is what that op comes to, and wears none.
        warned = not item.icon(0).isNull()
        tone = "warn" if warned else ""
        item.setData(0, STATUS_ROLE, tone or None)
        is_op = not change.op_uid or self._op_of(change) is not None
        item.setIcon(0, op_marks.icon(change.op) if is_op else QIcon())

    def _paint_op(self, item, op) -> None:
        """A kept op's own colour, and the grey it wears when it is switched off.

        Not the mod heading's amber, which is what it used to wear: the two
        kinds of heading in this list sat at different depths in the same
        colour, and depth is the one thing a folded tree takes away.

        Greyed rather than struck through when off, because that is what
        happens to the rows underneath it -- switching an op off switches
        every answer it came to off with it -- and the heading should read the
        way its own list reads. A mod row is struck through instead: a mod the
        game is not loading is a different claim from an edit left out.
        """
        on = op is None or op.active
        # An op there is something to say of wears the colour; one kept as
        # every op is reads as the changes around it do.
        marked = op is None or op.searches or op.kept
        item.setForeground(
            0, QColor((theme.RULE if marked else theme.TEXT) if on else theme.TEXT_DIM)
        )
        item.setForeground(1, QColor(theme.TEXT_DIM))

    def _clash_tip(self, change) -> str:
        clash = self._clashes.get(change.key)
        if clash is None:
            return ""
        return "\n\n" + clash.describe(self._mod_name)

    def _yours(self, origin: str) -> bool:
        """Whether a mod is one of yours to put right: the loadout's own, or
        one whose author is you, by any of your names."""
        if is_own_mod(origin):
            return True
        layer = self.loadout.layer(origin)
        return layer is not None and patching.by_you(self.loadout, layer.creator)

    def _mod_name(self, origin: str) -> str:
        return self.loadout.label_of(origin) or tr("your mod")

    def _group_tip(self, group) -> str:
        # Which assets a cluster holds is the rows under it.
        lines = [
            trn("%(group)s\n%(n)s related asset, grouped here because the base "
                "game's own references say they belong to one another.",
                "%(group)s\n%(n)s related assets, grouped here because the base "
                "game's own references say they belong to one another.",
                len(group.assets), group=group.label)
            if group.is_cluster
            else "%s\nGUID %s" % (group.label, group.lead)
        ]
        if self.live:
            lines.append("\n" + (
                tr("Double-click or press Enter to open %s in the editor.", group.assets[0].label)
                if group.is_cluster
                else tr("Double-click or press Enter to open this asset in the editor.")
            ))
        return "\n".join(lines)

    @staticmethod
    def _colour_for(change) -> str:
        if change.op == "remove":
            return theme.REMOVED
        return theme.CHANGED
