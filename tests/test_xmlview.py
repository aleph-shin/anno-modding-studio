"""One asset as XML, a mod at a time (``core.xmlview``).

What the asset editor's XML view asks: the asset as the game ships it, then
for each mod that reaches it the ModOps it writes and the asset it leaves.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import RADIUS, StackCase  # noqa: E402
from support import radius_change as radius  # noqa: E402

from anno_modding_studio.core import build, xmlview  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_REMOVE,
    Change,
    Op,
    Unread,
)


class Stack(StackCase):
    """One asset over the two loaded mods, as the XML view asks for it."""

    def turns(self, guid: str = "1000", editing: str = "Late"):
        return xmlview.turns(self.workspace, guid, editing)

    def value(self, turn, path: str = "Values/Building/Radius") -> str:
        return etree.fromstring(turn.asset.encode("utf-8")).findtext(path)


class TestTheTurns(Stack):
    def test_the_game_comes_first_and_has_no_modops(self):
        game = self.turns()[0]
        self.assertTrue(game.is_game)
        self.assertEqual(game.modops, "")
        self.assertEqual(self.value(game), "10")

    def test_a_mod_that_does_not_reach_the_asset_has_no_turn(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.assertEqual([turn.origin for turn in self.turns()], [None, "Early", "Late"])

    def test_the_mod_being_edited_has_a_turn_with_nothing_written(self):
        turns = self.turns(editing="Early")
        self.assertEqual([turn.origin for turn in turns], [None, "Early"])
        self.assertTrue(turns[1].editing)
        self.assertEqual(turns[1].modops, "")
        self.assertEqual(self.value(turns[1]), "10")

    def test_each_turn_hands_on_what_the_one_before_left(self):
        # Late sets another field, so only a turn that starts from what Early
        # left shows Early's radius.
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
            tag="BuildingType", old_value="Factory", new_value="Farm", origin="Late",
        ))
        _game, early, late = self.turns()
        self.assertEqual(self.value(early), "12")
        self.assertEqual(self.value(early, "Values/Building/BuildingType"), "Factory")
        self.assertEqual(self.value(late), "12")
        self.assertEqual(self.value(late, "Values/Building/BuildingType"), "Farm")

    def test_a_turns_modops_are_what_show_xml_gives_for_those_rows(self):
        change = radius("Early", "10", "12")
        self.loadout.set_change(change)
        early = self.turns()[1]
        self.assertEqual(
            early.modops, build.fragment_xml(self.loadout, "Early", [change.key])
        )
        self.assertIn('Path="/Values/Building/Radius"', early.modops)

    def test_another_assets_ops_are_not_in_the_turn(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(radius("Early", "4", "5", guid="1001"))
        early = self.turns()[1]
        self.assertIn('GUID="1000"', early.modops)
        self.assertNotIn('GUID="1001"', early.modops)

    def test_a_removed_node_is_gone_from_the_asset_text(self):
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_REMOVE, tag="Item",
            path="/Values/ProductList/List/Item[Product='2002']", origin="Early",
        ))
        early = self.turns()[1]
        self.assertNotIn("2002", early.asset)
        self.assertIn("2002", self.turns()[0].asset)

    def test_a_switched_off_mod_has_no_turn(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.layer("Early").active = False
        self.assertEqual([turn.origin for turn in self.turns()], [None, "Late"])

    def test_only_the_last_turn_is_last(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.assertEqual([turn.last for turn in self.turns()], [False, False, True])


class TestWhoLoadsAfterWhom(Stack):
    def test_a_later_mod_setting_the_same_field_is_contested(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(radius("Late", "12", "20"))
        _game, early, late = self.turns(editing="Early")
        self.assertTrue(late.later)
        self.assertEqual(late.contested, [RADIUS])
        self.assertEqual(early.contested, [RADIUS])

    def test_a_later_mod_setting_another_field_is_not(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
            tag="BuildingType", old_value="Factory", new_value="Farm", origin="Late",
        ))
        _game, early, late = self.turns(editing="Early")
        self.assertEqual((early.contested, late.contested), ([], []))

    def test_an_earlier_mod_is_never_contested(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(radius("Late", "12", "20"))
        _game, early, late = self.turns(editing="Late")
        self.assertFalse(early.later)
        self.assertEqual((early.contested, late.contested), ([], []))

    def test_removing_the_node_a_field_is_in_is_contested_at_that_node(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_REMOVE, tag="Building",
            path="/Values/Building", origin="Late",
        ))
        late = self.turns(editing="Early")[2]
        self.assertEqual(late.contested, ["/Values/Building"])

    def test_two_mods_setting_one_value_is_not_contested(self):
        """What counts is the conflicts list's to say, and it does not count
        two mods that agree."""
        self.loadout.set_change(radius("Early", "10", "12"))
        again = radius("Late", "12", "12")
        again.restated = True
        self.loadout.set_change(again)
        _game, early, late = self.turns(editing="Early")
        self.assertEqual((early.contested, late.contested), ([], []))


class TestWhatBelongsToMoreThanThisAsset(Stack):
    SEARCH = (
        '<ModOp Type="merge" Path="//Values[Building]/Building">'
        "<Radius>9</Radius></ModOp>"
    )

    def test_a_search_that_finds_the_asset_is_in_the_turn_as_an_op_beyond_it(self):
        op = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                    origin="Early", matched=["1000", "1001"])
        self.loadout.set_op(op)
        answer = radius("Early", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        early = self.turns()[1]
        self.assertIn("//Values[Building]/Building", early.modops)
        (beyond,) = early.beyond
        self.assertIs(beyond.op, op)
        lines = early.modops.split("\n")
        self.assertIn("<ModOp ", lines[beyond.first - 1])
        self.assertIn("</ModOp>", lines[beyond.last - 1])
        # The line above it is blank: where whoever shows it says what it is.
        self.assertEqual(lines[beyond.first - 2].strip(), "")
        self.assertEqual(early.own_ops, [])
        self.assertEqual(early.reaching, [op])
        self.assertEqual(self.value(early), "9")
        self.assertIn("//Values[Building]/Building", xmlview.also_xml(self.loadout, early))

    def searching(self, guids=("1000", "1001")) -> Op:
        """The search read from Early's file, with the change it comes to for
        each of ``guids``."""
        op = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                origin="Early", matched=list(guids), made=len(guids))
        self.loadout.set_op(op)
        for guid in guids:
            answer = radius("Early", "10", "9", guid)
            answer.op_uid = op.uid
            self.loadout.set_change(answer)
        return op

    def test_it_still_reaches_an_asset_whose_changes_were_reverted(self):
        """Revert asset takes the change and leaves the search, which is
        written as it is and finds the asset in game as before. With the
        change went all that said so: the pane read "does not change this
        asset yet"."""
        op = self.searching()
        self.loadout.remove_asset("1000", "Early")
        self.assertIs(self.loadout.op(op.uid), op)
        early = self.turns(editing="Early")[1]
        self.assertEqual((early.reaching, early.reverted), ([op], [op]))
        # In the turn's ModOps all the same: it answered to the asset.
        self.assertEqual([item.op for item in early.beyond], [op])
        self.assertIn("//Values[Building]/Building", early.modops)
        self.assertIn("//Values[Building]/Building", xmlview.also_xml(self.loadout, early))
        # The asset shown is made of the changes, and has not got it.
        self.assertEqual(self.value(early), "10")
        # Read and not typed in, the mod has a turn for it all the same.
        read = self.turns()[1]
        self.assertEqual((read.origin, read.reverted), ("Early", [op]))
        self.assertIn("//Values[Building]/Building", read.whole)

    def test_but_not_one_it_never_changed_or_changes_still(self):
        op = self.searching()
        self.loadout.remove_asset("1000", "Early")
        other = self.turns("1001", editing="Early")[1]
        self.assertEqual((other.reaching, other.reverted), ([op], []))
        # 1002 is no asset the search answered to.
        self.assertEqual(self.turns("1002", editing="Early")[1].reaching, [])

    def test_nor_one_whose_op_is_switched_off(self):
        """Off, the op is not written, and does nothing to the asset in game.
        Switched off as the changes list does it: the op and its answers
        together, an answer off under an op that is on being no state the
        Studio gets into."""
        op = self.searching()
        self.loadout.remove_asset("1000", "Early")
        self.assertEqual(self.turns(editing="Early")[1].reverted, [op])
        op.active = False
        for change in self.loadout.changes_by_op(op.uid):
            change.active = False
        self.assertEqual(self.turns(editing="Early")[1].reverted, [])

    def test_a_view_of_the_asset_alone_is_told_of_every_mod_up_to_the_one_edited(self):
        """Fields shows the asset as every mod loaded up to the one being
        edited leaves it. It asked only about that mod, so an earlier mod's
        op, loaded by the game all the same, went unsaid."""
        early = self.searching()
        self.loadout.remove_asset("1000", "Early")
        late = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                  origin="Late", matched=["1000"], made=1)
        self.loadout.set_op(late)
        mine = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                  matched=["1000"], made=1)
        self.loadout.set_op(mine)

        def reaching(upto):
            return xmlview.reverted_reaching(self.workspace, "1000", upto)

        self.assertEqual(reaching("Early"), [early])
        # In load order, and none of a mod that loads after.
        self.assertEqual(reaching("Late"), [early, late])
        # Your own mod loads after both here, so it sees everything.
        self.assertEqual(reaching(""), [early, late, mine])
        # A mod that is not loading does nothing to the asset in game.
        self.loadout.layer("Early").active = False
        self.assertEqual(reaching("Late"), [late])

    def test_and_so_is_one_of_the_ops_it_could_not_read(self):
        """Asked as :func:`reverted_reaching` is, of every mod up to one: the
        editor built that set itself and handed it over, which let a mod that
        is not loading in, and counted its op on the asset."""
        def unread(origin):
            entry = Unread(origin=origin, order=3, label="replace @1000/Values/Empty",
                           source='<ModOp Type="replace" GUID="1000" Path="/Values/Empty">'
                                  "<Empty/></ModOp>")
            self.loadout.unread.append(entry)
            return entry

        early, late, mine = unread("Early"), unread("Late"), unread("")

        def reaching(upto):
            return xmlview.unread_reaching(self.loadout, "1000", None, upto=upto)

        self.assertEqual(reaching("Early"), [early])
        self.assertEqual(reaching("Late"), [early, late])
        self.assertEqual(reaching(""), [early, late, mine])
        self.loadout.layer("Early").active = False
        self.assertEqual(reaching("Late"), [late])
        self.assertEqual(reaching(""), [late, mine])

    def test_but_a_turn_that_is_only_read_shows_it_with_the_rest(self):
        """The split is for typing. Read, a mod's turn is all it does here:
        an op adding two assets was in neither asset's pane, and looked lost."""
        op = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                    origin="Early", matched=["1000", "1001"])
        self.loadout.set_op(op)
        answer = radius("Early", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        own = radius("Early", "9", "12")
        own.path = "/Values/Building/BuildingType"
        own.tag, own.old_value, own.new_value = "BuildingType", "Factory", "Farm"
        self.loadout.set_change(own)
        self.loadout.unread.append(Unread(
            origin="Early", order=9, label="merge @1000,1001",
            source='<ModOp Type="merge" GUID="1000,1001" Path="/Values/Nope"><A>1</A></ModOp>',
            why="no such node",
        ))
        early = self.turns()[1]
        self.assertEqual(early.modops.count("<ModOp "), 3)
        self.assertEqual(len(early.beyond), 2)
        self.assertEqual(early.whole.count("<ModOp "), 3)
        self.assertIn("//Values[Building]/Building", early.whole)
        self.assertIn("Farm", early.whole)
        self.assertIn("/Values/Nope", early.whole)

    def test_and_one_with_nothing_here_has_nothing_to_show(self):
        self.loadout.set_change(radius("Early", "10", "9"))
        late = self.turns()[-1]
        self.assertEqual((late.origin, late.whole), ("Late", ""))

    def test_an_unread_op_naming_only_the_asset_is_among_its_own_modops(self):
        self.loadout.unread.append(Unread(
            origin="Early", order=3, label="replace @1000/Values/Empty",
            source='<ModOp Type="replace" GUID="1000" Path="/Values/Empty"><Empty/></ModOp>',
        ))
        early = self.turns()[1]
        self.assertEqual(early.origin, "Early")
        self.assertEqual((len(early.own_unread), early.unread), (1, []))
        self.assertIn('Path="/Values/Empty"', early.modops)

    def test_an_unread_op_naming_two_assets_is_in_it_as_one_beyond_the_asset(self):
        entry = Unread(
            origin="Early", order=3, label="replace @1000,1001/Values/Empty",
            source='<ModOp Type="replace" GUID="1000,1001" Path="/Values/Empty"><Empty/></ModOp>',
        )
        self.loadout.unread.append(entry)
        early = self.turns()[1]
        self.assertEqual((early.own_unread, len(early.unread)), ([], 1))
        self.assertIn('GUID="1000,1001"', early.modops)
        self.assertEqual([item.unread for item in early.beyond], [entry])
        self.assertIn('Path="/Values/Empty"', xmlview.also_xml(self.loadout, early))

    def test_an_op_kept_as_written_for_the_asset_alone_is_among_its_own_modops(self):
        source = ('<ModOp Type="merge" GUID="1000" Path="/Values/Building" KeepAsWritten="1">'
                  "<Radius>9</Radius></ModOp>")
        op = Op(expression="@1000", source=source, origin="Early", kept="it is marked",
                    matched=["1000"])
        self.loadout.set_op(op)
        answer = radius("Early", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        early = self.turns()[1]
        self.assertEqual((early.own_ops, early.reaching), ([op], []))
        self.assertIn('KeepAsWritten="1"', early.modops)
        self.assertEqual(early.modops.count("<ModOp "), 1)

    def test_an_op_naming_this_asset_alone_is_the_turns_own_whyever_it_is_kept(self):
        """What is typed is what is stored, so its text is all there is to it,
        whatever the reader had to say of writing it another way."""
        source = ('<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                  "<Radius>9</Radius></ModOp>")
        op = Op(expression="@1000", source=source, origin="Early", matched=["1000"],
                    kept="written one field at a time its changes do not add up")
        self.loadout.set_op(op)
        answer = radius("Early", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        early = self.turns()[1]
        self.assertEqual((early.own_ops, early.reaching), ([op], []))
        self.assertEqual(early.modops.count("<ModOp "), 1)

    def test_one_in_a_group_is_in_it_as_the_op_and_not_the_assets_own(self):
        """Its condition is the group's, and is not in its text: the op is
        in the turn's ModOps, and the group's own tags are not."""
        group = Op(source='<Group Condition="#Some_Mod"></Group>', origin="Early", scope=True,
                   order=1)
        self.loadout.set_op(group)
        source = ('<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                  "<Radius>9</Radius></ModOp>")
        op = Op(expression="@1000", source=source, origin="Early", matched=["1000"],
                within=[group.uid], order=2)
        self.loadout.set_op(op)
        answer = radius("Early", "10", "9")
        answer.op_uid = op.uid
        self.loadout.set_change(answer)
        early = self.turns()[1]
        self.assertEqual((early.own_ops, early.reaching), ([], [op]))
        self.assertEqual([item.op for item in early.beyond], [op])
        self.assertEqual(early.modops.count("<ModOp "), 1)
        self.assertNotIn("<Group", early.modops)
        self.assertEqual(xmlview.group_condition(self.loadout, op), "#Some_Mod")

    def test_one_that_does_not_run_is_in_it_still(self):
        """An op of a group that does not run comes to nothing, and is the
        mod's op for this asset all the same."""
        group = Op(source='<Group Condition="#Nobody"></Group>', origin="Early", scope=True,
                   idle=True, order=1)
        self.loadout.set_op(group)
        op = Op(
            expression="@1000", origin="Early", matched=["1000"], within=[group.uid], order=2,
            source='<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                   "<Radius>9</Radius></ModOp>",
        )
        self.loadout.set_op(op)
        self.assertEqual(xmlview.beyond_ops(self.workspace, "1000", "Early"), [op])
        self.assertEqual(xmlview.beyond_ops(self.workspace, "1001", "Early"), [])
        # And it gives its mod a turn, read only as well as typed in: only
        # the mod being edited had one.
        for editing in ("Early", "Late"):
            with self.subTest(editing=editing):
                early = self.turns(editing=editing)[1]
                self.assertEqual(early.origin, "Early")
                self.assertEqual([item.op for item in early.beyond], [op])
                self.assertIn("<Radius>9</Radius>", early.whole)

    def test_a_group_kept_whole_is_not(self):
        """It is one thing with the ops it holds, and its text is the
        group's: typed over in the mod's file.  In the pane it read back as
        a group typed there, and took the place of the one the mod had
        (census, *All Items visible at Traders*)."""
        whole = Op(
            origin="Early", matched=["1000", "1001"],
            source='<Group MaxRepeat="3">%s</Group>' % self.SEARCH,
        )
        self.loadout.set_op(whole)
        answer = radius("Early", "10", "9")
        answer.op_uid = whole.uid
        self.loadout.set_change(answer)
        self.assertEqual(xmlview.beyond_ops(self.workspace, "1000", "Early"), [])
        early = self.turns(editing="Early")[1]
        self.assertEqual((early.modops, early.beyond), ("", []))
        self.assertEqual(early.reaching, [whole])

    def test_one_switched_off_is_not(self):
        op = Op(expression="//Values[Building]/Building", source=self.SEARCH,
                origin="Early", matched=["1000", "1001"], active=False)
        self.loadout.set_op(op)
        self.assertEqual(xmlview.beyond_ops(self.workspace, "1000", "Early"), [])

    def test_a_kept_op_that_comes_to_nothing_still_gives_its_mod_a_turn(self):
        source = ('<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
                  'KeepAsWritten="1"><Radius>10</Radius></ModOp>')
        self.loadout.set_op(Op(expression="@1000", source=source, origin="Early",
                                   kept="it is marked", matched=["1000"]))
        self.assertEqual([turn.origin for turn in self.turns()], [None, "Early", "Late"])

    def test_an_unread_op_aimed_elsewhere_does_not(self):
        self.loadout.unread.append(Unread(
            origin="Early", order=3,
            source='<ModOp Type="replace" GUID="1001" Path="/Values/Building"><Building/></ModOp>',
        ))
        self.assertEqual([turn.origin for turn in self.turns()], [None, "Late"])


class TestAnOpIsAnAssetsOwnAsItsGameReadsIt(unittest.TestCase):
    """An op that names one asset and no other is that asset's own, and is
    typed over in its turn.  Read as either game would, an Anno 1800 op in
    the short form was one, though its game passes it by."""

    def test_the_short_form_names_its_asset_in_anno_117_only(self):
        short = '<ModOp Merge="@1000/Building"><Radius>3</Radius></ModOp>'
        self.assertTrue(xmlview.names_only(short, "1000", "anno117"))
        self.assertFalse(xmlview.names_only(short, "1000", "anno1800"))

    def test_and_the_classic_form_in_both(self):
        classic = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>3</Radius></ModOp>'
        for game in ("anno117", "anno1800"):
            with self.subTest(game=game):
                self.assertTrue(xmlview.names_only(classic, "1000", game))


class TestAnAssetAModCreates(Stack):
    def test_the_game_has_none_and_the_creator_leaves_it(self):
        self.loadout.set_change(Change(
            guid="1000", asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
            origin="Early",
            payload="<Asset><Template>Building</Template><Values><Standard>"
                    "<GUID>1337000000</GUID><Name>brand_new</Name></Standard>"
                    "</Values></Asset>",
        ))
        game, early = self.turns(guid="1337000000", editing="Early")
        self.assertEqual(game.asset, "")
        self.assertIn("brand_new", early.asset)
        self.assertIn("<Asset>", early.modops)


class TestLines(Stack):
    def setUp(self):
        super().setUp()
        self.change = radius("Early", "10", "12")
        self.loadout.set_change(self.change)
        self.early = self.turns()[1]

    def test_a_fields_line_is_where_it_starts_in_the_asset_text(self):
        line = xmlview.line_of(self.early, RADIUS)
        self.assertIn("<Radius>12</Radius>", self.early.asset.splitlines()[line - 1])

    def test_a_path_that_finds_nothing_has_no_line(self):
        self.assertIsNone(xmlview.line_of(self.early, "/Values/Nowhere"))

    def test_a_line_gives_back_the_path_of_its_field(self):
        line = xmlview.line_of(self.early, RADIUS)
        self.assertEqual(xmlview.path_at(self.early, line), RADIUS)

    def test_a_list_entrys_line_gives_its_keyed_path(self):
        path = "/Values/Cost/Costs/Item[Ingredient='1011']/Amount"
        line = xmlview.line_of(self.early, path)
        self.assertEqual(xmlview.path_at(self.early, line), path)

    def test_a_changes_op_is_found_in_its_turns_modops(self):
        line = xmlview.op_line(self.loadout, self.early, self.change.key)
        self.assertIn(
            'Path="/Values/Building/Radius"', self.early.modops.splitlines()[line - 1]
        )

    def test_a_change_of_another_asset_has_no_op_line_here(self):
        other = radius("Early", "4", "5", guid="1001")
        self.loadout.set_change(other)
        self.assertIsNone(xmlview.op_line(self.loadout, self.early, other.key))

    def test_a_change_lands_on_the_line_of_the_field_it_sets(self):
        line = xmlview.line_for(self.early, self.change)
        self.assertEqual(line, xmlview.line_of(self.early, RADIUS))

    def test_a_changes_op_is_found_in_what_a_tab_only_read_shows(self):
        """Its whole ModOps, which is not what the tab typed in shows."""
        self.assertFalse(self.early.editing)
        line = xmlview.shown_op_line(self.loadout, self.early, self.change)
        self.assertIn(
            'Path="/Values/Building/Radius"', self.early.whole.splitlines()[line - 1]
        )

    def test_of_ops_that_open_alike_the_one_whose_lines_follow(self):
        """One merge after another into the same node opens the same way."""
        first = '<ModOp Type="merge" GUID="1" Path="/Values">\n  <A>1</A>\n</ModOp>'
        second = '<ModOp Type="merge" GUID="1" Path="/Values">\n  <B>2</B>\n</ModOp>'
        text = "<ModOps>\n  %s\n\n  %s\n</ModOps>" % (
            first.replace("\n", "\n  "), second.replace("\n", "\n  "),
        )
        self.assertEqual(xmlview._line_of_op(text, second), 6)
        self.assertEqual(xmlview._line_of_op(text, first), 2)


class TestTheLinesAModChanged(Stack):
    """What a turn left different from the turn before it, as lines of its
    asset: what the view marks, so nobody has to walk a path in their head."""

    def lines(self, turn):
        text = turn.asset.splitlines()
        return [text[line - 1].strip() for line in turn.changed]

    def test_a_value_set_is_its_one_line(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        self.assertEqual(self.lines(self.turns()[1]), ["<Radius>12</Radius>"])

    def test_the_game_and_a_mod_that_changes_nothing_have_none(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        game, _early, late = self.turns()
        self.assertEqual(game.changed, [])
        self.assertEqual(late.changed, [])

    def test_each_mod_is_marked_against_the_mod_before_it_and_not_the_game(self):
        self.loadout.set_change(radius("Early", "10", "12"))
        path = "/Values/Cost/Costs/Item[Ingredient='1011']/Amount"
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path=path, tag="Amount",
            old_value="1", new_value="9", origin="Late",
        ))
        late = self.turns()[2]
        self.assertEqual(self.lines(late), ["<Amount>9</Amount>"])

    def test_one_number_in_one_entry_of_a_list_is_one_line(self):
        path = "/Values/Cost/Costs/Item[Ingredient='1011']/Amount"
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path=path, tag="Amount",
            old_value="1", new_value="9", origin="Early",
        ))
        early = self.turns()[1]
        self.assertEqual(early.changed, [xmlview.line_of(early, path)])

    def test_an_entry_added_is_marked_whole(self):
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_ADD_NEXT, tag="Item",
            path="/Values/ProductList/List/Item[Product='2002']", origin="Early",
            payload="<Item><Product>2999</Product></Item>",
        ))
        marked = self.lines(self.turns()[1])
        self.assertEqual(marked, ["<Item>", "<Product>2999</Product>", "</Item>"])

    def test_an_entry_removed_marks_the_list_it_was_in(self):
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_REMOVE, tag="Item",
            path="/Values/ProductList/List/Item[Product='2002']", origin="Early",
        ))
        self.assertEqual(self.lines(self.turns()[1]), ["<List>"])

    def test_one_removed_and_one_added_leave_the_entries_between_unmarked(self):
        """The list is as long as it was, so entry for entry by place every
        one after the removal reads as changed: 2002 where 2001 was, 2003
        where 2002 was. The mod touched neither."""
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_REMOVE, tag="Item",
            path="/Values/ProductList/List/Item[Product='2001']", origin="Early",
        ))
        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", op=OP_ADD, tag="Item",
            path="/Values/ProductList/List", origin="Early",
            payload="<Item><Product>2999</Product></Item>",
        ))
        early = self.turns()[1]
        self.assertEqual(
            self.products(etree.fromstring(early.asset.encode("utf-8"))),
            ["2002", "2003", "2999"],
        )
        self.assertEqual(
            self.lines(early), ["<List>", "<Item>", "<Product>2999</Product>", "</Item>"]
        )

    def test_text_typed_into_a_list_itself_marks_the_lists_own_line(self):
        """Its entries are as they were, so the line that differs is its own."""
        before = etree.fromstring("<List><Item><A>1</A></Item><Item><A>2</A></Item></List>")
        after = etree.fromstring("<List>x<Item><A>1</A></Item><Item><A>2</A></Item></List>")
        self.assertEqual(xmlview.changed_lines(before, after, xmlview.laid_out(after)), [1])


if __name__ == "__main__":
    import unittest

    unittest.main()
