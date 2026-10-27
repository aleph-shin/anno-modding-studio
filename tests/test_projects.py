"""A mod of yours is kept in a project of its own, apart from the loadout.

``docs/proposals/projects-and-loadouts-proposal.md``, section 4: saving a
loadout writes each project whose part moved, opening it reads them back, and
a project another loadout has changed since is not written over.
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

from anno_modding_studio.core import disk, histories, places, projects  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    LOADOUT_FORMAT,
    Change,
    Loadout,
    LoadoutError,
    ModLayer,
    Op,
    name_in_file,
    own_record,
)


def _change(**kw) -> Change:
    base = {
        "guid": "1010340", "asset_label": "Lumberjack", "path": "/Values/Factory7/CycleTime",
        "tag": "CycleTime", "old_value": "30.00", "new_value": "15.00",
    }
    base.update(kw)
    return Change(**base)


class _Case(unittest.TestCase):
    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def own(self, name="My Mod") -> Loadout:
        loadout = Loadout(own=own_record(name=name, creator="me", mod_id="me-my-mod", version="1.2"), own_mod=True)
        loadout.set_change(_change())
        loadout.set_op(Op(source='<ModOp Type="merge" GUID="1" />', origin="", authored=True, uid="typed"))
        loadout.builds[""] = {"build": 3, "digest": "abc"}
        loadout.modio_mod_id = 1234
        return loadout

    def saved(self, loadout: Loadout, name="Main") -> Path:
        path = self.root / ("%s.annoload" % name)
        loadout.save(path)
        return path

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def project_of(self, loadout: Loadout, origin: str = "") -> Path:
        return projects.where(loadout.projects[origin].path)


class TestTheOwnModHasAProject(_Case):
    def test_its_details_ops_and_facts_are_in_the_project_and_not_the_loadout(self):
        loadout = self.own()
        path = self.saved(loadout)
        project = self.project_of(loadout)
        self.assertEqual(project.parent, places.projects_dir())
        self.assertEqual(project.suffix, projects.PROJECT_SUFFIX)
        kept = self.read(project)
        self.assertEqual(kept["kind"], projects.KIND)
        self.assertEqual(kept["mod_id"], "me-my-mod")
        self.assertEqual(kept["details"]["name"], "My Mod")
        self.assertEqual(kept["releases"]["modio_mod_id"], 1234)
        self.assertEqual(kept["build"], {"build": 3, "digest": "abc"})
        self.assertEqual([op["uid"] for op in kept["ops"]], ["typed"])

        written = self.read(path)
        self.assertEqual(written["format"], LOADOUT_FORMAT)
        for key in ("mod_name", "creator", "mod_id", "modio_mod_id"):
            self.assertNotIn(key, written)
        self.assertEqual(written["ops"], [])
        self.assertNotIn("", written["builds"])
        self.assertEqual(written["projects"], [{
            "origin": "", "path": "projects/My Mod.annoproj", "mod_id": "me-my-mod", "read_at": 1,
        }])
        # What the changes come to is the loadout's to keep.
        self.assertEqual(len(written["changes"]), 1)

    def test_opening_puts_it_back(self):
        loadout = self.own()
        loaded = Loadout.load(self.saved(loadout))
        self.assertEqual((loaded.own.name, loaded.own.mod_id, loaded.own.version), ("My Mod", "me-my-mod", "1.2"))
        self.assertEqual(loaded.modio_mod_id, 1234)
        self.assertEqual(loaded.builds[""]["build"], 3)
        self.assertEqual([op.uid for op in loaded.ops_of("")], ["typed"])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.held(""), "")

    def test_a_save_that_moved_nothing_of_it_leaves_it_alone(self):
        loadout = self.own()
        self.saved(loadout)
        loaded = Loadout.load(self.saved(loadout))
        project = self.project_of(loaded)
        before = project.stat().st_mtime_ns
        loaded.save(self.root / "Main.annoload")
        self.assertEqual(project.stat().st_mtime_ns, before)
        self.assertEqual(self.read(project)["revision"], 1)

    def test_a_save_that_moved_it_raises_its_revision(self):
        loadout = self.own()
        loaded = Loadout.load(self.saved(loadout))
        loaded.own.description = "Faster lumberjacks."
        loaded.save(self.root / "Main.annoload")
        self.assertEqual(self.read(self.project_of(loaded))["revision"], 2)
        self.assertEqual(self.read(self.root / "Main.annoload")["projects"][0]["read_at"], 2)
        self.assertEqual(Loadout.load(self.root / "Main.annoload").own.description, "Faster lumberjacks.")

    def test_two_mods_of_one_name_have_two_projects(self):
        one, other = self.own(), self.own()
        self.saved(one, "One")
        self.saved(other, "Other")
        self.assertNotEqual(self.project_of(one), self.project_of(other))


class TestAPatchHasAProject(_Case):
    def test_its_details_go_and_its_place_in_the_loadout_stays(self):
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", folder="C:/mods/Their Mod", order=1))
        loadout.set_layer(ModLayer(
            origin="Their Mod (my patch)", name="Their Mod (my patch)", creator="me",
            mod_id="me-their-patch", patches="Their Mod", order=2,
        ))
        path = self.saved(loadout)
        written = {layer["origin"]: layer for layer in self.read(path)["layers"]}
        patch = written["Their Mod (my patch)"]
        self.assertNotIn("name", patch)
        self.assertNotIn("mod_id", patch)
        self.assertEqual(patch["patches"], "Their Mod")
        # Theirs is the library's, and stays as it was.
        self.assertEqual(written["Their Mod"]["name"], "Their Mod")
        kept = self.read(self.project_of(loadout, "Their Mod (my patch)"))
        self.assertEqual(kept["details"]["mod_id"], "me-their-patch")
        self.assertNotIn("Their Mod", loadout.projects)

        loaded = Loadout.load(path)
        layer = loaded.layer("Their Mod (my patch)")
        self.assertEqual((layer.name, layer.mod_id, layer.creator, layer.patches),
                         ("Their Mod (my patch)", "me-their-patch", "me", "Their Mod"))

    def test_a_loadout_with_no_mod_of_yours_has_no_projects(self):
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
        written = self.read(self.saved(loadout))
        self.assertNotIn("projects", written)
        self.assertEqual(written["format"], LOADOUT_FORMAT)
        self.assertFalse(places.projects_dir().exists() and any(places.projects_dir().iterdir()))


class TestAProjectChangedElsewhere(_Case):
    """One loadout's older reading never goes back over another's work."""

    def two_loadouts(self):
        loadout = self.own()
        path = self.saved(loadout)
        first, second = Loadout.load(path), Loadout.load(path)
        second.own.description = "Changed in the other loadout."
        second.save(self.root / "Other.annoload")
        return first

    def test_it_is_opened_as_it_is_now_and_held(self):
        self.two_loadouts()
        loaded = Loadout.load(self.root / "Main.annoload")
        self.assertEqual(loaded.held(""), projects.MOVED)
        self.assertEqual(loaded.own.description, "Changed in the other loadout.")

    def test_saving_the_rest_still_works(self):
        self.two_loadouts()
        loaded = Loadout.load(self.root / "Main.annoload")
        loaded.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
        loaded.save(self.root / "Main.annoload")
        self.assertEqual(len(Loadout.load(self.root / "Main.annoload").layers), 1)

    def test_a_change_to_it_is_not_saved_over_it(self):
        first = self.two_loadouts()
        first.own.description = "Changed here."
        with self.assertRaises(LoadoutError):
            first.save(self.root / "Main.annoload")
        project = projects.read(self.project_of(first))
        self.assertEqual(project["details"]["description"], "Changed in the other loadout.")

    def test_edits_do_not_land_in_it(self):
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
        loadout.set_layer(ModLayer(origin="Patch", name="Patch", patches="Their Mod", order=2))
        loadout.editing = "Patch"
        path = self.saved(loadout)
        other = Loadout.load(path)
        other.layer("Patch").description = "Changed in the other loadout."
        other.save(self.root / "Other.annoload")
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held("Patch"), projects.MOVED)
        self.assertEqual(loaded.editing_origin(), "")


class TestWhatASaveWrites(_Case):
    def test_the_save_question_names_the_projects_that_moved(self):
        loadout = self.own()
        self.assertEqual(loadout.projects_to_save(), ["My Mod"])
        self.saved(loadout)
        self.assertEqual(loadout.projects_to_save(), [])
        loadout.own.version = "1.3"
        self.assertEqual(loadout.projects_to_save(), ["My Mod"])

    def test_a_copy_gets_its_own_mod_a_project_of_its_own(self):
        loadout = self.own()
        self.saved(loadout)
        first = self.project_of(loadout)
        loadout.own_project_anew()
        self.saved(loadout, "Fork")
        self.assertNotEqual(self.project_of(loadout), first)
        self.assertEqual(self.read(first)["details"]["name"], "My Mod")
        self.assertEqual(projects.where(self.read(self.root / "Main.annoload")["projects"][0]["path"]), first)

    def test_a_rename_goes_into_the_project_alone(self):
        from anno_modding_studio.core.changes import name_in_file

        loadout = self.own()
        path = self.saved(loadout)
        loadout.own.version = "unsaved"
        move = name_in_file(path, "", "Renamed")
        self.assertEqual(move, projects.Move("projects/My Mod.annoproj", "projects/Renamed.annoproj", (1, 2)))
        kept = self.read(projects.where(move.after))
        self.assertEqual((kept["details"]["name"], kept["details"]["version"], kept["revision"]),
                         ("Renamed", "1.2", 2))
        self.assertEqual(self.read(path)["projects"][0]["read_at"], 2)
        loaded = Loadout.load(path)
        self.assertEqual((loaded.own.name, loaded.held("")), ("Renamed", ""))


class TestAForksOwnMod(_Case):
    """A loadout's own mod is that loadout's alone: a fork's, or a copy's of
    the live loadout, is a mod of its own."""

    def test_it_takes_the_forks_name_where_the_loadout_is_the_mod(self):
        loadout = self.own()
        loadout.is_the_mod = True
        first = self.saved(loadout)
        project = self.project_of(loadout)
        loadout.own_mod_anew("Experiment")
        self.saved(loadout, "Experiment")
        self.assertEqual((loadout.own.name, loadout.own.mod_id), ("Experiment", ""))
        self.assertNotEqual(self.project_of(loadout), project)
        # None of the first mod's facts: they are that mod's.
        self.assertNotIn("", loadout.builds)
        self.assertEqual((loadout.modio_mod_id, loadout.published), (0, {}))
        # The loadout it came from has its mod as it was.
        kept = Loadout.load(first)
        self.assertEqual((kept.own.name, kept.own.mod_id, kept.modio_mod_id), ("My Mod", "me-my-mod", 1234))

    def test_it_takes_its_old_name_and_the_forks_otherwise(self):
        loadout = self.own("Anno 1800 mod list patch")
        loadout.own_mod_anew("Coop run")
        self.assertEqual(loadout.own.name, "Anno 1800 mod list patch (Coop run)")

    def test_a_fork_that_could_not_be_written_has_its_mod_back(self):
        loadout = self.own()
        self.saved(loadout)
        project = loadout.projects[""]
        was = loadout.own_mod_anew("Experiment")
        loadout.own_mod_back(was)
        self.assertEqual((loadout.own.name, loadout.own.mod_id, loadout.modio_mod_id), ("My Mod", "me-my-mod", 1234))
        self.assertIs(loadout.projects[""], project)
        self.assertEqual(loadout.builds[""]["build"], 3)

    def test_a_loadout_with_no_own_mod_is_left_alone(self):
        loadout = Loadout()
        loadout.own_mod_anew("Experiment")
        self.assertEqual(loadout.own.name, "My Anno Mod")


class TestAMissingProject(_Case):
    def test_the_loadout_still_opens_and_saves(self):
        loadout = self.own()
        path = self.saved(loadout)
        self.project_of(loadout).unlink()
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held(""), projects.MISSING)
        self.assertEqual(len(loaded), 1)
        loaded.save(path)
        self.assertFalse(self.project_of(loadout).exists())


class TestAProjectFollowsItsModsName(_Case):
    """A project's file is named for its mod and follows it when the mod is
    renamed: every loadout that refers to it is pointed at the new file in
    the same save, and none has to read the project again for it."""

    def shared(self):
        """A patch of yours, in two loadouts: Main and Other."""
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
        loadout.set_layer(ModLayer(origin="Patch", name="Patch", mod_id="me-patch", patches="Their Mod", order=2))
        main = self.saved(loadout)
        other = self.root / "Other.annoload"
        Loadout.load(main).save(other)
        return main, other

    def refs(self, path) -> dict:
        return {raw["origin"]: raw for raw in self.read(path)["projects"]}

    @staticmethod
    def kept(name) -> Path:
        return places.projects_dir() / ("%s%s" % (name, projects.PROJECT_SUFFIX))

    def test_a_rename_moves_it_and_every_loadout_that_has_it_follows(self):
        main, other = self.shared()
        move = name_in_file(main, "Patch", "Better Patch")
        self.assertEqual(move, projects.Move("projects/Patch.annoproj", "projects/Better Patch.annoproj", (1, 2)))
        self.assertFalse(self.kept("Patch").exists())
        self.assertEqual(projects.read(self.kept("Better Patch"))["details"]["name"], "Better Patch")
        for path in (main, other):
            with self.subTest(loadout=path.name):
                self.assertEqual(self.refs(path)["Patch"], {
                    "origin": "Patch", "path": "projects/Better Patch.annoproj", "mod_id": "me-patch", "read_at": 2,
                })
        # Opened as it was left: nothing to read again, and not found again
        # by its ModID either.
        opened = Loadout.load(other)
        self.assertEqual((opened.layer("Patch").name, opened.held("Patch"), opened.projects["Patch"].lost),
                         ("Better Patch", "", ""))

    def test_the_loadout_in_hand_follows_it_and_saves_nothing_more_of_it(self):
        main, _other = self.shared()
        loaded = Loadout.load(main)
        loaded.layer("Patch").name = "Better Patch"
        move = name_in_file(main, "Patch", "Better Patch")
        loaded.name_saved("Patch", "Patch", move)
        self.assertEqual((loaded.projects["Patch"].path, loaded.projects["Patch"].read_at), (move.after, 2))
        loaded.save(main)
        self.assertEqual(projects.read(self.kept("Better Patch"))["revision"], 2)

    def test_a_save_with_a_new_name_moves_it_and_no_loadout_reads_it_again(self):
        main, other = self.shared()
        loaded = Loadout.load(main)
        loaded.layer("Patch").name = "Better Patch"
        self.assertEqual(loaded.save(main), {"projects/Patch.annoproj": "projects/Better Patch.annoproj"})
        self.assertEqual(loaded.projects["Patch"].path, "projects/Better Patch.annoproj")
        self.assertFalse(self.kept("Patch").exists())
        self.assertEqual(self.refs(other)["Patch"]["path"], "projects/Better Patch.annoproj")
        self.assertEqual(self.refs(other)["Patch"]["read_at"], 2)
        opened = Loadout.load(other)
        self.assertEqual((opened.layer("Patch").name, opened.held("Patch")), ("Better Patch", ""))

    def test_a_save_that_changed_more_than_the_name_points_the_other_there_to_read_it_again(self):
        main, other = self.shared()
        loaded = Loadout.load(main)
        loaded.layer("Patch").name = "Better Patch"
        loaded.layer("Patch").description = "Patches more."
        loaded.save(main)
        self.assertEqual(self.refs(other)["Patch"]["path"], "projects/Better Patch.annoproj")
        self.assertEqual(self.refs(other)["Patch"]["read_at"], 1)
        opened = Loadout.load(other)
        # As after any change made in another loadout; not lost.
        self.assertEqual((opened.held("Patch"), opened.projects["Patch"].lost), (projects.MOVED, ""))

    def test_a_name_another_project_has_is_numbered(self):
        main, other = self.shared()
        self.kept("Better Patch").write_bytes(b"somebody else's")
        move = name_in_file(main, "Patch", "Better Patch")
        self.assertEqual(move.after, "projects/Better Patch (2).annoproj")
        self.assertEqual(self.kept("Better Patch").read_bytes(), b"somebody else's")
        self.assertEqual(self.refs(other)["Patch"]["path"], move.after)
        self.assertEqual(Loadout.load(other).held("Patch"), "")

    def test_a_name_it_is_named_for_already_leaves_it_where_it_is(self):
        self.assertTrue(projects.named_for("projects/Patch (3).annoproj", "Patch"))
        self.assertTrue(projects.named_for("projects/Patch.annoproj", "PATCH"))
        self.assertFalse(projects.named_for("projects/Patch.annoproj", "Patch 2"))
        self.assertFalse(projects.named_for("projects/Patch (x).annoproj", "Patch"))
        main, other = self.shared()
        move = name_in_file(main, "Patch", "PATCH")
        self.assertEqual((move.before, move.after), ("projects/Patch.annoproj", "projects/Patch.annoproj"))
        self.assertEqual(self.refs(other)["Patch"]["read_at"], 2)

    def test_a_write_that_fails_undoes_the_move(self):
        main, other = self.shared()
        before = {path: path.read_bytes() for path in (main, other, self.kept("Patch"))}
        real = os.replace

        def replace(source, target):
            if Path(target).name == "Other.annoload" and str(source).endswith(".part"):
                raise OSError("disk full")
            return real(source, target)

        loaded = Loadout.load(main)
        loaded.layer("Patch").name = "Better Patch"
        with mock.patch.object(os, "replace", side_effect=replace), self.assertRaises(OSError):
            loaded.save(main)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(sorted(path.name for path in places.projects_dir().iterdir()), ["Patch.annoproj"])
        self.assertEqual(loaded.projects["Patch"].path, "projects/Patch.annoproj")
        self.assertEqual(Loadout.load(other).held("Patch"), "")

    def test_and_a_rename_whose_write_fails_too(self):
        main, other = self.shared()
        before = {path: path.read_bytes() for path in (main, other, self.kept("Patch"))}
        real = disk.write_flushed

        def write(path, data):
            if Path(path).name.startswith("Other.annoload"):
                raise OSError("disk full")
            return real(path, data)

        with mock.patch.object(disk, "write_flushed", side_effect=write), self.assertRaises(OSError):
            name_in_file(main, "Patch", "Better Patch")
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(sorted(path.name for path in places.projects_dir().iterdir()), ["Patch.annoproj"])

    def test_one_named_for_an_old_name_moves_at_the_next_save_as_it_is(self):
        main, other = self.shared()
        os.replace(self.kept("Patch"), self.kept("Old Name"))
        for path in (main, other):
            data = self.read(path)
            data["projects"][0]["path"] = "projects/Old Name.annoproj"
            path.write_text(json.dumps(data), encoding="utf-8")
        held = self.kept("Old Name").read_bytes()
        loaded = Loadout.load(main)
        self.assertEqual(loaded.save(main), {"projects/Old Name.annoproj": "projects/Patch.annoproj"})
        # Moved, not written: the same revision, which no loadout reads again.
        self.assertEqual(self.kept("Patch").read_bytes(), held)
        self.assertFalse(self.kept("Old Name").exists())
        self.assertEqual(self.refs(other)["Patch"], {
            "origin": "Patch", "path": "projects/Patch.annoproj", "mod_id": "me-patch", "read_at": 1,
        })
        self.assertEqual(Loadout.load(other).held("Patch"), "")

    def test_the_checkpoints_follow_it(self):
        main, _other = self.shared()
        loaded = Loadout.load(main)
        loaded.history.append(histories.now(loaded, "With the patch"))
        loaded.save(main)
        move = name_in_file(main, "Patch", "Better Patch")
        (checkpoint,) = self.read(main)["history"]
        self.assertEqual(checkpoint["projects"]["Patch"]["path"], move.after)
        loaded.followed([move])
        self.assertEqual(loaded.history[0].projects["Patch"]["path"], move.after)

    def test_a_loadout_that_no_longer_has_it_brings_it_back_by_its_modid(self):
        _main, other = self.shared()
        loaded = Loadout.load(other)
        histories.take(loaded, "With the patch")
        loaded.save(other)
        main = self.root / "Main.annoload"
        Loadout.load(other).save(main)
        loaded.drop_layer("Patch")
        loaded.save(other)
        name_in_file(main, "Patch", "Better Patch")
        opened = Loadout.load(other)
        # Not pointed there: it no longer refers to the project.
        self.assertEqual(opened.history[-1].projects["Patch"]["path"], "projects/Patch.annoproj")
        self.assertEqual(histories.plan(opened, opened.history[-1], with_projects=True).not_back, [])


if __name__ == "__main__":
    unittest.main()
