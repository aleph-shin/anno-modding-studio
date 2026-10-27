"""A mod's own options, declared like its other details.

Anno 117 lets a mod offer options. They are declared in its modinfo's
``Options`` block -- each with a default, and for a mod manager a label, a
kind and the choices -- and its ModOps read them back as ``$name``: in a
``Condition``, or in ``<ModValue Insert="…"/>``. The loader runs the mod at
the defaults unless the player's ``active-options.jsonc`` says otherwise.

This is where a mod written here declares them. :class:`ModOptionsDialog
<.mod_options_dialog.ModOptionsDialog>` is the other half: what a mod's
options are *set to*, which is a question for whoever plays it.

A name the mod's ModOps already use and nothing declares is offered at the
top, since that is the usual way a mod comes to have an option: the op is
written first, and the declaration is what it was missing. Where an Apply
has just brought such names in, the dialog opens on its own with each of
them already in the list, of the kind its ops suggest, and marked as new:
proposals to look over, kept only by OK.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import declaring, settings
from ..core.i18n import tr, tr_noop, tr_stored, trn
from . import theme
from .help_tip import HelpTip
from .widgets import enter_activates, problem_footer, shown_row, single_line

# The kinds offered, in the order the list shows them. A kind the block
# already holds and the Studio does not offer stays as it is.
_KINDS = (
    # Translators: kinds of a mod's option, as a mod manager shows them.
    (settings.TOGGLE, tr_noop("Toggle", context="kind of option")),
    (settings.ENUM, tr_noop("Choice", context="kind of option")),
    (settings.SLIDER, tr_noop("Slider", context="kind of option")),
)
# Which page of the kind's own fields, and which editor of its default, a
# kind is shown with; any other kind gets the last of each.
_PAGES = {settings.TOGGLE: 0, settings.ENUM: 1, settings.SLIDER: 2}
_DEFAULTS = {settings.TOGGLE: 0, settings.ENUM: 1}
# The fields an option has in the dialog besides the ones it is kept with.
_OWN = ("was", "proposed")


def _item(option, was=None, proposed: bool = False) -> dict:
    """``option`` as the dialog has it: its fields, and the dialog's own."""
    return dict(settings.as_fields(option), was=was, proposed=proposed)


class OptionsEditorDialog(QDialog):
    """Declare, change and take out one mod's options."""

    def __init__(self, loadout, origin: str, mod_id: str, parent=None, proposed=(), focus="",
                 after_apply=False):
        super().__init__(parent)
        self.loadout = loadout
        self.origin = origin
        self.mod_id = mod_id
        self.original = settings.block_of(loadout, origin)
        # The options as the dialog has them, each as a plain dict of the
        # fields :class:`settings.Option` has, edited in place and turned
        # back into a block on OK, and two of the dialog's own (_OWN): "was",
        # the name the mod's ops read it by -- what it was declared as,
        # whatever it is called here since, None for one put in here, read by
        # the name it has -- and
        # "proposed", whether the dialog put it in itself, until OK keeps it.
        self.items = [_item(option, was=option.name) for option in settings.read(self.original)]
        self.used = declaring.named_by(loadout, origin, mod_id)
        # Where a use was asked to be shown, as ``(kind, which)``, once the
        # dialog has been kept for it.
        self.wanted_use = None
        # Whether there is a file tab to go to a use on: set by the opener.
        self.show_in_file = None
        taken = {item["name"] for item in self.items}
        for name in proposed:
            if name in taken:
                continue
            self.items.append(_item(
                settings.started_as(declaring.guessed_kind(loadout, origin, name), name),
                proposed=True,
            ))
        # Whether an Apply that brought the proposals in opened it, and
        # Cancel then takes that Apply back: the button says so. Said by the
        # opener: a link to a name nothing declares proposes it too, with
        # nothing applied.
        self.after_apply = after_apply
        self._showing = False
        self.mod_name = loadout.label_of(origin) or tr("your mod")
        self.setWindowTitle(tr("Options: %s", self.mod_name))
        self.setMinimumSize(760, 520)
        self._build_ui()
        first = next((at for at, item in enumerate(self.items) if item["proposed"]), 0)
        first = next((at for at, item in enumerate(self.items) if focus and item["name"] == focus), first)
        self._fill_list(first)

    # -- building ---------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        head = QHBoxLayout()
        hint = QLabel(tr("What this mod lets its players decide."))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        # Translators: Condition, ModValue and active-options.jsonc are the
        # loader's words and stay as they are.
        head.addWidget(HelpTip(tr(
            "Anno 117 lets a mod offer options. Its ModOps read each one as "
            "$name, or in full as $mod-id.name: in a Condition, or in "
            "<ModValue Insert=\"$name\"/>. The game runs the mod at each "
            "option's default unless the player's active-options.jsonc says "
            "otherwise. A mod manager shows the label and the choices."
        ), tr("Options")))
        outer.addLayout(head)

        # What the dialog put in itself, said once, above the rest.
        self.new_note = QLabel("")
        self.new_note.setWordWrap(True)
        theme.tint(self.new_note, "accent-text")
        outer.addWidget(self.new_note)

        missing_row = QHBoxLayout()
        self.missing = QLabel("")
        self.missing.setWordWrap(True)
        theme.tint(self.missing, "warn")
        missing_row.addWidget(self.missing, 1)
        self.declare_button = QPushButton(tr("&Declare them"))
        self.declare_button.setToolTip(tr("Adds each as a toggle, off unless the player turns it on."))
        self.declare_button.clicked.connect(self.declare_missing)
        missing_row.addWidget(self.declare_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(missing_row)

        body = QHBoxLayout()
        left = QVBoxLayout()
        # Translators: the options the mod declares.
        list_caption = QLabel(tr("&Options"))
        left.addWidget(list_caption)
        self.list = QListWidget()
        list_caption.setBuddy(self.list)
        theme.monospace(self.list)
        self.list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton(tr("&Add", context="verb"))
        self.add_button.clicked.connect(lambda: self.add_option())
        self.remove_button = QPushButton(tr("&Remove", context="verb"))
        self.remove_button.clicked.connect(self._remove)
        # Translators: moves the option up or down the list.
        self.up_button = QPushButton(tr("U&p", context="move"))
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = QPushButton(tr("Do&wn", context="move"))
        self.down_button.clicked.connect(lambda: self._move(1))
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            buttons.addWidget(button)
        left.addLayout(buttons)
        body.addLayout(left, 2)

        self.details = QWidget()
        form = QFormLayout(self.details)
        form.setContentsMargins(12, 0, 0, 0)
        self.name_edit = QLineEdit()
        theme.monospace(self.name_edit)
        self.name_edit.textEdited.connect(self._edited)
        form.addRow(tr("&Name", context="option"), self.name_edit)
        self.read_as = QLabel("")
        self.read_as.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        theme.monospace(self.read_as)
        # Translators: how the mod's ModOps name the option.
        shown_row(form, tr("Read as"), self.read_as)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(tr("what a mod manager shows, or the name if blank"))
        self.label_edit.textEdited.connect(self._edited)
        form.addRow(tr("&Label", context="option"), self.label_edit)
        self.kind_combo = QComboBox()
        for kind, shown in _KINDS:
            self.kind_combo.addItem(tr(shown, context="kind of option"), kind)
        self.kind_combo.activated.connect(self._kind_changed)
        form.addRow(tr("&Kind", context="option"), self.kind_combo)

        # The default, whatever the kind: one row, its editor the kind's.
        self.toggle_default = QComboBox()
        # Translators: a toggle's default -- on or off.
        self.toggle_default.addItem(tr("On", context="toggle"), "true")
        self.toggle_default.addItem(tr("Off", context="toggle"), "false")
        self.toggle_default.activated.connect(self._edited)
        self.choice_default = QComboBox()
        self.choice_default.activated.connect(self._edited)
        self.text_default = QLineEdit()
        theme.monospace(self.text_default)
        self.text_default.textEdited.connect(self._edited)
        self.defaults = QStackedWidget()
        for editor in (self.toggle_default, self.choice_default, self.text_default):
            # Named whichever the label is buddy to, for a screen reader
            # that meets one of the others.
            editor.setAccessibleName(tr("Default", context="option"))
            self.defaults.addWidget(editor)
        self.default_caption = QLabel(tr("Defa&ult", context="option"))
        form.addRow(self.default_caption, self.defaults)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._toggle_page())
        self.pages.addWidget(self._choice_page())
        self.pages.addWidget(self._slider_page())
        self.pages.addWidget(QWidget())
        form.addRow(self.pages)
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText(tr("more about it, where the label is not enough"))
        self.description_edit.textEdited.connect(self._edited)
        form.addRow(tr("D&escription", context="option"), self.description_edit)
        # Which of the mod's ModOps read it, to go to.
        # Translators: over a list of the ModOps that read an option.
        uses_caption = QLabel(tr("Read &by"))
        self.uses = QListWidget()
        uses_caption.setBuddy(self.uses)
        self.uses.setMaximumHeight(110)
        theme.monospace(self.uses)
        enter_activates(self.uses)
        self.uses.itemActivated.connect(lambda _item: self.show_use())
        self.uses.currentRowChanged.connect(lambda _row: self._sync_use_button())
        form.addRow(uses_caption, self.uses)
        self.uses_note = QLabel("")
        self.uses_note.setWordWrap(True)
        self.uses_note.setObjectName("StepHint")
        form.addRow(self.uses_note)
        use_row = QHBoxLayout()
        use_row.addStretch(1)
        self.use_button = QPushButton(tr("Show in &Mod file"))
        self.use_button.setToolTip(tr("Keeps what is here, as OK does, and goes to it."))
        self.use_button.clicked.connect(self.show_use)
        use_row.addWidget(self.use_button)
        form.addRow(use_row)
        body.addWidget(self.details, 3)
        outer.addLayout(body, 1)

        footer, self.problem, self.buttons = problem_footer(self, self._accept)
        if self.after_apply:
            # Translators: the Cancel button of the Options dialog, when an
            # Apply in an XML pane opened it. "Apply" is that button's name.
            self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
                tr("Undo the Appl&y"))
        outer.addLayout(footer)

    def _toggle_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        self.yes_edit = QLineEdit()
        self.no_edit = QLineEdit()
        for edit in (self.yes_edit, self.no_edit):
            edit.setPlaceholderText(tr("blank for the mod manager's own word"))
            edit.textEdited.connect(self._edited)
        form.addRow(tr("S&hown when on"), self.yes_edit)
        form.addRow(tr("Shown when o&ff"), self.no_edit)
        return page

    def _choice_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        # Translators: the values a choice offers, and what each is shown as.
        caption = QLabel(tr("&Choices"))
        column.addWidget(caption)
        self.choices = QTableWidget(0, 2)
        caption.setBuddy(self.choices)
        # Tab leaves it; the arrow keys move in it.
        self.choices.setTabKeyNavigation(False)
        single_line(self.choices)
        self.choices.setHorizontalHeaderLabels([
            # Translators: what the ModOps read for a choice.
            tr("Value", context="option"),
            # Translators: what a mod manager shows for a choice.
            tr("Shown as", context="option"),
        ])
        self.choices.verticalHeader().setVisible(False)
        self.choices.horizontalHeader().setStretchLastSection(True)
        self.choices.itemChanged.connect(self._choices_edited)
        column.addWidget(self.choices, 1)
        row = QHBoxLayout()
        self.add_choice = QPushButton(tr("Add a cho&ice"))
        self.add_choice.clicked.connect(self._add_choice)
        self.remove_choice = QPushButton(tr("Remo&ve the choice"))
        self.remove_choice.clicked.connect(self._remove_choice)
        row.addWidget(self.add_choice)
        row.addWidget(self.remove_choice)
        row.addStretch(1)
        column.addLayout(row)
        return page

    def _slider_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        self.low_edit, self.high_edit, self.step_edit = (QLineEdit() for _ in range(3))
        for edit in (self.low_edit, self.high_edit, self.step_edit):
            theme.monospace(edit)
            edit.textEdited.connect(self._edited)
        form.addRow(tr("Lowe&st", context="slider"), self.low_edit)
        form.addRow(tr("Hi&ghest", context="slider"), self.high_edit)
        form.addRow(tr("S&tep", context="slider"), self.step_edit)
        return page

    # -- showing ----------------------------------------------------------
    def _fill_list(self, row: int) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for item in self.items:
            self.list.addItem(QListWidgetItem(self._list_text(item)))
        self.list.blockSignals(False)
        if self.items:
            self.list.setCurrentRow(max(0, min(row, len(self.items) - 1)))
        self._show_current()

    def _list_text(self, item) -> str:
        name = item["name"] or tr("(no name)")
        # Translators: an option the dialog proposes, not yet kept.
        return tr("%s (new)", name) if item["proposed"] else name

    def current(self):
        row = self.list.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    def _show_current(self, *_args) -> None:
        item = self.current()
        self.details.setEnabled(item is not None)
        self.remove_button.setEnabled(item is not None)
        row = self.list.currentRow()
        self.up_button.setEnabled(item is not None and row > 0)
        self.down_button.setEnabled(item is not None and row < len(self.items) - 1)
        self._showing = True
        try:
            item = item or _item(settings.Option(name=""))
            kind = item["kind"]
            self.name_edit.setText(item["name"])
            self.label_edit.setText(item["label"] if item["label"] != item["name"] else "")
            self.description_edit.setText(item["description"])
            while self.kind_combo.count() > len(_KINDS):
                self.kind_combo.removeItem(self.kind_combo.count() - 1)
            position = self.kind_combo.findData(kind)
            if position < 0:
                self.kind_combo.addItem(kind, kind)  # i18n: keep - the mod's own word
                position = self.kind_combo.count() - 1
            self.kind_combo.setCurrentIndex(position)

            toggle = kind == settings.TOGGLE
            self.toggle_default.setCurrentIndex(0 if settings.is_on(item["default"]) else 1)
            labels = [*item["labels"], "", ""]
            self.yes_edit.setText(labels[0] if toggle else "")
            self.no_edit.setText(labels[1] if toggle else "")
            self.choices.setRowCount(0)
            if kind == settings.ENUM:
                for at, value in enumerate(item["values"]):
                    self._put_choice(value, item["labels"][at] if at < len(item["labels"]) else "")
            self._fill_default_choices(item["default"])
            values = [*item["values"], "", "", ""]
            slider = kind == settings.SLIDER
            self.low_edit.setText(values[0] if slider else "")
            self.high_edit.setText(values[1] if slider else "")
            self.step_edit.setText(values[2] if slider else "")
            self.text_default.setText(item["default"])
            self.pages.setCurrentIndex(_PAGES.get(kind, 3))
            self.defaults.setCurrentIndex(_DEFAULTS.get(kind, 2))
            self.default_caption.setBuddy(self.defaults.currentWidget())
        finally:
            self._showing = False
        self._refresh()

    def _put_choice(self, value: str, shown: str) -> None:
        row = self.choices.rowCount()
        self.choices.insertRow(row)
        self.choices.setItem(row, 0, QTableWidgetItem(value))
        self.choices.setItem(row, 1, QTableWidgetItem(shown))

    def _choice_rows(self):
        rows = []
        for row in range(self.choices.rowCount()):
            cells = [self.choices.item(row, column) for column in (0, 1)]
            rows.append(tuple(cell.text().strip() if cell is not None else "" for cell in cells))
        return rows

    def _fill_default_choices(self, default: str) -> None:
        self.choice_default.clear()
        for value, _shown in self._choice_rows():
            if value:
                self.choice_default.addItem(value, value)
        position = self.choice_default.findData(default)
        self.choice_default.setCurrentIndex(position if position >= 0 else 0)

    def _refresh(self) -> None:
        item = self.current()
        name = item["name"] if item is not None else ""
        # In full: the bare name is the one in the field above. In full it
        # is how another mod, or a patch of this one, names it.
        self.read_as.setText("$%s.%s" % (self.mod_id or "mod-id", name) if name else "")
        self.read_as.setToolTip(self.read_as.text())
        proposed = [item["name"] for item in self.items if item["proposed"]]
        if proposed:
            said = trn(
                "Your ModOps read %(names)s, which nothing declared. It is in the list "
                "as a new toggle or slider. Check its kind and default, or remove it. "
                "OK keeps it.",
                "Your ModOps read %(names)s, which nothing declared. They are in the "
                "list as new toggles or sliders. Check each one's kind and default, or "
                "remove it. OK keeps them.",
                len(proposed),
                names=tr(", ", context="between items of a list").join(
                    "$" + name for name in proposed),
            )
            if self.after_apply:
                # Translators: follows the sentences above when an Apply in
                # an XML pane opened the dialog. "Undo the Apply" is the
                # Cancel button's name then.
                said += " " + tr("Undo the Apply takes back what you applied.")
            self.new_note.setText(said)
        self.new_note.setVisible(bool(proposed))
        missing = self.missing_names()
        if missing:
            self.missing.setText(tr(
                "This mod's ModOps read %s, which nothing declares. The game "
                "reads an option nobody declares as off.",
                tr(", ", context="between items of a list").join("$" + name for name in missing),
            ))
        self.missing.setVisible(bool(missing))
        self.declare_button.setVisible(bool(missing))
        why = declaring.problems(self.options())
        self.problem.setText("\n".join(why))
        self.problem.setVisible(bool(why))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not why)
        self._show_uses(item)

    def _show_uses(self, item) -> None:
        """The ModOps that read the option in front, by the name they read
        it by: the one it was declared as."""
        was = (item["name"] if item["was"] is None else item["was"]) if item is not None else ""
        found = declaring.uses(self.loadout, self.origin, was) if was else []
        if [self.uses.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.uses.count())] \
                != [(use.kind, use.which) for use in found]:
            self.uses.clear()
            for use in found:
                # As the Mod changes panel says it: each option by its label.
                said = declaring.with_labels(self.loadout, self.origin, tr_stored(use.said))
                row = QListWidgetItem(said)
                row.setToolTip(said)
                row.setData(Qt.ItemDataRole.UserRole, (use.kind, use.which))
                self.uses.addItem(row)
        if item is not None and found and item["name"] != was:
            self.uses_note.setText(trn(
                "Renamed here: %(n)s ModOp still reads $%(was)s. Change it there too.",
                "Renamed here: %(n)s ModOps still read $%(was)s. Change them there too.",
                len(found), was=was,
            ))
        elif item is not None and not found:
            # Translators: an option no ModOp of the mod reads yet.
            self.uses_note.setText(tr("No ModOp reads it yet."))
        else:
            self.uses_note.setText("")
        self.uses_note.setVisible(bool(self.uses_note.text()))
        self._sync_use_button()

    def _sync_use_button(self) -> None:
        self.use_button.setEnabled(
            self.uses.currentItem() is not None and self.show_in_file is not None
            and not declaring.problems(self.options())
        )

    def show_use(self) -> None:
        """Keep what is here, as OK does, and go to the use picked."""
        picked = self.uses.currentItem()
        if picked is None or self.show_in_file is None or declaring.problems(self.options()):
            return
        self.wanted_use = picked.data(Qt.ItemDataRole.UserRole)
        self._accept()

    # -- state ------------------------------------------------------------
    def options(self):
        return [settings.Option(**{key: value for key, value in item.items() if key not in _OWN})
                for item in self.items]

    def block(self) -> dict:
        return settings.to_block(self.options(), self.original)

    def missing_names(self):
        declared = {item["name"] for item in self.items}
        return [name for name in self.used if name not in declared]

    def _edited(self, *_args) -> None:
        if self._showing:
            return
        item = self.current()
        if item is None:
            return
        item["name"] = self.name_edit.text().strip()
        item["label"] = self.label_edit.text().strip() or item["name"]
        item["description"] = self.description_edit.text().strip()
        kind = item["kind"]
        if kind == settings.TOGGLE:
            item["default"] = str(self.toggle_default.currentData())
            labels = (self.yes_edit.text().strip(), self.no_edit.text().strip())
            item["labels"] = labels if any(labels) else ()
            item["values"] = ()
        elif kind == settings.ENUM:
            rows = self._choice_rows()
            item["values"] = tuple(value for value, _shown in rows)
            shown = tuple(label for _value, label in rows)
            item["labels"] = shown if any(shown) else ()
            item["default"] = str(self.choice_default.currentData() or "")
        elif kind == settings.SLIDER:
            item["values"] = tuple(
                edit.text().strip() for edit in (self.low_edit, self.high_edit, self.step_edit)
            )
            item["default"] = self.text_default.text().strip()
        else:
            item["default"] = self.text_default.text().strip()
        list_item = self.list.currentItem()
        if list_item is not None:
            list_item.setText(self._list_text(item))
        self._refresh()

    def _choices_edited(self, *_args) -> None:
        """A choice typed in, added or taken out: the default is picked from
        the choices there are now, staying where it was if it still can."""
        if self._showing:
            return
        self._showing = True
        try:
            self._fill_default_choices(self.choice_default.currentData() or "")
        finally:
            self._showing = False
        self._edited()

    def _kind_changed(self, *_args) -> None:
        item = self.current()
        if item is None:
            return
        kind = self.kind_combo.currentData()
        if kind == item["kind"]:
            return
        item.update(settings.as_fields(settings.started_as(kind, item["name"], item["label"])))
        self._show_current()

    # -- acts -------------------------------------------------------------
    def add_option(self, name: str = "", kind: str = settings.TOGGLE) -> None:
        taken = {item["name"] for item in self.items}
        if not name:
            name = next(
                candidate for candidate in ("option%d" % n for n in range(1, len(taken) + 2))
                if candidate not in taken
            )
        self.items.append(_item(settings.started_as(kind, name)))
        self._fill_list(len(self.items) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def declare_missing(self) -> None:
        for name in self.missing_names():
            self.items.append(_item(settings.started_as(settings.TOGGLE, name)))
        self._fill_list(len(self.items) - 1)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            del self.items[row]
            self._fill_list(row)

    def _move(self, by: int) -> None:
        row = self.list.currentRow()
        to = row + by
        if 0 <= row < len(self.items) and 0 <= to < len(self.items):
            self.items[row], self.items[to] = self.items[to], self.items[row]
            self._fill_list(to)

    def _add_choice(self) -> None:
        values = {value for value, _shown in self._choice_rows()}
        value = next(str(n) for n in range(1, len(values) + 2) if str(n) not in values)
        self._showing = True
        try:
            self._put_choice(value, "")
        finally:
            self._showing = False
        self._choices_edited()

    def _remove_choice(self) -> None:
        row = self.choices.currentRow()
        if row >= 0:
            self.choices.removeRow(row)
            self._choices_edited()

    def _accept(self) -> None:
        if declaring.problems(self.options()):
            return
        settings.set_block(self.loadout, self.origin, self.block())
        self.accept()
