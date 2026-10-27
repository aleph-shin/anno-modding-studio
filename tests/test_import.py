"""Importing a mod somebody else wrote.

The dialects here are real: ``Type="merge" GUID Path="/Values/…"`` is how Anno
1800 mods are written and how Serp's Anno 117 keybind mod is written too, while
``Merge="@4115/Building"`` with a ``Condition`` is how the Anno 117 community
bug-fix mod is written.  Both appear in mods for the same game, which is why
nothing here decides on a dialect before reading a file.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import (  # noqa: E402
    IMPORT_ASSETS,
    MODINFO_WITH_OPTIONS,
    ImportCase,
    SessionCase,
    modops_file,
)

from anno_modding_studio.core import (  # noqa: E402
    conflicts,
    effective,
    games,
    generator,
    keptops,
    modfolder,
    modop,
    modops,
    patching,
    paths,
    reader,
    recording,
    settings,
    typedops,
)
from anno_modding_studio.core import (  # noqa: E402
    index as index_mod,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
    SWITCHED_OFF_MARK,
    Change,
    Loadout,
    ModLayer,
    Op,
    after_their_entry,
    emit_order,
    own_record,
)
from anno_modding_studio.core.workspace import Workspace  # noqa: E402


def _live_ops(xml: str) -> list:
    """The ops of a document that the game reads."""
    return [op for op in etree.fromstring(xml.encode("utf-8")) if isinstance(op.tag, str)]


class TestBothDialects(ImportCase):
    """A mod for either game may be written in either form."""

    def test_the_classic_form(self):
        changes = self.imported(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].op, OP_REPLACE)
        self.assertEqual(changes[0].path, "/Values/Building/Radius")
        self.assertEqual((changes[0].old_value, changes[0].new_value), ("10", "25"))

    def test_the_shorthand_form(self):
        changes = self.imported(
            '<ModOp Replace="@1000/Building/Radius"><Radius>25</Radius></ModOp>'
        )
        self.assertEqual(changes[0].path, "/Values/Building/Radius")
        self.assertEqual(changes[0].new_value, "25")

    def test_the_type_name_is_read_case_insensitively(self):
        """A shipping mod writes ``Type="AddNextSibling"``; another writes it
        lower case in the same game."""
        changes = self.imported(
            '<ModOp Type="AddNextSibling" GUID="1000">'
            "<Asset><Template>Thing</Template><Values><Standard>"
            "<GUID>5000</GUID><Name>invented</Name></Standard></Values></Asset>"
            "</ModOp>"
        )
        self.assertTrue(changes[0].creates_asset)

    def test_the_guid_may_live_inside_a_classic_path(self):
        changes = self.imported(
            '<ModOp Type="add" Path="@1000/ProductList/List">'
            "<Item><Product>2009</Product></Item></ModOp>"
        )
        self.assertEqual(changes[0].guid, "1000")
        self.assertEqual(changes[0].path, "/Values/ProductList/List")

    def test_a_path_that_already_starts_at_values_is_not_doubled(self):
        result = self.only(
            '<ModOp Merge="@1000/Values/Building"><Radius>7</Radius></ModOp>'
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertEqual(result.changes[0].path, "/Values/Building/Radius")


class TestBreakingCoarseOpsUp(ImportCase):
    def test_a_merge_becomes_one_change_per_field(self):
        changes = self.imported(
            '<ModOp Merge="@1000/Building">'
            "<Radius>25</Radius><BuildingType>Factory</BuildingType>"
            "<Elevation>3</Elevation></ModOp>"
        )
        # BuildingType already reads that way, so it is not a change at all.
        self.assertEqual(len(changes), 2)
        by_tag = {change.tag: change for change in changes}
        self.assertEqual(by_tag["Radius"].op, OP_REPLACE)
        self.assertEqual(by_tag["Elevation"].op, OP_MERGE)
        self.assertEqual(by_tag["Elevation"].path, "/Values/Building")

    def test_a_field_a_merge_makes_goes_back_out_as_a_merge(self):
        """A merge makes the field where it is absent and sets it where another
        mod made it first. Written back as an ``add`` it only made it, and
        beside a mod that had, the asset held two."""
        changes = self.imported(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>'
        )
        self.assertEqual(changes[0].ensured, "/Values/Building/Elevation")
        loadout = Loadout()
        loadout.set_change(changes[0])
        xml = generator.build_modops_xml(loadout)
        self.assertIn('Type="merge"', xml)
        self.assertIn('Path="/Values/Building"', xml)
        self.assertNotIn('Type="add"', xml)
        again = self.imported(xml.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0])
        self.assertEqual(
            [(c.op, c.path, c.tag, c.payload) for c in again],
            [(c.op, c.path, c.tag, c.payload) for c in changes],
        )

    def test_a_whole_section_a_merge_makes_is_one_merge(self):
        changes = self.imported(
            '<ModOp Merge="@1001/Values"><Cost><Costs><Item><Ingredient>1010</Ingredient>'
            "</Item></Costs></Cost></ModOp>"
        )
        self.assertEqual([(c.op, c.path, c.tag) for c in changes], [(OP_MERGE, "/Values", "Cost")])
        # One value, sections deep: where the section is there, this is what
        # the merge sets in it.
        self.assertEqual(changes[0].sets, ("/Values/Cost/Costs/Item/Ingredient", "1010"))

    def test_a_field_named_like_the_node_it_goes_into_stays_an_addition(self):
        """A merge carrying one node named like its target names the target
        twice, so written as a merge this one would not go inside it."""
        changes = self.imported(
            '<ModOp Merge="@1000/Building"><Building><Deep>1</Deep></Building>'
            "<Radius>25</Radius></ModOp>"
        )
        by_tag = {change.tag: change for change in changes}
        self.assertEqual(by_tag["Building"].op, OP_ADD)
        self.assertEqual(by_tag["Building"].ensured, "")

    def test_a_subtree_replace_becomes_the_difference(self):
        changes = self.imported(
            '<ModOp Replace="@1000/Building">'
            "<Building><BuildingType>Factory</BuildingType><Radius>3</Radius>"
            "<Extra>1</Extra></Building></ModOp>"
        )
        kinds = sorted((change.op, change.tag) for change in changes)
        self.assertEqual(kinds, [(OP_ADD, "Extra"), (OP_REPLACE, "Radius")])

    def test_a_replace_that_drops_a_field_removes_it(self):
        """A whole-node replace says what the node *is*, so what it leaves out
        is a removal -- unlike a merge, which only ever sets."""
        changes = self.imported(
            '<ModOp Replace="@1000/Building">'
            "<Building><Radius>10</Radius></Building></ModOp>"
        )
        self.assertEqual([(c.op, c.tag) for c in changes], [(OP_REMOVE, "BuildingType")])

    def test_a_path_matching_several_nodes_becomes_one_change_each(self):
        changes = self.imported('<ModOp Remove="@1000/ProductList/List/Item"></ModOp>')
        self.assertEqual(len(changes), 3)
        self.assertEqual(
            sorted(change.path for change in changes),
            [
                "/Values/ProductList/List/Item[Product='200%d']" % n
                for n in (1, 2, 3)
            ],
        )

    def test_each_change_is_re_addressed_by_content_not_position(self):
        changes = self.imported(
            "<ModOp Remove=\"@1000/Cost/Costs/Item[Amount = '8']\"></ModOp>"
        )
        self.assertEqual(changes[0].path, "/Values/Cost/Costs/Item[Ingredient='1011']")
        self.assertFalse(changes[0].positional)

    def test_the_mod_s_own_comment_is_kept_as_the_note(self):
        changes = self.imported(
            "<!-- #make it bigger - by somebody -->"
            '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
        )
        self.assertEqual(changes[0].note, "#make it bigger - by somebody")

    def test_an_op_the_studio_wrote_switched_off_is_nobodys_note(self):
        """It is a comment only so the game does not read it, and what is
        above it was said of it."""
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        _off, live = self.read(
            "<!-- said of the one that is off -->"
            "<!-- %s:\n<ModOp Merge=\"@1000/Building\"><Radius>9</Radius></ModOp>\n-->"
            '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>' % SWITCHED_OFF_MARK
        ).results
        self.assertEqual(live.changes[0].note, "")
        self.assertEqual(live.changes[0].new_value, "25")


class TestAnOpMarkedToBeKeptAsWritten(ImportCase):
    """``KeepAsWritten="1"``, the Studio's mark from when an op could be
    rewritten as narrow changes: every op goes back out as written now, so
    it means nothing, and the op is read and written as one without it.
    Both games run an op carrying it as they run it without (measured in
    game, 4 Oct 2026)."""

    MERGE = ('<ModOp Type="merge" GUID="1000" Path="/Values/Building" KeepAsWritten="1">'
             "<Radius>12</Radius><BuildingType>Farm</BuildingType></ModOp>")

    def written(self, report) -> str:
        loadout = Loadout()
        for result in report.results:
            if result.record is not None:
                loadout.set_op(result.record)
            for change in result.changes:
                loadout.set_change(change)
        return generator.build_modops_xml(loadout)

    def test_it_is_read_and_kept_as_the_op_it_is(self):
        result = self.only(self.MERGE)
        self.assertEqual(result.status, modops.IMPORTED)
        self.assertIsNotNone(result.record)
        self.assertEqual(result.record.kept, "")
        # What it comes to is worked out, to be shown under it.
        self.assertEqual(
            sorted(change.new_value for change in result.changes), ["12", "Farm"]
        )

    def test_it_is_written_back_with_its_mark_and_reads_the_same_again(self):
        written = self.written(self.read(self.MERGE))
        self.assertEqual(written.count("<ModOp "), 1)
        self.assertIn('KeepAsWritten="1"', written)
        body = written.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0]
        self.assertEqual(self.written(self.read(body)), written)

    def test_one_the_asset_already_answers_to_is_kept_all_the_same(self):
        result = self.only(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
            'KeepAsWritten="1"><Radius>10</Radius></ModOp>'
        )
        self.assertIsNotNone(result.record)
        self.assertEqual(result.changes, [])

    def test_the_attribute_is_one_the_reader_knows(self):
        result = self.only(self.MERGE)
        self.assertNotIn("ignored", result.reason)


class TestAnEmptyContainer(ImportCase):
    """``<Empty />`` is not a value, it is a container waiting to be filled.

    Anno 117's aqueduct consumers ship exactly like that and the bug-fix mod
    merges into them; reading the empty element as a value refused the op.
    """

    def test_merging_into_one_adds_fields(self):
        changes = self.imported(
            '<ModOp Merge="@1000/Empty"><Inner>4</Inner></ModOp>'
        )
        self.assertEqual([(c.op, c.tag) for c in changes], [(OP_MERGE, "Inner")])
        self.assertEqual(changes[0].path, "/Values/Empty")

    def test_a_real_shape_change_cannot_be_a_field_edit(self):
        """Merging a container into a node that holds a value is something the
        game will happily do; there is just no one field it changes, so it can
        only be carried as a swap of the whole node."""
        result = self.only(
            '<ModOp Merge="@1000/Building/Radius"><Inner>4</Inner></ModOp>'
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertIn("whole node", result.reason)
        added = next(c for c in result.changes if c.op != OP_REMOVE)
        swapped = effective.payload_elements(added)[0]
        self.assertEqual(swapped.text, "10")
        self.assertEqual(swapped.findtext("Inner"), "4")


class TestSiblingInsertions(ImportCase):
    """``Append``/``Prepend`` put a node beside another, not inside it.

    Every one of them in the shipping mods targets a list *entry*, while every
    ``Add`` targets the list itself.
    """

    def test_prepend_goes_before_the_node_it_names(self):
        changes = self.imported(
            "<ModOp Prepend=\"@1000/ProductList/List/Item[Product = '2002']\">"
            "<Item><Product>2009</Product></Item></ModOp>"
        )
        self.assertEqual(changes[0].op, OP_ADD_PREV)
        self.assertEqual(changes[0].path, "/Values/ProductList/List/Item[Product='2002']")

    def test_append_goes_after_it(self):
        changes = self.imported(
            "<ModOp Append=\"@1000/ProductList/List/Item[Product = '2002']\">"
            "<Item><Product>2009</Product></Item></ModOp>"
        )
        self.assertEqual(changes[0].op, OP_ADD_NEXT)

    def test_a_run_of_entries_stays_one_change(self):
        """Four ModOps inserting after the same node each land immediately
        after it, which reverses them; the run has to travel together."""
        changes = self.imported(
            "<ModOp Append=\"@1000/ProductList/List/Item[Product = '2001']\">"
            "<Item><Product>2007</Product></Item>"
            "<Item><Product>2008</Product></Item>"
            "<Item><Product>2009</Product></Item></ModOp>"
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(
            [node.findtext("Product") for node in effective.payload_elements(changes[0])],
            ["2007", "2008", "2009"],
        )

    def test_appending_inside_a_list_still_becomes_one_change_each(self):
        """An append *inside* a container is order-preserving on its own, so
        each entry can be its own ModOp and be undone separately."""
        changes = self.imported(
            '<ModOp Add="@1000/ProductList/List">'
            "<Item><Product>2007</Product></Item>"
            "<Item><Product>2008</Product></Item></ModOp>"
        )
        self.assertEqual(len(changes), 2)


class TestConditions(ImportCase):
    """A condition asks about the file being patched, so it gets answered."""

    def test_one_that_does_not_hold_means_the_op_is_not_part_of_the_mod(self):
        result = self.only(
            "<ModOp Merge=\"@1000/Building\" Condition=\"@1000/Building[Radius = '99']\">"
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertEqual(result.changes, [])

    def test_the_tilde_form_is_the_relative_path_in_an_older_spelling(self):
        """The loader strips the ``~`` and reads the rest against the op's own
        asset, exactly as it reads a bare path.  Forty-nine ops in the real
        Anno 1800 folder were unread for it."""
        result = self.only(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="~/Values/Building/Radius">'
            "<Radius>3</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        result = self.only(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="!~/Values/Building/Radius">'
            "<Radius>3</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertIn("does not hold", result.reason)
        result = self.only(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="!~/Values/Building/Nope">'
            "<Radius>3</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)

    def test_a_bare_path_starts_where_the_op_s_own_path_does(self):
        """From ``<Asset>`` for a ``Type=`` op, from ``<Values>`` for the short
        form -- the docs' "the Condition path skips /Values/" -- and in the
        document for an op with no GUID attribute, its asset named only in
        ``Merge="@1000/…"``. Every xmltest build, measured."""
        def status(op):
            return self.only(op + "<Radius>3</Radius></ModOp>").status

        typed = '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="%s">'
        self.assertEqual(status(typed % "/Values/Building"), modops.IMPORTED)
        self.assertEqual(status(typed % "/Building"), modops.SETTLED)
        self.assertEqual(status(typed % "!~/Building"), modops.IMPORTED)
        short = '<ModOp GUID="1000" Merge="Building" Condition="%s">'
        self.assertEqual(status(short % "/Building"), modops.IMPORTED)
        self.assertEqual(status(short % "/Values/Building"), modops.SETTLED)
        aimed = '<ModOp Merge="@1000/Building" Condition="%s">'
        self.assertEqual(status(aimed % "/Building"), modops.SETTLED)
        self.assertEqual(status(aimed % "!/Values/Building"), modops.IMPORTED)

    def test_the_tilde_alone_asks_whether_the_op_s_asset_is_there(self):
        """Every xmltest build, measured: ``~`` holds of an asset that is there."""
        lookup = self.index.asset_element
        self.assertTrue(modops.condition_holds("~", lookup, "1000"))
        self.assertFalse(modops.condition_holds("!~", lookup, "1000"))
        self.assertTrue(modops.condition_holds("~/", lookup, "1000"))

    def test_a_negated_condition_asks_the_opposite(self):
        self.assertTrue(
            modops.condition_holds(
                "!@1000/ProductList/List/Item[Product = '9999']",
                self.index.asset_element,
            )
        )
        self.assertFalse(
            modops.condition_holds(
                "!@1000/ProductList/List/Item[Product = '2001']",
                self.index.asset_element,
            )
        )

    def test_one_that_cannot_be_read_is_never_guessed_at(self):
        result = self.only(
            '<ModOp Merge="@1000/Building" Condition="//Something[@weird]">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("could not be checked", result.reason)


class TestOpsRunInOrder(ImportCase):
    """ModOps patch a document that is changing underneath them."""

    MOVE = (
        "<ModOp Remove=\"@1000/ProductList/List/Item[Product = '2003']\" "
        "Condition=\"@1000/ProductList/List/Item[Product = '2003']\"></ModOp>"
        "<ModOp Prepend=\"@1000/ProductList/List/Item[Product = '2001']\" "
        "Condition=\"!@1000/ProductList/List/Item[Product = '2003']\">"
        "<Item><Product>2003</Product></Item></ModOp>"
    )

    def test_a_condition_sees_what_the_op_before_it_did(self):
        """The second op's guard is only true because the first ran.  Read
        against the pristine file it would look like a no-op."""
        report = self.read(self.MOVE)
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED] * 2)

    def test_the_result_is_the_entry_moved(self):
        report = self.read(self.MOVE)
        replayed = self.index.asset_element("1000")
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        for change in emit_order(loadout.ordered()):
            recording.replay(replayed, change)
        self.assertEqual(self.products(replayed), ["2003", "2001", "2002"])

    def test_a_later_op_can_target_an_asset_an_earlier_one_created(self):
        report = self.read(
            '<ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Template>Thing</Template><Values><Standard>"
            "<GUID>5000</GUID><Name>invented</Name></Standard>"
            "<Bits><Item>1</Item></Bits></Values></Asset></ModOp>"
            '<ModOp Merge="@5000/Bits"><Item>2</Item></ModOp>'
        )
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED, modops.SETTLED])
        # The edit belongs in the payload that introduced the asset, not in a
        # second ModOp against an asset the base game has never heard of.
        self.assertEqual(len(report.changes), 1)
        created = effective.parse_payload(report.changes[0])
        self.assertEqual(created.findtext("Values/Bits/Item"), "2")

    def test_several_assets_in_one_op_become_one_change_each(self):
        changes = self.imported(
            '<ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Values><Standard><GUID>5000</GUID><Name>one</Name>"
            "</Standard></Values></Asset>"
            "<Asset><Values><Standard><GUID>5001</GUID><Name>two</Name>"
            "</Standard></Values></Asset></ModOp>"
        )
        self.assertEqual(len(changes), 2)
        self.assertEqual(
            [effective.new_asset_guid(change) for change in changes], ["5000", "5001"]
        )

    def test_the_guid_of_an_asset_a_change_adds_is_read_from_its_payload_once(self):
        """The change list's grouping asks it of every such change at every
        edit: 22,680 payloads read as XML again for one value typed."""
        change = self.imported(
            '<ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Values><Standard><GUID>5002</GUID><Name>three</Name>"
            "</Standard></Values></Asset></ModOp>"
        )[0]
        effective._guid_in.cache_clear()
        with mock.patch.object(
            effective, "parse_payload_text", wraps=effective.parse_payload_text
        ) as read:
            asked = [effective.new_asset_guid(change) for _ in range(3)]
        self.assertEqual(asked, ["5002"] * 3)
        self.assertEqual(read.call_count, 1)
        # Another payload is another answer.
        other = dataclasses.replace(change, payload=change.payload.replace("5002", "5003"))
        self.assertEqual(effective.new_asset_guid(other), "5003")


class TestWhatIsRefused(ImportCase):
    def test_content_templating(self):
        result = self.only(
            '<ModOp Type="add" Path="@1000/ProductList/List" '
            'Content="//Values/Standard/GUID/text()">'
            "<Item><ModOpContent /></Item></ModOp>"
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("copies from", result.reason)

    def test_an_op_that_searches_for_its_targets(self):
        result = self.only(
            '<ModOp Merge="//Values[Building]/Building"><Radius>1</Radius></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("by GUID", result.reason)

    def test_an_op_with_no_path_says_so(self):
        """A11-80: an op naming neither a GUID nor a Path was said to target
        assets by search, which did not tell its author what was missing."""
        for body in ('<ModOp Type="merge"><Residents>4</Residents></ModOp>',
                     '<ModOp Type="merge" Path=""/>', '<ModOp Merge=" "><A>1</A></ModOp>'):
            result = self.only(body)
            self.assertEqual(result.status, modops.BLOCKED, body)
            self.assertIn("neither a GUID nor a Path", result.reason, body)
        # One aimed by Property= names its assets that way.
        result = self.only('<ModOp Type="merge" Property="Building"><Radius>4</Radius></ModOp>')
        self.assertNotIn("neither a GUID nor a Path", result.reason)

    def test_an_asset_this_assets_xml_does_not_have(self):
        result = self.only('<ModOp Remove="@9999/Building"></ModOp>')
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("no asset 9999", result.reason)

    def test_a_moditem_under_anything_but_a_merge(self):
        """``<ModItem>`` means an entry to find only under a merge.  Under an
        add the loader puts a literal ``<ModItem>`` element into the game's
        data, measured -- so the op is carried, and goes on doing that."""
        result = self.only(
            '<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
            '<ModItem Merge="Product"><Product>2099</Product></ModItem></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("<ModItem>", result.reason)
        self.assertEqual(result.changes, [])

    def test_a_path_that_matches_nothing(self):
        result = self.only('<ModOp Merge="@1000/Nowhere"><A>1</A></ModOp>')
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("matches nothing", result.reason)

    def test_removing_something_already_absent_is_not_a_problem(self):
        result = self.only('<ModOp Remove="@1000/Nowhere"></ModOp>')
        self.assertEqual(result.status, modops.SETTLED)


class TestFallingBackToTheWholeNode(ImportCase):
    """When the diff cannot express it, swapping the node still can."""

    def test_a_list_with_nothing_to_match_entries_on(self):
        result = self.only(
            '<ModOp Merge="@1000/ProductList/List">'
            "<Item><Product>2001</Product></Item>"
            "<Item><Product>2001</Product></Item></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertIn("whole node", result.reason)

    def test_it_is_a_removal_plus_an_insertion_beside_a_neighbour(self):
        """Never beside the node being removed: removals are written first, so
        there would be nothing left to anchor on."""
        changes = self.imported(
            "<ModOp Replace=\"@1000/ProductList/List/Item[Product = '2002']\">"
            "<Item><Product>2007</Product></Item>"
            "<Item><Product>2008</Product></Item></ModOp>"
        )
        by_op = {change.op: change for change in changes}
        self.assertEqual(sorted(by_op), sorted([OP_REMOVE, OP_ADD_NEXT]))
        self.assertEqual(
            by_op[OP_REMOVE].path, "/Values/ProductList/List/Item[Product='2002']"
        )
        self.assertEqual(
            by_op[OP_ADD_NEXT].path, "/Values/ProductList/List/Item[Product='2001']"
        )

    def test_the_half_of_the_diff_that_did_work_is_thrown_away(self):
        """The diff walks the node as it goes, so a mismatch it only meets part
        way down leaves edits behind for the part it managed.  Keeping those
        alongside the swap would edit a node and replace it in one breath --
        which is what an Anno 1800 start-settings mod actually triggered."""
        result = self.only(
            '<ModOp Replace="@1000/Cost">'
            "<Cost><Other>1</Other><Costs>"
            "<Item><Ingredient>1010</Ingredient></Item>"
            "<Item><Ingredient>1010</Ingredient></Item>"
            "</Costs></Cost></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertIn("whole node", result.reason)
        self.assertEqual(
            sorted(change.op for change in result.changes),
            sorted([OP_REMOVE, OP_ADD_NEXT]),
        )

    def test_swapping_a_node_for_a_differently_named_one_still_works(self):
        changes = self.imported(
            '<ModOp Replace="@1000/Building/Radius"><Width>3</Width></ModOp>'
        )
        self.assertEqual(
            sorted(change.op for change in changes), sorted([OP_REMOVE, OP_ADD_NEXT])
        )

    def test_neighbours_swapped_by_one_op_are_not_each_other_s_anchor(self):
        """Each swap is hung off a node the op leaves where it is. Hung off a
        neighbour the same op takes out, the replacement went nowhere, and
        with nothing put in the reader failed on the whole mod."""
        for path, wanted in (
            ("Item[position() &lt; 3]", ["9", "9", "2003"]),
            ("Item[position() &gt; 1]", ["2001", "9", "9"]),
            ("Item", ["9", "9", "9"]),
        ):
            with self.subTest(path=path):
                result = self.only(
                    '<ModOp Replace="@1000/ProductList/List/%s"><Entry>9</Entry></ModOp>' % path
                )
                self.assertEqual(result.status, modops.IMPORTED, result.reason)
                replayed = self.index.asset_element("1000")
                loadout = Loadout()
                for change in result.changes:
                    loadout.set_change(change)
                for change in emit_order(loadout.ordered()):
                    recording.replay(replayed, change)
                entries = replayed.find("Values/ProductList/List")
                self.assertEqual(
                    [entry.findtext("Product") or entry.text for entry in entries], wanted
                )


class TestRemovalsTheGameWouldNotNotice(ImportCase):
    """A wide replace of a list deletes every entry it did not retype.

    Read as XPath that is exactly right, and it is what the Seal shrine mod's
    ``replace`` of a cost list says.  But the shipped files leave the amount off
    a good that costs nothing -- 893 of Anno 117's cost entries are bare like
    that -- so those deletions change nothing in game, were not what the author
    meant, and cost the mod its ability to stack with others.
    """

    WIDE_COST = (
        '<ModOp Replace="@1002/Cost/Costs">'
        "<Costs><Item><Ingredient>1010</Ingredient><Amount>9</Amount></Item>"
        "</Costs></ModOp>"
    )
    WIDE_LIST = (
        '<ModOp Replace="@1002/ProductList/List">'
        "<List><Item><Product>2001</Product></Item></List></ModOp>"
    )

    def lean(self, body: str):
        report = self.read(body, keep_inert=False)
        self.assertEqual(len(report.results), 1, report.results)
        return report.results[0]

    def test_read_strictly_it_deletes_the_lot(self):
        changes = self.imported(self.WIDE_COST)
        removed = sorted(c.path for c in changes if c.op == OP_REMOVE)
        self.assertEqual(
            removed,
            [
                "/Values/Cost/Costs/Item[Ingredient='%d']" % guid
                for guid in (1011, 1012, 1013)
            ],
        )

    def test_by_default_the_ones_that_cost_nothing_are_left_out(self):
        result = self.lean(self.WIDE_COST)
        self.assertEqual(result.status, modops.IMPORTED)
        removed = [c.path for c in result.changes if c.op == OP_REMOVE]
        self.assertEqual(removed, ["/Values/Cost/Costs/Item[Ingredient='1011']"])

    def test_an_entry_that_costs_something_is_still_removed(self):
        """1011 costs 7, so dropping it is a real edit."""
        result = self.lean(self.WIDE_COST)
        removal = next(c for c in result.changes if c.op == OP_REMOVE)
        self.assertEqual(removal.old_value, "")
        self.assertIn("1011", removal.path)

    def test_the_op_says_what_it_left_out(self):
        result = self.lean(self.WIDE_COST)
        self.assertIn("left out 2 removals", result.reason)
        self.assertIn("reads as absent already", result.reason)

    def test_the_value_it_really_changes_still_comes_through(self):
        result = self.lean(self.WIDE_COST)
        replace = next(c for c in result.changes if c.op == OP_REPLACE)
        self.assertEqual((replace.old_value, replace.new_value), ("5", "9"))

    def test_a_membership_list_is_left_strictly_alone(self):
        """Product-list entries never carry a value, so a bare one *is* the
        entry -- and removing it is a removal, not an artifact."""
        result = self.lean(self.WIDE_LIST)
        removed = sorted(c.path for c in result.changes if c.op == OP_REMOVE)
        self.assertEqual(
            removed,
            [
                "/Values/ProductList/List/Item[Product='%d']" % guid
                for guid in (2002, 2003, 2004)
            ],
        )
        self.assertNotIn("left out", result.reason)

    def test_a_removal_the_author_actually_wrote_is_never_second_guessed(self):
        """An explicit Remove is an instruction; a removal a wide replace only
        implied is an artifact of how it was written."""
        report = self.read(
            "<ModOp Remove=\"@1002/Cost/Costs/Item[Ingredient = '1012']\"></ModOp>",
            keep_inert=False,
        )
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        self.assertEqual(len(report.changes), 1)

    def test_an_op_that_does_nothing_else_is_reported_as_doing_nothing(self):
        result = self.lean(
            '<ModOp Replace="@1002/Cost/Costs">'
            "<Costs><Item><Ingredient>1010</Ingredient><Amount>5</Amount></Item>"
            "<Item><Ingredient>1011</Ingredient><Amount>7</Amount></Item>"
            "</Costs></ModOp>"
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertIn("reads as absent already", result.reason)

    def test_later_ops_still_read_the_document_the_mod_leaves_behind(self):
        """The deletion happens; it is only never written down.  A condition
        asking whether the entry is still there has to get the mod's answer."""
        report = self.read(
            self.WIDE_COST
            + "<ModOp Merge=\"@1002/Standard\" "
            "Condition=\"!@1002/Cost/Costs/Item[Ingredient = '1012']\">"
            "<Name>renamed</Name></ModOp>",
            keep_inert=False,
        )
        self.assertEqual(
            [r.status for r in report.results], [modops.IMPORTED, modops.IMPORTED]
        )

    def test_the_leaner_mod_still_does_what_the_mod_does(self):
        """Nothing is blocked, so the replay agreed -- and it agrees on what the
        game reads rather than on the literal XML."""
        report = self.read(self.WIDE_COST, keep_inert=False)
        self.assertEqual(report.count(modops.BLOCKED), 0)
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        self.assertEqual(
            [r for r in generator.verify_changes(self.index, loadout) if not r.ok], []
        )

    # -- the judgement itself -------------------------------------------
    def test_what_counts_as_an_entry_the_game_reads_as_absent(self):
        inert = recording.Inert(self.index)
        costs = self.index.asset_element("1002").find("Values/Cost/Costs")
        by_good = {item.findtext("Ingredient"): item for item in costs}
        self.assertTrue(inert(by_good["1012"]), "no amount, so it costs nothing")
        self.assertFalse(inert(by_good["1010"]), "it costs 5")

        products = self.index.asset_element("1002").find("Values/ProductList/List")
        self.assertFalse(
            inert(products[0]), "nothing in a product list ever carries a value"
        )

    def test_a_zero_is_not_something_set(self):
        """The same reading refs.py already uses for links: a zero amount and no
        amount are the same statement."""
        entry = etree.fromstring(
            "<Item><Ingredient>1012</Ingredient><Amount>0</Amount></Item>"
        )
        costs = self.index.asset_element("1002").find("Values/Cost/Costs")
        costs.append(entry)
        try:
            self.assertTrue(recording.Inert(self.index)(entry))
        finally:
            costs.remove(entry)

    def test_a_node_that_names_no_asset_at_all_is_never_dropped(self):
        entry = etree.fromstring("<Item><Note>hello</Note></Item>")
        costs = self.index.asset_element("1002").find("Values/Cost/Costs")
        costs.append(entry)
        try:
            self.assertFalse(recording.Inert(self.index)(entry))
        finally:
            costs.remove(entry)

    def test_where_a_node_sits_is_read_without_its_predicates(self):
        """The reference index is keyed by the plain path, not by the ModOp path
        that identifies one particular entry."""
        item = self.index.asset_element("1002").find("Values/Cost/Costs/Item")
        self.assertEqual(recording.plain_path(item), "/Values/Cost/Costs/Item")

class TestTheRefactorHasToStillDoIt(ImportCase):
    """The generated ModOps are replayed the way the game runs them.

    Narrow ops are applied in file order, and a path written for one node can
    match a node another op put there -- so rather than reason about that case
    by case, the translation is run forwards again and compared.
    """

    # One op that moves an entry: its own changes are one block, removal and
    # re-insertion together, and only the removals-first order keeps them apart.
    # (MOVE's two ops are two blocks, written in file order either way.)
    # An empty ``<Item/>`` cannot be merged field by field, so the op swaps the
    # whole cost list: a removal of ``Costs`` and an insertion of the new one.
    # A third entry just like the first: the loader adds it -- a merge goes
    # by position, and there is no third to merge into -- and two entries
    # alike are ones no narrow op can tell apart. (This was an empty <Item/>
    # set beside the first entry, which the loader in fact merges into, and
    # so leaves as it was.)
    SWAP = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Cost/Costs">'
        "<Item/><Item/><Item><Ingredient>1010</Ingredient><Amount>5</Amount></Item></ModOp>"
    )

    def test_a_whole_node_swap_is_kept_as_itself_too(self):
        """The swap claims everything inside the node, which is more than the
        op does, so it is what is shown and not what is written."""
        report = self.read(self.SWAP)
        result = report.results[0]
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertIn("swaps the whole node", result.record.kept)
        ops = sorted(change.op for change in result.changes)
        self.assertEqual(len(ops), 2)
        self.assertIn(OP_REMOVE, ops)

    def test_a_move_across_two_ops_is_replayed_op_by_op(self):
        """Each op's changes are written as a block at that op's place, so the
        replay runs them that way too -- not every removal of the mod first."""
        with mock.patch.object(reader, "emit_order", list):
            report = self.read(TestOpsRunInOrder.MOVE)
        self.assertEqual(report.count(modops.BLOCKED), 0)

    def test_and_with_the_ordering_it_relies_on_they_do(self):
        report = self.read(TestOpsRunInOrder.MOVE)
        self.assertEqual(report.count(modops.BLOCKED), 0)


class TestGeneratingWhatWasImported(ImportCase):
    def test_a_removal_that_takes_the_asset_s_guid_goes_last(self):
        """Every narrow ModOp finds its asset by ``Values/Standard/GUID``.

        *Enbesa Quest Skip* empties quests by replacing their ``Values`` with
        nothing, which reads as one removal per section. In path order
        ``/Values/Standard`` came before ``/Values/Text``, and the game's loader
        missed every removal after it -- eighty of them."""
        loadout = Loadout()
        for path in ("/Values/Objectives", "/Values/Standard", "/Values/Text"):
            loadout.set_change(
                Change(guid="1000", asset_label="alpha", path=path,
                       tag=path.rsplit("/", 1)[-1], op=OP_REMOVE)
            )
        xml = generator.build_modops_xml(loadout)
        order = [xml.index(tag) for tag in ("Objectives", "Text", "/Values/Standard")]
        self.assertEqual(order, sorted(order))

    def test_text_left_after_a_field_does_not_stop_the_read(self):
        """*Fast Move-In* closes a field with ``</MoveRandomMs>>``.

        The stray ``>`` is text after the element, which copying the element
        serialised along with it -- one element and then text past the end, so
        the copy would not parse and reading the whole folder crashed."""
        changes = self.imported(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building">'
            "<Extra>7</Extra>>\n</ModOp>"
        )
        self.assertEqual([change.tag for change in changes], ["Extra"])
        self.assertNotIn(">\n", changes[0].payload.split("</Extra>", 1)[-1])

    def test_an_item_whose_key_is_replaced_gets_its_key_written_last(self):
        """*Pizza Margherita* swaps a Colosseum ingredient for its own tomato.

        The list entry is found by the very field being replaced, so written
        with the key first, every later field's path named an entry that no
        longer existed -- and the Studio's own check refused the op for it."""
        result = self.only(
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1010']\">"
            "<Item><Ingredient>1012</Ingredient><Amount>9</Amount></Item></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        loadout = Loadout()
        for change in result.changes:
            loadout.set_change(change)
        xml = generator.build_modops_xml(loadout)
        self.assertLess(xml.index("<Amount>"), xml.index("<Ingredient>"))
        # And the asset as the Studio shows it applies them in that order too:
        # key first, the new entry kept its old amount.
        shown = Workspace(self.index, loadout).effective_asset("1000").element
        entry = next(i for i in shown.iter("Item") if i.findtext("Ingredient") == "1012")
        self.assertEqual(entry.findtext("Amount"), "9")

    def test_a_key_replacement_moves_behind_only_its_own_entry(self):
        """Behind every change to the asset, it landed after unrelated inserts
        and a re-read export of the *shared pools* bundle came out different."""
        entry = "/Values/Cost/Costs/Item[Ingredient='1010']"
        key = Change(guid="1000", asset_label="alpha", path=entry + "/Ingredient",
                     tag="Ingredient", old_value="1010", new_value="1012")
        amount = Change(guid="1000", asset_label="alpha", path=entry + "/Amount",
                        tag="Amount", old_value="1", new_value="9")
        other = Change(guid="1000", asset_label="alpha", path="/Values/Building",
                       tag="Extra", op=OP_ADD, payload="<Extra>1</Extra>")
        ordered = after_their_entry([key, amount, other])
        self.assertEqual(ordered, [amount, key, other])

    def test_a_key_replacement_waits_for_its_op_s_changes_to_the_entry_its_new_key_names(self):
        """Once ``Item[Product='2001']`` is ``2002``, a path by ``2002`` finds
        it too.  A wide replace read field by field gave it the new key first,
        and the add that followed put a second ``<X>`` in it."""
        result = self.only(
            '<ModOp Replace="@1000/ProductList/List/Item">'
            "<Item><Product>2002</Product><X>1</X></Item></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        replayed = self.index.asset_element("1000")
        loadout = Loadout()
        for change in result.changes:
            loadout.set_change(change)
        for change in emit_order(loadout.ordered()):
            recording.replay(replayed, change)
        entries = replayed.find("Values/ProductList/List")
        self.assertEqual(
            [(entry.findtext("Product"), len(entry.findall("X"))) for entry in entries],
            [("2002", 1)] * 3,
        )

    def test_a_removal_of_the_entry_itself_does_not_pull_its_key_behind_it(self):
        """The *shared pools* bundle replaces ``Item[GUID='11404']/GUID`` and
        later removes ``Item[GUID='11404']``. The game runs them in that order,
        so the removal finds nothing; the Studio removed the entry instead."""
        entry = "/Values/ItemEffect/EffectTargets/Item[GUID='11404']"
        key = Change(guid="1000", asset_label="alpha", path=entry + "/GUID",
                     tag="GUID", old_value="11404", new_value="1500200685")
        removal = Change(guid="1000", asset_label="alpha", path=entry,
                         tag="Item", op=OP_REMOVE, order=1)
        self.assertEqual(after_their_entry([key, removal]), [key, removal])

    def test_a_key_replacement_waits_for_its_own_op_s_change_to_the_entry_itself(self):
        """*Stronger Pirates* merges into ``ReputationTrigger/Item[MinTriggerReputation='25']``:
        a new key and a new field in one op. The field goes into the entry itself,
        found by the old key, so the key has to wait for it -- behind only the
        changes *under* the entry, it went first and fifteen ops were refused."""
        result = self.only(
            "<ModOp Type=\"merge\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1010']\">"
            "<Ingredient>1012</Ingredient><Extra>1</Extra></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        loadout = Loadout()
        for change in result.changes:
            loadout.set_change(change)
        xml = generator.build_modops_xml(loadout)
        self.assertLess(xml.index("<Extra>"), xml.index("<Ingredient>1012"))

    def test_an_entry_moved_beside_the_mod_s_own_is_not_folded_before_its_removal(self):
        """*New Docklands* removes an entry and inserts it again beside one of its
        own new entries. Folded into that earlier insertion, the moved entry was
        written before the removal, which then took it too."""
        report = self.read(
            "<ModOp Type=\"addPrevSibling\" GUID=\"1000\" Path=\"/Values/ProductList/List/Item[Product='2001']\">"
            "<Item><Product>2004</Product></Item></ModOp>"
            "<ModOp Type=\"remove\" GUID=\"1000\" Path=\"/Values/ProductList/List/Item[Product='2002']\"/>"
            "<ModOp Type=\"addPrevSibling\" GUID=\"1000\" Path=\"/Values/ProductList/List/Item[Product='2004']\">"
            "<Item><Product>2002</Product></Item></ModOp>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0)
        moved = [c for c in report.changes if c.is_structural and "2002" in c.payload and c.op != OP_REMOVE]
        self.assertEqual(len(moved), 1, [c.payload for c in report.changes])
        self.assertNotIn("2004", moved[0].payload)
        removal = next(c for c in report.changes if c.op == OP_REMOVE)
        self.assertGreater(moved[0].order, removal.order)

    def test_an_entry_whose_key_an_earlier_op_replaced_is_found_by_the_new_key(self):
        """The *shared pools* bundle replaces ``Item[GUID='11404']`` with its own
        pool and later removes that entry. Written against the shipped key, the
        removal found nothing in game and the entry stayed."""
        report = self.read(
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1010']/Ingredient\">"
            "<Ingredient>1012</Ingredient></ModOp>"
            "<ModOp Type=\"remove\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1012']\"/>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0)
        removal = next(c for c in report.changes if c.op == OP_REMOVE)
        self.assertEqual(removal.path, "/Values/Cost/Costs/Item[Ingredient='1012']")

    def test_the_asset_as_shown_applies_a_later_op_s_change_after_the_key_it_finds_by(self):
        """One op replaces an entry's key; a later op changes that entry, found by
        the new key. Applied by kind and then by path, the later change could run
        first -- ``Item[Ingredient='1001']`` sorts before ``'1011'`` -- find
        nothing, and vanish from the asset the Studio shows."""
        report = self.read(
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1011']/Ingredient\">"
            "<Ingredient>1001</Ingredient></ModOp>"
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1001']/Amount\">"
            "<Amount>9</Amount></ModOp>"
        )
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        shown = Workspace(self.index, loadout).effective_asset("1000").as_the_game_reads_it()
        entries = [(i.findtext("Ingredient"), i.findtext("Amount")) for i in shown.iter("Item")]
        self.assertIn(("1001", "9"), entries)

    def test_an_entry_whose_key_an_earlier_op_duplicated_is_found_by_another_field(self):
        """*Serp's tooltip fixes* add a buff entry with the same ``InfoTip`` as a
        shipped one, then change the shipped entry's icon. Keyed by the InfoTip,
        the change matched both entries in game."""
        report = self.read(
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1010</Ingredient><Amount>7</Amount></Item></ModOp>"
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Amount='5']/Amount\">"
            "<Amount>6</Amount></ModOp>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0, [r.reason for r in report.results])
        amount = next(c for c in report.changes if c.op == OP_REPLACE)
        self.assertNotIn("Ingredient='1010'", amount.path)
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        shown = Workspace(self.index, loadout).effective_asset("1000").as_the_game_reads_it()
        self.assertEqual(
            [(i.findtext("Ingredient"), i.findtext("Amount")) for i in shown.find("Values/Cost/Costs")],
            [("1010", "6"), ("1011", "8"), ("1010", "7")],
        )

    def test_an_entry_no_one_field_picks_out_is_found_by_two(self):
        """*Serp's tooltip fixes* change a buff entry whose every field is shared
        by then, with the entry the mod added or with a shipped neighbour; the mod
        itself finds it by two of them."""
        report = self.read(
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1010</Ingredient><Amount>9</Amount></Item>"
            "<Item><Ingredient>1012</Ingredient><Amount>5</Amount></Item></ModOp>"
            "<ModOp Type=\"remove\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1010' and Amount='5']\"/>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0, [r.reason for r in report.results])
        removal = next(c for c in report.changes if c.op == OP_REMOVE)
        self.assertEqual(removal.path, "/Values/Cost/Costs/Item[Ingredient='1010' and Amount='5']")
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        shown = Workspace(self.index, loadout).effective_asset("1000").as_the_game_reads_it()
        self.assertEqual(
            [(i.findtext("Ingredient"), i.findtext("Amount")) for i in shown.find("Values/Cost/Costs")],
            [("1011", "8"), ("1010", "9"), ("1012", "5")],
        )

    def test_a_key_the_op_itself_replaces_is_still_found_by_the_shipped_key(self):
        result = self.only(
            "<ModOp Type=\"replace\" GUID=\"1000\" Path=\"/Values/Cost/Costs/Item[Ingredient='1010']\">"
            "<Item><Ingredient>1012</Ingredient><Amount>9</Amount></Item></ModOp>"
        )
        self.assertEqual(
            sorted(c.path for c in result.changes),
            ["/Values/Cost/Costs/Item[Ingredient='1010']/Amount",
             "/Values/Cost/Costs/Item[Ingredient='1010']/Ingredient"],
        )

    def test_an_asset_s_values_swapped_whole_is_written_as_one_replace(self):
        """Removal and insertion lose the asset in game in either order: first,
        nothing finds it by its GUID; second, the removal takes both copies.
        *New Docklands* merges a positional ``<Item/>`` into a wharf's Values."""
        loadout = Loadout()
        loadout.set_change(Change(guid="1000", asset_label="alpha", path="/Values",
                                  tag="Values", op=OP_REMOVE))
        loadout.set_change(Change(guid="1000", asset_label="alpha", path="/Template",
                                  tag="Values", op=OP_ADD_NEXT,
                                  payload="<Values><Standard><GUID>1000</GUID></Standard></Values>"))
        xml = generator.build_modops_xml(loadout)
        ops = etree.fromstring(xml.encode("utf-8")).findall("ModOp")
        self.assertEqual([(op.get("Type"), op.get("Path")) for op in ops], [("replace", "/Values")])
        self.assertEqual([child.tag for child in ops[0]], ["Values"])

    def test_a_removal_of_values_put_back_by_the_same_changes_stays_first(self):
        """*New Docklands* merges into a wharf's ``Values``, read as removing
        ``/Values`` and inserting a new one. Moved after the insert, the removal
        matched both and ten more ops found nothing."""
        removal = Change(guid="1000", asset_label="alpha", path="/Values",
                         tag="Values", op=OP_REMOVE)
        insert = Change(guid="1000", asset_label="alpha", path="/Template",
                        tag="Values", op=OP_ADD_NEXT, payload="<Values><Standard/></Values>")
        self.assertEqual(emit_order([insert, removal]), [removal, insert])

    def test_removals_are_written_before_the_insertions(self):
        loadout = Loadout()
        for change in self.read(TestOpsRunInOrder.MOVE).changes:
            loadout.set_change(change)
        xml = generator.build_modops_xml(loadout)
        self.assertLess(xml.index('Type="remove"'), xml.index("addPrevSibling"))

    def test_a_run_of_entries_is_written_inside_one_modop(self):
        loadout = Loadout()
        for change in self.imported(
            "<ModOp Append=\"@1000/ProductList/List/Item[Product = '2001']\">"
            "<Item><Product>2007</Product></Item>"
            "<Item><Product>2008</Product></Item></ModOp>"
        ):
            loadout.set_change(change)
        xml = generator.build_modops_xml(loadout)
        root = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(len(root.findall("ModOp")), 1)
        self.assertEqual(len(root.findall("ModOp/Item")), 2)

    def test_the_generated_mod_is_checked_against_the_source_asset(self):
        loadout = Loadout()
        for change in self.read(TestOpsRunInOrder.MOVE).changes:
            loadout.set_change(change)
        problems = [r for r in generator.verify_changes(self.index, loadout) if not r.ok]
        self.assertEqual(problems, [])


class TestTheEffectiveTree(ImportCase):
    """What the editor shows has to be what the mod does."""

    def _effective(self, body: str):
        loadout = Loadout()
        for change in self.read(body).changes:
            loadout.set_change(change)
        asset = effective.build(
            self.index.asset_element("1000"), loadout.for_asset("1000"), "1000"
        )
        kept = [
            item.findtext("Product")
            for item in asset.element.findall("Values/ProductList/List/Item")
            if not asset.origin(item).is_removed
        ]
        return asset, kept

    def test_a_moved_entry_shows_in_its_new_place(self):
        _asset, kept = self._effective(TestOpsRunInOrder.MOVE)
        self.assertEqual(kept, ["2003", "2001", "2002"])

    def test_an_inserted_run_keeps_its_order(self):
        _asset, kept = self._effective(
            "<ModOp Append=\"@1000/ProductList/List/Item[Product = '2001']\">"
            "<Item><Product>2007</Product></Item>"
            "<Item><Product>2008</Product></Item></ModOp>"
        )
        self.assertEqual(kept, ["2001", "2007", "2008", "2002", "2003"])

    def test_an_insertion_does_not_move_the_provenance_of_its_neighbours(self):
        """Origins used to be filed by tree path, and inserting a sibling
        shifts every following node's path -- so the entries after an insertion
        came back wearing their neighbours' provenance."""
        asset, _kept = self._effective(TestOpsRunInOrder.MOVE)
        items = asset.element.findall("Values/ProductList/List/Item")
        marked = {
            item.findtext("Product"): asset.origin(item).provenance for item in items
        }
        self.assertEqual(marked["2001"], effective.PROV_BASE)
        self.assertEqual(marked["2002"], effective.PROV_BASE)
        self.assertEqual(
            sorted(
                provenance
                for product, provenance in
                [(i.findtext("Product"), asset.origin(i).provenance) for i in items]
                if product == "2003"
            ),
            [effective.PROV_ADDED, effective.PROV_REMOVED],
        )


class TestReadingTheFileItself(ImportCase):
    def test_a_bare_group_is_only_a_bracket_around_ops(self):
        report = self.read(
            "<Group><ModOp Merge=\"@1000/Building\"><Radius>25</Radius></ModOp>"
            '<ModOp Merge="@1001/Building"><Radius>9</Radius></ModOp></Group>'
        )
        self.assertEqual(len(report.results), 2)
        self.assertEqual(report.extras, [])

    def test_a_group_with_its_own_target_is_flagged_rather_than_flattened(self):
        report = self.read(
            '<Group GUID="1000" Condition="@1000/Building">'
            '<ModOp Merge="/Values/Building"><Radius>25</Radius></ModOp></Group>'
        )
        # Not read -- and not nothing either: it takes a place among the ops as
        # one the Studio could not read, so that an export can put it back.
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertEqual(report.results[0].changes, [])
        self.assertEqual(len(report.extras), 1)
        self.assertIn("Condition", report.extras[0].what)
        self.assertIn("the 1 op inside it", report.extras[0].detail)

    # -- a group with only a condition is a scope, and scopes are read ------
    GROUPED = '<ModOp Merge="@1001/Building"><Radius>25</Radius></ModOp>'

    def test_a_group_whose_condition_holds_is_read_as_the_ops_in_it(self):
        """The loader asks a group's question once and then runs what is in it.
        About the game's files that is answered for good, as an op's own
        condition is: 1,896 of these in the Anno 1800 mods on mod.io used to be
        carried whole and shown as nothing."""
        report = self.read('<Group Condition="@1000/Building">%s</Group>' % self.GROUPED)
        self.assertEqual([r.status for r in report.results], [modops.SETTLED, modops.IMPORTED])
        self.assertEqual([(c.guid, c.new_value) for c in report.changes], [("1001", "25")])
        self.assertEqual(report.extras, [])
        self.assertEqual(report.unread("m"), [])

    def test_and_one_whose_condition_does_not_hold_runs_nothing(self):
        report = self.read('<Group Condition="@1000/Nothing">%s</Group>' % self.GROUPED)
        self.assertEqual([r.status for r in report.results], [modops.SETTLED, modops.SETTLED])
        self.assertEqual(report.changes, [])
        self.assertIn("does not run", report.results[1].reason)

    def test_a_bare_path_on_a_group_starts_at_the_top_of_the_document(self):
        """A group has no asset for ``/Values/…`` to start from, so it is an
        absolute path, and the top of assets.xml is ``<AssetList>``. *Dockland
        Redux* guards two groups that way, one with a ``!``."""
        report = self.read("<Group Condition=\"/Values[Standard/GUID='1000']/Building\">%s</Group>"
                           % self.GROUPED)
        self.assertEqual([r.status for r in report.results], [modops.SETTLED, modops.SETTLED])
        report = self.read("<Group Condition=\"!/Values[Standard/GUID='1000']/Building\">%s</Group>"
                           % self.GROUPED)
        self.assertEqual([(c.guid, c.new_value) for c in report.changes], [("1001", "25")])
        self.assertIsNone(modops.condition_holds(
            "/AssetList/Assets", self.index.asset_element, in_document=True))

    def test_the_question_is_put_once_on_the_way_in(self):
        """Not per op: the first op here adds the very thing the group asks
        after, and the second still runs, as it does in game."""
        report = self.read(
            '<Group Condition="!@1000/Building/Extra">'
            '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra/></ModOp>'
            "%s</Group>" % self.GROUPED
        )
        self.assertEqual(
            [r.status for r in report.results], [modops.SETTLED, modops.IMPORTED, modops.IMPORTED]
        )

    def test_a_group_inside_a_group_answers_to_both(self):
        report = self.read(
            '<Group Condition="@1000/Building"><Group Condition="@1000/Nothing">%s</Group>'
            '<ModOp Merge="@1000/Building"><Radius>26</Radius></ModOp></Group>' % self.GROUPED
        )
        self.assertEqual([(c.guid, c.new_value) for c in report.changes], [("1000", "26")])

    def test_one_that_cannot_be_answered_is_carried_whole_and_says_so_once(self):
        report = self.read(
            "<Group Condition=\"//Values[Standard/GUID='1000']/Building\">%s%s</Group>"
            % (self.GROUPED, self.GROUPED)
        )
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertIn("not a question the Studio can answer", report.results[0].reason)
        (entry,) = report.unread("m")
        self.assertEqual(entry.source.count("<ModOp"), 2)

    def test_anything_that_is_not_a_modop_is_flagged(self):
        report = self.read("<Include File='other.xml' />")
        self.assertEqual(report.results, [])
        self.assertEqual(report.extras[0].what, "<Include other.xml>")
        self.assertIn("nothing to resolve the path against", report.extras[0].detail)


class TestReadingAModFolder(unittest.TestCase):
    """A mod is a folder, and most of what is in one is not an asset patch."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.base = tmp / "assets.xml"
        cls.base.write_text(IMPORT_ASSETS, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.base, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.base)

        cls.mod = tmp / "[Fix] Someone Else's Mod"
        patch = cls.mod / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            modops_file('<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'),
            encoding="utf-8",
        )
        texts = cls.mod / "data/base/config/gui/texts_english.xml"
        texts.parent.mkdir(parents=True)
        texts.write_text(
            modops_file("<ModOp Merge=\"//Text[LineId = '1']\"><Text/></ModOp>"),
            encoding="utf-8",
        )
        (cls.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "Someone Else\'s Mod"}, '
            '"CreatorName": "Taludas", "Version": "3.7.0", '
            '"Category": {"English": "Fix"}}',
            encoding="utf-8",
        )
        (cls.mod / "ReadMe.txt").write_text("hello", encoding="utf-8")
        graphics = cls.mod / "data/graphics/thing"
        graphics.mkdir(parents=True)
        for name in ("a.cfg", "b.cfg", "c.ifo"):
            (graphics / name).write_bytes(b"binary")
        inner = cls.mod / "shared_bits"
        (inner / "data/base/config/export").mkdir(parents=True)
        (inner / "modinfo.json").write_text("{}", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def setUp(self):
        self.report = reader.analyse(self.mod, self.index)

    def _extra(self, needle: str):
        for extra in self.report.extras:
            if needle in extra.what or needle in extra.where:
                return extra
        self.fail("no extra mentioning %r in %s" % (needle, self.report.extras))

    def test_the_asset_patch_is_the_one_file_that_is_read(self):
        self.assertEqual([p.name for p in self.report.patch_files], ["assets.xml"])
        self.assertEqual(len(self.report.results), 1)
        self.assertEqual(self.report.results[0].status, modops.IMPORTED)

    def test_the_patch_s_own_path_says_which_game_it_is_for(self):
        """This mod's patch sits at data/base/config/export/assets.xml -- Anno
        117's convention -- independent of anything it claims about itself,
        which here is nothing at all."""
        self.assertEqual(self.report.game_hint, games.ANNO_117)

    def test_a_localisation_patch_is_said_to_be_read_for_names(self):
        """Not imported as changes -- read for what the mod calls things, in
        the language the game's texts are loaded in (core.texts)."""
        extra = self._extra("texts_english.xml")
        self.assertEqual(extra.what, "a localisation patch")
        self.assertIn("read for the names it gives", extra.detail)

    def test_replaced_game_files_are_grouped_by_kind(self):
        extra = self._extra(".cfg")
        self.assertIn("2 .cfg files", extra.what)
        self.assertIn("replaces a game file outright", extra.detail)

    def test_documentation_is_not_reported_as_a_replaced_game_file(self):
        self.assertIn("alongside", self._extra(".txt").detail)

    def test_a_mod_bundled_inside_a_mod_is_called_out(self):
        """And is read as a mod in its own right, because that is what the game
        does with it -- with its own place in load order, routinely nowhere near
        its host's."""
        extra = self._extra("shared_bits")
        self.assertIn("bundled inside this one", extra.what)
        self.assertIn("mod of its own", extra.detail)

    def test_the_modinfo_is_read_for_the_loadout_s_own_details(self):
        found = dict(self.report.metadata)
        found.pop("declared", None)
        self.assertEqual(
            found,
            {
                "mod_name": "Someone Else's Mod",
                "creator": "Taludas",
                "version": "3.7.0",
                "category": "Fix",
            },
        )

    def test_and_the_file_itself_is_kept_beside_what_was_read_out_of_it(self):
        """The six fields the Studio models are not the whole file, and writing
        a mod back out from six of a dozen deletes the rest."""
        self.assertEqual(
            self.report.metadata["declared"]["ModName"],
            {"English": "Someone Else's Mod"},
        )

    def test_patches_the_game_reads_though_they_are_not_well_formed(self):
        """What the game's loader reads and a strict parser refuses.

        Each case was tried against the loader itself (xmltest) first; it
        applies the op in every one, including a tag left open, where it
        complains and applies what it got through."""
        op = '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
        cases = {
            "'--' inside a comment": ("<ModOps><!-- a -- b -->%s</ModOps>" % op).encode(),
            "text that is not UTF-8": (
                b"<ModOps><!-- " + "创建".encode("gbk") + b" -->" + op.encode() + b"</ModOps>"
            ),
            "a bare '&'": (
                '<ModOps>%s<ModOp Merge="@1001/Standard"><Name>a & b</Name></ModOp></ModOps>' % op
            ).encode(),
            "'<' inside an attribute": (
                b'<ModOps><ModOp Merge="@1000/Building" Condition="@1000/Building[Radius < 99]">'
                b"<Radius>25</Radius></ModOp></ModOps>"
            ),
            "a broken structure": ("<ModOps>%s</ModOps>\n<ModOps/>" % op).encode(),
        }
        for repair, body in cases.items():
            with self.subTest(repair=repair), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "a mod"
                patch = root / "data/base/config/export/assets.xml"
                patch.parent.mkdir(parents=True)
                patch.write_bytes(body)
                report = reader.analyse(root, self.index)

                self.assertGreaterEqual(report.count(modops.IMPORTED), 1, report.extras)
                noted = [e for e in report.extras if "not well-formed" in e.detail]
                self.assertEqual(len(noted), 1, report.extras)
                self.assertIn(repair, noted[0].detail)

    def test_a_comment_is_passed_over_whole_by_the_lenient_read(self):
        """Ended at its first ``>``, what was left of the comment was walked
        as a tag, its apostrophe opened a quote that ran to the end of the
        file, and the read came back with no ops at all."""
        body = (
            b"<ModOps><!-- a > b <- don't -->"
            b'<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
            b"""<ModOp Merge="@1001/Standard" Condition="@1001/Standard[Name='beta']">"""
            b"<Name>a & b</Name></ModOp></ModOps>"
        )
        root, repairs = modops._read_leniently(body)
        self.assertEqual(len(root.findall("ModOp")), 2)
        self.assertEqual(repairs, ["a bare '&'"])

    def test_a_greater_than_in_an_attribute_is_no_repair(self):
        """Legal there, and escaped all the same: a file repaired for a bare
        ``&`` was said to have had a ``<`` inside an attribute too."""
        body = (
            b'<ModOps><ModOp Merge="@1000/Building" Condition="@1000/Building[Radius > 3]">'
            b"<Radius>25</Radius></ModOp>"
            b'<ModOp Merge="@1001/Standard"><Name>a & b</Name></ModOp></ModOps>'
        )
        root, repairs = modops._read_leniently(body)
        self.assertEqual(repairs, ["a bare '&'"])
        self.assertEqual(root.find("ModOp").get("Condition"), "@1000/Building[Radius > 3]")
        # And a '<' there is still one.
        root, repairs = modops._read_leniently(body.replace(b"Radius > 3", b"Radius < 99"))
        self.assertEqual(repairs, ["a bare '&'", "'<' inside an attribute"])
        self.assertEqual(root.find("ModOp").get("Condition"), "@1000/Building[Radius < 99]")

    def test_the_names_an_export_wrote_come_back_with_the_assets(self):
        """A mod the Studio exported, read in again: the in-game names of the
        assets it adds are in its texts files, in a block the next export
        rewrites from the changes -- which had none, so it wrote it out empty."""
        from anno_modding_studio.core import texts
        from anno_modding_studio.core.games import ANNO_117

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a mod"
            patch = root / "data/base/config/export/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(
                modops_file(
                    '<ModOp Type="addNextSibling" GUID="1000"><Asset><BaseAssetGUID>1000</BaseAssetGUID>'
                    "<Values><Standard><GUID>2001000000</GUID><Name>mine</Name></Standard>"
                    "</Values></Asset></ModOp>"
                ),
                encoding="utf-8",
            )
            names = root / "data/base/config/gui/texts_english.xml"
            names.parent.mkdir(parents=True)
            names.write_text(
                "<ModOps>\n%s\n</ModOps>\n"
                % texts.names_block(ANNO_117, [("2001000000", "Mine & Yours")]),
                encoding="utf-8",
            )
            report = reader.analyse(root, self.index)

        made = [change for change in report.changes if change.creates_asset]
        self.assertEqual([change.in_game_name for change in made], ["Mine & Yours"])

    def test_an_include_the_game_reads_though_it_is_not_well_formed(self):
        """*Obsidian Weapon Extended* keeps three of its ops files with comments
        saved in GBK, and the Studio used to skip all three."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a mod"
            folder = root / "data/base/config/export"
            folder.mkdir(parents=True)
            (folder / "assets.xml").write_text(
                modops_file('<Include File="more.include.xml"/>'), encoding="utf-8"
            )
            (folder / "more.include.xml").write_bytes(
                b"<ModOps><!-- " + "创建".encode("gbk") + b" -->"
                b'<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp></ModOps>'
            )
            report = reader.analyse(root, self.index)

        self.assertEqual(report.count(modops.IMPORTED), 1, report.extras)
        noted = [e for e in report.extras if "not well-formed" in e.detail]
        self.assertEqual([e.where for e in noted], ["data/base/config/export/more.include.xml"])

    def test_a_modinfo_that_is_not_utf8_is_read_rather_than_crashed_on(self):
        """Eleven mods on mod.io save their modinfo in a Chinese codepage.

        Read strictly, one raised UnicodeDecodeError out of the middle of
        reading a whole mods folder, and every other mod in it went too."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a mod"
            patch = root / "data/base/config/export/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(
                modops_file('<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'),
                encoding="utf-8",
            )
            (root / "modinfo.json").write_bytes(
                '{"ModID": "gbk-mod", "Version": "1.0", '
                # The full-width comma is how these descriptions are punctuated.
                '"ModName": {"Chinese": "黑曜石", "English": "Obsidian\uff0cExtended"}}'.encode("gbk")
            )
            report = reader.analyse(root, self.index)

        self.assertEqual(report.error, "")
        self.assertEqual(report.count(modops.IMPORTED), 1)
        self.assertEqual(
            (report.metadata["mod_id"], report.metadata["version"]), ("gbk-mod", "1.0")
        )
        self.assertIn("Obsidian", report.metadata["mod_name"])
        extra = next(e for e in report.extras if "not UTF-8" in e.what)
        self.assertEqual(extra.where, "modinfo.json")

    def test_a_folder_with_no_asset_patch_says_so(self):
        report = reader.analyse(self.mod / "shared_bits", self.index)
        self.assertIn("No assets.xml patch", report.error)

    def test_a_modinfo_written_with_comments_is_still_a_modinfo(self):
        """``modinfo.jsonc`` is what the community's editor extension writes and
        what the Anno 117 loader takes.  Refusing it leaves an ordinary mod
        looking like one with no name, no creator and no ModID -- which, once
        the mod profile keys on ModID, is a mod reported as switched off."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a mod"
            patch = root / "data/config/export/main/asset/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text("<ModOps/>", encoding="utf-8")
            (root / "modinfo.jsonc").write_text(
                "// what this mod is\n"
                '{\n  "ModID": "someone-elses-mod",  // keyed on by profiles\n'
                '  /* the shown name */\n  "ModName": {"English": "Commented"},\n'
                '  "Description": {"English": "see https://example.com/x"}\n}\n',
                encoding="utf-8",
            )
            report = reader.analyse(root, self.index)
        self.assertEqual(report.metadata.get("mod_id"), "someone-elses-mod")
        self.assertEqual(report.metadata.get("mod_name"), "Commented")
        # A URL is not the start of a comment.
        self.assertIn("https://example.com/x", report.metadata.get("description"))

    def test_the_summary_counts_every_outcome(self):
        report = modops.ImportReport(
            results=[
                modops.OpResult(
                    modops.SourceOp(),
                    modops.IMPORTED,
                    changes=[Change("1", "alpha", "/Values/A", "A")],
                ),
                modops.OpResult(modops.SourceOp(), modops.SETTLED),
                modops.OpResult(modops.SourceOp(), modops.BLOCKED),
            ]
        )
        self.assertEqual(
            report.summary(),
            "1 change from 1 ModOp; 1 already true of your assets.xml; 1 need a look",
        )

class TestOpsThatNameSeveralAssets(ImportCase):
    """``GUID="1000,1001"`` is one ModOp making the same edit to each."""

    def test_each_asset_gets_its_own_change(self):
        changes = self.imported(
            '<ModOp Type="merge" GUID="1000,1001" Path="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual(
            sorted((c.guid, c.new_value) for c in changes),
            [("1000", "30"), ("1001", "30")],
        )

    def test_it_is_still_reported_as_the_one_modop_it_was_written_as(self):
        report = self.read(
            '<ModOp Type="merge" GUID="1000,1001" Path="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual(len(report.results), 1)

    def test_an_asset_it_already_matches_is_named_rather_than_dropped(self):
        result = self.only(
            '<ModOp Type="merge" GUID="1000,1001" Path="/Values/Building">'
            "<Radius>4</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED)
        self.assertIn("already true of 1001", result.reason)
        self.assertEqual([c.guid for c in result.changes], ["1000"])

    def test_a_guid_that_is_not_there_does_not_take_the_others_with_it(self):
        result = self.only(
            '<ModOp Type="merge" GUID="1000,9999" Path="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED)
        self.assertIn("9999", result.reason)
        self.assertEqual([c.guid for c in result.changes], ["1000"])

    def test_kept_as_itself_it_is_kept_once_and_for_all_of_them(self):
        """Whether an op goes out as itself is a question about the op.  It was
        asked of each asset's share: one share kept made a kept op whose source
        is the whole ModOp, so it went out for every asset it names while the others
        were written narrow as well.  A copy is the simplest op that is kept."""
        translator = reader.Translator(self.index, True)
        root = etree.fromstring(modops_file(
            '<ModOp Type="merge" GUID="2001,2002" Path="/Values/Standard/Name" '
            'Content="@2003/Standard/Name"></ModOp>'
        ).encode("utf-8"))
        (result,) = translator.run_all(modops.parse_ops(root, [], "assets.xml"))
        kept = translator.found.get("", [])
        self.assertEqual(len(kept), 1, [op.source for op in kept])
        self.assertEqual(sorted(kept[0].matched), ["2001", "2002"])
        self.assertEqual(sorted(c.guid for c in result.changes), ["2001", "2002"])
        self.assertEqual({c.op_uid for c in result.changes}, {kept[0].uid})

    def test_a_space_after_the_comma_is_part_of_the_guid_and_finds_nothing(self):
        """Measured, and against every guess: the loader splits the list on the
        comma and looks up what is left, space and all.  *Unlock Coal Mine at 1
        Worker* names ``500005, 500145`` and reaches only the first of them in
        game, so reading it as both wrote an export that did more than the mod."""
        result = self.only(
            '<ModOp Type="merge" GUID="1000, 1001" Path="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual([c.guid for c in result.changes], ["1000"])
        self.assertIn("not read for ' 1001'", result.reason)
        self.assertIn("split on the comma and nothing else", result.reason)

    def test_and_one_written_that_way_alone_is_carried_whole(self):
        report = self.read(
            '<ModOp Type="merge" GUID=" 1001 " Path="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertEqual(report.changes, [])
        self.assertEqual(len(report.unread("m")), 1)


class TestPathsAsModsActuallyWriteThem(ImportCase):
    """The leading slash is optional, and mods use both spellings."""

    def test_a_union_that_searches_the_file_goes_back_out_as_written(self):
        """Both loaders, measured, change the other asset too; *Hacienda
        Overhaul* lost one from its export when it was read as one."""
        result = self.only(
            "<ModOp Type=\"add\" GUID=\"1000\" Path=\"/Values/Building | "
            "//Values[Standard/GUID='1001']/Building\"><Extra/></ModOp>"
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("searches the whole file", result.reason)
        self.assertFalse(modop.reaches_other_assets("/Values/Building[Name='a | //b']"))
        self.assertFalse(modop.reaches_other_assets("self::Radius | self::BuildingType"))

    def test_a_path_with_no_leading_slash(self):
        changes = self.imported(
            '<ModOp Type="replace" GUID="1000" Path="Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(changes[0].path, "/Values/Building/Radius")

    def test_the_117_short_form_with_no_leading_slash(self):
        changes = self.imported(
            '<ModOp Type="replace" GUID="1000" Path="Building/Radius">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(changes[0].path, "/Values/Building/Radius")

    def test_a_trailing_slash_is_not_a_step(self):
        changes = self.imported(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(changes[0].path, "/Values/Building/Radius")

    def test_the_asset_itself_is_written_as_no_path_at_all(self):
        """A mod may aim an op at the asset element with ``Path="/"``.

        That is the asset itself, which the Studio spells as an empty path --
        "/" on its own is neither a ModOp path nor a legal XPath expression, and
        one written down would be a change nothing could ever resolve.
        """
        changes = self.imported(
            '<ModOp Type="merge" GUID="1001" Path="/">'
            "<Template>Renamed</Template></ModOp>"
        )
        self.assertNotIn("/", changes[0].path.strip("/") or "x")
        asset = self.index.asset_element("1001")
        self.assertEqual(
            [node.tag for node in paths.resolve(asset, changes[0].path)], ["Template"]
        )

    def test_and_an_empty_path_resolves_to_the_asset(self):
        asset = self.index.asset_element("1001")
        self.assertEqual(paths.resolve(asset, ""), [asset])
        self.assertEqual(paths.resolve(asset, "/"), [asset])


class TestConditionsAsModsActuallyWriteThem(ImportCase):
    """A bare path in a Condition is read against the op's own asset."""

    MERGE = '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="%s">' \
            "<Radius>30</Radius></ModOp>"

    def test_a_condition_that_holds_of_its_own_asset(self):
        result = self.only(self.MERGE % "/Values/Building/Radius")
        self.assertEqual(result.status, modops.IMPORTED)

    def test_a_condition_that_does_not(self):
        result = self.only(self.MERGE % "/Values/Nonsense")
        self.assertEqual(result.status, modops.SETTLED)

    def test_a_negated_one(self):
        result = self.only(self.MERGE % "!/Values/Nonsense")
        self.assertEqual(result.status, modops.IMPORTED)

    def test_text_found_is_something_found(self):
        """The loader's docs: a condition holds on "a non-empty list", and
        text is one.  *Community Patch* guards 20 ops with ``…/text()``."""
        self.assertEqual(self.only(self.MERGE % "/Values/Standard/Name/text()").status,
                         modops.IMPORTED)
        self.assertEqual(self.only(self.MERGE % "!/Values/Standard/Name/text()").status,
                         modops.SETTLED)
        self.assertEqual(self.only(self.MERGE % "/Values/Empty/text()").status, modops.SETTLED)
        self.assertTrue(modops.condition_holds(
            "@1000/Standard/Name/text()", self.index.asset_element))

    def test_a_named_asset_is_read_from_its_values(self):
        """``@1000`` is short for ``//Values[Standard/GUID='1000']``, so what
        follows starts inside ``<Values>`` -- measured the same with xmltest."""
        lookup = self.index.asset_element
        self.assertTrue(modops.condition_holds("@1000/Building", lookup))
        self.assertFalse(modops.condition_holds("@1000/Values/Building", lookup))
        self.assertTrue(modops.condition_holds("!@1000/Values/Building", lookup))
        self.assertTrue(modops.condition_holds("@1000", lookup))
        self.assertTrue(modops.condition_holds("@1000/", lookup))
        self.assertFalse(modops.condition_holds("@9999", lookup))

    def test_a_search_inside_a_named_asset_is_left_to_the_game(self):
        """``@1000//Item`` searches the asset by the docs, and finds nothing
        in any xmltest build. Where the search finds nothing the two agree;
        where it finds something the op goes back out as written."""
        lookup = self.index.asset_element
        self.assertIsNone(modops.condition_holds("@1000//Item[Product='2001']", lookup))
        self.assertIsNone(modops.condition_holds("!@1000//Item[Product='2001']", lookup))
        self.assertFalse(modops.condition_holds("@1000//Item[Product='9999']", lookup))
        self.assertTrue(modops.condition_holds("!@1000//Item[Product='9999']", lookup))
        result = self.only(self.MERGE % "!@1000//Item[Product=&apos;2001&apos;]")
        self.assertEqual(result.status, modops.BLOCKED)

    def test_asking_whether_another_mod_is_installed(self):
        """Answerable only when the Studio knows what the whole folder holds."""
        lookup = self.index.asset_element
        self.assertIsNone(modops.condition_holds("#other-mod", lookup, "1000"))
        self.assertFalse(
            modops.condition_holds("#other-mod", lookup, "1000", present=set())
        )
        self.assertTrue(
            modops.condition_holds("#other-mod", lookup, "1000", present={"other-mod"})
        )
        self.assertFalse(
            modops.condition_holds("!#other-mod", lookup, "1000", present={"other-mod"})
        )


class TestConditionsThatCombineMods(ImportCase):
    """Anno 117's loader turns each ``#mod-id`` into ``true()`` or ``false()``
    and evaluates what is left, so ``#mod-a or #mod-b`` asks after two mods.
    Read as a single ModID it came out the other way round: *City Watch City
    Skins* guards an op with ``#…-roof-skins-jtmzac or #taludas-oldroofskins``,
    and with both loaded it read as not running."""

    def holds(self, condition, present=None, chosen=None, game=games.ANNO_117):
        return modops.condition_holds(
            condition, self.index.asset_element, "1000", present, chosen, game
        )

    def test_either_of_two_mods(self):
        self.assertTrue(self.holds("#mod-a or #mod-b", {"mod-a", "mod-b"}))
        self.assertTrue(self.holds("#mod-a or #mod-b", {"mod-b"}))
        self.assertFalse(self.holds("#mod-a or #mod-b", set()))

    def test_one_mod_and_not_another(self):
        self.assertTrue(self.holds("#mod-a and not(#mod-b)", {"mod-a"}))
        self.assertFalse(self.holds("#mod-a and not(#mod-b)", {"mod-a", "mod-b"}))

    def test_a_leading_not_turns_the_whole_of_it_round(self):
        """The loader takes the ``!`` off first and negates the answer."""
        self.assertFalse(self.holds("!#mod-a or #mod-b", {"mod-b"}))
        self.assertTrue(self.holds("!#mod-a or #mod-b", set()))

    def test_a_not_inside_is_not_xpath_and_is_not_answered(self):
        self.assertIsNone(self.holds("#mod-a and !#mod-b", {"mod-a"}))

    def test_without_a_mods_folder_there_is_no_answer(self):
        self.assertIsNone(self.holds("#mod-a or #mod-b", None))

    def test_a_setting_among_them(self):
        self.assertTrue(
            self.holds("#mod-a and $useinfluence", {"mod-a"}, {"useinfluence": "True"})
        )
        self.assertFalse(
            self.holds("#mod-a and $useinfluence", {"mod-a"}, {"useinfluence": "false"})
        )
        self.assertTrue(self.holds("#mod-a and $range > 5", {"mod-a"}, {"range": "10"}))
        # Not declared, or not a yes, a no or a number: not answered.
        self.assertIsNone(self.holds("#mod-a and $range > 5", {"mod-a"}, {}))
        self.assertIsNone(self.holds("#mod-a and $mode", {"mod-a"}, {"mode": "fast"}))

    def test_a_path_among_them_is_not_answered(self):
        self.assertIsNone(self.holds("#mod-a and /Values/Building", {"mod-a"}))

    def test_one_name_with_and_in_it_is_still_one_name(self):
        self.assertTrue(
            self.holds("#vegetables-and-cheese", {"vegetables-and-cheese"})
        )

    def test_anno_1800_reads_it_all_as_one_modid(self):
        """Its loader looks up everything after the ``#``, spaces and all."""
        self.assertFalse(
            self.holds("#mod-a or #mod-b", {"mod-a", "mod-b"}, game=games.ANNO_1800)
        )

    def test_with_no_game_there_is_no_telling_which(self):
        self.assertIsNone(self.holds("#mod-a or #mod-b", {"mod-a"}, game=""))


class TestAddingTextRatherThanNodes(ImportCase):
    """``<ModOp Type="add" …>;Mod1</ModOp>`` extends what a field says."""

    def test_it_becomes_the_field_s_new_value(self):
        changes = self.imported(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building/BuildingType">'
            ";Extra</ModOp>"
        )
        self.assertEqual(changes[0].op, OP_REPLACE)
        self.assertEqual(
            (changes[0].old_value, changes[0].new_value), ("Factory", "Factory;Extra")
        )

    def test_an_op_with_nothing_in_it_at_all_is_not_a_refusal(self):
        result = self.only('<ModOp Type="add" GUID="1000" Path="/Values/Building"/>')
        self.assertEqual(result.status, modops.SETTLED)
        self.assertIn("nothing in it", result.reason)


class TestNewAssetsOnEitherSide(ImportCase):
    """A whole <Asset> beside an asset is how a mod introduces one."""

    ASSET = (
        "<Asset><Template>Thing</Template><Values><Standard>"
        "<GUID>5001</GUID><Name>invented</Name></Standard></Values></Asset>"
    )

    def test_before_the_anchor_as_well_as_after(self):
        changes = self.imported(
            '<ModOp Type="addPrevSibling" GUID="1000">%s</ModOp>' % self.ASSET
        )
        self.assertTrue(changes[0].creates_asset)
        self.assertIn("5001", changes[0].payload)

    def test_something_that_is_not_an_asset_beside_an_asset_is_refused(self):
        result = self.only(
            '<ModOp Type="addPrevSibling" GUID="1000"><Radius>3</Radius></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("only a whole <Asset> can go", result.reason)

    def test_a_guid_introduced_twice_keeps_both_payloads(self):
        """Dropping the displaced one would leave its ModOp with an empty body."""
        second = self.ASSET.replace("invented", "invented again")
        changes = [
            c
            for c in self.read(
                '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self.ASSET
                + '<ModOp Type="addNextSibling" GUID="1001">%s</ModOp>' % second
            ).changes
            if c.creates_asset
        ]
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(change.payload for change in changes))
        self.assertIn("invented again", changes[1].payload)


class TestSayingWhatTheReadingIsDoing(SessionCase):
    """Reading a folder is two halves, and only the first one was reported.

    The bar filled while the mods were read and the file searched, and then sat
    at 100% -- for ten seconds on a real Anno 1800 folder -- while every op was
    answered against the stack, with nothing said about it.
    """

    def read(self):
        root = self.mods(
            One='<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
                "<Radius>9</Radius></ModOp>",
            Two='<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
                "<Radius>7</Radius></ModOp>",
        )
        self.stages = []
        self.translated = []
        self.session(
            root,
            stage=self.stages.append,
            translating=lambda pos, total: self.translated.append((pos, total)),
        )

    def test_the_long_half_is_reported_as_it_goes(self):
        self.read()
        self.assertTrue(self.translated)
        positions = [pos for pos, _total in self.translated]
        self.assertEqual(positions[0], 0)
        # And it ends at the total, so the bar is full when the work is done.
        self.assertEqual(self.translated[-1][0], self.translated[-1][1])

    def test_reading_still_works_with_nobody_listening(self):
        root = self.mods(
            One='<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
                "<Radius>9</Radius></ModOp>",
        )
        self.assertEqual(len(self.session(root).changes), 1)


class TestOneModReadAgainInItsStack(SessionCase):
    """P7-18: changing a mod's settings reads it again -- where it stands.

    Read alone, an op that edits what an earlier mod made aims at an asset
    that is not there; across the census, mods with settings read alone came
    back with other unread ops and other changes than they had in the stack.
    """

    MAKER = (
        '<ModOp Type="addNextSibling" GUID="1000">'
        "<Asset><Template>Thing</Template><Values><Standard>"
        "<GUID>7777</GUID><Name>invented</Name></Standard>"
        "<Building><Radius>5</Radius></Building></Values></Asset></ModOp>"
    )
    USER = '<ModOp Merge="@7777/Building"><Radius><ModValue Insert="$radius"/></Radius></ModOp>'

    def loadout_of(self, root):
        (root / "b user" / "modinfo.json").write_text(
            '{"Options": {"radius": {"type": "slider", "default": "9"}}}', encoding="utf-8"
        )
        loadout = Loadout(game=games.ANNO_117)
        for read in self.session(root, reading=modops.Reading()).reads:
            loadout.set_layer(ModLayer(
                origin=read.origin, folder=str(read.folder), order=read.order,
                options=dict(read.report.options_read),
            ))
        return loadout

    def test_an_op_on_what_an_earlier_mod_made_reads_again_at_the_new_setting(self):
        root = self.mods(a__maker=self.MAKER, b__user=self.USER)
        loadout = self.loadout_of(root)
        alone = reader.analyse(
            root / "b user", self.index, origin="b user",
            reading=modops.Reading(options={"b user": {"radius": "12"}}),
        )
        self.assertEqual(alone.count(modops.BLOCKED), 1)

        again = reader.analyse_in_stack(loadout, "b user", self.index, {"radius": "12"})
        self.assertEqual([r.status for r in again.results], [modops.IMPORTED])
        self.assertEqual([(c.guid, c.new_value) for c in again.changes], [("7777", "12")])
        self.assertEqual(again.options_read, {"radius": "12"})

    def test_a_condition_asking_after_a_later_mod_still_finds_it(self):
        """Only the mods up to it are read again, so the ones after it are
        named as there rather than read: ``#z later`` is still loaded."""
        root = self.mods(
            a__maker=self.MAKER,
            b__user='<ModOp Merge="@1000/Building" Condition="#z later">'
            '<Radius><ModValue Insert="$radius"/></Radius></ModOp>',
            z__later="<ModOp/>",
        )
        loadout = self.loadout_of(root)
        again = reader.analyse_in_stack(loadout, "b user", self.index, {"radius": "12"})
        self.assertEqual([r.status for r in again.results], [modops.IMPORTED])
        self.assertEqual([c.new_value for c in again.changes], ["12"])

    def test_a_mod_named_by_its_link_reads_again_in_its_stack(self):
        """A11-106: a library mod with no link is kept under another name
        than its link's, which it is loaded under. Its stack named it by its
        folder, did not find it, and it was read alone, where the asset the
        mod before it makes is not there."""
        root = self.mods(a__maker=self.MAKER, b__user=self.USER)
        loadout = self.loadout_of(root)
        library = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, library, True)
        kept = library / "b user-1.2"
        shutil.move(str(root / "b user"), str(kept))
        layer = loadout.layer("b user")
        loadout.drop_layer("b user")
        loadout.set_layer(dataclasses.replace(layer, origin="[Gameplay] User", folder=str(kept)))
        again = reader.analyse_in_stack(loadout, "[Gameplay] User", self.index, {"radius": "12"})
        self.assertEqual([r.status for r in again.results], [modops.IMPORTED])
        self.assertEqual([(c.guid, c.new_value) for c in again.changes], [("7777", "12")])

    def test_a_mod_that_is_not_loaded_or_has_no_folder_is_refused(self):
        root = self.mods(a__maker=self.MAKER, b__user=self.USER)
        loadout = self.loadout_of(root)
        with self.assertRaises(ValueError):
            reader.analyse_in_stack(loadout, "nobody", self.index, {})
        loadout.layer("b user").folder = str(root / "gone")
        with self.assertRaises(ValueError):
            reader.analyse_in_stack(loadout, "b user", self.index, {})


class TestAWholeModsFolder(SessionCase):
    """The point of reading a stack rather than a mod: what the game will do."""

    def test_mods_are_read_in_the_order_the_game_loads_them(self):
        root = self.mods(
            b__second='<ModOp Merge="@1000/Building"><Radius>2</Radius></ModOp>',
            a__first='<ModOp Merge="@1000/Building"><Radius>1</Radius></ModOp>',
        )
        session = self.session(root)
        self.assertEqual([read.origin for read in session.reads], ["a first", "b second"])
        self.assertEqual([read.order for read in session.reads], [1, 2])

    def test_every_change_says_which_mod_it_came_from(self):
        root = self.mods(
            a__first='<ModOp Merge="@1000/Building"><Radius>1</Radius></ModOp>',
            b__second='<ModOp Merge="@1001/Building"><Radius>2</Radius></ModOp>',
        )
        session = self.session(root)
        self.assertEqual(
            sorted({change.origin for change in session.changes}),
            ["a first", "b second"],
        )

    def test_a_value_two_mods_both_set_is_a_change_of_each(self):
        """In the stack the second finds the field reading as it wants and
        changes nothing. It is its change all the same: read as none, the mod
        was written back without it, and what an export held depended on what
        else was loaded."""
        for body in (
            '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>',
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>",
        ):
            with self.subTest(body=body):
                session = self.session(self.mods(
                    a__first='<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>',
                    b__second=body,
                ))
                statuses = {r.origin: r.report.results[0].status for r in session.reads}
                self.assertEqual(statuses["b second"], modops.IMPORTED)
                # Against the game's file, as the mod alone would change it.
                self.assertEqual(
                    [(c.origin, c.op, c.path, c.old_value, c.new_value) for c in session.changes],
                    [
                        (name, OP_REPLACE, "/Values/Building/Radius", "10", "25")
                        for name in ("a first", "b second")
                    ],
                )

    def test_and_the_two_are_listed_as_agreeing(self):
        session = self.session(self.mods(
            a__first='<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>',
            b__second='<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>',
        ))
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.origin))
        for change in session.changes:
            loadout.set_change(change)
        self.assertEqual([clash.kind for clash in conflicts.find(loadout)], [conflicts.AGREES])

    def test_a_value_the_game_ships_is_still_no_change_of_anybodys(self):
        session = self.session(self.mods(
            a__first='<ModOp Merge="@1000/Building"><Radius>10</Radius></ModOp>',
            b__second='<ModOp Merge="@1000/Building"><Radius>10</Radius></ModOp>',
        ))
        self.assertEqual(
            [read.report.results[0].status for read in session.reads],
            [modops.SETTLED, modops.SETTLED],
        )

    def test_a_value_a_mod_sets_twice_itself_is_one_change(self):
        session = self.session(self.mods(
            a__first='<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
            '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>',
        ))
        self.assertEqual(len(session.changes), 1)

    def test_a_field_two_mods_both_make_with_one_value_is_a_merge_of_each(self):
        changes = self.second(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
        )
        self.assertEqual(
            [(c.op, c.path, c.payload) for c in changes],
            [(OP_MERGE, "/Values/Building", "<Elevation>3</Elevation>")],
        )

    def second(self, first: str, second: str):
        """The changes of the later of two mods read as a stack."""
        session = self.session(self.mods(a__first=first, b__second=second))
        return [change for change in session.changes if change.origin == "b second"]

    def test_a_merge_setting_a_field_another_mod_made_is_still_a_merge(self):
        """Read as a replace it was written as one, at a path only the other
        mod's work gives the asset: on its own the mod then did nothing, where
        its author's merge makes the field."""
        changes = self.second(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            '<ModOp Merge="@1000/Building"><Elevation>5</Elevation></ModOp>',
        )
        self.assertEqual(
            [(c.op, c.path, c.tag, c.payload) for c in changes],
            [(OP_MERGE, "/Values/Building", "Elevation", "<Elevation>5</Elevation>")],
        )
        self.assertEqual(changes[0].sets, ("/Values/Building/Elevation", "5"))

    def test_and_written_out_alone_it_reads_the_same_without_that_mod(self):
        changes = self.second(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            '<ModOp Merge="@1000/Building"><Elevation>5</Elevation></ModOp>',
        )
        loadout = Loadout()
        for change in changes:
            loadout.set_change(dataclasses.replace(change, origin=""))
        xml = generator.build_modops_xml(loadout)
        alone = self.imported(xml.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0])
        self.assertEqual(
            [(c.op, c.path, c.payload) for c in alone],
            [(c.op, c.path, c.payload) for c in changes],
        )

    def test_the_two_are_a_clash_like_any_two_mods_setting_one_field(self):
        session = self.session(self.mods(
            a__first='<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            b__second='<ModOp Merge="@1000/Building"><Elevation>5</Elevation></ModOp>',
        ))
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.origin))
        for change in session.changes:
            loadout.set_change(change)
        clash = conflicts.find(loadout)[0]
        self.assertEqual(clash.kind, conflicts.OVERWRITES)
        self.assertEqual(clash.path, "/Values/Building/Elevation")
        self.assertEqual(clash.winner.origin, "b second")

    def test_a_merge_into_a_section_another_mod_made_carries_the_section(self):
        """Aimed at the nearest node the game ships, so it lands with or
        without the mod that made the section -- by a path into that section
        it matched nothing without it."""
        made = '<ModOp Merge="@1001/Values"><Cost><Fee>1</Fee></Cost></ModOp>'
        for body in (
            '<ModOp Merge="@1001/Values"><Cost><Tax>2</Tax></Cost></ModOp>',
            '<ModOp Merge="@1001/Cost"><Tax>2</Tax></ModOp>',
        ):
            with self.subTest(body=body):
                changes = self.second(made, body)
                self.assertEqual([(c.op, c.path, c.tag) for c in changes],
                                 [(OP_MERGE, "/Values", "Cost")])
                self.assertEqual(changes[0].sets, ("/Values/Cost/Tax", "2"))

    def test_a_merge_setting_a_field_in_a_section_another_mod_made(self):
        changes = self.second(
            '<ModOp Merge="@1001/Values"><Cost><Fee>1</Fee></Cost></ModOp>',
            '<ModOp Merge="@1001/Cost"><Fee>7</Fee></ModOp>',
        )
        self.assertEqual([(c.op, c.path, c.tag) for c in changes], [(OP_MERGE, "/Values", "Cost")])
        self.assertEqual(changes[0].sets, ("/Values/Cost/Fee", "7"))

    def test_a_replace_of_a_field_another_mod_made_stays_a_replace(self):
        """A replace needs the field there, in the mod's file as here: it is
        the merge that would have made it."""
        changes = self.second(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Elevation">'
            "<Elevation>5</Elevation></ModOp>",
        )
        self.assertEqual([(c.op, c.path) for c in changes],
                         [(OP_REPLACE, "/Values/Building/Elevation")])

    def test_a_replace_restating_a_value_another_mod_gave_its_field_is_carried(self):
        """In the stack it changes nothing, and it is an op the mod has: read
        as nothing, the mod was written back without it."""
        changes = self.second(
            '<ModOp Merge="@1000/Building"><Elevation>3</Elevation></ModOp>',
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Elevation">'
            "<Elevation>3</Elevation></ModOp>",
        )
        self.assertEqual(
            [(c.op, c.path, c.old_value, c.new_value, c.restated) for c in changes],
            [(OP_REPLACE, "/Values/Building/Elevation", "3", "3", True)],
        )
        self.assertFalse(changes[0].is_noop)
        self.assertEqual(changes[0].describe_change(), "3 (restated)")
        # It survives being put in a loadout, saved and loaded, and written out.
        loadout = Loadout()
        loadout.set_change(changes[0])
        self.assertEqual(len(loadout.changes), 1)
        from anno_modding_studio.core.changes import change_from_dict

        self.assertTrue(change_from_dict(dataclasses.asdict(changes[0])).restated)
        xml = generator.build_modops_xml(loadout)
        self.assertIn('Type="replace"', xml)
        self.assertIn("<Elevation>3</Elevation>", xml)

    def test_a_value_typed_back_to_its_original_is_still_no_change(self):
        loadout = Loadout()
        loadout.set_change(Change(guid="1000", asset_label="alpha", tag="Radius",
                                  path="/Values/Building/Radius",
                                  old_value="10", new_value="10"))
        self.assertEqual(len(loadout.changes), 0)

    def test_a_merge_setting_one_of_two_entries_another_mod_made_stays_a_replace(self):
        """A merge finds same-named entries by position, so it cannot be aimed
        past them at the one it means."""
        changes = self.second(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building">'
            "<Slot><N>1</N></Slot><Slot><N>2</N></Slot></ModOp>",
            "<ModOp Type=\"merge\" GUID=\"1000\" Path=\"/Values/Building/Slot[N='2']\">"
            "<N>9</N></ModOp>",
        )
        self.assertEqual([c.op for c in changes], [OP_REPLACE])

    def test_a_mod_may_edit_an_asset_another_mod_invents(self):
        """Read on its own the second mod aims at an asset that does not exist.
        Read as the stack it is part of, it is perfectly ordinary."""
        root = self.mods(
            a__maker='<ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Template>Thing</Template><Values><Standard>"
            "<GUID>7777</GUID><Name>invented</Name></Standard>"
            "<Building><Radius>5</Radius></Building></Values></Asset></ModOp>",
            b__user='<ModOp Merge="@7777/Building"><Radius>9</Radius></ModOp>',
        )
        alone = reader.analyse(root / "b user", self.index)
        self.assertEqual(alone.count(modops.BLOCKED), 1)

        session = self.session(root)
        later = next(read for read in session.reads if read.origin == "b user")
        self.assertEqual(later.report.results[0].status, modops.IMPORTED)
        change = later.changes[0]
        self.assertEqual((change.guid, change.new_value), ("7777", "9"))

    def test_and_that_edit_stays_out_of_the_first_mod_s_payload(self):
        """The payload is what gets written into *that* mod's folder, so an
        edit another mod makes must not end up inside it."""
        root = self.mods(
            a__maker='<ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Template>Thing</Template><Values><Standard>"
            "<GUID>7777</GUID><Name>invented</Name></Standard>"
            "<Building><Radius>5</Radius></Building></Values></Asset></ModOp>",
            b__user='<ModOp Merge="@7777/Building"><Radius>9</Radius></ModOp>',
        )
        session = self.session(root)
        maker = next(read for read in session.reads if read.origin == "a maker")
        self.assertIn("<Radius>5</Radius>", maker.changes[0].payload)
        self.assertNotIn("<Radius>9</Radius>", maker.changes[0].payload)

    def test_a_condition_asking_after_another_mod_can_be_answered(self):
        root = self.mods(
            a__other="<ModOp/>",
            b__asker='<ModOp Merge="@1000/Building" Condition="#a other">'
            "<Radius>30</Radius></ModOp>",
            c__asker='<ModOp Merge="@1001/Building" Condition="#not installed">'
            "<Radius>30</Radius></ModOp>",
        )
        session = self.session(root)
        found = {read.origin: read.report.results[0] for read in session.reads}
        self.assertEqual(found["b asker"].status, modops.IMPORTED)
        self.assertEqual(found["c asker"].status, modops.SETTLED)

    def test_a_mod_read_alone_is_present_by_the_names_a_stack_gives_it(self):
        """A11-36: in a stack a mod answers ``#name`` to its ModID and its
        name, and read on its own it answered to the folder it was read from
        as well, so a library mod kept under another name than its link's was
        loaded by that name alone and not in a stack."""
        asks = '<ModOp Merge="@1000/Building" Condition="#%s"><Radius>30</Radius></ModOp>'
        root = self.mods(kept__as=asks % "kept as", linked__as=asks % "linked as")
        for folder, status in (("kept as", modops.SETTLED), ("linked as", modops.IMPORTED)):
            alone = reader.analyse(root / folder, self.index, origin="linked as")
            self.assertEqual(alone.results[0].status, status, folder)
        # With no name of its own, it goes by its folder's.
        alone = reader.analyse(root / "kept as", self.index)
        self.assertEqual(alone.results[0].status, modops.IMPORTED)

    def test_so_can_one_that_combines_mods(self):
        """In a whole mods folder, and in a group as well as on an op."""
        root = self.mods(
            other="<ModOp/>",
            either='<ModOp Merge="@1000/Building" Condition="#other or #missing">'
            "<Radius>30</Radius></ModOp>",
            both='<ModOp Merge="@1001/Building" Condition="#other and #missing">'
            "<Radius>31</Radius></ModOp>",
            grouped='<Group Condition="#missing or #other">'
            '<ModOp Merge="@1001/Building"><Radius>32</Radius></ModOp></Group>',
        )
        session = self.session(root)
        found = {read.origin: read.report for read in session.reads}
        self.assertEqual(found["either"].results[0].status, modops.IMPORTED)
        self.assertEqual(found["both"].results[0].status, modops.SETTLED)
        self.assertEqual([c.new_value for c in found["grouped"].changes], ["32"])
        # Which mods are loaded can change, so the question goes back out.
        for origin in ("either", "both", "grouped"):
            self.assertIn("which other mods are loaded", found[origin].ops[0].kept)

    def test_and_the_question_goes_back_out_with_the_op(self):
        """A condition about the game's files is answered once, because the
        files do not change under a mod.  Which other mods are loaded does: an
        op written back without the question goes on running after the mod it
        asked about has been taken out, and one left out because that mod was
        not there never runs when it is put in.  *Noblesse Oblige* guards an
        effect target with ``#shared-pools-and-definitions``."""
        root = self.mods(
            a__other="<ModOp/>",
            b__asker='<ModOp Merge="@1000/Building" Condition="#a other">'
            "<Radius>30</Radius></ModOp>",
            c__asker='<ModOp Merge="@1001/Building" Condition="!#a other">'
            "<Radius>31</Radius></ModOp>",
        )
        session = self.session(root)
        found = {read.origin: read for read in session.reads}
        runs, idle = found["b asker"].report, found["c asker"].report
        # Read either way: the editor shows what the game does today.
        self.assertEqual([c.new_value for c in runs.changes], ["30"])
        self.assertEqual(idle.changes, [])
        for report, says in ((runs, "which other mods are loaded"), (idle, "it does nothing")):
            self.assertEqual(len(report.ops), 1)
            self.assertIn(says, report.ops[0].kept)
            self.assertIn('Condition="', report.ops[0].source)
            self.assertIn("#a other", report.ops[0].source)

    def test_a_folder_with_no_asset_patch_is_named_rather_than_ignored(self):
        root = self.mods(a__real='<ModOp Merge="@1000/Building"><Radius>1</Radius></ModOp>')
        (root / "z docs").mkdir()
        (root / "z docs" / "readme.txt").write_text("hello", encoding="utf-8")
        session = self.session(root)
        self.assertEqual([folder.name for folder, _why in session.skipped], ["z docs"])

    def test_tooling_folders_are_not_mods(self):
        root = self.mods(a__real='<ModOp Merge="@1000/Building"><Radius>1</Radius></ModOp>')
        (root / ".vscode").mkdir()
        self.assertEqual([p.name for p in modfolder.mod_folders(root)], ["a real"])

    def test_the_summary_counts_what_could_not_be_read(self):
        root = self.mods(
            a__real='<ModOp Merge="@1000/Building"><Radius>1</Radius></ModOp>'
            # Copies from an asset that is not here, so it cannot be read.
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" '
            'Content="@9999/Building/Radius"></ModOp>',
        )
        session = self.session(root)
        self.assertEqual(session.unread, 1)
        self.assertIn("could not read", session.summary())

    def test_a_search_across_the_whole_folder_is_answered_once(self):
        """Two mods searching for the same thing is one read of the file."""
        root = self.mods(
            a__one='<ModOp Type="merge" Path="//Values/Building">'
            "<Elevation>1</Elevation></ModOp>",
            b__two='<ModOp Type="merge" Path="//Values/Building">'
            "<Terrace>1</Terrace></ModOp>",
        )
        asked = []
        real = self.index.find_matching

        def counted(paths, **kwargs):
            asked.append(list(paths))
            return real(paths, **kwargs)

        self.index.find_matching = counted
        try:
            session = self.session(root)
        finally:
            self.index.find_matching = real
        self.assertEqual(len(asked), 1)
        self.assertEqual(session.unread, 0)


class TestOneLoadoutHoldingSeveralMods(unittest.TestCase):
    """The change set is a stack of mods, not a single mod's worth of edits."""

    def setUp(self):
        self.loadout = Loadout(own=own_record(name="Mine"))
        self.loadout.set_layer(ModLayer(origin="b mod", name="Bee"))
        self.loadout.set_layer(ModLayer(origin="a mod", name="Ay"))

    def change(self, origin, guid="1", path="/Values/X", new="1", **kwargs):
        return Change(
            guid=guid, asset_label="asset " + guid, path=path, tag="X",
            old_value="0", new_value=new, origin=origin, **kwargs
        )

    def test_two_mods_setting_the_same_field_are_two_changes(self):
        self.loadout.set_change(self.change("a mod", new="1"))
        self.loadout.set_change(self.change("b mod", new="2"))
        self.assertEqual(len(self.loadout), 2)

    def test_and_re_reading_one_of_them_replaces_only_its_own(self):
        self.loadout.set_change(self.change("a mod", new="1"))
        self.loadout.set_change(self.change("b mod", new="2"))
        self.loadout.set_change(self.change("a mod", new="3"))
        self.assertEqual(
            sorted((c.origin, c.new_value) for c in self.loadout),
            [("a mod", "3"), ("b mod", "2")],
        )

    def test_load_order_is_folder_order_whatever_order_they_arrived_in(self):
        self.assertEqual([layer.origin for layer in self.loadout.layers], ["a mod", "b mod"])
        self.assertEqual([layer.order for layer in self.loadout.layers], [1, 2])

    def test_a_mod_written_to_publish_sits_where_the_game_sorts_it(self):
        """By its folder's name in Anno 1800, ``[Gameplay] Mine``: straight
        after the mod that sorts in front of it, or in front of them all. It
        used to be on top whatever it was called, which is an order the game
        was never asked for."""
        self.loadout.set_change(self.change("a mod"))
        self.loadout.set_change(self.change("", guid="2"))
        self.loadout.set_change(self.change("b mod", guid="3"))
        self.assertEqual(self.loadout.origins(), ["", "a mod", "b mod"])
        self.loadout.set_layer(ModLayer(origin="[Gameplay] Aaa", name="Aaa"))
        self.assertEqual(self.loadout.origins(loaded=True), ["[Gameplay] Aaa", "", "a mod", "b mod"])
        self.loadout.set_layer(ModLayer(origin="[Gameplay] Zzz", name="Zzz"))
        self.assertEqual(
            self.loadout.origins(loaded=True),
            ["[Gameplay] Aaa", "", "[Gameplay] Zzz", "a mod", "b mod"],
        )
        # Asked again once its name has moved.
        self.loadout.own.name = "Zzzz"
        self.assertEqual(
            self.loadout.origins(loaded=True),
            ["[Gameplay] Aaa", "[Gameplay] Zzz", "", "a mod", "b mod"],
        )

    def test_your_own_mod_sits_on_top_of_the_rest(self):
        """On a loadout, where it declares that it loads after everything."""
        self.loadout.own.loads_last = True
        self.loadout.set_change(self.change("a mod"))
        self.loadout.set_change(self.change("", guid="2"))
        self.loadout.set_change(self.change("b mod", guid="3"))
        self.assertEqual([c.origin for c in self.loadout.ordered()], ["a mod", "b mod", ""])
        self.assertEqual(self.loadout.origins(), ["a mod", "b mod", ""])

    def test_a_field_you_change_yourself_is_not_the_other_mod_s_change(self):
        """The editor asks "is this already an edit of mine?" before writing."""
        theirs = self.change("a mod")
        self.loadout.set_change(theirs)
        self.assertIsNone(self.loadout.find("1", "/Values/X"))
        self.assertIs(self.loadout.find("1", "/Values/X", origin="a mod"), theirs)

    def test_dropping_a_mod_takes_its_changes_with_it(self):
        self.loadout.set_change(self.change("a mod"))
        self.loadout.set_change(self.change("b mod", guid="2"))
        self.assertEqual(self.loadout.drop_layer("a mod"), 1)
        self.assertEqual([c.origin for c in self.loadout], ["b mod"])
        self.assertEqual([layer.origin for layer in self.loadout.layers], ["b mod"])

    def test_one_mod_alone_is_not_a_stack(self):
        """A heading over the whole list says nothing nobody already knows."""
        self.loadout.drop_layer("b mod")
        self.loadout.set_change(self.change("a mod"))
        self.assertFalse(self.loadout.is_multi_mod)
        self.loadout.set_change(self.change("", guid="2"))
        self.assertTrue(self.loadout.is_multi_mod)

    def test_whether_there_are_several_mods_is_counted_and_not_sorted(self):
        """It was asked of origins(), which puts every mod in load order,
        and the Mod changes panel asks it for every mod's row: 2.3 of the
        4.5 seconds an edit held the window with 417 mods loaded."""
        self.loadout.set_change(self.change("a mod"))
        with mock.patch.object(Loadout, "origins") as ordered, \
                mock.patch.object(Loadout, "layer_rank") as ranked:
            self.assertTrue(self.loadout.is_multi_mod)
            self.loadout.drop_layer("b mod")
            self.assertFalse(self.loadout.is_multi_mod)
            # A mod that is not loaded and has a change is a mod as well.
            self.loadout.set_change(self.change("gone mod", guid="3"))
            self.assertTrue(self.loadout.is_multi_mod)
        ordered.assert_not_called()
        ranked.assert_not_called()

    def test_the_mods_are_in_load_order_as_each_ones_rank_has_it(self):
        """origins() works the ranks out for itself, with the layers gone
        through once: the answer is layer_rank's, for every mod."""
        loadout = self.loadout
        for at, name in enumerate(("Zed", "alpha", "Mid", "beta")):
            loadout.set_layer(ModLayer(origin=name, name=name, order=10 - at))
            loadout.set_change(self.change(name, guid=str(20 + at)))
        loadout.set_change(self.change("", guid="30"))
        loadout.set_change(self.change("not loaded", guid="31"))
        for loaded in (False, True):
            found = loadout.origins(loaded=loaded)
            self.assertEqual(found, sorted(set(found), key=loadout.layer_rank))
        self.assertIn("", loadout.origins())
        self.assertEqual(loadout.origins(loaded=True)[0], "not loaded")

    def test_but_a_second_mod_is_one_even_before_it_changes_anything(self):
        """It is still a second mod, and its row is where it is unloaded or
        re-read at other settings."""
        self.loadout.set_change(self.change("a mod"))
        self.assertTrue(self.loadout.is_multi_mod)
        self.assertEqual(
            self.loadout.origins(loaded=True), ["a mod", "b mod"]
        )
        self.assertEqual(self.loadout.origins(), ["a mod"])

    def test_the_loaded_mods_survive_saving_and_opening(self):
        self.loadout.set_change(self.change("a mod"))
        self.loadout.set_change(self.change("", guid="2"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            self.loadout.save(path)
            reopened = Loadout.load(path)
        self.assertEqual([l.origin for l in reopened.layers], ["a mod", "b mod"])
        self.assertEqual(reopened.layer("a mod").name, "Ay")
        self.assertEqual(sorted(c.origin for c in reopened), ["", "a mod"])


class TestWhenTwoModsWantTheSameThing(unittest.TestCase):
    """What :mod:`core.conflicts` will and will not call a clash."""

    def setUp(self):
        self.loadout = Loadout(own=own_record(name="Mine"))
        for name in ("a mod", "b mod"):
            self.loadout.set_layer(ModLayer(origin=name, name=name.title()))

    def add(self, origin, path="/Values/X", new="1", op=OP_REPLACE, guid="1"):
        change = Change(
            guid=guid, asset_label="Alpha", path=path, tag="X",
            old_value="0", new_value=new, op=op, origin=origin,
        )
        self.loadout.set_change(change)
        return change

    def test_two_mods_setting_one_field_differently(self):
        self.add("a mod", new="1")
        self.add("b mod", new="2")
        clash = conflicts.find(self.loadout)[0]
        self.assertEqual(clash.kind, conflicts.OVERWRITES)
        self.assertEqual(clash.origins, ("a mod", "b mod"))
        self.assertEqual(clash.winner.origin, "b mod")
        self.assertIn("B Mod", clash.describe(lambda o: o.title()))

    def test_two_mods_wanting_the_same_value_is_not_a_problem(self):
        self.add("a mod", new="7")
        self.add("b mod", new="7")
        clash = conflicts.find(self.loadout)[0]
        self.assertEqual(clash.kind, conflicts.AGREES)
        self.assertTrue(clash.is_harmless)
        self.assertEqual(conflicts.summarise(self.loadout, str), "")

    def test_two_mods_editing_different_fields_of_one_asset_is_the_whole_point(self):
        self.add("a mod", path="/Values/X")
        self.add("b mod", path="/Values/Y")
        self.assertEqual(conflicts.find(self.loadout), [])

    def test_a_later_mod_removing_what_an_earlier_one_edits(self):
        self.add("a mod", path="/Values/Costs/Item/Amount")
        self.add("b mod", path="/Values/Costs/Item", op=OP_REMOVE)
        clash = conflicts.find(self.loadout)[0]
        self.assertEqual(clash.kind, conflicts.REMOVES)
        self.assertEqual(clash.path, "/Values/Costs/Item")

    def test_but_not_an_earlier_mod_removing_what_a_later_one_adds_back(self):
        """The later mod was read against the document the removal leaves, so
        nothing of its is lost -- this is a stack working, not a clash."""
        self.add("a mod", path="/Values/Costs/Item", op=OP_REMOVE)
        self.add("b mod", path="/Values/Costs/Item/Amount")
        self.assertEqual(conflicts.find(self.loadout), [])

    def test_two_additions_at_the_same_place_both_happen(self):
        self.add("a mod", path="/Values/List", op=OP_ADD)
        self.add("b mod", path="/Values/List", op=OP_ADD)
        self.assertEqual(conflicts.find(self.loadout), [])

    def ensure(self, origin, payload, path="/Values/Container", tag="SlotCount"):
        change = Change(
            guid="1", asset_label="Alpha", path=path, tag=tag,
            op=OP_MERGE, payload=payload, origin=origin,
        )
        self.loadout.set_change(change)
        return change

    def test_two_mods_making_sure_of_one_field_differently(self):
        """Each is a merge into the same node, and the second finds the field
        the first made and writes over it -- which as two additions nobody
        could tell from two entries in a list."""
        self.ensure("a mod", "<SlotCount>3</SlotCount>")
        self.ensure("b mod", "<SlotCount>5</SlotCount>")
        clash = conflicts.find(self.loadout)[0]
        self.assertEqual(clash.kind, conflicts.OVERWRITES)
        self.assertEqual(clash.path, "/Values/Container/SlotCount")
        self.assertEqual(clash.winner.origin, "b mod")

    def test_two_mods_making_sure_of_the_same_value_agree(self):
        self.ensure("a mod", "<SlotCount>3</SlotCount>")
        self.ensure("b mod", "<SlotCount> 3 </SlotCount>")
        self.assertEqual(conflicts.find(self.loadout)[0].kind, conflicts.AGREES)

    def test_a_field_one_mod_makes_and_a_later_one_replaces(self):
        self.ensure("a mod", "<SlotCount>3</SlotCount>")
        self.add("b mod", path="/Values/Container/SlotCount", new="5")
        clash = conflicts.find(self.loadout)[0]
        self.assertEqual(clash.kind, conflicts.OVERWRITES)
        self.assertEqual(clash.path, "/Values/Container/SlotCount")

    def test_two_mods_filling_one_section_with_different_fields_do_not_clash(self):
        self.ensure("a mod", "<Cost><A>1</A></Cost>", path="/Values", tag="Cost")
        self.ensure("b mod", "<Cost><B>2</B></Cost>", path="/Values", tag="Cost")
        self.assertEqual(conflicts.find(self.loadout), [])

    def test_a_later_mod_removing_the_node_a_field_was_made_in(self):
        self.ensure("a mod", "<SlotCount>3</SlotCount>")
        self.add("b mod", path="/Values/Container", op=OP_REMOVE)
        self.assertEqual(conflicts.find(self.loadout)[0].kind, conflicts.REMOVES)

    def test_one_mod_contradicting_itself_is_not_a_clash_between_mods(self):
        self.add("a mod", new="1")
        self.add("a mod", path="/Values/X", new="2")  # same key, replaced
        self.assertEqual(conflicts.find(self.loadout), [])

    def test_switching_a_change_off_settles_it(self):
        self.add("a mod", new="1")
        loser = self.add("b mod", new="2")
        self.assertEqual(len(conflicts.find(self.loadout)), 1)
        loser.active = False
        self.assertEqual(conflicts.find(self.loadout), [])

    def test_the_pair_tally_says_which_two_mods_disagree_most(self):
        self.add("a mod", path="/Values/X", new="1")
        self.add("b mod", path="/Values/X", new="2")
        self.add("a mod", path="/Values/Y", new="1")
        self.add("b mod", path="/Values/Y", new="2")
        self.assertEqual(conflicts.between(self.loadout), {("a mod", "b mod"): 2})

    def test_each_side_can_find_the_clash_it_is_in(self):
        first = self.add("a mod", new="1")
        second = self.add("b mod", new="2")
        found = conflicts.by_change(self.loadout)
        self.assertIn(first.key, found)
        self.assertIs(found[first.key], found[second.key])

    def test_nothing_is_contested_when_only_one_mod_is_loaded(self):
        self.add("a mod", new="1")
        self.add("a mod", path="/Values/Y", new="2")
        self.assertEqual(conflicts.find(self.loadout), [])
class TestOpsThatSearchForTheirAssets(ImportCase):
    """``//Values/Trader`` is a perfectly ordinary thing for a mod to write.

    Which assets it means is a question about the whole file rather than about
    one asset, so the file is searched -- and the answer is then one narrow
    ModOp per asset it turns out to be about, which is strictly better than
    what the mod had: another mod editing a different one no longer collides.
    """

    def searched(self, body: str, **kwargs) -> modops.ImportReport:
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        translator = reader.Translator(self.index, kwargs.pop("keep_inert", True))
        translator.search = reader.searcher(self.index, ops)
        report.results = translator.run_all(ops)
        return report

    def read_ops(self, body: str) -> modops.ImportReport:
        """:meth:`searched` as :func:`reader.read_ops` does it, kept ops and all.

        Not named ``read``: that is :meth:`ImportCase.read`, which
        :meth:`ImportCase.only` reads through with no search installed."""
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        return reader.read_ops(modops.parse_ops(root, [], "assets.xml"), self.index)

    def test_one_change_per_asset_the_search_finds(self):
        report = self.searched(
            '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        self.assertEqual(
            sorted((c.guid, c.new_value) for c in report.changes),
            [("1000", "30"), ("1001", "30")],
        )

    def test_an_asset_a_mod_has_changed_is_matched_as_one_none_has(self):
        """The file is swept for the assets no mod has touched and the rest
        are asked one by one, and the two were asked by different rules: a
        search finding text matched an asset until a mod changed it."""
        search = '<ModOp Type="remove" Path="//Values/Building/BuildingType/text()"/>'
        alone = self.searched(search)
        after = self.searched(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>7</Radius></ModOp>'
            + search
        )
        self.assertEqual(sorted(alone.results[0].record.matched), ["1000", "1001"])
        self.assertEqual(sorted(after.results[1].record.matched), ["1000", "1001"])

    def test_and_each_one_is_addressed_by_guid_from_then_on(self):
        report = self.searched(
            '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
        )
        for change in report.changes:
            self.assertEqual(change.path, "/Values/Building/Radius")
            self.assertTrue(change.guid)

    def test_a_bare_path_condition_is_asked_of_the_document_not_each_asset(self):
        """The loader asks an op with no GUID its condition once, with no asset
        to start from. Asked of each asset found, ``/Values/Building`` held of
        both; *Lux et Industria* has three such ops, which xmltest never runs."""
        op = ('<ModOp Type="merge" Path="//Values/Building" Condition="%s">'
              "<Radius>30</Radius></ModOp>")
        report = self.searched(op % "/Values/Building")
        self.assertEqual(report.results[0].status, modops.SETTLED)
        self.assertEqual(report.changes, [])
        report = self.searched(op % "!/Values/Building/Radius")
        self.assertEqual(sorted(c.guid for c in report.changes), ["1000", "1001"])

    def test_a_predicate_narrows_it(self):
        report = self.searched(
            "<ModOp Type=\"merge\" Path=\"//Values[Building/BuildingType='Farm']"
            '/Building"><Radius>30</Radius></ModOp>'
        )
        self.assertEqual([c.guid for c in report.changes], ["1001"])

    def test_a_search_for_the_asset_element_itself(self):
        report = self.searched(
            "<ModOp Type=\"merge\" Path=\"//Asset[Values/Standard/Name='beta']"
            '/Values/Building"><Radius>30</Radius></ModOp>'
        )
        self.assertEqual([c.guid for c in report.changes], ["1001"])

    def test_a_search_that_finds_nothing_is_not_a_refusal(self):
        report = self.searched(
            '<ModOp Type="merge" Path="//Values/NoSuchThing"><X>1</X></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.SETTLED)
        self.assertIn("nothing in your assets.xml matches", report.results[0].reason)

    def test_and_its_op_comes_back_on_its_result(self):
        """The op was written down and not handed back, so the import dialog
        greyed the row out with no tick, always took the op, and left it out
        of its count."""
        report = self.read_ops(
            '<ModOp Type="merge" Path="//Values/NoSuchThing"><X>1</X></ModOp>'
        )
        result = report.results[0]
        self.assertIsNotNone(result.record)
        self.assertEqual([op.uid for op in report.ops], [result.record.uid])
        self.assertEqual(result.record.made, 0)

    def test_as_does_one_whose_bare_path_condition_does_not_hold(self):
        report = self.read_ops(
            '<ModOp Type="merge" Path="//Values/Building" Condition="/Values/Building">'
            "<Radius>30</Radius></ModOp>"
        )
        result = report.results[0]
        self.assertEqual(result.status, modops.SETTLED)
        self.assertIsNotNone(result.record)
        self.assertEqual([op.uid for op in report.ops], [result.record.uid])
        self.assertTrue(result.record.idle)

    def test_an_asset_it_already_matches_is_left_alone(self):
        report = self.searched(
            "<ModOp Type=\"merge\" Path=\"//Values/Building\">"
            "<BuildingType>Factory</BuildingType></ModOp>"
        )
        self.assertEqual([c.guid for c in report.changes], ["1001"])

    def test_a_search_that_hits_half_the_game_is_refused_out_loud(self):
        from anno_modding_studio.core import reader as under_test

        was = under_test.MAX_ASSETS_PER_SEARCH
        under_test.MAX_ASSETS_PER_SEARCH = 1
        try:
            report = self.searched(
                '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
            )
        finally:
            under_test.MAX_ASSETS_PER_SEARCH = was
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertIn("matches 2 assets", report.results[0].reason)

    def test_without_a_search_to_ask_it_says_so_rather_than_guessing(self):
        result = self.only(
            '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("has not been searched", result.reason)


class TestNarrowingTheSearch(unittest.TestCase):
    """What an asset must contain before it is worth parsing at all."""

    def test_the_steps_name_elements_a_match_cannot_do_without(self):
        needed, _one_of = index_mod.narrowing("//Cost/Costs/Item/Ingredient")
        self.assertEqual(needed, {b"Cost", b"Costs", b"Item", b"Ingredient"})

    def test_but_not_the_ones_every_asset_has(self):
        needed, _one_of = index_mod.narrowing("//Values/Building/Radius")
        self.assertEqual(needed, {b"Building", b"Radius"})

    def test_a_literal_it_compares_against_must_be_written_down_somewhere(self):
        _needed, one_of = index_mod.narrowing("//Values[../Template='Farm']")
        self.assertEqual(one_of, [b"Farm"])

    def test_unless_the_predicate_is_about_something_being_absent(self):
        """There a literal missing is the whole point, so requiring it would
        rule out exactly the assets that match."""
        _needed, one_of = index_mod.narrowing("//Values[not(Template='Farm')]")
        self.assertEqual(one_of, [])
        _needed, one_of = index_mod.narrowing("//Values[Template!='Farm']")
        self.assertEqual(one_of, [])

    def test_an_axis_or_a_function_step_requires_nothing(self):
        needed, _one_of = index_mod.narrowing("self::node()[FarmType='PlantFarm']")
        self.assertEqual(needed, frozenset())

    def test_a_slash_inside_a_predicate_is_not_a_step(self):
        """*Rainbow Obsidian* searches by icon file, and a file name has slashes.

        Split at every one, ``ui`` and ``fhd`` became element names the asset
        had to contain, so nothing ever did and the search matched nothing."""
        needed, one_of = index_mod.narrowing(
            "//Values/Standard[IconFilename='data/ui/fhd/icon.png']"
        )
        self.assertEqual(needed, frozenset())
        self.assertEqual(one_of, [b"data/ui/fhd/icon.png"])
        self.assertEqual(
            list(index_mod.step_tags("//A[B='x/y']/C[D=\"p/q\"]/E")), ["A", "C", "E"]
        )


class TestSearchingTheWholeFile(ImportCase):
    """The sweep itself."""

    def test_it_finds_every_asset_with_the_element(self):
        found = self.index.find_matching(["//Values/Building"])
        self.assertEqual(sorted(found["//Values/Building"]), ["1000", "1001"])

    def test_a_batch_is_one_pass_and_keeps_the_answers_apart(self):
        found = self.index.find_matching(
            ["//Values/Building", "//Values/ProductList", "//Values/Nothing"]
        )
        self.assertEqual(sorted(found["//Values/Building"]), ["1000", "1001"])
        self.assertEqual(sorted(found["//Values/ProductList"]), ["1000", "1002"])
        self.assertEqual(found["//Values/Nothing"], [])

    def test_an_expression_that_is_not_xpath_is_left_out_rather_than_raised(self):
        found = self.index.find_matching(["//["])
        self.assertEqual(found, {})

    def test_it_says_how_far_it_has_got(self):
        seen = []
        self.index.find_matching(["//Values/Building"], progress=lambda n, t: seen.append((n, t)))
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1])

    def test_a_literal_with_a_slash_in_it_still_finds_its_asset(self):
        path = "//Values/Standard[Name='alpha' or Name='a/b']"
        self.assertEqual(self.index.find_matching([path])[path], ["1000"])


class TestWhatEachGamesLoaderReads(ImportCase):
    """Spellings the two games' loaders read differently.

    Each was tried against the loader itself -- xmltest's 1800 and 117 builds --
    before the Studio was made to agree with it."""

    def read_for(self, body: str, game: str) -> modops.ImportReport:
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        translator = reader.Translator(self.index, True)
        translator.search = reader.searcher(self.index, ops)
        report.results = translator.run_all(ops)
        return report

    def test_anno_117_reads_op_attributes_in_any_case(self):
        """*Obsidian Weapon Extended* writes ``<ModOp merge="@30456/Standard">``."""
        for body in (
            '<ModOp merge="@1000/Building"><Radius>25</Radius></ModOp>',
            '<ModOp type="replace" GUID="1000" path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>",
        ):
            with self.subTest(body=body):
                report = self.read_for(body, games.ANNO_117)
                self.assertEqual(
                    [(c.guid, c.new_value) for c in report.changes], [("1000", "25")]
                )

    def test_anno_1800_does_not(self):
        report = self.read_for(
            '<ModOp type="replace" GUID="1000" path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>",
            games.ANNO_1800,
        )
        self.assertEqual(report.changes, [])

    def test_a_condition_spelt_in_lower_case(self):
        """Asked by 117's loader; ignored by 1800's, which runs the op regardless."""
        body = (
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
            'condition="@1000/Nowhere"><Radius>25</Radius></ModOp>'
        )
        self.assertEqual(self.read_for(body, games.ANNO_117).changes, [])
        self.assertEqual(
            [c.new_value for c in self.read_for(body, games.ANNO_1800).changes], ["25"]
        )

    def test_a_path_starting_with_two_slashes(self):
        """117's loader searches the whole file whatever GUID the op names;
        1800's looks only inside that asset. *Pizza Margherita* gives three ops
        ``GUID='106338'`` and adds its tomato to ten other assets."""
        body = (
            '<ModOp Type="replace" GUID="1001" Path="//Values/Building/Radius">'
            "<Radius>12</Radius></ModOp>"
        )
        self.assertEqual(
            sorted(c.guid for c in self.read_for(body, games.ANNO_117).changes),
            ["1000", "1001"],
        )
        self.assertEqual(
            [c.guid for c in self.read_for(body, games.ANNO_1800).changes], ["1001"]
        )

    def test_an_op_on_the_asset_itself_is_an_op_on_its_values_in_anno_117(self):
        """``Add="@1000/"`` puts its payload inside ``Values`` for 117's loader.
        *GUID Explorer* adds to seventeen thousand assets that way, and every
        one was read as an op on no path at all."""
        for body in (
            '<ModOp Add="@1000/"><Extra>7</Extra></ModOp>',
            '<ModOp Add="@1000"><Extra>7</Extra></ModOp>',
            '<ModOp Merge="@1000/"><Extra>7</Extra></ModOp>',
        ):
            with self.subTest(body=body):
                changes = self.read_for(body, games.ANNO_117).changes
                self.assertTrue(changes)
                self.assertTrue(all(c.path.startswith("/Values") for c in changes), changes)

    def test_a_group_spelt_in_lower_case_is_a_group(self):
        body = (
            '<group><ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp></group>"
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                self.assertEqual(
                    [c.new_value for c in self.read_for(body, game).changes], ["25"]
                )

    def test_a_mod_folder_is_read_as_the_game_its_patch_is_for(self):
        """The patch's own place in the mod says which loader it is for."""
        body = modops_file(
            '<ModOp type="replace" GUID="1000" path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            for game, expected in ((games.ANNO_117, ["25"]), (games.ANNO_1800, [])):
                with self.subTest(game=game):
                    root = Path(tmp) / game
                    patch = root / games.get(game).asset_relpath
                    patch.parent.mkdir(parents=True)
                    patch.write_text(body, encoding="utf-8")
                    report = reader.analyse(root, self.index)
                    self.assertEqual([c.new_value for c in report.changes], expected)


ONE_ENTRY = """<AssetList><Assets>
  <Asset><Template>Decision</Template><Values>
    <Standard><GUID>5000</GUID><Name>decision</Name></Standard>
    <CostData><Products><Item><GUID>2137</GUID></Item></Products></CostData>
  </Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>2137</GUID><Name>bread</Name></Standard>
  </Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>9</GUID><Name>pizza</Name></Standard>
  </Values></Asset>
</Assets></AssetList>"""


KEYED_MID_OP = """<AssetList><Assets>
  <Asset><Template>Building</Template><Values>
    <Standard><GUID>7000</GUID><Name>emptied</Name></Standard>
    <Cost><Costs>
      <Item><VectorElement><InheritedIndex>0</InheritedIndex></VectorElement>
        <Ingredient>1010</Ingredient><Amount>10</Amount></Item>
      <Item><VectorElement><InheritedIndex>1</InheritedIndex></VectorElement>
        <Ingredient>1011</Ingredient><Amount>10</Amount></Item>
    </Costs></Cost>
  </Values></Asset>
  <Asset><Template>Building</Template><Values>
    <Standard><GUID>7001</GUID><Name>depot</Name></Standard>
    <Maintenance><Maintenances>
      <Item><Amount>50</Amount><InactiveAmount>50</InactiveAmount></Item>
    </Maintenances></Maintenance>
  </Values></Asset>
  <Asset><Template>ProductionChain</Template><Values>
    <Standard><GUID>7002</GUID><Name>chain</Name></Standard>
    <ProductionChain><Tier1><Item><Building>1010</Building></Item></Tier1></ProductionChain>
  </Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>1010</GUID><Name>wood</Name></Standard></Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>1011</GUID><Name>brick</Name></Standard></Values></Asset>
</Assets></AssetList>"""


class TestKeysAreChosenFromTheAssetAsTheOpFoundIt(unittest.TestCase):
    """A list entry's key is judged before the op's own edits, never halfway through."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        assets = tmp / "assets.xml"
        assets.write_text(KEYED_MID_OP, encoding="utf-8")
        index_mod.build_index(assets, tmp / "index.sqlite")
        cls.index = index_mod.AssetIndex(tmp / "index.sqlite", assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def read(self, body: str) -> modops.ImportReport:
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        report.results = reader.Translator(self.index, True).run_all(ops)
        return report

    def exported_and_read_again(self, report) -> modops.ImportReport:
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        xml = generator.build_modops_xml(loadout)
        body = xml.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0]
        return self.read(body)

    def test_an_emptied_list_is_not_keyed_by_a_value_its_twin_just_lost(self):
        """*Creative Mode* empties a cost list whose entries share an Amount.
        Keyed halfway through, the second Amount looked unique -- its twin was
        already gone -- and in the file that path matched both entries."""
        report = self.read(
            '<ModOp Type="replace" GUID="7000" Path="/Values/Cost/Costs">'
            "<Costs>"
            "<Item><VectorElement><InheritedIndex>0</InheritedIndex></VectorElement></Item>"
            "<Item><VectorElement><InheritedIndex>1</InheritedIndex></VectorElement></Item>"
            "</Costs></ModOp>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0, [r.reason for r in report.results])
        self.assertFalse([c.path for c in report.changes if "Amount='10'" in c.path])
        again = self.exported_and_read_again(report)
        self.assertEqual(again.count(modops.BLOCKED), 0, [r.reason for r in again.results])

    def test_an_entry_is_not_keyed_by_a_field_the_op_is_adding_to_it(self):
        """*Harbour Service Overhaul* merges a Product into a maintenance entry.
        Keyed halfway through, the entry was found by that very Product, and its
        own export could not be read back."""
        report = self.read(
            '<ModOp Type="merge" GUID="7001" Path="/Values">'
            "<Maintenance><Maintenances>"
            "<Item><Product>1010</Product><Amount>50</Amount><InactiveAmount>25</InactiveAmount></Item>"
            "<Item><Product>1011</Product><Amount>5</Amount></Item>"
            "</Maintenances></Maintenance></ModOp>"
        )
        self.assertEqual(report.count(modops.BLOCKED), 0, [r.reason for r in report.results])
        self.assertFalse([c.path for c in report.changes if "Product=" in c.path])
        again = self.exported_and_read_again(report)
        self.assertEqual(again.count(modops.BLOCKED), 0, [r.reason for r in again.results])

    def test_a_list_another_mod_added_is_copied_once_per_op(self):
        """*Production Works* gives an entry of a chain another of its mods added a new key.

        The op's second edit, taking ForceIcon away, must find the entry by the
        key the op found it under. The sheet remembered the lists it had copied
        by the ``id()`` of their parent, and nothing held a parent this import
        had added: freed between the op's two edits, it came back as a new lxml
        proxy, and the list was copied again with its key already replaced --
        or it came back at the same address, and was not. Which, depended on
        memory, so the census read the op two ways from run to run. Here other
        proxies take the freed memory after every edit, as a big read's did.
        """
        from unittest import mock

        ops = []
        for mod, body in (
            ("a", '<ModOp Type="add" GUID="7002" Path="/Values/ProductionChain/Tier1">'
                  "<Item><Building>1011</Building><Tier2>"
                  "<Item><Building>1014</Building><ForceIcon>1</ForceIcon></Item>"
                  "<Item><Building>1013</Building></Item></Tier2></Item></ModOp>"),
            ("b", '<ModOp Type="replace" GUID="7002" '
                  "Path=\"/Values/ProductionChain/Tier1/Item/Tier2/Item[Building='1014']\">"
                  "<Item><Building>1012</Building></Item></ModOp>"),
        ):
            root = etree.fromstring(modops_file(body).encode("utf-8"))
            ops.extend(dataclasses.replace(op, mod=mod) for op in modops.parse_ops(root, [], "assets.xml"))

        starting = recording.Sheet.starting
        crowd = []

        def crowded(sheet, *args, **kwargs):
            starting(sheet, *args, **kwargs)
            crowd.append(list(etree.fromstring(KEYED_MID_OP).iter()))

        with mock.patch.object(recording.Sheet, "starting", crowded):
            results = reader.Translator(self.index, True).run_all(ops)
        self.assertEqual([r.status for r in results], [modops.IMPORTED] * 2, [r.reason for r in results])
        entry = "/Values/ProductionChain/Tier1/Item[Building='1011']/Tier2/Item[Building='1014']"
        self.assertEqual(
            sorted((c.op, c.path) for c in results[1].changes),
            [("remove", entry + "/ForceIcon"), ("replace", entry + "/Building")],
        )


class TestAddingBesideTheOnlyEntryInAList(unittest.TestCase):
    """*Pizza Margherita* adds its item next to the one entry of a list, three times.

    Each op was written against the list as it stood when read, when
    ``Products/Item`` needed no key: one entry, then two, then four, and
    replayed that way the list came out with seven new items where the mod
    adds three -- so the Studio refused all three ops."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        assets = tmp / "assets.xml"
        assets.write_text(ONE_ENTRY, encoding="utf-8")
        index_mod.build_index(assets, tmp / "index.sqlite")
        cls.index = index_mod.AssetIndex(tmp / "index.sqlite", assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def test_three_additions_beside_a_lone_entry_stay_three(self):
        op = (
            "<ModOp Type=\"addNextSibling\" GUID=\"5000\" "
            "Path=\"/Values/CostData/Products/Item[GUID='2137']\">"
            "<Item><GUID>9</GUID></Item></ModOp>"
        )
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(op * 3).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        report.results = reader.Translator(self.index, True).run_all(ops)

        self.assertEqual(
            [r.status for r in report.results],
            [modops.IMPORTED] * 3,
            [r.reason for r in report.results],
        )
        # The first goes in while the entry is still the only one, so its path
        # needs no key; the two after it are written keyed, since by then the
        # mod's own additions sit beside the entry.
        paths = sorted(report.changes, key=lambda c: c.order)
        self.assertTrue(all("Item[GUID='2137']" in c.path for c in paths[1:]), [c.path for c in paths])
        loadout = Loadout()
        for change in report.changes:
            loadout.set_change(change)
        shown = Workspace(self.index, loadout).effective_asset("5000").as_the_game_reads_it()
        self.assertEqual(
            [item.findtext("GUID") for item in shown.iter("Item")], ["2137", "9", "9", "9"]
        )


class TestFormsRealModsUseThatLookLikeNothing(ImportCase):
    """Two shapes that come up in shipping mods and read as almost anything."""

    def test_the_op_name_holds_the_path_and_the_guid_stands_alone(self):
        """``<ModOp GUID="5977" Merge="EffectSource">`` mixes both forms."""
        changes = self.imported(
            '<ModOp GUID="1000" Merge="Building"><Radius>25</Radius></ModOp>'
        )
        self.assertEqual(changes[0].guid, "1000")
        self.assertEqual(changes[0].path, "/Values/Building/Radius")
        self.assertEqual(changes[0].new_value, "25")

    def test_a_merge_may_name_its_target_twice(self):
        """Once in the path, once as the payload's root -- and then the payload
        is what the target should look like, not something to put inside it."""
        changes = self.imported(
            '<ModOp GUID="1000" Merge="Building">'
            "<Building><Radius>25</Radius></Building></ModOp>"
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].path, "/Values/Building/Radius")
        self.assertEqual((changes[0].old_value, changes[0].new_value), ("10", "25"))

    def test_but_not_into_an_entry_that_is_not_the_first_of_its_name(self):
        """The loader merges such a payload into the parent, where it pairs
        with the first entry of that name -- measured with xmltest, and not in
        its docs -- so *Mail 1840*'s merge into every ``Item`` of a list fills
        only the first. The op goes back out as written."""
        merge = ('<ModOp Type="merge" GUID="1000" Path="/Values/ProductList/List/Item%s">'
                 "<Item><Extra>1</Extra></Item></ModOp>")
        result = self.only(merge % "[Product='2002']")
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("not the first of that name", result.reason)
        self.assertEqual(self.only(merge % "").status, modops.BLOCKED)
        changes = self.imported(merge % "[Product='2001']")
        self.assertEqual([c.path for c in changes], ["/Values/ProductList/List/Item[Product='2001']"])

    def test_unless_both_readings_come_to_the_same(self):
        """*All Modules 100 Range* adds a second field and merges the same
        value into both: by the loader's source the first takes it twice and
        the second has it already, which is what reading each in turn says."""
        report = self.read(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Radius>25</Radius></ModOp>'
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius"><Radius>25</Radius></ModOp>'
        )
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED, modops.IMPORTED])

    def test_including_when_the_target_is_a_value(self):
        changes = self.imported(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>25</Radius></ModOp>"
        )
        self.assertEqual(changes[0].path, "/Values/Building/Radius")
        self.assertEqual(changes[0].new_value, "25")

    def test_and_so_where_the_target_holds_a_node_of_its_name(self):
        """117 has 1,887 <Objective>s inside an <Objective>. The loader takes
        one payload node named like its target for the target whatever it
        holds, and so does the Studio."""
        target = etree.fromstring("<Thing><Thing><A>1</A></Thing></Thing>")
        payload = (etree.fromstring("<Thing><B>2</B></Thing>"),)
        self.assertEqual([child.tag for child in reader._merge_wanted(payload, target)], ["B"])

    def test_in_any_case_in_anno_117(self):
        target = etree.fromstring("<Thing><A>1</A></Thing>")
        payload = (etree.fromstring("<thing><B>2</B></thing>"),)
        self.assertEqual([child.tag for child in reader._merge_wanted(payload, target)], ["B"])
        self.assertEqual(
            [child.tag for child in reader._merge_wanted(payload, target, games.ANNO_1800)],
            ["thing"],
        )


class TestAMergeFindsAFieldAsTheLoaderDoes(ImportCase):
    """In any case in Anno 117 (``pugih::equals``) and as written in Anno 1800
    (``strcmp``), as ``core/applying.py`` has it from the loaders' source."""

    MERGE = ('<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
             "<radius>25</radius></ModOp>")

    def read_as(self, game):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(self.MERGE).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        (result,) = reader.Translator(self.index, True).run_all(ops)
        return result

    def test_anno_117_sets_the_field_it_has(self):
        result = self.read_as(games.ANNO_117)
        self.assertEqual([(c.op, c.path, c.new_value) for c in result.changes],
                         [("replace", "/Values/Building/Radius", "25")])

    def test_anno_1800_adds_one_of_that_name(self):
        result = self.read_as(games.ANNO_1800)
        self.assertEqual([(c.op, c.tag) for c in result.changes], [("merge", "radius")])


class TestWhatAModLetsYouDecide(unittest.TestCase):
    """A mod's own options, read off the modinfo.json that declares them."""

    def setUp(self):
        self.offered = settings.read(json.loads(MODINFO_WITH_OPTIONS)["Options"])

    def test_every_option_is_read_with_its_type_and_default(self):
        self.assertEqual(len(self.offered), 4)
        roomier = self.offered.get("roomier")
        self.assertEqual(roomier.kind, settings.TOGGLE)
        self.assertEqual(roomier.default, "false")
        self.assertEqual(roomier.label, "Make it roomier?")

    def test_a_dummy_option_that_only_heads_a_section_is_not_a_choice(self):
        self.assertTrue(self.offered.get("ph_head").is_heading)
        self.assertEqual(
            [option.name for option in self.offered.settable],
            ["roomier", "factor", "size"],
        )

    def test_a_slider_carries_the_author_s_own_range(self):
        self.assertEqual(self.offered.get("factor").range, (0.0, 2.0, 0.5))

    def test_an_enum_uses_the_author_s_own_wording(self):
        self.assertEqual(self.offered.get("size").label_for("20"), "Large")

    def test_the_defaults_are_what_the_mod_is_read_with(self):
        self.assertEqual(
            self.offered.settled(),
            {"ph_head": "----------", "roomier": "false", "factor": "1", "size": "10"},
        )

    def test_and_a_choice_replaces_one_of_them(self):
        self.assertEqual(self.offered.settled({"size": "20"})["size"], "20")

    def test_a_setting_it_does_not_declare_is_not_invented(self):
        self.assertNotIn("nonsense", self.offered.settled({"nonsense": "1"}))

    def test_what_counts_as_on(self):
        for value in ("true", "1", "yes", "300"):
            self.assertTrue(settings.is_on(value), value)
        for value in ("false", "0", "", "no", "off"):
            self.assertFalse(settings.is_on(value), value)

    def test_a_mod_with_no_options_block_offers_nothing(self):
        self.assertEqual(len(settings.read(None)), 0)
        self.assertFalse(settings.read({}))


class TestConfigurableMods(SessionCase):
    """A mod whose ops read its own settings back."""

    GUARDED = (
        '<ModOp Merge="@1000/Building" Condition="$roomier">'
        "<Radius>30</Radius></ModOp>"
        '<ModOp Merge="@1001/Building" Condition="!$roomier">'
        "<Radius>30</Radius></ModOp>"
    )

    def configurable(self, body: str = "", modinfo: str = MODINFO_WITH_OPTIONS):
        root = self.mods(a__mod=body or self.GUARDED)
        (root / "a mod" / "modinfo.json").write_text(modinfo, encoding="utf-8")
        return root

    def read(self, root, **kwargs):
        return reader.analyse(root / "a mod", self.index, origin="a mod", **kwargs)

    def test_the_options_come_across_with_the_mod(self):
        report = self.read(self.configurable())
        offered = report.metadata["options"]
        self.assertEqual([option.name for option in offered.settable],
                         ["roomier", "factor", "size"])

    def test_a_condition_naming_a_setting_is_answered_from_its_default(self):
        report = self.read(self.configurable())
        self.assertEqual(
            [result.status for result in report.results],
            [modops.SETTLED, modops.IMPORTED],
        )

    def test_and_from_a_choice_when_one_has_been_made(self):
        report = self.read(
            self.configurable(),
            reading=modops.Reading(options={"a mod": {"roomier": "true"}}),
        )
        self.assertEqual(
            [result.status for result in report.results],
            [modops.IMPORTED, modops.SETTLED],
        )

    def test_a_setting_the_mod_does_not_declare_is_still_unanswerable(self):
        report = self.read(
            self.configurable(
                '<ModOp Merge="@1000/Building" Condition="$nonsense">'
                "<Radius>30</Radius></ModOp>"
            )
        )
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertIn("reads $nonsense, which the mod does not declare as an option",
                      report.results[0].reason)

    def test_a_whole_folder_reads_each_mod_at_its_own_settings(self):
        root = self.mods(
            a__mod=self.GUARDED,
            b__mod='<ModOp Merge="@1002/Cost/Costs/Item/Amount" '
            'Condition="$roomier"><Amount>9</Amount></ModOp>',
        )
        for name in ("a mod", "b mod"):
            (root / name / "modinfo.json").write_text(
                MODINFO_WITH_OPTIONS, encoding="utf-8"
            )
        session = self.session(
            root, reading=modops.Reading(options={"a mod": {"roomier": "true"}})
        )
        found = {read.origin: read.report.results for read in session.reads}
        self.assertEqual(found["a mod"][0].status, modops.IMPORTED)
        self.assertEqual(found["b mod"][0].status, modops.SETTLED)


class TestSettingsNamedInFull(SessionCase):
    """``$mod-id.option``, which is how the loader's own docs name a setting.

    The loader keeps every loaded mod's options in one table under
    ``mod-id.option``, and reads a bare ``$option`` as the mod's own
    (``ReplaceStaticVariables``): so a mod can name its own setting either
    way, and another mod's only in full. A dot in the name used to leave
    the condition unanswered, and a ``ModValue`` called it undeclared.
    """

    @staticmethod
    def modinfo(mod_id: str) -> str:
        declared = json.loads(MODINFO_WITH_OPTIONS)
        declared["ModID"] = mod_id
        return json.dumps(declared)

    def folder(self, **bodies):
        root = self.mods(**bodies)
        for name in bodies:
            name = name.replace("__", " ")
            mod_id = name.replace(" ", "-")
            (root / name / "modinfo.json").write_text(self.modinfo(mod_id), encoding="utf-8")
        return root

    def read(self, root, **kwargs):
        return reader.analyse(root / "a mod", self.index, origin="a mod", **kwargs)

    def test_a_mod_names_its_own_setting_in_full(self):
        root = self.folder(a__mod=(
            '<ModOp Merge="@1000/Building" Condition="$a-mod.roomier">'
            "<Radius>30</Radius></ModOp>"
            '<ModOp Merge="@1001/Building" Condition="!$a-mod.roomier">'
            "<Radius>30</Radius></ModOp>"
        ))
        report = self.read(root)
        self.assertEqual(
            [result.status for result in report.results],
            [modops.SETTLED, modops.IMPORTED],
        )
        report = self.read(root, reading=modops.Reading(options={"a mod": {"roomier": "true"}}))
        self.assertEqual(
            [result.status for result in report.results],
            [modops.IMPORTED, modops.SETTLED],
        )

    def test_and_combines_it_with_the_bare_name(self):
        root = self.folder(a__mod=(
            '<ModOp Merge="@1000/Building" Condition="$a-mod.size = 10 and not($roomier)">'
            "<Radius>30</Radius></ModOp>"
        ))
        game = mock.patch.object(modops, "game_for_patch", return_value=games.ANNO_117)
        with game:
            report = self.read(root)
        self.assertEqual(report.results[0].status, modops.IMPORTED, report.results[0].reason)

    def test_a_value_inserted_from_a_setting_named_in_full(self):
        root = self.folder(a__mod=(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
            '<Radius><ModValue Insert="$a-mod.size"/></Radius></ModOp>'
        ))
        report = self.read(root, reading=modops.Reading(options={"a mod": {"size": "20"}}))
        self.assertEqual(report.results[0].status, modops.IMPORTED, report.results[0].reason)
        self.assertEqual(
            [(change.old_value, change.new_value) for change in report.changes],
            [("10", "20")],
        )

    def test_one_mod_reads_another_mods_setting(self):
        root = self.folder(
            a__mod='<ModOp Merge="@1001/Building"><Radius>12</Radius></ModOp>',
            b__mod='<ModOp Merge="@1000/Building" Condition="$a-mod.roomier">'
            "<Radius>30</Radius></ModOp>",
        )
        for roomier, status in (("true", modops.IMPORTED), ("false", modops.SETTLED)):
            with self.subTest(roomier=roomier):
                session = self.session(
                    root, reading=modops.Reading(options={"a mod": {"roomier": roomier}})
                )
                found = {read.origin: read.report.results for read in session.reads}
                self.assertEqual(found["b mod"][0].status, status)

    def test_a_mod_not_read_beside_it_is_still_not_answered(self):
        root = self.folder(a__mod=(
            '<ModOp Merge="@1000/Building" Condition="$elsewhere.roomier">'
            "<Radius>30</Radius></ModOp>"
        ))
        self.assertEqual(self.read(root).results[0].status, modops.BLOCKED)

    def test_what_each_mod_sees(self):
        seen = settings.visible(
            {"a": {"roomier": "true"}, "b": {"size": "5"}, "c": {"x": "1"}},
            {"a": "a-mod", "b": "b-mod", "d": "d-mod"},
        )
        both = {"a-mod.roomier": "true", "b-mod.size": "5"}
        self.assertEqual(seen["a"], {**both, "roomier": "true"})
        self.assertEqual(seen["b"], {**both, "size": "5"})
        # No ModID: its own by their bare names, and nobody else's in full.
        self.assertEqual(seen["c"], {**both, "x": "1"})
        # No settings of its own: everybody's in full.
        self.assertEqual(seen["d"], both)


class TestValuesAModWorksOutAsItPatches(ImportCase):
    """``<ModValue Insert=…>`` and ``Content=`` write a value the mod does not
    know until it runs.  Neither is something a narrow ModOp can say, so reading
    them means settling them -- which is a choice, and a different one each:
    a value from the mod's own settings loses nothing that cannot be got back,
    a copy loses the link that was the point of writing it that way."""

    SETTING = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
        '<Radius><ModValue Insert="$size"/></Radius></ModOp>'
    )
    ARITHMETIC = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
        '<Radius><ModValue Insert="self::node() * $factor"/></Radius></ModOp>'
    )
    COPIED = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius" '
        'Content="@1001/Building/Radius"></ModOp>'
    )

    DEFAULTS = {"size": "20", "factor": "0.5"}

    def settled(self, body: str, chosen=DEFAULTS):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        translator = reader.Translator(self.index, True)
        translator.search = reader.searcher(self.index, ops)
        translator.chosen = {"": dict(chosen)}
        report.results = translator.run_all(ops)
        return report

    def test_a_setting_becomes_the_value_it_is_set_to(self):
        report = self.settled(self.SETTING)
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        change = report.changes[0]
        self.assertEqual((change.old_value, change.new_value), ("10", "20"))

    def test_arithmetic_on_what_the_field_already_says(self):
        report = self.settled(self.ARITHMETIC)
        self.assertEqual(report.changes[0].new_value, "5")

    def test_a_value_computed_straight_inside_the_op(self):
        """No field around it: ``<ModOp Merge="@…/Factor"><ModValue …/></ModOp>``.

        Faster Fast Gamespeed writes all three of its speeds this way. The value
        froze into text that the payload then dropped, so the merge had nothing
        in it, fell back to swapping the whole node, and wrote back the value
        already there -- 1, 2 and 5 where its defaults say 2, 4 and 10.
        """
        for body, expected in (
            (
                '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
                '<ModValue Insert="$size"/></ModOp>',
                "20",
            ),
            (
                '<ModOp Merge="@1000/Building/Radius">'
                '<ModValue Insert="self::node() * $factor"/></ModOp>',
                "5",
            ),
        ):
            with self.subTest(body=body):
                report = self.settled(body)
                self.assertEqual(
                    report.results[0].status, modops.IMPORTED, report.results[0].reason
                )
                self.assertEqual(
                    [(c.op, c.old_value, c.new_value) for c in report.changes],
                    [(OP_REPLACE, "10", expected)],
                )

    def test_a_computed_value_added_with_no_field_around_it_is_not_guessed_at(self):
        report = self.settled(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building/Radius">'
            '<ModValue Insert="$size"/></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertEqual(report.changes, [])

    # What Anno 117 makes of a ``<ModValue/>``.  The game itself was asked on
    # 10 Oct 2026, with marker mods the loader logs (docs/design-notes.md,
    # "Values a mod works out while patching"), and xmltest3 gave the same
    # answer to every question: ``TestTheRealLoader`` in test_modio_loader.py
    # pins each shape here to that build.  Asset 1000's radius is 10, asset
    # 1001's is 4, and the ``size`` option is at 20.
    MERGED = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>%s</Radius></ModOp>'
    REPLACED = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>%s</Radius></ModOp>'
    NEW_ASSET = (
        '<ModOp Type="AddNextSibling" GUID="1000"><Asset><Template>Building</Template><Values>'
        "<Standard><GUID>5000</GUID><Name>new</Name></Standard>%s<Building>"
        "<Radius>%s</Radius></Building>%s</Values></Asset></ModOp>"
    )
    ARITH = '<ModValue Insert="2+3"/>'
    OPTION = '<ModValue Insert="$size"/>'
    FROM_1001 = '<ModValue Insert="@1001/Building/Radius/text()"/>'
    TWICE_ITSELF = '<ModValue Insert="self::node() * 2"/>'

    def not_read(self, body, why):
        """The op is carried exactly as written, with ``why`` in the reason,
        and nothing is shown for it."""
        report = self.settled(body)
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED], body)
        self.assertIn(why, report.results[0].reason)
        self.assertEqual((report.changes, report.ops), ([], []))
        (entry,) = report.unread("a mod")
        self.assertTrue(keptops.same_op(entry.source, body), entry.source)
        self.assertEqual(
            [node.tail for node in etree.fromstring(entry.source).iter("ModValue")],
            [node.tail for node in etree.fromstring(body).iter("ModValue")],
        )

    def radius(self, body):
        report = self.settled(body)
        self.assertEqual(
            [(r.status, r.reason) for r in report.results if r.status != modops.IMPORTED], [], body
        )
        return [(c.path, c.old_value, c.new_value) for c in report.changes]

    def added(self, body):
        report = self.settled(body)
        self.assertEqual(
            [(r.status, r.reason) for r in report.results if r.status != modops.IMPORTED], [], body
        )
        return [(c.op, "".join((c.payload or "").split())) for c in report.changes]

    def test_under_a_merge_it_is_read_as_it_was(self):
        """In game: ``2+3`` gives 5, ``self::node() * 2`` twice what the field
        held, a copy what it copies and an option what it is set to, in an
        element of the op's body or straight inside the op."""
        for inside, expected in (
            (self.ARITH, "5"),
            (self.TWICE_ITSELF, "20"),
            (self.OPTION, "20"),
            ('<ModValue Insert="self::node() * $factor"/>', "5"),
            (self.FROM_1001, "4"),
        ):
            for shape in (
                self.MERGED,
                '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">%s</ModOp>',
                '<ModOp Merge="@1000/Building"><Radius>%s</Radius></ModOp>',
            ):
                with self.subTest(inside=inside, shape=shape):
                    self.assertEqual(
                        self.radius(shape % inside), [("/Values/Building/Radius", "10", expected)]
                    )

    def test_text_after_a_mark_under_a_merge_is_what_the_field_ends_up_holding(self):
        """In game ``1<ModValue Insert="2+3"/>7`` leaves the field at ``7``,
        and so does the mark with ``7`` after it alone.  It was read as
        ``15``, then not at all."""
        for inside, expected in (
            ("1" + self.ARITH + "7", "7"),
            (self.ARITH + "7", "7"),
            (self.OPTION + "7", "7"),
            (self.TWICE_ITSELF + "7", "7"),
            (self.FROM_1001 + "7", "7"),
            ("\n  " + self.ARITH + "\n  7\n", "7"),
        ):
            with self.subTest(inside=inside):
                self.assertEqual(
                    self.radius(self.MERGED % inside), [("/Values/Building/Radius", "10", expected)]
                )
        self.assertEqual(
            self.radius('<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
                        '<ModValue Insert="self::node() * 2"/>5</ModOp>'),
            [("/Values/Building/Radius", "10", "5")],
        )

    def test_text_before_a_mark_under_a_merge_is_dropped(self):
        """In game ``1<ModValue Insert="2+3"/>`` leaves the field at ``5``.
        The shipped files write a ``0`` there, so the field reads as a number
        with nothing put in.  ``self::node()`` is still what the game held."""
        for inside, expected in (
            (self.ARITH, "5"),
            ("\n    " + self.ARITH + "\n  ", "5"),
            ("0" + self.ARITH, "5"),
            ("\n    0" + self.ARITH + "\n  ", "5"),
            ("1" + self.ARITH, "5"),
            ("\n  1\n  " + self.ARITH + "\n", "5"),
            ("3" + self.TWICE_ITSELF, "20"),
            ("3" + self.OPTION, "20"),
            ("9" + self.FROM_1001, "4"),
        ):
            with self.subTest(inside=inside):
                self.assertEqual(
                    self.radius(self.MERGED % inside), [("/Values/Building/Radius", "10", expected)]
                )

    def test_a_field_replaced_is_read(self):
        """In game: 5 for ``2+3``, and the option and the copy as under a
        merge.  ``Replace=`` is the same op."""
        for inside, expected in ((self.ARITH, "5"), (self.OPTION, "20"), (self.FROM_1001, "4")):
            for shape in (
                self.REPLACED,
                '<ModOp Replace="@1000/Building/Radius"><Radius>%s</Radius></ModOp>',
            ):
                with self.subTest(inside=inside, shape=shape):
                    self.assertEqual(
                        self.radius(shape % inside), [("/Values/Building/Radius", "10", expected)]
                    )

    def test_a_section_replaced_is_read(self):
        """What *Ceres* and its like write: a section swapped whole, one of
        its values from an option."""
        self.assertEqual(
            self.radius(
                '<ModOp Type="replace" GUID="1000" Path="/Values/Building"><Building>'
                "<BuildingType>Factory</BuildingType>"
                '<Radius><ModValue Insert="0 + $size"/></Radius></Building></ModOp>'
            ),
            [("/Values/Building/Radius", "10", "20")],
        )

    def test_a_field_put_in_new_has_no_value_of_its_own_to_build_on(self):
        """In game a replace with ``self::node() * 2`` leaves the text
        ``NaN``, and so does a new asset.  The Studio read 20, twice the
        game's value.  It gives no number now and says what the game writes."""
        for body in (
            self.REPLACED % self.TWICE_ITSELF,
            '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>'
            % self.TWICE_ITSELF,
            self.NEW_ASSET % ("", self.TWICE_ITSELF, ""),
            # A merge puts in a field the game does not have as an add does.
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>'
            % self.TWICE_ITSELF,
        ):
            with self.subTest(body=body):
                self.not_read(body, "the game writes NaN")

    def test_a_mark_straight_inside_a_replace_takes_the_field_away(self):
        """In game the field is gone afterwards.  It was read as the field
        set to the value, 20."""
        self.not_read(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            '<ModValue Insert="$size"/></ModOp>',
            "leaves the game with no such field",
        )
        self.not_read(
            '<ModOp Replace="@1000/Building/Radius"><ModValue Insert="2+3"/></ModOp>',
            "leaves the game with no such field",
        )

    def test_a_field_added_is_read(self):
        """In game each of these adds a field holding 5."""
        for body, op, path in (
            ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>',
             OP_ADD, "/Values/Building"),
            ('<ModOp Add="@1000/Building"><Extra>%s</Extra></ModOp>', OP_ADD, "/Values/Building"),
            ('<ModOp Type="addNextSibling" GUID="1000" Path="/Values/Building/Radius">'
             "<Extra>%s</Extra></ModOp>", OP_ADD_NEXT, "/Values/Building/Radius"),
            ('<ModOp Type="addPrevSibling" GUID="1000" Path="/Values/Building/Radius">'
             "<Extra>%s</Extra></ModOp>", OP_ADD_PREV, "/Values/Building/Radius"),
            ('<ModOp Append="@1000/Building/Radius"><Extra>%s</Extra></ModOp>',
             OP_ADD_NEXT, "/Values/Building/Radius"),
        ):
            with self.subTest(body=body):
                report = self.settled(body % self.ARITH)
                self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
                self.assertEqual(
                    [(c.op, c.path, c.payload) for c in report.changes],
                    [(op, path, "<Extra>5</Extra>")],
                )
        self.assertEqual(
            self.added('<ModOp Type="add" GUID="1000" Path="/Values/Building">'
                       "<Extra><N>%s</N><M>%s</M></Extra></ModOp>" % (self.OPTION, self.FROM_1001)),
            [(OP_ADD, "<Extra><N>20</N><M>4</M></Extra>")],
        )

    def test_an_asset_added_whole_is_read(self):
        """What seven mods on mod.io write, 433 ops of them: a new asset
        whose values come from the mod's options.  In game, asked in the
        file that adds it and from a mod loaded after it: ``2+3`` is 5, the
        option what it is set to, ``3 * $opt`` on a line of its own three
        times that, and a copy what it copies."""
        for inside, expected in (
            (self.ARITH, "5"),
            (self.OPTION, "20"),
            ('\n            <ModValue Insert="3 * $size"/>\n          ', "60"),
            (self.FROM_1001, "4"),
        ):
            with self.subTest(inside=inside):
                ((op, payload),) = self.added(self.NEW_ASSET % ("", inside, ""))
                self.assertEqual(op, OP_ADD_NEXT)
                self.assertIn("<GUID>5000</GUID>", payload)
                self.assertIn("<Building><Radius>%s</Radius></Building>" % expected, payload)
                self.assertNotIn("ModValue", payload)

    def test_text_beside_a_mark_in_a_field_put_in_new_is_not_read(self):
        """In game ``1<ModValue Insert="2+3"/>7`` under a replace and in a
        new asset is left holding ``17`` with the mark still in it.  With
        text only before the mark xmltest3 leaves it the same way, the
        shipped files' ``0`` included."""
        for inside in ("1" + self.ARITH + "7", "1" + self.ARITH, "0" + self.OPTION,
                       "9" + self.FROM_1001):
            for shape in (
                self.REPLACED,
                '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>',
                self.NEW_ASSET % ("", "%s", ""),
                '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>',
            ):
                with self.subTest(inside=inside, shape=shape):
                    self.not_read(shape % inside, "leaves the mark as written")

    def test_text_only_after_a_mark_in_a_field_put_in_new_is_not_guessed_at(self):
        """The game was not asked.  xmltest3 gives the value and drops the
        text, xmltest2 leaves both under a replace, and no mod writes it."""
        for shape in (self.REPLACED, self.NEW_ASSET % ("", "%s", "")):
            with self.subTest(shape=shape):
                self.not_read(shape % (self.ARITH + "7"), "is not known")

    # An asset added whole, by what stands in it besides the section holding
    # the mark: before <Standard>, inside it, between it and the section,
    # inside the section before the mark's field, and after the section.
    HOLLOW = (
        '<ModOp Type="%s" GUID="1000"><Asset>%s<Values>%s<Standard><GUID>5000</GUID>'
        "<Name>new</Name>%s</Standard>%s<Building>%s<Radius>"
        '<ModValue Insert="$size"/></Radius></Building>%s</Values></Asset></ModOp>'
    )

    def hollow(self, kind="AddNextSibling", template="<Template>Building</Template>", first="",
               named="", between="", beside="", after=""):
        return self.HOLLOW % (kind, template, first, named, between, beside, after)

    def test_marks_after_an_empty_element_in_an_asset_added_whole_are_not_read(self):
        """In game: with ``<Hollow/>`` among the asset's sections before the
        one holding the marks, every mark is left in the file and its field
        has no value.  The same asset with it after them has its values."""
        for kind in ("AddNextSibling", "addPrevSibling"):
            with self.subTest(kind=kind):
                self.not_read(self.hollow(kind, between="<Hollow/>"), "the empty element <Hollow>")
                ((_op, payload),) = self.added(self.hollow(kind, after="<Hollow/>"))
                self.assertIn("<Radius>20</Radius>", payload)

    def test_and_where_that_line_runs(self):
        """Measured with xmltest3
        (``test_an_empty_element_stops_the_marks_after_it_at_its_own_level``
        in test_modio_loader.py, the same list).  The loader stops, at each
        level of what is put in, at the first thing with nothing in it that
        has something after it, and goes on one level up."""
        for what, body, stopped_by in (
            ("written with an end tag", self.hollow(between="<Hollow></Hollow>"), "Hollow"),
            ("holding layout", self.hollow(between="<Hollow>\n  </Hollow>"), "Hollow"),
            ("holding a comment", self.hollow(between="<Hollow><!-- c --></Hollow>"), "Hollow"),
            ("with an attribute", self.hollow(between='<Hollow a="1" />'), "Hollow"),
            ("first among the sections", self.hollow(first="<Hollow/>"), "Hollow"),
            ("in the mark's own section", self.hollow(beside="<Hollow/>"), "Hollow"),
            ("before <Values>", self.hollow(template="<Template>Building</Template><Hollow/>"),
             "Hollow"),
            ("the template itself", self.hollow(template="<Template/>"), "Template"),
            ("a comment and no element", self.hollow(between="<!-- c -->"), ""),
            ("last in an earlier section", self.hollow(named="<Hollow/>"), ""),
            ("in an earlier section, a field after it",
             self.hollow(between="<Other><Hollow/><F>1</F></Other>"), ""),
            ("alone in an earlier section", self.hollow(between="<Other><Hollow/></Other>"), ""),
        ):
            with self.subTest(what):
                if stopped_by:
                    self.not_read(body, "the empty element <%s>" % stopped_by)
                else:
                    ((_op, payload),) = self.added(body)
                    self.assertIn("<Radius>20</Radius>", payload)

    def test_an_empty_element_stops_only_what_follows_it(self):
        """A mark before it is worked out and one after it is not, so the op
        is not read.  One in another op, or in another asset of the same op,
        is nothing to this one."""
        two = (
            '<ModOp Type="AddNextSibling" GUID="1000"><Asset><Template>Building</Template><Values>'
            "<Standard><GUID>5000</GUID><Name>new</Name></Standard><Building>"
            "<Radius>%s</Radius><Hollow/><Kind>%s</Kind></Building></Values></Asset></ModOp>"
        )
        self.not_read(two % (self.ARITH, self.OPTION), "the empty element <Hollow>")
        ((_op, payload),) = self.added(two % (self.ARITH, "plain"))
        self.assertIn("<Radius>5</Radius><Hollow/><Kind>plain</Kind>", payload)
        other = self.hollow(between="<Hollow/>").replace("5000", "5001").replace(
            '<ModValue Insert="$size"/>', "1"
        )
        report = self.settled(other + self.hollow())
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED] * 2)
        self.assertIn("<Radius>20</Radius>", report.changes[1].payload)

    def test_text_loose_among_the_elements_put_in_stops_them_as_well(self):
        self.not_read(self.hollow(between="loose"), "text standing loose")

    def test_a_field_added_with_a_path_follows_the_same_line(self):
        """Measured with xmltest3: inside the element added, an empty one
        before the mark's field leaves the mark.  Each element straight
        inside the op is gone through on its own, so an empty one beside the
        element holding the mark stops nothing."""
        add = '<ModOp Type="%s" GUID="1000" Path="/Values/Building%s">%s</ModOp>'
        inside = "<Extra><Hollow/><N>%s</N></Extra>" % self.ARITH
        for kind, path in (("add", ""), ("addNextSibling", "/Radius"), ("addPrevSibling", "/Radius")):
            with self.subTest(kind=kind):
                self.not_read(add % (kind, path, inside), "the empty element <Hollow>")
        self.not_read(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building"><Building><Hollow/>'
            "<Radius>%s</Radius></Building></ModOp>" % self.ARITH,
            "the empty element <Hollow>",
        )
        self.assertEqual(
            self.added(add % ("add", "", "<Hollow/><Extra>%s</Extra>" % self.ARITH)),
            [(OP_ADD, "<Hollow/>"), (OP_ADD, "<Extra>5</Extra>")],
        )
        self.assertEqual(
            self.added(add % ("add", "", "<Extra><N>%s</N><Hollow/></Extra>" % self.ARITH)),
            [(OP_ADD, "<Extra><N>5</N><Hollow/></Extra>")],
        )
        self.assertEqual(
            self.added(add % ("add", "", "<Extra><S><Hollow/></S><N>%s</N></Extra>" % self.ARITH)),
            [(OP_ADD, "<Extra><S><Hollow/></S><N>5</N></Extra>")],
        )

    def test_and_so_does_what_a_merge_puts_in_new(self):
        """Measured with xmltest3: a section the game does not have is gone
        through as an addition is.  What a merge finds in the game is not:
        there an empty element before the mark changes nothing."""
        merge = '<ModOp Type="merge" GUID="1000" Path="/Values%s">%s</ModOp>'
        self.not_read(
            merge % ("", "<Fresh><Hollow/><N>%s</N></Fresh>" % self.ARITH),
            "the empty element <Hollow>",
        )
        self.not_read(
            merge % ("", "<Building><Fresh><Hollow/><N>%s</N></Fresh></Building>" % self.ARITH),
            "the empty element <Hollow>",
        )
        for path, inside in (
            ("", "<Fresh><N>%s</N></Fresh>"),
            ("/Building", "<Hollow/><Fresh>%s</Fresh>"),
            ("", "<Building><Hollow/><Fresh>%s</Fresh></Building>"),
        ):
            with self.subTest(inside=inside):
                report = self.settled(merge % (path, inside % self.ARITH))
                self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
                self.assertIn(">5<", "".join(c.payload or "" for c in report.changes))
        for path, inside in (
            ("/Building", "<Hollow/><Radius>%s</Radius>"),
            ("", "<Empty/><Building><Radius>%s</Radius></Building>"),
            ("", "<Building><Hollow/><Radius>%s</Radius></Building>"),
        ):
            with self.subTest(inside=inside):
                self.assertIn(
                    ("/Values/Building/Radius", "10", "5"),
                    self.radius(merge % (path, inside % self.ARITH)),
                )

    def test_a_copy_the_game_was_not_asked_about_is_not_read_outside_a_merge(self):
        """It was asked about a value copied from another asset with
        ``/text()``.  One copied from beside where it lands xmltest3 looks
        for from where the mark stands in what the op puts in, which is not
        where the Studio looks."""
        for inside in ('<ModValue Insert="../BuildingType/text()"/>',
                       '<ModValue Insert="@1001/Building"/>'):
            for shape in (self.REPLACED, self.NEW_ASSET % ("", "%s", "")):
                with self.subTest(inside=inside, shape=shape):
                    self.not_read(shape % inside, "in an op that is not a merge")

    def test_text_beside_a_copy_is_run_together_with_it(self):
        """``<ModOpContent/>`` is another matter: every build of the loader
        puts the text before it, the copy and the text after it together."""
        report = self.settled(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Building/Radius/text()"><Copied>1<ModOpContent/>7</Copied></ModOp>'
        )
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
        self.assertEqual([c.payload for c in report.changes], ["<Copied>147</Copied>"])

    # A ``<ModOpContent/>`` straight inside the op, not inside an element of
    # its body.  It was read as the addition of an element of that name.
    STRAIGHT_INSIDE = (
        '<ModOp Type="%s" GUID="1000" Path="/Values/Standard/Name" Content="@%s">%s</ModOp>'
    )

    def test_a_copy_marked_straight_inside_an_add_is_added_as_text(self):
        """Every build appends the text before the mark, the copy and the
        text after it to what the field says, once for each node copied
        (``test_a_copy_marked_straight_inside_an_add_is_added_as_text`` in
        test_modio_loader.py, the same eight), and layout alone on either
        side is not text."""
        for inside, expected in (
            ("before<ModOpContent/>after", "alphabefore4after"),
            ("before<ModOpContent/>", "alphabefore4"),
            ("<ModOpContent/>after", "alpha4after"),
            ("<ModOpContent/>", "alpha4"),
            ("\n  <ModOpContent/>\n", "alpha4"),
            ("\n  be fore \n <ModOpContent /> af ter \n", "alpha\n  be fore \n 4 af ter \n"),
            ("&lt;br/&gt;(<ModOpContent />)", "alpha<br/>(4)"),
            ("[<ModOpContent/>]", "alpha[2001][2002][2003]"),
        ):
            source = "1000/ProductList/List/Item/Product" if "[" in inside else "1001/Building/Radius"
            body = self.STRAIGHT_INSIDE % ("add", source + "/text()", inside)
            with self.subTest(inside=inside):
                report = self.settled(body)
                self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
                self.assertEqual(
                    [(c.op, c.path, c.old_value, c.new_value, c.payload) for c in report.changes],
                    [(OP_REPLACE, "/Values/Standard/Name", "alpha", expected, "")],
                )

    def test_and_kept_as_written_it_is_the_op_it_was(self):
        body = self.STRAIGHT_INSIDE % ("add", "1001/Building/Radius/text()", "(<ModOpContent/>)")
        (result,) = self.settled(body).results
        self.assertEqual(result.record.source, body)
        self.assertIn("copies its value from another asset", result.record.kept)

    def test_and_with_elements_of_its_own_the_op_is_read_by_those(self):
        """Text among an op's elements is carried and given no meaning, and
        text that is copied there is no other: every build leaves it loose
        after the element."""
        report = self.settled(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Building/Radius/text()"><Extra>1</Extra><ModOpContent/> more</ModOp>'
        )
        self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
        self.assertEqual([c.payload for c in report.changes], ["<Extra>1</Extra>"])

    def test_marked_straight_inside_anything_else_it_is_not_guessed_at(self):
        """The builds agree on these as well, and what they agree on is text
        left loose among fields or mixed with an element inside one
        (``test_marked_straight_inside_anything_else_it_comes_to_text_left_loose``
        in test_modio_loader.py).  The docs say nothing of any of it, so the
        op is carried and nothing is shown for it."""
        text, node = "1001/Building/Radius/text()", "1001/Building/Radius"
        for kind, source, inside in (
            ("add", node, "before<ModOpContent/>after"),
            ("add", node, "<ModOpContent/>after"),
            ("add", text, "a<ModOpContent/>b<ModOpContent/>c"),
            ("add", text, "a<!-- why -->b<ModOpContent/>"),
            ("add", text, '<ModOpContent SkipParent="1"/>'),
            ("addNextSibling", text, "before<ModOpContent/>after"),
            ("addPrevSibling", text, "<ModOpContent/>"),
            ("replace", text, "before<ModOpContent/>after"),
            ("replace", text, "<ModOpContent/>"),
            ("merge", text, "before<ModOpContent/>after"),
            ("merge", node, "<ModOpContent/>after"),
        ):
            body = self.STRAIGHT_INSIDE % (kind, source, inside)
            with self.subTest(kind=kind, inside=inside):
                report = self.settled(body)
                self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
                self.assertIn("straight into the op", report.results[0].reason)
                self.assertEqual((report.changes, report.ops), ([], []))
                (entry,) = report.unread("a mod")
                self.assertEqual(entry.source, body)

    def test_nor_is_such_text_added_to_a_section(self):
        """There every build leaves it loose after the section's fields."""
        report = self.settled(
            '<ModOp Type="add" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Building/Radius/text()">(<ModOpContent/>)</ModOp>'
        )
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertIn("straight into the op", report.results[0].reason)
        self.assertEqual(report.changes, [])

    def test_a_mark_alone_that_stands_for_whole_nodes_is_read_as_it_was(self):
        """Thirteen ops in four census mods write ``<ModOpContent/>`` and
        nothing else in an op that copies nodes.  Every build puts the nodes
        where an op with no body at all would, and so does the reading."""
        for kind, expected in (
            (OP_ADD, [(OP_ADD, "/Values/Building", "<Radius>4</Radius>")]),
            ("addNextSibling", [("addNextSibling", "/Values/Building/Radius", "<Radius>4</Radius>")]),
        ):
            path = "/Values/Building" + ("" if kind == OP_ADD else "/Radius")
            for inside in ("<ModOpContent/>", "\n  <ModOpContent/>\n", ""):
                with self.subTest(kind=kind, inside=inside):
                    report = self.settled(
                        '<ModOp Type="%s" GUID="1000" Path="%s" Content="@1001/Building/Radius">'
                        "%s</ModOp>" % (kind, path, inside)
                    )
                    self.assertEqual([r.status for r in report.results], [modops.IMPORTED])
                    self.assertEqual(
                        [(c.op, c.path, c.payload) for c in report.changes], expected
                    )

    def test_a_value_copied_from_another_asset(self):
        report = self.settled(self.COPIED)
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        self.assertEqual(report.changes[0].new_value, "4")

    def test_a_whole_section_copied_from_another_asset(self):
        report = self.settled(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Building"></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        self.assertEqual(
            sorted((c.tag, c.new_value) for c in report.changes),
            [("BuildingType", "Farm"), ("Radius", "4")],
        )

    def test_a_copy_unasked_for_is_read_at_what_it_comes_to_and_kept_as_written(self):
        """It used to be refused outright, so that the export would not freeze
        the link.  But not reading it meant not showing it either, and the
        editor then claimed a value the game does not have.  The setting now
        decides what goes back out, not whether the op is read: the change is
        what the copy comes to today, and what goes out is the op itself."""
        report = self.settled(self.COPIED)
        result = report.results[0]
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertEqual(report.changes[0].new_value, "4")
        self.assertIsNotNone(result.record)
        self.assertIn("copies its value from another asset", result.record.kept)
        self.assertEqual(result.record.source, self.COPIED)
        self.assertEqual(result.record.matched, ["1000"])
        self.assertEqual(report.changes[0].op_uid, result.record.uid)
        self.assertIn("kept as written", result.reason)

    def test_a_computed_value_unasked_for_is_read_the_same_way(self):
        """Not a section swap, and not unread either.

        Putting an element where the text was is not a shape a narrow ModOp
        has, and the diff's fallback for that is to replace the whole enclosing
        section -- which reads as the mod deleting and retyping a section it
        never touched.  So the value is settled for the editor whatever the
        setting says, and the setting decides that the op goes back out as
        itself.
        """
        report = self.settled(self.SETTING)
        result = report.results[0]
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertEqual(
            [(c.op, c.old_value, c.new_value) for c in report.changes],
            [(OP_REPLACE, "10", "20")],
        )
        self.assertIn("works its value out", result.record.kept)

    def test_a_setting_it_has_no_value_for(self):
        report = self.settled(self.SETTING, chosen={})
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertIn("size option is not declared", report.results[0].reason)

    def test_copying_from_an_asset_that_is_not_there(self):
        report = self.settled(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius" '
            'Content="@9999/Building/Radius"></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertIn("assets.xml does not have", report.results[0].reason)

    def test_a_copy_guarded_by_a_condition_that_does_not_hold_is_settled(self):
        """The mod says "only if there is anything to copy", so an op that does
        not run is not an op the Studio failed to read."""
        report = self.settled(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius" '
            'Content="@1001/Building/Nothing" '
            'Condition="@1001/Building/Nothing"></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.SETTLED)

    def test_writing_a_value_where_the_game_has_a_section(self):
        report = self.settled(
            '<ModOp Type="merge" GUID="1000" Path="/Values">'
            '<Building><ModValue Insert="$size"/></Building></ModOp>'
        )
        self.assertEqual(report.results[0].status, modops.BLOCKED)
        self.assertIn("section and not a field", report.results[0].reason)

    def test_arithmetic_is_read_rather_than_run(self):
        self.assertEqual(reader.computed_value("2 * (3 + 1)", None, {}), "8")
        with self.assertRaises(reader.Uncomputable):
            reader.computed_value("__import__('os').getcwd()", None, {})

    def test_a_computed_value_is_written_as_a_number_the_game_can_read(self):
        """This goes straight into the payload of an exported ModOp.

        %g formatting kept six *significant* digits and reached for an exponent
        past seven, so an ordinary 1234567.25 was written into a shipped mod as
        "1.23457e+06" -- which Anno cannot read as a number at all.
        """
        for expression, expected in (
            ("1234567.25", "1234567.25"),
            ("1234567.25 * 1", "1234567.25"),
            ("123456.789 * 1", "123456.789"),
            ("100000000 + 0.5", "100000000.5"),
            ("2.0000001 * 1", "2.0000001"),
            ("30 * 1.5", "45"),
        ):
            with self.subTest(expression=expression):
                got = reader.computed_value(expression, None, {})
                self.assertEqual(got, expected)
                self.assertNotIn("e", got.lower())

    def test_a_runaway_expression_is_answered_rather_than_crashed_on(self):
        """The expression comes out of somebody else's mod.

        Uncomputable is one of the two answers this may give; RecursionError
        and MemoryError out of the parser are neither.
        """
        for label, expression in (
            ("a long chain", "+".join(["1"] * 50000)),
            ("deep nesting", "(" * 300 + "1" + ")" * 300),
            ("stacked unary", "-" * 20000 + "1"),
        ):
            with self.subTest(label=label):
                with self.assertRaises(reader.Uncomputable):
                    reader.computed_value(expression, None, {})

    def test_a_computed_value_that_is_not_a_number_is_refused(self):
        with self.assertRaises(reader.Uncomputable):
            reader.computed_value("1e308 * 10", None, {})
class _KeptOps(SessionCase):
    """A search is a question, and the assets that answer it can change.

    So the op is kept as well as its answer: an Op holding the ModOp verbatim
    with the changes it came to hanging off it.  That is what lets the question
    be put to an asset that was not there the first time, and what lets the mod
    be written back out still asking it.
    """

    SEARCH = (
        '<ModOp Type="merge" Path="//Values/Building"><Elevation>3</Elevation></ModOp>'
    )
    # The same shape, but writing a field the assets already have, so what it
    # comes to is a replacement with a value to look at rather than an addition.
    RADIUS = (
        '<ModOp Type="merge" Path="//Values/Building"><Radius>99</Radius></ModOp>'
    )

    def loaded(self, root, **kwargs):
        """Read a folder into a loadout the way the window does."""
        session = self.session(root, **kwargs)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
        return loadout

    def written(self, loadout, origin="a mod"):
        return generator.build_modops_xml(
            generator.for_mod(loadout, origin)
        )

    def invents(self, loadout, origin):
        """Have ``origin`` create a building the search would answer to."""
        loadout.set_layer(ModLayer(origin=origin, name=origin))
        loadout.set_change(
            Change(
                guid="1000", asset_label="anchor", path="", tag="Asset",
                op=OP_ADD_NEXT, summary="create asset 7777", origin=origin,
                payload="<Asset><Template>Building</Template><Values><Standard>"
                        "<GUID>7777</GUID><Name>invented</Name></Standard>"
                        "<Building><Radius>2</Radius></Building></Values></Asset>",
            )
        )


class TestWhatAKeptSearchRemembers(_KeptOps):
    """What a search read in keeps of itself."""

    # -- what is remembered ----------------------------------------------
    def test_every_change_a_search_made_remembers_which_op(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.assertTrue(loadout.changes)
        uid = loadout.ops[0].uid
        for change in loadout:
            self.assertEqual(change.search, "//Values/Building")
            self.assertEqual(change.op_uid, uid)

    def test_the_op_keeps_the_modop_itself_not_a_summary_of_it(self):
        """The whole difference between a kept op and a note about one."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        op = loadout.ops[0]
        self.assertEqual(op.source, self.SEARCH)
        self.assertTrue(op.can_expand)
        self.assertEqual(op.summary, "merge Elevation = 3")

    def test_it_remembers_everything_the_search_matched(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.assertEqual(sorted(loadout.ops[0].matched), ["1000", "1001"])

    def test_including_the_assets_it_had_nothing_to_do_to(self):
        """1001 is already a Farm, so it takes no change -- and is still one of
        the assets this mod's search is about."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building">'
                "<BuildingType>Farm</BuildingType></ModOp>"
            )
        )
        view = keptops.of(loadout)[0]
        self.assertEqual(view.assets, ("1000",))
        self.assertEqual(sorted(view.matched), ["1000", "1001"])
        self.assertIn("1 change across 1 of 2 matching assets", view.caption())

    def test_a_search_that_matched_nothing_at_all_is_still_kept(self):
        """It is precisely the one that starts answering when a mod adds an
        asset shaped the way it asks for."""
        loadout = self.loaded(
            self.mods(a__mod='<ModOp Type="merge" Path="//Values/Harbour">'
                             "<Radius>10</Radius></ModOp>")
        )
        self.assertEqual([op.expression for op in loadout.ops], ["//Values/Harbour"])
        self.assertEqual(loadout.ops[0].matched, [])

    def test_a_change_says_what_it_was_one_of(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        change = next(iter(loadout))
        self.assertEqual(
            keptops.describe(loadout, change),
            "One of 2 assets matching //Values/Building",
        )

    def test_and_an_ordinary_change_says_nothing(self):
        loadout = self.loaded(
            self.mods(a__mod='<ModOp Merge="@1000/Building"><Radius>3</Radius></ModOp>')
        )
        self.assertEqual(keptops.describe(loadout, next(iter(loadout))), "")

    def test_one_mod_may_target_by_the_same_search_twice(self):
        """Two fields, one shape -- which is why a change names its op by uid
        rather than by the expression it shares with the other."""
        loadout = self.loaded(
            self.mods(
                a__mod=self.SEARCH
                + '<ModOp Type="merge" Path="//Values/Building">'
                "<Radius>99</Radius></ModOp>"
            )
        )
        self.assertEqual(len(loadout.ops), 2)
        self.assertEqual(
            {op.expression for op in loadout.ops}, {"//Values/Building"}
        )
        self.assertEqual(
            len({change.op_uid for change in loadout}), 2
        )

    def test_the_kept_ops_survive_saving_and_opening(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            loadout.save(path)
            reopened = Loadout.load(path)
        op = reopened.ops[0]
        self.assertEqual(sorted(op.matched), ["1000", "1001"])
        self.assertEqual(op.source, self.SEARCH)
        self.assertEqual({change.op_uid for change in reopened}, {op.uid})


class TestWritingAKeptSearchBackOut(_KeptOps):
    """A search written back out as the op it came from, and where it cannot be."""

    # -- writing one back out --------------------------------------------
    def test_a_whole_search_is_written_back_as_the_op_it_came_from(self):
        """The point of keeping it: a list of GUIDs covers what answered the
        search that day, and the search covers what answers it in play."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        written = self.written(loadout)
        self.assertIn('Path="//Values/Building"', written)
        self.assertIn("<Elevation>3</Elevation>", written)

    def test_and_its_changes_are_not_written_a_second_time(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        written = self.written(loadout)
        self.assertNotIn('GUID="1000"', written)
        self.assertEqual(written.count("<ModOp "), 1)

    def test_switching_the_op_off_writes_it_as_a_comment(self):
        """The op is the fact.  It is on or off as one thing, and off it
        comes to nothing: its answers go off with it, and nothing is written
        for the game -- the op is there as a comment."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building">'
                "<Radius>99</Radius></ModOp>"
            )
        )
        self.assertEqual(len(loadout.changes), 2)
        loadout.ops[0].active = False
        for change in loadout:
            change.active = False
        view = keptops.of(loadout)[0]
        self.assertFalse(view.whole)
        self.assertIn("switched off", view.reason)
        written = self.written(loadout)
        self.assertEqual(_live_ops(written), [])
        self.assertEqual(written.count("<ModOp "), 1)
        self.assertIn(SWITCHED_OFF_MARK, written)

    def test_one_of_its_answers_marked_off_does_not_narrow_it(self):
        """A search applies to every asset it matches or to none, so a change
        of its marked off by hand is a caveat on the list, not a hole in the
        op.  It used to collapse the op into narrow ModOps, which claimed
        that the author had excluded an asset the op still covers in game."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building">'
                "<Radius>99</Radius></ModOp>"
            )
        )
        next(iter(loadout)).active = False
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        self.assertIn("cannot honour", view.caveat)
        written = self.written(loadout)
        self.assertIn('Path="//Values/Building"', written)
        self.assertNotIn("GUID=", written)

    def test_nor_does_reverting_one(self):
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building">'
                "<Radius>99</Radius></ModOp>"
            )
        )
        loadout.remove_key(next(iter(loadout)).key)
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        self.assertIn("reverted", view.caveat)
        self.assertIn('Path="//Values/Building"', self.written(loadout))

    def test_an_op_read_from_an_older_loadout_writes_narrowly_too(self):
        """It has no op to write, so its changes are all there is."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.ops[0].source = ""
        self.assertNotIn('Path="//Values/Building"', self.written(loadout))
        self.assertIn('GUID="1000"', self.written(loadout))

    # -- when a search cannot speak for itself ----------------------------
    def test_a_search_that_ran_after_another_op_keeps_its_place_in_the_file(self):
        """Found on real mods: *-One Free Reroll* names nine traders and then
        says "every trader", so the search did less because the first op had
        already been.  Written at the front it would do that work twice -- and
        for an addition, twice means two of the node.  Written where the mod put
        it, the question does not arise."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<Elevation>3</Elevation></ModOp>"
                + self.SEARCH
            )
        )
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        written = self.written(loadout)
        self.assertLess(
            written.index('GUID="1000"'), written.index('Path="//Values/Building"')
        )
        # And exactly one of each: the named op for the asset it named, the
        # search for the rest.
        self.assertEqual(written.count("<ModOp "), 2)

    def test_and_one_that_ran_before_keeps_its_place_too(self):
        loadout = self.loaded(
            self.mods(
                a__mod=self.RADIUS
                + '<ModOp Type="merge" GUID="1000"><Radius>5</Radius></ModOp>'
            )
        )
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        written = self.written(loadout)
        self.assertLess(
            written.index('Path="//Values/Building"'), written.index('GUID="1000"')
        )

    def test_and_between_the_changes_of_ops_written_narrow(self):
        """The two tests above write kept ops alone, so writing every kept op
        ahead of every block of changes passed them.  Here the named ops are
        written narrow, each a block of the Studio's where its op stood, and
        the search kept as written still sits between them."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<Radius>5</Radius></ModOp>"
                + self.SEARCH
                + '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<BuildingType>Mill</BuildingType></ModOp>"
            )
        )
        named = [op.uid for op in loadout.ops if not op.searches]
        self.assertEqual(keptops.write_narrow(loadout, named)[0], 2)
        self.assertEqual([op.expression for op in loadout.ops], ["//Values/Building"])
        written = self.written(loadout)
        first = written.index("<Radius>5</Radius>")
        search = written.index('Path="//Values/Building"')
        second = written.index("<BuildingType>Mill</BuildingType>")
        self.assertLess(first, search)
        self.assertLess(search, second)

    def test_each_op_gets_its_own_block_so_a_search_can_sit_between_two(self):
        """Grouping an asset's changes by asset alone collapsed two ops into
        one block, which put a search on the wrong side of half of them."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<Radius>5</Radius></ModOp>"
                + self.SEARCH
                + '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<BuildingType>Mill</BuildingType></ModOp>"
            )
        )
        written = self.written(loadout)
        # Both named ops are about asset 1000; the search belongs between them.
        first = written.index('GUID="1000"')
        search = written.index('Path="//Values/Building"')
        second = written.index('GUID="1000"', search)
        self.assertLess(first, search)
        self.assertLess(search, second)

    def test_whether_it_can_be_written_as_a_search_is_replayed_not_guessed(self):
        """The mod is applied again the way the game would, with the search as
        its own op sitting where it sat, and the answer is whether every
        asset still reads the same."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                "<Elevation>3</Elevation></ModOp>"
                + self.SEARCH
            )
        )
        op = loadout.ops[0]
        self.assertEqual(op.caveat, "")
        # 1000 is reached by both ops and is still not a reason to refuse it.
        self.assertIn("1000", op.matched)

    def test_searches_keep_the_order_the_mod_wrote_them_in(self):
        """Two searches can hit the same node, and the second one's answer
        depends on what the first one did -- so the file order is the answer."""
        second = (
            "<ModOp Type=\"merge\" Path=\"//Values[Standard/Name='alpha']/Building\">"
            "<Radius>7</Radius></ModOp>"
        )
        loadout = self.loaded(self.mods(a__mod=self.RADIUS + second))
        written = self.written(loadout)
        self.assertLess(
            written.index('"//Values/Building"'),
            written.index("Standard/Name='alpha'"),
        )

    def test_a_search_that_copies_its_value_is_kept_as_written(self):
        """It used to come in as nothing at all -- refused, so that the export
        could not freeze the copy.  Read at what it comes to today and written
        back as itself, it is kept like any other search, and says why it
        must go out as the op."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building" '
                'Content="@1000/Building/Radius"/>'
            )
        )
        self.assertEqual(len(loadout.ops), 1)
        # A search is kept in its own right, so it needs no further reason
        # to go out as itself -- and the copy goes with it.
        self.assertEqual(loadout.ops[0].kept, "")
        self.assertIn("Content=", loadout.ops[0].source)
        # 1000 already has its own radius; 1001 takes it.
        self.assertEqual([change.guid for change in loadout], ["1001"])
        written = self.written(loadout)
        self.assertIn('Content="@1000/Building/Radius"', written)
        self.assertNotIn('GUID="1001"', written)

    def test_an_op_the_studio_could_not_read_is_not_kept_as_an_op(self):
        """A kept op says the Studio understood the op.  One it could not read is
        carried as an unread op instead -- see the class below."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="merge" Path="//Values/Building" '
                'Content="@9999/Building/Radius"/>'
            )
        )
        self.assertEqual(loadout.ops, [])
        self.assertEqual(len(loadout.changes), 0)

    def test_a_search_and_the_change_carrying_its_work_go_out_together(self):
        """The second search edits what the first one added, so its work lives
        in that change's payload rather than in a change of its own."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="add" Path="//Values/Building">'
                "<Extra><N>1</N></Extra></ModOp>"
                '<ModOp Type="merge" Path="//Values/Building/Extra">'
                "<N>2</N></ModOp>"
            )
        )
        adds, edits = keptops.of(loadout)
        self.assertEqual(edits.changes, ())
        self.assertTrue(adds.whole, adds.reason)
        self.assertTrue(edits.whole, edits.reason)
        written = self.written(loadout)
        self.assertIn("<Extra>", written)
        self.assertIn('"//Values/Building/Extra"', written)

    def test_and_still_do_with_one_of_their_answers_marked_off(self):
        """Both searches are facts; a hand-marked answer is a caveat on the list.
        Both used to collapse to narrow ModOps, each taking the other down."""
        loadout = self.loaded(
            self.mods(
                a__mod='<ModOp Type="add" Path="//Values/Building">'
                "<Extra><N>1</N></Extra></ModOp>"
                '<ModOp Type="merge" Path="//Values/Building/Extra">'
                "<N>2</N></ModOp>"
            )
        )
        next(iter(loadout)).active = False
        adds, edits = keptops.of(loadout)
        self.assertTrue(adds.whole, adds.reason)
        self.assertTrue(edits.whole, edits.reason)
        written = self.written(loadout)
        self.assertIn('"//Values/Building/Extra"', written)
        self.assertIn("<N>1</N>", written)
        self.assertEqual(written.count("<ModOp "), 2)


class TestASearchWhoseAnswerHasMoved(_KeptOps):
    """Noticing that a search would now answer differently, and putting it to what arrived."""

    # -- noticing that the answer has moved -------------------------------
    def test_nothing_has_moved_when_nothing_has_moved(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.assertEqual(keptops.drifted(loadout, Workspace(self.index, loadout)), [])

    def test_an_asset_another_mod_invents_is_what_moves_it(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.invents(loadout, "0 earlier")

        drifts = keptops.drifted(loadout, Workspace(self.index, loadout))
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].origin, "a mod")
        self.assertEqual(drifts[0].arrived, ("7777",))
        self.assertEqual(drifts[0].gone, ())
        self.assertIn("1 new", drifts[0].describe(str))
        self.assertEqual(
            keptops.summarise(drifts, str),
            "1 pattern in 1 mod now matches 1 asset it did not cover when you "
            "loaded it.",
        )

    def test_but_not_one_invented_by_a_mod_that_loads_afterwards(self):
        """The game would not cover it either: by the time that mod creates the
        asset, the search has already run."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.invents(loadout, "z later")
        self.assertEqual(keptops.drifted(loadout, Workspace(self.index, loadout)), [])

    def test_an_asset_that_has_left_the_session_moves_it_too(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.ops[0].matched.append("9999")
        drifts = keptops.drifted(loadout, Workspace(self.index, loadout))
        self.assertEqual(drifts[0].gone, ("9999",))

    # -- and closing the gap ----------------------------------------------
    def test_the_search_is_put_to_the_asset_that_arrived(self):
        """What storing the op buys over storing only the question: the mod
        goes on covering what it said it covered."""
        loadout = self.loaded(self.mods(a__mod=self.RADIUS))
        self.invents(loadout, "0 earlier")
        view = Workspace(self.index, loadout)

        made, covered = keptops.extend(loadout, self.index, view)
        self.assertEqual([change.guid for change in made], ["7777"])
        self.assertEqual((made[0].old_value, made[0].new_value), ("2", "99"))
        self.assertEqual(made[0].op_uid, loadout.ops[0].uid)
        self.assertEqual([guids for _op, guids in covered], [["7777"]])

    def test_and_then_nothing_has_moved_any_more(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.invents(loadout, "0 earlier")
        view = Workspace(self.index, loadout)
        keptops.extend(loadout, self.index, view)
        self.assertEqual(keptops.drifted(loadout, view), [])
        self.assertIn("7777", loadout.ops[0].matched)

    def test_the_search_is_still_whole_afterwards(self):
        """Extending has to move the count with it, or the op would read as
        having picked up changes from nowhere and stop writing as itself."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.invents(loadout, "0 earlier")
        keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        self.assertEqual(len(view.changes), 3)

    def test_a_configurable_mod_is_asked_again_at_the_settings_it_was_read_at(self):
        """Its ops read those values back, so re-running one with the answers
        thrown away would make it do something the rest of the mod does not."""
        seen = {}
        real = reader.read_ops

        def spy(ops, document, **asked):
            seen.update(asked["chosen"])
            return real(ops, document, **asked)

        loadout = self.loaded(self.mods(a__mod=self.RADIUS))
        loadout.layer("a mod").options = {"roomier": "true"}
        self.invents(loadout, "0 earlier")
        with mock.patch.object(reader, "read_ops", spy):
            keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        # Every loaded mod's, since an op can name another's in full.
        self.assertEqual(seen, {"a mod": {"roomier": "true"}, "0 earlier": {}})

    def test_a_search_with_no_op_behind_it_cannot_be_put_to_anything(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.ops[0].source = ""
        self.invents(loadout, "0 earlier")
        made, _covered = keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        self.assertEqual(made, [])

    def test_extending_reads_the_asset_as_this_mod_will_be_handed_it(self):
        """Not as the whole stack leaves it: a search belonging to a mod halfway
        down is answered by the document that mod sees."""
        loadout = self.loaded(self.mods(a__mod=self.RADIUS))
        self.invents(loadout, "0 earlier")
        # A mod loading after "a mod" already sets the field the search writes.
        # Read against the finished stack the search would think it started at 50.
        loadout.set_layer(ModLayer(origin="z last", name="z last"))
        loadout.set_change(
            Change(
                guid="7777", asset_label="invented", path="/Values/Building/Radius",
                tag="Radius", old_value="2", new_value="50", op=OP_REPLACE,
                origin="z last",
            )
        )
        made, _covered = keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        self.assertEqual([change.old_value for change in made], ["2"])

    def test_a_later_mod_extended_at_once_reads_what_the_earlier_one_just_made(self):
        """Extended inside a block that holds the change set fixed, the later
        mod read the asset without the earlier one's change and started
        from the game's value."""
        loadout = self.loaded(self.mods(
            a__mod=self.RADIUS, b__mod=self.RADIUS.replace("99", "50"),
        ))
        self.invents(loadout, "0 earlier")
        view = Workspace(self.index, loadout)
        made, _covered = keptops.extend(loadout, self.index, view)
        self.assertEqual(
            [(change.origin, change.old_value, change.new_value) for change in made],
            [("a mod", "2", "99"), ("b mod", "99", "50")],
        )
        self.assertEqual(view.asset_before("7777", "b mod").findtext(".//Radius"), "99")


class TestASearchPutToWhatArrivedIsReadAsItsModWas(_KeptOps):
    """The search is read again to be put to a new asset, and that reading has
    to be the one the mod had: in the loadout's game, beside the mods that
    are loaded, and with its other searches asked of the file.

    Each of these went wrong while the reading was set up twice, here and for
    an op typed in the XML view, and only the second was kept up.
    """

    # The search is the op's own; the condition's is another, of the file.
    WHERE_GAMMA_IS = (
        '<ModOp Type="merge" Path="//Values/Building" '
        """Condition="//Values[Standard/GUID='1002']"><Radius>99</Radius></ModOp>"""
    )
    UNLESS_GAMMA_IS = WHERE_GAMMA_IS.replace('Condition="', 'Condition="!')
    IF_THE_OTHER_IS_LOADED = (
        '<ModOp Type="merge" Path="//Values/Building" Condition="#the other">'
        "<Radius>99</Radius></ModOp>"
    )
    BY_PROPERTY = '<ModOp Property="Building" Merge="self::node()"><Radius>99</Radius></ModOp>'
    # Every building gets beta's radius: where it copies from is a search too.
    COPIED_FROM_BETA = (
        '<ModOp Type="replace" Path="//Values/Building/Radius" '
        """Content="//Values[Standard/GUID='1001']/Building/Radius"/>"""
    )

    def extended(self, loadout):
        """What "a mod" comes to for the asset that arrives."""
        self.invents(loadout, "0 earlier")
        made, _covered = keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        return [(change.guid, change.new_value) for change in made if change.origin == "a mod"]

    def test_a_condition_that_holds_in_the_file_holds_for_what_arrives(self):
        """Answered with the one asset that arrived, the condition's search
        found no asset of the game's, and the op stopped itself."""
        loadout = self.loaded(self.mods(a__mod=self.WHERE_GAMMA_IS))
        self.assertEqual(len(loadout.changes), 2)
        self.assertEqual(self.extended(loadout), [("7777", "99")])

    def test_and_one_that_does_not_hold_there_does_not_start_to(self):
        """Answered the same way, a search that finds nothing in the file
        found the asset that arrived, and the op ran where it would not."""
        op = Op(expression="//Values/Building", source=self.UNLESS_GAMMA_IS, origin="a mod")
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="a mod", name="a mod"))
        made, reason = keptops.expand(loadout, self.index, op, ["1000", "1001"])
        self.assertEqual(made, [], reason)

    def test_what_it_copies_is_looked_for_in_the_file_as_well(self):
        """Answered with the asset that arrived, the copy was looked for in
        that asset alone, which is not where it is."""
        loadout = self.loaded(self.mods(a__mod=self.COPIED_FROM_BETA))
        self.assertEqual(self.extended(loadout), [("7777", "4")])

    def test_a_search_that_asks_after_another_mod_is_answered_by_the_mods_loaded(self):
        loadout = self.loaded(
            self.mods(a__mod=self.IF_THE_OTHER_IS_LOADED, the__other=self.SEARCH)
        )
        self.assertEqual(sum(change.origin == "a mod" for change in loadout), 2)
        self.assertEqual(self.extended(loadout), [("7777", "99")])

    def test_it_is_read_in_the_loadouts_game(self):
        """``Property=`` is Anno 117's, and read as either game's it is no
        attribute the op is known to have: the op came to nothing."""
        loadout = self.loaded(self.mods(a__mod=self.BY_PROPERTY))
        loadout.game = games.ANNO_117
        self.assertEqual(sum(change.origin == "a mod" for change in loadout), 2)
        self.assertEqual(self.extended(loadout), [("7777", "99")])

    def test_and_with_the_inert_removals_its_mod_was_read_with(self):
        seen = {}
        real = reader.read_ops

        def spy(ops, document, **asked):
            seen.update(asked)
            return real(ops, document, **asked)

        loadout = self.loaded(self.mods(a__mod=self.RADIUS))
        loadout.layer("a mod").read_as = modops.Reading(keep_inert=True).recorded()
        self.invents(loadout, "0 earlier")
        with mock.patch.object(reader, "read_ops", spy):
            keptops.extend(loadout, self.index, Workspace(self.index, loadout))
        self.assertTrue(seen["keep_inert"])

    def test_an_op_typed_in_the_xml_view_is_read_with_the_same(self):
        loadout = self.loaded(self.mods(a__mod=self.RADIUS))
        loadout.layer("a mod").options = {"roomier": "true"}
        with mock.patch.object(reader, "read_ops", wraps=reader.read_ops) as read:
            typedops.read_one(Workspace(self.index, loadout), "a mod", self.RADIUS)
        asked = read.call_args.kwargs
        self.assertEqual(asked["chosen"], {"a mod": {"roomier": "true"}})
        self.assertIn("a mod", asked["present"])
        self.assertEqual(
            {name: asked[name] for name in ("keep_inert", "chosen", "present", "mod_ids")},
            keptops.reading_of(loadout, "a mod"),
        )


class TestAPatchReadOnItsOwnIsItsGames(SessionCase):
    """Which game a patch is for is said by the folders it sits in, and a
    file read by itself was asked by its name alone: it was read as neither
    game's, and what only one of them writes was not read at all."""

    # ``Property=`` is Anno 117's way of naming every asset with a property.
    BY_PROPERTY = '<ModOp Property="Building" Merge="self::node()"><Radius>99</Radius></ModOp>'

    def test_it_is_read_as_its_folder_is(self):
        mod = self.mods(a__mod=self.BY_PROPERTY) / "a mod"
        patch = mod / "data/base/config/export/assets.xml"
        through_folder = reader.analyse(mod, self.index)
        alone = reader.analyse(patch, self.index)
        self.assertEqual(
            sorted(change.guid for change in through_folder.changes), ["1000", "1001"]
        )
        self.assertEqual(
            sorted((change.guid, change.new_value) for change in alone.changes),
            sorted((change.guid, change.new_value) for change in through_folder.changes),
        )


class TestAPatternOfYourOwn(_KeptOps):
    """Writing a pattern yourself, and telling one op from another."""

    BUILT = '<ModOp Type="merge" Path="//Values/Building"><Elevation>7</Elevation></ModOp>'

    # -- writing one yourself ---------------------------------------------
    def test_a_pattern_can_be_written_rather_than_read(self):
        op = Op(expression="//Values/Building", source=self.BUILT,
                    summary=modops.describe_op(modops.read_op(self.BUILT)))
        made, _reason = keptops.expand(Loadout(), self.index, op, ["1000", "1001"])
        self.assertEqual(sorted(change.guid for change in made), ["1000", "1001"])
        self.assertEqual({change.payload for change in made}, {"<Elevation>7</Elevation>"})
        self.assertEqual({change.op_uid for change in made}, {op.uid})

    def test_typed_on_its_own_its_search_is_asked_of_the_whole_file(self):
        found = typedops.read_one(Workspace(self.index, Loadout()), "", self.BUILT)
        self.assertEqual([op.matched for op in found.reaching], [["1000", "1001"]])
        self.assertEqual(sorted(change.guid for change in found.answers), ["1000", "1001"])

    def test_and_of_the_assets_the_session_invents(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.invents(loadout, "0 earlier")
        found = typedops.read_one(Workspace(self.index, loadout), "", self.BUILT)
        self.assertIn("7777", found.reaching[0].matched)
        self.assertIn("7777", {change.guid for change in found.answers})

    def test_a_pattern_that_matches_nothing_makes_nothing(self):
        op = Op(expression="//Values/Nothing", source=(
            '<ModOp Type="merge" Path="//Values/Nothing"><X>1</X></ModOp>'))
        made, reason = keptops.expand(Loadout(), self.index, op, [])
        self.assertEqual(made, [])
        self.assertIn("matches nothing", reason)

    # -- one op and another -------------------------------------------------
    def test_two_spellings_of_the_same_op_are_the_same_op(self):
        self.assertTrue(
            keptops.same_op(
                '<ModOp Merge="//Values/Building"><Elevation>7</Elevation></ModOp>',
                '<ModOp Type="merge" Path="//Values/Building">'
                "<Elevation>7</Elevation></ModOp>",
            )
        )

    def test_and_so_are_two_ways_of_naming_its_asset(self):
        for one, other in (
            ('<ModOp Type="merge" Path="@123/Values/X"><A>1</A></ModOp>',
             '<ModOp Type="merge" GUID="123" Path="/Values/X"><A>1</A></ModOp>'),
            ('<ModOp Merge="@123/Values/X"><A>1</A></ModOp>',
             '<ModOp GUID="123" Merge="/Values/X"><A>1</A></ModOp>'),
        ):
            with self.subTest(one=one):
                self.assertTrue(keptops.same_op(one, other))
                self.assertEqual(keptops.canonical_op(one), keptops.canonical_op(other))

    def test_but_ops_on_two_assets_are_two_ops(self):
        """The reader takes the ``@GUID`` out of the path, and the asset it
        names was not compared: an op retargeted only there was the op it
        had been."""
        for one, other in (
            ('<ModOp Type="merge" Path="@123/Values/X"><A>1</A></ModOp>',
             '<ModOp Type="merge" Path="@456/Values/X"><A>1</A></ModOp>'),
            ('<ModOp Merge="@123"><A>1</A></ModOp>',
             '<ModOp Merge="@456"><A>1</A></ModOp>'),
            ('<ModOp Type="merge" Path="@123/Values/X"><A>1</A></ModOp>',
             '<ModOp Type="merge" GUID="456" Path="/Values/X"><A>1</A></ModOp>'),
        ):
            with self.subTest(one=one):
                self.assertFalse(keptops.same_op(one, other))
                self.assertNotEqual(keptops.canonical_op(one), keptops.canonical_op(other))

    def test_but_an_op_missing_an_attribute_is_not(self):
        for lost in (
            '<ModOp Type="merge" Path="//X"><A>1</A></ModOp>',
            '<ModOp Type="merge" Path="//X" ModOpID="tweakable"><A>1</A></ModOp>',
            '<ModOp Type="merge" Path="//X"><A><ModValue/></A></ModOp>',
            '<ModOp Type="merge" Path="//X"><A>1</A><B>2</B></ModOp>',
        ):
            with self.subTest(lost=lost):
                self.assertFalse(
                    keptops.same_op(
                        '<ModOp Type="merge" Path="//X" Condition="@1/V">'
                        '<A><ModValue Insert="$size"/></A></ModOp>',
                        lost,
                    )
                )

    # Text after something inside an element the op holds, at two depths.
    # The reading makes a value of the first two, and all of it goes out.
    TAILED = (
        '<ModOp Type="merge" Path="//X"><A><ModOpContent/>5</A></ModOp>',
        '<ModOp Type="merge" Path="//X"><A>1<ModValue Insert="2+3"/>7</A></ModOp>',
        '<ModOp Type="merge" Path="//X"><A><B/>x</A></ModOp>',
        '<ModOp Type="merge" Path="//X"><A><B><C>1</C><!-- c -->x</B></A></ModOp>',
    )

    def test_nor_is_one_without_the_text_after_something_inside_an_element(self):
        """``source`` keeps that text and ``same_op`` did not look at it
        below the op's own elements, so an op edited only there was the op
        it had been: the XML view left the edit out on Apply."""
        import re

        for op in self.TAILED:
            with self.subTest(op=op):
                tail = re.search(r"/>(5|7|x)</", op) or re.search(r"-->(x)</", op)
                without = op[: tail.start(1)] + op[tail.end(1):]
                other = op[: tail.start(1)] + "y" + op[tail.end(1):]
                for lost in (without, other):
                    self.assertFalse(keptops.same_op(op, lost), lost)
                    self.assertFalse(keptops.same_op(lost, op), lost)
                    self.assertNotEqual(keptops.canonical_op(op), keptops.canonical_op(lost))

    def test_while_layout_after_something_inside_an_element_is_not_looked_at(self):
        for op in self.TAILED:
            with self.subTest(op=op):
                spread = (
                    op.replace("<A>", "<A>\n    ", 1)
                    .replace("</A>", "\n  </A>\n")
                    .replace("</B>", "  </B>\n   ")
                    .replace("</C>", "</C>\n\t")
                    .replace("<B/>", "<B/>  ")
                )
                self.assertNotEqual(spread, op)
                self.assertTrue(keptops.same_op(op, spread), spread)
                self.assertEqual(keptops.canonical_op(op), keptops.canonical_op(spread))

    def test_a_computed_value_is_said_rather_than_left_blank(self):
        """"merge Limit" tells the reader nothing; the expression is the op."""
        op = modops.read_op(
            '<ModOp Merge="//Limit"><Limit><ModValue Insert="$traderstorage"/>'
            "</Limit></ModOp>"
        )
        self.assertEqual(modops.describe_op(op), "merge Limit = $traderstorage")


class TestPropertyTargetingEveryAssetThatHasOne(ImportCase):
    """Anno 117's ``Property="X"``: every asset with that property, not a list
    of GUIDs.  Written as the search it is, so the op goes back out as itself
    and goes on covering assets other mods add.  Measured in
    ``test_modio_loader``; 7 ops in 5 census units, none in Anno 1800."""

    def op(self, attributes: str, payload: str = "<Building><Radius>99</Radius></Building>"):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file("<ModOp %s>%s</ModOp>" % (attributes, payload)).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_117)
        self.assertEqual(len(ops), 1)
        return ops[0]

    def test_it_becomes_a_search_for_that_property(self):
        self.assertEqual(
            self.op('Property="Building" Merge="self::node()[BuildingType=\'Factory\']"').path,
            "//Values/Building[BuildingType='Factory']",
        )

    def test_with_nothing_to_filter_by_it_is_every_one(self):
        self.assertEqual(
            self.op('Property="Building" Merge="self::node()"').path, "//Values/Building"
        )

    def test_a_path_below_the_property_hangs_off_it(self):
        for written in ("self::node()/Radius", "Radius", "/Radius"):
            with self.subTest(merge=written):
                self.assertEqual(
                    self.op('Property="Building" Merge="%s"' % written).path,
                    "//Values/Building/Radius",
                )

    def test_an_absolute_path_wins_and_the_property_is_only_a_hint(self):
        self.assertEqual(
            self.op('Property="Value" Merge="//Values/CityStatus/*/*/Value"').path,
            "//Values/CityStatus/*/*/Value",
        )

    def test_with_nothing_to_aim_it_targets_nothing(self):
        self.assertEqual(self.op('Property="Building"').path, "")

    def test_a_guid_beside_it_wins_as_the_loader_says_it_does(self):
        """"You can use only one of GUID, Property or Template", says the
        loader, and then goes with the GUID."""
        op = self.op('Property="Building" GUID="1001" Merge="self::node()"')
        self.assertEqual((op.guid, op.path), ("1001", "self::node()"))

    def test_anno_1800_does_nothing_with_one_so_it_is_left_alone(self):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(
            '<ModOp Type="merge" Property="Building" Path="self::node()">'
            "<Building><Radius>99</Radius></Building></ModOp>"
        ).encode("utf-8"))
        (op,) = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_1800)
        self.assertEqual(op.path, "self::node()")

    def test_the_whole_op_read_against_real_assets(self):
        """*Farm Animal Module Limit* is this, and its two ops reach 23 assets
        in game -- which is what the Studio now shows."""
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(
            '<ModOp Property="Building" Merge="self::node()[BuildingType=\'Factory\']">'
            "<Building><Radius>99</Radius></Building></ModOp>"
        ).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_117)
        translator = reader.Translator(self.index, True)
        translator.search = reader.searcher(self.index, ops)
        (result,) = translator.run_all(ops)
        self.assertEqual(result.status, modops.IMPORTED)
        self.assertEqual([(c.guid, c.new_value) for c in result.changes], [("1000", "99")])
        self.assertNotIn("ignored", result.reason)


class TestModItemAnEntryFoundByWhatItHolds(ImportCase):
    """Anno 117's answer to a merge being positional.

    ``<ModItem Merge="Building">`` is "the entry whose Building is the one I
    carry", so a mod can patch the entry it means rather than the one that
    happens to sit in that place.  Every rule here was measured with xmltest;
    see ``test_modio_loader``.  161 of them in the Anno 117 census, in 9 mods.
    """

    LIST = 'Merge="@1000/ProductList/List"'
    COSTS = 'Merge="@1000/Cost/Costs"'

    def read_as(self, game: str, body: str) -> modops.ImportReport:
        """Read as the named game: the two loaders differ about <ModItem>."""
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        report.results = reader.Translator(self.index, True).run_all(ops)
        return report

    def read117(self, body: str) -> modops.ImportReport:
        return self.read_as(games.ANNO_117, body)

    def only117(self, body: str) -> modops.OpResult:
        report = self.read117(body)
        self.assertEqual(len(report.results), 1, report.results)
        return report.results[0]

    def test_it_finds_the_entry_by_the_field_it_names(self):
        result = self.only117(
            '<ModOp %s><ModItem Merge="Ingredient"><Ingredient>1011</Ingredient>'
            "<Amount>99</Amount></ModItem></ModOp>" % self.COSTS
        )
        self.assertEqual(
            [(c.op, c.path, c.new_value) for c in result.changes],
            [(OP_REPLACE, "/Values/Cost/Costs/Item[Ingredient='1011']/Amount", "99")],
        )

    def test_an_entry_that_is_not_there_is_added(self):
        result = self.only117(
            '<ModOp %s><ModItem Merge="Product"><Product>2099</Product></ModItem></ModOp>'
            % self.LIST
        )
        (change,) = result.changes
        self.assertEqual((change.op, change.path), (OP_ADD, "/Values/ProductList/List"))
        self.assertIn("<Product>2099</Product>", change.payload)

    def test_and_where_append_says_it_goes(self):
        for attribute, anchor, expected in (
            ("Append", "2001", "/Values/ProductList/List/Item[Product='2001']"),
            ("Prepend", "2003", "/Values/ProductList/List/Item[Product='2002']"),
        ):
            with self.subTest(attribute=attribute):
                result = self.only117(
                    '<ModOp %s><ModItem Merge="Product" %s="Product=\'%s\'">'
                    "<Product>2099</Product></ModItem></ModOp>"
                    % (self.LIST, attribute, anchor)
                )
                (change,) = result.changes
                self.assertEqual((change.op, change.path), (OP_ADD_NEXT, expected))

    def test_a_field_it_already_reads_that_way_is_nothing_to_do(self):
        result = self.only117(
            '<ModOp %s><ModItem Merge="Product"><Product>2002</Product></ModItem></ModOp>'
            % self.LIST
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertEqual(result.changes, [])

    def test_two_of_them_in_one_op_are_two_changes(self):
        result = self.only117(
            '<ModOp %s><ModItem Merge="Product"><Product>2098</Product></ModItem>'
            '<ModItem Merge="Product"><Product>2099</Product></ModItem></ModOp>' % self.LIST
        )
        self.assertEqual([c.op for c in result.changes], [OP_ADD, OP_ADD])

    def test_anno_1800_does_not_know_what_one_is_so_the_op_does_nothing(self):
        """Measured: its loader leaves the list alone entirely.  Carried, so
        the export goes on doing nothing there too."""
        report = self.read_as(
            games.ANNO_1800,
            '<ModOp Type="merge" GUID="1000" Path="/Values/ProductList/List">'
            '<ModItem Merge="Product"><Product>2099</Product></ModItem></ModOp>',
        )
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertIn("Anno 1800", report.results[0].reason)
        self.assertEqual(len(report.unread("m")), 1)

    def test_one_that_leaves_two_entries_alike_is_read_at_the_entry_it_found(self):
        """*Sandarac Nursery as Plantation* merges 8579 into an entry that is
        already 2878, leaving two entries alike, so no key tells the list's
        entries apart afterwards. It was read as the whole list replaced. But
        the merge found one entry, by a key nothing else holds, and changed
        one field of it."""
        result = self.only117(
            '<ModOp %s><ModItem Merge="Product=\'2003\'"><Product>2001</Product></ModItem></ModOp>'
            % self.LIST
        )
        self.assertEqual(
            [(c.op, c.path, c.new_value) for c in result.changes],
            [(OP_REPLACE, "/Values/ProductList/List/Item[Product='2003']/Product", "2001")],
        )

    def test_entries_alike_elsewhere_in_the_list_leave_the_one_it_names_narrow(self):
        """Decision D7, from the second hands-on run: the Fishing Hut's costs
        held Ingredient 2176 twice once an earlier op had added another, and a
        merge into 2174's Amount read as "Costs removed" and "+ Costs" -- a
        conflict with any mod touching any cost, and the whole list written."""
        report = self.read117(
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1010</Ingredient><Amount>1</Amount></Item></ModOp>"
            '<ModOp %s><ModItem Merge="Ingredient"><Ingredient>1011</Ingredient>'
            "<Amount>99</Amount></ModItem></ModOp>" % self.COSTS
        )
        self.assertEqual(
            [(c.op, c.path, c.new_value) for c in report.results[1].changes],
            [(OP_REPLACE, "/Values/Cost/Costs/Item[Ingredient='1011']/Amount", "99")],
        )

    def test_and_an_entry_it_adds_beside_them_is_an_addition(self):
        report = self.read117(
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1010</Ingredient><Amount>1</Amount></Item></ModOp>"
            '<ModOp %s><ModItem Merge="Ingredient"><Ingredient>1012</Ingredient>'
            "<Amount>3</Amount></ModItem></ModOp>" % self.COSTS
        )
        (change,) = report.results[1].changes
        self.assertEqual((change.op, change.path), (OP_ADD, "/Values/Cost/Costs"))
        self.assertIn("<Ingredient>1012</Ingredient>", change.payload)

    def test_one_whose_key_is_met_twice_is_still_shown_at_what_it_comes_to(self):
        """The loader merges into the first of the two, and no path written
        for that entry says "the first": the op is kept as written and shown
        coarsely. Shown wrongly, though, until the coarse view learned
        <ModItem> too: it held a literal <ModItem> element, which is what the
        game makes of one only under an op that is not a merge."""
        report = self.read117(
            '<ModOp Type="add" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1010</Ingredient><Amount>1</Amount></Item></ModOp>"
            '<ModOp %s><ModItem Merge="Ingredient"><Ingredient>1010</Ingredient>'
            "<Amount>99</Amount></ModItem></ModOp>" % self.COSTS
        )
        changes = report.results[1].changes
        self.assertFalse([c for c in changes if c.path.endswith("/Amount")], changes)
        payload = "".join(c.payload for c in changes)
        self.assertNotIn("ModItem", payload)
        self.assertEqual(
            [node.text for node in etree.fromstring(payload.encode("utf-8")).iter("Amount")],
            ["99", "8", "1"],
        )

    def test_the_shapes_that_leave_the_loader_in_a_muddle_are_carried(self):
        """Two of them, both measured.  An entry that is found *and* says where
        to go leaves the ``Append=`` attribute behind in the game's own data;
        one whose anchor is missing leaves a literal ``<ModItem>`` there.

        Neither is a thing the Studio can show, coarsely or narrowly -- so the
        op is carried and shown as nothing, rather than shown as something it
        does not do.  It still goes back out exactly as written."""
        for name, attribute in (
            ("found as well", "Append=\"Product='2001'\"><Product>2003</Product>"),
            ("anchor missing", "Append=\"Product='9999'\"><Product>2099</Product>"),
        ):
            with self.subTest(shape=name):
                report = self.read117(
                    '<ModOp %s><ModItem Merge="Product" %s</ModItem></ModOp>'
                    % (self.LIST, attribute)
                )
                (result,) = report.results
                self.assertEqual(result.status, modops.BLOCKED)
                self.assertEqual(result.changes, [])
                self.assertIn("<ModItem", report.unread("m")[0].source)


class TestFlagsMergedAndRemoved(ImportCase):
    """``<ModValue Merge="A;B"/>`` and ``Remove=`` (Anno 117): flags added to a
    ``;`` list if they are not in it, or taken out -- as the loader's
    ``MergeFlags`` does, into the game's list for a merge and into the mod's
    own copy of the field for anything else."""

    def changed(self, body: str):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_117)
        (result,) = reader.Translator(self.index, True).run_all(ops)
        return result, [(c.path, c.new_value) for c in result.changes]

    def test_merged_into_what_the_game_has(self):
        result, changes = self.changed(
            '<ModOp Merge="@1000/Building"><BuildingType>'
            '<ModValue Merge="Moderate;Factory"/></BuildingType></ModOp>'
        )
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertEqual(changes, [("/Values/Building/BuildingType", "Factory;Moderate")])

    def test_several_in_one_field_apply_in_order(self):
        _result, changes = self.changed(
            '<ModOp Merge="@1000/Building"><BuildingType>'
            '<ModValue Remove="Factory"/><ModValue Merge="Custom"/></BuildingType></ModOp>'
        )
        self.assertEqual(changes, [("/Values/Building/BuildingType", "Custom")])

    def test_under_a_replace_or_an_add_they_are_not_read(self):
        """The docs merge flags under ``Merge``.  A replace was read as
        working on its own copy of the list, ``Rain;SnowRain`` coming to
        ``Custom``, and both Anno 117 builds leave every mark in that field
        as it was written
        (``test_and_flags_under_anything_else_are_left_as_written_too`` in
        test_modio_loader.py)."""
        for body in (
            '<ModOp Replace="@1000/Building/BuildingType"><BuildingType>Rain;SnowRain'
            '<ModValue Remove="Rain;Snow"/><ModValue Merge="Custom"/>'
            '<ModValue Remove="SnowRain"/></BuildingType></ModOp>',
            '<ModOp Replace="@1000/Building/BuildingType"><BuildingType>'
            '<ModValue Merge="Moderate"/></BuildingType></ModOp>',
            '<ModOp Add="@1000/Building"><Kind><ModValue Merge="Moderate"/></Kind></ModOp>',
        ):
            with self.subTest(body=body):
                result, changes = self.changed(body)
                self.assertEqual(result.status, modops.BLOCKED)
                self.assertIn("in an op that is not a merge", result.reason)
                self.assertEqual(changes, [])

    def test_flags_beside_a_computed_value_are_not_read(self):
        result, changes = self.changed(
            '<ModOp Merge="@1000/Building"><BuildingType>'
            '<ModValue Merge="A"/><ModValue Insert="self::node()"/></BuildingType></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("flags and a computed value", result.reason)


class TestAllowNoMatch(ImportCase):
    """Only quietens the loader's "no matching node" warning -- so it is known
    here, and an op carrying it that matches nothing is what its author
    expected rather than something to look at."""

    def test_on_an_op_that_matches_it_is_known_not_ignored(self):
        result = self.only(
            '<ModOp Type="replace" GUID="1001" Path="/Values/Standard/Name" AllowNoMatch="1">'
            "<Name>x</Name></ModOp>"
        )
        self.assertEqual(result.status, modops.IMPORTED)
        self.assertNotIn("ignored", result.reason)

    def test_matching_nothing_is_allowed_and_it_goes_back_out(self):
        result = self.only(
            '<ModOp Type="replace" GUID="1001" Path="/Values/Nothing/Here" AllowNoMatch="0">'
            "<Here>x</Here></ModOp>"
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertIn("which its author allows (AllowNoMatch)", result.reason)
        self.assertIsNotNone(result.record)
        self.assertIn('AllowNoMatch="0"', result.record.source)

    def test_without_it_matching_nothing_is_still_a_question(self):
        result = self.only(
            '<ModOp Type="replace" GUID="1001" Path="/Values/Nothing/Here"><Here>x</Here></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)

    def ops_of(self, body: str):
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        translator = reader.Translator(self.index, True)
        results = translator.run_all(modops.parse_ops(root, [], "assets.xml"))
        return results, [op for ops in translator.found.values() for op in ops]

    def test_an_op_naming_several_assets_goes_out_once(self):
        """Once, however many of its assets it misses. It went out once per
        asset that missed, and once more for the op: *S.L.O.P. - Attributes*
        halves Belief in twenty-five assets, some without it, and the game
        halved the rest three times over."""
        for payload in ("<Radius>3</Radius>",
                        '<Radius><ModValue Insert="self::node() * 0.5"/></Radius>'):
            with self.subTest(payload=payload):
                results, ops = self.ops_of(
                    '<ModOp GUID="1000,1001,1002" Merge="Building/Radius" AllowNoMatch="1">'
                    "%s</ModOp>" % payload
                )
                self.assertEqual(len(results), 1)
                self.assertEqual(len(ops), 1, [op.kept for op in ops])
                self.assertIs(results[0].record, ops[0])
                # What it does to the two that have one is still shown.
                self.assertEqual(
                    sorted(change.guid for change in results[0].changes), ["1000", "1001"]
                )

    def test_missing_every_asset_it_names_goes_out_once(self):
        results, ops = self.ops_of(
            '<ModOp GUID="1000,1001" Merge="Nothing/Here" AllowNoMatch="1"><Here>x</Here></ModOp>'
        )
        self.assertEqual(results[0].status, modops.SETTLED)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].matched, ["1000", "1001"])

    def test_one_that_misses_elsewhere_keeps_what_holds_its_work_here(self):
        """Its work went into what the op before had just added to one asset,
        and the other asset it names has no such node. The op kept for the
        miss lost the key of the change its work is in, and its row read
        "0 changes, kept as the op itself"."""
        results, _ops = self.ops_of(
            '<ModOp Type="add" GUID="1000" Path="/Values"><Extra><A>1</A></Extra></ModOp>'
            '<ModOp Type="add" GUID="1000,1002" Path="/Values/Extra" AllowNoMatch="1">'
            "<B>2</B></ModOp>"
        )
        host, into = results
        self.assertTrue(into.allowed_miss)
        self.assertEqual(list(into.folded), [host.changes[0].key])
        self.assertEqual(into.record.folded, [list(host.changes[0].key)])


class TestAGroupThatCarriesMoreThanACondition(ImportCase):
    """``ModOpID``, ``Name`` and ``Skip``: read, where a target is not.

    Measured against the loader in ``test_modio_loader``: a name is nothing to
    it, and any ``Skip`` at all switches the group off.
    """

    INSIDE = (
        '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>77</Radius></ModOp>'
        '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>9</Radius></ModOp>'
    )

    def test_a_name_is_for_a_tweak_tool_so_what_is_inside_is_read(self):
        """473 groups in the Anno 1800 census carry one, holding 1,473 ModOps
        that used to be unread for it."""
        for attribute in ('ModOpID="Farms"', 'Name="Farms"'):
            with self.subTest(attribute=attribute):
                report = self.read("<Group %s>%s</Group>" % (attribute, self.INSIDE))
                self.assertEqual(
                    sorted((c.guid, c.new_value) for c in report.changes),
                    [("1000", "77"), ("1001", "9")],
                )
                self.assertEqual(report.unread("m"), [])

    def test_any_skip_switches_it_off_and_keeps_it(self):
        for value in ("1", "0", "false", ""):
            with self.subTest(skip=value):
                report = self.read('<Group Skip="%s">%s</Group>' % (value, self.INSIDE))
                self.assertEqual(report.changes, [])
                self.assertEqual(report.unread("m"), [])
                self.assertIn("switched off", report.results[0].record.kept)

    def test_a_name_and_a_condition_together(self):
        for condition, runs in (("@1000/Building", True), ("@1000/Nothing", False)):
            with self.subTest(condition=condition):
                report = self.read(
                    '<Group ModOpID="F" Condition="%s">%s</Group>' % (condition, self.INSIDE)
                )
                self.assertEqual(len(report.changes), 2 if runs else 0)
                self.assertIn("ModOpID=F", report.results[0].record.kept)

    def test_max_repeat_with_no_game_known_is_carried_whole(self):
        """Anno 117 loops it and Anno 1800 runs it once; with no game known,
        which is not known either. The loops themselves are read in
        ``TestAGroupThatRepeats``."""
        report = self.read('<Group MaxRepeat="3">%s</Group>' % self.INSIDE)
        self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
        self.assertEqual(report.changes, [])
        self.assertEqual(len(report.unread("m")), 1)


class TestAGroupThatRepeats(ImportCase):
    """``MaxRepeat="N"`` (Anno 117): the group runs again while its condition
    holds, at most N times -- measured with the loader's xmltest3, which
    removed the first of five items five times under ``MaxRepeat="10"`` and
    added three times while fewer than three. Anno 1800 runs it once."""

    REMOVING = ('<Group Condition="@1000/Cost/Costs/Item" MaxRepeat="%s">'
                '<ModOp Remove="@1000/Cost/Costs/Item[1]"/></Group>')
    # The asset's product list holds three; this adds until it holds five.
    ADDING = ('<Group Condition="@1000/ProductList/List[count(Item) &lt; 5]" MaxRepeat="10">'
              '<ModOp Add="@1000/ProductList/List"><Item><Product>3000</Product></Item></ModOp></Group>')

    def read(self, body, game=games.ANNO_117):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        report.results = reader.Translator(self.index, True).run_all(ops)
        return report

    def test_it_stops_when_the_condition_does(self):
        report = self.read(self.REMOVING % "10")
        # Both items this asset's costs hold, one a round, and then it stops.
        paths = [c.path for c in report.changes]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(path.startswith("/Values/Cost/Costs/Item[") for path in paths))
        self.assertEqual(report.unread(""), [])
        self.assertEqual(len(self.read(self.ADDING).changes), 2)

    def test_it_goes_back_out_once_as_written(self):
        """Every round is read, and hangs off the group -- which is written
        back as its author wrote it, not as a round-by-round list of ops."""
        report = self.read(self.REMOVING % "10")
        op = report.results[0].record
        self.assertIn("at most 10 times", op.kept)
        self.assertEqual(op.source.count("<Group"), 1)
        self.assertIn('MaxRepeat="10"', op.source)
        self.assertTrue(report.changes)
        self.assertTrue(all(change.op_uid == op.uid for change in report.changes))
        self.assertEqual(op.parts, [])
        self.assertEqual(modops.describe_op(report.results[0].op),
                         "a group of ops, again while @1000/Cost/Costs/Item, at most 10 times")

    def test_and_at_the_cap_whether_or_not_it_still_holds(self):
        self.assertEqual(len(self.read(self.REMOVING % "1").changes), 1)

    # What is true of one op inside the loop is not written on the loop's
    # kept op, which is what the op's record is there.
    WORKING = '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'

    def loop_of(self, *inside, repeat="2"):
        report = self.read('<Group MaxRepeat="%s">%s</Group>' % (repeat, "".join(inside)))
        return report, report.results[0].record

    def test_an_op_inside_whose_condition_fails_does_not_make_the_loop_idle(self):
        """A search whose own condition did not hold marked the whole loop
        idle, with the change of the op beside it still under it."""
        search = ('<ModOp Type="replace" Path="//Asset[Values/Standard/GUID=\'1001\']/Values/Building/Radius"'
                  ' Condition="/Values/Nothing"><Radius>9</Radius></ModOp>')
        translator = reader.Translator(self.index, True)
        translator.search = lambda path: ["1001"]
        root = etree.fromstring(modops_file(
            '<Group MaxRepeat="2">%s%s</Group>' % (self.WORKING, search)).encode("utf-8"))
        results = translator.run_all(modops.parse_ops(root, [], "assets.xml", game=games.ANNO_117))
        loop = results[0].record
        self.assertTrue(any(change.op_uid == loop.uid for result in results for change in result.changes))
        self.assertFalse(loop.idle)

    def test_an_op_inside_written_switched_off_does_not_switch_the_loop_off(self):
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        off = ("<!-- %s:\n<ModOp Merge=\"@1001/Building\"><Radius>9</Radius></ModOp>\n-->"
               % SWITCHED_OFF_MARK)
        report, loop = self.loop_of(self.WORKING, off)
        self.assertTrue(loop.active)
        live = [change for change in report.changes if change.op_uid == loop.uid]
        self.assertTrue(live)
        self.assertTrue(all(change.active for change in live))
        # And nothing switched off hangs under an op that is on.
        self.assertEqual([change for result in report.results for change in result.shelved], [])

    def test_an_op_inside_partly_unread_is_said_on_the_loop(self):
        """Standing alone it carries the caveat; inside a loop it was said
        nowhere, and the loop's row showed part of what it does as all of it."""
        partly = '<ModOp Type="merge" GUID="1000,9999" Path="/Values/Building"><Radius>25</Radius></ModOp>'
        alone = self.read(partly).results[0]
        self.assertEqual(alone.partial, 1)
        self.assertIn("could not read what it does to 1 of the assets", alone.record.caveat)
        _report, loop = self.loop_of(partly, repeat="1")
        self.assertIn("could not read what it does to 1 of the assets", loop.caveat)

    def test_anno_1800_runs_it_once(self):
        # The op in the classic form: Anno 1800's loader has no short form.
        removing = ('<Group Condition="@1000/Cost/Costs/Item" MaxRepeat="10">'
                    '<ModOp Type="remove" GUID="1000" Path="/Values/Cost/Costs/Item[1]"/></Group>')
        self.assertEqual(len(self.read(removing, games.ANNO_1800).changes), 1)

    def test_a_round_going_out_as_written_takes_only_the_rounds_left(self):
        """Unanswered from the first round, the group goes back out whole, and
        as many times over as it was written for -- the rounds nested inside
        it are not written a second time."""
        report = self.read('<Group Condition="#some-mod" MaxRepeat="4">'
                           '<ModOp Remove="@1000/Cost/Costs/Item[1]"/></Group>')
        (unread,) = report.unread("")
        self.assertIn('MaxRepeat="4"', unread.source)
        self.assertEqual(unread.source.count("<ModOp"), 1)

    def test_the_number_is_read_as_the_loader_reads_it(self):
        """Held to 1 to 1,000, from the digits it starts with, and 1 where
        there are none: Anno 117's loader, L570-571 and ``fromchars``."""
        for written, removed in (("1001", 2), ("0", 1), ("-3", 1), ("2x", 2), (" 5", 1),
                                 ("five", 1), ("99999999999", 1)):
            with self.subTest(written=written):
                report = self.read(self.REMOVING % written)
                self.assertEqual(len(report.changes), removed)
                self.assertIn('MaxRepeat="%s"' % written, report.results[0].record.source)

    def test_its_ops_are_read_once_and_run_round_after_round(self):
        """A loop is one scope with its ops inside it, as the file has them,
        and not a round of nested scopes for each time it may run."""
        root = etree.fromstring(modops_file(self.REMOVING % "10").encode("utf-8"))
        ops = modops.parse_ops(root, [], "assets.xml", game=games.ANNO_117)
        self.assertEqual([bool(op.scope) for op in ops], [True, False])
        self.assertEqual((ops[0].rounds, ops[1].scopes), (10, (ops[0].scope,)))

    def test_a_group_inside_it_is_one_row_however_many_rounds(self):
        report = self.read('<Group Condition="@1000/Cost/Costs/Item" MaxRepeat="10">'
                           '<Group ModOpID="Inner"><ModOp Remove="@1000/Cost/Costs/Item[1]"/>'
                           '</Group></Group>')
        record = report.results[0].record
        self.assertEqual(len(report.changes), 2)
        self.assertEqual([part.get("id") for part in record.parts], ["Inner"])

    def test_inside_a_group_that_does_not_run_it_runs_no_rounds(self):
        report = self.read('<Group ModOpID="Off" Skip="1">%s</Group>' % (self.REMOVING % "10"))
        self.assertEqual(report.changes, [])
        self.assertEqual(sum(1 for result in report.results if result.op.scope), 2)


class TestAModValueThatCopies(ImportCase):
    """``<ModValue Insert="…"/>`` naming a path (Anno 117): a copy from
    another asset, or from beside where the value lands. In the loader's docs
    and not yet in a census mod."""

    def read(self, body):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_117)
        report.results = reader.Translator(self.index, True).run_all(ops)
        return report

    def values(self, body):
        return [(c.guid, c.path, c.new_value) for c in self.read(body).changes]

    def test_from_another_asset(self):
        self.assertEqual(
            self.values('<ModOp Merge="@1000/Building"><Radius>'
                        '<ModValue Insert="@1001/Building/Radius/text()"/></Radius></ModOp>'),
            [("1000", "/Values/Building/Radius", "4")],
        )

    def test_from_beside_where_it_lands(self):
        self.assertEqual(
            self.values('<ModOp Merge="@1000/Building"><Radius>'
                        '<ModValue Insert="../BuildingType/text()"/></Radius></ModOp>'),
            [("1000", "/Values/Building/Radius", "Factory")],
        )
        self.assertEqual(
            self.values('<ModOp Merge="@1001/Building"><Radius>'
                        '<ModValue Insert="../../Standard/GUID/text()"/></Radius></ModOp>'),
            [("1001", "/Values/Building/Radius", "1001")],
        )

    def test_text_beside_it_counts_as_it_does_beside_any_mark(self):
        """Under a merge the text after it is what the field ends up
        holding and the text before it is dropped, measured with xmltest3
        for a copy as the game has it for a value worked out.  The three
        were run together, ``101`` for the first of these, as a
        ``<ModOpContent/>`` is."""
        self.assertEqual(
            self.values('<ModOp Merge="@1000/Building"><Radius>'
                        '<ModValue Insert="../Radius/text()"/>1</Radius></ModOp>'),
            [("1000", "/Values/Building/Radius", "1")],
        )
        self.assertEqual(
            self.values('<ModOp Merge="@1000/Building"><Radius>'
                        '9<ModValue Insert="@1001/Building/Radius/text()"/></Radius></ModOp>'),
            [("1000", "/Values/Building/Radius", "4")],
        )

    def test_what_it_does_not_read_goes_back_out(self):
        for body, why in (
            ('<ModOp Merge="@1000/Building"><Radius><ModValue Insert="@1002/Cost/Costs/Item/Ingredient/text()">'
             "<Item><ModValueContent/></Item></ModValue></Radius></ModOp>", "ModValueContent"),
            ('<ModOp Merge="@1000/Building"><Radius>'
             '<ModValue Insert="@1002/Cost/Costs/Item/Ingredient/text()"/></Radius></ModOp>', "matches 4 nodes"),
            ('<ModOp Merge="@1000/Building"><Radius>'
             '<ModValue Insert="@4242/Building/Radius/text()"/></Radius></ModOp>', "does not have"),
            # The docs' own inline copy.  It was read as the value, ``4``.
            # The game was asked about a value copied with ``/text()`` and
            # not about a node copied whole outside a merge.
            ('<ModOp Replace="@1000/Building/Radius">'
             '<ModValue Insert="@1001/Building/Radius"/></ModOp>', "in an op that is not a merge"),
            # A section copied in beside text: nothing says what comes of it.
            ('<ModOp Merge="@1000/Building"><Radius>9'
             '<ModValue Insert="@1001/Building"/></Radius></ModOp>', "beside text of its own"),
            # A field named with no ``/text()`` was read as its value too.
            # Both builds leave the field holding the copied element.
            ('<ModOp Merge="@1000/Building"><Radius>'
             '<ModValue Insert="@1001/Building/Radius"/></Radius></ModOp>', "as a whole node"),
            ('<ModOp Merge="@1000/Building/Radius">'
             '<ModValue Insert="@1001/Building/Radius"/></ModOp>', "as a whole node"),
        ):
            with self.subTest(why=why):
                report = self.read(body)
                self.assertEqual([r.status for r in report.results], [modops.BLOCKED])
                self.assertIn(why, report.results[0].reason)


class TestAssetsOneOpPutSideBySide(SessionCase):
    """A run of assets from one op goes back out as the one op it was.

    ``addNextSibling`` puts what it carries immediately after the node it
    names, so two of them aimed at the same node land the second one first.
    Written back one ModOp per change, a pair of assets came out reversed --
    *Freely Buildable Music Pavilion* adds two triggers in one op, and the game
    took the wrong one of them.  Measured with the loader in
    ``test_modio_loader``.
    """

    ASSET = (
        "<Asset><Template>Trigger</Template><Values><Standard><GUID>%s</GUID>"
        "<Name>%s</Name></Standard></Values></Asset>"
    )

    def loaded(self, root):
        session = self.session(root)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.name, order=read.order))
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout, origin="a mod"):
        return generator.build_modops_xml(
            generator.for_mod(loadout, origin)
        )

    def body(self, kind="addNextSibling"):
        return '<ModOp Type="%s" GUID="1000">%s%s</ModOp>' % (
            kind, self.ASSET % ("7777", "first"), self.ASSET % ("7778", "second")
        )

    def test_they_are_two_changes_and_one_modop(self):
        loadout = self.loaded(self.mods(a__mod=self.body()))
        self.assertEqual(len(loadout.active()), 2)
        written = self.written(loadout)
        self.assertEqual(written.count("<ModOp "), 1)
        self.assertLess(written.index("<Name>first</Name>"), written.index("<Name>second</Name>"))

    def test_the_order_is_the_op_s_own_and_not_the_changes_list_s(self):
        """The changes list is sorted for reading, by asset name; the file is
        not.  Two assets one op added come back in the order it had them
        whichever way round their names sort."""
        body = '<ModOp Type="addNextSibling" GUID="1000">%s%s</ModOp>' % (
            self.ASSET % ("7777", "zeta"), self.ASSET % ("7778", "alpha")
        )
        loadout = self.loaded(self.mods(a__mod=body))
        self.assertEqual(
            [c.asset_label.split(" ")[0] for c in loadout.active()], ["alpha", "zeta"]
        )
        written = self.written(loadout)
        self.assertLess(written.index("<Name>zeta</Name>"), written.index("<Name>alpha</Name>"))

    def test_a_run_added_the_other_way_round_is_one_op_as_well(self):
        """A new asset is a new asset however the mod reached its neighbour --
        the Studio writes every one of them with ``addNextSibling`` -- and what
        one op added still goes out as one op."""
        loadout = self.loaded(self.mods(a__mod=self.body("addPrevSibling")))
        written = self.written(loadout)
        self.assertEqual(written.count("<ModOp "), 1)
        self.assertLess(written.index("<Name>first</Name>"), written.index("<Name>second</Name>"))

    def test_a_run_that_ends_at_a_change_aimed_elsewhere_is_still_kept(self):
        """Asked of the grouping itself: a change aimed somewhere else closes
        the run rather than throwing it away, and starts one of its own."""
        def add(path, spot):
            return Change(
                guid="1000", asset_label="x", path=path, tag="Asset",
                op=OP_ADD_NEXT, spot=spot, payload="<Asset/>",
            )

        block = [add("", 1), add("", 0), add("/Values/Building", 0), add("/Values/Building", 1)]
        runs = generator._side_by_side(block)
        self.assertEqual(
            [[change.path for change in run] for run in runs.values()],
            [["", ""], ["/Values/Building", "/Values/Building"]],
        )
        self.assertEqual([change.spot for change in runs[block[0].key]], [0, 1])

    def test_an_asset_added_on_its_own_is_still_its_own_op(self):
        body = self.body() .replace(self.ASSET % ("7778", "second"), "") + (
            '<ModOp Type="addNextSibling" GUID="1001">%s</ModOp>' % (self.ASSET % ("7778", "second"))
        )
        loadout = self.loaded(self.mods(a__mod=body))
        self.assertEqual(self.written(loadout).count("<ModOp "), 2)

    def test_two_assets_given_the_same_add_are_two_modops(self):
        """*Group ModOps by asset*, unticked, ran an op's assets into one
        block, and the second asset's add went out inside the first one's
        ModOp: one asset had both nodes and the other none."""
        loadout = Loadout(game=games.ANNO_1800)
        loadout.own_mod = True
        for guid in ("1000", "1001"):
            loadout.set_change(Change(
                guid=guid, asset_label=guid, path="/Values/Building", tag="Extra",
                op=OP_ADD_NEXT, payload="<Extra%s/>" % guid, authored=True,
            ))
        written = self.written(loadout, "")
        self.assertEqual(written.count("<ModOp "), 2)
        self.assertLess(written.index('GUID="1000"'), written.index("<Extra1000/>"))
        self.assertLess(written.index("<Extra1000/>"), written.index('GUID="1001"'))
        self.assertLess(written.index('GUID="1001"'), written.index("<Extra1001/>"))


class TestOpsTheStudioCouldNotReadStillGoOut(SessionCase):
    """An unread op used to be dropped from an export, by design: the change
    list was the promise, and an op with no changes in it would make the file
    do more than the list showed.  That kept the list honest by making the mod
    do less in game than it did before it was opened -- and for a mod of your
    own, overwriting the only copy.  So the op goes out exactly as it arrived,
    in the place it came from, and the list says so instead.
    """

    BEFORE = '<ModOp Merge="@1000/Building"><Radius>5</Radius></ModOp>'
    # No asset 9999 here, so the Studio cannot say what this does. In a game
    # with the mod that adds 9999 installed, it does exactly what it says.
    UNREAD = (
        '<ModOp Type="merge" GUID="9999" Path="/Values/Building" '
        'Condition="!/Values/Building/Extra"><Radius>1</Radius></ModOp>'
    )
    AFTER = '<ModOp Merge="@1001/Building"><Radius>6</Radius></ModOp>'

    def loaded(self, root, **kwargs):
        """Read a folder into a loadout the way the window does, unread and all."""
        session = self.session(root, **kwargs)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout, origin="a mod", comments=False):
        return generator.build_modops_xml(
            generator.for_mod(loadout, origin)
        )

    def test_the_op_is_carried_exactly_as_written(self):
        loadout = self.loaded(self.mods(a__mod=self.BEFORE + self.UNREAD + self.AFTER))
        self.assertEqual(len(loadout.unread), 1)
        written = self.written(loadout)
        self.assertIn('GUID="9999"', written)
        self.assertIn('Condition="!/Values/Building/Extra"', written)
        self.assertEqual(written.count("<ModOp "), 3)

    def test_and_in_the_place_it_came_from(self):
        """It patches the same changing document as the ops around it, so
        where it sits is part of what it does."""
        loadout = self.loaded(self.mods(a__mod=self.BEFORE + self.UNREAD + self.AFTER))
        written = self.written(loadout)
        self.assertLess(written.index("<Radius>5</Radius>"), written.index('GUID="9999"'))
        self.assertLess(written.index('GUID="9999"'), written.index("<Radius>6</Radius>"))

    # What sits among the ModOps without being one. Each used to be listed
    # under *Not ModOps* and then left out of the export.
    GROUP = (
        '<Group Condition="#1000"><ModOp Merge="@1001/Building">'
        "<Radius>25</Radius></ModOp></Group>"
    )
    ASSET = (
        "<Asset><Template>Building</Template><Values><Standard><GUID>7777</GUID>"
        "</Standard></Values></Asset>"
    )
    TWEAKS = '<ImyaTweaks Title="Radius"><Expose ModOpID="radius" Path="Radius"/></ImyaTweaks>'

    def test_a_group_with_a_condition_goes_back_where_it_was_ops_and_all(self):
        """It asks after a mod that is not loaded, so nothing in it runs today;
        it is kept as written all the same, because tomorrow it might."""
        loadout = self.loaded(self.mods(a__mod=self.BEFORE + self.GROUP + self.AFTER))
        self.assertEqual(loadout.unread, [])
        (kept,) = [op for op in loadout.ops if op.source.startswith("<Group")]
        self.assertIn("none of it does", kept.kept)
        self.assertEqual([c.new_value for c in loadout if c.op_uid == kept.uid], [])
        written = self.written(loadout)
        self.assertIn('<Group Condition="#1000">', written)
        self.assertIn("<Radius>25</Radius>", written)
        self.assertLess(written.index("<Radius>5</Radius>"), written.index("<Group "))
        self.assertLess(written.index("</Group>"), written.index("<Radius>6</Radius>"))

    def test_so_does_an_asset_written_straight_under_modops(self):
        """How Anno 117 mods add assets: a hundred of the mods on mod.io do, and
        an export without them is a mod whose other ops patch nothing."""
        loadout = self.loaded(self.mods(a__mod=self.ASSET + self.AFTER))
        written = self.written(loadout)
        self.assertIn("<GUID>7777</GUID>", written)
        self.assertLess(written.index("<GUID>7777</GUID>"), written.index("<Radius>6</Radius>"))

    def test_something_added_to_it_afterwards_is_shown_once(self):
        """The op after it goes out as an op of its own, so the asset kept as
        written keeps what its author wrote. It also took the addition in, and
        shown both ways it was there twice: *AI Buffed GeneralEnemy Ships*
        showed an effect target twice that the game has once."""
        add = '<ModOp Add="@7777/Standard"><Extra>1</Extra></ModOp>'
        loadout = self.loaded(self.mods(a__mod=self.ASSET + add))
        shown = Workspace(self.index, loadout).effective_asset("7777").as_the_game_reads_it()
        self.assertEqual(len(shown.findall("Values/Standard/Extra")), 1)
        written = self.written(loadout)
        self.assertEqual(written.count("<Extra>1</Extra>"), 1)

    @staticmethod
    def block(*guids, path="/AssetList/Groups[last()]", extra=""):
        """An add of one group: the first asset nested a group down, the rest
        in the group's own list."""
        asset = ("<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID>"
                 "</Standard></Values></Asset>")
        return (
            '<ModOp Add="%s"><Group><Groups><Group><Assets>%s</Assets></Group></Groups>'
            "<Assets>%s</Assets>%s</Group></ModOp>"
            % (path, asset % guids[0], "".join(asset % guid for guid in guids[1:]), extra)
        )

    @staticmethod
    def to_the_list_of(beside, *guids, path=None, extra=""):
        """An add of assets to the list that holds ``beside``."""
        asset = ("<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID>"
                 "<Name>made %s</Name></Standard><Building><Radius>5</Radius></Building>"
                 "</Values></Asset>")
        return '<ModOp Type="add" Path="%s">%s%s</ModOp>' % (
            path or "//Assets[Asset/Values/Standard/GUID='%s']" % beside,
            "".join(asset % (guid, guid) for guid in guids), extra,
        )

    def test_assets_added_to_the_list_that_holds_one_are_assets_the_mod_adds(self):
        """*Charcoal Power Plant*, *Commuter pier*, and 618 ModOps of the
        census: the path finds the list an asset stands in and no asset, so
        read as a search it answered to none.  The row said "0 assets", and
        every op aimed at what it adds read as aimed at nothing."""
        later = '<ModOp Type="merge" GUID="7777" Path="/Values/Building"><Radius>9</Radius></ModOp>'
        loadout = self.loaded(self.mods(a__mod=self.to_the_list_of("1000", "7777", "7778") + later))
        self.assertEqual(loadout.unread, [])
        (kept,) = [op for op in loadout.ops if "//Assets[" in op.source]
        self.assertEqual(kept.matched, ["7777", "7778"])
        self.assertFalse(kept.searches)
        self.assertIn("added to the list that holds asset 1000", kept.kept)
        made = [change for change in loadout.changes.values() if change.creates_asset]
        self.assertEqual([change.op_uid for change in made], [kept.uid, kept.uid])
        view = Workspace(self.index, loadout)
        shown = view.effective_asset("7777").as_the_game_reads_it()
        # The op after it found the asset, and changed it.
        self.assertEqual(shown.findtext("Values/Building/Radius"), "9")
        self.assertIsNotNone(view.effective_asset("7778").as_the_game_reads_it())
        # No pattern, to its row: it is drawn and counted as an op that
        # names what it changes.
        (shown_as,) = [one for one in keptops.of(loadout) if one.op is kept]
        self.assertEqual(keptops.shape(shown_as, loadout.game)[2][0], 0)
        self.assertEqual(keptops.marks(shown_as, loadout.game), [])
        # Written as it was, once.
        written = self.written(loadout)
        self.assertEqual(written.count("//Assets[Asset/Values/Standard/GUID='1000']"), 1)
        self.assertEqual(written.count("<GUID>7777</GUID>"), 1)

    def test_and_what_is_not_that_stays_the_search_it_was(self):
        for said, op in (
            ("no asset by that GUID", self.to_the_list_of("424242", "7777")),
            ("a GUID something has", self.to_the_list_of("1000", "1001")),
            ("the same GUID twice", self.to_the_list_of("1000", "7777", "7777")),
            ("something that is no asset", self.to_the_list_of("1000", "7777", extra="<Item/>")),
            ("another path", self.to_the_list_of(
                "1000", "7777", path="//Assets[Asset/Values/Standard/Name='x']")),
        ):
            with self.subTest(said):
                loadout = self.loaded(self.mods(a__mod=op))
                (kept,) = loadout.ops
                self.assertFalse([c for c in loadout.changes.values() if c.creates_asset])
                self.assertFalse(kept.kept)

    def test_a_block_of_assets_is_not_a_search(self):
        """It names no GUID, and was marked as a search for that: its row
        was offered to the pattern editor, which changes one."""
        from anno_modding_studio.ui import pattern_dialog

        loadout = self.loaded(self.mods(a__mod=self.block("7777", "7778")))
        (kept,) = [op for op in loadout.ops if "Groups[last()]" in op.source]
        self.assertTrue(kept.kept)
        self.assertFalse(kept.searches)
        self.assertFalse(pattern_dialog.holds(kept, loadout.game))

    def test_an_asset_straight_under_its_group_is_one_of_them(self):
        """*Require All Needs* keeps a list of its own in an ``<Asset>`` with no
        ``<Assets>`` round it and no template, and fills it with later ops."""
        cache = (
            '<ModOp Add="/AssetList/Groups[last()]"><Group><Asset><Values><Standard>'
            "<GUID>7777</GUID></Standard><Entries/></Values></Asset></Group></ModOp>"
            '<ModOp Add="@7777/Entries"><Item>1</Item></ModOp>'
        )
        loadout = self.loaded(self.mods(a__mod=cache))
        self.assertEqual(loadout.unread, [])
        shown = Workspace(self.index, loadout).effective_asset("7777").as_the_game_reads_it()
        self.assertEqual(len(shown.findall("Values/Entries/Item")), 1)
        self.assertEqual(self.written(loadout).count("<Item>1</Item>"), 1)

    def test_a_group_the_studio_cannot_vouch_for_stays_the_search_it_was(self):
        """Which of two assets sharing a GUID a later op finds was measured both
        ways for a group at the end of the file, so one repeating a GUID is the
        game's to answer; so is anything in the group besides assets, and any
        place but the one measured.  Each goes out as written, as before."""
        for name, body in (
            ("a GUID the game ships", self.block("7777", "1000")),
            ("a GUID twice", self.block("7777", "7778", "7777")),
            ("something besides assets", self.block("7777", "7778", extra="<Other/>")),
            ("another place", self.block("7777", "7778", path="//Groups")),
        ):
            with self.subTest(name):
                loadout = self.loaded(self.mods(a__mod=body))
                self.assertEqual([c for c in loadout if c.creates_asset], [])
                (kept,) = loadout.ops
                self.assertEqual((kept.matched, kept.kept), ([], ""))
                self.assertEqual(self.written(loadout).count("<GUID>7777</GUID>"),
                                 body.count("<GUID>7777</GUID>"))

    def test_a_copy_into_an_asset_added_that_way_still_goes_out(self):
        """*Celtic Independence Overhaul* adds two asset pools and fills each
        with a ``Content=`` copy of one of the game's.  The copy's work landed
        in the new asset, so it had no changes of its own -- and only an op
        with changes was ever kept as written.  That was enough while the new
        asset was written from the working tree; kept as the ``<Asset>`` it
        came as, it went out empty and the copy did not go out at all."""
        pool = (
            "<Asset><Template>Building</Template><Values><Standard><GUID>7777</GUID>"
            "</Standard><Building><Radius>1</Radius></Building></Values></Asset>"
        )
        copy = '<ModOp Replace="@7777/Building/Radius" Content="@1000/Building/Radius"></ModOp>'
        loadout = self.loaded(self.mods(a__mod=pool + copy))
        self.assertEqual(loadout.unread, [])
        written = self.written(loadout)
        self.assertIn('Content="@1000/Building/Radius"', written)
        self.assertLess(written.index("<GUID>7777</GUID>"), written.index("Content="))
        shown = Workspace(self.index, loadout).effective_asset("7777").as_the_game_reads_it()
        self.assertEqual(shown.findtext("Values/Building/Radius"), "10")

    def test_what_a_copy_read_stays_in_the_addition_it_read(self):
        """*Industrial Cities*' compact menus add an entry to one menu, copy it
        into another with ``Content=``, then remove it from the first. The
        removal folded into the addition, which came out empty, so in game the
        copy -- which goes out as written -- had nothing to copy.

        Each of the three ops now goes out as written, in its place among the
        others: the addition with the entry it adds, whatever a later op of
        the mod does to it (A11-132)."""
        add = ('<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
               "<Item><Product>1010</Product><Compact/></Item></ModOp>")
        copy = ('<ModOp Type="add" GUID="1002" Path="/Values/ProductList/List" '
                'Content="@1000/ProductList/List/Item[Compact]"/>')
        remove = '<ModOp Type="remove" GUID="1000" Path="/Values/ProductList/List/Item[Compact]"/>'
        loadout = self.loaded(self.mods(a__mod=add + copy + remove))
        workspace = Workspace(self.index, loadout)
        self.assertIsNone(workspace.effective_asset("1000").as_the_game_reads_it().find(".//Compact"))
        self.assertIsNotNone(workspace.effective_asset("1002").as_the_game_reads_it().find(".//Compact"))
        written = self.written(loadout)
        # The entry goes in, the copy reads it, and only then is it taken out.
        added = written.index("<Compact")
        copied = written.index('Content="@1000/ProductList/List/Item[Compact]"')
        taken = written.index('Type="remove"')
        self.assertLess(added, copied)
        self.assertLess(copied, taken)

    def test_but_only_for_the_game_whose_loader_is_known_to_add_it(self):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(self.ASSET).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=games.ANNO_1800)
        (result,) = reader.Translator(self.index).run_all(ops)
        self.assertEqual(result.status, modops.BLOCKED)

    def test_a_tweak_tool_s_markup_is_carried_without_counting_as_unread(self):
        """Not an op the Studio failed to read; but a skipped op kept for a
        tweak tool is no use to it without the markup that names it."""
        session = self.session(self.mods(a__mod=self.TWEAKS + self.AFTER))
        self.assertEqual(session.reads[0].unread, 0)
        self.assertEqual(
            [r.status for r in session.reads[0].report.results],
            [modops.CARRIED, modops.IMPORTED],
        )
        written = self.written(self.loaded(self.mods(b__mod=self.TWEAKS + self.AFTER)), "b mod")
        self.assertIn('<ImyaTweaks Title="Radius">', written)

    def test_a_mod_that_is_nothing_but_carried_ops_is_still_a_mod_to_write(self):
        loadout = self.loaded(self.mods(a__mod=self.UNREAD))
        view = generator.for_mod(loadout, "a mod")
        self.assertEqual(view.active_count, 0)
        self.assertTrue(generator.writes_anything(view))
        self.assertFalse(generator.writes_anything(generator.for_mod(Loadout(), "")))

    def test_the_file_says_nothing_over_it(self):
        """Why it could not be read is on the screen, where it is asked."""
        loadout = self.loaded(self.mods(a__mod=self.UNREAD))
        written = self.written(loadout, comments=True)
        self.assertIn('GUID="9999"', written)
        self.assertNotIn("<!--", written)

    def test_each_mod_takes_only_its_own(self):
        loadout = self.loaded(self.mods(a__mod=self.UNREAD, b__mod=self.AFTER))
        self.assertNotIn('GUID="9999"', self.written(loadout, "b mod"))
        self.assertIn('GUID="9999"', self.written(loadout, "a mod"))

    def test_the_position_survives_being_saved(self):
        from anno_modding_studio.core.changes import Unread

        loadout = self.loaded(self.mods(a__mod=self.BEFORE + self.UNREAD))
        entry = loadout.unread[0]
        self.assertEqual(entry.order, 1)
        self.assertEqual(Unread.from_dict(entry.to_dict()), entry)


class TestValuesKeptAsWrittenGoOutAsThemselves(SessionCase):
    """A value a mod works out while patching is settled for the editor and,
    unless asked otherwise, written back as the op that works it out.  That is
    the difference between a mod that still follows the trade union's socket
    count after the trip through the Studio and one frozen at *three*."""

    COPIED = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius" '
        'Content="@1001/Building/Radius"></ModOp>'
    )
    COMPUTED = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
        '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp>'
    )

    def loaded(self, root, **settle):
        session = self.session(root, reading=modops.Reading(**settle))
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
        return loadout

    def written(self, loadout):
        return generator.build_modops_xml(
            generator.for_mod(loadout, "a mod")
        )

    def test_the_editor_sees_what_the_copy_comes_to(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        self.assertEqual([c.new_value for c in loadout], ["4"])

    def test_and_the_export_keeps_the_copy(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        written = self.written(loadout)
        self.assertIn('Content="@1001/Building/Radius"', written)
        self.assertNotIn("<Radius>4</Radius>", written)
        self.assertEqual(written.count("<ModOp "), 1)

    def test_the_same_for_a_value_worked_out_from_the_mods_settings(self):
        loadout = self.loaded(self.mods(a__mod=self.COMPUTED))
        self.assertEqual([c.new_value for c in loadout], ["20"])
        written = self.written(loadout)
        self.assertIn('Insert="self::node() * 2"', written)
        self.assertNotIn("<Radius>20</Radius>", written)

    # A copy can go into a body the op writes itself, at a place it marks.
    TEMPLATE = (
        '<ModOp Type="add" GUID="1001" Path="/Values/Building" '
        'Content="@1000/Building/Radius%s"><Copied><ModOpContent/></Copied></ModOp>'
    )

    def test_kept_as_the_op_it_still_shows_what_the_body_comes_to(self):
        loadout = self.loaded(self.mods(a__mod=self.TEMPLATE % "/text()"))
        self.assertIn("<Copied>10</Copied>", "".join(next(iter(loadout)).payload.split()))
        self.assertIn("<ModOpContent/>", "".join(self.written(loadout).split()))

    # An asset made to be copied from, and removed once it has been.
    SCRATCH = (
        '<ModOp Type="addNextSibling" GUID="1000"><Asset><Template>Text</Template>'
        "<Values><Standard><GUID>7777</GUID><Name>scratch</Name></Standard>"
        "<Holds><Radius>33</Radius></Holds></Values></Asset></ModOp>"
        '<ModOp Type="merge" GUID="1001" Path="/Values/Building/Radius" '
        'Content="@7777/Holds/Radius"></ModOp>'
        '<ModOp Type="remove" GUID="7777"></ModOp>'
    )

    def test_a_scratch_asset_is_still_made_for_the_copies_that_need_it(self):
        """*City Variations* makes an asset, copies four lists out of it and
        removes it again.  The removal was folded into the creation's payload,
        which left an asset-creating op with an empty body -- and the copies,
        which go out as the ops they are, copying from an asset never made."""
        loadout = self.loaded(self.mods(a__mod=self.SCRATCH))
        written = self.written(loadout)
        self.assertIn("<GUID>7777</GUID>", written)
        self.assertIn("<Radius>33</Radius>", written)
        self.assertLess(written.index("<GUID>7777</GUID>"), written.index('Content="@7777/Holds/Radius"'))
        # Removed last, in whichever of the two games' spellings this is.
        self.assertIn("remove", written[written.index('Content="@7777/Holds/Radius"'):].lower())
        self.assertLess(written.index('Content="@7777/Holds/Radius"'), written.rindex("7777"))

    # Whether an op runs at all can be one of the mod's settings too.
    ROOMIER = (
        '<ModOp Merge="@1000/Building" Condition="$roomier"><Radius>40</Radius></ModOp>'
        '<ModOp Merge="@1001/Building" Condition="!$roomier"><Radius>41</Radius></ModOp>'
    )

    def configurable(self, body):
        root = self.mods(a__mod=body)
        (root / "a mod" / "modinfo.json").write_text(MODINFO_WITH_OPTIONS, encoding="utf-8")
        return root

    def test_and_for_an_op_that_asks_a_setting_whether_to_run(self):
        """Unsettled, that is the game's question to go on asking.  These were
        answered once, at the default, whatever the box said: the op that ran
        lost its condition and the one that did not was dropped, so the setting
        the mod still offers in game did nothing."""
        loadout = self.loaded(self.configurable(self.ROOMIER))
        self.assertEqual([(c.guid, c.new_value) for c in loadout], [("1001", "41")])
        written = self.written(loadout)
        self.assertIn('Condition="$roomier"', written)
        self.assertIn('Condition="!$roomier"', written)
        self.assertEqual(written.count("<ModOp "), 2)
        off = [op for op in loadout.ops if not op.made]
        self.assertIn("it does nothing", off[0].kept)

    def test_switching_it_off_writes_a_comment_rather_than_the_op(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        loadout.ops[0].active = False
        next(iter(loadout)).active = False
        view = keptops.of(loadout)[0]
        self.assertFalse(view.whole)
        written = self.written(loadout)
        self.assertEqual(_live_ops(written), [])
        self.assertIn(SWITCHED_OFF_MARK, written)

    def test_a_kept_op_names_its_asset_so_nothing_new_can_drift_into_it(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        self.assertEqual(keptops.drifted(loadout, Workspace(self.index, loadout)), [])

    def test_the_row_says_what_it_copies(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        op = loadout.ops[0]
        self.assertEqual(op.summary, "merge, copied from @1001/Building/Radius")
        self.assertIn("1 change, kept as the op itself", keptops.of(loadout)[0].caption())

    def test_the_reason_survives_being_saved(self):
        loadout = self.loaded(self.mods(a__mod=self.COPIED))
        op = loadout.ops[0]
        self.assertEqual(Op.from_dict(op.to_dict()).kept, op.kept)


class TestWhatTheStudioWroteSwitchedOffComesBackOff(SessionCase):
    """A mod exported with what is switched off in comments, read again:
    each of those is back, off, as the op it was, and goes out the same."""

    NARROW = (
        '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
        "<Radius>9</Radius></ModOp>"
    )
    LIVE = '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
    SEARCH = '<ModOp Type="merge" Path="//Values/Building"><Radius>99</Radius></ModOp>'

    def loaded(self, root):
        session = self.session(root)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout, comments: bool = True) -> str:
        return generator.build_modops_xml(
            generator.for_mod(loadout, "a mod"),
        )

    def again(self, xml: str):
        """What was written, read as the mod it is."""
        body = xml.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0]
        return self.loaded(self.mods(a__mod=body))

    def off(self, body: str) -> str:
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        return "<!-- %s:\n%s\n-->" % (SWITCHED_OFF_MARK, body)

    def test_an_op_that_is_not_written_the_studios_way_stays_the_op(self):
        """Its changes would go out as another op: the same edit, and not
        what the file said."""
        for body in (
            '<ModOp Merge="@1001/Building"><Radius>9</Radius></ModOp>',
            '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius" '
            "Condition=\"@1001/Building[Radius = '4']\"><Radius>9</Radius></ModOp>",
            '<ModOp Type="replace" GUID="1001,1000" Path="/Values/Building/Radius">'
            "<Radius>9</Radius></ModOp>",
        ):
            with self.subTest(op=body):
                back = self.loaded(self.mods(a__mod=self.off(body)))
                self.assertEqual(len(back.ops), 1)
                self.assertFalse(back.ops[0].active)
                self.assertTrue(all(c.op_uid == back.ops[0].uid for c in back))
                self.assertIn(body.split(">")[0], self.written(back).replace("\n", " "))

    def test_one_aimed_at_what_the_mod_adds_stays_the_op(self):
        """Read apart from the mod's other ops there is no such asset, so
        there is no answer to be the op."""
        back = self.loaded(self.mods(a__mod=self.off(
            '<ModOp Type="replace" GUID="999999" Path="/Values/Building/Radius">'
            "<Radius>9</Radius></ModOp>"
        )))
        self.assertEqual(len(back.changes), 0)
        self.assertEqual(len(back.ops), 1)
        self.assertIn('GUID="999999"', self.written(back))

    def test_it_is_not_run(self):
        """The op after it reads the document the game would hand it."""
        report = self.read(
            self.off('<ModOp Type="remove" GUID="1001" Path="/Values/Building"/>')
            + '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
            "<Radius>6</Radius></ModOp>"
        )
        shelved, live = report.results
        self.assertEqual(shelved.status, modops.SHELVED)
        self.assertEqual(report.count(modops.SETTLED), 0)
        self.assertIn("1 switched off", report.summary())
        self.assertNotIn("already true", report.summary())
        self.assertEqual(live.status, modops.IMPORTED, live.reason)
        self.assertEqual([(c.old_value, c.new_value) for c in live.changes], [("4", "6")])
        self.assertTrue(all(not c.active for c in shelved.changes))

    def test_a_search_comes_back_a_search(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.ops[0].active = False
        for change in loadout:
            change.active = False
        back = self.again(self.written(loadout))
        op = back.ops[0]
        self.assertFalse(op.active)
        self.assertEqual(op.kept, "")
        self.assertEqual(op.expression, "//Values/Building")
        self.assertEqual(sorted(op.matched), sorted({c.guid for c in loadout}))
        self.assertEqual(len(back.changes), len(loadout.changes))
        self.assertTrue(all(not c.active for c in back))

    def test_something_that_is_not_an_op_comes_back_as_itself(self):
        back = self.loaded(self.mods(
            a__mod=self.off('<Group Odd="1">%s</Group>' % self.NARROW)
        ))
        self.assertEqual(len(back.changes), 0)
        self.assertEqual(back.unread, [])
        self.assertFalse(back.ops[0].active)
        self.assertIn('<Group Odd="1">', self.written(back))
        self.assertEqual(self.written(back).count("switched off in"), 1)

    def test_a_comment_that_only_starts_like_one_is_a_comment(self):
        report = self.read(self.off("<ModOp Type=") + self.LIVE)
        self.assertEqual(len(report.results), 1)

    def test_what_was_said_above_it_is_its_note(self):
        back = self.loaded(self.mods(
            a__mod="<!-- why it is off -->" + self.off(self.NARROW) + self.LIVE
        ))
        self.assertEqual([c.note for c in back if not c.active], ["why it is off"])
        self.assertEqual([c.note for c in back if c.active], [""])


class TestWhatTheStudioSaidAboveAnOpIsNotANote(SessionCase):
    """A mod exported with a comment on each ModOp, read again.  The nearest
    comment above an op is its note, and the nearest one was the Studio's own:
    a change with no note came back with ``Radius: 10 -> 25`` for one, went
    out with that under ``note:``, and came back with ``note: Radius: 10 ->
    25`` -- a ``note:`` more for every trip.  What the Studio says is marked
    now, and what is read back and written again is the same file."""

    LIVE = '<ModOp Merge="@1000/Building"><Radius>25</Radius></ModOp>'
    OTHER = (
        '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
        "<Radius>9</Radius></ModOp>"
    )
    SEARCH = '<ModOp Type="merge" Path="//Values/Building"><Radius>99</Radius></ModOp>'
    # No asset 9999 here, so it is carried as written.
    UNREAD = (
        '<ModOp Type="merge" GUID="9999" Path="/Values/Building" '
        'Condition="!/Values/Building/Extra"><Radius>1</Radius></ModOp>'
    )

    def loaded(self, root):
        session = self.session(root)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout) -> str:
        return generator.build_modops_xml(generator.for_mod(loadout, "a mod"))

    def again(self, xml: str):
        """What was written, read as the mod it is."""
        body = xml.split("<ModOps>", 1)[1].rsplit("</ModOps>", 1)[0]
        return self.loaded(self.mods(a__mod=body))

    def said(self, loadout):
        """Every note in it, by what it is a note on."""
        return (
            sorted((c.guid, c.note) for c in loadout),
            sorted((op.expression, op.note) for op in loadout.ops),
            [entry.note for entry in loadout.unread],
        )

    def test_a_comment_written_by_hand_is_still_the_note(self):
        back = self.loaded(self.mods(
            a__mod="<!-- by hand -->" + self.LIVE + "<!-- note: marked by hand -->" + self.OTHER
        ))
        self.assertEqual(
            sorted((c.guid, c.note) for c in back),
            [("1000", "by hand"), ("1001", "marked by hand")],
        )
        once = self.written(back)
        self.assertEqual(self.said(self.again(once)), self.said(back))
        self.assertEqual(self.written(self.again(once)), once)

    def test_but_a_comment_that_only_reads_like_one_of_those_is_the_author_s(self):
        back = self.loaded(self.mods(a__mod="<!-- Radius: 10 -> 30 -->" + self.LIVE))
        self.assertEqual([c.note for c in back], ["Radius: 10 -> 30"])


class TestOpsKeptAsThemselvesGoOutAsThemselves(SessionCase):
    """Three more kinds of op that are the fact, with the changes as the answer:
    one the field-by-field reading could not express, one shipped switched
    off, and an edit made here to a mod that has searches."""

    # A third entry just like the first: the loader adds it -- a merge goes
    # by position, and there is no third to merge into -- and two entries
    # alike are ones no narrow op can tell apart. (This was an empty <Item/>
    # set beside the first entry, which the loader in fact merges into, and
    # so leaves as it was.)
    SWAP = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Cost/Costs">'
        "<Item/><Item/><Item><Ingredient>1010</Ingredient><Amount>5</Amount></Item></ModOp>"
    )
    SKIPPED = '<ModOp Merge="@1000/Building" Skip="1"><Radius>77</Radius></ModOp>'
    SEARCH = '<ModOp Type="merge" Path="//Values/Building"><Radius>99</Radius></ModOp>'

    def loaded(self, root):
        session = self.session(root)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(
                ModLayer(origin=read.origin, name=read.name, order=read.order)
            )
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout):
        return generator.build_modops_xml(
            generator.for_mod(loadout, "a mod")
        )

    def test_a_whole_node_swap_goes_out_as_the_op_not_the_swap(self):
        loadout = self.loaded(self.mods(a__mod=self.SWAP))
        self.assertEqual(len(loadout.ops), 1)
        self.assertIn(OP_REMOVE, {c.op for c in loadout})
        written = self.written(loadout)
        self.assertIn('Path="/Values/Cost/Costs"', written)
        self.assertIn("<Item/>", written.replace("<Item />", "<Item/>"))
        self.assertNotIn('Type="remove"', written)
        self.assertEqual(written.count("<ModOp "), 1)

    def test_a_skipped_op_goes_out_switched_off_as_it_came(self):
        loadout = self.loaded(self.mods(a__mod=self.SKIPPED))
        self.assertEqual(len(loadout.ops), 1)
        self.assertEqual(len(loadout.changes), 0)
        written = self.written(loadout)
        self.assertIn('Skip="1"', written)
        self.assertIn("<Radius>77</Radius>", written)
        view = keptops.of(loadout)[0]
        self.assertTrue(view.whole, view.reason)
        self.assertIn("0 changes, kept as the op itself", view.caption())

    def test_an_edit_made_here_lands_after_the_mods_own_ops(self):
        """All of these, except this one: written before the search, the
        search would overwrite it.  An edit made in the Studio goes after every op
        read from the mod's file."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        self.assertEqual(loadout.ops[0].order, 0)
        self.assertEqual(loadout.after_last_op("a mod"), 1)
        exception = loadout.place(
            Change(
                guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
                tag="BuildingType", old_value="Farm", new_value="Mine", origin="a mod",
            )
        )
        self.assertEqual(exception.order, 1)
        loadout.set_change(exception)
        written = self.written(loadout)
        self.assertLess(
            written.index('Path="//Values/Building"'),
            written.index("<BuildingType>Mine</BuildingType>"),
        )

    def exception(self, loadout, value="5"):
        return loadout.place(
            Change(
                guid="1000", asset_label="alpha", path="/Values/Building/Radius",
                tag="Radius", old_value="99", new_value=value, origin="a mod",
            )
        )

    def test_typing_over_one_of_an_ops_answers_is_an_exception_beside_it(self):
        """The answer is what the op does to that asset and stays; the edit
        is the op after it that puts the one asset back.  It used to take
        the answer's key and replace it in the list."""
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        answers = len(loadout.changes)
        exception = self.exception(loadout)
        self.assertEqual(exception.exception_of, loadout.ops[0].uid)
        self.assertTrue(exception.uid)
        self.assertEqual(exception.order, 1)
        loadout.set_change(exception)
        self.assertEqual(len(loadout.changes), answers + 1)
        self.assertEqual(keptops.of(loadout)[0].caveat, "")
        written = self.written(loadout)
        self.assertLess(
            written.index('Path="//Values/Building"'), written.index("<Radius>5</Radius>")
        )
        self.assertEqual(written.count("<ModOp "), 2)

    def test_typing_over_it_again_retunes_the_exception(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        first = self.exception(loadout)
        loadout.set_change(first)
        second = self.exception(loadout, "6")
        self.assertEqual(second.key, first.key)
        loadout.set_change(second)
        self.assertEqual([c.new_value for c in loadout if c.exception_of], ["6"])

    def test_and_typing_the_ops_value_back_takes_the_exception_away(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.set_change(self.exception(loadout))
        loadout.set_change(self.exception(loadout, "99"))
        self.assertEqual([c for c in loadout if c.exception_of], [])

    def test_the_asset_shows_the_exception_over_the_op(self):
        loadout = self.loaded(self.mods(a__mod=self.SEARCH))
        loadout.set_change(self.exception(loadout))
        shown = Workspace(self.index, loadout).effective_asset("1000")
        self.assertEqual(shown.element.findtext("Values/Building/Radius"), "5")


class TestWhatStandsAmongAnOpsElementsGoesOutWithIt(SessionCase):
    """An op kept as written holds what its author put directly inside it:
    text after an element, and comments.  The op as written had the text
    before its first element and the elements, so the rest was in nothing
    the mod held and gone from the export: after a ``<ModOpContent/>``,
    where it is part of the value the game makes, as much as anywhere."""

    MERGE = '<ModOp Type="merge" GUID="1000" Path="/Values/Building">%s</ModOp>'
    TAIL = MERGE % "\n  <Radius>12</Radius> loose words\n  <BuildingType>Farm</BuildingType>\n"
    COMMENT = MERGE % (
        "\n  <Radius>12</Radius>\n  <!-- why the type -->\n  <BuildingType>Farm</BuildingType>\n"
    )
    COPY = (
        '<ModOp Type="add" GUID="1001" Path="/Values/Building" '
        'Content="@1000/Building/Radius/text()"><Copied>1</Copied><ModOpContent/> and more'
        "</ModOp>"
    )
    VALUE = (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
        '<ModValue Insert="self::node() * 2"/>5</ModOp>'
    )
    TEXT = '<ModOp Type="add" GUID="1000" Path="/Values/Standard/Name">;one<!-- c -->;two</ModOp>'
    GROUP = '<Group Condition="#a mod">%s</Group>' % (TAIL + COMMENT + COPY + VALUE)
    # What each is, the op, and the part of it that was not kept.
    WRITTEN = (
        ("text after an element", TAIL, "</Radius> loose words\n  <BuildingType>"),
        ("a comment between two", COMMENT, "</Radius><!-- why the type --><BuildingType>"),
        ("text after a copy", COPY, "<ModOpContent/> and more</ModOp>"),
        ("text after a value worked out", VALUE, '2"/>5</ModOp>'),
        ("text on both sides of a comment", TEXT, ">;one<!-- c -->;two</ModOp>"),
    )

    def loaded(self, root):
        loadout = Loadout()
        for read in self.session(root).reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.name, order=read.order))
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def exported(self, loadout, off: bool = False):
        """The mod written out, as a mods folder of its own to read again."""
        written = generator.build_modops_xml(
            generator.for_mod(loadout, "a mod")
        )
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        patch = root / "a mod" / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(written, encoding="utf-8")
        return root

    def says(self, loadout):
        return [op.source for op in loadout.ops] + [entry.source for entry in loadout.unread]

    def test_the_op_as_written_has_it(self):
        for what, op, held in self.WRITTEN:
            with self.subTest(what):
                (source,) = self.says(self.loaded(self.mods(a__mod=op)))
                self.assertIn(held, source)

    def test_and_the_export_read_again_says_what_the_mod_said(self):
        for what, op, held in self.WRITTEN:
            with self.subTest(what):
                loadout = self.loaded(self.mods(a__mod=op))
                again = self.loaded(self.exported(loadout))
                self.assertEqual(self.says(again), self.says(loadout))
                self.assertIn(held, self.says(again)[0])

    def test_the_same_for_ops_in_a_group(self):
        loadout = self.loaded(self.mods(a__mod=self.GROUP))
        said = self.says(loadout)
        self.assertEqual(len(said), 5)
        for what, _op, held in self.WRITTEN[:4]:
            with self.subTest(what):
                self.assertEqual(sum(held in source for source in said), 1)
        self.assertEqual(self.says(self.loaded(self.exported(loadout))), said)

    def test_the_reading_makes_the_same_changes_of_it_as_before(self):
        """What the game does with such text is the game's: it is carried,
        and the changes shown are worked out from the elements as they were.

        But for text after a value worked out under a merge, which the
        reading took no notice of and showed the value alone, 20.  In game
        that text is what the field ends up holding
        (``test_text_after_a_mark_under_a_merge_is_what_the_field_ends_up_holding``)."""
        for what, op, _held in self.WRITTEN:
            if op is self.VALUE:
                loadout = self.loaded(self.mods(a__mod=op))
                self.assertEqual([change.new_value for change in loadout], ["5"])
                self.assertEqual(loadout.unread, [])
                continue
            with self.subTest(what):
                node = etree.fromstring(op)
                for child in node:
                    child.tail = None
                    if not isinstance(child.tag, str):
                        node.remove(child)
                bare = etree.tostring(node, encoding="unicode")
                self.assertNotEqual(bare, op)

                def changes(body):
                    return [
                        (change.op, change.path, change.new_value, change.payload)
                        for change in self.loaded(self.mods(a__mod=body))
                    ]

                self.assertEqual(changes(op), changes(bare))

    def test_a_mod_read_again_is_the_ops_it_has_here_already(self):
        """Matched by what each op says, so what an op says has to come out
        the same each time its file is read."""
        root = self.mods(a__mod=self.GROUP + "".join(op for _what, op, _held in self.WRITTEN))
        loadout = self.loaded(root)
        uids = [op.uid for op in loadout.ops]
        self.assertEqual(len(uids), 10)
        (read,) = self.session(root).reads
        self.assertTrue(set(uids).isdisjoint(op.uid for op in read.report.ops))
        matched = loadout.as_before(read.report.ops, read.changes, read.report.unread(read.origin))
        self.assertEqual(matched, len(uids))
        self.assertEqual([op.uid for op in read.report.ops], uids)
        for op in read.report.ops:
            loadout.set_op(op)
        self.assertEqual([op.uid for op in loadout.ops], uids)

    def test_how_an_op_is_laid_out_is_not_in_what_it_says(self):
        """Blank text between two elements, and around a comment, is the
        file's indentation: an op says the same however it was laid out."""
        flat = self.MERGE % "<Radius>12</Radius><BuildingType>Farm</BuildingType>"
        spread = self.MERGE % (
            "\n  <Radius>12</Radius>\n\n\t<BuildingType>Farm</BuildingType>\n"
        )
        for op in (flat, spread):
            self.assertEqual(self.says(self.loaded(self.mods(a__mod=op))), [flat])
        (commented,) = self.says(self.loaded(self.mods(a__mod=self.COMMENT)))
        self.assertEqual(
            commented,
            self.MERGE % "<Radius>12</Radius><!-- why the type --><BuildingType>Farm</BuildingType>",
        )

    def test_an_op_without_it_is_not_the_same_op(self):
        """What asks whether an op put together again is the op it was made
        from: one that lost such text, or a comment, is not."""
        for what, op, _held in self.WRITTEN:
            with self.subTest(what):
                (source,) = self.says(self.loaded(self.mods(a__mod=op)))
                node = etree.fromstring(source)
                for child in node:
                    child.tail = None
                    if not isinstance(child.tag, str):
                        node.remove(child)
                bare = etree.tostring(node, encoding="unicode")
                self.assertTrue(keptops.same_op(source, op))
                self.assertFalse(keptops.same_op(source, bare))
                self.assertNotEqual(keptops.canonical_op(source), keptops.canonical_op(bare))

    def test_switched_off_it_goes_out_and_comes_back_with_its_text(self):
        """An op read back from the comment it was switched off in is let
        go of for its changes where those, written out, are that op.  With
        text after an element they are not, and it stays the op it is."""
        loadout = self.loaded(self.mods(a__mod=self.TAIL))
        for op in loadout.ops:
            op.active = False
        for change in loadout:
            change.active = False
        again = self.loaded(self.exported(loadout, off=True))
        self.assertEqual(self.says(again), self.says(loadout))
        self.assertEqual([op.active for op in again.ops], [False])

    def switched_off(self, body: str):
        """A mod of these ops with every one switched off, and the file it
        is written as."""
        loadout = self.loaded(self.mods(a__mod=body))
        for op in loadout.ops:
            op.active = False
        for change in loadout:
            change.active = False
        root = self.exported(loadout, off=True)
        return loadout, root, (root / "a mod/data/base/config/export/assets.xml").read_text("utf-8")

    def test_switched_off_it_comes_back_with_its_comments(self):
        """A comment cannot hold a comment, so the op's own were left out of
        the one it is switched off in, and the op read back was another."""
        for what, op, held in self.WRITTEN:
            with self.subTest(what):
                loadout, root, _written = self.switched_off(op)
                again = self.loaded(root)
                self.assertEqual(self.says(again), self.says(loadout))
                self.assertIn(held, self.says(again)[0])
                self.assertEqual([op.active for op in again.ops], [False])

    # Comments an op can hold that the way they are written switched off has
    # to carry: deep inside it, over several ops, and saying what that
    # writing itself uses.
    DEEP = MERGE % "<Radius>12</Radius><Sub><!-- deep --><Item>1</Item><!-- last --></Sub>"
    AWKWARD = (
        "<!---starts with a dash -->",
        "<!-- R&D &amp; &#45; &amp;#45; -->",
        "<!-- &#45;&#45;> is how one ends, <!&#45;&#45; how one starts -->",
        "<!-- <Radius>1</Radius> <?not one?> -->",
        "<!---->",
    )

    def test_whatever_its_comments_say(self):
        for comment in self.AWKWARD:
            with self.subTest(comment):
                op = self.MERGE % ("<Radius>12</Radius>%s<BuildingType>Farm</BuildingType>" % comment)
                loadout, root, written = self.switched_off(self.DEEP + op)
                self.assertEqual(self.says(loadout), [self.DEEP, op])
                deep, flat = self.says(self.loaded(root))
                self.assertEqual(flat, op)
                # Inside an element the layout it was written out in is held
                # with it, so this one is the op as the same export gives it
                # switched on, and the op held in all but that.
                on = self.loaded(self.mods(a__mod=self.DEEP + op))
                self.assertEqual(self.says(self.loaded(self.exported(on)))[0], deep)
                self.assertTrue(keptops.same_op(deep, self.DEEP))
                self.assertEqual(deep.count("<!--"), 2)
                # And the file is one the game can read: a comment holding
                # ``--`` is not XML.
                etree.fromstring(written.encode("utf-8"))

    # A comment that runs over lines, with a blank one and an indentation of
    # its own: directly inside the op, and inside an element of it.
    OVER_LINES = (
        "<!-- why the type:\n\n        it was a farm\n      all along -->",
        "<!--\n deep\n\t  one -->",
    )
    LONG = MERGE % (
        "\n  <Radius>12</Radius>\n  %s\n  <Sub>%s<Item>1</Item></Sub>\n" % OVER_LINES
    )

    def test_a_comment_over_several_lines_goes_out_as_it_is_held(self):
        """The export laid the op out and indented every line of it again,
        the comment's own with the rest, and dropped a blank line in it: the
        op written was not the op held, and read back it was another."""
        loadout = self.loaded(self.mods(a__mod=self.LONG))
        (held,) = self.says(loadout)
        for comment in self.OVER_LINES:
            self.assertIn(comment, held)
        root = self.exported(loadout)
        written = (root / "a mod/data/base/config/export/assets.xml").read_text("utf-8")
        for comment in self.OVER_LINES:
            self.assertIn(comment, written)
        again = self.loaded(root)
        self.assertEqual(self.says(again), [held])
        self.assertTrue(keptops.same_op(self.says(again)[0], self.LONG))

    def test_and_switched_off_it_comes_back_as_it_was_held(self):
        loadout, root, written = self.switched_off(self.LONG)
        again = self.loaded(root)
        self.assertEqual(self.says(again), self.says(loadout))
        self.assertTrue(keptops.same_op(self.says(again)[0], self.LONG))
        self.assertEqual([op.active for op in again.ops], [False])
        etree.fromstring(written.encode("utf-8"))

    def test_an_op_whose_comments_are_one_line_each_is_laid_out_as_it_was(self):
        root = self.exported(self.loaded(self.mods(a__mod=self.COMMENT + self.DEEP)))
        self.assertIn(
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">\n'
            "    <Radius>12</Radius>\n"
            "    <!-- why the type -->\n"
            "    <BuildingType>Farm</BuildingType>\n"
            "  </ModOp>\n"
            # With no blank line between them, as the mod has them.
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">\n'
            "    <Radius>12</Radius>\n"
            "    <Sub>\n"
            "      <!-- deep -->\n"
            "      <Item>1</Item>\n"
            "      <!-- last -->\n"
            "    </Sub>\n"
            "  </ModOp>\n",
            (root / "a mod/data/base/config/export/assets.xml").read_text("utf-8"),
        )

    def test_text_that_reads_like_a_comment_written_so_stays_text(self):
        """How a comment is written inside the one an op is switched off in
        is nothing an author's own words can come out as."""
        from xml.sax.saxutils import escape, quoteattr

        said = "<!&#45;&#45; not one &#45;&#45;> <!-- nor this --> -- &#45; &amp;"
        op = (
            '<ModOp Type="add" GUID="1000" Path="/Values/Standard/Name" Note=%s>%s'
            "<Radius>%s</Radius><!-- one -->%s</ModOp>"
        ) % (quoteattr(said), escape(said), escape(said), escape(said))
        loadout, root, written = self.switched_off(op)
        again = self.loaded(root)
        self.assertEqual(self.says(again), self.says(loadout))
        (node,) = [etree.fromstring(source) for source in self.says(again)]
        self.assertEqual(node.get("Note"), said)
        self.assertEqual(node.text, said)
        self.assertEqual(node[0].text, said)
        self.assertEqual([child.text for child in node if not isinstance(child.tag, str)], [" one "])
        etree.fromstring(written.encode("utf-8"))

    def test_a_processing_instruction_is_not_gone_through_for_comments(self):
        op = self.MERGE % (
            "<Radius>12</Radius><?tool <!-- not one --> <!&#45;&#45; nor this &#45;&#45;> a&b ?>"
            "<!-- one -->"
        )
        loadout, root, _written = self.switched_off(op)
        self.assertEqual(self.says(self.loaded(root)), self.says(loadout))

    def test_an_op_with_no_comment_is_written_switched_off_as_it_was(self):
        _loadout, _root, written = self.switched_off(
            self.MERGE % "<Radius>a--b &amp; c</Radius><BuildingType>Farm</BuildingType>"
        )
        self.assertIn(
            "  <!-- switched off in Anno Modding Studio:\n"
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">\n'
            "    <Radius>a-&#45;b &amp; c</Radius>\n"
            "    <BuildingType>Farm</BuildingType>\n"
            "  </ModOp>\n"
            "  -->\n",
            written,
        )

    def test_a_comment_of_the_mods_own_is_read_as_it_was(self):
        """Only what the Studio switched off is read back so: commented-out
        XML an author left in a file is a comment, however it is spelt."""
        left = "<!-- <ModOp Type='merge' GUID='1000' Path='/Values'><!&#45;&#45; c &#45;&#45;></ModOp> -->"
        loadout = self.loaded(self.mods(a__mod=left + self.TAIL))
        self.assertEqual(len(self.says(loadout)), 1)
        self.assertEqual(self.says(self.loaded(self.exported(loadout))), self.says(loadout))


class TestKeptOpsWithNoPathSurviveReopening(SessionCase):
    """An op that names its asset and has no Path is kept with no expression.

    ``<ModOp Type="addNextSibling" GUID="601470">`` kept as written is one:
    its source is the whole of it, and there is no search to show.  Opening
    a loadout dropped every op without an expression, so a loadout came back
    from disk with its changes pointing at ops it no longer had -- written
    one field at a time, or not at all where the op had nothing underneath.
    """

    SKIPPED = (
        '<ModOp Type="merge" GUID="1000" Skip="1">'
        "<Values><Building><Radius>77</Radius></Building></Values></ModOp>"
    )
    ASKS = (
        '<ModOp Type="merge" GUID="1000" Condition="#a other">'
        "<Values><Building><Radius>30</Radius></Building></Values></ModOp>"
    )

    loaded = TestOpsKeptAsThemselvesGoOutAsThemselves.loaded

    def reopened(self, loadout):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loadout.annoload"
            loadout.save(path)
            return Loadout.load(path)

    def written(self, loadout, origin="a mod"):
        return generator.build_modops_xml(
            generator.for_mod(loadout, origin)
        )

    def test_a_skipped_op_is_still_in_the_file_after_reopening(self):
        loadout = self.loaded(self.mods(a__mod=self.SKIPPED))
        self.assertEqual([op.expression for op in loadout.ops], [""])
        again = self.reopened(loadout)
        self.assertEqual(
            [op.to_dict() for op in again.ops],
            [op.to_dict() for op in loadout.ops],
        )
        self.assertIn('Skip="1"', self.written(again))
        self.assertEqual(self.written(again), self.written(loadout))

    def test_an_op_asking_after_another_mod_keeps_its_question(self):
        """With changes underneath, which are what went out in its place: the
        op without its condition, running whether the other mod is there or
        not."""
        loadout = self.loaded(self.mods(a__other="<ModOp/>", b__asker=self.ASKS))
        op = loadout.ops_of("b asker")[0]
        self.assertEqual(op.expression, "")
        self.assertIn("which other mods are loaded", op.kept)
        self.assertEqual(op.matched, ["1000"])
        before = self.written(loadout, "b asker")
        self.assertIn('Condition="#a other"', before)

        again = self.reopened(loadout)
        self.assertEqual(again.op(op.uid).to_dict(), op.to_dict())
        answers = [c for c in again if c.origin == "b asker"]
        self.assertEqual([c.new_value for c in answers], ["30"])
        for change in answers:
            self.assertIsNotNone(again.op(change.op_uid), change.key)
        self.assertEqual(self.written(again, "b asker"), before)

    def test_everything_it_carries_comes_back(self):
        op = Op(
            source='<ModOp Type="addNextSibling" GUID="1000"><Asset/></ModOp>',
            summary="addNextSibling Asset",
            origin="a mod",
            order=7,
            matched=["1000"],
            made=2,
            caveat="replayed as written it does not land on alpha",
            folded=[["a mod", "1000", "", OP_ADD_NEXT, "abc"]],
            kept="written one field at a time its changes to alpha do not add up",
        )
        loadout = Loadout()
        loadout.set_op(op)
        self.assertEqual(
            [kept.to_dict() for kept in self.reopened(loadout).ops], [op.to_dict()]
        )

    def test_an_op_with_neither_an_expression_nor_a_source_is_still_dropped(self):
        loadout = Loadout()
        loadout.set_op(Op(origin="a mod"))
        self.assertEqual(self.reopened(loadout).ops, [])


class TestOpsKeptInOtherFiles(ImportCase):
    """A mod may keep its ModOps somewhere else and have assets.xml list them.

    *Charcoal Power Plant* is seven ``<Include>``s and no ops at all -- read
    literally it does nothing, which is not a reading anybody wants.  Eighteen
    of the mods installed here are written that way.
    """

    def mod(self, main: str, **beside) -> Path:
        """Write a mod: ``main`` as its assets.xml, plus files beside it.

        Keys are paths under the mod root with ``/`` written as ``|``, since a
        keyword cannot hold a slash.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        patch = root / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(modops_file(main), encoding="utf-8")
        for name, body in beside.items():
            where = root / name.replace("|", "/")
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_text(modops_file(body), encoding="utf-8")
        return root

    def run_mod(self, main: str, **beside):
        return reader.analyse(self.mod(main, **beside), self.index, origin="m")

    # The classic form: the mod is an Anno 1800 one, whose loader has no short form.
    RAISE = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>77</Radius></ModOp>'

    def said_about_includes(self, report):
        """What the report has to say about the ``<Include>``s themselves.

        Searched for rather than indexed: a mod that keeps ops in files of its
        own is also a mod that ships files the Studio does not model, and those
        are listed too.
        """
        return [e for e in report.extras if e.what.startswith("<Include")]

    def test_the_ops_of_the_file_it_names_are_read(self):
        report = self.run_mod(
            main='<Include File="../../../../../parts/one.xml" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual(len(report.results), 1)
        self.assertEqual([c.new_value for c in report.changes], ["77"])

    def test_the_element_in_any_case_as_the_loader_takes_it(self):
        """The loader compares element names without case; ``File`` it looks
        up as written, so ``file=`` names nothing to it."""
        report = self.run_mod(
            main='<include File="/parts/one.xml" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual([c.new_value for c in report.changes], ["77"])
        report = self.run_mod(
            main='<Include file="/parts/one.xml" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual(report.changes, [])

    def test_the_path_is_relative_to_the_file_doing_the_including(self):
        """Five levels up out of data/config/export/main/asset is the mod root,
        which is how every mod that does this writes it."""
        report = self.run_mod(
            main='<Include File="../../../../../deep/down/here.xml" />',
            **{'deep|down|here.xml': self.RAISE},
        )
        self.assertEqual(len(report.changes), 1)

    def test_unless_it_starts_with_a_slash_which_is_the_top_of_the_mod(self):
        """``File="/data/…"`` was joined to a folder as it stood, which makes it
        a path from the top of the drive: the file was "not there", and all but
        one op of *BT Balanced Profits* went unread."""
        report = self.run_mod(
            main='<Include File="/parts/one.xml" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual([c.new_value for c in report.changes], ["77"])
        self.assertEqual([e.detail for e in self.said_about_includes(report)], [])

    def test_and_the_same_from_inside_a_file_that_was_itself_included(self):
        report = self.run_mod(
            main='<Include File="/parts/outer.xml" />',
            **{
                'parts|outer.xml': '<Include File="/deep/down/here.xml" />',
                'deep|down|here.xml': self.RAISE,
            },
        )
        self.assertEqual([c.new_value for c in report.changes], ["77"])

    def test_and_what_it_includes_is_still_read_when_the_group_is(self):
        report = self.run_mod(
            main='<Group Condition="@1000/Building"><Include File="/parts/one.xml" /></Group>',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual([c.new_value for c in report.changes], ["77"])

    def test_one_included_on_a_condition_that_holds_is_read(self):
        report = self.run_mod(
            main='<Include File="/parts/one.xml" Condition="@1000/Building" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual([c.new_value for c in report.changes], ["77"])
        self.assertEqual(report.unread("m"), [])

    def test_the_files_it_pulled_in_are_listed_as_read(self):
        report = self.run_mod(
            main='<Include File="../../../../../parts/one.xml" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual(
            [p.name for p in report.patch_files], ["assets.xml", "one.xml"]
        )

    def test_a_file_that_was_read_is_not_listed_as_left_behind(self):
        """*Celtic Independence Overhaul* keeps its ops in 20 ``.include.xml``
        files.  The folder scan lists such a file as a patch for a game file
        the Studio does not model, and went on listing it once an ``<Include>``
        had its ops read: 20 of its 29 "not ModOps" were files it had read."""
        report = self.run_mod(
            main='<Include File="/parts/one.include.xml" />',
            **{
                'parts|one.include.xml': self.RAISE + '<Include File="two.include.xml" />',
                'parts|two.include.xml': self.RAISE,
                'parts|stray.include.xml': self.RAISE,
            },
        )
        self.assertEqual(len(report.changes), 1)
        self.assertEqual(
            [e.where for e in report.extras if "another game file" in e.what],
            ["parts/stray.include.xml"],
        )

    def test_and_what_reading_it_had_to_say_is_still_said(self):
        report = self.run_mod(
            main='<Include File="/parts/one.include.xml" />',
            **{'parts|one.include.xml': self.RAISE + '<Include File="nowhere.xml" />'},
        )
        self.assertEqual(
            [e for e in report.extras if "another game file" in e.what], []
        )
        self.assertIn("is not there", self.said_about_includes(report)[0].detail)

    def test_an_include_inside_an_include_is_followed_too(self):
        report = self.run_mod(
            main='<Include File="../../../../../parts/one.xml" />',
            **{'parts|one.xml': '<Include File="two.xml" />'},
            **{'parts|two.xml': self.RAISE},
        )
        self.assertEqual(len(report.changes), 1)

    def test_a_patch_in_a_sub_folder_with_no_modinfo_is_not_the_mod_s(self):
        """P7-32, measured in Anno 1800: a folder inside a mod with a data tree
        and no modinfo of its own is loaded neither as a mod nor as part of its
        host -- the loader log names it nowhere, and an op in it never ran.
        *Merchants* ships ``wool_mill_kurila/`` that way, and read as the host's,
        its ops went into the export's own patch and ran."""
        report = self.run_mod(
            main='<ModOp Type="merge" GUID="1001" Path="/Values/Building"><Radius>25</Radius></ModOp>',
            **{"Not A Mod|data|config|export|main|asset|assets.xml": self.RAISE},
        )
        self.assertEqual([c.new_value for c in report.changes], ["25"])
        said = [e for e in report.extras if "does not load" in e.what]
        self.assertEqual([e.where for e in said], ["Not A Mod/data/config/export/main/asset/assets.xml"])

    def test_one_that_cannot_be_read_goes_back_out_as_the_include_it_was(self):
        """*Super Specialists* has text before the XML declaration of one of
        its files, which the loader passes over and this parser does not.
        Dropped, the export lost all of it in game; kept, and named from the
        top of the mod, the game reads the file itself."""
        root = self.mod(main='<Include File="../../../../../parts/one.xml" />',
                        **{"parts|one.xml": self.RAISE})
        (root / "parts" / "one.xml").write_text(
            '2144000030<?xml version="1.0"?>' + modops_file(self.RAISE), encoding="utf-8")
        report = reader.analyse(root, self.index, origin="m")
        self.assertEqual(report.changes, [])
        (entry,) = report.unread("m")
        kept = etree.fromstring(entry.source)
        self.assertEqual((kept.tag, kept.get("File")), ("Include", "/parts/one.xml"))
        self.assertIn("for the game to read", entry.why)
        self.assertIn("unreadable", self.said_about_includes(report)[0].detail)

    def test_the_comment_above_one_that_cannot_be_read_goes_back_out_with_it(self):
        """The ``<Include>`` that goes back out is a copy of the one written,
        and its comment was read from the copy, which has nothing above it:
        what the author said of the line was lost."""
        root = self.mod(main='<!-- the parts -->'
                             '<Include File="../../../../../parts/one.xml" />',
                        **{"parts|one.xml": self.RAISE})
        (root / "parts" / "one.xml").write_text(
            '2144000030<?xml version="1.0"?>' + modops_file(self.RAISE), encoding="utf-8")
        _report, ops = modops.read_source(root, origin="m")
        (kept,) = [op for op in ops if op.path.startswith("<Include")]
        self.assertEqual(kept.comment, "the parts")
        self.assertGreater(kept.line, 0)

    def test_a_file_that_is_not_there_is_said_rather_than_ignored(self):
        """Two of the installed mods name files they do not ship."""
        report = self.run_mod(main='<Include File="../nowhere.xml" />')
        self.assertEqual(report.results, [])
        said = self.said_about_includes(report)
        self.assertIn("is not there", said[0].detail)
        self.assertIn("nowhere.xml", said[0].what)

    def test_one_the_mod_ships_switched_off_is_not_read(self):
        report = self.run_mod(
            main='<Include File="../../../../../parts/one.xml" Skip="1" />',
            **{'parts|one.xml': self.RAISE},
        )
        self.assertEqual(report.changes, [])
        self.assertIn("switched off", self.said_about_includes(report)[0].detail)

    def test_a_file_that_includes_itself_is_read_once(self):
        report = self.run_mod(
            main='<Include File="../../../../../parts/one.xml" />',
            **{'parts|one.xml': '<Include File="one.xml" />' + self.RAISE},
        )
        self.assertEqual(len(report.changes), 1)
        self.assertIn(
            "already included", self.said_about_includes(report)[0].detail
        )

    def test_something_that_is_not_a_modops_document_is_said(self):
        root = self.mod(main='<Include File="../../../../../notes.txt" />')
        (root / "notes.txt").write_text("hello", encoding="utf-8")
        report = reader.analyse(root, self.index, origin="m")
        self.assertIn(
            "not a ModOps document", self.said_about_includes(report)[0].detail
        )


class TestEachGameReadsTheTypesItsLoaderKnows(ImportCase):
    """Each loader has its own list of types (``ReadType``), and a type not on
    it is an "Unknown ModOp" that does nothing.  Anno 1800 has no short form,
    measured in the game on 4 October 2026; ``Type="append"`` is neither
    game's, and ``AddNextSibling=`` is no short form of either.  Anno 117
    knows ``addAfter`` and ``addBefore`` as the sibling adds."""

    def read_as(self, game: str, body: str) -> modops.OpResult:
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        (result,) = reader.Translator(self.index, True).run_all(ops)
        return result

    def test_what_the_game_passes_by_is_carried_and_changes_nothing(self):
        radius = "<Radius>77</Radius>"
        cases = (
            (games.ANNO_1800, '<ModOp Merge="@1000/Building">%s</ModOp>' % radius),
            (games.ANNO_1800, '<ModOp Replace="@1000/Building/Radius">%s</ModOp>' % radius),
            (games.ANNO_117, '<ModOp AddNextSibling="@1000/Building/Radius">%s</ModOp>' % radius),
            (games.ANNO_117, '<ModOp Type="append" GUID="1000" Path="/Values/Building/Radius">'
                             '%s</ModOp>' % radius),
            (games.ANNO_1800, '<ModOp Type="prepend" GUID="1000" Path="/Values/Building/Radius">'
                              '%s</ModOp>' % radius),
            (games.ANNO_1800, '<ModOp Type="addAfter" GUID="1000" Path="/Values/Building/Radius">'
                              '%s</ModOp>' % radius),
        )
        for game, body in cases:
            with self.subTest(game=game, body=body):
                result = self.read_as(game, body)
                self.assertEqual(result.status, modops.BLOCKED)
                self.assertEqual(result.changes, [])
                self.assertIn("the game passes it by", result.reason)

    def test_while_anno_117_reads_the_short_form_and_its_own_sibling_adds(self):
        merged = self.read_as(games.ANNO_117, '<ModOp Merge="@1000/Building"><Radius>77</Radius></ModOp>')
        self.assertEqual([c.new_value for c in merged.changes], ["77"])
        for written, kind in (("addAfter", OP_ADD_NEXT), ("ADDBEFORE", OP_ADD_PREV)):
            with self.subTest(type=written):
                result = self.read_as(
                    games.ANNO_117,
                    '<ModOp Type="%s" GUID="1000" Path="/Values/Building/Radius">'
                    "<Extra>1</Extra></ModOp>" % written,
                )
                self.assertEqual(result.op.kind, kind)
                self.assertNotIn("passes it by", result.reason)

    def test_and_a_type_the_game_knows_and_the_studio_does_not_says_so(self):
        result = self.read_as(games.ANNO_117, '<ModOp Type="assets"><Asset/></ModOp>')
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("not an op type the Studio reads", result.reason)


class TestOpsAModShipsSwitchedOff(ImportCase):
    """``Skip="1"`` means the op does not run.

    Mods built for the Imya tweak tool ship them in bulk -- 609 across seven of
    the mods installed here -- present in the file and off until something turns
    them on.  Reading them as things the mod does showed changes the game never
    makes.
    """

    def test_a_skipped_op_is_not_something_the_mod_does(self):
        result = self.only(
            '<ModOp Merge="@1000/Building" Skip="1"><Radius>77</Radius></ModOp>'
        )
        self.assertEqual(result.status, modops.SETTLED)
        self.assertEqual(result.changes, [])
        self.assertIn("switched off", result.reason)

    def test_but_it_is_something_the_mod_ships_so_it_is_kept(self):
        """Off until a tweak tool turns it on -- which it cannot do if the op
        is not in the file any more.  A kept op with nothing hanging off it."""
        result = self.only(
            '<ModOp Merge="@1000/Building" Skip="1"><Radius>77</Radius></ModOp>'
        )
        self.assertIsNotNone(result.record)
        self.assertIn("Skip", result.record.kept)
        self.assertEqual(result.record.matched, ["1000"])
        self.assertIn('Skip="1"', result.record.source)

    def test_any_skip_at_all_switches_it_off(self):
        """The loaders ask whether ``Skip`` is there, not what it says, as
        they do of a group: ``Skip="0"`` ran in the Studio and not in the
        game."""
        for value in ("1", "0", "false", ""):
            with self.subTest(skip=value):
                result = self.only(
                    '<ModOp Merge="@1000/Building" Skip="%s"><Radius>77</Radius></ModOp>' % value
                )
                self.assertEqual(result.changes, [])
                self.assertIn("switched off", result.reason)

    def test_while_the_same_op_without_it_runs(self):
        self.assertEqual(
            [c.new_value for c in self.imported(
                '<ModOp Merge="@1000/Building"><Radius>77</Radius></ModOp>'
            )],
            ["77"],
        )

    def test_and_the_attributes_a_tweak_tool_adds_are_not_reported_as_ignored(self):
        result = self.only(
            '<ModOp Merge="@1000/Building" ModOpID="radius"><Radius>77</Radius></ModOp>'
        )
        self.assertNotIn("ignored", result.reason)


class TestModsBundledInsideMods(SessionCase):
    """A mod carrying another mod inside it is two mods, and the game knows it.

    Real ones do this constantly -- one Anno 1800 mod ships twenty-six bundled
    mods, mostly shared pieces several mods depend on -- and the loader finds
    each of them and loads it in its own right, at its own place in load order,
    routinely nowhere near its host's.  Read as part of the host, they land in
    the host's file when it is written back, while their own folder goes on
    being loaded: everything they add would be added twice.
    """

    OPS = '<ModOp Merge="@1000/Building"><Radius>%s</Radius></ModOp>'

    def bundle(self, host="Host", inside="bits", host_ops=True,
               mod_id="bits", version="1.0"):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        if host_ops:
            patch = root / host / "data/base/config/export/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(modops_file(self.OPS % 11), encoding="utf-8")
        (root / host).mkdir(parents=True, exist_ok=True)
        (root / host / "modinfo.json").write_text(
            '{"ModID": "host", "ModName": {"English": "Host"}}', encoding="utf-8"
        )
        nested = root / host / inside
        patch = nested / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(modops_file(self.OPS % 22), encoding="utf-8")
        (nested / "modinfo.json").write_text(
            '{"ModID": "%s", "Version": "%s", "ModName": {"English": "Bits"}}'
            % (mod_id, version),
            encoding="utf-8",
        )
        return root

    def read(self, root, **kwargs):
        return reader.analyse_all(
            modfolder.mod_folders(root), self.index, roots=[root], **kwargs
        )

    def test_it_is_read_as_a_mod_of_its_own(self):
        session = self.read(self.bundle())
        self.assertEqual(
            sorted(read.origin for read in session.reads), ["Host", "Host/bits"]
        )

    def test_named_by_where_it_lives_so_two_copies_are_two_names(self):
        """The same bundled mod turns up inside several hosts -- nine copies of
        one shared piece in a real folder -- so a bare name would not say which
        copy this is."""
        session = self.read(self.bundle())
        self.assertIn("Host/bits", [read.origin for read in session.reads])

    def test_its_ops_are_not_also_the_host_s(self):
        """Which is the whole bug: folded into the host they would be written
        into the host's file and go on being loaded from their own folder too."""
        session = self.read(self.bundle())
        by_origin = {read.origin: read.changes for read in session.reads}
        self.assertEqual([c.new_value for c in by_origin["Host"]], ["11"])
        self.assertEqual([c.new_value for c in by_origin["Host/bits"]], ["22"])

    def test_a_host_that_only_carries_mods_has_nothing_of_its_own(self):
        """It has a modinfo, so the game loads it -- as a mod that changes
        nothing -- and the loadout has to know of it, or the Start tab would
        count it as a stranger for ever.  A box with no modinfo is not a mod
        and is skipped as before."""
        session = self.read(self.bundle(host_ops=False))
        self.assertEqual(sorted(read.origin for read in session.reads), ["Host", "Host/bits"])
        by_origin = {read.origin: read for read in session.reads}
        self.assertEqual(by_origin["Host"].changes, [])
        self.assertEqual(by_origin["Host"].ops, 0)
        self.assertEqual(session.skipped, [])
        root = self.bundle(host_ops=False)
        (root / "Host" / "modinfo.json").unlink()
        session = self.read(root)
        self.assertEqual([read.origin for read in session.reads], ["Host/bits"])
        self.assertEqual(len(session.skipped), 1)

    def test_it_gets_its_own_place_in_load_order(self):
        session = self.read(self.bundle(), logged=["Host/bits", "Host"])
        self.assertEqual(
            [read.origin for read in session.reads], ["Host/bits", "Host"]
        )

    def test_it_is_written_back_into_its_own_folder(self):
        session = self.read(self.bundle())
        loadout = Loadout(game="anno117")
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.origin))
            for change in read.changes:
                loadout.set_change(change)
        self.assertEqual(
            generator.folder_for(loadout, "Host/bits"), "Host/bits"
        )
        with tempfile.TemporaryDirectory() as out:
            view = generator.for_mod(loadout, "Host/bits")
            written = generator.write_mod_folder(
                view, Path(out), generator.build_modops_xml(view),
                folder_name=generator.folder_for(loadout, "Host/bits"),
            )
            self.assertEqual(written, Path(out) / "Host" / "bits")
            self.assertIn(
                "22",
                (written / "data/base/config/export/assets.xml").read_text(
                    encoding="utf-8"
                ),
            )

    # -- the same mod bundled twice ----------------------------------------
    def twice(self, first="1.0", second="2.0"):
        root = self.bundle(
            host="A Host", inside="shared", mod_id="shared", version=first
        )
        other = root / "B Host" / "shared"
        patch = other / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(modops_file(self.OPS % 33), encoding="utf-8")
        (other / "modinfo.json").write_text(
            '{"ModID": "shared", "Version": "%s"}' % second, encoding="utf-8"
        )
        (root / "B Host" / "modinfo.json").write_text(
            '{"ModID": "b-host"}', encoding="utf-8"
        )
        return root

    def test_only_one_copy_of_a_mod_is_loaded(self):
        """The loader's log had a hundred and eighteen mods and a hundred and
        eighteen distinct ModIDs, while nine folders on disk declared one of
        them.  Reading all nine applies that mod nine times."""
        session = self.read(self.twice())
        origins = [read.origin for read in session.reads]
        self.assertEqual(
            sum(1 for origin in origins if origin.endswith("/shared")), 1
        )
        self.assertEqual(len(session.duplicates), 1)

    def test_two_mod_ids_differing_only_in_case_are_two_mods(self):
        """P8-30: the loader keys its copies by the ModID as written. No mod in
        the census or on this machine differs from another only in case, so
        this is the loader's source read, not a case seen."""
        root = self.twice()
        (root / "B Host" / "shared" / "modinfo.json").write_text(
            '{"ModID": "Shared", "Version": "2.0"}', encoding="utf-8"
        )
        session = self.read(root)
        origins = [read.origin for read in session.reads]
        self.assertEqual(sum(1 for origin in origins if origin.endswith("/shared")), 2)
        self.assertEqual(session.duplicates, [])

    def test_and_it_is_the_newest_one(self):
        """Checked against every real case where two copies differed: the
        highest version is the one the loader used, every time."""
        session = self.read(self.twice(first="1.0", second="2.0"))
        kept = [o for o in (r.origin for r in session.reads) if o.endswith("/shared")]
        self.assertEqual(kept, ["B Host/shared"])
        session = self.read(self.twice(first="10.5.1", second="8.8.2"))
        kept = [o for o in (r.origin for r in session.reads) if o.endswith("/shared")]
        self.assertEqual(kept, ["A Host/shared"])

    def test_unless_the_loader_s_log_says_which_it_used(self):
        session = self.read(self.twice(first="1.0", second="2.0"),
                            logged=["A Host/shared"])
        kept = [o for o in (r.origin for r in session.reads) if o.endswith("/shared")]
        self.assertEqual(kept, ["A Host/shared"])

    def test_between_equals_the_last_the_loader_comes_to(self):
        """The loader's AddMod keeps a copy unless the one it holds is newer.
        Against the game's own log that named the copy in every ModID
        installed twice; the first one found, which this used to keep, was
        wrong about five of them."""
        session = self.read(self.twice(first="1.0", second="1.0"))
        kept = [o for o in (r.origin for r in session.reads) if o.endswith("/shared")]
        self.assertEqual(kept, ["B Host/shared"])

    def test_and_a_copy_switched_off_is_not_one_it_comes_to(self):
        root = self.twice(first="1.0", second="2.0")
        (root / "B Host").rename(root / "-B Host")
        session = self.read(root)
        kept = [o for o in (r.origin for r in session.reads) if o.endswith("/shared")]
        self.assertEqual(kept, ["A Host/shared"])

    def test_a_patch_for_a_bundled_mod_stands_outside_its_host(self):
        """Put inside the host's folder, updating the host would replace the
        folder and take the patch with it -- the whole thing a patch avoids."""
        self.assertEqual(patching.suggest_name("Host/bits"), "bits (my patch)")
        self.assertTrue(patching.loads_after("bits (my patch)", "Host/bits"))

    def test_a_patch_s_folder_is_compared_as_the_loader_compares_it(self):
        """As written: capitals come before ``[``. Lower-cased, ``Zed`` passed
        for loading after ``[Gameplay] Theirs`` and loads in front of it."""
        self.assertFalse(patching.loads_after("Zed patch", "[Gameplay] Theirs"))
        self.assertTrue(patching.loads_after("zed patch", "[Gameplay] Theirs"))
        self.assertTrue(patching.loads_after("[Gameplay] Theirs (my patch)", "[Gameplay] Theirs"))
        # Anno 117 does not sort by folder, so none is the wrong one.
        self.assertTrue(patching.loads_after("Zed patch", "[Gameplay] Theirs", games.ANNO_117))

    def test_a_renamed_patch_that_sorts_before_its_mod_is_noticed(self):
        """Anno 117 sorts by category and name, which the Export tab renames;
        Anno 1800 by the folder, which it does not."""
        for game, moved in ((games.ANNO_117, True), (games.ANNO_1800, False)):
            loadout = Loadout(game=game)
            loadout.set_layer(ModLayer(
                origin="[Gameplay] Theirs", name="Theirs", category="Gameplay", mod_id="theirs",
            ))
            patch = patching.make(loadout, "[Gameplay] Theirs")
            self.assertFalse(patching.sorts_before_its_mod(loadout, patch), game)
            patch.name = "A fix"
            self.assertEqual(patching.sorts_before_its_mod(loadout, patch), moved, game)
            patch.name = "Theirs (my patch)"
            patch.category = "Addon"
            self.assertEqual(patching.sorts_before_its_mod(loadout, patch), moved, game)
            # Not a patch, or its mod gone: nothing to be in front of.
            self.assertFalse(patching.sorts_before_its_mod(loadout, loadout.layer("[Gameplay] Theirs")))
            loadout.drop_layer("[Gameplay] Theirs")
            self.assertFalse(patching.sorts_before_its_mod(loadout, patch), game)

    def test_and_the_copy_passed_over_is_reported_rather_than_dropped(self):
        session = self.read(self.twice())
        folder, instead = session.duplicates[0]
        self.assertEqual(folder.name, "shared")
        self.assertTrue(instead.endswith("/shared"))
        self.assertIn("bundled copy", session.summary())


class TestWhichGameAPatchIsFor(unittest.TestCase):
    """A mod's own folder shape says which game it targets, independent of
    anything it claims about itself -- see the wrong-game import guards in
    ModImportDialog and ModsFolderDialog."""

    def test_1800_s_shape(self):
        self.assertEqual(
            modops.game_for_patch(
                Path("data/config/export/main/asset/assets.xml")
            ),
            games.ANNO_1800,
        )

    def test_117_s_shape(self):
        self.assertEqual(
            modops.game_for_patch(Path("data/base/config/export/assets.xml")),
            games.ANNO_117,
        )

    def test_the_shape_can_sit_deeper_in_a_bundled_mod(self):
        self.assertEqual(
            modops.game_for_patch(
                Path("bundled/sub/data/base/config/export/assets.xml")
            ),
            games.ANNO_117,
        )

    def test_a_shape_neither_game_uses_says_nothing(self):
        self.assertIsNone(modops.game_for_patch(Path("assets.xml")))

    def test_a_report_with_no_patch_files_has_no_hint(self):
        report = modops.ImportReport()
        self.assertIsNone(report.game_hint)

    def test_a_report_whose_patches_disagree_has_no_hint(self):
        """Real mods do not mix the two conventions; this is "I do not know",
        not a guess at which one wins."""
        report = modops.ImportReport(
            root=Path("/mods/x"),
            patch_files=[
                Path("/mods/x/data/config/export/main/asset/assets.xml"),
                Path("/mods/x/bundled/data/base/config/export/assets.xml"),
            ],
        )
        self.assertIsNone(report.game_hint)

    def test_a_loose_file_with_no_folder_context_has_no_hint(self):
        """It sits nowhere in particular, so nothing here can be a guess."""
        report = modops.ImportReport(
            root=Path("/tmp/assets.xml"), patch_files=[Path("/tmp/assets.xml")]
        )
        self.assertIsNone(report.game_hint)


class TestAMergeOfSameNamedEntriesGoesByPosition(ImportCase):
    """The second ``<Item>`` of a merge's body goes into the second ``<Item>``
    there, whatever either holds, and one past the end is added.

    Measured with both games' loaders (``tests/test_modio_loader.py`` pins it),
    because it is not what anybody would guess, and it was not what the Studio
    read: it paired entries by what they held.  *Noblesse Oblige*'s four new
    stable fields were four additions where the game renames the four that are
    there, and *Adds Goldmine To 2 Specialist Pools* had its tiers reversed.
    Every expectation below is what the loader produces.
    """

    def replayed(self, changes, where="Values/ProductList/List"):
        tree = self.index.asset_element("1000")
        for change in emit_order(list(changes)):
            recording.replay(tree, change)
        return [
            ",".join((field.text or "").strip() for field in item)
            for item in tree.find(where)
        ]

    def merged(self, path, body):
        return self.imported('<ModOp Type="merge" GUID="1000" Path="%s">%s</ModOp>' % (path, body))

    def items(self, *products):
        return "".join("<Item><Product>%s</Product></Item>" % product for product in products)

    def test_new_entries_write_over_the_first_ones(self):
        changes = self.merged("/Values/ProductList/List", self.items("9001", "9002"))
        self.assertEqual(self.replayed(changes), ["9001", "9002", "2003"])

    def test_one_that_is_already_in_the_list_still_writes_over_the_first(self):
        """It used to read as already true, and the game makes 2002 twice."""
        changes = self.merged("/Values/ProductList/List", self.items("2002"))
        self.assertEqual(self.replayed(changes), ["2002", "2002", "2003"])

    def test_entries_past_the_end_are_added(self):
        changes = self.merged("/Values/ProductList/List", self.items("9001", "9002", "9003", "9004"))
        self.assertEqual(self.replayed(changes), ["9001", "9002", "9003", "9004"])

    def test_the_same_one_level_up(self):
        changes = self.merged("/Values/ProductList", "<List>%s</List>" % self.items("9001"))
        self.assertEqual(self.replayed(changes), ["9001", "2002", "2003"])

    def test_a_field_of_each_entry_in_turn(self):
        changes = self.merged(
            "/Values/Cost/Costs",
            "<Item><Amount>50</Amount></Item><Item><Amount>80</Amount></Item>",
        )
        self.assertEqual([change.op for change in changes], [OP_REPLACE, OP_REPLACE])
        self.assertEqual(self.replayed(changes, "Values/Cost/Costs"), ["1010,50", "1011,80"])

    def test_an_entry_that_names_the_second_still_lands_on_the_first(self):
        changes = self.merged(
            "/Values/Cost/Costs", "<Item><Ingredient>1011</Ingredient><Amount>80</Amount></Item>"
        )
        self.assertEqual(self.replayed(changes, "Values/Cost/Costs"), ["1011,80", "1011,8"])

    def test_fields_with_names_of_their_own_are_read_as_they_always_were(self):
        changes = self.merged("/Values/Building", "<Radius>30</Radius><New>1</New>")
        self.assertEqual(sorted(change.op for change in changes), [OP_MERGE, OP_REPLACE])


class TestAReplayGoesByTheRulesOfTheModsGame(ImportCase):
    """Anno 1800's loader compares names as written: ``<radius>`` merged in
    beside ``<Radius>`` is a field of its own.  The reading said so, and the
    replay that checks it merged by Anno 117's rules, set the one there, and
    put a caveat on an op read right."""

    MERGE = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><radius>30</radius></ModOp>'

    def read(self, body, game):
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml", game=game)
        report.results = reader.Translator(self.index, True).run_all(ops)
        return report

    def test_an_anno_1800_merge_replays_as_it_was_read(self):
        (result,) = self.read(self.MERGE, games.ANNO_1800).results
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        self.assertNotIn("replayed as written", result.record.caveat or "")
        tree = self.index.asset_element("1000")
        for change in result.changes:
            recording.replay(tree, change, games.ANNO_1800)
        self.assertEqual(
            [(node.tag, node.text) for node in tree.find("Values/Building")],
            [("BuildingType", "Factory"), ("Radius", "10"), ("radius", "30")],
        )


class TestAReplaceKeepsTheListInItsOrder(ImportCase):
    """A wide replace says what the list is, order included.

    *[Hadleys] StartInseln* retypes whole fertility sets: new entries between
    the shipped ones, and in one set the shipped ones in a different order. The
    Studio appended every new entry, so what it showed -- and what the narrow
    export did -- was a list the mod never wrote.
    """

    def replayed(self, changes):
        tree = self.index.asset_element("1000")
        for change in emit_order(list(changes)):
            recording.replay(tree, change)
        return [node.text for node in tree.iterfind("Values/ProductList/List/Item/Product")]

    def replace_list(self, products):
        return self.imported(
            '<ModOp Type="replace" GUID="1000" Path="/Values/ProductList/List"><List>%s</List></ModOp>'
            % "".join("<Item><Product>%s</Product></Item>" % product for product in products)
        )

    def test_new_entries_go_between_the_ones_it_keeps(self):
        wanted = ["2001", "2009", "2008", "2002", "2003"]
        changes = self.replace_list(wanted)
        self.assertEqual([change.op for change in changes], [OP_ADD_NEXT])
        self.assertEqual(self.replayed(changes), wanted)

    def test_one_that_leads_the_list_goes_before_the_first_it_keeps(self):
        wanted = ["2009", "2001", "2002", "2003"]
        changes = self.replace_list(wanted)
        self.assertEqual([change.op for change in changes], [OP_ADD_PREV])
        self.assertEqual(self.replayed(changes), wanted)

    def test_ones_that_end_the_list_are_still_appended_one_by_one(self):
        wanted = ["2001", "2002", "2003", "2008", "2009"]
        changes = self.replace_list(wanted)
        self.assertEqual([change.op for change in changes], [OP_ADD, OP_ADD])
        self.assertEqual(self.replayed(changes), wanted)

    def test_and_ones_dropped_from_the_end_do_not_stand_in_the_way(self):
        wanted = ["2001", "2002", "2009"]
        changes = self.replace_list(wanted)
        self.assertEqual(sorted(change.op for change in changes), [OP_ADD, OP_REMOVE])
        self.assertEqual(self.replayed(changes), wanted)

    def test_entries_it_keeps_in_a_new_order_are_moved(self):
        """No narrow op moves an entry, so the one out of place is taken out and
        put back -- not the whole list rewritten, which needs a neighbour
        outside the list to hang it off, and that neighbour may be going too."""
        wanted = ["2003", "2001", "2009", "2002"]
        changes = self.replace_list(wanted)
        self.assertEqual(self.replayed(changes), wanted)
        self.assertEqual([c.op for c in changes].count(OP_REMOVE), 1)
        self.assertNotIn("/Values/ProductList/List", [c.path for c in changes])

    def test_an_entry_placed_beside_one_it_also_edits_is_found_after_the_edit(self):
        """*Free Ornaments* takes the Ingredient out of cost entries and inserts
        between them; placed first, the insertion was keyed on the Ingredient
        its own removal had already taken away."""
        changes = self.imported(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Cost/Costs"><Costs>'
            "<Item><Amount>5</Amount></Item>"
            "<Item><Ingredient>1012</Ingredient><Amount>1</Amount></Item>"
            "<Item><Amount>8</Amount></Item>"
            "</Costs></ModOp>"
        )
        tree = self.index.asset_element("1000")
        for change in emit_order(list(changes)):
            recording.replay(tree, change)
        self.assertEqual(
            [
                (item.findtext("Ingredient"), item.findtext("Amount"))
                for item in tree.iterfind("Values/Cost/Costs/Item")
            ],
            [(None, "5"), ("1012", "1"), (None, "8")],
        )


class TestAnAssetsOwnGuidIsNotAField(ImportCase):
    """*Industrial Cities* swaps two assets' GUIDs through a temporary "swap".

    Every change is filed under its asset's GUID, so read as a plain value the
    rename left the Studio showing both assets where they were -- while the
    export renamed them, and the game lost one.
    """

    def test_changing_it_is_left_unread_and_says_why(self):
        result = self.only(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Standard/GUID"><GUID>swap</GUID></ModOp>'
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("own GUID", result.reason)
        self.assertEqual(result.changes, [])

    def test_a_replace_that_writes_the_same_guid_back_is_read_as_usual(self):
        changes = self.imported(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Standard">'
            "<Standard><GUID>1000</GUID><Name>renamed</Name></Standard></ModOp>"
        )
        self.assertEqual([(change.op, change.new_value) for change in changes], [(OP_REPLACE, "renamed")])


class TestReadingSomethingDamaged(unittest.TestCase):
    """Mod XML and mod paths, neither of which we wrote.

    A kept op holds its ModOp as text and reads it back; a search op carries an XPath
    somebody typed. Both can be unreadable, and the reader's job is to say so
    by returning nothing rather than by raising out of the middle of an import.
    """

    def test_an_op_that_is_not_xml_reads_as_no_op(self):
        for text in ("<ModOp Type=", "<ModOp></ModOps>", "<", ""):
            with self.subTest(text=text):
                self.assertIsNone(modops.read_op(text))

    def test_an_op_that_is_xml_but_not_a_modop_reads_as_no_op(self):
        self.assertIsNone(modops.read_op("<Asset><Values/></Asset>"))

    def test_a_search_that_will_not_evaluate_matches_nothing(self):
        """Compiles, then fails on the document: what an undefined variable does."""
        asset = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>"
        )
        self.assertEqual(modops.search_in(asset, "//Asset[$missing]"), [])

    def test_a_search_that_will_not_compile_matches_nothing(self):
        asset = etree.fromstring(b"<Asset><Values/></Asset>")
        for path in ("//Asset[", "(((", "//a b c"):
            with self.subTest(path=path):
                self.assertEqual(modops.search_in(asset, path), [])

    def test_a_search_that_answers_yes_or_a_number_finds_nothing_to_change(self):
        asset = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>"
        )
        for path in ("//Missing or 1", "count(//Values)", "count(//Missing) = 0", "string(//GUID)"):
            with self.subTest(path=path):
                self.assertEqual(modops.search_in(asset, path), [])

    def test_a_target_path_that_will_not_evaluate_resolves_to_nothing(self):
        """Tried with and without the /Values prefix; neither may raise."""
        asset = etree.fromstring(b"<Asset><Values><A>1</A></Values></Asset>")
        for path in ("/A[", "/Values/A[", "((("):
            with self.subTest(path=path):
                self.assertEqual(reader._resolve(asset, path), [])

    def test_a_slider_whose_bounds_are_not_numbers_offers_no_range(self):
        """The mod declares these; a slider with words for bounds is not one."""
        from anno_modding_studio.core import settings as settings_mod

        for values in (("low", "high"), ("1", "ten"), ("", "5")):
            with self.subTest(values=values):
                option = settings_mod.Option(
                    name="size", kind=settings_mod.SLIDER, values=values
                )
                self.assertIsNone(option.range)
        # And the shape it is meant for still answers.
        good = settings_mod.Option(
            name="size", kind=settings_mod.SLIDER, values=("1", "10", "2")
        )
        self.assertEqual(good.range, (1.0, 10.0, 2.0))


class TestACopyOfSeveralNodes(SessionCase):
    """A ``Content=`` copy that matches several nodes copies all of them, in
    the order found, each into its own copy of a body that marks a place for
    it -- both loaders, measured. It was refused, and *General Lifestyle*,
    which moves three needs up a list by copying them in ahead and removing
    the second of each, went out with every need twice."""

    loaded = _KeptOps.loaded
    written = _KeptOps.written

    def products(self, loadout, guid="1000"):
        shown = Workspace(self.index, loadout).effective_asset(guid).as_the_game_reads_it()
        return [
            item.findtext("Product") for item in shown.findall("Values/ProductList/List/Item")
            if not item.get("removed")
        ]

    MOVE = (
        """<ModOp Type="addPrevSibling" GUID="1000" Path="/Values/ProductList/List/Item[Product='2001']" """
        """Content="@1000/ProductList/List/Item[Product='2002' or Product='2003']"></ModOp>"""
    )

    def test_every_node_it_matches_is_copied_in_order(self):
        loadout = self.loaded(self.mods(a__mod=self.MOVE))
        self.assertEqual(self.products(loadout), ["2002", "2003", "2001", "2002", "2003"])

    def test_and_a_twin_is_removed_by_which_of_them_it_is(self):
        """Nothing tells a copy from what it copied, so the removal names its
        position, as the mod does; keyed by the product alone it named both,
        and the Studio showed neither gone."""
        remove = """<ModOp Type="remove" GUID="1000" Path="/Values/ProductList/List/Item[Product='2002'][2]" />"""
        loadout = self.loaded(self.mods(a__mod=self.MOVE + remove))
        (removal,) = [change for change in loadout if change.op == OP_REMOVE]
        self.assertTrue(removal.path.endswith("[2]"), removal.path)
        self.assertEqual(self.products(loadout), ["2002", "2003", "2001", "2003"])

    def test_a_body_is_repeated_for_each(self):
        body = (
            '<ModOp Type="add" GUID="1001" Path="/Values/Building" '
            'Content="@1002/Cost/Costs/Item/Ingredient/text()"><Copied><ModOpContent/></Copied></ModOp>'
        )
        loadout = self.loaded(self.mods(a__mod=body))
        shown = Workspace(self.index, loadout).effective_asset("1001").as_the_game_reads_it()
        self.assertEqual([node.text for node in shown.findall("Values/Building/Copied")],
                         ["1010", "1011", "1012", "1013"])


class TestASearchByPlaceAmongAssets(ImportCase):
    def test_is_not_read_as_matching_nothing(self):
        """Each asset is searched as a document of its own, where it stands
        alone. *A_Modified_Ornaments_Tab* removes duplicates of its own assets
        with ``[position() < last()]``; read as matching nothing, the Studio
        showed the placeholder the game has removed."""
        result = self.only(
            '<ModOp Type="remove" '
            """Path="//Asset[Values/Standard/GUID='1000'][position() &lt; last()]" />"""
        )
        self.assertEqual(result.status, modops.BLOCKED)
        self.assertIn("where they stand among others", result.reason)


class TestARemovalAfterAnOpNotRead(ImportCase):
    def test_is_not_dropped_as_already_done(self):
        """What it removes may be there in game: the op the Studio could not
        read may have put it there. Dropped, it went missing from the export."""
        report = self.read(
            '<ModOp Type="frobnicate" GUID="1001" Path="/Values/Building"><Extra/></ModOp>'
            '<ModOp Type="remove" GUID="1001" Path="/Values/Building/Extra" />'
            '<ModOp Type="remove" GUID="1000" Path="/Values/Building/Extra" />'
        )
        statuses = [result.status for result in report.results]
        self.assertEqual(statuses, [modops.BLOCKED, modops.BLOCKED, modops.SETTLED])


if __name__ == "__main__":
    unittest.main()



def _plain(guid, name="", extra=""):
    return ("<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID>"
            "<Name>%s</Name></Standard>%s</Values></Asset>" % (guid, name or "asset %s" % guid, extra))


class TestAGuidInTwoOfTheFilesGroups(SessionCase):
    """Of two assets sharing a GUID the loader finds the first in the file only
    where both are in one of the file's top-level groups: its look-up starts
    with the top-level group its last look-up landed in.  Measured in both
    games with a probe mod, five cases each: with the copy in a later group
    than the shipped asset the op after it edited the copy, and with the copy
    in an earlier group an op after one aimed elsewhere edited the shipped one.
    The Studio read both as the first in the file.  Which group the last
    look-up landed in is not worked out, so such an op is carried as written.
    """

    # Three top-level groups: 100 in the first; 200 and 201 in the second, a
    # group down as the shipped files have them; 300 in the third.
    GROUPED = (
        "<AssetList><Groups>"
        "<Group><Assets>%s</Assets></Group>"
        "<Group><Groups><Group><Assets>%s%s</Assets></Group></Groups></Group>"
        "<Group><Assets>%s</Assets></Group>"
        "</Groups></AssetList>"
    ) % (_plain("100"), _plain("200", "shipped"), _plain("201"), _plain("300"))
    EDIT = '<ModOp Merge="@200/Standard"><Name>renamed</Name></ModOp>'

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(cls.GROUPED, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @staticmethod
    def copy(beside, guid="200", kind="addNextSibling"):
        return '<ModOp Type="%s" GUID="%s">%s</ModOp>' % (kind, beside, _plain(guid, "the copy"))

    def loaded(self, root):
        session = self.session(root)
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.name, order=read.order))
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread.extend(read.report.unread(read.origin))
        return loadout

    def written(self, loadout):
        return generator.build_modops_xml(generator.for_mod(loadout, "a mod"))

    def test_the_index_knows_each_assets_top_level_group(self):
        self.assertEqual([self.index.top_group(guid) for guid in ("100", "200", "201", "300")],
                         [0, 1, 1, 2])
        self.assertIsNone(self.index.top_group("999"))

    def test_an_op_aimed_at_it_is_carried_as_written(self):
        for name, beside in (("the copy in a later group", "300"), ("in an earlier one", "100")):
            with self.subTest(name):
                loadout = self.loaded(self.mods(a__mod=self.copy(beside) + self.EDIT))
                (unread,) = loadout.unread
                self.assertIn("twice", unread.why)
                self.assertIn("200", unread.why)
                # The copy is still made, and the op goes out once, after it.
                written = self.written(loadout)
                self.assertEqual(written.count("the copy"), 1)
                self.assertEqual(written.count("renamed"), 1)
                self.assertLess(written.index("the copy"), written.index("renamed"))

    def test_in_one_group_it_is_the_first_in_the_file_as_before(self):
        loadout = self.loaded(self.mods(a__mod=self.copy("201") + self.EDIT))
        self.assertEqual(loadout.unread, [])
        (edit,) = [change for change in loadout if not change.creates_asset]
        self.assertEqual((edit.guid, edit.old_value, edit.new_value), ("200", "shipped", "renamed"))

    def test_an_op_before_the_copy_is_made_is_read(self):
        loadout = self.loaded(self.mods(a__mod=self.EDIT + self.copy("300")))
        self.assertEqual(loadout.unread, [])
        self.assertEqual(len([change for change in loadout if not change.creates_asset]), 1)

    def test_two_copies_a_mod_makes_in_two_groups_are_apart_as_well(self):
        made = self.copy("100", guid="7777") + self.copy("300", guid="7777")
        edit = '<ModOp Merge="@7777/Standard"><Name>renamed</Name></ModOp>'
        loadout = self.loaded(self.mods(a__mod=made + edit))
        self.assertEqual(len(loadout.unread), 1)
        # And beside one asset they are in one group, the nearer one first.
        made = self.copy("300", guid="7777") + self.copy("300", guid="7777")
        self.assertEqual(self.loaded(self.mods(a__mod=made + edit)).unread, [])

    def test_an_asset_put_beside_a_copy_is_in_the_copys_group(self):
        """7777 is made beside 300, in the third group; a copy of 200 put
        beside 7777 is in the third group too, and 200 ships in the second."""
        made = self.copy("300", guid="7777") + self.copy("7777")
        loadout = self.loaded(self.mods(a__mod=made + self.EDIT))
        self.assertEqual(len(loadout.unread), 1)

    def test_a_bare_asset_is_at_the_end_wherever_its_guid_ships(self):
        """An ``<Asset>`` straight under ``<ModOps>`` goes to the end of the
        file, and the game adds it there whatever the last group holds
        (measured in Anno 117); that is not known to be the shipped asset's
        group, so it is not said to be.  Two of them are in one place."""
        bare = _plain("200", "the copy")
        loadout = self.loaded(self.mods(a__mod=bare + self.EDIT))
        self.assertEqual(len(loadout.unread), 1)
        twice = _plain("7777", "one") + _plain("7777", "two")
        edit = '<ModOp Merge="@7777/Standard"><Name>renamed</Name></ModOp>'
        self.assertEqual(self.loaded(self.mods(a__mod=twice + edit)).unread, [])
