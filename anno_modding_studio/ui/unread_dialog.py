"""The ModOps the Studio could not read, and which mod each came from.

Every loaded mod carries a count of these, and a count is where the question
starts rather than where it ends.  What a reader actually wants to know is
which op, out of which mod, and what stopped it -- because that is the
difference between "this mod is mostly fine" and "the one thing I installed it
for is the part that did not come through".

None of it survives in the change list, which is the point: these produced no
changes.  So the op is kept as the mod wrote it, with its reason, and this is
where you read them.
"""

from __future__ import annotations

import contextlib
import warnings

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.changes import is_own_mod
from ..core.i18n import number, tr, tr_noop, tr_stored, trn
from . import theme
from .help_tip import HelpTip
from .widgets import listing_table, sortable

COLUMNS = (tr_noop("Mod"), "ModOp", tr_noop("Why it was not read"), tr_noop("What it does in game"))
IN_GAME = 3


def summarise(loadout) -> str:
    """One line for a button, or "" when everything was read."""
    entries = loadout.not_read()
    if not entries:
        return ""
    mods = {entry.origin for entry in entries}
    # Translators: "3 ModOps not read, in 2 mods" -- the two counts come
    # from the next two sentences.
    return tr(
        "%(ops)s, in %(mods)s",
        ops=trn("%(n)s ModOp not read", "%(n)s ModOps not read", len(entries)),
        mods=trn("%(n)s mod", "%(n)s mods", len(mods)),
    )


class UnreadDialog(QDialog):
    """Every op that could not be translated, with the mod it came from."""

    def __init__(self, loadout, parent=None, window=None, place=None):
        super().__init__(parent)
        self.loadout = loadout
        # The op to open on, as (mod, order): the row double-clicked in the
        # Mod changes list.  None, or one no longer here, is the first row.
        self._place = place
        # The main window, when there is one: what finds the game's loader,
        # the index, and what the loader said last time, kept across openings.
        self.window = window
        self._asking = None
        self._own_effects = {}
        self.setWindowTitle(tr("ModOps that could not be read"))
        self.setMinimumSize(1000, 600)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)


        self.summary = QLabel("")
        self.summary.setObjectName("SubHeading")
        head = QHBoxLayout()
        head.addWidget(self.summary, 1)
        head.addWidget(HelpTip(tr(
            "These are ModOp features with no narrow equivalent, or ModOps "
            "aimed at something your assets.xml doesn't have. The Studio "
            "leaves them unread, so these mods do more in the game than the "
            "changes list shows. Exporting such a mod writes each of these "
            "ModOps as the mod had it, in its own place in the file. The "
            "Studio cannot check your edits against them."
        ), self.windowTitle()))
        outer.addLayout(head)

        # The Studio shows nothing for these; the game's loader runs them.
        # Asked per mod, since each op takes a run of the loader of its own.
        ask_row = QHBoxLayout()
        self.ask_label = QLabel("")
        self.ask_label.setObjectName("StepHint")
        self.ask_label.setWordWrap(True)
        ask_row.addWidget(self.ask_label, 1)
        self.ask_button = QPushButton(tr("&Ask the loader"))
        self.ask_button.clicked.connect(self.ask_loader)
        ask_row.addWidget(self.ask_button, 0, Qt.AlignmentFlag.AlignTop)
        ask_row.addWidget(HelpTip(tr(
            "Runs the selected row's mod through the game's loader, on top "
            "of the mods it builds on. It runs once as it is, and once more "
            "with each ModOp here left out. The difference is what that "
            "ModOp does in the game. It takes a few seconds for each ModOp."
        ), self.ask_button.text()), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(ask_row)

        split = QSplitter(Qt.Orientation.Vertical)

        self.table = listing_table([tr(column) for column in COLUMNS], self.windowTitle())
        self.table.itemSelectionChanged.connect(self._on_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        split.addWidget(self.table)
        self.sorting = sortable(self.table)

        # The op as the mod wrote it. A label is not enough to judge one of
        # these by -- the reason is usually about something inside the body.
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 6, 0, 0)
        inner.setSpacing(4)
        caption = QLabel(tr("The ModOp as written, and its effect in the game"))
        caption.setObjectName("SubHeading")
        inner.addWidget(caption)
        self.source = QPlainTextEdit()
        self.source.setAccessibleName(caption.text())
        self.source.setReadOnly(True)
        self.source.setPlaceholderText(tr("Select a row to see the ModOp it came from."))
        inner.addWidget(self.source, 1)
        split.addWidget(holder)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

    # -- filling in -----------------------------------------------------
    def _name(self, origin: str) -> str:
        return self.loadout.label_of(origin) or tr("your mod")

    def reload(self) -> None:
        # In load order, so the list reads the way the stack does, and grouped
        # by mod: "which of my mods is only half read" is the question these
        # get asked in.
        self.entries = sorted(
            self.loadout.not_read(),
            key=lambda entry: (self.loadout.layer_rank(entry.origin), entry.label),
        )
        if not self.entries:
            self.summary.setText(
                tr("Every ModOp in every loaded mod was read.")
            )
        else:
            counts = {}
            for entry in self.entries:
                counts[entry.origin] = counts.get(entry.origin, 0) + 1
            worst = max(counts.items(), key=lambda pair: pair[1])
            self.summary.setText(tr(
                "%(summary)s. Most of them (%(count)s) are in %(mod)s.",
                summary=summarise(self.loadout), count=number(worst[1]),
                mod=self._name(worst[0]),
            ))

        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            mod = QTableWidgetItem(self._name(entry.origin))
            tip = [self._name(entry.origin)]
            if not is_own_mod(entry.origin):
                tip.append(tr("folder: %s", entry.origin))
            if entry.where:
                tip.append(tr_stored(entry.where))
            mod.setToolTip("\n".join(tip))
            self.table.setItem(row, 0, mod)

            label = QTableWidgetItem(tr_stored(entry.label))
            label.setToolTip(
                (entry.note + "\n\n" if entry.note else "") + entry.source
            )
            self.table.setItem(row, 1, label)

            why = QTableWidgetItem(tr_stored(entry.why))
            why.setToolTip(tr_stored(entry.why))
            theme.mark(why, "warn")
            self.table.setItem(row, 2, why)
            self.table.setItem(row, IN_GAME, self._in_game_item(entry))
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(IN_GAME, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 260)
        self._update_ask()
        if self.entries:
            self.table.selectRow(next((
                row for row, entry in enumerate(self.entries)
                if (entry.origin, entry.order) == self._place
            ), 0))

    # -- what an unread op offers ---------------------------------------
    def _context_menu(self, point) -> None:
        item = self.table.itemAt(point)
        if item is None:
            return
        self.table.selectRow(item.row())
        menu = self.menu_for(item.row())
        menu.exec(self.table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, row: int) -> QMenu:
        """Getting an unread op out of the Studio and into a message.

        This list exists to be acted on somewhere else: the op is a real
        ModOp feature with no narrow equivalent, and what somebody does about
        it is tell whoever wrote the mod, or ask about it.  Which meant
        selecting the row, clicking into the pane below, selecting all of it
        by hand and copying -- for the one thing the dialog is for.
        """
        entry = self.entries[row] if 0 <= row < len(self.entries) else None
        menu = QMenu(self)
        copy_op = menu.addAction(
            tr("Copy this ModOp"),
            lambda: QGuiApplication.clipboard().setText(
                entry.source if entry else ""
            ),
        )
        copy_op.setEnabled(bool(entry and entry.source))
        menu.addAction(
            tr("Copy why it was not read"),
            lambda: QGuiApplication.clipboard().setText(tr_stored(entry.why) if entry else ""),
        )
        menu.addSeparator()
        # The whole row as one paste: which mod, which op, and why -- the
        # three things a question about it has to carry.
        menu.addAction(
            tr("Copy all three"),
            lambda: QGuiApplication.clipboard().setText(
                "\n\n".join(
                    part
                    for part in (
                        self._name(entry.origin) if entry else "",
                        tr_stored(entry.why) if entry else "",
                        entry.source if entry else "",
                    )
                    if part
                )
            ),
        )
        return menu

    def _on_selected(self) -> None:
        self._update_ask()
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            # Emptied, so the pane asks for a row again rather than going on
            # showing the last one as if it were still chosen.
            self.source.clear()
            return
        entry = self.entries[rows.pop()]
        lines = []
        effect = self._effects().get(self._key(entry))
        if effect is not None:
            lines.extend(effect.details())
            lines.append("")
        if entry.note:
            lines.append(tr("The mod says: %s", entry.note))
        if entry.where:
            lines.append(tr_stored(entry.where))
        if lines:
            lines.append("")
        lines.append(entry.source)
        self.source.setPlainText("\n".join(lines))

    # -- what the game's loader says they do -----------------------------
    @staticmethod
    def _key(entry) -> tuple:
        return (entry.origin, entry.order, entry.source)

    def _effects(self) -> dict:
        """What the loader said of each op, kept on the window so it is still
        there the next time the dialog is opened."""
        if self.window is None:
            return self._own_effects
        if not hasattr(self.window, "unread_effects"):
            self.window.unread_effects = {}
        return self.window.unread_effects

    def _in_game_item(self, entry) -> QTableWidgetItem:
        from ..core import loadercheck

        effect = self._effects().get(self._key(entry))
        if effect is not None:
            item = QTableWidgetItem(effect.describe())
            item.setToolTip("\n".join(effect.details())[:4000])
            if effect.why:
                theme.mark(item, "warn")
            return item
        item = QTableWidgetItem("" if loadercheck.is_an_op(entry) else tr("Not an op: nothing to run"))
        item.setForeground(QColor(theme.TEXT_DIM))
        return item

    def _selected_entry(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        return self.entries[rows.pop()] if len(rows) == 1 else None

    def _loader(self):
        # A window with no loader check (a test's, or none at all) has no loader.
        finder = getattr(getattr(self.window, "loader_check", None), "game_loader", None)
        return finder() if finder is not None else None

    def _update_ask(self) -> None:
        from ..core import loadercheck

        if self._asking is not None:
            return
        entry = self._selected_entry()
        if self.window is None or getattr(self.window, "index", None) is None:
            self.ask_button.setEnabled(False)
            self.ask_label.setText("")
            return
        loader = self._loader()
        if loader is None:
            self.ask_button.setEnabled(False)
            self.ask_label.setText(tr(
                "With the Anno Modding Tools extension for VS Code "
                "installed, the game's loader can show what each of these "
                "does in the game."
            ))
            return
        ops = [one for one in self.entries if entry is not None and one.origin == entry.origin
               and loadercheck.is_an_op(one)]
        self.ask_button.setEnabled(bool(ops))
        if entry is None:
            self.ask_label.setText(tr("Select a row to ask about its mod."))
        elif not ops:
            self.ask_label.setText(tr("%s carries nothing here the loader runs.", self._name(entry.origin)))
        else:
            self.ask_label.setText(trn(
                "%(n)s op in %(mod)s for the game's loader (%(loader)s): %(time)s.",
                "%(n)s ops in %(mod)s for the game's loader (%(loader)s): %(time)s.",
                len(ops), mod=self._name(entry.origin), loader=tr_stored(loader.said),
                time=_about(len(ops) + 1)))

    def ask_loader(self) -> None:
        """Ask the loader about every op the selected row's mod carries, in
        the background, each row filling in when the answers come."""
        from ..core import generator, loadercheck
        from .workers import UnreadEffectsWorker

        entry = self._selected_entry()
        loader = self._loader()
        index = getattr(self.window, "index", None)
        if entry is None or loader is None or index is None or self._asking is not None:
            return
        if _WINDING_DOWN:
            # The last question still holds the loader's work folder.
            self.ask_label.setText(tr(
                "The loader is still finishing the last question. Try again in a moment."
            ))
            return
        job = loadercheck.prepare(self.loadout, entry.origin, index=getattr(index, "index", index))
        job.xml = generator.build_modops_xml(job.view) if job.writes else ""
        worker = UnreadEffectsWorker(entry.origin, job, getattr(index, "index", index), loader, self)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._on_effects)
        worker.finished.connect(worker.deleteLater)
        self._asking = worker
        self.ask_button.setEnabled(False)
        self.ask_label.setText(tr("Asking the game's loader about %s…", self._name(entry.origin)))
        worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.ask_label.setText(tr("Asking the game's loader: run %(done)s of %(total)s…",
                                  done=number(min(done + 1, total)), total=number(total)))

    def _on_effects(self, origin: str, effects) -> None:
        self._asking = None
        store = self._effects()
        by_place = {(effect.order, effect.source): effect for effect in effects}
        for entry in self.entries:
            if entry.origin == origin and (entry.order, entry.source) in by_place:
                store[self._key(entry)] = by_place[(entry.order, entry.source)]
        for row, entry in enumerate(self.entries):
            if entry.origin == origin:
                self.table.setItem(row, IN_GAME, self._in_game_item(entry))
        changing = sum(1 for effect in effects if effect.count)
        self._on_selected()
        name = self._name(origin)
        if len(effects) == 1 and changing:
            said = tr("The game's loader: the one op in %s changes something in game, on top of the "
                      "mods it builds on.", name)
        elif len(effects) == 1:
            said = tr("The game's loader: the one op in %s does nothing in game, on top of the mods "
                      "it builds on.", name)
        else:
            said = trn("The game's loader: %(n)s of %(total)s ops in %(mod)s changes something in game, "
                       "on top of the mods it builds on.",
                       "The game's loader: %(n)s of %(total)s ops in %(mod)s change something in game, "
                       "on top of the mods it builds on.", changing, total=number(len(effects)), mod=name)
        self.ask_label.setText(said)

    def done(self, result: int) -> None:
        """Closing stops a question still being asked: the answer would have
        nowhere to go.

        It is told to stop and left to finish by itself. The loader cannot
        be interrupted in the middle of a run, and a run takes seconds, so
        waiting for it here froze the window for as long.
        """
        worker, self._asking = self._asking, None
        if worker is not None:
            worker.requestInterruption()
            _wind_down(worker)
        super().done(result)


# Questions told to stop whose current run of the loader has not ended yet.
# Held here so each outlives its dialog, and so a new question waits for the
# loader's work folder to be free.
_WINDING_DOWN: set = set()


def _wind_down(worker) -> None:
    """Let ``worker`` finish its current run without its dialog."""
    from PySide6.QtWidgets import QApplication

    # With nothing connected, PySide warns or raises, by version. Either is fine.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for signal in (worker.progressed, worker.succeeded):
            with contextlib.suppress(RuntimeError, TypeError):
                signal.disconnect()
    if not worker.isRunning():
        return
    # Its parent is the dialog, which is about to be deleted.
    worker.setParent(None)
    _WINDING_DOWN.add(worker)
    worker.finished.connect(lambda: _WINDING_DOWN.discard(worker))
    app = QApplication.instance()
    if app is not None:
        # A thread still running when the app goes takes the process with it.
        app.aboutToQuit.connect(lambda: worker.isRunning() and worker.wait(60000))


def _about(runs: int) -> str:
    """How long ``runs`` runs of the loader take, roughly: a few seconds each
    on Anno 1800's file, ten or more under a tall stack of mods."""
    low, high = runs * 3, runs * 12
    # Translators: how long asking the game's loader will take, after
    # "3 ops in My Mod for the game's loader (…):".
    if high < 60:
        return tr("under a minute")
    if low < 60:
        return trn("up to %(n)s minute", "up to %(n)s minutes", round(high / 60))
    return tr("%(low)s to %(high)s minutes", low=number(max(1, round(low / 60))), high=number(round(high / 60)))
