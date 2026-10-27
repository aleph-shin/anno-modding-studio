"""The game's own files, as the window loads them: its assets.xml built into
an index and handed to every tab, its texts, extracting both from the game's
archives, and remembering what was read so the next start opens it again.

``core.index`` builds and reads the index, ``core.gamefiles`` and
``core.rda`` take the files out of the game's archives; this is the window's
side -- the worker and its progress dialog, what happens to the open loadout
when the game changes, which texts go with which assets -- held as
``window.game_files`` (Stage 3.3 of docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

from ..core import builds, disk, games
from ..core import index as index_mod
from ..core.changes import is_own_mod
from ..core.i18n import number, tr, trn
from ..core.workspace import Workspace
from .import_dialog import ImportDialog
from .widgets import listing, wrappable_path
from .workers import IndexWorker, TextsWorker


def _base_fingerprint(source) -> str:
    """The assets.xml's fingerprint, or "" if it cannot be read just now.

    The same hash the index cache is keyed on, so reading it costs what the
    cache lookup already cost: a fifth of a second for the largest file.
    """
    try:
        return index_mod.fingerprint(Path(source))
    except OSError:
        return ""


class GameFilesController:
    """The window's game_files, moved off it; ``window`` is the MainWindow."""

    READ_GAME_KEY = "last_read_game"
    READ_ROOT_KEY = "last_read_root"
    # Set, for a game, while its texts stand cleared: see texts_cleared().
    TEXTS_CLEARED_KEY = "texts_cleared"

    def __init__(self, window) -> None:
        self.window = window
        # The texts file loaded into the index, if one is.
        self.texts_path = None
        # The worker building the index, and the one loading texts into it,
        # while each runs.
        self.worker = None
        self.texts_worker = None
        # What waits for the index being built: the archive it came out of,
        # a loadout read off disk to take up once it is open, and the
        # schema and texts read alongside.
        self.pending_archive = ""
        self.pending_loadout = None
        self.pending_schema = {}
        self.pending_texts = None
        # A game switch that has been agreed to but not carried out: the
        # loadout is cleared, and the install forgotten, only once the new
        # game's files are actually open. See confirm_game_switch.
        self.pending_reset = False
        self._game_folder_before = None
        self._progress = None
        self._reopen_on_failure = None
        self.status_base = tr("No assets.xml loaded.")
        self.texts_were_automatic = False

    def source_path(self):
        return Path(self.window.loadout.source_path) if self.window.loadout.source_path else None

    def open_game(self) -> str:
        """Which game's files are open, as opposed to which the loadout claims.

        The two agree the moment an index is adopted, and can stop agreeing
        before the next one is: a loadout carries its own game and is adopted
        first, so a base file that then never finishes indexing -- a rebuild
        cancelled at the progress dialog, or one that fails -- used to leave
        the Start tab naming the loadout's game and marking it "in use" while
        the index, the localisation and every GUID behind it were still the
        other game's.  Read off the index and there is nothing to disagree
        with: it says what is loaded because it *is* what is loaded.
        """
        if self.window.index is None:
            return ""
        return games.detect_from_path(Path(self.window.index.source_path))

    def open_assets(self, path=None, force: bool = False, asked: bool = False) -> None:
        """``asked`` means somebody has already put the switch to the user in
        better terms than the generic guard can -- opening a loadout names both
        files and both games, and being asked twice reads as the first answer
        not having taken."""
        if not path:
            start = self.window.remembered("last_assets_dir")
            chosen, _f = QFileDialog.getOpenFileName(
                self.window, tr("Select the game's assets.xml"), start,
                tr("XML files (*.xml);;All files (*)"),
            )
            if not chosen:
                return
            path = Path(chosen)
        path = Path(path)
        if not path.is_file():
            QMessageBox.warning(
                self.window, tr("Not found"), tr("%s does not exist.", wrappable_path(path))
            )
            return
        if not asked and not self.confirm_game_switch(
            games.detect_from_path(path), path.name
        ):
            return
        self.window.settings.setValue("last_assets_dir", str(path.parent))

        db_path = index_mod.cache_path_for(path)
        if db_path.exists() and not force:
            self.adopt_index(db_path, path, cached=True)
            return

        if self.window.index is not None and Path(self.window.index.db_path) == db_path:
            # Rebuilding the very file we already have open: our own read
            # connection holds a Windows file handle that the rebuild's final
            # replace cannot overwrite, so let go of it first. build_index()
            # never touches db_path until it has fully succeeded, so a failed
            # rebuild leaves the original file untouched -- reopening the same
            # connection afterwards is enough, no need to reload anything.
            # A reading has the file open as well, in a process of its own.
            self._hold_readings("rebuild")
            self.window.index.close()
            self._reopen_on_failure = (db_path, path)

        self._progress = QProgressDialog(
            tr("Indexing %s…\nThis happens once per version of the file.", path.name),
            tr("Cancel"),
            0,
            1000,
            self.window,
        )
        self._progress.setWindowTitle(tr("Building asset index"))
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setValue(0)

        self.worker = IndexWorker(path, db_path, self.window)
        self.worker.progress.connect(self._on_index_progress)
        self.worker.succeeded.connect(lambda db, n: self._on_index_done(db, path, n))
        self.worker.failed.connect(self._on_index_failed)
        self._progress.canceled.connect(self.worker.cancel)
        self.worker.start()

    def confirm_game_switch(self, detected: str, source_name: str) -> bool:
        """Whether it is OK to point the loadout at ``detected``'s assets.

        A no-op -- nothing to confirm, nothing to reset -- unless the game
        actually changes and there is something loaded that was built against
        the old one.  Anno 117's paths and Anno 1800's do not overlap, so
        nothing here carries over: not the loaded mods, which were read
        against the wrong dialect and GUID space, and not your own edits,
        which would go on "changing" values that do not exist in the new file.
        """
        if self.window.index is None or detected == self.window.loadout.game:
            return True
        if not self.window.loadout.layers and not self.window.loadout.authored_count:
            return True
        if not self.window.prompts.ask_switch_games(
            source_name, detected, self.window.loadout.game, len(self.window.loadout.layers),
        ):
            return False
        if not self.window.files.confirm_discard():
            return False
        self.leave_the_game()
        return True

    def leave_the_game(self) -> None:
        """What a switch to the other game's files costs, once it is agreed
        to: here, or by a question that stands in for this one, as opening a
        loadout of the other game asks its own."""
        # Agreed to, not done: a switch is not a switch until the other game's
        # index is open, and this used to clear the loadout on the strength of
        # the answer alone.  A build that then failed -- or that the user
        # cancelled at the progress dialog, having changed their mind, which
        # is the likeliest way to arrive here -- left them with neither game:
        # the work discarded and the old files still loaded.  adopt_index()
        # carries it out; _on_index_failed() puts back what it was about to
        # replace.
        self.pending_reset = True
        # Stale the moment the game changes: it names an install for the game
        # being left, and offering it as where this game's mods live is the
        # same bug this whole check exists to prevent.  Kept, so that a switch
        # which does not happen can have it back.
        self._game_folder_before = self.window.game_folder
        self.window.game_folder = None

    def _on_index_progress(self, pos: int, total: int, count: int) -> None:
        # Held locally: QProgressDialog.setValue() can pump a nested event
        # loop, which lets a near-simultaneous succeeded/failed signal run
        # _on_index_done()/_on_index_failed() -- and null out self._progress --
        # before this call returns. Re-reading the attribute afterwards would
        # then crash on a build that finishes fast enough for that to happen,
        # a small sample file among them.
        progress = self._progress
        if progress is None:
            return
        fraction = int(1000 * pos / total) if total else 0
        progress.setValue(min(fraction, 999))
        progress.setLabelText(trn(
            "Indexing…  %(percent)s%%   ·   %(n)s asset found",
            "Indexing…  %(percent)s%%   ·   %(n)s assets found",
            count, percent=number(100.0 * pos / total if total else 0),
        ))

    def _on_index_done(self, db_path: str, source: Path, count: int) -> None:
        if self._progress:
            self._progress.close()
            self._progress = None
        self._reopen_on_failure = None
        self.adopt_index(Path(db_path), source, cached=False, count=count)

    def _on_index_failed(self, message: str) -> None:
        if self._progress:
            self._progress.close()
            self._progress = None
        reopen = self._reopen_on_failure
        self._reopen_on_failure = None
        if reopen is not None:
            self._reopen_index_connection(*reopen)
            self._release_readings("rebuild")
        waiting = self._forget_pending_load()
        if message == "cancelled":
            self.window.status_label.setText(
                tr("Indexing cancelled.")
                if waiting is None
                else tr("Indexing cancelled, so %s was not opened.",
                        self.window.files.name_of(waiting[0]))
            )
            return
        QMessageBox.critical(self.window, tr("Could not index that file"), message)
        if waiting is not None:
            self.window.status_label.setText(tr(
                "%s was not opened: the file it is built against could not be "
                "read.", self.window.files.name_of(waiting[0])
            ))

    def _forget_pending_load(self):
        """Let go of everything staged for a load that did not happen.

        Each of these is an answer about the file that was being opened: its
        localisation, its schema, the archive it came out of, the loadout
        being opened against it, the install it belongs to, and the work that
        was going to be cleared to make room.  A build that fails or is
        cancelled means not one of them applies -- but every one of them used
        to be left armed, to be picked up by whatever index was adopted next,
        or spent already and never put back.

        Returns the loadout that was waiting, so the caller can say which one
        did not open.
        """
        waiting, self.pending_loadout = self.pending_loadout, None
        self.pending_texts = None
        self.pending_schema = {}
        self.pending_archive = ""
        if self.pending_reset:
            # The switch did not happen, so neither does anything it was going
            # to cost: the loadout stands, and the install it came from is
            # still where this game's mods go.
            self.pending_reset = False
            self.window.game_folder = self._game_folder_before
            self._game_folder_before = None
        return waiting

    def _reopen_index_connection(self, db_path: Path, source: Path) -> None:
        """Pick the same index back up after closing it around a failed reindex.

        Not a fresh load: the file on disk is exactly what it was before the
        rebuild was attempted, so this only needs to replace the closed
        connection, not run the whole adoption flow (which would also reset
        the active tab and other state the user did nothing to disturb).
        """
        self.window.index = index_mod.AssetIndex(db_path, source)
        if self.window.workspace is not None:
            self.window.workspace.index = self.window.index

    def _hold_readings(self, why: str) -> None:
        """No reading in the background while the index is closed, built
        again or written to: see :meth:`ReadingsController.hold`."""
        readings = getattr(self.window, "readings", None)
        if readings is not None:
            readings.hold(why)

    def _release_readings(self, why: str) -> None:
        readings = getattr(self.window, "readings", None)
        if readings is not None:
            readings.release(why)

    def adopt_index(self, db_path: Path, source: Path, cached: bool, count: int = 0) -> None:
        # The index is closed and another opened: a reading of the old one
        # stops first, and what is in doubt is read against the new one.
        self._hold_readings("adopt")
        try:
            self._adopt_index(db_path, source, cached, count)
        finally:
            self._release_readings("rebuild")
            self._release_readings("adopt")

    def _adopt_index(self, db_path: Path, source: Path, cached: bool, count: int = 0) -> None:
        self.stop_texts_worker()
        before = None
        if self.window.index is not None:
            before = self.window.index.db_path
            self.window.index.close()
        self.window.index = index_mod.AssetIndex(db_path, source)

        # The switch these files are the other half of, if there was one: the
        # work agreed to be lost goes now, with somewhere to have arrived.
        if self.pending_reset:
            self.pending_reset = False
            self._game_folder_before = None
            self.window.reset_loadout()

        # Before anything is measured against it: a loadout waiting on this
        # file is what the workspace, the change list and the editor are all
        # about to be built around.
        waiting, self.pending_loadout = self.pending_loadout, None
        if waiting is not None:
            self.window.adopt_loadout(*waiting)

        self.window.loadout.source_path = str(source)

        detected = games.detect_from_path(source)
        self.window.loadout.game = detected
        # Provenance for the generated header. Consumed here so a later manual
        # file pick does not inherit the archive name from an earlier import.
        self.window.loadout.base_archive = self.pending_archive
        self.pending_archive = ""
        self.window.loadout.base_assets = self.window.index.asset_count
        self.window.loadout.base_fingerprint = _base_fingerprint(source)
        self.window.loadout.base_update, self.window.loadout.base_game_version = self._update_of(
            source, self.window.loadout.base_fingerprint
        )

        self.window.workspace = Workspace(self.window.index, self.window.loadout)
        # The editor starts over on a new index, and the graph and the trail
        # they share go with it: GUIDs from the last one may not be in this one.
        self.window.pending_in_editor = self.window.pending_in_refs = None
        self.window.editor.set_index(self.window.workspace)
        self.window.file_panel.set_source(self.window.workspace)
        self.window.references.clear()
        self.window.references.set_index(self.window.workspace)
        self.window.changes_panel.set_index(self.window.workspace)
        # The workspace, not the bare index: verification still checks a change
        # against the shipped asset, but a mod loaded on top of others has to be
        # checked against the document those others leave behind.
        self.window.output.set_index(self.window.workspace)
        self.window.output.set_game(detected)
        self.window.output.set_loader_finder(self.window.loader_check.game_loader)
        # A new version of the extension is exactly what the probes are for.
        self.window.loader_check.probe_loader()
        self.window.loader_check.ask_newest_loader()
        self.window.scan_elsewhere()
        if waiting is not None:
            self.window.output.load_loadout_fields()
        self.window.texts_action.setEnabled(True)
        self.window.reindex_action.setEnabled(True)
        self.window.new_asset_action.setEnabled(True)
        self.window.load_mods_action.setEnabled(True)
        self.window.library_add_action.setEnabled(True)
        self.window.get_mod_action.setEnabled(True)
        self.window.loadout_mods_action.setEnabled(True)
        self.window.convert_action.setEnabled(True)
        self.window.put_back_action.setEnabled(True)
        self.window.undo_convert_action.setEnabled(True)
        self.window.refer_action.setEnabled(True)
        self.window.sync_action.setEnabled(True)
        # Play is the window's to switch, with the title: MainWindow._show_play.
        self.window.check_patterns_action.setEnabled(True)
        self.window.new_pattern_action.setEnabled(True)
        # The file named is the one read into *this* index: another index's
        # texts came from wherever it was loaded, which is not known here.
        if self.window.index.text_count == 0 or not disk.same_file(before, db_path):
            self.texts_path = None
        self.window.start.refresh_source_panel()
        # Not a jump to the editor: the game files loading is Start's first
        # half, and its second half -- which of the three ways you are working
        # -- has not been asked yet.  Answering that is what moves you on, in
        # choose_aim(), so somebody who lands here from "Open loadout" or a
        # reindex sees the question rather than skipping straight past it.

        game = " ".join(filter(None, (games.get(detected).label, self.window.loadout.base_update)))
        if cached:
            self.status_base = trn(
                "%(file)s   ·   %(n)s asset loaded from cache   ·   %(game)s",
                "%(file)s   ·   %(n)s assets loaded from cache   ·   %(game)s",
                self.window.index.asset_count, file=source.name, game=game,
            )
        else:
            self.status_base = trn(
                "%(file)s   ·   %(n)s asset indexed   ·   %(game)s",
                "%(file)s   ·   %(n)s assets indexed   ·   %(game)s",
                self.window.index.asset_count, file=source.name, game=game,
            )
        stale = self.stale_builds()
        if stale:
            self.status_base = tr(
                "%(status)s   ·   the game data is newer than your last build of %(mods)s",
                status=self.status_base, mods=listing(stale),
            )
        self._set_status(self.window.index.text_count)
        self.window.update_title()
        self.window.mark_output_dirty()

        if self.pending_schema:
            schema, self.pending_schema = self.pending_schema, {}
            self._hold_readings("schema")
            try:
                templates, defaults = self.window.index.load_schema_files(
                    schema.get("templates.xml"), schema.get("properties.xml")
                )
            except Exception as exc:  # noqa: BLE001 - schema is optional
                self.window.status_label.setText(tr("Could not read the schema files: %s", exc))
            else:
                if templates or defaults:
                    self.status_base = trn(
                        "%(status)s   ·   %(n)s template",
                        "%(status)s   ·   %(n)s templates",
                        templates, status=self.status_base,
                    )
                    self._set_status(self.window.index.text_count)
            self._release_readings("schema")

        if self.pending_texts:
            pending, self.pending_texts = self.pending_texts, None
            self.open_texts(pending)
        elif self.window.index.text_count == 0:
            # A fresh index has no texts in it yet. If the last ones loaded are
            # still on disk, put them back rather than making the user notice
            # every name has reverted to its internal form and go find the file.
            remembered = self.remembered_texts()
            if remembered is not None:
                self.open_texts(remembered, automatic=True)

        # A fresh index knows nothing of the mods' names yet.
        self.refresh_mod_names()
        # A loadout that was waiting on this file is open now, and only now:
        # the game it is for is settled, which is what says where its mods go.
        if waiting is not None:
            self.window.modio.offer_missing_mods()
            self.window.refresh_truth()

    def _update_of(self, source, fingerprint: str) -> tuple:
        """``(update, version)`` of the game data at ``source``, or ``("", "")``.

        Asked of the game's log only when ``source`` is the extraction last
        read from the game and the game has not changed since -- otherwise
        what is installed now says nothing about this file -- and only when
        the log itself is current.  What is learnt is kept against the file's
        fingerprint, so the same data keeps its name after the game moves on.
        """
        from ..core import gamefiles, gameversion

        game = self.window.loadout.game
        stamp, state, _why = self.last_read(game)
        if stamp is not None and state == gamefiles.READ_CURRENT and disk.same_file(
            stamp.get("assets"), source
        ):
            build = gameversion.installed(stamp.get("game_folder") or None, game)
            if build is not None:
                gameversion.remember(fingerprint, build)
                return build.update, build.version
        known = gameversion.remembered(fingerprint)
        if known is not None:
            return known["update"], known["version"]
        return "", ""

    def stale_builds(self) -> list:
        """Your mods whose last build was made on other game data than now."""
        names = []
        for origin in self.window.loadout.builds:
            if builds.stale(self.window.loadout, origin) is None:
                continue
            if is_own_mod(origin):
                names.append(self.window.loadout.own.name or tr("your mod"))
            else:
                layer = self.window.loadout.layer(origin)
                names.append(layer.label if layer is not None else origin)
        return names

    def texts_cleared(self, game: str = "") -> bool:
        """Whether ``game``'s texts -- the open game's, unnamed -- were
        cleared and none chosen since.

        Clearing is kept, as the file loaded is, because the Studio puts
        texts back by itself: the file remembered into an index with none,
        and the ones a read of the game took whenever its files are opened.
        With nothing kept, a clearing lasted until the next launch.  It is
        kept by game, so one game's does not empty the other's.
        """
        key = "%s:%s" % (self.TEXTS_CLEARED_KEY, game or self.window.loadout.game)
        return str(self.window.settings.value(key) or "") == "1"

    def _set_texts_cleared(self, cleared: bool) -> None:
        key = "%s:%s" % (self.TEXTS_CLEARED_KEY, self.window.loadout.game)
        if cleared:
            self.window.settings.setValue(key, "1")
        else:
            self.window.settings.remove(key)

    def remembered_texts(self):
        """The texts file to restore into a freshly built index, if any.

        The exact file, when one has been loaded since the Studio started
        recording it.  Failing that, the folder the last one came from -- but
        only when it holds a single candidate: a game ships a dozen languages
        and quietly loading German for an English speaker is worse than
        loading nothing at all.  And none at all once they were cleared.
        """
        if self.texts_cleared():
            return None
        remembered = self.window.remembered_file("last_texts_file")
        if remembered and Path(remembered).is_file():
            return Path(remembered)
        folder = self.window.remembered("last_texts_dir")
        if not folder or not Path(folder).is_dir():
            return None
        try:
            candidates = sorted(Path(folder).glob("texts_*.xml"))
        except OSError:
            return None
        return candidates[0] if len(candidates) == 1 else None

    def import_from_game(self, game: str = "") -> None:
        """Read the game's archives and extract just the files we need.

        ``game`` is which one the user asked for, from the Start tab's list --
        so the dialog opens on that game's installation rather than on whatever
        was read last, which is the difference between switching games and
        being asked to find one.
        """
        remembered = ""
        if game:
            remembered = self.window.remembered("last_game_folder:%s" % game)
            if not remembered:
                stamp, _state, _why = self.last_read(game)
                folder = (stamp or {}).get("game_folder") or ""
                remembered = folder if folder and Path(folder).is_dir() else ""
        dialog = ImportDialog(
            self.window,
            last_folder=remembered or self.window.remembered("last_game_folder"),
            last_destination=self.window.remembered("last_extract_dir"),
            # A row's own game.  Without it the dialog had only a folder to
            # go on, and with nothing remembered and two games installed it
            # opened on neither.
            game=game,
        )
        if dialog.exec() != int(dialog.DialogCode.Accepted) or not dialog.assets_path:
            return
        self.window.settings.setValue("last_game_folder", dialog.folder_edit.text())
        self.window.settings.setValue("last_extract_dir", dialog.dest_edit.text())

        # Checked here too, before self.window.game_folder changes: declining inside
        # open_assets()'s own check further down would still leave it pointing
        # at the other game's install, which is the exact bug this prevents.
        detected = games.detect_from_path(dialog.assets_path)
        if not self.confirm_game_switch(detected, Path(dialog.assets_path).name):
            return

        # Applied once the index for the new base file is ready.
        self.pending_texts = dialog.texts_path
        self.pending_schema = dict(dialog.schema_paths or {})
        self.pending_archive = dialog.source_archive
        self.window.game_folder = dialog.game_folder
        # What was taken, and what the game looked like when it was taken, so
        # the next launch can tell whether reading it again would change
        # anything. See gamefiles.read_state.
        self._remember_read(dialog)
        # Asked above, before game_folder moved; the guard in there would
        # only put the same question again, which reads as the first answer
        # not having taken.
        self.open_assets(dialog.assets_path, asked=True)

    def _remember_read(self, dialog) -> None:
        from ..core import gamefiles

        game = games.detect_from_path(dialog.assets_path)
        root = dialog.dest_edit.text().strip()
        try:
            gamefiles.record_read(
                root,
                game=game,
                game_folder=dialog.game_folder,
                assets=dialog.assets_path,
                texts=dialog.texts_path,
                schema=dialog.schema_paths,
                source_archive=dialog.source_archive,
            )
        except OSError:
            return  # a stamp that cannot be written is not worth stopping for
        self.window.settings.setValue("%s:%s" % (self.READ_ROOT_KEY, game), root)
        self.window.settings.setValue(self.READ_GAME_KEY, game)
        # Per game as well as globally: the next time this game is asked for,
        # the dialog should open on *its* installation.
        self.window.settings.setValue(
            "last_game_folder:%s" % game, str(dialog.game_folder or "")
        )

    def last_read(self, game: str = ""):
        """``(stamp, state, why)`` for the last extraction of ``game``.

        Without a game, the one read last -- which is what a fresh launch has
        to go on.
        """
        from ..core import gamefiles

        game = game or str(self.window.settings.value(self.READ_GAME_KEY) or "")
        if not game:
            return None, gamefiles.READ_GONE, tr("nothing has been read from the game yet")
        root = str(self.window.settings.value("%s:%s" % (self.READ_ROOT_KEY, game)) or "")
        # The default place after the remembered one, for a read made before
        # this was remembered, and then the default as it was before the
        # extracted files left Documents.
        stamp = None
        for place in (root, gamefiles.default_extract_root(game), gamefiles.former_extract_root(game)):
            found = gamefiles.read_stamp(place) if place else None
            # Only a read of this game.  A folder two games were read into
            # holds the stamp of the later one, and opening that for the
            # other is opening the files already open: "Use these files"
            # did nothing but load the texts again.
            if found is not None and (found.get("game") or game) == game:
                stamp = found
                break
        state, why = gamefiles.read_state(stamp)
        return stamp, state, why

    def restore_last_read(self, game: str = "") -> bool:
        """Open what was last extracted, without reading the archives again.

        The whole of loading Anno 1800, minus the two slow parts: no archive
        scan, no extraction, and an index that is already built because the
        file it was built from has not been rewritten.
        """
        from ..core import gamefiles

        stamp, state, _why = self.last_read(game)
        if stamp is None or state == gamefiles.READ_GONE:
            return False
        assets = Path(stamp["assets"])
        # Asked here, before anything below moves, for the same reason
        # import_from_game() asks: open_assets() asks too, but by then
        # game_folder, the export destination and the pending texts/schema/
        # archive all name the game just declined, and nothing puts them back.
        # That is the desync -- the Start tab going on saying one game while
        # everything hanging off it had quietly become the other's.
        if not self.confirm_game_switch(games.detect_from_path(assets), assets.name):
            return False
        texts = stamp.get("texts") or ""
        if self.texts_cleared(games.detect_from_path(assets)):
            texts = ""
        self.pending_texts = Path(texts) if texts and Path(texts).is_file() else None
        self.pending_schema = {
            name: Path(path)
            for name, path in (stamp.get("schema") or {}).items()
            if Path(path).is_file()
        }
        self.pending_archive = stamp.get("source_archive", "")
        folder = stamp.get("game_folder") or ""
        self.window.game_folder = Path(folder) if folder else None
        # Asked at the top, for the same reason and with the same effect.
        self.open_assets(assets, asked=True)
        # Whether *these* files are the ones open, not whether any are: the
        # index that was already loaded made a restore that never happened --
        # declined, cancelled, or still being built -- report success.
        opened = self.window.index is not None and Path(self.window.index.source_path) == assets
        if opened:
            # The game a launch comes back to is the one last open, not the
            # one last extracted: choosing Anno 1800 on the Start tab used to
            # leave the next launch on Anno 117, whose files were read later.
            self.window.settings.setValue(self.READ_GAME_KEY, stamp.get("game") or game)
        return opened

    def restore_on_launch(self) -> None:
        """Put the last game files back, when that costs nothing.

        Only when the extraction still matches the installed game *and* its
        index is already built: this is the fast path or it is nothing.  A
        rebuild is minutes of work nobody asked for at startup, so that case
        is left to the button on the Start tab, which says why it is there.

        The game files only.  The loadout you were last in used to come back
        with them, on the same argument; but reading the files is a cache,
        with nothing to decide, and which loadout to be in is the decision
        the Start tab exists to ask.  A new session asks it again, with the
        ones you had open lately one click away on that tab.
        """
        from ..core import gamefiles

        stamp, state, _why = self.last_read()
        if stamp is None or state != gamefiles.READ_CURRENT:
            return
        assets = Path(stamp["assets"])
        try:
            if not index_mod.cache_path_for(assets).exists():
                return
        except OSError:
            return
        if self.restore_last_read(stamp.get("game", "")):
            # After the whole line, names counted and all, not the base alone.
            self.window.status_label.setText(tr(
                "%s   ·   the game has not changed since you last read it.",
                self._status_line(self.window.index.text_count),
            ))

    def open_texts(self, path=None, automatic: bool = False) -> None:
        if self.window.index is None:
            return
        # A restore the user did not ask for reports failure quietly: a
        # modal error about a file they never picked, on a window they
        # just opened, is startling and not actionable.
        self.texts_were_automatic = automatic
        if path:
            chosen = str(path)
        else:
            start = self.window.remembered("last_texts_dir")
            chosen, _f = QFileDialog.getOpenFileName(
                self.window, tr("Select texts_<language>.xml"), start,
                tr("XML files (*.xml);;All files (*)"),
            )
            if not chosen:
                return
        if not automatic:
            # Chosen, or taken by a read of the game: either is asking for
            # texts again.
            self._set_texts_cleared(False)
        self.window.settings.setValue("last_texts_dir", str(Path(chosen).parent))
        self.texts_path = Path(chosen)

        self._progress = QProgressDialog(
            tr("Loading localisation texts…"), "", 0, 0, self.window)
        self._progress.setWindowTitle(tr("Loading texts"))
        self._progress.setCancelButton(None)
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.show()

        self.stop_texts_worker()
        # It writes into the index, which a reading in its own process reads.
        self._hold_readings("texts")
        self.texts_worker = TextsWorker(self.window.index, Path(chosen), self.window)
        self.texts_worker.succeeded.connect(self.on_texts_loaded)
        self.texts_worker.failed.connect(self.on_texts_failed)
        self.texts_worker.start()

    def clear_texts(self) -> None:
        if self.window.index is None:
            return
        self._hold_readings("texts")
        try:
            self.window.index.clear_texts()
        finally:
            self._release_readings("texts")
        self.texts_path = None
        self._set_texts_cleared(True)
        self.refresh_mod_names()
        self.window.editor.refresh_search()
        self._set_status(0)
        self.window.start.refresh_source_panel()

    def stop_texts_worker(self) -> None:
        """Let any in-flight texts load finish before the index moves.

        It writes straight into the index's own connection from another
        thread, so closing or replacing that index while it runs takes the
        connection out from under it -- which crashes the process rather than
        raising anything catchable.
        """
        worker = self.texts_worker
        if worker is None:
            return
        self.texts_worker = None
        try:
            worker.succeeded.disconnect()
            worker.failed.disconnect()
        except (RuntimeError, TypeError):
            pass
        if worker.isRunning():
            worker.wait(30000)
        self._release_readings("texts")

    def on_texts_loaded(self, count: int) -> None:
        if self._progress:
            self._progress.close()
            self._progress = None
        self._release_readings("texts")
        if count and self.texts_path:
            # Remembered so a rebuilt index can restore them by itself: the
            # texts live in the cache, so re-indexing (after a game patch, or a
            # Studio update that changes the cache format) otherwise drops every
            # localised name with nothing on screen saying why.
            self.window.settings.setValue("last_texts_file", str(self.texts_path))
        # The mods' names are read in the language just loaded.
        self.refresh_mod_names()
        self.window.editor.refresh_search()
        self._set_status(count)
        self.window.start.refresh_source_panel()

    def on_texts_failed(self, message: str) -> None:
        if self._progress:
            self._progress.close()
            self._progress = None
        self._release_readings("texts")
        self.texts_path = None
        if self.texts_were_automatic:
            self.texts_were_automatic = False
            self.window.settings.remove("last_texts_file")
            self.window.status_label.setText(
                tr("Could not reload the last localisation texts: %s", message)
            )
            self.window.start.refresh_source_panel()
            return
        QMessageBox.critical(self.window, tr("Could not load texts"), message)

    def _set_status(self, text_count: int = 0) -> None:
        self.window.status_label.setText(self._status_line(text_count))

    def _status_line(self, text_count: int = 0) -> str:
        if not text_count:
            return self.status_base
        return trn(
            "%(status)s   ·   %(n)s localised name",
            "%(status)s   ·   %(n)s localised names",
            text_count, status=self.status_base,
        )

    def refresh_mod_names(self) -> None:
        """Read the names the mods that load give things, in the language the
        game's texts are loaded in -- see :mod:`core.texts`. Files are read
        once and held, so this is cheap to ask again after a switch."""
        if self.window.index is None or not isinstance(getattr(self.window.index, "texts_language", None), str):
            return
        from ..core import texts

        language = (
            self.window.index.texts_language
            or texts.language_of(self.window.remembered_file("last_texts_file") or "")
            or "english"
        )
        names = texts.build(self.window.loadout, language, self.window.index.base_text)
        if self.window.index.set_mod_names(names):
            self.window.editor.refresh_search()
