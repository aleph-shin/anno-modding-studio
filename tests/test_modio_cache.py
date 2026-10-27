"""The game's own mod.io client, read off the files it leaves behind."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modio_cache  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


def _no_override():
    """The suite points the cache at a folder of its own; these tests are
    about how the real root is found, so they look past that."""
    return mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: ""})


def _state(folder: Path, game_id: int, mods) -> None:
    metadata = folder / str(game_id) / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "state.json").write_text(json.dumps({"Mods": mods, "version": 1}), encoding="utf-8")


def _entry(mod_id: int, name: str, on_disk: str, modfile: int, version: str) -> dict:
    return {
        "ID": mod_id,
        "PathOnDisk": on_disk,
        "State": 1,
        "Profile": {
            "id": mod_id,
            "name": name,
            "date_updated": 1724483782,
            "profile_url": "https://mod.io/g/anno-1800/m/x",
            "modfile": {"id": modfile, "version": version, "filename": "x.zip"},
        },
    }


class TestWhereTheClientKeepsThings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.local = Path(self._tmp.name)
        override = _no_override()
        override.start()
        self.addCleanup(override.stop)

    def test_the_suite_can_point_it_anywhere(self):
        with mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: str(self.local / "elsewhere")}):
            self.assertEqual(modio_cache.root(str(self.local)), self.local / "elsewhere")

    def test_the_root_is_read_from_the_clients_own_settings(self):
        (self.local / "mod.io").mkdir()
        (self.local / "mod.io" / "globalsettings.json").write_text(
            '{"RootLocalStoragePath":"D:/Games/modio/"}', encoding="utf-8"
        )
        self.assertEqual(modio_cache.root(str(self.local)), Path("D:/Games/modio/"))

    def test_without_settings_it_is_the_public_folder(self):
        self.assertEqual(modio_cache.root(str(self.local)), modio_cache.DEFAULT_ROOT)
        self.assertEqual(modio_cache.root(""), modio_cache.DEFAULT_ROOT)

    def test_a_broken_settings_file_falls_back_too(self):
        (self.local / "mod.io").mkdir()
        (self.local / "mod.io" / "globalsettings.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(modio_cache.root(str(self.local)), modio_cache.DEFAULT_ROOT)

    def test_each_game_has_its_own_numbered_folder(self):
        self.assertEqual(modio_cache.game_dir(ANNO_1800, self.local), self.local / "4169")
        self.assertEqual(modio_cache.game_dir(ANNO_117, self.local), self.local / "11358")

    def test_the_mods_folder_only_counts_once_it_exists(self):
        self.assertIsNone(modio_cache.mods_dir(ANNO_1800, self.local))
        (self.local / "4169" / "mods").mkdir(parents=True)
        self.assertEqual(modio_cache.mods_dir(ANNO_1800, self.local), self.local / "4169" / "mods")


class TestWhatItInstalled(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_every_mod_in_the_state_file_with_its_file_and_version(self):
        on_disk = str(self.root / "4169" / "mods" / "3031538")
        _state(self.root, 4169, [_entry(3031538, "Extended Agriculture", on_disk, 5277683, "1.6.3")])
        found = modio_cache.installed(ANNO_1800, self.root)
        self.assertEqual(len(found), 1)
        record = found[0]
        self.assertEqual(record.mod_id, 3031538)
        self.assertEqual(record.name, "Extended Agriculture")
        self.assertEqual(record.path, Path(on_disk))
        self.assertEqual(record.modfile_id, 5277683)
        self.assertEqual(record.version, "1.6.3")
        self.assertEqual(record.date_updated, 1724483782)

    def test_an_empty_state_file_is_nothing_installed(self):
        """The 117 one on the machine this was written against is zero bytes."""
        metadata = self.root / "11358" / "metadata"
        metadata.mkdir(parents=True)
        (metadata / "state.json").write_bytes(b"")
        self.assertEqual(modio_cache.installed(ANNO_117, self.root), [])

    def test_no_state_file_at_all_is_nothing_installed(self):
        self.assertEqual(modio_cache.installed(ANNO_1800, self.root), [])

    def test_an_entry_with_no_id_is_skipped_and_the_rest_kept(self):
        _state(self.root, 4169, [{"Profile": {}}, "junk", _entry(7, "Seven", "", 70, "1")])
        found = modio_cache.installed(ANNO_1800, self.root)
        self.assertEqual([record.mod_id for record in found], [7])
        # No PathOnDisk recorded: the folder the client would have used.
        self.assertEqual(found[0].path, self.root / "4169" / "mods" / "7")


if __name__ == "__main__":
    unittest.main()
