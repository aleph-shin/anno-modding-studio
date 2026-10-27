"""Readings that have gone stale: noticed, and read again in the background.

``core.stale`` says which mods' readings a change in what loads could have
moved, reads them again and puts the new readings in place.  This is the
window's side, held as ``window.readings``: it looks after every change to
the loadout, starts the reading on a thread of its own, and takes the
answer when it comes -- unless the stack has changed again meanwhile, in
which case the answer is to a question nobody is asking and the reading is
started over.

An edit made here leaves readings in doubt as well: the later ops of the
mod edited, and the mods that load after it.  The window says so after each
step of undo it records, takes back or makes again (:meth:`edited`).

A mod's own folder is looked at when a loadout is opened and before Play
(:meth:`follow`): one updated on disk since it was read is read again the
same way.

A reading taken ends the undo history, as every reading of a folder does
(see :mod:`core.undo`) -- but not one that was asked for by edits alone.
That would end the history at every edit.  What such a reading moved is
taken as it is, and the steps stay.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..core import loaderlog, stale
from ..core.i18n import tr, trn
from .background import is_waiting
from .widgets import busy_cursor
from .workers import RereadWorker

# How long to wait before trying again to take a reading that came while a
# dialog was open.
HELD_MS = 400
# How long after an edit the reading for it starts.  Edits come in runs --
# one value after another in Fields -- and each would otherwise write out
# every edited mod's file for a reading the next edit stops.
EDIT_MS = 500


class ReadingsController:
    """The window's stale readings; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        self.worker = None
        # A reading that came while a dialog was open, kept until it shuts:
        # the dialog may be in the middle of changing the loadout itself.
        self._held = None
        # The question a reading failed for, so it is not asked again on
        # every edit; and what went wrong, for the rows to say.
        self._refused = None
        self.failed = ""
        # Why no reading may run just now: see hold().
        self._holds: set = set()
        # Whether it has been said that a reading was done in the window's
        # own process: once is enough.
        self._said_fell_back = False
        # Runs out EDIT_MS after the last edit, and starts the reading.
        self._soon = QTimer(window)
        self._soon.setSingleShot(True)
        self._soon.timeout.connect(self._start_waiting)

    @staticmethod
    def off() -> bool:
        """``ANNO_STUDIO_REREAD=none`` turns it off, marking included, which
        is how the test suite runs: see tests/_isolation.py.  ``thread`` has
        the reading done in the window's own process (:class:`RereadWorker`)."""
        return os.environ.get("ANNO_STUDIO_REREAD", "").strip().lower() == "none"

    # -- the index, while something else has it ---------------------------------
    def hold(self, why: str) -> None:
        """Stop reading, and start no reading until :meth:`release` is told
        the same ``why``: the index is about to be closed, built again or
        written to.

        A reading in a process of its own has the index's file open.  On
        Windows that stops the file being replaced, and a sweep holds it
        against a writer for as long as the sweep takes.  A reading on a
        thread has the window's own connection, which must not be closed
        under it.
        """
        self._holds.add(why)
        self._soon.stop()
        worker = self.worker
        if worker is not None:
            worker.cancel(closing=True)
            worker.wait(30000)

    def release(self, why: str) -> None:
        """What :meth:`hold` was told of is over; with nothing else holding,
        read whatever is in doubt."""
        if why not in self._holds:
            return
        self._holds.discard(why)
        if not self._holds and self.worker is None:
            self.check()

    # -- noticing ------------------------------------------------------------
    def check(self) -> None:
        """Look at what loads now, and read again whatever that leaves in
        doubt.  Called after every change to the loadout, so it is cheap
        where nothing that loads has moved."""
        if self.off():
            return
        loadout = self.window.loadout
        stale.check(loadout)
        if not stale.waiting(loadout):
            self._show(False)
        elif not self._soon.isActive():
            # Else an edit has one coming, and that reads for this as well.
            self.start()

    def follow(self) -> None:
        """:meth:`check`, after looking in every mod's folder for one that
        holds another version or build than the one read
        (:func:`core.stale.follow`), and for a mod of yours whose project
        another loadout changed since, or that is missing and has a library
        build to be read from instead (:func:`core.stale.follow_projects`).
        Reads a file from each, so it is for opening a loadout and for Play,
        not for every change."""
        if self.off():
            return
        loadout = self.window.loadout
        projects = stale.follow_projects(loadout, self.window.where.library_dir())
        if stale.follow(loadout) or projects:
            self.window.changes_panel.refresh()
            self.window.file_panel.mark_stale()
        self.check()

    def edited(self, step, redraw: bool = True) -> None:
        """A step of undo was just recorded, taken back or made again:
        what it changed in the mods' files leaves readings in doubt.

        ``redraw`` draws the rows again where a mod is newly marked, for
        whoever is not about to do that anyway.
        """
        # A mod arriving or going is a change in what loads, which check()
        # sees; what such a step moved in the mod is the mod itself.
        if self.off() or not step.reach or step.layers is not None:
            return
        self._marked(step.reach, step.assets, redraw)

    def options_declared(self, origin: str) -> None:
        """What a mod declares as its options was just changed: each of its
        ops that reads one is read again at them, and so is every mod after
        it, which may read them in full."""
        if not self.off():
            self._marked({origin: 0}, None, True)

    def _marked(self, reach, assets, redraw: bool) -> None:
        loadout = self.window.loadout
        before = stale.waiting(loadout)
        stale.edited(loadout, reach, assets)
        if not stale.waiting(loadout):
            return
        if self.worker is not None:
            # It reads the mods as they were: stopped now, which costs
            # nothing, and whatever follows starts the one that is wanted.
            self.start()
        self._start_soon()
        if redraw and stale.waiting(loadout) != before:
            self.window.changes_panel.refresh()
            self.window.file_panel.mark_stale()

    def _start_soon(self) -> None:
        """Start the reading once the edits have stopped coming.  Said to be
        running from now, so Export and Play wait for it and the rows say
        they are being read again."""
        if self.window.index is None:
            return
        if EDIT_MS <= 0:
            self.start()
            return
        self._soon.start(EDIT_MS)
        self._show(True)
        self._tell(len(stale.waiting(self.window.loadout)))

    def _start_waiting(self) -> None:
        if self.off() or not stale.waiting(self.window.loadout):
            self._tell()
            return
        self.start()

    def read_alone(self, origin: str) -> None:
        """A mod was just imported on its own."""
        if not self.off():
            stale.read_alone(self.window.loadout, origin)

    def settle(self, origins=None) -> None:
        """The mods named, or all of them, were just read where they stand."""
        stale.settle(self.window.loadout, origins)

    def adopted(self) -> None:
        """Another loadout is open: nothing held or refused is about it."""
        self._held = None
        self._soon.stop()
        self._refused = None
        self.failed = ""
        self._tell()
        self.window.changes_panel.moved = set()
        self.follow()

    # -- reading -------------------------------------------------------------
    def start(self) -> None:
        window = self.window
        if window.index is None or self._holds:
            return
        loadout = window.loadout
        if self.worker is not None:
            # Reading for a stack that has changed since, or for mods as
            # they were before an edit: stop it, and the stopping starts
            # the reading that is wanted.
            if not stale.still_asked(loadout, self.worker.taken):
                self.worker.cancel()
            return
        taken = stale.snapshot(loadout, library_dir=window.where.library_dir())
        if self._refused == (taken.stack, taken.asked, taken.edits):
            self._show(False)
            self._tell()
            return
        self.failed = ""
        worker = RereadWorker(taken, window.index, loaderlog.logged_order(loadout.game), window)
        worker.done.connect(self._on_done)
        worker.fell_back.connect(self._on_fell_back)
        worker.finished.connect(worker.deleteLater)
        self.worker = worker
        self._show(True)
        self._tell(len(taken.asked))
        worker.start()

    def stop(self) -> None:
        """The window is closing: stop reading and wait for the thread."""
        self._held = None
        self._soon.stop()
        worker = self.worker
        if worker is not None:
            # Wherever it is, the sweep for the mods' searches included.
            worker.cancel(closing=True)
            worker.wait(30000)
        self._tell()

    def _show(self, reading: bool) -> None:
        self.window.changes_panel.reading_again = reading

    def _tell(self, mods: int = 0) -> None:
        """Say in the status bar's corner that ``mods`` are being read
        again, or that none is: a reading held for a dialog is still one
        the window has not caught up with."""
        tasks = self.window.background
        if self.worker is None and self._held is None and not self._soon.isActive():
            tasks.end("readings")
        elif mods:
            tasks.begin("readings", trn(
                "Reading %(n)s mod again…", "Reading %(n)s mods again…", mods
            ))

    def _on_fell_back(self, why: str) -> None:
        """A reading's process could not be started, or died: it is being
        done in the window's own process, which is slower and not wrong."""
        if self._said_fell_back:
            return
        self._said_fell_back = True
        self.window.status_label.setText(tr(
            "The mods are being read again inside the Studio's own process, "
            "which slows the window meanwhile: %s", why,
        ))

    def _on_done(self, taken, reports, error: str) -> None:
        self.worker = None
        self._tell()
        loadout = self.window.loadout
        if (reports is None and not error) or not stale.still_asked(loadout, taken):
            # Stopped, or answered for a stack that has changed since.
            self.check()
            return
        if error:
            self._refused = (taken.stack, taken.asked, taken.edits)
            self.failed = error
            self._show(False)
            self.window.changes_panel.refresh()
            self.window.status_label.setText(
                tr("The mods could not be read again: %s", error)
            )
            return
        self._held = (taken, reports)
        self._tell(len(taken.asked))
        self._take_held()

    def _take_held(self) -> None:
        if self._held is None:
            return
        taken, reports = self._held
        if not stale.still_asked(self.window.loadout, taken):
            self._held = None
            self._tell()
            self.check()
            return
        modal = QApplication.activeModalWidget()
        if modal is not None and not is_waiting(modal):
            QTimer.singleShot(HELD_MS, self._take_held)
            return
        self._held = None
        self._take(taken, reports)

    def _take(self, taken, reports) -> None:
        window = self.window
        loadout = window.loadout
        # Asked before the reading is taken, which is what says they are
        # in doubt no more.
        for_edits = stale.only_edits(loadout, taken.asked)
        updated = len(stale.updated(loadout, taken.asked))
        standing = len(set(stale.standing_in(loadout)) & set(taken.asked))
        with busy_cursor():
            held = loadout.standing()
            moved = stale.take(
                loadout, {origin: reports[origin] for origin in taken.asked if origin in reports}
            )
            stale.give_up(loadout, [origin for origin in taken.asked if origin not in reports])
            window.changes_panel.moved = {uid for found in moved for uid in found.ops}
            self._show(False)
            from_text = [origin for origin in taken.asked if origin in taken.texts]
            window.files.took_reading(
                [origin for origin in taken.asked if origin not in from_text]
            )
            read_back = [found.origin for found in moved if found.project]
            window.files.read_again([origin for origin in from_text if origin not in read_back], held)
            window.files.took_projects(read_back)
            # Not work: what it brings is read again from the same mods on
            # the next opening. A loadout opened and only looked at wore the
            # unsaved mark once the reading it was saved waiting for came in.
            window.on_changes_updated(work=False)
            window.start.refresh_source_panel()
        self._tell()
        ops = sum(len(found.ops) + found.gone for found in moved)
        if for_edits:
            # Asked for by what was done here: the steps stay, and what the
            # reading moved is no step of its own.  Said only where it
            # moved something, since it follows every edit.
            window.undo.absorb()
            if ops:
                window.status_label.setText(trn(
                    "After that edit, %(n)s ModOp comes to something else. Its row says so.",
                    "After that edit, %(n)s ModOps come to something else. Their rows say so.",
                    ops,
                ))
            return
        # A reading of their folders, not an edit: see core.undo.
        window.forget_undo()
        said = []
        projects = [found for found in moved if found.project]
        changed = [found for found in projects if not found.first]
        if changed:
            said.append(trn(
                "%(n)s mod of yours was changed in its project by another loadout, "
                "and has been read again as its project is now.",
                "%(n)s mods of yours were changed in their projects by another loadout, "
                "and have been read again as their projects are now.",
                len(changed),
            ))
        # Just added from another loadout, or its project found again: nobody
        # changed it, and it was said they had (R3-5, R3-42).
        first = len(projects) - len(changed)
        if first:
            said.append(trn(
                "%(n)s mod of yours has been read from its project.",
                "%(n)s mods of yours have been read from their projects.",
                first,
            ))
        given_up = sum(found.given_up for found in projects)
        if given_up:
            said.append(trn(
                "%(n)s edit made here was never saved into its project, so it is given up.",
                "%(n)s edits made here were never saved into their projects, so they are given up.",
                given_up,
            ))
        if standing:
            said.append(trn(
                "%(n)s mod of yours has no project to be found, and is read from the "
                "library's build of it until it is found.",
                "%(n)s mods of yours have no project to be found, and are read from the "
                "library's builds of them until they are found.",
                standing,
            ))
        if updated:
            said.append(trn(
                "%(n)s mod was updated on disk and has been read again.",
                "%(n)s mods were updated on disk and have been read again.",
                updated,
            ))
        rest = len(moved) - updated - len(projects) - standing
        if rest > 0:
            said.append(trn(
                "%(n)s mod was read again, because what loads before it changed.",
                "%(n)s mods were read again, because what loads before them changed.",
                rest,
            ))
        if ops:
            said.append(trn(
                "%(n)s ModOp comes to something else now. Its row says so.",
                "%(n)s ModOps come to something else now. Their rows say so.",
                ops,
            ))
        else:
            said.append(tr("Every ModOp comes to what it did."))
        window.status_label.setText(" ".join(said))
