"""A mod's file as text, and what typing in it comes to (``core.modfile``).

The file tab holds one mod's whole file.  What is typed there is read in the
order typed, and the mod is moved to match: an op the text still has is the op
the mod held, a new one goes where it was typed, and one left out is taken
out.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lxml import etree  # noqa: E402
from support import StackCase  # noqa: E402
from support import radius_change as radius  # noqa: E402

from anno_modding_studio.core import games, generator, modfile, typedops  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    STUDIO_SAYS_MARK,
    SWITCHED_OFF_MARK,
    Change,
    switched_off,
)
from anno_modding_studio.core.undo import UndoHistory  # noqa: E402

GUID = "1000"
RADIUS = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>%s</Radius></ModOp>'
TYPE = (
    '<ModOp Type="replace" GUID="1000" Path="/Values/Building/BuildingType">'
    "<BuildingType>%s</BuildingType></ModOp>"
)
EVERY = '<ModOp Type="merge" Path="//Values/Building"><Radius>%s</Radius></ModOp>'
NOWHERE = '<ModOp Type="replace" GUID="9999" Path="/Values/Nope"><Nope>1</Nope></ModOp>'
GROUP = '<Group Condition="%s">%s</Group>'
LOOP = '<Group MaxRepeat="2">%s</Group>'


def document(*ops: str) -> str:
    return "<ModOps>\n%s\n</ModOps>\n" % "\n".join(ops)


class Filed(StackCase):
    """Typing into the file of *Late*, which loads after *Early*."""

    ORIGIN = "Late"

    def shown(self) -> modfile.Shown:
        return modfile.shown(self.loadout, self.ORIGIN)

    def type(self, text: str, was: modfile.Shown = None) -> modfile.Applied:
        found, done = modfile.apply(
            self.workspace, self.ORIGIN, text, was if was is not None else self.shown()
        )
        self.assertIsNotNone(done, (found.error, found.loose, found.refused))
        return done

    def retype(self, old: str, new: str) -> modfile.Applied:
        """Apply the file as shown with ``old`` typed over as ``new``."""
        shown = self.shown()
        self.assertEqual(shown.text.count(old), 1, shown.text)
        return self.type(shown.text.replace(old, new), shown)

    def ops(self):
        return sorted(self.loadout.ops_of(self.ORIGIN), key=lambda op: op.order)

    def sources(self) -> list:
        """What the mod's file says, an op to an entry, in order."""
        root = etree.fromstring(self.shown().text.encode("utf-8"))
        return [
            " ".join(etree.tostring(node, encoding="unicode", with_tail=False).split())
            for node in root
            if isinstance(node.tag, str)
        ]

    def held(self) -> dict:
        """Everything the mod holds, to compare before and after."""
        return {
            "ops": [(op.uid, op.source, op.order, tuple(op.within), op.active, op.note, op.authored)
                    for op in self.ops()],
            "changes": {
                key: dict(vars(change)) for key, change in self.loadout.changes.items()
                if change.origin == self.ORIGIN
            },
            "unread": [(entry.source, entry.order, tuple(entry.within))
                       for entry in self.loadout.unread_of(self.ORIGIN)],
            "taken out": [list(entry) for entry in self.loadout.taken_out],
        }


class TestTheFileAsShown(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(RADIUS % 20, GROUP % ("#Early", EVERY % 30)))
        change = Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        )
        self.loadout.set_change(self.loadout.place(change))

    def test_it_is_the_file_with_nothing_of_the_studios_over_its_modops(self):
        shown = self.shown()
        written = generator.build_modops_xml(generator.for_mod(self.loadout, "Late"))
        self.assertNotIn(STUDIO_SAYS_MARK, written)
        self.assertEqual(str(shown.text), written)
        lines = shown.text.split("\n")
        at = next(at for at, line in enumerate(lines) if 'Type="add"' in line)
        self.assertEqual(lines[at - 1], "")

    def test_each_thing_says_which_lines_it_is_on(self):
        shown = self.shown()
        lines = shown.text.split("\n")
        kinds = [item.kind for item in shown.items]
        self.assertEqual(kinds, ["op", "group", "op", "end", "changes"])
        for item in shown.items:
            said = "\n".join(lines[item.first - 1:item.last])
            if item.kind == "end":
                self.assertEqual(said.strip(), "</Group>")
            elif item.kind == "group":
                self.assertEqual(said.strip(), '<Group Condition="#Early">')
            else:
                self.assertTrue(said.lstrip().startswith("<ModOp "), said)
                self.assertTrue(said.rstrip().endswith("</ModOp>"), said)
        self.assertEqual([item.depth for item in shown.items], [0, 0, 1, 0, 0])

    def test_a_line_finds_what_it_is_part_of_and_the_other_way(self):
        shown = self.shown()
        for item in shown.items:
            self.assertIs(shown.item_at(item.first), item)
            self.assertIs(shown.item_at(item.last), item)
        self.assertIsNone(shown.item_at(1))
        op = self.ops()[0]
        self.assertEqual(shown.line_of_op(op.uid), shown.items[0].first)
        (added,) = [c for c in self.loadout.changes_of("Late") if not c.op_uid]
        self.assertEqual(shown.line_of_change(added.key), shown.items[-1].first)

    def test_one_switched_off_is_the_comment_it_is_written_as(self):
        self.ops()[0].active = False
        shown = self.shown()
        first = shown.items[0]
        self.assertTrue(first.off)
        lines = shown.text.split("\n")
        self.assertIn(SWITCHED_OFF_MARK, lines[first.first - 1])
        self.assertEqual(lines[first.last - 1].strip(), "-->")

    def test_each_thing_opens_with_its_tag(self):
        self.ops()[0].note = "wider"
        shown = self.shown()
        self.assertEqual([shown.opening(item) for item in shown.items], [
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">',
            '<Group Condition="#Early">',
            '<ModOp Type="merge" Path="//Values/Building">',
            "</Group>",
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">',
        ])
        # Switched off, it is the tag inside the comment.
        self.ops()[0].active = False
        shown = self.shown()
        self.assertEqual(
            shown.opening(shown.items[0]),
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">',
        )


class TestTheTagAThingOpensWith(unittest.TestCase):
    """What stands for an op in the outline of the file: read off the text,
    which is the file as the Studio writes it, or as a mod carried unread."""

    def opening(self, text: str, off: bool = False) -> str:
        lines = text.split("\n")
        item = modfile.Item(kind="op", first=1, last=len(lines), off=off)
        return modfile.Shown(text=text, items=[item]).opening(item)

    def test_a_tag_over_several_lines_is_one(self):
        self.assertEqual(
            self.opening('  <ModOp Type="merge"\n     GUID="1">\n    <A>1</A>\n  </ModOp>'),
            '<ModOp Type="merge" GUID="1">',
        )

    def test_a_close_in_quotes_is_not_the_end(self):
        self.assertEqual(
            self.opening("<ModOp Type='merge' Path=\"//V[A > 2]\"><A>3</A></ModOp>"),
            "<ModOp Type='merge' Path=\"//V[A > 2]\">",
        )

    def test_the_comments_before_it_are_not_it(self):
        self.assertEqual(
            self.opening('  <!-- note: why -->\n  <!-- one\n  two -->\n  <ModOp Type="add">\n  </ModOp>'),
            '<ModOp Type="add">',
        )

    def test_switched_off_a_dash_is_a_dash_again(self):
        text = "\n".join(switched_off(['  <ModOp Type="replace" Path="//A[B=\'--\']">', "  </ModOp>"]))
        self.assertEqual(self.opening(text, off=True), '<ModOp Type="replace" Path="//A[B=\'--\']">')

    def test_with_no_tag_it_is_its_first_line(self):
        self.assertEqual(self.opening("\n  just words\n"), "just words")


class TestTypingNothingChangesNothing(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(
            "<!-- why -->", RADIUS % 20, GROUP % ("#Early", EVERY % 30), NOWHERE, TYPE % "Farm",
        ))
        self.loadout.set_change(self.loadout.place(Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        )))
        self.ops()[-1].active = False
        for change in self.loadout.changes_by_op(self.ops()[-1].uid):
            change.active = False

    def test_the_text_as_shown_is_not_read(self):
        before = self.held()
        done = self.type(self.shown().text)
        self.assertFalse(done.moved)
        self.assertEqual(self.held(), before)

    def test_nor_is_it_laid_out_another_way(self):
        before = self.held()
        flat = "\n".join(line.strip() for line in self.shown().text.split("\n") if line.strip())
        self.assertFalse(self.type(flat).moved)
        self.assertEqual(self.held(), before)

    def test_read_all_the_same_it_comes_back_as_it_was(self):
        """Typed with something new at its end, every op the text still has
        is the op it was: its uid, its place, its switch, its note."""
        before = self.held()
        shown = self.shown()
        done = self.type(shown.text.replace("</ModOps>", (RADIUS % 99) + "\n</ModOps>"), shown)
        self.assertEqual((done.added, done.changed, done.dropped), (1, 0, 0))
        after = self.held()
        self.assertEqual(after["ops"][:-1], before["ops"])
        self.assertEqual(after["unread"], before["unread"])
        self.assertEqual(after["taken out"], [])
        added = [c for c in self.loadout.changes_of("Late") if c.op == OP_ADD]
        self.assertEqual(len(added), 1)
        self.assertFalse(added[0].op_uid, "the Studio's own change is that change still")

    def test_and_what_is_written_then_is_what_was_typed(self):
        shown = self.shown()
        typed = shown.text.replace("</ModOps>", (RADIUS % 99) + "\n</ModOps>")
        self.type(typed, shown)
        again = self.shown().text
        # Laid out the Studio's way, and otherwise letter for letter.
        self.assertEqual(
            "".join(line.strip() for line in again.split("\n")),
            "".join(line.strip() for line in typed.split("\n")),
        )
        self.assertFalse(self.type(again).moved)


class TestAnOpTyped(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(RADIUS % 20, TYPE % "Farm"))
        for op in self.ops():
            # As a mod read from its folder has them.
            op.authored = False

    def test_a_new_one_goes_where_it_was_typed(self):
        first, last = self.ops()
        done = self.retype("  <ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Building/BuildingType\">",
                           (EVERY % 30) + "\n  " + TYPE.split("<BuildingType>")[0])
        self.assertEqual((done.added, done.changed, done.dropped), (1, 0, 0))
        ops = self.ops()
        self.assertEqual([op.uid for op in (ops[0], ops[2])], [first.uid, last.uid])
        self.assertEqual([op.order for op in ops], [0, 1, 2])
        self.assertTrue(ops[1].searches)
        self.assertEqual((ops[0].authored, ops[1].authored, ops[2].authored), (False, True, False))
        self.assertEqual(len(self.sources()), 3)
        self.assertIn("//Values/Building", self.sources()[1])

    def test_it_is_read_after_the_ops_typed_above_it(self):
        """A pattern typed after an op that sets the same field finds what
        that op left, and one typed before it finds the game's own."""
        self.retype("</ModOps>", (EVERY % 30) + "\n</ModOps>")
        pattern = self.ops()[-1]
        mine = [c for c in self.loadout.changes_by_op(pattern.uid) if c.guid == GUID]
        self.assertEqual([(c.old_value, c.new_value) for c in mine], [("20", "30")])

    def test_a_pattern_finds_what_an_earlier_mod_adds_and_not_a_later_ones(self):
        def adding(origin: str, guid: str) -> Change:
            return Change(
                guid=GUID, asset_label="made", path="", tag="Asset", op=OP_ADD_NEXT,
                origin=origin, authored=False,
                payload="<Asset><Template>Building</Template><Values><Standard>"
                "<GUID>%s</GUID><Name>made</Name></Standard><Building><Radius>5</Radius>"
                "</Building></Values></Asset>" % guid,
            )

        from anno_modding_studio.core.changes import ModLayer

        self.loadout.set_layer(ModLayer(origin="Later", name="Later", order=3))
        self.loadout.set_change(adding("Early", "8801"))
        self.loadout.set_change(adding("Later", "8802"))
        self.retype("</ModOps>", (EVERY % 30) + "\n</ModOps>")
        pattern = self.ops()[-1]
        reached = {change.guid for change in self.loadout.changes_by_op(pattern.uid)}
        self.assertIn("8801", reached)
        self.assertNotIn("8802", reached)

    def test_one_left_out_is_taken_out_and_stays_out(self):
        first, last = self.ops()
        shown = self.shown()
        item = shown.items[0]
        lines = shown.text.split("\n")
        del lines[item.first - 1:item.last]
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.added, done.changed, done.dropped), (0, 0, 1))
        self.assertEqual([op.uid for op in self.ops()], [last.uid])
        self.assertFalse([c for c in self.loadout.changes_of("Late") if c.tag == "Radius"])
        self.assertEqual([entry[:3] for entry in self.loadout.taken_out], [["Late", first.source, []]])

    def test_one_typed_over_keeps_its_uid_and_its_place(self):
        first, last = self.ops()
        done = self.retype("<Radius>20</Radius>", "<Radius>25</Radius>")
        self.assertEqual((done.added, done.changed, done.dropped), (0, 1, 0))
        ops = self.ops()
        self.assertEqual([op.uid for op in ops], [first.uid, last.uid])
        self.assertIn("25", ops[0].source)
        self.assertTrue(ops[0].authored)
        (change,) = self.loadout.changes_by_op(first.uid)
        self.assertEqual(change.new_value, "25")
        # The op the mod's folder has is not to come back beside it.
        self.assertEqual([entry[:3] for entry in self.loadout.taken_out], [["Late", first.source, []]])

    def test_one_aimed_at_another_asset_at_the_head_of_its_path_is_typed_over(self):
        """The ``@GUID`` is taken out of the path as the op is read, and the
        asset was not compared: the Apply was the text unedited."""
        at = RADIUS.replace('GUID="1000" Path="/', 'Path="@1000/') % 7
        self.retype("</ModOps>", at + "\n</ModOps>")
        done = self.retype('Path="@1000/', 'Path="@1001/')
        # Aimed elsewhere, it is another op, as with a GUID= typed over.
        self.assertEqual((done.added, done.dropped), (1, 1))
        typed = self.ops()[-1]
        self.assertIn("@1001/", typed.source)
        self.assertEqual(
            [change.guid for change in self.loadout.changes_by_op(typed.uid)], ["1001"]
        )

    def test_two_moved_about_are_the_ops_they_were(self):
        first, last = self.ops()
        shown = self.shown()
        a, b = shown.items
        lines = shown.text.split("\n")
        moved = (
            lines[:a.first - 1] + lines[b.first - 1:b.last] + [""]
            + lines[a.first - 1:a.last] + lines[b.last:]
        )
        done = self.type("\n".join(moved), shown)
        self.assertTrue(done.rearranged)
        self.assertTrue(done.moved)
        self.assertEqual([op.uid for op in self.ops()], [last.uid, first.uid])
        self.assertEqual(self.loadout.taken_out, [])
        self.assertFalse(any(op.authored for op in self.ops()))

    def test_a_comment_typed_above_one_is_its_note(self):
        first, _last = self.ops()
        done = self.retype(
            '  <ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">',
            '  <!-- so the hut reaches the forest -->\n'
            '  <ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">',
        )
        self.assertEqual((done.added, done.changed, done.dropped), (0, 1, 0))
        self.assertEqual(self.loadout.op(first.uid).note, "so the hut reaches the forest")
        self.assertFalse(self.loadout.op(first.uid).authored)
        self.assertIn("note: so the hut reaches the forest", self.shown().text)

    def test_one_the_studio_cannot_read_is_carried_where_it_was_typed(self):
        done = self.retype("</ModOps>", NOWHERE + "\n</ModOps>")
        self.assertEqual((done.added, done.unread), (1, 1))
        (entry,) = self.loadout.unread_of("Late")
        self.assertEqual(entry.order, 2)
        self.assertIn("9999", self.sources()[-1])
        # And typed over so that it reads, it is an op.
        shown = self.shown()
        typed = shown.text.replace('GUID="9999"', 'GUID="1000"').replace(
            "/Values/Nope", "/Values/Building/Radius"
        ).replace("<Nope>1</Nope>", "<Radius>7</Radius>")
        done = self.type(typed, shown)
        self.assertEqual((done.added, done.dropped), (1, 1))
        self.assertEqual(self.loadout.unread_of("Late"), [])
        self.assertEqual(len(self.ops()), 3)


class TestSwitchingByTyping(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(RADIUS % 20, TYPE % "Farm"))

    def test_put_in_the_comment_it_is_switched_off(self):
        first, _last = self.ops()
        shown = self.shown()
        item = shown.items[0]
        lines = shown.text.split("\n")
        lines.insert(item.last, "  -->")
        lines.insert(item.first - 1, "  <!-- %s:" % SWITCHED_OFF_MARK)
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.added, done.changed, done.dropped), (0, 1, 0))
        op = self.loadout.op(first.uid)
        self.assertFalse(op.active)
        self.assertEqual([c.active for c in self.loadout.changes_by_op(op.uid)], [False])
        self.assertTrue(self.shown().items[0].off)

    def test_and_taken_out_of_it_switched_on(self):
        first, _last = self.ops()
        first.active = False
        for change in self.loadout.changes_by_op(first.uid):
            change.active = False
        shown = self.shown()
        item = shown.items[0]
        lines = shown.text.split("\n")
        del lines[item.last - 1]
        del lines[item.first - 1]
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.added, done.changed, done.dropped), (0, 1, 0))
        self.assertTrue(self.loadout.op(first.uid).active)
        self.assertEqual(
            [c.active for c in self.loadout.changes_by_op(first.uid)], [True]
        )


class TestAChangeOfTheStudios(Filed):
    """A change made in Fields is in the file as the ModOp written for it."""

    def setUp(self):
        super().setUp()
        self.type(document(TYPE % "Farm"))
        self.change = radius("Late", "10", "20")
        self.change.note = "wider"
        self.loadout.set_change(self.loadout.place(self.change))

    def mine(self):
        return [c for c in self.loadout.changes_of("Late") if not c.op_uid]

    def test_left_as_it_is_it_is_that_change_still(self):
        self.retype("<BuildingType>Farm</BuildingType>", "<BuildingType>Mine</BuildingType>")
        (change,) = self.mine()
        self.assertIs(change, self.change)
        self.assertEqual((change.new_value, change.note), ("20", "wider"))
        self.assertEqual(len(self.ops()), 1)

    def test_typed_over_it_is_the_op_that_was_typed_where_it_was(self):
        done = self.retype("<Radius>20</Radius>", "<Radius>25</Radius>")
        self.assertEqual((done.added, done.changed, done.dropped), (1, 0, 1))
        self.assertEqual(self.mine(), [])
        ops = self.ops()
        self.assertEqual(len(ops), 2)
        self.assertIn("<Radius>25</Radius>", ops[1].source)
        self.assertTrue(ops[1].authored)
        (change,) = self.loadout.changes_by_op(ops[1].uid)
        self.assertEqual((change.old_value, change.new_value), ("10", "25"))

    def test_moved_above_an_op_it_is_written_there(self):
        shown = self.shown()
        op, change = shown.items
        lines = shown.text.split("\n")
        moved = (
            lines[:op.first - 1] + lines[change.first - 1:change.last] + [""]
            + lines[op.first - 1:op.last] + ["</ModOps>"]
        )
        done = self.type("\n".join(moved), shown)
        self.assertTrue(done.rearranged)
        self.assertEqual([item.kind for item in self.shown().items], ["changes", "op"])
        self.assertIs(self.mine()[0], self.change)

    def test_switched_off_by_typing_and_its_note_typed_over(self):
        shown = self.shown()
        item = shown.items[-1]
        lines = shown.text.split("\n")
        self.assertIn("note: wider", lines[item.first - 1])
        lines[item.first - 1] = "  <!-- note: wider still -->"
        lines.insert(item.last, "  -->")
        lines.insert(item.first, "  <!-- %s:" % SWITCHED_OFF_MARK)
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.added, done.changed, done.dropped), (0, 1, 0))
        (change,) = self.mine()
        self.assertIs(change, self.change)
        self.assertEqual((change.active, change.note), (False, "wider still"))

    def test_an_op_typed_onto_its_field_stands_after_it(self):
        """Two ops of a mod that set one field are two ops: the change is
        that change still, and the op typed after it has an answer of its
        own, set over what the change left."""
        self.retype("</ModOps>", (RADIUS % 33) + "\n</ModOps>")
        (change,) = self.mine()
        self.assertIs(change, self.change)
        typed = self.ops()[-1]
        (answer,) = self.loadout.changes_by_op(typed.uid)
        self.assertEqual((answer.old_value, answer.new_value), ("20", "33"))
        self.assertNotEqual(answer.key, change.key)
        self.assertEqual(len(self.sources()), 3)
        self.assertFalse(self.type(self.shown().text).moved)

    def test_an_edit_made_after_goes_after_what_was_typed(self):
        self.retype("</ModOps>", NOWHERE + "\n</ModOps>")
        made = self.loadout.place(Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        ))
        self.loadout.set_change(made)
        self.assertGreater(made.order, self.loadout.unread_of("Late")[0].order)
        self.assertEqual(self.shown().items[-1].changes, (made,))


class TestAGroupIsItsTags(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(GROUP % ("#Early", (RADIUS % 20) + (TYPE % "Farm"))))

    def test_what_is_in_it_says_so(self):
        group, first, last = self.ops()
        self.assertTrue(group.scope)
        self.assertFalse(group.idle)
        self.assertEqual((first.within, last.within), ([group.uid], [group.uid]))
        self.assertEqual(len(self.loadout.changes_of("Late")), 2)

    def test_its_condition_typed_over_is_that_group_asked_something_else(self):
        group, first, last = self.ops()
        done = self.retype('Condition="#Early"', 'Condition="#Nobody"')
        # A group is no ModOp, and is counted apart.
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 0, 1))
        self.assertTrue(done.moved)
        now = self.ops()
        self.assertEqual([op.uid for op in now], [group.uid, first.uid, last.uid])
        self.assertTrue(now[0].idle)
        self.assertIn("#Nobody", now[0].source)
        # It does not run today, so nothing in it comes to anything.
        self.assertEqual(self.loadout.changes_of("Late"), [])

    def test_its_tags_taken_out_leave_its_ops_in_no_group(self):
        _group, first, last = self.ops()
        shown = self.shown()
        lines = [
            line for line in shown.text.split("\n")
            if "<Group" not in line and "</Group>" not in line
        ]
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.dropped, done.groups), (0, 1))
        now = self.ops()
        self.assertEqual([op.uid for op in now], [first.uid, last.uid])
        self.assertEqual([op.within for op in now], [[], []])

    def test_one_written_in_two_parts_is_not_read_again_for_that(self):
        """A change of the Studio's that stands between two ops of a group
        is written between two groups with one tag.  Each of those is a tag:
        with something typed elsewhere, what is in them stays as it was."""
        _group, first, last = self.ops()
        between = radius("Late", "20", "21")
        between.path, between.tag = "/Values/Cost/Costs/Item/Amount", "Amount"
        between.order = first.order
        self.loadout.set_change(between)
        shown = self.shown()
        self.assertEqual(shown.text.count("<Group "), 2)
        done = self.type(shown.text.replace("</ModOps>", NOWHERE + "\n</ModOps>"), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (1, 0, 0, 0))
        # The very ops, and not readings of them made anew.
        self.assertIs(self.loadout.op(first.uid), first)
        self.assertIs(self.loadout.op(last.uid), last)
        self.assertEqual(self.shown().text.count("<Group "), 2)

    def test_past_line_65535_an_op_is_still_known_for_the_one_it_is(self):
        """The parser counts lines in sixteen bits.  Past that it gives an
        element a line near the one it starts on, and a group's tag was
        taken for the op under it: what was in the group fell out of it."""
        group, first, last = self.ops()
        shown = self.shown()
        pad = 70000
        lines = shown.text.split("\n")
        shown.text = "\n".join(lines[:2] + [""] * pad + lines[2:])
        for item in shown.items:
            item.first += pad
            item.last += pad
        done = self.type(shown.text.replace("</ModOps>", NOWHERE + "\n</ModOps>"), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (1, 0, 0, 0))
        self.assertIs(self.loadout.op(group.uid), group)
        self.assertEqual(self.loadout.op(first.uid).within, [group.uid])
        self.assertEqual(self.loadout.op(last.uid).within, [group.uid])

    def test_an_op_typed_into_it_is_in_it(self):
        group = self.ops()[0]
        self.retype("  </Group>", "    " + (EVERY % 30) + "\n  </Group>")
        typed = self.ops()[-1]
        self.assertTrue(typed.searches)
        self.assertEqual(typed.within, [group.uid])


class TestAGroupSwitchedOff(Filed):
    """A group switched off as its box in the list switches it: the group
    and everything in it.  Its two tags are each in a comment of their own,
    so the file says the group is off (A11-8)."""

    def setUp(self):
        super().setUp()
        self.type(document(GROUP % ("#Early", (RADIUS % 20) + (TYPE % "Farm"))))
        for op in self.ops():
            op.active = False
        for change in self.loadout.changes_of("Late"):
            change.active = False

    def test_its_tags_are_written_switched_off(self):
        shown = self.shown()
        self.assertEqual([(item.kind, item.off) for item in shown.items], [
            ("group", True), ("op", True), ("op", True), ("end", True),
        ])
        lines = shown.text.split("\n")
        group, end = shown.items[0], shown.items[-1]
        self.assertIn(SWITCHED_OFF_MARK, lines[group.first - 1])
        self.assertIn(SWITCHED_OFF_MARK, lines[end.first - 1])
        self.assertEqual(shown.opening(group), '<Group Condition="#Early">')
        # Nothing of it is anything the game reads.
        root = etree.fromstring(shown.text.encode("utf-8"))
        self.assertEqual([node for node in root if isinstance(node.tag, str)], [])

    def test_applied_as_it_is_shown_nothing_changes(self):
        before = self.held()
        shown = self.shown()
        found, done = modfile.apply(self.workspace, self.ORIGIN, shown.text, shown)
        self.assertIsNotNone(done, (found.error, found.loose, found.refused))
        self.assertEqual(self.held(), before)
        self.assertFalse(self.ops()[0].active)

    def test_with_a_note_applied_as_it_is_shown_nothing_changes(self):
        """Its note above its switched-off tag is its note still, and not a
        comment standing alone that leaves the ops in it in no group."""
        self.ops()[0].note = "why"
        before = self.held()
        shown = self.shown()
        found, done = modfile.apply(self.workspace, self.ORIGIN, shown.text, shown)
        self.assertIsNotNone(done, (found.error, found.loose, found.refused))
        self.assertEqual(self.held(), before)
        self.assertEqual(self.shown().text, shown.text)

    def test_with_something_else_typed_it_stays_off(self):
        group, first, last = self.ops()
        shown = self.shown()
        self.type(shown.text.replace("</ModOps>", NOWHERE + "\n</ModOps>"), shown)
        self.assertIs(self.loadout.op(group.uid), group)
        self.assertFalse(group.active)
        self.assertEqual((first.within, last.within), ([group.uid], [group.uid]))

    def test_its_tags_taken_out_of_their_comments_switch_it_on(self):
        group, first, _last = self.ops()
        shown = self.shown()
        lines = shown.text.split("\n")
        for item in (shown.items[-1], shown.items[0]):
            del lines[item.last - 1]
            del lines[item.first - 1]
        self.type("\n".join(lines), shown)
        self.assertTrue(self.loadout.op(group.uid).active)
        self.assertFalse(self.loadout.op(first.uid).active)
        self.assertIn('<Group Condition="#Early">', self.shown().text)

    def test_with_an_op_in_it_switched_on_its_tags_are_live(self):
        """That op runs only if the group's condition holds, which it can
        only do inside the group."""
        _group, first, _last = self.ops()
        first.active = True
        for change in self.loadout.changes_by_op(first.uid):
            change.active = True
        shown = self.shown()
        self.assertEqual([(item.kind, item.off) for item in shown.items], [
            ("group", False), ("op", False), ("op", True), ("end", False),
        ])
        root = etree.fromstring(shown.text.encode("utf-8"))
        (group,) = [node for node in root if isinstance(node.tag, str)]
        self.assertEqual(group.get("Condition"), "#Early")


class TestALoopIsOneOp(Filed):
    """Anno 117 runs a ``MaxRepeat`` group in rounds, and the mod holds it
    whole: one op, whose text is the group with everything in it."""

    def setUp(self):
        super().setUp()
        self.loadout.game = games.ANNO_117
        self.type(document(NOWHERE, LOOP % ((RADIUS % 30) + (TYPE % "Farm"))))

    def values(self) -> dict:
        return {change.tag: change.new_value for change in self.loadout.changes_of("Late")}

    def written(self) -> list:
        """What the file says, however it is laid out."""
        return [source.replace("> <", "><") for source in self.sources()]

    def moved_out(self) -> str:
        """The file as shown, with the loop's last op after its closing tag."""
        lines = self.shown().text.split("\n")
        first = next(at for at, line in enumerate(lines) if "/BuildingType" in line)
        end = next(at for at, line in enumerate(lines) if "</Group>" in line)
        return "\n".join([*lines[:first], lines[end], *lines[first:end], *lines[end + 1:]])

    def test_it_is_one_op_of_the_mod(self):
        (loop,) = self.ops()
        self.assertEqual(self.values(), {"Radius": "30", "BuildingType": "Farm"})
        self.assertEqual(self.written(), [NOWHERE, LOOP % ((RADIUS % 30) + (TYPE % "Farm"))])
        self.assertEqual({change.op_uid for change in self.loadout.changes_of("Late")}, {loop.uid})

    def test_something_typed_elsewhere_leaves_it_the_op_it_was(self):
        (loop,) = self.ops()
        source = loop.source
        done = self.retype("</ModOps>", (RADIUS % 99) + "\n</ModOps>")
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (1, 0, 0, 0))
        self.assertIs(self.loadout.op(loop.uid), loop)
        self.assertEqual(loop.source, source)

    def test_a_value_typed_over_in_it_is_what_the_file_says(self):
        (loop,) = self.ops()
        done = self.retype("<Radius>30</Radius>", "<Radius>31</Radius>")
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 1, 0, 0))
        self.assertEqual(self.values(), {"Radius": "31", "BuildingType": "Farm"})
        self.assertEqual(self.written(), [NOWHERE, LOOP % ((RADIUS % 31) + (TYPE % "Farm"))])
        self.assertEqual([op.uid for op in self.ops()], [loop.uid])
        # And typing nothing after it changes nothing.
        self.assertFalse(self.type(self.shown().text).moved)

    def test_typed_into_it_is_yours_and_the_folders_does_not_come_back(self):
        (loop,) = self.ops()
        # As a mod read from its folder has it.
        loop.authored = False
        source = loop.source
        self.retype("<Radius>30</Radius>", "<Radius>31</Radius>")
        (now,) = self.ops()
        self.assertEqual(now.uid, loop.uid)
        self.assertTrue(now.authored)
        self.assertEqual([entry[:3] for entry in self.loadout.taken_out], [["Late", source, []]])

    def test_an_op_typed_into_it_is_in_what_the_file_says(self):
        (loop,) = self.ops()
        done = self.retype("  </Group>", "    " + (EVERY % 40) + "\n  </Group>")
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (1, 0, 0, 0))
        self.assertEqual([op.uid for op in self.ops()], [loop.uid])
        self.assertEqual(
            self.written(),
            [NOWHERE, LOOP % ((RADIUS % 30) + (TYPE % "Farm") + (EVERY % 40))],
        )

    def test_an_op_taken_out_of_it_is_counted_and_gone(self):
        shown = self.shown()
        lines = shown.text.split("\n")
        first = next(at for at, line in enumerate(lines) if "/BuildingType" in line)
        done = self.type("\n".join(lines[:first] + lines[first + 3:]), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 1, 0))
        self.assertEqual(self.values(), {"Radius": "30"})
        self.assertEqual(self.written(), [NOWHERE, LOOP % (RADIUS % 30)])

    def test_its_tags_taken_out_leave_its_ops_in_no_group(self):
        shown = self.shown()
        lines = [
            line for line in shown.text.split("\n")
            if "<Group" not in line and "</Group>" not in line
        ]
        done = self.type("\n".join(lines), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 0, 1))
        now = self.ops()
        self.assertEqual([op.within for op in now], [[], []])
        self.assertEqual(self.written(), [NOWHERE, RADIUS % 30, TYPE % "Farm"])
        self.assertEqual(self.values(), {"Radius": "30", "BuildingType": "Farm"})
        self.assertEqual(
            {change.op_uid for change in self.loadout.changes_of("Late")},
            {op.uid for op in now},
        )

    def test_an_op_moved_out_of_it_is_an_op_of_its_own(self):
        (loop,) = self.ops()
        shown = self.shown()
        done = self.type(self.moved_out(), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 0, 0))
        self.assertTrue(done.rearranged)
        still, moved = self.ops()
        self.assertEqual(still.uid, loop.uid)
        self.assertEqual(self.written(), [NOWHERE, LOOP % (RADIUS % 30), TYPE % "Farm"])
        self.assertEqual(self.values(), {"Radius": "30", "BuildingType": "Farm"})
        self.assertEqual(
            {change.tag: change.op_uid for change in self.loadout.changes_of("Late")},
            {"Radius": loop.uid, "BuildingType": moved.uid},
        )

    def test_and_moved_back_in_it_is_in_the_loop_again(self):
        (loop,) = self.ops()
        was = self.shown().text
        self.type(self.moved_out())
        done = self.type(was)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 0, 0))
        self.assertEqual([op.uid for op in self.ops()], [loop.uid])
        self.assertEqual(self.written(), [NOWHERE, LOOP % ((RADIUS % 30) + (TYPE % "Farm"))])
        self.assertEqual(self.values(), {"Radius": "30", "BuildingType": "Farm"})

    def test_made_an_ordinary_group_what_was_in_it_is_each_an_op(self):
        done = self.retype(' MaxRepeat="2"', ' Condition="#Early"')
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 0, 1))
        group, first, last = self.ops()
        self.assertTrue(group.scope)
        self.assertEqual((first.within, last.within), ([group.uid], [group.uid]))
        self.assertEqual(self.values(), {"Radius": "30", "BuildingType": "Farm"})

    def test_taken_out_whole_it_is_a_group_and_its_ops(self):
        shown = self.shown()
        lines = shown.text.split("\n")
        first = next(at for at, line in enumerate(lines) if "<Group" in line)
        end = next(at for at, line in enumerate(lines) if "</Group>" in line)
        done = self.type("\n".join(lines[:first] + lines[end + 1:]), shown)
        self.assertEqual((done.added, done.changed, done.dropped, done.groups), (0, 0, 2, 1))
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.values(), {})


class TestTextThatIsNotApplied(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(RADIUS % 20))

    def refused(self, text: str) -> typedops.Read:
        before = self.held()
        found, done = modfile.apply(self.workspace, "Late", text, self.shown())
        self.assertIsNone(done)
        self.assertEqual(self.held(), before)
        return found

    def test_text_that_is_not_xml_says_where_the_reader_stopped(self):
        text = self.shown().text.replace("</Radius>", "</Radios>")
        found = self.refused(text)
        self.assertTrue(found.error)
        self.assertEqual(found.line, 1 + text.split("\n").index(
            next(line for line in text.split("\n") if "Radios" in line)
        ))

    def test_text_loose_in_an_op_is_refused(self):
        found = self.refused(self.shown().text.replace("<Radius>20", "stray <Radius>20"))
        self.assertEqual(found.loose, "stray")

    def test_an_include_on_a_condition_is_refused_for_a_mod_with_no_folder(self):
        """It is followed from the mod's folder, as an import follows it,
        and passed by where there is none: applied, the line would be gone
        without a word.  One that only names a file makes the file: see
        test_mod_files."""
        found = self.refused(self.shown().text.replace(
            "</ModOps>", '<Include File="more.xml" Condition="#Other"/>\n</ModOps>'
        ))
        self.assertEqual(found.refused, modfile.INCLUDE_WITHOUT_A_FOLDER)
        self.assertGreater(found.line, 1)


class TestAnApplyIsAStepToUndo(Filed):
    def setUp(self):
        super().setUp()
        self.type(document(RADIUS % 20, NOWHERE, TYPE % "Farm"))
        self.history = UndoHistory(self.loadout)

    def test_taken_back_everything_stands_where_it_stood(self):
        """An op carried unread among them: an undo step knows one by which
        entry it is, so one moved where it stands stayed moved."""
        before, text = self.held(), self.shown().text
        shown = self.shown()
        first, unread, last = shown.items
        lines = shown.text.split(chr(10))
        moved = (
            lines[:first.first - 1] + lines[unread.first - 1:unread.last] + [""]
            + lines[first.first - 1:first.last] + [""] + lines[last.first - 1:]
        )
        done = self.type(chr(10).join(moved), shown)
        self.assertTrue(done.rearranged)
        self.assertIsNotNone(self.history.commit())
        self.assertEqual(self.loadout.unread_of("Late")[0].order, 0)
        self.assertEqual([item.kind for item in self.shown().items], ["unread", "op", "op"])
        self.history.undo()
        self.assertEqual(self.held(), before)
        self.assertEqual(self.shown().text, text)
        self.history.redo()
        self.assertEqual([item.kind for item in self.shown().items], ["unread", "op", "op"])


class TestYourOwnMod(Filed):
    """The loadout's own mod has a file as any other does."""

    ORIGIN = ""

    def test_typed_from_nothing(self):
        self.assertEqual(self.shown().items, [])
        done = self.type(document(RADIUS % 20, EVERY % 30))
        self.assertEqual(done.added, 2)
        self.assertEqual(len(self.ops()), 2)
        self.assertTrue(all(op.authored for op in self.ops()))
        self.assertFalse(self.type(self.shown().text).moved)


if __name__ == "__main__":
    import unittest

    unittest.main()
