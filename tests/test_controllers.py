"""The controllers cut out of the main window (Stage 3.3) ask it for things
through its public names, and each is one the window holds."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UI = ROOT / "anno_modding_studio" / "ui"
# module -> the name the window holds it under
CONTROLLERS = {
    "preferences": "preferences",
    "modio_controller": "modio",
    "loader_controller": "loader_check",
    "library_controller": "library_controller",
    "live_controller": "live",
    "loadout_files": "files",
    "game_files_controller": "game_files",
    "patches_controller": "patches",
    "start_controller": "start",
    "patterns_controller": "patterns",
}


class TestTheControllers(unittest.TestCase):
    def test_none_reaches_a_private_name_of_the_window(self):
        """What a controller needs of the window is the window's interface,
        and an interface is public: a private name reached from outside is
        one nobody can rename without reading every controller."""
        for module in CONTROLLERS:
            with self.subTest(module):
                source = (UI / (module + ".py")).read_text(encoding="utf-8")
                self.assertEqual(re.findall(r"self\.window\._\w+", source), [])

    def test_the_window_holds_each_one(self):
        source = (UI / "main_window.py").read_text(encoding="utf-8")
        for module, attr in CONTROLLERS.items():
            with self.subTest(module):
                self.assertRegex(source, r"self\.%s = \w+\(self\)" % attr)


class TestOneWayEach(unittest.TestCase):
    """Stage 3.5: what one method does is done only through it."""

    def sources(self):
        return {path.name: path.read_text(encoding="utf-8") for path in UI.glob("*.py")}

    def test_only_the_gate_opens_a_tab(self):
        """Going to an asset used to switch its tab on, past the gate (P4-2)."""
        for name, source in self.sources().items():
            with self.subTest(name):
                enabling = re.findall(r"\.setTabEnabled\(", source)
                going = re.findall(r"\btabs\.setCurrentIndex\(", source)
                if name == "main_window.py":
                    # update_tab_gates, and its sending you back; open_tab;
                    # and _page_of, which swaps a tab's page for the one that
                    # says its panel is in a window of its own, and puts
                    # back whether the tab was open and which was in front.
                    self.assertEqual((len(enabling), len(going)), (2, 3))
                else:
                    self.assertEqual((enabling, going), ([], []))

    def test_only_loadout_files_marks_the_loadout_saved_or_not(self):
        for name, source in self.sources().items():
            with self.subTest(name):
                marks = re.findall(r"^\s*self(?:\.window)?\.dirty = .*$", source, re.M)
                if name == "loadout_files.py":
                    self.assertEqual(len(marks), 2)  # mark_saved, mark_unsaved
                elif name == "main_window.py":
                    self.assertEqual([m.strip() for m in marks], ["self.dirty = False"])  # declared
                else:
                    self.assertEqual(marks, [])

    def test_attributes_are_declared_not_looked_for(self):
        """An attribute first set by some later method, and read with a
        getattr default meanwhile, is one a reader cannot find the start of.
        What is left is asked during construction, by an event that can come
        before the widget it is about exists."""
        found = sorted(
            "%s: %s" % (name, hit)
            for name, source in self.sources().items()
            for hit in re.findall(r"(?:getattr|hasattr)\(self, \"(\w+)\"", source)
        )
        self.assertEqual(found, ["main_window.py: tabs", "source_panel.py: ways_step"])


if __name__ == "__main__":
    unittest.main()
