"""Search answers kept between launches (#217).

What a search expression matches in the game's own file is written beside
the index and read back the next time it is opened -- unless the rule that
answers an expression has changed since, or the index was built again.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core import places  # noqa: E402

ASSETS = (
    "<AssetList><Groups><Group><Name>outer</Name><Assets>"
    "<Asset><Template>Farm</Template><Values><Standard><GUID>1</GUID><Name>one</Name></Standard>"
    "<Building><BuildingType>Farm</BuildingType></Building></Values></Asset>"
    "<Asset><Template>Mine</Template><Values><Standard><GUID>2</GUID><Name>two</Name></Standard>"
    "</Values></Asset>"
    "</Assets></Group></Groups></AssetList>"
)
SEARCH = "//Values/Building[BuildingType='Farm']"


class KeptSearchesCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="ams-kept-searches-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.source = self.folder / "assets.xml"
        self.source.write_text(ASSETS, encoding="utf-8")
        self.db = self.folder / "assets.sqlite"
        index_mod.build_index(self.source, self.db)

    def open(self, **kwargs):
        index = index_mod.AssetIndex(self.db, self.source, **kwargs)
        self.addCleanup(index.close)
        return index

    def swept(self, index, paths):
        """``(answers, whether it swept the file for them)``."""
        seen = []
        found = index.find_matching(paths, progress=lambda n, t: seen.append((n, t)))
        return found, seen != [(1, 1)]


class TheAnswersOutlastTheIndex(KeptSearchesCase):
    def test_the_next_index_opened_has_the_answers_and_no_sweep_to_make(self):
        found, swept = self.swept(self.open(), [SEARCH])
        self.assertTrue(swept)
        self.assertEqual(found[SEARCH], ["1"])
        self.assertTrue(index_mod.searches_path_for(self.db).exists())

        again = self.open()
        self.assertEqual(again.kept_searches(), {SEARCH: ["1"]})
        found, swept = self.swept(again, [SEARCH])
        self.assertFalse(swept)
        self.assertEqual(found[SEARCH], ["1"])

    def test_an_answer_that_matched_nothing_is_kept_as_well(self):
        self.open().find_matching(["//Values/Nothing"])
        self.assertEqual(self.open().kept_searches(), {"//Values/Nothing": []})

    def test_a_sweep_stopped_half_way_writes_nothing(self):
        self.open().find_matching([SEARCH], should_cancel=lambda: True)
        self.assertFalse(index_mod.searches_path_for(self.db).exists())

    def test_answers_handed_over_by_the_reading_are_written(self):
        self.open().keep_searches({SEARCH: ["1"]})
        self.assertEqual(self.open().kept_searches(), {SEARCH: ["1"]})

    def test_answers_it_had_already_are_not_written_again(self):
        index = self.open()
        index.find_matching([SEARCH])
        with mock.patch.object(index, "_write_searches") as write:
            index.keep_searches({SEARCH: ["1"]})
            index.find_matching([SEARCH])
        write.assert_not_called()

    def test_a_read_only_index_neither_reads_nor_writes_them(self):
        self.open().find_matching([SEARCH])
        kept = index_mod.searches_path_for(self.db)
        before = kept.read_bytes()

        reading = self.open(read_only=True)
        self.assertEqual(reading.kept_searches(), {})
        reading.find_matching(["//Values/Standard"])
        reading.keep_searches({"//Values/Other": []})
        self.assertEqual(kept.read_bytes(), before)


class WhatTheyAreKeyedBy(KeptSearchesCase):
    def setUp(self):
        super().setUp()
        index = index_mod.AssetIndex(self.db, self.source)
        index.find_matching([SEARCH])
        index.close()

    def test_another_rule_does_not_read_them(self):
        with mock.patch.object(index_mod, "search_rule", return_value="another"):
            self.assertEqual(self.open().kept_searches(), {})

    def test_the_rule_names_the_studio_s_version(self):
        self.assertTrue(index_mod.search_rule().startswith(__version__ + "|"))

    def test_from_source_the_rule_moves_with_the_file_it_is_written_in(self):
        here = index_mod.search_rule()
        real = Path.read_bytes

        def edited(path):
            data = real(path)
            return data + b"#" if Path(path) == Path(index_mod.__file__) else data

        with mock.patch.object(Path, "read_bytes", edited):
            self.assertNotEqual(index_mod.search_rule(), here)

    def test_a_built_studio_with_no_source_is_its_version(self):
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("frozen")):
            rule = index_mod.search_rule()
        self.assertTrue(rule.startswith(__version__ + "|lxml "))
        self.assertEqual(rule.count("|"), 1)

    def test_an_index_built_again_does_not_have_them(self):
        index_mod.build_index(self.source, self.db)
        self.assertFalse(index_mod.searches_path_for(self.db).exists())
        self.assertEqual(self.open().kept_searches(), {})

    def test_answers_from_another_build_at_the_same_place_are_not_read(self):
        kept = index_mod.searches_path_for(self.db)
        data = json.loads(kept.read_text(encoding="utf-8"))
        data["index"] = "0"
        kept.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.open().kept_searches(), {})

    def test_a_file_that_is_not_whole_is_none(self):
        kept = index_mod.searches_path_for(self.db)
        for text in ("{", "[]", '{"rule": 1}', ""):
            with self.subTest(text=text):
                kept.write_text(text, encoding="utf-8")
                self.assertEqual(self.open().kept_searches(), {})


class PruningThem(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="ams-kept-searches-cache-")
        self.addCleanup(shutil.rmtree, folder, True)
        patched = mock.patch.dict("os.environ", {"ANNO_STUDIO_CACHE": folder})
        patched.start()
        self.addCleanup(patched.stop)
        self.cache = places.cache_dir()
        self.assertEqual(self.cache, Path(folder))

    def _index(self, name, version):
        import sqlite3

        path = self.cache / name
        con = sqlite3.connect(str(path))
        con.execute("create table meta (key text, value text)")
        con.execute("insert into meta values ('schema_version', ?)", (str(version),))
        con.commit()
        con.close()
        index_mod.searches_path_for(path).write_text("{}", encoding="utf-8")
        return path

    def test_they_go_with_their_index_and_stay_with_it(self):
        current = self._index("a.sqlite", index_mod.SCHEMA_VERSION)
        older = self._index("b.sqlite", index_mod.SCHEMA_VERSION - 1)
        orphan = self.cache / ("c" + index_mod.SEARCHES_SUFFIX)
        orphan.write_text("{}", encoding="utf-8")

        self.assertEqual(index_mod.prune_cache(), 2)

        self.assertTrue(index_mod.searches_path_for(current).exists())
        self.assertFalse(older.exists())
        self.assertFalse(index_mod.searches_path_for(older).exists())
        self.assertFalse(orphan.exists())

    def test_one_left_half_written_goes_once_it_is_old(self):
        import os
        import time

        self._index("d.sqlite", index_mod.SCHEMA_VERSION)
        writing = self.cache / ("d" + index_mod.SEARCHES_SUFFIX + ".1-2.writing")
        writing.write_text("{", encoding="utf-8")
        index_mod.prune_cache()
        self.assertTrue(writing.exists())
        os.utime(writing, (time.time() - 2 * index_mod.STALE_BUILDING_SECONDS,) * 2)
        index_mod.prune_cache()
        self.assertFalse(writing.exists())


if __name__ == "__main__":
    unittest.main()
