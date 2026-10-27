"""List the English still on screen once every catalog sentence is translated.

Installs a pseudo-catalog in which every marked sentence reads ⟦itself⟧
(``translations.pseudo``), builds a window, and walks every widget in it:
text, tooltips, titles, placeholders, headers, menu entries, combo items.
Whatever has words outside the brackets never went through ``tr`` -- built
by a helper, kept in a variable, or simply missed.  ``lint`` cannot see those;
this can, for whatever the window has built by the time it looks.

    python tools/untranslated_widgets.py

Run it with the suite's isolation, which it sets up itself, so the window it
builds reads and writes nothing of yours.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import _isolation  # noqa: E402,F401 -- first: settings, home and cache go to temp folders
import translations  # noqa: E402

_BRACKETED = re.compile(r"⟦[^⟦⟧]*⟧")
_WORDS = re.compile(r"[A-Za-z]{2,}")


def bare(text: str) -> str:
    """What is left of ``text`` outside every ⟦…⟧, innermost first."""
    while True:
        shorter = _BRACKETED.sub(" ", text)
        if shorter == text:
            return text
        text = shorter


def texts_of(widget) -> Iterable[Tuple[str, str]]:
    """``(what, text)`` for everything a widget shows in words."""
    from PySide6.QtWidgets import (
        QAbstractButton,
        QComboBox,
        QGroupBox,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMenu,
        QTabBar,
        QTreeWidget,
        QWidget,
    )

    if not isinstance(widget, QWidget):
        return
    yield "tooltip", widget.toolTip()
    if widget.isWindow():
        yield "title", widget.windowTitle()
    if isinstance(widget, (QLabel, QAbstractButton)):
        yield "text", widget.text()
    if isinstance(widget, QGroupBox):
        yield "title", widget.title()
    if isinstance(widget, QLineEdit):
        yield "placeholder", widget.placeholderText()
    if isinstance(widget, QComboBox):
        for index in range(widget.count()):
            yield "item", widget.itemText(index)
    if isinstance(widget, QTabBar):
        for index in range(widget.count()):
            yield "tab", widget.tabText(index)
            yield "tab tooltip", widget.tabToolTip(index)
    if isinstance(widget, QMenu):
        yield "menu", widget.title()
        for action in widget.actions():
            yield "action", action.text()
            yield "action tip", action.toolTip() if action.toolTip() != action.text().replace("&", "") else ""
    if isinstance(widget, QTreeWidget):
        header = widget.headerItem()
        for column in range(header.columnCount()):
            yield "header", header.text(column)
    if isinstance(widget, QHeaderView) and widget.model() is not None:
        model = widget.model()
        from PySide6.QtCore import Qt

        count = model.columnCount() if widget.orientation() == Qt.Orientation.Horizontal else 0
        for column in range(count):
            value = model.headerData(column, Qt.Orientation.Horizontal)
            yield "header", str(value or "")


def untranslated(root, ignore: Iterable[str] = ()) -> List[str]:
    """Every piece of English under ``root`` that bypassed the catalog."""
    from PySide6.QtWidgets import QWidget

    ignored = [re.compile(pattern) for pattern in ignore]
    found = []
    for widget in [root, *root.findChildren(QWidget)]:
        for what, text in texts_of(widget):
            left = bare(text or "")
            if not _WORDS.search(left):
                continue
            if any(pattern.search(left) for pattern in ignored):
                continue
            name = widget.objectName() or type(widget).__name__
            found.append("%s %s: %r" % (name, what, text[:100]))
    return sorted(set(found))


def install_pseudo() -> None:
    from anno_modding_studio.core import i18n

    folder = Path(tempfile.mkdtemp(prefix="ams-pseudo-"))
    translations.pseudo(folder)
    assert i18n.install("qps", folder)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    install_pseudo()
    from anno_modding_studio.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    for line in untranslated(window):
        print(line)
    from anno_modding_studio.core import secrets
    from anno_modding_studio.ui.settings_dialog import SettingsDialog

    # An empty credential store of its own: this reads nobody's mod.io key.
    store = secrets.Store(Path(tempfile.mkdtemp(prefix="ams-no-key-")) / "modio")
    dialog = SettingsDialog(window, window, store=store)
    for line in untranslated(dialog):
        print(line)
    window.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
