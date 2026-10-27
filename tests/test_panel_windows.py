"""Panels in windows of their own (``ui.panel_windows``): the Mod changes
panel moved out of the main window and back, what the fold has to do with
it, what is remembered between launches, and the two rules the main window
keeps in place of asking which tab is in front.  And the References tab
moved out: the page its tab keeps, the graph and the editor each following
the other while they are side by side, the pin, and the graph's own history.
And the Mod file tab moved out: kept current beside the editor, found in
and shown at a line where it is, and text typed in it and in the XML view
at once.
And the Asset editor moved out: everything that ends in it bringing its
window forward, Undo, the Edit menu, and the graph beside it the other way
round.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMenuBar, QToolButton  # noqa: E402

from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import AIM_MAKE  # noqa: E402
from anno_modding_studio.ui import shortcuts_dialog, theme  # noqa: E402
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402
from anno_modding_studio.ui.navigation import (  # noqa: E402
    TAB_EDIT,
    TAB_FILE,
    TAB_GENERATE,
    TAB_REFS,
    TAB_SOURCE,
)
from anno_modding_studio.ui.panel_windows import (  # noqa: E402
    CHANGES,
    EDITOR,
    FILE,
    REFERENCES,
    AwayPage,
    PanelWindows,
    on_a_screen,
)

_app = QApplication.instance() or QApplication([])

SCREEN = QRect(0, 0, 1920, 1080)
# Where a second monitor to the right of that one would have had it.
GONE = "2400,200,500,700"


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _isolation.forget_settings()


class PanelCase(unittest.TestCase):
    """A window, shown, on one screen of a known size."""

    def setUp(self):
        self.addCleanup(_flush_deletes)
        screens = mock.patch.object(PanelWindows, "_screens", return_value=[SCREEN])
        screens.start()
        self.addCleanup(screens.stop)
        self.window = self.make()

    def make(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        self.addCleanup(window.panels.put_all_back)
        window.resize(1500, 900)
        window.show()
        _app.processEvents()
        return window

    def close(self, window: MainWindow, agreed: bool = True) -> None:
        with mock.patch.object(window.files, "confirm_discard", return_value=agreed):
            window.close()
        _app.processEvents()


class TestOnAScreen(unittest.TestCase):
    def test_a_window_is_found_where_its_middle_is_on_a_screen(self):
        self.assertTrue(on_a_screen(QRect(100, 100, 500, 700), [SCREEN]))
        # Half off the edge is still somewhere it can be reached.
        self.assertTrue(on_a_screen(QRect(1500, 100, 800, 700), [SCREEN]))

    def test_a_window_on_a_screen_that_has_gone_is_not(self):
        self.assertFalse(on_a_screen(QRect(2400, 200, 500, 700), [SCREEN]))
        self.assertTrue(
            on_a_screen(QRect(2400, 200, 500, 700), [SCREEN, QRect(1920, 0, 1920, 1080)])
        )
        self.assertFalse(on_a_screen(QRect(100, 100, 500, 700), []))


class TestModChangesOutAndBack(PanelCase):
    def test_it_goes_to_a_window_of_its_own(self):
        window = self.window
        window.changes_out.setChecked(True)
        _app.processEvents()
        own = window.panels.window_of(CHANGES)
        self.assertTrue(window.panels.is_out(CHANGES))
        self.assertIs(window.changes_panel.window(), own)
        self.assertFalse(own.isHidden())
        self.assertEqual(own.windowTitle(), "Mod changes")
        # It belongs to the main window, which is what keeps it above it.
        self.assertIs(own.parentWidget(), window)

    def test_the_tabs_get_the_whole_width_and_give_it_back(self):
        window = self.window
        before = window.splitter.sizes()
        window.changes_out.setChecked(True)
        _app.processEvents()
        self.assertTrue(window.changes_side.isHidden())
        self.assertEqual(window.splitter.sizes()[1], 0)
        self.assertEqual(window.splitter.sizes()[0], window.splitter.width())
        window.changes_out.setChecked(False)
        _app.processEvents()
        self.assertFalse(window.changes_side.isHidden())
        for was, now in zip(before, window.splitter.sizes(), strict=True):
            self.assertAlmostEqual(was, now, delta=2)

    def test_it_comes_back_to_its_place_under_the_title(self):
        window = self.window
        window.changes_out.setChecked(True)
        window.changes_out.setChecked(False)
        _app.processEvents()
        self.assertFalse(window.panels.is_out(CHANGES))
        self.assertIs(window.changes_panel.window(), window)
        self.assertIs(window.changes_panel.parentWidget(), window.changes_body)
        self.assertEqual(window.changes_body.layout().indexOf(window.changes_panel), 1)
        self.assertTrue(window.changes_panel.isVisible())

    def test_it_is_still_the_windows_panel_while_it_is_out(self):
        window = self.window
        window.changes_out.setChecked(True)
        with mock.patch.object(window.changes_panel, "refresh") as refresh:
            window.on_changes_updated()
        refresh.assert_called_once()
        with mock.patch.object(window.prompts, "ask_unapplied_modops"), \
                mock.patch.object(window.editor, "load_asset", return_value=False) as load:
            window.tabs.setTabEnabled(TAB_EDIT, True)
            with mock.patch.object(window, "update_tab_gates"):
                window.changes_panel.changeActivated.emit("1010340", "", None, None)
        load.assert_called_once_with("1010340")

    def test_closing_its_window_puts_it_back(self):
        window = self.window
        window.changes_out.setChecked(True)
        _app.processEvents()
        own = window.panels.window_of(CHANGES)
        own.close()
        _app.processEvents()
        self.assertFalse(window.panels.is_out(CHANGES))
        self.assertFalse(window.changes_out.isChecked())
        self.assertTrue(own.isHidden())
        self.assertIs(window.changes_panel.window(), window)

    def test_the_button_on_the_panel_moves_it_out_and_is_not_shown_there(self):
        window = self.window
        button = window.changes_move
        self.assertTrue(window.changes_panel.isAncestorOf(button))
        self.assertTrue(button.isVisible())
        self.assertIn("Shift+F9", button.toolTip())
        self.assertEqual(
            button.accessibleName(), "Move Mod changes to a separate window"
        )
        button.click()
        _app.processEvents()
        self.assertTrue(window.panels.is_out(CHANGES))
        # Closing the window is the way back, and the only one in it.
        self.assertTrue(button.isHidden())
        window.panels.window_of(CHANGES).close()
        _app.processEvents()
        self.assertFalse(window.panels.is_out(CHANGES))
        self.assertTrue(button.isVisible())

    def test_the_button_is_drawn_as_the_buttons_beside_it_are(self):
        """With no rule of its own it was a darker square that did nothing
        under the mouse.  The theme has one for it by name, with the border
        that takes the accent there and the mark for the keyboard."""
        button = self.window.changes_move
        self.assertEqual(button.objectName(), "PanelMove")
        css = theme.stylesheet()
        for rule in (
            "QToolButton#PanelMove {", "QToolButton#PanelMove:hover {",
            "QToolButton#PanelMove:focus {",
        ):
            self.assertIn(rule, css)

    def test_it_has_a_key_and_a_place_in_the_view_menu(self):
        window = self.window
        self.assertEqual(window.changes_out.shortcut().toString(), "Shift+F9")
        view = dict(shortcuts_dialog.gather(window))["View"]
        self.assertIn(("Mod changes in a separate window", "Shift+F9"), view)
        self.assertIn(("Mod changes panel", "F9"), view)


class TestTheMainWindowsKeysInAPanelsWindow(PanelCase):
    def test_its_window_answers_them(self):
        window = self.window
        window.changes_out.setChecked(True)
        held = window.panels.window_of(CHANGES).actions()
        for action in (
            window.undo_action, window.redo_action, window.play_action,
            window.changes_out, window.toggle_changes,
        ):
            self.assertIn(action, held)

    def test_a_key_pressed_there_does_what_it_does_in_the_main_window(self):
        window = self.window
        window.changes_out.setChecked(True)
        own = window.panels.window_of(CHANGES)
        window.changes_panel.tree.setFocus()
        self.assertTrue(QTest.qWaitForWindowActive(own, 2000))
        # The key moves the list out of the window it was pressed in, and the
        # release never arrives: Shift would stay down for every test after
        # this one, and a row selected there would be a range.
        self.addCleanup(self.let_go_of_shift)
        QTest.keyClick(
            window.changes_panel.tree, Qt.Key.Key_F9, Qt.KeyboardModifier.ShiftModifier
        )
        _app.processEvents()
        self.assertFalse(window.panels.is_out(CHANGES))

    def let_go_of_shift(self) -> None:
        QTest.keyRelease(self.window, Qt.Key.Key_Shift)
        self.assertEqual(
            QGuiApplication.keyboardModifiers(), Qt.KeyboardModifier.NoModifier
        )

    def test_a_tab_asked_for_from_there_brings_the_main_window_forward(self):
        window = self.window
        window.changes_out.setChecked(True)
        with mock.patch.object(window.panels, "has_keyboard", return_value=True), \
                mock.patch.object(window, "activateWindow") as forward:
            self.assertTrue(window.open_tab(TAB_SOURCE))
        forward.assert_called_once()

    def test_a_tab_asked_for_from_the_main_window_leaves_it_be(self):
        window = self.window
        with mock.patch.object(window, "activateWindow") as forward:
            self.assertTrue(window.open_tab(TAB_SOURCE))
        forward.assert_not_called()


class TestTheFold(PanelCase):
    def test_out_the_fold_is_greyed_and_back_it_is_not(self):
        window = self.window
        window.changes_out.setChecked(True)
        self.assertFalse(window.toggle_changes.isEnabled())
        window.changes_out.setChecked(False)
        self.assertTrue(window.toggle_changes.isEnabled())

    def test_a_folded_panel_taken_out_comes_back_open(self):
        window = self.window
        wide = window.splitter.sizes()[1]
        window.toggle_changes.setChecked(False)
        _app.processEvents()
        self.assertTrue(window.changes_body.isHidden())
        window.changes_out.setChecked(True)
        _app.processEvents()
        self.assertTrue(window.toggle_changes.isChecked())
        self.assertTrue(window.changes_panel.isVisible())
        window.changes_out.setChecked(False)
        _app.processEvents()
        self.assertFalse(window.changes_body.isHidden())
        self.assertTrue(window.changes_strip.isHidden())
        self.assertAlmostEqual(window.splitter.sizes()[1], wide, delta=2)


class TestWhatIsRemembered(PanelCase):
    def test_a_panel_that_was_out_is_put_out_again_where_it_was(self):
        window = self.window
        window.changes_out.setChecked(True)
        _app.processEvents()
        window.panels.window_of(CHANGES).setGeometry(300, 150, 520, 640)
        _app.processEvents()
        self.close(window)
        self.assertEqual(window.settings.value("changesOut"), "yes")
        self.assertEqual(window.settings.value("changesWindow"), "300,150,520,640")
        # It comes back open, so it is not also remembered as folded.
        self.assertEqual(window.settings.value("changesFolded"), "no")
        again = self.make()
        self.assertTrue(again.panels.is_out(CHANGES))
        self.assertEqual(
            again.panels.window_of(CHANGES).geometry(), QRect(300, 150, 520, 640)
        )
        self.assertFalse(again.toggle_changes.isEnabled())

    def test_a_window_that_was_maximised_is_maximised_again(self):
        window = self.window
        window.changes_out.setChecked(True)
        own = window.panels.window_of(CHANGES)
        own.setGeometry(300, 150, 520, 640)
        own.showMaximized()
        _app.processEvents()
        self.close(window)
        self.assertEqual(window.settings.value("changesWindowMaximised"), "yes")
        # Under it is the size it goes back to.
        self.assertEqual(window.settings.value("changesWindow"), "300,150,520,640")
        again = self.make()
        self.assertTrue(again.panels.window_of(CHANGES).isMaximized())

    def test_a_panel_that_was_in_stays_in(self):
        window = self.window
        window.changes_out.setChecked(True)
        window.changes_out.setChecked(False)
        self.close(window)
        self.assertEqual(window.settings.value("changesOut"), "no")
        again = self.make()
        self.assertFalse(again.panels.is_out(CHANGES))

    def test_closing_the_studio_puts_the_panel_back_first(self):
        window = self.window
        before = window.splitter.sizes()
        window.changes_out.setChecked(True)
        _app.processEvents()
        self.close(window)
        self.assertIs(window.changes_panel.window(), window)
        again = self.make()
        again.panels.put_all_back()
        _app.processEvents()
        for was, now in zip(before, again.splitter.sizes(), strict=True):
            self.assertAlmostEqual(was, now, delta=2)

    def test_put_out_at_launch_it_comes_back_at_the_width_it_was_dragged_to(self):
        # H2-29: put out again as the window is shown, the panel's width was
        # read from a splitter not yet laid out at the window's size (a
        # maximised window is not, then), and it came back much narrower.
        wide = mock.patch.object(PanelWindows, "_screens", return_value=[QRect(0, 0, 3000, 1200)])
        wide.start()
        self.addCleanup(wide.stop)
        window = self.window
        window.resize(2600, 900)
        _app.processEvents()
        total = sum(window.splitter.sizes())
        window.splitter.setSizes([total - 1000, 1000])
        _app.processEvents()
        dragged = window.splitter.sizes()[1]
        window.changes_out.setChecked(True)
        _app.processEvents()
        self.close(window)
        again = MainWindow()
        self.addCleanup(again.deleteLater)
        self.addCleanup(again.hide)
        self.addCleanup(again.panels.put_all_back)
        again.resize(1800, 900)
        again.show()
        _app.processEvents()
        self.assertTrue(again.panels.is_out(CHANGES))
        again.resize(2600, 900)
        _app.processEvents()
        again.changes_out.setChecked(False)
        _app.processEvents()
        self.assertAlmostEqual(again.splitter.sizes()[1], dragged, delta=2)

    def test_a_close_that_is_refused_leaves_the_window_open(self):
        window = self.window
        window.changes_out.setChecked(True)
        _app.processEvents()
        self.close(window, agreed=False)
        self.assertTrue(window.panels.is_out(CHANGES))
        self.assertFalse(window.panels.window_of(CHANGES).isHidden())
        self.assertFalse(window.isHidden())

    def test_a_window_on_a_screen_that_has_gone_opens_in_the_main_window(self):
        self.close(self.window)
        settings = self.window.settings
        settings.setValue("changesOut", "yes")
        settings.setValue("changesWindow", GONE)
        again = self.make()
        self.assertFalse(again.panels.is_out(CHANGES))
        self.assertIs(again.changes_panel.window(), again)
        self.assertTrue(again.toggle_changes.isEnabled())
        # And is kept as it was, for the day the screen is back.
        self.close(again)
        self.assertEqual(again.settings.value("changesOut"), "yes")
        self.assertEqual(again.settings.value("changesWindow"), GONE)
        with mock.patch.object(
            PanelWindows, "_screens", return_value=[SCREEN, QRect(1920, 0, 1920, 1080)]
        ):
            back = self.make()
            self.assertTrue(back.panels.is_out(CHANGES))

    def test_taken_out_by_hand_with_the_screen_gone_it_opens_by_the_main_window(self):
        window = self.window
        window.settings.setValue("changesWindow", GONE)
        window.changes_out.setChecked(True)
        _app.processEvents()
        place = window.panels.window_of(CHANGES).geometry()
        self.assertTrue(on_a_screen(place, [window.frameGeometry()]), place)

    def test_moved_by_hand_it_is_no_longer_waiting_for_its_screen(self):
        self.close(self.window)
        self.window.settings.setValue("changesOut", "yes")
        self.window.settings.setValue("changesWindow", GONE)
        again = self.make()
        again.changes_out.setChecked(True)
        again.changes_out.setChecked(False)
        self.close(again)
        self.assertEqual(again.settings.value("changesOut"), "no")


class TestShowingAndBringingForward(PanelCase):
    """The two rules that stand in for the tab's position, with a tab
    said to be out."""

    def out(self, key: str):
        return mock.patch.object(
            self.window.panels, "is_out", side_effect=lambda asked: asked == key
        )

    def test_a_panel_is_showing_as_the_tab_in_front(self):
        window = self.window
        self.assertTrue(window.showing(TAB_SOURCE))
        self.assertFalse(window.showing(TAB_REFS))

    def test_a_panel_is_showing_in_a_window_of_its_own(self):
        window = self.window
        with self.out(EDITOR):
            self.assertTrue(window.showing(TAB_EDIT))
            self.assertFalse(window.showing(TAB_FILE))

    def test_the_graph_follows_the_editor_while_it_is_showing(self):
        window = self.window
        with mock.patch.object(window.references, "show_asset") as shown:
            window._on_editor_opened("1010340")
            shown.assert_not_called()
            self.assertEqual(window.pending_in_refs, "1010340")
            with self.out("references"):
                window._on_editor_opened("1010341")
            shown.assert_called_once_with("1010341")

    def test_going_to_a_panel_that_is_out_brings_its_window_forward(self):
        window = self.window
        window.tabs.setTabEnabled(TAB_REFS, True)
        with mock.patch.object(window, "update_tab_gates"), mock.patch.object(
            window.panels, "bring_forward", side_effect=lambda key: key == "references"
        ) as forward:
            self.assertTrue(window.open_tab(TAB_REFS))
            # The main window's tab is left where it was.
            self.assertEqual(window.tabs.currentIndex(), TAB_SOURCE)
            forward.assert_called_once_with("references")

    def test_a_shut_tab_is_not_gone_to_wherever_its_panel_is(self):
        window = self.window
        with mock.patch.object(
            window.panels, "bring_forward", return_value=True
        ) as forward:
            self.assertFalse(window.open_tab(TAB_REFS))
        forward.assert_not_called()

    def test_bringing_forward_a_panel_that_is_in_does_nothing(self):
        window = self.window
        self.assertFalse(window.panels.bring_forward(CHANGES))
        self.assertFalse(window.panels.bring_forward(None))
        window.changes_out.setChecked(True)
        self.assertTrue(window.panels.bring_forward(CHANGES))


class GraphCase(PanelCase):
    """A window on six assets that each refer to the next, with the tabs'
    gates open."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(6):
            parts.append(
                "<Asset><Template>Link</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>link_%d</Name></Standard>"
                "<Bits><Item><Product>%d</Product></Item></Bits>"
                "</Values></Asset>" % (7300 + i, i, 7300 + (i + 1) % 6)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        super().setUp()
        self.open_gates(self.window)
        self.editor = self.window.editor
        self.graph = self.window.references

    def open_gates(self, window: MainWindow) -> None:
        window.game_files.adopt_index(self.db, self.assets, cached=True)
        window.loadout.aim = AIM_MAKE
        window.update_tab_gates()
        self.addCleanup(self.shut, window)

    @staticmethod
    def shut(window: MainWindow) -> None:
        window.editor.search._debounce.stop()
        window.game_files.stop_texts_worker()
        if window.index is not None:
            window.index.close()
            window.index = None
        window.loadout.clear()

    def take_out(self) -> None:
        self.window.references_out.setChecked(True)
        _app.processEvents()


class TestReferencesOutAndBack(GraphCase):
    def test_it_goes_to_a_window_of_its_own_and_the_tab_stays(self):
        window = self.window
        self.take_out()
        own = window.panels.window_of(REFERENCES)
        self.assertIs(self.graph.window(), own)
        self.assertEqual(own.windowTitle(), "References")
        self.assertEqual(window.tabs.count(), 5)
        self.assertEqual(window.tabs.tabText(TAB_REFS), "References")
        self.assertIsInstance(window.tabs.widget(TAB_REFS), AwayPage)
        self.assertTrue(window.tabs.isTabEnabled(TAB_REFS))
        self.assertTrue(window.showing(TAB_REFS))

    def test_moving_it_changes_nothing_else_about_the_tabs(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        with mock.patch.object(window, "_on_tab_changed") as told:
            self.take_out()
            self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)
            window.references_out.setChecked(False)
            self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)
        told.assert_not_called()
        self.assertIs(window.tabs.widget(TAB_REFS), self.graph)
        self.assertIs(self.graph.window(), window)
        self.assertTrue(window.tabs.isTabEnabled(TAB_GENERATE))

    def test_it_comes_back_in_front_where_its_tab_was_in_front(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)
        away = window.tabs.widget(TAB_REFS)
        self.assertTrue(away.isVisible())
        window.references_out.setChecked(False)
        _app.processEvents()
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)
        self.assertTrue(self.graph.isVisible())
        # And the page it left is not drawn over it.
        self.assertTrue(away.isHidden())

    def test_the_page_it_leaves_says_where_it_is_and_moves_it_back(self):
        window = self.window
        self.take_out()
        away = window.tabs.widget(TAB_REFS)
        said = [label.text() for label in away.findChildren(QLabel)]
        self.assertEqual(said, ["References is in a separate window."])
        away.back_button.click()
        _app.processEvents()
        self.assertFalse(window.panels.is_out(REFERENCES))
        self.assertIs(window.tabs.widget(TAB_REFS), self.graph)

    def test_its_button_is_in_its_top_row_and_it_has_a_key(self):
        window = self.window
        button = next(
            one for one in self.graph.findChildren(QToolButton)
            if one.objectName() == "PanelMove"
        )
        self.assertEqual(
            button.accessibleName(), "Move References to a separate window"
        )
        self.assertEqual(window.references_out.shortcut().toString(), "Ctrl+Shift+4")
        view = dict(shortcuts_dialog.gather(window))["View"]
        self.assertIn(("References in a separate window", "Ctrl+Shift+4"), view)
        button.click()
        _app.processEvents()
        self.assertTrue(window.panels.is_out(REFERENCES))

    def test_going_to_its_tab_brings_its_window_forward(self):
        window = self.window
        self.take_out()
        with mock.patch.object(
            window.panels, "bring_forward", wraps=window.panels.bring_forward
        ) as forward:
            window.tabs.setCurrentIndex(TAB_EDIT)
            window.tabs.setCurrentIndex(TAB_REFS)
        # The keyboard stays with the tabs, which may only be walked along.
        forward.assert_called_with(REFERENCES, keyboard=False)
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)

    def test_references_for_an_asset_moves_the_graph_where_it_is(self):
        window = self.window
        window._open_asset("7300")
        self.take_out()
        with mock.patch.object(
            window.panels, "bring_forward", wraps=window.panels.bring_forward
        ) as forward:
            window.show_references("7303")
        self.assertEqual(self.graph.guid, "7303")
        forward.assert_called_once_with(REFERENCES)
        self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)


class TestTheGate(PanelCase):
    def test_a_tab_that_is_shut_cannot_be_taken_out(self):
        window = self.window
        window.update_tab_gates()
        self.assertFalse(window.tabs.isTabEnabled(TAB_REFS))
        self.assertFalse(window.references_out.isEnabled())

    def test_one_that_is_out_when_its_gate_shuts_can_be_brought_back(self):
        window = self.window
        window.references_out.setChecked(True)
        window.update_tab_gates()
        self.assertFalse(window.tabs.isTabEnabled(TAB_REFS))
        self.assertTrue(window.references_out.isEnabled())
        window.references_out.setChecked(False)
        window.update_tab_gates()
        self.assertFalse(window.references_out.isEnabled())
        self.assertFalse(window.tabs.isTabEnabled(TAB_REFS))

    def test_put_back_with_its_gate_shut_it_cannot_be_taken_out_again(self):
        """The action was greyed again only by the next look at the gates,
        and putting the panel back was not one: checked again, it took the
        panel of a shut tab back out to its window."""
        window = self.window
        window.references_out.setChecked(True)
        window.update_tab_gates()
        window.references_out.setChecked(False)
        self.assertFalse(window.references_out.isEnabled())
        window.references_out.trigger()
        self.assertFalse(window.panels.is_out(REFERENCES))

    def test_it_is_put_out_again_at_launch_whatever_the_gate(self):
        window = self.window
        window.references_out.setChecked(True)
        _app.processEvents()
        self.close(window)
        self.assertEqual(window.settings.value("referencesOut"), "yes")
        again = self.make()
        self.assertTrue(again.panels.is_out(REFERENCES))
        self.assertIsInstance(again.tabs.widget(TAB_REFS), AwayPage)
        # And can be brought back by its key, with its tab shut.
        self.assertFalse(again.tabs.isTabEnabled(TAB_REFS))
        self.assertTrue(again.references_out.isEnabled())

    def test_out_it_is_greyed_and_not_as_its_tab_is(self):
        """Greying a tab greys its page, which a panel that is out is not:
        put out at launch with its tab shut, it stayed grey in its window
        after the tab opened."""
        window = self.window
        window.references_out.setChecked(True)
        _app.processEvents()
        self.close(window)
        again = self.make()
        self.assertFalse(again.references.isEnabled())
        again.loadout.aim = AIM_MAKE
        with mock.patch.object(again, "index", object()):
            again.update_tab_gates()
            self.assertTrue(again.tabs.isTabEnabled(TAB_REFS))
            self.assertTrue(again.references.isEnabled())
        again.loadout.clear()
        again.update_tab_gates()
        self.assertFalse(again.references.isEnabled())
        again.references_out.setChecked(False)
        self.assertFalse(again.references.isEnabled())
        self.assertFalse(again.tabs.isTabEnabled(TAB_REFS))


class TestFollowingSideBySide(GraphCase):
    """With the graph in a window of its own, it and the editor both show."""

    def test_the_graph_follows_the_editor_as_it_moves(self):
        self.window._open_asset("7300")
        self.take_out()
        self.assertEqual(self.graph.guid, "7300")
        self.editor.load_asset("7301")
        self.assertEqual(self.graph.guid, "7301")

    def test_taken_out_it_catches_up_with_the_editor(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7302")
        self.assertIsNone(self.graph.guid)
        self.take_out()
        self.assertEqual(self.graph.guid, "7302")

    def test_the_editor_does_not_follow_the_graph(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.show_asset("7302")
        self.assertEqual(self.editor.guid, "7300")
        self.assertIsNone(self.window.pending_in_editor)
        # Nor when its tab is next come to: it was in view all along.
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7300")

    def test_the_editor_moves_when_it_is_asked_to(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.show_asset("7302")
        self.graph.open_button.click()
        self.assertEqual(self.editor.guid, "7302")
        self.assertEqual(self.graph.guid, "7302")

    def test_with_the_editor_out_of_view_it_follows_on_arrival_as_before(self):
        self.window._open_asset("7300")
        self.take_out()
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.graph.show_asset("7302")
        self.assertEqual(self.window.pending_in_editor, "7302")
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7302")

    def test_moving_it_back_moves_neither_of_them(self):
        window = self.window
        window._open_asset("7300")
        self.take_out()
        self.graph.show_asset("7302")
        window.references_out.setChecked(False)
        self.assertEqual((self.graph.guid, self.editor.guid), ("7302", "7300"))
        # Nor does looking at either afterwards.
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7302")
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7300")

    def test_moved_back_with_its_tab_in_front_it_stays_where_it_was(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.show_asset("7302")
        window.references_out.setChecked(False)
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)
        self.assertEqual(self.graph.guid, "7302")

    def test_back_in_its_tab_each_follows_the_other_from_the_next_move(self):
        window = self.window
        window._open_asset("7300")
        self.take_out()
        self.graph.show_asset("7302")
        window.references_out.setChecked(False)
        self.editor.load_asset("7301")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7301")
        self.graph.show_asset("7303")
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7303")


class TestThePin(GraphCase):
    def test_it_is_offered_only_in_a_window_of_its_own(self):
        self.assertTrue(self.graph.pin_box.isHidden())
        self.assertTrue(self.graph.pin_help.isHidden())
        self.take_out()
        self.assertFalse(self.graph.pin_box.isHidden())
        self.assertFalse(self.graph.pin_help.isHidden())
        self.assertFalse(self.graph.pinned)
        self.window.references_out.setChecked(False)
        self.assertTrue(self.graph.pin_box.isHidden())

    def test_pinned_the_graph_stays_whatever_the_editor_opens(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.editor.load_asset("7301")
        self.assertEqual(self.graph.guid, "7300")
        self.assertIsNone(self.window.pending_in_refs)

    def test_pinned_it_can_still_be_clicked_through(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.graph.show_asset("7304")
        self.assertEqual(self.graph.guid, "7304")
        self.assertEqual(self.editor.guid, "7300")

    def test_unpinned_it_moves_to_the_editors_asset(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.editor.load_asset("7301")
        self.graph.pin_box.setChecked(False)
        self.assertEqual(self.graph.guid, "7301")

    def test_moved_back_it_is_no_longer_pinned_and_stays_on_its_asset(self):
        """It went to the asset the editor had open, and the window put
        back was the one thing that had sent it there."""
        self.window._open_asset("7300")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.graph.assetActivated.emit("7301")  # Edit, on a neighbour's menu
        self.assertEqual((self.graph.guid, self.editor.guid), ("7300", "7301"))
        self.window.references_out.setChecked(False)
        self.assertFalse(self.graph.pinned)
        self.assertEqual(self.graph.guid, "7300")
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7300")
        # The next asset opened in the editor is followed, as ever.
        self.editor.load_asset("7302")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7302")

    def test_it_is_not_remembered_between_launches(self):
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.close(self.window)
        again = self.make()
        self.assertTrue(again.panels.is_out(REFERENCES))
        self.assertFalse(again.references.pinned)

    def test_its_help_says_what_it_does(self):
        self.assertEqual(self.graph.pin_box.text(), "Pin to this asset")
        self.assertIn("keep the graph on its asset", self.graph.pin_help.accessibleDescription())


class TestTheGraphsOwnHistory(GraphCase):
    def test_in_its_tab_it_walks_the_editors_history(self):
        self.assertIs(self.graph.trail, self.window.trail)
        self.assertIs(self.graph.nav.trail, self.window.trail)

    def test_out_it_walks_one_of_its_own_from_where_it_is(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.take_out()
        self.assertIsNot(self.graph.trail, self.window.trail)
        self.assertIs(self.graph.nav.trail, self.graph.trail)
        self.assertEqual(self.graph.trail.current, "7301")
        self.assertFalse(self.graph.back_button.isEnabled())

    def test_back_in_the_graph_leaves_the_editors_history_alone(self):
        self.window._open_asset("7300")
        self.take_out()
        self.graph.show_asset("7302")
        self.graph.show_asset("7303")
        self.assertEqual(self.window.trail.current, "7300")
        self.assertFalse(self.editor.nav.back_button.isEnabled())
        self.assertTrue(self.graph.back_button.isEnabled())
        self.graph.go_back()
        self.assertEqual(self.graph.guid, "7302")
        self.assertEqual(self.editor.guid, "7300")
        self.assertEqual(self.window.trail.current, "7300")

    def test_back_in_the_editor_takes_the_graph_with_it(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.take_out()
        self.editor.go_back()
        self.assertEqual(self.editor.guid, "7300")
        self.assertEqual(self.graph.guid, "7300")

    def test_moved_back_it_takes_up_the_editors_history_again(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.take_out()
        self.graph.show_asset("7304")
        self.window.references_out.setChecked(False)
        self.assertIs(self.graph.trail, self.window.trail)
        self.assertIs(self.graph.nav.trail, self.window.trail)
        self.assertTrue(self.graph.back_button.isEnabled())
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.graph.go_back()
        self.assertEqual(self.graph.guid, "7300")

    def test_a_loadout_closed_clears_both(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.take_out()
        self.window.close_asset()
        self.assertIsNone(self.window.trail.current)
        self.assertIsNone(self.graph.trail.current)
        self.assertFalse(self.editor.nav.back_button.isEnabled())


HUT = "1010340"
CYCLE = (
    '<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/CycleTime">'
    "<CycleTime>%s</CycleTime></ModOp>"
)


def document(*ops: str) -> str:
    return "<ModOps>\n%s\n</ModOps>\n" % "\n".join(ops)


class FileCase(PanelCase):
    """A window on a small set of game files, with a mod of your own and an
    asset open in the editor."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        super().setUp()
        window = self.window
        window.game_files.adopt_index(self.db, self.assets, cached=True)
        window.loadout.aim = AIM_MAKE
        # Somebody who has said who they are, as the card asks as it starts:
        # an edit into your own mod asks otherwise.
        window.settings.setValue(window.preferences.CREATOR_KEY, "Me")
        window.loadout.make_own_mod()
        window.update_tab_gates()
        self.addCleanup(GraphCase.shut, window)
        self.panel = window.file_panel
        self.editor = window.editor
        window._open_asset(HUT)

    def take_out(self) -> None:
        self.window.file_out.setChecked(True)
        _app.processEvents()

    def type(self, text: str) -> None:
        """Put ``text`` in the file as somebody typing it would."""
        cursor = self.panel.pane.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def apply(self, text: str) -> None:
        self.panel.refresh()
        self.type(text)
        self.panel.apply_button.click()

    def cycle_row(self):
        match = Qt.MatchFlag.MatchRecursive | Qt.MatchFlag.MatchExactly
        return self.editor.tree.findItems("CycleTime", match, 0)[0]

    def edit(self, value: str) -> None:
        """Set the hut's cycle time in the editor's tree."""
        self.cycle_row().setText(1, value)
        _app.processEvents()

    def file(self) -> str:
        return self.panel.pane.toPlainText()


class TestModFileOutAndBack(FileCase):
    def test_it_goes_to_a_window_of_its_own_and_the_tab_stays(self):
        window = self.window
        self.take_out()
        own = window.panels.window_of(FILE)
        self.assertIs(self.panel.window(), own)
        self.assertEqual(own.windowTitle(), "Mod file")
        self.assertIsInstance(window.tabs.widget(TAB_FILE), AwayPage)
        self.assertEqual(window.tabs.tabText(TAB_FILE), "Mod file")
        window.file_out.setChecked(False)
        self.assertIs(window.tabs.widget(TAB_FILE), self.panel)

    def test_it_has_a_key_a_button_and_a_place_in_the_view_menu(self):
        window = self.window
        self.assertEqual(window.file_out.shortcut().toString(), "Ctrl+Shift+3")
        view = dict(shortcuts_dialog.gather(window))["View"]
        self.assertIn(("Mod file in a separate window", "Ctrl+Shift+3"), view)
        button = next(
            one for one in self.panel.findChildren(QToolButton)
            if one.objectName() == "PanelMove"
        )
        self.assertEqual(button.accessibleName(), "Move Mod file to a separate window")
        button.click()
        _app.processEvents()
        self.assertTrue(window.panels.is_out(FILE))

    def test_it_is_remembered(self):
        self.take_out()
        self.close(self.window)
        self.assertEqual(self.window.settings.value("fileOut"), "yes")
        again = self.make()
        self.assertTrue(again.panels.is_out(FILE))


class TestTheFileBesideTheEditor(FileCase):
    def test_out_it_is_drawn_again_at_every_change(self):
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        self.assertNotIn("CycleTime", self.file())
        self.edit("44")
        self.assertIn("<CycleTime>44</CycleTime>", self.file())
        self.edit("45")
        self.assertIn("<CycleTime>45</CycleTime>", self.file())
        self.window.undo_last()
        self.assertIn("<CycleTime>44</CycleTime>", self.file())

    def test_in_its_tab_behind_another_it_waits_to_be_looked_at(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        with mock.patch.object(self.panel, "refresh", wraps=self.panel.refresh) as drawn:
            self.edit("44")
            drawn.assert_not_called()
            window.tabs.setCurrentIndex(TAB_FILE)
            drawn.assert_called()
        self.assertIn("<CycleTime>44</CycleTime>", self.file())

    def test_moved_back_behind_another_tab_it_waits_again(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        window.file_out.setChecked(False)
        with mock.patch.object(self.panel, "refresh") as drawn:
            self.edit("44")
        drawn.assert_not_called()

    def test_an_apply_in_the_file_is_shown_in_the_editor_beside_it(self):
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        self.apply(document(CYCLE % "77"))
        self.assertEqual(self.cycle_row().text(1), "77")

    def test_an_apply_in_the_file_is_shown_in_the_editor_when_it_is_come_back_to(self):
        """It was not: the editor went on showing the asset as it was
        before the Apply, in its tab as well."""
        window = self.window
        window.tabs.setCurrentIndex(TAB_FILE)
        self.apply(document(CYCLE % "77"))
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.cycle_row().text(1), "77")

    def test_show_in_file_goes_to_the_line_in_its_window(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.edit("44")
        self.take_out()
        change = next(iter(window.loadout))
        with mock.patch.object(
            window.panels, "bring_forward", wraps=window.panels.bring_forward
        ) as forward:
            window.show_in_file(change.origin, "change", change.key)
        forward.assert_called_once_with(FILE)
        self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)
        self.assertIn("CycleTime", self.panel.pane.textCursor().block().text())


class TestFindWhereTheKeyboardIs(FileCase):
    def focus_in(self, widget):
        return mock.patch.object(QApplication, "focusWidget", return_value=widget)

    def test_in_the_file_in_its_window_it_finds_in_the_file(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        with self.focus_in(self.panel.pane), \
                mock.patch.object(self.editor, "find_in_asset") as in_asset:
            window._find_in_asset()
        in_asset.assert_not_called()
        self.assertFalse(self.panel.find_bar.isHidden())
        self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)

    def test_in_the_editor_it_finds_in_the_asset_with_the_file_beside_it(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        with self.focus_in(self.editor.tree), \
                mock.patch.object(self.editor, "find_in_asset", return_value=True) as in_asset:
            window._find_in_asset()
        in_asset.assert_called_once()
        self.assertTrue(self.panel.find_bar.isHidden())

    def test_in_neither_it_goes_by_the_main_windows_tab(self):
        window = self.window
        elsewhere = window.changes_panel.tree
        window.tabs.setCurrentIndex(TAB_FILE)
        with self.focus_in(elsewhere), \
                mock.patch.object(self.editor, "find_in_asset", return_value=True) as in_asset:
            window._find_in_asset()
            in_asset.assert_not_called()
            self.assertFalse(self.panel.find_bar.isHidden())
            # The file's tab in front says only where the file has gone.
            self.panel.find_bar.hide()
            self.take_out()
            window._find_in_asset()
            in_asset.assert_called_once()
        self.assertTrue(self.panel.find_bar.isHidden())
        self.assertEqual(window.tabs.currentIndex(), TAB_EDIT)


class TestTypingInBothAtOnce(FileCase):
    """The file and the XML view each hold text typed and not applied, and
    side by side both can.  Neither loses what was typed to the other."""

    def setUp(self):
        super().setUp()
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.take_out()
        self.apply(document(CYCLE % "15"))

    def test_an_edit_in_the_editor_leaves_what_is_typed_in_the_file(self):
        self.type(document(CYCLE % "18"))
        self.edit("21")
        self.assertTrue(self.panel.has_unapplied())
        self.assertIn("<CycleTime>18</CycleTime>", self.file())
        self.assertIn("The mod changed while you were typing", self.panel.status.text())

    def test_an_apply_in_the_file_leaves_what_is_typed_in_the_xml_view(self):
        xml = self.editor.xml
        self.editor.set_view("xml")
        cursor = xml.typing.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(CYCLE % "18")
        self.assertTrue(xml.has_unapplied())
        self.apply(document(CYCLE % "33"))
        self.assertTrue(xml.has_unapplied())
        self.assertIn("<CycleTime>18</CycleTime>", xml.typing.toPlainText())
        self.assertIn("changed while you were typing", xml.status.text())
        # And the file is the mod's again, with what it applied.
        self.assertFalse(self.panel.has_unapplied())
        self.assertIn("<CycleTime>33</CycleTime>", self.file())

    def test_an_apply_in_the_xml_view_leaves_what_is_typed_in_the_file(self):
        xml = self.editor.xml
        self.editor.set_view("xml")
        self.type(document(CYCLE % "18"))
        cursor = xml.typing.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(CYCLE % "33")
        xml.apply_button.click()
        _app.processEvents()
        self.assertFalse(xml.has_unapplied())
        self.assertTrue(self.panel.has_unapplied())
        self.assertIn("<CycleTime>18</CycleTime>", self.file())
        self.assertIn("The mod changed while you were typing", self.panel.status.text())


SAWMILL = "1010341"


class TestAssetEditorOutAndBack(FileCase):
    def take_out(self) -> None:
        self.window.editor_out.setChecked(True)
        _app.processEvents()

    def test_it_goes_to_a_window_of_its_own_and_the_tab_stays(self):
        window = self.window
        self.take_out()
        own = window.panels.window_of(EDITOR)
        self.assertIs(self.editor.window(), own)
        self.assertEqual(own.windowTitle(), "Asset editor")
        self.assertEqual(window.tabs.count(), 5)
        self.assertIsInstance(window.tabs.widget(TAB_EDIT), AwayPage)
        self.assertEqual(window.tabs.tabText(TAB_EDIT), "Asset editor")
        self.assertTrue(window.showing(TAB_EDIT))
        # The asset it had open is the asset it has open.
        self.assertEqual(self.editor.guid, HUT)
        window.editor_out.setChecked(False)
        self.assertIs(window.tabs.widget(TAB_EDIT), self.editor)
        self.assertIs(self.editor.window(), window)
        self.assertEqual(self.editor.guid, HUT)

    def test_it_has_a_key_a_button_and_a_place_in_the_view_menu(self):
        window = self.window
        self.assertEqual(window.editor_out.shortcut().toString(), "Ctrl+Shift+2")
        view = dict(shortcuts_dialog.gather(window))["View"]
        self.assertIn(("Asset editor in a separate window", "Ctrl+Shift+2"), view)
        button = next(
            one for one in self.editor.findChildren(QToolButton)
            if one.objectName() == "PanelMove"
        )
        self.assertEqual(
            button.accessibleName(), "Move Asset editor to a separate window"
        )
        button.click()
        _app.processEvents()
        self.assertTrue(window.panels.is_out(EDITOR))

    def test_it_is_remembered(self):
        self.take_out()
        self.close(self.window)
        self.assertEqual(self.window.settings.value("editorOut"), "yes")
        again = self.make()
        self.assertTrue(again.panels.is_out(EDITOR))

    def test_the_search_and_its_list_go_with_it(self):
        window = self.window
        self.take_out()
        own = window.panels.window_of(EDITOR)
        self.assertIs(self.editor.search.window(), own)
        # The list floats over the editor itself, and not over a window.
        self.assertIs(self.editor.search.results.parentWidget(), self.editor)

    def test_an_edit_made_there_is_an_edit(self):
        window = self.window
        self.take_out()
        self.edit("44")
        self.assertEqual(len(list(window.loadout)), 1)
        self.assertTrue(window.undo_action.isEnabled())

    def test_every_tab_out_leaves_start_and_export(self):
        window = self.window
        self.take_out()
        window.file_out.setChecked(True)
        window.references_out.setChecked(True)
        _app.processEvents()
        self.assertEqual(window.tabs.count(), 5)
        for tab in (TAB_EDIT, TAB_FILE, TAB_REFS):
            self.assertIsInstance(window.tabs.widget(tab), AwayPage)
        self.assertTrue(window.open_tab(TAB_GENERATE))
        self.assertEqual(window.tabs.currentIndex(), TAB_GENERATE)


class TestWhatEndsInTheEditor(FileCase):
    """Everything that ends in the editor brings its window forward, and
    leaves the main window's tab where it was."""

    def setUp(self):
        super().setUp()
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window.editor_out.setChecked(True)
        _app.processEvents()
        forward = mock.patch.object(
            self.window.panels, "bring_forward", wraps=self.window.panels.bring_forward
        )
        self.forward = forward.start()
        self.addCleanup(forward.stop)

    def arrived(self) -> None:
        self.forward.assert_called_with(EDITOR)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_GENERATE)

    def test_an_asset_opened(self):
        self.window._open_asset(SAWMILL)
        self.assertEqual(self.editor.guid, SAWMILL)
        self.arrived()

    def test_a_double_click_on_a_change(self):
        self.edit("44")
        self.editor.load_asset(SAWMILL)
        self.window.changes_panel.changeActivated.emit(
            HUT, "/Values/Factory7/CycleTime", None, None
        )
        self.assertEqual(self.editor.guid, HUT)
        self.assertEqual(self.editor.tree.currentItem().text(0), "CycleTime")
        self.arrived()

    def test_the_search_for_an_asset(self):
        with mock.patch.object(self.editor, "focus_search") as search:
            self.window._focus_search()
        search.assert_called_once_with(show_results=True)
        self.arrived()

    def test_edit_this_asset_in_the_graph(self):
        self.window.references.assetActivated.emit(SAWMILL)
        self.assertEqual(self.editor.guid, SAWMILL)
        self.arrived()

    def test_find_with_the_keyboard_nowhere_in_particular(self):
        with mock.patch.object(self.editor, "find_in_asset", return_value=True) as find:
            self.window._find_in_asset()
        find.assert_called_once()
        self.arrived()

    def test_its_tab_clicked_brings_it_forward_and_keeps_the_keyboard(self):
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.forward.assert_called_with(EDITOR, keyboard=False)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertEqual(self.editor.guid, HUT)


class TestUndoWithTheEditorOut(FileCase):
    def test_in_its_tab_behind_another_undo_leaves_the_editor_where_it_is(self):
        window = self.window
        self.edit("44")
        self.editor.load_asset(SAWMILL)
        window.tabs.setCurrentIndex(TAB_GENERATE)
        window.undo_last()
        self.assertEqual(self.editor.guid, SAWMILL)

    def test_out_it_moves_to_the_asset_of_the_step_undone(self):
        """An editor that is out is beside whatever is being looked at, so
        nobody is pulled away from it."""
        window = self.window
        self.edit("44")
        self.editor.load_asset(SAWMILL)
        window.tabs.setCurrentIndex(TAB_GENERATE)
        window.editor_out.setChecked(True)
        window.undo_last()
        self.assertEqual(self.editor.guid, HUT)
        self.assertEqual(self.cycle_row().text(1), "30.00")
        self.assertEqual(window.tabs.currentIndex(), TAB_GENERATE)
        window.redo_last()
        self.assertEqual(self.cycle_row().text(1), "44")

    def test_the_keys_reach_undo_from_a_text_box_in_a_panels_window(self):
        """A text box with nothing to undo leaves Ctrl+Z to the window
        (H2-26), and did so only in the main window: from the Mod changes
        panel's filter in a window of its own the key did nothing."""
        window = self.window
        self.edit("44")
        self.assertEqual(len(window.loadout), 1)
        window.changes_out.setChecked(True)
        own = window.panels.window_of(CHANGES)
        box = window.changes_panel.filter_box
        box.setText("Cycle")  # set, not typed: nothing of its own to undo
        box.setFocus()
        self.assertTrue(QTest.qWaitForWindowActive(own, 2000))
        _app.processEvents()
        self.assertIs(QApplication.focusWidget(), box)
        QTest.keyClick(box, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.loadout), 0)
        QTest.keyClick(box, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.loadout), 1)


class TestTheEditMenuWithTheEditorOut(FileCase):
    def offered(self):
        self.window._populate_edit_menu()
        return [action.text() for action in self.window.edit_menu.actions()]

    def test_it_is_filled_from_the_row_selected_in_the_editors_window(self):
        window = self.window
        self.editor.tree.setCurrentItem(self.cycle_row())
        inside = self.offered()
        window.editor_out.setChecked(True)
        _app.processEvents()
        self.assertEqual(self.offered(), inside)
        self.assertNotIn("Select a field to edit…", inside)
        # And from another row picked over there.
        self.editor.tree.setCurrentItem(self.cycle_row().parent())
        self.assertNotEqual(self.offered(), inside)

    def test_the_menu_stays_in_the_main_window(self):
        window = self.window
        window.editor_out.setChecked(True)
        own = window.panels.window_of(EDITOR)
        self.assertEqual(own.findChildren(QMenuBar), [])
        # Undo and Redo are answered in the editor's window as in this one.
        self.assertIn(window.undo_action, own.actions())
        self.assertIn(window.redo_action, own.actions())


class TestTheGraphInAndTheEditorOut(GraphCase):
    """What phase 2 built with the graph out holds the other way round."""

    def take_out(self) -> None:
        self.window.editor_out.setChecked(True)
        _app.processEvents()

    def test_the_graph_follows_the_editor_while_its_tab_is_in_front(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.assertEqual(self.graph.guid, "7300")
        self.editor.load_asset("7301")
        self.assertEqual(self.graph.guid, "7301")

    def test_behind_another_tab_it_follows_on_arrival_as_before(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_GENERATE)
        self.take_out()
        self.editor.load_asset("7301")
        self.assertIsNone(self.graph.guid)
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7301")

    def test_the_editor_does_not_follow_the_graph(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.show_asset("7302")
        self.assertEqual(self.editor.guid, "7300")
        self.assertIsNone(window.pending_in_editor)

    def test_the_editor_moves_when_it_is_asked_to(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.show_asset("7302")
        self.graph.open_button.click()
        self.assertEqual(self.editor.guid, "7302")
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)

    def test_references_for_another_asset_leaves_the_editor_on_its_own(self):
        """The graph is moved before its tab is in front.  What was kept
        for the editor then waited until it was moved back and its tab
        come to, and moved it then."""
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_GENERATE)
        self.take_out()
        window.show_references("7303")
        self.assertEqual(window.tabs.currentIndex(), TAB_REFS)
        self.assertEqual((self.graph.guid, self.editor.guid), ("7303", "7300"))
        self.assertIsNone(window.pending_in_editor)
        window.editor_out.setChecked(False)
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7300")

    def test_taken_out_it_catches_up_with_the_graph(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.graph.show_asset("7302")
        self.assertEqual(window.pending_in_editor, "7302")
        self.take_out()
        self.assertEqual(self.editor.guid, "7302")

    def test_moving_it_back_moves_neither_of_them(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.show_asset("7302")
        window.editor_out.setChecked(False)
        self.assertEqual((self.graph.guid, self.editor.guid), ("7302", "7300"))
        window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7302")

    def test_moved_back_the_graph_is_still_owed_what_was_opened_behind_it(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_GENERATE)
        self.take_out()
        self.editor.load_asset("7301")
        window.editor_out.setChecked(False)
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.graph.guid, "7301")

    def test_the_pin_is_offered_and_holds_the_graph(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.assertTrue(self.graph.pin_box.isHidden())
        self.take_out()
        self.assertFalse(self.graph.pin_box.isHidden())
        self.graph.pin_box.setChecked(True)
        self.editor.load_asset("7301")
        self.assertEqual(self.graph.guid, "7300")
        self.graph.pin_box.setChecked(False)
        self.assertEqual(self.graph.guid, "7301")

    def test_moved_back_the_pin_goes_and_the_graph_stays(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.graph.pin_box.setChecked(True)
        self.editor.load_asset("7301")
        window.editor_out.setChecked(False)
        self.assertTrue(self.graph.pin_box.isHidden())
        self.assertFalse(self.graph.pinned)
        self.assertEqual(self.graph.guid, "7300")

    def test_the_graph_walks_a_history_of_its_own(self):
        window = self.window
        window._open_asset("7300")
        window.tabs.setCurrentIndex(TAB_REFS)
        self.take_out()
        self.assertIsNot(self.graph.trail, window.trail)
        self.graph.show_asset("7302")
        self.graph.go_back()
        self.assertEqual(self.graph.guid, "7300")
        self.assertEqual(window.trail.current, "7300")
        window.editor_out.setChecked(False)
        self.assertIs(self.graph.trail, window.trail)

    def test_with_both_out_one_moved_back_leaves_them_apart(self):
        window = self.window
        window._open_asset("7300")
        self.take_out()
        window.references_out.setChecked(True)
        self.graph.pin_box.setChecked(True)
        own = self.graph.trail
        for back in (window.editor_out, window.references_out):
            back.setChecked(False)
            self.assertTrue(self.graph.pinned)
            self.assertFalse(self.graph.pin_box.isHidden())
            self.assertIs(self.graph.trail, own)
            back.setChecked(True)
        window.editor_out.setChecked(False)
        window.references_out.setChecked(False)
        self.assertFalse(self.graph.pinned)
        self.assertIs(self.graph.trail, window.trail)


class TestFindWithTheEditorOut(FileCase):
    def test_in_the_editor_in_its_window_it_finds_in_the_asset(self):
        window = self.window
        window.tabs.setCurrentIndex(TAB_FILE)
        window.editor_out.setChecked(True)
        with mock.patch.object(QApplication, "focusWidget", return_value=self.editor.tree), \
                mock.patch.object(self.editor, "find_in_asset", return_value=True) as find:
            window._find_in_asset()
        find.assert_called_once()
        self.assertTrue(self.panel.find_bar.isHidden())
        self.assertEqual(window.tabs.currentIndex(), TAB_FILE)


if __name__ == "__main__":
    unittest.main()
