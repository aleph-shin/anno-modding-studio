"""The Studio's own JSON records: stamped, and never written over unread.

Much of what the Studio keeps is a small JSON record that it reads, changes
and writes back whole: which library copy came from where
(``installed.json``), every link the library made (``links.json``), the
folders it stands for (``references.json``), which loadout is live
(``live.json``), which game update each piece of game data came from
(``game-updates.json``), and a conversion's journal.  Each used to be read
with ``disk.read_json(path, {})``, which answers {} alike for a file that is
missing, damaged, or of a shape the reader does not know.  The next write
then replaced the file with what was made of it.  A record a newer Studio
wrote in a newer shape, or one edited by hand, was emptied by the next
install, and with it the record of where every library copy came from.

So each record says which format it is in and which Studio wrote it, and is
read here into a :class:`Record` that knows whether it may be written back.
One that is missing, or of a format this build reads, may be.  One that
can't be read, is of a shape no Studio writes, or is of a newer format, may
not.  :func:`write` refuses it with a :class:`RecordError`, which says why
and what to do.  It is an ``OSError``, so every caller that already reports
a write that failed reports this one too.

A record is one of two shapes.  A *keyed* record holds entries by name, under
:data:`ENTRIES` beside its stamp.  A *flat* record is one set of facts, the
stamp among them.  A file from before records had a stamp is format 0: for a
keyed record the whole file is its entries.  Before the first write that
upgrades a record from an older format, the old file is kept beside it, once
per format (:func:`keep_before_upgrade`).  A loadout or a project another
version of the Studio wrote is kept so too, once per version, before this
one writes over it (:func:`keep_other_version`).  The rules are in
``docs/standards/code-standards.md``, section 11.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import __version__
from . import disk
from .i18n import tr

# The keys of the stamp, in every record.  Frozen: every later Studio reads
# them, from files any earlier one wrote.
FORMAT = "format"
WRITTEN_BY = "written_by"
# Where a keyed record holds its entries.
ENTRIES = "entries"
STAMP = (FORMAT, WRITTEN_BY)


class RecordError(OSError):
    """A record the Studio will not write over, with the sentence saying why."""


@dataclass
class Record:
    """One record as read: what it holds, and whether it may be written back.

    ``data`` is the entries of a keyed record, and the facts of a flat one
    without the stamp.  It is what could be read, even of a record that may
    not be written: a newer format's entries, where they are where this
    build looks for them.  ``problem`` says why it may not be written back,
    and is "" where it may.
    """

    path: Path
    data: Dict[str, Any] = field(default_factory=dict)
    # The format it says it is in: 0 for a file from before records said,
    # and for one that is not there.
    format: int = 0
    written_by: str = ""
    exists: bool = False
    problem: str = ""
    # What a keyed record holds beside its entries and its stamp, which a
    # write puts back as it was: a newer Studio's, of the same format.
    rest: Dict[str, Any] = field(default_factory=dict)


def _format_of(raw: dict) -> Optional[int]:
    found = raw.get(FORMAT)
    if isinstance(found, int) and not isinstance(found, bool):
        return found
    return None


def newer(path, written_by: str) -> str:
    """Why a file a newer Studio wrote is left as it is, and what to do."""
    name = Path(path).name
    if written_by:
        return tr(
            "%(file)s was written by version %(version)s of the Studio, which "
            "keeps it in a newer way than this version (%(this)s) can read. "
            "It is left as it is. Start that version or a later one to "
            "change it.",
            file=name, version=written_by, this=__version__,
        )
    return tr(
        "%(file)s was written by a newer version of the Studio, in a way "
        "this version (%(this)s) can't read. It is left as it is. Start the "
        "newest version you have to change it.",
        file=name, this=__version__,
    )


def unreadable(path) -> str:
    """Why a file that can't be read as the Studio's is left as it is."""
    path = Path(path)
    return tr(
        "%(file)s can't be read as one of the Studio's records, so it is "
        "left as it is, and not written over. Move it out of %(folder)s, or "
        "put back a copy from a backup, and try again.",
        file=path.name, folder=path.parent,
    )


def read(path, current: int, keyed: bool = True, entry: Optional[type] = dict) -> Record:
    """The record at ``path``, of which this build reads formats up to
    ``current``.

    ``keyed`` says it holds entries by name, each a ``entry`` (None for
    any).  An entry of another kind is a shape no Studio wrote, and the
    record is then not written back, though the entries that are of the
    kind are still read.
    """
    path = Path(path)
    found = Record(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        found.format = current
        return found
    except OSError:
        found.exists = True
        found.problem = unreadable(path)
        return found
    found.exists = True
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        found.problem = unreadable(path)
        return found
    stamped = _format_of(raw)
    found.format = stamped or 0
    found.written_by = str(raw.get(WRITTEN_BY) or "") if stamped is not None else ""
    if stamped is not None and stamped > current:
        found.problem = newer(path, found.written_by)
    if not keyed:
        found.data = {key: value for key, value in raw.items() if stamped is None or key not in STAMP}
        return found
    if stamped is None:
        entries: Any = raw
    else:
        entries = raw.get(ENTRIES, {})
        found.rest = {key: value for key, value in raw.items() if key not in STAMP and key != ENTRIES}
    if not isinstance(entries, dict):
        found.problem = found.problem or unreadable(path)
        return found
    for name, value in entries.items():
        if entry is None or isinstance(value, entry):
            found.data[str(name)] = value
        else:
            found.problem = found.problem or unreadable(path)
    return found


def keep_before_upgrade(found: Record, current: int) -> Optional[Path]:
    """Keep the file ``found`` was read from beside itself, before a write
    in format ``current`` replaces one of an older format.  Once per format:
    a copy kept already is not written over.  Where the copy is, or None
    where none was wanted or none could be made."""
    if not found.exists or found.format >= current:
        return None
    copy = found.path.with_name("%s.format%d.bak" % (found.path.name, found.format))
    if copy.exists():
        return copy
    try:
        shutil.copy2(str(found.path), str(copy))
    except OSError:
        return None
    return copy


def keep_other_version(path, known: Optional[Tuple[str, int]] = None) -> Optional[Path]:
    """Keep the file at ``path`` beside itself, before this build writes
    over it, where another version of the Studio wrote it: a loadout or a
    project, which are rewritten whole in this version's shape.  Where the
    copy is, or None where none was wanted or none could be made.

    Somebody who then has to go back has the file that version wrote:
    ``<name>.<version>.bak`` beside it, the file's own name and suffix
    first (``Mine.annoload.0.1.0.bak``, ``Mine.annoproj.0.1.0.bak``), or
    ``.format<n>.bak`` for a file that does not say which version wrote
    it.  The same goes the other way, for a file a newer version wrote.
    Once per version: a copy kept already is not written over, so it stays
    the file as that version last left it before another took it up.
    ``known`` is what the file says wrote it, ``(written_by, format)``,
    where the caller knows without reading it again.
    """
    path = Path(path)
    if not path.is_file():
        return None
    if known is None:
        raw = disk.read_json(path, {})
        if not isinstance(raw, dict) or not raw:
            return None
        saved = raw.get(FORMAT)
        known = (str(raw.get(WRITTEN_BY) or ""),
                 saved if isinstance(saved, int) and not isinstance(saved, bool) else 0)
    wrote, saved = known
    if wrote == __version__:
        return None
    copy = path.with_name("%s.%s.bak" % (
        path.name, re.sub(r"[^\w.+-]", "_", wrote) if wrote else "format%d" % saved))
    if copy.exists():
        return copy
    try:
        disk.write_bytes(copy, path.read_bytes())
    except OSError:
        return None
    return copy


def write(path, data: Dict[str, Any], current: int, keyed: bool = True,
          entry: Optional[type] = dict, **how) -> None:
    """Write ``data`` as the record at ``path``, in format ``current``.

    The file there is read first, and a record that may not be written
    back raises :class:`RecordError`, leaving it as it is.  What a keyed
    record of this format held beside its entries goes back with them.
    ``how`` is handed to :func:`core.disk.write_json`.
    """
    path = Path(path)
    found = read(path, current, keyed, entry)
    if found.problem:
        raise RecordError(found.problem)
    keep_before_upgrade(found, current)
    out: Dict[str, Any] = {FORMAT: current, WRITTEN_BY: __version__}
    if keyed:
        out.update((key, value) for key, value in found.rest.items() if key not in out)
        out[ENTRIES] = data
    else:
        out.update((key, value) for key, value in data.items() if key not in STAMP)
    path.parent.mkdir(parents=True, exist_ok=True)
    disk.write_json(path, out, **how)

