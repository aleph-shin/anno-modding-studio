"""The reading after an edit in a process of its own: core.reading_process,
and the worker that starts it.

Each test that starts a process ends it after LIMIT seconds whatever
happens, so one that goes wrong fails and does not hang the suite.
"""

from __future__ import annotations

import os
import pickle
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_stale import ALPHA, PATTERN, StaleCase  # noqa: E402

from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core import modfile, reading_process, stale  # noqa: E402
from anno_modding_studio.core.reading_process import (  # noqa: E402
    DONE,
    FAILED,
    SEARCHES,
    SWEEPING,
    Died,
    Failed,
    Reading,
    Stopped,
)

LIMIT = 60

# A process that stands in for the reading's: it reads the request, and then
# does what the test wrote for it.
STAND_IN = """
import pickle, sys, time
source, sink = sys.stdin.buffer, sys.stdout.buffer
request = pickle.load(source)
def say(*message):
    pickle.dump(message, sink, pickle.HIGHEST_PROTOCOL)
    sink.flush()
"""


def shape(answers) -> dict:
    """What a reading found, without the ids it makes up as it goes: an op
    by where it stands, and a change by the op it is under."""
    out = {}
    for origin, found in answers.items():
        answer = found.report if isinstance(found, modfile.Reread) else found
        rank = {op.uid: op.order for op in answer.ops}
        out[origin] = (
            [(op.order, op.source, tuple(rank.get(uid, uid) for uid in op.within))
             for op in answer.ops],
            sorted(
                (change.guid, change.path, change.op, change.new_value,
                 change.payload or "", rank.get(change.op_uid, -1))
                for change in answer.changes
            ),
            [(entry.order, entry.source, entry.why) for entry in answer.unread(origin)],
            getattr(found, "text", None),
            getattr(found, "orders", None),
        )
    return out


class ProcessCase(StaleCase):
    def asked(self, edited: bool = False):
        """A stack whose last mod searches, with its reading asked for."""
        root = self.mods(a=ALPHA, c=PATTERN + ALPHA)
        loadout = self.loadout(root, "a", "c")
        stale.check(loadout)
        if edited:
            stale.edited(loadout, {"a": 0})
        # As a session that has not answered the mod's search yet.
        self.index._matched.clear()
        return loadout, stale.snapshot(loadout, asked=None if edited else ["c"])

    def reading(self, taken) -> Reading:
        return Reading(reading_process.request_for(taken, self.index))

    def stand_in(self, body: str) -> Reading:
        """A reading whose process is ``body``, after :data:`STAND_IN`."""
        reading = Reading(reading_process.Request(taken=None))
        script = STAND_IN + textwrap.dedent(body)
        patch = mock.patch.object(
            reading_process, "command", return_value=[sys.executable, "-c", script]
        )
        patch.start()
        self.addCleanup(patch.stop)
        return reading

    def run_it(self, reading: Reading, searched=None):
        """``reading.run``, ended after LIMIT seconds if it has not ended."""
        late = []

        def give_up():
            late.append(True)
            reading.stop(now=True)

        timer = threading.Timer(LIMIT, give_up)
        timer.start()
        try:
            return reading.run(searched=searched)
        finally:
            timer.cancel()
            self.assertFalse(late, "the reading did not end in %d s" % LIMIT)

    def in_background(self, reading: Reading, searched=None):
        """Run ``reading`` on a thread; what became of it, once joined."""
        ended = {}

        def run():
            try:
                ended["answers"] = self.run_it(reading, searched)
            except Exception as exc:  # noqa: BLE001 - handed to the test
                ended["raised"] = exc

        thread = threading.Thread(target=run)
        thread.start()
        return thread, ended

    def wait_until(self, holds, what: str) -> None:
        until = time.monotonic() + LIMIT
        while not holds():
            self.assertLess(time.monotonic(), until, what)
            time.sleep(0.01)


class TestTheAnswerFromAProcess(ProcessCase):
    def test_it_is_the_answer_a_reading_here_gives(self):
        _loadout, taken = self.asked()
        apart = self.run_it(self.reading(taken))
        here = stale.read_again(taken, self.index)
        self.assertEqual(shape(apart), shape(here))
        self.assertTrue(apart["c"].changes)

    def test_and_for_a_mod_read_from_what_the_loadout_writes(self):
        loadout, taken = self.asked(edited=True)
        self.assertIn("a", taken.texts)
        apart = self.run_it(self.reading(taken))
        self.assertIsInstance(apart["a"], modfile.Reread)
        self.assertEqual(shape(apart), shape(stale.read_again(taken, self.index)))
        stale.take(loadout, apart)
        self.assertEqual(stale.waiting(loadout), [])

    def test_what_its_sweep_found_is_told_and_only_what_is_new(self):
        _loadout, taken = self.asked()
        told = []
        self.run_it(self.reading(taken), told.append)
        self.assertEqual(len(told), 1)
        self.assertTrue(told[0])
        # Kept on the window's index, the next reading has no sweep to make.
        self.index.keep_searches(told[0])
        self.assertEqual(self.index.kept_searches(), told[0])
        again = []
        self.run_it(self.reading(taken), again.append)
        self.assertEqual(again, [])

    def test_a_reading_that_goes_wrong_says_so_in_a_sentence(self):
        _loadout, taken = self.asked()
        request = reading_process.request_for(taken, self.index)
        request.db_path = str(Path(request.db_path).with_name("no-such-index.sqlite"))
        with self.assertRaises(Failed) as raised:
            self.run_it(Reading(request))
        self.assertIn("OperationalError", str(raised.exception))

    def test_it_reads_in_the_windows_language(self):
        _loadout, taken = self.asked()
        with mock.patch.object(reading_process, "Request", wraps=reading_process.Request) as made, \
                mock.patch("anno_modding_studio.core.i18n.language", return_value="de"):
            reading_process.request_for(taken, self.index)
        self.assertEqual(made.call_args.kwargs["language"], "de")


class TestStoppingIt(ProcessCase):
    def test_stopped_before_it_starts_nothing_is_started(self):
        reading = self.stand_in("time.sleep(30)")
        reading.stop()
        with mock.patch.object(subprocess, "Popen") as started, self.assertRaises(Stopped):
            self.run_it(reading)
        started.assert_not_called()

    def test_stopped_it_ends_at_once(self):
        reading = self.stand_in("time.sleep(30)")
        thread, ended = self.in_background(reading)
        self.wait_until(lambda: reading._process is not None, "it never started")
        started = time.monotonic()
        reading.stop()
        thread.join(LIMIT)
        self.assertIsInstance(ended.get("raised"), Stopped)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(reading._process.poll())

    def test_stopped_in_its_sweep_it_ends_when_the_sweep_has_said_what_it_found(self):
        reading = self.stand_in("""
            say("sweeping")
            time.sleep(1.5)
            say("searches", {"//Found": ["1000"]})
            time.sleep(30)
        """)
        told = []
        thread, ended = self.in_background(reading, told.append)
        self.wait_until(lambda: reading._sweeping, "it never said it was sweeping")
        reading.stop()
        time.sleep(0.3)
        self.assertIsNone(reading._process.poll(), "ended in the middle of its sweep")
        thread.join(LIMIT)
        self.assertIsInstance(ended.get("raised"), Stopped)
        self.assertEqual(told, [{"//Found": ["1000"]}])
        self.assertIsNotNone(reading._process.poll())

    def test_a_closing_window_ends_it_in_its_sweep(self):
        reading = self.stand_in("""
            say("sweeping")
            time.sleep(30)
            say("searches", {"//Found": ["1000"]})
        """)
        told = []
        thread, ended = self.in_background(reading, told.append)
        self.wait_until(lambda: reading._sweeping, "it never said it was sweeping")
        started = time.monotonic()
        reading.stop(now=True)
        thread.join(LIMIT)
        self.assertIsInstance(ended.get("raised"), Stopped)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(told, [])

    def test_the_names_of_the_messages_are_what_the_process_sends(self):
        self.assertEqual((SWEEPING, SEARCHES, DONE, FAILED),
                         ("sweeping", "searches", "done", "failed"))


class TestAProcessThatFails(ProcessCase):
    def test_one_that_ends_with_no_answer_died(self):
        reading = self.stand_in("sys.exit(3)")
        with self.assertRaises(Died):
            self.run_it(reading)

    def test_one_that_cannot_be_started_died(self):
        reading = Reading(reading_process.Request(taken=None))
        with mock.patch.object(
            reading_process, "command", return_value=[str(ROOT / "no-such-python.exe")]
        ), self.assertRaises(Died):
            self.run_it(reading)

    def test_one_that_says_it_failed_is_a_reading_that_failed(self):
        reading = self.stand_in('say("failed", "ValueError: no such mod")')
        with self.assertRaises(Failed) as raised:
            self.run_it(reading)
        self.assertEqual(str(raised.exception), "ValueError: no such mod")


class TestItDoesNotOutliveTheWindow(unittest.TestCase):
    def start(self, command):
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env=reading_process._environment(),
        )
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.kill)
        return process

    def test_the_studio_started_with_the_argument_is_the_process(self):
        self.assertEqual(reading_process.command()[-1], reading_process.ARGUMENT)
        process = self.start(reading_process.command())
        # A window that goes before it has asked anything.
        process.stdin.close()
        self.assertEqual(process.wait(LIMIT), 0)

    def test_a_built_studio_starts_itself_with_the_argument_and_its_own_path(self):
        """It has no ``-m`` and no package on a path: the exe routes the
        argument (``__main__``), and an outside PYTHONPATH is left as it is."""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:/Studio/AnnoModdingStudio.exe"), \
                mock.patch.dict(os.environ, {"PYTHONPATH": "elsewhere"}):
            self.assertEqual(reading_process.command(),
                             ["C:/Studio/AnnoModdingStudio.exe", reading_process.ARGUMENT])
            self.assertEqual(reading_process._environment()["PYTHONPATH"], "elsewhere")

    def test_the_pipe_closing_ends_it_in_the_middle_of_its_work(self):
        process = self.start([sys.executable, "-c", textwrap.dedent("""
            import sys, threading, time
            from anno_modding_studio.core import reading_process
            threading.Thread(
                target=reading_process._end_with_the_window,
                args=(sys.stdin.buffer,), daemon=True,
            ).start()
            sys.stdout.buffer.write(b"up")
            sys.stdout.buffer.flush()
            time.sleep(120)
        """)])
        self.assertEqual(process.stdout.read(2), b"up")
        started = time.monotonic()
        process.stdin.close()
        self.assertEqual(process.wait(LIMIT), 0)
        self.assertLess(time.monotonic() - started, 10)

    def test_it_imports_nothing_of_qt(self):
        done = subprocess.run(
            [sys.executable, "-c",
             "import sys, anno_modding_studio.core.reading_process as r; "
             "from anno_modding_studio.core import stale, index, i18n; "
             "print(sorted(name for name in sys.modules if name.startswith('PySide6')))"],
            capture_output=True, text=True, env=reading_process._environment(), timeout=LIMIT,
        )
        self.assertEqual(done.stdout.strip(), "[]", done.stderr)


class TestAnIndexOpenedToReadOnly(ProcessCase):
    def test_it_reads_and_cannot_write(self):
        other = index_mod.AssetIndex(self.index.db_path, self.index.source_path, read_only=True)
        self.addCleanup(other.close)
        self.assertEqual(other.asset_count, self.index.asset_count)
        with self.assertRaises(sqlite3.OperationalError):
            other.clear_texts()

    def test_the_names_the_mods_give_are_its_own_to_keep(self):
        from anno_modding_studio.core.texts import Names

        other = index_mod.AssetIndex(self.index.db_path, self.index.source_path, read_only=True)
        self.addCleanup(other.close)
        other.set_mod_names(Names(texts={"1000": "A name"}, by={"1000": "a"}))
        self.assertEqual(other.text_for("1000"), "A name")
        self.assertNotEqual(self.index.text_for("1000"), "A name")

    def test_search_answers_handed_over_are_not_looked_for_again(self):
        _loadout, taken = self.asked()
        stale.read_again(taken, self.index)
        kept = self.index.kept_searches()
        self.assertTrue(kept)
        other = index_mod.AssetIndex(self.index.db_path, self.index.source_path, read_only=True)
        self.addCleanup(other.close)
        other.keep_searches(kept)
        swept = []
        stale.read_again(taken, other, searching=lambda *where: swept.append(where))
        # Nothing to look for: one word that it is done, and no sweep.
        self.assertEqual(swept, [(1, 1)])
        # And what is handed over does not replace what is there.
        other.keep_searches({path: [] for path in kept})
        self.assertEqual(other.kept_searches(), kept)

    def test_a_request_is_what_can_be_sent(self):
        _loadout, taken = self.asked()
        request = reading_process.request_for(taken, self.index, ["a", "c"])
        back = pickle.loads(pickle.dumps(request))
        self.assertEqual((back.db_path, back.logged), (str(self.index.db_path), ["a", "c"]))


class TestTheWorker(ProcessCase):
    def setUp(self):
        super().setUp()
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui.workers import RereadWorker

        self.Qt = Qt
        self.Worker = RereadWorker
        self.addCleanup(setattr, RereadWorker, "failures", 0)
        RereadWorker.failures = 0

    def worker(self, taken, where: str = ""):
        patch = mock.patch.dict(os.environ, {"ANNO_STUDIO_REREAD": where})
        patch.start()
        self.addCleanup(patch.stop)
        worker = self.Worker(taken, self.index)
        said, fell = [], []
        # Said on whichever thread runs it: no test here has an event loop.
        direct = self.Qt.ConnectionType.DirectConnection
        worker.done.connect(lambda _taken, answers, error: said.append((answers, error)), direct)
        worker.fell_back.connect(fell.append, direct)
        return worker, said, fell

    def test_the_suite_and_the_tests_about_the_reading_read_here(self):
        for where, apart in (("none", False), ("thread", False), ("", True), ("Thread ", False)):
            with mock.patch.dict(os.environ, {"ANNO_STUDIO_REREAD": where}):
                self.assertEqual(self.Worker.in_process(), apart, where)

    def test_it_reads_in_a_process_and_keeps_what_the_sweep_found(self):
        _loadout, taken = self.asked()
        worker, said, fell = self.worker(taken)
        with mock.patch.object(stale, "read_again", side_effect=AssertionError("read here")):
            worker.run()
        ((answers, error),) = said
        self.assertEqual(error, "")
        self.assertEqual(shape(answers), shape(stale.read_again(taken, self.index)))
        self.assertEqual(fell, [])
        self.assertTrue(self.index.kept_searches())

    def test_on_the_thread_no_process_is_started(self):
        _loadout, taken = self.asked()
        worker, said, _fell = self.worker(taken, "thread")
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("started")):
            worker.run()
        self.assertTrue(said[0][0])

    def test_a_process_that_dies_is_read_here_and_said(self):
        _loadout, taken = self.asked()
        worker, said, fell = self.worker(taken)
        with mock.patch.object(
            reading_process, "command", return_value=[sys.executable, "-c", "raise SystemExit(3)"]
        ):
            worker.run()
        ((answers, error),) = said
        self.assertEqual(error, "")
        self.assertEqual(shape(answers), shape(stale.read_again(taken, self.index)))
        self.assertEqual(len(fell), 1)
        self.assertEqual(self.Worker.failures, 1)

    def test_two_in_a_row_and_no_more_are_tried(self):
        _loadout, taken = self.asked()
        dead = mock.patch.object(
            reading_process, "command", return_value=[sys.executable, "-c", "raise SystemExit(3)"]
        )
        with dead:
            for _n in range(self.Worker.GIVE_UP):
                worker, _said, _fell = self.worker(taken)
                self.assertTrue(self.Worker.in_process())
                worker.run()
        self.assertFalse(self.Worker.in_process())
        worker, said, fell = self.worker(taken)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("started")):
            worker.run()
        self.assertTrue(said[0][0])
        self.assertEqual(fell, [])

    def test_one_that_works_is_a_fresh_start(self):
        _loadout, taken = self.asked()
        self.Worker.failures = 1
        worker, _said, _fell = self.worker(taken)
        worker.run()
        self.assertEqual(self.Worker.failures, 0)

    def test_a_reading_that_failed_there_is_said_as_one_that_failed_here(self):
        _loadout, taken = self.asked()
        worker, said, fell = self.worker(taken)
        script = STAND_IN + 'say("failed", "ValueError: no such mod")'
        with mock.patch.object(
            reading_process, "command", return_value=[sys.executable, "-c", script]
        ), mock.patch.object(stale, "read_again", side_effect=AssertionError("read here")):
            worker.run()
        self.assertEqual(said, [(None, "ValueError: no such mod")])
        self.assertEqual((fell, self.Worker.failures), ([], 0))

    def test_cancelled_before_it_runs_it_says_it_was_stopped(self):
        _loadout, taken = self.asked()
        worker, said, fell = self.worker(taken)
        worker.cancel()
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("started")):
            worker.run()
        self.assertEqual((said, fell), ([(None, "")], []))

    def test_a_closing_window_ends_its_process(self):
        _loadout, taken = self.asked()
        worker, said, fell = self.worker(taken)
        script = STAND_IN + 'say("sweeping")\ntime.sleep(60)'
        with mock.patch.object(
            reading_process, "command", return_value=[sys.executable, "-c", script]
        ):
            thread = threading.Thread(target=worker.run)
            thread.start()
            self.wait_until(
                lambda: worker._apart is not None and worker._apart._sweeping,
                "it never said it was sweeping",
            )
            started = time.monotonic()
            worker.cancel(closing=True)
            thread.join(LIMIT)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((said, fell), ([(None, "")], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
