"""The accessibility mechanisms of docs/standards/design-standards.md, section 8.

What Stage 1 of the design review built, each held by a test:

- Enter does what double-click does in a list, and stops at the list
- Ctrl+1 to Ctrl+5 switch the main tabs
- a Copy button keeps the keyboard focus through its feedback
- every kind of control draws a focus indicator, in every colour scheme
- a status is never shown by colour alone: labels and list items carry a glyph
- the reference graph can be walked with the keyboard and read by a screen
  reader

Whether each widget has a name and a mnemonic is the window walk's job, in
``test_design.py``.
"""

from __future__ import annotations

import unittest
from unittest import mock

import _isolation
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anno_modding_studio.ui import theme
from anno_modding_studio.ui.help_tip import HelpTip
from anno_modding_studio.ui.main_window import MainWindow
from anno_modding_studio.ui.reference_panel import (
    COLUMN_GAP,
    NODE_HEIGHT,
    NODE_WIDTH,
    ROW_GAP,
    GraphNode,
    ListedAsset,
    ReferenceListDialog,
    ReferencePanel,
    ReferenceView,
)
from anno_modding_studio.ui.widgets import enter_activates, flash_button_feedback

_app = QApplication.instance() or QApplication([])


class _ThemeCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self._restore)

    def _restore(self):
        theme.apply(_app, theme.SCHEMES[theme.DEFAULT_SCHEME],
                    theme.DEFAULT_OUTLINES, theme.DEFAULT_DENSITY)
        _app.setStyleSheet("")


class TestEnterDoesWhatDoubleClickDoes(unittest.TestCase):
    def setUp(self):
        self.dialog = QDialog()
        layout = QVBoxLayout(self.dialog)
        self.table = QTableWidget(2, 1)
        for row, name in enumerate(("first", "second")):
            self.table.setItem(row, 0, QTableWidgetItem(name))
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setDefault(True)
        buttons.rejected.connect(self.dialog.reject)
        buttons.accepted.connect(self.dialog.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.dialog.reject)
        layout.addWidget(buttons)
        self.opened = []
        self.closed = []
        self.dialog.finished.connect(self.closed.append)
        self.table.itemActivated.connect(lambda item: self.opened.append(item.text()))
        self.dialog.show()

    def test_enter_on_a_row_activates_it_and_does_not_reach_the_default_button(self):
        enter_activates(self.table)
        self.table.setCurrentCell(1, 0)
        QTest.keyClick(self.table, Qt.Key.Key_Return)
        self.assertEqual(self.opened, ["second"])
        self.assertEqual(self.closed, [], "the dialog's default button took the key too")
        QTest.keyClick(self.table, Qt.Key.Key_Enter)
        self.assertEqual(self.opened, ["second", "second"])

    def test_without_a_current_row_the_key_is_left_alone(self):
        enter_activates(self.table)
        self.table.setCurrentCell(-1, -1)
        QTest.keyClick(self.table, Qt.Key.Key_Return)
        self.assertEqual(self.opened, [])

    def test_a_view_can_give_the_keyboard_its_own_answer(self):
        asked = []
        enter_activates(self.table, lambda index: asked.append(index.row()))
        self.table.setCurrentCell(0, 0)
        QTest.keyClick(self.table, Qt.Key.Key_Return)
        self.assertEqual(asked, [0])
        self.assertEqual(self.opened, [], "nothing is emitted when a slot answers")

    def test_no_list_in_the_studio_is_double_click_only(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import design_check

        self.assertEqual(design_check.counts().get("double-click-only", 0), 0)
        self.assertEqual(design_check.counts().get("status-colour-alone", 0), 0)


class TestTheMainWindowFromTheKeyboard(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def test_ctrl_and_a_number_goes_to_that_tab(self):
        keys = [action.shortcut().toString() for action in self.window.tab_actions]
        self.assertEqual(keys, ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5"])
        names = [action.text() for action in self.window.tab_actions]
        self.assertEqual(names, [self.window.tabs.tabText(n) for n in range(5)])
        for tab in range(5):
            self.window.tabs.setTabEnabled(tab, True)
        # With every gate held open: what opens them is not this test's.
        with mock.patch.object(self.window, "update_tab_gates"):
            self.window.tab_actions[3].trigger()
            self.assertEqual(self.window.tabs.currentIndex(), 3)
            self.window.tab_actions[0].trigger()
            self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_a_tab_that_is_shut_is_not_gone_to(self):
        # A window with no game files loaded: only Start is open.
        self.window.tabs.setCurrentIndex(0)
        self.window.tab_actions[2].trigger()
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertFalse(self.window.tabs.isTabEnabled(2))
        self.window._sync_tab_actions()
        self.assertFalse(self.window.tab_actions[2].isEnabled())
        self.assertTrue(self.window.tab_actions[0].isEnabled())


class TestCopyKeepsTheFocus(unittest.TestCase):
    def test_the_button_stays_enabled_and_gets_its_own_text_back(self):
        button = QPushButton("Copy")
        flash_button_feedback(button, duration_ms=30)
        self.assertEqual(button.text(), "Copied!")
        self.assertTrue(button.isEnabled(), "disabling it throws the focus elsewhere")
        # Pressed again during the flash: still its own text afterwards.
        flash_button_feedback(button, duration_ms=30)
        QTest.qWait(120)
        self.assertEqual(button.text(), "Copy")
        flash_button_feedback(button, duration_ms=30)
        QTest.qWait(120)
        self.assertEqual(button.text(), "Copy")

    def test_only_the_latest_press_puts_the_text_back(self):
        """Pressed again while it says "Copied!", it says so for as long as
        the second press asks: the first press's timer does not cut it short."""
        button = QPushButton("Copy")
        flash_button_feedback(button, duration_ms=30)
        flash_button_feedback(button, duration_ms=5000)
        QTest.qWait(150)  # the first timer has fired, the second has not
        self.assertEqual(button.text(), "Copied!")
        button.deleteLater()


def _count(image, colour: str) -> int:
    wanted = QColor(colour).rgb()
    return sum(
        1 for y in range(image.height()) for x in range(image.width())
        if image.pixel(x, y) == wanted
    )


class TestEveryControlShowsTheFocus(_ThemeCase):
    """Each kind of control draws something in the scheme's focus colour
    when it has the focus that it does not draw without it. The colour's
    3:1 against the surfaces is ``test_design``'s to check."""

    def controls(self):
        primary = QPushButton("Export this mod")
        primary.setObjectName("Primary")
        recents = QListWidget()
        recents.setObjectName("Recents")
        recents.addItem("Better Bricks")
        tree = QTreeWidget()
        QTreeWidgetItem(tree, ["Residents"])
        table = QTableWidget(1, 1)
        listing = QListWidget()
        listing.addItem("row")
        combo = QComboBox()
        combo.addItem("Gameplay")
        return {
            "button": QPushButton("Refresh"),
            "primary button": primary,
            "checkbox": QCheckBox("Header comment"),
            "radio button": QRadioButton("This is my mod"),
            "line edit": QLineEdit("Better Bricks"),
            "text box": QPlainTextEdit("notes"),
            "combo box": combo,
            "spin box": QSpinBox(),
            "spin box of decimals": QDoubleSpinBox(),
            "list": listing,
            "recent loadouts": recents,
            "tree": tree,
            "table": table,
            "help tip": HelpTip("What it does.", "Header comment"),
        }

    def test_in_every_scheme(self):
        for key, scheme in theme.SCHEMES.items():
            theme.apply(_app, scheme)
            host = QWidget()
            layout = QVBoxLayout(host)
            # Something else to hold the focus while a control is without it.
            elsewhere = QLineEdit()
            layout.addWidget(elsewhere)
            controls = self.controls()
            for control in controls.values():
                layout.addWidget(control)
            host.show()
            host.activateWindow()
            _app.processEvents()
            for name, control in controls.items():
                with self.subTest(scheme=key, control=name):
                    # A primary button's ring is the text colour: the focus
                    # colour is its own fill.
                    ring = scheme["text"] if name == "primary button" else scheme["focus"]
                    elsewhere.setFocus()
                    _app.processEvents()
                    without = _count(control.grab().toImage(), ring)
                    control.setFocus()
                    _app.processEvents()
                    self.assertTrue(control.hasFocus())
                    self.assertGreater(_count(control.grab().toImage(), ring), without)
            host.deleteLater()

    def test_the_current_tab_shows_the_focus_in_a_dialog_and_in_the_main_window(self):
        """In every scheme.  In Granite the main tabs' focus was an underline
        in the accent, which is the focus colour there, under the selected
        tab, which already has one: tabbing to them, or to the XML view's
        strip of mods inside them, showed nothing (H2-17)."""
        for key, scheme in theme.SCHEMES.items():
            theme.apply(_app, scheme)
            for name in ("", "MainTabs", "strip"):
                tabs = QTabWidget()
                tabs.setObjectName("MainTabs" if name == "strip" else name)
                tabs.addTab(QLabel("one"), "Start")
                tabs.addTab(QLabel("two"), "Export")
                bar = tabs.tabBar()
                if name == "strip":
                    # A tab bar of its own inside the main tabs, as the strip is.
                    bar = QTabBar()
                    bar.setObjectName("ModStrip")
                    bar.addTab("Base game")
                    bar.addTab("My Anno Mod")
                    bar.setCurrentIndex(1)
                    tabs.widget(0).deleteLater()
                    tabs.removeTab(0)
                    page = QWidget()
                    QVBoxLayout(page).addWidget(bar)
                    tabs.insertTab(0, page, "Start")
                    tabs.setCurrentIndex(0)
                elsewhere = QLineEdit()
                host = QWidget()
                layout = QVBoxLayout(host)
                layout.addWidget(elsewhere)
                layout.addWidget(tabs)
                host.show()
                host.activateWindow()
                with self.subTest(scheme=key, tabs=name or "dialog"):
                    elsewhere.setFocus()
                    _app.processEvents()
                    without = _count(bar.grab().toImage(), theme.FOCUS)
                    # A real Tab is a key pressed first. Without one, a test
                    # that clicked before this one (the app's focus filter
                    # stays installed) left the mouse as what was last used,
                    # and a focus handed on by Tab is then drawn as pointed.
                    QTest.keyClick(elsewhere, Qt.Key.Key_Right)
                    bar.setFocus(Qt.FocusReason.TabFocusReason)
                    _app.processEvents()
                    self.assertTrue(bar.hasFocus())
                    self.assertGreater(_count(bar.grab().toImage(), theme.FOCUS), without)
                host.deleteLater()


class TestAClickDoesNotDrawTheFocus(_ThemeCase):
    """A control that got the focus from a click is drawn as it is without
    it, and draws its mark at the first key: the click said where the
    pointer is, and an underline under a checkbox just clicked read as a
    second thing selected."""

    def setUp(self):
        super().setUp()
        theme.keep_focus_marks_for_the_keyboard(_app)

    def controls(self):
        primary = QPushButton("Export this mod")
        primary.setObjectName("Primary")
        segment = QPushButton("XML")
        segment.setObjectName("Segment")
        segment.setCheckable(True)
        return {
            "button": QPushButton("Refresh"),
            "primary button": primary,
            "segment": segment,
            "checkbox": QCheckBox("Header comment"),
            "radio button": QRadioButton("This is my mod"),
        }

    def host(self, controls):
        host = QWidget()
        layout = QVBoxLayout(host)
        elsewhere = QLineEdit()
        layout.addWidget(elsewhere)
        for control in controls:
            layout.addWidget(control)
        host.show()
        host.activateWindow()
        _app.processEvents()
        return host, elsewhere

    def test_in_every_scheme(self):
        for key, scheme in theme.SCHEMES.items():
            theme.apply(_app, scheme)
            controls = self.controls()
            host, elsewhere = self.host(controls.values())
            for name, control in controls.items():
                with self.subTest(scheme=key, control=name):
                    # The pressed-in half of a switch is filled with the
                    # accent in a contrast scheme, as a primary button is.
                    filled = name == "primary button" or (
                        name == "segment" and scheme.contrast)
                    ring = scheme["text"] if filled else scheme["focus"]
                    QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                    _app.processEvents()
                    self.assertTrue(control.hasFocus())
                    clicked = control.grab().toImage()
                    # The same control in the same state, without the focus.
                    elsewhere.setFocus()
                    _app.processEvents()
                    without = control.grab().toImage()
                    self.assertEqual(_count(clicked, ring), _count(without, ring))
                    self.assertEqual(clicked.size(), without.size())
                    # A modifier alone is not a key pressed on it; a key is.
                    QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                    QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                    QTest.keyClick(control, Qt.Key.Key_Shift)
                    _app.processEvents()
                    quiet = _count(control.grab().toImage(), ring)
                    QTest.keyClick(control, Qt.Key.Key_F9)
                    _app.processEvents()
                    self.assertGreater(_count(control.grab().toImage(), ring), quiet)
            host.deleteLater()

    def test_a_control_reached_any_other_way_draws_it(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        box = QCheckBox("Header comment")
        host, elsewhere = self.host([box])
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        elsewhere.setFocus()
        _app.processEvents()
        without = _count(box.grab().toImage(), theme.FOCUS)
        QTest.keyClick(elsewhere, Qt.Key.Key_Tab)
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertGreater(_count(box.grab().toImage(), theme.FOCUS), without)
        # And by a shortcut or by the Studio, which press no key on it.
        for reason in (Qt.FocusReason.ShortcutFocusReason, Qt.FocusReason.OtherFocusReason):
            QTest.mouseClick(box, Qt.MouseButton.LeftButton)
            elsewhere.setFocus()
            box.setFocus(reason)
            _app.processEvents()
            self.assertGreater(_count(box.grab().toImage(), theme.FOCUS), without)
        host.deleteLater()

    def test_a_menu_closing_over_a_clicked_control_does_not_draw_it(self):
        """The focus comes back from a menu for Qt's own reason, which was
        read as the keyboard's: a clicked checkbox drew its mark once a menu
        opened with the mouse had closed."""
        theme.apply(_app, theme.SCHEMES["granite"])
        box = QCheckBox("Header comment")
        host, elsewhere = self.host([box])
        elsewhere.setFocus()
        _app.processEvents()
        without = _count(box.grab().toImage(), theme.FOCUS)
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        menu = QMenu(host)
        menu.addAction("Copy")
        menu.popup(host.mapToGlobal(QPoint(2, 2)))
        _app.processEvents()
        menu.close()
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertTrue(box.property("pointed"))
        self.assertEqual(_count(box.grab().toImage(), theme.FOCUS), without)
        # Opened or closed from the keyboard, the mark is drawn.
        menu.popup(host.mapToGlobal(QPoint(2, 2)))
        _app.processEvents()
        QTest.keyClick(menu, Qt.Key.Key_Escape)
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertGreater(_count(box.grab().toImage(), theme.FOCUS), without)
        host.deleteLater()

    def test_nor_does_a_menu_take_the_mark_from_a_control_tabbed_to(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        box = QCheckBox("Header comment")
        host, elsewhere = self.host([box])
        elsewhere.setFocus()
        _app.processEvents()
        without = _count(box.grab().toImage(), theme.FOCUS)
        QTest.keyClick(elsewhere, Qt.Key.Key_Tab)
        self.assertTrue(box.hasFocus())
        QTest.mouseClick(host, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        menu = QMenu(host)
        menu.addAction("Copy")
        menu.popup(host.mapToGlobal(QPoint(2, 2)))
        _app.processEvents()
        menu.close()
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertGreater(_count(box.grab().toImage(), theme.FOCUS), without)
        host.deleteLater()

    def test_a_clicked_button_that_disables_itself_does_not_draw_it_on_the_next(self):
        """Qt moves the focus on from a control that can no longer hold it,
        and gives Tab as the reason with no key pressed."""
        theme.apply(_app, theme.SCHEMES["granite"])
        button, box = QPushButton("Refresh"), QCheckBox("Header comment")
        button.clicked.connect(lambda: button.setEnabled(False))
        host, _elsewhere = self.host([button, box])
        without = _count(box.grab().toImage(), theme.FOCUS)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertEqual(_count(box.grab().toImage(), theme.FOCUS), without)
        # From the keyboard it does: Space pressed on the button.
        button.setEnabled(True)
        button.setFocus(Qt.FocusReason.ShortcutFocusReason)
        QTest.keyClick(button, Qt.Key.Key_Space)
        _app.processEvents()
        self.assertTrue(box.hasFocus())
        self.assertGreater(_count(box.grab().toImage(), theme.FOCUS), without)
        host.deleteLater()

    def opened_on_a_radio_button(self):
        """A window whose first control is a radio button, as New asset is,
        and so the one it opens with the focus on."""
        host = QWidget()
        layout = QVBoxLayout(host)
        radio = QRadioButton("An existing asset")
        radio.setChecked(True)
        layout.addWidget(radio)
        elsewhere = QLineEdit()
        layout.addWidget(elsewhere)
        host.show()
        host.activateWindow()
        _app.processEvents()
        self.assertTrue(radio.hasFocus())
        with_it = _count(radio.grab().toImage(), theme.FOCUS)
        elsewhere.setFocus()
        _app.processEvents()
        without = _count(radio.grab().toImage(), theme.FOCUS)
        host.deleteLater()
        return with_it, without

    def test_a_window_opened_with_a_click_does_not_draw_it_on_what_it_opens_on(self):
        """Real: New asset…, clicked, opened with the focus underline under
        "An existing asset", beside tabs that underline what is current."""
        theme.apply(_app, theme.SCHEMES["granite"])
        button = QPushButton("New asset…")
        host, _elsewhere = self.host([button])
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        with_it, without = self.opened_on_a_radio_button()
        self.assertEqual(with_it, without)
        host.deleteLater()

    def test_a_window_opened_from_the_keyboard_draws_it(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        button = QPushButton("New asset…")
        host, _elsewhere = self.host([button])
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        QTest.keyClick(button, Qt.Key.Key_Space)
        with_it, without = self.opened_on_a_radio_button()
        self.assertGreater(with_it, without)
        host.deleteLater()

    def test_a_click_still_does_what_a_click_does(self):
        box, button = QCheckBox("Header comment"), QPushButton("Refresh")
        pressed = []
        button.clicked.connect(lambda: pressed.append(1))
        host, _elsewhere = self.host([box, button])
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertTrue(box.isChecked())
        self.assertEqual(pressed, [1])
        host.deleteLater()

    def test_a_clicked_tab_is_drawn_as_the_current_tab_and_no_more(self):
        theme.apply(_app, theme.SCHEMES["marble"])
        for name in ("", "MainTabs"):
            tabs = QTabWidget()
            tabs.setObjectName(name)
            tabs.addTab(QLabel("one"), "Start")
            tabs.addTab(QLabel("two"), "Export")
            host, elsewhere = self.host([tabs])
            bar = tabs.tabBar()
            with self.subTest(tabs=name or "dialog"):
                QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
                _app.processEvents()
                self.assertEqual(tabs.currentIndex(), 1)
                clicked = _count(bar.grab().toImage(), theme.FOCUS)
                elsewhere.setFocus()
                _app.processEvents()
                self.assertEqual(clicked, _count(bar.grab().toImage(), theme.FOCUS))
                QTest.keyClick(elsewhere, Qt.Key.Key_Tab)
                _app.processEvents()
                self.assertTrue(bar.hasFocus())
                self.assertGreater(_count(bar.grab().toImage(), theme.FOCUS), clicked)
            host.deleteLater()


class TestNoStatusByColourAlone(_ThemeCase):
    def test_a_status_label_carries_its_glyph(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        for tone in theme.STATUS:
            label = QLabel("3 mods changed since it was made live")
            theme.tint(label, tone)
            self.assertEqual(label.property("glyph"), tone)
            image = theme.indicators()["label_" + tone]
            self.assertIn('QLabel[glyph="%s"]' % tone, theme.STYLESHEET)
            self.assertIn("background-image: url(%s)" % image, theme.STYLESHEET)
        # Room for it is made by the style sheet, so the text does not sit
        # under the glyph.
        plain, warned = QLabel("drift"), theme.tint(QLabel("drift"), "warn")
        for label in (plain, warned):
            label.ensurePolished()
        self.assertGreater(warned.sizeHint().width(), plain.sizeHint().width())

    def test_a_label_already_on_screen_makes_room_when_it_becomes_a_status(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        host = QWidget()
        layout = QVBoxLayout(host)
        tinted, named = QLabel("drift"), QLabel("All 2 changes verified")
        layout.addWidget(tinted)
        layout.addWidget(named)
        host.show()
        _app.processEvents()
        self.assertEqual(tinted.contentsRect().left(), 0)
        theme.tint(tinted, "warn")
        named.setObjectName("Ok")
        theme.repolish(named)
        _app.processEvents()
        # Without the room, the glyph is drawn over the first letters.
        self.assertGreaterEqual(tinted.contentsRect().left(), 16)
        self.assertGreaterEqual(named.contentsRect().left(), 16)
        theme.tint(tinted)
        _app.processEvents()
        self.assertEqual(tinted.contentsRect().left(), 0)
        host.deleteLater()

    def test_a_status_label_is_as_tall_as_the_text_it_draws(self):
        """The padding made it a frame, and a frame's label measured an
        indent it did not draw: it wrapped early, kept a line too many and
        centred its text in it, a line below the glyph."""
        from PySide6.QtCore import QRect

        theme.apply(_app, theme.SCHEMES["granite"])
        text = ("What these mods say about each other: 1 mod will not load because "
                "another replaces it, 2 mods need one that will not load.")
        for name in ("", "StepHint"):
            label = QLabel(text)
            label.setObjectName(name)
            label.setWordWrap(True)
            theme.tint(label, "warn")
            label.ensurePolished()
            room = label.contentsMargins().left()
            for width in range(120, 700, 3):
                drawn = label.fontMetrics().boundingRect(
                    QRect(0, 0, width - room, 10000), Qt.TextFlag.TextWordWrap, text).height()
                self.assertEqual(label.heightForWidth(width), drawn, (name, width))

    def test_and_its_text_starts_beside_the_glyph(self):
        """The glyph is drawn at the top. Given more height than its text, a
        label centred the text, and the glyph stood above it."""
        theme.apply(_app, theme.SCHEMES["granite"])
        vertical = Qt.AlignmentFlag.AlignVertical_Mask
        label = QLabel("24 GUIDs are taken twice")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        theme.tint(label, "warn")
        self.assertEqual(label.alignment(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        theme.tint(label, "error")
        self.assertEqual(label.alignment() & vertical, Qt.AlignmentFlag.AlignTop)
        # Back to where it was without one.
        theme.tint(label)
        self.assertEqual(label.alignment(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # And a label named for a status, once the sheet has read it again.
        label.setObjectName("Warn")
        theme.repolish(label)
        self.assertEqual(label.alignment() & vertical, Qt.AlignmentFlag.AlignTop)
        # Top-aligned, it is no taller than its line.
        plain = QLabel("24 GUIDs are taken twice")
        plain.ensurePolished()
        self.assertEqual(label.sizeHint().height(), plain.sizeHint().height())

    def test_a_tone_that_is_not_a_status_has_none_and_one_can_be_left_out(self):
        label = QLabel("a rule")
        theme.tint(label, "rule")
        self.assertFalse(label.property("glyph"))
        theme.tint(label, "warn", glyph=False)
        self.assertEqual(label.property("tone"), "warn")
        self.assertFalse(label.property("glyph"))
        theme.tint(label, "warn")
        self.assertEqual(label.property("glyph"), "warn")
        theme.tint(label)
        self.assertFalse(label.property("glyph"))

    def test_labels_named_for_a_status_get_the_glyph_from_the_name(self):
        for name in ("Warn", "Error", "Ok"):
            self.assertIn("QLabel#%s," % name, theme.STYLESHEET)

    def test_a_marked_item_has_the_colour_and_the_glyph(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        item = theme.mark(QListWidgetItem("older than installed"), "warn")
        self.assertEqual(item.foreground().color().name(), theme.WARN)
        self.assertFalse(item.icon().isNull())
        tree = QTreeWidget()
        tree.setColumnCount(2)
        row = theme.mark(QTreeWidgetItem(tree, ["Residents", "20 → 25"]), "error", 0)
        self.assertEqual(row.foreground(0).color().name(), theme.ERROR)
        self.assertFalse(row.icon(0).isNull())
        self.assertTrue(row.icon(1).isNull())
        # Marked with something that is not a status, the glyph comes off.
        theme.mark(row, "text-dim", 0)
        self.assertTrue(row.icon(0).isNull())
        self.assertEqual(row.foreground(0).color().name(), theme.TEXT_DIM)

    def test_the_glyph_is_drawn_in_the_scheme_in_effect(self):
        theme.apply(_app, theme.SCHEMES["granite"])
        icon = theme.icon("error")
        dark = _count(icon.pixmap(32, 32).toImage(), theme.ERROR)
        self.assertGreater(dark, 0)
        theme.apply(_app, theme.SCHEMES["quartz"])
        image = icon.pixmap(32, 32).toImage()
        self.assertGreater(_count(image, theme.ERROR), 0)
        self.assertEqual(_count(image, theme.SCHEMES["granite"]["error"]), 0)
        with self.assertRaises(ValueError):
            theme.icon("rule")

    def test_a_contested_change_in_the_export_problem_list_is_marked(self):
        window = MainWindow()
        self.addCleanup(window.close)
        item = QListWidgetItem("problem")
        theme.mark(item, "error")
        window.output.check_list.addItem(item)
        self.assertFalse(window.output.check_list.item(0).icon().isNull())


class TestTheReferenceGraphFromTheKeyboard(_ThemeCase):
    """Three columns: two assets that refer to the middle one, the middle
    one, and one it refers to, with an overflow marker under it."""

    def setUp(self):
        super().setUp()
        self.view = ReferenceView()
        step_x, step_y = NODE_WIDTH + COLUMN_GAP, NODE_HEIGHT + ROW_GAP
        self.nodes = {}
        for guid, role, column, x, y in (
            ("left-a", "normal", -1, -step_x, -step_y / 2),
            ("left-b", "normal", -1, -step_x, step_y / 2),
            ("middle", "focus", 0, 0, 0),
            ("right", "normal", 1, step_x, -step_y / 2),
            ("", "more", 1, step_x, step_y / 2),
        ):
            node = GraphNode(guid, guid or "+ 3 more", "Template", role, column)
            node.spoken = "%s, Template" % (guid or "3 more assets")
            node.setPos(x, y)
            self.view.scene().addItem(node)
            self.nodes[guid or "more"] = node
        self.view.resize(900, 400)
        self.view.show()
        self.view.activateWindow()
        self.view.setFocus()
        _app.processEvents()
        self.clicked, self.opened, self.more, self.menus = [], [], [], []
        self.view.nodeClicked.connect(self.clicked.append)
        self.view.nodeActivated.connect(self.opened.append)
        self.view.moreClicked.connect(self.more.append)
        self.view.menuRequested.connect(self.menus.append)

    def on(self) -> str:
        return self.view.cursor_node().guid or "more"

    def test_it_starts_on_the_asset_in_the_middle(self):
        self.assertEqual(self.on(), "middle")
        self.assertEqual(self.view.accessibleName(), "Reference graph")

    def test_arrow_keys_move_between_nodes(self):
        QTest.keyClick(self.view, Qt.Key.Key_Left)
        self.assertIn(self.on(), ("left-a", "left-b"))
        QTest.keyClick(self.view, Qt.Key.Key_Up)
        self.assertEqual(self.on(), "left-a")
        QTest.keyClick(self.view, Qt.Key.Key_Down)
        self.assertEqual(self.on(), "left-b")
        QTest.keyClick(self.view, Qt.Key.Key_Down)
        self.assertEqual(self.on(), "left-b", "nothing below: it stays")
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        self.assertEqual(self.on(), "middle")
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        QTest.keyClick(self.view, Qt.Key.Key_Down)
        self.assertEqual(self.on(), "more")

    def test_the_node_the_keyboard_is_on_is_drawn_and_spoken(self):
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        QTest.keyClick(self.view, Qt.Key.Key_Up)
        self.assertEqual(self.on(), "right")
        self.assertTrue(self.nodes["right"].keyboard)
        self.assertFalse(self.nodes["middle"].keyboard)
        self.assertEqual(self.view.accessibleDescription(), "right, Template")

    def test_enter_centres_ctrl_enter_opens_and_the_menu_key_asks_for_the_menu(self):
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        QTest.keyClick(self.view, Qt.Key.Key_Up)
        QTest.keyClick(self.view, Qt.Key.Key_Return)
        self.assertEqual((self.clicked, self.opened), (["right"], []))
        QTest.keyClick(self.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.opened, ["right"])
        QTest.keyClick(self.view, Qt.Key.Key_Menu)
        self.assertEqual(len(self.menus), 1)
        self.assertIs(self.view.node_at(self.menus[0]), self.nodes["right"])
        QTest.keyClick(self.view, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(self.menus), 2)

    def test_enter_on_the_overflow_marker_opens_the_whole_list(self):
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        QTest.keyClick(self.view, Qt.Key.Key_Down)
        QTest.keyClick(self.view, Qt.Key.Key_Return)
        self.assertEqual((self.more, self.clicked), ([1], []))
        # There is nothing to open in the editor, so Ctrl+Enter is the same.
        QTest.keyClick(self.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual((self.more, self.opened), ([1, 1], []))

    def test_the_ring_goes_when_the_focus_does_and_comes_back_with_it(self):
        QTest.keyClick(self.view, Qt.Key.Key_Left)
        on = self.view.cursor_node()
        other = QLineEdit()
        other.show()
        other.activateWindow()
        other.setFocus()
        _app.processEvents()
        self.assertFalse(on.keyboard)
        self.view.activateWindow()
        self.view.setFocus()
        _app.processEvents()
        self.assertTrue(on.keyboard)

    def test_a_redraw_puts_the_keyboard_back_where_it_was(self):
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        QTest.keyClick(self.view, Qt.Key.Key_Up)
        was = self.view._cursor
        self.view.scene().clear()
        again = GraphNode("right", "right", "Template", "normal", 1)
        again.setPos(QPoint(0, 0))
        self.view.scene().addItem(again)
        self.view.restore_cursor()
        self.assertEqual(self.view._cursor, was)
        self.assertTrue(again.keyboard)

    def test_clicking_the_canvas_into_focus_keeps_where_it_was_panned_to(self):
        step_x = NODE_WIDTH + COLUMN_GAP
        for column in range(2, 12):
            far = GraphNode("far-%d" % column, "far", "Template", "normal", column)
            far.setPos(column * step_x, 0)
            self.view.scene().addItem(far)
        self.view.scene().setSceneRect(self.view.scene().itemsBoundingRect())
        # One window, as in the app: the click is what brings the focus over,
        # not a window coming to the front.
        host = QWidget()
        self.addCleanup(host.close)
        layout = QVBoxLayout(host)
        other = QLineEdit()
        layout.addWidget(other)
        layout.addWidget(self.view)
        host.resize(900, 450)
        host.show()
        host.activateWindow()
        other.setFocus()
        _app.processEvents()
        bar = self.view.horizontalScrollBar()
        bar.setValue(bar.maximum())
        panned = bar.value()
        self.assertGreater(panned, 0)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(5, 5))
        _app.processEvents()
        self.assertTrue(self.view.hasFocus())
        self.assertEqual(bar.value(), panned, "the click threw the pan away")
        self.assertTrue(self.view.cursor_node().keyboard)
        # Coming by the keyboard still shows where the keyboard is.
        other.setFocus()
        _app.processEvents()
        self.view.setFocus(Qt.FocusReason.TabFocusReason)
        _app.processEvents()
        self.assertLess(bar.value(), panned)



class TestClosingWhileTheLoaderIsAsked(unittest.TestCase):
    """Closing the unread-ModOps dialog during a question froze the window
    until the loader's current run ended, which takes seconds."""

    def test_the_dialog_closes_at_once_and_the_question_finishes_by_itself(self):
        import time

        from PySide6.QtCore import QThread, Signal

        from anno_modding_studio.ui import unread_dialog

        class SlowQuestion(QThread):
            progressed = Signal(int, int)
            succeeded = Signal(str, object)

            def run(self):
                time.sleep(0.6)  # one run of the loader, which cannot be cut short

        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(window.close)
        dialog = unread_dialog.UnreadDialog(window.loadout, window, window)
        worker = SlowQuestion(dialog)
        answers = []
        worker.succeeded.connect(lambda *answer: answers.append(answer))
        dialog._asking = worker
        worker.start()

        started = time.monotonic()
        dialog.done(0)
        self.assertLess(time.monotonic() - started, 0.3, "closing waited for the loader")
        self.assertTrue(worker.isInterruptionRequested())
        self.assertIsNone(worker.parent(), "it would be deleted with its dialog while running")
        self.assertIn(worker, unread_dialog._WINDING_DOWN)

        # A new question waits for the loader's work folder to be free.
        again = unread_dialog.UnreadDialog(window.loadout, window, window)
        with mock.patch.object(again, "_selected_entry", return_value=object()), \
                mock.patch.object(again, "_loader", return_value=object()), \
                mock.patch.object(window, "index", object(), create=True):
            again.ask_loader()
        self.assertIsNone(again._asking)
        self.assertIn("still finishing", again.ask_label.text())

        self.assertTrue(worker.wait(5000))
        _app.processEvents()
        self.assertNotIn(worker, unread_dialog._WINDING_DOWN)
        self.assertEqual(answers, [], "an answer has nowhere to go once the dialog is shut")

    def test_closing_with_nothing_asked_is_as_before(self):
        from anno_modding_studio.ui import unread_dialog

        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(window.close)
        dialog = unread_dialog.UnreadDialog(window.loadout, window, window)
        dialog.done(0)
        self.assertEqual(unread_dialog._WINDING_DOWN, set())


class TestTheComboBoxArrow(_ThemeCase):
    def test_it_is_the_schemes_chevron_and_not_a_raised_button(self):
        theme.apply(_app, theme.SCHEMES["marble"])
        images = theme.indicators()
        self.assertIn("QComboBox::drop-down {", theme.STYLESHEET)
        self.assertIn("QComboBox::down-arrow { image: url(%s)" % images["spin_down"],
                      theme.STYLESHEET)
        drop = theme.STYLESHEET[theme.STYLESHEET.index("QComboBox::drop-down {"):]
        self.assertIn("border: none;", drop[: drop.index("}")])



class TestTheReferencesTab(unittest.TestCase):
    """Area 4: the tab says what it is for only while it has no asset, and
    what its controls mean is behind a ? beside each."""

    def setUp(self):
        self.panel = ReferencePanel()
        self.addCleanup(self.panel.deleteLater)

    def test_no_heading_repeats_the_tabs_name(self):
        from PySide6.QtWidgets import QLabel

        headings = [label.text() for label in self.panel.findChildren(QLabel)
                    if label.objectName() == "Heading"]
        self.assertEqual(headings, [])

    def test_what_to_do_shows_only_while_there_is_no_asset(self):
        self.assertFalse(self.panel.hint.isHidden())
        self.assertIn("asset editor", self.panel.hint.text())
        self.assertTrue(self.panel.subtitle_label.isHidden())

        self.panel.index = mock.Mock()
        self.panel.index.asset_row.return_value = None
        with mock.patch.object(self.panel, "refresh"):
            self.panel.show_asset("1010032")
        self.assertTrue(self.panel.hint.isHidden())
        self.assertFalse(self.panel.subtitle_label.isHidden())

        self.panel.clear()
        self.assertFalse(self.panel.hint.isHidden())
        self.assertEqual(self.panel.subtitle_label.full_text(), "")

    def test_each_filter_has_its_help_beside_it(self):
        from anno_modding_studio.ui.help_tip import HelpTip

        named = {tip.accessibleName() for tip in self.panel.findChildren(HelpTip)}
        for box in (self.panel.uncertain_box, self.panel.nominal_box):
            self.assertEqual(box.toolTip(), "")
            self.assertTrue(any(box.text() in name for name in named), box.text())
        self.assertTrue(any("References" in name for name in named))

    def test_depth_is_reached_by_its_label(self):
        from PySide6.QtWidgets import QLabel

        caption = next(label for label in self.panel.findChildren(QLabel)
                       if label.buddy() is self.panel.depth_spin)
        self.assertEqual(caption.text(), "&Depth:")

    def test_a_long_name_does_not_widen_the_tab(self):
        before = self.panel.minimumSizeHint().width()
        self.panel.title_label.setText("An asset with a very long name " * 6)
        self.panel.layout().activate()
        self.assertEqual(self.panel.minimumSizeHint().width(), before)

    def test_the_list_names_its_filter_and_its_table(self):
        rows = [ListedAsset(guid="1", label="Bricks", template="Product", through="Costs")]
        dialog = ReferenceListDialog(rows, "1 asset that points at Bricks")
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.search_box.accessibleName(), "Filter")
        self.assertEqual(dialog.table.accessibleName(), "1 asset that points at Bricks")
        self.assertEqual(dialog.open_button.text(), "&Show in graph")



class TestTheExportTab(unittest.TestCase):
    """Area 5: the tab has no heading or paragraph of its own, and what was
    in its long tooltips is behind a ? beside the control."""

    @classmethod
    def setUpClass(cls):
        cls.window = MainWindow()
        cls.out = cls.window.output

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        _isolation.forget_settings()

    def test_no_heading_repeats_the_tabs_name(self):
        from PySide6.QtWidgets import QLabel

        headings = [label.text() for label in self.out.findChildren(QLabel)
                    if label.objectName() == "Heading"]
        self.assertEqual(headings, [])

    def test_what_export_is_for_is_beside_its_rows(self):
        self.assertIsInstance(self.out.play_note, HelpTip)
        self.assertIn("Play tests", self.out.play_note.help_text())
        self.assertIn("In your game", self.out.play_note.accessibleName())

    def test_every_tooltip_on_the_tab_is_short(self):
        from PySide6.QtWidgets import QWidget

        for widget in self.out.findChildren(QWidget):
            if isinstance(widget, HelpTip):
                continue
            words = len(widget.toolTip().split())
            self.assertLessEqual(words, 15, widget.toolTip())

    def test_the_fields_are_reached_by_their_labels(self):
        from PySide6.QtWidgets import QLabel

        buddies = {label.buddy(): label.text() for label in self.out.findChildren(QLabel)
                   if label.buddy() is not None and "&" in label.text()}
        for field in (self.out.name_edit, self.out.version_edit, self.out.creator_edit,
                      self.out.category_combo, self.out.modid_edit,
                      self.out.description_edit, self.out.difficulty_combo,
                      self.out.mod_box):
            self.assertIn(field, buddies)
        letters = [text[text.index("&") + 1].lower() for text in buddies.values()]
        self.assertEqual(len(letters), len(set(letters)), sorted(buddies.values()))
        # Alt and a letter of the menu bar opens that menu.
        self.assertFalse(set(letters) & set("fevh"), sorted(buddies.values()))

    def test_there_are_no_output_options(self):
        for gone in ("opt_header", "opt_comments", "opt_off", "opt_group"):
            self.assertFalse(hasattr(self.out, gone), gone)



class TestTheDialogsThatReadMods(unittest.TestCase):
    """Area 6: one mod and a folder of mods offer the same three ways of
    reading, each explained once."""

    @classmethod
    def setUpClass(cls):
        import design_dialogs

        cls.window = MainWindow()
        cls.one = design_dialogs.DIALOGS["mod_import"](cls.window)
        cls.folder = design_dialogs.DIALOGS["mods_folder"](cls.window)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        _isolation.forget_settings()

    @staticmethod
    def _help(dialog):
        return {tip.accessibleName(): tip.help_text() for tip in dialog.findChildren(HelpTip)}

    def test_both_explain_each_way_of_reading_in_the_same_words(self):
        from anno_modding_studio.ui import help_tip

        one, folder = self._help(self.one), self._help(self.folder)
        for box, text in (("keep_inert_box", help_tip.KEEP_INERT_HELP),):
            label = getattr(self.one, box).text()
            self.assertEqual(label, getattr(self.folder, box).text())
            self.assertEqual(getattr(self.one, box).toolTip(), "")
            name = "Help: " + label.replace("&", "")
            self.assertEqual(one[name], text)
            self.assertEqual(folder[name], text)

    def test_no_heading_repeats_a_title(self):
        from PySide6.QtWidgets import QLabel

        for dialog in (self.one, self.folder):
            texts = [label.text() for label in dialog.findChildren(QLabel)
                     if label.objectName() == "Heading"]
            self.assertEqual(texts, [], dialog.windowTitle())

    def test_this_is_my_mod_keeps_its_label_and_says_why_it_is_off(self):
        self.assertEqual(self.one.metadata_box.text(), "This is my mo&d")
        tips = self._help(self.one)
        self.assertIn("update to that mod", tips["Help: This is my mod"])

    def test_unloading_says_what_is_lost_without_being_asked(self):
        import design_dialogs
        from PySide6.QtWidgets import QLabel

        dialog = design_dialogs.DIALOGS["unload"](self.window)
        self.addCleanup(dialog.deleteLater)
        shown = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertTrue(any("are lost" in text for text in shown), shown)

    def test_a_mods_option_is_named_by_its_question(self):
        import design_dialogs

        dialog = design_dialogs.DIALOGS["mod_options"](self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.controls["Roomier"].accessibleName(),
                         "Artisans house five more")

    def test_each_script_hook_is_reached_by_its_letter(self):
        import design_dialogs
        from PySide6.QtWidgets import QLabel

        dialog = design_dialogs.DIALOGS["scripts"](self.window)
        self.addCleanup(dialog.deleteLater)
        captions = {label.text() for label in dialog.findChildren(QLabel) if label.buddy()}
        for hook in ("&Init", "&Load", "&Unload", "&Tick"):
            self.assertIn(hook, captions)



class TestPlayingAndTheLibrary(unittest.TestCase):
    """Area 7: what cannot be undone stays in view, and the rest is behind
    a ? beside it."""

    @classmethod
    def setUpClass(cls):
        cls.window = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        _isolation.forget_settings()

    def build(self, key):
        import design_dialogs

        dialog = design_dialogs.DIALOGS[key](self.window)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_no_heading_repeats_a_title(self):
        from PySide6.QtWidgets import QLabel

        for key in ("convert", "loadout_check", "unsubscribe", "modio_get"):
            dialog = self.build(key)
            texts = [label.text() for label in dialog.findChildren(QLabel)
                     if label.objectName() == "Heading"]
            self.assertEqual(texts, [], key)

    def test_converting_says_no_mod_is_deleted_and_explains_the_rest_on_request(self):
        from PySide6.QtWidgets import QLabel

        dialog = self.build("convert")
        shown = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("No mod is deleted.", shown)
        self.assertIn("link into the Studio's library", dialog.help.help_text())
        self.assertNotIn("link into the Studio's library", shown)

    def test_the_full_copy_is_a_short_label_with_its_size_beside_it(self):
        dialog = self.build("convert")
        self.assertEqual(dialog.full_box.text(), "Keep a &full copy")
        self.assertIn("kept for good", dialog.full_note.text())
        self.assertEqual(dialog.full_box.toolTip(), "")

    def test_unsubscribing_keeps_the_account_wide_warning_in_view(self):
        dialog = self.build("unsubscribe")
        self.assertIn("on every machine you play on", dialog.wide.text())
        self.assertIn("in the game's own mod browser", dialog.hint.text())
        self.assertIn("leaves the game's copy where it is", dialog.help.help_text())

    def test_starting_afresh_is_asked_with_its_own_name(self):
        from PySide6.QtWidgets import QMessageBox

        dialog = self.build("unsubscribe")
        dialog.afresh = mock.Mock(folder="mods", come_back=[1], stay_out=[])
        asked = []

        def answer(box):
            asked.append([button.text() for button in box.buttons()])
            return 0

        with mock.patch.object(QMessageBox, "exec", answer):
            self.assertFalse(dialog.confirm_afresh())
        self.assertIn("&Start afresh", asked[0])
        self.assertNotIn("&Yes", asked[0])

    def test_the_unsubscribe_dialog_closes(self):
        """Its result label was called ``done``, which is the method a dialog
        closes through, so Close raised and left the dialog open."""
        dialog = self.build("unsubscribe")
        dialog.show()
        dialog.reject()
        self.assertFalse(dialog.isVisible())

    def test_getting_a_mod_says_what_picking_one_does(self):
        dialog = self.build("modio_get")
        self.assertIn("downloaded, checked against mod.io's checksum", dialog.hint.text())
        self.assertEqual(dialog.kind.toolTip(), "")
        self.assertEqual(dialog.kind.accessibleName(), "What to search for")



class TestAQuestionNamesItsAction(unittest.TestCase):
    """Area 8: a question's button says what it does, where it said Yes."""

    def ask(self, press="Restore", **how):
        """Ask a question, look at the box, and press ``press``."""
        from anno_modding_studio.ui import widgets

        seen = {}

        def look(box):
            seen["buttons"] = [button.text() for button in box.buttons()]
            seen["default"] = box.defaultButton().text()
            seen["escape"] = box.escapeButton().text()
            return next(button for button in box.buttons()
                        if button.text().replace("&", "") == press)

        with mock.patch.object(widgets, "answer_of", look):
            answer = widgets.ask(None, "Restore these files?", "Put 3 files back.",
                                 "&Restore", **how)
        return answer, seen

    def test_the_action_is_on_the_button_and_is_the_default(self):
        answer, seen = self.ask()
        self.assertTrue(answer)
        self.assertEqual(seen["buttons"], ["&Restore", "Cancel"])
        self.assertEqual(seen["default"], "&Restore")
        self.assertEqual(seen["escape"], "Cancel")

    def test_an_action_that_destroys_something_is_not_the_default(self):
        _answer, seen = self.ask(default_action=False)
        self.assertEqual(seen["default"], "Cancel")

    def test_declining_can_be_named_too(self):
        answer, seen = self.ask(press="Keep them", cancel="&Keep them")
        self.assertFalse(answer)
        self.assertEqual(seen["buttons"], ["&Restore", "&Keep them"])
        self.assertEqual(seen["escape"], "&Keep them")

    def test_a_test_answers_by_true_false_or_the_buttons_name(self):
        from anno_modding_studio.ui import widgets

        with _isolation.answering(False) as asked:
            self.assertFalse(widgets.ask(None, "Start fresh?", "Clear it?", "&Start fresh"))
        self.assertEqual(asked.titles, ["Start fresh?"])
        self.assertEqual(asked.buttons, [["Start fresh", "Cancel"]])
        with _isolation.answering("Start fresh"):
            self.assertTrue(widgets.ask(None, "Start fresh?", "Clear it?", "&Start fresh"))

    def test_no_box_in_the_studio_offers_the_generic_pair(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import design_check

        self.assertEqual(design_check.counts()["yes-no-box"], 0)


class TestTheHelpMenu(unittest.TestCase):
    """Stage 3: the shortcuts, the guide, and About; and the Studio's folders."""

    @classmethod
    def setUpClass(cls):
        cls.window = MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def groups(self):
        from anno_modding_studio.ui import shortcuts_dialog

        return dict(shortcuts_dialog.gather(self.window))

    def test_the_menu_holds_the_shortcuts_the_guide_and_about(self):
        menu = next(top.menu() for top in self.window.menuBar().actions()
                    if top.text() == "&Help")
        self.assertEqual(
            [action.text() for action in menu.actions() if not action.isSeparator()],
            ["&Keyboard shortcuts…", "&User guide", "&Studio folders…",
             "&Check for updates", "&About Anno Modding Studio"],
        )

    def test_every_shortcut_in_a_menu_is_listed(self):
        from anno_modding_studio.ui import shortcuts_dialog

        listed = {keys for rows in self.groups().values() for _what, keys in rows}

        def walk(menu):
            for action in menu.actions():
                if action.menu() is not None:
                    yield from walk(action.menu())
                elif action.isVisible() and not action.shortcut().isEmpty():
                    yield action

        actions = [action for top in self.window.menuBar().actions() for action in walk(top.menu())]
        self.assertGreater(len(actions), 10)
        for action in actions:
            self.assertIn(shortcuts_dialog.keys_of(action.shortcuts()), listed, action.text())

    def test_a_row_is_the_action_without_its_mnemonic_or_its_dots(self):
        groups = self.groups()
        self.assertIn(("Settings", "Ctrl+,"), groups["File"])
        self.assertIn(("User guide", "F1"), groups["Help"])
        # The main tabs, whatever they are called.
        # Ctrl and a digit, and nothing between: Ctrl+Shift+4 moves a tab
        # to a window of its own.
        self.assertEqual([keys for _what, keys in groups["View"]
                          if len(keys) == 6 and keys.startswith("Ctrl+") and keys[-1].isdigit()],
                         ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5"])

    def test_an_action_with_several_keys_gives_one_to_a_line(self):
        keys = dict(self.groups()["Edit"])["Redo"].split("\n")
        self.assertGreater(len(keys), 1)
        self.assertIn("Ctrl+Y", keys)

    def test_a_new_action_is_listed_without_being_written_down(self):
        from PySide6.QtGui import QAction, QKeySequence

        menu = next(top.menu() for top in self.window.menuBar().actions()
                    if top.text() == "&View")
        extra = QAction("&Zoom to fit…", self.window)
        extra.setShortcut(QKeySequence("Ctrl+Shift+9"))
        menu.addAction(extra)
        try:
            self.assertIn(("Zoom to fit", "Ctrl+Shift+9"), self.groups()["View"])
        finally:
            menu.removeAction(extra)

    def test_back_and_forward_are_listed_once_for_both_tabs(self):
        self.assertEqual(self.groups()["Assets you have visited"],
                         [("Back to the asset before", "Alt+Left"),
                          ("Forward again", "Alt+Right")])

    def test_the_keys_a_widget_answers_itself_are_listed(self):
        groups = self.groups()
        self.assertIn(("Open the asset in the editor", "Ctrl+Enter"), groups["Reference graph"])
        self.assertIn(("Do what a double-click does", "Enter"), groups["Lists"])

    def test_the_dialog_shows_every_group_open(self):
        from anno_modding_studio.ui.shortcuts_dialog import ShortcutsDialog

        dialog = ShortcutsDialog(self.window)
        tree = dialog.tree
        tops = [tree.topLevelItem(n) for n in range(tree.topLevelItemCount())]
        self.assertEqual([top.text(0) for top in tops], list(self.groups()))
        self.assertTrue(all(top.isExpanded() and top.childCount() for top in tops))
        self.assertEqual(tree.accessibleName(), "Keyboard shortcuts")

    def test_the_guide_entry_opens_the_guide(self):
        from anno_modding_studio.ui import help_tip

        self.assertTrue(help_tip.GUIDE.is_file())
        with mock.patch.object(help_tip, "open_guide") as opened:
            self.window.guide_action.trigger()
        opened.assert_called_once_with()

    def test_about_does_not_repeat_its_title(self):
        from anno_modding_studio.ui.about_dialog import AboutDialog

        shown = []
        with mock.patch.object(AboutDialog, "exec", lambda dialog: shown.append(dialog)):
            self.window.about_action.trigger()
        dialog = shown[0]
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.windowTitle(), "About Anno Modding Studio")
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertFalse(any("<h3>" in text or text == "Anno Modding Studio" for text in texts))
        self.assertIn("Not affiliated with Ubisoft or mod.io. Anno is a trademark of Ubisoft.", texts)

    def test_about_names_the_version_the_licence_and_where_the_notices_are(self):
        from anno_modding_studio import __version__
        from anno_modding_studio.ui import about_dialog

        dialog = about_dialog.AboutDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.version_label.text(), "Version %s" % __version__)
        self.assertIn("GNU General Public License v3 or later", dialog.licence.text())
        self.assertIn(about_dialog.LICENCE, dialog.licence.text())
        self.assertIn("Copyright © 2026 aleph-shin", dialog.licence.text())
        self.assertIn("ABSOLUTELY NO WARRANTY", dialog.licence.text())
        with mock.patch.object(about_dialog.QMessageBox, "aboutQt") as shown:
            dialog.about_qt_button.click()
        shown.assert_called_once_with(dialog)
        for url in (about_dialog.NOTICES, about_dialog.CREDITS, "https://github.com/aleph-shin/anno-modding-studio"):
            self.assertIn('href="%s"' % url, dialog.links.text())
        self.assertTrue(about_dialog.NOTICES.startswith(about_dialog.DOCUMENTS_URL))

    def test_about_asks_for_its_links_where_every_other_label_is_plain(self):
        # widgets.external_text_stays_plain turns a label left in AutoText
        # into plain text, which would show the links as their markup.
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui import about_dialog

        dialog = about_dialog.AboutDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        for label in (dialog.what, dialog.licence, dialog.links):
            self.assertEqual(label.textFormat(), Qt.TextFormat.RichText)

    def test_copy_version_info_puts_what_a_bug_report_needs_on_the_clipboard(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio import __version__
        from anno_modding_studio.ui.about_dialog import AboutDialog

        dialog = AboutDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        dialog.copy_button.click()
        copied = QApplication.clipboard().text()
        self.assertTrue(copied.startswith("Anno Modding Studio %s\n" % __version__))
        for line in ("Reader version: ", "Data version: ", "Python: ", "Qt: ", "PySide6: ",
                     "lxml: ", "Frozen: no"):
            self.assertIn(line, copied)
        self.assertEqual(dialog.copy_button.text(), "Copied!")


if __name__ == "__main__":
    unittest.main()
