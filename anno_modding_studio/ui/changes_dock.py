"""Always-visible review list: every pending edit, grouped and switchable.

A flat list was fine while a loadout was a dozen edits somebody typed one at a
time.  Importing another author's mod lands three hundred at once, most of which
the user did not write and some of which they will not want, and a flat list of
those is unreadable and un-editable.

So the list is a tree of what the changes are *about* -- see :mod:`core.grouping`
for how assets that belong together are recognised -- and every row carries a
checkbox.  Unticking is not deleting: the change stays in the loadout and in its
saved file, and simply is not part of the mod.  That distinction is the point.
The alternative, "revert it and add it again later", loses the edit, and for an
imported mod there is nowhere to get it back from.
"""

from __future__ import annotations

import time
from contextlib import nullcontext as _nothing
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    build,
    conflicts,
    dependencies,
    games,
    generator,
    grouping,
    infotips,
    keptops,
    loaderlog,
    modops,
    repeats,
    scripts,
    stale,
    zipped,
)
from ..core.changes import is_own_mod
from ..core.i18n import join_list, number, tr, tr_stored, trn
from . import op_marks, theme
from . import unread_dialog as unread_mod
from .change_rows import (  # noqa: F401 - the names half the window imports from here
    FIND_ROLE,
    GOTO_ROLE,
    GUID_ROLE,
    KEY_ROLE,
    KIND_ROLE,
    OP_ROLE,
    OPEN_ROLE,
    ORIGIN_ROLE,
    PART_ROLE,
    PATH_ROLE,
    SHORT_ROLE,
    STATUS_ROLE,
    UNBUILT_ROLE,
    UNREAD_ROLE,
    ChangeRows,
    ChangesTree,
    NameFirstDelegate,
    changes_tree,
    compact_step,
    field_label,
    named_path,
    open_guid,
)
from .help_tip import HelpTip
from .widgets import FlowLayout, ask, enter_activates, open_on_disk, open_script
from .xml_view import XmlDialog

# The rows that are written as ModOps, and so have XML to show. A bundled
# mod's row stands for a row somewhere else, which is the one to ask.
XML_KINDS = ("mod", "op", "part", "group", "asset", "change", "unread")

# How long the filter goes on making rows before it lets the window answer
# again, in seconds.  A mod's rows are made whole, so one long mod overruns.
# Not shorter: each slice has the view take stock of every change once
# before it can be asked anything (Workspace.steady), 30 to 50 ms on a list
# of 32,000 changes.  At half this the filter took half as long again in
# all, for a longest wait no shorter: that is one long mod's rows.
FILTER_SLICE = 0.2

# What the filter looks in, as the box beside it has it: every row, the mods'
# own rows alone, or one mod's rows (``_scope_origin``).
SCOPE_ALL = "all"
SCOPE_NAMES = "names"
SCOPE_MOD = "mod"

# How many of a list a mod row's tooltip names before saying how many more:
# its changed options, or the mods bundled inside it.
OPTIONS_SHOWN = 5


def _written_as_comments(view) -> int:
    """How many things the generator writes ``view`` with as comments: the
    ops and changes it shelves.  Counted here apart from it, a change whose
    op was gone was a comment in the XML and none in the count."""
    ops, changes = generator.shelved(view)
    return len(ops) + len(changes)


class ChangesPanel(QWidget, ChangeRows):
    """Docked review list: every pending edit, across every asset."""

    # guid, path, and the mod it is a change of: its tab is the one opened.
    # Last, the change's key, or None for a row that is not one change.
    changeActivated = Signal(str, str, str, object)
    changesRemoved = Signal()
    # Mark the op this change is written as to be kept as written (its key),
    # or take the mark off a kept op (its uid).
    activationChanged = Signal()
    conflictsRequested = Signal()
    # The unread op to open the list on, as (mod, place), or None for them all.
    unreadRequested = Signal(object)
    heldRequested = Signal(str)   # origin: the edits an update held back
    opActivated = Signal(str)  # the uid of an op to open for editing
    # Show this in its mod's file: the mod, "op", "change" or "unread", and
    # the op's uid, the change's key or the unread op's place.
    fileRequested = Signal(str, str, object)
    # Raised by the right-click menu. The panel knows which mod a row belongs
    # to; what to do about it is the window's business, and each of these is
    # something the window already does from somewhere less findable.
    newPatternRequested = Signal()
    optionsRequested = Signal(str)  # origin: re-read that mod at other settings
    # One of a mod's options, or all of them with "", to open where they
    # are declared: the mod's origin and the option's name.
    optionRequested = Signal(str, str)
    # The rows picked, to be put under one of their mod's options: the mod,
    # and each as ``(kind, which)`` (:func:`core.option_groups.runs`).
    optionGroupRequested = Signal(str, list)
    patchRequested = Signal(str)    # origin: put a patch mod in front of it
    forkRequested = Signal(str)     # origin: make your own copy of it
    projectRequested = Signal(str)  # origin: a mod of yours nothing keeps, made a project
    needRequested = Signal(str)     # a patch: make it need another mod too
    unneedRequested = Signal(str, str)  # a patch, and a mod it is to stop needing
    unloadRequested = Signal(str)   # origin: forget it and everything it brought
    addModsRequested = Signal()     # read more mods in beside these
    updateRequested = Signal(str)   # origin: ask mod.io whether it has a newer file

    def __init__(self, loadout, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.view = None
        # The last reading of what the game will load, when the window has
        # one: it knows the mods Play cannot switch, which the loadout alone
        # does not. See :func:`core.dependencies.after_play`.
        self.found = None
        # Whether a change to one of a mod's own ModOps goes into its file,
        # given its origin: the window's answer, which knows what was said
        # about patches this session. Without one, core.patching's.
        self.changed_directly = None
        # Whether an edit inside the ops of each of a list of mods may go
        # on: the window's, which asks the three ways on of somebody else's
        # mod (section 5.2 of the projects and loadouts proposal). Without
        # one, it may. Where it says which mod each one's ops are in now,
        # the copies *Make your own copy* put in their place, keyed by the
        # mod each replaced, until the next asking (:meth:`_again`).
        self.may_edit_inside = None
        # The folder a mod is read from, given the one its layer records:
        # the window's, which knows the library a mod with no link is kept
        # in (core.library.reading_folder). Without one, the folder recorded.
        self.reading_folder = None
        self._copied = {}
        self._needs = None
        # Mods that are on and not loaded because another replaces them, and
        # the section each mod row was drawn from -- so a switch flipped can
        # repaint every row it touches without redrawing the list.
        self._replaced = {}
        self._on = set()
        # Each mod's origin -> the copy of it the game loads: see
        # Loadout.loading_copies.
        self._copies = {}
        # Each mod's origin -> the mod whose switch it answers to: see
        # Loadout.switch_of.
        self._holders = {}
        self._mod_sections = {}
        # Every mod's row by its origin, a mod before the mods inside it: a
        # bundled mod's row is under its host's, not at the top of the list.
        self._mod_items = {}
        # Which mods run scripts, and the script files each one's assets name
        # -- see :mod:`core.scripts`. Asked once a rebuild.
        self._runs = {}
        self._named = {}
        # Module names and globals two loading mods' scripts both claim.
        self._shared = []
        # Assets created under a GUID something else has: see core.repeats.
        self._repeats = []
        # Which mods on this PC create each GUID, once the window has scanned
        # for them (see set_elsewhere); {} until then.
        self._elsewhere = {}
        # The names more than one mod in the loadout goes by: see _mod_row_text.
        # The InfoTip patches of the mods that load -- see core.infotips.
        self._infotips = None
        # What the game's loader said the last time it started, for the ops of
        # each mod that did nothing -- see core.loaderlog.
        self._log = None
        # What the rows themselves are drawn from: see ChangeRows.
        self._init_rows()
        self._opened = set()
        # How rows were opened and shut by hand while a filter is on, by
        # _fold_key, and the filter that was: a refill under the same filter
        # -- an undo, a mod read again -- leaves them so (H2-20).
        self._filter_folds = {}
        self._folds_needle = ()
        # The mod the filter is narrowed to, where it is: see SCOPE_MOD.
        self._scope_origin = None
        # Of a filter by the mods' names, how many mods it leaves showing.
        self._names_shown = None
        self._toggling = False
        self._groups = []
        self._ops = []
        self._sections = None
        self._owners = None
        self._grouped_from = None
        self._loading = False
        # How many change rows the filter is leaving visible, or None when
        # there is no filter on and the answer is "all of them".
        self._shown = None
        # Mods drawn at least once, so a first drawing can decide whether a
        # mod starts open without overruling how it was left afterwards.
        self._drawn = set()
        # Ticking a group box walks every change under it one at a time, and
        # each one would otherwise rebuild the editor's tree. Announce the lot
        # once, when the burst is over.
        self._announce = QTimer(self)
        self._announce.setSingleShot(True)
        self._announce.setInterval(0)
        self._announce.timeout.connect(self.activationChanged)
        self._build_ui()
        self.refresh()

    def set_index(self, view) -> None:
        """The workspace, once there is one: grouping needs the reference graph."""
        self.view = view
        self._owners = None
        self._grouped_from = None
        self._names = {}
        self._short = {}
        self.refresh()

    def _grouping(self):
        """The grouped change set, rebuilt only when the change set moves.

        Which assets belong together is a question about the base game, so
        retyping a value cannot change the answer -- and asking it again costs a
        reference lookup per asset, on a panel that redraws after every
        keystroke that lands.

        Returns a list of :class:`grouping.ModSection`, one mod alone included:
        the list reads the same way with one mod open as with sixty, and the
        mod's row is where its name, its switch and its menu are.
        """
        signature = (
            tuple(
                sorted(
                    # Whether a change is on is part of the grouping, not just
                    # of how a row is painted: a kept op's row says how many
                    # of the changes under it are marked off.  And
                    # which object it is: the rows are drawn from the objects
                    # grouped, so a change or an op typed over under the same
                    # key (a value retyped, a pattern applied again in the XML
                    # view) went on being drawn as it was before (H2-2).
                    (grouping.asset_guid(change), change.key, change.active, id(change))
                    for change in self.loadout.changes.values()
                )
            ),
            tuple(sorted((op.uid, op.active, id(op)) for op in self.loadout.ops)),
            # Which mods are loaded, not only which have changed something: a
            # mod with nothing in it yet still gets a row, and a patch mod is
            # exactly that at the moment it is made.
            tuple(sorted(layer.origin for layer in self.loadout.layers)),
            # Your own mod being made adds a row without moving a change.
            self.loadout.has_own_mod(),
            # And what its row says of it. A loaded mod's row reads the mod
            # itself each time it is drawn; your own is described afresh for
            # each grouping (Loadout.own_layer), so one kept from before a
            # rename went on saying the old name until the loadout was opened
            # again.
            (self.loadout.own.name, self.loadout.own.creator, self.loadout.own.version),
        )
        if signature != self._grouped_from:
            steady = getattr(self.view, "steady", None)
            with steady() if steady is not None else _nothing():
                # The ownership cache outlives the redraw: the base game's own
                # references are what it reads, and those do not move while the
                # app is open.
                self._owners = grouping.owners_for(self.view, self._owners)
                self._sections = grouping.by_mod(
                    self.loadout, self.view, self._owners
                )
                self._groups = [
                    group for section in self._sections for group in section.groups
                ]
                self._ops = [
                    held
                    for section in self._sections
                    for op in section.ops
                    for held in op.every()
                ]
                self._op_rows = {op.uid for op in self._ops}
            self._grouped_from = signature
        return self._sections, self._ops, self._groups

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # A mods folder is thousands of rows under dozens of mod headings, and
        # until now the only way to find one was to scroll. Above the tree
        # rather than below it: it is a way of addressing the list, the same
        # way the editor's own field filter sits above its tree.
        self.filter_box = QLineEdit()
        self.filter_box.setObjectName("Search")
        self.filter_box.setPlaceholderText(
            tr("Filter changes…  (e.g. “lumberjack”, “CycleTime”, a mod's name)")
        )
        self.filter_box.setAccessibleName(tr("Filter changes"))
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.setToolTip(tr(
            "Shows only rows that mention this text. Filtering changes "
            "nothing in the mod."
        ))
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._start_filter)
        # The mods a filter has still to look in: see _start_filter.
        self._unsearched = []
        self._matched = set()
        self._filter_step = QTimer(self)
        self._filter_step.setSingleShot(True)
        self._filter_step.setInterval(0)
        self._filter_step.timeout.connect(self._search_some)
        self.filter_box.textChanged.connect(self._filter_timer_start)
        # A hundred mods' changes mention most words, and a mod looked for by
        # its name was one row among all the changes that mention it.  So the
        # filter can look in the mods' own rows alone, or in one mod's.
        self.scope_box = QComboBox()
        self.scope_box.setAccessibleName(tr("Filter in"))
        self.scope_box.addItem(tr("Mods and changes", context="filter in"), SCOPE_ALL)
        self.scope_box.addItem(tr("Mod names", context="filter in"), SCOPE_NAMES)
        self.scope_box.setToolTip(tr(
            "What the filter looks in. A mod's menu has Filter this mod's changes."
        ))
        # Not as wide as its longest entry, which can be a mod's name: the
        # panel is narrow, and the filter box beside it is what is typed in.
        self.scope_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.scope_box.setMinimumContentsLength(10)
        self.scope_box.currentIndexChanged.connect(self._on_scope)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.filter_box, 1)
        top.addWidget(self.scope_box)
        # The window adds what it has to say about the panel as a whole at
        # the end of this row, where it goes wherever the panel goes.
        self.top_row = top
        self.legend_tip = HelpTip(
            tr("Each row that is a ModOp wears a badge for what it does. A ModOp "
               "kept as written has up to four marks after it, for what the game "
               "works out as it loads. A mark that is filled comes to something "
               "as things stand."),
            tr("The marks on a ModOp"),
            legend=op_marks.legend,
        )
        top.addWidget(self.legend_tip)
        outer.addLayout(top)

        self.tree = changes_tree()
        self.tree.setAccessibleName(tr("Changes"))
        theme.notifier().changed.connect(self._redraw_marks)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        enter_activates(self.tree)
        self.tree.itemActivated.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        # First: a mod's rows are made when it is first opened.
        self.tree.itemExpanded.connect(self._build_rows)
        self.tree.itemExpanded.connect(self._remember_open)
        self.tree.itemCollapsed.connect(self._remember_closed)
        self.tree.itemSelectionChanged.connect(self._sync_revert)
        outer.addWidget(self.tree, 1)

        # The summary gets its own row rather than sharing one with the buttons:
        # this panel's minimum width is narrow enough that squeezing a label in
        # beside them left it only ~100px, well under what a summary needs.
        self.summary = QLabel("")
        self.summary.setObjectName("SubHeading")
        # The panel is as wide as the buttons along its foot need and no
        # wider for a sentence: a long one is cut at the panel's edge, as it
        # always was, where asking for its width would push the window out.
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.summary)

        # Only ever shown when there is something to say: with one mod loaded
        # nothing can be contested, and a permanent "0 conflicts" is noise.
        self.conflicts_button = QPushButton("")
        self.conflicts_button.setToolTip(tr(
            "Fields set by more than one loaded mod. Only the last one "
            "loaded takes effect."
        ))
        self.conflicts_button.clicked.connect(self.conflictsRequested)
        self.conflicts_button.setVisible(False)
        outer.addWidget(self.conflicts_button)

        # The other thing the list cannot say for itself: the ops that produced
        # no changes at all, and so are nowhere in it.
        self.unread_button = QPushButton("")
        self.unread_button.setToolTip(tr(
            "ModOps the Studio could not read. Their mods do more than this list shows."
        ))
        self.unread_button.clicked.connect(lambda: self.unreadRequested.emit(None))
        self.unread_button.setVisible(False)
        outer.addWidget(self.unread_button)
        # Each of these two is a sentence, and comes and goes with what is
        # loaded: like the summary, neither makes the panel wider.
        for button in (self.conflicts_button, self.unread_button):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        # A row that wraps. Side by side in a box the three were squeezed
        # under what their text needs wherever the panel was at its
        # narrowest and the language was not English: "wahl zurückset" and
        # "lle zurücksetze", for the two that revert, in German.
        row = FlowLayout(spacing=outer.spacing())
        self.fold_button = QPushButton(tr("Collapse all"))
        self.fold_button.setToolTip(tr("Show one row per asset instead of every change."))
        self.fold_button.clicked.connect(self._toggle_fold)
        row.addWidget(self.fold_button)
        self.remove_button = QPushButton(tr("Revert selected"))
        self.remove_button.clicked.connect(self._remove_selected)
        row.addWidget(self.remove_button)
        # Where Revert all was: getting more mods into the list is what the
        # list is for, and it was only on its right-click menu and in Mods.
        # Revert all, which throws every change away, is on the right-click
        # menu now, beside Revert selected, and not a click from Add mods.
        self.add_button = QPushButton(tr("Add mods…"))
        self.add_button.setToolTip(tr(
            "From the mods folder, the library, mod.io or a zip, beside this "
            "loadout's mods."
        ))
        self.add_button.clicked.connect(self.addModsRequested.emit)
        row.addWidget(self.add_button)
        outer.addLayout(row)

    # -- building the tree ----------------------------------------------
    def regroup(self) -> None:
        """Redraw from scratch, grouping and all.

        The grouping is held against what the changes say, which is right
        after an edit and wrong after an undo: that puts back copies saying
        exactly what the grouped objects last said, without being them -- so
        the rows would go on painting the objects undo took away.
        """
        self._grouped_from = None
        self.refresh()

    # -- finding one row among thousands ---------------------------------
    def _filter_timer_start(self) -> None:
        # Clearing the box is a keystroke like any other, but it is also the
        # one the user is waiting on -- the whole list coming back -- so it
        # happens at once rather than after the settling delay.
        if not self.filter_box.text().strip():
            self._filter_timer.stop()
            self._apply_filter()
            return
        self._filter_timer.start()

    def _needle(self) -> str:
        return self.filter_box.text().strip().lower()

    def _filtering(self) -> bool:
        """Whether a filter is on: something typed, or the list narrowed to
        one mod, which hides the others with nothing typed."""
        return bool(self._needle()) or self.scope_box.currentData() == SCOPE_MOD

    def _filter_key(self) -> tuple:
        """The filter on, as folds made under it are kept by; () for none."""
        if not self._filtering():
            return ()
        kind = self.scope_box.currentData()
        return (self._needle(), kind, self._scope_origin if kind == SCOPE_MOD else None)

    def filter_mod(self, origin: str) -> None:
        """Narrow the filter to the rows of ``origin``'s mod: an entry of
        its own at the end of the box beside the filter, and chosen."""
        label = self._mod_name(origin)
        self._scope_origin = origin
        was = self.scope_box.blockSignals(True)
        try:
            if self.scope_box.count() > 2:
                self.scope_box.removeItem(2)
            self.scope_box.addItem(tr("In %s", label, context="filter in"), SCOPE_MOD)
            self.scope_box.setItemData(2, label, Qt.ItemDataRole.ToolTipRole)
            self.scope_box.setCurrentIndex(2)
        finally:
            self.scope_box.blockSignals(was)
        self._on_scope()
        self.filter_box.setFocus()

    def clear_filter(self) -> None:
        """Every row back: nothing typed, and the filter in every row."""
        was = self.filter_box.blockSignals(True)
        self.filter_box.clear()
        self.filter_box.blockSignals(was)
        if self.scope_box.currentData() == SCOPE_MOD:
            self._forget_scope()
        self._apply_filter()

    def _forget_scope(self) -> None:
        """Take away the entry of the mod the filter was narrowed to, and go
        back to every row."""
        was = self.scope_box.blockSignals(True)
        try:
            if self.scope_box.count() > 2:
                self.scope_box.removeItem(2)
            self.scope_box.setCurrentIndex(0)
        finally:
            self.scope_box.blockSignals(was)
        self._scope_origin = None
        self._say_scope()

    def _on_scope(self) -> None:
        self._say_scope()
        self._filter_timer.stop()
        self._start_filter()

    def _say_scope(self) -> None:
        """The box's hint, for what it is filtering."""
        kind = self.scope_box.currentData()
        if kind == SCOPE_NAMES:
            self.filter_box.setPlaceholderText(tr("Filter mods by name or folder…"))
        elif kind == SCOPE_MOD:
            self.filter_box.setPlaceholderText(
                tr("Filter the changes of %s…", self._mod_name(self._scope_origin or ""))
            )
        else:
            self.filter_box.setPlaceholderText(
                tr("Filter changes…  (e.g. “lumberjack”, “CycleTime”, a mod's name)")
            )

    def _apply_filter(self) -> None:
        """Filter the whole list, now: every mod looked in before this
        returns.  What somebody typing gets is :meth:`_start_filter`."""
        self._start_filter()
        self.finish_filter()

    def _start_filter(self) -> None:
        """Filter what there are rows for, and look in the rest a mod at a
        time as the window has a moment.

        A filter has to see every row, and a folded mod's rows are not made
        until something needs them: the first keystroke after an edit made
        all of them at once, 32,360 rows on a list of 417 mods, and the
        window did not answer for three to five seconds.  The rows still
        have to be made.  They are made a few mods at a time between the
        window's other work, each mod filtered as its rows arrive, and the
        line under the list says how many are still to be looked in.

        The mods that matched last time are looked in first: a filter left
        on through an edit shows what it showed before the rest is gone
        through again.
        """
        needle = self._needle()
        self._filter_step.stop()
        under = self._filter_key()
        if under != self._folds_needle:
            self._filter_folds = {}
            self._folds_needle = under
        self._names_shown = None
        kind = self.scope_box.currentData()
        if kind == SCOPE_MOD and self._scope_origin not in self._mod_items:
            # Unloaded, or reverted to nothing: there is no row to narrow to.
            self._forget_scope()
            kind = SCOPE_ALL
        if kind == SCOPE_MOD:
            self._filter_one(needle, self._mod_items[self._scope_origin])
            return
        if kind == SCOPE_NAMES and needle:
            self._filter_names(needle)
            return
        waiting = []
        # setExpanded fires the signals that remember how the user left a row
        # folded. Hiding rows is not folding them, and a filter must not be
        # able to rearrange what the list looks like once it is cleared.
        self._loading = True
        try:
            shown = 0
            # The mods inside a mod before it: a host stays in sight for what
            # was found inside it.
            for node in reversed(list(self._mod_items.values())):
                if needle and node.data(0, UNBUILT_ROLE):
                    # Not known yet. Out of the way until it is, unless the
                    # mod itself is what was asked for.
                    node.setHidden(
                        needle not in (node.data(0, FIND_ROLE) or "")
                        and not self._holds_shown(node)
                    )
                    waiting.append(node)
                else:
                    shown += self._filter_item(node, needle)
            waiting.reverse()
        finally:
            self._loading = False
        first, rest = [], []
        for node in waiting:
            (first if node.data(0, ORIGIN_ROLE) in self._matched else rest).append(node)
        self._unsearched = first + rest
        if not needle:
            self._matched = set()
        self._shown = shown if needle else None
        self._refresh_summary()
        self._sync_fold_button()
        if self._unsearched:
            self._filter_step.start()

    def _filter_names(self, needle: str) -> None:
        """Show the mods whose row or folder name holds ``needle``.  What is
        under each is as it was left, and no rows are made for it: the mod
        is what was asked for, and it opens as it is opened by hand."""
        self._unsearched = []
        self._matched = set()
        self._loading = True
        try:
            named = 0
            # The mods inside a mod before it, as in _start_filter.
            for node in reversed(list(self._mod_items.values())):
                origin = node.data(0, ORIGIN_ROLE) or ""
                if needle in ("%s %s" % (node.text(0), origin)).lower():
                    self._filter_item(node, "")
                    if self._holds_shown(node):
                        # Folded back as it was left, it hid the mod named
                        # inside it, which is counted as shown.
                        node.setExpanded(True)
                    named += 1
                    self._reveal_above(node, needle)
                else:
                    # Shown only for a mod inside it, if that.
                    for child in self._own_rows(node):
                        child.setHidden(True)
                    node.setHidden(not self._holds_shown(node))
        finally:
            self._loading = False
        self._names_shown = named
        self._shown = 0
        self._refresh_summary()
        self._sync_fold_button()

    def _filter_one(self, needle: str, node) -> None:
        """Show one mod's row alone, open, with those of its rows that hold
        ``needle``, or all of them for nothing typed.  A mod it is inside
        is shown for it, with none of that mod's own rows."""
        self._unsearched = []
        self._loading = True
        try:
            for other in self._mod_items.values():
                if other is not node:
                    other.setHidden(True)
            self._build_rows(node)
            found = self._filter_item(node, needle)
            node.setHidden(False)
            node.setExpanded(True)
            above = node.parent()
            while above is not None:
                for child in self._own_rows(above):
                    child.setHidden(True)
                above.setHidden(False)
                above.setExpanded(True)
                above = above.parent()
        finally:
            self._loading = False
        self._shown = found
        self._refresh_summary()
        self._sync_fold_button()

    def _search_some(self) -> None:
        """Look in the next mods the filter has not seen, for one slice."""
        needle = self._needle()
        until = time.perf_counter() + FILTER_SLICE
        # Held over the slice and not taken a mod at a time: taking it is
        # the stock-taking, and 407 mods each taking it made the filter
        # four times as long as making every row at once had been.
        with self._steady():
            while self._unsearched:
                self._search(self._unsearched.pop(0), needle)
                if time.perf_counter() >= until:
                    break
        if self._unsearched:
            # Only the line: the rest of the summary is counted over the
            # whole loadout, and has not moved.
            self._say_filtered()
            self._filter_step.start()
        else:
            self._filter_step.stop()
            self._refresh_summary()
        self._sync_fold_button()

    def _steady(self):
        """The view held still while rows are made: see refresh()."""
        steady = getattr(self.view, "steady", None)
        return steady() if steady is not None else _nothing()

    def _search(self, node, needle: str) -> None:
        """Make one mod's rows and filter them."""
        painting = self.tree.updatesEnabled()
        self.tree.setUpdatesEnabled(False)
        was = self._loading
        self._loading = True
        try:
            self._build_rows(node)
            found = self._filter_item(node, needle)
            if not node.isHidden():
                self._reveal_above(node, needle)
        finally:
            self._loading = was
            self.tree.setUpdatesEnabled(painting)
        self._shown = (self._shown or 0) + found
        origin = node.data(0, ORIGIN_ROLE) or ""
        if node.isHidden():
            self._matched.discard(origin)
        else:
            self._matched.add(origin)

    def finish_filter(self, origins=None) -> None:
        """Look in every mod the filter has still to see, before this
        returns: for whatever acts on what the filter shows.  In the mods
        of ``origins`` alone, where it is only about those."""
        if not self._unsearched:
            return
        needle = self._needle()
        left = []
        with self._steady():
            for node in self._unsearched:
                if origins is None or (node.data(0, ORIGIN_ROLE) or "") in origins:
                    self._search(node, needle)
                else:
                    left.append(node)
        self._unsearched = left
        if not left:
            self._filter_step.stop()
        self._refresh_summary()
        self._sync_fold_button()

    def _filter_item(self, item, needle: str) -> int:
        """Hide what does not match, keeping the ancestors of what does.

        Returns how many change rows are left showing underneath, so the
        summary can say what the list is now a part of.

        A row that matches takes its whole subtree with it: asking for a mod by
        name, or an asset by name, means asking for what it holds -- and a
        parent that matched while its children vanished would look like an
        empty mod rather than like a found one.
        """
        mine = not needle or needle in (item.data(0, FIND_ROLE) or "")
        # Under a match everything matches, which is what "takes its subtree
        # with it" means; the recursion carries an empty needle to say so.
        below = needle if not mine else ""
        found = 0
        for child in self._own_rows(item):
            found += self._filter_item(child, below)

        visible = mine or found > 0 or self._holds_shown(item)
        item.setHidden(not visible)
        if not needle:
            # Back to how the user left it, rather than to how the filter left
            # it: the fold state is theirs and the filter only borrowed it.
            wanted = self._was_open(item)
        elif visible and item.childCount():
            wanted = True
        else:
            wanted = None
        if wanted is not None and self._folds_needle:
            # Opened or shut by hand under this filter, before a refill.
            wanted = self._filter_folds.get(self._fold_key(item), wanted)
        if wanted is not None:
            item.setExpanded(wanted)
        if item.data(0, KIND_ROLE) == "mod" and needle:
            origin = item.data(0, ORIGIN_ROLE) or ""
            if visible:
                self._matched.add(origin)
            else:
                self._matched.discard(origin)
        if not visible:
            return 0
        if item.data(0, KIND_ROLE) == "change":
            return 1
        return found

    def _own_rows(self, item) -> list:
        """The rows under ``item`` that are its own: a mod bundled inside the
        one a row is about is a mod of its own, filtered, selected and
        reverted as one, and not part of its host's."""
        return [
            item.child(index) for index in range(item.childCount())
            if item.child(index).data(0, KIND_ROLE) != "mod"
        ]

    @staticmethod
    def _holds_shown(item) -> bool:
        """Whether a mod inside the one at ``item`` has its row showing."""
        return any(
            item.child(index).data(0, KIND_ROLE) == "mod" and not item.child(index).isHidden()
            for index in range(item.childCount())
        )

    def _reveal_above(self, node, needle: str) -> None:
        """Show the hosts above a mod a filter has found something in."""
        above = node.parent()
        while above is not None:
            above.setHidden(False)
            if needle:
                above.setExpanded(True)
                self._matched.add(above.data(0, ORIGIN_ROLE) or "")
            above = above.parent()

    def _was_open(self, item) -> bool:
        """How a row stands when nothing is being filtered -- see ``_rebuild``."""
        kind = item.data(0, KIND_ROLE)
        if kind == "mod":
            return item.data(0, ORIGIN_ROLE) in self._opened
        if kind == "op":
            return item.data(0, OP_ROLE) not in self._collapsed
        if kind == "part":
            return item.data(0, PART_ROLE) in self._opened_parts
        if kind == "idle":
            return ("idle", item.data(0, ORIGIN_ROLE)) in self._opened_parts
        if kind == "group":
            return item.data(0, OPEN_ROLE) not in self._collapsed
        # An asset inside a cluster: always open, the way _add_group draws it.
        return True

    def refresh(self) -> None:
        self._loading = True
        # Once a rebuild, for every mod row's tooltip: what the mods that will
        # load say about each other. Pure, over the loadout's own layers.
        self._needs = dependencies.for_loadout(self.loadout, self.found)
        self._replaced = self.loadout.replaced()
        self._on = {layer.origin for layer in dependencies.switched_on(self.loadout)}
        self._copies = self.loadout.loading_copies()
        self._holders = self.loadout.switch_holders()
        self._mod_sections = {}
        self._named = scripts.named_by(self.loadout)
        self._runs = scripts.runs_code(self.loadout, self._named)
        self._shared = scripts.clashes(self.loadout)
        self._repeats = repeats.find(self.loadout, getattr(self.view, "index", None), self._elsewhere)
        self._infotips = infotips.overview(self.loadout)
        self._log = loaderlog.read_cached(self.loadout.game) if self.loadout.game else None
        # Four thousand rows go in one at a time; repainting between them, and
        # emitting a signal per row nothing is listening for, is most of what
        # the rebuild costs.
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        # Drawing asks the view about thousands of assets -- names, whether
        # each is new -- and nothing edits the loadout meanwhile. Without
        # this every question fingerprinted all the changes again: a whole
        # Anno 1800 folder, 13,000 changes, took a minute and a half to draw.
        steady = getattr(self.view, "steady", None)
        try:
            with steady() if steady is not None else _nothing():
                self._rebuild()
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
            self._loading = False
        if self._filtering():
            # A rebuild draws every row showing; a filter that was on before
            # the edit is still on after it, and refreshes the summary itself.
            self._start_filter()
        else:
            self._shown = None
            self._refresh_summary()
        self._sync_fold_button()
        self.tree.layout_columns()

    def _rebuild(self) -> None:
        # The view forgets which rows were open before the rows go, not as
        # they go: it keeps a list of the open ones and takes each out of it
        # as its row is deleted, which for the tens of thousands of rows a
        # filter has built was most of the clearing.  Clearing resets the
        # view in any case, so nothing is lost that was going to be kept.
        self.tree.reset()
        # The rows a filter was still to look under go with the rest.
        self._filter_step.stop()
        self._unsearched = []
        self.tree.clear()
        sections, _ops, _groups = self._grouping()
        self._clashes = conflicts.by_change(self.loadout)
        self._stale = stale.reached(self.loadout)
        mono = theme.mono_font()

        # One mod alone starts open -- its changes are what you came to see --
        # while a mods folder's start shut. Decided only the first time a mod
        # is drawn, so a row folded by hand is not reopened by the next redraw.
        # Drawn *with something in it*: your own mod has a row in every
        # loadout, empty until you write into it, and an empty row neither
        # makes a second mod of the one you read in nor has anything to open.
        alone = not self._several()
        for section in self._with_content(sections):
            if section.origin not in self._drawn:
                self._drawn.add(section.origin)
                if alone:
                    self._opened.add(section.origin)
                    # And the mods it is inside, or it is open out of sight.
                    host = self._host_of(section.origin)
                    while host is not None:
                        self._opened.add(host.origin)
                        host = self._host_of(host.origin)

        # A mod bundled inside another is drawn inside it: the game loads it
        # as a mod of its own, at its own place in load order, but it switches
        # with its host and is updated with it, and a row of its own at the top
        # of the list read as a mod to be switched off on its own. Placed a
        # host before what is inside it, so each row's parent is there first.
        self._mod_items = {}
        drawn = {section.origin for section in sections}
        inside: dict = {}
        tops = []
        for section in sections:
            above = self._row_above(section.origin, drawn)
            (inside.setdefault(above, []) if above is not None else tops).append(section)
        placed = []

        def place(section, above) -> None:
            placed.append((section, above))
            for held in inside.get(section.origin, ()):
                place(held, section.origin)

        for section in tops:
            place(section, None)

        for section, above in placed:
            node = QTreeWidgetItem(self._mod_items[above] if above is not None else self.tree)
            self._mod_items[section.origin] = node
            node.setData(0, KIND_ROLE, "mod")
            node.setData(0, ORIGIN_ROLE, section.origin)
            node.setData(0, SHORT_ROLE, "")
            # Marked the same way the Generate tab's picker marks it: the one
            # row in this list that an edit in the editor will land in.
            node.setText(0, self._mod_row_text(section.origin, section.label))
            node.setText(1, section.caption())
            if above is not None and self._bundled_row(section.origin):
                # Where it loads, which its place in the list no longer says.
                node.setText(1, tr(", ", context="between items of a list").join([
                    tr("bundled, loads at position %s", number(section.layer.order)),
                    section.caption(),
                ]))
            wide = self._claiming_more(section)
            if wide:
                # Counted for a mod of yours, as its unread ops are: each is
                # something its row's menu can put right.
                node.setText(1, tr(", ", context="between items of a list").join([
                    node.text(1),
                    trn("%(n)s ModOp replaces more than it changes",
                        "%(n)s ModOps replace more than they change", wide),
                ]))
            edited_words = self._edited_words(section.origin)
            if edited_words:
                node.setText(1, tr(", ", context="between items of a list").join([
                    node.text(1), edited_words,
                ]))
            inner = self._pieces(section.origin)
            if inner and not (section.groups or section.ops or section.unread):
                # A box of mods with nothing of its own. "0 changes" would read
                # as a mod the Studio could not see into; what it has is its
                # pieces, each a row underneath.
                node.setText(
                    1,
                    trn("holds %(n)s mod of its own", "holds %(n)s mods of its own",
                        len(inner)),
                )
            if section.origin in self._stale:
                # First, where a narrow column cuts the rest short.
                node.setText(1, tr(", ", context="between items of a list").join([
                    self._stale_words(), node.text(1),
                ]))
            node.setToolTip(0, self._mod_tip(section))
            self._mod_sections[section.origin] = section
            layer = self.loadout.layer(section.origin)
            if layer is not None and self._has_switch(layer):
                # Not a summary of the boxes underneath it: this box is whether
                # the game is loading the mod at all, which is a different fact
                # and one the mods folder itself records. Switching a mod off
                # leaves your tweaks to it exactly as they were. The mod being
                # written here has no such switch, so its row has no box; nor
                # has a bundled mod, which switches with its host.
                node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                node.setCheckState(
                    0,
                    Qt.CheckState.Checked if layer.active else Qt.CheckState.Unchecked,
                )
            else:
                node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self._paint_mod(node, layer)
            # Its folder name as well as its label: a mods folder's rows are
            # often better known by the directory they came out of.
            self._remember_text(node, section.origin)

        # What each mod has of its own goes under the mods inside it, so a
        # folded row's rows, made when it is first opened, go in after them
        # as well.
        for section, _above in placed:
            node = self._mod_items[section.origin]
            inner = self._pieces(section.origin)
            # A mods folder is dozens of mods, so they start shut and are
            # opened one at a time: the opposite default from a single mod's
            # assets, where the changes themselves are what you came to see.
            # And a shut mod's rows wait until it is opened: a folder of 400
            # mods is 50,000 rows, almost none of them on screen, and making
            # them all was most of what every edit cost.
            if section.origin in self._opened:
                self._add_rows(node, section, inner, mono)
                node.setExpanded(True)
            elif section.groups or section.ops or section.unread or any(via for _p, via in inner):
                node.setData(0, UNBUILT_ROLE, True)
                node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

    def _add_rows(self, node, section, inner, mono) -> None:
        """Everything under one mod's row."""
        # The rows of the groups kept as groups, by op: an op the Studio
        # could not read goes under the group it is in.
        self._scope_rows = {}
        # The ops that change nothing as things stand -- already so, or a
        # condition that does not hold -- are one row to open, where there
        # are several: each is still an op of the mod, and a third of a
        # large folder's ops would otherwise stand between a reader and the
        # ones that do something.
        idle = [op for op in section.ops if self._changes_nothing(op)]
        fold = None
        if len(idle) > 1:
            fold = QTreeWidgetItem(node)
            fold.setData(0, KIND_ROLE, "idle")
            fold.setData(0, ORIGIN_ROLE, section.origin)
            fold.setData(0, SHORT_ROLE, "")
            fold.setFlags(fold.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            fold.setText(0, trn(
                "%(n)s ModOp that changes nothing as things stand",
                "%(n)s ModOps that change nothing as things stand",
                len(idle),
            ))
            fold.setForeground(0, QColor(theme.TEXT_DIM))
            fold.setToolTip(0, tr(
                "Each is in the mod's file and is written back as it is. What it "
                "sets is already so, or its condition does not hold, with the mods "
                "loaded now."
            ))
            self._remember_text(fold, "")
        for op in section.ops:
            self._add_op(fold if fold is not None and op in idle else node, op, mono)
        if fold is not None:
            # Shut until somebody opens it, and then as they left it.
            fold.setExpanded(("idle", section.origin) in self._opened_parts)
        for group in section.groups:
            self._add_group(node, group, mono)
        for entry in section.unread:
            inside = [uid for uid in (entry.within or ()) if uid in self._scope_rows]
            self._add_unread(self._scope_rows[inside[-1]] if inside else node, entry, mono)
        # Last, the copies bundled in it that the game takes from another mod:
        # the ones it loads from here are rows of their own, above.
        for piece, via in inner:
            if via:
                self._add_bundled(node, section.origin, piece, via)

    def _changes_nothing(self, op_group) -> bool:
        """Whether an op's row has nothing under it and nothing else to say:
        not a group, not an op whose work is shown in the op it edits."""
        op = op_group.view.op
        return not (
            op_group.changes or op_group.parts or op_group.inside
            or op.scope or op.folded or not op.active
        ) and modops.described(op.source, self.loadout.game).kind != "group"

    def _build_rows(self, node) -> None:
        """Make a folded mod's rows, the first time anything needs them."""
        if not node.data(0, UNBUILT_ROLE):
            return
        node.setData(0, UNBUILT_ROLE, None)
        node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
        origin = node.data(0, ORIGIN_ROLE) or ""
        section = self._mod_sections.get(origin)
        if section is None:
            return
        was = self._loading
        self._loading = True
        blocked = self.tree.blockSignals(True)
        # Not repainted row by row: see refresh().
        painting = self.tree.updatesEnabled()
        self.tree.setUpdatesEnabled(False)
        steady = getattr(self.view, "steady", None)
        try:
            with steady() if steady is not None else _nothing():
                self._add_rows(node, section, self._pieces(origin), theme.mono_font())
        finally:
            self.tree.setUpdatesEnabled(painting)
            self.tree.blockSignals(blocked)
            self._loading = was

    def build_every_row(self) -> None:
        """Make the rows of every mod not opened yet: for whatever has to see
        all of them -- the filter, a selection being acted on."""
        # A filter that has not looked in all of them does so first: a row
        # it would hide is not one to act on.
        self.finish_filter()
        painting = self.tree.updatesEnabled()
        self.tree.setUpdatesEnabled(False)
        steady = getattr(self.view, "steady", None)
        try:
            with steady() if steady is not None else _nothing():
                for item in list(self._mod_items.values()):
                    self._build_rows(item)
        finally:
            self.tree.setUpdatesEnabled(painting)

    def _row_above(self, origin: str, drawn) -> Optional[str]:
        """The mod whose row ``origin``'s goes under, or None for the top of
        the list: the loaded mod it came bundled inside; and for your patch
        to a bundled mod, wherever that mod's row is, so the patch stays
        straight below it."""
        layer = self.loadout.layer(origin)
        if layer is None:
            return None
        if layer.patches and layer.patches in drawn:
            return self._row_above(layer.patches, drawn)
        host = self._host_of(origin)
        return host.origin if host is not None and host.origin in drawn else None

    def _has_switch(self, layer) -> bool:
        """Whether a mod's row has a box: whether it answers to a switch of
        its own, and not to its host's (Loadout.switch_of)."""
        holder = self._holders.get(layer.origin)
        return holder is None or holder is layer

    def _bundled_row(self, origin: str) -> bool:
        """Whether ``origin``'s row is a bundled mod's, under its host."""
        layer = self.loadout.layer(origin)
        return layer is not None and not layer.patches and self._host_of(origin) is not None

    def _add_bundled(self, parent, host_origin: str, piece_origin: str, via: str) -> None:
        """A row under a host for a copy of a mod bundled in it that the game
        does not load from here, because another mod ships a copy it prefers:
        the row goes to that copy, whose changes are the ones listed.
        Double-clicking goes there."""
        layer = self.loadout.layer(via)
        label = layer.label if layer is not None else piece_origin.rsplit("/", 1)[-1]
        node = QTreeWidgetItem(parent)
        node.setData(0, KIND_ROLE, "bundled")
        node.setData(0, ORIGIN_ROLE, host_origin)
        node.setData(0, GOTO_ROLE, via)
        node.setData(0, SHORT_ROLE, "")
        node.setText(0, "↪ " + label)
        carrier = self._host_of(via)
        node.setText(1, tr("loads from another mod"))
        node.setForeground(0, QColor(theme.TEXT_DIM))
        node.setToolTip(
            0,
            tr("%(mod)s is bundled here, but the game loads one copy of "
               "a mod however many mods ship it. The copy it uses is in "
               "%(host)s, and its changes are listed there.\n"
               "\n"
               "Double-click or press Enter to go there.",
               mod=label, host=carrier.label if carrier else via),
        )
        node.setForeground(1, QColor(theme.TEXT_DIM))
        self._remember_text(node, label)

    def go_to_mod(self, origin: str) -> bool:
        """Select a mod's own row and bring it into view; whether there was one."""
        item = self._mod_items.get(origin)
        if item is None:
            return False
        above = item
        while above is not None:
            if above.isHidden():
                return False
            above = above.parent()
        above = item.parent()
        while above is not None:
            above.setExpanded(True)
            above = above.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
        return True

    def show_rows(self, keys=(), uids=(), unread=(), most: int = 200) -> int:
        """Select the rows of these changes, ops and ops carried unread --
        the last as ``(mod, place)`` -- and bring the first into view; how
        many rows that was.  What was selected before is not any more.

        A row under one that is selected is not selected with it: an op's
        row stands for what the op comes to.  Nothing the filter hides is
        selected, and no more than ``most`` rows, which is already more than
        anybody goes through one at a time.
        """
        keys = {tuple(key) for key in keys}
        uids, unread = set(uids), set(unread)
        origins = {key[0] for key in keys} | {origin for origin, _at in unread}
        for uid in uids:
            op = self.loadout.op(uid)
            if op is not None:
                origins.add(op.origin)
        # What the filter hides is not selected, so it has to have looked.
        self.finish_filter(origins)
        found: list = []

        def walk(item, origin: str) -> None:
            if item.isHidden() or len(found) >= most:
                return
            uid, at = item.data(0, OP_ROLE), item.data(0, UNREAD_ROLE)
            if (
                (uid and uid in uids)
                or (at is not None and (origin, at) in unread)
                or tuple(item.data(0, KEY_ROLE) or ()) in keys
            ):
                found.append(item)
                return
            self._build_rows(item)
            for child in self._own_rows(item):
                walk(child, origin)

        for origin, item in list(self._mod_items.items()):
            if origin in origins:
                walk(item, origin)
        self.tree.clearSelection()
        if not found:
            return 0
        for item in found:
            above = item.parent()
            while above is not None:
                above.setExpanded(True)
                above = above.parent()
            item.setSelected(True)
        # The row the keyboard is on moves there too, and selects no more.
        self.tree.setCurrentItem(found[0], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.tree.scrollToItem(found[0], QAbstractItemView.ScrollHint.PositionAtCenter)
        return len(found)

    def _add_unread(self, parent, entry, mono) -> None:
        """One ModOp the Studio could not read, greyed, below the mod's changes.

        A count in the mod's tooltip said these existed; a row says which. It
        has no box because there is nothing to switch: the Studio did not read
        it, so it cannot leave it out either -- an export carries it exactly as
        written, and the row is there so the list stops claiming to be all the
        mod does.
        """
        item = QTreeWidgetItem(parent)
        item.setData(0, KIND_ROLE, "unread")
        item.setData(0, ORIGIN_ROLE, entry.origin)
        item.setData(0, UNREAD_ROLE, entry.order)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        item.setText(0, "⌗ " + (tr_stored(entry.label) or tr("a ModOp")))
        item.setFont(0, mono)
        item.setText(1, tr("not read, kept as written"))
        item.setForeground(0, QColor(theme.TEXT_DIM))
        item.setForeground(1, QColor(theme.TEXT_DIM))
        # The reason is a sentence of its own, often past what a tooltip may
        # hold, so it is in the dialog with the note, the place and the XML.
        hint = tr("Double-click or press Enter to see why it was not read.")
        item.setToolTip(0, hint)
        item.setToolTip(1, hint)
        # The op's own XML too: an unread row is identified by what it says,
        # and its label is a shortened form of exactly that.
        self._remember_text(item, entry.source or "")

    def _mod_row_text(self, origin: str, label: str) -> str:
        """A mod row's label: whose it is, and whether it is the one being edited.

        Your own mod is marked yours, and a patch is named for every mod it
        needs, by the name the Generate tab's picker gives it
        (patching.patched_names).  The picker says more of your own mod --
        how far its changes reach, which is what choosing where they go is
        about -- and the row says only whose it is.  The editing mark is put
        only on somebody else's, because editing into your own is the default,
        is already said in the editor, and would otherwise be permanent noise
        on the one row that never moves.
        """
        from ..core import patching

        # Each mark is a note put after the name, not part of a sentence, so
        # each wraps what is there so far and can go where its language puts it.
        if is_own_mod(origin):
            label = tr("%s  (yours)", label)
        layer = self.loadout.layer(origin)
        lead = ""
        if layer is not None and layer.patches:
            # A patch is not a mod beside the one it patches, it is your layer
            # on top of it -- and it already sits immediately below it, because
            # that is what its name is for. So it reads as a continuation of
            # the row above rather than as another entry in the list.
            lead = "    ↳ "
            label = tr("your patch for %s", patching.patched_names(self.loadout, layer))
        # A mod that is switched off says so by being struck through (see
        # _paint_mod), and is not also said to be replaced.
        if (layer is None or layer.active) and origin in self._replaced:
            instead = self.loadout.layer(self._replaced[origin])
            label = tr("%(mod)s  (replaced by %(other)s)", mod=label,
                       other=instead.label if instead is not None else self._replaced[origin])
        # Said on the row itself, not only in its tooltip: what a script does
        # is the one part of a mod this list cannot show.
        if origin in self._runs:
            label = tr("%s  (runs scripts)", label)
        # Said on the row too: the game can reach only one of two assets under
        # one GUID, and nothing in the list below shows which.
        taken = repeats.mark(self._repeats, origin)
        if taken:
            # Translators: a mod's name, then a note on it in brackets, such
            # as "creates 2 GUIDs another mod has".
            label = tr("%(mod)s  (%(note)s)", mod=label, note=taken)
        also = self.loadout.patches_needing(origin) if layer is not None else []
        if also:
            # Translators: a mod's name, then the patches of yours that need it
            # besides the one made for it.
            label = tr("%(mod)s  (also patched by %(patches)s)", mod=label,
                       patches=join_list([patch.label for patch in also]))
        reason = patching.why_inert(self.loadout, layer) if layer else ""
        if reason:
            label = tr("%(mod)s  (%(reason)s)", mod=label, reason=reason)
        label = self._copy_note(layer, label)
        editing = self.loadout.editing_origin()
        # Only worth saying where there is another mod it could have been.
        if editing and origin == editing and self.loadout.is_multi_mod:
            label = tr("%s  ← editing this mod", label)
        return lead + label

    def _copy_note(self, layer, label: str) -> str:
        """``label`` with a note where the copy the game loads is not the one
        these rows were read from -- see Loadout.loading_copies -- and on
        your patch to such a mod, where that copy is another version."""
        if layer is None:
            return label
        if layer.patches:
            patched = self.loadout.layer(layer.patches)
            loading = self._copies.get(layer.patches, layer.patches)
            if (
                patched is None or loading in ("", layer.patches)
                or dependencies.same_version(self.loadout, patched, loading)
            ):
                return label
            now = self.loadout.copy_version(loading)
            if now and patched.version:
                return tr("%(mod)s  (written against version %(have)s, and the game "
                          "loads %(now)s)", mod=label, have=patched.version, now=now)
            return tr("%s  (written against another copy than the one the game loads)",
                      label)
        loading = self._copies.get(layer.origin, layer.origin)
        if loading in ("", layer.origin):
            return label
        carrier = self.loadout.carrier_of(loading)
        where = carrier.label if carrier is not None else loading
        if dependencies.same_version(self.loadout, layer, loading):
            return tr("%(mod)s  (the game loads the copy in %(host)s)", mod=label, host=where)
        now = self.loadout.copy_version(loading)
        if now:
            return tr("%(mod)s  (the game loads the copy in %(host)s, version %(version)s)",
                      mod=label, host=where, version=now)
        return tr("%(mod)s  (the game loads the copy in %(host)s, of a version not recorded)",
                  mod=label, host=where)

    def refresh_marks(self) -> None:
        """Re-label the mod rows without redrawing the list underneath them.

        Which mod an edit goes to is the only thing that has moved, and a
        redraw of four thousand rows to restate it is most of a second.
        """
        if self._sections is None:
            return
        labels = {section.origin: section.label for section in self._sections}
        self._loading = True
        try:
            for origin, item in self._mod_items.items():
                item.setText(
                    0, self._mod_row_text(origin, labels.get(origin, origin))
                )
                self._remember_text(item, origin)
                self._paint_mod(item, self.loadout.layer(origin))
        finally:
            self._loading = False

    def _stale_tip(self, layer) -> list:
        """Why a mod's reading is in doubt, and what is being done about it."""
        lines = [tr("What its ModOps come to may be out of date:")]
        lines.extend("  " + reason for reason in stale.why(self.loadout, layer))
        lines.append(
            tr("It is being read again.") if self.reading_again
            else tr("Reading the mods folder again puts it right.")
        )
        return lines


    def _redraw_marks(self) -> None:
        """The theme has changed, and the ops' drawings were coloured from the
        one before."""
        op_marks.forget()
        self.refresh()

    def _add_script_actions(self, menu, layer) -> None:
        """Open each script this mod runs, in whatever the system opens Lua
        with -- an editor made for it, not this one.  Only a Lua file inside
        the mod that has it is listed (:func:`core.scripts.files`)."""
        found = scripts.files(layer, self._named.get(layer.origin), self.loadout)
        if not found:
            none = menu.addAction(tr("Open a script"))
            none.setEnabled(False)
            none.setToolTip(tr(
                "No script %s runs is in a loaded mod. They may be the game's own.", layer.label
            ))
            return
        into = menu.addMenu(tr("Open a script"))
        for path in found:
            # A mod read from a zip has its scripts only in the Studio's
            # mirror of it. That copy is what there is to read, and it opens,
            # but it is not the mod's: the zip is, and the mirror is made
            # again from it. Said, where the path alone looked like the mod's.
            source = zipped.zip_for(path) if zipped.is_mirrored(path) else None
            into.addAction(
                path.name,
                # files() kept to the mod's folder; this keeps to Lua again.
                lambda path=path: open_script(path, path.parent),
            ).setToolTip(
                str(path) if source is None else tr(
                    "A copy the Studio keeps of the script in %(zip)s. A change "
                    "made to the copy does not reach the mod.\n%(path)s",
                    zip=source.name, path=path,
                )
            )
        into.setToolTipsVisible(True)

    def _add_folder_action(self, menu, layer) -> None:
        """Open the mod's folder in Explorer. A mod read from a zip has only
        the Studio's mirror of it for a folder, which is nobody's to look in:
        that one opens the mods folder the zip is in. A library mod switched
        off has no link where its layer says, and opens where the library
        keeps it."""
        folder = Path(layer.folder) if layer.folder else None
        if folder is not None and self.reading_folder is not None:
            folder = self.reading_folder(folder)
        if folder is not None and zipped.is_mirrored(folder):
            source = zipped.zip_for(folder)
            folder = source.parent if source is not None else None
        action = menu.addAction(tr("Open this mod's folder"))
        if folder is not None and folder.is_dir():
            action.setToolTip(str(folder))
            action.triggered.connect(lambda: open_on_disk(folder))
        else:
            action.setEnabled(False)
            action.setToolTip(tr("%s has no folder on this PC.", layer.label))

    def set_elsewhere(self, owners) -> None:
        """Which mods on this PC create each GUID -- :func:`core.discovery.guid_owners`
        -- so a mod's GUIDs are checked against the ones not loaded here too."""
        self._elsewhere = owners or {}
        self._repaint_mods()
        self._refresh_summary()

    @property
    def elsewhere(self) -> dict:
        """What :meth:`set_elsewhere` was last given: for the conflicts
        dialog, so it lists the GUIDs the conflicts button counts."""
        return self._elsewhere

    def elsewhere_of(self, origin: str) -> list:
        """The GUIDs ``origin`` creates that a mod on this PC outside the
        loadout creates too."""
        return [repeat for repeat in repeats.of(self._repeats, origin) if repeat.elsewhere]

    def _repaint_mods(self) -> None:
        """Every mod row's colour and tooltip again, from the switches as
        they are now."""
        self._needs = dependencies.for_loadout(self.loadout, self.found)
        self._replaced = self.loadout.replaced()
        self._on = {layer.origin for layer in dependencies.switched_on(self.loadout)}
        self._copies = self.loadout.loading_copies()
        self._holders = self.loadout.switch_holders()
        # A switch moves which mods patch the same InfoTip, too, and which
        # scripts share a name.
        self._infotips = infotips.overview(self.loadout)
        self._shared = scripts.clashes(self.loadout)
        self._repeats = repeats.find(self.loadout, getattr(self.view, "index", None), self._elsewhere)
        was = self._loading
        self._loading = True
        try:
            for origin, item in self._mod_items.items():
                layer = self.loadout.layer(origin)
                self._paint_mod(item, layer)
                section = self._mod_sections.get(origin)
                if section is not None and layer is not None:
                    item.setText(0, self._mod_row_text(origin, section.label))
                    self._remember_text(item, origin)
                    item.setToolTip(0, self._mod_tip(section))
        finally:
            self._loading = was

    def _paint_mod(self, item, layer) -> None:
        """A mod the game is not loading is greyed the way its changes would be.

        It keeps its row and its changes -- installed and switched off is not the
        same thing as gone -- but nothing under it counts towards what the game
        does, and the list should not need reading twice to see that.
        """
        from ..core import patching

        on = layer is None or (
            self._loads(layer)
            and not patching.is_inert(self.loadout, layer)
            and layer.origin not in self._replaced
        )
        colour = QColor(theme.ACCENT_TEXT if on else theme.TEXT_DIM)
        item.setForeground(0, colour)
        item.setForeground(1, QColor(theme.TEXT_DIM))
        font = item.font(0)
        font.setStrikeOut(not on)
        target = self._is_target(item.data(0, ORIGIN_ROLE) or "")
        font.setBold(target)
        item.setFont(0, font)
        # The mod changes go to is told from the rest at a glance, not only
        # by the words at the end of its name: the pencil its tab wears in
        # the XML view, and a band of the accent across the row.
        item.setIcon(0, theme.glyph("tab_edit") if target else QIcon())
        band = theme.wash() if target else None
        for column in (0, 1):
            item.setBackground(column, QBrush(QColor(band)) if band else QBrush())

    def _is_target(self, origin: str) -> bool:
        """Whether ``origin`` is the mod changes go to, where there is another
        it could have been.  Yours too: its name says "yours", not that an
        edit lands there."""
        return origin == self.loadout.editing_origin() and self.loadout.is_multi_mod

    def _loads(self, layer) -> bool:
        """Whether a mod's switch is on, and for a bundled one its host's as
        well: :func:`dependencies.switched_on`'s answer, which is what the
        overviews of what is replaced, needed and scripted go by.  Worked out
        with them, once for the list."""
        return layer.origin in self._on

    def _claiming_more(self, section) -> int:
        """How many ops of a mod of yours replace more than they change."""
        if not self._yours(section.origin):
            return 0
        return sum(
            1 for group in section.ops for held in group.every()
            if keptops.claims_more(held.view, self.loadout.game)
        )

    def _mod_tip(self, section) -> str:
        from ..core import patching

        layer = section.layer
        lines = [layer.label]
        if layer.stale:
            lines.extend([*self._stale_tip(layer), ""])
        if layer.creator:
            # Translators: who made a mod, under its name.
            lines.append(tr("by %s", layer.creator))
        if layer.version:
            lines.append(tr("version %s", layer.version))
        setup = dependencies.setup(layer.declared)
        if setup:
            lines.append(" · ".join(setup))
        host = self._host_of(layer.origin)
        loading = False
        if not is_own_mod(layer.origin):
            lines.append("\n" + tr("folder: %s", layer.origin))
            source = zipped.zip_for(layer.folder) if layer.folder and zipped.is_mirrored(layer.folder) else None
            if source is not None:
                lines.append(tr(
                    "read from the zip %s, as the game reads it, and never "
                    "written back out.", source
                ))
            instead = self._replaced.get(layer.origin)
            loads = self._loads(layer)
            holder = self._holders.get(layer.origin)
            loading_from = self._copies.get(layer.origin, layer.origin)
            if loads and instead:
                replacing = self.loadout.layer(instead)
                lines.append(tr(
                    "on, but not loaded. %s replaces it, so the rows below "
                    "it have no effect while that mod is on.",
                    replacing.label if replacing is not None else instead,
                ))
            elif loads:
                loading = True
                lines.append(tr("loads at position %s", number(layer.order)))
                if loading_from not in ("", layer.origin):
                    lines.extend(dependencies.about_copy(self.loadout, layer, loading_from))
            elif holder is not None and holder is not layer:
                # It switches with its host, and the host is off.  The host
                # is named here, so the line further down that names it for
                # a bundled mod is left out.
                host = None
                lines.append(tr(
                    "not loaded. %s, which it came bundled inside, is "
                    "switched off, so the rows below it have no effect "
                    "until that mod is ticked.",
                    holder.label,
                ))
            else:
                lines.append(tr(
                    "switched off in your mods folder, so the rows below it "
                    "have no effect. Ticking it puts it back at position %s.",
                    number(layer.order)
                ))
            said = (self._needs.about.get(dependencies.layer_key(layer)) or []) if self._needs else []
            if said:
                lines.append("\n" + "\n".join(said))
            ran = scripts.describe(layer, self._named.get(layer.origin))
            if ran:
                ran += scripts.describe_clashes(self._shared, layer.origin, self._mod_name)
                lines.append("\n" + "\n".join(ran))
            patch = self._infotips.by_mod.get(layer.origin) if self._infotips else None
            if patch is not None:
                tips = infotips.describe(patch, self._infotips.others(layer.origin, layer.label))
                if tips:
                    lines.append("\n" + "\n".join(tips))
            if self._log is not None:
                warned = loaderlog.describe_warnings(self._log.warnings_for_layer(layer), self._log.started)
                if warned:
                    lines.append("\n" + "\n".join(warned))
        # Your own mod as well: a GUID taken twice is easiest put right there.
        taken = repeats.describe(self._repeats, layer.origin, self._mod_name)
        if taken:
            lines.append("\n" + "\n".join(taken))
        if not is_own_mod(layer.origin):
            also =[name for name in self.loadout.listed_in(layer.origin) if name != layer.from_collection]
            if layer.from_collection:
                lines.append("\n" + tr("came with the collection %s", layer.from_collection))
            if also and layer.from_collection:
                lines.append(tr("also in %s", join_list(also)))
            elif also:
                lines.append("\n" + trn(
                    "yours from before, and %(lists)s also lists it",
                    "yours from before, and %(lists)s also list it",
                    len(also), lists=join_list(also),
                ))
        if layer.patches and layer.needs:
            lines.append("\n" + tr(
                "your patch for %s. It loads after all of them and needs every one: "
                "with any of them off, it is left out.",
                patching.patched_names(self.loadout, layer),
            ))
        elif layer.patches:
            patched = self.loadout.layer(layer.patches)
            lines.append("\n" + tr(
                "your patch for %s. It loads straight after that mod, so an "
                "update to that mod leaves these changes in place.",
                patched.label if patched is not None else layer.patches,
            ))
        elif self.loadout.patch_for(layer.origin) is not None:
            lines.append("\n" + tr(
                "patched by %s, which loads straight after it.",
                self.loadout.patch_for(layer.origin).label,
            ))
        elif is_own_mod(layer.origin):
            # Only your own mod: every loaded mod used to fall through to this.
            lines.append("\n" + tr("the mod you are writing, applied on top of the rest"))
        # Read off the section rather than by asking keptops.of() again: this
        # is called once per mod while the tree is built, and a mods folder is
        # sixty of them.  The ops themselves are rows now, so all that is
        # wanted here is how many, and how many survive as searches.
        # Only the ops that search: every op is kept as written now, and one
        # that names its asset is not a thing targeted by search.
        searching = [op for op in section.ops if op.view.op.searches]
        if searching:
            kept = sum(1 for op in searching if op.view.whole)
            if kept == len(searching):
                # As every op is unless it is off: nothing to tell apart.
                lines.append("\n" + trn(
                    "targets %(n)s thing by search, not by GUID.",
                    "targets %(n)s things by search, not by GUID.",
                    len(searching),
                ))
            else:
                # Two counts, so two plurals: the second is a clause of its own.
                which = trn(
                    "of which %(n)s is written back out as the search itself",
                    "of which %(n)s are written back out as the search itself",
                    kept,
                )
                # Translators: %(which)s is "of which N are written back out...".
                lines.append("\n" + trn(
                    "targets %(n)s thing by search, not by GUID, %(which)s.",
                    "targets %(n)s things by search, not by GUID, %(which)s.",
                    len(searching), which=which,
                ))
        changed = self._options_changed(layer)
        if changed:
            # A configurable mod does different things at different settings, so
            # which ones it was read at is part of what these changes are. Only
            # the ones away from the author's defaults, by the author's own
            # labels, and not all of them: an iModYourAnno mod can offer a
            # hundred, and a tooltip taller than the screen says nothing.
            lines.append("\n" + trn(
                "read with %(n)s of its options changed from the author's defaults:",
                "read with %(n)s of its options changed from the author's defaults:",
                len(changed),
            ))
            lines.extend("  %s: %s" % pair for pair in changed[:OPTIONS_SHOWN])
            if len(changed) > OPTIONS_SHOWN:
                lines.append("  " + trn(
                    "and %(n)s more (%(entry)s on this row shows them all)",
                    "and %(n)s more (%(entry)s on this row shows them all)",
                    len(changed) - OPTIONS_SHOWN, entry=tr("Mod options…"),
                ))
        inner = self._pieces(layer.origin)
        if inner:
            # A mod that is a box of others -- Item Overhaul is eleven -- has
            # little or nothing of its own; the game loads each piece as a mod
            # in its own right, at its own place in load order.
            lines.append("\n" + trn(
                "holds %(n)s mod of its own, which the game loads separately "
                "and switches with this one: the row under this one.",
                "holds %(n)s mods of its own, which the game loads separately "
                "and switches with this one: the rows under this one.",
                len(inner),
            ))
        if host is not None and loading:
            lines.append("\n" + tr(
                "bundled inside %s, which it came with. The game loads it as "
                "a mod of its own, and switches it with that mod.", host.label
            ))
        elif host is not None:
            # Off, or replaced: said above, and it is not loading as anything.
            lines.append("\n" + tr("bundled inside %s, which it came with.", host.label))
        # Counted from the entries the rows below are drawn from, and not
        # from the number the layer keeps, which the mod being written never
        # had and an edit can leave behind.
        if section.unread:
            lines.append("\n" + trn(
                "%(n)s ModOp here could not be read, so what this mod does "
                "is not fully shown. It is listed greyed below, and an "
                "export carries it as written.",
                "%(n)s ModOps here could not be read, so what this mod does "
                "is not fully shown. They are listed greyed below, and an "
                "export carries them as written.",
                len(section.unread),
            ))
        return "\n".join(lines)

    @staticmethod
    def _options_changed(layer) -> list:
        """``(label, value)`` for each option a mod was read at that is not its
        author's default, in the order the mod declares them."""
        if not layer.options:
            return []
        from ..core import settings as settings_mod

        offered = settings_mod.from_dict(layer.offered or {})
        found = []
        for option in offered.settable:
            if option.name not in layer.options:
                continue
            value = str(layer.options[option.name])
            if option.kind == settings_mod.TOGGLE:
                if settings_mod.is_on(value) == settings_mod.is_on(option.default):
                    continue
                shown = (tr("on", context="option switched") if settings_mod.is_on(value)
                         else tr("off", context="option switched"))
            else:
                if value == option.default:
                    continue
                shown = option.label_for(value)
            found.append((option.label or option.name, shown))
        return found

    def _bundled_in(self, origin: str) -> list:
        """The loaded mods that came bundled inside the one at ``origin``, in
        load order: a bundled mod's origin is its host's, then a slash."""
        if is_own_mod(origin):
            return []
        prefix = origin + "/"
        return sorted(
            (layer for layer in self.loadout.layers if layer.origin.startswith(prefix)),
            key=lambda layer: layer.order,
        )

    def _pieces(self, origin: str) -> list:
        """``(piece origin, via)`` for each mod bundled straight inside the one
        at ``origin``: the loaded ones in load order, ``via`` empty; then the
        copies the game does not load from here, ``via`` the copy it does."""
        if is_own_mod(origin):
            return []
        loaded = [
            (piece.origin, "") for piece in self._bundled_in(origin)
            if (host := self._host_of(piece.origin)) is not None and host.origin == origin
        ]
        layer = self.loadout.layer(origin)
        copies = sorted((layer.copies or {}).items()) if layer is not None else []
        return loaded + [
            (passed, kept) for passed, kept in copies if self.loadout.layer(kept) is not None
        ]

    def _host_of(self, origin: str):
        """The loaded mod that ``origin`` came bundled inside, if any."""
        return self.loadout.host_layer(origin)

    def _say_filtered(self) -> None:
        """The line under the list while a filter is on.  The one thing the
        list cannot say for itself is how much of itself is missing, so that
        takes the line.  The counts it replaces are one keystroke away."""
        if self._names_shown is not None:
            self.summary.setText(
                trn("Filtered: %(shown)s of %(n)s mod shown",
                    "Filtered: %(shown)s of %(n)s mods shown",
                    len(self._mod_items), shown=number(self._names_shown))
                if self._names_shown
                else tr("No mod's name has “%s”.", self.filter_box.text().strip())
            )
            return
        if self.scope_box.currentData() == SCOPE_MOD:
            origin = self._scope_origin or ""
            mine = sum(1 for change in self.loadout if change.origin == origin)
            self.summary.setText(
                trn("Filtered: %(shown)s of the %(n)s change of %(mod)s shown",
                    "Filtered: %(shown)s of the %(n)s changes of %(mod)s shown",
                    mine, shown=number(self._shown or 0), mod=self._mod_name(origin))
                if self._shown or not self._needle()
                else tr("No rows of %(mod)s match “%(typed)s”.",
                        mod=self._mod_name(origin), typed=self.filter_box.text().strip())
            )
            return
        if self._unsearched:
            # Not "no rows match" while there are mods still to look in.
            self.summary.setText(trn(
                "Filtered: %(shown)s shown so far, %(n)s mod still to search…",
                "Filtered: %(shown)s shown so far, %(n)s mods still to search…",
                len(self._unsearched), shown=number(self._shown or 0),
            ))
            return
        self.summary.setText(
            trn("Filtered: %(shown)s of %(n)s change shown",
                "Filtered: %(shown)s of %(n)s changes shown",
                len(self.loadout), shown=number(self._shown))
            if self._shown
            else tr("No rows match “%s”.", self.filter_box.text().strip())
        )

    def _refresh_summary(self) -> None:
        total = len(self.loadout)
        active = self.loadout.active_count
        assets = len({grouping.asset_guid(c) for c in self.loadout})
        contested = conflicts.summarise(self.loadout, self._mod_name, self._shared, self._repeats)
        self.conflicts_button.setVisible(bool(contested))
        self.conflicts_button.setText(contested or "")
        missed = unread_mod.summarise(self.loadout)
        self.unread_button.setVisible(bool(missed))
        self.unread_button.setText(missed or "")
        if self._shown is not None:
            self._say_filtered()
        elif not total:
            # Short on purpose: it sits in a panel that is often at its narrowest
            # width, where a longer phrasing was simply clipped.
            self.summary.setText(tr("No changes yet."))
        else:
            where = trn("%(n)s asset", "%(n)s assets", assets)
            mods = len(self.loadout.origins())
            if mods > 1:
                where = trn("%(n)s mod, %(assets)s", "%(n)s mods, %(assets)s",
                            mods, assets=where)
            # Changes, the word the rows above use for the same things: one
            # for each change row. It said ModOps, from when every change was
            # written as one; an op kept as written can come to several
            # changes or to none, so "22 ModOps" under "22 changes" was the
            # one count under two names.
            if active == total:
                # Translators: %(where)s is "N assets" or "N mods, N assets".
                self.summary.setText(trn(
                    "%(n)s change across %(where)s", "%(n)s changes across %(where)s",
                    total, where=where,
                ))
            else:
                # Translators: %(where)s is "N assets" or "N mods, N assets".
                self.summary.setText(trn(
                    "%(active)s of %(n)s change on, across %(where)s",
                    "%(active)s of %(n)s changes on, across %(where)s",
                    total, active=number(active), where=where,
                ))
        self._sync_revert()
        self.fold_button.setEnabled(total > 0)

    # -- folding ---------------------------------------------------------
    @staticmethod
    def _fold_key(item):
        """What a row's fold is remembered by, or None for one whose is not."""
        kind = item.data(0, KIND_ROLE)
        if kind in ("mod", "idle"):
            return (kind, item.data(0, ORIGIN_ROLE))
        if kind == "op":
            return (kind, item.data(0, OP_ROLE))
        if kind == "part":
            return (kind, item.data(0, PART_ROLE))
        if kind == "group":
            return (kind, item.data(0, OPEN_ROLE))
        return None

    def _remember_filtered(self, item, opened: bool) -> None:
        if self._folds_needle:
            key = self._fold_key(item)
            if key is not None:
                self._filter_folds[key] = opened

    def _remember_open(self, item) -> None:
        if self._loading:
            return
        self._remember_filtered(item, True)
        kind = item.data(0, KIND_ROLE)
        if kind == "mod":
            self._opened.add(item.data(0, ORIGIN_ROLE))
        elif kind == "op":
            self._collapsed.discard(item.data(0, OP_ROLE))
        elif kind == "part":
            self._opened_parts.add(item.data(0, PART_ROLE))
        elif kind == "idle":
            self._opened_parts.add(("idle", item.data(0, ORIGIN_ROLE)))
        elif kind == "group":
            self._collapsed.discard(item.data(0, OPEN_ROLE))
        else:
            return
        if not self._toggling:
            self._sync_fold_button()

    def _remember_closed(self, item) -> None:
        if self._loading:
            return
        self._remember_filtered(item, False)
        kind = item.data(0, KIND_ROLE)
        if kind == "mod":
            self._opened.discard(item.data(0, ORIGIN_ROLE))
        elif kind == "op":
            self._collapsed.add(item.data(0, OP_ROLE))
        elif kind == "part":
            self._opened_parts.discard(item.data(0, PART_ROLE))
        elif kind == "idle":
            self._opened_parts.discard(("idle", item.data(0, ORIGIN_ROLE)))
        elif kind == "group":
            self._collapsed.add(item.data(0, OPEN_ROLE))
        else:
            return
        if not self._toggling:
            self._sync_fold_button()

    @staticmethod
    def _with_content(sections) -> list:
        """The mods with anything under their row."""
        return [s for s in sections if s.groups or s.ops or s.unread]

    def _several(self) -> bool:
        """Whether the list is a stack of mods, which starts shut and folds
        at its mods, and not one mod alone, which starts open and folds at
        its groups.  Counted by the mods with something in them, here for the
        first drawing and the fold button both: counted by rows for the
        button, your own empty mod made a stack of one mod read in, and
        Collapse all shut the only row with anything under it."""
        return len(self._with_content(self._sections or ())) > 1

    def _folded(self) -> bool:
        """Whether the list is currently shut, at the level it folds at.

        Mods, when there are several; one mod alone folds at its groups, since
        shutting the only row there is would hide the whole list.  Shut by
        hand, that row has hidden it, and the list is shut whatever its
        groups are.

        Under a filter, as the rows stand: the filter opens what it finds,
        and the button said Expand all over a list it had opened (H2-20).
        """
        if self._folds_needle:
            return self._folded_as_shown()
        if self._several():
            return not self._opened
        if any(
            section.origin not in self._opened
            for section in self._with_content(self._sections or ())
        ):
            return True
        top = [group.lead for group in self._groups] + [
            op.uid for op in self._ops
        ]
        return bool(top) and all(key in self._collapsed for key in top)

    def _shown_tops(self) -> list:
        """The mod rows a filter leaves showing that have rows under them."""
        tree = self.tree
        return [
            top for top in (tree.topLevelItem(n) for n in range(tree.topLevelItemCount()))
            if not top.isHidden() and (top.childCount() or top.data(0, UNBUILT_ROLE))
        ]

    def _folded_as_shown(self) -> bool:
        """:meth:`_folded`, over the rows a filter leaves showing."""
        tops = self._shown_tops()
        if self._several():
            return not any(top.isExpanded() for top in tops)
        if any(not top.isExpanded() for top in tops):
            return True
        inner = [
            child for top in tops for child in (top.child(n) for n in range(top.childCount()))
            if not child.isHidden() and child.childCount()
        ]
        return bool(inner) and not any(child.isExpanded() for child in inner)

    def _toggle_shown(self) -> None:
        """Expand all or Collapse all under a filter: the rows it shows,
        each remembered as one opened or shut by hand -- and not the mods
        it hides, whose rows are made as they are opened."""
        expand = self._folded_as_shown()
        several = self._several()
        # Told once at the end, not for each row as it is remembered.
        self._toggling = True
        try:
            for top in self._shown_tops():
                if several:
                    top.setExpanded(expand)
                    continue
                top.setExpanded(True)
                for n in range(top.childCount()):
                    child = top.child(n)
                    if not child.isHidden() and child.childCount():
                        child.setExpanded(expand)
        finally:
            self._toggling = False
        self._sync_fold_button()

    def _sync_fold_button(self) -> None:
        """Offer the way out of whichever state the list is actually in."""
        self.fold_button.setText(tr("Expand all") if self._folded() else tr("Collapse all"))

    def _toggle_fold(self) -> None:
        if self._folds_needle:
            self._toggle_shown()
            return
        expand = self._folded()
        several = self._several()
        if several:
            self._opened = (
                {section.origin for section in self._sections} if expand else set()
            )
        else:
            self._collapsed = (
                set()
                if expand
                else {group.lead for group in self._groups}
                | {op.uid for op in self._ops}
            )
        alone = {section.origin for section in self._with_content(self._sections or ())}
        self._loading = True
        try:
            for origin, top in self._mod_items.items():
                if several:
                    top.setExpanded(expand)
                    continue
                if origin not in alone:
                    # An empty row of your own, or a box of other mods: not
                    # the one mod this folds inside, and left as it stands.
                    # Opening a shut box here made and showed its rows on
                    # Collapse all.
                    continue
                top.setExpanded(True)
                self._opened.add(origin)
                for child in self._own_rows(top):
                    child.setExpanded(expand)
        finally:
            self._loading = False
        self._sync_fold_button()

    # -- switching changes on and off ------------------------------------
    def _on_item_changed(self, item, column) -> None:
        """Write a ticked box back to the loadout.

        Two different facts wear the same box.  On a mod row it is whether the
        game loads that mod at all; everywhere below it, it is whether one change
        counts.  In between, Qt keeps the grouping rows in step with their
        children on its own -- a group's state is a summary, not a fact to store.
        """
        if self._loading or column != 0:
            return
        kind = item.data(0, KIND_ROLE)
        if kind == "mod":
            origin = item.data(0, ORIGIN_ROLE) or ""
            layer = self.loadout.layer(origin)
            if layer is None:
                return
            wanted = item.checkState(0) == Qt.CheckState.Checked
            if layer.active == wanted:
                return
            layer.active = wanted
            # Not only this row: a mod switched on can replace another, and
            # one switched off can let the mod it replaced load again.
            self._repaint_mods()
            self._refresh_summary()
            self._announce.start()
            return
        if kind == "op":
            op = self.loadout.op(item.data(0, OP_ROLE))
            if op is None:
                return
            wanted = item.checkState(0) == Qt.CheckState.Checked
            if op.active == wanted:
                return
            if not self._asked_inside(item, op.origin, op.active):
                return
            op.active = wanted
            # Its answers follow it, all of them: they are what the op comes
            # to, and an op that is off comes to nothing. Kept in step here
            # so the editor, the conflicts and the export all agree at once.
            # And for a group, every op and group inside it.
            switched = {op.uid}
            for held in self.loadout.ops:
                if op.uid in held.within:
                    held.active = wanted
                    switched.add(held.uid)
            for change in self.loadout.changes.values():
                if change.op_uid in switched:
                    change.active = wanted
            self._paint_op(item, op)
            if len(switched) > 1:
                self._retick_under(item)
            self._repaint_under(item)
            self._grouped_from = None
            self._refresh_summary()
            self._announce.start()
            return
        if kind != "change":
            return
        key = tuple(item.data(0, KEY_ROLE) or ())
        change = self.loadout.changes.get(key)
        if change is None:
            return
        alone = self._op_of(change)
        if alone is None and change.op_uid and self.loadout.op(change.op_uid) is not None:
            # A kept op's answer has no box, so this is a repaint, not a tick
            # -- and reading the box it does not have as "unticked" would switch
            # the change off underneath an op that is on.
            return
        wanted = item.checkState(0) == Qt.CheckState.Checked
        if change.active == wanted and (alone is None or alone.active == wanted):
            return
        if not self._asked_inside(item, change.origin, change.active):
            return
        change.active = wanted
        if alone is not None:
            # The op is what is written, so the op is what is switched.
            alone.active = wanted
        self._paint(item, change)
        self._refresh_summary()
        self._announce.start()

    def _asked_inside(self, item, origin: str, was: bool) -> bool:
        """Whether switching one of ``origin``'s ops or changes may go on:
        somebody else's mod is asked the three ways on first, unless where
        its edits go is known already.  Where it is asked, the box goes back
        as it was, and the question waits until the tick has returned: Qt is
        still inside the row's setData while this runs, and goes on reading
        the row after it, and the answer can make a patch, which draws the
        list again and deletes the row under it.  The switch is made after
        the answer by what the box stood for, and the list drawn again then."""
        if self._may_change_inside([origin], ask=False):
            return True
        uid, key = item.data(0, OP_ROLE), tuple(item.data(0, KEY_ROLE) or ())
        self._loading = True
        try:
            item.setCheckState(0, Qt.CheckState.Checked if was else Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        QTimer.singleShot(0, self, lambda: self._switch_inside(origin, uid, key, not was))
        return False

    def _switch_inside(self, origin: str, uid, key: tuple, wanted: bool) -> None:
        """Ask the three ways on of ``origin`` and, answered, switch the op
        ``uid`` or else the change ``key`` to ``wanted`` (:meth:`_asked_inside`)."""
        if not self._may_change_inside([origin]):
            return
        op = self.loadout.op(uid) if uid else None
        if op is not None:
            switched = {op.uid, *(held.uid for held in self.loadout.ops if op.uid in held.within)}
            for held in self.loadout.ops:
                if held.uid in switched:
                    held.active = wanted
            for change in self.loadout.changes.values():
                if change.op_uid in switched:
                    change.active = wanted
        else:
            change = self.loadout.changes.get(self._again(key))
            if change is None:
                return
            change.active = wanted
            alone = self._op_of(change)
            if alone is not None:
                alone.active = wanted
        self._grouped_from = None
        self.refresh()
        self._announce.start()

    def _may_change_inside(self, origins, ask: bool = True, strict: bool = False) -> bool:
        """Whether an edit inside the ops of each of ``origins`` may go on:
        a mod of yours, or somebody else's whose edits a mod of yours keeps
        (:func:`core.patching.changed_directly`), or -- with ``ask`` -- one
        the three ways on were asked of and answered (:attr:`may_edit_inside`).
        With no window to ask, it may, unless ``strict``."""
        from ..core import patching

        origins = sorted({origin for origin in origins if origin})
        allowed = [
            self.changed_directly(one) if self.changed_directly is not None
            else patching.changed_directly(self.loadout, one)
            for one in origins
        ]
        self._copied = {}
        if all(allowed):
            return True
        if self.may_edit_inside is None:
            return not strict
        if not ask:
            return False
        went = self.may_edit_inside(
            [one for one, yes in zip(origins, allowed, strict=True) if not yes]
        )
        if isinstance(went, dict):
            self._copied = {one: there for one, there in went.items() if there != one}
        return bool(went)

    def _again(self, key: tuple) -> tuple:
        """The change ``key`` named before :meth:`_may_change_inside` asked,
        as it is named now: on the copy made as the answer, which everything
        of its mod went to, keys and all -- the edit is made there."""
        there = self._copied.get(key[0]) if key else None
        return (there, *key[1:]) if there is not None else key

    def _retick_under(self, item) -> None:
        """Set every box below a group's row to what the loadout now says."""
        was = self._loading
        self._loading = True
        try:
            def walk(row):
                for index in range(row.childCount()):
                    child = row.child(index)
                    kind = child.data(0, KIND_ROLE)
                    held = None
                    if kind == "op":
                        held = self.loadout.op(child.data(0, OP_ROLE))
                        if held is not None:
                            self._paint_op(child, held)
                    elif kind == "change":
                        change = self.loadout.changes.get(tuple(child.data(0, KEY_ROLE) or ()))
                        held = self._op_of(change) if change is not None else None
                    if held is not None and child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        child.setCheckState(
                            0, Qt.CheckState.Checked if held.active else Qt.CheckState.Unchecked
                        )
                    walk(child)
            walk(item)
        finally:
            self._loading = was

    def _repaint_under(self, item) -> None:
        """Grey or ungrey every change row below ``item`` to match the loadout.

        Painting a row is a change to it as far as the tree is concerned, so
        this is done as a load rather than as ticks.
        """
        was = self._loading
        self._loading = True
        try:
            self._repaint_walk(item)
        finally:
            self._loading = was

    def _repaint_walk(self, item) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            if child.data(0, KIND_ROLE) == "change":
                change = self.loadout.changes.get(tuple(child.data(0, KEY_ROLE) or ()))
                if change is not None:
                    self._paint(child, change)
            self._repaint_walk(child)

    # -- selection -------------------------------------------------------
    def _selected_changes(self):
        """Every change row under the selection, plus what to open for it.

        A kept op's answers are left out: reverting one would not take the
        asset out of the op, only out of the list.

        So is anything the filter is hiding.  A selection outlives the
        keystroke that hides it, and a whole asset can be selected while only
        one of its changes is on screen: in both cases "revert selected" would
        otherwise throw away changes the user cannot see, which is the one
        thing a box that only hides rows must never lead to.
        """
        found = {}

        def walk(item):
            if item.isHidden():
                return
            if item.data(0, KIND_ROLE) == "change":
                key = tuple(item.data(0, KEY_ROLE) or ())
                change = self.loadout.changes.get(key)
                if self._answer_of_an_op(change):
                    return
                found[key] = (
                    item.data(0, OPEN_ROLE) or item.data(0, GUID_ROLE),
                    item.data(0, PATH_ROLE),
                )
                return
            self._build_rows(item)
            for child in self._own_rows(item):
                walk(child)

        for item in self.tree.selectedItems():
            walk(item)
        return found

    def _answer_of_an_op(self, change) -> bool:
        """Whether a change is one of the answers of an op with a row of its
        own, which reverting does not take out of the op."""
        return bool(
            change is not None
            and change.op_uid
            and self.loadout.op(change.op_uid) is not None
            and self._op_of(change) is None
        )

    def _selection_reverts(self) -> tuple:
        """``(reverts, held)``: whether :meth:`_selected_changes` would find
        anything under the selection, and whether an op holds any of what it
        would pass over.

        Asked each time the selection moves, so a folded mod's rows are not
        made for it: nothing of a mod not yet opened is hidden, and its
        changes answer for its rows.
        """
        reverts = held = False
        folded = set()

        def walk(item):
            nonlocal reverts, held
            if item.isHidden():
                return
            kind = item.data(0, KIND_ROLE)
            if kind == "change":
                key = tuple(item.data(0, KEY_ROLE) or ())
                if self._answer_of_an_op(self.loadout.changes.get(key)):
                    held = True
                else:
                    reverts = True
                return
            if kind == "op":
                held = True
            if item.data(0, UNBUILT_ROLE):
                folded.add(item.data(0, ORIGIN_ROLE) or "")
                return
            for child in self._own_rows(item):
                walk(child)

        for item in self.tree.selectedItems():
            walk(item)
        if folded:
            for change in self.loadout.changes.values():
                if change.origin not in folded:
                    continue
                if self._answer_of_an_op(change):
                    held = True
                else:
                    reverts = True
            held = held or any(op.origin in folded for op in self.loadout.ops)
        return reverts, held

    def _revert_offer(self) -> tuple:
        """``(enabled, tooltip)`` for Revert selected, on the button and in a
        row's menu: off where it would do nothing, and saying why where an op
        is the reason."""
        reverts, held = self._selection_reverts()
        if held and not reverts:
            return False, tr(
                "This does not revert a ModOp kept as written. Untick it to "
                "switch it off."
            )
        return reverts, tr(
            "Removes these changes for good. To keep one out of the mod, untick it."
        )

    def _sync_revert(self) -> None:
        enabled, tip = self._revert_offer()
        self.remove_button.setEnabled(enabled)
        self.remove_button.setToolTip(tip)

    # -- the right-click menu ---------------------------------------------
    def _origin_of(self, item) -> str:
        """Which loaded mod a row belongs to, walking up until something says.

        Only a mod row carries it directly; a change five levels down belongs
        to the same mod as the section it is under, and asking the row you
        actually clicked is what people do.
        """
        while item is not None:
            origin = item.data(0, ORIGIN_ROLE)
            if origin is not None:
                return origin
            item = item.parent()
        return ""

    def picked_for_option(self):
        """``(origin, [(kind, which), …])`` for the rows picked, where they
        can be put under one of their mod's options: all of one mod of yours,
        in Anno 117.  A change an op of the mod comes to is that op; an
        asset's row is its changes.  None where they cannot be."""
        if self.loadout.game != games.ANNO_117:
            return None
        picked: list = []
        origins = set()

        def take(item) -> None:
            kind = item.data(0, KIND_ROLE)
            origin = self._origin_of(item)
            if kind == "op":
                picked.append(("op", item.data(0, OP_ROLE)))
            elif kind == "change":
                change = self.loadout.changes.get(tuple(item.data(0, KEY_ROLE) or ()))
                if change is None:
                    return
                picked.append(("op", change.op_uid) if change.op_uid else ("change", change.key))
            elif kind == "unread":
                picked.append(("unread", item.data(0, UNREAD_ROLE)))
            elif kind in ("asset", "group"):
                for at in range(item.childCount()):
                    take(item.child(at))
                return
            else:
                return
            origins.add(origin)

        for item in self.tree.selectedItems():
            take(item)
        if not picked or len(origins) != 1:
            return None
        origin = origins.pop()
        layer = self.loadout.layer(origin)
        if not is_own_mod(origin) and (layer is None or not self.loadout.yours(layer)):
            return None
        return origin, list(dict.fromkeys(picked))

    def _add_option_actions(self, menu, origin: str, op) -> None:
        """An entry for each of its mod's options an op's own condition
        asks -- of a group, the group's -- to open it."""
        if op is None or self.loadout.game != games.ANNO_117:
            return
        from ..core import declaring, settings

        offered = settings.read(settings.block_of(self.loadout, origin))
        for name in declaring.asked_by(self.loadout, origin, op.source):
            option = offered.get(name)
            # Translators: %s is the label of one of a mod's options, or its
            # $name where nothing declares it.
            action = menu.addAction(tr("Option “%s”…", option.label if option is not None else "$" + name))
            action.setToolTip(tr("Whether it runs is this option of its mod."))
            action.triggered.connect(lambda _on=False, name=name: self.optionRequested.emit(origin, name))

    def _context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        # Right-clicking outside the selection selects what was clicked, so
        # that "Revert selected" means the row under the cursor rather than
        # whatever happened to be selected before. A click *inside* a multiple
        # selection leaves it alone, which is the whole point of having made it.
        if item is not None and not item.isSelected():
            self.tree.setCurrentItem(item)
        menu = self.menu_for(item)
        menu.exec(self.tree.viewport().mapToGlobal(point))
        menu.deleteLater()

    # -- writing an op narrow, on asking ----------------------------------
    def _narrowings(self, uids, origin=None) -> list:
        """What *Write as narrow ModOps* would make of the ops ``uids``, or of
        every op of ``origin`` that has anything to gain by it."""
        found = keptops.narrowings(self.loadout, origin if uids is None else None)
        if uids is None:
            return [one for one in found if not one.plain]
        wanted = set(uids)
        return [one for one in found if one.op.uid in wanted]

    def _add_narrow(self, menu, uids, origin=None) -> None:
        """The entry that writes an op -- or every op of a mod -- as the narrow
        ModOps it comes to.  Off where none of them can be, saying why; not
        there at all on somebody else's mod (:meth:`_all_yours`)."""
        if not self._all_yours(uids, origin):
            return
        found = self._narrowings(uids, origin)
        can = [one for one in found if one.possible]
        if uids is None:
            if not found:
                return
            # Translators: on a mod's row: every ModOp of the mod that can be.
            entry = menu.addAction(tr("Write its ModOps as narrow ModOps…"))
        else:
            entry = menu.addAction(tr("Write as narrow ModOps…"))
        entry.setEnabled(bool(can))
        if can:
            entry.setToolTip(tr("Replaces it by the narrow ModOps it comes to today."))
        elif len(found) == 1:
            entry.setToolTip(tr("Not as narrow ModOps: %s.", found[0].refused))
        else:
            entry.setToolTip(tr("None of its ModOps can be written as narrow ModOps."))
        entry.triggered.connect(lambda: self.write_narrow(uids, origin))

    def _all_yours(self, uids, origin=None) -> bool:
        """Whether the mod of each of these ops -- or ``origin`` -- is one of
        yours, whose own ops are rewritten in its file.  Somebody else's is
        not edited directly, so its ops stay as its author wrote them: the
        entry was offered on it, and asked the three ways on first."""
        if uids is not None:
            ops = [self.loadout.op(uid) for uid in uids]
            origins = {op.origin for op in ops if op is not None}
        else:
            origins = {origin or ""}
        for one in origins:
            layer = self.loadout.layer(one)
            if not is_own_mod(one) and (layer is None or not self.loadout.yours(layer)):
                return False
        return True

    def write_narrow(self, uids, origin=None) -> bool:
        """Ask, and then write the ops ``uids`` -- or every op of ``origin``
        that can be -- as narrow ModOps.  One step to undo.  Whether it was
        done."""
        if not self._all_yours(uids, origin):
            return False
        found = self._narrowings(uids, origin)
        can = [one for one in found if one.possible]
        if not can:
            return False
        changes = sum(len(one.changes) for one in can)
        said = [tr(
            "%(ops)s will be replaced by %(changes)s, each written as a narrow "
            "ModOp of its own. They are yours from then on.",
            ops=trn("%(n)s ModOp", "%(n)s ModOps", len(can)),
            changes=trn("%(n)s change", "%(n)s changes", changes),
        )]
        stay = len(found) - len(can)
        if stay:
            said.append(trn(
                "%(n)s ModOp stays as written, because it cannot be written narrow.",
                "%(n)s ModOps stay as written, because they cannot be written narrow.",
                stay,
            ))
        # What it gives up, each thing once and how many ops it is said of.
        costs: dict = {}
        for one in can:
            for warning in one.warnings:
                costs[warning] = costs.get(warning, 0) + 1
        if len(can) == 1:
            said.extend(costs)
        else:
            patterns = sum(
                1 for one in can
                if modops.described(one.op.source, self.loadout.game).target
            )
            worked = sum(
                1 for one in can
                if modops.described(one.op.source, self.loadout.game).payload
            )
            if patterns:
                said.append(trn(
                    "%(n)s of them finds what it changes as the game loads. It "
                    "becomes what it finds today, and nothing a mod adds later.",
                    "%(n)s of them find what they change as the game loads. They "
                    "become what they find today, and nothing a mod adds later.",
                    patterns,
                ))
            if worked:
                said.append(trn(
                    "%(n)s of them works its value out as the game loads. It "
                    "becomes the value it comes to today.",
                    "%(n)s of them work their values out as the game loads. They "
                    "become the values they come to today.",
                    worked,
                ))
        if not ask(
            self, tr("Write as narrow ModOps?"), "\n\n".join(said),
            # Translators: the button that does it, in a question's box.
            tr("&Write narrow"),
        ):
            return False
        keptops.write_narrow(self.loadout, [one.op.uid for one in can])
        # The same as changes taken out: the mod says something else now,
        # and it is one step to undo.
        self.changesRemoved.emit()
        self.refresh()
        return True

    def _edited_words(self, origin: str) -> str:
        """What a mod's row says of your edits to somebody else's mod: on its
        own row how many, and on its patch's row that they are there too --
        your changes to a mod are read together, whichever way each one
        reaches the game (docs/proposals/patches-proposal.md, section 4)."""
        from ..core import edits, patching

        layer = self.loadout.layer(origin)
        if layer is None:
            return ""
        if layer.patches:
            made = edits.count(self.loadout, layer.patches)
            theirs = self.loadout.layer(layer.patches)
            return trn(
                "and %(n)s edit of yours to %(mod)s's own ModOps, under it",
                "and %(n)s edits of yours to %(mod)s's own ModOps, under it",
                made, mod=theirs.label if theirs is not None else layer.patches,
            ) if made else ""
        if patching.is_mine(self.loadout, layer):
            return ""
        made = edits.count(self.loadout, origin)
        waiting = sum(1 for record in self.loadout.held_edits if record.get("origin") == origin)
        if waiting:
            # Before how many there are: it is the part that wants doing.
            said = trn("%(n)s edit held after an update", "%(n)s edits held after an update",
                       waiting)
            return said if not made else tr(", ", context="between items of a list").join([
                said, trn("%(n)s edit of yours", "%(n)s edits of yours", made)])
        return trn("%(n)s edit of yours", "%(n)s edits of yours", made) if made else ""

    def menu_for(self, item):
        """What can be done to one row, as a menu nobody has shown yet.

        Everything here is reachable from somewhere else -- the Edit menu, a
        button beside the editor, the File menu -- and nowhere else is beside
        the thing it is about.  An op is a row in this list; the mod whose
        settings you want to change is a row in this list; so this is where
        somebody looks for them.

        Built apart from being shown so that what it offers can be asked
        without a modal menu sitting on the screen waiting for a click.
        """
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        kind = item.data(0, KIND_ROLE) if item is not None else None
        origin = self._origin_of(item)
        layer = self.loadout.layer(origin)

        if kind == "op":
            uid = item.data(0, OP_ROLE)
            op = self.loadout.op(uid)
            edit = menu.addAction(tr("Edit this pattern…"))
            edit.setEnabled(self._can_edit(op))
            if op is not None and not op.can_expand:
                edit.setToolTip(
                    tr("Read from a loadout saved before a pattern kept its ModOp.")
                )
            elif op is not None and not op.searches:
                edit.setToolTip(tr("It names its assets. Change it in the XML view."))
            elif op is not None and op.scope:
                edit.setToolTip(tr("It is a group. The ModOps in it are changed one at a time."))
            edit.triggered.connect(lambda: self.opActivated.emit(uid))
            self._add_narrow(menu, [uid])
            self._add_option_actions(menu, origin, op)
        elif kind in ("change", "asset", "group"):
            guid = item.data(0, OPEN_ROLE) or item.data(0, GUID_ROLE)
            if guid:
                menu.addAction(
                    tr("Open in asset editor"),
                    lambda: self.changeActivated.emit(
                        guid, item.data(0, PATH_ROLE) or "", self._origin_of(item),
                        item.data(0, KEY_ROLE) if kind == "change" else None,
                    ),
                )
        if kind in ("op", "change", "unread"):
            which = {
                "op": item.data(0, OP_ROLE),
                "change": item.data(0, KEY_ROLE),
                "unread": item.data(0, UNREAD_ROLE),
            }[kind]
            in_file = menu.addAction(
                tr("Show in file"),
                lambda: self.fileRequested.emit(self._origin_of(item), kind, which),
            )
            in_file.setToolTip(tr("Goes to it on the Mod file tab. Your changes go where they did."))
        picked = self.picked_for_option()
        if picked is not None:
            under = menu.addAction(tr("Put under an option…"))
            under.setToolTip(tr("Wraps the rows picked in a group that runs only while a toggle is on."))
            under.triggered.connect(lambda: self.optionGroupRequested.emit(*picked))
        if kind in XML_KINDS:
            show = menu.addAction(tr("Show XML…"), lambda: self._show_xml(item))
            show.setToolTip(tr(
                "The ModOps the Studio writes for this, as they go into the mod's file."
            ))
        if not menu.isEmpty():
            menu.addSeparator()

        if kind == "mod":
            narrow = menu.addAction(tr("Filter this mod's changes"))
            narrow.setToolTip(tr("Hides the other mods. What you type then looks in this one."))
            narrow.triggered.connect(lambda: self.filter_mod(origin))
            self._add_narrow(menu, None, origin)
            if self.loadout.game == games.ANNO_117 and (
                is_own_mod(origin) or (layer is not None and self.loadout.yours(layer))
            ):
                declare = menu.addAction(tr("Edit options…"))
                declare.setToolTip(tr(
                    "What this mod lets its players decide, and which of its ModOps read each."
                ))
                declare.triggered.connect(lambda: self.optionRequested.emit(origin, ""))
        if layer is not None:
            if origin in self._runs:
                self._add_script_actions(menu, layer)
            self._add_folder_action(menu, layer)
            tweak = menu.addAction(tr("Mod options…"))
            # Every mod row offers it, so that a mod with none says so once
            # rather than leaving somebody hunting for where the entry went.
            tweak.setEnabled(bool(layer.offered))
            tweak.setToolTip(
                tr("The settings %s offers, and what its ModOps read back.", layer.label)
                if layer.offered
                else tr("%s declares no settings of its own.", layer.label)
            )
            tweak.triggered.connect(lambda: self.optionsRequested.emit(origin))

            from ..core import forking, patching

            # A mod of yours is changed in place, or made a project of: never
            # patched. It was offered on one held from here, one with your
            # name on it and the loadout's own (R3-20, hands-on run 3).
            mine = (
                self.loadout.yours(layer) or origin in self.loadout.projects
                or patching.looks_mine(self.loadout, layer)
            )
            patch = menu.addAction(tr("Patch this mod…"))
            patch.setEnabled(
                not mine and not layer.patches and self.loadout.patch_for(origin) is None
            )
            patch.setToolTip(tr(
                "%s is yours: your changes go into it, with no patch.", layer.label
            ) if mine and not layer.patches else tr(
                "Makes a mod of yours that loads after this one and holds your changes."
            ))
            patch.triggered.connect(lambda: self.patchRequested.emit(origin))

            if patching.without_a_project(self.loadout, layer):
                # As Currently editing offers it: the row is where somebody
                # looking at the mod wonders why it is read like anybody
                # else's (section 5.5 of the projects and loadouts proposal).
                project = menu.addAction(tr("Make a project of yours…"))
                project.setToolTip(tr(
                    "%s has your name on it. A project keeps it, and changes go straight in.",
                    layer.label,
                ))
                project.triggered.connect(lambda: self.projectRequested.emit(origin))

            if forking.can_fork(self.loadout, layer):
                # Translators: your own copy, somebody else's mod made yours.
                fork = menu.addAction(tr("Make your own copy…"))
                fork.setToolTip(tr(
                    "Makes a copy of it yours, in its place, to change directly."
                ))
                fork.triggered.connect(lambda: self.forkRequested.emit(origin))

            if layer.patches:
                needing = menu.addAction(tr("Needs another mod too…"))
                needing.setEnabled(bool(patching.can_need(self.loadout, layer)))
                needing.setToolTip(tr("Couples this patch to another loaded mod as well."))
                needing.triggered.connect(lambda: self.needRequested.emit(origin))
                for other in layer.needs:
                    held_layer = self.loadout.layer(other)
                    menu.addAction(
                        tr("No longer needs %s", held_layer.label if held_layer is not None else other),
                        lambda other=other: self.unneedRequested.emit(origin, other),
                    )

            if not self.loadout.yours(layer):
                # The Mods menu asks about every loaded mod at once; this is
                # the one somebody looking at a mod's row wonders about.
                update = menu.addAction(
                    tr("Check for an update…"),
                    lambda: self.updateRequested.emit(origin),
                )
                update.setToolTip(tr("Asks mod.io whether %s has a newer file.", layer.label))
            if any(record.get("origin") == origin for record in self.loadout.held_edits):
                menu.addAction(
                    tr("Review edits held after an update…"),
                    lambda: self.heldRequested.emit(origin),
                )
            menu.addSeparator()

        # .emit, not the signal itself: a Qt signal instance is not callable,
        # so an action given one raises when it is triggered rather than doing
        # anything -- and nothing said so at the point it was added.
        menu.addAction(tr("Add mods…"), self.addModsRequested.emit)
        menu.addAction(tr("New pattern…"), self.newPatternRequested.emit)
        if self.tree.selectedItems():
            revert = menu.addAction(tr("Revert selected"), self._remove_selected)
            enabled, tip = self._revert_offer()
            revert.setEnabled(enabled)
            revert.setToolTip(tip)
        # Off the panel's foot, where it sat a click from the buttons used
        # all day; it asks first, and says what it takes with it.
        clear = menu.addAction(tr("Revert all…"), self._clear_all)
        clear.setToolTip(tr("Removes every change in the loadout."))
        clear.setEnabled(len(self.loadout) > 0)
        if kind == "mod" and layer is not None:
            # Last, and only on the mod's own row: one right-click on a
            # change inside the mod offered it beside what that row is for.
            menu.addSeparator()
            unload = menu.addAction(
                tr("Unload %s…", layer.label),
                lambda: self.unloadRequested.emit(origin),
            )
            if origin == self.loadout.home:
                # Without it the loadout has nothing to write, and "Currently
                # editing" a stand-in for a mod nobody asked for.
                unload.setEnabled(False)
                unload.setToolTip(tr(
                    "This loadout is %s, so it stays loaded. Start another loadout "
                    "for other work.",
                    layer.label,
                ))
            else:
                unload.setToolTip(tr(
                    "Takes it out of the loadout, with its changes. Edit ▸ Undo "
                    "puts it back."
                ))
        menu.setToolTipsVisible(True)
        return menu

    # -- a row as the XML it is written as --------------------------------
    def _xml_rows(self, item) -> list:
        """The rows one question about ``item`` covers: the selection it is
        part of, where it is part of one, as far as that is in the same mod --
        a document is one mod's, and two mods' ops in one would be nobody's."""
        chosen = self.tree.selectedItems()
        if item not in chosen:
            return [item]
        origin = self._origin_of(item)
        return [row for row in chosen if self._origin_of(row) == origin]

    def xml_for(self, item) -> tuple:
        """``(heading, xml, note)``: what ``item`` -- or the selection it is in
        -- is written as, and what a reader should know that the XML does not
        say.  A mod's row is every op it writes."""
        rows = self._xml_rows(item)
        origin = self._origin_of(item)
        mod = self._mod_name(origin)
        # An export leaves a mod switched off as a mod out altogether, so what
        # is shown is what it would write, and nothing of it is "in the mod".
        unwritten = tr(
            "%s is switched off, so the Studio does not write it. This is what "
            "it writes once the mod is switched on again.", mod,
        ) if origin in self.loadout.switched_off() else ""
        if any(row.data(0, KIND_ROLE) == "mod" for row in rows):
            view = generator.for_mod(self.loadout, origin)
            off = _written_as_comments(view)
            note = unwritten or (trn(
                "%(n)s switched-off change is a comment here, as it is in the mod.",
                "%(n)s switched-off changes are comments here, as they are in the mod.",
                off,
            ) if off else "")
            xml = build.modops_xml(view)
            return tr("%s, as the Studio writes it", mod), xml, note

        keys, ops, unread, inside = self._xml_parts(rows, origin)
        notes = [unwritten] if unwritten else []
        if inside:
            notes.append(tr(
                "This is part of a ModOp kept as written. It is written whole, so the "
                "whole ModOp is shown."
            ))
        off = _written_as_comments(
            build.fragment_view(self.loadout, origin, keys, ops, unread)
        )
        if off and not unwritten:
            notes.append(trn(
                "%(n)s of these is switched off and is written to the mod as "
                "a comment.",
                "%(n)s of these are switched off and are written to the mod as "
                "comments.",
                off,
            ))
        if len(rows) == 1:
            what = item.text(0).removeprefix("⌗ ")
            heading = tr("%(row)s in %(mod)s, as the Studio writes it", row=what, mod=mod)
        else:
            heading = trn(
                "%(n)s row of %(mod)s, as the Studio writes it",
                "%(n)s rows of %(mod)s, as the Studio writes them",
                len(rows), mod=mod,
            )
        xml = build.fragment_xml(self.loadout, origin, keys, ops, unread)
        return heading, xml, " ".join(notes)

    def _xml_parts(self, rows, origin: str) -> tuple:
        """``(keys, ops, unread, inside)``: what of ``origin`` the rows are
        written as -- changes by key, ops by uid, unread ops by position --
        and whether a row is part of an op kept as written, which is shown
        whole."""
        keys, ops, unread, inside = set(), set(), set(), False

        def walk(row):
            if row.isHidden():
                return
            kind = row.data(0, KIND_ROLE)
            if kind == "change":
                key = tuple(row.data(0, KEY_ROLE) or ())
                change = self.loadout.changes.get(key)
                op = self._op_of(change) if change is not None else None
                if op is not None:
                    # An op drawn as the one change it comes to: the row is
                    # the op, and is written as it.
                    ops.add(op.uid)
                else:
                    keys.add(key)
            elif kind == "op":
                ops.add(row.data(0, OP_ROLE))
            elif kind == "unread":
                unread.add(row.data(0, UNREAD_ROLE))
            else:
                for index in range(row.childCount()):
                    walk(row.child(index))

        for row in rows:
            # Anything under an op is written as part of it, so the op is the
            # answer whichever of its rows was asked.  A group is not such an
            # op: what is under it is each an op of its own, and a row there
            # is asked about for itself.
            above = row if row.data(0, KIND_ROLE) == "op" else row.parent()
            while above is not None and above.data(0, KIND_ROLE) != "op":
                above = above.parent()
            held = self.loadout.op(above.data(0, OP_ROLE)) if above is not None else None
            if above is row:
                ops.add(row.data(0, OP_ROLE))
            elif held is not None and not held.scope:
                ops.add(held.uid)
                inside = True
            else:
                walk(row)
        # A group is written with what is in it, and an op in a group inside
        # it: alone, the first is an empty pair of tags and the second looks
        # as though it always ran.
        scopes = {uid for uid in ops if getattr(self.loadout.op(uid), "scope", False)}
        if scopes:
            for op in self.loadout.ops:
                if scopes & set(op.within):
                    ops.add(op.uid)
            for entry in self.loadout.unread:
                if entry.origin == origin and scopes & set(entry.within):
                    unread.add(entry.order)
        for uid in list(ops):
            ops.update(getattr(self.loadout.op(uid), "within", ()) or ())
        for entry in self.loadout.unread:
            if entry.origin == origin and entry.order in unread:
                ops.update(entry.within)
        return keys, ops, unread, inside

    def _show_xml(self, item) -> None:
        dialog = XmlDialog(*self.xml_for(item), parent=self)
        dialog.exec()
        dialog.deleteLater()

    def _on_double_click(self, item, _column) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "change":
            self.changeActivated.emit(
                item.data(0, OPEN_ROLE) or item.data(0, GUID_ROLE),
                item.data(0, PATH_ROLE),
                self._origin_of(item),
                item.data(0, KEY_ROLE),
            )
        elif kind == "asset":
            self.changeActivated.emit(item.data(0, OPEN_ROLE), "", self._origin_of(item), None)
        elif kind == "op":
            uid = item.data(0, OP_ROLE)
            op = self.loadout.op(uid)
            if self._can_edit(op):
                self.opActivated.emit(uid)
            else:
                item.setExpanded(not item.isExpanded())
        elif kind == "group" and not item.isExpanded():
            item.setExpanded(True)
        elif kind == "group":
            self.changeActivated.emit(item.data(0, OPEN_ROLE), "", self._origin_of(item), None)
        elif kind == "unread":
            self.unreadRequested.emit(
                (item.data(0, ORIGIN_ROLE) or "", item.data(0, UNREAD_ROLE)))
        elif kind == "bundled":
            # A filter can hide the row it stands for; showing everything is
            # the only way to get there, and is what somebody double-clicking
            # to go to a mod has asked for.
            target = item.data(0, GOTO_ROLE)
            if not self.go_to_mod(target) and self._filtering():
                self.clear_filter()
                self.go_to_mod(target)
        else:
            item.setExpanded(not item.isExpanded())

    def _remove_selected(self) -> None:
        found = self._selected_changes()
        if not found:
            return
        # Somebody else's mod is asked the three ways on first.
        if not self._may_change_inside(
            change.origin for change in (self.loadout.changes.get(tuple(key)) for key in found)
            if change is not None
        ):
            return
        # By key, not by (guid, path): a structural op's identity carries a uid,
        # and several additions can share one path.
        reverted = set()
        for key in [self._again(tuple(key)) for key in found]:
            change = self.loadout.changes.get(key)
            alone = self._op_of(change) if change is not None else None
            if change is not None:
                reverted.add(change.origin)
            if alone is not None:
                # The op goes with the one change it came to: it is what
                # would be written.
                self.loadout.drop_op(alone.uid)
            self.loadout.remove_key(key)
        # Somebody else's mod with nothing left of it is no longer loaded, so
        # it stops being listed -- otherwise reverting a whole mod leaves an
        # empty heading. A mod of yours is different: a patch, or the mod the
        # loadout carries on with, is the place your edits go, and it was
        # empty before the first one too. Reverting that edit puts it back
        # the way it started, not out of the loadout.
        #
        # Only a mod something was reverted in, and only with nothing left:
        # no change, no op kept as written, nothing unread.  Judged by its
        # changes alone and over every mod, reverting one change in one mod
        # unloaded every other mod that had none -- one whose ops could not
        # be read, or come to nothing today -- and its ops with it.
        left = set(self.loadout.origins())
        left.update(op.origin for op in self.loadout.ops)
        left.update(entry.origin for entry in self.loadout.unread)
        for layer in list(self.loadout.layers):
            if (
                layer.origin in reverted and layer.origin not in left
                and not self.loadout.yours(layer)
            ):
                self.loadout.drop_layer(layer.origin)
        self.refresh()
        self.changesRemoved.emit()

    def _clear_all(self) -> None:
        count = len(self.loadout)
        if not count:
            return
        # What the button does, which is to the loadout and not to one mod
        # of it: it asked about "this mod" and then cleared every mod here.
        mods = len(self.loadout.origins(loaded=True))
        if mods > 1:
            said = [trn(
                "Discard the one change in this loadout? It is the only "
                "change of the %(mods)s mods loaded in it.",
                "Discard all %(n)s changes in this loadout? That is every "
                "change of all %(mods)s mods loaded in it.",
                count, mods=number(mods),
            )]
        else:
            said = [trn(
                "Discard the one change in this mod?",
                "Discard all %(n)s changes in this mod?",
                count,
            )]
        others = sum(1 for layer in self.loadout.layers if not self.loadout.yours(layer))
        if others:
            said.append(trn(
                "%(n)s mod that is not yours is unloaded from the loadout "
                "with its changes. Its folder is not touched.",
                "%(n)s mods that are not yours are unloaded from the loadout "
                "with their changes. Their folders are not touched.",
                others,
            ))
        said.append(tr("Saved versions in the loadout history are not affected."))
        answer = ask(
            self,
            tr("Revert everything?"),
            "\n\n".join(said),
            tr("&Revert all"),
            default_action=False,
        )
        if not answer:
            return
        self.loadout.revert_all()
        self.refresh()
        self.changesRemoved.emit()
