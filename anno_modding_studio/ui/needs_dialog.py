"""What the mods that load say about each other, one row a finding.

The card under Play says how many there are of each kind; on a big loadout
the sentences themselves were most of it. They are here instead, as a table
that sorts by either mod: one replaced, one needing another that will not
load, one loading beside a mod it says it does not work with.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.i18n import tr, tr_noop
from . import theme
from .help_tip import HelpTip
from .widgets import listing_table, sortable

COLUMNS = (tr_noop("Mod"), tr_noop("The other mod"), tr_noop("What happens"))


def rows(check) -> list:
    """``(mod, the other mod, what happens)`` for each finding in ``check``
    (a :class:`core.dependencies.Check`), in the card's order."""
    found = []
    for old, new in check.replaced:
        found.append((old, new, tr("will not load: the other mod replaces it")))
    for mod, needs, off in check.missing:
        found.append((mod, needs, tr("needs the other mod, which is switched off") if off
                      else tr("needs the other mod, which is not installed")))
    for mod, other in check.clashes:
        found.append((mod, other, tr("does not work with the other mod, and both load")))
    return found


class NeedsDialog(QDialog):
    """Every mod that will not load, needs one that will not, or clashes."""

    def __init__(self, check, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("What these mods say about each other"))
        self.setMinimumSize(980, 440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # Translators: how many mods each kind of finding is about, as a
        # list: "2 mods need one that will not load and 1 mod loads beside
        # one it does not work with."
        summary = QLabel(tr("%s.", check.counted()))
        summary.setObjectName("SubHeading")
        summary.setWordWrap(True)
        head = QHBoxLayout()
        head.addWidget(summary, 1)
        head.addWidget(HelpTip(tr(
            "Each mod's modinfo can name mods it needs, mods it does not work "
            "with, and mods it replaces. These are what the mods that load "
            "say of each other. The game's loader does not load a mod that "
            "another replaces, and it reports a missing one in its log only "
            "once the game has started."
        ), self.windowTitle()), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        found = rows(check)
        self.table = listing_table([tr(column) for column in COLUMNS], self.windowTitle())
        self.table.setRowCount(len(found))
        for row, said in enumerate(found):
            for column, text in enumerate(said):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 2:
                    theme.mark(item, "warn")
                self.table.setItem(row, column, item)
        header = self.table.horizontalHeader()
        for column in range(2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 290)
        self.table.setColumnWidth(1, 290)
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        # What the Studio cannot tell, below the list it qualifies.
        note = QLabel(check.note())
        note.setObjectName("StepHint")
        note.setWordWrap(True)
        note.setVisible(bool(note.text()))
        outer.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
