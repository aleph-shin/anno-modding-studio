"""Undo and redo over what a mod writes out -- see ``core/undo.py``."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import patching  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    OP_ADD,
    Change,
    Loadout,
    ModLayer,
    Op,
    Unread,
)
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402


def replace(value, origin="", guid="100", tag="Residents", old="8"):
    return Change(
        guid=guid,
        asset_label="Farmer Residence",
        path="/Values/Residence7/%s" % tag,
        tag=tag,
        old_value=old,
        new_value=value,
        origin=origin,
    )


class UndoCase(unittest.TestCase):
    def setUp(self):
        self.loadout = Loadout()
        self.loadout.set_change(replace("12"))
        self.history = UndoHistory(self.loadout)

    def value(self, origin=""):
        change = self.loadout.find("100", "/Values/Residence7/Residents", origin=origin)
        return None if change is None else change.new_value


class TestRecordingAStep(UndoCase):
    def test_nothing_moved_is_not_a_step(self):
        self.assertIsNone(self.history.commit())
        self.assertFalse(self.history.can_undo)

    def test_an_edit_goes_back_and_comes_again(self):
        self.loadout.set_change(replace("20"))
        self.assertIsNotNone(self.history.commit())

        self.history.undo()
        self.assertEqual(self.value(), "12")
        self.assertTrue(self.history.can_redo)

        self.history.redo()
        self.assertEqual(self.value(), "20")

    def test_a_new_change_is_taken_away_again(self):
        self.loadout.set_change(replace("3", tag="Workforce", old="1"))
        self.history.commit()
        self.history.undo()
        self.assertEqual(len(self.loadout), 1)

    def test_a_reverted_change_comes_back(self):
        self.loadout.remove("100", "/Values/Residence7/Residents")
        step = self.history.commit()
        self.assertIn("reverting", step.label)
        self.history.undo()
        self.assertEqual(self.value(), "12")

    def test_an_edit_made_in_place_is_still_seen(self):
        """A payload edit changes the change object itself rather than
        replacing it, so the same object says something new."""
        added = Change(
            guid="100",
            asset_label="Farmer Residence",
            path="/Values/Residence7",
            tag="Item",
            op=OP_ADD,
            payload="<Item><Amount>1</Amount></Item>",
        )
        self.loadout.set_change(added)
        self.history.commit()
        added.payload = "<Item><Amount>5</Amount></Item>"
        self.assertIsNotNone(self.history.commit())

        self.history.undo()
        self.assertEqual(
            self.loadout.changes[added.key].payload, "<Item><Amount>1</Amount></Item>"
        )

    def test_switching_a_change_off_says_so(self):
        change = self.loadout.find("100", "/Values/Residence7/Residents")
        change.active = False
        step = self.history.commit()
        self.assertEqual(step.label, "switching Residents on Farmer Residence off")
        self.history.undo()
        self.assertTrue(
            self.loadout.find("100", "/Values/Residence7/Residents").active
        )

    def test_a_label_given_is_the_label_kept(self):
        self.loadout.set_change(replace("20"))
        self.history.commit("typing 20")
        self.assertEqual(self.history.undo_label, "typing 20")

    def test_a_new_step_after_undoing_drops_what_could_be_redone(self):
        self.loadout.set_change(replace("20"))
        self.history.commit()
        self.history.undo()
        self.loadout.set_change(replace("30"))
        self.history.commit()
        self.assertFalse(self.history.can_redo)

    def test_it_only_goes_back_so_far(self):
        history = UndoHistory(self.loadout, limit=3)
        for value in ("20", "21", "22", "23", "24"):
            self.loadout.set_change(replace(value))
            history.commit()
        while history.undo() is not None:
            pass
        self.assertEqual(self.value(), "21")


class TestWhatItPutsBack(UndoCase):
    def test_what_it_puts_back_is_a_copy(self):
        """Changing the loadout afterwards must not reach into the history,
        or redoing would replay whatever it had been changed to since."""
        self.loadout.set_change(replace("20"))
        self.history.commit()
        self.history.undo()
        self.loadout.find("100", "/Values/Residence7/Residents").new_value = "999"
        self.history.redo()
        self.assertEqual(self.value(), "20")

    def test_what_was_never_recorded_is_left_alone(self):
        """Something that moved without being a step stays as it is, rather
        than being taken back along with the step before it."""
        self.loadout.set_change(replace("20"))
        self.history.commit()
        self.loadout.set_change(replace("5", tag="Workforce", old="1"))
        self.history.undo()
        self.assertEqual(self.value(), "12")
        self.assertIsNotNone(
            self.loadout.find("100", "/Values/Residence7/Workforce")
        )

    def test_forgetting_starts_over(self):
        self.loadout.set_change(replace("20"))
        self.history.commit()
        self.history.reset()
        self.assertFalse(self.history.can_undo)
        self.assertIsNone(self.history.undo())
        self.assertEqual(self.value(), "20")


class TestModsAsWellAsChanges(unittest.TestCase):
    def setUp(self):
        self.loadout = Loadout()
        self.layer = ModLayer(origin="Theirs", name="Their Mod")
        self.loadout.set_layer(self.layer)
        self.loadout.set_change(replace("12", origin="Theirs"))
        self.loadout.set_op(Op(expression="//Values/Trader", origin="Theirs"))
        self.loadout.unread.append(Unread(origin="Theirs", label="an op"))
        self.history = UndoHistory(self.loadout)

    def test_switching_a_mod_off_comes_back_on(self):
        self.layer.active = False
        step = self.history.commit()
        self.assertEqual(step.label, "switching Their Mod off")
        self.history.undo()
        self.assertTrue(self.loadout.layer("Theirs").active)

    def test_unloading_a_mod_puts_all_of_it_back(self):
        self.loadout.drop_layer("Theirs")
        self.history.commit()
        self.history.undo()
        self.assertIs(self.loadout.layer("Theirs"), self.layer)
        self.assertEqual(len(self.loadout.changes_of("Theirs")), 1)
        self.assertEqual(len(self.loadout.ops_of("Theirs")), 1)
        self.assertEqual(len(self.loadout.unread_of("Theirs")), 1)

        self.history.redo()
        self.assertIsNone(self.loadout.layer("Theirs"))
        self.assertEqual(self.loadout.unread, [])

    def test_an_op_switched_off_is_a_step(self):
        self.loadout.ops[0].active = False
        self.assertIsNotNone(self.history.commit())
        self.history.undo()
        self.assertTrue(self.loadout.ops[0].active)

    def test_editing_a_mods_details_is_not_a_step(self):
        """Its name and version are what the Export tab edits, not the change
        set, and a step for every letter typed there would bury the real ones."""
        self.layer.version = "2.0"
        self.assertIsNone(self.history.commit())

    def test_which_asset_a_step_is_about(self):
        self.loadout.set_change(replace("30", origin="Theirs"))
        step = self.history.commit()
        self.assertEqual(step.asset(), "100")


class TestCarryingOnWithAModOfYours(unittest.TestCase):
    """A mod of yours read back in is the mod the loadout is writing."""

    def setUp(self):
        self.loadout = Loadout(aim=AIM_MAKE)
        self.mine = ModLayer(origin="My Mod", name="My Mod", creator="Somebody")
        self.loadout.set_layer(self.mine)
        self.loadout.home = "My Mod"

    def test_it_counts_as_yours_whatever_name_is_on_it(self):
        self.assertTrue(patching.is_mine(self.loadout, self.mine))

    def test_the_loadout_stands_for_it_not_for_its_own_mod(self):
        self.assertEqual(self.loadout.stands_for(), "My Mod")

    def test_it_survives_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.loadout.save(path)
            loaded = Loadout.load(path)
        self.assertEqual(loaded.home, "My Mod")
        self.assertIs(loaded.home_layer(), loaded.layer("My Mod"))

    def test_unloaded_it_is_not_forgotten(self):
        """So that undoing the unload finds the loadout as it was."""
        self.loadout.drop_layer("My Mod")
        self.assertIsNone(self.loadout.home_layer())
        self.assertEqual(self.loadout.home, "My Mod")


class TestAStepIsOfTheModsItMoved(unittest.TestCase):
    """A mod read again since a step was made is not put back to what the
    step held of it: see ``core/stale.py`` for what reads one again after
    an edit."""

    def setUp(self):
        self.loadout = Loadout()
        for at, origin in enumerate(("First", "Second")):
            self.loadout.set_layer(ModLayer(origin=origin, name=origin, order=at + 1))
            self.loadout.set_op(Op(expression="//Values/Trader", origin=origin, uid=origin, order=2))
            self.loadout.set_change(self.under(origin, "12"))
            self.loadout.unread.append(Unread(origin=origin, label="an op", order=5))
        self.history = UndoHistory(self.loadout)

    def under(self, uid, value, origin=None, tag="Residents"):
        change = replace(value, origin=origin or uid, tag=tag)
        change.op_uid = uid
        change.order = 2
        return change

    def op(self, uid):
        return self.loadout.op(uid)

    def read_again(self, origin, value):
        """What a reading of ``origin`` taken in the background does, as far
        as a history can tell: its op and what that comes to are others."""
        self.op(origin).matched = [value]
        self.loadout.set_change(self.under(origin, value))
        self.loadout.unread = [
            Unread(origin=origin, label="read again", order=5) if entry.origin == origin else entry
            for entry in self.loadout.unread
        ]
        self.history.absorb()

    def test_a_step_says_which_mods_it_moved_and_from_where(self):
        self.op("First").active = False
        step = self.history.commit()
        self.assertEqual(step.reach, {"First": 2})
        self.assertEqual(step.ops_of, frozenset({"First"}))

    def test_a_change_says_so_as_well(self):
        mine = replace("3", origin="Second", tag="Workforce")
        mine.order = 7
        self.loadout.set_change(mine)
        self.assertEqual(self.history.commit().reach, {"Second": 7})

    def test_a_step_says_which_assets_an_op_it_moved_answered_to(self):
        self.op("First").matched = ["100"]
        self.history.absorb()
        self.op("First").active = False
        self.assertEqual(self.history.commit().assets, {"First": frozenset({"100"})})

    def test_a_group_kept_whole_with_ops_not_read_in_it_reaches_every_op(self):
        # What the ops inside it the Studio could not read do is not known,
        # so the mod goes wide as it does for an op carried unread.
        self.op("First").matched = ["100"]
        self.op("First").unread_at = ["200"]
        self.history.absorb()
        self.op("First").active = False
        step = self.history.commit()
        self.assertEqual(step.reach, {"First": 2})
        self.assertEqual(step.assets, {})

    def test_what_only_moved_along_the_file_is_not_what_a_step_was_about(self):
        self.loadout.unread = [entry for entry in self.loadout.unread if entry.origin != "Second"]
        self.op("Second").matched = ["100"]
        self.loadout.set_op(Op(source="<!-- aside -->", origin="Second", uid="aside",
                               order=3, aside=True, scope=True))
        self.history.absorb()
        # An op put in ahead of them, as the Mod file tab does it.
        self.loadout.make_room("Second", 1)
        self.loadout.set_op(Op(expression="//Values/New", origin="Second", uid="new",
                               order=2, matched=["9"]))
        step = self.history.commit()
        self.assertEqual(step.reach, {"Second": 2})
        self.assertEqual(step.assets, {"Second": frozenset({"9"})})

    def test_two_ops_swapped_are_what_a_step_was_about(self):
        # Not moved along to make room: each now stands where the other
        # did, and what they add to an asset comes in the other order.
        self.op("First").matched = ["100"]
        self.loadout.set_op(Op(expression="//Values/Other", origin="First", uid="other",
                               order=3, matched=["300"]))
        self.history.absorb()
        self.op("First").order, self.op("other").order = 3, 2
        step = self.history.commit()
        self.assertEqual(step.assets, {"First": frozenset({"100", "300"})})

    def test_two_changes_swapped_are_what_a_step_was_about(self):
        later = replace("4", origin="First", guid="200")
        later.order = 3
        self.loadout.set_change(later)
        self.history.absorb()
        first = self.loadout.find("100", "/Values/Residence7/Residents", origin="First")
        first.order, later.order = 3, 2
        step = self.history.commit()
        self.assertEqual(step.assets, {"First": frozenset({"100", "200"})})

    def test_a_step_says_what_it_added_or_changed(self):
        """For the rows to select after an Apply: not what it took away, and
        not what only moved along the file to make room."""
        from anno_modding_studio.core.undo import touched

        mine = replace("3", origin="Second", tag="Workforce")
        mine.order = 7
        self.loadout.set_change(mine)
        self.loadout.set_op(Op(expression="//Values/New", origin="Second", uid="new", order=8))
        self.loadout.unread.append(Unread(origin="Second", source="<x/>", label="new", order=9))
        # Moved along, and nothing else about them is different.
        self.op("Second").order = 3
        along = self.under("Second", "12")
        along.order = 3
        self.loadout.set_change(along)
        # Taken away.
        self.loadout.drop_op("First")
        step = self.history.commit()
        keys, uids, unread = touched(step)
        self.assertEqual(keys, {mine.key})
        self.assertEqual(uids, {"new"})
        self.assertEqual(unread, {("Second", 9)})

    def test_what_was_absorbed_is_no_step(self):
        self.read_again("Second", "24")
        self.assertFalse(self.history.can_undo)
        self.assertIsNone(self.history.commit())

    def test_undo_leaves_another_mod_as_it_is_now(self):
        self.op("First").active = False
        self.history.commit()
        self.read_again("Second", "24")

        self.history.undo()
        self.assertTrue(self.op("First").active)
        self.assertEqual(self.op("Second").matched, ["24"])
        self.assertEqual(self.loadout.find("100", "/Values/Residence7/Residents", origin="Second").new_value, "24")
        self.assertEqual(
            [(entry.origin, entry.label) for entry in self.loadout.unread],
            [("First", "an op"), ("Second", "read again")],
        )

        self.history.redo()
        self.assertFalse(self.op("First").active)
        self.assertEqual(self.op("Second").matched, ["24"])

    def test_an_op_carried_unread_goes_back_in_its_own_mod_only(self):
        self.loadout.unread = [entry for entry in self.loadout.unread if entry.origin != "First"]
        step = self.history.commit()
        self.assertEqual(step.unread_of, frozenset({"First"}))
        self.read_again("Second", "24")
        self.history.undo()
        self.assertEqual(
            sorted((entry.origin, entry.label) for entry in self.loadout.unread),
            [("First", "an op"), ("Second", "read again")],
        )

    def test_what_an_op_came_to_since_goes_with_the_op(self):
        self.loadout.set_op(Op(expression="//Values/New", origin="First", uid="typed", order=3))
        self.loadout.set_change(self.under("typed", "40", origin="First", tag="Workforce"))
        self.history.commit()
        # Read again, it comes to something at another place.
        self.loadout.remove_key(self.under("typed", "40", origin="First", tag="Workforce").key)
        self.loadout.set_change(self.under("typed", "50", origin="First", tag="Happiness"))
        self.history.absorb()

        self.history.undo()
        self.assertIsNone(self.op("typed"))
        self.assertEqual(
            [change.tag for change in self.loadout.changes_of("First")], ["Residents"]
        )
        self.assertIsNone(self.history.commit())


if __name__ == "__main__":
    unittest.main()
