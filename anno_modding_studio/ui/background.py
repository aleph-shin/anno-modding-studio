"""What the window is doing behind your back, said in the status bar.

Several things run on threads of their own while the window stays usable:
mods read again after a change in what loads, the scan of the other mods on
this PC, the game's loader asked about an export.  Each ends by changing
something on screen, and none of them said it was running -- so there was
no telling, before doing something that matters, whether the window had
caught up.

:class:`BackgroundTasks` is the list of what is running, kept by whoever
starts each thing; :class:`BackgroundIndicator` is the corner of the status
bar that shows it, and says so in as many words when nothing is.

Export and Play do not go ahead of it: each asks :meth:`BackgroundTasks.wait`
first, which shows what is still running and returns when it has finished.
Both leave the game's loader out of that wait: it is a second opinion on what
is written, and none of the files waits on it.  Play then waits for the
loader's verdicts on its own, in a wait whose button skips the check
(``ui.loader_controller``).
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QObject, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressDialog,
    QWidget,
)

from ..core.i18n import tr, trn
from . import theme

# How wide the moving bar is: enough to be seen moving, and no more.
BAR_WIDTH = 64
# And how tall: well inside a line of the status bar's text, so the status
# bar does not grow while it shows.  A QProgressBar here was a line of text
# and its frame tall, and grew the status bar by ten pixels.
BAR_HEIGHT = 6
# The block that moves across it, as a share of its width.
BLOCK_SHARE = 3 / 8
# How long the block takes to cross, in and out again.  Qt's busy bar ran
# at about 190 px a second and wrapped round every third of a second; this
# is half that pace, 88 px in 0.9 s, and nothing jumps back to the start.
SWEEP_MS = 900
# Set on the dialog that waits for the background to finish.  Work that holds
# off while a dialog is open -- it may be changing the loadout -- goes ahead
# under this one, which is open for no other reason than to wait for it.
WAITS = "waitsForBackground"


def is_waiting(widget) -> bool:
    """Whether ``widget`` is the dialog :meth:`BackgroundTasks.wait` shows."""
    return widget is not None and bool(widget.property(WAITS))


class BackgroundTasks(QObject):
    """What is running in the background, each under a key of its own."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running: Dict[str, str] = {}

    def begin(self, key: str, text: str, worker=None) -> None:
        """``key`` is running, described by ``text``.  With ``worker``, a
        thread, it ends when the thread does; without, when :meth:`end`
        says so."""
        if worker is not None:
            worker.finished.connect(lambda key=key: self.end(key))
        if self._running.get(key) != text:
            self._running[key] = text
            self.changed.emit()

    def end(self, key: str) -> None:
        if self._running.pop(key, None) is not None:
            self.changed.emit()

    def running(self) -> List[str]:
        """What is running, the first started first."""
        return list(self._running.values())

    @property
    def busy(self) -> bool:
        return bool(self._running)

    def wait(self, parent=None, but=(), only=None, title: str = "", cancel: str = "",
             note: str = "") -> bool:
        """Wait for everything running to finish, saying what it is; whether
        it did.  At once where nothing is running; False where the wait was
        cancelled, and whoever asked should not go on.

        ``but`` are keys not waited for: work whose answer the one waiting
        does not need, which goes on running behind it.  ``only``, when
        given, are the keys waited for, and nothing else is.  ``title``,
        ``cancel`` and ``note`` (a line under the list) are for a wait that
        is about one thing: Play's for the game's loader, whose button goes
        on without it."""
        but = frozenset(but)
        wanted = None if only is None else frozenset(only)

        def waited_for(key: str) -> bool:
            return key not in but and (wanted is None or key in wanted)

        def busy() -> bool:
            return any(waited_for(key) for key in self._running)

        if not busy():
            return True
        dialog = QProgressDialog(parent)
        dialog.setProperty(WAITS, True)
        dialog.setWindowTitle(title or tr("Finishing background work"))
        dialog.setCancelButtonText(cancel or tr("Cancel"))
        dialog.setRange(0, 0)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)

        def look() -> None:
            if not busy():
                dialog.accept()
                return
            waited = [text for key, text in self._running.items() if waited_for(key)]
            dialog.setLabelText("\n".join([tr("Waiting for this to finish first:"), *waited]
                                          + (["", note] if note else [])))

        look()
        self.changed.connect(look)
        try:
            return dialog.exec() == int(QDialog.DialogCode.Accepted) and not busy()
        finally:
            self.changed.disconnect(look)
            dialog.deleteLater()


class BusyBar(QWidget):
    """A thin track with a block sliding across it, left to right, for as
    long as it shows.  Drawn here rather than by a QProgressBar, whose busy
    animation runs at a pace of the style's choosing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(BAR_WIDTH, BAR_HEIGHT)
        self._at = 0.0
        self._sweep = QVariantAnimation(self)
        self._sweep.setStartValue(0.0)
        self._sweep.setEndValue(1.0)
        self._sweep.setDuration(SWEEP_MS)
        self._sweep.setLoopCount(-1)
        self._sweep.valueChanged.connect(self._move)

    def _move(self, at) -> None:
        self._at = float(at)
        self.update()

    # Moving only while it can be seen: a hidden bar costs nothing.
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sweep.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sweep.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = track.height() / 2
        outline = QPainterPath()
        outline.addRoundedRect(track, radius, radius)
        painter.fillPath(outline, QColor(theme.colour("surface")))
        painter.setClipPath(outline)
        block = self.width() * BLOCK_SHARE
        left = -block + self._at * (self.width() + block)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(left, 0, block, self.height()), radius, radius)
        painter.fillPath(shape, QColor(theme.colour("accent")))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(theme.colour("border")), 1))
        painter.drawPath(outline)


class BackgroundIndicator(QWidget):
    """The status bar's right-hand corner: what is running, or that
    nothing is."""

    def __init__(self, tasks: BackgroundTasks, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("")
        row.addWidget(self.label)
        # Moving while anything runs: seen from the corner of an eye, which
        # a sentence is not.
        self.bar = BusyBar()
        row.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        tasks.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        running = self.tasks.running()
        self.bar.setVisible(bool(running))
        if not running:
            self.label.setText(tr("No background work running"))
            self.setToolTip(tr(
                "Everything the Studio reads or checks behind the window "
                "has finished."
            ))
            return
        self.label.setText(
            running[0] if len(running) == 1
            # Translators: %(task)s is what is running, as in "Reading 3
            # mods again…"; %(n)s is how many other things are as well.
            else trn("%(task)s (and %(n)s more)", "%(task)s (and %(n)s more)",
                     len(running) - 1, task=running[0])
        )
        self.setToolTip("\n".join(running))
