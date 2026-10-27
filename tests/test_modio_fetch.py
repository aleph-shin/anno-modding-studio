"""The mod.io census fetcher, against a fake CDN.

Nothing here touches the network: the fetcher takes its transport as an
argument, and ``FakeCdn`` answers the way mod.io's did when it was tried --
a redirect from the download link, then single ranges, several ranges as
multipart/byteranges, or the whole file.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import modio_fetch  # noqa: E402
from modio_fetch import Response  # noqa: E402

from anno_modding_studio.core import modio  # noqa: E402

LINK = "https://g-1.modapi.io/v1/games/1/mods/9/files/90/download"
CDN = "https://binary.example/mod.zip"
BIG = random.Random(0).randbytes(3 * 1024 * 1024)
MB = random.Random(1).randbytes(1024 * 1024)


class FakeCdn:
    def __init__(self, blob: bytes, *, ignore_ranges=False, lapse_first=False):
        self.blob = blob
        self.ignore_ranges = ignore_ranges
        self.lapse_next = lapse_first
        self.calls = []

    def gets(self):
        return [call for call in self.calls if call[0] == "GET"]

    def heads(self):
        return [call for call in self.calls if call[0] == "HEAD"]

    def __call__(self, method, url, byte_range):
        self.calls.append((method, url, byte_range))
        if url == LINK:
            return Response(302, {"location": CDN + "?verify=token"})
        if not url.startswith(CDN):
            return Response(404)
        if self.lapse_next:
            self.lapse_next = False
            return Response(403)
        size = len(self.blob)
        if byte_range is None or self.ignore_ranges:
            return Response(200, {"content-length": str(size)}, self.blob)
        spans = []
        for text in byte_range[len("bytes=") :].split(","):
            start, end = (int(n) for n in text.split("-"))
            spans.append((start, min(end, size - 1)))
        if len(spans) == 1:
            start, end = spans[0]
            return Response(
                206, {"content-range": f"bytes {start}-{end}/{size}"}, self.blob[start : end + 1]
            )
        body = b"".join(
            b"\r\n--B\r\nContent-Type: application/zip\r\n"
            + f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n".encode()
            + self.blob[start : end + 1]
            for start, end in spans
        )
        return Response(
            206, {"content-type": "multipart/byteranges; boundary=B"}, body + b"\r\n--B--\r\n"
        )


def _zip(*members, zip64=()):
    """Members are (name, data) or (name, data, compress_type); a name ending "/" is a folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member in members:
            name, data = member[0], member[1]
            method = member[2] if len(member) > 2 else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo(name)
            info.compress_type = method
            if name in zip64:
                with archive.open(info, "w", force_zip64=True) as handle:
                    handle.write(data)
            else:
                archive.writestr(info, data)
    return buffer.getvalue()


def _mod(blob, *, modfile_id=90, flagged=0, md5=None, filesize=None):
    return {
        "id": 9,
        "name_id": "test-mod",
        "name": "Test Mod",
        "modfile": {
            "id": modfile_id,
            "filename": "mod.zip",
            "version": "1.0",
            "filesize": len(blob) if filesize is None else filesize,
            "filehash": {"md5": md5 or hashlib.md5(blob).hexdigest()},
            "virus_positive": flagged,
            "download": {"binary_url": LINK},
        },
    }


ASSETS = b'<ModOps><ModOp Type="merge" GUID="1" Path="/Values"><X>1</X></ModOp></ModOps>'
MODINFO = b'{"ModID": "test-mod", "Version": "1.0"}'


class FetcherCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.dest = self.tmp / "census" / "9"

    def fetch(self, blob, cdn=None, mod=None, **options):
        cdn = cdn or FakeCdn(blob)
        resolve = modio_fetch.resolver(lambda: None, cdn, sleep=lambda _: None)
        manifest, fetched = modio_fetch.fetch_mod(
            mod or _mod(blob), self.dest, resolve, cdn, sleep=lambda _: None, **options
        )
        return manifest, fetched, cdn

    def written(self, path):
        return (self.dest / "files" / path).read_bytes()


class TestRangedFetch(FetcherCase):
    def test_reads_the_text_files_and_not_the_textures(self):
        blob = _zip(
            ("Mod/", b""),
            ("Mod/modinfo.json", MODINFO),
            ("Mod/data/config/export/main/asset/assets.xml", ASSETS),
            ("Mod/data/graphics/big.dds", BIG, zipfile.ZIP_STORED),
        )
        manifest, fetched, cdn = self.fetch(blob)

        self.assertTrue(fetched)
        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(manifest["strategy"], "ranged")
        self.assertEqual(self.written("Mod/modinfo.json"), MODINFO)
        self.assertEqual(self.written("Mod/data/config/export/main/asset/assets.xml"), ASSETS)
        self.assertFalse((self.dest / "files" / "Mod/data/graphics/big.dds").exists())
        self.assertEqual(
            [e["name"] for e in manifest["entries"]],
            ["Mod/", "Mod/modinfo.json", "Mod/data/config/export/main/asset/assets.xml",
             "Mod/data/graphics/big.dds"],
        )
        # The tail, then both members in one go; the download link resolved once.
        self.assertEqual(len(cdn.gets()), 2)
        self.assertEqual(len(cdn.heads()), 1)
        self.assertLess(manifest["bytes_received"], 100_000)

    def test_members_far_apart_are_asked_for_in_one_request(self):
        members = []
        for n in range(5):
            members.append((f"Mod/part{n}.xml", ASSETS))
            members.append((f"Mod/padding{n}.bin", MB, zipfile.ZIP_STORED))
        manifest, _, cdn = self.fetch(_zip(*members))

        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(len(manifest["extracted"]), 5)
        self.assertEqual(len(cdn.gets()), 2)
        self.assertEqual(cdn.gets()[1][2].count(","), 4)

    def test_a_local_header_longer_than_the_directory_entry(self):
        # A member written with forced zip64 carries zip64 sizes in its local
        # header only; with no slack the fetched span stops short of its data.
        blob = _zip(
            ("Mod/assets.xml", ASSETS),
            ("Mod/big.dds", BIG, zipfile.ZIP_STORED),
            zip64=("Mod/assets.xml",),
        )
        with mock.patch.object(modio_fetch, "HEADER_SLACK", 0):
            manifest, _, cdn = self.fetch(blob)

        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(self.written("Mod/assets.xml"), ASSETS)
        self.assertEqual(len(cdn.gets()), 3)

    def test_a_server_that_ignores_ranges(self):
        blob = _zip(("Mod/assets.xml", ASSETS), ("Mod/big.dds", BIG, zipfile.ZIP_STORED))
        manifest, _, cdn = self.fetch(blob, FakeCdn(blob, ignore_ranges=True))

        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(self.written("Mod/assets.xml"), ASSETS)
        self.assertEqual(len(cdn.gets()), 1)

    def test_a_lapsed_cdn_address_is_resolved_again(self):
        blob = _zip(("Mod/assets.xml", ASSETS), ("Mod/big.dds", BIG, zipfile.ZIP_STORED))
        manifest, _, cdn = self.fetch(blob, FakeCdn(blob, lapse_first=True))

        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(len(cdn.heads()), 2)

    def test_a_file_that_is_not_the_size_listed(self):
        blob = _zip(("Mod/assets.xml", ASSETS), ("Mod/big.dds", BIG, zipfile.ZIP_STORED))
        manifest, _, _ = self.fetch(blob, mod=_mod(blob, filesize=len(blob) + 10))

        self.assertEqual(manifest["status"], "failed")
        self.assertIn("not the", manifest["error"])


class TestWholeFetch(FetcherCase):
    def test_a_small_file_is_downloaded_whole_and_checked(self):
        blob = _zip(("Mod/assets.xml", ASSETS), ("Mod/modinfo.json", MODINFO))
        manifest, _, cdn = self.fetch(blob)

        self.assertEqual(manifest["status"], "ok", manifest["error"])
        self.assertEqual(manifest["strategy"], "whole")
        self.assertIs(manifest["md5_ok"], True)
        self.assertEqual([call[2] for call in cdn.gets()], [None])
        self.assertEqual(self.written("Mod/assets.xml"), ASSETS)

    def test_a_download_that_does_not_match_its_md5(self):
        blob = _zip(("Mod/assets.xml", ASSETS))
        manifest, _, _ = self.fetch(blob, mod=_mod(blob, md5="0" * 32))

        self.assertEqual(manifest["status"], "failed")
        self.assertIn("MD5", manifest["error"])
        self.assertFalse((self.dest / "files").exists())

    def test_a_file_that_is_not_a_zip(self):
        blob = b"Rar!\x1a\x07\x00" + bytes(200)
        manifest, _, _ = self.fetch(blob)

        self.assertEqual(manifest["status"], "failed")
        self.assertIn("BadZipFile", manifest["error"])


class TestWhatGetsWritten(FetcherCase):
    def test_names_that_are_unsafe_or_differ_only_by_case(self):
        blob = _zip(
            ("../escape.xml", ASSETS),
            ("C:/drive.xml", ASSETS),
            ("Mod/bad<name>.xml", ASSETS),
            ("Mod/ModInfo.json", MODINFO),
            ("mod/modinfo.json", b"{}"),
            ("MOD/MODINFO.JSON", b"[]"),
        )
        manifest, _, _ = self.fetch(blob)

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(
            sorted((s["name"], s["reason"]) for s in manifest["skipped"]),
            [("../escape.xml", "not a safe path"), ("C:/drive.xml", "not a safe path")],
        )
        self.assertEqual(
            {r["name"]: r["path"] for r in manifest["renamed"]},
            {
                "Mod/bad<name>.xml": "Mod/bad_name_.xml",
                "mod/modinfo.json": "mod/modinfo~2.json",
                "MOD/MODINFO.JSON": "MOD/MODINFO~3.JSON",
            },
        )
        self.assertEqual(self.written("Mod/ModInfo.json"), MODINFO)
        self.assertEqual(self.written("mod/modinfo~2.json"), b"{}")
        self.assertEqual(self.written("MOD/MODINFO~3.JSON"), b"[]")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["census"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "census").iterdir()), ["9"])

    def test_members_too_large_to_take(self):
        blob = _zip(("Mod/assets.xml", ASSETS), ("Mod/modinfo.json", MODINFO))
        with mock.patch.object(modio_fetch, "MAX_MEMBER", len(MODINFO)):
            manifest, _, _ = self.fetch(blob)

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["skipped"], [{"name": "Mod/assets.xml", "reason": "too large"}])
        self.assertEqual(self.written("Mod/modinfo.json"), MODINFO)

    def test_an_archive_inside_the_mod_is_listed_not_opened(self):
        inner = _zip(("assets.xml", ASSETS))
        manifest, _, _ = self.fetch(_zip(("Mod/assets.xml", ASSETS), ("Mod/extra.zip", inner)))

        self.assertEqual(manifest["nested_archives"], ["Mod/extra.zip"])
        self.assertEqual([e["name"] for e in manifest["extracted"]], ["Mod/assets.xml"])

    def test_a_flagged_mod_or_one_without_a_file_is_not_requested(self):
        blob = _zip(("Mod/assets.xml", ASSETS))
        manifest, _, cdn = self.fetch(blob, mod=_mod(blob, flagged=1))
        self.assertEqual((manifest["status"], cdn.calls), ("flagged", []))

        mod = _mod(blob)
        mod["modfile"] = None
        manifest, _, cdn = self.fetch(blob, mod=mod, refetch=True)
        self.assertEqual((manifest["status"], cdn.calls), ("no-file", []))


class TestFetchingAgain(FetcherCase):
    def test_a_mod_already_fetched_is_left_alone(self):
        blob = _zip(("Mod/assets.xml", ASSETS))
        self.fetch(blob)
        manifest, fetched, cdn = self.fetch(blob)

        self.assertFalse(fetched)
        self.assertEqual(manifest["status"], "ok")
        self.assertEqual(cdn.calls, [])

    def test_a_new_file_for_the_mod_is_fetched(self):
        old = _zip(("Mod/assets.xml", b"<ModOps/>"), ("Mod/gone.xml", b"<ModOps/>"))
        self.fetch(old)
        new = _zip(("Mod/assets.xml", ASSETS))
        _, fetched, _ = self.fetch(new, mod=_mod(new, modfile_id=91))

        self.assertTrue(fetched)
        self.assertEqual(self.written("Mod/assets.xml"), ASSETS)
        self.assertFalse((self.dest / "files" / "Mod/gone.xml").exists())
        self.assertFalse(self.dest.with_name("9.partial").exists())

    def test_a_failed_fetch_is_tried_again(self):
        blob = _zip(("Mod/assets.xml", ASSETS))
        self.fetch(blob, mod=_mod(blob, md5="0" * 32))
        manifest, fetched, _ = self.fetch(blob)

        self.assertTrue(fetched)
        self.assertEqual(manifest["status"], "ok")


class TestTheParts(unittest.TestCase):
    def test_safe_path(self):
        cases = {
            "Mod/data/assets.xml": "Mod/data/assets.xml",
            "Mod\\data\\assets.xml": "Mod/data/assets.xml",
            "/Mod/./assets.xml": "Mod/assets.xml",
            "Mod/a:b?.xml": "Mod/a_b_.xml",
            "Mod/CON.xml": "Mod/_CON.xml",
            "Mod/trailing. /x.xml": "Mod/trailing/x.xml",
            "Mod/../../x.xml": None,
            "c:x.xml": None,
            "": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(modio_fetch.safe_path(name), expected)

    def test_merge_spans(self):
        gap = modio_fetch.MERGE_GAP
        far = 200 + gap + 1
        self.assertEqual(
            modio_fetch.merge_spans([(far, far + 100), (0, 100), (50, 200), (far + 50, far + 60)]),
            [(0, 200), (far, far + 100)],
        )
        self.assertEqual(modio_fetch.merge_spans([(0, 10), (10 + gap, 20 + gap)]), [(0, 20 + gap)])

    def test_multipart_data_containing_the_boundary(self):
        data = b"xx\r\n--B\r\nContent-Range: bytes 0-1/9\r\n\r\nyy"
        body = (
            b"\r\n--B\r\nContent-Range: bytes 3-" + str(2 + len(data)).encode() + b"/99\r\n\r\n"
            + data
            + b"\r\n--B\r\nContent-Range: bytes 90-91/99\r\n\r\nzz\r\n--B--\r\n"
        )
        self.assertEqual(
            modio_fetch.parse_multipart(body, 'multipart/byteranges; boundary="B"'),
            [(3, 99, data), (90, 99, b"zz")],
        )

    def test_multipart_that_arrived_short(self):
        body = b"\r\n--B\r\nContent-Range: bytes 0-99/200\r\n\r\nshort"
        with self.assertRaises(modio_fetch.FetchError):
            modio_fetch.parse_multipart(body, "multipart/byteranges; boundary=B")


class Environment(unittest.TestCase):
    """The key and the census folder come from the environment, not a key file."""

    def run_main(self, env, *argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(modio_fetch, "find_games", return_value=[]), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
            return modio_fetch.main(list(argv)), out.getvalue()

    def test_key_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"MODIO_API_KEY": " abc123\n"}, clear=True):
            self.assertEqual(modio_fetch._read_key(), "abc123")

    def test_no_key_or_not_a_single_one(self):
        for env in ({}, {"MODIO_API_KEY": ""}, {"MODIO_API_KEY": "abc 123"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(modio_fetch.ModioError) as caught:
                    modio_fetch._read_key()
                self.assertNotIn("123", str(caught.exception))

    def test_census_goes_under_modio_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out = self.run_main({"MODIO_API_KEY": "k", "MODIO_DIR": folder})
        self.assertEqual(code, 0)
        self.assertIn(str(Path(folder) / "census"), out)

    def test_root_wins_over_modio_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {"MODIO_API_KEY": "k", "MODIO_DIR": str(ROOT)}
            code, out = self.run_main(env, "--root", folder)
        self.assertEqual(code, 0)
        self.assertIn(str(Path(folder) / "census"), out)

    def test_no_folder_given_anywhere(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main({"MODIO_API_KEY": "k"})
        self.assertEqual(caught.exception.code, 2)

    def test_refuses_a_folder_inside_the_repository(self):
        code, _ = self.run_main({"MODIO_API_KEY": "k", "MODIO_DIR": str(ROOT)})
        self.assertEqual(code, 2)


class TestTheApiClient(unittest.TestCase):
    """The census reads mod.io through the Studio's own client, not a copy of it."""

    def client(self, answers):
        self.urls, self.sleeps, self.now = [], [], [0.0]

        def fetch(url, _headers):
            self.urls.append(url)
            return answers.pop(0) if len(answers) > 1 else answers[0]

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now[0] += seconds

        return modio_fetch.Client("secretkey", fetch=fetch, sleep=sleep, clock=lambda: self.now[0])

    def test_gives_up_as_the_studio_does(self):
        client = self.client([modio.Response(503, {}, b"")])
        with self.assertRaises(modio.ModioError):
            client.get("/games")
        self.assertEqual(len(self.urls), modio.MAX_ATTEMPTS)
        # No wait after the last attempt: nothing comes after it.
        backoff = [s for s in self.sleeps if s > modio.MIN_INTERVAL]
        self.assertEqual(backoff, [2.0, 4.0, 8.0][: modio.MAX_ATTEMPTS - 1])

    def test_an_answer_that_is_not_json_is_a_modio_error(self):
        client = self.client([modio.Response(200, {}, b"<html>not json</html>")])
        with self.assertRaises(modio.ModioError):
            client.get("/games")

    def test_a_rejected_key_is_said_so(self):
        body = b'{"error": {"message": "bad secretkey"}}'
        client = self.client([modio.Response(401, {}, body)])
        with self.assertRaises(modio.KeyRejected) as caught:
            client.get("/games")
        self.assertNotIn("secretkey", str(caught.exception))

    def test_resolving_a_link_waits_its_turn_with_the_api_calls(self):
        client = self.client([modio.Response(200, {}, b"{}")])
        client.get("/games")
        self.sleeps.clear()
        client.wait_turn()
        self.assertEqual(self.sleeps, [modio.MIN_INTERVAL])

    def test_the_run_catches_what_the_client_raises(self):
        self.assertIs(modio_fetch.ModioError, modio.ModioError)


class TestWhatTheRunSays(unittest.TestCase):
    """Mod names come in every script there is, and the run prints each."""

    def test_a_name_the_output_cannot_encode_does_not_end_the_run(self):
        # What Windows gives a run whose output is piped or redirected.
        raw = io.BytesIO()
        piped = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
        mods = [{"id": 1, "name": "建筑扩展"}, {"id": 2, "name": "Plain"}]
        manifest = {"status": "ok", "extracted": [], "bytes_received": 0, "filesize": 0,
                    "error": None}

        def fetch_mod(mod, *_args, **_kwargs):
            return {**manifest, "name": mod["name"]}, True

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {"MODIO_API_KEY": "k"}, clear=True), \
                mock.patch.object(modio_fetch, "find_games",
                                  return_value=[{"name": "Anno", "name_id": "anno"}]), \
                mock.patch.object(modio_fetch, "list_mods", return_value=mods), \
                mock.patch.object(modio_fetch, "fetch_mod", fetch_mod), \
                mock.patch("sys.stdout", piped), mock.patch("sys.stderr", io.StringIO()):
            code = modio_fetch.main(["--root", folder])
            piped.flush()
        said = raw.getvalue().decode("cp1252")
        self.assertEqual(code, 0)
        self.assertIn("[1/2] ok", said)
        self.assertIn("[2/2] ok", said)
        self.assertIn("Plain", said)

    def test_a_mod_that_cannot_be_stored_ends_the_run_with_a_message(self):
        # A full disk, or a folder Windows holds locked, fails outside the
        # download: it used to end the run with a traceback.
        mods = [{"id": 1, "name": "Locked"}, {"id": 2, "name": "Never reached"}]
        calls = []

        def fetch_mod(mod, *_args, **_kwargs):
            calls.append(mod["id"])
            raise PermissionError(13, "Access is denied", "census/anno/1")

        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {"MODIO_API_KEY": "k"}, clear=True), \
                mock.patch.object(modio_fetch, "find_games",
                                  return_value=[{"name": "Anno", "name_id": "anno"}]), \
                mock.patch.object(modio_fetch, "list_mods", return_value=mods), \
                mock.patch.object(modio_fetch, "fetch_mod", fetch_mod), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = modio_fetch.main(["--root", folder])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [1])
        self.assertIn("Locked", err.getvalue())
        self.assertIn("Access is denied", err.getvalue())


if __name__ == "__main__":
    unittest.main()
