"""*Add mods to this loadout…* in a loadout opens on what the loadout holds,
read already, and reads the folder once -- D3 (H2-16): it read the whole
folder before the list appeared, about 90 s for the run's 551 folders, and
ticking a library mod with no link read it all a second time."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_library  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402

from anno_modding_studio.core import library, reader  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402
from anno_modding_studio.ui import mods_folder_dialog  # noqa: E402
from anno_modding_studio.ui.mods_folder_dialog import (  # noqa: E402
    FOLDER_ROLE,
    HELD_ROLE,
    SHELF_ROLE,
    ModsFolderDialog,
    standing_of,
)


class TestAddingModsReadsOnce(test_library._PickerCase):
    """The loadout of TestAddingModsFromTheLibrary, with one more mod in the
    folder that the loadout does not have."""

    # Its fixture, borrowed: inherited, its tests would run here again.
    stock = test_library.TestAddingModsFromTheLibrary.stock
    held = test_library.TestAddingModsFromTheLibrary.held
    shelved = test_library.TestAddingModsFromTheLibrary.shelved
    tick = test_library.TestAddingModsFromTheLibrary.tick

    def setUp(self):
        super().setUp()
        self.stock()
        self.mod("[Gameplay] New", "new", 50)
        self.mod("[Gameplay] Newer", "newer", 60)
        self.reads = mock.patch.object(reader, "analyse_all", wraps=reader.analyse_all)
        self.read = self.reads.start()
        self.addCleanup(self.reads.stop)

    def opened(self, shelf=None, loadout=None):
        """The list as Add mods opens it on ``loadout`` (the window's)."""
        loadout = loadout or self.window.loadout
        dialog = ModsFolderDialog(
            self.window.index, self.window, start_dir=str(self.mods), game=ANNO_1800,
            focus={layer.origin.split("/")[0] for layer in loadout.layers},
            provenance=self.window.loadout.provenance, listed=self.window.loadout.listed,
            keep={layer.origin: layer.active for layer in loadout.layers},
            shelf=shelf, standing=standing_of(loadout),
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def row(self, dialog, name):
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if name in (item.data(Qt.ItemDataRole.UserRole), item.data(FOLDER_ROLE),
                        item.data(SHELF_ROLE)):
                return item
        self.fail("no row for %s" % name)

    def tick_row(self, dialog, name, on=True):
        self.row(dialog, name).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.settle(dialog)

    def layers(self, dialog):
        return sorted((layer.origin, layer.active) for layer in dialog.layers)

    # -- opening -----------------------------------------------------------
    def test_it_opens_on_the_loadouts_mods_and_reads_nothing(self):
        dialog = self.opened(shelf=self.shelved())
        self.settle(dialog)
        self.read.assert_not_called()
        self.assertIsNone(dialog.session)
        for origin in ("[Gameplay] On", "[Gameplay] Theirs"):
            item = self.row(dialog, origin)
            self.assertEqual(item.checkState(), Qt.CheckState.Checked)
            self.assertTrue(item.data(HELD_ROLE))
            self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        for name in ("[Gameplay] New", "[Gameplay] Newer"):
            item = self.row(dialog, name)
            self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
            self.assertEqual(dialog.table.item(item.row(), 5).text(), "not read yet")
        self.assertEqual(dialog.table.item(self.row(dialog, "[Gameplay] Off").row(), 5).text(),
                         "not read yet")
        self.assertIn("2 mods here are not in this loadout", dialog.status.text())
        self.assertIn("2 mods in the library are listed by name", dialog.status.text())
        self.assertIn("The first tick can take a few minutes", dialog.focus_label.text())
        # Nothing new is ticked: nothing to import.
        self.assertFalse(dialog.load_button.isEnabled())

    def test_the_loadouts_rows_say_what_a_read_of_them_says(self):
        """Built from the layers, the counts and the names are the read's."""
        full = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"},
                           keep={"[Gameplay] On": True, "[Gameplay] Theirs": True})
        before = {origin: cells for origin, cells in self.cells(full).items()
                  if origin in ("[Gameplay] On", "[Gameplay] Theirs")}
        full._accept()
        loadout = SimpleNamespace(
            layers=full.layers, changes=dict(enumerate(full.changes)), ops=full.ops,
        )
        self.read.reset_mock()
        dialog = self.opened(loadout=loadout)
        self.read.assert_not_called()
        self.assertEqual({origin: cells for origin, cells in self.cells(dialog).items()
                          if origin in before}, before)
        # The read's count of ModOps is its own: blank until it is made.
        self.assertEqual(dialog.table.item(self.row(dialog, "[Gameplay] On").row(), 5).text(), "")

    def cells(self, dialog):
        return {
            dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole): [
                dialog.table.item(row, column).text() for column in range(1, 10) if column != 5
            ]
            for row in range(dialog.table.rowCount())
        }

    def test_tick_none_and_the_filter_work_on_the_rows_shown(self):
        dialog = self.opened(shelf=self.shelved())
        dialog._tick_shown(False)
        self.settle(dialog)
        self.read.assert_not_called()
        self.assertEqual(self.row(dialog, "[Gameplay] On").checkState(), Qt.CheckState.Checked)
        dialog.filter_box.setText("newer")
        shown = [dialog.table.item(row, 1).text() for row in range(dialog.table.rowCount())
                 if not dialog.table.isRowHidden(row)]
        self.assertEqual(shown, ["[Gameplay] Newer"])
        # Tick all ticks what the filter shows, and that is one read.
        dialog._tick_shown(True)
        self.settle(dialog)
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(self.ticked(dialog), ["[Gameplay] Newer", "[Gameplay] On", "[Gameplay] Theirs"])

    def ticked(self, dialog):
        return sorted(read.origin for read in dialog.chosen())

    # -- the one read ------------------------------------------------------
    def test_ticking_a_folder_mod_reads_once_and_the_next_tick_reads_nothing(self):
        dialog = self.opened(shelf=self.shelved())
        self.tick_row(dialog, "[Gameplay] New")
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(self.ticked(dialog), ["[Gameplay] New", "[Gameplay] On", "[Gameplay] Theirs"])
        self.tick_row(dialog, "[Gameplay] Newer")
        self.assertEqual(self.read.call_count, 1)
        self.assertIn("2 mods are new to this loadout", dialog.focus_label.text())
        dialog._accept()
        self.assertEqual(self.layers(dialog), [
            ("[Gameplay] New", True), ("[Gameplay] Newer", True),
            ("[Gameplay] On", True), ("[Gameplay] Theirs", True),
        ])

    def test_a_library_mod_ticked_after_the_read_reads_nothing_again(self):
        dialog = self.opened(shelf=self.shelved())
        self.tick_row(dialog, "[Gameplay] New")
        self.assertEqual(self.read.call_count, 1)
        # Read with the folder, and listed as it was: unticked, and left so
        # by "Tick all", as a library mod listed by name was.
        self.assertEqual(self.row(dialog, "[Gameplay] Off").checkState(), Qt.CheckState.Unchecked)
        dialog._tick_shown(True)
        self.settle(dialog)
        self.assertNotIn("[Gameplay] Off", self.ticked(dialog))
        self.tick_row(dialog, "[Gameplay] Off")
        self.assertEqual(self.read.call_count, 1)
        dialog._accept()
        # Ticked to add, it comes in on: the library's way, its link goes back at Play.
        self.assertEqual(self.layers(dialog), [
            ("[Gameplay] New", True), ("[Gameplay] Newer", True), ("[Gameplay] Off", True),
            ("[Gameplay] On", True), ("[Gameplay] Theirs", True),
        ])
        self.assertFalse((self.mods / "[Gameplay] Off").exists())

    def test_a_library_mod_ticked_first_is_the_one_read(self):
        dialog = self.opened(shelf=self.shelved())
        self.tick_shelved(dialog, "[Gameplay] Off")
        self.assertEqual(self.read.call_count, 1)
        self.tick_row(dialog, "[Gameplay] New")
        self.tick_row(dialog, "[Gameplay] Spare")
        self.assertEqual(self.read.call_count, 1)
        dialog._accept()
        self.assertEqual(self.layers(dialog), [
            ("[Gameplay] New", True), ("[Gameplay] Off", True), ("[Gameplay] On", True),
            ("[Gameplay] Spare", True), ("[Gameplay] Theirs", True),
        ])

    def test_the_loadouts_switches_are_kept(self):
        self.window.loadout.layers[1].active = False
        dialog = self.opened()
        self.tick_row(dialog, "[Gameplay] New")
        dialog._accept()
        self.assertEqual(self.layers(dialog), [
            ("[Gameplay] New", True), ("[Gameplay] On", True), ("[Gameplay] Theirs", False),
        ])

    def test_a_mod_the_folder_has_off_ticked_to_add_comes_in_on(self):
        self.mod("-[Gameplay] Later", "later", 70)
        dialog = self.opened()
        self.assertEqual(dialog.table.item(self.row(dialog, "[Gameplay] Later").row(), 3).text(), "off")
        self.tick_row(dialog, "[Gameplay] Later")
        dialog._accept()
        self.assertIn(("[Gameplay] Later", True), self.layers(dialog))

    def test_import_brings_what_the_full_read_brought(self):
        """The same folder and the same tick, read at once as Add mods did
        before and read at the tick: the same layers and the same changes."""
        keep = {layer.origin: layer.active for layer in self.window.loadout.layers}
        full = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"}, keep=keep)
        self.tick(full, "[Gameplay] New", True)
        full._accept()
        once = self.opened()
        self.tick_row(once, "[Gameplay] New")
        once._accept()
        self.assertEqual([layer.to_dict() for layer in once.layers],
                         [layer.to_dict() for layer in full.layers])
        # Each read gives its changes, and their ops, uids of their own.
        self.assertEqual([replace(change, uid="", op_uid="") for change in once.changes],
                         [replace(change, uid="", op_uid="") for change in full.changes])
        self.assertEqual(len(once.ops), len(full.ops))

    # -- through the window ------------------------------------------------
    def test_opening_and_cancelling_reads_nothing(self):
        seen = {}

        def shown(inner):
            seen["reads"] = self.read.call_count
            seen["rows"] = inner.table.rowCount()
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", shown):
            self.assertFalse(self.window.library_controller.add_mods())
        self.assertEqual(seen["reads"], 0)
        # The loadout's two, the folder's two, the library's two.
        self.assertEqual(seen["rows"], 6)
        self.read.assert_not_called()

    def test_adding_through_the_window_reads_once_and_keeps_the_loadout(self):
        self.addCleanup(self.window.files.mark_saved)
        self.window.loadout.layers[1].active = False

        def driven(inner):
            self.tick_row(inner, "[Gameplay] New")
            self.tick_row(inner, "[Gameplay] Off")
            inner._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", driven):
            self.assertTrue(self.window.library_controller.add_mods())
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(
            sorted((layer.origin, layer.active) for layer in self.window.loadout.layers),
            [("[Gameplay] New", True), ("[Gameplay] Off", True),
             ("[Gameplay] On", True), ("[Gameplay] Theirs", False)],
        )
        self.assertEqual(library.links_into(self.library), self.mods)


if __name__ == "__main__":
    unittest.main()
