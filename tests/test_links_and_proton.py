"""The mods folder's links where there are no junctions, and whether a game
run through Proton can see what they point at
(docs/proposals/patches-proposal.md, section 7 and phase 6).

Junctions are Windows'; Linux has symbolic links, which Windows lets a
program make only with developer mode on.  What a symbolic link does is
tested here through stand-ins, and once for real where the system allows.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import library, proton, wine  # noqa: E402


class _Temp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class TestLinksWithoutJunctions(_Temp):
    def test_a_link_is_a_symbolic_link_where_there_are_no_junctions(self):
        target = self.root / "library" / "Mod"
        target.mkdir(parents=True)
        with mock.patch.object(library, "_JUNCTIONS", False), mock.patch.object(library.os, "symlink") as made:
            library.make_link(self.root / "mods" / "Mod", target)
        made.assert_called_once_with(str(target.absolute()), str(self.root / "mods" / "Mod"),
                                     target_is_directory=True)

    def test_taking_one_away_unlinks_it_and_leaves_the_folder(self):
        with mock.patch.object(library, "_JUNCTIONS", False), \
                mock.patch.object(library, "is_junction", return_value=False), \
                mock.patch.object(library.os, "unlink") as gone:
            library.remove_link(self.root / "mods" / "Mod")
        gone.assert_called_once_with(self.root / "mods" / "Mod")

    def test_any_symbolic_link_is_a_link_and_a_relative_one_points_from_where_it_stands(self):
        link = self.root / "mods" / "Mod"
        with mock.patch.object(library.os.path, "islink", return_value=True), \
                mock.patch.object(library.os, "readlink", return_value=os.path.join("..", "library", "Mod")):
            self.assertTrue(library.is_link(link))
            self.assertEqual(library.target_of(link), Path(os.path.normpath(self.root / "library" / "Mod")))

    def test_a_real_symbolic_link_where_the_system_allows_one(self):
        target = self.root / "library" / "Mod"
        target.mkdir(parents=True)
        (self.root / "mods").mkdir()
        link = self.root / "mods" / "Mod"
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except OSError:
            self.skipTest("this system makes no symbolic links for a program without developer mode")
        self.assertTrue(library.is_link(link))
        self.assertEqual(library.target_of(link), target)
        library.remove_link(link)
        self.assertFalse(link.exists())
        self.assertTrue(target.is_dir())


class TestWhatProtonSees(_Temp):
    """A Steam folder as Linux has it, laid out under a temporary folder."""

    def setUp(self):
        super().setUp()
        self.home = self.root / "home"
        self.steam = self.home / ".local" / "share" / "Steam"
        self.install = self.steam / "steamapps" / "common" / "Anno 1800"
        self.mods = self.install / "mods"
        self.mods.mkdir(parents=True)
        (self.steam / "steamapps" / "appmanifest_916440.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"916440"\n\t"installdir"\t\t"Anno 1800"\n}\n', encoding="utf-8")
        self.other = self.root / "games" / "SteamLibrary"
        (self.other / "steamapps" / "common").mkdir(parents=True)

    def unseen(self, library_dir, mods=None):
        return proton.unseen(
            library_dir, mods or self.mods, roots=[self.steam],
            libraries=[self.steam / "steamapps" / "common", self.other / "steamapps" / "common"],
            home=self.home, windows=False,
        )

    def launch_options(self, options: str):
        config = self.steam / "userdata" / "123" / "config"
        config.mkdir(parents=True)
        (config / "localconfig.vdf").write_text(
            '"UserLocalConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
            '\t\t\t\t"apps"\n\t\t\t\t{\n\t\t\t\t\t"916440"\n\t\t\t\t\t{\n'
            '\t\t\t\t\t\t"cloud"\n\t\t\t\t\t\t{\n\t\t\t\t\t\t\t"last_sync_state"\t\t"synchronized"\n'
            '\t\t\t\t\t\t}\n'
            '\t\t\t\t\t\t"LaunchOptions"\t\t"%s"\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n' % options,
            encoding="utf-8")

    def test_a_library_in_the_home_folder_or_a_steam_library_is_seen(self):
        self.assertEqual(self.unseen(self.home / ".local" / "share" / "AnnoModdingStudio" / "library" / "anno1800"), [])
        self.assertEqual(self.unseen(self.other / "AnnoLibrary" / "anno1800"), [])

    def test_one_anywhere_else_is_said_with_what_to_do(self):
        (line,) = self.unseen(self.root / "data" / "library" / "anno1800")
        self.assertIn(str(self.root / "data" / "library"), line)
        self.assertIn("STEAM_COMPAT_MOUNTS=", line)

    def test_a_folder_the_launch_options_mount_is_seen(self):
        self.launch_options('STEAM_COMPAT_MOUNTS=/srv:%s %%command%%' % (self.root / "data").as_posix())
        self.assertEqual(self.unseen(self.root / "data" / "library" / "anno1800"), [])

    def test_the_options_of_another_game_do_not_count(self):
        self.launch_options('STEAM_COMPAT_MOUNTS=%s %%command%%' % (self.root / "data").as_posix())
        other = self.steam / "steamapps" / "common" / "Other" / "mods"
        other.mkdir(parents=True)
        self.assertEqual(len(self.unseen(self.root / "data" / "library" / "anno1800", other)), 1)

    def test_nothing_is_said_on_windows_or_for_a_mods_folder_steam_does_not_hold(self):
        far = self.root / "data" / "library" / "anno1800"
        self.assertEqual(proton.unseen(far, self.mods, roots=[], libraries=[], home=self.home, windows=True), [])
        self.assertEqual(self.unseen(far, self.root / "Ubisoft" / "Anno 1800" / "mods"), [])

    def test_the_app_is_read_off_the_prefix_or_the_install(self):
        prefix = self.steam / "steamapps" / "compatdata" / "916440" / "pfx" / "drive_c" / "mods"
        self.assertEqual(proton.app_of(prefix), 916440)
        self.assertEqual(proton.app_of(self.mods), 916440)

    def test_launch_options_are_read_past_quotes_and_braces(self):
        text = ('"apps" { "1" { "LaunchOptions" "x" } "916440" { "a" { "b" "}" } '
                '"LaunchOptions" "PRESSURE_VESSEL_FILESYSTEMS_RW=\\"/a b:/c\\" %command%" } }')
        self.assertEqual(proton._launch_options(text, 916440),
                         'PRESSURE_VESSEL_FILESYSTEMS_RW="/a b:/c" %command%')


class TestWhichWine(unittest.TestCase):
    def test_this_is_not_wine(self):
        self.assertEqual(wine.version(), "")
        self.assertIsNone(wine.host())
        self.assertEqual(wine.junctions_note(), "")

    def test_junctions_from_10_19_on(self):
        for text, makes in (("10.18", False), ("10.0 (Staging)", False), ("9.0-rc3", False),
                            ("10.19", True), ("11.0", True), ("12.3", True)):
            self.assertEqual(wine.makes_junctions(text), makes, text)

    def test_a_version_that_cannot_be_read_says_nothing(self):
        self.assertTrue(wine.makes_junctions(""))
        self.assertTrue(wine.makes_junctions("proton"))

    def test_the_note_names_the_wine_and_what_to_run_instead(self):
        note = wine.junctions_note("10.0")
        self.assertIn("10.0", note)
        self.assertIn("Proton 11", note)
        self.assertEqual(wine.junctions_note("11.0"), "")


class TestJunctionsUnderWine(_Temp):
    def test_a_folder_with_the_tag_and_no_target_is_no_junction(self):
        """Wine gives a Linux mount point the junction's tag and no target."""
        tag = getattr(library.stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
        if tag is None:
            self.skipTest("no junction tag off Windows")
        mounted = self.root / "mounted"
        mounted.mkdir()
        found = mock.Mock(st_reparse_tag=tag)
        with mock.patch.object(library.os, "lstat", return_value=found):
            with mock.patch.object(library.os, "readlink", side_effect=OSError(4390, "not a reparse point")):
                self.assertFalse(library.is_junction(mounted))
                self.assertFalse(library.is_link(mounted))
            with mock.patch.object(library.os, "readlink", return_value=str(self.root / "elsewhere")):
                self.assertTrue(library.is_junction(mounted))

    def test_a_link_an_old_wine_cannot_make_says_why(self):
        import _winapi

        refused = OSError(50, "The request is not supported")
        with mock.patch.object(_winapi, "CreateJunction", side_effect=refused):
            with mock.patch.object(library.wine, "version", return_value="10.0"), \
                    self.assertRaises(OSError) as said:
                library.make_junction(self.root / "link", self.root / "target")
            self.assertIn("Proton 11", str(said.exception))
            self.assertIs(said.exception.__cause__, refused)
            with self.assertRaises(OSError) as said:
                library.make_junction(self.root / "link", self.root / "target")
            self.assertIs(said.exception, refused)


class _Linux(wine.Host):
    """Wine's Linux side, laid out under a temporary folder that stands for
    ``/``: a Linux path is the folder under it, and back."""

    def __init__(self, top: Path, home: str, drive_c: Path, steam=()):
        super().__init__(home, list(steam), drive_c)
        self.top = top

    def unix(self, path) -> str:
        try:
            inside = Path(path).relative_to(self.top)
        except ValueError:
            return ""
        return "/" if inside == Path(".") else "/" + inside.as_posix()

    def dos(self, unix: str):
        return self.top / str(unix).lstrip("/")


class TestWhatProtonSeesFromWine(_Temp):
    """The Studio under Wine: Steam laid out as on Linux, with the game's prefix."""

    def setUp(self):
        super().setUp()
        self.top = self.root / "unix"
        self.home = self.top / "home" / "u"
        self.steam = self.home / ".local" / "share" / "Steam"
        self.install = self.steam / "steamapps" / "common" / "Anno 1800"
        self.mods = self.install / "mods"
        self.mods.mkdir(parents=True)
        (self.steam / "steamapps" / "appmanifest_916440.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"916440"\n\t"installdir"\t\t"Anno 1800"\n}\n', encoding="utf-8")
        self.pfx = self.steam / "steamapps" / "compatdata" / "916440" / "pfx"
        (self.pfx / "drive_c" / "users").mkdir(parents=True)
        self.other = self.top / "games" / "SteamLibrary"
        (self.other / "steamapps" / "common").mkdir(parents=True)
        (self.steam / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"/home/u/.local/share/Steam"\n\t}\n'
            '\t"1"\n\t{\n\t\t"path"\t\t"/games/SteamLibrary"\n\t}\n}\n', encoding="utf-8")

    def host(self, drive_c=None):
        return _Linux(self.top, "/home/u", self.pfx / "drive_c" if drive_c is None else drive_c)

    def unseen(self, library_dir, mods=None, host=None):
        return proton.unseen_from_wine(library_dir, mods or self.mods, host or self.host())

    def test_a_library_in_the_game_s_prefix_its_home_or_a_steam_library_is_seen(self):
        local = self.pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
        self.assertEqual(self.unseen(local / "AnnoModdingStudio" / "library" / "anno1800"), [])
        self.assertEqual(self.unseen(self.home / "AnnoLibrary" / "anno1800"), [])
        self.assertEqual(self.unseen(self.other / "AnnoLibrary" / "anno1800"), [])

    def test_one_anywhere_else_is_said_by_its_linux_path(self):
        (line,) = self.unseen(self.top / "data" / "library" / "anno1800")
        self.assertIn("STEAM_COMPAT_MOUNTS=/data/library", line)
        self.assertNotIn(str(self.top), line)

    def test_a_folder_the_launch_options_mount_is_seen(self):
        config = self.steam / "userdata" / "123" / "config"
        config.mkdir(parents=True)
        (config / "localconfig.vdf").write_text(
            '"apps" { "916440" { "LaunchOptions" "STEAM_COMPAT_MOUNTS=/srv:/data %command%" } }',
            encoding="utf-8")
        self.assertEqual(self.unseen(self.top / "data" / "library" / "anno1800"), [])

    def test_a_library_on_the_c_drive_of_another_prefix_is_said(self):
        studio_c = self.home / ".wine" / "drive_c"
        studio_c.mkdir(parents=True)
        (line,) = self.unseen(studio_c / "AnnoModdingStudio" / "library" / "anno1800",
                              host=self.host(studio_c))
        self.assertIn("protontricks-launch --appid 916440", line)
        # A library in the home folder is the same folder from every prefix.
        self.assertEqual(self.unseen(self.home / "AnnoLibrary" / "anno1800", host=self.host(studio_c)), [])

    def test_a_mods_folder_in_the_prefix_is_the_game_s_prefix(self):
        documents = self.pfx / "drive_c" / "users" / "steamuser" / "Documents" / "Anno 117" / "mods"
        documents.mkdir(parents=True)
        self.assertEqual(self.unseen(self.pfx / "drive_c" / "library" / "anno117", documents), [])
        (line,) = self.unseen(self.top / "data" / "library" / "anno117", documents)
        self.assertIn("/data/library", line)

    def test_nothing_is_said_for_a_game_outside_steam(self):
        plain_c = self.home / ".wine" / "drive_c"
        mods = plain_c / "Ubisoft" / "Anno 1800" / "mods"
        mods.mkdir(parents=True)
        far = self.top / "data" / "library" / "anno1800"
        self.assertEqual(self.unseen(far, mods, host=self.host(plain_c)), [])

    def test_unseen_asks_wine_where_the_studio_runs_under_it(self):
        far = self.top / "data" / "library" / "anno1800"
        with mock.patch.object(proton.wine, "version", return_value="11.0"), \
                mock.patch.object(proton.wine, "host", return_value=self.host()):
            self.assertEqual(len(proton.unseen(far, self.mods, windows=True)), 1)


if __name__ == "__main__":
    unittest.main()
