"""Import assets and texts straight out of a game installation's .rda archives.

This exists because doing it by hand is genuinely awful: Anno 1800 ships 38
archives, most of which contain a complete ``assets.xml``, and only the newest
one is correct.  Extracting the obvious-looking ``data0.rda`` gets you the
launch-day game.  Here the Studio reads every archive's directory, resolves
patch order, and pulls out just the two files you need.

For someone who has never opened this dialog before and has Anno installed in a
Steam or Ubisoft Connect default location, that whole process should not require
a decision.  ``_populate_installs`` runs a live registry-and-directory scan (see
``core.discovery`` -- nothing cached, nothing done before the dialog opens) every
time the dialog is constructed, and when it turns up exactly one archived
install *and* the user has never pointed this dialog anywhere before, the game
folder is filled in **and the archive scan starts on its own**.  Nothing here
touches disk beyond reading directory listings and archive headers, so doing it
unasked is safe -- extraction still waits for an explicit "Extract and load".
A returning user's last folder always wins over the auto-detected one, and
anyone with more than one install, or none, still chooses by hand.

Choosing is all that is left to them, though.  The scan is as safe after a
choice as before one, so picking a detected install from the list starts it:
with two games installed the dialog used to open with nothing chosen, and
then wait for "Scan archives" beside an "Extract and load" that was greyed,
barely, with nothing to say why.  And a dialog opened from one game's row on
the Start tab has had its choice made already: that game's install is picked
as the list would pick it, unless the folder that game was last read from is
another copy of it.  Where no install of that game was detected the dialog
still opens on that game: on no folder, saying so, with nothing scanned.  The
other game's install, the only one found, is not what was asked for.  Until a
scan has run, the dialog says that one is needed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import discovery, disk, gamefiles, games, launch
from ..core.i18n import join_list, number, tr, trn
from .help_tip import EXTRACTED_HELP, HelpTip
from .widgets import sort_as, sortable, tab_as_laid_out


class ScanWorker(QThread):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = gamefiles.scan_game_files(
                self.folder,
                progress=lambda i, n, name: self.progress.emit(i, n, name),
                should_cancel=lambda: self._cancel,
            )
        except Exception as exc:  # noqa: BLE001 - a worker reports whatever went wrong
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            self.succeeded.emit(result)


class _Cancelled(Exception):
    """An extraction stopped by :meth:`ExtractWorker.cancel`."""


class ExtractWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object, object, object)  # assets, texts, {schema name: path}
    failed = Signal(str)

    def __init__(self, assets, texts, schema, destination: Path, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.texts = texts
        self.schema = list(schema or [])
        self.destination = Path(destination)
        self._cancel = False

    def cancel(self) -> None:
        """Stop before the next file: one being written is finished first."""
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            assets_path = self._extract(self.assets)
            texts_path = None
            if self.texts is not None:
                texts_path = self._extract(self.texts)
            schema_paths = {}
            for found in self.schema:
                schema_paths[found.filename.lower()] = self._extract(found)
        except _Cancelled:
            return
        except Exception as exc:  # noqa: BLE001 - a worker reports whatever went wrong
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            if not self._cancel:
                self.succeeded.emit(assets_path, texts_path, schema_paths)

    def _extract(self, found) -> Path:
        if self._cancel:
            raise _Cancelled
        return gamefiles.extract(
            found, self.destination, progress=lambda p: self.progress.emit(p)
        )


class ImportDialog(QDialog):
    """Pick a game folder, see what's inside, extract the two files that matter."""

    def __init__(
        self,
        parent=None,
        last_folder: str = "",
        last_destination: str = "",
        game: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Load from game files"))
        self.setMinimumWidth(820)
        self.result_scan = None
        # The folder result_scan was read from.  The folder box is not it once
        # someone types or browses after a scan, or a rescan of another fails.
        self._scanned_folder = None
        self.assets_path = None
        self.texts_path = None
        self.schema_paths = {}
        self.source_archive = ""
        self.game_folder = None
        self._scan_worker = None
        self._extract_worker = None
        self._auto_detected_path = None
        # The installs in the list, in its order: row 0 is "Detected installs…".
        self._installs: list[discovery.GameInstall] = []

        self._build_ui(last_destination)
        self._name_the_folder_of(game)
        self._populate_installs()
        asked = self._row_of(game) if game else 0
        if asked and not self._read_from_another_copy(last_folder, game, asked):
            # Opened from this game's row on the Start tab, which is the
            # choice the list is there for.  Whatever was read last is the
            # other game's folder as often as not, and does not win here.
            self.install_combo.setCurrentIndex(asked)
            self._install_chosen(asked)
        elif game and not asked and not self._is_install(last_folder, game):
            # That game's row, and no one install of it to pick.  The folder
            # read last and the one install detected are the other game's
            # then, and neither stands in for the game asked for.
            self._open_on_no_folder(game)
        elif last_folder:
            # A returning user already made this choice once; do not second-guess
            # it just because a different install was also found this time.
            self.folder_edit.setText(last_folder)
        elif self._auto_detected_path:
            # Nobody has used this dialog before, and there is exactly one clear
            # answer -- so there is nothing left for the user to decide before
            # the scan itself, which only reads archive directories.
            self._scan()
        if self._scan_worker is None and not self.status.text():
            self.status.setText(tr(
                "Choose the game folder, then scan its archives. Extract and "
                "load takes the files the scan finds."
            ))

    # -- construction -------------------------------------------------------
    def _build_ui(self, last_destination: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)

        # -- game folder
        row = QHBoxLayout()
        folder_caption = QLabel(tr("&Game folder:"))
        row.addWidget(folder_caption)
        self.install_combo = QComboBox()
        self.install_combo.setAccessibleName(tr("Detected installs"))
        self.install_combo.setMinimumWidth(230)
        self.install_combo.currentIndexChanged.connect(self._install_picked)
        # Only a pick made by hand: filling the list in picks one too.
        self.install_combo.activated.connect(self._install_chosen)
        row.addWidget(self.install_combo)
        self.folder_edit = QLineEdit()
        folder_caption.setBuddy(self.folder_edit)
        self.folder_edit.returnPressed.connect(self._folder_entered)
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton(tr("&Browse…"))
        browse.clicked.connect(self._browse_folder)
        row.addWidget(browse)
        self.scan_button = QPushButton(tr("&Scan archives"))
        self.scan_button.setObjectName("Primary")
        self.scan_button.clicked.connect(self._scan)
        # Off while the box is empty. With both games installed and nothing
        # read yet no install is picked, the box's grey example path read as
        # a folder filled in, and the scan it offered said "That folder does
        # not exist."
        self.scan_button.setEnabled(False)
        self.folder_edit.textChanged.connect(self._sync_scan_button)
        row.addWidget(self.scan_button)
        row.addWidget(HelpTip(tr(EXTRACTED_HELP), self.windowTitle()))
        outer.addLayout(row)

        self.progress = QProgressBar()
        self.progress.hide()
        outer.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("SubHeading")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- findings
        self.table = QTableWidget(0, 4)
        self.table.setTabKeyNavigation(False)
        self.table.setHorizontalHeaderLabels([
            tr("File", context="column"), tr("From archive", context="column"),
            tr("Size", context="column"), tr("Path in game", context="column"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # Never more than a couple of rows (the asset file, one language of
        # texts) -- sized to fit them exactly rather than scrolling inside a
        # fixed box, so every row is visible without the user touching anything.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setAccessibleName(tr("Files found"))
        self.table.hide()
        outer.addWidget(self.table)
        self.sorting = sortable(self.table)

        # -- language
        lang_row = QHBoxLayout()
        language_caption = QLabel(tr("&Localisation:"))
        lang_row.addWidget(language_caption)
        self.language_combo = QComboBox()
        language_caption.setBuddy(self.language_combo)
        self.language_combo.setMinimumWidth(200)
        self.language_combo.currentIndexChanged.connect(self._refresh_table)
        lang_row.addWidget(self.language_combo)
        lang_row.addWidget(
            QLabel(tr("Gives assets their in-game names and makes them searchable."))
        )
        lang_row.addStretch(1)
        self.language_row = QWidget()
        self.language_row.setLayout(lang_row)
        self.language_row.hide()
        outer.addWidget(self.language_row)

        # -- destination
        dest_row = QHBoxLayout()
        dest_caption = QLabel(tr("E&xtract to:"))
        dest_row.addWidget(dest_caption)
        self.dest_edit = QLineEdit(last_destination)
        dest_caption.setBuddy(self.dest_edit)
        dest_row.addWidget(self.dest_edit, 1)
        dest_browse = QPushButton(tr("Bro&wse…"))
        dest_browse.clicked.connect(self._browse_destination)
        dest_row.addWidget(dest_browse)
        self.dest_row = QWidget()
        self.dest_row.setLayout(dest_row)
        self.dest_row.hide()
        outer.addWidget(self.dest_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.extract_button = self.buttons.addButton(
            tr("&Extract and load"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.extract_button.setObjectName("Primary")
        self.extract_button.setEnabled(False)
        # Until a scan has run, and gone with it: see _on_scan_done.
        self.extract_button.setToolTip(tr("Scan the archives first."))
        self.extract_button.clicked.connect(self._extract)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _populate_installs(self) -> None:
        """Fill the dropdown with what a live scan of Steam/Ubisoft finds.

        This is a fresh scan every time -- registry reads plus a shallow
        directory listing, cheap enough to run synchronously on open rather than
        caching anything between launches, which would risk showing a game that
        has since moved or been uninstalled.
        """
        self.install_combo.blockSignals(True)
        self.install_combo.clear()
        self.install_combo.addItem(tr("Detected installs…"), "")
        found = []
        for install in discovery.find_game_installs():
            if install.has_archives:
                self.install_combo.addItem(
                    trn("%(install)s  (%(n)s archive)", "%(install)s  (%(n)s archives)",
                        install.archive_count, install=install.name),
                    str(install.path),
                )
                found.append(install)
        self.install_combo.blockSignals(False)
        self._installs = found
        if len(found) == 1:
            self.install_combo.setCurrentIndex(1)
            self._install_picked()
            self._auto_detected_path = str(found[0].path)
        elif not found:
            self.install_combo.setEnabled(False)
            self.status.setText(tr(
                "No Anno installation was detected. Choose your game folder."
            ))

    def _row_of(self, game: str) -> int:
        """The row of the list holding ``game``'s install, or 0: none was
        detected, or more than one, which is a choice nobody has made yet."""
        rows = [
            row
            for row, install in enumerate(self._installs, start=1)
            if launch.is_install_of(install.path, game)
        ]
        return rows[0] if len(rows) == 1 else 0

    def _name_the_folder_of(self, game: str) -> None:
        """Show, in the folder box while it is empty, where ``game`` is
        installed as a rule: the game the dialog is open on, which with none
        asked for and none read yet is the one the Studio starts on."""
        self.folder_edit.setPlaceholderText(
            "…\\steamapps\\common\\%s" % games.get(game).install_folder  # i18n: keep - a path
        )

    @staticmethod
    def _is_install(folder: str, game: str) -> bool:
        return bool(folder) and launch.is_install_of(Path(folder), game)

    def _open_on_no_folder(self, game: str) -> None:
        """Opened for ``game`` with no install of it to pick: nothing chosen
        and nothing read, and where none was detected, that said."""
        # Filling the list in picked the one install there is.
        self.install_combo.setCurrentIndex(0)
        self.folder_edit.clear()
        self._auto_detected_path = None
        if any(launch.is_install_of(install.path, game) for install in self._installs):
            # Several copies of it: which one is still to be chosen.
            self.status.clear()
            return
        self.status.setText(tr(
            "No installation of %(game)s was found. Use Browse to choose its "
            "folder, then scan its archives.",
            game=games.get(game).label,
        ))

    def _read_from_another_copy(self, last_folder: str, game: str, row: int) -> bool:
        """Whether ``last_folder`` is an install of ``game`` other than the
        detected one in ``row``: a second copy, chosen by hand the last time,
        which is the choice a returning user is not second-guessed on."""
        detected = self._installs[row - 1].path
        return self._is_install(last_folder, game) and not disk.same_file(
            last_folder, detected
        )

    # -- actions ------------------------------------------------------------
    def _sync_scan_button(self) -> None:
        """Scan archives offered while there is a folder to scan and no scan
        or extraction is running, which the progress bar shows."""
        self.scan_button.setEnabled(
            bool(self.folder_edit.text().strip()) and self.progress.isHidden()
        )

    def _install_picked(self) -> None:
        path = self.install_combo.currentData()
        if path:
            self.folder_edit.setText(path)

    def _install_chosen(self, row: int) -> None:
        """A detected install picked from the list: its folder, scanned at once.

        Which folder was the one thing left to decide before a scan, and a
        scan reads archive directories and nothing else.  Picking the install
        already in the box counts: it is asked for again.
        """
        self._install_picked()
        if self.install_combo.itemData(row) and not self._extracting():
            self._scan()

    def _folder_entered(self) -> None:
        """Enter in the folder box: the folder it names, scanned."""
        if not self._extracting():
            self._scan()

    def _extracting(self) -> bool:
        """Whether an extraction is running, under which no scan starts: its
        files are the last scan's, and a new scan would put the buttons back
        before it is done."""
        return self._extract_worker is not None and self._extract_worker.isRunning()

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Select the Anno game folder"), self.folder_edit.text()
        )
        if chosen:
            self.folder_edit.setText(chosen)

    def _browse_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Where should the extracted files go?"), self.dest_edit.text()
        )
        if chosen:
            self.dest_edit.setText(chosen)

    def _suggest_destination(self, game: str) -> None:
        """Point "Extract to" at this game's own folder -- unless the field
        holds something the user actually chose.

        Scanning a different game than last time must not leave the field
        naming the *previous* one: that is exactly the kind of mismatch that
        makes someone go looking through the wrong folder later.  So a field
        left at Anno 1800's suggestion is just as much a suggestion when the
        user switches to scanning Anno 117: see
        :func:`core.gamefiles.suggested_root`.
        """
        current = self.dest_edit.text().strip()
        suggested = gamefiles.suggested_root(current, game)
        if suggested != current:
            self.dest_edit.setText(suggested)

    def _scan(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder:
            return  # Enter in the empty box: the status says what to do
        if not Path(folder).is_dir():
            self.status.setText(tr("That folder does not exist."))
            return
        if gamefiles.maindata_folder(Path(folder)) is None:
            self.status.setText(tr(
                "No .rda archives in that folder. Pick the game folder itself "
                "(the one containing maindata)."
            ))
            return

        self._stop_scan()
        self.scan_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        self.table.hide()
        self.progress.show()
        self.progress.setRange(0, 100)
        self.status.setText(tr("Reading archive directories…"))

        self._scan_worker = ScanWorker(Path(folder), self)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.succeeded.connect(self._on_scan_done)
        self._scan_worker.failed.connect(self._on_failed)
        self._scan_worker.start()

    def _stop_scan(self) -> None:
        """Give up a scan still running: another folder is about to be read.

        The button that starts a scan is off while one runs, but the list of
        installs is not, and picking from it starts one.
        """
        worker = self._scan_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    def _given_up(self) -> bool:
        """Whether what is being answered comes from a scan since given up.

        Stopped, it still reports what it had read so far, and that arrives
        after the next scan has started: taken as an answer, it would offer
        to extract one folder's files under another folder's name.
        """
        sender = self.sender()
        return (
            sender is not None
            and sender is not self._scan_worker
            and sender is not self._extract_worker
        )

    def _on_scan_progress(self, done: int, total: int, name: str) -> None:
        if self._given_up():
            return
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        if name:
            self.status.setText(tr("Reading %(archive)s  (%(done)s of %(total)s)",
                                   archive=name, done=number(done + 1), total=number(total)))

    def _on_scan_done(self, result) -> None:
        if self._given_up():
            return
        self.progress.hide()
        self._sync_scan_button()
        self.result_scan = result
        self._scanned_folder = self._scan_worker.folder if self._scan_worker else None
        # A scan has run: whatever keeps the button off now, the status says.
        self.extract_button.setToolTip("")

        if not result.best_assets:
            self.status.setText(trn(
                "Scanned %(n)s archive but found no assets.xml. Is this an Anno install?",
                "Scanned %(n)s archives but found no assets.xml. Is this an Anno install?",
                result.archives_scanned,
            ))
            return

        best = result.best_assets
        game = games.get(best.game).label
        # One sentence after another, each whole.
        said = [tr(
            "Found %(file)s in %(archive)s (%(game)s, %(size)s MB).",
            file=best.filename, archive=best.archive.name, game=game,
            size=number(best.size / 1e6),
        )]
        if result.schema:
            said.append(tr(
                "Also taking %s, so the editor knows what fields exist.",
                join_list(sorted(f.filename for f in result.schema)),
            ))
        if result.superseded:
            said.append(trn(
                "%(n)s older copy of these files in earlier archives was skipped.",
                "%(n)s older copies of these files in earlier archives were skipped.",
                result.superseded,
            ))
        if result.archives_failed:
            said.append(trn(
                "%(n)s archive could not be read.", "%(n)s archives could not be read.",
                len(result.archives_failed),
            ))
        self.status.setText("  ".join(said))

        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(tr("None", context="language"), "")
        for language in result.languages:
            self.language_combo.addItem(language, language)
        default = self.language_combo.findData("English")
        self.language_combo.setCurrentIndex(default if default >= 0 else 0)
        self.language_combo.blockSignals(False)
        self.language_row.setVisible(bool(result.languages))

        self._suggest_destination(best.game)
        # The game read is the one the dialog is on now, whichever it opened on.
        self._name_the_folder_of(best.game)
        self.dest_row.show()
        self.extract_button.setEnabled(True)
        self._refresh_table()
        # Several rows just appeared (language, destination, the findings
        # table); grow the window to fit them rather than leaving any behind a
        # scrollbar or clipped at the bottom.
        self.adjustSize()

    def _selected_texts(self):
        if self.result_scan is None:
            return None
        language = self.language_combo.currentData()
        return self.result_scan.texts_for(language) if language else None

    def _refresh_table(self) -> None:
        if self.result_scan is None:
            return
        rows = [self.result_scan.best_assets]
        texts = self._selected_texts()
        if texts is not None:
            rows.append(texts)

        self.table.setRowCount(len(rows))
        for r, found in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(found.filename))
            self.table.setItem(r, 1, QTableWidgetItem(found.archive.name))
            self.table.setItem(r, 2, sort_as(
                QTableWidgetItem(tr("%s MB", number(found.size / 1e6, 1))), found.size,
            ))
            self.table.setItem(r, 3, QTableWidgetItem(found.path))
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()
        was_visible = self.table.isVisible()
        self._size_table_to_contents()
        self.table.show()
        if was_visible:
            # A later language pick can add or drop the texts row; adjustSize()
            # in _on_scan_done only covers the scan's own initial reveal.
            self.adjustSize()

    def _size_table_to_contents(self) -> None:
        """Fit the table to exactly its rows, so nothing needs a scrollbar.

        There are only ever one or two findings here, so growing the dialog to
        show all of them beats hiding a row behind a scrollbar in a box sized
        for a row count that never actually occurs.
        """
        self.table.resizeRowsToContents()
        height = self.table.horizontalHeader().height() + 2 * self.table.frameWidth()
        for row in range(self.table.rowCount()):
            height += self.table.rowHeight(row)
        self.table.setFixedHeight(height)

    def _extract(self) -> None:
        if self.result_scan is None or not self.result_scan.best_assets:
            return
        destination = self.dest_edit.text().strip()
        if not destination:
            self.status.setText(tr("Choose somewhere to extract to."))
            return

        self.extract_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)  # indeterminate
        self.status.setText(tr("Extracting…"))

        self._extract_worker = ExtractWorker(
            self.result_scan.best_assets,
            self._selected_texts(),
            self.result_scan.schema,
            Path(destination),
            self,
        )
        self._extract_worker.progress.connect(
            lambda p: self.status.setText(tr("Extracting %s…", p))
        )
        self._extract_worker.succeeded.connect(self._on_extracted)
        self._extract_worker.failed.connect(self._on_failed)
        self._extract_worker.start()

    def _on_extracted(self, assets_path, texts_path, schema_paths) -> None:
        worker = self.sender()
        if isinstance(worker, ExtractWorker) and worker.cancelled():
            # Cancelled just as it finished: the dialog has been closed.
            return
        best = self.result_scan.best_assets if self.result_scan else None
        self.source_archive = best.archive.name if best else ""
        self.game_folder = self._scanned_folder
        self.assets_path = Path(assets_path)
        self.texts_path = Path(texts_path) if texts_path else None
        self.schema_paths = dict(schema_paths or {})
        self.accept()

    def _on_failed(self, message: str) -> None:
        if self._given_up():
            return
        self.progress.hide()
        self._sync_scan_button()
        self.extract_button.setEnabled(self.result_scan is not None)
        self.status.setText(message)

    # -- lifecycle ----------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        # A line edit leaves Enter unused, and the dialog then presses its
        # default button: in the folder box that was "Extract and load", of
        # the folder scanned before, whatever the box named by then.  There
        # Enter scans -- its returnPressed.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            self.focusWidget() is self.folder_edit
        ):
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        for worker in (self._scan_worker, self._extract_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(3000)
        super().reject()
