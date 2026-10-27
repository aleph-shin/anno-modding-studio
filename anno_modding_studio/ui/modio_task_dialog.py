"""A dialog that runs its mod.io jobs on a thread, one at a time, with a
progress bar and a Stop button.

The Get dialog and the install plan grew the same plumbing side by side --
start the worker, show the bar, take its progress, hide the bar, stop it,
wait for it on the way out -- and drifted: only the plan counted the bar in
kilobytes, so in Get a download past two gigabytes overflowed the bar's
signed thirty-two bits.  This is the one copy (Stage 3.4 of
docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QApplication, QDialog, QProgressBar, QPushButton

from ..core.i18n import tr
from .widgets import show_progress
from .workers import ModioTaskWorker


class ModioTaskDialog(QDialog):
    """A subclass builds ``self.progress`` and ``self.stop_button``, sets
    ``self._fetch`` (the transport, which a test replaces), says what is
    happening through :meth:`say`, and keeps its own buttons right in
    :meth:`_refresh_state`, which runs as a job starts and as it ends.

    ``_worker`` is the job running, or the last one to have run.
    """

    progress: QProgressBar
    stop_button: QPushButton

    # How long the way out waits for a job to end, in milliseconds.
    halt_wait = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[ModioTaskWorker] = None
        self._fetch: Any = None
        self._on_done: Any = None
        self._halted = False

    # -- what a subclass says -------------------------------------------------
    def _api_key(self) -> str:
        raise NotImplementedError

    def say(self, text: str) -> None:
        raise NotImplementedError

    def _refresh_state(self) -> None:
        pass

    # -- the thread -----------------------------------------------------------
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _start(self, job, on_done, on_settled=None) -> None:
        """Run ``job`` (see :class:`ModioTaskWorker`); ``on_done`` gets what
        it returns, and ``on_settled`` each step of a queue as it lands."""
        self._worker = ModioTaskWorker(self._api_key(), job, parent=self, fetch=self._fetch)
        self._worker.progress.connect(self._on_progress)
        if on_settled is not None:
            self._worker.settled.connect(on_settled)
        self._on_done = on_done
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.stop_button.setVisible(True)
        self._worker.start()
        self._refresh_state()

    def _on_succeeded(self, result) -> None:
        # A job's answer that lands once the dialog is on its way out is not
        # acted on: the request in flight when Cancel was pressed finishes,
        # and its job comes back as if never stopped (A11-145).
        if self._halted or (self.sender() is not None and self.sender() is not self._worker):
            return
        self._on_done(result)

    def _on_progress(self, done: int, total: int, text: str) -> None:
        show_progress(self.progress, done, total)
        if text:
            self.say(text)

    def _on_failed(self, message: str) -> None:
        self.say(message)

    def _on_finished(self) -> None:
        # A job started from the slot the last one's result landed in is
        # running before that one's ``finished`` gets here: the bar and Stop
        # are the new job's by then.
        if self.sender() is not None and self.sender() is not self._worker:
            return
        self.progress.setVisible(False)
        self.stop_button.setVisible(False)
        self._refresh_state()

    def _stop(self) -> None:
        """Stop pressed: the job stops at its next request or chunk."""
        if self._worker is not None:
            self._worker.cancel()
            self.say(tr("Stopping…"))

    def _halt(self) -> None:
        """On the way out: stop the job and give it a moment to end.

        Nothing the job answers after this is acted on.  One that has not
        ended in the moment -- a request mod.io is slow to answer, an unpack
        -- runs on without the dialog: its callers delete the dialog, which
        deleted the running thread with it and ended the process (A11-146).
        """
        self._halted = True
        if self._busy():
            self._worker.cancel()
            if not self._worker.wait(self.halt_wait):
                _outlive(self._worker)


# Jobs whose dialog has gone, held here until each ends.
_OUTLIVING: set = set()


def _outlive(worker: ModioTaskWorker) -> None:
    """Let ``worker`` run to its end without the dialog it is a child of."""
    worker.setParent(None)
    _OUTLIVING.add(worker)
    # Deleted on the window's thread once it has ended, and let go of then.
    worker.finished.connect(worker.deleteLater)
    worker.destroyed.connect(lambda: _OUTLIVING.discard(worker))
    app = QApplication.instance()
    if app is not None and not getattr(app, "_waits_for_outliving", False):
        # A thread still running when the app goes takes the process with it.
        app.aboutToQuit.connect(_wait_for_outliving)
        app._waits_for_outliving = True


def _wait_for_outliving() -> None:
    for worker in list(_OUTLIVING):
        worker.wait(60000)
