"""Play's pieces that need no window: knowing the game is running, finding the
store that starts it, and writing Anno 117's options file."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import install_mod  # noqa: E402

from anno_modding_studio.core import disk, launch, library, switches  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def steam_install(self, name: str, app_id: int) -> Path:
        steamapps = self.root / "steam" / "steamapps"
        install = steamapps / "common" / name
        (install / "Bin" / "Win64").mkdir(parents=True)
        exe = "Anno1800.exe" if "1800" in name else "Anno117.exe"
        (install / "Bin" / "Win64" / exe).write_bytes(b"")
        (steamapps / ("appmanifest_%d.acf" % app_id)).write_text(
            '"AppState"\n{\n\t"appid"\t\t"%d"\n\t"name"\t\t"%s"\n\t"installdir"\t\t"%s"\n}\n'
            % (app_id, name, name),
            encoding="utf-8",
        )
        return install


class TestIsTheGameRunning(unittest.TestCase):
    def test_by_its_process_name_whatever_the_case(self):
        self.assertTrue(launch.running(ANNO_1800, ["explorer.exe", "ANNO1800.EXE"]))
        self.assertFalse(launch.running(ANNO_1800, ["Anno117.exe"]))
        self.assertTrue(launch.running(ANNO_117, ["Anno117.exe"]))
        self.assertFalse(launch.running("anno2205", ["Anno2205.exe"]))

    def test_windows_is_asked_through_its_own_tasklist(self):
        """A bare name is looked for in the program's folder and the working
        folder before System32: a tasklist.exe left there would have run."""
        import importlib.util
        import os
        import subprocess

        # The suite's isolation stands in for _processes; a fresh copy of the
        # module has the real one.
        spec = importlib.util.find_spec(launch.__name__)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        answered = subprocess.CompletedProcess([], 0, '"%s","1"\n' % fresh._exe(ANNO_1800), "")
        # Asked only where Windows' own snapshot of them could not be taken.
        with mock.patch.object(fresh.os, "name", "nt"), \
                mock.patch.object(fresh, "_snapshot", return_value=None), \
                mock.patch.object(fresh.subprocess, "run", return_value=answered) as run:
            self.assertTrue(fresh.running(ANNO_1800))
        exe = run.call_args[0][0][0]
        self.assertTrue(os.path.isabs(exe), exe)
        self.assertTrue(exe.lower().endswith(os.path.join("system32", "tasklist.exe")), exe)

    @unittest.skipUnless(sys.platform == "win32", "Windows' own list of processes")
    def test_windows_own_snapshot_names_this_process(self):
        """Asked every few seconds to grey Play, so with no program started
        to ask it."""
        import os
        import subprocess

        with mock.patch.object(subprocess, "run") as run:
            names = launch._snapshot()
        run.assert_not_called()
        self.assertIn(os.path.basename(sys.executable).lower(), [name.lower() for name in names])


class TestStartingItThroughItsStore(_Folder):
    def test_a_steam_install_is_started_by_the_app_its_manifest_names(self):
        self.steam_install("Some Other Game", 400)
        install = self.steam_install("Anno 1800", 916440)
        self.assertEqual(launch.steam_app_id(install), 916440)
        self.assertEqual(launch.uri(install), "steam://rungameid/916440")

    def test_a_folder_steam_does_not_know_is_not_steams(self):
        loose = self.root / "Games" / "Anno 117 - Pax Romana"
        loose.mkdir(parents=True)
        self.assertEqual(launch.steam_app_id(loose), 0)
        with mock.patch.object(launch, "ubisoft_game_id", return_value=""):
            self.assertEqual(launch.uri(loose), "")
        with mock.patch.object(launch, "ubisoft_game_id", return_value="4553"):
            self.assertEqual(launch.uri(loose), "uplay://launch/4553/0")
        self.assertEqual(launch.uri(None), "")

    def test_the_install_is_the_one_the_files_came_from_when_it_is_the_games(self):
        install = self.steam_install("Anno 117 - Pax Romana", 3274580)
        self.assertEqual(launch.install_for(ANNO_117, install), install)
        with mock.patch.object(launch.discovery, "find_game_installs", return_value=[]):
            # Not an 1800 install, and nothing else to find.
            self.assertIsNone(launch.install_for(ANNO_1800, install))


class TestWritingTheGameOwnOptions(_Folder):
    """Anno 117's ``active-options.jsonc``: the loader's input, keyed by ModID,
    holding only what differs from each mod's defaults."""

    def setUp(self):
        super().setUp()
        self.path = self.root / "active-options.jsonc"

    def test_the_loadout_s_mods_are_written_and_nobody_else_s_touched(self):
        self.path.write_text(
            '// written by a mod manager\n{\n  "Theirs": {"x": "1"},\n  "mine": {"old": "2"}\n}\n',
            encoding="utf-8",
        )
        written = switches.write_game_options(ANNO_117, {"Mine": {"speed": "3"}, "gone": {}}, at=self.path)
        self.assertEqual(written, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// written by a mod manager\n{"))
        self.assertEqual(switches.game_options(ANNO_117, at=self.path), {"theirs": {"x": "1"}, "mine": {"speed": "3"}})

    def test_a_mod_at_its_defaults_loses_its_entry(self):
        self.path.write_text(json.dumps({"mine": {"speed": "3"}}), encoding="utf-8")
        switches.write_game_options(ANNO_117, {"mine": {}}, at=self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})

    def test_nothing_changed_is_nothing_written(self):
        self.path.write_text(json.dumps({"mine": {"speed": "3"}}), encoding="utf-8")
        self.assertIsNone(switches.write_game_options(ANNO_117, {"mine": {"speed": "3"}}, at=self.path))

    def test_a_file_that_is_not_json_is_left_alone(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(switches.write_game_options(ANNO_117, {"mine": {"speed": "3"}}, at=self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json")

    def test_none_yet_is_one_made(self):
        switches.write_game_options(ANNO_117, {"mine": {"speed": "3"}}, at=self.path)
        self.assertEqual(switches.game_options(ANNO_117, at=self.path), {"mine": {"speed": "3"}})

    def test_1800_has_no_such_file(self):
        self.assertIsNone(switches.write_game_options(ANNO_1800, {"mine": {"speed": "3"}}))


class TestWhatTheLibraryHolds(_Folder):
    def test_through_a_junction_is_still_the_librarys(self):
        held = self.root / "library"
        copy = held / "[Gameplay] Thing"
        (copy / "data").mkdir(parents=True)
        mods = self.root / "mods"
        mods.mkdir()
        link = library.link(copy, mods).link
        self.assertTrue(library.holds(link / "data", held))
        own = mods / "Mine"
        own.mkdir()
        self.assertFalse(library.holds(own, held))


class TestSwitchingALibraryModThatBundlesAnother(_Folder):
    """A library mod's switch is its link, which Play moves itself and keeps
    from the game's own switches. A mod bundled inside it then looked, to
    those, like one asked for without its host, and was reported as one that
    could not be changed over a folder that had become what was wanted (A8-9)."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.core.changes import Loadout, ModLayer

        patched = mock.patch.object(switches, "documents_mods", lambda game: None)
        patched.start()
        self.addCleanup(patched.stop)
        self.held = self.root / "library"
        host = install_mod(self.held, "Host", "host")
        install_mod(host, "Inner", "inner")
        self.mods = self.root / "mods"
        self.mods.mkdir()
        library.note_link(self.held, library.link(host, self.mods), "copy")
        # A junction is not a folder a temporary directory may walk into.
        self.addCleanup(lambda: library.is_junction(self.mods / "Host")
                        and library.remove_junction(self.mods / "Host"))
        self.loadout = Loadout(game=ANNO_1800)
        self.loadout.layers = [
            ModLayer(origin="Host", mod_id="host", active=True),
            ModLayer(origin="Host/Inner", mod_id="inner", active=True),
        ]

    def play(self, on: bool):
        from anno_modding_studio.core import play

        for layer in self.loadout.layers:
            layer.active = on
        found = play.plan(self.loadout, str(self.mods), self.held)
        return found, (None if found.nothing_to_do else play.carry_out(found, self.loadout, self.held))

    def test_off_and_on_again_with_nothing_said_to_have_failed(self):
        found, written = self.play(True)
        self.assertTrue(found.nothing_to_do)
        for on in (False, True):
            with self.subTest(on=on):
                found, written = self.play(on)
                self.assertEqual(found.moving.get("Host"), on)
                self.assertEqual(written.failed, [])
                self.assertEqual([name for name, _path in written.linked], ["Host"])
                self.assertNotIn("could not be changed", written.describe())
                self.assertEqual(library.is_junction(self.mods / "Host"), on)
                again, _written = self.play(on)
                self.assertTrue(again.nothing_to_do)

    def test_a_link_that_would_not_move_answers_for_nothing(self):
        with mock.patch.object(library, "unlink", lambda *a, **k: False):
            _found, written = self.play(False)
        # Only the host: the mod inside it switches with it, and is not a
        # switch of its own to have failed.
        self.assertEqual([name for name, _why in written.failed], ["Host"])
        self.assertTrue(library.is_junction(self.mods / "Host"))

    def test_a_bundled_mods_path_into_the_library_outlasts_the_link(self):
        """The library's copy stays where it is whichever way the link goes,
        so a bundled mod's path into it is right throughout.  Following the
        host's link with it gave it a path through the link, which is not
        there once the link is taken away (A9-4)."""
        copy = self.held / "Host"
        inner = self.loadout.layer("Host/Inner")
        self.loadout.layer("Host").folder = str(copy)
        inner.folder = str(copy / "Inner")
        for on in (False, True, False):
            with self.subTest(on=on):
                _found, written = self.play(on)
                self.assertEqual(written.failed, [])
                self.assertEqual(library.is_junction(self.mods / "Host"), on)
                self.assertEqual(Path(inner.folder), copy / "Inner")
                self.assertTrue(Path(inner.folder).is_dir())

    def test_a_bundled_mods_path_through_the_link_is_not_moved_to_one_that_is_gone(self):
        through = self.mods / "Host" / "Inner"
        inner = self.loadout.layer("Host/Inner")
        inner.folder = str(through)
        self.play(False)
        self.assertEqual(Path(inner.folder), through)
        self.play(True)
        self.assertEqual(Path(inner.folder), through)
        self.assertTrue(through.is_dir())


class TestFollowingARenamedHost(_Folder):
    """Anno 1800 switches a mod by renaming its folder, and the loadout's path
    to it follows.  The mods bundled inside went with the folder and their
    paths did not, so each was left pointing at a folder that was not there
    (A9-4)."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.core.changes import Loadout

        patched = mock.patch.object(switches, "documents_mods", lambda game: None)
        patched.start()
        self.addCleanup(patched.stop)
        self.held = self.root / "library"
        self.held.mkdir()
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.loadout = Loadout(game=ANNO_1800)

    def install(self, host: str):
        """A host on disk as ``host``, with a mod bundled in it and one in
        that, and a layer for each holding the path it has there."""
        from anno_modding_studio.core.changes import ModLayer

        at = install_mod(self.mods, host, "host")
        install_mod(install_mod(at / "shared", "Inner", "inner"), "Deep", "deep")
        install_mod(self.mods, "Other", "other")
        self.loadout.layers = [
            ModLayer(origin="Host", mod_id="host", folder=str(at)),
            ModLayer(origin="Host/shared/Inner", mod_id="inner", folder=str(at / "shared" / "Inner")),
            ModLayer(origin="Host/shared/Inner/Deep", mod_id="deep",
                     folder=str(at / "shared" / "Inner" / "Deep")),
            ModLayer(origin="Other", mod_id="other", folder=str(self.mods / "Other")),
        ]

    def play(self, on: bool):
        from anno_modding_studio.core import play

        for layer in self.loadout.layers:
            layer.active = on or layer.origin == "Other"
        found = play.plan(self.loadout, str(self.mods), self.held)
        return play.carry_out(found, self.loadout, self.held)

    def folders(self) -> dict:
        return {layer.origin: Path(layer.folder) for layer in self.loadout.layers}

    def test_the_bundled_mods_paths_follow_it_off_and_on(self):
        for was, on, now in (("Host", False, "-Host"), ("-Host", True, "Host")):
            with self.subTest(on=on):
                if not on:
                    self.install(was)
                self.assertTrue((self.mods / was).is_dir())
                written = self.play(on)
                self.assertEqual(written.failed, [])
                self.assertEqual([name for name, _path in written.renamed], ["Host"])
                host = self.mods / now
                self.assertEqual(self.folders(), {
                    "Host": host,
                    "Host/shared/Inner": host / "shared" / "Inner",
                    "Host/shared/Inner/Deep": host / "shared" / "Inner" / "Deep",
                    "Other": self.mods / "Other",
                })
                for folder in self.folders().values():
                    self.assertTrue(folder.is_dir(), folder)

    def test_a_bundled_mod_holding_no_path_is_given_none(self):
        self.install("Host")
        self.loadout.layer("Host/shared/Inner").folder = ""
        self.play(False)
        self.assertEqual(self.loadout.layer("Host/shared/Inner").folder, "")
        self.assertEqual(self.folders()["Host/shared/Inner/Deep"],
                         self.mods / "-Host" / "shared" / "Inner" / "Deep")


# -- the loader's log ------------------------------------------------------------------
LOG_1800 = """[2026-09-24 23:09:53.163] [info] Load mods
[2026-09-24 23:09:53.200] [error] modinfo.json of Broken Mod has errors
[2026-09-24 23:09:54.332] [info] Load [Gameplay] Mine (Serp) (1.13, mine_serp) from mods\\[Gameplay] Mine
[2026-09-24 23:09:55.428] [info] Load [Shared] Piece (1.0, shared_piece) from mods\\[Gameplay] Mine\\shared_piece
[2026-09-24 23:09:56.100] [info] Load [Gameplay] Stray (1.0, stray) from mods\\[Gameplay] Stray
[2026-09-24 23:09:57.100] [info] Load [Building] Windmill (2.0.1, windmill) from C:\\Users\\Public\\mod.io\\4169\\mods\\3229975\\[Building] Windmill
[2026-09-24 23:09:58.100] [info] Load [Gameplay] Twin (1.2, twin) from mods\\[Seal] Twin\\[Gameplay] Twin
[2026-09-24 23:10:42.577] [warning] [Gameplay] Wanted is missing a dependency: shared-thing
[2026-09-24 23:10:43.378] [warning] [Gameplay] Mine: No matching node for Path "//Asset" (data/config/export/main/asset/assets.xml:660)
[2026-09-24 23:11:00.759] [info] Loading complete
"""

LOG_117 = """[2026-09-23 18:54:37.644] [info] Search mods...
[2026-09-23 18:54:37.676] [info] Enabled mod: mods/Mine  (mine-serp@1.0)
[2026-09-23 18:54:37.678] [info] Enabled mod: C:/Users/Public/mod.io/11358/mods/12/Theirs  (theirs-mod@1.0.0)
[2026-09-23 18:54:37.679] [info] Enabled mod: <game dir>/mods/.ubi/Infinite Money  (ubi-infinite-money@1.0)
[2026-09-23 18:54:37.706] [info] Enabled mods: 3 (plus 0 nested)
[2026-09-23 18:54:37.758] [info] Option: mine-serp: true
[2026-09-23 18:54:37.768] [info] Option: mine-serp.speed: 3
[2026-09-23 18:54:37.770] [info] Option: mine-serp.walls: false
[2026-09-23 18:54:37.820] [info] 2 mod options found
[2026-09-23 18:54:37.822] [info] Load "[Cheat] Mine" mine-serp@1.0 (from "mods/Mine")
[2026-09-23 18:54:39.656] [info] Patching completed in 2s
"""


class TestReadingTheLoadersLog(_Folder):
    def write(self, text: str) -> Path:
        path = self.root / "mod-loader.log"
        path.write_text(text, encoding="utf-8")
        return path

    def test_1800_what_loaded_from_where_and_what_went_wrong(self):
        import time

        from anno_modding_studio.core import loaderlog

        log = loaderlog.read(ANNO_1800, self.write(LOG_1800))
        self.assertTrue(log.complete)
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log.started)), "2026-09-24 23:09:53")
        self.assertEqual(
            [(m.mod_id, m.folder, m.bundled) for m in log.mods],
            [("mine_serp", "[Gameplay] Mine", False), ("shared_piece", "[Gameplay] Mine", True),
             ("stray", "[Gameplay] Stray", False), ("windmill", "[Building] Windmill", False),
             ("twin", "[Seal] Twin", True)],
        )
        self.assertEqual(log.mods[0].name, "[Gameplay] Mine (Serp)")
        # Errors and warnings about whether mods load; not the ones about one op.
        self.assertEqual([text for _level, text in log.problems], [
            "modinfo.json of Broken Mod has errors",
            "[Gameplay] Wanted is missing a dependency: shared-thing",
        ])

    def test_a_game_still_loading_is_not_complete(self):
        from anno_modding_studio.core import loaderlog

        log = loaderlog.read(ANNO_1800, self.write(LOG_1800.replace("Loading complete", "Still going")))
        self.assertFalse(log.complete)
        self.assertIsNone(loaderlog.read(ANNO_1800, self.root / "none.log"))

    def test_117_the_mods_it_enabled_and_the_options_it_applied(self):
        from anno_modding_studio.core import loaderlog

        log = loaderlog.read(ANNO_117, self.write(LOG_117))
        self.assertTrue(log.complete)
        self.assertEqual(
            [(m.mod_id, m.name, m.folder, m.bundled) for m in log.mods],
            [("mine-serp", "[Cheat] Mine", "Mine", False), ("theirs-mod", "Theirs", "Theirs", False),
             ("ubi-infinite-money", "Infinite Money", "Infinite Money", False)],
        )
        self.assertEqual(log.options, {"mine-serp": {"speed": "3", "walls": "false"}})


class TestCheckingTheLogAgainstTheLoadout(_Folder):
    """What the game loaded, beside what the loadout has on."""

    def mod(self, folder: str, mod_id: str, origin: str = "", wanted=None, cache: bool = False):
        from anno_modding_studio.core import installplan, truth

        installed = installplan.FolderMod(
            folder=self.root / folder, name=folder, mod_id=mod_id, version="1",
            in_cache=cache,
        )
        return truth.Mod(installed=installed, on=True, loads=True, origin=origin, wanted=wanted)

    def found(self, game, *mods):
        from anno_modding_studio.core import truth

        return truth.Truth(game=game, folder=self.root, mods=list(mods))

    def test_all_of_it_loaded_and_nothing_else(self):
        from anno_modding_studio.core import loaderlog
        from anno_modding_studio.core.changes import Loadout

        log = loaderlog.read(ANNO_1800, self._log(LOG_1800))
        found = self.found(
            ANNO_1800,
            self.mod("[Gameplay] Mine", "mine_serp", "[Gameplay] Mine", True),
            self.mod("[Gameplay] Stray", "stray", "[Gameplay] Stray", True),
            # The host whose own copy the loader took from inside it.
            self.mod("[Seal] Twin", "twin", "[Seal] Twin", True),
            self.mod("[Building] Windmill", "windmill", cache=True),
        )
        loadout = Loadout(game=ANNO_1800)
        loadout.own.loads_last = True
        checked = loaderlog.check(log, found, loadout)
        self.assertTrue(checked.ok)
        self.assertEqual(checked.browser, ["[Building] Windmill"])
        self.assertEqual(checked.loaded, 4)
        self.assertEqual(
            checked.describe(),
            # The browser's mod is one of the 4, and not the loadout's.
            "When the game started (24 Sep 23:09), it loaded 4 mods. "
            "The game's own browser installed 1 of them, and every other one is this loadout's. "
            # The log's one "No matching node", kept rather than thrown away:
            # of a mod the loadout has no layer for, so named, not sent to a
            # tooltip there is none of.
            "1 op in 1 mod this loadout does not have ([Gameplay] Mine) did nothing: "
            "a path that matched nothing, or a file it includes that was not there.",
        )

    def test_the_browsers_mods_are_some_of_those_it_loaded_not_more(self):
        from anno_modding_studio.core import loaderlog

        log = loaderlog.LoaderLog(path=self.root / "mod-loader.log")
        only = loaderlog.Check(log=log, loaded=1, browser=["Windmill"])
        self.assertEqual(
            only.describe(),
            "When the game started, it loaded 1 mod. "
            "The game's own browser installed 1 of the mods it loaded.",
        )
        stray = loaderlog.Check(log=log, loaded=3, browser=["Windmill"], extra=["Stray"])
        said = stray.describe()
        self.assertTrue(said.startswith("When the game started, it loaded 3 mods. 1 mod it does not"), said)
        self.assertTrue(said.endswith(" The game's own browser installed 1 of the mods it loaded."), said)

    def test_what_did_not_load_says_why_and_what_loaded_unasked_is_named(self):
        from anno_modding_studio.core import loaderlog
        from anno_modding_studio.core.changes import Loadout

        log = loaderlog.read(ANNO_1800, self._log(LOG_1800))
        found = self.found(
            ANNO_1800,
            self.mod("[Gameplay] Mine", "mine_serp", "[Gameplay] Mine", True),
            self.mod("[Gameplay] Wanted", "wanted", "[Gameplay] Wanted", True),
            self.mod("[Seal] Twin", "twin", "[Seal] Twin", True),
        )
        checked = loaderlog.check(log, found, Loadout(game=ANNO_1800))
        self.assertFalse(checked.ok)
        self.assertEqual(checked.missing, [
            ("[Gameplay] Wanted", "[Gameplay] Wanted is missing a dependency: shared-thing"),
        ])
        self.assertEqual(checked.extra, ["[Gameplay] Stray", "[Building] Windmill"])
        said = checked.describe()
        self.assertIn("1 mod this loadout has on did not load: [Gameplay] Wanted "
                      "([Gameplay] Wanted is missing a dependency: shared-thing)", said)
        self.assertIn("2 mods it does not have on loaded: [Gameplay] Stray, [Building] Windmill", said)

    def test_117_options_the_game_ran_differently_are_named(self):
        from anno_modding_studio.core import loaderlog, settings
        from anno_modding_studio.core.changes import Loadout, ModLayer

        log = loaderlog.read(ANNO_117, self._log(LOG_117))
        loadout = Loadout(game=ANNO_117)
        offered = settings.ModOptions([
            settings.Option(name="speed", label="Speed", kind=settings.SLIDER, default="1"),
            settings.Option(name="walls", label="Walls", kind=settings.TOGGLE, default="false"),
        ]).to_dict()
        loadout.layers = [ModLayer(origin="Mine", name="Mine", mod_id="mine-serp", offered=offered,
                                   options={"speed": "3.0", "walls": "true"})]
        found = self.found(
            ANNO_117,
            self.mod("Mine", "mine-serp", "Mine", True),
            self.mod("Theirs", "theirs-mod", "Theirs", True),
            self.mod("Infinite Money", "ubi-infinite-money", "Infinite Money", True),
        )
        checked = loaderlog.check(log, found, loadout)
        # 3.0 is 3; the switch is not.
        self.assertEqual(checked.options, [("Mine", "Walls", "true", "false")])
        self.assertIn("1 option ran differently: Mine Walls at false, not true", checked.describe())

    def _log(self, text: str) -> Path:
        path = self.root / "mod-loader.log"
        path.write_text(text, encoding="utf-8")
        return path


class TestWhenItWasMadeLive(_Folder):
    def test_the_pointer_remembers_when_and_a_rename_keeps_it(self):
        held = self.root / "library"
        self.assertEqual(library.live_since(held), 0.0)
        library.set_live(held, self.root / "Main.annoload", at=1000.0)
        self.assertEqual(library.live_since(held), 1000.0)
        library.follow_live(held, self.root / "Main.annoload", self.root / "New.annoload")
        self.assertEqual(library.live_since(held), 1000.0)


# -- a mod's data tree with no folder of its own ------------------------------------------
class TestFoldersTheLoaderListsAndLoadsNothingFrom(_Folder):
    """Anno 1800's loader lists every folder in the mods folder as a mod, and
    runs only what sits under a ``data`` folder or beside a modinfo --
    ``tools/loose_data_probe.py`` asked the game.  A mod's data tree
    extracted straight into ``mods`` is the common one: listed, and inert."""

    def setUp(self):
        super().setUp()
        self.mods = self.root / "mods"
        loose = self.mods / "data" / "config" / "export" / "main" / "asset"
        loose.mkdir(parents=True)
        (loose / "assets.xml").write_text("<ModOps/>", encoding="utf-8")
        (self.mods / ".vscode").mkdir()
        (self.mods / ".vscode" / "settings.json").write_text("{}", encoding="utf-8")
        (self.mods / "Old backups").mkdir()
        (self.mods / "Old backups" / "notes.txt").write_text("x", encoding="utf-8")

    def test_the_loose_shape_is_told_from_a_mod_and_from_tooling(self):
        from anno_modding_studio.core import modzip

        self.assertTrue(modzip.is_loose_data(self.mods / "data"))
        self.assertFalse(modzip.is_loose_data(self.mods / ".vscode"))
        (self.mods / "Other" / "config").mkdir(parents=True)
        self.assertFalse(modzip.is_loose_data(self.mods / "Other"))

    def read(self, game=ANNO_1800):
        from anno_modding_studio.core import installplan, truth
        from anno_modding_studio.core.changes import Loadout

        with mock.patch.object(switches, "documents_mods", return_value=None):
            found = installplan.inventory(game, self.mods, library_dir=self.root / "library")
            return found, truth.read(Loadout(game=game), self.mods, library_dir=self.root / "library")

    def test_they_are_counted_named_and_kept_out_of_the_grid(self):
        found, read = self.read()
        self.assertEqual(
            sorted((mod.folder_name, mod.inert, mod.loose) for mod in found),
            [("Old backups", True, False), ("data", True, True)],
        )
        # Nothing they hold reaches the game: not a mod that loads, not a
        # stray for Play to switch off, not one to move into the library.
        self.assertEqual(read.mods, [])
        self.assertEqual(read.adoptable, [])
        said = read.describe_inert()
        self.assertIn("“data” in your mods folder is a mod's contents without a folder of its own", said)
        self.assertIn("loads nothing from it. Put it in a folder named for the mod", said)
        self.assertIn("“Old backups” in your mods folder is no mod", said)

    def test_117_lists_only_mods(self):
        found, read = self.read(ANNO_117)
        self.assertEqual(found, [])
        self.assertEqual(read.describe_inert(), "")

    def test_the_log_check_passes_over_them(self):
        from anno_modding_studio.core import loaderlog
        from anno_modding_studio.core.changes import Loadout

        _found, read = self.read()
        path = self.root / "mod-loader.log"
        path.write_text(
            "[2026-09-24 23:10:49.451] [info] Load data (unversioned, data) from mods\\data\n"
            "[2026-09-24 23:10:49.500] [info] Loading complete\n",
            encoding="utf-8",
        )
        checked = loaderlog.check(loaderlog.read(ANNO_1800, path), read, Loadout(game=ANNO_1800))
        self.assertTrue(checked.ok)
        self.assertEqual(checked.loaded, 0)


class TestWhatPlayLeftBehind(_Folder):
    def test_the_pointer_keeps_when_what_and_the_launch(self):
        held = self.root / "library"
        self.assertEqual(library.live_record(held), {})
        library.note_launched(held, at=5.0)  # nothing live: nothing noted
        self.assertEqual(library.launched_at(held), 0.0)
        library.set_live(held, self.root / "Main.annoload", at=100.0, fingerprint="abc")
        self.assertEqual(library.launched_at(held), 0.0)
        library.note_launched(held, at=150.0)
        self.assertEqual(library.launched_at(held), 150.0)
        library.follow_live(held, self.root / "Main.annoload", self.root / "New.annoload")
        record = library.live_record(held)
        self.assertEqual((record["at"], record["fingerprint"], record["launched"]), (100.0, "abc", 150.0))
        self.assertTrue(disk.same_file(record["loadout"], self.root / "New.annoload"))

    def test_the_fingerprint_moves_with_any_switch(self):

        from anno_modding_studio.core import truth
        from anno_modding_studio.core.changes import Loadout

        mods = self.root / "mods"
        mods.mkdir()
        install_mod(mods, "[Gameplay] One", "one", "1")
        install_mod(mods, "[Gameplay] Two", "two", "1")
        with mock.patch.object(switches, "documents_mods", return_value=None):
            first = truth.fingerprint(truth.read(Loadout(game=ANNO_1800), mods))
            again = truth.fingerprint(truth.read(Loadout(game=ANNO_1800), mods))
            (mods / "[Gameplay] Two").rename(mods / "-[Gameplay] Two")
            after = truth.fingerprint(truth.read(Loadout(game=ANNO_1800), mods))
        self.assertEqual(first, again)
        self.assertNotEqual(first, after)

    def test_a_copy_that_goes_while_another_of_the_mod_loads_is_no_change(self):
        """What the mod browser's fresh start does to copies the library
        shadows: the game loads exactly what it did.  The fingerprint taken
        the way it was before still counts, so a loadout made live then is not
        said to have changed for the new way of counting alone."""

        from anno_modding_studio.core import truth
        from anno_modding_studio.core.changes import Loadout

        mods = self.root / "mods"
        mods.mkdir()
        install_mod(mods, "[Gameplay] One", "one", "2")
        install_mod(mods, "-[Gameplay] One old", "one", "1")
        with mock.patch.object(switches, "documents_mods", return_value=None):
            found = truth.read(Loadout(game=ANNO_1800), mods)
            first, before = truth.fingerprint(found), truth.fingerprint_before(found)
            import shutil

            shutil.rmtree(mods / "-[Gameplay] One old")
            found = truth.read(Loadout(game=ANNO_1800), mods)
        self.assertEqual(truth.fingerprint(found), first)
        self.assertNotEqual(truth.fingerprint_before(found), before)


class TestWhereALogCameFrom(unittest.TestCase):
    def check(self, **facts):
        from anno_modding_studio.core import loaderlog

        log = loaderlog.LoaderLog(path=Path("mod-loader.log"), started=0.0, complete=True)
        checked = loaderlog.Check(log=log, loaded=2, extra=["[Gameplay] Sneaky"])
        for name, value in facts.items():
            setattr(checked, name, value)
        return checked

    def test_play_s_own_start_is_graded(self):
        checked = self.check(by_play=True)
        self.assertTrue(checked.fault)
        self.assertTrue(checked.describe().startswith("When Play started the game, it loaded 2 mods."))

    def test_a_start_elsewhere_counts_the_same_while_the_folder_is_as_play_left_it(self):
        checked = self.check(by_play=False)
        self.assertTrue(checked.fault)
        self.assertTrue(checked.describe().startswith("When the game was started outside the Studio"))

    def test_a_folder_changed_since_play_is_not_play_s_result(self):
        checked = self.check(by_play=False, changed=True)
        self.assertFalse(checked.fault)
        said = checked.describe()
        self.assertIn("changed outside the Studio since Play wrote it", said)
        self.assertNotIn("Sneaky", said)

    def test_a_modid_with_brackets_is_read_whole(self):
        from anno_modding_studio.core import loaderlog

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mod-loader.log"
            path.write_text(
                "[2026-09-24 23:09:53.163] [info] Load [Test] Loose (Studio) (unversioned, "
                "[Test] Loose (Studio)) from mods\\\\[Test] Loose (Studio)\\n",
                encoding="utf-8",
            )
            (mod,) = loaderlog.read(ANNO_1800, path).mods
        self.assertEqual((mod.name, mod.mod_id, mod.folder), ("[Test] Loose (Studio)",) * 3)


if __name__ == "__main__":
    unittest.main()
