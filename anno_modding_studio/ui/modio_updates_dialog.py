"""Which of your loaded mods mod.io has a newer file for.

A row per loaded mod, ticked, saying what is installed and -- once asked --
what is on mod.io and how sure the Studio is that they are the same mod: see
:mod:`core.updates` for how that is decided.  Nothing is asked of mod.io
until **Check** is pressed, and then only about the mods ticked: opening the
dialog to see what it does, or to check two mods of four hundred, used to
set off a check of every one.

Each answer is shown as it comes.  Most of a check's time is the wait
between requests that keeps to mod.io's rate limit, not the Studio's own
work, so a row filled in as soon as its answer is in costs nothing and shows
what the results look like long before the last one.  The asking happens on
a thread so the window stays answerable, and **Stop** ends it between
requests, keeping what was answered.

A mod with an update can be brought up to date from here: its current file
is fetched, checked, and unpacked over the folder the game loads it from,
which is first moved aside whole so the update can be undone.  Not every row
can: a copy the game's own mod browser installed is that browser's to update,
and a mod bundled inside another is updated by updating its host.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import (
    disk,
    games,
    installplan,
    library,
    loaderlog,
    modfolder,
    modio,
    modio_cache,
    modzip,
    secrets,
    updates,
)
from ..core.i18n import date_text, number, tr, tr_noop, trn
from . import theme
from .modio_brand import branding
from .widgets import (
    ask,
    enter_activates,
    filter_box,
    rows_table,
    show_progress,
    sort_as,
    sortable,
    tick_shown,
    wrappable_path,
)
from .workers import ModioTaskWorker, UpdatesWorker

DOWNLOADS = ".modio-downloads"

COLUMNS = (
    "", tr_noop("Mod"), tr_noop("Installed"), tr_noop("On mod.io"),
    # Translators: a column heading -- when mod.io last updated the mod.
    tr_noop("Updated"), tr_noop("Status"), tr_noop("Matched by"),
)
TICK, NAME, INSTALLED, LATEST, UPDATED, STATUS, HOW = range(len(COLUMNS))
# What a row says before it is answered: not asked about, or asked about and
# waiting its turn.
NOT_CHECKED, WAITING = "not checked", "waiting"

STATUS_TEXT = {
    updates.UPDATE: tr_noop("Update available"),
    updates.CURRENT: tr_noop("Up to date"),
    updates.AHEAD: tr_noop("Newer than mod.io"),
    updates.DIFFERENT: tr_noop("Different version"),
    updates.UNKNOWN: tr_noop("No version to compare"),
    updates.UNMATCHED: tr_noop("Not found on mod.io"),
    updates.MISSING: tr_noop("No longer on mod.io"),
    NOT_CHECKED: tr_noop("Not checked"),
    # Translators: a mod ticked to be checked, whose turn has not come yet.
    WAITING: tr_noop("Waiting"),
}
# The order the Status column sorts in: what wants doing first.
RANK = {
    updates.UPDATE: 0, updates.DIFFERENT: 1, updates.AHEAD: 2, updates.MISSING: 3,
    updates.UNKNOWN: 4, updates.CURRENT: 5, updates.UNMATCHED: 6, WAITING: 7, NOT_CHECKED: 8,
}
HOW_TEXT = {
    # Translators: this and the next three fill the "Matched by" column: how
    # the Studio knows which mod on mod.io an installed mod is.
    updates.LOG: tr_noop("the loader's log"),
    updates.CACHE: tr_noop("the download cache"),
    # Translators: "Matched by" this loadout, which remembered the match.
    updates.LOADOUT: tr_noop("this loadout"),
    # Translators: "Matched by" the mod's name alone.
    updates.NAME: tr_noop("its name"),
    # Translators: "Matched by" the file the mod was installed from.
    updates.HASH: tr_noop("its file"),
}
HOW_TIP = {
    updates.LOG: tr_noop("The game loaded this mod out of the mod.io cache, and its log says which mod.io mod it is."),
    updates.CACHE: tr_noop("The in-game browser installed a copy of this ModID, and its state file says which mod.io mod that is."),
    updates.LOADOUT: tr_noop("This loadout remembered the match from an earlier check."),
    updates.NAME: tr_noop("A mod on mod.io has this name. That is the only "
                          "evidence, so check it is the right mod."),
    updates.HASH: tr_noop("The zip this mod was installed from is one of this "
                          "mod's files on mod.io, byte for byte."),
}


class ModioUpdatesDialog(QDialog):
    """The loaded mods, to be asked mod.io about, and what it said.

    Afterwards ``learned`` maps each origin whose mod.io mod was decided to
    that mod's id, for the caller to keep in the loadout, and ``verdicts``
    holds what was said of each mod checked.
    """

    def __init__(
        self,
        loadout,
        game: str,
        store: secrets.Store,
        parent=None,
        fetch=modio.transport,
        logged: Optional[Dict[str, int]] = None,
        cache=None,
        open_account: Optional[Callable[[], None]] = None,
        chunks=modio.stream,
        *,
        library_dir: Path,
        only=None,
    ):
        super().__init__(parent)
        self.loadout = loadout
        self.library_dir = Path(library_dir)
        self.game_key = game
        self.game = games.get(game)
        self.store = store
        self._fetch = fetch
        self._chunks = chunks
        self._open_account = open_account
        self.learned: Dict[str, int] = {}
        # What was said of each mod checked, by origin, in the order answered.
        self._verdicts: Dict[str, updates.Verdict] = {}
        # What Download and update did, in order, for the caller to re-read.
        self.installed: List[modzip.InstallResult] = []
        self._worker: Optional[UpdatesWorker] = None
        self._task: Optional[ModioTaskWorker] = None
        self._failed = False
        self.setWindowTitle(tr("Check mod.io for updates"))
        self.setMinimumSize(760, 460)
        # A mod row asks about its own mod (``only``); the menu, about all.
        wanted = None if only is None else set(only)
        self.matches = updates.identify(
            [layer for layer in loadout.layers if wanted is None or layer.origin in wanted],
            logged if logged is not None else loaderlog.logged_cache_ids(game),
            cache if cache is not None else updates.cache_index(game),
            library_dir=self.library_dir,
        )
        # A library copy unpacked from a zip can be made sure of by it.
        known = library.registry(self.library_dir)
        for match in self.matches:
            layer = loadout.layer(match.origin)
            name = library.copy_name(self.library_dir, layer.folder) if layer and layer.folder else ""
            match.zip_md5 = str(known.get(name, {}).get("zip_md5") or "")
        self._row_of = {match.origin: row for row, match in enumerate(self.matches)}
        self._build_ui()
        self._say_ready()
        self._refresh_check()

    @property
    def verdicts(self) -> List[updates.Verdict]:
        return list(self._verdicts.values())

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        # A loadout holds hundreds of mods, and type-ahead only finds a row
        # by the start of its name.
        self.filter_box = filter_box(tr("Filter mods…"), tr("Filter mods"), self._filter)
        outer.addWidget(self.filter_box)

        self.table = rows_table([tr(column) if column else "" for column in COLUMNS],
                                one_at_a_time=False)
        self.table.itemSelectionChanged.connect(self._refresh_open)
        enter_activates(self.table)
        self.table.itemActivated.connect(lambda _item: self._open_selected())
        self.table.setAccessibleName(tr("Loaded mods"))
        outer.addWidget(self.table, 1)
        # Why the mod chosen, which has an update, cannot be updated from
        # here. It was said only in the tooltip of a greyed-out button, and a
        # list of bundled mods read as a button that never worked.
        self.why_label = QLabel("")
        self.why_label.setObjectName("StepHint")
        self.why_label.setWordWrap(True)
        outer.addWidget(self.why_label)
        self._fill()
        self.sorting = sortable(self.table)
        self.tick_box = tick_shown(self.table, tr("Tick every mod shown"), self._ticks_changed)

        buttons = QHBoxLayout()
        buttons.addWidget(branding())
        self.account_button = QPushButton(tr("Set up mod.io &account…"))
        self.account_button.clicked.connect(self._account)
        self.account_button.setVisible(False)
        buttons.addWidget(self.account_button)
        self.check_button = QPushButton(tr("Chec&k"))
        self.check_button.setObjectName("Primary")
        self.check_button.clicked.connect(self._check_or_stop)
        buttons.addWidget(self.check_button)
        self.open_button = QPushButton(tr("&Open on mod.io"))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_selected)
        buttons.addWidget(self.open_button)
        self.update_button = QPushButton(tr("&Download and update"))
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self._update_ticked)
        buttons.addWidget(self.update_button)
        # Enter in the filter box presses none of them, Close below included:
        # a check is asked for, never set off by a key meant for the list.
        for button in (self.account_button, self.check_button, self.open_button, self.update_button):
            button.setAutoDefault(False)
        buttons.addStretch(1)
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.box.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        self.box.rejected.connect(self.reject)
        buttons.addWidget(self.box)
        outer.addLayout(buttons)
        # A tick changed is a different number of mods to check and to
        # update: listened for once the buttons that say so are there.
        self.table.itemChanged.connect(lambda _item: self._ticks_changed())

    # -- choosing ---------------------------------------------------------------
    def _filter(self, text: str) -> None:
        """Show the rows whose mod holds every word typed.  A hidden row
        keeps its tick: the filter narrows the list, not the choice."""
        wanted = text.casefold().split()
        for row, match in enumerate(self.matches):
            words = " ".join((match.name, match.origin)).casefold()
            self.table.setRowHidden(row, not all(word in words for word in wanted))
        self.tick_box.sync()
        self._refresh_open()

    def _ticked(self) -> List[updates.Match]:
        return [
            match for row, match in enumerate(self.matches)
            if self.table.item(row, TICK).checkState() == Qt.CheckState.Checked
        ]

    def _ticks_changed(self) -> None:
        self._refresh_check()
        self._refresh_open()

    def _checking(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _refresh_check(self) -> None:
        if self._checking():
            self.check_button.setText(tr("&Stop"))
            self.check_button.setEnabled(True)
            return
        count = len(self._ticked())
        self.check_button.setText(
            trn("Chec&k %(n)s mod", "Chec&k %(n)s mods", count) if count else tr("Chec&k")
        )
        busy = self._task is not None and self._task.isRunning()
        self.check_button.setEnabled(bool(count) and bool(self.store.load().api_key) and not busy)

    def _say_ready(self) -> None:
        if not self.matches:
            self.summary.setText(tr("No mods are loaded, so there is nothing to check."))
        elif not self.store.load().api_key:
            self.summary.setText(
                tr("Checking needs your mod.io API key, which is not set up yet.")
            )
            self.account_button.setVisible(True)
        else:
            self.summary.setText(tr(
                "Tick the mods to ask mod.io about, then check them. A mod with "
                "no mod.io id yet is looked up by its name, about one a second."
            ))

    # -- asking ---------------------------------------------------------------
    def _check_or_stop(self) -> None:
        if self._checking():
            self._worker.cancel()
            self.check_button.setEnabled(False)
            return
        self._begin()

    def _begin(self) -> None:
        key = self.store.load().api_key
        chosen = self._ticked()
        if not key or not chosen or self._checking():
            return
        for match in chosen:
            self._said(self._row_of[match.origin], WAITING)
        matched = sum(1 for match in chosen if match.matched)
        self.summary.setText(trn(
            "Asking mod.io about %(n)s mod: %(known)s already known to it, "
            "%(unknown)s to look up by name…",
            "Asking mod.io about %(n)s mods: %(known)s already known to it, "
            "%(unknown)s to look up by name…",
            len(chosen), known=number(matched),
            unknown=number(len(chosen) - matched),
        ))
        self.progress.setRange(0, len(chosen) - matched + 1)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._failed = False
        self._worker = UpdatesWorker(
            key, self.game.modio_game_id, chosen, parent=self, fetch=self._fetch
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.checked.connect(self._on_verdict)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_done)
        self._worker.start()
        self._refresh_check()
        self._refresh_open()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        show_progress(self.progress, done, total)
        if name:
            # The first step arrives as a whole sentence; each after it is
            # the name of a mod being searched for.
            self.summary.setText(name if not done else tr("Looking up %s…", name))

    def _on_task_progress(self, done: int, total: int, text: str) -> None:
        # An update says whole sentences, downloading included: not a name
        # to be looked up, as the check's are.
        show_progress(self.progress, done, total)
        if text:
            self.summary.setText(text)

    def _on_failed(self, message: str) -> None:
        self._failed = True
        self.progress.setVisible(False)
        self.summary.setText(tr("mod.io could not be asked: %s", message))

    def _on_verdict(self, verdict: updates.Verdict) -> None:
        """One mod answered: its row says so now, not when the last one is."""
        self._verdicts[verdict.origin] = verdict
        if verdict.modio_id:
            self.learned[verdict.origin] = verdict.modio_id
        row = self._row_of.get(verdict.origin)
        if row is not None:
            self._show(row, verdict)
        self._refresh_open()

    def _on_done(self) -> None:
        """The check ended, answered, stopped or failed."""
        self.progress.setVisible(False)
        # A mod whose turn never came, the check being stopped, was not
        # checked after all.
        for match in self.matches:
            if match.origin not in self._verdicts:
                self._said(self._row_of[match.origin], NOT_CHECKED)
        if not self._failed:
            self.summary.setText(self._summarise())
        # What wants doing first at the top, unless a sort was chosen.
        if self._verdicts and self.sorting.column < 0:
            self.sorting.sort(STATUS)
        self._refresh_check()
        self._refresh_open()

    def _on_checked(self, verdicts) -> None:
        """Every answer at once, as a check that is not shown answer by
        answer would give them."""
        for verdict in verdicts:
            self._on_verdict(verdict)
        self._on_done()

    def reject(self) -> None:
        for worker in (self._worker, self._task):
            if worker is not None and worker.isRunning():
                worker.cancel()
                worker.wait(5000)
        super().reject()

    # -- showing --------------------------------------------------------------
    def _fill(self) -> None:
        """A row per loaded mod, ticked, saying what is known before asking."""
        self.table.setRowCount(len(self.matches))
        for row, match in enumerate(self.matches):
            tick = QTableWidgetItem()
            tick.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            # A bundled mod starts unticked: it comes and goes with its host,
            # which is checked in its own row, and looked up by its own name
            # it is most often "not found", or found as a mod of the same name
            # that is not the copy the host carries.
            bundled = modfolder.is_bundled(match.origin)
            tick.setCheckState(Qt.CheckState.Unchecked if bundled else Qt.CheckState.Checked)
            tick.setData(Qt.ItemDataRole.UserRole, match.origin)
            self.table.setItem(row, TICK, tick)
            name = QTableWidgetItem(match.name)
            if bundled:
                name.setForeground(QColor(theme.TEXT_DIM))
                name.setToolTip(tr(
                    "Bundled inside %s. It is updated with that mod, so it starts unticked.",
                    self._host_label(match.origin),
                ))
            self.table.setItem(row, NAME, name)
            self.table.setItem(row, INSTALLED, QTableWidgetItem(match.installed_version))
            self.table.setItem(row, LATEST, QTableWidgetItem(""))
            self.table.setItem(row, UPDATED, _dated(0))
            self._said(row, NOT_CHECKED)
            self.table.setItem(row, HOW, self._how(match.how))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        for column in range(INSTALLED, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def _said(self, row: int, status: str) -> None:
        """What the Status cell of ``row`` says, and how it is drawn."""
        item = QTableWidgetItem(tr(STATUS_TEXT[status]) if status in STATUS_TEXT else status)
        sort_as(item, RANK.get(status, 9))
        if status == updates.UPDATE:
            item.setForeground(QColor(theme.CHANGED))
        elif status in (updates.UNMATCHED, updates.CURRENT, NOT_CHECKED, WAITING):
            item.setForeground(QColor(theme.TEXT_DIM))
        elif status in (updates.DIFFERENT, updates.MISSING):
            theme.mark(item, "warn")
        self.table.setItem(row, STATUS, item)

    @staticmethod
    def _how(how: str) -> QTableWidgetItem:
        item = QTableWidgetItem(tr(HOW_TEXT[how]) if how in HOW_TEXT else "")
        item.setToolTip(tr(HOW_TIP[how]) if how in HOW_TIP else "")
        if how == updates.NAME:
            theme.mark(item, "warn")
        return item

    def _show(self, row: int, verdict: updates.Verdict) -> None:
        """What mod.io said of the mod on ``row``."""
        name = self.table.item(row, NAME)
        name.setData(Qt.ItemDataRole.UserRole, verdict.url or self._page_for(verdict))
        if verdict.modio_name and not updates.same_name(verdict.modio_name, verdict.name):
            name.setToolTip(tr("On mod.io as: %s", verdict.modio_name))
        self.table.setItem(row, INSTALLED, QTableWidgetItem(verdict.installed_version))
        self.table.setItem(row, LATEST, QTableWidgetItem(verdict.latest_version))
        self.table.setItem(row, UPDATED, _dated(verdict.date_updated))
        self._said(row, verdict.status)
        self.table.setItem(row, HOW, self._how(verdict.how))

    def _summarise(self) -> str:
        counts = {}
        for verdict in self.verdicts:
            counts[verdict.status] = counts.get(verdict.status, 0) + 1
        # Whole sentences, each on its own.
        bits = [trn("%(n)s mod checked.", "%(n)s mods checked.", len(self.verdicts))]
        if counts.get(updates.UPDATE):
            bits.append(trn("%(n)s with an update on mod.io.",
                            "%(n)s with an update on mod.io.", counts[updates.UPDATE]))
        else:
            bits.append(tr("No updates."))
        if counts.get(updates.DIFFERENT):
            bits.append(trn("%(n)s at a version mod.io does not list.",
                            "%(n)s at a version mod.io does not list.",
                            counts[updates.DIFFERENT]))
        if counts.get(updates.UNMATCHED):
            bits.append(trn("%(n)s not found on mod.io.", "%(n)s not found on mod.io.",
                            counts[updates.UNMATCHED]))
        by_name = sum(1 for v in self.verdicts if v.how == updates.NAME)
        if by_name:
            bits.append(trn("%(n)s matched by name only.", "%(n)s matched by name only.",
                            by_name))
        return "  ".join(bits)

    def _page_for(self, verdict: updates.Verdict) -> str:
        return self.game.modio_url if verdict.modio_id else ""

    def _refresh_open(self) -> None:
        """Open on mod.io follows the row chosen; Download and update follows
        the ticks, as Check does.  It followed the row chosen, and in a list
        of ticks a mod ticked alone looked chosen and left the button grey."""
        self.open_button.setEnabled(bool(self._selected_url()))
        chosen = self._updatable()
        busy = (self._task is not None and self._task.isRunning()) or self._checking()
        self.update_button.setEnabled(bool(chosen) and not busy)
        self.update_button.setText(
            trn("&Download and update %(n)s mod", "&Download and update %(n)s mods", len(chosen))
            if chosen else tr("&Download and update")
        )
        self.update_button.setToolTip(
            tr("Downloads each ticked mod's current file and replaces the installed copy.")
            if chosen else tr("Tick a mod with an update to download it.")
        )
        # Why a mod with an update cannot have it from here: the one chosen,
        # else how many of the ticked ones the filter shows -- the sentence
        # sends the reader to their rows, and a hidden row cannot be chosen.
        verdict = self._selected_verdict()
        why = self.why_not_updatable(verdict) if self._has_update(verdict) else ""
        if not why:
            stuck = [
                v for v in (
                    self._verdicts.get(m.origin) for m in self._ticked()
                    if not self.table.isRowHidden(self._row_of[m.origin])
                )
                if self._has_update(v) and self.why_not_updatable(v)
            ]
            if stuck:
                why = trn(
                    "%(n)s ticked mod with an update can't be updated from here. "
                    "Choose its row to see why.",
                    "%(n)s ticked mods with an update can't be updated from here. "
                    "Choose a row to see why.",
                    len(stuck),
                )
        self.why_label.setText(why)
        self.why_label.setVisible(bool(why))

    @staticmethod
    def _has_update(verdict: Optional[updates.Verdict]) -> bool:
        return verdict is not None and verdict.status in (updates.UPDATE, updates.DIFFERENT)

    def _updatable(self) -> List[updates.Verdict]:
        """The ticked mods that can be brought up to date from here."""
        found = []
        for match in self._ticked():
            verdict = self._verdicts.get(match.origin)
            if verdict is not None and not self.why_not_updatable(verdict):
                found.append(verdict)
        return found

    def _host_label(self, origin: str) -> str:
        """What the mod a bundled one sits in is called."""
        host = self.loadout.layer(modfolder.host_of(origin))
        return host.label if host is not None else modfolder.host_of(origin)

    def _selected_verdict(self) -> Optional[updates.Verdict]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.matches):
            return None
        return self._verdicts.get(self.matches[rows[0].row()].origin)

    # -- bringing one up to date ---------------------------------------------
    def why_not_updatable(self, verdict: updates.Verdict) -> str:
        """Why this row cannot be updated from here, or "" if it can."""
        if verdict.status not in (updates.UPDATE, updates.DIFFERENT):
            return tr("mod.io has no newer file to download.")
        if verdict.latest is None or not verdict.latest.has_file:
            return tr("mod.io has no file for it.")
        if verdict.latest.virus_positive:
            return tr("mod.io's scan flagged this file, so the Studio will not download it.")
        layer = self.loadout.layer(verdict.origin)
        if layer is None or not layer.folder:
            return tr("The Studio does not know which folder this mod is in.")
        folder = Path(layer.folder)
        # Before the bundled check: a cache mod always sits under its numeric
        # id folder, so it always looks bundled, and the truer reason wins.
        cache = modio_cache.mods_dir(self.game_key)
        if cache is not None and disk.inside(folder, cache):
            return tr("The game's own mod browser installed this copy. Update it there.")
        if modfolder.is_bundled(verdict.origin):
            return tr("This mod is bundled inside %s. Update that mod to update it.",
                      self._host_label(verdict.origin))
        if not folder.is_dir():
            return tr("Its folder is no longer where it was.")
        return ""

    def _update_ticked(self) -> None:
        chosen = self._updatable()
        busy = (self._task is not None and self._task.isRunning()) or self._checking()
        if not chosen or busy:
            return
        if len(chosen) > 1 and not ask(
            self, tr("Update these mods?"),
            trn(
                "%(n)s mod is downloaded from mod.io and replaces the installed "
                "copy. What was there before is set aside.",
                "%(n)s mods are downloaded from mod.io and replace the installed "
                "copies. What was there before is set aside.",
                len(chosen),
            ),
            tr("Update"),
        ):
            return
        plans = [(verdict, Path(self.loadout.layer(verdict.origin).folder)) for verdict in chosen]
        game_id, chunks, library_dir = self.game.modio_game_id, self._chunks, self.library_dir

        def job(client, say, stop):
            done = []
            for verdict, folder in plans:
                if stop():
                    break
                latest = verdict.latest
                scratch = folder.parent / DOWNLOADS
                try:
                    # The new file lands in the library, and a junction takes
                    # the old folder's place -- which is set aside, as before.
                    fresh, installed = installplan.fetch_and_install(
                        client, game_id, latest, scratch, library_dir, folder.parent,
                        replace=folder, ask_first=True, chunks=chunks, stop=stop,
                        progress=lambda got, total, name=latest.name: say(
                            got, total, tr("Downloading %s…", name)),
                        saying=lambda text: say(0, 0, text),
                    )
                except modio.Cancelled:
                    raise
                except (modio.ModioError, modzip.InstallError, OSError) as exc:
                    # One that fails does not stop the ones after it.
                    done.append((verdict, None, str(exc)))
                else:
                    done.append((verdict, fresh, installed))
                finally:
                    shutil.rmtree(scratch, ignore_errors=True)
            return done

        self._task = ModioTaskWorker(self.store.load().api_key, job, parent=self, fetch=self._fetch)
        self._task.progress.connect(self._on_task_progress)
        self._task.succeeded.connect(self._on_updated)
        self._task.failed.connect(self._on_update_failed)
        self._task.finished.connect(self._on_task_done)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self._task.start()
        self._refresh_open()
        self._refresh_check()

    def _on_updated(self, done) -> None:
        said = []
        for verdict, fresh, installed in done:
            if fresh is None:
                said.append(tr("Could not update %(mod)s: %(why)s", mod=verdict.name, why=installed))
                continue
            self.installed.append(installed)
            verdict.status = updates.CURRENT
            verdict.installed_version = fresh.version
            self._show(self._row_of[verdict.origin], verdict)
            if installed.set_aside is not None:
                note = tr("Updated %(mod)s to %(version)s. What was there before is at %(where)s.",
                          mod=verdict.name, version=fresh.version,
                          where=wrappable_path(installed.set_aside))
            else:
                note = tr("Updated %(mod)s to %(version)s.", mod=verdict.name, version=fresh.version)
            # The player's iModYourAnno settings were in the folder the update
            # replaced; say what was carried into the new one, and what could not be.
            if installed.carried:
                note += " " + installed.carried.describe()
            if installplan.unchecked(fresh):
                note += " " + installplan.unchecked(fresh)
            said.append(note)
        self.summary.setText("  ".join(said))

    def _on_update_failed(self, message: str) -> None:
        self.summary.setText(tr("Could not update: %s", message))

    def _on_task_done(self) -> None:
        self.progress.setVisible(False)
        self._refresh_open()
        self._refresh_check()

    def _selected_url(self) -> str:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), NAME)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _open_selected(self) -> None:
        url = self._selected_url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _account(self) -> None:
        if self._open_account is None:
            return
        self._open_account()
        if self.store.load().api_key:
            self.account_button.setVisible(False)
            self._say_ready()
            self._refresh_check()


def _date(timestamp: int) -> str:
    if not timestamp:
        return ""
    return date_text(timestamp)


def _dated(timestamp: int) -> QTableWidgetItem:
    """A date's cell, which sorts by when and not by how it is written."""
    item = QTableWidgetItem(_date(timestamp))
    return sort_as(item, timestamp) if timestamp else item


