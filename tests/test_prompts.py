"""The window's questions live on ``ui/prompts.py``; this keeps them there."""

from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.core.games import ANNO_1800  # noqa: E402
from anno_modding_studio.ui import prompts  # noqa: E402
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestTheQuestionsAreOnThePrompts(unittest.TestCase):
    def test_the_window_asks_nothing_itself(self):
        """A question on the window is one a test can only answer by patching
        the window, which is what kept the window from being cut up."""
        named = sorted(
            name for name, _ in inspect.getmembers(MainWindow, inspect.isfunction)
            if re.match(r"_?(ask|confirm|report)_", name)
        )
        self.assertEqual(named, [], "a new question belongs on ui.prompts.Prompts")

    def test_every_question_shows_over_the_window_and_changes_nothing_else(self):
        source = inspect.getsource(prompts)
        self.assertNotRegex(source, r"self\.parent\.\w", "a prompt reads or changes the window")


def answered(ask, button=prompts.QMessageBox.StandardButton.Cancel):
    """``ask(prompts)`` answered with the standard ``button``: what it
    returned, and the box it asked in."""
    shown = []

    def answer(_self, box):
        shown.append(box)
        return box.button(button)

    with mock.patch.object(prompts.Prompts, "answer", answer):
        said = ask(prompts.Prompts(None))
    return said, shown[0]


class TestLeavingWhatWasTyped(unittest.TestCase):
    """The question asked before something typed in the XML view is left."""

    def asked(self, typed_asset: bool):
        """What the box is asked with: its title, text and default button."""
        _said, box = answered(lambda asked: asked.ask_unapplied_modops("Lumberjack's Hut",
                                                                       typed_asset))
        return (box.windowTitle(), _isolation.box_text(box),
                box.standardButton(box.defaultButton()))

    def test_it_says_which_of_the_two_panes_was_typed_in(self):
        """It said "The ModOps you typed" whichever it was, and the view has
        a pane for the asset beside the one for its ModOps."""
        title, text, _default = self.asked(typed_asset=False)
        self.assertEqual(title, "ModOps not applied")
        self.assertIn("The ModOps you typed for Lumberjack's Hut", text)
        title, text, _default = self.asked(typed_asset=True)
        self.assertEqual(title, "Asset not applied")
        self.assertIn("What you typed in the asset Lumberjack's Hut", text)
        self.assertNotIn("ModOps", text)

    def test_enter_applies_and_does_not_discard(self):
        """With no default named, Qt gave it to the first button in the row,
        which was Discard: the one answer that loses what was typed."""
        for typed_asset in (False, True):
            _title, _text, default = self.asked(typed_asset)
            self.assertEqual(default, prompts.QMessageBox.StandardButton.Apply)

    def test_each_answer_comes_back_as_its_word(self):
        box = prompts.QMessageBox
        for button, word in (
            (box.StandardButton.Apply, "apply"),
            (box.StandardButton.Discard, "discard"),
            (box.StandardButton.Cancel, ""),
        ):
            said, _box = answered(lambda asked: asked.ask_unapplied_modops("An asset", True),
                                  button)
            self.assertEqual(said, word)


class TestLeavingUnsavedWork(unittest.TestCase):
    """The question asked before a loadout with unsaved work is left."""

    def asked(self, *counts) -> str:
        _said, box = answered(lambda asked: asked.ask_save_first(*counts))
        return _isolation.box_text(box)

    def test_it_says_what_is_not_saved(self):
        """With nothing made here it said the mods switched on or the details
        had changed, whatever it was: a revert of changes read from a mod's
        folder is neither."""
        self.assertIn("2 changes you have made are not saved.", self.asked(2, 3))
        self.assertIn(
            "3 changes and ModOps have been reverted or switched since this "
            "loadout was saved.", self.asked(0, 3),
        )
        self.assertIn("1 change or ModOp has been reverted or switched", self.asked(0, 1))
        self.assertIn("Which mods this loadout has on, or its details", self.asked(0, 0, True))

    def test_it_says_both_where_changes_were_made_and_others_reverted(self):
        """With changes made here it named those alone, and a revert of what
        the loadout held when it was saved went unmentioned."""
        self.assertEqual(
            self.asked(2, 3),
            "2 changes you have made are not saved. 3 changes and ModOps have "
            "been reverted or switched since this loadout was saved.\n\n"
            "Save before continuing?",
        )
        self.assertEqual(
            self.asked(1, 1),
            "1 change you have made is not saved. 1 change or ModOp has been "
            "reverted or switched since this loadout was saved.\n\n"
            "Save before continuing?",
        )

    def test_a_note_on_what_was_read_in_is_said_as_a_note(self):
        """It was counted with what had been reverted or switched."""
        one = "The note on 1 change or ModOp has changed since this loadout was saved."
        two = "The notes on 2 changes and ModOps have changed since this loadout was saved."
        made = "1 change you have made is not saved."
        taken = "1 change or ModOp has been reverted or switched since this loadout was saved."
        details = "A mod's details have changed since this loadout was saved."
        for counts, said in (
            # (changes, taken, which mods are on, a mod's details, notes)
            ((0, 0, False, False, 1), one),
            ((0, 0, False, False, 2), two),
            ((1, 0, False, False, 2), made + " " + two),
            ((0, 1, False, False, 1), taken + " " + one),
            ((1, 1, False, True, 1), made + " " + taken + " " + one + " " + details),
            # It is a count like the others: which mods are on goes unsaid
            # beside it.
            ((0, 0, True, False, 1), one),
            ((0, 0, True, True, 1), one + " " + details),
        ):
            self.assertEqual(
                self.asked(*counts), said + "\n\nSave before continuing?", counts
            )

    def test_and_that_its_details_changed_only_where_they_did(self):
        """With no change counted it said the mods switched on or the details
        had changed, whether they had or not."""
        # Beside a count they go unsaid, as they did.
        self.assertEqual(
            self.asked(1, 0, True), "1 change you have made is not saved.\n\nSave before continuing?"
        )
        self.assertEqual(
            self.asked(0, 0, True),
            "Which mods this loadout has on, or its details, have changed "
            "since it was saved.\n\nSave before continuing?",
        )
        # Marked unsaved for something that is none of the three: it says
        # no more than it knows.
        for counts in ((0,), (0, 0), (0, 0, False)):
            self.assertEqual(
                self.asked(*counts),
                "This loadout has been worked on since it was saved.\n\n"
                "Save before continuing?",
            )

    def test_but_a_mods_details_are_named_beside_whatever_is_counted(self):
        """With a change made and a description typed it counted the change
        and said nothing of the description, which Discard lost with it."""
        details = "A mod's details have changed since this loadout was saved."
        made = "2 changes you have made are not saved."
        taken = (
            "3 changes and ModOps have been reverted or switched since this "
            "loadout was saved."
        )
        switches = (
            "Which mods this loadout has on, or its details, have changed "
            "since it was saved."
        )
        for counts, said in (
            # (changes, taken, which mods are on, a mod's details)
            ((0, 0, False, True), details),
            ((2, 0, False, True), made + " " + details),
            ((0, 3, False, True), taken + " " + details),
            ((2, 3, False, True), made + " " + taken + " " + details),
            # Which mods are on is said where nothing is counted, and its
            # sentence has the details in it.
            ((0, 0, True, True), switches),
            ((0, 0, True, False), switches),
            ((2, 0, True, True), made + " " + details),
            ((2, 3, True, True), made + " " + taken + " " + details),
            ((2, 0, True, False), made),
            ((0, 3, True, False), taken),
            ((2, 3, False, False), made + " " + taken),
        ):
            self.assertEqual(
                self.asked(*counts), said + "\n\nSave before continuing?", counts
            )

    def test_and_only_the_one_that_is_so_where_one_is(self):
        self.assertEqual(
            self.asked(2), "2 changes you have made are not saved.\n\nSave before continuing?"
        )
        self.assertEqual(
            self.asked(0, 1),
            "1 change or ModOp has been reverted or switched since this loadout "
            "was saved.\n\nSave before continuing?",
        )
        self.assertEqual(
            self.asked(0, 0, True),
            "Which mods this loadout has on, or its details, have changed since "
            "it was saved.\n\nSave before continuing?",
        )


class TestTheGameIsRunning(unittest.TestCase):
    """One guard before anything that moves the mods a running game read."""

    def test_it_says_so_only_when_it_is(self):
        asked = prompts.Prompts(None)
        with mock.patch("anno_modding_studio.core.launch.running", return_value=False), \
                mock.patch.object(prompts.QMessageBox, "information") as told:
            self.assertFalse(asked.game_running(ANNO_1800, "Close it."))
        told.assert_not_called()
        with mock.patch("anno_modding_studio.core.launch.running", return_value=True), \
                mock.patch.object(prompts.QMessageBox, "information") as told:
            self.assertTrue(asked.game_running(ANNO_1800, "Close it."))
        self.assertEqual(told.call_args[0][1:], ("Anno 1800 is running", "Close it."))

    def test_nothing_else_asks_windows_before_moving_mods(self):
        """The four copies of the guard are one; a fifth would not be."""
        root = Path(prompts.__file__).resolve().parent
        asking = sorted(
            path.name for path in root.glob("*.py")
            if 'tr("%s is running"' in path.read_text(encoding="utf-8")
        )
        self.assertEqual(asking, ["prompts.py"])


if __name__ == "__main__":
    unittest.main()
