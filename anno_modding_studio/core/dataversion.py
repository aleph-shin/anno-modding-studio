"""Which version of the Studio has used its folders, and whether this one may.

A loadout says which format it is in, and a build refuses one newer than it
reads.  The library's records, the backups' manifests and the conversions'
journals say so too (:mod:`core.records`), and a build writes none of them
back, prunes none and undoes none, of a format newer than it reads.  What a
stamp can't stop is a build reading something it would act on wrongly before
it ever writes: a folder laid out differently, say.

So each of the Studio's folders (:func:`places.data_folders`) also holds a
small ``data-version.json``: the newest data version, and the newest version
of the Studio at that data version, that have used it.  A build whose
:data:`DATA_VERSION` is older than a folder's does not start over it, and
names the version that wrote it (:func:`refusal`).  Otherwise it writes
itself in (:func:`record`).  The rules for bumping it are in
``docs/standards/code-standards.md``, section 11.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import __version__
from . import disk, places, versioning
from .i18n import tr

FILE_NAME = "data-version.json"

# What the Studio's folders hold, as a number.  Bump it in the change that
# an earlier build would harm by reading alone: a folder laid out
# differently, or a record whose new meaning an earlier build would act on
# before its stamp stopped it writing.  Not for what its own stamp covers (a
# loadout's format, a record's, an index's schema, a cache's key), and not
# for a field an earlier build carries over without harm.  Each bump shuts
# every earlier release out of the folders, so a change that can do without
# one does.
DATA_VERSION = 1


@dataclass(frozen=True)
class Stamp:
    """What one folder's ``data-version.json`` says."""

    folder: Path
    data_version: int
    # "" where the file does not say.
    app_version: str = ""


def read(folder: Path) -> Optional[Stamp]:
    """What ``folder`` says has used it, or None where it says nothing that
    can be read: a folder no Studio has stamped, or a file that is not one."""
    found = disk.read_json(Path(folder) / FILE_NAME, {})
    data_version = found.get("data_version")
    if not isinstance(data_version, int) or isinstance(data_version, bool):
        return None
    return Stamp(Path(folder), data_version, str(found.get("app_version") or ""))


def newer_than_this() -> Optional[Stamp]:
    """The first of the Studio's folders that a newer data version has
    used, or None where this build may use every one."""
    for folder in places.data_folders():
        stamp = read(folder)
        if stamp is not None and stamp.data_version > DATA_VERSION:
            return stamp
    return None


def last_used_by() -> str:
    """The newest version of the Studio that any of its folders says has
    used it, or "" where none says: a first start on this PC.

    Asked before :func:`record` writes this build in, it is the version the
    Studio was before an update, which the window names once
    (``ui.studio_release_controller``)."""
    newest = ""
    for folder in places.data_folders():
        stamp = read(folder)
        if stamp is not None and versioning.key(stamp.app_version) > versioning.key(newest):
            newest = stamp.app_version
    return newest


def refusal(stamp: Stamp) -> str:
    """Why this build does not start over ``stamp``'s folder, and what to
    do instead."""
    if stamp.app_version:
        return tr(
            "Your Studio data in %(folder)s was last used by version "
            "%(version)s, which keeps it in a newer way than this version "
            "(%(this)s) can read. Start that version or a later one to "
            "carry on with it. The folder is left as that version left it.",
            folder=stamp.folder, version=stamp.app_version, this=__version__,
        )
    return tr(
        "Your Studio data in %(folder)s was last used by a newer version, "
        "which keeps it in a way this version (%(this)s) can't read. Start "
        "the newest version you have to carry on with it. The folder is "
        "left as that version left it.",
        folder=stamp.folder, this=__version__,
    )


def record() -> None:
    """Write this build into each of the Studio's folders, where it is the
    newest to have used it.

    A folder an earlier build of the same data version uses goes on naming
    the later one, which is the one a refusal sends people back to.  One
    that cannot be written is left: the Studio says what it cannot write
    where it tries, and this is not where.
    """
    for folder in places.data_folders():
        stamp = read(folder)
        if stamp is not None and stamp.data_version > DATA_VERSION:
            continue
        newest = __version__
        if (stamp is not None and stamp.data_version == DATA_VERSION
                and versioning.key(stamp.app_version) > versioning.key(newest)):
            newest = stamp.app_version
        if stamp == Stamp(Path(folder), DATA_VERSION, newest):
            continue
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            disk.write_json(Path(folder) / FILE_NAME, {
                "data_version": DATA_VERSION, "app_version": newest,
            })
        except OSError:
            continue
