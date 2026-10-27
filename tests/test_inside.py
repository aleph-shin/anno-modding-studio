"""Edits inside somebody else's ops belong to a mod of yours.

``docs/proposals/projects-and-loadouts-proposal.md``, section 5.3: an edit
typed over one of their ops, taken out, switched off or put among them is
kept by your patch for their mod or by the loadout's own mod, whichever
*Changes go to* chose.  The keeper's project carries it; Play writes the
generated copy with the patch's edits and then the own mod's; and the
patch's export carries its own alone.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_edits import NAME, PATCH, THEIRS, _Edited  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    edits,
    held,
    histories,
    inside,
    patching,
    projects,
    reader,
    sharing,
    stale,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    OWN,
    Change,
    Checkpoint,
    Loadout,
    ModLayer,
    Op,
    inside_says,
    own_record,
)
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402

THEIRS_OP = '<ModOp Type="replace" GUID="1" Path="/Values/Standard/Name"><Name>Theirs</Name></ModOp>'
OTHER_OP = '<ModOp Type="merge" GUID="2" Path="/Values/Building"><Radius>8</Radius></ModOp>'


def _loadout() -> Loadout:
    """Their mod, with two ops of theirs, in a loadout of somebody else's."""
    loadout = Loadout(own=own_record(creator="Ay"))
    loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", creator="Dee", order=1))
    loadout.set_op(Op(source=THEIRS_OP, origin="Their Mod", uid="theirs", order=1))
    loadout.set_op(Op(source=OTHER_OP, origin="Their Mod", uid="other", order=2))
    return loadout


def _change(**kw) -> Change:
    base = {
        "guid": "1", "asset_label": "Thing", "path": "/Values/Standard/Name", "tag": "Name",
        "old_value": "Theirs", "new_value": "Mine", "origin": "Their Mod",
    }
    base.update(kw)
    return Change(**base)


class TestWhoKeepsAnEdit(unittest.TestCase):
    def test_the_patch_while_changes_go_to_it(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        self.assertTrue(inside.assign(loadout))
        (entry,) = loadout.taken_out
        self.assertEqual(inside.keeper_of(entry), patch.origin)
        self.assertFalse(loadout.own_mod)

    def test_the_loadouts_own_mod_otherwise_which_is_then_made(self):
        loadout = _loadout()
        self.assertFalse(loadout.has_own_mod())
        loadout.set_op(Op(source=THEIRS_OP.replace("Theirs", "Mine"), origin="Their Mod",
                          uid="mine", order=1, authored=True))
        inside.assign(loadout)
        self.assertEqual(loadout.op("mine").kept_by, OWN)
        # The own mod keeps it, so it is there, with a project to keep it in.
        self.assertTrue(loadout.has_own_mod())
        self.assertIn("", loadout.project_origins())

    def test_an_op_of_theirs_switched_off_and_on_again(self):
        loadout = _loadout()
        loadout.op("other").active = False
        inside.assign(loadout)
        self.assertEqual(loadout.op("other").kept_by, OWN)
        loadout.op("other").active = True
        inside.assign(loadout)
        self.assertIsNone(loadout.op("other").kept_by)

    def test_an_ops_answers_are_its_keepers(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.set_op(Op(source=THEIRS_OP.replace("Theirs", "Mine"), origin="Their Mod",
                          uid="mine", order=1, authored=True))
        loadout.set_change(_change(op_uid="mine", authored=False))
        inside.assign(loadout)
        (change,) = loadout.changes.values()
        self.assertEqual(change.kept_by, patch.origin)

    def test_the_answers_of_their_own_ops_are_nobodys(self):
        loadout = _loadout()
        loadout.set_change(_change(op_uid="theirs", authored=False))
        inside.assign(loadout)
        (change,) = loadout.changes.values()
        self.assertIsNone(change.kept_by)

    def test_a_keeper_once_said_stays_when_changes_go_elsewhere(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        inside.assign(loadout)
        loadout.editing = ""
        loadout.drop_op("other")
        inside.assign(loadout)
        self.assertEqual([inside.keeper_of(entry) for entry in loadout.taken_out],
                         [patch.origin, OWN])

    def test_a_mod_of_yours_keeps_its_own_edits(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.set_op(Op(source=OTHER_OP, origin=patch.origin, uid="patched", authored=True))
        inside.assign(loadout)
        self.assertIsNone(loadout.op("patched").kept_by)

    def test_a_step_settles_it_and_undo_takes_it_back(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        history = UndoHistory(loadout)
        loadout.op("other").active = False
        history.commit("switch it off")
        self.assertEqual(loadout.op("other").kept_by, patch.origin)
        history.undo()
        self.assertTrue(loadout.op("other").active)
        history.redo()
        self.assertEqual(loadout.op("other").kept_by, patch.origin)

    def test_counted_by_keeper(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        inside.assign(loadout)
        loadout.editing = ""
        loadout.op("other").active = False
        inside.assign(loadout)
        self.assertEqual(edits.count(loadout, "Their Mod"), 2)
        self.assertEqual(edits.count(loadout, "Their Mod", patch.origin), 1)
        self.assertEqual(edits.count(loadout, "Their Mod", OWN), 1)
        self.assertEqual(edits.kept_in(loadout, patch.origin), {"Their Mod": 1})
        self.assertEqual(edits.kept_in(loadout, OWN), {"Their Mod": 1})

    def test_one_not_settled_yet_counts_as_the_step_will_make_it(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        self.assertTrue(edits.has_edits(loadout, "Their Mod", patch.origin))
        self.assertFalse(edits.has_edits(loadout, "Their Mod", OWN))


class TestWhenTheKeeperGoes(unittest.TestCase):
    def test_unloading_a_patch_lets_go_of_what_it_kept_here(self):
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        loadout.op("other").active = False
        loadout.set_op(Op(source=THEIRS_OP.replace("Theirs", "Mine"), origin="Their Mod",
                          uid="mine", order=1, authored=True))
        loadout.held_edits = [{"uid": "h", "origin": "Their Mod", "kind": held.GONE}]
        inside.assign(loadout)
        loadout.drop_layer(patch.origin)
        theirs = sorted(op.source for op in loadout.ops)
        self.assertEqual(theirs, sorted([THEIRS_OP, OTHER_OP]))
        self.assertTrue(all(op.active and op.kept_by is None for op in loadout.ops))
        self.assertEqual((loadout.taken_out, loadout.held_edits), ([], []))
        self.assertFalse(edits.has_edits(loadout, "Their Mod"))

    def test_a_mod_made_yours_keeps_its_edits_as_its_own(self):
        loadout = _loadout()
        loadout.op("other").active = False
        inside.assign(loadout)
        self.assertEqual(loadout.op("other").kept_by, OWN)
        loadout.layer("Their Mod").kept = True
        inside.assign(loadout)
        self.assertIsNone(loadout.op("other").kept_by)
        self.assertFalse(loadout.op("other").active)

    def test_renamed_a_patch_still_keeps_its_edits(self):
        from anno_modding_studio.core import builtby

        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        loadout.op("other").active = False
        inside.assign(loadout)
        builtby.rename(loadout, patch.origin, "Renamed patch", "")
        self.assertEqual(inside.keeper_of(loadout.taken_out[0]), "Renamed patch")
        self.assertEqual(loadout.op("other").kept_by, "Renamed patch")


class _Saved(unittest.TestCase):
    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def edited(self):
        """A patch keeping one of each kind of edit inside their mod, and the
        loadout's own mod keeping one more."""
        loadout = _loadout()
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.drop_op("theirs")
        loadout.set_op(Op(source=THEIRS_OP.replace("Theirs", "Mine"), origin="Their Mod",
                          uid="mine", order=1, authored=True))
        loadout.op("other").active = False
        loadout.set_change(_change(guid="9", path="/Values/Standard/Text"))
        loadout.held_edits = [{"uid": "h1", "origin": "Their Mod", "kind": held.TAKEN_OUT,
                               "why": held.GONE, "was": OTHER_OP, "now": "", "yours": [],
                               "active": []}]
        inside.assign(loadout)
        loadout.editing = ""
        loadout.set_op(Op(source=OTHER_OP.replace("8", "9"), origin="Their Mod",
                          uid="own", order=2, authored=True))
        inside.assign(loadout)
        return loadout, patch


class TestTheProjectsKeepThem(_Saved):
    def test_the_patchs_are_in_its_project_and_not_the_loadout(self):
        loadout, patch = self.edited()
        path = self.root / "Main.annoload"
        loadout.save(path)
        written = self.read(path)
        self.assertEqual(written["taken_out"], [])
        self.assertEqual(written["held_edits"], [])
        self.assertEqual([op["uid"] for op in written["ops"]], ["other"])
        self.assertEqual(written["changes"], [])
        kept = self.read(projects.where(loadout.projects[patch.origin].path))[projects.INSIDE]
        self.assertEqual([entry[1] for entry in kept["taken_out"]], [THEIRS_OP])
        self.assertEqual([op["uid"] for op in kept["ops"]], ["mine"])
        self.assertEqual(kept["off"], [{"origin": "Their Mod", "uid": "other", "source": OTHER_OP}])
        self.assertEqual([change["guid"] for change in kept["changes"]], ["9"])
        self.assertEqual([record["uid"] for record in kept["held_edits"]], ["h1"])
        # And the own mod's is in its project, used by this loadout alone.
        own = self.read(projects.where(loadout.projects[""].path))[projects.INSIDE]
        self.assertEqual([op["uid"] for op in own["ops"]], ["own"])

    def test_opening_puts_them_back_as_they_were(self):
        loadout, patch = self.edited()
        path = self.root / "Main.annoload"
        loadout.save(path)
        loaded = Loadout.load(path)
        for key in ("taken_out", "held_edits", "ops", "changes"):
            with self.subTest(key=key):
                self.assertEqual(loaded.to_dict()[key], loadout.to_dict()[key])
        self.assertEqual(loaded.op("mine").kept_by, patch.origin)
        self.assertEqual(loaded.op("own").kept_by, OWN)
        self.assertFalse(loaded.op("other").active)

    def test_ops_of_both_go_back_where_each_stood(self):
        loadout, patch = self.edited()
        loadout.set_op(Op(source=OTHER_OP.replace("8", "10"), origin="Their Mod", uid="own 2",
                          order=2, authored=True, kept_by=""))
        loadout.set_op(Op(source=OTHER_OP.replace("8", "11"), origin="Their Mod", uid="patch 2",
                          order=2, authored=True, kept_by=patch.origin))
        path = self.root / "Main.annoload"
        loadout.save(path)
        self.assertEqual([op.uid for op in Loadout.load(path).ops], [op.uid for op in loadout.ops])

    def test_a_save_of_nothing_new_leaves_the_projects_alone(self):
        loadout, _patch = self.edited()
        path = self.root / "Main.annoload"
        loadout.save(path)
        self.assertEqual(Loadout.load(path).projects_to_save(), [])


class TestUnloadingTheirMod(_Saved):
    """Unloading their mod takes nothing out of your patch: its project
    goes on keeping what it keeps inside that mod."""

    def kept(self, loadout, origin) -> dict:
        return self.read(projects.where(loadout.projects[origin].path)).get(projects.INSIDE, {})

    def saved_then_unloaded(self):
        loadout, patch = self.edited()
        path = self.root / "Main.annoload"
        loadout.save(path)
        before = self.kept(loadout, patch.origin)
        loadout.drop_layer("Their Mod")
        return loadout, patch, path, before

    def test_the_patchs_project_still_keeps_them_after_a_save(self):
        loadout, patch, path, before = self.saved_then_unloaded()
        self.assertNotIn(projects.where(loadout.projects[patch.origin].path).stem,
                         loadout.projects_to_save())
        loadout.save(path)
        self.assertEqual(self.kept(loadout, patch.origin), before)
        # The loadout's own mod's edits to it go with the mod, as before.
        self.assertEqual(self.kept(loadout, ""), {})

    def test_opened_without_their_mod_they_stay_kept(self):
        loadout, patch, path, before = self.saved_then_unloaded()
        loadout.save(path)
        loaded = Loadout.load(path)
        self.assertIn(patch.origin, loaded.kept_aside)
        self.assertFalse(any(op.origin == "Their Mod" for op in loaded.ops))
        self.assertEqual(loaded.projects_to_save(), [])
        loaded.save(path)
        self.assertEqual(self.kept(loaded, patch.origin), before)
        self.assertEqual(self.read(projects.where(loaded.projects[patch.origin].path))["revision"], 1)

    def test_reading_their_mod_in_again_puts_them_back(self):
        loadout, patch, _path, _before = self.saved_then_unloaded()
        # Read again: their ops as their file has them, under new uids.
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", creator="Dee", order=1))
        loadout.set_op(Op(source=THEIRS_OP, origin="Their Mod", uid="theirs again", order=1))
        loadout.set_op(Op(source=OTHER_OP, origin="Their Mod", uid="other again", order=2))
        self.assertEqual(inside.take_back(loadout, {"Their Mod"}), 5)
        self.assertEqual(loadout.kept_aside, {})
        self.assertEqual(loadout.op("mine").kept_by, patch.origin)
        self.assertIsNone(loadout.op("theirs again"))
        self.assertEqual([entry[1] for entry in loadout.taken_out], [THEIRS_OP])
        self.assertEqual(inside.keeper_of(loadout.taken_out[0]), patch.origin)
        again = loadout.op("other again")
        self.assertEqual((again.active, again.kept_by), (False, patch.origin))
        self.assertEqual([held["uid"] for held in loadout.held_edits], ["h1"])
        self.assertTrue(any(change.guid == "9" and change.kept_by == patch.origin
                            for change in loadout.changes.values()))

    def test_an_unload_undone_is_not_kept_twice(self):
        loadout, patch = self.edited()
        path = self.root / "Main.annoload"
        loadout.save(path)
        before = self.kept(loadout, patch.origin)
        history = UndoHistory(loadout)
        loadout.drop_layer("Their Mod")
        history.commit()
        history.undo()
        loadout.drop_layer("Their Mod")
        history.commit()
        history.undo()
        self.assertEqual(loadout.projects_to_save(), [])
        loadout.save(path)
        self.assertEqual(self.kept(loadout, patch.origin), before)

    def test_unloading_the_patch_as_well_lets_go_of_them_here(self):
        loadout, patch, path, before = self.saved_then_unloaded()
        project = projects.where(loadout.projects[patch.origin].path)
        loadout.drop_layer(patch.origin)
        self.assertEqual(loadout.kept_aside, {})
        loadout.save(path)
        # Its project is not this loadout's to write any more, and keeps them.
        self.assertEqual(self.read(project)[projects.INSIDE], before)


class TestTheKeepersHistory(_Saved):
    """What a mod of yours keeps inside somebody else's ops is its work, and
    its history holds it: a checkpoint has it, and a restore puts it back."""

    def edited_since(self, loadout, patch) -> None:
        """Every kind of the patch's edit inside their mod undone or moved."""
        loadout.editing = patch.origin
        loadout.op("other").active = True
        loadout.op("other").kept_by = None
        loadout.ops = [op for op in loadout.ops if op.uid != "mine"]
        loadout.changes = {key: change for key, change in loadout.changes.items() if change.guid != "9"}
        loadout.set_change(_change(guid="10", path="/Values/Standard/Text"))
        inside.assign(loadout)

    def test_a_checkpoint_holds_what_each_keeps_inside_their_mod(self):
        loadout, patch = self.edited()
        kept = loadout.checkpoint("Then", origin=patch.origin, loadout="Main").inside
        self.assertEqual([entry[1] for entry in kept["taken_out"]], [THEIRS_OP])
        self.assertEqual([op["uid"] for op in kept["ops"]], ["mine"])
        self.assertEqual(kept["off"], [{"origin": "Their Mod", "uid": "other", "source": OTHER_OP}])
        self.assertEqual([change["guid"] for change in kept["changes"]], ["9"])
        self.assertEqual([record["uid"] for record in kept["held_edits"]], ["h1"])
        # Each edit noted with the loadout it was made in, as the mod's own.
        self.assertEqual(kept["changes"][0]["made_in"], "Main")
        # And the own mod's checkpoint has the own mod's, and none of the patch's.
        own = loadout.checkpoint("Then").inside
        self.assertEqual([op["uid"] for op in own["ops"]], ["own"])
        self.assertEqual(set(own), {"ops"})

    def test_an_edit_inside_their_mod_is_work_since_the_checkpoint(self):
        loadout, patch = self.edited()
        loadout.checkpoint("Then", origin=patch.origin)
        self.assertFalse(loadout.changed_since_checkpoint(patch.origin))
        loadout.op("other").active = True
        inside.assign(loadout)
        self.assertTrue(loadout.changed_since_checkpoint(patch.origin))
        # The own mod's work did not move.
        loadout.checkpoint("Then")
        self.assertFalse(loadout.changed_since_checkpoint(""))

    def test_and_the_history_says_so(self):
        """R3-41, hands-on run 3: an op of theirs switched back on, the
        working copy was "saved as the latest checkpoint"."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.history_dialog import HistoryDialog

        QApplication.instance() or QApplication([])
        loadout, patch = self.edited()
        loadout.checkpoint("Then", origin=patch.origin)
        loadout.op("other").active = True
        inside.assign(loadout)
        dialog = HistoryDialog(loadout, origin=patch.origin)
        self.addCleanup(dialog.deleteLater)
        working = dialog.mod.checkpoints.item(0).text()
        self.assertNotIn("saved as the latest checkpoint", working)
        self.assertIn("edits inside other mods' ModOps moved since the last checkpoint", working)
        self.assertTrue(dialog.mod.checkpoint_button.isEnabled())

    def test_where_their_ops_stand_or_what_their_reading_calls_them_is_not(self):
        loadout, patch = self.edited()
        loadout.checkpoint("Then", origin=patch.origin)
        # Their mod read again: an op more before them, and new uids.
        loadout.ops.insert(0, Op(source=THEIRS_OP, origin="Their Mod", uid="first", order=0))
        loadout.op("other").uid = "other again"
        self.assertFalse(loadout.changed_since_checkpoint(patch.origin))

    def test_a_restore_puts_them_back(self):
        loadout, patch = self.edited()
        then = loadout.checkpoint("Then", origin=patch.origin)
        own_before = inside_says(loadout.kept_inside(""))
        self.edited_since(loadout, patch)
        histories.restore_mod(loadout, patch.origin, then, "Main")
        self.assertEqual(inside_says(loadout.kept_inside(patch.origin)), inside_says(then.inside))
        self.assertEqual(loadout.op("mine").kept_by, patch.origin)
        self.assertEqual((loadout.op("other").active, loadout.op("other").kept_by),
                         (False, patch.origin))
        self.assertFalse(any(change.guid == "10" for change in loadout.changes.values()))
        self.assertEqual([op.source for op in loadout.ops if op.source == THEIRS_OP], [])
        self.assertNotIn("made_in", loadout.op("mine").to_dict())
        # The own mod's edits inside their mod are its, and stay.
        self.assertEqual(inside_says(loadout.kept_inside("")), own_before)
        # Their mod is read again, as after any edit to it.
        self.assertIn("Their Mod", stale.waiting(loadout))
        # And what it kept before the restore is a checkpoint to go back to.
        before = loadout.history_of(patch.origin)[-2]
        self.assertEqual(before.label, histories.BEFORE % then.when)
        self.assertEqual([change["guid"] for change in before.inside["changes"]], ["10"])

    def test_restoring_the_own_mod_puts_back_its_own(self):
        loadout, patch = self.edited()
        then = loadout.checkpoint("Then")
        patch_before = inside_says(loadout.kept_inside(patch.origin))
        loadout.ops = [op for op in loadout.ops if op.uid != "own"]
        histories.restore_mod(loadout, "", then, "Main")
        self.assertEqual(loadout.op("own").kept_by, OWN)
        self.assertEqual(inside_says(loadout.kept_inside(patch.origin)), patch_before)

    def test_a_restore_that_keeps_them_reads_nothing_again(self):
        loadout, patch = self.edited()
        then = loadout.checkpoint("Then", origin=patch.origin)
        stale.settle(loadout)
        histories.restore_mod(loadout, patch.origin, then, "Main")
        self.assertEqual(stale.waiting(loadout), [])

    def test_inside_a_mod_not_loaded_they_are_set_aside_for_the_project(self):
        loadout, patch = self.edited()
        then = loadout.checkpoint("Then", origin=patch.origin)
        self.edited_since(loadout, patch)
        loadout.drop_layer("Their Mod")
        histories.restore_mod(loadout, patch.origin, then, "Main")
        self.assertEqual(inside_says(loadout.kept_aside[patch.origin]), inside_says(then.inside))
        # And reading it in again puts them back where they were.
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", creator="Dee", order=1))
        loadout.set_op(Op(source=THEIRS_OP, origin="Their Mod", uid="theirs again", order=1))
        loadout.set_op(Op(source=OTHER_OP, origin="Their Mod", uid="other again", order=2))
        inside.take_back(loadout, {"Their Mod"})
        self.assertEqual(inside_says(loadout.kept_inside(patch.origin)), inside_says(then.inside))

    def test_the_project_file_keeps_them_in_its_history(self):
        loadout, patch = self.edited()
        loadout.checkpoint("Then", origin=patch.origin)
        path = self.root / "Main.annoload"
        loadout.save(path)
        written = self.read(projects.where(loadout.projects[patch.origin].path))["history"][-1]
        self.assertEqual([op["uid"] for op in written["inside"]["ops"]], ["mine"])
        loaded = Loadout.load(path).latest_checkpoint_of(patch.origin)
        self.assertEqual(inside_says(loaded.inside), inside_says(loadout.kept_inside(patch.origin)))
        self.assertEqual(Checkpoint.from_dict(written).inside, written["inside"])

    def test_a_checkpoint_of_nothing_inside_writes_nothing(self):
        loadout = Loadout(own=own_record(creator="Ay"))
        self.assertNotIn("inside", loadout.checkpoint("Then").to_dict())


class TestAnotherLoadoutWithThePatch(unittest.TestCase):
    """The patch's project carries its edits into a loadout that reads their
    mod for itself: an op of theirs it switched off is found by its text."""

    def data(self, active=True, kept_by=None):
        op = {"source": OTHER_OP, "origin": "Their Mod", "uid": "elsewhere", "active": active}
        if kept_by is not None:
            op["kept_by"] = kept_by
        return {"ops": [op], "changes": [{"op_uid": "elsewhere", "active": active}],
                "layers": [{"origin": "Their Mod"}, {"origin": "P", "patches": "Their Mod"}]}

    def test_an_op_it_switched_off_is_off_here_too(self):
        data = self.data()
        projects.join(data, {"P": {projects.INSIDE: {"off": [
            {"origin": "Their Mod", "uid": "not here", "source": OTHER_OP}]}}})
        self.assertEqual((data["ops"][0]["active"], data["ops"][0]["kept_by"]), (False, "P"))
        self.assertFalse(data["changes"][0]["active"])

    def test_one_it_no_longer_has_off_is_on_again(self):
        data = self.data(active=False, kept_by="P")
        projects.join(data, {"P": {}})
        self.assertTrue(data["ops"][0]["active"])
        self.assertNotIn("kept_by", data["ops"][0])
        self.assertTrue(data["changes"][0]["active"])

    def test_found_by_text_it_is_the_one_it_had_off(self):
        # An earlier op of the same text, switched on, is not the one: the
        # one the loadout has off for the project stays off.
        data = self.data(active=False, kept_by="P")
        data["ops"].insert(0, {"source": OTHER_OP, "origin": "Their Mod", "uid": "first"})
        data["changes"].append({"op_uid": "first", "active": True})
        projects.join(data, {"P": {projects.INSIDE: {"off": [
            {"origin": "Their Mod", "uid": "not here", "source": OTHER_OP}]}}})
        first, elsewhere = data["ops"]
        self.assertEqual((first.get("active", True), first.get("kept_by")), (True, None))
        self.assertEqual((elsewhere["active"], elsewhere["kept_by"]), (False, "P"))
        self.assertEqual([change["active"] for change in data["changes"]], [False, True])

    def test_two_off_by_the_same_text_are_two_ops(self):
        data = self.data()
        data["ops"].append({"source": OTHER_OP, "origin": "Their Mod", "uid": "second"})
        projects.join(data, {"P": {projects.INSIDE: {"off": [
            {"origin": "Their Mod", "uid": "gone", "source": OTHER_OP},
            {"origin": "Their Mod", "uid": "gone too", "source": OTHER_OP}]}}})
        self.assertEqual([op["active"] for op in data["ops"]], [False, False])

    def test_the_own_mods_are_left_to_the_loadout(self):
        data = self.data(active=False, kept_by="")
        projects.join(data, {"P": {}})
        self.assertFalse(data["ops"][0]["active"])


class TestPlayAndTheExport(_Edited):
    """Both keep edits to one mod: Play writes them all into one generated
    copy, and the patch's export works out its own alone."""

    def setUp(self):
        super().setUp()
        self.patch = patching.make(self.loadout, NAME, "My patch")

    def op(self, words: str):
        return next(op for op in self.loadout.ops if words in op.source and not op.authored)

    def type_over(self, words: str, new: str, keeper: str):
        """As the editor does it: theirs taken out, yours in its place."""
        self.loadout.editing = keeper
        op = self.op(words)
        mine = replace(op, source=op.source.replace(words, new), authored=True,
                       uid=op.uid + "m", within=list(op.within), kept_by=None)
        self.loadout.drop_op(op.uid)
        self.loadout.set_op(mine)
        inside.assign(self.loadout)
        return mine

    def test_the_generated_copy_holds_both(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>", self.patch.origin)
        self.loadout.editing = ""
        self.op("<Residents>99</Residents>").active = False
        inside.assign(self.loadout)
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        text = done.written[PATCH].decode("utf-8")
        self.assertEqual(text.count('Skip="1"'), 2)
        self.assertIn("<Name>Mine</Name>", text)

    def test_the_patchs_export_has_its_own_alone(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>", self.patch.origin)
        self.loadout.editing = ""
        self.loadout.drop_op(self.op("<Residents>99</Residents>").uid)
        inside.assign(self.loadout)
        shared = sharing.work_out(self.loadout, self.patch.origin, self.copy, self.index)
        # The own mod's op taken out would be listed as left out: it is not
        # the patch's to carry.
        self.assertEqual((len(shared.turned), shared.left, shared.trouble), (1, [], ""))
        alone = edits.work_out(self.loadout, NAME, self.copy, self.index, keeper=self.patch.origin)
        self.assertEqual(alone.written[PATCH].decode("utf-8").count('Skip="1"'), 1)
        # And the loadout itself is as it was.
        self.assertEqual(len(self.loadout.taken_out), 2)

    def test_the_patchs_come_first_where_both_put_one_at_a_place(self):
        theirs = self.op("<Name>Theirs</Name>")
        own = Op(source=theirs.source.replace("Theirs", "Own"), origin=NAME, uid="own",
                 order=theirs.order, within=list(theirs.within), authored=True, kept_by="")
        patched = Op(source=theirs.source.replace("Theirs", "Patched"), origin=NAME, uid="patched",
                     order=theirs.order, within=list(theirs.within), authored=True,
                     kept_by=self.patch.origin)
        self.loadout.drop_op(theirs.uid)
        self.loadout.set_op(own)
        self.loadout.set_op(patched)
        inside.assign(self.loadout)
        text = edits.files_of(self.loadout, NAME)[PATCH]
        self.assertLess(text.index("<Name>Patched</Name>"), text.index("<Name>Own</Name>"))


class TestAnUpdateKeepsTheKeeper(_Edited):
    def test_an_edit_held_by_an_update_is_still_the_patchs(self):
        patch = patching.make(self.loadout, NAME, "My patch")
        self.loadout.editing = patch.origin
        theirs = next(op for op in self.loadout.ops if "<Name>Theirs</Name>" in op.source)
        self.loadout.drop_op(theirs.uid)
        inside.assign(self.loadout)
        (self.copy / PATCH).write_text(THEIRS.split('  <ModOp Type="replace"')[0] + "</ModOps>\n",
                                       encoding="utf-8")
        report = reader.analyse(self.link, self.index, origin=NAME)
        stale.take(self.loadout, {NAME: report})
        (record,) = self.loadout.held_edits
        self.assertEqual(record["kept_by"], patch.origin)

    def test_an_op_switched_off_stays_the_keepers(self):
        patch = patching.make(self.loadout, NAME, "My patch")
        self.loadout.editing = patch.origin
        next(op for op in self.loadout.ops if "<Residents>99</Residents>" in op.source).active = False
        inside.assign(self.loadout)
        self.loadout.editing = ""
        (self.copy / PATCH).write_text(THEIRS.replace("<!-- the author's -->", "<!-- 1.1 -->"),
                                       encoding="utf-8")
        report = reader.analyse(self.link, self.index, origin=NAME)
        stale.take(self.loadout, {NAME: report})
        (op,) = [op for op in self.loadout.ops
                 if "<Residents>99</Residents>" in op.source and op.origin == NAME]
        self.assertEqual((op.active, op.kept_by), (False, patch.origin))


if __name__ == "__main__":
    unittest.main()
