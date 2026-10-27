"""Converting to the library: the survey, the snapshot, profiles as loadouts,
and the move -- against temporary folders standing in for the mods folder,
the mod.io cache and the library."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import LibraryFolders, install_mod  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    convert,
    disk,
    library,
    switches,
    truth,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class TestTheSurvey(LibraryFolders):
    def test_one_row_a_modid_and_what_becomes_of_every_copy(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "2")
        self.cached(7, "[Gameplay] Mine (cache)", "mine", "1")       # shadowed by mine
        self.cached(8, "[Gameplay] Only Cached", "cached", "1")
        install_mod(self.mods, "-[Gameplay] Twin old", "twin", "1")     # off, loses to the newer
        install_mod(self.mods, "[Gameplay] Twin", "twin", "2")
        checkout = install_mod(self.mods, "[Gameplay] Dev", "dev", "1")
        (checkout / ".git").mkdir()
        library.install(self._archive("[Gameplay] Already"), self.library, self.mods)
        found = self.survey()
        rows = {row.name: row for row in found.rows}
        self.assertEqual(
            [(row.name, row.where) for row in found.rows],
            [("[Gameplay] Dev", "yours"), ("[Gameplay] Mine", "yours"), ("[Gameplay] Twin", "yours"),
             ("[Gameplay] Only Cached", "cache"), ("[Gameplay] Already", "library")],
        )
        self.assertEqual(rows["[Gameplay] Mine"].describe(),
                         "moved into the library; the cache's copy left to unsubscribe from")
        self.assertEqual(rows["[Gameplay] Twin"].describe(), "moved into the library; 1 other copy set aside")
        self.assertEqual(rows["[Gameplay] Only Cached"].describe(), "copied into the library")
        self.assertEqual(rows["[Gameplay] Dev"].describe(), "a working copy (it has .git): left where it is")
        self.assertEqual(rows["[Gameplay] Already"].describe(), "already in the library")
        self.assertFalse(rows["[Gameplay] Already"].to_do)
        self.assertFalse(rows["[Gameplay] Dev"].to_do)
        self.assertEqual(rows["[Gameplay] Only Cached"].modio_id, 8)

    def test_a_library_mod_with_a_duplicate_says_what_is_to_do(self):
        # The library's copy stays, but the row has work: the duplicate is set
        # aside, so it is offered checked and must not open "nothing to do".
        library.install(self._archive("[Gameplay] Already"), self.library, self.mods)
        install_mod(self.mods, "-[Gameplay] Already old", "already", "1")
        row, = self.survey().rows
        self.assertEqual(row.where, "library")
        self.assertTrue(row.to_do)
        self.assertEqual(row.describe(), "already in the library; 1 other copy set aside")

    def test_settings_are_said_so_they_can_be_seen_to_travel(self):
        mine = install_mod(self.mods, "[Gameplay] Tweaked", "tweaked", "1")
        (mine / "data" / "assets.xml").write_text('<ModOps><ImyaExpose ExposeID="x"/></ModOps>', encoding="utf-8")
        install_mod(self.mods, "[Gameplay] Plain", "plain", "1")
        found = truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library)
        self.assertEqual(convert.settings_of(found), {"tweaked": "iModYourAnno"})
        rows = {row.name: row.settings for row in convert.survey(found, convert.settings_of(found)).rows}
        self.assertEqual(rows["[Gameplay] Tweaked"], "iModYourAnno")

    def test_the_space_a_copy_needs_is_counted_and_a_rename_needs_none(self):
        self.cached(8, "[Gameplay] Only Cached", "cached", "1")
        (self.cache / "mods" / "8" / "[Gameplay] Only Cached" / "data" / "big.bin").write_bytes(b"x" * 5000)
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        # Exactly the cached copy: the move of Mine, on the library's drive, is
        # a rename, and counting it would add its bytes.
        need = convert.space_needed(self.survey().to_do, self.library)
        self.assertEqual(need, disk.folder_size(self.cache / "mods" / "8" / "[Gameplay] Only Cached"))

    def test_other_managers_are_named(self):
        install = self.root / "Anno 1800"
        (install / ".imya").mkdir(parents=True)
        self.assertIn("iModYourAnno is installed", convert.other_managers(ANNO_1800, install)[0])
        roaming = self.root / "roaming"
        (roaming / "Anno 117 Mod Manager").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"APPDATA": str(roaming)}):
            self.assertIn("Taludas' Anno 117 Mod Manager", convert.other_managers(ANNO_117, None)[0])
        self.assertEqual(convert.other_managers(ANNO_1800, self.root), [])

    def _archive(self, name: str) -> Path:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            zipped.writestr("%s/modinfo.json" % name, '{"ModID": "already", "Version": "1"}')
        path = self.root / "already.zip"
        path.write_bytes(buffer.getvalue())
        return path


class TestTheMove(LibraryFolders):
    def test_everything_goes_where_the_survey_said(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "2")
        self.cached(7, "[Gameplay] Mine", "mine", "1")
        cached = self.cached(8, "[Gameplay] Only Cached", "cached", "3")
        off = install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        install_mod(self.mods, "-[Gameplay] Twin old", "twin", "1")
        install_mod(self.mods, "[Gameplay] Twin", "twin", "2")
        found = self.survey()
        done = convert.convert(found.to_do, self.library, self.mods, ANNO_1800)
        self.assertEqual(done.failed, [])
        self.assertEqual(sorted(done.moved), ["[Gameplay] Mine", "[Gameplay] Off", "[Gameplay] Twin"])
        self.assertEqual(done.copied, ["[Gameplay] Only Cached"])
        self.assertEqual([name for name, _where in done.set_aside], ["[Gameplay] Twin"])
        # Links where the folders were; the off one off the library's way.
        for name in ("[Gameplay] Mine", "[Gameplay] Twin", "[Gameplay] Only Cached"):
            self.assertTrue(library.is_junction(self.mods / name), name)
        self.assertFalse(off.exists())
        self.assertIn("[Gameplay] Off", library.unlinked(self.library))
        # The cache is the client's: copied from, never touched -- and the
        # library knows exactly which mod.io file it holds.
        self.assertTrue((cached / "modinfo.json").is_file())
        self.assertFalse(library.is_junction(cached))
        self.assertEqual(library.registry(self.library)["[Gameplay] Only Cached"],
                         {"modio_id": 8, "modfile_id": 80, "version": "3", "name": "[Gameplay] Only Cached",
                          "source": library.CACHE})
        # And of a folder it moved in, that it holds the only copy.
        self.assertEqual(library.registry(self.library)["[Gameplay] Mine"],
                         {"source": library.ADOPTED, "version": "2"})
        # The duplicate is set aside, not deleted.
        (_name, aside), = done.set_aside
        self.assertTrue((aside / "modinfo.json").is_file())
        self.assertEqual(done.paths[str(cached)], str(self.mods / "[Gameplay] Only Cached"))
        self.assertEqual(done.describe(),
                         "Converted: moved 3 into the library, copied 1 out of the game's mod browser, "
                         "set 1 duplicate aside.")
        # And the game loads what it loaded before.
        after = truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library)
        self.assertEqual(sorted(mod.name for mod in after.loading),
                         ["[Gameplay] Mine", "[Gameplay] Only Cached", "[Gameplay] Twin"])

    def test_the_snapshot_is_kept_and_the_proof_compares_with_it(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Gone", "gone", "1")
        before = truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library)
        record = convert.snapshot(before, {})
        path = convert.save_snapshot(record, self.library)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["loading"], ["gone", "mine"])
        (self.mods / "[Gameplay] Gone").rename(self.mods / "-[Gameplay] Gone")
        install_mod(self.mods, "[Gameplay] New", "new", "1")
        after = truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library)
        self.assertEqual(convert.compare(record, after), (["[Gameplay] Mine"], ["[Gameplay] New"], ["[Gameplay] Gone"]))


def _listing(folder: Path) -> dict:
    """Every file under ``folder`` and its bytes, by path -- what "as it was"
    means for a mods folder."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(Path(folder).rglob("*")) if path.is_file()
    }


class TestUndoingIt(LibraryFolders):
    """Undone, the folders are as they were -- every name, every byte -- and
    the library holds none of it."""

    def build(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "2")
        self.cached(7, "[Gameplay] Mine", "mine", "1")
        self.cached(8, "[Gameplay] Only Cached", "cached", "3")
        install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        install_mod(self.mods, "-[Gameplay] Twin old", "twin", "1")
        install_mod(self.mods, "[Gameplay] Twin", "twin", "2")
        checkout = install_mod(self.mods, "[Gameplay] Dev", "dev", "1")
        (checkout / ".git").mkdir()
        (checkout / ".git" / "HEAD").write_text("ref", encoding="utf-8")

    def convert_and_record(self):
        rows = self.survey().to_do
        pinned = convert.pin_before(ANNO_1800, self.mods, rows)
        done = convert.convert(rows, self.library, self.mods, ANNO_1800)
        self.assertEqual(done.failed, [])
        record = convert.snapshot(truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library))
        path = convert.save_snapshot(record, self.library)
        record["journal"] = {"steps": done.steps, "backups": pinned, "paths": done.paths}
        convert.write_record(path, record)
        return path, done

    def test_the_mods_folder_and_the_cache_are_as_they_were(self):
        self.build()
        before_mods, before_cache = _listing(self.mods), _listing(self.cache)
        path, _done = self.convert_and_record()
        self.assertNotEqual(_listing(self.mods), before_mods)
        self.assertEqual(convert.last_undoable(self.library), path)
        undone = convert.undo(path, self.library)
        self.assertEqual(undone.failed, [])
        self.assertEqual(_listing(self.mods), before_mods)
        self.assertEqual(_listing(self.cache), before_cache)
        # The off one is off again, the game's way, and nothing is a link.
        self.assertTrue((self.mods / "-[Gameplay] Off").is_dir())
        self.assertFalse(any(library.is_junction(entry) for entry in self.mods.iterdir()))
        # The library holds none of it, and does not think it does.
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir() and p.name != "conversions"], [])
        self.assertEqual(library.links(self.library), {})
        self.assertEqual(library.registry(self.library), {})
        self.assertEqual((undone.put_back, undone.removed), (4, 1))
        # Once.
        self.assertIsNone(convert.last_undoable(self.library))

    def test_a_patch_rewritten_since_is_put_back_from_the_pinned_backup(self):
        self.build()
        patch = self.mods / "[Gameplay] Mine" / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps>as shipped</ModOps>", encoding="utf-8")
        path, _done = self.convert_and_record()
        linked = self.mods / "[Gameplay] Mine" / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        linked.write_text("<ModOps>rewritten by Play</ModOps>", encoding="utf-8")
        convert.undo(path, self.library)
        self.assertEqual(patch.read_text(encoding="utf-8"), "<ModOps>as shipped</ModOps>")
        # And the backup it came from survives pruning.
        from anno_modding_studio.core import backup

        self.assertTrue(all(kept.pinned for kept in backup.saves(self.mods)
                            if kept.label == convert.BEFORE))

    def test_a_folder_whose_place_was_taken_since_is_left_and_said(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        path, _done = self.convert_and_record()
        library.unlink(self.library, self.mods / "[Gameplay] Mine")
        install_mod(self.mods, "[Gameplay] Mine", "mine", "9")   # somebody's new copy
        undone = convert.undo(path, self.library)
        self.assertEqual([name for name, _why in undone.failed], ["[Gameplay] Mine"])
        self.assertIn("could not be undone", undone.describe())
        self.assertIn('"9"', (self.mods / "[Gameplay] Mine" / "modinfo.json").read_text(encoding="utf-8"))


class TestWhereTheConversionsMovedALoadoutsMods(LibraryFolders):
    """What a loadout's file should say of its mods' folders, from the records
    of the conversions not undone that name it."""

    def record(self, stamp: str, loadout, paths, undone=False):
        folder = self.library / "conversions"
        folder.mkdir(parents=True, exist_ok=True)
        record = {"journal": {"loadout": str(loadout), "paths": paths}}
        if undone:
            record["undone"] = 1
        (folder / ("%s.json" % stamp)).write_text(json.dumps(record), encoding="utf-8")

    def test_one_after_another_and_only_the_ones_that_name_it_and_stand(self):
        main = self.root / "Main.annoload"
        self.record("2026-01-01_00-00-00", main, {"cache/One": "mods/One", "mods/Two": "mods/Two"})
        self.record("2026-01-02_00-00-00", main, {"mods/One": "mods/One (2)"})
        self.record("2026-01-03_00-00-00", self.root / "Other.annoload", {"mods/Three": "x"})
        self.record("2026-01-04_00-00-00", main, {"mods/Four": "y"}, undone=True)
        self.assertEqual(convert.moved_folders(self.library, main),
                         {"cache/One": "mods/One (2)", "mods/One": "mods/One (2)"})


class TestANewerCacheCopyUnderYourFoldersName(LibraryFolders):
    """The cache's newer copy loads, and the user's older one has the same
    folder name: the link goes where the older one was, and the older one is
    set aside -- once, and never the link.  A rehearsal on a real folder
    found three such mods, each left loading from the cache."""

    def test_the_link_stays_and_the_duplicate_is_set_aside_once(self):
        install_mod(self.mods, "[Gameplay] Sockets", "sockets", "1")
        self.cached(9, "[Gameplay] Sockets", "sockets", "2")
        before = _listing(self.mods)
        rows = self.survey().to_do
        done = convert.convert(rows, self.library, self.mods, ANNO_1800)
        self.assertEqual(done.failed, [])
        link = self.mods / "[Gameplay] Sockets"
        self.assertTrue(library.is_junction(link))
        self.assertTrue(disk.same_file(library.target_of(link), self.library / "[Gameplay] Sockets"))
        self.assertEqual([op["op"] for op in done.steps], ["aside", "copy"])
        (_name, aside), = done.set_aside
        self.assertFalse(library.is_junction(aside))
        self.assertTrue((aside / "modinfo.json").is_file())
        after = truth.read(Loadout(game=ANNO_1800), self.mods, library_dir=self.library)
        (loading,) = after.loading
        self.assertTrue(loading.installed.kind)  # the library's copy, not the cache's
        record = convert.snapshot(after)
        path = convert.save_snapshot(record, self.library)
        record["journal"] = {"steps": done.steps}
        convert.write_record(path, record)
        self.assertEqual(convert.undo(path, self.library).failed, [])
        self.assertEqual(_listing(self.mods), before)


class TestACacheModInItsNumberedFolder(LibraryFolders):
    def test_it_is_copied_in_under_its_name(self):
        root = self.cache / "mods"
        install_mod(root, "3813676", "storage", "1", "Building storage improvements")
        rows = self.survey().to_do
        self.assertEqual([(row.name, row.where) for row in rows], [("Building storage improvements", convert.CACHE)])
        done = convert.convert(rows, self.library, self.mods, ANNO_1800)
        self.assertEqual(done.failed, [])
        self.assertTrue(library.is_junction(self.mods / "Building storage improvements"))


class TestAFullCopy(LibraryFolders):
    def test_the_folder_whole_and_not_what_its_links_point_at(self):
        mine = install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        (mine / "data" / "big.bin").write_bytes(b"x" * 3000)
        library.install(TestTheSurvey._archive(self, "[Gameplay] Already"), self.library, self.mods)
        rows = self.survey().to_do
        self.assertEqual(convert.folders_to_copy(rows), [self.mods])
        size = convert.size_of([self.mods])
        seen = []
        root = convert.full_copy([self.mods], into=self.root / "full", progress=lambda d, t, n: seen.append((d, t)))
        import zipfile

        copied = self.root / "extracted"
        with zipfile.ZipFile(root / "1-mods.zip") as archive:
            archive.extractall(copied)
        self.assertEqual(_listing(copied), {k: v for k, v in _listing(self.mods).items()
                                            if not k.startswith("[Gameplay] Already")})
        self.assertFalse((copied / "[Gameplay] Already").exists())
        # Compressed: the big file of one letter is next to nothing zipped.
        self.assertLess((root / "1-mods.zip").stat().st_size, 1500)
        self.assertEqual(seen[-1], (size, size))
        note = json.loads((root / "full-copy.json").read_text(encoding="utf-8"))
        self.assertEqual(note["folders"][0]["source"], str(self.mods))

    def test_stopped_it_leaves_nothing(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        with self.assertRaises(convert.Stopped):
            convert.full_copy([self.mods], into=self.root / "full", stopped=lambda: True)
        self.assertEqual(list((self.root / "full").glob("*")), [])


class TestUndoingOn117(LibraryFolders):
    game = ANNO_117

    def test_the_profile_and_the_options_are_put_back(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        pointer = self.mods / switches.POINTER_FILE
        pointer.write_text("default\n", encoding="utf-8")
        profiles = self.mods / switches.PROFILE_DIR
        profiles.mkdir()
        (profiles / "default.txt").write_text("mine\n", encoding="utf-8")
        options = self.root / "options" / switches.OPTIONS_FILE
        with mock.patch.object(switches, "options_file", return_value=options):
            rows = self.survey().to_do
            pinned = convert.pin_before(ANNO_117, self.mods, rows)
            record = convert.snapshot(truth.read(Loadout(game=ANNO_117), self.mods, library_dir=self.library))
            path = convert.save_snapshot(record, self.library)
            done = convert.convert(rows, self.library, self.mods, ANNO_117)
            record["journal"] = {"steps": done.steps, "backups": pinned}
            convert.write_record(path, record)
            # What Play writes when it makes the loadout live.
            studio = profiles / (switches.STUDIO_PROFILE + ".txt")
            studio.write_text("the Studio's", encoding="utf-8")
            pointer.write_text(switches.STUDIO_PROFILE + "\n", encoding="utf-8")
            options.parent.mkdir(parents=True)
            options.write_text("{}", encoding="utf-8")
            undone = convert.undo(path, self.library)
        self.assertEqual(undone.failed, [])
        self.assertEqual(pointer.read_text(encoding="utf-8"), "default\n")
        self.assertFalse(studio.exists())
        self.assertFalse(options.exists())
        self.assertTrue((self.mods / "[Gameplay] Mine" / "modinfo.json").is_file())


class TestProfilesAsLoadouts(LibraryFolders):
    def loadout(self):
        loadout = Loadout(game=self.game)
        loadout.layers = [
            ModLayer(origin="[Gameplay] One", mod_id="one"),
            ModLayer(origin="[Gameplay] Two", mod_id="two"),
            ModLayer(origin="[Gameplay] One/shared/Piece", mod_id="piece"),
        ]
        return loadout

    def test_imodyouranno_lists_folders(self):
        install = self.root / "Anno 1800"
        profiles = install / ".imya" / "profiles"
        profiles.mkdir(parents=True)
        (profiles / "Hard game.imyaprofile").write_text("[Gameplay] One\n-[Gameplay] Gone\n", encoding="utf-8")
        (found,) = convert.profiles(ANNO_1800, install, self.mods)
        self.assertEqual((found.name, found.source, found.on), ("Hard game", "iModYourAnno", {"[gameplay] one", "[gameplay] gone"}))
        loadout = self.loadout()
        switched, missing = convert.profile_switches(loadout, found)
        self.assertEqual(switched,
                         {"[Gameplay] One": True, "[Gameplay] Two": False, "[Gameplay] One/shared/Piece": True})
        self.assertEqual(missing, ["[gameplay] gone"])


class TestProfilesOn117(LibraryFolders):
    game = ANNO_117

    def test_117_profiles_by_modid_leaving_out_the_studios_and_backups(self):
        folder = self.mods / "profiles"
        folder.mkdir()
        (folder / "Romans.txt").write_text("# EnableNewMods\none\n# two\n", encoding="utf-8")
        (folder / "default.txt.bak.txt").write_text("one\n", encoding="utf-8")
        switches.write_profile(self.mods, switches.STUDIO_PROFILE, "x", {"one": True}, frozen=True)
        # And one of the Studio's that something else wrote again since,
        # without the line that says whose it is: known by its name.
        (folder / "anno modding studio (2).txt").write_text("# EnableNewMods\none\n", encoding="utf-8")
        (found,) = convert.profiles(ANNO_117, None, self.mods)
        self.assertEqual((found.name, found.on, found.unlisted_on), ("Romans", {"one"}, False))
        loadout = Loadout(game=ANNO_117)
        loadout.layers = [ModLayer(origin="One", mod_id="one"), ModLayer(origin="Two", mod_id="two"),
                          ModLayer(origin="Three", mod_id="three")]
        switched, _missing = convert.profile_switches(loadout, found)
        self.assertEqual([switched[layer.origin] for layer in loadout.layers], [True, False, False])


class TestTheSurveyDialog(LibraryFolders):
    def test_rows_to_do_are_ticked_and_unticking_leaves_a_mod_where_it_is(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.convert_dialog import ConvertDialog

        QApplication.instance() or QApplication([])
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Other", "other", "1")
        self.cached(8, "[Gameplay] Only Cached", "cached", "1")
        found = self.survey()
        profile = convert.FoundProfile(name="Hard game", source="iModYourAnno", path=self.root / "x", on={"a"})
        dialog = ConvertDialog(found, found_profiles=[profile], managers=["iModYourAnno is installed"])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(sorted(row.name for row in dialog.chosen_rows()),
                         ["[Gameplay] Mine", "[Gameplay] Only Cached", "[Gameplay] Other"])
        self.assertEqual(dialog.go.text(), "&Convert 3 mods")
        first = dialog.tree.topLevelItem(0).child(0)
        first.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(len(dialog.chosen_rows()), 2)
        self.assertEqual(dialog.go.text(), "&Convert 2 mods")
        self.assertEqual([p.name for p in dialog.chosen_profiles()], ["Hard game"])
        # One the loadout has by name already is listed, and not ticked.
        profile.here = True
        again = ConvertDialog(found, found_profiles=[profile])
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.chosen_profiles(), [])
        self.assertEqual(again.profile_list.item(0).text(),
                         "Hard game (iModYourAnno, 1 mod on), already a profile of this loadout")
        profile.here = False
        # Opened from one row of the checklist: that place alone, no profiles.
        only = ConvertDialog(found, only={convert.CACHE}, found_profiles=[profile])
        self.addCleanup(only.deleteLater)
        self.assertEqual([row.name for row in only.chosen_rows()], ["[Gameplay] Only Cached"])
        self.assertEqual(only.chosen_profiles(), [])


if __name__ == "__main__":
    unittest.main()


class TestTheFullCopyOffer(LibraryFolders):
    def dialog(self, **kwargs):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.convert_dialog import ConvertDialog

        QApplication.instance() or QApplication([])
        if not (self.mods / "[Gameplay] Mine").exists():
            install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        made = ConvertDialog(self.survey(), full_folders=[self.mods], **kwargs)
        self.addCleanup(made.deleteLater)
        return made

    def test_ticked_on_a_first_conversion_and_not_after(self):
        first = self.dialog(full_size=2_400_000_000, full_free=237_000_000_000, full_default=True)
        self.assertFalse(first.full_box.isHidden())
        self.assertIn("kept for good", first.full_note.text())
        self.assertTrue(first.keep_full_copy())
        later = self.dialog(full_size=2_400_000_000, full_free=237_000_000_000, full_default=False)
        self.assertFalse(later.keep_full_copy())

    def test_not_offered_without_room_or_without_anything_to_copy(self):
        tight = self.dialog(full_size=2_400_000_000, full_free=1_000_000_000, full_default=True)
        self.assertFalse(tight.full_box.isEnabled())
        self.assertIn("not enough room", tight.full_note.text())
        self.assertFalse(tight.keep_full_copy())
        nothing = self.dialog(full_size=0, full_default=True)
        self.assertTrue(nothing.full_box.isHidden())
        self.assertFalse(nothing.keep_full_copy())
