"""A window whose loadout has given way to another holds what a window just
made holds.

What the window and its controllers remember about a loadout has to go when
the loadout does. It was let go of in two lists, one for *New loadout* and
one for opening a loadout, and they were not the same: after *New loadout*
the loader's verdict on the loadout before was still there to be shown, with
what its unread ops were found to do and a reading waiting to be taken (audit
finding A8-18). Ten of the audit's findings were of this kind.

So each thing remembered is asked about, one at a time: set to something a
fresh window does not have, the loadout changed, and looked at again. It has
to be back as a fresh window has it, or be in ``STAYS`` with why. An
attribute added later is asked about the day it is added.

**What is asked about.** The window's own attributes and those of its
controllers, where a fresh window holds plain data in them: nothing, a
number, a word, an empty list. Widgets are left out. What a panel holds of
its own is not looked at here: set from outside, a panel's insides took Qt
down with them.

**Not asked about:** adopting game files again, which is the other change a
window lives through. A8-4 is of that kind, in a panel.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from anno_modding_studio.core.changes import Loadout  # noqa: E402
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402

PLAIN = (type(None), bool, int, float, str)


def holders(window):
    """The window, and each controller of it that is not a widget."""
    yield "window", window
    for name, value in sorted(vars(window).items()):
        module = type(value).__module__ or ""
        if module.startswith("anno_modding_studio.ui") and hasattr(value, "__dict__") \
                and not isinstance(value, QWidget):
            yield name, value


def plain(value) -> bool:
    if isinstance(value, PLAIN):
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(plain(item) for item in value)
    if isinstance(value, dict):
        return all(plain(key) and plain(item) for key, item in value.items())
    return False


def held(window) -> dict:
    """``{(holder, attribute): value}`` for everything plain a window holds."""
    return {
        (holder, name): value
        for holder, owner in holders(window)
        for name, value in vars(owner).items()
        if plain(value)
    }


def something_else(value):
    """A value a fresh window does not have there, of the kind it has; None
    where there is no telling what kind that is."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 7
    if isinstance(value, str):
        return value + " left"
    if value is None:
        return ("left",)
    if isinstance(value, dict) and not value:
        return {"left": "left"}
    if isinstance(value, list) and not value:
        return ["left"]
    if isinstance(value, set) and not value:
        return {"left"}
    return None


class TestAWindowWhoseLoadoutHasGone(unittest.TestCase):
    # What stays when the loadout changes, and why. Anything else has to be
    # back as a window just made has it.
    STAYS: ClassVar[dict] = {
        # The game's files are not the loadout's: they stay loaded.
        ("window", "index"): "the game's files",
        ("window", "workspace"): "made over the game's files",
        ("window", "game_folder"): "where the game is",
        ("window", "game_open"): "whether the game is open, which greys Play",
        ("window", "_elsewhere"): "Play's menu, kept by the loadout it was worked out "
                                  "for (_elsewhere_key) and worked out again for another",
        ("window", "_asked_who_you_are"): "who you are is asked once a session, not once "
                                          "a loadout: it is you, not the loadout",
        ("game_files", "texts_path"): "the in-game names loaded",
        ("game_files", "texts_were_automatic"): "how the in-game names were loaded",
        ("game_files", "status_base"): "the status bar's line about the game's files",
        ("game_files", "_game_folder_before"): "a load of game files under way",
        ("game_files", "_reopen_on_failure"): "a load of game files under way",
        ("game_files", "_progress"): "a load of game files under way",
        ("game_files", "pending_archive"): "a load of game files under way",
        ("game_files", "pending_reset"): "a load of game files under way",
        ("game_files", "pending_schema"): "a load of game files under way",
        ("game_files", "pending_texts"): "a load of game files under way",
        ("game_files", "pending_loadout"): "the loadout waiting on that load: this one",
        # Work in the background, which is stopped or left to finish, not forgotten.
        ("game_files", "worker"): "a thread",
        ("game_files", "texts_worker"): "a thread",
        ("loader_check", "newest_worker"): "a thread, asking what is newest of the extension",
        ("loader_check", "probe_worker"): "a thread, checking the reading rules",
        ("loader_check", "_say_newest"): "that thread's answer, asked for from the Check menu",
        ("loader_check", "_say_probes"): "that thread's answer, asked for from the Check menu",
        ("loader_check", "play_workers"): "threads, Play's checks of the loader: a skipped "
                                          "one is asked to stop and let go when it ends",
        ("studio_release", "worker"): "a thread, asking GitHub whether a newer Studio is out",
        ("studio_release", "_say"): "that thread's answer, asked for from the Help menu",
        # About the Studio itself, whichever loadout is open.
        ("studio_release", "shown"): "the release the status bar's note of a newer Studio is about",
        ("readings", "worker"): "a thread",
        ("readings", "_holds"): "what has the index just now: about the game's files",
        ("readings", "_said_fell_back"): "said once a session, of where readings are done",
        ("window", "_elsewhere_worker"): "a thread, scanning the mods on this PC",
        ("window", "_elsewhere_again"): "whether that scan is to run again",
        ("window", "_announce_elsewhere"): "which import that scan is to speak of when it ends",
        ("background", "_running"): "what the status bar says is running",
        ("live", "log_timer"): "a timer, watching the game's log",
        ("library_controller", "_converting"): "a conversion under way",
        # About the window.
        ("window", "_changes_width"): "how wide the changes list was dragged",
        ("window", "_panels_restored"): "the panels in windows of their own were put out at launch",
        ("window", "_moving_graph"): "set only while the graph is being moved between windows",
        ("window", "_restoring_panels"): "set only while those panels are put out at launch",
        ("window", "_output_dirty"): "the Export tab is filled again by the change itself",
        ("patches", "in_its_file"): "set for the length of one call",
    }

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fresh = MainWindow()
        cls.fresh = held(fresh)
        fresh.deleteLater()
        _isolation.forget_settings()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.deleteLater)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def left_after(self, change, sets=()) -> list:
        """Each thing held that ``change`` leaves other than fresh, by name;
        ``sets`` are the ones it sets itself, of the loadout that has come."""
        owners = dict(holders(self.window))
        left = []
        for (holder, name), fresh in sorted(self.fresh.items()):
            new = something_else(fresh)
            if new is None or (holder, name) in self.STAYS or (holder, name) in sets:
                continue
            owner = owners[holder]
            setattr(owner, name, new)
            try:
                change()
                after = vars(owner).get(name)
                if not (plain(after) and after == fresh):
                    left.append("%s.%s" % (holder, name))
            finally:
                setattr(owner, name, fresh)
        return left

    def test_a_new_loadout_leaves_nothing_of_the_one_before(self):
        self.assertEqual(self.left_after(self.window.reset_loadout), [])

    def test_nor_does_opening_one(self):
        path = Path(self._tmp.name) / "Other.annoload"

        def opened() -> None:
            self.window.adopt_loadout(path, Loadout())

        self.assertEqual(self.left_after(opened, sets={
            ("window", "loadout_path"), ("window", "on_disk"), ("window", "look_for_missing_mods"),
        }), [])

    def test_what_stays_is_still_there_to_stay(self):
        """A reason kept for an attribute that has gone is a line nobody reads."""
        self.assertEqual(sorted(set(self.STAYS) - set(self.fresh)), [])

    def test_it_would_have_caught_the_verdict_left_standing(self):
        self.assertIsNotNone(something_else(self.fresh[("loader_check", "last_loadout_check")]))
        self.assertNotIn(("loader_check", "last_loadout_check"), self.STAYS)


if __name__ == "__main__":
    unittest.main()
