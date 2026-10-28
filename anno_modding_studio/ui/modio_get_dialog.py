"""Install a mod from mod.io: search, download, unpack, and hand it to the import.

The whole feature for most sessions is a search box and one button.  The
button fetches the mod's current file, checks it against the checksum mod.io
gave for it, unpacks it into the Studio's library the way the game wants it
(see :mod:`core.modzip`) with a link in the game's mods folder (see
:mod:`core.library`), and the window then reads it in -- which is the
*Change a mod you downloaded* aim with the download bolted on the front.

The same box finds collections.  A collection is a list of mods somebody
curated -- thirty-seven of them and over a gigabyte, at the median for Anno
1800 -- so picking one does not download anything: it opens the plan of
:mod:`ui.install_plan_dialog`, which says which of its mods are already here
and how big the rest are, and fetches what is ticked.

Everything that touches the network runs on a thread and can be stopped.
Nothing is written into the mod.io client's own cache: that folder belongs to
the game's browser, and a folder there its state file does not know about is
a folder it may delete.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import games, installplan, library, modio, secrets, updates
from ..core.i18n import date_text, number, size_text, tr, tr_noop, trn
from . import theme
from .install_plan_dialog import InstallPlanDialog
from .modio_brand import branding
from .modio_task_dialog import ModioTaskDialog
from .widgets import (
    ElidingLabel,
    enter_activates,
    least_size,
    named,
    rows_table,
    show_box_text,
    sort_as,
    sortable,
    tab_as_laid_out,
)

COLUMNS = (
    tr_noop("Mod"),
    # Translators: a column heading: who made the mod.
    tr_noop("By"),
    # Translators: a column heading -- when mod.io last updated the mod.
    tr_noop("Updated"),
    tr_noop("Version"), tr_noop("Size"),
    # Translators: a column heading: how many times it was downloaded.
    tr_noop("Downloads"),
)
COLLECTION_COLUMNS = (
    tr_noop("Collection"), tr_noop("By"), tr_noop("Updated"),
    # Translators: a column heading: how many mods a collection holds.
    tr_noop("Mods"),
    tr_noop("Size"), tr_noop("Downloads"),
)
# What the search looks for. Kept in English as the switch's data; shown
# translated.
MODS, COLLECTIONS = tr_noop("Mods"), tr_noop("Collections")
DOWNLOADS = ".modio-downloads"


class ModioGetDialog(ModioTaskDialog):
    """Search mod.io and install one mod into the library, linked into the
    mods folder.

    On accept, ``installed`` says what landed and ``summary`` which mod it
    was; the caller reads the folder in.  When it was a collection instead,
    ``collection`` says which and ``placed`` is every folder its plan
    installed; ``installed`` stays None.
    """

    def __init__(
        self,
        game: str,
        store: secrets.Store,
        mods_folder: Optional[Path],
        parent=None,
        fetch=modio.transport,
        chunks=modio.stream,
        open_account: Optional[Callable[[], None]] = None,
        known_ids=None,
        links=None,
        *,
        library_dir: Path,
    ):
        super().__init__(parent)
        self.game_key = game
        self.game = games.get(game)
        self.library_dir = Path(library_dir)
        # ModID -> mod.io id, as far as the loadout knows: what lets a
        # collection's plan recognise a mod installed under another name.
        self.links = dict(links or {})
        self.collections: List[modio.CollectionSummary] = []
        self.collection: Optional[modio.CollectionSummary] = None
        self.placed: List[Path] = []
        self.set_aside: List[Path] = []
        # Every mod of the collection installed now, by folder name: what was
        # fetched and what was there already.
        self.present: List[str] = []
        # And the ones it brought: not installed before it was.
        self.brought: List[str] = []
        # mod.io ids of the mods loaded in the loadout. A dependency that is
        # one of them is said to be loaded; one that is installed and not
        # loaded is found on disk when the mod is asked for.
        self.known_ids = set(known_ids or ())
        self.store = store
        self.mods_folder = Path(mods_folder) if mods_folder else None
        self._fetch = fetch
        self._chunks = chunks
        self._open_account = open_account
        self.results: List[modio.ModSummary] = []
        self.summary: Optional[modio.ModSummary] = None
        self.installed: Optional[library.LibraryInstallResult] = None
        # Dependencies fetched alongside, each with what install did for it.
        self.also: List[tuple] = []
        self.setWindowTitle(tr("Install a mod from mod.io"))
        least_size(self, 820, 520)
        self._build_ui()
        self._refresh_state()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        self.hint = QLabel(self._hint_text(False))
        self.hint.setObjectName("StepHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        search = QHBoxLayout()
        self.kind = QComboBox()
        for kind in (MODS, COLLECTIONS):
            self.kind.addItem(tr(kind), kind)
        self.kind.setAccessibleName(tr("What to search for"))
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        search.addWidget(self.kind)
        self.query = QLineEdit()
        self.query.setPlaceholderText(tr("Search by name, or paste a mod.io address…"))
        self.query.setAccessibleName(tr("Search"))
        self.query.returnPressed.connect(self._search)
        search.addWidget(self.query, 1)
        self.search_button = QPushButton(tr("&Search"))
        self.search_button.clicked.connect(self._search)
        search.addWidget(self.search_button)
        outer.addLayout(search)

        self.table = rows_table([tr(column) for column in COLUMNS])
        self.table.itemSelectionChanged.connect(self._refresh_state)
        enter_activates(self.table)
        self.table.itemActivated.connect(lambda _item: self._download())
        self.table.setAccessibleName(tr("Search results"))
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        # Both places it writes to: the library holds the mod, and the mods
        # folder a link to it.
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
        change = QPushButton(tr("C&hange…"))
        link_caption.setBuddy(change)
        change.clicked.connect(self._choose_folder)
        destination.addWidget(change)
        outer.addLayout(destination)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addWidget(branding())
        self.account_button = QPushButton(tr("Set up mod.io &account…"))
        self.account_button.clicked.connect(self._account)
        buttons.addWidget(self.account_button)
        buttons.addStretch(1)
        self.stop_button = QPushButton(tr("S&top"))
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setVisible(False)
        buttons.addWidget(self.stop_button)
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.get_button = self.box.addButton(
            tr("&Install mod"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.get_button.setObjectName("Primary")
        self.box.accepted.connect(self._download)
        self.box.rejected.connect(self.reject)
        tab_as_laid_out(self.box)
        buttons.addWidget(self.box)
        outer.addLayout(buttons)

    # -- state ----------------------------------------------------------------
    def _api_key(self) -> str:
        return self.store.load().api_key

    def say(self, text: str) -> None:
        self.status.setText(text)

    def _collecting(self) -> bool:
        return self.kind.currentData() == COLLECTIONS

    def set_kind(self, kind: str) -> None:
        """Look for ``MODS`` or ``COLLECTIONS``."""
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))

    def _hint_text(self, collecting: bool) -> str:
        if collecting:
            return tr(
                "A collection is a set of %s mods somebody put together on "
                "mod.io. Picking one downloads no mods yet. It first shows "
                "which of its mods you already have and how big the rest are.",
                self.game.label,
            )
        return tr(
            "The %s mod you pick is downloaded, checked against mod.io's "
            "checksum, installed into your library and read in. Your mods "
            "folder gets a link to it.",
            self.game.label,
        )

    def _selected(self) -> Optional[modio.ModSummary]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if self._collecting() or not rows or rows[0].row() >= len(self.results):
            return None
        return self.results[rows[0].row()]

    def _selected_collection(self) -> Optional[modio.CollectionSummary]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not self._collecting() or not rows or rows[0].row() >= len(self.collections):
            return None
        return self.collections[rows[0].row()]

    def _on_kind_changed(self, _kind: str) -> None:
        """The two kinds of result do not share a table: changing which is
        being looked for empties it."""
        self.results, self.collections = [], []
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(
            [tr(column) for column in (COLLECTION_COLUMNS if self._collecting() else COLUMNS)]
        )
        # Other columns now: a sort by one of the old ones says nothing.
        self.sorting.sort(-1)
        self.get_button.setText(
            tr("See what it installs…") if self._collecting() else tr("&Install mod")
        )
        self.hint.setText(self._hint_text(self._collecting()))
        self.status.setText("")
        self._refresh_state()

    def _refresh_state(self) -> None:
        busy = self._busy()
        has_key = bool(self._api_key())
        self.account_button.setVisible(not has_key)
        self.search_button.setEnabled(has_key and not busy)
        self.query.setEnabled(has_key and not busy)
        self.folder_label.setFullText(str(self.mods_folder) if self.mods_folder else tr("(choose a mods folder)"))
        chosen = self._selected()
        if self._collecting():
            # Which folder is settled on the plan, where it decides what
            # counts as installed; nothing is needed here but a collection.
            self.get_button.setEnabled(
                has_key and not busy and self._selected_collection() is not None
            )
        else:
            self.get_button.setEnabled(
                has_key and not busy and chosen is not None and chosen.has_file
                and not chosen.virus_positive and self.mods_folder is not None
            )
        if not has_key:
            self.status.setText(tr("Searching needs your mod.io API key, which is not set up yet."))
        elif chosen is not None and chosen.virus_positive:
            self.status.setText(tr("mod.io's scan flagged this mod's file, so the Studio will not download it."))
        elif chosen is not None and not chosen.has_file:
            self.status.setText(tr("This mod has no file to download yet."))

    def _account(self) -> None:
        if self._open_account is not None:
            self._open_account()
        self._refresh_state()

    def _choose_folder(self) -> None:
        start = str(self.mods_folder) if self.mods_folder else ""
        chosen = QFileDialog.getExistingDirectory(self, tr("Mods folder to link into"), start)
        if chosen:
            self.mods_folder = Path(chosen)
            self._refresh_state()

    # -- searching ------------------------------------------------------------
    def _search(self) -> None:
        wanted = self.query.text().strip()
        if not wanted or not self._api_key() or self._busy():
            return
        game_id = self.game.modio_game_id
        link = modio.parse_page(wanted)
        if link is not None and link.game_name_id != self.game.modio_name_id:
            self.status.setText(tr(
                "That address is for another game (%(other)s). This loadout is %(game)s.",
                other=link.game_name_id, game=self.game.label,
            ))
            return
        if link is not None:
            # The address says which kind it is, whatever the switch says.
            self.set_kind(COLLECTIONS if link.collection_name_id else MODS)
        if self._collecting():
            self._search_collections(wanted, link)
            return

        def job(client, say, stop):
            if link is not None:
                say(0, 0, tr("Looking up %s on mod.io…", link.mod_name_id))
                found = client.by_name_id(game_id, link.mod_name_id)
                return [found] if found is not None else []
            say(0, 0, tr("Searching mod.io for %s…", wanted))
            return client.search(game_id, wanted, limit=50)

        self.status.setText(tr("Asking mod.io…"))
        self._start(job, self._on_results)

    def _search_collections(self, wanted: str, link) -> None:
        game_id = self.game.modio_game_id

        def job(client, say, stop):
            if link is not None:
                say(0, 0, tr("Looking up %s on mod.io…", link.collection_name_id))
                found = client.collection_by_name_id(game_id, link.collection_name_id)
                return [found] if found is not None else []
            say(0, 0, tr("Searching mod.io's collections for %s…", wanted))
            return client.collections(game_id, wanted, limit=50)

        self.status.setText(tr("Asking mod.io…"))
        self._start(job, self._on_collections)

    def _on_collections(self, results) -> None:
        self.collections = list(results)
        self.table.setRowCount(len(self.collections))
        for row, found in enumerate(self.collections):
            name = QTableWidgetItem(found.name)
            name.setToolTip(found.summary)
            if found.incomplete:
                theme.mark(name, "warn")
                name.setToolTip(tr(
                    "mod.io marks this collection incomplete: some of its mods "
                    "are no longer there."
                ))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(found.submitted_by))
            self.table.setItem(row, 2, _dated(found.date_updated))
            self.table.setItem(row, 3, _number(number(found.mods_total), found.mods_total))
            self.table.setItem(row, 4, _number(
                size_text(found.filesize) if found.filesize else "", found.filesize or None,
            ))
            self.table.setItem(row, 5, _number(number(found.downloads), found.downloads))
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLLECTION_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.status.setText(
            trn("%(n)s collection found.", "%(n)s collections found.", len(self.collections))
            if self.collections else tr("No collection on mod.io is called that.")
        )
        if self.collections:
            self.table.selectRow(0)
        self._refresh_state()

    # -- a collection: not a download but a plan --------------------------------
    def plan_for(self, collection: modio.CollectionSummary) -> InstallPlanDialog:
        """The plan dialog for ``collection``; its own method so a test can
        drive the dialog rather than wait on it."""
        return InstallPlanDialog(
            self.game_key,
            self.store,
            self.mods_folder,
            installplan.collection_source(self.game.modio_game_id, collection),
            title=collection.name,
            intro=self._collection_intro(collection),
            links=self.links,
            page=collection.url,
            library_dir=self.library_dir,
            parent=self,
            fetch=self._fetch,
            chunks=self._chunks,
            open_account=self._open_account,
        )

    def _collection_intro(self, collection: modio.CollectionSummary) -> str:
        size = size_text(collection.filesize)
        if collection.submitted_by:
            return trn(
                "A collection by %(who)s: %(n)s mod, %(size)s to download in "
                "all. It lists mods, not versions, so you get each mod's "
                "current file. Mods you already have are left as they are. "
                "Untick any you do not want.",
                "A collection by %(who)s: %(n)s mods, %(size)s to download "
                "in all. It lists mods, not versions, so you get each mod's "
                "current file. Mods you already have are left as they are. "
                "Untick any you do not want.",
                collection.mods_total, who=collection.submitted_by, size=size,
            )
        return trn(
            "A collection by somebody on mod.io: %(n)s mod, %(size)s to "
            "download in all. It lists mods, not versions, so you get each "
            "mod's current file. Mods you already have are left as they are. "
            "Untick any you do not want.",
            "A collection by somebody on mod.io: %(n)s mods, %(size)s to "
            "download in all. It lists mods, not versions, so you get each "
            "mod's current file. Mods you already have are left as they are. "
            "Untick any you do not want.",
            collection.mods_total, size=size,
        )

    def _open_collection(self) -> None:
        chosen = self._selected_collection()
        if chosen is None or not self.get_button.isEnabled():
            return
        plan = self.plan_for(chosen)
        try:
            plan.exec()
            if plan.mods_folder is not None:
                self.mods_folder = plan.mods_folder
            if not plan.placed:
                self._refresh_state()
                return
            self.collection = chosen
            self.placed, self.set_aside = list(plan.placed), list(plan.set_aside)
            self.present = list(plan.present)
            self.brought = list(getattr(plan, "brought", []))
        finally:
            plan.deleteLater()
        self.accept()

    def _on_results(self, results) -> None:
        self.results = list(results)
        self.table.setRowCount(len(self.results))
        for row, mod in enumerate(self.results):
            name = QTableWidgetItem(mod.name)
            if mod.virus_positive:
                theme.mark(name, "error")
                name.setToolTip(tr("mod.io's scan flagged this file."))
            elif not mod.has_file:
                name.setForeground(QColor(theme.TEXT_DIM))
                name.setToolTip(tr("No file uploaded yet."))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(mod.submitted_by))
            self.table.setItem(row, 2, _dated(mod.date_updated))
            self.table.setItem(row, 3, QTableWidgetItem(mod.version))
            self.table.setItem(row, 4, _number(
                size_text(mod.filesize) if mod.filesize else "", mod.filesize or None,
            ))
            self.table.setItem(row, 5, _number(number(mod.downloads), mod.downloads))
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.status.setText(
            trn("%(n)s mod found.", "%(n)s mods found.", len(self.results))
            if self.results else tr("No mod on mod.io has that name.")
        )
        if self.results:
            self.table.selectRow(0)
        self._refresh_state()

    # -- downloading ----------------------------------------------------------
    def _download(self) -> None:
        """First what the mod needs, then the mod: its dependencies are asked
        about before anything is fetched, and fetched first if wanted."""
        if self._collecting():
            self._open_collection()
            return
        chosen = self._selected()
        if chosen is None or self.mods_folder is None or not self.get_button.isEnabled():
            return
        game_id, game, mods_folder = self.game.modio_game_id, self.game_key, self.mods_folder
        links, library_dir = self.links, self.library_dir

        def job(client, say, stop):
            say(0, 0, tr("Asking mod.io what %s needs…", chosen.name))
            needs = client.dependencies(game_id, chosen.id)
            # Stop pressed while mod.io was answering: the client asks only
            # before each request, and the answer would start the download.
            if stop():
                raise modio.Cancelled("stopped")
            # Which of them no mods folder and no library has, counted as a
            # collection's plan counts it: the loadout's layers alone miss a
            # mod that is installed and not loaded.
            absent = installplan.missing(
                installplan.wanted_from_mods(needs),
                installplan.inventory(game, mods_folder, links, library_dir=library_dir),
            ) if needs else []
            return chosen, needs, {one.modio_id for one in absent}

        self._start(job, self._on_dependencies)

    def _on_dependencies(self, result) -> None:
        chosen, needs, absent = result
        missing = [dep for dep in needs if dep.id in absent and dep.id not in self.known_ids]
        with_deps: List[modio.ModSummary] = []
        if missing:
            answer = self.ask_dependencies(chosen, needs, missing)
            if answer == "cancel":
                self.status.setText(tr("No mod was installed."))
                return
            if answer == "all":
                with_deps = missing
        self._fetch_all([*with_deps, chosen])

    def ask_dependencies(self, chosen, needs, missing) -> str:
        """Whether to fetch the mod's dependencies too: "all", "one" or "cancel".

        A method rather than a static prompt so a test can answer it.  A
        dependency that is not ``missing`` is loaded in the loadout or only
        installed, and is listed as which; neither is fetched, and this
        loads neither.
        """
        lines = [trn("%(mod)s needs %(n)s other mod:", "%(mod)s needs %(n)s other mods:",
                     len(needs), mod=named(chosen.name)), ""]
        for dep in needs:
            lines.append(
                "  • %s" % dep.name if dep in missing
                else tr("  • %s  (already loaded)", dep.name) if dep.id in self.known_ids
                else tr("  • %s  (already installed)", dep.name)
            )
        lines.append("")
        lines.append(
            trn("Install the %(n)s not installed yet first?",
                "Install the %(n)s not installed yet first?",
                len(missing))
            if len(missing) != len(needs)
            else tr("Install them first?")
        )
        box = QMessageBox(self)
        box.setWindowTitle(tr("This mod needs others"))
        show_box_text(box, "\n".join(lines))
        everything = box.addButton(tr("&Install them too"), QMessageBox.ButtonRole.AcceptRole)
        alone = box.addButton(tr("&Just this mod"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(everything)
        box.exec()
        clicked = box.clickedButton()
        return "all" if clicked is everything else "one" if clicked is alone else "cancel"

    def _fetch_all(self, mods: List[modio.ModSummary]) -> None:
        game_id, mods_folder = self.game.modio_game_id, self.mods_folder
        chunks, library_dir = self._chunks, self.library_dir

        def job(client, say, stop):
            done_all = []
            scratch = mods_folder / DOWNLOADS
            scratch.mkdir(parents=True, exist_ok=True)
            try:
                for position, wanted in enumerate(mods, start=1):
                    label = tr(
                        "%(mod)s (%(position)s of %(count)s)", mod=wanted.name,
                        position=number(position), count=number(len(mods)),
                    ) if len(mods) > 1 else wanted.name
                    done_all.append(installplan.fetch_and_install(
                        client, game_id, wanted, scratch, library_dir, mods_folder,
                        name=updates.strip_category(wanted.name) or wanted.name,
                        ask_first=True, chunks=chunks, stop=stop, label=label,
                        progress=lambda done, total, label=label: say(done, total, tr("Downloading %s…", label)),
                        saying=lambda text: say(0, 0, text),
                    ))
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            return done_all

        self._start(job, self._on_installed)

    def _on_installed(self, result) -> None:
        *self.also, (self.summary, self.installed) = result
        self.accept()

    # -- the thread (ModioTaskDialog runs it) -----------------------------------
    def reject(self) -> None:
        self._halt()
        super().reject()


def _number(text: str, value=None) -> QTableWidgetItem:
    """A right-aligned cell that sorts as ``value``, the number it writes."""
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return sort_as(item, value) if value is not None else item


def _dated(timestamp: int) -> QTableWidgetItem:
    """A date's cell, which sorts by when and not by how it is written."""
    item = QTableWidgetItem(_date(timestamp))
    return sort_as(item, timestamp) if timestamp else item


def _date(timestamp: int) -> str:
    if not timestamp:
        return ""
    return date_text(timestamp)
