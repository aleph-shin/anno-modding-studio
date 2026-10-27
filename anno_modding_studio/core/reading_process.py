"""A reading of the stack in a process of its own.

The reading that follows an edit (:mod:`core.stale`) is a minute of Python
on a long mod list.  On a thread of the window's process it shares the
interpreter with the window, turn about, and everything done meanwhile
takes about twice as long.  A process has an interpreter of its own.

Both sides are here.  :class:`Reading` is the window's: it starts the
process, sends it a :class:`Request`, and waits for the answer.
:func:`main` is the process's: it reads the one request, reads the mods
again, and says what it found.  Each message either way is one pickle on a
pipe: both ends are this code at the same commit, started by the window.

**One process for each reading**, gone when the reading ends.  It starts in
a third of a second, it is stopped by ending it, and the gigabyte a reading
of a long list takes goes back to the system with it.

**Except during its sweep.**  The sweep of every asset for the mods'
searches is half a minute on a new index, and its answers are worth more
than the reading: every later reading asks the same searches.  So the
process says when a sweep starts and what it found when it ends, and a
reading stopped between the two is ended only when the answers are in --
unless the window is closing.  The window's index keeps them
(:meth:`.index.AssetIndex.keep_searches`) and hands them to the next
process, which then has no sweep to make -- and writes them beside its
file, for the first reading of the next launch.  The process's own index
is opened read-only, and neither reads nor writes them there.

**It does not outlive the window.**  Nothing is written to the process's
standard input after the request, so the pipe closing means the window has
gone, however it went, and the process ends itself.

Nothing here imports Qt, and nothing the process imports does: that is why
it starts as quickly as it does.  See docs/proposals/separate-process-proposal.md.
"""

from __future__ import annotations

import contextlib
import os
import pickle
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# What the Studio's own entry point is started with to be this process and
# not the window: ``__main__`` looks for it before it imports anything of Qt.
# The same from source and from a frozen build, where there is no ``-m``.
ARGUMENT = "--reading-process"

# The messages the process sends, each a tuple that starts with one of these.
SWEEPING = "sweeping"   # a sweep for the mods' searches has begun
SEARCHES = "searches"   # it is over: {expression: [guid, ...]} for each new one
DONE = "done"           # the answer: {origin: stale.Answer or modfile.Reread}
FAILED = "failed"       # what went wrong, as a sentence, and the traceback


@dataclass
class Request:
    """Everything a reading asks of the window, to be sent to the process."""

    # What to read: a :class:`.stale.Snapshot`.
    taken: Any
    # The index to open, and the game file it was built from.
    db_path: str = ""
    source_path: str = ""
    # The order the game's loader logged (:func:`.loaderlog.logged_order`).
    logged: List[str] = field(default_factory=list)
    # What the window's index knows that a new one over the same file does
    # not: its search answers, and the names the loaded mods give things.
    searches: Dict[str, List[str]] = field(default_factory=dict)
    names: Any = None
    # The language the window is in, for whatever a reading says in words.
    language: str = ""


def request_for(taken, index, logged=None) -> Request:
    """The request for a reading of ``taken`` against the window's ``index``."""
    from . import i18n

    return Request(
        taken=taken,
        db_path=str(index.db_path),
        source_path=str(index.source_path),
        logged=list(logged or []),
        searches=index.kept_searches(),
        names=index.mod_names,
        language=i18n.language(),
    )


# ------------------------------------------------------------- the window's side
class Stopped(Exception):
    """The reading was stopped: there is no answer and nothing went wrong."""


class Died(Exception):
    """The process could not be started, or ended without an answer though
    nobody stopped it.  The reading can still be done here."""


class Failed(Exception):
    """The reading itself went wrong, as it would have on a thread: what
    the reader said, as a sentence."""


def command() -> List[str]:
    """What starts the process: the Studio itself, with :data:`ARGUMENT`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, ARGUMENT]
    return [sys.executable, "-m", __name__.split(".")[0], ARGUMENT]


def _environment() -> Dict[str, str]:
    """The window's own, so that every ``ANNO_STUDIO_*`` setting reaches the
    process, with the package findable from wherever the window was started.
    A built Studio carries its package inside it."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        return env
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (root, env.get("PYTHONPATH", "")) if part
    )
    return env


def _flags() -> int:
    """Below the window in priority, which between two processes means what
    it says, and with no console of its own."""
    if sys.platform != "win32":
        return 0
    return subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW


class Reading:
    """One reading in a process of its own, from the window's side.

    :meth:`run` blocks until the answer is in, on whichever thread calls it;
    :meth:`stop` is for any other thread.
    """

    def __init__(self, request: Request) -> None:
        self.request = request
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._stopped = threading.Event()
        self._sweeping = False

    def stop(self, now: bool = False) -> None:
        """End the reading: at once, unless it is in its sweep and ``now``
        is not set, in which case when the sweep has said what it found."""
        with self._lock:
            self._stopped.set()
            if self._process is not None and (now or not self._sweeping):
                self._process.kill()

    def run(self, searched: Optional[Callable[[dict], None]] = None) -> dict:
        """The answer: each mod's reading by origin.

        ``searched`` is handed the answers of a sweep as soon as it is over,
        whether or not the reading goes on to finish.  Raises
        :class:`Stopped`, :class:`Failed` or :class:`Died`.
        """
        with self._lock:
            if self._stopped.is_set():
                raise Stopped()
            try:
                self._process = subprocess.Popen(
                    command(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    # A window with no console has nowhere for it to go.
                    stderr=None if sys.stderr is not None else subprocess.DEVNULL,
                    env=_environment(),
                    creationflags=_flags(),
                )
            except OSError as exc:
                raise Died("%s: %s" % (type(exc).__name__, exc)) from exc
        process = self._process
        assert process.stdin is not None and process.stdout is not None
        try:
            pickle.dump(self.request, process.stdin, pickle.HIGHEST_PROTOCOL)
            process.stdin.flush()
            while True:
                kind, *told = pickle.load(process.stdout)
                if kind == SWEEPING:
                    with self._lock:
                        self._sweeping = True
                elif kind == SEARCHES:
                    if searched is not None:
                        searched(told[0])
                    with self._lock:
                        self._sweeping = False
                        if self._stopped.is_set():
                            process.kill()
                elif kind == DONE:
                    return told[0]
                elif kind == FAILED:
                    raise Failed(told[0])
        except (EOFError, OSError, ValueError, pickle.UnpicklingError) as exc:
            # The pipe closed under it: ended here, or gone by itself.
            if self._stopped.is_set():
                raise Stopped() from None
            raise Died(
                "the reading's process ended with no answer (%s)" % type(exc).__name__
            ) from exc
        finally:
            self._end(process)

    @staticmethod
    def _end(process: subprocess.Popen) -> None:
        """Whatever happened, the process is gone when this returns."""
        if process.poll() is None:
            process.kill()
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        process.wait()


# ------------------------------------------------------------ the process's side
def _end_with_the_window(source) -> None:
    """Wait for the window's end of the pipe to close, and go with it."""
    with contextlib.suppress(OSError, ValueError):
        source.read(1)
    os._exit(0)


def main(argv=None) -> int:
    """Read the one request on standard input, read the mods again, and say
    what was found on standard output."""
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    # Nothing but messages goes down the pipe, whatever else prints.
    sys.stdout = sys.stderr

    def say(*message) -> None:
        pickle.dump(message, sink, pickle.HIGHEST_PROTOCOL)
        sink.flush()

    try:
        request: Request = pickle.load(source)
    except EOFError:
        # The window went before it had asked anything.
        return 0
    threading.Thread(target=_end_with_the_window, args=(source,), daemon=True).start()
    try:
        from . import i18n, stale
        from .index import AssetIndex

        i18n.install(request.language)
        index = AssetIndex(
            Path(request.db_path), Path(request.source_path) if request.source_path else None,
            read_only=True,
        )
        index.keep_searches(request.searches)
        if request.names is not None:
            index.set_mod_names(request.names)
        told = set(request.searches)
        sweep = {"on": False, "over": False}

        def tell_searches() -> None:
            found = {
                path: guids for path, guids in index.kept_searches().items()
                if path not in told
            }
            told.update(found)
            say(SEARCHES, found)

        def swept(position: int, total: int) -> None:
            # A sweep with nothing to look for says (1, 1) and no more.
            if position == 0 and not sweep["on"]:
                sweep["on"] = True
                say(SWEEPING)
            elif position >= total and sweep["on"]:
                # What it found is on the index by the next step.
                sweep["over"] = True

        def step(*_told) -> None:
            if sweep["over"]:
                sweep["on"] = sweep["over"] = False
                tell_searches()

        answers = stale.read_again(
            request.taken, index, request.logged,
            progress=step, translating=step, searching=swept,
        )
        if sweep["on"]:
            tell_searches()
        say(DONE, answers)
    except Exception as exc:  # noqa: BLE001 - said to the window, which shows it
        traceback.print_exc()
        say(FAILED, "%s: %s" % (type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
