"""Two histories: each project's, and the loadout's.

``docs/proposals/projects-and-loadouts-proposal.md``, section 9: a project's
history is its mod's work at each checkpoint, kept in the project file; a
loadout's is its list, with which checkpoint of each project it stood at.
Each is restored on its own, a loadout's with its projects if asked, and
neither ever restores build numbers, the mod.io link or what was published.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import histories, projects, releases, stale  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    KIND_EXPORT,
    KIND_PUBLISH,
    MADE_IN,
    Change,
    Loadout,
    ModLayer,
    Op,
    own_record,
)


def _change(value="15.00", **kw) -> Change:
    base = {
        "guid": "1010340", "asset_label": "Lumberjack", "path": "/Values/Factory7/CycleTime",
        "tag": "CycleTime", "old_value": "30.00", "new_value": value,
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

    def own(self) -> Loadout:
        loadout = Loadout(own=own_record(name="My Mod", creator="me", mod_id="me-my-mod", version="1.2"), own_mod=True)
        loadout.set_change(_change())
        loadout.builds[""] = {"build": 3, "digest": "abc"}
        loadout.modio_mod_id = 1234
        return loadout

    def folder(self, name: str) -> str:
        folder = self.root / "library" / name
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def theirs(self, loadout: Loadout, *names: str) -> None:
        for order, name in enumerate(names, start=1):
            loadout.set_layer(ModLayer(origin=name, name=name, folder=self.folder(name), order=order))

    def saved(self, loadout: Loadout, name="Main") -> Path:
        path = self.root / "loadouts" / ("%s.annoload" % name)
        loadout.save(path)
        return path

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))


class TestAProjectsHistory(_Case):
    def test_a_checkpoint_holds_the_mods_work_and_details_and_nobody_elses(self):
        loadout = self.own()
        self.theirs(loadout, "Theirs")
        loadout.set_change(_change("9", origin="Theirs", authored=False))
        loadout.set_op(Op(source='<ModOp Type="merge" GUID="1" />', origin="", authored=True, uid="typed"))
        checkpoint = loadout.checkpoint("Exported v1.2", kind=KIND_EXPORT, loadout="Main")
        self.assertEqual([c.origin for c in checkpoint.as_changes()], [""])
        self.assertEqual([op.uid for op in checkpoint.as_ops()], ["typed"])
        self.assertEqual(checkpoint.details["name"], "My Mod")
        self.assertEqual((checkpoint.version, checkpoint.loadout), ("1.2", "Main"))
        # Which project it is, is not a detail to go back on.
        self.assertNotIn("mod_id", checkpoint.details)
        self.assertEqual(loadout.history_of(""), [checkpoint])
        self.assertEqual(loadout.history, [])

    def test_each_edit_is_noted_with_the_loadout_it_was_made_in(self):
        loadout = self.own()
        first = loadout.checkpoint("one", loadout="Main")
        self.assertEqual(first.changes[0][MADE_IN], "Main")
        loadout.set_change(_change("7", guid="2"))
        second = loadout.checkpoint("two", loadout="Test")
        made_in = {key[1]: said for key, said in second.made_in().items()}
        # The edit that is as it was keeps where it was made.
        self.assertEqual(made_in, {"1010340": "Main", "2": "Test"})
        # And the note is no part of the change put back.
        self.assertIsNone(second.as_changes()[0].extra)

    def test_moved_since_counts_details_as_well_as_changes(self):
        loadout = self.own()
        self.assertTrue(loadout.changed_since_checkpoint())
        loadout.checkpoint("one")
        self.assertFalse(loadout.changed_since_checkpoint())
        loadout.own.description = "Now with words."
        self.assertTrue(loadout.changed_since_checkpoint())

    def test_a_restore_puts_back_work_and_details_but_never_what_happened(self):
        loadout = self.own()
        checkpoint = loadout.checkpoint("Exported v1.2", kind=KIND_EXPORT)
        loadout.set_change(_change("5"))
        loadout.own.version, loadout.own.mod_id = "1.3", "me-renamed"
        loadout.builds[""] = {"build": 12, "digest": "def"}
        loadout.published = {"version": "1.3", "build": 12, "checkpoint": 5.0}
        loadout.modio_mod_id = 99
        histories.restore_mod(loadout, "", checkpoint, "Main")
        self.assertEqual([c.new_value for c in loadout.authored()], ["15.00"])
        self.assertEqual(loadout.own.version, "1.2")
        # Build 12 is not handed out again, and the mod.io page and the
        # record of the publish stay.
        self.assertEqual(loadout.builds[""]["build"], 12)
        self.assertEqual((loadout.modio_mod_id, loadout.published["build"]), (99, 12))
        self.assertEqual(loadout.own.mod_id, "me-renamed")
        self.assertEqual(
            [entry.label for entry in loadout.history_of("")],
            ["Exported v1.2", "Before restoring %s" % checkpoint.when, "Restored %s" % checkpoint.when],
        )

    def test_a_restore_leaves_every_other_mod_alone(self):
        loadout = self.own()
        loadout.set_layer(ModLayer(origin="Patch", name="Patch", patches="Theirs"))
        loadout.set_change(_change("1", origin="Patch"))
        checkpoint = loadout.checkpoint("own")
        loadout.set_change(_change("2", origin="Patch"))
        loadout.restore(checkpoint)
        self.assertEqual(loadout.get("1010340", "/Values/Factory7/CycleTime", origin="Patch").new_value, "2")

    def test_one_taken_where_the_mod_had_another_name_is_this_mods(self):
        """R3-36 and R3-38, hands-on run 3: a checkpoint keeps each edit under
        the name the loadout it was taken in had for the mod.  Restored where
        the mod has another -- the loadout it is the own mod of, or after a
        rename -- it came back as a second mod beside the first, and the
        history listed every edit as taken out and made again."""
        elsewhere = Loadout(own=own_record(name="Other"), own_mod=True)
        elsewhere.set_layer(ModLayer(origin="[Gameplay] Stout", name="Stout", kept=True))
        elsewhere.set_change(_change("12", origin="[Gameplay] Stout", authored=True))
        elsewhere.set_op(Op(source='<ModOp Type="merge" GUID="1" />', origin="[Gameplay] Stout",
                            authored=True, uid="typed"))
        checkpoint = elsewhere.checkpoint("there", origin="[Gameplay] Stout", loadout="Coop")
        loadout = self.own()
        loadout.mod_history[""] = [checkpoint]
        self.assertTrue(loadout.diff_since(checkpoint).modified)
        self.assertFalse(loadout.diff_since(checkpoint).added or loadout.diff_since(checkpoint).removed)
        loadout.restore(checkpoint, "")
        self.assertEqual({c.origin for c in loadout.changes.values()}, {""})
        self.assertEqual([op.uid for op in loadout.ops if op.origin == ""], ["typed"])
        self.assertFalse(any(op.origin == "[Gameplay] Stout" for op in loadout.ops))
        self.assertTrue(loadout.diff_since(checkpoint).is_empty)
        # And an edit made in Coop is still noted as made there.
        again = loadout.checkpoint("here", loadout="Main")
        self.assertEqual(again.changes[0][MADE_IN], "Coop")

    def test_it_is_kept_in_the_project_file(self):
        loadout = self.own()
        loadout.checkpoint("Exported v1.2", kind=KIND_EXPORT, loadout="Main")
        path = self.saved(loadout)
        self.assertNotIn("mod_history", self.read(path))
        kept = self.read(projects.where(loadout.projects[""].path))
        self.assertEqual([entry["label"] for entry in kept["history"]], ["Exported v1.2"])
        loaded = Loadout.load(path)
        self.assertEqual([entry.label for entry in loaded.history_of("")], ["Exported v1.2"])
        self.assertEqual(releases.last_release(loaded).version, "1.2")

    def test_edits_to_a_mod_no_project_keeps_stay_in_the_loadout(self):
        """Until somebody else's mod is never edited (phase 8)."""
        loadout = self.own()
        self.theirs(loadout, "Theirs")
        loadout.set_change(_change("4", origin="Theirs"))
        loadout.checkpoint("theirs", origin="Theirs")
        path = self.saved(loadout)
        self.assertEqual(list(self.read(path)["mod_history"]), ["Theirs"])
        self.assertEqual(len(Loadout.load(path).history_of("Theirs")), 1)


class TestNoCheckpointOfNothingNew(_Case):
    """An export, a publish or a restore that finds the work as its last
    checkpoint has it takes no second one, and the history dialog offers
    none by hand."""

    def test_the_same_work_exported_again_takes_none(self):
        loadout = self.own()
        first = loadout.release_checkpoint("Exported v1.2, build 3", KIND_EXPORT)
        again = loadout.release_checkpoint("Exported v1.2, build 4", KIND_EXPORT)
        self.assertIs(again, first)
        self.assertEqual([entry.label for entry in loadout.history_of("")],
                         ["Exported v1.2, build 3"])
        # The details are work too.
        loadout.own.description = "Now with words."
        third = loadout.release_checkpoint("Exported v1.2, build 5", KIND_EXPORT)
        self.assertIsNot(third, first)
        self.assertEqual(len(loadout.history_of("")), 2)

    def test_a_publish_of_what_was_exported_makes_that_one_the_publish(self):
        loadout = self.own()
        exported = loadout.release_checkpoint("Exported v1.2", KIND_EXPORT)
        published = loadout.release_checkpoint("Published v1.2", KIND_PUBLISH)
        self.assertIs(published, exported)
        self.assertEqual((published.kind, published.label), (KIND_PUBLISH, "Published v1.2"))
        self.assertEqual(len(loadout.history_of("")), 1)
        # And exporting what was published is not a new release either.
        self.assertIs(loadout.release_checkpoint("Exported v1.2", KIND_EXPORT), published)
        self.assertEqual(published.kind, KIND_PUBLISH)

    def test_a_release_after_a_checkpoint_by_hand_still_takes_one(self):
        """The next release is measured from the last checkpoint that marks
        one, and one taken by hand does not."""
        loadout = self.own()
        loadout.checkpoint("by hand")
        exported = loadout.release_checkpoint("Exported v1.2", KIND_EXPORT)
        self.assertEqual([entry.label for entry in loadout.history_of("")],
                         ["by hand", "Exported v1.2"])
        self.assertIs(releases.last_release(loadout).checkpoint, exported)

    def test_before_the_first_details_alone_are_not_worth_one(self):
        loadout = Loadout(own=own_record(name="My Mod", creator="me", mod_id="me-my-mod",
                                         version="1.2"), own_mod=True)
        self.assertFalse(loadout.worth_a_checkpoint())
        # A loadout's checkpoint still takes one, empty, to put it back to.
        self.assertTrue(loadout.changed_since_checkpoint())
        loadout.set_change(_change())
        self.assertTrue(loadout.worth_a_checkpoint())
        loadout.checkpoint("one")
        self.assertFalse(loadout.worth_a_checkpoint())

    def test_a_restore_to_where_it_stands_takes_none(self):
        loadout = self.own()
        checkpoint = loadout.checkpoint("Exported v1.2", kind=KIND_EXPORT)
        histories.restore_mod(loadout, "", checkpoint, "Main")
        self.assertEqual([entry.label for entry in loadout.history_of("")], ["Exported v1.2"])

    def test_the_loadouts_list_the_same_takes_none(self):
        loadout = self.own()
        self.theirs(loadout, "A", "B")
        first = histories.take_release(loadout, "Exported v1.2", KIND_EXPORT)
        self.assertFalse(histories.moved(loadout))
        self.assertIs(histories.take_release(loadout, "Exported v1.2", KIND_EXPORT), first)
        published = histories.take_release(loadout, "Published v1.2", KIND_PUBLISH)
        self.assertIs(published, first)
        self.assertEqual((published.kind, published.label), (KIND_PUBLISH, "Published v1.2"))
        self.assertEqual(len(loadout.history), 1)
        # A project whose work moved takes one with the list's.
        loadout.set_change(_change("5"))
        self.assertTrue(histories.moved(loadout))
        histories.take_release(loadout, "Exported v1.3", KIND_EXPORT)
        self.assertEqual(len(loadout.history), 2)
        # And so does the list itself.
        loadout.layer("B").active = False
        self.assertTrue(histories.moved(loadout))


class TestALoadoutsHistory(_Case):
    def loadout(self) -> Loadout:
        loadout = self.own()
        self.theirs(loadout, "A", "B", "C")
        loadout.layer("C").options = {"Speed": "fast"}
        return loadout

    def test_a_checkpoint_holds_the_list_and_where_each_project_stood(self):
        loadout = self.loadout()
        loadout.add_profile("Second")
        taken = histories.take(loadout, "by hand", taken_in="Main")
        self.assertEqual([mod["origin"] for mod in taken.mods], ["A", "B", "C"])
        self.assertEqual(taken.mods[2]["options"], {"Speed": "fast"})
        self.assertNotIn("declared", taken.mods[0])
        self.assertEqual([p["name"] for p in taken.profiles], ["Main", "Second"])
        # The project had none, so it took one with the loadout's.
        own = loadout.history_of("")
        self.assertEqual([entry.label for entry in own], ["by hand, with the loadout"])
        self.assertEqual(taken.projects[""]["checkpoint"], own[-1].timestamp)
        # And none again where nothing of it moved.
        histories.take(loadout, "again")
        self.assertEqual(len(loadout.history_of("")), 1)
        self.assertFalse(histories.changed_since_checkpoint(loadout))
        # Moved, it takes one labelled as its own mod's export, not as the
        # one the loadout exported (R3-35, hands-on run 3).
        loadout.set_change(_change("5"))
        histories.take(loadout, "Exported v1.0, build 1", label_of=lambda origin: "Exported v2.0, build 3")
        self.assertEqual(loadout.history_of("")[-1].label, "Exported v2.0, build 3, with the loadout")
        self.assertEqual(loadout.history[-1].label, "Exported v1.0, build 1")

    def test_a_project_held_from_here_takes_none(self):
        """Its project is not written from this loadout, and a checkpoint in
        it would be work the save then refused to write."""
        loadout = self.loadout()
        path = self.saved(loadout)
        loadout.projects[""].held = projects.MOVED
        histories.take(loadout, "by hand")
        self.assertEqual(loadout.history_of(""), [])
        loadout.save(path)

    def test_it_is_kept_in_the_loadout_file(self):
        loadout = self.loadout()
        histories.take(loadout, "by hand")
        path = self.saved(loadout)
        self.assertEqual([entry["label"] for entry in self.read(path)["history"]], ["by hand"])
        loaded = Loadout.load(path)
        self.assertEqual([mod["origin"] for mod in loaded.history[0].mods], ["A", "B", "C"])

    def test_what_moved_between_two(self):
        loadout = self.loadout()
        before = histories.take(loadout, "one")
        loadout.drop_layer("A")
        loadout.layer("B").active = False
        loadout.layer("C").options = {}
        self.theirs(loadout, "D")
        loadout.add_profile("Second")
        diff = histories.compare(before, histories.now(loadout))
        self.assertEqual([mod["origin"] for mod in diff.added], ["D"])
        self.assertEqual([mod["origin"] for mod in diff.gone], ["A"])
        self.assertEqual([mod["origin"] for mod in diff.off], ["B"])
        self.assertEqual([mod["origin"] for mod in diff.settings], ["C"])
        self.assertEqual(diff.profiles_added, ["Second"])

    def test_a_mod_moved_is_the_one_moved_and_not_those_it_passed(self):
        loadout = self.own()
        self.theirs(loadout, "A", "B", "C", "D")
        before = histories.now(loadout)
        for order, name in enumerate(("B", "C", "D", "A"), start=1):
            loadout.layer(name).order = order
        diff = histories.compare(before, histories.now(loadout))
        self.assertEqual([mod["origin"] for mod in diff.moved], ["A"])

    def test_the_switches_of_a_profile_not_chosen_are_part_of_the_list(self):
        loadout = self.loadout()
        loadout.add_profile("Second")
        target = histories.take(loadout, "one")
        loadout.choose_profile("Second")
        loadout.layer("B").active = False
        loadout.choose_profile("Main")
        self.assertTrue(histories.changed_since_checkpoint(loadout))
        diff = histories.compare(target, histories.now(loadout))
        self.assertEqual(diff.profiles_switched, ["Second"])
        # The chosen one's switches are the mods' own on and off.
        self.assertEqual((diff.on, diff.off), ([], []))
        histories.restore(loadout, target)
        self.assertEqual(loadout.find_profile("Second").switches["B"], True)
        # What the restore replaced is a checkpoint to go back to.
        self.assertEqual(
            [entry.label for entry in loadout.history],
            ["one", "Before restoring %s" % target.when, "Restored %s" % target.when],
        )
        before = {p["name"]: p["switches"] for p in loadout.history[1].profiles}
        self.assertFalse(before["Second"]["B"])

    def test_a_restore_that_takes_out_a_project_keeps_its_work_first(self):
        loadout = self.loadout()
        path = self.saved(loadout)
        target = histories.take(loadout, "without the patch")
        loadout.set_layer(ModLayer(origin="P", name="P", patches="A", mod_id="me-p", order=4))
        loadout.set_change(_change("20", origin="P"))
        loadout.save(path)
        histories.take(loadout, "with the patch")
        loadout.set_change(_change("99", origin="P"))
        # Only the project's work moved: the list is as it was.
        self.assertFalse(histories.changed_since_checkpoint(loadout))
        histories.restore(loadout, target)
        self.assertIsNone(loadout.layer("P"))
        self.assertEqual(
            [entry.label for entry in loadout.history],
            ["without the patch", "with the patch", "Before restoring %s" % target.when,
             "Restored %s" % target.when],
        )
        kept = loadout.history_of("P")[-1]
        self.assertEqual([c.new_value for c in kept.as_changes()], ["99"])

    def test_a_restore_puts_the_list_back(self):
        loadout = self.loadout()
        target = histories.take(loadout, "one")
        loadout.drop_layer("A")
        loadout.layer("B").active = False
        loadout.layer("C").options = {}
        self.theirs(loadout, "D")
        loadout.set_change(_change("5"))
        histories.restore(loadout, target, taken_in="Main")
        self.assertEqual([layer.origin for layer in loadout.layers], ["A", "B", "C"])
        self.assertTrue(loadout.layer("B").active)
        self.assertEqual(loadout.layer("C").options, {"Speed": "fast"})
        # Put back, and set back to other settings: each is read again.
        self.assertEqual(loadout.layer("A").stale, [[stale.RESTORED, "A"]])
        self.assertEqual(loadout.layer("C").stale, [[stale.RESTORED, "C"]])
        # The mod's work is its project's history, not the loadout's.
        self.assertEqual([c.new_value for c in loadout.authored()], ["5"])
        self.assertEqual(
            [entry.label for entry in loadout.history],
            ["one", "Before restoring %s" % target.when, "Restored %s" % target.when],
        )

    def test_a_mod_whose_folder_is_gone_is_not_put_back(self):
        loadout = self.loadout()
        target = histories.take(loadout, "one")
        loadout.drop_layer("A")
        Path(self.folder("A")).rmdir()
        done = histories.restore(loadout, target)
        self.assertEqual([(mod["origin"], why) for mod, why in done.not_back], [("A", histories.GONE)])
        self.assertIsNone(loadout.layer("A"))

    def test_with_each_project_as_it_was_then(self):
        loadout = self.loadout()
        self.saved(loadout)
        target = histories.take(loadout, "one", taken_in="Main")
        loadout.set_change(_change("5"))
        loadout.own.description = "Later."
        loadout.builds[""] = {"build": 12, "digest": "def"}
        alone = histories.plan(loadout, target)
        self.assertEqual(alone.projects, [])
        whole = histories.plan(loadout, target, with_projects=True)
        self.assertEqual(whole.projects, [""])
        histories.restore(loadout, target, with_projects=True, taken_in="Main")
        self.assertEqual([c.new_value for c in loadout.authored()], ["15.00"])
        self.assertEqual(loadout.own.description, "")
        self.assertEqual(loadout.builds[""]["build"], 12)
        # Its work as it was replaced is a checkpoint of its own: the list
        # had not moved, so the project's ordinary restore took it.
        self.assertEqual(
            [entry.label for entry in loadout.history_of("")],
            ["one, with the loadout", "Before restoring %s" % target.when,
             "Restored %s" % target.when],
        )
        self.assertEqual([entry.label for entry in loadout.history], ["one", "Restored %s" % target.when])

    def test_a_project_taken_out_since_comes_back_only_with_its_project(self):
        loadout = self.loadout()
        loadout.set_layer(ModLayer(origin="A (patch)", name="A (patch)", patches="A", mod_id="me-a-patch"))
        loadout.set_change(_change("3", origin="A (patch)"))
        self.saved(loadout)
        target = histories.take(loadout, "with the patch")
        path = self.saved(loadout)
        project = projects.where(loadout.projects["A (patch)"].path)
        loadout.drop_layer("A (patch)")
        loadout.save(path)

        alone = histories.plan(loadout, target)
        self.assertEqual([(mod["origin"], why) for mod, why in alone.not_back], [("A (patch)", histories.YOURS)])
        histories.restore(loadout, target, with_projects=True)
        layer = loadout.layer("A (patch)")
        self.assertEqual(layer.patches, "A")
        self.assertEqual(loadout.get("1010340", "/Values/Factory7/CycleTime", origin="A (patch)").new_value, "3")
        # Referring to its project again, as it is now: no second project.
        self.assertEqual(projects.where(loadout.projects["A (patch)"].path), project)
        loadout.save(path)
        self.assertEqual(len(list(project.parent.glob("A (patch)*"))), 1)


if __name__ == "__main__":
    unittest.main()
