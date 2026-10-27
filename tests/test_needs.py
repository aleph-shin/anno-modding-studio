"""A patch of several mods: coupled to all of them, loading after all of
them, and left out while any is off (docs/proposals/patches-proposal.md,
phase 4).  A buff that is only fair while two nerfs are on is a patch of
both."""

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

from anno_modding_studio.core import games, generator, modinfo, patching  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer, own_record  # noqa: E402

PATCH = "Nerf A (my patch)"


def a_loadout(game=games.ANNO_1800) -> Loadout:
    loadout = Loadout(game=game, own=own_record(creator="Me"))
    loadout.set_layer(ModLayer(origin="Nerf A", name="Nerf A", mod_id="nerf-a"))
    loadout.set_layer(ModLayer(origin="Nerf B", name="Nerf B", mod_id="nerf-b"))
    loadout.set_layer(ModLayer(origin="Other", name="Other", mod_id="other"))
    patch = patching.make(loadout, "Nerf A", PATCH)
    patching.need(loadout, patch, "Nerf B")
    return loadout


class TestWhatAPatchNeeds(unittest.TestCase):
    def test_the_mod_it_was_made_for_then_the_rest(self):
        loadout = a_loadout()
        self.assertEqual(loadout.needed(loadout.layer(PATCH)), ["Nerf A", "Nerf B"])
        self.assertEqual(loadout.needed(loadout.layer("Other")), [])
        self.assertEqual([one.origin for one in loadout.patches_needing("Nerf B")], [PATCH])
        self.assertEqual(loadout.patches_needing("Nerf A"), [])

    def test_with_any_of_them_off_it_is_left_out(self):
        loadout = a_loadout()
        patch = loadout.layer(PATCH)
        self.assertFalse(patching.is_inert(loadout, patch))
        loadout.layer("Nerf B").active = False
        self.assertTrue(patching.is_inert(loadout, patch))
        self.assertIn("Nerf B, which it also needs", patching.why_inert(loadout, patch))
        loadout.layer("Nerf B").active = True
        loadout.layer("Nerf A").active = False
        self.assertEqual(patching.why_inert(loadout, patch), "the mod it patches is switched off")

    def test_it_loads_after_all_of_them(self):
        loadout = a_loadout()
        self.assertEqual(loadout.load_after_of(loadout.layer(PATCH)), ["nerf-a", "nerf-b"])
        loadout.layer("Nerf B").load_after = ["*"]
        self.assertEqual(loadout.load_after_of(loadout.layer(PATCH))[0], "*")

    def test_needing_and_no_longer_needing(self):
        loadout = a_loadout()
        patch = loadout.layer(PATCH)
        self.assertEqual([one.origin for one in patching.can_need(loadout, patch)], ["Other"])
        self.assertFalse(patching.need(loadout, patch, "Nerf A"))
        self.assertFalse(patching.need(loadout, patch, PATCH))
        self.assertTrue(patching.need(loadout, patch, "Other"))
        # A new layer in its place, so a step of undo sees it.
        self.assertIsNot(loadout.layer(PATCH), patch)
        self.assertEqual(loadout.layer(PATCH).needs, ["Nerf B", "Other"])
        self.assertTrue(patching.unneed(loadout, loadout.layer(PATCH), "Nerf B"))
        self.assertEqual(loadout.layer(PATCH).needs, ["Other"])
        self.assertFalse(patching.unneed(loadout, loadout.layer(PATCH), "Nerf A"))

    def test_saved_with_the_loadout(self):
        loadout = a_loadout()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.annoload"
            loadout.save(path)
            self.assertEqual(Loadout.load(path).layer(PATCH).needs, ["Nerf B"])


class TestItsModinfo(unittest.TestCase):
    def written(self, game):
        return json.loads(modinfo.build(generator.for_mod(a_loadout(game), PATCH)))

    def test_anno_1800_loads_after_and_depends_on_both(self):
        out = self.written(games.ANNO_1800)
        self.assertEqual(out["LoadAfterIds"], ["nerf-a", "nerf-b"])
        self.assertEqual(out["ModDependencies"], ["nerf-a", "nerf-b"])
        self.assertEqual(out[modinfo.STUDIO_KEY]["patches"], "nerf-a")
        self.assertEqual(out[modinfo.STUDIO_KEY]["needs"], ["nerf-b"])

    def test_anno_117_requires_and_loads_after_both(self):
        out = self.written(games.ANNO_117)
        self.assertEqual(out["Dependencies"]["LoadAfter"], ["nerf-a", "nerf-b"])
        self.assertEqual(out["Dependencies"]["Require"], ["nerf-a", "nerf-b"])

    def test_read_back_out_of_a_folder_it_needs_them_again(self):
        out = self.written(games.ANNO_1800)
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Me"))
        for origin, mod_id in (("Nerf A", "nerf-a"), ("Nerf B", "nerf-b")):
            loadout.set_layer(ModLayer(origin=origin, mod_id=mod_id))
        loadout.set_layer(ModLayer(origin=PATCH, mod_id=out["ModID"], declared=out))
        self.assertEqual(patching.reattach(loadout), 1)
        self.assertEqual((loadout.layer(PATCH).patches, loadout.layer(PATCH).needs),
                         ("Nerf A", ["Nerf B"]))

    def test_a_patch_of_one_mod_needs_that_one(self):
        loadout = a_loadout()
        patching.unneed(loadout, loadout.layer(PATCH), "Nerf B")
        out = json.loads(modinfo.build(generator.for_mod(loadout, PATCH)))
        self.assertEqual(out["ModDependencies"], ["nerf-a"])
        self.assertNotIn("needs", out[modinfo.STUDIO_KEY])


class TestTheList(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.files.mark_saved)
        loadout = a_loadout()
        self.window.loadout.layers = loadout.layers
        self.window.changes_panel.refresh()

    def rows(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        found = {}

        def walk(item):
            if item.data(0, KIND_ROLE) == "mod":
                found[item.data(0, ORIGIN_ROLE)] = item
            for at in range(item.childCount()):
                walk(item.child(at))

        for at in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(at))
        return found

    def test_the_patch_names_them_all_and_each_other_one_names_the_patch(self):
        rows = self.rows()
        self.assertIn("your patch for Nerf A and Nerf B", rows[PATCH].text(0))
        self.assertIn("(also patched by", rows["Nerf B"].text(0))
        self.assertNotIn("also patched by", rows["Nerf A"].text(0))
        self.assertIn("needs every one", rows[PATCH].toolTip(0))

    def test_and_the_generate_tab_names_them_all_too(self):
        """Named apart there, the patch was "your patch for Nerf A and Nerf B"
        in the list and "your patch for Nerf A" in the Generate tab, which
        said it went wherever Nerf A is used, though without Nerf B it is
        left out."""
        editor = self.window.editor
        self.assertEqual(editor._target_text(PATCH),
                         "Your patch for Nerf A and Nerf B: wherever you use it")
        self.window.loadout.editing = PATCH
        editor.refresh_target()
        self.assertEqual(editor.target_box.currentText(),
                         "Your patch for Nerf A and Nerf B: wherever you use it")
        # The line beside the picker does not say it again.
        self.assertNotIn("Nerf A", editor.target_label.text())

    def test_its_menu_adds_one_and_takes_one_away(self):
        panel = self.window.changes_panel
        texts = [action.text() for action in panel.menu_for(self.rows()[PATCH]).actions()]
        self.assertIn("Needs another mod too…", texts)
        self.assertIn("No longer needs Nerf B", texts)
        with mock.patch.object(self.window.prompts, "pick_a_mod_to_need", return_value=0) as asked:
            self.assertTrue(self.window.patches.add_need(PATCH))
        self.assertEqual(asked.call_args.args[1], ["Other"])
        self.assertEqual(self.window.loadout.layer(PATCH).needs, ["Nerf B", "Other"])
        self.assertTrue(self.window.patches.remove_need(PATCH, "Nerf B"))
        self.assertEqual(self.window.loadout.layer(PATCH).needs, ["Other"])
        self.window.undo_action.trigger()
        self.assertEqual(self.window.loadout.layer(PATCH).needs, ["Nerf B", "Other"])


if __name__ == "__main__":
    unittest.main()
