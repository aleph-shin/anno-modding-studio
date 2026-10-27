"""Put mods back the way they were before the Studio wrote over them.

What the Studio exports is its own reading of a mod, and a reading of a mod
nobody has tested it against can be subtly wrong.  This is the way back: every
export first copies what it is about to overwrite, and this is where those copies
are.  See :mod:`core.backup` for why the copy is small enough to keep several of.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ..core import backup
from ..core.i18n import bullets, date_text, number, tr, trn
from .help_tip import BACKUPS_HELP, HelpTip
from .widgets import ask, tab_as_laid_out, wrappable_path


class BackupDialog(QDialog):
    """The saved copies for one mods folder, and a way to put one back."""

    def __init__(self, mods_folder, parent=None, into=None):
        super().__init__(parent)
        self.mods_folder = Path(mods_folder)
        self.into = into
        self.restored = 0
        self.setWindowTitle(tr("Restore mods from a backup"))
        self.setMinimumSize(680, 420)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title says what this is. Which files a backup holds, and how
        # long it is kept, is behind the ? beside the folder they belong to.
        where = QLabel(str(self.mods_folder))
        where.setObjectName("SubHeading")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head = QHBoxLayout()
        head.addWidget(where, 1)
        head.addWidget(HelpTip(tr(BACKUPS_HELP), self.windowTitle()))
        outer.addLayout(head)

        self.list = QListWidget()
        self.list.setAccessibleName(tr("Backups"))
        self.list.currentRowChanged.connect(self._refresh_button)
        self.list.itemSelectionChanged.connect(self._show_detail)
        outer.addWidget(self.list, 1)

        self.detail = QLabel("")
        self.detail.setObjectName("StepHint")
        self.detail.setWordWrap(True)
        outer.addWidget(self.detail)

        row = QHBoxLayout()
        row.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.restore_button = self.buttons.addButton(
            tr("&Restore these files"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.restore_button.setObjectName("Primary")
        self.restore_button.setEnabled(False)
        self.buttons.accepted.connect(self._restore)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        row.addWidget(self.buttons)
        outer.addLayout(row)

    def _reload(self) -> None:
        self.saves = backup.saves(self.mods_folder, into=self.into)
        self.list.clear()
        for kept in self.saves:
            item = QListWidgetItem(kept.describe())
            item.setToolTip("\n".join(kept.mods[:30]))
            self.list.addItem(item)
        if not self.saves:
            self.detail.setText(tr(
                "No copies are saved for this folder yet. One is made the "
                "first time an export overwrites a mod that is already "
                "there."
            ))
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _current(self):
        row = self.list.currentRow()
        return self.saves[row] if 0 <= row < len(self.saves) else None

    @staticmethod
    def _restorable(kept) -> list:
        """The save's files restoring would write: not those of a mod that is
        in the library now (see :func:`core.backup.restore`)."""
        linked = set(backup.linked_now(kept))
        return [entry for entry in kept.files if entry.get("mod") not in linked]

    def _refresh_button(self) -> None:
        kept = self._current()
        self.restore_button.setEnabled(
            kept is not None and not kept.refusal and bool(self._restorable(kept))
        )

    def _show_detail(self) -> None:
        kept = self._current()
        if kept is None:
            return
        if kept.refusal:
            self.detail.setText(kept.refusal)
            return
        text = trn(
            "%(n)s file, %(size)s MB, from %(folder)s.  Restoring overwrites "
            "that file and nothing else.",
            "%(n)s files, %(size)s MB, from %(folder)s.  Restoring overwrites "
            "those files and nothing else.",
            len(kept.files), size=number(kept.bytes / 1e6, 1),
            folder=wrappable_path(kept.mods_folder),
        )
        linked = backup.linked_now(kept)
        if linked and not self._restorable(kept):
            text += "\n\n" + tr(
                "Every mod in it is in the library now, so there is nothing "
                "here to restore. Undoing the conversion to the library, in "
                "the Library menu, moves them back and restores their files."
            )
        elif linked:
            text += "\n\n" + trn(
                "%(n)s of its mods is in the library now and is left as it "
                "is. Undoing the conversion to the library, in the Library "
                "menu, moves it back and restores its files.",
                "%(n)s of its mods are in the library now and are left as "
                "they are. Undoing the conversion to the library, in the "
                "Library menu, moves them back and restores their files.",
                len(linked),
            )
        self.detail.setText(text)

    def _restore(self) -> None:
        kept = self._current()
        if kept is None:
            return
        restorable = self._restorable(kept)
        answer = ask(
            self,
            tr("Restore these files?"),
            trn("Put %(n)s file back into:\n"
                "%(folder)s\n"
                "\n"
                "This overwrites whatever the Studio wrote there since "
                "%(when)s. Other files in the folder are not changed.",
                "Put %(n)s files back into:\n"
                "%(folder)s\n"
                "\n"
                "This overwrites whatever the Studio wrote there since "
                "%(when)s. Other files in the folder are not changed.",
                len(restorable), folder=wrappable_path(kept.mods_folder),
                when=date_text(kept.when, clock=True)),
            tr("&Restore"),
            default_action=False,
        )
        if not answer:
            return
        put, failed = backup.restore(kept)
        self.restored = put
        if failed:
            QMessageBox.warning(
                self,
                tr("Some files could not be restored"),
                bullets(failed, most=12),
            )
        if not put:
            # Nothing went back, which the warning has said why: "the mods
            # are as they were" would be the one thing not so.
            return
        QMessageBox.information(
            self,
            tr("Restored"),
            trn("Put %(n)s file back.\n"
                "\n"
                "The mods are as they were. Read the folder again to bring "
                "that into this loadout.",
                "Put %(n)s files back.\n"
                "\n"
                "The mods are as they were. Read the folder again to bring "
                "that into this loadout.", put),
        )
        self.accept()
