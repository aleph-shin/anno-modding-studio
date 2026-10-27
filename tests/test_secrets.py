"""Holding the mod.io key: wrapped, in a file of its own, out of the tests' way."""

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

from support import reverse_bytes  # noqa: E402

from anno_modding_studio.core import secrets  # noqa: E402


class TestTheStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "deep" / "credentials.bin"
        self.store = secrets.Store(self.path, wrap=reverse_bytes, unwrap=reverse_bytes)

    def test_nothing_saved_reads_as_no_credentials(self):
        self.assertFalse(self.store.exists)
        self.assertFalse(self.store.load())

    def test_what_is_saved_comes_back_and_is_not_on_disk_in_the_clear(self):
        self.store.save(secrets.Credentials(api_key="k3y", email="a@b"))
        self.assertTrue(self.store.exists)
        self.assertNotIn(b"k3y", self.path.read_bytes())
        loaded = self.store.load()
        self.assertEqual(loaded.api_key, "k3y")
        self.assertEqual(loaded.email, "a@b")
        self.assertEqual(loaded.token, "")

    def test_a_file_that_cannot_be_unwrapped_is_no_credentials(self):
        """Somebody else's, or another machine's: nothing can be done with it."""
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"\x00garbage")

        def refuse(_data):
            raise OSError("not yours")

        store = secrets.Store(self.path, wrap=reverse_bytes, unwrap=refuse)
        self.assertFalse(store.load())

    def test_a_file_that_unwraps_to_nonsense_is_no_credentials(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(reverse_bytes(b"[1, 2]"))
        self.assertFalse(self.store.load())

    def test_forgetting_removes_the_file_and_is_fine_twice(self):
        self.store.save(secrets.Credentials(api_key="k"))
        self.store.forget()
        self.assertFalse(self.store.exists)
        self.store.forget()

    def test_saving_leaves_no_partial_file_behind(self):
        self.store.save(secrets.Credentials(api_key="k"))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["credentials.bin"])


class TestWhereItLives(unittest.TestCase):
    def test_the_suites_cache_folder_carries_it_too(self):
        """Isolation nobody has to remember: moving the cache moves this."""
        with mock.patch.dict(os.environ, {"ANNO_STUDIO_CACHE": "C:/tmp/cache", "ANNO_STUDIO_SECRETS": ""}):
            self.assertEqual(secrets.default_path(), Path("C:/tmp/cache") / "modio" / "credentials.bin")

    def test_a_named_file_wins(self):
        with mock.patch.dict(os.environ, {"ANNO_STUDIO_SECRETS": "D:/x/c.bin"}):
            self.assertEqual(secrets.default_path(), Path("D:/x/c.bin"))

    def test_otherwise_beside_the_index_cache_under_local_app_data(self):
        with mock.patch.dict(
            os.environ,
            {"ANNO_STUDIO_CACHE": "", "ANNO_STUDIO_SECRETS": "", "LOCALAPPDATA": "C:/Users/x/AppData/Local"},
        ):
            found = secrets.default_path()
        self.assertEqual(found.name, "credentials.bin")
        self.assertEqual(found.parent.name, "modio")
        self.assertIn("AppData", str(found))


@unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows")
class TestDpapi(unittest.TestCase):
    def test_round_trip_and_the_wrong_entropy_is_refused(self):
        wrapped = secrets.protect(b"not-a-real-key")
        self.assertNotEqual(wrapped, b"not-a-real-key")
        self.assertEqual(secrets.unprotect(wrapped), b"not-a-real-key")
        with self.assertRaises(OSError):
            secrets.unprotect(wrapped, entropy=b"somebody else")


if __name__ == "__main__":
    unittest.main()
