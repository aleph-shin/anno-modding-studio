"""The status bar's recent messages: what it said before the line it shows
now, the button that lists them and the count on it.  See
``ui/recent_messages.py`` and ``ui.main_window.StatusLine``."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from anno_modding_studio.ui import recent_messages  # noqa: E402
from anno_modding_studio.ui.recent_messages import (  # noqa: E402
    MessageHistory,
    RecentMessagesDialog,
    Said,
)

_app = QApplication.instance() or QApplication([])


class _Clock:
    """A clock the test moves on a second at a time."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        self.now += 1
        return self.now


class TestTheHistory(unittest.TestCase):
    def setUp(self):
        self.history = MessageHistory(kept=5, clock=_Clock())

    def texts(self):
        return [said.text for said in self.history.entries()]

    def test_the_newest_comes_first(self):
        for text in ("One.", "Two.", "Three."):
            self.history.add(text)
        self.assertEqual(self.texts(), ["Three.", "Two.", "One."])

    def test_it_keeps_so_many_and_lets_the_oldest_go(self):
        for n in range(8):
            self.history.add("Message %d." % n)
        self.assertEqual(self.texts(), ["Message %d." % n for n in (7, 6, 5, 4, 3)])

    def test_the_same_words_again_are_one_message_at_the_later_time(self):
        self.history.add("Exported.")
        first = self.history.entries()[0].when
        self.history.add("Exported.")
        self.assertEqual(self.texts(), ["Exported."])
        self.assertGreater(self.history.entries()[0].when, first)
        self.assertEqual(self.history.unseen, 1)

    def test_the_same_words_after_others_are_a_message_again(self):
        for text in ("Exported.", "Read.", "Exported."):
            self.history.add(text)
        self.assertEqual(self.texts(), ["Exported.", "Read.", "Exported."])

    def test_an_empty_line_is_not_a_message(self):
        self.history.add("Exported.")
        self.history.add("")
        self.history.add("Exported.")
        self.assertEqual(self.texts(), ["Exported."])

    def test_a_folder_named_is_kept_with_its_message(self):
        self.history.add("Exported.", "C:/exports/Better Bricks")
        self.history.add("Read.")
        exported = self.history.entries()[1]
        self.assertEqual(exported.folder, Path("C:/exports/Better Bricks"))
        self.assertIsNone(self.history.entries()[0].folder)

    def test_counts_what_came_since_it_was_seen_no_more_than_it_keeps(self):
        for n in range(3):
            self.history.add("Message %d." % n)
        self.assertEqual(self.history.unseen, 3)
        self.history.seen()
        self.assertEqual(self.history.unseen, 0)
        for n in range(9):
            self.history.add("Later %d." % n)
        self.assertEqual(self.history.unseen, 5)


class TestTheTime(unittest.TestCase):
    def test_today_is_the_time_to_the_second(self):
        when = time.mktime((2026, 11, 20, 14, 3, 21, 0, 0, -1))
        self.assertEqual(recent_messages.shown_time(when, now=when + 60), "14:03:21")

    def test_a_day_before_says_the_day(self):
        when = time.mktime((2026, 11, 19, 23, 59, 2, 0, 0, -1))
        shown = recent_messages.shown_time(when, now=when + 3600)
        self.assertIn("19", shown)
        self.assertTrue(shown.endswith("23:59:02"), shown)


class TestTheList(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "Better Bricks"
        now = time.time()
        self.said = [
            Said("Every ModOp comes to what it did.", when=now),
            Said("Exported Better Bricks to %s." % self.folder, self.folder, now - 5),
        ]
        self.dialog = RecentMessagesDialog(self.said)
        self.addCleanup(self.dialog.deleteLater)

    def shown(self):
        return [label.text().replace("\u200b", "") for label in self.dialog.findChildren(QLabel)]

    def test_shows_every_message_whole(self):
        shown = self.shown()
        for said in self.said:
            self.assertIn(said.text, shown)

    def test_a_message_that_named_a_folder_has_the_way_there_again(self):
        self.assertEqual(len(self.dialog.links), 1)
        link = self.dialog.links[0]
        self.assertEqual(link.accessibleName(), "Show in folder")
        self.assertEqual(link.toolTip(), str(self.folder))
        with mock.patch.object(recent_messages, "show_in_folder") as shown:
            link.linkActivated.emit("link")
        shown.assert_called_once_with(self.folder)

    def test_copy_all_copies_each_with_its_time_and_folder(self):
        self.dialog.copy_all()
        copied = QGuiApplication.clipboard().text()
        lines = copied.splitlines()
        self.assertTrue(lines[0].endswith("  Every ModOp comes to what it did."), lines)
        self.assertIn("    %s" % self.folder, lines)

    def test_with_nothing_said_it_says_what_belongs_there(self):
        empty = RecentMessagesDialog([])
        self.addCleanup(empty.deleteLater)
        self.assertFalse(empty.copy_button.isEnabled())
        self.assertIn("The messages the status bar shows are kept here, the newest first.",
                      [label.text() for label in empty.findChildren(QLabel)])


class TestTheStatusLine(unittest.TestCase):
    """The line keeps what it showed, and its button lists it."""

    def setUp(self):
        from anno_modding_studio.ui.main_window import StatusLine

        self.line = StatusLine("Ready.")
        self.addCleanup(self.line.row.deleteLater)
        self.addCleanup(self.line.history_button.deleteLater)

    def test_every_message_shown_is_kept_and_the_first_too(self):
        self.line.say("Exported.", "C:/exports")
        self.line.setText("Every ModOp comes to what it did.")
        said = self.line.history.entries()
        self.assertEqual([entry.text for entry in said],
                         ["Every ModOp comes to what it did.", "Exported.", "Ready."])
        self.assertEqual(said[1].folder, Path("C:/exports"))

    def test_the_button_counts_what_came_since_the_list_was_opened(self):
        self.line.say("Exported.")
        self.line.say("Read.")
        button = self.line.history_button
        self.assertTrue(button.text().endswith(" 3"), button.text())
        self.assertIn("3 new", button.toolTip())
        with mock.patch.object(RecentMessagesDialog, "exec") as shown:
            button.click()
        shown.assert_called_once()
        self.assertEqual(button.text(), recent_messages.GLYPH)
        self.assertEqual(self.line.history.unseen, 0)
        self.line.say("Later.")
        self.assertTrue(button.text().endswith(" 1"), button.text())

    def test_the_list_opened_holds_the_messages_newest_first(self):
        self.line.say("Exported.", "C:/exports")
        made = []
        real = RecentMessagesDialog.__init__

        def keep(dialog, said, parent=None):
            made.append(list(said))
            real(dialog, said, parent)

        with mock.patch.object(RecentMessagesDialog, "__init__", keep), \
                mock.patch.object(RecentMessagesDialog, "exec"):
            self.line.show_history()
        self.assertEqual([entry.text for entry in made[0]], ["Exported.", "Ready."])
        self.assertEqual(made[0][0].folder, Path("C:/exports"))


class TestTheWindow(unittest.TestCase):
    def setUp(self):
        from anno_modding_studio.ui.main_window import MainWindow

        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.deleteLater)
        self.window.editor.search._debounce.stop()

    def test_the_button_is_at_the_status_bars_left_end(self):
        button = self.window.status_label.history_button
        self.assertIs(button.parentWidget(), self.window.statusBar())
        self.window.resize(1200, 700)
        self.window.show()
        self.addCleanup(self.window.hide)
        _app.processEvents()
        self.assertLess(button.geometry().right(), self.window.status_label.row.geometry().left())
        # The row still asks for no width: the button stands outside it.
        self.assertEqual(self.window.status_label.row.minimumSizeHint().width(), 0)

    def test_the_view_menu_and_its_keys_open_the_list(self):
        action = self.window.messages_action
        self.assertEqual(action.shortcut(), recent_messages.SHORTCUT)
        self.window.status_label.setText("Exported.")
        with mock.patch.object(RecentMessagesDialog, "exec") as shown:
            action.trigger()
        shown.assert_called_once()
        self.assertEqual(self.window.status_label.history.unseen, 0)

    def test_the_keyboard_shortcuts_list_has_it(self):
        from anno_modding_studio.ui import shortcuts_dialog

        rows = [row for _where, rows in shortcuts_dialog.gather(self.window) for row in rows]
        self.assertIn("Recent messages", [what for what, _keys in rows])


if __name__ == "__main__":
    unittest.main()
