"""The round-trip harness: that it notices a round trip that loses something.

A harness that only ever says "identical" is indistinguishable from one that
compares nothing, so the tests that matter here break the export on purpose
and check that the difference is caught -- in the change sets and in what the
game would be handed.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import modio_loader  # noqa: E402
import modio_roundtrip  # noqa: E402
from support import (  # noqa: E402
    IMPORT_ASSETS,
    LOADER,
    modops_file,
    requires,
)

from anno_modding_studio.core import (  # noqa: E402
    games,
    generator,
    loadercheck,
    modops,
    modzip,
    reader,
)
from anno_modding_studio.core import index as index_mod  # noqa: E402

PATCH = "data/base/config/export/assets.xml"

RADIUS = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
PRODUCT = (
    '<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
    "<Item><Product>2004</Product></Item></ModOp>"
)
EVERY_FACTORY = (
    "<ModOp Merge=\"//Values[Building/BuildingType='Factory']/Building\">"
    "<Building><Radius>20</Radius></Building></ModOp>"
)
MISSING = '<ModOp Type="replace" GUID="9999" Path="/Values/Building/Radius"><Radius>1</Radius></ModOp>'
# Arithmetic the Studio does not do and the loader does: not read, and not idle.
FLOOR = (
    '<ModOp Type="merge" GUID="1001" Path="/Values/Building">'
    '<Radius><ModValue Insert="floor(self::node() div 3)"/></Radius></ModOp>'
)
MODINFO = """{
  // what the community's extension writes, comments and all
  "ModID": "a-mod",
  "Version": "2.1",
  "ModName": {"English": "A Mod", "German": "Ein Mod"},
  "Category": {"English": "Gameplay"},
  "CreatorName": "somebody",
  "Description": {"English": "What it does.", "French": "Ce que cela fait."},
  "LoadAfterIds": ["another-mod"],
  "SomethingNobodyHasHeardOf": {"keep": ["me"]}
}"""


class RoundTripCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(IMPORT_ASSETS, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def mod(self, where: Path, body: str, modinfo: str = "") -> Path:
        patch = where / PATCH
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(modops_file(body), encoding="utf-8")
        if modinfo:
            (where / "modinfo.json").write_text(modinfo, encoding="utf-8")
        return where

    def zip_holding(self, **mods) -> Path:
        files = self.root / "files"
        for name, body in mods.items():
            self.mod(files / name.replace("__", " "), body)
        return files

    def run_unit(self, files: Path, keep: bool = False, how=None):
        return modio_roundtrip.run_unit(
            {"id": "unit", "name": "a unit", "files": str(files)},
            self.index,
            games.ANNO_117,
            self.root / "work",
            keep=keep,
            how=how,
        )


class TestARoundTripThatHolds(RoundTripCase):
    def test_a_mod_that_comes_back_the_same(self):
        result = self.run_unit(self.zip_holding(First__Mod=RADIUS + PRODUCT, Second=EVERY_FACTORY))

        self.assertEqual(result["status"], "identical", result)
        self.assertEqual(result["mods"], 2)
        self.assertEqual(result["blocked"], 0)
        self.assertGreater(result["changes"], 0)
        for mode in modio_roundtrip.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(result["modes"][mode]["exported"], 2)
                self.assertEqual(result["modes"][mode]["missing"], 0)
                self.assertEqual(result["modes"][mode]["effect_assets"], 0)
                self.assertTrue(result["modes"][mode]["order_kept"])
        self.assertFalse((self.root / "work" / "unit").exists())

    def test_a_zip_holding_the_mod_itself_rather_than_its_folder(self):
        files = self.mod(self.root / "files", RADIUS)
        folders, roots = modio_roundtrip.folders_of(files)
        self.assertEqual((folders, roots), ([files], [self.root]))

        result = self.run_unit(files)
        self.assertEqual(result["status"], "identical", result)

    def test_a_folder_that_is_a_box_of_mods_is_read_as_those_mods(self):
        """*More Trade Contracts* ships two mods inside one folder with no data of
        its own; the loader was handed that folder and ran nothing."""
        files = self.root / "files"
        box = files / "More trade slots-712-1"
        self.mod(box / "More Trade Slots", RADIUS)
        self.mod(box / "More_Ship_Slots", PRODUCT)
        folders, roots = modio_roundtrip.folders_of(files)
        self.assertEqual(folders, [box / "More Trade Slots", box / "More_Ship_Slots"])
        self.assertEqual(roots, [files, box])
        self.assertEqual(self.run_unit(files)["mods"], 2)

    def test_a_mod_taken_out_of_its_box_is_named_and_switched_off_by_its_own_folder(self):
        """Out of the box, ``-Inner Off`` sits in the mods folder as itself: its
        own off mark switches it off, and the box is no part of its name."""
        files = self.root / "files"
        self.mod(files / "Box" / "-Inner Off", RADIUS)
        self.mod(files / "Box" / "Second", PRODUCT)
        folders, roots = modio_roundtrip.folders_of(files)
        self.assertEqual(
            {reader._origin_for(folder, roots) for folder in folders}, {"Inner Off", "Second"})
        self.assertTrue(reader._marked_off(files / "Box" / "-Inner Off", roots))
        self.assertFalse(reader._marked_off(files / "Box" / "Second", roots))

    def test_two_mods_of_one_name_in_different_boxes_stay_two_mods(self):
        """A zip offering variants (``Option A/Mod``, ``Option B/Mod``) holds
        two mods of one folder name. Named by their folder alone they were one
        mod twice over, and the round trip said it failed."""
        files = self.root / "files"
        self.mod(files / "BoxA" / "Shared", RADIUS)
        self.mod(files / "BoxB" / "Shared", PRODUCT)
        self.mod(files / "BoxC" / "Alone", EVERY_FACTORY)
        folders, roots = modio_roundtrip.folders_of(files)
        self.assertEqual(
            sorted(reader._origin_for(folder, roots) for folder in folders),
            ["Alone", "BoxA/Shared", "BoxB/Shared"])
        result = self.run_unit(files)
        self.assertEqual(result["status"], "identical", result)
        self.assertEqual(result["mods"], 3)

    def test_a_boxed_mod_named_as_a_top_level_one_stays_apart_from_it(self):
        """``Box/-Same`` beside ``Same``: one name once the off mark is off."""
        files = self.root / "files"
        self.mod(files / "Box" / "-Same", RADIUS)
        self.mod(files / "Same", PRODUCT)
        folders, roots = modio_roundtrip.folders_of(files)
        self.assertEqual(
            sorted(reader._origin_for(folder, roots) for folder in folders),
            ["Box/-Same", "Same"])
        self.assertEqual(self.run_unit(files)["status"], "identical")

    def test_which_folders_are_mods_is_what_the_apps_zip_install_installs(self):
        """A box holding no mod is no mod, and a modinfo two levels inside a
        box is reached, as the game reaches it (A11-144): the zip install puts
        that one in place, and not the other."""
        files = self.root / "files"
        (files / "Notes" / "pictures").mkdir(parents=True)
        self.mod(files / "Box" / "Wrap" / "Deep", RADIUS, MODINFO)
        self.mod(files / "Mod", PRODUCT)
        folders, _roots = modio_roundtrip.folders_of(files)
        self.assertEqual(folders, modzip.mods_in(files))
        self.assertEqual(folders, [files / "Box" / "Wrap" / "Deep", files / "Mod"])

    def test_a_mod_nested_four_folders_down_is_read(self):
        """Census unit 3986772 holds its mod inside four folders of its name;
        read as no mod, it came out no-patch (A11-144)."""
        files = self.root / "files"
        self.mod(files.joinpath(*["[Cheat] Ultra Income"] * 4), RADIUS, MODINFO)
        result = self.run_unit(files)
        self.assertEqual(result["status"], "identical", result)
        self.assertEqual(result["mods"], 1)

    def test_nothing_to_read(self):
        (self.root / "files/Textures Only/data/graphics").mkdir(parents=True)
        result = self.run_unit(self.root / "files")
        self.assertEqual(result["status"], "no-patch")


class TestARoundTripThatLosesSomething(RoundTripCase):
    def test_an_export_that_drops_an_op_is_caught(self):
        real = generator.build_modops_xml

        def forgetful(loadout, *args, **kwargs):
            root = etree.fromstring(real(loadout, *args, **kwargs).encode("utf-8"))
            ops = root.findall(".//ModOp")
            ops[-1].getparent().remove(ops[-1])
            return etree.tostring(root, encoding="unicode")

        with mock.patch.object(generator, "build_modops_xml", forgetful):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + PRODUCT))

        self.assertEqual(result["status"], "differs")
        for mode in modio_roundtrip.MODES:
            with self.subTest(mode=mode):
                found = result["modes"][mode]
                self.assertEqual(found["missing"], 1)
                self.assertEqual(found["effect_assets"], 1)
                self.assertTrue(found["effect_examples"][0]["diff"])
        self.assertTrue((self.root / "work" / "unit").exists(), "a unit that differs keeps its export")

    def test_an_export_that_changes_a_value_the_game_sees(self):
        real = generator.build_modops_xml

        def wrong(loadout, *args, **kwargs):
            return real(loadout, *args, **kwargs).replace("<Radius>12</Radius>", "<Radius>13</Radius>")

        with mock.patch.object(generator, "build_modops_xml", wrong):
            result = self.run_unit(self.zip_holding(Mod=RADIUS))

        self.assertEqual(result["status"], "differs")
        diff = "\n".join(result["modes"]["rules"]["effect_examples"][0]["diff"])
        self.assertIn("-", diff)
        self.assertIn("13", diff)

    def test_an_op_that_could_not_be_read_is_counted_and_carried(self):
        result = self.run_unit(self.zip_holding(Mod=RADIUS + MISSING), keep=True)

        self.assertEqual(result["status"], "identical", result)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(sum(result["blocked_reasons"].values()), 1)
        # It goes out as written and comes back unread again, beside the op
        # that was read, which is kept as written as well.
        self.assertEqual(result["carried"]["kept"], 2)
        self.assertEqual(result["carried"]["missing"], 0)
        self.assertEqual(result["modes"]["rules"]["reread_blocked"], 1)
        self.assertIn('GUID="9999"', (self.root / "work/unit/rules/Mod" / PATCH).read_text(encoding="utf-8"))

    def test_an_export_that_drops_a_carried_op_is_caught(self):
        """The effect comparison cannot see this one go: the Studio never applies
        an op it could not read, so both readings show the same assets."""
        real = generator.build_modops_xml

        def forgetful(loadout, *args, **kwargs):
            loadout.unread = []
            return real(loadout, *args, **kwargs)

        with mock.patch.object(generator, "build_modops_xml", forgetful):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + MISSING))

        self.assertEqual(result["modes"]["rules"]["effect_assets"], 0)
        self.assertEqual(result["carried"]["missing"], 1)
        self.assertIn("GUID=\"9999\"", result["carried"]["missing_examples"][0])
        self.assertEqual(result["status"], "differs")

    def test_a_carried_op_is_looked_for_in_the_file_not_in_the_second_reading(self):
        """*Noblesse Oblige* has ops kept as written because their narrow form did
        not add up; beside the ops the export wrote round them it does, so they
        read back as changes.  They are in the file, and that is the promise."""
        real = modio_roundtrip.read
        calls = []

        def forgetful_second_time(*args, **kwargs):
            session, loadout = real(*args, **kwargs)
            calls.append(loadout)
            if len(calls) > 1:
                loadout.unread = []
            return session, loadout

        with mock.patch.object(modio_roundtrip, "read", forgetful_second_time):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + MISSING))

        self.assertEqual(result["carried"]["missing"], 0)
        self.assertEqual(result["carried"]["reread_differently"], 1)
        self.assertEqual(result["status"], "same-effect")

    def test_a_mod_with_nothing_but_unread_ops_is_still_written(self):
        # It used not to be: the Export tab wrote a mod that had changes, this
        # one has none, and nineteen Anno 117 mods on mod.io are like it.
        result = self.run_unit(self.zip_holding(Mod=MISSING))

        self.assertEqual(result["left_out"], [])
        self.assertEqual(result["carried"], {**result["carried"], "kept": 1, "missing": 0})
        self.assertEqual(result["status"], "identical")

    def test_a_mod_the_export_leaves_out_is_named(self):
        with mock.patch.object(generator, "writes_anything", lambda loadout: bool(loadout.active_count)):
            result = self.run_unit(self.zip_holding(Mod=MISSING))

        self.assertEqual(result["left_out"], ["Mod"])
        self.assertEqual(result["carried"]["missing"], 1)
        self.assertEqual(result["status"], "differs")


class TestWhatAUnitLeavesOnDisk(RoundTripCase):
    """A unit that does not come back identical keeps its exports, and a census
    has a hundred of those or more each run.  Carrying a mod's textures into
    each of three exports made a run 5.4 GB; the comparisons read none of it."""

    def shipping_a_texture(self) -> Path:
        files = self.zip_holding(Mod=RADIUS)
        (files / "Mod/data/graphics").mkdir(parents=True)
        (files / "Mod/data/graphics/thing.dds").write_bytes(b"\0" * 4096)
        (files / "Mod/data/config/gui").mkdir(parents=True)
        (files / "Mod/data/config/gui/texts_english.xml").write_text("<TextExport/>", encoding="utf-8")
        return files

    def kept(self) -> list:
        return sorted(
            path.relative_to(self.root / "work/unit/rules/Mod").as_posix()
            for path in (self.root / "work/unit/rules/Mod").rglob("*") if path.is_file()
        )

    def test_an_export_keeps_what_is_compared_and_not_the_mod_s_textures(self):
        result = self.run_unit(self.shipping_a_texture(), keep=True)

        self.assertEqual(result["status"], "identical", result)
        self.assertIn("data/config/gui/texts_english.xml", self.kept())
        self.assertNotIn("data/graphics/thing.dds", self.kept())

    def test_or_all_of_it_on_asking(self):
        modio_roundtrip.run_unit(
            {"id": "unit", "name": "a unit", "files": str(self.shipping_a_texture())},
            self.index, games.ANNO_117, self.root / "work", keep=True, carry_all=True,
        )
        self.assertIn("data/graphics/thing.dds", self.kept())


class TestOldRunsAreCleanedUp(unittest.TestCase):
    """Half a gigabyte a run still adds up, so a run starts by deleting the
    runs beside it that nothing has been written to for a week."""

    DAY = 86400.0

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.now = 1_800_000_000.0

    def a_run(self, name: str, days_old: float, game: str = "anno-1800") -> Path:
        results = self.root / name / game / "results"
        results.mkdir(parents=True)
        (self.root / name / game / "work" / "123").mkdir(parents=True)
        result = results / "123.json"
        result.write_text("{}", encoding="utf-8")
        when = self.now - days_old * self.DAY
        os.utime(result, (when, when))
        return self.root / name

    def pruned(self, days: float = 7) -> list:
        gone = modio_roundtrip.prune_runs(self.root / "rt-now", days, now=self.now)
        return sorted(path.name for path in gone)

    def test_runs_older_than_the_limit_go_and_younger_ones_stay(self):
        self.a_run("rt-now", 0)
        self.a_run("rt-fresh", 2)
        self.a_run("rt-old", 10)
        self.a_run("rt-older", 30, game="anno-117-pax-romana")

        self.assertEqual(self.pruned(), ["rt-old", "rt-older"])
        self.assertTrue((self.root / "rt-fresh").is_dir())
        self.assertTrue((self.root / "rt-now").is_dir())

    def test_the_newest_other_run_stays_however_old_as_the_baseline(self):
        self.a_run("rt-now", 0)
        self.a_run("rt-baseline", 20)
        self.a_run("rt-ancient", 40)

        self.assertEqual(self.pruned(), ["rt-ancient"])

    def test_a_run_marked_keep_stays(self):
        self.a_run("rt-now", 0)
        self.a_run("rt-baseline", 20)
        kept = self.a_run("rt-for-the-review", 40)
        (kept / "KEEP.txt").write_text("", encoding="utf-8")

        self.assertEqual(self.pruned(), [])

    def test_nothing_that_is_not_a_run_is_touched(self):
        """The census, the loader's cache and a frozen source sit beside the
        runs, and are old."""
        self.a_run("rt-now", 0)
        self.a_run("rt-baseline", 20)
        for other in ("census/anno-1800/3230012/files", "loader-cache/runs/00", "src-abc/tools"):
            (self.root / other).mkdir(parents=True)
            old = self.now - 90 * self.DAY
            os.utime(self.root / other, (old, old))

        self.assertEqual(self.pruned(), [])
        for other in ("census", "loader-cache", "src-abc"):
            self.assertTrue((self.root / other).is_dir(), other)


class TestTheModinfo(RoundTripCase):
    def test_an_author_s_modinfo_comes_back_key_for_key(self):
        files = self.root / "files"
        self.mod(files / "Mod", RADIUS)
        (files / "Mod" / "modinfo.jsonc").write_text(MODINFO, encoding="utf-8")

        result = self.run_unit(files)

        self.assertEqual(result["modinfo"]["keys"], {}, result["modinfo"])
        self.assertEqual(
            result["modinfo"]["mods"], {"kept": 1, "modinfo.jsonc written back as modinfo.json": 1}
        )

    def test_a_mod_that_shipped_none_is_given_one(self):
        result = self.run_unit(self.zip_holding(Mod=RADIUS))
        self.assertEqual(result["modinfo"]["mods"], {"created": 1})

    def test_a_key_the_export_loses_or_rewrites_is_named(self):
        original = self.mod(self.root / "one", RADIUS, MODINFO.replace("// what", "// "))
        exported = self.mod(
            self.root / "two", RADIUS, '{"ModID": "a-mod", "Version": "3", "ModName": {"English": "A Mod"}}'
        )
        found = modio_roundtrip.compare_modinfo(original, exported)

        self.assertEqual(found["state"], "changed")
        self.assertEqual(found["changed"], ["Version"])
        self.assertIn("ModName.German", found["lost"])
        self.assertIn("LoadAfterIds", found["lost"])


class TestTheCheckboxes(RoundTripCase):
    def test_a_unit_run_off_the_defaults_says_so(self):
        how = modio_roundtrip.How(keep_inert=True)
        result = self.run_unit(self.zip_holding(Mod=RADIUS + PRODUCT), keep=True, how=how)

        self.assertEqual(result["how"], {"keep_inert": True})

    def test_and_one_run_at_them_does_not(self):
        self.assertNotIn("how", self.run_unit(self.zip_holding(Mod=RADIUS)))


XMLTEST_117 = modio_loader.find_xmltest(games.ANNO_117)


@requires(XMLTEST_117, LOADER)
class TestTheOrderUnitsRunIn(unittest.TestCase):
    """Longest first, so the big bundles do not come up last and run alone."""

    def test_by_last_time_and_a_unit_never_timed_first_biggest_first(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        results = root / "results"
        results.mkdir()
        units = []
        for name, size in (("quick", 10), ("slow", 10), ("new-small", 10), ("new-big", 5000), ("old", 10)):
            files = root / name
            files.mkdir()
            (files / "assets.xml").write_text("x" * size, encoding="utf-8")
            units.append({"id": name, "files": str(files)})
        # A result already in the folder counts when the timings file has none.
        (results / "old.json").write_text('{"seconds": 50}', encoding="utf-8")
        order = modio_roundtrip._longest_first(units, results, {"quick": 5, "slow": 900})
        self.assertEqual([unit["id"] for unit in order], ["new-big", "new-small", "slow", "old", "quick"])


class TestAUnitsTimeIsItsOwn(unittest.TestCase):
    """The wait for a loader slot is other units' loader runs: counted in, a
    unit that had only waited was written down as a timeout."""

    def test_a_process_waiting_for_a_slot_says_so_and_stops_saying_so(self):
        import multiprocessing
        import threading
        import time

        slots = modio_roundtrip.Slots(multiprocessing.get_context("spawn"), 1, 2)
        me = os.getpid()
        self.assertFalse(slots.waiting(me))
        entered = threading.Event()

        def second():
            with slots:
                entered.set()

        with slots:
            self.assertFalse(slots.waiting(me))  # holding one is not waiting
            other = threading.Thread(target=second, daemon=True)
            other.start()
            until = time.monotonic() + 5
            while not slots.waiting(me) and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(slots.waiting(me))
        self.assertTrue(entered.wait(5))
        other.join(5)
        self.assertFalse(slots.waiting(me))

    def test_what_a_killed_process_was_waiting_for_is_forgotten(self):
        import multiprocessing

        slots = modio_roundtrip.Slots(multiprocessing.get_context("spawn"), 1, 2)
        slots._mark(4242, True)
        self.assertTrue(slots.waiting(4242))
        self.assertEqual(slots.free(4242), 0)
        self.assertFalse(slots.waiting(4242))

    def test_the_limit_is_on_the_time_not_spent_waiting(self):
        self.assertTrue(modio_roundtrip._within_time(4000, 3000, 3600))
        self.assertFalse(modio_roundtrip._within_time(4000, 300, 3600))
        self.assertTrue(modio_roundtrip._within_time(3600, 0, 3600))


class TestUnitsStartAsMemoryAllows(unittest.TestCase):
    """The longest units are the ones that take gigabytes, and longest first
    started every one of them together."""

    def test_a_unit_that_does_not_fit_beside_what_is_running_waits_and_a_smaller_one_goes_ahead(self):
        todo = [{"id": name} for name in ("huge", "big", "small", "new")]
        needs = {"huge": 4000, "big": 2000, "small": 40}
        fits = modio_roundtrip._next_that_fits
        # With no limit, or nothing running, the order is the order.
        self.assertEqual(fits(todo, needs, 9000, None), 0)
        self.assertEqual(fits(todo, needs, 0, 100), 0)
        self.assertEqual(fits(todo, needs, 1000, 5000), 0)
        self.assertEqual(fits(todo, needs, 2000, 5000), 1)
        self.assertEqual(fits(todo, needs, 4000, 5000), 2)
        # One never run before is taken for an ordinary one.
        self.assertEqual(fits(todo[3:], needs, 4000, 4000 + modio_roundtrip.UNKNOWN_MB), 0)
        self.assertIsNone(fits(todo[3:], needs, 4000, 4100))
        self.assertIsNone(fits(todo, needs, 4990, 5000))
        self.assertIsNone(fits([], needs, 0, 5000))

    def test_a_unit_bigger_than_the_whole_limit_is_waited_for_and_runs_alone(self):
        todo = [{"id": "huge"}, {"id": "small"}]
        needs = {"huge": 4000, "small": 40}
        fits = modio_roundtrip._next_that_fits
        # Not passed over: it would come last, after every small one.
        self.assertIsNone(fits(todo, needs, 40, 3000))
        self.assertEqual(fits(todo, needs, 0, 3000), 0)
        self.assertIsNone(fits(todo[1:], needs, 4000, 3000))


class TestOneRunAtATime(unittest.TestCase):
    """Two runs at once ask for the memory one was sized for twice over, and
    write the one timings file: the second is turned away, or waits."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = self.root / "locks" / "census.lock"
        (self.root / "census" / "anno-117-pax-romana").mkdir(parents=True)
        self.argv = ["--census", str(self.root / "census"), "--out", str(self.root / "rt"),
                     "--lock", str(self.path)]

    def held(self, who=None):
        lock = modio_roundtrip.RunLock(self.path)
        self.assertIsNone(lock.take(who or {"pid": 1, "out": "rt-first", "started": "then"}))
        self.addCleanup(lock.release)
        return lock

    def test_a_second_run_is_told_who_has_it_and_gets_it_when_that_one_ends(self):
        first = self.held()
        second = modio_roundtrip.RunLock(self.path)
        self.assertEqual(second.take({"out": "rt-second"})["out"], "rt-first")
        first.release()
        self.assertIsNone(second.take({"out": "rt-second"}))
        self.addCleanup(second.release)
        self.assertEqual(modio_roundtrip.RunLock(self.path).take({})["out"], "rt-second")

    def test_a_run_that_is_killed_leaves_nothing_to_clear_up(self):
        import subprocess

        code = (
            "import sys, time; sys.path.insert(0, sys.argv[1]); import modio_roundtrip as m; "
            "from pathlib import Path; "
            "print(m.RunLock(Path(sys.argv[2])).take({'out': 'rt-killed'}), flush=True); time.sleep(60)"
        )
        other = subprocess.Popen(
            [sys.executable, "-c", code, str(Path(modio_roundtrip.__file__).parent), str(self.path)],
            stdout=subprocess.PIPE,
        )
        self.addCleanup(other.stdout.close)
        try:
            self.assertEqual(other.stdout.readline().strip(), b"None")
            self.assertEqual(modio_roundtrip.RunLock(self.path).take({})["out"], "rt-killed")
        finally:
            other.kill()
            other.wait()
        mine = modio_roundtrip.RunLock(self.path)
        self.assertIsNone(mine.take({}))
        mine.release()

    def test_a_run_started_while_another_is_going_refuses(self):
        self.held()
        with mock.patch.object(modio_roundtrip, "_run_plan") as ran,                 mock.patch("sys.stderr", new_callable=io.StringIO) as said:
            self.assertEqual(modio_roundtrip.main(self.argv), 3)
        ran.assert_not_called()
        self.assertIn("rt-first", said.getvalue())

    def test_told_to_wait_it_starts_when_the_other_has_ended(self):
        first = self.held()
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            first.release()

        with mock.patch.object(modio_roundtrip, "_run_plan", return_value=0) as ran,                 mock.patch.object(modio_roundtrip.time, "sleep", sleep),                 mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(modio_roundtrip.main([*self.argv, "--wait"]), 0)
        self.assertEqual(waits, [modio_roundtrip.WAIT])
        ran.assert_called_once()

    def test_the_lock_is_held_for_the_whole_run_and_let_go_after_it(self):
        seen = []

        def run_plan(*_args):
            seen.append(modio_roundtrip.RunLock(self.path).take({}))
            return 0

        with mock.patch.object(modio_roundtrip, "_run_plan", run_plan):
            self.assertEqual(modio_roundtrip.main(self.argv), 0)
        self.assertEqual(seen[0]["pid"], os.getpid())
        after = modio_roundtrip.RunLock(self.path)
        self.assertIsNone(after.take({}))
        after.release()

    def test_a_timings_file_that_cannot_be_written_does_not_end_the_run(self):
        out = self.root / "rt" / "anno117"
        written = modio_roundtrip._write_json

        def write(path, data):
            if Path(path).name == "timings.json":
                raise PermissionError(32, "being used by another process")
            written(path, data)

        class Done:
            pid, exitcode, sentinel = 4242, 0, 0

            def __init__(self, target, args, daemon):
                self.result = Path(args[5])

            def start(self):
                written(self.result, {"unit": "a", "status": "no-patch", "seconds": 1.5})

            def is_alive(self):
                return False

            def join(self):
                pass

        context = mock.Mock(Process=Done)
        with mock.patch.object(modio_roundtrip, "_write_json", write),                 mock.patch.object(modio_roundtrip.multiprocessing, "get_context", return_value=context),                 mock.patch("sys.stdout", new_callable=io.StringIO) as said:
            modio_roundtrip.run([{"id": "a", "name": "A"}], games.ANNO_117, self.root / "assets.xml", out,
                                1, 60, False, False, timings_file=self.root / "timings.json")
        self.assertIn("[1/1] no-patch", said.getvalue())


class TestWhatTheCommandLineAsksOfTheRun(unittest.TestCase):
    """main, down to the run of the units: what a census's folders and the
    options make of it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.lock = self.root / "locks" / "census.lock"
        census = self.root / "census" / "anno-117-pax-romana"
        for mod in ("101", "102", "103"):
            (census / mod / "files").mkdir(parents=True)
        (census / "101" / "manifest.json").write_text('{"name": "Roads"}', encoding="utf-8")
        # Fetched, and nothing in it yet: not a unit.
        (census / "104").mkdir()
        self.assets = self.root / "assets.xml"
        self.assets.write_text("<AssetList/>", encoding="utf-8")
        self.argv = ["--census", str(self.root / "census"), "--out", str(self.root / "rt"),
                     "--lock", str(self.lock), "--assets", str(self.assets), "--prune-days", "0",
                     "--memory", "0", "--jobs", "3"]

    def main(self, *more, run=None):
        def folders(_units, _game, _source, out, *_rest):
            # The run makes the folder its results go in.
            (out / modio_roundtrip.RESULTS).mkdir(parents=True)

        ran = mock.Mock(side_effect=run or folders)
        with mock.patch.object(modio_roundtrip, "run", ran), \
                mock.patch.object(modio_roundtrip, "open_index") as opened, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as said, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as failed:
            code = modio_roundtrip.main([*self.argv, *more])
        self.opened = opened
        return code, ran, said.getvalue() + failed.getvalue()

    def test_a_census_is_run_unit_by_unit_with_its_timings_beside_it(self):
        code, ran, _said = self.main()
        self.assertEqual(code, 0)
        units, game, source, out, jobs, timeout, redo, keep, *rest = ran.call_args.args
        self.assertEqual(units, [
            {"id": "101", "name": "Roads", "files": str(self.root / "census/anno-117-pax-romana/101/files")},
            {"id": "102", "name": "102", "files": str(self.root / "census/anno-117-pax-romana/102/files")},
            {"id": "103", "name": "103", "files": str(self.root / "census/anno-117-pax-romana/103/files")},
        ])
        self.assertEqual((game, source, out), (games.ANNO_117, self.assets, self.root / "rt/anno-117-pax-romana"))
        self.assertEqual((jobs, timeout, redo, keep), (3, modio_roundtrip.TIMEOUT, False, False))
        loader_exe, _loader_jobs, _how, cache, _action, timings, _carry, budget = rest
        self.assertEqual((loader_exe, cache, budget), (None, None, None))
        self.assertEqual(timings, (self.root / "timings" / "anno-117-pax-romana.json").resolve())
        # The index is built once here, not by every unit at once.
        self.opened.assert_called_once_with(self.assets)
        self.assertTrue((self.root / "rt/anno-117-pax-romana/summary.txt").is_file())

    def test_the_options_that_choose_and_redo_units_reach_it(self):
        _code, ran, _said = self.main("--mod", "103", "--mod", "101", "--limit", "1", "--redo",
                                      "--timeout", "7", "--timings", str(self.root / "t.json"))
        units, _game, _source, _out, _jobs, timeout, redo, *rest = ran.call_args.args
        self.assertEqual([unit["id"] for unit in units], ["101"])
        self.assertEqual((timeout, redo, rest[6]), (7, True, self.root / "t.json"))

    def test_a_unit_asked_for_by_name_is_the_one_run(self):
        """A unit that is not the first: with a limit of one, the first unit
        is what comes back whether or not ``--mod`` chose anything."""
        _code, ran, _said = self.main("--mod", "103")
        self.assertEqual([unit["id"] for unit in ran.call_args.args[0]], ["103"])

    def test_a_run_stopped_by_hand_ends_as_one_and_lets_the_lock_go(self):
        code, _ran, _said = self.main(run=KeyboardInterrupt)
        self.assertEqual(code, 130)
        self.assertFalse((self.root / "rt/anno-117-pax-romana/summary.txt").exists())
        after = modio_roundtrip.RunLock(self.lock)
        self.assertIsNone(after.take({}))
        after.release()

    def test_with_no_base_assets_it_says_so_and_runs_nothing(self):
        self.assets.unlink()
        code, ran, said = self.main()
        self.assertEqual(code, 1)
        ran.assert_not_called()
        self.assertIn("no base assets.xml", said)

    def test_it_will_not_write_its_results_inside_the_repository(self):
        inside = Path(modio_roundtrip.REPO) / "docs" / "rt"
        code, ran, said = self.main("--out", str(inside))
        self.assertEqual(code, 2)
        ran.assert_not_called()
        self.assertIn("inside the repository", said)
        self.assertFalse(inside.exists())
        self.assertFalse(self.lock.exists())

    def test_a_census_folder_with_no_game_in_it_is_refused_as_such(self):
        """A mistyped --census found no game folder, and the run crashed, or
        with nothing to prune did nothing and ended as if it had worked."""
        empty = self.root / "not-a-census"
        empty.mkdir()
        for prune in ("0", "30"):
            with self.subTest(prune_days=prune):
                said = self.refused("--census", str(empty), "--prune-days", prune)
                self.assertIn("no game's folder", said)

    def test_a_game_the_census_has_no_folder_for_is_refused_as_such(self):
        self.assertIn("no anno-1800 folder", self.refused("--game", "anno-1800"))

    def refused(self, *more) -> str:
        """What the parser said, turning the command line away before the run."""
        with mock.patch.object(modio_roundtrip, "_run_plan") as ran, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as said, \
                self.assertRaises(SystemExit) as ended:
            modio_roundtrip.main([*self.argv, *more])
        self.assertEqual(ended.exception.code, 2)
        ran.assert_not_called()
        self.assertFalse(self.lock.exists())
        return said.getvalue()


class _Unit:
    """A unit's process, standing in for one: what :func:`modio_roundtrip.run`
    asks of it, answered from ``PLAN`` by the unit's id -- the result it
    writes (None for none), how many looks it is still running for (None for
    ever), and the code it ends with."""

    PLAN: ClassVar[dict] = {}
    started: ClassVar[list] = []
    # The units whose process is going now, and the most there were at once.
    going: ClassVar[set] = set()
    most: ClassVar[int] = 0
    _pids = iter(range(5000, 6000))

    def __init__(self, target, args, daemon):
        self.unit, self.target = args[0], Path(args[5])
        self.result, self.looks, self.exitcode = self.PLAN[self.unit["id"]]
        self.pid, self.sentinel = next(self._pids), 0

    def start(self):
        self.started.append(self.unit["id"])
        self.going.add(self.unit["id"])
        _Unit.most = max(_Unit.most, len(self.going))

    def is_alive(self):
        if self.looks is None:
            return True
        self.looks -= 1
        if self.looks >= 0:
            return True
        self.going.discard(self.unit["id"])
        if self.result is not None:
            modio_roundtrip._write_json(self.target, self.result)
        return False

    def join(self):
        pass


class _Slots:
    """The loader's slots, as the run uses them: who is waiting, and what was
    handed back."""

    def __init__(self, *_args):
        self.freed, self.all_wait = [], False

    def waiting(self, pid):
        return self.all_wait

    def free(self, pid):
        self.freed.append(pid)
        return 1


class TestTheRunOfUnits(unittest.TestCase):
    """What the run writes down of each unit, whichever way its process ends,
    and what it leaves for the next run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.out = self.root / "rt" / "anno117"
        self.results = self.out / modio_roundtrip.RESULTS
        self.clock = 0.0
        self.killed = []
        self.slots = None
        _Unit.started = []
        _Unit.going, _Unit.most = set(), 0

    def run_units(self, plan, timeout=60, **more):
        _Unit.PLAN = plan

        def sleep(_seconds):
            self.clock += 10

        def slots(*args):
            self.slots = self.slots or _Slots()
            return self.slots

        context = mock.Mock(Process=_Unit)
        with mock.patch.object(modio_roundtrip.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(modio_roundtrip.time, "monotonic", lambda: self.clock), \
                mock.patch.object(modio_roundtrip.time, "sleep", sleep), \
                mock.patch.object(modio_roundtrip, "_kill", lambda process: self.killed.append(process.unit["id"])), \
                mock.patch.object(modio_roundtrip, "_peak_memory", return_value=None), \
                mock.patch.object(modio_roundtrip, "Slots", slots), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as said:
            modio_roundtrip.run(
                [{"id": name, "name": name.upper()} for name in plan], games.ANNO_117,
                self.root / "assets.xml", self.out, more.pop("jobs", 4), timeout,
                more.pop("redo", False), False, **more)
        return said.getvalue()

    def result(self, unit):
        return json.loads((self.results / ("%s.json" % unit)).read_text(encoding="utf-8"))

    def test_a_unit_past_its_time_is_killed_and_written_down_as_a_timeout(self):
        half = self.out / modio_roundtrip.WORK / "slow"
        half.mkdir(parents=True)
        (half / "assets.xml").write_text("<ModOps>", encoding="utf-8")
        said = self.run_units({
            "slow": (None, None, None),
            "quick": ({"unit": "quick", "status": "identical", "seconds": 2.0}, 1, 0),
        }, timeout=25)
        self.assertEqual(self.killed, ["slow"])
        self.assertEqual(self.result("slow"), {
            "unit": "slow", "name": "SLOW", "game": games.ANNO_117, "status": "timeout", "seconds": 25,
        })
        # Half an export, of a unit with no result to read it by.
        self.assertFalse(half.exists())
        self.assertEqual(self.result("quick")["status"], "identical")
        self.assertIn("[1/2] identical", said)
        self.assertIn("[2/2] timeout", said)

    def test_a_process_that_ends_with_no_result_is_written_down_as_crashed(self):
        self.run_units({"a": (None, 0, -1073741819)})
        self.assertEqual(self.killed, [])
        self.assertEqual(self.result("a"), {
            "unit": "a", "name": "A", "game": games.ANNO_117, "status": "crashed", "exitcode": -1073741819,
        })

    def test_a_run_started_again_carries_on_with_the_units_that_have_no_result(self):
        modio_roundtrip._write_json(self.results / "a.json", {"unit": "a", "status": "differs", "seconds": 3.0})
        plan = {
            "a": ({"unit": "a", "status": "identical", "seconds": 1.0}, 0, 0),
            "b": ({"unit": "b", "status": "identical", "seconds": 1.0}, 0, 0),
        }
        said = self.run_units(plan)
        self.assertEqual(_Unit.started, ["b"])
        self.assertEqual(self.result("a")["status"], "differs")
        self.assertIn("2 units, 1 to run", said)
        # And told to, it runs them all again.
        _Unit.started = []
        self.run_units(plan, redo=True)
        self.assertEqual(sorted(_Unit.started), ["a", "b"])
        self.assertEqual(self.result("a")["status"], "identical")

    def test_each_units_time_is_kept_and_the_next_run_starts_the_longest_first(self):
        timings = self.root / "timings.json"
        plan = {
            "quick": ({"unit": "quick", "status": "identical", "seconds": 2.0}, 0, 0),
            "slow": ({"unit": "slow", "status": "identical", "seconds": 90.0}, 0, 0),
            "broke": (None, 0, 1),
        }
        self.run_units(plan, timings_file=timings)
        # A crash has no time, and is not given one.
        self.assertEqual(json.loads(timings.read_text(encoding="utf-8")), {"quick": 2.0, "slow": 90.0})
        # The next run writes somewhere new, as a census run does: no result
        # is there to take a time from, so the order is the timings file's.
        # The one never timed goes first, since nothing says it is quick.
        _Unit.started = []
        self.out = self.root / "rt-next" / "anno117"
        self.run_units(plan, timings_file=timings)
        self.assertEqual(_Unit.started, ["broke", "slow", "quick"])
        # With no timings file they start as they are listed.
        _Unit.started = []
        self.out = self.root / "rt-untimed" / "anno117"
        self.run_units(plan)
        self.assertEqual(_Unit.started, ["quick", "slow", "broke"])

    def test_no_more_units_run_at_once_than_asked_for(self):
        # Each is looked at twice before it has ended.
        done = {"status": "identical", "seconds": 1.0}
        self.run_units(dict.fromkeys("abcde", (done, 2, 0)), jobs=2)
        self.assertEqual(_Unit.most, 2)
        self.assertEqual(len(_Unit.started), 5)
        self.assertEqual(sorted(path.name for path in self.results.glob("*.json")),
                         ["a.json", "b.json", "c.json", "d.json", "e.json"])
        # And one at a time where one is asked for.
        _Unit.started, _Unit.going, _Unit.most = [], set(), 0
        self.run_units(dict.fromkeys("abc", (done, 2, 0)), jobs=1, redo=True)
        self.assertEqual((_Unit.most, len(_Unit.started)), (1, 3))

    def test_a_loader_slot_is_handed_back_however_the_unit_ended(self):
        said = self.run_units({
            "slow": (None, None, None),
            "broke": (None, 0, 1),
            "fine": ({"unit": "fine", "status": "identical", "seconds": 1.0}, 0, 0),
        }, timeout=25, loader_exe=self.root / "xmltest.exe")
        self.assertIn("timeout", said)
        self.assertEqual(len(self.slots.freed), 3)
        self.assertEqual(len(set(self.slots.freed)), 3)

    def test_the_wait_for_a_loader_slot_is_not_counted_against_the_unit(self):
        self.slots = _Slots()
        self.slots.all_wait = True
        # Running for 50 seconds, all of it waiting, with 25 to do its work in.
        self.run_units({"a": ({"unit": "a", "status": "identical", "seconds": 1.0}, 5, 0)},
                       timeout=25, loader_exe=self.root / "xmltest.exe")
        self.assertEqual(self.killed, [])
        self.assertEqual(self.result("a")["status"], "identical")

    def test_stopped_by_hand_it_kills_what_is_running_and_the_results_stay(self):
        plan = {
            "fine": ({"unit": "fine", "status": "identical", "seconds": 1.0}, 0, 0),
            "slow": (None, None, None),
        }

        def sleep(_seconds):
            raise KeyboardInterrupt

        _Unit.PLAN = plan
        context = mock.Mock(Process=_Unit)
        with mock.patch.object(modio_roundtrip.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(modio_roundtrip.time, "sleep", sleep), \
                mock.patch.object(modio_roundtrip, "_kill", lambda process: self.killed.append(process.unit["id"])), \
                mock.patch.object(modio_roundtrip, "_peak_memory", return_value=None), \
                mock.patch("sys.stdout", new_callable=io.StringIO), \
                self.assertRaises(KeyboardInterrupt):
            modio_roundtrip.run([{"id": name, "name": name} for name in plan], games.ANNO_117,
                                self.root / "assets.xml", self.out, 4, 3600, False, False)
        self.assertEqual(self.killed, ["slow"])
        self.assertEqual(self.result("fine")["status"], "identical")


class TestAUnitsOwnProcess(unittest.TestCase):
    """What a unit's process writes, and how one is ended."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def child(self, run_unit):
        target = self.root / "results" / "a.json"
        with mock.patch.object(modio_roundtrip, "open_index", return_value="the index") as opened, \
                mock.patch.object(modio_roundtrip, "run_unit", run_unit):
            modio_roundtrip._child({"id": "a", "name": "A"}, games.ANNO_117, str(self.root / "assets.xml"),
                                   str(self.root / "work"), True, str(target))
        opened.assert_called_once_with(self.root / "assets.xml")
        return json.loads(target.read_text(encoding="utf-8"))

    def test_it_writes_the_units_result_where_the_run_looks_for_it(self):
        asked = []

        def run_unit(*args):
            asked.append(args)
            return {"unit": "a", "status": "identical"}

        self.assertEqual(self.child(run_unit), {"unit": "a", "status": "identical"})
        self.assertEqual(asked, [({"id": "a", "name": "A"}, "the index", games.ANNO_117,
                                  self.root / "work", True, None, None, False)])

    def test_a_unit_that_breaks_is_a_result_and_says_where_it_broke(self):
        def run_unit(*_args):
            raise ValueError("no such asset")

        result = self.child(run_unit)
        self.assertEqual((result["unit"], result["name"], result["game"], result["status"]),
                         ("a", "A", games.ANNO_117, "error"))
        self.assertIn("ValueError: no such asset", result["error"])

    def test_a_killed_slot_holder_gives_its_slot_to_the_next(self):
        import multiprocessing

        slots = modio_roundtrip.Slots(multiprocessing.get_context("spawn"), 1, 2)
        # As a unit's process holds one when it is killed: taken, never left.
        with mock.patch.object(modio_roundtrip.os, "getpid", return_value=4242):
            slots.__enter__()
        self.assertFalse(slots._semaphore.acquire(False))
        self.assertEqual(slots.free(4242), 1)
        self.assertEqual(slots.free(4242), 0)
        self.assertTrue(slots._semaphore.acquire(False))
        slots._semaphore.release()
        with slots:
            self.assertEqual(list(slots._holders), [os.getpid()])
        self.assertEqual(list(slots._holders), [0])

    def test_killing_a_unit_ends_what_it_started_too(self):
        import multiprocessing
        import subprocess
        import time

        said = self.root / "pid.txt"
        started = (
            "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
        )
        process = multiprocessing.get_context("spawn").Process(
            target=subprocess.call, args=([sys.executable, "-c", started, str(said)],), daemon=True)
        process.start()
        self.addCleanup(process.kill)
        until = time.monotonic() + 30
        while not (said.is_file() and said.read_text()) and time.monotonic() < until:
            time.sleep(0.05)
        inner = int(said.read_text())

        modio_roundtrip._kill(process)

        self.assertFalse(process.is_alive())
        if os.name == "nt":
            # An xmltest left running holds its memory until it finishes.
            listed = subprocess.run(["tasklist", "/FI", "PID eq %d" % inner, "/NH", "/FO", "CSV"],
                                    capture_output=True, text=True, check=False).stdout
            self.assertNotIn('"%d"' % inner, listed)


IN_A_GROUP = '<Group Condition="!#another-mod">%s</Group>' % PRODUCT


class TestAModReadAsWritten(RoundTripCase):
    """The questions asked of a mod read with every op kept as written."""

    def setUp(self):
        super().setUp()

    def test_every_op_and_group_comes_back_as_it_was_and_where_it_was(self):
        result = self.run_unit(self.zip_holding(First=RADIUS + IN_A_GROUP, Second=EVERY_FACTORY))

        self.assertEqual(result["reads"], "written")
        self.assertEqual(result["status"], "identical", result)
        found = result["as_written"]
        # Three ops and the group one of them is in.
        self.assertEqual((found["mods"], found["ops"], found["same"]), (2, 4, 2))
        self.assertEqual((found["moved"], found["differs"], found["missing"], found["extra"]), (0, 0, 0, 0))

    def test_the_mod_is_laid_out_with_how_deep_each_op_sits(self):
        mod = self.mod(self.root / "Mod", RADIUS + IN_A_GROUP)
        depths = [depth for _file, depth, _text in modio_roundtrip.laid_out(mod)]
        self.assertEqual(depths, [0, 0, 1])
        self.assertIn("Group", modio_roundtrip.laid_out(mod)[1][2])
        self.assertNotIn("Product", modio_roundtrip.laid_out(mod)[1][2])
        # Each in the mod's patch, which is the one file it has.
        self.assertEqual({file for file, _depth, _text in modio_roundtrip.laid_out(mod)}, {""})

    def test_an_op_the_export_leaves_out_is_a_difference(self):
        real = generator.build_modops_xml

        def forgetful(loadout, *args, **kwargs):
            root = etree.fromstring(real(loadout, *args, **kwargs).encode("utf-8"))
            ops = root.findall(".//ModOp")
            ops[-1].getparent().remove(ops[-1])
            return etree.tostring(root, encoding="unicode")

        with mock.patch.object(generator, "build_modops_xml", forgetful):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + PRODUCT))

        self.assertEqual(result["status"], "differs")
        found = result["as_written"]
        self.assertEqual((found["differs"], found["missing"], found["extra"]), (1, 1, 0))
        self.assertIn("- 1 at depth 0", found["examples"][0])

    def test_an_op_the_export_takes_out_of_its_group_is_one_too(self):
        real = generator.build_modops_xml

        def flat(loadout, *args, **kwargs):
            root = etree.fromstring(real(loadout, *args, **kwargs).encode("utf-8"))
            group = root.find(".//Group")
            for op in list(group):
                root.append(op)
            return etree.tostring(root, encoding="unicode")

        with mock.patch.object(generator, "build_modops_xml", flat):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + IN_A_GROUP))

        self.assertEqual(result["as_written"]["differs"], 1)
        self.assertEqual(result["status"], "differs")

    def test_two_ops_changing_places_is_said_and_is_not_identical(self):
        real = generator.build_modops_xml

        def swapped(loadout, *args, **kwargs):
            root = etree.fromstring(real(loadout, *args, **kwargs).encode("utf-8"))
            first, second = root.findall(".//ModOp")
            second.addnext(first)
            return etree.tostring(root, encoding="unicode")

        with mock.patch.object(generator, "build_modops_xml", swapped):
            result = self.run_unit(self.zip_holding(Mod=RADIUS + PRODUCT))

        found = result["as_written"]
        self.assertEqual((found["moved"], found["differs"]), (1, 0))
        self.assertIn("moved, first at op 1", found["examples"][0])
        self.assertEqual(result["status"], "same-effect")

    def test_the_second_export_is_the_mod_written_narrow_where_it_can_be(self):
        result = self.run_unit(
            self.zip_holding(Mod=RADIUS + EVERY_FACTORY + IN_A_GROUP), keep=True
        )

        self.assertEqual(sorted(result["modes"]), ["action", "rules"])
        self.assertNotIn("narrow", result)
        self.assertEqual(result["action"], "identical")
        # The op and the pattern; not the group, nor the op in it.
        self.assertEqual((result["rules"], result["narrowable"]), (4, 2))
        written = (self.root / "work/unit/action/Mod" / PATCH).read_text(encoding="utf-8")
        self.assertNotIn("//Values[", written)
        self.assertIn('Condition="!#another-mod"', written)
        whole = (self.root / "work/unit/rules/Mod" / PATCH).read_text(encoding="utf-8")
        self.assertIn("//Values[", whole)

    def test_an_op_the_studio_could_not_read_stays_in_it(self):
        result = self.run_unit(self.zip_holding(Mod=RADIUS + FLOOR), keep=True)

        self.assertEqual(result["blocked"], 1)
        written = (self.root / "work/unit/action/Mod" / PATCH).read_text(encoding="utf-8")
        self.assertIn("floor(", written)
        self.assertEqual(result["as_written"]["same"], 1)

    def test_what_takes_an_op_out_of_the_action(self):
        _session, loadout = modio_roundtrip.read(
            *modio_roundtrip.folders_of(self.zip_holding(Mod=RADIUS + PRODUCT)),
            self.index, games.ANNO_117,
        )
        first, second = loadout.ops
        self.assertEqual(modio_roundtrip.narrowable(loadout), {first.uid, second.uid})
        second.caveat = "the reading is short here"
        self.assertEqual(modio_roundtrip.narrowable(loadout), {first.uid})
        second.caveat = ""
        first.active = False
        self.assertEqual(modio_roundtrip.narrowable(loadout), {second.uid})
        first.active = True
        # One whose work is shown in the other's changes, and that other.
        (change,) = [c for c in loadout.changes.values() if c.op_uid == first.uid]
        second.folded = [list(change.key)]
        self.assertEqual(modio_roundtrip.narrowable(loadout), set())

    def test_the_summary_leads_with_them(self):
        result = self.run_unit(self.zip_holding(Mod=RADIUS + EVERY_FACTORY))
        results = self.root / "results"
        results.mkdir()
        (results / "unit.json").write_text(json.dumps(result), encoding="utf-8")
        said = modio_roundtrip.summarise(results, "a run")
        self.assertIn("read with every op kept as written: 1 of 1 units", said)
        self.assertIn("1 mods and 2 ops and groups; 1 mods the same, 0 with an op moved, 0 that differ", said)
        self.assertIn("written narrow where it can be (2 ops; 0 stay as written)", said)
        self.assertNotIn("the change list alone", said)


@requires(XMLTEST_117, LOADER)
class TestAgainstTheGamesOwnLoader(RoundTripCase):
    """The same units, with the loader as the judge rather than the Studio."""

    def run_loaded(self, files):
        return modio_roundtrip.run_unit(
            {"id": "unit", "name": "a unit", "files": str(files)},
            self.index,
            games.ANNO_117,
            self.root / "work",
            loader=modio_roundtrip.LoaderSetup(XMLTEST_117, self.assets),
        )

    def test_a_mod_read_and_exported_faithfully_agrees_with_the_loader(self):
        result = self.run_loaded(self.zip_holding(Mod=RADIUS + PRODUCT))

        self.assertEqual(result["loader"]["original"]["failures"], [])
        self.assertEqual(result["loader"]["original"]["changed"], 1)
        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("same", "same", "same"),
            result["loader"],
        )

    def test_an_export_that_changes_what_the_game_loads(self):
        real = generator.build_modops_xml

        def wrong(loadout, *args, **kwargs):
            return real(loadout, *args, **kwargs).replace("<Radius>12</Radius>", "<Radius>13</Radius>")

        with mock.patch.object(generator, "build_modops_xml", wrong):
            result = self.run_loaded(self.zip_holding(Mod=RADIUS))

        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("same", "differs", "differs"),
        )
        self.assertEqual(result["loader"]["rules"]["writing"]["assets"], 1)
        self.assertIn("13", "\n".join(result["loader"]["rules"]["writing"]["examples"][0]["diff"]))

    def test_an_op_the_studio_cannot_read_still_reaches_the_game(self):
        result = self.run_loaded(self.zip_holding(Mod=RADIUS + FLOOR))

        self.assertEqual(result["blocked"], 1)
        # The reading is short by what the op does; the export is not, because it
        # carries the op; and the writing is judged on what was read, so an op
        # the Studio shows nothing for is not held against it.
        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("differs", "same", "same"),
            result["loader"],
        )

    def test_the_mod_written_narrow_where_it_can_be_goes_through_the_loader_on_asking(self):
        files = self.zip_holding(Mod=RADIUS + PRODUCT)
        without = self.run_loaded(files)
        result = modio_roundtrip.run_unit(
            {"id": "unit", "name": "a unit", "files": str(files)},
            self.index, games.ANNO_117, self.root / "work",
            loader=modio_roundtrip.LoaderSetup(XMLTEST_117, self.assets, action=True),
        )
        self.assertNotIn("game_action", without)
        self.assertEqual(
            (result["game_reading"], result["game_roundtrip"], result["game_action"]),
            ("same", "same", "same"),
            result["loader"],
        )

    def test_a_mod_the_studio_only_partly_reads(self):
        # A group asking something the Studio cannot answer is not read; the
        # loader can answer it, and runs what is inside.
        # A search the Studio does answer now (see the test below) was the
        # example here; an expression round one is not.
        grouped = (
            "<Group Condition=\"boolean(//Values[Standard/GUID='1000']/Building)\">"
            '<ModOp Type="replace" GUID="1001" '
            'Path="/Values/Building/Radius"><Radius>9</Radius></ModOp></Group>'
        )
        result = self.run_loaded(self.zip_holding(Mod=RADIUS + grouped))

        # Not read, and carried: the reading differs from the game and the trip
        # as a whole does not. It used to, when a group was only set aside.
        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("differs", "same", "same"),
        )
        self.assertEqual([e["guid"] for e in result["loader"]["reading"]["examples"]], ["1001"])

    def test_a_group_with_a_condition_is_read_the_way_the_loader_runs_it(self):
        for condition, runs in (
            ("@1000/Building", True),
            ("@1000/Nothing", False),
            # A search of the whole file: answered from it, as the loader does.
            ("//Values[Standard/GUID='1000']/Building", True),
            ("//Values[Standard/GUID='4242']", False),
        ):
            with self.subTest(condition=condition):
                grouped = (
                    '<Group Condition="%s"><ModOp Type="replace" GUID="1001" '
                    'Path="/Values/Building/Radius"><Radius>9</Radius></ModOp></Group>' % condition
                )
                result = self.run_loaded(self.zip_holding(Mod=RADIUS + grouped))
                self.assertEqual(
                    (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
                    ("same", "same", "same"),
                    result["loader"],
                )
                self.assertEqual(result["changes"], 2 if runs else 1)
                shutil.rmtree(self.root / "files", True)

    def test_a_condition_asking_after_another_mod_of_the_unit(self):
        """Every ``#ModID`` is false inside xmltest, so the loader is told what
        is loaded as the app's own check tells it. Untold, *Production Works*'
        sulphur group ran for the Studio and never for the loader, and the
        census held the difference against the round trip."""
        files = self.root / "files"
        self.mod(files / "First", RADIUS, modinfo='{"ModID": "first-mod"}')
        self.mod(files / "Second",
                 '<Group Condition="#first-mod"><ModOp Type="replace" GUID="1001" '
                 'Path="/Values/Building/Radius"><Radius>9</Radius></ModOp></Group>')
        result = self.run_loaded(files)

        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("same", "same", "same"),
            result["loader"],
        )

    def test_a_reading_gap_that_is_written_back_as_misread(self):
        real = reader.Translator._run

        def blind(translator, op, *part):
            result = real(translator, op, *part)
            if op.guid == "1001":
                # Read as doing nothing, so neither a change nor a carried op:
                # the op the reading kept is taken back out as well.
                if result.record is not None:
                    translator.found[op.mod].remove(result.record)
                return modops.OpResult(op, modops.SETTLED, "misread")
            return result

        beta = '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>9</Radius></ModOp>'
        with mock.patch.object(reader.Translator, "_run", blind):
            result = self.run_loaded(self.zip_holding(Mod=RADIUS + beta))

        self.assertEqual(
            (result["game_reading"], result["game_writing"], result["game_roundtrip"]),
            ("differs", "same", "differs"),
        )


class TestTheComparisons(unittest.TestCase):
    def test_layout_and_comments_are_not_differences(self):
        one = modio_roundtrip.canonical_fragment("<A>\n  <B>1</B> <!-- note -->\n</A>")
        two = modio_roundtrip.canonical_fragment("<A><B>1</B></A>")
        self.assertEqual(one, two)
        self.assertNotEqual(one, modio_roundtrip.canonical_fragment("<A><B>2</B></A>"))

    def test_a_change_is_compared_without_its_identity(self):
        from anno_modding_studio.core.changes import OP_ADD, Change

        def added():
            return Change(guid="1", asset_label="", path="/Values", tag="X", op=OP_ADD,
                          payload="<X>1</X>", origin="m")

        first, second = added(), added()
        self.assertNotEqual(first.uid, second.uid)
        self.assertEqual(modio_roundtrip.signature(first), modio_roundtrip.signature(second))

    def test_like_reasons_count_together(self):
        self.assertEqual(
            modio_roundtrip._normal("there is no asset 9999 in your assets.xml"),
            modio_roundtrip._normal("there is no asset 1234 in your assets.xml"),
        )

    def test_fields_that_appear_once_are_compared_in_any_order(self):
        one = etree.fromstring(b"<A><X>1</X><Y><Z>2</Z></Y></A>")
        two = etree.fromstring(b"<A><Y><Z>2</Z></Y><X>1</X></A>")
        self.assertEqual(modio_roundtrip.pretty(one), modio_roundtrip.pretty(two))

    def test_but_a_list_keeps_its_order(self):
        one = etree.fromstring(b"<A><Item>1</Item><Item>2</Item></A>")
        two = etree.fromstring(b"<A><Item>2</Item><Item>1</Item></A>")
        self.assertNotEqual(modio_roundtrip.pretty(one), modio_roundtrip.pretty(two))

    def test_text_between_a_section_s_fields_is_not_a_difference(self):
        stray = etree.fromstring(b"<A><X>1</X>&gt;\n</A>")
        clean = etree.fromstring(b"<A><X>1</X></A>")
        self.assertEqual(modio_roundtrip.pretty(stray), modio_roundtrip.pretty(clean))
        other = etree.fromstring(b"<A><X>2</X></A>")
        self.assertNotEqual(modio_roundtrip.pretty(clean), modio_roundtrip.pretty(other))

    def test_an_asset_stripped_of_its_guid_is_as_gone_as_a_deleted_one(self):
        """The loader's diff drops both from its "after" list alike, and the
        Studio shows the stripped one as a husk: the same state, two spellings."""
        inert = mock.Mock(return_value=False)
        husk = etree.fromstring(b"<Asset><Template>Quest</Template><Values/></Asset>")
        whole = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>"
        )
        gone = modio_roundtrip.prepared(None, inert)
        self.assertEqual(modio_roundtrip.prepared(husk, inert), gone)
        self.assertNotEqual(modio_roundtrip.prepared(whole, inert), gone)

    def test_an_asset_compared_more_than_once_is_prepared_once(self):
        """The census puts one source through several comparisons, and
        preparing an asset is nearly all of what a comparison costs."""
        assets = {
            guid: etree.fromstring(
                b"<Asset><Values><Standard><GUID>%d</GUID><Name>%s</Name></Standard></Values></Asset>"
                % (guid, name)
            )
            for guid, name in ((1, b"a"), (2, b"b"))
        }
        other = dict(assets)
        other[2] = etree.fromstring(
            b"<Asset><Values><Standard><GUID>2</GUID><Name>c</Name></Standard></Values></Asset>"
        )
        inert = mock.Mock(return_value=False)
        asked = []

        def source(guid):
            asked.append(guid)
            return assets[int(guid)]

        labels = ("one", "two")
        plain = modio_roundtrip.compare_views({"1", "2"}, source, lambda g: other[int(g)], inert, labels)
        self.assertEqual((plain["assets"], plain["assets_strict"]), (1, 1))
        self.assertEqual(plain["examples"][0]["guid"], "2")

        asked.clear()
        kept = modio_roundtrip.Views(source, inert)
        for _again in range(3):
            self.assertEqual(
                modio_roundtrip.compare_views({"1", "2"}, kept, lambda g: other[int(g)], inert, labels), plain
            )
        # Once each, and once more a time for the one whose difference is shown.
        self.assertEqual(sorted(asked), ["1", "2", "2", "2", "2"])
        asked.clear()
        modio_roundtrip.compare_views({"1"}, kept, kept, inert, labels)
        self.assertEqual(asked, [])

    def test_an_asset_with_nothing_inert_in_it_is_laid_out_once(self):
        whole = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1</GUID></Standard><Cost><Costs><Item><Ingredient>5</Ingredient>"
            b"</Item></Costs></Cost></Values></Asset>"
        )
        with mock.patch.object(loadercheck, "pretty", wraps=loadercheck.pretty) as laid_out:
            exact, meant = modio_roundtrip.prepared(whole, mock.Mock(return_value=False))
            self.assertEqual(laid_out.call_count, 1)
            self.assertEqual(exact, meant)
            stripped = modio_roundtrip.prepared(whole, lambda node: node.tag == "Item")
            self.assertEqual(laid_out.call_count, 3)
        self.assertEqual(stripped[0], exact)
        self.assertIn("Ingredient", stripped[0])
        self.assertNotIn("Ingredient", stripped[1])
        # And the asset handed in is left as it was.
        self.assertIsNotNone(whole.find("Values/Cost/Costs/Item"))


if __name__ == "__main__":
    unittest.main()
