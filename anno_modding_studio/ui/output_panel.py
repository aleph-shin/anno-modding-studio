"""Review the generated ModOps and write out the mod."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    adding,
    build,
    builds,
    builtby,
    declaring,
    disk,
    filecache,
    games,
    generator,
    histories,
    library,
    modfolder,
    modinfo,
    modio,
    patching,
    projects,
    publish,
    releases,
    scripts,
    secrets,
    settings,
    sharing,
    versioning,
)
from ..core.changes import KIND_PUBLISH, OWN, Loadout, LoadoutError, is_own_mod
from ..core.changes import loadout_name as file_loadout_name
from ..core.i18n import (
    STORED,
    date_text,
    join_list,
    number,
    tr,
    tr_kw,
    tr_noop,
    tr_stored,
    trn,
)
from . import theme
from .add_to_loadout_dialog import AddToLoadoutDialog
from .add_to_loadout_dialog import Row as AddRow
from .help_tip import EXPORT_HELP, HelpTip
from .modio_publish_dialog import LinkDialog, NewMod, PublishDialog
from .prompts import Prompts
from .widgets import (
    ElidingLabel,
    TextCap,
    ask,
    busy_cursor,
    enter_activates,
    flash_button_feedback,
    hold_text_lines,
    named,
    shown_row,
    tell,
    wrappable_path,
)
from .xml_view import XmlHighlighter

# The gap the left column keeps to its right, which its scroll bar stands in.
COLUMN_GAP = 8

# The Mod to export box's last choice: every mod you changed, at once. Not an
# origin -- a mod's folder name cannot start with a NUL.
ALL_YOURS = "\0yours"


class _Column(QScrollArea):
    """A scroll area that asks for the room its widget asks for.

    Qt's own asks for no more than a fixed few lines of text, whatever is
    in it.  The window is split by what each side asks for, so the column
    asking for less moved the split on every tab.
    """

    def sizeHint(self):
        inside = self.widget()
        return inside.sizeHint() if inside is not None else super().sizeHint()

_DIFFICULTY_LABELS = {
    # Translators: how a mod changes Anno 117's difficulty, as the mod declares
    # it -- here, by cheating (no construction costs, say).
    "cheat": tr_noop("cheat", context="difficulty"),
    "easier": tr_noop("easier", context="difficulty"),
    # Translators: how a mod changes Anno 117's difficulty -- not at all.
    "unchanged": tr_noop("unchanged", context="difficulty"),
    "harder": tr_noop("harder", context="difficulty"),
}


class OutputPanel(QWidget):
    """Mod metadata, live XML preview, validation, and export."""

    loadoutEdited = Signal()
    # A mod's Scripts block was kept: which mods run code, and what their
    # scripts share, has moved for every list that says so.
    scriptsEdited = Signal()
    # A mod's Options block was kept: its ModOps that read a setting are to
    # be read again, at what it declares now. The mod's origin.
    optionsEdited = Signal(str)
    # A ModOp that reads an option was asked to be gone to from the Options
    # dialog: its mod, "op" or "unread", and which.
    fileRequested = Signal(str, str, object)
    # The folder written into, and which mod was written: its origin, or
    # None where it was not one mod, or no mod's build.
    modExported = Signal(str, object)
    modPublished = Signal(str)  # a sentence about what went to mod.io
    changeActivated = Signal(str, str)  # guid, path -- a validation row, opened
    # The loadout saved, before a mod of it is added to another: answered
    # by the time the emit returns.
    saveRequested = Signal()
    # The mod.io sign-in, asked for by the On mod.io row: answered by the
    # time the emit returns.
    signInRequested = Signal()
    # This loadout made what the game loads: Game ▸ Make this live….
    makeLiveRequested = Signal()
    # A sentence for the status line: what Update built.
    modUpdated = Signal(str)
    # The mod added to other loadouts, or taken out of them.
    loadoutsChanged = Signal()
    # Add to loadout's What I play now…: this loadout's file, saved, and
    # the mod to add to a loadout of what the game loads.
    playNowRequested = Signal(object, str)

    def __init__(self, loadout, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.index = None
        # The window's answers to where things are (ui.where); None standing alone.
        self.where = None
        # And its list of what is running in the background (ui.background).
        self.background = None
        # And what the loadout is called, which the checkpoint a publish
        # takes notes its edits as made in.
        self.loadout_name = str
        # And where it is: its file, or None before it is saved.
        self.loadout_path = lambda: None
        # And its mods of yours given a new ModID moved to folders of their
        # own, before a build: the window's, which follows them on screen.
        self.own_folders = lambda: builtby.own_folders(self.loadout)
        self._xml_text = ""
        # The same mod as an export writes it: its patch, and each file the
        # patch includes.
        self._as_files = generator.Written()
        # What the last export of one mod left out of it, said in words: see
        # export_mod.
        self.left_out: list = []
        # The last refresh's for_export, with the origin it was of.
        self._exported = None
        # The game's own loader, as a second opinion on the mod on the preview
        # -- see core.loadercheck. How to find it (the window's, which knows
        # the settings), the verdicts by mod with the key each was asked
        # under, and the check running now, if one is.
        self._loader_finder = None
        # A newer Anno Modding Tools than the one asked, when one is out.
        self._loader_update = ""
        self._verdicts = {}
        self._checking = None
        self._check_again = False
        # The check running now was asked of the loadout before: what it says
        # when it ends is dropped. See forget_loader_verdicts().
        self._drop_next = False
        # Play is asking the loader itself: no check of the tab's starts by
        # itself meanwhile. See checks_held().
        self._held = False
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        # After the typing stops: a check is a second or two, and a mod being
        # edited changes far more often than that.
        self._check_timer.setInterval(1500)
        self._check_timer.timeout.connect(self.check_with_loader)
        # The setup card on screen instead of the preview, a mod list being
        # filled (its own signals ignored meanwhile), and the loader's rows
        # under the preview.
        self._showing_setup = False
        self._loading_mods = False
        self._loader_rows: list = []
        # The summary as the Studio's own check left it, and whether it says
        # the changes are verified: what the loader's findings are added to.
        self._summary_own = ("", "SubHeading", False)
        self._default_output_dir = None
        self._chosen_output_dir = None
        # Where copies of overwritten mods go; None means the usual place under
        # the user's documents. Overridable so a test never writes into it.
        self.backup_into = None
        # How mod.io is reached: the credential store and a client factory,
        # both replaceable so a test never touches the real file or the network.
        self.modio_store = None
        self.modio_client = lambda key, token="", game_id=0: modio.Client(
            key, token=token, game_id=game_id
        )
        # Which loaded mods' details are yours to edit, and what that was
        # decided against -- see details_are_yours().
        self._yours = {}
        self._yours_from = None
        self._build_ui()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The tab says "Export" already. How the file is written is behind the
        # ? beside the preview, and what Export is for beside its destination.

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # -- left: metadata + options
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, COLUMN_GAP, 0)

        self.meta_box = QGroupBox(tr("Details for modinfo.json"))
        meta_box = self.meta_box
        form = QFormLayout(meta_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Said, not asked. Which game this is was settled by the files loaded
        # on the Start tab, and everything in the loadout -- every path, every
        # GUID, every change -- was read against them. A dropdown here could
        # only relabel an 1800 mod as a 117 one and write it to a folder shape
        # the other game never reads; the real answer is loading that game.
        self.game_label = QLabel("")
        # Translators: "Start" is the name of the Studio's first tab.
        self.game_label.setToolTip(tr("Set by the game files loaded on the Start tab."))
        self._show_game()

        self.name_edit = QLineEdit(self.loadout.own.name)
        self.version_edit = QLineEdit(self.loadout.own.version)
        self.creator_edit = QLineEdit(self.loadout.own.creator)
        self.modid_edit = QLineEdit(self.loadout.own.mod_id)
        # Angle brackets, not parentheses: parenthesised placeholder text reads
        # as an instruction to fill the field in, which is the opposite of true.
        self.modid_edit.setPlaceholderText(tr("<auto-populate from mod name>"))
        # Categories mirror what mod.io offers for each game -- a short, fixed
        # list, so this is a plain combo rather
        # than the typeable, filterable one templates need: mod.io does not
        # accept an arbitrary category, so nothing here should invite typing
        # one in. _reload_categories() still appends a loadout's existing
        # category if it is not on the current game's list, so switching games
        # never silently discards it -- just without free text entry to get
        # there in the first place.
        self.category_combo = QComboBox()
        self._reload_categories()
        # mod.io's summary, which is the modinfo's Description too: what a
        # player reads of the mod under its name on mod.io and in search.
        # Named as mod.io names it, as mod.io is where players see it, and
        # held to what mod.io takes. Its page's description is rich text, and
        # the website's to write (see core.publish).
        self.description_edit = QPlainTextEdit()
        self.summary_cap = TextCap(self.description_edit, modio.SUMMARY_LIMIT)
        self._set_summary(self.loadout.own.description)
        # Whole lines: the column scrolls where the window is short of room.
        hold_text_lines(self.description_edit, 5)
        self.summary_count = QLabel("")
        self.summary_count.setObjectName("StepHint")
        self.description_edit.textChanged.connect(self._show_summary_count)

        # The version is yours; beside it, the three moves authors make by hand
        # and the build the Studio counts (see core.versioning, core.builds).
        self.bump_menu = QMenu(self)
        self.bump_menu.aboutToShow.connect(self._fill_bump_menu)
        # Translators: a button that moves a mod's version number on, as in
        # 1.2.0 to 1.3.0.
        self.bump_button = QPushButton(tr("Bump"))
        self.bump_button.setToolTip(tr(
            "Moves the version on. The menu shows where each step lands."
        ))
        self.bump_button.setMenu(self.bump_menu)
        self.build_label = QLabel("")
        self.build_label.setObjectName("StepHint")
        version_row = QWidget()
        version_layout = QHBoxLayout(version_row)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.addWidget(self.version_edit, 1)
        version_layout.addWidget(self.bump_button)
        version_layout.addWidget(self.build_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # What the work since the last release calls for, and why -- a guess
        # the author can take with one click or ignore (see core.releases).
        self._suggestion = None
        self.suggest_label = QLabel("")
        self.suggest_label.setObjectName("StepHint")
        self.suggest_label.setWordWrap(True)
        self.suggest_button = QPushButton("")
        self.suggest_button.clicked.connect(self._use_suggestion)
        suggest_row = QWidget()
        suggest_layout = QHBoxLayout(suggest_row)
        suggest_layout.setContentsMargins(0, 0, 0, 0)
        suggest_layout.addWidget(self.suggest_label, 1)
        suggest_layout.addWidget(self.suggest_button, 0, Qt.AlignmentFlag.AlignTop)
        self.suggest_label.hide()
        self.suggest_button.hide()

        shown_row(form, tr("Game"), self.game_label)
        # Translators: a field of a mod's modinfo.json -- the mod's name.
        form.addRow(tr("&Name"), self.name_edit)
        version_caption = QLabel(tr("Ve&rsion"))
        version_caption.setBuddy(self.version_edit)
        form.addRow(version_caption, version_row)
        form.addRow("", suggest_row)
        # Translators: a field of a mod's modinfo.json -- who made the mod.
        form.addRow(tr("&Author"), self.creator_edit)
        form.addRow(tr("Cate&gory"), self.category_combo)
        form.addRow(tr("Mod &ID"), self.modid_edit)
        # Translators: mod.io's word for the short text under a mod's name;
        # use the word mod.io uses in your language.
        summary_caption = QLabel(tr("&Summary"))
        summary_caption.setBuddy(self.description_edit)
        summary_row = QVBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(2)
        summary_row.addWidget(self.description_edit)
        count_row = QHBoxLayout()
        count_row.addWidget(self.summary_count, 1)
        count_row.addWidget(HelpTip(tr(
            "What players read of the mod under its name on mod.io and in "
            "search, and the description in its modinfo.json. Publishing an "
            "update puts it on mod.io. The mod's page on mod.io has a longer "
            "description of its own, written on mod.io's website."
        ), summary_caption.text()))
        summary_row.addLayout(count_row)
        form.addRow(summary_caption, summary_row)
        self._show_summary_count()
        # Anno 117's modinfo can list Lua for the game to run. Only said and
        # edited here; the Lua is written in an editor made for it.
        scripts_row = QHBoxLayout()
        self.scripts_label = QLabel("")
        self.scripts_label.setWordWrap(True)
        scripts_row.addWidget(self.scripts_label, 1)
        self.scripts_button = QPushButton(tr("Edit…"))
        self.scripts_button.clicked.connect(self.edit_scripts)
        scripts_row.addWidget(self.scripts_button)
        self._scripts_row = form.rowCount()
        # Translators: a field of a mod's modinfo.json -- the Lua scripts the
        # game runs for the mod.
        shown_row(form, tr("Scripts"), scripts_row)
        self._scripts_form = form
        # And the options it offers its players, which its ModOps read back.
        options_row = QHBoxLayout()
        self.options_label = QLabel("")
        self.options_label.setWordWrap(True)
        options_row.addWidget(self.options_label, 1)
        self.options_button = QPushButton(tr("Edit…"))
        self.options_button.clicked.connect(self.edit_options)
        options_row.addWidget(self.options_button)
        self._options_row = form.rowCount()
        # Translators: a field of a mod's modinfo.json -- the options a
        # player can set for the mod.
        shown_row(form, tr("Options"), options_row)
        # And what Anno 117 asks every mod to say about the game it belongs
        # in: how it changes the difficulty, and whether it needs a new game,
        # can come out of a save, works in multiplayer and in the campaign.
        # The game shows these to players choosing mods.
        self.difficulty_combo = QComboBox()
        for difficulty in modinfo.DIFFICULTIES:
            self.difficulty_combo.addItem(
                tr(_DIFFICULTY_LABELS.get(difficulty, difficulty), context="difficulty"),
                difficulty,
            )
        difficulty_row = QHBoxLayout()
        difficulty_row.addWidget(self.difficulty_combo, 1)
        difficulty_caption = QLabel(tr("Difficult&y"))
        difficulty_caption.setBuddy(self.difficulty_combo)
        # Translators: cheat, easier and unchanged are choices of the
        # Difficulty list this explains.
        difficulty_row.addWidget(HelpTip(tr(
            "The game shows this to players choosing mods. Cheat is for a mod "
            "that, for example, removes construction costs. Easier is for one "
            "that, for example, lowers consumption. Unchanged is balanced like "
            "the base game."
        ), difficulty_caption.text()))
        self._difficulty_row = form.rowCount()
        form.addRow(difficulty_caption, difficulty_row)
        # Two by two: four in a row made the whole tab wider than the window
        # can spare beside the Mod changes panel.
        setup_row = QGridLayout()
        setup_row.setContentsMargins(0, 0, 0, 0)
        self.setup_boxes = {}
        for position, (key, label, tip) in enumerate((
            ("RequiresNewGame", tr("Needs a new game"),
             tr("Only works in a new save, like river slots.")),
            ("SafeToRemove", tr("Safe to remove"),
             tr("Can be taken out of a save without leaving traces.")),
            ("Multiplayer", tr("Multiplayer"), tr("Can be used in multiplayer games.")),
            ("Campaign", tr("Campaign"), tr("Can be used in the campaign.")),
        )):
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.toggled.connect(self._on_setup_changed)
            self.setup_boxes[key] = box
            setup_row.addWidget(box, position // 2, position % 2)
        self._setup_row = form.rowCount()
        # Translators: Anno 117's GameSetup -- what a mod says about the games
        # it can be used in.
        shown_row(form, tr("Game setup"), setup_row)
        self.difficulty_combo.currentIndexChanged.connect(self._on_setup_changed)
        # Whose details these are. With a mods folder open the preview is just
        # as likely to be about somebody else's mod as about yours, and typing
        # into a box that turned out to describe a different one is the
        # sharpest form of the same confusion.
        self.meta_note = QLabel("")
        self.meta_note.setObjectName("StepHint")
        self.meta_note.setWordWrap(True)
        form.addRow("", self.meta_note)
        left_layout.addWidget(meta_box)

        for widget in (self.name_edit, self.version_edit, self.creator_edit, self.modid_edit):
            widget.textChanged.connect(self._on_meta_changed)
        self.category_combo.currentTextChanged.connect(self._on_meta_changed)
        self.description_edit.textChanged.connect(self._on_meta_changed)

        check_box = QGroupBox(tr("Validation"))
        check_layout = QVBoxLayout(check_box)
        check_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.check_summary = QLabel("")
        self.check_summary.setWordWrap(True)
        check_layout.addWidget(self.check_summary)
        self.check_list = QListWidget()
        self.check_list.setWordWrap(True)
        self.check_list.setMaximumHeight(200)
        self.check_list.hide()  # only shown when there is something to report
        # A change that would not apply is a change somebody has to go and look
        # at, and this was a list you could only read: the name of the asset
        # was there, and getting to it meant finding it again by hand in the
        # editor or the change list.
        enter_activates(self.check_list)
        self.check_list.itemActivated.connect(self._open_problem)
        self.check_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.check_list.customContextMenuRequested.connect(self._problem_menu)
        self.check_list.setToolTip(tr(
            "Double-click a row or press Enter to open its change."
        ))
        self.check_list.setAccessibleName(check_box.title())
        check_layout.addWidget(self.check_list)
        # What the game's own loader makes of the mod, when the Anno Modding
        # Tools extension is there to ask. One line, never a gate.
        loader_row = QHBoxLayout()
        self.loader_label = QLabel("")
        self.loader_label.setObjectName("StepHint")
        self.loader_label.setWordWrap(True)
        self.loader_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        loader_row.addWidget(self.loader_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.loader_button = QPushButton(tr("Ask the loader again"))
        self.loader_button.setToolTip(tr(
            "Runs this mod, as Export would write it, through the game's loader again."
        ))
        self.loader_button.clicked.connect(lambda: self.check_with_loader(force=True))
        loader_row.addWidget(self.loader_button, 0, Qt.AlignmentFlag.AlignTop)
        check_layout.addLayout(loader_row)
        left_layout.addWidget(check_box)
        left_layout.addStretch(1)

        # The column scrolls where the window is too short for it. Without
        # that the layout took the height it lacked out of whatever would
        # give, which was the hint under the description: in German, at
        # 1500 by 950, its last line was cut through.
        self.left_scroll = _Column()
        self.left_scroll.setObjectName("ExportColumn")
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_scroll.setWidget(left)
        splitter.addWidget(self.left_scroll)
        # Its scroll bar is as wide as the gap the column keeps to its right,
        # and takes the gap's place when it is there. So the column is no
        # wider for being able to scroll, and the fields are as wide with
        # the bar as without.
        self.left_scroll.verticalScrollBar().setFixedWidth(COLUMN_GAP)
        self.left_scroll.verticalScrollBar().rangeChanged.connect(
            lambda _low, high: left_layout.setContentsMargins(
                0, 0, 0 if high > 0 else COLUMN_GAP, 0
            )
        )

        # -- right: preview
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        # Which of the loaded mods is being written out.  Hidden until there is
        # more than one, since with a single mod it is not a choice.
        self.mod_row = QWidget()
        mod_layout = QHBoxLayout(self.mod_row)
        mod_layout.setContentsMargins(0, 0, 0, 0)
        # "Previewing" named what the box changed on screen rather than what it
        # decides; "Mod to export" named one of the three rows under the
        # preview, and the box decides what all three are about.
        mod_caption = QLabel(tr("&Mod:"))
        mod_layout.addWidget(mod_caption)
        self.mod_box = QComboBox()
        mod_caption.setBuddy(self.mod_box)
        self.mod_box.currentIndexChanged.connect(self._on_mod_changed)
        mod_layout.addWidget(self.mod_box, 1)
        mod_layout.addWidget(HelpTip(tr_kw(
            "Chooses which loaded mod the preview and the rows below it are "
            "about: in your game, on mod.io and as files. Each mod is written "
            "as its own folder. “All the mods you changed” writes your own mod "
            "and your [[patch|patches]] at once. Your edits in the asset editor always "
            "go to your own mod, whichever mod is picked here."
        ), mod_caption.text()))
        # The mods you changed, all at once, are one more choice in the box
        # (ALL_YOURS) and go out through the one Export button: they were a
        # button of their own beside it, and beside that one rewriting every
        # loaded mod -- the Studio's reading of other people's mods put in
        # place of their files, which nobody sharing a mod wants.
        self.mod_row.setVisible(False)
        right_layout.addWidget(self.mod_row)

        preview_header = QHBoxLayout()
        self.preview_label = ElidingLabel(mode=Qt.TextElideMode.ElideLeft)
        self.preview_label.setObjectName("SubHeading")
        preview_header.addWidget(self.preview_label, 1)
        preview_header.addWidget(HelpTip(tr(
            "Each change is written as its own narrow ModOp: a replace, an "
            "addition, a removal or a whole new asset. Two mods that change "
            "different parts of the same asset then work together. A ModOp "
            "somebody else wrote is written back as it is, so a pattern still "
            "covers assets that other mods add."
        ), tr("Preview")))
        refresh_button = QPushButton(tr("Refresh"))
        refresh_button.clicked.connect(self.refresh)
        preview_header.addWidget(refresh_button)
        right_layout.addLayout(preview_header)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setAccessibleName(tr("Preview"))
        theme.monospace(self.preview)
        self.highlighter = XmlHighlighter(self.preview.document())
        right_layout.addWidget(self.preview, 1)

        # The three places a mod goes from here, a row each: a line on where
        # it stands and the next step (docs/proposals/export-tab-proposal.md,
        # section 4). Exporting led the tab, and is the way out used least:
        # a mod goes into the game you play, or onto mod.io, far more often.
        ways = QGridLayout()
        ways.setContentsMargins(0, 4, 0, 0)
        ways.setHorizontalSpacing(10)
        ways.setColumnStretch(1, 1)

        def row(caption: str, at: int) -> tuple:
            label = QLabel(caption)
            label.setObjectName("SubHeading")
            ways.addWidget(label, at, 0, Qt.AlignmentFlag.AlignVCenter)
            status = QLabel("")
            status.setWordWrap(True)
            ways.addWidget(status, at, 1, Qt.AlignmentFlag.AlignVCenter)
            return label, status

        # Translators: a row of the Export tab: whether the mod is in the
        # loadouts you play.
        self.game_caption, self.game_status = row(tr("In your game"), 0)
        # Which of your other loadouts have the mod: this game's loadouts,
        # from the files cache, ticked where they have it.
        self.game_button = QPushButton(tr("Add to loadout\u2026"))
        self.game_button.clicked.connect(self._on_game_button)
        ways.addWidget(self.game_button, 0, 3)
        # Once other loadouts have the mod: its build in the library made
        # again, which each of them links.
        # Translators: a button of the Export tab: the mod built again for
        # the loadouts that have it.
        self.update_button = QPushButton(tr("Update"))
        self.update_button.setToolTip(tr("Builds the mod again for the loadouts that have it."))
        self.update_button.clicked.connect(lambda: self.update_build())
        self.update_button.hide()
        ways.addWidget(self.update_button, 0, 2)
        # Publishing and testing, not installing: what Play is for, and what
        # each row is, behind the one ?.
        # Translators: "Play" is the Studio's button that builds your mods and
        # starts the game; use the same word as that button.
        self.play_note = HelpTip(tr(EXPORT_HELP), self.game_caption.text())
        ways.addWidget(self.play_note, 0, 4)

        # Translators: a row of the Export tab: the mod's page on mod.io.
        self.modio_caption, self.modio_status = row(tr("On mod.io"), 1)
        # One button, whatever the state: it does what comes next --
        # signing in, linking, publishing.
        self.modio_button = QPushButton(tr("Publish to mod.io…"))
        self.modio_button.clicked.connect(lambda: self.publish_to_modio())
        ways.addWidget(self.modio_button, 1, 3)

        # Translators: a row of the Export tab: the mod written out as a
        # folder, a zip or XML.
        files_caption = QLabel(tr("As files"))
        files_caption.setObjectName("SubHeading")
        ways.addWidget(files_caption, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        self.destination_label = ElidingLabel()
        self.destination_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        ways.addWidget(self.destination_label, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        change_dest = QPushButton(tr("Change…"))
        change_dest.setToolTip(tr("Chooses the folder exports go into."))
        change_dest.clicked.connect(self._choose_destination)
        files_caption.setBuddy(change_dest)
        ways.addWidget(change_dest, 2, 2)
        # Translators: a button with a menu: export the mod as a folder, as a
        # zip, or its ModOps as XML.
        self.export_button = QPushButton(tr("Export"))
        export_menu = QMenu(self.export_button)
        # Translators: an entry of the Export menu: the mod written as a
        # folder.
        self.folder_action = export_menu.addAction(tr("Folder"), lambda: self.export_chosen())
        # Translators: an entry of the Export menu: the mod folder, zipped.
        self.zip_action = export_menu.addAction(tr("Zip"), lambda: self.export_zip())
        self.zip_action.setToolTip(tr(
            "Zips the mod folder with its folder name at the top, as mod.io expects."
        ))
        export_menu.addSeparator()
        # The mod's ModOps as one file, copied or saved: for pasting into a
        # mod of your own somewhere else, not a way to share this one.
        self.copy_action = export_menu.addAction(tr("Copy XML"), self._copy)
        self.save_action = export_menu.addAction(tr("Save assets.xml…"), self._save_xml)
        export_menu.setToolTipsVisible(True)
        self.export_button.setMenu(export_menu)
        ways.addWidget(self.export_button, 2, 3)
        right_layout.addLayout(ways)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 700])
        outer.addWidget(splitter, 1)

        # Anno 117's three rows are in view only for an Anno 117 loadout, from
        # the start. Left in view until the first refresh, which is the first
        # time the tab is shown, they made the tab, and so the window, 128 px
        # taller at its smallest than anything it went on to show. The column
        # keeps the width they need, so it is as wide for either game.
        # Measured with the gap in, whether or not the bar has it just now.
        need = (left.minimumSizeHint().width() + COLUMN_GAP
                - left_layout.contentsMargins().right())
        left.setMinimumWidth(need - COLUMN_GAP)
        self.left_scroll.setMinimumWidth(need)
        self._show_game_rows()

    # -- state --------------------------------------------------------------
    def set_index(self, index) -> None:
        self.index = index

    def load_loadout_fields(self) -> None:
        """Push loadout metadata into the form (after loading a saved loadout)."""
        self._refresh_modio()
        for widget, value in (
            (self.name_edit, self.loadout.own.name),
            (self.version_edit, self.loadout.own.version),
            (self.creator_edit, self.loadout.own.creator),
            (self.modid_edit, self.loadout.own.mod_id),
        ):
            widget.blockSignals(True)
            widget.setText(value)
            widget.blockSignals(False)
        self._reload_categories()
        self._show_game()
        self.description_edit.blockSignals(True)
        self._set_summary(self.loadout.own.description)
        self.description_edit.blockSignals(False)
        self._show_summary_count()
        self.refresh()

    def _set_summary(self, text: str) -> None:
        """Show ``text`` as the summary, whatever its length: one written
        before the box was held to mod.io's is not cut short here."""
        with self.summary_cap.setting():
            self.description_edit.setPlainText(text)

    def _show_summary_count(self) -> None:
        """How much room the summary has left, or how far past mod.io's
        limit one written before is."""
        left = modio.SUMMARY_LIMIT - len(self.description_edit.toPlainText())
        if left >= 0:
            self.summary_count.setText(trn(
                "%(n)s character left", "%(n)s characters left", left))
            theme.tint(self.summary_count, "")
        else:
            self.summary_count.setText(trn(
                "%(n)s character more than mod.io takes",
                "%(n)s characters more than mod.io takes", -left))
            theme.tint(self.summary_count, "warn")

    def _show_details_for(self, origin: str) -> None:
        """Fill the details box with whichever mod the preview is about.

        Editable for a mod that is yours -- the one this loadout writes, one of
        yours read back in to carry on with, a patch you made -- and read-only
        for somebody else's: those are its own details, as it ships, and the box
        that edits yours should not double as one that silently edits theirs.

        It used to be editable for the loadout's own mod only, which locked the
        whole box on a mod of your own opened to carry on with: that mod is a
        loaded one, so it read as somebody else's.
        """
        record = self.loadout.mod(origin)
        if record is None:
            # Gone since it was picked: the next refresh picks one that is
            # here, and its details are no other mod's to show meanwhile.
            return
        mine = record is self.loadout.own
        yours = self.details_are_yours(origin)
        fields = (
            (self.name_edit, record.name),
            (self.version_edit, record.version),
            (self.creator_edit, record.creator),
            (self.modid_edit, record.mod_id),
        )
        for widget, value in fields:
            value = value or ""
            widget.blockSignals(True)
            # Only when it moved: this runs after every keystroke, and setText
            # puts the cursor at the end of a field being typed into the middle of.
            if widget.text() != value:
                widget.setText(value)
            widget.setReadOnly(not yours)
            widget.blockSignals(False)
        # Naming is part of deciding what you are doing, and this page is for
        # doing it. Writing a mod of your own, the loadout *is* the mod: it was
        # named when the work was, and the Start tab renames both together. The
        # other two ways round, your own mod is a thing inside a workspace whose
        # name is deliberately not the same -- "Anno 1800 loadout" holding
        # "Anno 1800 loadout patch" -- so there this is where it is named.
        self.name_edit.setReadOnly(not yours or self._name_follows_loadout(origin))
        description = record.description or ""
        self.description_edit.blockSignals(True)
        if self.description_edit.toPlainText() != description:
            self._set_summary(description)
        self.description_edit.setReadOnly(not yours)
        self.description_edit.blockSignals(False)
        self._show_summary_count()
        self.category_combo.setEnabled(yours)
        self.category_combo.blockSignals(True)
        position = self.category_combo.findText(record.category or "")
        if position >= 0:
            self.category_combo.setCurrentIndex(position)
        self.category_combo.blockSignals(False)
        self.bump_button.setEnabled(yours)
        self._show_build()
        self.show_suggestion()
        self.meta_box.setTitle(
            tr("Mod details") if mine
            else tr("Mod details: %s", record.label or origin)
        )
        self._show_scripts(origin, yours)
        self._show_options(origin, yours)
        self._show_setup(origin, yours)
        self._refresh_modio()
        if not yours:
            self.meta_note.setText(
                tr("These are that mod's own details, as it ships. Pick your "
                   "mod above to edit yours.")
                if "" in self.pickable()
                else tr("These are that mod's own details, as it ships.")
            )
            return
        # Where the inherited answers come from, so a field you cannot change
        # here does not read as a field that is broken. Each note is a whole
        # sentence of its own, set side by side.
        notes = []
        if self._name_follows_loadout(origin):
            # Translators: "Start" is the name of the Studio's first tab.
            notes.append(tr(
                "This mod and its loadout share one name. Rename them on the "
                "Start tab."
            ))
        if mine:
            notes.append(tr(
                "The author is who you publish as. Change it on the Start tab, "
                "or type here to publish this one mod under another name."
            ))
        else:
            notes.append(tr(
                "What you type here goes into %s's modinfo.json the next "
                "time it is exported. The rest of that file stays as it is.",
                record.label,
            ))
            host = self.loadout.layer(record.patches)
            if host is not None and patching.sorts_before_its_mod(self.loadout, record):
                # A rename can do this in Anno 117, which sorts by category
                # and name. Said, not refused: the declaration still holds.
                notes.append(tr(
                    "This name and category sort the patch before %s. It still "
                    "declares that it loads after that mod, and sorting after "
                    "it as well is safer.",
                    host.label,
                ))
        self.meta_note.setText("  ".join(notes))

    # -- version and build ------------------------------------------------------
    def _fill_bump_menu(self) -> None:
        """The moves on from the version as it stands, each saying where it lands.

        Built as the menu opens, so it always starts from what is in the box.
        A version that is not numbered is not moved on: the menu says so and
        offers the numbered version it would switch to instead.
        """
        self.bump_menu.clear()
        current = self.version_edit.text().strip()
        if versioning.is_numbered(current):
            for level in versioning.LEVELS:
                after = versioning.bump(current, level)
                # Translators: a menu item -- "Minor: 1.3.0  —  adds something".
                action = self.bump_menu.addAction(tr(
                    "%(level)s: %(version)s (%(meaning)s)",
                    level=tr(versioning.NAME[level], context="version part"),
                    version=after, meaning=tr(versioning.MEANING[level]),
                ))
                action.triggered.connect(lambda _checked=False, to=after: self.set_version(to))
            return
        note = self.bump_menu.addAction(
            tr("“%s” is not numbered, so there is nothing to move on", current)
            if current else tr("There is no version yet")
        )
        note.setEnabled(False)
        after = versioning.adopted(current)
        action = self.bump_menu.addAction(tr("Use numbered versions, starting at %s", after))
        action.triggered.connect(lambda _checked=False, to=after: self.set_version(to))

    def set_version(self, version: str) -> bool:
        """Put ``version`` in the box, as though typed: the mod's version follows."""
        if self.version_edit.isReadOnly():
            return False
        self.version_edit.setText(version)
        return True

    def _show_build(self) -> None:
        """The last build of the mod on the preview, beside its version."""
        origin = self.current_origin()
        if not builds.is_numbered(self.loadout, origin):
            self.build_label.hide()
            return
        build = builds.number(self.loadout, origin)
        # Translators: "Play" is the Studio's button that builds your mods and
        # starts the game.
        about = tr(
            "Counted by the Studio: every export or Play whose output differs "
            "from the last one is the next build. It is written into the "
            "modinfo and the header, never into the version."
        )
        held = builds.stale(self.loadout, origin)
        if held is not None:
            # The game moved on under it. Its patches find their nodes by
            # paths into the game's own assets, and an update can move them.
            self.build_label.setText(tr("build %s · older game data", build))
            theme.tint(self.build_label, "warn")
            said = {"build": build, "was": held.get("update"), "now": self.loadout.base_update}
            if said["was"] and said["now"]:
                made = tr("Build %(build)s was made on %(was)s's data. The "
                          "game data loaded now is %(now)s.", **said)
            elif said["was"]:
                made = tr("Build %(build)s was made on %(was)s's data. The "
                          "game data loaded now is different.", **said)
            elif said["now"]:
                made = tr("Build %(build)s was made on earlier game data. "
                          "The game data loaded now is %(now)s.", **said)
            else:
                made = tr("Build %s was made on earlier game data. The game "
                          "data loaded now is different.", build)
            self.build_label.setToolTip("%s %s\n\n%s" % (
                made,
                tr("The update may have moved something this mod changes: check "
                   "it still does what it should, then build it again."),
                about,
            ))
        else:
            self.build_label.setText(tr("build %s", build) if build else tr("no builds yet"))
            theme.tint(self.build_label)
            self.build_label.setToolTip(about)
        self.build_label.show()

    def show_suggestion(self) -> None:
        """What the work since the last release calls for, under the version.

        Only for the mod this loadout releases: its releases are what the
        checkpoints and the publish record describe.  Nothing before the
        first release, when the first version is whatever the author types.
        """
        found = None
        if self._publishable():
            found = releases.suggest(self.loadout, self.current_origin())
        self._suggestion = found
        if found is None:
            self.suggest_label.hide()
            self.suggest_button.hide()
            return
        self.suggest_label.setText(releases.short(found))
        self.suggest_label.setToolTip(
            "\n".join(
                [releases.headline(found), *found.reasons, "",
                 tr("This is a guess from what changed since the last "
                    "release. The version is yours to set.")]
            )
        )
        self.suggest_label.show()
        wanted = found.version
        offered = bool(wanted) and not found.covered and not self.version_edit.isReadOnly()
        self.suggest_button.setText(tr("Use %s", wanted, context="a version"))
        self.suggest_button.setVisible(offered)

    def _use_suggestion(self) -> None:
        if self._suggestion is not None and self._suggestion.version:
            self.set_version(self._suggestion.version)

    def _show_game_rows(self) -> bool:
        """Put the rows only Anno 117 has in or out of view. Whether they are in."""
        shown = self.loadout.game == games.ANNO_117
        form = self._scripts_form
        if form.isRowVisible(self._scripts_row) != shown:
            for row in (self._scripts_row, self._options_row, self._difficulty_row,
                        self._setup_row):
                form.setRowVisible(row, shown)
            # Qt passes a change of height up only through widgets in view,
            # and this tab is out of view until it is picked. Told by hand,
            # or the window goes on asking for the height it needed before.
            form.activate()
            widget = self.meta_box
            while widget is not None:
                widget.updateGeometry()
                widget = widget.parentWidget()
        return shown

    def _show_scripts(self, origin: str, yours: bool) -> None:
        """The Scripts row: what the mod's modinfo runs, in a few words."""
        if not self._show_game_rows():
            return
        found = scripts.declared({"Scripts": scripts.block_of(self.loadout, origin)})
        hooks = tr(", ", context="between items of a list").join(
            name for name, _ in found.hooks) if found is not None else ""
        if found is None:
            # Translators: what a mod's Scripts are -- it has none.
            text = tr("none", context="scripts")
        elif found.modules and hooks:
            text = trn("%(n)s module, run on %(hooks)s", "%(n)s modules, run on %(hooks)s",
                       len(found.modules), hooks=hooks)
        elif found.modules:
            text = trn("%(n)s module", "%(n)s modules", len(found.modules))
        else:
            # Translators: the names are when the game runs the mod's Lua --
            # Init, Load, Unload, Tick -- and stay as they are.
            text = tr("run on %s", hooks) if hooks else ""
        self.scripts_label.setText(text)
        self.scripts_button.setEnabled(yours)
        # Translators: "Mod changes" is the name of the Studio's panel.
        self.scripts_button.setToolTip(
            tr("The modules this mod's modinfo lists and the Lua it runs on Init, "
               "Load, Unload and Tick.") if yours else
            tr("That mod's own scripts, as it ships. Its row in the Mod changes "
               "panel says what they are.")
        )

    def _show_options(self, origin: str, yours: bool) -> None:
        """The Options row: how many the mod offers, and any its ModOps
        read that nothing declares."""
        if not self._show_game_rows():
            return
        offered = settings.read(settings.block_of(self.loadout, origin))
        missing = declaring.undeclared(self.loadout, origin) if yours else []
        count = len(offered.settable)
        if count:
            text = trn("%(n)s option", "%(n)s options", count)
        else:
            # Translators: what a mod's Options are -- it has none.
            text = tr("none", context="options")
        if missing:
            text = tr("%(options)s, and its ModOps read %(names)s, which nothing declares",
                      options=text,
                      names=tr(", ", context="between items of a list").join(
                          "$" + name for name in missing))
        self.options_label.setText(text)
        theme.tint(self.options_label, "warn" if missing else "")
        self.options_button.setEnabled(yours)
        # Translators: "Mod changes" is the name of the Studio's panel, and
        # "Mod options…" an item of its menu; use the same words as those.
        self.options_button.setToolTip(
            tr("What this mod lets its players decide. Its ModOps read each "
               "option as $name.") if yours else
            tr("That mod's own options, as it ships. Right-click it in Mod "
               "changes for Mod options…")
        )

    def edit_options(self) -> None:
        self.declare_options(self.current_origin())

    def propose_options(self, origin: str, before) -> Optional[bool]:
        """An Apply in an XML pane just changed ``origin``'s ModOps:
        where they now read settings of their own that nothing declares and
        that they did not read before -- ``before``, as
        :func:`core.declaring.undeclared` had it -- open the Options dialog
        with each in it as a proposal. Only for a mod of yours in Anno 117,
        where options are declared at all.

        None where nothing was proposed; else whether the dialog was kept.
        Not kept is the Apply not wanted, and the caller takes it back."""
        if self.loadout.game != games.ANNO_117 or not self.details_are_yours(origin):
            return None
        new = [name for name in declaring.undeclared(self.loadout, origin) if name not in set(before)]
        if not new:
            return None
        return self.declare_options(origin, new, after_apply=True)

    def declare_options(self, origin: str, proposed=(), focus: str = "", after_apply=False) -> bool:
        """The Options dialog for ``origin``, with ``proposed`` in it as
        new entries and ``focus`` the one in front; whether it was kept.
        ``after_apply``: an Apply brought the proposals in, and turning the
        dialog down takes it back."""
        from .options_editor_dialog import OptionsEditorDialog

        if not self.details_are_yours(origin):
            return False
        before = settings.block_of(self.loadout, origin)
        dialog = OptionsEditorDialog(
            self.loadout, origin, declaring.mod_id_of(self.loadout, origin), self, proposed, focus,
            after_apply=after_apply,
        )
        dialog.show_in_file = True
        try:
            kept = dialog.exec() == QDialog.DialogCode.Accepted
            wanted = dialog.wanted_use if kept else None
        finally:
            dialog.deleteLater()
        if kept and settings.block_of(self.loadout, origin) != before:
            self.loadoutEdited.emit()
            self.optionsEdited.emit(origin)
            self.refresh()
        if wanted is not None:
            self.fileRequested.emit(origin, *wanted)
        return kept

    def _show_setup(self, origin: str, yours: bool) -> None:
        """Difficulty and GameSetup, for Anno 117: the mod's own words, or
        for a key its modinfo leaves out what the loader takes it to be."""
        if not self._show_game_rows():
            return
        difficulty, flags = modinfo.setup_of(self.loadout, origin)
        self._showing_setup = True
        try:
            # Only the four the loader knows, plus the mod's own word when it
            # is something else -- shown as written, not quietly replaced.
            while self.difficulty_combo.count() > len(modinfo.DIFFICULTIES):
                self.difficulty_combo.removeItem(self.difficulty_combo.count() - 1)
            position = self.difficulty_combo.findData(difficulty.lower())
            if position < 0:
                self.difficulty_combo.addItem(
                    tr("%s (not one the game knows)", difficulty) if difficulty
                    else tr("none (not one the game knows)"),
                    difficulty,
                )
                position = self.difficulty_combo.count() - 1
            self.difficulty_combo.setCurrentIndex(position)
            for key, box in self.setup_boxes.items():
                box.setChecked(bool(flags.get(key)))
                box.setEnabled(yours)
            self.difficulty_combo.setEnabled(yours)
        finally:
            self._showing_setup = False

    def _on_setup_changed(self, *_args) -> None:
        if self._showing_setup:
            return
        origin = self.current_origin()
        if not self.details_are_yours(origin):
            return
        difficulty = str(self.difficulty_combo.currentData() or "")
        flags = {key: box.isChecked() for key, box in self.setup_boxes.items()}
        if (difficulty, flags) == modinfo.setup_of(self.loadout, origin):
            return
        modinfo.set_setup(self.loadout, origin, difficulty, flags)
        self.loadoutEdited.emit()
        self.refresh()

    def edit_scripts(self) -> None:
        from .scripts_dialog import ScriptsDialog

        origin = self.current_origin()
        if not self.details_are_yours(origin):
            return
        mod_id = modinfo.written_mod_id(generator.for_mod(self.loadout, origin))
        dialog = ScriptsDialog(self.loadout, origin, mod_id, self)
        try:
            kept = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()
        if kept:
            self.loadoutEdited.emit()
            self.scriptsEdited.emit()
            self.refresh()
            self._show_scripts(origin, True)

    # -- mod.io -----------------------------------------------------------------
    # -- in your game --------------------------------------------------------
    # What the In your game row says of the mod picked, by what it is
    # (docs/proposals/export-tab-proposal.md, section 8).
    _ADDABLE = "addable"      # a mod or a patch of yours, kept in a project
    _PLAYED = "played"        # the own mod of a loadout made to play
    _ONLY_HERE = "only here"  # the own mod beside a mod you carry on with,
                              # somebody else's mod, every mod you changed

    def _game_kind(self, origin: str) -> str:
        if self.exporting_all_yours():
            return self._ONLY_HERE
        if is_own_mod(origin):
            if self.loadout.is_the_mod:
                return self._ADDABLE
            # Made for this loadout's list, and used only here: the patch
            # over a whole list, or the own mod beside the mod carried on.
            return self._PLAYED if self.loadout.own.loads_last else self._ONLY_HERE
        return self._ADDABLE if origin in self.loadout.project_origins() else self._ONLY_HERE

    def _project_of(self, origin: str):
        """The file of the project ``origin`` is kept in, or None before
        the loadout's first save makes it."""
        ref = self.loadout.projects.get(origin)
        return projects.where(ref.path) if ref is not None else None

    def is_live(self, path) -> bool:
        """Whether the loadout at ``path`` is what the game loads now."""
        library_dir = self._library_or_none()
        live = library.live(library_dir) if library_dir is not None else ""
        return bool(live and path is not None and disk.same_file(live, path))

    def loadouts_having(self, origin: str) -> list:
        """The other loadouts for this game that have ``origin``'s project,
        from the files cache."""
        project = self._project_of(origin)
        if project is None:
            return []
        return filecache.loadouts_with_project(project, self.loadout.game,
                                               excluding=self.loadout_path())

    def _refresh_game_row(self) -> None:
        origin = self.current_origin()
        kind = self._game_kind(origin)
        button = self.game_button
        self.update_button.hide()
        # The step taken most often, for a mod of yours or a patch. On a
        # loadout made to play, Play in the tab bar is its main action.
        primary = "Primary" if kind == self._ADDABLE else ""
        if button.objectName() != primary:
            button.setObjectName(primary)
            theme.repolish(button)
        button.setEnabled(True)
        button.setToolTip("")
        if kind == self._PLAYED:
            live = self.is_live(self.loadout_path())
            self.game_status.setText(
                tr("This loadout is what the game loads.") if live else
                tr("This loadout is not what the game loads now.")
            )
            button.setText(tr("Make it live…"))
            button.setToolTip(tr("Makes this loadout what the game loads, without starting it."))
            button.setVisible(not live)
            return
        if kind == self._ONLY_HERE:
            self.game_status.setText(
                tr("Pick one mod to see where it is.") if self.exporting_all_yours() else
                tr("Only in this loadout: made for its list.") if is_own_mod(origin) else
                tr("Only in this loadout.")
            )
            button.setVisible(False)
            return
        button.setText(tr("Add to loadout\u2026"))
        button.setVisible(True)
        why = self._cannot_add(origin)
        if why:
            self.game_status.setText(why)
            button.setEnabled(False)
            return
        having = self.loadouts_having(origin)
        names = [file_loadout_name(path) for path in having]
        live = [name for name, path in zip(names, having, strict=True) if self.is_live(path)]
        if not names:
            said = tr("In no loadout but this one.")
        elif live:
            said = trn("In %(loadouts)s, which is what the game loads.",
                       "In %(loadouts)s. %(live)s is what the game loads.",
                       len(names), loadouts=join_list(names), live=live[0])
        else:
            said = tr("In %s.", join_list(names))
        folder = self.loadouts_from_folder(origin)
        if folder:
            others = sorted(file_loadout_name(path) for path in folder)
            said = "%s %s" % (said, trn(
                "%(loadouts)s has it from its folder, not from its project.",
                "%(loadouts)s have it from their folders, not from its project.",
                len(others), loadouts=join_list(others),
            ))
        button.setToolTip(tr("Chooses which of your other loadouts have the mod."))
        if names:
            behind = self._behind(origin)
            if behind:
                said = "%s %s" % (said, tr("The build they have is from before your last changes."))
            # The step to take next, where it is behind: the one highlighted.
            self.update_button.setVisible(True)
            self.update_button.setEnabled(behind is not False)
            if behind:
                button.setObjectName("")
                theme.repolish(button)
            if self.update_button.objectName() != ("Primary" if behind else ""):
                self.update_button.setObjectName("Primary" if behind else "")
                theme.repolish(self.update_button)
        self.game_status.setText(said)

    def _behind(self, origin: str):
        """Whether the library's build of ``origin`` is behind the mod
        (:func:`.build.behind`); None where that cannot be said."""
        library_dir = self._library_or_none()
        if library_dir is None:
            return None
        try:
            return build.behind(self.loadout, origin, library_dir)
        except (OSError, ValueError):
            return None

    def _cannot_add(self, origin: str) -> str:
        """Why the mod cannot be added to another loadout, or ""."""
        if self.loadout.held(origin):
            return tr("Its project cannot be written from here, so nothing is built from it.")
        library_dir = self._library_or_none()
        project = builtby.project_of(self.loadout, origin)
        if library_dir is not None and project:
            folder = generator.folder_for(self.loadout, origin)
            other = builtby.theirs(library_dir, folder, project)
            if other:
                return tr("Its folder in the library, %(folder)s, is the build of "
                          "another project, %(project)s.",
                          folder=folder, project=projects.where(other).stem)
        return ""

    def _mods_folder(self) -> str:
        """The mods folder this game plays from, or "" where none is set."""
        return self.where.mods_folder() if self.where is not None else ""

    def _on_game_button(self) -> None:
        if self._game_kind(self.current_origin()) == self._PLAYED:
            self.makeLiveRequested.emit()
            self._refresh_game_row()
        else:
            self.add_to_loadout()

    def loadout_rows(self, origin: str) -> list:
        """This game's other loadouts, as the Add to loadout dialog lists
        them, from the files cache: which have the mod, which the game
        loads, and for a patch which of the mods it needs each lacks."""
        having = {str(path).lower() for path in self.loadouts_having(origin)}
        wanted = []
        for other in adding.needed(self.loadout, origin):
            theirs = self.loadout.layer(other)
            if theirs is not None:
                keys = modfolder.match_keys(theirs.mod_id, theirs.origin)
                with_it = {str(path).lower() for path in filecache.loadouts_naming(keys, self.loadout.game)}
                wanted.append((theirs.label, with_it))
        folder = {str(path).lower() for path in self.loadouts_from_folder(origin)}
        rows = []
        for path in filecache.loadouts(self.loadout.game, excluding=self.loadout_path()):
            key = str(path).lower()
            rows.append(AddRow(
                path=Path(path), name=file_loadout_name(path), has=key in having,
                live=self.is_live(path),
                lacks=[name for name, with_it in wanted if key not in with_it],
                from_folder=key in folder,
            ))
        return rows

    def loadouts_from_folder(self, origin: str) -> list:
        """The other loadouts that have ``origin`` by one of its names but
        not through its project -- read from its folder, before the project
        was made -- by their files, from the files cache."""
        if self._game_kind(origin) != self._ADDABLE:
            return []
        having = {str(path).lower() for path in self.loadouts_having(origin)}
        named = filecache.loadouts_naming(adding.keys_of(self.loadout, origin), self.loadout.game,
                                          excluding=self.loadout_path())
        return [Path(path) for path in named if str(path).lower() not in having]

    def add_to_loadout(self) -> bool:
        """Add to loadout…: pick which of your other loadouts have the mod,
        and add it to or take it out of each.  Whether anything moved."""
        origin = self.current_origin()
        name = self.loadout.label_of(origin) or tr("your mod")
        to_play = filecache.loadouts_to_play(self.loadout.game, excluding=self.loadout_path())
        dialog = AddToLoadoutDialog(name, self.loadout_rows(origin), self, offer_play_now=not to_play)
        answer = dialog.exec()
        if answer == AddToLoadoutDialog.PLAY_NOW:
            return self.add_to_what_i_play(origin)
        if answer != int(QDialog.DialogCode.Accepted):
            return False
        had = {row.path for row in dialog.rows if row.has}
        ticked = dialog.ticked()
        return self.change_loadouts(origin, sorted(ticked - had), sorted(had - ticked))

    def add_to_what_i_play(self, origin: str) -> bool:
        """*What I play now…*, from Add to loadout: this loadout saved, as
        for any add, then the window asked to make a loadout of what the
        game loads with the mod in it (``playNowRequested``).  Whether it
        was asked."""
        origin = dict(self.own_folders()).get(origin, origin)
        self.saveRequested.emit()
        path = self.loadout_path()
        if path is None or origin not in self.loadout.projects or not self.caught_up():
            return False
        self.playNowRequested.emit(Path(path), origin)
        return True

    def change_loadouts(self, origin: str, adding_to, taking_from) -> bool:
        """Add ``origin`` to each loadout of ``adding_to`` and take it out of
        each of ``taking_from``, by their files, all in one save
        (docs/proposals/export-tab-proposal.md, sections 5.3 to 5.5).
        Whether anything moved."""
        adding_to, taking_from = [Path(p) for p in adding_to], [Path(p) for p in taking_from]
        if not adding_to and not taking_from:
            return False
        name = self.loadout.label_of(origin) or tr("your mod")
        mods_folder, library_dir = self._mods_folder(), self._library_or_none()
        if not mods_folder or library_dir is None:
            self._no_mods_folder()
            return False
        if taking_from and not ask(
            self, tr("Take the mod out?"),
            tr_kw("Take %(mod)s out of %(loadouts)s? The mod itself is kept, here "
                  "and in its [[project]].", mod=named(name),
               loadouts=join_list(file_loadout_name(path) for path in taking_from)),
            tr("&Take it out"),
        ):
            return False
        if adding_to:
            # A mod of yours given a new ModID goes into a folder of its own,
            # beside the old mod's, and is added under that name.
            moved = dict(self.own_folders())
            origin = moved.get(origin, origin)
            # Saved first: the loadouts refer to its project, as saved, and
            # its build is of the mod as it is here.
            self.saveRequested.emit()
            if origin not in self.loadout.projects or not self.caught_up():
                return False
        project = self._project_of(origin)
        targets, notes, live, there = {}, [], None, ""
        # Reading and writing a loadout of four hundred mods takes a while,
        # and nothing said so: the row said where the mod was before for
        # half a minute (R3-3, R3-9, hands-on run 3).
        try:
            # Every loadout first, in hand: one that cannot have the mod
            # stops the lot before anything is built or written.
            with busy_cursor():
                for path in [*adding_to, *taking_from]:
                    target = targets[path] = Loadout.load(path)
                    there = file_loadout_name(path)
                    if path in adding_to:
                        done = adding.add(target, self.loadout, origin, mods_folder)
                        if done.taken_up:
                            notes.append(tr_kw("%s had it from its folder, and reads it from "
                                               "its [[project]] from now on.", there))
                        if done.brought:
                            notes.append(tr("%(loadout)s also gets %(mods)s, which it needs.",
                                            loadout=there, mods=join_list(done.brought)))
                        if done.switched_on:
                            notes.append(tr("%(loadout)s has %(mods)s switched on for it.",
                                            loadout=there, mods=join_list(done.switched_on)))
                        # One it had from its folder is on in the game already.
                        held = None if done.taken_up else done.origin
                    else:
                        held = adding.having(target, project)
                    if held is not None and self.is_live(path):
                        # What switches it in the game, asked before it goes.
                        live = (path, adding.switch_of(target, held), path in adding_to)
                    if path in taking_from:
                        adding.take_out(target, project)
            there = ""
            if adding_to:
                with busy_cursor():
                    built = build.write(self.loadout, [origin], mods_folder, library_dir, link=False)
                self._show_build()
                if built.failed:
                    QMessageBox.critical(self, tr("Not added"),
                                         "\n\n".join(why for _name, why in built.failed))
                    return False
            with busy_cursor():
                adding.save_all(targets)
        except (OSError, ValueError, LoadoutError, projects.ProjectError) as exc:
            tell(self, tr("Not added"), tr(
                "No loadout was changed. %(loadout)s: %(why)s", loadout=named(there), why=exc,
            ) if there else str(exc), warning=True)
            return False
        said = []
        if adding_to:
            said.append(tr(
                "Added %(mod)s to %(loadouts)s. Each reads it the next time it is opened.",
                mod=named(name), loadouts=join_list(file_loadout_name(path) for path in adding_to),
            ))
        if taking_from:
            said.append(tr(
                "Took %(mod)s out of %(loadouts)s.", mod=named(name),
                loadouts=join_list(file_loadout_name(path) for path in taking_from),
            ))
        said += notes
        # Where the mod is now, before any question about the game: the
        # loadouts are saved already.
        self._refresh_game_row()
        if live is not None:
            path, switch, on = live
            said.append(self._switch_live(targets[path], path, switch, on))
        tell(self, tr("Add to loadout"), "\n\n".join(line for line in said if line))
        self._refresh_game_row()
        self.loadoutsChanged.emit()
        return True

    def _no_mods_folder(self) -> None:
        QMessageBox.information(self, tr("No mods folder"), tr(
            "The Studio does not know where %s's mods folder is. Load the "
            "game's files from its installation on the Start tab, or read "
            "a mods folder in, and try again.", games.get(self.loadout.game).label,
        ))

    def update_build(self, origin: Optional[str] = None) -> bool:
        """Update: build ``origin`` -- the mod picked, unless told -- again
        into its folder in the library, which every loadout that has it
        links: one the game loads has the new build at once, and each reads
        the mod's project again when it is next opened
        (docs/proposals/export-tab-proposal.md, section 5.6).  This loadout
        is saved first, as for *Add to loadout…*.  Whether it was built."""
        origin = self.current_origin() if origin is None else origin
        name = self.loadout.label_of(origin) or tr("your mod")
        mods_folder, library_dir = self._mods_folder(), self._library_or_none()
        if not mods_folder or library_dir is None:
            self._no_mods_folder()
            return False
        having = self.loadouts_having(origin)
        live = [path for path in [*having, self.loadout_path()] if path is not None and self.is_live(path)]
        if live and Prompts(self).game_running(self.loadout.game, tr(
            "Close the game first. It read %(mod)s when it started, from the "
            "build Update would write over.", mod=name,
        )):
            return False
        # As Play and Add to loadout do: a mod of yours given a new ModID
        # goes into a folder of its own first.
        origin = dict(self.own_folders()).get(origin, origin)
        self.saveRequested.emit()
        if origin not in self.loadout.projects or not self.caught_up():
            return False
        with busy_cursor():
            built = build.write(self.loadout, [origin], mods_folder, library_dir, link=False)
        self._show_build()
        if built.failed:
            QMessageBox.critical(self, tr("Not updated"), "\n\n".join(why for _name, why in built.failed))
            return False
        said = tr("Built %(mod)s again for %(loadouts)s.", mod=name,
                  loadouts=join_list(file_loadout_name(path) for path in having)) if having else \
            tr("Built %s again into the library.", name)
        if live:
            said = "%s %s" % (said, tr("The game has the new build: %s is what it loads.",
                                       file_loadout_name(live[0])))
        self.modUpdated.emit(said)
        self._refresh_game_row()
        return True

    def _switch_live(self, target, path, switch: tuple, on: bool) -> str:
        """The mod switched on or off in the game, in the loadout at ``path``
        that the game loads, if the user agrees; what to say of it."""
        mods_folder, library_dir = target.mods_folder or self._mods_folder(), self._library_or_none()
        loadout = file_loadout_name(path)
        name = self.loadout.label_of(self.current_origin()) or tr("your mod")
        from ..core import launch

        if launch.running(self.loadout.game):
            # It read its mods when it started: switching one now is for
            # its next start, which the loadout's next Play sees to.
            return tr("The game is running, so nothing was switched in it. %s gets "
                      "the mod when it is next played.", loadout)
        if adding.switched_already(target, switch, on, mods_folder):
            # Nothing to ask: it was never switched on, or is on already.
            return ""
        with busy_cursor():
            left = adding.as_play_left_it(target, mods_folder, library_dir)
        if not left:
            # Translators: "Play" is the Studio's button that builds your
            # mods and starts the game.
            return tr(
                "%s is what the game loads, but the mods folder has changed since "
                "its last Play. Open that loadout and press Play to bring the game "
                "back in step.", loadout,
            )
        asked = (
            tr("%(loadout)s is what the game loads. Switch %(mod)s on in the game "
               "now? Every other mod stays as it is.",
               loadout=named(loadout), mod=named(name)) if on else
            tr("%(loadout)s is what the game loads. Switch %(mod)s off in the game "
               "now? Every other mod stays as it is.",
               loadout=named(loadout), mod=named(name))
        )
        if not ask(self, tr("Switch the mod in the game?"), asked,
                   tr("&Switch on") if on else tr("&Switch off"), cancel=tr("&Not now")):
            return tr("The game has it as before until %s is next played.", loadout)
        why = adding.switch_live(target, path, switch, on, mods_folder, library_dir)
        if why:
            return tr("Not switched in the game: %s", why)
        return tr("Switched on in the game.") if on else tr("Switched off in the game.")

    # -- on mod.io ----------------------------------------------------------
    def _store(self):
        if self.modio_store is None:
            self.modio_store = secrets.Store()
        return self.modio_store

    def _publishable(self) -> bool:
        """Whether the previewed mod is the one this loadout publishes.

        The mod of yours the loadout was opened to carry on with, or, without
        one, the loadout's own mod: the link to a mod.io page is the
        loadout's, and the version suggested is measured against that mod's
        releases.  The loadout's own mod beside one of yours is a different
        mod, as a patch of yours is: neither is published from here.
        """
        origin = self.current_origin()
        if origin != self.loadout.home:
            return False
        return self.details_are_yours(origin)

    def _refresh_modio(self) -> None:
        """The On mod.io row: shown only for the mod this loadout publishes,
        saying where it stands, with the button doing what comes next."""
        mine = self._publishable() and not self.exporting_all_yours()
        for widget in (self.modio_caption, self.modio_status, self.modio_button):
            widget.setVisible(mine)
        if not mine:
            return
        credentials = self._store().load()
        linked = int(self.loadout.modio_mod_id or 0)
        signed = credentials.signed_in()
        # Where to sign in, said on the button: it opens it.
        self.modio_button.setToolTip("")
        if not signed and credentials.expired():
            state = tr("Your mod.io sign-in ran out on %s.", date_text(credentials.token_expires))
            self.modio_button.setToolTip(tr("Signs you in to mod.io again, then carries on."))
        elif not signed:
            state = tr("Not signed in to mod.io.")
            self.modio_button.setToolTip(tr("Signs you in to mod.io, then carries on."))
        elif not linked:
            state = tr("Not linked to a mod on mod.io yet.")
            self.modio_button.setToolTip(tr("Links the mod to its page on mod.io, then publishes it."))
        else:
            # Translators: the number is the mod's ID on mod.io.
            state = tr("Linked to mod.io mod %d.", linked)
            self.modio_button.setToolTip(tr("Builds the mod and sends it to mod.io as its next file."))
        version = self._modio_version() if signed and linked else ""
        if version:
            # Translators: a button; the version is the one about to be
            # published.
            self.modio_button.setText(tr("Publish %s…", version))
        else:
            self.modio_button.setText(tr("Publish to mod.io…"))
        sent = self.loadout.published
        if sent:
            said = {
                "version": sent["version"],
                "build": sent.get("build"),
                "when": date_text(sent["when"]) if sent.get("when") else "",
            }
            if said["build"] and said["when"]:
                last = tr("Last published from here: %(version)s, build %(build)s, on "
                          "%(when)s.", **said)
            elif said["build"]:
                last = tr("Last published from here: %(version)s, build %(build)s, on "
                          "an unknown day.", **said)
            elif said["when"]:
                last = tr("Last published from here: %(version)s, on %(when)s.", **said)
            else:
                last = tr("Last published from here: %s, on an unknown day.", said["version"])
            state = "%s  %s" % (state, last)
        if credentials.runs_out_soon():
            # Translators: "File \u25b8 Settings\u2026" is a menu path; use the
            # menu's own translated names.
            state = tr(
                "%(state)s  Your mod.io sign-in runs out on %(when)s. Sign in again "
                "before then: File \u25b8 Settings\u2026",
                state=state, when=date_text(credentials.token_expires),
            )
        self.modio_status.setText(state)

    def _modio_version(self) -> str:
        """The version the mod would be published as: its details'."""
        record = self.loadout.mod(self.current_origin())
        return (record.version if record is not None else "").strip()

    def _signed_out(self) -> None:
        """mod.io no longer takes the sign-in: it is taken off this PC rather
        than sent again, and you are told to sign in again."""
        self._store().drop_token()
        self._refresh_modio()
        QMessageBox.warning(self, tr("Signed out of mod.io"), tr(
            "mod.io no longer accepts your sign-in, so it has been removed from "
            "this PC and nothing was sent. Sign in again in File \u25b8 "
            "Settings\u2026, then try again."
        ))

    def _not_published(self, exc: Exception) -> bool:
        """Say why a publish stopped: False, for the caller to return."""
        if isinstance(exc, modio.SignedOut):
            self._signed_out()
        else:
            QMessageBox.warning(self, tr("Not published"), str(exc))
        return False

    def publish_to_modio(self) -> bool:
        """The On mod.io row's button: whatever comes next, in turn --
        signing in, linking the mod to its page, publishing.  Whether it
        was published."""
        if not self._store().load().signed_in():
            # Settings, on its mod.io tab, where the sign-in is: the
            # window's, which waits for it to close.
            self.signInRequested.emit()
            self._refresh_modio()
            if not self._store().load().signed_in():
                return False
        if not int(self.loadout.modio_mod_id or 0) and not self.link_modio():
            return False
        return self.publish_update()

    def _my_mods(self, credentials, game):
        """Your mods on mod.io for ``game``, or None, said, where they could
        not be listed."""
        try:
            with busy_cursor():
                return self.modio_client(
                    credentials.api_key, credentials.token, game.modio_game_id
                ).my_mods(game.modio_game_id)
        except modio.SignedOut:
            self._signed_out()
        except modio.ModioError as exc:
            QMessageBox.warning(  # i18n: keep - the title is a name
                self, "mod.io", tr("Your mods on mod.io could not be listed: %s", exc)
            )
        return None

    def looks_like(self, mine) -> Optional[modio.ModSummary]:
        """The one of ``mine`` that has this mod's name, or a name ID made
        of its name or its ModID; None where none or several do."""
        record = self.loadout.mod(self.current_origin())
        if record is None:
            return None
        names = {name.strip().casefold() for name in (record.name,) if name.strip()}
        slugs = {publish.name_id_of(text) for text in (record.name, record.mod_id)} - {""}
        found = [mod for mod in mine
                 if mod.name.strip().casefold() in names or mod.name_id.lower() in slugs]
        return found[0] if len(found) == 1 else None

    def link_modio(self) -> bool:
        """Link the mod to its page on mod.io: the one that looks like it,
        if you agree, or one picked from your mods there -- or created on
        the website first, from the same list."""
        credentials = self._store().load()
        if not credentials.signed_in():
            return False
        game = games.get(self.loadout.game)
        mine = self._my_mods(credentials, game)
        if mine is None:
            return False
        guess = self.looks_like(mine)
        if guess is not None and ask(
            self, tr("Link to mod.io"),
            tr("This looks like %s on mod.io. Link it?", named(guess.name)),
            tr("&Link", context="verb"), cancel=tr("&Show my mods"),
        ):
            return self._link(guess.id)
        record = self.loadout.mod(self.current_origin())
        new = NewMod(
            page=game.modio_add_url,
            name=record.name if record is not None else "",
            summary=record.description if record is not None else "",
            ubisoft=self.loadout.game == games.ANNO_117,
            relist=lambda: self._my_mods(self._store().load(), game),
        )
        dialog = LinkDialog(mine, current=int(self.loadout.modio_mod_id or 0), parent=self,
                            new=new)
        if dialog.exec() != int(QDialog.DialogCode.Accepted) or dialog.chosen is None:
            return False
        return self._link(int(dialog.chosen))

    def _link(self, mod_id: int) -> bool:
        self.loadout.modio_mod_id = int(mod_id)
        self.loadoutEdited.emit()
        self._refresh_modio()
        return True

    def publish_update(self) -> bool:
        """Build this mod, then send it to mod.io as its next file.

        Built into a scratch folder that is zipped from, and nothing else:
        publishing is not exporting, and wrote the export folder too, asking
        before it replaced one."""
        import shutil
        import tempfile

        credentials = self._store().load()
        linked = int(self.loadout.modio_mod_id or 0)
        if not credentials.signed_in() or not linked:
            return False
        if not self.caught_up():
            return False
        game = games.get(self.loadout.game)
        client = self.modio_client(credentials.api_key, credentials.token, game.modio_game_id)
        # What is live first, and before the build: a version that cannot
        # follow it is best settled while it can still go into the files.
        try:
            with busy_cursor():
                live = client.mod(game.modio_game_id, linked)
        except modio.ModioError as exc:
            return self._not_published(exc)
        if live is not None and not self._version_follows(live.version):
            return False
        # Measured before the build, which would otherwise become the last
        # release it is measured from.
        if not self._version_suits_changes():
            return False
        found = releases.suggest(self.loadout, self.current_origin())
        self.left_out = []
        writing = self._mod_to_write()
        if writing is None:
            QMessageBox.information(
                self, tr("No changes to publish"),
                tr("This mod has no active changes, so there is nothing to send."),
            )
            return False
        origin, views, self.left_out = writing
        scratch = Path(tempfile.mkdtemp(prefix="ams-publish-"))
        try:
            # The folder zipped, as an export would write it, built where
            # nothing else looks.
            built = build.write(self.loadout, [origin], scratch / "build", views=views,
                                folder_of=generator.export_folder_for)
            self._show_build()
            if built.failed:
                QMessageBox.critical(self, tr("Not published"), built.failed[0][1])
                return False
            (exported,) = built.written
            try:
                with busy_cursor():
                    who = client.me().username
                    ready = publish.plan(
                        client, game.modio_game_id, linked, exported,
                        self.exported_loadout().own.version, scratch, as_user=who, live=live,
                        summary=self.exported_loadout().own.description,
                    )
            except (publish.PublishError, modio.ModioError) as exc:
                return self._not_published(exc)
            if not ready.changelog:
                ready.changelog = releases.changelog(found)
            dialog = PublishDialog(ready, self, note="\n\n".join(
                [*([releases.headline(found)] if found is not None else []), *self.left_out]
            ))
            if dialog.exec() != int(QDialog.DialogCode.Accepted):
                return False
            try:
                with busy_cursor():
                    sent = publish.send(client, game.modio_game_id, ready)
            except modio.Refused as exc:
                QMessageBox.warning(self, tr("mod.io refused it"), publish.why_refused(exc, game.label))
                return False
            except modio.ModioError as exc:
                return self._not_published(exc)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self._record_published(ready.version, sent)
        # The file is live whatever becomes of the summary.
        try:
            with busy_cursor():
                publish.send_summary(client, game.modio_game_id, ready)
        except modio.ModioError as exc:
            QMessageBox.warning(self, tr("Summary not updated"), tr(
                "The file is published, but mod.io did not take the summary: %s", exc))
        said = {"mod": ready.mod.name, "version": sent.version or ready.version}
        note = tr("Published %(mod)s %(version)s to mod.io. It is live now.", **said)
        tell(
            self, tr("Published"),
            tr("Published %(mod)s %(version)s to mod.io. It is live now.\n"
               "\n"
               "%(url)s",
               url=ready.mod.url, mod=named(ready.mod.name), version=said["version"])
            if ready.mod.url else
            tr("Published %(mod)s %(version)s to mod.io. It is live now.",
               mod=named(ready.mod.name), version=said["version"]),
        )
        self.modPublished.emit(note)
        return True

    def _version_follows(self, live: str) -> bool:
        """Whether the version can go out after ``live`` -- made so, if the
        author agrees.

        The one mistake publishing refuses is the version never bumped, and
        refusing it only named the fix.  Offering the next number is the fix,
        shown before anything is exported, so the files carry the version
        that is actually sent.  Versions that do not order are left for
        :func:`publish.plan` to judge.
        """
        version = self.exported_loadout().own.version
        if not versioning.is_behind(version, live):
            return True
        after = versioning.next_after(version, live)
        if after is None:
            return True
        if version.strip().casefold() == live.strip().casefold():
            why = tr("%s is already the live version on mod.io.", version)
        else:
            why = tr("mod.io already has %(live)s, which is newer than %(version)s.",
                     live=live, version=version)
        answer = ask(
            self,
            tr("Publish as %s?", after),
            "%s\n\n%s" % (why, tr(
                "Publish this as %s instead? The version on this tab changes "
                "to match, and so does what the export writes.", after)),
            tr("&Publish as %s", after),
        )
        if not answer:
            return False
        return self.set_version(after)

    def _version_suits_changes(self) -> bool:
        """Whether to go on, having offered the version the changes call for.

        Only asked when the version is below what the work since the last
        release suggests -- a patch number on a release that adds an asset,
        say.  It is a guess, so keeping the version is one of the answers.
        """
        found = releases.suggest(self.loadout, self.current_origin())
        if found is None or found.covered or not found.version:
            return True
        answer = self._ask_suggested(found)
        if answer == "use":
            return self.set_version(found.version)
        return answer == "keep"

    def _ask_suggested(self, found) -> str:
        """``"use"``, ``"keep"`` or ``"cancel"``.  A method so a test can answer."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Publish as %s?", found.version))
        box.setText(releases.headline(found))
        box.setInformativeText(
            "\n".join(found.reasons) + "\n\n"
            + tr("This is a guess from what changed. The version is yours to set.")
        )
        use = box.addButton(tr("Publish as %s", found.version), QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton(tr("Keep %s", found.current, context="a version"),
                             QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(use)
        box.exec()
        clicked = box.clickedButton()
        return "use" if clicked is use else "keep" if clicked is keep else "cancel"

    def _record_published(self, version: str, sent) -> None:
        """Remember what went to mod.io, with a checkpoint of the mod's
        project to measure from, and one of the loadout's list: the ones an
        export of the same work recorded, where there are."""
        origin = self.current_origin()
        number = builds.number(self.loadout, origin)
        # Kept in the history as it is, and shown translated there.
        label = (
            tr_noop("Published v%(version)s, build %(build)d", context=STORED)
            % {"version": version, "build": number}
            if number else
            tr_noop("Published v%s", context=STORED) % version
        )
        name = self.loadout_name()
        checkpoint = self.loadout.release_checkpoint(label, KIND_PUBLISH, origin=origin, loadout=name)
        histories.take_release(self.loadout, label, KIND_PUBLISH, taken_in=name)
        self.loadout.published = {
            "version": version,
            "build": number,
            "when": time.time(),
            "modfile_id": int(getattr(sent, "modfile_id", 0) or 0),
            "checkpoint": checkpoint.timestamp if checkpoint is not None else 0.0,
        }
        self.loadoutEdited.emit()
        self._refresh_modio()
        self.show_suggestion()

    def details_are_yours(self, origin: str) -> bool:
        """Whether the mod ``origin`` is one whose details are yours to change.

        Worked out once per mod rather than on every keystroke. A loaded mod is
        taken for yours partly by its creator name and ModID, and those are two
        of the fields being typed into: deciding again after each letter would
        lock the box one letter into changing either of them.
        """
        if self.loadout.held(origin):
            # Its project cannot be written from here, and its details are
            # in it.
            return False
        if is_own_mod(origin):
            return True
        layer = self.loadout.layer(origin)
        if layer is None:
            return False
        settled = (
            tuple(other.origin for other in self.loadout.layers),
            self.loadout.home,
            self.loadout.own.creator,
            self.loadout.own.mod_id,
        )
        if settled != self._yours_from:
            self._yours, self._yours_from = {}, settled
        if origin not in self._yours:
            self._yours[origin] = bool(layer.patches) or patching.is_mine(
                self.loadout, layer
            )
        return self._yours[origin]

    def _name_follows_loadout(self, origin: str) -> bool:
        """Whether naming this mod and naming the loadout are the same act.

        Where the loadout *is* the mod -- the one started here as writing a
        mod of your own, or the one of yours it was opened to carry on with --
        the Start tab renames both together.  Read off the loadout's own fact
        about that rather than off the card clicked on the way in, which what
        was done since may have left behind.
        """
        return origin == self.loadout.stands_for()

    def _reload_categories(self) -> None:
        """Category lists differ per game, so rebuild on every game change."""
        game = games.get(self.loadout.game)
        self._categories_game = self.loadout.game
        wanted = self.loadout.own.category or game.categories[0]
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItems(list(game.categories))
        if wanted not in game.categories:
            # Keep an unrecognised category rather than silently rewriting it.
            self.category_combo.addItem(wanted)
        self.category_combo.setCurrentText(wanted)
        self.category_combo.blockSignals(False)
        self.loadout.own.category = self.category_combo.currentText()

    def _show_game(self) -> None:
        self.game_label.setText(games.get(self.loadout.game).label)

    def set_game(self, key: str) -> None:
        """Called when a base file is loaded: that is what decides the game."""
        key = key if key in games.GAMES else games.DEFAULT
        # Against the game the list was made for, not the loadout's: the
        # loadout shares the window's, which has usually been told already.
        changed = key != self._categories_game
        self.loadout.game = key
        self._show_game()
        if changed:
            self._reload_categories()
            self.refresh()

    def _on_meta_changed(self) -> None:
        origin = self.current_origin()
        if not self.details_are_yours(origin):
            return  # showing somebody else's details; nothing here edits them
        record = self.loadout.mod(origin)
        if record is None:
            return  # gone since: nothing to write them into
        values = {
            "version": self.version_edit.text(),
            "creator": self.creator_edit.text(),
            "mod_id": self.modid_edit.text(),
            "category": self.category_combo.currentText(),
            "description": self.description_edit.toPlainText(),
        }
        # Not the name when the loadout is the mod: that field is read-only
        # there, and writing it back would let a stale value overwrite a rename
        # that happened somewhere else.
        if not self.name_edit.isReadOnly():
            values["name"] = self.name_edit.text()
        for field, value in values.items():
            setattr(record, field, value)
        self.loadoutEdited.emit()
        self.refresh()
        self._refresh_modio()

    # -- generation ---------------------------------------------------------
    # -- which mod ----------------------------------------------------------
    def pickable(self):
        """Every mod the preview can be about, in load order.

        Yours is on the list even when it is empty, as long as anything else is
        loaded.  Leaving it off meant that opening a mods folder pointed the
        preview at somebody else's mod and gave you no way back to your own --
        while the asset editor went on writing to yours.

        The same goes for a patch of yours and for the mod the loadout
        carries on with: they are made empty and filled afterwards, and
        listing only mods with changes in them meant that reading a mod in to
        change it previewed that whole mod, as though exporting it were the
        idea, until the first edit landed in the patch.
        """
        changed = set(self.loadout.origins())
        origins = [
            origin
            for origin in self.loadout.origins(loaded=True)
            if origin in changed or self._is_yours_to_fill(origin)
        ]
        if self.loadout.layers and OWN not in origins and self.loadout.has_own_mod():
            origins = origins + [""]
        return origins

    def _is_yours_to_fill(self, origin: str) -> bool:
        """A loaded mod that is the place your edits go, empty or not."""
        layer = self.loadout.layer(origin)
        return layer is not None and (bool(layer.patches) or origin == self.loadout.home)

    def current_origin(self) -> str:
        """The loaded mod the preview and the export button are about.

        With every mod you changed picked (:meth:`exporting_all_yours`), the
        first of them: what is worked out of one mod is worked out of it,
        though the tab shows none of that until one mod is picked again."""
        origins = self.pickable()
        if len(origins) < 2:
            # One mod loaded, whoever wrote it: there is nothing to choose, and
            # defaulting to "yours" would preview an empty document.
            return origins[0] if origins else ""
        data = self.mod_box.currentData()
        if data == ALL_YOURS:
            yours = self.mine_to_export()
            return yours[0] if yours else ""
        if data is None or str(data) not in origins:
            # The box can still hold the last loadout's pick while another is
            # opened: asked for its folder then, a mod this loadout does not
            # have stopped the opening half-way (R3-29, hands-on run 3).
            return "" if "" in origins else origins[0]
        return str(data)

    def exporting_all_yours(self) -> bool:
        """Whether the box says every mod you changed, rather than one mod."""
        return len(self.pickable()) > 1 and self.mod_box.currentData() == ALL_YOURS

    def _offers_all_yours(self) -> bool:
        """Whether the box offers every mod you changed: with two of them.
        One is a mod on the list already."""
        return len(self.mine_to_export()) > 1

    def show_mod(self, origin: str) -> None:
        """Point the preview at a mod, because the editor's target moved there.

        The two are different questions -- what am I writing into, what am I
        writing out -- but the answer is nearly always the same one, and having
        them silently disagree is the confusion worth removing.  Picked by hand
        afterwards they part company again, which is fine.
        """
        self._reload_mods()
        position = self.mod_box.findData(origin)
        if position < 0 or position == self.mod_box.currentIndex():
            return
        # Quietly: refreshing here re-verifies every change in the loadout --
        # three seconds on a full mods folder -- for a tab nobody is looking
        # at. The caller marks the preview dirty, and it is rebuilt on arrival.
        blocked = self.mod_box.blockSignals(True)
        try:
            self.mod_box.setCurrentIndex(position)
        finally:
            self.mod_box.blockSignals(blocked)

    def previewing_yours(self) -> bool:
        return not self.current_origin()

    def exported_loadout(self):
        """That mod, on its own -- what actually gets written out."""
        return self.for_export(self.current_origin())[0]

    def for_export(self, origin: str):
        """``(the mod as an export writes it, what an export makes of your
        edits to its mod)``: for a patch, with each edit to its mod that can
        be turned into an op of its own at the top of it
        (:mod:`core.sharing`), and None for the second for anything else."""
        shared = self._shared(origin)
        return sharing.view(self.loadout, origin, shared), shared

    def _shared(self, origin: str):
        layer = self.loadout.layer(origin)
        if layer is None or not layer.patches:
            return None
        from ..core import edits

        if not edits.has_edits(self.loadout, layer.patches, origin):
            return None
        source = self._library_copy_of(layer.patches)
        if source is None:
            return None
        return sharing.work_out(self.loadout, origin, source, self._raw_index())

    def _library_copy_of(self, origin: str):
        """``origin``'s folder in the library's copy, a mod bundled inside
        another included; None for a mod that is not in the library."""
        from ..core import play

        theirs = self.loadout.layer(origin)
        library_dir = self.where.library_dir() if self.where is not None else None
        if theirs is None or not theirs.folder or library_dir is None:
            return None
        link, inside = play.link_of(theirs)
        source = play.library_copy(link, library.links(library_dir), library_dir)
        if source is None:
            return None
        return source / inside if inside else source

    def _for_writing(self, origins):
        """``(views, lines)``: each of ``origins`` that an export writes
        otherwise than :func:`generator.for_mod` does, by origin, for
        :func:`core.build.write`, and a line to say for each patch with
        edits of yours an export leaves out."""
        views, lines = {}, []
        for origin in origins:
            exported, shared = self.for_export(origin)
            if shared is None:
                continue
            views[origin] = exported
            lines.extend(self._left_out_lines(shared))
        return views, lines

    def _left_out_lines(self, shared) -> list:
        theirs = self.loadout.layer(shared.origin)
        name = theirs.label if theirs is not None else shared.origin
        if shared.trouble:
            return [tr("Your edits to %s could not be worked out, so none went into the export.", name)]
        if not shared.left:
            return []
        return [trn(
            "%(n)s of your edits to %(mod)s could not go into the export. "
            "The preview on the Export tab lists it, with why.",
            "%(n)s of your edits to %(mod)s could not go into the export. "
            "The preview on the Export tab lists them, each with why.",
            len(shared.left), mod=name,
        )]

    def _reload_mods(self) -> None:
        """Keep the mod list in step with what is loaded, without disturbing it."""
        origins = self.pickable()
        multi = len(origins) > 1
        self.mod_row.setVisible(multi)
        if not multi:
            return
        wanted = self.mod_box.currentData()
        # Last, under a line: it is not one more mod.
        offered = origins + ([None, ALL_YOURS] if self._offers_all_yours() else [])
        listed = [self.mod_box.itemData(i) for i in range(self.mod_box.count())]
        if listed == offered:
            return
        self._loading_mods = True
        try:
            self.mod_box.clear()
            for origin in origins:
                label = self.loadout.label_of(origin) or "My Anno Mod"
                if is_own_mod(origin):
                    label = tr("%s  (yours)", label)
                self.mod_box.addItem(label, origin)
            if ALL_YOURS in offered:
                self.mod_box.insertSeparator(self.mod_box.count())
                self.mod_box.addItem(tr("All the mods you changed"), ALL_YOURS)
            # Default to your own mod: that is the one the editor writes to,
            # and having the preview quietly be about somebody else's is the
            # whole confusion this is here to avoid.
            if wanted not in offered or wanted is None:
                wanted = "" if "" in origins else origins[-1]
            self.mod_box.setCurrentIndex(self.mod_box.findData(wanted))
        finally:
            self._loading_mods = False

    def _on_mod_changed(self) -> None:
        if not self._loading_mods:
            self.refresh()

    def refresh(self) -> None:
        problems = []
        self._reload_mods()
        every = self.exporting_all_yours()
        # The column is one mod's -- its details, its page on mod.io, its
        # checks -- so it goes while the box says several.
        self.left_scroll.setVisible(not every)
        if every:
            # Translators: an entry of the Export menu, with every mod you
            # changed picked: each of them written as a folder.
            self.folder_action.setText(tr("Folders"))
        else:
            self.folder_action.setText(tr("Folder"))
        self._refresh_game_row()
        self._refresh_modio()
        if every:
            self._show_all_yours()
            return
        exported, shared = self.for_export(self.current_origin())
        self._exported = (self.current_origin(), exported, shared)

        # The mod being previewed, not the whole stack. This page is about one
        # mod -- the preview, the details, the export button -- and re-checking
        # six thousand changes from sixty other mods to draw it was seconds of
        # work for an answer about somebody else's mods.
        if self.index is not None and exported.active_count:
            self.check_summary.setText(trn(
                "Checking %(n)s change against the base file…",
                "Checking %(n)s changes against the base file…",
                exported.active_count,
            ))
            # So the page says what it is doing before it goes quiet: switching
            # to this tab is what starts the check, and it used to hang with the
            # last mod's results still on screen.
            QGuiApplication.processEvents()
        results = generator.verify_changes(self.index, exported) if self.index else []
        for result in results:
            if not result.ok:
                problems.append(("error", result.change, result.message))
        for change in exported.active():
            if change.positional:
                problems.append(
                    (
                        "warn",
                        change,
                        tr("positional path, may break if a game patch reorders this list"),
                    )
                )

        # Copied and saved as one file, which stands on its own; written to
        # a folder as the mod's files.  The preview is of the folder.
        self._xml_text = build.modops_xml(exported)
        self._as_files = build.as_files(exported)
        self.preview.setPlainText((self._as_files.text + "".join(
            "\n<!-- %s -->\n%s" % (name, text)
            for name, text in self._as_files.files.items()
        ) if self._as_files.files else self._xml_text) + self._edits_block(self.current_origin(), shared))
        self._refresh_destination()
        game = games.get(self.loadout.game)
        # Whose mod this is, before where it goes: with a folder loaded the
        # preview is just as likely to be about somebody else's work as about
        # yours, and the file path is the same either way.
        where = game.asset_relpath.as_posix()
        self._show_details_for(self.current_origin())
        self.preview_label.setFullText(
            tr("Preview of your mod: %s", where) if self.previewing_yours()
            else tr("Preview of %(mod)s: %(path)s", mod=exported.own.name, path=where)
        )
        refused = self.mod_id_problem(self.current_origin())
        self._render_validation(problems, len(results), exported, refused)

        # A mod with every change switched off has nothing to write -- unless
        # it carries ops that go out as themselves, which have no switch.
        has_changes = generator.writes_anything(exported)
        self.copy_action.setEnabled(has_changes)
        self.save_action.setEnabled(has_changes)
        # The folder and the zip write the modinfo, so a Mod ID the game
        # would refuse shuts those two and not the XML.
        self.folder_action.setEnabled(has_changes and not refused[0])
        self.zip_action.setEnabled(has_changes and not refused[0])
        self._show_verdict()
        self._check_timer.start()

    def _show_all_yours(self) -> None:
        """The preview with every mod you changed picked: which folders the
        Export button writes, and where. Each mod's own XML is one pick away,
        and as many files end to end would be read by nobody."""
        origins = self.mine_to_export()
        self._exported = None
        self._xml_text = ""
        # Where, the line under the preview says: it moves with Change….
        lines = [trn(
            "Export writes %(n)s mod folder:",
            "Export writes %(n)s mod folders:",
            len(origins),
        ), ""]
        lines += ["    %s" % generator.export_folder_for(self.loadout, origin) for origin in origins]
        lines += ["", tr(
            "Every other mod stays as its author shipped it. Pick one mod "
            "above to see its preview, its details and its checks."
        )]
        self.preview.setPlainText("\n".join(lines))
        self.preview_label.setFullText(tr("The mods you changed"))
        self._refresh_destination()
        # One file and one zip are one mod's; the folders are the way out.
        self.copy_action.setEnabled(False)
        self.save_action.setEnabled(False)
        self.zip_action.setEnabled(False)
        self.folder_action.setEnabled(bool(origins))

    def export_chosen(self):
        """What the Export button does: the mod on the preview, or every mod
        you changed where the box says so."""
        if self.exporting_all_yours():
            return self.export_mine()
        return self.export_mod()

    # -- the game's own loader, as a second opinion -------------------------
    def set_loader_finder(self, finder) -> None:
        """How to find the game's loader: called with no arguments, it gives
        the one the extension ships for this loadout's game, or None."""
        self._loader_finder = finder
        self._verdicts.clear()
        self._check_timer.start()

    def set_loader_update(self, text: str) -> None:
        """Say, with a check the loader could not make, that a newer
        extension is out -- the first thing to try when it crashes."""
        self._loader_update = text or ""
        self._show_verdict()

    def _edits_block(self, origin: str, shared) -> str:
        """For a patch: your edits to its mod's own ModOps.  At Play they are
        written into that mod's generated copy, and the patch goes without
        them.  An export of the patch has each that can be as an op of its
        own, at the top of the patch above, and leaves the rest out: those
        are listed, each with why, their op and yours
        (docs/proposals/patches-proposal.md, section 9).  "" for anything
        else, and for a patch whose mod holds no edits of yours."""
        from ..core import edits, splicing

        layer = self.loadout.layer(origin)
        if layer is None or not layer.patches or not edits.has_edits(
            self.loadout, layer.patches, origin,
        ):
            return ""
        theirs = self.loadout.layer(layer.patches)
        if theirs is None or not theirs.folder:
            return ""
        name = theirs.label
        if shared is None:
            return "\n\n" + splicing.comment_block(tr(
                "Your edits to %s's own ModOps: that mod is not in the library, so "
                "Play writes them into its own folder.", name))
        if shared.trouble:
            return "\n\n" + splicing.comment_block(tr(
                "Your edits to %(mod)s's own ModOps could not be shown: %(why)s.",
                mod=name, why=shared.trouble))
        out = ["\n\n" + splicing.comment_block(tr(
            "Your edits to %s's own ModOps. At Play they are written into its "
            "generated copy, and this patch goes without them.", name))]
        turned = sum(len(hunk["ops"]) for hunk in shared.turned)
        if turned:
            out.append("\n" + splicing.comment_block(trn(
                "In an export, %(n)s of them is an op of this patch's own, at the top "
                "of it, and runs after %(mod)s.",
                "In an export, %(n)s of them are ops of this patch's own, at the top "
                "of it, and run after %(mod)s.",
                turned, mod=name)))
        if shared.left:
            out.append("\n" + splicing.comment_block(trn(
                "%(n)s is left out of an export:",
                "%(n)s are left out of an export:",
                len(shared.left))))
        for hunk in shared.left:
            out.append("\n\n" + splicing.comment_block(tr(
                "%(file)s, line %(line)s. %(why)s", file=hunk["file"],
                line=number(hunk["line"]), why=tr(hunk["why"]))))
            for text in hunk["skipped"]:
                out.append("\n" + splicing.comment_block(tr("theirs, switched off with Skip at Play")))
                out.append("\n" + text)
            for text in hunk["inserted"]:
                out.append("\n" + splicing.comment_block(tr("yours, after it at Play")))
                out.append("\n" + text)
        return "".join(out)

    def _raw_index(self):
        """The index itself: the panel holds the workspace built on it."""
        return getattr(self.index, "index", self.index)

    @staticmethod
    def _key(job) -> tuple:
        """What a verdict is about: the mod as it would be written, and what
        it stands on. Asked again only when one of them moves."""
        import hashlib

        written = hashlib.sha1(job.xml.encode("utf-8")).hexdigest()
        return (job.origin, written, tuple(name for name, _folder in job.stack),
                len(job.studio.changes))

    def _job(self, origin: str, exporting: bool = False):
        """What a check of ``origin`` hands the loader: the mod as Play writes
        it, which is what Play asks about -- or with ``exporting``, for the
        tab's own check, as an export writes it.  For a patch whose edits to
        its mod an export carries, that is the patch with them, on top of
        their mod as its author shipped it, where Play's is the patch
        without them on top of their mod's generated copy."""
        from ..core import loadercheck

        view, folders, shipped = None, None, None
        if exporting:
            exported, shared = self._exported_now(origin)
            source = (self._library_copy_of(shared.origin)
                      if shared is not None and shared.turned and shared.shipped is not None else None)
            if source is not None:
                view, folders, shipped = exported, {shared.origin: source}, {shared.origin: shared.shipped}
        job = loadercheck.prepare(self.loadout, origin, view=view, folders=folders, shipped=shipped,
                                 index=self._raw_index())
        job.xml = generator.build_modops_xml(job.view) if job.writes else ""
        return job

    def _exported_now(self, origin: str):
        """:meth:`for_export`, as the last refresh worked it out where that
        was of ``origin``: the check that follows a refresh asks again."""
        if self._exported is not None and self._exported[0] == origin:
            return self._exported[1], self._exported[2]
        return self.for_export(origin)

    def loader_job(self, origin: str):
        """What a check of ``origin`` hands the loader, and the key a verdict
        on it is kept under: ``(job, key)``."""
        job = self._job(origin)
        return job, self._key(job)

    def fresh_loader_verdict(self, origin: str, key=None):
        """The verdict for ``origin`` if it is about the mod as it is now:
        as ``key`` says it is, when the caller has worked that out."""
        held = self._verdicts.get(origin)
        if held is None or self.index is None:
            return None
        if key is None:
            key = self._key(self._job(origin))
        return held[1] if held[0] == key else None

    def keep_loader_verdict(self, key, verdict) -> None:
        """Keep what the loader said of a mod as it is now, asked by Play:
        the tab shows it, and does not ask again.

        Only where Play's job is the tab's: for a patch whose edits to its
        mod an export carries, Play asks about the patch without them, and
        the tab's line is about the patch as it is exported."""
        if key != self._key(self._job(key[0], exporting=True)):
            return
        self._verdicts[key[0]] = (key, verdict)
        self._show_verdict()

    def loader_check_running(self):
        """The key the check running now was asked under, or None."""
        return None if self._checking is None else self._checking.key

    @contextlib.contextmanager
    def checks_held(self):
        """No check of the tab's own starts by itself while this is open:
        Play is asking the loader about the same mods, and two runs of it
        at once would ask it twice."""
        waiting = self._check_timer.isActive()
        self._check_timer.stop()
        self._held = True
        try:
            yield
        finally:
            self._held = False
            if waiting or self._check_again:
                self._check_again = False
                self._check_timer.start()

    def check_with_loader(self, force: bool = False) -> None:
        """Ask the game's loader about the mod on the preview, in the
        background -- unless it has already been asked about it as it is.

        Unasked, a check that would run more than
        :data:`core.loadercheck.AUTOMATIC_LIMIT` mods is not started: the line
        says how many, and Ask the loader again (``force``) runs it."""
        from ..core import loadercheck
        from .workers import LoaderCheckWorker

        if self.index is None or self._loader_finder is None:
            self._show_verdict()
            return
        if self.exporting_all_yours():
            # The check is of one mod, and the line it fills is out of view.
            return
        if self._checking is not None or (self._held and not force):
            self._check_again = True
            return
        origin = self.current_origin()
        job = self._job(origin, exporting=True)
        key = self._key(job)
        held = self._verdicts.get(origin)
        if held is not None and held[0] == key and not force:
            return
        loader = self._loader_finder()
        if loader is None:
            # Nothing to ask, so nothing to wait for: said at once.
            self._on_verdict(key, loadercheck.run(job, self._raw_index(), None))
            return
        if not force and job.writes and not loadercheck.runs_by_itself(job):
            # Kept under its key like a verdict, so it is not worked out
            # again until the mod or what it stands on moves.
            self._on_verdict(key, loadercheck.not_run(job, loader, loadercheck.too_many(job)))
            return
        worker = LoaderCheckWorker(key, job, self._raw_index(), loader, parent=self)
        worker.succeeded.connect(self._on_verdict)
        worker.finished.connect(worker.deleteLater)
        self._checking = worker
        if self.background is not None:
            self.background.begin(
                "loader", tr("Asking the game's loader about the export…"), worker
            )
        if loader is not None and job.writes:
            self.loader_label.setText(tr("Asking the game's loader (%s)…", tr_stored(loader.said)))
        worker.start()

    def stop_loader_check(self) -> None:
        """Stop asking: the window is closing."""
        self._check_timer.stop()
        worker, self._checking = self._checking, None
        if worker is not None:
            worker.requestInterruption()
            worker.wait(30000)

    def forget_loader_verdicts(self) -> None:
        """Let go of what the game's loader said: another loadout is open.

        Kept by mod, and your own mod is ``""`` in every loadout, so after
        closing one loadout and opening a loadout the line went on saying
        "agrees on all 7 assets Run Two Renamed changes" over the loadout's
        one-change mod (H2-10).  A check still running was
        asked of the loadout before: it is asked to stop, and what it says
        when it ends is dropped and the new loadout asked instead.
        """
        self._check_timer.stop()
        self._verdicts.clear()
        if self._checking is not None:
            self._checking.requestInterruption()
            self._drop_next = True
            self._check_again = True
        self.loader_label.setText("")
        self.loader_label.setToolTip("")
        self._show_verdict()

    def _on_verdict(self, key, verdict) -> None:
        self._checking = None
        if self._drop_next:
            # Only one check runs at a time, so this is the one that was
            # running when the loadout before gave way.
            self._drop_next = False
            if self._check_again:
                self._check_again = False
                self._check_timer.start()
            return
        self._verdicts[key[0]] = (key, verdict)
        self._show_verdict()
        if self._check_again:
            self._check_again = False
            self._check_timer.start()

    def _show_verdict(self) -> None:
        from ..core import loadercheck

        held = self._verdicts.get(self.current_origin())
        verdict = held[1] if held is not None else None
        self._show_loader_rows(verdict)
        self._show_summary(verdict)
        if verdict is None:
            if self._loader_finder is None:
                self.loader_label.setText("")
            self.loader_button.setVisible(False)
            return
        said, details = verdict.describe(), verdict.details()
        if verdict.state == loadercheck.NOT_RUN:
            # The button beside the line.
            said = said + " " + tr("“Ask the loader again” runs it.")
            details = [said]
        if self._loader_update and verdict.state in (loadercheck.DIFFERS, loadercheck.FAILED):
            # A crash, or a reading the loader has moved on from, may be the
            # extension's copy trailing the game.
            if verdict.state == loadercheck.FAILED:
                said = said.rstrip(".") + ". " + self._loader_update
            details = details + ["", self._loader_update]
        self.loader_label.setText(said)
        self.loader_label.setToolTip("\n".join(details)[:6000])
        self.loader_label.setObjectName(
            "Warn" if verdict.state in (loadercheck.DIFFERS, loadercheck.FAILED) else "StepHint"
        )
        # repolish, so the label measures itself again and the glyph gets its
        # room. Polishing alone left the glyph drawn over the first letters.
        theme.repolish(self.loader_label)
        self.loader_button.setVisible(verdict.state not in (loadercheck.UNAVAILABLE, loadercheck.NOTHING))

    def _show_summary(self, verdict) -> None:
        """The summary, with what the loader found beside the Studio's own.

        "All changes verified" with a green tick over a list of rows where
        the loader differs read as two verdicts. Verified against the base
        file is still true, so the sentence stays, and the loader's count
        follows it under a warning."""
        said, name, verified = self._summary_own
        if verified and verdict is not None and verdict.state == "differs":
            said = "%s %s" % (said, trn(
                "The game's loader reads %(n)s asset differently (see below).",
                "The game's loader reads %(n)s assets differently (see below).",
                verdict.differing_count,
            ))
            name = "Warn"
        self.check_summary.setText(said)
        if self.check_summary.objectName() != name:
            self.check_summary.setObjectName(name)
        # So the objectName change takes effect.
        theme.repolish(self.check_summary)

    def _show_loader_rows(self, verdict) -> None:
        """Each asset the loader sees differently, as a Validation row that
        opens the change behind it.

        The line under the list says how many; a diff in its tooltip says how,
        but not what to do about it. The row names the fields where the two
        part and the mod's change there, and double-clicking it opens that
        change -- the first, when several reach the same fields."""
        for item in self._loader_rows:
            row = self.check_list.row(item)
            if row >= 0:
                self.check_list.takeItem(row)
        self._loader_rows = []
        if verdict is None or verdict.state != "differs":
            self.check_list.setVisible(self.check_list.count() > 0)
            return
        for found in verdict.differing:
            changes = found.get("changes") or []
            fields = ", ".join(found.get("fields") or []) or tr("the asset")
            listed = "; ".join(change["said"] for change in changes[:3])
            if len(changes) > 3:
                said = trn("The game's loader reads %(fields)s differently. The changes there: "
                           "%(changes)s and %(n)s more.",
                           "The game's loader reads %(fields)s differently. The changes there: "
                           "%(changes)s and %(n)s more.", len(changes) - 3, fields=fields, changes=listed)
            elif changes:
                said = trn("The game's loader reads %(fields)s differently. The change there: %(changes)s.",
                           "The game's loader reads %(fields)s differently. The changes there: %(changes)s.",
                           len(changes), fields=fields, changes=listed)
            else:
                said = tr("The game's loader reads %s differently, and none of this mod's "
                          "changes is to it: a copy or a search reached it.", fields)
            item = QListWidgetItem(tr("%(asset)s  ·  the game's loader\n    %(said)s",
                                      asset=found.get("label") or found["guid"], said=said))
            theme.mark(item, "warn")
            item.setToolTip("\n".join(["GUID %s" % found["guid"], ""] + list(found.get("diff") or [])[:30]
                                      + ([""] + [tr("Double-click or press Enter to open this change.")] if changes else [])))
            first = changes[0] if changes else {"guid": "", "path": ""}
            item.setData(Qt.ItemDataRole.UserRole, (first["guid"], first["path"], said, ""))
            self.check_list.addItem(item)
            self._loader_rows.append(item)
        if verdict.differing_count > len(verdict.differing):
            item = QListWidgetItem(trn("    …and %(n)s more the game's loader reads differently.",
                                       "    …and %(n)s more the game's loader reads differently.",
                                       verdict.differing_count - len(verdict.differing)))
            theme.mark(item, "warn")
            item.setData(Qt.ItemDataRole.UserRole, ("", "", "", ""))
            self.check_list.addItem(item)
            self._loader_rows.append(item)
        self.check_list.setVisible(self.check_list.count() > 0)

    # -- a Mod ID the game would refuse ------------------------------------
    def mod_id_problem(self, origin: str) -> tuple:
        """Why the game would refuse the Mod ID an export of ``origin`` writes.

        ``("", "")`` when it would not, and otherwise the reason and the ID to
        offer instead -- the mod's name as a slug in the shape the loader
        takes.  Anno 117 checks the ID and refuses the whole mod; Anno 1800
        never has, so there the answer is always nothing.

        Asked whether or not the mod has changes yet: an ID typed into a new
        mod went unremarked under "No changes to export yet" and was refused
        only once the first change landed (H2-30).  A blank one is filled in
        from the name in the shape the game takes, so it never is.
        """
        exported = generator.for_mod(self.loadout, origin)
        game = games.get(self.loadout.game)
        why = game.mod_id_problem(modinfo.written_mod_id(exported))
        if not why:
            return ("", "")
        return (why, game.usable_mod_id(exported.own.mod_id or exported.own.name))

    def _refuse_bad_mod_ids(self, origins, notify: bool = True) -> bool:
        """Whether any of ``origins`` has a Mod ID the game would refuse.

        Said in a box rather than exported anyway: a mod written with an ID
        the loader rejects is a mod that does nothing in game, and the log
        that says why is not one most players read.  The Validation list
        says the same about the mod on the preview, with the fix beside it.
        """
        refused = []
        for origin in origins:
            why, instead = self.mod_id_problem(origin)
            if why:
                exported = generator.for_mod(self.loadout, origin)
                said = {
                    "id": modinfo.written_mod_id(exported),
                    "why": why,
                    "instead": instead,
                }
                name = exported.own.name or origin
                refused.append(
                    tr("%(mod)s, Mod ID “%(id)s”: %(why)s Try “%(instead)s”.",
                       mod=name, **said)
                    if name
                    else tr("your mod, Mod ID “%(id)s”: %(why)s Try “%(instead)s”.",
                            **said)
                )
        if refused and notify:
            QMessageBox.warning(
                self,
                tr("The game would refuse this Mod ID"),
                tr(
                    "Not exported, because %(game)s would not load it:\n\n%(mods)s\n\n"
                    "Change the Mod ID in the details box, or switch the mod off "
                    "to leave it out.",
                    game=games.get(self.loadout.game).label,
                    mods="\n".join(refused),
                ),
            )
        return bool(refused)

    def _render_validation(
        self, problems, checked: int, shown=None, refused=("", "")
    ) -> None:
        """What the check found, for the mod the page is about.

        ``refused`` is why the game would turn the mod's ID away and what to
        use instead, from :meth:`mod_id_problem`; blank when it would not.
        """
        shown = shown if shown is not None else self.loadout
        self.check_list.clear()
        # The loader's rows went with the rest, and Qt has deleted them: asked
        # for its row, one of them raised, on every refresh under a difference.
        self._loader_rows = []
        errors = [p for p in problems if p[0] == "error"]
        warns = [p for p in problems if p[0] == "warn"]
        why, instead = refused

        if why:
            # Ahead of every other verdict, because it is the one that stops
            # the export: a change that would not apply still ships, a Mod
            # ID the loader rejects means the whole mod never loads.
            game = games.get(self.loadout.game).label
            self.check_summary.setText(
                trn(
                    "Cannot export: %(game)s would refuse this mod's ID (see "
                    "below). %(n)s of %(total)s changes would not apply "
                    "either.",
                    "Cannot export: %(game)s would refuse this mod's ID (see "
                    "below). %(n)s of %(total)s changes would not apply "
                    "either.",
                    len(errors), game=game, total=number(checked),
                )
                if errors
                else tr("Cannot export: %s would refuse this mod's ID (see below).", game)
            )
            self.check_summary.setObjectName("Error")
        elif not len(shown) and not shown.unread and getattr(shown, "scripts", None):
            # A mod that is only its Lua: nothing here to check against the
            # base file, and the modinfo is what tells the game about it.
            self.check_summary.setText(tr(
                "No changes to check. This mod is its scripts: the modinfo "
                "lists them, and the export copies them in."
            ))
            self.check_summary.setObjectName("SubHeading")
        elif not shown.active_count and generator.writes_anything(shown):
            # Nothing of the Studio's own writing to check, but not nothing to
            # write: what it kept as written goes out as it came -- or all of
            # it is switched off, and goes out as comments.
            self.check_summary.setText(tr(
                "No changes to check. Everything in this mod is switched off, "
                "and is exported as comments the game does not read."
            ) if generator.writes_only_comments(shown) else trn(
                "No changes to check. This mod is exported as it was "
                "written: %(n)s ModOp the Studio could not read, and the "
                "rest kept as the ModOps they are.",
                "No changes to check. This mod is exported as it was "
                "written: %(n)s ModOps the Studio could not read, and the "
                "rest kept as the ModOps they are.",
                len(shown.not_read()),
            ))
            self.check_summary.setObjectName("SubHeading")
        elif not len(shown):
            self.check_summary.setText(tr("No changes to export yet."))
            self.check_summary.setObjectName("SubHeading")
        elif not shown.active_count:
            self.check_summary.setText(trn(
                "%(n)s change is switched off, so this mod does nothing.",
                "All %(n)s changes are switched off, so this mod does nothing.",
                len(shown),
            ))
            self.check_summary.setObjectName("Warn")
        elif self.index is None:
            self.check_summary.setText(trn(
                "%(n)s change. Load an assets.xml to verify it against the base "
                "game.",
                "%(n)s changes. Load an assets.xml to verify them against the "
                "base game.",
                shown.active_count,
            ))
            self.check_summary.setObjectName("Warn")
        elif errors:
            self.check_summary.setText(trn(
                "%(n)s of %(total)s changes would not apply (see below).",
                "%(n)s of %(total)s changes would not apply (see below).",
                len(errors), total=number(checked),
            ))
            self.check_summary.setObjectName("Error")
            self._say_what_is_off(shown)
        elif warns:
            self.check_summary.setText(trn(
                "All %(total)s changes verified against the base file. %(n)s "
                "uses a positional path.",
                "All %(total)s changes verified against the base file. %(n)s "
                "use a positional path.",
                len(warns), total=number(checked),
            ))
            self.check_summary.setObjectName("Warn")
            self._say_what_is_off(shown)
        else:
            self.check_summary.setText(trn(
                "%(n)s change verified against the base file.",
                "All %(n)s changes verified against the base file.",
                checked,
            ))
            self.check_summary.setObjectName("Ok")
            self._say_what_is_off(shown)
        self._summary_own = (
            self.check_summary.text(),
            self.check_summary.objectName(),
            not why and bool(shown.active_count) and self.index is not None and not errors,
        )
        held = self._verdicts.get(self.current_origin())
        self._show_summary(held[1] if held is not None else None)

        if why:
            # Not a change, so the row is about the field instead: it names
            # the file the ID goes into where the others name an asset, and
            # opening it takes the offered ID rather than jumping anywhere.
            item = QListWidgetItem(tr(
                "Mod ID  ·  modinfo.json\n    %(why)s Use “%(instead)s” instead.",
                why=why, instead=instead,
            ))
            theme.mark(item, "error")
            item.setToolTip(tr(
                "Double-click or press Enter to use “%s” as the Mod ID.", instead
            ))
            item.setData(Qt.ItemDataRole.UserRole, ("", "", why, instead))
            self.check_list.addItem(item)
        for kind, change, message in errors + warns:
            item = QListWidgetItem(
                "%s  ·  %s\n    %s" % (tr_stored(change.asset_label), change.tag, message)
            )
            theme.mark(item, "error" if kind == "error" else "warn")
            item.setToolTip(
                tr("%s\n\nDouble-click or press Enter to open this change.", change.path)
            )
            # What the row is about, so it can be opened and quoted.
            item.setData(
                Qt.ItemDataRole.UserRole, (change.guid, change.path, message, "")
            )
            self.check_list.addItem(item)
        self.check_list.setVisible(self.check_list.count() > 0)

    def _say_what_is_off(self, shown) -> None:
        """After a count of the changes checked, the ones that were not.

        Only what is switched on is checked, so with one of 22 off the line
        said "All 21 changes verified" beside a list that said 22.  The rest
        of the count is said, and why it was left out.
        """
        off = len(shown) - shown.active_count
        if off <= 0:
            return
        # Translators: %(status)s is the sentence saying how many changes
        # were checked; the count is of the others.
        self.check_summary.setText(trn(
            "%(status)s %(n)s more is switched off, so it was not checked.",
            "%(status)s %(n)s more are switched off, so they were not checked.",
            off, status=self.check_summary.text(),
        ))

    def take_mod_id(self, instead: str) -> None:
        """Put the offered Mod ID into the field, as typing it would."""
        if not instead or not self.details_are_yours(self.current_origin()):
            return
        self.modid_edit.setText(instead)

    # -- getting from a problem to the change it is about -------------------
    def _open_problem(self, item) -> None:
        found = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if found and found[0]:
            self.changeActivated.emit(found[0], found[1])
        elif found and found[3]:
            self.take_mod_id(found[3])

    def _problem_menu(self, point) -> None:
        item = self.check_list.itemAt(point)
        if item is None:
            return
        self.check_list.setCurrentItem(item)
        menu = self.menu_for(item)
        menu.exec(self.check_list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, item) -> QMenu:
        """What can be done about one validation row.

        Opening it is the whole point of reading the list; copying the path is
        for the other case, where the answer is not in the Studio at all and
        the row has to be quoted somewhere -- a mod page, an issue, a message
        to whoever wrote the mod it came from.
        """
        guid, path, _message, instead = item.data(Qt.ItemDataRole.UserRole) or (
            "",
            "",
            "",
            "",
        )
        menu = QMenu(self)
        if instead:
            # The Mod ID row: nothing to open, but something to take.
            take = menu.addAction(
                tr("Use “%s” as the Mod ID", instead), lambda: self.take_mod_id(instead)
            )
            take.setEnabled(self.details_are_yours(self.current_origin()))
        else:
            open_it = menu.addAction(
                tr("Open this change in the asset editor"),
                lambda: self.changeActivated.emit(guid, path),
            )
            open_it.setEnabled(bool(guid))
        menu.addSeparator()
        menu.addAction(
            tr("Copy the path"), lambda: QGuiApplication.clipboard().setText(path)
        )
        menu.addAction(
            tr("Copy what it says"),
            lambda: QGuiApplication.clipboard().setText(
                "%s\n%s" % (item.text().replace("\n    ", " — "), path)
            ),
        )
        return menu

    # -- before overwriting anything ----------------------------------------
    def back_up(self, origins, label: str = "", parent=None):
        """Copy what this export is about to write over, before it does: see
        :func:`core.build.back_up`."""
        parent = Path(parent) if parent is not None else self.export_dir()
        return build.back_up(self.loadout, origins, parent, into=self.backup_into, label=label,
                            folder_of=generator.export_folder_for)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._xml_text)
        # The menu has closed by now: the button it opened from says so.
        flash_button_feedback(self.export_button)

    def set_default_output_dir(self, path) -> None:
        """Where exports go unless somewhere else is chosen.  Never a mods
        folder: installing is Play's."""
        path = Path(path) if path else None
        self._default_output_dir = None if path is not None and build.is_mods_folder(path) else path
        self._refresh_destination()

    def _library_or_none(self):
        """The game's folder in the Studio's library, where a mod written into
        a mods folder goes: see :func:`core.build.destination`.  The window's
        answer; None for a panel on its own."""
        return self.where.library_dir() if self.where is not None else None

    def export_dir(self) -> Path:
        """Where exports are written: chosen, else under Documents -- never a
        mods folder, which is Play's."""
        if self._chosen_output_dir is not None:
            return self._chosen_output_dir
        if self._default_output_dir is not None:
            return self._default_output_dir
        from ..core import places

        return places.exports_dir()

    def overwrite_warning(self, origin=None) -> str:
        """What replacing an existing folder will actually replace: each file
        of it the export writes again, a line for each kind.

        It used to say "assets.xml and modinfo.json" over an export that also
        rewrote the texts files and a file of ModOps the patch includes
        (H2-11).  Read off :func:`core.build.writes`, which the backup takes
        its copy by, so the two cannot name different files.  What the export
        copies in from the folder the mod was read out of is the last line:
        written over too, but from the mod itself, which still has it -- and
        not where the mod is written back over its own folder, where those
        files already are and nothing is copied.
        """
        if origin is None:
            origin = self.current_origin()
        target = self.export_target(origin)

        def there(paths) -> list:
            return [path for path in paths if (target / path).is_file()]

        written = build.writes(self.loadout, origin, target)
        lines = []
        included = there(written.included)
        if there([written.patch]) and included:
            lines.append(trn(
                "%(patch)s and the %(n)s file of ModOps it includes",
                "%(patch)s and the %(n)s files of ModOps it includes",
                len(included), patch=written.patch.name,
            ))
        elif there([written.patch]):
            lines.append(written.patch.name)
        elif included:
            lines.append(trn("%(n)s file of ModOps", "%(n)s files of ModOps", len(included)))
        if there(written.texts):
            lines.append(trn("%(n)s texts file", "%(n)s texts files", len(there(written.texts))))
        if there([written.modinfo]):
            lines.append(written.modinfo.name)
        if there(written.scripts):
            lines.append(trn("%(n)s Lua script", "%(n)s Lua scripts", len(there(written.scripts))))
        source = build.carry_folder(self.loadout, origin)
        if source is not None and source.resolve() != target.resolve():
            # Less what is written again after it is copied: the texts files
            # and the files of ModOps, said above.
            listed = {path.as_posix().lower() for path in written.all}
            carried = [path for path in there(generator.carried_files(source, self.loadout.game))
                       if path.as_posix().lower() not in listed]
            if carried:
                lines.append(trn(
                    "%(n)s other file, copied in again from the mod it was read from",
                    "%(n)s other files, copied in again from the mod it was read from",
                    len(carried),
                ))
        if not lines:
            return tr("The export writes over none of the files in it.")
        return "\n".join([tr("Replacing it writes over these files in it:")]
                         + ["    %s" % line for line in lines])

    def export_target(self, origin=None) -> Path:
        """The mod folder itself, name included."""
        if origin is None:
            origin = self.current_origin()
        return self.export_dir() / generator.export_folder_for(self.loadout, origin)

    def _refresh_destination(self) -> None:
        # Several folders go in one place, each under its own name.
        target = self.export_dir() if self.exporting_all_yours() else self.export_target()
        self.destination_label.setFullText(str(target))
        self.destination_label.setToolTip(str(target))

    def _choose_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Where should mods be exported?"), str(self.export_dir())
        )
        if chosen:
            self.choose_destination(Path(chosen))

    def choose_destination(self, chosen: Path) -> bool:
        """Export into ``chosen`` from now on -- unless it is a mods folder,
        which is Play's to write: it builds into the library and sets the
        rest to match, and an export dropped beside that is a second copy
        nothing is keeping in step."""
        if build.is_mods_folder(chosen):
            QMessageBox.information(
                self,
                tr("That is a mods folder"),
                # Translators: "In your game" and "Add to loadout" are the
                # Export tab's row and button; "Play" is the Studio's button
                # that builds your mods and starts the game.
                tr("To put your mod in the game you play, use Add to loadout "
                   "in the In your game row: the loadouts you play then have "
                   "it. To test it on its own, press Play.\n"
                   "\n"
                   "Export writes somewhere else, for sharing."),
            )
            return False
        self._chosen_output_dir = Path(chosen)
        self._refresh_destination()
        return True

    def _default_dir(self) -> str:
        return str(self.export_dir())

    def _save_xml(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, tr("Save ModOps XML"), str(Path(self._default_dir()) / "assets.xml"),
            tr("XML files (*.xml)"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self._xml_text, encoding="utf-8")
        except OSError as exc:
            # Said, and not recorded: modExported is what writes an "Exported"
            # checkpoint, and nothing was.
            QMessageBox.critical(
                self, tr("Could not export"), "%s\n\n%s" % (wrappable_path(path), exc)
            )
            return
        QMessageBox.information(self, tr("Saved"), tr("Wrote %s", wrappable_path(path)))
        self.modExported.emit(str(Path(path).parent), None)

    def mine_to_export(self) -> list:
        """The mods here that are yours: see :func:`core.build.mine`."""
        return build.mine(self.loadout)

    def unreadable_in(self, origins) -> int:
        """How many ModOps a rewrite of ``origins`` carries over unread.

        They are written back exactly as they arrived, so nothing is lost --
        but they are the part of the mod the Studio could not follow, so an
        edit of yours that lands near one is not something it could check.
        Worth a line on the confirmation for that reason.

        Counted off the loadout rather than off the layers, because the mod
        being authored here is not one of them.  Origin "" is the loadout
        itself, and carrying on your own published mod is exactly the way to
        end up with unread ops in it -- so counting layers alone answered zero
        for the one mod whose loss its author cannot undo by reinstalling.
        """
        wanted = set(origins)
        return sum(1 for entry in self.loadout.not_read() if entry.origin in wanted)

    # What an export does not wait for: the game's loader, asked about the
    # mod on the Export tab or trying the reading rules. A second opinion on
    # what is written, and nothing written depends on it -- and on a loadout
    # it replays every mod the export's changes reach, which was more than
    # two minutes of waiting for a one-change mod (H2-15). Its line on the
    # tab fills in when it is done. Play leaves the first out too, and waits
    # for the loader's verdicts on its own (LoaderController.loader_verdicts).
    NOT_WAITED_FOR = ("loader", "probe")

    def caught_up(self) -> bool:
        """Wait for what the window is still reading, before writing anything
        out; False where the wait was cancelled."""
        return self.background is None or self.background.wait(self, but=self.NOT_WAITED_FOR)

    def export_mine(self, confirm: bool = True, notify: bool = True):
        """Write out the mods you wrote into, and nothing else."""
        if not self.caught_up():
            return []
        origins = self.mine_to_export()
        if not origins:
            if notify:
                QMessageBox.information(
                    self,
                    tr("No changes of yours to export"),
                    tr("None of the loaded mods has a change of yours in it, "
                       "so everything here is already on disk."),
                )
            return []
        if self._refuse_bad_mod_ids(origins, notify):
            return []
        if confirm:
            # What goes out unread is said.  These are the mods you wrote, so
            # an op of yours that was never read is the likeliest to matter -- and the mod
            # it came from is about to be overwritten by what goes out here.
            lost = self.unreadable_in(origins)
            # A paragraph each, every one a whole sentence of its own.
            said = [trn(
                "Write %(n)s mod folder into:\n"
                "%(where)s\n"
                "\n"
                "This is the mod you have changed. Every other mod stays as "
                "its author shipped it.",
                "Write %(n)s mod folders into:\n"
                "%(where)s\n"
                "\n"
                "These are the mods you have changed: your own mod and your "
                "patches. Every other mod stays as its author shipped it.",
                len(origins), where=wrappable_path(self.export_dir()),
            )]
            if lost:
                said.append(trn(
                    "%(n)s ModOp in them could not be read. It is exported "
                    "as written, so the mod still does what it did. The "
                    "Studio cannot check anything you changed near it.",
                    "%(n)s ModOps in them could not be read. They are "
                    "exported as written, so the mod still does what it did. "
                    "The Studio cannot check anything you changed near them.",
                    lost,
                ))
            answer = ask(
                self,
                tr("Export your changes?"),
                "\n\n".join(said),
                tr("&Export"),
            )
            if not answer:
                return []
        return self._write_out(origins, notify, tr_noop("before exporting your changes"))

    def _write_out(self, origins, notify: bool = True, label: str = ""):
        """Write each of ``origins`` as its own mod folder, backing up first."""
        parent = self.export_dir()
        # Before the first byte goes out, not after: this is what makes the
        # export undoable for somebody who finds a mod behaving differently.
        kept = self.back_up(origins, label=label)
        views, left_out = self._for_writing(origins)
        built = build.write(self.loadout, origins, parent, self._library_or_none(), views=views,
                            folder_of=generator.export_folder_for)
        self._show_build()
        written = built.written
        failed = ["%s: %s" % (name or tr("your mod"), why) for name, why in built.failed]

        if notify:
            said = [trn(
                "Wrote %(n)s mod folder into:\n%(where)s",
                "Wrote %(n)s mod folders into:\n%(where)s",
                len(written), where=wrappable_path(parent),
            )]
            if kept is not None:
                # Translators: "Game \u25b8 Restore mods from a backup\u2026" is a menu
                # path; use the menu's own translated names.
                said.append(tr(
                    "What was there first is saved: %s.\nGame \u25b8 Restore "
                    "mods from a backup\u2026 puts it back.", kept.describe()
                ))
            if failed:
                said.append(tr("Could not write:\n%s", "\n".join(failed)))
            said.extend(left_out)
            QMessageBox.information(self, tr("Mods exported"), "\n\n".join(said))
        if written:
            # Each mod written takes a checkpoint in its project's history,
            # and only those: one that failed is not exported.
            self.modExported.emit(str(parent), list(built.origins))
        return written

    def _mod_to_write(self, notify: bool = True):
        """The mod on the preview, where there is something of it to write
        and nothing in the way, with :meth:`_for_writing`'s views and lines
        for it; None where there is not."""
        if not self.caught_up():
            return None
        origin = self.current_origin()
        exported, shared = self.for_export(origin)
        views = {origin: exported} if shared is not None else {}
        left_out = self._left_out_lines(shared) if shared is not None else []
        if not generator.writes_anything(exported):
            return None
        if self._refuse_bad_mod_ids([origin], notify):
            return None
        return origin, views, left_out

    def export_zip(self, notify: bool = True):
        """The mod on the preview, as a zip in the export destination."""
        writing = self._mod_to_write(notify)
        if writing is None:
            return None
        origin, views, left_out = writing
        parent = self.export_dir()
        built = build.write(self.loadout, [origin], parent, zipped=True, views=views,
                            folder_of=generator.export_folder_for)
        self._show_build()
        if built.failed:
            QMessageBox.critical(self, tr("Could not export"), built.failed[0][1])
            return None
        (archive,) = built.written
        if notify:
            QMessageBox.information(
                self, tr("Mod exported"),
                "\n\n".join([tr("Wrote:\n%s", wrappable_path(archive)), *left_out]),
            )
        self.modExported.emit(str(parent), origin)
        return archive

    def export_mod(self, confirm_overwrite: bool = True, notify: bool = True):
        """Write the mod folder to the destination shown on the panel."""
        self.left_out = []
        writing = self._mod_to_write(notify)
        if writing is None:
            return None
        origin, views, left_out = writing
        target = self.export_target()
        if confirm_overwrite and target.exists():
            answer = ask(
                self,
                tr("Replace the existing mod?"),
                "\n\n".join((
                    tr("%s already exists.", wrappable_path(target)), self.overwrite_warning()
                )),
                tr("&Replace"),
            )
            if not answer:
                return None

        parent = self.export_dir()
        kept = self.back_up([origin], label=tr_noop("before exporting one mod"))
        built = build.write(self.loadout, [origin], parent, self._library_or_none(), views=views,
                            folder_of=generator.export_folder_for)
        self._show_build()
        if built.failed:
            QMessageBox.critical(
                self, tr("Could not export"),
                "%s\n\n%s" % (wrappable_path(target), built.failed[0][1]),
            )
            return None
        (root,) = built.written
        # In the library, where the destination is a mods folder: see
        # :func:`core.build.destination`.
        link_into = parent if library.is_link(root) else None

        written = root / games.get(self.loadout.game).asset_relpath
        said = [tr("Wrote:\n%s", wrappable_path(written))]
        if link_into is not None:
            said.append(tr(
                "It is in the Studio's library, linked into your mods folder, so "
                "the game will pick it up."
            ))
        elif parent.name.lower() == "mods":
            said.append(tr("It is in your mods folder, so the game will pick it up."))
        else:
            # Not "drop it into your mods folder": the way into the game is a
            # loadout, which the library links in (R3-13, hands-on run 3).
            said.append(tr("This folder is a copy to share. To play the mod, put it in a "
                           "loadout with Add to loadout… on this tab."))
        if kept is not None:
            # Translators: "Game \u25b8 Restore mods from a backup\u2026" is a menu
            # path; use the menu's own translated names.
            said.append(tr(
                "What was there first is saved. Game ▸ Restore mods from a "
                "backup… puts it back."
            ))
        # Said after the export where it is asked for, and kept for
        # publishing, which asks for none.
        self.left_out = left_out
        said.extend(left_out)
        if notify:
            QMessageBox.information(self, tr("Mod exported"), "\n\n".join(said))
        self.modExported.emit(str(root), origin)
        return root
