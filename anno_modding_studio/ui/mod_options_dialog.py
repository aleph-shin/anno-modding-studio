"""The settings a mod offers, as the mod itself declares them.

Some mods are configurable.  An Anno 117 mod declares options in its modinfo
and its ModOps read the chosen values back: an op guarded by
``Condition="$gates_nomaintenancecosts"`` only runs when a toggle is on.  An
Anno 1800 mod does the same through iModYourAnno's markup, and the answer is
written into the mod's own files (see :mod:`core.imya`).  Neither game has a
screen for them -- a mod manager answers them -- and asking them here is what
lets the changes list show what the answers actually do.

The controls are the ones the mod asked for -- a toggle is a tick box, a slider a
number with the author's own range, an enum a list of their own wording -- and a
mod that declares a dummy option purely to head a section gets a heading.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import settings as settings_mod
from ..core.i18n import tr, tr_kw
from .help_tip import HelpTip
from .widgets import tab_as_laid_out

# More decimals than any mod's number has, and few enough that a spin box is
# still a box: a float has about fifteen digits that mean anything.
_MOST_PLACES = 12


def _places(text) -> int:
    """How many decimals a number as written has: 2 for "0.25", 0 for "3"
    and for anything that is not a number."""
    try:
        exponent = Decimal(str(text).strip()).normalize().as_tuple().exponent
    except (InvalidOperation, ValueError):
        return 0
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _number_text(value: float, places: int) -> str:
    """A spin box's value as a mod's ops read it: no trailing zeros, and a
    whole number without its point."""
    text = "%.*f" % (places, value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _state(control):
    """What a control says, as itself: to tell whether it was touched."""
    if isinstance(control, QCheckBox):
        return control.isChecked()
    if isinstance(control, QLineEdit):
        return control.text()
    if isinstance(control, QDoubleSpinBox):
        return control.value()
    return control.currentData()


def _shows(control, text: str) -> bool:
    """Whether ``control`` shows ``text`` as it is, rather than a choice it
    fell back on for a value it cannot show."""
    if isinstance(control, QComboBox):
        return control.findData(text) == control.currentIndex()
    if isinstance(control, QDoubleSpinBox):
        try:
            return math.isclose(float(text), control.value(), rel_tol=1e-12, abs_tol=1e-15)
        except (TypeError, ValueError):
            return False
    return True


class _TickLabel(QLabel):
    """A tick box's question, wrapping, that ticks the box when clicked --
    what a QCheckBox's own text does, except that its own text cannot wrap."""

    def __init__(self, text: str, box: QCheckBox):
        super().__init__(text)
        self._box = box
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._box.isEnabled():
            self._box.toggle()
        super().mouseReleaseEvent(event)


class ModOptionsDialog(QDialog):
    """Choose what a mod's own settings are set to, before it is read."""

    def __init__(self, name: str, offered, chosen=None, parent=None):
        super().__init__(parent)
        self.offered = offered
        self.controls = {}
        self.values = offered.settled(chosen)

        self.setWindowTitle(tr("Options for %s", name))
        self.setMinimumWidth(560)
        self._build_ui(name)
        self._remember_shown()
        self.resize(680, 640)

    def _build_ui(self, name: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title names the mod. Where the options start stays in view, and
        # what changing one does is behind the ?.
        head = QHBoxLayout()
        hint = QLabel(tr("These options start at what the game runs now."))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        # Translators: "Play" is the Studio's button that builds your mods and
        # starts the game; use the same word as that button.
        head.addWidget(HelpTip(tr_kw(
            "The mod's author offers these options, and what the mod does "
            "depends on them. Changing one re-reads the mod, so the changes "
            "list shows what that setting does. The game uses it once you "
            "press Play. Where the author offers an option, use it and not a "
            "[[patch]]: it survives the mod's updates and stays inside the range "
            "the author meant."
        ), self.windowTitle()))
        outer.addLayout(head)

        # Everything left-aligned and wrapping, so a mod with seventy-character
        # questions -- iModYourAnno's often are -- reads down the page instead
        # of pushing a scroll bar along the bottom. A tick box sits in front of
        # its question; any other control sits beside it.
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.labels = {}
        row = 0
        for option in self.offered:
            if option.is_heading:
                label = QLabel(option.label)
                label.setObjectName("SubHeading")
                label.setWordWrap(True)
                if row:
                    grid.setRowMinimumHeight(row, 6)
                    row += 1
                grid.addWidget(label, row, 0, 1, 3)
                row += 1
                continue
            control = self._control(option)
            # The question beside it is its name, in the mod's own words.
            control.setAccessibleName(option.label)
            if isinstance(control, QCheckBox):
                label = _TickLabel(option.label, control)
                grid.addWidget(control, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(label, row, 1, 1, 2)
            else:
                label = QLabel(option.label)
                label.setWordWrap(True)
                grid.addWidget(label, row, 0, 1, 2)
                grid.addWidget(control, row, 2, Qt.AlignmentFlag.AlignTop)
            if option.description:
                label.setToolTip(option.description)
                if not control.toolTip():
                    control.setToolTip(option.description)
            self.labels[option.name] = label
            row += 1
        grid.setRowStretch(row, 1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        outer.addWidget(area, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        reset = QPushButton(tr("The mod's own &defaults"))
        reset.clicked.connect(self._reset)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        outer.addWidget(buttons)

    def _control(self, option):
        value = self.values.get(option.name, option.default)
        if option.kind == settings_mod.TOGGLE:
            # No text of its own: the question beside it is the label, and the
            # author's own word for "on", where they gave one, is its tooltip.
            box = QCheckBox()
            box.setChecked(settings_mod.is_on(value))
            said = option.label_for("true")
            if said and said.lower() != "true":
                box.setToolTip(said)
            self.controls[option.name] = box
            return box

        if option.kind == settings_mod.TEXT:
            line = QLineEdit(str(value))
            self.controls[option.name] = line
            return line
        span = option.range
        if span is not None:
            low, high, step = span
            spin = QDoubleSpinBox()
            # As many decimals as the author's own numbers and the value it
            # starts at have, and no more. Two, whatever the step, rounded a
            # value of 0.0004 to nothing (H2-7).
            spin.setDecimals(min(_MOST_PLACES, max(
                _places(text) for text in (*option.values[:3], value)
            )))
            spin.setRange(low, high)
            spin.setSingleStep(step)
            try:
                spin.setValue(float(value))
            except (TypeError, ValueError):
                spin.setValue(low)
            if option.labels:
                spin.setToolTip(option.labels[0])
            self.controls[option.name] = spin
            return spin

        combo = QComboBox()
        for choice in option.values or (option.default,):
            combo.addItem(option.label_for(choice), choice)
        position = combo.findData(value)
        combo.setCurrentIndex(position if position >= 0 else 0)
        self.controls[option.name] = combo
        return combo

    def _remember_shown(self) -> None:
        """What each control says now, beside the value that put it there --
        where it shows that value, and not a choice it fell back on for one
        it cannot show: no longer among a list's, or out of a slider's range."""
        self._shown = {}
        for name, control in self.controls.items():
            text = str(self.values.get(name, ""))
            if _shows(control, text):
                self._shown[name] = (_state(control), text)

    # -- reading the controls back --------------------------------------
    def _read(self, option, control) -> str:
        # A control nobody touched gives back the value it was given, as it
        # was spelt: read back through a spin box, 0.0004166667 came out as
        # 0.000416667, and True as true -- an answer nobody gave, and a
        # mod read again for it (H2-7).
        shown = self._shown.get(option.name)
        if shown is not None and shown[0] == _state(control):
            return shown[1]
        if isinstance(control, QCheckBox):
            return "true" if control.isChecked() else "false"
        if isinstance(control, QLineEdit):
            return control.text()
        if isinstance(control, QDoubleSpinBox):
            return _number_text(control.value(), control.decimals())
        data = control.currentData()
        return str(data) if data is not None else option.default

    def chosen(self) -> dict:
        """What the controls say, as the mod's ops will read it."""
        found = dict(self.values)
        for name, control in self.controls.items():
            option = self.offered.get(name)
            if option is not None:
                found[name] = self._read(option, control)
        return found

    def _reset(self) -> None:
        self.values = self.offered.defaults()
        for name, control in list(self.controls.items()):
            option = self.offered.get(name)
            if option is None:
                continue
            value = option.default
            if isinstance(control, QCheckBox):
                control.setChecked(settings_mod.is_on(value))
            elif isinstance(control, QLineEdit):
                control.setText(str(value))
            elif isinstance(control, QDoubleSpinBox):
                try:
                    control.setDecimals(max(control.decimals(), min(_MOST_PLACES, _places(value))))
                    control.setValue(float(value))
                except (TypeError, ValueError):
                    pass
            else:
                position = control.findData(value)
                control.setCurrentIndex(position if position >= 0 else 0)
        self._remember_shown()

    def _accept(self) -> None:
        self.values = self.chosen()
        self.accept()
