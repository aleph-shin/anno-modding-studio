"""What the game will load, beside what the loadout says -- against temporary
folders standing in for the mods folder, the one under Documents, and mod.io's
download cache."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    IMYA_RADIUS,
    IMYA_TWEAKABLE,
    InstalledCollection,
    PlayingWindow,
    TruthFolders,
    TruthWindow,
    install_mod,
)

from anno_modding_studio.core import (  # noqa: E402
    disk,
    installplan,
    switches,
    truth,
)
from anno_modding_studio.core.changes import Loadout, ModLayer, Op  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402
from anno_modding_studio.ui.prompts import FORK_SEPARATE  # noqa: E402


class TestAnno1800(TruthFolders):
    def test_the_sentence_counts_what_loads_and_what_the_loadout_has(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        install_mod(self.documents, "[Misc] Elsewhere", "elsewhere", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        self.assertEqual(sorted(mod.name for mod in found.loading),
                         ["[Gameplay] Mine", "[Gameplay] Stray", "[Misc] Elsewhere"])
        self.assertEqual([mod.name for mod in found.in_loadout], ["[Gameplay] Mine"])
        self.assertEqual(sorted(mod.name for mod in found.others), ["[Gameplay] Stray", "[Misc] Elsewhere"])
        self.assertEqual([mod.name for mod in found.none_loads], ["[Gameplay] Stray", "[Misc] Elsewhere"])
        self.assertEqual(found.off_loads, [])
        self.assertEqual(found.differ, 2)
        self.assertEqual(
            found.describe(),
            "Play would make this loadout what the game loads: 2 mods that are not in "
            "it would be switched off.",
        )
        self.assertFalse(found.matches)

    def test_a_wrapper_folder_is_answered_for_by_the_mod_inside_it(self):
        """A host holding its modinfo and, one level down, the mod that does
        the work -- [Seal] Vanilla Riverslot Buildings NW on a real folder.
        The folder read brings in the inner mod alone; the host is what the
        game lists, and it was reported as a mod the loadout never mentioned."""
        host = install_mod(self.mods, "[Seal] Riverslot NW", "riverslot_nw", "1")
        inner = install_mod(host, "[Gameplay] Riverslot NW (Taludas)", "riverslot_nw", "1")
        (inner / "data" / "assets.xml").write_text("<ModOps/>", encoding="utf-8")
        found = self.read(ModLayer(origin="[Seal] Riverslot NW/[Gameplay] Riverslot NW (Taludas)",
                                   mod_id="riverslot_nw"))
        self.assertEqual(found.others, [])
        self.assertTrue(found.matches, found.describe())
        # And switched off in the loadout, the host is what that means.
        found = self.read(ModLayer(origin="[Seal] Riverslot NW/[Gameplay] Riverslot NW (Taludas)",
                                   mod_id="riverslot_nw", active=False))
        self.assertEqual([mod.installed.folder_name for mod in found.off_loads], ["[Seal] Riverslot NW"])

    def test_of_two_copies_the_same_version_the_last_the_loader_comes_to_loads(self):
        """The loader's AddMod keeps a copy unless the one it holds is newer,
        so between equals the last one it walks to is the one that loads; the
        truth used to name the first."""
        install_mod(self.mods, "[Gameplay] A Copy", "shared", "1.2")
        install_mod(self.mods, "[Gameplay] B Copy", "shared", "1.2")
        found = self.read()
        self.assertEqual([mod.installed.folder_name for mod in found.mods if mod.loads], ["[Gameplay] B Copy"])

    def test_and_the_cache_is_walked_first_so_the_mods_folder_s_copy_loads(self):
        install_mod(self.mods, "[Gameplay] Shared", "shared", "1.2")
        self.cached(3229845, "[Gameplay] Shared", "shared", "1.2")
        found = self.read()
        self.assertEqual([mod.installed.in_cache for mod in found.mods if mod.loads], [False])

    def test_everything_accounted_for_is_said_in_one_line(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        self.assertEqual(found.describe(),
                         "Matches this loadout: the game will load 1 mod and nothing else.")
        self.assertTrue(found.matches)
        self.assertEqual(self.read().describe(),
                         "Play would make this loadout what the game loads: 1 mod that is "
                         "not in it would be switched off.")

    def test_a_mod_the_loadout_has_off_but_the_disk_has_on_is_an_other(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine", active=False))
        self.assertEqual([mod.name for mod in found.others], ["[Gameplay] Mine"])
        self.assertEqual([mod.name for mod in found.off_loads], ["[Gameplay] Mine"])
        self.assertEqual(found.describe(), "Play would make this loadout what the game loads: "
                         "1 mod it has off would be switched off.")

    def test_and_the_reverse_is_said_too(self):
        install_mod(self.mods, "-[Gameplay] Mine", "mine", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine", active=True))
        self.assertEqual([mod.name for mod in found.wanted_but_off], ["[Gameplay] Mine"])
        self.assertEqual(found.describe(), "Play would make this loadout what the game loads: "
                         "1 mod it has on would be switched on.")

    def test_a_mod_of_the_loadout_not_in_the_mods_folder_is_counted_on_not_loaded(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine", name="Mine"))
        self.assertEqual(found.unbuilt_on, ["Mine"])
        self.assertEqual(found.unbuilt_off, [])
        # Play builds it rather than switching it, so no other count moves.
        self.assertEqual(found.wanted_but_off, [])
        self.assertEqual(self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine", name="Mine",
                                            active=False)).unbuilt_off, ["Mine"])

    def test_the_diagonal_is_counted_too_and_once_per_modid(self):
        install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        install_mod(self.documents, "-[Gameplay] Off", "off", "2")
        install_mod(self.mods, "-[Gameplay] Nobody", "nobody", "1")
        found = self.read(ModLayer(origin="[Gameplay] Off", mod_id="off", active=False))
        self.assertEqual([mod.name for mod in found.off_off], ["[Gameplay] Off"])
        self.assertEqual([mod.name for mod in found.none_off], ["[Gameplay] Nobody"])
        self.assertTrue(found.matches)
        self.assertEqual(found.describe(), "Matches this loadout: the game will load no mods and nothing else.")

    def test_one_copy_per_modid_loads_and_the_loadout_is_matched_by_modid(self):
        self.cached(3229975, "[Building] Windmill", "windmill", "2.0")
        install_mod(self.mods, "[Seal] Windmill", "windmill", "2.0.1")
        found = self.read(ModLayer(origin="3229975/[Building] Windmill", mod_id="windmill"))
        self.assertEqual(len(found.loading), 1)
        self.assertEqual(found.loading[0].installed.folder_name, "[Seal] Windmill")
        self.assertTrue(found.matches)

    def test_the_browsers_own_mods_are_said_to_be_its_to_switch(self):
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.read()
        self.assertEqual([mod.name for mod in found.unswitchable_others], ["[Gameplay] Theirs"])
        self.assertEqual([mod.name for mod in found.switchable_others], ["[Gameplay] Stray"])
        self.assertEqual(found.describe_cache(),
                         "The game's own mod browser installed 1 of the mods that load, outside this "
                         "loadout. The Studio cannot switch those until they are copied "
                         "into the library and unsubscribed from.")
        # And the sentence does not say Play would switch them off: it cannot.
        self.assertEqual(found.describe(),
                         "Play would make this loadout what the game loads: 1 mod that is "
                         "not in it would be switched off; 1 mod the game's own mod browser "
                         "installed would stay on.")

    def test_the_sentence_says_which_way_play_would_switch_each_mod(self):
        """It read "Playing this loadout changes 68: 68 it does not mention
        will load": what loads now, in words that read as what Play brings
        about, and not which way the 68 would go."""
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        install_mod(self.mods, "[Gameplay] Other", "other", "1")
        install_mod(self.mods, "[Gameplay] Unwanted", "unwanted", "1")
        install_mod(self.mods, "-[Gameplay] Wanted", "wanted", "1")
        found = self.read(
            ModLayer(origin="[Gameplay] Unwanted", mod_id="unwanted", active=False),
            ModLayer(origin="[Gameplay] Wanted", mod_id="wanted"),
        )
        self.assertEqual(
            found.describe(),
            "Play would make this loadout what the game loads: 1 mod it has on would be "
            "switched on; 1 mod it has off would be switched off; 2 mods that are not in "
            "it would be switched off.",
        )

    def test_and_in_german_it_does_not_start_with_a_small_letter(self):
        """It began with what stands in for the loadout's name, "dieses
        Loadout", which is written small."""
        from anno_modding_studio.core import i18n

        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.assertTrue(i18n.install("de"))
        self.addCleanup(i18n.install, i18n.SOURCE)
        said = self.read().describe()
        self.assertTrue(said.startswith("„Spielen“ würde dieses Loadout"), said)
        self.assertIn("1 Mod, die nicht dazugehört, würde ausgeschaltet", said)

    def test_a_bundled_mod_answers_to_its_host(self):
        install_mod(self.mods, "[Gameplay] Host", "host", "1")
        install_mod(self.mods / "[Gameplay] Host" / "shared", "Piece", "piece", "1")
        found = self.read(ModLayer(origin="[Gameplay] Host", mod_id="host"),
                          ModLayer(origin="[Gameplay] Host/shared/Piece", mod_id="piece"))
        self.assertEqual([mod.name for mod in found.loading], ["[Gameplay] Host"])
        self.assertTrue(found.matches)

    def test_switching_the_strays_off_renames_and_leaves_the_cache_alone(self):
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        install_mod(self.documents, "[Misc] Elsewhere", "elsewhere", "1")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        written = truth.switch_off(found)
        self.assertEqual(written.failed, [])
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["-[Gameplay] Stray", "[Gameplay] Mine"])
        self.assertEqual([p.name for p in self.documents.iterdir()], ["-[Misc] Elsewhere"])
        self.assertTrue((self.cache_root / "4169" / "mods" / "1" / "[Gameplay] Theirs").is_dir())
        again = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        self.assertEqual([mod.name for mod in again.others], ["[Gameplay] Theirs"])


class TestAnno117(TruthFolders):
    game = ANNO_117

    def profile(self, *lines: str) -> None:
        (self.mods / "profiles").mkdir(exist_ok=True)
        (self.mods / "profiles" / "default.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_the_profile_decides_and_reaches_into_the_cache(self):
        install_mod(self.mods, "Mine", "mine", "1")
        install_mod(self.mods, "Stray", "stray", "1")
        self.cached(5, "Theirs", "theirs", "1")
        self.profile("enablenewmods", "mine", "# theirs")
        found = self.read(ModLayer(origin="Mine", mod_id="mine"))
        self.assertEqual(found.profile, self.mods / "profiles" / "default.txt")
        self.assertEqual(sorted(mod.name for mod in found.loading), ["Mine", "Stray"])
        # Governed by the profile, so not the browser's to switch.
        self.assertEqual(found.unswitchable_others, [])
        self.assertEqual(found.describe_cache(), "")

    def test_a_frozen_profile_switches_unlisted_mods_off(self):
        install_mod(self.mods, "Mine", "mine", "1")
        install_mod(self.mods, "Stray", "stray", "1")
        self.profile("# enablenewmods", "mine")
        found = self.read(ModLayer(origin="Mine", mod_id="mine"))
        self.assertEqual([mod.name for mod in found.loading], ["Mine"])
        self.assertTrue(found.matches)

    def test_switching_off_writes_the_profile(self):
        install_mod(self.mods, "Mine", "mine", "1")
        install_mod(self.mods, "Stray", "stray", "1")
        self.cached(5, "Theirs", "theirs", "1")
        self.profile("enablenewmods", "mine")
        found = self.read(ModLayer(origin="Mine", mod_id="mine"))
        written = truth.switch_off(found)
        self.assertEqual(written.failed, [])
        text = (self.mods / "profiles" / "default.txt").read_text(encoding="utf-8")
        self.assertEqual(text, "enablenewmods\nmine\n# stray\n# theirs\n")
        self.assertTrue(self.read(ModLayer(origin="Mine", mod_id="mine")).matches)


if __name__ == "__main__":
    unittest.main()


class TestOnTheStartTab(TruthWindow):
    """The card and the window: shown after the moments that move the answer,
    and its buttons doing what they say."""

    def test_nothing_is_shown_until_there_is_a_loadout_to_compare(self):
        self.window.refresh_truth()
        self.assertIsNone(self.window.truth)
        self.assertTrue(self.window.source.truth_step.isHidden())

    def test_the_card_says_the_sentence_and_offers_both_ways_to_agree(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        card = self.window.source
        self.assertEqual(card.truth_cells["none", "loads"].text(), "1")
        self.assertEqual(card.truth_cells["on", "loads"].text(), "1")
        self.assertEqual(card.truth_cells["on", "off"].text(), "0")
        self.assertEqual(card.truth_cells["none", "loads"].toolTip(), "[Gameplay] Stray")
        self.assertEqual(card.truth_step.status.full_text(), "Play changes 1")
        # The mod the loadout does not mention has a row that names it, and
        # the button that reads it in.
        self.assertFalse(card.strangers_row.isHidden())
        self.assertEqual(card.strangers_said.text(),
                         "<b>1 mod in the mods folder is not in this loadout: [Gameplay] Stray</b>")
        self.assertEqual(card.strangers_button.text(), "Add these mods…")
        self.assertTrue(card.bring_in_button.isHidden())
        self.assertFalse(card.make_live_button.isHidden())
        self.assertTrue(card.truth_cache.isHidden())
        # One row for the conversion as a whole. A place's own row is for
        # when two places hold mods, and here one does.
        self.assertFalse(card.convert_row.isHidden())
        self.assertEqual(card.convert_said.text(), "<b>2 of 2 mods are outside the library: [Gameplay] Mine, [Gameplay] Stray</b>")
        self.assertIn(str(self.root / "library"), card.convert_where.text().replace("\u200b", ""))
        self.assertEqual(card.convert_button.text(), "Convert to library…")
        self.assertTrue(card.checklist["yours"][0].isHidden())
        self.assertTrue(card.checklist["cache"][0].isHidden())
        self.assertIn("Play would make this loadout what the game loads: 1 mod",
                      self.window.status_label.text())

    def _held(self, name: str, mod_id: str, version: str = "2"):
        """A mod of the library's, linked into the mods folder."""
        from anno_modding_studio.core import library

        held = install_mod(self.root / "library", name, mod_id, version)
        library.note_link(self.root / "library", library.link(held, self.mods, name), library.COPY)

    def test_a_second_copy_of_a_library_mod_is_on_the_card_as_it_is_in_the_menu(self):
        """The rows were counted by where the copy the game loads is, so a
        library mod with a second copy in the mods folder was counted nowhere:
        the card said all was done while the menu still offered to convert."""
        self._held("[Gameplay] Held", "held")
        for second in ("[Gameplay] Held old", "-[Gameplay] Held old"):
            with self.subTest(second=second):
                spare = install_mod(self.mods, second, "held", "1")
                self.addCleanup(shutil.rmtree, spare, True)
                self.window.loadout.layers = [ModLayer(origin="[Gameplay] Held", mod_id="held")]
                self.window.refresh_truth()
                card = self.window.source
                self.assertEqual(len(self.window.library_controller.waiting()), 1)
                self.assertTrue(self.window.convert_action.isEnabled())
                self.assertFalse(card.convert_row.isHidden())
                self.assertTrue(card.checklist_done.isHidden())
                # The mod is the library's: it is its other copy the row is about.
                self.assertEqual(
                    card.convert_said.text(),
                    "<b>1 mod has a second copy in your folders that the game does not load: [Gameplay] Held</b>",
                )
                self.assertNotIn("links", card.convert_where.text())
                self.assertIn("sets the second copies aside", card.convert_where.text())
                self.assertNotIn("outside the library", card.truth_step.status.full_text())
                shutil.rmtree(spare)

    def test_and_beside_mods_outside_the_library_it_is_said_after_them(self):
        self._held("[Gameplay] Held", "held")
        install_mod(self.mods, "[Gameplay] Held old", "held", "1")
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Held", mod_id="held"),
            ModLayer(origin="[Gameplay] Mine", mod_id="mine"),
        ]
        self.window.refresh_truth()
        card = self.window.source
        self.assertEqual(
            card.convert_said.text(),
            "<b>1 of 2 mods is outside the library: [Gameplay] Mine<br>"
            "1 mod has a second copy in your folders that the game does not load: [Gameplay] Held</b>",
        )
        self.assertIn("links", card.convert_where.text())
        self.assertIn("1 outside the library", card.truth_step.status.full_text())

    def test_the_done_line_is_said_only_while_the_library_is_where_all_of_it_loads_from(self):
        """With nothing left to convert it said everything the game loads comes
        from the library, of a working copy with .git too, which is left where
        it is and loads from there."""
        self._held("[Gameplay] Held", "held")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Held", mod_id="held")]
        self.window.refresh_truth()
        card = self.window.source
        self.assertFalse(card.checklist_done.isHidden())
        mine = install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        (mine / ".git").mkdir()
        self.window.loadout.layers.append(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        self.window.refresh_truth()
        self.assertEqual(self.window.library_controller.waiting(), [])
        self.assertTrue(card.convert_row.isHidden())
        self.assertTrue(card.checklist_done.isHidden())

    def test_make_this_live_asks_first_and_then_renames(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.loadout_path = self.root / "Mine.annoload"
        self.window.refresh_truth()
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=False) as asked:
            self.window.source.make_live_button.click()
        asked.assert_called_once()
        self.assertTrue((self.mods / "[Gameplay] Stray").is_dir())
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.source.make_live_button.click()
        self.assertTrue((self.mods / "-[Gameplay] Stray").is_dir())
        # Both are folders of the user's own, and the header says so.
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 2 outside the library")
        self.assertTrue(self.window.truth.is_live)
        self.assertTrue(self.window.source.make_live_button.isHidden())
        self.window.files.mark_saved()

    def test_the_browsers_mods_leave_nothing_to_switch_but_are_said(self):
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        self.window.loadout.aim = "manage"
        self.window.refresh_truth()
        card = self.window.source
        self.assertFalse(card.truth_cache.isHidden())
        self.assertEqual(card.truth_step.status.full_text(), "1 the Studio cannot switch")
        self.assertFalse(card.strangers_row.isHidden())
        self.assertEqual(card.strangers_button.text(), "Add these mods…")

    def test_a_mod_put_into_the_folder_switched_off_is_named_and_read_in_as_it_is(self):
        """It was a 1 in the grid's last row and nothing else: no name, and
        no button, since the button was for mods that load."""
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "-[Gameplay] New", "new", "1")
        self.window.loadout.set_up_as("manage")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        card = self.window.source
        self.assertTrue(self.window.truth.matches)
        self.assertEqual(card.strangers_said.text(),
                         "<b>1 mod in the mods folder is not in this loadout: [Gameplay] New</b>")
        self.assertEqual(card.strangers_button.text(), "Add these mods…")
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            card.strangers_button.click()
        # With the loadout's own, whose switches are kept; the new one comes
        # in as the folder has it, so the loadout still matches the game.
        read.assert_called_once_with(
            focus={"[Gameplay] New", "[Gameplay] Mine"}, keep_switches=True, adding=[])
        # In the loadout, it is no row.
        self.window.loadout.layers.append(ModLayer(origin="[Gameplay] New", mod_id="new", active=False))
        self.window.refresh_truth()
        self.assertTrue(card.strangers_row.isHidden())

    def test_more_mods_than_a_row_can_name_are_counted(self):
        for number in range(4):
            install_mod(self.mods, "[Gameplay] Mod %d" % number, "mod-%d" % number, "1")
        self.window.loadout.set_up_as("manage")
        self.window.refresh_truth()
        card = self.window.source
        self.assertEqual(card.strangers_said.text(),
                         "<b>4 mods in the mods folder are not in this loadout</b>")
        self.assertEqual(card.convert_said.text(), "<b>4 of 4 mods are outside the library</b>")

    def test_a_new_loadout_forgets_the_answer(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.aim = "manage"
        self.window.refresh_truth()
        self.assertIsNotNone(self.window.truth)
        self.window.index = None
        self.window.reset_loadout()
        self.assertIsNone(self.window.truth)
        self.assertTrue(self.window.source.truth_step.isHidden())

    def test_and_the_status_bar_stops_saying_what_play_would_do_with_the_last_one(self):
        """The sentence stayed up after File > New loadout, over no loadout."""
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.aim = "manage"
        self.window.refresh_truth()
        self.assertEqual(self.window.status_label.text(), self.window.truth.describe())
        self.assertIn("Play would make", self.window.status_label.text())
        self.window.reset_loadout()
        self.assertNotIn("Play would", self.window.status_label.text())
        self.assertEqual(self.window.status_label.text(), self.window.game_files.status_base)


class TestALoadout(TruthFolders):
    """``loadout``: a set of mods to play, complete by definition."""

    def test_it_is_saved_and_is_a_typed_detail(self):
        loadout = self.loadout()
        self.assertFalse(loadout.own.loads_last)
        before = loadout.typed_details()
        loadout.own.loads_last = True
        self.assertTrue(loadout.details_moved(before))
        path = self.root / "p.annoload"
        loadout.save(path)
        self.assertTrue(Loadout.load(path).own.loads_last)

    def test_the_third_card_writes_it_and_the_others_do_not(self):
        loadout = self.loadout()
        loadout.set_up_as("manage")
        self.assertTrue(loadout.own.loads_last)
        loadout.set_up_as("make")
        self.assertFalse(loadout.own.loads_last)

    def test_the_sentence_says_what_making_it_live_changes(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        loadout = self.loadout()
        loadout.own.loads_last = True
        found = truth.read(loadout, self.mods)
        self.assertEqual(found.describe(),
                         "Play would make this loadout what the game loads: 1 mod that is "
                         "not in it would be switched off.")


class TestWhatIsLive(TruthFolders):
    """The card's header and its first line, for each of the ways a loadout
    can stand to what the game loads."""

    def found(self, *layers, own_mod_loads_last=True, live="", is_live=False):
        loadout = self.loadout(*layers)
        loadout.own.loads_last = own_mod_loads_last
        found = truth.read(loadout, self.mods)
        found.live, found.is_live = live, is_live
        return found

    def test_nothing_live_and_a_loadout_to_make_so(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.found()
        self.assertEqual(found.status(), ("Play changes 1", ""))
        self.assertEqual(found.describe_live(), (
            "Live: nothing the Studio made",
            "Play makes this one live, changing 1 mod. The game now loads a set "
            "the Studio did not make (1 mod).",
        ))
        self.assertFalse(found.drift)

    def test_another_loadout_is_live_and_this_is_a_preview_not_a_fault(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.found(ModLayer(origin="[Gameplay] Mine", mod_id="mine"), live="Main loadout")
        self.assertEqual(found.status(), ("Play changes 1", ""))
        self.assertEqual(found.describe_live(), (
            "Live: Main loadout",
            "Play makes this one live, changing 1 mod. The game loads Main loadout now (2 mods).",
        ))

    def test_the_live_loadout_matching(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        found = self.found(ModLayer(origin="[Gameplay] Mine", mod_id="mine"), live="Main", is_live=True)
        self.assertEqual(found.status(), ("Matches", "ok"))
        self.assertEqual(found.describe_live(),
                         ("Live: this loadout", "The game loads what it has on, and nothing else."))

    def test_the_live_loadout_drifting_is_the_one_warning(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.found(ModLayer(origin="[Gameplay] Mine", mod_id="mine"), live="Main", is_live=True)
        self.assertTrue(found.drift)
        self.assertEqual(found.status(), ("1 changed since it was made live", "todo"))
        head, detail = found.describe_live()
        self.assertEqual(head, "Live: this loadout")
        self.assertEqual(detail, "The game no longer loads what it has on: 1 mod differs. "
                                 "Play switches that mod to match.")
        self.assertEqual(found.describe(),
                         "1 changed since this loadout was made live: 1 it does not mention will load.")

    def test_what_the_studio_cannot_switch_is_not_drift(self):
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        found = self.found(live="Main", is_live=True)
        self.assertFalse(found.drift)
        self.assertEqual(found.status(), ("1 the Studio cannot switch", ""))

    def test_a_switch_made_here_is_not_said_to_be_made_outside_the_studio(self):
        """The live loadout's own switch, flipped in the Studio: the folder
        is as Play left it, and it is the loadout that moved."""
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Other", "other", "1")
        found = self.found(ModLayer(origin="[Gameplay] Mine", mod_id="mine", active=False),
                           ModLayer(origin="[Gameplay] Other", mod_id="other", active=False),
                           live="Main", is_live=True)
        self.assertTrue(found.drift)
        detail = found.describe_live()[1]
        self.assertNotIn("outside the Studio", detail)
        self.assertEqual(detail, "The game no longer loads what it has on: 2 mods differ. "
                                 "Play switches those mods to match.")

    def test_what_the_studio_cannot_switch_is_said_to_load_as_well(self):
        """The card's first line said the game loads what the loadout has on
        "and nothing else" over a header counting what the Studio cannot
        switch."""
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        found = self.found(live="Main", is_live=True)
        self.assertFalse(found.matches)
        self.assertEqual(found.describe_live(), (
            "Live: this loadout",
            "The game loads what it has on, and 1 mod the game's own mod browser installed.",
        ))
        found.is_live = False
        self.assertEqual(found.describe_live(), (
            "Live: Main",
            "Play makes this one live and changes nothing: the game loads Main now (1 mod), "
            "which is what it has on and 1 mod the game's own mod browser installed.",
        ))
        found.live = ""
        self.assertEqual(found.describe_live(), (
            "Live: nothing the Studio made",
            "Play makes this one live and changes nothing: the game loads a set the Studio "
            "did not make now (1 mod), which is what it has on and 1 mod the game's own mod "
            "browser installed.",
        ))
        self.cached(2, "[Gameplay] Also theirs", "also", "1")
        found = self.found(live="Main", is_live=True)
        self.assertEqual(found.describe_live()[1], "The game loads what it has on, and 2 mods "
                                                   "the game's own mod browser installed.")

    def test_and_drift_beside_it_does_not_say_play_makes_the_two_match(self):
        """A stray in the mods folder and a mod the game's own browser
        installed: two differ and Play switches one, and the line said "1 mod
        differs. Play switches that mod to match."."""
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        found = self.found(live="Main", is_live=True)
        self.assertTrue(found.drift)
        self.assertEqual((found.differ, found.changes), (2, 1))
        self.assertEqual(found.describe(),
                         "2 changed since this loadout was made live: 2 it does not mention will load.")
        detail = found.describe_live()[1]
        self.assertNotIn("to match", detail)
        self.assertNotIn("1 mod differs", detail)
        self.assertEqual(detail, "The game no longer loads what it has on: Play switches 1 mod that "
                                 "differs. 1 mod the game's own mod browser installed loads as well, "
                                 "and Play leaves it on.")
        install_mod(self.mods, "[Gameplay] Other stray", "other", "1")
        self.cached(2, "[Gameplay] Also theirs", "also", "1")
        found = self.found(live="Main", is_live=True)
        self.assertEqual(found.describe_live()[1],
                         "The game no longer loads what it has on: Play switches 2 mods that "
                         "differ. 2 mods the game's own mod browser installed load as well, and "
                         "Play leaves them on.")

    def test_any_loadout_is_complete_when_played(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        found = self.found(own_mod_loads_last=False, live="Main")
        self.assertEqual(found.status(), ("Play changes 1", ""))
        self.assertEqual(found.describe(), "Play would make this loadout what the game loads: "
                         "1 mod that is not in it would be switched off.")
        found.is_live = True
        self.assertEqual(found.describe_live()[0], "Live: this loadout")


class TestTheLivePointer(TruthFolders):
    def test_it_is_remembered_followed_and_compared_by_file(self):
        from anno_modding_studio.core import library

        held = self.root / "library"
        self.assertEqual(library.live(held), "")
        main = self.root / "Main.annoload"
        library.set_live(held, main)
        self.assertTrue(disk.same_file(library.live(held), main))
        self.assertTrue(disk.same_file(str(main).upper(), main) or os.name != "nt")
        library.follow_live(held, self.root / "Other.annoload", self.root / "x.annoload")
        self.assertTrue(disk.same_file(library.live(held), main))
        library.follow_live(held, main, self.root / "Renamed.annoload")
        self.assertTrue(disk.same_file(library.live(held), self.root / "Renamed.annoload"))
        self.assertFalse(disk.same_file("", main))


class TestMakingALoadoutLive(TruthWindow):
    def setUp(self):
        super().setUp()
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.own.loads_last = True
        self.window.loadout_path = self.root / "Main.annoload"

    def live(self) -> str:
        from anno_modding_studio.core import library

        return library.live(self.window.where.library_dir())

    def test_sync_switches_the_strays_off_too_after_listing_them(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "-[Gameplay] Wanted", "wanted", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.cached(1, "[Gameplay] Theirs", "theirs", "1")
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Mine", mod_id="mine", folder=str(self.mods / "[Gameplay] Mine")),
            ModLayer(origin="[Gameplay] Wanted", mod_id="wanted", folder=str(self.mods / "-[Gameplay] Wanted")),
        ]

        # The panel's own refresh asks the (mocked) index where it came from;
        # what is under test is the switch, and the card is refreshed by it.
        with _isolation.answering("Make it live") as asked, \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.live.sync_loadout()
        (text,) = asked.texts
        self.assertIn("Switch on:\n  • [Gameplay] Wanted", text)
        self.assertIn("does not have them on:\n  • [Gameplay] Stray", text)
        self.assertIn("mod browser installed", text)
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()),
                         ["-[Gameplay] Stray", "[Gameplay] Mine", "[Gameplay] Wanted"])
        self.assertIn("renamed 2 folders", self.window.status_label.text())
        # It is the live one now, and only the browser's mod is left over --
        # which is not drift, since nothing could switch it.
        self.assertTrue(self.window.truth.is_live)
        self.assertEqual(self.window.source.truth_step.status.full_text(), "1 the Studio cannot switch")
        self.assertTrue(self.window.loadout_path.is_file())
        self.window.files.mark_saved()

    def test_nothing_to_do_says_nothing_else_is_on_and_is_live(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        from PySide6.QtWidgets import QMessageBox

        said = []
        with mock.patch.object(QMessageBox, "information",
                               staticmethod(lambda _p, title, text, *a: said.append(text))):
            self.window.live.sync_loadout()
        self.assertEqual(len(said), 1)
        self.assertIn("and no others.", said[0])
        self.assertTrue(self.live())
        self.window.files.mark_saved()

    def test_with_nothing_to_change_the_folder_is_read_once(self):
        """Planning reads the folders; noting what is live and the card after
        it used to read them again, twice, though nothing had moved."""
        from anno_modding_studio.core import installplan

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        with mock.patch.object(installplan, "inventory", wraps=installplan.inventory) as read:
            self.assertTrue(self.window.live.make_live(confirm=False, quiet=True))
        self.assertEqual(read.call_count, 1)
        self.assertTrue(self.live())
        self.window.files.mark_saved()

    def test_a_mod_of_yours_given_a_new_modid_is_built_beside_the_old_one(self):
        """Carried on, renamed with its Mod ID field cleared: another mod to
        the game.  Play builds it into a folder of its own, links it, and
        switches the old one off, which the loadout no longer has."""
        self.addCleanup(self.window.files.mark_saved)
        old, new = "[Gameplay] Harbours", "[Gameplay] Harbours (Studio)"
        install_mod(self.mods, old, "harbours", "1", "Harbours")
        loadout = self.window.loadout
        loadout.own.loads_last = False
        loadout.layers = [ModLayer(
            origin=old, name="Harbours (Studio)", category="Gameplay", kept=True,
            folder=str(self.mods / old), declared={"ModID": "harbours", "ModName": {"English": "Harbours"}},
        )]
        loadout.home = loadout.editing = old
        # What it does, as it was read: no edit of yours, so only its new
        # ModID makes Play build it.
        loadout.ops = [Op(origin=old, order=0, uid="a",
                          source='<ModOp Type="merge" GUID="1" Path="/Values"><Cost>1</Cost></ModOp>')]
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True):
            self.assertTrue(self.window.live.sync_loadout())
        self.assertEqual(loadout.home, new)
        self.assertTrue((self.root / "library" / new / "modinfo.json").is_file())
        self.assertIn("harbours-studio", (self.root / "library" / new / "modinfo.json").read_text(
            encoding="utf-8").lower())
        self.assertTrue((self.mods / new).is_dir())
        # The old mod is as it was, and off.
        self.assertTrue((self.mods / ("-" + old) / "modinfo.json").is_file())

    def test_a_loadout_that_is_not_a_loadout_is_complete_when_played_too(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.own.loads_last = False
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True):
            self.assertTrue(self.window.live.sync_loadout())
        self.assertTrue((self.mods / "-[Gameplay] Stray").is_dir())
        self.assertTrue(self.live())
        self.window.files.mark_saved()

    def test_drift_on_the_live_loadout_is_orange_and_a_preview_is_not(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        self.assertEqual(self.window.source.truth_step.status.full_text(), "Play changes 1")
        # A preview, so not orange.
        self.assertFalse(self.window.source.truth_cells["none", "loads"].property("tone"))
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True):
            self.assertTrue(self.window.live.sync_loadout())
        self.assertTrue(self.window.truth.is_live)
        self.assertEqual(self.window.source.truth_live.text(),
                         "<b>Live: this loadout</b><br>The game loads what it has on, and nothing else.")
        # Something installed behind its back is drift, and that is orange.
        install_mod(self.mods, "[Gameplay] Later", "later", "1")
        self.window.refresh_truth()
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "1 changed since it was made live")
        self.assertEqual(self.window.source.truth_cells["none", "loads"].property("tone"), "warn")
        self.window.files.mark_saved()

    def test_a_live_loadout_whose_file_is_gone_is_nothing(self):
        from anno_modding_studio.core import library

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        library.set_live(self.window.where.library_dir(), self.root / "Gone.annoload")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        self.assertEqual(self.window.truth.live, "")
        (self.root / "Other.annoload").write_text("{}", encoding="utf-8")
        library.set_live(self.window.where.library_dir(), self.root / "Other.annoload")
        self.window.refresh_truth()
        self.assertEqual(self.window.truth.live, "Other")
        self.assertFalse(self.window.truth.is_live)
        self.assertTrue(self.window.source.truth_live.text().startswith("<b>Live: Other</b>"))

    def test_renaming_the_live_loadout_keeps_it_live(self):
        with mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root):
            self.window.on_disk = True
            self.window.loadout.save(self.window.loadout_path)
            self.window.live.note_live()
            self.assertTrue(self.window.files.rename_loadout("Renamed"))
        self.assertTrue(disk.same_file(self.live(), self.root / "Renamed.annoload"))
        self.window.files.mark_saved()


class TestForkingALoadout(TruthWindow):
    def setUp(self):
        super().setUp()
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.set_up_as("manage")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.loadout_path = self.root / "Main.annoload"
        patched = mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)
        # A separate loadout, which is what a fork was before profiles; a
        # fork as a new profile is in test_profiles_ui.
        patched = mock.patch.object(self.window.prompts, "ask_fork_kind", return_value="separate")
        patched.start()
        self.addCleanup(patched.stop)

    def test_the_copy_is_opened_and_the_original_stays_live_and_untouched(self):
        from anno_modding_studio.core import library

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.refresh_truth()
        self.window.live.note_live()
        self.assertTrue(self.window.truth is not None)
        with mock.patch.object(self.window.prompts, "ask_fork_name", return_value="Trying things") as asked:
            self.assertTrue(self.window.files.fork_loadout())
        asked.assert_called_once_with("Main")
        self.assertEqual(self.window.loadout_path, self.root / "Trying things.annoload")
        self.assertTrue(self.window.loadout_path.is_file())
        self.assertTrue((self.root / "Main.annoload").is_file())
        self.assertTrue(disk.same_file(library.live(self.window.where.library_dir()), self.root / "Main.annoload"))
        self.assertIn("Forked Main as Trying things. The original is untouched and is still "
                      "what the game loads", self.window.status_label.text())
        self.assertTrue(Loadout.load(self.window.loadout_path).own.loads_last)
        # The card says so: the copy is not live, and making it so changes nothing yet.
        self.assertFalse(self.window.truth.is_live)
        self.assertEqual(self.window.truth.live, "Main")
        self.window.files.mark_saved()

    def test_what_was_not_saved_goes_with_the_fork(self):
        self.window.loadout.save(self.window.loadout_path)
        self.window.on_disk = True
        self.window.files.mark_saved()
        self.window.loadout.layers.append(ModLayer(origin="[Gameplay] New", mod_id="new"))
        self.window.dirty = True
        self.assertTrue(self.window.files.fork_loadout("Fork"))
        self.assertEqual(len(Loadout.load(self.root / "Main.annoload").layers), 1)
        self.assertEqual(len(Loadout.load(self.root / "Fork.annoload").layers), 2)
        self.assertIn("What you had not saved is in the fork only", self.window.status_label.text())
        self.window.files.mark_saved()

    def test_a_name_already_taken_gets_a_number_rather_than_overwriting(self):
        (self.root / "Fork.annoload").write_text("{}", encoding="utf-8")
        self.assertTrue(self.window.files.fork_loadout("Fork"))
        self.assertEqual(self.window.loadout_path.name, "Fork (2).annoload")
        self.assertEqual((self.root / "Fork.annoload").read_text(encoding="utf-8"), "{}")
        self.window.files.mark_saved()

    def test_its_own_name_is_taken_too_in_any_letter_case(self):
        self.window.loadout.save(self.window.loadout_path)
        self.window.on_disk = True
        self.window.files.mark_saved()
        main = (self.root / "Main.annoload").read_text(encoding="utf-8")
        for name, fork in (("Main", "Main (2)"), ("main", "main (2)")):
            self.window.loadout_path = self.root / "Main.annoload"
            self.assertTrue(self.window.files.fork_loadout(name))
            self.assertEqual(self.window.loadout_path.name, fork + ".annoload")
            self.assertEqual((self.root / "Main.annoload").read_text(encoding="utf-8"), main)
            self.window.files.mark_saved()
            self.window.loadout_path.unlink()

    def test_every_loadout_with_a_file_is_forked(self):
        self.window.start.refresh_source_panel = mock.Mock()
        with mock.patch.object(self.window.prompts, "ask_fork_name", return_value=""):
            self.assertFalse(self.window.files.fork_loadout())
        self.window.source.set_loadout(self.window.loadout_path, True)
        self.assertFalse(self.window.source.fork_button.isHidden())
        self.window.source.set_loadout(None, False)
        self.assertTrue(self.window.source.fork_button.isHidden())
        # A loadout its own mod does not load last in is forked all the same.
        self.window.loadout.own.loads_last = False
        self.assertTrue(self.window.files.fork_loadout("Apart", kind=FORK_SEPARATE))
        self.assertTrue((self.root / "Apart.annoload").exists())


class TestALoadoutFromACollection(TruthWindow):
    """Aim 3's second button."""

    def setUp(self):
        super().setUp()
        patched = mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.read = []

        def load_installed_mods(focus=None, arrived=None, keep_switches=False):
            self.read.append(set(focus or ()))
            self.arrived = set(arrived or ())
            return True

        patched = mock.patch.object(self.window, "load_installed_mods", side_effect=load_installed_mods)
        patched.start()
        self.addCleanup(patched.stop)

    def test_the_card_has_both_buttons_and_the_second_starts_from_a_collection(self):
        card = self.window.source.ways["manage"]
        self.assertEqual(card.title.text(), "<b>Play a set of mods</b>")
        self.assertEqual(card.button.text(), "What I play now…")
        self.assertEqual(card.second_button.text(), "A collection…")
        with mock.patch.object(self.window.start, "choose_aim") as chose:
            self.window.source.collectionLoadoutRequested.emit()
        chose.assert_called_once_with("manage", collection=True)

    def test_with_nothing_live_it_is_the_collection_on_its_own(self):
        with mock.patch.object(self.window.prompts, "ask_start_from") as asked, \
                mock.patch.object(self.window.modio, "pick_a_collection",
                                  return_value=InstalledCollection(self.mods, "Jakob's", "[Gameplay] One")):
            self.window.start.choose_aim("manage", collection=True)
        asked.assert_not_called()
        self.assertEqual(self.read, [{"[Gameplay] One"}])
        # Installing wrote no profile, so what arrived is said to come in on.
        self.assertEqual(self.arrived, {"[Gameplay] One"})
        self.assertEqual(self.window.files.loadout_name(), "Jakob's")
        self.assertTrue(self.window.loadout.own.loads_last)
        self.assertEqual(self.window.loadout.provenance, {"[Gameplay] One": "Jakob's"})

    def test_on_top_of_the_live_loadout_is_a_copy_of_it(self):
        from anno_modding_studio.core import library
        from anno_modding_studio.ui.prompts import START_COPY

        main = Loadout(game=self.game)
        main.set_up_as("manage")
        main.mods_folder = str(self.mods)
        main.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine"),
                       ModLayer(origin="[Gameplay] Off", mod_id="off", active=False)]
        main.save(self.root / "Main.annoload")
        library.set_live(self.window.where.library_dir(), self.root / "Main.annoload")
        with mock.patch.object(self.window.prompts, "ask_start_from", return_value=START_COPY) as asked, \
                mock.patch.object(self.window.modio, "pick_a_collection",
                                  return_value=InstalledCollection(self.mods, "Jakob's", "[Gameplay] One")) as picked:
            self.window.start.choose_aim("manage", collection=True)
        asked.assert_called_once_with("Main")
        picked.assert_called_once_with(str(self.mods))
        self.assertEqual(self.read, [{"[Gameplay] One", "[Gameplay] Mine"}])
        self.assertEqual(self.window.files.loadout_name(), "Main + Jakob's")
        # Main itself is as it was, and still the live one.
        self.assertEqual(len(Loadout.load(self.root / "Main.annoload").layers), 2)
        self.assertTrue(disk.same_file(library.live(self.window.where.library_dir()),
                                          self.root / "Main.annoload"))

    def test_saying_neither_or_closing_the_search_starts_nothing(self):
        from anno_modding_studio.core import library

        (self.root / "Main.annoload").write_text("{}", encoding="utf-8")
        library.set_live(self.window.where.library_dir(), self.root / "Main.annoload")
        with mock.patch.object(self.window.prompts, "ask_start_from", return_value=""), \
                mock.patch.object(self.window.modio, "pick_a_collection") as picked:
            self.window.start.choose_aim("manage", collection=True)
        picked.assert_not_called()
        with mock.patch.object(self.window.prompts, "ask_start_from", return_value="nothing"), \
                mock.patch.object(self.window.modio, "pick_a_collection", return_value=None):
            self.window.start.choose_aim("manage", collection=True)
        self.assertEqual(self.read, [])
        self.assertEqual(self.window.loadout.aim, "")
        self.assertFalse(self.window.loadout.own.loads_last)
        self.assertIsNone(self.window.loadout_path)


class TestALoadoutFromTheLibrary(TruthWindow):
    """Aim 3's third button."""

    def setUp(self):
        super().setUp()
        patched = mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def test_the_card_has_a_third_button_that_starts_from_the_library(self):
        card = self.window.source.ways["manage"]
        self.assertEqual(card.third_button.text(), "From the library…")
        self.assertIsNone(self.window.source.ways["make"].third_button)
        with mock.patch.object(self.window.start, "choose_aim") as chose:
            self.window.source.libraryLoadoutRequested.emit()
        chose.assert_called_once_with("manage", from_library=True)

    def test_the_mods_ticked_in_its_list_are_a_new_loadout(self):
        from anno_modding_studio.core import library

        held = library.adopt(self.window.where.library_dir(), install_mod(self.mods, "[Gameplay] Mine", "mine", "1"))
        library.unlink(self.window.where.library_dir(), held.link)

        def arrive(picked=None):
            # The folder dialog on the library: none read until ticked.
            self.assertEqual(picked, [])
            self.window.loadout.mods_folder = str(self.mods)
            self.window.loadout.layers = [
                ModLayer(origin="[Gameplay] Mine", mod_id="mine", folder=str(held.link))]
            self.window.refresh_truth()
            return True

        with mock.patch.object(self.window, "load_installed_mods", side_effect=arrive), \
                mock.patch.object(self.window.prompts, "ask_convert_now") as offered:
            self.window.start.choose_aim("manage", from_library=True)
        self.assertTrue(self.window.loadout.own.loads_last)
        self.assertEqual(self.window.loadout.aim, "manage")
        self.assertTrue(self.window.files.loadout_name().endswith(" loadout"))
        # It holds no mod outside the library, so no conversion is offered.
        offered.assert_not_called()
        # The game does not load its mod yet, so it is not what is live.
        self.assertEqual(library.live(self.window.where.library_dir()), "")
        self.assertEqual([mod.name for mod in self.window.truth.wanted_but_off], ["[Gameplay] Mine"])

    def test_closing_the_list_starts_nothing(self):
        with mock.patch("anno_modding_studio.core.library.catalogue", return_value=[mock.Mock()]), \
                mock.patch.object(self.window, "load_installed_mods", return_value=False) as read:
            self.window.start.choose_aim("manage", from_library=True)
        read.assert_called_once_with(picked=[])
        self.assertFalse(self.window.loadout.own.loads_last)

    def test_an_empty_library_says_so_and_starts_nothing(self):
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Had")]
        with mock.patch.object(self.window, "load_installed_mods") as read, \
                mock.patch("anno_modding_studio.ui.start_controller.QMessageBox.information") as said, \
                mock.patch("anno_modding_studio.ui.start_controller.ask") as asked:
            self.window.start.choose_aim("manage", from_library=True)
        said.assert_called_once()
        self.assertIn("The library holds no mods for", said.call_args.args[2])
        # Said before Start fresh is asked: nothing is cleared for nothing.
        asked.assert_not_called()
        read.assert_not_called()
        self.assertEqual(len(self.window.loadout.layers), 1)
        self.assertFalse(self.window.loadout.own.loads_last)
        self.window.loadout.layers = []


class TestWhatIPlayNow(TruthWindow):
    """Aim 3's first button: what the game loads, read into a loadout that is
    live from the start -- making it so would change nothing."""

    def test_read_as_it_is_it_is_live(self):
        from anno_modding_studio.core import library

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")

        def arrive(focus=None):
            self.window.loadout.mods_folder = str(self.mods)
            self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
            self.window.refresh_truth()
            return True

        with mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root), \
                mock.patch.object(self.window, "load_installed_mods", side_effect=arrive), \
                mock.patch.object(self.window.prompts, "ask_convert_now", return_value=False) as offered:
            self.window.start.choose_aim("manage")
        # The first read with a mod outside the library: the conversion is
        # offered for the one mod that is a folder of the user's own.
        offered.assert_called_once_with(1, self.window.loadout.game)
        # Declined, the Start tab stays: its fourth card is where the offer is.
        from anno_modding_studio.ui.navigation import TAB_SOURCE

        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)
        self.assertTrue(self.window.loadout.own.loads_last)
        self.assertTrue(disk.same_file(library.live(self.window.where.library_dir()), self.window.loadout_path))
        self.assertTrue(self.window.loadout_path.is_file())
        self.assertTrue(self.window.truth.is_live)

    def offer(self):
        """Whether the conversion is offered for what is in the folder."""
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        with mock.patch.object(self.window.prompts, "ask_convert_now", return_value=False) as offered:
            made = self.window.library_controller.offer_conversion()
        self.assertEqual(made, offered.called)
        return made

    def test_the_conversion_is_offered_each_time_there_is_something_to_convert(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.assertTrue(self.offer())
        self.assertTrue(self.offer())

    def test_a_library_already_in_use_is_still_offered_the_conversion(self):
        """One mod installed from mod.io first used to mean it never was."""
        from anno_modding_studio.core import library

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        held = install_mod(self.root / "library", "[Gameplay] Held", "held", "1")
        linked = library.link(held, self.mods, "[Gameplay] Held")
        library.note_link(self.root / "library", linked, library.COPY)
        self.assertTrue(library.links(self.root / "library"))
        self.assertTrue(self.offer())

    def test_with_no_mod_outside_the_library_no_offer_is_made(self):
        self.assertFalse(self.offer())

    def test_the_menu_and_the_cards_row_open_the_whole_survey(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.refresh_truth()
        self.assertTrue(self.window.convert_action.isEnabled())
        with mock.patch.object(self.window.library_controller, "convert_to_library") as converted:
            self.window.convert_action.trigger()
            self.window.source.convert_button.click()
        self.assertEqual(converted.call_args_list, [mock.call(), mock.call(only=None)])
        # With no mod outside the library there is no row, and no conversion to start.
        shutil.rmtree(self.mods / "[Gameplay] Mine")
        self.window.refresh_truth()
        self.assertTrue(self.window.source.convert_row.isHidden())
        self.assertFalse(self.window.convert_action.isEnabled())


class TestAProfileOfTheLoadoutsOwn(TruthFolders):
    """117: a loadout is written as a profile and made the live one."""

    game = ANNO_117

    def test_the_profile_is_written_whole_frozen_and_marked_as_the_studios(self):
        path = switches.write_profile(self.mods, "My Set", "My Set", {"a": True, "b": False}, frozen=True)
        self.assertEqual(path, self.mods / "profiles" / "My Set.txt")
        text = path.read_text(encoding="utf-8")
        self.assertIn(switches.STUDIO_LINE, text)
        self.assertTrue(text.endswith("# EnableNewMods\nDisplayName My Set\n\na\n# b\n"))
        self.assertTrue(switches.is_studios(path))
        profile = switches.read_profile(path)
        self.assertEqual((profile.on, profile.new_mods_on), ({"a": True, "b": False}, False))

    def test_a_modid_with_a_line_break_writes_no_line_of_its_own(self):
        # A ModID is a mod's own say; one holding a line break would switch
        # EnableNewMods back on, or switch on a mod the loadout leaves off.
        path = switches.write_profile(
            self.mods, "My Set", "My\nSet", {"a": True, "x\nenablenewmods": True},
            frozen=True)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("enablenewmods", lines)
        self.assertIn("DisplayName My Set", lines)
        profile = switches.read_profile(path)
        self.assertEqual((profile.on, profile.new_mods_on), ({"a": True}, False))

    def test_a_file_of_that_name_that_is_not_the_studios_is_left_alone(self):
        (self.mods / "profiles").mkdir()
        theirs = self.mods / "profiles" / "My Set.txt"
        theirs.write_text("DisplayName My Set\ntheirs\n", encoding="utf-8")
        path = switches.write_profile(self.mods, "My Set", "My Set", {"a": True}, frozen=False)
        self.assertEqual(path.name, "My Set (2).txt")
        self.assertEqual(theirs.read_text(encoding="utf-8"), "DisplayName My Set\ntheirs\n")
        # And the Studio's own is rewritten in place next time.
        again = switches.write_profile(self.mods, "My Set", "My Set", {"a": False}, frozen=False)
        self.assertEqual(again, path)
        self.assertIn("\n# a\n", path.read_text(encoding="utf-8"))

    def test_the_studios_profile_rewritten_by_the_game_is_still_the_studios(self):
        """The game can write the Studio's profile back without the line
        that marks it as the Studio's (seen 4 Oct 2026).  Taken for the
        user's, every Play after wrote a new numbered copy beside it
        (A11-1); the name is the Studio's own, so it is rewritten."""
        name = switches.STUDIO_PROFILE
        first = switches.write_profile(self.mods, name, "Set", {"a": True}, frozen=True)
        for on in (False, True, False):
            first.write_text("EnableNewMods\nDisplayName Set\n\na\n", encoding="utf-8")
            again = switches.write_profile(self.mods, name, "Set", {"a": on}, frozen=True)
            self.assertEqual(again, first)
        self.assertEqual(sorted(p.name for p in (self.mods / "profiles").iterdir()),
                         [name + ".txt"])
        self.assertTrue(switches.is_studios(first))

    def test_the_pointer_is_moved_and_its_comments_kept(self):
        pointer = self.mods / "profile.txt"
        pointer.write_text("## a comment\nActiveProfile default\n", encoding="utf-8")
        switches.point_at(self.mods, "My Set")
        self.assertEqual(pointer.read_text(encoding="utf-8"), "## a comment\nActiveProfile My Set\n")
        self.assertEqual(switches.pointed_at(self.mods), "My Set")
        # A folder without one gets one.
        other = self.root / "other"
        other.mkdir()
        switches.point_at(other, "x")
        self.assertEqual(switches.pointed_at(other), "x")
        self.assertEqual(switches.pointed_at(self.documents), "default")

    def test_a_profile_name_is_a_file_name(self):
        self.assertEqual(switches.profile_name("Jakob's: set #1?"), "Jakob's_ set _1_")
        self.assertEqual(switches.profile_name(""), "loadout")

    def test_the_pointer_folder_is_the_one_with_a_pointer_else_documents(self):
        self.assertEqual(switches.pointer_folder(self.mods, ANNO_117), self.documents)
        (self.mods / "profile.txt").write_text("ActiveProfile default\n", encoding="utf-8")
        self.assertEqual(switches.pointer_folder(self.mods, ANNO_117), self.mods)
        self.assertIsNone(switches.pointer_folder(self.mods, ANNO_1800))


class TestWhereAModCameFrom(TruthFolders):
    def test_provenance_is_saved_and_attached_when_the_folder_is_read(self):
        loadout = self.loadout()
        loadout.provenance["[Gameplay] Thing"] = "Jakob's Collection"
        path = self.root / "p.annoload"
        loadout.save(path)
        loaded = Loadout.load(path)
        self.assertEqual(loaded.provenance, {"[Gameplay] Thing": "Jakob's Collection"})
        loaded.set_layer(ModLayer(origin="[Gameplay] Thing", mod_id="thing"))
        loaded.set_layer(ModLayer(origin="[Gameplay] Thing/shared/Piece", mod_id="piece"))
        loaded.set_layer(ModLayer(origin="[Gameplay] Other", mod_id="other"))
        self.assertEqual({layer.origin: layer.from_collection for layer in loaded.layers}, {
            "[Gameplay] Thing": "Jakob's Collection",
            "[Gameplay] Thing/shared/Piece": "Jakob's Collection",
            "[Gameplay] Other": "",
        })
        self.assertEqual(loaded.collections(), ["Jakob's Collection"])
        loaded.save(path)
        self.assertEqual(Loadout.load(path).layer("[Gameplay] Thing").from_collection, "Jakob's Collection")


class TestSwitchingToMatchOnAnno117(TruthWindow):
    game = ANNO_117

    def setUp(self):
        super().setUp()
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout_path = self.root / "Roman Set.annoload"
        (self.documents / "profile.txt").write_text("## keep me\nActiveProfile default\n", encoding="utf-8")
        (self.documents / "profiles").mkdir()
        (self.documents / "profiles" / "default.txt").write_text(
            "EnableNewMods\nDisplayName default\nmine\n", encoding="utf-8"
        )

    def sync(self):
        from PySide6.QtWidgets import QMessageBox

        asked = []

        def information(_parent, _title, text, *_rest):
            asked.append("INFO " + text)

        with _isolation.answering(lambda _title, text: asked.append(text) or True), \
                mock.patch.object(QMessageBox, "information", staticmethod(information)), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.live.sync_loadout()
        return asked

    def test_a_mod_of_ubisofts_is_not_said_to_come_from_the_library(self):
        """Nothing is left to convert, and the done line said so by saying
        everything the game loads comes from the Studio's library."""
        from anno_modding_studio.core import convert

        install_mod(self.mods / ".ubi", "Shipped", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="Shipped", mod_id="mine")]
        self.window.refresh_truth()
        row, = convert.survey(self.window.truth).rows
        self.assertEqual((row.where, row.kept.loads, row.to_do), (convert.UBI, True, False))
        self.assertTrue(self.window.source.convert_row.isHidden())
        self.assertTrue(self.window.source.checklist_done.isHidden())

    def test_a_loadout_is_written_into_the_studios_profile_and_made_the_live_one(self):
        install_mod(self.mods, "Mine", "mine", "1")
        install_mod(self.mods, "Off", "off", "1")
        install_mod(self.mods, "Stray", "stray", "1")
        self.cached(5, "Theirs", "theirs", "1")
        self.window.loadout.layers = [
            ModLayer(origin="Mine", mod_id="mine"), ModLayer(origin="Off", mod_id="off", active=False),
        ]
        self.window.loadout.own.loads_last = True
        asked = self.sync()
        self.assertIn("the Studio's profile, Anno Modding Studio", asked[0])
        written = self.documents / "profiles" / "Anno Modding Studio.txt"
        self.assertTrue(written.is_file())
        # One file whatever loadout is played; its DisplayName says which.
        self.assertTrue(written.read_text(encoding="utf-8").endswith(
            "# EnableNewMods\nDisplayName Roman Set\n\nmine\n# off\n# stray\n# theirs\n"))
        self.assertEqual((self.documents / "profile.txt").read_text(encoding="utf-8"),
                         "## keep me\nActiveProfile Anno Modding Studio\n")
        # The user's own profile is not touched.
        self.assertEqual((self.documents / "profiles" / "default.txt").read_text(encoding="utf-8"),
                         "EnableNewMods\nDisplayName default\nmine\n")
        self.assertIn("wrote 4 mods into Anno Modding Studio.txt", self.window.status_label.text())
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 4 outside the library")
        # Again: nothing to ask, and the profile is written all the same --
        # every Play leaves it saying exactly this loadout.
        written.write_text(written.read_text(encoding="utf-8") + "# hand-edited\n", encoding="utf-8")
        again = self.sync()
        self.assertIn("INFO", again[0])
        self.assertIn("The Studio's profile matches", again[0])
        self.assertNotIn("hand-edited", written.read_text(encoding="utf-8"))
        # Another loadout played is the same file, saying the other loadout.
        self.window.loadout_path = self.root / "Celtic Set.annoload"
        self.sync()
        self.assertIn("DisplayName Celtic Set", written.read_text(encoding="utf-8"))
        self.assertFalse((self.documents / "profiles" / "Roman Set.txt").exists())
        self.window.files.mark_saved()

    def test_a_loadout_that_is_not_a_loadout_gets_a_profile_of_its_own_too(self):
        # Every loadout is complete when played: the user's own profile is
        # never the one rewritten.
        install_mod(self.mods, "Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="Mine", mod_id="mine", active=False)]
        self.sync()
        self.assertIn("# mine", (self.documents / "profiles" / "Anno Modding Studio.txt").read_text(encoding="utf-8"))
        self.assertEqual((self.documents / "profiles" / "default.txt").read_text(encoding="utf-8"),
                         "EnableNewMods\nDisplayName default\nmine\n")
        self.window.files.mark_saved()

    def test_the_mods_options_are_written_where_the_game_reads_them(self):
        from anno_modding_studio.core import settings

        (self.documents / "active-options.jsonc").write_text(
            '{\n  "someone-else": {"x": "1"},\n  "mine": {"speed": "3"}\n}\n', encoding="utf-8"
        )
        install_mod(self.mods, "Mine", "mine", "1")
        offered = settings.ModOptions([
            settings.Option(name="speed", label="Speed", kind=settings.SLIDER, default="1"),
            settings.Option(name="walls", label="Walls", kind=settings.TOGGLE, default="false"),
        ]).to_dict()
        self.window.loadout.layers = [ModLayer(
            origin="Mine", mod_id="mine", offered=offered, options={"speed": "1", "walls": "true"},
        )]
        asked = self.sync()
        self.assertIn("Mod options written into active-options.jsonc for: mine", asked[0])
        written = json.loads((self.documents / "active-options.jsonc").read_text(encoding="utf-8"))
        # Only what differs from the defaults, and nobody else's entry touched.
        self.assertEqual(written, {"someone-else": {"x": "1"}, "mine": {"walls": "true"}})
        self.assertIn("Wrote options for 1 mod", self.window.status_label.text())
        # Again: nothing left to write.
        again = self.sync()
        self.assertIn("INFO", again[0])
        self.window.files.mark_saved()


class TestSwitchingOffACollection(TruthWindow):
    def test_its_mods_go_off_in_the_loadout_and_not_yet_in_the_folder(self):
        install_mod(self.mods, "[Gameplay] One", "one", "1")
        install_mod(self.mods, "[Gameplay] Two", "two", "1")
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.provenance = {"[Gameplay] One": "Jakob's", "[Gameplay] Two": "Jakob's"}
        for origin, mod_id in (("[Gameplay] One", "one"), ("[Gameplay] Two", "two"), ("[Gameplay] Mine", "mine")):
            self.window.loadout.set_layer(ModLayer(origin=origin, mod_id=mod_id, folder=str(self.mods / origin)))
        self.assertEqual(self.window.loadout.collections(), ["Jakob's"])

        with mock.patch.object(self.window.prompts, "pick_collection", return_value="Jakob's"), \
                _isolation.answering(True), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.modio.switch_off_collection())
        # Nothing reaches the folder until Play.
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()),
                         ["[Gameplay] Mine", "[Gameplay] One", "[Gameplay] Two"])
        self.assertIn("Play", self.window.status_label.text())
        self.assertEqual({layer.origin: layer.active for layer in self.window.loadout.layers},
                         {"[Gameplay] One": False, "[Gameplay] Two": False, "[Gameplay] Mine": True})
        with mock.patch.object(self.window.prompts, "pick_collection", return_value="Jakob's"):
            self.assertFalse(self.window.modio.switch_off_collection())
        self.window.files.mark_saved()


class TestACollectionsModsAndYours(TruthWindow):
    """A collection lists some mods you had before it. They are yours, and
    in it: switching the collection off leaves them on unless you say."""

    def setUp(self):
        super().setUp()
        for origin in ("[Gameplay] Brought", "[Gameplay] Mine", "[Gameplay] Other"):
            install_mod(self.mods, origin, origin.split()[-1].lower(), "1")
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.note_collection(
            "Jakob's", ["[Gameplay] Brought", "[Gameplay] Mine"], brought=["[Gameplay] Brought"])
        for origin in ("[Gameplay] Brought", "[Gameplay] Mine", "[Gameplay] Other"):
            self.window.loadout.set_layer(ModLayer(origin=origin, mod_id=origin.split()[-1].lower(),
                                                   folder=str(self.mods / origin)))
        self.addCleanup(self.window.files.mark_saved)

    def switched(self, yours_too: bool) -> dict:
        with mock.patch.object(self.window.prompts, "pick_collection", return_value="Jakob's"), \
                mock.patch.object(self.window.prompts, "ask_switch_yours_too", return_value=yours_too) as asked, \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.modio.switch_off_collection()
        asked.assert_called_once_with("Jakob's", ["[Gameplay] Mine"])
        return {layer.origin: layer.active for layer in self.window.loadout.layers}

    def test_the_loadout_knows_which_it_brought_and_which_it_only_lists(self):
        loadout = self.window.loadout
        self.assertEqual(loadout.layer("[Gameplay] Brought").from_collection, "Jakob's")
        self.assertEqual(loadout.layer("[Gameplay] Mine").from_collection, "")
        self.assertEqual(loadout.listed_in("[Gameplay] Mine"), ["Jakob's"])
        self.assertEqual(loadout.collections(), ["Jakob's"])
        loadout.save(self.root / "kept.annoload")
        again = Loadout.load(self.root / "kept.annoload")
        self.assertEqual((again.provenance, again.listed), (loadout.provenance, loadout.listed))

    def test_switching_it_off_leaves_yours_on(self):
        self.assertEqual(self.switched(yours_too=False),
                         {"[Gameplay] Brought": False, "[Gameplay] Mine": True, "[Gameplay] Other": True})
        self.assertIn("1 it lists that you had before it stays on", self.window.status_label.text())

    def test_unless_you_say_so(self):
        self.assertEqual(self.switched(yours_too=True),
                         {"[Gameplay] Brought": False, "[Gameplay] Mine": False, "[Gameplay] Other": True})

    def test_a_caller_that_cannot_tell_says_they_all_came_with_it(self):
        loadout = Loadout(game=ANNO_1800)
        loadout.note_collection("Old", ["A", "B"])
        self.assertEqual(loadout.provenance, {"A": "Old", "B": "Old"})


class TestACollectionThatOnlyListsYours(TruthWindow):
    """Every mod a collection lists was installed before it: it brought
    none, and switching it off must not say that what it brought is off."""

    def setUp(self):
        super().setUp()
        for origin in ("[Gameplay] Mine", "[Gameplay] Also"):
            install_mod(self.mods, origin, origin.split()[-1].lower(), "1")
            self.window.loadout.set_layer(ModLayer(origin=origin, mod_id=origin.split()[-1].lower(),
                                                   folder=str(self.mods / origin)))
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.note_collection(
            "Jakob's", ["[Gameplay] Mine", "[Gameplay] Also"], brought=[])
        self.addCleanup(self.window.files.mark_saved)

    def switch_off(self, yours_too: bool) -> bool:
        with mock.patch.object(self.window.prompts, "pick_collection", return_value="Jakob's"), \
                mock.patch.object(self.window.prompts, "ask_switch_yours_too", return_value=yours_too), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            return self.window.modio.switch_off_collection()

    def test_leaving_yours_on_says_it_brought_none(self):
        self.assertFalse(self.switch_off(yours_too=False))
        said = self.window.status_label.text()
        self.assertNotIn("brought is off", said)
        self.assertIn("Jakob's brought no mods", said)
        self.assertIn("2 it lists that you had before it stay on", said)
        self.assertTrue(all(layer.active for layer in self.window.loadout.layers))

    def test_with_yours_off_already_it_says_what_it_lists_is_off(self):
        for layer in self.window.loadout.layers:
            layer.active = False
        self.assertFalse(self.switch_off(yours_too=False))
        self.assertEqual(self.window.status_label.text(),
                         "Every mod Jakob's lists is off in this loadout already.")

    def test_saying_so_switches_yours_off(self):
        self.assertTrue(self.switch_off(yours_too=True))
        self.assertFalse(any(layer.active for layer in self.window.loadout.layers))


class TestTheCardFollowsTheChangesPanel(TruthWindow):
    """A mod switched on or off in the changes panel moves the loadout's side
    of the fourth card, without the folders being looked at again."""

    def test_a_switch_flipped_moves_the_counts_and_reads_no_disk(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Other", "other", "1")
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Mine", mod_id="mine"),
            ModLayer(origin="[Gameplay] Other", mod_id="other"),
        ]
        self.window.refresh_truth()
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 2 outside the library")
        self.window.loadout.layer("[Gameplay] Other").active = False
        with mock.patch.object(installplan, "inventory") as inventory:
            self.window.on_activation_changed()
        inventory.assert_not_called()
        self.assertEqual(self.window.source.truth_step.status.full_text(), "Play changes 1")
        self.assertEqual(self.window.source.truth_cells["off", "loads"].text(), "1")
        self.window.loadout.layer("[Gameplay] Other").active = True
        self.window.on_activation_changed()
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 2 outside the library")
        self.window.files.mark_saved()


class TestPlay(PlayingWindow):
    """Make this loadout what the game loads, then start the game."""

    def test_already_live_it_starts_the_game_through_steam_without_asking(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        with mock.patch.object(self.window.prompts, "confirm_live") as asked:
            self.assertTrue(self.window.live.play())
        asked.assert_not_called()
        self.assertEqual(self.started_now(), ["steam://rungameid/916440"])
        self.assertIn("Starting Anno 1800", self.window.status_label.text())
        self.assertTrue(self.window.truth.is_live)

    def test_it_says_what_it_will_change_then_changes_it_then_starts(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        with mock.patch.object(self.window.prompts, "confirm_live", return_value=False):
            self.assertFalse(self.window.live.play())
        self.assertEqual(self.started_now(), [])
        self.assertTrue((self.mods / "[Gameplay] Stray").is_dir())

        with _isolation.answering("Play") as asked:
            self.assertTrue(self.window.live.play())
        (title,), (text,) = asked.titles, asked.texts
        self.assertEqual(title, "Play Main?")
        self.assertIn("does not have them on:\n  • [Gameplay] Stray", text)
        self.assertTrue(text.endswith("Then Anno 1800 starts."))
        self.assertTrue((self.mods / "-[Gameplay] Stray").is_dir())
        self.assertEqual(self.started_now(), ["steam://rungameid/916440"])

    # -- a mod other loadouts have (docs/proposals/export-tab-proposal.md, 5.6)
    def other_has_it(self):
        """Another loadout, Other, that has this loadout's mod and is what
        the game loads, as the files cache would say."""
        other = self.root / "Other.annoload"
        elsewhere = [(other, "Other", True)]
        for patched in (mock.patch.object(self.window.live, "played_elsewhere", return_value=elsewhere),
                        mock.patch.object(self.window, "_played_elsewhere", return_value=elsewhere)):
            patched.start()
            self.addCleanup(patched.stop)
        self.window._show_play()
        return other

    def test_where_play_plays_is_a_menu_on_the_button(self):
        other = self.other_has_it()
        window = self.window
        self.assertTrue(window.play_where.isVisibleTo(window))
        self.assertTrue(window.play_where_tip.isVisibleTo(window))
        menu = window.play_where.menu()
        self.assertEqual([action.text() for action in menu.actions()],
                         ["On its own", "In Other, what the game loads"])
        self.assertEqual([action.isChecked() for action in menu.actions()], [True, False])
        menu.actions()[1].trigger()
        self.assertEqual(window.play_button.text(), "▶  Play in Other")
        self.assertIn("opens Other and plays it", window.play_button.toolTip())
        self.assertEqual(window.live.play_target(), other)
        self.assertEqual([action.isChecked() for action in window.play_where.menu().actions()], [False, True])
        window.play_where.menu().actions()[0].trigger()
        self.assertEqual(window.play_button.text(), "▶  Play")
        self.assertIsNone(window.live.play_target())

    def test_play_in_another_loadout_updates_the_mod_then_opens_and_plays_that_one(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        other = self.other_has_it()
        live, window = self.window.live, self.window
        live.choose_play(other)

        def opened(path):
            window.loadout_path = path
            return True

        # Other's own question answered Cancel: what it would do is its own.
        with mock.patch.object(window.prompts, "confirm_live", return_value=False) as asked,                 mock.patch.object(window.loadout, "stands_for", return_value=""),                 mock.patch.object(window.output, "update_build", return_value=True) as updated,                 mock.patch.object(window, "load_loadout", side_effect=opened) as loaded:
            self.assertFalse(live.play())
        updated.assert_called_once_with("")
        loaded.assert_called_once_with(other)
        asked.assert_called_once()
        self.assertEqual(self.started_now(), [])
        self.assertTrue((self.mods / "[Gameplay] Stray").is_dir())

    def test_the_choice_follows_a_rename_of_either_loadout(self):
        """R3-15, hands-on run 3: the choice was kept by the file, and the
        rename lost it."""
        other = self.other_has_it()
        live, window = self.window.live, self.window
        live.choose_play(other)
        was, now = window.loadout_path, self.root / "Renamed.annoload"
        live.follow_rename(was, now)
        window.loadout_path = now
        self.addCleanup(setattr, window, "loadout_path", was)
        self.assertEqual(live.play_target(), other)
        # And Other renamed: the choice to play in it names its new file.
        moved = self.root / "Other renamed.annoload"
        live.follow_rename(other, moved)
        self.assertEqual(window.settings.value(live._choice_key()), str(moved))

    def test_one_that_no_longer_has_the_mod_is_not_played_in(self):
        other = self.root / "Other.annoload"
        self.window.live.choose_play(other)
        with mock.patch.object(self.window.live, "played_elsewhere", return_value=[]):
            self.assertIsNone(self.window.live.play_target())

    def test_a_loadout_made_to_play_is_not_played_elsewhere(self):
        self.assertIsNone(self.window.loadout.stands_for())
        self.assertEqual(self.window.live.played_elsewhere(), [])

    def test_an_edit_held_after_an_update_is_not_said_to_be_to_somebody_elses_mod(self):
        """Found in the hands-on run of 24 October: a mod of yours, its
        folder changed under it, listed under "Your edits to other
        people's mods"."""
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        loadout = self.window.loadout
        loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        loadout.held_edits = [{"uid": "1", "origin": "[Gameplay] Mine", "kind": "replaced",
                               "why": "changed", "was": "", "now": "", "yours": [], "active": []}]
        with _isolation.answering(False) as asked:
            self.assertFalse(self.window.live.play())
        (text,) = asked.texts
        self.assertIn("Edits held after an update:\n  • [Gameplay] Mine: 1 edit is held", text)
        self.assertNotIn("other people's mods", text)

    def test_refused_while_the_game_runs(self):
        from PySide6.QtWidgets import QMessageBox

        from anno_modding_studio.core import launch

        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        said = []
        with mock.patch.object(launch, "_processes", return_value=["Anno1800.exe"]), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda _p, title, *a: said.append(title))):
            self.assertFalse(self.window.live.play())
        self.assertEqual(said, ["Anno 1800 is running"])
        self.assertTrue((self.mods / "[Gameplay] Stray").is_dir())
        self.assertEqual(self.started_now(), [])

    def test_greyed_while_the_game_is_open(self):
        from anno_modding_studio.core import launch

        window = self.window
        plays = (window.play_button, window.play_action, window.source.play_button)
        self.assertTrue(window._game_watch.isActive())
        # Drawn for the files the test window was handed.
        window.update_title()
        self.assertTrue(all(play.isEnabled() for play in plays))
        with mock.patch.object(launch, "_processes", return_value=["Anno1800.exe"]):
            window._watch_game()
            # Drawn again by whatever else draws Play, it stays greyed.
            window.update_title()
        self.assertFalse(any(play.isEnabled() for play in plays))
        self.assertIn("Anno 1800 is open", window.play_button.toolTip())
        self.assertIn("Anno 1800 is open", window.source.play_button.toolTip())
        window._watch_game()
        self.assertTrue(all(play.isEnabled() for play in plays))
        self.assertNotIn("is open", window.play_button.toolTip())
        self.assertNotIn("is open", window.source.play_button.toolTip())

    def test_with_no_store_to_start_it_through_it_offers_to_choose_and_stays_live(self):
        from anno_modding_studio.core import launch

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        with mock.patch.object(launch, "install_for", return_value=None), \
                mock.patch.object(self.window.prompts, "ask_game_start", return_value="") as asked:
            self.assertFalse(self.window.live.play())
        asked.assert_called_once_with("Anno 1800", "")
        self.assertEqual(self.started_now(), [])
        self.assertTrue(self.window.truth.is_live)
        # Chosen there, it is started now and remembered for next time.
        launcher = str(self.root / "Launcher.exe")
        with mock.patch.object(launch, "install_for", return_value=None), \
                mock.patch.object(self.window.prompts, "ask_game_start", return_value=launcher):
            self.assertTrue(self.window.live.play())
        self.assertEqual(self.started_now(), [launcher])
        self.assertEqual(self.window.preferences.game_start("anno1800"), launcher)

    def test_a_choice_that_does_not_start_either_is_said_and_not_kept(self):
        """It was kept before it was tried, and Play then did nothing and
        said nothing: the prompt comes back, naming what failed."""
        from anno_modding_studio.core import launch

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        broken = str(self.root / "Broken.lnk")
        tried = []

        def start(address):
            tried.append(address)
            return address != broken

        with mock.patch.object(launch, "install_for", return_value=None), \
                mock.patch.object(launch, "start", start), \
                mock.patch.object(self.window.prompts, "ask_game_start", side_effect=[broken, ""]) as asked:
            self.assertFalse(self.window.live.play())
        self.assertEqual(asked.call_args_list, [mock.call("Anno 1800", ""), mock.call("Anno 1800", broken)])
        self.assertEqual(tried, [broken])
        self.assertEqual(self.window.preferences.game_start("anno1800"), "")

    def test_a_way_chosen_in_the_settings_wins_over_the_store(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.preferences.set_game_start("anno1800", r"C:\Games\My launcher.lnk")
        self.assertTrue(self.window.live.play())
        self.assertEqual(self.started_now(), [r"C:\Games\My launcher.lnk"])
        self.window.preferences.set_game_start("anno1800", "")
        self.assertEqual(self.window.preferences.automatic_game_start("anno1800"), "steam://rungameid/916440")

    def test_with_no_loadout_play_only_starts_the_game(self):
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout_path = None
        self.window.loadout.layers = []
        self.window.update_title()
        self.assertEqual(self.window.play_button.objectName(), "")
        self.assertIn("your mods folder as it is", self.window.play_button.toolTip())
        with mock.patch.object(self.window.prompts, "confirm_live") as asked:
            self.assertTrue(self.window.live.play())
        asked.assert_not_called()
        # Nothing switched: an empty loadout made live would switch every mod off.
        self.assertTrue((self.mods / "[Gameplay] Stray").is_dir())
        self.assertEqual(self.started_now(), ["steam://rungameid/916440"])
        self.window.loadout_path = self.root / "Main.annoload"
        self.window.update_title()
        self.assertEqual(self.window.play_button.objectName(), "Primary")

    def test_play_is_greyed_until_a_games_files_are_loaded_and_says_so(self):
        """Greyed on a cold start and not after File > New loadout: the two
        differ in whether there is a game to start, and nothing said so. The
        greyed button offered to start Anno 1800 "with your mods folder as it
        is", which it could not."""
        button, action = self.window.play_button, self.window.play_action
        self.window.loadout_path = None
        self.window.loadout.layers = []
        index, self.window.index = self.window.index, None
        self.window.update_title()
        self.assertFalse(button.isEnabled())
        self.assertFalse(action.isEnabled())
        self.assertEqual(button.objectName(), "")
        self.assertEqual(button.toolTip(), "Load the game's files first, on the Start tab.")
        # With them, and no loadout: it starts the game and changes nothing,
        # before a loadout as after File > New loadout.
        self.window.index = index
        self.window.update_title()
        before = (button.isEnabled(), action.isEnabled(), button.objectName(), button.toolTip())
        self.assertEqual(before, (True, True, "", "Starts Anno 1800 with your mods folder as it is. (F5)"))
        self.window.loadout_path = self.root / "Main.annoload"
        self.window.update_title()
        self.assertEqual(button.objectName(), "Primary")
        self.window.reset_loadout()
        self.assertEqual(
            (button.isEnabled(), action.isEnabled(), button.objectName(), button.toolTip()), before
        )

    def test_the_button_sits_just_after_the_last_tab(self):
        self.window._place_play()
        bar = self.window.tabs.tabBar()
        last = bar.tabRect(bar.count() - 1)
        right = bar.mapTo(self.window.tabs, last.topRight()).x()
        self.assertGreater(self.window.play_button.x(), right)
        self.assertLess(self.window.play_button.x(), right + 40)

    def test_the_loadouts_own_mod_is_built_into_the_library_and_counts_as_its_own(self):
        from anno_modding_studio.core import generator, library
        from anno_modding_studio.core.changes import Change

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        self.window.loadout.make_own_mod()
        self.window.loadout.set_change(Change(
            guid="1000", asset_label="Farm", path="/Values/Building/Radius", tag="Radius",
            old_value="20", new_value="30",
        ))
        asked = []
        with mock.patch.object(generator, "verify_changes", return_value=[]), \
                mock.patch.object(self.window.prompts, "confirm_live",
                                  side_effect=lambda plan, launch, *_: asked.append(plan) or True):
            self.assertTrue(self.window.live.sync_loadout())
        (plan,) = asked
        self.assertEqual(plan.built, [""])
        built = self.mods / plan.built_names[0]
        self.assertTrue(library.is_junction(built))
        self.assertTrue(library.holds(built, self.root / "library"))
        self.assertIn(
            "<Radius>30</Radius>",
            (built / "data/config/export/main/asset/assets.xml").read_text(encoding="utf-8"),
        )
        # It loads, and it is this loadout's: nothing left to change.
        self.assertTrue(self.window.truth.matches)
        self.assertIn(plan.built_names[0], [mod.installed.folder_name for mod in self.window.truth.in_loadout])
        self.assertTrue(self.window.status_label.text().startswith(
            "Built %s into the library." % plan.built_names[0]))

    def test_a_build_already_live_is_not_built_or_asked_about_again(self):
        """Play asked every time, even with nothing changed: a loadout with
        a mod of your own built it again on each press, the same build."""
        from anno_modding_studio.core import generator, library
        from anno_modding_studio.core.changes import Change

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        loadout = self.window.loadout
        loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        loadout.make_own_mod()
        loadout.set_change(Change(
            guid="1000", asset_label="Farm", path="/Values/Building/Radius", tag="Radius",
            old_value="20", new_value="30",
        ))
        self.addCleanup(self.window.files.mark_saved)
        with mock.patch.object(generator, "verify_changes", return_value=[]):
            with mock.patch.object(self.window.prompts, "confirm_live", return_value=True):
                self.assertTrue(self.window.live.sync_loadout())
            number = dict(loadout.builds[""])
            with mock.patch.object(self.window.prompts, "confirm_live") as asked, \
                    mock.patch.object(library, "record_built") as rebuilt, \
                    mock.patch.object(self.window.live, "_launch", return_value=True):
                self.assertTrue(self.window.live.play())
            asked.assert_not_called()
            rebuilt.assert_not_called()
            self.assertEqual(loadout.builds[""], number)
            self.assertIn("already has the same mods", self.window.status_label.text())
            # Changed since, it is built and asked about again.
            loadout.set_change(Change(
                guid="1000", asset_label="Farm", path="/Values/Building/Radius", tag="Radius",
                old_value="20", new_value="40",
            ))
            plans = []
            with mock.patch.object(self.window.prompts, "confirm_live",
                                   side_effect=lambda plan, *_: plans.append(plan) or True):
                self.assertTrue(self.window.live.sync_loadout())
        (plan,) = plans
        self.assertEqual(plan.built, [""])
        self.assertEqual(loadout.builds[""]["build"], number["build"] + 1)

    def test_imodyouranno_settings_go_into_a_generated_copy_and_never_a_folder_of_yours(self):
        """The library's copy stays as its author shipped it: the settings go
        into a generated copy of it, which its link points at."""
        from anno_modding_studio.core import generated, imya, library

        def tweakable(folder: Path, name: str) -> Path:
            mod = folder / name
            (mod / "data/config/export/main/asset").mkdir(parents=True)
            (mod / "data/config/export/main/asset/assets.xml").write_text(IMYA_TWEAKABLE, encoding="utf-8")
            (mod / "modinfo.json").write_text(json.dumps({"ModID": name.lower()}), encoding="utf-8")
            return mod

        copy = tweakable(self.root / "library", "Tweakable")
        link = library.link(copy, self.mods).link
        own = tweakable(self.mods, "Mine")
        offered = imya.read(own).options().to_dict()
        self.window.loadout.layers = [
            ModLayer(origin="Tweakable", mod_id="tweakable", folder=str(link), offered=offered, options={IMYA_RADIUS: "30"}),
            ModLayer(origin="Mine", mod_id="mine", folder=str(own), offered=offered, options={IMYA_RADIUS: "35"}),
        ]
        asked = []
        with mock.patch.object(self.window.prompts, "confirm_live",
                               side_effect=lambda plan, launch, *_: asked.append(plan) or True):
            self.assertTrue(self.window.live.sync_loadout())
        (plan,) = asked
        self.assertEqual([layer.origin for layer in plan.tweaks], ["Tweakable"])
        self.assertEqual(plan.tweaks_held, ["Mine"])
        self.assertEqual(imya.read(copy).chosen(), {})
        self.assertEqual(imya.read(own).chosen(), {})
        made = library.target_of(link)
        self.assertTrue(generated.is_generated(made, copy.parent))
        self.assertEqual(generated.source_of(made), copy)
        self.assertEqual(generated.check(made), [])
        self.assertEqual(imya.read(link).chosen(), {IMYA_RADIUS: "30"})
        # Still the library's copy, to everything asking which mod it is.
        self.assertEqual(library.kind_of(link, copy.parent), library.COPY)
        self.assertIn("iModYourAnno settings for 1 mod into a generated copy",
                      self.window.status_label.text())

        # Settings back at the author's: the link goes back to the library's
        # copy, and the generated one is gone.
        self.window.loadout.layers[0].options = {}
        asked.clear()
        with mock.patch.object(self.window.prompts, "confirm_live",
                               side_effect=lambda plan, launch, *_: asked.append(plan) or True):
            self.assertTrue(self.window.live.sync_loadout())
        (plan,) = asked
        self.assertEqual(plan.reverting, ["Tweakable"])
        self.assertTrue(library.is_junction(link))
        self.assertEqual(library.target_of(link), copy)
        self.assertFalse(made.exists())


class TestTheLoadersLogAfterPlay(PlayingWindow):
    """Once the game has loaded its mods, what its loader wrote down is set
    beside the live loadout -- on the fourth card, and after Play in the
    status bar."""

    def log(self, offset: float, *loaded: tuple, complete: bool = True) -> None:
        import time

        from anno_modding_studio.core import loaderlog

        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + offset))
        lines = ["[%s.000] [info] Load mods" % stamp]
        for folder, mod_id in loaded:
            lines.append("[%s.100] [info] Load %s (1, %s) from mods\\%s" % (stamp, folder, mod_id, folder))
        if complete:
            lines.append("[%s.900] [info] Loading complete" % stamp)
        path = self.root / "mod-loader.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        patched = mock.patch.object(loaderlog, "log_file", return_value=path)
        patched.start()
        self.addCleanup(patched.stop)

    def make_live(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine")]
        from PySide6.QtWidgets import QMessageBox

        with mock.patch.object(QMessageBox, "information", staticmethod(lambda *a: None)):
            self.assertTrue(self.window.live.sync_loadout())

    def test_before_the_game_has_started_the_card_says_so(self):
        self.make_live()
        self.log(-3600, ("[Gameplay] Mine", "mine"))
        self.window.refresh_truth()
        self.assertIsNone(self.window.truth.log_check)
        self.assertIn("has not started since this was made live", self.window.source.truth_log.text())
        self.assertFalse(self.window.source.truth_log.isHidden())

    def test_after_it_has_the_card_says_what_it_loaded(self):
        self.make_live()
        self.log(60, ("[Gameplay] Mine", "mine"))
        self.window.refresh_truth()
        self.assertTrue(self.window.truth.log_check.ok)
        self.assertIn("it loaded 1 mod, this loadout's.", self.window.source.truth_log.text())

    def test_what_it_loaded_unasked_is_a_warning(self):
        self.make_live()
        self.log(60, ("[Gameplay] Mine", "mine"), ("[Gameplay] Sneaky", "sneaky"))
        self.window.refresh_truth()
        self.assertIn("1 mod it does not have on loaded: [Gameplay] Sneaky", self.window.source.truth_log.text())
        self.assertEqual(self.window.source.truth_log.property("tone"), "warn")

    def test_a_loadout_that_is_not_live_is_not_checked(self):
        self.make_live()
        self.log(60, ("[Gameplay] Mine", "mine"))
        self.window.loadout_path = self.root / "Other.annoload"
        self.window.refresh_truth()
        self.assertIsNone(self.window.truth.log_check)
        self.assertTrue(self.window.source.truth_log.isHidden())

    def test_after_play_it_watches_for_the_new_log_and_says_what_it_found(self):
        self.make_live()
        self.assertTrue(self.window.live.play())
        self.assertTrue(self.window.live.log_timer.isActive())
        self.assertIn("checked once the mods are in", self.window.status_label.text())
        # The last game's log, and then one still loading: not yet.
        self.log(-3600, ("[Gameplay] Mine", "mine"))
        self.assertFalse(self.window.live.look_for_the_log())
        self.log(60, ("[Gameplay] Mine", "mine"), complete=False)
        self.assertFalse(self.window.live.look_for_the_log())
        self.assertTrue(self.window.live.log_timer.isActive())
        # Loaded: said, and the watch is over.
        self.log(60, ("[Gameplay] Mine", "mine"))
        with mock.patch.object(installplan, "inventory", wraps=installplan.inventory) as asked:
            self.assertTrue(self.window.live.look_for_the_log())
        asked.assert_called_once()
        self.assertFalse(self.window.live.log_timer.isActive())
        self.assertTrue(self.window.status_label.text().startswith("When Play started the game"))


class TestWhereTheLastStartCameFrom(PlayingWindow):
    """A log is judged by where it came from: Play's own start, a start some
    other way with the folder as Play left it, or a folder changed since."""

    log = TestTheLoadersLogAfterPlay.log
    make_live = TestTheLoadersLogAfterPlay.make_live

    def test_after_make_this_live_a_start_is_one_from_outside_the_studio(self):
        self.make_live()
        self.log(60, ("[Gameplay] Mine", "mine"))
        self.window.refresh_truth()
        checked = self.window.truth.log_check
        self.assertIs(checked.by_play, False)
        self.assertFalse(checked.changed)
        self.assertTrue(checked.describe().startswith("When the game was started outside the Studio"))

    def test_after_play_a_start_within_minutes_is_plays(self):
        self.make_live()
        self.assertTrue(self.window.live.play())
        self.log(60, ("[Gameplay] Mine", "mine"))
        self.window.refresh_truth()
        self.assertIs(self.window.truth.log_check.by_play, True)

    def test_a_folder_changed_since_play_is_said_and_not_graded(self):
        self.make_live()
        self.assertTrue(self.window.live.play())
        install_mod(self.mods, "[Gameplay] Sneaky", "sneaky", "1")
        self.log(60, ("[Gameplay] Mine", "mine"), ("[Gameplay] Sneaky", "sneaky"))
        self.window.refresh_truth()
        checked = self.window.truth.log_check
        self.assertTrue(checked.changed)
        self.assertFalse(checked.fault)
        self.assertIn("changed outside the Studio since Play wrote it", self.window.source.truth_log.text())
        self.assertNotEqual(self.window.source.truth_log.property("tone"), "warn")


class TestWhatComesInOnAfterAnInstall(unittest.TestCase):
    """The folder read after an install: what just arrived comes in on, and a
    loadout's own switches are kept over the folder's."""

    def dialog(self, on, arrived=(), keep=None):
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        dialog = ModsFolderDialog.__new__(ModsFolderDialog)
        dialog.folder_switches = mock.Mock(is_on=lambda origin: origin in on)
        dialog.arrived = set(arrived)
        dialog.keep = dict(keep or {})
        dialog.library_dir = None
        dialog._links = {}
        return dialog

    def test_the_folder_says_unless_the_caller_knows_better(self):
        dialog = self.dialog(on={"Mine"})
        self.assertTrue(dialog._comes_in_on("Mine"))
        self.assertFalse(dialog._comes_in_on("New"))
        dialog = self.dialog(on={"Mine"}, arrived={"New"}, keep={"Mine": False})
        # Installing wrote no profile, so the new mod is off on disk -- and on here.
        self.assertTrue(dialog._comes_in_on("New"))
        self.assertTrue(dialog._comes_in_on("New/shared/Piece"))
        # This loadout has it off, whatever the live one says.
        self.assertFalse(dialog._comes_in_on("Mine"))

    def test_a_library_mod_with_no_link_comes_in_off_unless_it_just_arrived(self):
        """The game does not load it, whatever the folder's switches say."""
        dialog = self.dialog(on={"Shelved"})
        dialog._links = {"Shelved": Path("mods/Shelved")}
        self.assertFalse(dialog._comes_in_on("Shelved"))
        dialog.arrived = {"Shelved"}
        self.assertTrue(dialog._comes_in_on("Shelved"))

    def test_a_folder_the_loader_loads_nothing_from_is_not_read_in(self):
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            (mods / "data" / "config").mkdir(parents=True)
            (mods / "Real" / "data").mkdir(parents=True)
            dialog = ModsFolderDialog.__new__(ModsFolderDialog)
            dialog.game = ANNO_1800
            loose = mock.Mock(origin="data", folder=str(mods / "data"))
            real = mock.Mock(origin="Real", folder=str(mods / "Real"))
            self.assertTrue(dialog._inert(loose))
            self.assertFalse(dialog._inert(real))
            dialog.game = ANNO_117
            self.assertFalse(dialog._inert(loose))
