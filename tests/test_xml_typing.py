"""What typing in an XML pane does besides type (``ui.asset_xml.XmlPane``):
end tags it closes, and lines it indents.  The same pane is the XML view's
and the Mod file tab's, so it is tested by itself.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.ui.asset_xml import XmlPane  # noqa: E402

_app = QApplication.instance() or QApplication([])

# Where the caret is in what a test expects.
CARET = "|"


class TypingCase(unittest.TestCase):
    def setUp(self):
        self.pane = XmlPane()
        self.pane.set_editable(True)

    def tearDown(self):
        self.pane.deleteLater()

    def given(self, text: str) -> None:
        """The pane holds ``text``, the caret where ``CARET`` is."""
        at = text.index(CARET)
        self.pane.setPlainText(text.replace(CARET, "", 1))
        cursor = self.pane.textCursor()
        cursor.setPosition(at)
        self.pane.setTextCursor(cursor)

    def key(self, key, text="", modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        self.pane.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))

    def type(self, text: str) -> None:
        """Type ``text`` a key at a time, as the keyboard would."""
        keys = {">": Qt.Key.Key_Greater, "<": Qt.Key.Key_Less, "/": Qt.Key.Key_Slash}
        for char in text:
            if char == "\n":
                self.key(Qt.Key.Key_Return)
            else:
                self.key(keys.get(char, Qt.Key.Key_A), char)

    def shown(self) -> str:
        """The pane's text, ``CARET`` where its caret is."""
        text = self.pane.toPlainText()
        at = self.pane.textCursor().position()
        return text[:at] + CARET + text[at:]


class TestClosingTags(TypingCase):
    def test_gt_that_ends_an_opening_tag_puts_its_end_tag_after_the_caret(self):
        self.given("<ModOps>\n  |\n</ModOps>")
        self.type('<ModOp Type="add" GUID="1010340">')
        self.assertEqual(
            self.shown(), '<ModOps>\n  <ModOp Type="add" GUID="1010340">|</ModOp>\n</ModOps>'
        )

    def test_and_one_with_no_attributes(self):
        self.given("|")
        self.type("<Values>")
        self.assertEqual(self.shown(), "<Values>|</Values>")

    def test_a_gt_in_an_xpath_closes_nothing(self):
        """A Path is XPath, and its ``>`` is in a quoted value."""
        self.given("|")
        self.type('<ModOp Type="merge" Path="//Asset[Values/Item/Amount > 0]"')
        self.assertEqual(
            self.shown(), '<ModOp Type="merge" Path="//Asset[Values/Item/Amount > 0]"|'
        )
        # The tag's own ``>``, after the value, does.
        self.type(">")
        self.assertEqual(
            self.shown(),
            '<ModOp Type="merge" Path="//Asset[Values/Item/Amount > 0]">|</ModOp>',
        )

    def test_and_nor_does_one_in_single_quotes(self):
        self.given("|")
        self.type("<ModOp Path='//A[B > 0]>")
        self.assertEqual(self.shown(), "<ModOp Path='//A[B > 0]>|")

    def test_nor_one_that_is_no_opening_tag(self):
        for typed in (
            "<Empty/>",       # closes itself
            "<Empty />",
            "<?xml version=\"1.0\"?>",
            "<!-- a comment -->",
            "<!-- <ModOp -->",   # in a comment, which ends there
            "<A></A>",           # the end tag's own ``>``
            "a > b",             # in text
        ):
            with self.subTest(typed=typed):
                self.given("|")
                self.type(typed)
                if typed == "<A></A>":
                    # The first ``>`` closed the tag, and the ``</`` typed
                    # after it was finished too; the last ``>`` added none.
                    continue
                self.assertEqual(self.shown(), typed + CARET)

    def test_nor_in_a_comment_or_cdata(self):
        for open_ in ("<!-- ", "<![CDATA["):
            with self.subTest(open_=open_):
                self.given(open_ + "|")
                self.type("<A>")
                self.assertEqual(self.shown(), open_ + "<A>|")

    def test_nor_typed_into_a_tag_that_goes_on(self):
        """``>`` typed in the middle of a tag is the one being fixed, not a
        new one."""
        self.given('<A| Type="add"></A>')
        self.type(">")
        self.assertEqual(self.shown(), '<A>| Type="add"></A>')

    def test_lt_slash_finishes_the_innermost_element_left_open(self):
        self.given('<ModOps>\n  <ModOp Type="add" Path="/A[B > 0]">\n    <B/><C>1</C>|')
        self.type("</")
        self.assertEqual(
            self.shown(),
            '<ModOps>\n  <ModOp Type="add" Path="/A[B > 0]">\n    <B/><C>1</C></ModOp>|',
        )
        self.type("\n</")
        self.assertTrue(self.shown().endswith("</ModOp>\n</ModOps>|"))

    def test_and_on_a_line_of_its_own_takes_that_elements_indent(self):
        self.given("<ModOps>\n  <ModOp>\n    <A>1</A>\n    |")
        self.type("</")
        self.assertEqual(self.shown(), "<ModOps>\n  <ModOp>\n    <A>1</A>\n  </ModOp>|")

    def test_and_with_nothing_left_open_is_only_a_slash(self):
        for text in ("<A></A>|", "|", "<!-- <A> |", '<A Path="|'):
            with self.subTest(text=text):
                self.given(text)
                self.type("</")
                self.assertEqual(self.shown(), text.replace(CARET, "</" + CARET))

    def test_an_end_tag_with_no_element_by_its_name_closes_nothing(self):
        self.given("<A><B></C>|")
        self.type("</")
        self.assertEqual(self.shown(), "<A><B></C></B>|")

    def test_each_is_one_step_of_undo_with_what_was_typed(self):
        self.given("<ModOps>\n  <A|\n</ModOps>")
        self.type(">")
        self.pane.undo()
        self.assertEqual(self.shown(), "<ModOps>\n  <A|\n</ModOps>")
        self.given("<ModOps>\n  <A>\n      <|")
        self.type("/")
        self.assertEqual(self.shown(), "<ModOps>\n  <A>\n  </A>|")
        self.pane.undo()
        self.assertEqual(self.pane.toPlainText(), "<ModOps>\n  <A>\n      <")

    def test_a_selection_is_typed_over_as_it_was(self):
        self.given("<A|")
        cursor = self.pane.textCursor()
        cursor.setPosition(0, QTextCursor.MoveMode.KeepAnchor)
        self.pane.setTextCursor(cursor)
        self.type(">")
        self.assertEqual(self.pane.toPlainText(), ">")

    def test_ctrl_with_them_is_no_typing(self):
        self.given("<A|")
        self.key(Qt.Key.Key_Greater, ">", Qt.KeyboardModifier.ControlModifier)
        self.assertNotIn("</A>", self.pane.toPlainText())

    def test_a_pane_that_is_only_read_takes_none_of_it(self):
        self.pane.set_editable(False)
        self.given("<A|")
        self.type(">\n</")
        self.assertEqual(self.pane.toPlainText(), "<A")


class TestIndentingLines(TypingCase):
    def test_enter_after_an_opening_tag_goes_a_level_deeper(self):
        self.given("  <ModOp>|")
        self.type("\n")
        self.assertEqual(self.shown(), "  <ModOp>\n    |")

    def test_as_deep_as_the_line_the_tag_starts_on(self):
        self.given('  <ModOp Type="add"\n         GUID="1">|')
        self.type("\n")
        self.assertEqual(self.shown(), '  <ModOp Type="add"\n         GUID="1">\n    |')

    def test_enter_between_a_tag_and_its_end_gives_it_a_line_inside(self):
        self.given("  <ModOp>|</ModOp>")
        self.type("\n")
        self.assertEqual(self.shown(), "  <ModOp>\n    |\n  </ModOp>")
        self.pane.undo()
        self.assertEqual(self.pane.toPlainText(), "  <ModOp></ModOp>")

    def test_and_so_after_the_tag_is_closed_as_it_was_typed(self):
        """What is typed is laid out as it goes, end tags and all."""
        self.given("<ModOps>\n  |\n</ModOps>")
        self.type("<ModOp>\n<A>1")
        self.assertEqual(
            self.shown(), "<ModOps>\n  <ModOp>\n    <A>1|</A>\n  </ModOp>\n</ModOps>"
        )

    def test_enter_after_anything_else_keeps_the_lines_indent(self):
        for text in (
            "    <Empty/>|", "    <A>1</A>|", "    </A>|", "    text|", "    <!-- x -->|",
            '    <A Path="a>|',  # the ``>`` is in a quoted value
        ):
            with self.subTest(text=text):
                self.given(text)
                self.type("\n")
                self.assertEqual(self.shown(), text.replace(CARET, "\n    " + CARET))

    def test_tab_still_indents_and_enter_with_a_selection_types_over_it(self):
        self.given("<A>|")
        self.key(Qt.Key.Key_Tab)
        self.assertEqual(self.shown(), "<A>  |")
        self.given("  <A>|")
        cursor = self.pane.textCursor()
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        self.pane.setTextCursor(cursor)
        self.type("\n")
        self.assertEqual(self.shown(), "  \n  |")


if __name__ == "__main__":
    unittest.main()
