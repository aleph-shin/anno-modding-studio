"""Readings the game's own loader disagreed with, found by the loader check on
the mods installed on one machine, each pinned where it was put right."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import SessionCase  # noqa: E402


class TestAWholeListReplacedAfterAnotherModAddedToIt(SessionCase):
    """*Vendors only sell common Items* replaces a whole item pool that
    *Military Attention* has already added to. The removal of that added entry
    was written as ``ItemsPool/Item`` -- every item -- because the replace's
    removals of the shipped entries had gone first and it was the last left."""

    def read(self):
        root = self.mods(
            a__adder='<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
                     "<Item><Product>2004</Product></Item></ModOp>",
            b__replacer='<ModOp Type="replace" GUID="1000" Path="/Values/ProductList/List">'
                        "<List><Item><Product>1010</Product></Item></List></ModOp>",
        )
        session = self.session(root)
        return next(read for read in session.reads if read.origin == "b replacer")

    def test_the_added_entry_is_removed_by_its_key(self):
        removals = sorted(c.path for c in self.read().changes if c.op == "remove")
        self.assertIn("/Values/ProductList/List/Item[Product='2004']", removals)
        self.assertNotIn("/Values/ProductList/List/Item", removals)

    def test_and_every_removal_picks_out_one_entry(self):
        for change in self.read().changes:
            if change.op == "remove":
                self.assertTrue(change.path.endswith("]"), change.path)


class TestAMergeMeetsWhatIsThere(unittest.TestCase):
    """A merge always merges into a node of the same name that is there,
    whichever of the two has children -- measured with both loaders' xmltest.
    It used to set a second one beside it when one side had children and the
    other none: *Trade Union Rebalance* merging a ``<FactoryUpgrade>`` into a
    specialist that ships an empty one came out with two."""

    def merged(self, base, want):
        from lxml import etree

        from anno_modding_studio.core import applying

        base = etree.fromstring(base)
        applying.merge(base, list(etree.fromstring(want)))
        return etree.tostring(base, encoding="unicode")

    def test_as_both_loaders_do_it(self):
        self.assertEqual(
            self.merged("<V><Empty/><Texty>5</Texty><Full><Z>2</Z></Full></V>",
                        "<V><Empty><Y>1</Y></Empty><Texty><Y>1</Y></Texty><Full>7</Full></V>"),
            "<V><Empty><Y>1</Y></Empty><Texty>5<Y>1</Y></Texty><Full><Z>2</Z>7</Full></V>",
        )

    def test_an_empty_entry_leaves_its_place_as_it_was(self):
        self.assertEqual(
            self.merged("<C><Item><I>1010</I><A>5</A></Item><Item><I>1011</I><A>8</A></Item></C>",
                        "<C><Item/><Item><I>1011</I><A>9</A></Item></C>"),
            "<C><Item><I>1010</I><A>5</A></Item><Item><I>1011</I><A>9</A></Item></C>",
        )


class TestAReplaceWithSeveralNodes(SessionCase):
    """A replace that puts several nodes in one's place puts them there last
    first, in both games' loaders: X, Y, Z comes out Z, Y, X. *Trade Union
    Rebalance* swaps one effect target for two."""

    def test_they_stand_in_the_loader_s_order(self):
        from anno_modding_studio.core import loadercheck
        from anno_modding_studio.core.workspace import Workspace

        root = self.mods(a__mod='<ModOp Type="replace" GUID="1000" '
                                "Path=\"/Values/ProductList/List/Item[Product='2001']\">"
                                "<Item><Product>9001</Product></Item><Item><Product>9002</Product></Item></ModOp>")
        from anno_modding_studio.core import modfolder

        _session, loadout = loadercheck.read_folders(modfolder.mod_folders(root), [root], self.index, "")
        asset = Workspace(self.index, loadout).effective_asset("1000").as_the_game_reads_it()
        self.assertEqual([item.findtext("Product") for item in asset.find("Values/ProductList/List")],
                         ["9002", "9001", "2002", "2003"])


class TestAnUnreadOpInAGroupSaysWhereItLands(SessionCase):
    """*Trade Union Rebalance* gives six guild house items the GUID 1, inside a
    group a tweak tool names. The Studio will not follow an asset to a new GUID,
    so that op is not read; it is carried, and the loader check is told which
    assets it is aimed at, to tell those assets apart from a reading it
    disagrees with."""

    def test_the_check_is_told_them(self):
        root = self.mods(a__mod='<Group ModOpID="Retire">'
                                '<ModOp Type="merge" GUID="1000,1001" Path="/Values">'
                                "<Standard><GUID>1</GUID></Standard></ModOp>"
                                '<ModOp Type="merge" GUID="1002" Path="/Values/Standard"><Name>kept</Name></ModOp>'
                                "</Group>")
        from anno_modding_studio.core import loadercheck, modfolder

        _session, loadout = loadercheck.read_folders(modfolder.mod_folders(root), [root], self.index, "")
        (unread,) = loadout.unread
        self.assertIn('GUID="1000,1001"', unread.source)
        self.assertEqual(loadercheck.prepare(loadout, "a mod", index=None).carried_guids, frozenset({"1000", "1001"}))


if __name__ == "__main__":
    unittest.main()
