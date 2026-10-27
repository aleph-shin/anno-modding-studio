"""A mod's options where its ModOps name them: coloured in the XML, linked
beside the line, on the Mod changes rows that read them, and an Apply that
reads a new one taken back when it is not declared.

The window here is the Mod file tab's (test_file_tab), on Anno 117."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QColor, QTextCharFormat
from test_file_tab import CYCLE, FileTabCase, _app, document

from anno_modding_studio.core.games import ANNO_117
from anno_modding_studio.ui import asset_xml, options_editor_dialog, theme
from anno_modding_studio.ui.change_rows import KIND_ROLE

READS = '<Group Condition="$roomier">%s</Group>'
ROOMIER = {"roomier": {"label": "Roomier houses", "type": "toggle", "default": "true"}}


class OptionsCase(FileTabCase):
    def setUp(self):
        super().setUp()
        self.loadout.game = ANNO_117
        self.loadout.own.mod_id = "my-mod"
        self.pane = self.panel.pane

    def line_of(self, said: str) -> int:
        return next(at for at, line in enumerate(self.pane.toPlainText().split("\n")) if said in line)

    def format_at(self, line: int, column: int) -> QTextCharFormat:
        block = self.pane.document().findBlockByNumber(line)
        # Held, and the format copied out: PySide frees each range with the
        # list it came in.
        ranges = block.layout().formats()
        for one in ranges:
            if one.start <= column < one.start + one.length:
                return QTextCharFormat(one.format)
        return QTextCharFormat()

    def row(self, kind: str):
        found = []

        def walk(item):
            if item.data(0, KIND_ROLE) == kind:
                found.append(item)
            for at in range(item.childCount()):
                walk(item.child(at))

        tree = self.window.changes_panel.tree
        for at in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(at))
        return found[0]


class TestInTheFile(OptionsCase):
    def setUp(self):
        super().setUp()
        self.loadout.options = dict(ROOMIER)
        self.apply(document(READS % (CYCLE % 15)))

    def test_a_setting_is_named_beside_its_line_and_opens_its_option(self):
        line = self.line_of("$roomier")
        named = self.pane.named_assets()[line]
        self.assertIn((asset_xml.OPTION + "\nroomier", "⚙ Roomier houses"), named)
        with mock.patch.object(self.window.output, "declare_options") as declared:
            self.pane._open_named(asset_xml.OPTION + "\nroomier")
        declared.assert_called_once_with("", [], focus="roomier")

    def test_a_setting_has_a_colour_of_its_own_and_a_linked_number_is_dotted(self):
        line = self.line_of("$roomier")
        column = self.pane.document().findBlockByNumber(line).text().index("$roomier")
        said = self.format_at(line, column + 1)
        self.assertEqual(said.foreground().color(), QColor(theme.SYN_OPTION))
        self.assertTrue(said.fontUnderline())
        line = self.line_of('GUID="1010340"')
        column = self.pane.document().findBlockByNumber(line).text().index("1010340")
        self.assertEqual(self.format_at(line, column + 2).underlineStyle(),
                         QTextCharFormat.UnderlineStyle.DotLine)

    def test_the_rows_that_read_it_open_it(self):
        panel = self.window.changes_panel
        menu = panel.menu_for(self.row("op"))
        self.addCleanup(menu.deleteLater)
        action = next(a for a in menu.actions() if a.text() == "Option “Roomier houses”…")
        with mock.patch.object(self.window.output, "declare_options") as declared:
            action.trigger()
        declared.assert_called_once_with("", [], focus="roomier")
        mod = panel.menu_for(self.row("mod"))
        self.addCleanup(mod.deleteLater)
        self.assertIn("Edit options…", [a.text() for a in mod.actions()])

    def test_not_in_anno_1800(self):
        self.loadout.game = "anno1800"
        self.pane.name_assets()
        self.assertFalse(any(
            guid.startswith(asset_xml.OPTION)
            for names in self.pane.named_assets().values() for guid, _name in names
        ))


class TestAnApplyNotWanted(OptionsCase):
    def test_turned_down_the_apply_is_taken_back_and_the_text_is_kept(self):
        typed = document(READS % (CYCLE % 15))
        seen = []

        def turned_down(dialog):
            seen.append([item["name"] for item in dialog.items])
            return int(options_editor_dialog.QDialog.DialogCode.Rejected)

        with mock.patch.object(options_editor_dialog.OptionsEditorDialog, "exec", turned_down):
            self.apply(typed)
        _app.processEvents()
        self.assertEqual(seen, [["roomier"]])
        # Read in, it was there: held unread, as an op asking what nothing
        # declares is. Taken back, it is not.
        self.assertEqual((self.loadout.ops, self.loadout.unread), ([], []))
        self.assertEqual(len(self.loadout.changes), 0)
        self.assertTrue(self.panel.has_unapplied())
        self.assertEqual(self.panel.typed_text(), typed)
        self.assertIn("Apply undone", self.panel.status.text())
        self.assertEqual(self.loadout.options, {})

    def test_kept_the_apply_stands_with_the_option_declared(self):
        def kept(dialog):
            dialog._accept()
            return int(options_editor_dialog.QDialog.DialogCode.Accepted)

        with mock.patch.object(options_editor_dialog.OptionsEditorDialog, "exec", kept):
            self.apply(document(READS % (CYCLE % 15)))
        self.assertTrue(self.loadout.ops or self.loadout.unread)
        self.assertEqual(self.loadout.options, {"roomier": {"type": "toggle", "default": "false"}})
        self.assertFalse(self.panel.has_unapplied())


class TestSomebodyElsesOption(OptionsCase):
    def test_opens_what_their_mod_is_read_at(self):
        with mock.patch.object(self.window.output, "details_are_yours", return_value=False), \
                mock.patch.object(self.window, "edit_mod_options") as setting:
            self.window.open_option("Theirs", "hard")
        setting.assert_called_once_with("Theirs")


class TestARowSaysTheOptionsLabel(OptionsCase):
    def test_a_group_reads_as_the_option_it_asks(self):
        self.loadout.options = dict(ROOMIER)
        self.apply(document(READS.replace("$roomier", "$my-mod.roomier") % (CYCLE % 15)))
        row = self.row("op")
        self.assertIn("only if option “Roomier houses”", row.text(0))
        # And is found by it.
        self.assertIn("roomier houses", row.data(0, file_panel_find_role()))


def file_panel_find_role():
    from anno_modding_studio.ui.change_rows import FIND_ROLE

    return FIND_ROLE


class TestPutUnderAnOption(OptionsCase):
    FAR = '<ModOp Type="replace" GUID="1010340" Path="/Values/Building/Radius"><Radius>9</Radius></ModOp>'

    def setUp(self):
        super().setUp()
        from test_file_tab import EVERY

        self.apply(document(EVERY % 12, CYCLE % 15))
        self.panel.refresh()

    def put(self, picked, label="Shrine of Ceres", which=0):
        from anno_modding_studio.ui import option_group_dialog

        seen = []

        def chosen(dialog):
            dialog.which.setCurrentIndex(which)
            if not dialog.which.currentData():
                dialog.label_edit.setText(label)
                dialog.label_edit.textEdited.emit(label)
            seen.append(dialog)
            return int(option_group_dialog.QDialog.DialogCode.Accepted)

        with mock.patch.object(option_group_dialog.OptionGroupDialog, "exec", chosen):
            done = self.window.put_under_option("", picked)
        return done, seen

    def every_pick(self):
        found = [("op", op.uid) for op in self.loadout.ops]
        found += [("change", change.key) for change in self.loadout.changes.values() if not change.op_uid]
        return found

    def test_a_new_toggle_is_declared_and_the_ops_go_into_one_group_under_it(self):
        done, _seen = self.put(self.every_pick())
        self.assertTrue(done)
        self.assertEqual(self.loadout.options, {
            "shrine-of-ceres": {"label": "Shrine of Ceres", "type": "toggle", "default": "true"},
        })
        text = self.panel.pane.toPlainText()
        # Named in full, as the mod's other options are.
        self.assertEqual(text.count('<Group Condition="$my-mod.shrine-of-ceres">'), 1)
        self.assertLess(text.index("<Group"), text.index("Residence7"))
        self.assertLess(text.index("CycleTime"), text.index("</Group>"))
        self.assertIn("put under option “Shrine of Ceres”", self.window.status_label.text())

    def test_it_is_one_step_to_undo(self):
        before = self.panel.pane.toPlainText()
        self.put(self.every_pick())
        self.window.undo_last()
        self.panel.refresh()
        self.assertEqual(self.panel.pane.toPlainText(), before)

    def test_an_option_the_mod_declares_is_offered_and_used(self):
        self.loadout.options = dict(ROOMIER)
        done, seen = self.put(self.every_pick(), which=1)
        self.assertTrue(done)
        self.assertEqual(seen[0].which.currentData(), "roomier")
        self.assertEqual(self.loadout.options, ROOMIER)
        self.assertIn('<Group Condition="$my-mod.roomier">', self.panel.pane.toPlainText())

    def test_picks_that_do_not_stand_together_go_into_a_group_each(self):
        from anno_modding_studio.core import modfile, option_groups

        self.apply(document(CYCLE % 15, document_middle(), self.FAR))
        shown = modfile.shown(self.loadout, "")
        # The first op and the last, with the one between left out: the
        # last is carried unread (the hut has no Building/Radius).
        first, last = shown.items[0], shown.items[-1]
        self.assertEqual((first.kind, last.kind, len(shown.items)), ("op", "unread", 3))
        picked = [("op", first.op.uid), ("unread", last.unread.order)]
        self.assertEqual(len(option_groups.runs(shown, picked)), 2)
        done, _seen = self.put(picked)
        self.assertTrue(done)
        self.assertEqual(self.panel.pane.toPlainText().count('<Group Condition="$my-mod.shrine-of-ceres">'), 2)

    def test_the_rows_picked_in_the_panel_are_offered_it(self):
        panel = self.window.changes_panel
        panel.refresh()
        row = self.row("op")
        row.setSelected(True)
        origin, picked = panel.picked_for_option()
        self.assertEqual((origin, picked), ("", [("op", row.data(0, asset_xml_op_role()))]))
        menu = panel.menu_for(row)
        self.addCleanup(menu.deleteLater)
        self.assertIn("Put under an option…", [a.text() for a in menu.actions()])
        self.loadout.game = "anno1800"
        self.assertIsNone(panel.picked_for_option())


class TestWhatFieldsWroteAndAnOptionHolds(OptionsCase):
    """What was typed in Fields and put under an option is typed over in
    Fields as the ModOps there are: each the Studio wrote, in its group, and
    retuned there.  No op of the Studio's after the group for a value one
    in it already sets."""

    FACTORY = "1010340"
    put = TestPutUnderAnOption.put
    every_pick = TestPutUnderAnOption.every_pick

    def field(self, tag):
        editor = self.window.editor
        editor.load_asset(self.FACTORY)
        return next(item for item in editor._iter_items() if item.text(0) == tag)

    def test_a_value_and_a_field_added_are_retuned_in_their_group(self):
        from lxml import etree

        from anno_modding_studio.core import generator

        self.field("CycleTime").setText(1, "45")
        height = etree.Element("Height")
        height.text = "4"
        factory = self.field("CycleTime").parent()
        self.window.editor._record_addition(factory, height, "add Height")
        done, _seen = self.put(self.every_pick())
        self.assertTrue(done)
        uids = [op.uid for op in self.loadout.ops]
        self.field("CycleTime").setText(1, "50")
        self.field("Height").setText(1, "6")
        self.assertEqual([op.uid for op in self.loadout.ops], uids)
        self.assertEqual([c for c in self.loadout if not c.op_uid], [])
        out = etree.fromstring(generator.build_modops_xml(generator.for_mod(self.loadout, "")).encode("utf-8"))
        (group,) = out.findall("Group")
        self.assertEqual(
            [(node.tag, node.text) for node in out.iter("CycleTime", "Height")],
            [("CycleTime", "50"), ("Height", "6")],
        )
        self.assertEqual(len(list(group.iter("CycleTime", "Height"))), 2)


def document_middle() -> str:
    from test_file_tab import EVERY

    return EVERY % 12


def asset_xml_op_role():
    from anno_modding_studio.ui.change_rows import OP_ROLE

    return OP_ROLE


if __name__ == "__main__":
    unittest.main()
