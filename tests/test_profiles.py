"""A loadout's profiles: one mod list, several sets of switches over it.

See ``docs/proposals/profiles-proposal.md``. Phase 1 is the model alone --
nothing a player sees yet -- and everything after it leans on ``layer.active``
being the chosen profile's switches and nothing else.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core.changes import (  # noqa: E402
    LOADOUT_FORMAT,
    Change,
    Loadout,
    LoadoutError,
    ModLayer,
    own_record,
)
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402


def _loadout(*origins: str) -> Loadout:
    loadout = Loadout(own=own_record(loads_last=True))
    for origin in origins:
        loadout.set_layer(ModLayer(origin=origin, name=origin))
    return loadout


def _switches(loadout: Loadout) -> dict:
    return {layer.origin: layer.active for layer in loadout.layers}


class _Disk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def round_trip(self, loadout: Loadout, name: str = "Anno 1800 loadout") -> Loadout:
        path = self.root / (name + ".annoload")
        loadout.save(path)
        return Loadout.load(path)

    def written(self, loadout: Loadout, name: str = "Anno 1800 loadout") -> dict:
        path = self.root / (name + ".annoload")
        loadout.save(path)
        return json.loads(path.read_text(encoding="utf-8"))


class TestChoosingAProfile(unittest.TestCase):
    def setUp(self):
        self.loadout = _loadout("A", "B", "C")
        self.loadout.rename_profile(self.loadout.profile, "Profile1")

    def test_a_loadout_starts_as_one_profile_called_main_with_every_switch_its_layers(self):
        """Not named after the loadout, which it would repeat beside it."""
        loadout = _loadout("A", "B")
        self.assertEqual((loadout.profile_names(), loadout.profile), (["Main"], "Main"))
        loadout.layer("B").active = False
        self.assertEqual(loadout.switches_of("Main"), {"A": True, "B": False})

    def test_a_new_profile_is_switched_as_the_chosen_one_and_is_not_chosen(self):
        self.loadout.layer("B").active = False
        self.loadout.add_profile("Profile2")
        self.assertEqual(self.loadout.profile, "Profile1")
        self.assertEqual(self.loadout.switches_of("Profile2"), {"A": True, "B": False, "C": True})

    def test_or_as_another_one_says(self):
        self.loadout.add_profile("Two")
        self.loadout.find_profile("Two").switches["C"] = False
        self.loadout.add_profile("Three", like="Two")
        self.assertEqual(self.loadout.switches_of("Three"), {"A": True, "B": True, "C": False})

    def test_choosing_one_puts_its_switches_in_the_layers_and_keeps_the_last(self):
        self.loadout.add_profile("Profile2")
        self.loadout.layer("A").active = False
        self.assertTrue(self.loadout.choose_profile("Profile2"))
        self.assertEqual(self.loadout.profile, "Profile2")
        self.assertEqual(_switches(self.loadout), {"A": True, "B": True, "C": True})
        self.loadout.layer("C").active = False
        self.loadout.choose_profile("Profile1")
        self.assertEqual(_switches(self.loadout), {"A": False, "B": True, "C": True})
        self.assertEqual(self.loadout.switches_of("Profile2"), {"A": True, "B": True, "C": False})

    def test_choosing_the_chosen_one_changes_nothing(self):
        self.loadout.layer("A").active = False
        self.assertFalse(self.loadout.choose_profile("Profile1"))
        self.assertFalse(self.loadout.layer("A").active)

    def test_only_the_switches_move(self):
        """Everything else about a mod is the loadout's, and is there for
        every profile: its changes, its options, its version."""
        self.loadout.set_change(Change(guid="1", asset_label="Farm", path="/Values/X", tag="X", old_value="1", new_value="2", origin="A"))
        self.loadout.layer("A").options = {"speed": "fast"}
        self.loadout.add_profile("Profile2")
        self.loadout.choose_profile("Profile2")
        self.assertEqual(len(self.loadout.changes_of("A")), 1)
        self.assertEqual(self.loadout.layer("A").options, {"speed": "fast"})

    def test_a_mod_that_arrives_is_on_where_it_arrived_and_off_in_the_rest(self):
        self.loadout.add_profile("Profile2")
        self.loadout.set_layer(ModLayer(origin="D", name="D"))
        self.assertTrue(self.loadout.layer("D").active)
        self.assertFalse(self.loadout.switches_of("Profile2")["D"])
        self.loadout.choose_profile("Profile2")
        self.assertFalse(self.loadout.layer("D").active)

    def test_one_that_is_not_there_cannot_be_chosen(self):
        with self.assertRaises(KeyError):
            self.loadout.choose_profile("Nope")


class TestNamingAndDeleting(unittest.TestCase):
    def setUp(self):
        self.loadout = _loadout("A", "B")
        self.loadout.rename_profile(self.loadout.profile, "Profile1")
        self.loadout.add_profile("Profile2")

    def test_a_name_is_needed_and_is_its_own(self):
        for name in ("", "   ", "profile2", "PROFILE1"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.loadout.add_profile(name)
        with self.assertRaises(ValueError):
            self.loadout.rename_profile("Profile1", "Profile2")

    def test_renaming_the_chosen_one_keeps_it_chosen(self):
        self.loadout.rename_profile("Profile1", "Everything")
        self.assertEqual(self.loadout.profile, "Everything")
        self.assertEqual(self.loadout.profile_names(), ["Everything", "Profile2"])
        # A change of case alone is a rename, not a clash with itself.
        self.loadout.rename_profile("Everything", "everything")
        self.assertEqual(self.loadout.profile, "everything")

    def test_the_chosen_one_can_go_and_the_first_of_the_rest_is_chosen(self):
        self.loadout.find_profile("Profile2").switches["B"] = False
        self.loadout.delete_profile("Profile1")
        self.assertEqual(self.loadout.profile_names(), ["Profile2"])
        self.assertEqual(self.loadout.profile, "Profile2")
        self.assertEqual(_switches(self.loadout), {"A": True, "B": False})

    def test_the_last_one_cannot(self):
        self.loadout.delete_profile("Profile2")
        with self.assertRaises(ValueError):
            self.loadout.delete_profile("Profile1")

    def test_a_name_is_given_only_to_a_profile_with_none(self):
        self.loadout.name_first_profile()
        self.assertEqual(self.loadout.profile_names(), ["Profile1", "Profile2"])
        alone = _loadout("A")
        alone.profiles[0].name = alone.profile = ""
        alone.name_first_profile()
        self.assertEqual((alone.profile_names(), alone.profile), (["Main"], "Main"))


class TestTheFile(_Disk):
    def setUp(self):
        super().setUp()
        self.loadout = _loadout("A", "B", "C")

    def test_every_profile_comes_back_and_the_chosen_one_is_in_the_layers(self):
        self.loadout.rename_profile(self.loadout.profile, "Profile1")
        self.loadout.add_profile("Profile2")
        self.loadout.layer("A").active = False
        self.loadout.choose_profile("Profile2")
        self.loadout.layer("C").active = False
        loaded = self.round_trip(self.loadout)
        self.assertEqual(loaded.profile_names(), ["Profile1", "Profile2"])
        self.assertEqual(loaded.profile, "Profile2")
        self.assertEqual(_switches(loaded), {"A": True, "B": True, "C": False})
        self.assertEqual(loaded.switches_of("Profile1"), {"A": False, "B": True, "C": True})
        loaded.choose_profile("Profile1")
        self.assertEqual(_switches(loaded), {"A": False, "B": True, "C": True})

    def test_one_profile_or_more_the_file_is_this_format(self):
        """One profile was written as format 2, which an older build reads
        whole.  No more: every loadout keeps its own mod as "own" (format
        6), and a build that reads 5 would open it as one whose own mod is
        new, and write that back over it."""
        self.assertEqual(self.written(self.loadout)["format"], LOADOUT_FORMAT)
        self.loadout.add_profile("Profile2")
        self.assertEqual(self.written(self.loadout)["format"], LOADOUT_FORMAT)

    def test_one_with_no_name_is_written_with_the_default(self):
        self.loadout.profiles[0].name = self.loadout.profile = ""
        self.assertEqual(self.written(self.loadout, "First")["profile"], "Main")
        self.assertEqual(self.round_trip(self.loadout, "Second").profile_names(), ["Main"])

    def test_a_mod_gone_from_the_loadout_is_gone_from_every_profile(self):
        self.loadout.add_profile("Profile2")
        self.loadout.drop_layer("B")
        for entry in self.written(self.loadout)["profiles"]:
            self.assertEqual(sorted(entry["switches"]), ["A", "C"])

    def test_the_layers_say_what_the_chosen_one_has_on(self):
        """A build that reads format 2 may have switched a mod since and
        written the layers alone."""
        self.loadout.rename_profile(self.loadout.profile, "Main")
        data = self.written(self.loadout)
        data["layers"][0]["active"] = False
        path = self.root / "edited.annoload"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(Loadout.load(path).switches_of("Main")["A"])

    def test_a_file_edited_by_hand_is_read_as_well_as_it_can_be(self):
        self.loadout.rename_profile(self.loadout.profile, "Main")
        self.loadout.add_profile("Other")
        data = self.written(self.loadout)
        data["profile"] = "Gone"
        data["profiles"].append({"name": "other", "switches": {}})
        data["profiles"].append({"name": "", "switches": {}})
        data["profiles"].append("not a profile")
        path = self.root / "edited.annoload"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = Loadout.load(path)
        self.assertEqual(loaded.profile_names(), ["Main", "Other"])
        self.assertEqual(loaded.profile, "Main")

    def test_profiles_that_are_not_a_list_are_refused(self):
        data = self.written(self.loadout)
        data["profiles"] = {"Main": {}}
        path = self.root / "damaged.annoload"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(LoadoutError):
            Loadout.load(path)


class TestUndoAcrossProfiles(unittest.TestCase):
    """A switch flipped is an edit to the chosen profile, and undo takes it
    back there -- even with another profile chosen since. Choosing a profile
    is not a step."""

    def setUp(self):
        self.loadout = _loadout("A", "B")
        self.loadout.rename_profile(self.loadout.profile, "Profile1")
        self.loadout.add_profile("Profile2")
        self.history = UndoHistory(self.loadout)

    def flip_a_in_profile1(self):
        self.loadout.layer("A").active = False
        self.assertIsNotNone(self.history.commit())
        self.loadout.choose_profile("Profile2")

    def test_choosing_a_profile_is_not_a_step(self):
        self.loadout.find_profile("Profile2").switches["A"] = False
        self.loadout.choose_profile("Profile2")
        self.assertIsNone(self.history.commit())
        self.assertFalse(self.history.can_undo)

    def test_undoing_a_switch_made_in_another_profile_takes_it_back_there(self):
        self.flip_a_in_profile1()
        self.history.undo()
        self.assertEqual(_switches(self.loadout), {"A": True, "B": True})
        self.assertTrue(self.loadout.switches_of("Profile1")["A"])
        self.history.redo()
        self.assertTrue(self.loadout.layer("A").active)
        self.assertFalse(self.loadout.switches_of("Profile1")["A"])

    def test_only_what_it_moved(self):
        """The rest of that profile is as it was left, which may be later."""
        self.loadout.layer("A").active = False
        self.history.commit()
        self.loadout.layer("B").active = False
        self.loadout.choose_profile("Profile2")
        self.history.undo()
        self.assertEqual(self.loadout.switches_of("Profile1"), {"A": True, "B": False})

    def test_and_back_in_that_profile_it_is_undone_in_the_layers(self):
        self.flip_a_in_profile1()
        self.loadout.choose_profile("Profile1")
        self.history.undo()
        self.assertTrue(self.loadout.layer("A").active)

    def test_a_renamed_profile_is_still_the_one_it_was_made_in(self):
        self.flip_a_in_profile1()
        self.loadout.rename_profile("Profile1", "Main")
        self.history.undo()
        self.assertTrue(self.loadout.switches_of("Main")["A"])
        self.assertTrue(self.loadout.layer("A").active)

    def test_a_mod_unloaded_in_one_profile_comes_back_to_each_as_it_had_it(self):
        self.loadout.find_profile("Profile2").switches["A"] = False
        self.loadout.drop_layer("A")
        self.history.commit()
        self.loadout.choose_profile("Profile2")
        self.history.undo()
        self.assertFalse(self.loadout.layer("A").active)
        self.loadout.choose_profile("Profile1")
        self.assertTrue(self.loadout.layer("A").active)

    def test_an_edit_is_the_loadouts_whichever_profile_is_chosen(self):
        self.loadout.set_change(Change(guid="1", asset_label="Farm", path="/Values/X", tag="X", old_value="1", new_value="2", origin="A"))
        self.history.commit()
        self.loadout.choose_profile("Profile2")
        self.history.undo()
        self.assertEqual(self.loadout.changes_of("A"), [])


class TestReadingTheFolderIn(unittest.TestCase):
    """The folder's switches are the live profile's, which need not be the
    chosen one. See Loadout.read_into."""

    def setUp(self):
        self.loadout = _loadout("A", "B")
        self.loadout.rename_profile(self.loadout.profile, "Profile1")
        self.loadout.add_profile("Profile2").switches["B"] = False
        self.loadout.add_profile("Profile3")
        self.loadout.choose_profile("Profile2")

    def read(self, live, folder: dict):
        before = self.loadout.live_switches()
        for origin, on in folder.items():
            layer = self.loadout.layer(origin)
            if layer is None:
                self.loadout.set_layer(ModLayer(origin=origin, name=origin, active=on))
            else:
                layer.active = on
        self.loadout.read_into(live, before)

    def test_with_another_profile_live_the_folder_goes_to_that_one(self):
        self.read("Profile1", {"A": False, "B": True, "C": True})
        self.assertEqual(self.loadout.switches_of("Profile1"), {"A": False, "B": True, "C": True})
        # The chosen one keeps its own, and a mod new to it takes the folder's word.
        self.assertEqual(_switches(self.loadout), {"A": True, "B": False, "C": True})
        # The rest are not touched, and a mod new to them is off.
        self.assertEqual(self.loadout.switches_of("Profile3"), {"A": True, "B": True, "C": False})

    def test_with_the_chosen_one_live_or_none_the_folder_is_its_say(self):
        for live in ("Profile2", None):
            with self.subTest(live=live):
                self.read(live, {"A": False, "B": True})
                self.assertEqual(_switches(self.loadout), {"A": False, "B": True})
                self.assertEqual(self.loadout.switches_of("Profile1"), {"A": True, "B": True})


class TestForgettingACollection(unittest.TestCase):
    def test_nothing_is_said_to_have_come_with_it_or_be_in_it(self):
        loadout = _loadout("New", "Mine")
        loadout.note_collection("Jakob's", ["New", "Mine"], brought=["New"])
        loadout.note_collection("Other", ["Mine"], brought=[])
        loadout.layer("New").from_collection = "Jakob's"
        loadout.forget_collection("Jakob's")
        self.assertEqual(loadout.provenance, {})
        self.assertEqual(loadout.listed, {"Mine": ["Other"]})
        self.assertEqual(loadout.layer("New").from_collection, "")


if __name__ == "__main__":
    unittest.main()
