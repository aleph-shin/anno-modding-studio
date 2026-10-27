"""Versions: read, moved on, adopted, and checked against what is live."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import versioning  # noqa: E402
from anno_modding_studio.core.versioning import MAJOR, MINOR, PATCH  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_one_to_three_numbers(self):
        self.assertEqual(versioning.parse("2"), (2, 0, 0))
        self.assertEqual(versioning.parse("1.0"), (1, 0, 0))
        self.assertEqual(versioning.parse(" 1.4.12 "), (1, 4, 12))

    def test_anything_else_is_not_numbered(self):
        for text in ("", "v1.2", "1.4b", "1.2.3.4", "2026-09-28", "1..2", "beta"):
            self.assertIsNone(versioning.parse(text), text)


class KeyTest(unittest.TestCase):
    def test_every_number_in_order(self):
        self.assertEqual(versioning.key("1.6.3"), (1, 6, 3))
        self.assertEqual(versioning.key("v2"), (2,))
        self.assertEqual(versioning.key("1.4b"), (1, 4))

    def test_ten_after_nine(self):
        self.assertGreater(versioning.key("1.10"), versioning.key("1.9"))

    def test_a_version_with_no_numbers_has_an_empty_key_that_orders_first(self):
        self.assertEqual(versioning.key("beta"), ())
        self.assertEqual(versioning.key(None), ())
        self.assertLess(versioning.key("beta"), versioning.key("0.1"))

    def test_trimmed_drops_the_zeros_at_the_end(self):
        self.assertEqual(versioning.trimmed("1.0"), versioning.trimmed("1.0.0"))
        self.assertEqual(versioning.trimmed("1.0.10"), (1, 0, 10))
        self.assertGreater(versioning.trimmed("1.0.1"), versioning.trimmed("1.0"))


class TrimmedTest(unittest.TestCase):
    def test_zeros_at_the_end_are_the_same_version(self):
        self.assertEqual(versioning.trimmed("1.0"), versioning.trimmed("1.0.0"))
        self.assertEqual(versioning.trimmed("1.3.0"), (1, 3))
        self.assertGreater(versioning.trimmed("1.0.1"), versioning.trimmed("1.0"))

    def test_a_version_with_no_numbers_and_nought_are_told_apart_by_key(self):
        self.assertEqual(versioning.trimmed("0.0"), ())
        self.assertEqual(versioning.key("beta"), ())
        self.assertNotEqual(versioning.key("0.0"), ())


class AsTheLoaderReadsTest(unittest.TestCase):
    """The mod loader's own reading, from its source: three unsigned parts."""

    def test_the_first_three_parts_leading_digits_only(self):
        self.assertEqual(versioning.as_the_loader_reads("1.4b"), (1, 4, 0))
        self.assertEqual(versioning.as_the_loader_reads("25.1.0.7"), (25, 1, 0))
        self.assertEqual(versioning.as_the_loader_reads("1.0421"), (1, 421, 0))

    def test_what_is_not_a_number_is_nought(self):
        self.assertEqual(versioning.as_the_loader_reads("v2"), (0, 0, 0))
        self.assertEqual(versioning.as_the_loader_reads("-1.2"), (0, 2, 0))
        self.assertEqual(versioning.as_the_loader_reads(""), (0, 0, 0))
        self.assertEqual(versioning.as_the_loader_reads("99999999999.1"), (0, 1, 0))

    def test_empty_parts_are_skipped(self):
        self.assertEqual(versioning.as_the_loader_reads("1..2"), (1, 2, 0))


class BumpTest(unittest.TestCase):
    def test_each_level(self):
        self.assertEqual(versioning.bump("1.4.2", PATCH), "1.4.3")
        self.assertEqual(versioning.bump("1.4.2", MINOR), "1.5.0")
        self.assertEqual(versioning.bump("1.4.2", MAJOR), "2.0.0")

    def test_a_short_version_takes_three_parts_on_its_first_move(self):
        self.assertEqual(versioning.bump("1.0", PATCH), "1.0.1")
        self.assertEqual(versioning.bump("1.0", MINOR), "1.1.0")
        self.assertEqual(versioning.bump("3", MAJOR), "4.0.0")

    def test_a_version_that_is_not_numbered_is_not_moved(self):
        self.assertIsNone(versioning.bump("v2", PATCH))

    def test_an_unknown_level_is_a_mistake(self):
        with self.assertRaises(ValueError):
            versioning.bump("1.0", "huge")


class AdoptedTest(unittest.TestCase):
    def test_keeps_its_own_numbers_so_it_never_goes_backwards(self):
        self.assertEqual(versioning.adopted("v2"), "2.0.0")
        self.assertEqual(versioning.adopted("1.4b"), "1.4.0")
        self.assertEqual(versioning.adopted("1.2.3.4"), "1.2.3")

    def test_starts_at_one_with_no_numbers(self):
        self.assertEqual(versioning.adopted("beta"), "1.0.0")
        self.assertEqual(versioning.adopted(""), "1.0.0")


class AgainstLiveTest(unittest.TestCase):
    def test_behind(self):
        self.assertTrue(versioning.is_behind("1.0", "1.0"))
        self.assertTrue(versioning.is_behind("Beta", "beta"))
        self.assertTrue(versioning.is_behind("1.2", "1.10"))
        self.assertFalse(versioning.is_behind("1.1", "1.0"))
        self.assertFalse(versioning.is_behind("1.0", ""))

    def test_versions_that_do_not_order_are_not_behind(self):
        self.assertFalse(versioning.is_behind("beta", "1.0"))
        self.assertFalse(versioning.is_behind("1.0", "gamma"))

    def test_the_next_is_after_whichever_is_further_on(self):
        self.assertEqual(versioning.next_after("1.0", "1.0"), "1.0.1")
        self.assertEqual(versioning.next_after("1.2", "1.10"), "1.10.1")
        self.assertEqual(versioning.next_after("1.0", "v1.3"), "1.3.1")
        self.assertEqual(versioning.next_after("1.2.3.4", "1.2.3.4"), "1.2.4")

    def test_no_next_without_numbers(self):
        self.assertIsNone(versioning.next_after("beta", "beta"))


class PublishedRecordTest(unittest.TestCase):
    def _saved(self, published):
        import json
        import tempfile

        from anno_modding_studio.core.changes import Loadout

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.annoload"
            Loadout().save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["published"] = published
            path.write_text(json.dumps(data), encoding="utf-8")
            return Loadout.load(path)

    def test_survives_a_save(self):
        record = {"version": "1.2.0", "build": 7, "when": 5.0, "modfile_id": 91, "checkpoint": 5.0}
        self.assertEqual(self._saved(record).published, record)

    def test_one_without_a_version_is_none(self):
        self.assertEqual(self._saved({"build": 7}).published, {})
        self.assertEqual(self._saved("1.2.0").published, {})


if __name__ == "__main__":
    unittest.main()
