"""Anno 117's Difficulty and GameSetup, set for a mod of your own -- or for a
loaded one of yours -- and written into its modinfo."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow  # noqa: E402

from anno_modding_studio.core import generator, modinfo  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer, own_record  # noqa: E402
from anno_modding_studio.core.games import ANNO_117  # noqa: E402


def _written(loadout, origin=""):
    return json.loads(generator.build_modinfo(generator.for_mod(loadout, origin)))


class TestYourMod(unittest.TestCase):
    def test_a_new_mod_says_what_it_always_said(self):
        written = _written(Loadout(game=ANNO_117, own=own_record(name="Mine")))
        self.assertEqual(written["Difficulty"], "unchanged")
        self.assertEqual(written["GameSetup"], {"RequiresNewGame": False, "SafeToRemove": True,
                                                "Multiplayer": True, "Campaign": True})

    def test_what_its_author_sets_goes_out(self):
        loadout = Loadout(game=ANNO_117, own=own_record(name="Mine"))
        modinfo.set_setup(loadout, "", "cheat", {"SafeToRemove": False, "Multiplayer": False})
        written = _written(loadout)
        self.assertEqual(written["Difficulty"], "cheat")
        self.assertEqual(written["GameSetup"], {"RequiresNewGame": False, "SafeToRemove": False,
                                                "Multiplayer": False, "Campaign": True})

    def test_what_is_typed_of_a_mod_carried_on_with_is_work_too(self):
        """Its description, creator, version, category and ModID are on the
        mod and not on the loadout.  Typed on the Export tab they were no
        detail, so closing asked nothing of them and they were gone."""
        loadout = Loadout(game=ANNO_117)
        loadout.set_layer(ModLayer(origin="Mine", name="Mine", description="as read"))
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", description="as read"))
        loadout.home = "Mine"
        for field in ("description", "creator", "version", "category", "mod_id"):
            with self.subTest(field=field):
                before = loadout.typed_details()
                setattr(loadout.layer("Mine"), field, "typed")
                self.assertTrue(loadout.details_moved(before))
                self.assertTrue(loadout.mod_details_moved(before))
        # Somebody else's mod is as it was read, and reading it again is no work.
        before = loadout.typed_details()
        loadout.layer("Theirs").description = "read again"
        self.assertFalse(loadout.details_moved(before))

    def test_saved_and_counted_as_work(self):
        loadout = Loadout(game=ANNO_117)
        before = loadout.typed_details()
        modinfo.set_setup(loadout, "", "harder", {"RequiresNewGame": True})
        self.assertTrue(loadout.details_moved(before))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            loadout.save(path)
            back = Loadout.load(path)
        self.assertEqual(modinfo.setup_of(back, ""), modinfo.setup_of(loadout, ""))
        self.assertEqual(back.difficulty, "harder")


class TestALoadedModOfYours(unittest.TestCase):
    def layer(self, declared):
        loadout = Loadout(game=ANNO_117)
        loadout.set_layer(ModLayer(origin="A", name="A", declared=declared))
        return loadout

    def test_what_it_leaves_out_is_the_loader_s_default(self):
        loadout = self.layer({"ModID": "a", "Difficulty": "easier", "GameSetup": {"Campaign": False}})
        self.assertEqual(modinfo.setup_of(loadout, "A"), ("easier", {
            "RequiresNewGame": False, "SafeToRemove": False, "Multiplayer": True, "Campaign": False,
        }))

    def test_set_it_keeps_the_rest_and_goes_out(self):
        loadout = self.layer({"ModID": "a", "Anno": 8, "Difficulty": "easier",
                              "GameSetup": {"Campaign": False, "Future": 1}})
        modinfo.set_setup(loadout, "A", "unchanged", {"RequiresNewGame": True, "Campaign": False})
        written = _written(loadout, "A")
        self.assertEqual(written["Difficulty"], "unchanged")
        self.assertEqual(written["GameSetup"], {"Campaign": False, "Future": 1, "RequiresNewGame": True})




class TestTheExportTab(PlayingWindow):
    def test_shown_for_117_and_set_from_the_boxes(self):
        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.make_own_mod()
        output = self.window.output
        output.refresh()
        form = output._scripts_form
        self.assertTrue(form.isRowVisible(output._difficulty_row))
        self.assertEqual(output.difficulty_combo.currentData(), "unchanged")
        self.assertTrue(output.setup_boxes["SafeToRemove"].isChecked())
        output.setup_boxes["SafeToRemove"].setChecked(False)
        output.difficulty_combo.setCurrentIndex(output.difficulty_combo.findData("cheat"))
        self.assertEqual(modinfo.setup_of(loadout, ""), ("cheat", {
            "RequiresNewGame": False, "SafeToRemove": False, "Multiplayer": True, "Campaign": True,
        }))
        loadout.game = "anno1800"
        output._show_details_for("")
        self.assertFalse(form.isRowVisible(output._setup_row))

    def test_somebody_else_s_is_shown_as_written_and_not_edited(self):
        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.layers = [ModLayer(origin="Theirs", name="Theirs", creator="someone else",
                                   declared={"Difficulty": "Easy", "GameSetup": {"Multiplayer": False}})]
        output = self.window.output
        output._show_details_for("Theirs")
        self.assertEqual(output.difficulty_combo.currentText(), "Easy (not one the game knows)")
        self.assertFalse(output.setup_boxes["Multiplayer"].isChecked())
        self.assertFalse(output.difficulty_combo.isEnabledTo(output.meta_box))
        # And back on yours, only the four the loader knows are offered.
        loadout.make_own_mod()
        output._show_details_for("")
        self.assertEqual(output.difficulty_combo.count(), 4)
        self.assertEqual(loadout.layers[0].declared["Difficulty"], "Easy")


if __name__ == "__main__":
    unittest.main()
