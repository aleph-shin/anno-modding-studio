"""The machine's own folders, asked in one place."""

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

from anno_modding_studio.core import games, places  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class GameDocumentsTest(unittest.TestCase):
    def test_each_game_has_its_own_folder_under_documents(self):
        home = Path("C:/Users/someone")
        with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(os.environ):
            # The suite names a Documents folder of its own: see below.
            os.environ.pop(places.ENV_DOCUMENTS, None)
            self.assertEqual(games.documents_dir(ANNO_1800), home / "Documents" / "Anno 1800")
            self.assertEqual(games.documents_dir(ANNO_117), home / "Documents" / "Anno 117 - Pax Romana")

    def test_the_suite_is_given_a_documents_folder_that_is_nobodys(self):
        """A game's own mods folder is under Documents, and the suite read
        the developer's as the game's where a test named no other."""
        named = Path(os.environ[places.ENV_DOCUMENTS])
        self.assertEqual(places.documents(), named)
        self.assertNotEqual(named, Path.home() / "Documents")
        self.assertEqual(games.documents_dir(ANNO_1800), named / "Anno 1800")
        self.assertFalse((named / "Anno 1800").exists())

    def test_a_game_the_studio_does_not_know_has_none(self):
        self.assertIsNone(games.documents_dir(""))
        self.assertIsNone(games.documents_dir("anno2205"))


class LocalAppDataTest(unittest.TestCase):
    def test_the_variable_when_it_is_set(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "D:/Local"}):
            self.assertEqual(places.local_app_data(), Path("D:/Local"))

    def test_its_usual_place_when_it_is_not(self):
        home = Path("C:/Users/someone")
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}), \
                mock.patch("pathlib.Path.home", return_value=home):
            self.assertEqual(places.local_app_data(), home / "AppData" / "Local")


class TestWhereTheStudioKeepsWhatItMakes(unittest.TestCase):
    """Everything written goes under one home, and it answers to a variable.

    Not a nicety: loadouts are written by ordinary use, so the suite driving
    that path wrote real loadout files into the developer's own Documents --
    fourteen hundred of them, on the machine this was found on.  That the
    window's own writers use it is pinned in test_onboarding.
    """

    def test_every_folder_hangs_off_the_one_home(self):
        from anno_modding_studio.core import places

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {places.ENV_HOME: tmp}):
                home = Path(tmp)
                self.assertEqual(places.studio_home(), home)
                for folder in (
                    places.loadouts_dir(),
                    places.exports_dir(),
                    places.backups_dir(),
                    places.extracted_dir(),
                ):
                    self.assertEqual(folder.parent, home)

    def test_unset_it_is_the_documents_folder(self):
        from anno_modding_studio.core import places

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(places.ENV_HOME, None)
            os.environ.pop(places.ENV_DOCUMENTS, None)
            self.assertEqual(
                places.studio_home(), Path.home() / "Documents" / places.NAME
            )

    def test_what_is_extracted_from_the_game_is_with_the_cache_not_in_documents(self):
        from anno_modding_studio.core import places

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "D:/Local"}):
            os.environ.pop(places.ENV_HOME, None)
            self.assertEqual(
                places.extracted_dir(), Path("D:/Local") / places.NAME / "extracted"
            )
            # Backups stay with what you make.
            self.assertEqual(
                places.backups_dir().parent, places.documents() / places.NAME
            )

    def test_the_suite_itself_is_pointed_somewhere_else(self):
        """conftest.py sets it before any test module is imported."""
        from anno_modding_studio.core import places

        self.assertTrue(os.environ.get(places.ENV_HOME))
        self.assertNotEqual(
            places.studio_home(), Path.home() / "Documents" / places.NAME
        )


class TestEverythingInOneFolder(unittest.TestCase):
    """Opted into, one folder holds all the Studio writes: what you make,
    what can be made again, the library, the settings and the mod.io key."""

    NAMED = ("ANNO_STUDIO_HOME", "ANNO_STUDIO_CACHE", "ANNO_STUDIO_LIBRARY",
             "ANNO_STUDIO_SETTINGS", "ANNO_STUDIO_SECRETS")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # As a fresh shell has it: none of the suite's own names set.
        patch = mock.patch.dict(os.environ, {})
        patch.start()
        self.addCleanup(patch.stop)
        for name in self.NAMED:
            os.environ.pop(name, None)

    def everything(self):
        from anno_modding_studio.core import library, secrets
        from anno_modding_studio.ui.main_window import open_settings

        return [
            places.loadouts_dir(), places.exports_dir(), places.backups_dir(),
            places.extracted_dir(), places.cache_dir(), library.root(),
            secrets.default_path(), Path(open_settings().fileName()),
        ]

    def test_a_named_folder_holds_all_of_it(self):
        os.environ[places.ENV_FOLDER] = str(self.root)
        self.assertEqual(places.one_folder(), self.root)
        for path in self.everything():
            self.assertIn(self.root, path.parents, "%s escaped the folder" % path)
        self.assertEqual(places.settings_file(), self.root / "settings.ini")

    def test_a_folder_made_beside_the_studio_is_it(self):
        os.environ.pop(places.ENV_FOLDER, None)
        with mock.patch.object(places, "app_dir", return_value=self.root):
            self.assertIsNone(places.one_folder())
            (self.root / places.BESIDE_NAME).mkdir()
            self.assertEqual(places.one_folder(), self.root / places.BESIDE_NAME)
            for path in self.everything():
                self.assertIn(self.root / places.BESIDE_NAME, path.parents)

    def built(self):
        """As the built Studio, whose folder is ``self.root``."""
        frozen = mock.patch.object(sys, "frozen", True, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        executable = mock.patch.object(sys, "executable", str(self.root / "Studio.exe"))
        executable.start()
        self.addCleanup(executable.stop)

    def test_a_built_studio_keeps_nothing_in_the_folder_beside_it(self):
        """Its update is a new folder, which would start on nothing, and the
        library the game links into went with the old one (S4)."""
        os.environ.pop(places.ENV_FOLDER, None)
        (self.root / places.BESIDE_NAME).mkdir()
        self.built()
        self.assertEqual(places.app_dir(), self.root.resolve())
        self.assertIsNone(places.one_folder())
        for path in self.everything():
            self.assertNotIn(self.root / places.BESIDE_NAME, path.parents)
        self.assertEqual(places.unused_beside(), self.root.resolve() / places.BESIDE_NAME)

    def test_a_built_studio_keeps_everything_in_the_folder_named(self):
        """Named outside the program's folder, which an update leaves
        behind, so every version finds it."""
        os.environ[places.ENV_FOLDER] = str(self.root / "data")
        (self.root / places.BESIDE_NAME).mkdir()
        self.built()
        self.assertEqual(places.one_folder(), self.root / "data")
        self.assertIsNone(places.unused_beside())

    def test_nothing_is_unused_where_there_is_no_folder_or_no_build(self):
        os.environ.pop(places.ENV_FOLDER, None)
        with mock.patch.object(places, "app_dir", return_value=self.root):
            (self.root / places.BESIDE_NAME).mkdir()
            # Run from the source, it is the one folder.
            self.assertIsNone(places.unused_beside())
        (self.root / places.BESIDE_NAME).rmdir()
        self.built()
        self.assertIsNone(places.unused_beside())

    def test_none_says_there_is_none_whatever_is_beside_the_studio(self):
        (self.root / places.BESIDE_NAME).mkdir()
        os.environ[places.ENV_FOLDER] = "none"
        with mock.patch.object(places, "app_dir", return_value=self.root):
            self.assertIsNone(places.one_folder())
            self.assertEqual(places.settings_file(), places.local_home() / "settings.ini")

    def test_the_suite_has_none(self):
        """tests/_isolation.py says so before any test module is imported."""
        self.assertEqual(os.environ.get(places.ENV_FOLDER), "none")
        self.assertIsNone(places.one_folder())

    def test_a_folder_named_apart_still_wins(self):
        """Which is how a test's home stays a test's."""
        os.environ[places.ENV_FOLDER] = str(self.root / "one")
        os.environ[places.ENV_HOME] = str(self.root / "home")
        os.environ["ANNO_STUDIO_CACHE"] = str(self.root / "cache")
        self.assertEqual(places.loadouts_dir(), self.root / "home" / "loadouts")
        self.assertEqual(places.extracted_dir(), self.root / "home" / "extracted")
        self.assertEqual(places.cache_dir(), self.root / "cache")

    def test_a_library_chosen_in_settings_is_still_the_library(self):
        from anno_modding_studio.core import library

        os.environ[places.ENV_FOLDER] = str(self.root)
        self.assertEqual(library.root("E:/Mods"), Path("E:/Mods"))


class TestSettingsBesideTheLibrary(unittest.TestCase):
    """The settings are kept with what the Studio keeps for itself.  They
    were under the roaming app data, a third folder of the same name, and
    a Studio that kept them there has them moved once, as it starts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patch = mock.patch.dict(os.environ, {
            "APPDATA": str(self.root / "Roaming"),
            "LOCALAPPDATA": str(self.root / "Local"),
            places.ENV_FOLDER: "none",
        })
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop(places.ENV_SETTINGS, None)
        self.former = self.root / "Roaming" / places.NAME / (places.NAME + ".ini")
        self.settings = self.root / "Local" / places.NAME / "settings.ini"

    def keep_former(self, text: str = "[General]\nlibrary_dir=E:/Mods\n") -> None:
        self.former.parent.mkdir(parents=True)
        self.former.write_text(text, encoding="utf-8")

    def test_the_settings_are_beside_the_library(self):
        self.assertEqual(places.settings_file(), self.settings)
        self.assertEqual(places.settings_file().parent, places.local_home())

    def test_a_file_named_from_outside_is_the_settings(self):
        os.environ[places.ENV_SETTINGS] = str(self.root / "mine.ini")
        self.assertEqual(places.settings_file(), self.root / "mine.ini")

    def test_the_former_settings_are_moved_and_their_folder_goes(self):
        self.keep_former()
        self.assertTrue(places.adopt_former_settings())
        self.assertIn("library_dir=E:/Mods", self.settings.read_text(encoding="utf-8"))
        self.assertFalse(self.former.parent.exists())

    def test_a_folder_with_anything_else_in_it_stays(self):
        self.keep_former()
        (self.former.parent / "notes.txt").write_text("mine", encoding="utf-8")
        self.assertTrue(places.adopt_former_settings())
        self.assertTrue((self.former.parent / "notes.txt").is_file())
        self.assertFalse(self.former.exists())

    def test_settings_already_beside_the_library_are_not_written_over(self):
        self.keep_former()
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("[General]\nlibrary_dir=F:/Kept\n", encoding="utf-8")
        self.assertFalse(places.adopt_former_settings())
        self.assertIn("F:/Kept", self.settings.read_text(encoding="utf-8"))
        self.assertTrue(self.former.is_file())

    def test_nothing_moves_into_a_file_named_from_outside(self):
        self.keep_former()
        os.environ[places.ENV_SETTINGS] = str(self.root / "mine.ini")
        self.assertFalse(places.adopt_former_settings())
        self.assertFalse((self.root / "mine.ini").exists())
        self.assertTrue(self.former.is_file())

    def test_nothing_moves_into_the_one_folder(self):
        """It was chosen to be apart from the usual places."""
        self.keep_former()
        os.environ[places.ENV_FOLDER] = str(self.root / "one")
        self.assertFalse(places.adopt_former_settings())
        self.assertTrue(self.former.is_file())

    def test_with_no_former_settings_nothing_is_made(self):
        self.assertFalse(places.adopt_former_settings())
        self.assertFalse(self.settings.parent.exists())


if __name__ == "__main__":
    unittest.main()
