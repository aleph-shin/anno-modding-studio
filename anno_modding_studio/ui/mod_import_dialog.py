"""Bring somebody else's mod in, one reviewed ModOp at a time.

Reading a mod is not a mechanical thing: :mod:`core.modops` decides what each
op does against *this* assets.xml, and says where it could not.  None of that
is worth trusting sight unseen, so nothing is applied until this dialog has
said, for every single op in the file, what it comes to or why it was not
read.

A row is the op itself.  Ticked, it comes in as the op it is, with the group
it is in and what it comes to today under it, and an op that changes nothing
today is as much the mod's as one that does.  What a row says:

*Read*            what the op comes to today is known, and listed.
*No change today* your assets.xml already reads that way, or the op is guarded
                  by a condition that does not hold here.
*Needs a look*    the Studio could not read it.  It goes with the mod and is
                  written back as it was; what it does is not shown.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import games, keptops, library, modfolder, modops, modzip, reader, switches
from ..core.changes import ModLayer, is_own_mod
from ..core.i18n import number, tr, tr_noop, tr_stored, trn
from . import op_marks, theme
from .help_tip import KEEP_INERT_HELP, HelpTip
from .mod_options_dialog import ModOptionsDialog
from .widgets import (
    ElideItemDelegate,
    add_detours,
    busy_cursor,
    fit_columns,
    hold_lines,
    sortable,
    tab_as_laid_out,
)

OUTCOMES = {
    # What the op comes to today is known, which may be nothing.
    modops.IMPORTED: tr_noop("Read", context="outcome"),
    modops.SETTLED: tr_noop("No change today", context="outcome"),
    modops.BLOCKED: tr_noop("Needs a look", context="outcome"),
    # A tweak tool's markup: not an op, and nothing to look at. It goes back
    # out where it was.
    modops.CARRIED: tr_noop("Kept as written", context="outcome"),
    modops.SHELVED: tr_noop("Switched off", context="outcome"),
}
# How far an op inside a group is set in, for each group it is in.
INSET = "    "


class ModImportDialog(QDialog):
    """Pick a mod folder, review what importing it means, then apply it.

    ``changes`` holds what the user chose once the dialog is accepted, and
    ``metadata`` the mod's own details if they said it is their mod to carry on.
    """

    def __init__(
        self,
        index,
        parent=None,
        start_dir: str = "",
        game: str = "",
        separate: bool = False,
        creator: str = "",
        aliases=(),
        library_dir=None,
        detours=(),
    ):
        super().__init__(parent)
        self.index = index
        # Other ways to the mod this adds, each a button: see add_detours.
        self._detours = tuple(detours)
        # The library, whose mods with no link in the mods folder are offered
        # beside the ones in it: they are installed too, only not switched on.
        self.library_dir = Path(library_dir) if library_dir else None
        # Who you are, so a mod you wrote yourself can be recognised as one
        # to carry on with rather than one to fold changes out of.
        self._creator = (creator or "").strip()
        # And the other names you have published under, which are you too.
        self._aliases = tuple(str(name).strip() for name in aliases)
        self.game = game or games.detect_from_path(getattr(index, "source_path", ""))
        # Whether the mod arrives as itself or folded into the mod being
        # written. "Change a mod you downloaded" wants the first: there has to
        # be a mod there for a patch to stand in front of.
        self._separate = separate
        self.report = None
        self.changes = []
        self.ops = []
        self.unread = []
        # How many of the mod's ModOps were taken, groups not among them.
        self.taken = 0
        self.metadata = {}
        # What the mod's own settings are set to, once anybody has said.
        self.settings = {}
        # A mod whose own folder shape says it is for the other game is not
        # something to import on a misclick -- see _mismatch().
        self._mismatch_confirmed = False
        # Once a folder is read: the settings it offers, and how it was read.
        self.offered = None
        self._reading = None
        # The user's own answer to "This is my mod", once they give one, and
        # the folder it and the settings were given for: see reload() and
        # _refresh_metadata_box().
        self._mine = None
        self._mine_for = ""

        self.setWindowTitle(tr("Import changes from a mod"))
        self.setMinimumSize(1000, 640)
        self._build_ui()
        self.folder_box.setText(start_dir or "")
        # A folder that is itself a mod is read straight away. It used to be
        # filled in and left unread, so the dialog opened showing a mod with
        # the Import button dead and nothing saying why -- you had to pick the
        # same folder again to wake it up.
        if self._is_mod_folder(start_dir):
            self.reload()
        else:
            self._list_installed(start_dir)

    @staticmethod
    def _is_mod_folder(path) -> bool:
        """Whether this is one mod, rather than the folder mods live in."""
        if not path:
            return False
        folder = Path(path)
        if not folder.is_dir():
            return folder.is_file()  # a ModOps file, which reads fine too
        if (folder / "modinfo.json").is_file():
            return True
        if not (folder / "data").is_dir():
            return False
        # A ``data`` folder and no modinfo is a mod -- unless mods sit beside
        # it.  Then it is a mods folder somebody once unpacked a mod's data
        # straight into, and reading the lot as one mod walks every mod in it
        # to show the two ops of that one.
        try:
            beside = [
                child for child in folder.iterdir()
                if child.is_dir() and child.name.lower() != "data"
                and not child.name.startswith(".")
            ]
        except OSError:
            return True
        return not any(modzip.is_mod_folder(child) for child in beside)

    # -- construction ---------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        picker = QHBoxLayout()
        picker.setSpacing(6)
        self.folder_box = QLineEdit()
        self.folder_box.setPlaceholderText(tr("A mod folder, or a ModOps assets.xml…"))
        self.folder_box.setAccessibleName(tr("Mod folder"))
        self.folder_box.returnPressed.connect(self.reload)
        picker.addWidget(self.folder_box, 1)
        browse = QPushButton(tr("&Browse…"))
        browse.clicked.connect(self._browse)
        picker.addWidget(browse)
        self.read_button = QPushButton(tr("&Read mod"))
        self.read_button.clicked.connect(self.reload)
        picker.addWidget(self.read_button)
        # The title says what this is for. How a mod is read is behind the ?.
        picker.addWidget(HelpTip(tr(
            "Reads a mod's ModOps against the assets.xml you have loaded and "
            "shows what each comes to today. Each ModOp you tick comes in as "
            "it was written, with its group, and is exported that way. A "
            "ModOp the Studio cannot read is listed too, and is still "
            "exported as written."
        ), self.windowTitle()))
        outer.addLayout(picker)

        # The mods sitting in that folder. You came here to choose one, so the
        # choice is on screen: pointing the dialog at your mods folder and then
        # making you go through Browse to name a mod inside it was asking you
        # to find something the Studio was already looking at.
        self.installed_label = QLabel("")
        self.installed_label.setObjectName("SubHeading")
        self.installed_label.setVisible(False)
        outer.addWidget(self.installed_label)
        self.installed = QListWidget()
        self.installed.setObjectName("Recents")
        self.installed.setFrameShape(QFrame.Shape.NoFrame)
        self.installed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.installed.setMaximumHeight(190)
        self.installed.setVisible(False)
        self.installed.setAccessibleName(tr("Mods in this folder"))
        self.installed.itemClicked.connect(self._pick_installed)
        self.installed.itemActivated.connect(self._pick_installed)
        outer.addWidget(self.installed)

        self.status = QLabel(tr("No mod read yet."))
        self.status.setObjectName("SubHeading")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # Shown only when the mod's own folder shape says it is for the other
        # game -- see ImportReport.game_hint. A mismatch is not refused outright
        # (a mod that ships no recognisable folder shape reads as no opinion,
        # and the odd mod really does target both), but it does not go through
        # on a misclick: the button stays off until this is ticked.
        self.mismatch_label = QLabel("")
        self.mismatch_label.setObjectName("Warn")
        self.mismatch_label.setWordWrap(True)
        self.mismatch_label.setVisible(False)
        outer.addWidget(self.mismatch_label)
        self.mismatch_box = QCheckBox(tr("Import an&yway"))
        self.mismatch_box.setVisible(False)
        self.mismatch_box.toggled.connect(self._on_mismatch_acknowledged)
        outer.addWidget(self.mismatch_box)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_ops_tab(), "ModOps")  # i18n: keep - a name
        # Translators: the tab listing the files of a mod that are not ModOps.
        self.tabs.addTab(self._build_extras_tab(), tr("Not ModOps"))
        outer.addWidget(self.tabs, 1)

        # A wide replace of a list says, in XPath terms, "these are the only
        # entries now" -- so read strictly it deletes every entry the author did
        # not retype, including the ones the game never reads. Carrying those
        # deletions costs compatibility and buys nothing, but it *is* what the
        # file says, so the reading stays available.
        self.keep_inert_box = QCheckBox(tr("Keep i&nert removals"))
        self.keep_inert_box.toggled.connect(self._on_policy_changed)
        inert = QHBoxLayout()
        inert.addWidget(self.keep_inert_box)
        inert.addWidget(HelpTip(tr(KEEP_INERT_HELP), self.keep_inert_box.text()))
        inert.addStretch(1)
        outer.addLayout(inert)

        # A value a mod works out while patching is read at what it comes to
        # and kept as the op that works it out: nothing is settled, so there
        # is nothing to ask about it here.
        settle = QHBoxLayout()
        settle.addStretch(1)
        self.options_button = QPushButton(tr("&Mod options…"))
        self.options_button.setToolTip(tr(
            "The settings this mod offers. Some of its ModOps only run at "
            "certain settings."
        ))
        self.options_button.setEnabled(False)
        self.options_button.clicked.connect(self._edit_options)
        settle.addWidget(self.options_button)
        outer.addLayout(settle)

        # Two different things to want from another author's mod: its changes,
        # folded into what you are writing, or the mod itself, sitting in your
        # session as its own thing. The first is what this dialog has always
        # done, so it stays the default; the second is what "Load installed
        # mods" does for a whole folder.
        self.separate_box = QCheckBox(tr("Keep it &separate"))
        self.separate_box.toggled.connect(self._on_separate_changed)
        separate = QHBoxLayout()
        separate.addWidget(self.separate_box)
        separate.addWidget(HelpTip(tr(
            "Ticked, the mod keeps its own group in the changes list, can be "
            "switched off in one go, and is exported as its own folder. The "
            "Studio can then also show where it and another mod set the same "
            "field. Unticked, its changes become part of the mod you are "
            "writing."
        ), self.separate_box.text()))
        separate.addStretch(1)
        outer.addLayout(separate)

        # Only a question when the mod is folded into yours, and only one use
        # survives there: bringing your own published mod back in to carry on
        # with it, where its ModID and version are what keep an update an
        # update. Folding somebody else's changes into your mod is the other
        # reason to merge, and then their name on your loadout is wrong.
        self.metadata_box = QCheckBox(tr("This is my mo&d"))
        self.metadata_box.setEnabled(False)
        self.metadata_box.clicked.connect(self._on_mine_answered)
        mine = QHBoxLayout()
        mine.addWidget(self.metadata_box)
        # Kept, to be hidden with the box: left behind, it was a ? under
        # "Keep it separate" belonging to nothing on screen.
        self.metadata_help = HelpTip(tr(
            "Your loadout takes the mod's name, ModID, version, category and "
            "description, so what you export next is an update to that mod "
            "and not a second mod. Leave it unticked to fold this mod's "
            "changes into the mod you are writing and keep your own details."
        ), self.metadata_box.text())
        mine.addWidget(self.metadata_help)
        mine.addStretch(1)
        outer.addLayout(mine)
        # After metadata_box exists, since toggling it reaches for that.
        self.separate_box.setChecked(self._separate)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText(tr("&Import"))
        self.import_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        add_detours(self, self.buttons, self._detours)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _build_ops_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        self.ops_table = QTableWidget(0, 5)
        self.ops_table.setTabKeyNavigation(False)
        self.ops_table.setHorizontalHeaderLabels([
            "", tr("What the mod says", context="column"), tr("Asset", context="column"),
            tr("Outcome", context="column"),
            # Translators: a column: what a ModOp changes as things stand.
            tr("Comes to", context="column"),
        ])
        self.ops_table.setIconSize(op_marks.icon_size())
        self.ops_table.verticalHeader().setVisible(False)
        self.ops_table.setAlternatingRowColors(True)
        self.ops_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ops_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ops_table.setItemDelegate(ElideItemDelegate(self.ops_table))
        self.ops_table.setAccessibleName("ModOps")  # i18n: keep - a name
        self.ops_table.itemChanged.connect(self._on_tick)
        header = self.ops_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.ops_table.setColumnWidth(0, 28)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        # The width shared out, rather than one column taking it all.
        fit_columns(self.ops_table, shares={1: 0.40, 2: 0.22, 3: 0.12, 4: 0.26})
        layout.addWidget(self.ops_table, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.detail = QLabel(tr("Pick a row to see what it comes to."))
        self.detail.setObjectName("StepHint")
        self.detail.setWordWrap(True)
        # A fixed height: the detail line runs to two lines for some rows and
        # one for others, and letting it resize walks the table under the mouse.
        # Two lines of whatever size the text actually is, not a pixel count
        # that clips the second line the moment the font grows.
        hold_lines(self.detail, 2, 6)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.detail, 1)
        layout.addLayout(row)

        self.ops_table.itemSelectionChanged.connect(self._on_selected)
        return page

    def _build_extras_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        note = QLabel(tr(
            "Files in the mod folder that are not assets.xml ModOps. They are "
            "not imported, and the mod still uses them in the game."
        ))
        note.setObjectName("StepHint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.extras_table = QTableWidget(0, 3)
        self.extras_table.setTabKeyNavigation(False)
        self.extras_table.setHorizontalHeaderLabels([
            tr("What", context="column"), tr("Where", context="column"),
            tr("Note", context="column"),
        ])
        self.extras_table.verticalHeader().setVisible(False)
        self.extras_table.setAlternatingRowColors(True)
        self.extras_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.extras_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.extras_table.setItemDelegate(ElideItemDelegate(self.extras_table))
        self.extras_table.setAccessibleName(tr("Not ModOps"))
        header = self.extras_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        fit_columns(self.extras_table)
        layout.addWidget(self.extras_table, 1)
        sortable(self.extras_table)
        return page

    # -- reading a mod --------------------------------------------------
    def _list_installed(self, folder) -> None:
        """Offer the mods inside ``folder``, when it holds any."""
        self.installed.clear()
        found = modfolder.mod_folders(Path(folder)) if folder else []
        for path in found:
            meta = modfolder.metadata_of(path)
            name = meta.get("mod_name") or path.name
            item = QListWidgetItem(
                name if name == path.name else "%s   ·   %s" % (name, path.name)  # i18n: keep - no words
            )
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.installed.addItem(item)
        # And the library's mods with no link there: installed, and off --
        # under a heading of their own, where a dimmed row among the
        # folder's was easy to read past.
        shelved = 0
        for mod in (library.held(self.library_dir) if self.library_dir is not None and folder else ()):
            if mod.linked or os.path.normcase(str(mod.link.parent)) != os.path.normcase(str(Path(folder))):
                continue
            if not shelved:
                # Translators: heads the library's mods in a list of mods to
                # pick from; the game does not load them now.
                heading = QListWidgetItem(tr("In the library, switched off"))
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
                self.installed.addItem(heading)
            meta = modfolder.metadata_of(mod.target)
            name = meta.get("mod_name") or mod.name
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, str(mod.target))
            item.setToolTip(str(mod.target))
            item.setForeground(QColor(theme.TEXT_DIM))
            self.installed.addItem(item)
            shelved += 1
        said = trn(
            "%(n)s mod in %(folder)s. Pick it, or browse for a mod elsewhere.",
            "%(n)s mods in %(folder)s. Pick one, or browse for a mod elsewhere.",
            len(found), folder=folder,
        )
        if shelved and not found:
            said = trn(
                "No mods in %(folder)s. %(n)s is in the library, switched off. "
                "Pick it, or browse for a mod elsewhere.",
                "No mods in %(folder)s. %(n)s are in the library, switched off. "
                "Pick one, or browse for a mod elsewhere.",
                shelved, folder=folder,
            )
        elif shelved:
            said = trn(
                "%(said)s %(n)s more is in the library, switched off.",
                "%(said)s %(n)s more are in the library, switched off.",
                shelved, said=said,
            )
        self.installed_label.setText(said)
        found = found or [None] * shelved
        self.installed_label.setVisible(bool(found))
        self.installed.setVisible(bool(found))

    def _pick_installed(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        self.folder_box.setText(path)
        self.reload()

    def _browse(self) -> None:
        start = self.folder_box.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(self, tr("Pick a mod folder"), start)
        if chosen:
            self.folder_box.setText(chosen)
            self.reload()

    def _on_policy_changed(self, _checked: bool) -> None:
        """The reading changed, so the whole mod has to be read again."""
        if self.report is not None:
            self.reload()

    def reload(self) -> None:
        source = self.folder_box.text().strip()
        if not source:
            return
        if source != self._mine_for:
            # Another mod: whether it is yours is asked of it afresh, and it is
            # read at its own settings, not at those chosen for the last one.
            self._mine = None
            self._mine_for = source
            self.settings = {}
        # Reading a mod is fast (the biggest shipping one takes a tenth of a
        # second), but it walks a folder that might be on a slow drive, and a
        # dialog that just sits there says nothing about why.
        with busy_cursor():
            # Kept: how it was read goes with the mod if it lands as its own.
            self._reading = modops.Reading(
                options={"": dict(self.settings)},
                game_options=switches.game_options(self.game),
                keep_inert=self.keep_inert_box.isChecked(),
            )
            self.report = reader.analyse(
                Path(source),
                self.index,
                self.keep_inert_box.isChecked(),
                reading=self._reading,
            )
        # A fresh read is a fresh mod, possibly a differently-shaped one; last
        # time's acknowledgement does not carry over.
        self._mismatch_confirmed = False
        self.mismatch_box.setChecked(False)
        self._show_installed_as(source)
        self._show_report()

    def _show_installed_as(self, source: str) -> None:
        """Highlight the listed mod that is the folder read, and none where
        it is not listed: a path typed in, or typing in the list, left
        another highlighted -- the fork, for its original's path (R3-31,
        hands-on run 3)."""
        wanted = os.path.normcase(os.path.abspath(source))
        self.installed.blockSignals(True)
        try:
            self.installed.setCurrentItem(None)
            self.installed.clearSelection()
            for row in range(self.installed.count()):
                item = self.installed.item(row)
                path = item.data(Qt.ItemDataRole.UserRole)
                if path and os.path.normcase(os.path.abspath(str(path))) == wanted:
                    self.installed.setCurrentItem(item)
                    break
        finally:
            self.installed.blockSignals(False)

    def _mismatch(self) -> str:
        """Which game this mod looks like it is for, if not the one loaded."""
        hint = self.report.game_hint if self.report is not None else None
        return hint if hint and hint != self.game else ""

    def _on_mismatch_acknowledged(self, checked: bool) -> None:
        self._mismatch_confirmed = checked
        self._update_button()

    def _show_report(self) -> None:
        report = self.report
        mismatch = self._mismatch()
        self.mismatch_label.setVisible(bool(mismatch))
        self.mismatch_box.setVisible(bool(mismatch))
        if mismatch:
            self.mismatch_label.setText(tr(
                "This mod has the folder layout of an %(game)s mod, and "
                "%(loaded)s is loaded. Its paths will not match, so almost "
                "none of it will be read correctly.",
                game=games.get(mismatch).label, loaded=games.get(self.game).label,
            ))
        self.status.setText(self._summary(report))
        self._fill_ops(report)
        self._fill_extras(report)
        self.metadata = dict(report.metadata)
        offered = self.metadata.pop("options", None)
        self.offered = offered
        self.options_button.setEnabled(bool(offered))
        if offered:
            self.options_button.setText(
                tr("&Mod options… (%s)", number(len(offered.settable)))
            )
        # The label stays what it is. Why it cannot be ticked is its tooltip.
        self.metadata_box.setToolTip(
            "" if report.metadata
            else tr("This mod has no modinfo.json, so it has no details to keep.")
        )
        self._refresh_metadata_box()
        self.tabs.setTabText(0, "ModOps (%s)" % number(len(report.results)))  # i18n: keep - a name
        self.tabs.setTabText(1, tr("Not ModOps (%s)", number(len(report.extras))))
        self._update_button()

    def _summary(self, report) -> str:
        """What reading the mod came to, in a line."""
        if report.error or not report.results:
            return report.summary()
        # Kept as written, the tally is of ops, and a group is not one.
        ops = [result for result in report.results if not result.op.scope]
        read = [result for result in ops if result.record is not None or result.changes]
        idle = sum(1 for result in read if not result.changes)
        bits = [trn(
            "%(n)s change from %(ops)s", "%(n)s changes from %(ops)s",
            len(report.changes),
            ops=trn("%(n)s ModOp", "%(n)s ModOps", len(read)),
        )]
        if idle:
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s changes nothing today", "%(n)s change nothing today", idle))
        shelved = sum(1 for result in ops if result.status == modops.SHELVED)
        if shelved:
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s switched off", "%(n)s switched off", shelved))
        blocked = sum(1 for result in ops if result.status == modops.BLOCKED)
        if blocked:
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s need a look", "%(n)s need a look", blocked))
        return "; ".join(bits)

    def _outcome(self, result) -> str:
        """The word for what became of an op, in its row."""
        words = OUTCOMES
        if result.op.scope and result.record is not None:
            # A group changes nothing itself: what there is to say of it is
            # whether what is in it runs.
            held = keptops.shape(keptops.View(result.record, ()), self.game)[2][2]
            if held == op_marks.ON:
                return tr("Runs today", context="outcome")
            if held == op_marks.OFF:
                return tr("Does not run today", context="outcome")
            return ""
        if result.status in words:
            return tr(words[result.status], context="outcome")
        return result.status

    def _fill_ops(self, report) -> None:
        table = self.ops_table
        table.blockSignals(True)
        table.setRowCount(0)
        table.setRowCount(len(report.results))
        for row, result in enumerate(report.results):
            # One that comes back switched off is taken or left like one
            # that is read: what it would come to travels with its op.
            importable = self._takeable(result)
            # Read: it is known what it comes to, which may be nothing.
            read = result.status in (modops.IMPORTED, modops.SHELVED)

            tick = QTableWidgetItem()
            tick.setFlags(
                (Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                if importable
                else Qt.ItemFlag.NoItemFlags
            )
            tick.setCheckState(
                Qt.CheckState.Checked if importable else Qt.CheckState.Unchecked
            )
            table.setItem(row, 0, tick)

            inset = INSET * len(result.op.within)
            says = QTableWidgetItem(inset + tr_stored(result.op.label()))
            says.setToolTip(result.op.comment or tr_stored(result.op.label()))
            says.setIcon(self._drawn(result))
            table.setItem(row, 1, says)

            label = self.index.display_name(result.op.guid) if result.op.guid else ""
            table.setItem(row, 2, QTableWidgetItem(label))

            table.setItem(row, 3, QTableWidgetItem(self._outcome(result)))

            if result.op.scope and importable:
                # A group comes to what is in it, and its row says why it is
                # kept and whether it runs.
                becomes = tr_stored(result.reason)
            elif read and result.reason:
                becomes = trn("%(n)s change  (%(reason)s)", "%(n)s changes  (%(reason)s)",
                              len(result.changes), reason=tr_stored(result.reason))
            elif read:
                becomes = trn("%(n)s change", "%(n)s changes", len(result.changes))
            else:
                becomes = tr_stored(result.reason)
            item = QTableWidgetItem(becomes)
            item.setToolTip(becomes)
            table.setItem(row, 4, item)

            if not importable:
                for column in range(1, 5):
                    table.item(row, column).setForeground(QColor(theme.TEXT_DIM))
        table.blockSignals(False)

    def _takeable(self, result) -> bool:
        """Whether an op's row has a tick: there is something of it to take.

        Every op the Studio read, the ones that change nothing today and the
        groups among them: each is kept as the op it is.  One it could not
        read is not a choice either way, and goes with the mod.
        """
        if result.status in (modops.IMPORTED, modops.SHELVED):
            return True
        return result.record is not None

    def _drawn(self, result):
        """An op's badge, and what is worked out as the game loads it -- the
        drawings the Mod changes panel has: see :mod:`ui.op_marks`."""
        found = modops.describe(result.op)
        if result.record is not None:
            view = keptops.View(result.record, tuple(result.changes))
            _kind, _off, slots = keptops.shape(view, self.game)
        else:
            slots = (
                (op_marks.ON if result.changes else op_marks.OFF) if found.target else 0,
                op_marks.ON if found.payload else 0,
                (op_marks.ON if result.changes else op_marks.UNKNOWN) if found.condition else 0,
                op_marks.ON if found.options else 0,
            )
        return op_marks.icon(found.kind, found.skip, slots)

    def _fill_extras(self, report) -> None:
        table = self.extras_table
        table.setRowCount(0)
        table.setRowCount(len(report.extras))
        for row, extra in enumerate(report.extras):
            for column, text in enumerate((extra.what, extra.where, extra.detail)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(row, column, item)

    # -- picking what to take -------------------------------------------
    def _rows(self):
        for row in range(self.ops_table.rowCount()):
            item = self.ops_table.item(row, 0)
            if item is not None:
                yield row, item

    def _ticked(self):
        """The results whose rows are ticked, in the mod's order."""
        if self.report is None:
            return []
        return [
            self.report.results[row]
            for row, item in self._rows()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _chosen(self):
        out = []
        for result in self._ticked():
            out.extend(result.changes)
        return out

    def _taken(self) -> int:
        """How many ModOps are ticked, a group not being one."""
        return sum(1 for result in self._ticked() if not result.op.scope)

    def _on_tick(self, item) -> None:
        if item is not None and item.column() == 0:
            self._tick_with_its_group(item)
        self._update_button()

    def _tick_with_its_group(self, item) -> None:
        """An op is in its group or it is not the op the mod wrote: its
        condition is the group's.  So ticking one ticks the groups it is in,
        and a group's tick is the tick of everything in it."""
        results = self.report.results
        mine = results[item.row()]
        on = item.checkState() == Qt.CheckState.Checked
        uid = mine.record.uid if mine.record is not None else ""
        self.ops_table.blockSignals(True)
        for row, other in self._rows():
            if not (other.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            result = results[row]
            holds = result.record is not None and result.record.uid in mine.op.within
            inside = bool(mine.op.scope) and uid in result.op.within
            if inside or (on and holds):
                other.setCheckState(item.checkState())
        self.ops_table.blockSignals(False)

    def _update_button(self) -> None:
        blocked = bool(self._mismatch()) and not self._mismatch_confirmed
        count = self._taken()
        said = trn("Import %(n)s ModOp", "Import %(n)s ModOps", count)
        self.import_button.setEnabled(bool(count) and not blocked)
        self.import_button.setText(said if count else tr("Import"))

    def _on_selected(self) -> None:
        rows = {index.row() for index in self.ops_table.selectedIndexes()}
        if self.report is None or len(rows) != 1:
            return
        result = self.report.results[rows.pop()]
        if result.status == modops.IMPORTED and result.changes:
            lines = [change.describe_change() for change in result.changes[:4]]
            if len(result.changes) > 4:
                lines.append(trn("and %(n)s more", "and %(n)s more", len(result.changes) - 4))
            text = "%s → %s" % (tr_stored(result.op.label()), "; ".join(lines))
        else:
            text = "%s — %s" % (tr_stored(result.op.label()), tr_stored(result.reason))
        if result.op.comment:
            text += "\n" + tr("The mod says: %s", result.op.comment)
        self.detail.setText(text)

    def _options_chosen(self) -> dict:
        """What the mod's options are set to: as answered here, else as it
        was read -- at the game's own options file, or at the iModYourAnno
        settings its folder holds."""
        read = self.report.options_read if self.report is not None else {}
        return dict(self.settings or read)

    def _edit_options(self) -> None:
        """A configurable mod is read at particular settings; here they are."""
        if not self.offered:
            return
        name = self.metadata.get("mod_name") or self.report.mod_name
        dialog = ModOptionsDialog(name, self.offered, self._options_chosen(), self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        self.settings = dialog.values
        self.reload()

    def _on_separate_changed(self, on: bool) -> None:
        self._refresh_metadata_box()

    def _on_mine_answered(self, checked: bool) -> None:
        self._mine = checked

    def _refresh_metadata_box(self) -> None:
        """Whether taking the mod's own details is on offer, and ticked.

        Both questions in one place because they were answered in two and drifted
        apart: reading a mod re-ticked this box whatever the *keep it separate*
        box said, so a mod imported as its own thing also handed its name, author
        and ModID to the loadout -- two mods then claiming one identity, and,
        since the Studio recognises a mod as yours by its creator, the author's
        own mod reading back as one you wrote.

        A mod kept as its own thing brings its details with it, so there is
        nothing to take -- and the box is not shown at all, rather than shown
        greyed out as a question with no answer.

        Merged in, it is offered and ticked only for a mod that is yours by the
        same test the Studio uses everywhere else: its creator is you.  Folding
        somebody else's changes into your mod is the commoner reason to merge,
        and for that their details on your loadout would be wrong.

        That is only the box's first answer: once the user ticks or unticks it
        themselves, theirs stands for this mod, through keeping it separate
        and back and through reading it again at other options.
        """
        separate = self.separate_box.isChecked()
        offer = bool(self.metadata) and not separate
        self.metadata_box.setVisible(not separate)
        self.metadata_help.setVisible(not separate)
        self.metadata_box.setEnabled(offer)
        if self._mine is not None:
            yours = self._mine
        else:
            theirs = str(self.metadata.get("creator") or "").strip()
            yours = bool(theirs) and theirs.casefold() in {
                name.casefold() for name in (self._creator, *self._aliases) if name
            }
        self.metadata_box.setChecked(offer and yours)

    @property
    def origin(self) -> str:
        """The identity the imported changes carry, "" when they are merged in."""
        if not self.separate_box.isChecked() or self.report is None:
            return ""
        root = self.report.root
        return root.name if root is not None and root.is_dir() else self.report.mod_name

    def layer(self):
        """The mod as a loaded layer, or None when it was merged into yours."""
        origin = self.origin
        if is_own_mod(origin):
            return None
        meta = self.report.metadata if self.report is not None else {}
        return ModLayer(
            origin=origin,
            name=meta.get("mod_name") or origin,
            folder=str(self.report.root) if self.report is not None else "",
            category=meta.get("category", ""),
            version=meta.get("version", ""),
            creator=meta.get("creator", ""),
            description=meta.get("description", ""),
            mod_id=meta.get("mod_id", ""),
            order=0,
            unread=self.report.count(modops.BLOCKED) if self.report is not None else 0,
            options=self._options_chosen(),
            offered=(self.offered.to_dict() if self.offered else {}),
            read_as=self._reading.recorded() if self._reading else {},
            load_after=list(meta.get("load_after") or []),
            declared=dict(meta.get("declared") or {}),
        )

    def _accept(self) -> None:
        self.changes = self._chosen()
        origin = self.origin
        for change in self.changes:
            change.origin = origin
        # Only the ops the user actually took, and only while every change
        # they made came in: an op that speaks for changes that were
        # left behind would claim them again the moment the mod was written out.
        wanted = {change.op_uid for change in self.changes if change.op_uid}
        landed = {}
        for change in self.changes:
            if change.op_uid:
                landed[change.op_uid] = landed.get(change.op_uid, 0) + 1
        ticked = self._ticked()
        self.taken = sum(1 for result in ticked if not result.op.scope)
        # Kept as written, a row is its op: the ones ticked come in, each
        # with the groups it is in, and the ones not ticked stay behind.
        offered = {}
        taken = set()
        if self.report is not None:
            offered = {
                result.record.uid for result in self.report.results if result.record is not None
            }
            for result in ticked:
                if result.record is not None:
                    taken.add(result.record.uid)
                    taken.update(result.op.within)
        self.ops = []
        for op in (self.report.ops if self.report is not None else []):
            if op.uid in offered:
                if op.uid not in taken:
                    continue
            elif op.made and (op.uid not in wanted or landed.get(op.uid) != op.made):
                continue
            op.origin = origin
            self.ops.append(op)
        self.unread = (
            self.report.unread(origin) if self.report is not None else []
        )
        if not self.metadata_box.isChecked():
            self.metadata = {}
        self.accept()
