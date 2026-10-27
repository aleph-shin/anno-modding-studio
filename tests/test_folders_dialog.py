"""Studio folders: every folder the Studio uses, in one list, and the
status bar's way back to the folder a message named.  See
``ui/folders_dialog.py`` and ``ui.main_window.StatusLine``."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.core import places  # noqa: E402
from anno_modding_studio.core.games import ANNO_117  # noqa: E402
from anno_modding_studio.ui import (  # noqa: E402
    folders_dialog,
    help_tip,
    where,
    widgets,
)
from anno_modding_studio.ui.where import Place  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _Folders(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def folder(self, *parts) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path


class TestHowAPathIsShown(_Folders):
    """Each of the Studio's folders starts the same long way into its full
    spelling; the part after that is what tells them apart."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {
            places.ENV_DOCUMENTS: str(self.root / "Documents"),
            "LOCALAPPDATA": str(self.root / "Local"),
        })
        patch.start()
        self.addCleanup(patch.stop)

    def test_from_documents(self):
        self.assertEqual(
            folders_dialog.shown(self.root / "Documents" / "AnnoModdingStudio" / "loadouts"),
            os.path.join("Documents", "AnnoModdingStudio", "loadouts"),
        )

    def test_from_the_local_app_data(self):
        self.assertEqual(
            folders_dialog.shown(self.root / "Local" / "AnnoModdingStudio" / "settings.ini"),
            os.path.join("AppData", "Local", "AnnoModdingStudio", "settings.ini"),
        )

    def test_whole_anywhere_else(self):
        elsewhere = Path("E:/Mods/library")
        self.assertEqual(folders_dialog.shown(elsewhere), str(elsewhere))


class TestTheList(_Folders):
    def setUp(self):
        super().setUp()
        self.loadouts = self.folder("loadouts")
        self.library = self.folder("library")
        self.cache = self.folder("cache")
        self.settings = self.root / "settings.ini"
        self.settings.write_text("", encoding="utf-8")
        self.found = [
            Place(where.LOADOUTS, self.loadouts),
            Place(where.EXPORTS, self.root / "exports"),
            Place(where.LIBRARY, self.library, chosen=True),
            Place(where.MODS, self.folder("mods")),
            Place(where.CACHE, self.cache),
            Place(where.SETTINGS, self.settings, file=True),
            Place(where.SOURCE, self.folder("checkout")),
        ]
        self.dialog = folders_dialog.FoldersDialog(self.found, ANNO_117)

    # What, Where and Note: the ? column has a widget and no text.
    SHOWN = (folders_dialog.WHAT_COLUMN, folders_dialog.WHERE_COLUMN, folders_dialog.NOTE_COLUMN)

    def rows(self):
        """``(heading, [(what, where, note), ...])`` as the list shows them."""
        tree = self.dialog.tree
        found = []
        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            found.append((top.text(0), [
                tuple(top.child(at).text(column) for column in self.SHOWN)
                for at in range(top.childCount())
            ]))
        return found

    def select(self, key):
        tree = self.dialog.tree
        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            for at in range(top.childCount()):
                if top.child(at).data(0, folders_dialog.PLACE_ROLE).key == key:
                    tree.setCurrentItem(top.child(at))
                    return
        self.fail("no row for %s" % key)

    def test_each_place_under_its_heading_with_what_to_know_about_it(self):
        rows = dict(self.rows())
        self.assertEqual(list(rows), ["Your work", "Mods", "Made again when needed", "The Studio"])
        work = {what: note for what, _where, note in rows["Your work"]}
        self.assertEqual(work, {"Loadouts": "Back this up", "Exported mods": "Not made yet"})
        mods = {what: note for what, _where, note in rows["Mods"]}
        self.assertEqual(mods["Mods library"], "Back this up. Chosen in Settings")
        self.assertEqual(mods["Mods folder for Anno 117: Pax Romana"], "What the game loads")
        self.assertEqual(rows["Made again when needed"], [
            ("Index and cache", str(self.cache), "Safe to delete"),
        ])
        studio = {what: note for what, _where, note in rows["The Studio"]}
        self.assertIn("run from source", studio["Program folder"])

    def test_the_full_path_is_in_each_rows_tooltip(self):
        self.select(where.LIBRARY)
        self.assertEqual(self.dialog.tree.currentItem().toolTip(folders_dialog.WHERE_COLUMN),
                         str(self.library))

    def test_each_row_has_a_question_mark_saying_what_it_holds(self):
        tree = self.dialog.tree
        tips = {}
        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            for at in range(top.childCount()):
                row = top.child(at)
                tip = tree.itemWidget(row, folders_dialog.HELP_COLUMN)
                self.assertIsInstance(tip, help_tip.HelpTip)
                self.assertEqual(tip.accessibleName(), "Help: %s" % row.text(0))
                tips[row.data(0, folders_dialog.PLACE_ROLE).key] = tip.accessibleDescription()
        self.assertEqual(len(tips), len(self.found))
        self.assertIn(".annoload", tips[where.LOADOUTS])

    def test_a_place_another_screen_explains_has_that_screens_words(self):
        """Written once: the Settings dialog's library box, the Export tab's
        destination, the Restore dialog and loading from the game files."""
        self.assertEqual(folders_dialog.HELP[where.LIBRARY], help_tip.LIBRARY_HELP)
        self.assertEqual(folders_dialog.HELP[where.EXPORTS], help_tip.EXPORT_HELP)
        self.assertEqual(folders_dialog.HELP[where.BACKUPS], help_tip.BACKUPS_HELP)
        self.assertEqual(folders_dialog.HELP[where.EXTRACTED], help_tip.EXTRACTED_HELP)
        for key in folders_dialog.WHAT:
            self.assertIn(key, folders_dialog.HELP)

    def test_tab_reaches_each_question_mark_before_the_buttons(self):
        chain, widget = [], self.dialog.tree
        for _ in range(40):
            widget = widget.nextInFocusChain()
            if widget is self.dialog.open_button:
                break
            if isinstance(widget, help_tip.HelpTip):
                chain.append(widget)
        self.assertEqual(chain, self.dialog.tips)

    def test_open_shows_the_place_and_a_file_in_its_folder(self):
        with mock.patch.object(folders_dialog, "show_in_folder") as shown:
            self.select(where.LIBRARY)
            self.dialog.open_button.click()
            self.select(where.SETTINGS)
            self.dialog.open_place()
        self.assertEqual([call.args[0] for call in shown.call_args_list], [self.library, self.settings])

    def test_a_place_not_made_yet_cannot_be_opened_and_its_path_can_be_copied(self):
        self.select(where.EXPORTS)
        self.assertFalse(self.dialog.open_button.isEnabled())
        with mock.patch.object(folders_dialog, "show_in_folder") as shown:
            self.dialog.open_place()
        shown.assert_not_called()
        self.dialog.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), str(self.root / "exports"))

    def test_a_heading_opens_nothing_and_copies_nothing(self):
        self.dialog.tree.setCurrentItem(self.dialog.tree.topLevelItem(0))
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertFalse(self.dialog.copy_button.isEnabled())

    def test_copy_all_gives_every_place_with_its_full_path(self):
        self.dialog.copy_all_button.click()
        lines = QGuiApplication.clipboard().text().splitlines()
        self.assertEqual(len(lines), len(self.found))
        self.assertEqual(lines[0], "Loadouts: %s" % self.loadouts)
        self.assertIn("Mods library: %s" % self.library, lines)

    def test_says_where_everything_is_kept_in_one_folder(self):
        self.assertFalse(self.dialog.one_folder.isVisibleTo(self.dialog))
        with mock.patch.dict(os.environ, {places.ENV_FOLDER: str(self.root / "one")}):
            dialog = folders_dialog.FoldersDialog(self.found, ANNO_117)
        self.assertIn("ANNO_STUDIO_FOLDER", dialog.one_folder.text())
        self.assertTrue(dialog.one_folder.isVisibleTo(dialog))


class TestShowInFolder(_Folders):
    def test_a_file_is_picked_out_in_its_folder(self):
        if sys.platform != "win32":
            self.skipTest("Explorer picks a file out")
        found = self.root / "settings.ini"
        found.write_text("", encoding="utf-8")
        with mock.patch.object(widgets, "_select_in_explorer") as select:
            widgets.show_in_folder(found)
        select.assert_called_once_with(found)

    def test_a_folder_not_there_opens_the_nearest_one_above_it(self):
        with mock.patch.object(widgets, "open_on_disk") as opened:
            widgets.show_in_folder(self.root / "not" / "made" / "yet")
        opened.assert_called_once_with(self.root)


class TestTheStatusLine(_Folders):
    """A message about something put in a folder can be followed there
    afterwards, until the next message."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.ui.main_window import StatusLine

        self.line = StatusLine("Ready.")
        self.line.row.resize(900, 30)
        self.line.row.show()
        self.addCleanup(self.line.row.close)

    def test_a_message_naming_a_folder_has_the_way_there(self):
        exported = self.folder("exports", "Better Bricks")
        self.line.say("Exported to %s" % exported, exported)
        self.assertTrue(self.line.folder_link.isVisible())
        self.assertEqual(self.line.folder_link.toolTip(), str(exported))
        with mock.patch("anno_modding_studio.ui.main_window.show_in_folder") as shown:
            self.line.folder_link.linkActivated.emit("folder")
        shown.assert_called_once_with(exported)

    def test_the_next_message_takes_it_away(self):
        self.line.say("Exported.", self.root)
        self.line.setText("Something else.")
        self.assertFalse(self.line.folder_link.isVisible())
        self.assertIsNone(self.line.folder)

    def test_no_folder_no_link(self):
        self.line.say("Put back: nothing.", "")
        self.assertFalse(self.line.folder_link.isVisible())

    def test_the_link_is_beside_the_message_and_not_at_the_bars_far_end(self):
        self.line.say("Short.", self.root)
        _app.processEvents()
        gap = self.line.folder_link.geometry().left() - self.line.geometry().right()
        self.assertLess(gap, 40)

    def test_a_long_message_gives_way_and_the_link_stays_in_sight(self):
        self.line.say("A very long message. " * 80, self.root)
        _app.processEvents()
        self.assertLessEqual(self.line.folder_link.geometry().right(), self.line.row.width())


if __name__ == "__main__":
    unittest.main()
