"""Edits to somebody else's mod, written into their file as ``Skip`` on the
author's op and yours inserted after it, every other byte as it was
(docs/proposals/patches-proposal.md, phase 2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import splicing  # noqa: E402

THEIRS = """<?xml version="1.0" encoding="utf-8"?>
<ModOps>
  <!-- The author's own note. -->
  <ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">
    <Radius>20</Radius>
  </ModOp>
  <ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>8</Radius></ModOp>
  <Group Condition="#other-mod">
    <ModOp Type="merge" GUID="1002" Path="/Values/Building"><Radius>5</Radius></ModOp>
  </Group>
  <ModOp Type="remove" GUID="1003" Path="/Values/Old" />
</ModOps>
"""


def spliced(theirs: str, ours: str) -> str:
    return splicing.apply(theirs, splicing.compare(splicing.scan(theirs), splicing.scan(ours)))


class TestScanning(unittest.TestCase):
    def test_the_ops_and_groups_with_where_each_stands(self):
        found = splicing.scan(THEIRS)
        self.assertEqual([node.tag for node in found.nodes], ["ModOp", "ModOp", "Group", "ModOp"])
        group = found.nodes[2]
        self.assertEqual([node.tag for node in group.children], ["ModOp"])
        self.assertTrue(THEIRS[found.nodes[3].start:found.nodes[3].end].endswith("/>"))
        self.assertTrue(THEIRS[group.start:group.end].endswith("</Group>"))

    def test_what_looks_like_a_tag_in_a_comment_or_an_attribute_is_not_one(self):
        text = ('<ModOps><!-- <ModOp Type="x"> --><ModOp Type="add" Path="a[b>1]">'
                "<![CDATA[<ModOp>]]></ModOp></ModOps>")
        found = splicing.scan(text)
        self.assertEqual(len(found.nodes), 1)
        self.assertEqual(text[found.nodes[0].start:found.nodes[0].end],
                         '<ModOp Type="add" Path="a[b>1]"><![CDATA[<ModOp>]]></ModOp>')


class TestComparing(unittest.TestCase):
    def test_the_same_ops_laid_out_differently_are_no_edit(self):
        ours = THEIRS.replace("  <!-- The author's own note. -->\n", "").replace(
            '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">',
            '<ModOp  Path="/Values/Building/Radius" Type="replace"\n     GUID="1001">',
        ).replace("<Radius>20</Radius>", "<Radius> 20 </Radius>")
        self.assertEqual(splicing.compare(splicing.scan(THEIRS), splicing.scan(ours)), [])

    def test_a_value_changed_skips_theirs_and_puts_yours_after_it(self):
        out = spliced(THEIRS, THEIRS.replace("<Radius>8</Radius>", "<Radius>12</Radius>"))
        theirs = '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius" Skip="1"><Radius>8</Radius></ModOp>'
        self.assertIn(theirs, out)
        after = out[out.index(theirs) + len(theirs):]
        self.assertTrue(after.startswith("\n  <!-- Anno Modding Studio: in place of the ModOp before this"))
        self.assertIn('<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>',
                      after.split("\n")[2])
        # Every byte of theirs is still there, with only what was added.
        self.assertEqual(out.replace(' Skip="1"', "").replace(after[: after.index("\n  <Group")], ""), THEIRS)

    def test_an_op_taken_out_is_skipped_and_said_so(self):
        ours = THEIRS.replace('  <ModOp Type="remove" GUID="1003" Path="/Values/Old" />\n', "")
        out = spliced(THEIRS, ours)
        self.assertIn('<ModOp Type="remove" GUID="1003" Path="/Values/Old" Skip="1" />', out)
        self.assertIn("the ModOp before this is switched off with Skip", out)

    def test_an_op_of_yours_goes_after_the_one_before_it(self):
        mine = '<ModOp Type="add" GUID="1000" Path="/Values"><Mine /></ModOp>'
        ours = THEIRS.replace("  <Group", "  %s\n  <Group" % mine)
        out = spliced(THEIRS, ours)
        self.assertNotIn("Skip", out)
        self.assertLess(out.index("<Radius>8</Radius></ModOp>"), out.index(mine))
        self.assertLess(out.index(mine), out.index("<Group"))
        self.assertIn("<!-- Anno Modding Studio: added here -->", out)

    def test_one_with_nothing_before_it_goes_at_the_top(self):
        mine = '<ModOp Type="add" GUID="1000" Path="/Values"><Mine /></ModOp>'
        ours = THEIRS.replace("<ModOps>\n", "<ModOps>\n  %s\n" % mine)
        out = spliced(THEIRS, ours)
        self.assertLess(out.index(mine), out.index("The author's own note"))

    def test_an_edit_inside_a_group_stays_inside_it(self):
        out = spliced(THEIRS, THEIRS.replace("<Radius>5</Radius>", "<Radius>6</Radius>"))
        group = out[out.index("<Group"):out.index("</Group>")]
        self.assertIn('Skip="1"><Radius>5</Radius>', group)
        self.assertIn("<Radius>6</Radius>", group)
        self.assertNotIn('<Group Condition="#other-mod" Skip', out)

    def test_a_group_whose_condition_changed_is_skipped_and_written_again(self):
        out = spliced(THEIRS, THEIRS.replace('Condition="#other-mod"', 'Condition="!#other-mod"'))
        self.assertIn('<Group Condition="#other-mod" Skip="1">', out)
        self.assertIn('<Group Condition="!#other-mod">', out)

    def test_an_op_shipped_switched_off_is_left_as_it_is(self):
        theirs = THEIRS.replace('GUID="1003" Path="/Values/Old"', 'GUID="1003" Path="/Values/Old" Skip="1"')
        ours = theirs.replace('  <ModOp Type="remove" GUID="1003" Path="/Values/Old" Skip="1" />\n', "")
        self.assertEqual(splicing.compare(splicing.scan(theirs), splicing.scan(ours)), [])


class TestAnEditCarriedOntoTheirFile(unittest.TestCase):
    """The edits are found between two writings of the mod, without them and
    with them, and carried onto the author's file: a place the writing only
    writes another way is not taken for an edit, nor taken along with one
    beside it."""

    THEIRS = """<ModOps>
  <ModOp Type="merge" GUID="1" Path="/Values"><A>1</A></ModOp>
  <Include File="parts.include.xml" Condition="#other" />
  <ModOp Type="merge" GUID="2" Path="/Values"><B>2</B></ModOp>
</ModOps>
"""
    # How a writing of it has the include: written out, in a group.
    BEFORE = """<ModOps>
  <ModOp Type="merge" GUID="1" Path="/Values"><A>1</A></ModOp>
  <Group Condition="#other">
    <ModOp Type="merge" GUID="3" Path="/Values"><C>3</C></ModOp>
  </Group>
  <ModOp Type="merge" GUID="2" Path="/Values"><B>2</B></ModOp>
</ModOps>
"""

    def carried(self, after: str) -> list:
        scan = splicing.scan
        return splicing.edit_hunks(scan(self.THEIRS), scan(self.BEFORE), scan(after))

    def test_no_edit_is_nothing_however_it_is_written(self):
        self.assertEqual(self.carried(self.BEFORE), [])

    def test_an_op_beside_it_taken_out_is_that_op_alone(self):
        after = self.BEFORE.replace('  <ModOp Type="merge" GUID="2" Path="/Values"><B>2</B></ModOp>\n', "")
        (hunk,) = self.carried(after)
        self.assertEqual([splice.kind for splice in hunk], ["skip"])
        self.assertIn('GUID="2"', self.THEIRS[hunk[0].node.start:hunk[0].node.end])

    def test_an_op_beside_it_typed_over_is_that_op_alone(self):
        (hunk,) = self.carried(self.BEFORE.replace("<A>1</A>", "<A>9</A>"))
        self.assertEqual([splice.kind for splice in hunk], ["skip", "insert"])
        self.assertIn("<A>9</A>", hunk[1].text)
        out = splicing.apply(self.THEIRS, hunk)
        self.assertIn('<Include File="parts.include.xml" Condition="#other" />', out)

    def test_an_edit_inside_it_takes_the_include_with_it(self):
        (hunk,) = self.carried(self.BEFORE.replace("<C>3</C>", "<C>4</C>"))
        out = splicing.apply(self.THEIRS, hunk)
        self.assertIn('<Include File="parts.include.xml" Condition="#other" Skip="1" />', out)
        self.assertIn("<C>4</C>", out)
        self.assertNotIn('GUID="1" Path="/Values" Skip', out)
        self.assertNotIn('GUID="2" Path="/Values" Skip', out)


class TestTheBytes(unittest.TestCase):
    def test_line_endings_and_a_byte_order_mark_are_kept(self):
        raw = b"\xef\xbb\xbf" + THEIRS.replace("\n", "\r\n").encode("utf-8")
        text, bom = splicing.decode(raw)
        ours = text.replace("<Radius>8</Radius>", "<Radius>12</Radius>")
        out = splicing.encode(splicing.apply(text, splicing.compare(splicing.scan(text), splicing.scan(ours))), bom)
        self.assertTrue(out.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))
        self.assertIn(b"<Radius>12</Radius>", out)

    def test_bytes_that_are_not_utf8_come_back_as_they_were(self):
        raw = THEIRS.encode("utf-8").replace(b"author's", b"auth\xe9r's")
        text, bom = splicing.decode(raw)
        self.assertEqual(splicing.encode(text, bom), raw)


if __name__ == "__main__":
    unittest.main()
