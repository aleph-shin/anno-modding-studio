"""A mod of yours added to a loadout that is not open, and taken out again.

``docs/proposals/export-tab-proposal.md``, section 5: the loadout's file
refers to the mod's project at a revision it never read, and the loadout
reads it when opened, where the mod stands in its load order, as it reads
a project another loadout changed.  See ``core/adding.py``.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import SessionCase, modops_file  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    adding,
    build,
    builtby,
    generator,
    library,
    loadercheck,
    narrow,
    places,
    projects,
    stale,
    switches,
    truth,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    Change,
    Loadout,
    ModLayer,
    own_record,
)

ALPHA = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>8</Radius></ModOp>'
DOUBLED = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
    '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp>'
)


def radius(new, old="10", origin="") -> Change:
    return Change(
        guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
        old_value=old, new_value=new, origin=origin, authored=True,
    )


class _Case(SessionCase):
    maxDiff = None

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self.root = places.loadouts_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def edit(self, loadout, *changes):
        for change in changes:
            loadout.set_change(loadout.place(change))
        narrow.write(loadout)

    def save(self, loadout, name) -> Path:
        path = self.root / ("%s.annoload" % name)
        loadout.save(path)
        return path

    def written_mod(self) -> Loadout:
        """A loadout made by Write a mod of your own: the mod it is, saved."""
        mine = Loadout(game="anno1800", own=own_record(name="Harbours", category="Gameplay"))
        mine.set_up_as(AIM_MAKE)
        mine.make_own_mod()
        self.edit(mine, radius("12"))
        self.save(mine, "Harbours")
        return mine

    def played(self, root, *names) -> Path:
        """A loadout of the mods ``names`` in ``root``, saved as Main."""
        _session, loadout = loadercheck.read_folders(
            [root / name for name in names], [root], self.index, "anno1800"
        )
        return self.save(loadout, "Main")

    def add(self, path, source, origin, mods_folder) -> adding.Added:
        target = Loadout.load(path)
        done = adding.add(target, source, origin, mods_folder)
        target.save(path)
        return done

    def read_back(self, loadout):
        """Read what is marked again and take it, as the window does."""
        stale.follow_projects(loadout)
        taken = stale.snapshot(loadout)
        reports = stale.read_again(taken, self.index)
        stale.take(loadout, reports)
        stale.give_up(loadout, [origin for origin in taken.asked if origin not in reports])


class TestByTheirFiles(_Case):
    def test_added_to_a_loadout_just_made_both_are_written_in_one_save(self):
        """What I play now… from Add to loadout (section 5.7): neither
        loadout open, the mod built, and the build number it took kept."""
        # A folder the game loads mods from, by its name: built into the
        # library only from one.
        mods = self.mods(a=ALPHA).rename(Path(tempfile.mkdtemp()) / "mods")
        self.addCleanup(shutil.rmtree, mods.parent, True)
        self.written_mod()
        source = self.root / "Harbours.annoload"
        main = self.played(mods, "a")
        library_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, library_dir, True)
        done = adding.add_by_files(main, source, "", mods, library_dir)
        self.assertEqual(done.origin, "[Gameplay] Harbours")
        self.assertEqual(Loadout.load(main).held(done.origin), projects.MOVED)
        self.assertTrue((library_dir / done.origin / "modinfo.json").is_file())
        self.assertFalse((mods / done.origin).exists())
        self.assertEqual(Loadout.load(source).builds[""]["build"], 1)

    def test_one_that_cannot_have_it_has_nothing_written(self):
        mods = self.mods(a=ALPHA)
        self.written_mod()
        source = self.root / "Harbours.annoload"
        main = self.played(mods, "a")
        target = Loadout.load(main)
        target.set_layer(ModLayer(origin="[Gameplay] Harbours", mod_id="someone-elses"))
        target.save(main)
        before = (main.read_bytes(), source.read_bytes())
        with self.assertRaises(adding.AddError):
            adding.add_by_files(main, source, "", mods, self.root)
        self.assertEqual((main.read_bytes(), source.read_bytes()), before)


class TestAMissingReadingIsReadOnOpening(_Case):
    def test_the_mod_is_read_where_it_stands_as_a_project_changed_elsewhere(self):
        """The first thing phase 2 proves (section 10 of the proposal)."""
        mods = self.mods(a=ALPHA, b=DOUBLED)
        mine = self.written_mod()
        project = adding.project_of(mine, "")
        before = project.read_bytes()
        main = self.played(mods, "a", "b")
        done = self.add(main, mine, "", mods)
        self.assertEqual(done.origin, "[Gameplay] Harbours")
        # Written without reading anything, and nothing over the project.
        self.assertEqual(project.read_bytes(), before)
        opened = Loadout.load(main)
        self.assertEqual(opened.held(done.origin), projects.MOVED)
        self.assertEqual(adding.having(opened, project), done.origin)
        layer = opened.layer(done.origin)
        self.assertTrue(layer.kept and layer.active)
        self.assertEqual(layer.name, "Harbours")
        # Where the game sorts it: its folder's name before "a".
        self.assertEqual([one.origin for one in sorted(opened.layers, key=lambda one: one.order)],
                         ["[Gameplay] Harbours", "a", "b"])
        self.read_back(opened)
        self.assertEqual(opened.held(done.origin), "")
        (read,) = [c for c in opened.changes_of(done.origin) if c.tag == "Radius"]
        self.assertEqual((read.old_value, read.new_value), ("10", "12"))
        # And the mods after it are read over it: b doubles what a sets.
        self.assertEqual([c.new_value for c in opened.changes_of("b") if c.tag == "Radius"], ["16"])
        # Saved once read, the project is as it was: nothing moved in it.
        opened.save(main)
        self.assertEqual(project.read_bytes(), before)
        self.assertEqual(Loadout.load(main).held(done.origin), "")

    def test_adding_it_twice_adds_it_once(self):
        mods = self.mods(a=ALPHA)
        mine = self.written_mod()
        main = self.played(mods, "a")
        first = self.add(main, mine, "", mods)
        again = self.add(main, mine, "", mods)
        self.assertEqual(again.origin, first.origin)
        self.assertEqual(len(Loadout.load(main).layers), 2)

    def test_a_mod_with_no_project_yet_is_saved_first(self):
        mine = Loadout(game="anno1800", own=own_record(name="Harbours"))
        mine.set_up_as(AIM_MAKE)
        mine.make_own_mod()
        with self.assertRaises(adding.AddError):
            adding.project_of(mine, "")

    def test_another_mod_in_a_folder_of_the_same_name_is_not_written_over(self):
        mods = self.mods(**{"[Gameplay]__Harbours": ALPHA})
        (mods / "[Gameplay] Harbours" / "modinfo.json").write_text(
            '{"ModID": "someone-elses-harbours"}', encoding="utf-8")
        mine = self.written_mod()
        main = self.played(mods, "[Gameplay] Harbours")
        target = Loadout.load(main)
        with self.assertRaises(adding.AddError) as refused:
            adding.add(target, mine, "", mods)
        self.assertIn("so to the game it is another mod", str(refused.exception))


class TestALoadoutThatHasItFromItsFolder(_Case):
    """Your report of 24 October: the mod was in Main from its folder before
    a loadout carried on with it and made its project.  Adding it to Main
    takes the project up there, and reads it from the project."""

    def test_it_is_found_and_taken_up_and_read_from_the_project(self):
        mods = self.mods(**{"[Gameplay]__Harbours": ALPHA})
        (mods / "[Gameplay] Harbours" / "modinfo.json").write_text(
            '{"ModID": "me-harbours", "ModName": {"English": "Harbours"}}', encoding="utf-8")
        main = self.played(mods, "[Gameplay] Harbours")
        # Carried on with in a loadout of its own, renamed, and changed.
        _session, carried = loadercheck.read_folders(
            [mods / "[Gameplay] Harbours"], [mods], self.index, "anno1800")
        carried.home = "[Gameplay] Harbours"
        carried.layer(carried.home).kept = True
        carried.layer(carried.home).name = "Harbours (Studio)"
        self.edit(carried, radius("12", old="8", origin=carried.home))
        self.save(carried, "Harbours")
        target = Loadout.load(main)
        self.assertIsNotNone(adding.from_folder(target, carried, carried.home))
        done = adding.add(target, carried, carried.home, mods)
        self.assertTrue(done.taken_up)
        self.assertEqual(done.origin, "[Gameplay] Harbours")
        target.save(main)
        opened = Loadout.load(main)
        self.assertEqual(len(opened.layers), 1)
        self.assertTrue(opened.layer(done.origin).kept)
        self.assertEqual(opened.held(done.origin), projects.MOVED)
        self.assertEqual(adding.having(opened, adding.project_of(carried, carried.home)), done.origin)
        self.read_back(opened)
        (read,) = [c for c in opened.changes_of(done.origin) if c.authored]
        self.assertEqual((read.old_value, read.new_value), ("8", "12"))
        self.assertEqual(opened.layer(done.origin).name, "Harbours (Studio)")

    def carried_on(self, mods, name):
        """The mod in Main from its folder, carried on in a loadout of its
        own with its Mod ID field cleared and ``name`` for its name."""
        main = self.played(mods, "[Gameplay] Harbours")
        _session, carried = loadercheck.read_folders(
            [mods / "[Gameplay] Harbours"], [mods], self.index, "anno1800")
        carried.home = "[Gameplay] Harbours"
        carried.layer(carried.home).mod_id = ""
        carried.layer(carried.home).name = name
        self.save(carried, "Harbours")
        return main, carried

    def harbours(self):
        mods = self.mods(**{"[Gameplay]__Harbours": ALPHA})
        (mods / "[Gameplay] Harbours" / "modinfo.json").write_text(
            '{"ModID": "harbours", "ModName": {"English": "Harbours"}}', encoding="utf-8")
        return mods

    def test_with_its_modid_field_cleared_and_its_name_kept_it_is_the_same_mod(self):
        mods = self.harbours()
        main, carried = self.carried_on(mods, "Harbours")
        self.assertEqual(adding.built_id(carried, carried.home), "harbours")
        self.assertIsNotNone(adding.from_folder(Loadout.load(main), carried, carried.home))

    def test_given_a_new_modid_it_is_another_mod_and_not_taken_up(self):
        """Your plan of 24 October: carried on as a new mod, with a new
        ModID made from a new name.  Main's copy is the mod as it was."""
        mods = self.harbours()
        main, carried = self.carried_on(mods, "Harbours (Studio)")
        self.assertEqual(adding.built_id(carried, carried.home), "harbours-studio")
        target = Loadout.load(main)
        self.assertIsNone(adding.from_folder(target, carried, carried.home))
        # It builds into the folder Main has the old mod in: refused, saying why.
        with self.assertRaises(adding.AddError) as refused:
            adding.add(target, carried, carried.home, mods)
        self.assertIn("with the ModID harbours. This mod builds with harbours-studio",
                      str(refused.exception))

class TestAPatchBringsTheModsItNeeds(_Case):
    def patched(self, mods):
        """A loadout of a and b, with a patch of a, saved."""
        _session, source = loadercheck.read_folders(
            [mods / "a", mods / "b"], [mods], self.index, "anno1800"
        )
        source.set_layer(ModLayer(origin="Patch", name="Patch", patches="a", order=1.5))
        self.edit(source, radius("12", old="8", origin="Patch"))
        self.save(source, "Patched")
        return source

    def test_the_mod_it_patches_comes_with_it_and_is_read_there(self):
        mods = self.mods(a=ALPHA, b=DOUBLED)
        source = self.patched(mods)
        main = self.played(mods, "b")
        done = self.add(main, source, "Patch", mods)
        self.assertEqual(done.brought, ["a"])
        opened = Loadout.load(main)
        patch = opened.layer(done.origin)
        self.assertEqual(patch.patches, "a")
        self.read_back(opened)
        self.assertEqual([c.new_value for c in opened.changes_of("a") if c.tag == "Radius"], ["8"])
        (mine,) = [c for c in opened.changes_of(done.origin) if c.tag == "Radius"]
        self.assertEqual((mine.old_value, mine.new_value), ("8", "12"))
        self.assertEqual([c.new_value for c in opened.changes_of("b") if c.tag == "Radius"], ["24"])

    def test_one_it_has_switched_off_is_switched_on(self):
        mods = self.mods(a=ALPHA, b=DOUBLED)
        source = self.patched(mods)
        main = self.played(mods, "a", "b")
        target = Loadout.load(main)
        target.layer("a").active = False
        target.save(main)
        done = self.add(main, source, "Patch", mods)
        self.assertEqual((done.brought, done.switched_on), ([], ["a"]))
        self.assertTrue(Loadout.load(main).layer("a").active)


class TestTakingItOut(_Case):
    def test_its_layer_and_reference_go_and_the_project_stays(self):
        mods = self.mods(a=ALPHA)
        mine = self.written_mod()
        project = adding.project_of(mine, "")
        before = project.read_bytes()
        main = self.played(mods, "a")
        done = self.add(main, mine, "", mods)
        target = Loadout.load(main)
        self.assertEqual(adding.take_out(target, project), "Harbours")
        target.save(main)
        opened = Loadout.load(main)
        self.assertIsNone(opened.layer(done.origin))
        self.assertIsNone(adding.having(opened, project))
        self.assertEqual(project.read_bytes(), before)

    def test_not_the_mod_a_loadout_is(self):
        mine = self.written_mod()
        path = self.root / "Harbours.annoload"
        with self.assertRaises(adding.AddError):
            adding.take_out(Loadout.load(path), adding.project_of(mine, ""))


class TestTheLoadoutTheGameLoads(_Case):
    NAME = "[Gameplay] Harbours"

    def setUp(self):
        super().setUp()
        self.mods_folder = self.mods(a=ALPHA)
        self.library = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.library, True)
        built = self.library / self.NAME
        (built / "data/base/config/export").mkdir(parents=True)
        (built / "data/base/config/export/assets.xml").write_text("<ModOps/>", encoding="utf-8")
        (built / "modinfo.json").write_text('{"ModID": "me-harbours"}', encoding="utf-8")
        mine = self.written_mod()
        self.main = self.played(self.mods_folder, "a")
        self.origin = self.add(self.main, mine, "", self.mods_folder).origin
        self.target = Loadout.load(self.main)
        found = truth.read(self.target, self.mods_folder, library_dir=self.library)
        library.set_live(self.library, self.main, fingerprint=truth.fingerprint(found))

    def test_the_one_mod_is_switched_on_and_off_and_nothing_else(self):
        self.assertTrue(adding.is_live(self.main, self.library))
        self.assertTrue(adding.as_play_left_it(self.target, self.mods_folder, self.library))
        link = self.mods_folder / self.NAME
        self.assertEqual(adding.switch_live(
            self.target, self.main, adding.switch_of(self.target, self.origin), True, self.mods_folder, self.library), "")
        self.assertTrue(library.is_link(link))
        self.assertTrue((self.mods_folder / "a").is_dir())
        # The live record takes the folder as it is now.
        self.assertTrue(adding.as_play_left_it(self.target, self.mods_folder, self.library))
        self.assertEqual(adding.switch_live(
            self.target, self.main, adding.switch_of(self.target, self.origin), False, self.mods_folder, self.library), "")
        self.assertFalse(link.exists())
        self.assertTrue(adding.as_play_left_it(self.target, self.mods_folder, self.library))

    def test_a_mod_is_switched_already_by_its_link(self):
        switch = adding.switch_of(self.target, self.origin)
        self.assertTrue(adding.switched_already(self.target, switch, False, self.mods_folder))
        self.assertFalse(adding.switched_already(self.target, switch, True, self.mods_folder))
        adding.switch_live(self.target, self.main, switch, True, self.mods_folder, self.library)
        self.assertTrue(adding.switched_already(self.target, switch, True, self.mods_folder))
        # Anno 117's profile says too: never taken as switched already.
        self.target.game = "anno117"
        self.assertFalse(adding.switched_already(self.target, switch, True, self.mods_folder))

    def test_a_folder_switched_since_is_not_as_play_left_it(self):
        (self.mods_folder / "a").rename(self.mods_folder / "-a")
        self.assertFalse(adding.as_play_left_it(self.target, self.mods_folder, self.library))

    def test_for_117_its_line_is_written_into_the_studios_profile(self):
        pointer = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pointer, True)
        profile = switches.write_profile(pointer, switches.STUDIO_PROFILE, "Main", {"a": True}, frozen=True)
        switches.point_at(pointer, profile.stem)
        self.target.game = "anno117"
        self.target.layer(self.origin).mod_id = "me-harbours"
        with mock.patch.object(adding.switches, "pointer_folder", lambda *a: pointer):
            self.assertEqual(adding.switch_live(
                self.target, self.main, adding.switch_of(self.target, self.origin), True, self.mods_folder, self.library), "")
            self.assertIn("\nme-harbours\n", profile.read_text(encoding="utf-8"))
            self.assertEqual(adding.switch_live(
                self.target, self.main, adding.switch_of(self.target, self.origin), False, self.mods_folder, self.library), "")
        lines = profile.read_text(encoding="utf-8").splitlines()
        self.assertIn("# me-harbours", lines)
        self.assertIn("a", lines)

    def test_not_a_profile_of_the_users_own(self):
        pointer = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pointer, True)
        (pointer / "profiles").mkdir()
        (pointer / "profiles" / "Mine.txt").write_text("EnableNewMods\na\n", encoding="utf-8")
        switches.point_at(pointer, "Mine")
        self.target.game = "anno117"
        with mock.patch.object(adding.switches, "pointer_folder", lambda *a: pointer):
            why = adding.switch_live(self.target, self.main, adding.switch_of(self.target, self.origin), True, self.mods_folder, self.library)
        self.assertIn("not the Studio's", why)
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertEqual((pointer / "profiles" / "Mine.txt").read_text(encoding="utf-8"), "EnableNewMods\na\n")

    def test_another_loadout_is_not_live(self):
        self.assertFalse(adding.is_live(self.root / "Harbours.annoload", self.library))


class TestANewModIDGetsAFolderOfItsOwn(_Case):
    """Your plan of 24 October, whole: carried on as a new mod with a new
    ModID, added to Main beside the old one."""

    def setUp(self):
        super().setUp()
        top = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, top, True)
        self.mods_folder = top / "mods"
        old = self.mods_folder / "[Gameplay] Harbours"
        (old / "data/base/config/export").mkdir(parents=True)
        (old / "data/base/config/export/assets.xml").write_text(modops_file(ALPHA), encoding="utf-8")
        (old / "modinfo.json").write_text(
            '{"ModID": "harbours", "ModName": {"English": "Harbours"}, '
            '"Category": {"English": "Gameplay"}}', encoding="utf-8")
        (old / "readme.txt").write_text("Harbours, by Aleph.", encoding="utf-8")
        self.library = top / "library"
        self.library.mkdir()
        self.main = self.played(self.mods_folder, "[Gameplay] Harbours")
        _session, carried = loadercheck.read_folders(
            [old], [self.mods_folder], self.index, "anno1800")
        carried.home = carried.editing = "[Gameplay] Harbours"
        carried.layer(carried.home).kept = True
        self.edit(carried, radius("12", old="8", origin=carried.home))
        self.carried = carried

    def renamed(self, name="Harbours (Studio)", mod_id=""):
        layer = self.carried.layer(self.carried.home)
        layer.name, layer.mod_id = name, mod_id

    def test_a_new_name_with_the_modid_field_cleared_is_a_new_modid(self):
        self.assertFalse(generator.given_a_new_mod_id(self.carried, self.carried.home))
        self.renamed()
        self.assertTrue(generator.given_a_new_mod_id(self.carried, self.carried.home))
        self.assertEqual(builtby.own_folder(self.carried, self.carried.home), "[Gameplay] Harbours (Studio)")
        self.assertEqual(generator.export_folder_for(self.carried, self.carried.home),
                         "[Gameplay] Harbours (Studio)")

    def test_so_is_a_modid_typed_with_the_name_kept(self):
        self.renamed(name="Harbours", mod_id="harbours-two")
        self.assertTrue(generator.given_a_new_mod_id(self.carried, self.carried.home))

    def test_and_the_same_modid_typed_again_is_not(self):
        self.renamed(name="Harbours (Studio)", mod_id="harbours")
        self.assertFalse(generator.given_a_new_mod_id(self.carried, self.carried.home))
        self.assertEqual(builtby.own_folders(self.carried), [])

    def test_it_moves_with_everything_of_it_and_builds_beside_the_old(self):
        self.renamed()
        old, new = "[Gameplay] Harbours", "[Gameplay] Harbours (Studio)"
        self.assertEqual(builtby.own_folders(self.carried), [(old, new)])
        self.assertEqual(self.carried.home, new)
        self.assertEqual(self.carried.editing, new)
        # Read from the folder it was carried on from, still: what it ships
        # is there, and nothing is in its new one until it is built.
        self.assertEqual(self.carried.layer(new).folder, str(self.mods_folder / old))
        self.assertEqual([c.new_value for c in self.carried.changes_of(new) if c.authored], ["12"])
        # Once moved, it stays.
        self.assertEqual(builtby.own_folders(self.carried), [])
        self.save(self.carried, "Harbours")
        self.assertIn(new, self.carried.projects)
        built = build.write(self.carried, [new], self.mods_folder, self.library, link=False)
        self.assertEqual(built.failed, [])
        self.assertTrue((self.library / new).is_dir())
        self.assertFalse((self.library / old).exists())
        self.assertIn('"harbours-studio"', (self.library / new / "modinfo.json").read_text(encoding="utf-8").lower())
        # With what the mod ships besides its patch.
        self.assertEqual((self.library / new / "readme.txt").read_text(encoding="utf-8"), "Harbours, by Aleph.")
        # The old mod's folder is the old mod's still.
        self.assertIn('"harbours"', (self.mods_folder / old / "modinfo.json").read_text(encoding="utf-8"))

    def test_added_to_main_it_stands_beside_the_old_mod(self):
        self.renamed()
        builtby.own_folders(self.carried)
        self.save(self.carried, "Harbours")
        new = self.carried.home
        done = self.add(self.main, self.carried, new, self.mods_folder)
        self.assertFalse(done.taken_up)
        opened = Loadout.load(self.main)
        self.assertEqual(sorted(layer.origin for layer in opened.layers),
                         ["[Gameplay] Harbours", "[Gameplay] Harbours (Studio)"])
        self.assertFalse(opened.layer("[Gameplay] Harbours").kept)


class TestSavedTogether(_Case):
    def test_two_loadouts_in_one_journal(self):
        mods = self.mods(a=ALPHA)
        mine = self.written_mod()
        main = self.played(mods, "a")
        test = self.root / "Test.annoload"
        test.write_bytes(main.read_bytes())
        targets = {main: Loadout.load(main), test: Loadout.load(test)}
        for target in targets.values():
            adding.add(target, mine, "", mods)
        adding.save_all(targets)
        project = adding.project_of(mine, "")
        for path in targets:
            self.assertEqual(adding.having(Loadout.load(path), project), "[Gameplay] Harbours")


if __name__ == "__main__":
    unittest.main()
