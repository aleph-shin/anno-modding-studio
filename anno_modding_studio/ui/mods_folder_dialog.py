"""Open a whole mods folder at once, and see what the game will actually load.

One mod at a time answers "what does this mod do".  A folder at a time answers a
different and more useful question: what will my game do, with everything I have
installed, and where do two of them reach for the same thing.

The mods are read as one stack, in the order the game loads them, rather than
one at a time -- see :func:`core.reader.analyse_all`.  That matters more than it
sounds: a mod that edits an asset another mod invents is perfectly ordinary, and
read on its own it looks broken.

Nothing is applied until this has said, per mod, how much of it was read.  A mod
whose ModOps the Studio cannot express is not a mod the Studio should pretend to
have loaded, so those are counted in their own column rather than rounded away.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import (
    builds,
    dependencies,
    games,
    infotips,
    library,
    loaderlog,
    modfolder,
    modinfo,
    modops,
    modzip,
    reader,
    scripts,
    switches,
)
from ..core import settings as mod_settings
from ..core.changes import ModLayer, is_own_mod
from ..core.i18n import join_list, number, tr, tr_noop, trn
from . import theme
from .help_tip import KEEP_INERT_HELP, HelpTip
from .mod_options_dialog import ModOptionsDialog
from .widgets import (
    ElideItemDelegate,
    HeaderTick,
    add_detours,
    enter_activates,
    keep_size,
    least_size,
    sort_as,
    sortable,
    tab_as_laid_out,
    ticks_state,
    wrappable_path,
)
from .workers import ModsWorker

COLUMNS = (
    "", tr_noop("Mod", context="column"), tr_noop("From", context="column"),
    tr_noop("In game", context="column"), tr_noop("Loads", context="column"), "ModOps",
    tr_noop("Changes", context="column"), tr_noop("Not read", context="column"),
    tr_noop("Options", context="column"), tr_noop("Built on", context="column"),
)
# On a row of the library's that is listed and not read: the name its link
# goes by.
SHELF_ROLE = Qt.ItemDataRole.UserRole + 1
# On a row of a mod the loadout has already, in a read that adds to it: its
# tick is not the user's to take away.
HELD_ROLE = Qt.ItemDataRole.UserRole + 2
# On a row of a mod that was read: whether the dialog would have ticked it
# itself, so a tick that differs is known for the user's.
OWN_TICK_ROLE = Qt.ItemDataRole.UserRole + 3
# On a row of a mod bundled inside another that is listed: the origin of the
# mod whose switch it answers to (Loadout.switch_of), whose tick it has.
HOST_ROLE = Qt.ItemDataRole.UserRole + 4
# On a row of a mod whose assets.xml patch read as nothing: it would come in
# as an empty heading, so it starts unticked and ticking them all leaves it out.
EMPTY_ROLE = Qt.ItemDataRole.UserRole + 5
# On a row of the folder's that is listed and not read, in a list opened on
# the loadout's own mods: the name of its top-level folder, bare.
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 6


class _Kept:
    """A mod the loadout has, in a list opened on what the loadout holds:
    what the list shows of its read, taken from the layer that read made
    and the changes and ops it brought.  Answers as a :class:`reader.ModRead`
    does, as far as the list asks one."""

    def __init__(self, layer: ModLayer, changes, ops) -> None:
        self.origin = layer.origin
        self.folder = Path(layer.folder) if layer.folder else None
        self.order = layer.order
        self.changes = list(changes)
        self.unread = layer.unread
        metadata = {
            "mod_name": layer.name, "mod_id": layer.mod_id, "creator": layer.creator,
            "declared": dict(layer.declared),
        }
        if layer.offered:
            metadata["options"] = mod_settings.from_dict(layer.offered)
        ops = list(ops)
        # The read's count of its ModOps is not the loadout's of the ops it
        # keeps: left blank until the folder is read.
        self.ops = None
        self.report = SimpleNamespace(
            metadata=metadata,
            # A mod that brought nothing is said to have no patch: "none".
            # One whose ops were all unread had one, and read as nothing.
            patch_files=bool(self.changes or ops or layer.unread),
            ops=ops, game_hint="", options_read=dict(layer.options),
        )

    @property
    def name(self) -> str:
        return self.report.metadata.get("mod_name") or self.origin


def standing_of(loadout) -> list:
    """The loadout's mods as the list shows them before the folder is read
    again: one :class:`_Kept` a layer, in load order."""
    changes, ops = {}, {}
    for change in loadout.changes.values():
        if not is_own_mod(change.origin) and not change.authored:
            changes.setdefault(change.origin, []).append(change)
    for op in loadout.ops:
        ops.setdefault(op.origin, []).append(op)
    return [
        _Kept(layer, changes.get(layer.origin, ()), ops.get(layer.origin, ()))
        for layer in sorted(loadout.layers, key=lambda layer: layer.order)
    ]


def _holds_a_mod(folder: Path) -> bool:
    """Whether a folder at the top of a mods folder is a mod or holds one:
    mod.io's numbered download folders hold theirs one level down."""
    if modzip.is_mod_folder(folder):
        return True
    try:
        return any(child.is_dir() and modzip.is_mod_folder(child) for child in folder.iterdir())
    except OSError:
        return False


def _named_in(folder: Path) -> dict:
    """What a folder at the top of a mods folder says of its mod: its own
    modinfo's, or the first one a level down."""
    found = modfolder.metadata_of(folder)
    if found:
        return found
    try:
        inside = sorted(child for child in folder.iterdir() if child.is_dir())
    except OSError:
        return {}
    for child in inside:
        found = modfolder.metadata_of(child)
        if found:
            return found
    return {}


class ModsFolderDialog(QDialog):
    """Read every mod in a folder, then choose which of them to load.

    ``layers`` and ``changes`` hold what the user settled on once the dialog is
    accepted.

    ``focus`` is the folder names to start ticked, when the read is about some
    of the folder rather than all of it -- the mods a collection has just put
    there.  Everything else is listed, unticked: the read is still of the
    whole folder, since that is what the game loads, and what is not ticked
    is one tick away.  ``provenance`` says which collection brought a
    folder, and ``listed`` every collection that lists it, for the column
    that says so.

    Whether each mod comes in on is the folder's say -- a read is an
    observation -- except for two things the caller knows better.
    ``arrived`` is the folders just installed, which come in on whatever the
    live profile says of them: installing writes no profile, and a mod
    installed to be off was not what anybody asked for.  ``keep`` is the
    switches the loadout already has, by origin, for a read after an install:
    the folder's switches are the live loadout's, and a loadout that is not
    live must not take them over its own.

    ``library_dir`` makes it a read of the library and not of the folder: the
    mods the library holds or stands for, the ones with no link in the mods
    folder among them.  ``picked`` narrows the read to the ones of those
    names, which are the loadout, so each comes in on; the rest are the
    ``shelf``.

    ``adding`` is library mods (:class:`core.library.Held`) read beside the
    folder: the ones with no link in it are read where the library keeps
    them, named as their links would be, as a read of the library names them.
    The loadout's own library mods that are switched off come this way.

    ``shelf`` is library mods listed by name and not read: the rest of the
    library.  Reading every mod a full library holds before anything could be
    ticked was a wait, so each is read once it is ticked -- read again with
    the rest, since mods are read as one stack -- and comes in on.  The ticks
    already made are kept across that read.

    ``standing`` (:func:`standing_of`) opens the list on what a loadout has
    already read, to add to it: its mods are rows as they were read, ticked
    and kept, and the folder's other mods are listed by name.  Nothing is
    read until a mod is ticked to add -- then the folder is, once, with the
    library's mods besides, so no tick after it reads again (D3).
    """

    def __init__(
        self, index, parent=None, start_dir: str = "", game: str = "",
        focus=None, provenance=None, arrived=None, keep=None, listed=None,
        base_fingerprint: str = "", base_update: str = "", library_dir=None, picked=None,
        adding=None, shelf=None, standing=None, detours=(),
    ):
        super().__init__(parent)
        self.index = index
        # Other ways to the mods this adds, each a button: see add_detours.
        self._detours = tuple(detours)
        self.library_dir = Path(library_dir) if library_dir else None
        self.picked = {str(name) for name in picked} if picked is not None else None
        self.adding = list(adding or ())
        # Listed and not read yet, by the name each one's link goes by.
        self.shelf = {mod.name: mod for mod in shelf or ()}
        # Of a list opened on the loadout's own mods: those mods, as they
        # were read, until the folder is read again; and the folder's other
        # mods, listed by name -- each one's top-level name -> (what its
        # modinfo says, its folder).
        self._standing = list(standing) if standing is not None else None
        self._unread_folder = {}
        # The library's mods it listed by name and read with the folder
        # without a tick, by the names their links go by, bare.
        self._besides = set()
        # The ticks as they were before the folder was read again, by
        # origin, so no read of the same folder undoes them: taken from the
        # list at each read, and dropped with the folder they were made in.
        self._kept_ticks = {}
        self._ticked_in = None
        self._read_again = False
        self._shelf_timer = QTimer(self)
        self._shelf_timer.setSingleShot(True)
        self._shelf_timer.setInterval(0)
        self._shelf_timer.timeout.connect(self._read_shelved)
        # The library's mods ticked all at once, by the names their links go
        # by: still on the shelf, and read when the list is imported.  Ticking
        # every mod a library holds was a read of all of them, half a minute
        # and more, before one of them could be unticked again.
        self._waiting = set()
        # Import was pressed with some of them waiting: once they are read.
        self._import_when_read = False
        # Of a library read: the mods with no link in the mods folder -- the
        # folder each is kept in -> the name its link goes by -- and where
        # each one's link would be, by origin.
        self._unlinked = {}
        self._links = {}
        # The game data loaded now, which a mod the Studio built is compared
        # with -- see core.builds.built_on_other_data.
        self.base_fingerprint = base_fingerprint
        self.base_update = base_update
        self.focus = {str(name) for name in focus} if focus else None
        self.arrived = {str(name) for name in arrived or ()}
        self.keep = dict(keep or {})
        self.provenance = dict(provenance or {})
        self.listed = {str(key): list(value) for key, value in (listed or {}).items()}
        self.game = game or games.detect_from_path(
            getattr(index, "source_path", "")
        )
        self.session = None
        # What the folder itself says is switched on, which is not the same
        # question as which mods to read: a mod that is installed and off is
        # still part of the list, and still one tick from being on again.
        self.folder_switches = None
        # Every folder the game loads mods from, the chosen one first.
        self.roots = []
        self.layers = []
        self.changes = []
        self.ops = []
        self.unread = []
        self._worker = None
        # What each mod's own settings are set to, for the mods that offer any.
        self.settings = {}
        self._mismatch_confirmed = False
        # How the folder was read, once it has been.
        self._reading = None

        # Opened on the loadout's own mods, it is Mods > Add mods.
        self.setWindowTitle(
            tr("Start a loadout from the library") if self.library_dir is not None
            else tr("Add mods") if self._standing is not None
            else tr("Import your installed mods")
        )
        least_size(self, 860, 600)
        self._build_ui()
        # Ten columns and the mods' whole names: opened wide, and at the size
        # it was left at.
        keep_size(self, getattr(parent, "settings", None), "mods_folder_dialog_size",
                  QSize(1180, 760))
        self.folder_box.setText(start_dir or "")
        if self._standing is not None:
            self._open_standing()

    def _open_standing(self) -> None:
        """The list as it opens on what the loadout has: its mods as they
        were read, and the rest of the folder by name, with nothing read.
        Reading a full folder was a minute and a half before the list
        appeared, for a list whose own mods were read already."""
        folder = Path(self.folder_box.text().strip())
        # The ticks made here are of this folder, kept across its read.
        self._ticked_in = folder
        self.roots = switches.mod_paths(self.game, folder) if folder.is_dir() else []
        # The loadout's library mods with no link: off, as the game has them.
        self._folders_of(self.adding)
        had = {kept.origin.split("/")[0] for kept in self._standing}
        tops = []
        for root in self.roots:
            try:
                entries = sorted(
                    entry for entry in root.iterdir()
                    if entry.is_dir() and not entry.name.startswith(".")
                )
            except OSError:
                continue
            for entry in entries:
                tops.append(entry)
                top = modfolder.bare_name(entry.name)
                if top in had or top in self._unread_folder or not _holds_a_mod(entry):
                    continue
                self._unread_folder[top] = (_named_in(entry), entry)
        try:
            self.folder_switches = switches.read(
                folder, self.game,
                ids={
                    **{kept.origin: kept.report.metadata.get("mod_id", "") for kept in self._standing},
                    **{top: said.get("mod_id", "") for top, (said, _at) in self._unread_folder.items()},
                },
                names=[kept.origin for kept in self._standing] + list(self._unread_folder),
                tops=tops,
            ) if folder.is_dir() else None
        except OSError:
            self.folder_switches = None
        self._fill_table()
        self.status.setText(self._standing_said())
        self._refresh_button()

    def _standing_said(self) -> str:
        """What the status says of a list opened on the loadout's own mods."""
        said = [trn(
            "%(n)s mod here is not in this loadout and is listed by name. "
            "Ticking it reads the folder, once.",
            "%(n)s mods here are not in this loadout and are listed by name. "
            "Ticking one reads the folder, once.",
            len(self._unread_folder),
        ) if self._unread_folder else tr("Every mod in this folder is in the loadout already.")]
        if self.shelf:
            said.append(self._shelf_said())
        return "  ".join(said)

    # -- construction ---------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # Said only when the read is about some of the folder: which mods
        # are ticked and why, and that the rest is one tick away.
        self.focus_label = QLabel("")
        self.focus_label.setObjectName("SubHeading")
        self.focus_label.setWordWrap(True)
        self.focus_label.setVisible(False)
        outer.addWidget(self.focus_label)

        picker = QHBoxLayout()
        picker.setSpacing(6)
        self.folder_box = QLineEdit()
        self.folder_box.setPlaceholderText(tr("Your game's mods folder…"))
        self.folder_box.setAccessibleName(tr("Mods folder"))
        self.folder_box.returnPressed.connect(self.reload)
        picker.addWidget(self.folder_box, 1)
        browse = QPushButton(tr("&Browse…"))
        browse.clicked.connect(self._browse)
        picker.addWidget(browse)
        self.read_button = QPushButton(tr("&Read mods"))
        self.read_button.clicked.connect(self.reload)
        picker.addWidget(self.read_button)
        # The title says what this is for. What reading does is behind the ?.
        about = HelpTip(tr(
            "Reads every mod in this folder as one stack, in the order the "
            "game loads them, so you see the game as you play it. Each mod "
            "keeps its own group in the changes list. You can switch each "
            "one off, or export it by itself."
        ), self.windowTitle())
        picker.addWidget(about)
        outer.addLayout(picker)
        if self.library_dir is not None:
            # The library is where it is: there is no folder to pick.
            for widget in (self.folder_box, browse, self.read_button, about):
                widget.setVisible(False)

        # Reading is asked for, not done on opening: a full folder takes a
        # while, and the folder shown may not be the one meant.
        self.read_button.setDefault(True)
        self.status = QLabel(tr("No mods read yet."))
        self.status.setObjectName("SubHeading")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # Shown only when this folder's own mods say, by their shape, that they
        # are for the other game -- see ImportReport.game_hint. Not a refusal:
        # a folder read against the wrong game's assets.xml reads back as
        # thousands of unreadable ops rather than anything useful, and that is
        # a worse way to find out than a banner up front. Those mods start
        # unticked, so the rest of the folder imports as it is; the box is
        # there only once one of them has been ticked.
        self.mismatch_label = QLabel("")
        self.mismatch_label.setObjectName("Warn")
        self.mismatch_label.setWordWrap(True)
        self.mismatch_label.setVisible(False)
        outer.addWidget(self.mismatch_label)
        self.mismatch_box = QCheckBox(tr("Import these an&yway"))
        self.mismatch_box.setVisible(False)
        self.mismatch_box.toggled.connect(self._on_mismatch_acknowledged)
        outer.addWidget(self.mismatch_box)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText(tr("Filter mods…"))
        self.filter_box.setAccessibleName(tr("Filter mods"))
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._filter)
        outer.addWidget(self.filter_box)
        # What the filter is matched against, a row each.
        self._words = []
        # A bundled mod's row -> the row of the listed mod it is straight
        # inside, which it is drawn below; and each read row by its origin.
        self._under = {}
        self._row_of = {}

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setTabKeyNavigation(False)
        self.table.setHorizontalHeaderLabels([
            tr(label, context="column") if label not in ("", "ModOps") else label
            for label in COLUMNS
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(1, ElideItemDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._refresh_options_button)
        enter_activates(self.table)
        self.table.itemActivated.connect(lambda _item: self._edit_options())
        self.table.setAccessibleName(
            tr("Mods in the library") if self.library_dir is not None
            else tr("Mods in this folder")
        )
        outer.addWidget(self.table, 1)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        # Filled in load order, which a third click on a heading goes back to.
        # A bundled mod stays below its host however the list is sorted.
        self.sorting = sortable(self.table, under=self._under.get)
        self.tick_box = HeaderTick(
            self.table, self._ticks_state, self._tick_shown, tr("Tick every mod shown"),
        )

        # Said when the filter leaves nothing, in place of an empty table.
        self.none_label = QLabel(tr("No mod here matches the filter."))
        self.none_label.setObjectName("StepHint")
        self.none_label.setVisible(False)
        outer.addWidget(self.none_label)

        # How the mods are read: three questions, one to a line, so each is
        # read whole -- side by side they were cut where the next began, and
        # beside the buttons where the buttons began.
        policies = QVBoxLayout()
        policies.setSpacing(4)
        self.keep_inert_box = QCheckBox(tr("Keep i&nert removals"))
        self.keep_inert_box.toggled.connect(self._on_policy_changed)
        policies.addLayout(self._with_help(self.keep_inert_box, KEEP_INERT_HELP))

        outer.addLayout(policies)

        row = QHBoxLayout()
        row.addStretch(1)
        # The library's ticked mods are read when asked, here or by the
        # import: a read of each as it was ticked read every one ticked
        # before it again, and drew the list again under the next tick.
        self.read_ticked_button = QPushButton(tr("Read &ticked mods"))
        self.read_ticked_button.setToolTip(tr(
            "Reads the ticked mods, to see what each changes and set its options first."
        ))
        self.read_ticked_button.setEnabled(False)
        self.read_ticked_button.setVisible(self.library_dir is not None)
        self.read_ticked_button.clicked.connect(self._read_waiting)
        row.addWidget(self.read_ticked_button)
        self.options_button = QPushButton(tr("&Mod options…"))
        self.options_button.setToolTip(tr(
            "The settings this mod offers. Some of its ModOps only run at "
            "certain settings."
        ))
        self.options_button.setEnabled(False)
        self.options_button.clicked.connect(self._edit_options)
        row.addWidget(self.options_button)
        outer.addLayout(row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.load_button = self.buttons.addButton(
            tr("&Import mods"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.load_button.setObjectName("Primary")
        self.load_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        add_detours(self, self.buttons, self._detours)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    @staticmethod
    def _with_help(box, text) -> QHBoxLayout:
        """``box`` with what it means behind a ? beside it."""
        row = QHBoxLayout()
        row.addWidget(box)
        row.addWidget(HelpTip(tr(text), box.text()))
        row.addStretch(1)
        return row

    # -- reading --------------------------------------------------------
    def _browse(self) -> None:
        start = self.folder_box.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("Your mods folder"), start)
        if chosen:
            self.folder_box.setText(chosen)
            self.reload()

    def _on_policy_changed(self) -> None:
        if self.session is not None:
            self.reload()

    def reload(self) -> None:
        """Read the folder in the background, keeping the window answerable."""
        folder = Path(self.folder_box.text().strip())
        if not folder.is_dir():
            self.status.setText(tr("%s is not a folder.", wrappable_path(folder)))
            return
        # Every folder the game loads from, not only the one that was picked:
        # a reading that claims to be the game you actually play cannot leave
        # out the mods sitting in the other three places it looks.
        self.roots = switches.mod_paths(self.game, folder)
        folders = []
        if self.library_dir is not None:
            folders = self._library_folders()
            if not folders and not self.shelf:
                self.status.setText(tr("The library holds no mods."))
                return
        else:
            for root in self.roots:
                folders.extend(modfolder.mod_folders(root))
            # One linked into a folder read here is read with it already.
            # Opened on the loadout's own mods, the library's listed by name
            # are read with the folder now: this is the one read, and a tick
            # of one of them later reads nothing again.
            besides = self.adding + (list(self.shelf.values()) if self._standing is not None else [])
            folders.extend(self._folders_of(
                mod for mod in besides if not mod.linked or mod.link.parent not in self.roots
            ))
        if not folders and self.shelf:
            # Nothing to read yet: the library's mods, by name, to tick.
            self.session = None
            self._fill_table()
            self.status.setText(self._shelf_said())
            self._refresh_button()
            return
        if not folders:
            self.status.setText(tr("No mod folders in %s.", wrappable_path(folder)))
            return
        if self._worker is not None:
            return

        # A fresh read is a fresh folder, possibly of a differently-shaped set
        # of mods; last time's acknowledgement does not carry over.
        self._mismatch_confirmed = False
        self.mismatch_box.setChecked(False)

        self._keep_ticks(folder)
        self.table.setRowCount(0)
        self.progress.setRange(0, len(folders))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.read_button.setEnabled(False)
        self.load_button.setEnabled(False)
        self.status.setText(trn("Reading %(n)s mod…", "Reading %(n)s mods…", len(folders)))

        # Kept: what the session was read under goes with each mod it lands.
        self._reading = modops.Reading(
            options=dict(self.settings),
            # A mod nobody has answered here is read at what the game has it
            # set to -- 117's options file -- not at its author's defaults.
            game_options=switches.game_options(self.game),
            keep_inert=self.keep_inert_box.isChecked(),
        )
        self._worker = ModsWorker(
            self.index,
            folders,
            self.keep_inert_box.isChecked(),
            reading=self._reading,
            parent=self,
            game=self.game,
            logged=loaderlog.logged_order(self.game),
            roots=self.roots,
            unlinked=self._unlinked,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.searching.connect(self._on_searching)
        self._worker.translating.connect(self._on_translating)
        self._worker.stage.connect(self.status.setText)
        self._worker.succeeded.connect(self._on_read)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _keep_ticks(self, folder: Path) -> None:
        """Remember the ticks the user changed for the read about to be made,
        which fills the list again: what they ticked stays ticked, and what
        they unticked stays not.  A tick left as the dialog set it is not
        theirs, and is set again by what the read finds: a mod with nothing
        readable in it starts unticked, and is ticked once it has something.
        Another folder's mods are other mods, whatever their names, and
        start as the dialog opens."""
        if folder != self._ticked_in:
            self._kept_ticks = {}
            self._ticked_in = folder
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            origin = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            # A bundled mod's tick is its host's, kept with that.
            if not origin or item.data(HOST_ROLE):
                continue
            ticked = item.checkState() == Qt.CheckState.Checked
            if ticked == bool(item.data(OWN_TICK_ROLE)):
                self._kept_ticks.pop(origin, None)
            else:
                self._kept_ticks[origin] = ticked

    def _library_folders(self) -> list:
        """The folders of a library read: each mod the library holds or
        stands for and the mods bundled in it, through its link -- or, with
        no link, where the library keeps it."""
        return self._folders_of(
            mod for mod in library.held(self.library_dir)
            if self.picked is None or mod.name in self.picked
        )

    def _folders_of(self, mods) -> list:
        """The folders of the library's ``mods``, as :meth:`_library_folders`
        reads them."""
        self._unlinked, self._links = {}, {}
        folders = []
        for mod in mods:
            at = mod.link
            if not mod.linked:
                at = mod.target
                self._unlinked[mod.target] = mod.name
                self._links[modfolder.bare_name(mod.name)] = mod.link
            elif mod.link.parent not in self.roots:
                # Linked into a folder the game is not read from here: still
                # named by where it sits in it.
                self.roots.append(mod.link.parent)
            folders.append(at)
            folders.extend(modfolder.bundled_in(at))
        return folders

    def _on_progress(self, position: int, total: int, name: str) -> None:
        self.progress.setValue(position)
        if name:
            self.status.setText(tr("Reading %(mod)s… (%(done)s of %(total)s)", mod=name,
                                   done=number(position + 1), total=number(total)))

    def _on_searching(self, position: int, total: int) -> None:
        """A mod that targets by search means reading the whole file once."""
        self.progress.setRange(0, total)
        self.progress.setValue(position)
        self.status.setText(trn(
            "Looking through all %(n)s assets for the mods that target by search…",
            "Looking through all %(n)s assets for the mods that target by search…",
            total,
        ))

    def _on_translating(self, position: int, total: int) -> None:
        """The half after the reading: every op answered against the stack.

        It is the longer half for a real mods folder, and it used to run with
        the bar sitting full from the search before it and nothing said.
        """
        self.progress.setRange(0, total)
        self.progress.setValue(position)

    def _on_failed(self, message: str) -> None:
        self._import_when_read = False
        if self._standing is not None:
            # Still the loadout's mods as they were read, to tick again.
            self._fill_table()
        self.status.setText(tr("Could not read the folder: %s", message))

    def _on_finished(self) -> None:
        self._worker = None
        self.progress.setVisible(False)
        self.read_button.setEnabled(True)
        if self._read_again:
            # Ticked on the shelf while the last read ran.
            self._read_again = False
            self.reload()

    # -- the library's mods, listed and read once ticked -------------------
    def _on_item_changed(self, item) -> None:
        if item.column() == 0 and item.data(SHELF_ROLE):
            if item.checkState() != Qt.CheckState.Checked:
                self._waiting.discard(item.data(SHELF_ROLE))
            elif self.library_dir is not None:
                # Started from the library, every mod is on the shelf, and
                # each tick read all those ticked before it again: dozens
                # ticked were dozens of reads.  They wait, as ticking them
                # all does, for Read ticked mods or the import.
                self._waiting.add(item.data(SHELF_ROLE))
            self._take_ticked_off_shelf()
        if item.column() == 0 and item.data(FOLDER_ROLE):
            self._take_ticked_in_folder()
        if item.column() == 0 and not item.data(HOST_ROLE):
            self._follow_hosts()
        if item.column() == 0 and self.keep and self.session is not None:
            # Adding to a loadout: how many are new to it, as they are ticked.
            self._say_what_is_ticked()
        self._refresh_button()

    def _follow_hosts(self) -> None:
        """Give each bundled mod's row its host's tick: the game loads it
        while that mod is on, and has no switch for it alone, so a loadout
        that reads the host and not it would not be the game."""
        was = self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            holder = item.data(HOST_ROLE) if item is not None else None
            at = self._row_of.get(holder) if holder else None
            if at is not None:
                item.setCheckState(self.table.item(at, 0).checkState())
        self.table.blockSignals(was)

    def _take_ticked_off_shelf(self) -> None:
        """The shelf's ticked rows, to be read: with the rest, once whatever
        else is ticked in the same moment has been.  Not the ones ticked all
        at once, which wait for the import."""
        names = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            name = item.data(SHELF_ROLE) if item is not None else None
            if name and item.checkState() == Qt.CheckState.Checked and name not in self._waiting:
                names.append(name)
        self._take_off_shelf(names)

    def _take_off_shelf(self, names) -> None:
        """Read the shelf's mods ``names``, which come in on."""
        taken = False
        for name in names:
            if name not in self.shelf:
                continue
            mod = self.shelf.pop(name)
            if self.library_dir is not None:
                self.picked = (self.picked or set()) | {mod.name}
            else:
                self.adding.append(mod)
            bare = modfolder.bare_name(mod.name)
            self.arrived.add(bare)
            if self.focus is not None:
                self.focus.add(bare)
            taken = True
        if taken:
            self._shelf_timer.start()

    def _take_ticked_in_folder(self) -> None:
        """The folder's ticked rows of a list opened on the loadout's own
        mods: the folder is read, with them ticked in it and coming in on,
        as a mod ticked to add does."""
        taken = False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            top = item.data(FOLDER_ROLE) if item is not None else None
            if not top or item.checkState() != Qt.CheckState.Checked:
                continue
            self.arrived.add(top)
            if self.focus is not None:
                self.focus.add(top)
            taken = True
        if taken:
            self._shelf_timer.start()

    def _read_waiting(self) -> None:
        """Read ticked mods: the library's ticked and waiting, read now and
        not imported."""
        names, self._waiting = list(self._waiting), set()
        self._take_off_shelf(names)
        self._refresh_button()

    def _read_shelved(self) -> None:
        if self._worker is not None:
            self._read_again = True
            return
        self.reload()

    def _shelf_said(self) -> str:
        """What the status says of the library's mods not read yet."""
        if not self.shelf:
            return ""
        if self.library_dir is not None:
            return trn(
                "%(n)s mod in the library is listed by name. Once ticked, it is read when "
                "you press Read ticked mods, or when you import it.",
                "%(n)s mods in the library are listed by name. The ones you tick "
                "are read when you press Read ticked mods, or when you import them.",
                len(self.shelf),
            )
        return trn(
            "%(n)s mod in the library is listed by name. It is read when you tick it.",
            "%(n)s mods in the library are listed by name. Each is read when you tick it.",
            len(self.shelf),
        )

    @staticmethod
    def _listed_tick(ticked: bool, role, value) -> QTableWidgetItem:
        """The tick of a row listed by name and not read, which holds
        ``value`` under ``role``."""
        tick = QTableWidgetItem()
        tick.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        tick.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
        tick.setData(role, value)
        return tick

    def _add_folder_rows(self, first: int) -> None:
        """The folder's mods the loadout does not have, from ``first`` on,
        in a list opened on the loadout's own: by name, unticked, and read
        once one of them is ticked."""
        for offset, (top, (said, at)) in enumerate(sorted(
            self._unread_folder.items(), key=lambda pair: (pair[1][0].get("mod_name") or pair[0]).casefold(),
        )):
            row = first + offset
            label = said.get("mod_name") or top
            self.table.setItem(row, 0, self._listed_tick(top in self.arrived, FOLDER_ROLE, top))
            name = QTableWidgetItem(label)
            name.setToolTip(tr(
                "%(mod)s\n%(folder)s\n\nIn your mods folder. It is read when you tick it.",
                mod=label, folder=wrappable_path(at),
            ))
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(""))
            on = self._on_in_game(top) and not modfolder.is_marked_off(at.name)
            playing = QTableWidgetItem(
                tr("on", context="switched") if on else tr("off", context="switched")
            )
            playing.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            if not on:
                playing.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(row, 3, playing)
            # After every mod that is read, and before the library's.
            self.table.setItem(row, 4, sort_as(QTableWidgetItem(""), 10 ** 9 - 1))
            waiting = QTableWidgetItem(tr("not read yet"))
            waiting.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(row, 5, waiting)
            for column in range(6, len(COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem(""))
            self._words.append(" ".join((
                label, top, str(said.get("mod_id") or ""), str(said.get("creator") or ""),
            )).casefold())

    def _add_shelf_rows(self, first: int) -> None:
        """The shelf's rows, from ``first`` on: unticked, and named as the
        library's list names them -- nothing in them is read."""
        for offset, mod in enumerate(sorted(
            self.shelf.values(), key=lambda mod: (mod.mod_name or modfolder.bare_name(mod.name)).casefold(),
        )):
            row = first + offset
            label = mod.mod_name or modfolder.bare_name(mod.name)
            # Ticked all at once and not read yet, it is still ticked.
            self.table.setItem(
                row, 0, self._listed_tick(mod.name in self._waiting, SHELF_ROLE, mod.name),
            )
            name = QTableWidgetItem(label)
            name.setToolTip(tr(
                "%(mod)s\n%(folder)s\n\nIn the library. It is read once you tick it "
                "and press Read ticked mods, or when you import it.",
                mod=label, folder=wrappable_path(mod.target),
            ) if self.library_dir is not None else tr(
                "%(mod)s\n%(folder)s\n\nIn the library. It is read when you tick it.",
                mod=label, folder=wrappable_path(mod.target),
            ))
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(""))
            playing = QTableWidgetItem(
                tr("on", context="switched") if mod.linked else tr("off", context="switched")
            )
            playing.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            if not mod.linked:
                playing.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(row, 3, playing)
            # After every mod that is read, in load order.
            self.table.setItem(row, 4, sort_as(QTableWidgetItem(""), 10 ** 9))
            # Translators: in the ModOps column of a mod listed by name and
            # not read yet.
            waiting = QTableWidgetItem(tr("not read yet"))
            waiting.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(row, 5, waiting)
            for column in range(6, len(COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem(""))
            self._words.append(" ".join((label, mod.name, mod.mod_id)).casefold())

    def _on_read(self, session) -> None:
        self.session = session
        if self._standing is not None:
            # Read now, the loadout's mods and the folder's and the
            # library's besides: rows of the read, from here on.
            self._besides = {modfolder.bare_name(name) for name in self.shelf}
            self.adding.extend(self.shelf.values())
            self.shelf = {}
            self._standing = None
            self._unread_folder = {}
        self._read_loadout()
        summary = session.summary()
        if self.folder_switches is not None and self.folder_switches.off:
            summary += "  " + self.folder_switches.describe()
        if self.shelf:
            summary += "  " + self._shelf_said()
        self.status.setText(summary)
        # Import was pressed for the mods this read is of; another read
        # still to come is of more.
        importing = self._import_when_read and not self._read_again
        if importing:
            self._import_when_read = False
        self._fill_table()
        self._show_mismatch()
        self._refresh_button()
        # The folder is read: Enter now imports it.
        self.load_button.setDefault(True)
        # Unless what was read stops it: a mod for the other game, ticked.
        if importing and self.load_button.isEnabled():
            self._accept()

    def _mismatched_reads(self) -> list:
        """Reads whose own folder shape says they are for the other game."""
        if self.session is None:
            return []
        return [
            read
            for read in self.session.reads
            if read.report.game_hint and read.report.game_hint != self.game
        ]

    def _on_mismatch_acknowledged(self, checked: bool) -> None:
        self._mismatch_confirmed = checked
        self._refresh_button()

    def _show_mismatch(self) -> None:
        mismatched = self._mismatched_reads()
        self.mismatch_label.setVisible(bool(mismatched))
        if not mismatched:
            self.mismatch_box.setVisible(False)
            return
        by_game = {}
        for read in mismatched:
            by_game.setdefault(read.report.game_hint, []).append(read.origin)
        # A list after a colon: the game, and a few of its mods.
        said = ", ".join(
            "%s (%s)" % (games.get(game).label, ", ".join(names[:2]) + (
                ", …" if len(names) > 2 else ""
            ))
            for game, names in by_game.items()
        )
        self.mismatch_label.setText(trn(
            "%(n)s mod here has the folder layout of another game, and "
            "%(game)s is loaded: %(mods)s. Its paths will not match, so "
            "almost none of it will be read correctly. It is left unticked, "
            "and ticking them all leaves it out.",
            "%(n)s mods here have the folder layout of another game, and "
            "%(game)s is loaded: %(mods)s. Their paths will not match, so "
            "almost none of them will be read correctly. They are left "
            "unticked, and ticking them all leaves them out.",
            len(mismatched), game=games.get(self.game).label, mods=said,
        ))

    def _read_loadout(self) -> None:
        """Which of these mods the game is actually loading.

        Read from the folder rather than decided here: a mod that is installed
        and switched off is a state both games already have a way of writing
        down, and reading theirs is what makes the answer survive closing the
        Studio -- and match what the game will really do tonight.
        """
        self.folder_switches = None
        folder = self.folder_box.text().strip()
        if not folder or self.session is None:
            return
        try:
            self.folder_switches = switches.read(
                folder,
                self.game,
                ids={
                    read.origin: read.report.metadata.get("mod_id", "")
                    for read in self.session.reads
                },
                names=[read.origin for read in self.session.reads],
                # Where each mod is, from the reading just made.
                tops=switches.tops_of(self.game, folder,
                                      [Path(read.folder) for read in self.session.reads]),
            )
        except OSError:
            self.folder_switches = None

    def _on_in_game(self, origin: str) -> bool:
        """Whether the game loads this mod as things are: the folder's say,
        and never for a library mod with no link."""
        if origin.split("/")[0] in self._links:
            return False
        return self.folder_switches is None or self.folder_switches.is_on(origin)

    def _why_off(self, read, host=None) -> str:
        """Why the game does not load ``read``: for a bundled mod whose
        ``host`` (its read) is off, that."""
        if host is not None and not self._on_in_game(host.origin):
            then = (
                tr("It comes into the loadout switched on all the same.")
                if self._comes_in_on(host.origin)
                else tr("It is still read in and listed, switched off, so it has no effect.")
            )
            return tr(
                "%s, which it is bundled inside, is switched off, so the game "
                "does not load it.", host.name,
            ) + "\n\n" + then
        if read.origin.split("/")[0] in self._links:
            return tr(
                "The library holds this mod with no link in your mods folder, "
                "so the game does not load it.\n"
                "\n"
                "Play puts the link back for a loadout that has it on."
            )
        # Why the game does not load it, and then what the dialog does with
        # it -- which is not always "off": one kept on, one that has just
        # arrived and every mod of a library read comes in on
        # (_comes_in_on), and the tooltip said "switched off" of those too.
        listing = None
        if self.folder_switches is not None and self.folder_switches.profile is not None:
            why = tr("%s has this switched off, so the game does not load it.",
                     self.folder_switches.profile.name)
        else:
            listing = self.folder_switches.disabled.get(read.origin) if self.folder_switches is not None else None
            if listing is not None:
                why = tr(
                    "%(file)s lists it under disabledIds, so Anno 1800 skips "
                    "it.\n"
                    "\n"
                    "%(path)s", file=listing.name, path=listing
                )
            else:
                why = tr("This folder's name starts with “-”, so Anno 1800 skips it.")
        if self._comes_in_on(read.origin):
            then = tr("It comes into the loadout switched on all the same.")
        elif listing is not None:
            then = tr(
                "It is still read in and listed, switched off, so it has no "
                "effect. Switching it on in the Studio takes it out of that "
                "list."
            )
        else:
            then = tr("It is still read in and listed, switched off, so it has no effect.")
        return why + "\n\n" + then

    def _fill_table(self) -> None:
        # Listed: every mod the game loads, whatever came of reading it --
        # the loadout should know of each. A mod whose patch read as no
        # change to any asset was left out, and one that is a single ModOp
        # adding its assets under a path reads as a kept op and no change: it
        # could not be read into a loadout at all, and the fourth card went
        # on counting it as not in the loadout.
        # Before a read, a list opened on the loadout's own mods has them as
        # they were read.
        every = self.session.reads if self.session is not None else self._standing or []
        reads = [read for read in every if self.session is None or not self._inert(read)]
        # For the other game by their own shape: listed, and left unticked.
        mismatched = {read.origin: read.report.game_hint for read in self._mismatched_reads()}
        self._words = []
        self.table.blockSignals(True)
        self.table.setRowCount(len(reads) + len(self._unread_folder) + len(self.shelf))
        # Switched on and still not loaded: another mod that is on replaces it.
        replaced = dependencies.replaced_origins(
            (
                (
                    read.origin,
                    (read.report.metadata.get("mod_id") or modfolder.bare_name(
                        read.origin.split("/")[-1])).strip().lower(),
                    read.report.metadata.get("declared"),
                )
                for read in every
                # A bundled mod is on while it and its host both are.
                if self._on_in_game(read.origin) and self._on_in_game(read.origin.split("/")[0])
            ),
            self.game,
        )
        names = {read.origin: read.name for read in every}
        # A mod bundled inside another that is listed is part of it, as in
        # the changes panel: drawn below it, and ticked with it.  The game
        # loads it while its host is on and has no switch for it alone, so
        # it cannot be left out on its own -- the loadout would be reading
        # a game without it.  Inside a host that is not listed (one with
        # nothing of its own to read), it answers for itself.
        self._row_of.clear()
        self._row_of.update((read.origin, row) for row, read in enumerate(reads))
        by_origin = {read.origin: read for read in reads}
        holders = {read.origin: modfolder.switch_holder(read.origin, by_origin) for read in reads}
        self._under.clear()
        self._under.update(
            (row, self._row_of[above]) for row, read in enumerate(reads)
            if (above := modfolder.listed_host(read.origin, by_origin))
        )
        # Each mod's own tick, before a bundled one takes its host's.
        wants = {read.origin: self._starts_ticked(read, mismatched) for read in reads}
        for row, read in enumerate(reads):
            top = read.origin.split("/")[0]
            holder = holders[read.origin]
            bundled = holder != read.origin
            tick = QTableWidgetItem()
            tick.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            own, wanted = wants[holder]
            tick.setData(OWN_TICK_ROLE, own)
            # Read as nothing, it would come in empty: ticking them all leaves it out.
            readable = self._readable(by_origin[holder])
            tick.setData(EMPTY_ROLE, not readable)
            # The loadout's own stay ticked: this read adds to them.  "Tick
            # none" and then one mod made a loadout of that one mod.
            held = wanted and holder in self.keep
            if held:
                tick.setFlags(tick.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                tick.setData(HELD_ROLE, True)
                tick.setToolTip(tr("Already in this loadout. Adding mods does not take it out."))
            if bundled:
                tick.setFlags(tick.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                tick.setData(HOST_ROLE, holder)
                tick.setToolTip(tr(
                    "Ticked with %s, which it is bundled inside: the game "
                    "switches them together.", names[holder],
                ))
            elif not held and not readable:
                tick.setToolTip(tr(
                    "Reads as no change, so it would come in empty. Ticking "
                    "all leaves it out."
                ))
            tick.setCheckState(
                Qt.CheckState.Checked if wanted else Qt.CheckState.Unchecked
            )
            tick.setData(Qt.ItemDataRole.UserRole, read.origin)
            self.table.setItem(row, 0, tick)

            # A patch of yours reads as a continuation of the mod above it,
            # not as another entry in the list -- the same way the changes
            # panel draws it, and for the same reason.  A bundled mod is
            # drawn inside the mod it came in, one step in for each.
            host = modinfo.declared_patch_of(read.report.metadata.get("declared"))
            inside = by_origin.get(modfolder.listed_host(read.origin, by_origin))
            depth = self._depth(row)
            if host:
                label = "    " * (depth + 1) + "↳ " + read.name  # i18n: keep - no words
            elif depth:
                # One with no name of its own goes by its folder's: the rest
                # of its origin is the row above.
                called = modfolder.folder_name(read.origin) if read.name == read.origin else read.name
                label = "    " * depth + "└ " + called  # i18n: keep - no words
            else:
                label = read.name
            name = QTableWidgetItem(label)
            # What it says of the game it belongs in: a cheat, a new game only.
            setup = dependencies.setup(read.report.metadata.get("declared"))
            # And what it runs, from its modinfo: the part of a mod no reading
            # of its ModOps can show.
            runs = scripts.describe(
                ModLayer(origin=read.origin, folder=str(read.folder or ""),
                         declared=read.report.metadata.get("declared") or {})
            )
            # And the tooltips it patches, which no reading of its ModOps shows.
            tips_file = infotips.patch_file(read.folder) if read.folder is not None else None
            tips = infotips.describe(infotips.read_cached(tips_file, self.game)) if tips_file else []
            if host:
                said = tr("%(mod)s\n%(folder)s\n\nYour patch for %(host)s.",
                          mod=read.name, folder=read.folder or "", host=host)
            else:
                said = "%s\n%s" % (read.name, read.folder or "")
            if inside is not None:
                said += "\n\n" + tr(
                    "Bundled inside %s. The game loads it as a mod of its "
                    "own, at its own place in load order, and switches it "
                    "with that mod.", inside.name,
                )
            name.setToolTip(
                said
                + ("\n\n" + " · ".join(setup) if setup else "")
                + ("\n\n" + "\n".join(runs) if runs else "")
                + ("\n\n" + "\n".join(tips) if tips else "")
            )
            if host:
                name.setForeground(QColor(theme.TEXT_DIM))
            if read.origin in mismatched:
                theme.mark(name, "warn")
                name.setToolTip(name.toolTip() + "\n\n" + tr(
                    "Has the folder layout of an %s mod.",
                    games.get(mismatched[read.origin]).label,
                ))
            self.table.setItem(row, 1, name)
            # A bundled mod is found by its host's name too.
            self._words.append(" ".join((
                read.name, read.origin, str(read.report.metadata.get("mod_id") or ""),
                str(read.report.metadata.get("creator") or ""),
                inside.name if inside is not None else "",
            )).casefold())

            # Which collection it came in with, when the Studio installed it
            # as part of one -- or, dimmer, the ones that list a mod you had
            # already.  Blank is the ordinary case, not a gap.  A bundled mod
            # came in its host.
            brought_by = self.provenance.get(top, "")
            also = [name for name in self.listed.get(top, ()) if name != brought_by]
            # Translators: in the From column: other collections that list a
            # mod that was already installed.
            came = QTableWidgetItem(brought_by or (tr("also in %s", join_list(also)) if also else ""))
            if inside is not None:
                # Translators: in the From column: the mod this one is
                # bundled inside.
                came.setText(tr("inside %s", inside.name))
                came.setForeground(QColor(theme.TEXT_DIM))
            elif not brought_by and also:
                came.setForeground(QColor(theme.TEXT_DIM))
                came.setToolTip(trn("You had this before. %(names)s also lists it.",
                                    "You had this before. %(names)s also list it.",
                                    len(also), names=join_list(also)))
            elif self.focus is not None and top in self.focus:
                came.setForeground(QColor(theme.ACCENT_TEXT))
            self.table.setItem(row, 2, came)

            # What the game itself is doing with this mod tonight, read off the
            # folder rather than decided here. Installed and switched off is an
            # ordinary state and not the same as absent, so it gets said rather
            # than left for the reader to notice in the totals.
            # A bundled mod is on while it and its host both are.
            outer = by_origin[holder] if bundled else None
            on = self._on_in_game(read.origin) and (outer is None or self._on_in_game(holder))
            instead = replaced.get(read.origin) if on else None
            if instead:
                # Translators: in the "In game" column: switched on, but another
                # mod that replaces this one is loaded instead.
                playing = QTableWidgetItem(tr("replaced", context="switched"))
            else:
                playing = QTableWidgetItem(tr("on", context="switched") if on
                                           else tr("off", context="switched"))
            playing.setTextAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            if not on:
                playing.setForeground(QColor(theme.TEXT_DIM))
                playing.setToolTip(self._why_off(read, outer))
            elif instead:
                playing.setForeground(QColor(theme.TEXT_DIM))
                playing.setToolTip(tr(
                    "On, but not loaded. %s replaces it, and the game loads "
                    "only the replacing mod.", names.get(instead, instead)
                ))
            self.table.setItem(row, 3, playing)

            self.table.setItem(row, 4, _number(read.order))
            self.table.setItem(row, 5, _number(read.ops) if read.ops is not None else QTableWidgetItem(""))
            changes = _number(len(read.changes))
            if not read.report.patch_files:
                changes.setText(tr("none", context="changes"))
                changes.setForeground(QColor(theme.TEXT_DIM))
                changes.setToolTip(tr(
                    "No assets.xml patch. It changes something the Studio "
                    "does not read, such as a texture."
                ))
            self.table.setItem(row, 6, changes)
            offered = read.report.metadata.get("options")
            settable = len(offered.settable) if offered is not None else 0
            options = QTableWidgetItem(
                trn("%(n)s option", "%(n)s options", settable) if settable else ""
            )
            if settable:
                options.setToolTip(  # i18n: keep - the mod's own words
                    "\n".join(
                        "%s: %s"
                        % (
                            option.label,
                            option.label_for(
                                self._options_chosen(read).get(option.name, option.default)
                            ),
                        )
                        for option in offered.settable
                    )
                )
            self.table.setItem(row, 8, options)
            unread = _number(read.unread)
            if read.unread:
                theme.mark(unread, "warn")
                unread.setToolTip(trn(
                    "%(n)s ModOp here was not read. It is exported as written.",
                    "%(n)s ModOps here were not read. They are exported as written.",
                    read.unread
                ))
            self.table.setItem(row, 7, unread)
            self.table.setItem(row, 9, self._built_on(read))
        self._add_folder_rows(len(reads))
        self._add_shelf_rows(len(reads) + len(self._unread_folder))
        self.table.blockSignals(False)
        self.sorting.apply()
        self._filter(self.filter_box.text())
        self._say_what_is_ticked()

    def _say_what_is_ticked(self) -> None:
        """The line above the list: which mods are ticked, and why."""
        ticked = fresh = 0
        had = {origin.split("/")[0] for origin in self.keep}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            origin = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if origin and item.checkState() == Qt.CheckState.Checked:
                ticked += 1
                # Ticked, and not a mod whose switch is being kept: new to
                # the loadout.
                fresh += origin.split("/")[0] not in had
        if self._standing is not None:
            self.focus_label.setText(tr(
                "The mods this loadout has are ticked, and keep their "
                "switches. Tick any other mod here to add it. The first tick "
                "can take a few minutes."
            ))
            self.focus_label.setVisible(True)
        elif self.library_dir is not None:
            # Translators: "Play" is the Studio's button that builds the mods
            # and starts the game.
            self.focus_label.setText(tr(
                "Tick the mods this loadout holds. They come in switched on, "
                "and Play switches off every mod it does not hold."
            ))
            self.focus_label.setVisible(True)
        elif self.focus is not None:
            names = sorted({self.provenance.get(top, "") for top in self.focus} - {""})
            # What the loadout has first: its own mods are no arrival,
            # whichever collection brought them once.
            if self.keep and not fresh:
                # Opened to add to the loadout: nothing new is ticked yet.
                said = tr(
                    "The mods this loadout has are ticked, and keep their "
                    "switches. Tick any other mod here to add it."
                )
            elif self.keep and fresh and fresh < ticked:
                # The loadout's own mods are ticked beside the new ones: mods
                # are read as one stack, so all of it is read again. It said
                # "34 mods that just arrived" of one new mod and 33 old ones.
                said = trn(
                    "%(n)s mod is new to this loadout. The mods the loadout "
                    "has already are read again with it, and keep their "
                    "switches.",
                    "%(n)s mods are new to this loadout. The mods the loadout "
                    "has already are read again with them, and keep their "
                    "switches.",
                    fresh,
                )
            elif names:
                said = trn(
                    "%(n)s mod that just arrived from %(collections)s is "
                    "ticked. Tick anything else here to read it in as well.",
                    "%(n)s mods that just arrived from %(collections)s are "
                    "ticked. Tick anything else here to read it in as well.",
                    ticked, collections=join_list(names),
                )
            else:
                said = trn(
                    "%(n)s mod that just arrived is ticked. Tick anything else "
                    "here to read it in as well.",
                    "%(n)s mods that just arrived are ticked. Tick anything "
                    "else here to read it in as well.",
                    ticked,
                )
            self.focus_label.setText(said)
            self.focus_label.setVisible(True)

    def _starts_ticked(self, read, mismatched) -> tuple:
        """Whether the dialog would tick ``read`` itself, and whether it is
        ticked: as the user left it before the folder was read again.

        A mod with nothing readable in it would come in as an empty heading,
        so it starts unticked rather than hidden -- the count in "Not read"
        is the reason, and it is worth seeing.  With a focus, only the mods
        it names start ticked."""
        own = bool(
            self._readable(read)
            and (self.focus is None or read.origin.split("/")[0] in self.focus)
            and read.origin not in mismatched
        )
        return own, bool(self._kept_ticks.get(read.origin, own))

    @staticmethod
    def _readable(read) -> bool:
        """Whether ``read`` would come in as more than an empty heading."""
        return bool(read.changes) or bool(read.report.ops) or not read.report.patch_files

    def _depth(self, row: int) -> int:
        """How many listed mods the one on ``row`` is bundled inside."""
        depth = 0
        while row in self._under:
            row = self._under[row]
            depth += 1
        return depth

    def _built_on(self, read) -> QTableWidgetItem:
        """Which game data a mod the Studio built was built on, and whether
        that is what is loaded now.  Blank for every other mod: nothing in a
        modinfo says so unless the Studio wrote it.

        The one question no manager can answer: after a game update, which of
        these mods were made before it.  Their patches find their nodes by
        paths into the game's own assets, and an update can move them.
        """
        declared = read.report.metadata.get("declared")
        against = builds.declared_against(declared)
        other = builds.built_on_other_data(declared, self.game, self.base_fingerprint)
        update = str(against.get("update") or "")
        if other is None:
            item = QTableWidgetItem(update)
            item.setForeground(QColor(theme.TEXT_DIM))
            return item
        if not other:
            # Translators: in the "Built on" column: the game data loaded now.
            item = QTableWidgetItem(update or tr("this data"))
            item.setToolTip(tr("Built by Anno Modding Studio on the game data loaded now."))
            return item
        if update:
            # Translators: in the "Built on" column; %s is a game update's name.
            item = QTableWidgetItem(tr("%s (older)", update))
        else:
            item = QTableWidgetItem(tr("older data"))
        theme.mark(item, "warn")
        now = self.base_update
        if update and now:
            said = tr(
                "Built by Anno Modding Studio on %(update)s's data. The game "
                "data loaded now is %(now)s. The update may have moved "
                "something this mod changes, so check it still does what it "
                "should.",
                update=update, now=now,
            )
        elif update:
            said = tr(
                "Built by Anno Modding Studio on %s's data. The game data "
                "loaded now is different. The update may have moved "
                "something this mod changes, so check it still does what it "
                "should.", update,
            )
        elif now:
            said = tr(
                "Built by Anno Modding Studio on earlier game data. The game "
                "data loaded now is %s. The update may have moved something "
                "this mod changes, so check it still does what it should.", now,
            )
        else:
            said = tr(
                "Built by Anno Modding Studio on earlier game data. The game "
                "data loaded now is different. The update may have moved "
                "something this mod changes, so check it still does what it "
                "should."
            )
        item.setToolTip(said)
        return item

    # -- choosing -------------------------------------------------------
    # -- a mod's own settings -------------------------------------------
    def _selected_read(self):
        rows = {item.row() for item in self.table.selectedItems()}
        if len(rows) != 1 or self.session is None:
            return None
        item = self.table.item(rows.pop(), 0)
        origin = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        for read in self.session.reads:
            if read.origin == origin:
                return read
        return None

    def _inert(self, read) -> bool:
        """A folder Anno 1800 lists as a mod and loads nothing from -- see
        :attr:`core.installplan.FolderMod.inert` -- which is no mod to read
        in, however the loader names it."""
        return (
            self.game == games.ANNO_1800 and "/" not in read.origin
            and not modzip.is_mod_folder(Path(read.folder))
        )

    def _comes_in_on(self, origin: str) -> bool:
        if origin in self.keep:
            return bool(self.keep[origin])
        if origin.split("/")[0] in self.arrived:
            return True
        if self.library_dir is not None:
            # Ticked in the library's list to be in the loadout, so on.
            return True
        # As the game has it: off, for a library mod with no link.
        return self._on_in_game(origin)

    def _folder_of(self, read) -> str:
        """Where the game finds a mod: where it was read, or, for a library
        mod with no link, where Play puts its link back."""
        parts = read.origin.split("/")
        link = self._links.get(parts[0])
        return str(read.folder) if link is None else str(link.joinpath(*parts[1:]))

    def _options_chosen(self, read) -> dict:
        """What a mod's options are set to: as answered here, else as it was
        read -- at the game's own options file, or at the iModYourAnno
        settings its folder holds."""
        return dict(self.settings.get(read.origin) or read.report.options_read)

    def _refresh_options_button(self) -> None:
        read = self._selected_read()
        offered = read.report.metadata.get("options") if read is not None else None
        self.options_button.setEnabled(bool(offered))

    def _edit_options(self) -> None:
        read = self._selected_read()
        offered = read.report.metadata.get("options") if read is not None else None
        if not offered:
            return
        dialog = ModOptionsDialog(
            read.name, offered, self._options_chosen(read), self
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        self.settings[read.origin] = dialog.values
        self.reload()

    def _filter(self, text: str) -> None:
        """Show the rows that hold every word typed.  A hidden row keeps its
        tick: the filter narrows the list, not the choice.  A host shows with
        a bundled mod that matches: it is the row with the tick."""
        wanted = text.casefold().split()
        matches = {
            row for row, words in enumerate(self._words)
            if all(word in words for word in wanted)
        }
        for row in list(matches):
            while row in self._under:
                row = self._under[row]
                matches.add(row)
        for row in range(len(self._words)):
            self.table.setRowHidden(row, row not in matches)
        self.none_label.setVisible(bool(self._words) and not matches)
        self.tick_box.sync()

    def _tick_shown(self, on: bool) -> None:
        """Tick, or untick, the rows the filter shows: the box in the
        heading of the ticks.

        A mod for the other game is not ticked by this: it is ticked by hand
        or not at all, or ticking them all would bring back the import that
        is held off until that one mod is found and unticked.  Nor is a mod
        whose patch read as nothing: the list opens with it unticked, since
        it would come in as an empty heading.  Unticking them all and then
        ticking them all is then the list as it opened, where no focus
        narrowed it.  A mod the folder has switched off is ticked like any
        other -- the list opens with it ticked -- and comes in switched off.

        The library's mods listed by name are ticked and not read: they
        wait for the import (``_waiting``)."""
        self.table.blockSignals(True)
        for row in self._ticked_by_all(on):
            item = self.table.item(row, 0)
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            name = item.data(SHELF_ROLE)
            if name and on:
                self._waiting.add(name)
            elif name:
                self._waiting.discard(name)
        self.table.blockSignals(False)
        self._follow_hosts()
        self._take_ticked_off_shelf()
        self._take_ticked_in_folder()
        self._refresh_button()

    def _ticked_by_all(self, on: bool) -> list:
        """The rows :meth:`_tick_shown` ticks, ``on``, or unticks."""
        left = {read.origin for read in self._mismatched_reads()} if on else set()
        found = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None or self.table.isRowHidden(row):
                continue
            if item.data(Qt.ItemDataRole.UserRole) in left:
                continue
            if on and item.data(EMPTY_ROLE):
                continue
            # Ticking every mod the library holds is a read of all of them:
            # in a folder's list that is not what ticking them all is for.
            # Read with the folder, they are left as they were listed by name.
            if self.library_dir is None and (item.data(SHELF_ROLE) or (
                on and self._unlinked_extra(item.data(Qt.ItemDataRole.UserRole))
            )):
                continue
            # A bundled mod is ticked with its host, by _follow_hosts.
            if item.data(HELD_ROLE) or item.data(HOST_ROLE):
                continue
            found.append(row)
        return found

    def _ticks_state(self):
        """How the rows shown stand, for the box in the heading."""
        shown = [
            row for row in range(self.table.rowCount())
            if not self.table.isRowHidden(row) and self.table.item(row, 0) is not None
        ]
        return ticks_state(self.table, shown, self._ticked_by_all(True))

    def _unlinked_extra(self, origin) -> bool:
        """Whether ``origin`` is of a library mod the list named and did not
        read, read since with the folder without being ticked."""
        return bool(origin) and origin.split("/")[0] in self._besides

    def chosen(self):
        """The mods currently ticked, as their session reads."""
        wanted = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                wanted.add(item.data(Qt.ItemDataRole.UserRole))
        if self.session is None:
            return []
        return [read for read in self.session.reads if read.origin in wanted]

    def _refresh_button(self) -> None:
        chosen = self.chosen()
        total = sum(len(read.changes) for read in chosen)
        # A mod that reads as kept ops alone is something to import too.
        anything = bool(total) or any(read.report.ops for read in chosen)
        mismatched_chosen = {read.origin for read in self._mismatched_reads()} & {
            read.origin for read in chosen
        }
        # Asked only of a mod for the other game that has been ticked.
        self.mismatch_box.setVisible(bool(mismatched_chosen))
        blocked = bool(mismatched_chosen) and not self._mismatch_confirmed
        waiting = len(self._waiting)
        self.read_ticked_button.setEnabled(bool(waiting) and not self._import_when_read)
        self.load_button.setEnabled(
            (anything or bool(waiting)) and not blocked and not self._import_when_read
        )
        if waiting:
            self.load_button.setText(trn(
                "Read and import %(n)s mod", "Read and import %(n)s mods",
                len(chosen) + waiting,
            ))
        else:
            self.load_button.setText(
                trn("Import %(n)s mod (%(changes)s)", "Import %(n)s mods (%(changes)s)",
                    len(chosen), changes=trn("%(n)s change", "%(n)s changes", total))
                if chosen else tr("Import mods")
            )
        self.tick_box.sync()

    def _accept(self) -> None:
        if self._waiting:
            # Read first, with the rest, and imported once they are: the
            # read says how far it has got, as any read does.
            names, self._waiting = list(self._waiting), set()
            self._import_when_read = True
            self._take_off_shelf(names)
            self._refresh_button()
            return
        self.layers = []
        self.changes = []
        self.ops = []
        self.unread = []
        chosen = self.chosen()
        holders = {read.origin: modfolder.switch_holder(read.origin, self._row_of) for read in chosen}
        # Ticked by the user, in a read that adds to the loadout: a mod
        # asked for comes in on, whatever the folder says of it.  Airship
        # Hangar, switched off in the folder by its "-", was ticked to add
        # and came in off.
        asked = set()
        if self.keep:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                origin = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                # The loadout's own keep their switches; a bundled mod's tick
                # is its host's.
                if (
                    origin and origin not in self.keep
                    and item.checkState() == Qt.CheckState.Checked
                    and not item.data(OWN_TICK_ROLE)
                    and not item.data(HOST_ROLE)
                ):
                    asked.add(origin)
        for read in chosen:
            meta = read.report.metadata
            self.layers.append(
                ModLayer(
                    origin=read.origin,
                    name=meta.get("mod_name") or read.origin,
                    folder=self._folder_of(read),
                    category=meta.get("category", ""),
                    version=meta.get("version", ""),
                    creator=meta.get("creator", ""),
                    description=meta.get("description", ""),
                    mod_id=meta.get("mod_id", ""),
                    order=read.order,
                    unread=read.unread,
                    options=self._options_chosen(read),
                    offered=(meta["options"].to_dict() if meta.get("options") else {}),
                    read_as=(
                        self._reading.recorded() if self._reading else {}
                    ),
                    # A bundled mod comes in as its host does.
                    active=holders[read.origin] in asked or self._comes_in_on(holders[read.origin]),
                    load_after=list(meta.get("load_after") or []),
                    declared=dict(meta.get("declared") or {}),
                )
            )
            self.changes.extend(read.changes)
            self.ops.extend(read.report.ops)
            self.unread.extend(read.report.unread(read.origin))
        # Each host's bundled copies the game does not load from it, and the
        # copy it loads instead -- so the list can show the piece under its
        # host and take you to where it really is.
        copies = (
            reader.copies_by_host(self.session, self.roots, self._unlinked)
            if self.session is not None else {}
        )
        versions = (
            reader.copy_versions(self.session, self.roots, self._unlinked)
            if self.session is not None else {}
        )
        for layer in self.layers:
            layer.copies = copies.get(layer.origin, {})
            layer.copy_versions = {
                passed: versions[passed] for passed in layer.copies if passed in versions
            }
        self.accept()

    def keyPressEvent(self, event) -> None:
        # A line edit leaves Enter unused, and a dialog then presses its
        # default button: Enter after a filter imported, and after a path
        # that is no folder imported the read before it.  In the folder box
        # Enter reads -- its returnPressed -- and in the filter box it has
        # nothing to do.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.focusWidget() in (
            self.folder_box, self.filter_box
        ):
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        if self._worker is not None:
            # Stopped, and then waited for: it stops at the next step it
            # reports, which is at once beside reading the folder out.
            self._worker.cancel()
            self._worker.wait()
        super().reject()


def _number(value) -> QTableWidgetItem:
    item = sort_as(QTableWidgetItem(number(value)), value)
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    return item
