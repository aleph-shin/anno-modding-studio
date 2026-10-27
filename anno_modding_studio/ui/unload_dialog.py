"""Pick which loaded mods to let go of.

Reading a mod in is cheap to undo -- read it again -- but a change *you* made to
it exists nowhere else, so unloading is one of the few places in the app that
can throw work away.  This only ever picks which mods; whether that is safe,
and what to do if it is not, is for the caller (see ``MainWindow.unload_mods``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.i18n import number, tr, tr_noop, trn
from . import theme
from .help_tip import HelpTip
from .widgets import (
    filter_box,
    single_line,
    sort_as,
    sortable,
    tab_as_laid_out,
    tick_shown,
)

COLUMNS = ("", tr_noop("Mod"), tr_noop("Changes"), tr_noop("Your edits"))


class UnloadModsDialog(QDialog):
    """A checklist of loaded mods; ``chosen`` holds the ones ticked on accept."""

    def __init__(self, loadout, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.chosen = []
        self.setWindowTitle(tr("Unload mods"))
        self.setMinimumSize(560, 420)
        # A list of a few hundred mods wants room to grow into.
        self.setSizeGripEnabled(True)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title says what this does. What is lost stays in view, because
        # it cannot be undone. The rest is behind the ?.
        head = QHBoxLayout()
        hint = QLabel(tr(
            "Edits you made here to a mod you unload are lost. They exist only "
            "in this loadout."
        ))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        head.addWidget(HelpTip(tr(
            "Unloading removes a loaded mod's group from the changes list, its "
            "place in load order, and the folder it is exported to. You can "
            "read the mod in again at any time."
        ), self.windowTitle()))
        outer.addLayout(head)

        # A loadout holds hundreds of mods, and type-ahead only finds a row
        # by the start of its name.
        self.filter_box = filter_box(tr("Filter mods…"), tr("Filter mods"), self._filter)
        outer.addWidget(self.filter_box)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setTabKeyNavigation(False)
        # One line per row, so a name too long for its column is cut where
        # it runs out rather than losing its last word whole.
        single_line(self.table)
        self.table.setHorizontalHeaderLabels(
            [tr(column) if column else "" for column in COLUMNS]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setAccessibleName(tr("Loaded mods"))
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        # Not the mod of yours the loadout carries on with: that mod is the
        # loadout, and without it "Currently editing" names a stand-in.
        origins = [
            layer.origin for layer in self.loadout.layers
            if layer.origin != self.loadout.home
        ]
        self.table.setRowCount(len(origins))
        for row, origin in enumerate(origins):
            layer = self.loadout.layer(origin)
            tick = QTableWidgetItem()
            tick.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            tick.setCheckState(Qt.CheckState.Unchecked)
            tick.setData(Qt.ItemDataRole.UserRole, origin)
            self.table.setItem(row, 0, tick)

            name = QTableWidgetItem(layer.label if layer else origin)
            self.table.setItem(row, 1, name)

            total = len(self.loadout.changes_of(origin))
            self.table.setItem(row, 2, _number(total))

            mine = len(self.loadout.authored_of(origin))
            edits = _number(mine)
            tips = []
            if mine:
                theme.mark(edits, "warn")
                tips.append(trn(
                    "%(n)s change here exists nowhere but this loadout.",
                    "%(n)s changes here exist nowhere but this loadout.", mine,
                ))
            # Your changes to somebody else's mod are in its patch, which
            # unloading the mod keeps: counted here, or the mod you changed
            # read as one you never touched (H2-4).
            patch = self.loadout.patch_for(origin)
            in_patch = len(self.loadout.changes_of(patch.origin)) if patch is not None else 0
            if in_patch:
                edits.setText(trn(
                    "%(mine)s, and %(n)s in its patch",
                    "%(mine)s, and %(n)s in its patch",
                    in_patch, mine=number(mine),
                ))
                sort_as(edits, mine + in_patch)
                tips.append(trn(
                    "%(n)s change of yours to it is in its patch, %(patch)s. "
                    "Unloading the mod keeps the patch, greyed and left out of "
                    "exports and Play until the mod is loaded again.",
                    "%(n)s changes of yours to it are in its patch, %(patch)s. "
                    "Unloading the mod keeps the patch, greyed and left out of "
                    "exports and Play until the mod is loaded again.",
                    in_patch, patch=patch.label,
                ))
            edits.setToolTip("\n\n".join(tips))
            self.table.setItem(row, 3, edits)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(lambda _item: self._refresh_button())
        self.tick_box = tick_shown(self.table, tr("Tick every mod shown"), self._refresh_button)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.unload_button = self.buttons.addButton(
            tr("&Unload"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.unload_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _filter(self, text: str) -> None:
        """Show the rows whose mod holds every word typed.  A hidden row
        keeps its tick: the filter narrows the list, not the choice."""
        wanted = text.casefold().split()
        for row in range(self.table.rowCount()):
            tick, name = self.table.item(row, 0), self.table.item(row, 1)
            words = " ".join((
                name.text() if name is not None else "",
                str(tick.data(Qt.ItemDataRole.UserRole) or "") if tick is not None else "",
            )).casefold()
            self.table.setRowHidden(row, not all(word in words for word in wanted))
        self.tick_box.sync()

    def _ticked(self) -> list:
        found = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                found.append(item.data(Qt.ItemDataRole.UserRole))
        return found

    def _refresh_button(self) -> None:
        count = len(self._ticked())
        self.unload_button.setEnabled(bool(count))
        self.unload_button.setText(
            trn("&Unload %(n)s mod", "&Unload %(n)s mods", count)
            if count
            else tr("&Unload")
        )

    def _accept(self) -> None:
        self.chosen = self._ticked()
        self.accept()


def _number(value: int) -> QTableWidgetItem:
    item = sort_as(QTableWidgetItem(number(value)), value)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item
