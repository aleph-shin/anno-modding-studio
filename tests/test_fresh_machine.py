"""``tools/fresh_machine.py``: that it stops while a Studio or a census run
holds a lock in a folder it would take away, and takes away a folder however
deep it goes.

It imports neither lock, being standard library only, so these tests hold the
real locks and are what keeps its probe in step with them.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import fresh_machine  # noqa: E402
import modio_roundtrip  # noqa: E402

from anno_modding_studio.ui import one_studio  # noqa: E402

if sys.platform == "win32":
    import _winapi


@unittest.skipUnless(sys.platform == "win32", "fresh_machine is Windows only")
class TestHeldLocks(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_a_running_census_run_is_seen(self):
        lock = modio_roundtrip.RunLock(self.folder / "census.lock")
        self.assertIsNone(lock.take({"out": "rt"}))
        self.addCleanup(lock.release)
        self.assertEqual(fresh_machine.held_locks([self.folder]), [self.folder / "census.lock"])

    def test_an_open_studio_is_seen(self):
        lock = one_studio.Lock(self.folder / one_studio.LOCK_NAME)
        self.assertTrue(lock.take())
        self.addCleanup(lock.release)
        self.assertEqual(fresh_machine.held_locks([self.folder]),
                         [self.folder / one_studio.LOCK_NAME])

    def test_locks_nothing_holds_are_not_held(self):
        census = modio_roundtrip.RunLock(self.folder / "census.lock")
        self.assertIsNone(census.take({}))
        census.release()
        studio = one_studio.Lock(self.folder / one_studio.LOCK_NAME)
        self.assertTrue(studio.take())
        studio.release()
        self.assertEqual(fresh_machine.held_locks([self.folder]), [])
        # And the probe let go of them again.
        self.assertIsNone(census.take({}))
        census.release()
        self.assertTrue(studio.take())
        studio.release()


@unittest.skipUnless(sys.platform == "win32", "fresh_machine is Windows only")
class TestDeepFolders(unittest.TestCase):
    """R3-0, hands-on run 3: the archive keeps a folder's whole path under its
    own, a library mod nested eight deep passed MAX_PATH there, and the local
    folder stayed behind with WinError 206."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fresh_machine.extended(self.root), True)
        self.local = self.root / "local"
        self.deep = self.local.joinpath(*["a folder of a mod nested deep"] * 9)
        self.assertGreater(len(str(self.deep)), 260)
        os.makedirs(fresh_machine.extended(self.deep))
        with open(fresh_machine.extended(self.deep / "rdm.xml"), "w", encoding="utf-8") as out:
            out.write("<x/>")
        self.pointed = self.root / "pointed at"
        self.pointed.mkdir()
        (self.pointed / "keep.txt").write_text("kept", encoding="utf-8")
        self.link = self.deep / "link"
        _winapi.CreateJunction(str(self.pointed), fresh_machine.extended(self.link))

    def test_a_folder_past_max_path_is_moved_whole_though_it_must_be_copied(self):
        into = self.root / "archive"
        moved = []
        # Where the move cannot be a rename -- another volume, or a package's
        # redirected folder -- it is a copy and a delete, file by file.
        with mock.patch("os.rename", side_effect=OSError(17, "not the same device")):
            fresh_machine.take_away(self.local, into, moved)
        self.assertFalse(self.local.exists())
        target = into / self.local.drive.rstrip(":") / Path(*self.deep.parts[1:])
        self.assertTrue(os.path.isfile(fresh_machine.extended(target / "rdm.xml")))
        self.assertIn({"link": str(self.link), "to": str(self.pointed)}, moved)
        self.assertFalse(os.path.lexists(fresh_machine.extended(target / "link")))
        self.assertEqual((self.pointed / "keep.txt").read_text(encoding="utf-8"), "kept")

    def test_and_deleted_whole(self):
        moved = []
        fresh_machine.take_away(self.local, None, moved)
        self.assertFalse(self.local.exists())
        self.assertEqual(moved[-1], {"deleted": str(self.local)})
        self.assertTrue((self.pointed / "keep.txt").is_file())

    def test_a_path_is_said_as_it_was_given(self):
        for text in (r"C:\Users\x\AppData", r"\\server\share\x"):
            self.assertEqual(fresh_machine.plain(fresh_machine.extended(text)), text)
        self.assertTrue(fresh_machine.extended(r"\\server\share\x").startswith("\\\\?\\UNC\\"))


if __name__ == "__main__":
    unittest.main()
