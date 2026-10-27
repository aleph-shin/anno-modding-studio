"""A list of mods, which of them are here, and a button to fetch the rest.

One dialog with two ways in.  A collection on mod.io opens it with the
collection's mods; a loadout somebody else made opens it with the mods that
loadout was built on.  What it shows either way is the plan of
:mod:`core.installplan` -- a row a mod, said to be installed or not by what is
on disk -- and what it does is run that module's queue over the rows that are
ticked, saying what became of each as it happens.

It says how big the fetch is before it starts, because the median collection
for Anno 1800 is thirty-seven mods and over a gigabyte, and it refuses to
start one the disk has no room for.  Stopping is not a failure: what landed
stays, and the rows still to come stay ticked for the next press.

Nothing is written into mod.io's own download cache -- that folder belongs to
the game's browser -- and nothing already installed is replaced unless its
row was ticked by hand.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import games, installplan, modfolder, modio, secrets
from ..core.i18n import size_text, tr, tr_noop, trn
from . import theme
from .modio_brand import branding
from .modio_task_dialog import ModioTaskDialog
from .widgets import ElidingLabel, rows_table, sort_as, sortable, wrappable_path

COLUMNS = (
    tr_noop("Mod"),
    # Translators: a column heading: who made the mod.
    tr_noop("By"),
    tr_noop("Version"),
    tr_noop("Size"),
    # Translators: a column heading: whether the mod is here, installed.
    tr_noop("Here"),
)

# What a row says of itself before anything more particular is known: the
# theme token its Here cell is drawn in, read from the scheme in use when the
# row is filled.
_COLOUR = {
    installplan.MISSING: "TEXT",
    installplan.CURRENT: "TEXT_DIM",
    installplan.INSTALLED: "TEXT_DIM",
    installplan.OLDER: "WARN",
    installplan.GONE: "ERROR",
    installplan.FLAGGED: "ERROR",
    installplan.UNFOUND: "ERROR",
}


class InstallPlanDialog(ModioTaskDialog):
    """Show what a list of mods would take to have, and fetch what is ticked.

    ``gather`` is where the list comes from -- see
    :func:`core.installplan.collection_source` and
    :func:`core.installplan.loadout_source`.  Afterwards ``placed`` is every
    folder that was installed, ``set_aside`` wherever something replaced
    went, and ``learned`` which mod.io mod each of a loadout's mods turned
    out to be.
    """

    def __init__(
        self,
        game: str,
        store: secrets.Store,
        mods_folder: Optional[Path],
        gather: installplan.Gather,
        *,
        title: str,
        intro: str,
        links: Optional[Dict[str, int]] = None,
        page: str = "",
        library_dir: Path,
        parent=None,
        fetch=modio.transport,
        chunks=modio.stream,
        open_account: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.game_key = game
        self.game = games.get(game)
        self.store = store
        self.mods_folder = Path(mods_folder) if mods_folder else None
        self._gather = gather
        self._links = dict(links or {})
        self._page = page
        # Where what is fetched lands: the Studio's library for this game,
        # with a junction into the mods folder where the folder would be.
        self.library_dir = Path(library_dir)
        self._fetch = fetch
        self._chunks = chunks
        self._open_account = open_account
        self.plan = installplan.Plan()
        self.notes: List[str] = []
        self.learned: Dict[str, int] = {}
        self.outcomes: List[installplan.Outcome] = []
        self.placed: List[Path] = []
        self.set_aside: List[Path] = []
        # What became of replaced mods' iModYourAnno settings, one line each.
        self.carried: List[str] = []
        # And each installed with no checksum to check its file against.
        self.unchecked: List[str] = []
        # Every mod of the list that is installed once the dialog closes --
        # what was fetched here and what was already there -- by the folder
        # name the game sees.  What a folder read afterwards is about.
        self.present: List[str] = []
        # And of those, the ones this dialog put there that were not installed
        # before -- not the ones it only updated. What a collection brought.
        self.brought: List[str] = []
        self._running: List[installplan.Row] = []
        self._filling = False
        self.setWindowTitle(title)
        self.setMinimumSize(860, 560)
        self._build_ui(title, intro)
        self._begin()

    # -- construction ------------------------------------------------------
    def _build_ui(self, title: str, intro: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        hint = QLabel(intro)
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.table = rows_table([tr(column) for column in COLUMNS])
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._refresh_state)
        self.table.setAccessibleName(title)
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        # Both places it writes to: the library holds the mods, and the mods
        # folder a link to each.
        library_row = QHBoxLayout()
        # Translators: before the library folder the mod is installed into.
        library_row.addWidget(QLabel(tr("Into:")))  # design: names a value that is only read
        self.library_label = ElidingLabel(mode=Qt.TextElideMode.ElideLeft)
        self.library_label.setObjectName("SubHeading")
        self.library_label.setFullText(str(self.library_dir))
        library_row.addWidget(self.library_label, 1)
        outer.addLayout(library_row)

        destination = QHBoxLayout()
        # Translators: before the mods folder that gets a link to the mod.
        link_caption = QLabel(tr("&Link in:"))
        destination.addWidget(link_caption)
        self.folder_label = ElidingLabel(mode=Qt.TextElideMode.ElideLeft)
        self.folder_label.setObjectName("SubHeading")
        destination.addWidget(self.folder_label, 1)
        self.change_button = QPushButton(tr("C&hange…"))
        link_caption.setBuddy(self.change_button)
        self.change_button.clicked.connect(self._choose_folder)
        destination.addWidget(self.change_button)
        outer.addLayout(destination)

        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setVisible(False)
        outer.addWidget(self.warnings)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        buttons = QHBoxLayout()
        buttons.addWidget(branding())
        self.account_button = QPushButton(tr("Set up mod.io &account…"))
        self.account_button.clicked.connect(self._account)
        self.account_button.setVisible(False)
        buttons.addWidget(self.account_button)
        self.open_button = QPushButton(tr("&Open on mod.io"))
        self.open_button.clicked.connect(self._open_selected)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        self.stop_button = QPushButton(tr("&Stop"))
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setVisible(False)
        buttons.addWidget(self.stop_button)
        self.close_button = QPushButton(tr("&Close"))
        self.close_button.clicked.connect(self._close)
        buttons.addWidget(self.close_button)
        self.fetch_button = QPushButton(tr("&Install"))
        self.fetch_button.setObjectName("Primary")
        self.fetch_button.setDefault(True)
        self.fetch_button.clicked.connect(self._fetch_chosen)
        buttons.addWidget(self.fetch_button)
        outer.addLayout(buttons)

    # -- state ----------------------------------------------------------------
    def _api_key(self) -> str:
        return self.store.load().api_key

    def say(self, text: str) -> None:
        self.summary.setText(text)

    def _refresh_state(self) -> None:
        busy = self._busy()
        chosen = self.plan.chosen()
        self.folder_label.setFullText(
            str(self.mods_folder) if self.mods_folder else tr("(choose a mods folder)")
        )
        self.change_button.setEnabled(not busy)
        short = installplan.short_of_room(self.plan, self.mods_folder) if self.mods_folder else ""
        self.fetch_button.setText(
            trn("&Install %(n)s mod", "&Install %(n)s mods", len(chosen)) if chosen else tr("&Install")
        )
        self.fetch_button.setEnabled(
            bool(chosen) and not busy and not short and self.mods_folder is not None
        )
        self.open_button.setEnabled(bool(self._selected_url()))
        lines = list(self.notes)
        if short:
            lines.append(short)
        self.warnings.setText("\n".join(lines))
        theme.tint(self.warnings, "error" if short else "warn")
        self.warnings.setVisible(bool(lines))
        if not busy and self.plan.rows and not self.outcomes:
            self.summary.setText(self.plan.describe())

    def _selected_row(self) -> Optional[installplan.Row]:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows or rows[0].row() >= len(self.plan.rows):
            return None
        return self.plan.rows[rows[0].row()]

    def _selected_url(self) -> str:
        row = self._selected_row()
        if row is not None and row.wanted.summary is not None and row.wanted.summary.url:
            return row.wanted.summary.url
        return self._page if row is None else ""

    def _open_selected(self) -> None:
        url = self._selected_url()
        if url:
            webbrowser.open(url)

    def _account(self) -> None:
        if self._open_account is not None:
            self._open_account()
        if self.store.load().api_key and not self.plan.rows:
            self.account_button.setVisible(False)
            self._begin()

    def _choose_folder(self) -> None:
        start = str(self.mods_folder) if self.mods_folder else ""
        chosen = QFileDialog.getExistingDirectory(self, tr("Mods folder to link into"), start)
        if chosen and Path(chosen) != self.mods_folder:
            self.mods_folder = Path(chosen)
            # What is installed is a fact about the folder, so a new folder
            # is a new plan.
            self._begin()

    # -- making the plan ------------------------------------------------------
    def _begin(self) -> None:
        if self._busy():
            return
        key = self.store.load().api_key
        if not key:
            self.summary.setText(tr("Asking mod.io needs your API key, which is not set up yet."))
            self.account_button.setVisible(True)
            self._refresh_state()
            return
        game, folder, links, gather = self.game_key, self.mods_folder, self._links, self._gather
        library_dir = self.library_dir

        def job(client, say, stop):
            say(0, 0, tr("Looking at what is installed…"))
            installed = installplan.inventory(game, folder, links, library_dir=library_dir)
            gathered = gather(client, say, stop, installed)
            return gathered, installplan.build(gathered.wanted, installed)

        self.outcomes = []
        self.summary.setText(tr("Looking at what is installed…"))
        self._start(job, self._on_planned)

    def _on_planned(self, result) -> None:
        gathered, plan = result
        self.plan = plan
        self.notes = list(gathered.notes)
        self.learned.update(gathered.learned)
        self._fill()

    def _fill(self) -> None:
        self._filling = True
        try:
            self.table.setRowCount(len(self.plan.rows))
            for number, row in enumerate(self.plan.rows):
                self._fill_row(number, row)
            # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
            self.sorting.apply()
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in (1, 2, 3):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        finally:
            self._filling = False
        self._refresh_state()

    def _fill_row(self, number: int, row: installplan.Row, said: str = "", colour: str = "") -> None:
        was = self._filling
        self._filling = True
        try:
            summary = row.wanted.summary
            name = QTableWidgetItem(row.name)
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if row.fetchable:
                name.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable)
                name.setCheckState(Qt.CheckState.Checked if row.fetch else Qt.CheckState.Unchecked)
            else:
                name.setFlags(flags)
                name.setToolTip(row.why_not)
            self.table.setItem(number, 0, name)
            self.table.setItem(number, 1, QTableWidgetItem(summary.submitted_by if summary else ""))
            self.table.setItem(number, 2, QTableWidgetItem(summary.version if summary else row.wanted.version))
            size = QTableWidgetItem(size_text(row.size) if row.size else "")
            if row.size:
                sort_as(size, row.size)
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(number, 3, size)
            here = QTableWidgetItem(said or row.note)
            here.setToolTip(said or row.why_not or row.note)
            here.setForeground(QColor(colour or getattr(theme, _COLOUR.get(row.status, "TEXT"))))
            self.table.setItem(number, 4, here)
        finally:
            self._filling = was

    def _on_item_changed(self, item) -> None:
        if self._filling or item.column() != 0 or item.row() >= len(self.plan.rows):
            return
        row = self.plan.rows[item.row()]
        if row.fetchable:
            row.fetch = item.checkState() == Qt.CheckState.Checked
            self.outcomes = []
            self._refresh_state()

    # -- fetching -------------------------------------------------------------
    def _fetch_chosen(self) -> None:
        chosen = self.plan.chosen()
        if not chosen or self.mods_folder is None or self._busy():
            return
        game_id, folder, chunks = self.game.modio_game_id, self.mods_folder, self._chunks
        library_dir = self.library_dir
        self._running = chosen
        self.outcomes = []

        game = self.game_key

        def job(client, say, stop):
            return installplan.fetch(
                client, game_id, chosen, folder, library_dir=library_dir, game=game,
                chunks=chunks, progress=say, stop=stop,
                settled=lambda position, outcome: self._worker.settled.emit(position, outcome),
            )

        self._start(job, self._on_fetched, self._on_settled)

    def _on_settled(self, position: int, outcome: installplan.Outcome) -> None:
        """One mod of the queue is done with: say so on its row as it happens."""
        row = outcome.row
        if row not in self.plan.rows:
            return
        number = self.plan.rows.index(row)
        if outcome.status == installplan.DONE:
            installed = outcome.installed
            # The junctions in the mods folder: what the game sees, and what
            # a caller reads in; the library folders behind them are the
            # Studio's business.
            self.placed.extend(linked.link for linked in installed.linked)
            if not row.copies:
                self.brought.extend(linked.link.name for linked in installed.linked)
            if installed.set_aside is not None:
                self.set_aside.append(installed.set_aside)
            # Here now, so not something to fetch again.
            row.status, row.fetch, row.replace = installplan.CURRENT, False, None
            row.why_not = tr("It is already installed.")
            row.note = tr("installed just now")
            if installed.carried:
                row.note = tr("installed just now, %s", installed.carried.describe())
                self.carried.append(installed.carried.describe(row.wanted.name))
            # A file mod.io gave no checksum for is installed unchecked, and
            # the summary says so whole.
            if outcome.message:
                row.note = tr("installed just now, unchecked")
                self.unchecked.append(outcome.message)
            self._fill_row(number, row, colour=theme.CHANGED)
        elif outcome.status == installplan.FAILED:
            self._fill_row(number, row, tr("could not be installed: %s", outcome.message), theme.ERROR)
        elif outcome.status == installplan.SKIPPED:
            row.fetch = False
            self._fill_row(number, row, tr("skipped: %s", outcome.message), theme.ERROR)

    def _on_fetched(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        # Whole sentences, one after another.
        parts = [installplan.describe_outcomes(self.outcomes)]
        if self.set_aside:
            parts.append(tr("What was replaced is at %s.",
                            wrappable_path(self.set_aside[0].parent)))
        parts += self.carried + self.unchecked
        said = " ".join(parts)
        self.summary.setText(said)

    # -- the thread (ModioTaskDialog runs it) -----------------------------------
    def _on_failed(self, message: str) -> None:
        self.say(tr("mod.io could not be asked: %s", message))

    def _close(self) -> None:
        self._halt()
        self.present = sorted({
            *(link.name for link in self.placed),
            *(modfolder.bare_name(row.copies[0].folder.name)
              for row in self.plan.rows if row.copies and not row.copies[0].in_cache),
        })
        if self.placed:
            super().accept()
        else:
            super().reject()

    def reject(self) -> None:
        self._close()
