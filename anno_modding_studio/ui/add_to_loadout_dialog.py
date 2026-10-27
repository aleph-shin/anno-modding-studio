"""Pick which of your other loadouts have a mod of yours.

The Export tab's *Add to loadout…* (docs/proposals/export-tab-proposal.md,
section 5.2).  A dialog, not a menu: the list can be long, and each line
says something -- which loadout the game loads, and for a patch, which of
the mods it needs a loadout lacks.  This only picks; adding and taking out
are the Export tab's (``OutputPanel.change_loadouts``).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, NamedTuple, Set

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

from ..core.i18n import join_list, tr, tr_noop
from .help_tip import HelpTip
from .widgets import single_line, tab_as_laid_out

COLUMNS = (tr_noop("Loadout"), tr_noop("Note"))


class Row(NamedTuple):
    """One of this game's other loadouts: its file and name, whether it has
    the mod, whether the game loads it, the mods it lacks that the mod
    needs, by name, and whether it has the mod from its folder, kept by no
    project: ticking it then takes the project up there."""

    path: Path
    name: str
    has: bool
    live: bool
    lacks: List[str]
    from_folder: bool = False


class AddToLoadoutDialog(QDialog):
    """A checklist of loadouts; :meth:`ticked` is the ones ticked.

    ``offer_play_now``: none of the others is a set of mods you play, so
    *What I play now…* is offered, which makes one with the mod in it
    (docs/proposals/export-tab-proposal.md, section 5.7).  The dialog then
    ends with :attr:`PLAY_NOW` where that is pressed."""

    PLAY_NOW = 2

    def __init__(self, mod: str, rows: List[Row], parent=None, offer_play_now: bool = False):
        super().__init__(parent)
        self.rows = list(rows)
        self.setWindowTitle(tr("Add to loadout"))
        self.setMinimumSize(520, 360)
        self.setSizeGripEnabled(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)
        head = QHBoxLayout()
        heading = QLabel(tr("&Add %s to:", mod))
        heading.setWordWrap(True)
        head.addWidget(heading, 1)
        head.addWidget(HelpTip(tr(
            "Each loadout ticked has the mod: it reads it the next time it is "
            "opened. The one the game loads now gets it at once, and nothing "
            "else in it is switched. A loadout unticked no longer has it. The "
            "mod itself is kept. This loadout is saved first, and the mod "
            "built into the Studio's library."
        ), self.windowTitle()))
        outer.addLayout(head)
        self.table = QTableWidget(len(self.rows), len(COLUMNS))
        self.table.setTabKeyNavigation(False)
        single_line(self.table)
        self.table.setHorizontalHeaderLabels([tr(column) for column in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAccessibleName(tr("Your loadouts"))
        for at, row in enumerate(self.rows):
            item = QTableWidgetItem(row.name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.has else Qt.CheckState.Unchecked)
            self.table.setItem(at, 0, item)
            self.table.setItem(at, 1, QTableWidgetItem(self.note_of(row)))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        heading.setBuddy(self.table)
        outer.addWidget(self.table, 1)
        if offer_play_now:
            none = QLabel(tr("None of your loadouts is a set of mods you play."))
            none.setObjectName("StepHint")
            none.setWordWrap(True)
            outer.addWidget(none)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.play_now = None
        if offer_play_now:
            # Translators: a button of the Add to loadout dialog, the same
            # words as the Start tab's: a loadout of the mods the game loads
            # now, with this mod added to it.
            self.play_now = self.buttons.addButton(tr("&What I play now…"),
                                                   QDialogButtonBox.ButtonRole.ActionRole)
            self.play_now.setToolTip(tr("A loadout of what the game loads now, with "
                                        "this mod added."))
            self.play_now.clicked.connect(lambda: self.done(self.PLAY_NOW))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    @staticmethod
    def note_of(row: Row) -> str:
        said = []
        if row.live:
            said.append(tr("what the game loads now"))
        if row.lacks and not row.has:
            said.append(tr("brings %s with it", join_list(row.lacks)))
        if row.from_folder and not row.has:
            said.append(tr("has it from its folder, and reads it from its project once ticked"))
        return "; ".join(said)

    def tick(self, path, on: bool = True) -> None:
        """Tick the row of the loadout at ``path``, or untick it."""
        for at, row in enumerate(self.rows):
            if row.path == Path(path):
                self.table.item(at, 0).setCheckState(
                    Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )

    def ticked(self) -> Set[Path]:
        return {
            row.path for at, row in enumerate(self.rows)
            if self.table.item(at, 0).checkState() == Qt.CheckState.Checked
        }
