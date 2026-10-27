"""RDA archive reading, and resolving game files across patch archives.

Most of these run against a synthetic archive built by ``_build_archive`` so they
work anywhere.  The tests at the bottom run against a real Anno install, and
fail without one (support.missing says where they skip instead).
"""

from __future__ import annotations

import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import missing  # noqa: E402

from anno_modding_studio.core import discovery, gamefiles, games, rda  # noqa: E402


def _build_archive(path: Path, blocks):
    """Write a minimal but real V2.2 archive.

    ``blocks`` is a list of ``(flags, [(name, payload_bytes), ...])``.  Payloads
    are laid out first, then each block's directory, then its 32-byte header,
    exactly as the game writes them.
    """
    out = bytearray(b"\x00" * rda.HEADER_SIZE)
    out[: len(rda.MAGIC_V22)] = rda.MAGIC_V22

    block_headers = []
    for flags, files in blocks:
        entries = bytearray()
        for name, payload in files:
            stored = zlib.compress(payload) if flags & rda.FLAG_COMPRESSED else payload
            offset = len(out)
            out += stored
            name_bytes = name.encode("utf-16-le")
            name_bytes += b"\x00" * (rda.NAME_BYTES - len(name_bytes))
            entries += name_bytes
            entries += struct.pack(
                "<QQQQQ", offset, len(stored), len(payload), 0, 0
            )
        directory = bytes(entries)
        if flags & rda.FLAG_COMPRESSED:
            directory = zlib.compress(directory)
        out += directory
        block_headers.append((len(out), flags, len(files), len(directory), len(entries)))
        out += b"\x00" * rda.BLOCK_HEADER_SIZE

    # Backfill each header now that we know where the next block starts.
    for i, (offset, flags, count, dir_size, dir_dec) in enumerate(block_headers):
        nxt = block_headers[i + 1][0] if i + 1 < len(block_headers) else len(out)
        struct.pack_into("<II", out, offset, flags, count)
        struct.pack_into("<QQQ", out, offset + 8, dir_size, dir_dec, nxt)

    struct.pack_into("<Q", out, rda.FIRST_BLOCK_OFFSET_POS, block_headers[0][0])
    path.write_bytes(bytes(out))
    return path


def _blocks(path: Path):
    """``(header offset, flags, count, dir_size)`` per block, as the reader walks them.

    Used to corrupt one block's directory without disturbing the rest of the
    file, which is what a truncated download or a half-copied game folder
    looks like from here.
    """
    raw = path.read_bytes()
    found = []
    offset = struct.unpack_from("<Q", raw, rda.FIRST_BLOCK_OFFSET_POS)[0]
    seen = set()
    while offset and offset not in seen and offset + rda.BLOCK_HEADER_SIZE <= len(raw):
        seen.add(offset)
        flags, count = struct.unpack_from("<II", raw, offset)
        dir_size, _dec, nxt = struct.unpack_from("<QQQ", raw, offset + 8)
        found.append((offset, flags, count, dir_size))
        offset = nxt
    return found


class TestADamagedArchive(unittest.TestCase):
    """What a corrupt archive does, which is not the same as what a missing one does.

    An archive that will not decompress is an ordinary thing to meet -- a
    download that stopped short, a game folder copied while the game was
    running. The two branches behave differently on purpose and neither was
    exercised.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_payload_that_will_not_decompress_is_reported(self):
        """Reading a damaged file says so rather than handing back nonsense."""
        path = _build_archive(
            self.tmp / "payload.rda",
            [(rda.FLAG_COMPRESSED, [("data/one.xml", b"<one/>" * 40)])],
        )
        with rda.RdaArchive(path) as archive:
            entry = next(iter(archive.entries()))
            where, length = entry.offset, entry.compressed_size
        raw = bytearray(path.read_bytes())
        raw[where : where + length] = b"\xff" * length
        path.write_bytes(bytes(raw))

        with rda.RdaArchive(path) as archive:
            entry = next(iter(archive.entries()))
            with self.assertRaises(rda.RdaError) as caught:
                archive.read(entry)
        self.assertIn("data/one.xml", str(caught.exception))

    def test_a_damaged_directory_costs_its_own_block_and_no_more(self):
        """The other branch, and the quieter one.

        A directory that will not decompress is skipped: the block's files
        simply are not there, with nothing said. That is deliberate -- one bad
        block must not cost the whole archive -- but it does mean a file can go
        missing without a word, so it is worth a test that says as much.
        """
        path = _build_archive(
            self.tmp / "dir.rda",
            [
                (rda.FLAG_COMPRESSED, [("data/good.xml", b"<good/>")]),
                (rda.FLAG_COMPRESSED, [("data/lost.xml", b"<lost/>")]),
            ],
        )
        header, _flags, _count, dir_size = _blocks(path)[-1]
        raw = bytearray(path.read_bytes())
        raw[header - dir_size : header] = b"\xff" * dir_size
        path.write_bytes(bytes(raw))

        with rda.RdaArchive(path) as archive:
            names = [entry.name for entry in archive.entries()]
        self.assertEqual(names, ["data/good.xml"])


class TestRdaFormat(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_an_uncompressed_archive(self):
        path = _build_archive(
            self.tmp / "a.rda",
            [(0, [("data/one.xml", b"<one/>"), ("data/two.xml", b"<two/>")])],
        )
        with rda.RdaArchive(path) as archive:
            entries = {e.name: e for e in archive.entries()}
            self.assertEqual(set(entries), {"data/one.xml", "data/two.xml"})
            self.assertEqual(archive.read(entries["data/one.xml"]), b"<one/>")
            self.assertEqual(entries["data/two.xml"].size, 6)

    def test_reads_a_compressed_archive(self):
        payload = b"<assets>" + b"x" * 5000 + b"</assets>"
        path = _build_archive(
            self.tmp / "c.rda", [(rda.FLAG_COMPRESSED, [("data/big.xml", payload)])]
        )
        with rda.RdaArchive(path) as archive:
            entry = archive.find("data/big.xml")
            self.assertIsNotNone(entry)
            self.assertTrue(entry.is_compressed)
            self.assertLess(entry.compressed_size, len(payload))
            self.assertEqual(archive.read(entry), payload)

    def test_walks_every_block(self):
        path = _build_archive(
            self.tmp / "m.rda",
            [
                (0, [("data/a.xml", b"a")]),
                (rda.FLAG_COMPRESSED, [("data/b.xml", b"b" * 200)]),
                (0, [("data/c.xml", b"c")]),
            ],
        )
        with rda.RdaArchive(path) as archive:
            names = sorted(e.name for e in archive.entries())
        self.assertEqual(names, ["data/a.xml", "data/b.xml", "data/c.xml"])

    def test_encrypted_blocks_are_skipped_not_decoded_as_garbage(self):
        path = _build_archive(
            self.tmp / "e.rda",
            [(0, [("data/plain.xml", b"ok")]), (rda.FLAG_ENCRYPTED, [("x", b"secret")])],
        )
        with rda.RdaArchive(path) as archive:
            names = [e.name for e in archive.entries()]
            self.assertEqual(names, ["data/plain.xml"])
            self.assertEqual(archive.encrypted_blocks, 1)

    def test_rejects_a_non_archive(self):
        path = self.tmp / "nope.rda"
        path.write_bytes(b"not an archive at all" + b"\x00" * 2000)
        with rda.RdaArchive(path) as archive:
            with self.assertRaises(rda.RdaError):
                list(archive.entries())

    def test_extract_writes_the_file(self):
        path = _build_archive(
            self.tmp / "x.rda", [(0, [("data/config/assets.xml", b"<Assets/>")])]
        )
        with rda.RdaArchive(path) as archive:
            entry = archive.find("data/config/assets.xml")
            target = archive.extract(entry, self.tmp / "out" / "assets.xml")
        self.assertEqual(target.read_bytes(), b"<Assets/>")

    def test_is_rda_sniff(self):
        good = _build_archive(self.tmp / "g.rda", [(0, [("a", b"1")])])
        bad = self.tmp / "b.rda"
        bad.write_bytes(b"hello")
        self.assertTrue(rda.is_rda(good))
        self.assertFalse(rda.is_rda(bad))


class TestLoadOrder(unittest.TestCase):
    def test_natural_sort_puts_data9_before_data33(self):
        """Lexicographic order would pick a stale archive."""
        names = ["data33.rda", "data9.rda", "data0.rda", "data10.rda", "data2.rda"]
        ordered = sorted(names, key=rda.natural_key)
        self.assertEqual(
            ordered, ["data0.rda", "data2.rda", "data9.rda", "data10.rda", "data33.rda"]
        )

    def test_patch_archives_sort_last(self):
        names = ["zz_patchfiles_200.rda", "config.rda", "zz_patchfiles_150.rda"]
        self.assertEqual(
            sorted(names, key=rda.natural_key),
            ["config.rda", "zz_patchfiles_150.rda", "zz_patchfiles_200.rda"],
        )


class TestResolveGameFiles(unittest.TestCase):
    ASSET = "data/config/export/main/asset/assets.xml"
    TEXTS = "data/config/gui/texts_english.xml"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.maindata = self.tmp / "maindata"
        self.maindata.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_latest_archive_wins(self):
        _build_archive(self.maindata / "data0.rda", [(0, [(self.ASSET, b"<old/>")])])
        _build_archive(self.maindata / "data9.rda", [(0, [(self.ASSET, b"<mid/>")])])
        _build_archive(self.maindata / "data33.rda", [(0, [(self.ASSET, b"<new/>")])])

        result = gamefiles.scan_game_files(self.tmp)
        best = result.best_assets
        self.assertEqual(best.archive.name, "data33.rda")
        self.assertEqual(result.archives_scanned, 3)

        with rda.RdaArchive(best.archive) as archive:
            self.assertEqual(archive.read(best.entry), b"<new/>")

    def test_counts_the_stale_copies_it_skipped(self):
        """The count is how the UI justifies not using data0.rda."""
        for name in ("data0.rda", "data9.rda", "data33.rda"):
            _build_archive(self.maindata / name, [(0, [(self.ASSET, b"<x/>")])])
        result = gamefiles.scan_game_files(self.tmp)
        self.assertEqual(result.superseded, 2)
        self.assertEqual(result.best_assets.archive.name, "data33.rda")

    def test_accepts_the_maindata_folder_directly(self):
        _build_archive(self.maindata / "config.rda", [(0, [(self.ASSET, b"<a/>")])])
        self.assertEqual(
            gamefiles.scan_game_files(self.maindata).best_assets.archive.name, "config.rda"
        )

    def test_finds_texts_and_lists_languages(self):
        _build_archive(
            self.maindata / "data1.rda",
            [
                (
                    0,
                    [
                        (self.ASSET, b"<a/>"),
                        (self.TEXTS, b"<TextExport/>"),
                        ("data/config/gui/texts_german.xml", b"<TextExport/>"),
                        ("data/config/gui/texts_metadata.xml", b"<meta/>"),
                    ],
                )
            ],
        )
        result = gamefiles.scan_game_files(self.tmp)
        self.assertEqual(result.languages, ["English", "German"])
        self.assertIsNotNone(result.texts_for("English"))
        # texts_metadata.xml matches the glob but is not a translation.
        self.assertNotIn("Metadata", result.languages)

    def test_a_tombstone_removes_a_file_added_earlier(self):
        _build_archive(self.maindata / "data1.rda", [(0, [(self.ASSET, b"<a/>")])])
        _build_archive(self.maindata / "data2.rda", [(0, [(self.ASSET, b"")])])
        result = gamefiles.scan_game_files(self.tmp)
        self.assertIsNone(result.best_assets)

    def test_detects_the_game_from_the_in_archive_path(self):
        _build_archive(
            self.maindata / "config.rda", [(0, [("data/base/config/export/assets.xml", b"<a/>")])]
        )
        self.assertEqual(gamefiles.scan_game_files(self.tmp).best_assets.game, games.ANNO_117)

    def test_extract_preserves_the_in_archive_path(self):
        _build_archive(self.maindata / "data1.rda", [(0, [(self.ASSET, b"<Assets/>")])])
        found = gamefiles.scan_game_files(self.tmp).best_assets
        out = gamefiles.extract(found, self.tmp / "extracted")
        self.assertEqual(out, self.tmp / "extracted" / self.ASSET)
        self.assertEqual(out.read_bytes(), b"<Assets/>")
        # The path is what tells the Studio which game this is.
        self.assertEqual(games.detect_from_path(out), games.ANNO_1800)

    def test_unreadable_archive_is_reported_not_fatal(self):
        _build_archive(self.maindata / "data1.rda", [(0, [(self.ASSET, b"<a/>")])])
        (self.maindata / "broken.rda").write_bytes(b"garbage" * 500)
        result = gamefiles.scan_game_files(self.tmp)
        self.assertIsNotNone(result.best_assets)
        self.assertEqual(len(result.archives_failed), 1)

    def test_folder_without_archives(self):
        self.assertIsNone(gamefiles.maindata_folder(self.tmp))


class TestWhatWasReadFromTheGame(unittest.TestCase):
    """Extraction is the slow half of loading Anno 1800, and it was paid every
    time -- for a file the game only changes every few weeks.

    So a read writes down what it took and what the installation looked like at
    the time.  A patch rewrites archives, so comparing that against the game
    now answers "would reading it again change anything?" in a few stat calls.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ams-read-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.game = self.tmp / "Anno 1800" / "maindata"
        self.game.mkdir(parents=True)
        for name in ("data0.rda", "data1.rda"):
            (self.game / name).write_bytes(name.encode("ascii"))
        self.extracted = self.tmp / "extracted"
        self.extracted.mkdir()
        self.assets = self.extracted / "assets.xml"
        self.assets.write_text("<AssetList><Assets/></AssetList>", encoding="utf-8")

    def stamp(self):
        from anno_modding_studio.core import games

        gamefiles.record_read(
            self.extracted,
            game=games.ANNO_1800,
            game_folder=self.game.parent,
            assets=self.assets,
            source_archive="data1.rda",
        )
        return gamefiles.read_stamp(self.extracted)

    def test_a_read_records_what_it_took_and_out_of_what(self):
        stamp = self.stamp()
        self.assertEqual(stamp["source_archive"], "data1.rda")
        self.assertEqual(Path(stamp["assets"]), self.assets)
        self.assertEqual(
            [mark[0] for mark in stamp["archives"]], ["data0.rda", "data1.rda"]
        )

    def test_an_unchanged_game_has_nothing_to_re_read(self):
        state, why = gamefiles.read_state(self.stamp())
        self.assertEqual(state, gamefiles.READ_CURRENT)
        self.assertIn("has not changed", why)

    def test_a_patch_that_adds_an_archive_says_so(self):
        stamp = self.stamp()
        (self.game / "data2.rda").write_bytes(b"a patch")
        state, why = gamefiles.read_state(stamp)
        self.assertEqual(state, gamefiles.READ_PATCHED)
        self.assertIn("changed since", why)

    def test_and_so_does_one_that_merely_rewrites_an_archive(self):
        stamp = self.stamp()
        (self.game / "data1.rda").write_bytes(b"data1.rda, but bigger")
        self.assertEqual(gamefiles.read_state(stamp)[0], gamefiles.READ_PATCHED)

    def test_extracted_files_that_have_gone_are_an_ordinary_first_run(self):
        stamp = self.stamp()
        self.assets.unlink()
        self.assertEqual(gamefiles.read_state(stamp)[0], gamefiles.READ_GONE)

    def test_and_so_is_never_having_read_anything(self):
        self.assertIsNone(gamefiles.read_stamp(self.extracted))
        self.assertEqual(gamefiles.read_state(None)[0], gamefiles.READ_GONE)


class TestRealInstall(unittest.TestCase):
    """Against the games installed here, which a machine changing the Studio
    has (decision 5.5): without one, this fails and says so."""

    @classmethod
    def setUpClass(cls):
        cls.installs = [i for i in discovery.find_game_installs() if i.has_archives]
        if not cls.installs:
            missing("Anno 1800 or Anno 117 installed")
        # Scanned once, for both tests: each scan reads every archive's
        # directory, and the second test used to scan the first game again.
        cls.scans = {install.name: gamefiles.scan_game_files(install.path) for install in cls.installs}

    def test_scan_finds_a_current_assets_file(self):
        for install in self.installs:
            with self.subTest(game=install.name):
                result = self.scans[install.name]
                best = result.best_assets
                self.assertIsNotNone(best, "no assets.xml in %s" % install.name)
                self.assertGreater(best.size, 1_000_000)
                self.assertIn(best.game, (games.ANNO_1800, games.ANNO_117))

    def test_extracted_bytes_parse_as_assets(self):
        from lxml import etree

        install = self.installs[0]
        best = self.scans[install.name].best_assets
        with rda.RdaArchive(best.archive) as archive:
            data = archive.read(best.entry)
        self.assertEqual(len(data), best.size)
        root = etree.fromstring(data, etree.XMLParser(huge_tree=True, recover=True))
        self.assertIsNotNone(root)
        self.assertGreater(data.count(b"<Asset>"), 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
