"""Main window: wires the steps together over one shared loadout."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QByteArray,
    QDataStream,
    QEvent,
    QObject,
    QSettings,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import (
    declaring,
    discovery,
    games,
    guidranges,
    inside,
    launch,
    library,
    modfile,
    patching,
    places,
    projects,
    repeats,
    settings,
    stale,
    truth,
    unsubscribe,
)
from ..core.changes import (
    AIM_MAKE,
    AIM_MANAGE,
    APP_NAME,
    Loadout,
    is_own_mod,
    keep_folders_inside,
)
from ..core.i18n import (
    number,
    tr,
    tr_stored,
    trn,
)
from ..core.undo import UndoHistory, held_in, touched
from . import help_tip, settings_dialog
from .about_dialog import AboutDialog
from .background import BackgroundIndicator, BackgroundTasks
from .changes_dock import ChangesPanel
from .conflicts_dialog import ConflictsDialog
from .editor_panel import EditorPanel
from .file_panel import FilePanel
from .folders_dialog import FoldersDialog
from .game_files_controller import GameFilesController
from .library_controller import LibraryController, is_mod_zip
from .live_controller import LiveController
from .loader_controller import LoaderController
from .loadout_files import LoadoutFiles
from .mod_import_dialog import ModImportDialog
from .modio_controller import ModioController
from .mods_folder_dialog import ModsFolderDialog, standing_of
from .navigation import (
    TAB_EDIT,
    TAB_FILE,
    TAB_GENERATE,
    TAB_REFS,
    TAB_SOURCE,
    AssetTrail,
)
from .needs_dialog import NeedsDialog
from .new_asset_dialog import NewAssetDialog
from .output_panel import OutputPanel
from .panel_windows import (
    CHANGES,
    EDITOR,
    REFERENCES,
    TAB_PANELS,
    AwayPage,
    PanelWindow,
    PanelWindows,
)
from .patches_controller import PatchesController
from .patterns_controller import PatternsController
from .preferences import Preferences
from .prompts import (
    FORK_SEPARATE,
    Prompts,
)
from .readings_controller import ReadingsController
from .reference_panel import ReferencePanel
from .shortcuts_dialog import ShortcutsDialog
from .source_panel import SourcePanel
from .start_controller import StartController
from .studio_release_controller import StudioReleaseController
from .where import Where
from .widgets import (
    DropHint,
    add_in_tab_order,
    ask,
    busy_cursor,
    listing,
    named,
    show_in_folder,
    show_rich,
    tell,
    wrappable_path,
)

# What a drag over the window sends, from entering it to being let go.
_DRAG_EVENTS = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)


def studio_title() -> str:
    """The Studio's name as the title bar starts with it: with its version,
    and "(dev)" after that for one run from its source rather than built.
    Which Studio a screenshot or a report came from is then on it."""
    if places.built():
        return "%s %s" % (APP_NAME, __version__)
    # Translators: the Studio's name and version in its title bar, for a
    # Studio run from its source code rather than the built program.
    return tr("%(name)s %(version)s (dev)", name=APP_NAME, version=__version__)


def open_settings() -> QSettings:
    """Where the remembered folders and window geometry live.

    An INI file, named explicitly, rather than whatever the platform calls
    native.  On Windows native means the registry, and nothing can redirect the
    registry -- so a test run writing a remembered path would write it into the
    developer's own settings, and did: a mods folder in a temp directory that no
    longer existed came back as the suggestion for the real one.

    ``QSettings.setDefaultFormat`` looks like the answer and is not; the
    two-argument constructor here still hands back a native-format object.

    The file is :func:`core.places.settings_file`'s to name: beside the
    library, or wherever ``ANNO_STUDIO_SETTINGS`` says.  A script that opens
    a window to look at something runs in its own process, and forgetting to
    name a file of its own writes a temp directory into the developer's real
    settings.  An environment variable cannot be forgotten by the thing it is
    protecting, because it is set from outside it.
    """
    return QSettings(str(places.settings_file()), QSettings.Format.IniFormat)


# The size the window opens at the first time, and the least it goes back to
# from maximised.
OPENING_SIZE = QSize(1400, 900)
# How often the window asks whether the game is open, which greys Play.
GAME_WATCH_MS = 3000
# What a mod imported into the loadout gives its own mod: the details its
# modinfo says, by the names the own mod's record has.
_IMPORTED_DETAILS = ("name", "creator", "description", "category", "version", "mod_id")


class UndoKeys(QObject):
    """Ctrl+Z and Ctrl+Y for the window's Undo and Redo, from a text box
    that has nothing of its own to take back.

    A text box with the focus keeps those keys for its own text, as text
    boxes do -- and kept them with nothing to undo, so they did nothing.
    The focus goes back to such a box when a dialog closes, or after the
    menu's Undo: the Mod changes panel's filter, whose text the panel sets
    and so leaves with no undo of its own, kept Ctrl+Z from the window
    until something else was clicked (H2-26).  The box being typed in
    keeps them while it has typing to take back; an editor open in a list
    keeps them always, as an edit not finished is not one to undo past.

    Watches only the text box with the focus, so it costs nothing while
    anything else has it."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._watched = None
        QApplication.instance().focusChanged.connect(self._focus_moved)

    def _focus_moved(self, _old, new) -> None:
        if self._watched is not None:
            try:
                self._watched.removeEventFilter(self)
            except RuntimeError:  # deleted with whatever it was in
                pass
            self._watched = None
        if (
            isinstance(new, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox))
            and self._answers_for(new.window())
        ):
            new.installEventFilter(self)
            self._watched = new

    def _answers_for(self, window) -> bool:
        """Whether the window's Undo and Redo are answered in ``window``:
        the main window, and a panel's window of its own, which holds the
        same actions.  Only the main window was watched, and the Mod changes
        panel's filter kept Ctrl+Z for itself while the panel was out."""
        return window is self._window or (
            isinstance(window, PanelWindow) and window.parentWidget() is self._window
        )

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.ShortcutOverride:
            return False
        if event.matches(QKeySequence.StandardKey.Undo):
            redo = False
        elif event.matches(QKeySequence.StandardKey.Redo):
            redo = True
        else:
            return False
        if _keeps_undo_keys(watched, redo):
            return False
        # Not taken by the box, so the window's shortcut is.
        event.ignore()
        return True


class StatusLine(QLabel):
    """The status bar's message: cut short at the window's width rather than
    pushing what is running behind the window out of sight, with the whole
    of it in its tooltip.

    A message about something put in a folder -- an export, Play, a
    conversion, an install -- has *Show in folder* after it, there until
    the next message.  The message named the folder and nothing opened it:
    somebody coming back to it afterwards copied the path out of the
    tooltip.  :attr:`row` is what goes in the status bar, the message with
    the link beside it."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        # Its whole width while there is room, and any less once there is
        # not: the link stays beside the text, and the text gives way first.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)
        self.folder: Optional[Path] = None
        self.folder_link = QLabel()
        self.folder_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.folder_link.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.folder_link.linkActivated.connect(lambda _key: self.show_folder())
        self.folder_link.hide()
        self.row = QWidget()
        line = QHBoxLayout(self.row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        line.addWidget(self)
        line.addWidget(self.folder_link)
        line.addStretch(1)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:
        self.say(text)

    def say(self, text: str, folder=None) -> None:
        """Show ``text``, and *Show in folder* for ``folder`` where one is
        named: a folder it opens, or a file it picks out in its folder."""
        super().setText(text)
        self.setToolTip(text)
        self.folder = Path(folder) if folder else None
        self.folder_link.setVisible(self.folder is not None)
        if self.folder is None:
            return
        # Written as it is shown, as the release note's links are.
        show_rich(self.folder_link, '<a href="folder">%s</a>' % html.escape(tr("Show in folder")))
        self.folder_link.setAccessibleName(tr("Show in folder"))
        self.folder_link.setToolTip(str(self.folder))

    def show_folder(self) -> None:
        if self.folder is not None:
            show_in_folder(self.folder)


# How many changed settings a mod read again at other settings names in the
# status bar; more are counted.
NAMED_SETTINGS = 2


def _reread_at(mod: str, offered, chosen: dict, was: dict, changes: int) -> str:
    """What the status bar says of ``mod`` read again at ``chosen``, where
    it was read at ``was`` before: the settings that moved, by name while
    they are few.  Every setting it has, named with its value, made a line
    that ran past the window's width with "Reading 248 mods again…" at its
    end (H2-22)."""
    settable = [option for option in offered.settable if option.name in chosen]
    if not settable:
        return trn("%(mod)s re-read at its own defaults: %(n)s change.",
                   "%(mod)s re-read at its own defaults: %(n)s changes.",
                   changes, mod=mod)
    moved = [
        option for option in settable
        if str(chosen[option.name]) != str(was.get(option.name))
    ] or settable
    if len(moved) > NAMED_SETTINGS:
        return trn(
            "%(mod)s re-read with %(settings)s changed: %(n)s change.",
            "%(mod)s re-read with %(settings)s changed: %(n)s changes.",
            changes, mod=mod,
            settings=trn("%(n)s setting", "%(n)s settings", len(moved)),
        )
    # Each setting as the mod names it, then the value picked for it.
    settings = [
        tr("%(setting)s %(value)s", setting=option.label,
           value=option.label_for(chosen[option.name]))
        for option in moved
    ]
    return trn("%(mod)s re-read at %(settings)s: %(n)s change.",
               "%(mod)s re-read at %(settings)s: %(n)s changes.",
               changes, mod=mod, settings=", ".join(settings))


def _keeps_undo_keys(box, redo: bool) -> bool:
    """Whether a text box with the focus takes Ctrl+Z (or, ``redo``,
    Ctrl+Y) for its own text: while it has something to take back, and an
    editor open in a list always."""
    parent = box.parentWidget()
    if parent is not None and isinstance(parent.parentWidget(), QAbstractItemView):
        return True
    if isinstance(box, QAbstractSpinBox):
        box = box.lineEdit()
        if box is None:
            return False
    if isinstance(box, QLineEdit):
        if box.isReadOnly():
            return False
        return box.isRedoAvailable() if redo else box.isUndoAvailable()
    if box.isReadOnly():
        return False
    document = box.document()
    return document.isRedoAvailable() if redo else document.isUndoAvailable()


def _side_width(state) -> int:
    """The width a saved ``QSplitter.saveState()`` gives the second of two
    widgets, or 0 where it is not such a state.

    Read from the state itself because a splitter that has not been shown
    has not laid that width out yet: its ``sizes()`` are the window's
    default, whatever was restored.
    """
    stream = QDataStream(QByteArray(state))
    stream.setVersion(QDataStream.Version.Qt_5_0)
    if stream.readInt32() != 0xFF:
        return 0
    stream.readInt32()  # the state's version
    if stream.readUInt32() != 2:
        return 0
    stream.readInt32()
    width = stream.readInt32()
    return width if stream.status() == QDataStream.Status.Ok and width > 0 else 0



def _answer_of(dialog) -> Optional[bool]:
    """Show a dialog that reads mods in, and say what came of it: None when
    it came back with something to take in, which is the caller's to do;
    otherwise whether anything landed.

    A detour taken on it is taken here, once it is shut, and what that did
    is the answer -- see ``widgets.add_detours``.  A mod read with every op
    kept as written can be worth taking with no change to show for it: ops
    that come to nothing today.
    """
    shut = dialog.exec()
    if dialog.detour is not None:
        return bool(dialog.detour())
    if shut != int(QDialog.DialogCode.Accepted) or not (dialog.changes or dialog.ops):
        return False
    return None


def _live(action) -> bool:
    """Whether a menu row can be clicked: a separator is enabled, to Qt, and
    a hidden row is not there to click."""
    return action.isEnabled() and action.isVisible() and not action.isSeparator()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = open_settings()
        # The other loadouts that have this loadout's mod, for Play's menu,
        # and what they were worked out for (_played_elsewhere).
        self._elsewhere_key: tuple = ()
        self._elsewhere: list = []
        # Whether the game is open, as the window last asked: Play is greyed
        # while it is (_watch_game).
        self.game_open = False
        # What is running behind the window, for the status bar to say.
        self.background = BackgroundTasks(self)
        # What the Studio remembers about you, and the Settings dialog.
        self.preferences = Preferences(self)
        self.loadout = Loadout()
        # Who you are is a fact about you, not about this piece of work, so it
        # is remembered once and every loadout starts out knowing it.
        self.loadout.own.creator = self.preferences.creator_name()
        self.preferences.hand_over(self.loadout)
        self.index = None
        self.workspace = None
        # Where this loadout lives. Decided as soon as there is a loadout --
        # picking a way of working is picking one -- rather than left open until
        # somebody presses Save, so the window can always say what space you are
        # in. ``on_disk`` is whether that file has actually been written yet:
        # the home is settled at once, the bytes land when there is something to
        # put in them. See :meth:`LoadoutFiles.name_loadout`.
        self.loadout_path = None
        self.on_disk = False
        # Whether the loadout in hand has moved on from the one on disk; set
        # only by files.mark_unsaved() and files.mark_saved().
        self.dirty = False
        self._output_dirty = True
        # Whether an edit into the loadout's own mod has asked who you are
        # this session: see _put_your_name_on_it.
        self._asked_who_you_are = False
        # Set by taking a loadout up, and spent once it is fully open: whether
        # to look for mods it was built on that are not installed here.
        self.look_for_missing_mods = False
        # Which mods elsewhere on this PC create each GUID: the scan running,
        # whether to run it again when it lands, and the mod just imported to
        # say so about (see scan_elsewhere).
        self._elsewhere_worker = None
        self._elsewhere_again = False
        self._announce_elsewhere = ""
        # What the game will load right now, against this loadout -- held
        # from the last time it was asked, and asked again at every moment
        # the answer can have moved.  See refresh_truth().
        self.truth = None
        self.game_folder = None
        # Where the mods folder, the library and the game are: one answer
        # to each, read off the state above whenever it is asked.
        self.where = Where(self)
        # Every question the window asks you: one object a test answers.
        self.prompts = Prompts(self)
        # Everything the window does with mod.io: getting, updating,
        # collections, the account.
        self.modio = ModioController(self)
        # The game's own loader, asked as a second opinion.
        self.loader_check = LoaderController(self)
        # Whether a newer Studio is out, and which version this was before
        # an update, said in the status bar.
        self.studio_release = StudioReleaseController(self)
        # The library: the conversion, taking it back, folding, the
        # library's mods no loadout mentions.
        self.library_controller = LibraryController(self)
        # Play, and which loadout and which profile are live.
        self.live = LiveController(self)
        # The loadout as a file: its path and name, saving, recents,
        # renaming and forking it, and what is unsaved.
        self.files = LoadoutFiles(self)
        # The game's files: its assets.xml and texts, read and remembered.
        self.game_files = GameFilesController(self)
        # Patches over somebody else's mod, and a mod of your own.
        self.patches = PatchesController(self)
        # The Start tab, and what you are here to do.
        self.start = StartController(self)
        # Patterns: a search and the edit made to every match.
        self.patterns = PatternsController(self)
        # Readings a change in what loads has left in doubt, read again.
        self.readings = ReadingsController(self)
        # The panels that are in windows of their own.
        self.panels = PanelWindows(self)
        self._panels_restored = False
        # While those are being put out, as the window is first shown.
        self._restoring_panels = False
        self.files.mark_saved()
        # One trail through the assets looked at, walked by both the asset
        # editor and the reference graph: they are two views of one asset, and
        # each keeping its own idea of which asset that was is how searching in
        # the editor left the graph behind. The tab not on screen catches up
        # when it is shown rather than redrawing for nobody.
        self.trail = AssetTrail(self)
        self.pending_in_editor = None
        self.pending_in_refs = None
        # While the graph or the editor is on its way to a window of its own
        # or back.
        self._moving_graph = False
        # Edit ▸ Undo, over everything that changes what the mod writes out --
        # the editor and the changes panel alike. See core.undo.
        self.undo = UndoHistory(self.loadout)
        # What the status bar last said was undone or redone, while it may
        # still be saying it: see _drop_undo_message.
        self._undo_said = ""
        # Profiles renamed since the loadout was last written: the name in
        # the file to the name now. The live record names a profile as the
        # file does, so it follows a rename when the file does -- see
        # LiveController.wrote_loadout -- and not before, or a rename not kept would leave
        # it naming a profile the file never had.
        self.profile_renames = {}

        self.setWindowTitle(studio_title())
        self.resize(OPENING_SIZE)
        # A mod's zip dropped anywhere on the window is installed: see
        # dropEvent.
        self.setAcceptDrops(True)
        self._build_ui()
        self._game_watch = QTimer(self)
        self._game_watch.setInterval(GAME_WATCH_MS)
        self._game_watch.timeout.connect(self._watch_game)
        self._game_watch.start()
        # A text box takes a dragged file as its address, typed in, and the
        # reference graph takes any drag at all: see eventFilter.
        for area in self.findChildren(QAbstractScrollArea):
            if area.viewport().acceptDrops():
                area.viewport().installEventFilter(self)
        # Shown while a zip is over the window, so it is plain the window
        # takes it, and that all of it does.
        self.drop_hint = DropHint(tr("Drop to install in the library"), self)
        # Each menu of the bar's, and the row it shows while nothing in it can
        # be clicked: see _top_menu.
        self._shut_rows = {}
        self._shut_gaps = {}
        self._build_menu()
        # Ctrl+Z and Ctrl+Y from a text box with nothing to undo: see UndoKeys.
        # The window holds it as its child, so it needs no name here.
        UndoKeys(self)
        self.update_title()
        self._restore_geometry()
        # The game files you had last time, if putting them back is free.
        self.game_files.restore_on_launch()
        # And draw the Start tab whether or not that found anything: the games
        # it knows, who you publish as, the loadouts you had open. None of it
        # was on screen until something happened to refresh it, which on a
        # cold start is nothing at all.
        # The gates go with it: refresh_source_panel() works them out, because
        # which tabs have anything to do on them is the same question the Start
        # tab is answering, and keeping the two together is what stops one of
        # them going stale behind the other.
        self.start.refresh_source_panel()

    # -- construction -------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:
        # A mod's zip dragged over a widget that would take it is passed up
        # to the window instead, which installs it.
        if event.type() in _DRAG_EVENTS and self._dropped_zips(event):
            event.ignore()
            return True
        tabs = getattr(self, "tabs", None)
        if tabs is not None and watched in (tabs, tabs.tabBar()) and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest,
        ):
            self._place_play()
        return super().eventFilter(watched, event)

    @staticmethod
    def _dropped_zips(event) -> list:
        """The mod zips among what is being dragged over the window."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [
            url.toLocalFile() for url in mime.urls()
            if url.isLocalFile() and is_mod_zip(url.toLocalFile())
        ]

    def dragEnterEvent(self, event) -> None:
        # Qt offers a drag to the widget under it and then to each parent in
        # turn, so this is reached from anywhere no field takes the file
        # itself -- and nothing in the window takes a zip.
        if self._dropped_zips(event):
            event.acceptProposedAction()
            self.drop_hint.cover()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.drop_hint.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.drop_hint.hide()
        zips = self._dropped_zips(event)
        if not zips:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        # Once the drop has returned: the program the zip was dragged from
        # waits on it, and what follows opens dialogs.
        QTimer.singleShot(0, lambda: self.library_controller.install_dropped(zips))

    def _place_play(self) -> None:
        """Play just after the last tab, centred on the tabs' own height."""
        bar = self.tabs.tabBar()
        if not bar.count():
            return
        last = bar.tabRect(bar.count() - 1)
        self.play_button.adjustSize()
        at = bar.mapTo(self.tabs, last.topRight())
        self.play_button.move(
            at.x() + 16, at.y() + max(0, (last.height() - self.play_button.height()) // 2)
        )
        self.play_button.raise_()
        x = self.play_button.geometry().right() + 2
        self.play_where.setFixedHeight(self.play_button.height())
        for beside in (self.play_where, self.play_where_tip):
            beside.adjustSize()
            beside.move(x, at.y() + max(0, (last.height() - beside.height()) // 2))
            beside.raise_()
            x = beside.geometry().right() + 4

    def _show_play(self) -> None:
        """Picked out when there is a loadout for Play to apply; plain when
        there is not, and then Play only starts the game as the mods folder
        already has it.  Greyed until a game's files are loaded, which is
        what says which game there is to start.

        The one place that says whether Play can be pressed, so the button
        says why either way.  It was switched on where the files are taken up
        and never looked at again, and greyed on a cold start it went on
        offering to start Anno 1800 "with your mods folder as it is" -- the
        game a loadout is for until files say otherwise, and nothing it could
        do.
        """
        has = self.has_something_to_play()
        loaded = self.index is not None
        # And greyed while the game is open: it read its mods when it
        # started, so there is nothing for Play to do until it is closed.
        ready = loaded and not self.game_open
        self.play_button.setEnabled(ready)
        self.play_action.setEnabled(ready)
        game = games.get(self.loadout.game).label
        # Translators: why Play is greyed; %s is the game.
        open_said = tr("%s is open, and read its mods when it started. Close it to play.",
                       game) if self.game_open else ""
        self.source.show_game_open(open_said)
        self.play_button.setObjectName("Primary" if has and ready else "")
        self.play_button.style().unpolish(self.play_button)
        self.play_button.style().polish(self.play_button)
        elsewhere = self._played_elsewhere() if loaded else []
        target = self.live.play_target() if elsewhere else None
        named = {str(path).lower(): name for path, name, _live in elsewhere}
        self.play_where.setVisible(bool(elsewhere))
        self.play_where_tip.setVisible(bool(elsewhere))
        self.play_where.setEnabled(loaded)
        self._fill_play_menu(elsewhere, target)
        if target is not None:
            # Translators: the Play button, set to play your mod in another
            # loadout; %s is that loadout's name.
            # Cut short, in the room the tab bar has: the full name is in
            # the tooltip and on the menu.
            name = self.play_button.fontMetrics().elidedText(
                named.get(str(target).lower(), ""), Qt.TextElideMode.ElideRight, 120,
            )
            self.play_button.setText(tr("▶  Play in %s", name))
        else:
            self.play_button.setText(tr("▶  Play"))
        self._place_play()
        if not loaded:
            self.play_button.setToolTip(tr("Load the game's files first, on the Start tab."))
        elif open_said:
            self.play_button.setToolTip(open_said)
        elif target is not None:
            self.play_button.setToolTip(tr(
                "Builds your mod again, then opens %(loadout)s and plays it. (F5)",
                loadout=named.get(str(target).lower(), ""),
            ))
        else:
            self.play_button.setToolTip(
                tr("Makes this loadout what %s loads, then starts the game. (F5)", game)
                if has else
                tr("Starts %s with your mods folder as it is. (F5)", game)
            )

    def _watch_game(self) -> None:
        """Ask whether the game is open, and draw Play again if that has
        changed.  Play asks again itself when pressed: the game may have
        started since the last time this asked."""
        now = launch.running(self.loadout.game)
        if now != self.game_open:
            self.game_open = now
            self._show_play()

    def _played_elsewhere(self, again: bool = False) -> list:
        """:meth:`.LiveController.played_elsewhere`, worked out again only
        where the loadout, its mod or its mod's project has changed, or with
        ``again``: this is asked at every edit, and the answer is from the
        loadouts folder.  Play itself asks afresh."""
        origin = self.loadout.stands_for()
        ref = self.loadout.projects.get(origin) if origin is not None else None
        key = (str(self.loadout_path), origin, ref.path if ref is not None else "")
        if again or key != self._elsewhere_key:
            self._elsewhere_key = key
            self._elsewhere = self.live.played_elsewhere() if ref is not None else []
        return self._elsewhere

    def _fill_play_menu(self, elsewhere, target) -> None:
        """Play's menu: here, on its own, and each other loadout that has
        this loadout's mod, the one picked ticked."""
        menu = self.play_where.menu()
        menu.clear()
        group = QActionGroup(menu)
        # Translators: an entry of Play's menu: this loadout played with
        # only its own mods.
        choices = [(None, tr("On its own"))] + [
            # Translators: an entry of Play's menu: %s is another of your
            # loadouts that has this mod, opened and played instead.
            (path, tr("In %s, what the game loads", name) if live else tr("In %s", name))
            for path, name, live in elsewhere
        ]
        for path, text in choices:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(path == target if target is not None else path is None)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, path=path: self._choose_play(path))

    def _choose_play(self, target) -> None:
        self.live.choose_play(target)
        self._show_play()

    def has_something_to_play(self) -> bool:
        return self.loadout_path is not None or bool(self.loadout.layers) or self.loadout.has_own_mod()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.source = SourcePanel()
        self.editor = EditorPanel(self.loadout, trail=self.trail)
        self.file_panel = FilePanel(self.loadout)
        self.references = ReferencePanel(trail=self.trail)
        self.output = OutputPanel(self.loadout)
        self.output.where = self.where
        self.output.loadout_name = self.files.loadout_name
        self.output.loadout_path = lambda: self.loadout_path
        self.output.own_folders = lambda: self.library_controller.give_own_folders()

        # Named so the stylesheet can make these the prominent navigation they
        # are, without restyling every dialog's tabs along with them.
        self.tabs.setObjectName("MainTabs")
        # Translators: the names of the main window's tabs, one per step.
        self.tabs.addTab(self.source, tr("Start"))
        self.tabs.addTab(self.editor, tr("Asset editor"))
        # Translators: the tab that holds the file of the mod being written,
        # to type in.
        self.tabs.addTab(self.file_panel, tr("Mod file"))
        # Translators: the tab that draws which assets refer to which.
        self.tabs.addTab(self.references, tr("References"))
        # "Export": what the page is for. "Generate mod" named the machinery
        # rather than the act, and the act is the one the user is here for.
        # Translators: the tab that writes the mod out, named for the act.
        self.tabs.addTab(self.output, tr("Export"))
        # Play, straight after the last tab, where it is in reach from every
        # one of them: it is the thing the rest of the window is for, when
        # what you are doing is playing. Not a corner widget -- the corner is
        # the far edge of the window, a long way from the tabs it belongs
        # with -- but a child of the tabs, placed after the last one whenever
        # they move. See _place_play().
        # Translators: "Play" is the Studio's button that makes the loadout
        # what the game loads and starts it -- the verb, as in playing a game.
        self.play_button = QPushButton(tr("▶  Play"), self.tabs)
        self.play_button.clicked.connect(lambda: self.live.play())
        self.play_button.setEnabled(False)
        # Where Play plays a mod of yours that other loadouts have: here, on
        # its own, or in one of them (docs/proposals/export-tab-proposal.md,
        # section 5.6). Picked once, not asked at every Play.
        self.play_where = QToolButton(self.tabs)
        self.play_where.setObjectName("PlayWhere")
        self.play_where.setText("▾")
        self.play_where.setAccessibleName(tr("Where Play plays"))
        self.play_where.setToolTip(tr("Chooses where Play plays your mod."))
        self.play_where.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.play_where.setMenu(QMenu(self.play_where))
        self.play_where.hide()
        self.play_where_tip = help_tip.HelpTip(tr(
            "On its own: only this loadout's mods, with every other mod switched off. "
            "In another loadout: your mod is built again, then that loadout is opened "
            "and played."
        ), tr("Where Play plays"), parent=self.tabs)
        self.play_where_tip.hide()
        # Made after the pages, so Tab reached it after all of them.
        QWidget.setTabOrder(self.tabs.tabBar(), self.play_button)
        QWidget.setTabOrder(self.play_button, self.play_where)
        QWidget.setTabOrder(self.play_where, self.play_where_tip)
        self.tabs.installEventFilter(self)
        self.tabs.tabBar().installEventFilter(self)
        # Which of these are open is update_tab_gates()'s to say, and it is
        # asked at the end of __init__. Setting two of the three by hand here
        # is what left Export reachable on a cold start, offering to export a
        # mod called "My Anno Mod" before anyone had decided there was one.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # The tabs that can be moved to a window of their own, each by the
        # action that moves it.
        self.tab_out_actions = {}
        self.editor_out = self._movable_tab(TAB_EDIT, self.editor, "Ctrl+Shift+2")
        self.file_out = self._movable_tab(TAB_FILE, self.file_panel, "Ctrl+Shift+3")
        self.references_out = self._movable_tab(TAB_REFS, self.references, "Ctrl+Shift+4")
        self.editor_out.toggled.connect(self._editor_moved_window)
        self.references_out.toggled.connect(self._graph_moved_window)
        self.references.pinChanged.connect(self._on_pin_changed)
        # References… on the editor and Edit this asset on the graph sit at
        # the same end of their rows, and as wide as each other: one place on
        # the screen goes to the graph and back.
        width = max(self.editor.references_button.sizeHint().width(),
                    self.references.open_button.sizeHint().width())
        self.editor.references_button.setMinimumWidth(width)
        self.references.open_button.setMinimumWidth(width)

        # The search bar is part of the editor now; keep the old name so the rest
        # of the window (and the Find shortcut) still has something to talk to.
        self.browser = self.editor.search

        # A splitter rather than a dock: a dock keeps whatever width it was given
        # when the window is resized, which left the changes list permanently
        # cramped on a wide screen.
        self.changes_panel = ChangesPanel(self.loadout)
        # The panel takes a third of the window on every tab, and the file of
        # a mod wants that width for text: so it folds to a strip at the
        # window's edge, and opens again from there.
        side = QWidget()
        across = QHBoxLayout(side)
        across.setContentsMargins(0, 0, 0, 0)
        across.setSpacing(0)
        self.changes_strip = QToolButton()
        self.changes_strip.setObjectName("PanelStrip")
        self.changes_strip.setText("\u00ab")  # i18n: keep - an arrow, no word
        self.changes_strip.setToolTip(tr("Show the Mod changes panel  (F9)"))
        self.changes_strip.setAccessibleName(tr("Show the Mod changes panel"))
        self.changes_strip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.changes_strip.clicked.connect(lambda: self.toggle_changes.setChecked(True))
        self.changes_strip.setVisible(False)
        across.addWidget(self.changes_strip)
        self.changes_body = QWidget()
        side_layout = QVBoxLayout(self.changes_body)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        # Translators: the name of the side panel listing what the mod changes.
        side_title = QLabel(tr("Mod changes"))
        side_title.setObjectName("PanelTitle")
        title_row.addWidget(side_title, 1)
        self.changes_fold = QToolButton()
        self.changes_fold.setObjectName("PanelFold")
        self.changes_fold.setText("\u00bb")  # i18n: keep - an arrow, no word
        self.changes_fold.setToolTip(tr("Fold the Mod changes panel away  (F9)"))
        self.changes_fold.setAccessibleName(tr("Fold the Mod changes panel away"))
        self.changes_fold.clicked.connect(lambda: self.toggle_changes.setChecked(False))
        title_row.addWidget(self.changes_fold)
        side_layout.addLayout(title_row)
        side_layout.addWidget(self.changes_panel, 1)
        # Out of this window altogether, for a second monitor: the panel
        # goes, the side of the splitter with it, and it comes back here.
        self.changes_out = self.panels.add(
            CHANGES, side_title.text(), self.changes_panel, "Shift+F9",
            leave=self._let_changes_go, enter=self._take_changes_back,
        )
        self.changes_move = self.panels.button(CHANGES)
        add_in_tab_order(self.changes_panel.top_row, self.changes_move)
        across.addWidget(self.changes_body, 1)
        # Three columns need real room; below this the paths become unreadable.
        # A floor under what the panel asks for, not a minimum set on it:
        # that stood in for the panel's own, and in German the three buttons
        # along its foot need more than this, so two of them were cut off at
        # both ends.
        side_layout.addStrut(340)
        self.changes_side = side
        # What the panel had when it was last folded away.
        self._changes_width = 500

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(side)
        # 2:1 rather than 3:1 -- Mod changes is where truncation happens most
        # often, so it should claim a bigger share of a wide window, not a
        # shrinking one.
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([900, 500])
        self.setCentralWidget(self.splitter)

        self.source.importRequested.connect(lambda: self.game_files.import_from_game())
        self.source.gameChosen.connect(self.game_files.restore_last_read)
        self.source.readGameRequested.connect(self.game_files.import_from_game)
        self.source.creatorChangeRequested.connect(self.preferences.change_creator)
        self.source.foldersRequested.connect(self.show_folders)
        self.source.browseAssetsRequested.connect(lambda: self.game_files.open_assets())
        self.source.browseTextsRequested.connect(lambda: self.game_files.open_texts())
        self.source.clearTextsRequested.connect(self.game_files.clear_texts)
        self.source.continueRequested.connect(self._go_to_editor)
        self.source.aimChosen.connect(self.start.choose_aim)
        self.source.ownModRequested.connect(
            lambda: self.start.choose_aim(AIM_MAKE, existing=True)
        )
        self.source.collectionLoadoutRequested.connect(
            lambda: self.start.choose_aim(AIM_MANAGE, collection=True)
        )
        self.source.libraryLoadoutRequested.connect(
            lambda: self.start.choose_aim(AIM_MANAGE, from_library=True)
        )
        self.source.forkRequested.connect(self.files.fork_loadout)
        self.source.profileChosen.connect(self.live.choose_profile)
        self.source.newProfileRequested.connect(lambda: self.live.new_profile())
        self.source.renameProfileRequested.connect(lambda: self.live.rename_profile())
        self.source.deleteProfileRequested.connect(lambda: self.live.delete_profile())
        self.source.separateCopyRequested.connect(lambda: self.files.fork_loadout(kind=FORK_SEPARATE))
        self.source.playRequested.connect(lambda: self.live.play())
        self.source.makeLiveRequested.connect(lambda: self.live.sync_loadout())
        self.source.renameRequested.connect(self.files.ask_rename_loadout)
        self.source.openLoadoutRequested.connect(lambda: self.load_loadout())
        self.source.bringInRequested.connect(self.bring_mods_into_loadout)
        self.source.convertRequested.connect(
            lambda where: self.library_controller.convert_to_library(only={where} if where else None)
        )
        self.source.addStrangersRequested.connect(self.library_controller.bring_strangers_in)
        self.source.unsubscribeRequested.connect(self.modio.unsubscribe_from_cache)
        self.source.needsRequested.connect(self.show_needs)
        self.source.repeatsRequested.connect(self.show_conflicts)
        self.source.truthRefreshRequested.connect(self.refresh_truth)
        # Through the menu entries, which are built later and say when each
        # can be asked: see _mirror.
        self.source.updatesRequested.connect(lambda: self.updates_action.trigger())
        self.source.loadoutCheckRequested.connect(lambda: self.loadout_check_action.trigger())
        self.source.recentChosen.connect(self.files.open_recent)
        self.editor.changesUpdated.connect(self._on_edited)
        # Settings typed into a mod that nothing declares are offered as
        # options to declare.
        self.editor.typedApplied.connect(self._typed_applied)
        # A mod's option, opened from wherever it is named or listed.
        self.editor.optionRequested.connect(self.open_option)
        self.file_panel.optionRequested.connect(self.open_option)
        self.changes_panel.optionRequested.connect(self.open_option)
        self.changes_panel.optionGroupRequested.connect(self.put_under_option)
        self.output.fileRequested.connect(self.show_in_file)
        self.editor.said.connect(self.say_of_a_step)
        self.editor.assetOpened.connect(self._on_editor_opened)
        self.references.assetShown.connect(self._on_graph_moved)
        self.editor.editingChanged.connect(self.on_editing_changed)
        # "A new patch for…" picked in Currently editing makes it.
        self.editor.patch_wanted = self.patches.patch_for_changes
        self.editor.project_wanted = self.patches.make_project
        self.editor.newAssetRequested.connect(self.new_asset)
        self.editor.referencesRequested.connect(self.show_references)
        # Which way the editor shows an asset is the user's habit, not the
        # loadout's: it is kept with the window's own settings.
        self.editor.viewChanged.connect(
            lambda view: self.settings.setValue("editorView", view)
        )
        self.editor.xml.loader_verdict = self.output.fresh_loader_verdict
        # Asked of the prompts as they are when it is asked, so a test that
        # answers for them is answered to.
        self.editor.ask_unapplied = (
            lambda asset, typed_asset: self.prompts.ask_unapplied_modops(asset, typed_asset)
        )
        self.editor.fileRequested.connect(self.show_in_file)
        self.editor.confirm_reach = lambda assets: self.prompts.confirm_reach(assets)
        self.file_panel.applyRequested.connect(self._apply_file)
        self.file_panel.editRequested.connect(self._edit_file_of)
        self.file_panel.assetRequested.connect(self._open_asset)
        self.file_panel.files_from = self._files
        self.file_panel.stale_from = lambda: stale.reached(self.loadout).get(
            self.file_panel.origin(), ()
        )
        self.file_panel.ask_unapplied = lambda mod: self.prompts.ask_unapplied_file(mod)
        # Text typed in a mod's file is for that mod: asked about before the
        # picker sends changes to another, where it was drawn over unasked.
        self.editor.settle_elsewhere = self.file_panel.settle_typed
        self.changes_panel.fileRequested.connect(self.show_in_file)
        self.references.assetActivated.connect(self._open_asset)
        self.changes_panel.changeActivated.connect(self.jump_to_change)
        self.changes_panel.changesRemoved.connect(self._on_changes_removed)
        self.changes_panel.activationChanged.connect(self.on_activation_changed)
        self.changes_panel.conflictsRequested.connect(self.show_conflicts)
        self.changes_panel.unreadRequested.connect(self.show_unread)
        self.changes_panel.heldRequested.connect(self.review_held_edits)
        self.changes_panel.opActivated.connect(self.patterns.edit_pattern)
        self.changes_panel.newPatternRequested.connect(self.patterns.new_pattern)
        self.changes_panel.optionsRequested.connect(self.edit_mod_options)
        self.changes_panel.patchRequested.connect(self.patches.make_patch)
        self.changes_panel.forkRequested.connect(self.patches.fork)
        self.changes_panel.projectRequested.connect(self.patches.make_project)
        self.changes_panel.needRequested.connect(self.patches.add_need)
        self.changes_panel.unneedRequested.connect(self.patches.remove_need)
        # Whether one of a mod's own ops is rewritten in its file is the
        # patches' question, which knows what was said this session; and
        # somebody else's is asked the three ways on before it is.
        self.changes_panel.changed_directly = self.patches.changed_directly
        self.changes_panel.may_edit_inside = self.patches.may_edit_inside
        self.changes_panel.reading_folder = lambda folder: library.reading_folder(
            self.where.library_dir(), folder
        )
        self.changes_panel.unloadRequested.connect(self.unload_one_mod)
        self.changes_panel.addModsRequested.connect(
            lambda: self.library_controller.add_mods()
        )
        self.changes_panel.updateRequested.connect(
            lambda origin: self.modio.check_modio_updates(only=[origin])
        )
        self.output.loadoutEdited.connect(self.files.on_loadout_edited)
        # Which mods run scripts, and what they share, is on the mod rows.
        self.output.scriptsEdited.connect(self.changes_panel.refresh)
        # What a mod declares is what its ops that read a setting find.
        self.output.optionsEdited.connect(lambda origin: self.readings.options_declared(origin))
        self.output.modExported.connect(self.files.on_mod_exported)
        # A validation row names a change; the same jump the Mod changes list
        # makes, so a problem read on the Export tab can be gone and looked at.
        self.output.changeActivated.connect(self.jump_to_change)
        # Resolved when it fires: the status bar is built further down.
        self.output.modPublished.connect(self.modio.on_mod_published)
        # The Export tab's rows: this loadout saved before a mod of it is
        # added to another, the mod.io sign-in, and this loadout made live.
        self.output.saveRequested.connect(lambda: self.files.save_loadout())
        self.output.signInRequested.connect(self.modio.modio_account)
        self.output.makeLiveRequested.connect(lambda: self.live.sync_loadout())
        self.output.modUpdated.connect(lambda said: self.status_label.setText(said))
        # Another loadout has the mod now, or no longer: Play's menu follows.
        self.output.loadoutsChanged.connect(lambda: (self._played_elsewhere(again=True), self._show_play()))
        self.output.playNowRequested.connect(lambda path, origin: self.start.play_now_with(path, origin))

        # Cut short at the window's width: a long line pushed what is running
        # behind the window, at the bar's other end, out of sight.
        self.status_label = StatusLine(tr("No assets.xml loaded."))
        self.statusBar().addWidget(self.status_label.row, 1)
        # A newer Studio, or the one just updated to, with its links: beside
        # the status line, which goes on saying everything else.
        self.statusBar().addWidget(self.studio_release.notice)
        # And at its other end, whether anything is running behind the
        # window: see ui.background.
        self.statusBar().addPermanentWidget(BackgroundIndicator(self.background))
        self.output.background = self.background

    def _build_menu(self) -> None:
        """The menu bar.

        A menu a kind of thing, named for it: the loadout (File), what is in
        it (Mods), what the game loads and how it gets there (Game), and the
        questions about what is loaded that the change list cannot answer
        for itself (Check).  They were all File once, grouped into submenus
        when it ran to twenty-two rows; the busiest of those, Mods, still ran
        to seventeen, and the entry most worth finding -- whether mod.io has
        moved on -- was the fourteenth of them, two levels down.

        An entry that has nothing to act on is greyed with the reason on it.
        A menu with nothing live in it at all says why in a row of its own --
        see :meth:`_sync_menus` -- and a submenu with nothing live is greyed
        whole, the reason on its row, rather than opening onto a list of
        things that cannot be clicked.
        """
        # Translators: in menu entries, & marks the letter pressed with Alt to
        # reach it. Keep one &, on a letter of the translation.
        file_menu = self.menuBar().addMenu(tr("&File"))
        file_menu.setToolTipsVisible(True)
        self.file_menu = file_menu

        # The loadout -- what this mod is -- comes before where its source
        # data comes from; Ctrl+O belongs to opening a loadout, the thing a
        # user is actually likely to reach for a shortcut to open.
        new_loadout = QAction(tr("&New loadout"), self)
        new_loadout.setShortcut(QKeySequence.StandardKey.New)
        new_loadout.triggered.connect(self.new_loadout)
        file_menu.addAction(new_loadout)

        load_loadout = QAction(tr("&Open loadout…"), self)
        load_loadout.setShortcut(QKeySequence.StandardKey.Open)
        load_loadout.triggered.connect(lambda: self.load_loadout())
        file_menu.addAction(load_loadout)

        # The Start tab has always listed these; the File menu is where the
        # habit of a lifetime says to look for them, and did not have them.
        # Filled when it is opened rather than when the window is built: the
        # list is read off disk, and a loadout saved since would be missing.
        self.recent_menu = file_menu.addMenu(tr("Open &recent"))
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)

        save_loadout = QAction(tr("&Save loadout"), self)
        save_loadout.setShortcut(QKeySequence.StandardKey.Save)
        save_loadout.triggered.connect(lambda: self.files.save_loadout())
        file_menu.addAction(save_loadout)

        save_as = QAction(tr("Save loadout &as…"), self)
        save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as.triggered.connect(lambda: self.files.save_loadout(ask=True))
        file_menu.addAction(save_as)

        # Save As, for a loadout, with a name and a sentence saying what it
        # is for: trying something without touching the set you play.
        self.fork_action = QAction(tr("&Fork this loadout…"), self)
        self.fork_action.triggered.connect(lambda: self.files.fork_loadout())
        self.fork_action.setEnabled(False)
        file_menu.addAction(self.fork_action)

        history = QAction(tr("Loadout &history…"), self)
        history.triggered.connect(self.files.show_history)
        file_menu.addAction(history)

        file_menu.addSeparator()

        # -- the game's own data, which the loadout is only ever read against
        self.game_data_menu = file_menu.addMenu(tr("Game &files"))
        self.game_data_menu.setToolTipsVisible(True)

        load_game_action = QAction(tr("&Load from game files…"), self)
        load_game_action.triggered.connect(lambda: self.game_files.import_from_game())
        self.game_data_menu.addAction(load_game_action)

        # "Load", not "Open": this is read as a data source, not something
        # you'd expect to edit directly the way "Open" usually implies.
        load_assets = QAction(tr("&Load base assets.xml…"), self)
        load_assets.triggered.connect(lambda: self.game_files.open_assets())
        self.game_data_menu.addAction(load_assets)

        self.reindex_action = QAction(tr("Re-index current assets.xml"), self)
        self.reindex_action.triggered.connect(lambda: self.game_files.open_assets(self.game_files.source_path(), force=True))
        self.reindex_action.setEnabled(False)
        self.game_data_menu.addAction(self.reindex_action)

        self.texts_action = QAction(tr("Load &localisation texts…"), self)
        self.texts_action.triggered.connect(lambda: self.game_files.open_texts())
        self.texts_action.setEnabled(False)
        self.game_data_menu.addAction(self.texts_action)

        file_menu.addSeparator()

        # -- what the Studio remembers about you, rather than about this work.
        # Three entries in a submenu here and a fourth over on View, each
        # opening something different and none of them saying what the Studio
        # currently thinks the answer is: a settings dialog with the lid off.
        # See :mod:`ui.settings_dialog`.
        self.settings_action = QAction(tr("&Settings…"), self)
        self.settings_action.setShortcut(settings_dialog.SHORTCUT)
        self.settings_action.setToolTip(tr(
            "Your author name, your mod.io account, the appearance, and the "
            "community GUID ranges."
        ))
        self.settings_action.triggered.connect(self.preferences.show_settings)
        file_menu.addAction(self.settings_action)

        file_menu.addSeparator()
        quit_action = QAction(tr("E&xit"), self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        file_menu.aboutToShow.connect(self._sync_menus)

        self.edit_menu = self.menuBar().addMenu(tr("&Edit"))
        # Over what the mod writes out, from wherever it was changed. A text box
        # with the focus keeps Ctrl+Z for its own text, as text boxes do,
        # while it has any to take back: see UndoKeys.
        self.undo_action = QAction(tr("&Undo"), self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self.undo_last)
        self.edit_menu.addAction(self.undo_action)
        self.redo_action = QAction(tr("&Redo"), self)
        self.redo_action.setShortcuts(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self.redo_last)
        self.edit_menu.addAction(self.redo_action)
        self._undo_separator = self.edit_menu.addSeparator()
        self._sync_undo_actions()
        self.new_asset_action = QAction(tr("&New asset…"), self)
        self.new_asset_action.triggered.connect(self.new_asset)
        self.new_asset_action.setEnabled(False)
        self.edit_menu.addAction(self.new_asset_action)
        # An edit aimed at a shape rather than at an asset: "every trade union
        # gets a fourth slot" goes on being true of trade unions another mod
        # adds, which a list of GUIDs written today does not.
        # Translators: a pattern is an edit aimed at every asset of a kind,
        # including ones other mods add, rather than at one asset.
        self.new_pattern_action = QAction(tr("New &pattern…"), self)
        self.new_pattern_action.triggered.connect(self.patterns.new_pattern)
        self.new_pattern_action.setEnabled(False)
        self.edit_menu.addAction(self.new_pattern_action)
        self._edit_menu_separator = self.edit_menu.addSeparator()
        # The rest of the menu mirrors whatever the field editor's own
        # right-click menu would offer for the row currently selected there --
        # rebuilt every time the menu opens, since that depends on selection.
        self.edit_menu.aboutToShow.connect(self._populate_edit_menu)

        view_menu = self.menuBar().addMenu(tr("&View"))
        self.toggle_changes = QAction(tr("Mod changes panel"), self)
        self.toggle_changes.setCheckable(True)
        self.toggle_changes.setChecked(True)
        self.toggle_changes.setShortcut(QKeySequence("F9"))
        self.toggle_changes.toggled.connect(self._show_changes_panel)
        view_menu.addAction(self.toggle_changes)
        view_menu.addAction(self.file_panel.toggle_outline)
        # Translators: a submenu of View, listing the panels that can each be
        # moved out of the main window into a window of its own.
        own_window = view_menu.addMenu(tr("Separate window"))
        own_window.addAction(self.changes_out)
        own_window.addAction(self.editor_out)
        own_window.addAction(self.file_out)
        own_window.addAction(self.references_out)
        # Find is for the text in front of you, as it is in any editor, and
        # the XML view is text. It was the search for an asset, which made
        # the one key mean two things depending on how the asset was shown.
        # Translators: finds text in the asset, or in the mod's file, that
        # is in front.
        find = QAction(tr("Find in asset or file"), self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self._find_in_asset)
        view_menu.addAction(find)
        focus_search = QAction(tr("Search for an asset"), self)
        focus_search.setShortcut(QKeySequence("Ctrl+P"))
        focus_search.triggered.connect(self._focus_search)
        view_menu.addAction(focus_search)

        # The main tabs, in the order they are shown. In the menu so the
        # shortcuts can be found, and so a shut tab's entry is greyed with it.
        # They go through open_tab, so a shortcut cannot pass a shut gate.
        view_menu.addSeparator()
        self.tab_actions = []
        for position in range(self.tabs.count()):
            go = QAction(self.tabs.tabText(position), self)
            go.setShortcut(QKeySequence("Ctrl+%d" % (position + 1)))
            go.triggered.connect(lambda _checked=False, n=position: self.open_tab(n))
            view_menu.addAction(go)
            self.tab_actions.append(go)
        view_menu.aboutToShow.connect(self._sync_tab_actions)

        # How the window looks is set in Settings. This is the signpost for
        # anyone who looks for it under View.
        view_menu.addSeparator()
        appearance = QAction(tr("Appearance…"), self)
        appearance.triggered.connect(self.preferences.show_settings)
        view_menu.addAction(appearance)

        self._build_mods_menu()
        self._build_game_menu()
        self._build_check_menu()

        help_menu = self.menuBar().addMenu(tr("&Help"))
        # Read off this window's own actions when it is opened.
        self.shortcuts_action = QAction(tr("&Keyboard shortcuts…"), self)
        self.shortcuts_action.triggered.connect(self.show_shortcuts)
        help_menu.addAction(self.shortcuts_action)
        self.guide_action = QAction(tr("&User guide"), self)
        self.guide_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        self.guide_action.setToolTip(tr("Opens the Studio's user guide."))
        self.guide_action.triggered.connect(lambda: help_tip.open_guide())
        help_menu.addAction(self.guide_action)
        # Every folder the Studio uses, for whoever is looking afterwards
        # for where something went: see ui.folders_dialog.
        self.folders_action = QAction(tr("&Studio folders…"), self)
        self.folders_action.setToolTip(tr("Your loadouts, the library, the mods folder and the rest."))
        self.folders_action.triggered.connect(self.show_folders)
        help_menu.addAction(self.folders_action)
        help_menu.setToolTipsVisible(True)
        help_menu.addSeparator()
        # Says how it came out in the status bar, so no dialog and no dots.
        self.release_check_action = QAction(tr("&Check for updates"), self)
        self.release_check_action.setToolTip(tr("Checks GitHub for a newer version of the Studio."))
        self.release_check_action.triggered.connect(lambda: self.studio_release.check_now())
        help_menu.addAction(self.release_check_action)
        self.about_action = QAction(tr("&About %s", APP_NAME), self)
        self.about_action.triggered.connect(self._about)
        help_menu.addAction(self.about_action)

    def _top_menu(self, title: str, shut: str) -> QMenu:
        """A menu of the menu bar's, with the row it shows -- greyed, and
        saying ``shut`` -- while nothing else in it can be clicked.

        A menu bar's own entry cannot carry a tooltip the way a submenu's
        row can, and a menu greyed at the bar cannot be opened to find out
        what is in it; so it opens, and the first thing in it is the reason.
        """
        menu = self.menuBar().addMenu(title)
        menu.setToolTipsVisible(True)
        row = QAction(shut, self)
        row.setEnabled(False)
        row.setVisible(False)
        menu.addAction(row)
        # Set off from the entries under it, which are greyed too: without
        # the line it read as one more of them.
        gap = menu.addSeparator()
        gap.setVisible(False)
        self._shut_rows[menu] = row
        self._shut_gaps[menu] = gap
        menu.aboutToShow.connect(self._sync_menus)
        return menu

    def _build_mods_menu(self) -> None:
        """Mods: what is in the loadout, and getting more of it."""
        self.mods_menu = self._top_menu(
            tr("&Mods"), tr("Load the game's files first. A mod is read against them."),
        )

        # The one way in, whatever the loadout is: a loadout read from a
        # mods folder gets the folder's list with its own mods ticked, an
        # empty loadout the library's list, anything else the one-mod
        # import -- and each of them has mod.io and a zip a button away.
        # There were four entries for this, three of them saying "import",
        # and which one a loadout wanted was the reader's to work out.
        self.library_add_action = QAction(tr("&Add mods…"), self)
        self.library_add_action.setToolTip(tr(
            "From the mods folder, the library, mod.io or a zip, beside this "
            "loadout's mods."
        ))
        self.library_add_action.triggered.connect(lambda: self.library_controller.add_mods())
        self.library_add_action.setEnabled(False)
        self.mods_menu.addAction(self.library_add_action)
        self._mirror(self.library_add_action, self.changes_panel.add_button)

        # A mod that is not here yet: fetched from mod.io, put in the library
        # with a link in the mods folder, and read in -- the tweak aim with
        # the download in front.
        self.get_mod_action = QAction(tr("I&nstall a mod from mod.io…"), self)
        self.get_mod_action.triggered.connect(self.modio.get_mod_from_modio)
        self.get_mod_action.setEnabled(False)
        self.mods_menu.addAction(self.get_mod_action)

        # The mods this loadout was built on, and which of them are not here:
        # what opening somebody else's loadout offers, there to be asked for
        # again by whoever said "not now".
        self.loadout_mods_action = QAction(tr("Mods this &loadout uses…"), self)
        self.loadout_mods_action.triggered.connect(lambda: self.modio.fetch_missing_mods())
        self.loadout_mods_action.setEnabled(False)
        self.mods_menu.addAction(self.loadout_mods_action)

        # The inverse: forget a mod rather than read one in.  Its own action,
        # not a checkbox on the mod's own row, because unloading is the one
        # thing here that can throw work away and deserves a moment of its
        # own rather than a click that looks like any other.
        self.unload_action = QAction(tr("&Unload mods…"), self)
        self.unload_action.triggered.connect(self.unload_mods_dialog)
        self.unload_action.setEnabled(False)
        self.mods_menu.addAction(self.unload_action)

        self.mods_menu.addSeparator()

        # With the mods loaded, whether mod.io has moved on from any of them.
        # Asks nothing until clicked: the Studio makes no request on its own.
        self.updates_action = QAction(tr("Check mod.io for up&dates…"), self)
        # Not connected straight: triggered's checked flag would come in as
        # ``only``, and the dialog failed before it opened.
        self.updates_action.triggered.connect(lambda: self.modio.check_modio_updates())
        self.updates_action.setEnabled(False)
        self.mods_menu.addAction(self.updates_action)
        self._mirror(self.updates_action, self.source.updates_button)

        self.mods_menu.addSeparator()

        # A set that came in together goes off together: the mods a collection
        # installed, switched off in one go and written to the folder.
        self.collection_off_action = QAction(tr("Switch off a &collection's mods…"), self)
        self.collection_off_action.triggered.connect(self.modio.switch_off_collection)
        self.collection_off_action.setEnabled(False)
        self.mods_menu.addAction(self.collection_off_action)
        self.collection_out_action = QAction(tr("&Take a collection out…"), self)
        self.collection_out_action.triggered.connect(lambda: self.modio.take_collection_out())
        self.collection_out_action.setEnabled(False)
        self.mods_menu.addAction(self.collection_out_action)

    def _build_game_menu(self) -> None:
        """Game: what the game loads, and the two directions between it and
        the loadout -- the loadout made what the game loads, and the
        loadout made to say what the game loads."""
        # Translators: a menu of the menu bar: what the game loads, playing,
        # and the Studio's library of installed mods.
        self.game_menu = self._top_menu(
            tr("&Game"), tr("Load the game's files first."),
        )

        # This loadout says which mods should be on, and the folder is where
        # the game reads that from. Play does it and starts the game; Make
        # this live is the same for somebody who starts the game another way.
        # Translators: the menu entry for the Play button: make the loadout
        # what the game loads, and start the game.
        self.play_action = QAction(tr("&Play"), self)
        self.play_action.setShortcut(QKeySequence("F5"))
        self.play_action.triggered.connect(lambda: self.live.play())
        self.play_action.setEnabled(False)
        self.game_menu.addAction(self.play_action)
        # Always here, where the fourth card's button shows only while the
        # folder differs -- for a tool that launches the game itself.
        self.sync_action = QAction(tr("&Make this live (without starting the game)…"), self)
        self.sync_action.triggered.connect(lambda: self.live.sync_loadout())
        self.sync_action.setEnabled(False)
        self.game_menu.addAction(self.sync_action)

        # The other direction: the whole folder read in, so the loadout says
        # what the game loads -- with the switches the game has now. Not an
        # addition, which is Mods > Add mods: every mod of theirs read before
        # is read again, and only your own edits carry over.
        # Translators: reads the whole mods folder into the loadout, so the
        # loadout says what the game loads; the opposite of "Make this live".
        self.load_mods_action = QAction(tr("Match this loadout to the mods &folder…"), self)
        self.load_mods_action.setToolTip(tr(
            "Reads the mods folder in again, with the game's switches. Your "
            "own edits stay."
        ))
        # Not connected straight: triggered's checked flag would come in as
        # ``focus``, and the folder would be read without being asked.
        self.load_mods_action.triggered.connect(lambda: self.load_installed_mods())
        self.load_mods_action.setEnabled(False)
        self.game_menu.addAction(self.load_mods_action)

        self.game_menu.addSeparator()

        # The way back. What an export writes is the Studio's *reading* of a
        # mod, and a reading of a mod nobody has tested it against can come
        # back subtly different -- so every export copies what it overwrites
        # first, and this is where those copies are.
        self.restore_action = QAction(tr("Restore mods from a &backup…"), self)
        self.restore_action.triggered.connect(self.live.restore_backup)
        self.game_menu.addAction(self.restore_action)

        self.game_menu.addSeparator()

        # -- where the installed mods are kept: into the library, and out
        # Translators: a submenu of Game: the Studio's library of installed
        # mods, and moving mods into it and out of it.
        self.library_menu = self.game_menu.addMenu(tr("&Library"))
        self.library_menu.setToolTipsVisible(True)
        # Where it is, first: the default is a folder Explorer hides.
        self.library_open_action = QAction(tr("&Open the library folder"), self)
        self.library_open_action.triggered.connect(lambda: self.library_controller.open_folder())
        self.library_menu.addAction(self.library_open_action)
        self.library_menu.addSeparator()

        # The way into the library, for what the game loads already. The
        # fourth card has the same button, but a menu is where a thing gets
        # looked for, and this one held the way back and not the way in.
        self.convert_action = QAction(tr("Con&vert the mods folder to the library…"), self)
        self.convert_action.triggered.connect(lambda: self.library_controller.convert_to_library())
        self.convert_action.setEnabled(False)
        self.library_menu.addAction(self.convert_action)
        # The way out of the library: every link a real folder again.
        self.put_back_action = QAction(tr("Put the &library's mods back into the mods folder…"), self)
        self.put_back_action.triggered.connect(self.library_controller.put_library_back)
        self.put_back_action.setEnabled(False)
        self.library_menu.addAction(self.put_back_action)
        # And the way back from a conversion: every folder where it was.
        self.undo_convert_action = QAction(tr("&Undo the conversion to the library…"), self)
        self.undo_convert_action.triggered.connect(self.library_controller.undo_conversion)
        self.undo_convert_action.setEnabled(False)
        self.library_menu.addAction(self.undo_convert_action)

        self.library_menu.addSeparator()
        # A folder of your own -- a checkout edited elsewhere -- managed in
        # place: linked into the mods folder, and never copied.
        self.refer_action = QAction(tr("Manage a &folder of your own in the mods folder…"), self)
        self.refer_action.triggered.connect(self.library_controller.manage_own_folder)
        self.refer_action.setEnabled(False)
        self.library_menu.addAction(self.refer_action)

    def _build_check_menu(self) -> None:
        """Check: the questions about what is loaded that the change list
        cannot answer for itself."""
        # Translators: a menu of the menu bar: checks run over the loaded mods.
        self.check_menu = self._top_menu(
            tr("&Check"), tr("No mods are loaded to check yet."),
        )

        # Which fields two mods both reach for.  It had no menu entry at all:
        # the only way in was a button in the Mod changes panel that appears
        # only while there is something contested, so somebody who had seen it
        # once and wanted it again had nowhere to look.
        self.conflicts_action = QAction(tr("Mods reaching for the same &thing…"), self)
        self.conflicts_action.triggered.connect(self.show_conflicts)
        self.conflicts_action.setEnabled(False)
        self.check_menu.addAction(self.conflicts_action)

        # A pattern was answered against the assets that matched it when the mod
        # was read, and that answer can move -- most obviously when another mod
        # adds an asset it would have covered. A pattern that still holds its op
        # can simply be asked again; one that does not can only be reported.
        self.check_patterns_action = QAction(tr("Re-check what the mods &search for…"), self)
        self.check_patterns_action.triggered.connect(lambda: self.patterns.check_patterns(True))
        self.check_patterns_action.setEnabled(False)
        self.check_menu.addAction(self.check_patterns_action)

        # A mod that is only nine-tenths read is not the same thing as one that
        # is fully read, and the change list cannot say which ops are missing
        # from it -- they are the ones that produced no changes.
        self.unread_action = QAction(tr("ModOps that could not be &read…"), self)
        self.unread_action.triggered.connect(lambda: self.show_unread())
        self.unread_action.setEnabled(False)
        self.check_menu.addAction(self.unread_action)

        # The Export tab's check is one mod on the mods it builds on; this is
        # every mod that loads, at once. Slow, so only on asking.
        self.loadout_check_action = QAction(tr("Check the whole loadout with the game's &loader…"), self)
        self.loadout_check_action.setToolTip(tr(
            "Runs every loaded mod through the game's loader. Large loadouts "
            "take over a minute."
        ))
        self.loadout_check_action.triggered.connect(self.loader_check.check_whole_loadout)
        self.loadout_check_action.setEnabled(False)
        self.check_menu.addAction(self.loadout_check_action)
        self._mirror(self.loadout_check_action, self.source.loadout_check_button)

        self.check_menu.addSeparator()
        # -- the loader itself, rather than the mods: actions, so not in
        # Settings, which keeps what they last said. Run from here, each
        # says its outcome in the status bar, since nothing else on screen
        # shows it.
        self.probes_action = QAction(tr("Check the reading r&ules with the loader"), self)
        self.probes_action.triggered.connect(lambda: self.loader_check.probe_loader(force=True, say=True))
        self.check_menu.addAction(self.probes_action)
        self.newest_action = QAction(tr("Check for loader e&xtension updates"), self)
        self.newest_action.triggered.connect(lambda: self.loader_check.ask_newest_loader(force=True, say=True))
        self.check_menu.addAction(self.newest_action)

    @staticmethod
    def _mirror(action: QAction, button) -> None:
        """A button that does what a menu entry does, greyed while it is:
        the entry is where whether it can be asked is decided."""
        button.setEnabled(action.isEnabled())
        action.enabledChanged.connect(button.setEnabled)

    def _sync_tab_actions(self) -> None:
        for position, go in enumerate(self.tab_actions):
            go.setEnabled(self.tabs.isTabEnabled(position))

    def _show_changes_panel(self, shown: bool) -> None:
        """Open the Mod changes panel, or fold it to the strip at the edge.

        Folded, the strip is all the side is: the splitter gives the tabs
        the rest, and what the panel had is kept to open it at again.
        """
        if shown != self.changes_body.isHidden():
            return
        sizes = self.splitter.sizes()
        # Not yet shown, the splitter has not been laid out, and the width it
        # gives the panel is none anybody dragged it to.
        if not shown and self.splitter.isVisible():
            self._changes_width = sizes[1]
        self.changes_body.setVisible(shown)
        self.changes_strip.setVisible(not shown)
        strip = self.changes_strip.sizeHint().width()
        self.changes_side.setMaximumWidth(16777215 if shown else strip)
        wanted = max(self._changes_width, 340) if shown else strip
        self.splitter.setSizes([max(1, sum(sizes) - wanted), wanted])
        if not shown and self.changes_panel.isAncestorOf(QApplication.focusWidget()):
            self.tabs.currentWidget().setFocus()

    def _movable_tab(self, tab: int, panel: QWidget, shortcut: str) -> QAction:
        """Make the panel of ``tab`` one that can be moved to a window of
        its own, with its button in its top row; the action that moves it.

        The tab stays in the strip while the panel is out, with a page that
        says where it is.
        """
        title = self.tabs.tabText(tab)
        away = AwayPage(title)
        action = self.panels.add(
            TAB_PANELS[tab], title, panel, shortcut,
            leave=lambda: self._page_of(tab, away),
            enter=lambda: self._tab_back(tab, panel),
        )
        away.back_button.clicked.connect(lambda: action.setChecked(False))
        add_in_tab_order(panel.top_row, self.panels.button(TAB_PANELS[tab]))
        self.tab_out_actions[tab] = action
        return action

    def _tab_back(self, tab: int, panel: QWidget) -> None:
        """``tab``'s panel back in its place, and the gates looked at again:
        out, its action stayed enabled whatever the gate, and greyed only at
        the next look it went on moving a shut tab's panel out."""
        self._page_of(tab, panel)
        self.update_tab_gates()

    def _page_of(self, tab: int, page: QWidget) -> None:
        """Put ``page`` in the place of ``tab``'s, and change nothing else
        about the tabs: which is in front, which are open, what each says."""
        tabs = self.tabs
        front = tabs.currentIndex()
        kept = tabs.tabText(tab), tabs.isTabEnabled(tab), tabs.tabToolTip(tab)
        gone = tabs.widget(tab)
        # Nobody is told a tab came and went: none did.
        quiet = tabs.blockSignals(True)
        tabs.removeTab(tab)
        # Taken out of the tabs it is still theirs, and would be drawn
        # over whichever page is in front.
        gone.hide()
        tabs.insertTab(tab, page, kept[0])
        tabs.setTabEnabled(tab, kept[1])
        tabs.setTabToolTip(tab, kept[2])
        tabs.setCurrentIndex(front)
        tabs.blockSignals(quiet)

    def _let_changes_go(self) -> None:
        """The Mod changes panel is going to a window of its own.

        In or out comes before open or folded: out, the panel is shown
        whole, and the fold has nothing to fold.  So it is opened first and
        its control greyed -- and opened is how it comes back, where a strip
        would be nothing like the window that was just closed.
        """
        self.toggle_changes.setChecked(True)
        self.toggle_changes.setEnabled(False)
        width = self.splitter.sizes()[1]
        # Put out again as the Studio opens, the window is still being laid
        # out -- maximised, it is not yet the width it will be -- and what
        # the splitter gives the panel now is no width anybody chose: the
        # one remembered with the splitter stands (H2-29).
        if not self._restoring_panels and width > self.changes_strip.sizeHint().width():
            self._changes_width = width
        self.changes_side.hide()

    def _take_changes_back(self) -> None:
        """The Mod changes panel is back from a window of its own: in its
        place under the title, at the width it had."""
        self.changes_body.layout().insertWidget(1, self.changes_panel, 1)
        # The tabs had the divider's width as well, while the side was gone.
        room = sum(self.splitter.sizes()) - self.splitter.handleWidth()
        self.changes_side.show()
        wanted = max(self._changes_width, 340)
        self.splitter.setSizes([max(1, room - wanted), wanted])
        self.toggle_changes.setEnabled(True)

    # -- remembered places ---------------------------------------------------
    def mods_key(self, base: str) -> str:
        """A settings key for a remembered mods folder, kept apart per game.

        1800 and 117 mods live in entirely different folders, and remembering
        one path for both meant the dialog could open on the wrong game's
        folder with nothing to say so -- exactly the kind of default that gets
        clicked through without being read.
        """
        return self.where.per_game(base)

    def remembered(self, key: str, fallback: str = "") -> str:
        """A folder this app remembered, if it is still a folder.

        A path that has since been deleted is worse than no suggestion at all:
        it opens the dialog somewhere that does not exist, and the user has no
        way to tell why. Which is also what a stale temporary folder looks
        like -- and there were several, from a test run that had been writing
        into the real settings. See :func:`open_settings`.
        """
        value = str(self.settings.value(key, "") or "")
        if value and Path(value).is_dir():
            return value
        return fallback

    def remembered_file(self, key: str) -> str:
        """The same, for a remembered file rather than a folder."""
        value = str(self.settings.value(key, "") or "")
        return value if value and Path(value).is_file() else ""

    # -- what the menus can do just now ---------------------------------------
    def _sync_menus(self) -> None:
        """Grey what has nothing to act on, and say why, as a menu opens.

        A submenu is a promise that there is something behind it.  Left
        enabled, one whose every entry is unavailable makes somebody hover,
        read a list of greyed lines and come back out -- and it does it every
        time they pass.  Disabled, the row itself carries the answer, and the
        tooltip carries the reason: the same bargain the rest of the app
        makes, where an entry that does not apply says so rather than
        vanishing.

        A menu of the bar's cannot be greyed that way and still say why, so
        one with nothing live in it opens on a row that does: see
        :meth:`_top_menu`.  Mods and Check, on a cold start, are the ones
        that do; Game always has a backup that can be put back.  It is
        written as a rule over all of them rather than as those cases, so
        that gating an entry later cannot quietly leave a menu promising
        something it no longer has.
        """
        self._sync_entries()
        for menu, reason in (
            (self.game_data_menu, tr("No game files to load.")),
            (self.library_menu, tr("Load the game's files first. The library is kept per game.")),
        ):
            live = any(_live(action) for action in menu.actions())
            menu.menuAction().setEnabled(live)
            menu.menuAction().setToolTip("" if live else reason)
        # The loader's own checks are not about the loaded mods: live, they
        # left the four greyed mod checks above them with nothing saying why.
        aside = {self.probes_action, self.newest_action}
        for menu, row in self._shut_rows.items():
            shut = not any(
                _live(action) for action in menu.actions()
                if action is not row and action not in aside
            )
            row.setVisible(shut)
            self._shut_gaps[menu].setVisible(shut)

    def _sync_entries(self) -> None:
        """Grey the entries that have nothing to act on, with the reason on
        each: in a fresh data folder, with no loadout and an empty library,
        three of them opened only to say so (H2-24).  Asked as a menu opens,
        of the loadout and the library's own records."""
        # The loader is there or not whether or not game files are loaded.
        found = self.loader_check.game_loader() is not None
        for action in (self.probes_action, self.newest_action):
            action.setEnabled(found)
            action.setToolTip("" if found else tr(
                "No loader found. Install Anno Modding Tools in VS Code to add the check."
            ))
        if self.index is None:
            # Greyed already, until the game files are loaded.
            return
        from ..core import convert, installplan

        there = self.where.library_dir()
        for action, live, reason in (
            (self.loadout_mods_action, bool(installplan.wanted_from_loadout(self.loadout)),
             tr("This loadout uses no mods of anybody else's.")),
            (self.put_back_action, any(mod.linked for mod in library.held(there)),
             tr("No mod in your mods folder comes from the library.")),
            (self.undo_convert_action, convert.last_undoable(there) is not None,
             tr("No conversion to the library is waiting to be undone.")),
        ):
            action.setEnabled(live)
            action.setToolTip("" if live else reason)

    def _populate_recent_menu(self) -> None:
        """The loadouts worked on lately, read off disk as the menu opens.

        Not filled once at start-up: the list comes from the loadouts folder as
        well as from settings, so one saved since -- or by the window itself,
        a minute ago -- would be missing from a menu built when the window was.
        """
        self.recent_menu.clear()
        recent = self.files.recent_rows(self.game_files.open_game())
        if not recent:
            empty = QAction(tr("No loadouts opened yet"), self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for name, path in recent:
            action = QAction(name, self)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, path=path: self.files.open_recent(path)
            )
            self.recent_menu.addAction(action)

    def _populate_edit_menu(self) -> None:
        """Rebuild the Edit menu's lower section for whatever is selected now."""
        kept = (
            self.undo_action,
            self.redo_action,
            self._undo_separator,
            self.new_asset_action,
            self.new_pattern_action,
            self._edit_menu_separator,
        )
        for action in list(self.edit_menu.actions()):
            if action not in kept:
                self.edit_menu.removeAction(action)
                action.deleteLater()
        self.editor.populate_edit_menu(self.edit_menu)

    def new_asset(self) -> None:
        """Create a new asset as a ModOp inserted next to an existing one."""
        if self.index is None:
            return
        # Every mod on this PC, not only the loadout: one switched off or
        # sitting unlinked in the library is a GUID taken all the same.  The
        # first read of a full folder takes seconds; after that only what
        # changed is read again.
        with busy_cursor():
            mods = discovery.mods_on_this_pc(
                self.loadout.game,
                [self.where.mods_folder(), self.where.game_mods()],
                self.where.library_dir(),
            )
            # The community GUID register, fetched here the first time it is
            # needed: one small file from GitHub, under the same busy
            # cursor.  Offline, the dialog offers personal use and says why.
            register = guidranges.fetch_if_missing(self.loadout.game)
            dialog = NewAssetDialog(
                self.workspace,
                self.loadout,
                self,
                mods=mods,
                export_target=self.output.export_target(),
                register=register,
                # The asset you found first is the one you meant to copy.
                base_guid=self.editor.guid,
                preferred=self.preferences.guid_range(self.loadout.game),
            )
        if dialog.exec() != int(dialog.DialogCode.Accepted) or dialog.change is None:
            return
        dialog.change.origin = self.loadout.editing_origin()
        self.loadout.set_change(self.loadout.place(dialog.change))
        self.on_changes_updated()
        self.record()
        self.status_label.setText(tr_stored(dialog.change.summary))

        # Open it straight away: it is a real asset as far as the Studio is
        # concerned now, just one that only exists inside this mod.
        from ..core import effective as effective_mod

        guid = effective_mod.new_asset_guid(dialog.change)
        if guid:
            self._open_asset(guid)

    # -- a loaded mod's own settings -----------------------------------------
    def edit_mod_options(self, origin: str) -> bool:
        """Re-read a loaded mod at different settings of its own.

        The import dialogs ask this before reading, which is the wrong and only
        moment they could: you have not seen what the mod does yet, and the
        answer is thrown away the next time you import it.  A loaded mod carries
        both what it offers and what it was read at, so the question can be put
        again here, where the changes it produced are on screen.

        Re-reading is the whole of it -- a configurable mod's ops read the
        answers back, so half of what it does cannot be decided any other way.
        That means the mod's changes are read again from its folder, and edits
        of your own that were folded into them cannot survive it.  So this asks
        first, and declining leaves the settings alone.
        """
        from ..core import settings as settings_mod
        from .mod_options_dialog import ModOptionsDialog

        layer = self.loadout.layer(origin)
        if layer is None or not layer.offered or self.index is None:
            return False
        # A library mod with no link is where the library keeps it (R3-46).
        folder = library.reading_folder(self.where.library_dir(), layer.folder)
        if folder is None:
            tell(
                self,
                tr("No folder to re-read"),
                tr("%s was read from a folder that is no longer there, so its "
                   "settings cannot be put to it again.", named(layer.label)),
            )
            return False

        offered = settings_mod.from_dict(layer.offered)
        dialog = ModOptionsDialog(layer.label, offered, layer.options, self)
        accepted = dialog.exec() == int(QDialog.DialogCode.Accepted)
        chosen = dict(dialog.values) if accepted else {}
        dialog.deleteLater()
        if not accepted or chosen == offered.settled(layer.options):
            return False

        if not self.prompts.confirm_read_again(layer, self.loadout):
            return False

        try:
            report = self._read_again_in_stack(origin, chosen)
        except Exception as exc:  # noqa: BLE001 - a bad folder is not a crash
            tell(
                self, tr("Could not read it again"),
                tr("%(mod)s: %(error)s", mod=named(layer.label), error=exc),
                warning=True,
            )
            return False

        self.loadout.drop_layer(origin)
        was = offered.settled(layer.options)
        layer.options = chosen
        # What it holds from here on is its folder's, and nothing done here.
        layer.edited = False
        layer.unread = sum(1 for entry in report.unread(origin) if not entry.markup)
        self.loadout.set_layer(layer)
        # Read where it stands a moment ago; the mods after it are another
        # matter, and on_changes_updated below sees to them.
        self.readings.settle({origin})
        for op in report.ops:
            self.loadout.set_op(op)
        for change in report.changes:
            self.loadout.set_change(change)
        # Which of its ops could not be read is part of what a setting decides:
        # an op guarded by a toggle that is now off does not run, so it is no
        # longer something the Studio failed to read.
        self.loadout.unread = self.loadout.unread + list(report.unread(origin))
        self.files.took_reading({origin})
        self.on_changes_updated()
        self.changes_panel.refresh()
        self.start.refresh_source_panel()
        self.status_label.setText(_reread_at(layer.label, offered, chosen, was, len(report.changes)))
        # A reading of its folder, not an edit: reading it again is the undo.
        self.forget_undo()
        return True

    def _read_again_in_stack(self, origin: str, chosen: dict):
        """``origin``'s reading at ``chosen``, with the mods around it read again too.

        See :func:`core.stale.read_at`: as many mods as the loadout holds,
        so a progress dialog says so, and the ones before it that were
        edited here read with those edits. A mod that reading cannot find
        under the name it was read in under -- the ValueError of
        :func:`core.reader.analyse_in_stack` -- is read alone, as it was
        before (P7-18).
        """
        from ..core import loaderlog, modops, reader

        layer = self.loadout.layer(origin)
        progress = QProgressDialog(tr("Reading %s again…", layer.label), "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowTitle(tr("Reading it again"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def said(position, total, name=""):
            progress.setMaximum(total)
            progress.setValue(position)
            if name:
                progress.setLabelText(tr(
                    "Reading %(mod)s… (%(done)s of %(total)s)", mod=name,
                    done=number(position + 1), total=number(total),
                ))
            QApplication.processEvents()

        try:
            with busy_cursor():
                try:
                    return stale.read_at(
                        self.loadout, origin, self.index, chosen,
                        logged=loaderlog.logged_order(self.loadout.game),
                        progress=said, translating=said,
                        library_dir=self.where.library_dir(),
                    )
                except ValueError:
                    # The way it was read when it came in, not at the defaults:
                    # the boxes on the import dialog decide what its changes
                    # are as much as its settings do.
                    reading = modops.Reading.as_recorded(layer.read_as, {origin: chosen})
                    folder = library.reading_folder(self.where.library_dir(), layer.folder)
                    return reader.analyse(
                        folder or Path(layer.folder), self.index, reading.keep_inert,
                        reading=reading, origin=origin,
                    )
        finally:
            progress.close()
            progress.deleteLater()

    # -- reading mods into the loadout ----------------------------------------
    def import_mod(self, separate: bool = False, start_dir: str = "", detours=()) -> bool:
        """Read another author's mod into this loadout, each op as written.

        Returns whether anything actually landed, so a caller that only wants
        to move on once there is something to look at -- choosing this as a
        way of working, say -- knows whether the dialog was cancelled.

        ``separate`` starts the dialog with "keep it as its own mod" ticked,
        which is what changing somebody else's mod needs: a patch has to have a
        mod to stand in front of.  The user can still untick it.

        ``start_dir`` points the dialog at one mod already, for the one case
        that has just chosen it: a mod fetched from mod.io a moment ago.

        ``detours`` are other ways to a mod, each a button on the dialog that
        closes it and is taken instead: see ``widgets.add_detours``.  What
        one of them returns is what this does.
        """
        from ..core import effective as effective_mod

        if self.index is None:
            return False
        # The mods folder itself, not the mod read last time: you are here to
        # choose *which* mod, and a dialog that opens already pointed at one --
        # without having read it, so the Import button is dead until you pick
        # it again -- answers a question you did not ask.  The folder the
        # window is about, which is the game's own only when the loadout's
        # mods are not elsewhere: the library's switched-off mods are listed
        # by the folder their links were in.
        start = start_dir or self.where.mods_folder()
        if not start:
            start = self.remembered(self.mods_key("last_mod_import_dir"))
        dialog = ModImportDialog(
            self.index,
            self,
            start_dir=start,
            game=self.loadout.game,
            separate=separate,
            creator=self.preferences.creator_name(),
            aliases=self.preferences.aliases(),
            library_dir=self.where.library_dir(),
            detours=detours,
        )
        answer = _answer_of(dialog)
        if answer is not None:
            return answer
        self.settings.setValue(
            self.mods_key("last_mod_import_dir"), dialog.folder_box.text().strip()
        )

        layer = dialog.layer()
        if layer is not None:
            self.loadout.set_layer(layer)
            patching.reattach(self.loadout)
        # The same for its ops as for its changes below: one imported before
        # is the one here, not a second.
        self.loadout.as_before(dialog.ops, dialog.changes, dialog.unread)
        for op in dialog.ops:
            self.loadout.set_op(op)
        self.loadout.unread = [
            entry for entry in self.loadout.unread if entry.origin != dialog.origin
        ] + list(dialog.unread)
        self.loadout.count_unread({dialog.origin})
        if layer is not None:
            # Read on its own, whatever loads before it here: it is read
            # again where it stands once it is in.
            self.readings.read_alone(layer.origin)
        replaced = 0
        for change in dialog.changes:
            # Re-importing a mod should update what it put here before, not
            # stack a second copy of every insertion on top of it.
            already = self.loadout.equivalent(change)
            if already is not None:
                replaced += 1
                change.uid = already.uid
            self.loadout.set_change(change)
        # The details its modinfo gives, as the own mod's record names them;
        # what else it says (what it loads after) is the mod's, not yours.
        for field, value in dialog.metadata.items():
            name = "name" if field == "mod_name" else field
            if name in _IMPORTED_DETAILS:
                setattr(self.loadout.own, name, value)
        if dialog.metadata:
            self.output.load_loadout_fields()
            # Carrying on your own mod: the loadout is the mod, so it takes the
            # mod's name too, rather than staying filed under whatever was typed
            # into "Name your mod" a moment before.
            taken = dialog.metadata.get("mod_name")
            if (
                taken
                and self.loadout.stands_for() is not None
                and self.loadout_path is not None
            ):
                self.files.rename_loadout(taken)

        # What a patch of yours keeps inside it, set aside while it was not
        # loaded here.
        inside.take_back(self.loadout, {dialog.origin})
        # With no card chosen and no loadout, the mod read in starts one.
        self.start.took_mods_in()
        # What it brought is what there is of it to take away: see
        # LoadoutFiles.unsaved_work.
        self.files.took_reading({dialog.origin})
        self.on_changes_updated()
        # What came in is the mod's ops, each as written.
        message = trn(
            "Imported %(n)s ModOp from %(mod)s.",
            "Imported %(n)s ModOps from %(mod)s.",
            dialog.taken,
            mod=dialog.report.metadata.get("mod_name") or dialog.report.mod_name,
        )
        if replaced:
            # Translators: %(status)s is the sentence saying what was imported.
            message = trn(
                "%(status)s  %(n)s replaced a change already in this loadout.",
                "%(status)s  %(n)s replaced a change already in this loadout.",
                replaced, status=message,
            )
        self.status_label.setText(message)
        # And against every other mod on this PC: one imported on its own is
        # only ever played beside others.
        self.scan_elsewhere(announce=dialog.origin or "<yours>")
        # Reading a mod in is undone by unloading it, not by Ctrl+Z walking back
        # through a reading of its folder.
        self.forget_undo()
        # A mod arriving can be the very thing that makes another mod's search
        # out of date, so this is the moment to look -- and the only assets that
        # can have moved anything are the ones it just brought.
        arrived = [
            effective_mod.new_asset_guid(change)
            for change in dialog.changes
            if change.creates_asset
        ]
        if arrived:
            self.patterns.check_patterns(among=[guid for guid in arrived if guid])
        return True

    def read_installed(self, placed, also=()) -> bool:
        """Read in the mods just installed at ``placed``, their links in the
        mods folder; whether anything landed.

        With a whole mods folder loaded, the folder is read again so the new
        mods take their place in the stack -- ``also`` names the folders
        installed with them, which only that reading takes in.  Otherwise
        each is imported on its own, kept separate, which is what changing
        somebody else's mod wants.
        """
        placed = list(placed)
        if self.loadout.mods_folder and self.loadout.layers:
            return self.load_installed_mods(
                arrived={link.name for link in placed} | set(also), keep_switches=True,
            )
        landed = False
        for link in placed:
            landed = self.import_mod(separate=True, start_dir=str(link)) or landed
        self.refresh_truth()
        return landed

    def load_installed_mods(self, focus=None, arrived=None, keep_switches: bool = False,
                            picked=None, adding=None, standing: bool = False,
                            detours=()) -> bool:
        """Read a whole mods folder in, so the Studio shows the game as played.

        ``focus`` names the folders to start ticked -- the mods a collection
        has just put there -- with the rest of the folder listed unticked.
        ``arrived`` names the folders just installed, which come in on, and
        ``keep_switches`` keeps the switches of the mods the loadout already
        has -- a read after an install is about the new mods, not a reason
        to take the live loadout's switches over this one's.
        ``picked`` is the library mods to read and nothing else, by their
        links' names -- the ones ticked in the library's own list, a mod
        switched off by having no link among them: they are the loadout, and
        come in on.
        ``adding`` is library mods (:class:`core.library.Held`) to read
        beside the folder, the ones with no link in it among them.  The rest
        of the library is listed in the dialog by name, each read once it is
        ticked.
        ``standing`` opens the list on the loadout's own mods as they were
        read, and reads the folder only once a mod is ticked to add (D3):
        see :class:`ModsFolderDialog`.
        ``detours`` are other ways to the mods, as :meth:`import_mod` has them.
        Returns whether anything actually landed -- see :meth:`import_mod`.
        """
        if self.index is None:
            return False
        start = self.remembered(self.mods_key("last_mods_folder"))
        if not start:
            folder = self.where.game_mods()
            start = str(folder) if folder is not None else ""
        if picked is not None:
            # The folder its links are in is the one this loadout plays in.
            linked_into = library.links_into(self.where.library_dir())
            start = str(linked_into) if linked_into is not None else self.where.mods_folder() or start
        adding = list(adding or ())
        if keep_switches and picked is None:
            # The loadout's own library mods that are switched off have no
            # link for the folder's read to find, and would drop out of it.
            asked = {mod.name for mod in adding}
            adding += [
                mod for mod in library.shelved(
                    self.where.library_dir(),
                    (layer.origin.split("/")[0] for layer in self.loadout.layers),
                )
                if mod.name not in asked
            ]
        # The rest of the library, listed by name: what the read already
        # covers left out -- a linked mod is in the folder.
        with busy_cursor():
            held = library.catalogue(self.where.library_dir())
        reading = {mod.name for mod in adding}
        shelf = [
            mod for mod in held
            if (mod.name not in picked if picked is not None
                else not mod.linked and mod.name not in reading)
        ]
        dialog = ModsFolderDialog(
            self.index, self, start_dir=start, game=self.loadout.game,
            focus=focus, provenance=self.loadout.provenance, arrived=arrived,
            listed=self.loadout.listed,
            keep={layer.origin: layer.active for layer in self.loadout.layers} if keep_switches else None,
            base_fingerprint=self.loadout.base_fingerprint,
            base_update=self.loadout.base_update,
            library_dir=self.where.library_dir() if picked is not None else None,
            picked=picked, adding=adding, shelf=shelf,
            standing=standing_of(self.loadout) if standing else None,
            detours=detours,
        )
        if keep_switches:
            # The mods it keeps keep their options as well as their switches.
            # What was answered here is in this loadout and nowhere else until
            # Play writes it, so a read of the folder alone put an iModYourAnno
            # mod back at whatever its folder holds (H2-7).
            dialog.settings = {
                layer.origin: dict(layer.options)
                for layer in self.loadout.layers if layer.options
            }
        # Read at once only where the read is what was just asked for: after
        # an install, or of the mods picked in the library. Opened from the
        # menu it waits for "Read mods" -- a full folder takes a while, and
        # the folder it opens on is only the one read last time. Opened on
        # what the loadout has, it reads once a mod is ticked to add.
        if start and not standing and (
            focus is not None or arrived or keep_switches or picked is not None
        ):
            dialog.reload()
        answer = _answer_of(dialog)
        if answer is not None:
            return answer
        chosen_folder = dialog.folder_box.text().strip()
        self.settings.setValue(self.mods_key("last_mods_folder"), chosen_folder)
        # Known from now on, so a loadout of it opened after another folder
        # was read is not asked about (check_named_folders).
        self.where.know_mods_folder(chosen_folder)
        self.loadout.mods_folder = chosen_folder
        # Reading a mods folder in is what makes the mod laid over it: the
        # place an edit to the game itself goes, among everybody else's. From
        # the card or from the File menu -- it is the reading that asks for it.
        self.loadout.make_own_mod()

        # Loading a folder replaces what was loaded from a folder before, so
        # reading it again after installing a mod does not leave the previous
        # reading of every other mod stacked underneath.
        # Ten seconds on a full Anno 1800 folder, and a wait cursor says only
        # that something is happening. These are the steps it is actually in.
        steps = (
            tr("Clearing what was loaded before…"),
            trn("Putting %(n)s mod in place…", "Putting %(n)s mods in place…",
                len(dialog.layers)),
            tr("Working out which of them are your patches…"),
            trn("Taking in %(n)s change…", "Taking in %(n)s changes…",
                len(dialog.changes)),
            tr("Grouping them for the changes panel…"),
        )
        progress = QProgressDialog("", "", 0, len(steps), self)
        progress.setWindowTitle(tr("Loading your mods"))
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        def step(number: int) -> None:
            progress.setLabelText(steps[number])
            progress.setValue(number)
            QApplication.processEvents()

        # Whose switches the folder's are: the profile the game loads, which
        # need not be the chosen one -- see Loadout.read_into.
        before = self.loadout.live_switches()
        live = None if keep_switches else self.live.live_profile_here()
        try:
            with busy_cursor():
                step(0)
                from ..core import held as held_edits

                # What of yours is made to each op of theirs, while those ops
                # are still here to say so: an update may have changed them.
                stocks = held_edits.stock(self.loadout, [layer.origin for layer in self.loadout.layers])
                # And each op's switch and note, which are the loadout's and
                # not the folder's: an op of theirs switched off stays off.
                ops_were = {op.uid: op for op in self.loadout.ops if not op.authored}
                changes_were = {
                    key: change for key, change in self.loadout.changes.items() if not change.authored
                }
                # Every op read, before the ones taken out here leave the list.
                fresh = held_edits.ops_read(dialog.ops, dialog.unread)
                # An op read again is the op that was here: what you typed
                # over one of its answers is still the exception to it.
                self.loadout.as_before(dialog.ops, dialog.changes, dialog.unread)
                # The patches made here are in no folder to be read again,
                # and the mod changes go to is asked for while this runs.
                patches = patching.held_over_a_read(self.loadout)
                mine = patching.mine_over_a_read(self.loadout)
                editing = self.loadout.editing
                for layer in list(self.loadout.layers):
                    # Keeping what you edited into them: those changes exist
                    # nowhere but here, and re-reading the folder cannot produce
                    # them a second time.
                    self.loadout.drop_layer(layer.origin, keep_authored=True)
                # What that left of other people's mods is yours.
                yours = sum(
                    1 for change in self.loadout.changes.values()
                    if change.authored and not is_own_mod(change.origin)
                )
                step(1)
                known = library.registry(self.where.library_dir())
                kept_at = {
                    mod.name: mod.target
                    for mod in (library.held(self.where.library_dir()) if picked is not None else adding)
                }
                for layer in dialog.layers:
                    # The Studio installed it and wrote down which mod.io mod it
                    # is; the update check and the plan need not guess by name.
                    if not layer.modio_id and layer.folder:
                        # A library mod with no link is known by the link's
                        # name: its target is in the library's own record.
                        target = (library.source_of(layer.folder, self.where.library_dir())
                                  or kept_at.get(Path(layer.folder).name))
                        facts = known.get(target.name, {}) if target is not None else {}
                        layer.modio_id = int(facts.get("modio_id") or 0)
                    self.loadout.set_layer(layer)
                # Which of these are patches of yours, and of what. A folder read
                # builds layers out of modinfo files, and a patch is only a mod that
                # loads after another one until this says otherwise.
                step(2)
                patching.reattach(self.loadout)
                patching.put_back(self.loadout, patches)
                patching.put_mine_back(self.loadout, mine)
                patching.find_lost(self.loadout)
                if self.loadout.layer(editing) is not None:
                    self.loadout.editing = editing
                self.loadout.read_into(live, before)
                for op in dialog.ops:
                    was = ops_were.get(op.uid)
                    if was is not None:
                        op.active = was.active
                        op.kept_by = was.kept_by
                        op.note = was.note or op.note
                    self.loadout.set_op(op)
                for change in dialog.changes:
                    was_change = changes_were.get(change.key)
                    if was_change is not None:
                        change.active = was_change.active
                # And what of a fork could not be read, which is in no folder.
                self.loadout.unread = list(dialog.unread) + patching.unread_of_forks(mine)
                self.loadout.count_unread()
                # Every mod here was just read where it stands.
                self.readings.settle()
                step(3)
                self.loadout.take_read(dialog.changes)
                kept = yours
                # Each op of theirs an edit of yours was made to, looked for in
                # what was just read: one an update changed holds the edit.
                settled = held_edits.settle(self.loadout, stocks, fresh)
                step(4)
                inside.take_back(self.loadout, {layer.origin for layer in dialog.layers})
                # With no card chosen and no loadout, the folder read starts one.
                self.start.took_mods_in()
                self.files.took_reading(layer.origin for layer in dialog.layers)
                self.on_changes_updated()
                self.changes_panel.refresh()
                progress.setValue(len(steps))
        finally:
            progress.close()
        unread = sum(layer.unread for layer in dialog.layers)
        message = tr(
            "Loaded %(mods)s, %(changes)s.",
            mods=trn("%(n)s mod", "%(n)s mods", len(dialog.layers)),
            changes=trn("%(n)s change", "%(n)s changes", len(dialog.changes)),
        )
        if kept:
            # Translators: %(status)s is what was said so far.
            message = trn(
                "%(status)s  %(n)s change you had made was kept.",
                "%(status)s  %(n)s changes you had made were kept.",
                kept, status=message,
            )
        if unread:
            message = trn(
                "%(status)s  %(n)s ModOp could not be read.",
                "%(status)s  %(n)s ModOps could not be read.",
                unread, status=message,
            )
        off = self.loadout.switched_off()
        if off:
            message = trn(
                "%(status)s  %(n)s of them your mods folder has switched off, so "
                "nothing it does counts.",
                "%(status)s  %(n)s of them your mods folder has switched off, so "
                "nothing they do counts.",
                len(off), status=message,
            )
        replaced = self.loadout.replaced()
        if replaced:
            message = trn(
                "%(status)s  %(n)s is on and not loaded, because another mod "
                "replaces it.",
                "%(status)s  %(n)s are on and not loaded, because another mod "
                "replaces them.",
                len(replaced), status=message,
            )
        if settled.moved:
            message = trn(
                "%(status)s  %(n)s edit of yours followed its op to where an update moved it.",
                "%(status)s  %(n)s edits of yours followed their ops to where an update moved them.",
                len(settled.moved), status=message,
            )
        if settled.held:
            message = trn(
                "%(status)s  %(n)s edit of yours is held: an update changed the op it was made to.",
                "%(status)s  %(n)s edits of yours are held: an update changed the ops they were made to.",
                len(settled.held), status=message,
            )
        self.status_label.setText(message)
        self.forget_undo()
        self.refresh_truth()
        self.start.refresh_source_panel()
        # The folder read is the loaded mods; the library and mod.io's
        # downloads may hold more.
        self.scan_elsewhere()
        if settled.held:
            # Asked now, when the update is what is on your mind; "Decide
            # later" leaves them held, and the mod's row says so.
            self.review_held_edits()
        return True

    # -- forgetting a loaded mod ---------------------------------------------
    def unload_mods(self, origins) -> int:
        """Forget the named mods: their layers, and every change they carry.

        Never touches origin ``""`` -- see :meth:`_confirm_unload` -- nor the
        mod of yours the loadout carries on with, which is the loadout.
        Callers that can lose work should confirm first; this only ever does
        what it is told.
        """
        origins = [
            origin for origin in origins
            if not is_own_mod(origin) and origin != self.loadout.home
        ]
        gone = 0
        names = [
            self.loadout.layer(origin).label
            for origin in origins
            if self.loadout.layer(origin) is not None
        ]
        for origin in origins:
            gone += self.loadout.drop_layer(origin)
        if origins:
            self.on_changes_updated()
            # Unlike reading one in, this can be taken back exactly: the mod,
            # its changes, its kept ops and its unread ops are all still in hand.
            # Translators: what Edit ▸ Undo would take back, said after "Undo".
            self.record(
                tr("unloading %s", names[0]) if len(names) == 1
                else trn("unloading %(n)s mod", "unloading %(n)s mods", len(origins))
            )
            self.changes_panel.refresh()
            self.start.refresh_source_panel()
        return gone

    def unload_one_mod(self, origin: str) -> None:
        """Unload the mod named, from the changes panel's own menu.

        Asked every time, unlike the Unload mods dialog's ticks and button:
        here it is one click in a menu, and an unload undone is a whole mod
        read back into every view."""
        layer = self.loadout.layer(origin)
        if layer is None or origin == self.loadout.home or not self.prompts.confirm_unload(
            [origin], self.loadout, always=True
        ):
            return
        gone = self.unload_mods([origin])
        self.status_label.setText(
            trn("Unloaded %(mod)s, %(n)s change with it.",
                "Unloaded %(mod)s, %(n)s changes with it.", gone, mod=layer.label)
            if gone
            else tr("Unloaded %s.", layer.label)
        )

    def unload_mods_dialog(self) -> None:
        """Mods ▸ Unload mods…: pick which loaded mods to let go of."""
        from .unload_dialog import UnloadModsDialog

        if all(layer.origin == self.loadout.home for layer in self.loadout.layers):
            return
        dialog = UnloadModsDialog(self.loadout, self)
        accepted = dialog.exec() == int(QDialog.DialogCode.Accepted)
        origins = list(dialog.chosen) if accepted else []
        dialog.deleteLater()
        if not origins or not self.prompts.confirm_unload(origins, self.loadout):
            return
        gone = self.unload_mods(origins)
        # Translators: %(mods)s and %(changes)s are counted phrases such as
        # "3 mods" and "12 changes".
        self.status_label.setText(
            tr("Unloaded %(mods)s, %(changes)s with them.",
               mods=trn("%(n)s mod", "%(n)s mods", len(origins)),
               changes=trn("%(n)s change", "%(n)s changes", gone))
            if gone
            else trn("Unloaded %(n)s mod.", "Unloaded %(n)s mods.", len(origins))
        )

    # -- what was not read, where mods clash, and the tab gates ---------------
    def show_unread(self, place=None) -> None:
        """The ModOps no loaded mod's changes account for, opened on the one
        at ``place`` -- ``(mod, order)`` -- when there is one."""
        from .unread_dialog import UnreadDialog

        dialog = UnreadDialog(self.loadout, self, window=self, place=place)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def review_held_edits(self, origin: Optional[str] = None) -> None:
        """Decide the edits an update held back, of the mod ``origin`` or,
        with None, of all of them: see :mod:`core.held`.  Each decision is a
        step to undo."""
        from .held_dialog import HeldDialog

        def decided() -> None:
            self.on_changes_updated()
            self.changes_panel.refresh()
            # Translators: what Edit ▸ Undo would take back, said after "Undo".
            self.record(tr("deciding an edit held after an update"))

        dialog = HeldDialog(self.loadout, self, origin, done=decided)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def show_conflicts(self) -> None:
        """List the fields more than one loaded mod sets."""
        dialog = ConflictsDialog(self.loadout, self, self.index, self.changes_panel.elsewhere)
        dialog.assetActivated.connect(self.jump_to_change)
        dialog.exec()

    def show_needs(self) -> None:
        """List what the mods that load say about each other, from the card."""
        if self.truth is None or self.truth.needs is None:
            return
        dialog = NeedsDialog(self.truth.needs, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def update_tab_gates(self) -> None:
        """Open the other tabs once there is something to do on them.

        Game files are half the question; the other half is what you are here
        to do, and the editor cannot answer it for you.  Loading an assets.xml
        and clicking straight into the editor used to land you somewhere that
        looks like it works and does nothing -- no loadout, no mod to write
        into, no way of working chosen.  A loadout that already holds work
        answers it by itself, which is what opening one does.
        """
        settled = bool(
            self.loadout.aim or self.loadout.layers or self.loadout.authored_count
        )
        open_them = self.index is not None and settled
        # A tab that cannot be used cannot be taken out -- and one that is
        # out can always be brought back.
        for tab, move in self.tab_out_actions.items():
            out = self.panels.panel_out(TAB_PANELS[tab])
            move.setEnabled(open_them or out is not None)
            # Greying a tab greys its page, and a panel that is out is not
            # its tab's page: it was left as it was when it went.
            if out is not None:
                out.setEnabled(open_them)
        for tab in (TAB_EDIT, TAB_FILE, TAB_REFS, TAB_GENERATE):
            self.tabs.setTabEnabled(tab, open_them)
            self.tabs.setTabToolTip(
                tab,
                ""
                if open_them
                else tr("Load the game files and pick what you are here to "
                        "do, on the Start tab."),
            )
        # A disabled tab is not a tab Qt refuses to open: it will park on one
        # quite happily, and what that looks like is a working page under its
        # own greyed-out tab.  So closing the gates has to bring anybody
        # standing past them back to where the questions are asked.
        if not open_them and self.tabs.currentIndex() != TAB_SOURCE:
            self.tabs.setCurrentIndex(TAB_SOURCE)

    # -- navigation ---------------------------------------------------------
    def open_tab(self, tab: int) -> bool:
        """Go to ``tab`` if its gate is open; whether it was.

        The one way anything but a click on the tab strip changes tab, so the
        gate is the one rule for when a tab can be used.  Going to an asset or
        its references used to switch the tab on as a side effect, past the
        gate.  The gates are worked out again first rather than taken on
        trust, because landing on a closed tab is silent: the page opens and
        only the tab strip says anything is wrong.
        """
        self.update_tab_gates()
        if not self.tabs.isTabEnabled(tab):
            return False
        # Going to a panel puts it in front of whoever asked, wherever it
        # is: its own window where it has one, and this one otherwise --
        # which a key pressed in a panel's window would leave behind it.
        if self.panels.bring_forward(TAB_PANELS.get(tab)):
            return True
        self.tabs.setCurrentIndex(tab)
        if self.panels.has_keyboard():
            self.activateWindow()
        return True

    def showing(self, tab: int) -> bool:
        """Whether the panel of ``tab`` is in view: the tab in front, or in
        a window of its own.  What is worth doing for a panel somebody is
        looking at asks this, and not which tab is in front."""
        return self.tabs.currentIndex() == tab or self.panels.is_out(TAB_PANELS.get(tab))

    def _go_to_editor(self, show_results: bool = False) -> None:
        # Offered by the Start tab's "in use" card, which knows the aim is
        # settled and the files are loaded -- the gates' own two conditions.
        if self.open_tab(TAB_EDIT):
            self.editor.focus_search(show_results=show_results)

    def _open_asset(self, guid: str) -> None:
        # Opened by hand, so nothing is left for the tab to catch up on when
        # it comes to the front.
        self.pending_in_editor = None
        if not self.editor.load_asset(guid):
            if self.editor.stayed:
                return
            QMessageBox.warning(self, tr("Missing asset"),
                                tr("GUID %s is not in the index.", guid))
            return
        self.open_tab(TAB_EDIT)

    def show_references(self, guid: str = None) -> None:
        """Open the References step on ``guid``, or on whatever is being edited."""
        if self.workspace is None:
            return
        guid = str(guid) if guid else self.editor.guid
        if not guid:
            return
        self.references.show_asset(guid)
        self.open_tab(TAB_REFS)

    def jump_to_change(
        self, guid: str, path: str, origin: Optional[str] = None, key=None,
    ) -> None:
        """Open the asset a change is about, at the field it changes, and in
        the XML view on the tab of ``origin``, the mod it is a change of, at
        the ModOp the change ``key`` comes of.

        Every way this can fall short now says so.  It used to fail silently
        three different ways -- an asset no longer in the index, a field the
        editor's filters were hiding, a structural op whose path names a
        container rather than a row -- and all three looked the same from the
        outside: a double-click that did nothing.
        """
        self.pending_in_editor = None
        if not self.open_tab(TAB_EDIT):
            return
        if self.editor.guid != guid:
            with busy_cursor():
                opened = self.editor.load_asset(guid)
            if not opened and self.editor.stayed:
                return
            if not opened:
                self.status_label.setText(tr(
                    "Asset %s is not in the assets.xml you have loaded. The "
                    "mod that changes it may be built against another game "
                    "version.",
                    guid,
                ))
                return
        if origin is not None:
            self.editor.show_mod(origin, key)
        if path and not self.editor.focus_on_path(path):
            self.status_label.setText(tr(
                "Opened %(asset)s. Its %(path)s is not a row in the tree. It "
                "is a removed node, or one inside something another mod took "
                "away.",
                asset=self.editor.asset_label or guid, path=path,
            ))

    def on_changes_updated(self, work: bool = True) -> None:
        """The loadout's changes have moved: everything that shows them
        follows.  ``work`` False is for a reading taken in the background,
        which moves what the rows say and is no work of anybody's: the
        loadout's mark stays as it was (H2-13)."""
        if work:
            self.files.mark_unsaved()
        # Work in a loadout answers "what are you here to do?" by itself.
        self.update_tab_gates()
        # The first change is the moment the loadout stops being a place you
        # might work and becomes one you are working in, so it gets written.
        self.files.ensure_loadout_file()
        # Before the list is drawn: its rows say which readings are in doubt.
        self.readings.check()
        self.changes_panel.refresh()
        self.file_panel.mark_stale()
        self.editor.refresh_search()
        self.mark_output_dirty()
        self.update_title()

    def _on_edited(self) -> None:
        """An edit in the asset editor, which is one step to undo."""
        # Recorded first: what the step leaves in doubt is marked before the
        # rows are drawn, and they are drawn once.  Drawn first, a list of
        # 68 mods was drawn again for the marks, every time.
        typed, self.editor.typed_applied = self.editor.typed_applied, None
        step = self.record(redraw=False)
        if step is not None:
            # Before the first change writes a new loadout's file, so the
            # name goes into it rather than waiting there unsaved.
            self._put_your_name_on_it()
        self.on_changes_updated()
        if typed is not None and step is not None:
            # An Apply in the XML view: undoing it puts the text back, and
            # what it added or changed is selected in the Mod changes panel,
            # where an op that is not this asset's own has gone.
            step.typed = typed
            self.changes_panel.show_rows(*touched(step))

    def _put_your_name_on_it(self) -> None:
        """An edit has gone into the loadout's own mod: with no author name
        known, ask for one, once a session.

        *Write a mod of your own* and *Change a mod you downloaded* ask as
        they start, since each ends in a mod with your name on it.  The
        patch over a list of mods to play ends in one as well, from its
        first edit, and nothing asked: from the card or from mods read in
        with no card, it went out with no author at all.  Asked here, after
        the edit, since a cancelled name is no reason to take the edit back.
        """
        if not is_own_mod(self.loadout.editing_origin()) or (self.loadout.own.creator or "").strip():
            return
        if not self.preferences.creator_name():
            if self._asked_who_you_are:
                return
            self._asked_who_you_are = True
        # Known: put on the mod without asking.
        self.preferences.ensure_creator()

    # -- a mod's file ---------------------------------------------------------
    def _files(self):
        """The mods there are to show the file of, as ``(label, origin)``:
        every loaded mod, in load order, and the loadout's own mod once it
        is made, under the name the asset editor's picker gives it.  Not the
        mod of your own it offers to make, which has no file."""
        self.editor.refresh_target()
        found = [(layer.label, layer.origin) for layer in self.loadout.layers]
        if self.editor.target_box.findData("") >= 0:
            # "My Anno Mod" stays English: it is the name the mod is exported
            # under until somebody gives it another.
            found.append((tr("%s  (yours)", self.loadout.own.name or "My Anno Mod"), ""))
        return found

    def _edit_file_of(self, origin: str) -> None:
        """*Edit this file* on the file tab: changes go to the mod whose
        file is shown, as though it had been picked in the asset editor.
        Somebody else's mod is never changed, so for one of theirs the
        three ways on are asked where a change to it would go nowhere
        settled yet (:meth:`PatchesController.place_for_a_pattern`), saying
        which file the tab goes to, and it goes to the file of the mod of
        yours that changes to it go to."""
        box = self.editor.target_box
        self.patches.in_its_file = True
        try:
            went = self.patches.place_for_a_pattern(origin)
        finally:
            self.patches.in_its_file = False
        if went is None:
            return
        if went != origin:
            self.file_panel.look_at(went)
        at = box.findData(went)
        if at >= 0:
            box.setCurrentIndex(at)
        # Wherever changes go now is the file to be in: the mod's own, or
        # the patch that was made for it.
        self.file_panel.mark_stale()

    def _apply_file(self, text: str) -> None:
        """Make the mod changes go to say what was typed in its file.  One
        step to undo."""
        panel = self.file_panel
        if self.workspace is None or panel.shown is None or not panel.is_typed_in():
            return
        undeclared = declaring.undeclared(self.loadout, panel.origin())
        top = self.undo.last_step
        with busy_cursor():
            found, done = modfile.apply(
                self.workspace, panel.origin(), text, panel.shown, panel.pane.regions()
            )
        if done is None:
            panel.refuse(found)
            return
        # What was typed is the mod's now, as the Studio read it: the file is
        # written again from the mod.
        panel.revert_typed()
        if done.moved:
            self._on_edited()
            # The asset open in the editor may be one the file just changed.
            # It went on showing the asset as it was, and did so still when
            # its tab was come back to: with the file in a window of its
            # own, that is on screen beside the text that says otherwise.
            self.editor.refresh_from_loadout()
        panel.refresh()
        panel.applied(done, bare_groups=modfile.bare_groups(text))
        if done.moved and self.output.propose_options(panel.origin(), undeclared) is False:
            # Not wanted with its settings undeclared: taken back, and the
            # text is there again to change, as it was typed.
            step = self.undo.last_step
            if step is not None and step is not top:
                self.undo_last()
                panel.put_back(text)
                panel.say(tr("Apply undone. Its new options were not declared."), "warn")

    def open_option(self, origin: str, name: str = "") -> None:
        """One of a mod's options, or all of them for "", opened from where
        it is named: where it is declared, for a mod of yours in Anno 117 --
        with it proposed, where nothing declares it yet -- and else what the
        mod is read at (:meth:`edit_mod_options`)."""
        if self.loadout.game == games.ANNO_117 and self.output.details_are_yours(origin):
            offered = settings.read(settings.block_of(self.loadout, origin))
            proposed = [name] if name and offered.get(name) is None else []
            self.output.declare_options(origin, proposed, focus=name)
            return
        self.edit_mod_options(origin)

    def put_under_option(self, origin: str, picked: list) -> bool:
        """Put the ModOps ``picked`` in the Mod changes panel under one of
        ``origin``'s options: each run of them in the mod's file wrapped in
        ``<Group Condition="$name">``, and applied as typing in the Mod file
        tab is -- one step to undo.  A new toggle is declared first, so the
        ops are read with it.  Whether it was done."""
        from ..core import option_groups
        from .option_group_dialog import OptionGroupDialog

        if self.workspace is None or self.loadout.game != games.ANNO_117:
            return False
        panel = self.file_panel
        if panel.has_unapplied() and panel.origin() == origin:
            QMessageBox.information(self, tr("Put under an option"), tr(
                "The Mod file tab has typing of this mod's that is not applied. "
                "Apply it or Revert it first."))
            return False
        shown = modfile.shown(self.loadout, origin)
        found = option_groups.runs(shown, picked)
        if not found:
            return False
        dialog = OptionGroupDialog(self.loadout, origin, len(picked), len(found), self)
        try:
            if dialog.exec() != int(QDialog.DialogCode.Accepted):
                return False
            name, new = dialog.chosen(), dialog.new_option()
        finally:
            dialog.deleteLater()
        was = settings.block_of(self.loadout, origin)
        if new is not None:
            settings.set_block(self.loadout, origin, {**was, **settings.to_block([new])})
        # Named in full, as the mod names its options elsewhere: the loader
        # reads a bare name as the mod's own all the same.
        mod_id = declaring.mod_id_of(self.loadout, origin)
        text = option_groups.wrapped(shown.text, found, "%s.%s" % (mod_id, name) if mod_id else name)
        with busy_cursor():
            read, done = modfile.apply(self.workspace, origin, text, shown)
        if done is None:
            settings.set_block(self.loadout, origin, was)
            QMessageBox.warning(self, tr("Put under an option"), tr(
                "The Studio could not read the file with the group in it: %s",
                read.error or tr("it is not ModOps"),
            ))
            return False
        if done.moved:
            self._on_edited()
            self.editor.refresh_from_loadout()
        if new is not None:
            self.output.loadoutEdited.emit()
            self.readings.options_declared(origin)
        self.file_panel.mark_stale()
        self.output.refresh()
        option = settings.read(settings.block_of(self.loadout, origin)).get(name)
        self.status_label.setText(trn(
            "%(n)s ModOp put under option “%(option)s”.",
            "%(n)s ModOps put under option “%(option)s”.",
            len(picked), option=option.label if option is not None else name,
        ))
        return True

    def _typed_applied(self, origin: str, before: list, typed) -> None:
        """ModOps typed in the XML view were applied: settings they read
        that nothing declares are offered as options, and the Apply is taken
        back where that offer is turned down -- the text then goes back in
        the pane as it was typed (:meth:`EditorPanel.typed_undone`)."""
        if self.output.propose_options(origin, before) is not False:
            return
        step = self.undo.last_step
        if step is not None and step.typed is typed:
            self.undo_last()
            self.status_label.setText(tr("Apply undone. Its new options were not declared."))

    def show_in_file(self, origin: str, kind: str, which) -> None:
        """Go to an op in its mod's file: ``kind`` is "op", "change" or
        "unread", and ``which`` its uid, its key or its place.  The file is
        shown, and where changes go is left alone."""
        if not self.open_tab(TAB_FILE):
            return
        panel = self.file_panel
        if not panel.look_at(origin):
            return
        panel.refresh()
        shown = panel.shown
        line = 0
        if shown is not None and kind == "op":
            line = shown.line_of_op(str(which))
        elif shown is not None and kind == "change":
            change = self.loadout.changes.get(tuple(which))
            if change is not None:
                line = shown.line_of_change(change.key) or shown.line_of_op(change.op_uid)
        elif shown is not None:
            entry = next(
                (one for one in self.loadout.unread_of(origin) if one.order == which), None
            )
            line = shown.line_of_unread(entry) if entry is not None else 0
        if panel.has_unapplied():
            self.status_label.setText(tr(
                "The file has text typed in it that is not applied, so its "
                "lines are not the mod's. Apply or Revert, then show it again."
            ))
        elif not panel.show_line(line):
            self.status_label.setText(tr("That is not in the file as a ModOp of its own."))

    def say_of_a_step(self, said: str) -> None:
        """What the editor has to say of the step it just made.

        It is about that step, as "Undid ..." is about its own, and is
        dropped the same way once another is made: see _drop_undo_message.
        """
        self.status_label.setText(said)
        self._undo_said = said

    # -- undo -----------------------------------------------------------------
    def record(self, label: str = "", redraw: bool = True):
        """Whatever the loadout did since the last step is a step of its
        own, and is returned where there was anything.  ``redraw`` off is
        for whoever draws the rows next anyway."""
        step = self.undo.commit(label)
        if step is not None:
            held = held_in(self.loadout, step)
            if held:
                # Work that no project could keep: taken back at once.
                self.undo.take_back()
                self._sync_undo_actions()
                self._after_undo(step, redone=False)
                self.status_label.setText(self._held_said(held[0]))
                self._undo_said = ""
                return None
            self._sync_undo_actions()
            self._drop_undo_message()
            # What it changed in a mod's file may be found differently by
            # the ops after it.
            self.readings.edited(step, redraw=redraw)
        return step

    def _held_said(self, origin: str) -> str:
        """Why an edit to ``origin``, a mod of yours, was taken back: its
        project cannot be written from this loadout."""
        name = self._project_name(origin)
        why = self.loadout.held(origin)
        if why == projects.MISSING:
            return tr("%s cannot be changed here: its project is missing, so nothing could keep "
                      "the change. Put the project back, and open the loadout again, to change "
                      "it again.", name)
        if why == projects.UNREADABLE:
            return tr("%s cannot be changed here: its project could not be read, so nothing "
                      "could keep the change.", name)
        return tr("%s cannot be changed until it is read again from its project, which "
                  "another loadout changed.", name)

    def forget_undo(self) -> None:
        """Start the history over from here -- see core.undo for what does."""
        self.undo.reset()
        self._sync_undo_actions()
        self._drop_undo_message()

    def _drop_undo_message(self) -> None:
        """What was last undone or redone is not the last thing done any more.

        An edit in the editor, an Apply in the XML view and a revert in the
        changes panel say nothing in the status bar themselves, so "Redid 6
        changes" stayed there through each of them, describing a step several
        steps back.  Only while it is still what the bar says: an action that
        has said something of its own keeps its sentence.
        """
        said, self._undo_said = self._undo_said, ""
        if said and self.status_label.text() == said:
            self.rest_status()

    def _sync_undo_actions(self) -> None:
        undo, redo = self.undo.undo_label, self.undo.redo_label
        self.undo_action.setEnabled(self.undo.can_undo)
        # Translators: %s names the step, e.g. "Undo typing 20".
        self.undo_action.setText(tr("&Undo %s", undo) if undo else tr("&Undo"))
        self.redo_action.setEnabled(self.undo.can_redo)
        # Translators: %s names the step, e.g. "Redo typing 20".
        self.redo_action.setText(tr("&Redo %s", redo) if redo else tr("&Redo"))

    def undo_last(self) -> None:
        step = self.undo.undo()
        if step is not None:
            self._after_undo(step, redone=False)

    def redo_last(self) -> None:
        step = self.undo.redo()
        if step is not None:
            self._after_undo(step, redone=True)

    def _after_undo(self, step, redone: bool) -> None:
        """Bring every view back in line with a loadout undo just rearranged."""
        typed = step.typed
        # Text the last undo put back in the XML view, and nobody typed in
        # since, is not held on to under another step.
        self.editor.xml.drop_put_back()
        if self.workspace is not None:
            self.workspace.forget_change_objects()
        self.files.mark_unsaved()
        self.update_tab_gates()
        # Before the rows are drawn: a step taken back is an edit as well.
        self.readings.edited(step, redraw=False)
        self.changes_panel.regroup()
        elsewhere = (
            step.profile is not None
            and step.profile is not self.loadout.chosen_profile()
            and (step.switches is not None or step.layers is not None)
        )
        if elsewhere and redone:
            said = tr("Redid %(step)s, in %(loadout)s.",
                      step=step.label, loadout=step.profile.name)
        elif elsewhere:
            said = tr("Undid %(step)s, in %(loadout)s.",
                      step=step.label, loadout=step.profile.name)
        elif redone:
            said = tr("Redid %s.", step.label)
        else:
            said = tr("Undid %s.", step.label)
        self.status_label.setText(said)
        # Until the next change to the mod: see _drop_undo_message.
        self._undo_said = said
        # An edit to an asset that is not the one on screen is shown, rather
        # than taken back somewhere out of sight -- but only from the editor:
        # undoing a switch flicked in the changes panel from the Export tab
        # should not pull you away from it.
        # An Apply in the XML view is shown on the asset it was typed on,
        # wherever what it changed is: an op typed there for another asset
        # opened that one, and the text was put back nowhere (H2-3).
        guid = (typed[1] if typed is not None else "") or step.asset()
        if (
            not guid
            or not self.showing(TAB_EDIT)
            or guid == self.editor.guid
            or not self.editor.load_asset(guid)
        ):
            self.editor.refresh_from_loadout()
        if typed is not None and not redone:
            self.editor.typed_undone(typed)
        self.editor.refresh_search()
        self.file_panel.mark_stale()
        self.mark_output_dirty()
        self.update_title()
        self._sync_undo_actions()

    # -- the asset in front of you -------------------------------------------
    def _on_editor_opened(self, guid: str) -> None:
        """The editor moved to ``guid``: the graph follows, now or on
        arrival -- unless it is pinned, and then not at all."""
        self.pending_in_editor = None
        if self.references.guid == guid or self.references.pinned:
            self.pending_in_refs = None
        elif self.showing(TAB_REFS):
            self.references.show_asset(guid)
        else:
            self.pending_in_refs = guid

    def _on_graph_moved(self, guid: str) -> None:
        """The graph moved to ``guid``: the editor follows when it is shown.

        Not straight away. Clicking through a graph is quick, and building the
        editor's tree for every asset passed through, on a tab nobody is
        looking at, would make each click wait on it.

        And not at all while both are showing.  There is no arrival then,
        and an editor that moved at every click would be that wait, on
        screen: it moves when it is asked to, by Edit this asset.  Nor for
        an editor in a window of its own, whatever the graph is doing: it
        is never arrived at, and what was kept for it would wait until it
        was moved back and its tab come to, and move it then.
        """
        self.pending_in_refs = None
        if (
            self.editor.guid == guid
            or self.panels.is_out(EDITOR)
            or (self.showing(TAB_EDIT) and self.showing(TAB_REFS))
        ):
            self.pending_in_editor = None
        else:
            self.pending_in_editor = guid

    def _graph_arrives(self) -> None:
        """The graph has come into view: whatever the editor moved to while
        it was not -- or, the first time, whatever is open there: the two
        are about one asset."""
        wanted = self.pending_in_refs or (
            self.editor.guid if self.references.guid is None else None
        )
        self.pending_in_refs = None
        if wanted and wanted != self.references.guid:
            self.references.show_asset(wanted)

    def _editor_arrives(self) -> None:
        """The editor has come into view: whatever the graph moved to
        while it was not."""
        wanted, self.pending_in_editor = self.pending_in_editor, None
        if wanted and wanted != self.editor.guid:
            with busy_cursor():
                opened = self.editor.load_asset(wanted)
            if not opened:
                self.status_label.setText(tr(
                    "The graph was on %s, which the asset editor cannot "
                    "open.", wanted,
                ))
        if self.editor.guid is None:
            # Nothing open yet, so the search field is where the user is
            # going -- the caret, at least. The list itself waits to be
            # asked for, by click, keystroke or Find.
            self.editor.focus_search(show_results=False)

    def _graph_and_editor_apart(self) -> None:
        """The graph or the editor went to a window of its own, or came
        back from one: whether the two are now in different windows.

        Apart, the graph can be held on its asset, and steps through a
        history of its own.  In one window it is the editor's other view
        again, and takes up the editor's history.  It is the same whichever
        of them is out, and with both out.
        """
        apart = self.panels.is_out(REFERENCES) or self.panels.is_out(EDITOR)
        # Before the pin is taken away: that it goes is not it being let go.
        self._moving_graph = True
        try:
            self.references.offer_pin(apart)
        finally:
            self._moving_graph = False
        self.references.walk_alone(apart)

    def _graph_moved_window(self, out: bool) -> None:
        """The graph went to a window of its own, or came back from one.

        Moving it back moves neither of them.  The graph stays on the
        asset it was on, pinned or clicked to, and the editor on its own:
        the graph went to the editor's asset once, and a window put back
        was then the one thing that had sent it there.  Each follows the
        other from the next asset either is moved to.
        """
        self._graph_and_editor_apart()
        if out:
            self._graph_arrives()
        else:
            self.pending_in_editor = self.pending_in_refs = None

    def _editor_moved_window(self, out: bool) -> None:
        """The editor went to a window of its own, or came back from one.

        Out, it has come into view, as its tab does when it is come to.
        Moving it back moves neither it nor the graph, and nothing is
        dropped: what the graph is owed for the assets opened while its tab
        was behind another, it still gets when that tab is come to.
        """
        self._graph_and_editor_apart()
        if out:
            self._editor_arrives()

    def _on_pin_changed(self, pinned: bool) -> None:
        """Let go, the graph moves to the asset the editor has open."""
        guid = self.editor.guid
        if not pinned and not self._moving_graph and guid and guid != self.references.guid:
            self._on_editor_opened(guid)

    def on_editing_changed(self) -> None:
        """A different mod is being written into: say so where it shows."""
        self.files.mark_unsaved()
        self.changes_panel.refresh_marks()
        self.file_panel.mark_stale()
        self.output.show_mod(self.loadout.editing_origin())
        self.mark_output_dirty()
        self.update_title()

    def on_activation_changed(self) -> None:
        """A change switched on or off: the mod changed, the change set did not."""
        self.files.mark_unsaved()
        self.editor.refresh_from_loadout()
        # A mod switched on or off here is the loadout's side of the fourth
        # card moving; the folders have not.
        self.refresh_truth(say=False, disk=False)
        self.mark_output_dirty()
        self.update_title()
        self.record()

    def _on_changes_removed(self) -> None:
        self.files.mark_unsaved()
        self.editor.refresh_from_loadout()
        # Reverted, or an op written as narrow ModOps: the mod's file says
        # something else now.
        self.file_panel.mark_stale()
        self.mark_output_dirty()
        self.update_title()
        self.record()

    def mark_output_dirty(self) -> None:
        self._output_dirty = True
        if self.tabs.currentIndex() == TAB_GENERATE:
            self._refresh_output()

    def _refresh_output(self) -> None:
        self.output.refresh()
        self._output_dirty = False

    def _on_tab_changed(self, index: int) -> None:
        # The tab of a panel that is out shows where the panel is, and the
        # panel's window comes forward. The keyboard stays with the tabs,
        # which somebody may only be walking along.
        if self.panels.bring_forward(TAB_PANELS.get(index), keyboard=False):
            return
        if index == TAB_SOURCE:
            # It says how much is in the loadout and what each way of working
            # would do next, and both move while you are on another tab.
            self.start.refresh_source_panel()
        elif index == TAB_GENERATE and self._output_dirty:
            self._refresh_output()
        elif index == TAB_REFS:
            self._graph_arrives()
        elif index == TAB_EDIT:
            self._editor_arrives()

    def _focus_search(self) -> None:
        # The editor's search box, so it is the same move as carrying on into
        # the editor -- gate and all. Asked for by name, so unlike the other
        # ways in, this one brings the result list down with it.
        self._go_to_editor(show_results=True)

    def _find_in_asset(self) -> None:
        """Find inside the asset the editor has open -- and with none open,
        the search for one, which is the only thing there is to find.  In
        the file of a mod, where that is what is in front.

        In front is where the keyboard is, of the two.  With it in
        neither, as in the Mod changes panel, it is the main window's tab:
        a file in a window of its own is found in from that window.
        """
        at = QApplication.focusWidget()
        in_file = at is not None and (
            at is self.file_panel or self.file_panel.isAncestorOf(at)
        )
        in_editor = at is not None and (at is self.editor or self.editor.isAncestorOf(at))
        if in_file or (not in_editor and self.tabs.currentWidget() is self.file_panel):
            self.file_panel.open_find()
            return
        if not self.open_tab(TAB_EDIT):
            return
        if not self.editor.find_in_asset():
            self.editor.focus_search(show_results=True)

    # -- opening a loadout, or starting one ----------------------------------
    def new_loadout(self) -> bool:
        """Start over.  Returns whether it actually happened."""
        if not self.files.confirm_discard():
            return False
        self.reset_loadout()
        return True

    def reset_loadout(self) -> None:
        """Empty the loadout, keeping what it is measured against.

        Split from new_loadout() because the question and the act belong at
        different moments when the reason for starting over is a game switch:
        agreeing to lose the work is answered up front, and losing it waits
        for the files that are replacing it. See game_files.confirm_game_switch.
        """
        source = self.loadout.source_path
        game = self.loadout.game
        archive = self.loadout.base_archive
        assets = self.loadout.base_assets
        fresh = Loadout(
            source_path=source, game=game, base_archive=archive, base_assets=assets,
            base_fingerprint=self.loadout.base_fingerprint,
            base_update=self.loadout.base_update,
            base_game_version=self.loadout.base_game_version,
        )
        self.loadout.__dict__.update(fresh.__dict__)
        self.loadout.own.creator = self.preferences.creator_name()
        self.preferences.hand_over(self.loadout)
        self.loadout_path = None
        self.on_disk = False
        self.look_for_missing_mods = False
        self.forget_the_loadout_before()
        self.files.mark_saved()
        self.output.load_loadout_fields()
        self.changes_panel.refresh()
        self.file_panel.mark_stale()
        self.start.refresh_source_panel()
        self.update_title()

    def forget_the_loadout_before(self) -> None:
        """Let go of what was held about the loadout that has given way to
        another: a new one, or one opened.

        One list for both. Kept as two, they were not the same: *New loadout*
        left the loader's verdict on the loadout before, what its unread ops
        were found to do and a reading waiting to be taken, each offered
        again over a loadout it was never asked of (audit finding A8-18).
        ``tests/test_fresh_window.py`` holds this to what a window just made
        has.
        """
        self.profile_renames.clear()
        self.forget_undo()
        # What the game loads was worked out for the loadout before; it is
        # worked out again for this one.
        self.truth = None
        self.changes_panel.found = None
        # What the status bar said was said of the loadout that has gone:
        # what Play would do with it stayed up over no loadout at all.
        self.rest_status()
        # What the game's loader said is about the loadout it was asked of.
        self.loader_check.last_loadout_check = None
        self.output.forget_loader_verdicts()
        self.unread_effects = {}
        self.readings.adopted()
        self.patches.forget_the_loadout_before()
        self.start.forget_the_loadout_before()
        # The asset open was open in the loadout before, and what was typed
        # to find it. Done here for a loadout opened however it is opened:
        # one that brings its own game files was taken up by another way,
        # which left the search box as it was (audit finding A8-15).
        self.close_asset()

    def close_asset(self) -> None:
        """No asset open, in the editor or the graph, and no trail back to one.

        Where a loadout starts.  The asset open in the one before used to
        stay, shown again against the new loadout: after File > New loadout
        the XML view had it under a tab named "My Anno Mod", a mod nobody had
        made, and went on saying so once a mod was read in and the loadout
        was that mod.  The same as for new game files: see
        game_files.adopt_index.
        """
        self.pending_in_editor = self.pending_in_refs = None
        self.editor.clear()
        # And what was searched for to find it: the GUID typed for the last
        # loadout's asset stayed in the box over "No asset selected".
        self.editor.search.forget_search()
        self.references.clear()

    def _base_file_for(self, loaded):
        """The assets.xml a loadout being opened needs, or None if it is loaded.

        Asked *before* the loadout is adopted, because it is a question about
        what is open now: once ``loadout.source_path`` has been overwritten with
        the saved one there is nothing left to compare against.
        """
        wanted = Path(loaded.source_path) if loaded.source_path else None
        if wanted is None:
            return None
        have = Path(self.loadout.source_path) if self.loadout.source_path else None
        if self.index is not None and have is not None and have == wanted:
            return None
        return wanted

    def adopt_loadout(self, path: Path, loaded) -> None:
        """Take ``loaded`` as the loadout being worked in.

        Its own step, because *when* it happens is the whole point.  A loadout
        names the base file it was built against, and until that file is
        indexed there is nothing for its changes to mean: the GUIDs they name
        are in it, and the game it says it is for decides the dialect every one
        of them exports in.  Taking the loadout up first and hoping the index
        followed left the Studio holding one game's work over another game's
        files whenever it did not -- a build cancelled at the progress dialog,
        or one that failed -- with only a line in the status bar to say so.
        """
        self.settings.setValue("last_loadout_dir", str(path.parent))
        self.loadout_path = path
        self.on_disk = True
        self.files.remember_recent(self.loadout_path)

        self.loadout.__dict__.update(loaded.__dict__)
        # Before anything looks in the folders it names.
        self.check_named_folders()
        # Before anything asks whose its mods are.
        self.preferences.hand_over(self.loadout)
        # And nothing held or said of the loadout before stays over this one.
        self.forget_the_loadout_before()
        self.patches.find_lost_patches()
        self.patches.settle_home()
        self.patches.off_a_bundled_copy()
        self.files.mark_saved()
        self.library_controller.follow_conversions()
        self.library_controller.follow_renames()
        self.look_for_missing_mods = True
        self.scan_elsewhere()
        # One another loadout changed is read again as it is now, which the
        # reading says when it lands (core/readback.py): only the projects
        # that cannot be read are said here, first, naming where they were
        # looked for.
        refs = self.loadout.projects
        held = [(origin, why, where) for origin, why, where in self.loadout.held_projects()
                if why != projects.MOVED]
        if held:
            self.prompts.report_held_projects([
                (self._project_name(origin), why, where, refs[origin].standing_in)
                for origin, why, where in held
            ])
        found = [(origin, ref) for origin, ref in refs.items() if ref.lost]
        if found:
            self.status_label.setText(" ".join(
                tr("The project of %(name)s was not at %(was)s, and was found by its ModID at "
                   "%(now)s.", name=self._project_name(origin), was=ref.lost,
                   now=projects.where(ref.path))
                for origin, ref in found
            ))

    def check_named_folders(self) -> None:
        """Take the mods folder the loadout just opened names only where
        this PC knows it, or you agree to it; else the one this PC would use
        anyway.  Then keep each mod's folder only where a mod's may be
        (:meth:`.where.Where.folder_roots`).  A loadout file can come from
        anybody, and Play switches the mods in its folder and builds into
        it (security review N1)."""
        loadout = self.loadout
        named = (loadout.mods_folder or "").strip()
        if named and not self.where.knows_mods_folder(named):
            loadout.mods_folder = ""
            instead = self.where.mods_folder()
            if Path(named).is_dir() and self.prompts.ask_use_mods_folder(named, instead):
                self.where.know_mods_folder(named)
                loadout.mods_folder = named
            else:
                loadout.mods_folder = instead
        forgotten = keep_folders_inside(loadout, self.where.folder_roots())
        if forgotten:
            self.status_label.setText(trn(
                "%(mods)s is in a folder outside the mods folders and the library, so it "
                "is not read from there.",
                "%(mods)s are in folders outside the mods folders and the library, so they "
                "are not read from there.",
                len(forgotten), mods=listing(forgotten),
            ))

    def _project_name(self, origin: str) -> str:
        """What a project's mod is called, for saying which project."""
        return self.loadout.label_of(origin) or "My Anno Mod"

    def load_loadout(self, path=None, asked: bool = False) -> bool:
        """Open a saved loadout, asking which one unless told.

        ``asked`` is that the caller has already asked what to do about
        unsaved work in the loadout open now, so it is not asked twice.

        Returns whether one is open now, so the Start tab's list knows whether
        the space it is showing has changed.  False when the base file it needs
        is still being indexed: it opens when that lands, and does not if the
        build is cancelled or fails.
        """
        if not asked and not self.files.confirm_discard():
            return False
        chosen = str(path) if path else ""
        if not chosen:
            start = self.remembered("last_loadout_dir", str(self.files.loadouts_dir()))
            chosen, _f = QFileDialog.getOpenFileName(
                self, tr("Open loadout"), start,
                tr("Anno Modding Studio loadouts (*.annoload);;All files (*)"),
            )
        if not chosen:
            return False
        try:
            loaded = Loadout.load(Path(chosen))
        except Exception as exc:  # noqa: BLE001 - whatever is wrong with the file is shown
            QMessageBox.critical(self, tr("Could not open"), str(exc))
            return False
        # Which assets.xml this loadout needs, decided before anything is
        # mutated, because the answer depends on what is loaded *now*.
        wanted = self._base_file_for(loaded)
        if wanted is not None and self.index is not None:
            if not self.prompts.confirm_base_swap(loaded, wanted, self.loadout):
                return False
        # Its own file gone and the same game open, and agreed to: it opens
        # against the file open now, and says so from then on (A11-103).
        repointed = wanted is not None and self.index is not None and not wanted.is_file()
        if repointed:
            for name in (
                "source_path", "game", "base_archive", "base_assets",
                "base_fingerprint", "base_update", "base_game_version",
            ):
                setattr(loaded, name, getattr(self.loadout, name))

        bringing_its_own = wanted is not None and wanted.is_file()
        if bringing_its_own and self.index is None:
            answer = ask(
                self,
                tr("Load the base file too?"),
                tr("This loadout was built against:\n%s\n\nLoad it now?",
                   wrappable_path(wanted)),
                tr("&Load it"),
                cancel=tr("Open &without it"),
            )
            # Declining opens the loadout on its own, against nothing -- which
            # is a thing to want, and says so plainly on the Start tab.
            bringing_its_own = answer

        if bringing_its_own:
            # The loadout already knows its provenance; don't overwrite it.
            self.game_files.pending_archive = loaded.base_archive
            # Handed over rather than opened here: game_files.adopt_index() takes it up
            # once the file is actually indexed, and _on_index_failed() lets it
            # go if that never happens.  Both games have already been named to
            # the user by _confirm_base_swap(), so the guard inside
            # open_assets() has nothing left to ask -- but what a switch costs
            # is still paid, as it is there.
            self.game_files.pending_loadout = (Path(chosen), loaded)
            if self.index is not None and games.detect_from_path(wanted) != self.loadout.game:
                self.game_files.leave_the_game()
            self.game_files.open_assets(wanted, asked=True)
            if self.game_files.pending_loadout is not None:
                # Either the build is running, and the loadout is taken up when
                # it lands, or open_assets() turned back at the door -- in which
                # case nothing is coming and nothing staged for it may be left
                # waiting.
                worker = self.game_files.worker
                if worker is None or not worker.isRunning():
                    self.game_files._forget_pending_load()
                return False
        else:
            self.adopt_loadout(Path(chosen), loaded)
            if repointed:
                # What is on disk still names the file that is gone.
                self.files.mark_unsaved()
            self.output.load_loadout_fields()
            self.changes_panel.refresh()
            # The Mod file tab too: it is another loadout's mods now.
            self.file_panel.mark_stale()
        self.start.refresh_source_panel()
        self.mark_output_dirty()
        self.update_title()
        self.modio.offer_missing_mods()
        self.refresh_truth()
        return True

    # -- mods elsewhere on this PC --------------------------------------------
    def scan_elsewhere(self, announce: str = "") -> bool:
        """Which mods on this PC create each GUID, in the background, so a
        mod read into the loadout is checked against every other mod here and
        not only the ones loaded with it. ``announce`` is a mod just imported,
        to say so in the status bar if the scan finds any of its GUIDs taken.
        Whether a scan was started; one already running is asked to run again.
        """
        from .workers import ElsewhereWorker

        if announce:
            self._announce_elsewhere = announce
        if not self.loadout.game or self.index is None:
            return False
        # Off in the test suite, as the loader is: see tests/_isolation.py.
        if os.environ.get("ANNO_STUDIO_ELSEWHERE", "").strip().lower() == "none":
            return False
        if self._elsewhere_worker is not None:
            self._elsewhere_again = True
            return False
        self._elsewhere_again = False
        worker = ElsewhereWorker(
            self.loadout.game,
            [self.where.mods_folder(), self.where.game_mods()],
            self.where.library_dir(),
            self.output.export_target(),
            self,
        )
        worker.succeeded.connect(self._on_elsewhere)
        worker.finished.connect(worker.deleteLater)
        self._elsewhere_worker = worker
        self.background.begin(
            "elsewhere", tr("Checking the other mods on this PC for the same GUIDs…")
        )
        worker.start()
        return True

    def _on_elsewhere(self, owners) -> None:
        self._elsewhere_worker = None
        self.background.end("elsewhere")
        if self._elsewhere_again:
            self.scan_elsewhere()
            return
        self.changes_panel.set_elsewhere(owners)
        origin = self._announce_elsewhere
        self._announce_elsewhere = ""
        if origin:
            taken = self.changes_panel.elsewhere_of("" if origin == "<yours>" else origin)
            if taken:
                self.status_label.setText(trn(
                    "%(n)s GUID the imported mod creates is made by other mods on this PC "
                    "as well: played together, the game finds only one of them. Its row in "
                    "the changes list says which.",
                    "%(n)s GUIDs the imported mod creates are made by other mods on this PC "
                    "as well: played together, the game finds only one of each. Its row in "
                    "the changes list says which.",
                    len(taken),
                ))

    # -- what the game will load, against this loadout ------------------------
    def refresh_truth(self, say: bool = True, disk: bool = True, inventory=None) -> None:
        """Ask the disk what the game will load and show it beside the loadout.

        ``say`` puts the sentence in the status bar too, when the sides do not
        agree; an action that has just said what it did passes False, so its
        own sentence stays.  ``disk`` False asks only the loadout's side again,
        over the folders as they were last looked at -- for a switch flipped
        in the changes panel, which moves the counts without moving a file.

        Not on a timer and not on a watcher: the answer moves when the Studio
        moves it -- an install, a folder read, a switch -- or when a loadout
        is opened, and each of those calls this.  Under two seconds for a
        full folder, behind a wait cursor.

        ``inventory`` is the folders as the caller has just read them, when
        nothing has moved since: Play reads them to plan, and reading them a
        second and a third time to say the same thing was most of a click.
        """
        # What the mods that load call things moves with the same things.
        self.game_files.refresh_mod_names()
        folder = self.where.mods_folder()
        if self.index is None or not folder or not (self.loadout.layers or self.loadout.aim):
            self.truth = None
            self.changes_panel.found = None
            self.source.set_truth(None)
            self.convert_action.setEnabled(self.index is not None)
            return
        held = self.truth.installed if (not disk and self.truth is not None) else None
        if inventory is not None:
            held = inventory
        try:
            with busy_cursor():
                self.truth = truth.read(
                    self.loadout, folder, in_folders=held, library_dir=self.where.library_dir(),
                )
                live = self.live.live_loadout()
                self.truth.live = self.live.live_name(live) if live else ""
                here = self.live.live_profile_here()
                self.truth.is_live = here is not None and here == self.loadout.chosen_profile().name
                if len(self.loadout.profiles) > 1:
                    self.truth.playing = self.loadout.chosen_profile().name
                if self.truth.is_live:
                    self.live.check_the_log(self.truth)
                self.truth.left_in_cache = unsubscribe.left_in_cache(self.truth)
                taken = repeats.find(self.loadout, self.index)
                self.truth.repeats = self.live.repeats_playing(taken)
                self.truth.repeated = len(repeats.playing(taken))
        except OSError as exc:
            self.truth = None
            self.status_label.setText(tr("Could not look at the mods folder: %s", exc))
        self.source.set_truth(self.truth, library_dir=self.where.library_dir())
        # With nothing read there is a loadout to start first, which the
        # conversion offers. With one read, it is for what is left to do.
        self.convert_action.setEnabled(self.truth is None or bool(self.library_controller.waiting()))
        # For the mod rows' tooltips, next time the list is drawn.
        self.changes_panel.found = self.truth
        if say and self.truth is not None and not self.truth.matches:
            self.status_label.setText(self.truth.describe())

    def bring_mods_into_loadout(self) -> bool:
        """Read the folder in, so the loadout says what the game does."""
        if self.truth is not None:
            self.settings.setValue(self.mods_key("last_mods_folder"), str(self.truth.folder))
        done = self.load_installed_mods()
        self.refresh_truth()
        return done

    # -- misc ---------------------------------------------------------------
    def rest_status(self) -> None:
        """The status bar with nothing to report: the game files that are
        loaded.  What an action said stays until the next one says something;
        this is for when what it was said of has gone."""
        self.game_files._set_status(self.index.text_count if self.index is not None else 0)

    def holds_something(self) -> bool:
        """Whether the loadout holds anything: a mod read in, a change or a
        ModOp, its own mod made, or a card chosen on the Start tab.  With no
        loadout file, that is a new loadout; with none of it, no loadout."""
        loadout = self.loadout
        return bool(
            loadout.layers or loadout.changes or loadout.ops or loadout.own_mod or loadout.aim
        )

    def update_title(self) -> None:
        """The mod being written, and what else is open alongside it.

        The two are worth keeping apart in the one place that is always on
        screen: the loadout *is* your mod, and the mods loaded next to it are
        cargo -- so the title names yours and counts theirs rather than adding
        them up into one number that means neither.
        """
        bits = [studio_title()]
        # The loadout first: which space you are in is the question every other
        # number here is an answer about, and it used to be the one thing the
        # window never said.
        if self.loadout_path is not None:
            bits.append(
                self.files.loadout_name() if self.on_disk
                # Translators: a loadout not yet saved to a file of its own.
                else tr("%s (new)", self.files.loadout_name())
            )
        else:
            # Mods read from the File menu with no card chosen are a loadout
            # all the same, with no file yet: "No loadout — 427 mods loaded"
            # said there was nothing while it counted what there was (D8).
            bits.append(tr("New loadout") if self.holds_something() else tr("No loadout"))
        if self.index is not None:
            bits.append(games.get(self.loadout.game).label)
        # The mod being written: the loaded mod edits go to, a patch or a mod
        # of yours, as *Currently editing* has it (EditorPanel._targets), or
        # otherwise the loadout's own.  A rule of its own here named only a
        # patch, and the loadout's own mod while a fork or a mod of yours
        # kept here was being written.
        home = self.loadout.home_layer()
        target = self.loadout.layer(self.loadout.editing_origin())
        if target is not None and (target.patches or patching.is_mine(self.loadout, target)):
            name = target.label
        elif self.loadout_path is not None and (
            self.loadout.has_own_mod() or not self.loadout.layers
        ):
            name = self.loadout.own.name or tr("Untitled")
        elif home is not None:
            name = home.label
        else:
            # Nobody's mod to name -- and with no loadout, none at all: "No
            # loadout" used to be followed by "My Anno Mod", the name a mod
            # would start with if somebody made one (see Loadout.own_mod).
            # A mods folder read from the File menu with no loadout makes
            # that mod, and the title said it again (H2-24).
            name = ""
        # The mark is exactly what closing the window will ask about -- see
        # unsaved_work(), which both this and _confirm_discard() call.
        unsaved = self.files.unsaved_work()
        if name:
            bits.append(name + (" *" if unsaved else ""))
        elif unsaved:
            # No mod of yours to name yet, so the unsaved mark goes on the
            # loadout itself, and not on the game named after it.
            bits[1] += " *"
        # Changes, as the Mod changes panel counts them, and not the ops typed
        # here counted beside what they come to: see Loadout.changes_made.
        # With mods read in beside yours it is not every change in that list,
        # so it says whose it counts.
        mine = self.loadout.changes_made
        if mine and self.loadout.layers:
            bits.append(trn("%(n)s change of yours",
                            "%(n)s changes of yours", mine))
        elif mine:
            bits.append(trn("%(n)s change",
                            "%(n)s changes", mine))
        # Yours counted apart from theirs: a patch, a fork or another mod of
        # your own is not a mod you installed, and rolling them into one
        # number means neither.
        yours = sum(1 for layer in self.loadout.layers if self.loadout.yours(layer))
        loaded = len(self.loadout.layers) - yours
        if loaded and yours:
            bits.append(trn("%(n)s mod loaded · %(yours)s yours",
                            "%(n)s mods loaded · %(yours)s yours",
                            loaded, yours=number(yours)))
        elif loaded:
            bits.append(trn("%(n)s mod loaded",
                            "%(n)s mods loaded", loaded))
        elif yours:
            bits.append(trn("%(n)s mod of yours",
                            "%(n)s mods of yours",
                            yours))
        self.setWindowTitle("  —  ".join(bits))
        self.editor.refresh_target()
        # Whether there is anything to unload changes with every one of these
        # calls, and greying the action out is the only way "nothing loaded"
        # is visible before clicking it and finding out.
        self.unload_action.setEnabled(any(
            layer.origin != self.loadout.home for layer in self.loadout.layers
        ))
        self.collection_off_action.setEnabled(bool(self.loadout.collections()))
        self.collection_out_action.setEnabled(bool(self.modio.collections_that_brought()))

        self.updates_action.setEnabled(bool(self.loadout.layers))
        self.fork_action.setEnabled(self.loadout_path is not None)
        self._show_play()
        # The same two questions the Mod changes panel puts a button up for,
        # under the same conditions: one mod alone can contest nothing, and a
        # dialog listing no unread ops is a dialog that wasted a click.
        self.conflicts_action.setEnabled(len(self.loadout.layers) > 1)
        self.unread_action.setEnabled(bool(self.loadout.not_read()))
        self.loadout_check_action.setEnabled(bool(self.loadout.layers) and self.index is not None)

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def _about(self) -> None:
        AboutDialog(self).exec()

    def show_folders(self) -> None:
        """Help > Studio folders."""
        FoldersDialog(self.where.places(), self.loadout.game, self).exec()

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
            self._keep_room_under_maximised()
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)
        split = self.settings.value("splitterState")
        if split:
            self.splitter.restoreState(split)
            # The width to open the panel at, and to bring it back at from a
            # window of its own: read from the state, since the splitter is
            # not laid out until the window is shown (H2-29).
            self._changes_width = _side_width(split) or self._changes_width
        # Where the changes list's own divider was left. Remembered for the
        # same reason the splitter is: it is a proportion the user set by hand,
        # and starting over at the default every launch is what made the old
        # fixed column feel fixed.
        share = self.settings.value("changesColumnShare")
        if share:
            self.changes_panel.tree.set_share(share)
        if str(self.settings.value("changesFolded", "") or "") == "yes":
            # The splitter is remembered opened so that the width read above
            # is the one to open the panel at again.
            self.toggle_changes.setChecked(False)
        if str(self.settings.value("fileOutlineFolded", "") or "") == "yes":
            self.file_panel.toggle_outline.setChecked(False)
        self.editor.set_view(str(self.settings.value("editorView", "") or ""))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Once this window is up: a window that belongs to it is opened
        # over it, and not before there is anything to be over.
        if not self._panels_restored:
            self._panels_restored = True
            self._restoring_panels = True
            try:
                self.panels.restore()
            finally:
                self._restoring_panels = False
            # The gates were set with every panel in.
            self.update_tab_gates()

    def _keep_room_under_maximised(self) -> None:
        """A window remembered as maximised keeps a size worth restoring to.

        Under a maximised window is the size it goes back to.  Dragged down
        to the smallest the window allowed and then maximised, that size is
        remembered with it, and is smaller than the window allows once this
        launch's layout is in.  Qt then resizes the window to fit, maximised
        or not: it opened as a small window in the corner of the screen that
        said it was maximised.
        """
        if not self.isMaximized():
            return
        state = self.windowState()
        room = OPENING_SIZE.boundedTo(self.screen().availableSize())
        wanted = self.size().expandedTo(room)
        if wanted != self.size():
            # Resizing is taken as leaving the maximised state.
            self.resize(wanted)
            self.setWindowState(state)

    def closeEvent(self, event) -> None:
        if not self.files.confirm_discard():
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("splitterState", self.splitter.saveState())
        self.settings.setValue(
            "changesColumnShare", "%.4f" % self.changes_panel.tree.share()
        )
        # Which panels are out and where, and then each back in here: the
        # splitter is remembered with the panel in it, and a panel that is
        # out comes back open, so it is not also remembered as folded.
        self.panels.remember()
        self.panels.put_all_back()
        # Opened first, so that the splitter is remembered with the panel in it.
        folded = not self.toggle_changes.isChecked()
        self.toggle_changes.setChecked(True)
        self.settings.setValue("splitterState", self.splitter.saveState())
        self.settings.setValue("changesFolded", "yes" if folded else "no")
        self.settings.setValue(
            "fileOutlineFolded", "no" if self.file_panel.toggle_outline.isChecked() else "yes"
        )
        self.game_files.stop_texts_worker()
        # A loader check or the probes may be running; neither outlives the
        # window, and the index they read is about to close.
        self.output.stop_loader_check()
        self.loader_check.stop_play_checks()
        probing = self.loader_check.probe_worker
        if probing is not None:
            probing.wait(30000)
        scanning = self._elsewhere_worker
        if scanning is not None:
            scanning.wait(30000)
        self.readings.stop()
        # Asking the Marketplace takes at most its timeout; the answer is
        # only kept for next time.
        asking = self.loader_check.newest_worker
        if asking is not None:
            asking.wait(20000)
        self.studio_release.stop()
        if self.index is not None:
            self.index.close()
        event.accept()
