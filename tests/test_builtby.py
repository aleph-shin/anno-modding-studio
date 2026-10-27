"""The library records which project built a copy, and one project builds it.

``docs/proposals/projects-and-loadouts-proposal.md``, section 7: a built
copy's registry entry names the project, ModID and build it was built from;
a build into a folder another project built is stopped and says which; the
loadout's own mod renamed moves its folder rather than building a second;
loadouts that have it follow it by ModID; and a project saved under another
name takes the record with it.

Against temporary libraries, as ``tests/test_library.py`` is.  Junctions are
a Windows thing, as is the game, so the suite is too.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    build,
    builtby,
    generator,
    histories,
    library,
    patching,
    places,
    play,
    projects,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    Op,
    own_record,
)

FOLDER = "[Gameplay] My Mod"


def _change(origin: str = "", **kw) -> Change:
    base = {
        "guid": "1010340", "asset_label": "Lumberjack", "path": "/Values/Factory7/CycleTime",
        "tag": "CycleTime", "old_value": "30.00", "new_value": "15.00", "origin": origin,
        "authored": True,
    }
    base.update(kw)
    return Change(**base)


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class _Library(unittest.TestCase):
    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.library = self.root / "library" / "anno1800"
        patched = mock.patch.object(places, "backups_dir", return_value=self.root / "backups")
        patched.start()
        self.addCleanup(patched.stop)

    def own(self, name="My Mod", mod_id="me-my-mod") -> Loadout:
        loadout = Loadout(game="anno1800", own=own_record(name=name, creator="me", mod_id=mod_id), own_mod=True, is_the_mod=True)
        loadout.set_change(_change())
        return loadout

    def built(self, loadout, origins=("",), folder_of=None) -> build.Built:
        done = build.write(loadout, list(origins), self.mods, self.library, folder_of=folder_of)
        return done

    def saved(self, loadout, name="Main") -> Path:
        path = self.root / ("%s.annoload" % name)
        loadout.save(path)
        return path

    def entry(self, name=FOLDER) -> dict:
        return library.registry(self.library)[name]


class TestABuiltCopyRecordsItsProject(_Library):
    def test_its_project_mod_id_and_build_are_in_the_registry(self):
        loadout = self.own()
        self.saved(loadout)
        done = self.built(loadout)
        self.assertEqual(done.failed, [])
        entry = self.entry()
        self.assertEqual(entry["source"], library.BUILT)
        self.assertEqual(entry[library.PROJECT], loadout.projects[""].path)
        self.assertEqual(entry[library.PROJECT], projects.as_written(projects.where(loadout.projects[""].path)))
        self.assertEqual(entry[library.MOD_ID], "me-my-mod")
        self.assertEqual(entry[library.BUILD], loadout.builds[""]["build"])

    def test_the_project_is_not_in_the_mod(self):
        """The modinfo goes to mod.io and to other people: a path on this
        PC has no business there."""
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        modinfo = (self.library / FOLDER / "modinfo.json").read_text(encoding="utf-8")
        self.assertNotIn(projects.PROJECT_SUFFIX, modinfo)
        self.assertNotIn(library.PROJECT, json.loads(modinfo)["AnnoModdingStudio"])

    def test_built_before_its_first_save_it_names_the_project_the_save_makes(self):
        loadout = self.own()
        self.built(loadout)
        named = self.entry()[library.PROJECT]
        self.assertTrue(named)
        self.assertFalse(projects.where(named).exists())
        self.saved(loadout)
        self.assertEqual(loadout.projects[""].path, named)
        self.assertTrue(projects.where(named).is_file())

    def test_built_again_by_its_project_it_is_built_over(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        loadout.set_change(_change(new_value="10.00"))
        done = self.built(loadout)
        self.assertEqual(done.failed, [])
        self.assertEqual(self.entry()[library.BUILD], 2)

    def test_what_else_was_known_of_the_copy_stays(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        library.record(self.library, FOLDER, modio_id=77)
        self.built(loadout)
        self.assertEqual(self.entry()["modio_id"], 77)


class TestOneWriterPerLibraryFolder(_Library):
    def first(self) -> Loadout:
        loadout = self.own()
        self.saved(loadout, "A")
        self.built(loadout)
        return loadout

    def test_another_project_building_into_the_folder_is_stopped_and_told_which(self):
        """Section 2.1: loadout B writing over A's build."""
        first = self.first()
        before = (self.library / FOLDER / "modinfo.json").read_bytes()
        other = self.own()
        other.set_change(_change(new_value="1.00"))
        self.saved(other, "B")
        done = self.built(other)
        self.assertEqual(done.written, [])
        self.assertEqual(len(done.failed), 1)
        self.assertIn(str(projects.where(first.projects[""].path)), done.failed[0][1])
        self.assertEqual((self.library / FOLDER / "modinfo.json").read_bytes(), before)
        self.assertEqual(self.entry()[library.PROJECT], first.projects[""].path)
        self.assertFalse(done.numbered)

    def test_a_loadout_with_no_project_for_the_mod_is_stopped_too(self):
        """A loadout that took the built copy for its own, as a loaded mod
        with edits, is not its project."""
        self.first()
        other = Loadout(game="anno1800")
        other.set_layer(ModLayer(origin=FOLDER, name="My Mod", folder=str(self.mods / FOLDER),
                                 mod_id="me-my-mod"))
        other.set_change(_change(origin=FOLDER))
        done = self.built(other, [FOLDER])
        self.assertEqual(done.written, [])
        self.assertEqual(len(done.failed), 1)

    def test_plays_plan_says_it_and_builds_the_rest(self):
        self.first()
        other = self.own()
        self.saved(other, "B")
        stopped = builtby.stopped(other, [""], self.mods, self.library)
        self.assertEqual([origin for origin, _why in stopped], [""])
        with mock.patch.object(play.build, "mine", return_value=[""]), \
                mock.patch.object(play, "generated_copies") as copies:
            copies.return_value = play.Copies()
            found = play.plan(other, str(self.mods), self.library)
        self.assertEqual(found.built, [])
        self.assertEqual(found.stopped, stopped)
        self.assertFalse(found.nothing_to_do)
        self.assertIn(stopped[0][1], found.warnings)

    def test_a_folder_whose_project_is_gone_is_nobodys(self):
        first = self.first()
        projects.where(first.projects[""].path).unlink()
        other = self.own()
        self.saved(other, "B")
        done = self.built(other)
        self.assertEqual(done.failed, [])
        self.assertEqual(self.entry()[library.PROJECT], other.projects[""].path)

    def test_a_copy_built_before_projects_were_recorded_is_nobodys(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        library.record(self.library, FOLDER, forget=library.BUILT_AS)
        other = self.own()
        self.saved(other, "B")
        self.assertEqual(self.built(other).failed, [])

    def test_a_zip_and_a_folder_outside_a_mods_folder_are_not_the_librarys(self):
        self.first()
        other = self.own()
        self.saved(other, "B")
        out = self.root / "export"
        done = build.write(other, [""], out, self.library)
        self.assertEqual(done.failed, [])
        done = build.write(other, [""], out, self.library, zipped=True)
        self.assertEqual(done.failed, [])


class TestARenamedModMovesItsFolder(_Library):
    def test_the_own_mod_renamed_moves_its_folder_and_link(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        self.assertTrue(library.is_link(self.mods / FOLDER))
        loadout.own.name = "New Name"
        done = self.built(loadout)
        self.assertEqual(done.failed, [])
        moved = "[Gameplay] New Name"
        self.assertFalse((self.library / FOLDER).exists())
        self.assertTrue((self.library / moved / "modinfo.json").is_file())
        self.assertFalse(library.is_link(self.mods / FOLDER))
        self.assertEqual(library.target_of(self.mods / moved), self.library / moved)
        self.assertNotIn(FOLDER, library.registry(self.library))
        self.assertEqual(self.entry(moved)[library.WAS], [FOLDER])
        self.assertEqual(self.entry(moved)[library.PROJECT], loadout.projects[""].path)
        self.assertIn("New Name", (self.library / moved / "modinfo.json").read_text(encoding="utf-8"))

    def test_switched_off_it_moves_and_stays_off(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        library.note_link(self.library, library.Linked(self.mods / FOLDER, self.library / FOLDER),
                          library.COPY)
        self.assertTrue(library.unlink(self.library, self.mods / FOLDER))
        moved = library.move_copy(self.library, FOLDER, "[Gameplay] New Name")
        self.assertEqual(moved, self.library / "[Gameplay] New Name")
        entry = library.links(self.library)["[Gameplay] New Name"]
        self.assertFalse(entry["linked"])
        self.assertFalse((self.mods / "[Gameplay] New Name").exists())
        self.assertEqual(library.relink(self.library, "[Gameplay] New Name"), self.mods / "[Gameplay] New Name")

    def test_a_name_taken_in_the_library_is_not_moved_onto(self):
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        (self.library / "[Gameplay] New Name").mkdir()
        with self.assertRaises(OSError):
            library.move_copy(self.library, FOLDER, "[Gameplay] New Name")
        self.assertTrue((self.library / FOLDER).is_dir())
        self.assertTrue(library.is_link(self.mods / FOLDER))

    def test_a_patch_exported_under_a_new_name_into_a_mods_folder_stays_in_its_folder(self):
        """A loaded mod is known in its loadout by its folder name: an
        export into a mods folder writes where its project built it, and
        only an export to share goes out under the new name."""
        patch = "Their Mod (my patch)"
        loadout = Loadout(game="anno1800")
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", folder=str(self.root / "theirs"),
                                   mod_id="their-mod"))
        loadout.set_layer(ModLayer(origin=patch, name=patch, creator="me", mod_id="me-their-patch",
                                   patches="Their Mod", order=1))
        loadout.set_change(_change(origin=patch))
        self.saved(loadout)
        self.assertEqual(self.built(loadout, [patch]).failed, [])
        loadout.layer(patch).name = "Better Patch"
        done = self.built(loadout, [patch], folder_of=generator.export_folder_for)
        self.assertEqual(done.failed, [])
        self.assertEqual([path.name for path in done.written], [patch])
        self.assertEqual(sorted(path.name for path in self.library.iterdir() if path.is_dir()), [patch])
        shared = build.write(loadout, [patch], self.root / "share", folder_of=generator.export_folder_for)
        self.assertEqual([path.name for path in shared.written], ["[Gameplay] Better Patch"])


class TestLoadoutsFollowAMovedMod(_Library):
    def other_with_it(self) -> Loadout:
        other = Loadout(game="anno1800")
        other.set_layer(ModLayer(origin=FOLDER, name="My Mod", folder=str(self.mods / FOLDER),
                                 mod_id="me-my-mod", order=0))
        other.set_layer(ModLayer(origin="Patch of it", name="Patch of it", patches=FOLDER,
                                 mod_id="me-patch", order=1))
        other.set_change(_change(origin=FOLDER, authored=False))
        other.listed[FOLDER] = ["A collection"]
        other.add_profile("Second")
        return other

    def renamed(self) -> Loadout:
        loadout = self.own()
        self.saved(loadout, "A")
        self.built(loadout)
        loadout.own.name = "New Name"
        self.built(loadout)
        return loadout

    def test_matched_by_mod_id_it_takes_the_new_name(self):
        other = self.other_with_it()
        self.renamed()
        moved = "[Gameplay] New Name"
        self.assertEqual(builtby.follow(other, self.library), [(FOLDER, moved)])
        layer = other.layer(moved)
        self.assertIsNotNone(layer)
        self.assertIsNone(other.layer(FOLDER))
        self.assertEqual(layer.folder, str(self.mods / moved))
        self.assertEqual(other.layer("Patch of it").patches, moved)
        self.assertEqual([change.origin for change in other.changes.values()], [moved])
        self.assertEqual([key[0] for key in other.changes], [moved])
        self.assertIn(moved, other.profiles[1].switches)
        self.assertNotIn(FOLDER, other.profiles[1].switches)
        self.assertEqual(other.listed, {moved: ["A collection"]})
        # And once is enough.
        self.assertEqual(builtby.follow(other, self.library), [])

    def test_saved_and_opened_again_it_keeps_the_new_name(self):
        other = self.other_with_it()
        self.renamed()
        builtby.follow(other, self.library)
        path = self.saved(other, "B")
        again = Loadout.load(path)
        self.assertIsNotNone(again.layer("[Gameplay] New Name"))
        self.assertEqual(again.layer("Patch of it").patches, "[Gameplay] New Name")

    def test_a_mod_still_where_it_was_is_left_alone(self):
        other = self.other_with_it()
        loadout = self.own()
        self.saved(loadout, "A")
        self.built(loadout)
        self.assertEqual(builtby.follow(other, self.library), [])

    def test_a_mod_id_that_went_with_the_name_is_matched_by_the_name_it_had(self):
        other = self.other_with_it()
        loadout = self.own(mod_id="")
        self.saved(loadout, "A")
        self.built(loadout)
        other.layer(FOLDER).mod_id = library.registry(self.library)[FOLDER][library.MOD_ID]
        loadout.own.name = "New Name"
        self.built(loadout)
        moved = "[Gameplay] New Name"
        self.assertEqual(builtby.follow(other, self.library), [(FOLDER, moved)])
        self.assertEqual(other.layer(moved).mod_id, self.entry(moved)[library.MOD_ID])

    def test_two_copies_with_its_mod_id_are_told_apart_by_the_name_it_had(self):
        other = self.other_with_it()
        other.layer(FOLDER).mod_id = "somebody-else"
        loadout = self.renamed()
        # A second moved copy with that ModID, so the name it had decides.
        library.record_built(self.library, "[Gameplay] New Name", project=loadout.projects[""].path,
                             mod_id="somebody-else")
        (self.library / "[Gameplay] Third").mkdir()
        library.record_built(self.library, "[Gameplay] Third", mod_id="somebody-else")
        library.record(self.library, "[Gameplay] Third", was=["[Gameplay] Elsewhere"])
        self.assertEqual(builtby.follow(other, self.library), [(FOLDER, "[Gameplay] New Name")])

    def test_an_unknown_mod_id_and_another_name_are_not_followed(self):
        other = Loadout(game="anno1800")
        other.set_layer(ModLayer(origin="[Gameplay] Unrelated", name="Unrelated", mod_id="somebody-else",
                                 folder=str(self.mods / "[Gameplay] Unrelated")))
        self.renamed()
        self.assertEqual(builtby.follow(other, self.library), [])


class TestARenamedModOfYoursKeepsItsHistories(unittest.TestCase):
    """Renamed, a mod of yours takes its own history with it, and the
    loadout's history knows it by the new name."""

    NEW = "[Gameplay] Renamed"

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)

    def checkpointed(self, loadout: Loadout, origin: str) -> None:
        loadout.add_profile("Second")
        loadout.profiles[1].switches = {origin: True, "Theirs": False}
        loadout.checkpoint("mine", origin=origin)
        histories.take(loadout, "list")

    def assert_known_by_the_new_name(self, loadout: Loadout, old: str) -> None:
        new = self.NEW
        self.assertIn(new, loadout.mod_history)
        self.assertNotIn(old, loadout.mod_history)
        held = loadout.mod_history[new][0]
        self.assertEqual({raw["origin"] for raw in held.changes}, {new})
        listed = loadout.history[-1]
        self.assertIn(new, [mod["origin"] for mod in listed.mods])
        self.assertNotIn(old, [mod["origin"] for mod in listed.mods])
        self.assertIn(new, listed.projects)
        self.assertNotIn(old, listed.projects)
        second = next(profile for profile in listed.profiles if profile["name"] == "Second")
        self.assertEqual(second["switches"], {new: True, "Theirs": False})
        # Its work is what its last checkpoint holds, and the list what the
        # loadout's does.
        self.assertFalse(loadout.changed_since_checkpoint(new))
        self.assertTrue(histories.compare(listed, histories.now(loadout)).is_empty)

    def test_a_patch(self):
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", order=1))
        patch = patching.make(loadout, "Theirs")
        old = patch.origin
        loadout.set_change(_change(origin=old))
        # An op of its among theirs, and one set aside inside a mod not loaded.
        loadout.set_op(Op(source="<ModOp/>", origin="Theirs", uid="mine", authored=True, kept_by=old))
        loadout.kept_aside = {old: {"taken_out": [["Elsewhere", "1", "", "", old]]}}
        self.checkpointed(loadout, old)
        builtby.rename(loadout, old, self.NEW, "")
        self.assert_known_by_the_new_name(loadout, old)
        held = loadout.mod_history[self.NEW][0]
        self.assertEqual([raw.get("kept_by") for raw in held.inside["ops"]], [self.NEW])
        self.assertEqual(held.inside["taken_out"], [["Elsewhere", "1", "", "", self.NEW]])
        mod = next(mod for mod in loadout.history[-1].mods if mod["origin"] == self.NEW)
        self.assertEqual(mod["patches"], "Theirs")
        self.assertEqual(loadout.kept_aside, {self.NEW: {"taken_out": [["Elsewhere", "1", "", "", self.NEW]]}})

    def test_a_carried_on_mod(self):
        old = FOLDER
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", order=0))
        loadout.set_layer(ModLayer(origin=old, name="My Mod", folder="C:/mods/" + old, mod_id="me-my-mod",
                                   order=1))
        loadout.set_layer(ModLayer(origin="Its patch", name="Its patch", patches=old, needs=[old], order=2))
        loadout.home = old
        loadout.set_change(_change(origin=old))
        self.checkpointed(loadout, old)
        builtby.rename(loadout, old, self.NEW, "C:/mods/" + self.NEW)
        self.assertEqual(loadout.home, self.NEW)
        self.assert_known_by_the_new_name(loadout, old)
        mods = {mod["origin"]: mod for mod in loadout.history[-1].mods}
        self.assertEqual(mods[self.NEW]["folder"], "C:/mods/" + self.NEW)
        self.assertEqual((mods["Its patch"]["patches"], mods["Its patch"]["needs"]), (self.NEW, [self.NEW]))


class TestAProjectSavedUnderAnotherName(_Library):
    def test_takes_its_copies_with_it(self):
        loadout = self.own()
        self.saved(loadout, "A")
        self.built(loadout)
        before = loadout.projects[""].path
        loadout.own_project_anew()
        self.saved(loadout, "Copy")
        after = loadout.projects[""].path
        self.assertNotEqual(before, after)
        library.follow_project(self.library, before, after)
        self.assertEqual(self.entry()[library.PROJECT], after)
        self.assertEqual(library.built_from(self.library, after), [FOLDER])
        self.assertEqual(library.built_from(self.library, before), [])
        # The copy builds it now, and the file it was saved from is stopped.
        self.assertEqual(self.built(loadout).failed, [])
        first = Loadout.load(self.root / "A.annoload")
        self.assertEqual(len(self.built(first).failed), 1)

    def test_a_project_named_two_ways_is_one(self):
        loadout = self.own()
        self.saved(loadout)
        written = loadout.projects[""].path
        whole = str(projects.where(written))
        self.assertTrue(builtby.same(written, whole))
        self.assertTrue(builtby.same(written, whole.upper()))
        self.assertFalse(builtby.same(written, ""))


class TestTheRecordsKeepTheirFormat(_Library):
    def test_an_older_build_keeps_the_new_facts(self):
        """Facts added to an entry are kept by every build that reads the
        record, so the format is not bumped for them (code standards,
        section 11)."""
        loadout = self.own()
        self.saved(loadout)
        self.built(loadout)
        library.record(self.library, "Another", source=library.ZIP)
        raw = json.loads((self.library / library.REGISTRY).read_text(encoding="utf-8"))
        self.assertEqual(raw["format"], library.RECORD_FORMAT)
        self.assertEqual(raw["entries"][FOLDER][library.MOD_ID], "me-my-mod")


if __name__ == "__main__":
    unittest.main()
