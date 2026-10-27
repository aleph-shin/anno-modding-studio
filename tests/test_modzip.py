"""Putting a downloaded mod into a mods folder, whatever shape its zip is."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modzip, places  # noqa: E402


def _zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return path


class _InTemp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        # Set-aside folders go under the Studio's backups, which the suite
        # already points at a folder of its own; keep it under this test's.
        patched = mock.patch.object(places, "backups_dir", return_value=self.root / "backups")
        patched.start()
        self.addCleanup(patched.stop)


class TestSafePaths(unittest.TestCase):
    def test_plain_paths_pass_and_bad_characters_are_replaced(self):
        self.assertEqual(modzip.safe_path("[Seal] X/modinfo.json"), "[Seal] X/modinfo.json")
        self.assertEqual(modzip.safe_path("a\\b<c>.txt"), "a/b_c_.txt")
        self.assertEqual(modzip.safe_path("CON.txt"), "_CON.txt")

    def test_every_device_name_is_kept_from_the_device(self):
        for name in ("COM0.txt", "LPT9", "CONIN$", "conout$.log", "CON .txt",
                     "COM\u00b9.txt"):
            with self.subTest(name=name):
                self.assertTrue(modzip.safe_path("Mod/" + name).startswith("Mod/_"))

    def test_climbing_out_or_naming_a_drive_is_refused(self):
        self.assertIsNone(modzip.safe_path("../evil.txt"))
        self.assertIsNone(modzip.safe_path("a/../../evil.txt"))
        self.assertIsNone(modzip.safe_path("C:/Windows/x"))
        self.assertIsNone(modzip.safe_path(""))


class TestTheThreeShapes(_InTemp):
    def test_a_zip_holding_a_mod_folder(self):
        archive = _zip(self.root / "m.zip", {"[Gameplay] Thing/modinfo.json": "{}", "[Gameplay] Thing/data/x.xml": "<a/>"})
        done = modzip.install(archive, self.mods)
        self.assertEqual(done.placed, [self.mods / "[Gameplay] Thing"])
        self.assertTrue((self.mods / "[Gameplay] Thing" / "data" / "x.xml").is_file())
        self.assertIsNone(done.set_aside)
        self.assertEqual([p.name for p in self.mods.iterdir()], ["[Gameplay] Thing"])

    def test_a_zip_that_is_the_mods_own_contents_is_named_after_the_zip(self):
        archive = _zip(self.root / "gameplaything-9rm7.zip", {"modinfo.json": "{}", "data/x.xml": "<a/>"})
        done = modzip.install(archive, self.mods)
        self.assertEqual(done.placed, [self.mods / "gameplaything-9rm7"])
        self.assertTrue((self.mods / "gameplaything-9rm7" / "modinfo.json").is_file())

    def test_and_takes_a_better_name_when_given_one(self):
        archive = _zip(self.root / "x.zip", {"modinfo.json": "{}"})
        done = modzip.install(archive, self.mods, name="[Gameplay] Thing (Kurila)")
        self.assertEqual(done.placed, [self.mods / "[Gameplay] Thing (Kurila)"])

    def test_a_box_of_mods_is_opened(self):
        archive = _zip(self.root / "box.zip", {
            "More Trade Contracts/readme.txt": "hi",
            "More Trade Contracts/[Gameplay] Contracts A/modinfo.json": "{}",
            "More Trade Contracts/[Gameplay] Contracts B/data/x.xml": "<a/>",
        })
        done = modzip.install(archive, self.mods)
        self.assertEqual(
            [p.name for p in done.placed], ["[Gameplay] Contracts A", "[Gameplay] Contracts B"]
        )
        self.assertFalse((self.mods / "More Trade Contracts").exists())

    def test_a_mod_nested_deep_in_its_box_is_found(self):
        """Census unit 3986772 holds its mod four folders down, and Anno 1800
        loads it from there: the loader walks down to each modinfo (A11-144).
        Deeper than a box's own folders, data without a modinfo is not a mod
        -- the game does not load one nested so (P7-32)."""
        nest = "[Cheat] Ultra Income/" * 4
        archive = _zip(self.root / "deep.zip", {
            nest + "modinfo.json": "{}",
            nest + "data/x.xml": "<a/>",
            "Extras/Wrap/Loose/data/y.xml": "<a/>",
        })
        done = modzip.install(archive, self.mods)
        self.assertEqual(done.placed, [self.mods / "[Cheat] Ultra Income"])
        self.assertTrue((self.mods / "[Cheat] Ultra Income" / "data" / "x.xml").is_file())

    def test_a_zip_with_no_mod_in_it_is_refused_and_leaves_nothing(self):
        archive = _zip(self.root / "junk.zip", {"readme.txt": "hi"})
        with self.assertRaises(modzip.InstallError):
            modzip.install(archive, self.mods)
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_unsafe_entries_are_skipped_and_reported(self):
        archive = _zip(self.root / "m.zip", {"Mod/modinfo.json": "{}", "../escape.txt": "no"})
        done = modzip.install(archive, self.mods)
        self.assertEqual(done.skipped, ["../escape.txt"])
        self.assertFalse((self.root / "escape.txt").exists())

    def test_a_name_from_outside_is_one_folder_in_the_mods_folder(self):
        # The name is a mod.io mod's, and anyone publishing chooses it.  A
        # folder of the user's two levels up must stay where it is.
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "own.txt").write_text("mine")
        nested = self.mods / "library" / "anno1800"
        nested.mkdir(parents=True)
        for name in ("..\\..\\..\\victim", "../../../victim", "..\\..\\x", "C:\\x",
                     "\\\\host\\share", "..", "Mod: Extra", "CON"):
            with self.subTest(name=name):
                archive = _zip(self.root / "x.zip", {"modinfo.json": "{}"})
                done = modzip.install(archive, nested, name=name)
                [placed] = done.placed
                self.assertEqual(placed.parent, nested)
                self.assertIsNone(done.set_aside)
                for one in nested.iterdir():
                    modzip._remove(one)
        self.assertEqual((victim / "own.txt").read_text(), "mine")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["mods", "victim", "x.zip"])

    def test_the_name_is_cleaned_to_one_safe_folder_name(self):
        archive = _zip(self.root / "x.zip", {"modinfo.json": "{}"})
        done = modzip.install(archive, self.mods, name="..\\..\\x")
        self.assertEqual(done.placed, [self.mods / ".._.._x"])
        archive = _zip(self.root / "x.zip", {"modinfo.json": "{}"})
        done = modzip.install(archive, self.mods, name="..")
        self.assertEqual(done.placed, [self.mods / "x"])

    def test_nothing_is_set_aside_outside_the_mods_folder(self):
        outside = self.root / "outside"
        outside.mkdir()
        archive = _zip(self.root / "m.zip", {"modinfo.json": "{}"})
        with self.assertRaises(modzip.InstallError):
            modzip.install(archive, self.mods, replace=outside)
        self.assertTrue(outside.is_dir())
        self.assertFalse((self.root / "backups").exists())

    def test_nothing_of_the_staging_folder_is_left_behind(self):
        archive = _zip(self.root / "m.zip", {"Mod/modinfo.json": "{}"})
        modzip.install(archive, self.mods)
        self.assertEqual([p.name for p in self.mods.iterdir()], ["Mod"])


@unittest.skipUnless(os.name == "nt", "the 260-character limit is Windows'")
class TestAZipDeeperThanWindowsReaches(_InTemp):
    """A mod's tree can run past the 260 characters Windows opens without
    being asked specially, before a library or a staging folder is put in
    front of it. Unpacking stopped there, and sync dropped the zip (A8-1)."""

    DEEP = "/".join(["d%d" % n + "x" * 38 for n in range(7)])

    def deep(self) -> Path:
        return _zip(self.root / "deep.zip", {
            "Deep Mod/modinfo.json": "{}",
            "Deep Mod/data/%s/leaf.cfg" % self.DEEP: "leaf",
        })

    def read(self, path: Path) -> str:
        from anno_modding_studio.core import disk

        with open(disk.reachable(path), encoding="utf-8") as handle:
            return handle.read()

    def test_it_is_unpacked_all_the_way_down(self):
        into = self.root / "out"
        self.assertEqual(modzip.unpack(self.deep(), into), [])
        leaf = into / "Deep Mod" / "data" / self.DEEP / "leaf.cfg"
        self.assertGreater(len(str(leaf)), 260)
        self.assertEqual(self.read(leaf), "leaf")
        modzip._remove(into)
        self.assertFalse(into.exists())

    def test_and_installed_leaving_no_staging_folder(self):
        done = modzip.install(self.deep(), self.mods)
        self.assertEqual(done.placed, [self.mods / "Deep Mod"])
        self.assertEqual(self.read(self.mods / "Deep Mod" / "data" / self.DEEP / "leaf.cfg"), "leaf")
        self.assertEqual([entry.name for entry in self.mods.iterdir()], ["Deep Mod"])
        # What the test made is as deep as what it tested: taken away the
        # same way, or the temporary folder cannot be.
        modzip._remove(self.mods)

    def test_a_zip_with_no_mod_in_it_still_leaves_nothing(self):
        junk = _zip(self.root / "junk.zip", {"notes/%s/leaf.txt" % self.DEEP: "hi"})
        with self.assertRaises(modzip.InstallError):
            modzip.install(junk, self.mods)
        self.assertEqual(list(self.mods.iterdir()), [])


class TestAZipTooBigIsRefusedFirst(_InTemp):
    """A zip bomb, or a zip of millions of entries, is refused before any of
    it is written: what it says it unpacks to is added up first."""

    def zip(self):
        return _zip(self.root / "big.zip", {"Mod/modinfo.json": "{}", "Mod/data/x.xml": "x" * 5000})

    def assert_refused_leaving_nothing(self):
        into = self.root / "out"
        with self.assertRaises(modzip.InstallError):
            modzip.unpack(self.zip(), into)
        self.assertFalse(into.exists())
        with self.assertRaises(modzip.InstallError):
            modzip.install(self.zip(), self.mods)
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_more_bytes_than_any_mod(self):
        with mock.patch.object(modzip, "MOST_BYTES", 1000):
            self.assert_refused_leaving_nothing()

    def test_more_entries_than_any_mod(self):
        with mock.patch.object(modzip, "MOST_ENTRIES", 1):
            self.assert_refused_leaving_nothing()

    def test_more_than_the_drive_has_free(self):
        with mock.patch.object(modzip.disk, "free_space", return_value=100):
            self.assert_refused_leaving_nothing()

    def test_a_modinfo_too_big_to_be_one_is_not_read(self):
        archive = _zip(self.root / "m.zip", {"Mod/modinfo.json": '{"ModID": "x"}'})
        with mock.patch.object(modzip, "MOST_MODINFO", 4):
            [peeked] = modzip.peek(archive)
        self.assertEqual(peeked.mod_id, "")


class TestPeeking(_InTemp):
    """What a zip holds, said without unpacking it."""

    def test_each_shape_is_found_as_unpacking_would_find_it(self):
        shapes = {
            "folder": {"[Gameplay] Thing/modinfo.json": "{}", "[Gameplay] Thing/data/x.xml": "<a/>"},
            "contents": {"modinfo.json": "{}", "data/x.xml": "<a/>"},
            "box": {
                "Box/readme.txt": "hi",
                "Box/[Gameplay] A/modinfo.json": "{}",
                "Box/[Gameplay] B/data/x.xml": "<a/>",
            },
            "two": {"B/modinfo.json": "{}", "a/data/x.xml": "<a/>", "notes.txt": "hi"},
            "deep": {
                "Box/Wrap/Inner/modinfo.json": "{}",
                "Box/Wrap/Inner/Bundled/modinfo.json": "{}",
                "Box/Other/Loose/data/x.xml": "<a/>",
                "Box/Mod/data/x.xml": "<a/>",
            },
        }
        for name, entries in shapes.items():
            with self.subTest(name):
                archive = _zip(self.root / (name + ".zip"), entries)
                peeked = [mod.folder or archive.stem for mod in modzip.peek(archive)]
                placed = [p.name for p in modzip.install(archive, self.root / name).placed]
                self.assertEqual(peeked, placed)
        # And nothing was written to find out.
        archive = _zip(self.root / "only.zip", {"Mod/modinfo.json": "{}"})
        before = sorted(p.name for p in self.root.iterdir())
        modzip.peek(archive)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_the_modinfo_is_read_out_of_the_zip(self):
        archive = _zip(self.root / "m.zip", {
            "Thing/ModInfo.json": '{"ModID": "thing-kurila", "Version": "1.2", '
                                  '"ModName": {"English": "Thing"}, "CreatorName": "Kurila"}',
        })
        (mod,) = modzip.peek(archive)
        self.assertEqual(
            (mod.folder, mod.mod_name, mod.mod_id, mod.version, mod.creator),
            ("Thing", "Thing", "thing-kurila", "1.2", "Kurila"),
        )

    def test_a_zip_with_no_mod_and_a_file_that_is_no_zip_are_refused(self):
        junk = _zip(self.root / "junk.zip", {"readme.txt": "hi"})
        with self.assertRaises(modzip.InstallError):
            modzip.peek(junk)
        (self.root / "not.zip").write_text("not a zip", encoding="utf-8")
        with self.assertRaises(modzip.InstallError):
            modzip.peek(self.root / "not.zip")

    def test_an_entry_unpacking_would_skip_does_not_make_a_mod(self):
        archive = _zip(self.root / "m.zip", {"../Evil/modinfo.json": "{}", "Mod/data/x.xml": "<a/>"})
        self.assertEqual([mod.folder for mod in modzip.peek(archive)], ["Mod"])


class TestReplacing(_InTemp):
    def test_the_old_folder_is_moved_aside_and_the_new_mod_takes_its_name(self):
        old = self.mods / "-[Seal] Thing (Kurila)"
        (old / "data").mkdir(parents=True)
        (old / "modinfo.json").write_text('{"Version": "1"}', encoding="utf-8")
        (old / "data" / "big.mesh").write_bytes(b"x" * 100)
        archive = _zip(self.root / "m.zip", {"[Gameplay] Thing (Kurila)/modinfo.json": '{"Version": "2"}'})
        done = modzip.install(archive, self.mods, replace=old)
        # Same name as before, switched-off mark and all: the position in
        # load order and the switch are the user's, not the download's.
        self.assertEqual(done.placed, [old])
        self.assertEqual((old / "modinfo.json").read_text(encoding="utf-8"), '{"Version": "2"}')
        self.assertFalse((old / "data" / "big.mesh").exists())
        self.assertIsNotNone(done.set_aside)
        self.assertTrue((done.set_aside / "data" / "big.mesh").is_file())
        self.assertTrue(str(done.set_aside).startswith(str(self.root / "backups")))
        self.assertEqual([p.name for p in self.mods.iterdir()], ["-[Seal] Thing (Kurila)"])

    def test_a_box_replacing_one_folder_places_all_its_mods_under_their_own_names(self):
        old = self.mods / "Old"
        old.mkdir()
        (old / "modinfo.json").write_text("{}", encoding="utf-8")
        archive = _zip(self.root / "box.zip", {"Box/A/modinfo.json": "{}", "Box/B/modinfo.json": "{}"})
        done = modzip.install(archive, self.mods, replace=old)
        self.assertEqual([p.name for p in done.placed], ["A", "B"])
        self.assertFalse(old.exists())

    def test_a_folder_already_at_the_new_name_is_moved_aside_too(self):
        (self.mods / "Mod").mkdir()
        (self.mods / "Mod" / "old.txt").write_text("old", encoding="utf-8")
        archive = _zip(self.root / "m.zip", {"Mod/modinfo.json": "{}"})
        done = modzip.install(archive, self.mods)
        self.assertTrue((self.mods / "Mod" / "modinfo.json").is_file())
        self.assertFalse((self.mods / "Mod" / "old.txt").exists())
        self.assertTrue((done.set_aside / "old.txt").is_file())

    def test_setting_aside_twice_in_one_moment_does_not_collide(self):
        for name in ("A", "B"):
            (self.mods / name).mkdir()
        with mock.patch.object(modzip.time, "strftime", return_value="same-moment"):
            first = modzip.set_aside(self.mods / "A")
            os.rename(first, first.parent / "A")  # pretend both were called A
            (self.mods / "A").mkdir()
            second = modzip.set_aside(self.mods / "A")
        self.assertNotEqual(first.parent / "A", second)
        self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
