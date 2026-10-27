"""A loadout file somebody sent: every path in it is checked as it is read.

A ``.annoload`` names its projects, its mods folder and each mod's folder as
paths, and a shared one can name any of them anywhere: a share on another
machine, which Windows connects to as soon as it is asked about it and hands
the user's sign-in; a project outside the Studio's folder; a folder of the
user's own to build into, or to copy into a mod (security review N1).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    disk,
    histories,
    places,
    projects,
    scripts,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Loadout,
    LoadoutError,
    ModLayer,
    is_folder_path,
    keep_folders_inside,
    own_record,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402
from anno_modding_studio.ui import where as where_mod  # noqa: E402

# Each way of spelling a path Windows would reach over the network, or as a
# device, for asking about it.
FAR = (
    r"\\host\share\x.annoproj",
    "//host/share/x.annoproj",
    r"\\?\C:\x.annoproj",
    r"\\.\pipe\x.annoproj",
    r"\??\UNC\host\share\x.annoproj",
)


@contextlib.contextmanager
def untouched(case: unittest.TestCase):
    """Fail ``case`` if anything asks the disk about a path of :data:`FAR`."""
    def far(path) -> bool:
        return str(path).replace("/", "\\").startswith(("\\\\", "\\??\\"))

    def guard(name, real):
        def asked(self, *args, **kwargs):
            if far(self):
                case.fail("%s asked of %s" % (name, self))
            return real(self, *args, **kwargs)
        return asked

    def guard_os(name, real):
        def asked(path, *args, **kwargs):
            if far(path):
                case.fail("%s asked of %s" % (name, path))
            return real(path, *args, **kwargs)
        return asked

    with contextlib.ExitStack() as stack:
        for name in ("exists", "is_file", "is_dir", "stat", "resolve", "open",
                     "read_text", "read_bytes", "iterdir"):
            stack.enter_context(mock.patch.object(Path, name, guard(name, getattr(Path, name))))
        for name in ("exists", "isdir", "isfile", "realpath"):
            stack.enter_context(mock.patch.object(os.path, name, guard_os(name, getattr(os.path, name))))
        yield


class _Case(unittest.TestCase):
    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def mine(self) -> Path:
        """A loadout of this PC's, with its own mod kept in a project."""
        loadout = Loadout(own=own_record(name="My Mod", creator="me", mod_id="me-my-mod"), own_mod=True)
        path = self.root / "Mine.annoload"
        loadout.save(path)
        return path

    def sent(self, **changed) -> Path:
        """A loadout file as somebody sent it: :meth:`mine`, with ``changed``
        written over its keys."""
        data = json.loads(self.mine().read_text(encoding="utf-8"))
        data.update(changed)
        path = self.root / "Sent.annoload"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def own_project(self, path: str, mod_id: str = "me-my-mod") -> list:
        return [{"origin": "", "path": path, "mod_id": mod_id, "read_at": 1}]


class TestProjectsAreReadOnlyFromTheStudiosFolder(_Case):
    def test_one_on_another_machine_is_never_asked_about(self):
        for far in FAR:
            with self.subTest(far), untouched(self):
                loaded = Loadout.load(self.sent(projects=self.own_project(far, mod_id="")))
            self.assertEqual(loaded.held(""), projects.MISSING)

    def test_and_is_found_by_its_mod_id_where_this_pc_has_it(self):
        with untouched(self):
            loaded = Loadout.load(self.sent(projects=self.own_project(FAR[0])))
        ref = loaded.projects[""]
        self.assertEqual(ref.lost, FAR[0])
        self.assertTrue(disk.inside(projects.where(ref.path), places.projects_dir()))
        self.assertEqual(loaded.own.name, "My Mod")

    def test_one_climbing_out_of_the_studio_s_folder_is_not_read(self):
        outside = self.root / "outside"
        outside.mkdir()
        mine = projects.where(Loadout.load(self.mine()).projects[""].path)
        victim = outside / "victim.annoproj"
        content = json.loads(mine.read_text(encoding="utf-8"))
        content["details"]["name"] = "Not Mine"
        content["mod_id"] = content["details"]["mod_id"] = "someone-else"
        victim.write_text(json.dumps(content), encoding="utf-8")
        climbing = os.path.relpath(victim, places.studio_home())
        self.assertTrue(climbing.startswith(".."))
        for path in (climbing, str(victim)):
            with self.subTest(path), mock.patch.object(projects, "read", wraps=projects.read) as read:
                loaded = Loadout.load(self.sent(projects=self.own_project(path, mod_id="someone-else")))
                self.assertNotIn(victim, [Path(call.args[0]) for call in read.call_args_list])
            self.assertEqual(loaded.held(""), projects.MISSING)
            self.assertNotEqual(loaded.own.name, "Not Mine")

    def test_one_inside_it_opens_as_before(self):
        loaded = Loadout.load(self.mine())
        self.assertEqual((loaded.held(""), loaded.own.name), ("", "My Mod"))

    def test_a_checkpoint_s_project_outside_it_is_not_read(self):
        outside = self.root / "outside.annoproj"
        mine = projects.where(Loadout.load(self.mine()).projects[""].path)
        shutil.copy(mine, outside)
        self.assertIsNone(histories._project_file({"path": str(outside)}, ANNO_1800))
        self.assertIsNotNone(histories._project_file({"path": str(mine)}, ANNO_1800))
        with untouched(self):
            self.assertIsNone(histories._project_file({"path": FAR[0]}, ANNO_1800))

    def test_where_never_spells_a_far_path_as_itself(self):
        for far in FAR:
            with self.subTest(far):
                where = projects.where(far)
                self.assertFalse(disk.remote_or_device(str(where)))
                self.assertFalse(where.exists())


class TestPathsOnAnotherMachineAreForgotten(_Case):
    def test_the_mods_folder_the_base_file_and_a_mod_s_folders(self):
        layer = ModLayer(origin="Theirs", name="Theirs", folder=FAR[0], scripts_folder=FAR[1]).to_dict()
        own = dict(json.loads(self.mine().read_text(encoding="utf-8"))["own"], scripts_folder=FAR[2])
        with untouched(self):
            loaded = Loadout.load(self.sent(
                mods_folder=FAR[0], source_path=FAR[3], layers=[layer], own=own,
            ))
        self.assertEqual((loaded.mods_folder, loaded.source_path), ("", ""))
        theirs = loaded.layer("Theirs")
        self.assertEqual((theirs.folder, theirs.scripts_folder, loaded.own.scripts_folder), ("", "", ""))

    def test_a_scripts_folder_a_project_holds(self):
        mine = projects.where(Loadout.load(self.mine()).projects[""].path)
        content = json.loads(mine.read_text(encoding="utf-8"))
        content["details"]["scripts_folder"] = FAR[0]
        mine.write_text(json.dumps(content), encoding="utf-8")
        self.assertEqual(projects.Project.read(mine).details["scripts_folder"], "")
        with untouched(self):
            self.assertEqual(Loadout.load(self.root / "Mine.annoload").own.scripts_folder, "")

    def test_one_on_this_pc_is_kept(self):
        layer = ModLayer(origin="Theirs", name="Theirs", folder=r"D:\mods\Theirs").to_dict()
        loaded = Loadout.load(self.sent(mods_folder=r"D:\mods", layers=[layer]))
        self.assertEqual((loaded.mods_folder, loaded.layer("Theirs").folder), (r"D:\mods", r"D:\mods\Theirs"))


class TestAModIsNamedByFolderNames(_Case):
    def test_one_named_otherwise_is_refused_file_and_all(self):
        for origin in ("..", "a/../b", "Host/", "/Theirs", r"a\..\b", "C:", "C:/Windows", "x:stream",
                       "CON", "Theirs/NUL", ".", "Theirs."):
            with self.subTest(origin):
                layer = ModLayer(origin=origin, name="Theirs").to_dict()
                with self.assertRaises(LoadoutError) as said:
                    Loadout.load(self.sent(layers=[layer]))
                self.assertIn("not the name of a folder", str(said.exception))

    def test_the_names_mods_have_are_taken(self):
        for origin in ("Theirs", "[Gameplay] Thing (Kurila)", "3229975/[Building] Nate's Windmill (Jakob)",
                       "Host/shared piece", " Spaced first"):
            with self.subTest(origin):
                self.assertTrue(is_folder_path(origin))
                layer = ModLayer(origin=origin, name="Theirs").to_dict()
                self.assertIsNotNone(Loadout.load(self.sent(layers=[layer])).layer(origin))


class TestAModsFolderOutsideWhereModsAreIsForgotten(unittest.TestCase):
    def test_only_folders_under_one_of_the_roots_are_kept(self):
        roots = [r"D:\Games\mods", r"C:\Library"]
        loadout = Loadout()
        for origin, folder in (("In", r"D:\Games\mods\In"), ("Deeper", r"C:\Library\anno1800\Deeper"),
                               ("Out", r"C:\Users\me\Documents"), ("Climbing", r"D:\Games\mods\..\secret"),
                               ("Beside", r"D:\Games\mods2\Beside"), ("None", "")):
            loadout.set_layer(ModLayer(origin=origin, name=origin, folder=folder))
        self.assertEqual(sorted(keep_folders_inside(loadout, roots)), ["Beside", "Climbing", "Out"])
        self.assertEqual({layer.origin: layer.folder for layer in loadout.layers}, {
            "In": r"D:\Games\mods\In", "Deeper": r"C:\Library\anno1800\Deeper",
            "Out": "", "Climbing": "", "Beside": "", "None": "",
        })


class TestThePathHelpers(unittest.TestCase):
    def test_far_paths_are_told_from_near_ones(self):
        for far in FAR:
            self.assertTrue(disk.remote_or_device(far), far)
        for near in (r"C:\x", "relative/x", r"\rooted\x", "", None, "D:/x"):
            self.assertFalse(disk.remote_or_device(near), near)

    def test_inside_by_the_spelling(self):
        self.assertTrue(disk.spelt_inside(r"C:\a\b", r"C:\a"))
        self.assertTrue(disk.spelt_inside(r"C:\A\b", "c:/a/"))
        self.assertTrue(disk.spelt_inside(r"C:\a", r"C:\a"))
        self.assertFalse(disk.spelt_inside(r"C:\ab", r"C:\a"))
        self.assertFalse(disk.spelt_inside(r"C:\a\..\b", r"C:\a"))
        self.assertFalse(disk.spelt_inside(r"\\host\a\b", r"\\host\a"))
        self.assertFalse(disk.spelt_inside("", r"C:\a"))


class TestAScriptModuleStaysInsideTheMod(_Case):
    def test_one_climbing_out_is_not_copied(self):
        lua = self.root / "lua"
        lua.mkdir()
        (self.root / "secret.lua").write_text("secret", encoding="utf-8")
        (lua / "fine.lua").write_text("fine", encoding="utf-8")
        loadout = Loadout(game=ANNO_117)
        loadout.make_own_mod()
        loadout.scripts = {"Modules": ["../secret.lua", "C:/secret.lua", "fine.lua"]}
        loadout.own.scripts_folder = str(lua)
        out = self.root / "out" / "mod"
        out.mkdir(parents=True)
        trouble = []
        self.assertEqual(scripts.carry(loadout, out, trouble), 1)
        self.assertEqual(len(trouble), 2)
        self.assertFalse((self.root / "out" / "secret.lua").exists())
        self.assertTrue((out / "fine.lua").is_file())


class _Settings:
    def __init__(self):
        self.held = {}

    def value(self, key, default=None):
        return self.held.get(key, default)

    def setValue(self, key, value):
        self.held[key] = value


class TestTheWindowChecksTheModsFolder(unittest.TestCase):
    """What :meth:`MainWindow.check_named_folders` does with what a loadout
    names, over a window of the test's own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.documents = self.root / "Documents" / "Anno 1800"
        (self.documents / "mods").mkdir(parents=True)
        patched = mock.patch.object(where_mod.games, "documents_dir", return_value=self.documents)
        patched.start()
        self.addCleanup(patched.stop)
        self.window = SimpleNamespace(
            loadout=Loadout(game=ANNO_1800), settings=_Settings(), game_folder=None,
            prompts=mock.Mock(), status_label=mock.Mock(),
        )
        self.window.where = where_mod.Where(self.window)
        self.theirs = self.root / "theirs" / "mods"
        self.theirs.mkdir(parents=True)

    def check(self, answer=False):
        from anno_modding_studio.ui.main_window import MainWindow

        self.window.prompts.ask_use_mods_folder.return_value = answer
        MainWindow.check_named_folders(self.window)

    def test_a_folder_this_pc_knows_is_taken_without_asking(self):
        known = str(self.documents / "mods")
        self.window.loadout.mods_folder = known
        self.check()
        self.window.prompts.ask_use_mods_folder.assert_not_called()
        self.assertEqual(self.window.loadout.mods_folder, known)

    def test_another_is_asked_about_and_not_used_without_a_yes(self):
        self.window.loadout.mods_folder = str(self.theirs)
        self.check(answer=False)
        self.window.prompts.ask_use_mods_folder.assert_called_once_with(
            str(self.theirs), str(self.documents / "mods"))
        self.assertEqual(self.window.loadout.mods_folder, str(self.documents / "mods"))
        self.assertFalse(self.window.where.knows_mods_folder(str(self.theirs)))

    def test_a_yes_takes_it_and_it_is_known_from_then_on(self):
        self.window.loadout.mods_folder = str(self.theirs)
        self.check(answer=True)
        self.assertEqual(self.window.loadout.mods_folder, str(self.theirs))
        self.assertTrue(self.window.where.knows_mods_folder(str(self.theirs)))
        self.window.prompts.reset_mock()
        self.check()
        self.window.prompts.ask_use_mods_folder.assert_not_called()

    def test_one_that_is_not_on_this_pc_is_not_asked_about(self):
        self.window.loadout.mods_folder = str(self.root / "gone" / "mods")
        self.check()
        self.window.prompts.ask_use_mods_folder.assert_not_called()
        self.assertEqual(self.window.loadout.mods_folder, str(self.documents / "mods"))

    def test_a_mod_s_folder_outside_the_mods_folders_and_the_library_is_forgotten(self):
        self.window.loadout.mods_folder = str(self.theirs)
        inside = ModLayer(origin="In", name="In", folder=str(self.documents / "mods" / "In"))
        library = ModLayer(origin="Lib", name="Lib", folder=str(self.window.where.library_dir() / "Lib"))
        outside = ModLayer(origin="Out", name="Out", folder=str(self.theirs / "Out"))
        for layer in (inside, library, outside):
            self.window.loadout.set_layer(layer)
        self.check(answer=False)
        self.assertEqual([layer.folder for layer in (inside, library, outside)],
                         [str(self.documents / "mods" / "In"), str(self.window.where.library_dir() / "Lib"), ""])
        self.assertIn("Out", self.window.status_label.setText.call_args.args[0])

    def test_a_folder_read_in_is_known(self):
        self.window.where.know_mods_folder(str(self.theirs))
        self.window.settings.setValue("%s:%s" % (where_mod.LAST_MODS_FOLDER, ANNO_1800), str(self.root))
        self.assertTrue(self.window.where.knows_mods_folder(str(self.theirs).upper() + "\\"))
        self.assertEqual(self.window.settings.value("%s:%s" % (where_mod.AGREED_MODS_FOLDERS, ANNO_117)), None)


class TestOpeningOneInTheWindow(unittest.TestCase):
    """The check is made as a loadout opens, before anything looks in its
    folders."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)

    def test_a_mods_folder_it_names_is_asked_about_first(self):
        theirs = self.root / "theirs" / "mods"
        theirs.mkdir(parents=True)
        path = self.root / "Sent.annoload"
        Loadout(mods_folder=str(theirs)).save(path)
        with mock.patch.object(self.window.prompts, "ask_use_mods_folder", return_value=False) as asked:
            self.assertTrue(self.window.load_loadout(path, asked=True))
        self.assertEqual(asked.call_args.args[0], str(theirs))
        self.assertNotEqual(self.window.loadout.mods_folder, str(theirs))


if __name__ == "__main__":
    unittest.main()
