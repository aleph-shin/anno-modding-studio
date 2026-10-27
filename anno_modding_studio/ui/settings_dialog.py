"""What the Studio remembers about you and this machine, in one place.

None of it is about the mod being made.  Who you publish as, the key the
Studio talks to mod.io with, how it looks, which GUID ranges the
community has claimed: these outlive every loadout opened in this window, and
they were scattered -- three of them behind a *File ▸ Settings* submenu that
opened something different for each, and the fourth under *View* because it
happens to change how the window looks.

A submenu of four entries is a settings dialog with the lid off: you have to
open each one to find out what it holds, and nothing ever shows you what the
Studio currently thinks the answers are.  This shows all four at once, with
the current answer beside each.

Eight boxes in one long scroll were the next problem: the mod.io key was
the second box and the loader the seventh, and finding either meant reading
past the others.  They are tabs now, by what the setting is about:
*General* (language, how the window looks, whether a newer Studio is
looked for), *Authoring* (who you publish as,
and the GUID range your new assets draw from), *mod.io* (the account itself:
key and sign-in, which was a dialog of its own behind a button here), *Game
and library* (where installed mods are kept, how the game is started) and
*Advanced* (the loader check).  The loader's two actions went to the Check
menu; this keeps what they last said.

Everything here takes effect as it is set, so the only button is Close.  The one exception is the
author names, which are typed rather than clicked: they land when the field is
left or the dialog closes, because applying half a name as it is typed would
rename the open loadout four times on the way to the real one.
"""

from __future__ import annotations

import contextlib
import html
from typing import Optional

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import games, guidranges, i18n, secrets
from ..core.i18n import number, tr, tr_kw, tr_stored, trn
from . import appearance, language, theme
from .help_tip import ALIASES_HELP, CREATOR_HELP, LIBRARY_HELP, HelpTip
from .modio_account_panel import ModioAccountPanel
from .new_asset_dialog import PERSONAL_RANGE, describe_range, your_range
from .widgets import open_on_disk, rich_label, show_rich, wrappable_path

# Ctrl+, is what a settings dialog answers to nearly everywhere; Qt's own
# Preferences standard key is unbound on Windows, so it is said outright.
SHORTCUT = QKeySequence("Ctrl+,")

# Tabs the dialog is opened on, by their place: General, Authoring,
# mod.io, Game and library, Advanced.
TAB_GENERAL = 0
TAB_MODIO = 2


class SettingsDialog(QDialog):
    """The Studio's own settings, as opposed to a loadout's.

    ``window`` is the main window.  Every setting here is one it owns -- it is
    the thing that knows how to restyle itself, whose loadout has to follow a
    changed creator name, and what to say in the status bar afterwards -- so
    this dialog asks it rather than keeping a second copy of any of it.  Tests
    hand in a stand-in with the same four methods.

    ``store`` is the mod.io credential store, taken as an argument so a test
    never has to touch the real one.  ``tab`` is the one it opens on.
    """

    def __init__(self, window, parent=None, store: Optional[secrets.Store] = None,
                 tab: int = TAB_GENERAL):
        super().__init__(parent if parent is not None else window)
        self.window = window
        self.store = store if store is not None else secrets.Store()
        # Filled by the GUID ranges box, which the creator name's box, built
        # first, refreshes as a name lands.
        self.range_notes: dict = {}
        self.setWindowTitle(tr("Settings"))
        self.setMinimumWidth(560)
        self.resize(640, 600)
        self._build_ui()
        self.pages.setCurrentIndex(tab)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # No mnemonics on the tabs: the letters are the dialog's, whichever
        # tab a control is on, and its buttons and labels have them all.
        # Ctrl+Tab goes from one tab to the next.
        self.pages = QTabWidget()
        # Translators: the tabs of the Settings dialog: the Studio itself,
        # making mods (who you publish as, GUIDs), your mod.io account, the
        # game and the library, and the rest.
        self.pages.addTab(self._page(self._language_box(), self._appearance_box(), self._updates_box()),
                          tr("General"))
        self.pages.addTab(self._page(self._creator_box(), self._ranges_box()), tr("Authoring"))
        self.account = ModioAccountPanel(self.store, self)
        self.pages.addTab(self._page(self.account), "mod.io")  # i18n: keep - a name
        self.pages.addTab(self._page(self._library_box(), self._start_box()), tr("Game and library"))
        self.pages.addTab(self._page(self._loader_box()), tr("Advanced"))
        outer.addWidget(self.pages, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        outer.addWidget(buttons)
        # Every setting takes effect as it is made, so Enter in a field has
        # no button to press. Left to the dialog it pressed the first that
        # would take it, and a name typed and entered opened the mod.io
        # account dialog.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    @staticmethod
    def _page(*boxes) -> QScrollArea:
        """A tab of ``boxes``, one under another.

        Scrolled, so each box keeps the height its wrapped text needs: on a
        laptop screen some are taller than the room, and squeezed to fit, Qt
        cut the last line off every paragraph rather than scroll.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 12, 0, 0)
        column.setSpacing(12)
        for box in boxes:
            column.addWidget(box)
        column.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # -- who you publish as -------------------------------------------------
    def _creator_box(self) -> QGroupBox:
        box = QGroupBox(tr("Who you publish as"))
        layout = QVBoxLayout(box)

        self.creator_edit = QLineEdit(self.window.preferences.creator_name())
        self.creator_edit.setPlaceholderText(tr("Not set"))
        # Not textChanged: a name lands when you have finished typing it.
        self.creator_edit.editingFinished.connect(self._apply_creator)
        name = QLabel(tr("Author &name"))
        name.setBuddy(self.creator_edit)
        row = QHBoxLayout()
        row.addWidget(name)
        row.addWidget(self.creator_edit, 1)
        row.addWidget(HelpTip(tr_kw(CREATOR_HELP), name.text()))
        layout.addLayout(row)

        self.aliases_edit = QLineEdit(", ".join(self.window.preferences.aliases()))
        self.aliases_edit.setPlaceholderText(tr("No other names"))
        self.aliases_edit.editingFinished.connect(self._apply_aliases)
        others = QLabel(tr("Also pu&blished as"))
        others.setBuddy(self.aliases_edit)
        row = QHBoxLayout()
        row.addWidget(others)
        row.addWidget(self.aliases_edit, 1)
        row.addWidget(HelpTip(tr(ALIASES_HELP), others.text()))
        layout.addLayout(row)

        self.creator_note = QLabel("")
        self.creator_note.setObjectName("StepHint")
        self.creator_note.setWordWrap(True)
        layout.addWidget(self.creator_note)
        self._refresh_creator_note()
        return box

    def _refresh_creator_note(self) -> None:
        known = self.window.preferences.your_names()
        self.creator_note.setText(
            tr("Mods by %s are yours, and the Studio edits them directly.",
               i18n.join_list(known, conjunction="or"))
            if known
            else tr("No name set. Every mod you load counts as someone "
                    "else's and gets a patch.")
        )

    def _apply_creator(self) -> None:
        self.window.preferences.set_creator_name(self.creator_edit.text())
        # The name given may have been one of the others, which it no longer is.
        self._show_aliases()
        self._refresh_creator_note()
        # Which range the name finds is the first choice's answer.
        self._refresh_range_notes()

    def _apply_aliases(self) -> None:
        self.window.preferences.set_aliases(self.aliases_edit.text().split(","))
        self._show_aliases()
        self._refresh_creator_note()
        # A name the author name is not listed under may be.
        self._refresh_range_notes()

    def _show_aliases(self) -> None:
        """The other names as kept: tidied, and none of them the author name."""
        with QSignalBlocker(self.aliases_edit):
            self.aliases_edit.setText(", ".join(self.window.preferences.aliases()))

    # -- which language the Studio speaks -----------------------------------
    def _language_box(self) -> QGroupBox:
        box = QGroupBox(tr("Language"))
        layout = QVBoxLayout(box)

        self.language_combo = QComboBox()
        # Each language is named in itself, the way anyone looking for their
        # own finds it -- except "automatic", which has to be read by whoever
        # is here now.
        automatic = dict(i18n.available()).get(language.resolve(""), i18n.SOURCE_LABEL)
        self.language_combo.addItem(tr("Match Windows (%s)", automatic), "")
        for code, label in i18n.available():
            self.language_combo.addItem(label, code)  # i18n: keep - named in itself
        current = self.language_combo.findData(self.window.preferences.language_choice())
        self.language_combo.setCurrentIndex(max(current, 0))
        self.language_combo.currentIndexChanged.connect(self._apply_language)
        self.language_combo.setAccessibleName(box.title())
        row = QHBoxLayout()
        row.addWidget(self.language_combo, 1)
        row.addWidget(HelpTip(tr(
            "Sets the language of the Studio's own text. Asset names and game "
            "text come from the game files, in the language you chose when "
            "you loaded them. Translations other than English are made by "
            "machine. Please report anything that reads oddly."
        ), box.title()))
        layout.addLayout(row)

        self.language_note = QLabel("")
        self.language_note.setObjectName("StepHint")
        self.language_note.setWordWrap(True)
        layout.addWidget(self.language_note)
        return box

    def _apply_language(self) -> None:
        code = str(self.language_combo.currentData() or "")
        self.window.preferences.set_language_choice(code)
        # Said in the language being left, which is the one being read.
        self.language_note.setText(
            "" if language.resolve(code) == i18n.language()
            else tr("Takes effect the next time the Studio starts.")
        )

    # -- how the Studio looks ------------------------------------------------
    def _appearance_box(self) -> QGroupBox:
        box = QGroupBox(tr("Appearance"))
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        now = self.window.preferences.appearance()

        def row(number, label, combo, help_text=""):
            name = QLabel(label)
            name.setBuddy(combo)
            grid.addWidget(name, number, 0)
            grid.addWidget(combo, number, 1)
            if help_text:
                grid.addWidget(HelpTip(help_text, label), number, 2)

        self.scheme_combo = QComboBox()
        self.scheme_combo.addItem(tr("Match Windows"), appearance.MATCH_WINDOWS)
        for key, scheme in theme.SCHEMES.items():
            self.scheme_combo.addItem(tr(scheme.label, context="colour scheme"), key)
        self.scheme_combo.setCurrentIndex(max(self.scheme_combo.findData(now["scheme"]), 0))
        self.scheme_combo.currentIndexChanged.connect(self._apply_appearance)
        row(0, tr("&Colour scheme"), self.scheme_combo, tr(
            "Match Windows uses Marble when Windows is in light mode and "
            "Granite when it is in dark mode. A Windows contrast theme is "
            "always drawn in its own colours, whichever scheme is picked here."
        ))
        self.contrast_note = QLabel(tr(
            "A Windows contrast theme is on, so the Studio uses its colours."
        ))
        self.contrast_note.setObjectName("StepHint")
        self.contrast_note.setWordWrap(True)
        self.contrast_note.setVisible(theme.scheme().key not in theme.SCHEMES)
        grid.addWidget(self.contrast_note, 1, 1, 1, 2)

        self.outlines_combo = QComboBox()
        for key, label in theme.OUTLINE_LABELS:
            self.outlines_combo.addItem(tr(label, context="outlines"), key)
        self.outlines_combo.setCurrentIndex(max(self.outlines_combo.findData(now["outlines"]), 0))
        self.outlines_combo.currentIndexChanged.connect(self._apply_appearance)
        row(2, tr("&Outlines"), self.outlines_combo, tr(
            "Boxed draws a border around groups of settings, lists and "
            "headers. Flat separates them with a heading and a line."
        ))

        self.density_combo = QComboBox()
        for key, label in theme.DENSITY_LABELS:
            self.density_combo.addItem(tr(label, context="density"), key)
        self.density_combo.setCurrentIndex(max(self.density_combo.findData(now["density"]), 0))
        self.density_combo.currentIndexChanged.connect(self._apply_appearance)
        row(3, tr("&Density"), self.density_combo, tr(
            "Compact tightens padding and row height, which suits a long list "
            "of mods. Text stays the same size."
        ))

        self.size_combo = QComboBox()
        for percent in appearance.SIZES:
            # Translators: an interface size, such as 125%.
            self.size_combo.addItem(tr("%(percent)d%%", percent=percent), percent)
        self.size_combo.setCurrentIndex(max(self.size_combo.findData(now["size"]), 0))
        self.size_combo.currentIndexChanged.connect(self._apply_interface_size)
        row(4, tr("&Interface size"), self.size_combo, tr(
            "Scales everything together: text, spacing, icons and the "
            "reference graph. 100% follows the scaling set in Windows."
        ))
        self.size_note = QLabel(tr("Takes effect the next time the Studio starts."))
        self.size_note.setObjectName("StepHint")
        self.size_note.setWordWrap(True)
        grid.addWidget(self.size_note, 5, 1, 1, 2)
        return box

    def _apply_appearance(self) -> None:
        self.window.preferences.set_appearance(
            scheme=str(self.scheme_combo.currentData() or ""),
            outlines=str(self.outlines_combo.currentData() or ""),
            density=str(self.density_combo.currentData() or ""),
        )
        self.contrast_note.setVisible(theme.scheme().key not in theme.SCHEMES)

    def _apply_interface_size(self) -> None:
        percent = self.size_combo.currentData()
        if percent:
            self.window.preferences.set_interface_size(int(percent))

    # -- whether a newer Studio is out ------------------------------------------
    def _updates_box(self) -> QGroupBox:
        box = QGroupBox(tr("Updates"))
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        # What is checked and how often: a shorter label drops one of them.
        self.release_check = QCheckBox(tr("Check fo&r a newer Studio once a day"))  # design: says what is checked and how often
        self.release_check.setChecked(self.window.preferences.checks_for_studio())
        self.release_check.toggled.connect(self.window.preferences.set_checks_for_studio)
        row.addWidget(self.release_check, 1)
        row.addWidget(HelpTip(tr(
            "A few seconds after the window opens, at most once a day, the "
            "Studio checks GitHub for its newest release. Only that request "
            "is sent, to GitHub, and it holds nothing about you or your mods. "
            "A newer version shows a note in the status bar with a link to "
            "its page. The Studio never downloads it. Help ▸ Check for "
            "updates checks at once, with this off too."
        ), self.release_check.text()))
        layout.addLayout(row)
        return box

    # -- the community's GUID register --------------------------------------
    # -- where what the Studio installs lives ---------------------------------
    def _library_box(self) -> QGroupBox:
        from ..core import library

        box = QGroupBox(tr("Library"))
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.library_label = QLabel("")
        self.library_label.setWordWrap(True)
        row.addWidget(self.library_label, 1)
        self.library_button = QPushButton(tr("C&hange…"))
        self.library_button.clicked.connect(self._choose_library)
        row.addWidget(self.library_button, 0, Qt.AlignmentFlag.AlignTop)
        self.library_open_button = QPushButton(tr("O&pen folder"))
        self.library_open_button.clicked.connect(self._open_library)
        row.addWidget(self.library_open_button, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(HelpTip(tr(LIBRARY_HELP), box.title()), 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)

        # What the library holds the only copy of, counted here and listed
        # behind the button: the one thing in it that is not got again.
        only = QHBoxLayout()
        self.only_copies_label = QLabel("")
        self.only_copies_label.setObjectName("StepHint")
        self.only_copies_label.setWordWrap(True)
        only.addWidget(self.only_copies_label, 1)
        self.only_copies_button = QPushButton(tr("&List them…"))
        self.only_copies_button.clicked.connect(self._show_only_copies)
        only.addWidget(self.only_copies_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(only)
        self._library = library
        self._refresh_library()
        return box

    def _only_copies(self) -> tuple:
        """``(groups, held)``: for each game with any, its name, its only
        copies, the copies nothing is recorded about and the ones of those
        two kept as a zip with the backups; and how many copies the library
        holds.

        What the game's own mod.io records say of a copy is written down
        first, so a copy they know is not counted as either.
        """
        from ..core import updates

        chosen = self.window.preferences.library_root()
        groups, held = [], 0
        for key, game in games.GAMES.items():
            folder = self._library.game_dir(key, chosen)
            # Said of what is known without it, where it cannot be read.
            with contextlib.suppress(OSError):
                updates.learn_from_cache(folder, key)
            held += sum(1 for mod in self._library.held(folder) if mod.kind == self._library.COPY)
            only, unknown = self._library.only_copies(folder), self._library.unrecorded(folder)
            kept = self._library.backed_up(folder)
            if only or unknown or kept:
                groups.append((game.label, only, unknown, kept))
        return groups, held

    def _show_only_copies(self) -> None:
        from .only_copies_dialog import OnlyCopiesDialog

        groups, _held = self._only_copies()
        OnlyCopiesDialog(groups, self, regroup=lambda: self._only_copies()[0]).exec()
        # A backup made there is a copy less to say there is no other of.
        self._refresh_library()

    def _refresh_library(self) -> None:
        chosen = self.window.preferences.library_root()
        where = wrappable_path(self._library.root(chosen))
        self.library_label.setText(where if chosen else tr("%s  (the default)", where))
        # The folder is made by the first thing put in it, so until then
        # there is nothing to open.
        there = self._library.root(chosen).is_dir()
        self.library_open_button.setEnabled(there)
        self.library_open_button.setToolTip("" if there else tr(
            "The folder does not exist yet. The Studio makes it the first "
            "time it installs a mod."
        ))

        groups, held = self._only_copies()
        only = sum(len(group[1]) for group in groups)
        unknown = sum(len(group[2]) for group in groups)
        kept = sum(len(group[3]) for group in groups)
        self.only_copies_button.setEnabled(bool(only or unknown or kept))
        if not held:
            self.only_copies_label.setText(tr("The library holds no mods."))
            return
        if not only and not unknown and not kept:
            self.only_copies_label.setText(trn(
                "The library's %(n)s mod can be downloaded or built again.",
                "All of the library's %(n)s mods can be downloaded or built again.",
                held,
            ))
            return
        said = []
        if only:
            said.append(trn(
                "The library holds the only copy of %(n)s mod. It can't be "
                "downloaded again.",
                "The library holds the only copy of %(n)s mods. They can't be "
                "downloaded again.",
                only,
            ))
        if unknown and only:
            said.append(trn(
                "Where %(n)s more came from is not recorded.",
                "Where %(n)s more came from is not recorded.",
                unknown,
            ))
        elif unknown:
            said.append(trn(
                "Where %(n)s of the library's %(held)s mods came from is not recorded.",
                "Where %(n)s of the library's %(held)s mods came from is not recorded.",
                unknown, held=number(held),
            ))
        if kept:
            said.append(trn(
                "%(n)s mod there is no other copy of is backed up.",
                "%(n)s mods there is no other copy of are backed up.",
                kept,
            ))
        self.only_copies_label.setText(" ".join(said))

    def _open_library(self) -> None:
        open_on_disk(self._library.root(self.window.preferences.library_root()))

    def _choose_library(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(
            self, tr("Choose the library folder"), str(self._library.root(self.window.preferences.library_root())),
        )
        if chosen:
            self.window.preferences.set_library_root(chosen)
            self._refresh_library()

    # -- how Play starts each game ----------------------------------------------
    def _start_box(self) -> QGroupBox:
        box = QGroupBox(tr("Starting the game"))
        layout = QVBoxLayout(box)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(HelpTip(tr(
            "Play starts each game through its store, Steam or Ubisoft "
            "Connect, because the games' launchers often refuse a direct "
            "start. If that fails, or you start the game another way, choose "
            "what Play starts: the program, a shortcut, a launcher, or a "
            "store address."
        ), box.title()))
        layout.addLayout(head)
        self.start_labels = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        top = Qt.AlignmentFlag.AlignVCenter
        for row, (key, game) in enumerate(games.GAMES.items()):
            grid.addWidget(rich_label("<b>%s</b>" % html.escape(game.label)), row, 0, top)
            label = QLabel("")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 1, top)
            choose = QPushButton(tr("Choose…"))
            choose.clicked.connect(lambda _c=False, k=key: self._choose_start(k))
            grid.addWidget(choose, row, 2, top)
            automatic = QPushButton(tr("Automatic"))
            automatic.setToolTip(tr("Start the game through its store again."))
            automatic.clicked.connect(lambda _c=False, k=key: self.set_start(k, ""))
            grid.addWidget(automatic, row, 3, top)
            self.start_labels[key] = label
        layout.addLayout(grid)
        self._refresh_start()
        return box

    def _refresh_start(self) -> None:
        for key, label in self.start_labels.items():
            chosen = self.window.preferences.game_start(key)
            if chosen:
                label.setText(wrappable_path(chosen))
                continue
            found = self.window.preferences.automatic_game_start(key)
            label.setText(
                tr("Automatic: %s", found) if found
                else tr("Automatic: no Steam or Ubisoft Connect install found")
            )

    def _choose_start(self, key: str) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _f = QFileDialog.getOpenFileName(
            self, tr("Choose what Play starts for %s", games.get(key).label), "",
            tr("Programs and shortcuts (*.exe *.lnk *.url *.bat *.cmd);;All files (*)"),
        )
        if chosen:
            self.set_start(key, chosen)

    def set_start(self, key: str, how: str) -> None:
        self.window.preferences.set_game_start(key, how)
        self._refresh_start()

    # -- the game's own loader, found in the Anno Modding Tools extension ------
    def _loader_box(self) -> QGroupBox:
        box = QGroupBox(tr("Mod loader check"))
        layout = QVBoxLayout(box)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(HelpTip(tr(
            "With the Anno Modding Tools extension for VS Code installed, the "
            "Studio runs your mod through the extension's copy of the game's "
            "mod loader. The Export tab shows whether the loader's result "
            "matches what the Studio shows. The check is optional. The "
            "extension's copy can differ from the one in the game, so the "
            "check shows its version."
        ), box.title()))
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.loader_labels = {}
        for row, (key, game) in enumerate(games.GAMES.items()):
            grid.addWidget(
                rich_label("<b>%s</b>" % html.escape(game.label)), row, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel("")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 1)
            self.loader_labels[key] = label
        layout.addLayout(grid)

        row = QHBoxLayout()
        self.loader_folder_label = QLabel("")
        self.loader_folder_label.setWordWrap(True)
        row.addWidget(self.loader_folder_label, 1)
        choose = QPushButton(tr("Choose &folder…"))
        choose.setToolTip(tr(
            "Use this if the extension is not in your editor's extensions folder."
        ))
        choose.clicked.connect(self._choose_loader_folder)
        row.addWidget(choose, 0, Qt.AlignmentFlag.AlignTop)
        automatic = QPushButton(tr("Automatic"))
        automatic.setToolTip(tr("Look only in the editors' extensions folders."))
        automatic.clicked.connect(lambda: self.set_loader_folder(""))
        row.addWidget(automatic, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)

        # What the loader's two actions on the Check menu last said, each
        # beside what it is: the actions are run from there.
        probes = QHBoxLayout()
        self.probes_label = QLabel("")
        self.probes_label.setWordWrap(True)
        probes.addWidget(self.probes_label, 1)
        probes.addWidget(HelpTip(tr(
            "The Studio's test patches, run through the loader: a loop, a "
            "flag merge, a search condition and more. This says whether the "
            "loader and the Studio read each one the same way. They run when "
            "a new version of the extension appears, and Check \u25b8 Check "
            "the reading rules with the loader runs them again."
        ), tr("Reading rules")), 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(probes)

        newest = QHBoxLayout()
        self.newest_label = QLabel("")
        self.newest_label.setWordWrap(True)
        newest.addWidget(self.newest_label, 1)
        newest.addWidget(HelpTip(tr(
            "The newest version of Anno Modding Tools on the VS Code "
            "Marketplace. It is looked up once a day while the extension is "
            "installed, and Check \u25b8 Check for loader extension updates "
            "looks it up now. VS Code updates the extension, not the Studio."
        ), tr("Extension updates")), 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(newest)
        self._refresh_loader()
        return box

    def _refresh_loader(self) -> None:
        for key, label in self.loader_labels.items():
            found = self.window.loader_check.game_loader(key)
            label.setText(
                "%s\n%s" % (tr_stored(found.said), found.exe) if found is not None
                else tr("Not found. Install Anno Modding Tools in VS Code to "
                        "add the check.")
            )
        chosen = self.window.loader_check.loader_folder()
        self.loader_folder_label.setText(
            tr("Also looking in %s", chosen) if chosen
            else tr("Looking in the extensions folders of VS Code and similar editors.")
        )
        said = tr_stored(self.window.loader_check.probes_said())
        self.probes_label.setText(
            said or tr("Reading rules not checked against this loader yet.")
        )
        self.newest_label.setText(self.window.loader_check.newest_said())

    def _choose_loader_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(
            self, tr("Choose the Anno Modding Tools extension folder"), self.window.loader_check.loader_folder()
        )
        if chosen:
            self.set_loader_folder(chosen)

    def set_loader_folder(self, path: str) -> None:
        self.window.loader_check.set_loader_folder(path)
        self._refresh_loader()

    # -- the GUIDs new assets take ---------------------------------------------
    def _ranges_box(self) -> QGroupBox:
        """Per game, the range new assets draw from -- the one the creator
        name is listed under unless another is chosen -- and the community
        register they are chosen from.  The New asset dialog starts on the
        range chosen here, and can still take another for one loadout."""
        box = QGroupBox(tr("GUID ranges"))
        layout = QVBoxLayout(box)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(HelpTip(tr(
            "New assets take their GUIDs from ranges that modders have "
            "claimed, so two mods don't use the same GUID. The list is kept "
            "at %s. Your author name finds your range. Choose it here if "
            "your listing spells your name differently.", guidranges.REPOSITORY,
        ), box.title()))
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        self.range_combos, self.range_notes, self._registers = {}, {}, {}
        for row, (key, game) in enumerate(games.GAMES.items()):
            grid.addWidget(
                rich_label("<b>%s</b>" % html.escape(game.label)), row * 2, 0,
                Qt.AlignmentFlag.AlignVCenter)
            combo = QComboBox()
            # As wide as the column, not the longest of a hundred owners'
            # names: sized to that, it pushed the tab past the dialog's edge.
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(20)
            combo.setAccessibleName(tr("GUID range for %s", game.label))
            combo.currentIndexChanged.connect(lambda _i, k=key: self._choose_range(k))
            grid.addWidget(combo, row * 2, 1)
            note = QLabel("")
            note.setObjectName("StepHint")
            note.setWordWrap(True)
            note.setOpenExternalLinks(True)
            grid.addWidget(note, row * 2 + 1, 1)
            self.range_combos[key], self.range_notes[key] = combo, note
        layout.addLayout(grid)

        row = QHBoxLayout()
        self.ranges_label = QLabel("")
        self.ranges_label.setObjectName("StepHint")
        self.ranges_label.setWordWrap(True)
        row.addWidget(self.ranges_label, 1)
        self.ranges_button = QPushButton(tr("Update no&w"))
        self.ranges_button.setToolTip(tr("Fetches the current list from the community register."))
        self.ranges_button.clicked.connect(self._update_ranges)
        row.addWidget(self.ranges_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        self._refresh_ranges()
        return box

    def _fill_range_combos(self) -> None:
        """Each game's ranges, from its register as it is now, with the
        choice made before selected again."""
        for key, combo in self.range_combos.items():
            register = guidranges.load(key)
            if register.personal() is None:
                register.ranges.append(PERSONAL_RANGE)
            self._registers[key] = register
            wanted = self.window.preferences.guid_range(key)
            with QSignalBlocker(combo):
                combo.clear()
                # Translators: the first choice of the GUID range, the one the
                # author name is listed under in the community register.
                combo.addItem(tr("Found by your author name"), "")
                for item in register.claimable():
                    combo.addItem(item.label, item.owner)
                at = combo.findData(wanted) if wanted else 0
                combo.setCurrentIndex(max(at, 0))
        self._refresh_range_notes()

    def _refresh_range_notes(self) -> None:
        creator = self.window.preferences.creator_name()
        aliases = self.window.preferences.aliases()
        for key, note in self.range_notes.items():
            register = self._registers[key]
            owner = str(self.range_combos[key].currentData() or "")
            chosen = your_range(register, creator, owner, aliases)
            text = describe_range(chosen, register, creator, aliases=aliases)
            if not owner:
                # Translators: %(range)s is the range the author name found,
                # %(said)s the sentence saying whose it is.
                text = tr("%(range)s. %(said)s", range=html.escape(chosen.label), said=text)
            show_rich(note, text)

    def _choose_range(self, key: str) -> None:
        self.window.preferences.set_guid_range(key, str(self.range_combos[key].currentData() or ""))
        self._refresh_range_notes()

    def _refresh_ranges(self) -> None:
        self._fill_range_combos()
        said = []
        for key in games.GAMES:
            register = guidranges.load(key)
            game, count = games.get(key).label, len(register.ranges)
            if register.missing:
                said.append(tr("%s: not fetched yet", game))
            else:
                said.append(trn("%(game)s: %(n)s range (downloaded copy)",
                                "%(game)s: %(n)s ranges (downloaded copy)", count, game=game))
        self.ranges_label.setText("\n".join(said))

    def _update_ranges(self) -> None:
        self.window.preferences.update_guid_ranges()
        self._refresh_ranges()

    # -- leaving ------------------------------------------------------------
    def done(self, result: int) -> None:
        """Every way out, including Escape and the window's own close button.

        A name typed and then closed straight away, with the caret still in
        the box, never raises editingFinished -- and there is no Cancel here
        for Escape to mean, so every exit means the same thing.
        """
        self._apply_aliases()
        self._apply_creator()
        # What the account did, said where the window says things.
        if self.account.note:
            self.window.status_label.setText(self.account.note)
        super().done(result)
