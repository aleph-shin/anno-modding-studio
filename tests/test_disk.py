"""Files written whole, JSON read back forgivingly, and paths compared."""

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

from anno_modding_studio.core import disk  # noqa: E402
from anno_modding_studio.core.changes import Loadout, own_record  # noqa: E402


class WritingWholeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_failed_write_leaves_the_old_file_whole_and_nothing_beside_it(self):
        path = self.root / "live.json"
        disk.write_json(path, {"loadout": "before"})
        with (
            mock.patch.object(disk.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            disk.write_json(path, {"loadout": "after"})
        self.assertEqual(disk.read_json(path), {"loadout": "before"})
        self.assertEqual([p.name for p in self.root.iterdir()], ["live.json"])

    def test_a_loadout_save_that_fails_keeps_the_saved_loadout(self):
        path = self.root / "Mine.annoload"
        loadout = Loadout(own=own_record(name="Mine"))
        loadout.save(path)
        loadout.own.name = "Renamed"
        with (
            mock.patch.object(disk.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            loadout.save(path)
        self.assertEqual(Loadout.load(path).own.name, "Mine")

    def test_text_is_written_with_the_platforms_line_ends_as_write_text_would(self):
        path = self.root / "profile.txt"
        disk.write_text(path, "one\ntwo\n")
        self.assertEqual(path.read_bytes(), ("one%stwo%s" % (os.linesep, os.linesep)).encode("utf-8"))
        disk.write_text(path, "one\ntwo\n", newline="\n")
        self.assertEqual(path.read_bytes(), b"one\ntwo\n")

    def test_non_ascii_is_written_as_itself(self):
        path = self.root / "live.json"
        disk.write_json(path, {"loadout": "Käse.annoload"})
        self.assertIn("Käse", path.read_text(encoding="utf-8"))


class ReadingJsonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.json"

    def test_missing_unreadable_and_not_json_are_all_the_default(self):
        self.assertEqual(disk.read_json(self.path, {}), {})
        self.path.write_text("{half", encoding="utf-8")
        self.assertEqual(disk.read_json(self.path, {}), {})
        self.path.write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(disk.read_json(self.path))

    def test_the_wrong_kind_of_thing_is_the_default(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(disk.read_json(self.path, {}), {})
        self.assertEqual(disk.read_json(self.path), [1, 2])

    def test_a_byte_order_mark_is_read_past(self):
        self.path.write_bytes(b'\xef\xbb\xbf{"a": 1}')
        self.assertEqual(disk.read_json(self.path, {}), {"a": 1})

    def test_comments_only_when_asked(self):
        self.path.write_text('// set by a manager\n{"url": "https://example.com" /* kept */}',
                             encoding="utf-8")
        self.assertEqual(disk.read_json(self.path, {}), {})
        self.assertEqual(disk.read_json(self.path, {}, comments=True), {"url": "https://example.com"})


class PathsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_inside_is_the_folder_itself_or_under_it(self):
        mods = self.root / "mods"
        self.assertTrue(disk.inside(mods, mods))
        self.assertTrue(disk.inside(mods / "[Gameplay] One" / "data", mods))
        self.assertFalse(disk.inside(self.root / "mods2", mods))
        self.assertFalse(disk.inside("", mods))

    def test_the_same_file_however_it_is_spelled(self):
        path = self.root / "Main.annoload"
        self.assertTrue(disk.same_file(path, self.root / "sub" / ".." / "Main.annoload"))
        self.assertFalse(disk.same_file(path, self.root / "Other.annoload"))
        self.assertFalse(disk.same_file("", path))

    def test_a_path_in_a_mod_as_the_mod_spells_it(self):
        (self.root / "Data" / "Config").mkdir(parents=True)
        (self.root / "Data" / "Config" / "Assets.xml").write_text("x", encoding="utf-8")
        self.assertEqual(disk.as_spelled(self.root, "data/config/assets.xml"), "Data/Config/Assets.xml")
        self.assertEqual(disk.as_spelled(self.root, "Data/Config/Assets.xml"), "Data/Config/Assets.xml")
        # From the first step it lacks, the rest is as written.
        self.assertEqual(disk.as_spelled(self.root, "data/gui/texts.xml"), "Data/gui/texts.xml")
        self.assertEqual(disk.as_spelled(self.root / "missing", "a/B"), "a/B")

    def test_free_space_is_asked_of_the_nearest_folder_that_exists(self):
        self.assertIsNotNone(disk.free_space(self.root / "not" / "yet"))


class NamesFromOutsideTest(unittest.TestCase):
    def test_a_name_becomes_one_name_windows_will_hold(self):
        cases = {
            "..\\..\\x": ".._.._x",
            "../../x": ".._.._x",
            "C:\\Windows": "C__Windows",
            "Mod: Extra": "Mod_ Extra",
            "a\nb": "a_b",
            "Thing. . ": "Thing",
            "CON": "_CON",
            "nul.txt": "_nul.txt",
            "COM\u00b2": "_COM\u00b2",
            "CONOUT$": "_CONOUT$",
            "Console": "Console",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(disk.safe_folder_name(name), expected)

    def test_nothing_left_takes_the_fallback(self):
        for name in ("", "..", ".", " . ", "   "):
            with self.subTest(name=name):
                self.assertEqual(disk.safe_folder_name(name, "mod-1"), "mod-1")

    def test_a_space_in_front_is_kept_where_the_game_sorts_by_it(self):
        self.assertEqual(disk.windows_name(" [Hadley] X."), " [Hadley] X")

    def test_directly_in_asks_where_a_built_path_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "mods"
            folder.mkdir()
            self.assertTrue(disk.directly_in(folder / "Mod", folder))
            self.assertFalse(disk.directly_in(folder / "..\\..\\x", folder))
            self.assertFalse(disk.directly_in(folder / "../x", folder))
            self.assertFalse(disk.directly_in(folder / "a" / "b", folder))
            self.assertFalse(disk.directly_in(folder / "..", folder))
            self.assertFalse(disk.directly_in(Path("C:/x"), folder))
            self.assertFalse(disk.directly_in(folder, folder))


class ReadCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "texts_english.xml"
        self.reads = []

        def read(path, game):
            self.reads.append(game)
            return path.read_text(encoding="utf-8")

        self.cache = disk.ReadCache(read)

    def test_read_once_while_the_file_is_as_it_was(self):
        self.path.write_text("one", encoding="utf-8")
        self.assertEqual(self.cache.get(self.path, "anno117"), "one")
        self.assertEqual(self.cache.get(self.path, "anno117"), "one")
        self.assertEqual(self.reads, ["anno117"])

    def test_read_again_when_it_changes_or_for_another_game(self):
        self.path.write_text("one", encoding="utf-8")
        self.cache.get(self.path, "anno117")
        self.path.write_text("three", encoding="utf-8")
        self.assertEqual(self.cache.get(self.path, "anno117"), "three")
        self.cache.get(self.path, "anno1800")
        self.assertEqual(self.reads, ["anno117", "anno117", "anno1800"])

    def test_a_file_that_is_not_there_is_none(self):
        self.assertIsNone(self.cache.get(self.path, "anno117"))
        self.assertEqual(self.reads, [])


if __name__ == "__main__":
    unittest.main()
