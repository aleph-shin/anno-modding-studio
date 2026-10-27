"""Your edits to somebody else's library mod reach the game through a
generated copy of it, and the library's copy stays as its author shipped it
(docs/proposals/patches-proposal.md, phase 2).

Junctions are a Windows thing, as is the game, so the library's half is too.
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
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    build,
    edits,
    generated,
    installplan,
    library,
    modio_cache,
    places,
    play,
    reader,
    stale,
)
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer, own_record  # noqa: E402

PATCH = "data/config/export/main/asset/assets.xml"
THEIRS = """<ModOps>
  <!-- the author's -->
  <ModOp Type="merge" Path="//Values/Residence7">
    <Residents>99</Residents>
  </ModOp>
  <ModOp Type="replace" GUID="1010030" Path="/Values/Standard/Name">
    <Name>Theirs</Name>
  </ModOp>
</ModOps>
"""
NAME = "[Gameplay] Crowded"


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class _Edited(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._class_tmp = tempfile.TemporaryDirectory()
        base = Path(cls._class_tmp.name)
        cls.assets = base / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = base / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.library = self.root / "library" / "anno1800"
        for patched in (
            mock.patch.dict(os.environ, {
                modio_cache.ENV_ROOT: str(self.root / "modio"),
                library.ENV_LIBRARY: str(self.root / "library"),
            }),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            mock.patch.object(installplan.switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        self.index = index_mod.AssetIndex(self.db)
        self.addCleanup(self.index.close)
        self.copy = self.library / NAME
        (self.copy / Path(PATCH).parent).mkdir(parents=True)
        (self.copy / PATCH).write_text(THEIRS, encoding="utf-8")
        (self.copy / "modinfo.json").write_text(json.dumps({"ModID": "crowded"}), encoding="utf-8")
        linked = library.link(self.copy, self.mods)
        library.note_link(self.library, linked, library.COPY)
        self.link = linked.link
        self.loadout = Loadout(game="anno1800", own=own_record(creator="Me"))
        report = reader.analyse(self.link, self.index, origin=NAME)
        self.loadout.set_layer(ModLayer(origin=NAME, name=NAME, mod_id="crowded", folder=str(self.link)))
        for op in report.ops:
            self.loadout.set_op(op)
        for change in report.changes:
            self.loadout.set_change(change)
        self.loadout.unread = list(report.unread(NAME))

    def op(self, words: str):
        return next(op for op in self.loadout.ops if words in op.source)

    def type_over(self, words: str, new: str):
        op = self.op(words)
        op.source = op.source.replace(words, new)
        op.authored = True
        return op


class TestWorkingThemOut(_Edited):
    def test_nothing_of_yours_is_nothing_to_write(self):
        self.assertFalse(edits.has_edits(self.loadout, NAME))
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        self.assertEqual((done.written, done.hunks, done.trouble), ({}, [], ""))

    def test_an_op_typed_over_is_theirs_switched_off_and_yours_after_it(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.assertTrue(edits.has_edits(self.loadout, NAME))
        self.assertEqual(edits.count(self.loadout, NAME), 1)
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        text = done.written[PATCH].decode("utf-8")
        self.assertIn('<ModOp Type="replace" GUID="1010030" Path="/Values/Standard/Name" Skip="1">', text)
        self.assertIn("<Name>Mine</Name>", text)
        self.assertIn("<!-- the author's -->", text)
        self.assertLess(text.index("<Name>Theirs</Name>"), text.index("<Name>Mine</Name>"))
        self.assertEqual(text.count('Skip="1"'), 1)
        self.assertEqual(len(done.hunks), 1)
        self.assertEqual(done.hunks[0]["line"], 6)

    def test_an_op_taken_out_is_switched_off_and_nothing_put_after_it(self):
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        self.assertTrue(edits.has_edits(self.loadout, NAME))
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        (hunk,) = done.hunks
        self.assertEqual((len(hunk["skipped"]), hunk["inserted"]), (1, []))

    def test_every_op_taken_out_is_every_op_switched_off(self):
        """With nothing of the mod left to run, there is still everything of
        theirs to switch off: a guard for a mod with no ops at all once
        took it for one and wrote nothing."""
        for op in [op for op in self.loadout.ops if op.origin == NAME and not op.scope]:
            self.loadout.drop_op(op.uid)
        for entry in list(self.loadout.unread):
            self.loadout.drop_unread([entry])
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        self.assertEqual(done.trouble, "")
        self.assertEqual(done.written[PATCH].decode("utf-8").count('Skip="1"'), 2)

    def test_what_has_edits_says_of_a_mod_is_what_the_readings_go_by(self):
        """One rule: a reading of the stack reads a mod edited here from what
        the loadout writes for it (:func:`stale.written_here`), and a change
        of theirs switched off on its own, or every op taken out, is an edit
        to both."""
        self.assertEqual(edits.edited(self.loadout), set())
        change = next(change for change in self.loadout.changes.values() if change.origin == NAME)
        change.active = False
        self.assertTrue(edits.has_edits(self.loadout, NAME))
        self.assertEqual(edits.edited(self.loadout), {NAME})
        change.active = True
        for op in [op for op in self.loadout.ops if op.origin == NAME]:
            self.loadout.drop_op(op.uid)
        for entry in list(self.loadout.unread):
            self.loadout.drop_unread([entry])
        self.assertTrue(edits.has_edits(self.loadout, NAME))
        self.assertEqual(edits.edited(self.loadout), {NAME})
        self.assertIn(NAME, stale.written_here(self.loadout))

    def test_an_op_switched_off_is_switched_off_in_their_file(self):
        self.op("<Residents>99</Residents>").active = False
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        self.assertIn('<ModOp Type="merge" Path="//Values/Residence7" Skip="1">',
                      done.written[PATCH].decode("utf-8"))

    def test_an_op_kept_off_is_off_with_all_it_comes_to_when_read_again(self):
        """R3-23, hands-on run 3: read again where it came to more, the op
        stayed off and what it came to now came in on, written again as two
        ops of yours after theirs."""
        from anno_modding_studio.core import held, stale

        op = self.op("<Residents>99</Residents>")
        held._switch(self.loadout, op.uid, False)
        # What it came to before is not what it comes to now: read in another
        # stack, it finds assets it did not.
        for key in [key for key, change in self.loadout.changes.items() if change.op_uid == op.uid]:
            del self.loadout.changes[key]
        report = reader.analyse(self.link, self.index, origin=NAME)
        stale._take_read(self.loadout, {NAME: report})
        again = self.op("<Residents>99</Residents>")
        self.assertFalse(again.active)
        under = [change for change in self.loadout.changes.values() if change.op_uid == again.uid]
        self.assertTrue(under)
        self.assertFalse(any(change.active for change in under))
        (hunk,) = edits.work_out(self.loadout, NAME, self.copy, self.index).hunks
        self.assertEqual((len(hunk["skipped"]), hunk["inserted"]), (1, []))

    def test_with_no_game_files_it_says_so(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        done = edits.work_out(self.loadout, NAME, self.copy, None)
        self.assertEqual(done.written, {})
        self.assertIn("no game files", done.trouble)

    def test_a_mod_that_spells_its_folders_otherwise_keeps_its_spelling(self):
        # The author's own capitals: the game finds the file either way, and
        # a disk that tells case apart has only the author's.
        (self.copy / "data").rename(self.copy / "Data")
        (self.copy / "Data" / "config").rename(self.copy / "Data" / "Config")
        spelled = "Data/Config/export/main/asset/assets.xml"
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        self.assertEqual(list(done.written), [spelled])
        self.assertEqual(done.hunks[0]["file"], spelled)
        made = generated.build(self.copy, self.root / "made", done.written)
        self.assertEqual(made.written, 1)
        self.assertIn("<Name>Mine</Name>", (self.root / "made" / spelled).read_text(encoding="utf-8"))
        names = {path.relative_to(self.root / "made").as_posix() for path in (self.root / "made").rglob("*.xml")}
        self.assertEqual(names, {spelled})


class TestPlay(_Edited):
    def test_their_mod_goes_into_a_generated_copy_and_is_never_built(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.loadout.set_change(next(iter(self.loadout.changes.values())))
        copies = play.generated_copies(self.loadout, self.library, self.index)
        self.assertEqual(copies.handled, {NAME})
        self.assertEqual([layer.origin for layer in copies.edited], [NAME])
        self.assertIn(self.link.name, copies.generating)
        self.assertIn(PATCH, copies.generating[self.link.name]["written"])
        self.assertNotIn(NAME, play.plan(self.loadout, str(self.mods), self.library, self.index).built)

    def test_carried_out_the_link_points_at_the_copy_and_the_library_is_untouched(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        found = play.plan(self.loadout, str(self.mods), self.library, self.index)
        self.assertEqual([layer.origin for layer in found.edited], [NAME])
        play.carry_out(found, self.loadout, self.library)
        made = generated.folder_for(self.library, NAME)
        self.assertEqual(library.target_of(self.link), made)
        self.assertEqual((self.copy / PATCH).read_text(encoding="utf-8"), THEIRS)
        self.assertIn("<Name>Mine</Name>", (self.link / PATCH).read_text(encoding="utf-8"))
        self.assertEqual(generated.check(made), [])
        said = generated.manifest(made)["edits"]
        self.assertEqual(said, [{"kind": "edits", "mod": NAME, "hunks": 1}])
        # Played again with nothing changed, there is nothing to do.
        again = play.plan(self.loadout, str(self.mods), self.library, self.index)
        self.assertEqual((again.generating, again.edited), ({}, []))

    def test_with_the_edits_undone_the_link_goes_back(self):
        op = self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        play.carry_out(play.plan(self.loadout, str(self.mods), self.library, self.index),
                       self.loadout, self.library)
        op.source = op.source.replace("<Name>Mine</Name>", "<Name>Theirs</Name>")
        op.authored = False
        found = play.plan(self.loadout, str(self.mods), self.library, self.index)
        self.assertEqual(found.reverting, [self.link.name])
        play.carry_out(found, self.loadout, self.library)
        self.assertEqual(library.target_of(self.link), self.copy)

    def test_taken_up_since_the_link_goes_back(self):
        """A copy made while the mod was somebody else's holds edits that
        are not the copy's to make once the mod is yours: it was left
        pointed at, because only their mods' links were spoken for."""
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        play.carry_out(play.plan(self.loadout, str(self.mods), self.library, self.index),
                       self.loadout, self.library)
        self.loadout.layer(NAME).kept = True
        found = play.plan(self.loadout, str(self.mods), self.library, self.index)
        self.assertEqual(found.reverting, [self.link.name])
        play.carry_out(found, self.loadout, self.library)
        self.assertEqual(library.target_of(self.link), self.copy)

    def test_your_own_mod_is_built_as_it_always_was(self):
        self.loadout.layer(NAME).creator = "me"
        self.loadout.layer(NAME).kept = True
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        copies = play.generated_copies(self.loadout, self.library, self.index)
        self.assertEqual((copies.handled, copies.generating), (set(), {}))

    def test_without_game_files_their_mod_loads_as_shipped_and_it_is_said(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        copies = play.generated_copies(self.loadout, self.library, None)
        self.assertEqual(copies.handled, {NAME})
        self.assertEqual(copies.generating, {})
        (said,) = copies.edits_held
        self.assertIn("your edits could not be worked out", said)
        self.assertNotIn(NAME, build.mine(self.loadout) and play.plan(
            self.loadout, str(self.mods), self.library, None).built)


class TestTheWindowAsksWhichModOfYours(_Edited):
    """Somebody else's mod is never changed, in the library or not: an edit
    inside its ops goes into your patch for it or this loadout's own mod,
    and the window asks which (section 5.2 of
    docs/proposals/projects-and-loadouts-proposal.md)."""

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.files.mark_saved)
        patched = mock.patch.object(self.window.where, "library_dir", return_value=self.library)
        patched.start()
        self.addCleanup(patched.stop)
        self.window.loadout.layers = [self.loadout.layer(NAME)]

    def test_a_library_mod_is_asked_about(self):
        self.assertFalse(self.window.patches.changed_directly(NAME))
        with mock.patch.object(self.window.prompts, "ask_way_on", return_value="") as asked:
            self.assertFalse(self.window.patches.may_edit_inside([NAME]))
        asked.assert_called_once_with(NAME, "", False)

    def test_and_the_answer_is_kept_for_that_mod(self):
        with mock.patch.object(self.window.prompts, "ask_way_on", return_value="own") as asked:
            self.assertTrue(self.window.patches.may_edit_inside([NAME]))
            self.assertTrue(self.window.patches.may_edit_inside([NAME]))
        asked.assert_called_once()
        self.assertTrue(self.window.loadout.has_own_mod())
        self.assertIsNone(self.window.loadout.patch_for(NAME))

    def test_a_patch_answered_is_made_and_where_changes_go(self):
        with mock.patch.object(self.window.prompts, "ask_way_on", return_value="patch"):
            self.assertTrue(self.window.patches.may_edit_inside([NAME]))
        patch = self.window.loadout.patch_for(NAME)
        self.assertIsNotNone(patch)
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertTrue(self.window.patches.changed_directly(NAME))

    def test_a_folder_of_somebody_elses_is_asked_alike(self):
        own = self.mods / "Theirs In A Folder"
        (own / Path(PATCH).parent).mkdir(parents=True)
        (own / PATCH).write_text(THEIRS, encoding="utf-8")
        self.window.loadout.set_layer(ModLayer(origin=own.name, name="Theirs", folder=str(own)))
        self.assertFalse(self.window.patches.changed_directly(own.name))
        with mock.patch.object(self.window.prompts, "ask_way_on", return_value="") as asked:
            self.window.patches.may_edit_inside([own.name])
        asked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
