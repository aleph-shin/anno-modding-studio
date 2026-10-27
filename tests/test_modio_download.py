"""Fetching a mod's file: whole, checked, and never half there."""

from __future__ import annotations

import hashlib
import http.client
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modio  # noqa: E402

BODY = bytes(range(256)) * 40
MD5 = hashlib.md5(BODY).hexdigest()


def _chunks(body: bytes, size: int = 1000):
    def stream(_url: str):
        for start in range(0, len(body), size):
            yield body[start:start + size]

    return stream


class TestDownload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = Path(self._tmp.name) / "in" / "mod.zip"

    def test_the_whole_file_arrives_checked_with_progress_along_the_way(self):
        seen = []
        got = modio.download(
            "https://cdn/x", self.dest, chunks=_chunks(BODY),
            progress=lambda done, total: seen.append((done, total)), md5=MD5, size=len(BODY),
        )
        self.assertEqual(got, self.dest)
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(seen[-1], (len(BODY), len(BODY)))
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], ["mod.zip"])

    def test_a_checksum_that_does_not_match_leaves_nothing(self):
        with self.assertRaises(modio.DownloadError):
            modio.download("u", self.dest, chunks=_chunks(BODY), md5="0" * 32)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_a_file_longer_than_said_is_stopped_where_it_passed(self):
        read = []

        def endless(_url):
            while True:
                read.append(1)
                yield b"x" * 1000

        with self.assertRaises(modio.DownloadError):
            modio.download("u", self.dest, chunks=endless, size=2500)
        self.assertEqual(len(read), 3)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_only_an_https_address_is_fetched(self):
        for url in ("file:///C:/Windows/win.ini", "http://cdn/x", "ftp://cdn/x"):
            with self.subTest(url=url), self.assertRaises(modio.DownloadError):
                modio.download(url, self.dest)
        self.assertFalse(self.dest.exists())

    def test_a_short_file_leaves_nothing(self):
        with self.assertRaises(modio.DownloadError):
            modio.download("u", self.dest, chunks=_chunks(BODY[:100]), size=len(BODY))
        self.assertFalse(self.dest.exists())

    def test_stopping_leaves_nothing(self):
        asked = []

        def stop():
            asked.append(True)
            return len(asked) > 2

        with self.assertRaises(modio.Cancelled):
            modio.download("u", self.dest, chunks=_chunks(BODY), cancelled=stop)
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_a_dropped_connection_is_a_download_error_and_leaves_nothing(self):
        def dropping(_url):
            yield b"abc"
            raise OSError("reset")

        with self.assertRaises(modio.DownloadError):
            modio.download("u", self.dest, chunks=dropping)
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_a_body_cut_short_is_a_download_error_and_leaves_nothing(self):
        # http.client's errors are not OSErrors: a chunked body cut short
        # raises IncompleteRead, a garbled answer BadStatusLine.
        for error in (http.client.IncompleteRead(b"abc", 10), http.client.BadStatusLine("")):
            with self.subTest(error=type(error).__name__):
                def failing(_url, error=error):
                    yield b"abc"
                    raise error

                with self.assertRaises(modio.DownloadError):
                    modio.download("u", self.dest, chunks=failing)
                self.assertEqual(list(self.dest.parent.iterdir()), [])


class TestTheSummaryKnowsTheFile(unittest.TestCase):
    def test_link_name_checksum_and_the_scan(self):
        summary = modio.summarise({
            "id": 1, "name": "X",
            "modfile": {
                "id": 9, "version": "1", "filename": "x-9rm7.zip", "filesize": 12,
                "download": {"binary_url": "https://api.mod.io/v1/.../download", "date_expires": 1},
                "filehash": {"md5": "ABCDEF"}, "virus_positive": 1,
            },
        })
        self.assertTrue(summary.has_file)
        self.assertEqual(summary.filename, "x-9rm7.zip")
        self.assertEqual(summary.md5, "abcdef")
        self.assertTrue(summary.virus_positive)

    def test_no_file_yet(self):
        self.assertFalse(modio.summarise({"id": 1, "name": "X"}).has_file)


if __name__ == "__main__":
    unittest.main()
