"""A patch exported to share carries your edits to its mod as ops of its own
where they can be, and lists the rest as left out
(docs/proposals/patches-proposal.md, section 9 and phase 5)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_edits import NAME, PATCH, _Edited  # noqa: E402

from anno_modding_studio.core import build, games, imya, patching, sharing  # noqa: E402

A1800 = games.ANNO_1800


def knob(name: str, kind: str = imya.SKIP_TOGGLE, path: str = "."):
    """A knob a tweak tool offers on the op or group called ``name``."""
    return imya.Expose(file="f.xml", expose_id="k", mod_op_id=name, path=path, kind=kind)


def turned(theirs, yours, chain=(), game=A1800, knobs=(), base=None):
    skipped = [theirs] if theirs else []
    inserted = [yours] if yours else []
    found: dict = {}
    for each in knobs:
        found.setdefault(each.mod_op_id, []).append(each)
    return sharing.turn(skipped, inserted, list(chain), sharing.About(game=game, knobs=found, base=base))


NAME_OP = '<ModOp Type="replace" GUID="1001" Path="/Values/Standard/Name"><Name>%s</Name></ModOp>'
MERGE_OP = '<ModOp Type="merge" GUID="%s" Path="/Values/Building">%s</ModOp>'


class TestWhatCanBeTurned(unittest.TestCase):
    def test_a_value_typed_over_with_the_same_search_is_yours_as_it_is(self):
        ops, why = turned(NAME_OP % "Theirs", NAME_OP % "Mine")
        self.assertEqual((ops, why), ([NAME_OP % "Mine"], ""))

    def test_a_merge_that_sets_all_theirs_did_and_more(self):
        theirs = MERGE_OP % ("1001", "<Radius>8</Radius><Cost>5</Cost>")
        yours = MERGE_OP % ("1001", "<Radius>12</Radius><Cost>5</Cost><Upkeep>1</Upkeep>")
        self.assertEqual(turned(theirs, yours), ([yours], ""))

    def test_a_merge_that_leaves_one_of_theirs_out_is_left_out(self):
        theirs = MERGE_OP % ("1001", "<Radius>8</Radius><Cost>5</Cost>")
        yours = MERGE_OP % ("1001", "<Radius>12</Radius>")
        self.assertEqual(turned(theirs, yours), ([], sharing.WHY_COVER))

    def test_more_assets_on_the_same_path_is_wider_and_turned(self):
        theirs = MERGE_OP % ("1001", "<Radius>8</Radius>")
        yours = MERGE_OP % ("1001,1002", "<Radius>12</Radius>")
        self.assertEqual(turned(theirs, yours)[1], "")

    def test_fewer_assets_or_another_path_is_left_out(self):
        theirs = MERGE_OP % ("1001,1002", "<Radius>8</Radius>")
        self.assertEqual(turned(theirs, MERGE_OP % ("1001", "<Radius>12</Radius>"))[1], sharing.WHY_SEARCH)
        elsewhere = theirs.replace("/Values/Building", "/Values/Other").replace("1001,1002", "1001,1002,1003")
        self.assertEqual(turned(theirs, elsewhere)[1], sharing.WHY_SEARCH)

    def test_an_op_taken_out_or_switched_off_is_left_out(self):
        self.assertEqual(turned(NAME_OP % "Theirs", None), ([], sharing.WHY_TAKEN_OUT))

    def test_yours_adding_or_theirs_removing_is_left_out(self):
        add = '<ModOp Type="add" GUID="1001" Path="/Values/Standard"><Name>Mine</Name></ModOp>'
        self.assertEqual(turned(NAME_OP % "Theirs", add)[1], sharing.WHY_KIND)
        remove = '<ModOp Type="remove" GUID="1001" Path="/Values/Standard/Name" />'
        self.assertEqual(turned(remove, NAME_OP % "Mine")[1], sharing.WHY_THEIRS)

    def test_a_replace_over_their_add_or_merge_at_the_node_covers_it(self):
        add = '<ModOp Type="add" GUID="1001" Path="/Values/Standard/Name"><Extra /></ModOp>'
        self.assertEqual(turned(add, NAME_OP % "Mine")[1], "")

    def test_the_condition_must_be_theirs_and_not_ask_the_files(self):
        theirs = NAME_OP.replace('Type="replace"', 'Type="replace" Condition="#other"') % "Theirs"
        self.assertEqual(turned(theirs, NAME_OP % "Mine")[1], sharing.WHY_CONDITION)
        self.assertEqual(turned(theirs, theirs.replace("Theirs", "Mine"))[1], "")
        asking = NAME_OP.replace('Type="replace"', 'Type="replace" Condition="//Values[Radius]"') % "Theirs"
        self.assertEqual(turned(asking, asking.replace("Theirs", "Mine"))[1], sharing.WHY_ASKS)

    def test_asking_whether_an_asset_is_there_is_asked_the_same_unless_their_mod_makes_it(self):
        there = NAME_OP.replace('Type="replace"', 'Type="replace" Condition="!@1500"') % "Theirs"
        self.assertEqual(turned(there, there.replace("Theirs", "Mine"))[1], "")
        made = sharing.turn([there], [there.replace("Theirs", "Mine")], [], sharing.About(made=frozenset({"1500"})))
        self.assertEqual(made[1], sharing.WHY_ASKS)

    def test_an_op_of_yours_asking_the_files_is_asked_as_their_group_s_condition_is(self):
        made = sharing.About(made=frozenset({"1500"}))
        mine = '<ModOp Type="merge" GUID="1001" Path="/Values" Condition="@1500"><Mine /></ModOp>'
        self.assertEqual(sharing.turn([], [mine], [], made), ([], sharing.WHY_ASKS))
        self.assertEqual(sharing.turn([], ["<Group>%s</Group>" % mine], [], made), ([], sharing.WHY_ASKS))
        self.assertEqual(sharing.turn([], [mine], [], sharing.About()), ([mine], ""))

    def test_a_value_worked_out_is_left_out(self):
        yours = MERGE_OP % ("1001", '<Radius><ModValue Insert="self::node() * 2" /></Radius>')
        self.assertEqual(turned(MERGE_OP % ("1001", "<Radius>8</Radius>"), yours)[1], sharing.WHY_WORKED_OUT)

    def test_a_search_that_tests_what_theirs_sets_is_left_out(self):
        path = '<ModOp Type="merge" GUID="1001" Path="/Values/Building[Radius=8]">%s</ModOp>'
        self.assertEqual(turned(path % "<Radius>9</Radius>", path % "<Radius>12</Radius>")[1], sharing.WHY_TESTS)

    def test_a_replace_that_puts_another_node_in_its_place_is_left_out(self):
        theirs = '<ModOp Type="replace" GUID="1001" Path="/Values/Standard/Name"><Title>X</Title></ModOp>'
        self.assertEqual(turned(theirs, NAME_OP % "Mine")[1], sharing.WHY_TESTS)

    ADDED = ('<ModOp Type="addNextSibling" GUID="100"><Asset><Template>X</Template><Values>'
             "<Standard><GUID>1500</GUID><Name>n</Name></Standard>"
             "<Building><Cost>5</Cost><Radius>3</Radius></Building>"
             "<List><Item><A>1</A></Item><Item><A>2</A></Item></List></Values></Asset></ModOp>")

    def test_a_value_in_an_asset_theirs_adds_is_a_merge_on_that_asset(self):
        self.assertEqual(
            turned(self.ADDED, self.ADDED.replace("<Cost>5", "<Cost>7")),
            (['<ModOp Type="merge" GUID="1500" Path="/Values/Building"><Cost>7</Cost></ModOp>'], ""))
        ops, why = turned(self.ADDED, self.ADDED.replace("<Cost>5", "<Cost>7").replace("<Name>n", "<Name>m"))
        self.assertEqual((len(ops), why), (2, ""))

    def test_a_list_entry_is_found_by_what_it_held_once_theirs_ran(self):
        self.assertEqual(
            turned(self.ADDED, self.ADDED.replace("<A>2", "<A>9")),
            (["<ModOp Type=\"merge\" GUID=\"1500\" Path=\"/Values/List/Item[A='2']\"><A>9</A></ModOp>"], ""))

    def test_an_entry_s_key_changed_with_a_value_deeper_in_it_is_found_before_it_changes(self):
        keyed = self.ADDED.replace("<Item><A>1</A></Item>", "<Item><A>1</A><Sub><X>1</X></Sub></Item>")
        self.assertEqual(
            turned(keyed, keyed.replace("<A>1", "<A>7").replace("<X>1", "<X>9")),
            (["<ModOp Type=\"merge\" GUID=\"1500\" Path=\"/Values/List/Item[A='1']/Sub\"><X>9</X></ModOp>",
              "<ModOp Type=\"merge\" GUID=\"1500\" Path=\"/Values/List/Item[A='1']\"><A>7</A></ModOp>"], ""))

    def test_an_entry_nothing_tells_from_the_next_is_left_out(self):
        alike = self.ADDED.replace("<Item><A>2</A></Item>", "<Item><A>1</A><B>x</B></Item>")
        self.assertEqual(turned(alike, alike.replace("<A>1</A></Item>", "<A>5</A></Item>", 1))[1],
                         sharing.WHY_ENTRY)

    BASE = ("<Asset><Values><Standard><GUID>1001</GUID></Standard><Factory><Inputs>"
            "<Item><Product>10</Product><Amount>1</Amount></Item></Inputs></Factory></Values></Asset>")
    ENTRY = "<Item><Product>20</Product><Amount>2</Amount></Item>"

    def base(self, xml=None):
        from lxml import etree

        return lambda guid: etree.fromstring(xml or self.BASE) if guid == "1001" else None

    def test_an_entry_theirs_adds_to_a_list_is_found_by_what_it_holds(self):
        wanted = (["<ModOp Type=\"merge\" GUID=\"1001\" Path=\"/Values/Factory/Inputs/Item[Product='20']\">"
                   "<Amount>3</Amount></ModOp>"], "")
        theirs = '<ModOp Type="add" GUID="1001" Path="/Values/Factory/Inputs">%s</ModOp>' % self.ENTRY
        self.assertEqual(turned(theirs, theirs.replace("<Amount>2", "<Amount>3"), base=self.base()), wanted)
        beside = ("<ModOp Type=\"addNextSibling\" GUID=\"1001\" Path=\"/Values/Factory/Inputs/Item[Product='10']\">"
                  "%s</ModOp>" % self.ENTRY)
        self.assertEqual(turned(beside, beside.replace("<Amount>2", "<Amount>3"), base=self.base()), wanted)

    def test_a_value_held_in_an_item_is_replaced_where_it_stands(self):
        theirs = '<ModOp Type="add" GUID="1001" Path="/Values/Factory/Inputs"><Item><Item>20</Item></Item></ModOp>'
        self.assertEqual(
            turned(theirs, theirs.replace(">20<", ">21<"), base=self.base()),
            (["<ModOp Type=\"replace\" GUID=\"1001\" Path=\"/Values/Factory/Inputs/Item[Item='20']/Item\">"
              "<Item>21</Item></ModOp>"], ""))

    def test_an_entry_the_base_game_has_the_like_of_or_with_no_game_to_ask_is_left_out(self):
        theirs = '<ModOp Type="add" GUID="1001" Path="/Values/Factory/Inputs">%s</ModOp>' % self.ENTRY
        yours = theirs.replace("<Amount>2", "<Amount>3")
        self.assertEqual(turned(theirs, yours)[1], sharing.WHY_ENTRY)
        twin = self.base(self.BASE.replace("<Product>10</Product>", "<Product>20</Product>"))
        self.assertEqual(turned(theirs, yours, base=twin)[1], sharing.WHY_ENTRY)
        searching = theirs.replace(' GUID="1001" Path="/Values', ' Path="//Values')
        self.assertEqual(turned(searching, searching.replace("<Amount>2", "<Amount>3"), base=self.base())[1],
                         sharing.WHY_ENTRY)

    KNOBBED = ('<ModOp Type="merge" GUID="1001" Path="/Values/Building" ModOpID="k">'
               "<Radius>8</Radius><Cost>5</Cost></ModOp>")

    def test_with_a_value_knob_only_the_values_you_changed_go(self):
        cost = knob("k", imya.SIMPLE, "self::Cost")
        self.assertEqual(
            turned(self.KNOBBED, self.KNOBBED.replace(">8<", ">12<"), knobs=[cost]),
            (['<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>12</Radius></ModOp>'], ""))
        replaced = ('<ModOp Type="replace" GUID="1001" Path="/Values/Building" ModOpID="k">'
                    "<Building><Radius>8</Radius><Cost>5</Cost></Building></ModOp>")
        self.assertEqual(
            turned(replaced, replaced.replace(">8<", ">12<"), knobs=[knob("k", imya.SLIDER, "Cost")]),
            (['<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>12</Radius></ModOp>'], ""))

    def test_the_value_a_knob_sets_or_a_knob_that_switches_is_left_out(self):
        cost = knob("k", imya.SIMPLE, "self::Cost")
        self.assertEqual(turned(self.KNOBBED, self.KNOBBED.replace(">5<", ">6<"), knobs=[cost])[1],
                         sharing.WHY_KNOB)
        self.assertEqual(turned(self.KNOBBED, self.KNOBBED.replace(">8<", ">12<"), knobs=[knob("k")])[1],
                         sharing.WHY_TOOL)

    def test_a_knob_clashes_on_the_value_s_own_elements_whatever_comes_first_in_the_op(self):
        # Told apart by the elements themselves: by address, lxml's freed
        # proxies made a knob on the first child stand for what holds the value.
        two = ('<ModOp Type="merge" GUID="1001" Path="/Values" ModOpID="k">'
               "<Standard><Name>n</Name></Standard><Building><Radius>8</Radius></Building></ModOp>")
        yours = two.replace(">8<", ">12<")
        for _ in range(50):
            self.assertEqual(
                turned(two, yours, knobs=[knob("k", imya.SIMPLE, "self::Standard")]),
                (['<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>12</Radius></ModOp>'], ""))
            self.assertEqual(turned(two, yours, knobs=[knob("k", imya.SIMPLE, "self::Building")])[1],
                             sharing.WHY_KNOB)

    def test_in_an_asset_theirs_adds_its_guid_or_its_shape_is_left_out(self):
        self.assertEqual(turned(self.ADDED, self.ADDED.replace("<GUID>1500", "<GUID>1501"))[1], sharing.WHY_KIND)
        self.assertEqual(turned(self.ADDED, self.ADDED.replace("<Radius>3</Radius>", ""))[1], sharing.WHY_KIND)
        moved = self.ADDED.replace('GUID="100"', 'GUID="101"').replace("<Cost>5", "<Cost>7")
        self.assertEqual(turned(self.ADDED, moved)[1], sharing.WHY_KIND)

    def test_text_added_to_a_value_is_left_out_and_never_dropped(self):
        theirs = '<ModOp Type="add" GUID="1010232" Path="/Values/Product/AssociatedRegion">;Colony01</ModOp>'
        self.assertEqual(turned(theirs, theirs.replace(";Colony01", ";Colony01x")), ([], sharing.WHY_KIND))

    def test_an_op_of_yours_among_theirs_goes_as_it_is(self):
        mine = '<ModOp Type="add" GUID="1001" Path="/Values"><Mine /></ModOp>'
        self.assertEqual(turned(None, mine), ([mine], ""))
        reads = '<ModOp Type="merge" GUID="1001" Path="/Values" Condition="$hard"><Mine /></ModOp>'
        self.assertEqual(turned(None, reads)[1], sharing.WHY_SETTING)

    def test_in_their_group_it_goes_in_one_with_the_same_condition(self):
        ops, why = turned(NAME_OP % "Theirs", NAME_OP % "Mine", chain=[{"Condition": "#other-mod"}])
        self.assertEqual(why, "")
        self.assertEqual(ops, ['<Group Condition="#other-mod">\n%s\n</Group>' % (NAME_OP % "Mine")])
        self.assertEqual(turned(NAME_OP % "Theirs", NAME_OP % "Mine", chain=[{}])[0], [NAME_OP % "Mine"])

    def test_in_a_group_they_switched_off_it_does_nothing_and_is_left_out(self):
        self.assertEqual(turned(NAME_OP % "A", NAME_OP % "B", chain=[{"Skip": "1"}])[1], sharing.WHY_OFF)

    def test_a_later_op_of_theirs_on_the_same_field_overtakes_it(self):
        later = ['<ModOp Type="merge" GUID="1001" Path="/Values/Standard"><Name>Later</Name></ModOp>']
        self.assertTrue(sharing._overtaken([NAME_OP % "Mine"], later, A1800))
        elsewhere = ['<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>3</Radius></ModOp>']
        self.assertFalse(sharing._overtaken([NAME_OP % "Mine"], elsewhere, A1800))
        other_asset = ['<ModOp Type="merge" GUID="2002" Path="/Values/Standard"><Name>Later</Name></ModOp>']
        self.assertFalse(sharing._overtaken([NAME_OP % "Mine"], other_asset, A1800))
        searching = ['<ModOp Type="merge" Path="//Values/Standard"><Name>Later</Name></ModOp>']
        self.assertTrue(sharing._overtaken([NAME_OP % "Mine"], searching, A1800))
        whole = ['<ModOp Type="replace" GUID="1001" Path="/Values"><Values /></ModOp>']
        self.assertTrue(sharing._overtaken([NAME_OP % "Mine"], whole, A1800))

    def test_a_later_op_meets_only_what_its_path_reaches(self):
        def meets(later, mine=NAME_OP % "Mine"):
            return sharing._overtaken([mine], [later], A1800)

        entry = ("<ModOp Type=\"merge\" GUID=\"1001\" Path=\"/Values/Factory/Inputs/Item[Product='20']\">"
                 "<Amount>3</Amount></ModOp>")
        added = '<ModOp Type="add" GUID="1001" Path="/Values/Factory/Inputs"><Item><Product>%s</Product></Item></ModOp>'
        self.assertFalse(meets(added % "30", entry))
        self.assertTrue(meets(added % "20", entry))
        other_entry = entry.replace("'20'", "'30'").replace("<Amount>3", "<Amount>9")
        self.assertFalse(meets(other_entry, entry))
        whole_list = '<ModOp Type="replace" GUID="1001" Path="/Values/Factory/Inputs"><Inputs /></ModOp>'
        self.assertTrue(meets(whole_list, entry))
        # A search meets what its steps can reach, at any depth.
        self.assertFalse(meets('<ModOp Type="merge" Path="//Standard"><Info>x</Info></ModOp>'))
        self.assertTrue(meets('<ModOp Type="replace" Path="//Standard"><Standard /></ModOp>'))
        self.assertTrue(meets("<ModOp Type=\"merge\" Path=\"//Asset[Template='X']/Values/Standard\">"
                              "<Name>y</Name></ModOp>"))
        # An asset added again, and a path that cannot be read, meet everything of it.
        self.assertTrue(meets('<ModOp Type="addNextSibling" GUID="5"><Asset><Values><Standard>'
                              "<GUID>1001</GUID></Standard></Values></Asset></ModOp>"))
        self.assertTrue(meets("<ModOp Type=\"merge\" GUID=\"1001\" Path=\"/Values/*[contains(name(), 'S')]\">"
                              "<Name>z</Name></ModOp>"))

    def test_a_group_that_repeats_or_a_tool_switches_is_left_out(self):
        self.assertEqual(turned(NAME_OP % "A", NAME_OP % "B", chain=[{"MaxRepeat": "3"}])[1], sharing.WHY_REPEAT)
        self.assertEqual(turned(NAME_OP % "A", NAME_OP % "B", chain=[{"ModOpID": "knob"}], knobs=[knob("knob")])[1],
                         sharing.WHY_TOOL)
        named = (NAME_OP % "A").replace('Type="replace"', 'Type="replace" ModOpID="knob"')
        self.assertEqual(turned(named, named.replace(">A<", ">B<"), knobs=[knob("knob")])[1], sharing.WHY_TOOL)
        self.assertEqual(turned(named, named.replace(">A<", ">B<"))[1], "")

    def test_a_change_to_a_group_or_several_at_once_is_left_out(self):
        group = '<Group Condition="#a">%s</Group>' % (NAME_OP % "A")
        self.assertEqual(turned(group, group.replace("#a", "#b"))[1], sharing.WHY_GROUP)
        self.assertEqual(sharing.turn([NAME_OP % "A", NAME_OP % "B"], [NAME_OP % "C"], [], sharing.About())[1],
                         sharing.WHY_SEVERAL)

    def test_one_written_another_way_is_nothing_to_turn(self):
        laid = NAME_OP.replace('Type="replace" GUID="1001"', 'GUID="1001"  Type="replace"') % "A"
        self.assertEqual(turned(NAME_OP % "A", laid), ([], ""))


class TestTheirSettingsNamedInFull(unittest.TestCase):
    """Anno 117 reads a bare ``$hard`` as the setting of the mod whose op it
    is, and ``$their-mod.hard`` as that mod's from anywhere. So an op that
    reads their mod's setting goes into a patch with it named in full."""

    THEIRS = sharing.About(game=games.ANNO_117, mod_id="their-mod")
    READS = ('<ModOp Type="merge" GUID="1001" Path="/Values" Condition="$hard">'
             '<Radius><ModValue Insert="self::node() * $factor"/></Radius></ModOp>')

    def turn(self, theirs, yours, chain=(), about=THEIRS):
        return sharing.turn([theirs] if theirs else [], [yours] if yours else [], list(chain), about)

    def test_an_op_of_yours_among_theirs(self):
        ops, why = self.turn(None, self.READS)
        self.assertEqual(why, "")
        self.assertEqual(ops, [self.READS.replace("$hard", "$their-mod.hard")
                               .replace("$factor", "$their-mod.factor")])

    def test_an_op_in_a_group_of_yours_among_theirs(self):
        group = '<Group><ModOp Type="merge" GUID="1001" Path="/Values" Condition="$hard"><Mine /></ModOp></Group>'
        self.assertEqual(self.turn(None, group), ([group.replace("$hard", "$their-mod.hard")], ""))

    def test_a_name_already_in_full_is_left_as_it_is(self):
        named = self.READS.replace("$hard", "$their-mod.hard and $other-mod.easy")
        ops, _why = self.turn(None, named)
        self.assertIn('Condition="$their-mod.hard and $other-mod.easy"', ops[0])

    def test_their_group_s_condition(self):
        ops, why = self.turn(NAME_OP % "Theirs", NAME_OP % "Mine", chain=[{"Condition": "!$hard"}])
        self.assertEqual(why, "")
        self.assertEqual(ops, ['<Group Condition="!$their-mod.hard">\n%s\n</Group>' % (NAME_OP % "Mine")])

    def test_an_op_of_theirs_typed_over_under_the_same_condition(self):
        theirs = (NAME_OP % "Theirs").replace("<ModOp ", '<ModOp Condition="$hard" ')
        ops, why = self.turn(theirs, theirs.replace("Theirs", "Mine"))
        self.assertEqual(why, "")
        self.assertEqual(ops, [theirs.replace("Theirs", "Mine").replace("$hard", "$their-mod.hard")])

    def test_where_it_cannot_be_named_in_full_it_is_left_out(self):
        for about in (sharing.About(game=A1800, mod_id="their-mod"),
                      sharing.About(game=games.ANNO_117)):
            with self.subTest(game=about.game, mod_id=about.mod_id):
                self.assertEqual(self.turn(None, self.READS, about=about), ([], sharing.WHY_SETTING))
                grouped = '<Group><ModOp Type="merge" GUID="1" Path="/V" Condition="$hard"><M /></ModOp></Group>'
                self.assertEqual(self.turn(None, grouped, about=about), ([], sharing.WHY_SETTING))
                self.assertEqual(
                    self.turn(NAME_OP % "A", NAME_OP % "B", chain=[{"Condition": "$hard"}], about=about)[1],
                    sharing.WHY_SETTING,
                )

    def test_named_in_full_the_patch_asks_what_their_op_asked(self):
        from anno_modding_studio.core import modops, settings

        chosen = settings.visible({"theirs": {"hard": "true"}, "patch": {}},
                                  {"theirs": "their-mod", "patch": "my-patch"})
        asked = sharing.qualified("!$hard", self.THEIRS)
        self.assertEqual(
            modops.condition_holds(asked, None, chosen=chosen["patch"], game=games.ANNO_117),
            modops.condition_holds("!$hard", None, chosen=chosen["theirs"], game=games.ANNO_117),
        )


class TestAnExportOfThePatch(_Edited):
    def setUp(self):
        super().setUp()
        self.patch = patching.make(self.loadout, NAME, "My patch")
        # The patch is where changes go, so it keeps the edits.
        self.loadout.editing = self.patch.origin

    def shared(self):
        return sharing.work_out(self.loadout, self.patch.origin, self.copy, self.index)

    def test_it_knows_their_mod_id(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        with mock.patch.object(sharing, "turn", wraps=sharing.turn) as turn:
            self.shared()
        self.assertEqual(turn.call_args.args[3].mod_id, self.loadout.layer(NAME).mod_id)
        self.assertTrue(self.loadout.layer(NAME).mod_id)

    def test_an_edit_that_can_be_turned_goes_at_the_top_of_the_patch(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        shared = self.shared()
        self.assertEqual((len(shared.turned), shared.left, shared.trouble), (1, [], ""))
        view = sharing.view(self.loadout, self.patch.origin, shared)
        text = build.as_files(view).text
        self.assertIn("<Name>Mine</Name>", text)
        self.assertIn("in place of %s's ModOp at assets.xml line 6, which runs first" % NAME, text)
        # Play writes the patch as it always did, without it.
        plain = build.as_files(sharing.view(self.loadout, self.patch.origin, None)).text
        self.assertNotIn("<Name>Mine</Name>", plain)

    def test_written_out_to_share_it_is_in_the_folder(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        view = sharing.view(self.loadout, self.patch.origin, self.shared())
        out = self.root / "exports"
        built = build.write(self.loadout, [self.patch.origin], out, views={self.patch.origin: view})
        (folder,) = built.written
        self.assertIn("<Name>Mine</Name>", (folder / PATCH).read_text(encoding="utf-8"))

    def test_an_edit_a_later_op_of_theirs_would_undo_is_left_out(self):
        main = self.copy / PATCH
        main.write_text(main.read_text(encoding="utf-8").replace("</ModOps>", (
            '  <ModOp Type="merge" GUID="1010030" Path="/Values/Standard"><Name>Later</Name></ModOp>\n'
            "</ModOps>")), encoding="utf-8")
        self.reread()
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        shared = self.shared()
        self.assertEqual(shared.turned, [])
        self.assertEqual([left["why"] for left in shared.left], [sharing.WHY_LATER])

    def test_an_op_taken_out_is_listed_as_left_out(self):
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        shared = self.shared()
        self.assertEqual(shared.turned, [])
        (left,) = shared.left
        self.assertEqual((left["why"], left["line"]), (sharing.WHY_TAKEN_OUT, 6))

    def test_with_no_edits_the_patch_is_as_it_was(self):
        shared = self.shared()
        self.assertEqual((shared.turned, shared.left), ([], []))

    def test_anything_but_a_patch_has_nothing_to_share(self):
        self.assertIsNone(sharing.work_out(self.loadout, NAME, self.copy, self.index))

    def included(self, line: str) -> dict:
        parts = self.copy / Path(PATCH).parent / "parts.xml"
        parts.write_text(
            '<ModOps>\n  <ModOp Type="merge" GUID="1010030" Path="/Values/Standard"><Other>1</Other></ModOp>\n'
            '  <ModOp Type="replace" GUID="1010030" Path="/Values/Standard/Name">'
            "<Name>Part</Name></ModOp>\n</ModOps>\n", encoding="utf-8")
        main = self.copy / PATCH
        main.write_text(main.read_text(encoding="utf-8").replace("</ModOps>", line + "\n</ModOps>"), encoding="utf-8")
        self.reread()
        self.type_over("<Name>Part</Name>", "<Name>Mine</Name>")
        shared = self.shared()
        self.assertEqual(shared.left, [])
        (hunk,) = shared.turned
        self.assertEqual((hunk["file"], hunk["line"]), ((Path(PATCH).parent / "parts.xml").as_posix(), 3))
        return hunk

    def test_an_edit_in_an_included_file_is_that_edit(self):
        (op,) = self.included('  <Include File="parts.xml" />')["ops"]
        self.assertTrue(op.startswith("<ModOp"))
        self.assertIn("<Name>Mine</Name>", op)

    def test_one_written_out_in_full_is_still_that_edit_with_its_condition(self):
        (op,) = self.included('  <Include File="parts.xml" Condition="#other-mod" />')["ops"]
        self.assertTrue(op.startswith('<Group Condition="#other-mod">'))
        self.assertEqual(op.count("<ModOp"), 1)

    def test_one_in_a_group_of_theirs_is_in_a_group_with_its_condition(self):
        (op,) = self.included('  <Group Condition="#more">\n    <Include File="parts.xml" />\n  </Group>')["ops"]
        self.assertTrue(op.startswith('<Group Condition="#more">'))

    def test_includes_side_by_side_are_opened_together(self):
        before = self.copy / Path(PATCH).parent / "before.xml"
        before.write_text('<ModOps>\n  <ModOp Type="merge" GUID="1010030" Path="/Values/Standard">'
                          "<Other>2</Other></ModOp>\n</ModOps>\n", encoding="utf-8")
        hunk = self.included('  <Group Condition="#more">\n    <Include File="before.xml" />\n'
                             '    <Include File="parts.xml" />\n  </Group>')
        self.assertTrue(hunk["ops"][0].startswith('<Group Condition="#more">'))
        self.assertIn("<Name>Mine</Name>", hunk["ops"][0])

    def reread(self):
        from anno_modding_studio.core import reader

        self.loadout.ops = [op for op in self.loadout.ops if op.origin != NAME]
        self.loadout.changes = {key: change for key, change in self.loadout.changes.items() if change.origin != NAME}
        report = reader.analyse(self.link, self.index, origin=NAME)
        for op in report.ops:
            self.loadout.set_op(op)
        for change in report.changes:
            self.loadout.set_change(change)


class TestTheExportTab(_Edited):
    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.output_panel import OutputPanel

        self.app = QApplication.instance() or QApplication([])
        self.patch = patching.make(self.loadout, NAME, "My patch")
        self.loadout.editing = self.patch.origin
        self.panel = OutputPanel(self.loadout)
        self.addCleanup(self.panel.close)
        self.panel.where = mock.Mock(library_dir=mock.Mock(return_value=self.library))
        self.panel.index = self.index
        self.panel._chosen_output_dir = self.root / "exports"

    def show(self):
        index = self.panel.mod_box.findData(self.patch.origin)
        self.panel.mod_box.setCurrentIndex(index)
        self.panel.refresh()
        return self.panel.preview.toPlainText()

    def test_the_preview_has_the_turned_edit_in_the_patch_and_says_so(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        text = self.show()
        self.assertIn("<Name>Mine</Name>", text[:text.index("Your edits to")])
        self.assertIn("In an export, 1 of them is an op of this patch's own", text)
        self.assertNotIn("left out", text)

    def test_the_preview_lists_what_is_left_out_with_why(self):
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        text = self.show()
        self.assertIn("1 is left out of an export", text)
        self.assertIn("line 6. Taken out or switched off", text)

    def test_the_tabs_check_is_of_the_export_on_their_mod_as_shipped(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.show()
        exported = self.panel._job(self.patch.origin, exporting=True)
        self.assertIn("<Name>Mine</Name>", exported.xml)
        self.assertIn((NAME, self.copy), exported.stack)
        played = self.panel._job(self.patch.origin)
        self.assertNotIn("<Name>Mine</Name>", played.xml)
        self.assertNotIn((NAME, self.copy), played.stack)
        self.assertNotEqual(self.panel._key(exported), self.panel._key(played))
        # What the Studio holds is handed the patch on their mod as shipped,
        # as the loader is: their mod read afresh, not the loadout's.
        shipped = self.panel._exported_now(self.patch.origin)[1].shipped
        handed = {key for key, change in exported.underneath.changes.items() if change.origin == NAME}
        self.assertEqual(handed, set(shipped.changes))
        self.assertIsNot(exported.underneath.changes[next(iter(handed))], self.loadout.changes.get(next(iter(handed))))

    def test_with_nothing_an_export_carries_the_two_checks_are_one(self):
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        self.show()
        self.assertEqual(self.panel._key(self.panel._job(self.patch.origin, exporting=True)),
                         self.panel._key(self.panel._job(self.patch.origin)))

    def test_plays_verdict_on_the_patch_as_played_is_not_the_tabs(self):
        """Play's verdict took the tab's one slot for the mod and was shown
        on its line, though for a patch with edits an export carries it is
        about another job than the one the tab exports."""
        from anno_modding_studio.core import loadercheck

        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.show()
        exported = self.panel._job(self.patch.origin, exporting=True)
        played = self.panel._job(self.patch.origin)
        tabs = loadercheck.Verdict(state=loadercheck.NOT_RUN, origin=self.patch.origin, why="As exported.")
        plays = loadercheck.Verdict(state=loadercheck.NOT_RUN, origin=self.patch.origin, why="As played.")
        self.panel._on_verdict(self.panel._key(exported), tabs)
        said = self.panel.loader_label.text()
        self.panel.keep_loader_verdict(self.panel._key(played), plays)
        self.assertEqual(self.panel.loader_label.text(), said)
        self.assertIs(self.panel._verdicts[self.patch.origin][1], tabs)
        # Where the two are one job, Play's is the tab's.
        self.loadout.drop_op(self.op("<Name>Mine</Name>").uid)
        self.show()
        self.panel.keep_loader_verdict(self.panel._key(self.panel._job(self.patch.origin)), plays)
        self.assertIs(self.panel._verdicts[self.patch.origin][1], plays)

    def test_an_export_writes_it_and_says_what_it_left_out(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.show()
        root = self.panel.export_mod(confirm_overwrite=False, notify=False)
        self.assertIn("<Name>Mine</Name>", (root / PATCH).read_text(encoding="utf-8"))
        self.assertEqual(self.panel.left_out, [])

        self.loadout.drop_op(self.op("ModOp Type=\"merge\"").uid)
        self.show()
        self.panel.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(len(self.panel.left_out), 1)
        self.assertIn("1 of your edits to %s could not go into the export" % NAME, self.panel.left_out[0])


if __name__ == "__main__":
    unittest.main()
