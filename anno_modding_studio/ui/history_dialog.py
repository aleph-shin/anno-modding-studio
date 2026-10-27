"""History: the loadout's, and the project of the mod in question.

Two histories, each restored on its own (``core/histories.py``; the
projects-and-loadouts proposal, section 9).  The dialog has a tab for each:

- **The project** of the mod in question -- the Export tab's, or the one
  *Changes go to* names: its work at each checkpoint, which is taken
  whenever it is exported or published, by hand, and around a restore.
  Selecting one shows what changed to reach it, each edit with the loadout
  it was made in.  Restoring one changes the mod in every loadout that has
  it, so the question names them.
- **The loadout**: its list at each checkpoint -- which mods, in which
  order, which on, their settings, its profiles, and where each project
  stood.  Restoring one can restore its projects too, each as it was then.

Restoring saves what it replaces as a checkpoint first, so nothing is ever
lost; and never restores build numbers, the mod.io link or what was
published.

What a checkpoint of a mod records is the work done *here*, not the mods that
happened to be open at the time.  Reading a mods folder lands thousands of
changes that are already on disk and can be read again; a history of those
would say nothing, and restoring one would put somebody else's mod back to a
state it was never in.

They were called versions until mods had numbered versions of their own; a
list of "versions" beside a mod at 1.2.0 would have meant two things.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import histories
from ..core.changes import diff_changes, inside_says, kept_changes
from ..core.i18n import STORED, date_text, number, tr, tr_noop, tr_stored, trn
from . import theme, widgets
from .changes_dock import field_label
from .help_tip import HelpTip
from .widgets import ask, fit_columns, sortable_tree, tab_as_laid_out

INDEX_ROLE = Qt.ItemDataRole.UserRole  # -1 marks the live working copy
# How many names a sentence lists before it says how many more.
_NAMED = 5


def _names(names) -> str:
    names = [str(name) for name in names]
    shown = tr(", ", context="between items of a list").join(names[:_NAMED])
    if len(names) > _NAMED:
        shown = trn("%(names)s and %(n)s more", "%(names)s and %(n)s more",
                    len(names) - _NAMED, names=shown)
    return shown


def _settings_said(was: dict, now: dict) -> str:
    """What a mod's settings came to: its options where they changed, and
    how it is read where that changed (each was once said as its options
    alone, so a mod read another way read as set back to its defaults)."""
    options = now.get("options") or {}
    shown = (_names("%s = %s" % (key, value) for key, value in sorted(options.items()))
             or tr("the defaults"))
    said = [shown] if options != (was.get("options") or {}) else []
    inert = bool((now.get("read_as") or {}).get("keep_inert"))
    if inert != bool((was.get("read_as") or {}).get("keep_inert")):
        # Translators: how a mod is read, after "Keep inert removals" was
        # ticked or cleared for it; shown with its settings.
        said.append(tr("inert removals kept") if inert else tr("inert removals not kept"))
    return "; ".join(said) or shown


def _op_of(entry) -> tuple:
    """The mod an edit kept inside another mod's ops is about, and the op's
    text where it names one: a ``taken_out`` entry, or any other kind."""
    if isinstance(entry, (list, tuple)):
        return (str(entry[0]) if entry else "", str(entry[1]) if len(entry) > 1 else "")
    return (str(entry.get("origin", "")), str(entry.get("source", "")))


def mod_in_question(loadout, origin):
    """The mod whose history the dialog shows beside the loadout's: ``origin``
    -- the Export tab's mod, or the one *Changes go to* names, where None --
    when it is a mod of yours or one you edited, else the mod the loadout
    releases; None where there is none."""
    if origin is None:
        origin = loadout.editing_origin()
    for candidate in (origin, loadout.home or ""):
        if candidate and loadout.layer(candidate) is None:
            continue
        if (
            candidate in loadout.project_origins()
            or loadout.history_of(candidate)
            or any(change.origin == candidate for change in loadout.authored())
        ):
            return candidate
    return None


class HistoryDialog(QDialog):
    """The two histories, a tab each: browse checkpoints, see what each one
    changed, and restore one."""

    def __init__(self, loadout, parent=None, origin=None, loadout_name: str = "", having=None):
        super().__init__(parent)
        self.loadout = loadout
        self.loadout_name = loadout_name
        # The other loadouts that have the project of an origin, by name.
        self.having = having or (lambda _origin: [])
        # Whether anything was restored, and whether a mod's work was.
        self.restored = False
        self.mod_restored = False
        self.checkpoint_taken = False
        self.setWindowTitle(tr("Loadout history: %s", loadout_name or loadout.own.name)
                            if loadout_name or loadout.own.name
                            else tr("Loadout history: Untitled"))
        self.setMinimumSize(980, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName(tr("Histories"))
        self.origin = mod_in_question(loadout, origin)
        self.mod = None
        if self.origin is not None:
            self.mod = ModHistory(self, self.origin)
            self.tabs.addTab(self.mod, self.mod.title())
        self.list = LoadoutHistory(self)
        self.tabs.addTab(
            self.list, tr("Loadout: %s", loadout_name) if loadout_name else tr("This loadout"))
        outer.addWidget(self.tabs, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def mod_name(self, origin: str) -> str:
        return self.loadout.label_of(origin) or tr("your mod")

    def loadouts_with(self, origin: str) -> list:
        """Every loadout that has the project of ``origin``: this one first."""
        here = [self.loadout_name or tr("this loadout")]
        return here + [name for name in self.having(origin) if name != self.loadout_name]

    def reload(self) -> None:
        if self.mod is not None:
            self.mod.reload()
        self.list.reload()


def _split(left: QWidget, right: QWidget) -> QSplitter:
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(left)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([280, 660])
    return splitter


class _HistoryTab(QWidget):
    """One history: its checkpoints on the left, what one changed on the
    right, and the buttons for each."""

    def __init__(self, dialog: HistoryDialog, hint: str, help_text: str, columns,
                 restore_label: str):
        super().__init__()
        self.dialog = dialog
        self.loadout = dialog.loadout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 4)
        # When a checkpoint is made stays in view. What one holds is behind
        # the ?.
        head = QHBoxLayout()
        said = QLabel(hint)
        said.setObjectName("StepHint")
        said.setWordWrap(True)
        head.addWidget(said, 1)
        self.help = HelpTip(help_text, tr("Checkpoints"))
        head.addWidget(self.help)
        layout.addLayout(head)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.checkpoints = QListWidget()
        self.checkpoints.setAccessibleName(tr("Checkpoints"))
        self.checkpoints.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.checkpoints.currentItemChanged.connect(self._show_diff)
        left_layout.addWidget(self.checkpoints, 1)
        row = QHBoxLayout()
        self.checkpoint_button = QPushButton(tr("&Save a checkpoint now…"))
        self.checkpoint_button.clicked.connect(self._save_checkpoint)
        row.addWidget(self.checkpoint_button)
        row.addStretch(1)
        left_layout.addLayout(row)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.diff_heading = QLabel("")
        self.diff_heading.setObjectName("SubHeading")
        self.diff_heading.setWordWrap(True)
        right_layout.addWidget(self.diff_heading)
        self.diff_tree = QTreeWidget()
        self.diff_tree.setAccessibleName(tr("Changes in this checkpoint"))
        self.diff_tree.setColumnCount(len(columns))
        self.diff_tree.setHeaderLabels(columns)
        self.diff_tree.setAlternatingRowColors(True)
        self.diff_tree.setRootIsDecorated(False)
        header = self.diff_tree.header()
        header.resizeSection(0, 72)
        for column in range(1, len(columns) - 1):
            header.resizeSection(column, 170)
        header.setStretchLastSection(True)
        fit_columns(self.diff_tree)
        right_layout.addWidget(self.diff_tree, 1)
        self.sorting = sortable_tree(self.diff_tree)
        row = QHBoxLayout()
        row.addStretch(1)
        self.restore_button = QPushButton(restore_label)
        self.restore_button.clicked.connect(self._restore)
        row.addWidget(self.restore_button)
        right_layout.addLayout(row)
        layout.addWidget(_split(left, right), 1)

    def _selected_index(self):
        item = self.checkpoints.currentItem()
        return None if item is None else item.data(INDEX_ROLE)

    def _working_copy(self, said: str, moved: bool) -> None:
        working = QListWidgetItem(said)
        working.setData(INDEX_ROLE, -1)
        working.setForeground(QColor(theme.ACCENT_TEXT if moved else theme.TEXT_DIM))
        self.checkpoints.addItem(working)

    def _row(self, kind: str, colour: str, *texts) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.diff_tree)
        item.setText(0, kind)
        item.setForeground(0, QColor(colour))
        for column, text in enumerate(texts, start=1):
            item.setText(column, text)
        item.setForeground(1, QColor(theme.TEXT_DIM))
        return item

    # -- what each history says for itself --------------------------------
    @property
    def history(self) -> list:
        raise NotImplementedError

    def _working(self):
        """The working copy's line, and whether it moved since the last."""
        raise NotImplementedError

    def _said_of(self, checkpoint) -> str:
        raise NotImplementedError

    def _take(self, label: str) -> None:
        raise NotImplementedError

    def _restore_to(self, checkpoint) -> None:
        raise NotImplementedError

    def _unsaved(self) -> bool:
        """Whether a checkpoint taken now would hold anything the last one
        does not."""
        raise NotImplementedError

    # -- the same for both ---------------------------------------------------
    def reload(self, select_row: int = 0) -> None:
        # Offered only with something to save: a checkpoint of nothing new
        # is the one before it again, under another date.
        unsaved = self._unsaved()
        self.checkpoint_button.setEnabled(unsaved)
        self.checkpoint_button.setToolTip("" if unsaved else (
            tr("The working copy is the same as the last checkpoint.") if self.history
            else tr("There are no changes to save yet.")
        ))
        self.checkpoints.clear()
        self._working_copy(*self._working())
        for position in range(len(self.history) - 1, -1, -1):
            item = QListWidgetItem(self._said_of(self.history[position]))
            item.setData(INDEX_ROLE, position)
            self.checkpoints.addItem(item)
        self.checkpoints.setCurrentRow(min(select_row, self.checkpoints.count() - 1))

    def _save_checkpoint(self) -> None:
        label, ok = QInputDialog.getText(
            self, tr("Save a checkpoint"), tr("What changed since the last one?"),
            text=tr("Work in progress"),
        )
        if not ok:
            return
        # The labels are kept in the files, so they stay English.
        self._take(label.strip() or tr_noop("Saved", context=STORED))
        self.dialog.checkpoint_taken = True
        self.dialog.reload()
        self.checkpoints.setCurrentRow(1)

    def _show(self, position: int) -> None:
        """What the checkpoint at ``position`` changed; -1 is the working
        copy."""
        raise NotImplementedError

    def _show_diff(self) -> None:
        position = self._selected_index()
        self.diff_tree.clear()
        if position is None:
            self.restore_button.setEnabled(False)
            return
        self._show(position)

    def _restore(self) -> None:
        position = self._selected_index()
        if position is None or position < 0:
            return
        self._restore_to(self.history[position])


class ModHistory(_HistoryTab):
    """The history of one mod of yours: its project's."""

    def __init__(self, dialog: HistoryDialog, origin: str):
        self.origin = origin
        name = dialog.mod_name(origin)
        super().__init__(
            dialog,
            tr("A checkpoint of %s is recorded every time it is exported or "
               "published.", name),
            tr("It holds the changes you made to the mod, its ModOps, its details "
               "and the edits it keeps inside other mods' ModOps, and none of a "
               "mod read in beside them. Select one to see what changed to reach "
               "it, and in which loadout."),
            # Translators: column headings; "Change" is the noun, what an
            # edit did to a value; "Made in" names the loadout an edit was
            # made in.
            ["", tr("Mod"), tr("Asset"), tr("Field"), tr("Change"), tr("Made in")],
            tr("&Restore this checkpoint…"),
        )
        self.reload()

    def title(self) -> str:
        name = self.dialog.mod_name(self.origin)
        if self.origin in self.loadout.project_origins():
            return tr("Project: %s", name)
        return tr("Your edits to %s", name)

    @property
    def history(self):
        return self.loadout.history_of(self.origin)

    def _changes_now(self) -> list:
        """Its changes as a checkpoint of it would hold them now: the ones
        made to it, and the ones it keeps inside other mods' ops."""
        loadout, origin = self.loadout, self.origin
        return ([change for change in loadout.authored() if change.origin == origin]
                + kept_changes(loadout.kept_inside(origin)))

    def _changes_in(self, checkpoint) -> list:
        # Under the name the mod has here, whichever it was taken under.
        return checkpoint.as_changes(self.origin) + kept_changes(checkpoint.inside)

    def _working(self):
        loadout, origin = self.loadout, self.origin
        latest = loadout.latest_checkpoint_of(origin)
        # "unsaved" has to mean unsaved: right after an export the working
        # copy matches the checkpoint that was just recorded.
        now = self._changes_now()
        pending = diff_changes(self._changes_in(latest) if latest is not None else [], now)
        # Counted as a checkpoint of it would be: the changes made here.
        mine = len(now)
        # An op of theirs switched off or taken out is work that comes to no
        # change of its own.  Said as well: one switched back on was "saved
        # as the latest checkpoint" (R3-41, hands-on run 3).
        inside_moved = inside_says(loadout.kept_inside(origin)) != inside_says(
            latest.inside if latest is not None else {})
        if latest is None:
            # No checkpoint yet for it to be saved as.
            said = trn("Working copy\n    %(n)s change", "Working copy\n    %(n)s changes", mine)
        elif pending.is_empty and not inside_moved:
            said = trn(
                "Working copy\n    %(n)s change, saved as the latest checkpoint",
                "Working copy\n    %(n)s changes, saved as the latest checkpoint",
                mine,
            )
        elif pending.is_empty:
            said = trn(
                "Working copy\n    %(n)s change, and edits inside other mods' ModOps "
                "moved since the last checkpoint",
                "Working copy\n    %(n)s changes, and edits inside other mods' ModOps "
                "moved since the last checkpoint",
                mine,
            )
        else:
            said = trn(
                "Working copy\n    %(n)s change, %(pending)s since the last checkpoint",
                "Working copy\n    %(n)s changes, %(pending)s since the last checkpoint",
                mine, pending=pending.summary(),
            )
        moved = not pending.is_empty or inside_moved
        return said, moved

    def _said_of(self, checkpoint) -> str:
        return trn(
            "%(when)s   v%(version)s\n    %(label)s, %(n)s change",
            "%(when)s   v%(version)s\n    %(label)s, %(n)s changes",
            len(self._changes_in(checkpoint)),
            when=date_text(checkpoint.timestamp, clock=True),
            version=checkpoint.version, label=tr_stored(checkpoint.label),
        )

    def _show(self, position: int) -> None:
        loadout, origin = self.loadout, self.origin
        if position == -1:
            checkpoint = None
            previous = loadout.latest_checkpoint_of(origin)
            after = self._changes_now()
            details = loadout.details_of(origin)
            kept = loadout.kept_inside(origin)
            made_in: dict = {}
        else:
            checkpoint = self.history[position]
            previous = self.history[position - 1] if position > 0 else None
            after = self._changes_in(checkpoint)
            details = checkpoint.details
            kept = checkpoint.inside
            made_in = checkpoint.made_in()
        # A project held from here is written by nobody from here.
        self.restore_button.setEnabled(checkpoint is not None and not loadout.held(origin))
        before = self._changes_in(previous) if previous is not None else []
        diff = diff_changes(before, after)
        summary = diff.summary()
        if checkpoint is None and previous is not None:
            heading = tr(
                "Working copy vs the last checkpoint (%(when)s): %(summary)s",
                when=date_text(previous.timestamp, clock=True), summary=summary,
            )
        elif checkpoint is None:
            heading = tr("Working copy vs an empty loadout: %s", summary)
        elif previous is not None:
            heading = tr(
                "%(when)s v%(version)s vs the checkpoint before it: %(summary)s",
                when=date_text(checkpoint.timestamp, clock=True),
                version=checkpoint.version, summary=summary,
            )
        else:
            heading = tr(
                "%(when)s v%(version)s vs an empty loadout: %(summary)s",
                when=date_text(checkpoint.timestamp, clock=True),
                version=checkpoint.version, summary=summary,
            )
        self.diff_heading.setText(heading)
        here = self.dialog.loadout_name if checkpoint is None else ""
        was_made_in = previous.made_in() if previous is not None else {}
        self._fill_diff(diff, made_in, was_made_in, here)
        self._fill_inside(previous.inside if previous is not None else {}, kept)
        if previous is not None:
            self._fill_details(previous.details, details)

    def _fill_diff(self, diff, made_in: dict, was_made_in: dict, here: str) -> None:
        mono = theme.mono_font()

        def row(kind, colour, change, value, where):
            item = self._row(kind, colour, self.dialog.mod_name(change.origin),
                             tr_stored(change.asset_label), field_label(change.path), value, where)
            item.setFont(3, mono)
            item.setToolTip(3, change.path)

        for change in diff.added:
            # Translators: what happened to a change between two checkpoints.
            row(tr("added", context="change"), theme.ADDED, change,
                "%s → %s" % (change.old_value, change.new_value), made_in.get(change.key, here))
        for before, after in diff.modified:
            # Translators: what happened to a change between two checkpoints.
            row(tr("edited", context="change"), theme.CHANGED, after,
                "%s → %s" % (before.new_value, after.new_value), made_in.get(after.key, here))
        for change in diff.removed:
            # Translators: what happened to a change between two checkpoints.
            row(tr("removed", context="change"), theme.REMOVED, change,
                tr("reverted to %s", change.old_value), was_made_in.get(change.key, ""))

    def _fill_inside(self, before: dict, after: dict) -> None:
        """A row for each op of another mod's it switched off or took out
        since ``before``, and each it no longer does: edits that come to no
        change of their own."""
        mono = theme.mono_font()
        for key, done, undone in (
            # Translators: what happened to another mod's ModOp between two
            # checkpoints of a mod of yours.
            ("off", tr("switched off", context="change"), tr("switched on", context="change")),
            # Translators: as "switched off"; "taken out" of the mod, and
            # "put back" into it.
            ("taken_out", tr("taken out", context="change"), tr("put back", context="change")),
        ):
            was = {_op_of(entry): entry for entry in before.get(key) or ()}
            now = {_op_of(entry): entry for entry in after.get(key) or ()}
            for (target, source), kind, colour in [
                *((op, done, theme.CHANGED) for op in now if op not in was),
                *((op, undone, theme.REMOVED) for op in was if op not in now),
            ]:
                item = self._row(kind, colour, self.dialog.mod_name(target), "", tr("ModOp"),
                                 " ".join(source.split()), "")
                item.setFont(4, mono)
                item.setToolTip(4, source)

    def _fill_details(self, before: dict, after: dict) -> None:
        """A row for each of the mod's details that moved."""
        for name in sorted(set(before) | set(after)):
            if name in before and name in after and before[name] != after[name]:
                # Translators: what happened to a change between two checkpoints.
                self._row(tr("edited", context="change"), theme.CHANGED,
                          self.dialog.mod_name(self.origin), tr("Details"), name,
                          "%s → %s" % (before[name], after[name]), "")

    def _unsaved(self) -> bool:
        # A project held from here is written by nobody from here.
        return (not self.loadout.held(self.origin)
                and self.loadout.worth_a_checkpoint(self.origin))

    def _take(self, label: str) -> None:
        self.loadout.checkpoint(label, origin=self.origin, loadout=self.dialog.loadout_name)

    def _restore_to(self, checkpoint) -> None:
        loadout, origin = self.loadout, self.origin
        name = self.dialog.mod_name(origin)
        mine = sum(1 for change in loadout.authored() if change.origin == origin)
        parts = [trn(
            "Replace the current %(n)s change with the %(then)s from %(when)s?",
            "Replace the current %(n)s changes with the %(then)s from %(when)s?",
            mine, then=number(len(checkpoint.changes)),
            when=date_text(checkpoint.timestamp, clock=True))]
        moved = sorted(
            key for key, value in checkpoint.details.items()
            if loadout.details_of(origin).get(key) != value
        )
        if moved:
            parts.append(tr("Its details go back as well: %s.", _names(moved)))
        kept = loadout.kept_inside(origin)
        if inside_says(kept) != inside_says(checkpoint.inside):
            inside = sorted({
                self.dialog.mod_name(_op_of(entry)[0])
                for held in (kept, checkpoint.inside) for entries in held.values() for entry in entries
            })
            parts.append(tr("Its edits inside other mods' ModOps go back as well: %s.", _names(inside)))
        if origin in loadout.project_origins():
            parts.append(tr(
                "This changes %(mod)s in every loadout that has it: %(loadouts)s.",
                mod=widgets.named(name), loadouts=_names(self.dialog.loadouts_with(origin))))
            parts.append(tr("Its build numbers, its mod.io link and what was "
                            "published stay as they are."))
        parts.append(tr("Your working copy is saved as a checkpoint first, so nothing is lost."))
        if not ask(self, tr("Restore checkpoint"), "\n\n".join(parts), tr("&Restore")):
            return
        histories.restore_mod(loadout, origin, checkpoint, self.dialog.loadout_name)
        self.dialog.restored = self.dialog.mod_restored = True
        self.dialog.checkpoint_taken = True
        self.dialog.reload()


class LoadoutHistory(_HistoryTab):
    """The history of the loadout's list."""

    def __init__(self, dialog: HistoryDialog):
        super().__init__(
            dialog,
            tr("A checkpoint of the loadout is recorded with every export and "
               "publish."),
            tr("It records the loadout's list: which mods, in which order, which "
               "are on, their settings and its profiles, and where each project "
               "of yours stood."),
            # Translators: column headings; "Change" is the noun, what
            # happened to a mod in the list.
            ["", tr("Mod"), tr("Change")],
            tr("&Restore this checkpoint…"),
        )
        self.reload()

    @property
    def history(self):
        return self.loadout.history

    def _working(self):
        moved = histories.changed_since_checkpoint(self.loadout) and bool(self.history)
        mods = len(self.loadout.layers)
        if moved:
            said = trn("Working copy\n    %(n)s mod, moved since the last checkpoint",
                       "Working copy\n    %(n)s mods, moved since the last checkpoint", mods)
        elif self.history:
            said = trn("Working copy\n    %(n)s mod, saved as the latest checkpoint",
                       "Working copy\n    %(n)s mods, saved as the latest checkpoint", mods)
        else:
            said = trn("Working copy\n    %(n)s mod", "Working copy\n    %(n)s mods", mods)
        return said, moved

    def _said_of(self, checkpoint) -> str:
        return trn(
            "%(when)s\n    %(label)s, %(n)s mod",
            "%(when)s\n    %(label)s, %(n)s mods",
            len(checkpoint.mods),
            when=date_text(checkpoint.timestamp, clock=True), label=tr_stored(checkpoint.label),
        )

    def _show(self, position: int) -> None:
        if position == -1:
            checkpoint = None
            previous = self.history[-1] if self.history else None
            after = histories.now(self.loadout)
        else:
            checkpoint = self.history[position]
            previous = self.history[position - 1] if position > 0 else None
            after = checkpoint
        self.restore_button.setEnabled(checkpoint is not None)
        diff = histories.compare(previous, after)
        rows = self._fill(diff, after, previous)
        count = trn("%(n)s change", "%(n)s changes", rows) if rows else tr("no changes")
        if checkpoint is None and previous is not None:
            heading = tr("Working copy vs the last checkpoint (%(when)s): %(summary)s",
                         when=date_text(previous.timestamp, clock=True), summary=count)
        elif checkpoint is None:
            heading = tr("Working copy vs an empty loadout: %s", count)
        elif previous is not None:
            heading = tr("%(when)s vs the checkpoint before it: %(summary)s",
                         when=date_text(checkpoint.timestamp, clock=True), summary=count)
        else:
            heading = tr("%(when)s vs an empty loadout: %(summary)s",
                         when=date_text(checkpoint.timestamp, clock=True), summary=count)
        self.diff_heading.setText(heading)

    def _fill(self, diff, after, before) -> int:
        """A row for each thing that moved in the list; how many."""
        rows = 0

        def row(kind, colour, name, said=""):
            nonlocal rows
            self._row(kind, colour, name, said)
            rows += 1

        places = {mod.get("origin"): place for place, mod in enumerate(after.mods, start=1)}
        for mod in diff.added:
            # Translators: what happened to a mod in the loadout between two
            # checkpoints, as do "taken out", "on", "off", "moved" and
            # "settings" after it.
            row(tr("added", context="mod"), theme.ADDED, histories.label_of(mod))
        for mod in diff.gone:
            row(tr("taken out", context="mod"), theme.REMOVED, histories.label_of(mod))
        for mod in diff.on:
            row(tr("on", context="mod"), theme.CHANGED, histories.label_of(mod), tr("switched on"))
        for mod in diff.off:
            row(tr("off", context="mod"), theme.CHANGED, histories.label_of(mod), tr("switched off"))
        for mod in diff.moved:
            row(tr("moved", context="mod"), theme.CHANGED, histories.label_of(mod),
                tr("now %(place)s of %(total)s in load order",
                   place=number(places.get(mod.get("origin"), 0)), total=number(len(after.mods))))
        was = {mod.get("origin"): mod for mod in (before.mods if before is not None else [])}
        for mod in diff.settings:
            row(tr("settings", context="mod"), theme.CHANGED, histories.label_of(mod),
                _settings_said(was.get(mod.get("origin")) or {}, mod))
        for name in diff.profiles_added:
            row(tr("profile"), theme.ADDED, name, tr("added", context="profile"))
        for name in diff.profiles_gone:
            row(tr("profile"), theme.REMOVED, name, tr("removed", context="profile"))
        for name in diff.profiles_switched:
            row(tr("profile"), theme.CHANGED, name, tr("mods switched on or off"))
        if diff.chosen is not None:
            row(tr("profile"), theme.CHANGED, diff.chosen[1], tr("chosen"))
        for origin, _was, now in diff.projects:
            checkpoint = now.get("checkpoint") or 0
            row(tr("project"), theme.CHANGED, self.dialog.mod_name(origin),
                tr("revision %(revision)s, at its checkpoint of %(when)s",
                   revision=number(now.get("revision") or 0),
                   when=date_text(checkpoint, clock=True)) if checkpoint
                else tr("revision %s", number(now.get("revision") or 0)))
        return rows

    def _unsaved(self) -> bool:
        # Before the first, a list with no mods in it is nothing to save.
        return histories.moved(self.loadout) and bool(self.history or self.loadout.layers)

    def _take(self, label: str) -> None:
        histories.take(self.loadout, label, taken_in=self.dialog.loadout_name)

    def _restore_to(self, target) -> None:
        alone = histories.plan(self.loadout, target)
        whole = histories.plan(self.loadout, target, with_projects=True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Restore checkpoint"))
        widgets.show_box_text(box, self._question(target, alone, whole))
        go = box.addButton(tr("&Restore the loadout"), QMessageBox.ButtonRole.AcceptRole)
        both = None
        if whole.projects:
            both = box.addButton(tr("Restore, &with each project as it was then"),
                                 QMessageBox.ButtonRole.AcceptRole)
        stay = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(go)
        box.setEscapeButton(stay)
        answer = widgets.answer_of(box)
        if answer is not go and (both is None or answer is not both):
            return
        done = histories.restore(self.loadout, target, with_projects=answer is both,
                                 taken_in=self.dialog.loadout_name)
        self.dialog.restored = True
        self.dialog.mod_restored = self.dialog.mod_restored or bool(done.projects)
        self.dialog.checkpoint_taken = True
        self.dialog.reload()

    def _question(self, target, alone, whole) -> str:
        """What restoring ``target`` changes, said before it is done."""
        diff = alone.diff
        lines = []

        def names(mods):
            return _names(histories.label_of(mod) for mod in mods)

        left_out = {mod.get("origin") for mod, _why in alone.not_back}
        back = [mod for mod in diff.added if mod.get("origin") not in left_out]
        if diff.gone:
            lines.append(tr("Takes out: %s.", names(diff.gone)))
        if back:
            lines.append(tr("Puts back, to read again from its folder: %s.", names(back)))
        if diff.on:
            lines.append(tr("Switches on: %s.", names(diff.on)))
        if diff.off:
            lines.append(tr("Switches off: %s.", names(diff.off)))
        if diff.moved:
            lines.append(tr("Moves back to where they were: %s.", names(diff.moved)))
        if diff.settings:
            lines.append(tr("Sets back the settings of: %s.", names(diff.settings)))
        if diff.profiles_added:
            lines.append(tr("Brings back the profiles: %s.", _names(diff.profiles_added)))
        if diff.profiles_gone:
            lines.append(tr("Takes out the profiles: %s.", _names(diff.profiles_gone)))
        if diff.profiles_switched:
            lines.append(tr("Sets back which mods are on in the profiles: %s.",
                            _names(diff.profiles_switched)))
        if diff.chosen is not None:
            lines.append(tr("Chooses the profile %s.", widgets.named(diff.chosen[1])))
        for mod, why in alone.not_back:
            if why == histories.YOURS:
                lines.append(tr("%s is a mod of yours, and comes back only with its project.",
                                widgets.named(histories.label_of(mod))))
            else:
                lines.append(tr("%s cannot be put back: its folder or its project is not "
                                "there any more.", widgets.named(histories.label_of(mod))))
        lines = ["• " + line for line in lines]
        # Counted by the mod, as the list of a checkpoint counts them, not by
        # the line: a line names several mods, and a mod not put back is no
        # change at all.
        changes = (len(diff.gone) + len(back) + len(diff.on) + len(diff.off) + len(diff.moved)
                   + len(diff.settings) + len(diff.profiles_added) + len(diff.profiles_gone)
                   + (diff.chosen is not None))
        when = date_text(target.timestamp, clock=True)
        if changes:
            text = trn("Restore the loadout to %(when)s? It changes %(n)s thing in its list.",
                       "Restore the loadout to %(when)s? It changes %(n)s things in its list.",
                       changes, when=when)
        elif lines:
            text = tr("Restore the loadout to %s? It changes nothing in its list.", when)
        else:
            text = tr("Restore the loadout to %s? Its list is as it was then.", when)
        parts = [text] + (["\n".join(lines)] if lines else [])
        if whole.projects:
            names = [self.dialog.mod_name(origin) for origin in whole.projects]
            loadouts = []
            for origin in whole.projects:
                loadouts += [name for name in self.dialog.loadouts_with(origin) if name not in loadouts]
            parts.append(tr(
                "With each project as it was then, %(mods)s go back as well, which "
                "changes them in every loadout that has them: %(loadouts)s. Their "
                "build numbers, mod.io links and what was published stay as they are.",
                mods=_names(names), loadouts=_names(loadouts)))
        parts.append(tr("The loadout is saved as a checkpoint first, so nothing is lost."))
        return "\n\n".join(parts)
