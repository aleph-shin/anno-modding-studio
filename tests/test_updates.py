"""Which mod.io mod a loaded mod is, and whether mod.io has a newer file."""

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

from anno_modding_studio.core import modio, modio_cache, updates  # noqa: E402
from anno_modding_studio.core.changes import ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402


def _summary(mod_id: int, name: str, version: str, modfile: int = 0, date: int = 0) -> modio.ModSummary:
    return modio.ModSummary(
        id=mod_id, name=name, profile_url="https://mod.io/g/anno-1800/m/%d" % mod_id,
        version=version, modfile_id=modfile, date_updated=date,
    )


def _record(mod_id: int, path: Path, modfile: int = 0, version: str = "") -> modio_cache.CachedMod:
    return modio_cache.CachedMod(mod_id=mod_id, name="x", path=path, modfile_id=modfile, version=version)


class TestNames(unittest.TestCase):
    def test_category_prefixes_come_off(self):
        self.assertEqual(updates.strip_category("[Gameplay] [Seal] Thing (Kurila)"), "Thing (Kurila)")
        self.assertEqual(updates.strip_category("Thing"), "Thing")

    def test_the_same_mod_under_two_category_prefixes(self):
        self.assertTrue(updates.same_name("[Gameplay] Extended Agriculture", "[Seal] extended  agriculture"))
        self.assertFalse(updates.same_name("Extended Agriculture", "Extended Agriculture 2"))

    def test_a_folder_name_written_another_way_is_still_the_mod(self):
        """Two Anno 117 mods installed by hand were "not found on mod.io"."""
        self.assertTrue(updates.named_alike("Hexagon Maptemplate (Taludas)", "Hexagon Map Template (Taludas)"))
        self.assertTrue(updates.named_alike("Early Build On Water (CheapOrange)", "Early Build On Water"))
        self.assertTrue(updates.named_alike("Early Build On Water", "[Gameplay] Early-Build on Water (Cheap-Orange)"))

    def test_two_authors_in_brackets_are_two_mods(self):
        self.assertFalse(updates.named_alike("Thing (Kurila)", "Thing (Taludas)"))
        self.assertFalse(updates.named_alike("Thing", "Thing 2"))
        self.assertFalse(updates.named_alike("(Kurila)", "(Taludas)"))

    def test_the_same_name_wins_over_one_named_alike(self):
        alike, same = _summary(1, "Thing (Kurila)", "1.0"), _summary(2, "Thing", "1.0")
        self.assertIs(updates.called([alike, same], "Thing"), same)
        self.assertIs(updates.called([alike], "Thing"), alike)
        self.assertIsNone(updates.called([_summary(3, "Other", "1.0")], "Thing"))


class TestIdentifying(unittest.TestCase):
    def test_the_log_wins_and_brings_the_caches_file_with_it(self):
        cache_path = Path("C:/Users/Public/mod.io/4169/mods/3229975")
        layer = ModLayer(
            origin="3229975/[Building] Windmill", name="Windmill", mod_id="jakob_windmill",
            folder=str(cache_path / "[Building] Windmill"), version="2.0.1",
        )
        cache = {"jakob_windmill": _record(3229975, cache_path, modfile=555)}
        (match,) = updates.identify([layer], {"3229975/[Building] Windmill": 3229975}, cache)
        self.assertEqual((match.modio_id, match.how), (3229975, updates.LOG))
        self.assertEqual(match.installed_file, 555)

    def test_the_id_belongs_to_the_modid_so_a_hand_installed_copy_gets_it_too(self):
        """Nineteen mods in the cache, one loaded from it: the rest run from
        copies installed by hand under other folder names."""
        cache_path = Path("C:/Users/Public/mod.io/4169/mods/3031538")
        by_hand = ModLayer(
            origin="[Seal] Extended Agriculture", name="Extended Agriculture",
            mod_id="extended_agriculture_kurila", folder="C:/Games/Anno 1800/mods/[Seal] Extended Agriculture",
            version="1.6.3",
        )
        cache = {"extended_agriculture_kurila": _record(3031538, cache_path, modfile=777, version="1.6.3")}
        (match,) = updates.identify([by_hand], {}, cache)
        self.assertEqual((match.modio_id, match.how), (3031538, updates.CACHE))
        # But not the cache's file: this copy is not the one the cache put there.
        self.assertEqual(match.installed_file, 0)
        self.assertEqual(match.installed_version, "1.6.3")

    def test_a_remembered_link_is_used_when_nothing_firmer_is(self):
        layer = ModLayer(origin="X", name="X", mod_id="x", modio_id=42)
        (match,) = updates.identify([layer], {}, {})
        self.assertEqual((match.modio_id, match.how), (42, updates.LOADOUT))

    def test_a_copy_the_studio_installed_is_compared_by_its_file(self):
        """The library wrote down which file it installed. The install plan
        goes by that; the update check went by version text, and said
        "current" of a file mod.io had replaced under the same version."""
        from anno_modding_studio.core import library

        with tempfile.TemporaryDirectory() as tmp:
            held = Path(tmp) / "library"
            copy = held / "[Gameplay] Thing"
            copy.mkdir(parents=True)
            library.record(held, copy.name, modio_id=4242, modfile_id=900, version="1.0")
            layer = ModLayer(origin=copy.name, name="Thing", mod_id="thing",
                             folder=str(copy), version="1.0")
            (plain,) = updates.identify([layer], {}, {})
            self.assertFalse(plain.matched)
            (match,) = updates.identify([layer], {}, {}, library_dir=held)
            self.assertEqual((match.modio_id, match.how, match.installed_file),
                             (4242, updates.LOADOUT, 900))
            latest = modio.ModSummary(id=4242, name="Thing", version="1.0", modfile_id=901)
            self.assertEqual(updates.compare(match, latest).status, updates.UPDATE)
            # A folder outside the library is nobody's copy.
            other = ModLayer(origin="X", name="X", folder=str(Path(tmp) / "elsewhere" / copy.name))
            (unknown,) = updates.identify([other], {}, {}, library_dir=held)
            self.assertFalse(unknown.matched)

    def test_otherwise_unmatched(self):
        (match,) = updates.identify([ModLayer(origin="X", name="X")], {}, {})
        self.assertFalse(match.matched)
        self.assertEqual(match.how, "")


class TestComparing(unittest.TestCase):
    def _verdict(self, installed_version="", installed_file=0, latest=None, modio_id=1):
        match = updates.Match(
            origin="o", name="n", mod_id="m", installed_version=installed_version,
            modio_id=modio_id, how=updates.CACHE, installed_file=installed_file,
        )
        return updates.compare(match, latest)

    def test_files_are_compared_when_the_cache_knows_which_is_installed(self):
        self.assertEqual(self._verdict(installed_file=5, latest=_summary(1, "n", "9.9", modfile=5)).status, updates.CURRENT)
        self.assertEqual(self._verdict(installed_file=5, latest=_summary(1, "n", "9.9", modfile=6)).status, updates.UPDATE)

    def test_versions_that_read_as_numbers_are_ordered(self):
        self.assertEqual(self._verdict("1.6.3", latest=_summary(1, "n", "1.7.0")).status, updates.UPDATE)
        self.assertEqual(self._verdict("1.7.0", latest=_summary(1, "n", "1.7.0")).status, updates.CURRENT)
        self.assertEqual(self._verdict("2.0", latest=_summary(1, "n", "1.9")).status, updates.AHEAD)

    def test_zeros_at_the_end_do_not_make_another_version(self):
        self.assertEqual(self._verdict("1.0", latest=_summary(1, "n", "1.0.0")).status, updates.CURRENT)
        self.assertEqual(self._verdict("1.0.0", latest=_summary(1, "n", "1.0")).status, updates.CURRENT)
        self.assertEqual(self._verdict("1.0", latest=_summary(1, "n", "1.0.1")).status, updates.UPDATE)
        self.assertEqual(self._verdict("1.1", latest=_summary(1, "n", "1.0.9")).status, updates.AHEAD)
        self.assertEqual(self._verdict("1.0a", latest=_summary(1, "n", "1.0.0b")).status, updates.DIFFERENT)

    def test_versions_that_do_not_order_are_only_different(self):
        self.assertEqual(self._verdict("beta", latest=_summary(1, "n", "final")).status, updates.DIFFERENT)
        self.assertEqual(self._verdict("1.0a", latest=_summary(1, "n", "1.0b")).status, updates.DIFFERENT)
        self.assertEqual(self._verdict("1.0a", latest=_summary(1, "n", "1.0.0b")).status, updates.DIFFERENT)

    def test_zeros_at_the_end_are_the_same_version(self):
        # As releases.Suggestion.met and studio_release.is_newer count them.
        self.assertEqual(self._verdict("1.0", latest=_summary(1, "n", "1.0.0")).status, updates.CURRENT)
        self.assertEqual(self._verdict("1.3.0", latest=_summary(1, "n", "1.3")).status, updates.CURRENT)
        self.assertEqual(self._verdict("1.0", latest=_summary(1, "n", "1.0.1")).status, updates.UPDATE)
        self.assertEqual(self._verdict("1.0.1", latest=_summary(1, "n", "1.0")).status, updates.AHEAD)

    def test_nothing_to_compare_by(self):
        self.assertEqual(self._verdict("", latest=_summary(1, "n", "1.0")).status, updates.UNKNOWN)

    def test_matched_but_gone_from_modio(self):
        self.assertEqual(self._verdict("1.0", latest=None).status, updates.MISSING)

    def test_unmatched(self):
        self.assertEqual(self._verdict("1.0", latest=None, modio_id=0).status, updates.UNMATCHED)


class FakeClient:
    def __init__(self, known, by_name):
        self.known = known
        self.by_name = by_name
        self.searched = []
        # mod id -> {md5: modfile id}, for every file the mod has had.
        self.files = {}
        self.asked_files = []

    def mods_by_ids(self, game_id, ids):
        return {i: self.known[i] for i in ids if i in self.known}

    def search(self, game_id, query, limit=20):
        self.searched.append(query)
        return self.by_name.get(query.casefold(), [])

    def file_with_md5(self, game_id, mod_id, md5):
        self.asked_files.append(mod_id)
        return self.files.get(mod_id, {}).get(md5, 0)


class TestTheWholeCheck(unittest.TestCase):
    def test_matched_mods_are_asked_in_one_go_and_the_rest_by_name(self):
        known = {1: _summary(1, "Alpha", "2.0")}
        by_name = {"beta": [_summary(2, "Beta Two", "1.0"), _summary(3, "[Cheat] Beta", "1.5")]}
        client = FakeClient(known, by_name)
        matches = [
            updates.Match(origin="a", name="Alpha", mod_id="a", installed_version="1.0", modio_id=1, how=updates.CACHE),
            updates.Match(origin="b", name="[Gameplay] Beta", mod_id="b", installed_version="1.5"),
            updates.Match(origin="c", name="Gamma", mod_id="c", installed_version="1.0"),
        ]
        seen = []
        verdicts = updates.check(client, 4169, matches, progress=lambda d, t, n: seen.append((d, t)))
        self.assertEqual([v.status for v in verdicts], [updates.UPDATE, updates.CURRENT, updates.UNMATCHED])
        self.assertEqual((verdicts[1].modio_id, verdicts[1].how), (3, updates.NAME))
        self.assertEqual(client.searched, ["Beta", "Gamma"])
        self.assertEqual(seen[-1], (3, 3))

    def test_a_mod_installed_by_hand_under_its_authors_name_is_found(self):
        by_name = {"early build on water (cheaporange)": [
            _summary(4, "More water in aqueduct (Lukinor)", "1.0"), _summary(5, "Early Build On Water", "1.4.1"),
        ]}
        match = updates.Match(origin="e", name="Early Build On Water (CheapOrange)", mod_id="e", installed_version="1.4.1")
        (verdict,) = updates.check(FakeClient({}, by_name), 11358, [match])
        self.assertEqual((verdict.status, verdict.modio_id, verdict.how), (updates.CURRENT, 5, updates.NAME))


class TestTheZipACopyWasUnpackedFrom(unittest.TestCase):
    """mod.io publishes each file's MD5, so a zip downloaded there by hand
    says which mod it is and which of its files, as a name cannot."""

    def check(self, client, **match):
        match.setdefault("installed_version", "1.0")
        (verdict,) = updates.check(client, 4169, [updates.Match(origin="a", mod_id="a", **match)])
        return verdict

    def client(self, md5="", files=None, by_name=None):
        import dataclasses

        latest = dataclasses.replace(_summary(1, "Alpha", "2.0", modfile=20), md5=md5)
        client = FakeClient({1: latest}, by_name or {})
        client.files = files or {}
        return client

    def test_the_current_file_is_told_without_asking_again(self):
        client = self.client(md5="aa")
        # The version string says older; the file says it is this one.
        verdict = self.check(client, name="Alpha", modio_id=1, how=updates.NAME, zip_md5="AA")
        self.assertEqual((verdict.status, verdict.how, verdict.installed_file),
                         (updates.CURRENT, updates.HASH, 20))
        self.assertEqual(client.asked_files, [])

    def test_an_older_file_is_found_among_the_mods_files(self):
        client = self.client(md5="bb", files={1: {"aa": 10}})
        # The version string says the same; the file says there is a newer one.
        verdict = self.check(client, name="Alpha", modio_id=1, how=updates.LOADOUT,
                             zip_md5="aa", installed_version="2.0")
        self.assertEqual((verdict.status, verdict.how, verdict.installed_file),
                         (updates.UPDATE, updates.HASH, 10))
        self.assertEqual(client.asked_files, [1])

    def test_a_zip_that_is_none_of_them_leaves_the_match_as_it_was(self):
        client = self.client(md5="bb")
        verdict = self.check(client, name="Alpha", modio_id=1, how=updates.NAME, zip_md5="cc")
        self.assertEqual((verdict.status, verdict.how, verdict.installed_file),
                         (updates.UPDATE, updates.NAME, 0))

    def test_a_mod_found_by_name_in_this_check_is_made_sure_of_in_it(self):
        import dataclasses

        named = dataclasses.replace(_summary(3, "Beta", "1.0", modfile=30), md5="dd")
        client = self.client(by_name={"beta": [named]})
        verdict = self.check(client, name="[Gameplay] Beta", zip_md5="dd")
        self.assertEqual((verdict.modio_id, verdict.how, verdict.installed_file, verdict.status),
                         (3, updates.HASH, 30, updates.CURRENT))

    def test_a_copy_with_no_zip_is_never_asked_about(self):
        client = self.client(md5="aa")
        verdict = self.check(client, name="Alpha", modio_id=1, how=updates.NAME)
        self.assertEqual((verdict.how, client.asked_files), (updates.NAME, []))

    def test_mod_io_failing_to_list_the_files_does_not_fail_the_check(self):
        client = self.client(md5="bb")

        def refuse(*_args):
            raise modio.ModioError("no")

        client.file_with_md5 = refuse
        verdict = self.check(client, name="Alpha", modio_id=1, how=updates.NAME, zip_md5="aa")
        self.assertEqual((verdict.status, verdict.how), (updates.UPDATE, updates.NAME))


class TestWhatTheCacheSaysOfTheLibrary(unittest.TestCase):
    """A library filled before it recorded where its copies came from is told
    what the game's own client records say of them, without a network."""

    def test_a_copy_whose_modid_the_client_installed_is_written_down_as_that_mod(self):
        from anno_modding_studio.core import library

        record = modio_cache.CachedMod(mod_id=3031538, name="Host", path=Path("x"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "library"
            for name, mod_id in (("Known", "Host_Kurila"), ("Unknown", "nobody"),
                                 ("Named", "Host_Kurila"), ("No ModID", "")):
                (folder / name).mkdir(parents=True)
                info = {"ModID": mod_id} if mod_id else {}
                (folder / name / "modinfo.json").write_text(json.dumps(info), encoding="utf-8")
                library.write_links(folder, {
                    **library.links(folder),
                    name: {"target": str(folder / name), "kind": library.COPY,
                           "mods_folder": str(root / "mods"), "linked": False},
                })
            library.record(folder, "Named", modio_id=7, modfile_id=70)
            with mock.patch.object(updates, "cache_index", return_value={"host_kurila": record}):
                self.assertEqual(updates.learn_from_cache(folder, ANNO_1800), 1)
                self.assertEqual(updates.learn_from_cache(folder, ANNO_1800), 0)
            known = library.registry(folder)
        self.assertEqual(known["Known"], {"modio_id": 3031538, library.KNOWN_BY: updates.CACHE})
        # What the Studio installed by id is left as it was installed.
        self.assertEqual(known["Named"], {"modio_id": 7, "modfile_id": 70})
        self.assertNotIn("Unknown", known)
        self.assertNotIn("No ModID", known)


class TestTheCacheByModId(unittest.TestCase):
    def test_every_modinfo_under_a_cached_mod_maps_its_modid_to_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            on_disk = root / "4169" / "mods" / "3031538"
            mod = on_disk / "[Gameplay] Host"
            (mod / "shared_piece").mkdir(parents=True)
            (mod / "modinfo.json").write_text(json.dumps({"ModID": "Host_Kurila"}), encoding="utf-8")
            (mod / "shared_piece" / "modinfo.json").write_text(json.dumps({"ModID": "shared-piece"}), encoding="utf-8")
            metadata = root / "4169" / "metadata"
            metadata.mkdir(parents=True)
            (metadata / "state.json").write_text(
                json.dumps({"Mods": [{"ID": 3031538, "PathOnDisk": str(on_disk), "Profile": {"name": "Host", "modfile": {"id": 9, "version": "1"}}}]}),
                encoding="utf-8",
            )
            index = updates.cache_index(ANNO_1800, root)
        self.assertEqual(sorted(index), ["host_kurila", "shared-piece"])
        self.assertEqual(index["host_kurila"].mod_id, 3031538)
        self.assertEqual(index["shared-piece"].modfile_id, 9)


if __name__ == "__main__":
    unittest.main()
