"""Readings that have gone stale: marked, and read again.

Phase 5 of docs/proposals/ops-as-written-proposal.md.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import SessionCase, modops_file  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    builds,
    generator,
    keptops,
    loadercheck,
    modfile,
    modfolder,
    modinfo,
    reader,
    stale,
    typedops,
)
from anno_modding_studio.core.changes import Change, Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402
from anno_modding_studio.core.workspace import Workspace  # noqa: E402

MAKER = (
    '<ModOp Type="addNextSibling" GUID="1000">'
    "<Asset><Template>Building</Template><Values><Standard>"
    "<GUID>7777</GUID><Name>invented</Name></Standard>"
    "<Building><BuildingType>Farm</BuildingType><Radius>5</Radius></Building></Values></Asset></ModOp>"
)
# Aimed at what MAKER makes: read alone, at an asset that is not there.
USER = '<ModOp Type="merge" GUID="7777" Path="/Values/Building"><Radius>12</Radius></ModOp>'
PATTERN = (
    "<ModOp Type=\"merge\" Path=\"//Values/Building[BuildingType='Farm']\">"
    "<Radius>30</Radius></ModOp>"
)
BETA = '<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>8</Radius></ModOp>'
ALPHA = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>8</Radius></ModOp>'
SET = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
    '<Radius><ModValue Insert="$radius"/></Radius></ModOp>'
)
DOUBLED = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
    '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp>'
)


class StaleCase(SessionCase):
    def loadout(self, root, *names):
        """The named mods of ``root`` read as one stack into a loadout."""
        _session, loadout = loadercheck.read_folders(
            [root / name for name in names], [root], self.index, "anno1800"
        )
        return loadout

    def bring(self, loadout, root, name):
        """Read one more mod in on its own, as importing a single mod does."""
        report = reader.analyse(root / name, self.index, origin=name)
        loadout.set_layer(ModLayer(origin=name, name=name, folder=str(root / name)))
        for op in report.ops:
            loadout.set_op(op)
        for change in report.changes:
            loadout.set_change(change)
        loadout.unread = loadout.unread + list(report.unread(name))
        loadout.layer(name).unread = len(report.unread(name))
        stale.read_alone(loadout, name)

    def again(self, loadout):
        """Read what is marked again and take it, as the window does."""
        taken = stale.snapshot(loadout)
        reports = stale.read_again(taken, self.index)
        self.assertTrue(stale.still_asked(loadout, taken))
        moved = stale.take(loadout, reports)
        stale.give_up(loadout, [origin for origin in taken.asked if origin not in reports])
        return moved

    def ops(self, loadout, origin):
        return [op for op in loadout.ops if op.origin == origin]

    def comes_to(self, loadout, origin):
        return sorted(
            (change.guid, change.new_value)
            for change in loadout.changes.values() if change.origin == origin
        )


class TestWhatIsMarked(StaleCase):
    def test_the_first_look_at_a_loadout_marks_nothing(self):
        root = self.mods(a=ALPHA, b=BETA)
        loadout = self.loadout(root, "a", "b")
        self.assertIsNone(loadout.read_stack)
        self.assertEqual(stale.check(loadout), [])
        self.assertEqual([entry[0] for entry in loadout.read_stack], ["a", "b"])
        self.assertEqual(stale.check(loadout), [])
        self.assertEqual(stale.waiting(loadout), [])

    def test_a_loadout_read_by_another_reader_has_every_mod_read_again(self):
        root = self.mods(a=ALPHA, b=BETA)
        loadout = self.loadout(root, "a", "b")
        # Made here, so read by this Studio's reader.
        self.assertIsNone(loadout.read_by)
        self.assertEqual(stale.check(loadout), [])
        self.assertEqual(loadout.read_by, reader.READER_VERSION)
        # As a file a Studio that read mods differently saved says.
        loadout.read_by = reader.READER_VERSION + 1
        self.assertEqual(stale.check(loadout), ["a", "b"])
        self.assertEqual(loadout.read_by, reader.READER_VERSION)
        self.assertEqual(loadout.layer("a").stale, [[stale.READER, ""]])
        self.assertEqual(stale.why(loadout, loadout.layer("a")),
                         ["It was read by another version of the Studio, which reads mods differently."])
        self.assertEqual(stale.reached(loadout)["b"], {op.uid for op in self.ops(loadout, "b")})
        self.again(loadout)
        self.assertEqual(stale.waiting(loadout), [])
        self.assertEqual(stale.check(loadout), [])

    def test_the_reader_a_loadout_was_read_by_is_kept_in_its_file(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-read-by-"))
        self.addCleanup(shutil.rmtree, folder, True)
        loadout = Loadout()
        stale.check(loadout)
        loadout.save(folder / "a.annoload")
        self.assertEqual(Loadout.load(folder / "a.annoload").read_by, reader.READER_VERSION)
        # Saved before any look, by this version: this version's reader.
        Loadout().save(folder / "b.annoload")
        self.assertIsNone(Loadout.load(folder / "b.annoload").read_by)
        # By another version, or from before the number was kept: another
        # reader's, and read again once.
        for name, written_by in (("c", "0.0.1"), ("d", None)):
            data = Loadout().to_dict()
            data["written_by"] = written_by
            if written_by is None:
                del data["written_by"], data["read_by"]
            (folder / ("%s.annoload" % name)).write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Loadout.load(folder / ("%s.annoload" % name)).read_by, 0, name)

    def test_a_mod_going_marks_every_op_of_the_mods_after_it(self):
        root = self.mods(a=ALPHA, b=BETA, c=PATTERN + ALPHA)
        loadout = self.loadout(root, "a", "b", "c")
        stale.check(loadout)
        loadout.drop_layer("b")
        self.assertEqual(stale.check(loadout), ["c"])
        self.assertEqual(loadout.layer("a").stale, [])
        self.assertEqual(loadout.layer("c").stale, [[stale.GONE, "b"]])
        self.assertEqual(stale.reached(loadout), {"c": {op.uid for op in self.ops(loadout, "c")}})
        self.assertIn("b no longer loads before it.", stale.why(loadout, loadout.layer("c")))

    def test_a_mod_arriving_marks_itself_and_what_it_could_reach_after_it(self):
        root = self.mods(a=MAKER, b=ALPHA, c=PATTERN + ALPHA + BETA)
        loadout = self.loadout(root, "a", "c")
        stale.check(loadout)
        self.bring(loadout, root, "b")
        self.assertEqual(loadout.layer("b").stale, [[stale.ALONE, "b"]])
        self.assertEqual(stale.check(loadout), ["c"])
        self.assertEqual(loadout.layer("c").stale, [[stale.ADDED, "b"]])
        self.assertEqual(stale.waiting(loadout), ["b", "c"])
        pattern, alpha, _beta = self.ops(loadout, "c")
        found = stale.reached(loadout)
        # The pattern, whose reach may have grown, and the op at the asset
        # the new mod changes; not the op at an asset it leaves alone.
        self.assertEqual(found["c"], {pattern.uid, alpha.uid})
        self.assertEqual(found["b"], {op.uid for op in self.ops(loadout, "b")})

    def test_a_mod_arriving_first_is_not_in_doubt_itself(self):
        root = self.mods(a=ALPHA, b=BETA)
        loadout = self.loadout(root, "b")
        stale.check(loadout)
        self.bring(loadout, root, "a")
        self.assertEqual(loadout.layer("a").stale, [])
        self.assertEqual(stale.check(loadout), ["b"])

    def test_a_mod_imported_again_marks_the_mods_after_it(self):
        root = self.mods(a=ALPHA, b=ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        self.bring(loadout, root, "a")
        self.assertEqual(loadout.layer("a").stale, [])
        self.assertEqual(loadout.layer("b").stale, [[stale.CHANGED, "a"]])

    def test_a_patch_of_yours_is_never_in_doubt(self):
        root = self.mods(a=ALPHA, b=ALPHA, c=ALPHA)
        loadout = self.loadout(root, "a", "b", "c")
        loadout.layer("b").patches = "a"
        stale.check(loadout)
        loadout.layer("a").options = {"x": "1"}
        self.assertEqual(stale.check(loadout), ["c"])
        self.assertEqual(loadout.layer("b").stale, [])

    def test_a_version_typed_on_the_export_tab_is_no_change_in_what_loads(self):
        root = self.mods(a=ALPHA, b=ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        loadout.layer("a").version = "2.0"
        loadout.layer("a").name = "Another"
        self.assertEqual(stale.check(loadout), [])

    def test_other_settings_on_a_mod_mark_the_mods_after_it_and_not_it(self):
        root = self.mods(a=SET, b=DOUBLED)
        (root / "a" / "modinfo.json").write_text(
            '{"Options": {"radius": {"type": "slider", "default": "9"}}}', encoding="utf-8"
        )
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        loadout.layer("a").options = {"radius": "12"}
        self.assertEqual(stale.check(loadout), ["b"])
        self.assertEqual(loadout.layer("b").stale, [[stale.CHANGED, "a"]])
        self.assertEqual(loadout.layer("a").stale, [])

    def test_switching_a_mod_off_marks_nothing(self):
        root = self.mods(a=ALPHA, b=ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        loadout.layer("a").active = False
        self.assertEqual(stale.check(loadout), [])

    def test_a_mod_with_no_folder_is_not_part_of_it(self):
        root = self.mods(a=ALPHA, b=ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        loadout.set_layer(ModLayer(origin="[Patch] a", name="patch"))
        self.assertEqual(stale.check(loadout), [])

    def test_it_is_saved_with_the_loadout(self):
        root = self.mods(a=ALPHA, b=BETA, c=ALPHA)
        loadout = self.loadout(root, "a", "b", "c")
        stale.check(loadout)
        loadout.drop_layer("a")
        stale.check(loadout)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        loadout.save(folder / "p.json")
        opened = Loadout.load(folder / "p.json")
        self.assertEqual(opened.read_stack, loadout.read_stack)
        self.assertEqual(opened.layer("c").stale, [[stale.GONE, "a"]])
        self.assertEqual(stale.check(opened), [])
        self.assertEqual(stale.waiting(opened), ["b", "c"])


class TestTheAnswerOfAReading(StaleCase):
    """What a reading hands back is what is taken of it, as plain data, and
    the sweep for the mods' searches says where it is."""

    def asked(self):
        root = self.mods(a=ALPHA, c=PATTERN + ALPHA)
        loadout = self.loadout(root, "a", "c")
        stale.check(loadout)
        # As a session that has not answered the mod's search yet.
        self.index._matched.clear()
        return loadout, stale.snapshot(loadout, asked=["c"])

    def test_it_is_the_ops_the_changes_and_the_unread_ops(self):
        _loadout, taken = self.asked()
        report = reader.analyse_stack(taken, ["c"], self.index)["c"]
        answer = stale.read_again(taken, self.index)["c"]
        self.assertIsInstance(answer, stale.Answer)
        # Two readings give an op two uids: by where each stands.
        self.assertEqual(len(report.ops), 2)
        self.assertEqual(
            [(op.origin, op.order) for op in answer.ops],
            [(op.origin, op.order) for op in report.ops],
        )
        self.assertTrue(report.changes)
        self.assertEqual(
            [(change.guid, change.path, change.new_value) for change in answer.changes],
            [(change.guid, change.path, change.new_value) for change in report.changes],
        )
        self.assertEqual(answer.unread("c"), report.unread("c"))
        self.assertFalse(hasattr(answer, "results"))

    def test_it_can_be_handed_from_one_process_to_another(self):
        import pickle

        _loadout, taken = self.asked()
        answers = stale.read_again(taken, self.index)
        back = pickle.loads(pickle.dumps(answers))
        self.assertEqual(back["c"], answers["c"])
        # And so can the question.
        self.assertEqual(pickle.loads(pickle.dumps(taken)).asked, ["c"])

    def test_a_mod_read_from_the_loadouts_text_answers_the_same_way(self):
        import pickle

        root = self.mods(a=ALPHA, c=PATTERN + ALPHA)
        loadout = self.loadout(root, "a", "c")
        stale.check(loadout)
        stale.edited(loadout, {"a": 0})
        taken = stale.snapshot(loadout)
        self.assertIn("a", taken.texts)
        answers = stale.read_again(taken, self.index)
        self.assertIsInstance(answers["a"], modfile.Reread)
        self.assertIsInstance(answers["a"].report, stale.Answer)
        pickle.dumps(answers)
        stale.take(loadout, answers)
        self.assertEqual(stale.waiting(loadout), [])

    def test_the_sweep_for_its_searches_says_where_it_is(self):
        _loadout, taken = self.asked()
        told = []
        stale.read_again(taken, self.index, searching=lambda *where: told.append(where))
        self.assertTrue(told)
        self.assertTrue(self.index._matched)

    def worker(self, taken):
        from anno_modding_studio.ui.workers import RereadWorker

        worker = RereadWorker(taken, self.index)
        said = []
        worker.done.connect(lambda _taken, answers, error: said.append((answers, error)))
        return worker, said

    def test_a_reading_stopped_for_another_finishes_its_sweep(self):
        _loadout, taken = self.asked()
        worker, said = self.worker(taken)
        self.stopped_in_the_sweep(worker, closing=False)
        worker.run()
        self.assertEqual(said, [(None, "")])
        # What the sweep found is there for the reading that is wanted.
        self.assertTrue(self.index._matched)

    def test_a_closing_window_stops_it_in_the_sweep(self):
        _loadout, taken = self.asked()
        worker, said = self.worker(taken)
        self.stopped_in_the_sweep(worker, closing=True)
        worker.run()
        self.assertEqual(said, [(None, "")])
        self.assertFalse(self.index._matched)

    def stopped_in_the_sweep(self, worker, closing: bool) -> None:
        """Have ``worker`` told to stop when its sweep first says where it
        is: before it, a reading stops at a step like any other."""
        swept = worker._swept

        def stop(*told):
            worker.cancel(closing=closing)
            swept(*told)

        worker._swept = stop

    def test_closing_after_stopping_is_still_closing(self):
        _loadout, taken = self.asked()
        worker, _said = self.worker(taken)
        worker.cancel(closing=True)
        worker.cancel()
        self.assertTrue(worker._closing)


class TestReadingAgain(StaleCase):
    def test_a_mod_read_alone_is_read_where_it_stands(self):
        root = self.mods(a=MAKER, b=USER)
        loadout = self.loadout(root, "a")
        stale.check(loadout)
        self.bring(loadout, root, "b")
        self.assertEqual(loadout.layer("b").unread, 1)
        stale.check(loadout)
        (moved,) = self.again(loadout)
        self.assertEqual(self.comes_to(loadout, "b"), [("7777", "12")])
        self.assertEqual(loadout.layer("b").unread, 0)
        self.assertEqual([entry for entry in loadout.unread if entry.origin == "b"], [])
        self.assertEqual(stale.waiting(loadout), [])
        self.assertEqual(moved.ops, {op.uid for op in self.ops(loadout, "b")})
        # And nothing is marked for having been put right.
        self.assertEqual(stale.check(loadout), [])

    def test_a_mod_after_one_whose_settings_changed_comes_to_the_new_answer(self):
        root = self.mods(a=SET, b=DOUBLED, c=BETA)
        (root / "a" / "modinfo.json").write_text(
            '{"Options": {"radius": {"type": "slider", "default": "9"}}}', encoding="utf-8"
        )
        loadout = self.loadout(root, "a", "b", "c")
        stale.check(loadout)
        self.assertEqual(self.comes_to(loadout, "b"), [("1000", "18")])
        was = [op.uid for op in loadout.ops]
        loadout.layer("a").options = {"radius": "12"}
        stale.check(loadout)
        by_origin = {moved.origin: moved for moved in self.again(loadout)}
        self.assertEqual(self.comes_to(loadout, "b"), [("1000", "24")])
        self.assertEqual(by_origin["b"].ops, {op.uid for op in self.ops(loadout, "b")})
        # A mod read again that comes to what it did has nothing to say.
        self.assertEqual(by_origin["c"].ops, set())
        self.assertEqual(sorted(op.uid for op in loadout.ops), sorted(was))

    def test_a_reading_of_a_stack_that_has_changed_since_is_not_an_answer(self):
        root = self.mods(a=ALPHA, b=BETA, c=ALPHA)
        loadout = self.loadout(root, "a", "b", "c")
        stale.check(loadout)
        loadout.drop_layer("a")
        stale.check(loadout)
        taken = stale.snapshot(loadout)
        self.assertTrue(stale.still_asked(loadout, taken))
        loadout.drop_layer("b")
        stale.check(loadout)
        self.assertFalse(stale.still_asked(loadout, taken))

    def test_a_mod_whose_folder_is_gone_is_left_as_it_is(self):
        root = self.mods(a=ALPHA, b=ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        loadout.layer("a").options = {"x": "1"}
        stale.check(loadout)
        shutil.rmtree(root / "b")
        before = self.comes_to(loadout, "b")
        self.assertEqual(self.again(loadout), [])
        self.assertEqual(self.comes_to(loadout, "b"), before)
        self.assertEqual(stale.waiting(loadout), [])


class TestWhatIsKeptOfWhatWasDoneHere(StaleCase):
    def stale_pair(self):
        root = self.mods(a=ALPHA, b=BETA + ALPHA)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        return root, loadout

    def test_an_op_switched_off_and_a_note_are_still_there(self):
        _root, loadout = self.stale_pair()
        beta, alpha = self.ops(loadout, "b")
        beta.active = False
        alpha.note = "mine"
        change = next(c for c in loadout.changes.values() if c.op_uid == alpha.uid)
        change.active = False
        loadout.drop_layer("a")
        stale.check(loadout)
        self.again(loadout)
        beta, alpha = self.ops(loadout, "b")
        self.assertFalse(beta.active)
        self.assertEqual(alpha.note, "mine")
        self.assertFalse(next(c for c in loadout.changes.values() if c.op_uid == alpha.uid).active)

    def test_a_change_of_yours_to_the_mod_is_still_there(self):
        _root, loadout = self.stale_pair()
        mine = Change(
            guid="1001", asset_label="beta", path="Values/Building/BuildingType",
            tag="BuildingType", old_value="Farm", new_value="Mine", origin="b", authored=True,
        )
        loadout.set_change(mine)
        loadout.drop_layer("a")
        stale.check(loadout)
        self.again(loadout)
        self.assertIn(("1001", "Mine"), self.comes_to(loadout, "b"))
        self.assertEqual(len(self.ops(loadout, "b")), 2)


class _TakenOut(StaleCase):
    """A loaded mod with something of it taken out here, and the two ways
    the mod is read again."""

    def start(self, c, a=BETA):
        root = self.mods(a=a, c=c)
        loadout = self.loadout(root, "a", "c")
        stale.check(loadout)
        return root, loadout

    def folder_read(self, loadout, root, name="c"):
        """One mod read again where it is, as a mods folder read lands it."""
        report = reader.analyse(root / name, self.index, origin=name)
        layer = loadout.layer(name)
        ops, changes, unread = list(report.ops), list(report.changes), list(report.unread(name))
        loadout.as_before(ops, changes, unread)
        loadout.drop_layer(name, keep_authored=True)
        loadout.set_layer(layer)
        for op in ops:
            loadout.set_op(op)
        loadout.unread = [entry for entry in loadout.unread if entry.origin != name] + unread
        loadout.count_unread({name})
        loadout.take_read(changes)

    def stale_read(self, loadout, _root=None):
        """What loads before it changes, and its reading is taken again."""
        loadout.drop_layer("a")
        self.assertEqual(stale.check(loadout), ["c"])
        self.again(loadout)

    def written(self, loadout, name="c"):
        return generator.build_modops_xml(
            generator.for_mod(loadout, name)
        )

    def each_reading(self, c, act):
        """``(loadout, root)`` after ``act`` and each kind of reading."""
        for read in (self.folder_read, self.stale_read):
            with self.subTest(read=read.__name__):
                root, loadout = self.start(c)
                act(loadout)
                read(loadout, root)
                yield loadout, root


class TestAnOpTypedOverOneOfTheMods(_TakenOut):
    """The mod's file still has the op that was typed over, so the mod read
    again brought it back: it stood beside the one typed here and was written
    out after it, where it set its own value again."""

    WIDER = PATTERN.replace("30", "45")

    def pattern_over(self, loadout, text=None):
        (op,) = self.ops(loadout, "c")
        view = Workspace(self.index, loadout)
        found = typedops.read_one(view, "c", text or self.WIDER)
        return typedops.store_one(view, "c", found, op)

    def assert_only_the_typed_one(self, loadout, radius="45"):
        (op,) = self.ops(loadout, "c")
        self.assertTrue(op.authored)
        self.assertIn("<Radius>%s</Radius>" % radius, op.source)
        self.assertEqual(self.written(loadout).count("<ModOp "), 1)
        self.assertEqual(
            {value for _guid, value in self.comes_to(loadout, "c")}, {radius}
        )

    def test_the_mods_own_is_remembered_as_taken_out(self):
        _root, loadout = self.start(PATTERN, a=ALPHA)
        self.pattern_over(loadout)
        self.assertEqual([entry[:3] for entry in loadout.taken_out], [["c", PATTERN, []]])

    def test_read_again_the_mod_does_not_bring_its_own_back(self):
        for loadout, root in self.each_reading(PATTERN, self.pattern_over):
            self.assert_only_the_typed_one(loadout)
            # And again: it goes on being out.
            self.folder_read(loadout, root)
            self.assert_only_the_typed_one(loadout)

    def test_typed_over_twice_the_mods_own_is_taken_out_once(self):
        root, loadout = self.start(PATTERN, a=ALPHA)
        self.pattern_over(loadout)
        self.pattern_over(loadout, PATTERN.replace("30", "60"))
        self.assertEqual(len(loadout.taken_out), 1)
        self.folder_read(loadout, root)
        self.assert_only_the_typed_one(loadout, "60")

    def test_it_is_remembered_in_a_saved_loadout(self):
        root, loadout = self.start(PATTERN, a=ALPHA)
        self.pattern_over(loadout)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        loadout.save(folder / "kept.json")
        opened = Loadout.load(folder / "kept.json")
        self.assertEqual(opened.taken_out, loadout.taken_out)
        self.folder_read(opened, root)
        self.assert_only_the_typed_one(opened)

    def test_an_op_the_mod_writes_twice_and_typed_over_once_comes_back_once(self):
        root, loadout = self.start(PATTERN + PATTERN, a=ALPHA)
        first = self.ops(loadout, "c")[0]
        view = Workspace(self.index, loadout)
        typedops.store_one(view, "c", typedops.read_one(view, "c", self.WIDER), first)
        self.folder_read(loadout, root)
        sources = [op.source for op in self.ops(loadout, "c")]
        self.assertEqual(sorted(sources), sorted([self.WIDER, PATTERN]))

    def test_once_the_mods_file_says_something_else_that_is_a_new_op_of_it(self):
        """What was taken out is no longer in the file, so nothing answers
        for it: the mod's new op lands like any other."""
        root, loadout = self.start(PATTERN, a=ALPHA)
        self.pattern_over(loadout)
        patch = next((root / "c").rglob("assets.xml"))
        patch.write_text(
            patch.read_text(encoding="utf-8").replace(">30<", ">33<"), encoding="utf-8"
        )
        self.folder_read(loadout, root)
        self.assertEqual(len(self.ops(loadout, "c")), 2)

    def test_an_op_typed_over_in_the_xml_view_is_not_brought_back_either(self):
        def act(loadout):
            view = Workspace(self.index, loadout)
            typed = ALPHA.replace(">8<", ">9<")
            _found, done = typedops.read_and_apply(view, "1000", "c", typed, ALPHA)
            self.assertIsNotNone(done)

        for loadout, _root in self.each_reading(ALPHA, act):
            (op,) = self.ops(loadout, "c")
            self.assertIn(">9<", op.source)
            self.assertEqual(self.written(loadout).count("<ModOp "), 1)


class TestAnOpOfTheModsTakenOut(_TakenOut):
    """An op of a loaded mod reverted here is still in the mod's file, and
    the mod read again put it back: by a mods folder read, and by a stale
    reading taken again, which nobody asks for."""

    PLAIN = (
        '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
        "<Radius>8</Radius></ModOp>"
    )
    # Nothing in asset 1000 answers to it, so the Studio carries it unread.
    UNREAD = '<ModOp Type="merge" GUID="1000" Path="/Values/Nowhere/Deep"><X>1</X></ModOp>'

    def assert_nothing_of_it(self, loadout):
        self.assertEqual(self.ops(loadout, "c"), [])
        self.assertEqual(self.comes_to(loadout, "c"), [])
        self.assertEqual([entry for entry in loadout.unread if entry.origin == "c"], [])
        self.assertEqual(self.written(loadout).count("<ModOp "), 0)

    def test_typed_out_of_the_xml_view(self):
        def act(loadout):
            view = Workspace(self.index, loadout)
            _found, done = typedops.read_and_apply(view, "1000", "c", "", ALPHA)
            self.assertIsNotNone(done)

        for loadout, _root in self.each_reading(ALPHA, act):
            self.assert_nothing_of_it(loadout)

    def test_reverted_in_the_changes_list(self):
        def act(loadout):
            (op,) = self.ops(loadout, "c")
            loadout.drop_op(op.uid)

        for loadout, _root in self.each_reading(ALPHA, act):
            self.assert_nothing_of_it(loadout)

    def test_reverted_with_its_asset(self):
        for loadout, _root in self.each_reading(
            ALPHA, lambda loadout: loadout.remove_asset("1000", "c")
        ):
            self.assert_nothing_of_it(loadout)

    def test_one_the_studio_could_not_read(self):
        def act(loadout):
            self.assertEqual(loadout.layer("c").unread, 1)
            loadout.remove_asset("1000", "c")

        for loadout, _root in self.each_reading(self.UNREAD, act):
            self.assert_nothing_of_it(loadout)
            self.assertEqual(loadout.layer("c").unread, 0)

    def test_written_as_narrow_modops_it_is_those_and_not_itself_as_well(self):
        whole = PATTERN.replace("[BuildingType='Farm']", "")

        def act(loadout):
            (op,) = self.ops(loadout, "c")
            self.assertEqual(keptops.write_narrow(loadout, [op.uid]), (1, 2))

        for loadout, _root in self.each_reading(whole, act):
            self.assertEqual(self.ops(loadout, "c"), [])
            self.assertEqual(self.written(loadout).count("<ModOp "), 2)

    def test_retuned_where_it_stands_it_is_the_edit_and_not_itself_as_well(self):
        def act(loadout):
            (op,) = self.ops(loadout, "c")
            self.assertTrue(op.plain)
            (prior,) = loadout.changes_by_op(op.uid)
            loadout.set_change(loadout.place(Change(
                guid=prior.guid, asset_label=prior.asset_label, path=prior.path,
                tag=prior.tag, old_value=prior.old_value, new_value="11",
                origin="c", authored=True,
            )))
            self.assertEqual(self.ops(loadout, "c"), [])

        for loadout, _root in self.each_reading(self.PLAIN, act):
            self.assertEqual(self.ops(loadout, "c"), [])
            self.assertEqual(self.comes_to(loadout, "c"), [("1000", "11")])
            self.assertEqual(self.written(loadout).count("<ModOp "), 1)

    def test_a_whole_group_taken_out_with_its_ops_stays_out(self):
        """A conditioned group is kept as a group, its ops inside it. Taken
        out together, the mod read again dropped only the group and brought
        its ops back, outside any group and written out with no condition."""
        grouped = '<Group Condition="#1001">%s%s</Group>' % (
            ALPHA, ALPHA.replace("<Radius>8", "<Radius>9"),
        )

        def act(loadout):
            ops = self.ops(loadout, "c")
            self.assertEqual(len(ops), 3)
            self.assertEqual(sum(1 for op in ops if op.within), 2)
            loadout.drop_ops([op.uid for op in ops])

        for loadout, _root in self.each_reading(grouped, act):
            self.assert_nothing_of_it(loadout)

    def test_an_op_of_your_own_typing_is_not_remembered(self):
        """It is in no file to come back from."""
        _root, loadout = self.start(PATTERN, a=ALPHA)
        (op,) = self.ops(loadout, "c")
        view = Workspace(self.index, loadout)
        made = typedops.store_one(
            view, "c", typedops.read_one(view, "c", PATTERN.replace("30", "45")), op
        )
        loadout.drop_op(made.uid)
        self.assertEqual(len(loadout.taken_out), 1)

    def test_undone_it_is_in_the_mod_again_and_stays(self):
        from anno_modding_studio.core.undo import UndoHistory

        root, loadout = self.start(ALPHA)
        steps = UndoHistory(loadout)
        (op,) = self.ops(loadout, "c")
        loadout.drop_op(op.uid)
        steps.commit("revert")
        self.assertEqual(len(loadout.taken_out), 1)
        steps.undo()
        self.assertEqual(loadout.taken_out, [])
        self.folder_read(loadout, root)
        self.assertEqual(len(self.ops(loadout, "c")), 1)
        steps.redo()
        self.assertEqual(len(loadout.taken_out), 1)

    def test_the_mod_unloaded_and_loaded_again_starts_from_its_file(self):
        root, loadout = self.start(ALPHA)
        (op,) = self.ops(loadout, "c")
        loadout.drop_op(op.uid)
        loadout.drop_layer("c")
        self.assertEqual(loadout.taken_out, [])
        self.bring(loadout, root, "c")
        self.assertEqual(len(self.ops(loadout, "c")), 1)


# ----------------------------------------------------------- an edit made here
# Phase 1 of docs/proposals/file-tab-proposal.md: the background reading.
RADIUS_8 = "<Radius>8</Radius>"


class EditedCase(StaleCase):
    """Mods read as a stack, then one of them typed in, as the Mod file tab
    does it, with a history that records the step."""

    def start(self, **folders):
        root = self.mods(**folders)
        loadout = self.loadout(root, *folders)
        stale.check(loadout)
        self.history = UndoHistory(loadout)
        return loadout

    def retype(self, loadout, origin, old, new):
        """Type ``new`` over ``old`` in ``origin``'s file and apply it; the
        step of undo that was."""
        shown = modfile.shown(loadout, origin)
        self.assertIn(old, shown.text)
        _found, done = modfile.apply(
            Workspace(self.index, loadout), origin, shown.text.replace(old, new), shown
        )
        self.assertIsNotNone(done)
        return self.said()

    def said(self):
        """Record what just moved, and say so as the window does."""
        step = self.history.commit()
        self.assertIsNotNone(step)
        stale.edited(self.history.loadout, step.reach, step.assets)
        return step

    def radius(self, loadout, origin):
        return [
            change.new_value for change in loadout.ordered()
            if change.origin == origin and change.tag == "Radius"
        ]


class TestAnEditMarksWhatItReaches(EditedCase):
    def test_the_mods_after_the_one_edited_and_not_the_ones_before(self):
        loadout = self.start(a=BETA, b=ALPHA, c=DOUBLED)
        self.retype(loadout, "b", RADIUS_8, "<Radius>12</Radius>")
        # The op typed over is read where the mod stands as well.
        self.assertEqual(stale.waiting(loadout), ["b", "c"])
        self.assertEqual(loadout.layer("b").stale, [["edited", "b", "0"]])
        self.assertEqual(loadout.layer("c").stale, [["edited", "b"]])
        self.assertEqual(loadout.layer("a").stale, [])
        self.assertTrue(loadout.layer("b").edited)
        reached = stale.reached(loadout)
        self.assertEqual(reached["c"], {op.uid for op in self.ops(loadout, "c")})

    def test_the_mod_edited_from_the_op_that_changed_on(self):
        loadout = self.start(a=BETA + ALPHA + DOUBLED)
        beta, _alpha, doubled = self.ops(loadout, "a")
        shown = modfile.shown(loadout, "a")
        at = shown.text.index('GUID="1000" Path="/Values/Building"')
        typed = shown.text[:at] + shown.text[at:].replace(RADIUS_8, "<Radius>12</Radius>", 1)
        modfile.apply(Workspace(self.index, loadout), "a", typed, shown)
        self.said()
        self.assertEqual(loadout.layer("a").stale, [["edited", "a", "1"]])
        reached = stale.reached(loadout)["a"]
        self.assertNotIn(beta.uid, reached)
        self.assertIn(doubled.uid, reached)
        # And only its own later ops are read again, in the one pass.
        self.again(loadout)
        self.assertCountEqual(self.radius(loadout, "a"), ["8", "12", "24"])

    def test_a_change_at_the_end_of_a_mod_marks_only_what_loads_after(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        loadout.set_change(loadout.place(Change(
            guid="1001", asset_label="beta", path="/Values/Building/Radius", tag="Radius",
            old_value="5", new_value="6", origin="a", authored=True,
        )))
        self.said()
        self.assertEqual(stale.waiting(loadout), ["b"])

    def test_the_last_mod_edited_at_its_end_marks_nothing(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        loadout.set_change(loadout.place(Change(
            guid="1001", asset_label="beta", path="/Values/Building/Radius", tag="Radius",
            old_value="5", new_value="6", origin="b", authored=True,
        )))
        self.said()
        self.assertEqual(stale.waiting(loadout), [])

    def test_a_reading_from_before_another_edit_is_not_an_answer(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        taken = stale.snapshot(loadout)
        self.assertTrue(stale.still_asked(loadout, taken))
        self.retype(loadout, "a", "<Radius>12</Radius>", "<Radius>13</Radius>")
        self.assertFalse(stale.still_asked(loadout, taken))

    def test_the_marks_are_saved_with_the_loadout(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        loadout.save(folder / "p.json")
        again = Loadout.load(folder / "p.json")
        self.assertEqual(stale.waiting(again), ["a", "b"])
        self.assertTrue(again.layer("a").edited)


class TestWhichRowsAnEditReaches(EditedCase):
    """The rows that say they are being read again are the ones the edit
    could reach, where the assets it was about are known."""

    def test_in_a_later_mod_the_ops_at_an_asset_the_edit_was_about(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + BETA + PATTERN)
        doubled, _beta, pattern = self.ops(loadout, "b")
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.assertEqual(loadout.edit_reach, {"a": {"1000"}})
        # The op at the asset, and the pattern, whose reach may have moved.
        self.assertEqual(stale.reached(loadout)["b"], {doubled.uid, pattern.uid})

    def test_an_op_that_comes_to_nothing_is_one_of_them(self):
        # It may have been aimed at something that is there now.  The first
        # op of the later mod sets what the game's files have already.
        loadout = self.start(a=BETA, b=ALPHA.replace(RADIUS_8, "<Radius>10</Radius>") + DOUBLED)
        nothing, doubled = self.ops(loadout, "b")
        self.assertEqual(loadout.changes_by_op(nothing.uid), [])
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.assertEqual(loadout.edit_reach, {"a": {"1001"}})
        self.assertEqual(stale.reached(loadout)["b"], {nothing.uid})
        self.assertNotEqual(nothing.uid, doubled.uid)

    def test_a_second_edit_adds_what_it_was_about(self):
        loadout = self.start(a=ALPHA + BETA, b=DOUBLED + BETA)
        shown = modfile.shown(loadout, "a")
        typed = shown.text.replace(RADIUS_8, "<Radius>12</Radius>", 1)
        modfile.apply(Workspace(self.index, loadout), "a", typed, shown)
        self.said()
        self.assertEqual(loadout.edit_reach, {"a": {"1000"}})
        self.assertEqual(len(stale.reached(loadout)["b"]), 1)
        self.retype(loadout, "a", RADIUS_8, "<Radius>9</Radius>")
        self.assertEqual(loadout.edit_reach, {"a": {"1000", "1001"}})
        self.assertEqual(len(stale.reached(loadout)["b"]), 2)

    def test_a_group_moved_reaches_every_op_after_it(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + BETA)
        shown = modfile.shown(loadout, "a")
        at = shown.text.index("<ModOp ")
        end = shown.text.rindex("</ModOps>")
        typed = (
            shown.text[:at] + '<Group Condition="#Nobody">\n' + shown.text[at:end]
            + "</Group>\n" + shown.text[end:]
        )
        modfile.apply(Workspace(self.index, loadout), "a", typed, shown)
        self.said()
        self.assertEqual(loadout.edit_reach, {"a": None})
        self.assertEqual(stale.reached(loadout)["b"], {op.uid for op in self.ops(loadout, "b")})

    def test_two_ops_swapped_in_their_file_are_what_the_edit_was_about(self):
        # Cut and pasted with the gaps kept: only the place of each op in
        # the file moved, and with it the order of what they add.
        first = '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>1</Extra></ModOp>'
        second = '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>2</Extra></ModOp>'
        aimed = (
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Extra[1]">'
            "<Extra>9</Extra></ModOp>"
        )
        loadout = self.start(a=first + "\n" + second, b=aimed)
        shown = modfile.shown(loadout, "a")
        one, two = (
            "    <Extra>%s</Extra>\n  </ModOp>" % n for n in ("1", "2")
        )
        typed = shown.text.replace(one, "@").replace(two, one).replace("@", two)
        self.assertNotEqual(typed, shown.text)
        modfile.apply(Workspace(self.index, loadout), "a", typed, shown)
        self.said()
        self.assertEqual(loadout.edit_reach, {"a": {"1000"}})
        self.assertEqual(stale.reached(loadout)["b"], {op.uid for op in self.ops(loadout, "b")})

    def test_with_nothing_said_of_the_assets_it_is_every_op(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + BETA)
        (alpha,) = self.ops(loadout, "a")
        alpha.active = False
        stale.edited(loadout, self.history.commit().reach)
        self.assertEqual(stale.reached(loadout)["b"], {op.uid for op in self.ops(loadout, "b")})

    def test_what_was_known_is_forgotten_once_the_reading_is_in(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + BETA)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        loadout.edit_reach["a"].add("made up")
        self.again(loadout)
        self.history.absorb()
        self.retype(loadout, "a", "<Radius>12</Radius>", "<Radius>13</Radius>")
        self.assertEqual(loadout.edit_reach, {"a": {"1000"}})


class TestAnEditIsReadAgain(EditedCase):
    def test_a_later_mod_comes_to_what_the_edit_leaves_it(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        self.assertEqual(self.radius(loadout, "b"), ["16"])
        (doubled,) = self.ops(loadout, "b")
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        moved = {found.origin: found for found in self.again(loadout)}
        self.assertEqual(self.radius(loadout, "b"), ["24"])
        self.assertEqual(moved["b"].ops, {doubled.uid})
        self.assertEqual(moved["a"].ops, set())
        self.assertEqual(stale.waiting(loadout), [])

    def test_it_comes_to_what_the_folders_would_with_the_edit_written_into_them(self):
        later = DOUBLED + USER + PATTERN
        loadout = self.start(a=ALPHA + MAKER, b=later)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.retype(loadout, "a", "<Radius>5</Radius>", "<Radius>6</Radius>")
        self.again(loadout)
        root = self.mods(
            a=ALPHA.replace(RADIUS_8, "<Radius>12</Radius>")
            + MAKER.replace("<Radius>5</Radius>", "<Radius>6</Radius>"),
            b=later,
        )
        fresh = self.loadout(root, "a", "b")
        for origin in ("a", "b"):
            self.assertEqual(self.comes_to(loadout, origin), self.comes_to(fresh, origin))
            self.assertEqual(
                [op.idle for op in self.ops(loadout, origin)],
                [op.idle for op in self.ops(fresh, origin)],
            )
        self.assertEqual(len(loadout.unread), len(fresh.unread))

    def test_an_op_that_cannot_be_read_any_more_is_carried_unread_and_back(self):
        loadout = self.start(a=MAKER, b=USER + BETA)
        self.assertEqual(self.comes_to(loadout, "b"), [("1001", "8"), ("7777", "12")])
        self.retype(loadout, "a", "<GUID>7777</GUID>", "<GUID>7778</GUID>")
        self.again(loadout)
        self.history.absorb()
        # Aimed at an asset nothing makes now.
        self.assertEqual(self.comes_to(loadout, "b"), [("1001", "8")])
        self.assertEqual([entry.origin for entry in loadout.unread], ["b"])
        self.assertEqual(loadout.layer("b").unread, 1)
        self.assertEqual(len(self.ops(loadout, "b")), 1)
        text = modfile.shown(loadout, "b").text
        self.assertIn('GUID="7777"', text)
        self.assertLess(text.index('GUID="7777"'), text.index('GUID="1001"'))

        step = self.history.undo()
        stale.edited(loadout, step.reach)
        self.again(loadout)
        self.assertEqual(self.comes_to(loadout, "b"), [("1001", "8"), ("7777", "12")])
        self.assertEqual(loadout.unread, [])
        self.assertEqual(loadout.layer("b").unread, 0)
        self.assertFalse(any(op.authored for op in self.ops(loadout, "b")))
        self.assertEqual(modfile.shown(loadout, "b").text, text)

    def test_a_later_op_of_the_mod_edited_does_as_well_and_is_the_op_it_was(self):
        loadout = self.start(a=ALPHA + DOUBLED)
        alpha, doubled = self.ops(loadout, "a")
        doubled.note = "mine"
        self.history.reset()
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.again(loadout)
        self.assertEqual(self.radius(loadout, "a"), ["12", "24"])
        self.assertEqual([op.uid for op in self.ops(loadout, "a")], [alpha.uid, doubled.uid])
        again = self.ops(loadout, "a")[1]
        self.assertEqual(again.note, "mine")
        self.assertFalse(again.authored)
        self.assertEqual(stale.waiting(loadout), [])

    def test_a_change_made_in_fields_is_read_by_the_mods_after_it(self):
        loadout = self.start(a=BETA, b=DOUBLED)
        loadout.set_change(loadout.place(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
            old_value="5", new_value="9", origin="a", authored=True,
        )))
        self.said()
        self.again(loadout)
        self.assertEqual(self.radius(loadout, "b"), ["18"])
        # And the change is the change it was.
        mine = [c for c in loadout.changes_of("a") if c.authored]
        self.assertEqual([(c.guid, c.new_value, c.op_uid) for c in mine], [("1000", "9", "")])

    def test_an_op_switched_off_is_not_there_for_the_mods_after_it(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        (alpha,) = self.ops(loadout, "a")
        alpha.active = False
        for change in loadout.changes_by_op(alpha.uid):
            change.active = False
        self.said()
        self.again(loadout)
        # Twice the 10 the game's files have, and not twice the 8.
        self.assertEqual(self.radius(loadout, "b"), ["20"])
        (alpha,) = self.ops(loadout, "a")
        self.assertFalse(alpha.active)

    def test_a_mod_with_every_op_taken_out_is_not_there_for_the_mods_after_it(self):
        """Edited as :func:`edits.has_edits` has it, with nothing left in it:
        it was once read from its folder, every op taken out back in it."""
        loadout = self.start(a=ALPHA, b=DOUBLED)
        (alpha,) = self.ops(loadout, "a")
        loadout.drop_op(alpha.uid)
        self.said()
        self.assertIn("a", stale.written_here(loadout))
        self.again(loadout)
        # Twice the 10 the game's files have: read from its folder, the op
        # taken out was still there for the mod after it (A11-26).
        self.assertEqual(self.radius(loadout, "b"), ["20"])
        self.assertEqual(self.ops(loadout, "a"), [])

    def test_the_mod_written_here_is_read_where_it_loads(self):
        loadout = self.start(a=BETA)
        loadout.own.loads_last = True
        view = Workspace(self.index, loadout)
        shown = modfile.shown(loadout, "")
        modfile.apply(view, "", "<ModOps>\n%s\n%s\n</ModOps>" % (ALPHA, DOUBLED), shown)
        self.history.reset()
        self.assertEqual(self.radius(loadout, ""), ["8", "16"])
        first, second = self.ops(loadout, "")
        self.retype(loadout, "", RADIUS_8, "<Radius>12</Radius>")
        self.assertEqual(stale.waiting(loadout), [""])
        self.again(loadout)
        self.assertEqual(self.radius(loadout, ""), ["12", "24"])
        self.assertEqual([op.uid for op in self.ops(loadout, "")], [first.uid, second.uid])
        self.assertTrue(all(op.authored for op in self.ops(loadout, "")))
        self.assertEqual(loadout.own.stale, [])

    def test_read_again_with_nothing_edited_before_it_a_mod_is_as_it_was(self):
        loadout = self.start(a=ALPHA, b=BETA + DOUBLED)
        beta, _doubled = self.ops(loadout, "b")
        beta.active = False
        for change in loadout.changes_by_op(beta.uid):
            change.active = False
        self.said()
        text = modfile.shown(loadout, "b").text
        was = sorted(
            (c.guid, c.path, c.new_value, c.active, c.op_uid) for c in loadout.changes_of("b")
        )
        (moved,) = self.again(loadout)
        self.assertEqual(moved.ops, set())
        self.assertEqual(modfile.shown(loadout, "b").text, text)
        self.assertEqual(was, sorted(
            (c.guid, c.path, c.new_value, c.active, c.op_uid) for c in loadout.changes_of("b")
        ))


class TestAModReadAtOtherSettings(EditedCase):
    """Reading a mod's folder again at other settings reads the mods before
    it as well, and those are what the loadout holds of them."""

    def start(self, **folders):
        root = self.mods(**folders)
        (root / "b" / "modinfo.json").write_text(
            '{"Options": {"radius": {"type": "slider", "default": "9"}}}', encoding="utf-8"
        )
        loadout = self.loadout(root, *folders)
        stale.check(loadout)
        self.history = UndoHistory(loadout)
        return loadout

    def at(self, loadout, value):
        report = stale.read_at(loadout, "b", self.index, {"radius": value})
        return sorted((change.guid, change.new_value) for change in report.changes)

    def test_a_mod_before_it_is_read_with_what_was_edited_in_it_here(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + SET.replace('GUID="1000"', 'GUID="1001"'))
        self.assertEqual(self.at(loadout, "7"), [("1000", "16"), ("1001", "7")])
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.assertEqual(self.at(loadout, "7"), [("1000", "24"), ("1001", "7")])

    def test_the_mod_itself_is_read_from_its_folder_whatever_was_done_to_it(self):
        loadout = self.start(a=ALPHA, b=DOUBLED + BETA)
        self.retype(loadout, "b", RADIUS_8, "<Radius>3</Radius>")
        self.assertIn("b", stale.written_here(loadout))
        self.assertEqual(self.at(loadout, "7"), [("1000", "16"), ("1001", "8")])

    def test_nothing_of_the_loadout_is_moved_by_asking(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        marks = [list(layer.stale) for layer in loadout.layers]
        was = self.comes_to(loadout, "b")
        self.at(loadout, "7")
        self.assertEqual(self.comes_to(loadout, "b"), was)
        self.assertEqual([list(layer.stale) for layer in loadout.layers], marks)


class TestUndoAfterAReadingForAnEdit(EditedCase):
    def test_the_edit_is_taken_back_and_what_it_reached_is_marked_again(self):
        loadout = self.start(a=ALPHA, b=DOUBLED)
        self.retype(loadout, "a", RADIUS_8, "<Radius>12</Radius>")
        self.again(loadout)
        self.history.absorb()
        self.assertTrue(self.history.can_undo)

        step = self.history.undo()
        stale.edited(loadout, step.reach)
        # Nothing of the later mod was put back to what the step held.
        self.assertEqual(self.radius(loadout, "b"), ["24"])
        self.assertEqual(stale.waiting(loadout), ["a", "b"])
        self.again(loadout)
        self.history.absorb()
        self.assertEqual(self.radius(loadout, "a"), ["8"])
        self.assertEqual(self.radius(loadout, "b"), ["16"])

        step = self.history.redo()
        stale.edited(loadout, step.reach)
        self.again(loadout)
        self.assertEqual(self.radius(loadout, "b"), ["24"])

    def test_an_op_typed_and_taken_back_leaves_nothing_of_what_it_came_to(self):
        loadout = self.start(a=ALPHA)
        shown = modfile.shown(loadout, "a")
        typed = shown.text.replace("</ModOps>", DOUBLED + "\n</ModOps>")
        modfile.apply(Workspace(self.index, loadout), "a", typed, shown)
        self.said()
        self.again(loadout)
        self.history.absorb()
        self.assertEqual(self.radius(loadout, "a"), ["8", "16"])
        self.history.undo()
        self.assertEqual(self.radius(loadout, "a"), ["8"])
        self.assertEqual(len(self.ops(loadout, "a")), 1)


class TestAModUpdatedOnDisk(StaleCase):
    """Phase 6a of docs/proposals/projects-and-loadouts-proposal.md: a mod
    whose folder holds another version or build than the one read is read
    again (section 8), and so is every mod after it."""

    def write(self, root, name, body=None, **said):
        """Put a modinfo in ``name``'s folder saying ``said`` -- ``version``,
        ``build`` -- and, with ``body``, its ModOps."""
        declared = {"ModName": {"English": name}}
        if "version" in said:
            declared["Version"] = said["version"]
        if "build" in said:
            declared[modinfo.STUDIO_KEY] = {modinfo.BUILD_KEY: said["build"]}
        (root / name / "modinfo.json").write_text(json.dumps(declared), encoding="utf-8")
        if body is not None:
            next((root / name).rglob("assets.xml")).write_text(modops_file(body), encoding="utf-8")

    def read(self, b=BETA, **said):
        root = self.mods(a=ALPHA, b=b)
        self.write(root, "a", **said)
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        return root, loadout

    def test_a_version_moved_is_read_again_with_the_mods_after_it(self):
        root, loadout = self.read(version="1.0")
        self.write(root, "a", ALPHA.replace(">8<", ">9<"), version="1.1")
        self.assertEqual(stale.follow(loadout), ["a", "b"])
        self.assertEqual(loadout.layer("a").stale, [[stale.UPDATED, "a"]])
        self.assertEqual(loadout.layer("b").stale, [[stale.UPDATED, "a"]])
        self.assertEqual(stale.updated(loadout), ["a"])
        self.assertEqual(stale.why(loadout, loadout.layer("a")),
                         ["Its folder holds another version or build of it than the one read."])
        self.assertEqual(stale.why(loadout, loadout.layer("b")),
                         ["a, which loads before it, has been updated on disk."])
        # What its modinfo says is taken at once, the marks holding the doubt.
        self.assertEqual(loadout.layer("a").version, "1.1")
        self.assertEqual(self.comes_to(loadout, "a"), [("1000", "8")])
        self.again(loadout)
        self.assertEqual(self.comes_to(loadout, "a"), [("1000", "9")])
        self.assertEqual(stale.waiting(loadout), [])
        self.assertEqual(stale.follow(loadout), [])

    def test_a_rebuild_under_the_same_version_is_caught_by_its_build(self):
        root, loadout = self.read(version="1.0", build=3)
        self.write(root, "a", ALPHA.replace(">8<", ">9<"), version="1.0", build=4)
        # The loader check leaves it out until it is read again.
        self.assertTrue(builds.updated_on_disk(loadout, loadout.layer("a"), root / "a"))
        self.assertEqual(stale.follow(loadout), ["a", "b"])
        self.assertEqual(builds.declared_build(loadout.layer("a").declared), 4)
        self.again(loadout)
        self.assertEqual(self.comes_to(loadout, "a"), [("1000", "9")])
        self.assertFalse(builds.updated_on_disk(loadout, loadout.layer("a"), root / "a"))
        self.assertEqual(stale.follow(loadout), [])

    def test_a_build_this_loadout_wrote_is_no_update(self):
        root, loadout = self.read(version="1.0", build=3)
        loadout.builds["a"] = {"build": 4, "digest": "x"}
        self.write(root, "a", version="1.0", build=4)
        self.assertEqual(stale.follow(loadout), [])

    def test_an_unchanged_mod_is_left_as_it_is(self):
        _root, loadout = self.read(version="1.0", build=3)
        was = copy.deepcopy(loadout.layers)
        self.assertEqual(stale.follow(loadout), [])
        self.assertEqual(stale.waiting(loadout), [])
        self.assertEqual(loadout.layers, was)

    def test_a_mod_that_never_names_its_version_cannot_be_told_apart(self):
        root, loadout = self.read()
        self.write(root, "a", ALPHA.replace(">8<", ">9<"))
        self.assertEqual(stale.follow(loadout), [])

    def test_a_mod_whose_folder_is_gone_is_left_as_it_is(self):
        root, loadout = self.read(version="1.0")
        shutil.rmtree(root / "a")
        before = self.comes_to(loadout, "a")
        self.assertEqual(stale.follow(loadout), [])
        self.assertEqual(stale.waiting(loadout), [])
        self.assertEqual(self.comes_to(loadout, "a"), before)

    def test_what_was_done_to_it_here_is_kept(self):
        root = self.mods(a=ALPHA, b=BETA + ALPHA)
        self.write(root, "b", version="1.0")
        loadout = self.loadout(root, "a", "b")
        stale.check(loadout)
        beta, alpha = self.ops(loadout, "b")
        beta.active = False
        alpha.note = "mine"
        loadout.set_change(Change(
            guid="1001", asset_label="beta", path="Values/Building/BuildingType",
            tag="BuildingType", old_value="Farm", new_value="Mine", origin="b", authored=True,
        ))
        # Edited here, so read from what the loadout writes for it -- until
        # its folder moves, which is what is read then.
        self.assertIn("b", stale.written_here(loadout))
        self.write(root, "b", BETA + ALPHA + MAKER, version="1.1")
        self.assertEqual(stale.follow(loadout), ["b"])
        self.assertNotIn("b", stale.snapshot(loadout).texts)
        self.again(loadout)
        beta, alpha, maker = self.ops(loadout, "b")
        self.assertFalse(beta.active)
        self.assertEqual(alpha.note, "mine")
        self.assertIn("7777", maker.source)
        self.assertIn(("1001", "Mine"), self.comes_to(loadout, "b"))
        self.assertEqual(stale.waiting(loadout), [])

    def test_a_mod_of_yours_keeps_its_modinfo_and_the_version_typed_here(self):
        root, loadout = self.read(version="1.0", build=3)
        loadout.home = "a"
        layer = loadout.layer("a")
        modinfo.set_setup(loadout, "a", "hard", {})
        layer.version = "2.0"
        self.write(root, "a", version="1.0", build=4)
        self.assertEqual(stale.follow(loadout), ["a", "b"])
        self.assertEqual(layer.version, "2.0")
        self.assertEqual(layer.declared["Difficulty"], "hard")
        self.assertEqual(builds.declared_build(layer.declared), 4)
        # And with the version not typed over, the folder's.
        layer.version = "1.0"
        self.write(root, "a", version="1.2", build=5)
        stale.follow(loadout)
        self.assertEqual(layer.version, "1.2")
        self.assertEqual(modfolder.declared_version(layer.declared), "1.2")

    def test_a_patch_is_never_in_doubt(self):
        root, loadout = self.read(version="1.0")
        loadout.layer("a").patches = "b"
        self.write(root, "a", version="1.1")
        self.assertEqual(stale.follow(loadout), [])
        self.assertEqual(modfolder.declared_version(loadout.layer("a").declared), "1.0")

if __name__ == "__main__":
    unittest.main()
