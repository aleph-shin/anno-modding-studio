"""Conditions that search the whole document -- ``//Values[Standard/GUID='…']``
-- answered from the document as it stands when the op runs: the shipped
file, and what the mods ahead of it have done."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import SessionCase  # noqa: E402

from anno_modding_studio.core import modops  # noqa: E402

MAKER = ('<ModOp Type="addNextSibling" GUID="1000">'
         "<Asset><Template>Thing</Template><Values><Standard>"
         "<GUID>7777</GUID><Name>invented</Name></Standard>"
         "<Building><Radius>5</Radius></Building></Values></Asset></ModOp>")


def _asks(condition: str, guid: str = "1000", radius: str = "30") -> str:
    return '<ModOp Merge="@%s/Building" Condition="%s"><Radius>%s</Radius></ModOp>' % (
        guid, condition, radius)


class TestAnswered(SessionCase):
    def statuses(self, **mods):
        session = self.session(self.mods(**mods))
        return {read.origin: read.report.results[-1].status for read in session.reads}

    def test_from_the_shipped_file(self):
        found = self.statuses(
            there=_asks("//Values[Standard/GUID='1001']"),
            absent=_asks("//Values[Standard/GUID='9999']", "1001"),
            negated=_asks("!//Values[Standard/GUID='9999']", "1000", "31"),
            text=_asks("//Values[Standard/GUID='1001']/Standard/Name/text()", "1001"),
        )
        self.assertEqual(found, {"there": modops.IMPORTED, "absent": modops.SETTLED,
                                 "negated": modops.IMPORTED, "text": modops.IMPORTED})

    def test_from_what_the_mods_ahead_did(self):
        found = self.statuses(
            a__maker=MAKER,
            b__retuner='<ModOp Merge="@1001/Building"><Radius>77</Radius></ModOp>',
            c__asker=_asks("//Values[Standard/GUID='7777']"),
            d__asker=_asks("//Building[Radius='77']", "1001", "78"),
            # Nothing shipped has a radius of 3 any more once b has run.
            e__asker=_asks("//Building[Radius='3']", "1000", "79"),
        )
        self.assertEqual(found["c asker"], modops.IMPORTED)
        self.assertEqual(found["d asker"], modops.IMPORTED)
        self.assertEqual(found["e asker"], modops.SETTLED)

    def test_on_a_group(self):
        session = self.session(self.mods(
            grouped="<Group Condition=\"//Values[Standard/GUID='1001']\">"
                    '<ModOp Merge="@1000/Building"><Radius>40</Radius></ModOp></Group>',
            skipped="<Group Condition=\"//Values[Standard/GUID='9999']\">"
                    '<ModOp Merge="@1001/Building"><Radius>41</Radius></ModOp></Group>',
        ))
        changes = {read.origin: [c.new_value for c in read.changes] for read in session.reads}
        self.assertEqual(changes, {"grouped": ["40"], "skipped": []})


class TestNotAnsweredWhereTheStudioCouldNotRead(TestAnswered):
    """Finding nothing where an op the Studio could not read has been is not
    an answer: *BT Quests Instead Preferred* fills a pool by copying a list,
    unread, and adds quests only if that pool has entries."""

    UNREAD = '<ModOp Type="frobnicate" GUID="1001" Path="/Values/Building"><Radius>99</Radius></ModOp>'

    def test_a_search_naming_that_asset_is_not_answered(self):
        found = self.statuses(
            a__unread=self.UNREAD,
            b__asker=_asks("//Values[Standard/GUID='1001']/Building[Radius='99']"),
            # One naming only assets nothing unread has touched still is --
            # and b's own asset now counts as unread: whether b ran is unknown.
            c__elsewhere=_asks("//Values[Standard/GUID='1002']/Building[Radius='99']", "1001"),
            # And finding something is an answer wherever it is found.
            d__found=_asks("//Values[Standard/GUID='1001']", "1000", "32"),
        )
        self.assertEqual(found["b asker"], modops.BLOCKED)
        self.assertEqual(found["c elsewhere"], modops.SETTLED)
        self.assertEqual(found["d found"], modops.IMPORTED)

    def test_nor_is_an_op_that_searches_for_it(self):
        """An op aimed by the same search is answered from the same two halves,
        and had a join of its own: it came to nothing where the condition was
        not answered."""
        path = "//Values[Standard/GUID='1001']/Building[Radius='99']"
        found = self.statuses(
            a__unread=self.UNREAD,
            b__asker=_asks(path),
            c__searcher='<ModOp Type="merge" Path="%s"><Radius>5</Radius></ModOp>' % path,
        )
        self.assertEqual(found["b asker"], modops.BLOCKED)
        self.assertEqual(found["c searcher"], modops.BLOCKED)

    def test_one_naming_no_asset_is_answered_from_what_there_is(self):
        """It could reach the unread op's asset as easily as not, and holding
        all of them back left 157 more census ops unread, most of them answered
        rightly before."""
        found = self.statuses(
            a__unread=self.UNREAD,
            b__asker=_asks("//Building[Radius='99']"),
        )
        self.assertEqual(found["b asker"], modops.SETTLED)


class TestNotAnswered(unittest.TestCase):
    def test_without_a_search_or_from_the_op_s_own_asset(self):
        condition = "//Values[Standard/GUID='1']"
        self.assertIsNone(modops.condition_holds(condition, lambda guid: None))
        self.assertTrue(modops.condition_holds(condition, lambda guid: None, search=lambda path: True))
        self.assertIsNone(modops.condition_holds("~" + condition, lambda guid: None, "5",
                                                 search=lambda path: True))


if __name__ == "__main__":
    unittest.main()
