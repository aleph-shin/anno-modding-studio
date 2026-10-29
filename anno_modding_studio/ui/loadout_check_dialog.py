"""The whole loadout through the game's loader at once, against the Studio.

The Export tab's check asks about one mod, on the mods it builds on. This asks
about every mod that loads, together, in one run of the loader -- the question
the census asks of each mod.io bundle -- and compares every asset they change
with what the Studio shows. A mod whose reading goes wrong only beside
another is caught here and nowhere else.

It is slow, so it is asked for rather than run on its own: the loader patches
the whole of the game's file once, and then each of the thousands of assets
the mods change is built on the Studio's side. About fifty seconds for a
hundred-odd Anno 1800 mods on the machine this was written on.

Each difference is a row naming the mods that change the asset, the fields
where the two part and the change there; double-clicking one opens it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import number, tr, tr_noop, trn
from . import theme
from .help_tip import HelpTip
from .widgets import (
    enter_activates,
    least_size,
    listing_table,
    sortable,
    tab_as_laid_out,
)

COLUMNS = (tr_noop("Asset"), tr_noop("Changed by"), tr_noop("Where they part"), tr_noop("The change there"))


class LoadoutCheckDialog(QDialog):
    """Runs the check in the background and shows what it found."""

    changeActivated = Signal(str, str)  # guid, path

    def __init__(self, job, index, loader, parent=None):
        super().__init__(parent)
        self.job = job
        self.index = index
        self.loader = loader
        self.verdict = None
        self._worker = None
        self.setWindowTitle(tr("The whole loadout, by the game's loader"))
        least_size(self, 1000, 620)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The title says what this is. How to read a difference is behind
        # the ? beside what the check found.
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head = QHBoxLayout()
        head.addWidget(self.status, 1)
        head.addWidget(HelpTip(tr(
            "Every mod that loads is run through the game's loader in one "
            "run, as the game would load them. Every asset they change is "
            "compared with what the Studio shows. Your own mods are run as "
            "Export would write them. A difference means the Studio reads a "
            "mod differently from the game, and the change it names is where "
            "to look."
        ), self.windowTitle()), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = listing_table([tr(column) for column in COLUMNS], tr("Assets that differ"))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selected)
        enter_activates(self.table)
        self.table.itemActivated.connect(lambda item: self.open_row(item.row()))
        self.table.setToolTip(tr("Double-click a row or press Enter to open the change it names."))
        # Sized here and not with the verdict: while the loader is asked the
        # table is empty, and its headings were cut off at Qt's own widths.
        header = self.table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 240)
        split.addWidget(self.table)
        self.sorting = sortable(self.table)

        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 6, 0, 0)
        inner.setSpacing(4)
        caption = QLabel(tr("The asset, as the Studio shows it and as the loader left it"))
        caption.setObjectName("SubHeading")
        inner.addWidget(caption)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(tr("Select a row to see where the two differ."))
        self.details.setAccessibleName(caption.text())
        inner.addWidget(self.details, 1)
        split.addWidget(holder)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        # What was left out, and what the loader warned: below the list, since
        # it qualifies the whole of it.
        self.notes = QLabel("")
        self.notes.setObjectName("StepHint")
        self.notes.setWordWrap(True)
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.stop_button = QPushButton(tr("&Stop"))
        self.stop_button.clicked.connect(self.stop)
        buttons.addButton(self.stop_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        outer.addWidget(buttons)
        self.stop_button.setVisible(False)

    # -- running it -------------------------------------------------------
    def start(self) -> None:
        from .workers import LoadoutCheckWorker

        worker = LoadoutCheckWorker(self.job, self.index, self.loader, self)
        worker.progressed.connect(self.status.setText)
        worker.succeeded.connect(self.show_verdict)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self.stop_button.setVisible(True)
        self.status.setText(tr("Starting…"))
        worker.start()

    def stop(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.requestInterruption()
            self.status.setText(tr("Stopping…"))

    def done(self, result: int) -> None:
        """Closing stops a check still running: it holds the loader's work
        folder, and the answer would have nowhere to go."""
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.requestInterruption()
            worker.wait(120000)
        super().done(result)

    # -- what it found ----------------------------------------------------
    def show_verdict(self, verdict) -> None:
        from ..core import loadercheck

        self._worker = None
        self.stop_button.setVisible(False)
        self.verdict = verdict
        self.status.setText(verdict.describe())
        theme.tint(self.status, "warn" if verdict.state in (loadercheck.DIFFERS, loadercheck.FAILED) else "text")
        rows = verdict.differing
        self.table.setRowCount(len(rows))
        for row, found in enumerate(rows):
            asset = QTableWidgetItem(found.get("label") or found["guid"])
            asset.setToolTip("GUID %s" % found["guid"])  # i18n: keep - a GUID is a name
            self.table.setItem(row, 0, asset)
            mods = found.get("mods") or []
            by = QTableWidgetItem(", ".join(mods) if mods else tr("No mod's change: a copy or a search"))
            by.setToolTip("\n".join(mods))
            self.table.setItem(row, 1, by)
            fields = QTableWidgetItem(", ".join(found.get("fields") or []))
            self.table.setItem(row, 2, fields)
            changes = found.get("changes") or []
            said = "; ".join("%s: %s" % (change["mod"], change["said"]) for change in changes[:3])
            if len(changes) > 3:
                said = trn("%(changes)s and %(n)s more", "%(changes)s and %(n)s more", len(changes) - 3,
                           changes=said)
            there = QTableWidgetItem(said)
            there.setToolTip("\n".join("%s: %s" % (change["mod"], change["said"]) for change in changes))
            if changes:
                theme.mark(there, "warn")
            self.table.setItem(row, 3, there)
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()
        self.notes.setText(self._notes(verdict))
        if rows:
            self.table.selectRow(0)

    @staticmethod
    def _notes(verdict) -> str:
        lines = []
        if verdict.differing_count > len(verdict.differing):
            lines.append(tr("Listed: the first %(shown)s of %(total)s.", shown=number(len(verdict.differing)),
                            total=number(verdict.differing_count)))
        if verdict.updated:
            lines.append(tr(
                "Left out, because they were updated on disk since the "
                "loadout read them: %s. Read the mods folder again to check "
                "them.", ", ".join(verdict.updated)))
        if verdict.missing:
            lines.append(tr("Left out, not where the loadout read them from: %s.", ", ".join(verdict.missing)))
        if verdict.warnings:
            said = ["%s (%s)" % (name, number(len(found))) for name, found in verdict.warnings.items()]
            lines.append(tr("Ops the loader found nothing for, by mod: %s. Each mod's own Export tab "
                            "check lists them.", ", ".join(said)))
        return "\n".join(lines)

    def _on_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1 or self.verdict is None:
            return
        found = self.verdict.differing[rows.pop()]
        lines = ["%s (GUID %s)" % (found.get("label") or found["guid"], found["guid"])]
        if found.get("fields"):
            lines.append(tr("They differ at: %s", ", ".join(found["fields"])))
        for change in found.get("changes") or []:
            lines.append("  %s: %s" % (change["mod"], change["said"]))
        lines.append("")
        lines.extend(found.get("diff") or [])
        self.details.setPlainText("\n".join(lines))

    def open_row(self, row: int) -> None:
        if self.verdict is None or not 0 <= row < len(self.verdict.differing):
            return
        changes = self.verdict.differing[row].get("changes") or []
        if changes:
            # Closed on the way, as the conflicts list is: the change opens in
            # the window behind. The result is kept, and the menu shows it again.
            self.changeActivated.emit(changes[0]["guid"], changes[0]["path"])
            self.accept()
