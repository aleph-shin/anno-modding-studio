"""The Mod file tab in the window (``ui.file_panel``): the file of the mod
changes go to, typed in and applied, with its outline beside it; and the Mod
changes panel that folds away to give it room.

What typing comes to is ``core.modfile``'s, and is tested in test_modfile.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontMetricsF, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core import modfile  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    STUDIO_SAYS_MARK,
    Change,
    ModLayer,
)
from anno_modding_studio.ui import file_panel, theme  # noqa: E402
from anno_modding_studio.ui.change_rows import KIND_ROLE  # noqa: E402
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402
from anno_modding_studio.ui.navigation import (  # noqa: E402
    TAB_EDIT,
    TAB_FILE,
    TAB_REFS,
    TAB_SOURCE,
)

_app = QApplication.instance() or QApplication([])

HUT = "1010340"
CYCLE = (
    '<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/CycleTime">'
    "<CycleTime>%s</CycleTime></ModOp>"
)
EVERY = '<ModOp Type="merge" Path="//Values/Residence7"><Residents>%s</Residents></ModOp>'
NOWHERE = '<ModOp Type="replace" GUID="99999999" Path="/Values/Nope"><Nope>1</Nope></ModOp>'
GROUP = '<Group Condition="#NobodyHasThis">%s</Group>'
TAGGED = CYCLE.replace("<ModOp ", '<ModOp ModOpID="Cycle" ')
EXPOSE = (
    '<ImyaExpose Path="self::CycleTime" ModOpID="Cycle" ExposeID="Cycle time" Kind="Slider">'
    '<SliderDefinition Min="5" Max="30" Stepping="5"/></ImyaExpose>'
)


def document(*ops: str) -> str:
    return "<ModOps>\n%s\n</ModOps>\n" % "\n".join(ops)


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _isolation.forget_settings()


class FileTabCase(unittest.TestCase):
    """A window on a small set of game files, with a mod of your own."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        (tmp / "texts_english.xml").write_text(
            make_sample_assets.build_texts_xml(), encoding="utf-8"
        )
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.window.loadout.aim = AIM_MAKE
        # Somebody who has said who they are, as the card asks as it starts:
        # an edit into your own mod asks otherwise.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.window.loadout.make_own_mod()
        self.window.update_tab_gates()
        self.panel = self.window.file_panel
        self.loadout = self.window.loadout

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def open(self) -> None:
        self.assertTrue(self.window.open_tab(TAB_FILE))
        self.panel.refresh()

    def open_as_it_is(self) -> None:
        """Go to the tab as a click on it does: drawn again only if it was
        told the mod had moved."""
        self.assertTrue(self.window.open_tab(TAB_FILE))
        if self.panel._waiting:
            self.panel.refresh()

    def type(self, text: str) -> None:
        """Put ``text`` in the file as somebody typing it would."""
        cursor = self.panel.pane.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def apply(self, text: str) -> None:
        self.open()
        self.type(text)
        self.panel.apply_button.click()

    def rows(self) -> list:
        """The outline's rows, each as ``(depth, its words, what it comes to)``."""
        found = []

        def walk(row, depth):
            found.append((depth, row.text(0), row.text(1)))
            for at in range(row.childCount()):
                walk(row.child(at), depth + 1)

        tree = self.panel.outline.tree
        for at in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(at), 0)
        return found


class TestTheTab(FileTabCase):
    def test_it_stands_after_the_asset_editor(self):
        names = [self.window.tabs.tabText(at) for at in range(self.window.tabs.count())]
        self.assertEqual(names[TAB_EDIT:TAB_REFS + 1], ["Asset editor", "Mod file", "References"])
        self.assertIs(self.window.tabs.widget(TAB_FILE), self.panel)

    def test_it_waits_for_the_start_tab_as_the_others_do(self):
        self.window.loadout.clear()
        self.window.loadout.aim = ""
        self.window.update_tab_gates()
        self.assertFalse(self.window.tabs.isTabEnabled(TAB_FILE))
        self.assertFalse(self.window.open_tab(TAB_FILE))
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_it_says_whose_file_it_is(self):
        self.loadout.own.name = "Faster Lumber"
        self.open()
        self.assertEqual(self.panel.heading.text(), "The file of Faster Lumber")
        self.assertEqual(self.panel.pane.accessibleName(), "The file of Faster Lumber")
        # One mod there is: nothing to pick.
        self.assertTrue(self.panel.file_box.isHidden())

    def test_a_mod_with_nothing_in_it_says_what_to_do(self):
        self.open()
        self.assertIn("Type them here, then Apply", self.panel.pane.placeholderText())
        self.assertFalse(self.panel.apply_button.isEnabled())
        self.assertFalse(self.panel.revert_button.isEnabled())


class TestTypingAndApply(FileTabCase):
    def test_apply_puts_what_was_typed_into_the_mod(self):
        self.open()
        self.type(document(CYCLE % 15, EVERY % 12))
        self.assertTrue(self.panel.has_unapplied())
        self.assertTrue(self.panel.apply_button.isEnabled())
        self.panel.apply_button.click()
        self.assertFalse(self.panel.has_unapplied())
        self.assertEqual(len(self.loadout.ops), 2)
        self.assertIn("2 ModOps added.", self.panel.status.text())
        # The file is written again from the mod, the Studio's way.
        self.assertIn("    <CycleTime>15</CycleTime>", self.panel.pane.toPlainText())
        self.assertFalse(self.panel.apply_button.isEnabled())

    def test_it_is_one_step_to_undo(self):
        self.apply(document(CYCLE % 15, EVERY % 12))
        self.assertTrue(self.window.undo.can_undo)
        self.window.undo_last()
        self.assertEqual(self.loadout.ops, [])
        self.assertEqual(len(self.loadout.changes), 0)
        self.assertFalse(self.window.undo.can_undo)
        self.assertNotIn("CycleTime", self.panel.pane.toPlainText())
        self.window.redo_last()
        self.assertEqual(len(self.loadout.ops), 2)
        self.assertIn("CycleTime", self.panel.pane.toPlainText())

    def test_ctrl_enter_applies(self):
        from PySide6.QtGui import QShortcut

        self.open()
        self.type(document(CYCLE % 15))
        keys = {
            shortcut.key().toString(): shortcut for shortcut in self.panel.findChildren(QShortcut)
        }
        keys["Ctrl+Return"].activated.emit()
        self.assertEqual(len(self.loadout.ops), 1)

    def test_text_that_is_not_xml_is_kept_and_the_caret_goes_to_it(self):
        self.open()
        self.type(document(CYCLE % 15).replace("</CycleTime>", "</Cycle>"))
        self.panel.apply_button.click()
        self.assertEqual(self.loadout.ops, [])
        self.assertTrue(self.panel.has_unapplied())
        self.assertIn("Not applied. Line 2", self.panel.status.text())
        self.assertEqual(self.panel.pane.textCursor().blockNumber(), 1)

    def test_revert_puts_the_mods_own_file_back(self):
        self.apply(document(CYCLE % 15))
        before = self.panel.pane.toPlainText()
        self.type("<ModOps/>")
        self.panel.revert_button.click()
        self.assertEqual(self.panel.pane.toPlainText(), before)
        self.assertFalse(self.panel.has_unapplied())
        self.assertEqual(len(self.loadout.ops), 1)

    def test_what_is_typed_stays_while_the_mod_moves_under_it(self):
        self.apply(document(CYCLE % 15))
        self.type(document(CYCLE % 18))
        # An edit made somewhere else: the Mod changes panel, Fields, undo.
        self.loadout.ops[0].note = "said elsewhere"
        self.window.on_changes_updated()
        self.assertIn("<CycleTime>18</CycleTime>", self.panel.pane.toPlainText())
        self.assertIn("The mod changed while you were typing", self.panel.status.text())
        self.panel.apply_button.click()
        (op,) = self.loadout.ops
        self.assertIn("18", op.source)

    def test_undone_after_the_mod_moved_it_is_the_file_as_it_is_now(self):
        """A8-39: nothing typed is left, so nothing stands for the old file."""
        self.apply(document(CYCLE % 15))
        cursor = self.panel.pane.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("x")
        self.loadout.ops[0].note = "said elsewhere"
        self.window.on_changes_updated()
        self.assertIn("The mod changed while you were typing", self.panel.status.text())
        self.panel.pane.document().undo()
        self.assertFalse(self.panel.has_unapplied())
        self.assertEqual(self.panel.pane.toPlainText(), self.panel.shown.text)
        self.assertIn("said elsewhere", self.panel.pane.toPlainText())
        self.assertEqual(self.panel.status.text(), "")
        self.assertFalse(self.panel.apply_button.isEnabled())
        self.assertFalse(self.panel.revert_button.isEnabled())

    def test_undone_with_the_mod_where_it_was_the_typing_can_be_done_again(self):
        self.apply(document(CYCLE % 15))
        before = self.panel.pane.toPlainText()
        cursor = self.panel.pane.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("x")
        self.panel.pane.document().undo()
        self.assertEqual(self.panel.pane.toPlainText(), before)
        self.assertTrue(self.panel.pane.document().isRedoAvailable())

    def test_an_op_that_could_not_be_read_is_said_and_marked(self):
        self.apply(document(CYCLE % 15, NOWHERE))
        self.assertIn("1 ModOp in the file could not be read", self.panel.status.text())
        marks = self.panel.pane.marks()
        text = self.panel.pane.toPlainText().split("\n")
        line = next(at for at, said in enumerate(text) if "99999999" in said)
        self.assertEqual(marks.get(line), "over")
        self.assertNotIn(0, marks)


class TestWhatIsDrawnAndNotText(FileTabCase):
    def setUp(self):
        super().setUp()
        self.apply(document(EVERY % 12))
        self.loadout.set_change(self.loadout.place(Change(
            guid=HUT, asset_label="Lumberjack's Hut", path="/Values/Factory7/CycleTime",
            tag="CycleTime", old_value="30", new_value="15",
        )))
        self.window.on_changes_updated()

    def test_nothing_is_said_over_a_modop_of_the_studios(self):
        """The asset's name beside the line says what it is aimed at; a
        heading over each asset's ModOps said it again."""
        self.assertNotIn(STUDIO_SAYS_MARK, self.panel.pane.toPlainText())
        self.assertEqual(self.panel.pane.headings(), [])
        lines = self.panel.pane.toPlainText().split("\n")
        at = next(at for at, line in enumerate(lines) if 'GUID="1010340"' in line)
        self.assertEqual(lines[at - 1], "")

    def test_an_op_written_as_narrow_modops_is_drawn_again(self):
        """The Mod changes panel rewrote the mod, and the tab in front went
        on showing the op it had."""
        from anno_modding_studio.ui import changes_dock

        (op,) = self.loadout.ops
        with mock.patch.object(changes_dock, "ask", return_value=True):
            self.assertTrue(self.window.changes_panel.write_narrow([op.uid]))
        text = self.panel.pane.toPlainText()
        self.assertNotIn("//Values/Residence7", text)
        self.assertEqual(text, modfile.shown(self.loadout, "").text)

    def test_one_switched_off_is_marked_as_off(self):
        op = self.loadout.ops[0]
        op.active = False
        for change in self.loadout.changes_by_op(op.uid):
            change.active = False
        self.window.on_changes_updated()
        text = self.panel.pane.toPlainText().split("\n")
        line = next(at for at, said in enumerate(text) if "//Values/Residence7" in said)
        self.assertEqual(self.panel.pane.marks().get(line), "off")


class TestTheOutline(FileTabCase):
    def setUp(self):
        super().setUp()
        self.apply(document(CYCLE % 15, GROUP % (EVERY % 12), NOWHERE))

    def test_every_op_and_group_is_a_row_in_file_order(self):
        rows = self.rows()
        self.assertEqual([depth for depth, _words, _comes in rows], [0, 0, 1, 0])
        # Each row is the tag its lines open with, as the file has it.
        self.assertEqual([words for _depth, words, _comes in rows], [
            '<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/CycleTime">',
            '<Group Condition="#NobodyHasThis">',
            '<ModOp Type="merge" Path="//Values/Residence7">',
            '<ModOp Type="replace" GUID="99999999" Path="/Values/Nope">',
        ])
        self.assertEqual(rows[1][2], "does not run")
        self.assertEqual(rows[3][2], "not read, kept as written")

    def test_every_row_has_its_words_in_one_place(self):
        """In the one face, and after the same room for the drawing, whether
        a row has the drawing's strip or not, or no drawing at all."""
        tree = self.panel.outline.tree
        rows = [tree.topLevelItem(0), tree.topLevelItem(1),
                tree.topLevelItem(1).child(0), tree.topLevelItem(2)]
        sizes = {row.icon(0).actualSize(tree.iconSize()).width() for row in rows}
        self.assertEqual(sizes, {tree.iconSize().width()})
        self.assertEqual({row.font(0).family() for row in rows}, {rows[0].font(0).family()})
        # And the whole tag over what the Mod changes panel would say.
        self.assertTrue(rows[0].toolTip(0).startswith('<ModOp Type="replace" GUID="1010340"'))

    def test_a_switched_off_op_is_its_tag_and_not_its_comment(self):
        self.loadout.ops[0].note = "slower"
        self.loadout.ops[0].active = False
        for change in self.loadout.changes_by_op(self.loadout.ops[0].uid):
            change.active = False
        self.window.on_changes_updated()
        self.open()
        rows = self.rows()
        self.assertEqual(
            rows[0][1], '<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/CycleTime">'
        )
        self.assertEqual(
            self.panel.outline.tree.topLevelItem(0).foreground(0).color().name(),
            QColor(theme.TEXT_DIM).name(),
        )

    def test_a_row_goes_to_its_op_in_the_file(self):
        tree = self.panel.outline.tree
        row = tree.topLevelItem(1).child(0)
        self.panel._go_to_row(row)
        line = self.panel.pane.textCursor().block().text()
        self.assertIn("//Values/Residence7", line)

    def test_it_narrows_to_one_kind_and_keeps_the_group_round_it(self):
        box = self.panel.narrow_box
        box.setCurrentIndex(box.findData(file_panel.PATTERN))
        tree = self.panel.outline.tree
        hidden = [tree.topLevelItem(at).isHidden() for at in range(tree.topLevelItemCount())]
        self.assertEqual(hidden, [True, False, True])
        self.assertFalse(tree.topLevelItem(1).child(0).isHidden())
        box.setCurrentIndex(box.findData(file_panel.UNREAD))
        hidden = [tree.topLevelItem(at).isHidden() for at in range(tree.topLevelItemCount())]
        self.assertEqual(hidden, [True, True, False])
        box.setCurrentIndex(box.findData(file_panel.OFF))
        self.assertEqual(self.panel.outline_note.text(), "No ModOp of this kind in the file.")

    def test_a_row_has_no_box_to_switch_it(self):
        tree = self.panel.outline.tree
        for at in range(tree.topLevelItemCount()):
            row = tree.topLevelItem(at)
            self.assertIsNone(row.data(0, Qt.ItemDataRole.CheckStateRole), row.text(0))

    def test_while_something_is_typed_it_says_what_it_is_of(self):
        self.type(document(CYCLE % 18))
        self.assertEqual(
            self.panel.outline_note.text(), "The outline is of the file as it was applied."
        )

    def test_a_row_reaches_the_assets_it_changes(self):
        item = self.panel.shown.items[0]
        self.assertEqual(self.panel.reached(item), [HUT])


class TestMovingInTheOutline(FileTabCase):
    """#196: a row dragged, or moved up and down, moves its lines in the
    text, as the cut and paste it stands for would, and applies nothing."""

    def setUp(self):
        super().setUp()
        self.apply(document(CYCLE % 15, GROUP % (EVERY % 12), NOWHERE))
        self.tree = self.panel.outline.tree
        self.first, self.group, self.last = (self.tree.topLevelItem(at) for at in range(3))

    def tags(self) -> list:
        return [words for _depth, words, _comes in self.rows()]

    def test_moved_down_the_text_says_so_and_nothing_is_applied(self):
        ops = [op.uid for op in self.loadout.ops]
        self.assertTrue(self.panel.move_row(self.first, 1))
        text = self.panel.pane.toPlainText()
        self.assertLess(text.index("#NobodyHasThis"), text.index("CycleTime"))
        self.assertTrue(self.panel.has_unapplied())
        self.assertTrue(self.panel.apply_button.isEnabled())
        self.assertEqual([op.uid for op in self.loadout.ops], ops)
        self.assertEqual(self.panel.status.text(), "Moved in the text. Apply to change the mod.")
        # The rows are of the text as it stands, and say nothing of it.
        self.assertEqual(self.tags()[:2], ['<Group Condition="#NobodyHasThis">', EVERY.split(">")[0] + ">"])
        self.assertEqual(self.tags()[2], CYCLE.split(">")[0] + ">")
        self.assertFalse(self.panel.outline_note.isVisibleTo(self.panel))
        self.assertTrue(self.panel.can_move())
        # The row moved is the one picked, and the caret is on its line.
        self.assertIn("CycleTime", self.tree.currentItem().text(0))
        self.assertIn("CycleTime", self.panel.pane.textCursor().block().text())

    def test_moves_follow_one_another_and_apply_keeps_each_op(self):
        uids = sorted(op.uid for op in self.loadout.ops)
        self.panel.move_row(self.tree.topLevelItem(0), 1)
        self.panel.move_row(self.tree.topLevelItem(2), -1)
        text = self.panel.pane.toPlainText()
        self.assertLess(text.index("Values/Nope"), text.index("CycleTime"))
        self.panel.apply_button.click()
        self.assertTrue(self.panel.status.text().startswith(
            "The ModOps are in the order and the files you typed them in."
        ))
        self.assertEqual(sorted(op.uid for op in self.loadout.ops), uids)
        self.assertFalse(self.panel.has_unapplied())
        # The group, the op in it, and then the two the other way round.
        self.assertIn("Nope", self.tags()[2])
        self.assertIn("CycleTime", self.tags()[3])

    def drop(self, row, into, before=None) -> None:
        """What the tree says when a row is let go, and the drag over."""
        thing = self.panel._thing
        self.panel._dropped(thing(row), thing(into), thing(before))
        QCoreApplication.processEvents()

    def test_dropped_into_a_group(self):
        self.drop(self.last, self.group)
        self.assertEqual(self.tree.topLevelItemCount(), 2)
        self.assertEqual(self.tree.topLevelItem(1).childCount(), 2)
        self.assertIn("Nope", self.tree.topLevelItem(1).child(1).text(0))
        self.panel.apply_button.click()
        # Applied, the mod has it in the group.
        self.assertFalse(self.panel.has_unapplied())
        self.assertEqual(self.tree.topLevelItem(1).childCount(), 2)
        self.assertIn("Nope", self.tree.topLevelItem(1).child(1).text(0))

    def test_the_tree_says_where_a_row_was_let_go(self):
        """What Qt's drop says, read as a place: above a row is before it,
        on a group is into it, and below the last is the end of the file."""
        tree = self.tree
        self.tree.setCurrentItem(self.last)
        where = file_panel.QAbstractItemView.DropIndicatorPosition

        class Let:
            def __init__(self, row):
                self.point = tree.visualItemRect(row).center()
                self.action = None

            def position(self):
                from PySide6.QtCore import QPointF

                return QPointF(self.point)

            def source(self):
                return tree

            def setDropAction(self, action):
                self.action = action

            def accept(self):
                pass

        for row, at, wanted in (
            (self.first, where.AboveItem, (self.last, None, self.first)),
            (self.group, where.OnItem, (self.last, self.group, None)),
            (self.first, where.BelowItem, (self.last, None, self.group)),
        ):
            with self.subTest(at=at), mock.patch.object(tree, "dropIndicatorPosition", return_value=at):
                self.assertEqual(tree.place(Let(row)), wanted)
        # Let go on the group: Qt moves nothing, and the file says where.
        dropped = []
        tree.dropped.connect(lambda *found: dropped.append(found))
        thing = self.panel._thing
        wanted = [(thing(self.last), thing(self.group), None)]
        let = Let(self.group)
        with mock.patch.object(tree, "dropIndicatorPosition", return_value=where.OnItem):
            tree.dropEvent(let)
        self.assertEqual(let.action, Qt.DropAction.IgnoreAction)
        QCoreApplication.processEvents()
        self.assertEqual(dropped, wanted)
        self.assertEqual(self.tree.topLevelItem(1).childCount(), 2)

    def test_a_drop_the_rows_have_moved_on_from_is_not_made(self):
        thing = self.panel._thing
        self.panel._dropped(thing(self.last), thing(self.group), None)
        # Moved another way before the drag was over: the rows are of
        # another text, and the drop was of the one before.
        self.panel.move_row(self.first, 1)
        text = self.panel.pane.toPlainText()
        QCoreApplication.processEvents()
        self.assertEqual(self.panel.pane.toPlainText(), text)

    def test_undone_then_drawn_again_then_done_again_it_is_that_move(self):
        self.panel.move_row(self.first, 1)
        moved = self.tags()
        self.panel.pane.undo()
        self.panel.refresh()
        self.panel.pane.redo()
        self.assertEqual(self.tags(), moved)
        self.assertTrue(self.panel.can_move())

    def test_a_no_break_space_in_the_file_keeps_its_place_in_undo(self):
        """Qt keeps it as a plain space: the text the pane has is not the
        file's letter for letter, and is still the file's."""
        # In an op as written, put there as no typing can put it.
        op = self.loadout.ops[0]
        op.source = op.source.replace("<CycleTime>15", "<CycleTime>1\u00a05")
        self.window.on_changes_updated()
        self.open()
        self.assertIn("\u00a0", self.panel.shown.text)
        self.assertNotEqual(self.panel.pane.toPlainText(), self.panel.shown.text)
        self.panel.move_row(self.tree.topLevelItem(0), 1)
        self.assertTrue(self.panel.has_unapplied())
        self.assertTrue(self.panel.can_move())
        self.assertTrue(self.panel.pane.document().isUndoAvailable())
        self.panel.pane.undo()
        self.assertFalse(self.panel.has_unapplied())

    def test_a_drop_goes_where_the_tree_says_and_only_where_it_can(self):
        self.assertTrue(self.panel._can_drop(self.last, self.group, None))
        self.assertTrue(self.panel._can_drop(self.last, None, self.first))
        # Not into an op, nor into itself.
        self.assertFalse(self.panel._can_drop(self.last, self.first, None))
        self.assertFalse(self.panel._can_drop(self.group, self.group, None))
        self.assertTrue(self.tree.dragEnabled())
        flags = self.first.flags()
        self.assertTrue(flags & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(flags & Qt.ItemFlag.ItemIsDropEnabled)
        self.assertTrue(self.group.flags() & Qt.ItemFlag.ItemIsDropEnabled)

    def test_qt_reads_the_rows_flags_a_group_takes_a_row_and_an_op_does_not(self):
        """Where Qt says a row held over another would go, from the rows'
        flags: into a group, and only above or below an op.

        Qt takes no drag in the outline's own mode that no drag of its own
        started, and a test cannot start one; so this asks it in the mode
        that takes any, where the rest of what it does is the same.  A drag
        from anywhere else is no place for a row (``place``), whatever Qt
        says.
        """
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QDragMoveEvent

        self.window.resize(1200, 800)
        self.window.show()
        self.addCleanup(self.window.hide)
        QCoreApplication.processEvents()
        tree = self.tree
        tree.setCurrentItem(self.last)
        mime = tree.model().mimeData([tree.indexFromItem(self.last)])
        buttons, keys = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

        def held_over(row):
            point = QPointF(tree.visualItemRect(row).center())
            return QDragMoveEvent(point, Qt.DropAction.MoveAction, mime, buttons, keys)

        where = file_panel.QAbstractItemView.DropIndicatorPosition
        # Not from this tree: nothing is a place for it.
        foreign = held_over(self.group)
        tree.dragMoveEvent(foreign)
        self.assertFalse(foreign.isAccepted())
        tree.setDragDropMode(file_panel.QAbstractItemView.DragDropMode.DragDrop)
        with mock.patch.object(tree, "place", return_value=(self.last, self.group, None)):
            over_group = held_over(self.group)
            tree.dragMoveEvent(over_group)
            self.assertTrue(over_group.isAccepted())
            self.assertEqual(tree.dropIndicatorPosition(), where.OnItem)
            # Over the middle of an op it is above or below it, never into it.
            tree.dragMoveEvent(held_over(self.first))
            self.assertIn(tree.dropIndicatorPosition(), (where.AboveItem, where.BelowItem))

    def test_undone_in_the_text_the_rows_are_as_they_were(self):
        before = self.tags()
        self.panel.move_row(self.first, 1)
        self.panel.pane.undo()
        self.assertFalse(self.panel.has_unapplied())
        self.assertEqual(self.tags(), before)
        self.panel.pane.redo()
        self.assertEqual(self.tags()[0], '<Group Condition="#NobodyHasThis">')
        self.assertTrue(self.panel.can_move())

    def test_once_something_is_typed_the_rows_do_not_move(self):
        self.panel.move_row(self.first, 1)
        self.panel.pane.textCursor().insertText(" ")
        self.assertFalse(self.panel.can_move())
        self.assertFalse(self.tree.dragEnabled())
        self.assertFalse(self.panel.move_row(self.tree.topLevelItem(0), 1))
        self.assertEqual(
            self.panel.outline_note.text(),
            "The outline is of the file as it was before you typed in it.",
        )

    def test_revert_puts_the_rows_back_too(self):
        before = self.tags()
        self.panel.move_row(self.first, 1)
        self.panel.revert_button.click()
        self.assertEqual(self.tags(), before)
        self.assertFalse(self.panel.has_unapplied())

    def test_not_while_the_outline_is_narrowed(self):
        box = self.panel.narrow_box
        box.setCurrentIndex(box.findData(file_panel.PATTERN))
        self.assertFalse(self.panel.can_move())
        self.assertFalse(self.panel.move_row(self.first, 1))

    def test_the_menu_and_the_keys(self):
        menu = self.panel.menu_for(self.first)
        self.addCleanup(menu.deleteLater)
        actions = {action.text(): action for action in menu.actions()}
        self.assertFalse(actions["Move up"].isEnabled())
        self.assertTrue(actions["Move down"].isEnabled())
        self.assertEqual(actions["Move down"].shortcut().toString(), "Alt+Down")
        from PySide6.QtGui import QShortcut

        keys = {
            shortcut.key().toString(): shortcut
            for shortcut in self.tree.findChildren(QShortcut)
        }
        self.assertEqual(keys["Alt+Down"].context(), Qt.ShortcutContext.WidgetShortcut)
        self.tree.setCurrentItem(self.first)
        keys["Alt+Down"].activated.emit()
        self.assertTrue(self.panel.has_unapplied())
        self.assertEqual(self.tags()[0], '<Group Condition="#NobodyHasThis">')


class TestATweakToolsMarkup(FileTabCase):
    """#174: an ``<ImyaExpose>`` is no op, so nothing says one was not read."""

    def setUp(self):
        super().setUp()
        self.apply(document(TAGGED % 15, EXPOSE))

    def test_it_stays_in_the_file_and_nothing_marks_it(self):
        self.assertIn("<ImyaExpose", self.panel.pane.toPlainText())
        self.assertEqual(len(self.loadout.unread), 1)
        self.assertNotIn("could not be read", self.panel.status.text())
        self.assertEqual(self.panel.pane.marks(), {})

    def test_it_is_no_row_of_the_outline(self):
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("CycleTime", rows[0][1])

    def test_no_list_or_count_of_unread_ops_has_it(self):
        from anno_modding_studio.ui import unread_dialog

        self.assertEqual(self.loadout.not_read(), [])
        self.assertEqual(unread_dialog.summarise(self.loadout), "")
        self.assertFalse(self.window.unread_action.isEnabled())
        kinds = []

        def walk(item):
            self.window.changes_panel._build_rows(item)
            kinds.append(item.data(0, KIND_ROLE))
            for at in range(item.childCount()):
                walk(item.child(at))

        tree = self.window.changes_panel.tree
        for at in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(at))
        self.assertNotIn("unread", kinds)

    def test_an_op_beside_it_that_was_not_read_is_still_said(self):
        self.apply(document(TAGGED % 15, EXPOSE, NOWHERE))
        self.assertIn("1 ModOp in the file could not be read", self.panel.status.text())
        self.assertEqual(len(self.loadout.not_read()), 1)
        self.assertEqual(sorted(set(self.panel.pane.marks().values())), ["over"])
        self.assertEqual(self.rows()[-1][2], "not read, kept as written")
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(self.window.unread_action.isEnabled())


class TestShowInFile(FileTabCase):
    def setUp(self):
        super().setUp()
        self.apply(document(CYCLE % 15, EVERY % 12))
        self.window.open_tab(TAB_EDIT)

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

    def test_an_ops_row_goes_to_it(self):
        op = next(op for op in self.loadout.ops if op.searches)
        self.window.changes_panel.fileRequested.emit("", "op", op.uid)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_FILE)
        self.assertIn("//Values/Residence7", self.panel.pane.textCursor().block().text())

    def test_a_change_goes_to_the_op_it_is_written_as(self):
        change = next(c for c in self.loadout.changes.values() if c.guid == HUT)
        self.window.changes_panel.fileRequested.emit("", "change", list(change.key))
        self.assertEqual(self.window.tabs.currentIndex(), TAB_FILE)
        self.assertIn("/Values/Factory7/CycleTime", self.panel.pane.textCursor().block().text())

    def test_the_menu_offers_it_on_a_row_that_is_written_as_an_op(self):
        panel = self.window.changes_panel
        for kind in ("change", "op"):
            with self.subTest(row=kind):
                menu = panel.menu_for(self.row(kind))
                self.addCleanup(menu.deleteLater)
                self.assertIn("Show in file", [action.text() for action in menu.actions()])
        mod = panel.menu_for(self.row("mod"))
        self.addCleanup(mod.deleteLater)
        self.assertNotIn("Show in file", [action.text() for action in mod.actions()])

    def test_with_something_typed_it_says_why_it_does_not_go(self):
        self.window.open_tab(TAB_FILE)
        self.type(document(CYCLE % 18))
        op = next(op for op in self.loadout.ops if op.searches)
        self.window.changes_panel.fileRequested.emit("", "op", op.uid)
        self.assertIn("not applied", self.window.status_label.text())


class TestLeavingWhatWasTyped(FileTabCase):
    def test_closing_the_loadout_asks_and_apply_applies(self):
        self.open()
        self.type(document(CYCLE % 15))
        with mock.patch.object(
            self.window.prompts, "ask_unapplied_file", return_value="apply"
        ) as asked:
            self.assertTrue(self.panel.settle_typed())
        asked.assert_called_once_with("My Anno Mod")
        self.assertEqual(len(self.loadout.ops), 1)

    def test_discard_puts_the_file_back(self):
        self.open()
        self.type(document(CYCLE % 15))
        with mock.patch.object(self.window.prompts, "ask_unapplied_file", return_value="discard"):
            self.assertTrue(self.panel.settle_typed())
        self.assertEqual(self.loadout.ops, [])
        self.assertFalse(self.panel.has_unapplied())

    def test_cancel_stays(self):
        self.open()
        self.type(document(CYCLE % 15))
        with mock.patch.object(self.window.prompts, "ask_unapplied_file", return_value=""):
            self.assertFalse(self.window.files.confirm_discard())
        self.assertTrue(self.panel.has_unapplied())


class TestFindingInTheFile(FileTabCase):
    def test_find_is_for_the_file_while_it_is_in_front(self):
        self.apply(document(CYCLE % 15))
        self.window._find_in_asset()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_FILE)
        self.assertFalse(self.panel.find_bar.isHidden())
        self.panel.find_bar.box.setText("CycleTime")
        self.assertEqual(self.panel.find_bar.count.text(), "3 matches")


class TestTheModChangesPanelFolds(FileTabCase):
    def test_it_folds_to_a_strip_and_opens_from_it(self):
        window = self.window
        window.resize(1500, 900)
        window.show()
        self.addCleanup(window.hide)
        _app.processEvents()
        self.assertTrue(window.changes_strip.isHidden())
        window.toggle_changes.setChecked(False)
        _app.processEvents()
        self.assertTrue(window.changes_body.isHidden())
        self.assertFalse(window.changes_strip.isHidden())
        self.assertLess(window.changes_side.width(), 60)
        window.changes_strip.click()
        _app.processEvents()
        self.assertTrue(window.toggle_changes.isChecked())
        self.assertFalse(window.changes_body.isHidden())
        self.assertGreaterEqual(window.changes_side.width(), 340)

    def test_it_has_a_key_and_says_so(self):
        self.assertEqual(self.window.toggle_changes.shortcut().toString(), "F9")
        self.assertIn("F9", self.window.changes_fold.toolTip())
        self.assertIn("F9", self.window.changes_strip.toolTip())
        self.assertTrue(self.window.changes_fold.accessibleName())
        self.assertTrue(self.window.changes_strip.accessibleName())

    def test_the_window_remembers_which(self):
        window = self.window
        window.toggle_changes.setChecked(False)
        with mock.patch.object(window.files, "confirm_discard", return_value=True):
            window.close()
        self.assertEqual(window.settings.value("changesFolded"), "yes")
        again = MainWindow()
        self.addCleanup(again.deleteLater)
        self.assertFalse(again.toggle_changes.isChecked())
        self.assertTrue(again.changes_body.isHidden())

    def test_folded_at_close_it_opens_next_time_at_the_width_it_was_dragged_to(self):
        """Folded as the window opened, the width kept to open it at again
        was read off the splitter before it had been laid out, and the panel
        opened at the 340 floor whatever it had been dragged to."""
        window = self.window
        window.resize(2600, 900)
        window.show()
        _app.processEvents()
        total = sum(window.splitter.sizes())
        window.splitter.setSizes([total - 700, 700])
        _app.processEvents()
        self.assertEqual(window.splitter.sizes()[1], 700)
        window.toggle_changes.setChecked(False)
        with mock.patch.object(window.files, "confirm_discard", return_value=True):
            window.close()
        again = MainWindow()
        self.addCleanup(again.deleteLater)
        again.resize(2600, 900)
        again.show()
        self.addCleanup(again.hide)
        _app.processEvents()
        again.toggle_changes.setChecked(True)
        _app.processEvents()
        self.assertEqual(again.splitter.sizes()[1], 700)


class TestTheOutlineFolds(FileTabCase):
    def test_it_folds_to_a_strip_and_opens_from_it_at_the_width_it_had(self):
        window = self.window
        window.resize(1800, 900)
        window.show()
        self.addCleanup(window.hide)
        self.open()
        _app.processEvents()
        panel = self.panel
        self.assertTrue(panel.outline_strip.isHidden())
        total = sum(panel.splitter.sizes())
        panel.splitter.setSizes([total - 450, 450])
        _app.processEvents()
        panel.outline_fold.click()
        _app.processEvents()
        self.assertFalse(panel.toggle_outline.isChecked())
        self.assertTrue(panel.outline_body.isHidden())
        self.assertFalse(panel.outline_strip.isHidden())
        self.assertLess(panel.outline_side.width(), 60)
        # The file has the width.
        self.assertGreater(panel.pane.width(), total - 60)
        panel.outline_strip.click()
        _app.processEvents()
        self.assertTrue(panel.toggle_outline.isChecked())
        self.assertFalse(panel.outline_body.isHidden())
        self.assertEqual(panel.splitter.sizes()[1], 450)

    def test_it_has_a_key_and_says_so(self):
        panel = self.panel
        self.assertEqual(panel.toggle_outline.shortcut().toString(), "F8")
        self.assertIn("F8", panel.outline_fold.toolTip())
        self.assertIn("F8", panel.outline_strip.toolTip())
        self.assertTrue(panel.outline_fold.accessibleName())
        self.assertTrue(panel.outline_strip.accessibleName())
        # In the View menu, beside the Mod changes panel.
        view = next(
            action.menu() for action in self.window.menuBar().actions()
            if action.text() == "&View"
        )
        self.assertIn(panel.toggle_outline, view.actions())

    def test_the_window_remembers_which(self):
        window = self.window
        self.panel.toggle_outline.setChecked(False)
        with mock.patch.object(window.files, "confirm_discard", return_value=True):
            window.close()
        self.assertEqual(window.settings.value("fileOutlineFolded"), "yes")
        again = MainWindow()
        self.addCleanup(again.deleteLater)
        self.assertFalse(again.file_panel.toggle_outline.isChecked())
        self.assertTrue(again.file_panel.outline_body.isHidden())


class TestWhoseFileIsShown(FileTabCase):
    """The tab picks whose file it shows, apart from where changes go."""

    THEIRS = "[Gameplay] Their Mod"

    def setUp(self):
        super().setUp()
        self.apply(document(CYCLE % 15))
        self.loadout.set_layer(ModLayer(origin=self.THEIRS, name="Their Mod", order=1))
        shown = modfile.shown(self.loadout, self.THEIRS)
        modfile.apply(self.window.workspace, self.THEIRS, document(EVERY % 12), shown)
        # As read from their file, and not typed here: edits of yours to
        # their mod would be ones the loadout's own mod keeps already.
        for held in (self.loadout.ops, self.loadout.changes.values()):
            for one in held:
                if one.origin == self.THEIRS:
                    one.authored = False
        self.window.forget_undo()
        self.window.on_changes_updated()
        # Their mod is never changed: Edit this file asks the three ways on.
        asked = mock.patch.object(self.window.prompts, "ask_way_on", return_value="")
        self.asked = asked.start()
        self.addCleanup(asked.stop)

    def pick(self, origin: str) -> None:
        box = self.panel.file_box
        box.setCurrentIndex(box.findData(origin))

    def test_a_loadout_opened_under_it_is_the_one_whose_file_it_shows(self):
        """It went on saying "The file of My Anno Mod", with no picker, over
        a loadout of forty mods: drawn once for the empty loadout the window
        starts with, and never told that another had been opened."""
        self.loadout.own.name = "The list patch"
        path = Path(self._tmp.name) / "opened.annoload"
        self.loadout.save(path)
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True),                 mock.patch.object(self.window.prompts, "ask_missing_mods", return_value=False):
            self.window.new_loadout()
            self.panel.refresh()
            self.assertEqual(self.panel.pane.accessibleName(), "The file of My Anno Mod")
            self.assertTrue(self.panel.file_box.isHidden())
            self.assertTrue(self.window.load_loadout(path))
        self.open_as_it_is()
        self.assertFalse(self.panel.file_box.isHidden())
        self.assertEqual(self.panel.pane.accessibleName(), "The file of The list patch")
        self.assertIn("CycleTime", self.panel.pane.toPlainText())
        # And a new loadout after it is not that one still.
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.window.new_loadout()
        self.assertTrue(self.panel._waiting or "CycleTime" not in self.panel.pane.toPlainText())

    def test_it_starts_on_the_mod_changes_go_to(self):
        self.open()
        self.assertFalse(self.panel.file_box.isHidden())
        # The picker says whose file it is, so the heading does not as well.
        self.assertEqual(self.panel.heading.text(), "")
        self.assertEqual(self.panel.pane.accessibleName(), "The file of My Anno Mod")
        self.assertEqual(self.panel.file_box.currentData(), "")
        self.assertIn("(yours)", self.panel.file_box.currentText())
        self.assertFalse(self.panel.pane.isReadOnly())
        self.assertTrue(self.panel.look_bar.isHidden())
        self.assertTrue(self.panel.edit_button.isHidden())

    def test_edit_this_file_is_beside_the_picker_while_a_file_is_only_shown(self):
        """It was at the far end of the bar under the picker, below the help
        and the window's buttons, where it read as nothing to do with the
        file picked, and a file only shown looked like one that could not be
        typed in at all."""
        self.open()
        self.pick(self.THEIRS)
        row = self.panel.top_row
        self.assertEqual(row.indexOf(self.panel.edit_button), row.indexOf(self.panel.file_box) + 1)
        self.assertFalse(self.panel.edit_button.isHidden())
        self.pick("")
        self.assertTrue(self.panel.edit_button.isHidden())

    def test_another_mod_is_shown_without_changes_going_to_it(self):
        self.open()
        self.pick(self.THEIRS)
        self.assertIn("//Values/Residence7", self.panel.pane.toPlainText())
        self.assertEqual(self.loadout.editing_origin(), "")
        self.assertEqual(self.window.editor.target_box.currentData(), "")
        # And nobody was asked whether to patch it.
        self.asked.assert_not_called()

    def test_a_file_only_looked_at_is_not_typed_in(self):
        self.open()
        self.pick(self.THEIRS)
        self.assertTrue(self.panel.pane.isReadOnly())
        self.assertFalse(self.panel.look_bar.isHidden())
        self.assertIn("Only shown. Your changes go to My Anno Mod", self.panel.look_note.text())
        self.assertFalse(self.panel.apply_button.isEnabled())
        # The outline is of the file shown.
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn("CycleTime", str(self.rows()))

    def test_edit_this_file_on_their_mod_asks_the_three_ways_on(self):
        self.open()
        self.pick(self.THEIRS)
        self.panel.edit_button.click()
        # Asked as somebody who wanted to type in this file.
        self.asked.assert_called_once_with("Their Mod", "", True)
        # Cancelled, nothing moved.
        self.assertEqual(self.loadout.editing_origin(), "")
        self.assertEqual(self.panel.origin(), self.THEIRS)
        self.assertTrue(self.panel.pane.isReadOnly())

    def test_asked_from_the_file_the_question_says_which_file_it_leads_to(self):
        from anno_modding_studio.ui import prompts

        # The question itself, and not the stand-in the other tests answer by.
        real = type(self.window.prompts).ask_way_on
        with mock.patch.object(prompts.QMessageBox, "exec"), \
                mock.patch.object(prompts.QMessageBox, "setText") as said:
            real(self.window.prompts, "Their Mod", "Their Mod (my patch)", True)
            real(self.window.prompts, "Their Mod", "Their Mod (my patch)")
        from_file, from_elsewhere = (call.args[0] for call in said.call_args_list)
        self.assertIn("This tab then shows that mod's file.", from_file)
        self.assertNotIn("This tab", from_elsewhere)

    def test_their_mod_is_listed_to_look_at_and_not_where_changes_go(self):
        self.open()
        self.assertGreaterEqual(self.panel.file_box.findData(self.THEIRS), 0)
        self.assertEqual(self.window.editor.target_box.findData(self.THEIRS), -1)

    def test_where_a_patch_is_made_its_file_is_the_one_to_be_in(self):
        self.asked.return_value = "patch"
        self.open()
        self.pick(self.THEIRS)
        self.panel.edit_button.click()
        patch = self.loadout.editing_origin()
        self.assertNotIn(patch, ("", self.THEIRS))
        self.assertEqual(self.panel.origin(), patch)
        self.assertFalse(self.panel.pane.isReadOnly())
        # Their file is still there to look at.
        self.pick(self.THEIRS)
        self.assertIn("//Values/Residence7", self.panel.pane.toPlainText())
        self.assertEqual(self.loadout.editing_origin(), patch)

    def test_with_a_patch_already_made_it_offers_the_patch(self):
        """Their mod with a patch of yours in front of it: *Edit this file*
        switched changes straight to their file, with no question, where the
        README says it asks (H2-8)."""
        from anno_modding_studio.core import patching

        patch = patching.make(self.loadout, self.THEIRS).origin
        self.window.editor.refresh_target()
        self.open()
        self.pick(self.THEIRS)
        self.asked.return_value = "patch"
        self.panel.edit_button.click()
        self.assertEqual(self.asked.call_args.args[:2], ("Their Mod", self.loadout.layer(patch).label))
        self.assertEqual(self.loadout.editing_origin(), patch)
        self.assertEqual(self.panel.origin(), patch)
        self.assertFalse(self.panel.pane.isReadOnly())

    def test_answered_with_the_loadouts_own_mod_the_tab_goes_to_its_file(self):
        self.open()
        self.pick(self.THEIRS)
        self.asked.return_value = "own"
        self.panel.edit_button.click()
        self.assertEqual(self.loadout.editing_origin(), "")
        self.assertEqual(self.panel.origin(), "")
        self.assertFalse(self.panel.pane.isReadOnly())
        self.assertIn("CycleTime", self.panel.pane.toPlainText())

    def test_once_the_loadouts_own_mod_keeps_their_changes_it_is_not_asked_again(self):
        """Where a change to their mod goes was worked out here apart from
        everywhere else, and without the loadout's own mod already keeping
        that mod's changes: the three ways on were asked at every *Edit this
        file*, where a change to one of their patterns goes there unasked."""
        self.open()
        self.pick(self.THEIRS)
        self.asked.return_value = "own"
        self.panel.edit_button.click()
        self.pick(self.THEIRS)
        self.panel.edit_button.click()
        self.asked.assert_called_once()
        self.assertEqual(self.loadout.editing_origin(), "")
        self.assertEqual(self.panel.origin(), "")
        self.assertFalse(self.panel.pane.isReadOnly())

    def test_with_changes_going_to_the_patch_it_goes_to_the_patch_unasked(self):
        """As run 2 had it: changes going to the patch, their file only shown."""
        from anno_modding_studio.core import patching

        patch = patching.make(self.loadout, self.THEIRS).origin
        self.loadout.editing = patch
        self.window.editor.refresh_target()
        self.open()
        self.pick(self.THEIRS)
        self.assertTrue(self.panel.pane.isReadOnly())
        self.panel.edit_button.click()
        self.asked.assert_not_called()
        self.assertEqual(self.loadout.editing_origin(), patch)
        self.assertEqual(self.panel.origin(), patch)
        self.assertFalse(self.panel.pane.isReadOnly())

    # -- Changes go to: mods of yours only -----------------------------------
    def _with_a_patch(self) -> str:
        from anno_modding_studio.core import patching

        patch = patching.make(self.loadout, self.THEIRS).origin
        self.window.editor.refresh_target()
        self.assertEqual(self.loadout.editing_origin(), "")
        return patch

    def _point_at(self, origin: str) -> None:
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(origin))

    def test_no_question_for_the_patch_or_the_loadouts_own_mod(self):
        patch = self._with_a_patch()
        self._point_at(patch)
        self.assertEqual(self.loadout.editing_origin(), patch)
        self._point_at("")
        self.assertEqual(self.loadout.editing_origin(), "")
        self.asked.assert_not_called()

    def test_no_question_for_a_loaded_mod_of_yours_with_a_patch(self):
        from anno_modding_studio.core import patching

        self.loadout.own.creator = "Me"
        self.loadout.layer(self.THEIRS).creator = "Me"
        self.loadout.layer(self.THEIRS).kept = True
        patching.make(self.loadout, self.THEIRS)
        self.window.editor.refresh_target()
        self._point_at(self.THEIRS)
        self.assertEqual(self.loadout.editing_origin(), self.THEIRS)
        self.asked.assert_not_called()

    def test_it_follows_where_changes_go_when_that_moves(self):
        patch = self._with_a_patch()
        self.open()
        self.pick(self.THEIRS)
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(patch))
        box.setCurrentIndex(box.findData(""))
        self.assertEqual(self.panel.origin(), "")
        self.assertFalse(self.panel.pane.isReadOnly())

    def test_picking_the_mod_changes_go_to_is_typing_in_it_again(self):
        self.open()
        self.pick(self.THEIRS)
        self.pick("")
        self.assertIsNone(self.panel.looking)
        self.assertFalse(self.panel.pane.isReadOnly())
        self.assertIn("CycleTime", self.panel.pane.toPlainText())

    def test_something_typed_is_asked_about_before_another_file_is_shown(self):
        self.open()
        self.type(document(CYCLE % 18))
        with mock.patch.object(self.window.prompts, "ask_unapplied_file", return_value="") as ask:
            self.pick(self.THEIRS)
        ask.assert_called_once()
        self.assertEqual(self.panel.origin(), "")
        self.assertEqual(self.panel.file_box.currentData(), "")
        self.assertIn("<CycleTime>18</CycleTime>", self.panel.pane.toPlainText())

    def test_something_typed_is_asked_about_before_changes_go_to_another_mod(self):
        """Picked in the asset editor, the other mod's file was drawn over
        what was typed here, and nothing asked."""
        patch = self._with_a_patch()
        self.open()
        self.type(document(CYCLE % 18))
        box = self.window.editor.target_box
        with mock.patch.object(self.window.prompts, "ask_unapplied_file", return_value="") as ask:
            box.setCurrentIndex(box.findData(patch))
        ask.assert_called_once()
        self.asked.assert_not_called()
        # Stayed: changes go where they went, and the text is still there.
        self.assertEqual(self.loadout.editing_origin(), "")
        self.assertEqual(box.currentData(), "")
        self.assertTrue(self.panel.has_unapplied())
        self.assertIn("<CycleTime>18</CycleTime>", self.panel.pane.toPlainText())
        # Discarded, they go to the other mod.
        with mock.patch.object(self.window.prompts, "ask_unapplied_file", return_value="discard"):
            box.setCurrentIndex(box.findData(patch))
        self.assertEqual(self.loadout.editing_origin(), patch)
        self.assertFalse(self.panel.has_unapplied())

    def test_show_in_file_shows_another_mods_op_and_leaves_changes_where_they_go(self):
        op = next(op for op in self.loadout.ops if op.origin == self.THEIRS)
        self.window.changes_panel.fileRequested.emit(self.THEIRS, "op", op.uid)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_FILE)
        self.assertEqual(self.panel.origin(), self.THEIRS)
        self.assertIn("//Values/Residence7", self.panel.pane.textCursor().block().text())
        self.assertEqual(self.loadout.editing_origin(), "")
        self.asked.assert_not_called()

    def test_a_mod_that_is_unloaded_is_not_looked_at_any_more(self):
        self.open()
        self.pick(self.THEIRS)
        self.loadout.drop_layer(self.THEIRS)
        self.window.on_changes_updated()
        self.assertEqual(self.panel.origin(), "")
        self.assertFalse(self.panel.pane.isReadOnly())



# The product a factory makes, swapped for Bricks: a GUID in a field that links.
BRICKS = (
    '<ModOp Type="replace" GUID="1010340" '
    'Path="/Values/Factory7/FactoryOutputs/Item/Product"><Product>1010197</Product></ModOp>'
)


class TestNamesBesideTheLines(FileTabCase):
    """The name of each asset a number links to, drawn after its line: no
    part of the text, and a way to the asset."""

    def setUp(self):
        super().setUp()
        self.apply(document(BRICKS))
        self.window.resize(1500, 900)
        self.window.show()
        self.addCleanup(self.window.hide)
        _app.processEvents()
        self.pane = self.panel.pane

    def line_of(self, said: str) -> int:
        return next(
            at for at, line in enumerate(self.pane.toPlainText().split("\n")) if said in line
        )

    def name_point(self, line: int):
        """Where the first name beside ``line`` is drawn, in the viewport."""
        block = self.pane.document().findBlockByNumber(line)
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        ((where, _guid, _name, _tip), *_rest), _chip = self.pane._name_boxes(block, box)
        return where.center().toPoint()

    def test_what_a_number_links_to_is_named_and_is_no_part_of_the_text(self):
        named = self.pane.named_assets()
        self.assertEqual(named[self.line_of("<Product>")], [("1010197", "Bricks")])
        self.assertEqual(named[self.line_of('GUID="1010340"')], [("1010340", "Lumberjack's Hut")])
        self.assertNotIn("Bricks", self.pane.toPlainText())

    def test_a_click_on_a_name_opens_its_asset_and_leaves_the_caret(self):
        caret = self.pane.textCursor().position()
        QTest.mouseClick(
            self.pane.viewport(), Qt.MouseButton.LeftButton, pos=self.name_point(self.line_of("<Product>"))
        )
        self.assertEqual(self.window.editor.guid, "1010197")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertEqual(self.pane.textCursor().position(), caret)

    def test_the_menu_of_the_line_opens_it_too(self):
        line = self.line_of("<Product>")
        block = self.pane.document().findBlockByNumber(line)
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        menu = self.pane.menu_at(box.center().toPoint())
        self.addCleanup(menu.deleteLater)
        (opening,) = [action for action in menu.actions() if action.text() == "Open Bricks"]
        self.assertIs(menu.actions()[0], opening)
        opening.trigger()
        self.assertEqual(self.window.editor.guid, "1010197")

    def test_a_line_typed_in_says_nothing_until_typing_stops(self):
        line = self.line_of("<Product>")
        block = self.pane.document().findBlockByNumber(line)
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + block.text().index("1010197"))
        cursor.setPosition(cursor.position() + 7, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText("1010202")
        self.assertNotIn(line, self.pane.named_assets())
        self.assertTrue(self.pane._naming.isActive())
        # As the pause after typing does.
        self.pane.name_assets()
        self.assertEqual(self.pane.named_assets()[line], [("1010202", "Steel")])

    def test_naming_a_file_looks_at_the_loadout_once(self):
        """Each name asked the workspace, and each question took the whole
        loadout's fingerprint again: 107 times, and seconds, for the longest
        file of 417 mods."""
        workspace = self.window.workspace
        self.pane._namer.forget()
        with mock.patch.object(workspace, "_fingerprint", wraps=workspace._fingerprint) as looked:
            self.pane.name_assets()
        self.assertGreaterEqual(len(self.pane.named_assets()), 2)
        self.assertLessEqual(looked.call_count, 1)

    def test_a_line_past_the_edge_has_its_names_at_the_edge(self):
        line = self.line_of('GUID="1010340"')
        block = self.pane.document().findBlockByNumber(line)
        self.pane.resize(400, self.pane.height())
        _app.processEvents()
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        ((where, guid, _name, tip),), chip = self.pane._name_boxes(block, box)
        self.assertEqual(guid, "1010340")
        self.assertIsNotNone(chip)
        self.assertLessEqual(chip.right(), self.pane.viewport().width())
        self.assertEqual(self.pane.name_at(where.center()), ("1010340", tip))
        # With the caret on the line, the line is read whole.
        self.pane.setTextCursor(QTextCursor(block))
        boxes, chip = self.pane._name_boxes(block, box)
        self.assertIsNone(chip)
        self.assertGreater(boxes[0][0].left(), self.pane.viewport().width())

    def test_a_long_line_scrolled_to_its_end_has_its_names_after_it(self):
        """Pinned to the edge, a name covered the end of the line however
        far it was scrolled: the pane scrolled no further than the line."""
        line = self.line_of('GUID="1010340"')
        block = self.pane.document().findBlockByNumber(line)
        self.pane.resize(400, self.pane.height())
        _app.processEvents()
        bar = self.pane.horizontalScrollBar()
        bar.setValue(bar.maximum())
        _app.processEvents()
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        ends = box.left() + self.pane.document().documentMargin() \
            + block.layout().lineAt(0).naturalTextWidth()
        self.assertLess(ends, self.pane.viewport().width())
        ((where, guid, _name, tip),), chip = self.pane._name_boxes(block, box)
        self.assertEqual(guid, "1010340")
        self.assertIsNone(chip)
        self.assertGreater(where.left(), ends)
        self.assertLessEqual(where.right(), self.pane.viewport().width())
        self.assertEqual(self.pane.name_at(where.center()), ("1010340", tip))
        # And every other line's names are after it too.
        block = self.pane.document().firstBlock()
        while block.isValid():
            box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
            boxes, chip = self.pane._name_boxes(block, box)
            if boxes:
                self.assertIsNone(chip, block.text())
                self.assertGreater(
                    boxes[0][0].left(),
                    box.left() + block.layout().lineAt(0).naturalTextWidth(),
                    block.text(),
                )
            block = block.next()

    def test_what_is_said_at_a_long_lines_end_is_after_it_scrolled_there(self):
        """What is said of an op that goes elsewhere was pinned over the end
        of a long line however far it was scrolled, as the names were."""
        line = self.line_of('GUID="1010340"')
        block = self.pane.document().findBlockByNumber(line)
        self.pane.set_asides([(line + 1, "Goes to Lumberjack's Hut", line + 1)])
        self.pane.setTextCursor(QTextCursor(block.next()))
        self.pane.resize(900, self.pane.height())
        _app.processEvents()
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        chip = self.pane._aside_chip(block, box, "Goes to Lumberjack's Hut")
        # Scrolled to the start, at the edge over the line, the names'
        # chip up to it with none of the line between.
        self.assertLessEqual(chip.right(), self.pane.viewport().width())
        _boxes, names_chip = self.pane._name_boxes(block, box)
        self.assertEqual(names_chip.right(), chip.left())
        bar = self.pane.horizontalScrollBar()
        bar.setValue(bar.maximum())
        _app.processEvents()
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        ends = box.left() + self.pane.document().documentMargin() \
            + block.layout().lineAt(0).naturalTextWidth()
        chip = self.pane._aside_chip(block, box, "Goes to Lumberjack's Hut")
        self.assertGreater(chip.left(), ends)
        self.assertLessEqual(chip.right(), self.pane.viewport().width())
        # And the names between the two, over neither.
        ((where, guid, _name, _tip),), names_chip = self.pane._name_boxes(block, box)
        self.assertEqual(guid, "1010340")
        self.assertIsNone(names_chip)
        self.assertGreater(where.left(), ends)
        self.assertLess(where.right(), chip.left())
        # Gone, the room for it goes too.
        full = bar.maximum()
        self.pane.set_asides(())
        _app.processEvents()
        self.assertLessEqual(bar.maximum(), full - chip.width())

    def test_a_line_that_ends_in_view_has_as_many_names_after_it_as_there_is_room_for(self):
        self.pane.setPlainText(
            '<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/FactoryOutputs/'
            "Item[Product='1010202']/Product\"><Product>1010197</Product></ModOp>\n<Other />"
        )
        self.pane.name_assets()
        block = self.pane.document().firstBlock()
        self.assertEqual(len(self.pane.named_assets()[0]), 3)
        # The caret off the line: the line it is on is read whole.
        self.pane.setTextCursor(QTextCursor(block.next()))
        metrics = QFontMetricsF(self.pane._names_font())
        ends = self.pane.document().documentMargin() + block.layout().lineAt(0).naturalTextWidth()
        # Scrolled to leave room after the line for its first name and the
        # count of the rest, and not for all three.
        room = ends + metrics.horizontalAdvance("  ") * 3 + self.pane._laid(
            metrics, self.pane._names_on(block)[:1], 3
        )[1] + 2
        bar = self.pane.horizontalScrollBar()
        bar.setValue(int(room) - self.pane.viewport().width())
        _app.processEvents()
        self.assertEqual(bar.value(), int(room) - self.pane.viewport().width())
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        boxes, chip = self.pane._name_boxes(block, box)
        self.assertIsNone(chip)
        self.assertEqual([guid for _where, guid, _name, _tip in boxes], ["1010340", None])
        self.assertGreater(boxes[0][0].left(), box.left() + ends)
        self.assertLessEqual(boxes[-1][0].right(), self.pane.viewport().width())
        # Scrolled to its end, all three.
        bar.setValue(bar.maximum())
        _app.processEvents()
        box = self.pane.blockBoundingGeometry(block).translated(self.pane.contentOffset())
        boxes, chip = self.pane._name_boxes(block, box)
        self.assertIsNone(chip)
        self.assertEqual(len(boxes), 3)
        self.assertLessEqual(boxes[-1][0].right(), self.pane.viewport().width())

    def test_a_line_typed_above_takes_its_name_with_it(self):
        line = self.line_of("<Product>")
        QTextCursor(self.pane.document()).insertText("<!-- mine -->\n")
        self.assertEqual(self.pane.named_assets()[line + 1], [("1010197", "Bricks")])


if __name__ == "__main__":
    unittest.main()
