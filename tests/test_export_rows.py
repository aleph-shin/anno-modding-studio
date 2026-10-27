"""The Export tab's In your game row, driven offscreen: what it says of the
mod picked, from the files cache, and where its button takes you.

The cache and the library are stood in for: which loadouts have a project
is :mod:`core.filecache`'s to answer, and is tested there.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402
from support import PlayingWindow, SessionCase, install_mod, modops_file  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    adding,
    library,
    loadercheck,
    places,
    projects,
    truth,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    AIM_MANAGE,
    Change,
    Loadout,
    ModLayer,
    own_record,
)
from anno_modding_studio.ui import output_panel, widgets  # noqa: E402
from anno_modding_studio.ui.add_to_loadout_dialog import (  # noqa: E402
    AddToLoadoutDialog,
)
from anno_modding_studio.ui.output_panel import OutputPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])

ALPHA = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>8</Radius></ModOp>'


class _Where:
    def __init__(self, library_dir: Path, mods_folder: str = ""):
        self._library = library_dir
        self._mods = mods_folder

    def library_dir(self, game: str = "") -> Path:
        return self._library

    def mods_folder(self) -> str:
        return self._mods


class TestInYourGame(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.loadouts = self.root / "loadouts"
        self.loadouts.mkdir()
        self.here = self.loadouts / "Harbours.annoload"
        self.main = self.loadouts / "Main.annoload"
        self.test = self.loadouts / "Test.annoload"
        self.loadout = Loadout(game="anno1800", own=own_record(name="Better Harbours", version="1.1"))
        self.loadout.set_up_as(AIM_MAKE)
        self.loadout.make_own_mod()
        self.loadout.set_change(Change(guid="1", asset_label="Fish", path="Cost", tag="Value", new_value="2"))
        self.loadout.projects[""] = projects.Ref(str(self.root / "projects" / "Better Harbours.annoproj"))
        self.panel = OutputPanel(self.loadout)
        self.panel.where = _Where(self.library)
        self.panel.loadout_path = lambda: self.here
        # This game's other loadouts, and which of them have the mod.
        self.others = [self.main, self.test]
        self.having = []
        for name, answer in (("loadouts", lambda *a, **k: list(self.others)),
                             ("loadouts_with_project", lambda *a, **k: list(self.having))):
            patcher = mock.patch.object(output_panel.filecache, name, answer)
            patcher.start()
            self.addCleanup(patcher.stop)

    def shown(self):
        self.panel.refresh()
        return self.panel.game_status.text()

    def test_a_mod_in_no_other_loadout_says_so_and_is_the_step_to_take(self):
        self.assertEqual(self.shown(), "In no loadout but this one.")
        button = self.panel.game_button
        self.assertEqual(button.text(), "Add to loadout\u2026")
        self.assertEqual(button.objectName(), "Primary")
        self.assertNotEqual(self.panel.export_button.objectName(), "Primary")

    def test_the_loadouts_that_have_it_are_named(self):
        self.having = [self.main, self.test]
        self.assertEqual(self.shown(), "In Main and Test.")

    def test_and_which_of_them_the_game_loads(self):
        self.having = [self.main, self.test]
        library.set_live(self.library, self.main)
        self.assertEqual(self.shown(), "In Main and Test. Main is what the game loads.")
        self.having = [self.main]
        self.assertEqual(self.shown(), "In Main, which is what the game loads.")

    def test_the_dialog_lists_the_others_ticked_where_they_have_it(self):
        self.having = [self.test]
        library.set_live(self.library, self.main)
        rows = self.panel.loadout_rows("")
        self.assertEqual([(row.name, row.has, row.live) for row in rows],
                         [("Main", False, True), ("Test", True, False)])
        dialog = AddToLoadoutDialog("Better Harbours", rows)
        self.assertEqual(dialog.ticked(), {self.test})
        self.assertEqual(dialog.table.item(0, 1).text(), "what the game loads now")

    def test_ticking_and_unticking_adds_and_takes_out(self):
        self.having = [self.test]
        asked = []

        def pick(dialog):
            dialog.tick(self.main)
            dialog.tick(self.test, False)
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(AddToLoadoutDialog, "exec", pick), \
                mock.patch.object(OutputPanel, "change_loadouts",
                                  lambda panel, origin, to, out: asked.append((origin, to, out)) or True):
            self.assertTrue(self.panel.add_to_loadout())
        self.assertEqual(asked, [("", [self.main], [self.test])])

    def test_one_that_has_it_from_its_folder_is_said(self):
        with mock.patch.object(output_panel.filecache, "loadouts_naming",
                               lambda *a, **k: [self.main]):
            self.assertEqual(self.shown(), "In no loadout but this one. Main has it from its "
                                           "folder, not from its project.")
            (main, test) = self.panel.loadout_rows("")
        self.assertTrue(main.from_folder and not main.has)
        self.assertFalse(test.from_folder)
        dialog = AddToLoadoutDialog("Better Harbours", [main])
        self.assertEqual(dialog.table.item(0, 1).text(),
                         "has it from its folder, and reads it from its project once ticked")

    def test_with_no_loadout_to_play_the_dialog_offers_what_i_play_now(self):
        self.others = []
        dialog = AddToLoadoutDialog("Better Harbours", self.panel.loadout_rows(""), offer_play_now=True)
        self.assertEqual(dialog.ticked(), set())
        self.assertIn("None of your loadouts is a set of mods you play.", " ".join(
            label.text() for label in dialog.findChildren(QLabel)))
        dialog.play_now.click()
        self.assertEqual(dialog.result(), AddToLoadoutDialog.PLAY_NOW)

    def test_with_one_it_is_not_offered(self):
        dialog = AddToLoadoutDialog("Better Harbours", self.panel.loadout_rows(""))
        self.assertIsNone(dialog.play_now)

    def test_without_a_mods_folder_nothing_is_done(self):
        said = []
        with mock.patch.object(QMessageBox, "information",
                               staticmethod(lambda *a, **k: said.append(a[2]))):
            self.assertFalse(self.panel.change_loadouts("", [self.main], []))
        self.assertIn("does not know where", said[0])

    def test_the_own_mod_of_a_loadout_made_to_play_offers_to_make_it_live(self):
        self.loadout.set_up_as(AIM_MANAGE)
        self.assertEqual(self.shown(), "This loadout is not what the game loads now.")
        button = self.panel.game_button
        self.assertEqual(button.text(), "Make it live…")
        self.assertNotEqual(button.objectName(), "Primary")
        asked = []
        self.panel.makeLiveRequested.connect(lambda: asked.append(True))
        button.click()
        self.assertEqual(asked, [True])

    def test_and_once_it_is_live_says_so_with_nothing_to_press(self):
        self.loadout.set_up_as(AIM_MANAGE)
        library.set_live(self.library, self.here)
        self.assertEqual(self.shown(), "This loadout is what the game loads.")
        self.assertFalse(self.panel.game_button.isVisibleTo(self.panel))

    def test_the_own_mod_beside_a_mod_carried_on_stays_in_its_loadout(self):
        self.loadout.layers.append(ModLayer(origin="Harbours", mod_id="harbours"))
        self.loadout.set_up_as("")
        self.loadout.home = "Harbours"
        self.panel.refresh()
        self.panel.mod_box.setCurrentIndex(self.panel.mod_box.findData(""))
        self.assertEqual(self.shown(), "Only in this loadout: made for its list.")
        self.assertFalse(self.panel.game_button.isVisibleTo(self.panel))

    def test_a_mods_folder_points_at_add_to_loadout(self):
        mods = self.root / "Anno 1800" / "mods"
        mods.mkdir(parents=True)
        said = []
        with mock.patch.object(QMessageBox, "information",
                               staticmethod(lambda *a, **k: said.append(a[2]))):
            self.assertFalse(self.panel.choose_destination(mods))
        self.assertIn("Add to loadout", said[0])


class TestAddingFromTheTab(SessionCase):
    """End to end, through the files: the loadout saved, the mod built
    into the library, and the other loadout written."""

    NAME = "[Gameplay] Harbours"

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self.root = places.loadouts_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        top = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, top, True)
        self.mods_folder = top / "mods"
        (self.mods_folder / "a" / "data/base/config/export").mkdir(parents=True)
        (self.mods_folder / "a" / "data/base/config/export/assets.xml").write_text(
            modops_file(ALPHA), encoding="utf-8")
        self.library = top / "library"
        self.library.mkdir()
        self.here = self.root / "Harbours.annoload"
        mine = Loadout(game="anno1800", own=own_record(name="Harbours", category="Gameplay"))
        mine.set_up_as(AIM_MAKE)
        mine.make_own_mod()
        mine.set_change(Change(guid="1000", asset_label="alpha", path="/Values/Building/Radius",
                               tag="Radius", old_value="10", new_value="12", authored=True))
        self.mine = mine
        _session, played = loadercheck.read_folders(
            [self.mods_folder / "a"], [self.mods_folder], self.index, "anno1800")
        self.main = self.root / "Main.annoload"
        played.save(self.main)
        self.panel = OutputPanel(mine)
        self.panel.where = _Where(self.library, str(self.mods_folder))
        self.panel.loadout_path = lambda: self.here
        self.saved = []
        self.panel.saveRequested.connect(lambda: self.saved.append(mine.save(self.here)))
        self.said = []
        # What the tab says when it is done, or why it stopped, in words.
        patcher = mock.patch.object(
            output_panel, "tell",
            lambda _parent, _title, text, **_how: self.said.append(widgets.plain_text(text)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(QMessageBox, "information",
                                    staticmethod(lambda *a, **k: self.said.append(a[2])))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_live(self):
        target = Loadout.load(self.main)
        found = truth.read(target, self.mods_folder, library_dir=self.library)
        library.set_live(self.library, self.main, fingerprint=truth.fingerprint(found))

    def test_added_it_is_saved_first_built_and_not_linked(self):
        self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.assertEqual(len(self.saved), 1)
        self.assertTrue((self.library / self.NAME).is_dir())
        self.assertFalse((self.mods_folder / self.NAME).exists())
        opened = Loadout.load(self.main)
        self.assertEqual(opened.held(self.NAME), projects.MOVED)
        self.assertIn("Added Harbours to Main.", self.said[0])

    def test_the_loadout_the_game_loads_gets_it_at_once_if_you_agree(self):
        self.make_live()
        with _isolation.answering("Switch on") as asked:
            self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.assertEqual(asked.titles, ["Switch the mod in the game?"])
        self.assertIn("Switch Harbours on in the game now?", asked.texts[0])
        self.assertTrue(library.is_link(self.mods_folder / self.NAME))
        self.assertIn("Switched on in the game.", self.said[0])
        with _isolation.answering(True) as asked:
            self.assertTrue(self.panel.change_loadouts("", [], [self.main]))
        self.assertEqual(asked.titles, ["Take the mod out?", "Switch the mod in the game?"])
        self.assertIn("Take Harbours out of Main?", asked.texts[0])
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertIsNone(Loadout.load(self.main).layer(self.NAME))

    def test_not_now_leaves_the_game_as_it_was(self):
        self.make_live()
        with _isolation.answering(False):
            self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertIn("until Main is next played", self.said[0])

    def test_one_never_switched_on_is_taken_out_with_no_question_about_the_game(self):
        """R3-3, hands-on run 3: asked to switch it off in the game, where it
        had never been switched on."""
        self.make_live()
        with _isolation.answering(False):
            self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        with _isolation.answering(True) as asked:
            self.assertTrue(self.panel.change_loadouts("", [], [self.main]))
        self.assertEqual(asked.titles, ["Take the mod out?"])
        self.assertEqual(self.said[-1], "Took Harbours out of Main.")

    def test_with_the_game_running_nothing_is_switched(self):
        self.make_live()
        with _isolation.answering(True) as asked,                 mock.patch("anno_modding_studio.core.launch.running", lambda game: True):
            self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.assertEqual(asked.titles, [])
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertIn("The game is running", self.said[0])

    def test_a_loadout_that_cannot_have_it_stops_the_lot_before_anything_is_built(self):
        other = self.mods_folder / self.NAME
        (other / "data/base/config/export").mkdir(parents=True)
        (other / "data/base/config/export/assets.xml").write_text(modops_file(ALPHA), encoding="utf-8")
        (other / "modinfo.json").write_text('{"ModID": "someone-elses"}', encoding="utf-8")
        _session, played = loadercheck.read_folders(
            [self.mods_folder / "a", other], [self.mods_folder], self.index, "anno1800")
        played.save(self.main)
        before = self.main.read_bytes()
        self.assertFalse(self.panel.change_loadouts("", [self.main], []))
        self.assertIn("No loadout was changed. Main: ", self.said[0])
        self.assertFalse((self.library / self.NAME).exists())
        self.assertEqual(self.main.read_bytes(), before)

    def test_a_mod_carried_on_with_a_new_modid_goes_in_beside_the_old_one(self):
        """The hands-on plan of 24 October: Main has the mod; it is carried
        on with a new name and its Mod ID cleared, and added to Main."""
        old, new = "[Gameplay] Harbours", "[Gameplay] Harbours (Studio)"
        folder = self.mods_folder / old
        (folder / "data/base/config/export").mkdir(parents=True)
        (folder / "data/base/config/export/assets.xml").write_text(modops_file(ALPHA), encoding="utf-8")
        (folder / "modinfo.json").write_text(
            '{"ModID": "harbours", "ModName": {"English": "Harbours"}, '
            '"Category": {"English": "Gameplay"}}', encoding="utf-8")
        _session, played = loadercheck.read_folders(
            [self.mods_folder / "a", folder], [self.mods_folder], self.index, "anno1800")
        played.save(self.main)
        _session, carried = loadercheck.read_folders([folder], [self.mods_folder], self.index, "anno1800")
        carried.home = carried.editing = old
        layer = carried.layer(old)
        layer.kept, layer.name, layer.mod_id = True, "Harbours (Studio)", ""
        here = self.root / "Harbours (Studio).annoload"
        panel = OutputPanel(carried)
        panel.where = _Where(self.library, str(self.mods_folder))
        panel.loadout_path = lambda: here
        panel.saveRequested.connect(lambda: carried.save(here))
        self.assertTrue(panel.change_loadouts(old, [self.main], []))
        self.assertEqual(carried.home, new)
        self.assertTrue((self.library / new).is_dir())
        self.assertFalse((self.library / old).exists())
        opened = Loadout.load(self.main)
        self.assertEqual(sorted(one.origin for one in opened.layers), sorted(["a", old, new]))

    def test_a_folder_changed_since_its_play_is_left_to_its_play(self):
        self.make_live()
        (self.mods_folder / "a").rename(self.mods_folder / "-a")
        with _isolation.answering(True) as asked:
            self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.assertEqual(asked.titles, [])
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertIn("press Play", self.said[0])

    # -- Update (section 5.6) --------------------------------------------------
    def change_it(self, value: str = "14") -> None:
        self.mine.set_change(Change(guid="1000", asset_label="alpha", path="/Values/Building/Radius",
                                    tag="Radius", old_value="10", new_value=value, authored=True))

    def built(self) -> str:
        return next((self.library / self.NAME).rglob("assets.xml")).read_text(encoding="utf-8")

    def test_once_another_loadout_has_it_update_builds_it_again_for_them(self):
        self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.panel.refresh()
        # As built: nothing to update.
        self.assertTrue(self.panel.update_button.isVisibleTo(self.panel))
        self.assertFalse(self.panel.update_button.isEnabled())
        self.assertEqual(self.panel.game_status.text(), "In Main.")
        self.change_it()
        self.panel.refresh()
        self.assertEqual(self.panel.game_status.text(),
                         "In Main. The build they have is from before your last changes.")
        self.assertTrue(self.panel.update_button.isEnabled())
        # The step to take next is the one highlighted.
        self.assertEqual(self.panel.update_button.objectName(), "Primary")
        self.assertEqual(self.panel.game_button.objectName(), "")
        said = []
        self.panel.modUpdated.connect(said.append)
        self.assertTrue(self.panel.update_build())
        self.assertEqual(len(self.saved), 2)
        self.assertIn("<Radius>14</Radius>", self.built())
        self.assertFalse((self.mods_folder / self.NAME).exists())
        self.assertEqual(said, ["Built Harbours again for Main."])
        self.assertEqual(self.panel.game_status.text(), "In Main.")
        self.assertFalse(self.panel.update_button.isEnabled())
        self.assertEqual(self.panel.game_button.objectName(), "Primary")

    def test_the_build_update_made_is_its_projects_and_the_next_moves_on(self):
        """R3-43, hands-on run 3: Update kept its build in the loadout only,
        nothing saved it afterwards, and the next Update took the same
        number again for other files."""
        self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        ref = self.mine.projects[""]
        self.change_it()
        self.assertTrue(self.panel.update_build())
        revision = ref.read_at
        project = projects.Project.read(projects.where(ref.path))
        self.assertEqual(project.build, 2)
        self.assertEqual(project.revision, revision)
        # Opened again, as after closing without a save: the build is there.
        again = Loadout.load(self.here)
        self.assertEqual(again.builds[""]["build"], 2)
        # And nothing of the project is left for the next save to write.
        self.mine.save(self.here)
        self.assertEqual(projects.Project.read(projects.where(ref.path)).revision, revision)
        self.change_it("16")
        self.assertTrue(self.panel.update_build())
        self.assertEqual(projects.Project.read(projects.where(ref.path)).build, 3)

    def test_a_build_from_before_builds_said_what_they_wrote_can_be_updated(self):
        self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        library.record(self.library, self.NAME, forget=(library.DIGEST,))
        self.change_it()
        self.panel.refresh()
        self.assertEqual(self.panel.game_status.text(), "In Main.")
        self.assertTrue(self.panel.update_button.isEnabled())
        self.assertEqual(self.panel.update_button.objectName(), "")

    def test_where_the_game_loads_it_it_has_the_new_build_at_once(self):
        self.assertTrue(self.panel.change_loadouts("", [self.main], []))
        self.make_live()
        self.change_it()
        with mock.patch("anno_modding_studio.core.launch.running", lambda game: True):
            self.assertFalse(self.panel.update_build())
        self.assertIn("Close the game first.", self.said[-1])
        self.assertNotIn("<Radius>14</Radius>", self.built())
        said = []
        self.panel.modUpdated.connect(said.append)
        self.assertTrue(self.panel.update_build())
        self.assertIn("<Radius>14</Radius>", self.built())
        self.assertEqual(said, ["Built Harbours again for Main. The game has the new build: Main is what it loads."])
        # Nothing was switched: Main is as its Play left it.
        self.assertTrue(adding.as_play_left_it(Loadout.load(self.main), self.mods_folder, self.library))



class TestWhatIPlayNowFromTheTab(PlayingWindow):
    """Section 5.7: no loadout of the mods you play, so Add to loadout makes
    one of what the game loads, with the mod in it.  The Start card's own
    questions -- clearing the window, moving the mods into the library --
    are its own, and tested there: here it is stood in for by a loadout of
    what the game loads, saved and opened."""

    def mine(self) -> Path:
        """The loadout Write a mod of your own made, saved and open."""
        mine = Loadout(game="anno1800", own=own_record(name="Harbours", category="Gameplay"))
        mine.set_up_as(AIM_MAKE)
        mine.make_own_mod()
        mine.set_change(Change(guid="1000", asset_label="alpha", path="/Values/Building/Radius",
                               tag="Radius", old_value="10", new_value="12", authored=True))
        source = self.root / "Harbours.annoload"
        mine.save(source)
        self.assertTrue(self.window.load_loadout(source, asked=True))
        return source

    def test_a_loadout_of_what_the_game_loads_with_the_mod_in_it(self):
        install_mod(self.mods, "[Gameplay] Other", "other", "1")
        source = self.mine()
        made = self.root / "What I play.annoload"
        window = self.window

        def what_the_game_loads(aim):
            self.assertEqual(aim, AIM_MANAGE)
            played = Loadout(game="anno1800", mods_folder=str(self.mods))
            played.set_up_as(AIM_MANAGE)
            played.layers = [ModLayer(origin="[Gameplay] Other", mod_id="other",
                                      folder=str(self.mods / "[Gameplay] Other"))]
            played.save(made)
            window.load_loadout(made, asked=True)

        with mock.patch.object(window.start, "choose_aim", side_effect=what_the_game_loads):
            self.assertTrue(window.start.play_now_with(source, ""))
        self.assertEqual(window.loadout_path, made)
        self.assertEqual(sorted(layer.origin for layer in window.loadout.layers),
                         ["[Gameplay] Harbours", "[Gameplay] Other"])
        self.assertTrue((window.where.library_dir() / "[Gameplay] Harbours").is_dir())
        self.assertEqual(window.status_label.text(), "What I play has the mods the game loads now, "
                                                     "and Harbours. Press Play to play it.")

    def test_turned_down_along_the_way_nothing_is_added(self):
        source = self.mine()
        before = source.read_bytes()
        with mock.patch.object(self.window.start, "choose_aim"):
            self.assertFalse(self.window.start.play_now_with(source, ""))
        self.assertEqual(self.window.loadout_path, source)
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
