"""A mod folder, and a layer's origin, each read one way."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import convert, dependencies, modfolder  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402


class OriginTest(unittest.TestCase):
    """``<folder>``, ``<host>/<folder>``, ``<mod.io id>/<folder>``, and a mod
    bundled inside a downloaded one, ``<mod.io id>/<host>/<folder>``."""

    def test_a_mod_of_its_own(self):
        self.assertFalse(modfolder.is_bundled("Their Mod"))
        self.assertEqual(modfolder.host_of("Their Mod"), "Their Mod")
        self.assertEqual(modfolder.cache_id("Their Mod"), 0)
        self.assertEqual(modfolder.folder_name("Their Mod"), "Their Mod")

    def test_a_bundled_mod_answers_to_its_top_level_host(self):
        self.assertTrue(modfolder.is_bundled("Host/shared piece"))
        self.assertEqual(modfolder.host_of("Host/shared piece"), "Host")
        self.assertEqual(modfolder.host_of("Host/inner/deeper"), "Host")
        self.assertEqual(modfolder.folder_name("Host/inner/deeper"), "deeper")

    def test_the_download_folder_is_not_a_host(self):
        self.assertFalse(modfolder.is_bundled("123456/Their Mod"))
        self.assertEqual(modfolder.host_of("123456/Their Mod"), "123456/Their Mod")
        self.assertEqual(modfolder.cache_id("123456/Their Mod"), 123456)

    def test_but_a_mod_inside_a_downloaded_one_is_bundled_in_it(self):
        self.assertTrue(modfolder.is_bundled("123456/Host/shared piece"))
        self.assertEqual(modfolder.host_of("123456/Host/shared piece"), "123456/Host")
        self.assertEqual(modfolder.cache_id("123456/Host/shared piece"), 123456)

    def test_the_loadout_s_own_mod(self):
        self.assertFalse(modfolder.is_bundled(""))
        self.assertEqual(modfolder.cache_id(""), 0)

    def test_the_switch_is_the_outermost_listed_mod_and_the_row_the_innermost(self):
        listed = {"Host", "Host/inner", "Host/inner/deeper", "123456/Other/piece", "Gone/piece"}
        self.assertEqual(modfolder.switch_holder("Host/inner/deeper", listed), "Host")
        self.assertEqual(modfolder.listed_host("Host/inner/deeper", listed), "Host/inner")
        self.assertEqual(modfolder.switch_holder("Host", listed), "Host")
        self.assertEqual(modfolder.listed_host("Host", listed), "")
        # A host that is not listed leaves the mod inside it its own switch;
        # a download folder is never a host.
        self.assertEqual(modfolder.switch_holder("Gone/piece", listed), "Gone/piece")
        self.assertEqual(modfolder.listed_host("Gone/piece", listed), "")
        self.assertEqual(modfolder.switch_holder("123456/Other/piece", listed), "123456/Other/piece")
        self.assertEqual(modfolder.listed_host("123456/Other/piece", listed | {"123456/Other"}),
                         "123456/Other")
        self.assertEqual(modfolder.listed_host("123456/Other", listed | {"123456"}), "")
        self.assertEqual(modfolder.switch_holder("Not here", listed), "")


class ModKeyTest(unittest.TestCase):
    def test_the_mod_id_or_the_bare_folder_name_lower_cased(self):
        self.assertEqual(modfolder.mod_key("Shared-Pools", "-[Shared] Pools"), "shared-pools")
        self.assertEqual(modfolder.mod_key("", "-[Shared] Pools"), "[shared] pools")

    def test_which_copy_loads_is_keyed_by_the_mod_id_as_written(self):
        """P8-30: the loader's map of copies is keyed by the ModID string, case
        and all, so ``Shared-Pools`` and ``shared-pools`` are two mods to it."""
        self.assertEqual(modfolder.loader_key("Shared-Pools ", "-[Shared] Pools"), "Shared-Pools")
        self.assertNotEqual(modfolder.loader_key("shared-pools", "x"),
                            modfolder.loader_key("Shared-Pools", "x"))
        self.assertEqual(modfolder.loader_key("", "-[Shared] Pools"), "[shared] pools")


class LoadingCopyTest(unittest.TestCase):
    """Which copy of one ModID loads: the loader's AddMod, walking the cache first."""

    @staticmethod
    def copy(name, version, in_cache=False):
        return SimpleNamespace(name=name, version=version, in_cache=in_cache)

    def test_the_highest_version(self):
        old, new = self.copy("old", "1.9"), self.copy("new", "1.10")
        self.assertIs(modfolder.loading_copy([new, old]), new)
        self.assertIs(modfolder.loading_copy([old, new]), new)

    def test_between_equals_the_last_it_comes_to(self):
        a, b = self.copy("a", "1.2"), self.copy("b", "1.2")
        self.assertIs(modfolder.loading_copy([a, b]), b)

    def test_it_comes_to_the_cache_first_so_a_copy_in_the_mods_folder_wins(self):
        folder, cached = self.copy("folder", "1.2"), self.copy("cached", "1.2", in_cache=True)
        self.assertIs(modfolder.loading_copy([folder, cached]), folder)

    def test_versions_as_the_loader_reads_them(self):
        # v2 is 0.0.0 to the loader; a fourth part counts for nothing.
        v2, older = self.copy("v2", "v2"), self.copy("older", "1.4")
        self.assertIs(modfolder.loading_copy([v2, older]), older)
        a, b = self.copy("a", "25.1.0.9"), self.copy("b", "25.1.0.0")
        self.assertIs(modfolder.loading_copy([a, b]), b)

    def test_the_log_s_word_over_all_of_it(self):
        old, new = self.copy("old", "1.0"), self.copy("new", "2.0")
        self.assertIs(modfolder.loading_copy([old, new], named=lambda copy: copy.name == "old"), old)

    def test_none_of_none(self):
        self.assertIsNone(modfolder.loading_copy([]))


class BundledInADownloadTest(unittest.TestCase):
    """What changed: ``truth`` and ``convert`` read ``<id>/<host>/<folder>``
    as a mod of its own, so it was switched on its own and not with the
    downloaded mod it sits in; ``dependencies`` and ``patching`` already read
    it as bundled."""

    def loadout(self):
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="123456/Host", name="Host", active=False))
        loadout.set_layer(ModLayer(origin="123456/Host/shared piece", name="Shared", active=True))
        return loadout

    def test_it_is_off_while_the_mod_it_sits_in_is(self):
        on = [layer.origin for layer in dependencies.switched_on(self.loadout())]
        self.assertEqual(on, [])

    def test_a_found_profile_switches_it_with_that_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = convert.FoundProfile(name="p", source="iModYourAnno", path=Path(tmp) / "p", on={"host"})
            switched, _missing = convert.profile_switches(self.loadout(), found)
        self.assertTrue(switched["123456/Host"])
        self.assertTrue(switched["123456/Host/shared piece"])


class FolderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_json_before_jsonc_and_nothing_without_either(self):
        mod = self.root / "Mod"
        mod.mkdir()
        self.assertIsNone(modfolder.modinfo_of(mod))
        self.assertEqual(modfolder.metadata_of(mod), {})
        (mod / "modinfo.jsonc").write_text('{"ModID": "from-jsonc" // said twice\n}', encoding="utf-8")
        self.assertEqual(modfolder.metadata_of(mod)["mod_id"], "from-jsonc")
        (mod / "modinfo.json").write_text('{"ModID": "from-json"}', encoding="utf-8")
        self.assertEqual(modfolder.metadata_of(mod)["mod_id"], "from-json")

    def test_mods_folders_list_bundled_mods_and_skip_dot_folders(self):
        (self.root / "-Off Mod" / "Inner").mkdir(parents=True)
        (self.root / "-Off Mod" / "Inner" / "modinfo.json").write_text("{}", encoding="utf-8")
        (self.root / "A Mod" / ".vscode" / "x").mkdir(parents=True)
        (self.root / "A Mod" / ".vscode" / "x" / "modinfo.json").write_text("{}", encoding="utf-8")
        (self.root / ".git").mkdir()
        found = [path.relative_to(self.root).as_posix() for path in modfolder.mod_folders(self.root)]
        self.assertEqual(found, ["A Mod", "-Off Mod", "-Off Mod/Inner"])


if __name__ == "__main__":
    unittest.main()
