"""The folder ``tools/bench.py`` builds from a census, and the census mods
``tools/op_counts.py`` counts: the same units, holding the same mods."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bench  # noqa: E402
import op_counts  # noqa: E402

from anno_modding_studio.core import modfolder  # noqa: E402

PATCH = "data/config/export/main/asset/assets.xml"
OP = '<ModOps><ModOp Type="remove" GUID="%s" Path="/Values/X"/></ModOps>'


class _Census(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.census = Path(self._tmp.name) / "census"
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()

    def unit(self, number: int, *folders: str) -> Path:
        """A unit holding a mod in each of ``folders``; ``""`` is the mod's
        own contents at the top of the zip."""
        files = self.census / bench.CENSUS_GAME / str(number) / "files"
        for folder in folders:
            patch = files / folder / PATCH
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_text(OP % number, encoding="utf-8")
        return files

    def built(self, count: int = 10):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            folder = bench.build_folder(self.census, self.work, count)
        stamp = json.loads((folder / ".bench.json").read_text(encoding="utf-8"))
        names = sorted(path.name for path in folder.iterdir() if path.is_dir())
        return folder, stamp, names, said.getvalue()


class TestTheBenchFolder(_Census):
    def test_a_unit_with_the_mods_contents_at_the_top_is_one_mod(self):
        """It was copied as a mod folder called ``data``."""
        self.unit(7, "")
        self.unit(9, "Plain")
        folder, stamp, names, _said = self.built()
        self.assertEqual(names, ["Plain", "unit-7"])
        self.assertTrue((folder / "unit-7" / PATCH).is_file())
        self.assertEqual(stamp["folders"], 2)

    def test_a_folder_whose_name_is_taken_is_said_to_be_left_out(self):
        """Dropped without a word, the stamp counted fewer folders than the
        units had and nothing said which."""
        self.unit(1, "ModA")
        self.unit(2, "moda", "Other")
        _folder, stamp, names, said = self.built()
        self.assertEqual(names, ["ModA", "Other"])
        self.assertEqual(stamp["left_out"], ["2/moda"])
        self.assertIn("2/moda", said)

    def test_it_is_built_again_when_a_units_files_are_not_the_ones_copied(self):
        """Used again on the unit ids alone, a census fetched again was not
        copied again."""
        files = self.unit(1, "Mod")
        folder, _stamp, _names, _said = self.built()
        marker = folder / "Mod" / "left by the run before"
        marker.write_text("x", encoding="utf-8")
        self.built()
        self.assertTrue(marker.is_file(), "the same census: the folder is used as it is")
        (files / "Mod" / PATCH).write_text(OP % 99, encoding="utf-8")
        os.utime(files / "Mod" / PATCH, ns=(1, 1))
        folder, _stamp, _names, _said = self.built()
        self.assertFalse(marker.exists())
        self.assertIn('GUID="99"', (folder / "Mod" / PATCH).read_text(encoding="utf-8"))

    def test_it_is_built_again_when_a_run_changed_its_folders(self):
        """Play renamed a folder to switch its mod off, and wrote the mod of
        your own beside them: the loadout read from the folder afterwards
        had 416 of its 417 mods off."""
        self.unit(1, "Mod")
        self.unit(2, "Other")
        folder, _stamp, _names, _said = self.built()
        (folder / "Mod").rename(folder / "-Mod")
        _folder, _stamp, names, _said = self.built()
        self.assertEqual(names, ["Mod", "Other"])
        (folder / "[Gameplay] My Anno Mod").mkdir()
        _folder, _stamp, names, _said = self.built()
        self.assertEqual(names, ["Mod", "Other"])

    def test_a_run_works_in_a_copy(self):
        self.unit(1, "Mod")
        folder, _stamp, _names, _said = self.built()
        box = self.work / "sandbox"
        with contextlib.redirect_stdout(io.StringIO()):
            copy = bench.working_copy(folder, box)
        self.assertEqual(copy, box / "mods")
        self.assertTrue((copy / "Mod" / PATCH).is_file())
        self.assertFalse((copy / ".bench.json").exists())
        (copy / "Mod").rename(copy / "-Mod")
        with contextlib.redirect_stdout(io.StringIO()):
            copy = bench.working_copy(folder, box)
        self.assertEqual(sorted(path.name for path in copy.iterdir()), ["Mod"])
        self.assertTrue((folder / "Mod").is_dir())

    def test_built_again_the_loadout_read_from_the_old_folder_goes(self):
        """It was opened in place of reading the new folder, and the timings
        were of the old one."""
        self.unit(1, "Mod")
        self.built()
        saved = bench.saved_loadout(self.work, 10)
        saved.write_text("{}", encoding="utf-8")
        self.built()
        self.assertTrue(saved.is_file(), "the folder was not built again")
        self.unit(2, "Another")
        self.built()
        self.assertFalse(saved.exists())


class TestTheRealGameFiles(unittest.TestCase):
    def test_with_everything_in_one_folder_its_settings_say_where_they_are(self):
        """The settings were looked for where Qt keeps a user's, under
        ``APPDATA``, which is not where a launch reads them when everything
        is kept in one folder (audit finding A10-25)."""
        from unittest import mock

        from PySide6.QtCore import QSettings

        from anno_modding_studio.core import gamefiles
        from anno_modding_studio.core import index as index_mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            folder, extracted = tmp / "one folder", tmp / "extracted here"
            folder.mkdir()
            assets = extracted / "assets.xml"
            assets.parent.mkdir()
            assets.write_text("<AssetList/>", encoding="utf-8")
            gamefiles.record_read(extracted, bench.GAME, tmp / "no game", assets)
            settings = QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("last_read_root:%s" % bench.GAME, str(extracted))
            settings.sync()
            del settings
            outside = {key: value for key, value in os.environ.items()
                       if key != "ANNO_STUDIO_SETTINGS"}
            outside.update({"ANNO_STUDIO_FOLDER": str(folder), "APPDATA": str(tmp / "roaming")})
            with mock.patch.dict(os.environ, outside, clear=True):
                db = index_mod.cache_path_for(assets)
                db.write_bytes(b"")
                try:
                    root, found, indexed = bench.real_game_files()
                finally:
                    db.unlink()
        self.assertEqual(Path(root), extracted)
        self.assertEqual(found, assets)
        self.assertEqual(indexed, db)


class TestTheTwoToolsTakeTheSameMods(_Census):
    def test_a_unit_with_the_mods_contents_at_the_top_is_counted_as_that_mod(self):
        """``op_counts`` listed its ``data`` folder as the mod, and so found
        no patch in it."""
        files = self.unit(9, "")
        mods = op_counts.census_mods(self.census, bench.CENSUS_GAME, None)
        self.assertEqual(mods, [files])
        self.assertEqual(op_counts.count(mods).modops, 1)

    def test_the_first_units_are_the_same_for_both(self):
        self.unit(10, "Ten")
        self.unit(9, "")
        self.unit(2, "Two")
        (self.census / bench.CENSUS_GAME / "1").mkdir()  # fetched, with no files
        mods = op_counts.census_mods(self.census, bench.CENSUS_GAME, 2)
        _folder, stamp, names, _said = self.built(2)
        self.assertEqual(stamp["units"], ["2", "9"])
        self.assertEqual(len(mods), len(names))
        self.assertEqual(op_counts.count(mods).modops, 2)

    def test_a_box_of_mods_is_taken_out_of_its_box_by_both(self):
        """A folder that is no mod but holds mods, as *More Trade Contracts*
        ships: ``bench`` copied the box as one mod, so its mods sat a level
        too deep for the game, while ``op_counts`` counted each (audit
        finding A11-139)."""
        self.unit(3, "Box/Alpha", "Box/Beta")
        self.unit(4, "Plain")
        mods = op_counts.census_mods(self.census, bench.CENSUS_GAME, None)
        folder, stamp, names, _said = self.built()
        self.assertEqual(names, ["Alpha", "Beta", "Plain"])
        self.assertEqual(stamp["folders"], 3)
        self.assertTrue((folder / "Alpha" / PATCH).is_file())
        self.assertEqual(sorted(mod.name for mod in mods), names)
        self.assertEqual(len(modfolder.mod_folders(folder)), len(mods))

    def test_a_mod_bundled_in_another_goes_with_it(self):
        """Not copied out a second time beside the mod it ships in."""
        self.unit(5, "Host", "Host/Shared")
        (self.census / bench.CENSUS_GAME / "5" / "files" / "Host" / "Shared"
         / "modinfo.json").write_text("{}", encoding="utf-8")
        mods = op_counts.census_mods(self.census, bench.CENSUS_GAME, None)
        folder, _stamp, names, _said = self.built()
        self.assertEqual(names, ["Host"])
        self.assertTrue((folder / "Host" / "Shared" / PATCH).is_file())
        self.assertEqual(len(modfolder.mod_folders(folder)), len(mods))


class TestNoDialogWaitsForAClick(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFileDialog,
            QInputDialog,
            QMessageBox,
        )

        QApplication.instance() or QApplication([])
        # What quiet_dialogs replaces, put back after: the rest of the suite
        # runs in this process.
        for kind in (QDialog, QMessageBox, QInputDialog, QFileDialog):
            kept = dict(vars(kind))
            self.addCleanup(self._put_back, kind, kept)
        with contextlib.redirect_stdout(io.StringIO()):
            bench.quiet_dialogs()

    @staticmethod
    def _put_back(kind, kept):
        for name in set(vars(kind)) - set(kept):
            delattr(kind, name)
        for name, value in kept.items():
            if vars(kind).get(name) is not value:
                setattr(kind, name, value)

    def test_a_static_question_is_answered_with_what_it_was_given(self):
        """``QInputDialog.getText`` runs its own loop in Qt's C++, past the
        patched ``exec``: offscreen, the first edit to a loadout's own mod
        asked "Who are you?" and the bench waited all night."""
        from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit

        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(QInputDialog.getText(None, "Who are you?", "label", text="me"), ("me", True))
            self.assertEqual(QInputDialog.getText(
                None, "t", "label", QLineEdit.EchoMode.Normal, "typed"), ("typed", True))
            self.assertEqual(QInputDialog.getItem(None, "t", "label", ["a", "b"], 1, False), ("b", True))
            self.assertEqual(QFileDialog.getSaveFileName(None, "Save loadout", "x"), ("", ""))
        self.assertIn("[getText] Who are you?", said.getvalue())


if __name__ == "__main__":
    unittest.main()
