"""The op counts: that each op lands in the row section 4 would put it in."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import op_counts  # noqa: E402

PATCH_117 = "data/base/config/export/assets.xml"
PATCH_1800 = "data/config/export/main/asset/assets.xml"


def _mod(root: Path, name: str, patch: str, body: str) -> None:
    path = root / name / patch
    path.parent.mkdir(parents=True)
    path.write_text("<ModOps>%s</ModOps>" % body, encoding="utf-8")


class TestEachOpIsCountedByWhatItWorksOut(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def counted(self) -> op_counts.Counts:
        return op_counts.count(op_counts.mods_in(self.root))

    def test_a_named_literal_op_with_no_condition_is_in_the_first_row(self):
        _mod(self.root, "One", PATCH_1800,
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
             '<Radius>4</Radius></ModOp>')
        counts = self.counted()
        self.assertEqual((counts.mods, counts.modops), (1, 1))
        self.assertEqual(counts.by_mix["none"], 1)

    def test_each_property_worked_out_at_load_has_a_row(self):
        _mod(self.root, "One", PATCH_1800,
             # a condition about the document
             '<ModOp Type="add" GUID="1000" Path="/Values/Cost" '
             'Condition="!/Values/Cost/Costs"><Costs/></ModOp>'
             # a search across assets
             '<ModOp Type="replace" Path="//Values[Building]/Building/Radius">'
             '<Radius>4</Radius></ModOp>'
             # a pattern inside a named asset, with a condition
             '<ModOp Type="remove" GUID="1000" Path="/Values/Cost/Costs/Item[Amount]" '
             'Condition="#other-mod"/>'
             # a copy
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
             'Content="//Values[Standard/GUID=\'1001\']/Building/Radius"/>')
        counts = self.counted()
        self.assertEqual(counts.modops, 4)
        self.assertEqual(
            {key: counts.by_mix[key] for key in ("condition", "target", "target+condition", "payload")},
            {"condition": 1, "target": 1, "target+condition": 1, "payload": 1},
        )
        self.assertEqual(dict(counts.target), {"search": 1, "pattern": 1})
        self.assertEqual(dict(counts.condition), {"document": 1, "loadout": 1})
        self.assertEqual(dict(counts.payload), {"copy": 1})

    def test_ops_in_a_group_are_counted_with_how_deep_and_the_group_beside(self):
        _mod(self.root, "One", PATCH_117,
             '<Group Condition="#other-mod"><Group Condition="#third-mod">'
             '<ModOp Replace="@1000/Building/Radius"><Radius>4</Radius></ModOp>'
             '</Group></Group>')
        counts = self.counted()
        self.assertEqual(counts.modops, 1)
        self.assertEqual(counts.scopes, 2)
        self.assertEqual((counts.in_scope, counts.deepest, counts.in_scope_constant), (1, 2, 1))
        self.assertEqual(dict(counts.by_game), {"anno117": 1})

    def test_a_loop_and_its_ops_are_counted_once(self):
        _mod(self.root, "One", PATCH_117,
             '<Group Condition="@1000/Building" MaxRepeat="999">'
             '<ModOp Replace="@1000/Building/Radius"><Radius>4</Radius></ModOp></Group>')
        counts = self.counted()
        self.assertEqual((counts.modops, counts.scopes, counts.deepest), (1, 1, 1))

    def test_an_op_shipped_switched_off_is_counted_and_said(self):
        _mod(self.root, "One", PATCH_1800,
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" Skip="1">'
             '<Radius>4</Radius></ModOp>')
        counts = self.counted()
        self.assertEqual((counts.modops, counts.skipped), (1, 1))

    def test_ops_in_a_group_switched_off_are_counted_as_shipped_switched_off(self):
        _mod(self.root, "One", PATCH_1800,
             '<Group Skip="1">'
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
             '<Radius>4</Radius></ModOp>'
             '<Group><ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
             '<Radius>5</Radius></ModOp></Group>'
             '</Group>'
             '<ModOp Type="replace" GUID="1002" Path="/Values/Building/Radius">'
             '<Radius>6</Radius></ModOp>')
        counts = self.counted()
        self.assertEqual((counts.modops, counts.skipped), (3, 2))
        self.assertIn("- Shipped switched off (Skip): 2 ops.", op_counts.report(counts))

    def test_an_op_that_adds_an_asset_makes_one(self):
        _mod(self.root, "One", PATCH_1800,
             '<ModOp Type="addNextSibling" GUID="1000"><Asset><Template>Building</Template>'
             '<Values><Standard><GUID>1900000001</GUID></Standard></Values></Asset></ModOp>')
        self.assertEqual(self.counted().makes_assets, 1)

    def test_an_asset_written_straight_under_modops_is_counted_once_and_not_as_an_op(self):
        asset = ('<Asset><Template>Building</Template><Values><Standard>'
                 '<GUID>190000000%d</GUID></Standard></Values></Asset>')
        _mod(self.root, "One", PATCH_117,
             asset % 1 + asset % 2
             + '<ModOp Replace="@1000/Building/Radius"><Radius>4</Radius></ModOp>')
        counts = self.counted()
        self.assertEqual((counts.modops, dict(counts.carried)), (1, {"asset": 2}))
        # Not ops: counted with them, they were two of the one op there is.
        self.assertEqual(counts.makes_assets, 0)
        text = op_counts.report(counts)
        self.assertIn("carried, 2 asset.", text)
        self.assertIn("- Making assets: 0 ops (0.0%).", text)

    def test_a_bundled_mod_is_read_as_a_mod_of_its_own(self):
        _mod(self.root, "Host", PATCH_1800,
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
             '<Radius>4</Radius></ModOp>')
        inner = self.root / "Host" / "Inner"
        _mod(inner.parent, "Inner", PATCH_1800,
             '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
             '<Radius>5</Radius></ModOp>')
        (inner / "modinfo.json").write_text("{}", encoding="utf-8")
        counts = self.counted()
        self.assertEqual((counts.mods, counts.modops), (2, 2))

    def test_the_report_lays_out_section_fours_table(self):
        _mod(self.root, "One", PATCH_1800,
             '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
             '<Radius>4</Radius></ModOp>')
        text = op_counts.report(self.counted())
        self.assertIn("| None | 1 | 100.0% |", text)
        self.assertIn("| Any other pair, or all three | 0 | 0.0% |", text)


if __name__ == "__main__":
    unittest.main()
