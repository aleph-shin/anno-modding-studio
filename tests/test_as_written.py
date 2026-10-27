"""A mod read with every op kept as it was written.

Phase 2 of docs/proposals/ops-as-written-proposal.md.  It is the one reading
there is: the narrow one left in phase 3 of docs/proposals/reader-proposal.md.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 - first: keeps the suite out of real files
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import ImportCase, SessionCase, modops_file  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    conflicts,
    games,
    generator,
    keptops,
    loadercheck,
    modfolder,
    modop,
    modops,
    reader,
)

RADIUS = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>25</Radius></ModOp>'
WIDE = (
    '<ModOp Type="replace" GUID="1000" Path="/Values/Building">'
    "<Building><BuildingType>Factory</BuildingType><Radius>25</Radius></Building></ModOp>"
)
SEVERAL = '<ModOp Type="merge" GUID="1000,1001" Path="/Values/Building"><Radius>25</Radius></ModOp>'
ALREADY = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>10</Radius></ModOp>'
SAME_TYPE = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
    "<BuildingType>Factory</BuildingType></ModOp>"
)


class AsWrittenCase(ImportCase):
    def read(self, body: str) -> modops.ImportReport:
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        translator = reader.Translator(self.index, True)
        translator.search = reader.searcher(self.index, ops)
        report.results = translator.run_all(ops)
        report.ops = translator.found.get("", [])
        return report

    def same(self, op, written: str) -> None:
        self.assertTrue(keptops.same_op(op.source, written), op.source)


class TestEveryOpIsKept(AsWrittenCase):
    def test_an_op_that_is_one_narrow_change_already(self):
        report = self.read(RADIUS)
        (result,) = report.results
        self.assertEqual(result.status, modops.IMPORTED)
        (op,) = report.ops
        self.assertIs(result.record, op)
        self.same(op, RADIUS)
        self.assertEqual([c.op_uid for c in report.changes], [op.uid])
        self.assertEqual(op.made, 1)
        # Nothing stands in the way of writing it narrow, should that be asked.
        self.assertEqual(op.kept, "")

    def test_a_wide_replace_is_one_op_with_what_it_changes_under_it(self):
        report = self.read(WIDE)
        (op,) = report.ops
        self.same(op, WIDE)
        self.assertEqual([(c.tag, c.new_value) for c in report.changes], [("Radius", "25")])
        self.assertEqual({c.op_uid for c in report.changes}, {op.uid})

    def test_an_op_naming_several_assets_is_one_op(self):
        report = self.read(SEVERAL)
        (op,) = report.ops
        self.same(op, SEVERAL)
        self.assertEqual(op.matched, ["1000", "1001"])
        self.assertEqual(sorted(c.guid for c in report.changes), ["1000", "1001"])
        self.assertEqual({c.op_uid for c in report.changes}, {op.uid})

    def test_an_op_that_changes_nothing_today_is_kept_with_nothing_under_it(self):
        report = self.read(ALREADY)
        (result,) = report.results
        self.assertEqual(result.status, modops.SETTLED)
        (op,) = report.ops
        self.same(op, ALREADY)
        self.assertEqual(report.changes, [])
        self.assertEqual(op.made, 0)

    def test_its_note_is_the_ops(self):
        report = self.read("<!-- bigger -->" + RADIUS)
        self.assertEqual(report.ops[0].note, "bigger")

    def test_where_it_stood_in_the_file_is_kept(self):
        report = self.read(ALREADY + RADIUS + WIDE)
        self.assertEqual([op.order for op in report.ops], [0, 1, 2])


class TestAConditionIsKeptWithItsOp(AsWrittenCase):
    # The classic form, which both games read: the list on screen reads its
    # mods as Anno 1800's, whose loader has no short form.
    HOLDS = (
        "<ModOp Type=\"merge\" GUID=\"1000\" Path=\"/Values/Building\" "
        "Condition=\"/Values/Building[Radius = '10']\"><Radius>25</Radius></ModOp>"
    )
    FAILS = (
        "<ModOp Type=\"merge\" GUID=\"1000\" Path=\"/Values/Building\" "
        "Condition=\"/Values/Building[Radius = '99']\"><Radius>25</Radius></ModOp>"
    )

    def test_one_that_holds_is_not_answered_for_good(self):
        report = self.read(self.HOLDS)
        (op,) = report.ops
        self.assertIn("Condition=", op.source)
        self.assertEqual([c.new_value for c in report.changes], ["25"])

    def test_one_that_does_not_hold_still_has_its_op(self):
        report = self.read(self.FAILS)
        (op,) = report.ops
        self.assertIn("Condition=", op.source)
        self.assertEqual(report.changes, [])

    def test_a_search_whose_condition_does_not_hold_still_has_its_op(self):
        report = self.read(
            '<ModOp Type="merge" Path="//Values/Building" Condition="/Values/Nothing">'
            "<Radius>30</Radius></ModOp>"
        )
        self.assertEqual(report.results[0].status, modops.SETTLED)
        (op,) = report.ops
        self.assertEqual(op.matched, [])


class TestAGroupIsKeptAsAGroupWithOpsInIt(AsWrittenCase):
    GROUPED = '<ModOp Merge="@1001/Building"><Radius>25</Radius></ModOp>'

    def test_one_asking_about_the_games_files_whose_answer_is_yes(self):
        report = self.read('<Group Condition="@1000/Building">%s</Group>' % self.GROUPED)
        group, op = report.ops
        # The group is its own tag and nothing inside it...
        self.assertTrue(group.scope)
        self.assertFalse(group.idle)
        self.assertEqual(group.source, '<Group Condition="@1000/Building"/>')
        # ... and the op inside is an op of its own that says where it is.
        self.same(op, self.GROUPED)
        self.assertEqual(op.within, [group.uid])
        self.assertEqual([c.new_value for c in report.changes], ["25"])
        self.assertEqual({c.op_uid for c in report.changes}, {op.uid})
        self.assertEqual([c.within for c in report.changes], [()])

    def test_and_one_whose_answer_is_no_still_holds_its_ops(self):
        report = self.read('<Group Condition="@1000/Nothing">%s</Group>' % self.GROUPED)
        group, op = report.ops
        self.assertTrue(group.idle)
        self.assertIn("none of it does", group.kept)
        self.same(op, self.GROUPED)
        self.assertEqual(op.within, [group.uid])
        self.assertEqual(report.changes, [])

    def test_one_the_mod_ships_switched_off(self):
        report = self.read('<Group Skip="1">%s</Group>' % self.GROUPED)
        group, op = report.ops
        self.assertTrue(group.idle)
        self.assertEqual(op.within, [group.uid])
        self.assertEqual(report.changes, [])

    def test_a_group_inside_a_group(self):
        report = self.read(
            '<Group Condition="@1000/Building">%s<Group ModOpID="inner">%s</Group></Group>'
            % (RADIUS, self.GROUPED)
        )
        outer, first, inner, second = report.ops
        self.assertEqual(first.within, [outer.uid])
        self.assertEqual(inner.within, [outer.uid])
        self.assertTrue(inner.scope)
        self.assertEqual(inner.tool, {"id": "inner"})
        self.assertEqual(second.within, [outer.uid, inner.uid])
        self.assertEqual(len(report.changes), 2)

    def test_what_is_inside_one_that_does_not_run_does_not_either(self):
        report = self.read(
            '<Group Condition="@1000/Nothing"><Group Condition="@1000/Building">%s</Group></Group>'
            % self.GROUPED
        )
        outer, inner, op = report.ops
        self.assertTrue(outer.idle and inner.idle)
        self.assertEqual(op.within, [outer.uid, inner.uid])
        self.assertEqual(report.changes, [])

    def test_an_op_the_studio_cannot_read_says_which_group_it_is_in(self):
        report = self.read(
            '<Group Condition="@1000/Building">'
            '<ModOp Type="replace" GUID="9999" Path="/Values/Nope"><Nope>1</Nope></ModOp></Group>'
        )
        (group,) = report.ops
        (unread,) = report.unread("")
        self.assertEqual(unread.within, [group.uid])

    def test_one_whose_question_cannot_be_answered_is_carried_whole(self):
        with mock.patch.object(reader, "condition_holds", return_value=None):
            report = self.read('<Group Condition="@1000/Building">%s</Group>' % self.GROUPED)
        self.assertEqual(report.ops, [])
        (unread,) = report.unread("")
        self.assertIn("Radius", unread.source)

    def test_an_op_inside_one_can_be_plain(self):
        report = self.read('<Group Condition="@1000/Building">%s</Group>' % RADIUS)
        _group, op = report.ops
        self.assertTrue(op.plain)


class TestOpsOfOneModThatMeet(AsWrittenCase):
    """An op that edits what its own mod has just added is read into that
    addition, kept as written or not: the asset is shown from the changes,
    and an edit written as a change of its own, with a path into a node that
    was not there a moment ago, did not always find that node again (the
    first census read as written showed five assets wrongly for it).  Each
    op still goes out as itself."""

    ADD = '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra><List/></Extra></ModOp>'
    INTO = (
        '<ModOp Type="add" GUID="1000" Path="/Values/Building/Extra/List">'
        "<Item><N>1</N></Item></ModOp>"
    )

    def test_an_edit_to_what_an_earlier_op_added_is_shown_in_the_addition(self):
        report = self.read(self.ADD + self.INTO)
        first, second = report.results
        self.assertIn("<Item>", first.changes[0].payload)
        self.assertEqual(second.changes, [])
        # Both are kept, and the second says where its work is.
        self.assertEqual(len(report.ops), 2)
        self.same(second.record, self.INTO)
        self.assertEqual(second.record.folded, [list(first.changes[0].key)])

    def test_and_its_row_says_where_its_work_is(self):
        from anno_modding_studio.core.changes import Loadout

        report = self.read(self.ADD + self.INTO)
        loadout = Loadout()
        for op in report.ops:
            loadout.set_op(op)
        for change in report.changes:
            loadout.set_change(change)
        _first, second = keptops.of(loadout)
        self.assertEqual(second.caption(), "shown in the op it edits")

    def test_two_ops_setting_one_field_are_two_changes(self):
        again = RADIUS.replace("25", "40")
        report = self.read(RADIUS + again)
        self.assertEqual([c.new_value for c in report.changes], ["25", "40"])
        self.assertEqual(len({c.key for c in report.changes}), 2)
        self.assertEqual(len({c.op_uid for c in report.changes}), 2)
        # The second is another op of the mod, not an exception somebody typed.
        self.assertEqual([c.exception_of for c in report.changes], ["", ""])


class TestNothingIsSettledOrReplayed(AsWrittenCase):
    COPY = (
        '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
        'Content="@1001/Building/Radius" />'
    )

    def test_and_says_why_it_cannot_be_written_narrow_as_it_stands(self):
        report = self.read(self.COPY)
        self.assertIn("copies its value", report.ops[0].kept)

    def test_the_changes_are_still_replayed_for_what_is_shown_from_them(self):
        """Not on trial as ops to write, but the asset is drawn from them."""
        with mock.patch.object(
            reader.Translator, "_replays_as_read", return_value=False
        ), mock.patch.object(reader.Translator, "_rekeyed", return_value=False) as rekeyed:
            report = self.read(WIDE)
        rekeyed.assert_called_once()
        self.assertIn("does not land on", report.ops[0].caveat)
        self.assertEqual(self.read(WIDE).ops[0].caveat, "")


class TestWrittenBackItIsWhatItWas(SessionCase):
    """The whole way through: a folder read, and its mod written out again."""

    def export(self, **folders):
        root = self.mods(**folders)
        _session, loadout = loadercheck.read_folders(
            modfolder.mod_folders(root), [root], self.index, "anno1800"
        )
        (origin,) = loadout.origins(loaded=True)
        text = generator.build_modops_xml(
            generator.for_mod(loadout, origin),
        )
        written = etree.fromstring(text.encode("utf-8"))
        return loadout, [node for node in written if isinstance(node.tag, str)]

    def test_every_op_goes_out_as_the_op_it_was(self):
        body = ALREADY + WIDE + SEVERAL
        loadout, written = self.export(Mod=body)
        asked = list(etree.fromstring(modops_file(body).encode("utf-8")))
        self.assertEqual(len(loadout.ops), 3)
        self.assertEqual(len(written), len(asked))
        for ours, theirs in zip(written, asked, strict=True):
            self.assertTrue(keptops.same_op(
                etree.tostring(ours, encoding="unicode", with_tail=False),
                etree.tostring(theirs, encoding="unicode", with_tail=False),
            ))

    def test_and_a_group_as_the_group_it_was(self):
        body = '<Group Condition="@1000/Building">%s</Group>' % RADIUS
        _loadout, written = self.export(Mod=body)
        (group,) = written
        self.assertEqual(group.tag, "Group")
        self.assertEqual(group.get("Condition"), "@1000/Building")
        self.assertEqual([child.tag for child in group], ["ModOp"])

    def test_groups_in_groups_with_ops_before_between_and_after(self):
        body = (
            RADIUS
            + '<Group Condition="@1000/Nothing">'
            + SEVERAL
            + '<Group ModOpID="inner">' + SAME_TYPE + "</Group>"
            + '<ModOp Type="replace" GUID="9999" Path="/Values/Nope"><Nope>1</Nope></ModOp>'
            + "</Group>"
            + '<group Skip="1">' + WIDE + "</group>"
            + ALREADY
        )
        _loadout, written = self.export(Mod=body)
        asked = [
            node for node in etree.fromstring(modops_file(body).encode("utf-8"))
            if isinstance(node.tag, str)
        ]

        def shape(node):
            return (node.tag, sorted(node.attrib.items()) if node.tag.lower() == "group" else None,
                    [shape(child) for child in node if isinstance(child.tag, str)]
                    if node.tag.lower() == "group" else None)

        self.assertEqual([shape(node) for node in written], [shape(node) for node in asked])

    def test_an_ops_own_text_goes_out_whitespace_and_all(self):
        """The loader appends it as it stands.  *Extended Enbesa* adds
        ``;Africa`` on a line of its own, inside a group."""
        appended = "\n            ;Extra"
        body = (
            '<Group Condition="@1000/Building">'
            '<ModOp Type="add" GUID="1000" Path="/Values/Building/BuildingType">%s</ModOp>'
            "</Group>" % appended
        )
        _loadout, written = self.export(Mod=body)
        (group,) = written
        self.assertEqual(group[0].text, appended)

    def test_a_group_with_nothing_in_it(self):
        _loadout, written = self.export(Mod='<Group Condition="@1000/Building"></Group>' + RADIUS)
        self.assertEqual([node.tag for node in written], ["Group", "ModOp"])
        self.assertEqual(len(written[0]), 0)

    def test_an_op_switched_off_inside_a_group_is_a_comment_inside_it(self):
        root = self.mods(Mod='<Group Condition="@1000/Building">%s</Group>' % RADIUS)
        _session, loadout = loadercheck.read_folders(
            modfolder.mod_folders(root), [root], self.index, "anno1800"
        )
        loadout.ops[1].active = False
        for change in loadout.changes.values():
            change.active = False
        text = generator.build_modops_xml(
            generator.for_mod(loadout, "Mod"),
        )
        (group,) = [n for n in etree.fromstring(text.encode("utf-8")) if isinstance(n.tag, str)]
        self.assertEqual([child.tag for child in group if isinstance(child.tag, str)], [])
        self.assertIn("switched off", "".join(c.text or "" for c in group if not isinstance(c.tag, str)))

    def test_notes_stacked_over_an_op_are_its_note_a_line_each(self):
        """A11-40: how the Studio writes a ModOp for several changes, under
        the note of each.  Read back, only the last was its note and the
        others were comments standing alone."""
        loadout, _written = self.export(Mod="<!-- note: one -->\n<!-- note: two -->\n" + RADIUS)
        (op,) = [op for op in loadout.ops if not op.aside]
        self.assertEqual(op.note, "one\ntwo")
        self.assertEqual([op for op in loadout.ops if op.aside], [])
        text = generator.build_modops_xml(generator.for_mod(loadout, "Mod"))
        self.assertIn("  <!-- note: one -->\n  <!-- note: two -->\n  <ModOp ", text)

    def test_a_comment_of_the_authors_over_them_is_not(self):
        loadout, _written = self.export(Mod="<!-- why -->\n<!-- note: two -->\n" + RADIUS)
        (op,) = [op for op in loadout.ops if not op.aside]
        self.assertEqual(op.note, "two")
        (aside,) = [op for op in loadout.ops if op.aside]
        self.assertIn("why", aside.source)

    def test_a_group_switched_off_reads_back_switched_off(self):
        """A11-8: its tags were written live, so read back it was on."""
        body = '<Group Condition="@1000/Building">%s</Group>' % RADIUS
        root = self.mods(Mod=RADIUS.replace("25", "30") + body)
        _session, loadout = loadercheck.read_folders(
            modfolder.mod_folders(root), [root], self.index, "anno1800"
        )
        group = next(op for op in loadout.ops if op.scope)
        group.note = "why"
        for op in loadout.ops:
            if op is group or group.uid in op.within:
                op.active = False
                for change in loadout.changes_by_op(op.uid):
                    change.active = False
        text = generator.build_modops_xml(generator.for_mod(loadout, "Mod"))
        (root / "Mod/data/base/config/export/assets.xml").write_text(text, encoding="utf-8")
        _session, again = loadercheck.read_folders(
            modfolder.mod_folders(root), [root], self.index, "anno1800"
        )
        ops = sorted(again.ops, key=lambda op: op.order)
        self.assertEqual([(op.scope, op.active) for op in ops],
                         [(False, True), (True, False), (False, False)])
        self.assertEqual(ops[2].within, [ops[1].uid])
        self.assertIn("@1000/Building", ops[1].source)
        self.assertEqual(ops[1].note, "why")
        # And written again, it is the same file.
        self.assertEqual(generator.build_modops_xml(generator.for_mod(again, "Mod")), text)


class TestWhatAnOpIsSaidToBe(AsWrittenCase):
    """Two things a list needs to know of an op kept as written."""

    SEARCH = '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
    COPY = TestNothingIsSettledOrReplayed.COPY

    def test_a_search_says_it_searches(self):
        (op,) = self.read(self.SEARCH).ops
        self.assertTrue(op.searches)
        self.assertEqual(op.kept, "")

    def test_an_op_naming_its_assets_does_not(self):
        for body in (RADIUS, WIDE, SEVERAL, ALREADY, self.COPY):
            with self.subTest(body=body):
                self.assertFalse(self.read(body).ops[0].searches)

    def test_one_change_at_the_node_it_names_is_plain(self):
        self.assertTrue(self.read(RADIUS).ops[0].plain)

    def test_anything_else_is_not(self):
        cases = {
            "it changes a field inside the node it names": WIDE,
            "it names several assets": SEVERAL,
            "it changes nothing": ALREADY,
            "there is a reason to give for it": self.COPY,
            "it searches": self.SEARCH,
            "it asks something first": TestAConditionIsKeptWithItsOp.HOLDS,
        }
        for why, body in cases.items():
            with self.subTest(why=why):
                self.assertFalse(self.read(body).ops[0].plain)

    def test_both_are_saved_with_it(self):
        from anno_modding_studio.core.changes import Op

        (op,) = self.read(RADIUS).ops
        again = Op.from_dict(op.to_dict())
        self.assertEqual((again.plain, again.searches), (True, False))


class ListedCase(SessionCase):
    """A folder read as written into a loadout, as the mods-folder import
    makes it."""

    def loadout(self, **folders):
        root = self.mods(**folders)
        _session, loadout = loadercheck.read_folders(
            modfolder.mod_folders(root), [root], self.index, "anno1800"
        )
        return loadout


class TestHowTheListDrawsIt(ListedCase):
    def section(self, body):
        from anno_modding_studio.core import grouping

        loadout = self.loadout(Mod=body)
        (section,) = [s for s in grouping.by_mod(loadout) if s.origin == "Mod"]
        return loadout, section

    def test_a_plain_op_is_its_changes_row_among_the_assets(self):
        _loadout, section = self.section(RADIUS)
        self.assertEqual(section.ops, ())
        (group,) = section.groups
        self.assertEqual([change.new_value for change in group.changes], ["25"])

    def test_any_other_has_a_row_of_its_own(self):
        _loadout, section = self.section(RADIUS + WIDE.replace("25", "40") + SAME_TYPE)
        self.assertEqual(len(section.ops), 2)
        self.assertEqual([len(op.changes) for op in section.ops], [1, 0])
        self.assertEqual(len(section.groups), 1)

    def test_a_plain_op_whose_change_has_gone_has_its_own_row_again(self):
        loadout, _section = self.section(RADIUS)
        loadout.changes.clear()
        from anno_modding_studio.core import grouping

        (section,) = [s for s in grouping.by_mod(loadout) if s.origin == "Mod"]
        self.assertEqual(len(section.ops), 1)

    def test_the_mod_counts_each_asset_once(self):
        _loadout, section = self.section(RADIUS + WIDE.replace("25", "40"))
        self.assertTrue(section.caption().startswith("1 asset, 2 changes"), section.caption())
        self.assertNotIn("rule", section.caption())

    def test_an_op_kept_for_no_reason_of_its_own_says_how_much_it_changes(self):
        from anno_modding_studio.core import keptops

        loadout = self.loadout(Mod=SEVERAL + SAME_TYPE)
        first, second = keptops.of(loadout, "Mod")
        self.assertEqual(first.caption(), "2 changes")
        self.assertEqual(second.caption(), "changes nothing as things stand")


class TestTheListOnScreen(ListedCase):
    def panel(self, **folders):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock

        QApplication.instance() or QApplication([])
        loadout = self.loadout(**folders)
        panel = changes_dock.ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, loadout))
        panel.build_every_row()
        return loadout, panel

    def rows(self, panel, kind):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        found = []

        def walk(item):
            if item.data(0, KIND_ROLE) == kind:
                found.append(item)
            for at in range(item.childCount()):
                walk(item.child(at))

        for top in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(top))
        return found

    def test_the_box_on_a_plain_ops_row_switches_the_op(self):
        from PySide6.QtCore import Qt

        loadout, panel = self.panel(Mod=RADIUS)
        self.assertEqual(self.rows(panel, "op"), [])
        (row,) = self.rows(panel, "change")
        self.assertTrue(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(row.checkState(0), Qt.CheckState.Checked)
        row.setCheckState(0, Qt.CheckState.Unchecked)
        (op,) = loadout.ops
        (change,) = loadout.changes.values()
        self.assertEqual((op.active, change.active), (False, False))
        # Off, it is written as an op switched off and not as a narrow one.
        text = generator.build_modops_xml(
            generator.for_mod(loadout, "Mod"),
        )
        self.assertEqual(text.count("<ModOp "), 1)
        self.assertIn("switched off", text)

    def test_reverting_that_row_takes_the_op_with_it(self):
        loadout, panel = self.panel(Mod=RADIUS + SEVERAL.replace("25", "40"))
        (row,) = [
            row for row in self.rows(panel, "change")
            if row.flags() & __import__("PySide6.QtCore").QtCore.Qt.ItemFlag.ItemIsUserCheckable
        ]
        panel.tree.setCurrentItem(row)
        panel._remove_selected()
        self.assertEqual(len(loadout.ops), 1)
        self.assertTrue(keptops.same_op(loadout.ops[0].source, SEVERAL.replace("25", "40")))
        self.assertEqual(len(loadout.changes), 2)

    def revert_offered(self, panel, row):
        """Whether Revert selected is on with ``row`` selected, on the button
        and in the row's menu, and what each says of it."""
        panel.tree.setCurrentItem(row)
        (entry,) = [
            action for action in panel.menu_for(row).actions()
            if action.text() == "Revert selected"
        ]
        self.assertEqual(entry.isEnabled(), panel.remove_button.isEnabled())
        self.assertEqual(entry.toolTip(), panel.remove_button.toolTip())
        return entry.isEnabled(), entry.toolTip()

    def test_revert_selected_is_off_where_an_op_holds_everything_selected(self):
        """An op's row, a change under one and a mod whose changes all hang
        off ops: it was offered on each, and did nothing."""
        loadout, panel = self.panel(Mod=SEVERAL)
        (op_row,) = self.rows(panel, "op")
        under = self.rows(panel, "change")
        self.assertEqual(len(under), 2)
        (mod_row,) = self.rows(panel, "mod")
        for row in (op_row, under[0], mod_row):
            offered, why = self.revert_offered(panel, row)
            self.assertFalse(offered, row.text(0))
            self.assertIn("This does not revert a ModOp kept as written", why)
            panel._remove_selected()
            self.assertEqual((len(loadout.changes), len(loadout.ops)), (2, 1))

    def test_and_on_where_something_selected_is_a_change_of_its_own(self):
        loadout, panel = self.panel(Mod=RADIUS + SEVERAL.replace("25", "40"))
        (mod_row,) = self.rows(panel, "mod")
        offered, why = self.revert_offered(panel, mod_row)
        self.assertTrue(offered)
        self.assertIn("Removes these changes for good", why)
        (op_row,) = self.rows(panel, "op")
        self.assertFalse(self.revert_offered(panel, op_row)[0])
        # With nothing selected there is nothing to revert, and no op to
        # give as the reason.
        panel.tree.clearSelection()
        self.assertFalse(panel.remove_button.isEnabled())
        self.assertIn("Removes these changes for good", panel.remove_button.toolTip())
        # What the mod's row reverts is the change of its own, and the op's
        # two stay.
        panel.tree.setCurrentItem(self.rows(panel, "mod")[0])
        panel._remove_selected()
        self.assertEqual((len(loadout.changes), len(loadout.ops)), (2, 1))

    def test_what_is_under_an_ops_own_row_has_no_box(self):
        from PySide6.QtCore import Qt

        _loadout, panel = self.panel(Mod=SEVERAL)
        (op,) = self.rows(panel, "op")
        self.assertTrue(op.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        for row in self.rows(panel, "change"):
            self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_the_same_edit_to_several_assets_is_a_row_for_each_asset(self):
        _loadout, panel = self.panel(Mod=SEVERAL)
        (op,) = self.rows(panel, "op")
        self.assertEqual(op.text(0), "2 assets \u203a Building: Radius = 25")
        self.assertEqual(self.rows(panel, "group"), [])
        self.assertEqual(
            sorted(row.text(0) for row in self.rows(panel, "change")),
            ["alpha \u203a Building \u203a Radius", "beta \u203a Building \u203a Radius"],
        )

    def test_an_op_kept_as_every_op_is_wears_no_mark(self):
        _loadout, panel = self.panel(Mod=TestWhatAnOpIsSaidToBe.COPY + SEVERAL)
        copy, plain = self.rows(panel, "op")
        self.assertEqual(plain.text(1), "2 changes")
        self.assertEqual(copy.text(1), "= 4")

    def test_each_thing_worked_out_at_load_is_a_mark_with_what_it_comes_to(self):
        search = '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
        some = '<ModOp Type="merge" Path="//Values/Building"><Radius>10</Radius></ModOp>'
        entries = '<ModOp Type="remove" GUID="1000" Path="/Values/Cost/Costs/Item[Amount]" />'
        asked = TestAConditionIsKeptWithItsOp
        cases = {
            search: "2 assets",
            some: "1 of 2 assets",
            entries: "2 matches",
            asked.HOLDS: "runs \u00b7 1 change",
            asked.FAILS: "does not run",
            RADIUS.replace("<ModOp ", '<ModOp Skip="1" '): "off in the mod's file",
        }
        for body, wanted in cases.items():
            with self.subTest(wanted=wanted):
                _loadout, panel = self.panel(Mod=body)
                (row,) = self.rows(panel, "op")
                self.assertEqual(row.text(1), wanted)
                self.assertTrue(row.toolTip(1))


class TestWhatIsSaidOverItInTheFile(ListedCase):
    def written(self, body):
        loadout = self.loadout(Mod=body)
        return generator.build_modops_xml(generator.for_mod(loadout, "Mod"))

    def test_nothing_of_the_studios_is_said_over_an_op(self):
        """Why an op is kept, and that it searches, were the narrow reading's
        reasons.  The Mod changes panel says what an op is."""
        for body in (WIDE, TestWhatAnOpIsSaidToBe.COPY, TestWhatAnOpIsSaidToBe.SEARCH):
            with self.subTest(body=body):
                text = self.written(body)
                self.assertNotIn("<!--", text)
                self.assertIn("<ModOp ", text)


class TestWhatThereIsToSayOfAnOp(unittest.TestCase):
    """Which of its target, payload and condition are worked out at load."""

    def said(self, text, game=""):
        return modops.described(text, game)

    def test_an_op_with_everything_written_down(self):
        for body in (RADIUS, WIDE, SEVERAL):
            with self.subTest(body=body):
                found = self.said(body)
                self.assertTrue(found.constant)
                self.assertEqual((found.target, found.payload, found.condition), ("", "", ""))

    def test_a_target_that_is_a_search(self):
        found = self.said('<ModOp Type="merge" Path="//Values/Building"><Radius>3</Radius></ModOp>')
        self.assertEqual(found.target, modop.TARGET_SEARCH)
        self.assertTrue(found.pattern)

    def test_a_guid_wins_over_a_template_or_a_property(self):
        # Both loaders make an operation for each GUID and pass the
        # Template or Property by, so the op names its asset.
        for aim in ('Template="Building"', 'Property="Building"'):
            with self.subTest(aim=aim):
                found = self.said(
                    '<ModOp Type="merge" GUID="1000" %s Path="/Values/Building">'
                    '<Radius>3</Radius></ModOp>' % aim
                )
                self.assertEqual(found.target, "")

    def test_a_template_or_a_property_alone_is_a_search(self):
        for aim in ('Template="Building"', 'Property="Building"'):
            with self.subTest(aim=aim):
                found = self.said(
                    '<ModOp Type="merge" %s Path="/Values/Building">'
                    '<Radius>3</Radius></ModOp>' % aim
                )
                self.assertEqual(found.target, modop.TARGET_SEARCH)

    def test_a_target_that_is_a_pattern_inside_the_assets_it_names(self):
        paths = (
            "/Values/Cost/Costs/Item[Amount]",
            "/Values/Cost/Costs/*/Amount",
            "/Values/Cost/Costs/Item[1]",
            "/Values/Cost/Costs/Item[last()]",
            "/Values/Cost//Amount",
            "/Values/Cost/Costs/Item[Ingredient='1010' or Ingredient='1011']",
        )
        for path in paths:
            with self.subTest(path=path):
                found = self.said('<ModOp Type="remove" GUID="1000" Path="%s" />' % path)
                self.assertEqual(found.target, modop.TARGET_PATTERN)

    def test_an_entry_picked_out_by_a_value_is_named(self):
        found = self.said(
            "<ModOp Type=\"remove\" GUID=\"1000\" "
            "Path=\"/Values/Cost/Costs/Item[Ingredient='1010']\" />"
        )
        self.assertEqual(found.target, "")

    def test_a_payload_that_is_copied_computed_or_flags(self):
        cases = {
            modop.PAYLOAD_COPY: TestNothingIsSettledOrReplayed.COPY,
            modop.PAYLOAD_COMPUTED: (
                '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                '<Radius><ModValue Insert="self::node() * $factor"/></Radius></ModOp>'
            ),
            modop.PAYLOAD_FLAGS: (
                '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
                '<BuildingType><ModValue Merge="A;B"/></BuildingType></ModOp>'
            ),
        }
        for wanted, body in cases.items():
            with self.subTest(wanted=wanted):
                found = self.said(body)
                self.assertEqual(found.payload, wanted)
                self.assertTrue(found.computed)

    def test_a_condition_and_what_it_asks_about(self):
        op = '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="%s"><Radius>3</Radius></ModOp>'
        cases = {
            modop.ASKS_DOCUMENT: "!~/Values/Building/Radius",
            modop.ASKS_LOADOUT: "#other-mod",
            modop.ASKS_SETTING: "$roomier",
        }
        for wanted, condition in cases.items():
            with self.subTest(wanted=wanted):
                found = self.said(op % condition)
                self.assertEqual(found.condition, wanted)
                self.assertTrue(found.conditional)

    def test_what_is_beside_the_patch(self):
        found = self.said(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Skip="1" '
            'AllowNoMatch="1" ModOpID="bigger"><Radius>3</Radius></ModOp>'
        )
        self.assertEqual((found.skip, found.allow_no_match, found.name), (True, True, "bigger"))
        self.assertTrue(found.constant)

    def test_a_group_is_described_by_what_it_carries(self):
        found = self.said('<Group Condition="#other-mod" ModOpID="extras">%s</Group>' % RADIUS)
        self.assertEqual((found.condition, found.name), (modop.ASKS_LOADOUT, "extras"))
        self.assertTrue(self.said('<Group Skip="1">%s</Group>' % RADIUS).skip)

    def test_its_type_and_whether_it_answers_to_the_mods_options(self):
        self.assertEqual(self.said(RADIUS).kind, "replace")
        self.assertEqual(self.said(SEVERAL).kind, "merge")
        self.assertEqual(self.said('<Group Condition="#m">%s</Group>' % RADIUS).kind, "group")
        self.assertEqual(self.said("<Asset><Template>T</Template></Asset>", games.ANNO_117).kind, "add")
        self.assertFalse(self.said(RADIUS).options)
        answering = (
            RADIUS.replace("<ModOp ", '<ModOp ModOpID="bigger" '),
            RADIUS.replace("<ModOp ", '<ModOp Condition="$roomier" '),
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building">'
            '<Radius><ModValue Insert="self::node() * $factor"/></Radius></ModOp>',
            '<Group ModOpID="extras">%s</Group>' % RADIUS,
        )
        for body in answering:
            with self.subTest(body=body):
                self.assertTrue(self.said(body).options)

    def test_an_asset_straight_under_modops_is_what_the_reading_made_of_it(self):
        """An add in Anno 117 and nothing read anywhere else, as the reading
        of the file has it: the kept text was called an add for any game, so
        an Anno 1800 op set aside as not a ModOp was a kept add once kept."""
        body = "<Asset><Values/></Asset>"
        for game, kind in ((games.ANNO_117, "add"), (games.ANNO_1800, ""), ("", "")):
            with self.subTest(game=game):
                root = etree.fromstring(modops_file(body).encode("utf-8"))
                (op,) = modops.parse_ops(root, [], "assets.xml", game=game)
                self.assertEqual(modops.describe(op).kind, kind)
                self.assertEqual(self.said(op.verbatim, game).kind, kind)

    def test_text_that_is_no_op_has_nothing_said_of_it(self):
        self.assertTrue(self.said("<not xml").constant)
        self.assertTrue(self.said("<ImyaTweaks />").constant)


class TestAValueWrittenOverLinesWithCommentsInIt(unittest.TestCase):
    """*Add Collection* binds a key to a console command a line, a comment
    after each.  Its text is then in the tails of those comments, and laid
    out afresh every line of the value gained two spaces."""

    OP = (
        '<ModOp Type="add" GUID="1000" Path="/Values/Building">\n'
        "    <Command>\n"
        "        one();  <!-- the first -->\n"
        "        two();  <!-- the second -->\n"
        "    </Command>\n"
        "</ModOp>"
    )

    def value(self, text):
        node = etree.fromstring(text.encode("utf-8")).find(".//Command")
        return (node.text or "") + "".join(child.tail or "" for child in node)

    def test_it_goes_out_as_it_is_held(self):
        written = "\n".join(generator._verbatim_lines(self.OP))
        self.assertEqual(self.value(written), self.value(self.OP))


class TestGroupsInTheList(TestTheListOnScreen):
    BODY = (
        '<Group Condition="@1000/Building">'
        + RADIUS
        + SEVERAL.replace("25", "40")
        + '<ModOp Type="replace" GUID="9999" Path="/Values/Nope"><Nope>1</Nope></ModOp>'
        + "</Group>"
    )

    def test_a_group_is_a_row_with_what_is_in_it_under_it(self):
        _loadout, panel = self.panel(Mod=self.BODY)
        group, several = self.rows(panel, "op")
        self.assertIs(several.parent(), group)
        self.assertEqual(group.text(1), "runs \u00b7 3 changes")
        # The plain op is its change's row, under the group and not the mod.
        plain = [row for row in self.rows(panel, "change") if row.text(1).endswith("25")]
        self.assertEqual(len(plain), 1)
        self.assertIs(plain[0].parent().parent(), group)
        # And the op the Studio could not read is under the group it is in.
        (unread,) = self.rows(panel, "unread")
        self.assertIs(unread.parent(), group)

    def test_one_that_does_not_run_says_so(self):
        _loadout, panel = self.panel(
            Mod='<Group Condition="@1000/Nothing">%s</Group>' % SEVERAL
        )
        group, _op = self.rows(panel, "op")
        self.assertEqual(group.text(1), "does not run")

    def test_its_box_switches_everything_in_it(self):
        from PySide6.QtCore import Qt

        loadout, panel = self.panel(Mod=self.BODY)
        group, several = self.rows(panel, "op")
        group.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual([op.active for op in loadout.ops], [False, False, False])
        self.assertEqual({change.active for change in loadout.changes.values()}, {False})
        self.assertEqual(several.checkState(0), Qt.CheckState.Unchecked)
        group.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual([op.active for op in loadout.ops], [True, True, True])
        self.assertEqual(several.checkState(0), Qt.CheckState.Checked)


class TestOpsThatChangeNothingAreOneRowToOpen(TestTheListOnScreen):
    SAME_NAME = (
        '<ModOp Type="merge" GUID="1001" Path="/Values/Building">'
        "<BuildingType>Farm</BuildingType></ModOp>"
    )

    def test_several_are_folded_under_one(self):
        _loadout, panel = self.panel(Mod=SAME_TYPE + self.SAME_NAME + SEVERAL)
        (fold,) = self.rows(panel, "idle")
        self.assertEqual(fold.text(0), "2 ModOps that change nothing as things stand")
        self.assertFalse(fold.isExpanded())
        inside = [row for row in self.rows(panel, "op") if row.parent() is fold]
        self.assertEqual(len(inside), 2)
        self.assertEqual(len(self.rows(panel, "op")), 3)

    def test_one_alone_is_not(self):
        _loadout, panel = self.panel(Mod=SAME_TYPE + SEVERAL)
        self.assertEqual(self.rows(panel, "idle"), [])

    def test_opened_it_stays_open_when_the_list_is_drawn_again(self):
        _loadout, panel = self.panel(Mod=SAME_TYPE + self.SAME_NAME)
        (fold,) = self.rows(panel, "idle")
        fold.setExpanded(True)
        panel.refresh()
        panel.build_every_row()
        (fold,) = self.rows(panel, "idle")
        self.assertTrue(fold.isExpanded())


class TestTypingOverAPlainOpsValue(ListedCase):
    """The op is retuned where it stands, and no exception is put after it."""

    def edit(self, loadout, new, old="10"):
        from anno_modding_studio.core.changes import Change

        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
            old_value=old, new_value=new, origin="Mod",
        )
        loadout.set_change(loadout.place(change))

    def test_the_edit_takes_the_ops_place(self):
        loadout = self.loadout(
            Mod=SAME_TYPE + "<!-- bigger -->" + RADIUS + SEVERAL.replace("Radius", "BuildingType")
        )
        self.edit(loadout, "30")
        self.assertEqual(len(loadout.ops), 2)
        self.assertEqual(len(loadout.changes), 3)
        (change,) = [c for c in loadout.changes.values() if not c.op_uid]
        self.assertEqual(
            (change.new_value, change.exception_of, change.order, change.note),
            ("30", "", 1, "bigger"),
        )
        text = generator.build_modops_xml(
            generator.for_mod(loadout, "Mod"),
        )
        written = [n for n in etree.fromstring(text.encode("utf-8")) if isinstance(n.tag, str)]
        self.assertEqual(len(written), 3)
        self.assertEqual(text.count("<Radius>"), 1)
        self.assertEqual(written[1].findtext("Radius"), "30")

    def test_typing_the_games_value_back_takes_the_op_out(self):
        loadout = self.loadout(Mod=RADIUS)
        self.edit(loadout, "10")
        self.assertEqual((loadout.ops, list(loadout.changes)), ([], []))

    GROUPED = '<Group Condition="@1000/Building"><!-- bigger -->%s</Group>' % RADIUS

    def written(self, loadout) -> str:
        return generator.build_modops_xml(generator.for_mod(loadout, "Mod"))

    def test_in_a_group_the_op_is_typed_over_where_it_stands(self):
        loadout = self.loadout(Mod=self.GROUPED + SAME_TYPE)
        group, op, _after = loadout.ops
        self.edit(loadout, "30")
        self.assertEqual([held.uid for held in loadout.ops], [group.uid, op.uid, _after.uid])
        retuned = loadout.op(op.uid)
        self.assertEqual((retuned.within, retuned.order, retuned.note), ([group.uid], op.order, "bigger"))
        self.assertTrue(keptops.same_op(retuned.source, RADIUS.replace("25", "30")))
        self.assertEqual(retuned.summary, "replace Radius = 30")
        (change,) = [c for c in loadout.changes.values() if c.path.endswith("Radius")]
        self.assertEqual(
            (change.new_value, change.op_uid, change.exception_of, change.order),
            ("30", op.uid, "", op.order),
        )
        text = self.written(loadout)
        written = etree.fromstring(text.encode("utf-8"))
        (inside,) = written.find("Group").iter("Radius")
        self.assertEqual(inside.text, "30")
        self.assertEqual(text.count("<Radius>"), 1)

    def test_typed_over_again_it_is_still_the_one_op(self):
        loadout = self.loadout(Mod=self.GROUPED)
        self.edit(loadout, "30")
        self.edit(loadout, "40")
        _group, op = loadout.ops
        self.assertTrue(keptops.same_op(op.source, RADIUS.replace("25", "40")))
        self.assertEqual([c.new_value for c in loadout.changes.values()], ["40"])
        self.assertEqual(self.written(loadout).count("<Radius>"), 1)

    def test_in_a_group_typing_the_games_value_back_takes_the_op_out(self):
        loadout = self.loadout(Mod=self.GROUPED)
        self.edit(loadout, "10")
        (group,) = loadout.ops
        self.assertTrue(group.scope)
        self.assertEqual(list(loadout.changes), [])

    def test_undo_puts_the_value_it_had_back(self):
        from anno_modding_studio.core.undo import UndoHistory

        loadout = self.loadout(Mod=self.GROUPED)
        history = UndoHistory(loadout)
        self.edit(loadout, "30")
        history.commit("type over it")
        history.undo()
        _group, op = loadout.ops
        self.assertTrue(keptops.same_op(op.source, RADIUS))
        self.assertEqual([c.new_value for c in loadout.changes.values()], ["25"])
        history.redo()
        _group, op = loadout.ops
        self.assertTrue(keptops.same_op(op.source, RADIUS.replace("25", "30")))

    def test_what_is_added_in_an_op_laid_out_over_lines_is_laid_out_as_it(self):
        from anno_modding_studio.core import effective
        from anno_modding_studio.core.effective import payload_child_path

        loadout = self.loadout(Mod=(
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building">\n'
            "  <Lofts>\n    <Width>2</Width>\n  </Lofts>\n"
            "  <Attics>\n    <!-- low -->\n    <Width>1</Width>\n  </Attics>\n"
            "</ModOp>"
        ))
        (op,) = loadout.ops
        lofts, attics = sorted(loadout.changes_by_op(op.uid), key=lambda change: change.tag)[::-1]
        height = etree.Element("Height")
        height.text = "3"
        self.assertTrue(keptops.add_into(loadout, lofts, "", height))
        room = etree.Element("Room")
        etree.SubElement(room, "Size").text = "4"
        self.assertTrue(keptops.add_into(loadout, lofts, "", room))
        # The payload has no comment in it: its Width is its first.
        self.assertTrue(keptops.take_out_of(loadout, attics, payload_child_path("", 0)))
        source = loadout.op(op.uid).source
        self.assertIn(
            "<Lofts>\n    <Width>2</Width>\n    <Height>3</Height>\n"
            "    <Room>\n      <Size>4</Size>\n    </Room>\n  </Lofts>",
            source,
        )
        self.assertIn("<Attics>\n    <!-- low -->\n  </Attics>", source)
        self.assertEqual(
            [effective.parse_payload(change).findtext("Width") for change in (lofts, attics)],
            ["2", None],
        )

    def test_over_any_other_ops_value_it_is_still_the_exception_after_it(self):
        loadout = self.loadout(Mod=WIDE)
        self.edit(loadout, "30")
        (op,) = loadout.ops
        self.assertTrue(keptops.same_op(op.source, WIDE))
        exception = [c for c in loadout.changes.values() if c.exception_of]
        self.assertEqual([c.new_value for c in exception], ["30"])


class TestAnOpKeyedWhereTheStudioLeavesItBare(ListedCase):
    """An op is one with its change where it keys a list entry the Studio's
    path for the change leaves bare, since the entry has no twin as the op
    runs: the two name the same node.  The change keeps the Studio's
    spelling, which conflicts and the editor find it by."""

    # A mod loading first gives beta a cost list one entry long, as the
    # Shrine of Mars ships its FunctionalEffects.
    EARLY = (
        '<ModOp Type="merge" GUID="1001" Path="/Values">'
        "<Cost><Costs><Item><Ingredient>1010</Ingredient><Amount>3</Amount></Item></Costs></Cost>"
        "</ModOp>"
    )
    KEYED = "/Values/Cost/Costs/Item[Ingredient='1010']"
    BARE = "/Values/Cost/Costs/Item"
    SETS = '<ModOp Type="replace" GUID="1001" Path="%s/Amount"><Amount>%s</Amount></ModOp>'

    def read(self, **folders):
        return self.loadout(**{"A Early": self.EARLY, **folders})

    def answer(self, body):
        loadout = self.read(Mod=body)
        (op,) = [held for held in loadout.ops if held.origin == "Mod" and not held.scope]
        (change,) = loadout.changes_by_op(op.uid)
        return loadout, op, change

    def test_a_removal_keyed_where_the_entry_is_alone(self):
        _loadout, op, change = self.answer('<ModOp Type="remove" GUID="1001" Path="%s"/>' % self.KEYED)
        self.assertEqual(change.path, self.BARE)
        self.assertTrue(op.plain)

    def test_and_a_value_in_it(self):
        _loadout, op, change = self.answer(self.SETS % (self.KEYED, "6"))
        self.assertEqual((change.path, change.new_value), (self.BARE + "/Amount", "6"))
        self.assertTrue(op.plain)

    def test_in_anno_117s_short_form_and_in_a_group(self):
        _loadout, op, change = self.answer(
            '<Group Condition="@1001/Building">'
            "<ModOp Remove=\"@1001/Cost/Costs/Item[Ingredient='1010']\"/></Group>"
        )
        self.assertEqual(change.path, self.BARE)
        self.assertTrue(op.plain and op.within)

    def test_keyed_by_place_too(self):
        _loadout, op, change = self.answer('<ModOp Type="remove" GUID="1001" Path="%s[1]"/>' % self.BARE)
        self.assertEqual(change.path, self.BARE)
        self.assertTrue(op.plain)

    def test_two_mods_setting_it_each_their_way_still_meet(self):
        loadout = self.read(**{"B Keyed": self.SETS % (self.KEYED, "6"), "C Bare": self.SETS % (self.BARE, "9")})
        (clash,) = conflicts.find(loadout)
        self.assertEqual((clash.path, clash.kind), (self.BARE + "/Amount", conflicts.OVERWRITES))

    def typed(self, loadout, value):
        from anno_modding_studio.core.changes import Change

        loadout.set_change(loadout.place(Change(
            guid="1001", asset_label="beta", path=self.BARE + "/Amount", tag="Amount",
            old_value="3", new_value=value, origin="Mod",
        )))

    def test_typed_over_in_a_group_it_is_typed_into_the_op_and_keeps_its_key(self):
        loadout, op, _change = self.answer('<Group Condition="@1001/Building">%s</Group>' % (self.SETS % (self.KEYED, "6")))
        self.typed(loadout, "7")
        self.assertTrue(keptops.same_op(loadout.op(op.uid).source, self.SETS % (self.KEYED, "7")))
        self.assertEqual(
            [(c.path, c.new_value, c.op_uid, c.exception_of) for c in loadout.changes.values() if c.origin == "Mod"],
            [(self.BARE + "/Amount", "7", op.uid, "")],
        )

    def test_typed_over_elsewhere_it_is_the_studios_where_the_op_was(self):
        loadout, op, _change = self.answer(self.SETS % (self.KEYED, "6"))
        self.typed(loadout, "7")
        self.assertIsNone(loadout.op(op.uid))
        self.assertEqual(
            [(c.path, c.new_value, c.exception_of, c.order) for c in loadout.changes.values() if c.origin == "Mod"],
            [(self.BARE + "/Amount", "7", "", op.order)],
        )

    def test_what_names_another_node_is_not_one_with_it(self):
        names = reader._names_it_as
        self.assertTrue(names(self.BARE, self.KEYED))
        self.assertTrue(names(self.BARE + "/Amount", "/Cost/Costs/Item[Ingredient='1010']/Amount"))
        # Keyed by the Studio another way, or another step, or another depth.
        self.assertFalse(names("/Values/Cost/Costs/Item[Ingredient='1011']", self.KEYED))
        self.assertFalse(names(self.BARE + "/Amount", self.KEYED + "/Rare"))
        self.assertFalse(names(self.BARE, self.KEYED + "/Amount"))
        self.assertFalse(names(self.BARE, "//Item[Ingredient='1010']"))


class TestAnOpIsDrawn(TestTheListOnScreen):
    """A badge for its type and a strip for what is worked out at load."""

    def shape(self, body):
        loadout = self.loadout(Mod=body)
        (view,) = keptops.of(loadout, "Mod")
        return keptops.shape(view, "anno1800")

    def test_the_shape_of_each_kind(self):
        search = '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
        nothing = '<ModOp Type="merge" Path="//Values/Nothing"><Radius>30</Radius></ModOp>'
        asked = TestAConditionIsKeptWithItsOp
        cases = {
            RADIUS: ("replace", False, (0, 0, 0, 0)),
            search: ("merge", False, (1, 0, 0, 0)),
            nothing: ("merge", False, (2, 0, 0, 0)),
            TestWhatAnOpIsSaidToBe.COPY: ("replace", False, (0, 1, 0, 0)),
            asked.HOLDS: ("merge", False, (0, 0, 1, 0)),
            asked.FAILS: ("merge", False, (0, 0, 2, 0)),
            RADIUS.replace("<ModOp ", '<ModOp Skip="1" ModOpID="x" '): ("replace", True, (0, 0, 0, 1)),
        }
        for body, wanted in cases.items():
            with self.subTest(body=body):
                self.assertEqual(self.shape(body), wanted)

    def test_a_condition_the_studio_cannot_say_of(self):
        """It has one, it came to nothing, and not because the condition failed."""
        held = (
            "<ModOp Type=\"merge\" GUID=\"1000\" Path=\"/Values/Building\" "
            "Condition=\"@1000/Building[Radius = '10']\"><Radius>10</Radius></ModOp>"
        )
        self.assertEqual(self.shape(held)[2][2], 3)

    def test_a_row_that_is_an_op_wears_a_drawing_and_what_it_comes_to_does_not(self):
        from anno_modding_studio.ui import op_marks

        _loadout, panel = self.panel(Mod=RADIUS + SEVERAL.replace("Radius", "BuildingType"))
        (op,) = self.rows(panel, "op")
        self.assertEqual(op.icon(0).actualSize(op_marks.icon_size()).width(), op_marks.width(True))
        plain = [row for row in self.rows(panel, "change") if row.parent() is not op]
        under = [row for row in self.rows(panel, "change") if row.parent() is op]
        self.assertEqual((len(plain), len(under)), (1, 2))
        self.assertEqual(
            plain[0].icon(0).actualSize(op_marks.icon_size()).width(), op_marks.width(False)
        )
        for row in under:
            self.assertTrue(row.icon(0).isNull())

    def test_each_drawing_is_made_once_and_again_after_the_theme_changes(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks

        QApplication.instance() or QApplication([])
        first = op_marks.icon("merge", False, (1, 0, 2, 1))
        self.assertIs(op_marks.icon("merge", False, (1, 0, 2, 1)), first)
        self.assertIsNot(op_marks.icon("merge", True, (1, 0, 2, 1)), first)
        op_marks.forget()
        self.assertIsNot(op_marks.icon("merge", False, (1, 0, 2, 1)), first)

    def test_the_words_leave_out_what_the_badge_says(self):
        cases = {
            SEVERAL: "2 assets \u203a Building: Radius = 25",
            WIDE.replace("25", "40") + RADIUS: None,
            '<ModOp Type="remove" GUID="1000,1001" Path="/Values/Building/Radius" />':
                "2 assets \u203a Building \u203a Radius",
            '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>':
                "Radius = 30",
        }
        for body, wanted in cases.items():
            if wanted is None:
                continue
            with self.subTest(body=body):
                _loadout, panel = self.panel(Mod=body)
                (row,) = self.rows(panel, "op")
                self.assertEqual(row.text(0), wanted)

    def test_a_field_that_is_the_node_named_is_said_once(self):
        both = SEVERAL.replace('Path="/Values/Building"', 'Path="/Values/Building/Radius"').replace(
            'Type="merge"', 'Type="replace"')
        _loadout, panel = self.panel(Mod=both)
        (row,) = self.rows(panel, "op")
        self.assertEqual(row.text(0), "2 assets \u203a Building \u203a Radius = 25")

    def test_an_op_on_one_asset_says_which_and_has_no_row_for_it(self):
        """Its words were a path in nobody's asset, and the asset was a row
        of its own between it and its changes."""
        _loadout, panel = self.panel(Mod=WIDE)
        (row,) = self.rows(panel, "op")
        self.assertTrue(row.text(0).startswith("alpha \u203a Building"), row.text(0))
        self.assertEqual(self.rows(panel, "group"), [])
        self.assertTrue(self.rows(panel, "change"))
        for change in self.rows(panel, "change"):
            self.assertIs(change.parent(), row)

    def test_anno_117s_shorthand_is_one_row_as_the_longhand_is(self):
        """``Replace="@1000/Building/Radius"`` leaves out the ``/Values`` its
        change's path has, so it was never taken for one change at the node it
        names: a row for the op, over one for the asset, over its change."""
        short = '<ModOp Replace="@1000/Building/Radius"><Radius>25</Radius></ModOp>'
        _loadout, panel = self.panel(Mod=short)
        self.assertEqual(self.rows(panel, "op"), [])
        self.assertEqual(len(self.rows(panel, "change")), 1)

    def test_a_rows_warning_is_kept_beside_its_badge(self):
        """Left of its arrow, so the badge has the place the glyph had."""
        from anno_modding_studio.ui.changes_dock import STATUS_ROLE

        loadout, panel = self.panel(Mod=RADIUS)
        (row,) = self.rows(panel, "change")
        (change,) = loadout.changes.values()
        change.positional = True
        panel._paint(row, change)
        self.assertEqual(row.data(0, STATUS_ROLE), "warn")
        self.assertFalse(row.icon(0).isNull())
        change.positional = False
        panel._paint(row, change)
        self.assertIsNone(row.data(0, STATUS_ROLE))
        self.assertFalse(row.icon(0).isNull())
        # And it is drawn without complaint, arrow or none.
        panel.resize(600, 300)
        panel.grab()

    def test_the_drawings_are_explained_where_the_list_is(self):
        from anno_modding_studio.ui import op_marks

        _loadout, panel = self.panel(Mod=RADIUS)
        rows = [entry for entry in op_marks.legend() if entry is not None]
        self.assertEqual(len(rows), 15)
        for image, words in rows:
            self.assertFalse(image.isNull())
            self.assertTrue(words)
        panel.legend_tip.toggle()
        self.addCleanup(panel.legend_tip.toggle)
        self.assertTrue(panel.legend_tip.is_open())

    def test_a_badge_the_colour_of_the_tick_box_is_moved_off_it(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks, theme

        app = QApplication.instance() or QApplication([])
        was = theme.scheme()
        self.addCleanup(lambda: theme.apply(app, was))
        theme.apply(app, theme.SCHEMES["quartz"])
        # Quartz's accent is the blue of a replace.
        self.assertNotEqual(op_marks._tone("replace"), theme.OP_HUES[(False, True)][1])
        self.assertEqual(op_marks._tone("remove"), theme.OP_HUES[(False, True)][2])

    def test_a_red_beside_a_gold_box_is_not_near_it(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks, theme

        app = QApplication.instance() or QApplication([])
        was = theme.scheme()
        self.addCleanup(lambda: theme.apply(app, was))
        for key in ("granite", "brick"):
            theme.apply(app, theme.SCHEMES[key])
            self.assertEqual(op_marks._tone("remove"), theme.OP_HUES[(True, False)][2])

    def test_every_badge_stands_out_against_the_list_in_every_scheme(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks, theme

        app = QApplication.instance() or QApplication([])
        was = theme.scheme()
        self.addCleanup(lambda: theme.apply(app, was))
        for key, scheme in theme.SCHEMES.items():
            theme.apply(app, scheme)
            for verb in ("add", "replace", "remove", "merge", "group"):
                with self.subTest(scheme=key, verb=verb):
                    # Its sign is drawn in the background's colour, on it.
                    ratio = theme.contrast_ratio(op_marks._tone(verb), scheme["bg"])
                    self.assertGreaterEqual(round(ratio, 2), 3)

    def test_a_merge_is_not_drawn_in_a_groups_colour(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks, theme

        app = QApplication.instance() or QApplication([])
        was = theme.scheme()
        self.addCleanup(lambda: theme.apply(app, was))
        for key, scheme in theme.SCHEMES.items():
            theme.apply(app, scheme)
            merge, group = QColor(op_marks._tone("merge")), QColor(op_marks._tone("group"))
            with self.subTest(scheme=key):
                self.assertGreater(merge.hslSaturationF(), 0.4)
                if group.hslSaturationF() > 0.15:
                    self.assertGreater(op_marks._apart(merge.hslHue(), group.hslHue()), 60)

    def test_on_a_selected_row_every_mark_holds_against_the_rows_fill(self):
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks, theme

        app = QApplication.instance() or QApplication([])
        was = theme.scheme()
        self.addCleanup(lambda: theme.apply(app, was))
        for key, scheme in theme.SCHEMES.items():
            theme.apply(app, scheme)
            fill, _words = op_marks._on_selection()
            for token in ("rule", "text", "warn", "ok", "text-dim"):
                with self.subTest(scheme=key, token=token):
                    ink = op_marks._ink(scheme[token], True)
                    self.assertGreaterEqual(round(theme.contrast_ratio(ink, fill), 2), 3)
        # Obsidian's blue lens on its yellow: the selected version is its own.
        theme.apply(app, theme.SCHEMES["obsidian"])
        made = op_marks.icon("merge", False, (1, 0, 0, 0))
        size = op_marks.icon_size()
        self.assertNotEqual(
            made.pixmap(size, QIcon.Mode.Normal).toImage(),
            made.pixmap(size, QIcon.Mode.Selected).toImage(),
        )

    def test_every_type_and_every_state_can_be_drawn(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import op_marks

        QApplication.instance() or QApplication([])
        for verb in ("add", "addNextSibling", "addPrevSibling", "remove", "replace", "merge",
                     "group", "something else"):
            for hollow in (False, True):
                self.assertFalse(op_marks.icon(verb, hollow).isNull())
        for slots in ((1, 1, 1, 1), (2, 0, 2, 0), (0, 0, 3, 0)):
            self.assertFalse(op_marks.icon("merge", False, slots).isNull())


SWITCH = '<ImyaExpose Path="*" Kind="SkipToggle" InitState="True" ModOpID="%s" ExposeID="%s" />'
TWEAKED = (
    '<ImyaTweaks Title="Radius tweaks" Description="How far things reach." />'
    + '<Group ModOpID="Kept">%s</Group>' % RADIUS
    + '<Group ModOpID="Off" Skip="1">%s</Group>' % SAME_TYPE
    + '<Group ModOpID="Lonely">%s</Group>' % SEVERAL.replace("25", "26")
    + '<Group ModOpID="LonelyOff" Skip="1">%s</Group>' % ALREADY
    + SWITCH % ("Kept", "Keep the radius")
    + SWITCH % ("Off", "Switch the other")
    + SWITCH % ("Kept", "Switch the lonely one")
)


class TestAGroupATweakToolSwitches(ListedCase):
    """A group kept as a group says of itself what a group inside one kept
    whole has said: the option that switches it, and where the mod's switches
    for it have gone wrong."""

    panel = TestTheListOnScreen.panel
    rows = TestTheListOnScreen.rows

    def groups(self):
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        loadout, panel = self.panel(Mod=TWEAKED)
        rows = {}
        for row in self.rows(panel, "op"):
            op = loadout.op(row.data(0, OP_ROLE))
            if op is not None and op.scope:
                rows[op.tool.get("id")] = row
        return rows

    def test_its_row_has_the_name_the_mods_options_give_it(self):
        rows = self.groups()
        self.assertEqual(rows["Kept"].text(0), "Keep the radius")
        self.assertEqual(rows["Off"].text(0), "Switch the other")
        self.assertIn("Lonely", rows["Lonely"].text(0))

    def test_and_says_what_it_is_called_in_the_file_and_where_it_is_switched(self):
        tip = self.groups()["Kept"].toolTip(0)
        self.assertIn('ModOpID="Kept" in the mod\'s file', tip)
        self.assertIn("Mod options", tip)

    def test_a_second_switch_naming_it_is_said(self):
        rows = self.groups()
        self.assertIn("one more switch naming this group: Switch the lonely one", rows["Kept"].toolTip(0))
        self.assertNotIn("more switch", rows["Off"].toolTip(0))

    def test_one_switched_off_says_where_it_is_switched_on(self):
        tip = self.groups()["Off"].toolTip(0)
        self.assertIn("Switched off in the mod, so nothing in it runs", tip)

    def test_one_tagged_with_no_switch_is_said_on_or_off(self):
        rows = self.groups()
        self.assertIn("offers no switch for it", rows["Lonely"].toolTip(0))
        self.assertIn("stops running once", rows["Lonely"].toolTip(0))
        self.assertIn("none of the mod's options switches it on", rows["LonelyOff"].toolTip(0))
        self.assertNotIn("offers no switch", rows["Kept"].toolTip(0))

    def test_it_is_found_by_either_name(self):
        from anno_modding_studio.ui.changes_dock import FIND_ROLE

        found = self.groups()["Kept"].data(0, FIND_ROLE)
        self.assertIn("keep the radius", found)
        self.assertIn("kept", found.split())

    def test_a_group_no_tweak_tool_finds_says_none_of_it(self):
        _loadout, panel = self.panel(Mod=IN_A_GROUP)
        (row, *_inside) = self.rows(panel, "op")
        self.assertIn("group", row.text(0))
        self.assertNotIn("iModYourAnno", row.toolTip(0))
        self.assertNotIn("in the mod's file", row.toolTip(0))


NOWHERE = '<ModOp Type="replace" GUID="1000" Path="/Values/Nope/Radius"><Radius>25</Radius></ModOp>'
IN_A_GROUP = '<Group Condition="!#another_mod">%s</Group>' % SEVERAL.replace("25", "26")


class TestImportingOneModOpByOp(SessionCase):
    """The dialog that reads one mod, where a row is the op itself."""

    BODY = ALREADY + RADIUS + IN_A_GROUP + NOWHERE

    def dialog(self, body=None):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        QApplication.instance() or QApplication([])
        root = self.mods(Mod=self.BODY if body is None else body)
        dialog = ModImportDialog(self.index, None, str(root / "Mod"))
        self.addCleanup(dialog.deleteLater)
        dialog.game = dialog.report.game_hint or dialog.game
        dialog._update_button()
        return dialog

    def row(self, dialog, says):
        (found,) = [
            row for row in range(dialog.ops_table.rowCount())
            if dialog.ops_table.item(row, 1).text().strip() == says
        ]
        return found

    def tick(self, dialog, says, on):
        from PySide6.QtCore import Qt

        dialog.ops_table.item(self.row(dialog, says), 0).setCheckState(
            Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        )

    def ticked(self, dialog):
        from PySide6.QtCore import Qt

        return [
            dialog.ops_table.item(row, 1).text().strip()
            for row in range(dialog.ops_table.rowCount())
            if dialog.ops_table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def taken(self, dialog):
        with mock.patch.object(dialog, "accept"):
            dialog._accept()
        return dialog

    def test_every_op_that_was_read_can_be_taken_or_left(self):
        from PySide6.QtCore import Qt

        dialog = self.dialog()
        can = [
            bool(dialog.ops_table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
            for row in range(dialog.ops_table.rowCount())
        ]
        # The one that changes nothing today and the group among them, and
        # not the one the Studio could not read.
        self.assertEqual(can, [True, True, True, True, False])
        self.assertEqual(len(self.ticked(dialog)), 4)

    def test_a_row_says_whether_it_was_read_and_what_it_comes_to(self):
        dialog = self.dialog()
        table = dialog.ops_table
        self.assertEqual(table.horizontalHeaderItem(4).text(), "Comes to")
        self.assertEqual(
            [table.item(row, 3).text() for row in range(table.rowCount())],
            ["No change today", "Read", "Runs today", "Read", "Needs a look"],
        )
        self.assertEqual(
            dialog.status.text(),
            "3 changes from 3 ModOps; 1 changes nothing today; 1 need a look",
        )
        self.assertEqual(table.item(1, 4).text(), "1 change")
        self.assertEqual(table.item(3, 4).text(), "2 changes")
        self.assertIn("runs only if", table.item(2, 4).text())

    def test_nothing_is_said_to_have_become_anything(self):
        dialog = self.dialog('<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>')
        self.assertEqual(dialog.ops_table.item(0, 4).text(), "2 changes")
        self.assertNotIn("became", dialog.report.results[0].reason)

    def test_an_op_in_a_group_is_set_in_under_it_and_each_is_drawn(self):
        dialog = self.dialog()
        table = dialog.ops_table
        self.assertTrue(table.item(3, 1).text().startswith("    "))
        self.assertFalse(table.item(2, 1).text().startswith(" "))
        for row in range(table.rowCount()):
            self.assertFalse(table.item(row, 1).icon().isNull())

    def test_the_button_counts_the_ops_and_not_their_group(self):
        dialog = self.dialog()
        self.assertEqual(dialog.import_button.text(), "Import 3 ModOps")
        self.assertTrue(dialog.import_button.isEnabled())

    def test_taken_whole_it_is_every_op_as_written_with_its_group(self):
        dialog = self.taken(self.dialog())
        self.assertEqual(dialog.taken, 3)
        self.assertEqual(len(dialog.ops), 4)
        group, inside = dialog.ops[2], dialog.ops[3]
        self.assertTrue(group.scope)
        self.assertEqual(inside.within, [group.uid])
        self.assertEqual(len(dialog.changes), 3)
        self.assertEqual({c.op_uid for c in dialog.changes}, {dialog.ops[1].uid, inside.uid})
        # The one not read goes with the mod, as it does read narrow.
        self.assertEqual(len(dialog.unread), 1)

    def test_an_op_left_behind_leaves_its_changes_too(self):
        dialog = self.dialog()
        self.tick(dialog, "replace @1000/Values/Building/Radius", False)
        self.assertEqual(dialog.import_button.text(), "Import 2 ModOps")
        self.taken(dialog)
        self.assertEqual(len(dialog.ops), 3)
        self.assertEqual(len(dialog.changes), 2)
        self.assertEqual({c.op_uid for c in dialog.changes}, {dialog.ops[-1].uid})

    def test_an_op_that_changes_nothing_today_is_still_the_mods(self):
        dialog = self.dialog(ALREADY)
        self.assertEqual(dialog.import_button.text(), "Import 1 ModOp")
        self.assertTrue(dialog.import_button.isEnabled())
        self.taken(dialog)
        self.assertEqual(dialog.changes, [])
        (op,) = dialog.ops
        self.assertTrue(keptops.same_op(op.source, ALREADY))

    def test_leaving_a_group_leaves_everything_in_it(self):
        dialog = self.dialog()
        self.tick(dialog, "<Group Condition>", False)
        self.assertEqual(
            self.ticked(dialog),
            ["merge @1000/Values/Building", "replace @1000/Values/Building/Radius"],
        )
        self.taken(dialog)
        self.assertFalse(any(op.scope or op.within for op in dialog.ops))

    def test_taking_an_op_takes_the_group_it_is_in(self):
        dialog = self.dialog()
        self.tick(dialog, "<Group Condition>", False)
        self.tick(dialog, "merge @1000,1001/Values/Building", True)
        self.assertIn("<Group Condition>", self.ticked(dialog))
        # And leaving the op alone leaves the group where it is.
        self.tick(dialog, "merge @1000,1001/Values/Building", False)
        self.assertIn("<Group Condition>", self.ticked(dialog))

    def test_a_group_that_does_not_run_says_so(self):
        dialog = self.dialog('<Group Condition="#another_mod">%s</Group>' % RADIUS)
        table = dialog.ops_table
        self.assertEqual(
            [table.item(row, 3).text() for row in range(2)],
            ["Does not run today", "No change today"],
        )
        self.assertEqual(dialog.status.text(), "0 changes from 1 ModOp; 1 changes nothing today")
        self.assertEqual(dialog.import_button.text(), "Import 1 ModOp")

    def test_settling_is_not_asked_about(self):
        from PySide6.QtWidgets import QCheckBox

        dialog = self.dialog()
        asked = [box.text().replace("&", "") for box in dialog.findChildren(QCheckBox)]
        self.assertIn("Keep inert removals", asked)
        self.assertFalse([text for text in asked if "ettle" in text])


class TestImportingAModASecondTime(SessionCase):
    BODY = TestImportingOneModOpByOp.BODY
    dialog = TestImportingOneModOpByOp.dialog
    taken = TestImportingOneModOpByOp.taken

    def loadout(self):
        from anno_modding_studio.core.changes import Loadout

        return Loadout()

    def into(self, loadout, dialog):
        self.taken(dialog)
        matched = loadout.as_before(dialog.ops, dialog.changes, dialog.unread)
        for op in dialog.ops:
            loadout.set_op(op)
        for change in dialog.changes:
            already = loadout.equivalent(change)
            if already is not None:
                change.uid = already.uid
            loadout.set_change(change)
        return matched

    def test_its_ops_are_the_ones_already_here(self):
        loadout = self.loadout()
        self.assertEqual(self.into(loadout, self.dialog()), 0)
        first = [op.uid for op in loadout.ops]
        changes = len(loadout.changes)
        self.assertEqual(self.into(loadout, self.dialog()), 4)
        self.assertEqual([op.uid for op in loadout.ops], first)
        self.assertEqual(len(loadout.changes), changes)
        self.assertLessEqual({change.op_uid for change in loadout.changes.values()}, set(first))
        # The op in the group is still in the group that is here.
        self.assertEqual(loadout.ops[3].within, [loadout.ops[2].uid])

    def test_an_op_written_twice_is_still_two(self):
        loadout = self.loadout()
        self.into(loadout, self.dialog(ALREADY + ALREADY))
        self.assertEqual(len(loadout.ops), 2)
        self.assertEqual(self.into(loadout, self.dialog(ALREADY + ALREADY)), 2)
        self.assertEqual(len(loadout.ops), 2)

    def test_the_same_op_in_another_group_is_another_op(self):
        loadout = self.loadout()
        self.into(loadout, self.dialog(RADIUS))
        self.assertEqual(self.into(loadout, self.dialog(IN_A_GROUP.replace(SEVERAL.replace("25", "26"), RADIUS))), 0)
        self.assertEqual(len(loadout.ops), 3)

    def test_one_new_to_the_mod_is_added(self):
        loadout = self.loadout()
        self.into(loadout, self.dialog(RADIUS))
        self.assertEqual(self.into(loadout, self.dialog(RADIUS + SAME_TYPE)), 1)
        self.assertEqual(len(loadout.ops), 2)


class TestImportingAFolderAsWritten(SessionCase):
    def dialog(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        QApplication.instance() or QApplication([])
        dialog = ModsFolderDialog(self.index, None, game="anno117")
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_settling_is_not_asked_about(self):
        from PySide6.QtWidgets import QCheckBox

        dialog = self.dialog()
        asked = [box.text().replace("&", "") for box in dialog.findChildren(QCheckBox)]
        self.assertIn("Keep inert removals", asked)
        self.assertFalse([text for text in asked if "ettle" in text])

    def test_a_mod_of_ops_that_change_nothing_today_can_be_imported(self):
        root = self.mods(Mod=ALREADY)
        dialog = self.dialog()
        dialog.session = self.session(root)
        dialog._fill_table()
        dialog._refresh_button()
        self.assertTrue(dialog.load_button.isEnabled())
        with mock.patch.object(dialog, "accept"):
            dialog._accept()
        self.assertEqual(dialog.changes, [])
        self.assertEqual(len(dialog.ops), 1)


class TestAnOpInAGroupShownForAnAsset(ListedCase):
    def test_it_is_written_inside_its_group_and_not_as_though_it_always_ran(self):
        from anno_modding_studio.core import xmlview
        from anno_modding_studio.core.workspace import Workspace

        loadout = self.loadout(Mod=RADIUS + IN_A_GROUP)
        turn = xmlview.turns(Workspace(self.index, loadout), "1000", "")[1]
        # What is typed over holds both, the one in the group as the op: it
        # is said to be a group's by whoever shows it, over the op.
        self.assertEqual(turn.modops.count("<ModOp "), 2)
        self.assertNotIn("<Group", turn.modops)
        self.assertEqual(len(turn.beyond), 1)
        self.assertEqual(xmlview.group_condition(loadout, turn.beyond[0].op), "!#another_mod")
        self.assertEqual(len(turn.reaching), 1)
        for text in (turn.whole, xmlview.also_xml(loadout, turn)):
            self.assertIn('<Group Condition="!#another_mod">', text)
            self.assertLess(text.index("<Group "), text.index('GUID="1000,1001"'))
        self.assertEqual(turn.whole.count("<ModOp "), 2)


PATTERN = '<ModOp Type="merge" Path="//Values/Building"><Radius>30</Radius></ModOp>'
COPIED = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius" '
    'Content="@1001/Building/Radius" />'
)
CONDITIONED = RADIUS.replace("<ModOp ", '<ModOp Condition="!#another_mod" ')


class TestWritingAnOpNarrow(ListedCase):
    """*Write as narrow ModOps*: an op replaced, on asking, by what it comes
    to (phase 4 of docs/proposals/ops-as-written-proposal.md)."""

    def found(self, body):
        loadout = self.loadout(Mod=body)
        return loadout, keptops.narrowings(loadout, "Mod")

    def export(self, loadout):
        return generator.build_modops_xml(
            generator.for_mod(loadout, "Mod"),
        )

    def test_a_wide_replace_becomes_the_one_field_it_changes(self):
        loadout, (found,) = self.found(WIDE)
        self.assertTrue(found.possible)
        self.assertEqual(found.warnings, ())
        (op,) = loadout.ops
        op.note, op.order = "bigger", 7
        self.assertEqual(keptops.write_narrow(loadout, [op.uid]), (1, 1))
        self.assertEqual(loadout.ops, [])
        (change,) = loadout.changes.values()
        self.assertEqual(
            (change.op_uid, change.authored, change.order, change.note, change.new_value),
            ("", True, 7, "bigger", "25"),
        )
        written = self.export(loadout)
        self.assertEqual(written.count("<ModOp "), 1)
        self.assertIn("<Radius>25</Radius>", written)
        self.assertNotIn("BuildingType", written)

    def test_its_comment_is_above_the_first_of_them_only(self):
        """The reader gives every change of a commented op that comment as
        its note, and written narrow it stood above each ModOp."""
        loadout, (found,) = self.found("<!-- bigger -->" + SEVERAL)
        self.assertEqual(found.op.note, "bigger")
        self.assertEqual(keptops.write_narrow(loadout, [found.op.uid]), (1, 2))
        self.assertEqual([change.note for change in loadout.changes.values()], ["bigger", ""])
        written = self.export(loadout)
        self.assertEqual(written.count("<ModOp "), 2)
        self.assertEqual(written.count("bigger"), 1)

    def test_its_comment_is_above_the_first_modop_written(self):
        """The first of its changes in the list is not the first written: a
        removal goes out before a value set, and what adds after both."""
        retyped = WIDE.replace("<Radius>25</Radius>", "").replace("Factory", "Farm")
        cases = {
            "a removal, listed after the value set": (retyped, 2),
            "the same in two assets": (retyped.replace('"1000"', '"1000,1001"'), 3),
            "an entry added, listed before the ones changed": (
                '<ModOp Type="replace" GUID="1000" Path="/Values/Cost/Costs"><Costs>'
                "<Item><Ingredient>1011</Ingredient><Amount>9</Amount></Item>"
                "<Item><Ingredient>1012</Ingredient><Amount>1</Amount></Item>"
                "</Costs></ModOp>", 3),
        }
        for name, (body, changes) in cases.items():
            with self.subTest(name):
                loadout, (found,) = self.found("<!-- bigger -->" + body)
                self.assertEqual(keptops.write_narrow(loadout, [found.op.uid]), (1, changes))
                written = self.export(loadout)
                self.assertEqual(written.count("<ModOp "), changes)
                self.assertEqual(written.count("bigger"), 1)
                self.assertLess(written.index("bigger"), written.index("<ModOp "))

    def test_it_says_how_much_more_than_it_changes_a_replace_claims(self):
        loadout, _found = self.found(WIDE + RADIUS.replace("25", "40"))
        wide, plain = keptops.of(loadout, "Mod")
        self.assertEqual(keptops.claims_more(wide), 1)
        self.assertEqual(keptops.claims_more(plain), 0)
        merge = keptops.of(self.loadout(Mod=SEVERAL), "Mod")[0]
        self.assertEqual(keptops.claims_more(merge), 0)

    def test_a_pattern_can_be_and_says_what_is_given_up(self):
        loadout, (found,) = self.found(PATTERN)
        self.assertTrue(found.possible)
        (warning,) = found.warnings
        self.assertIn("the 2 assets it finds today", warning)
        self.assertEqual(keptops.write_narrow(loadout, [found.op.uid]), (1, 2))
        self.assertEqual(self.export(loadout).count("<ModOp "), 2)
        self.assertNotIn("//Values", self.export(loadout))

    def test_a_copied_value_can_be_and_stops_following(self):
        loadout, (found,) = self.found(COPIED)
        self.assertTrue(found.possible, found.refused)
        self.assertIn("stops following it", found.warnings[0])
        keptops.write_narrow(loadout, [found.op.uid])
        self.assertNotIn("Content=", self.export(loadout))

    def test_a_condition_about_the_game_files_that_holds_is_given_up_and_said(self):
        loadout, (found,) = self.found(
            RADIUS.replace("<ModOp ", '<ModOp Condition="/Values/Building" '))
        self.assertTrue(found.possible, found.refused)
        self.assertIn("condition about the game files", found.warnings[0])
        keptops.write_narrow(loadout, [found.op.uid])
        self.assertNotIn("Condition", self.export(loadout))

    def test_one_shown_as_a_whole_node_swapped_is_not_narrow_whatever_else_it_is(self):
        """*Cheaper and Faster Streets* merges at ``/Values`` with values
        worked out as the game loads. What is under it is the whole of
        ``Values`` taken out and put back, and written narrow that was what
        went out: the Studio then read its own export as another asset.

        Read from such an op, and not marked so by hand (A11-128), so that
        the reader saying it is part of what is tested."""
        loadout, (found,) = self.found(
            '<ModOp Type="merge" GUID="1000" Path="/Values"><Building><Radius>'
            '<ModValue Insert="2 * 2"/></Radius></Building>'
            "<Cost><Costs><Item/><Item/><Item/></Costs></Cost></ModOp>"
        )
        self.assertIn("works its value out", found.op.kept)
        self.assertIn("swaps the whole node", found.op.wide)
        self.assertEqual(
            sorted((c.op, c.tag) for c in loadout.changes.values()),
            [("addNextSibling", "Values"), ("remove", "Values")],
        )
        self.assertIn("swaps the whole node", found.refused)
        self.assertEqual(keptops.write_narrow(loadout, [found.op.uid]), (0, 0))

    def test_the_reader_says_so_of_an_op_it_also_keeps_for_another_reason(self):
        loadout = self.loadout(Mod=(
            '<ModOp Type="merge" GUID="1000" Path="/Values"><Building><Radius>'
            '<ModValue Insert="2 * 2"/></Radius><Extra><Deep>1</Deep></Extra></Building></ModOp>'
        ))
        (op,) = loadout.ops
        # Whichever way it is read here, the two say the same thing.
        swapped = any(c.op != "replace" and c.tag == "Values" for c in loadout.changes.values())
        self.assertEqual(bool(op.wide), swapped)

    def test_what_cannot_be_and_why(self):
        cases = {
            CONDITIONED: "runs on a condition",
            RADIUS.replace("<ModOp ", '<ModOp Skip="1" '): "ships it switched off",
            ALREADY: "changes nothing as things stand",
        }
        for body, why in cases.items():
            with self.subTest(body=body):
                loadout, (found,) = self.found(body)
                self.assertIn(why, found.refused)
                self.assertEqual(keptops.write_narrow(loadout, [found.op.uid]), (0, 0))
                self.assertEqual(len(loadout.ops), 1)

    def test_a_group_and_what_is_in_it_stay(self):
        _loadout, (group, inside) = self.found(IN_A_GROUP)
        self.assertIn("it is a group", group.refused)
        self.assertIn("it is in a group", inside.refused)

    def test_one_switched_off_or_with_a_change_taken_from_under_it_stays(self):
        loadout, (found,) = self.found(SEVERAL)
        found.op.active = False
        self.assertIn("switched off", keptops.narrowings(loadout, "Mod")[0].refused)
        found.op.active = True
        loadout.remove_key(next(iter(loadout.changes)))
        self.assertIn("reverted", keptops.narrowings(loadout, "Mod")[0].refused)

    def test_an_edit_that_was_its_exception_is_an_edit_after_it(self):
        loadout, (found,) = self.found(SEVERAL)
        answer = next(iter(loadout.changes.values()))
        from anno_modding_studio.core.changes import Change

        edit = loadout.place(Change(
            guid=answer.guid, asset_label=answer.asset_label, path=answer.path, tag=answer.tag,
            old_value="10", new_value="99", origin="Mod",
        ))
        loadout.set_change(edit)
        self.assertEqual(edit.exception_of, found.op.uid)
        keptops.write_narrow(loadout, [found.op.uid])
        self.assertEqual(edit.exception_of, "")
        self.assertEqual(len(loadout.changes), 3)

    def test_an_op_that_is_its_one_change_already_has_nothing_to_gain(self):
        _loadout, (found,) = self.found(RADIUS)
        self.assertTrue(found.possible)
        self.assertTrue(found.plain)


class TestShowingAGroupsXml(ListedCase):
    """*Show XML* on a group's row showed the group empty, and on an op in
    it showed that same empty group."""

    panel = TestTheListOnScreen.panel
    rows = TestTheListOnScreen.rows
    NESTED = (
        RADIUS
        + '<Group Condition="!#another_mod">'
        + WIDE.replace("25", "31")
        + '<Group Condition="!#third_mod">' + PATTERN + "</Group>"
        + NOWHERE
        + "</Group>"
    )

    def shown(self, panel, row):
        return panel.xml_for(row)[1]

    def row(self, panel, loadout, says):
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        return next(
            row for row in self.rows(panel, "op")
            if says in loadout.op(row.data(0, OP_ROLE)).source
        )

    def test_a_group_is_shown_with_everything_in_it(self):
        loadout, panel = self.panel(Mod=self.NESTED)
        xml = self.shown(panel, self.row(panel, loadout, "another_mod"))
        self.assertEqual(xml.count("<Group "), 2)
        self.assertEqual(xml.count("<ModOp "), 3)
        self.assertIn("/Values/Nope", xml)
        # And not what stands outside it.
        self.assertNotIn('Path="/Values/Building/Radius"', xml)
        self.assertLess(xml.index("another_mod"), xml.index("third_mod"))
        self.assertLess(xml.index("third_mod"), xml.index("//Values/Building"))

    def test_an_op_in_a_group_is_shown_as_itself_inside_its_groups(self):
        loadout, panel = self.panel(Mod=self.NESTED)
        xml = self.shown(panel, self.row(panel, loadout, "//Values/Building"))
        self.assertEqual(xml.count("<Group "), 2)
        self.assertEqual(xml.count("<ModOp "), 1)
        self.assertIn("//Values/Building", xml)
        wide = self.shown(panel, self.row(panel, loadout, "<BuildingType>"))
        self.assertEqual((wide.count("<Group "), wide.count("<ModOp ")), (1, 1))
        self.assertIn("<Radius>31</Radius>", wide)

    def test_a_change_under_an_op_in_a_group_shows_that_op(self):
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        loadout, panel = self.panel(Mod=self.NESTED)
        pattern = self.row(panel, loadout, "//Values/Building")
        under = pattern.child(0)
        self.assertIsNone(under.data(0, OP_ROLE))
        _heading, xml, note = panel.xml_for(under)
        self.assertEqual((xml.count("<Group "), xml.count("<ModOp ")), (2, 1))
        self.assertIn("kept as written", note)

    def test_an_op_outside_any_group_is_shown_alone(self):
        loadout, panel = self.panel(Mod=WIDE + self.NESTED.replace(RADIUS, "", 1))
        xml = self.shown(panel, self.row(panel, loadout, "<Radius>25</Radius>"))
        self.assertEqual((xml.count("<Group "), xml.count("<ModOp ")), (0, 1))


class TestWhatShowXmlSaysIsSwitchedOff(ListedCase):
    """*Show XML* says what of it is switched off, by the generator's own
    reckoning; and of a mod switched off as a mod, that it is not written."""

    panel = TestTheListOnScreen.panel
    rows = TestTheListOnScreen.rows

    def mod_row(self, panel):
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        return next(row for row in self.rows(panel, "mod") if row.data(0, ORIGIN_ROLE) == "Mod")

    def test_a_mod_switched_off_as_a_mod_says_it_is_not_written(self):
        """An export leaves such a mod out, and its rows said they were how
        the Studio writes it."""
        from anno_modding_studio.core import build

        loadout, panel = self.panel(Mod=SEVERAL + WIDE.replace("25", "40"))
        loadout.layer("Mod").active = False
        self.assertNotIn("Mod", build.writable(loadout))
        for row in (self.mod_row(panel), *self.rows(panel, "op")):
            with self.subTest(row=row.text(0)):
                _heading, xml, note = panel.xml_for(row)
                self.assertIn("<ModOp ", xml)
                self.assertIn("Mod is switched off, so the Studio does not write it.", note)

    def test_a_mod_that_is_on_says_nothing_of_it(self):
        _loadout, panel = self.panel(Mod=SEVERAL)
        self.assertEqual(panel.xml_for(self.mod_row(panel))[2], "")

    def test_the_count_is_of_the_comments_the_xml_holds(self):
        """Counted apart from the generator, a change whose op is gone was a
        comment in the XML and not in the count."""
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        loadout, panel = self.panel(Mod=SEVERAL + WIDE.replace("25", "40"))
        several = next(op for op in loadout.ops if "1000,1001" in op.source)
        loadout.drop_op(several.uid, with_changes=False)
        for change in loadout.changes.values():
            if change.op_uid == several.uid:
                change.active = False
        panel.refresh()
        panel.build_every_row()
        _heading, xml, note = panel.xml_for(self.mod_row(panel))
        self.assertEqual(xml.count(SWITCHED_OFF_MARK), 2)
        self.assertTrue(note.startswith("2 switched-off changes"), note)


class TestWritingNarrowFromTheList(ListedCase):
    rows = TestTheListOnScreen.rows

    def panel(self, **folders):
        loadout, panel = TestTheListOnScreen.panel(self, **folders)
        # Yours, kept by a project, so its own ops are rewritten in its file.
        loadout.own.creator = "Me"
        loadout.layer("Mod").creator = "Me"
        loadout.layer("Mod").kept = True
        return loadout, panel

    def entry(self, panel, row, text="Write as narrow ModOps\u2026"):
        return next((a for a in panel.menu_for(row).actions() if a.text() == text), None)

    def mod_row(self, panel):
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        return next(row for row in self.rows(panel, "mod") if row.data(0, ORIGIN_ROLE) == "Mod")

    def test_an_ops_row_offers_it_and_asks_first(self):
        from anno_modding_studio.ui import changes_dock

        loadout, panel = self.panel(Mod=WIDE)
        (row,) = self.rows(panel, "op")
        entry = self.entry(panel, row)
        self.assertTrue(entry.isEnabled())
        heard = []
        panel.changesRemoved.connect(lambda: heard.append(True))
        with mock.patch.object(changes_dock, "ask", return_value=False) as asked:
            entry.trigger()
        self.assertIn("1 ModOp will be replaced by 1 change", asked.call_args.args[2])
        self.assertEqual((len(loadout.ops), heard), (1, []))
        with mock.patch.object(changes_dock, "ask", return_value=True):
            self.entry(panel, self.rows(panel, "op")[0]).trigger()
        self.assertEqual((loadout.ops, heard), ([], [True]))
        self.assertEqual(self.rows(panel, "op"), [])
        self.assertEqual(len(self.rows(panel, "change")), 1)

    def test_where_it_cannot_be_the_entry_says_why(self):
        _loadout, panel = self.panel(Mod=CONDITIONED)
        (row,) = self.rows(panel, "op")
        entry = self.entry(panel, row)
        self.assertFalse(entry.isEnabled())
        self.assertIn("it runs on a condition", entry.toolTip())

    def test_what_is_given_up_is_in_the_question(self):
        from anno_modding_studio.ui import changes_dock

        _loadout, panel = self.panel(Mod=PATTERN)
        (row,) = self.rows(panel, "op")
        with mock.patch.object(changes_dock, "ask", return_value=False) as asked:
            self.entry(panel, row).trigger()
        self.assertIn("the 2 assets it finds today", asked.call_args.args[2])

    def test_a_mods_row_offers_it_for_every_op_that_can_be(self):
        from anno_modding_studio.ui import changes_dock

        loadout, panel = self.panel(Mod=WIDE + PATTERN.replace("30", "31") + CONDITIONED + RADIUS.replace("25", "40"))
        entry = self.entry(panel, self.mod_row(panel), "Write its ModOps as narrow ModOps\u2026")
        self.assertTrue(entry.isEnabled())
        with mock.patch.object(changes_dock, "ask", return_value=True) as asked:
            entry.trigger()
        said = asked.call_args.args[2]
        self.assertIn("2 ModOps will be replaced by", said)
        self.assertIn("1 ModOp stays as written", said)
        self.assertIn("1 of them finds what it changes as the game loads", said)
        # The condition's op and the one that was its one change already.
        self.assertEqual(len(loadout.ops), 2)

    def test_a_mod_with_nothing_to_gain_is_not_offered_it(self):
        _loadout, panel = self.panel(Mod=RADIUS)
        self.assertIsNone(
            self.entry(panel, self.mod_row(panel), "Write its ModOps as narrow ModOps\u2026")
        )

    def test_a_mod_of_yours_says_how_many_of_its_ops_replace_more_than_they_change(self):
        loadout, panel = self.panel(Mod=WIDE)
        self.assertNotIn("replace", self.mod_row(panel).text(1))
        loadout.own.creator = "Me"
        loadout.layer("Mod").creator = "me"
        loadout.layer("Mod").kept = True
        panel.refresh()
        panel.build_every_row()
        self.assertIn("1 ModOp replaces more than it changes", self.mod_row(panel).text(1))
        (row,) = self.rows(panel, "op")
        self.assertIn("It replaces 1 field it leaves as it was", row.toolTip(0))


class TestNotWritingSomebodyElsesNarrow(ListedCase):
    """Written narrow, an op is rewritten in its mod.  Somebody else's mod
    is not edited directly (sections 5.2 and 5.3 of
    docs/proposals/projects-and-loadouts-proposal.md), so it is not offered
    there: it was, and asked the three ways on first."""

    rows = TestTheListOnScreen.rows
    entry = TestWritingNarrowFromTheList.entry
    mod_row = TestWritingNarrowFromTheList.mod_row

    def panel(self, **folders):
        return TestTheListOnScreen.panel(self, **folders)

    def test_an_ops_row_does_not_offer_it(self):
        _loadout, panel = self.panel(Mod=WIDE)
        (row,) = self.rows(panel, "op")
        self.assertIsNone(self.entry(panel, row))

    def test_nor_a_mods_row(self):
        _loadout, panel = self.panel(Mod=WIDE + PATTERN.replace("30", "31"))
        self.assertIsNone(self.entry(panel, self.mod_row(panel), "Write its ModOps as narrow ModOps…"))

    def test_nor_one_with_your_name_that_no_project_keeps(self):
        # Read like anybody else's until it is made one (section 5.5).
        loadout, panel = self.panel(Mod=WIDE)
        loadout.own.creator = "Me"
        loadout.layer("Mod").creator = "Me"
        panel.refresh()
        panel.build_every_row()
        (row,) = self.rows(panel, "op")
        self.assertIsNone(self.entry(panel, row))

    def test_asked_all_the_same_it_writes_nothing_and_asks_nothing(self):
        from anno_modding_studio.ui import changes_dock

        loadout, panel = self.panel(Mod=WIDE)
        panel.changed_directly = lambda origin: True
        panel.may_edit_inside = lambda origins: self.fail("asked the three ways on")
        with mock.patch.object(changes_dock, "ask", return_value=True) as asked:
            self.assertFalse(panel.write_narrow([loadout.ops[0].uid]))
            self.assertFalse(panel.write_narrow(None, "Mod"))
        asked.assert_not_called()
        self.assertEqual(len(loadout.ops), 1)


class TestPickingAModToImport(SessionCase):
    """What the one-mod dialog opens on: a mod, or the folder mods live in."""

    def test_a_folder_with_its_data_and_nothing_beside_it_is_a_mod(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        root = self.mods(Mod=RADIUS)
        self.assertTrue(ModImportDialog._is_mod_folder(str(root / "Mod")))
        self.assertFalse(ModImportDialog._is_mod_folder(str(root)))

    def test_a_mods_folder_with_a_stray_data_folder_in_it_is_still_a_mods_folder(self):
        """Read as one mod, it walked every mod in it to show that one's ops."""
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        root = self.mods(One=RADIUS, Two=SEVERAL)
        (root / "data" / "config").mkdir(parents=True)
        (root / ".vscode").mkdir()
        self.assertFalse(ModImportDialog._is_mod_folder(str(root)))
        # A mod with a modinfo is one whatever sits beside it.
        (root / "modinfo.json").write_text("{}", encoding="utf-8")
        self.assertTrue(ModImportDialog._is_mod_folder(str(root)))

    def test_the_librarys_mods_with_no_link_are_offered_beside_the_ones_there(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import library
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        QApplication.instance() or QApplication([])
        root = self.mods(One=RADIUS)
        kept = self.mods(Shelved=SEVERAL, Linked=ALREADY)
        held = [
            library.Held(name="Shelved", link=root / "Shelved", target=kept / "Shelved",
                         kind=library.COPY, linked=False),
            library.Held(name="Linked", link=root / "Linked", target=kept / "Linked",
                         kind=library.COPY, linked=True),
        ]
        with mock.patch.object(library, "held", return_value=held):
            dialog = ModImportDialog(self.index, None, str(root), library_dir=kept)
        self.addCleanup(dialog.deleteLater)
        rows = [dialog.installed.item(row) for row in range(dialog.installed.count())]
        self.assertEqual(
            [row.text() for row in rows], ["One", "In the library, switched off", "Shelved"]
        )
        # The heading is not a mod to pick.
        self.assertEqual(rows[1].flags(), Qt.ItemFlag.NoItemFlags)
        self.assertIsNone(rows[1].data(Qt.ItemDataRole.UserRole))
        self.assertEqual(rows[2].data(Qt.ItemDataRole.UserRole), str(kept / "Shelved"))
        self.assertIn("1 more is in the library", dialog.installed_label.text())
        self.assertIsNone(dialog.report)

    def test_a_folder_with_only_the_librarys_mods_says_so_without_a_count_of_none(self):
        """It said "0 mods in it. Pick one" and then "1 more is in the library"."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import library
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        QApplication.instance() or QApplication([])
        root = self.mods()
        kept = self.mods(Shelved=SEVERAL)
        held = [
            library.Held(name="Shelved", link=root / "Shelved", target=kept / "Shelved",
                         kind=library.COPY, linked=False),
        ]
        with mock.patch.object(library, "held", return_value=held):
            dialog = ModImportDialog(self.index, None, str(root), library_dir=kept)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(
            dialog.installed_label.text(),
            "No mods in %s. 1 is in the library, switched off. Pick it, or browse "
            "for a mod elsewhere." % root,
        )
        self.assertFalse(dialog.installed.isHidden())


class TestAFolderReadNobodyWaitsFor(SessionCase):
    def worker(self, root):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.workers import ModsWorker

        QApplication.instance() or QApplication([])
        worker = ModsWorker(self.index, modfolder.mod_folders(root), roots=[root], game="anno117")
        self.addCleanup(worker.deleteLater)
        said = []
        worker.succeeded.connect(lambda session: said.append("read"))
        worker.failed.connect(lambda why: said.append(why))
        return worker, said

    def test_it_reads_the_folder(self):
        worker, said = self.worker(self.mods(One=RADIUS, Two=SEVERAL))
        worker.run()
        self.assertEqual(said, ["read"])

    def test_cancelled_it_stops_and_says_nothing(self):
        worker, said = self.worker(self.mods(One=RADIUS, Two=SEVERAL))
        steps = []
        worker.progress.connect(lambda *told: steps.append(told))
        worker.cancel()
        worker.run()
        self.assertEqual((said, steps), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
