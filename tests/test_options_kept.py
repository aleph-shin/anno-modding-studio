"""A mod's own options answered here survive the folder being read again to
add mods -- H2-7: *Add mods to this loadout…* read Early Game back at what
its folder holds, and the one switch the user had changed was gone."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt  # noqa: E402
from support import TruthWindow, install_mod  # noqa: E402

from anno_modding_studio.core.changes import ModLayer  # noqa: E402
from anno_modding_studio.ui.mods_folder_dialog import (  # noqa: E402
    FOLDER_ROLE,
    ModsFolderDialog,
)

CALAMARY = "data/config/export/main/asset/assets.xml::Disable/Enable Calamary for Jornalero"


class _Seen(ModsFolderDialog):
    """The real dialog, stopped where it would start reading: what it would
    read each mod at is what it holds then.  Shown, a mod the loadout does
    not have is ticked to add, which is what reads the folder (D3)."""

    seen: ClassVar[list] = []

    def reload(self):
        type(self).seen.append(dict(self.settings))

    def exec(self):
        # Opened on the loadout's own mods: nothing read yet.
        type(self).seen.append("shown")
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(FOLDER_ROLE):
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self._shelf_timer.stop()
        self._read_shelved()
        return 0


class TestAddingModsKeepsTheirOptions(TruthWindow):
    def setUp(self):
        super().setUp()
        for origin in ("[Gameplay] Early Game", "[Gameplay] Other", "[Gameplay] New"):
            install_mod(self.mods, origin, origin.split()[-1].lower(), "1")
        loadout = self.window.loadout
        loadout.mods_folder = str(self.mods)
        loadout.set_up_as("manage")
        loadout.layers = [
            ModLayer(origin="[Gameplay] Early Game", mod_id="game",
                     folder=str(self.mods / "[Gameplay] Early Game"),
                     options={CALAMARY: "false"}),
            ModLayer(origin="[Gameplay] Other", mod_id="other",
                     folder=str(self.mods / "[Gameplay] Other")),
        ]
        _Seen.seen = []
        patched = mock.patch("anno_modding_studio.ui.main_window.ModsFolderDialog", _Seen)
        patched.start()
        self.addCleanup(patched.stop)

    def test_add_mods_reads_each_mod_at_what_was_answered_here(self):
        self.window.library_controller.add_mods()
        self.assertEqual(_Seen.seen, ["shown", {"[Gameplay] Early Game": {CALAMARY: "false"}}])

    def test_a_read_of_the_folder_as_the_game_has_it_does_not(self):
        """Import your installed mods: the folder is the say, switches and all."""
        self.window.settings.setValue(self.window.mods_key("last_mods_folder"), str(self.mods))
        with mock.patch.object(self.window.live, "live_profile_here", return_value=None):
            self.window.load_installed_mods(focus=set())
        # Read as it opens, a read about the mods just ticked.
        self.assertEqual(_Seen.seen[0], {})


if __name__ == "__main__":
    unittest.main()
