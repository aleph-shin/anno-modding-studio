"""Patterns: a search over the game's assets and the edit made to every
match, kept as one op rather than as the hundreds of changes it comes to.

``core.keptops`` says when what such an op matches has drifted;
``ui.pattern_dialog`` is where one is written, and puts it in the loadout as
the XML view puts a typed ModOp there.  This is the window's
side -- opening the dialog for a new pattern or an existing one, and checking
every one in the loadout against the game's files as they are now, with
what to do about the ones that have moved -- held as ``window.patterns``
(Stage 3.3 of docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from ..core import keptops
from ..core.i18n import tr, trn
from . import widgets
from .widgets import ask, busy_cursor


class PatternsController:
    """The window's patterns, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window

    def new_pattern(self) -> None:
        """Write an edit aimed at every asset of a given shape."""
        self._pattern_dialog(None)

    def edit_pattern(self, uid: str) -> None:
        op = self.window.loadout.op(uid)
        if op is None:
            return
        if not op.can_expand:
            QMessageBox.information(
                self.window,
                tr("No pattern to edit"),
                tr("This pattern came from a loadout saved before patterns kept the ModOp "
                   "they were written as, so there is nothing here to re-run."),
            )
            return
        if self.window.index is None or self.window.workspace is None:
            return
        from .pattern_dialog import holds

        # One the dialog can only show, as its author wrote it, changes
        # nothing, so there is nothing to ask about where a change goes.
        if not holds(op, self.window.loadout.game):
            self._pattern_dialog(op)
            return
        # Somebody else's pattern is changed in their file only where that is
        # what you are doing; otherwise it is written again in your patch.
        where = self.window.patches.place_for_a_pattern(op.origin)
        if where is None:
            return
        # Read again: a copy of the mod made as the answer took its ops, this
        # one among them, and is changed where it is -- not written again.
        op = self.window.loadout.op(uid)
        if op is None:
            return
        if where == op.origin:
            self._pattern_dialog(op)
        else:
            self._pattern_dialog(None, origin=where, copy_of=op)

    def _pattern_dialog(self, op, origin=None, copy_of=None) -> None:
        if self.window.index is None or self.window.workspace is None:
            return
        from .pattern_dialog import PatternDialog

        dialog = PatternDialog(
            self.window.workspace, self.window, op, origin=origin, copy_of=copy_of
        )
        try:
            if dialog.exec() != int(QDialog.DialogCode.Accepted):
                return
            # In the loadout already, with what it comes to: a new one after
            # every op read from the mod, the same as any other edit made
            # here, and an edited one in its op's place (typedops.store_one).
            made = dialog.result_op
            if made is None:
                return
            self.window.on_changes_updated()
            # The asset open in the editor may be one it matches, and went
            # on showing the asset without it, in Fields and in XML.
            self.window.editor.refresh_from_loadout()
            # Translators: what Edit ▸ Undo would take back, said after "Undo".
            self.window.record(tr("the pattern %s", made.expression))
            # About this step, and dropped by the next: kept, it went on
            # saying the pattern's old value after the pattern was typed
            # over in the XML view (H2-2).
            # Translators: %(changes)s and %(assets)s are counted phrases such
            # as "3 changes" and "2 assets".
            self.window.say_of_a_step(tr(
                "%(pattern)s: %(changes)s across %(assets)s matching %(expression)s.",
                pattern=made.summary or tr("pattern"),
                changes=trn("%(n)s change", "%(n)s changes", len(dialog.changes)),
                assets=trn("%(n)s asset", "%(n)s assets",
                           len({change.guid for change in dialog.changes})),
                expression=made.expression,
            ))
        finally:
            dialog.deleteLater()

    def check_patterns(self, asked: bool = False, among=None) -> None:
        """Have the searches the loaded mods target by moved under them?

        A mod that says "every asset shaped like this" was rebuilt against the
        assets that answered it when it was read.  Adding another mod can put a
        new asset in front of that question -- the original mod would cover it,
        and the rebuilt one does not.  Nothing is done about it automatically:
        closing the gap means reading those mods again, which is a decision.
        """
        if self.window.index is None:
            return
        # A pattern is an op that searches, as keptops.drifted asks it: every
        # op remembers what it matched, one naming its GUIDs as well.
        found = [op for op in self.window.loadout.ops if op.searches]
        if not found:
            if asked:
                QMessageBox.information(
                    self.window,
                    tr("No patterns"),
                    tr("None of the loaded mods targets its assets by search, so "
                       "there is nothing here whose answer could move."),
                )
            return

        if self.window.workspace is None:
            return
        drifts = keptops.drifted(self.window.loadout, self.window.workspace, among)

        if not drifts:
            if asked:
                QMessageBox.information(
                    self.window,
                    tr("Still up to date"),
                    trn("%(n)s pattern checked. It matches the same assets as when "
                        "it was read.",
                        "%(n)s patterns checked. Each matches the same assets as "
                        "when it was read.",
                        len(found)),
                )
            return

        name = self.window.changes_panel._mod_name
        lines = [keptops.summarise(drifts, name), ""]
        lines.extend("  • " + drift.describe(name) for drift in drifts[:10])
        if len(drifts) > 10:
            lines.append(trn("  … and %(n)s more", "  … and %(n)s more",
                             len(drifts) - 10))

        # Most patterns still hold the op they came from, so the gap can simply
        # be closed: run the op against the assets it did not cover and keep what
        # it does. That settles new assets only. One read before ops were kept
        # has nothing to run, and one whose assets are gone -- a mod unloaded
        # took them -- still keeps the edits it made to them; both need the
        # mods reading again, and each is told why.
        def holds(drift) -> bool:
            op = self.window.loadout.op(drift.uid)
            return op is not None and op.can_expand

        can_run = [drift for drift in drifts if drift.arrived and holds(drift)]
        old = [drift for drift in drifts if not holds(drift)]
        gone = [drift for drift in drifts if drift.gone and holds(drift)]
        lines.append(self._why(can_run, old, gone))
        text = "\n".join(lines)

        if can_run and (old or gone):
            choice = self._run_or_read(text)
            if choice == "read":
                self.window.load_installed_mods()
                return
            if choice != "run":
                return
        elif can_run:
            if not ask(
                self.window,
                tr("A pattern has more to cover"),
                text,
                tr("&Run them again"),
                cancel=tr("&Not now"),
            ):
                return
        else:
            if ask(
                self.window,
                tr("A search has moved"),
                text,
                tr("&Read the mods again"),
                cancel=tr("&Not now"),
                default_action=False,
            ):
                self.window.load_installed_mods()
            return

        with busy_cursor():
            made, covered = keptops.extend(
                self.window.loadout, self.window.index, self.window.workspace, among
            )
            for change in made:
                self.window.loadout.set_change(change)
        self.window.on_changes_updated()
        # Translators: the name of an undo step, shown after "Undo".
        self.window.record(trn("extending %(n)s pattern",
                         "extending %(n)s patterns",
                         len(covered)))
        # Three counts, so each is counted on its own and put in whole.
        self.window.say_of_a_step(tr(
            "Extended %(patterns)s to %(assets)s: %(changes)s added.",
            patterns=trn("%(n)s pattern",
                      "%(n)s patterns", len(covered)),
            assets=trn(
                "%(n)s asset",
                "%(n)s assets",
                len({guid for _op, guids in covered for guid in guids}),
            ),
            changes=trn("%(n)s change",
                        "%(n)s changes", len(made)),
        ))

    @staticmethod
    def _why(can_run, old, gone) -> str:
        """What can be done about the patterns listed, and why: ``can_run``
        hold their op and have new assets to cover, ``old`` were read before
        ops were kept, ``gone`` hold their op and matched assets no longer
        loaded. A pattern can be in ``can_run`` and ``gone`` both."""
        if not old and not gone:
            return tr(
                "\n"
                "Those patterns still hold the ModOp they came from, so they "
                "can be run again over the assets they have not covered."
            )
        if not can_run and not gone:
            return tr(
                "\nThose mods were read before the Studio kept the ModOps they "
                "target by, so there is nothing here to ask again. Reading the mods "
                "again puts that right."
            )
        if not can_run and not old:
            return tr(
                "\nSome of the assets those patterns matched are no longer loaded, "
                "and the edits they made to them are still kept. Reading the mods "
                "again puts that right."
            )
        # Some of each: every kind is counted, among the patterns listed.
        parts = []
        if can_run:
            # Translators: "them" is the patterns listed above.
            parts.append(trn(
                "%(n)s of them still holds the ModOp it came from, so it can be "
                "run again over the new assets it has not covered.",
                "%(n)s of them still hold the ModOp they came from, so they can "
                "be run again over the new assets they have not covered.",
                len(can_run)))
        if old:
            # Translators: "them" is the patterns listed above.
            parts.append(trn(
                "%(n)s of them was read before the Studio kept the ModOps mods "
                "target by, so there is nothing here to run again.",
                "%(n)s of them were read before the Studio kept the ModOps mods "
                "target by, so there is nothing here to run again.",
                len(old)))
        if gone:
            # Translators: "them" is the patterns listed above.
            parts.append(trn(
                "%(n)s of them matched assets that are no longer loaded, and "
                "still keeps the edits it made to them.",
                "%(n)s of them matched assets that are no longer loaded, and "
                "still keep the edits they made to them.",
                len(gone)))
        parts.append(
            tr("Running them again covers the new assets only. Reading the mods "
               "again puts every one of them right.")
            if can_run
            else tr("Reading the mods again puts every one of them right.")
        )
        return "\n" + " ".join(parts)

    def _run_or_read(self, text: str) -> str:
        """Ask whether to run the patterns that can be run again, or read the
        mods again for all of them: "run", "read", or "" for neither."""
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("A pattern has more to cover"))
        box.setText(text)
        run = box.addButton(tr("&Run them again"), QMessageBox.ButtonRole.AcceptRole)
        read = box.addButton(tr("&Read the mods again"), QMessageBox.ButtonRole.ActionRole)
        stay = box.addButton(tr("&Not now"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(run)
        box.setEscapeButton(stay)
        try:
            answer = widgets.answer_of(box)
        finally:
            box.deleteLater()
        if answer is run:
            return "run"
        if answer is read:
            return "read"
        return ""
