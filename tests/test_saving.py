"""Saving several files at once: all of them, or none.

Each step of a save is made to fail in turn, and each to stop as a power cut
would, and every time the files are found as they were or as they were to
be, never some of each: in the save itself where it fails, and once the next
start has settled the journal where it stops.
"""

from __future__ import annotations

import contextlib
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

from anno_modding_studio.core import disk, records, saving  # noqa: E402
from anno_modding_studio.core.changes import Loadout, own_record  # noqa: E402

# A file written over, one made, and one taken away: a project that follows
# its mod's name to another file.
OLD = {"a.annoload": b"old a", "b.annoproj": b"old b", "c.annoproj": None, "d.annoproj": b"old d"}
NEW = {"a.annoload": b"new a", "b.annoproj": b"new b", "c.annoproj": b"new c", "d.annoproj": None}


class Crash(BaseException):
    """The Studio stopping where it stands, as a power cut stops it."""


class Steps:
    """Every step a save takes on the disk -- a file written, moved or
    deleted -- counted, and the ones in ``fail`` failing with what is there."""

    def __init__(self, fail=None):
        self.fail = dict(fail or {})
        self.taken = 0
        self._patches = []

    def __enter__(self):
        for owner, name in ((disk, "write_flushed"), (os, "replace"), (os, "unlink"), (os, "remove")):
            real = getattr(owner, name)
            patch = mock.patch.object(owner, name, side_effect=self._step(real))
            patch.start()
            self._patches.append(patch)
        return self

    def __exit__(self, *_exc):
        for patch in reversed(self._patches):
            patch.stop()

    def _step(self, real):
        def step(*args, **kwargs):
            self.taken += 1
            failure = self.fail.get(self.taken)
            if failure is not None:
                raise failure
            return real(*args, **kwargs)

        return step


class SavingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        waits = mock.patch.object(saving, "WAITS", (0.0, 0.0))
        waits.start()
        self.addCleanup(waits.stop)
        self.addCleanup(self.clear_journals)
        self.clear_journals()

    def clear_journals(self):
        for path in saving.journal_dir().glob("*"):
            path.unlink()

    def files(self, which=OLD):
        for name, data in which.items():
            path = self.root / name
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)

    def state(self) -> dict:
        return {name: (self.root / name).read_bytes() if (self.root / name).exists() else None
                for name in OLD}

    def save(self, which=NEW):
        saving.write_all({self.root / name: data for name, data in which.items()})

    def assert_nothing_beside(self):
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         sorted(name for name, data in self.state().items() if data is not None))
        self.assertEqual(list(saving.journal_dir().glob("*.json")), [])

    def steps_in_a_save(self) -> int:
        self.files()
        with Steps() as steps:
            self.save()
        self.assertEqual(self.state(), NEW)
        return steps.taken


class AllOrNothing(SavingTest):
    def test_every_file_is_saved_and_nothing_is_left_beside_them(self):
        self.files()
        self.save()
        self.assertEqual(self.state(), NEW)
        self.assert_nothing_beside()

    def test_a_failure_at_any_step_leaves_the_old_files_or_the_new(self):
        total = self.steps_in_a_save()
        for at in range(1, total + 1):
            with self.subTest(step=at):
                self.files()
                with Steps({at: OSError("disk full")}):
                    try:
                        self.save()
                    except OSError:
                        self.assertEqual(self.state(), OLD)
                    else:
                        # Only deleting what is left once the save is made.
                        self.assertEqual(self.state(), NEW)
                self.assertEqual(saving.recover(), [])
                self.assertIn(self.state(), (OLD, NEW))
                self.assertEqual(list(saving.journal_dir().glob("*.json")), [])

    def test_a_save_stopped_at_any_step_is_settled_old_or_new_at_the_next_start(self):
        total = self.steps_in_a_save()
        for at in range(1, total + 1):
            with self.subTest(step=at):
                self.files()
                with Steps({at: Crash()}), self.assertRaises(Crash):
                    self.save()
                self.assertEqual(saving.recover(), [])
                self.assertIn(self.state(), (OLD, NEW))
                self.assertEqual(list(saving.journal_dir().glob("*.json")), [])

    def test_an_undo_stopped_at_any_step_is_settled_old_or_new_at_the_next_start(self):
        total = self.steps_in_a_save()
        for failed in range(1, total + 1):
            for stopped in range(failed + 1, failed + 8):
                with self.subTest(failed=failed, stopped=stopped):
                    self.files()
                    with Steps({failed: OSError("held"), stopped: Crash()}),                             contextlib.suppress(OSError, Crash):
                        self.save()
                    self.assertEqual(saving.recover(), [])
                    self.assertIn(self.state(), (OLD, NEW))
                    self.assertEqual(list(saving.journal_dir().glob("*.json")), [])

    def test_a_move_something_holds_for_a_moment_is_tried_again(self):
        self.files()
        held = PermissionError(13, "The process cannot access the file")
        with Steps({6: held, 7: held}):
            self.save()
        self.assertEqual(self.state(), NEW)
        self.assert_nothing_beside()

    def test_one_held_for_longer_undoes_the_save(self):
        self.files()
        held = PermissionError(13, "The process cannot access the file")
        with Steps(dict.fromkeys(range(6, 6 + len(saving.WAITS) + 1), held)), \
                self.assertRaises(PermissionError):
            self.save()
        self.assertEqual(self.state(), OLD)
        self.assert_nothing_beside()

    def test_one_that_cannot_be_undone_either_is_left_for_the_next_start(self):
        self.files()
        held = OSError("held")
        with Steps(dict.fromkeys(range(6, 30), held)), self.assertRaises(saving.Unfinished) as said:
            self.save()
        self.assertEqual(len(list(saving.journal_dir().glob("*.json"))), 1)
        self.assertIn(str(saving.journal_dir()), str(said.exception))
        self.assertEqual(saving.recover(), [])
        self.assertIn(self.state(), (OLD, NEW))
        self.assertEqual(list(saving.journal_dir().glob("*.json")), [])

    def test_the_same_file_twice_is_refused_before_anything_is_written(self):
        self.files()
        with self.assertRaises(ValueError):
            saving.write_all({str(self.root / "a.annoload"): b"one",
                              str(self.root / "sub" / ".." / "a.annoload"): b"two"})
        self.assertEqual(self.state(), OLD)
        self.assert_nothing_beside()


class OneFile(SavingTest):
    def test_is_written_as_disk_writes_it_with_no_journal(self):
        path = self.root / "a.annoload"
        with mock.patch.object(saving.disk, "write_bytes") as wrote, \
                mock.patch.object(saving, "write_journal") as journal:
            saving.write_all({path: b"new a"})
        wrote.assert_called_once_with(path, b"new a")
        journal.assert_not_called()

    def test_taking_one_away_is_deleting_it_with_no_journal(self):
        self.files()
        with mock.patch.object(saving, "write_journal") as journal:
            saving.write_all({self.root / "d.annoproj": None})
            saving.write_all({self.root / "c.annoproj": None})
        journal.assert_not_called()
        self.assertIsNone(self.state()["d.annoproj"])

    def test_a_loadout_is_saved_through_it_byte_for_byte_as_before(self):
        path = self.root / "Mine.annoload"
        with mock.patch.object(saving, "write_all", wraps=saving.write_all) as through:
            Loadout(own=own_record(name="Mine")).save(path)
        through.assert_called_once()
        self.assertEqual(list(through.call_args.args[0]), [path])
        self.assertEqual(path.read_bytes(), disk.json_bytes(json.loads(path.read_text(encoding="utf-8"))))

    def test_settles_what_this_process_left_first(self):
        # A save stopped after its commit, left for a later save in the same
        # run: settled after the later one, it would overtake it.
        self.files()
        with Steps({7: Crash()}), self.assertRaises(Crash):
            self.save()
        saving.write_all({self.root / "a.annoload": b"later a"})
        self.assertEqual(self.state()["a.annoload"], b"later a")
        self.assertEqual(saving.recover(), [])
        self.assertEqual(self.state()["a.annoload"], b"later a")


class Recovery(SavingTest):
    def journal_after_commit(self) -> Path:
        """A save stopped as soon as its journal is on the disk."""
        self.files()
        with mock.patch.object(saving, "settle"):
            self.save()
        (journal,) = saving.journal_dir().glob("*.json")
        return journal

    def test_a_journal_left_after_the_commit_completes_the_save(self):
        self.journal_after_commit()
        self.assertEqual(self.state(), OLD)
        self.assertEqual(saving.recover(), [])
        self.assertEqual(self.state(), NEW)
        self.assert_nothing_beside()

    def test_a_journal_whose_parts_are_all_in_place_is_only_tidied(self):
        journal = self.journal_after_commit()
        entries, _ = saving.read_journal(journal)
        for entry in entries:
            if entry.existed:
                os.replace(entry.target, entry.old)
            if not entry.removed:
                os.replace(entry.part, entry.target)
        self.assertEqual(saving.recover(), [])
        self.assertEqual(self.state(), NEW)
        self.assert_nothing_beside()

    def test_a_journal_whose_parts_are_gone_undoes_the_save(self):
        journal = self.journal_after_commit()
        entries, _ = saving.read_journal(journal)
        for entry in entries:
            # One taken away has none.
            entry.part.unlink(missing_ok=entry.removed)
        self.assertEqual(saving.recover(), [])
        self.assertEqual(self.state(), OLD)
        self.assert_nothing_beside()

    def test_and_one_part_gone_after_its_file_was_moved_aside_puts_that_back_too(self):
        journal = self.journal_after_commit()
        entries, _ = saving.read_journal(journal)
        os.replace(entries[0].target, entries[0].old)
        entries[0].part.unlink()
        self.assertEqual(saving.recover(), [])
        self.assertEqual(self.state(), OLD)
        self.assert_nothing_beside()

    def test_a_journal_a_newer_studio_wrote_is_left_as_it_is(self):
        journal = self.journal_after_commit()
        data = json.loads(journal.read_text(encoding="utf-8"))
        data[records.FORMAT] = saving.JOURNAL_FORMAT + 1
        journal.write_text(json.dumps(data), encoding="utf-8")
        before = journal.read_bytes()
        (said,) = saving.recover()
        self.assertIn(journal.name, said)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.state(), OLD)

    def test_one_that_is_not_a_journal_is_left_as_it_is(self):
        for listed in ("damaged", [], [{"target": "a"}]):
            with self.subTest(listed=listed):
                self.clear_journals()
                journal = saving.journal_dir() / "1-damaged.json"
                journal.parent.mkdir(parents=True, exist_ok=True)
                journal.write_text(json.dumps({"format": 1, saving.FILES: listed}), encoding="utf-8")
                (said,) = saving.recover()
                self.assertIn(journal.name, said)
                self.assertTrue(journal.exists())

    def test_one_that_would_move_a_file_from_elsewhere_is_refused(self):
        journal = self.journal_after_commit()
        data = json.loads(journal.read_text(encoding="utf-8"))
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "precious").write_bytes(b"keep")
        data[saving.FILES][0]["old"] = str(elsewhere / "precious")
        journal.write_text(json.dumps(data), encoding="utf-8")
        (said,) = saving.recover()
        self.assertIn(journal.name, said)
        self.assertEqual((elsewhere / "precious").read_bytes(), b"keep")
        self.assertEqual(self.state(), OLD)

    def test_journals_are_kept_in_the_studios_own_folder(self):
        self.assertEqual(saving.journal_dir().parent, saving.places.local_home())


if __name__ == "__main__":
    unittest.main()
