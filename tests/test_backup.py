"""Keeping a copy of what an export is about to write over.

What the Studio exports is its *reading* of a mod, not the mod.  The reading is
checked, but a mod nobody has tested it against can come back subtly different --
an op it could not narrow, a value it settled that the mod meant to compute -- and
somebody who notices their game behaving differently should be able to put the
mod back without downloading it again.

The whole folder is far too big to copy (2.4 GB in the one this was written
against, nearly all meshes and textures).  The files the Studio can actually
overwrite are 4 MB of it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import backup  # noqa: E402

PATCH = "data/config/export/main/asset/assets.xml"


class BackupCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods = Path(self._tmp.name) / "mods"
        self.store = Path(self._tmp.name) / "backups"
        self.mods.mkdir()

    def mod(self, name, ops="<ModOps/>", inner=None, texture=True):
        root = self.mods / name
        patch = root / PATCH
        patch.parent.mkdir(parents=True)
        patch.write_text(ops, encoding="utf-8")
        (root / "modinfo.json").write_text('{"ModID": "%s"}' % name, encoding="utf-8")
        if texture:
            big = root / "data/graphics/building.dds"
            big.parent.mkdir(parents=True)
            big.write_bytes(b"\x00" * 4096)
        if inner:
            nested = root / inner / PATCH
            nested.parent.mkdir(parents=True)
            nested.write_text("<ModOps/>", encoding="utf-8")
            (root / inner / "modinfo.json").write_text("{}", encoding="utf-8")
        return root


class TestWhatIsWorthCopying(BackupCase):
    def test_the_modops_and_the_modinfo_are(self):
        self.mod("A Mod")
        kept = [p.as_posix() for p in backup.at_risk(self.mods / "A Mod")]
        self.assertIn(PATCH, kept)
        self.assertIn("modinfo.json", kept)

    def test_the_textures_and_meshes_are_not(self):
        """They are almost all of a real mods folder and the Studio cannot
        touch them, so copying them would make the backup useless in size for
        no gain in safety."""
        self.mod("A Mod")
        kept = [p.as_posix() for p in backup.at_risk(self.mods / "A Mod")]
        self.assertNotIn("data/graphics/building.dds", kept)

    def test_a_bundled_sub_mod_s_files_count_too(self):
        """Covered twice over when that mod is being exported in its own right,
        and once when it is not -- which is the way round to be wrong."""
        self.mod("A Mod", inner="sub")
        kept = [p.as_posix() for p in backup.at_risk(self.mods / "A Mod")]
        self.assertIn("sub/" + PATCH, kept)
        self.assertIn("sub/modinfo.json", kept)

    def test_the_texts_files_are(self):
        """An export writes the names of the assets a mod adds into them, and
        takes its block out again: they were written over with no copy."""
        root = self.mod("A Mod")
        for inside in ("data/config/gui/texts_english.xml", "sub/data/base/config/gui/texts_german.xml",
                       "data/config/gui/notes.xml", "texts_english.xml"):
            (root / inside).parent.mkdir(parents=True, exist_ok=True)
            (root / inside).write_text("<ModOps/>", encoding="utf-8")
        kept = [p.as_posix() for p in backup.at_risk(root)]
        self.assertIn("data/config/gui/texts_english.xml", kept)
        self.assertIn("sub/data/base/config/gui/texts_german.xml", kept)
        self.assertNotIn("data/config/gui/notes.xml", kept)
        self.assertNotIn("texts_english.xml", kept)

    def test_a_modinfo_written_with_comments_counts(self):
        root = self.mod("A Mod")
        (root / "modinfo.jsonc").write_text("// x\n{}", encoding="utf-8")
        kept = [p.as_posix() for p in backup.at_risk(root)]
        self.assertIn("modinfo.jsonc", kept)


class TestTakingAndPuttingBack(BackupCase):
    def take(self, *mods, **kwargs):
        return backup.take(
            self.mods, mods or ["A Mod"], into=self.store, game="anno1800", **kwargs
        )

    def test_a_save_records_what_it_copied(self):
        self.mod("A Mod")
        kept = self.take()
        self.assertEqual(kept.mods, ["A Mod"])
        self.assertEqual(len(kept.files), 2)
        self.assertIn("2 files from 1 mod", kept.describe())
        self.assertTrue((kept.root / backup.MANIFEST).is_file())

    def test_and_can_be_found_again_afterwards(self):
        self.mod("A Mod")
        made = self.take()
        found = backup.saves(self.mods, into=self.store)
        self.assertEqual([kept.root for kept in found], [made.root])

    def test_restoring_puts_the_original_bytes_back(self):
        self.mod("A Mod", ops="<ModOps><!-- theirs --></ModOps>")
        kept = self.take()
        (self.mods / "A Mod" / PATCH).write_text("<ModOps><!-- ours --></ModOps>",
                                                 encoding="utf-8")
        put, failed = backup.restore(kept)
        self.assertEqual((put, failed), (2, []))
        self.assertIn(
            "theirs", (self.mods / "A Mod" / PATCH).read_text(encoding="utf-8")
        )

    def test_and_touches_nothing_else_in_the_folder(self):
        """The same promise the export it undoes makes."""
        root = self.mod("A Mod")
        kept = self.take()
        (root / "data/graphics/building.dds").write_bytes(b"\x01" * 8)
        backup.restore(kept)
        self.assertEqual((root / "data/graphics/building.dds").read_bytes(), b"\x01" * 8)

    def test_a_bundled_sub_mod_comes_back_too(self):
        self.mod("A Mod", inner="sub")
        kept = self.take()
        (self.mods / "A Mod" / "sub" / PATCH).write_text("<ModOps>x</ModOps>",
                                                         encoding="utf-8")
        backup.restore(kept)
        self.assertEqual(
            (self.mods / "A Mod" / "sub" / PATCH).read_text(encoding="utf-8"),
            "<ModOps/>",
        )

    def test_the_copies_are_kept_flat_rather_than_nested(self):
        """Mirroring the folders would be tidier and would break: a mod with a
        sub-mod inside it puts two long names and two copies of
        data/config/export/main/asset in one path, and Windows gives up at 260
        characters."""
        self.mod("A Mod", inner="a rather long bundled sub-mod name")
        kept = self.take()
        deepest = max(len(path.relative_to(kept.root).parts)
                      for path in kept.root.rglob("*") if path.is_file())
        self.assertEqual(deepest, 1)

    def test_a_file_outside_any_mod_can_be_saved_too(self):
        """Anno 117's mod profile: one text file naming every mod that is on."""
        self.mod("A Mod")
        profile = self.mods / "profiles" / "default.txt"
        profile.parent.mkdir()
        profile.write_text("a-mod\n", encoding="utf-8")
        kept = self.take(extra=[profile])
        self.assertIn("profiles/default.txt", [f["path"] for f in kept.files])
        profile.write_text("# a-mod\n", encoding="utf-8")
        backup.restore(kept)
        self.assertEqual(profile.read_text(encoding="utf-8"), "a-mod\n")

    def test_a_file_the_export_creates_is_taken_back_off_again(self):
        """A mod carrying a modinfo.jsonc gets a modinfo.json written beside it.
        Putting the original back and leaving the new one there would leave the
        mod with two, which is a different mod again."""
        root = self.mods / "A Mod"
        (root / PATCH).parent.mkdir(parents=True)
        (root / PATCH).write_text("<ModOps/>", encoding="utf-8")
        (root / "modinfo.jsonc").write_text("// x\n{}", encoding="utf-8")
        kept = self.take(writing=[PATCH, "modinfo.json"])
        (root / "modinfo.json").write_text("{}", encoding="utf-8")  # the export
        backup.restore(kept)
        self.assertFalse((root / "modinfo.json").exists())
        self.assertTrue((root / "modinfo.jsonc").is_file())

    def test_but_one_that_was_already_there_is_only_put_back(self):
        self.mod("A Mod")
        kept = self.take(writing=[PATCH, "modinfo.json"])
        (self.mods / "A Mod" / "modinfo.json").write_text("{}", encoding="utf-8")
        backup.restore(kept)
        self.assertIn(
            "A Mod",
            (self.mods / "A Mod" / "modinfo.json").read_text(encoding="utf-8"),
        )

    def test_nothing_to_copy_makes_no_save_at_all(self):
        self.assertIsNone(self.take("Not Installed"))
        self.assertEqual(backup.saves(self.mods, into=self.store), [])

    def test_two_saves_in_the_same_second_are_two_saves(self):
        """One mod, then the rest, is an ordinary thing to do -- and the second
        must not land on top of the first."""
        self.mod("A Mod")
        made = [self.take() for _ in range(3)]
        self.assertEqual(len({kept.root for kept in made}), 3)
        self.assertEqual(len(backup.saves(self.mods, into=self.store)), 3)

    def test_only_so_many_saves_are_kept_and_the_first_copy_of_every_file_for_good(self):
        self.mod("A Mod")
        made = [backup.take(self.mods, ["A Mod"], into=self.store, game="anno1800") for _ in range(4)]
        backup.prune(self.mods, into=self.store, keep=2)
        left = backup.saves(self.mods, into=self.store)
        # The newest two, and the first: what the files were before the Studio
        # first wrote them.
        self.assertEqual([kept.root for kept in left], [made[3].root, made[2].root, made[0].root])
        self.assertTrue(left[-1].first)
        self.assertIn("kept for good", left[-1].describe())
        self.assertFalse(left[0].kept_for_good)

    def test_a_mod_first_copied_later_keeps_that_save_too(self):
        self.mod("A Mod")
        self.mod("B Mod")
        first = backup.take(self.mods, ["A Mod"], into=self.store)
        backup.take(self.mods, ["A Mod"], into=self.store)
        b_first = backup.take(self.mods, ["B Mod"], into=self.store)
        for _ in range(3):
            backup.take(self.mods, ["A Mod", "B Mod"], into=self.store)
        backup.prune(self.mods, into=self.store, keep=1)
        left = {kept.root for kept in backup.saves(self.mods, into=self.store)}
        self.assertIn(first.root, left)
        self.assertIn(b_first.root, left)
        self.assertEqual(len(left), 3)

    def test_a_pinned_save_is_never_pruned(self):
        self.mod("A Mod")
        backup.take(self.mods, ["A Mod"], into=self.store)
        pinned = backup.take(self.mods, ["A Mod"], into=self.store, pinned=True, label="before converting")
        for _ in range(3):
            backup.take(self.mods, ["A Mod"], into=self.store)
        backup.prune(self.mods, into=self.store, keep=1)
        left = backup.saves(self.mods, into=self.store)
        self.assertIn(pinned.root, [kept.root for kept in left])
        self.assertTrue(backup.read(pinned.root).pinned)

    def test_a_profile_outside_the_mods_folder_goes_back_where_it_lives(self):
        """Anno 117's profile sits under Documents while the mods are in the
        install. Only its name used to be kept, and restoring wrote it into
        the mods folder, beside nothing that reads it."""
        profiles = Path(self._tmp.name) / "documents" / "mods" / "profiles"
        profiles.mkdir(parents=True)
        profile = profiles / "default.txt"
        profile.write_text("mine\n", encoding="utf-8")
        kept = backup.take(self.mods, [], into=self.store, extra=[profile], label="before switching mods")
        profile.write_text("# mine\n", encoding="utf-8")
        put, failed = backup.restore(kept)
        self.assertEqual((put, failed), (1, []))
        self.assertEqual(profile.read_text(encoding="utf-8"), "mine\n")
        self.assertFalse((self.mods / "default.txt").exists())

    def test_an_older_save_that_only_knew_the_name_says_so_rather_than_guessing(self):
        kept = backup.take(self.mods, [], into=self.store, extra=[self._outside("default.txt")], label="x")
        kept.files[0].pop("at")
        put, failed = backup.restore(kept)
        self.assertEqual(put, 0)
        self.assertIn("does not record where the file lives", failed[0])
        self.assertFalse((self.mods / "default.txt").exists())

    def test_a_manifest_naming_somewhere_else_is_still_refused(self):
        kept = backup.take(self.mods, [], into=self.store,
                           extra=[self._outside("default.txt")], label="x")
        entry = kept.files[0]
        for bad in ("evil.dll", "../default.txt"):
            entry["at"] = str(Path(self._tmp.name) / "elsewhere" / bad)
            entry["path"] = Path(bad).name
            put, failed = backup.restore(kept)
            self.assertEqual(put, 0, bad)
            self.assertTrue(failed, bad)

    def _outside(self, name):
        folder = Path(self._tmp.name) / "outside"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text("x", encoding="utf-8")
        return path

    def test_a_file_outside_the_mods_that_is_about_to_be_made_is_removed_on_restore(self):
        profile = self.mods / "profiles" / "Anno Modding Studio.txt"
        kept = backup.take(self.mods, [], into=self.store, creating=[profile])
        profile.parent.mkdir(parents=True)
        profile.write_text("written later", encoding="utf-8")
        backup.restore(kept)
        self.assertFalse(profile.exists())

    def test_two_mods_folders_do_not_share_a_store(self):
        self.mod("A Mod")
        other = Path(self._tmp.name) / "other mods"
        (other / "A Mod").mkdir(parents=True)
        self.take()
        self.assertEqual(len(backup.saves(self.mods, into=self.store)), 1)
        self.assertEqual(backup.saves(other, into=self.store), [])


class TestRestoreStaysWhereItSaidItWould(BackupCase):
    """restore() writes files and deletes them.

    Where it does that is read out of a manifest -- a JSON file on disk like
    any other -- and joining a path in pathlib does not contain it: an absolute
    right-hand side replaces the left outright, and ``..`` walks back out.
    """

    def _kept(self, entries):
        return backup.Kept(
            root=Path(self.store) / "save1",
            when=0.0,
            label="",
            mods_folder=str(self.mods),
            game="",
            files=entries,
        )

    def test_an_entry_pointing_outside_the_mods_folder_is_refused(self):
        outside = Path(self._tmp.name) / "outside.txt"
        outside.write_text("do not touch me", encoding="utf-8")
        kept = self._kept([
            {"mod": "", "path": str(outside), "copy": "c0", "existed": True},
            {"mod": "M", "path": "../../outside.txt", "copy": "c1", "existed": True},
        ])
        put, failed = backup.restore(kept)
        self.assertEqual(put, 0)
        self.assertEqual(len(failed), 2)
        for line in failed:
            self.assertIn("not inside the mods folder", line)
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not touch me")

    def test_an_entry_pointing_outside_is_not_deleted_either(self):
        """The `existed: False` branch unlinks, which is the worse half."""
        outside = Path(self._tmp.name) / "keepme.txt"
        outside.write_text("still here", encoding="utf-8")
        kept = self._kept([
            {"mod": "", "path": str(outside), "copy": "c0", "existed": False},
        ])
        put, failed = backup.restore(kept)
        self.assertEqual(put, 0)
        self.assertTrue(outside.is_file(), "restore deleted a file outside the folder")
        self.assertIn("not inside the mods folder", failed[0])


class TestAModMovedIntoTheLibrarySince(BackupCase):
    """A save from before the conversion, opened after it: the mod's folder
    in the mods folder is a link into the library now.  Resolved, every one
    of its files read as outside the mods folder, and each was refused with
    that, 42 lines of it for a 20-mod save -- and nothing went back."""

    def setUp(self):
        super().setUp()
        self.library = Path(self._tmp.name) / "library"
        self.library.mkdir()

    def move_in(self, name):
        from anno_modding_studio.core import library

        moved = self.library / name
        (self.mods / name).rename(moved)
        library.make_link(self.mods / name, moved)
        return moved

    def test_it_is_left_as_it_is_and_said_once(self):
        self.mod("A Mod", ops="<ModOps><!-- theirs --></ModOps>")
        kept = backup.take(self.mods, ["A Mod"], into=self.store)
        moved = self.move_in("A Mod")
        (moved / PATCH).write_text("<ModOps><!-- the library's --></ModOps>", encoding="utf-8")
        self.assertEqual(backup.linked_now(kept), ["A Mod"])
        put, failed = backup.restore(kept)
        self.assertEqual(put, 0)
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("A Mod", failed[0])
        self.assertIn("in the library now", failed[0])
        self.assertIn("the library's", (moved / PATCH).read_text(encoding="utf-8"))

    def test_the_rest_of_the_save_is_still_restored(self):
        self.mod("Moved", ops="<ModOps><!-- moved --></ModOps>")
        self.mod("Stayed", ops="<ModOps><!-- theirs --></ModOps>")
        kept = backup.take(self.mods, ["Moved", "Stayed"], into=self.store)
        self.move_in("Moved")
        (self.mods / "Stayed" / PATCH).write_text("<ModOps><!-- ours --></ModOps>", encoding="utf-8")
        put, failed = backup.restore(kept)
        self.assertEqual(put, 2)
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("Moved", failed[0])
        self.assertIn("theirs", (self.mods / "Stayed" / PATCH).read_text(encoding="utf-8"))

    def test_once_it_is_a_folder_again_it_restores(self):
        """What undoing the conversion, or putting the library back, makes of it."""
        from anno_modding_studio.core import library

        self.mod("A Mod", ops="<ModOps><!-- theirs --></ModOps>")
        kept = backup.take(self.mods, ["A Mod"], into=self.store)
        moved = self.move_in("A Mod")
        (moved / PATCH).write_text("<ModOps><!-- ours --></ModOps>", encoding="utf-8")
        library.remove_link(self.mods / "A Mod")
        moved.rename(self.mods / "A Mod")
        self.assertEqual(backup.linked_now(kept), [])
        self.assertEqual(backup.restore(kept), (2, []))
        self.assertIn("theirs", (self.mods / "A Mod" / PATCH).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
