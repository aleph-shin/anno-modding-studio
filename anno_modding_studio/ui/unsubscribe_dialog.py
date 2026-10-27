"""The game's mod browser's copies of mods the library has, to unsubscribe from in game.

A list, not an action: the game's client lets go of a copy only when the
account unsubscribes in its own browser -- one unsubscribed from through
mod.io's API stayed, folder and all -- so the Studio says which copies to
unsubscribe from, and where each stands by the game's own files, and leaves
the doing to the game.  No sign-in and no network.  See :mod:`core.unsubscribe`.

The one thing it does itself is for copies another account holds, which
unsubscribing as the game's account cannot free, and which an account merged
into another or closed never will: it starts the game's mod browser's folder
afresh, setting it aside into the backups, when the game loads nothing from
it.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core import unsubscribe
from ..core.i18n import tr, tr_noop, trn
from . import theme
from .help_tip import HelpTip
from .widgets import SORT_ROLE, sortable_tree, tab_as_laid_out, wrappable_path

COLUMNS = (
    tr_noop("Mod"), tr_noop("mod.io id"), tr_noop("In the library"),
    tr_noop("In the cache"), tr_noop("What to do"),
)


class UnsubscribeDialog(QDialog):
    """``look_again`` reads the folders afresh and returns the new list, for
    after a start of the game.  ``afresh`` is whether the mod browser's
    folder can be started afresh now (a :class:`core.unsubscribe.Afresh`),
    and ``start_afresh`` does it, returning where the folder went."""

    def __init__(self, left: List[unsubscribe.Left], parent=None, *, game_account=None,
                 look_again: Optional[Callable[[], List[unsubscribe.Left]]] = None,
                 afresh: Optional[unsubscribe.Afresh] = None,
                 start_afresh: Optional[Callable[[], object]] = None):
        super().__init__(parent)
        self.left = list(left)
        self.game_account = game_account
        self._look_again = look_again
        self.afresh = afresh
        self._start_afresh = start_afresh
        self.setWindowTitle(tr("Unsubscribe from the game's copies"))
        self.resize(980, 600)
        self._build_ui()
        self._fill()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title says what this is for. What to do stays in view, and why
        # it has to be done in the game is behind the ?.
        head = QHBoxLayout()
        hint = self.hint = QLabel(tr(
            "Unsubscribe from each mod marked below in the game's own mod "
            "browser, under your subscriptions."
        ))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        self.help = HelpTip(tr(
            "The library has its own copy of each of these, and that copy is "
            "what the game loads. The game's mod browser keeps a copy too, "
            "and keeps it up to date, until you unsubscribe there. "
            "Unsubscribing anywhere else, on mod.io's site or in another "
            "tool, leaves the game's copy where it is. Afterwards the game "
            "should remove its copies. This list is read from the game's "
            "files, so look again after the game's next start."
        ), self.windowTitle())
        head.addWidget(self.help)
        outer.addLayout(head)

        self.wide = QLabel(tr(
            "Unsubscribing removes the mod on every machine you play on with "
            "that mod.io account, even one without the Studio."
        ))
        self.wide.setWordWrap(True)
        theme.tint(self.wide, "warn")
        outer.addWidget(self.wide)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels([tr(column) for column in COLUMNS])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.tree.setAccessibleName(tr("The game's copies"))
        outer.addWidget(self.tree, 1)
        self.sorting = sortable_tree(self.tree)

        # What the fresh start did, once it has. Not called "done": that is
        # the method QDialog closes through, and a label there stops it closing.
        self.outcome = QLabel("")
        self.outcome.setWordWrap(True)
        self.outcome.setVisible(False)
        outer.addWidget(self.outcome)

        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.afresh_button = self.box.addButton(
            tr("Start the mod browser's folder &afresh…"),  # design: names the folder it resets
            QDialogButtonBox.ButtonRole.ActionRole)
        self.afresh_button.clicked.connect(self._afresh)
        self.again = self.box.addButton(tr("&Look again"), QDialogButtonBox.ButtonRole.ActionRole)
        self.again.setToolTip(tr("Reads the game's files again. Use it after the game has started."))
        self.again.clicked.connect(self._again)
        self.again.setVisible(self._look_again is not None)
        self.box.rejected.connect(self.reject)
        tab_as_laid_out(self.box)
        outer.addWidget(self.box)

    def _fill(self) -> None:
        self.tree.clear()
        for one in self.left:
            cached = one.version or "—"
            item = QTreeWidgetItem([
                one.name, str(one.modio_id), one.library_version or "—",
                tr("%s (the game loads this one)", cached) if one.loads else cached,
                one.describe(),
            ])
            # A missing version sorts as an empty cell, last either way, and
            # not as the dash it shows, which went first sorted descending.
            for column, version in ((2, one.library_version), (3, one.version)):
                if not version:
                    item.setData(column, SORT_ROLE, "")
            item.setToolTip(0, str(one.folder))
            item.setToolTip(3, str(one.folder))
            item.setToolTip(4, one.explain())
            if one.standing != unsubscribe.LISTED:
                item.setForeground(4, self.palette().placeholderText())
            self.tree.addTopLevelItem(item)
        self.summary.setText(self.summarise())
        self._show_afresh()

    def _held_by_others(self) -> list:
        return [one for one in self.left if one.standing == unsubscribe.OTHERS or one.shared]

    def _show_afresh(self) -> None:
        """Offered for copies another account holds -- which nothing else
        frees -- and able only while the game loads nothing from the folder."""
        plan = self.afresh
        offered = plan is not None and self._start_afresh is not None and bool(self._held_by_others())
        self.afresh_button.setVisible(offered)
        if not offered:
            return
        why = plan.why_not()
        self.afresh_button.setEnabled(not why)
        self.afresh_button.setToolTip(why or tr(
            "Sets the folder aside into the Studio's backups. The game then "
            "rebuilds it from your subscriptions."))

    def summarise(self) -> str:
        if not self.left:
            return tr("The game's mod browser keeps no copies of the library's mods any more.")
        counts = {standing: [one for one in self.left if one.standing == standing]
                  for standing in (unsubscribe.LISTED, unsubscribe.DROPPED, unsubscribe.OTHERS,
                                   unsubscribe.UNKNOWN)}
        account = self.game_account
        lines = []
        if counts[unsubscribe.LISTED] or counts[unsubscribe.UNKNOWN]:
            count = len(counts[unsubscribe.LISTED]) + len(counts[unsubscribe.UNKNOWN])
            if account is not None and account.id and account.username:
                lines.append(trn("%(n)s to unsubscribe from in game as %(who)s.",
                                 "%(n)s to unsubscribe from in game as %(who)s.",
                                 count, who=account.username))
            elif account is not None and account.id:
                lines.append(trn("%(n)s to unsubscribe from in game as account %(id)s.",
                                 "%(n)s to unsubscribe from in game as account %(id)s.",
                                 count, id=str(account.id)))
            else:
                lines.append(trn("%(n)s to unsubscribe from in game.",
                                 "%(n)s to unsubscribe from in game.", count))
        if counts[unsubscribe.DROPPED]:
            lines.append(trn(
                "%(n)s was unsubscribed from outside the game, and the game still keeps "
                "it: subscribe again in its mod browser, then unsubscribe there.",
                "%(n)s were unsubscribed from outside the game, and the game still keeps "
                "them: subscribe again in its mod browser, then unsubscribe there.",
                len(counts[unsubscribe.DROPPED])))
        if counts[unsubscribe.OTHERS]:
            lines.append(trn(
                "%(n)s is kept for another mod.io account that has signed in "
                "to the game here. Only that account can let go of it.",
                "%(n)s are kept for another mod.io account that has signed "
                "in to the game here. Only that account can let go of them.",
                len(counts[unsubscribe.OTHERS])))
        shared = [one for one in self.left if one.shared]
        if counts[unsubscribe.OTHERS] or shared:
            lines.append(tr(
                "If that account no longer exists, because it was merged "
                "into another or closed, it will never let go of what it "
                "holds. Starting the mod browser's folder afresh does: the "
                "game rebuilds it from your subscriptions alone."))
        if shared:
            lines.insert(len(lines) - 1, trn(
                "%(n)s is held by another account on this machine as well, and the game "
                "keeps it while that account does.",
                "%(n)s are held by another account on this machine as well, and the game "
                "keeps them while that account does.",
                len(shared)))
        return " ".join(lines)

    def confirm_afresh(self) -> bool:
        """Whether to start the folder afresh, having said what it does.  A
        method so a test can answer it."""
        plan = self.afresh
        lines = [
            tr("The game's mod browser's folder, %s, is set aside into the "
               "Studio's backups. It is kept, not deleted. The game starts "
               "it afresh the next time its mod browser opens, from your "
               "account's subscriptions alone.",
               wrappable_path(plan.folder)),
            "",
            trn("%(n)s copy held for your account comes back, held by it alone.",
                "%(n)s copies held for your account come back, held by it alone.",
                len(plan.come_back)),
        ]
        if plan.stay_out:
            # A list after a colon, not part of the sentence.
            names = ", ".join(plan.stay_out[:8])
            if len(plan.stay_out) > 8:
                lines.append(trn(
                    "%(n)s held only for another account is not downloaded again: "
                    "%(names)s and more.",
                    "%(n)s held only for another account are not downloaded again: "
                    "%(names)s and more.",
                    len(plan.stay_out), names=names))
            else:
                lines.append(trn(
                    "%(n)s held only for another account is not downloaded again: %(names)s.",
                    "%(n)s held only for another account are not downloaded again: %(names)s.",
                    len(plan.stay_out), names=names))
        lines += ["", tr("The game loads the library's copies either way, and stays signed in.")]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Start the mod browser's folder afresh"))
        box.setText("\n".join(lines))
        start = box.addButton(tr("&Start afresh"), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is start

    def _afresh(self) -> None:
        if self.afresh is None or self._start_afresh is None or not self.confirm_afresh():
            return
        try:
            aside = self._start_afresh()
        except (OSError, ValueError) as exc:
            self.outcome.setText(tr("The folder was not set aside: %s", exc))
            theme.tint(self.outcome, "warn")
            self.outcome.setVisible(True)
            return
        self.outcome.setText(tr(
            "Set aside to %s. Start the game and open its mod browser once: it "
            "downloads your subscriptions again, held for your account alone. Look "
            "again here afterwards.", wrappable_path(aside)))
        theme.tint(self.outcome)
        self.outcome.setVisible(True)
        self.afresh = None
        if self._look_again is not None:
            self.left = list(self._look_again())
        self._fill()

    def _again(self) -> None:
        if self._look_again is None:
            return
        self.left = list(self._look_again())
        self._fill()
