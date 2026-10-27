"""``core/applying.py``: what an op does to the nodes it finds, by its type,
as the loader's own source has it. Run against the loader itself in
``test_modio_loader.TestTheLoadersOwnApply``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anno_modding_studio.core import applying, games


def _applied(kind, base, path, payload, game=games.ANNO_117):
    root = etree.fromstring(base)
    (node,) = root.xpath("." + path) if path else [root]
    warned = applying.apply(kind, node, list(etree.fromstring("<x>%s</x>" % payload)), game)
    return etree.tostring(root, encoding="unicode"), warned


class TestEachType(unittest.TestCase):
    def test_add_appends_in_order(self):
        self.assertEqual(_applied("add", "<L><I>1</I></L>", "", "<I>2</I><I>3</I>")[0],
                         "<L><I>1</I><I>2</I><I>3</I></L>")

    def test_the_siblings_keep_the_payload_s_order(self):
        for kind, path in (("addNextSibling", "/I[1]"), ("addPrevSibling", "/I[2]")):
            with self.subTest(kind=kind):
                self.assertEqual(_applied(kind, "<L><I>1</I><I>2</I></L>", path, "<A/><B/>")[0],
                                 "<L><I>1</I><A/><B/><I>2</I></L>")

    def test_a_replace_puts_several_in_last_first(self):
        self.assertEqual(_applied("replace", "<L><X/><Q/></L>", "/X", "<A/><B/><C/>")[0],
                         "<L><C/><B/><A/><Q/></L>")

    def test_what_is_taken_out_leaves_the_text_after_it(self):
        self.assertEqual(_applied("remove", "<L><X/>words<Q/></L>", "/X", "")[0],
                         "<L>words<Q/></L>")


class TestAMerge(unittest.TestCase):
    """``RecursiveMerge``: by name, and among nodes of one name by position."""

    def merged(self, base, payload, game=games.ANNO_117):
        return _applied("merge", base, "", payload, game)[0]

    def test_as_both_loaders_do_it(self):
        self.assertEqual(
            self.merged("<V><Empty/><Texty>5</Texty><Full><Z>2</Z></Full></V>",
                        "<Empty><Y>1</Y></Empty><Texty><Y>1</Y></Texty><Full>7</Full>"),
            "<V><Empty><Y>1</Y></Empty><Texty>5<Y>1</Y></Texty><Full><Z>2</Z>7</Full></V>",
        )

    def test_the_second_of_a_name_goes_into_the_second(self):
        self.assertEqual(
            self.merged("<C><Item><I>1010</I><A>5</A></Item><Item><I>1011</I><A>8</A></Item></C>",
                        "<Item/><Item><I>1011</I><A>9</A></Item><Item><I>7</I></Item>"),
            "<C><Item><I>1010</I><A>5</A></Item><Item><I>1011</I><A>9</A></Item>"
            "<Item><I>7</I></Item></C>",
        )

    def test_an_empty_field_leaves_the_value_where_it_was(self):
        self.assertEqual(self.merged("<B><Radius>10</Radius></B>", "<Radius/>"),
                         "<B><Radius>10</Radius></B>")

    def test_one_node_named_like_the_target_stands_for_it(self):
        self.assertEqual(
            self.merged("<O><O><A>1</A></O></O>", "<O><B>2</B></O>"),
            "<O><O><A>1</A></O><B>2</B></O>",
        )

    def test_names_in_any_case_in_anno_117_and_as_written_in_anno_1800(self):
        self.assertEqual(self.merged("<B><radius>1</radius></B>", "<Radius>4</Radius>"),
                         "<B><radius>4</radius></B>")
        self.assertEqual(self.merged("<B><radius>1</radius></B>", "<Radius>4</Radius>",
                                     games.ANNO_1800),
                         "<B><radius>1</radius><Radius>4</Radius></B>")

    def test_attributes_are_set_on_the_node_merged_into(self):
        self.assertEqual(self.merged('<B><F a="1">x</F></B>', '<F b="2"/>'),
                         '<B><F a="1" b="2">x</F></B>')


class TestAModItem(unittest.TestCase):
    """Anno 117's ``<ModItem>``, as measured in ``test_modio_loader``."""

    LIST = "<L><Item><P>1</P><A>5</A></Item><Item><P>2</P><A>8</A></Item></L>"

    def merged(self, payload, game=games.ANNO_117):
        return _applied("merge", self.LIST, "", payload, game)

    def test_it_finds_its_entry_by_a_field_or_a_value(self):
        for payload in ('<ModItem Merge="P"><P>2</P><A>9</A></ModItem>',
                        "<ModItem Merge=\"P='2'\"><A>9</A></ModItem>"):
            with self.subTest(payload=payload):
                self.assertEqual(self.merged(payload),
                                 ("<L><Item><P>1</P><A>5</A></Item><Item><P>2</P><A>9</A></Item></L>", []))

    def test_one_it_cannot_find_is_an_item_at_the_end_or_beside_one(self):
        self.assertEqual(self.merged('<ModItem Merge="P"><P>3</P></ModItem>')[0],
                         self.LIST[:-4] + "<Item><P>3</P></Item></L>")
        self.assertEqual(self.merged("<ModItem Merge=\"P\" Prepend=\"P='2'\"><P>3</P></ModItem>")[0],
                         "<L><Item><P>1</P><A>5</A></Item><Item><P>3</P></Item>"
                         "<Item><P>2</P><A>8</A></Item></L>")

    def test_the_two_shapes_that_leave_the_loader_in_a_muddle(self):
        found, _warned = self.merged("<ModItem Merge=\"P\" Append=\"P='1'\"><P>2</P></ModItem>")
        self.assertIn("<Item Append=\"P='1'\"><P>2</P>", found)
        missing, warned = self.merged("<ModItem Merge=\"P\" Append=\"P='9'\"><P>3</P></ModItem>")
        self.assertIn("<ModItem><P>3</P></ModItem>", missing)
        self.assertEqual(warned, [applying.NOT_BESIDE % ("Append", "Item[P='9']")])

    def test_a_plain_entry_after_one_is_passed_by(self):
        found, warned = self.merged('<ModItem Merge="P"><P>2</P></ModItem><Item><P>7</P></Item>')
        self.assertNotIn("7", found)
        self.assertEqual(warned, [applying.NOT_AFTER % "Item"])

    def test_anno_1800_takes_it_for_any_other_node(self):
        self.assertIn("<ModItem Merge=\"P\"><P>3</P></ModItem>",
                      self.merged('<ModItem Merge="P"><P>3</P></ModItem>', games.ANNO_1800)[0])


if __name__ == "__main__":
    unittest.main()
