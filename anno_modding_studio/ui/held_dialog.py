"""Edits an update held back: their op as it was, as it is now, and yours.

When the author updates a mod you edited, each op of theirs an edit of yours
was made to is looked for in the new version (:mod:`core.held`).  One that
is still there takes the edit as before.  One the update changed or took
away holds the edit back -- not applied, nothing dropped -- until it is
decided here, one at a time, with the three side by side: what you edited,
what it is now, and what you made of it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import build, generator, held
from ..core.i18n import tr
from .asset_xml import XmlPane

_UID = Qt.ItemDataRole.UserRole


def describe(loadout, record: dict) -> str:
    """One line for a held edit: whose mod, and what became of the op."""
    layer = loadout.layer(record.get("origin", ""))
    mod = layer.label if layer is not None else record.get("origin", "")
    kind, why = record.get("kind"), record.get("why")
    if kind == held.REPLACED:
        said = (tr("an op you typed over, changed by the update") if why == held.CHANGED
                else tr("an op you typed over, gone from the update"))
    elif kind == held.TAKEN_OUT:
        said = (tr("an op you took out, changed by the update") if why == held.CHANGED
                else tr("an op you took out, gone from the update"))
    else:
        said = (tr("an op you switched off, changed by the update") if why == held.CHANGED
                else tr("an op you switched off, gone from the update"))
    # Translators: %(mod)s is a mod, %(what)s one of the lines above.
    return tr("%(mod)s: %(what)s", mod=mod, what=said)


def actions(record: dict) -> tuple:
    """``(what "apply mine" does, what "keep theirs" does)`` for a held edit,
    as its buttons say them; "" for one that has nothing to do."""
    kind, why = record.get("kind"), record.get("why")
    if kind == held.REPLACED:
        if why == held.CHANGED:
            return tr("&Use mine on the new version"), tr("&Keep theirs, drop mine")
        return tr("&Keep mine on its own"), tr("&Drop mine")
    if why == held.GONE:
        return "", tr("&Forget it")
    if kind == held.TAKEN_OUT:
        return tr("&Take the new one out too"), tr("&Keep the new one")
    return tr("Switch the new one &off too"), tr("&Keep the new one on")


def yours_text(loadout, record: dict) -> str:
    """What of yours stood in place of their op, as it is written."""
    origin = record.get("origin", "")
    parts = []
    for kind, which in record.get("yours") or ():
        if kind == "op":
            op = loadout.op(str(which))
            if op is not None:
                parts.append(generator.laid_out_op(op.source))
        elif tuple(which) in loadout.changes:
            parts.append(build.fragment_xml(loadout, origin, keys=[tuple(which)]).strip())
    return "\n\n".join(parts)


class HeldDialog(QDialog):
    """Every edit an update held back, or one mod's, to decide one by one.
    ``done`` is called after each decision, so the window can record it as a
    step and redraw."""

    def __init__(self, loadout, parent=None, origin: Optional[str] = None, done=None):
        super().__init__(parent)
        self.loadout = loadout
        self.origin = origin
        self._done = done
        self.setWindowTitle(tr("Edits held after an update"))
        self.resize(980, 620)
        outer = QVBoxLayout(self)
        lead = QLabel(tr(
            "The author changed or took away these ops of theirs, which you had "
            "edited. Until you decide, your edit is not applied and the game gets "
            "their new version."
        ))
        lead.setWordWrap(True)
        outer.addWidget(lead)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Edits held after an update"))
        self.list.currentItemChanged.connect(self._show)
        split.addWidget(self.list)
        panes = QWidget()
        side = QHBoxLayout(panes)
        side.setContentsMargins(0, 0, 0, 0)
        self.was, self.now, self.yours = XmlPane(), XmlPane(), XmlPane()
        for pane, title in ((self.was, tr("Theirs, as you edited it")),
                            (self.now, tr("Theirs, in the new version")),
                            (self.yours, tr("Yours"))):
            column = QVBoxLayout()
            heading = QLabel(title)
            heading.setObjectName("SubHeading")
            column.addWidget(heading)
            pane.setReadOnly(True)
            pane.setAccessibleName(title)
            column.addWidget(pane, 1)
            side.addLayout(column, 1)
        split.addWidget(panes)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        row = QHBoxLayout()
        self.mine_button = QPushButton()
        self.mine_button.clicked.connect(self.apply_mine)
        self.theirs_button = QPushButton()
        self.theirs_button.clicked.connect(self.keep_theirs)
        row.addWidget(self.mine_button)
        row.addWidget(self.theirs_button)
        row.addStretch(1)
        outer.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # Translators: closes the window; what is held stays held.
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("Decide &later"))
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.refill()

    def records(self) -> list:
        return held.of(self.loadout, self.origin)

    def refill(self) -> None:
        self.list.clear()
        for record in self.records():
            item = QListWidgetItem(describe(self.loadout, record))
            item.setData(_UID, record.get("uid"))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show(None)

    def current(self):
        item = self.list.currentItem()
        uid = item.data(_UID) if item is not None else None
        return next((record for record in self.records() if record.get("uid") == uid), None)

    def _show(self, *_args) -> None:
        record = self.current()
        if record is None:
            for pane in (self.was, self.now, self.yours):
                pane.setPlainText("")
            self.mine_button.setVisible(False)
            self.theirs_button.setVisible(False)
            return
        self.was.setPlainText(generator.laid_out_op(record.get("was", "")))
        self.now.setPlainText(
            generator.laid_out_op(record["now"]) if record.get("now")
            else tr("Not in the new version.")
        )
        mine = yours_text(self.loadout, record)
        self.yours.setPlainText(mine or tr("You put no op of yours in its place."))
        apply_words, keep_words = actions(record)
        self.mine_button.setText(apply_words)
        self.mine_button.setVisible(bool(apply_words))
        self.theirs_button.setText(keep_words)
        self.theirs_button.setVisible(True)

    def apply_mine(self) -> None:
        record = self.current()
        if record is not None:
            held.apply_mine(self.loadout, record)
            self._decided()

    def keep_theirs(self) -> None:
        record = self.current()
        if record is not None:
            held.keep_theirs(self.loadout, record)
            self._decided()

    def _decided(self) -> None:
        if self._done is not None:
            self._done()
        self.refill()
        if not self.list.count():
            self.accept()
