"""A fork: somebody else's mod made into one of yours.

``docs/proposals/projects-and-loadouts-proposal.md``, section 5.4: a project
of yours with you as its author and a ModID of its own, swapped in for the
original at its place in load order with its settings, deprecating the
original, and offering to fold your patch for the original into it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_edits import NAME, PATCH, THEIRS, _Edited  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    build,
    dependencies,
    forking,
    generator,
    inside,
    modinfo,
    patching,
    projects,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    own_record,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class TestWhatAForkSays(unittest.TestCase):
    def test_it_deprecates_the_original_in_each_games_place(self):
        self.assertEqual(forking.deprecating({"ModID": "mine"}, "theirs", ANNO_1800)["DeprecateIds"],
                         ["theirs"])
        made = forking.deprecating({"Dependencies": {"Require": ["x"]}}, "theirs", ANNO_117)
        self.assertEqual(made["Dependencies"], {"Require": ["x"], "Deprecate": ["theirs"]})

    def test_what_it_deprecated_already_stays_and_is_not_said_twice(self):
        made = forking.deprecating({"DeprecateIds": ["old", "Theirs"]}, "theirs", ANNO_1800)
        self.assertEqual(made["DeprecateIds"], ["old", "Theirs"])

    def test_the_original_is_left_as_it_was(self):
        declared = {"DeprecateIds": ["old"]}
        forking.deprecating(declared, "theirs", ANNO_1800)
        self.assertEqual(declared, {"DeprecateIds": ["old"]})

    def test_its_mod_id_is_its_own(self):
        loadout = Loadout(game=ANNO_117)
        layer = ModLayer(origin="Their Mod", mod_id="their-mod")
        loadout.set_layer(layer)
        loadout.set_layer(ModLayer(origin="Other", mod_id="their-mod-my-copy"))
        self.assertEqual(forking.new_mod_id(loadout, layer), "their-mod-my-copy-2")


class TestYourOtherNames(unittest.TestCase):
    """A mod published under another name of yours is yours, as one with the
    loadout's author name is: edited directly, never forked or patched."""

    def loadout(self):
        loadout = Loadout(own=own_record(creator="Ay"), aliases=["Old Ay"])
        loadout.set_layer(ModLayer(origin="Old Mod", name="Old", creator="old ay "))
        loadout.set_layer(ModLayer(origin="Their Mod", name="Theirs", creator="Dee"))
        return loadout

    def test_a_mod_by_any_of_them_looks_like_yours(self):
        loadout = self.loadout()
        self.assertTrue(patching.by_you(loadout, "AY"))
        self.assertTrue(patching.looks_mine(loadout, loadout.layer("Old Mod")))
        self.assertFalse(patching.looks_mine(loadout, loadout.layer("Their Mod")))
        self.assertFalse(patching.by_you(loadout, ""))

    def test_and_is_neither_forked_nor_patched(self):
        loadout = self.loadout()
        self.assertFalse(forking.can_fork(loadout, loadout.layer("Old Mod")))
        self.assertTrue(patching.without_a_project(loadout, loadout.layer("Old Mod")))

    def test_they_are_never_saved_with_the_loadout(self):
        self.assertNotIn("aliases", self.loadout().to_dict())


class TestWhatCanBeForked(unittest.TestCase):
    def loadout(self):
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Their Mod", name="Theirs", creator="Dee"))
        loadout.set_layer(ModLayer(origin="My Mod", name="Mine", creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host", name="Host", creator="Dee"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", name="Shared", creator="Dee"))
        return loadout

    def test_somebody_elses_mod_on_its_own(self):
        self.assertTrue(forking.can_fork(self.loadout(), self.loadout().layer("Their Mod")))

    def test_not_yours_nor_a_patch(self):
        loadout = self.loadout()
        patch = patching.make(loadout, "Their Mod")
        self.assertFalse(forking.can_fork(loadout, loadout.layer("My Mod")))
        self.assertFalse(forking.can_fork(loadout, patch))

    def test_not_bundled_nor_a_host_of_bundled_mods(self):
        loadout = self.loadout()
        self.assertFalse(forking.can_fork(loadout, loadout.layer("Host/shared piece")))
        self.assertFalse(forking.can_fork(loadout, loadout.layer("Host")))


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class TestMakingOne(_Edited):
    def setUp(self):
        super().setUp()
        self.loadout.own.creator = "Me"
        self.loadout.layer(NAME).options = {"hard": "1"}
        self.loadout.layer(NAME).declared = {"ModID": "crowded", "Version": "1.2"}
        self.loadout.set_layer(ModLayer(origin="Before", name="Before", order=0))
        self.loadout.set_layer(ModLayer(origin="After", name="After", order=9))
        self.at = [layer.origin for layer in self.loadout.layers].index(NAME)

    def fork(self, fold=True):
        return forking.make(self.loadout, NAME, self.library, fold=fold)

    def test_it_takes_the_originals_place_and_the_original_goes(self):
        fork = self.fork()
        origins = [layer.origin for layer in self.loadout.layers]
        self.assertEqual(origins.index(fork.origin), self.at)
        self.assertNotIn(NAME, origins)
        self.assertEqual(fork.origin, NAME + forking.SUFFIX)

    def test_it_is_yours_with_a_mod_id_of_its_own_and_the_originals_settings(self):
        fork = self.fork()
        self.assertEqual((fork.creator, fork.fork_of, fork.options), ("Me", "crowded", {"hard": "1"}))
        self.assertNotIn(fork.mod_id, ("", "crowded"))
        self.assertTrue(patching.is_mine(self.loadout, fork))
        self.assertIn(fork.origin, self.loadout.project_origins())

    def test_its_modinfo_deprecates_the_original(self):
        fork = self.fork()
        written = json.loads(modinfo.build(generator.for_mod(self.loadout, fork.origin)))
        self.assertEqual(written["ModID"], fork.mod_id)
        self.assertEqual(written["DeprecateIds"], ["crowded"])
        self.assertEqual(written["CreatorName"], "Me")
        found = dependencies.links(written, ANNO_1800)
        self.assertEqual(found.deprecate, ["crowded"])

    def test_its_files_are_a_copy_and_the_librarys_stays_as_shipped(self):
        fork = self.fork()
        folder = Path(fork.folder)
        self.assertTrue(str(folder).startswith(str(forking.root_for(self.library))))
        self.assertEqual((folder / PATCH).read_text(encoding="utf-8"), THEIRS)
        self.assertFalse((folder / "generated.json").exists())
        self.assertEqual((self.copy / PATCH).read_text(encoding="utf-8"), THEIRS)
        self.assertEqual(json.loads((self.copy / "modinfo.json").read_text(encoding="utf-8")),
                         {"ModID": "crowded"})

    def test_its_ops_are_the_originals_and_your_edits_to_it_are_its_own(self):
        theirs = [op.source for op in self.loadout.ops if op.origin == NAME]
        self.loadout.drop_op(self.op("<Residents>99</Residents>").uid)
        inside.assign(self.loadout)
        fork = self.fork()
        mine = [op for op in self.loadout.ops if op.origin == fork.origin]
        self.assertEqual(len(mine), len(theirs) - 1)
        self.assertTrue(all(op.kept_by is None for op in mine))
        (entry,) = self.loadout.taken_out
        self.assertEqual((entry[0], len(entry)), (fork.origin, 4))
        self.assertFalse(any(change.origin == NAME for change in self.loadout.changes.values()))

    def test_it_is_on_or_off_in_each_profile_as_the_original_was(self):
        from anno_modding_studio.core.changes import Profile

        patch = patching.make(self.loadout, NAME)
        self.loadout.profiles = [Profile(name="Main"), Profile(name="Other", switches={
            NAME: True, patch.origin: True, "Before": False})]
        fork = self.fork()
        switches = self.loadout.profiles[1].switches
        self.assertEqual(switches, {fork.origin: True, "Before": False})

    def test_an_update_of_the_original_holds_nothing_of_the_forks(self):
        from anno_modding_studio.core import held

        self.loadout.drop_op(self.op("<Residents>99</Residents>").uid)
        fork = self.fork()
        self.assertEqual(held.stock(self.loadout, [fork.origin]), {})

    def test_it_is_built_as_a_mod_of_yours(self):
        fork = self.fork()
        self.assertIn(fork.origin, build.mine(self.loadout))

    def test_a_patch_folded_in_is_the_forks_and_leaves_the_loadout(self):
        patch = patching.make(self.loadout, NAME)
        self.loadout.set_change(Change(
            guid="1010030", asset_label="Farm", path="/Values/Residence7/Residents",
            tag="Residents", old_value="99", new_value="120", origin=patch.origin, authored=True,
        ))
        self.loadout.editing = patch.origin
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        inside.assign(self.loadout)
        self.loadout.projects[patch.origin] = projects.Ref("projects/patch.annoproj")
        fork = self.fork()
        self.assertIsNone(self.loadout.layer(patch.origin))
        self.assertNotIn(patch.origin, self.loadout.projects)
        (change,) = [c for c in self.loadout.changes.values() if c.new_value == "120"]
        self.assertEqual((change.origin, change.authored), (fork.origin, True))
        # Its edit inside the original is the fork's own now.
        (entry,) = self.loadout.taken_out
        self.assertEqual((entry[0], len(entry)), (fork.origin, 4))
        self.assertEqual(self.loadout.editing_origin(), fork.origin)

    def test_declined_the_patch_stays_patching_nothing(self):
        patch = patching.make(self.loadout, NAME)
        self.fork(fold=False)
        self.assertIsNotNone(self.loadout.layer(patch.origin))
        self.assertTrue(patching.is_inert(self.loadout, self.loadout.layer(patch.origin)))

    def test_saved_it_is_a_project_that_says_what_it_was_made_of(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        fork = self.fork()
        path = self.root / "Main.annoload"
        self.loadout.save(path)
        kept = json.loads(projects.where(self.loadout.projects[fork.origin].path).read_text(encoding="utf-8"))
        self.assertEqual(kept["details"]["fork_of"], "crowded")
        self.assertEqual(Loadout.load(path).layer(fork.origin).fork_of, "crowded")


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class TestForkingFromTheWindow(_Edited):
    """Asked for on the mod's row or as the third way on, a fork says what it
    means before anything happens."""

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock

        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.mock = mock
        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.files.mark_saved)
        patched = mock.patch.object(self.window.where, "library_dir", return_value=self.library)
        patched.start()
        self.addCleanup(patched.stop)
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.window.loadout.own.creator = "Me"
        self.window.loadout.layers = [self.loadout.layer(NAME)]
        self.window.loadout.ops = list(self.loadout.ops)
        self.window.changes_panel.refresh()

    def entry(self):
        panel = self.window.changes_panel
        menu = panel.menu_for(panel._mod_items[NAME])
        self.addCleanup(menu.deleteLater)
        return next((a for a in menu.actions() if a.text() == "Make your own copy…"), None)

    def test_the_mods_row_offers_it_and_cancelled_nothing_is_made(self):
        with self.mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=None) as asked:
            self.entry().trigger()
        asked.assert_called_once_with(NAME, ANNO_1800, "", [], creator="")
        self.assertIsNotNone(self.window.loadout.layer(NAME))

    def test_made_changes_go_straight_into_it(self):
        with self.mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=("copy", False)):
            self.entry().trigger()
        fork = self.window.loadout.layer(NAME + forking.SUFFIX)
        self.assertIsNotNone(fork)
        self.assertEqual(self.window.loadout.editing_origin(), fork.origin)
        self.assertEqual(self.window.editor.target_box.currentData(), fork.origin)
        self.assertIn("your own copy of", self.window.status_label.text())

    def test_the_question_names_the_patch_to_fold_in(self):
        patch = patching.make(self.window.loadout, NAME)
        with self.mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=("copy", True)) as asked:
            self.window.patches.fork(NAME)
        self.assertEqual(asked.call_args.args[2], patch.label)
        self.assertIsNone(self.window.loadout.layer(patch.origin))

    def test_a_patch_held_is_no_way_on(self):
        from anno_modding_studio.core import projects as project_files

        patch = patching.make(self.window.loadout, NAME)
        self.window.loadout.projects[patch.origin] = project_files.Ref(
            "projects/p.annoproj", held=project_files.MOVED)
        with self.mock.patch.object(self.window.prompts, "ask_way_on", return_value="patch"):
            self.assertFalse(self.window.patches.may_edit_inside([NAME]))
        self.assertNotEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertIn("changed in another loadout", self.window.status_label.text())

    def forked(self):
        """The three ways on answered with *Make your own copy*, and the
        copy made without folding anything in."""
        mock = self.mock
        for patched in (mock.patch.object(self.window.prompts, "ask_way_on", return_value="fork"),
                        mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=("copy", False))):
            patched.start()
            self.addCleanup(patched.stop)

    def test_the_third_way_on(self):
        """The copy made is where the edit goes on: it was answered as though
        the question had been cancelled, and the edit that asked it dropped
        (A11-97)."""
        self.forked()
        self.assertEqual(self.window.patches.way_on(NAME), NAME + forking.SUFFIX)
        self.assertIsNotNone(self.window.loadout.layer(NAME + forking.SUFFIX))

    def test_and_the_edit_inside_goes_on_on_the_copy(self):
        self.forked()
        self.assertEqual(self.window.patches.may_edit_inside([NAME]), {NAME: NAME + forking.SUFFIX})

    def with_changes(self):
        for change in self.loadout.changes.values():
            self.window.loadout.set_change(change)
        self.window.changes_panel.refresh()
        return next(key for key, change in self.window.loadout.changes.items() if change.origin == NAME)

    def test_a_change_switched_off_is_switched_off_on_the_copy(self):
        key = self.with_changes()
        self.forked()
        self.window.changes_panel._switch_inside(NAME, None, key, False)
        moved = self.window.loadout.changes[(NAME + forking.SUFFIX, *key[1:])]
        self.assertFalse(moved.active)

    def test_a_change_taken_out_is_taken_out_of_the_copy(self):
        key = self.with_changes()
        self.forked()
        panel = self.window.changes_panel
        with self.mock.patch.object(panel, "_selected_changes", return_value=[key]):
            panel._remove_selected()
        self.assertNotIn((NAME + forking.SUFFIX, *key[1:]), self.window.loadout.changes)
        self.assertIsNotNone(self.window.loadout.layer(NAME + forking.SUFFIX))

    # -- a mod that was yours all along -----------------------------------
    def by_another_name(self, with_patch=True):
        """The mod published under another name of yours, with a patch of it
        holding a change, as a mod dropped in that did not look like yours
        is set up."""
        self.window.loadout.layer(NAME).creator = "Old Me"
        self.window.loadout.layer(NAME).version = "1.2"
        if not with_patch:
            return None
        patch = patching.make(self.window.loadout, NAME)
        self.window.loadout.set_change(Change(
            guid="1010030", asset_label="Farm", path="/Values/Residence7/Residents",
            tag="Residents", old_value="99", new_value="120", origin=patch.origin, authored=True,
        ))
        self.window.loadout.editing = patch.origin
        return patch

    def answered(self, text, creator="Old Me"):
        """The real question, answered with the button called ``text``: its
        answer, and the texts of its buttons."""
        from anno_modding_studio.ui import prompts

        seen = []

        def clicked(box):
            seen.extend(button.text() for button in box.buttons())
            return next((b for b in box.buttons() if b.text() == text), None)

        real = type(self.window.prompts).ask_fork_a_mod
        with self.mock.patch.object(prompts.QMessageBox, "exec"), \
                self.mock.patch.object(prompts.QMessageBox, "clickedButton", new=clicked):
            answer = real(self.window.prompts, "Crowded", ANNO_1800, "Crowded (my patch)",
                          creator=creator)
        return answer, seen

    def test_the_fold_tick_is_read_though_the_box_is_gone(self):
        """R3-28, hands-on run 3: the tick-box went with the box, and
        reading it afterwards raised, so Make my copy did nothing."""
        import shiboken6

        from anno_modding_studio.ui import prompts

        def clicked(box):
            box.checkBox().setChecked(False)
            button = next(b for b in box.buttons() if b.text() == "&Make my copy")
            shiboken6.delete(box)
            return button

        real = type(self.window.prompts).ask_fork_a_mod
        with self.mock.patch.object(prompts.QMessageBox, "exec"), \
                self.mock.patch.object(prompts.QMessageBox, "clickedButton", new=clicked):
            answer = real(self.window.prompts, "Crowded", ANNO_1800, "Crowded (my patch)")
        self.assertEqual(answer, ("copy", False))

    def test_a_mod_with_an_author_name_can_be_carried_on_with_as_yours(self):
        self.assertEqual(self.answered("&Carry on as mine")[0], ("mine", True))
        self.assertEqual(self.answered("&Make my copy")[0], ("copy", True))

    def test_and_the_question_asks_with_the_name_it_has(self):
        self.by_another_name(with_patch=False)
        with self.mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=None) as asked:
            self.window.patches.fork(NAME)
        self.assertEqual(asked.call_args.kwargs["creator"], "Old Me")

    def test_with_no_author_name_there_is_nothing_to_carry_on_as(self):
        self.assertNotIn("&Carry on as mine", self.answered("&Make my copy", creator="")[1])
        self.assertIn("&Carry on as mine", self.answered("&Make my copy")[1])

    def claim(self, fold=True):
        with self.mock.patch.object(self.window.prompts, "ask_fork_a_mod",
                                    return_value=("mine", fold)):
            return self.window.patches.fork(NAME)

    def test_carried_on_as_yours_it_is_itself_and_the_name_is_yours(self):
        """No copy: its name, ModID and author name stay, and that name is
        one of yours from then on."""
        self.by_another_name()
        self.assertEqual(self.claim(), NAME)
        loadout = self.window.loadout
        layer = loadout.layer(NAME)
        self.assertIsNone(loadout.layer(NAME + forking.SUFFIX))
        self.assertEqual((layer.name, layer.mod_id, layer.creator, layer.fork_of),
                         (NAME, "crowded", "Old Me", ""))
        self.assertTrue(layer.kept)
        self.assertEqual(self.window.preferences.aliases(), ["Old Me"])
        self.assertEqual(loadout.aliases, ["Old Me"])
        self.assertEqual(self.window.preferences.creator_name(), "Me")

    def test_and_the_patch_is_folded_into_it(self):
        patch = self.by_another_name()
        self.claim()
        loadout = self.window.loadout
        self.assertIsNone(loadout.layer(patch.origin))
        (change,) = [c for c in loadout.changes.values() if c.new_value == "120"]
        self.assertEqual((change.origin, change.authored), (NAME, True))
        self.assertEqual(loadout.editing_origin(), NAME)

    def test_or_left_patching_it(self):
        patch = self.by_another_name()
        self.claim(fold=False)
        self.assertIsNotNone(self.window.loadout.layer(patch.origin))

    def test_alone_in_the_loadout_it_is_the_mod_carried_on_with_and_published(self):
        """As a mod with your author name dropped in is: the Export tab
        publishes it."""
        from anno_modding_studio.core.changes import AIM_MAKE

        self.by_another_name()
        self.claim()
        loadout = self.window.loadout
        self.assertEqual((loadout.home, loadout.aim), (NAME, AIM_MAKE))
        with self.mock.patch.object(self.window.output, "current_origin", return_value=NAME):
            self.assertTrue(self.window.output._publishable())
        written = json.loads(modinfo.build(generator.for_mod(loadout, NAME)))
        self.assertEqual(written["CreatorName"], "Old Me")
        self.assertEqual((written["ModID"], written["DeprecateIds"]), ("crowded", []))

    def test_beside_other_mods_it_is_only_made_yours(self):
        self.by_another_name()
        self.window.loadout.set_layer(ModLayer(origin="Other", name="Other", creator="Dee", order=5))
        self.claim()
        loadout = self.window.loadout
        self.assertEqual(loadout.home, "")
        self.assertTrue(patching.is_mine(loadout, loadout.layer(NAME)))
        self.assertIn("is yours, with a project of its own", self.window.status_label.text())

    def test_and_every_mod_by_that_name_is_yours_from_then_on(self):
        self.by_another_name(with_patch=False)
        self.claim()
        other = ModLayer(origin="Older", name="Older", creator="old me")
        self.window.loadout.set_layer(other)
        self.assertTrue(patching.looks_mine(self.window.loadout, other))

    def test_what_the_question_says(self):
        from anno_modding_studio.ui import prompts

        real = type(self.window.prompts).ask_fork_a_mod
        for game in (ANNO_1800, ANNO_117):
            with self.subTest(game=game),                     self.mock.patch.object(prompts.QMessageBox, "exec"),                     self.mock.patch.object(prompts.QMessageBox, "setText") as said:
                self.assertIsNone(real(self.window.prompts, "Crowded", game))
                text = said.call_args.args[0]
                self.assertIn("Its author's updates no longer reach it.", text)
                self.assertIn("without its author's permission", text)
                self.assertIn("The library's copy, as its author shipped it, is kept.", text)
                self.assertEqual("will say it is missing" in text, game == ANNO_1800)


if __name__ == "__main__":
    unittest.main()
