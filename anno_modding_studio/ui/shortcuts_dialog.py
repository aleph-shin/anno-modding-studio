"""The keyboard shortcuts, read off the window that has them.

The menus' rows come from the window's own actions and the back and forward
keys from its named shortcuts, so a shortcut that is added, moved or taken
away is right here without anyone remembering to say so.  What is left is
the handful of keys a widget answers in its own ``keyPressEvent``, which no
action stands for.  Those are written down in ``WIDGET_KEYS``.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core.i18n import tr, tr_noop

# The property a QShortcut says what it does with. One without it is left out.
# An action in a menu is listed by its text, and by this where it has one:
# an entry of a submenu whose text is only half of what it does.
NAME = "shortcut_name"

# Keys a widget answers itself: (where, ((what it does, the keys), ...)).
WIDGET_KEYS = (
    (tr_noop("Reference graph"), (
        (tr_noop("Move between assets"), tr_noop("Arrow keys")),
        (tr_noop("Centre the graph on the asset"), tr_noop("Enter")),
        (tr_noop("Open the asset in the editor"), tr_noop("Ctrl+Enter")),
        (tr_noop("Open the asset's menu"), tr_noop("Menu key, Shift+F10")),
    )),
    (tr_noop("Fields view"), (
        (tr_noop("Copy the field and everything in it"), tr_noop("Ctrl+C")),
        (tr_noop("Paste a copied field in, or beside, the one selected"), tr_noop("Ctrl+V")),
    )),
    (tr_noop("XML view"), (
        (tr_noop("Find the next match"), tr_noop("Enter, F3")),
        (tr_noop("Find the match before"), tr_noop("Shift+F3")),
        (tr_noop("Close the find bar"), tr_noop("Esc")),
        (tr_noop("Apply the ModOps you typed"), tr_noop("Ctrl+Enter")),
        (tr_noop("Indent, or indent less, where you type ModOps"), tr_noop("Tab, Shift+Tab")),
        (tr_noop("Leave the pane you type ModOps in"), tr_noop("Esc, then Tab")),
    )),
    (tr_noop("Mod file"), (
        (tr_noop("Find the next match"), tr_noop("Enter, F3")),
        (tr_noop("Find the match before"), tr_noop("Shift+F3")),
        (tr_noop("Close the find bar"), tr_noop("Esc")),
        (tr_noop("Apply what you typed in the file"), tr_noop("Ctrl+Enter")),
        (tr_noop("Indent, or indent less"), tr_noop("Tab, Shift+Tab")),
        (tr_noop("Leave the file with the keyboard"), tr_noop("Esc, then Tab")),
    )),
    (tr_noop("Lists"), (
        (tr_noop("Do what a double-click does"), tr_noop("Enter")),
    )),
    (tr_noop("Help icons"), (
        (tr_noop("Show or hide the help"), tr_noop("Enter, F1")),
        (tr_noop("Hide the help"), tr_noop("Esc")),
    )),
)

Group = Tuple[str, List[Tuple[str, str]]]


def plain(text: str) -> str:
    """An action's text without its mnemonic mark or its trailing dots."""
    return re.sub(r"&(?!&)", "", text).replace("&&", "&").rstrip("… .")


def keys_of(sequences) -> str:
    """The keys of one action, one to a line: Redo has four."""
    return "\n".join(
        sequence.toString(QKeySequence.SequenceFormat.NativeText)
        for sequence in sequences if not sequence.isEmpty()
    )


def _menu_rows(menu) -> List[Tuple[str, str]]:
    rows = []
    for action in menu.actions():
        if action.menu() is not None:
            rows.extend(_menu_rows(action.menu()))
        elif action.isVisible() and keys_of(action.shortcuts()):
            what = action.property(NAME) or plain(action.text())
            rows.append((what, keys_of(action.shortcuts())))
    return rows


def gather(window) -> List[Group]:
    """Every shortcut ``window`` has, grouped by where it is found."""
    groups: List[Group] = []
    for top in window.menuBar().actions():
        rows = _menu_rows(top.menu()) if top.menu() is not None else []
        if rows:
            groups.append((plain(top.text()), rows))
    named = []
    for shortcut in window.findChildren(QShortcut):
        row = (shortcut.property(NAME) or "", keys_of([shortcut.key()]))
        # Each tab has its own pair for the same thing.
        if row[0] and row[1] and row not in named:
            named.append(row)
    if named:
        groups.append((tr("Assets you have visited"), named))
    for where, rows in WIDGET_KEYS:
        groups.append((tr(where), [(tr(what), tr(keys)) for what, keys in rows]))
    return groups


class ShortcutsDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent if parent is not None else window)
        self.setWindowTitle(tr("Keyboard shortcuts"))
        self.resize(560, 640)
        layout = QVBoxLayout(self)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(tr("Keyboard shortcuts"))
        self.tree.setHeaderLabels([tr("Action"), tr("Keys")])
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        # The keys always fit. An action's name is cut short where it is long:
        # Undo names the change it would undo.
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for where, rows in gather(window):
            group = QTreeWidgetItem(self.tree, [where])
            group.setFirstColumnSpanned(True)
            font = group.font(0)
            font.setWeight(font.Weight.DemiBold)
            group.setFont(0, font)
            for what, keys in rows:
                QTreeWidgetItem(group, [what, keys])
        self.tree.expandAll()
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("&Close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
