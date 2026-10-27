"""The library: what the Studio installs lives there, junctioned into the mods
folder; and leaving it makes the mods folder whole again.

Junctions are a Windows thing, as is the game, so the suite is too.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt  # noqa: E402
from support import downloadable_mod, install_mod  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    installplan,
    library,
    modio_cache,
    places,
    switches,
    truth,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402
from anno_modding_studio.core.modio import summarise as modio_summary  # noqa: E402
from anno_modding_studio.ui import widgets  # noqa: E402


def QMessageBox_for_tests():
    from anno_modding_studio.ui import main_window

    return main_window.QMessageBox


def _zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return buffer.getvalue()


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class _Folders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.library = self.root / "library" / "anno1800"
        for patched in (
            mock.patch.dict(os.environ, {
                modio_cache.ENV_ROOT: str(self.root / "modio"),
                library.ENV_LIBRARY: str(self.root / "library"),
            }),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            mock.patch.object(installplan.switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)

    def archive(self, name: str, entries=None) -> Path:
        path = self.root / (name.replace(" ", "") + ".zip")
        path.write_bytes(_zip(entries or {"%s/modinfo.json" % name: '{"Version": "2.0"}'}))
        return path


class TestWhere(_Folders):
    def test_the_environment_then_the_choice_then_local_app_data(self):
        self.assertEqual(library.game_dir(ANNO_1800), self.library)
        with mock.patch.dict(os.environ, {library.ENV_LIBRARY: "", "LOCALAPPDATA": str(self.root / "local")}):
            self.assertEqual(library.root("D:/chosen"), Path("D:/chosen"))
            self.assertEqual(library.root(""), self.root / "local" / "AnnoModdingStudio" / "library")


class TestJunctions(_Folders):
    def test_made_read_and_removed_without_touching_the_target(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "1")
        linked = library.link(target, self.mods)
        self.assertEqual(linked.link, self.mods / "[Gameplay] Thing")
        self.assertTrue(library.is_junction(linked.link))
        self.assertEqual(library.target_of(linked.link).resolve(), target.resolve())
        self.assertEqual(library.kind_of(linked.link, self.library), library.COPY)
        self.assertTrue((linked.link / "modinfo.json").is_file())
        library.remove_junction(linked.link)
        self.assertFalse(linked.link.exists())
        self.assertTrue(target.is_dir())
        self.assertEqual(library.kind_of(target, self.library), "")

    def test_a_junction_is_seen_on_a_python_without_path_is_junction(self):
        """``Path.is_junction`` is Python 3.12's, and the Studio runs on 3.10.
        Asking it there raised AttributeError, which read as "not a junction"
        for every junction the library made."""
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "1")
        linked = library.link(target, self.mods)

        def before_3_12(_path):
            raise AttributeError("is_junction")

        with mock.patch.object(Path, "is_junction", before_3_12, create=True):
            self.assertTrue(library.is_junction(linked.link))
            self.assertFalse(library.is_junction(target))
            self.assertFalse(library.is_junction(self.mods / "nothing here"))

    def test_a_real_folder_in_the_way_is_set_aside_and_a_junction_replaced(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "2")
        theirs = install_mod(self.mods, "[Gameplay] Thing", "thing", "1")
        linked = library.link(target, self.mods)
        self.assertIsNotNone(linked.set_aside)
        self.assertTrue((linked.set_aside / "modinfo.json").is_file())
        self.assertEqual(json.loads((self.mods / "[Gameplay] Thing" / "modinfo.json").read_text())["Version"], "2")
        other = install_mod(self.library, "Other", "thing", "3")
        again = library.link(other, self.mods, "[Gameplay] Thing")
        self.assertIsNone(again.set_aside)
        self.assertEqual(library.target_of(again.link).resolve(), other.resolve())
        self.assertTrue(theirs.parent.is_dir())

    def test_nothing_is_linked_or_set_aside_outside_the_mods_folder(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "2")
        theirs = self.mods.parent / "theirs"
        theirs.mkdir(exist_ok=True)
        with self.assertRaises(OSError):
            library.link(target, self.mods, "..\\theirs")
        self.assertFalse(library.is_link(theirs))
        self.assertTrue(theirs.is_dir())

    def test_the_folder_at_the_name_is_not_set_aside_to_link_to_itself(self):
        """Manage own folder with a folder picked inside the mods folder: it
        was moved into the backups and the link to it then failed, so the mod
        was gone from the mods folder."""
        mine = install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        with self.assertRaises(OSError):
            library.refer(self.library, mine, self.mods)
        self.assertFalse(library.is_link(mine))
        self.assertTrue((mine / "modinfo.json").is_file())
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(library.references(self.library), {})

    def test_linking_again_to_the_junction_at_the_name_keeps_it(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "1")
        linked = library.link(target, self.mods)
        again = library.link(linked.link, self.mods)
        self.assertIsNone(again.set_aside)
        self.assertTrue(library.is_link(linked.link))
        self.assertEqual(library.target_of(linked.link).resolve(), target.resolve())
        self.assertFalse(library.is_link(again.target))
        self.assertEqual(again.target.resolve(), target.resolve())

    def test_managing_the_junction_at_the_name_keeps_what_it_stands_for(self):
        """Manage own folder with the junction already in the mods folder
        picked: the reference recorded was the junction itself, pointing at
        itself."""
        own = self.root / "own"
        mine = install_mod(own, "[Gameplay] Mine", "mine", "1")
        first = library.refer(self.library, mine, self.mods)
        again = library.refer(self.library, first.link, self.mods)
        # Resolving would follow the junction and hide a record of it, so
        # each is asked not to be a link as well as to land on the folder.
        self.assertFalse(library.is_link(again.target))
        self.assertEqual(again.target.resolve(), mine.resolve())
        held = Path(library.references(self.library)["[Gameplay] Mine"])
        self.assertFalse(library.is_link(held))
        self.assertEqual(held.resolve(), mine.resolve())
        noted = Path(library.links(self.library)["[Gameplay] Mine"]["target"])
        self.assertFalse(library.is_link(noted))
        self.assertEqual(noted.resolve(), mine.resolve())
        self.assertEqual(library.target_of(first.link).resolve(), mine.resolve())

    def test_a_target_that_is_not_there_touches_nothing(self):
        theirs = install_mod(self.mods, "[Gameplay] Thing", "thing", "1")
        with self.assertRaises(OSError):
            library.link(self.library / "gone", self.mods, "[Gameplay] Thing")
        self.assertFalse(library.is_link(theirs))
        self.assertTrue((theirs / "modinfo.json").is_file())

    def test_a_link_that_fails_puts_back_what_was_at_the_name(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "2")
        theirs = install_mod(self.mods, "[Gameplay] Thing", "thing", "1")
        with mock.patch.object(library, "make_link", side_effect=OSError("no")), \
                self.assertRaises(OSError):
            library.link(target, self.mods)
        self.assertFalse(library.is_link(theirs))
        self.assertEqual(json.loads((theirs / "modinfo.json").read_text())["Version"], "1")

    def test_a_link_that_fails_puts_back_the_junction_it_replaced(self):
        target = install_mod(self.library, "[Gameplay] Thing", "thing", "2")
        old = install_mod(self.library, "Old", "thing", "1")
        first = library.link(old, self.mods, "[Gameplay] Thing")
        real_make_link = library.make_link
        tries = []

        def fails_the_first_time(at, to):
            tries.append(to)
            if len(tries) == 1:
                raise OSError("no")
            real_make_link(at, to)

        with mock.patch.object(library, "make_link", side_effect=fails_the_first_time), \
                self.assertRaises(OSError):
            library.link(target, self.mods, "[Gameplay] Thing")
        self.assertTrue(library.is_link(first.link))
        self.assertEqual(library.target_of(first.link).resolve(), old.resolve())


class TestInstalling(_Folders):
    def test_the_zip_lands_in_the_library_and_a_link_where_the_folder_would_be(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        self.assertEqual(done.placed, [self.library / "[Gameplay] Thing"])
        self.assertEqual([l.link for l in done.linked], [self.mods / "[Gameplay] Thing"])
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))
        self.assertEqual([p.name for p in self.mods.iterdir()], ["[Gameplay] Thing"])

    def test_replacing_a_library_mod_replaces_its_target_and_keeps_the_link(self):
        first = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        link = first.linked[0].link
        done = library.install(
            self.archive("[Gameplay] Thing", {"[Gameplay] Thing/modinfo.json": '{"Version": "3"}'}),
            self.library, self.mods, replace=link,
        )
        self.assertEqual(done.placed, [self.library / "[Gameplay] Thing"])
        self.assertIsNotNone(done.set_aside)  # the old copy, out of the library
        self.assertEqual(json.loads((link / "modinfo.json").read_text())["Version"], "3")
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir()], ["[Gameplay] Thing"])

    def test_replacing_a_real_folder_sets_it_aside_and_takes_its_name(self):
        theirs = install_mod(self.mods, "-[Seal] Thing", "thing", "1")
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods, replace=theirs)
        # The old name exactly, "off" mark and all: that is the user's.
        self.assertEqual(done.linked[0].link, self.mods / "-[Seal] Thing")
        self.assertEqual(done.placed, [self.library / "[Gameplay] Thing"])
        self.assertTrue(library.is_junction(theirs))
        self.assertIsNotNone(done.set_aside)
        self.assertTrue((done.set_aside / "modinfo.json").is_file())
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["-[Seal] Thing"])


class TestAZipFromDisk(_Folders):
    """A zip nobody vouched for: what it would replace is found first, and
    installing it replaces that."""

    def modded(self, folder: str, version: str, mod_id: str = "thing") -> Path:
        return self.archive(folder + version, {
            "%s/modinfo.json" % folder: json.dumps({"ModID": mod_id, "Version": version}),
        })

    def test_nothing_in_the_library_means_nothing_replaced_and_nothing_written(self):
        (mod,) = library.arriving(self.modded("[Gameplay] Thing", "1"), self.library)
        self.assertEqual((mod.name, mod.mod_id, mod.version, mod.replaces), ("[Gameplay] Thing", "thing", "1", None))
        self.assertFalse(self.library.exists())
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_the_same_modid_under_another_folder_name_is_replaced_not_doubled(self):
        library.install_zip(self.modded("[Gameplay] Thing", "1"), self.library, self.mods)
        newer = self.modded("Thing v2", "2")
        (mod,) = library.arriving(newer, self.library)
        self.assertEqual(mod.replaces.name, "[Gameplay] Thing")
        self.assertEqual(mod.replaces.version, "1")
        done = library.install_zip(newer, self.library, self.mods)
        # One copy, under the name the first had: load order keys on it.
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir()], ["[Gameplay] Thing"])
        self.assertEqual([p.name for p in self.mods.iterdir()], ["[Gameplay] Thing"])
        self.assertEqual(json.loads((self.mods / "[Gameplay] Thing" / "modinfo.json").read_text())["Version"], "2")
        self.assertIsNotNone(done.set_aside)

    def test_a_copy_switched_off_is_replaced_and_comes_in_on(self):
        first = library.install_zip(self.modded("[Gameplay] Thing", "1"), self.library, self.mods)
        library.unlink(self.library, first.linked[0].link)
        library.install_zip(self.modded("Thing v2", "2"), self.library, self.mods)
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir()], ["[Gameplay] Thing"])
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))

    def test_a_folder_of_the_users_own_with_that_modid_is_left_alone(self):
        checkout = install_mod(self.root / "dev", "my-thing", "thing", "0.1")
        library.refer(self.library, checkout, self.mods, "[Gameplay] Mine")
        (mod,) = library.arriving(self.modded("[Gameplay] Thing", "1"), self.library)
        self.assertIsNone(mod.replaces)

    def test_the_registry_says_it_came_from_a_zip_and_keeps_which_modio_mod_it_is(self):
        library.install(
            self.archive("[Gameplay] Thing"), self.library, self.mods,
            facts={"modio_id": 7, "modfile_id": 70, "version": "2.0"},
        )
        dropped = self.modded("[Gameplay] Thing", "3")
        library.install_zip(dropped, self.library, self.mods)
        self.assertEqual(
            library.registry(self.library)["[Gameplay] Thing"],
            {"modio_id": 7, "version": "3", "source": library.ZIP,
             "zip": dropped.name, "zip_md5": hashlib.md5(dropped.read_bytes()).hexdigest(),
             library.BACKUP: str(library.backup_dir(self.library) / dropped.name)},
        )

    def test_a_zip_nobody_named_is_known_by_its_name_and_hash_and_nothing_else(self):
        dropped = self.modded("[Gameplay] Thing", "1")
        library.install_zip(dropped, self.library, self.mods)
        entry = library.registry(self.library)["[Gameplay] Thing"]
        kept = library.backup_dir(self.library) / dropped.name
        self.assertEqual(entry, {
            "version": "1", "source": library.ZIP,
            "zip": dropped.name, "zip_md5": hashlib.md5(dropped.read_bytes()).hexdigest(),
            library.BACKUP: str(kept),
        })
        # The zip itself, kept with the backups as it came.
        self.assertEqual(kept.read_bytes(), dropped.read_bytes())

    def test_a_zip_kept_twice_is_one_file_and_another_of_its_name_is_kept_beside_it(self):
        first = self.modded("[Gameplay] Thing", "1")
        library.install_zip(first, self.library, self.mods)
        library.install_zip(first, self.library, self.mods)
        other = first.parent / "other"
        other.mkdir()
        same_name = other / first.name
        same_name.write_bytes(self.modded("[Gameplay] Thing", "2").read_bytes())
        library.install_zip(same_name, self.library, self.mods)
        self.assertEqual(sorted(path.name for path in library.backup_dir(self.library).iterdir()),
                         sorted([first.name, "%s (2)%s" % (first.stem, first.suffix)]))
        self.assertEqual(library.registry(self.library)["[Gameplay] Thing"][library.BACKUP],
                         str(library.backup_dir(self.library) / ("%s (2)%s" % (first.stem, first.suffix))))

    def test_mod_io_naming_it_afterwards_unsays_the_zip(self):
        library.install_zip(self.modded("[Gameplay] Thing", "1"), self.library, self.mods)
        library.learn(self.library, "[Gameplay] Thing", 7, "name")
        library.install(
            self.archive("[Gameplay] Thing"), self.library, self.mods,
            replace=self.mods / "[Gameplay] Thing",
            facts={"modio_id": 7, "modfile_id": 71, "version": "2", "source": library.MODIO},
        )
        self.assertEqual(
            library.registry(self.library)["[Gameplay] Thing"],
            {"modio_id": 7, "modfile_id": 71, "version": "2", "source": library.MODIO},
        )

    def test_a_box_lands_under_its_mods_own_names(self):
        box = self.archive("box", {
            "Box/A/modinfo.json": '{"ModID": "a"}', "Box/B/modinfo.json": '{"ModID": "b"}',
        })
        self.assertEqual([mod.name for mod in library.arriving(box, self.library)], ["A", "B"])
        done = library.install_zip(box, self.library, self.mods)
        self.assertEqual([linked.link.name for linked in done.linked], ["A", "B"])


class TestAdoptingAndReferring(_Folders):
    def test_adopting_moves_the_bytes_and_links_under_the_bare_name(self):
        mine = install_mod(self.mods, "[Gameplay] On", "on", "1")
        linked = library.adopt(self.library, mine)
        self.assertEqual(linked.link, self.mods / "[Gameplay] On")
        self.assertEqual(linked.target, self.library / "[Gameplay] On")
        self.assertTrue(library.is_junction(linked.link))
        self.assertEqual(library.kind_of(linked.link, self.library), library.COPY)

    def test_adopting_says_the_folder_was_moved_in(self):
        """It is the only copy there is now, and nothing says where another
        could be had."""
        mine = install_mod(self.mods, "[Gameplay] On", "on", "1")
        library.adopt(self.library, mine)
        entry = library.registry(self.library)["[Gameplay] On"]
        self.assertEqual(entry, {"source": library.ADOPTED, "version": "1"})

    def test_a_switched_off_folder_comes_in_off_the_librarys_way(self):
        # Off is no link, not a "-" on one: the mark comes off, and the link
        # is taken away at once, remembered for Play to put back.
        theirs = install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        linked = library.adopt(self.library, theirs)
        self.assertEqual(linked.target, self.library / "[Gameplay] Off")
        self.assertEqual(list(self.mods.iterdir()), [])
        self.assertEqual(list(library.unlinked(self.library)), ["[Gameplay] Off"])
        self.assertEqual(library.relink(self.library, "[Gameplay] Off"), self.mods / "[Gameplay] Off")

    def test_across_drives_a_move_that_does_not_come_out_whole_moves_nothing(self):
        mine = install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        with mock.patch.object(library.os, "rename", side_effect=OSError("another drive")), \
                mock.patch.object(library, "_files_of", side_effect=[{"a": 1}, {"a": 2}]):
            with self.assertRaises(OSError):
                library.adopt(self.library, mine)
        self.assertTrue((mine / "modinfo.json").is_file())
        self.assertFalse((self.library / "[Gameplay] Mine").exists())

    def test_a_reference_is_a_link_to_a_folder_the_user_keeps(self):
        checkout = install_mod(self.root / "dev", "my-mod", "mine", "0.1")
        linked = library.refer(self.library, checkout, self.mods, "[Gameplay] Mine")
        self.assertEqual(library.kind_of(linked.link, self.library), library.REFERENCE)
        self.assertEqual(library.references(self.library), {"[Gameplay] Mine": str(checkout)})
        # A junction to somewhere the library does not know is nobody's.
        stray = self.root / "elsewhere"
        stray.mkdir()
        library.make_junction(self.mods / "Stray", stray)
        self.assertEqual(library.kind_of(self.mods / "Stray", self.library), "")


class TestLeaving(_Folders):
    def test_copies_become_folders_again_and_references_are_unlinked(self):
        library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        checkout = install_mod(self.root / "dev", "my-mod", "mine", "0.1")
        library.refer(self.library, checkout, self.mods, "[Gameplay] Mine")
        install_mod(self.mods, "[Gameplay] Theirs", "theirs", "1")
        done = library.put_back([self.mods], self.library)
        self.assertEqual(done.materialised, [self.mods / "[Gameplay] Thing"])
        self.assertEqual(done.unlinked, [self.mods / "[Gameplay] Mine"])
        self.assertEqual(done.failed, [])
        self.assertFalse(library.is_junction(self.mods / "[Gameplay] Thing"))
        self.assertTrue((self.mods / "[Gameplay] Thing" / "modinfo.json").is_file())
        self.assertFalse((self.mods / "[Gameplay] Mine").exists())
        self.assertTrue((checkout / "modinfo.json").is_file())
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir()], [])
        self.assertEqual(library.references(self.library), {})
        self.assertIn("1 mod is a real folder", done.describe())
        self.assertEqual(library.put_back([self.mods], self.library).describe(),
                         "No mod in the mods folder came from the library.")


class TestTheInventorySees(_Folders):
    def test_a_library_mod_is_installed_and_said_to_be_the_librarys(self):
        library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        install_mod(self.mods, "[Gameplay] Theirs", "theirs", "1")
        found = {mod.folder_name: mod for mod in installplan.inventory(ANNO_1800, self.mods)}
        self.assertEqual(sorted(found), ["[Gameplay] Theirs", "[Gameplay] Thing"])
        self.assertEqual(found["[Gameplay] Thing"].kind, library.COPY)
        self.assertEqual(found["[Gameplay] Theirs"].kind, "")
        self.assertEqual(found["[Gameplay] Thing"].version, "2.0")


class TestDroppingAZipOnTheWindow(_Folders):
    """A mod's zip dropped on the window is installed into the library once
    the question is answered, and read in or opened as a loadout."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock(source_path=str(self.root / "assets.xml"), asset_count=0, text_count=0)
        self.addCleanup(setattr, self.window, "index", None)
        self.said = []
        for target, name, value in (
            (self.window.where, "mods_folder", mock.Mock(return_value=str(self.mods))),
            (self.window.prompts, "report_dropped", mock.Mock(side_effect=lambda title, text: self.said.append(text))),
            (self.window, "refresh_truth", mock.Mock()),
        ):
            patched = mock.patch.object(target, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.set_up_as("tweak")
        self.addCleanup(self.window.files.mark_saved)
        self.zip = self.archive("[Gameplay] Thing", {
            "[Gameplay] Thing/modinfo.json": json.dumps({"ModID": "thing", "Version": "2", "CreatorName": "Kurila"}),
        })

    def drop(self, answer, *archives):
        from anno_modding_studio.ui import prompts

        answer = {"add": prompts.ZIP_ADD, "open": prompts.ZIP_OPEN, "install": prompts.ZIP_INSTALL, "": ""}[answer]
        with mock.patch.object(self.window.prompts, "ask_dropped_zips", return_value=answer) as asked:
            landed = self.window.library_controller.install_dropped(archives or [self.zip])
        return landed, asked

    def test_added_to_the_loadout_it_is_installed_and_imported_kept_separate(self):
        with mock.patch.object(self.window, "import_mod", return_value=True) as imported:
            landed, asked = self.drop("add")
        self.assertTrue(landed)
        link = self.mods / "[Gameplay] Thing"
        self.assertTrue(library.is_junction(link))
        self.assertTrue((self.library / "[Gameplay] Thing" / "modinfo.json").is_file())
        imported.assert_called_once_with(separate=True, start_dir=str(link))
        # The question had what the zip holds and where it goes, before any of it went.
        (arriving, game, folder, loaded, working), _ = asked.call_args
        self.assertEqual([(mod.label, mod.version, mod.replaces) for mod in arriving], [("[Gameplay] Thing", "2", None)])
        self.assertEqual((game, folder, loaded, working), (ANNO_1800, str(self.mods), False, True))
        self.assertIn("Installed [Gameplay] Thing into the library", self.window.status_label.text())

    def test_several_added_to_a_loadout_with_no_folder_read_are_each_imported_in_turn(self):
        """The drop question says "Add to loadout" reads them in, so each
        is, one import after another -- unlike a box from mod.io, where one
        mod was asked for and only the first is (A11-84)."""
        box = self.archive("box", {"Box/A/modinfo.json": "{}", "Box/B/modinfo.json": "{}"})
        with mock.patch.object(self.window, "import_mod", return_value=True) as imported:
            landed, _ = self.drop("add", box)
        self.assertTrue(landed)
        self.assertEqual(imported.call_args_list, [
            mock.call(separate=True, start_dir=str(self.mods / "A")),
            mock.call(separate=True, start_dir=str(self.mods / "B")),
        ])

    def other_games(self):
        """A zip of a mod laid out as an Anno 117 one, dropped with 1800 loaded."""
        return self.archive("Celtic", {
            "Celtic/modinfo.json": json.dumps({"ModID": "celtic", "Version": "1"}),
            "Celtic/data/base/config/export/assets.xml": "<ModOps/>",
        })

    def test_a_mod_for_the_other_game_is_asked_about_before_anything_is_installed(self):
        """It was said only by the import after the install, by when the mod
        was in this game's library whatever was answered."""
        with mock.patch.object(self.window.prompts, "ask_dropped_other_game", return_value="") as asked, \
                mock.patch.object(self.window, "import_mod") as imported:
            landed, question = self.drop("add", self.other_games())
        self.assertFalse(landed)
        (mismatched, game, others), _ = asked.call_args
        self.assertEqual([(mod.label, mod.game) for mod in mismatched], [("Celtic", "anno117")])
        self.assertEqual((game, others), (ANNO_1800, 0))
        question.assert_not_called()
        imported.assert_not_called()
        self.assertEqual(list(self.mods.iterdir()), [])
        self.assertFalse(self.library.exists())

    def test_left_out_the_mods_dropped_with_it_are_still_installed(self):
        from anno_modding_studio.ui import prompts

        with mock.patch.object(self.window.prompts, "ask_dropped_other_game",
                               return_value=prompts.OTHER_GAME_SKIP), \
                mock.patch.object(self.window, "import_mod", return_value=True):
            landed, question = self.drop("add", self.other_games(), self.zip)
        self.assertTrue(landed)
        self.assertEqual([mod.label for mod in question.call_args[0][0]], ["[Gameplay] Thing"])
        self.assertEqual([path.name for path in self.mods.iterdir()], ["[Gameplay] Thing"])

    def test_the_mods_left_out_with_it_are_counted_by_the_zip(self):
        """A11-83: leaving a mod out leaves its whole zip out, so a mod for
        this game in the same zip is not one leaving out still installs. It
        was counted, Leave it out was offered, and then nothing was
        installed and nothing said."""
        both = self.archive("both", {
            "Both/Plain/data/config/export/main/asset/assets.xml": "<ModOps/>",
            "Both/Celtic/modinfo.json": json.dumps({"ModID": "celtic", "Version": "1"}),
            "Both/Celtic/data/base/config/export/assets.xml": "<ModOps/>",
        })
        with mock.patch.object(self.window.prompts, "ask_dropped_other_game", return_value="") as asked:
            self.drop("add", both)
        self.assertEqual(asked.call_args[0][2], 0)
        with mock.patch.object(self.window.prompts, "ask_dropped_other_game", return_value="") as asked:
            self.drop("add", both, self.zip)
        self.assertEqual(asked.call_args[0][2], 1)

    def test_installed_anyway_it_goes_on_to_the_usual_question(self):
        from anno_modding_studio.ui import prompts

        with mock.patch.object(self.window.prompts, "ask_dropped_other_game",
                               return_value=prompts.OTHER_GAME_ANYWAY), \
                mock.patch.object(self.window, "import_mod", return_value=True):
            landed, question = self.drop("add", self.other_games())
        self.assertTrue(landed)
        question.assert_called_once()
        self.assertTrue(library.is_junction(self.mods / "Celtic"))

    def test_a_mod_for_the_game_loaded_is_not_asked_about(self):
        mine = self.archive("Plain", {
            "Plain/data/config/export/main/asset/assets.xml": "<ModOps/>",
        })
        self.assertEqual([mod.game for mod in library.arriving(mine, self.library)], [ANNO_1800])
        self.assertEqual([mod.game for mod in library.arriving(self.zip, self.library)], [""])
        with mock.patch.object(self.window.prompts, "ask_dropped_other_game") as asked, \
                mock.patch.object(self.window, "import_mod", return_value=True):
            self.drop("add", mine)
        asked.assert_not_called()

    def test_the_other_game_question_offers_leaving_out_only_beside_other_mods(self):
        shown = []
        with mock.patch.object(self.window.prompts, "answer",
                               side_effect=lambda box: shown.append(
                                   (box.text(), [b.text() for b in box.buttons()],
                                    box.defaultButton().text()))):
            arriving = library.arriving(self.other_games(), self.library)
            self.assertEqual(self.window.prompts.ask_dropped_other_game(arriving, ANNO_1800, 0), "")
            self.window.prompts.ask_dropped_other_game(arriving, ANNO_1800, 1)
        text, buttons, default = shown[0]
        self.assertIn("Celtic (Anno 117", text)
        self.assertIn("Anno 1800 is loaded", text)
        self.assertNotIn("&Leave it out", buttons)
        self.assertNotEqual(default, "&Install anyway")
        self.assertEqual(shown[1][2], "&Leave it out")

    def test_declined_nothing_is_written(self):
        with mock.patch.object(self.window, "import_mod") as imported:
            landed, _ = self.drop("")
        self.assertFalse(landed)
        imported.assert_not_called()
        self.assertEqual(list(self.mods.iterdir()), [])
        self.assertFalse(self.library.exists())

    def test_with_no_game_files_loaded_it_is_refused_before_anything_is_asked(self):
        self.window.index = None
        landed, asked = self.drop("add")
        self.assertFalse(landed)
        asked.assert_not_called()
        self.assertIn("No game files are loaded yet.", self.said[0])
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_a_zip_with_no_mod_in_it_is_said_and_the_rest_still_asked_about(self):
        junk = self.archive("junk", {"readme.txt": "hi"})
        with mock.patch.object(self.window, "import_mod", return_value=True):
            landed, asked = self.drop("add", junk, self.zip)
        self.assertTrue(landed)
        self.assertIn("junk.zip could not be installed: the zip holds no mod", self.said[0])
        self.assertEqual(len(asked.call_args[0][0]), 1)
        # And alone, it is said and nothing is asked.
        landed, asked = self.drop("add", junk)
        self.assertFalse(landed)
        asked.assert_not_called()

    def test_a_file_that_is_not_a_zip_is_not_for_this(self):
        text = self.root / "notes.txt"
        text.write_text("hi", encoding="utf-8")
        landed, asked = self.drop("add", text)
        self.assertFalse(landed)
        asked.assert_not_called()
        self.assertEqual(self.said, [])

    def test_with_a_mods_folder_loaded_the_folder_is_read_again_with_it_arrived(self):
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Other", mod_id="other")]
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            landed, asked = self.drop("add")
        self.assertTrue(landed)
        read.assert_called_once_with(arrived={"[Gameplay] Thing"}, keep_switches=True)
        self.assertTrue(asked.call_args[0][3])

    def test_opened_as_a_loadout_somebody_elses_mod_gets_the_patch_aim(self):
        from anno_modding_studio.core.changes import AIM_TWEAK

        with mock.patch.object(self.window.start, "choose_aim") as chosen:
            self.drop("open")
        chosen.assert_called_once_with(AIM_TWEAK, start_dir=str(self.mods / "[Gameplay] Thing"))

    def test_opened_as_a_loadout_a_mod_with_your_creator_name_is_carried_on_with(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        with mock.patch.object(self.window.preferences, "creator_name", return_value="kurila"), \
                mock.patch.object(self.window.start, "choose_aim") as chosen:
            self.drop("open")
        chosen.assert_called_once_with(AIM_MAKE, existing=True, start_dir=str(self.mods / "[Gameplay] Thing"))

    def test_opening_as_a_loadout_asks_about_what_is_open_before_unpacking(self):
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Other", mod_id="other")]
        with mock.patch.object(self.window, "new_loadout", return_value=False) as fresh, \
                mock.patch.object(self.window.start, "choose_aim") as chosen:
            landed, _ = self.drop("open")
        self.assertFalse(landed)
        fresh.assert_called_once_with()
        chosen.assert_not_called()
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_opening_as_a_loadout_leaves_a_loadout_that_holds_nothing_yet(self):
        """A loadout with a name and a file but no mod read in and nothing
        made was not started over, so the dropped mod was read into it,
        under its file and its name (A8-38)."""
        old = self.root / "Old Mod.annoload"
        self.window.loadout.own.name = "Old Mod"
        self.window.loadout_path = old
        with mock.patch.object(self.window.start, "choose_aim") as chosen:
            self.drop("open")
        chosen.assert_called_once()
        self.assertIsNone(self.window.loadout_path)
        self.assertNotEqual(self.window.loadout.own.name, "Old Mod")
        self.assertEqual(self.window.loadout.aim, "")

    def test_and_asks_about_it_first_as_about_any_other(self):
        self.window.loadout_path = self.root / "Old Mod.annoload"
        with mock.patch.object(self.window, "new_loadout", return_value=False) as fresh, \
                mock.patch.object(self.window.start, "choose_aim") as chosen:
            landed, _ = self.drop("open")
        self.assertFalse(landed)
        fresh.assert_called_once_with()
        chosen.assert_not_called()
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_the_aim_starts_from_the_mod_dropped_without_asking_which(self):
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK

        link = str(self.mods / "[Gameplay] Thing")
        with mock.patch.object(self.window, "import_mod", return_value=False) as imported, \
                mock.patch.object(self.window.preferences, "ensure_creator"):
            self.window.start.choose_aim(AIM_TWEAK, start_dir=link)
            self.window.start.choose_aim(AIM_MAKE, existing=True, start_dir=link)
        self.assertEqual(imported.call_args_list, [mock.call(separate=True, start_dir=link)] * 2)

    def test_with_no_loadout_and_no_way_of_working_one_mod_can_only_be_opened_as_a_loadout(self):
        self.window.new_loadout()
        self.assertEqual(self.window.loadout.aim, "")
        _, asked = self.drop("")
        self.assertFalse(asked.call_args[0][4])
        shown = []
        with mock.patch.object(self.window.prompts, "answer",
                               side_effect=lambda box: shown.extend(b.text() for b in box.buttons())):
            self.window.prompts.ask_dropped_zips(
                library.arriving(self.zip, self.library), ANNO_1800, str(self.mods), False, False,
            )
        self.assertIn("&Open as loadout", shown)
        self.assertNotIn("&Add to loadout", shown)

    def test_with_no_loadout_and_no_way_of_working_several_mods_can_only_be_installed(self):
        # A10-15: they were offered "Add to loadout", by default, and read
        # into a window whose Start tab then said "None yet".
        box = self.archive("box", {"Box/A/modinfo.json": "{}", "Box/B/modinfo.json": "{}"})
        shown = []

        def answer(box_):
            shown.append((box_.text(), [b.text() for b in box_.buttons()], box_.defaultButton().text(),
                          box_.defaultButton().toolTip()))
            return None

        with mock.patch.object(self.window.prompts, "answer", side_effect=answer):
            self.window.prompts.ask_dropped_zips(
                library.arriving(box, self.library), ANNO_1800, str(self.mods), False, False,
            )
        text, buttons, default, tip = shown[0]
        self.assertNotIn("&Add to loadout", buttons)
        self.assertEqual(default, "&Install only")
        self.assertIn("reads none of them in", tip)
        self.assertIn("choose a way of working on the Start tab first", text)

    def test_installed_only_they_are_in_the_library_and_linked_and_nothing_is_read(self):
        self.window.new_loadout()
        box = self.archive("box", {"Box/A/modinfo.json": "{}", "Box/B/modinfo.json": "{}"})
        with mock.patch.object(self.window, "read_installed") as read:
            landed, _ = self.drop("install", box)
        self.assertFalse(landed)
        read.assert_not_called()
        self.assertTrue(library.is_junction(self.mods / "A"))
        self.assertTrue(library.is_junction(self.mods / "B"))
        self.assertEqual(self.window.loadout.layers, [])
        self.assertIn("Installed", self.window.status_label.text())

    def test_the_question_names_the_copy_replaced_and_offers_a_loadout_for_one_mod_only(self):
        library.install_zip(self.zip, self.library, self.mods)
        newer = self.archive("Thing v3", {"Thing v3/modinfo.json": json.dumps({"ModID": "thing", "Version": "3"})})
        box = self.archive("box", {"Box/A/modinfo.json": "{}", "Box/B/modinfo.json": "{}"})
        shown = []

        def answer(box_):
            shown.append((box_.text(), [button.text() for button in box_.buttons()]))
            return None

        with mock.patch.object(self.window.prompts, "answer", side_effect=answer):
            for archive in (newer, box):
                self.window.prompts.ask_dropped_zips(
                    library.arriving(archive, self.library), ANNO_1800, str(self.mods), False,
                )
        text, buttons = shown[0]
        self.assertIn("Thing v3 3 (replaces version 2)", text)
        self.assertIn("One copy in the library is replaced, and set aside in the backups.", text)
        self.assertIn(str(self.mods.name), text)
        self.assertIn("&Open as loadout", buttons)
        text, buttons = shown[1]
        self.assertIn("You dropped 2 mods for Anno 1800:", text)
        self.assertNotIn("&Open as loadout", buttons)

    def test_a_zip_dragged_over_the_window_is_taken_and_any_other_file_is_not(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent

        def dragged(path):
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            return mime

        text = self.root / "notes.txt"
        text.write_text("hi", encoding="utf-8")
        buttons, keys = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        for path, taken in ((self.zip, True), (text, False), (self.root / "gone.zip", False)):
            mime = dragged(path)
            entering = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, buttons, keys)
            entering.ignore()
            self.window.dragEnterEvent(entering)
            self.assertEqual(entering.isAccepted(), taken, path.name)
            # And the window says so, all over, until the drag leaves it.
            self.assertEqual(not self.window.drop_hint.isHidden(), taken, path.name)
            if taken:
                self.assertEqual(self.window.drop_hint.geometry(), self.window.rect())
            self.window.dragLeaveEvent(QDragLeaveEvent())
            self.assertTrue(self.window.drop_hint.isHidden())
        # A text box would take the file's address as text: the window does first.
        mime = dragged(self.zip)
        entering = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, buttons, keys)
        self.assertTrue(self.window.eventFilter(self.window.output, entering))
        self.assertFalse(entering.isAccepted())
        self.window.drop_hint.cover()
        with mock.patch.object(self.window.library_controller, "install_dropped") as installed:
            self.window.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, buttons, keys))
            # Not from inside the drop, which the program dragged from waits on.
            installed.assert_not_called()
            self.assertTrue(self.window.drop_hint.isHidden())
            self.app.processEvents()
        installed.assert_called_once_with([QUrl.fromLocalFile(str(self.zip)).toLocalFile()])

    def test_a_zip_dragged_over_a_text_box_reaches_the_window(self):
        """As Qt hands the drag on, and not by calling the window's filter:
        with the filter on no viewport, every test here passed."""
        from PySide6.QtCore import QMimeData, QPoint, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent
        from PySide6.QtWidgets import QAbstractScrollArea

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.zip))])
        # Qt offers a drag to no widget of a tab that is not open yet.
        for tab in range(self.window.tabs.count()):
            self.window.tabs.setTabEnabled(tab, True)
        taking = [
            area for area in self.window.findChildren(QAbstractScrollArea)
            if area.viewport().acceptDrops() and area.isEnabled()
        ]
        # A text box that can be typed in would take the file's address as
        # text, and the reference graph any drag at all.
        self.assertIn(self.window.references.view, taking)
        for area in taking:
            entering = QDragEnterEvent(
                QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            self.app.sendEvent(area.viewport(), entering)
            said = "%s %s" % (type(area).__name__, area.objectName())
            self.assertTrue(entering.isAccepted(), said)
            # Taken by the window, which says so, and not by the box.
            self.assertFalse(self.window.drop_hint.isHidden(), said)
            self.window.dragLeaveEvent(QDragLeaveEvent())


class TestTheConversionInTheWindow(_Folders):
    """Profiles become profiles of the loadout; the library's mods the
    loadout does not mention are in the row for every such mod."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import launch
        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock(source_path=str(self.root / "assets.xml"), asset_count=0, text_count=0)
        self.addCleanup(setattr, self.window, "index", None)
        self.loadouts = self.root / "loadouts"
        self.loadouts.mkdir()
        for target, name, value in (
            (self.window.where, "mods_folder", str(self.mods)),
            (self.window.files, "loadouts_dir", self.loadouts),
            (launch, "install_for", None),
        ):
            patched = mock.patch.object(target, name, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.set_up_as("manage")
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout_path = self.loadouts / "Main.annoload"
        self.addCleanup(self.window.files.mark_saved)

    def test_profiles_chosen_in_the_survey_become_profiles_of_this_loadout(self):
        from anno_modding_studio.core import convert

        install_mod(self.mods, "[Gameplay] One", "one", "1")
        install_mod(self.mods, "[Gameplay] Two", "two", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] One", mod_id="one"),
                                      ModLayer(origin="[Gameplay] Two", mod_id="two")]
        profile = convert.FoundProfile(name="Only one", source="iModYourAnno", path=self.root / "p",
                                       on={"[gameplay] one"})
        said = []
        with mock.patch.object(self.window.prompts, "ask_convert", side_effect=lambda surveyed, *a, **k: (surveyed.to_do, [profile])), \
                mock.patch.object(self.window.prompts, "report_conversion",
                                  side_effect=lambda text: said.append(widgets.plain_text(text))), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.library_controller.convert_to_library())
        # A second conversion finds the same profile, and says the loadout has it.
        with mock.patch.object(convert, "profiles", return_value=[profile]), \
                mock.patch.object(self.window.prompts, "ask_convert", return_value=None) as asked:
            self.window.library_controller.convert_to_library()
        self.assertTrue(asked.call_args[0][2][0].here)
        # Not a loadout of its own: one mod list, switched two ways.
        self.assertFalse((self.loadouts / "Only one.annoload").exists())
        loadout = self.window.loadout
        self.assertEqual(loadout.profile_names(), ["Main", "Only one"])
        self.assertEqual(loadout.switches_of("Only one"), {"[Gameplay] One": True, "[Gameplay] Two": False})
        self.assertIn("Your profile Only one is now a profile of this loadout.", said[0])
        # Saved with it, since undoing the conversion reads them back out.
        self.assertEqual(Loadout.load(self.loadouts / "Main.annoload").profile_names(),
                         ["Main", "Only one"])
        # The chosen one is as it was, and both mods are links now.
        self.assertEqual(loadout.profile, "Main")
        self.assertTrue(all(layer.active for layer in loadout.layers))
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Two"))
        # What the game loaded before is kept beside the library.
        self.assertEqual(len(list((self.library / "conversions").glob("*.json"))), 1)

    def test_a_full_copy_first_and_then_undone_everything_is_as_it_was(self):
        from anno_modding_studio.core import convert

        install_mod(self.mods, "[Gameplay] One", "one", "1")
        install_mod(self.mods, "-[Gameplay] Two", "two", "1")
        # A file the pinned backups do not keep (they keep modinfo.json and
        # patches), so only the mod's own folder moved back brings it back.
        for name in ("[Gameplay] One", "-[Gameplay] Two"):
            (self.mods / name / "data" / "own.txt").write_text(name, encoding="utf-8")
        before = sorted(p.name for p in self.mods.iterdir())
        listing = {path.relative_to(self.mods).as_posix(): path.read_bytes()
                   for path in sorted(self.mods.rglob("*")) if path.is_file()}
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] One", mod_id="one", folder=str(self.mods / "[Gameplay] One")),
                                      ModLayer(origin="[Gameplay] Two", mod_id="two", active=False,
                                               folder=str(self.mods / "-[Gameplay] Two"))]
        profile = convert.FoundProfile(name="Only one", source="iModYourAnno", path=self.root / "p",
                                       on={"[gameplay] one"})
        said = []
        with mock.patch.object(self.window.prompts, "ask_convert",
                               side_effect=lambda surveyed, *a, **k: (surveyed.to_do, [profile], True)), \
                mock.patch.object(self.window.prompts, "report_conversion",
                                  side_effect=lambda text: said.append(widgets.plain_text(text))), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.library_controller.convert_to_library())
        self.assertIn("Undo the conversion to the library", said[0])
        # Where its mods are now is saved with the loadout, which is what
        # undoing it reads back: closed without saving, it used to go on
        # naming the folders the conversion had moved.
        saved = {layer.origin: layer.folder
                 for layer in Loadout.load(self.loadouts / "Main.annoload").layers}
        self.assertEqual(saved["[Gameplay] Two"], str(self.mods / "[Gameplay] Two"))
        full = list((self.root / "backups" / convert.FULL).glob("*/full-copy.json"))
        self.assertEqual(len(full), 1)
        self.assertIn("A full copy of your mods folder is at", said[0])
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] One"))
        self.assertTrue(self.window.library_controller.library_used())

        undone = []
        with mock.patch.object(self.window.prompts, "ask_undo_conversion", return_value=True) as asked, \
                mock.patch.object(self.window.prompts, "report_undo",
                                  side_effect=lambda text: undone.append(widgets.plain_text(text))), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.library_controller.undo_conversion())
        _record, count, made, profiles = asked.call_args[0]
        self.assertEqual(count, {"move": 2, "copy": 0, "aside": 0})
        self.assertEqual((made, profiles), ([], ["Only one"]))
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), before)
        self.assertFalse(any(library.is_junction(p) for p in self.mods.iterdir()))
        self.assertEqual({path.relative_to(self.mods).as_posix(): path.read_bytes()
                          for path in sorted(self.mods.rglob("*")) if path.is_file()}, listing)
        # The loadout follows its folders back, and the profile made from
        # yours, which did not exist before, is gone again.
        self.assertEqual({layer.origin: layer.folder for layer in self.window.loadout.layers},
                         {"[Gameplay] One": str(self.mods / "[Gameplay] One"),
                          "[Gameplay] Two": str(self.mods / "-[Gameplay] Two")})
        self.assertEqual(self.window.loadout.profile_names(), ["Main"])
        self.assertEqual(Loadout.load(self.loadouts / "Main.annoload").profile_names(), ["Main"])
        self.assertEqual(library.live(self.library), "")
        self.assertIn("Profiles it made from yours, deleted: Only one.", undone[0])
        self.assertIn("The full copy kept before it is still at", undone[0])
        # And the Studio meets the folder as if for the first time.
        self.assertFalse(self.window.library_controller.library_used())
        with mock.patch.object(QMessageBox_for_tests(), "information") as told:
            self.assertFalse(self.window.library_controller.undo_conversion())
        told.assert_called_once()

    def test_a_library_mod_and_a_folder_of_your_own_share_the_one_row(self):
        """They had a row each, with the same button on both."""
        library.install(self.archive("[Gameplay] Lonely"), self.library, self.mods)
        install_mod(self.mods, "[Gameplay] Dropped", "dropped", "1")
        self.window.refresh_truth()
        card = self.window.source
        self.assertFalse(card.strangers_row.isHidden())
        self.assertEqual(
            card.strangers_said.text(),
            "<b>2 mods in the mods folder are not in this loadout: "
            "[Gameplay] Dropped, [Gameplay] Lonely</b>")
        self.assertNotIn("unmentioned", card.checklist)
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            card.strangers_button.click()
        # Both are linked in, so the folder's read finds them: no library
        # mod is read beside it.
        read.assert_called_once_with(
            focus={"[Gameplay] Lonely", "[Gameplay] Dropped"}, keep_switches=True, adding=[])
        # In this loadout, neither is in the row.
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Lonely"),
                                      ModLayer(origin="[Gameplay] Dropped", mod_id="dropped")]
        self.window.refresh_truth()
        self.assertTrue(card.strangers_row.isHidden())


if __name__ == "__main__":
    unittest.main()


class TestExportingIntoAModsFolder(_Folders):
    """Play's build lands in the library, linked into the mods folder; an
    export goes anywhere but a mods folder, as the folder it always was."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        tools = ROOT / "tools"
        sys.path.insert(0, str(tools))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._class_tmp = tempfile.TemporaryDirectory()
        base = Path(cls._class_tmp.name)
        cls.assets = base / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = base / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.cleanup()

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.addCleanup(self.window.files.mark_saved)
        self.window.loadout.game = ANNO_1800
        patched = mock.patch.object(self.window.where, "library_dir", return_value=self.library)
        patched.start()
        self.addCleanup(patched.stop)
        self.window.output.name_edit.setText("Silent Mod")
        self.window.output.category_combo.setCurrentText("Map")

    def _edit(self):
        """One value changed in the editor, the way the UI tests do it."""
        self.window._open_asset("1010340")
        for item in self.window.editor._iter_items():
            if item.text(0) == "CycleTime" and item.data(0, 258) is not None:  # ORIG_ROLE
                item.setText(1, "15.00")
                break
        else:
            self.fail("no editable CycleTime")
        self.window.output.refresh()

    def _play_build(self):
        """Play's step: the loadout's own mod built into the mods folder."""
        from anno_modding_studio.core import build

        build.back_up(self.window.loadout, [""], self.mods, into=self.window.output.backup_into)
        built = build.write(self.window.loadout, [""], self.mods, self.library)
        return built.written, built.failed

    def test_a_build_is_the_librarys_folder_and_the_games_link(self):
        self._edit()
        (root,), failed = self._play_build()
        self.assertEqual(failed, [])
        self.assertEqual(root, self.mods / "[Map] Silent Mod")
        self.assertTrue(library.is_junction(root))
        self.assertEqual(library.kind_of(root, self.library), library.COPY)
        self.assertTrue((self.library / "[Map] Silent Mod" / "modinfo.json").is_file())
        # And the library says it is the project's to write again: which
        # project, the ModID and build it was built as, and what it wrote.
        entry = library.registry(self.library)["[Map] Silent Mod"]
        self.assertEqual(entry, {
            "source": library.BUILT, library.PROJECT: self.window.loadout.projects[""].path,
            library.MOD_ID: "Silent-Mod", library.BUILD: 1,
            library.DIGEST: self.window.loadout.builds[""]["digest"],
        })
        # Building again writes through the link, into the same library folder.
        (again,), _failed = self._play_build()
        self.assertEqual(again, root)
        self.assertEqual([p.name for p in self.library.iterdir() if p.is_dir()], ["[Map] Silent Mod"])

    def test_saved_as_another_file_the_build_goes_with_its_project(self):
        """The own mod of a loadout saved as another file is a project of
        its own, and takes what its project built with it."""
        from PySide6.QtWidgets import QFileDialog

        self._edit()
        first = self.root / "loadouts" / "First.annoload"
        self.window.loadout_path = first
        self.assertTrue(self.window.files.save_loadout())
        self._play_build()
        was = self.window.loadout.projects[""].path
        copy = self.root / "loadouts" / "Copy.annoload"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(copy), "")):
            self.assertTrue(self.window.files.save_loadout(ask=True))
        now = self.window.loadout.projects[""].path
        self.assertNotEqual(now, was)
        self.assertEqual(library.built_by(self.library, "[Map] Silent Mod"), now)

    def test_a_loadout_opened_after_its_mod_moved_follows_it(self):
        self._edit()
        self.window.loadout_path = self.root / "loadouts" / "First.annoload"
        self.assertTrue(self.window.files.save_loadout())
        self._play_build()
        self.window.output.name_edit.setText("Loud Mod")
        (moved,), failed = self._play_build()
        self.assertEqual(failed, [])
        self.assertEqual(moved.name, "[Map] Loud Mod")
        other = Loadout(game=ANNO_1800)
        other.set_layer(ModLayer(origin="[Map] Silent Mod", name="Silent Mod", mod_id="Silent-Mod",
                                 folder=str(self.mods / "[Map] Silent Mod")))
        path = self.root / "loadouts" / "Other.annoload"
        other.save(path)
        self.window.adopt_loadout(path, Loadout.load(path))
        self.assertIsNotNone(self.window.loadout.layer("[Map] Loud Mod"))
        self.assertIsNone(self.window.loadout.layer("[Map] Silent Mod"))
        # Its ModID was made up from its name, and went with it.
        self.assertEqual(self.window.loadout.layer("[Map] Loud Mod").mod_id, "Loud-Mod")
        # Saved at once, as following a conversion is.
        self.assertIsNotNone(Loadout.load(path).layer("[Map] Loud Mod"))
        self.assertIn("[Map] Loud Mod", self.window.status_label.text())

    def test_export_will_not_write_into_a_mods_folder(self):
        from PySide6.QtWidgets import QMessageBox

        self.window.output.set_default_output_dir(self.mods)
        self.assertNotEqual(self.window.output.export_dir(), self.mods)
        said = []
        with mock.patch.object(QMessageBox, "information", staticmethod(lambda _p, _t, text, *a: said.append(text))):
            self.assertFalse(self.window.output.choose_destination(self.mods))
        self.assertIn("press Play", said[0])
        self.assertNotEqual(self.window.output.export_dir(), self.mods)
        self.assertTrue(self.window.output.choose_destination(self.root / "elsewhere"))

    def test_anywhere_else_is_an_ordinary_folder(self):
        self._edit()
        out = self.root / "exports"
        self.window.output.set_default_output_dir(out)
        root = self.window.output.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(root, out / "[Map] Silent Mod")
        self.assertFalse(library.is_junction(root))
        self.assertFalse(self.library.exists())

    def test_a_real_folder_of_the_users_at_that_name_is_written_over_as_before(self):
        self._edit()
        theirs = install_mod(self.mods, "[Map] Silent Mod", "silent", "0.9")
        self.window.output.backup_into = self.root / "backups"
        (root,), _failed = self._play_build()
        self.assertEqual(root, theirs)
        self.assertFalse(library.is_junction(root))
        self.assertFalse(self.library.exists())


class TestPuttingBack(_Folders):
    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.loadout.game = ANNO_1800
        patched = mock.patch.object(self.window.where, "mods_folder", return_value=str(self.mods))
        patched.start()
        self.addCleanup(patched.stop)

    def test_asks_first_and_then_makes_the_folder_whole(self):
        library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        install_mod(self.mods, "[Gameplay] Theirs", "theirs", "1")
        with mock.patch.object(self.window.prompts, "confirm_put_back", return_value=False):
            self.assertFalse(self.window.library_controller.put_library_back())
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))
        listed = []
        with mock.patch.object(self.window.prompts, "confirm_put_back",
                               side_effect=lambda held: listed.extend(e.name for e in held) or True):
            self.assertTrue(self.window.library_controller.put_library_back())
        self.assertEqual(listed, ["[Gameplay] Thing"])
        self.assertFalse(library.is_junction(self.mods / "[Gameplay] Thing"))
        self.assertTrue((self.mods / "[Gameplay] Thing" / "modinfo.json").is_file())
        self.assertIn("1 mod is a real folder", self.window.status_label.text())

    def test_not_while_the_game_is_running(self):
        """Putting back turns links into the folders the game is reading."""
        library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        with mock.patch.object(self.window.prompts, "game_running", return_value=True), \
                mock.patch.object(self.window.prompts, "confirm_put_back") as asked:
            self.assertFalse(self.window.library_controller.put_library_back())
        asked.assert_not_called()
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))

    def test_the_librarys_root_is_a_setting_the_window_hands_on(self):
        self.assertEqual(self.window.preferences.library_root(), "")
        self.window.preferences.set_library_root(str(self.root / "elsewhere"))
        self.assertEqual(self.window.preferences.library_root(), str(self.root / "elsewhere"))
        # The environment still wins, as it does for every other folder in the
        # suite; without it the choice would.
        with mock.patch.dict(os.environ, {library.ENV_LIBRARY: ""}):
            self.assertEqual(self.window.where.library_dir(), self.root / "elsewhere" / "anno1800")

    def test_what_is_live_is_read_from_the_library_where_it_was_moved(self):
        # The pointer lives beside the library, so a library moved in the
        # settings is where it is read -- not the default place, which the
        # environment hides from every other test in the suite.
        elsewhere = self.root / "elsewhere"
        self.window.preferences.set_library_root(str(elsewhere))
        loadout = self.root / "Live one.json"
        Loadout(game=ANNO_1800).save(loadout)
        with mock.patch.dict(os.environ, {library.ENV_LIBRARY: ""}):
            library.set_live(library.game_dir(ANNO_1800, str(elsewhere)), loadout,
                             profile="Evenings")
            self.assertEqual(self.window.live.live_for(ANNO_1800), loadout)
            self.assertEqual(self.window.live.live_profile_shown(ANNO_1800), "Evenings")


class _PickerCase(_Folders):
    """A window over a game index, and the mods-folder dialog read over
    ``self.mods``."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._class_tmp = tempfile.TemporaryDirectory()
        base = Path(cls._class_tmp.name)
        cls.assets = base / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = base / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.cleanup()

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.window.loadout.game = ANNO_1800

    def mod(self, name: str, mod_id: str, residents: int = 12) -> Path:
        """A mod with one readable change; each its own value, so no two
        mods' changes are the same change."""
        folder = install_mod(self.mods, name, mod_id, "1")
        (folder / "data" / "config" / "export" / "main" / "asset").mkdir(parents=True)
        (folder / "data" / "config" / "export" / "main" / "asset" / "assets.xml").write_text(
            '<ModOps><ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>%d</Residents></ModOp></ModOps>'
            % residents,
            encoding="utf-8",
        )
        return folder

    def dialog(self, focus=None, arrived=None, keep=None, library_dir=None, picked=None, adding=None,
               shelf=None):
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        dialog = ModsFolderDialog(
            self.window.index, self.window, start_dir=str(self.mods), game=ANNO_1800,
            focus=focus, provenance=self.window.loadout.provenance,
            listed=self.window.loadout.listed, arrived=arrived, keep=keep,
            library_dir=library_dir, picked=picked, adding=adding, shelf=shelf,
        )
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        # Until the read has landed in the table, not only until the worker
        # is done: its result arrives as a signal the event loop delivers.
        for _ in range(200):
            if dialog._worker is not None and dialog._worker.isRunning():
                dialog._worker.wait(5000)
            self.app.processEvents()
            if dialog.session is not None and dialog.table.rowCount():
                break
        return dialog

    def settle(self, dialog):
        """Until the reads a tick on the shelf asked for have landed."""
        for _ in range(200):
            self.app.processEvents()
            if dialog._worker is not None:
                dialog._worker.wait(5000)
                self.app.processEvents()
            if dialog._worker is None and not dialog._shelf_timer.isActive() and not dialog._read_again:
                break

    def tick_shelved(self, dialog, name):
        from anno_modding_studio.ui.mods_folder_dialog import SHELF_ROLE

        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(SHELF_ROLE) == name:
                item.setCheckState(Qt.CheckState.Checked)
                break
        else:
            self.fail("no row on the shelf for %s" % name)
        self.settle(dialog)

    def rows(self, dialog):
        found = {}
        for row in range(dialog.table.rowCount()):
            name = dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            found[name] = (
                dialog.table.item(row, 0).checkState() == Qt.CheckState.Checked,
                dialog.table.item(row, 2).text(),
            )
        return found


class TestReadingTheFolderAfterACollection(_PickerCase):
    """The folder read that follows a collection starts with the collection's
    mods ticked and names them; the rest of the folder is there, unticked."""

    def test_a_mod_the_game_skips_says_what_the_dialog_does_with_it(self):
        """Off in game by its folder's name, it comes in off and is said to;
        kept on, it comes in on, and was said to come in "switched off"."""
        self.mod("-[Gameplay] Skipped", "skipped", 12)

        def said(**how):
            dialog = self.dialog(**how)
            (row,) = [row for row in range(dialog.table.rowCount())
                      if "Skipped" in dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole)]
            origin = dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.assertEqual(dialog.table.item(row, 3).text(), "off")
            return origin, dialog.table.item(row, 3).toolTip()

        origin, tip = said()
        self.assertIn("name starts with “-”, so Anno 1800 skips it.", tip)
        self.assertIn("It is still read in and listed, switched off", tip)
        _origin, tip = said(keep={origin: True})
        self.assertIn("name starts with “-”, so Anno 1800 skips it.", tip)
        self.assertIn("It comes into the loadout switched on all the same.", tip)
        self.assertNotIn("switched off", tip)

    def test_a_mod_you_had_that_the_collection_lists_says_so_dimmer(self):
        self.mod("[Gameplay] One", "one", 12)
        self.mod("[Gameplay] Mine", "mine", 20)
        self.window.loadout.note_collection("Jakob's", ["[Gameplay] One", "[Gameplay] Mine"],
                                            brought=["[Gameplay] One"])
        rows = self.rows(self.dialog())
        self.assertEqual(rows["[Gameplay] One"][1], "Jakob's")
        self.assertEqual(rows["[Gameplay] Mine"][1], "also in Jakob's")

    def test_with_a_focus_only_its_mods_start_ticked_and_the_column_says_from_where(self):
        self.mod("[Gameplay] One", "one", 12)
        self.mod("[Gameplay] Two", "two", 20)
        self.mod("[Gameplay] Theirs", "theirs", 30)
        self.window.loadout.provenance = {"[Gameplay] One": "Jakob's", "[Gameplay] Two": "Jakob's"}
        dialog = self.dialog(focus={"[Gameplay] One", "[Gameplay] Two"})
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] One": (True, "Jakob's"),
            "[Gameplay] Two": (True, "Jakob's"),
            "[Gameplay] Theirs": (False, ""),
        })
        self.assertFalse(dialog.focus_label.isHidden())
        self.assertIn("2 mods that just arrived from Jakob's are ticked", dialog.focus_label.text())
        self.assertEqual(dialog.load_button.text(), "Import 2 mods (2 changes)")

    def test_one_new_mod_beside_the_loadouts_own_is_counted_as_one(self):
        """It said every ticked mod had just arrived: 34 of them, for one."""
        self.mod("[Gameplay] New", "new", 12)
        self.mod("[Gameplay] Had", "had", 20)
        dialog = self.dialog(focus={"[Gameplay] New", "[Gameplay] Had"}, arrived={"[Gameplay] New"},
                             keep={"[Gameplay] Had": True})
        self.assertIn("1 mod is new to this loadout.", dialog.focus_label.text())
        self.assertEqual(dialog.load_button.text(), "Import 2 mods (2 changes)")

    def test_a_mod_that_reads_as_no_change_is_listed_all_the_same(self):
        """Left out of the list, it could not be read into a loadout at all."""
        self.mod("[Gameplay] One", "one", 12)
        empty = install_mod(self.mods, "[Gameplay] Path Only", "path-only", "1")
        patch = empty / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps></ModOps>", encoding="utf-8")
        rows = self.rows(self.dialog())
        self.assertEqual(sorted(rows), ["[Gameplay] One", "[Gameplay] Path Only"])
        # Nothing read from it, so it starts unticked; it is there to tick.
        self.assertFalse(rows["[Gameplay] Path Only"][0])

    def test_without_a_focus_everything_starts_ticked_as_before(self):
        self.mod("[Gameplay] One", "one", 12)
        self.mod("[Gameplay] Theirs", "theirs", 30)
        self.window.loadout.provenance = {"[Gameplay] One": "Jakob's"}
        dialog = self.dialog()
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] One": (True, "Jakob's"),
            "[Gameplay] Theirs": (True, ""),
        })
        self.assertTrue(dialog.focus_label.isHidden())

    def test_the_window_hands_the_collections_folders_on_as_the_focus(self):
        from anno_modding_studio.core import modio

        fake = mock.Mock()
        fake.placed = [self.mods / "[Gameplay] One", self.mods / "-[Gameplay] Two"]
        fake.set_aside = []
        # Every mod of the collection that is there now, fetched or already
        # installed: the focus and the provenance are the whole of it.
        fake.present = ["[Gameplay] One", "[Gameplay] Two", "[Gameplay] Already"]
        # And of those, what it brought: "Already" was installed before it.
        fake.brought = ["[Gameplay] One", "[Gameplay] Two"]
        fake.collection = modio.CollectionSummary(id=39, name="Jakob's")
        with mock.patch.object(self.window.prompts, "ask_read_folder", return_value=True), \
                mock.patch.object(self.window, "load_installed_mods", return_value=False) as read:
            self.window.modio.after_collection(fake)
        read.assert_called_once_with(
            focus={"[Gameplay] One", "[Gameplay] Two", "[Gameplay] Already"},
            arrived={"[Gameplay] One", "[Gameplay] Two", "[Gameplay] Already"},
            keep_switches=False,
        )
        self.assertEqual(self.window.loadout.provenance, {
            "[Gameplay] One": "Jakob's", "[Gameplay] Two": "Jakob's",
        })
        self.assertEqual(self.window.loadout.listed_in("[Gameplay] Already"), ["Jakob's"])
        # With a folder already loaded, the whole of it is re-read as before.
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Theirs")]
        with mock.patch.object(self.window, "load_installed_mods", return_value=False) as read:
            self.window.modio.after_collection(fake)
        # Keeping this loadout's own switches: the folder's are the live one's.
        read.assert_called_once_with(
            focus=None, arrived={"[Gameplay] One", "[Gameplay] Two", "[Gameplay] Already"},
            keep_switches=True,
        )
        self.window.files.mark_saved()


class TestABundledModInThePicker(_PickerCase):
    """A mod bundled inside another is part of it in the mods-folder dialog,
    as in the Mod changes panel: drawn below it however the list is sorted,
    and ticked with it.  The game loads it while its host is on, so a tick
    of its own let a loadout read the host without it."""

    def setUp(self):
        super().setUp()
        self.host = self.mod("[Gameplay] Host", "host", 12)
        inner = install_mod(self.host, "[Shared] Piece", "piece", "1")
        patch = inner / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            '<ModOps><ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
            "<Residents>20</Residents></ModOp></ModOps>",
            encoding="utf-8",
        )
        self.mod("[Gameplay] Other", "other", 30)

    def row(self, dialog, word):
        (row,) = [row for row in range(dialog.table.rowCount())
                  if word in dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole).split("/")[-1]]
        return row

    def rows_of(self, dialog):
        return self.row(dialog, "Host"), self.row(dialog, "Piece"), self.row(dialog, "Other")

    def ticked(self, dialog, row):
        return dialog.table.item(row, 0).checkState() == Qt.CheckState.Checked

    def test_it_is_drawn_straight_below_its_host_however_it_is_sorted(self):
        dialog = self.dialog()
        host, piece, _other = self.rows_of(dialog)
        self.assertEqual(dialog.table.item(piece, 1).text().strip(), "└ [Shared] Piece")
        self.assertEqual(dialog.table.item(piece, 2).text(), "inside [Gameplay] Host")
        self.assertIn("Bundled inside [Gameplay] Host", dialog.table.item(piece, 1).toolTip())
        vertical = dialog.table.verticalHeader()

        def below():
            return vertical.visualIndex(piece) == vertical.visualIndex(host) + 1

        self.assertTrue(below())
        for column in (1, 4, 6):
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                dialog.sorting.sort(column, order)
                self.assertTrue(below(), (column, order))
        dialog.sorting.sort(-1)
        self.assertTrue(below())

    def test_its_tick_is_its_hosts(self):
        from anno_modding_studio.ui.mods_folder_dialog import HOST_ROLE

        dialog = self.dialog()
        host, piece, other = self.rows_of(dialog)
        tick = dialog.table.item(piece, 0)
        self.assertFalse(tick.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(tick.flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertEqual(tick.data(HOST_ROLE), dialog.table.item(host, 0).data(Qt.ItemDataRole.UserRole))
        self.assertIn("Ticked with [Gameplay] Host", tick.toolTip())
        self.assertTrue(self.ticked(dialog, piece))
        dialog.table.item(host, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(self.ticked(dialog, piece))
        self.assertEqual([read.name for read in dialog.chosen()], ["[Gameplay] Other"])
        dialog.table.item(host, 0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(self.ticked(dialog, piece))
        # Tick none and Tick all go by the host too.
        dialog._tick_shown(False)
        self.assertFalse(self.ticked(dialog, piece))
        dialog._tick_shown(True)
        self.assertTrue(self.ticked(dialog, piece))
        self.assertTrue(self.ticked(dialog, other))

    def test_a_read_again_keeps_the_hosts_tick_for_both(self):
        dialog = self.dialog()
        host, _piece, _other = self.rows_of(dialog)
        dialog.table.item(host, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog.reload()
        self.settle(dialog)
        host, piece, other = self.rows_of(dialog)
        self.assertFalse(self.ticked(dialog, host))
        self.assertFalse(self.ticked(dialog, piece))
        self.assertTrue(self.ticked(dialog, other))

    def test_the_filter_shows_it_with_its_host(self):
        dialog = self.dialog()
        host, piece, other = self.rows_of(dialog)
        dialog.filter_box.setText("piece")
        self.assertEqual([dialog.table.isRowHidden(row) for row in (host, piece, other)],
                         [False, False, True])
        dialog.filter_box.setText("[gameplay] host")
        self.assertEqual([dialog.table.isRowHidden(row) for row in (host, piece, other)],
                         [False, False, True])
        dialog.filter_box.setText("other")
        self.assertEqual([dialog.table.isRowHidden(row) for row in (host, piece, other)],
                         [True, True, False])

    def test_it_comes_into_the_loadout_switching_with_its_host(self):
        dialog = self.dialog()
        dialog._accept()
        self.window.loadout.layers = list(dialog.layers)
        by_name = {layer.origin: layer for layer in dialog.layers}
        self.assertEqual(set(by_name), {
            "[Gameplay] Host", "[Gameplay] Host/[Shared] Piece", "[Gameplay] Other",
        })
        piece = by_name["[Gameplay] Host/[Shared] Piece"]
        self.assertIs(self.window.loadout.switch_of(piece.origin), by_name["[Gameplay] Host"])
        self.assertEqual(piece.active, by_name["[Gameplay] Host"].active)
        self.window.loadout.layers = []

    def test_in_a_host_the_game_skips_it_is_off_and_says_why(self):
        self.host.rename(self.host.with_name("-" + self.host.name))
        dialog = self.dialog()
        piece = self.row(dialog, "Piece")
        self.assertEqual(dialog.table.item(piece, 3).text(), "off")
        self.assertIn("[Gameplay] Host, which it is bundled inside, is switched off",
                      dialog.table.item(piece, 3).toolTip())


class TestALoadoutPickedOutOfTheLibrary(TestReadingTheFolderAfterACollection):
    """Aim 3's third button: the library's own list, the mods with no link in
    the mods folder among them, and a read of the ones ticked in it."""

    def stock(self):
        library.adopt(self.library, self.mod("[Gameplay] On", "on", 12))
        off = library.adopt(self.library, self.mod("[Gameplay] Off", "off", 20))
        library.unlink(self.library, off.link)
        # A folder of the user's own: installed, and not the library's.
        self.mod("[Gameplay] Theirs", "theirs", 30)

    def test_the_library_holds_both_and_says_which_is_linked(self):
        self.stock()
        self.assertEqual(
            [(mod.name, mod.linked, mod.link, mod.target) for mod in library.held(self.library)],
            [("[Gameplay] Off", False, self.mods / "[Gameplay] Off", self.library / "[Gameplay] Off"),
             ("[Gameplay] On", True, self.mods / "[Gameplay] On", self.library / "[Gameplay] On")],
        )
        self.assertEqual(library.links_into(self.library), self.mods)
        self.assertIsNone(library.links_into(self.root / "nowhere"))

    def test_a_mod_with_no_link_is_read_where_it_is_kept_under_its_links_name(self):
        self.stock()
        from anno_modding_studio.core import reader

        # Kept under another name than its link's, as a replaced mod is.
        kept = self.library / "[Gameplay] Off"
        session = reader.analyse_all(
            [kept], self.window.index, game=ANNO_1800, roots=[self.mods],
            unlinked={kept: "[Gameplay] Renamed"},
        )
        self.assertEqual([(read.origin, len(read.changes)) for read in session.reads],
                         [("[Gameplay] Renamed", 1)])

    def test_a_mod_with_no_link_is_found_where_it_is_kept_by_where_its_link_would_be(self):
        """R3-46, hands-on run 3: added switched off, its layer names where
        Play puts its link back, which is no folder until then; its options
        could not be asked, nor could it be read again."""
        self.stock()
        from anno_modding_studio.core import stale
        from anno_modding_studio.core.changes import Loadout, ModLayer

        link = self.mods / "[Gameplay] Off"
        kept = self.library / "[Gameplay] Off"
        self.assertFalse(link.exists())
        self.assertEqual(library.kept_at(self.library, link), kept)
        (kept / "inside").mkdir()
        self.assertEqual(library.kept_at(self.library, link / "inside"), kept / "inside")
        self.assertIsNone(library.kept_at(self.library, self.root / "elsewhere" / "[Gameplay] Off"))
        self.assertEqual(library.reading_folder(self.library, link), kept)
        self.assertEqual(library.reading_folder(self.library, self.mods / "[Gameplay] On"),
                         self.mods / "[Gameplay] On")
        loadout = Loadout(game=ANNO_1800)
        loadout.set_layer(ModLayer(origin="[Gameplay] Off", name="Off", folder=str(link)))
        taken = stale.snapshot(loadout, asked=["[Gameplay] Off"], library_dir=self.library)
        self.assertEqual(taken.layer("[Gameplay] Off").folder, str(kept))
        # The loadout's own record still says where the link goes back.
        self.assertEqual(loadout.layer("[Gameplay] Off").folder, str(link))

    def shown(self, dialog):
        return [
            dialog.table.item(row, 1).text() for row in range(dialog.table.rowCount())
            if not dialog.table.isRowHidden(row)
        ]

    def test_from_card_3_every_mod_is_named_and_none_is_read(self):
        self.stock()
        with mock.patch("anno_modding_studio.core.reader.analyse_all") as read:
            dialog = self.dialog(library_dir=self.library, picked=[],
                                 shelf=library.catalogue(self.library))
        read.assert_not_called()
        self.assertIsNone(dialog.session)
        self.assertEqual(self.shown(dialog), ["[Gameplay] Off", "[Gameplay] On"])
        self.assertEqual([dialog.table.item(row, 3).text() for row in range(2)], ["off", "on"])
        self.assertEqual([dialog.table.item(row, 5).text() for row in range(2)],
                         ["not read yet", "not read yet"])
        self.assertIn("read when you press Read ticked mods", dialog.status.text())
        # Nothing ticked, so nothing to import or read yet.
        self.assertFalse(dialog.load_button.isEnabled())
        self.assertTrue(dialog.read_ticked_button.isVisibleTo(dialog))
        self.assertFalse(dialog.read_ticked_button.isEnabled())

    def test_a_mod_ticked_there_waits_and_is_read_with_the_rest_when_asked(self):
        """Each tick read every mod ticked before it again and drew the list
        again: dozens ticked were dozens of reads, the list redrawn under
        each next tick.  They wait for Read ticked mods, or the import."""
        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=[],
                             shelf=library.catalogue(self.library))
        with mock.patch("anno_modding_studio.core.reader.analyse_all") as read:
            self.tick_shelved(dialog, "[Gameplay] Off")
            self.tick_shelved(dialog, "[Gameplay] On")
        read.assert_not_called()
        self.assertIsNone(dialog.session)
        self.assertEqual(dialog.load_button.text(), "Read and import 2 mods")
        self.assertTrue(dialog.read_ticked_button.isEnabled())
        dialog.read_ticked_button.click()
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] Off": (True, ""), "[Gameplay] On": (True, ""),
        })
        self.assertEqual(len(dialog.session.reads), 2)
        # Read, and not imported: there to look at first.
        self.assertEqual(dialog.result(), 0)
        self.assertFalse(dialog.read_ticked_button.isEnabled())
        dialog._accept()
        self.assertEqual(sorted((layer.origin, layer.active) for layer in dialog.layers),
                         [("[Gameplay] Off", True), ("[Gameplay] On", True)])

    def test_one_ticked_there_and_imported_is_read_and_comes_in_on(self):
        from PySide6.QtWidgets import QDialog

        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=[],
                             shelf=library.catalogue(self.library))
        self.tick_shelved(dialog, "[Gameplay] Off")
        self.assertEqual(dialog.load_button.text(), "Read and import 1 mod")
        dialog._accept()
        self.settle(dialog)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual([(layer.origin, layer.active) for layer in dialog.layers],
                         [("[Gameplay] Off", True)])

    def test_a_folders_list_has_no_read_ticked_button(self):
        """Only the library's own list waits: beside a folder the first tick
        reads, and ticks after it read nothing again (TestAddingModsFromTheLibrary)."""
        self.stock()
        dialog = self.dialog(shelf=library.catalogue(self.library))
        self.assertFalse(dialog.read_ticked_button.isVisibleTo(dialog))

    def test_ticking_them_all_there_reads_nothing_until_the_import(self):
        """Ticking every mod a full library holds read all of them, half a
        minute and more, before one could be unticked again.  They wait, and
        the import reads the ones the filter showed and are still ticked."""
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.mods_folder_dialog import SHELF_ROLE

        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=[],
                             shelf=library.catalogue(self.library))
        dialog.filter_box.setText("gameplay OFF")
        self.assertEqual(self.shown(dialog), ["[Gameplay] Off"])
        with mock.patch("anno_modding_studio.core.reader.analyse_all") as read:
            dialog.tick_box.click()
            self.settle(dialog)
        read.assert_not_called()
        self.assertIsNone(dialog.session)
        dialog.filter_box.setText("")
        self.assertEqual(len(self.shown(dialog)), 2)
        ticked = {
            dialog.table.item(row, 0).data(SHELF_ROLE): dialog.table.item(row, 0).checkState()
            for row in range(dialog.table.rowCount())
        }
        self.assertEqual(ticked, {"[Gameplay] Off": Qt.CheckState.Checked,
                                  "[Gameplay] On": Qt.CheckState.Unchecked})
        self.assertEqual(dialog.tick_box.checkState(), Qt.CheckState.PartiallyChecked)
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertEqual(dialog.load_button.text(), "Read and import 1 mod")
        dialog._accept()
        self.settle(dialog)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual([layer.origin for layer in dialog.layers], ["[Gameplay] Off"])

    def test_one_unticked_after_ticking_them_all_is_not_read(self):
        from anno_modding_studio.ui.mods_folder_dialog import SHELF_ROLE

        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=[],
                             shelf=library.catalogue(self.library))
        dialog.tick_box.click()
        self.assertEqual(dialog.load_button.text(), "Read and import 2 mods")
        row = next(row for row in range(dialog.table.rowCount())
                   if dialog.table.item(row, 0).data(SHELF_ROLE) == "[Gameplay] On")
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.load_button.text(), "Read and import 1 mod")
        dialog._accept()
        self.settle(dialog)
        self.assertEqual([layer.origin for layer in dialog.layers], ["[Gameplay] Off"])

    def test_only_the_mods_picked_are_read(self):
        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=["[Gameplay] Off"])
        self.assertEqual(self.rows(dialog), {"[Gameplay] Off": (True, "")})
        self.assertEqual(len(dialog.session.reads), 1)

    def test_the_read_is_of_the_library_and_says_which_mod_the_game_loads(self):
        self.stock()
        dialog = self.dialog(library_dir=self.library)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (True, ""),
            "[Gameplay] Off": (True, ""),
        })
        in_game = {
            dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole): dialog.table.item(row, 3)
            for row in range(dialog.table.rowCount())
        }
        self.assertEqual(in_game["[Gameplay] On"].text(), "on")
        self.assertEqual(in_game["[Gameplay] Off"].text(), "off")
        self.assertIn("no link in your mods folder", in_game["[Gameplay] Off"].toolTip())
        self.assertEqual(dialog.windowTitle(), "Start a loadout from the library")
        self.assertIn("Tick the mods this loadout holds", dialog.focus_label.text())
        # No folder to pick: the library is where it is.
        self.assertTrue(dialog.folder_box.isHidden())

    def test_the_ticked_mods_come_in_on_where_their_links_are_or_will_be(self):
        self.stock()
        dialog = self.dialog(library_dir=self.library, picked=["[Gameplay] On", "[Gameplay] Off"])
        self.assertEqual(dialog.load_button.text(), "Import 2 mods (2 changes)")
        dialog._accept()
        self.assertEqual(
            sorted((layer.origin, layer.active, layer.folder) for layer in dialog.layers),
            [("[Gameplay] Off", True, str(self.mods / "[Gameplay] Off")),
             ("[Gameplay] On", True, str(self.mods / "[Gameplay] On"))],
        )
        self.assertEqual(len(dialog.changes), 2)
        # Reading moved no link: that is Play's to do.
        self.assertFalse((self.mods / "[Gameplay] Off").exists())

    def test_an_empty_library_has_nothing_to_list(self):
        dialog = self.dialog(library_dir=self.root / "library" / "anno117")
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(dialog.status.text(), "The library holds no mods.")


class TestAddingModsFromTheLibrary(TestReadingTheFolderAfterACollection):
    """Adding mods to the loadout open now: the mods folder's list, the
    library's other mods named in it and read once ticked. Mods ▸ Add mods, and
    the Mod changes panel's menu."""

    def stock(self):
        """A loadout read from the folder: one library mod linked in and one
        a mod of the user's own, both in the loadout. The library has two
        more with no link, which the folder's read does not find."""
        library.adopt(self.library, self.mod("[Gameplay] On", "on", 12))
        self.mod("[Gameplay] Theirs", "theirs", 30)
        for name, mod_id, residents in (("[Gameplay] Off", "off", 20), ("[Gameplay] Spare", "spare", 40)):
            off = library.adopt(self.library, self.mod(name, mod_id, residents))
            library.unlink(self.library, off.link)
        self.window.loadout.own.loads_last = True
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] On", mod_id="on", folder=str(self.mods / "[Gameplay] On")),
            ModLayer(origin="[Gameplay] Theirs", mod_id="theirs",
                     folder=str(self.mods / "[Gameplay] Theirs")),
        ]
        self.addCleanup(self.window.loadout.clear)

    def held(self, name):
        return next(mod for mod in library.catalogue(self.library) if mod.name == name)

    def shelved(self):
        return [self.held("[Gameplay] Off"), self.held("[Gameplay] Spare")]

    def test_the_librarys_mods_with_no_link_are_listed_after_the_folder_and_not_read(self):
        self.stock()
        dialog = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"},
                             keep={"[Gameplay] On": True, "[Gameplay] Theirs": False},
                             shelf=self.shelved())
        self.assertEqual(sorted(read.origin for read in dialog.session.reads),
                         ["[Gameplay] On", "[Gameplay] Theirs"])
        self.assertEqual(
            [dialog.table.item(row, 1).text() for row in range(dialog.table.rowCount())][-2:],
            ["[Gameplay] Off", "[Gameplay] Spare"],
        )
        self.assertIn("2 mods in the library are listed by name", dialog.status.text())
        self.assertIn("The mods this loadout has are ticked", dialog.focus_label.text())
        # Tick all is of the folder: it reads nothing from the library.
        dialog._tick_shown(True)
        self.settle(dialog)
        self.assertEqual(len(dialog.session.reads), 2)

    def test_a_mod_ticked_there_is_read_beside_the_folder_and_the_ticks_are_kept(self):
        self.stock()
        dialog = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"},
                             keep={"[Gameplay] On": True, "[Gameplay] Theirs": False},
                             shelf=self.shelved())
        # Unticked by hand before the library mod is ticked: it stays so.
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "[Gameplay] Theirs":
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.tick_shelved(dialog, "[Gameplay] Off")
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (True, ""), "[Gameplay] Theirs": (False, ""),
            "[Gameplay] Off": (True, ""), None: (False, ""),
        })
        self.assertIn("1 mod is new to this loadout", dialog.focus_label.text())
        dialog._accept()
        self.assertEqual(
            sorted((layer.origin, layer.active, layer.folder) for layer in dialog.layers),
            [("[Gameplay] Off", True, str(self.mods / "[Gameplay] Off")),
             ("[Gameplay] On", True, str(self.mods / "[Gameplay] On"))],
        )
        # Reading moved no link: that is Play's to do.
        self.assertFalse((self.mods / "[Gameplay] Off").exists())

    def tick(self, dialog, origin, on):
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == origin:
                dialog.table.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )
                return
        self.fail("no row for %s" % origin)

    def test_the_ticks_are_kept_across_any_read_of_the_same_folder(self):
        """A read the keep-inert box or "Read mods" asked for put every tick
        back to how the dialog opens."""
        self.stock()
        dialog = self.dialog()
        self.tick(dialog, "[Gameplay] Theirs", False)
        dialog.keep_inert_box.setChecked(True)
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (True, ""), "[Gameplay] Theirs": (False, ""),
        })
        dialog.read_button.click()
        self.settle(dialog)
        self.assertFalse(self.rows(dialog)["[Gameplay] Theirs"][0])

    def test_a_tick_made_after_a_library_mod_was_read_is_kept_too(self):
        """The ticks as they were when the library mod was ticked were put
        back by every read after it, over the ones made since."""
        self.stock()
        dialog = self.dialog(shelf=self.shelved())
        self.tick(dialog, "[Gameplay] Theirs", False)
        self.tick_shelved(dialog, "[Gameplay] Off")
        self.assertFalse(self.rows(dialog)["[Gameplay] Theirs"][0])
        self.tick(dialog, "[Gameplay] Theirs", True)
        self.tick(dialog, "[Gameplay] On", False)
        dialog.keep_inert_box.setChecked(True)
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (False, ""), "[Gameplay] Theirs": (True, ""),
            "[Gameplay] Off": (True, ""), None: (False, ""),
        })

    def test_another_folder_starts_with_its_own_ticks(self):
        """The ticks are of the folder read: a mod of the same name in
        another folder is another mod."""
        self.stock()
        dialog = self.dialog()
        self.tick(dialog, "[Gameplay] Theirs", False)
        other = self.root / "other"
        other.mkdir()
        self.mods = other
        self.mod("[Gameplay] Theirs", "theirs", 30)
        dialog.folder_box.setText(str(other))
        dialog.read_button.click()
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {"[Gameplay] Theirs": (True, "")})

    def test_a_tick_the_dialog_left_off_itself_is_not_kept_off(self):
        """Only the ticks the user changed are theirs. A mod with nothing
        readable in it starts unticked, and was held unticked by every later
        read, though it had something to read by then."""
        self.stock()
        empty = self.mod("[Gameplay] Empty", "empty", 50)
        patch = empty / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        written = patch.read_text(encoding="utf-8")
        patch.write_text("<ModOps></ModOps>", encoding="utf-8")
        dialog = self.dialog()
        self.assertFalse(self.rows(dialog)["[Gameplay] Empty"][0])
        self.tick(dialog, "[Gameplay] Theirs", False)
        patch.write_text(written, encoding="utf-8")
        dialog.read_button.click()
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (True, ""), "[Gameplay] Empty": (True, ""),
            # And the one the user took out stays out.
            "[Gameplay] Theirs": (False, ""),
        })
        # Put back as the dialog had it, a tick is the dialog's again.
        self.tick(dialog, "[Gameplay] Theirs", True)
        self.tick(dialog, "[Gameplay] Empty", False)
        self.tick(dialog, "[Gameplay] Empty", True)
        patch.write_text("<ModOps></ModOps>", encoding="utf-8")
        dialog.read_button.click()
        self.settle(dialog)
        self.assertEqual(self.rows(dialog), {
            "[Gameplay] On": (True, ""), "[Gameplay] Empty": (False, ""),
            "[Gameplay] Theirs": (True, ""),
        })

    def test_the_loadouts_own_mods_a_collection_brought_are_not_said_to_have_just_arrived(self):
        """It said "2 mods that just arrived from Jakob's are ticked" of the
        two mods the loadout had, each time mods were added to it."""
        self.stock()
        self.window.loadout.provenance = {"[Gameplay] On": "Jakob's", "[Gameplay] Theirs": "Jakob's"}
        dialog = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"},
                             keep={"[Gameplay] On": True, "[Gameplay] Theirs": True},
                             shelf=self.shelved())
        self.assertIn("The mods this loadout has are ticked", dialog.focus_label.text())
        self.assertNotIn("just arrived", dialog.focus_label.text())
        self.tick_shelved(dialog, "[Gameplay] Off")
        self.assertIn("1 mod is new to this loadout", dialog.focus_label.text())
        self.assertNotIn("just arrived", dialog.focus_label.text())

    def test_a_mod_the_folder_has_off_ticked_to_add_comes_in_on(self):
        """The README: ticked, it is read in beside them, switched on.
        Airship Hangar, off in the folder by its "-", came in off."""
        self.stock()
        self.mod("-[Gameplay] Later", "later", 70)
        self.mod("-[Gameplay] Left", "left", 80)
        dialog = self.dialog(focus={"[Gameplay] On", "[Gameplay] Theirs"},
                             keep={"[Gameplay] On": True, "[Gameplay] Theirs": False})
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "[Gameplay] Later":
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog._accept()
        self.assertEqual({layer.origin: layer.active for layer in dialog.layers}, {
            "[Gameplay] On": True, "[Gameplay] Theirs": False, "[Gameplay] Later": True,
        })

    def test_one_with_no_link_that_did_not_just_arrive_comes_in_off(self):
        """As the game has it: no link, not loaded."""
        self.stock()
        dialog = self.dialog(adding=[self.held("[Gameplay] Spare")])
        dialog._accept()
        self.assertEqual(
            {layer.origin: layer.active for layer in dialog.layers}["[Gameplay] Spare"], False,
        )

    def test_into_a_loadout_the_folder_opens_with_the_loadouts_mods_ticked(self):
        self.stock()
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            self.assertTrue(self.window.library_controller.add_mods())
        # On what the loadout has, read already: the folder waits for a tick.
        read.assert_called_once_with(focus={"[Gameplay] On", "[Gameplay] Theirs"}, keep_switches=True,
                                     standing=True, detours=mock.ANY)
        # Read from the folder the loadout was read from.
        self.assertEqual(self.window.remembered(self.window.mods_key("last_mods_folder")), str(self.mods))

    def test_the_folders_list_names_the_rest_of_the_library(self):
        from anno_modding_studio.ui import mods_folder_dialog

        self.stock()
        seen = {}

        def opened(inner):
            seen["shelf"] = sorted(inner.shelf)
            seen["adding"] = [mod.name for mod in inner.adding]
            # The dialog reads the folder on a thread of its own. Left
            # reading, it still had a file of the test's folder open when
            # the folder was taken away, one run in three.
            if inner._worker is not None:
                inner._worker.wait(10000)
            return 0

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", opened):
            self.window.library_controller.add_mods()
        # Linked, On is in the folder; none is in the loadout switched off.
        self.assertEqual(seen, {"shelf": ["[Gameplay] Off", "[Gameplay] Spare"], "adding": []})

    def test_the_whole_way_the_loadout_keeps_its_mods_and_gains_the_one_ticked(self):
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mods_folder_dialog

        self.stock()
        # Switched off in this loadout, so Play took its link away.
        self.window.loadout.layers.append(ModLayer(
            origin="[Gameplay] Spare", mod_id="spare", active=False,
            folder=str(self.mods / "[Gameplay] Spare"),
        ))
        # And one the folder has on that this loadout has off: its switch is
        # the loadout's to keep, and only that tells the two apart.
        self.window.loadout.layers[1].active = False
        self.addCleanup(self.window.files.mark_saved)
        test = self

        def driven(inner):
            test.settle(inner)
            # The loadout's own, switched off, is read with it, not listed.
            test.assertEqual(sorted(inner.shelf), ["[Gameplay] Off"])
            test.tick_shelved(inner, "[Gameplay] Off")
            inner._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", driven):
            self.assertTrue(self.window.library_controller.add_mods())
        self.assertEqual(
            sorted((layer.origin, layer.active) for layer in self.window.loadout.layers),
            [("[Gameplay] Off", True), ("[Gameplay] On", True),
             ("[Gameplay] Spare", False), ("[Gameplay] Theirs", False)],
        )

    def test_tick_none_and_then_one_mod_still_adds_to_what_the_loadout_has(self):
        """It made a loadout of the one mod: the ticks are the whole read,
        and "Tick none" took the loadout's own with the rest."""
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mods_folder_dialog

        self.stock()
        self.addCleanup(self.window.files.mark_saved)
        had = sorted(layer.origin for layer in self.window.loadout.layers)
        test = self

        def held(inner):
            found = []
            for row in range(inner.table.rowCount()):
                item = inner.table.item(row, 0)
                if item.data(Qt.ItemDataRole.UserRole) in had:
                    test.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                    if item.checkState() == Qt.CheckState.Checked:
                        found.append(item.data(Qt.ItemDataRole.UserRole))
            return sorted(found)

        def driven(inner):
            test.settle(inner)
            # Before the folder is read: the loadout's rows, as they were read.
            inner._tick_shown(False)
            test.assertEqual(held(inner), had)
            test.tick_shelved(inner, "[Gameplay] Off")
            # And once it is.
            inner._tick_shown(False)
            test.assertEqual(sorted(read.origin for read in inner.chosen()), had)
            test.assertEqual(held(inner), had)
            test.tick(inner, "[Gameplay] Off", True)
            inner._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", driven):
            self.assertTrue(self.window.library_controller.add_mods())
        self.assertEqual(
            sorted(layer.origin for layer in self.window.loadout.layers),
            sorted([*had, "[Gameplay] Off"]),
        )

    def test_into_a_loadout_with_no_folder_read_it_is_the_one_mod_import(self):
        self.stock()
        self.window.loadout.own.loads_last = False
        self.window.loadout.mods_folder = ""
        with mock.patch.object(self.window, "import_mod", return_value=True) as imported:
            self.assertTrue(self.window.library_controller.add_mods())
        imported.assert_called_once_with(separate=True, detours=mock.ANY)

    def test_an_empty_loadout_starts_as_one_from_the_library_does(self):
        self.stock()
        self.window.loadout.layers = []
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            self.window.library_controller.add_mods()
        read.assert_called_once_with(picked=[], detours=mock.ANY)

    def test_and_is_named_as_one_from_the_library_is(self):
        """R3-21, hands-on run 3: it was "New loadout" holding "My Anno Mod",
        and Save offered "My Anno Mod.annoload"."""
        self.stock()
        self.window.loadout.layers = []
        self.window.loadout_path = None
        self.addCleanup(self.window.files.mark_saved)
        with mock.patch.object(self.window, "load_installed_mods", return_value=True):
            self.window.library_controller.add_mods()
        self.assertEqual(self.window.files.loadout_name(), "Anno 1800 loadout")
        self.assertEqual(self.window.loadout.own.name, "Anno 1800 loadout patch")

    def test_the_fourth_cards_button_reads_the_librarys_strangers_too(self):
        self.stock()
        self.window.refresh_truth()
        with mock.patch.object(self.window, "load_installed_mods", return_value=True) as read:
            self.window.library_controller.bring_strangers_in()
        self.assertEqual(
            sorted(mod.name for mod in read.call_args.kwargs["adding"]),
            ["[Gameplay] Off", "[Gameplay] Spare"],
        )


class TestTheFolderListItself(TestReadingTheFolderAfterACollection):
    """Game ▸ Match this loadout to the mods folder: its ticks, its rows and its
    size."""

    def ticks(self, dialog) -> dict:
        return {
            dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole):
                dialog.table.item(row, 0).checkState() == Qt.CheckState.Checked
            for row in range(dialog.table.rowCount())
        }

    def test_tick_all_leaves_out_a_mod_that_read_as_nothing_as_the_list_opened(self):
        """Tick none and Tick all was not the list as it opened: Tick all
        ticked the mods the dialog leaves unticked because their patch
        reads as nothing (H2-24). A mod the folder has switched off is
        ticked as the list opens, and comes in off."""
        from anno_modding_studio.ui.mods_folder_dialog import EMPTY_ROLE

        self.mod("[Gameplay] Alpha", "alpha", 12)
        empty = self.mod("[Gameplay] Empty", "empty", 50)
        (empty / "data" / "config" / "export" / "main" / "asset" / "assets.xml").write_text(
            "<ModOps></ModOps>", encoding="utf-8")
        self.mod("-[Gameplay] Gone", "gone", 60)
        dialog = self.dialog()
        opened = self.ticks(dialog)
        self.assertEqual(opened, {"[Gameplay] Alpha": True, "[Gameplay] Empty": False,
                                  "[Gameplay] Gone": True})
        dialog._tick_shown(False)
        self.assertEqual(set(self.ticks(dialog).values()), {False})
        dialog._tick_shown(True)
        self.assertEqual(self.ticks(dialog), opened)
        row = next(row for row in range(dialog.table.rowCount())
                   if dialog.table.item(row, 0).data(EMPTY_ROLE))
        self.assertIn("Ticking all leaves it out", dialog.table.item(row, 0).toolTip())
        # Ticked by hand, it is ticked.
        dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog._accept()
        self.assertEqual({layer.origin: layer.active for layer in dialog.layers}, {
            "[Gameplay] Alpha": True, "[Gameplay] Empty": True, "[Gameplay] Gone": False,
        })

    def test_sorted_and_filtered_every_row_shown_is_drawn(self):
        """Sorted by Mod and filtered on "Combined", the first of two rows
        was drawn blank, though it ticked when clicked (H2-5)."""
        from PySide6.QtWidgets import QStyledItemDelegate

        for at, name in enumerate(("[Gameplay] A One", "[Gameplay] B Fish", "[Gameplay] C Two",
                                   "[Gameplay] D Fish")):
            self.mod(name, "mod%d" % at, 12 + at)
        dialog = self.dialog()
        painted = set()

        class Recording(QStyledItemDelegate):
            def paint(self, painter, option, index):
                painted.add(index.row())
                super().paint(painter, option, index)

        dialog.table.setItemDelegateForColumn(0, Recording(dialog.table))
        dialog.show()
        self.addCleanup(dialog.hide)
        # Last in load order first, which puts the first row filled, hidden
        # by the filter, below the rows it shows.
        dialog.sorting.sort(4, Qt.SortOrder.DescendingOrder)
        dialog.filter_box.setText("fish")
        self.app.processEvents()
        shown = {row for row in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(row)}
        self.assertEqual(len(shown), 2)
        painted.clear()
        dialog.table.viewport().repaint()
        self.assertEqual(painted & shown, shown)

    def test_it_writes_down_the_size_it_was_left_at(self):
        """It opened small every time, the Mod column cut to 140 pixels."""
        self.mod("[Gameplay] Alpha", "alpha", 12)
        dialog = self.dialog()
        dialog.reject()
        self.assertEqual(
            self.window.settings.value("mods_folder_dialog_size"),
            "%d,%d" % (dialog.width(), dialog.height()),
        )


class TestWhatTheLibraryKnows(_Folders):
    """The registry: the Studio installed it, so it knows exactly what it is."""

    def facts(self, mod_id: int = 7) -> dict:
        return {"modio_id": mod_id, "modfile_id": mod_id * 10, "version": "2.0", "name": "Thing"}

    def test_installing_records_and_the_inventory_reads_it_back(self):
        library.install(self.archive("Thing Folder"), self.library, self.mods, facts=self.facts())
        self.assertEqual(library.registry(self.library), {"Thing Folder": self.facts()})
        found = installplan.inventory(ANNO_1800, self.mods)
        self.assertEqual((found[0].modio_id, found[0].modfile_id, found[0].kind), (7, 70, library.COPY))

    def test_so_the_plan_knows_a_collection_mod_whatever_its_folder_is_called(self):

        library.install(self.archive("Thing Folder"), self.library, self.mods, facts=self.facts())
        from dataclasses import replace

        # The fake's modfile id is ten times the mod id: 70, as recorded.
        wanted = installplan.wanted_from_mods([modio_summary(downloadable_mod(7, "[Gameplay] Thing (Kurila)", "2.0", b"zip"))])
        plan = installplan.build(wanted, installplan.inventory(ANNO_1800, self.mods))
        self.assertEqual(plan.rows[0].status, installplan.CURRENT)
        # And a newer file on mod.io is told by the file, not the name.
        wanted[0].summary = replace(wanted[0].summary, modfile_id=71)
        plan = installplan.build(wanted, installplan.inventory(ANNO_1800, self.mods))
        self.assertEqual(plan.rows[0].status, installplan.OLDER)

    def test_a_match_is_written_beside_how_it_was_made(self):
        mine = install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        linked = library.adopt(self.library, mine)
        name = library.copy_name(self.library, linked.link)
        self.assertEqual(name, "[Gameplay] Mine")
        self.assertTrue(library.learn(self.library, name, 44, "name"))
        entry = library.registry(self.library)[name]
        self.assertEqual((entry["modio_id"], entry[library.KNOWN_BY], entry["source"]),
                         (44, "name", library.ADOPTED))
        # So the inventory knows it without asking by name again.
        found = installplan.inventory(ANNO_1800, self.mods)
        self.assertEqual(found[0].modio_id, 44)
        # Said twice it is written once, and a better match replaces it.
        self.assertFalse(library.learn(self.library, name, 44, "name"))
        self.assertTrue(library.learn(self.library, name, 44, "log"))
        self.assertEqual(library.registry(self.library)[name][library.KNOWN_BY], "log")

    def test_a_match_never_overwrites_what_the_studio_installed_by_id(self):
        library.install(self.archive("Thing Folder"), self.library, self.mods, facts=self.facts())
        self.assertFalse(library.learn(self.library, "Thing Folder", 99, "name"))
        self.assertEqual(library.registry(self.library), {"Thing Folder": self.facts()})

    def test_a_zip_that_turns_out_to_be_a_mod_io_file_is_no_longer_only_a_match(self):
        dropped = self.archive("[Gameplay] Thing")
        library.install_zip(dropped, self.library, self.mods)
        library.learn(self.library, "[Gameplay] Thing", 7, "name")
        self.assertTrue(library.confirm(self.library, "[Gameplay] Thing", 7, 70))
        entry = library.registry(self.library)["[Gameplay] Thing"]
        self.assertEqual((entry["modio_id"], entry["modfile_id"], entry["source"]), (7, 70, library.ZIP))
        self.assertNotIn(library.KNOWN_BY, entry)
        # Said twice it is written once, and the plan compares by the file.
        self.assertFalse(library.confirm(self.library, "[Gameplay] Thing", 7, 70))
        found = installplan.inventory(ANNO_1800, self.mods)
        self.assertEqual((found[0].modio_id, found[0].modfile_id), (7, 70))

    def test_the_only_copies_are_the_ones_with_no_second_source(self):
        """Not what mod.io named or was matched to, not what a loadout
        builds, and not a folder of the user's own."""
        library.install(self.archive("From Modio"), self.library, self.mods, facts=self.facts())
        dropped = self.archive("Dropped")
        library.install_zip(dropped, self.library, self.mods)
        library.install_zip(self.archive("Dropped And Matched"), self.library, self.mods)
        library.learn(self.library, "Dropped And Matched", 9, "name")
        library.adopt(self.library, install_mod(self.mods, "[Gameplay] Moved In", "moved", "3"))
        library.adopt(self.library, install_mod(self.mods, "-[Gameplay] Moved In Off", "off", "1"))
        built = self.library / "Built"
        built.mkdir()
        library.note_link(self.library, library.link(built, self.mods), library.COPY)
        library.record(self.library, "Built", source=library.BUILT)
        before = self.library / "From Before"
        before.mkdir()
        library.note_link(self.library, library.link(before, self.mods), library.COPY)
        library.refer(self.library, install_mod(self.root / "dev", "mine", "mine", "0.1"), self.mods, "Mine")

        only = {mod.target.name: mod for mod in library.only_copies(self.library)}
        self.assertEqual(sorted(only), ["[Gameplay] Moved In", "[Gameplay] Moved In Off"])
        self.assertEqual((only["[Gameplay] Moved In"].source, only["[Gameplay] Moved In"].version),
                         (library.ADOPTED, "3"))
        # A zip is kept with the backups as it is installed, so its copy is
        # not one there is no second source of.
        (kept,) = library.backed_up(self.library)
        self.assertEqual((kept.target.name, kept.source, kept.zip), ("Dropped", library.ZIP, dropped.name))
        self.assertTrue(Path(kept.backup).is_file())
        # A copy from before how it arrived was recorded is counted apart:
        # nothing can be said of it either way.
        self.assertEqual([mod.target.name for mod in library.unrecorded(self.library)], ["From Before"])

    def test_a_folder_backed_up_is_a_zip_that_installs_it_again(self):
        """Dropped on the window, the zip is the mod: its folder at the top."""
        import zipfile

        moved = install_mod(self.mods, "[Gameplay] Moved In", "moved", "3")
        (moved / "data" / "graphics").mkdir(parents=True)
        (moved / "data" / "graphics" / "model.rdm").write_bytes(b"\x00" * 5000)
        library.adopt(self.library, moved)
        told = []
        made = library.back_up(self.library, "[Gameplay] Moved In", progress=lambda d, t: told.append((d, t)))
        self.assertEqual(made.parent, library.backup_dir(self.library))
        self.assertEqual(told[-1][0], told[-1][1])
        with zipfile.ZipFile(made) as zipped:
            names = zipped.namelist()
        self.assertIn("[Gameplay] Moved In/data/graphics/model.rdm", names)
        self.assertIn("[Gameplay] Moved In/modinfo.json", names)
        self.assertEqual({name.split("/")[0] for name in names}, {"[Gameplay] Moved In"})
        self.assertEqual(library.only_copies(self.library), [])
        self.assertEqual([mod.backup for mod in library.backed_up(self.library)], [str(made)])
        # The library lost, the zip puts it back.
        library.remove_copy(self.library, self.mods / "[Gameplay] Moved In")
        library.install_zip(made, self.library, self.mods)
        self.assertEqual(
            (self.library / "[Gameplay] Moved In" / "data" / "graphics" / "model.rdm").stat().st_size, 5000)
        # Kept where it was, not copied onto itself.
        self.assertEqual([path.name for path in library.backup_dir(self.library).iterdir()], [made.name])

    def test_a_backup_stopped_part_way_leaves_nothing_that_looks_whole(self):
        library.adopt(self.library, install_mod(self.mods, "[Gameplay] Moved In", "moved", "3"))
        self.assertIsNone(library.back_up(self.library, "[Gameplay] Moved In", stop=lambda: True))
        self.assertEqual(list(library.backup_dir(self.library).iterdir()), [])
        self.assertNotIn(library.BACKUP, library.registry(self.library)["[Gameplay] Moved In"])

    def test_mod_io_installing_it_drops_the_backup_of_what_it_replaced(self):
        library.install_zip(self.archive("[Gameplay] Thing"), self.library, self.mods)
        library.install(
            self.archive("[Gameplay] Thing"), self.library, self.mods,
            replace=self.mods / "[Gameplay] Thing", facts=self.facts(),
        )
        self.assertNotIn(library.BACKUP, library.registry(self.library)["[Gameplay] Thing"])

    def test_a_copy_switched_off_is_still_known_by_where_its_link_was(self):
        linked = library.adopt(self.library, install_mod(self.mods, "[Gameplay] Mine", "mine", "1"))
        library.unlink(self.library, linked.link)
        self.assertEqual(library.copy_name(self.library, linked.link), "[Gameplay] Mine")

    def test_what_is_not_a_copy_has_no_name_in_the_registry(self):
        checkout = install_mod(self.root / "dev", "my-mod", "mine", "0.1")
        linked = library.refer(self.library, checkout, self.mods, "[Gameplay] Mine")
        real = install_mod(self.mods, "[Gameplay] Real", "real", "1")
        self.assertEqual(library.copy_name(self.library, linked.link), "")
        self.assertEqual(library.copy_name(self.library, real), "")
        self.assertFalse(library.learn(self.library, "Nothing Here", 5, "name"))
        self.assertEqual(library.registry(self.library), {})

    def test_putting_back_forgets(self):
        library.install(self.archive("Thing Folder"), self.library, self.mods, facts=self.facts())
        library.put_back([self.mods], self.library)
        self.assertEqual(library.registry(self.library), {})


class TestBackingUpOnlyCopies(_Folders):
    """The list of only copies backs up what is chosen in it, on request,
    and moves it under its own heading once it is."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def groups(self):
        return [("Anno 1800", library.only_copies(self.library), library.unrecorded(self.library),
                 library.backed_up(self.library))]

    def dialog(self):
        from anno_modding_studio.ui.only_copies_dialog import OnlyCopiesDialog

        dialog = OnlyCopiesDialog(self.groups(), regroup=self.groups)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_only_copies_start_chosen_and_are_backed_up_when_asked(self):
        library.adopt(self.library, install_mod(self.mods, "[Gameplay] Moved In", "moved", "3"))
        before = self.library / "From Before"
        before.mkdir()
        library.note_link(self.library, library.link(before, self.mods), library.COPY)
        dialog = self.dialog()
        # Ready to back up what the list is for; a mod nothing is recorded
        # about is backed up only if it is chosen too.
        self.assertEqual(dialog.backup_button.text().replace("&", ""), "Back up 1 mod…")
        with _isolation.answering(True) as asked:
            dialog.backup_button.click()
        self.assertEqual(asked.titles, ["Back up these mods?"])
        self.assertIn("drop its zip", asked.texts[0])
        dialog._worker.wait(10000)
        self.app.processEvents()
        self.assertEqual(dialog.status.text(), "1 mod backed up.")
        (kept,) = library.backed_up(self.library)
        self.assertEqual(kept.target.name, "[Gameplay] Moved In")
        game = dialog.tree.topLevelItem(0)
        self.assertEqual([game.child(n).text(0) for n in range(game.childCount())],
                         ["Not recorded", "Backed up"])
        self.assertTrue(game.child(1).child(0).text(3))

    def test_declining_backs_up_nothing(self):
        library.adopt(self.library, install_mod(self.mods, "[Gameplay] Moved In", "moved", "3"))
        dialog = self.dialog()
        with _isolation.answering(False):
            dialog.backup_button.click()
        self.assertIsNone(dialog._worker)
        self.assertEqual(library.backed_up(self.library), [])

    def test_a_heading_chosen_stands_for_every_mod_under_it(self):
        for name in ("[Gameplay] One", "[Gameplay] Two"):
            library.adopt(self.library, install_mod(self.mods, name, name, "1"))
        dialog = self.dialog()
        dialog.tree.clearSelection()
        self.assertFalse(dialog.backup_button.isEnabled())
        dialog.tree.topLevelItem(0).setSelected(True)
        self.assertEqual(dialog.backup_button.text().replace("&", ""), "Back up 2 mods…")


class TestInstallingOnAnno117(_Folders):
    """Installing writes no profile: whether a mod is on is the loadout's to
    say and Play's to write -- the live profile is as often the player's own."""

    def setUp(self):
        super().setUp()
        self.library = self.root / "library" / "anno117"
        (self.mods / "profiles").mkdir()
        (self.mods / "profile.txt").write_text("ActiveProfile default\n", encoding="utf-8")
        self.profile = self.mods / "profiles" / "default.txt"
        self.profile.write_text("# EnableNewMods\nDisplayName default\n# deliberately-off\n", encoding="utf-8")

    def archive117(self, folder: str, mod_id: str) -> Path:
        path = self.root / (mod_id + ".zip")
        path.write_bytes(_zip({"%s/modinfo.json" % folder: json.dumps({"ModID": mod_id, "Version": "1"})}))
        return path

    def test_the_live_profile_is_left_exactly_as_it_was(self):
        before = self.profile.read_text(encoding="utf-8")
        library.install(self.archive117("Thing", "thing-kurila"), self.library, self.mods)
        library.install(self.archive117("Off", "deliberately-off"), self.library, self.mods)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), before)
        self.assertTrue(library.is_junction(self.mods / "Thing"))


class TestSwitchingTheLibrarysModsOn(_Folders):
    """A mod the Studio installed and the game is ignoring is worth a line on
    the fourth card, and making the loadout live switches it back on."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock()
        self.addCleanup(setattr, self.window, "index", None)
        patched = mock.patch.object(self.window.where, "mods_folder", return_value=str(self.mods))
        patched.start()
        self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.aim = "manage"

    def make_live(self):

        with _isolation.answering(True), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.live.sync_loadout())

    def test_1800_a_library_mod_marked_off_is_said_and_renamed_on(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        link = done.linked[0].link
        link.rename(self.mods / "-[Gameplay] Thing")
        install_mod(self.mods, "-[Gameplay] Theirs", "theirs", "1")
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Thing", active=True)]
        self.window.refresh_truth()
        card = self.window.source
        self.assertEqual(card.truth_cells["on", "off"].text(), "1")
        self.assertEqual(card.checklist["yours"][1].text(), "1 in your folders")
        self.make_live()
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))
        # Theirs is theirs: still off.
        self.assertTrue((self.mods / "-[Gameplay] Theirs").is_dir())
        self.assertEqual(card.truth_cells["on", "off"].text(), "0")
        self.assertIn("renamed 1 folder", self.window.status_label.text())

    def test_117_an_unlisted_or_off_library_mod_is_listed_on(self):
        self.window.loadout.game = ANNO_117
        library117 = self.root / "library" / "anno117"
        (self.mods / "profiles").mkdir()
        (self.mods / "profile.txt").write_text("ActiveProfile default\n", encoding="utf-8")
        profile = self.mods / "profiles" / "default.txt"
        profile.write_text("# EnableNewMods\nDisplayName default\n# theirs\n", encoding="utf-8")
        zipped = self.root / "t.zip"
        zipped.write_bytes(_zip({"Thing/modinfo.json": json.dumps({"ModID": "thing-kurila"})}))
        # Installed without being told the game, as an older build did: unlisted, so off.
        library.install(zipped, library117, self.mods)
        install_mod(self.mods, "Theirs", "theirs", "1")
        self.window.loadout.layers = [ModLayer(origin="Thing", mod_id="thing-kurila", active=True)]
        self.window.refresh_truth()
        self.assertEqual(self.window.source.truth_cells["on", "off"].text(), "1")
        self.make_live()
        # Its own profile, made the live one; the user's is left as it was.
        live = switches.active_profile(self.mods)
        self.assertNotEqual(live, profile)
        self.assertEqual(switches.read_profile(live).on, {"theirs": False, "thing-kurila": True})
        self.assertEqual(profile.read_text(encoding="utf-8"), "# EnableNewMods\nDisplayName default\n# theirs\n")
        self.window.refresh_truth()
        self.assertEqual([m.name for m in self.window.truth.loading], ["Thing"])


class TestAModWithNoAssetPatch(TestReadingTheFolderAfterACollection):
    """A camera tweak or a texture pack is a mod the game loads, so it is
    read as a mod that changes nothing rather than skipped -- or the fourth
    card would count it as a stranger forever."""

    def test_it_is_listed_ticked_with_nothing_to_show(self):
        self.mod("[Gameplay] One", "one", 12)
        camera = install_mod(self.mods, "Zoom Out (Serp)", "zoom-serp", "1")
        (camera / "data" / "base" / "config" / "game").mkdir(parents=True)
        (camera / "data" / "base" / "config" / "game" / "camera.xml").write_text("<x/>", encoding="utf-8")
        (self.mods / "not-a-mod").mkdir()
        dialog = self.dialog()
        rows = self.rows(dialog)
        self.assertEqual(sorted(rows), ["Zoom Out (Serp)", "[Gameplay] One"])
        self.assertTrue(rows["Zoom Out (Serp)"][0])
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "Zoom Out (Serp)":
                self.assertEqual(dialog.table.item(row, COLUMNS.index("Changes")).text(), "none")
        self.assertIn("1 change from 2 mods", dialog.status.text())
        # And it comes through as a layer of the loadout's.
        dialog._accept()
        self.assertEqual(sorted(layer.origin for layer in dialog.layers), ["Zoom Out (Serp)", "[Gameplay] One"])
        zoom = [layer for layer in dialog.layers if layer.origin == "Zoom Out (Serp)"][0]
        self.assertEqual(zoom.mod_id, "zoom-serp")


class TestMovingIntoTheLibrary(_Folders):
    """The nudge: every mod outside the library is one the Studio is not
    managing yet, and one click moves them in -- name and switch untouched."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock()
        self.addCleanup(setattr, self.window, "index", None)
        patched = mock.patch.object(self.window.where, "mods_folder", return_value=str(self.mods))
        patched.start()
        self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.aim = "manage"

    def test_the_checklist_counts_what_is_not_the_librarys_and_the_survey_moves_it_in(self):
        from anno_modding_studio.core import convert

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        off = install_mod(self.mods, "-[Gameplay] Off", "off", "1")
        library.install(self.archive("[Gameplay] Already"), self.library, self.mods)
        cache = self.root / "modio" / "4169" / "mods" / "5"
        install_mod(cache, "[Gameplay] Theirs", "theirs", "1")
        self.window.loadout.own.loads_last = True
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Mine", mod_id="mine"),
            ModLayer(origin="[Gameplay] Off", mod_id="off", active=False),
        ]
        self.window.loadout_path = self.root / "Main.annoload"
        self.window.refresh_truth()
        card = self.window.source
        # Not what the game loads (Already and Theirs load, unmentioned): the
        # conversion will not start from it until it is.
        with mock.patch.object(self.window.prompts, "ask_bring_in_first", return_value=False) as first,                 mock.patch.object(self.window.prompts, "ask_convert") as asked:
            self.assertFalse(self.window.library_controller.move_into_library())
        first.assert_called_once()
        asked.assert_not_called()
        self.window.loadout.layers += [
            ModLayer(origin="[Gameplay] Already", mod_id=""),
            ModLayer(origin="5/[Gameplay] Theirs", mod_id="theirs"),
        ]
        self.window.refresh_truth()
        self.assertTrue(self.window.truth.matches)
        self.assertEqual(card.checklist["yours"][1].text(), "2 in your folders")
        self.assertEqual(card.checklist["cache"][1].text(), "1 installed by the game's mod browser")
        # Two places hold mods, so each has its row under the one for both.
        self.assertFalse(card.checklist["yours"][0].isHidden())
        self.assertFalse(card.checklist["cache"][0].isHidden())
        self.assertEqual(card.convert_said.text(), "<b>3 of 4 mods are outside the library: [Gameplay] Mine, [Gameplay] Off, [Gameplay] Theirs</b>")
        # The card's row opens the survey on its own place; declined, nothing moves.
        with mock.patch.object(self.window.prompts, "ask_convert", return_value=None) as asked:
            card.checklist["yours"][2].click()
        self.assertEqual(asked.call_args[0][3], {convert.YOURS})
        self.assertFalse(library.is_junction(self.mods / "[Gameplay] Mine"))
        said = []
        with mock.patch.object(self.window.prompts, "ask_convert",
                               side_effect=lambda surveyed, *a, **k: ([r for r in surveyed.rows if r.where == convert.YOURS], [])), \
                mock.patch.object(self.window.prompts, "report_conversion",
                                  side_effect=lambda text: said.append(widgets.plain_text(text))), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.assertTrue(self.window.library_controller.move_into_library())
        # Mine is a link now; the off one is in the library and off the
        # library's way -- no link, not a "-" on one.
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Mine"))
        self.assertFalse(off.exists())
        self.assertFalse((self.mods / "[Gameplay] Off").exists())
        self.assertIn("[Gameplay] Off", library.unlinked(self.library))
        self.assertEqual(sorted(p.name for p in self.library.iterdir() if p.is_dir()),
                         ["[Gameplay] Already", "[Gameplay] Mine", "[Gameplay] Off", "conversions"])
        # The browser's mod was not touched.
        self.assertFalse(library.is_junction(cache / "[Gameplay] Theirs"))
        self.assertTrue(card.checklist["yours"][0].isHidden())
        self.assertIn("moved 2 into the library", said[0])
        self.assertIn("Before, the game loaded 3 mods. It loads the same ones now", said[0])
        # And the way back still works.
        with mock.patch.object(self.window.prompts, "confirm_put_back", return_value=True):
            self.assertTrue(self.window.library_controller.put_library_back())
        self.assertFalse(library.is_junction(self.mods / "[Gameplay] Mine"))
        self.window.files.mark_saved()

    def test_a_folder_of_your_own_is_linked_in_and_managed_where_it_is(self):
        checkout = install_mod(self.root / "dev", "my-mod", "mine", "0.1")
        with mock.patch.object(self.window.prompts, "pick_own_folder", return_value=str(checkout)):
            self.assertTrue(self.window.library_controller.manage_own_folder())
        link = self.mods / "my-mod"
        self.assertTrue(library.is_junction(link))
        self.assertEqual(library.kind_of(link, self.library), library.REFERENCE)
        self.assertTrue((checkout / "modinfo.json").is_file())
        self.window.refresh_truth()
        found = {m.name: m.installed.kind for m in self.window.truth.mods}
        self.assertEqual(found, {"my-mod": library.REFERENCE})
        # Managed, so not something to move in.
        self.assertTrue(self.window.source.checklist["yours"][0].isHidden())
        # Not a mod folder: refused.
        with mock.patch.object(self.window.prompts, "pick_own_folder", return_value=str(self.root / "dev")), \
                mock.patch("anno_modding_studio.ui.main_window.QMessageBox.warning", return_value=None) as warn:
            self.assertFalse(self.window.library_controller.manage_own_folder())
        warn.assert_called_once()


class TestALibraryModsSwitchIsItsLink(_Folders):
    """Off is no link; on is the link back where it was.  The library
    remembers every link it made, so an unlinked mod is installed-and-off
    to the truth view, and every switch moves a link for a library mod."""

    def test_unlink_and_relink_round_trip_and_the_inventory_sees_both_states(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods,
                               facts={"modio_id": 7, "modfile_id": 70, "version": "2.0"})
        link = done.linked[0].link
        self.assertEqual(library.links(self.library)["[Gameplay] Thing"]["linked"], True)
        self.assertTrue(library.unlink(self.library, link))
        self.assertFalse(link.exists())
        self.assertTrue((self.library / "[Gameplay] Thing" / "modinfo.json").is_file())
        self.assertEqual(list(library.unlinked(self.library)), ["[Gameplay] Thing"])
        found = installplan.inventory(ANNO_1800, self.mods)
        self.assertEqual([(m.folder_name, m.linked, m.kind, m.modio_id) for m in found],
                         [("[Gameplay] Thing", False, library.COPY, 7)])
        self.assertEqual(library.relink(self.library, "[Gameplay] Thing"), link)
        self.assertTrue(library.is_junction(link))
        self.assertEqual(library.unlinked(self.library), {})
        # Not the library's: refused, untouched.
        theirs = install_mod(self.mods, "[Gameplay] Theirs", "theirs", "1")
        self.assertFalse(library.unlink(self.library, theirs))
        self.assertTrue(theirs.is_dir())
        self.assertIsNone(library.relink(self.library, "nobody"))

    def test_the_truth_reads_an_unlinked_mod_as_installed_and_off(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        library.unlink(self.library, done.linked[0].link)
        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [ModLayer(origin="[Gameplay] Thing", active=True)]
        found = truth.read(loadout, self.mods, library_dir=self.library)
        self.assertEqual([m.name for m in found.wanted_but_off], ["[Gameplay] Thing"])
        self.assertEqual([m.name for m in found.library_off], ["[Gameplay] Thing"])
        self.assertEqual(found.loading, [])
        # Switched on: the link comes back.
        self.assertEqual(library.relink(self.library, "[Gameplay] Thing"), self.mods / "[Gameplay] Thing")
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))
        again = truth.read(loadout, self.mods, library_dir=self.library)
        self.assertTrue(again.matches)
        # And off from the card: the link goes, the game's own switch untouched.
        loadout.layers[0].active = False
        found = truth.read(loadout, self.mods, library_dir=self.library)
        truth.switch_off(found, self.library)
        self.assertFalse((self.mods / "[Gameplay] Thing").exists())
        self.assertEqual(list(library.unlinked(self.library)), ["[Gameplay] Thing"])

    def test_putting_back_materialises_an_unlinked_copy_too(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        library.unlink(self.library, done.linked[0].link)
        result = library.put_back([self.mods], self.library)
        self.assertEqual(result.materialised, [self.mods / "[Gameplay] Thing"])
        self.assertTrue((self.mods / "[Gameplay] Thing" / "modinfo.json").is_file())
        self.assertFalse(library.is_junction(self.mods / "[Gameplay] Thing"))
        self.assertEqual(library.links(self.library), {})


class TestSyncMovesLinks(_Folders):
    """Switch installed mods to match, over library mods: a
    loadout's switches are links."""

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock()
        self.addCleanup(setattr, self.window, "index", None)
        patched = mock.patch.object(self.window.where, "mods_folder", return_value=str(self.mods))
        patched.start()
        self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.mods_folder = str(self.mods)

    def sync(self):

        with _isolation.answering(True), \
                mock.patch.object(self.window.start, "refresh_source_panel"):
            self.window.live.sync_loadout()

    def test_a_loadout_unlinks_the_strays_and_relinks_what_it_wants(self):
        wanted = library.install(self.archive("[Gameplay] Wanted"), self.library, self.mods)
        library.unlink(self.library, wanted.linked[0].link)
        library.install(self.archive("[Gameplay] Stray"), self.library, self.mods)
        install_mod(self.mods, "[Gameplay] Theirs", "theirs", "1")
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Wanted", active=True, folder=str(self.mods / "[Gameplay] Wanted")),
        ]
        self.window.loadout.own.loads_last = True
        self.sync()
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Wanted"))
        self.assertFalse((self.mods / "[Gameplay] Stray").exists())
        self.assertEqual(list(library.unlinked(self.library)), ["[Gameplay] Stray"])
        # The user's own stray got the game's own switch, as before.
        self.assertTrue((self.mods / "-[Gameplay] Theirs").is_dir())
        self.assertEqual(self.window.source.truth_step.status.full_text(),
                         "Matches, 1 outside the library")
        self.window.files.mark_saved()

    def test_a_loadout_that_has_a_library_mod_off_takes_its_link_away(self):
        done = library.install(self.archive("[Gameplay] Thing"), self.library, self.mods)
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Thing", active=False, folder=str(done.linked[0].link)),
        ]
        self.sync()
        self.assertFalse((self.mods / "[Gameplay] Thing").exists())
        # A link taken away is not a folder renamed, and the status bar knows.
        self.assertIn("switched 1 library mod by its link", self.window.status_label.text())
        self.assertNotIn("renamed", self.window.status_label.text())
        self.window.loadout.layers[0].active = True
        self.sync()
        self.assertTrue(library.is_junction(self.mods / "[Gameplay] Thing"))
        self.window.files.mark_saved()
