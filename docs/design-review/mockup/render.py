"""Render the Studio under one approved colour scheme, as the design review did.

    python docs/design-review/mockup/render.py <studio checkout> <scheme> <out dir>
        [--flat] [--scale 1.5]

``<studio checkout>`` must be the app as of commit ``cb10f5a``: this patches
that version's ``ui/theme.py`` from outside, and stops working once Stage 0
replaces it (see ``../README.md``). ``<scheme>`` is a key of
``schemes.SCHEMES`` or ``schemes.REJECTED``. ``--flat`` lays Graphite's
flattening and density over the scheme.

The scenario is fixed so renders can be compared: the sample game files from
``tools/make_sample_assets.py``, a new mod called Better Bricks with two edits
to the Artisan Residence, and the Start, Asset editor, Export and References
tabs in that order, at a 1500 x 950 window. Settings, caches and the library
go to a temporary folder; nothing is shown on screen.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1500, 950
SHOTS = ("1_start", "2_editor", "3_export", "4_refs")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("studio", type=Path)
    parser.add_argument("scheme")
    parser.add_argument("out", type=Path)
    parser.add_argument("--flat", action="store_true")
    parser.add_argument("--scale", default="")
    args = parser.parse_args(argv)

    # Under %PUBLIC%, not %TEMP%, so "Export to" names no user.
    work = Path(tempfile.mkdtemp(prefix="studio-render-", dir=os.environ.get("PUBLIC")))
    for name, sub in (("SETTINGS", "iso.ini"), ("HOME", "home"), ("CACHE", "cache"),
                      ("LIBRARY", "library"), ("MODIO", "modio"),
                      ("MODIO_USERS", "modio_users")):
        os.environ["ANNO_STUDIO_" + name] = str(work / sub)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    if args.scale:
        os.environ["QT_SCALE_FACTOR"] = args.scale
    sample = work / "sample"
    subprocess.run([sys.executable, str(args.studio / "tools" / "make_sample_assets.py"),
                    str(sample)], check=True, capture_output=True)
    sys.path.insert(0, str(args.studio))
    sys.path.insert(0, str(HERE))
    args.out.mkdir(parents=True, exist_ok=True)

    import schemes
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import (
        QApplication,
        QInputDialog,
        QMessageBox,
        QPushButton,
        QTreeWidgetItemIterator,
    )

    app = QApplication([])
    app.setStyle("Fusion")
    from anno_modding_studio.ui import modio_brand, output_panel, theme

    t = dict(schemes.SCHEMES.get(args.scheme) or schemes.REJECTED[args.scheme])
    if args.flat:
        t.update(schemes.GRAPHITE_STYLE)
    px = 14
    schemes.apply_tokens(theme, t)
    ind = schemes.indicators(t, work / "indicators")
    css = schemes.base_with_tokens(theme, t, px) + schemes.overrides(t, ind, px)
    theme.FONT_PX, theme.SMALL_PX, theme.STYLESHEET = px, px - 1, css
    app.setPalette(schemes.palette(t))
    app.setStyleSheet(css)

    # Code that reads theme.ACCENT at draw time uses it as text: give it the
    # text shade. The fill stays in the stylesheet built above.
    theme.ACCENT = t["ACCENT_TEXT"]
    # The XML preview's three colours are hard-coded in this version.
    syntax = {"#6f7a8a": t["SYN_COMMENT"], "#7aa6d6": t["SYN_TAG"],
              "#a8d99a": t["SYN_VALUE"]}
    output_panel.QColor = (
        lambda c, *a: QColor(syntax.get(c, c)) if isinstance(c, str) else QColor(c, *a)
    )
    logo = schemes.LOGOS / ("%s.svg" % t["LOGO"])

    def draw_logo(_path):
        height, ratio = 20, 2.0
        pixmap = QPixmap(int(height * 1858 / 572 * ratio), int(height * ratio))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(str(logo)).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    modio_brand.QPixmap = draw_logo
    from anno_modding_studio.ui.main_window import MainWindow

    def settle(seconds=1.0):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(widget, name):
        settle(0.3)
        widget.grab().save(str(args.out / ("%s_%s.png" % (args.scheme, name))))

    win = MainWindow()
    win.resize(WIDTH, HEIGHT)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    settle(0.5)
    win.game_files.open_assets(sample / "assets.xml", asked=True)
    for _ in range(200):
        settle(0.2)
        if win.index is not None:
            break
    settle(1.5)
    shot(win, SHOTS[0])

    with mock.patch.object(QInputDialog, "getText", return_value=("Better Bricks", True)), \
            mock.patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Yes):
        win.start.choose_aim("make")
    settle(1)
    win._open_asset("1010032")
    tree = win.editor.tree
    items = QTreeWidgetItemIterator(tree)
    edited = 0
    while items.value():
        item = items.value()
        items += 1
        if (item.childCount() == 0 and item.text(1).isdigit()
                and item.text(0) != "GUID" and edited < 2):
            item.setText(1, str(int(item.text(1)) + 5))
            edited += 1
    settle(0.5)
    tree.expandAll()
    win.editor.findChildren(QPushButton)[0].setFocus()
    shot(win, SHOTS[1])
    win.tabs.setCurrentIndex(3)
    shot(win, SHOTS[2])
    win.tabs.setCurrentIndex(2)
    win.references.depth_spin.setValue(2)
    settle(1)
    shot(win, SHOTS[3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
