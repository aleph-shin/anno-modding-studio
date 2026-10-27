"""Anno 1800 mods read straight out of a zip in a mods folder, through a
mirror of it in the Studio's cache."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    build,
    convert,
    disk,
    installplan,
    modfolder,
    reader,
    switches,
    zipped,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

MODINFO = '{"ModID": "%s", "ModName": {"English": "%s"}}'
ASSETS = "data/config/export/main/asset/assets.xml"


def _zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as out:
        for name, text in files.items():
            out.writestr(name, text)
    return path


class _Mods(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods = Path(self._tmp.name) / "mods"
        self.mods.mkdir()
        patched = mock.patch.object(switches, "documents_mods", lambda game: None)
        patched.start()
        self.addCleanup(patched.stop)
        # The three shapes the loader's docs give.
        _zip(self.mods / "Single.zip", {"modinfo.json": MODINFO % ("single", "Single"), ASSETS: "<ModOps/>"})
        _zip(self.mods / "Wrapped.zip", {"Wrapped/modinfo.json": MODINFO % ("wrapped", "Wrapped"),
                                         "Wrapped/" + ASSETS: "<ModOps/>"})
        _zip(self.mods / "-Box.zip", {"A/modinfo.json": MODINFO % ("box-a", "A"), "A/" + ASSETS: "<ModOps/>",
                                      "B/modinfo.json": MODINFO % ("box-b", "B"), "B/" + ASSETS: "<ModOps/>"})
        (self.mods / "Folder").mkdir()
        (self.mods / "Folder" / "modinfo.json").write_text(MODINFO % ("folder", "Folder"), encoding="utf-8")


class TestTheMirror(_Mods):
    def test_read_beside_the_folder_it_is_of(self):
        paths = switches.mod_paths(ANNO_1800, self.mods)
        self.assertEqual(paths[0], self.mods)
        mirror = paths[1]
        self.assertTrue(zipped.is_mirrored(mirror))
        self.assertFalse(zipped.is_mirrored(self.mods))
        found = [path.relative_to(mirror).as_posix() for path in modfolder.mod_folders(mirror)]
        self.assertEqual(found, ["-Box.zip", "-Box.zip/A", "-Box.zip/B", "Single.zip", "Wrapped.zip",
                                 "Wrapped.zip/Wrapped"])
        self.assertEqual(zipped.zip_for(mirror / "-Box.zip" / "A"), self.mods / "-Box.zip")

    def test_unpacked_once_until_the_zip_changes_and_gone_with_it(self):
        mirror = zipped.sync(ANNO_1800, self.mods)
        marker = mirror / "Single.zip" / "left-by-test"
        marker.write_text("x", encoding="utf-8")
        zipped.sync(ANNO_1800, self.mods)
        self.assertTrue(marker.exists())
        _zip(self.mods / "Single.zip", {"modinfo.json": MODINFO % ("single", "Single v2")})
        zipped.sync(ANNO_1800, self.mods)
        self.assertFalse(marker.exists())
        (self.mods / "Single.zip").unlink()
        zipped.sync(ANNO_1800, self.mods)
        self.assertFalse((mirror / "Single.zip").exists())

    def test_not_for_117_and_not_without_zips(self):
        self.assertIsNone(zipped.sync(ANNO_117, self.mods))
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(switches.mod_paths(ANNO_1800, empty), [empty])

    def test_a_broken_zip_is_left_out(self):
        (self.mods / "Broken.zip").write_bytes(b"not a zip")
        mirror = zipped.sync(ANNO_1800, self.mods)
        self.assertFalse((mirror / "Broken.zip").exists())
        self.assertTrue((mirror / "Single.zip").is_dir())


class TestAZipDeeperThanWindowsReaches(unittest.TestCase):
    """A zip whose tree runs past 260 characters is mirrored since A8-1, and
    a mirror is only right while it can be taken away again: left behind, it
    is read as a mod whose zip has gone, or one with files its zip dropped."""

    DEEP = "/".join(["d%d" % n + "x" * 38 for n in range(7)])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods = Path(self._tmp.name) / "mods"
        self.mods.mkdir()
        # The mirror is in the cache, not the temporary folder, and deeper
        # than a plain removal reaches.
        self.addCleanup(shutil.rmtree, disk.reachable(zipped.mirror_of(self.mods)), True)
        self.one = self.zip("one.zip", "leaf.cfg", "gone.cfg")
        self.mirror = zipped.sync(ANNO_1800, self.mods)

    def zip(self, name: str, *leaves) -> Path:
        files = {"modinfo.json": MODINFO % ("one", "One")}
        files.update(("data/%s/%s" % (self.DEEP, leaf), "x" * (n + 1)) for n, leaf in enumerate(leaves))
        return _zip(self.mods / name, files)

    def held(self) -> list:
        return sorted(entry.name for entry in self.mirror.iterdir() if entry.is_dir())

    def leaves(self, name: str) -> list:
        return sorted(os.listdir(disk.reachable(self.mirror / name / "data" / self.DEEP)))

    def test_it_is_mirrored_all_the_way_down(self):
        self.assertEqual(self.held(), ["one.zip"])
        self.assertGreater(len(str(self.mirror / "one.zip" / "data" / self.DEEP / "leaf.cfg")), 260)
        self.assertEqual(self.leaves("one.zip"), ["gone.cfg", "leaf.cfg"])

    def test_renamed_it_leaves_no_mirror_under_its_old_name(self):
        self.one.rename(self.mods / "-one.zip")
        zipped.sync(ANNO_1800, self.mods)
        self.assertEqual(self.held(), ["-one.zip"])
        self.assertEqual([path.name for path in modfolder.mod_folders(self.mirror)], ["-one.zip"])

    def test_changed_it_keeps_no_file_the_zip_dropped(self):
        self.zip("one.zip", "leaf.cfg", "new.cfg", "newer.cfg")
        zipped.sync(ANNO_1800, self.mods)
        self.assertEqual(self.leaves("one.zip"), ["leaf.cfg", "new.cfg", "newer.cfg"])

    def test_deleted_beside_another_its_mirror_goes(self):
        _zip(self.mods / "Other.zip", {"modinfo.json": MODINFO % ("other", "Other")})
        self.one.unlink()
        zipped.sync(ANNO_1800, self.mods)
        self.assertEqual(self.held(), ["Other.zip"])

    def test_and_with_the_last_zip_the_mirror_itself(self):
        self.one.unlink()
        self.assertIsNone(zipped.sync(ANNO_1800, self.mods))
        self.assertFalse(self.mirror.exists())


class TestTheSwitch(_Mods):
    def test_off_by_the_zip_s_name_and_switched_by_renaming_it(self):
        found = switches.read(self.mods, ANNO_1800)
        self.assertEqual(found.on, {"Box.zip": False, "Folder": True, "Single.zip": True, "Wrapped.zip": True})
        written = switches.write(found, {"Box.zip": True, "Single.zip": False})
        self.assertTrue((self.mods / "Box.zip").is_file())
        self.assertTrue((self.mods / "-Single.zip").is_file())
        mirror = zipped.mirror_of(self.mods)
        # The mirror followed rather than being unpacked again, and what the
        # loadout holds follows it there.
        self.assertEqual(dict(written.renamed), {"Box.zip": mirror / "Box.zip",
                                                 "Single.zip": mirror / "-Single.zip"})
        self.assertTrue((mirror / "Box.zip" / "A" / "modinfo.json").is_file())
        self.assertEqual(switches.read(self.mods, ANNO_1800).off, ["Single.zip"])


class TestTheInventory(_Mods):
    def test_zipped_mods_are_mods_and_the_conversion_leaves_the_zip(self):
        found = installplan.inventory(ANNO_1800, self.mods)
        by_id = {mod.mod_id: mod for mod in found if mod.mod_id}
        self.assertEqual(sorted(by_id), ["box-a", "box-b", "folder", "single", "wrapped"])
        self.assertTrue(by_id["single"].zipped)
        self.assertFalse(by_id["folder"].zipped)
        self.assertEqual(convert._where(by_id["single"]), convert.ZIPPED)
        self.assertEqual(convert._fate(convert.Copy(installed=by_id["single"], on=True, loads=True,
                                                    where=convert.ZIPPED), True), convert.LEFT_ZIPPED)


sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow, SchemaTestBase  # noqa: E402


class TestReadingThem(SchemaTestBase):
    def test_a_mods_folder_read_names_them_by_their_zip(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(switches, "documents_mods", lambda game: None):
            mods = Path(tmp)
            op = ('<ModOps><ModOp Type="replace" GUID="1000" Path="/Values/Standard/Name">'
                  '<Name>zipped</Name></ModOp></ModOps>')
            _zip(mods / "Single.zip", {"modinfo.json": MODINFO % ("single", "Single"), ASSETS: op})
            _zip(mods / "Box.zip", {"A/modinfo.json": MODINFO % ("box-a", "A"),
                                    "A/" + ASSETS: op.replace(">zipped<", ">boxed<")})
            roots = switches.mod_paths(ANNO_1800, mods)
            folders = [folder for root in roots for folder in modfolder.mod_folders(root)]
            session = reader.analyse_all(folders, self.index, game=ANNO_1800, roots=roots)
            read = {one.origin: one for one in session.reads}
            self.assertIn("Single.zip", read)
            self.assertIn("Box.zip/A", read)
            self.assertEqual([len(read[o].changes) for o in ("Box.zip/A", "Single.zip")], [1, 1])




class TestNeverWrittenBack(PlayingWindow):
    def test_a_zipped_mod_is_not_exported_even_edited(self):
        from anno_modding_studio.core.changes import Change, ModLayer

        _zip(self.mods / "Single.zip", {"modinfo.json": MODINFO % ("single", "Single"), ASSETS: "<ModOps/>"})
        mirror = zipped.sync(ANNO_1800, self.mods)
        loadout = self.window.loadout
        loadout.layers = [ModLayer(origin="Single.zip", name="Single", folder=str(mirror / "Single.zip"))]
        change = Change(guid="1", asset_label="x", path="Values/Standard/Name", tag="Name", op="replace",
                        old_value="a", new_value="b", origin="Single.zip", authored=True)
        loadout.changes[change.key] = change
        self.assertNotIn("Single.zip", build.writable(loadout))


if __name__ == "__main__":
    unittest.main()
