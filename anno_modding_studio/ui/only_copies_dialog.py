"""The library's copies that could not be had again, were the library lost.

Most of what the library holds has a second source: mod.io for a mod it was
downloaded from or has since been matched to, the loadout for a mod Play
built.  What is left is a zip nobody matched to anything and a folder moved
in from the mods folder -- for those the library holds the only copy the
Studio knows of, and this is where it says which they are (see
:func:`core.library.only_copies`).

A zip is kept with the backups as it is installed, so its copy has a second
source from the start.  A folder is backed up here, whole and as a zip, when
**Back up** is pressed: the large part of the backups is only ever made
because somebody asked for it.  Either zip is had back the way any mod's zip
is, by dropping it on the window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core import disk, library, modfolder
from ..core.i18n import date_text, size_text, tr, trn
from .help_tip import HelpTip
from .widgets import (
    ask,
    enter_activates,
    heading_row,
    open_on_disk,
    show_progress,
    tab_as_laid_out,
    wrappable_path,
)
from .workers import LibraryBackupWorker

# One game's: its name, its only copies, the copies nothing is recorded
# about, and the ones of those kept as a zip with the backups.
Group = Tuple[str, List[library.Held], List[library.Held], List[library.Held]]

FOLDER_ROLE = Qt.ItemDataRole.UserRole
MOD_ROLE = Qt.ItemDataRole.UserRole + 1


def arrived(mod: library.Held) -> str:
    """How an only copy came to be in the library, for its row."""
    if mod.source == library.ZIP:
        return tr("Unpacked from %s", mod.zip) if mod.zip else tr("Unpacked from a zip")
    if mod.source == library.ADOPTED:
        return tr("Moved in from your mods folder")
    return tr("Not recorded")


class OnlyCopiesDialog(QDialog):
    """``groups`` as :data:`Group`; ``regroup`` gives them again after a
    backup, which moves the mods backed up under their own heading."""

    def __init__(self, groups: List[Group], parent=None,
                 regroup: Optional[Callable[[], List[Group]]] = None):
        super().__init__(parent)
        self._regroup = regroup
        self._worker: Optional[LibraryBackupWorker] = None
        self.setWindowTitle(tr("Only copies in the library"))
        self.resize(760, 480)
        layout = QVBoxLayout(self)

        # What the first group means stays in view. Why the second is there
        # is behind the ?.
        head = QHBoxLayout()
        intro = QLabel(tr(
            "The mods under Only copies are in the library and nowhere else. "
            "If the library is lost, they can't be downloaded again."
        ))
        intro.setWordWrap(True)
        head.addWidget(intro, 1)
        self.help = HelpTip(tr(
            "The mods under Not recorded came into the library before the "
            "Studio wrote down where a mod came from. Some may be on mod.io. "
            "A mod leaves this list when a check for updates finds it there."
        ), self.windowTitle())
        head.addWidget(self.help)
        layout.addLayout(head)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(tr("Only copies in the library"))
        self.tree.setHeaderLabels(
            [tr("Mod"), tr("Version"), tr("How it arrived"), tr("Backed up")]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._sync)
        self.tree.currentItemChanged.connect(lambda *_: self._sync())
        self.tree.itemActivated.connect(lambda *_: self._open())
        enter_activates(self.tree)
        layout.addWidget(self.tree, 1)

        self.status = QLabel("")
        self.status.setObjectName("StepHint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("&Close"))
        self.backup_button = buttons.addButton(
            tr("&Back up"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.backup_button.clicked.connect(self._back_up)
        self.open_button = buttons.addButton(
            tr("&Open folder"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.open_button.clicked.connect(self._open)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        layout.addWidget(buttons)
        self._fill(groups)

    # -- the list ------------------------------------------------------------------
    def _fill(self, groups: List[Group]) -> None:
        """The rows, and the only copies chosen: what a backup is for."""
        self.tree.clear()
        chosen = []
        for game, only, unknown, kept in groups:
            top = heading_row(self.tree, game)
            for title, mods in ((tr("Only copies"), only), (tr("Not recorded"), unknown),
                                (tr("Backed up"), kept)):
                if not mods:
                    continue
                under = heading_row(top, title)
                for mod in mods:
                    row = QTreeWidgetItem(under, [
                        mod.mod_name or modfolder.bare_name(mod.name), mod.version,
                        # Under its own heading, a copy nothing is known of
                        # says nothing more.
                        arrived(mod) if mod.source else "",
                        _backed_up_on(mod.backup),
                    ])
                    row.setData(0, FOLDER_ROLE, str(mod.target))
                    row.setData(0, MOD_ROLE, mod)
                    row.setToolTip(0, str(mod.target))
                    if mods is only:
                        chosen.append(row)
        self.tree.expandAll()
        for row in chosen:
            row.setSelected(True)
        self._sync()

    def _rows(self) -> List[QTreeWidgetItem]:
        """The mods chosen: each row selected, and every row under a
        heading selected."""
        found: List[QTreeWidgetItem] = []

        def take(item) -> None:
            if item.data(0, MOD_ROLE) is not None and item not in found:
                found.append(item)
            for index in range(item.childCount()):
                take(item.child(index))

        for item in self.tree.selectedItems():
            take(item)
        return found

    def _folder(self) -> str:
        item = self.tree.currentItem()
        return str(item.data(0, FOLDER_ROLE) or "") if item is not None else ""

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _sync(self) -> None:
        self.open_button.setEnabled(bool(self._folder()))
        count = len(self._rows())
        self.backup_button.setText(
            trn("&Back up %(n)s mod…", "&Back up %(n)s mods…", count) if count else tr("&Back up")
        )
        self.backup_button.setEnabled(bool(count) and not self._busy())

    def _open(self) -> None:
        folder = self._folder()
        if folder:
            open_on_disk(folder)

    # -- backing up ------------------------------------------------------------------
    def _back_up(self) -> None:
        mods = [row.data(0, MOD_ROLE) for row in self._rows()]
        if not mods or self._busy():
            return
        sized = [(mod.target.parent, mod.target.name, disk.folder_size(mod.target)) for mod in mods]
        total = sum(size for _folder, _name, size in sized)
        where = library.backup_dir(mods[0].target.parent).parent
        if not ask(
            self, tr("Back up these mods?"),
            trn(
                "%(n)s mod, %(size)s, is zipped whole into %(where)s. To get it "
                "back, drop its zip on the Studio's window.",
                "%(n)s mods, %(size)s, are zipped whole into %(where)s. To get one "
                "back, drop its zip on the Studio's window.",
                len(mods), size=size_text(total), where=wrappable_path(where),
            ),
            tr("Back up"),
        ):
            return
        self._worker = LibraryBackupWorker(sized, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_done)
        self.progress.setVisible(True)
        show_progress(self.progress, 0, total)
        self._worker.start()
        self._sync()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        show_progress(self.progress, done, total)
        self.status.setText(tr("Backing up %s…", name))

    def _on_done(self, done) -> None:
        self.progress.setVisible(False)
        made = [name for name, result in done if isinstance(result, Path)]
        failed = [(name, result) for name, result in done if not isinstance(result, Path)]
        said = []
        if made:
            said.append(trn("%(n)s mod backed up.", "%(n)s mods backed up.", len(made)))
        for name, why in failed:
            said.append(tr("%(mod)s could not be backed up: %(why)s", mod=name, why=why))
        self.status.setText(" ".join(said))
        if self._regroup is not None:
            self._fill(self._regroup())
        self._sync()

    def reject(self) -> None:
        if self._busy():
            self._worker.cancel()
            self._worker.wait(10000)
        super().reject()


def _backed_up_on(backup: str) -> str:
    """When a copy's zip was made, for its row, or "" for none."""
    if not backup:
        return ""
    try:
        return date_text(Path(backup).stat().st_mtime)
    except OSError:
        return ""
