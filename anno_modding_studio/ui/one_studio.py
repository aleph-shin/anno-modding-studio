"""One Studio open over one set of the Studio's folders.

Two open over the same folders work over each other: the settings are one
file and the last to close wins, both build and prune the same cache, both
convert into the same library, and both junction mods into the same mods
folder with neither knowing what the other did.

The lock is on the folder of what the Studio keeps for itself
(``places.local_home``).  Ordinarily that is one for the machine; a run set
apart with ``ANNO_STUDIO_FOLDER`` -- a hands-on test beside your own Studio --
has a folder of its own and a lock of its own.  It is the operating system's
lock on an open file, so it goes with the process however that ends: a crash
leaves nothing to clear, and a process that reuses the number is not taken
for the Studio.

A second launch hands over to the first: the first comes to the front, opens
the file the second was given if it was given one, and the second ends.
Nothing here depends on how the Studio was started, so a frozen build locks
and hands over the same way.  Two versions unzipped side by side keep their
data in the same folders, so they share the lock too: the first answers with
its version, and a second of another version says which is open rather than
ending as though it had started (:func:`say_another_version_is_open`).

The Studio that holds the lock asks one more thing before it opens a window:
whether a newer version has kept its folders in a way this one would misread
(:func:`may_use_data`, over ``core.dataversion``).  Then it does not open.
Where it does, it first settles any save the last Studio left half done
(:func:`finish_saves`, over ``core.saving``).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QMessageBox

from .. import __version__
from ..core import dataversion, packaged, places, saving, wine
from ..core.changes import APP_NAME
from ..core.i18n import tr

LOCK_NAME = "open.lock"
# How long a second launch keeps trying the first, which may have taken the
# lock a moment ago and not be listening yet.
WAIT_MS = 5000
_STEP_MS = 200
# The most the first is read for in answer: a version, and a line's end.
_MOST_SAID = 64
# And the most a second launch is read for: a file's path, and a line's end.
MOST_HANDED = 32 * 1024


def lock_path() -> Path:
    return places.local_home() / LOCK_NAME


class Lock:
    """The lock on one set of the Studio's folders, held while it is open."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Optional[int] = None

    def take(self) -> bool:
        """Take it; False only where another Studio holds it.

        A folder that cannot be written is not a Studio already open: the
        Studio opens unlocked, and says what it cannot write where it tries.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
        except OSError:
            return True
        try:
            _lock(file)
        except OSError:
            os.close(file)
            return False
        self._file = file
        return True

    @property
    def held(self) -> bool:
        return self._file is not None

    def release(self) -> None:
        if self._file is None:
            return
        with contextlib.suppress(OSError):
            _unlock(self._file)
        os.close(self._file)
        self._file = None


def _lock(file: int) -> None:
    os.lseek(file, 0, os.SEEK_SET)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(file, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file: int) -> None:
    os.lseek(file, 0, os.SEEK_SET)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(file, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_UN)


def server_name(path: Path) -> str:
    """What the Studio holding ``path`` listens on: one name for each lock,
    the same however the folder is spelt."""
    with contextlib.suppress(OSError):
        path = path.resolve()
    digest = hashlib.sha1(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
    return "%s-%s" % (places.NAME, digest[:16])


def listen(name: str, on_message: Callable[[str], None], parent=None) -> QLocalServer:
    """Wait for a second launch's hand-over, and give ``on_message`` what it
    sent: a file to open, or "" for only coming to the front."""
    server = QLocalServer(parent)
    # Only this user may connect: what is handed over is opened, a loadout
    # among them (security review S12, N3).
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    # Left by one that crashed, where a name is a file (not on Windows).
    QLocalServer.removeServer(name)
    server.listen(name)

    def arrived() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            received = bytearray()

            def read(socket=socket, received=received) -> None:
                received.extend(bytes(socket.readAll()))
                if len(received) > MOST_HANDED:
                    # No path is that long: not a second launch, so not
                    # read any further, nor answered.
                    received.clear()
                    socket.readyRead.disconnect()
                    socket.abort()
                    return
                if received.endswith(b"\n"):
                    # Which Studio this is: the second may be another
                    # version, and should not end as though it were this.
                    socket.write((__version__ + "\n").encode("utf-8"))
                    socket.flush()
                    socket.disconnectFromServer()
                    on_message(received[:-1].decode("utf-8", "replace"))

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            read()

    server.newConnection.connect(arrived)
    return server


def hand_over(name: str, message: str = "", wait_ms: int = WAIT_MS) -> Optional[str]:
    """Ask the Studio listening on ``name`` to come to the front and open
    ``message``, if it names a file: the version it said it is, "" where it
    said none, and None where it did not answer.

    Two versions keep their data in the same folders, so the one already
    open is the one the new one hands over to, after an update too."""
    # Windows lets only the program in front put another in front: this one
    # was just started, and lends it.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:  # noqa: BLE001 - the first only flashes on the taskbar
            pass
    socket = QLocalSocket()
    waited = 0
    while True:
        socket.connectToServer(name)
        if socket.waitForConnected(_STEP_MS):
            break
        socket.abort()
        if waited >= wait_ms:
            return None
        QThread.msleep(_STEP_MS)
        waited += _STEP_MS
    socket.write((message + "\n").encode("utf-8"))
    # False too where the write was done before the wait began, as a short
    # one into a pipe often is on a busy machine: only what is still unwritten
    # is a failure.
    if not socket.waitForBytesWritten(1000) and socket.bytesToWrite():
        socket.disconnectFromServer()
        return None
    answer = bytearray()
    while not answer.endswith(b"\n") and len(answer) < _MOST_SAID:
        if not socket.waitForReadyRead(1000):
            break
        answer.extend(bytes(socket.readAll()))
    answer.extend(bytes(socket.readAll()))
    socket.disconnectFromServer()
    said = answer[:_MOST_SAID].decode("utf-8", "replace").strip()
    # Shown in a sentence, and from whoever listens on the name: only what
    # a version looks like.
    return said if re.fullmatch(r"[0-9A-Za-z.+-]{1,32}", said) else ""


def say_another_version_is_open(version: str) -> None:
    """The second launch's word where the Studio already open is another
    version: it came to the front, and this one does not start."""
    QMessageBox.information(
        None,
        APP_NAME,
        tr("%(app)s %(open)s is already open, so %(this)s did not start. "
           "Close that one, then start the Studio again.",
           app=APP_NAME, open=version, this=__version__),
    )


def come_forward(window) -> None:
    """Put ``window`` in front, out of the taskbar if it was minimised."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()


def may_use_data() -> bool:
    """Whether this Studio may start over its folders, which it then says
    it has used; where a newer data version has used one, False, once that
    is said.

    Asked with the lock held, so no other Studio over the same folders is
    writing its own stamp meanwhile.
    """
    newer = dataversion.newer_than_this()
    if newer is not None:
        QMessageBox.critical(None, APP_NAME, dataversion.refusal(newer))
        return False
    dataversion.record()
    return True


def finish_saves() -> None:
    """Complete or undo every save the last Studio left half done
    (``core.saving``), and say where one could not be.

    Asked with the lock held and before anything is opened, so nothing
    reads the files meanwhile or writes them.
    """
    problems = saving.recover()
    if problems:
        QMessageBox.warning(None, APP_NAME, "\n\n".join(problems))


def say_if_beside_is_unused() -> None:
    """Say so where a built Studio finds a folder for all its data beside
    it, which it does not use (``places.unused_beside``): the folder was
    made for it, and its data is not where whoever made it thinks."""
    beside = places.unused_beside()
    if beside is None:
        return
    QMessageBox.information(
        None,
        APP_NAME,
        tr("The Studio keeps its data in your usual folders, and leaves the "
           "folder beside it unused:\n\n%s\n\n"
           "To keep everything there, set the environment variable "
           "ANNO_STUDIO_FOLDER to it. To stop this message, rename or remove "
           "that folder.", str(beside)),
    )


# The Wine a Studio last said made no junctions, so it says so once for each.
WINE_TOLD = "wineToldNoJunctions"


def say_if_wine_makes_no_junctions(settings) -> None:
    """Say so where the Studio runs under a Wine too old to make the links it
    puts in a mods folder (``core.wine``), once for each such Wine: before
    anything is installed, which is where it would first fail."""
    note = wine.junctions_note()
    if not note or str(settings.value(WINE_TOLD, "") or "") == wine.version():
        return
    QMessageBox.warning(None, APP_NAME, note)
    settings.setValue(WINE_TOLD, wine.version())
    settings.sync()


def carry_on_if_kept_in_a_package() -> bool:
    """Say so where Windows keeps the Studio's own files in a packaged app's
    private folder (:mod:`core.packaged`), at every start, and ask whether to
    go on; whether it does.  Every link it made into a mods folder would
    point at nothing for the game."""
    said = packaged.note()
    if not said:
        return True
    box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, said)
    close = box.addButton(tr("&Close the Studio"), QMessageBox.ButtonRole.RejectRole)
    box.addButton(tr("Carry &on anyway"), QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(close)
    box.exec()
    return box.clickedButton() is not close


def say_already_open() -> None:
    """The second launch's word, where the first did not answer it."""
    QMessageBox.information(
        None,
        APP_NAME,
        tr("%s is already open, and did not come to the front when asked. If it "
           "has stopped responding, end it in Task Manager and start the Studio "
           "again.", APP_NAME),
    )
