"""The window's one answer to where the mods folder and the library are:
see ``ui/where.py``."""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import library  # noqa: E402
from anno_modding_studio.core.changes import Loadout  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402
from anno_modding_studio.ui import where as where_mod  # noqa: E402


class _Settings:
    def __init__(self):
        self.held = {}

    def value(self, key, default=None):
        return self.held.get(key, default)

    def setValue(self, key, value):
        self.held[key] = value


class _Places(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.window = SimpleNamespace(
            loadout=Loadout(game=ANNO_1800), settings=_Settings(), game_folder=None,
        )
        self.where = where_mod.Where(self.window)
        # Documents is the test's: nothing of the machine's is looked at.
        self.documents = self.root / "Documents" / "Anno 1800"
        patched = mock.patch.object(where_mod.games, "documents_dir", return_value=self.documents)
        patched.start()
        self.addCleanup(patched.stop)

    def folder(self, *parts) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True)
        return path

    def remember(self, folder, game=ANNO_1800):
        self.window.settings.setValue("%s:%s" % (where_mod.LAST_MODS_FOLDER, game), str(folder))


class TestTheModsFolder(_Places):
    def test_the_loadouts_own_folder_first(self):
        own = self.folder("theirs", "mods")
        self.remember(self.folder("read last"))
        self.window.loadout.mods_folder = str(own)
        self.assertEqual(self.where.mods_folder(), str(own))
        self.assertEqual(self.where.install_folder(), str(own))

    def test_a_loadout_from_another_machine_falls_through_to_the_folder_read_last(self):
        """The folder the loadout names is not on this machine. Play took the
        folder read last here while the card took the game's own, so the two
        described different folders; and a fetch went to the missing one."""
        self.window.loadout.mods_folder = r"D:\somebody else\Anno 1800\mods"
        last = self.folder("read last")
        self.window.game_folder = str(self.folder("Anno 1800", "mods").parent)
        self.remember(last)
        self.assertEqual(self.where.mods_folder(), str(last))
        self.assertEqual(self.where.install_folder(), str(last))

    def test_the_folder_read_last_for_the_other_game_is_not_this_ones(self):
        self.remember(self.folder("117 mods"), game=ANNO_117)
        installed = self.folder("Anno 1800")
        (installed / "mods").mkdir()
        self.window.game_folder = str(installed)
        self.assertEqual(self.where.mods_folder(), str(installed / "mods"))

    def test_a_folder_read_last_that_has_gone_is_passed_over(self):
        self.remember(self.root / "gone")
        documents_mods = self.documents / "mods"
        documents_mods.mkdir(parents=True)
        self.assertEqual(self.where.mods_folder(), str(documents_mods))

    def test_with_nothing_there_there_is_no_folder_but_a_fetch_may_make_documents_one(self):
        self.window.loadout.mods_folder = r"D:\somebody else\mods"
        self.assertEqual(self.where.mods_folder(), "")
        self.assertEqual(self.where.install_folder(), str(self.documents / "mods"))

    def test_the_game_s_own_needs_its_mods_folder_to_be_there(self):
        self.window.game_folder = str(self.folder("Anno 1800"))
        self.assertIsNone(self.where.game_mods())
        (Path(self.window.game_folder) / "mods").mkdir()
        self.assertEqual(self.where.game_mods(), Path(self.window.game_folder) / "mods")


class TestTheLibrary(_Places):
    def test_the_chosen_root_and_any_game(self):
        self.window.settings.setValue(where_mod.LIBRARY_KEY, str(self.root / "lib"))
        self.assertEqual(self.where.library_root(), str(self.root / "lib"))
        self.assertEqual(self.where.library_dir(), library.game_dir(ANNO_1800, str(self.root / "lib")))
        self.assertEqual(self.where.library_dir(ANNO_117), library.game_dir(ANNO_117, str(self.root / "lib")))

    def test_the_game_is_the_open_loadout_s_when_asked(self):
        self.window.loadout = Loadout(game=ANNO_117)
        self.assertEqual(self.where.game, ANNO_117)
        self.assertEqual(self.where.library_dir(), library.game_dir(ANNO_117, ""))


class TestEveryPlace(_Places):
    """*Help > Studio folders* lists what this answers: each place the
    Studio uses, from the module that owns it, as it is now."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.core import places

        self.places = places
        self.window.output = SimpleNamespace(export_dir=places.exports_dir)
        self.window.game_files = SimpleNamespace(READ_ROOT_KEY="last_read_root")
        self.window.settings.fileName = lambda: str(self.root / "settings.ini")
        self.window.loadout.mods_folder = str(self.folder("mods"))

    def by_key(self):
        return {place.key: place for place in self.where.places()}

    def test_each_place_once_in_the_order_they_are_listed(self):
        self.assertEqual([place.key for place in self.where.places()], [
            where_mod.LOADOUTS, where_mod.PROJECTS, where_mod.EXPORTS, where_mod.BACKUPS,
            where_mod.LIBRARY, where_mod.MODS, where_mod.EXTRACTED,
            where_mod.CACHE, where_mod.SETTINGS, where_mod.SOURCE,
        ])

    def test_each_is_where_its_owner_says(self):
        found = self.by_key()
        self.assertEqual(found[where_mod.LOADOUTS].path, self.places.loadouts_dir())
        self.assertEqual(found[where_mod.LIBRARY].path, library.root())
        self.assertEqual(found[where_mod.MODS].path, self.root / "mods")
        self.assertEqual(found[where_mod.EXTRACTED].path, self.places.extracted_dir())
        self.assertEqual(found[where_mod.SOURCE].path, self.places.app_dir())
        self.assertFalse(any(place.chosen for place in found.values()))

    def test_the_settings_are_the_file_the_window_has_open(self):
        settings = self.by_key()[where_mod.SETTINGS]
        self.assertEqual(settings.path, self.root / "settings.ini")
        self.assertTrue(settings.file)

    def test_a_library_chosen_in_settings_is_listed_where_it_is_and_as_chosen(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(library.ENV_LIBRARY, None)
            self.window.settings.setValue(where_mod.LIBRARY_KEY, str(self.root / "E-Mods"))
            listed = self.by_key()[where_mod.LIBRARY]
        self.assertEqual(listed.path, self.root / "E-Mods")
        self.assertTrue(listed.chosen)

    def test_an_export_folder_of_the_loadouts_own_is_listed_as_chosen(self):
        self.window.output = SimpleNamespace(export_dir=lambda: self.root / "out")
        listed = self.by_key()[where_mod.EXPORTS]
        self.assertEqual(listed.path, self.root / "out")
        self.assertTrue(listed.chosen)

    def test_game_files_read_elsewhere_are_listed_where_they_were_read(self):
        read = self.folder("my extracted", "Anno 1800")
        self.window.settings.setValue("last_read_root:%s" % ANNO_1800, str(read))
        listed = self.by_key()[where_mod.EXTRACTED]
        self.assertEqual(listed.path, read)
        self.assertTrue(listed.chosen)

    def test_a_built_studio_lists_its_program_folder(self):
        with mock.patch.object(self.places, "built", return_value=True):
            keys = [place.key for place in self.where.places()]
        self.assertIn(where_mod.PROGRAM, keys)
        self.assertNotIn(where_mod.SOURCE, keys)


class TestTheDialogsAreTold(unittest.TestCase):
    def test_none_of_the_three_guesses_the_library(self):
        """Each fell back to the default library when not given one, which is
        the wrong one once the library has been moved in Settings."""
        from anno_modding_studio.ui.install_plan_dialog import InstallPlanDialog
        from anno_modding_studio.ui.modio_get_dialog import ModioGetDialog
        from anno_modding_studio.ui.modio_updates_dialog import ModioUpdatesDialog

        for dialog in (InstallPlanDialog, ModioGetDialog, ModioUpdatesDialog):
            with self.subTest(dialog.__name__):
                parameter = inspect.signature(dialog.__init__).parameters["library_dir"]
                self.assertIs(parameter.default, parameter.empty)


if __name__ == "__main__":
    unittest.main()
