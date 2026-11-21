"""Which option some ModOps are put under: one the mod declares, or a new
toggle, named here.

What the ModOps go into is ``<Group Condition="$mod-id.name">``, which runs only
while the option is on (:mod:`core.option_groups`).  A toggle is what that
asks of: on or off.  So the options offered are the mod's toggles, and the
new one is a toggle, on unless the player turns it off -- what the mod did
before it had the option.

An option whose group is in the file already has them put in it: a change
made later goes in with the ones put under it before, and not into a group
of its own beside them.  Unticked, they get a group of their own where they
stand, as for a new toggle.  What is under the option already stays where
it is either way: a group of the option inside one of the option asks
nothing.  So where all of them are, the box is greyed and OK is too.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..core import declaring, option_groups, settings
from ..core.i18n import tr, trn
from . import theme
from .widgets import problem_footer

# What the dialog starts a new option's name from, as the label is typed.
_NOT_NAMED = re.compile(r"[^a-z0-9_-]+")


def name_from(label: str) -> str:
    """A name the loader reads, made of ``label``: "Shrine of Ceres" is
    ``shrine-of-ceres``."""
    name = _NOT_NAMED.sub("-", label.lower()).strip("-")
    return name if not name or name[0].isalpha() or name[0] == "_" else "_" + name


class OptionGroupDialog(QDialog):
    """Pick or make the toggle some ModOps are to be put under.

    ``planned``, where given, says for one of the mod's options what putting
    the ModOps under it comes to: into its own groups, or with the second
    argument off each in a group of its own where it stands
    (:func:`core.option_groups.planned`)."""

    def __init__(
        self, loadout, origin: str, picked: int, groups: int, parent=None,
        planned: Optional[Callable[[str, bool], option_groups.Plan]] = None,
    ):
        super().__init__(parent)
        self.offered = settings.read(settings.block_of(loadout, origin))
        self._picked, self._groups = picked, groups
        self._planned = planned
        self._plans: dict = {}
        self.setWindowTitle(tr("Put under an option"))
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        self.said = QLabel()
        self.said.setWordWrap(True)
        self.said.setObjectName("StepHint")
        outer.addWidget(self.said)

        form = self._form = QFormLayout()
        self.which = QComboBox()
        # Translators: the first choice of which option to put ModOps under.
        self.which.addItem(tr("A new toggle"), "")
        for option in self.offered.settable:
            if option.kind == settings.TOGGLE:
                self.which.addItem("%s  ($%s)" % (option.label, option.name), option.name)
        self.which.currentIndexChanged.connect(self._refresh)
        form.addRow(tr("&Option", context="option"), self.which)
        self.join_box = QCheckBox(tr("Into its &existing group"))
        self.join_box.setChecked(True)
        self.join_box.setToolTip(tr(
            "Joins what was put under it before. Unticked: a new group where they stand."
        ))
        self.join_box.toggled.connect(self._refresh)
        form.addRow("", self.join_box)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(tr("what a mod manager shows, such as the part it turns off"))
        self.label_edit.textEdited.connect(self._label_typed)
        form.addRow(tr("&Label", context="option"), self.label_edit)
        self.name_edit = QLineEdit()
        theme.monospace(self.name_edit)
        self.name_edit.textEdited.connect(self._name_typed)
        form.addRow(tr("&Name", context="option"), self.name_edit)
        self.on_box = QCheckBox(tr("On &by default"))
        self.on_box.setChecked(True)
        self.on_box.setToolTip(tr("On, the mod does what it did before it had the option."))
        form.addRow("", self.on_box)
        outer.addLayout(form)

        footer, self.problem, self.buttons = problem_footer(self, self.accept)
        outer.addLayout(footer)
        # The name follows the label until it is typed in itself.
        self._named = False
        self._refresh()

    def _label_typed(self, text: str) -> None:
        if not self._named:
            self.name_edit.setText(name_from(text))
        self._refresh()

    def _name_typed(self, _text: str) -> None:
        self._named = True
        self._refresh()

    def chosen(self) -> str:
        """The name of the option the ModOps go under."""
        return str(self.which.currentData() or "") or self.name_edit.text().strip()

    def new_option(self) -> Optional[settings.Option]:
        """The toggle to declare, or None where the mod declares it."""
        if self.which.currentData():
            return None
        name = self.name_edit.text().strip()
        return settings.Option(
            name=name, label=self.label_edit.text().strip() or name, kind=settings.TOGGLE,
            default="true" if self.on_box.isChecked() else "false",
        )

    def plan(self) -> Optional[option_groups.Plan]:
        """What OK does for the chosen option, where any of them can go into
        its group or is in one already: into that group, ticked, and else a
        group of their own where they stand -- what is under it already left
        where it is, either way.  None for a new toggle and for an option
        with no such group."""
        name = str(self.which.currentData() or "")
        if self._plan_of(name) is None:
            return None
        return self._plan_of(name, self.join_box.isChecked())

    def _plan_of(self, name: str, join: bool = True) -> Optional[option_groups.Plan]:
        """The plan for ``name``, where any of them goes into its group or
        is in one already: with them put into it, or with ``join`` off,
        each in a group of its own where it stands."""
        if not name or self._planned is None:
            return None
        for one in {True, join}:
            if (name, one) not in self._plans:
                self._plans[(name, one)] = self._planned(name, one)
        found = self._plans[(name, True)]
        return self._plans[(name, join)] if found.joined or found.already else None

    def problem_text(self) -> str:
        new = self.new_option()
        if new is None:
            plan = self.plan()
            if plan is not None and plan.nothing_to_do:
                return tr("They are under that option already, in its group.")
            return ""
        if self.offered.get(new.name) is not None:
            return tr("The mod already has an option called %s.", new.name)
        return "\n".join(declaring.problems([new]))

    def _said_text(self) -> str:
        plan = self.plan()
        said = []
        if plan is None or not self.join_box.isChecked():
            # Each in a group of its own where it stands.
            count = self._picked if plan is None else plan.alone
            groups = self._groups if plan is None else len(plan.left)
            if count:
                said.append(trn(
                    "%(n)s ModOp goes into a group that runs only while the option is on.",
                    "%(n)s ModOps go into a group that runs only while the option is on.",
                    count,
                ) + (" " + trn(
                    "They do not stand together in the file, so they go into %(n)s group.",
                    "They do not stand together in the file, so they go into %(n)s groups.",
                    groups,
                ) if groups > 1 else ""))
            if plan is not None and plan.already:
                said.append(self._already_text(plan.already))
            return " ".join(said)
        if plan.joined:
            said.append(trn(
                "%(n)s ModOp goes into the group the option has in the file, which runs only while it is on.",
                "%(n)s ModOps go into the group the option has in the file, which runs only while it is on.",
                plan.joined,
            ))
        if plan.passed:
            said.append(trn(
                "To get there they go past %(n)s other ModOp, which runs on their other side then.",
                "To get there they go past %(n)s other ModOps, which run on their other side then.",
                plan.passed,
            ))
        if plan.left:
            said.append(trn(
                "The rest have no such group beside them, and go into %(n)s group of their own.",
                "The rest have no such group beside them, and go into %(n)s groups of their own.",
                len(plan.left),
            ))
        if plan.already:
            said.append(self._already_text(plan.already))
        return " ".join(said)

    @staticmethod
    def _already_text(count: int) -> str:
        return trn(
            "%(n)s of them is under it already, and stays where it is.",
            "%(n)s of them are under it already, and stay where they are.",
            count,
        )

    def _refresh(self, *_args) -> None:
        new = not self.which.currentData()
        for widget in (self.label_edit, self.name_edit, self.on_box):
            widget.setEnabled(new)
        into = self._plan_of(str(self.which.currentData() or ""))
        self._form.setRowVisible(self.join_box, into is not None)
        # Everything under it already: unticked, it would be put in a group
        # of the option inside the group it is in.  Nothing is offered.
        self.join_box.setEnabled(into is not None and not into.nothing_to_do)
        self.said.setText(self._said_text())
        # Nothing typed yet is nothing wrong yet: OK waits, and says nothing.
        blank = new and not self.name_edit.text().strip()
        why = "" if blank else self.problem_text()
        self.problem.setText(why)
        self.problem.setVisible(bool(why))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not why and not blank)
