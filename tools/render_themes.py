"""Render the Studio under its themes, for review by eye.

    python tools/render_themes.py <out dir>              the whole set
    python tools/render_themes.py <out dir> --compare    the six schemes only,
                                                         each against its reference
    python tools/render_themes.py <out dir> --every      every scheme in every
                                                         style, at 100% and 200%

The whole set is the four main tabs, every registered dialog
(``tests/design_dialogs.py``) and the asset editor over two loaded mods that
set one field (``5_stack``, with the menu on that row, and ``6_xml``, the
same asset in the XML view), rendered:

- in every colour scheme, with the default style
- Flat and Compact, on Granite and Marble
- at 200%, on Granite
- in German, on Granite

``--every`` is the set a review of the whole look needs, and the one the
design review closed on: each of the six schemes as it comes, Flat and
Compact, each of those at 100% and at 200%, and German on Granite. It is 37
renders and takes a while.

Each render runs in a process of its own, because interface size and
language are read once at startup. Settings, caches and the library go to a
temporary folder, and nothing is shown on screen. The result is a folder of
pictures and ``index.html``, a contact sheet of them.

``--compare`` is Stage 0's acceptance check (docs/design-review/README.md).
The scenario is the one the approved renders were made in, with the export
folder pinned, so a render can be compared pixel for pixel
with its reference in ``docs/design-review/renders/``. The contact sheet
then shows the built render, the reference and their difference, with the
number of pixels that changed. It exits 1 if any did.

This is a tool for looking, and no check runs it: the look needs eyes. The
renders are looked at once per release, on the release branch
(``docs/standards/release-checklist.md``).
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "docs" / "design-review" / "renders"
WIDTH, HEIGHT = 1500, 950
TABS = ("1_start", "2_editor", "3_export", "4_refs")
# The asset editor on a field one loaded mod makes and a later one sets, and
# the menu on that row. Not in the comparison set: it has no approved render.
STACK = "5_stack"
XML = "6_xml"
SCHEMES = ("granite", "brick", "marble", "timber", "obsidian", "quartz")
# Differing pixels are painted in this over a faded copy of the render.
DIFFERENCE_MARK = "#ff00c8"


def variants(compare: bool, every: bool = False) -> list:
    """Every render to make: (name, scheme, outlines, density, scale, language)."""
    rows = [(scheme, scheme, "boxed", "comfortable", 100, "en") for scheme in SCHEMES]
    if compare:
        return rows
    if every:
        rows = []
        for scheme in SCHEMES:
            for style, outlines, density in (("", "boxed", "comfortable"),
                                             ("-flat", "flat", "comfortable"),
                                             ("-compact", "boxed", "compact")):
                rows.append((scheme + style, scheme, outlines, density, 100, "en"))
                rows.append((scheme + style + "-200", scheme, outlines, density, 200, "en"))
        rows.append(("granite-de", "granite", "boxed", "comfortable", 100, "de"))
        return rows
    for scheme in ("granite", "marble"):
        rows.append((scheme + "-flat", scheme, "flat", "comfortable", 100, "en"))
        rows.append((scheme + "-compact", scheme, "boxed", "compact", 100, "en"))
    rows.append(("granite-200", "granite", "boxed", "comfortable", 200, "en"))
    rows.append(("granite-de", "granite", "boxed", "comfortable", 100, "de"))
    return rows


def keep_off_screen(app) -> None:
    """Nothing ``app`` shows from here on reaches the screen or takes the focus.

    The windows platform draws real text, and it also puts every window
    that is shown on the desktop: the progress dialog of the index build,
    a menu, a tooltip. Each takes the focus from whatever has it, and a
    fullscreen game minimises. A scratch script that builds the Studio to
    take pictures of it calls this too.
    """
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QWidget

    hidden = Qt.WidgetAttribute.WA_DontShowOnScreen

    class OffScreen(QObject):
        def eventFilter(self, watched, event):
            # Polish comes before a first show, and Show before the window
            # is put on screen: the second is for one polished as a child.
            if (event.type() in (QEvent.Type.Polish, QEvent.Type.Show)
                    and isinstance(watched, QWidget) and watched.isWindow()
                    and not watched.testAttribute(hidden)):
                watched.setAttribute(hidden, True)
            return False

    app.installEventFilter(OffScreen(app))


# -- one render, in its own process -------------------------------------------
def render_one(name, scheme, outlines, density, scale, lang, out: Path, dialogs: bool) -> int:
    # Under %PUBLIC%, not %TEMP%: the Export tab shows the export folder, and
    # a render under %TEMP% would carry the user's name into the repository.
    work = Path(os.environ.get("PUBLIC") or tempfile.gettempdir()) / "studio-render-pinned"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for variable, sub in (("SETTINGS", "iso.ini"), ("HOME", "home"), ("CACHE", "cache"),
                          ("LIBRARY", "library"), ("MODIO", "modio"),
                          ("MODIO_USERS", "modio_users")):
        os.environ["ANNO_STUDIO_" + variable] = str(work / sub)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    sample = work / "sample"
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_sample_assets.py"), str(sample)],
                   check=True, capture_output=True)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))

    from unittest import mock

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import (
        QApplication,
        QInputDialog,
        QMessageBox,
        QPushButton,
        QTreeWidgetItemIterator,
    )

    from anno_modding_studio.ui import appearance, language

    settings = QSettings(str(work / "iso.ini"), QSettings.Format.IniFormat)
    settings.setValue(appearance.SCHEME_KEY, scheme)
    settings.setValue(appearance.OUTLINES_KEY, outlines)
    settings.setValue(appearance.DENSITY_KEY, density)
    settings.setValue(appearance.SIZE_KEY, scale)
    settings.setValue(language.LANGUAGE_KEY, lang)
    settings.sync()

    # Started the way app.main starts it.
    token = appearance.set_scale(appearance.saved_size(settings))
    app = QApplication([])
    appearance.clear_scale(token)
    keep_off_screen(app)
    app.setStyle("Fusion")
    appearance.apply_saved(app, settings)
    language.apply(app, settings)

    from anno_modding_studio.ui.main_window import MainWindow
    from anno_modding_studio.ui.navigation import TAB_GENERATE, TAB_REFS

    def settle(seconds=1.0):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(widget, what):
        settle(0.3)
        widget.grab().save(str(out / ("%s_%s.png" % (name, what))))

    win = MainWindow()
    win.resize(WIDTH, HEIGHT)
    win.show()
    settle(0.5)
    win.game_files.open_assets(sample / "assets.xml", asked=True)
    for _ in range(200):
        settle(0.2)
        if win.index is not None:
            break
    settle(1.5)
    shot(win, TABS[0])

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
    shot(win, TABS[1])
    win.tabs.setCurrentIndex(TAB_GENERATE)
    shot(win, TABS[2])
    win.tabs.setCurrentIndex(TAB_REFS)
    win.references.depth_spin.setValue(2)
    settle(1)
    shot(win, TABS[3])

    if dialogs:
        import design_dialogs

        for key, factory in design_dialogs.DIALOGS.items():
            dialog = factory(win)
            dialog.show()
            shot(dialog, "dialog_" + key)
            dialog.close()
            dialog.deleteLater()
        # Last, because it loads mods into the loadout the dialogs were shown on.
        stacked(win, shot, settle)
    return 0


def stacked(win, shot, settle) -> None:
    """The asset editor where two loaded mods set one field, and that row's menu.

    *Hearths* makes two fields the game does not ship. *Warmer Hearths*, which
    loads after it and is the mod being edited into, makes sure of one with a
    value of its own and replaces the other with the value it already has. So
    the picture holds the three wordings that exist only over a stack: "set
    by this mod" on the row, "Stop setting this" in its menu, and a change
    that restates another mod's value in the Mod changes panel.
    """
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    from anno_modding_studio.core.changes import OP_MERGE, Change, ModLayer

    guid, section = "1010032", "/Values/Residence7"
    first, second = "[Gameplay] Hearths", "[Gameplay] Warmer Hearths"
    loadout = win.loadout
    for order, origin in enumerate((first, second), 1):
        loadout.set_layer(ModLayer(origin=origin, name=origin, order=order))

    def ensure(origin, tag, value):
        return Change(
            guid=guid, asset_label="Artisan Residence", path=section, tag=tag,
            op=OP_MERGE, origin=origin, authored=False,
            payload="<%s>%s</%s>" % (tag, value, tag),
        )

    for change in (
        ensure(first, "Hearths", "1"),
        ensure(first, "Chimneys", "2"),
        ensure(second, "Hearths", "3"),
        Change(
            guid=guid, asset_label="Artisan Residence", path=section + "/Chimneys",
            tag="Chimneys", old_value="2", new_value="2", restated=True,
            origin=second, authored=False,
        ),
    ):
        loadout.set_change(loadout.place(change))
    loadout.editing = second
    win.editor.refresh_target()
    win.tabs.setCurrentIndex(1)
    win.editor.load_asset(guid)
    win.changes_panel.refresh()
    settle(0.5)
    win.editor.tree.expandAll()
    win.changes_panel.tree.expandAll()
    row = None
    items = QTreeWidgetItemIterator(win.editor.tree)
    while items.value():
        if items.value().text(0) == "Hearths":
            row = items.value()
        items += 1
    if row is None:
        return
    win.editor.tree.setCurrentItem(row)
    shot(win, STACK)
    menu = win.editor._build_item_menu(row)
    menu.show()
    shot(menu, STACK + "_menu")
    menu.close()
    # The same asset as XML, on the tab of the mod that loads first: the one
    # after it writes over a field it sets, which is what the tab's warning
    # sign, the shaded line and the sentence under the panes are for.
    loadout.editing = first
    win.editor.refresh_target()
    win.editor.load_asset(guid)
    win.changes_panel.refresh_marks()
    win.editor.set_view("xml")
    shot(win, XML)


# -- comparing and the contact sheet ------------------------------------------
def difference(built: Path, reference: Path, to: Path):
    """How many pixels differ, and a picture of where. None if sizes differ."""
    from PySide6.QtGui import QColor, QImage, QPainter

    a = QImage(str(built)).convertToFormat(QImage.Format.Format_RGB32)
    b = QImage(str(reference)).convertToFormat(QImage.Format.Format_RGB32)
    if a.size() != b.size():
        return None
    marked = QImage(a)
    painter = QPainter(marked)
    painter.fillRect(marked.rect(), QColor(0, 0, 0, 150))
    mark = QColor(DIFFERENCE_MARK)
    changed = 0
    for y in range(a.height()):
        ours, theirs = bytes(a.constScanLine(y)), bytes(b.constScanLine(y))
        if ours == theirs:
            continue
        for x in range(a.width()):
            if ours[4 * x:4 * x + 4] != theirs[4 * x:4 * x + 4]:
                changed += 1
                painter.fillRect(x, y, 1, 1, mark)
    painter.end()
    marked.save(str(to))
    return changed


def contact_sheet(out: Path, rows: list, compare: bool) -> int:
    """Write index.html. The number of renders that differ from a reference.

    Comparing, that is counted over the pictures there should be and not the
    ones found: a picture with no reference and a reference or a tab with no
    picture each count as one.  Counted over what was found, a run that
    rendered nothing compared nothing and passed.
    """
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - images need one
    differing = 0
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Studio renders</title>",
        "<style>body{font:14px Segoe UI,sans-serif;margin:16px}"
        "img{max-width:100%;border:1px solid #888}"
        ".row{display:grid;gap:8px;margin-bottom:24px}"
        ".three{grid-template-columns:repeat(3,1fr)}h2{margin:24px 0 8px}</style>",
        "<h1>Studio renders</h1>",
    ]
    for name, *_rest in rows:
        parts.append("<h2>%s</h2>" % html.escape(name))
        built = sorted(out.glob(name + "_*.png"))
        if compare:
            # Only the pictures a --compare render makes: the folder is not
            # cleared, and the whole set writes the stack and the dialogs
            # under the same name.
            made = {"%s_%s.png" % (name, tab) for tab in TABS}
            built = [picture for picture in built if picture.name in made]
            missing = sorted(
                (made | {path.name for path in REFERENCES.glob(name + "_*.png")})
                - {picture.name for picture in built}
            )
            for absent in missing:
                differing += 1
                print("%-28s %s" % (absent, "not rendered"))
                parts.append("<p>%s: not rendered</p>" % html.escape(absent))
        for picture in built:
            reference = REFERENCES / picture.name
            if compare and not reference.is_file():
                differing += 1
                print("%-28s %s" % (picture.name, "no reference"))
                parts.append("<p>%s: no reference</p><div class='row'><img src='%s'></div>"
                             % (html.escape(picture.name), picture.name))
            elif compare:
                diff = out / ("diff_" + picture.name)
                changed = difference(picture, reference, diff)
                differing += bool(changed is None or changed)
                said = "sizes differ" if changed is None else "%d pixels differ" % changed
                print("%-28s %s" % (picture.name, said))
                parts.append(
                    "<p>%s: %s</p><div class='row three'><img src='%s' title='built'>"
                    "<img src='%s' title='reference'><img src='%s' title='difference'></div>"
                    % (html.escape(picture.name), said, picture.name,
                       reference.as_uri(), diff.name)
                )
            else:
                parts.append("<p>%s</p><div class='row'><img src='%s'></div>"
                             % (html.escape(picture.name), picture.name))
    (out / "index.html").write_text("\n".join(parts), encoding="utf-8")
    return differing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--compare", action="store_true",
                        help="the six schemes, each against its reference render")
    parser.add_argument("--every", action="store_true",
                        help="every scheme in every style, at 100%% and 200%%")
    parser.add_argument("--only", default="", help="render one variant by name")
    parser.add_argument("--one", nargs=6, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.one:
        name, scheme, outlines, density, scale, lang = args.one
        return render_one(name, scheme, outlines, density, int(scale), lang, args.out,
                          dialogs=not args.compare)

    known = variants(args.compare, args.every)
    rows = [row for row in known if args.only in ("", row[0])]
    if not rows:
        # A name that matches nothing rendered nothing, and with --compare
        # that passed.
        parser.error("no variant is called %r; there are: %s"
                     % (args.only, ", ".join(row[0] for row in known)))
    for row in rows:
        command = [sys.executable, __file__, str(args.out), "--one", *map(str, row)]
        if args.compare:
            command.append("--compare")
        print("rendering %s" % row[0], flush=True)
        done = subprocess.run(command, cwd=ROOT)
        if done.returncode:
            print("  failed with exit code %d" % done.returncode)
            return done.returncode
    differing = contact_sheet(args.out, rows, args.compare)
    print("contact sheet: %s" % (args.out / "index.html"))
    return 1 if differing else 0


if __name__ == "__main__":
    raise SystemExit(main())
