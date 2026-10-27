"""ModOps typed for one asset, read and put into the mod (``core.typedops``).

The XML view's pane for the mod being edited is typed in.  What is typed is
read as an import reads a mod, and the mod's changes are moved to match:
what the text still says stays exactly as it was, an op typed over is its
change retuned in place, and only the rest is new or gone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import RADIUS, StackCase  # noqa: E402
from support import radius_change as radius  # noqa: E402

from anno_modding_studio.core import generator, keptops, typedops, xmlview  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_MERGE,
    OP_REMOVE,
    Change,
    Op,
    Unread,
)

GUID = "1000"


def replace_op(path: str, tag: str, value: str, guid: str = GUID) -> str:
    return '<ModOp Type="replace" GUID="%s" Path="%s"><%s>%s</%s></ModOp>' % (
        guid, path, tag, value, tag,
    )


class Typing(StackCase):
    """Typing into *Late*, which loads after *Early*."""

    ORIGIN = "Late"

    def text(self, guid: str = GUID) -> str:
        """What the pane shows: the mod's own ModOps for the asset."""
        return next(
            turn for turn in xmlview.turns(self.workspace, guid, self.ORIGIN) if turn.editing
        ).modops

    def type(self, text: str, guid: str = GUID):
        found, done = typedops.read_and_apply(
            self.workspace, guid, self.ORIGIN, text, self.text(guid)
        )
        self.assertEqual(found.error, "")
        return done

    def own(self, guid: str = GUID):
        return xmlview.own_changes(self.workspace, guid, self.ORIGIN)


class TestTypingNothingChangesNothing(Typing):
    def setUp(self):
        super().setUp()
        self.loadout.set_change(radius("Early", "10", "11"))
        late = radius("Late", "11", "20")
        late.authored = False
        late.order = 7
        self.loadout.set_change(late)
        self.loadout.set_change(Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", authored=False, order=3,
            payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        ))
        self.loadout.set_change(Change(
            guid=GUID, asset_label="alpha", op=OP_REMOVE, tag="Item", origin="Late",
            path="/Values/ProductList/List/Item[Product='2002']", authored=False, order=5,
        ))

    def test_applying_it_leaves_every_change_as_it_was(self):
        before = {key: vars(change).copy() for key, change in self.loadout.changes.items()}
        done = self.type(self.text())
        self.assertFalse(done.moved)
        self.assertEqual(done.kept, 3)
        after = {key: vars(change) for key, change in self.loadout.changes.items()}
        self.assertEqual(after, before)

    def test_layout_and_comments_do_not_count(self):
        flat = " ".join(
            line.strip() for line in self.text().splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        )
        self.assertFalse(self.type(flat).moved)


class TestAnOpTypedOver(Typing):
    def setUp(self):
        super().setUp()
        change = radius("Late", "10", "20")
        change.authored = False
        change.order = 4
        change.note = "what it was for"
        self.loadout.set_change(change)

    def test_a_note_typed_over_under_an_op_left_alone_is_taken(self):
        done = self.type(self.text().replace("what it was for", "what it is for now"))
        (change,) = self.own()
        self.assertEqual((change.note, change.new_value), ("what it is for now", "20"))
        self.assertEqual((change.order, change.authored), (4, False))
        self.assertTrue(done.moved)

    def test_a_note_taken_out_is_gone(self):
        text = "\n".join(
            line for line in self.text().splitlines() if "what it was for" not in line
        )
        self.type(text)
        (change,) = self.own()
        self.assertEqual(change.note, "")

    def test_the_studios_own_comments_are_not_taken_for_notes(self):
        """They are written afresh each time, and marked as the Studio's.
        Taken for notes, every Apply would turn them into notes about
        themselves."""
        more = replace_op("/Values/Building/BuildingType", "BuildingType", "Farm")
        self.type(self.text().replace("</ModOps>", more + "</ModOps>"))
        again = self.text()
        self.assertEqual(again.count("note:"), 1)
        self.type(again.replace("Farm", "Mine"))
        self.assertEqual(self.text().count("note:"), 1)

    def test_typing_the_value_it_had_takes_the_change_away(self):
        self.type(self.text().replace("<Radius>20</Radius>", "<Radius>10</Radius>"))
        self.assertEqual(self.own(), [])

    def test_an_op_taken_out_is_a_change_taken_out(self):
        done = self.type("<ModOps></ModOps>")
        self.assertEqual(done.dropped, 1)
        self.assertEqual(self.own(), [])

    def test_an_empty_pane_is_a_mod_that_leaves_the_asset_alone(self):
        self.assertEqual(self.type("").dropped, 1)

    def test_another_assets_changes_are_not_touched_by_any_of_it(self):
        other = radius("Late", "4", "5", guid="1001")
        self.loadout.set_change(other)
        self.type("")
        self.assertIn(other.key, self.loadout.changes)


class TestANewOp(Typing):

    def test_ops_need_no_document_around_them(self):
        self.assertEqual(self.type(replace_op(RADIUS, "Radius", "33")).added, 1)
        whole = "<ModOps>%s</ModOps>" % replace_op(RADIUS, "Radius", "33")
        self.assertFalse(self.type(whole).moved)

    def test_text_between_two_ops_is_in_neither_and_is_known_to_be_left_out(self):
        """It is no op's, so the mod holds it nowhere and the pane written
        again has lost it: which is said, where it used to go unsaid."""
        one = replace_op(RADIUS, "Radius", "33")
        two = replace_op("/Values/Building/BuildingType", "BuildingType", "Farm")
        found, done = typedops.read_and_apply(
            self.workspace, GUID, "Late", "%s\nstray\n%s" % (one, two))
        self.assertTrue(found.stray)
        self.assertEqual(done.added, 2)
        self.assertNotIn("stray", self.text())
        self.assertEqual([op.note for op in self.loadout.ops], ["", ""])
        self.assertFalse(typedops.read(self.workspace, GUID, "Late", one + "\n" + two).stray)

    def test_text_in_a_group_is_left_out_too(self):
        op = replace_op(RADIUS, "Radius", "33")
        grouped = '<Group Condition="#Some_Dependency">stray%s</Group>' % op
        self.assertTrue(typedops.read(self.workspace, GUID, "Late", grouped).stray)


class TestTextBesideWhatAnOpHolds(Typing):
    """Text inside a ModOp, beside the elements it holds.  Before the first
    of them it was kept and exported, after one it was dropped without a
    word, and inside an element of the payload it was carried.  It is one
    rule now: none of it is applied, and where it stands is said."""

    MERGE = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
        "%s<Radius>12</Radius>%s<BuildingType>Mine</BuildingType>%s</ModOp>"
    )
    INSIDE = (
        '<ModOp Type="merge" GUID="1000" Path="/Values">'
        "<Building>%s<Radius>12</Radius>%s</Building></ModOp>"
    )

    def refused(self, text: str):
        found, done = typedops.read_and_apply(self.workspace, GUID, "Late", text, self.text())
        self.assertIsNone(done, text)
        self.assertEqual(found.error, "")
        self.assertEqual((self.loadout.ops, self.own()), ([], []))
        return found

    def test_it_is_refused_wherever_among_the_ops_elements_it_stands(self):
        for at in range(3):
            marks = ["", "", ""]
            marks[at] = "loose"
            with self.subTest(at=at):
                self.assertEqual(self.refused(self.MERGE % tuple(marks)).loose, "loose")
        self.assertEqual(self.type(self.MERGE % ("", "", "")).added, 1)

    def test_it_is_refused_among_the_fields_of_an_element_the_op_holds(self):
        self.assertEqual(self.refused(self.INSIDE % ("loose", "")).loose, "loose")
        self.assertEqual(self.refused(self.INSIDE % ("", "loose")).loose, "loose")

    def test_the_line_it_starts_on_is_said_as_the_pane_counts_lines(self):
        """With the blank lines before the ops counted, and none for the
        document the reader puts around ops typed without one."""
        op = self.MERGE % ("", "\n\n  loose\n", "")
        self.assertEqual(self.refused(op).line, 3)
        self.assertEqual(self.refused("\n\n" + op).line, 5)
        self.assertEqual(self.refused("<ModOps>\n%s\n</ModOps>" % op).line, 4)
        before = self.MERGE % ("\nloose\n", "", "")
        self.assertEqual(self.refused(replace_op(RADIUS, "Radius", "33") + "\n" + before).line, 3)

    def test_long_text_is_said_cut_short(self):
        found = self.refused(self.MERGE % ("", "", "word " * 40))
        self.assertEqual(len(found.loose), 40)
        self.assertTrue(found.loose.startswith("word word"))

    def test_text_that_is_all_an_op_holds_is_its_value(self):
        op = '<ModOp Type="add" GUID="1000" Path="/Values/Building/Radius">;7</ModOp>'
        self.type(op)
        self.assertIn(">;7</ModOp>", self.text())

    def test_text_beside_a_value_worked_out_as_the_op_runs_is_part_of_it(self):
        """It is not refused as loose text.  Under a replace the game leaves
        such a mark as written, so the op is carried as typed and no value is
        shown for it."""
        op = (
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            '<Radius>1<ModValue Insert="2+3"/>7</Radius></ModOp>'
        )
        self.type(op)
        self.assertIn('<Radius>1<ModValue Insert="2+3"/>7</Radius>', self.text())
        self.assertEqual([entry.source for entry in self.loadout.unread], [op])
        self.assertEqual(self.own(), [])

    def test_and_typed_over_it_is_another_op(self):
        """Two ops that differ only in such text were taken for one op, so
        typing over it and applying left the mod with the text it had."""
        op = (
            '<ModOp Type="add" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Values/Building/Radius/text()">'
            "<Copied><ModOpContent/>5</Copied></ModOp>"
        )
        self.type(op)
        self.assertEqual([o.source for o in self.loadout.ops if o.origin == "Late"], [op])
        done = self.type(self.text().replace("/>5</Copied>", "/>7</Copied>"))
        self.assertTrue(done.moved)
        (held,) = [o.source for o in self.loadout.ops if o.origin == "Late"]
        self.assertIn("<ModOpContent/>7</Copied>", held)
        self.assertTrue(keptops.same_op(held, op.replace("/>5<", "/>7<")))
        self.assertFalse(self.type(self.text()).moved)

    def test_text_around_a_copy_marked_straight_inside_the_op_is_its_value(self):
        """The copy and the text on both sides are run together into what
        the field says.  Text after the mark was refused as loose text here,
        while the same op read from a mod's files was read."""
        op = (
            '<ModOp Type="add" GUID="1000" Path="/Values/Standard/Name" '
            'Content="@1001/Values/Standard/Name/text()">%s<ModOpContent/>%s</ModOp>'
        )
        for before, after in (("", " after"), ("before ", ""), (" (", ")")):
            with self.subTest(before=before, after=after):
                self.type(op % (before, after))
                self.assertIn("%s<ModOpContent/>%s" % (before, after), self.text())

    def test_but_text_after_a_value_worked_out_directly_in_the_op_is_refused(self):
        op = (
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
            '<ModValue Insert="2+3"/>%s</ModOp>'
        )
        self.assertEqual(self.refused(op % "after").loose, "after")

    def test_comments_are_read_as_they_were(self):
        """Directly inside the op a comment is part of the op as written, as
        one inside an element of it is, and neither is refused."""
        commented = self.MERGE % ("<!--one-->", "<!--two-->", "<!--three-->")
        self.type(commented)
        self.assertEqual(self.loadout.ops[0].source, commented)
        self.assertFalse(self.type(self.text()).moved)
        self.type(self.INSIDE % ("", "<!--kept-->"))
        self.assertIn("<!--kept-->", self.loadout.ops[0].source)

    def test_text_after_a_comment_directly_in_the_op_is_refused(self):
        self.assertEqual(self.refused(self.MERGE % ("<!--c-->loose", "", "")).loose, "loose")

    def test_an_op_the_mod_holds_with_such_text_is_not_refused_for_it(self):
        """An op read from a mod's files is carried as written, and is in the
        pane like that.  Typing another op beside it is not refused for text
        nobody typed, and the op is left exactly as it was."""
        carried = self.INSIDE % ("", ">")
        self.loadout.set_op(Op(
            expression="/Values", source=carried, origin="Late", matched=[GUID], order=1,
        ))
        self.assertIn("&gt;</Building>", self.text())
        more = replace_op("/Values/Standard/Name", "Name", "typed")
        done = self.type(self.text().replace("</ModOps>", more + "</ModOps>"))
        self.assertEqual((done.added, done.dropped), (1, 0))
        self.assertIn(carried, [op.source for op in self.loadout.ops])
        # Typed into another op, or typed differently, it is refused.
        found, done = typedops.read_and_apply(
            self.workspace, GUID, "Late", self.text().replace("&gt;</Building>", "x</Building>"),
            self.text(),
        )
        self.assertIsNone(done)
        self.assertEqual(found.loose, "x")


class TestWhatReachesBeyondTheAsset(Typing):

    def test_a_search_goes_in_as_a_kept_op_with_its_answers(self):
        done = self.type(
            '<ModOp Type="merge" Path="//Values[Building/BuildingType=\'Farm\']/Building">'
            "<Radius>9</Radius></ModOp>"
        )
        self.assertEqual(done.reaching, 1)
        (op,) = self.loadout.ops
        self.assertEqual(op.origin, "Late")
        answers = [change for change in self.loadout.changes.values()
                   if change.op_uid == op.uid]
        self.assertEqual([change.guid for change in answers], ["1001"])


class TestAnAssetTheModCreates(Typing):
    PAYLOAD = ("<Asset><Template>Building</Template><Values><Standard>"
               "<GUID>1337000000</GUID><Name>brand_new</Name></Standard>"
               "<Building><Radius>1</Radius></Building></Values></Asset>")

    def setUp(self):
        super().setUp()
        self.loadout.set_change(Change(
            guid=GUID, asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
            origin="Late", payload=self.PAYLOAD,
        ))

    def test_typing_in_the_asset_it_adds_retunes_the_creation(self):
        text = self.text("1337000000").replace("<Radius>1</Radius>", "<Radius>6</Radius>")
        done = self.type(text, "1337000000")
        self.assertEqual((done.retuned, done.added, done.dropped), (1, 0, 0))
        # The op that adds it is the new asset's own, and is kept as typed.
        (op,) = self.loadout.ops
        self.assertTrue(op.authored)
        self.assertIn("<Radius>6</Radius>", op.source.replace("\n", "").replace(" ", ""))
        self.assertEqual(self.own("1337000000"), [])
        self.assertIn("<Radius>6</Radius>", self.text("1337000000").replace("\n", "").replace(" ", ""))
        self.assertFalse(self.type(self.text("1337000000"), "1337000000").moved)


class TypingTheAsset(Typing):
    """The other pane: the asset as the mod should leave it."""

    def asset(self, guid: str = GUID) -> str:
        return next(
            turn for turn in xmlview.turns(self.workspace, guid, self.ORIGIN) if turn.editing
        ).asset

    def type_asset(self, text: str, guid: str = GUID):
        found, done = typedops.read_asset_and_apply(self.workspace, guid, self.ORIGIN, text)
        self.assertEqual((found.error, found.refused), ("", ""))
        return done

    def refused(self, text: str, guid: str = GUID) -> str:
        found, done = typedops.read_asset_and_apply(self.workspace, guid, self.ORIGIN, text)
        self.assertIsNone(done)
        return found.refused


class TestTypingTheAsset(TypingTheAsset):
    ITEM = "        <Item>\n          <Product>%s</Product>\n        </Item>\n"

    def test_the_asset_as_shown_reads_back_and_applying_it_changes_nothing(self):
        self.type(replace_op(RADIUS, "Radius", "20")
                  + '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
                    "<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item></ModOp>"
                  + '<ModOp Type="remove" GUID="1000" '
                    "Path=\"/Values/ProductList/List/Item[Product='2002']\"/>")
        before = dict(self.loadout.changes)
        self.assertTrue(typedops.asset_reads_back(self.workspace, GUID, "Late", self.asset()))
        self.assertFalse(self.type_asset(self.asset()).moved)
        self.assertEqual(self.loadout.changes, before)

    def test_a_value_typed_over_is_one_narrow_change(self):
        done = self.type_asset(self.asset().replace("<Radius>10</Radius>", "<Radius>25</Radius>"))
        self.assertEqual(done.added, 1)
        (change,) = self.own()
        self.assertEqual((change.path, change.old_value, change.new_value), (RADIUS, "10", "25"))
        self.assertIn('Path="%s"' % RADIUS, self.text())

    def test_it_is_read_against_what_the_mods_before_left(self):
        self.loadout.set_change(radius("Early", "10", "11"))
        self.type_asset(self.asset().replace("<Radius>11</Radius>", "<Radius>25</Radius>"))
        (change,) = self.own()
        self.assertEqual(change.old_value, "11")

    def test_typing_over_a_value_the_mod_set_retunes_that_change_and_keeps_its_note(self):
        change = radius("Late", "10", "20")
        change.authored = False
        change.order = 4
        change.note = "what it was for"
        self.loadout.set_change(change)
        done = self.type_asset(self.asset().replace("<Radius>20</Radius>", "<Radius>25</Radius>"))
        self.assertEqual((done.retuned, done.added), (1, 0))
        (now,) = self.own()
        self.assertEqual((now.new_value, now.order, now.note), ("25", 4, "what it was for"))

    def test_typing_the_value_the_asset_had_takes_the_change_away(self):
        self.loadout.set_change(radius("Late", "10", "20"))
        self.type_asset(self.asset().replace("<Radius>20</Radius>", "<Radius>10</Radius>"))
        self.assertEqual(self.own(), [])

    def test_an_entry_typed_in_is_an_addition_and_one_taken_out_a_removal(self):
        text = self.asset().replace(
            "</Costs>", "<Item><Ingredient>1012</Ingredient><Amount>2</Amount></Item></Costs>"
        ).replace(self.ITEM % "2002", "")
        self.type_asset(text)
        self.assertEqual(sorted(change.op for change in self.own()), ["add", "remove"])
        removal = next(change for change in self.own() if change.op == "remove")
        self.assertEqual(removal.path, "/Values/ProductList/List/Item[Product='2002']")

    def test_layout_does_not_count(self):
        flat = " ".join(line.strip() for line in self.asset().splitlines())
        self.assertFalse(self.type_asset(flat).moved)

    def test_text_between_a_nodes_fields_is_no_change_and_is_known_to_be_left_out(self):
        """After a node's opening tag, or after one of its fields: a node
        with fields in it has no value, so no ModOp is read from that text
        and the asset shown again is without it."""
        for typed in (
            self.asset().replace("<Building>", "<Building>55"),
            self.asset().replace("<Radius>10</Radius>", "<Radius>10</Radius>stray"),
            self.asset().replace(self.ITEM % "2001", (self.ITEM % "2001").replace("<Item>", "<Item>55")),
        ):
            with self.subTest(typed=typed):
                self.assertNotEqual(typed, self.asset())
                found, done = typedops.read_asset_and_apply(self.workspace, GUID, "Late", typed)
                self.assertEqual((found.refused, found.stray), ("", True))
                self.assertFalse(done.moved)
        found = typedops.read_asset(self.workspace, GUID, "Late", self.asset())
        self.assertFalse(found.stray)

    def test_only_what_differs_is_put_to_the_reader(self):
        """An asset is mostly lists padded with entries nothing tells apart.
        A replace of the whole asset has the reader match every one, and it
        says the whole asset would have to be swapped -- for one number."""
        twin = TestTypingTheAsset.ITEM % "2001"
        padded = '<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">%s</ModOp>'
        self.loadout.unread.clear()
        early = Change(
            guid=GUID, asset_label="alpha", path="/Values/ProductList/List", tag="Item",
            op=OP_ADD, origin="Early", payload="<Item><Product>2001</Product></Item>",
        )
        self.loadout.set_change(early)
        shown = self.asset()
        self.assertEqual(shown.count(twin), 2, padded)
        found = typedops.read_asset(self.workspace, GUID, "Late", shown)
        self.assertEqual((found.refused, found.ops), ("", 0))
        done = self.type_asset(shown.replace("<Radius>10</Radius>", "<Radius>25</Radius>"))
        self.assertEqual(done.added, 1)
        self.assertEqual(self.own()[0].path, RADIUS)

    PLACEHOLDER = "<Item>\n          <Ingredient>1012</Ingredient>\n        </Item>\n"

    def test_a_placeholder_taken_out_by_hand_is_a_removal_like_any_other(self):
        """A cost entry with no amount is padding, and a mod's wide replace
        that leaves one out is not read as removing it. Here the entry was on
        show and its lines were taken out: that was asked for. Left
        unwritten, the entry came back as though nothing had been typed,
        while Remove from asset in the Fields view wrote the removal."""
        shown = self.asset("1002")
        self.assertIn(self.PLACEHOLDER, shown)
        found, done = typedops.read_asset_and_apply(
            self.workspace, "1002", "Late", shown.replace(self.PLACEHOLDER, ""))
        self.assertEqual((found.refused, done.added), ("", 1))
        (removal,) = self.own("1002")
        self.assertEqual(
            (removal.op, removal.path), ("remove", "/Values/Cost/Costs/Item[Ingredient='1012']"))
        self.assertNotIn(self.PLACEHOLDER, self.asset("1002"))

    def test_and_it_is_counted_as_one_the_game_may_not_notice(self):
        shown = self.asset("1002")
        found = typedops.read_asset(
            self.workspace, "1002", "Late", shown.replace(self.PLACEHOLDER, ""))
        self.assertEqual(found.unnoticed, 1)
        # Once: the asset as it then stands says nothing new.
        typedops.apply(self.workspace, "1002", "Late", found, notes=False)
        again = typedops.read_asset(self.workspace, "1002", "Late", self.asset("1002"))
        self.assertEqual((again.unnoticed, len(again.changes)), (0, 1))
        self.assertFalse(self.type_asset(self.asset("1002"), "1002").moved)

    def test_an_entry_that_sets_something_is_not_counted_so(self):
        """1011 costs 7, and a product list's entries are the entries: taking
        either out is something the game notices."""
        costs = ("<Item>\n          <Ingredient>1011</Ingredient>\n"
                 "          <Amount>7</Amount>\n        </Item>\n")
        shown = self.asset("1002")
        self.assertIn(costs, shown)
        found = typedops.read_asset(
            self.workspace, "1002", "Late",
            shown.replace(costs, "").replace(self.ITEM % "2002", ""))
        self.assertEqual(sorted(change.op for change in found.changes), ["remove", "remove"])
        self.assertEqual(found.unnoticed, 0)

    def test_the_same_removal_made_in_fields_reads_back_from_the_asset(self):
        """Remove from asset writes it. The asset as then shown has to come
        back as that removal, or typing in it is only ever laid over."""
        self.loadout.set_change(Change(
            guid="1002", asset_label="gamma", op=OP_REMOVE, tag="Item", origin="Late",
            path="/Values/Cost/Costs/Item[Ingredient='1012']",
        ))
        shown = self.asset("1002")
        self.assertNotIn(self.PLACEHOLDER, shown)
        self.assertTrue(typedops.asset_reads_back(self.workspace, "1002", "Late", shown))
        self.assertEqual(typedops.read_asset(self.workspace, "1002", "Late", shown).unnoticed, 0)

    def test_what_a_pattern_of_the_mod_does_is_not_written_again(self):
        op = Op(
            expression="//Values[Building]/Building", origin="Late",
            source='<ModOp Type="merge" Path="//Values[Building]/Building">'
                   "<Radius>9</Radius></ModOp>",
            matched=["1000", "1001"],
        )
        self.loadout.set_op(op)
        answer = radius("Late", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        self.assertFalse(self.type_asset(self.asset()).moved)
        self.type_asset(self.asset().replace("<Radius>9</Radius>", "<Radius>14</Radius>"))
        (exception,) = self.own()
        self.assertEqual((exception.old_value, exception.exception_of), ("9", op.uid))

    def test_what_an_op_kept_as_written_does_is_not_written_again(self):
        self.type('<ModOp Type="merge" GUID="1000" Path="/Values/Building" KeepAsWritten="1">'
                  "<Radius>12</Radius></ModOp>")
        (op,) = self.loadout.ops
        done = self.type_asset(self.asset())
        self.assertFalse(done.moved)
        self.assertEqual((self.own(), self.loadout.ops), ([], [op]))


class TestTypingOverAnAssetThatDoesNotComeBackAsItsChanges(TypingTheAsset):
    """Where the asset as shown does not read as the changes the mod has --
    here a field it makes sure of, which reads back as a field added -- what
    is typed is read over the asset as shown, as an edit in the tree is: laid
    over the mod's changes, and none of them taken away."""

    def setUp(self):
        super().setUp()
        self.ensure = Change(
            guid=GUID, asset_label="alpha", path="/Values/Building", tag="Range",
            op=OP_MERGE, origin="Late", payload="<Range>3</Range>", authored=False, order=2,
        )
        self.loadout.set_change(self.ensure)
        change = radius("Late", "10", "20")
        change.authored = False
        change.order = 4
        change.note = "what it was for"
        self.loadout.set_change(change)
        self.radius = change

    def test_it_does_not_come_back_as_its_changes(self):
        self.assertIn("<Range>3</Range>", self.asset())
        self.assertFalse(typedops.asset_reads_back(self.workspace, GUID, "Late", self.asset()))

    def test_which_way_it_is_read_is_what_asset_reads_back_says(self):
        """The rule was written twice, once here and once in read_asset, and
        the tests asked the copy only."""
        from unittest import mock

        text = self.asset().replace("<Radius>20</Radius>", "<Radius>25</Radius>")
        self.assertTrue(typedops.read_asset(self.workspace, GUID, "Late", text).over)
        with mock.patch.object(typedops, "asset_reads_back", return_value=True) as asked:
            self.assertFalse(typedops.read_asset(self.workspace, GUID, "Late", text).over)
        asked.assert_called_once()

    def test_typing_nothing_changes_nothing(self):
        before = {key: vars(change).copy() for key, change in self.loadout.changes.items()}
        done = self.type_asset(self.asset())
        self.assertFalse(done.moved)
        self.assertEqual(
            {key: vars(change) for key, change in self.loadout.changes.items()}, before)

    def test_a_value_typed_elsewhere_is_one_more_change_and_the_rest_stay(self):
        text = self.asset().replace("<BuildingType>Factory</BuildingType>",
                                    "<BuildingType>Farm</BuildingType>")
        found, done = typedops.read_asset_and_apply(self.workspace, GUID, "Late", text)
        self.assertTrue(found.over)
        self.assertEqual((done.added, done.retuned, done.dropped), (1, 0, 0))
        self.assertIn(self.ensure.key, self.loadout.changes)
        self.assertIn(self.radius.key, self.loadout.changes)
        self.assertEqual(len(self.own()), 3)

    def test_typing_over_a_value_the_mod_set_retunes_that_change(self):
        done = self.type_asset(self.asset().replace("<Radius>20</Radius>", "<Radius>25</Radius>"))
        self.assertEqual((done.added, done.retuned), (0, 1))
        now = self.loadout.changes[self.radius.key]
        # What it replaced is still what the game had, and its note stays.
        self.assertEqual((now.old_value, now.new_value, now.order, now.note),
                         ("10", "25", 4, "what it was for"))

    def test_typing_the_value_the_asset_had_takes_that_change_away(self):
        self.type_asset(self.asset().replace("<Radius>20</Radius>", "<Radius>10</Radius>"))
        self.assertNotIn(self.radius.key, self.loadout.changes)
        self.assertIn(self.ensure.key, self.loadout.changes)

    def test_an_entry_the_mod_added_and_you_took_out_is_a_removal_after_it(self):
        added = Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        )
        self.loadout.set_change(added)
        entry = ("        <Item>\n          <Ingredient>1012</Ingredient>\n"
                 "          <Amount>3</Amount>\n        </Item>\n")
        shown = self.asset()
        self.assertIn(entry, shown)
        self.type_asset(shown.replace(entry, ""))
        self.assertIn(added.key, self.loadout.changes)
        self.assertNotIn("<Ingredient>1012</Ingredient>", self.asset())


class TestAnAssetThatCannotBeApplied(TypingTheAsset):
    def test_text_that_is_not_xml_says_where(self):
        found, done = typedops.read_asset_and_apply(
            self.workspace, GUID, "Late", self.asset().replace("</Radius>", "", 1))
        self.assertIsNone(done)
        self.assertTrue(found.error)
        self.assertGreater(found.line, 1)

    def test_blank_lines_before_the_asset_are_counted_in_the_line_it_names(self):
        broken = self.asset().replace("</Radius>", "", 1)
        at = typedops.read_asset_and_apply(self.workspace, GUID, "Late", broken)[0]
        lower = typedops.read_asset_and_apply(
            self.workspace, GUID, "Late", "\n\n\n" + broken)[0]
        self.assertEqual((lower.line, lower.column), (at.line + 3, at.column))

    def test_something_that_is_not_an_asset_is_refused(self):
        self.assertEqual(self.refused("<Values/>"), typedops.NOT_AN_ASSET)

    def test_another_assets_guid_is_refused(self):
        text = self.asset().replace("<GUID>1000</GUID>", "<GUID>1001</GUID>")
        self.assertEqual(self.refused(text), typedops.ANOTHER_GUID)

    def test_a_change_beside_values_is_refused(self):
        text = self.asset().replace("<Template>Building</Template>", "<Template>Farm</Template>")
        self.assertEqual(self.refused(text), typedops.OUTSIDE_VALUES)

    def test_a_change_with_no_narrow_form_is_refused_with_the_readers_reason(self):
        """A list given two entries nothing tells apart: the reader could only
        swap the whole list, and would keep a replace of the whole asset."""
        item = TestTypingTheAsset.ITEM % "2001"
        why = self.refused(self.asset().replace(item, item + item))
        self.assertIn("nothing unique", why)
        self.assertEqual((self.own(), self.loadout.ops), ([], []))

    def test_the_reason_is_about_the_list_and_says_which(self):
        """The reader's reason for keeping such an op in a mod goes on to say
        what is shown in its place. Here nothing is, and none of what was
        typed is applied: not the value typed beside the list either."""
        item = TestTypingTheAsset.ITEM % "2001"
        typed = self.asset().replace(item, item + item).replace(
            "<Radius>10</Radius>", "<Radius>25</Radius>")
        found, done = typedops.read_asset_and_apply(self.workspace, GUID, "Late", typed)
        self.assertIsNone(done)
        self.assertEqual(
            found.refused, "its 4 <Item> entries have nothing unique to match them on")
        self.assertEqual(found.refused_at, "/Values/ProductList/List")
        self.assertEqual((self.own(), self.loadout.ops), ([], []))
        # And the list is found again in what was typed, by that path.
        line = typedops.line_typed(typed, found.refused_at)
        self.assertEqual(typed.splitlines()[line - 1].strip(), "<List>")

    def test_the_list_is_named_and_not_the_node_over_it_that_was_read(self):
        """A node that gained a field is read whole, and the list that
        cannot be matched is somewhere in it."""
        item = TestTypingTheAsset.ITEM % "2001"
        typed = self.asset().replace(item, item + item).replace(
            "</List>", "</List>\n      <Sorted>1</Sorted>")
        found = typedops.read_asset(self.workspace, GUID, "Late", typed)
        self.assertIn("nothing unique", found.refused)
        self.assertEqual(found.refused_at, "/Values/ProductList/List")

    def test_one_the_reader_leaves_unread_is_found_by_its_path_whatever_is_carried(self):
        """Only the replaces written for what differs are put to the reader,
        so a refusal for one left unread names that replace's path -- not
        the search of an op some mod carries unread for the asset, which
        would find no line in what was typed."""
        search = ('<ModOp Type="replace" '
                  "Path=\"//Asset[Values/Standard/GUID='1000']/Values/Standard/Name\">"
                  "<Name>x</Name></ModOp>")
        typed = self.asset().replace("<Radius>10</Radius>",
                                     "<Radius><ModItem>1</ModItem></Radius>")
        for origin in ("Early", "Late"):
            with self.subTest(carried_by=origin):
                self.loadout.unread[:] = [
                    Unread(origin=origin, order=1, label="x", why="unread", source=search)]
                found, done = typedops.read_asset_and_apply(self.workspace, GUID, "Late", typed)
                self.assertIsNone(done)
                self.assertIn("ModItem", found.refused)
                self.assertEqual(found.refused_at, RADIUS)
                line = typedops.line_typed(typed, found.refused_at)
                self.assertIn("<ModItem>", typed.splitlines()[line - 1])


class TestTypingAnAssetTheModAdds(TypingTheAsset):
    PAYLOAD = ("<Asset><Template>Building</Template><Values><Standard>"
               "<GUID>1337000000</GUID><Name>brand_new</Name></Standard>"
               "<Building><Radius>1</Radius></Building></Values></Asset>")
    NEW = "1337000000"

    def setUp(self):
        super().setUp()
        self.loadout.set_change(Change(
            guid=GUID, asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
            origin="Late", payload=self.PAYLOAD,
        ))

    def test_the_asset_as_shown_reads_back(self):
        self.assertTrue(
            typedops.asset_reads_back(self.workspace, self.NEW, "Late", self.asset(self.NEW)))

    def test_the_typed_asset_is_the_one_the_mod_adds(self):
        text = self.asset(self.NEW).replace("<Radius>1</Radius>", "<Radius>6</Radius>")
        done = self.type_asset(text, self.NEW)
        self.assertEqual((done.retuned, done.added, done.dropped), (1, 0, 0))
        (creator,) = self.own(self.NEW)
        self.assertIn("<Radius>6</Radius>", creator.payload)
        self.assertIn("<Radius>6</Radius>", self.asset(self.NEW))

    def test_text_typed_between_its_fields_is_in_the_op_that_adds_it(self):
        """The typed asset is that op's payload, text and all: it is not
        left out, so it is not said to be."""
        text = self.asset(self.NEW).replace("<Building>", "<Building>55")
        found, _done = typedops.read_asset_and_apply(self.workspace, self.NEW, "Late", text)
        self.assertFalse(found.stray)
        self.assertIn("<Building>55", self.own(self.NEW)[0].payload)

    def test_one_the_mod_adds_and_then_changes_keeps_both_ops(self):
        """The op that adds it stays as it is, and what is typed is read as
        the changes the mod makes after it."""
        after = radius("Late", "1", "4", guid=self.NEW)
        self.loadout.set_change(after)
        shown = self.asset(self.NEW)
        self.assertIn("<Radius>4</Radius>", shown)
        self.assertTrue(typedops.asset_reads_back(self.workspace, self.NEW, "Late", shown))
        done = self.type_asset(shown.replace("<Radius>4</Radius>", "<Radius>9</Radius>"), self.NEW)
        self.assertEqual((done.retuned, done.added, done.dropped), (1, 0, 0))
        creator = next(change for change in self.own(self.NEW) if change.creates_asset)
        self.assertIn("<Radius>1</Radius>", creator.payload)
        self.assertEqual(self.loadout.changes[after.key].new_value, "9")

    def test_one_a_pattern_of_the_mod_changes_is_not_typed_into_the_op_that_adds_it(self):
        """Typing nothing must change nothing: what the pattern does is in the
        asset as shown, and must not end up in the op that adds the asset."""
        op = Op(
            expression="//Values[Building]/Building", origin="Late",
            source='<ModOp Type="merge" Path="//Values[Building]/Building">'
                   "<Radius>9</Radius></ModOp>",
            matched=[self.NEW],
        )
        self.loadout.set_op(op)
        answer = radius("Late", "1", "9", guid=self.NEW)
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        shown = self.asset(self.NEW)
        self.assertIn("<Radius>9</Radius>", shown)
        before = self.own(self.NEW)[0].payload
        self.assertFalse(self.type_asset(shown, self.NEW).moved)
        self.assertEqual(self.own(self.NEW)[0].payload, before)

    def test_typing_nothing_leaves_the_op_that_adds_it_letter_for_letter(self):
        creator = self.own(self.NEW)[0]
        before = creator.payload
        self.assertFalse(self.type_asset(self.asset(self.NEW), self.NEW).moved)
        self.assertIs(self.own(self.NEW)[0], creator)
        self.assertEqual(creator.payload, before)

    def test_one_added_by_an_op_kept_as_written_is_not_made_a_change_of_its_own(self):
        """The op that adds it is the kept op's answer, not the pane's.
        Typing nothing must not write the asset a second time."""
        creator = self.own(self.NEW)[0]
        op = Op(expression="@1000", origin="Late", kept="it adds a group of assets",
                    source='<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self.PAYLOAD,
                    matched=[GUID])
        self.loadout.set_op(op)
        creator.op_uid = op.uid
        self.loadout.reindex()
        self.assertEqual(self.own(self.NEW), [])
        before = dict(self.loadout.changes)
        self.assertFalse(self.type_asset(self.asset(self.NEW), self.NEW).moved)
        self.assertEqual(self.loadout.changes, before)
        self.type_asset(
            self.asset(self.NEW).replace("<Radius>1</Radius>", "<Radius>6</Radius>"), self.NEW)
        (change,) = self.own(self.NEW)
        self.assertEqual((change.path, change.new_value), (RADIUS, "6"))
        self.assertIn("<Radius>1</Radius>", self.loadout.changes[creator.key].payload)

    def test_its_template_can_be_typed_over_too(self):
        text = self.asset(self.NEW).replace("<Template>Building</Template>",
                                            "<Template>Farm</Template>")
        self.type_asset(text, self.NEW)
        self.assertIn("<Template>Farm</Template>", self.own(self.NEW)[0].payload)


class TestTextThatCannotBeRead(Typing):
    def test_text_that_is_not_xml_says_where_and_changes_nothing(self):
        self.loadout.set_change(radius("Late", "10", "20"))
        found, done = typedops.read_and_apply(
            self.workspace, GUID, "Late", "<ModOps>\n  <ModOp Type='replace'\n</ModOps>"
        )
        self.assertIsNone(done)
        self.assertTrue(found.error)
        self.assertEqual(found.line, 3)
        self.assertEqual(len(self.own()), 1)

    def test_blank_lines_before_it_are_counted_in_the_line_it_names(self):
        """The pane counts every line. The reader does not see the blank ones
        at the top, and named a line that many too high up."""
        broken = "<ModOps>\n  <ModOp Type='replace'\n</ModOps>"
        found = typedops.read(self.workspace, GUID, "Late", "\n \n\n" + broken)
        self.assertEqual(found.line, 6)
        # Ops with no document around them, where the reader is given a line more.
        bare = "<ModOp Type='replace' GUID='1000' Path='/Values/X'>\n  <X>\n</ModOp>"
        at = typedops.read(self.workspace, GUID, "Late", bare)
        self.assertEqual(at.line, 3)
        self.assertEqual(typedops.read(self.workspace, GUID, "Late", "\n\n" + bare).line, 5)

    def test_and_in_the_line_of_a_document_that_is_not_modops(self):
        found = typedops.read(self.workspace, GUID, "Late", "\n\n<ModOps2><ModOps/></ModOps2>")
        self.assertIn("ModOps", found.error)
        self.assertEqual(found.line, 3)

    def test_a_document_that_is_not_modops_is_refused(self):
        found, done = typedops.read_and_apply(self.workspace, GUID, "Late", "<ModOps2/>")
        self.assertIsNone(done)
        self.assertIn("ModOps", found.error)

    NOWHERE = '<ModOp Type="replace" GUID="1000" Path="/Values/Nowhere"><X/></ModOp>'

    def test_an_op_the_reader_cannot_follow_is_carried_as_typed_and_stays(self):
        done = self.type(self.NOWHERE)
        self.assertEqual((done.carried, done.carried_new, done.unread), (1, 1, 0))
        self.assertTrue(done.why_unread[0])
        (entry,) = self.loadout.unread
        self.assertEqual(entry.origin, "Late")
        self.assertIn("/Values/Nowhere", entry.source)
        # It names this asset and no other, so it is still in the pane.
        self.assertIn("/Values/Nowhere", self.text())
        self.assertFalse(self.type(self.text()).moved)

    def test_and_taken_out_of_the_text_it_is_carried_no_more(self):
        self.type(self.NOWHERE)
        self.type("")
        self.assertEqual(self.loadout.unread, [])

    def test_one_naming_two_assets_is_carried_and_in_the_pane_as_one_beyond_it(self):
        done = self.type(self.NOWHERE.replace('GUID="1000"', 'GUID="1000,1001"'))
        self.assertEqual((done.carried, done.unread, len(done.why_unread)), (0, 0, 1))
        self.assertIn("/Values/Nowhere", self.text())
        # Typed back as it stands, it is the op carried, and nothing moves.
        self.assertFalse(self.type(self.text()).moved)
        self.assertEqual(len(self.loadout.unread), 1)
        # And taken out of the text it is carried no more.
        self.type("")
        self.assertEqual(self.loadout.unread, [])


if __name__ == "__main__":
    import unittest

    unittest.main()


# ------------------------------------------- read with every op kept as written
WIDE = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
    "<Radius>30</Radius><BuildingType>Farm</BuildingType></ModOp>"
)


class TypingAsWritten(Typing):
    """The same pane where what is typed is what is stored
    (docs/proposals/ops-as-written-proposal.md, phase 3)."""

    def setUp(self):
        super().setUp()

    def kept(self, source: str, order: int = 2, **more) -> Op:
        """An op of *Late* as a mod read as written has it, with what it
        comes to under it."""
        found = typedops.read(self.workspace, GUID, "Late", source)
        (op,) = found.own_ops + found.reaching
        op.order = order
        for name, value in more.items():
            setattr(op, name, value)
        self.loadout.set_op(op)
        for answer in found.answers:
            answer.order = order
            self.loadout.set_change(answer)
        return op

    def ops(self):
        return [op for op in self.loadout.ops if op.origin == "Late"]

    def frozen(self):
        return (
            {key: vars(change).copy() for key, change in self.loadout.changes.items()},
            [vars(op).copy() for op in self.loadout.ops],
        )


class TestWhatIsTypedIsWhatIsStored(TypingAsWritten):
    def test_an_op_is_kept_as_typed_and_not_one_for_each_field(self):
        done = self.type(WIDE)
        (op,) = self.ops()
        self.assertTrue(keptops.same_op(op.source, WIDE))
        self.assertEqual(done.added, 1)
        under = [c for c in self.loadout.changes.values() if c.op_uid == op.uid]
        self.assertEqual(sorted(c.new_value for c in under), ["30", "Farm"])
        self.assertEqual(self.own(), [])
        # And it is in the pane as it was typed: one op.
        self.assertEqual(self.text().count("<ModOp "), 1)
        self.assertIn("Farm", self.text())

    def test_it_is_work_whether_or_not_it_comes_to_anything(self):
        self.assertEqual(self.loadout.authored_count, 0)
        self.type(replace_op("/Values/Building/Radius", "Radius", "10"))
        (op,) = self.ops()
        self.assertTrue(op.authored)
        self.assertEqual([c for c in self.loadout.changes.values() if c.op_uid == op.uid], [])
        self.assertEqual(self.loadout.authored_count, 1)
        # And reading its mod's folder again does not take it away.
        self.loadout.drop_layer("Late", keep_authored=True)
        self.assertEqual(self.loadout.ops, [op])


class TestTypingNothingAsWritten(TypingAsWritten):
    def setUp(self):
        super().setUp()
        self.op = self.kept(WIDE, order=4)
        self.loadout.set_change(Change(
            guid=GUID, asset_label="alpha", path="/Values/Cost/Costs", tag="Item", op=OP_ADD,
            origin="Late", order=6,
            payload="<Item><Ingredient>1012</Ingredient><Amount>3</Amount></Item>",
        ))

    def test_the_pane_holds_the_op_and_the_studios_own_change(self):
        text = self.text()
        self.assertIn('Type="merge"', text)
        self.assertIn("<Ingredient>1012</Ingredient>", text)

    def test_applying_it_leaves_every_op_and_change_as_it_was(self):
        before = self.frozen()
        done = self.type(self.text())
        self.assertFalse(done.moved)
        self.assertEqual(done.kept, 2)
        self.assertEqual(self.frozen(), before)

    def test_the_studios_own_change_is_still_a_change_and_not_an_op(self):
        self.type(self.text())
        self.assertEqual(len(self.ops()), 1)
        self.assertEqual(len(self.own()), 1)

    def test_layout_does_not_count(self):
        flat = " ".join(
            line.strip() for line in self.text().splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        )
        before = self.frozen()
        self.assertFalse(self.type(flat).moved)
        self.assertEqual(self.frozen(), before)


class TestTypingNothingIntoAModThatIsNotLoading(TypingAsWritten):
    """The mod being edited can be switched off.  Its pane was filled without
    its own kept and unread ops, which applying compares the text against:
    applying the pane as shown took both out of the mod."""

    NOWHERE = '<ModOp Type="replace" GUID="1000" Path="/Values/No"><X/></ModOp>'

    def setUp(self):
        super().setUp()
        from dataclasses import replace as replaced

        self.op = self.kept(WIDE, order=4)
        self.nowhere = Unread(origin="Late", order=6, label="x", why="no such node",
                              source=self.NOWHERE)
        self.loadout.unread.append(self.nowhere)
        self.loadout.set_layer(replaced(self.loadout.layer("Late"), active=False))
        self.assertIn("Late", self.loadout.not_loading())

    def test_the_pane_holds_them(self):
        (turn,) = [turn for turn in xmlview.turns(self.workspace, GUID, "Late") if turn.editing]
        self.assertEqual((turn.own_ops, turn.own_unread), ([self.op], [self.nowhere]))
        self.assertIn('Type="merge"', turn.modops)
        self.assertIn("/Values/No", turn.modops)

    def test_applying_it_leaves_them_in_the_mod(self):
        done = self.type(self.text())
        self.assertFalse(done.moved)
        self.assertEqual((self.ops(), self.loadout.unread), ([self.op], [self.nowhere]))


class TestAnOpTypedOverAsWritten(TypingAsWritten):
    def setUp(self):
        super().setUp()
        self.first = self.kept(replace_op("/Values/Building/BuildingType", "BuildingType", "Farm"), order=3)
        self.op = self.kept(WIDE.replace("<BuildingType>Farm</BuildingType>", ""), order=5)

    def test_it_takes_the_place_of_the_one_it_was_typed_over(self):
        done = self.type(self.text().replace("<Radius>30</Radius>", "<Radius>44</Radius>"))
        self.assertEqual((done.retuned, done.added, done.dropped), (1, 0, 0))
        first, second = sorted(self.ops(), key=lambda op: op.order)
        self.assertIs(first, self.first)
        self.assertEqual(second.order, 5)
        self.assertIn("44", second.source)
        (under,) = [c for c in self.loadout.changes.values() if c.op_uid == second.uid]
        self.assertEqual(under.new_value, "44")
        self.assertIsNone(self.loadout.op(self.op.uid))

    def test_the_one_not_touched_is_the_op_it_was(self):
        before = vars(self.first).copy()
        self.type(self.text().replace("<Radius>30</Radius>", "<Radius>44</Radius>"))
        self.assertEqual(vars(self.first), before)

    def test_one_taken_out_is_gone_with_what_it_came_to(self):
        text = self.text()
        start = text.index('<ModOp Type="merge"')
        done = self.type(text[:start] + text[text.index("</ModOp>", start) + len("</ModOp>"):])
        self.assertEqual(done.dropped, 1)
        self.assertEqual(self.ops(), [self.first])
        self.assertEqual(
            [c.new_value for c in self.loadout.changes.values() if c.origin == "Late"], ["Farm"]
        )

    def test_every_one_taken_out_is_counted_as_taken_out(self):
        """An emptied pane has no op to say which way it is read, and was
        read the other way: the ops went, and none was counted as gone."""
        done = self.type("")
        self.assertEqual(done.dropped, 2)
        self.assertEqual(self.ops(), [])
        self.assertEqual([c for c in self.loadout.changes.values() if c.origin == "Late"], [])

    def test_a_new_one_goes_after_the_mods_ops(self):
        added = replace_op("/Values/Building/Radius", "Radius", "50")
        done = self.type(self.text().replace("</ModOps>", added + "</ModOps>"))
        self.assertEqual((done.kept, done.added), (2, 1))
        newest = max(self.ops(), key=lambda op: op.order)
        self.assertTrue(keptops.same_op(newest.source, added))
        self.assertGreater(newest.order, 5)

    def test_a_comment_typed_above_an_op_is_its_note(self):
        text = self.text().replace('<ModOp Type="merge"', '<!-- wider -->\n  <ModOp Type="merge"')
        done = self.type(text)
        self.assertEqual(self.op.note, "wider")
        self.assertEqual(done.retuned, 1)
        self.assertIsNotNone(self.loadout.op(self.op.uid))


class TestAChangeOfTheStudiosTypedOver(TypingAsWritten):
    def setUp(self):
        super().setUp()
        change = radius("Late", "10", "20")
        change.order = 4
        self.loadout.set_change(change)

    def test_it_becomes_the_op_that_was_typed_where_the_change_was(self):
        done = self.type(self.text().replace(">20<", ">21<"))
        self.assertEqual((done.retuned, done.added, done.dropped), (1, 0, 0))
        (op,) = self.ops()
        self.assertEqual(op.order, 4)
        (change,) = [c for c in self.loadout.changes.values() if c.origin == "Late"]
        self.assertEqual((change.new_value, change.op_uid), ("21", op.uid))

    def test_taken_out_it_is_gone(self):
        done = self.type("")
        self.assertEqual(done.dropped, 1)
        self.assertEqual([c for c in self.loadout.changes.values() if c.origin == "Late"], [])


class TestAssetsTheStudioAddsSideBySide(TypingAsWritten):
    """Two assets added beside the same one are written as one ModOp, and
    that op is what the pane shows for both."""

    PAYLOAD = ("<Asset><BaseAssetGUID>1000</BaseAssetGUID><Values><Standard>"
               "<GUID>%s</GUID><Name>made_%s</Name></Standard></Values></Asset>")

    def setUp(self):
        super().setUp()
        for guid, name in (("1337000000", "First"), ("1337000001", "Second")):
            self.loadout.set_change(Change(
                guid=GUID, asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                origin="Late", payload=self.PAYLOAD % (guid, guid), in_game_name=name,
                authored=True,
            ))

    def named(self):
        return sorted(
            change.in_game_name for change in self.loadout.changes.values()
            if change.origin == "Late" and change.creates_asset
        )

    def test_the_pane_shows_the_two_as_one_op(self):
        self.assertEqual(self.text().count("<ModOp "), 1)
        self.assertEqual(self.text().count("<Asset>"), 2)

    def test_typed_back_as_that_op_they_are_those_changes_still(self):
        """It was matched to neither, so both were taken out and it went in
        as an op typed here: at every Apply of anything typed beside it."""
        before = self.frozen()
        done = self.type(self.text())
        self.assertEqual((done.added, done.retuned, done.dropped), (0, 0, 0))
        self.assertEqual(self.frozen(), before)
        self.assertEqual(self.ops(), [])

    def test_and_an_op_typed_beside_it_leaves_them_alone(self):
        typed = self.text().replace(
            "</ModOps>", replace_op("/Values/Building/Radius", "Radius", "12") + "</ModOps>"
        )
        done = self.type(typed)
        self.assertEqual((done.added, done.dropped), (1, 0))
        self.assertEqual(len(self.ops()), 1)
        self.assertEqual(self.named(), ["First", "Second"])
        self.assertEqual(len(self.own()), 2)

    def test_typed_over_they_keep_what_players_see_them_called(self):
        """The name is on the change and in no op, so an op typed over the
        change that adds an asset took the asset's name with it, and the
        next export wrote the mod's texts files without it."""
        self.type(self.text().replace("made_1337000001", "renamed"))
        (op,) = self.ops()
        self.assertIn("renamed", op.source)
        self.assertEqual(self.named(), ["First", "Second"])

    def test_two_such_ops_beside_the_same_asset_are_each_their_changes_still(self):
        """Two more put beside it at another place in the mod are another op.
        The four were taken for one run, written as no one op, so neither op
        was known: all four changes went, and the two ops went in typed here."""
        for guid, name in (("1337000002", "Third"), ("1337000003", "Fourth")):
            self.loadout.set_change(Change(
                guid=GUID, asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                origin="Late", payload=self.PAYLOAD % (guid, guid), in_game_name=name,
                authored=True, order=5,
            ))
        self.assertEqual(self.text().count("<ModOp "), 2)
        before = self.frozen()
        done = self.type(self.text())
        self.assertEqual((done.added, done.retuned, done.dropped), (0, 0, 0))
        self.assertEqual(self.frozen(), before)
        self.assertEqual(self.ops(), [])


BOTH = WIDE.replace('GUID="1000"', 'GUID="1000,1001"')
SEARCH = '<ModOp Type="merge" Path="//Values[Building]/Building"><Radius>9</Radius></ModOp>'


class TestWhatIsNotTheAssetsAlone(TypingAsWritten):
    """Phase 2 of the file tab proposal: an op of the mod that reaches the
    asset and is not its own is in the pane, and typed over there."""

    def turn(self):
        return next(t for t in xmlview.turns(self.workspace, GUID, "Late") if t.editing)

    def group(self, idle: bool = False) -> Op:
        group = Op(source='<Group Condition="#Some_Mod"></Group>', origin="Late", scope=True,
                   order=1, idle=idle)
        self.loadout.set_op(group)
        return group

    def test_an_op_in_a_group_is_in_the_pane_and_not_the_assets_own(self):
        op = self.kept(WIDE, within=[self.group().uid])
        turn = self.turn()
        self.assertEqual(turn.own_ops, [])
        self.assertEqual([item.op for item in turn.beyond], [op])
        self.assertEqual(self.text().count("<ModOp "), 1)

    def test_left_as_it_is_in_the_text_it_is_left_as_it_is(self):
        op = self.kept(WIDE, within=[self.group().uid])
        before = self.frozen()
        typed = self.text().replace(
            "</ModOps>", replace_op("/Values/Building/Radius", "Radius", "50") + "</ModOps>"
        )
        done = self.type(typed)
        self.assertEqual(done.added, 1)
        self.assertIs(self.loadout.op(op.uid), op)
        self.assertEqual(vars(op), before[1][1])
        self.assertEqual(len([o for o in self.ops() if not o.scope]), 2)

    def test_it_is_not_read_again(self):
        """What it comes to was read where the mod stands, for every asset
        it reaches, and nothing typed here knows more."""
        self.kept(BOTH)
        found = typedops.read(self.workspace, GUID, "Late", self.text())
        self.assertEqual(len(found.standing), 1)
        self.assertEqual((found.own_ops, found.reaching, found.answers), ([], [], []))
        self.assertEqual(found.ops, 1)

    def test_typed_as_it_stands_nothing_moves(self):
        self.kept(BOTH)
        before = self.frozen()
        self.assertFalse(self.type(self.text()).moved)
        self.assertEqual(self.frozen(), before)

    def test_typed_over_it_is_the_op_it_was_where_it_stood(self):
        group = self.group()
        op = self.kept(BOTH, order=2, within=[group.uid])
        self.kept(replace_op("/Values/Building/BuildingType", "BuildingType", "Mine"), order=3)
        done = self.type(self.text().replace("<Radius>30</Radius>", "<Radius>31</Radius>"))
        self.assertEqual((done.retuned, done.added, done.dropped, done.reaching), (1, 0, 0, 0))
        again = self.loadout.op(op.uid)
        self.assertIsNot(again, op)
        self.assertIn("<Radius>31</Radius>", again.source)
        self.assertEqual((again.order, again.within), (2, [group.uid]))
        under = self.loadout.changes_by_op(op.uid)
        self.assertEqual(
            sorted((c.guid, c.new_value) for c in under if c.tag == "Radius"),
            [("1000", "31"), ("1001", "31")],
        )
        self.assertTrue(all(c.order == 2 for c in under))

    def test_typed_over_in_a_group_that_does_not_run_it_comes_to_nothing(self):
        group = self.group(idle=True)
        op = self.kept(BOTH, within=[group.uid])
        for change in self.loadout.changes_by_op(op.uid):
            self.loadout.remove_key(change.key)
        op.matched = ["1000", "1001"]
        self.type(self.text().replace("<Radius>30</Radius>", "<Radius>31</Radius>"))
        again = self.loadout.op(op.uid)
        self.assertIn("<Radius>31</Radius>", again.source)
        self.assertEqual(self.loadout.changes_by_op(op.uid), [])

    def test_what_it_did_is_not_read_under_what_is_typed_over_it(self):
        """The asset the typed op is read against is without the op it was
        typed over: twice the radius, and not twice that again."""
        twice = (
            '<ModOp Type="merge" GUID="1000,1001" Path="/Values/Building/Radius">'
            '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp>'
        )
        op = self.kept(twice)
        self.assertEqual(
            [c.new_value for c in self.loadout.changes_by_op(op.uid) if c.guid == GUID], ["20"]
        )
        self.type(self.text().replace("* 2", "* 3"))
        self.assertEqual(
            [c.new_value for c in self.loadout.changes_by_op(op.uid) if c.guid == GUID], ["30"]
        )

    def test_taken_out_of_the_text_it_is_taken_out_of_the_mod(self):
        op = self.kept(BOTH)
        done = self.type("")
        self.assertEqual(done.dropped, 1)
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.loadout.changes_by_op(op.uid), [])

    def test_an_op_typed_for_two_assets_is_applied_and_stays_in_the_pane(self):
        done = self.type(BOTH)
        self.assertEqual((done.added, done.reaching), (1, 0))
        (op,) = self.ops()
        self.assertTrue(keptops.same_op(op.source, BOTH))
        self.assertEqual([item.op for item in self.turn().beyond], [op])

    def test_one_typed_for_another_asset_is_applied_and_leaves_it(self):
        done = self.type(WIDE.replace('GUID="1000"', 'GUID="1001"'))
        self.assertEqual((done.added, done.reaching), (0, 1))
        self.assertEqual(len(self.ops()), 1)
        self.assertEqual(self.text(), "")

    def test_a_note_typed_over_one_is_its_note(self):
        op = self.kept(BOTH)
        done = self.type(self.text().replace("<ModOp ", "<!-- both of them -->\n<ModOp "))
        self.assertEqual(done.retuned, 1)
        self.assertEqual(self.loadout.op(op.uid).note, "both of them")

    def test_a_group_typed_with_one_op_in_it_is_one_modop_and_the_groups_own_tag(self):
        """It goes in as two, the group and the op in it, and whoever typed
        it typed one ModOp: which of the two is the tag is said apart.  The
        op is in the pane from then on, and the tag is not."""
        done = self.type('<Group Condition="#Some_Dependency">%s</Group>' % WIDE)
        self.assertEqual((done.reaching, done.groups), (1, 1))
        self.assertEqual(sorted(op.scope for op in self.ops()), [False, True])
        self.assertEqual(self.text().count("<ModOp "), 1)
        self.assertNotIn("<Group", self.text())


class TestWhereANewOpGoes(TypingAsWritten):
    """Typed between two ops of the pane, a new op goes between them in the
    mod's file.  The pane holds the ops that reach one asset, so that is
    straight after the one before it, whatever else the file has there."""

    RADIUS = replace_op("/Values/Building/Radius", "Radius", "20")
    TYPE = replace_op("/Values/Building/BuildingType", "BuildingType", "Mine")
    OTHER = replace_op("/Values/Building/Radius", "Radius", "7", "1001")
    NEW = '<ModOp Type="merge" GUID="1000" Path="/Values/Standard"><Name>n33</Name></ModOp>'

    def setUp(self):
        super().setUp()
        # In the file: one of this asset's, one of another's, one of this.
        self.first = self.kept(self.RADIUS, order=1)
        self.other = self.kept(self.OTHER, order=2)
        self.last = self.kept(self.TYPE, order=3)

    def in_file(self):
        """The mod's ops and ops carried unread, as the file has them."""
        things = self.ops() + [e for e in self.loadout.unread if e.origin == "Late"]
        return [
            "new" if "33" in thing.source or "Nowhere" in thing.source
            else "first" if thing.source == self.first.source
            else "other" if thing.source == self.other.source
            else "last" if thing.source == self.last.source else "group"
            for thing in sorted(things, key=lambda thing: thing.order)
        ]

    def typed(self, before: str, new=None) -> str:
        """The pane's text with ``new`` typed in front of the op that has
        ``before`` in it, or at its end."""
        new = new or self.NEW
        text = self.text()
        if not before:
            return text.replace("</ModOps>", new + "</ModOps>")
        at = text.rindex("<ModOp ", 0, text.index(before))
        return text[:at] + new + "\\n" + text[at:]

    def test_between_two_it_goes_straight_after_the_one_before_it(self):
        done = self.type(self.typed("BuildingType"))
        self.assertEqual(done.added, 1)
        self.assertEqual(self.in_file(), ["first", "new", "other", "last"])
        # Each thing has a place of its own still, and what an op comes to
        # stands where the op does.
        self.assertEqual(sorted(op.order for op in self.ops()), [1, 2, 3, 4])
        for op in self.ops():
            self.assertEqual(
                {change.order for change in self.loadout.changes_by_op(op.uid)}, {op.order}
            )
        # And the pane shows it where it was typed.
        text = self.text()
        self.assertLess(text.index("<Radius>20"), text.index("n33"))
        self.assertLess(text.index("n33"), text.index("BuildingType"))

    def test_before_them_all_it_goes_straight_before_the_first(self):
        self.type(self.typed("<Radius>20"))
        self.assertEqual(self.in_file(), ["new", "first", "other", "last"])

    def test_after_them_all_it_goes_straight_after_the_last_of_the_pane(self):
        after = self.kept(self.OTHER.replace(">7<", ">8<"), order=4)
        self.type(self.typed(""))
        self.assertEqual(self.in_file()[:4], ["first", "other", "last", "new"])
        self.assertEqual(self.loadout.op(after.uid).order, 5)

    def test_two_typed_together_stay_in_the_order_typed(self):
        second = self.NEW.replace("n33", "n34").replace("Standard", "Building").replace(
            "Name", "TerrainType")
        self.type(self.typed("BuildingType", self.NEW + second))
        ops = sorted(self.ops(), key=lambda op: op.order)
        self.assertEqual(
            ["33" in op.source or "34" in op.source for op in ops],
            [False, True, True, False, False],
        )
        self.assertIn("33", ops[1].source)

    def test_with_nothing_in_the_pane_it_goes_after_the_mods_other_ops(self):
        for op in (self.first, self.last):
            self.loadout.drop_op(op.uid)
        self.type(self.NEW)
        self.assertEqual(self.in_file(), ["other", "new"])

    def test_an_op_that_cannot_be_read_goes_where_it_was_typed_too(self):
        nowhere = '<ModOp Type="replace" GUID="1000" Path="/Values/Nowhere"><X/></ModOp>'
        done = self.type(self.typed("BuildingType", nowhere))
        self.assertEqual(len(done.why_unread), 1)
        self.assertEqual(self.in_file(), ["first", "new", "other", "last"])

    def test_after_one_in_a_group_it_goes_after_the_group(self):
        group = Op(source='<Group Condition="#Some_Mod"></Group>', origin="Late", scope=True,
                   order=0)
        self.loadout.set_op(group)
        self.first.within = [group.uid]
        self.other.within = [group.uid]
        self.type(self.typed("BuildingType"))
        self.assertEqual(self.in_file(), ["group", "first", "other", "new", "last"])
        new = next(op for op in self.ops() if "33" in op.source)
        self.assertEqual(new.within, [])

    def test_between_two_of_one_group_it_goes_between_them(self):
        group = Op(source='<Group Condition="#Some_Mod"></Group>', origin="Late", scope=True,
                   order=0)
        self.loadout.set_op(group)
        for op in (self.first, self.other, self.last):
            op.within = [group.uid]
        self.type(self.typed("BuildingType"))
        self.assertEqual(self.in_file(), ["group", "first", "new", "other", "last"])
        new = next(op for op in self.ops() if "33" in op.source)
        self.assertEqual(new.within, [])
        # The group is written round it, in two parts.
        written = generator.build_modops_xml(generator.for_mod(self.loadout, "Late"))
        self.assertEqual(written.count("<Group "), 2)
        self.assertLess(written.index("n33"), written.rindex("<Group "))

    def test_it_is_one_step_to_undo_with_every_place_it_moved(self):
        from anno_modding_studio.core.undo import UndoHistory

        nowhere = Unread(origin="Late", order=4, label="x", why="no such node",
                         source='<ModOp Type="replace" GUID="1001" Path="/Values/No"><X/></ModOp>')
        self.loadout.unread.append(nowhere)
        history = UndoHistory(self.loadout)
        self.type(self.typed("BuildingType"))
        self.assertEqual([e.order for e in self.loadout.unread], [5])
        history.commit()
        history.undo()
        self.assertEqual(sorted(op.order for op in self.ops()), [1, 2, 3])
        self.assertEqual(
            [op.order for op in (self.loadout.op(self.first.uid), self.loadout.op(self.other.uid),
                                 self.loadout.op(self.last.uid))],
            [1, 2, 3],
        )
        self.assertIs(self.loadout.unread[0], nowhere)
        self.assertEqual(nowhere.order, 4)


class TestAnOpTheTextLeavesAloneIsNotReadAgain(TypingAsWritten):
    """An op of the asset's own was read again at every Apply, from the
    pane.  That reading knows less than the one its mod was read with, and
    an op that copies a value another mod left came to nothing: it was
    carried unread from the first Apply, whatever had been typed (census:
    *Early Game*, two ops of one asset)."""

    COPIES = (
        '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
        'Content="~/Values/Elsewhere/Number/text()"><Radius><ModOpContent/></Radius></ModOp>'
    )

    def setUp(self):
        super().setUp()
        # As the mod was read in its stack: an op, with what it came to.
        self.op = Op(origin="Late", source=self.COPIES, matched=[GUID], order=1, made=1)
        self.loadout.set_op(self.op)
        answer = radius("Late", "10", "7")
        answer.op_uid = self.op.uid
        answer.order = 1
        self.loadout.set_change(answer)

    def test_read_from_the_pane_it_would_not_be_read(self):
        found = typedops.read(self.workspace, GUID, "Late", "<ModOps>%s</ModOps>" % self.COPIES)
        self.assertEqual(len(found.standing), 1)
        self.assertEqual((found.own_ops, found.own_unread), ([], []))
        # Typed anew it is read, and cannot be.
        other = self.COPIES.replace("Elsewhere", "Nowhere")
        found = typedops.read(self.workspace, GUID, "Late", "<ModOps>%s</ModOps>" % other)
        self.assertEqual((len(found.standing), len(found.own_unread)), (0, 1))

    def test_another_op_typed_beside_it_leaves_it_the_op_it_was(self):
        before = self.frozen()
        typed = self.text().replace(
            "</ModOps>",
            replace_op("/Values/Building/BuildingType", "BuildingType", "Mine") + "</ModOps>",
        )
        done = self.type(typed)
        self.assertEqual((done.added, done.dropped, done.why_unread), (1, 0, []))
        self.assertIs(self.loadout.op(self.op.uid), self.op)
        self.assertEqual(vars(self.op), before[1][0])
        self.assertEqual(
            [c.new_value for c in self.loadout.changes_by_op(self.op.uid)], ["7"]
        )
        self.assertEqual(self.loadout.unread, [])
        self.assertEqual(self.loadout.taken_out, [])

    def test_what_is_typed_is_read_against_the_asset_with_what_it_does(self):
        typed = self.text().replace("</ModOps>", (
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
            '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp></ModOps>'
        ))
        self.type(typed)
        (twice,) = [op for op in self.ops() if op is not self.op]
        self.assertEqual(
            [c.new_value for c in self.loadout.changes_by_op(twice.uid)], ["14"]
        )


class TestAnAssetTheModAddsWithAnOpThatIsNotItsOwn(TypingAsWritten):
    """Two assets added by one op: the op is neither asset's own, so it is
    not read with the ops typed for one of them, and they were read against
    no asset at all.  Every one was carried unread at the first Apply
    (census: *Require All Needs*, 25 ops)."""

    NEW = "7001"
    ADDS_TWO = (
        '<ModOp Type="addNextSibling" GUID="1000">%s%s</ModOp>' % tuple(
            "<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID>"
            "<Name>made</Name></Standard><Building><BuildingType>Farm</BuildingType>"
            "<Radius>5</Radius></Building></Values></Asset>" % guid
            for guid in ("7001", "7002")
        )
    )

    def setUp(self):
        super().setUp()
        self.adds = self.kept(self.ADDS_TWO, order=1)

    def test_the_op_that_adds_it_is_in_its_pane_as_one_beyond_it(self):
        turn = next(t for t in xmlview.turns(self.workspace, self.NEW, "Late") if t.editing)
        self.assertEqual([item.op for item in turn.beyond], [self.adds])

    def test_an_op_typed_for_it_is_read_against_the_asset_as_made(self):
        typed = self.text(self.NEW).replace(
            "</ModOps>",
            replace_op("/Values/Building/Radius", "Radius", "9", self.NEW) + "</ModOps>",
        )
        done = self.type(typed, self.NEW)
        self.assertEqual((done.added, done.why_unread), (1, []))
        self.assertEqual(self.loadout.unread, [])
        (own,) = xmlview.own_ops(self.loadout, self.NEW, "Late")
        self.assertEqual(
            [(c.old_value, c.new_value) for c in self.loadout.changes_by_op(own.uid)],
            [("5", "9")],
        )
        # And applied again as it stands, nothing moves.
        before = self.frozen()
        self.assertFalse(self.type(self.text(self.NEW), self.NEW).moved)
        self.assertEqual(self.frozen(), before)


class TestHowFarWhatWasTypedReaches(TypingAsWritten):
    """What the Apply button says before it is clicked, from the text alone."""

    def reach(self, text: str, written=None):
        return typedops.reach(
            self.workspace, GUID, "Late", text, self.text() if written is None else written
        )

    def test_nothing_typed_reaches_nothing(self):
        self.kept(BOTH)
        found = self.reach(self.text())
        self.assertEqual((found.assets, found.search), ([], False))

    def test_an_op_of_the_assets_own_reaches_it_alone(self):
        found = self.reach(WIDE)
        self.assertEqual((found.assets, found.search), ([GUID], False))

    def test_one_that_names_two_reaches_both(self):
        self.assertEqual(self.reach(BOTH).assets, ["1000", "1001"])

    def test_one_the_mod_held_typed_over_reaches_what_it_named(self):
        self.kept(BOTH)
        typed = self.text().replace("<Radius>30</Radius>", "<Radius>31</Radius>")
        self.assertEqual(self.reach(typed).assets, ["1000", "1001"])
        # And taken out, the same.
        self.assertEqual(self.reach("").assets, ["1000", "1001"])

    def test_a_search_typed_is_not_counted_until_it_is_read(self):
        found = self.reach(SEARCH)
        self.assertTrue(found.search)
        self.assertEqual(found.assets, [GUID])

    def test_a_search_the_mod_held_knows_what_it_found(self):
        op = self.kept(SEARCH)
        self.assertIn(GUID, op.matched)
        found = self.reach("")
        self.assertFalse(found.search)
        self.assertEqual(found.assets, sorted(set(op.matched) | {GUID}))

    def test_text_that_is_not_modops_reaches_nothing_to_say(self):
        self.assertIsNone(self.reach("<ModOp"))


class TestWhereWhatWasTypedGoes(TypingAsWritten):
    """What the pane says beside an op before Apply: file tab proposal,
    phase 3.  From the text alone, as the button's count is."""

    def goes(self, text: str, written=None):
        found = typedops.goes(
            self.workspace, GUID, "Late", text, self.text() if written is None else written
        )
        self.last = [] if found is None else [one.last for one in found]
        return None if found is None else [
            (one.line, one.assets, one.search) for one in found
        ]

    def test_an_op_of_the_assets_own_goes_nowhere_else(self):
        self.assertEqual(self.goes(WIDE), [])

    def test_each_op_says_for_itself_on_the_line_it_starts_on(self):
        other = WIDE.replace('GUID="%s"' % GUID, 'GUID="1001"')
        self.assertNotEqual(other, WIDE)
        typed = "\n".join(["", "<ModOps>", WIDE, other, BOTH, SEARCH, "</ModOps>"])
        lines = typed.split("\n")
        self.assertEqual(self.goes(typed), [
            (lines.index(other) + 1, ["1001"], False),
            (lines.index(BOTH) + 1, ["1000", "1001"], False),
            (lines.index(SEARCH) + 1, [], True),
        ])

    def test_with_no_document_round_them_the_lines_are_the_texts_own(self):
        other = WIDE.replace('GUID="%s"' % GUID, 'GUID="1001"')
        self.assertEqual(self.goes(WIDE + "\n" + other), [(2, ["1001"], False)])
        self.assertEqual(self.last, [2])

    def test_and_which_line_it_ends_on(self):
        other = WIDE.replace('GUID="%s"' % GUID, 'GUID="1001"')
        spread = other.replace("><", ">\n<")
        self.assertEqual(spread.count("\n"), 3)
        self.assertEqual(self.goes(WIDE + "\n" + spread + "\n" + other), [
            (2, ["1001"], False), (6, ["1001"], False),
        ])
        self.assertEqual(self.last, [5, 6])

    def test_an_op_the_pane_was_handed_is_not_said_again(self):
        self.kept(BOTH)
        self.assertEqual(self.goes(self.text()), [])
        typed = self.text().replace("<Radius>30</Radius>", "<Radius>31</Radius>")
        ((_line, assets, _search),) = self.goes(typed)
        self.assertEqual(assets, ["1000", "1001"])

    def test_text_that_is_not_modops_has_nothing_to_say(self):
        self.assertIsNone(self.goes("<ModOp"))


class TestTypingTheAssetAsWritten(TypingAsWritten):
    def asset(self):
        return next(
            turn for turn in xmlview.turns(self.workspace, GUID, "Late") if turn.editing
        ).asset

    def test_where_only_the_studios_changes_reach_it_it_is_read_whole(self):
        self.loadout.set_change(radius("Late", "10", "20"))
        found = typedops.read_asset(
            self.workspace, GUID, "Late", self.asset().replace("<Radius>20<", "<Radius>10<"))
        self.assertFalse(found.over)

    def test_where_an_op_somebody_wrote_reaches_it_what_is_typed_is_laid_over(self):
        op = self.kept(WIDE)
        found, done = typedops.read_asset_and_apply(
            self.workspace, GUID, "Late", self.asset().replace("<Radius>30<", "<Radius>31<"))
        self.assertTrue(found.over)
        self.assertEqual(found.refused, "")
        self.assertEqual(done.added, 1)
        # The op is as it was, and the change is the Studio's, after it.
        self.assertIsNotNone(self.loadout.op(op.uid))
        (own,) = self.own()
        self.assertEqual((own.new_value, own.op_uid), ("31", ""))

    def test_what_is_worked_out_from_a_typed_asset_is_narrow(self):
        found = typedops.read_asset(
            self.workspace, GUID, "Late",
            self.asset().replace("<Radius>10<", "<Radius>31<").replace(
                "<BuildingType>Factory<", "<BuildingType>Farm<"))
        self.assertEqual(found.refused, "")
        self.assertEqual(sorted(c.new_value for c in found.changes), ["31", "Farm"])
        self.assertEqual(found.own_ops + found.reaching, [])
