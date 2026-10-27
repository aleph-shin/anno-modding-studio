"""The ModOps the Studio writes for each mod of yours -- see ``core/narrow.py``."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import ImportCase  # noqa: E402

from anno_modding_studio.core import games, generator, keptops, narrow  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_REMOVE,
    SWITCHED_OFF_MARK,
    Change,
    Loadout,
    Op,
)
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402


def value(tag, new, guid="100", old="1", label="Farmer Residence", **more):
    return Change(
        guid=guid, asset_label=label, path="/Values/Residence7/%s" % tag, tag=tag,
        old_value=old, new_value=new, **more,
    )


def beside(guid, made, spot=0, note=""):
    """A new asset made beside ``guid``, as the New asset dialog makes one."""
    return Change(
        guid=guid, asset_label="Template", path="", tag="", op=OP_ADD_NEXT, spot=spot, note=note,
        payload="<Asset><Values><Standard><GUID>%s</GUID></Standard></Values></Asset>" % made,
    )


def edited(*changes) -> Loadout:
    loadout = Loadout()
    for change in changes:
        loadout.set_change(loadout.place(change))
    return loadout


def from_changes(loadout: Loadout) -> str:
    """What the loadout writes from its changes alone."""
    bare = Loadout(game=loadout.game)
    bare.changes = dict(loadout.changes)
    bare.ops, bare.unread = list(loadout.ops), list(loadout.unread)
    return generator.build_modops_xml(bare)


class TestEachEditIsAModOp(unittest.TestCase):
    def test_each_change_is_written_as_a_modop_of_its_own(self):
        loadout = edited(value("Residents", "9"), value("Money", "3"))
        self.assertTrue(narrow.write(loadout))
        self.assertEqual(len(loadout.narrow), 2)
        for change in loadout.changes.values():
            op = next(op for op in loadout.narrow if op.uid == change.written_as)
            self.assertEqual(op.made, 1)
            self.assertIn("<%s>%s</%s>" % (change.tag, change.new_value, change.tag), op.source)
            self.assertTrue(op.source.startswith('<ModOp Type="replace" GUID="100"'))

    def test_what_is_exported_is_the_same_as_from_the_changes(self):
        loadout = edited(
            value("Residents", "9", note="more\nof them"),
            value("Text", "two\nlines", guid="200", label="Alpha"),
            Change(guid="300", asset_label="Gone", path="/Values/Building/Thing", tag="Thing",
                   op=OP_REMOVE),
            beside("400", "9001", note="one"),
            beside("400", "9002", spot=1, note="two"),
            Change(guid="500", asset_label="Swapped", path="/Values/Standard", tag="Standard",
                   op=OP_REMOVE),
            Change(guid="500", asset_label="Swapped", path="/Values", tag="Standard", op=OP_ADD,
                   payload="<Standard><GUID>500</GUID></Standard>", note="swapped"),
        )
        loadout.find("100", "/Values/Residence7/Residents").active = False
        before = generator.written(loadout)
        narrow.write(loadout)
        after = generator.written(loadout)
        self.assertEqual(after.text, before.text)
        self.assertEqual(
            [(s.kind, s.first, s.last, s.off) for s in after.spans],
            [(s.kind, s.first, s.last, s.off) for s in before.spans],
        )
        self.assertIn(SWITCHED_OFF_MARK, after.text)

    def test_the_export_is_written_from_the_modops(self):
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        loadout.narrow[0].source = loadout.narrow[0].source.replace(">9<", ">10<")
        self.assertIn("<Residents>10</Residents>", generator.build_modops_xml(loadout))
        # Until its change moves: then the change is newer, and the step
        # that moved it writes the op again.
        loadout.find("100", "/Values/Residence7/Residents").written_as = ""
        self.assertIn("<Residents>9</Residents>", generator.build_modops_xml(loadout))

    def test_ones_written_for_another_game_are_not_this_ones(self):
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        loadout.game = games.ANNO_117
        self.assertEqual(generator.build_modops_xml(loadout), from_changes(loadout))
        self.assertTrue(narrow.write(loadout))
        self.assertIn('Replace="@100/Residence7/Residents"', loadout.narrow[0].source)

    def test_a_node_swapped_whole_is_one_modop_for_two_changes(self):
        loadout = edited(
            Change(guid="500", asset_label="Swapped", path="/Values/Standard", tag="Standard",
                   op=OP_REMOVE),
            Change(guid="500", asset_label="Swapped", path="/Values", tag="Standard", op=OP_ADD,
                   payload="<Standard><GUID>500</GUID></Standard>", note="swapped"),
        )
        narrow.write(loadout)
        (op,) = loadout.narrow
        self.assertEqual((op.made, op.plain, op.note), (2, False, "swapped"))
        self.assertTrue(op.source.startswith('<ModOp Type="replace" GUID="500" Path="/Values/Standard"'))

    def test_a_second_asset_made_beside_the_same_one_joins_its_modop(self):
        loadout = edited(beside("400", "9001", note="one"))
        narrow.write(loadout)
        (first,) = loadout.narrow
        loadout.set_change(loadout.place(beside("400", "9002", spot=1, note="two")))
        narrow.write(loadout)
        (op,) = loadout.narrow
        self.assertEqual(op.uid, first.uid)
        self.assertEqual((op.made, op.note), (2, "one\ntwo"))
        self.assertEqual(op.source.count("<Asset>"), 2)
        self.assertEqual(from_changes(loadout), generator.build_modops_xml(loadout))

    def test_a_change_switched_off_is_a_modop_switched_off(self):
        loadout = edited(value("Residents", "9"))
        loadout.find("100", "/Values/Residence7/Residents").active = False
        narrow.write(loadout)
        self.assertFalse(loadout.narrow[0].active)

    def test_writing_again_with_nothing_moved_keeps_every_modop(self):
        loadout = edited(value("Residents", "9"), value("Money", "3"))
        narrow.write(loadout)
        held = list(loadout.narrow)
        self.assertFalse(narrow.write(loadout))
        self.assertTrue(all(a is b for a, b in zip(held, loadout.narrow, strict=True)))

    def test_a_value_typed_again_is_the_same_modop_saying_something_else(self):
        loadout = edited(value("Residents", "9"), value("Money", "3"))
        narrow.write(loadout)
        money = next(op for op in loadout.narrow if "Money" in op.source)
        loadout.set_change(loadout.place(value("Residents", "12")))
        self.assertTrue(narrow.write(loadout))
        self.assertTrue(any(op is money for op in loadout.narrow))
        residents = loadout.find("100", "/Values/Residence7/Residents")
        op = next(op for op in loadout.narrow if op.uid == residents.written_as)
        self.assertIn("<Residents>12</Residents>", op.source)

    def test_a_change_edited_where_it_stands_is_written_again(self):
        """Fields edits a payload in place (``effective.set_payload_text``)."""
        loadout = edited(beside("400", "9001"), value("Money", "3"))
        narrow.write(loadout)
        money = next(op for op in loadout.narrow if "Money" in op.source)
        made = next(change for change in loadout.changes.values() if change.creates_asset)
        made.payload = made.payload.replace("9001", "9005")
        self.assertTrue(narrow.write(loadout))
        self.assertTrue(any("9005" in op.source for op in loadout.narrow))
        self.assertTrue(any(op is money for op in loadout.narrow))

    def test_a_change_reverted_takes_its_modop_with_it(self):
        loadout = edited(value("Residents", "9"), value("Money", "3"))
        narrow.write(loadout)
        loadout.remove("100", "/Values/Residence7/Residents")
        narrow.write(loadout)
        self.assertEqual([op.made for op in loadout.narrow], [1])
        self.assertIn("Money", loadout.narrow[0].source)

    def test_only_the_own_mods_edits_are_written_so(self):
        loadout = edited(value("Residents", "9", origin="Their Mod", authored=False))
        loadout.set_op(Op(origin="", source='<ModOp Type="merge" Path="//Values/Trader"/>',
                          authored=True, uid="typed"))
        loadout.set_change(value("Money", "3", op_uid="typed"))
        narrow.write(loadout)
        self.assertEqual(loadout.narrow, [])
        self.assertTrue(all(not change.written_as for change in loadout.changes.values()))

    def test_they_are_no_kept_ops(self):
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        self.assertEqual(keptops.of(loadout), [])
        self.assertEqual(keptops.exportable(loadout), ([], set()))


PATCH = "Their Mod (my patch)"


def with_a_patch(*changes) -> Loadout:
    """A loadout holding somebody's mod and your patch of it, with
    ``changes`` made in the patch."""
    from anno_modding_studio.core.changes import ModLayer

    loadout = Loadout()
    loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
    loadout.set_layer(ModLayer(origin=PATCH, name=PATCH, patches="Their Mod", order=2))
    for change in changes:
        change.origin, change.authored = PATCH, True
        loadout.set_change(loadout.place(change))
    return loadout


class TestEachModOfYoursHasItsModOps(unittest.TestCase):
    """Section 4.2 for the patches and a carried-on mod (4b-2): what their
    projects keep are ModOps, as the loadout's own mod's are."""

    def test_an_edit_in_a_patch_is_a_modop_of_the_patch(self):
        loadout = with_a_patch(value("Residents", "9"))
        self.assertTrue(narrow.write(loadout))
        (op,) = loadout.narrow
        self.assertEqual(op.origin, PATCH)
        self.assertIn("<Residents>9</Residents>", op.source)
        self.assertEqual(loadout.find("100", "/Values/Residence7/Residents", origin=PATCH).written_as, op.uid)

    def test_the_patch_exports_the_same_as_from_its_changes(self):
        loadout = with_a_patch(
            value("Residents", "9", note="more"),
            value("Text", "two\nlines", guid="200", label="Alpha"),
            beside("400", "9001"),
        )
        before = generator.written(generator.for_mod(loadout, PATCH)).text
        narrow.write(loadout)
        view = generator.for_mod(loadout, PATCH)
        self.assertEqual(len(view.narrow), 3)
        self.assertEqual(generator.written(view).text, before)

    def test_each_mod_keeps_its_own(self):
        loadout = with_a_patch(value("Residents", "9"))
        loadout.set_change(loadout.place(value("Money", "3")))
        narrow.write(loadout)
        self.assertEqual(sorted(op.origin for op in loadout.narrow), ["", PATCH])
        self.assertEqual([op.origin for op in generator.for_mod(loadout, "").narrow], [""])

    def test_a_carried_on_mod_has_modops_for_what_was_made_here_alone(self):
        from anno_modding_studio.core.changes import ModLayer

        loadout = Loadout(home="Mine")
        loadout.set_layer(ModLayer(origin="Mine", name="Mine", folder="C:/mods/Mine", order=1))
        loadout.set_op(Op(origin="Mine", source='<ModOp Type="merge" GUID="100"/>', uid="read"))
        loadout.set_change(value("Money", "3", origin="Mine", authored=False, op_uid="read"))
        loadout.set_change(loadout.place(value("Residents", "9", origin="Mine", authored=True)))
        narrow.write(loadout)
        (op,) = loadout.narrow
        self.assertEqual(op.origin, "Mine")
        self.assertIn("Residents", op.source)

    def test_an_edit_in_somebody_elses_mod_is_not_one(self):
        from anno_modding_studio.core.changes import ModLayer

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", order=1))
        loadout.set_change(loadout.place(value("Residents", "9", origin="Their Mod", authored=True)))
        narrow.write(loadout)
        self.assertEqual(loadout.narrow, [])

    def test_undoing_an_edit_in_the_patch_puts_back_the_patchs_alone(self):
        loadout = with_a_patch(value("Residents", "9"))
        loadout.set_change(loadout.place(value("Money", "3")))
        history = UndoHistory(loadout)
        own = next(op for op in loadout.narrow if op.origin == "")
        loadout.set_change(loadout.place(value("Residents", "12", origin=PATCH, authored=True)))
        step = history.commit()
        self.assertEqual(step.narrow_of, frozenset({PATCH}))
        history.undo()
        patch = next(op for op in loadout.narrow if op.origin == PATCH)
        self.assertIn(">9<", patch.source)
        self.assertEqual(vars(next(op for op in loadout.narrow if op.origin == "")), vars(own))

    def test_a_held_patchs_modops_are_left_as_they_were_read(self):
        from anno_modding_studio.core import projects

        loadout = with_a_patch(value("Residents", "9"))
        narrow.write(loadout)
        newer = Op(origin=PATCH, source='<ModOp Type="replace" GUID="100"/>', uid="theirs", authored=True)
        loadout.narrow = [newer]
        loadout.projects[PATCH] = projects.Ref("projects/p.annoproj", held=projects.MOVED)
        narrow.write(loadout)
        self.assertEqual(loadout.narrow, [newer])

    def test_the_patchs_project_keeps_them(self):
        from anno_modding_studio.core import projects

        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        loadout = with_a_patch(value("Residents", "9"))
        UndoHistory(loadout)
        with tempfile.TemporaryDirectory() as tmp:
            loadout.save(Path(tmp) / "Main.annoload")
            kept = projects.read(projects.where(loadout.projects[PATCH].path))
            written = json.loads((Path(tmp) / "Main.annoload").read_text(encoding="utf-8"))
        self.assertEqual([op["uid"] for op in kept["narrow"]], [loadout.narrow[0].uid])
        self.assertNotIn("narrow", written)


class TestUndoMovesThemWithTheChanges(unittest.TestCase):
    def test_a_step_holds_the_modops_and_puts_them_back(self):
        loadout = edited(value("Residents", "9"))
        history = UndoHistory(loadout)
        (first,) = loadout.narrow
        loadout.set_change(loadout.place(value("Residents", "12")))
        step = history.commit()
        self.assertIsNotNone(step.narrow)
        self.assertIn(">12<", loadout.narrow[0].source)
        history.undo()
        self.assertEqual(vars(loadout.narrow[0]), vars(first))
        self.assertEqual(loadout.find("100", "/Values/Residence7/Residents").new_value, "9")
        self.assertEqual(from_changes(loadout), generator.build_modops_xml(loadout))
        history.redo()
        self.assertIn(">12<", loadout.narrow[0].source)
        self.assertEqual(from_changes(loadout), generator.build_modops_xml(loadout))

    def test_a_new_edit_and_its_modop_are_one_step(self):
        loadout = Loadout()
        history = UndoHistory(loadout)
        loadout.set_change(loadout.place(value("Residents", "9")))
        history.commit()
        self.assertEqual(len(loadout.narrow), 1)
        history.undo()
        self.assertEqual((loadout.changes, loadout.narrow), ({}, []))

    def test_a_loadout_taken_on_has_its_modops_written_and_no_step(self):
        loadout = edited(value("Residents", "9"))
        history = UndoHistory(loadout)
        self.assertEqual(len(loadout.narrow), 1)
        self.assertFalse(history.can_undo)
        self.assertIsNone(history.commit())


class TestTheyAreKept(unittest.TestCase):
    def test_a_checkpoint_holds_them_and_a_restore_puts_them_back(self):
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        held = [vars(op) for op in loadout.narrow]
        checkpoint = loadout.checkpoint("before")
        loadout.set_change(loadout.place(value("Residents", "12")))
        narrow.write(loadout)
        loadout.restore(checkpoint)
        self.assertEqual([vars(op) for op in loadout.narrow], held)
        self.assertEqual(from_changes(loadout), generator.build_modops_xml(loadout))

    def test_saved_and_read_back_they_are_what_they_were(self):
        loadout = edited(value("Residents", "9"), beside("400", "9001"), beside("400", "9002", 1))
        narrow.write(loadout)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mine.annoload"
            loadout.save(path)
            back = Loadout.load(path)
        self.assertEqual([vars(op) for op in back.narrow], [vars(op) for op in loadout.narrow])
        self.assertEqual(generator.build_modops_xml(back), generator.build_modops_xml(loadout))

    def test_a_loadout_without_them_is_written_back_without_them(self):
        loadout = edited(value("Residents", "9"))
        saved = loadout.to_dict()
        self.assertNotIn("narrow", saved)
        self.assertNotIn("written_as", saved["changes"][0])

    def test_ones_another_studio_saved_are_written_again_from_the_changes(self):
        """An older Studio keeps them as it found them and edits the changes."""
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        saved = loadout.to_dict()
        saved["changes"][0]["new_value"] = "12"
        saved["written_by"] = "0.0.1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mine.annoload"
            path.write_text(json.dumps(saved), encoding="utf-8")
            back = Loadout.load(path)
        self.assertEqual(back.narrow, [])
        self.assertIn("<Residents>12</Residents>", generator.build_modops_xml(back))
        UndoHistory(back)
        self.assertIn("<Residents>12</Residents>", back.narrow[0].source)

    def test_a_mod_written_out_on_its_own_takes_them_along(self):
        loadout = edited(value("Residents", "9"))
        narrow.write(loadout)
        view = generator.for_mod(loadout, "")
        self.assertEqual([op.uid for op in view.narrow], [loadout.narrow[0].uid])


class TestReadBackTheyAreTheirChanges(ImportCase):
    """What a loadout will do once a mod of yours keeps only its ModOps: read
    each, and find the change it was written for."""

    def test_each_modop_reads_back_as_what_it_was_written_for(self):
        report = self.read(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
            '<ModOp Type="remove" GUID="1000" Path="/Values/Cost/Costs/Item[Ingredient=\'1011\']"/>'
            '<ModOp Type="add" GUID="1001" Path="/Values/Building"><Upkeep>3</Upkeep></ModOp>'
            '<ModOp Type="addNextSibling" GUID="1001">'
            "<Asset><Template>Building</Template><Values><Standard><GUID>9001</GUID>"
            "<Name>new</Name></Standard></Values></Asset></ModOp>"
        )
        loadout = Loadout()
        for change in report.changes:
            # As the Studio holds an edit of its own: narrow, and made here.
            change.op_uid, change.search, change.authored, change.order = "", "", True, 0
            loadout.set_change(change)
        narrow.write(loadout)
        self.assertEqual(len(loadout.narrow), 4)
        for op in loadout.narrow:
            again = self.read(op.source)
            under = [c for c in loadout.changes.values() if c.written_as == op.uid]
            self.assertEqual(
                sorted((c.guid, c.path, c.op, c.new_value, c.payload) for c in again.changes),
                sorted((c.guid, c.path, c.op, c.new_value, c.payload) for c in under),
                op.source,
            )


if __name__ == "__main__":
    unittest.main()
