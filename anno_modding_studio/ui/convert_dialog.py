"""The survey: every installed mod, where it lives, and what converting does.

Read-only until the button at the bottom.  One row per mod -- per ModID,
however many copies of it are installed -- grouped by where the copy the game
loads lives; each says what will happen to it and to its other copies.  All
ticked; unticking leaves a mod where it is.  Beneath, the profiles other tools
keep, each offered as a loadout.  See :mod:`core.convert`.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core import convert, games
from ..core.i18n import join_list, number, size_text, tr, tr_noop, trn
from . import theme
from .help_tip import HelpTip
from .widgets import fit_columns, tab_as_laid_out, wrappable_path

COLUMNS = (
    tr_noop("Mod", context="column"), "ModID", tr_noop("Version", context="column"),
    tr_noop("On", context="column"), tr_noop("On mod.io", context="column"),
    tr_noop("Settings", context="column"), tr_noop("What happens", context="column"),
)
ROW_ROLE = Qt.ItemDataRole.UserRole


class ConvertDialog(QDialog):
    """``only`` narrows it to rows living in those places -- the fourth card's
    checklist opens it on one row of its own."""

    def __init__(self, survey: convert.Survey, parent=None, *, managers=(), found_profiles=(),
                 space: int = 0, free: int = 0, library_dir="", only: Optional[Iterable[str]] = None,
                 full_size: int = 0, full_free: int = 0, full_folders=(), full_default: bool = False):
        super().__init__(parent)
        self.survey = survey
        self.only = set(only) if only else None
        self.setWindowTitle(tr("Convert to the library"))
        self.resize(1100, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title says what this does. That it deletes no mod, and how to
        # leave one out, stay in view. What becomes of each kind is behind the ?.
        if games.get(survey.game).in_game_browser:
            # Translators: "Play" is the Studio's name for its launch tab, and
            # "the fourth card" one of the steps on it.
            said = tr(
                "Every mod the game loads becomes a link into the Studio's "
                "library, so the Studio sets every switch. Your own folders "
                "move into the library, with a link left where each was. The "
                "game's mod browser's mods are copied, and its copies stay "
                "until you unsubscribe in the game (Unsubscribe in game… on "
                "the fourth card). A copy the game does not load is set "
                "aside. What the game loads now is recorded first and checked "
                "against the next Play's loader log."
            )
        else:
            # Translators: "Play" is the Studio's name for its launch tab.
            said = tr(
                "Every mod the game loads becomes a link into the Studio's "
                "library, so the Studio sets every switch. Your own folders "
                "move into the library, with a link left where each was. "
                "Ubisoft's own mods in .ubi stay where they are. A copy the "
                "game does not load is set aside. What the game loads now is "
                "recorded first and checked against the next Play's loader log."
            )
        head = QHBoxLayout()
        hint = QLabel(tr("No mod is deleted. Untick a mod to leave it where it is."))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        self.help = HelpTip(said, self.windowTitle())
        head.addWidget(self.help)
        outer.addLayout(head)

        # Another tool being here is a fact to work with, not a warning.
        for text in managers:
            said = QLabel(text)
            said.setObjectName("StepHint")
            said.setWordWrap(True)
            outer.addWidget(said)
        if space:
            enough = not free or free > space * 1.1
            said = QLabel(
                tr("%(size)s to copy into the library at %(where)s. %(free)s "
                   "is free there.",
                   size=size_text(space), where=wrappable_path(library_dir),
                   free=size_text(free))
                if free else
                tr("%(size)s to copy into the library at %(where)s. The free "
                   "space there is unknown.",
                   size=size_text(space), where=wrappable_path(library_dir))
            )
            said.setObjectName("StepHint" if enough else "")
            if not enough:
                theme.tint(said, "warn")
            said.setWordWrap(True)
            outer.addWidget(said)
        if survey.inert:
            said = QLabel(tr(
                "Not mods, left where they are: %s. The game lists them and "
                "loads nothing from them.", ", ".join(survey.inert)
            ))
            said.setObjectName("StepHint")
            said.setWordWrap(True)
            outer.addWidget(said)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels([
            label if label == "ModID" else tr(label, context="column") for label in COLUMNS
        ])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        # The mod's name and what becomes of it share what is left; a ModID
        # can be a sentence, so it gets a fixed start and a drag handle.
        header = self.tree.header()
        header.setStretchLastSection(False)
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 170)
        fit_columns(self.tree)
        self.tree.itemChanged.connect(self._refresh_button)
        self.tree.setAccessibleName(tr("Mods to convert"))
        outer.addWidget(self.tree, 1)
        self._fill()

        self.profile_list = QListWidget()
        # Translators: a loadout is the Studio's name for a set of mods kept
        # together; a profile is one arrangement of which of them are on.
        box = QGroupBox(tr("Your profiles, as profiles of this loadout"))
        box_layout = QVBoxLayout(box)
        profiles_head = QHBoxLayout()
        said = QLabel(tr("Each profile itself is left as it is."))
        said.setObjectName("StepHint")
        said.setWordWrap(True)
        profiles_head.addWidget(said, 1)
        profiles_head.addWidget(HelpTip(tr(
            "Each becomes a profile of this loadout: the same mods, switched "
            "as the profile sets them. All of them share one mod list, so a "
            "mod that updates is updated in every profile."
        ), box.title()))
        box_layout.addLayout(profiles_head)
        self.profile_list.setAccessibleName(box.title())
        box_layout.addWidget(self.profile_list)
        for found in found_profiles:
            if found.here:
                # A profile of that name is here: from a conversion before
                # this one, most likely. Ticked, it would come in again.
                item = QListWidgetItem(trn(
                    "%(profile)s (%(source)s, %(n)s mod on), already a profile of this loadout",
                    "%(profile)s (%(source)s, %(n)s mods on), already a profile of this loadout",
                    len(found.on), profile=found.name, source=found.source))
            else:
                item = QListWidgetItem(trn(
                    "%(profile)s (%(source)s, %(n)s mod on)",
                    "%(profile)s (%(source)s, %(n)s mods on)",
                    len(found.on), profile=found.name, source=found.source))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if found.here else Qt.CheckState.Checked)
            item.setData(ROW_ROLE, found)
            self.profile_list.addItem(item)
        rows = min(4, max(1, len(found_profiles)))
        self.profile_list.setMaximumHeight(self.profile_list.sizeHintForRow(0) * rows + 6 if found_profiles else 30)
        box.setVisible(bool(found_profiles) and self.only is None)
        outer.addWidget(box)

        # Insurance for the first real run: the whole of what moves, copied
        # first and kept for good. Undoing does not need it; it is for the
        # case where undoing is wrong.
        self.full_box = QCheckBox(tr("Keep a &full copy"))
        self.full_note = QLabel("")
        self.full_note.setObjectName("StepHint")
        full_help = HelpTip(tr(
            "Zips everything that moves into the Studio's backups before "
            "anything moves, and keeps it for good. Undo the conversion "
            "(Game \u25b8 Library) moves every folder back without it. The zip is "
            "for the case where that goes wrong, to extract by hand. Models "
            "and textures compress well: about 1 GB for 2.4 GB of Anno 1800 "
            "mods."
        ), self.full_box.text())
        if full_size:
            fits = not full_free or full_free > full_size * 1.1
            names = join_list(str(folder) for folder in full_folders) or tr("your mods folder")
            size = size_text(full_size)
            free_text = (size_text(full_free) if full_free
                         else tr("unknown", context="free space"))
            if fits:
                self.full_note.setText(tr(
                    "%(folders)s, %(size)s, zipped into the Studio's backups "
                    "(%(free)s free) and kept for good",
                    folders=names, size=size, free=free_text))
            else:
                self.full_note.setText(tr(
                    "%(folders)s, %(size)s, zipped into the Studio's backups "
                    "(%(free)s free): not enough room",
                    folders=names, size=size, free=free_text))
                theme.tint(self.full_note, "warn")
            self.full_box.setChecked(bool(full_default) and fits)
            self.full_box.setEnabled(fits)
        full_row = QHBoxLayout()
        full_row.addWidget(self.full_box)
        full_row.addWidget(full_help)
        full_row.addWidget(self.full_note, 1, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(full_row)
        # Once they are in the dialog: a widget with no parent shows as a window.
        for widget in (self.full_box, self.full_note, full_help):
            widget.setVisible(bool(full_size))

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = self.buttons.addButton(tr("&Convert"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setObjectName("Primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)
        self._refresh_button()

    def _fill(self) -> None:
        self.tree.blockSignals(True)
        for where, label in convert.WHERE.items():
            if self.only is not None and where not in self.only:
                continue
            rows = self.survey.of(where)
            if not rows:
                continue
            group = QTreeWidgetItem([tr("%(place)s (%(count)s)", place=tr(label),
                                        count=number(len(rows)))])
            group.setFirstColumnSpanned(True)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            for row in rows:
                item = QTreeWidgetItem([
                    row.name, row.mod_id, row.version,
                    tr("on", context="switched") if row.on else tr("off", context="switched"),
                    tr("yes", context="on mod.io") if row.modio_id else "",
                    row.settings, row.describe(),
                ])
                item.setData(0, ROW_ROLE, row.key)
                if row.to_do:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Checked)
                else:
                    item.setForeground(6, self.palette().placeholderText())
                tip = "\n".join(
                    "%s — %s" % (copy.folder, tr(convert.FATES[copy.fate])
                                 if convert.FATES.get(copy.fate)
                                 else tr("stays", context="a copy of a mod"))
                    for copy in row.copies
                )
                item.setToolTip(0, tip)
                item.setToolTip(6, tip)
                group.addChild(item)
            group.setExpanded(True)
        self.tree.blockSignals(False)

    def _items(self) -> List[QTreeWidgetItem]:
        found = []
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            found.extend(group.child(i) for i in range(group.childCount()))
        return found

    def chosen_rows(self) -> List[convert.Row]:
        by_key = {row.key: row for row in self.survey.rows}
        return [
            by_key[item.data(0, ROW_ROLE)]
            for item in self._items()
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.checkState(0) == Qt.CheckState.Checked
        ]

    def keep_full_copy(self) -> bool:
        return not self.full_box.isHidden() and self.full_box.isEnabled() and self.full_box.isChecked()

    def chosen_profiles(self) -> list:
        if self.only is not None:
            return []
        return [
            self.profile_list.item(i).data(ROW_ROLE)
            for i in range(self.profile_list.count())
            if self.profile_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _refresh_button(self, *_args) -> None:
        count = len(self.chosen_rows())
        self.go.setText(trn("&Convert %(n)s mod", "&Convert %(n)s mods", count) if count
                        else tr("&Convert"))
        self.go.setEnabled(bool(count) or bool(self.chosen_profiles()))
