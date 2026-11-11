"""Python's garbage collected on the window's own thread, never on a worker's.

Python collects reference cycles whenever enough objects have been made,
on whichever thread happens to make the one over the line. A cycle can hold
Qt objects the window's thread made: a dialog that was shut and the widgets
and timers in it, held to each other through slots and lambdas. Collected on
a worker's thread, each is destroyed there, and Qt objects destroyed on a
thread they do not belong to corrupt the heap. Qt says "QBasicTimer::stop:
Failed. Possibly trying to stop from a different thread", and a moment later
Windows ends the Studio with 0xc0000374 (heap corruption), with no word.

It did, reliably, on *Open one of yours…* once a mod had been imported: the
import dialog, shut, was left in a cycle, the check of the other mods on
this PC was walking their folders in the background, and while *Make a
project?* was up the walk crossed the line and collected the dialog.

So automatic collection is turned off, and a timer on the window's thread
collects instead, a generation at a time on the same thresholds Python would
have used. The timer runs inside a modal dialog's loop as much as in the
application's, so nothing waits long. A worker can still make garbage; it
just never collects it.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

# How often the window's thread looks whether a collection is due.  Python
# looks after every allocation; a second is long enough to cost nothing and
# short enough that the cycles waiting for it stay few.
INTERVAL_MS = 1000


class Collector(QObject):
    """Collects garbage on the thread it was made on, which is the window's."""

    def __init__(self, parent=None, interval_ms: int = INTERVAL_MS):
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self) -> None:
        """A collection of each generation whose count is over its
        threshold, the youngest first, as Python's own would be."""
        young, middle, old = gc.get_count()
        if young > self.threshold[0]:
            gc.collect(0)
            if middle > self.threshold[1]:
                gc.collect(1)
                if old > self.threshold[2]:
                    gc.collect(2)


def collect_on_this_thread(app) -> Collector:
    """Turn automatic collection off and collect on ``app``'s thread from
    now on; the collector is ``app``'s child, and lives as long as it does."""
    return Collector(app)
