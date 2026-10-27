"""The numbers in some XML that are GUIDs of assets (``core.guidnames``):
which numbers are found, what field each is in, and which of them the
Namer says are links -- by the rule the Fields view's Refers-to column has.

What the panes draw of them is tested in test_file_tab and test_ui.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402

from anno_modding_studio.core import guidnames  # noqa: E402
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.guidnames import Mention, Namer, mentions  # noqa: E402

TIMBER = "1010196"


def found(text: str) -> list:
    """``(the number as it stands in the text, field, path)`` for each."""
    return [(text[one.start:one.end], one.tag, one.path) for one in mentions(text)]


class TestWhatIsFound(unittest.TestCase):
    def test_a_value_with_the_path_of_the_element_holding_it(self):
        text = (
            "<Asset><Values><Standard><GUID>1010340</GUID></Standard>"
            "<Factory7><FactoryOutputs><Item><Product>1010196</Product>"
            "<Amount>1</Amount></Item></FactoryOutputs></Factory7></Values></Asset>"
        )
        self.assertEqual(found(text), [
            ("1010340", "GUID", "/Values/Standard"),
            ("1010196", "Product", "/Values/Factory7/FactoryOutputs/Item"),
            ("1", "Amount", "/Values/Factory7/FactoryOutputs/Item"),
        ])

    def test_a_value_over_lines_is_found_where_its_number_is(self):
        text = "<Values><Item><Product>\n   1010196\n</Product></Item></Values>"
        (one,) = mentions(text)
        self.assertEqual(text[one.start:one.end], TIMBER)
        self.assertEqual(text.count("\n", 0, one.start), 1)

    def test_what_a_modop_carries_is_where_the_op_aims(self):
        holder = "/Values/Factory7/FactoryOutputs/Item"
        for kind, path in (
            ("add", "/Values/Factory7/FactoryOutputs"),
            ("merge", "//Values/Factory7/FactoryOutputs"),
            # A replace, and an op beside its node, puts it where the node is.
            ("replace", "/Values/Factory7/FactoryOutputs/Item[Product='1']"),
            ("addNextSibling", "/Values/Factory7/FactoryOutputs/Item"),
        ):
            with self.subTest(kind=kind):
                text = (
                    '<ModOp Type="%s" GUID="1010340" Path="%s"><Item>'
                    "<Product>1010196</Product></Item></ModOp>" % (kind, path)
                )
                product = [one for one in mentions(text) if one.tag == "Product"][-1]
                self.assertEqual(product.path, holder)

    def test_an_anno_117_op_in_its_short_form(self):
        text = '<ModOp Add="@1010340/Values/Cost/Costs"><Item><Ingredient>1010196</Ingredient></Item></ModOp>'
        self.assertEqual(found(text), [
            ("1010340", "GUID", ""),
            ("1010196", "Ingredient", "/Values/Cost/Costs/Item"),
        ])

    def test_an_op_aimed_by_a_search_has_no_path_to_give(self):
        text = '<ModOp Type="merge" Path="//Item"><Product>1010196</Product></ModOp>'
        self.assertEqual(found(text), [("1010196", "Product", "")])

    def test_in_attributes_each_guid_named_and_each_compared(self):
        text = (
            '<ModOp Type="replace" GUID="1010340,1010341" '
            "Path=\"/Values/Cost/Costs/Item[Ingredient=1010196]/Amount\" "
            "Condition=\"//Values/Standard[GUID='1010030']\"><Amount>4</Amount></ModOp>"
        )
        self.assertEqual(found(text), [
            ("1010340", "GUID", ""),
            ("1010341", "GUID", ""),
            (TIMBER, "Ingredient", ""),
            ("1010030", "GUID", ""),
            ("4", "Amount", "/Values/Cost/Costs/Item"),
        ])

    def test_comments_and_long_numbers_are_passed_by(self):
        # Past the largest a GUID can be; ten digits under it may be a mod's.
        text = "<Values><!-- 1010196 --><Id>2147483648</Id><Name>Hut 7</Name></Values>"
        self.assertEqual(found(text), [])
        self.assertEqual(found("<Values><Id>2130025000</Id></Values>"), [
            ("2130025000", "Id", "/Values"),
        ])

    def test_half_typed_text_still_has_its_numbers(self):
        self.assertEqual(found("<Values><Item><Product>101019"), [
            ("101019", "Product", "/Values/Item"),
        ])
        self.assertEqual(found('<ModOp Type="add" GUID="1010340'), [])
        self.assertEqual(found('<ModOp GUID="1010340" Path="/Va'), [])


class TestTheNamer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        assets = tmp / "assets.xml"
        assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.index = index_mod.AssetIndex(self.db)
        self.addCleanup(self.index.close)

        class Workspace:
            index = self.index

            def asset_row(self, guid):
                return Workspace.index.asset_row(guid)

        self.namer = Namer(Workspace())

    def name(self, guid: str, tag: str, path: str = ""):
        row = self.namer.row(Mention(0, len(guid), guid, tag, path))
        return None if row is None else guidnames.name_of(row)

    def test_a_link_is_named(self):
        self.assertEqual(self.name(TIMBER, "Product", "/Values/Factory7/FactoryOutputs/Item"), "Timber")
        self.assertEqual(self.name("1010340", "GUID"), "Lumberjack's Hut")

    def test_a_number_that_is_no_link_is_not(self):
        # Amount holds plain numbers wherever the game has it.
        self.assertIsNone(self.name("1010340", "Amount", "/Values/Cost/Costs/Item"))
        # And a number no asset has is nothing.
        self.assertIsNone(self.name("99999999", "GUID"))

    def test_without_a_path_the_field_is_asked_of_across_the_game(self):
        self.assertGreater(self.index.reference_tag("Product")[0], 0)
        self.assertEqual(self.name(TIMBER, "Product"), "Timber")
        self.assertIsNone(self.name("1010340", "Amount"))

    def test_an_assets_own_guid_is_not_named(self):
        self.assertIsNone(self.name("1010340", "GUID", "/Values/Standard"))

    def test_nor_one_it_is_told_to_skip(self):
        self.namer.skip = frozenset({"1010340"})
        self.assertIsNone(self.name("1010340", "GUID"))
        self.assertEqual(self.name(TIMBER, "GUID"), "Timber")

    def test_without_a_workspace_nothing_is(self):
        self.assertIsNone(Namer(None).row(Mention(0, 7, TIMBER, "GUID")))


if __name__ == "__main__":
    unittest.main()
