"""What two loaded mods both reach for, and which of them the game will obey.

The game answers this silently: it applies every ModOp in load order and keeps
whatever was written last.  Nothing warns you that half of one mod is being
overwritten by another -- you find out by noticing a number in game that is not
the one the mod's page promised.

The list is by contested field rather than by mod, because that is the level at
which the answer is actionable: a pair of mods is rarely wholly incompatible,
and knowing that they disagree about exactly one building's cost is the
difference between dropping a mod and switching off one change.

Script mods can reach for the same thing too, in a way no field shows: two
modules under one name, or two snippets setting one global. Those are listed
beneath, when there are any -- and so are assets created under a GUID
something else has, of which the game can reach only one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import conflicts, repeats, scripts
from ..core.i18n import join_list, tr, tr_noop, tr_stored, trn
from . import theme
from .changes_dock import field_label
from .help_tip import HelpTip
from .widgets import (
    enter_activates,
    least_size,
    listing_table,
    open_script,
    sortable,
)

COLUMNS = (
    tr_noop("Asset"), tr_noop("Field"), tr_noop("Contested by"),
    tr_noop("What happens"),
)
SCRIPT_COLUMNS = (
    tr_noop("Name"), tr_noop("Is", context="a script name is a module or a global"),
    tr_noop("Shared by"), tr_noop("What happens"),
)
REPEAT_COLUMNS = (
    "GUID", tr_noop("Assets"), tr_noop("Created by"), tr_noop("What happens"),
)


class ConflictsDialog(QDialog):
    """Every field more than one loaded mod sets, and who wins it."""

    assetActivated = Signal(str, str)  # guid, path

    def __init__(self, loadout, parent=None, index=None, elsewhere=None):
        super().__init__(parent)
        self.loadout = loadout
        # The base game's, so a GUID a mod takes from it is said too.
        self.index = index
        # Which mods on this PC create each GUID (core.discovery.guid_owners),
        # as the changes panel has them: its button counts a GUID a mod not
        # in the loadout creates as well, and this lists what it counts
        # (H2-9).
        self.elsewhere = elsewhere or {}
        self.setWindowTitle(tr("Mods reaching for the same thing"))
        least_size(self, 980, 560)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        self.hint = HelpTip(tr(
            "The game applies mods in load order and keeps the value written "
            "last. These are the fields where that decides something. To "
            "settle one, switch off one of the two changes in the changes "
            "list. Double-click a row or press Enter to open the asset."
        ), self.windowTitle())

        self.summary = QLabel("")
        self.summary.setObjectName("SubHeading")
        head = QHBoxLayout()
        head.addWidget(self.summary, 1)
        head.addWidget(self.hint)
        outer.addLayout(head)

        self.table = listing_table([tr(column) for column in COLUMNS],
                                   tr("Fields set by more than one mod"))
        enter_activates(self.table)
        self.table.itemActivated.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        # What scripts share. Its own table: a module name is not an asset's
        # field, and nothing here opens in the asset editor.
        self.scripts_heading = QLabel(tr("Scripts sharing a name"))
        self.scripts_heading.setObjectName("SubHeading")
        self.scripts_hint = HelpTip(tr(
            "The modules and globals of every script mod share one space. "
            "The loader's documentation asks for unique names. These are the "
            "names that two loaded mods both use. Right-click a module to "
            "open either mod's file."
        ), self.scripts_heading.text())
        head = QHBoxLayout()
        head.addWidget(self.scripts_heading, 1)
        head.addWidget(self.scripts_hint)
        outer.addLayout(head)
        self.scripts_table = listing_table([
            tr(SCRIPT_COLUMNS[0]),
            tr(SCRIPT_COLUMNS[1], context="a script name is a module or a global"),
        ] + [tr(column) for column in SCRIPT_COLUMNS[2:]], self.scripts_heading.text())
        self.scripts_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scripts_table.customContextMenuRequested.connect(self._script_menu)
        self.scripts_table.setMaximumHeight(160)
        outer.addWidget(self.scripts_table)
        self.scripts_sorting = sortable(self.scripts_table)

        # Assets under one GUID. Not a field either: the game finds only the
        # first in the file, and whatever refers to that GUID gets that one.
        self.repeats_heading = QLabel(tr("Assets sharing a GUID"))
        self.repeats_heading.setObjectName("SubHeading")
        self.repeats_hint = HelpTip(tr(
            "The game finds an asset by its GUID. Where two assets share "
            "one, it finds only the first in the file. Every ModOp aimed at "
            "that GUID, and everything that refers to it, gets that asset. "
            "A GUID that a mod on this PC outside the loadout creates as well "
            "is listed too. Double-click a row or press Enter to open it."
        ), self.repeats_heading.text())
        head = QHBoxLayout()
        head.addWidget(self.repeats_heading, 1)
        head.addWidget(self.repeats_hint)
        outer.addLayout(head)
        self.repeats_table = listing_table([tr(column) for column in REPEAT_COLUMNS],
                                           self.repeats_heading.text())
        enter_activates(self.repeats_table)
        self.repeats_table.itemActivated.connect(self._open_repeat)
        self.repeats_table.setMaximumHeight(160)
        outer.addWidget(self.repeats_table)
        self.repeats_sorting = sortable(self.repeats_table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

    # -- filling in -----------------------------------------------------
    def _name(self, origin: str) -> str:
        return self.loadout.label_of(origin) or tr("your mod")

    def reload(self) -> None:
        self.clashes = conflicts.find(self.loadout)
        self.shared = scripts.clashes(self.loadout)
        self.repeated = repeats.find(self.loadout, self.index, self.elsewhere)
        self._fill_scripts()
        self._fill_repeats()
        real = [clash for clash in self.clashes if not clash.is_harmless]
        agreed = sum(1 for c in self.clashes if c.kind == conflicts.AGREES)
        meant = sum(1 for c in self.clashes if c.kind == conflicts.INTENDED)
        taken = repeats.summarise(self.repeated)
        # The panel's count, as its button says it, and the part of it that
        # only a mod outside the loadout takes as well: the overview card
        # counts what loads (repeats.playing), which is the rest (A11-32).
        apart = sum(1 for repeat in self.repeated if not repeat.harmless) - len(
            repeats.playing(self.repeated))
        if taken and apart:
            # Translators: "3 GUIDs taken twice", then how many of those a mod
            # on this PC that the loadout does not have takes as well.
            taken = trn("%(guids)s, %(n)s of them only with a mod on this PC outside the loadout",
                        "%(guids)s, %(n)s of them only with a mod on this PC outside the loadout",
                        apart, guids=taken)
        shared = "; ".join(bit for bit in (scripts.summarise(self.shared), taken) if bit)
        if not self.clashes and shared:
            self.summary.setText(
                tr("No two loaded mods reach for the same field, %s.", shared)
            )
        elif not self.clashes:
            self.summary.setText(tr("No two loaded mods reach for the same field."))
        else:
            text = trn("%(n)s field contested", "%(n)s fields contested", len(real))
            extra = []
            if agreed:
                extra.append(trn("%(n)s where both mods want the same value",
                                 "%(n)s where both mods want the same value", agreed))
            if meant:
                # Listed, because which mods touch a value is worth knowing --
                # but not counted as contested, because a mod that names another
                # in its load order knew it was there.
                extra.append(trn("%(n)s where the later mod means to win",
                                 "%(n)s where the later mod means to win", meant))
            if extra:
                # Translators: how many fields are contested, then how many
                # more are shared harmlessly.
                text = tr("%(contested)s, %(others)s", contested=text,
                          others=join_list(extra))
            if shared:
                # Translators: the fields contested, then what scripts and
                # assets share: "3 fields contested; 2 script names shared".
                text = tr("%(fields)s, %(shared)s", fields=text, shared=shared)
            self.summary.setText(text)

        # With nothing but scripts shared, an empty table of fields and the
        # hint about switching changes off only stand in the way of them.
        fields = bool(self.clashes) or not (self.shared or self.repeated)
        self.table.setVisible(fields)
        self.hint.setVisible(fields)
        self.scripts_table.setMaximumHeight(160 if fields or self.repeated else 16777215)
        self.repeats_table.setMaximumHeight(160 if fields else 16777215)
        self.table.setRowCount(len(self.clashes))
        for row, clash in enumerate(self.clashes):
            asset = QTableWidgetItem(tr_stored(clash.label))
            asset.setToolTip(tr("%(asset)s\nGUID %(guid)s", asset=tr_stored(clash.label),
                                guid=clash.guid))
            asset.setData(Qt.ItemDataRole.UserRole, (clash.guid, clash.path))
            self.table.setItem(row, 0, asset)

            field = QTableWidgetItem(field_label(clash.path))
            field.setToolTip(clash.path)
            self.table.setItem(row, 1, field)

            mods = QTableWidgetItem(
                " → ".join(self._name(origin) for origin in clash.origins)
            )
            mods.setToolTip(tr(
                "In load order. The game reads the last one.\n%s",
                "\n".join(self._name(origin) for origin in clash.origins),
            ))
            self.table.setItem(row, 2, mods)

            what = QTableWidgetItem(clash.describe(self._name))
            if clash.is_harmless:
                what.setForeground(QColor(theme.TEXT_DIM))
            elif clash.kind == conflicts.REMOVES:
                theme.mark(what, "error")
            else:
                theme.mark(what, "warn")
            self.table.setItem(row, 3, what)
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 240)

    def _fill_scripts(self) -> None:
        shown = bool(self.shared)
        for widget in (self.scripts_heading, self.scripts_hint, self.scripts_table):
            widget.setVisible(shown)
        self.scripts_table.setRowCount(len(self.shared))
        for row, clash in enumerate(self.shared):
            module = clash.kind == scripts.MODULE
            name = QTableWidgetItem("%s.lua" % clash.name if module else clash.name)
            name.setData(Qt.ItemDataRole.UserRole, row)
            self.scripts_table.setItem(row, 0, name)
            self.scripts_table.setItem(row, 1, QTableWidgetItem(
                # Translators: what a shared script name is -- a Lua module or
                # a Lua global variable.
                tr("module", context="script") if module else tr("global", context="script")
            ))
            mods = QTableWidgetItem(" → ".join(self._name(origin) for origin in clash.origins))
            lines = []
            for origin, where in zip(clash.origins, clash.where, strict=False):
                if not module:
                    # Translators: %(hook)s is the loader's hook that sets
                    # the global: Init, Load, Unload or Tick.
                    lines.append(tr("%(mod)s: set in its %(hook)s",
                                    mod=self._name(origin), hook=where))
                elif where:
                    lines.append("%s: %s" % (self._name(origin), where))
                else:
                    lines.append(tr("%s: its file is not on disk", self._name(origin)))
            mods.setToolTip(tr("In load order.\n%s", "\n".join(lines)))
            self.scripts_table.setItem(row, 2, mods)
            what = QTableWidgetItem(clash.describe(self._name))
            what.setToolTip(what.text())
            theme.mark(what, "text-dim" if clash.is_harmless else "warn")
            self.scripts_table.setItem(row, 3, what)
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.scripts_sorting.apply()
        header = self.scripts_table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.scripts_table.setColumnWidth(0, 220)
        self.scripts_table.setColumnWidth(1, 70)
        self.scripts_table.setColumnWidth(2, 240)

    def _fill_repeats(self) -> None:
        shown = bool(self.repeated)
        for widget in (self.repeats_heading, self.repeats_hint, self.repeats_table):
            widget.setVisible(shown)
        self.repeats_table.setRowCount(len(self.repeated))
        for row, repeat in enumerate(self.repeated):
            guid = QTableWidgetItem(repeat.guid)
            guid.setData(Qt.ItemDataRole.UserRole, repeat.guid)
            self.repeats_table.setItem(row, 0, guid)
            names = ([tr("%s (base game)", repeat.shipped)] if repeat.shipped else []) + [
                copy.name for copy in repeat.copies
            ]
            assets = QTableWidgetItem(" / ".join(names))
            assets.setToolTip("\n".join(names))
            self.repeats_table.setItem(row, 1, assets)
            who = " → ".join(self._name(origin) for origin in repeat.origins)
            if repeat.elsewhere:
                who = tr(", ", context="between items of a list").join([
                    who, tr("and on this PC by %s", join_list(list(repeat.elsewhere))),
                ])
            mods = QTableWidgetItem(who)
            mods.setToolTip(tr("In the order they stand in the file.\n%s", "\n".join(
                "%s: %s" % (self._name(copy.origin), copy.name) for copy in repeat.copies
            )))
            self.repeats_table.setItem(row, 2, mods)
            if repeat.elsewhere and len(repeat.copies) == 1 and not repeat.shipped:
                said = tr("made by a mod not in this loadout as well: if both "
                          "load, the game finds only one of them")
            elif repeat.tidied:
                said = tr("a mod removes all but the last, and the game finds %s", repeat.found)
            elif repeat.same:
                said = tr("the same asset each time, so no harm done")
            elif repeat.apart:
                said = repeats.said_apart()
            else:
                said = tr("the game finds only %s", repeat.found)
            what = QTableWidgetItem(said)
            what.setToolTip(said)
            theme.mark(what, "text-dim" if repeat.harmless else "warn")
            self.repeats_table.setItem(row, 3, what)
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.repeats_sorting.apply()
        header = self.repeats_table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.repeats_table.setColumnWidth(0, 110)
        self.repeats_table.setColumnWidth(1, 320)
        self.repeats_table.setColumnWidth(2, 240)

    def _open_repeat(self, item) -> None:
        first = self.repeats_table.item(item.row(), 0)
        guid = first.data(Qt.ItemDataRole.UserRole) if first is not None else None
        if guid:
            self.assetActivated.emit(guid, "")
            self.accept()

    def _script_menu(self, point) -> None:
        item = self.scripts_table.itemAt(point)
        if item is None:
            return
        self.scripts_table.selectRow(item.row())
        menu = self.script_menu_for(item.row())
        menu.exec(self.scripts_table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def script_menu_for(self, row: int) -> QMenu:
        """Each mod's file under a shared module name, to open and compare --
        in whatever the system opens Lua with, as everywhere else."""
        clash = self.shared[row]
        menu = QMenu(self)
        if clash.kind == scripts.MODULE:
            for origin, where in zip(clash.origins, clash.where, strict=False):
                action = menu.addAction(
                    tr("Open %(mod)s's %(module)s.lua", mod=self._name(origin),
                       module=clash.name),
                    lambda where=where, origin=origin: open_script(
                        where, scripts.folder_of(self.loadout, origin)),
                )
                action.setEnabled(scripts.may_open(where, scripts.folder_of(self.loadout, origin)))
            menu.addSeparator()
        menu.addAction(
            tr("Copy name"),
            lambda: QGuiApplication.clipboard().setText(clash.name),
        )
        return menu

    def _on_double_click(self, item) -> None:
        self._open(item.row())

    def _open(self, row: int) -> None:
        first = self.table.item(row, 0)
        found = first.data(Qt.ItemDataRole.UserRole) if first is not None else None
        if found:
            self.assetActivated.emit(found[0], found[1])
            self.accept()

    # -- what a contested field offers -------------------------------------
    def _context_menu(self, point) -> None:
        item = self.table.itemAt(point)
        if item is None:
            return
        self.table.selectRow(item.row())
        menu = self.menu_for(item.row())
        menu.exec(self.table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, row: int) -> QMenu:
        """What can be done about one contested field.

        Opening the asset is what a double-click does; the hint says so, and
        this says it where somebody looking for what a row can do would find
        it.  The path is the other half: a clash is something you go and tell
        the two mods' authors about, and the field it is over is the whole
        content of that message -- and it is not on screen in full, because
        the column shows the field's tail and keeps the rest for the tooltip.
        """
        first = self.table.item(row, 0)
        found = first.data(Qt.ItemDataRole.UserRole) if first is not None else None
        menu = QMenu(self)
        open_it = menu.addAction(
            tr("Open in asset editor"), lambda: self._open(row)
        )
        open_it.setEnabled(bool(found))
        menu.addSeparator()
        menu.addAction(
            tr("Copy field path"),
            lambda: QGuiApplication.clipboard().setText(found[1] if found else ""),
        )
        menu.addAction(
            tr("Copy GUID"),
            lambda: QGuiApplication.clipboard().setText(found[0] if found else ""),
        )
        return menu
