"""The asset editor: find an asset, see what it does, then change it.

Finding and editing are one step.  Searching is how you *address* the thing you
are about to edit, so the search field sits at the top of this panel and drops
its results down over the tree; picking a row folds the list away again.

The lxml tree we load here is treated as immutable: it is the pristine base-game
state.  Edits live only in the :class:`Loadout`, which keeps generated paths
and their predicates honest -- if we mutated the tree, editing a ``<Product>``
that another entry's path keys off would silently produce a ModOp that no longer
matches the shipped file.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape

from lxml import etree
from PySide6.QtCore import QEvent, QMimeData, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    declaring,
    effective,
    keptops,
    patching,
    recording,
    refs,
    repeats,
    scripts,
    texts,
    typedops,
    xmlview,
)
from ..core import paths as pathmod
from ..core.changes import OP_ADD, OP_MERGE, OP_REMOVE, Change, is_own_mod
from ..core.i18n import STORED, join_list, tr, tr_kw, tr_noop, tr_stored, trn
from . import asset_xml, theme
from .add_field_dialog import AddFieldDialog
from .asset_picker import AssetPickerDialog
from .asset_xml import AssetXmlView
from .browser_panel import AssetSearchBar
from .help_tip import HelpTip
from .navigation import AssetTrail, NavButtons
from .widgets import (
    busy_cursor,
    enter_activates,
    fit_columns,
    flash_button_feedback,
    hold_lines,
    item_text_room,
    open_script,
    show_plain,
    show_rich,
)

# The two ways the panel shows an asset. Kept under these names in the
# settings, so they are not for translating.
VIEW_FIELDS = "fields"
VIEW_XML = "xml"

ELEM_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ORIG_ROLE = Qt.ItemDataRole.UserRole + 2

# The last entry of "Currently editing" while the loadout has no mod of its own:
# picking it makes one. No mod folder can be called this -- the characters are
# not allowed in a file name -- so it cannot collide with a loaded mod.
NEW_OWN_MOD = "<new mod of your own>"
# An entry of "Currently editing" for somebody else's mod with no patch of
# yours yet: picking it makes one. Followed by that mod's origin.
NEW_PATCH = "<new patch for>"
# And for a mod with your name on it that no project keeps: picking it offers
# to make one (section 5.5 of the projects and loadouts proposal).
NEW_PROJECT = "<new project for>"
# The mod that writes over this row after the one being edited into.
BEATEN_ROLE = Qt.ItemDataRole.UserRole + 12
POS_ROLE = Qt.ItemDataRole.UserRole + 3
EMPTY_ROLE = Qt.ItemDataRole.UserRole + 4
SPATH_ROLE = Qt.ItemDataRole.UserRole + 5   # predicate-free path, for the schema
ORIGIN_ROLE = Qt.ItemDataRole.UserRole + 6   # effective.Origin for this node
ADD_ROLE = Qt.ItemDataRole.UserRole + 7      # "entry" / "field", or None
# What tells a list entry from its siblings, painted dimmed after its tag.
IDENT_ROLE = Qt.ItemDataRole.UserRole + 13
# What the row says of the mod it came from, for as long as its value is not
# edited: the text and colour of the last column, and the value's colour.
NOTE_ROLE = Qt.ItemDataRole.UserRole + 14

# The inline add affordance, painted at the right edge of the Property column.
# Odd width so there is an exact centre pixel: at an even size the arms sit a
# pixel off centre, and a small off-centre cross reads as a star, not a plus.
PLUS_SIZE = 15
PLUS_MARGIN = 6
PLUS_ARM = 4

# Rows must be tall enough to hold a QLineEdit editor; at the default height the
# editor overflows its cell and the user sees only the top half of what they type.
ROW_HEIGHT = 26

# What a copied block says beside its XML: the game and the place it came
# from, so a paste back into that place need not be held to the schema.
BLOCK_MIME = "application/x-anno-studio-block"


VALUE_COLUMN = 1


def plus_rect(row_rect: QRect) -> QRect:
    """Where the inline "+" sits inside a Property-column cell."""
    top = row_rect.top() + (row_rect.height() - PLUS_SIZE) // 2
    return QRect(
        row_rect.right() - PLUS_SIZE - PLUS_MARGIN, top, PLUS_SIZE, PLUS_SIZE
    )


def paint_plus(painter, box: QRect, colour) -> None:
    """The boxed "+" in ``box``, sharp at any interface size.

    Everything is placed in device pixels and drawn with the scaling taken
    off. At 125% or 175% a logical pixel is not a whole number of device
    pixels, and a one-pixel line placed in logical pixels lands between two
    of them: the border goes soft and the two arms come out different
    thicknesses.
    """
    # From the painter's coordinates to the device's pixels: the scale, and
    # where this widget sits in its window, which need not be a whole pixel.
    to_device = painter.deviceTransform()
    scale = to_device.m11()
    origin = to_device.map(QPointF(box.x(), box.y()))
    left, top = round(origin.x()), round(origin.y())
    side = round(box.width() * scale)
    line = max(1, round(scale))
    # An odd side for a line of odd thickness and an even one for an even,
    # so the cross has a centre to sit on. Off centre, it reads as a star.
    if (side - line) % 2:
        side -= 1
    arm = round(PLUS_ARM * scale)
    middle = (side - line) // 2

    painter.setWorldTransform(to_device.inverted()[0] * painter.worldTransform())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(colour, line))
    inset = line / 2
    painter.drawRoundedRect(
        QRectF(left + inset, top + inset, side - line, side - line),
        3.5 * scale, 3.5 * scale,
    )
    # The arms are filled rectangles: a stroked cross this small blurs.
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.fillRect(QRect(left + middle - arm, top + middle, 2 * arm + line, line), colour)
    painter.fillRect(QRect(left + middle, top + middle - arm, line, 2 * arm + line), colour)


class RowHeightDelegate(QStyledItemDelegate):
    """Row sizing, the inline "+", and the rule that only Value is editable.

    Item flags in a QTreeWidget are per-row, not per-column, so making a row
    editable would otherwise let the user type into "Refers to" and "Original"
    as well -- neither of which means anything.
    """

    def __init__(self, panel=None):
        super().__init__(panel)
        self._panel = panel

    def createEditor(self, parent, option, index):
        if index.column() != VALUE_COLUMN:
            return None
        return super().createEditor(parent, option, index)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), ROW_HEIGHT))
        return size

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        rect.setHeight(max(rect.height(), ROW_HEIGHT))
        editor.setGeometry(rect)

    def paint(self, painter, option, index):
        """Draw a "+" on rows that can take a child, so adding is one click."""
        add = index.data(ADD_ROLE) if index.column() == 0 else None
        ident = index.data(IDENT_ROLE) if index.column() == 0 else None
        if not add and not ident:
            super().paint(painter, option, index)
            return
        # Keep the label clear of the glyph rather than letting it run underneath.
        trimmed = QStyleOptionViewItem(option)
        self.initStyleOption(trimmed, index)
        trimmed.rect = QRect(option.rect)
        if add:
            trimmed.rect.setRight(
                max(option.rect.left(), option.rect.right() - PLUS_SIZE - PLUS_MARGIN * 2)
            )
        super().paint(painter, trimmed, index)
        if ident:
            self._paint_ident(painter, trimmed, index, ident)
        if not add:
            return

        box = plus_rect(option.rect)
        # Lit only when the cursor is over the glyph itself. Row-level hover
        # promised a click anywhere on the row, which is not what happens.
        hot = self._panel is not None and self._panel.is_plus_hovered(index)
        colour = QColor(theme.ACCENT_TEXT)
        colour.setAlpha(255 if hot else 120)

        painter.save()
        paint_plus(painter, box, colour)
        painter.restore()


    def _paint_ident(self, painter, option, index, ident) -> None:
        """An entry's identifier, dimmed, just past its tag: ``Item (Marble)``.

        Painted rather than put in the text, because the text is the tag and a
        great deal reads it back as one -- paths, keys, the add dialogs.
        """
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        area, margin = item_text_room(opt)
        metrics = QFontMetrics(opt.font)
        start = area.left() + margin + metrics.horizontalAdvance(opt.text + "  ")
        room = QRect(start, area.top(), area.right() - margin - start, area.height())
        if room.width() <= metrics.horizontalAdvance("(…)"):
            return
        shown = metrics.elidedText(
            "(%s)" % ident, Qt.TextElideMode.ElideRight, room.width()
        )
        # The row's own colour where it has one -- an added entry stays green
        # and a removed one red -- only quieter.
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        colour = QColor(brush.color() if isinstance(brush, QBrush) else theme.TEXT_DIM)
        if isinstance(brush, QBrush):
            colour.setAlpha(170)
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(colour)
        painter.drawText(
            room, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), shown
        )
        painter.restore()


def _with_keys(text: str, standard) -> str:
    """A menu entry's text with its keys shown beside it, without the entry
    taking them: a menu built for one row must not hold on to Ctrl+C."""
    keys = QKeySequence(standard).toString(QKeySequence.SequenceFormat.NativeText)
    return "%s\t%s" % (text, keys)


def _is_identity_guid(elem) -> bool:
    """True for ``/Values/Standard/GUID`` -- the asset's own identity.

    Other GUID fields (a list entry pointing at another asset, say) are ordinary
    editable values; only this one is off limits.
    """
    if elem.tag != "GUID":
        return False
    parent = elem.getparent()
    if parent is None or parent.tag != "Standard":
        return False
    grandparent = parent.getparent()
    return grandparent is not None and grandparent.tag == "Values"


PROPERTY_WIDTH = 300
# How Value, Refers to and Original share what Property leaves. A header can
# only stretch one column, and "Refers to" swallowed every extra pixel of a
# wide window while Value and Original -- the longer, more often cut short
# -- stayed as they were built.
FIELD_SHARES = {1: 0.42, 2: 0.33, 3: 0.25}


class EditorPanel(QWidget):
    """Search for an asset, then inspect and change it, in one place."""

    changesUpdated = Signal()
    # ModOps typed in the XML view were applied to a mod: its origin, the
    # settings of its own it read and did not declare before, and what was
    # typed as the step of undo that recorded it holds it (``typed_applied``).
    typedApplied = Signal(str, list, object)
    # A setting an op reads was clicked in the XML view: whose and which.
    optionRequested = Signal(str, str)
    editingChanged = Signal()  # a different mod is being written into
    # The user picked a different one *themselves*. Kept apart from the
    # signal above because that one is raised by anything that moves the
    # target -- including making a patch, which moves it on your behalf --
    # and only a deliberate pick is worth asking a question about.
    targetPicked = Signal(str)
    newAssetRequested = Signal()
    referencesRequested = Signal(str)  # show this GUID in the References step
    # A different asset is open now -- not raised for reloading the same one.
    assetOpened = Signal(str)
    # Go to an op in its mod's file: the mod, "op" or "unread", and which.
    fileRequested = Signal(str, str, object)
    # The panel shows the asset the other way now: VIEW_FIELDS or VIEW_XML.
    viewChanged = Signal(str)
    # A sentence for the status bar about the step just made here.
    said = Signal(str)

    def __init__(self, loadout, parent=None, trail: AssetTrail = None):
        super().__init__(parent)
        self.loadout = loadout
        # The assets looked at, shared with the reference graph when the window
        # hands one over: the two tabs are about the same asset.
        self.trail = trail if trail is not None else AssetTrail(self)
        self.index = None
        self.guid = None
        self.asset_root = None
        self.effective = None
        self._overwritten = {}
        self.is_new_asset = False
        self.asset_label = ""
        self._loading = False
        self._plus_hover = None
        self._entry_values = {}
        # Values typed since the tree was drawn, by the element of their row.
        self._typed = {}
        # The XML view is filled when it is looked at, not behind the tree.
        self._xml_stale = True
        # Asked when something typed in the XML view would be left unapplied:
        # ``ask(asset name, whether it was the asset that was typed)`` answers
        # "apply", "discard" or "" to stay. The window hands it over; with
        # none, what was typed is discarded.
        self.ask_unapplied = None
        # What else has to be settled before changes go to another mod: the
        # window gives the Mod file tab's, where text typed and not applied
        # is for the mod it was typed in.  Returns False to stay.
        self.settle_elsewhere = None
        # Where changes go, given the mod picked in *Changes go to*: that mod,
        # another -- the patch of yours already in front of it -- or None to
        # stay.  The window asks; with none, it is the mod picked.
        # Makes the patch for somebody else's mod, picked as "A new patch
        # for…": its origin, or None where none was made.
        self.patch_wanted = None
        # And the project for a mod of yours nothing keeps, picked as "Make…
        # a project of yours".
        self.project_wanted = None
        # Asked before ModOps typed in the XML view are applied, where they
        # change a search: how many assets it reaches, to go ahead or not.
        self.confirm_reach = None
        # An Apply in the XML view that changed the mod, until the window
        # has made a step of undo of it: ``(which pane, the asset, the mod,
        # the text)``.
        self.typed_applied = None
        # Set by load_asset when it opened nothing because the user chose to
        # stay with what they had typed -- not because the asset is missing.
        self.stayed = False

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)

        self._build_ui()
        self.clear()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        top = QHBoxLayout()
        # Translators: the name of the Studio's tab for changing one asset.
        # Following a reference to another asset and wanting the last one back
        # is as common here as in the graph, and it is the same trail.
        self.nav = NavButtons(self.trail, host=self)
        self.nav.backRequested.connect(self.go_back)
        self.nav.forwardRequested.connect(self.go_forward)
        top.addWidget(self.nav)
        top.addStretch(1)
        self.new_asset_button = QPushButton(tr("New asset…"))
        self.new_asset_button.setToolTip(
            tr("Create an asset this mod adds to the game, from scratch or as a copy.")
        )
        self.new_asset_button.clicked.connect(self.newAssetRequested.emit)
        top.addWidget(self.new_asset_button)
        # Translators: opens the Studio's graph of which assets point at which.
        self.references_button = QPushButton(tr("References…"))
        self.references_button.setToolTip(
            tr("See what points at this asset, and what it points at.")
        )
        self.references_button.clicked.connect(
            lambda: self.guid and self.referencesRequested.emit(self.guid)
        )
        self.revert_asset_button = QPushButton(tr("Revert asset"))
        self.revert_asset_button.clicked.connect(self._revert_asset)
        # What players see an asset this mod adds called -- see core.texts.
        self.in_game_button = QPushButton(tr("In-game name…"))
        self.in_game_button.clicked.connect(self._name_in_game)
        self.in_game_button.setVisible(False)
        top.addWidget(self.in_game_button)
        top.addWidget(self.revert_asset_button)
        # Last, where the References tab has Edit this asset: going to the
        # graph and back is one place on the screen clicked twice.
        top.addWidget(self.references_button)
        # The window adds what it has to say about the panel as a whole at
        # the end of this row, where it goes wherever the panel goes.
        self.top_row = top
        outer.addLayout(top)

        self.hint = QLabel(tr(
            "Double-click a value to change it. Right-click a section to add "
            "a field, add a list entry, or remove a node. Each change "
            "becomes one ModOp."
        ))
        self.hint.setObjectName("StepHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        # Which mod an edit lands in. Obvious with nothing else loaded and not
        # obvious at all with sixty mods open, which is exactly when it matters:
        # the asset in front of you is the whole stack's, and the change you are
        # about to make goes into exactly one of them.
        self.target_row = QWidget()
        target = QHBoxLayout(self.target_row)
        target.setContentsMargins(0, 0, 0, 0)
        target.setSpacing(6)
        self.target_caption = QLabel(tr("&Currently editing:"))
        target.addWidget(self.target_caption)
        self.target_box = QComboBox()
        self.target_caption.setBuddy(self.target_box)
        self._loading_target = False
        self.target_box.currentIndexChanged.connect(self._on_target_changed)
        target.addWidget(self.target_box)
        self.target_help = HelpTip(tr_kw(
            "Sets which mod of yours your edits become part of. Somebody "
            "else's mod is never changed: your [[patch]] for it holds what you "
            "want different about it wherever you use it, and this loadout's "
            "own mod holds what you want different here only."
        ), self.target_caption.text())
        target.addWidget(self.target_help)
        self.target_label = QLabel("")
        self.target_label.setObjectName("SubHeading")
        self.target_label.setWordWrap(True)
        target.addWidget(self.target_label, 1)
        outer.addWidget(self.target_row)

        self.search = AssetSearchBar()
        self.search.assetChosen.connect(self._on_asset_chosen)
        # A result row can ask for the graph without opening the asset first --
        # same step the editor's own References… button and the GUID fields'
        # right-click menu reach.
        self.search.referencesRequested.connect(self.referencesRequested.emit)
        outer.addWidget(self.search)
        # The result list floats over the editor, so opening it never moves the
        # tree out from under the cursor.
        self.search.attach_overlay(self)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.title_label = QLabel(tr("No asset selected"))
        self.title_label.setObjectName("AssetTitle")
        heading.addWidget(self.title_label, 1)
        # Two ways to look at the one asset, with the same mod written into,
        # the same undo and the same changes list behind both.
        # One control in two halves, the one pressed in being the view on
        # show: it changes everything under it, and is drawn to look it.
        switch = QHBoxLayout()
        switch.setSpacing(0)
        self.view_buttons = QButtonGroup(self)
        # Translators: the asset shown as a tree of its fields.
        self.fields_button = QPushButton(tr("Fields"))
        self.fields_button.setProperty("side", "first")
        self.fields_button.setToolTip(tr("Shows the asset as fields you can edit."))
        # Translators: the asset shown as XML, a tab for each mod.
        self.xml_button = QPushButton(tr("XML"))
        self.xml_button.setProperty("side", "last")
        self.xml_button.setToolTip(tr("Shows the asset as XML, mod by mod."))
        for button in (self.fields_button, self.xml_button):
            button.setObjectName("Segment")
            button.setCheckable(True)
            self.view_buttons.addButton(button)
            switch.addWidget(button)
        self.fields_button.setChecked(True)
        self.fields_button.setAccessibleName(tr("Fields view"))
        self.xml_button.setAccessibleName(tr("XML view"))
        self.xml_button.toggled.connect(self._on_view_toggled)
        heading.addLayout(switch)
        self.view_help = HelpTip(tr(
            "Fields shows the asset as a tree you can edit. XML shows a tab "
            "for the base game and for each mod that changes the asset, in "
            "load order. A mod's tab has the ModOps it writes for this asset "
            "and the asset as it leaves it, with the lines it changed marked. "
            "The last tab's asset is what the game loads."
        ), tr("Fields or XML"))
        heading.addWidget(self.view_help)
        outer.addLayout(heading)

        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("SubHeading")
        self.subtitle_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        outer.addWidget(self.subtitle_label)

        self.fields_page = QWidget()
        fields = QVBoxLayout(self.fields_page)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(8)

        tools = QHBoxLayout()
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText(tr("Filter fields…  (e.g. “cycle”, “amount”)"))
        self.filter_box.setAccessibleName(tr("Filter fields"))
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._filter_timer.start)
        tools.addWidget(self.filter_box, 1)

        # Every other field is nested inside some container you can right-click
        # to add to; the top level itself has no row of its own to click on --
        # this is the one way to add a field straight under the asset (e.g. an
        # <EffectSource> the base game only bothers shipping on some variants).
        # Placed beside the fields it actually affects, not up with New asset —
        # New asset creates a different asset entirely, not a field on this one.
        self.add_field_button = QPushButton(tr("Add field…"))
        self.add_field_button.setToolTip(tr(
            "Adds a field the base game uses directly on this asset."
        ))
        self.add_field_button.clicked.connect(self._add_root_field)
        tools.addWidget(self.add_field_button)

        # Translators: a filter over the asset's fields: show only the changed ones.
        self.only_changed = QCheckBox(tr("Only changed"))
        self.only_changed.setToolTip(
            tr("Show only what this mod touches: edited values, additions and removals.")
        )
        self.only_changed.toggled.connect(self._apply_filter)
        tools.addWidget(self.only_changed)

        # Assets are padded with dozens of empty <Item/> and unused sections;
        # they are noise until somebody deliberately goes looking for them.
        # Translators: a filter over the asset's fields: show the empty ones too.
        self.show_empty = QCheckBox(tr("Show empty"))
        self.show_empty.setToolTip(
            tr("Show fields and list entries that are empty in the base game.")
        )
        self.show_empty.toggled.connect(self._apply_filter)
        tools.addWidget(self.show_empty)

        # One button that says what a click does to the tree as it stands,
        # as the Mod changes panel's does: there were two, side by side, and
        # one of them always did nothing.
        # Translators: "Collapse all" folds the field tree back to its sections.
        self.fold_button = QPushButton(tr("Expand all"))
        self.fold_button.clicked.connect(self._toggle_fold)
        tools.addWidget(self.fold_button)
        self._all_open = False
        fields.addLayout(tools)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(tr("Fields"))
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([
            # Translators: column headings: a field's name, its value, the
            # asset a GUID in it points at, and the value before this mod.
            tr("Property"), tr("Value"), tr("Refers to"), tr("Original"),
        ])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemCollapsed.connect(self._on_row_collapsed)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.tree.setMouseTracking(True)   # so the "+" can light up under the cursor
        self.tree.setItemDelegate(RowHeightDelegate(self))
        self.tree.viewport().installEventFilter(self)
        self.tree.setColumnWidth(0, PROPERTY_WIDTH)
        # Property holds the width it is dragged to, and the other three
        # share the rest: a column dragged wider takes from its neighbours
        # rather than scrolling the tree sideways.
        fit_columns(self.tree, shares=FIELD_SHARES, keep=(0,))
        self.tree.doubleClicked.connect(self._maybe_pick_asset)
        enter_activates(self.tree, self._enter_on_row)
        # The tree's own, so a value being typed keeps its Ctrl+C and Ctrl+V.
        for keys, does in (
            (QKeySequence.StandardKey.Copy, self._copy_block),
            (QKeySequence.StandardKey.Paste, self._paste),
        ):
            shortcut = QShortcut(QKeySequence(keys), self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda does=does: does(self.tree.currentItem()))
        fields.addWidget(self.tree, 1)

        path_row = QHBoxLayout()
        path_caption = QLabel(tr("ModOp &path:"))
        path_row.addWidget(path_caption)
        self.path_box = QLineEdit()
        path_caption.setBuddy(self.path_box)
        self.path_box.setReadOnly(True)
        theme.monospace(self.path_box)
        path_row.addWidget(self.path_box, 1)
        self.copy_path_button = QPushButton(tr("Copy", context="verb"))
        self.copy_path_button.clicked.connect(self._copy_path)
        path_row.addWidget(self.copy_path_button)
        fields.addLayout(path_row)

        self.warning_label = QLabel("")
        self.warning_label.setObjectName("StepHint")
        self.warning_label.setWordWrap(True)
        # Fixed height so switching between "no hint", the one-line container
        # hint, and the (possibly two-line) positional-path warning never
        # shifts the tree above it -- a selection is not supposed to jiggle
        # the layout just by what it happens to say below the tree.
        hold_lines(self.warning_label, 2, 6)
        fields.addWidget(self.warning_label)

        self.xml = AssetXmlView(self.loadout)
        self.xml.fieldRequested.connect(self._show_field)
        self.xml.applyRequested.connect(self._apply_typed)
        self.xml.fileRequested.connect(self.fileRequested)
        self.xml.assetRequested.connect(self.load_asset)
        self.xml.optionRequested.connect(self.optionRequested)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.fields_page)
        self.stack.addWidget(self.xml)
        outer.addWidget(self.stack, 1)

    # -- loading ------------------------------------------------------------
    def set_index(self, index) -> None:
        self.index = index
        self.search.set_index(index)
        self.xml.set_source(index)
        self.clear()
        # The list deliberately stays shut here.  It used to drop open as soon
        # as a base file was adopted, so the first thing a new user saw on this
        # tab was a panel they had not asked for -- and the first click anywhere
        # else made it disappear again. Clicking or typing in the search box is
        # what opens it, which is also what explains what it is.

    def refresh_search(self) -> None:
        self.search.refresh()

    def focus_search(self, show_results: bool = True) -> None:
        self.search.focus_search(show_results)

    def _on_asset_chosen(self, guid: str) -> None:
        self.load_asset(guid)

    def clear(self) -> None:
        self._loading = True
        self.tree.clear()
        self._loading = False
        self._set_all_open(False)
        self.guid = None
        self.asset_root = None
        self.effective = None
        self.is_new_asset = False
        self.asset_label = ""
        self.title_label.setText(tr("No asset selected"))
        self.subtitle_label.setText(tr("Search above to pick the asset you want to change."))
        self.hint.setVisible(True)
        self.path_box.clear()
        self.warning_label.clear()
        self.revert_asset_button.setEnabled(False)
        self.add_field_button.setEnabled(False)
        self.references_button.setEnabled(False)
        self.search.set_current_guid(None)
        self.xml.clear()
        self._xml_stale = False

    def load_asset(self, guid: str) -> bool:
        self.stayed = False
        if self.index is None:
            return False
        if guid != self.guid and not self.settle_typed():
            self.stayed = True
            return False
        # The asset as it will be once this mod applies, not as it shipped: you
        # have to be able to build on what you already added.  Stopping at the
        # mod being edited into rather than at the end of the stack, because
        # that is the document its ModOps are written against -- a value some
        # later mod goes on to overwrite is not the value this one replaces.
        self.effective = self.index.effective_asset(guid, upto=self.editing())
        root = self.effective.element
        if root is None:
            if self.guid == guid:
                # The asset that was open just stopped existing -- most often
                # because its creating change was just deleted from the dock.
                # Leaving the stale tree in place let you keep "editing" an
                # asset that is already gone.
                self.clear()
            return False
        # One fingerprint for the row and what else is under its GUID.
        steady = getattr(self.index, "steady", None)
        with steady() if steady is not None else nullcontext():
            row = self.index.asset_row(guid)
            standing = getattr(self.index, "standing_of", None)
            standing = standing(guid) if standing is not None else None

        arrived = guid != self.guid
        self.guid = guid
        # What to do here is for an empty editor. With an asset open, the
        # room is the asset's.
        self.hint.setVisible(False)
        self.asset_root = root
        self._overwritten = self._later_claims(guid, root)
        self.is_new_asset = bool(row and row.is_new)
        self.asset_label = row.label if row else ("GUID " + guid)
        self.title_label.setText(self.asset_label)
        # Separate facts side by side, not one sentence: each is whole.
        bits = ["GUID %s" % guid]
        if self.is_new_asset:
            # Whose it is by the rule the in-game name button goes by: any
            # mod's asset counts as new, and it is not always this one's.
            if self._own_creator(guid) is not None:
                bits.append(tr("created by this mod"))
            else:
                finder = getattr(self.index, "creator_for", None)
                maker = finder(guid) if finder is not None else None
                if maker is not None:
                    # Translators: %s is the mod that makes the asset.
                    bits.append(tr("created by %s", self._layer_name(maker.origin)))
        if standing is not None:
            bits.extend(self._other_copies(standing))
        if row and row.template:
            bits.append(tr("Template: %s", row.template))
        if row and row.group_path:
            bits.append(row.group_path)
        if row and getattr(row, "named_by", ""):
            # Which mod's texts the name is from -- and, where another mod that
            # loads names it too, that the one shown is the one that loads last.
            # Translators: which mod's texts give the asset its name.
            bits.append(tr("named by %s", row.named_by))
            if row.also:
                bits.append(tr("also named differently by %s", join_list(row.also)))
        shadow = self._unread_reaching(guid, root)
        if shadow:
            bits.append(trn(
                "%(n)s ModOp the Studio could not read reaches this asset",
                "%(n)s ModOps the Studio could not read reach this asset",
                len(shadow),
            ))
        # The XML view says this under its panes, a mod to a tab.  Here the
        # values shown are what every mod up to the one being edited leaves,
        # without these ops, and nothing else says the game still has them.
        undone = xmlview.reverted_reaching(self.index, guid, self.editing())
        tips = [self._shadow_tip(shadow)]
        if standing is not None:
            tips.append(self._copies_tip(standing))
        if undone:
            bits.append(trn(
                "%(n)s kept ModOp reaches this asset, its change here reverted",
                "%(n)s kept ModOps reach this asset, their changes here reverted",
                len(undone),
            ))
            tips.append(asset_xml.said_of_reverted(len(undone)) + self._whose(undone))
        self.subtitle_label.setText("   ·   ".join(bits))
        self.subtitle_label.setToolTip("\n\n".join(tip for tip in tips if tip))

        self._populate()
        self.search.set_current_guid(guid)
        self.revert_asset_button.setEnabled(bool(self.loadout.for_asset(guid, self.editing())))
        self._show_in_game_button(guid)
        self.references_button.setEnabled(True)
        if arrived:
            self.trail.visit(guid)
            self.assetOpened.emit(guid)
        return True

    def _unread_reaching(self, guid: str, root) -> list:
        """The unread ops, of mods loaded up to the one being edited, aimed here.

        An op the Studio could not read is carried into the export as written
        but never applied to the asset shown, so the value in front of you may
        not be the one the game has -- and a change made against it records
        that value as its "before".  Said on the asset rather than discovered
        in game.  Ops aimed by GUID are matched by it; searches are put to the
        asset as shown, which is what they would be put to in game.
        """
        return xmlview.unread_reaching(self.loadout, guid, root, upto=self.editing())

    def _other_copies(self, standing) -> list:
        """What the subtitle says of a GUID with more than one asset under
        it: how many others there are, and which one the game finds -- the
        one shown, which every op aimed at the GUID edits."""
        said = [trn(
            "%(n)s other asset has this GUID",
            "%(n)s other assets have this GUID",
            standing.others,
        )]
        if standing.apart:
            said.append(repeats.said_apart())
            said.append(
                tr("shown as the game ships it") if standing.change is None
                else tr("shown as the first in the file")
            )
        elif standing.change is None:
            said.append(tr("the game finds the one it ships, shown here"))
        elif standing.shipped:
            said.append(tr("the game finds this one, put in ahead of the one it ships"))
        else:
            said.append(tr("the game finds this one"))
        return said

    def _copies_tip(self, standing) -> str:
        """Every asset a mod makes under the GUID, in the order they stand in
        the file, and whether the game ships one too."""
        names = []
        for change in standing.copies:
            layer = self.loadout.layer(change.origin)
            # "My Anno Mod" stays English, as in the XML view's tabs.
            mod = (layer.label if layer is not None else
                   change.origin or self.loadout.own.name or "My Anno Mod")
            names.append(tr("%(name)s (%(mod)s)",
                            name=effective.new_asset_name(change) or tr("unnamed"), mod=mod))
        said = tr("Made by mods under this GUID, as they stand in the file: %s.", join_list(names))
        if standing.shipped:
            said += " " + tr("The base game ships one as well.")
        return said

    def _whose(self, ops) -> str:
        """A sentence naming the mods ``ops`` are of, to follow one about the
        ops, or "" where they are all of the mod being edited: the count
        under the heading is then of that mod, as the reader takes it."""
        origins = list(dict.fromkeys(op.origin for op in ops))
        if origins == [self.editing()]:
            return ""
        names = []
        for origin in origins:
            layer = self.loadout.layer(origin)
            # "My Anno Mod" stays English, as in the XML view's tabs.
            names.append(layer.label if layer is not None else
                         origin or self.loadout.own.name or "My Anno Mod")
        return " " + trn(
            "It is a ModOp of %(mods)s.", "They are ModOps of %(mods)s.",
            len(ops), mods=join_list(names),
        )

    def _shadow_tip(self, shadow) -> str:
        if not shadow:
            return ""
        lines = [tr(
            "These ModOps could not be read, so their effect on this asset "
            "is not shown. The value shown may differ from the game's, and a "
            "change records it as the value before."
        )]
        for entry in shadow:
            lines.append("")
            lines.append("%s: %s" % (self.loadout.layer(entry.origin).label
                                     if self.loadout.layer(entry.origin) else
                                     (entry.origin or tr("your mod")), tr_stored(entry.label)))
            lines.append("  " + tr_stored(entry.why or ""))
        return "\n".join(lines)

    # -- history ------------------------------------------------------------
    def go_back(self) -> None:
        self._walk(self.trail.back)

    def go_forward(self) -> None:
        self._walk(self.trail.forward)

    def _walk(self, step) -> None:
        """Open the asset one step along the trail, passing any that are gone.

        The graph can draw an asset that no longer exists as a bare GUID; the
        editor has nothing to show for one -- a new asset whose creating change
        was reverted -- so it steps on past it rather than stopping on nothing.

        Staying put -- what was typed for this asset was kept, or could not be
        applied -- puts the trail back where it was: it had moved before the
        question was asked, and Back then started from an asset nobody was on.
        """
        if self.nav.back_button.hasFocus() or self.nav.forward_button.hasFocus():
            # The button goes grey at the end of the trail, and Qt hands on
            # the keyboard of a control that greys out under it: to New
            # asset…, the next along.  It goes to the asset, as it does from
            # Revert asset, whichever step this is.
            self._focus_asset()
        place = self.trail.place
        while True:
            guid = step()
            if guid is None or guid == self.guid or self.load_asset(guid):
                return
            if self.stayed:
                self.trail.return_to(place)
                return

    def mousePressEvent(self, event) -> None:
        """The mouse's side buttons walk the trail, as they do in the graph."""
        if event.button() == Qt.MouseButton.BackButton:
            self.go_back()
            return
        if event.button() == Qt.MouseButton.ForwardButton:
            self.go_forward()
            return
        super().mousePressEvent(event)

    def _populate(self) -> None:
        # Drawing an asset asks the workspace about every reference in it --
        # what it is called, whether a mod made it -- and nothing edits the
        # loadout meanwhile. Without holding it steady, each question
        # fingerprinted every change again: 524 times for one asset of a
        # 400-mod loadout, 33 s of the 33.
        steady = getattr(self.index, "steady", None)
        with steady() if steady is not None else nullcontext():
            self._draw()
        self._refresh_xml()

    # -- the two views --------------------------------------------------------
    def view(self) -> str:
        """Which way the asset is shown: ``VIEW_FIELDS`` or ``VIEW_XML``."""
        return VIEW_XML if self.xml_button.isChecked() else VIEW_FIELDS

    def set_view(self, view: str) -> None:
        (self.xml_button if view == VIEW_XML else self.fields_button).setChecked(True)

    def _on_view_toggled(self, as_xml: bool) -> None:
        if not as_xml and not self.settle_typed():
            # Staying with what was typed: an edit made in Fields meanwhile
            # would be taken out again by applying text that does not have it.
            self.set_view(VIEW_XML)
            return
        self.stack.setCurrentWidget(self.xml if as_xml else self.fields_page)
        if as_xml and self._xml_stale:
            self._refresh_xml()
        self.viewChanged.emit(self.view())

    def show_mod(self, origin: str, key=None) -> None:
        """Turn the XML view to ``origin``'s tab, where it is in front and
        that mod reaches the open asset: a change opened from the changes
        list is shown as the mod it is under wrote it, at the change ``key``
        where there is one.  The Fields view is always the asset as of the
        mod changes go to, so it is left alone."""
        if self.view() != VIEW_XML or self._xml_stale:
            return
        if key:
            self.xml.show_change(origin, key)
        else:
            self.xml.show_mod(origin)

    def _refresh_xml(self) -> None:
        """Show the open asset in the XML view, now if it is in front and
        when it is next looked at if not."""
        if self.view() != VIEW_XML or self.index is None:
            self._xml_stale = True
            return
        with busy_cursor():
            self.xml.show_asset(self.guid, self.editing())
        self._xml_stale = False

    # -- ModOps typed in the XML view ----------------------------------------
    def _apply_typed(self, kind: str, text: str) -> bool:
        """Read what was typed for the open asset -- its ModOps, or the asset
        itself -- and change the mod to match; whether it could be read.  One
        step to undo."""
        if self.guid is None or self.index is None:
            return False
        typed_asset = kind == asset_xml.ASSET
        undeclared = declaring.undeclared(self.loadout, self.editing())
        with busy_cursor():
            if typed_asset:
                found, done = typedops.read_asset_and_apply(
                    self.index, self.guid, self.editing(), text
                )
            else:
                found = typedops.read(self.index, self.guid, self.editing(), text)
                done = None
        if not typed_asset and not (found.error or found.loose):
            if not self._reach_agreed(found, text):
                # What was typed stays typed, and nothing is said of it.
                return False
            with busy_cursor():
                done = typedops.apply_read(
                    self.index, self.guid, self.editing(), found, self.xml.typed_from()
                )
        if done is None:
            self.xml.refuse(found, kind)
            return False
        # What was typed is the mod's now, as the Studio read it: the pane is
        # written again from the mod, like every other.
        self.xml.revert_typed()
        if done.moved:
            self._reload_keeping_view()
            self.revert_asset_button.setEnabled(
                bool(self.loadout.for_asset(self.guid, self.editing()))
            )
        self.xml.applied(
            found, done, self.index.display_name,
            # Whether the asset the mod leaves is the one that was typed.
            not_all=typed_asset and not typedops.same_xml(
                text, self.xml.asset_in_front().toPlainText()
            ),
        )
        if done.moved:
            typed = (kind, self.guid, self.editing(), text)
            self.typed_applied = typed
            self.changesUpdated.emit()
            self.typedApplied.emit(self.editing(), undeclared, typed)
        return True

    def typed_undone(self, typed: tuple) -> bool:
        """An Apply of ``typed`` was undone, and the views show the mod
        without it: put the text back where it was typed, as text typed and
        not applied.  Only with that asset shown in the XML view and changes
        still going to that mod: Redo has it back from anywhere else."""
        kind, guid, origin, text = typed
        if (
            self.guid != guid or self.editing() != origin
            or self.view() != VIEW_XML or self._xml_stale
        ):
            return False
        return self.xml.put_back(kind, guid, origin, text)

    def _reach_agreed(self, found, text: str) -> bool:
        """Whether to go ahead with typed ModOps that change a search: asked
        with how many assets it reaches, now that it has been read.  The
        button could only say "a search" before."""
        if self.confirm_reach is None:
            return True
        reach = typedops.reach(
            self.index, self.guid, self.editing(), text, self.xml.typed_from()
        )
        if reach is None or not reach.search:
            return True
        assets = set(reach.assets) | {answer.guid for answer in found.answers}
        return bool(self.confirm_reach(len(assets)))

    def settle_typed(self) -> bool:
        """Before leaving what was typed in the XML view: apply it, discard
        it, or stay.  False means stay."""
        if not self.xml.has_unapplied():
            return True
        typed_asset = self.xml.typed_kind() == asset_xml.ASSET
        answer = (
            self.ask_unapplied(self.asset_label, typed_asset) if self.ask_unapplied
            else "discard"
        )
        if answer == "apply":
            return self._apply_typed(self.xml.typed_kind(), self.xml.typed_text())
        if answer == "discard":
            self.xml.revert_typed()
            return True
        return False

    def find_in_asset(self) -> bool:
        """Put the keyboard where text in the open asset is found: the XML
        view's find bar, or the Fields view's filter.  False with no asset
        open, when there is nothing to find in."""
        if self.guid is None:
            return False
        if self.view() == VIEW_XML:
            return self.xml.open_find()
        self.filter_box.setFocus()
        self.filter_box.selectAll()
        return True

    def _show_in_xml(self, item) -> None:
        """Go to a row's field in the XML view, and to the ModOp that writes
        it where the mod changes go to writes one."""
        path, _positional = self._path_for(item)
        if not path:
            return
        key = None
        origin = self._origin(item)
        if self._owned_here(origin):
            key = origin.change_key
        else:
            for change in self.loadout.for_asset(self.guid, self.editing()):
                held = change.sets
                if (held[0] if held is not None else change.path) == path:
                    key = change.key
                    break
        self.set_view(VIEW_XML)
        self.xml.reveal(path, key)

    def _show_field(self, path: str) -> None:
        """Go to the row at ModOp ``path`` in the Fields view."""
        self.set_view(VIEW_FIELDS)
        if self.focus_on_path(path):
            self.tree.setFocus()

    def _draw(self) -> None:
        self._loading = True
        self._plus_hover = None   # the item it pointed at is about to be deleted
        self._entry_values = {}
        self._typed = {}
        self.tree.clear()
        values = self.asset_root.find("Values")
        # Everything interesting lives under <Values>; show the rest read-only.
        for child in self.asset_root:
            if not isinstance(child.tag, str):
                continue
            if child is values:
                continue
            item = QTreeWidgetItem(self.tree, [child.tag, (child.text or "").strip(), ""])
            item.setForeground(0, QBrush(QColor(theme.TEXT_DIM)))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if values is not None:
            for child in values:
                self._add_node(self.tree, child)
        self._loading = False

        self.tree.expandToDepth(0)
        self._set_all_open(False)
        self._apply_filter()
        self.add_field_button.setEnabled(bool(self._root_missing_fields()))
        # Emptying the tree tells nobody that the row it was on is gone, so
        # the path box and the hint under it went on being about that row:
        # an entry just reverted, or one of the asset open before.  A reload
        # that finds the row again puts the tree back on it, which says so.
        self._on_current_changed(self.tree.currentItem(), None)

    def _add_node(self, parent, elem) -> None:
        if not isinstance(elem.tag, str):  # comments / processing instructions
            return
        children = [c for c in elem if isinstance(c.tag, str)]
        text = (elem.text or "").strip()

        if isinstance(parent, QTreeWidget):
            item = QTreeWidgetItem(parent)
        else:
            item = QTreeWidgetItem(parent)
        item.setText(0, elem.tag)
        item.setData(0, ELEM_ROLE, elem)
        parent_spath = (
            parent.data(0, SPATH_ROLE)
            if isinstance(parent, QTreeWidgetItem)
            else "/Values"
        )
        item.setData(0, SPATH_ROLE, (parent_spath or "/Values") + "/" + elem.tag)

        if not children and not text:
            item.setData(0, EMPTY_ROLE, True)

        if children:
            item.setText(1, trn("%(n)s field", "%(n)s fields", len(children)))
            self._label_entry(item, elem)
            item.setForeground(1, QBrush(QColor(theme.TEXT_DIM)))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            for child in children:
                self._add_node(item, child)
        else:
            item.setText(1, text)
            item.setData(0, ORIG_ROLE, text)
            if _is_identity_guid(elem) and not self.is_new_asset:
                # The asset's own GUID is what every ModOp keys off. Changing it
                # would not edit this asset, it would retarget the edit at a
                # different (probably nonexistent) one.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(1, QBrush(QColor(theme.TEXT_DIM)))
                item.setToolTip(
                    1,
                    tr("This asset's identity. ModOps target it by this GUID, so it "
                       "cannot be edited."),
                )
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setData(0, EMPTY_ROLE, not text)
            self._annotate_reference(item)

        self._mark_addable(item)
        self._style_by_origin(item, elem)

    def _label_entry(self, item, elem) -> None:
        """Say which entry of a list a row is, beside its tag.

        Seven rows called ``Item`` are seven rows to open one at a time to find
        the one you want.  What tells them apart is nearly always one field
        inside -- a cost's Ingredient, a production's Product -- so that field
        is read out, with a GUID given the name the game calls it by.

        Only a field that says what the entry *is*: one of the keys lists are
        known to use, or a reference.  Path generation will settle for any
        unique field, an Amount say, and a reader has no use for that.

        The field that singles the entry out among its siblings, where one
        does: that is the one the list is told apart by.  Where none does --
        a second cost entry given an ingredient the list already has -- the
        entry is still read out, and so is the one it repeats.  Whether a path
        can key an entry is the path's business (it falls back to a position,
        and says so); what a row is called does not wait on it.
        """
        parent = elem.getparent()
        if parent is None:
            return
        siblings = [
            other for other in parent
            if isinstance(other.tag, str) and other.tag == elem.tag
        ]
        if len(siblings) < 2 and elem.tag != pathmod.LIST_ENTRY:
            return
        shared = []
        for tag in pathmod.key_candidates(elem):
            value = self._value_shown(elem, tag)
            if not value:
                continue
            leaf = tag.rsplit("/", 1)[-1]
            known = leaf in pathmod.KEY_CHILDREN
            if not known and not refs.could_be_reference(leaf, value):
                continue
            if not self._unique_in_list(parent, siblings, tag, value):
                shared.append((tag, value, known))
                continue
            if self._name_entry(item, elem, tag, value, known):
                return
        for tag, value, known in shared:
            if self._name_entry(item, elem, tag, value, known):
                return

    def _name_entry(self, item, elem, tag: str, value: str, known: bool) -> bool:
        """Read an entry's row out by ``value``, its field ``tag``; False where
        that would say nothing, a number that names no asset."""
        named = self._entry_name(value)
        if not known and not named:
            return False
        item.setData(0, IDENT_ROLE, named or value)
        if not item.toolTip(0):
            item.setToolTip(
                0,
                "%s  —  %s %s%s"
                % (elem.tag, tag, value, "  (%s)" % named if named else ""),
            )
        return True

    def _value_shown(self, entry, tag: str) -> str:
        """``pathmod.value_of`` as the tree reads now: a value typed since it
        was drawn is in its row, and not in the element the row was drawn from."""
        child = entry.find(tag)
        typed = self._typed.get(id(child)) if child is not None else None
        return typed[1] if typed is not None else pathmod.value_of(entry, tag)

    def _unique_in_list(self, parent, siblings, tag, value) -> bool:
        """Whether only one of ``siblings`` holds ``value`` at ``tag``.

        Counted once per list and field rather than once per entry: an asset
        pool is hundreds of entries, and comparing each against all the others
        is what would make opening one slow.
        """
        key = (id(parent), siblings[0].tag, tag)
        held = self._entry_values.get(key)
        if held is None or held[0] is not parent:
            counts = {}
            for other in siblings:
                seen = self._value_shown(other, tag)
                if seen:
                    counts[seen] = counts.get(seen, 0) + 1
            held = (parent, counts)
            self._entry_values[key] = held
        return held[1].get(value, 0) == 1

    def _relabel_entry(self, item) -> None:
        """A value was typed into ``item``: read its entry's row out again.

        Nothing draws the tree again for a typed value, so an entry just added
        and given its ingredient went on reading plain ``Item`` until
        something else did.  Only the entry typed in: what its siblings are
        called does not turn on it.
        """
        elem = item.data(0, ELEM_ROLE)
        if elem is None:
            return
        # Held with the element, which is what keeps its id its own.
        self._typed[id(elem)] = (elem, item.text(1).strip())
        was, self._loading = self._loading, True
        try:
            row = item.parent()
            # The field an entry is read out by is its own, or one level in.
            for _level in range(2):
                entry = row.data(0, ELEM_ROLE) if row is not None else None
                holder = entry.getparent() if entry is not None else None
                if holder is None:
                    return
                self._entry_values = {
                    key: held for key, held in self._entry_values.items()
                    if held[0] is not holder
                }
                row.setData(0, IDENT_ROLE, None)
                if not row.data(0, ADD_ROLE):
                    row.setToolTip(0, "")
                self._label_entry(row, entry)
                row = row.parent()
        finally:
            self._loading = was

    def _entry_name(self, value: str) -> str:
        """What the game calls the asset ``value`` names, or ""."""
        if not value.isdigit() or self.index is None:
            return ""
        row = self.index.asset_row(value)
        return (row.display or row.name or "") if row is not None else ""

    # -- editing ------------------------------------------------------------
    def editing(self) -> str:
        """The mod an edit made here lands in."""
        return self.loadout.editing_origin()

    def _later_claims(self, guid: str, root) -> dict:
        """Nodes some mod loading after this one writes over.

        The tree shows the asset as the mod being edited into leaves it, which
        is the document its ModOps are written against -- but not necessarily
        what the game ends up with.  Typing a value into a field a later mod
        also sets is work the player will never see, and that is worth saying
        on the row rather than leaving to be discovered in the conflicts list.

        Read from the changes rather than by building the whole stack again:
        it is a resolve per contested field, not a second effective tree.
        """
        rank = self.loadout.layer_rank(self.editing())
        # Each node's last claimant in load order, by its name: the loadout's
        # own mod has no origin to go by, and it overwrites like any other.
        latest: dict = {}
        found = {}
        for change in self.loadout.for_asset(guid):
            if not change.active or not change.path:
                continue
            theirs = self.loadout.layer_rank(change.origin)
            if theirs <= rank:
                continue
            path = change.path
            if change.ensured:
                # The field it sets, not the section it is merged into -- and
                # one that fills a section in overwrites nothing.
                held = change.sets
                if held is None:
                    continue
                path = held[0]
            for node in pathmod.resolve(root, path):
                if id(node) not in latest or latest[id(node)] <= theirs:
                    latest[id(node)] = theirs
                    found[id(node)] = self._layer_name(change.origin)
        return found

    def _owned_here(self, origin) -> bool:
        """Whether a node was added by the very mod being edited into.

        Only then does an edit to it belong in that ModOp's payload.  A node
        some *other* mod added is an ordinary node by the time this mod loads,
        so editing it is a ModOp of this mod's own -- and folding it into the
        other mod's payload would quietly rewrite somebody else's file.
        """
        if not origin.is_mod_owned:
            return False
        change = self._change_by_key(origin.change_key)
        return change is not None and change.origin == self.editing()

    def _addition_here(self, origin):
        """``(change, kept)`` for an edit to a node of ``origin``.

        ``change`` is the change that added the node, where the mod being
        edited into did (:meth:`_owned_here`), and ``None`` otherwise.

        ``kept`` is the op that change is an answer of, where the op stays as
        its author wrote it, and ``None`` otherwise.  An op read from a mod's
        file goes out as itself, so an edit made in the payload of its answer
        was shown here and never written.  The rule is the one
        ``Loadout.place`` has for a value an op replaces.  A plain op, one
        change and nothing else said, is let go of for the edit
        (:meth:`_let_go_of_op`), and the edit goes into the payload.  Any
        other op stays, and the edit is a change of its own after it: the
        exception to ``kept``.
        """
        if not self._owned_here(origin):
            return None, None
        change = self._change_by_key(origin.change_key)
        op = self.loadout.op(change.op_uid) if change.op_uid else None
        if op is not None and op.plain and sum(
            1 for held in self.loadout.changes.values() if held.op_uid == op.uid
        ) == 1:
            op = None
        return change, op

    def _into_op(self, change, kept, edit) -> bool:
        """Make an edit to what ``change`` adds in the op it is an answer
        of, where that op writes it down, and say whether that was done:
        ``edit()`` is the :mod:`.keptops` call that makes it
        (:func:`.keptops.type_into`, :func:`~.keptops.add_into`,
        :func:`~.keptops.take_out_of`).

        For an op kept as written, which would otherwise have the edit as
        an exception after it, and for a plain op in a group: let go of
        for the edit, it would be the Studio's ModOp where the op was,
        outside the group, and run whatever the group's condition said.
        A plain op elsewhere is let go of as before (:meth:`_let_go_of_op`).
        """
        op = self.loadout.op(change.op_uid) if change is not None and change.op_uid else None
        if op is None or (kept is None and not op.within):
            return False
        return edit()

    def _let_go_of_op(self, change) -> None:
        """Make an answer of a plain op the Studio's own, once its payload
        has been edited: it takes the op's place in the file, its note and
        its switch, and is written as it now reads where the op was."""
        op = self.loadout.op(change.op_uid) if change.op_uid else None
        if op is None:
            return
        change.op_uid = ""
        change.search = ""
        change.authored = True
        change.order = op.order
        change.active = op.active
        if not change.note:
            change.note = op.note
        self.loadout.drop_op(op.uid, with_changes=False)

    @staticmethod
    def _left_by(change, payload_path: str, fallback: str) -> str:
        """The value ``change`` gives the node at ``payload_path``: what an
        exception to its op is typed over, whatever an earlier exception has
        made of the row since."""
        root = effective.parse_payload(change)
        node = effective.payload_node(root, payload_path) if root is not None else None
        return (node.text or "").strip() if node is not None else fallback

    def _layer_name(self, origin: str) -> str:
        return self.loadout.label_of(origin) or tr("your mod")

    def _origin(self, item):
        """Where the node behind ``item`` came from."""
        origin = item.data(0, ORIGIN_ROLE)
        if origin is not None:
            return origin
        elem = item.data(0, ELEM_ROLE)
        if elem is None or self.effective is None:
            return effective.Origin(effective.PROV_BASE)
        return self.effective.origin(elem)

    def _style_by_origin(self, item, elem) -> None:
        """Colour a row by where it came from, and record its origin.

        "Where it came from" is relative to the mod being edited into.  What an
        *earlier* mod did is part of the document this one is handed, so it
        reads as inherited rather than as an edit of yours -- and, importantly,
        the value a change of yours would replace is the one that mod left,
        not the one it found.
        """
        if self.effective is None:
            return
        origin = self.effective.origin(elem)
        item.setData(0, ORIGIN_ROLE, origin)
        beaten = self._overwritten.get(id(elem))
        item.setData(0, BEATEN_ROLE, beaten or None)

        key = getattr(origin, "change_key", None)
        change = self._change_by_key(key) if key else None
        mine = change is not None and change.origin == self.editing()
        # None when it is this mod's own doing, which each line says outright.
        whose = None if change is None or mine else self._layer_name(change.origin)

        if origin.provenance == effective.PROV_REMOVED:
            for column in range(4):
                font = item.font(column)
                font.setStrikeOut(True)
                item.setFont(column, font)
                item.setForeground(column, QBrush(QColor(theme.REMOVED)))
            item.setText(3, tr("removed by %s", whose) if whose else tr("removed by this mod"))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        elif origin.provenance == effective.PROV_ADDED:
            for column in range(4):
                font = item.font(column)
                font.setItalic(True)
                item.setFont(column, font)
                item.setForeground(column, QBrush(QColor(theme.CHANGED)))
            self._note_origin(
                item, tr("added by %s", whose) if whose else tr("added by this mod"),
                theme.CHANGED, value=theme.CHANGED,
            )
        elif origin.provenance == effective.PROV_SET:
            # Another mod made the field and this change set it: not an
            # addition, and with no original of the game's to show.
            if mine:
                self._mark_item(item)
                self._note_origin(item, tr("set by this mod"), theme.TEXT_DIM)
            else:
                self._note_origin(item, tr("set by %s", whose), theme.TEXT_DIM)
        elif origin.provenance == effective.PROV_EDITED:
            if mine:
                # The tree shows the edited value, so "Original" has to come
                # from the change that made it -- the effective tree no longer
                # holds it.
                item.setData(0, ORIG_ROLE, change.old_value)
                self._mark_item(item)
            elif change is not None:
                # Somebody else's edit, already applied by the time this mod
                # loads. The row keeps the value it inherited as its original,
                # because that is what a ModOp of this mod's would replace.
                self._note_origin(item, tr("set by %s", whose), theme.TEXT_DIM)
        self._note_beaten(item)

    @staticmethod
    def _note_origin(item, text: str, colour: str, value: str = "") -> None:
        """Say on the row where it came from, and keep what was said: an edit
        shows the original there instead, and typed back the row says this
        again.  It was left blank, and plain, until the asset was read again."""
        item.setText(3, text)
        item.setForeground(3, QBrush(QColor(colour)))
        item.setData(0, NOTE_ROLE, (text, colour, value))

    def _note_beaten(self, item) -> None:
        """Say, over anything else the row had to say, that it is overruled."""
        beaten = item.data(0, BEATEN_ROLE)
        if not beaten:
            return
        item.setText(3, tr("overwritten by %s", beaten))
        theme.mark(item, "warn", 3)

    def _field_evidence(self, item):
        """What the base game's own use of this exact field says about it.

        ``(seen, resolved, telling)`` for the field this row sits in, or
        ``None`` when the index cannot say -- a field the shipped game never
        uses here, which is common for one this mod just added.
        """
        if self.index is None or not hasattr(self.index, "reference_field"):
            return None
        spath = self._structural_path(item)
        if not spath or "/" not in spath:
            return None
        parent, _, tag = spath.rpartition("/")
        evidence = self.index.reference_field(parent, tag)
        return evidence if evidence[0] else None

    def _reference_target(self, item):
        """The asset this row points at, or ``None`` if it does not point at one.

        Two sources, best first: what the base game does with this exact field
        (measured while indexing, and the same evidence the References step
        draws its graph from), then -- only when the game never uses the field
        here and so has nothing to say -- the per-value guess from the field's
        name.  The first is what stops a field of durations being labelled as
        links because two thirds of them collide with a GUID, and a rotation
        of 90 degrees as the asset whose GUID is 90.
        """
        if item is None or item.data(0, ORIG_ROLE) is None or self.index is None:
            return None
        if not bool(item.flags() & Qt.ItemFlag.ItemIsEditable):
            return None  # a row that cannot be changed has nothing to pick
        elem = item.data(0, ELEM_ROLE)
        if elem is not None and _is_identity_guid(elem):
            # The asset's own GUID resolves to itself; saying so is just noise.
            # By what the row is and not by whether it can be typed in: an
            # asset this mod makes has a GUID that can.
            return None
        tag, value = item.text(0), item.text(1).strip()
        if not value.isdigit():
            return None

        # The rule the References graph draws by too (refs.is_link).
        evidence = self._field_evidence(item) or (0, 0, True)
        if not refs.is_link(tag, value, *evidence):
            return None
        return self.index.asset_row(value)

    def _annotate_reference(self, item) -> None:
        """Show what a GUID-valued field points at, in the "Refers to" column."""
        if item is not None and scripts.is_script_path(item.text(0), item.text(1)):
            self._annotate_script(item)
            return
        row = self._reference_target(item)
        label = ""
        if row is not None:
            label = (
                "%s  ·  %s" % (row.label, row.template) if row.template else row.label
            )
        item.setText(2, label)
        item.setForeground(2, QBrush(QColor(theme.TEXT_DIM)))
        if label:
            note = ""
            evidence = self._field_evidence(item)
            if evidence is not None:
                note = "\n\n" + refs.confidence_note(item.text(0), *evidence)
            item.setToolTip(
                2,
                tr("%(field)s = %(value)s\n"
                   "\n"
                   "%(target)s%(note)s\n"
                   "\n"
                   "Double-click or press Enter to pick a different asset.",
                   field=item.text(0), value=item.text(1), target=label, note=note),
            )
        else:
            item.setToolTip(2, "")

    def _script_file(self, item):
        """Where the script a ``ScriptFileName`` names is, as the game would
        find it, or None."""
        if item is None or not scripts.is_script_path(item.text(0), item.text(1)):
            return None
        supplier = scripts.locate(self.loadout, item.text(1))
        if supplier is None:
            return None
        return Path(supplier.folder) / item.text(1).strip().replace("\\", "/").lstrip("/"), supplier

    def _annotate_script(self, item) -> None:
        """A script's file, in the "Refers to" column: which mod that loads has
        it -- the last in load order, whose file the game uses -- or that none
        does, which may mean the game's own: the Studio does not list those."""
        found = self._script_file(item)
        if found is not None:
            path, supplier = found
            item.setText(2, tr("script in %s", supplier.label))
            item.setToolTip(2, tr(
                "%(field)s = %(value)s\n"
                "\n"
                "A Lua script, in %(mod)s:\n"
                "%(path)s\n"
                "\n"
                "It runs while the game runs, so its effect is not shown "
                "here. Double-click or press Enter to open it.",
                field=item.text(0), value=item.text(1), mod=supplier.label, path=path,
            ))
        else:
            item.setText(2, tr("script in no mod that loads"))
            item.setToolTip(2, tr(
                "%(field)s = %(value)s\n"
                "\n"
                "No loaded mod has this file. It may be one of the game's "
                "own scripts, which the Studio does not list, or a file that "
                "a mod is missing.", field=item.text(0), value=item.text(1),
            ))
        item.setForeground(2, QBrush(QColor(theme.TEXT_DIM)))

    def _is_reference_field(self, item) -> bool:
        return self._reference_target(item) is not None

    def _takes_an_asset(self, item) -> bool:
        """Whether an empty row is a field that holds an asset's GUID: one
        named as a GUID, or one the base game fills with GUIDs wherever it
        uses it.  A new cost entry's Ingredient is empty, and was offered
        only *Edit value* until a GUID had been typed into it (H2-27)."""
        if item is None or item.data(0, ORIG_ROLE) is None or self.index is None:
            return False
        if not bool(item.flags() & Qt.ItemFlag.ItemIsEditable) or item.text(1).strip():
            return False
        elem = item.data(0, ELEM_ROLE)
        if elem is not None and _is_identity_guid(elem):
            return False
        tag = item.text(0)
        evidence = self._field_evidence(item)
        if evidence is None:
            return refs.names_a_guid(tag)
        return refs.is_confident(tag, *evidence)

    def _picks_an_asset(self, item) -> bool:
        """Whether the row's one thing to open is the asset picker: it refers
        to an asset, or it is an empty field that takes one.  Asked by the
        menu, a double-click and Enter alike; the last two once knew only the
        first and did nothing on an empty Ingredient (A11-107)."""
        return self._is_reference_field(item) or self._takes_an_asset(item)

    def _maybe_pick_asset(self, model_index) -> None:
        """Double-clicking the "Refers to" cell opens the asset picker."""
        if model_index.column() != 2:
            return
        item = self.tree.itemFromIndex(model_index)
        found = self._script_file(item)
        if found is not None:
            open_script(found[0], found[1].folder)
            return
        if self._picks_an_asset(item):
            self._pick_asset(item)

    def _enter_on_row(self, model_index) -> None:
        """Enter on a row: what double-clicking its cells does.

        A row has one thing to open. For a script it is the file, for a
        field that refers to an asset or takes one it is the picker, and for
        any other field with a value it is the value's editor.
        """
        item = self.tree.itemFromIndex(model_index)
        if item is None:
            return
        found = self._script_file(item)
        if found is not None:
            open_script(found[0], found[1].folder)
        elif self._picks_an_asset(item):
            self._pick_asset(item)
        elif item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.tree.editItem(item, VALUE_COLUMN)

    def _pick_asset(self, item) -> None:
        if self.index is None or item.data(0, ORIG_ROLE) is None:
            return
        dialog = AssetPickerDialog(self.index, item.text(0), item.text(1), self)
        if dialog.exec() == int(QDialog.DialogCode.Accepted) and dialog.chosen_guid:
            item.setText(1, dialog.chosen_guid)

    def _change_by_key(self, key):
        if not key:
            return None
        return self.loadout.changes.get(tuple(key))

    # -- structural edits ---------------------------------------------------
    def _structural_path(self, item) -> str:
        """Predicate-free path, which is what the observed schema is keyed by."""
        return item.data(0, SPATH_ROLE) or ""

    def _schema(self):
        return self.index.schema if self.index is not None else None

    def _present_children(self, item):
        return [item.child(i).text(0) for i in range(item.childCount())]

    def _can_hold_children(self, item) -> bool:
        """Whether it makes sense to add something *inside* this node.

        A node with children obviously can.  So can one that is empty: an
        ``<Attributes/>`` this mod just added, or a section the base game ships
        blank, is a container with nothing in it yet -- indistinguishable in the
        XML from an empty value, which is why the schema gets the casting vote.
        """
        if item.data(0, ELEM_ROLE) is None:
            return False
        if item.childCount():
            return True
        if (item.text(1) or "").strip():
            return False  # it holds a value, so it is a leaf
        schema = self._schema()
        return schema is not None and schema.knows_path(self._structural_path(item))

    def _add_kind(self, item) -> str:
        """What adding to this node means: ``"entry"``, ``"field"``, or ``""``.

        Deliberately exclusive.  A list takes more entries and nothing else; a
        node with named fields takes fields and nothing else.  Offering both made
        them two routes to the same result on a list, and on a node like
        ``AdditionalHull`` "add list entry" happily stacked duplicates of a child
        that has to appear exactly once.
        """
        schema = self._schema()
        if schema is None or not self._can_hold_children(item):
            return ""
        spath = self._structural_path(item)
        if schema.is_list(spath):
            return "entry"
        if schema.missing_children(spath, self._present_children(item)):
            return "field"
        return ""

    def _mark_addable(self, item) -> None:
        """Record whether this row gets a "+", so the delegate can paint it."""
        kind = self._add_kind(item)
        item.setData(0, ADD_ROLE, kind or None)
        if kind == "entry":
            tag = self._schema().entry_tag_for(self._structural_path(item)) or "Item"
            item.setToolTip(0, tr("Click + to add another <%s> entry here.", tag))
        elif kind == "field":
            item.setToolTip(0, tr("Click + to add a field the base game uses here."))

    def _add_here(self, item) -> None:
        """The one thing "+" does, whichever kind of node this is."""
        kind = self._add_kind(item)
        if kind == "entry":
            self._add_list_entry(item)
        elif kind == "field":
            self._add_field(item)

    def _add_field(self, item) -> None:
        """Offer the fields the base game uses at this path, addable ones first."""
        schema = self._schema()
        if schema is None or self.guid is None:
            return
        spath = self._structural_path(item)
        present = self._present_children(item)
        if not schema.missing_children(spath, present):
            return  # the menu greys this out; there is nothing to say

        dialog = AddFieldDialog(schema.children_of(spath), spath, present, self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted) or not dialog.chosen_tag:
            return

        element = etree.Element(dialog.chosen_tag)
        if not dialog.is_container:
            element.text = dialog.value
        # Ensured, not added: a field has one of its name, and another mod may
        # have put it there by the time this one loads.
        self._record_addition(
            item,
            element,
            tr_noop("set %(what)s in %(where)s", context=STORED)
            % {"what": dialog.chosen_tag, "where": item.text(0)},
            op=OP_MERGE if element.tag != item.text(0) else OP_ADD,
        )

    def _add_list_entry(self, item) -> None:
        """Append a fresh entry to a list, shaped the way the game shapes them."""
        schema = self._schema()
        if schema is None or self.guid is None:
            return
        spath = self._structural_path(item)
        entry_tag = schema.entry_tag_for(spath) or "Item"
        entry = schema.new_list_entry(spath, entry_tag)
        self._record_addition(
            item, entry,
            tr_noop("add %(what)s entry to %(where)s", context=STORED)
            % {"what": entry_tag, "where": item.text(0)},
        )

    def _top_level_tags(self):
        return [
            self.tree.topLevelItem(i).text(0)
            for i in range(self.tree.topLevelItemCount())
        ]

    def _root_missing_fields(self):
        """Fields the base game uses directly under ``<Values>`` this asset lacks.

        Every other field lives inside some container with its own row to
        right-click; the top level itself has no such row, which is exactly
        why this needs its own path rather than reusing ``_add_field``.
        """
        schema = self._schema()
        if schema is None or self.guid is None:
            return []
        return schema.missing_children("/Values", self._top_level_tags())

    def _add_root_field(self) -> None:
        """Add a field straight under the asset, not nested in a container.

        A base-game asset variant sometimes simply omits a whole section
        another variant of the same building has (an ``<EffectSource>`` only
        some shrines ship, say) -- there is no existing row for that section
        to right-click, so this is the one way to add it.
        """
        if self.guid is None or self.asset_root is None:
            return
        schema = self._schema()
        if schema is None or not self._root_missing_fields():
            return

        dialog = AddFieldDialog(
            schema.children_of("/Values"), "/Values", self._top_level_tags(), self
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted) or not dialog.chosen_tag:
            return

        element = etree.Element(dialog.chosen_tag)
        if not dialog.is_container:
            element.text = dialog.value
        self._add_at_root(
            element, tr_noop("add %s to this asset", context=STORED) % dialog.chosen_tag
        )

    def _add_at_root(self, element, summary: str) -> None:
        """Put ``element`` straight under ``<Values>``, which has no row of its own."""
        values = self.asset_root.find("Values") if self.asset_root is not None else None
        if values is None:
            return
        origin = (
            self.effective.origin(values)
            if self.effective is not None
            else effective.Origin(effective.PROV_BASE)
        )

        change, kept = self._addition_here(origin)
        grown = self._grow_addition(change, kept, origin, element)
        if grown is False:
            return
        if grown is None:
            generated = pathmod.path_for(self.asset_root, values)
            self.loadout.set_change(
                self.loadout.place(Change(
                    guid=self.guid,
                    asset_label=self.asset_label,
                    path=generated.path,
                    tag=element.tag,
                    op=OP_MERGE,
                    payload=etree.tostring(element, pretty_print=True).decode().strip(),
                    summary=summary,
                    positional=generated.positional,
                    origin=self.editing(),
                    exception_of=kept.uid if kept is not None else "",
                ))
            )

        self.revert_asset_button.setEnabled(True)
        self.changesUpdated.emit()
        # It is the only field with this tag, so this is its key once reloaded.
        self._reload_keeping_view(focus_key="%s[0]" % element.tag)

    def _grow_addition(self, change, kept, origin, element) -> Optional[bool]:
        """Add ``element`` under the node of ``origin``, where this mod adds
        that node: grow what it adds rather than emit a second ModOp against
        a node the game lacks.  In the op that writes it down
        (:meth:`_into_op`), or in the payload of a plain op let go of for it.
        Whether it was added, or ``None`` where the node is no addition of
        this mod's to grow, and the edit is a ModOp of its own."""
        if self._into_op(change, kept, lambda: keptops.add_into(
            self.loadout, change, origin.payload_path, element
        )):
            self.loadout.reindex()
            return True
        if change is None or kept is not None:
            return None
        if not effective.add_to_payload(change, origin.payload_path, element):
            return False
        self._let_go_of_op(change)
        self.loadout.reindex()
        return True

    def _record_addition(self, parent_item, element, summary: str, op: str = OP_ADD) -> None:
        origin = self._origin(parent_item)
        parent_key = self._item_key(parent_item)

        change, kept = self._addition_here(origin)
        grown = self._grow_addition(change, kept, origin, element)
        if grown is False:
            return
        if grown is None:
            path, positional = self._path_for(parent_item)
            if path is None:
                return
            self.loadout.set_change(
                self.loadout.place(Change(
                    guid=self.guid,
                    asset_label=self.asset_label,
                    path=path,
                    tag=element.tag,
                    op=op,
                    payload=etree.tostring(element, pretty_print=True).decode().strip(),
                    summary=summary,
                    positional=positional,
                    origin=self.editing(),
                    exception_of=kept.uid if kept is not None else "",
                ))
            )

        self.revert_asset_button.setEnabled(True)
        self.changesUpdated.emit()
        # Land on what was just added, so a long asset does not scroll it away.
        self._reload_keeping_view(focus_parent_key=parent_key, focus_tag=element.tag)

    def _remove_node(self, item) -> None:
        """Delete a node: from our own payload if we added it, else via a ModOp."""
        if self.guid is None or item.data(0, ELEM_ROLE) is None:
            return
        origin = self._origin(item)
        parent = item.parent()
        # A payload removal takes the row with it, so fall back to its parent.
        focus_key = self._item_key(parent) if parent is not None else None
        change, kept = self._addition_here(origin)
        into = self._into_op(change, kept, lambda: keptops.take_out_of(
            self.loadout, change, origin.payload_path
        ))
        in_payload = into or (change is not None and kept is None)
        if not in_payload:
            # A base node stays, struck through, and so does what an op kept
            # as written adds.
            focus_key = self._item_key(item)
        unnoticed = False

        if into:
            self.loadout.reindex()
        elif in_payload:
            outcome = effective.remove_from_payload(change, origin.payload_path)
            if outcome == "empty":
                # The payload root itself: the whole addition goes away.
                self._let_go_of_op(change)
                self.loadout.remove_key(tuple(origin.change_key))
            elif outcome == "removed":
                self._let_go_of_op(change)
                self.loadout.reindex()
            else:
                return
        else:
            path, positional = self._path_for(item)
            if path is None:
                return
            # Asked of the entry while it is still in the asset.
            unnoticed = recording.Inert(
                getattr(self.index, "index", self.index)
            )(item.data(0, ELEM_ROLE))
            self.loadout.set_change(
                self.loadout.place(Change(
                    guid=self.guid,
                    asset_label=self.asset_label,
                    path=path,
                    tag=item.text(0),
                    op=OP_REMOVE,
                    old_value=item.data(0, ORIG_ROLE) or "",
                    summary=tr_noop("remove %s", context=STORED) % item.text(0),
                    positional=positional,
                    origin=self.editing(),
                    exception_of=kept.uid if kept is not None else "",
                ))
            )

        self.revert_asset_button.setEnabled(True)
        self.changesUpdated.emit()
        self._reload_keeping_view(focus_key=focus_key)
        if unnoticed:
            # What the XML view says beside Apply of an entry taken out of
            # the asset there, by the same rule: the removal is written, and
            # what the reader knows of such an entry is said.
            self._set_hint(tr(
                "This entry only names an asset and sets nothing, so the game "
                "may not notice it gone. The removal is written all the same."
            ))

    # -- copying and pasting a block ---------------------------------------
    def _block_of(self, item):
        """A row and everything under it, as the tree shows them now.

        Read off the rows, not the element they were drawn from: a value typed
        since the tree was drawn is in its row and not in the element, and a
        row struck out is not part of what anyone means to copy.
        """
        element = etree.Element(item.text(0))
        if item.data(0, ORIG_ROLE) is not None:
            element.text = item.text(1) or None
            return element
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, ELEM_ROLE) is None or self._origin(child).is_removed:
                continue
            element.append(self._block_of(child))
        return element

    def _copy_block(self, item) -> None:
        """Put a row and what is in it on the clipboard, as the XML it is.

        Plain XML, so it pastes into the XML view or a mod file as well; and
        with the place it came from beside it, which is what lets a paste back
        into that same place skip asking whether the game uses its shape.
        """
        if item is None or item.data(0, ELEM_ROLE) is None:
            return
        block = self._block_of(item)
        mime = QMimeData()
        mime.setText(etree.tostring(block, pretty_print=True).decode().strip())
        mime.setData(BLOCK_MIME, json.dumps({
            "game": self.loadout.game,
            "path": self._structural_path(item),
        }).encode("utf-8"))
        QGuiApplication.clipboard().setMimeData(mime)
        self._set_hint(tr("Copied <%s> and everything in it.", block.tag))

    def _clipboard_block(self):
        """The block on the clipboard and the place it was copied from:
        ``(element, path)``, the path "" where that is not known, and
        ``(None, "")`` when what is there is not one element of XML."""
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not mime.hasText():
            return None, ""
        block = effective.parse_block(mime.text())
        if block is None:
            return None, ""
        source = ""
        if mime.hasFormat(BLOCK_MIME):
            try:
                said = json.loads(bytes(mime.data(BLOCK_MIME)).decode("utf-8"))
                if said.get("game") == self.loadout.game:
                    source = said.get("path") or ""
            except (ValueError, AttributeError):
                pass
        return block, source

    def _paste_plan(self, item, block, source: str):
        """What pasting ``block`` with ``item`` selected would do.

        ``("value", item)`` types a lone field's value into the row of that
        name; ``("add", row)`` adds the block inside ``row`` -- the selected
        row, or the one it is in, so a list entry pasted on its sibling joins
        their list -- with ``None`` for the asset itself; and ``("", why)``
        where it has no place here.

        Held to the shape the base game uses, as Add field is: the block has
        to be something the game puts there, and so does everything in it.  A
        paste back where it was copied from is not asked the second part --
        it was there already.
        """
        schema = self._schema()
        if schema is None or self.guid is None or item.data(0, ELEM_ROLE) is None:
            return "", tr("Only a field of the asset takes a paste.")
        tag = block.tag
        if (
            not len(block)
            and item.text(0) == tag
            and item.flags() & Qt.ItemFlag.ItemIsEditable
            and not self._can_hold_children(item)
            and not self._origin(item).is_removed
        ):
            return "value", item

        full = False  # the row that has one already, None being the asset
        for row in (item, item.parent()):
            if row is not None and (
                not self._can_hold_children(row) or self._origin(row).is_removed
            ):
                continue
            where = self._structural_path(row) if row is not None else "/Values"
            if tag not in {f.tag for f in schema.children_of(where)}:
                continue
            if schema.is_list(where):
                if schema.entry_tag_for(where) != tag:
                    continue
            else:
                present = (
                    self._present_children(row) if row is not None
                    else self._top_level_tags()
                )
                if tag in present:
                    if full is False:
                        full = row
                    continue
            if source != where + "/" + tag:
                unknown = schema.unknown_inside(where + "/" + tag, block)
                if unknown:
                    return "", tr(
                        "The base game never has %(path)s, so this <%(tag)s> "
                        "does not fit here.",
                        path=unknown.replace("/Values/", "", 1), tag=tag,
                    )
            return "add", row
        if full is not False:
            return "", tr(
                "%(row)s has a <%(tag)s> already.",
                row=full.text(0) if full is not None else tr("This asset"), tag=tag,
            )
        return "", tr(
            "The base game never puts <%(tag)s> in %(row)s or beside it.",
            tag=tag, row=item.text(0),
        )

    def _paste(self, item) -> None:
        """Paste the block on the clipboard in, or beside, ``item``."""
        if item is None:
            return
        block, source = self._clipboard_block()
        if block is None:
            self._set_hint(tr("There is no XML on the clipboard to paste."), warn=True)
            return
        how, row = self._paste_plan(item, block, source)
        if how == "value":
            # Typed in, as if by hand: the row's own edit records it.
            row.setText(1, block.text or "")
        elif how == "add":
            if row is None:
                self._add_at_root(
                    block, tr_noop("paste %s into this asset", context=STORED) % block.tag
                )
                return
            spath = self._structural_path(row)
            self._record_addition(
                row,
                block,
                tr_noop("paste %(what)s into %(where)s", context=STORED)
                % {"what": block.tag, "where": row.text(0)},
                op=OP_ADD if self._schema().is_list(spath) or block.tag == row.text(0)
                else OP_MERGE,
            )
        else:
            self._set_hint(row, warn=True)

    # -- rebuilding without losing the user's place ------------------------
    def _item_key(self, item) -> str:
        """A stable address for a row, surviving a rebuild of the tree.

        Sibling tags repeat constantly (``Item`` after ``Item``), so each step
        carries how many same-named siblings came before it.
        """
        if item is None:
            return ""
        steps = []
        node = item
        while node is not None:
            parent = node.parent()
            if parent is not None:
                siblings = [parent.child(i) for i in range(parent.childCount())]
            else:
                siblings = [
                    self.tree.topLevelItem(i)
                    for i in range(self.tree.topLevelItemCount())
                ]
            tag = node.text(0)
            n = 0
            for sibling in siblings:
                if sibling is node:
                    break
                if sibling.text(0) == tag:
                    n += 1
            steps.append("%s[%d]" % (tag, n))
            node = parent
        return "/".join(reversed(steps))

    def _reload_keeping_view(
        self, focus_key: str = None, focus_parent_key: str = None, focus_tag: str = None
    ) -> None:
        """Rebuild from the effective tree, holding the user's place.

        A structural edit changes the shape of the tree, so the whole thing is
        rebuilt.  Without this, every add or remove threw away the scroll
        position and every expanded section, and the row you had just created
        was somewhere off screen.
        """
        if self.guid is None:
            return
        scroll = self.tree.verticalScrollBar().value()
        current = self.tree.currentItem()
        current_key = self._item_key(current) if current is not None else None
        expanded = {self._item_key(i) for i in self._iter_items() if i.isExpanded()}
        all_open = self._all_open

        self.tree.setUpdatesEnabled(False)
        try:
            self.load_asset(self.guid)
            target = None
            parent_of_new = None
            for item in self._iter_items():
                key = self._item_key(item)
                if item.childCount() and key in expanded:
                    item.setExpanded(True)
                if focus_key is not None and key == focus_key:
                    target = item
                if focus_parent_key is not None and key == focus_parent_key:
                    parent_of_new = item
                if focus_key is None and focus_parent_key is None and key == current_key:
                    target = item

            if parent_of_new is not None:
                parent_of_new.setExpanded(True)
                # The addition always appends, so it is the last child by that tag.
                for i in reversed(range(parent_of_new.childCount())):
                    child = parent_of_new.child(i)
                    if focus_tag is None or child.text(0) == focus_tag:
                        target = child
                        break
                if target is None:
                    target = parent_of_new
        finally:
            self.tree.setUpdatesEnabled(True)
        # The rows that were open are open again, and the button says so.
        self._set_all_open(all_open)

        self.tree.verticalScrollBar().setValue(scroll)
        if target is not None:
            node = target.parent()
            while node is not None:
                node.setExpanded(True)
                node = node.parent()
            self.tree.setCurrentItem(target)
            # EnsureVisible, not PositionAtCenter: if it is already on screen the
            # view does not move at all, which is what makes this feel calm.
            self.tree.scrollToItem(
                target, QAbstractItemView.ScrollHint.EnsureVisible
            )

    def _path_for(self, item):
        """Compute (and cache) the ModOp path for a tree item."""
        cached = item.data(0, PATH_ROLE)
        if cached:
            return cached, bool(item.data(0, POS_ROLE))
        elem = item.data(0, ELEM_ROLE)
        if elem is None or self.asset_root is None:
            return None, False
        generated = pathmod.path_for(self.asset_root, elem)
        item.setData(0, PATH_ROLE, generated.path)
        item.setData(0, POS_ROLE, generated.positional)
        return generated.path, generated.positional

    def _on_item_changed(self, item, column) -> None:
        if self._loading or column != 1 or self.guid is None:
            return
        original = item.data(0, ORIG_ROLE)
        if original is None:  # branch node, not an editable value
            return

        new_value = item.text(1)
        origin = self._origin(item)

        change, kept = self._addition_here(origin)
        typed = self._into_op(change, kept, lambda: keptops.type_into(
            self.loadout, change, origin.payload_path, new_value
        ))
        if typed or (change is not None and kept is None):
            # This node only exists because of one of our own ModOps, so the edit
            # belongs in that ModOp's payload -- emitting a replace against the
            # base game would target something that isn't there.
            if not typed:
                if not effective.set_payload_text(change, origin.payload_path, new_value):
                    return
                self._let_go_of_op(change)
            self.loadout.reindex()
            item.setData(0, ORIG_ROLE, new_value)
            # The value moved, so what it points at moved too. Nothing rebuilds
            # the tree on a payload edit, so this column has to be redone here.
            self._annotate_reference(item)
            self._relabel_entry(item)
            self.changesUpdated.emit()
            return

        path, positional = self._path_for(item)
        if path is None:
            return
        if kept is not None:
            original = self._left_by(change, origin.payload_path, original)

        self.loadout.set_change(
            self.loadout.place(Change(
                guid=self.guid,
                asset_label=self.asset_label,
                path=path,
                tag=item.text(0),
                old_value=original,
                new_value=new_value,
                positional=positional,
                origin=self.editing(),
                exception_of=kept.uid if kept is not None else "",
            ))
        )
        self._mark_item(item)
        self._annotate_reference(item)
        self._relabel_entry(item)
        self.revert_asset_button.setEnabled(bool(self.loadout.for_asset(self.guid, self.editing())))
        if self.only_changed.isChecked():
            self._apply_filter()
        self.changesUpdated.emit()

    def refresh_target(self) -> None:
        """Say -- and let you choose -- which mod an edit here lands in.

        With nothing else loaded there is nothing to choose and the sentence is
        the whole of it.  With a mods folder open the asset on screen is the
        whole stack's work, the row you are about to change goes into exactly
        one mod, and which one is not something to have to infer.
        """
        wanted = self.loadout.editing_origin()
        origins = self._targets()
        if wanted and wanted not in origins:
            # Somebody else's mod is never where changes go (section 5.2 of
            # the projects and loadouts proposal): a loadout saved with one
            # there sends them to its own mod.  Not a mod that is only not
            # loaded: that is remembered, for an undo of its unload.
            self.loadout.editing = wanted = ""
        # Yours is one of them once it exists, empty or not -- and while it is
        # where edits are pointed, since the first of them will make it. Until
        # then the last entry offers to make one, which is the way to a mod of
        # your own from a loadout that started as something else.
        if self.loadout.has_own_mod() or not wanted:
            origins.append("")
        else:
            origins.append(NEW_OWN_MOD)
        self._loading_target = True
        try:
            listed = [
                self.target_box.itemData(i) for i in range(self.target_box.count())
            ]
            if listed != origins:
                self.target_box.clear()
                for origin in origins:
                    self.target_box.addItem(self._target_text(origin), origin)
            else:
                # Only a name can have moved; the list is the same mods. Yours,
                # or a loaded one: the mod a loadout carries on with is renamed
                # with the loadout.
                for at, origin in enumerate(origins):
                    self.target_box.setItemText(at, self._target_text(origin))
            self.target_box.setCurrentIndex(origins.index(wanted))
        finally:
            self._loading_target = False

        others = len(self.loadout.layers)
        self.target_box.setVisible(bool(others))
        self.target_help.setVisible(bool(others))
        self.target_caption.setText(tr("&Currently editing:") if others else "")
        layer = self.loadout.layer(wanted)
        if not others and not (self.loadout.aim or self.loadout.has_own_mod()):
            # No loadout: "Currently editing My Anno Mod" named a mod nobody had
            # made (H2-24).
            show_plain(self.target_label, tr(
                "No loadout is open. Start one or open one on the Start tab."
            ))
            self.target_label.setToolTip("")
            return
        if not others:
            show_rich(
                self.target_label,
                tr("Currently editing <b>%s</b>.", escape(self.loadout.own.name or "My Anno Mod")),
            )
            self.target_label.setToolTip(tr(
                "Your mod. Set its name on the Start tab and its details on Export."
            ))
            return

        # How many of the open mods the game loads after yours. None on a
        # loadout, whose own mod says it loads last; for a mod written to
        # publish, whatever its name sorts it in front of.
        own_rank = self.loadout.layer_rank("")
        above = sum(
            1 for other in self.loadout.layers
            if self.loadout.layer_rank(other.origin) > own_rank
        )
        # The picker names the mod and how far its changes reach. The line
        # beside it says only what the picker does not.
        if not wanted and above:
            show_plain(self.target_label, self._loads_after(above))
            self.target_label.setToolTip(tr(
                "A mod that loads after yours wins where both set the same field."
            ))
            return

        if not wanted:
            show_plain(self.target_label, trn(
                "The game loads it after the %(n)s mod you have open.",
                "The game loads it after the %(n)s mods you have open.",
                others,
            ))
            self.target_label.setToolTip(tr(
                "Your mod loads last, so it wins where another mod sets the "
                "same field."
            ))
            return

        # Editing one of the loaded mods. Three things are worth saying out
        # loud, because each changes what an edit there means.
        after = sum(
            1 for other in self.loadout.layers if other.order > (layer.order if layer else 0)
        ) + (
            1 if layer is not None and self.loadout.has_own_mod()
            and own_rank > self.loadout.layer_rank(layer.origin) else 0
        )  # yours, where it loads after it
        said = []
        if layer is None or not layer.patches:
            # Somebody else's mod is never where changes go: one of yours,
            # which the picker names by its name alone.
            said.append(tr("Edits go straight into it."))
        if after:
            said.append(self._loads_after(after))
        missed = len(self.loadout.not_read(wanted))
        if missed:
            said.append(trn(
                "%(n)s of its ModOps could not be read.",
                "%(n)s of its ModOps could not be read.",
                missed,
            ))
        show_plain(self.target_label, " ".join(said))
        tip = [tr(
            "The editor shows this asset as that mod leaves it, because its "
            "ModOps are written against that."
        )]
        if missed:
            tip.append("\n" + trn(
                "%(n)s of its ModOps could not be read. An export from here writes "
                "it back as it is, and what it does is not shown.",
                "%(n)s of its ModOps could not be read. An export from here writes "
                "them back as they are, and what they do is not shown.",
                missed,
            ))
        self.target_label.setToolTip("\n".join(tip))

    @staticmethod
    def _loads_after(count: int) -> str:
        """That ``count`` open mods load after the one edits go to."""
        return trn(
            "%(n)s mod loads after it and can overwrite what you write.",
            "%(n)s mods load after it and can overwrite what you write.",
            count,
        )

    def _targets(self) -> List[str]:
        """What *Currently editing* offers, in load order, before the
        loadout's own mod: each mod of yours, and for somebody else's mod
        your patch for it or the offer to make one.  Their mod itself is
        never offered, since it is never changed (section 5 of the projects
        and loadouts proposal).  A mod whose project another loadout has
        changed since this one read it takes no edits from here
        (core.projects)."""
        found: List[str] = []
        patched = patching.patched_by(self.loadout)
        for layer in self.loadout.layers:
            if self.loadout.held(layer.origin):
                continue
            if layer.patches or patching.is_mine(self.loadout, layer):
                found.append(layer.origin)
            elif patching.without_a_project(self.loadout, layer):
                found.append(NEW_PROJECT + layer.origin)
            elif layer.origin not in patched:
                found.append(NEW_PATCH + layer.origin)
        return found

    def _target_text(self, origin: str) -> str:
        """How *Currently editing* names one of its entries: for a patch and
        the loadout's own mod, how far its changes reach (section 5.1)."""
        if origin == NEW_OWN_MOD:
            return tr("A new mod of your own…")
        if origin.startswith(NEW_PATCH):
            theirs = self.loadout.layer(origin[len(NEW_PATCH):])
            return tr("A new patch for %s…", theirs.label if theirs is not None else origin)
        if origin.startswith(NEW_PROJECT):
            mine = self.loadout.layer(origin[len(NEW_PROJECT):])
            return tr("Make %s a project of yours…", mine.label if mine is not None else origin)
        if is_own_mod(origin):
            if self.loadout.stands_for() == "":
                # "My Anno Mod" stays English: it is the name the mod is
                # exported under until somebody gives it another.
                return tr("%s  (yours)", self.loadout.own.name or "My Anno Mod")
            return tr("This loadout's own mod: only in this loadout")
        layer = self.loadout.layer(origin)
        if layer is None:
            return origin
        if layer.patches:
            return tr("Your patch for %s: wherever you use it",
                      patching.patched_names(self.loadout, layer))
        return layer.label

    def _on_target_changed(self) -> None:
        if self._loading_target:
            return
        if not self.settle_typed() or (
            self.settle_elsewhere is not None and not self.settle_elsewhere()
        ):
            # What was typed is for the mod it was typed into.
            self.refresh_target()
            return
        data = self.target_box.currentData()
        if isinstance(data, str) and data.startswith(NEW_PATCH):
            # Somebody else's mod with no patch of yours: picking it makes one.
            made = self.patch_wanted(data[len(NEW_PATCH):]) if self.patch_wanted is not None else None
            if made is None:
                self.refresh_target()
                return
            data = made
        elif isinstance(data, str) and data.startswith(NEW_PROJECT):
            # A mod of yours nothing keeps: picking it offers a project.
            made = (self.project_wanted(data[len(NEW_PROJECT):])
                    if self.project_wanted is not None else None)
            if made is None:
                self.refresh_target()
                return
            data = made
        if data == NEW_OWN_MOD:
            # Asked for, so made: from here on it is one of the loadout's mods.
            self.loadout.make_own_mod()
            data = ""
        self.loadout.editing = "" if data is None else str(data)
        # The tree is scoped to the mod being edited into, so it has to be read
        # again: what was on screen was a different document.
        if self.guid is not None:
            self.load_asset(self.guid)
        self.refresh_target()
        self.editingChanged.emit()
        self.targetPicked.emit(self.loadout.editing_origin())

    def _mark_item(self, item) -> None:
        """Repaint one row according to whether it currently has a pending edit.

        Repainting is not editing.  Each font, colour and tooltip set here
        tells the tree its item changed, and without the guard they came back
        through :meth:`_on_item_changed` as edits of their own: a first edit
        to a field went round three more times and rebuilt the changes list
        four times over -- 26 s, on a loadout of 400 mods.
        """
        if item.data(0, ORIG_ROLE) is None:
            return
        was, self._loading = self._loading, True
        try:
            self._paint_item(item)
        finally:
            self._loading = was

    def _paint_item(self, item) -> None:
        original = item.data(0, ORIG_ROLE)
        changed = item.text(1) != original
        font = item.font(1)
        font.setBold(changed)
        item.setFont(1, font)
        note, colour, value = item.data(0, NOTE_ROLE) or ("", theme.TEXT_DIM, "")
        item.setForeground(
            1, QBrush(QColor(theme.CHANGED if changed else value or theme.TEXT))
        )
        item.setText(3, original if changed else note)
        item.setForeground(3, QBrush(QColor(theme.TEXT_DIM if changed else colour)))
        self._note_beaten(item)
        name_font = item.font(0)
        name_font.setBold(changed)
        item.setFont(0, name_font)
        if changed:
            item.setToolTip(1, tr("Original value: %s", original) if original
                            else tr("Original value: (empty)"))
        else:
            item.setToolTip(1, "")

    def refresh_from_loadout(self) -> None:
        """Re-sync after changes were altered elsewhere (e.g. the dock)."""
        if self.guid is None:
            return
        if self.index is not None:
            # Structural changes alter the shape of the tree, not just values.
            self._reload_keeping_view()
            return
        self._loading = True
        for item in self._iter_items():
            original = item.data(0, ORIG_ROLE)
            if original is None:
                continue
            path = item.data(0, PATH_ROLE)
            change = (
                self.loadout.get(self.guid, path, origin=self.editing())
                if path
                else None
            )
            item.setText(1, change.new_value if change else original)
            self._mark_item(item)
            self._annotate_reference(item)
        self._loading = False
        self.revert_asset_button.setEnabled(bool(self.loadout.for_asset(self.guid, self.editing())))
        if self.only_changed.isChecked():
            self._apply_filter()

    def _iter_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            for i in range(item.childCount()):
                stack.append(item.child(i))

    def _revert_item(self, item) -> None:
        original = item.data(0, ORIG_ROLE)
        if original is None:
            return
        item.setText(1, original)  # triggers _on_item_changed, which drops the change

    def _own_creator(self, guid):
        """The change that creates ``guid``, when it belongs to the mod being
        written into -- a name is part of that mod's export."""
        finder = getattr(self.index, "creator_for", None)
        return finder(guid, self.editing()) if finder is not None and guid else None

    def _show_in_game_button(self, guid) -> None:
        creator = self._own_creator(guid)
        self.in_game_button.setVisible(bool(self.is_new_asset))
        self.in_game_button.setEnabled(creator is not None)
        if creator is not None:
            self.in_game_button.setToolTip(
                tr("Players see it as “%s”. The name is written to every "
                   "language's texts file on export.", creator.in_game_name)
                if creator.in_game_name else
                tr("Players see no name for it yet. A name is written to "
                   "every language's texts file on export.")
            )
        else:
            self.in_game_button.setToolTip(tr(
                "Another mod adds this asset, so that mod sets its name."
            ))

    def _name_in_game(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        creator = self._own_creator(self.guid)
        if creator is None:
            return
        name, ok = QInputDialog.getText(
            self,
            tr("In-game name"),
            tr("The name players see for this asset. It is written to every "
               "language's texts file on export.\n"
               "Leave it empty for no name."),
            text=creator.in_game_name,
        )
        if not ok or name.strip() == creator.in_game_name:
            return
        creator.in_game_name = name.strip()
        if creator.in_game_name:
            creator.payload = texts.link_name(creator.payload, self.guid, self.loadout.game)
        self.refresh_from_loadout()
        self.changesUpdated.emit()

    def _focus_asset(self) -> None:
        """Put the keyboard on the asset, in whichever view is showing.

        In the XML view that is the strip of its mods and not a pane: a pane
        that can be typed in keeps Ctrl+Z for its own text, and the keyboard is
        sent here so that the next Ctrl+Z is the mod's.
        """
        if self.view() != VIEW_XML:
            self.tree.setFocus()
        elif self.xml.strip.isVisible():
            self.xml.strip.setFocus()
        else:
            self.xml.setFocus()

    def _revert_asset(self) -> None:
        if self.guid is None:
            return
        # The button goes grey once nothing is left to revert, and Qt hands on
        # the keyboard of a control that greys out under it: to the search
        # box, where Ctrl+Z then undid text and not this.
        self._focus_asset()
        gone = self.loadout.for_asset(self.guid, self.editing())
        self.loadout.remove_asset(self.guid, self.editing())
        self.refresh_from_loadout()
        self.changesUpdated.emit()
        self.said.emit(self._reverted(gone))

    def _reverted(self, gone) -> str:
        """What Revert asset just took out, for the status bar.

        It asks nothing first, being one step to undo, and one click took out
        eighteen changes with nothing said: among them two assets the mod
        adds beside this one, which are filed under it and are not changes
        to it.
        """
        said = [trn(
            "Reverted %(n)s change to %(asset)s.",
            "Reverted %(n)s changes to %(asset)s.",
            len(gone), asset=self.asset_label,
        )]
        added = sum(1 for change in gone if change.creates_asset)
        if added:
            said.append(trn(
                "%(n)s of them adds a new asset beside it, which went too.",
                "%(n)s of them add new assets beside it, which went too.",
                added,
            ))
        # Translators: %s is the keys for Undo, e.g. "Ctrl+Z".
        said.append(tr("%s undoes this.", QKeySequence(
            QKeySequence.StandardKey.Undo
        ).toString(QKeySequence.SequenceFormat.NativeText)))
        return " ".join(said)

    # -- selection / filtering ---------------------------------------------
    def _copy_path(self) -> None:
        QGuiApplication.clipboard().setText(self.path_box.text())
        flash_button_feedback(self.copy_path_button)

    def _set_hint(self, text: str, warn: bool = False) -> None:
        """Show ``text`` below the tree, styled as a warning only when it is one.

        Selecting a container is routine, not a problem -- it should not read
        like one, the way a "Warn"-styled label would. Only the genuinely
        cautionary positional-path message earns that colour.
        """
        self.warning_label.setText(text)
        name = "Warn" if warn else "StepHint"
        if self.warning_label.objectName() != name:
            self.warning_label.setObjectName(name)
            theme.repolish(self.warning_label)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None or current.data(0, ELEM_ROLE) is None:
            self.path_box.clear()
            self._set_hint("")
            return
        path, positional = self._path_for(current)
        self.path_box.setText(path or "")
        if current.data(0, ORIG_ROLE) is None:
            self._set_hint(tr(
                "This is a container. Right-click it to add a field or a list entry, "
                "or pick one of its values to change it."
            ))
        elif positional:
            self._set_hint(
                tr("This value has no unique sibling key, so the path uses a "
                   "position index. It works, but can break if a game patch "
                   "reorders the list."),
                warn=True,
            )
        else:
            self._set_hint("")

    def _apply_filter(self) -> None:
        needle = self.filter_box.text().strip().lower()
        only_changed = self.only_changed.isChecked()
        show_empty = self.show_empty.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), needle, only_changed, show_empty)

    def _filter_item(self, item, needle: str, only_changed: bool, show_empty: bool) -> bool:
        """Hide non-matching rows but keep the ancestors of anything that matches."""
        child_match = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), needle, only_changed, show_empty):
                child_match = True

        is_leaf = item.data(0, ORIG_ROLE) is not None
        origin = item.data(0, ORIGIN_ROLE)
        # Added, removed, or living inside something this mod added: all of these
        # are things the mod did, whether or not they hold a value.
        mod_touched = origin is not None and (origin.is_mod_owned or origin.is_removed)

        self_match = True
        if needle:
            haystack = " ".join(
                (item.text(0), str(item.data(0, IDENT_ROLE) or ""), item.text(1), item.text(2))
            ).lower()
            self_match = needle in haystack
        if not show_empty and item.data(0, EMPTY_ROLE) and not child_match:
            # An empty leaf, or a container with nothing left showing inside it.
            # Not for nodes this mod added, though: a container is added empty on
            # purpose, and hiding it makes the add look like it did nothing.
            if not mod_touched:
                self_match = False
        if only_changed:
            # "Changed" is not just an edited value any more -- additions and
            # removals are changes too, and a container can be either.
            if is_leaf:
                edited = item.text(1) != item.data(0, ORIG_ROLE)
                self_match = self_match and (edited or mod_touched)
            else:
                self_match = self_match and mod_touched

        visible = child_match or self_match
        item.setHidden(not visible)
        if (needle or only_changed) and child_match:
            item.setExpanded(True)
        return visible

    def _collapse_to_sections(self) -> None:
        self.tree.collapseAll()
        self.tree.expandToDepth(0)

    def _toggle_fold(self) -> None:
        # Decided first: folding shuts rows, which says so as it goes.
        opening = not self._all_open
        if opening:
            self.tree.expandAll()
        else:
            self._collapse_to_sections()
        self._set_all_open(opening)

    def _set_all_open(self, shown: bool) -> None:
        """Whether every row is open: the fold button offers the other."""
        self._all_open = shown
        self.fold_button.setText(tr("Collapse all") if shown else tr("Expand all"))

    def _on_row_collapsed(self, _item) -> None:
        # A row shut by hand: Collapse all would no longer be all there was
        # left to do, and Expand all opens it again.
        if self._all_open and not self._loading:
            self._set_all_open(False)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = self._build_item_menu(item)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def show_references(self) -> None:
        if self.guid:
            self.referencesRequested.emit(self.guid)

    def populate_edit_menu(self, menu: QMenu) -> None:
        """Fill ``menu`` with actions for the row currently selected in the tree.

        Same menu the tree's own right-click offers for that row -- the Edit
        menu is just a way to reach it without a mouse over the right spot.
        """
        item = self.tree.currentItem()
        if item is None or item.isHidden():
            placeholder = QAction(tr("Select a field to edit…"), self)
            placeholder.setEnabled(False)
            menu.addAction(placeholder)
            return
        before = len(menu.actions())
        self._build_item_menu(item, menu=menu)
        if len(menu.actions()) == before:
            placeholder = QAction(tr("No actions for this selection"), self)
            placeholder.setEnabled(False)
            menu.addAction(placeholder)

    def _build_item_menu(self, item, menu: QMenu = None) -> QMenu:
        if menu is None:
            menu = QMenu(self)

        origin = self._origin(item)
        schema = self._schema()
        is_leaf = item.data(0, ORIG_ROLE) is not None
        if schema is not None and self._can_hold_children(item):
            spath = self._structural_path(item)
            kind = self._add_kind(item)
            if kind == "entry":
                entry = QAction(
                    tr("Add %s entry", schema.entry_tag_for(spath) or "Item"), self
                )
                entry.triggered.connect(lambda: self._add_list_entry(item))
                menu.addAction(entry)
            elif kind == "field":
                add = QAction(tr("Add field…"), self)
                add.triggered.connect(lambda: self._add_field(item))
                menu.addAction(add)
            else:
                # Better said here than in a menu item whose only job is to open
                # a dialog telling you there was nothing to pick.
                nothing = QAction(tr("No fields left to add"), self)
                nothing.setEnabled(False)
                menu.addAction(nothing)
            menu.addSeparator()

        if item.data(0, ELEM_ROLE) is not None:
            copy_block = QAction(
                _with_keys(tr("Copy block"), QKeySequence.StandardKey.Copy), self
            )
            copy_block.triggered.connect(lambda: self._copy_block(item))
            menu.addAction(copy_block)
            block, source = self._clipboard_block()
            if block is not None:
                how, why = self._paste_plan(item, block, source)
                paste = QAction(
                    _with_keys(tr("Paste <%s>", block.tag), QKeySequence.StandardKey.Paste),
                    self,
                )
                paste.setEnabled(bool(how))
                if not how:
                    paste.setToolTip(why)
                    menu.setToolTipsVisible(True)
                paste.triggered.connect(lambda: self._paste(item))
                menu.addAction(paste)
            menu.addSeparator()

        path, _pos = self._path_for(item)
        if path:
            act = QAction(tr("Copy ModOp path"), self)
            act.triggered.connect(lambda: QGuiApplication.clipboard().setText(path))
            menu.addAction(act)
            in_xml = QAction(tr("Show in XML view"), self)
            in_xml.triggered.connect(lambda: self._show_in_xml(item))
            menu.addAction(in_xml)
        if is_leaf:
            copy_value = QAction(tr("Copy value"), self)
            copy_value.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(item.text(1))
            )
            menu.addAction(copy_value)
            if item.flags() & Qt.ItemFlag.ItemIsEditable:
                edit = QAction(tr("Edit value"), self)
                edit.triggered.connect(lambda: self.tree.editItem(item, 1))
                menu.addAction(edit)
            if self._picks_an_asset(item):
                pick = QAction(tr("Choose asset…"), self)
                pick.triggered.connect(lambda: self._pick_asset(item))
                menu.addAction(pick)
            if self._is_reference_field(item):
                target = item.text(1).strip()
                show_refs = QAction(tr("References for %s…", item.text(2).split("  ·")[0]), self)
                show_refs.triggered.connect(
                    lambda: self.referencesRequested.emit(target)
                )
                menu.addAction(show_refs)
            if item.text(1) != item.data(0, ORIG_ROLE):
                revert = QAction(tr("Revert to original"), self)
                revert.triggered.connect(lambda: self._revert_item(item))
                menu.addAction(revert)

        elem = item.data(0, ELEM_ROLE)
        if elem is not None and not (_is_identity_guid(elem) and not self.is_new_asset):
            menu.addSeparator()
            if origin.is_removed:
                # Only a removal of the mod edited into can be taken back
                # here: what an earlier mod removed is not this mod's to undo.
                # By the rule the row's "removed by" is said by.
                remover = self._change_by_key(origin.change_key)
                if remover is None or remover.origin == self.editing():
                    undo = QAction(tr("Keep this after all"), self)
                    undo.triggered.connect(lambda: self._undo_remove(item, path))
                    menu.addAction(undo)
            elif origin.provenance == effective.PROV_ADDED:
                drop = QAction(tr("Remove this addition"), self)
                drop.triggered.connect(lambda: self._remove_node(item))
                menu.addAction(drop)
            elif origin.provenance == effective.PROV_SET and self._owned_here(origin):
                # The field is another mod's; what there is to take back is
                # this mod's value for it.
                drop = QAction(tr("Stop setting this"), self)
                drop.triggered.connect(lambda: self._remove_node(item))
                menu.addAction(drop)
            else:
                remove = QAction(tr("Remove from asset"), self)
                remove.triggered.connect(lambda: self._remove_node(item))
                menu.addAction(remove)
        return menu

    # -- the inline "+" ----------------------------------------------------
    def is_plus_hovered(self, index) -> bool:
        """Whether the cursor is on this row's "+", not merely on the row."""
        item = self.tree.itemFromIndex(index)
        return item is not None and item is self._plus_hover

    def _plus_item_at(self, point):
        """The item whose "+" is under ``point``, if any."""
        index = self.tree.indexAt(point)
        if not index.isValid() or index.column() != 0:
            return None
        item = self.tree.itemFromIndex(index)
        if item is None or not item.data(0, ADD_ROLE):
            return None
        if not plus_rect(self.tree.visualRect(index)).contains(point):
            return None
        return item

    def _set_plus_hover(self, item) -> None:
        if item is self._plus_hover:
            return
        self._plus_hover = item
        self.tree.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if item else Qt.CursorShape.ArrowCursor
        )
        self.tree.viewport().update()

    def eventFilter(self, watched, event):
        """Turn a click on the "+" glyph into an add, before the tree sees it."""
        if watched is self.tree.viewport():
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress and event.button() in (
                Qt.MouseButton.BackButton,
                Qt.MouseButton.ForwardButton,
            ):
                # Over the tree too, which would otherwise take the press as a
                # click on whatever row is under it.
                if event.button() == Qt.MouseButton.BackButton:
                    self.go_back()
                else:
                    self.go_forward()
                return True
            if kind in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseButtonRelease,
            ):
                # Working in the asset means the search results have served their
                # purpose; they overlay the tree, so they must not linger.
                self.search.close_results()
                item = self._plus_item_at(event.position().toPoint())
                if item is not None:
                    # Press and double-click are swallowed too, or the tree
                    # selects the row and starts an edit under the click.
                    if kind == QEvent.Type.MouseButtonRelease:
                        self._add_here(item)
                    return True
            elif kind == QEvent.Type.MouseMove:
                self._set_plus_hover(self._plus_item_at(event.position().toPoint()))
            elif kind == QEvent.Type.Leave:
                self._set_plus_hover(None)
        return super().eventFilter(watched, event)

    def _undo_remove(self, item, path: str) -> None:
        key = self._item_key(item)
        self.loadout.remove(self.guid, path, OP_REMOVE, self.editing())
        self.changesUpdated.emit()
        self._reload_keeping_view(focus_key=key)

    def focus_on_path(self, path: str) -> bool:
        """Select the row a given ModOp path points at (used by the changes dock).

        Returns whether anything was found, so a caller can say so rather than
        looking like the double-click did nothing.  Two things used to make it
        silently miss: a row hidden by the filters above the tree -- selecting
        a hidden row shows nothing at all -- and a structural op, whose path
        names the container a node was added to or removed from rather than a
        row that is still there.  So the filters are lifted and retried, and
        failing that the nearest ancestor is selected: the place it happened.
        """
        if self._select_path(path):
            return True
        if self.only_changed.isChecked() or self.filter_box.text().strip():
            self.only_changed.setChecked(False)
            self.filter_box.clear()
            self._apply_filter()
            if self._select_path(path):
                return True
        parent = path
        while "/" in parent:
            parent = parent.rsplit("/", 1)[0]
            if parent and self._select_path(parent):
                return True
        return False

    def _select_path(self, path: str) -> bool:
        """Select the visible row at exactly ``path``, if there is one."""
        if not path:
            return False
        for item in self._iter_items():
            if item.isHidden():
                continue
            item_path, _pos = self._path_for(item)
            if item_path == path:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return True
        return False
