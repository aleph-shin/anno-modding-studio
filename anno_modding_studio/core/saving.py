"""Saving several files at once: all of them, or after any failure none.

Each file the Studio keeps is already written whole: ``disk.write_bytes``
writes it beside itself and moves it over.  A save that changes several -- a
loadout and the projects of the mods it changed -- needs more than that, since
a failure between two of them would leave one saved and the other not.  So a
save of several files goes in four steps (section 6 of
``docs/proposals/projects-and-loadouts-proposal.md``):

1. **Write** each file as a ``.part`` beside it, flushed to the disk.  Where
   one fails, the ``.part`` files go and nothing has changed.
2. **Commit**: write the journal, which lists for each file its target, its
   ``.part`` and an ``.old`` name for what is there now.  Once the journal is
   on the disk, the save happens.
3. **Swap**: move each target to its ``.old``, then its ``.part`` to the
   target.  A move something holds -- an antivirus scan, a sync tool -- is
   tried again for a moment.  Where one still fails, every file is put back
   as it was.
4. **Finish**: delete the journal, then the ``.old`` files.

A Studio that stops between 2 and 4 leaves the journal, and the next one
settles the save when it starts (:func:`recover`), under the one-Studio lock
and before it opens anything.  It completes the save where every file can
still go forward, and undoes it where one cannot: its ``.part`` is gone and
it is not in place.  That is also what tells an undo that stopped half way
from a swap that did, so a journal is written once and never changed.  The
journal goes before the ``.old`` files for the same reason: with them gone
and the journal there, a saved file would look like one put back.

A save can also take a file away: a project that follows its mod's new name
is written under the new one and goes from the old one, in the same save.
A file taken away has no ``.part``.  Its swap moves it to its ``.old``, and
finishing deletes that, so an undo puts it back as it puts back a file
written over.

A save of one file needs none of it: moving one ``.part`` over its target is
one step already, so :func:`write_all` writes it as ``disk.write_bytes``
does.

The journals are kept in the Studio's own folder (``places.local_home``),
which no sync tool reaches, under :data:`JOURNALS`.  Each is named after the
process that wrote it, so a save first settles any its own process left,
which a later save of the same file must not be overtaken by.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

from . import disk, places, records
from .i18n import tr

# The format of a save's journal, which is stamped with it (see
# :mod:`core.records`).  Bump it in the change that gives the journal a
# meaning an earlier build would misread: that build then leaves it alone.
# 2: a file can be taken away (``removed``), which format 1 would have read
# as a file whose ``.part`` was lost, and undone the save.
JOURNAL_FORMAT = 2
# The folder of the Studio's local one the journals are kept in.  Frozen: a
# later Studio looks there for what an earlier one left.
JOURNALS = "saving"
# The files a journal lists, under this key, each with these, and with
# ``removed`` where it is taken away.
FILES = "files"
ENTRY_KEYS = ("target", "part", "old", "existed")
REMOVED = "removed"
# How long a move something holds waits before each try after the first, in
# seconds: a second and a half in all.
WAITS: Tuple[float, ...] = (0.05, 0.1, 0.2, 0.4, 0.8)


class Unfinished(OSError):
    """A save that could neither be completed nor undone, with the sentence
    saying so.  Its journal is left, for the next try."""


@dataclass(frozen=True)
class Entry:
    """One file of a save: where it goes, where it waits until then, where
    what is there now goes meanwhile, and whether anything was there.  Or,
    ``removed``, a file the save takes away, which has no ``.part``."""

    target: Path
    part: Path
    old: Path
    existed: bool
    removed: bool = False

    def to_dict(self) -> dict:
        return {"target": str(self.target), "part": str(self.part),
                "old": str(self.old), "existed": self.existed,
                **({REMOVED: True} if self.removed else {})}


def journal_dir() -> Path:
    return places.local_home() / JOURNALS


# -- saving ---------------------------------------------------------------------------
def write_all(files: Mapping[Any, Optional[bytes]]) -> None:
    """Put each of ``files``, a path and what it is to hold, on the disk,
    and take away each whose bytes are None: all of them, or after any
    failure none, each left as it was.

    Raises the OSError that stopped it, once everything is as it was; or
    :class:`Unfinished` where it could not be put back either, which the
    next save or start puts right.
    """
    finish_own()
    if len(files) == 1:
        path, data = next(iter(files.items()))
        if data is None:
            with contextlib.suppress(FileNotFoundError):
                _again(os.remove, _target(path))
        else:
            disk.write_bytes(path, data)
        return
    token = "%d-%s" % (os.getpid(), uuid.uuid4().hex[:12])
    entries: List[Entry] = []
    seen = set()
    for path, data in files.items():
        target = _target(path)
        place = os.path.normcase(os.path.abspath(target))
        if place in seen:
            raise ValueError("%s is saved twice" % target)
        seen.add(place)
        entries.append(Entry(
            target=target,
            part=target.with_name("%s.%s.part" % (target.name, token)),
            old=target.with_name("%s.%s.old" % (target.name, token)),
            existed=target.exists(),
            removed=data is None,
        ))
    written: List[Entry] = []
    try:
        for entry, data in zip(entries, files.values(), strict=True):
            if data is None:
                continue
            written.append(entry)
            disk.write_flushed(entry.part, data)
        journal = write_journal(entries, token)
    except BaseException:
        for entry in written:
            with contextlib.suppress(OSError):
                entry.part.unlink()
        raise
    settle(journal, entries)


def _target(path) -> Path:
    # As disk.write_bytes does: a link is written through.
    target = Path(path)
    if target.is_symlink():
        target = target.resolve()
    return target.absolute()


def write_journal(entries: Iterable[Entry], token: str) -> Path:
    """The journal listing ``entries``, written and on the disk."""
    path = journal_dir() / ("%s.json" % token)
    records.write(path, {FILES: [entry.to_dict() for entry in entries]},
                  JOURNAL_FORMAT, keyed=False, durable=True)
    return path


def settle(journal: Path, entries: List[Entry], forward: bool = True) -> None:
    """Complete the save ``entries`` describe, or undo it where it cannot be
    completed, or where ``forward`` is False; then ``journal`` goes.

    Raises the OSError that stopped it completing, once it is undone, and
    :class:`Unfinished` where it could not be undone either.
    """
    failed: Optional[OSError] = None
    if forward:
        try:
            for entry in entries:
                _forward(entry)
        except OSError as exc:
            failed = exc
    if failed is not None or not forward:
        try:
            for entry in reversed(entries):
                _back(entry)
        except OSError as stuck:
            raise Unfinished(unfinished(journal, stuck)) from stuck
    try:
        journal.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # Left for the next try, which needs the .old files it lists.
        if failed is not None:
            raise failed from None
        return
    if failed is not None:
        raise failed
    if forward:
        for entry in entries:
            with contextlib.suppress(OSError):
                entry.old.unlink()


def _forward(entry: Entry) -> None:
    if entry.removed:
        if entry.existed and entry.target.exists() and not entry.old.exists():
            _again(os.replace, entry.target, entry.old)
        return
    if not entry.part.exists():
        return
    if entry.existed and entry.target.exists() and not entry.old.exists():
        _again(os.replace, entry.target, entry.old)
    _again(os.replace, entry.part, entry.target)


def _back(entry: Entry) -> None:
    if entry.old.exists():
        _again(os.replace, entry.old, entry.target)
    elif (not entry.existed and not entry.removed and not entry.part.exists()
          and entry.target.exists()):
        _again(os.remove, entry.target)
    if entry.part.exists():
        _again(os.remove, entry.part)


def can_go_forward(entry: Entry) -> bool:
    """Whether ``entry`` can still be completed: its ``.part`` is there to
    move in, or it is in already.  One taken away always can: it is still
    there to move aside, or moved aside already, or was never there."""
    if entry.removed or entry.part.exists():
        return True
    return entry.target.exists() and (entry.old.exists() or not entry.existed)


def _again(move: Callable[..., None], *paths: Path) -> None:
    """``move(*paths)``, tried again for a moment while something holds the
    file -- which on Windows is a PermissionError."""
    for wait in (*WAITS, None):
        try:
            move(*paths)
            return
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def unfinished(journal: Path, error: OSError) -> str:
    return tr(
        "A save could not be completed, or undone: %(error)s. The files it "
        "was saving are listed in %(journal)s. Close anything that has them "
        "open. The Studio tries again the next time it saves or starts.",
        error=error, journal=journal,
    )


# -- recovering ----------------------------------------------------------------------
def read_journal(path) -> Tuple[List[Entry], str]:
    """The files the journal at ``path`` lists, and "" -- or, where it can't
    be acted on, why: a newer Studio's, or not a journal any Studio writes.
    The whole journal is checked before any of it is acted on."""
    found = records.read(path, JOURNAL_FORMAT, keyed=False)
    if not found.exists:
        # Settled meanwhile: nothing to do.
        return [], ""
    if found.problem:
        return [], found.problem
    listed = found.data.get(FILES)
    entries: List[Entry] = []
    if not isinstance(listed, list) or not listed:
        return [], records.unreadable(path)
    for item in listed:
        if (not isinstance(item, dict)
                or not all(isinstance(item.get(key), str) and item.get(key) for key in ENTRY_KEYS[:3])
                or not isinstance(item.get("existed"), bool)
                or not isinstance(item.get(REMOVED, False), bool)):
            return [], records.unreadable(path)
        entry = Entry(Path(item["target"]), Path(item["part"]), Path(item["old"]), item["existed"],
                      item.get(REMOVED, False))
        # Only ever beside the file itself, under another name.
        names = {entry.target.name, entry.part.name, entry.old.name}
        if (not entry.target.is_absolute() or len(names) < 3
                or any(path.parent != entry.target.parent for path in (entry.part, entry.old))):
            return [], records.unreadable(path)
        entries.append(entry)
    return entries, ""


def recover_one(path: Path) -> str:
    """Complete or undo the save the journal at ``path`` describes; "" once
    it is settled, or why it could not be."""
    entries, problem = read_journal(path)
    if problem:
        return problem
    try:
        settle(path, entries, forward=all(can_go_forward(entry) for entry in entries))
    except Unfinished as stuck:
        return str(stuck)
    except OSError:
        # Undone, which settles it as well as completing would.
        pass
    return ""


def recover() -> List[str]:
    """Settle every save a journal says was left half done, oldest first;
    why, for each that could not be.  Asked as the Studio starts, under the
    one-Studio lock and before anything is opened."""
    return [problem for problem in map(recover_one, _journals("*.json")) if problem]


def finish_own() -> None:
    """Settle the saves this process left half done, before it saves again:
    an earlier save of the same file, settled after a later one, would
    overtake it.  Raises :class:`Unfinished` where one can't be settled."""
    for path in _journals("%d-*.json" % os.getpid()):
        problem = recover_one(path)
        if problem:
            raise Unfinished(problem)


def _journals(pattern: str) -> List[Path]:
    def written(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    try:
        found = list(journal_dir().glob(pattern))
    except OSError:
        return []
    return sorted(found, key=written)
