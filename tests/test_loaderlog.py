"""The loader's log: which mods it loaded, in what order, and from where."""

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

from anno_modding_studio.core import loaderlog, modio_cache  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

STAMP = "[2026-09-26 21:51:15.608] [info] "


class TheOrderItRanThemIn(unittest.TestCase):
    """Read from the log's Load lines, each mod by what the Studio calls it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "mod-loader.log"

    def write(self, *lines):
        self.log.write_text("".join(STAMP + line + "\n" for line in lines), encoding="utf-8")

    def test_a_bundled_mod_has_a_place_of_its_own(self):
        self.write(
            "Load mods",
            "Load [Gameplay] Second (1.0, second) from mods\\B Mod",
            "Load [Shared] Bits (1.0, bits) from mods\\A Mod\\bits",
            "Load [Gameplay] First (1.0, first) from mods\\A Mod",
        )
        # Here in front of the mod it is sitting in.
        self.assertEqual(loaderlog.logged_order(ANNO_1800, self.log), ["B Mod", "A Mod/bits", "A Mod"])

    def test_a_switched_off_folder_is_named_without_its_mark(self):
        self.write('Load "A Mod" a-mod@1.0 (from "C:/Games/x/mods/-A Mod")')
        self.assertEqual(loaderlog.logged_order(ANNO_117, self.log), ["A Mod"])

    def test_with_no_log_there_is_nothing_to_read(self):
        self.assertEqual(loaderlog.logged_order(ANNO_1800, self.log), [])
        self.assertEqual(loaderlog.logged_cache_ids(ANNO_1800, self.log), {})

    def test_a_mod_loaded_from_the_modio_cache_is_named_under_its_id_folder(self):
        """1800 writes the cache's absolute path where every other mod is
        relative; the numeric folder is the mod.io id, and the log gives it
        away for nothing."""
        with self.cache_at("C:\\Users\\Public\\mod.io"):
            self.write(
                "Load [Gameplay] First (1.0, first) from mods\\A Mod",
                "Load [Building] Windmill (2.0.1, windmill) from "
                "C:\\Users\\Public\\mod.io\\4169\\mods\\3229975\\[Building] Windmill",
                "Load [Shared] Bits (1.0, bits) from "
                "C:\\Users\\Public\\mod.io\\4169\\mods\\3229975\\[Building] Windmill\\bits",
            )
            self.assertEqual(
                loaderlog.logged_order(ANNO_1800, self.log),
                ["A Mod", "3229975/[Building] Windmill", "3229975/[Building] Windmill/bits"],
            )
            self.assertEqual(
                loaderlog.logged_cache_ids(ANNO_1800, self.log),
                {"3229975/[Building] Windmill": 3229975, "3229975/[Building] Windmill/bits": 3229975},
            )

    def cache_at(self, root: str):
        """The game's mod.io client keeping its files at ``root``: only a
        name here, as nothing is read from it."""
        return mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: root})

    def test_the_cache_is_where_the_client_keeps_it_wherever_that_is(self):
        """The client's RootLocalStoragePath can move its folder anywhere --
        under a folder of its own name, or under one called Mods -- and a mod
        can have its files straight in its id folder.  Each is a cache mod,
        named as the mods folder read names it."""
        for root, line, name, mod_id in (
            ("D:\\ModioStore", "D:\\ModioStore\\4169\\mods\\111\\[A] Custom", "111/[A] Custom", 111),
            ("D:\\Mods\\mod.io", "D:\\Mods\\mod.io\\4169\\mods\\222\\[B] Upper", "222/[B] Upper", 222),
            ("C:\\Users\\Public\\mod.io", "C:\\Users\\Public\\mod.io\\4169\\mods\\555", "555", 555),
            ("d:\\modiostore", "D:\\ModioStore\\4169\\Mods\\111\\[A] Custom", "111/[A] Custom", 111),
        ):
            with self.subTest(line=line), self.cache_at(root):
                self.write("Load [A] Mod (1.0, a) from %s" % line)
                self.assertEqual(loaderlog.logged_order(ANNO_1800, self.log), [name])
                self.assertEqual(loaderlog.logged_cache_ids(ANNO_1800, self.log), {name: mod_id})
                logged = loaderlog.read(ANNO_1800, self.log).loaded[0]
                self.assertEqual((logged.folder, logged.bundled), (name.split("/")[-1], False))

    def test_a_folder_called_mod_io_elsewhere_is_not_the_cache(self):
        with self.cache_at("C:\\Users\\Public\\mod.io"):
            self.write("Load [A] Mod (1.0, a) from D:\\Old\\mod.io\\4169\\mods\\111\\[A] Mod")
            self.assertEqual(loaderlog.logged_cache_ids(ANNO_1800, self.log), {})


# The D1 probe's log, from the game on 16 Oct 2026, its Documents mods folder
# shortened to "mods": three hosts with a mod bundled in each.  The bundled
# ones load by the loader's sort, between hosts B and C, and are named on
# their Load and warning lines by ModID alone.
PROBE_117 = (
    "Enabled mod: mods/[Test] BundledProbe A Host  (bundledprobe-a-host@1.0)",
    "Enabled mod: mods/[Test] BundledProbe B Host  (bundledprobe-b-host@1.0)",
    "Enabled mod: mods/[Test] BundledProbe A Host/Bundled a [SubMod] (bundledprobe-a-bundled@1.0)",
    "Enabled mod: mods/[Test] BundledProbe B Host/Bundled b [SubMod] (bundledprobe-b-bundled@1.0)",
    "Enabled mod: mods/[Test] BundledProbe C Host/Bundled c [SubMod] (bundledprobe-c-bundled@1.0)",
    "Enabled mod: mods/[Test] BundledProbe C Host  (bundledprobe-c-host@1.0)",
    "Enabled mods: 3 (plus 3 nested)",
    'Load "[Test] BundledProbe A Host" bundledprobe-a-host@1.0 (from "mods/[Test] BundledProbe A Host")',
    'Load "[Test] BundledProbe B Host" bundledprobe-b-host@1.0 (from "mods/[Test] BundledProbe B Host")',
    "-- Load bundledprobe-a-bundled@1.0 (from bundledprobe-a-host)",
    "bundledprobe-a-bundled: No matching node for Path \"//Asset[Values/Standard/GUID='2001271']"
    "/Values/BundledProbe_a_ran\" (data/base/config/export/assets.xml:3)",
    "-- Load bundledprobe-b-bundled@1.0 (from bundledprobe-b-host)",
    "-- Load bundledprobe-c-bundled@1.0 (from bundledprobe-c-host)",
    'Load "[Test] BundledProbe C Host" bundledprobe-c-host@1.0 (from "mods/[Test] BundledProbe C Host")',
    "bundledprobe-c-host: No matching node for Path \"//Asset[Values/Standard/GUID='2001271']"
    "/Values/BundledProbe_C_ran\" (data/base/config/export/assets.xml:3)",
    "Patching completed in 0s",
)


class TheBundledModsOf117(unittest.TestCase):
    """117 writes a mod bundled inside another -- its [SubMod] -- differently
    from a mod of its own, on every line it has."""

    setUp = TheOrderItRanThemIn.setUp
    cache_at = TheOrderItRanThemIn.cache_at

    def write(self, *lines):
        stamped = [("[2026-10-16 19:55:50.919] [warning] " if ": No matching node" in line else STAMP) + line
                   for line in lines]
        self.log.write_text("".join(line + "\n" for line in stamped), encoding="utf-8")

    def test_each_has_the_place_it_loaded_in(self):
        self.write(*PROBE_117)
        self.assertEqual(loaderlog.logged_order(ANNO_117, self.log), [
            "[Test] BundledProbe A Host", "[Test] BundledProbe B Host",
            "[Test] BundledProbe A Host/Bundled a", "[Test] BundledProbe B Host/Bundled b",
            "[Test] BundledProbe C Host/Bundled c", "[Test] BundledProbe C Host",
        ])

    def test_its_path_is_its_folder_without_the_tag(self):
        self.write(*PROBE_117)
        log = loaderlog.read(ANNO_117, self.log)
        bundled = [mod for mod in log.mods if mod.bundled]
        self.assertEqual([mod.path for mod in bundled], [
            "mods/[Test] BundledProbe A Host/Bundled a",
            "mods/[Test] BundledProbe B Host/Bundled b",
            "mods/[Test] BundledProbe C Host/Bundled c",
        ])
        # Once each: its Enabled line and its Load line are one mod.
        self.assertEqual(len(log.mods), 6)
        self.assertEqual(sorted(mod.mod_id for mod in log.mods), sorted({mod.mod_id for mod in log.mods}))

    def test_a_warning_is_the_mod_s_by_its_modid(self):
        """117 starts one with the ModID, a host's as well as a bundled
        mod's: a host is found by its folder too, a bundled mod by its
        ModID alone."""
        self.write(*PROBE_117)
        log = loaderlog.read(ANNO_117, self.log)
        self.assertEqual(
            [(w.mod_id, w.bundled) for w in log.op_warnings],
            [("bundledprobe-a-bundled", True), ("bundledprobe-c-host", False)],
        )
        self.assertEqual(len(log.warnings_for({"bundledprobe-a-bundled"})), 1)
        self.assertEqual(len(log.warnings_for({"[test] bundledprobe c host"})), 1)
        self.assertEqual(log.warnings_for({"[test] bundledprobe a host"}), [])

    def test_one_in_the_modio_cache_is_named_under_its_id_folder(self):
        with self.cache_at("C:\\Users\\Public\\mod.io"):
            self.write(
                "Enabled mod: C:/Users/Public/mod.io/11358/mods/12/Host  (host@1.0)",
                "Enabled mod: C:/Users/Public/mod.io/11358/mods/12/Host/inner [SubMod] (inner@1.0)",
                'Load "[A] Host" host@1.0 (from "C:/Users/Public/mod.io/11358/mods/12/Host")',
                "-- Load inner@1.0 (from host)",
            )
            self.assertEqual(loaderlog.logged_order(ANNO_117, self.log), ["12/Host", "12/Host/inner"])
            self.assertEqual(loaderlog.logged_cache_ids(ANNO_117, self.log), {"12/Host": 12, "12/Host/inner": 12})

    def test_one_with_no_enabled_line_keeps_its_place_unnamed(self):
        """Not seen: every nested Load line so far had its Enabled line.
        Without one there is no folder to name it by, and it is not
        guessed."""
        self.write("-- Load stray@1.0 (from host)")
        log = loaderlog.read(ANNO_117, self.log)
        self.assertEqual([(mod.mod_id, mod.bundled) for mod in log.loaded], [("stray", True)])
        self.assertEqual(loaderlog.logged_order(ANNO_117, self.log), [])


if __name__ == "__main__":
    unittest.main()
