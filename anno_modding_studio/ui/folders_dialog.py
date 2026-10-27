"""Help > Studio folders: every folder the Studio uses, in one list.

The Studio's own folders are all called AnnoModdingStudio, under Documents
and under the local app data, and each was shown only where it was used:
the library on the Start tab, the export folder on the Export tab.  That
said where a thing was going, and nothing said afterwards where it went.
This lists each place with which to back up and which can be deleted, a
``?`` saying what it holds, and opens it.  What is listed is
:meth:`ui.where.Where.places`.  Where another screen explains the same
place, its ``?`` and this one's are the same text (:mod:`ui.help_tip`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .. import __version__
from ..core import games, places
from ..core.i18n import tr, tr_noop
from . import where
from .help_tip import (
    BACKUPS_HELP,
    EXPORT_HELP,
    EXTRACTED_HELP,
    LIBRARY_HELP,
    HelpTip,
)
from .widgets import (
    ElideItemDelegate,
    enter_activates,
    flash_button_feedback,
    heading_row,
    show_in_folder,
    tab_as_laid_out,
    wrappable_path,
)

PLACE_ROLE = Qt.ItemDataRole.UserRole

# The headings, and the places under each, in the order they are listed.
GROUPS = (
    (tr_noop("Your work"), (where.LOADOUTS, where.PROJECTS, where.EXPORTS, where.BACKUPS)),
    (tr_noop("Mods"), (where.LIBRARY, where.MODS)),
    (tr_noop("Made again when needed"), (where.EXTRACTED, where.CACHE)),
    (tr_noop("The Studio"), (where.SETTINGS, where.PROGRAM, where.SOURCE)),
)

# What each place is, where the words need nothing filled in.
WHAT = {
    where.LOADOUTS: tr_noop("Loadouts"),
    where.PROJECTS: tr_noop("Projects"),
    where.EXPORTS: tr_noop("Exported mods"),
    where.BACKUPS: tr_noop("Backups"),
    # Translators: the folder the Studio keeps installed mods in, as the
    # Start tab names it.
    where.LIBRARY: tr_noop("Mods library"),
    where.EXTRACTED: tr_noop("Extracted game files"),
    where.CACHE: tr_noop("Index and cache"),
    where.SETTINGS: tr_noop("Settings"),
    # Translators: the folder the Studio is started from.
    where.PROGRAM: tr_noop("Program folder"),
    where.SOURCE: tr_noop("Program folder"),
}

# What is in each, behind its ?: the place's own words where no other screen
# has them, and the other screen's where it does.
HELP = {
    where.LOADOUTS: tr_noop(
        "A loadout is what the Studio opens: the mods you have open, in "
        "load order, which of them are on, what your changes come to and a "
        "history of versions. Each is one .annoload file, saved here unless "
        "you save it somewhere else. File \u25b8 Open loadout\u2026 opens one "
        "from any folder."
    ),
    where.PROJECTS: tr_noop(
        "Each mod of yours is kept in a project: its details, its ModOps, its "
        "builds and its mod.io page. Every loadout that has the mod refers "
        "to its project, so an edit made in any of them goes there. Each is "
        "one .annoproj file, and saving a loadout saves the projects it "
        "changed."
    ),
    where.EXPORTS: EXPORT_HELP,
    where.BACKUPS: BACKUPS_HELP,
    where.LIBRARY: LIBRARY_HELP,
    where.MODS: tr_noop(
        "The folder the game loads mods from. Play switches mods on and off "
        "here, and a mod in the library has a link here in place of its "
        "folder. A game can have more than one mods folder. This is the one "
        "the open loadout plays."
    ),
    where.EXTRACTED: EXTRACTED_HELP,
    where.CACHE: tr_noop(
        "The index of the game files, which opens them in seconds after the "
        "first time. Also the unpacked copies of zipped mods, and the "
        "community GUID ranges as last fetched. Each is built again when it "
        "is missing."
    ),
    where.SETTINGS: tr_noop(
        "Your author name, the appearance, where the library is, the "
        "folders you used last, your recent loadouts and the window's "
        "layout. They are true of this PC only. Your mod.io key is kept "
        "apart, encrypted."
    ),
    where.PROGRAM: tr_noop(
        "The folder the Studio runs from. It holds the program alone. A "
        "new version goes in a folder of its own, and your loadouts, library "
        "and settings stay where they are."
    ),
}
HELP[where.SOURCE] = HELP[where.PROGRAM]

# The columns.
WHAT_COLUMN, HELP_COLUMN, WHERE_COLUMN, NOTE_COLUMN = range(4)


def what(place: where.Place, game: str) -> str:
    """The name of ``place``'s row."""
    if place.key == where.MODS:
        # Translators: %s is a game's name, as in "Mods folder for Anno 1800".
        return tr("Mods folder for %s", games.get(game).label)
    return tr(WHAT[place.key])


def note(place: where.Place) -> str:
    """What to know about ``place``: whether to back it up or it can go,
    and whether somebody chose it."""
    if not place.path.exists():
        return tr("Not made yet")
    if place.key in (where.LOADOUTS, where.PROJECTS, where.BACKUPS):
        return tr("Back this up")
    if place.key == where.LIBRARY:
        # The library is not a cache: a conversion moves your own mod
        # folders into it, so it can hold the only copy of one.
        return tr("Back this up. Chosen in Settings") if place.chosen else tr("Back this up")
    if place.key == where.EXPORTS and place.chosen:
        return tr("Chosen on the Export tab")
    if place.key == where.MODS:
        return tr("What the game loads")
    if place.key in (where.EXTRACTED, where.CACHE):
        return tr("Safe to delete")
    if place.key == where.PROGRAM:
        return tr("Version %s", __version__)
    if place.key == where.SOURCE:
        return tr("Version %s, run from source", __version__)
    return ""


def shown(path: Path) -> str:
    """``path`` from the folder it is known by: Documents, or the local app
    data, which every Studio folder's full spelling starts the same long
    way into.  The part that tells them apart is then the first thing read.
    Whole anywhere else."""
    for root, name in (
        # Translators: the user's Documents folder, at the start of a path.
        (places.documents(), tr("Documents")),
        (places.local_app_data(), os.path.join("AppData", "Local")),
    ):
        try:
            rest = path.relative_to(root)
        except ValueError:
            continue
        return name if str(rest) == "." else os.path.join(name, str(rest))
    return str(path)


def one_folder_line() -> str:
    """What the dialog says over the list where everything is kept in one
    folder; "" where it is not."""
    folder = places.one_folder()
    if folder is None:
        return ""
    if os.environ.get(places.ENV_FOLDER, "").strip():
        return tr("Everything is kept in %s, which ANNO_STUDIO_FOLDER names.",
                  wrappable_path(folder))
    return tr("Everything is kept in %s, beside the Studio.", wrappable_path(folder))


class FoldersDialog(QDialog):
    """``found`` as :meth:`ui.where.Where.places` gives them, for a loadout
    of ``game``."""

    def __init__(self, found: List[where.Place], game: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Studio folders"))
        self.resize(860, 440)
        self.game = game
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        self.one_folder = QLabel(one_folder_line())
        self.one_folder.setWordWrap(True)
        self.one_folder.setVisible(bool(self.one_folder.text()))
        layout.addWidget(self.one_folder)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(tr("Studio folders"))
        self.tree.setHeaderLabels([tr("What"), "", tr("Where"), tr("Note")])
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        # A long path loses its middle, where its start and its own name
        # are what tell it from the others.
        self.tree.setItemDelegateForColumn(
            WHERE_COLUMN, ElideItemDelegate(self.tree, Qt.TextElideMode.ElideMiddle)
        )
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(WHAT_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(HELP_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(WHERE_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(NOTE_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemActivated.connect(lambda *_: self.open_place())
        enter_activates(self.tree)
        layout.addWidget(self.tree, 1)
        # Before the buttons, so Tab reaches each ? in the list before them.
        self.found = list(found)
        self.tips: List[HelpTip] = []
        first = self._fill()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("&Close"))
        self.open_button = buttons.addButton(
            tr("&Open folder"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.open_button.clicked.connect(self.open_place)
        self.copy_button = buttons.addButton(
            tr("Copy &path"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.copy_button.clicked.connect(self.copy_path)
        self.copy_all_button = buttons.addButton(
            tr("Copy &all"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.copy_all_button.setToolTip(tr("Copies every place in the list, with its full path."))
        self.copy_all_button.clicked.connect(self.copy_all)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        layout.addWidget(buttons)

        self.tree.currentItemChanged.connect(lambda *_: self._sync())
        if first is not None:
            self.tree.setCurrentItem(first)
        self._sync()

    def _fill(self):
        """The rows under their headings, each with its ?; the first row."""
        by_key = {place.key: place for place in self.found}
        first = None
        for heading, keys in GROUPS:
            listed = [by_key[key] for key in keys if key in by_key]
            if not listed:
                continue
            top = heading_row(self.tree, tr(heading))
            for place in listed:
                named = what(place, self.game)
                row = QTreeWidgetItem(top, [named, "", shown(place.path), note(place)])
                row.setData(0, PLACE_ROLE, place)
                row.setToolTip(WHERE_COLUMN, str(place.path))
                tip = HelpTip(tr(HELP[place.key]), named)
                self.tree.setItemWidget(row, HELP_COLUMN, tip)
                self.tips.append(tip)
                first = first or row
        self.tree.expandAll()
        if self.tips:
            self.tree.setColumnWidth(HELP_COLUMN, self.tips[0].sizeHint().width() + 8)
        return first

    def current(self):
        """The place on the row the keyboard is on; None on a heading."""
        item = self.tree.currentItem()
        return item.data(0, PLACE_ROLE) if item is not None else None

    def _sync(self) -> None:
        place = self.current()
        self.open_button.setEnabled(place is not None and place.path.exists())
        self.copy_button.setEnabled(place is not None)

    def open_place(self) -> None:
        place = self.current()
        if place is not None and place.path.exists():
            show_in_folder(place.path)

    def copy_path(self) -> None:
        place = self.current()
        if place is None:
            return
        QGuiApplication.clipboard().setText(str(place.path))
        flash_button_feedback(self.copy_button)

    def copy_all(self) -> None:
        """Every place, one to a line with its full path: to paste where
        somebody is helping you find something."""
        order = [key for _heading_text, keys in GROUPS for key in keys]
        listed = sorted(self.found, key=lambda place: order.index(place.key))
        lines = ["%s: %s" % (what(place, self.game), place.path) for place in listed]
        QGuiApplication.clipboard().setText("\n".join(lines) + "\n")
        flash_button_feedback(self.copy_all_button)
