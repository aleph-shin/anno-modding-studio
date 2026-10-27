"""The plan and the queue: which of a list of mods are installed, and fetching
the rest one at a time.

Against a fake mod.io and temporary folders.  The mod.io download cache is a
folder of the test's own as well, since the plan looks in it.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    FakeServer,
    downloadable_mod,
    fake_client,
    install_mod,
    modio_collection,
    zip_bytes,
)

from anno_modding_studio.core import (  # noqa: E402
    disk,
    installplan,
    modio,
    modio_cache,
    places,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402


class _Folders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.cache_root = self.root / "modio"
        for patched in (
            mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: str(self.cache_root)}),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            # The folder under Documents is one of the places the game loads
            # from; the developer's own is not part of any test.
            mock.patch.object(installplan.switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)

    def cached(self, mod_id: int, folder: str, mod: str = "", version: str = "", modfile: int = 0) -> Path:
        placed = install_mod(self.cache_root / "4169" / "mods" / str(mod_id), folder, mod, version)
        state = self.cache_root / "4169" / "metadata" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        held = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {"Mods": []}
        held["Mods"].append({
            "ID": mod_id,
            "PathOnDisk": str(placed.parent),
            "Profile": {"id": mod_id, "name": folder, "modfile": {"id": modfile, "version": version}},
        })
        state.write_text(json.dumps(held), encoding="utf-8")
        return placed


class TestCollectionsOnTheClient(unittest.TestCase):
    def test_a_collection_address_is_told_from_a_mods(self):
        link = modio.parse_page("https://mod.io/g/anno-1800/c/jakobs-collection?tab=mods")
        self.assertEqual((link.game_name_id, link.collection_name_id, link.mod_name_id),
                         ("anno-1800", "jakobs-collection", ""))
        self.assertEqual(modio.parse_page("mod.io/g/anno-1800/m/thing").collection_name_id, "")
        self.assertIsNone(modio.parse_page("https://mod.io/g/anno-1800/collections/x"))

    def test_a_collection_is_summarised_with_an_address_built_for_it(self):
        found = modio.summarise_collection(modio_collection(39, "Jakob's Collection", [1, 2, 3]))
        self.assertEqual(found.url, "https://mod.io/g/anno-1800/c/jakobs-collection")
        self.assertEqual((found.mods_total, found.filesize, found.submitted_by), (3, 2048, "collector"))
        self.assertFalse(found.incomplete)

    def test_searching_and_looking_up_by_slug(self):
        server = FakeServer(collections=[
            modio_collection(39, "Jakob's Collection", [1]), modio_collection(40, "Other", [2]),
        ])
        client = fake_client(server)
        self.assertEqual([c.id for c in client.collections(4169, "jakob")], [39])
        self.assertEqual(client.collection_by_name_id(4169, "Other").id, 40)
        self.assertIsNone(client.collection_by_name_id(4169, "nothing"))
        for url, _headers in server.calls:
            self.assertTrue(url.startswith("https://g-4169.modapi.io/v1/games/4169/collections?"))

    def test_its_mods_are_paged_to_the_end_whatever_the_game_says_the_cap_is(self):
        mods = [downloadable_mod(i, "Mod %d" % i, "1", b"zip%d" % i) for i in range(1, 251)]
        server = FakeServer(mods, collections=[modio_collection(7, "Big", range(1, 251))])
        found = fake_client(server).collection_mods(4169, 7)
        self.assertEqual(len(found), 250)
        self.assertEqual(len(server.calls), 3)
        # Whole mod objects: the file, its size unpacked, and a link to it.
        self.assertEqual(found[0].download_url, "https://cdn/1")
        self.assertEqual(found[0].filesize_uncompressed, len(b"zip1") * 3)


class TestWhatIsInstalled(_Folders):
    def test_every_mod_folder_is_read_with_its_modinfo(self):
        install_mod(self.mods, "[Gameplay] Thing", "kurila_thing", "1.2", "Thing")
        install_mod(self.mods, "-[Misc] Off", "off_mod", "3")
        install_mod(self.mods / "[Gameplay] Thing" / "shared", "Piece", "shared_piece", "1")
        (self.mods / ".modio-downloads").mkdir()
        found = {mod.folder_name: mod for mod in installplan.inventory(ANNO_1800, self.mods)}
        self.assertEqual(sorted(found), ["Piece", "[Gameplay] Thing", "[Misc] Off"])
        self.assertEqual((found["[Gameplay] Thing"].mod_id, found["[Gameplay] Thing"].version,
                          found["[Gameplay] Thing"].name), ("kurila_thing", "1.2", "Thing"))
        self.assertTrue(found["Piece"].bundled)
        self.assertFalse(found["[Misc] Off"].bundled)

    def test_the_cache_says_which_mod_its_folders_are_and_so_which_every_copy_is(self):
        self.cached(3229975, "[Building] Windmill", "jakob_windmill", "2.0", modfile=55)
        install_mod(self.mods, "[Seal] Windmill", "jakob_windmill", "2.0.1")
        found = installplan.inventory(ANNO_1800, self.mods)
        by_hand = next(mod for mod in found if not mod.in_cache)
        cached = next(mod for mod in found if mod.in_cache)
        self.assertEqual((cached.modio_id, cached.modfile_id, cached.bundled), (3229975, 55, False))
        # One ModID, two folders, two names: the id answers for both.
        self.assertEqual(by_hand.modio_id, 3229975)

    def test_a_cache_mod_whose_files_sit_in_the_numbered_folder_is_seen(self):
        """*Building storage improvements* ships its modinfo and data straight
        into ``mods/3813676``. The game loads it; the inventory used to skip
        every numbered folder as a mere container, and so never saw it."""
        root = self.cache_root / "4169" / "mods"
        install_mod(root, "3813676", "Building_storage_improvements", "1.0", "Building storage improvements")
        install_mod(root / "3813676", "inner", "inner_piece", "1")
        found = {mod.folder_name: mod for mod in installplan.inventory(ANNO_1800, self.mods)}
        mod = found["3813676"]
        self.assertEqual((mod.name, mod.modio_id, mod.in_cache, mod.bundled),
                         ("Building storage improvements", 3813676, True, False))
        self.assertTrue(found["inner"].bundled)

    def test_a_loadout_that_remembers_says_so_too(self):
        install_mod(self.mods, "[Gameplay] Thing", "kurila_thing", "1")
        found = installplan.inventory(ANNO_1800, self.mods, {"Kurila_Thing": 7})
        self.assertEqual(found[0].modio_id, 7)


class TestThePlan(_Folders):
    def wanted(self, *mods) -> list:
        return installplan.wanted_from_mods(modio.summarise(mod) for mod in mods)

    def plan(self, *mods):
        return installplan.build(self.wanted(*mods), installplan.inventory(ANNO_1800, self.mods))

    def test_what_is_not_here_is_ticked_and_what_is_here_is_not(self):
        install_mod(self.mods, "[Gameplay] Thing", "thing", "2.0", "Thing")
        plan = self.plan(downloadable_mod(1, "Thing", "2.0", b"a"), downloadable_mod(2, "Other", "1.0", b"bbbb"))
        self.assertEqual([row.status for row in plan.rows], [installplan.CURRENT, installplan.MISSING])
        self.assertEqual([row.fetch for row in plan.rows], [False, True])
        self.assertEqual(plan.download_size, 4)
        self.assertEqual(plan.unpacked_size, 12)
        self.assertIn("1 of these 2 mods is already installed", plan.describe())
        self.assertIn("Installing the other 1 will download", plan.describe())

    def test_a_mod_is_known_by_its_name_under_a_category_prefix(self):
        install_mod(self.mods, "[Gameplay] Thing (Kurila)")
        plan = self.plan(downloadable_mod(1, "Thing (Kurila)", "2.0", b"a"))
        self.assertEqual(plan.rows[0].status, installplan.INSTALLED)
        self.assertFalse(plan.rows[0].fetchable)

    def test_an_older_copy_is_offered_but_never_ticked_for_you(self):
        old = install_mod(self.mods, "[Gameplay] Thing", "thing", "1.0", "Thing")
        row = self.plan(downloadable_mod(1, "Thing", "2.0", b"a")).rows[0]
        self.assertEqual((row.status, row.fetch, row.fetchable, row.replace),
                         (installplan.OLDER, False, True, old))
        self.assertIn("mod.io has 2.0", row.note)

    def test_the_games_own_copy_is_not_replaced_from_here(self):
        self.cached(1, "[Gameplay] Thing", "thing", "1.0", modfile=5)
        row = self.plan(downloadable_mod(1, "Thing renamed since", "2.0", b"a")).rows[0]
        self.assertEqual(row.status, installplan.OLDER)
        self.assertFalse(row.fetchable)
        self.assertIn("own mod browser", row.why_not)

    def test_installed_twice_is_said_and_the_newer_copy_is_the_one_compared(self):
        self.cached(1, "[Gameplay] Thing", "thing", "1.0", modfile=5)
        install_mod(self.mods, "[Seal] Thing", "thing", "2.0")
        row = self.plan(downloadable_mod(1, "Thing", "2.0", b"a")).rows[0]
        self.assertEqual(row.status, installplan.CURRENT)
        self.assertIn("installed twice", row.note)
        self.assertEqual(len(row.copies), 2)

    def test_what_cannot_be_fetched_says_why(self):
        plan = self.plan(downloadable_mod(1, "Flagged", "1", b"a", flagged=True), downloadable_mod(2, "No file", "1"))
        self.assertEqual([row.status for row in plan.rows], [installplan.FLAGGED, installplan.GONE])
        self.assertEqual(plan.chosen(), [])
        self.assertIn("2 cannot be installed", plan.describe())

    def test_room_is_asked_for_before_anything_is_fetched(self):
        plan = self.plan(downloadable_mod(1, "Thing", "1", b"abcd"))
        self.assertEqual(plan.room_needed, 12 + 4)
        with mock.patch.object(disk, "free_space", return_value=10):
            self.assertIn("needs about", installplan.short_of_room(plan, self.mods))
        with mock.patch.object(disk, "free_space", return_value=10**9):
            self.assertEqual(installplan.short_of_room(plan, self.mods), "")
        # A folder that is not there yet is asked about where it would be.
        self.assertIsNotNone(disk.free_space(self.mods / "not" / "yet"))


class TestALoadoutsMods(_Folders):
    def loadout(self) -> Loadout:
        loadout = Loadout()
        loadout.layers = [
            ModLayer(origin="[Gameplay] Thing", name="Thing", mod_id="thing", version="1.0", modio_id=1),
            ModLayer(origin="[Gameplay] Thing/shared/Piece", name="Piece", mod_id="piece"),
            ModLayer(origin="3229975/[Building] Windmill", name="Windmill", mod_id="windmill"),
            ModLayer(origin="[Gameplay] Thing-patch", name="My patch", patches="thing"),
            ModLayer(origin="[Misc] Mine", name="Mine", mod_id="mine"),
            ModLayer(origin="[Misc] Off", name="Off", mod_id="off", active=False,
                     options={"speed": "2"}),
        ]
        loadout.home = "[Misc] Mine"
        return loadout

    def test_only_mods_somebody_else_could_get_are_wanted(self):
        wanted = installplan.wanted_from_loadout(self.loadout())
        self.assertEqual([one.name for one in wanted], ["Thing", "Windmill", "Off"])
        # The cache's numbered folder is the mod.io id, read off the origin.
        self.assertEqual([one.modio_id for one in wanted], [1, 3229975, 0])
        self.assertEqual(wanted[1].folder_name, "[Building] Windmill")

    def test_a_fork_and_a_kept_mod_of_yours_are_not_wanted(self):
        """Both are the loadout's own, as a patch is: they were listed as
        mods somebody else had to fetch, and looked up on mod.io."""
        loadout = self.loadout()
        loadout.layers += [
            ModLayer(origin="[Gameplay] Thing 2", name="Thing 2", mod_id="thing-2",
                     fork_of="[Gameplay] Thing"),
            ModLayer(origin="[Misc] Made here", name="Made here", mod_id="made", kept=True),
        ]
        wanted = installplan.wanted_from_loadout(loadout)
        self.assertEqual([one.name for one in wanted], ["Thing", "Windmill", "Off"])

    def test_missing_is_asked_of_the_disk_alone_and_by_modid_first(self):
        install_mod(self.mods, "[Seal] Renamed Thing", "thing", "1.0")
        wanted = installplan.wanted_from_loadout(self.loadout())
        absent = installplan.missing(wanted, installplan.inventory(ANNO_1800, self.mods))
        self.assertEqual([one.name for one in absent], ["Windmill", "Off"])

    def test_the_missing_ones_are_looked_up_by_id_and_then_by_name(self):
        server = FakeServer([
            downloadable_mod(3229975, "Windmill renamed", "2.0", b"w"),
            downloadable_mod(44, "[Misc] Off", "1.5", b"o"),
            downloadable_mod(45, "Official", "1", b"x"),
        ])
        loadout = self.loadout()
        gather = installplan.loadout_source(4169, loadout)
        install_mod(self.mods, "[Gameplay] Thing", "thing", "0.9")
        installed = installplan.inventory(ANNO_1800, self.mods)
        gathered = gather(fake_client(server), lambda *a: None, lambda: False, installed)
        self.assertEqual(gathered.learned, {"[Misc] Off": 44})
        plan = installplan.build(gathered.wanted, installed)
        self.assertEqual([row.status for row in plan.rows],
                         [installplan.INSTALLED, installplan.MISSING, installplan.MISSING])
        # Built against a version that is not the one here: said, not acted on.
        self.assertIn("built against 1.0", plan.rows[0].note)
        # Switched off in the loadout, so not needed to play it: unticked.
        self.assertEqual([row.fetch for row in plan.rows], [False, True, False])
        self.assertIn("switched off", plan.rows[2].note)
        # And the settings the loadout was read at are the game's to be told.
        self.assertTrue(any("mod menu" in note for note in gathered.notes))
        # Nothing was asked about the mod that is installed, and asking again
        # asks nothing at all.
        asked = len(server.calls)
        self.assertEqual(asked, 2)
        gather(fake_client(server), lambda *a: None, lambda: False, installed)
        self.assertEqual(len(server.calls), asked)

    def test_a_mod_mod_io_has_never_heard_of_is_said_to_be_that(self):
        loadout = Loadout()
        loadout.layers = [ModLayer(origin="[Misc] Private", name="Private", mod_id="private_mod")]
        gathered = installplan.loadout_source(4169, loadout)(
            fake_client(FakeServer()), lambda *a: None, lambda: False, []
        )
        row = installplan.build(gathered.wanted, []).rows[0]
        self.assertEqual(row.status, installplan.UNFOUND)
        self.assertIn("private_mod", row.note)
        self.assertFalse(row.fetchable)


class TestTheQueue(_Folders):
    def setUp(self):
        super().setUp()
        self.bodies = {}
        self.fetched = []

    def mod(self, mod_id: int, name: str, folder: str = "") -> dict:
        body = zip_bytes({"%s/modinfo.json" % (folder or name): '{"Version": "2.0"}'})
        self.bodies["https://cdn/%d" % mod_id] = body
        return downloadable_mod(mod_id, name, "2.0", body)

    def chunks(self, url: str):
        self.fetched.append(url)
        body = self.bodies[url]
        for start in range(0, len(body), 64):
            yield body[start:start + 64]

    def rows(self, *mods):
        wanted = installplan.wanted_from_mods(modio.summarise(mod) for mod in mods)
        return installplan.build(wanted, installplan.inventory(ANNO_1800, self.mods)).chosen()

    def test_each_is_fetched_and_installed_and_nothing_is_asked_of_mod_io(self):
        server = FakeServer()
        settled = []
        outcomes = installplan.fetch(
            fake_client(server), 4169, self.rows(self.mod(1, "One"), self.mod(2, "Two")), self.mods, library_dir=self.root / 'library',
            chunks=self.chunks, settled=lambda position, outcome: settled.append((position, outcome.status)),
        )
        self.assertEqual([outcome.status for outcome in outcomes], [installplan.DONE, installplan.DONE])
        self.assertEqual(settled, [(0, installplan.DONE), (1, installplan.DONE)])
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["One", "Two"])
        # The listing's own links were used; mod.io was not asked again.
        self.assertEqual(server.calls, [])
        self.assertIn("2 of 2 installed", installplan.describe_outcomes(outcomes))

    def test_a_file_with_no_checksum_is_installed_and_said_to_be_unchecked(self):
        """The owner's decision on what mod.io listing no md5 does (security
        review S7, N5): installed, with a warning wherever installs are said."""
        bare = self.mod(1, "Bare")
        del bare["modfile"]["filehash"]
        outcomes = installplan.fetch(
            fake_client(FakeServer()), 4169, self.rows(bare, self.mod(2, "Checked")), self.mods,
            library_dir=self.root / 'library', chunks=self.chunks,
        )
        self.assertEqual([outcome.status for outcome in outcomes], [installplan.DONE, installplan.DONE])
        self.assertEqual(outcomes[0].message, "mod.io gave no checksum for Bare's file, so the download "
                                              "couldn't be checked.")
        self.assertEqual(outcomes[1].message, "")
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["Bare", "Checked"])

    def test_one_bad_mod_does_not_lose_the_others(self):
        bad = self.mod(1, "Bad")
        bad["modfile"]["filehash"]["md5"] = "0" * 32
        server = FakeServer([bad])
        outcomes = installplan.fetch(
            fake_client(server), 4169, self.rows(bad, self.mod(2, "Good")), self.mods, library_dir=self.root / 'library', chunks=self.chunks,
        )
        self.assertEqual([outcome.status for outcome in outcomes], [installplan.FAILED, installplan.DONE])
        self.assertIn("checksum", outcomes[0].message)
        self.assertEqual([p.name for p in self.mods.iterdir()], ["Good"])
        # Asked about once, in case it was the link, and tried once more.
        self.assertEqual(self.fetched.count("https://cdn/1"), 2)
        self.assertIn("1 could not be installed", installplan.describe_outcomes(outcomes))

    def test_a_link_that_has_expired_is_asked_for_again(self):
        stale = self.mod(1, "One")
        fresh = json.loads(json.dumps(stale))
        fresh["modfile"]["download"]["binary_url"] = "https://cdn/fresh"
        self.bodies["https://cdn/fresh"] = self.bodies.pop("https://cdn/1")

        def chunks(url):
            if url == "https://cdn/1":
                raise OSError("403")
            return self.chunks(url)

        outcomes = installplan.fetch(
            fake_client(FakeServer([fresh])), 4169, self.rows(stale), self.mods, library_dir=self.root / 'library', chunks=chunks,
        )
        self.assertEqual(outcomes[0].status, installplan.DONE)
        self.assertEqual(self.fetched, ["https://cdn/fresh"])

    def test_a_body_cut_short_is_asked_for_again_and_never_stops_the_queue(self):
        cut = self.mod(1, "Cut")

        def chunks(url):
            if url == "https://cdn/1":
                yield b"PK"
                raise http.client.IncompleteRead(b"PK", 100)
            yield from self.chunks(url)

        server = FakeServer([cut])
        outcomes = installplan.fetch(
            fake_client(server), 4169, self.rows(cut, self.mod(2, "Good")), self.mods,
            library_dir=self.root / 'library', chunks=chunks,
        )
        self.assertEqual([outcome.status for outcome in outcomes], [installplan.FAILED, installplan.DONE])
        self.assertEqual(len(server.calls), 1, "asked about once, in case it was the link")
        self.assertEqual([p.name for p in self.mods.iterdir()], ["Good"])

    def test_one_mod_asked_about_first_is_still_tried_again_once(self):
        """What the get and updates dialogs do: ask mod.io for the current
        file, then fetch it -- with the queue's one retry, which they lacked."""
        listed = self.mod(1, "One")
        current = json.loads(json.dumps(listed))
        attempts = []

        def chunks(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise OSError("403")
            return self.chunks(url)

        server = FakeServer([current])
        summary, _installed = installplan.fetch_and_install(
            fake_client(server), 4169, modio.summarise(listed), self.root / "scratch",
            self.root / "library", self.mods, ask_first=True, chunks=chunks,
        )
        self.assertEqual(summary.id, 1)
        self.assertEqual([p.name for p in self.mods.iterdir()], ["One"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(server.calls), 2, "asked first, and again after the failed download")

    def test_a_file_flagged_since_the_plan_was_made_is_skipped(self):
        stale = self.mod(1, "One")
        flagged = json.loads(json.dumps(stale))
        flagged["modfile"]["virus_positive"] = 1
        stale["modfile"]["filehash"]["md5"] = "0" * 32
        outcomes = installplan.fetch(
            fake_client(FakeServer([flagged])), 4169, self.rows(stale), self.mods, library_dir=self.root / 'library', chunks=self.chunks,
        )
        self.assertEqual((outcomes[0].status, list(self.mods.iterdir())), (installplan.SKIPPED, []))

    def test_stopping_keeps_what_landed_and_leaves_the_rest_for_next_time(self):
        rows = self.rows(self.mod(1, "One"), self.mod(2, "Two"), self.mod(3, "Three"))
        done = []
        outcomes = installplan.fetch(
            fake_client(FakeServer()), 4169, rows, self.mods, library_dir=self.root / 'library', chunks=self.chunks,
            stop=lambda: bool(done), settled=lambda position, outcome: done.append(position),
        )
        self.assertEqual([outcome.status for outcome in outcomes],
                         [installplan.DONE, installplan.NOT_ATTEMPTED, installplan.NOT_ATTEMPTED])
        self.assertEqual([p.name for p in self.mods.iterdir()], ["One"])
        self.assertIn("Stopped with 2 still on mod.io", installplan.describe_outcomes(outcomes))
        # The next plan is made from disk, so it offers exactly the other two.
        again = self.rows(self.mod(1, "One"), self.mod(2, "Two"), self.mod(3, "Three"))
        self.assertEqual([row.name for row in again], ["Two", "Three"])

    def test_an_older_copy_that_was_ticked_is_set_aside_and_keeps_its_folder_name(self):
        old = install_mod(self.mods, "[Seal] One", "one", "1.0", "One")
        wanted = installplan.wanted_from_mods([modio.summarise(self.mod(1, "One", folder="[Gameplay] One"))])
        plan = installplan.build(wanted, installplan.inventory(ANNO_1800, self.mods))
        plan.rows[0].fetch = True
        outcomes = installplan.fetch(fake_client(FakeServer()), 4169, plan.chosen(), self.mods, library_dir=self.root / 'library', chunks=self.chunks)
        # The link keeps the old folder's name; the folder behind it is the library's.
        self.assertEqual([l.link for l in outcomes[0].installed.linked], [old])
        self.assertEqual(outcomes[0].installed.placed, [self.root / "library" / "[Gameplay] One"])
        self.assertEqual((old / "modinfo.json").read_text(encoding="utf-8"), '{"Version": "2.0"}')
        self.assertTrue(outcomes[0].installed.set_aside.is_dir())


if __name__ == "__main__":
    unittest.main()
