"""A loadout's profiles on screen: the picker on the loadout card, the fourth
card and Play by profile, the live record naming one, recents, and 117's
DisplayName; forking, collections, and a loadout caught up with a conversion. See
``docs/proposals/profiles-proposal.md``, phases 2 to 4."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import InstalledCollection, TruthWindow, install_mod  # noqa: E402

from anno_modding_studio.core import library  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117  # noqa: E402
from anno_modding_studio.ui.widgets import laid_out  # noqa: E402


class _Profiles(TruthWindow):
    """A saved loadout of two mods, with a second profile that has one off."""

    def setUp(self):
        super().setUp()
        for origin in ("[Gameplay] Mine", "[Gameplay] Other"):
            install_mod(self.mods, origin, origin.split()[-1].lower(), "1")
        loadout = self.window.loadout
        loadout.mods_folder = str(self.mods)
        loadout.set_up_as("manage")
        loadout.layers = [
            ModLayer(origin=origin, mod_id=origin.split()[-1].lower(), folder=str(self.mods / origin))
            for origin in ("[Gameplay] Mine", "[Gameplay] Other")
        ]
        loadout.rename_profile(loadout.profile, "Profile1")
        loadout.add_profile("Profile2").switches["[Gameplay] Other"] = False
        self.window.loadout_path = self.root / "Main.annoload"
        loadout.save(self.window.loadout_path)
        self.window.on_disk = True
        self.window.files.mark_saved()
        self.window.forget_undo()
        self.addCleanup(self.window.files.mark_saved)
        patched = mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def make_live(self):
        from PySide6.QtWidgets import QMessageBox

        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True), \
                mock.patch.object(QMessageBox, "information"), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.live.sync_loadout()

    def picker(self) -> list:
        box = self.window.source.profile_box
        return [box.itemText(i) for i in range(box.count()) if box.itemText(i)]

    def head(self) -> str:
        return self.window.source.truth_live.text().split("</b>")[0][len("<b>"):]


class TestThePicker(_Profiles):
    def test_it_lists_the_profiles_and_what_is_done_to_them(self):
        self.window.start.refresh_source_panel()
        self.assertFalse(self.window.source.profile_box.isHidden())
        self.assertEqual(self.picker(), [
            "Profile1", "Profile2", "New profile…", "Rename this profile…", "Delete this profile…",
            "Copy to new loadout…",
        ])
        self.assertEqual(self.window.source.profile_box.currentText(), "Profile1")

    def test_every_loadout_with_a_file_has_one(self):
        self.window.loadout.own.loads_last = False
        self.window.start.refresh_source_panel()
        self.assertFalse(self.window.source.profile_box.isHidden())
        self.window.loadout_path = None
        self.window.start.refresh_source_panel()
        self.assertTrue(self.window.source.profile_box.isHidden())

    def test_picking_one_chooses_it(self):
        self.window.start.refresh_source_panel()
        box = self.window.source.profile_box
        box.activated.emit(1)
        self.assertEqual(self.window.loadout.profile, "Profile2")
        self.assertFalse(self.window.loadout.layer("[Gameplay] Other").active)
        self.assertEqual(box.currentText(), "Profile2")

    def test_picking_an_act_asks_the_window_and_leaves_the_choice_alone(self):
        self.window.start.refresh_source_panel()
        box = self.window.source.profile_box
        with mock.patch.object(self.window.prompts, "ask_profile_name", return_value="") as asked:
            box.activated.emit(box.findText("New profile…"))
        asked.assert_called_once()
        self.assertEqual(box.currentText(), "Profile1")
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2"])

    def test_its_last_entry_is_a_separate_copy(self):
        """The other half of forking, beside New profile: the card's header
        has no Fork button of its own."""
        self.window.start.refresh_source_panel()
        box = self.window.source.profile_box
        with mock.patch.object(self.window.prompts, "ask_fork_kind") as asked, \
                mock.patch.object(self.window.prompts, "ask_fork_name", return_value="Apart"):
            box.activated.emit(box.findText("Copy to new loadout…"))
        asked.assert_not_called()
        self.assertEqual(self.window.loadout_path, self.root / "Apart.annoload")
        self.assertTrue(self.window.loadout_path.is_file())
        self.assertNotIn(self.window.source.fork_button, self._head_widgets())

    def test_its_acts_are_in_the_same_place_whichever_profile_is_chosen(self):
        """H2-18: opened over the box with the chosen profile under the pointer,
        the list put New profile… where Rename was a moment before."""
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

        from anno_modding_studio.ui import theme

        # Styled as the app is: the theme's style sheet is what says it.
        theme.apply(QApplication.instance(), theme.SCHEMES[theme.DEFAULT_SCHEME])
        self.addCleanup(_isolation.forget_styling)
        self.window.show()
        self.addCleanup(self.window.hide)
        self.window.start.refresh_source_panel()
        box = self.window.source.profile_box
        QApplication.processEvents()
        # Not the kind of list that opens over the box at the chosen row.
        option = QStyleOptionComboBox()
        box.initStyleOption(option)
        self.assertFalse(box.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup, option, box))
        # And every row in view, so none is scrolled away.
        self.assertEqual(box.maxVisibleItems(), box.count())

        def below_the_box() -> int:
            """How far below the box's foot New profile… is, opened."""
            QApplication.processEvents()
            box.showPopup()
            QApplication.processEvents()
            view = box.view()
            row = view.visualRect(view.model().index(box.findText("New profile…"), 0))
            spot = view.viewport().mapToGlobal(row.center()).y()
            foot = box.mapToGlobal(box.rect().bottomLeft()).y()
            box.hidePopup()
            QApplication.processEvents()
            return spot - foot

        first = below_the_box()
        self.window.live.choose_profile("Profile2")
        self.window.start.refresh_source_panel()
        self.assertEqual(box.currentText(), "Profile2")
        self.assertEqual(below_the_box(), first)
        # Clear of the box: below it, or above it on a screen with no room
        # below, as a menu goes.
        self.assertTrue(first > 0 or first < -box.height(), first)

    def _head_widgets(self) -> list:
        return laid_out(self.window.source.loadout_step._head_row)

    def test_the_last_one_cannot_be_offered_for_deletion(self):
        self.window.loadout.delete_profile("Profile2")
        self.window.start.refresh_source_panel()
        box = self.window.source.profile_box
        item = box.model().item(box.findText("Delete this profile…"))
        self.assertFalse(item.isEnabled())


class TestWhatCountsAsUnsaved(_Profiles):
    def test_choosing_a_profile_does_and_choosing_back_does_not(self):
        self.assertFalse(self.window.files.unsaved_work())
        self.window.live.choose_profile("Profile2")
        self.assertTrue(self.window.files.unsaved_work())
        self.window.live.choose_profile("Profile1")
        self.assertFalse(self.window.files.unsaved_work())

    def test_so_does_a_new_one_a_rename_and_a_deletion(self):
        for act in (
            lambda: self.window.live.new_profile("Profile3"),
            lambda: self.window.live.rename_profile("Everything"),
        ):
            act()
            self.assertTrue(self.window.files.unsaved_work())
            self.window.files.mark_saved()
        with mock.patch.object(self.window.prompts, "ask_delete_profile", return_value=True):
            self.assertTrue(self.window.live.delete_profile())
        self.assertTrue(self.window.files.unsaved_work())


    def test_opening_another_once_asked_does_not_ask_again(self):
        """Getting a collection asks about unsaved work before it fetches,
        then opens the live loadout.  It used to clear the unsaved mark so
        the opening would not ask a second time -- which a typed detail, read
        apart from the mark, still made it do."""
        self.window.loadout.own.description = "typed, never saved"
        self.window.live.choose_profile("Profile2")
        self.assertTrue(self.window.files.unsaved_work())
        other = self.root / "Other.annoload"
        Loadout(game=self.window.loadout.game).save(other)
        with mock.patch.object(self.window.prompts, "ask_save_first",
                               side_effect=AssertionError("asked twice")):
            self.assertTrue(self.window.load_loadout(other, asked=True))
        self.assertEqual(self.window.loadout_path, other)


class TestTheCardByProfile(_Profiles):
    def test_a_profile_not_live_says_what_playing_it_would_change(self):
        self.make_live()
        self.window.live.choose_profile("Profile2")
        self.assertEqual(self.window.source.truth_step.status.full_text(), "Playing Profile2 changes 1")
        self.assertEqual(self.head(), "Live: Main — Profile1")
        # Said to be the profile, not another loadout of that name (R3-33).
        self.assertIn("Play makes its profile Profile2 live, changing 1 mod",
                      self.window.source.truth_live.text())
        self.assertTrue(
            self.window.status_label.text().startswith("Working in Profile2. Play would make Profile2 what the game loads:")
        )

    def test_play_makes_the_chosen_one_live_and_records_which(self):
        self.window.live.choose_profile("Profile2")
        self.make_live()
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Profile2")
        self.assertTrue(self.window.truth.is_live)
        self.window.refresh_truth()
        self.assertEqual(self.head(), "Live: this loadout (Profile2)")
        self.assertTrue((self.mods / "-[Gameplay] Other").is_dir())
        self.window.live.choose_profile("Profile1")
        self.assertFalse(self.window.truth.is_live)
        self.assertEqual(self.head(), "Live: Main — Profile2")

    def test_a_record_from_before_profiles_means_the_first(self):
        library.set_live(self.window.where.library_dir(), self.window.loadout_path)
        self.window.refresh_truth()
        self.assertTrue(self.window.truth.is_live)
        self.window.live.choose_profile("Profile2")
        self.assertFalse(self.window.truth.is_live)

    def test_a_loadout_of_one_profile_reads_as_it_did(self):
        self.window.loadout.delete_profile("Profile2")
        self.make_live()
        self.window.refresh_truth()
        self.assertEqual(self.head(), "Live: this loadout")
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 2 outside the library")


class TestRenamingAndDeletingTheLiveOne(_Profiles):
    def setUp(self):
        super().setUp()
        self.make_live()

    def test_the_record_follows_a_rename_once_it_is_saved(self):
        self.window.live.rename_profile("Everything")
        # The file still says Profile1 until it is saved, and so does the
        # record -- which is still this profile, by its new name here.
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Profile1")
        self.assertEqual(self.window.live.live_profile_here(), "Everything")
        self.window.refresh_truth()
        self.assertTrue(self.window.truth.is_live)
        self.assertTrue(self.window.files.save_loadout())
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Everything")
        self.assertEqual(Loadout.load(self.window.loadout_path).profile_names(), ["Everything", "Profile2"])

    def test_two_names_swapped_before_a_save_keep_it_on_the_same_profile(self):
        """The record is looked up once among the renames: following each in
        turn moved it to Profile2 and then back to Profile1, the other one."""
        self.window.live.rename_profile("Tmp")
        self.window.live.choose_profile("Profile2")
        self.window.live.rename_profile("Profile1")
        self.window.live.choose_profile("Tmp")
        self.window.live.rename_profile("Profile2")
        self.assertEqual(self.window.profile_renames, {"Profile1": "Profile2", "Profile2": "Profile1"})
        self.assertEqual(self.window.live.live_profile_here(), "Profile2")
        self.assertTrue(self.window.files.save_loadout())
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Profile2")
        self.assertEqual(self.window.live.live_profile_here(), "Profile2")

    def test_a_rename_not_kept_leaves_it_naming_what_the_file_does(self):
        self.window.live.rename_profile("Everything")
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.window.load_loadout(self.window.loadout_path)
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2"])
        self.assertEqual(self.window.live.live_profile_here(), "Profile1")

    def test_play_before_saving_a_rename_records_the_name_the_file_has(self):
        self.window.live.rename_profile("Everything")
        self.make_live()
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Profile1")
        self.window.files.save_loadout()
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Everything")

    def test_the_live_one_cannot_be_deleted(self):
        from anno_modding_studio.ui import live_controller

        with mock.patch.object(live_controller, "tell") as told, \
                mock.patch.object(self.window.prompts, "ask_delete_profile") as asked:
            self.assertFalse(self.window.live.delete_profile())
        told.assert_called_once()
        asked.assert_not_called()
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2"])

    def test_another_one_can(self):
        self.window.live.choose_profile("Profile2")
        with mock.patch.object(self.window.prompts, "ask_delete_profile", return_value=True):
            self.assertTrue(self.window.live.delete_profile())
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1"])
        self.assertTrue(self.window.loadout.layer("[Gameplay] Other").active)
        self.assertTrue(self.window.truth.is_live)


class TestMakingAndNaming(_Profiles):
    def test_a_new_profile_is_switched_as_the_chosen_one_and_chosen(self):
        self.window.live.choose_profile("Profile2")
        self.assertTrue(self.window.live.new_profile("Profile3"))
        self.assertEqual(self.window.loadout.profile, "Profile3")
        self.assertFalse(self.window.loadout.layer("[Gameplay] Other").active)
        self.assertIn("switched as Profile2 was", self.window.status_label.text())

    def test_it_is_offered_a_name_nobody_has(self):
        with mock.patch.object(self.window.prompts, "ask_profile_name", return_value="") as asked:
            self.window.live.new_profile()
        self.assertEqual(asked.call_args[0][2], "Profile 3")

    def test_a_name_taken_is_said_and_nothing_is_made(self):
        from PySide6.QtWidgets import QMessageBox

        with mock.patch.object(QMessageBox, "warning") as warned:
            self.assertFalse(self.window.live.new_profile("profile2"))
        warned.assert_called_once()
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2"])

    def test_a_new_loadouts_first_profile_is_main_not_the_loadouts_name(self):
        self.window.reset_loadout()
        self.window.loadout.set_up_as("manage")
        self.window.loadout_path = self.root / "Fresh.annoload"
        self.window.live.new_profile("Try")
        self.assertEqual(self.window.loadout.profile_names(), ["Main", "Try"])


class TestUndoSaysWhere(_Profiles):
    def test_undoing_a_switch_made_in_another_profile_says_which(self):
        self.window.loadout.layer("[Gameplay] Mine").active = False
        self.window.record()
        self.window.live.choose_profile("Profile2")
        self.window.undo_last()
        self.assertTrue(self.window.status_label.text().endswith(", in Profile1."))
        self.assertTrue(self.window.loadout.switches_of("Profile1")["[Gameplay] Mine"])


class TestRecents(_Profiles):
    def test_the_live_loadout_says_which_profile(self):
        self.window.live.choose_profile("Profile2")
        self.make_live()
        self.window.files.save_loadout()
        other = self.root / "Other.annoload"
        Loadout(game=self.game).save(other)
        self.window.loadout_path = other
        # The recents list reads the live record of each loadout's game, in
        # the window's library.
        with mock.patch.object(self.window.game_files, "open_game", return_value=self.game):
            self.window.start.refresh_source_panel()
        rows = [self.window.source.recent_list.item(i).text()
                for i in range(self.window.source.recent_list.count())]
        self.assertIn("Main: what the game loads (Profile2)", rows)


class TestAnno117sDisplayName(TruthWindow):
    game = ANNO_117

    def setUp(self):
        super().setUp()
        (self.documents / "profiles").mkdir()
        install_mod(self.mods, "Mine", "mine", "1")
        loadout = self.window.loadout
        loadout.mods_folder = str(self.mods)
        loadout.set_up_as("manage")
        loadout.layers = [ModLayer(origin="Mine", mod_id="mine")]
        self.window.loadout_path = self.root / "Roman Set.annoload"
        self.addCleanup(self.window.files.mark_saved)

    def written(self) -> str:
        from PySide6.QtWidgets import QMessageBox

        with mock.patch.object(self.window.prompts, "confirm_live", return_value=True), \
                mock.patch.object(QMessageBox, "information"), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.live.sync_loadout()
        return (self.documents / "profiles" / "Anno Modding Studio.txt").read_text(encoding="utf-8")

    def test_it_names_the_profile_when_there_are_several(self):
        self.window.loadout.rename_profile(self.window.loadout.profile, "Roman Set")
        self.window.loadout.add_profile("Legion")
        self.window.live.choose_profile("Legion")
        self.assertIn("DisplayName Roman Set — Legion\n", self.written())

    def test_and_the_loadout_alone_when_there_is_one(self):
        self.assertIn("DisplayName Roman Set\n", self.written())


class TestASwitchIsUnsaved(_Profiles):
    def test_flipped_it_is_and_flipped_back_it_is_not(self):
        layer = self.window.loadout.layer("[Gameplay] Other")
        layer.active = False
        self.window.on_activation_changed()
        self.assertTrue(self.window.files.unsaved_work())
        layer.active = True
        self.window.on_activation_changed()
        self.assertFalse(self.window.files.unsaved_work())

    def test_so_is_one_in_a_profile_not_chosen(self):
        self.window.live.choose_profile("Profile2")
        self.window.loadout.layer("[Gameplay] Mine").active = False
        self.window.live.choose_profile("Profile1")
        self.assertTrue(self.window.files.unsaved_work())


# -- phase 3 ------------------------------------------------------------------
class _FolderRead:
    """Stands in for the mods folder dialog: every folder in the mods folder,
    on as ``disk`` says unless ``keep`` says otherwise, and what arrived on."""

    disk: ClassVar[dict] = {}
    detour = None

    def __init__(self, index, parent=None, start_dir="", game="", focus=None,
                 provenance=None, arrived=None, keep=None, listed=None,
                 base_fingerprint="", base_update="", library_dir=None, picked=None,
                 adding=None, shelf=None, standing=None, detours=()):
        from anno_modding_studio.core.changes import Change

        arrived, keep = set(arrived or ()), dict(keep or {})
        self.folder_box = mock.Mock()
        self.folder_box.text.return_value = start_dir
        self.ops, self.unread, self.layers = [], [], []
        for folder in sorted(Path(start_dir).iterdir()):
            origin = folder.name
            on = keep.get(origin, origin in arrived or self.disk.get(origin, True))
            self.layers.append(ModLayer(origin=origin, mod_id=origin.split()[-1].lower(),
                                        folder=str(folder), active=on))
        self.changes = [Change(guid="1", asset_label="Farm", path="/Values/X", tag="X",
                               old_value="1", new_value="2", origin=self.layers[0].origin)]

    def reload(self):
        pass

    def exec(self):
        return 1


class _Reading(_Profiles):
    def setUp(self):
        super().setUp()
        self.window.settings.setValue(self.window.mods_key("last_mods_folder"), str(self.mods))
        _FolderRead.disk = {}
        for patched in (
            mock.patch("anno_modding_studio.ui.main_window.ModsFolderDialog", _FolderRead),
            mock.patch.object(self.window.start, "refresh_source_panel"),
        ):
            patched.start()
            self.addCleanup(patched.stop)


class TestReadingTheFolderKeepsEveryProfile(_Reading):
    def test_the_folders_switches_go_to_the_live_profile_not_the_chosen_one(self):
        self.make_live()  # Profile1: both on
        self.window.live.choose_profile("Profile2")  # Other off
        install_mod(self.mods, "[Gameplay] New", "new", "1")
        _FolderRead.disk = {"[Gameplay] Mine": False}
        self.assertTrue(self.window.load_installed_mods())
        loadout = self.window.loadout
        self.assertEqual(loadout.switches_of("Profile1"),
                         {"[Gameplay] Mine": False, "[Gameplay] Other": True, "[Gameplay] New": True})
        self.assertEqual(loadout.switches_of("Profile2"),
                         {"[Gameplay] Mine": True, "[Gameplay] Other": False, "[Gameplay] New": True})

    def test_with_the_chosen_one_live_the_folder_is_its_say_and_the_rest_keep_theirs(self):
        self.make_live()
        _FolderRead.disk = {"[Gameplay] Mine": False}
        self.window.load_installed_mods()
        loadout = self.window.loadout
        self.assertFalse(loadout.layer("[Gameplay] Mine").active)
        self.assertEqual(loadout.switches_of("Profile2"), {"[Gameplay] Mine": True, "[Gameplay] Other": False})


class TestForkingAsAProfile(_Profiles):
    def test_the_first_answer_is_a_new_profile_of_this_loadout(self):
        shown = self.window.live.profile_shown()
        with mock.patch.object(self.window.prompts, "ask_fork_kind", return_value="profile") as asked:
            self.assertTrue(self.window.files.fork_loadout("Try"))
        asked.assert_called_once_with("Main", shown)
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2", "Try"])
        self.assertEqual(self.window.loadout_path, self.root / "Main.annoload")
        self.assertFalse((self.root / "Try.annoload").exists())

    def test_saying_neither_forks_nothing(self):
        with mock.patch.object(self.window.prompts, "ask_fork_kind", return_value=""):
            self.assertFalse(self.window.files.fork_loadout())
        self.assertEqual(self.window.loadout.profile_names(), ["Profile1", "Profile2"])

    def test_a_separate_copy_names_the_profile_it_was_named_after_for_itself(self):
        self.window.loadout.rename_profile("Profile1", "Main")
        self.window.loadout.save(self.window.loadout_path)
        self.assertTrue(self.window.files.fork_loadout("Fork", kind="separate"))
        self.assertEqual(Loadout.load(self.root / "Fork.annoload").profile_names(), ["Fork", "Profile2"])
        self.assertEqual(Loadout.load(self.root / "Main.annoload").profile_names(), ["Main", "Profile2"])


class TestACollectionAsAProfile(_Reading):
    def setUp(self):
        super().setUp()
        self.make_live()
        self.window.files.save_loadout()
        install_mod(self.mods, "[Gameplay] New", "new", "1")
        self.window.loadout.layer("[Gameplay] Other").active = False
        self.window.loadout.find_profile("Profile2").switches["[Gameplay] Other"] = True
        self.window.files.save_loadout()

    def start(self, answer="profile"):

        dialog = InstalledCollection(self.mods, "Jakob's", "[Gameplay] New", "[Gameplay] Other")
        dialog.brought = ["[Gameplay] New"]
        with mock.patch.object(self.window.prompts, "ask_start_from", return_value=answer) as asked, \
                mock.patch.object(self.window.modio, "pick_a_collection", return_value=dialog):
            done = self.window.start.choose_aim("manage", collection=True)
        asked.assert_called_once_with("Main")
        return done

    def test_its_mods_are_on_in_a_new_profile_and_off_in_the_rest(self):
        self.start()
        loadout = self.window.loadout
        self.assertEqual(self.window.loadout_path, self.root / "Main.annoload")
        self.assertEqual(loadout.profile_names(), ["Profile1", "Profile2", "Jakob's"])
        self.assertEqual(loadout.profile, "Jakob's")
        # Switched as the live one is, with every mod the collection lists on
        # -- the one you had, which the live profile has off, included.
        self.assertEqual(loadout.switches_of("Jakob's"),
                         {"[Gameplay] Mine": True, "[Gameplay] Other": True, "[Gameplay] New": True})
        self.assertEqual(loadout.switches_of("Profile1"),
                         {"[Gameplay] Mine": True, "[Gameplay] Other": False, "[Gameplay] New": False})
        self.assertFalse(loadout.switches_of("Profile2")["[Gameplay] New"])
        self.assertEqual(loadout.layer("[Gameplay] New").from_collection, "Jakob's")
        self.assertEqual(loadout.listed_in("[Gameplay] Other"), ["Jakob's"])
        # Nothing the game loads has moved: Profile1 is still the live one.
        self.assertEqual(library.live_profile(self.window.where.library_dir()), "Profile1")
        self.assertIn("Jakob's is a new profile of Main", self.window.status_label.text())
        # Added to the loadout you play, so not a blank page: unsaved.
        self.assertTrue(self.window.files.unsaved_work())

    def test_from_another_loadout_the_live_loadout_is_opened_for_it(self):
        other = self.root / "Other.annoload"
        Loadout(game=self.game).save(other)
        self.window.load_loadout(other)
        self.start()
        self.assertEqual(self.window.loadout_path, self.root / "Main.annoload")
        self.assertIn("Jakob's", self.window.loadout.profile_names())


class TestTakingACollectionOut(_Reading):
    def setUp(self):
        super().setUp()
        install_mod(self.mods, "[Gameplay] New", "new", "1")
        linked = library.adopt(self.window.where.library_dir(), self.mods / "[Gameplay] New")
        self.copy = library.target_of(linked.link)
        loadout = self.window.loadout
        loadout.note_collection("Jakob's", ["[Gameplay] New", "[Gameplay] Other"], brought=["[Gameplay] New"])
        loadout.set_layer(ModLayer(origin="[Gameplay] New", mod_id="new", folder=str(linked.link)))
        self.window.files.save_loadout()
        self.window.forget_undo()

    def take_out(self, delete: bool):
        with mock.patch.object(self.window.prompts, "ask_take_out", return_value=True) as asked, \
                mock.patch.object(self.window.prompts, "ask_delete_copies", return_value=delete) as offered:
            self.assertTrue(self.window.modio.take_collection_out("Jakob's"))
        return asked, offered

    def test_what_it_brought_leaves_every_profile_and_yours_stay(self):
        asked, _offered = self.take_out(delete=False)
        self.assertEqual(asked.call_args[0][1:3], (["[Gameplay] New"], ["[Gameplay] Other"]))
        loadout = self.window.loadout
        self.assertIsNone(loadout.layer("[Gameplay] New"))
        self.assertIsNotNone(loadout.layer("[Gameplay] Other"))
        self.assertEqual((loadout.provenance, loadout.listed), ({}, {}))
        for name in loadout.profile_names():
            self.assertNotIn("[Gameplay] New", loadout.switches_of(name))
        # Kept, not deleted, when you say no.
        self.assertTrue(self.copy.is_dir())
        self.assertIn("Their library copies are kept", self.window.status_label.text())

    def test_its_library_copy_nothing_else_uses_is_offered_and_deleted(self):
        _asked, offered = self.take_out(delete=True)
        self.assertEqual(offered.call_args[0][1], ["[Gameplay] New"])
        self.assertFalse(self.copy.exists())
        self.assertFalse((self.mods / "[Gameplay] New").exists())
        self.assertIn("Deleted 1 library copy", self.window.status_label.text())

    def test_while_the_game_runs_its_copies_are_kept_unasked(self):
        """The mods leave the loadout; deleting what the game loaded waits."""
        with mock.patch("anno_modding_studio.core.launch.running", return_value=True):
            _asked, offered = self.take_out(delete=True)
        offered.assert_not_called()
        self.assertIsNone(self.window.loadout.layer("[Gameplay] New"))
        self.assertTrue(self.copy.is_dir())
        self.assertIn("Their library copies are kept", self.window.status_label.text())

    def test_one_another_loadout_uses_is_not_offered(self):
        other = Loadout(game=self.game)
        other.layers = [ModLayer(origin="[Gameplay] New", mod_id="new")]
        other.save(self.root / "Other.annoload")
        _asked, offered = self.take_out(delete=True)
        offered.assert_not_called()
        self.assertTrue(self.copy.is_dir())

    def test_one_another_games_loadout_uses_is_offered(self):
        other = Loadout(game=ANNO_117 if self.game != ANNO_117 else "anno1800")
        other.layers = [ModLayer(origin="[Gameplay] New", mod_id="new")]
        other.save(self.root / "Other.annoload")
        _asked, offered = self.take_out(delete=True)
        self.assertEqual(offered.call_args[0][1], ["[Gameplay] New"])

    def test_this_windows_mods_count_before_they_are_saved(self):
        """The loadout open here is asked as it stands, not as its file has it."""
        self.window.loadout.set_layer(ModLayer(origin="[Gameplay] Unsaved", mod_id="unsaved"))
        mentioned = self.window.library_controller.mentioned_keys(excluding=self.window.loadout_path)
        self.assertLessEqual({"unsaved", "[gameplay] unsaved", "new"}, mentioned)

    def test_it_is_one_step_to_undo(self):
        self.take_out(delete=False)
        self.window.undo_last()
        self.assertIsNotNone(self.window.loadout.layer("[Gameplay] New"))


# -- phase 4 ------------------------------------------------------------------
class TestCaughtUpOnOpening(_Profiles):
    """A loadout the conversion moved the mods of, opened afterwards."""

    def test_a_loadout_whose_file_still_names_the_folders_the_conversion_moved_is_caught_up(self):
        """Converted before the conversion saved it, and closed: opened, it
        is brought up to where its mods are now, and saved."""
        record = self.window.where.library_dir() / "conversions" / "2026-09-26_18-08-31.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        old = str(self.mods / "[Gameplay] Other")
        new = str(self.mods / "[Gameplay] Other (linked)")
        record.write_text(json.dumps({"journal": {
            "loadout": str(self.window.loadout_path), "paths": {old: new},
        }}), encoding="utf-8")
        self.window.load_loadout(self.window.loadout_path)
        self.assertEqual(self.window.loadout.layer("[Gameplay] Other").folder, new)
        saved = Loadout.load(self.window.loadout_path)
        self.assertEqual(saved.layer("[Gameplay] Other").folder, new)
        self.assertIn("1 of this loadout's mods", self.window.status_label.text())
        self.assertFalse(self.window.files.unsaved_work())



class TestAListPatchIsNamedAfterItsLoadout(TruthWindow):
    """The patch over a loadout's whole list is that loadout's own mod, and
    so is named after it: two loadouts' patches named alike built into one
    folder under one ModID, and the second one's Play was stopped."""

    def setUp(self):
        super().setUp()
        loadout = self.window.loadout
        loadout.set_up_as("manage")
        loadout.make_own_mod()
        patched = mock.patch.object(self.window.files, "loadouts_dir", return_value=self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.addCleanup(self.window.files.mark_saved)

    def test_a_new_loadouts_patch_takes_its_name(self):
        self.window.files.name_loadout("Main")
        self.assertEqual(self.window.loadout.own.name, "Main patch")

    def test_two_loadouts_patches_are_two_mods(self):
        from anno_modding_studio.core import modinfo

        self.window.files.name_loadout("Anno 1800 loadout")
        (self.root / "Anno 1800 loadout.annoload").write_text("{}", encoding="utf-8")
        first = (self.window.loadout.own.name, modinfo.own_mod_id(self.window.loadout))
        self.window.loadout.own.name = "My Anno Mod"
        self.window.loadout_path = None
        self.window.files.name_loadout("Anno 1800 loadout")
        second = (self.window.loadout.own.name, modinfo.own_mod_id(self.window.loadout))
        self.assertEqual(second[0], "Anno 1800 loadout (2) patch")
        self.assertNotEqual(first[1], second[1])

    def test_a_rename_takes_it_along(self):
        self.window.files.name_loadout("Main")
        self.window.files.rename_loadout("Coop run")
        self.assertEqual(self.window.loadout.own.name, "Coop run patch")

    def test_a_name_you_gave_it_stays(self):
        self.window.files.name_loadout("Main")
        self.window.loadout.own.name = "Harbour fixes"
        self.window.files.rename_loadout("Coop run")
        self.assertEqual(self.window.loadout.own.name, "Harbour fixes")

    def test_saved_its_card_is_no_longer_new(self):
        """R3-7, hands-on run 3: after Ctrl+S the card still said "Coop (new)"."""
        self.window.files.name_loadout("Coop")
        self.window.start.refresh_source_panel()
        self.assertEqual(self.window.source._loadout_status[0], "Coop (new)")
        self.assertTrue(self.window.files.save_loadout())
        self.assertEqual(self.window.source._loadout_status[0], "Coop")

    def test_the_rename_box_says_the_patch_follows_where_it_does(self):
        """R3-47, hands-on run 3: it said no mod inside was renamed, and the
        patch over the list took the new name."""
        from unittest import mock

        self.window.files.name_loadout("Main")
        asked = mock.patch.object(self.window.prompts, "ask_loadout_name", return_value=("", False))
        with asked as box:
            self.window.files.ask_rename_loadout()
        self.assertTrue(box.call_args.kwargs["patch_follows"])
        self.window.loadout.own.name = "Harbour fixes"
        with asked as box:
            self.window.files.ask_rename_loadout()
        self.assertFalse(box.call_args.kwargs["patch_follows"])

    def test_a_forks_patch_is_named_after_the_fork(self):
        self.window.files.name_loadout("Main")
        self.window.loadout.own_mod_anew("Coop run", came_from="Main")
        self.assertEqual(self.window.loadout.own.name, "Coop run patch")

    def test_a_loadout_that_is_its_mod_is_left_to_its_own_naming(self):
        self.window.loadout.set_up_as("make")
        self.window.loadout.own.name = "Harbour Tweaks"
        self.window.files.name_loadout("Harbour Tweaks")
        self.assertEqual(self.window.loadout.own.name, "Harbour Tweaks")


if __name__ == "__main__":
    unittest.main()
