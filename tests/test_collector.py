"""Python's garbage collected on the window's thread, never on a worker's.

A cycle holding Qt objects, collected on a worker's thread, destroyed them
there and corrupted the heap: *Open one of yours…* ended the Studio while
*Make a project?* was up, the import dialog's cycle collected by the check
of the other mods on this PC (see ui.collector).
"""

from __future__ import annotations

import gc
import os
import sys
import threading
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.ui import collector  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _Held(QObject):
    """A Qt object in a cycle with itself, as a shut dialog is with its slots."""

    def __init__(self):
        super().__init__()
        self.me = self


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.collected_on = []
        gc.collect()
        self.addCleanup(gc.enable)
        self.collector = collector.Collector(interval_ms=60_000)
        self.addCleanup(self.collector.deleteLater)
        self.addCleanup(self.collector.timer.stop)
        callback = lambda phase, _info: phase == "start" and self.collected_on.append(  # noqa: E731
            threading.current_thread()
        )
        gc.callbacks.append(callback)
        self.addCleanup(gc.callbacks.remove, callback)

    def test_a_worker_making_garbage_never_collects_it(self):
        held = _Held()
        gone = []
        held.destroyed.connect(lambda: gone.append(threading.current_thread()))
        del held

        def churn():
            # Far over every threshold: Python's own collection would run
            # here, on this thread, many times over.
            for _ in range(200_000):
                pair = [], []
                pair[0].append(pair[1])
                pair[1].append(pair[0])

        worker = threading.Thread(target=churn)
        worker.start()
        worker.join()
        self.assertNotIn(worker, self.collected_on)
        self.assertEqual(gone, [])

        self.collector.check()
        gc.collect()
        self.assertTrue(self.collected_on)
        self.assertTrue(all(thread is threading.main_thread() for thread in self.collected_on))
        self.assertEqual(gone, [threading.main_thread()])

    def test_check_collects_once_over_the_threshold(self):
        self.collector.threshold = (10, 10, 10)
        for _ in range(100):
            pair = [], []
            pair[0].append(pair[1])
        self.collector.check()
        self.assertEqual(self.collected_on, [threading.main_thread()])


if __name__ == "__main__":
    unittest.main()
