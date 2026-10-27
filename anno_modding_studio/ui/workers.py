"""Background workers so a multi-hundred-megabyte parse never freezes the UI."""

from __future__ import annotations

import os
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core import index as index_mod
from ..core.i18n import STORED, tr_noop

# What a signal carries a count of bytes as. ``int`` is a C++ int, 32 bits:
# a download or a backup past 2 GB came out the other side wrapped, negative
# or short, before ``widgets.show_progress`` could scale it to the bar.
BYTES = "qint64"


class IndexWorker(QThread):
    """Builds the SQLite index for one assets.xml."""

    progress = Signal(BYTES, BYTES, int)  # bytes read, total bytes, assets found
    succeeded = Signal(str, int)  # db path, asset count
    failed = Signal(str)

    def __init__(self, source: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self.source = Path(source)
        self.db_path = Path(db_path)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            count = index_mod.build_index(
                self.source,
                self.db_path,
                progress=lambda pos, total, n: self.progress.emit(pos, total, n),
                should_cancel=lambda: self._cancel,
            )
        except index_mod.IndexCancelled:
            self.failed.emit("cancelled")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            traceback.print_exc()
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            self.succeeded.emit(str(self.db_path), count)


class TextsWorker(QThread):
    """Imports a texts_*.xml into an already-open index."""

    succeeded = Signal(int)
    failed = Signal(str)

    def __init__(self, index, texts_path: Path, parent=None):
        super().__init__(parent)
        self.index = index
        self.texts_path = Path(texts_path)

    def run(self) -> None:
        try:
            count = self.index.load_texts(self.texts_path)
        except Exception as exc:  # noqa: BLE001 - a worker reports whatever went wrong
            traceback.print_exc()
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            self.succeeded.emit(count)


class _Cancelled(Exception):
    """A read nobody wants any more, stopped where it next said where it was."""


class ModsWorker(QThread):
    """Reads a whole mods folder as the one stack the game will load.

    Off the main thread because it is not a quick job: sixty-five mods for
    Anno 1800 is eleven hundred ModOps read against a hundred thousand assets,
    and the window has to stay answerable while it happens.
    """

    progress = Signal(int, int, str)  # position, total, mod being read
    # Assets looked at, out of all of them: a mod may target by search rather
    # than by GUID, and answering that means reading the whole file once.
    searching = Signal(int, int)
    # Reading the folder is only the first half; turning what was read into
    # changes is the other, and it used to happen behind a full progress bar
    # with nothing said.
    translating = Signal(int, int)
    stage = Signal(str)
    succeeded = Signal(object)  # a reader.SessionReport
    failed = Signal(str)

    def __init__(
        self, index, folders, keep_inert: bool = False, reading=None, parent=None,
        game: str = "", logged=None, roots=None, unlinked=None,
    ):
        super().__init__(parent)
        self.index = index
        # Which game, because load order is not the same question for both:
        # 1800 goes by folder name, 117 by the category and name in the modinfo.
        self.game = game
        # What the game's own mod loader recorded last time it ran, which beats
        # any rule for working the order out from the outside.
        self.logged = logged
        # The mods directories the folders came out of, so a mod can be named by
        # where it sits: a mod bundled inside another needs its host in its name.
        self.roots = [Path(root) for root in (roots or [])]
        self.folders = [Path(folder) for folder in folders]
        # Library mods among them with no link in the mods folder: the folder
        # each is kept in -> the name its link goes by.
        self.unlinked = dict(unlinked or {})
        self.keep_inert = keep_inert
        # How to read them: what each mod's own settings are set to, and whether
        # values a mod computes are settled into fixed ones.
        self.reading = reading
        self._cancelled = False

    def cancel(self) -> None:
        """Stop reading at the next step it reports: nobody wants the answer.
        A read is hundreds of small steps, each of which says where it is, so
        that is where it can be told to stop."""
        self._cancelled = True

    def _said(self, signal, *told) -> None:
        if self._cancelled:
            raise _Cancelled()
        signal.emit(*told)

    def run(self) -> None:
        from ..core import reader

        try:
            session = reader.analyse_all(
                self.folders,
                self.index,
                keep_inert_removals=self.keep_inert,
                progress=lambda pos, total, name: self._said(self.progress, pos, total, name),
                searching=lambda pos, total: self._said(self.searching, pos, total),
                translating=lambda pos, total: self._said(self.translating, pos, total),
                stage=lambda text: self._said(self.stage, text),
                reading=self.reading,
                game=self.game,
                logged=self.logged,
                roots=self.roots,
                unlinked=self.unlinked,
            )
        except _Cancelled:
            # Nobody is waiting for it: nothing to say, to anybody.
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            traceback.print_exc()
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            if not self._cancelled:
                self.succeeded.emit(session)


class RereadWorker(QThread):
    """Reads the mods whose reading is in doubt again, where each stands in
    the stack: see :mod:`core.stale`.

    From a copy of the stack taken when it was asked, since the loadout goes
    on being worked in meanwhile; the copy comes back with the answer, so
    whoever takes it can tell whether it is still the question.

    The reading itself is done in a process of its own
    (:mod:`core.reading_process`), and this thread only waits for it: a
    minute of Python on a thread of the window's takes turns with the window
    for the interpreter.  It is done here instead where
    ``ANNO_STUDIO_REREAD=thread`` says so, which is how the tests about the
    reading run, and where the process cannot be started or dies.
    """

    # The copy asked of, each mod's reading by origin, and what went wrong.
    # No readings and nothing wrong is a reading that was stopped.
    done = Signal(object, object, str)
    # The reading was done on this thread after all, and why.
    fell_back = Signal(str)

    # How many processes in a row failed, over every reading of the session,
    # and how many it takes before no more are tried.
    failures = 0
    GIVE_UP = 2

    def __init__(self, taken, index, logged=None, parent=None):
        super().__init__(parent)
        self.taken = taken
        self.index = index
        self.logged = logged
        self._cancelled = False
        self._closing = False
        self._apart = None

    @classmethod
    def in_process(cls) -> bool:
        """Whether a reading is given a process of its own."""
        asked = os.environ.get("ANNO_STUDIO_REREAD", "").strip().lower()
        return asked not in ("none", "thread") and cls.failures < cls.GIVE_UP

    def cancel(self, closing: bool = False) -> None:
        """Stop the reading: its process ended at once, or on this thread at
        the next step it reports.

        Not in the sweep for the mods' searches, unless the window is
        ``closing``: a sweep is half a minute on a long list, what it finds
        is kept on the index only once it is whole, and the reading that is
        wanted instead asks the same searches.  Stopped half way at every
        edit, somebody who goes on editing would never see one finish.
        """
        self._cancelled = True
        self._closing = self._closing or closing
        apart = self._apart
        if apart is not None:
            apart.stop(now=self._closing)

    def _said(self, *_told) -> None:
        if self._cancelled:
            raise _Cancelled()

    def _swept(self, *_told) -> None:
        if self._closing:
            raise _Cancelled()

    def run(self) -> None:
        from ..core import reading_process

        try:
            reports = self._read()
        except _Cancelled:
            self.done.emit(self.taken, None, "")
        except reading_process.Failed as exc:
            # Said by the process, as a reading here would have said it.
            self.done.emit(self.taken, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - said on the rows, verbatim
            traceback.print_exc()
            self.done.emit(self.taken, None, "%s: %s" % (type(exc).__name__, exc))
        else:
            self.done.emit(self.taken, None if self._cancelled else reports, "")

    def _read(self) -> dict:
        from ..core import reading_process, stale

        if self.in_process():
            try:
                reports = self._read_apart()
            except reading_process.Stopped:
                raise _Cancelled() from None
            except reading_process.Died as exc:
                # Slower here, and not wrong.
                type(self).failures += 1
                if self._cancelled:
                    raise _Cancelled() from None
                self.fell_back.emit(str(exc))
            else:
                type(self).failures = 0
                return reports
        return stale.read_again(
            self.taken, self.index, self.logged,
            progress=self._said, translating=self._said, searching=self._swept,
        )

    def _read_apart(self) -> dict:
        from ..core import reading_process

        apart = reading_process.Reading(
            reading_process.request_for(self.taken, self.index, self.logged)
        )
        self._apart = apart
        if self._cancelled:
            # Stopped between being started and getting this far.
            apart.stop(now=self._closing)
        # What a sweep found is the window's index's to keep, for the next
        # reading and for whatever else asks the same search.
        return apart.run(searched=self.index.keep_searches)


class UpdatesWorker(QThread):
    """Asks mod.io which of the loaded mods have a newer file.

    Off the main thread because it is network: one request per hundred mods
    already matched, then one per mod that has to be looked up by name, each
    paced to the key's sixty a minute.  ``cancel`` stops it between requests.
    """

    progress = Signal(int, int, str)  # done, total, what is being looked up
    checked = Signal(object)  # one updates.Verdict, as soon as it is decided
    succeeded = Signal(object)  # a list of updates.Verdict
    failed = Signal(str)

    def __init__(self, key: str, game_id: int, matches, parent=None, fetch=None):
        super().__init__(parent)
        from ..core import modio

        self.game_id = game_id
        self.matches = matches
        self._stop = False
        self.client = modio.Client(
            key,
            fetch=fetch or modio.transport,
            cancelled=lambda: self._stop,
            game_id=game_id,
        )

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..core import modio, updates

        verdicts = []
        try:
            for verdict in updates.check_each(
                self.client,
                self.game_id,
                self.matches,
                progress=lambda done, total, name: self.progress.emit(done, total, name),
            ):
                verdicts.append(verdict)
                self.checked.emit(verdict)
        except modio.Cancelled:
            return
        except modio.ModioError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            traceback.print_exc()
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            self.succeeded.emit(verdicts)


class ModioTaskWorker(QThread):
    """One job against mod.io on a thread: a search, a download and unpack.

    The job is a function of the client, a ``say(done, total, text)`` to
    report progress with, and a ``stop()`` to ask between steps.  Whatever it
    returns is what ``succeeded`` carries.  Stopping is honoured between
    requests and between download chunks, and is not an error.

    A job that does several things in a row -- a queue of downloads -- says
    what became of each through ``settled``, which it reaches by the worker
    it is running on; nothing emits it otherwise.
    """

    progress = Signal(BYTES, BYTES, str)  # done, total: bytes, for a download
    settled = Signal(int, object)  # which one, and what became of it
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, key: str, job, parent=None, fetch=None):
        super().__init__(parent)
        from ..core import modio

        self._job = job
        self._stop = False
        self.client = modio.Client(
            key, fetch=fetch or modio.transport, cancelled=lambda: self._stop
        )

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..core import modio, modzip

        try:
            result = self._job(
                self.client,
                lambda done, total, text: self.progress.emit(done, total, text),
                lambda: self._stop,
            )
        except modio.Cancelled:
            return
        except (modio.ModioError, modzip.InstallError, OSError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            traceback.print_exc()
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        else:
            self.succeeded.emit(result)


class ZipInstallWorker(QThread):
    """Unpacks zips from disk into the library, one after another -- see
    :func:`core.library.install_zip`.  A mod can be hundreds of megabytes of
    meshes, so never on the window's own thread.

    A zip that fails does not stop the ones after it: ``done`` pairs each
    with what became of it, a :class:`core.library.LibraryInstallResult` or
    the error's text.
    """

    progress = Signal(int, int, str)  # position, total, the zip's name
    succeeded = Signal(object)  # the list ``done``

    def __init__(self, archives, library_dir, mods_folder, parent=None):
        super().__init__(parent)
        self.archives = [Path(archive) for archive in archives]
        self.library_dir = Path(library_dir)
        self.mods_folder = Path(mods_folder)

    def run(self) -> None:
        from ..core import library, modzip

        done: list = []
        for position, archive in enumerate(self.archives):
            self.progress.emit(position, len(self.archives), archive.name)
            try:
                done.append((archive, library.install_zip(archive, self.library_dir, self.mods_folder)))
            except (modzip.InstallError, OSError) as exc:
                done.append((archive, str(exc)))
            except Exception as exc:  # noqa: BLE001 - any zip may be dropped; said, not raised into the thread
                traceback.print_exc()
                done.append((archive, "%s: %s" % (type(exc).__name__, exc)))
        self.succeeded.emit(done)


class LibraryBackupWorker(QThread):
    """Zips library copies whole into the backups, one after another -- see
    :func:`core.library.back_up`.  A mod can be gigabytes of models, so
    never on the window's own thread.  ``cancel`` ends it between files,
    keeping the zips finished.

    ``done`` pairs each mod's folder name with the zip made of it, or the
    error's text: one that fails does not stop the ones after it.
    """

    progress = Signal(BYTES, BYTES, str)  # bytes done, bytes in all, the mod's name
    succeeded = Signal(object)  # the list ``done``

    def __init__(self, mods, parent=None):
        super().__init__(parent)
        self.mods = list(mods)  # (library folder, folder name, size in bytes)
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..core import library

        total = sum(size for _folder, _name, size in self.mods)
        before = 0
        done: list = []
        for folder, name, size in self.mods:
            if self._stop:
                break
            try:
                made = library.back_up(
                    folder, name, stop=lambda: self._stop,
                    progress=lambda now, _all, at=before, mod=name: self.progress.emit(at + now, total, mod),
                )
            except OSError as exc:
                done.append((name, str(exc)))
            else:
                if made is not None:
                    done.append((name, made))
            before += size
        self.succeeded.emit(done)


class LoaderCheckWorker(QThread):
    """Asks the game's loader what one mod does -- see core.loadercheck."""

    succeeded = Signal(object, object)  # the key it was asked under, a Verdict

    def __init__(self, key, job, index, loader, target=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.job = job
        self.index = index
        self.loader = loader
        self.target = target

    def run(self) -> None:
        from ..core import loadercheck

        try:
            verdict = loadercheck.run(
                self.job, self.index, self.loader, self.target,
                should_stop=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - a check that breaks is a verdict too
            traceback.print_exc()
            verdict = loadercheck.Verdict(
                state=loadercheck.FAILED, origin=self.job.origin, name=self.job.name,
                loader=self.loader, why="%s: %s" % (type(exc).__name__, exc),
            )
        self.succeeded.emit(self.key, verdict)


class ElsewhereWorker(QThread):
    """Which mods on this PC create each GUID -- see
    :func:`core.discovery.guid_owners`. Seconds the first time over a full
    mods folder, so never on the window's own thread."""

    succeeded = Signal(object)  # {guid: [Owner, ...]}

    def __init__(self, game, folders, library_dir, exclude=None, parent=None):
        super().__init__(parent)
        self.game = game
        self.folders = list(folders)
        self.library_dir = library_dir
        self.exclude = exclude

    def run(self) -> None:
        from ..core import discovery, games

        try:
            mods = discovery.mods_on_this_pc(self.game, self.folders, self.library_dir)
            owners = discovery.guid_owners(mods, games.get(self.game).asset_relpath, self.exclude)
        except Exception:  # noqa: BLE001 -- a courtesy check says nothing rather than break
            traceback.print_exc()
            owners = {}
        self.succeeded.emit(owners)


class UnreadEffectsWorker(QThread):
    """Asks the game's loader what each op one mod carries unread does --
    see core.loadercheck.explain_unread."""

    progressed = Signal(int, int)  # runs done, runs in all
    succeeded = Signal(str, object)  # the mod's origin, a list of UnreadEffects

    def __init__(self, origin, job, index, loader, parent=None):
        super().__init__(parent)
        self.origin = origin
        self.job = job
        self.index = index
        self.loader = loader

    def run(self) -> None:
        from ..core import loadercheck

        try:
            effects = loadercheck.explain_unread(
                self.job, self.index, self.loader,
                progress=self.progressed.emit, should_stop=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - said on each row rather than lost
            traceback.print_exc()
            why = "%s: %s" % (type(exc).__name__, exc)
            effects = [loadercheck.UnreadEffect(entry.order, entry.source, why=why)
                       for entry in self.job.view.unread if loadercheck.is_an_op(entry)]
        self.succeeded.emit(self.origin, effects)


class LoadoutCheckWorker(QThread):
    """Checks every loading mod at once with the game's loader -- see
    core.loadercheck.check_loadout."""

    progressed = Signal(str)
    succeeded = Signal(object)  # a LoadoutVerdict

    def __init__(self, job, index, loader, parent=None):
        super().__init__(parent)
        self.job = job
        self.index = index
        self.loader = loader

    def run(self) -> None:
        from ..core import loadercheck

        try:
            verdict = loadercheck.check_loadout(
                self.job, self.index, self.loader,
                progress=self.progressed.emit, should_stop=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - a check that breaks is a verdict too
            traceback.print_exc()
            verdict = loadercheck.LoadoutVerdict(state=loadercheck.FAILED, loader=self.loader,
                                                 why="%s: %s" % (type(exc).__name__, exc))
        self.succeeded.emit(verdict)


class NewestLoaderWorker(QThread):
    """Asks the Marketplace which version of Anno Modding Tools is newest --
    see core.xmltest.newest_published."""

    succeeded = Signal(object)  # a Published, or None when it could not be asked

    def run(self) -> None:
        from ..core import xmltest

        try:
            published = xmltest.newest_published()
        except (OSError, ValueError):
            # Offline, or the Marketplace changed its answer: nothing to say.
            published = None
        except Exception:  # noqa: BLE001 - an answer cut short (IncompleteRead) is no answer either
            traceback.print_exc()
            published = None
        self.succeeded.emit(published)


class StudioReleaseWorker(QThread):
    """Asks GitHub whether a newer Studio is out -- see
    core.studio_release.check, which never raises for what the network
    does."""

    succeeded = Signal(object)  # a studio_release.Answer

    def run(self) -> None:
        from ..core import studio_release

        try:
            answer = studio_release.check()
        except Exception as exc:  # noqa: BLE001 - a check that breaks is an answer too
            traceback.print_exc()
            answer = studio_release.Answer(
                studio_release.UNCHECKED, reason=studio_release.UNREADABLE,
                why="%s: %s" % (type(exc).__name__, exc))
        self.succeeded.emit(answer)


class ProbeWorker(QThread):
    """Runs the drift probes against one game's loader."""

    succeeded = Signal(object, object)  # the Loader, a list of ProbeResults

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader

    def run(self) -> None:
        from ..core import loadercheck

        try:
            results = loadercheck.probe(self.loader)
        except Exception as exc:  # noqa: BLE001 - a failed run is reported as a result
            traceback.print_exc()
            probe = loadercheck.Probe(tr_noop("running the probes", context=STORED), self.loader.game, "")
            results = [loadercheck.ProbeResult(probe, None, "%s: %s" % (type(exc).__name__, exc))]
        self.succeeded.emit(self.loader, results)
