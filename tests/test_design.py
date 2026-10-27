"""The design standards that can only be checked on built widgets and schemes.

Three parts, as docs/standards/design-standards.md lists them:

- **Colour schemes.** Every scheme in the registry, and a scheme built from
  a sample Windows contrast palette, is checked against the contrast floors.
  A new scheme is checked without a test being written for it.
- **Drawing styles.** A style block names no colour, so any style works with
  any scheme.
- **The window walk.** The main window and every dialog in
  ``design_dialogs.DIALOGS`` are built, and every widget is looked at. Each
  is then shown and gone through with Tab, and each dialog closed with Esc.

Every walk rule but one holds at zero. Buttons without a mnemonic are
counted and held where they are, under ``"walk"`` in
``tools/check_baseline.json``. A count that grows fails. When a change brings
it down, record it:

    python tests/test_design.py --record
"""

from __future__ import annotations

import ast
import colorsys
import itertools
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path
from typing import ClassVar
from unittest import mock

import _isolation
import design_dialogs
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QColor, QHelpEvent, QKeyEvent, QMouseEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTextEdit,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anno_modding_studio.core.i18n import tr_kw
from anno_modding_studio.ui import appearance, help_tip, theme, widgets
from anno_modding_studio.ui.help_tip import HelpTip
from anno_modding_studio.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "check_baseline.json"
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

# Two of Windows 11's own contrast themes, Night sky and Desert.
WINDOWS_SAMPLES = {
    "night sky": theme.system_scheme(
        window="#000000", text="#ffffff", gray="#a6a6a6", highlight="#d6b4fd",
        highlight_text="#2b2b2b", button="#000000", hot="#8080ff"),
    "desert": theme.system_scheme(
        window="#fffaef", text="#3d3d3d", gray="#676767", highlight="#903909",
        highlight_text="#fff5e3", button="#fffaef", hot="#1c5e75"),
}

SURFACES = ("bg", "surface", "raised")
# Each text colour, on each background it is drawn on.
TEXT_PAIRS = (
    [("text", s) for s in SURFACES]
    + [("text-dim", s) for s in SURFACES]
    + [(token, "surface") for token in (
        "accent-text", "ok", "changed", "added", "warn", "error", "removed", "rule",
        "syn-comment", "syn-tag", "syn-attr", "syn-value", "syn-option")]
    + [(token, "bg") for token in (
        "accent-text", "ok", "changed", "added", "warn", "error", "removed", "rule")]
    + [("text-on-accent", "accent")]
)
# What marks where a control starts, or what has the focus. Flat draws its
# dividing lines in field-border, so they are covered here too.
BOUNDARY_PAIRS = (
    [("field-border", s) for s in SURFACES]
    + [("focus", s) for s in SURFACES]
    + [("scrollbar", "bg")]
)
# Colours that have to be told apart by hue, and how far apart in degrees.
# Set from the approved schemes: the closest pair in any of them is warn and
# accent in Marble, 12 degrees apart.
HUE_TOKENS = ("warn", "error", "removed", "changed", "accent")
SAME_MEANING = ({"error", "removed"},)
HUE_FLOOR = 10
# A surface this unsaturated has no hue to be confused with.
TINTED = 0.2


def hue_and_saturation(colour: str):
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, _lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, saturation


def hue_distance(a: str, b: str) -> float:
    apart = abs(hue_and_saturation(a)[0] - hue_and_saturation(b)[0]) % 360
    return min(apart, 360 - apart)


def all_schemes():
    yield from theme.SCHEMES.items()
    yield from WINDOWS_SAMPLES.items()


class TestColourSchemes(unittest.TestCase):
    def test_every_scheme_defines_every_token(self):
        for name, scheme in all_schemes():
            with self.subTest(scheme=name):
                self.assertEqual(set(scheme.tokens), set(theme.TOKENS))
                for token, value in scheme.tokens.items():
                    self.assertRegex(value, r"^#[0-9a-f]{6}$", token)

    def test_the_six_approved_schemes_are_registered_with_granite_the_default(self):
        self.assertEqual(
            list(theme.SCHEMES),
            ["granite", "brick", "marble", "timber", "obsidian", "quartz"],
        )
        self.assertEqual(theme.DEFAULT_SCHEME, "granite")
        for key in ("obsidian", "quartz"):
            self.assertTrue(theme.SCHEMES[key].contrast)
            self.assertIn("(high contrast)", theme.SCHEMES[key].label)

    def test_text_meets_its_contrast_floor_on_every_background_it_is_drawn_on(self):
        for name, scheme in all_schemes():
            # A high-contrast scheme of the Studio's aims for 7:1. A Windows
            # contrast theme's colours are the user's own, so it is held to
            # the ordinary floor: its links are 6.5:1 as Windows ships them.
            floor = 7 if name in theme.SCHEMES and scheme.contrast else 4.5
            pairs = list(TEXT_PAIRS)
            # Selected text: in a high-contrast scheme it is drawn in
            # text-on-accent, on the accent.
            pairs.append(("text-on-accent" if scheme.contrast else "text", "selection"))
            for text, background in pairs:
                with self.subTest(scheme=name, text=text, on=background):
                    ratio = theme.contrast_ratio(scheme[text], scheme[background])
                    self.assertGreaterEqual(round(ratio, 2), floor)

    def test_a_row_washed_in_the_accent_keeps_its_text_readable(self):
        """The changes list's row for the mod changes go to: its name is in
        accent-text, or text-dim when the mod is off, and its count in
        text-dim.  A Windows contrast theme gets no wash at all."""
        for name, scheme in all_schemes():
            band = theme.wash(of=scheme)
            if name not in theme.SCHEMES:
                with self.subTest(scheme=name):
                    self.assertIsNone(band)
                continue
            floor = 7 if scheme.contrast else 4.5
            for text in ("accent-text", "text-dim", "text"):
                with self.subTest(scheme=name, text=text):
                    ratio = theme.contrast_ratio(scheme[text], band)
                    self.assertGreaterEqual(round(ratio, 2), floor)
            with self.subTest(scheme=name, against="surface"):
                self.assertNotEqual(band, scheme["surface"])

    def test_boundaries_and_focus_meet_three_to_one(self):
        for name, scheme in all_schemes():
            for line, background in BOUNDARY_PAIRS:
                with self.subTest(scheme=name, line=line, on=background):
                    ratio = theme.contrast_ratio(scheme[line], scheme[background])
                    self.assertGreaterEqual(round(ratio, 2), 3)

    def test_status_colours_differ_by_hue_and_not_only_by_lightness(self):
        # Not asked of a Windows contrast theme, which has no status colours:
        # there a glyph or a word carries the meaning alone.
        for name, scheme in theme.SCHEMES.items():
            for index, a in enumerate(HUE_TOKENS):
                for b in HUE_TOKENS[index + 1:]:
                    if {a, b} in SAME_MEANING:
                        continue
                    with self.subTest(scheme=name, a=a, b=b):
                        self.assertGreaterEqual(hue_distance(scheme[a], scheme[b]), HUE_FLOOR)
                for surface in SURFACES:
                    if hue_and_saturation(scheme[surface])[1] < TINTED:
                        continue
                    with self.subTest(scheme=name, a=a, surface=surface):
                        self.assertGreaterEqual(
                            hue_distance(scheme[a], scheme[surface]), HUE_FLOOR)

    def test_every_image_renders_and_is_filled_from_tokens_only(self):
        QApplication.instance() or QApplication([])
        for name, scheme in all_schemes():
            values = set(scheme.tokens.values())
            for image, svg in theme.indicator_svgs(scheme).items():
                with self.subTest(scheme=name, image=image):
                    self.assertTrue(QSvgRenderer(svg.encode("utf-8")).isValid())
                    self.assertLessEqual(set(HEX.findall(svg)), values)
                    named = set(re.findall(r'(?:fill|stroke)="([a-z]+)"', svg))
                    self.assertLessEqual(named, {"none"})

    def test_an_items_checkbox_is_the_schemes_image_in_every_state(self):
        # Fusion's own has no edge on a light surface: unticked, it could not
        # be seen in Marble, Timber or Quartz.
        for name, scheme in all_schemes():
            images = theme.indicators(scheme)
            sheet = theme.stylesheet(scheme, "boxed", "comfortable")
            for state, image in (("unchecked", "check_off"), ("checked", "check"),
                                 ("indeterminate", "check_partial")):
                with self.subTest(scheme=name, state=state):
                    self.assertIn(
                        "QAbstractItemView::indicator:%s { image: url(%s); }"
                        % (state, images[image]), sheet)

    def test_a_ticked_and_an_unticked_item_are_drawn_with_an_edge(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(theme.apply, app, theme.SCHEMES[theme.DEFAULT_SCHEME],
                        theme.DEFAULT_OUTLINES, theme.DEFAULT_DENSITY)
        for name in ("marble", "quartz", "granite"):
            scheme = theme.SCHEMES[name]
            theme.apply(app, scheme)
            listing = QListWidget()
            listing.addItem("Stray")
            item = listing.item(0)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            listing.resize(200, 60)
            for state in (Qt.CheckState.Unchecked, Qt.CheckState.Checked):
                item.setCheckState(state)
                image = listing.grab().toImage()
                row = listing.visualItemRect(item)
                seen = {
                    image.pixelColor(x, y).name()
                    for x in range(row.left(), row.left() + 24)
                    for y in range(row.top(), row.bottom())
                }
                edge = scheme["field-border"] if state == Qt.CheckState.Unchecked else scheme["focus"]
                with self.subTest(scheme=name, state=state):
                    self.assertIn(edge, seen)

    def test_the_images_are_written_to_the_cache_folder_as_svg(self):
        from anno_modding_studio.core import places

        for path in theme.indicators(theme.SCHEMES["marble"]).values():
            self.assertTrue(Path(path).is_file(), path)
            self.assertEqual(Path(path).suffix, ".svg")
            self.assertIn(places.cache_dir().as_posix(), path)

    def test_every_colour_in_the_style_sheet_is_one_of_the_schemes_tokens(self):
        for name, scheme in all_schemes():
            values = set(scheme.tokens.values())
            for outline in theme.OUTLINES:
                with self.subTest(scheme=name, outlines=outline):
                    sheet = theme.stylesheet(scheme, outline, "compact")
                    self.assertLessEqual(set(HEX.findall(sheet)), values)

    def test_no_rule_is_for_a_control_the_studio_no_longer_builds(self):
        """A link-styled button, a flat one, a dock's separator and a rich
        text box each had rules, and the package builds none of them."""
        sheet = theme.stylesheet(theme.SCHEMES["marble"], "boxed", "comfortable")
        for gone in ("QPushButton#Link", "QPushButton:flat", "QMainWindow::separator",
                     "QTextEdit"):
            with self.subTest(selector=gone):
                self.assertFalse(gone in sheet)

    def test_a_button_that_is_off_is_drawn_in_the_disabled_colour(self):
        """It was drawn in the colour of a hint, which is made to be read:
        6:1 and more on its background, and taken for a button that was on."""
        for name, scheme in all_schemes():
            with self.subTest(scheme=name):
                sheet = theme.stylesheet(scheme, "boxed", "comfortable")
                for rule in ("QPushButton:disabled", "QPushButton#Primary:disabled"):
                    body = sheet.split(rule + " {", 1)[1].split("}", 1)[0]
                    self.assertIn(" color: %s;" % scheme["text-disabled"], " " + body.strip())

    def test_a_checkbox_or_radio_button_that_is_off_is_drawn_in_the_disabled_colour(self):
        """A checkbox that was off had no rule and kept the palette's text
        colour, so it looked on; a radio button's was text-dim, the colour of
        a hint. A field, a list to pick from, a number box, a label and a
        group's title kept the text colour too (A10-39)."""
        kinds = ("QCheckBox", "QRadioButton", "QLineEdit", "QPlainTextEdit", "QComboBox",
                 "QAbstractSpinBox", "QLabel", r"QGroupBox::title")
        for name, scheme in all_schemes():
            for outline in ("boxed", "flat"):
                with self.subTest(scheme=name, outline=outline):
                    sheet = theme.stylesheet(scheme, outline, "comfortable")
                    rules = re.findall(r"([^{}]*)\{([^}]*)\}", sheet)
                    for kind in kinds:
                        said = [body for selectors, body in rules
                                if re.search(r"(^|[\s,])%s:disabled\b" % kind, selectors)]
                        self.assertTrue(said, kind)
                        self.assertIn(" color: %s;" % scheme["text-disabled"], " " + said[-1].strip())

    def test_a_menu_entry_that_is_off_is_drawn_in_the_disabled_colour(self):
        """A menu bar's item and a menu's entry that were off were drawn in
        text-dim, the colour of a hint, after the controls had left it for
        that reason (A11-96)."""
        for name, scheme in all_schemes():
            with self.subTest(scheme=name):
                sheet = theme.stylesheet(scheme, "boxed", "comfortable")
                for rule in ("QMenuBar::item:disabled", "QMenu::item:disabled",
                             "QMenu::item:disabled:selected"):
                    body = sheet.split("\n" + rule + " {", 1)[1].split("}", 1)[0]
                    self.assertIn(" color: %s;" % scheme["text-disabled"], " " + body.strip())

    def test_each_scheme_names_a_logo_that_is_there(self):
        for name, scheme in all_schemes():
            with self.subTest(scheme=name):
                self.assertTrue(theme.logo_path(scheme).is_file())
                self.assertEqual("White" in scheme.logo, scheme.dark)

    def test_a_windows_contrast_theme_keeps_its_own_colours(self):
        scheme = WINDOWS_SAMPLES["desert"]
        self.assertFalse(scheme.dark)
        self.assertTrue(scheme.contrast)
        self.assertEqual(
            (scheme["bg"], scheme["text"], scheme["accent"], scheme["text-on-accent"]),
            ("#fffaef", "#3d3d3d", "#903909", "#fff5e3"),
        )
        self.assertTrue(WINDOWS_SAMPLES["night sky"].dark)


class TestDrawingStyles(unittest.TestCase):
    def test_a_style_block_sets_no_colour(self):
        named = re.compile(
            r":\s*(white|black|red|green|blue|gray|grey|yellow|orange|rgb|rgba|hsl)\b")
        for key, block in theme.OUTLINES.items():
            with self.subTest(outlines=key):
                self.assertIsNone(HEX.search(block))
                self.assertIsNone(named.search(block))
                for token in re.findall(r"(?<!\{)\{([a-z-]+)\}(?!\})", block):
                    self.assertIn(token, theme.TOKENS)
        for key, metrics in theme.DENSITIES.items():
            with self.subTest(density=key):
                self.assertIsNone(HEX.search(repr(metrics)))

    def test_both_densities_define_the_same_metrics(self):
        names = {key: set(metrics) for key, metrics in theme.DENSITIES.items()}
        self.assertEqual(names["comfortable"], names["compact"])
        self.assertLessEqual({"radius", "pad-v", "pad-h", "row-pad", "gap"}, names["compact"])

    def test_flat_draws_its_lines_at_the_boundary_floor(self):
        self.assertIn("{field-border}", theme.OUTLINES["flat"])
        self.assertNotIn("{border}", theme.OUTLINES["flat"])

    def test_a_high_contrast_scheme_has_square_corners_in_either_density(self):
        for dense in theme.DENSITIES:
            sheet = theme.stylesheet(theme.SCHEMES["quartz"], "boxed", dense)
            field = sheet[sheet.index("QLineEdit, QPlainTextEdit"):]
            self.assertIn("border-radius: 0px;", field[: field.index("}")])


class _ThemeCase(unittest.TestCase):
    """Puts the default theme back after a test that changes it."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self._restore)

    def _restore(self):
        theme.apply(self.app, theme.SCHEMES[theme.DEFAULT_SCHEME],
                    theme.DEFAULT_OUTLINES, theme.DEFAULT_DENSITY)
        self.app.setStyleSheet("")


class TestApplyingATheme(_ThemeCase):
    def test_a_box_of_decimals_is_drawn_as_a_box_of_whole_numbers_is(self):
        """The field rules named QSpinBox, so the one QDoubleSpinBox the
        Studio builds, in a mod's options, was drawn by none of them."""
        for name, scheme in theme.SCHEMES.items():
            theme.apply(self.app, scheme)
            host = QWidget()
            layout = QVBoxLayout(host)
            # The window opens with the focus on its first field.
            layout.addWidget(QLineEdit())
            whole, decimal = QSpinBox(), QDoubleSpinBox()
            decimal.setDecimals(0)
            for box in (whole, decimal):
                box.setFixedWidth(120)
                layout.addWidget(box)
            host.show()
            self.app.processEvents()
            with self.subTest(scheme=name):
                self.assertEqual(decimal.grab().toImage(), whole.grab().toImage())
            host.deleteLater()

    def test_a_token_read_from_the_module_follows_the_scheme(self):
        theme.apply(self.app, theme.SCHEMES["granite"])
        self.assertEqual(theme.TEXT_DIM, "#9ba4b2")
        self.assertEqual(theme.colour("field-border"), theme.FIELD_BORDER)
        theme.apply(self.app, theme.SCHEMES["timber"])
        self.assertEqual(theme.TEXT_DIM, "#5c4c3a")
        self.assertEqual(theme.scheme().key, "timber")
        with self.assertRaises(AttributeError):
            theme.NO_SUCH_TOKEN  # noqa: B018

    def test_the_application_palette_is_set_from_the_tokens(self):
        from PySide6.QtGui import QPalette

        theme.apply(self.app, theme.SCHEMES["marble"])
        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), theme.BG)
        self.assertEqual(palette.color(QPalette.ColorRole.Base).name(), theme.SURFACE)
        self.assertEqual(palette.color(QPalette.ColorRole.Link).name(), theme.ACCENT_TEXT)

    def test_items_already_in_a_list_move_to_the_new_schemes_colours(self):
        theme.apply(self.app, theme.SCHEMES["granite"])
        tree = QTreeWidget()
        tree.setColumnCount(2)
        item = QTreeWidgetItem(tree, ["Residents", "20 → 25"])
        item.setForeground(0, QColor(theme.TEXT_DIM))
        item.setForeground(1, QColor(theme.CHANGED))
        listing = QListWidget()
        listing.addItem("older")
        listing.item(0).setForeground(QColor(theme.WARN))
        changed = []
        tree.itemChanged.connect(lambda *_: changed.append(1))

        theme.apply(self.app, theme.SCHEMES["quartz"])
        quartz = theme.SCHEMES["quartz"]
        self.assertEqual(item.foreground(0).color().name(), quartz["text-dim"])
        self.assertEqual(item.foreground(1).color().name(), quartz["changed"])
        self.assertEqual(listing.item(0).foreground().color().name(), quartz["warn"])
        self.assertEqual(changed, [], "recolouring is not an edit")

    def test_whatever_listens_is_told_after_the_change(self):
        seen = []
        theme.notifier().changed.connect(lambda: seen.append(theme.scheme().key))
        theme.apply(self.app, theme.SCHEMES["brick"])
        self.assertEqual(seen[-1], "brick")

    def test_mod_io_s_mark_stops_listening_once_it_is_gone(self):
        """A lambda kept setting the logo on its label after the label was
        deleted, and each change of scheme raised in it."""
        from PySide6.QtCore import QCoreApplication, QEvent

        from anno_modding_studio.ui import modio_brand

        row = modio_brand.branding()
        mark = row.findChildren(QLabel)[-1]
        self.assertFalse(mark.pixmap().isNull(), "the logo is drawn, not the name")
        row.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        raised = []
        with mock.patch.object(sys, "excepthook", lambda *error: raised.append(error)), \
                mock.patch.object(sys, "unraisablehook", raised.append):
            theme.apply(self.app, theme.SCHEMES["granite"])
        self.assertEqual(raised, [])

    def test_a_style_changes_without_changing_the_scheme(self):
        theme.apply(self.app, theme.SCHEMES["brick"])
        theme.apply(self.app, outline="flat", dense="compact")
        self.assertEqual((theme.scheme().key, theme.outlines(), theme.density()),
                         ("brick", "flat", "compact"))
        self.assertEqual(theme.metric("radius"), 2)
        self.assertIn("QGroupBox { border: none;", self.app.styleSheet())

    def test_a_group_box_is_as_tall_flat_as_it_is_boxed(self):
        # Flat's were 4 px taller each, and the Export tab's five took the
        # window past the 950 px it fits in when Boxed.
        heights = {}
        for outline in theme.OUTLINES:
            theme.apply(self.app, theme.SCHEMES["granite"], outline, "comfortable")
            box = QGroupBox("Output options")
            QVBoxLayout(box).addWidget(QLabel("Header comment"))
            box.ensurePolished()
            heights[outline] = box.sizeHint().height()
        self.assertEqual(heights["flat"], heights["boxed"])

    def test_a_tint_is_a_property_the_style_sheet_reads_and_not_a_style_sheet(self):
        theme.apply(self.app, theme.SCHEMES["granite"])
        label = QLabel("3 mods changed")
        label.setObjectName("StepHint")
        theme.tint(label, "warn", strong=True)
        self.assertEqual(label.styleSheet(), "")
        self.assertEqual(label.property("tone"), "warn")
        label.ensurePolished()
        self.assertEqual(label.palette().color(label.foregroundRole()).name(), theme.WARN)
        theme.tint(label)
        self.assertFalse(label.property("tone"))
        label.ensurePolished()
        self.assertEqual(label.palette().color(label.foregroundRole()).name(), theme.TEXT_DIM)
        with self.assertRaises(ValueError):
            theme.tint(label, "#ff0000")


class FakeSettings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


class TestAppearanceSettings(_ThemeCase):
    def test_the_defaults_are_granite_boxed_comfortable_at_100(self):
        settings = FakeSettings()
        self.assertEqual(appearance.saved_scheme(settings), "granite")
        self.assertEqual(appearance.saved_outlines(settings), "boxed")
        self.assertEqual(appearance.saved_density(settings), "comfortable")
        self.assertEqual(appearance.saved_size(settings), 100)

    def test_a_value_that_is_not_offered_reads_as_the_default(self):
        settings = FakeSettings({
            appearance.SCHEME_KEY: "slate", appearance.OUTLINES_KEY: "round",
            appearance.DENSITY_KEY: "", appearance.SIZE_KEY: "130",
            # A saved Text size is not migrated.
            "text_size": 18,
        })
        self.assertEqual(appearance.saved_scheme(settings), "granite")
        self.assertEqual(appearance.saved_outlines(settings), "boxed")
        self.assertEqual(appearance.saved_density(settings), "comfortable")
        self.assertEqual(appearance.saved_size(settings), 100)
        self.assertEqual(appearance.SIZES, (90, 100, 110, 125, 150, 175, 200))

    def test_match_windows_is_marble_in_light_mode_and_granite_in_dark(self):
        with mock.patch.object(appearance, "windows_contrast_scheme", return_value=None):
            with mock.patch.object(appearance, "windows_is_dark", return_value=False):
                self.assertEqual(appearance.resolve(appearance.MATCH_WINDOWS).key, "marble")
            with mock.patch.object(appearance, "windows_is_dark", return_value=True):
                self.assertEqual(appearance.resolve(appearance.MATCH_WINDOWS).key, "granite")
                self.assertEqual(appearance.resolve("timber").key, "timber")

    def test_a_windows_contrast_theme_wins_over_the_scheme_picked(self):
        desert = WINDOWS_SAMPLES["desert"]
        with mock.patch.object(appearance, "windows_contrast_scheme", return_value=desert):
            for choice in ("granite", "obsidian", appearance.MATCH_WINDOWS):
                self.assertIs(appearance.resolve(choice), desert)

    def test_the_saved_appearance_is_applied_and_applying_it_again_does_nothing(self):
        settings = FakeSettings({appearance.SCHEME_KEY: "timber",
                                 appearance.OUTLINES_KEY: "flat"})
        with mock.patch.object(appearance, "windows_contrast_scheme", return_value=None):
            appearance.apply_saved(self.app, settings)
            self.assertEqual((theme.scheme().key, theme.outlines()), ("timber", "flat"))
            with mock.patch.object(theme, "apply") as again:
                appearance.apply_saved(self.app, settings)
            again.assert_not_called()

    def test_interface_size_is_qts_scale_factor_and_is_taken_back_out(self):
        with mock.patch.dict("os.environ", clear=False) as environ:
            environ.pop(appearance.SCALE_VARIABLE, None)
            self.assertIsNone(appearance.set_scale(100))
            self.assertNotIn(appearance.SCALE_VARIABLE, environ)
            token = appearance.set_scale(125)
            self.assertEqual(environ[appearance.SCALE_VARIABLE], "1.25")
            appearance.clear_scale(token)
            # Gone again, so the game does not inherit it from Play.
            self.assertNotIn(appearance.SCALE_VARIABLE, environ)
            # One set from outside is put back as it was.
            environ[appearance.SCALE_VARIABLE] = "3"
            appearance.clear_scale(appearance.set_scale(200))
            self.assertEqual(environ[appearance.SCALE_VARIABLE], "3")

    def test_settings_offers_every_scheme_style_and_size_and_applies_at_once(self):
        window = MainWindow()
        dialog = design_dialogs.DIALOGS["settings"](window)
        schemes = [dialog.scheme_combo.itemData(i) for i in range(dialog.scheme_combo.count())]
        self.assertEqual(schemes, [appearance.MATCH_WINDOWS, *theme.SCHEMES])
        self.assertEqual(dialog.scheme_combo.currentData(), "granite")
        sizes = [dialog.size_combo.itemData(i) for i in range(dialog.size_combo.count())]
        self.assertEqual(sizes, list(appearance.SIZES))
        self.assertEqual(dialog.size_combo.currentText(), "100%")

        with mock.patch.object(appearance, "windows_contrast_scheme", return_value=None):
            dialog.scheme_combo.setCurrentIndex(dialog.scheme_combo.findData("marble"))
            dialog.density_combo.setCurrentIndex(dialog.density_combo.findData("compact"))
        self.assertEqual((theme.scheme().key, theme.density()), ("marble", "compact"))
        self.assertEqual(window.settings.value(appearance.SCHEME_KEY), "marble")
        self.assertFalse(dialog.contrast_note.isVisibleTo(dialog))

        dialog.size_combo.setCurrentIndex(dialog.size_combo.findData(150))
        self.assertEqual(appearance.saved_size(window.settings), 150)
        self.assertIn("next time", dialog.size_note.text())

    def test_settings_says_when_a_windows_contrast_theme_is_in_charge(self):
        theme.apply(self.app, WINDOWS_SAMPLES["night sky"])
        window = MainWindow()
        dialog = design_dialogs.DIALOGS["settings"](window)
        self.assertTrue(dialog.contrast_note.isVisibleTo(dialog))


class TestHelpTip(_ThemeCase):
    def make(self, **more):
        tip = HelpTip("Keeps its name, ModID and version.", "&This is my mod", **more)
        self.addCleanup(tip.close_panel)
        tip.show()
        return tip

    def test_a_screen_reader_reads_it_without_opening_it(self):
        tip = self.make()
        self.assertEqual(tip.accessibleName(), "Help: This is my mod")
        self.assertEqual(tip.accessibleDescription(), "Keeps its name, ModID and version.")

    def test_the_keyboard_reaches_it_opens_it_and_closes_it(self):
        tip = self.make()
        self.assertTrue(tip.focusPolicy() & Qt.FocusPolicy.TabFocus)
        for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_F1):
            QTest.keyClick(tip, key)
            self.assertTrue(tip.is_open(), key)
            QTest.keyClick(tip, Qt.Key.Key_Escape)
            self.assertFalse(tip.is_open(), key)

    def test_its_text_can_be_selected_and_it_does_not_time_out(self):
        tip = self.make()
        tip.open()
        body = tip._panel.body
        self.assertTrue(body.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertTrue(body.wordWrap())
        QTest.qWait(400)
        self.assertTrue(tip.is_open())

    def test_one_a_hover_opened_closes_when_the_mouse_has_left_both(self):
        tip = self.make()
        tip.open(pinned=False)
        far = tip._panel.geometry().bottomRight() + QPoint(200, 200)
        with mock.patch("anno_modding_studio.ui.help_tip.QCursor.pos", return_value=far):
            tip._close_if_left()
        self.assertFalse(tip.is_open())
        # Pinned by a click or a key, it stays wherever the mouse goes.
        tip.open(pinned=True)
        with mock.patch("anno_modding_studio.ui.help_tip.QCursor.pos", return_value=far):
            tip._close_if_left()
        self.assertTrue(tip.is_open())

    def column(self):
        """Two ? one above the other, as down a list of rows."""
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        holder = QWidget()
        self.addCleanup(holder.close)
        layout = QVBoxLayout(holder)
        layout.setSpacing(8)
        tips = [HelpTip("About the first row.", "First"), HelpTip("About the second.", "Second")]
        for tip in tips:
            self.addCleanup(tip.close_panel)
            layout.addWidget(tip)
        holder.show()
        self.app.processEvents()
        return tips

    def test_the_mouse_on_the_next_rows_icon_has_left_the_first(self):
        """The rectangle round icon and panel took in everything below the
        icon, and down a column of ? every panel stayed open."""
        first, second = self.column()
        first.open(pinned=False)
        at = second.mapToGlobal(second.rect().center())
        with mock.patch("anno_modding_studio.ui.help_tip.QCursor.pos", return_value=at):
            first._close_if_left()
        self.assertFalse(first.is_open())

    def test_crossing_from_the_icon_to_its_panel_keeps_it_open(self):
        first, _second = self.column()
        first.open(pinned=False)
        icon = first.mapToGlobal(first.rect().bottomRight())
        corner = first._panel.geometry().topLeft()
        for at in (icon, (icon + corner) / 2, corner + QPoint(20, 20)):
            with mock.patch("anno_modding_studio.ui.help_tip.QCursor.pos", return_value=at):
                first._close_if_left()
            self.assertTrue(first.is_open(), at)

    def test_one_panel_at_a_time(self):
        first, second = self.column()
        first.open(pinned=False)
        second.open(pinned=False)
        self.assertFalse(first.is_open())
        self.assertTrue(second.is_open())
        # A pinned one gives way too: a click on another is a new question.
        second.open(pinned=True)
        first.open(pinned=True)
        self.assertFalse(second.is_open())
        self.assertTrue(first.is_open())

    def test_it_can_end_with_a_link_to_the_guide(self):
        tip = self.make(guide=True)
        tip.open()
        links = [label for label in tip._panel.findChildren(QLabel) if "<a " in label.text()]
        self.assertEqual(len(links), 1)
        with mock.patch.object(help_tip, "open_guide") as opened:
            links[0].linkActivated.emit("guide")
        opened.assert_called_once()

    def test_it_is_one_line_of_text_tall(self):
        tip = self.make()
        self.assertEqual(tip.sizeHint().height(), tip.fontMetrics().height())
        self.assertFalse(tip.grab().toImage().isNull())


class TestTooltipsStayOpen(_ThemeCase):
    def setUp(self):
        super().setUp()
        self.filter = help_tip._TooltipFilter()

    def shown(self, widget, at=None):
        at = at or QPoint(5, 5)
        event = QHelpEvent(QEvent.Type.ToolTip, at, widget.mapToGlobal(at))
        with mock.patch.object(QToolTip, "showText") as show:
            handled = self.filter.eventFilter(widget, event)
        return handled, show.call_args

    def test_a_plain_widgets_tooltip_is_shown_with_no_expiry(self):
        button = QPushButton("Refresh")
        button.setToolTip("Reads the mods folder again.")
        handled, call = self.shown(button)
        self.assertTrue(handled)
        self.assertEqual(call.args[1], "Reads the mods folder again.")
        self.assertEqual(call.args[4], help_tip.NEVER)

    def test_a_widget_without_one_is_left_to_qt(self):
        handled, call = self.shown(QPushButton("Refresh"))
        self.assertFalse(handled)
        self.assertIsNone(call)

    def test_an_item_views_tooltips_come_from_its_items(self):
        listing = QListWidget()
        listing.addItem("[Gameplay] Mine")
        listing.item(0).setToolTip("Loaded from your mods folder.")
        listing.resize(200, 100)
        listing.show()
        at = listing.visualItemRect(listing.item(0)).center()
        handled, call = self.shown(listing.viewport(), at)
        self.assertTrue(handled)
        self.assertEqual(call.args[1], "Loaded from your mods folder.")

    def test_a_long_line_is_wrapped_and_keeps_its_indent(self):
        """Qt shows a plain tooltip's line as one line, however wide."""
        long = "  " + " ".join(["word"] * 40)
        button = QPushButton("Refresh")
        button.setToolTip("Short.\n\n" + long)
        _handled, call = self.shown(button)
        lines = call.args[1].split("\n")
        self.assertEqual(lines[:2], ["Short.", ""])
        self.assertGreater(len(lines), 3)
        for line in lines[2:]:
            self.assertLessEqual(len(line), help_tip.TIP_WRAP_CHARACTERS)
            self.assertTrue(line.startswith("  word"), line)
        self.assertEqual(" ".join(call.args[1].split()), " ".join(long.split()).join(["Short. ", ""]))

    def test_a_word_longer_than_a_line_is_left_whole(self):
        path = "/Values/" + "/".join(["Step"] * 40)
        self.assertEqual(help_tip.wrapped("At " + path).split("\n"), ["At", path])

    def rows(self):
        listing = QListWidget()
        for name in ("[Gameplay] Mine", "[Gameplay] Theirs"):
            listing.addItem(name)
            listing.item(listing.count() - 1).setToolTip("About %s." % name)
        listing.resize(200, 100)
        listing.show()
        self.addCleanup(listing.deleteLater)
        return listing, [
            listing.visualItemRect(listing.item(row)).center() for row in range(2)
        ]

    def ask(self, listing, at, visible):
        event = QHelpEvent(QEvent.Type.ToolTip, at, listing.viewport().mapToGlobal(at))
        with mock.patch.object(QToolTip, "showText") as show, \
                mock.patch.object(QToolTip, "hideText") as hide, \
                mock.patch.object(QToolTip, "isVisible", return_value=visible):
            handled = self.filter.eventFilter(listing.viewport(), event)
        return handled, show, hide

    def test_the_next_row_waits_its_turn_where_qt_would_show_it_at_once(self):
        """Down a list that was a tooltip for every row the mouse crossed."""
        listing, (first, second) = self.rows()
        _handled, show, _hide = self.ask(listing, first, visible=False)
        show.assert_called_once()
        handled, show, hide = self.ask(listing, second, visible=True)
        self.assertTrue(handled)
        show.assert_not_called()
        hide.assert_called_once()
        self.assertTrue(self.filter._wait.isActive())
        self.assertGreaterEqual(self.filter._wait.interval(), 300)
        # The wait over and the mouse still there, it is shown.
        at = listing.viewport().mapToGlobal(second)
        with mock.patch.object(QToolTip, "showText") as show, \
                mock.patch.object(help_tip.QCursor, "pos", return_value=at):
            self.filter._show_pending()
        self.assertEqual(show.call_args.args[1], "About [Gameplay] Theirs.")

    def test_and_not_at_all_if_the_mouse_has_gone_on(self):
        listing, (first, second) = self.rows()
        self.ask(listing, first, visible=False)
        self.ask(listing, second, visible=True)
        at = listing.viewport().mapToGlobal(first)
        with mock.patch.object(QToolTip, "showText") as show, \
                mock.patch.object(help_tip.QCursor, "pos", return_value=at):
            self.filter._show_pending()
        show.assert_not_called()

    def test_a_click_ends_the_waiting_since_qt_waits_again_after_one(self):
        """Qt asks for nothing after a click until the mouse has moved and
        rested the whole time: a second wait here would be twice that."""
        listing, (first, second) = self.rows()
        self.ask(listing, first, visible=False)
        self.ask(listing, second, visible=True)
        self.assertTrue(self.filter._wait.isActive())
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, second, listing.viewport().mapToGlobal(second),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self.filter.eventFilter(listing.viewport(), press))
        self.assertFalse(self.filter._wait.isActive())
        _handled, show, _hide = self.ask(listing, second, visible=False)
        show.assert_called_once()

    def test_the_row_whose_tooltip_is_up_keeps_it(self):
        listing, (first, _second) = self.rows()
        self.ask(listing, first, visible=False)
        _handled, show, hide = self.ask(listing, first, visible=True)
        show.assert_called_once()
        hide.assert_not_called()

    def test_after_a_rest_a_row_is_shown_as_soon_as_qt_asks(self):
        listing, (first, second) = self.rows()
        self.ask(listing, first, visible=False)
        self.filter._last -= 10
        _handled, show, _hide = self.ask(listing, second, visible=False)
        show.assert_called_once()

    def test_a_button_is_still_shown_straight_after_another(self):
        one, other = QPushButton("One"), QPushButton("Other")
        one.setToolTip("The first.")
        other.setToolTip("The second.")
        self.shown(one)
        handled, call = self.shown(other)
        self.assertTrue(handled)
        self.assertEqual(call.args[1], "The second.")

    def test_the_filter_is_installed_once(self):
        help_tip.keep_tooltips_open(self.app)
        first = self.app._tooltip_filter
        help_tip.keep_tooltips_open(self.app)
        self.assertIs(self.app._tooltip_filter, first)
        self.app.removeEventFilter(first)
        self.app._tooltip_filter = None


# -- the window walk ----------------------------------------------------------
WORD = re.compile(r"\w")
MNEMONIC = re.compile(r"&([^&\s])")


def words(text: str) -> int:
    plain = re.sub(r"<[^>]+>", " ", text or "")
    return sum(1 for word in plain.split() if WORD.search(word))


def has_name(widget, buddies) -> bool:
    if widget.accessibleName().strip():
        return True
    if isinstance(widget, QAbstractButton):
        # A button that is hidden until it has something to say is given
        # its text as it is shown: the changes panel's "3 conflicts".
        return bool(widget.text().strip()) or widget.isHidden()
    return widget in buddies


def named_by_its_parent(widget) -> bool:
    """A field inside a combo box or spin box, or the list a combo box drops
    down, which sits in a frame of its own."""
    parent = widget.parent()
    if isinstance(parent, (QComboBox, QAbstractSpinBox, QAbstractItemView)):
        return True
    frame = parent.parent() if parent is not None else None
    return isinstance(widget, QAbstractItemView) and isinstance(frame, QComboBox)


# The buttons Qt names itself. Esc presses Cancel or Close and Enter presses
# OK, so the keyboard reaches them without a letter of their own.
OWN_KEY = (QDialogButtonBox.StandardButton.Ok, QDialogButtonBox.StandardButton.Cancel,
           QDialogButtonBox.StandardButton.Close)


def has_own_key(widget) -> bool:
    box = widget.parent()
    return isinstance(box, QDialogButtonBox) and box.standardButton(widget) in OWN_KEY


# What widgets.named and i18n.tr_kw put round a name or a keyword, which
# only a box or a help panel turns into weight or a link. Anywhere else the
# characters themselves would show.
MARKS = re.compile("[\ue000-\ue004]")


def shown_texts(widget) -> list:
    """Every text a widget shows or says of itself."""
    texts = [widget.toolTip(), widget.windowTitle(), widget.accessibleName(),
             widget.accessibleDescription(), widget.statusTip(), widget.whatsThis()]
    text = getattr(widget, "text", None)
    if callable(text):
        try:
            said = text()
        except TypeError:
            said = ""
        if isinstance(said, str):
            texts.append(said)
    if isinstance(widget, QComboBox):
        texts += [widget.itemText(at) for at in range(widget.count())]
    return texts


def walk(root) -> Counter:
    """What the walk finds under one window or dialog, by rule."""
    found: Counter = Counter()
    widgets = [root, *root.findChildren(QWidget)]
    buddies = {label.buddy() for label in root.findChildren(QLabel) if label.buddy()}
    named_kinds = (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox,
                   QPlainTextEdit, QTextEdit, QAbstractItemView)
    for widget in widgets:
        if widget.styleSheet():
            found["own-stylesheet"] += 1
        if any(MARKS.search(text) for text in shown_texts(widget)):
            found["marks-shown"] += 1
        if isinstance(widget, HelpTip):
            if not (widget.focusPolicy() & Qt.FocusPolicy.TabFocus
                    and widget.accessibleDescription().strip()):
                found["helptip-unreachable"] += 1
        elif words(widget.toolTip()) > 15:
            found["long-tooltip"] += 1
        if (isinstance(widget, named_kinds) and not named_by_its_parent(widget)
                and widget.focusPolicy() != Qt.FocusPolicy.NoFocus
                and not has_name(widget, buddies)):
            found["no-accessible-name"] += 1
    if isinstance(root, QDialog):
        taken: Counter = Counter()
        for widget in widgets:
            labelled = isinstance(widget, QLabel) and widget.buddy() is not None
            if not (labelled or isinstance(widget, (QPushButton, QCheckBox, QRadioButton))):
                continue
            text = widget.text()
            if not text.strip() or has_own_key(widget):
                continue
            match = MNEMONIC.search(text)
            if match is None:
                found["no-mnemonic"] += 1
            else:
                taken[match.group(1).lower()] += 1
        found["repeated-mnemonic"] += sum(count - 1 for count in taken.values())
    return found


def tab_stops(root, *, switched_off=False) -> list:
    """The controls of ``root`` that Tab stops on, in the order it does.

    With ``switched_off``, the ones switched off as well: a button switched
    off until a row is picked still has its place in the order.
    """
    stops, widget = [], root.nextInFocusChain()
    while widget is not root:
        if ((widget.focusPolicy() & Qt.FocusPolicy.TabFocus) == Qt.FocusPolicy.TabFocus
                and widget.focusProxy() is None and widget.window() is root
                and widget.isVisibleTo(root) and (switched_off or widget.isEnabled())):
            stops.append(widget)
        widget = widget.nextInFocusChain()
    return stops


def reads_before(first, then) -> bool:
    """Whether ``then`` comes before ``first`` in reading order: above it, or
    to its left in the same row.

    Compared where the two part, as the two widgets there that hold them, so
    that a control scrolled out of sight is where it is on its page, and a
    panel beside a page is a column of its own.
    """
    if first.isAncestorOf(then) or then.isAncestorOf(first):
        return False
    holders = []
    widget = first
    while widget is not None:
        holders.append(widget)
        widget = widget.parentWidget()
    holds_then = then
    while holds_then.parentWidget() not in holders:
        holds_then = holds_then.parentWidget()
    holds_first = holders[holders.index(holds_then.parentWidget()) - 1]
    a, b = holds_first.geometry(), holds_then.geometry()
    if b.bottom() < a.top():
        return True
    same_row = not (b.top() > a.bottom() or b.bottom() < a.top())
    return same_row and b.right() < a.left()


def links(widget) -> int:
    """Tab moves through a label's links before it leaves the label."""
    return widget.text().count("<a ") if isinstance(widget, QLabel) else 0


def _press(widget, key) -> None:
    app = QApplication.instance()
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        app.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
    app.processEvents()


def keyboard(root) -> Counter:
    """What the keyboard finds in ``root``, shown: Tab from its first control
    round to it again, and for a dialog, Esc.
    """
    found: Counter = Counter()
    app = QApplication.instance()
    root.show()
    app.processEvents()
    try:
        everything = tab_stops(root, switched_off=True)
        found["tab-out-of-order"] += sum(
            reads_before(first, then) for first, then in itertools.pairwise(everything)
        )
        # A table moves through its cells on Tab, round and round.
        found["tab-held"] += sum(
            view.tabKeyNavigation() for view in root.findChildren(QAbstractItemView)
            if view.focusPolicy() & Qt.FocusPolicy.TabFocus
        )
        stops = tab_stops(root)
        if stops:
            stops[0].setFocus(Qt.FocusReason.TabFocusReason)
            app.processEvents()
            reached = [root.focusWidget()]
            for _turn in range(2 * len(stops)):
                here = root.focusWidget()
                for _press_again in range(links(here) + 1):
                    _press(here, Qt.Key.Key_Tab)
                    if root.focusWidget() is not here:
                        break
                there = root.focusWidget()
                if there is here:
                    found["tab-held"] += 1
                    at = stops.index(here) if here in stops else -1
                    there = stops[(at + 1) % len(stops)]
                    there.setFocus(Qt.FocusReason.TabFocusReason)
                    app.processEvents()
                if there is reached[0]:
                    break
                reached.append(there)
            found["tab-unreached"] += len(set(stops) - set(reached))
            found["tab-repeated"] += len(reached) - len(set(reached))
        if isinstance(root, QDialog):
            # A question on the way out is answered No, and not waited for.
            with (mock.patch.object(QDialog, "exec", return_value=0),
                  mock.patch.object(QMessageBox, "exec", return_value=0)):
                _press(root.focusWidget() or root, Qt.Key.Key_Escape)
            found["esc-stays-open"] += root.isVisible()
    finally:
        root.hide()
    return found


# Rules that hold at zero. The rest are held at their recorded count.
HARD = ("own-stylesheet", "marks-shown", "helptip-unreachable", "long-tooltip", "no-accessible-name",
        "repeated-mnemonic", "window-while-built", "tab-unreached", "tab-repeated",
        "tab-held", "tab-out-of-order", "esc-stays-open")
RATCHETED = ("no-mnemonic",)


class _ShownWindows(QObject):
    """Counts the windows shown while it is installed on the application.

    A widget made visible before it has a parent is a window of its own
    for a moment: it flashes on screen and takes the focus.
    """

    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.Show and isinstance(watched, QWidget)
                and watched.isWindow()):
            self.count += 1
        return False


def walk_everything() -> Counter:
    app = QApplication.instance() or QApplication([])
    found: Counter = Counter()
    window = MainWindow()
    found.update(walk(window))
    found.update(keyboard(window))
    for factory in design_dialogs.DIALOGS.values():
        shown = _ShownWindows()
        app.installEventFilter(shown)
        dialog = factory(window)
        app.removeEventFilter(shown)
        found["window-while-built"] += shown.count
        found.update(walk(dialog))
        found.update(keyboard(dialog))
        dialog.deleteLater()
    window.deleteLater()
    return found


def enter_presses(dialog) -> list:
    """The buttons Enter would press from a text field of ``dialog`` that are
    not what the dialog is for: ``(field, button)`` by their words.

    A dialog gives Enter to its default button, and with none named that is
    the first button that will take it, whatever it does. Whether the button
    is switched on today is not asked: it is on another day. What Enter may
    press is the dialog's primary button, or the one that accepts it.

    A dialog can keep the key from its button while a field has the focus, in
    a ``keyPressEvent`` of its own, so the button is not only looked at: the
    key is given to the dialog, and the button is counted if that presses it.
    """
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QPushButton

    app = QApplication.instance() or QApplication([])
    accepting = (
        QDialogButtonBox.ButtonRole.AcceptRole, QDialogButtonBox.ButtonRole.YesRole,
        QDialogButtonBox.ButtonRole.ApplyRole,
    )

    def meant(button) -> bool:
        if button.objectName() == "Primary":
            return True
        box = button.parent()
        while box is not None and not isinstance(box, QDialogButtonBox):
            box = box.parent()
        return box is not None and box.buttonRole(button) in accepting

    def pressed(button) -> bool:
        """Whether Enter, given to the dialog as it stands, presses ``button``.

        The key goes to the dialog and not to the field, so nothing the field
        does with it happens; and the button says nothing while it is pressed,
        so nothing it does happens either. A button that can be ticked shows
        afterwards that it was pressed, and one switched off is switched on to
        be asked.
        """
        was = (button.isEnabled(), button.isCheckable(), button.isChecked())
        silent = button.blockSignals(True)
        try:
            button.setEnabled(True)
            button.setCheckable(True)
            button.setChecked(False)
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                modifiers = (
                    Qt.KeyboardModifier.KeypadModifier if key == Qt.Key.Key_Enter
                    else Qt.KeyboardModifier.NoModifier
                )
                app.sendEvent(dialog, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))
                if button.isChecked():
                    return True
            return False
        finally:
            button.setChecked(was[2])
            button.setCheckable(was[1])
            button.setEnabled(was[0])
            button.blockSignals(silent)

    found = []
    dialog.show()
    app.processEvents()
    try:
        for field in dialog.findChildren(QLineEdit):
            if not field.isVisibleTo(dialog) or field.isReadOnly():
                continue
            field.setFocus()
            app.processEvents()
            for button in dialog.findChildren(QPushButton):
                if (
                    button.isDefault() and button.isVisibleTo(dialog) and not meant(button)
                    and pressed(button)
                ):
                    found.append((
                        field.accessibleName() or field.placeholderText() or field.objectName(),
                        button.text(),
                    ))
    finally:
        dialog.hide()
    return found


class TestEnterInATextField(unittest.TestCase):
    """Enter in a text field presses the dialog's own button or none.

    The mod.io account dialog gave it to *Forget key*, the first button it
    had: the emailed code typed, Enter pressed, and the saved key was gone
    (audit finding A8-30). Asked of every dialog there is a factory for, so
    that the next one is caught where it is built.
    """

    # Dialogs where Enter reaches a button it is not for, and why that stands.
    # None today. The mods folder dialog was here, for *Read mods*, its default
    # until the folder is read: it now keeps Enter in its two boxes from the
    # button (audit finding A9-8), which the test below it holds it to.
    KNOWN: ClassVar[dict] = {}

    def window(self):
        # A window made with no application there ends the process.
        QApplication.instance() or QApplication([])
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(_isolation.forget_settings)
        return window

    def test_it_presses_only_what_the_dialog_is_for(self):
        window = self.window()
        for name, factory in design_dialogs.DIALOGS.items():
            with self.subTest(dialog=name):
                dialog = factory(window)
                try:
                    found = enter_presses(dialog)
                finally:
                    dialog.deleteLater()
                self.assertEqual(found, self.KNOWN.get(name, []))

    def test_it_would_have_caught_the_account_dialog(self):
        """With the buttons as they were: the first of them takes Enter.
        The account is Settings' mod.io tab now."""
        window = self.window()
        from anno_modding_studio.ui.settings_dialog import TAB_MODIO

        dialog = design_dialogs.DIALOGS["settings"](window)
        self.addCleanup(dialog.deleteLater)
        # Enter is pressed from the fields in view: the tab they are on.
        dialog.pages.setCurrentIndex(TAB_MODIO)
        dialog.account.forget_button.setAutoDefault(True)
        self.assertIn("Forget &key", [button for _field, button in enter_presses(dialog)])

    def test_the_mods_folder_dialog_keeps_it_from_read_mods(self):
        """*Read mods* is the default until the folder is read, and is not the
        dialog's primary button: only the dialog's own handling of the key
        keeps Enter in the folder box or the filter box from pressing it."""
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        window = self.window()
        dialog = design_dialogs.DIALOGS["mods_folder"](window)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.read_button.isDefault())
        self.assertEqual(enter_presses(dialog), [])
        # And nothing was read or imported by the asking.
        self.assertIsNone(dialog._worker)
        self.assertIsNone(dialog.session)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(
            (dialog.read_button.isEnabled(), dialog.read_button.isCheckable()), (True, False)
        )

        # With the key handled as any dialog handles it, both boxes reach it.
        with mock.patch.object(ModsFolderDialog, "keyPressEvent", QDialog.keyPressEvent):
            plain = design_dialogs.DIALOGS["mods_folder"](window)
            self.addCleanup(plain.deleteLater)
            self.assertEqual(
                enter_presses(plain),
                [("Mods folder", "&Read mods"), ("Filter mods", "&Read mods")],
            )
            self.assertIsNone(plain._worker)


class TestTheWindowWalk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = walk_everything()
        _isolation.forget_settings()

    def test_no_widget_carries_a_style_sheet_of_its_own(self):
        self.assertEqual(self.found["own-stylesheet"], 0)

    def test_no_widget_shows_the_marks_of_a_name_or_a_keyword(self):
        self.assertEqual(self.found["marks-shown"], 0)

    def test_every_help_tip_takes_focus_and_has_a_description(self):
        self.assertEqual(self.found["helptip-unreachable"], 0)

    def test_no_plain_tooltip_is_over_fifteen_words(self):
        self.assertEqual(self.found["long-tooltip"], 0)

    def test_every_control_that_takes_focus_has_an_accessible_name(self):
        self.assertEqual(self.found["no-accessible-name"], 0)

    def test_no_mnemonic_repeats_within_a_dialog(self):
        self.assertEqual(self.found["repeated-mnemonic"], 0)

    def test_building_a_dialog_shows_no_window(self):
        self.assertEqual(self.found["window-while-built"], 0)

    def test_the_walk_finds_what_each_rule_is_for(self):
        dialog = QDialog()
        QLineEdit(dialog)
        QComboBox(dialog)
        shown, hidden = QPushButton("", dialog), QPushButton("", dialog)
        hidden.setVisible(False)
        wordy = QPushButton("&Refresh", dialog)
        wordy.setToolTip(" ".join(["word"] * 16))
        QPushButton("&Rename", dialog)
        QPushButton("Export", dialog)
        QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        QLabel(tr_kw("Make a [[patch]]"), dialog)
        QLabel(widgets.named("Mine"), dialog).setToolTip("plain")
        found = walk(dialog)
        self.assertEqual(found["marks-shown"], 2)
        # The field, the combo box and the button that is shown with no text.
        self.assertFalse(shown.isHidden())
        self.assertEqual(found["no-accessible-name"], 3)
        self.assertEqual(found["long-tooltip"], 1)
        self.assertEqual(found["repeated-mnemonic"], 1)
        # Export. Close has Esc.
        self.assertEqual(found["no-mnemonic"], 1)

    def test_tab_reaches_every_control_once(self):
        self.assertEqual(self.found["tab-unreached"], 0)
        self.assertEqual(self.found["tab-repeated"], 0)

    def test_no_control_keeps_tab(self):
        self.assertEqual(self.found["tab-held"], 0)

    def test_tab_goes_in_reading_order(self):
        self.assertEqual(self.found["tab-out-of-order"], 0)

    def test_esc_closes_every_dialog(self):
        self.assertEqual(self.found["esc-stays-open"], 0)

    def test_the_keyboard_walk_finds_what_each_rule_is_for(self):
        class Skips(QPushButton):
            """Tab from here goes to ``to``."""

            def event(self, event):
                if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
                    self.to.setFocus(Qt.FocusReason.TabFocusReason)
                    return True
                return super().event(event)

        class StaysOpen(QDialog):
            def reject(self):
                pass

        dialog = StaysOpen()
        self.addCleanup(dialog.deleteLater)
        layout = QVBoxLayout(dialog)
        first, skipped, last = Skips("&First"), QPushButton("&Skipped"), QPushButton("&Last")
        first.to = last
        for widget in (first, skipped, last, QTableWidget(0, 2)):
            layout.addWidget(widget)
        # Laid out at the top, and made last: Tab reaches it last.
        layout.insertWidget(0, QPushButton("&Top"))
        found = keyboard(dialog)
        self.assertEqual(found["tab-unreached"], 1)
        # The table, which moves through its cells on Tab.
        self.assertEqual(found["tab-held"], 1)
        self.assertEqual(found["tab-out-of-order"], 1)
        self.assertEqual(found["esc-stays-open"], 1)

        # Round and round two buttons, and never back to the first.
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        layout = QVBoxLayout(dialog)
        one, two, back = QPushButton("&One"), QPushButton("&Two"), Skips("&Back")
        back.to = two
        for widget in (one, two, back):
            layout.addWidget(widget)
        found = keyboard(dialog)
        self.assertGreater(found["tab-repeated"], 0)
        self.assertEqual(found["tab-unreached"], 0)
        self.assertEqual(found["esc-stays-open"], 0)

    def test_no_counted_rule_has_grown(self):
        recorded = json.loads(BASELINE.read_text(encoding="utf-8")).get("walk")
        self.assertIsNotNone(recorded, "record the walk: python tests/test_design.py --record")
        for rule in RATCHETED:
            with self.subTest(rule=rule):
                self.assertLessEqual(self.found[rule], recorded.get(rule, 0))

    def test_every_dialog_has_a_factory_or_is_waiting_for_one(self):
        classes = set()
        for path in (ROOT / "anno_modding_studio" / "ui").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    "Dialog" in ast.unparse(base) for base in node.bases
                ):
                    classes.add(node.name)
        listed = set(design_dialogs.BUILT) | set(design_dialogs.WAITING)
        self.assertEqual(classes - listed, set(), "no factory, and not listed as waiting")
        self.assertEqual(listed - classes, set(), "listed, and no longer a dialog")
        self.assertEqual(set(design_dialogs.BUILT) & set(design_dialogs.WAITING), set())
        self.assertEqual(len(design_dialogs.BUILT), len(design_dialogs.DIALOGS))


def record() -> None:
    found = walk_everything()
    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    recorded["walk"] = {rule: found[rule] for rule in RATCHETED if found[rule]}
    BASELINE.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("walk counts recorded: %s" % recorded["walk"])


if __name__ == "__main__":
    if "--record" in sys.argv:
        record()
    else:
        unittest.main()
