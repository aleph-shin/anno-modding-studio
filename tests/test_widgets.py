"""Shared widgets: one control that behaves the same way in every dialog.

Every combo box in the app -- "Target game", the mod category, every template
picker -- is now a plain, non-editable ``QComboBox``. That was not always true:
a custom searchable combo used to back the template pickers and the category
field, so that typing could filter a list of ~1,000 templates. It kept
generating its own small inconsistencies with the plain reference control
("Target game") no matter how much it was patched -- a caret here, a
highlighted-selection bar there -- because it was, underneath, a genuinely
different kind of widget: one with a line edit, next to ones without.

The category field turned out not to need it at all: mod.io defines a short,
fixed list per game and does not accept an arbitrary category, so a plain
combo was simply the correct widget, not a compromise. The template pickers
were the harder case, but Qt's own type-ahead search (accumulate the last few
keystrokes, jump to the first item whose name starts with them) reaches any of
~1,000 templates in a few keystrokes without a line edit at all -- which is
exactly the standard-combo experience "Target game" already had, extended to a
much longer list. Losing substring-anywhere matching was the real tradeoff,
accepted because the main asset search box already does that against template
names too, so nothing near-1,000-items-long is searched *only* through the
combo.
"""

from __future__ import annotations

import html
import os
import sys
import time
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.ui import widgets  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestElideItemDelegate(unittest.TestCase):
    """A table cell keeps as much of its text as fits, never just an ellipsis.

    Regression: with a global stylesheet active, a cell paints through
    QStyleSheetStyle, which computes its own eliding -- and for a long enough
    string in a narrow enough column, that computation collapsed the text to a
    bare "…" instead of keeping the leading characters that actually fit (the
    Add a field dialog's Field column, for a long field name). Doing the
    elision ourselves, against the column's real paint-time width, is what
    fixes it; this has to be checked at a real paint, because a synthetically
    constructed style option (no view underneath it) never gets a real,
    non-zero rect to elide against in the first place.
    """

    def _painted_text(self, item_text: str, column_width: int) -> str:
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        from anno_modding_studio.ui import theme
        from anno_modding_studio.ui.widgets import ElideItemDelegate

        table = QTableWidget(1, 1)
        table.setStyleSheet(theme.STYLESHEET)
        table.horizontalHeader().hide()
        table.verticalHeader().hide()
        table.setColumnWidth(0, column_width)
        table.setItem(0, 0, QTableWidgetItem(item_text))

        captured = {}

        class Probe(ElideItemDelegate):
            def paint(self, painter, option, index):
                opt = type(option)(option)
                self.initStyleOption(opt, index)
                captured["text"] = opt.text
                captured["rect_width"] = opt.rect.width()
                super().paint(painter, option, index)

        table.setItemDelegateForColumn(0, Probe(table))
        table.resize(column_width + 40, 60)
        table.show()
        _app.processEvents()
        table.viewport().repaint()
        _app.processEvents()
        table.deleteLater()
        return captured

    def test_a_long_name_in_a_narrow_column_keeps_its_leading_characters(self):
        long_name = "▸ ResearchCenterMissingElectricity"
        captured = self._painted_text(long_name, column_width=150)
        self.assertGreater(captured["rect_width"], 0, "the probe needs a real paint")
        # The actual regression: this used to render as "…" alone, discarding
        # every leading character rather than keeping what fits.
        self.assertNotEqual(captured["text"].strip(), "…")
        self.assertTrue(captured["text"].startswith("▸ Resea"), captured["text"])
        self.assertTrue(captured["text"].endswith("…"))
        self.assertLess(len(captured["text"]), len(long_name))

    def test_a_name_that_fits_is_left_alone(self):
        captured = self._painted_text("Product", column_width=250)
        self.assertEqual(captured["text"], "Product")


class TestAWordTooLongIsCutNotDropped(unittest.TestCase):
    """A cell's last word survives in part rather than vanishing whole.

    ``QTableView`` word-wraps by default and these rows are one line tall,
    which together are not "wrap" but *a word that does not fit disappears*:
    the style lays the text out wrapped, the long word moves to a second line
    nobody has the height to draw, and what is painted is the line before it
    plus an ellipsis.  A mod called "Copy Pools AP ConstructionCategoryBuildings"
    read as "Copy Pools AP …" in a column with room for two thirds of the word
    it dropped.

    Two things had to be true at once, which is why it was hard to see.  The
    delegate's margin -- how much of the column it assumed the style would not
    give the text -- was a flat ten pixels, close to right and not right, so
    the text it elided came out a few pixels too wide and the style elided it a
    *second* time.  That second pass is the wrapping one.  Asking the style
    what width it will really use removes the second pass; turning wrapping off
    removes what the second pass would do.  Both are in: either alone fixes the
    symptom, and a one-line row has no business wrapping whatever the margin
    says.

    Measured on the real *Unread ModOps* dialog while fixing it -- the text
    reached 44% of its column before and 95% after -- rather than asserted
    here in pixels, because reproducing it needs the app's own style and
    stylesheet set globally, which this suite deliberately does not do.  What
    is checked here is the rule that keeps it fixed, and
    :class:`TestEveryOneLineTableSaysSo` checks that every table follows it.
    """

    def test_the_delegate_turns_wrapping_off_on_the_view_it_is_given(self):
        from PySide6.QtWidgets import QTableWidget

        from anno_modding_studio.ui.widgets import ElideItemDelegate

        table = QTableWidget(1, 1)
        self.assertTrue(table.wordWrap(), "Qt's default, and the thing to undo")
        ElideItemDelegate(table)
        self.assertFalse(table.wordWrap())
        table.deleteLater()

    def test_single_line_says_the_same_thing_for_a_table_with_no_delegate(self):
        from PySide6.QtWidgets import QTableWidget

        from anno_modding_studio.ui.widgets import single_line

        table = QTableWidget(1, 1)
        self.assertIs(single_line(table), table)
        self.assertFalse(table.wordWrap())
        table.deleteLater()


class TestEveryOneLineTableSaysSo(unittest.TestCase):
    """No table in the app is left on Qt's wrapping default by accident.

    Nine of the eleven were, which is how the same cell could read one way in
    search results and another in the mods folder.  The two that behaved were
    the two that had happened to say ``setWordWrap(False)``; nothing made that
    the rule, and nothing noticed the other nine.

    Read off the source rather than by opening every dialog: what is being
    checked is that somebody *said* which kind of table it is, and the ones
    that did not say it are exactly the ones nobody would open to find out.
    """

    #: The one table that really does wrap. Its rows are sized to their
    #: contents (``resizeRowsToContents``) and there are only ever one or two
    #: of them, so a long path wraps onto a second line and the row grows to
    #: show it -- which loses nothing, and is the opposite of the fault above.
    WRAPS_ON_PURPOSE = frozenset({"import_dialog.py"})

    def test_every_table_either_elides_or_says_it_wraps(self):
        import re

        ui = ROOT / "anno_modding_studio" / "ui"
        missing = []
        for path in sorted(ui.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tables = len(re.findall(r"=\s*QTableWidget\(", source))
            if not tables or path.name in self.WRAPS_ON_PURPOSE:
                continue
            says = "single_line(" in source or "ElideItemDelegate(" in source
            if not says:
                missing.append(path.name)
        self.assertEqual(
            missing, [],
            "these build a table without saying it is one line per row: "
            "give it single_line(), or an ElideItemDelegate, which says it too",
        )

    def test_the_exception_is_a_real_file_that_really_does_wrap(self):
        """So the allowance above cannot outlive the thing it allows."""
        for name in self.WRAPS_ON_PURPOSE:
            path = ROOT / "anno_modding_studio" / "ui" / name
            self.assertTrue(path.exists(), name)
            source = path.read_text(encoding="utf-8")
            self.assertIn("resizeRowsToContents", source, name)
            self.assertNotIn("single_line(", source, name)


RADIO_STATES = ("radio_off", "radio_off_hover", "radio_off_disabled", "radio_on",
                "radio_on_disabled")
CHECK_STATES = ("check_off", "check_off_hover", "check_off_disabled", "check",
                "check_disabled")


class TestRadioButtonArtwork(unittest.TestCase):
    """The radio indicator is an image, not a QSS-drawn circle.

    QSS border-radius shapes render with no antialiasing at this size, which
    is invisible on the checkbox's barely-rounded square but produced a
    visibly jagged circle for the radio button.  Every state is an SVG
    filled from the colour scheme (``theme.indicators``); this pins that the
    files are written and that the stylesheet points at them.
    """

    def test_every_state_has_an_image(self):
        """The radio's states and the checkbox's, both drawn as images."""
        from anno_modding_studio.ui import theme

        images = theme.indicators()
        for state in RADIO_STATES + CHECK_STATES:
            with self.subTest(state=state):
                self.assertTrue(Path(images[state]).is_file(), images[state])

    def test_the_stylesheet_points_at_images_not_a_drawn_circle(self):
        from anno_modding_studio.ui import theme

        images = theme.indicators()
        # No border-radius circle left to go jagged.
        self.assertNotIn(
            "QRadioButton::indicator:checked { border",
            theme.STYLESHEET,
        )
        for state in RADIO_STATES:
            self.assertIn("url(%s)" % images[state], theme.STYLESHEET, state)


class TestCheckboxArtwork(unittest.TestCase):
    """Every checkbox state is an image, unchecked included.

    Mixing a CSS `border` box (unchecked) with a border-less `image:` box
    (checked) left the two states at genuinely different total sizes -- QSS
    adds `border` on top of the declared width/height -- so toggling a
    checkbox visibly nudged the label text beside it a couple of pixels.
    Drawing *every* state as a same-sized, border-less image is what fixes
    that.
    """

    def test_the_stylesheet_points_at_images_with_no_border_anywhere(self):
        from anno_modding_studio.ui import theme

        images = theme.indicators()
        for state in CHECK_STATES:
            self.assertIn("url(%s)" % images[state], theme.STYLESHEET, state)
        # One size and no border for every state of both indicators: a border
        # on one state and not another is what made them different sizes.
        self.assertIn(
            "QCheckBox::indicator, QRadioButton::indicator {\n"
            "    width: 16px; height: 16px;\n    border: none;",
            theme.STYLESHEET,
        )

    def test_checked_and_unchecked_images_are_the_same_size(self):
        """The actual regression: unequal box sizes moved the checkbox label."""
        from PySide6.QtSvg import QSvgRenderer

        from anno_modding_studio.ui import theme

        images = theme.indicators()
        sizes = {state: QSvgRenderer(images[state]).defaultSize() for state in CHECK_STATES}
        first = next(iter(sizes.values()))
        self.assertTrue(first.isValid())
        for name, size in sizes.items():
            self.assertEqual(size, first, "%s differs in size" % name)

    def test_toggling_a_checkbox_does_not_move_its_label(self):
        """End to end, at the pixel level: ``sizeHint()`` stays identical
        between a bordered indicator and a border-less one (Qt computes it from
        a generic formula, not the style's real paint-time geometry), so only
        an actual render catches the shift the user saw. This genuinely fails
        against the old CSS -- a 1px difference, small but exactly the "tiny
        bit" reported -- and passes against the current image-only rules.
        """
        from PySide6.QtWidgets import QCheckBox

        from anno_modding_studio.ui import theme

        def first_light_pixel_x(checked: bool) -> int:
            box = QCheckBox("Header comment")
            box.setStyleSheet(theme.STYLESHEET)
            box.ensurePolished()
            box.setChecked(checked)
            box.resize(box.sizeHint())
            box.show()
            _app.processEvents()
            image = box.grab().toImage()
            for x in range(20, image.width()):
                for y in range(image.height()):
                    pixel = image.pixelColor(x, y)
                    if pixel.red() > 180 and pixel.green() > 180 and pixel.blue() > 180:
                        box.deleteLater()
                        return x
            box.deleteLater()
            raise AssertionError("label text not found in the rendered checkbox")

        self.assertEqual(first_light_pixel_x(False), first_light_pixel_x(True))


class TestFlashButtonFeedback(unittest.TestCase):
    """A Copy button used to change the clipboard silently -- no visible sign
    the click did anything. ``flash_button_feedback`` swaps the label in for a
    moment and restores it, which is the only user-visible effect a click on a
    Copy button ever has.
    """

    def test_a_click_relabels_the_button_then_restores_it(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.ui.widgets import flash_button_feedback

        button = QPushButton("Copy")
        flash_button_feedback(button, duration_ms=20)
        self.assertEqual(button.text(), "Copied!")
        self.assertTrue(button.isEnabled())

        deadline = time.monotonic() + 5
        while button.text() != "Copy" and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertEqual(button.text(), "Copy")
        self.assertTrue(button.isEnabled())
        button.deleteLater()

    def test_the_button_being_gone_when_the_timer_fires_does_not_crash(self):
        from unittest import mock

        import shiboken6
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.ui import widgets
        from anno_modding_studio.ui.widgets import flash_button_feedback

        button = QPushButton("Copy")
        # The restore itself, called here: raised in a slot Qt calls, an
        # exception is printed and the test passes whatever the slot did.
        with mock.patch.object(widgets.QTimer, "singleShot") as later:
            flash_button_feedback(button, duration_ms=20)
        (_wait, restore), _ = later.call_args
        shiboken6.delete(button)
        self.assertFalse(shiboken6.isValid(button))
        restore()  # RuntimeError, without the guard


class TestElidingLabel(unittest.TestCase):
    """A label that elides to its real width instead of forcing its layout
    wider.  Regression: the Generate mod page's "Export to:" path grew with
    every character typed into the mod name, because a plain QLabel's
    minimumSizeHint tracks its full, unwrapped text -- there was nothing
    stopping the label (and the splitter, and the window) from being dragged
    wider to keep showing all of an ever-longer path.
    """

    def test_text_beyond_the_available_width_is_elided(self):
        from anno_modding_studio.ui.widgets import ElidingLabel

        label = ElidingLabel()
        label.resize(80, 20)
        long_path = r"C:\Users\someone\Documents\AnnoModdingStudio\exports\[Gameplay] A Very Long Mod Name"
        label.setFullText(long_path)
        self.assertEqual(label.full_text(), long_path)
        self.assertLess(len(label.text()), len(long_path))
        self.assertNotEqual(label.text(), long_path)

    def test_text_that_fits_is_shown_in_full(self):
        from anno_modding_studio.ui.widgets import ElidingLabel

        label = ElidingLabel()
        label.resize(400, 20)
        label.setFullText("short.xml")
        self.assertEqual(label.text(), "short.xml")

    def test_minimum_size_hint_never_grows_with_the_text(self):
        """The actual regression: a normal QLabel's minimumSizeHint grows with
        its text, which is what dragged the layout wider. This must not.
        """
        from anno_modding_studio.ui.widgets import ElidingLabel

        label = ElidingLabel()
        label.resize(200, 20)
        label.setFullText("short")
        short_hint = label.minimumSizeHint().width()
        label.setFullText("a " * 200)  # much longer than the label is wide
        long_hint = label.minimumSizeHint().width()
        self.assertEqual(short_hint, 0)
        self.assertEqual(long_hint, 0)


class TestStatusBarBackground(unittest.TestCase):
    """The status bar's own label must not paint a darker box behind its text.

    Regression: the blanket ``QWidget { background: ... }`` rule gave the
    status label its own (darker) background instead of letting it blend into
    the status bar, rendering as a visible box around the text.
    """

    def test_the_label_blends_into_the_status_bar_not_a_darker_box(self):
        from PySide6.QtWidgets import QLabel, QMainWindow

        from anno_modding_studio.ui import theme

        window = QMainWindow()
        window.setStyleSheet(theme.STYLESHEET)
        label = QLabel("assets.xml  ·  32,911 assets indexed")
        window.statusBar().addWidget(label)
        window.resize(500, 120)
        window.show()
        _app.processEvents()

        image = window.grab().toImage()
        bar_pos = window.statusBar().mapTo(window, label.pos())
        # Just inside the label's own rectangle, near its top edge: exactly
        # where the regression painted a visibly darker background rectangle
        # instead of blending into the status bar behind it.
        probe = image.pixelColor(bar_pos.x() + 2, bar_pos.y() + 1)
        self.assertEqual(probe.name(), theme.RAISED, "status bar background")
        window.deleteLater()


class TestWrappedTextIsKeptWhole(unittest.TestCase):
    def test_a_label_gives_back_the_height_it_needed_while_narrow(self):
        """A label's height for a width is never less than its minimum, and
        the keeper set that minimum: a label measured narrow kept the lines
        it needed then once it was wide, and centred its text in them."""
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        host = QWidget()
        layout = QVBoxLayout(host)
        label = QLabel("What these mods say about each other: 1 mod will not load because "
                       "another replaces it, and 2 mods need one that will not load.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        widgets.keep_wrapped_text_whole(host)
        host.resize(160, 400)
        host.show()
        _app.processEvents()
        narrow = label.height()
        host.resize(900, 400)
        _app.processEvents()
        _app.processEvents()
        self.assertLess(label.height(), narrow)
        fresh = QLabel(label.text())
        fresh.setWordWrap(True)
        self.assertEqual(label.minimumHeight(), fresh.heightForWidth(label.width()))
        host.deleteLater()


class TestPathsWrapLikePaths(unittest.TestCase):
    """A Windows path in a wrapped label used to come apart after the drive.

    Qt offers a line break *before* a backslash and none after it, so every
    message box that named a file put "C:" on one line by itself and the whole
    of the rest on the next -- and a path too deep for two lines had nowhere
    left to break at all.
    """

    def lines(self, text, width):
        """How Qt would break ``text`` into a column ``width`` pixels wide."""
        from PySide6.QtGui import QFont, QTextLayout, QTextOption

        layout = QTextLayout(text, QFont())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)
        layout.beginLayout()
        out = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start = line.textStart()
            out.append(text[start : start + line.textLength()])
        layout.endLayout()
        return [bit.replace(widgets.PATH_BREAK, "") for bit in out]

    PATH = "C:\\Users\\somebody\\Documents\\Anno\\extracted\\data\\assets.xml"

    def test_the_drive_letter_is_not_a_line_of_its_own(self):
        for width in (40, 80, 160, 220, 320):
            with self.subTest(width=width):
                self.assertEqual(
                    self.lines(self.PATH, width)[0],
                    "C:",
                    "the bug: a lone drive letter",
                )
                self.assertNotEqual(
                    self.lines(widgets.wrappable_path(self.PATH), width)[0], "C:"
                )

    def test_and_it_breaks_after_a_folder_rather_than_before_one(self):
        broken = self.lines(widgets.wrappable_path(self.PATH), 120)
        self.assertGreater(len(broken), 1)
        for line in broken[:-1]:
            self.assertTrue(line.endswith("\\"), line)

    def test_a_path_too_deep_to_fit_used_to_have_nowhere_to_break(self):
        """Without a break after the separator there is only the one before
        the first, so everything past it overflows in a single run."""
        self.assertEqual(len(self.lines(self.PATH, 120)), 2)
        self.assertGreater(len(self.lines(widgets.wrappable_path(self.PATH), 120)), 2)

    def test_what_is_shown_is_still_the_path(self):
        shown = widgets.wrappable_path(self.PATH)
        self.assertNotEqual(shown, self.PATH)
        self.assertEqual(shown.replace(widgets.PATH_BREAK, ""), self.PATH)

    def test_and_a_posix_one_wraps_too(self):
        posix = "/home/somebody/anno/extracted/data/assets.xml"
        shown = widgets.wrappable_path(Path(posix))
        self.assertEqual(shown.replace(widgets.PATH_BREAK, ""), str(Path(posix)))
        self.assertGreater(len(self.lines(shown, 120)), 1)


class TestAButtonRowWraps(unittest.TestCase):
    """The fourth Start card offers up to five things at once, in one row.
    A box layout squeezed them below the width their labels need and Qt
    clipped the labels ("ove them into the libra"), so the row wraps instead:
    every button as wide as its text, another line when the width runs out,
    and no room kept for a hidden one."""

    def setUp(self):
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        self.host = QWidget()
        outer = QVBoxLayout(self.host)
        outer.setContentsMargins(0, 0, 0, 0)
        self.row = widgets.FlowLayout(spacing=8)
        self.buttons = [
            QPushButton(text) for text in (
                "Add to loadout", "Switch them on", "Switch them off",
                "Move them into the library", "Check again",
            )
        ]
        for button in self.buttons:
            self.row.addWidget(button)
        outer.addLayout(self.row)
        self.wanted = sum(b.sizeHint().width() for b in self.buttons) + 8 * 4

    def test_every_label_gets_its_width_and_the_row_grows_down(self):
        narrow = self.wanted // 2
        self.host.resize(narrow, 400)
        self.host.show()
        _app.processEvents()
        for button in self.buttons:
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
        tops = {button.y() for button in self.buttons}
        self.assertGreater(len(tops), 1)
        self.assertGreater(self.row.heightForWidth(narrow), self.row.heightForWidth(self.wanted))

    def test_wide_enough_is_one_line_and_a_hidden_button_takes_no_room(self):
        self.host.resize(self.wanted + 20, 400)
        self.host.show()
        _app.processEvents()
        self.assertEqual(len({button.y() for button in self.buttons}), 1)
        one_line = self.row.heightForWidth(self.wanted + 20)
        # Hide two, and the row that was just too narrow for five fits three.
        self.buttons[0].hide()
        self.buttons[3].hide()
        _app.processEvents()
        shown = [b for b in self.buttons if not b.isHidden()]
        room = sum(b.sizeHint().width() for b in shown) + 8 * (len(shown) - 1)
        self.assertEqual(self.row.heightForWidth(room), one_line)
        self.host.resize(room, 400)
        _app.processEvents()
        self.assertEqual(len({button.y() for button in shown}), 1)
        self.assertEqual(shown[0].x(), 0)


class TestBusyCursor(unittest.TestCase):
    """The wait cursor comes back off however the block ends.

    Set and restored by hand, a raise the handlers did not name skipped the
    restore and left the cursor spinning over a window waiting for nothing.
    """

    def tearDown(self):
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def test_the_cursor_waits_inside_and_not_after(self):
        with widgets.busy_cursor():
            self.assertEqual(QApplication.overrideCursor().shape(), widgets.Qt.CursorShape.WaitCursor)
        self.assertIsNone(QApplication.overrideCursor())

    def test_a_raise_puts_it_back(self):
        with self.assertRaises(KeyError), widgets.busy_cursor():
            raise KeyError("anything nobody caught")
        self.assertIsNone(QApplication.overrideCursor())

    def test_nobody_sets_it_by_hand(self):
        root = Path(widgets.__file__).resolve().parents[1]
        by_hand = [
            str(path.relative_to(root)) for path in root.rglob("*.py")
            if path.name != "widgets.py" and "setOverrideCursor" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(by_hand, [])


class TestTheWheelPassesByChoices(unittest.TestCase):
    """A wheel tick over a combo box or spin box scrolls what is behind it.

    Scrolling Settings, a tick that came under a combo box changed the
    setting, and on Currently editing each tick asked about patching the mod.
    A tick sent here is not the system's, so Qt hands it to nobody else: the
    scroll area taking it over is checked by hand, with real ticks.
    """

    def setUp(self):
        widgets.wheel_passes_by_choices(_app)

    def tearDown(self):
        _app.removeEventFilter(_app._wheel_filter)
        _app._wheel_filter = None

    def tick(self, widget):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent

        centre = QPointF(widget.rect().center())
        event = QWheelEvent(centre, QPointF(widget.mapToGlobal(centre)), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(widget, event)
        return event

    def test_a_combo_box_keeps_its_choice(self):
        from PySide6.QtWidgets import QComboBox

        combo = QComboBox()
        combo.addItems(["one", "two", "three"])
        event = self.tick(combo)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertFalse(event.isAccepted(), "left for the parent to take")

    def test_a_spin_box_keeps_its_value(self):
        from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

        for spin in (QSpinBox(), QDoubleSpinBox()):
            spin.setValue(5)
            self.tick(spin)
            self.assertEqual(spin.value(), 5)

    def test_a_list_still_scrolls(self):
        # A combo box's open list among them.
        from PySide6.QtWidgets import QListWidget

        view = QListWidget()
        view.addItems(["row %d" % n for n in range(200)])
        view.resize(200, 100)
        view.show()
        QApplication.processEvents()
        self.tick(view.viewport())
        self.assertGreater(view.verticalScrollBar().value(), 0)
        view.hide()

    def test_the_filter_is_installed_once_and_by_the_app(self):
        first = _app._wheel_filter
        widgets.wheel_passes_by_choices(_app)
        self.assertIs(_app._wheel_filter, first)
        app_py = Path(widgets.__file__).resolve().parents[1] / "app.py"
        self.assertIn("widgets.wheel_passes_by_choices(app)", app_py.read_text(encoding="utf-8"))


class TestADialogKeepsItsSize(unittest.TestCase):
    """Opened at the size it was left at, within the screen it opens on."""

    def settings(self):
        from PySide6.QtCore import QSettings

        path = Path(os.environ["ANNO_STUDIO_SETTINGS"]).with_name("keep-size.ini")
        settings = QSettings(str(path), QSettings.Format.IniFormat)
        self.addCleanup(settings.clear)
        return settings

    def dialog(self):
        from PySide6.QtWidgets import QDialog

        dialog = QDialog()
        dialog.setMinimumSize(200, 150)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_size_it_closes_at_is_the_size_it_opens_at(self):
        from PySide6.QtCore import QSize

        settings = self.settings()
        first = self.dialog()
        widgets.keep_size(first, settings, "a_dialog_size", QSize(400, 300))
        self.assertEqual(first.size(), QSize(400, 300))
        first.resize(500, 350)
        first.reject()
        again = self.dialog()
        widgets.keep_size(again, settings, "a_dialog_size", QSize(400, 300))
        self.assertEqual(again.size(), QSize(500, 350))

    def test_never_larger_than_the_screen_nor_smaller_than_its_least(self):
        from PySide6.QtCore import QSize

        settings = self.settings()
        settings.setValue("a_dialog_size", "99999,99999")
        dialog = self.dialog()
        widgets.keep_size(dialog, settings, "a_dialog_size", QSize(400, 300))
        self.assertEqual(dialog.size(), dialog.screen().availableGeometry().size())
        settings.setValue("a_dialog_size", "not a size")
        small = self.dialog()
        widgets.keep_size(small, settings, "a_dialog_size", QSize(10, 10))
        self.assertEqual(small.size(), QSize(200, 150))


class TestADialogsLeastSize(unittest.TestCase):
    """No smaller than its least size, nor than what its contents need: with
    setMinimumSize alone, a dialog opened at 200% on a small screen had its
    buttons cut at both ends."""

    def dialog(self, *texts):
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton

        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        row = QHBoxLayout(dialog)
        buttons = [QPushButton(text) for text in texts]
        for button in buttons:
            row.addWidget(button)
        return dialog, buttons

    def test_the_least_size_holds_where_the_contents_need_less(self):
        from PySide6.QtCore import QSize

        dialog, _ = self.dialog("OK")
        widgets.least_size(dialog, 400, 300)
        self.assertEqual(dialog.minimumSize(), QSize(400, 300))

    def test_the_contents_hold_where_they_need_more_and_as_they_grow(self):
        dialog, (button,) = self.dialog("A button")
        widgets.least_size(dialog, 20, 20)
        dialog.show()
        self.addCleanup(dialog.close)
        self.assertGreaterEqual(dialog.minimumWidth(), button.minimumSizeHint().width())
        button.setText("A button whose text has grown a great deal longer than it was")
        QApplication.sendPostedEvents()
        self.assertGreaterEqual(dialog.minimumWidth(), button.minimumSizeHint().width())
        self.assertGreaterEqual(dialog.width(), button.minimumSizeHint().width())

    def test_never_larger_than_the_screen(self):
        dialog, _ = self.dialog("OK")
        widgets.least_size(dialog, 99999, 99999)
        self.assertEqual(dialog.minimumSize(), dialog.screen().availableGeometry().size())


class TestTabOrder(unittest.TestCase):
    """Tab goes where the controls are laid out, whatever order they were made in."""

    @staticmethod
    def order(root) -> list:
        from PySide6.QtCore import Qt

        found, widget = [], root.nextInFocusChain()
        while widget is not root:
            if widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                found.append(widget.text())
            widget = widget.nextInFocusChain()
        return found

    def dialog(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        return dialog, QVBoxLayout(dialog)

    def test_a_button_box_in_the_order_its_buttons_are_laid_out(self):
        from PySide6.QtWidgets import QDialogButtonBox

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.addButton("Publish", QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton("Defaults", QDialogButtonBox.ButtonRole.ResetRole)
        laid = [button.text() for button in sorted(box.buttons(), key=box.layout().indexOf)]
        self.assertNotEqual(self.order(box), laid)
        was = box.focusPolicy()
        widgets.tab_as_laid_out(box)
        self.assertEqual(self.order(box), laid)
        # The box takes Tab only while the order is set.
        self.assertEqual(box.focusPolicy(), was)

    def test_a_widget_added_late_goes_where_it_is_put(self):
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

        dialog, layout = self.dialog()
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(QPushButton("Filter"))
        # The row's last item holds two controls of its own, as a scope
        # picker beside the filter box does: the new one goes after the
        # last of them, not after the row's first control or the item's.
        scope = QFrame()
        for name in ("Mine", "All"):
            QPushButton(name, scope)
        row.addWidget(scope)
        layout.addWidget(QPushButton("OK"))
        widgets.add_in_tab_order(row, QPushButton("Move"))
        self.assertEqual(self.order(dialog), ["Filter", "Mine", "All", "Move", "OK"])

        games = QFrame()
        for name in ("Anno 117", "Anno 1800"):
            QPushButton(name, games)
        layout.insertWidget(1, games)
        self.assertEqual(self.order(dialog)[-2:], ["Anno 117", "Anno 1800"])
        last = widgets.tab_after(dialog.findChildren(QPushButton)[0], games)
        self.assertEqual(last.text(), "Anno 1800")
        self.assertEqual(
            self.order(dialog), ["Filter", "Anno 117", "Anno 1800", "Mine", "All", "Move", "OK"],
        )

    @staticmethod
    def layout_items_held() -> set:
        """Every layout item Python has a wrapper of: a QWidgetItem or a
        spacer, which, not being a QObject, PySide is not told is gone."""
        import shiboken6
        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QLayoutItem

        return {
            id(each) for each in shiboken6.getAllValidWrappers()
            if isinstance(each, QLayoutItem) and not isinstance(each, QObject)
        }

    def test_the_order_is_read_without_keeping_a_layout_item(self):
        """A wrapper kept of a layout item stays registered at its address
        once Qt deletes it -- a button box makes its items again at every
        theme -- and a Qt object made there later was taken for it: the
        suite's access violations in XmlHighlighter.__init__ (18 Oct)."""
        from PySide6.QtWidgets import QDialogButtonBox, QHBoxLayout, QPushButton

        before = self.layout_items_held()
        _dialog, layout = self.dialog()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.addButton("Publish", QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(box)
        widgets.tab_as_laid_out(box)
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(QPushButton("Filter"))
        row.addStretch(1)
        widgets.add_in_tab_order(row, QPushButton("Move"))
        self.assertEqual(self.layout_items_held() - before, set())
        self.assertEqual([button.text() for button in widgets.laid_out(row)], ["Filter", "Move"])

    def test_a_row_added_with_its_widgets_keeps_no_layout_item(self):
        """PySide6 6.11's addLayout made a wrapper of every item already in
        the layout it added, kept for as long as the layout lived: 129 in
        one MainWindow. 6.12 makes none, and requirements.txt asks for it."""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        before = self.layout_items_held()
        _dialog, layout = self.dialog()
        row = QHBoxLayout()
        row.addWidget(QPushButton("Filter"))
        row.addStretch(1)
        layout.addLayout(row)
        self.assertEqual(self.layout_items_held() - before, set())

    def test_a_layout_on_no_widget_yet_has_nothing_laid_out(self):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        row = QHBoxLayout()
        row.addWidget(QPushButton("Filter"))
        self.assertEqual(widgets.laid_out(row), [])


class TestSortingByAHeading(unittest.TestCase):
    """A click on a heading sorts by it, the next turns it round, the third
    puts the rows back -- and no row is moved, so a row's number still means
    what it meant to the code that filled the table."""

    def table(self, columns=None):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        table = QTableWidget(4, 3)
        table.setHorizontalHeaderLabels(["Name", "Count", ""])
        self.addCleanup(table.deleteLater)
        for row, (name, count, ticked) in enumerate((
            ("mod 10", 1_200, False), ("Mod 9", 30, True), ("", 811, False), ("alpha", 5, True),
        )):
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, widgets.sort_as(QTableWidgetItem(f"{count:,}"), count))
            tick = QTableWidgetItem()
            tick.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
            table.setItem(row, 2, tick)
        return table, widgets.sortable(table, columns)

    def drawn(self, table) -> list:
        header = table.verticalHeader()
        return [header.logicalIndex(at) for at in range(table.rowCount())]

    def test_three_clicks_go_up_down_and_back(self):
        table, _ = self.table()
        header = table.horizontalHeader()
        header.sectionClicked.emit(1)
        self.assertEqual(self.drawn(table), [3, 1, 2, 0])
        self.assertEqual(table.horizontalHeaderItem(1).text(), "Count ▲")
        header.sectionClicked.emit(1)
        self.assertEqual(self.drawn(table), [0, 2, 1, 3])
        self.assertEqual(table.horizontalHeaderItem(1).text(), "Count ▼")
        header.sectionClicked.emit(1)
        self.assertEqual(self.drawn(table), [0, 1, 2, 3])
        self.assertEqual(table.horizontalHeaderItem(1).text(), "Count")
        # No room is made for an arrow in every heading: see _ARROWS.
        self.assertFalse(header.isSortIndicatorShown())
        # The rows themselves never moved.
        self.assertEqual(table.item(0, 0).text(), "mod 10")

    def test_text_sorts_as_a_reader_would_and_an_empty_cell_is_last_both_ways(self):
        table, sorting = self.table()
        sorting.sort(0)
        self.assertEqual(self.drawn(table), [3, 1, 0, 2])
        table.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual(self.drawn(table), [0, 1, 3, 2])

    def test_a_column_of_ticks_puts_the_ticked_first(self):
        table, sorting = self.table()
        sorting.sort(2)
        self.assertEqual(self.drawn(table), [1, 3, 0, 2])

    def test_only_the_columns_named_sort(self):
        table, sorting = self.table(columns=[1])
        table.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual((sorting.column, self.drawn(table)), (-1, [0, 1, 2, 3]))

    def test_a_table_filled_again_is_drawn_sorted_again(self):
        from PySide6.QtWidgets import QTableWidgetItem

        table, sorting = self.table()
        sorting.sort(1)
        table.setRowCount(0)
        table.setRowCount(3)
        for row, count in enumerate((7, 3, 5)):
            table.setItem(row, 1, widgets.sort_as(QTableWidgetItem(str(count)), count))
        sorting.apply()
        self.assertEqual(self.drawn(table), [1, 2, 0])

    def test_a_sorted_striped_table_with_rows_hidden_draws_every_row_shown(self):
        """Qt's striped table skipped the rows drawn above the first row
        shown in fill order: the import list, sorted and filtered, had a
        blank row that still ticked when clicked (H2-5)."""
        from PySide6.QtWidgets import (
            QStyledItemDelegate,
            QTableWidget,
            QTableWidgetItem,
        )

        painted = set()

        class Recording(QStyledItemDelegate):
            def paint(self, painter, option, index):
                painted.add(index.row())
                super().paint(painter, option, index)

        table = QTableWidget(5, 1)
        self.addCleanup(table.deleteLater)
        table.setAlternatingRowColors(True)
        table.setItemDelegate(Recording(table))
        for row, name in enumerate(("delta", "echo", "charlie", "alpha", "bravo")):
            table.setItem(row, 0, QTableWidgetItem(name))
        sorting = widgets.sortable(table)
        table.resize(300, 400)
        table.show()
        sorting.sort(0)
        # Drawn alpha, bravo, charlie, delta, echo; alpha is fill row 3.
        for row in (0, 1, 2):
            table.setRowHidden(row, True)
        QApplication.processEvents()
        painted.clear()
        table.viewport().repaint()
        self.assertEqual(painted, {3, 4})
        # Back in fill order, or with nothing hidden, it is striped again.
        sorting.sort(-1)
        self.assertTrue(table.alternatingRowColors())
        sorting.sort(0)
        self.assertFalse(table.alternatingRowColors())
        for row in (0, 1, 2):
            table.setRowHidden(row, False)
        QApplication.processEvents()
        self.assertTrue(table.alternatingRowColors())

    def test_a_flat_tree_is_put_in_order_and_back(self):
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

        tree = QTreeWidget()
        self.addCleanup(tree.deleteLater)
        tree.setColumnCount(2)
        for name, version in (("b", "1.10"), ("c", "1.9"), ("a", "")):
            tree.addTopLevelItem(QTreeWidgetItem([name, version]))
        sorting = widgets.sortable_tree(tree)

        def names():
            return [tree.topLevelItem(at).text(0) for at in range(tree.topLevelItemCount())]

        tree.header().sectionClicked.emit(1)
        self.assertEqual(names(), ["c", "b", "a"])
        tree.header().sectionClicked.emit(1)
        self.assertEqual(names(), ["b", "c", "a"])
        tree.header().sectionClicked.emit(1)
        self.assertEqual(names(), ["b", "c", "a"])
        sorting.sort(0)
        self.assertEqual(names(), ["a", "b", "c"])
        sorting.sort(-1)
        self.assertEqual(names(), ["b", "c", "a"])


class TestNothingIsSquintSized(unittest.TestCase):
    def test_no_text_in_the_stylesheet_is_under_13px(self):
        """Hints, details and counts carry a lot of this app, and people run
        it fullscreen for hours."""
        import re

        from anno_modding_studio.ui import theme

        sizes = [int(s) for s in re.findall(r"font-size:\s*(\d+)px", theme.STYLESHEET)]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 13, sizes)
        self.assertGreater(theme.FONT_PX, theme.SMALL_PX, "a hint should still read as one")


class TestTextFromOutsideStaysText(unittest.TestCase):
    """A mod's name, a modinfo option, a mod.io summary: shown as the
    characters they are, never as HTML Qt would act on."""

    HOSTILE = '<img src="x"> and <a href="file:///C:/x.exe">a link</a>'

    def setUp(self):
        widgets.external_text_stays_plain(_app)
        self.addCleanup(self._remove_filter)

    @staticmethod
    def _remove_filter():
        # Other test modules in the same process build their labels without it.
        _app.removeEventFilter(_app._plain_labels)
        _app._plain_labels = None

    def shown(self, widget):
        self.addCleanup(widget.deleteLater)
        widget.show()
        _app.processEvents()
        self.addCleanup(widget.hide)
        return widget

    def test_a_label_shows_markup_from_outside_as_its_characters(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        label = self.shown(QLabel(self.HOSTILE))
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(label.text(), self.HOSTILE)
        # Drawn as the plain text is: as wide as every character of it.
        plain = QLabel(self.HOSTILE)
        plain.setTextFormat(Qt.TextFormat.PlainText)
        self.addCleanup(plain.deleteLater)
        self.assertEqual(label.sizeHint(), plain.sizeHint())

    def test_and_so_does_a_label_whose_text_comes_after_it_is_shown(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        label = self.shown(QLabel("Waiting"))
        label.setText(self.HOSTILE)
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_a_message_box_shows_it_as_text(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QMessageBox

        box = QMessageBox(QMessageBox.Icon.Warning, "Title", self.HOSTILE)
        box.setInformativeText(self.HOSTILE)
        self.shown(box)
        labels = [label for label in box.findChildren(QLabel) if label.text() == self.HOSTILE]
        self.assertEqual(len(labels), 2)
        for label in labels:
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_a_tooltip_shows_it_as_text(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtWidgets import QToolTip

        owner = self.shown(widgets.QWidget())
        QToolTip.showText(QPoint(20, 20), self.HOSTILE, owner)
        self.addCleanup(QToolTip.hideText)
        _app.processEvents()
        tips = [widget for widget in _app.topLevelWidgets()
                if widget.objectName() == "qtooltip_label" and widget.isVisible()]
        self.assertEqual(len(tips), 1)
        self.assertEqual(tips[0].textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(tips[0].text(), self.HOSTILE)

    def test_the_studios_own_markup_still_reads_as_markup(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        own = self.shown(widgets.rich_label("<b>%s</b>" % "Anno 117"))
        self.assertEqual(own.textFormat(), Qt.TextFormat.RichText)
        with widgets.studio_rich_text():
            qt_built = self.shown(QLabel("<p>About</p>"))
        self.assertEqual(qt_built.textFormat(), Qt.TextFormat.AutoText)
        # And a value from outside in it is escaped where it is made.
        label = QLabel()
        self.addCleanup(label.deleteLater)
        widgets.show_rich(label, "<b>%s</b>" % html.escape(self.HOSTILE))
        self.assertNotIn("<img", label.text())


class TestTheCaretFollowsTheFocus(unittest.TestCase):
    """Win+V opens by the system caret, which Qt makes only while the input
    panel is asked for: a field taking text asks for it, and leaving lets go."""

    def test_a_text_field_asks_for_it_and_a_button_lets_it_go(self):
        from unittest import mock

        from PySide6.QtGui import QInputMethod
        from PySide6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget

        with mock.patch.object(widgets.sys, "platform", "win32"):
            widgets.caret_follows_focus(_app)
        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QVBoxLayout(host)
        button, field = QPushButton("b"), QLineEdit("text")
        layout.addWidget(button)
        layout.addWidget(field)
        host.show()
        host.activateWindow()
        button.setFocus()
        _app.processEvents()
        with (mock.patch.object(QInputMethod, "show") as shown,
              mock.patch.object(QInputMethod, "hide") as hidden):
            field.setFocus()
            _app.processEvents()
            self.assertTrue(shown.called)
            button.setFocus()
            _app.processEvents()
            self.assertTrue(hidden.called)


class TestColumnsKeepToTheirView(unittest.TestCase):
    """A column dragged wider takes its room from the columns after it,
    rather than pushing them past the edge for the view to scroll to."""

    def table(self, modes=None, shares=None, keep=(), floors=None):
        from PySide6.QtWidgets import QHeaderView, QTableWidget

        table = QTableWidget(3, 4)
        self.addCleanup(table.deleteLater)
        header = table.horizontalHeader()
        for column, mode in (modes or {}).items():
            header.setSectionResizeMode(column, mode)
        for column in range(4):
            if header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive:
                table.setColumnWidth(column, 150)
        fit = widgets.fit_columns(table, shares, keep, floors)
        table.resize(640, 300)
        table.show()
        _app.processEvents()
        return table, fit

    def widths(self, table) -> list:
        return [table.columnWidth(column) for column in range(table.columnCount())]

    def assertFits(self, table) -> None:
        self.assertEqual(sum(self.widths(table)), table.viewport().width())
        self.assertEqual(table.horizontalScrollBar().maximum(), 0)

    def test_the_columns_fill_the_view_as_they_were_set_out(self):
        table, _ = self.table()
        self.assertFits(table)
        widths = self.widths(table)
        self.assertLessEqual(max(widths) - min(widths), 1)

    def test_a_column_dragged_wider_takes_from_the_next(self):
        table, _ = self.table()
        before = self.widths(table)
        table.horizontalHeader().resizeSection(1, before[1] + 60)
        after = self.widths(table)
        self.assertEqual(after[1], before[1] + 60)
        self.assertEqual(after[2], before[2] - 60)
        self.assertEqual((after[0], after[3]), (before[0], before[3]))
        self.assertFits(table)

    def test_and_from_the_ones_after_that_when_the_next_is_narrowest(self):
        table, _ = self.table()
        floor = table.horizontalHeader().minimumSectionSize()
        before = self.widths(table)
        table.horizontalHeader().resizeSection(0, 10_000)
        after = self.widths(table)
        self.assertEqual(after[1:], [floor] * 3)
        self.assertEqual(after[0], sum(before) - 3 * floor)
        self.assertFits(table)

    def test_one_dragged_narrower_gives_its_room_to_the_next(self):
        table, _ = self.table()
        before = self.widths(table)
        table.horizontalHeader().resizeSection(2, before[2] - 40)
        after = self.widths(table)
        self.assertEqual(after[3], before[3] + 40)
        self.assertFits(table)

    def test_the_last_ones_right_edge_is_the_views(self):
        table, _ = self.table()
        before = self.widths(table)
        table.horizontalHeader().resizeSection(3, before[3] + 80)
        self.assertEqual(self.widths(table), before)

    def test_a_wider_view_keeps_the_proportions_dragged(self):
        table, _ = self.table()
        before = self.widths(table)
        table.horizontalHeader().resizeSection(0, before[0] + 100)
        dragged = self.widths(table)
        table.resize(1280, 300)
        _app.processEvents()
        wide = self.widths(table)
        self.assertFits(table)
        self.assertAlmostEqual(wide[0] / sum(wide), dragged[0] / sum(dragged), places=2)

    def test_a_kept_column_holds_its_pixels(self):
        table, _ = self.table(keep=(0,))
        before = self.widths(table)
        table.resize(1280, 300)
        _app.processEvents()
        self.assertEqual(table.columnWidth(0), before[0])
        self.assertFits(table)

    def test_a_stretched_column_shares_and_can_be_dragged(self):
        from PySide6.QtWidgets import QHeaderView

        Mode = QHeaderView.ResizeMode
        table, _ = self.table(modes={3: Mode.Stretch})
        header = table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(3), Mode.Interactive)
        self.assertFits(table)
        # The stretched one had what the others left.
        self.assertEqual(table.columnWidth(3), table.viewport().width() - 3 * 150)

    def test_a_column_sized_to_its_contents_is_left_its_width(self):
        from PySide6.QtWidgets import QHeaderView, QTableWidgetItem

        Mode = QHeaderView.ResizeMode
        table, _ = self.table(modes={2: Mode.ResizeToContents})
        table.setItem(0, 2, QTableWidgetItem("a longer cell"))
        # The header sizes the column when it next lays out, and the others
        # are fitted round it after that.
        _app.processEvents()
        _app.processEvents()
        header = table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(2), Mode.ResizeToContents)
        self.assertGreater(table.columnWidth(2), 150)
        self.assertFits(table)

    def test_a_narrow_view_fits_columns_of_unequal_shares(self):
        """A column raised to its narrowest takes those pixels from the wider
        ones: raised without, the columns ran past the edge of a view they
        had room in, and it scrolled sideways."""
        table, _ = self.table(shares={0: 0.1, 1: 0.6, 2: 0.15, 3: 0.15})
        table.resize(300, 300)
        _app.processEvents()
        floor = table.horizontalHeader().minimumSectionSize()
        self.assertEqual(self.widths(table)[0], floor)
        self.assertFits(table)

    def test_and_after_one_is_dragged_to_its_narrowest(self):
        table, _ = self.table()
        table.horizontalHeader().resizeSection(0, 1)
        table.resize(300, 300)
        _app.processEvents()
        self.assertFits(table)

    def test_kept_columns_of_unequal_widths_give_way_within_the_view(self):
        table, _ = self.table(keep=(0, 1))
        header = table.horizontalHeader()
        header.resizeSection(0, table.columnWidth(0) + 100)
        table.resize(300, 300)
        _app.processEvents()
        self.assertEqual(table.columnWidth(1), header.minimumSectionSize())
        self.assertFits(table)

    def test_a_column_given_a_floor_of_its_own_is_held_at_it(self):
        """The changes tree's names need more room than its changes do,
        and neither may go under the least it was given."""
        table, _ = self.table(floors={0: 140, 3: 90})
        header = table.horizontalHeader()
        header.resizeSection(0, 1)
        self.assertEqual(table.columnWidth(0), 140)
        self.assertFits(table)
        header.resizeSection(2, 10_000)
        self.assertEqual(table.columnWidth(3), 90)
        self.assertFits(table)
        table.resize(300, 300)
        _app.processEvents()
        self.assertGreaterEqual(table.columnWidth(0), 140)
        self.assertGreaterEqual(table.columnWidth(3), 90)

    def test_the_shares_are_read_and_set_for_remembering(self):
        table, fit = self.table(shares={0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4})
        self.assertAlmostEqual(fit.shares()[3], 0.4)
        table.horizontalHeader().resizeSection(2, table.columnWidth(2) - 40)
        dragged = fit.shares()
        self.assertAlmostEqual(dragged[3], table.columnWidth(3) / table.viewport().width(), places=3)
        fit.set_shares({0: 0.25, 1: 0.25, 2: 0.25, 3: 0.25})
        widths = self.widths(table)
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertFits(table)

    def test_shares_set_before_the_view_is_shown_are_where_it_starts(self):
        from PySide6.QtWidgets import QTableWidget

        table = QTableWidget(3, 2)
        self.addCleanup(table.deleteLater)
        fit = widgets.fit_columns(table, {0: 0.5, 1: 0.5})
        fit.set_shares({0: 0.75, 1: 0.25})
        self.assertAlmostEqual(fit.shares()[1], 0.25)
        table.resize(640, 300)
        table.show()
        _app.processEvents()
        self.assertAlmostEqual(table.columnWidth(1) / table.viewport().width(), 0.25, places=2)

    def test_a_view_with_nothing_to_drag_is_left_to_qt(self):
        from PySide6.QtWidgets import QHeaderView

        Mode = QHeaderView.ResizeMode
        table, _ = self.table(modes={0: Mode.Stretch, 1: Mode.ResizeToContents,
                                     2: Mode.ResizeToContents, 3: Mode.ResizeToContents})
        self.assertEqual(table.horizontalHeader().sectionResizeMode(0), Mode.Stretch)


class TestByteCountsPastTwoGigabytes(unittest.TestCase):
    """A count of bytes reaches the bar whole through its worker's signal.

    Declared ``int``, the signal carried 32 bits: 3 GiB of 5 GiB came out the
    other side as -1 GiB of 1 GiB, and ``show_progress`` never saw a total
    it had to scale. Its own test called it directly, past the signal."""

    def bar(self, worker):
        from PySide6.QtWidgets import QProgressBar

        bar = QProgressBar()
        self.addCleanup(bar.deleteLater)
        worker.progress.connect(lambda done, total, _text: widgets.show_progress(bar, done, total))
        return bar

    def assertScaled(self, bar) -> None:
        self.assertEqual((bar.maximum(), bar.value()), (5 * 1024 ** 2, 3 * 1024 ** 2))

    def test_a_download(self):
        from anno_modding_studio.ui import workers

        def job(_client, say, _stop):
            say(3 * 1024 ** 3, 5 * 1024 ** 3, "")

        worker = workers.ModioTaskWorker("key", job, fetch=lambda *_a, **_k: None)
        self.addCleanup(worker.deleteLater)
        bar = self.bar(worker)
        worker.run()
        self.assertScaled(bar)

    def test_a_library_backup(self):
        from unittest import mock

        from anno_modding_studio.core import library
        from anno_modding_studio.ui import workers

        def back_up(_folder, _name, stop, progress):
            progress(3 * 1024 ** 3, 5 * 1024 ** 3)

        worker = workers.LibraryBackupWorker([(Path("m"), "m", 5 * 1024 ** 3)])
        self.addCleanup(worker.deleteLater)
        bar = self.bar(worker)
        with mock.patch.object(library, "back_up", back_up):
            worker.run()
        self.assertScaled(bar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
