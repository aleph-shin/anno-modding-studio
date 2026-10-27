"""Putting a downloaded mod into a mods folder, and taking the old one out.

A zip from mod.io comes in one of three shapes, all seen in the census.  Most
hold a mod folder.  Some hold the mod's own contents at the top, so the zip
itself is the mod and its name has to be invented.  And some hold a folder that
is not a mod but a box of them -- *More Trade Contracts* ships two mods inside
one folder with no data of its own -- which a player installs by taking the
mods out of the box.  :func:`install` reads whichever it is handed and leaves
the mods folder holding mods, the way the game wants them.

Nothing is written straight into the mods folder.  The zip is unpacked into a
dot-folder beside it, which the game and the Studio both ignore, and only whole
mod folders are moved across -- so a stop or a bad zip leaves nothing half
there.  Entry names are checked before they touch the disk: a name climbing out
with ``..`` or naming a drive is refused, and characters Windows will not have
in a file name are replaced.

A folder being replaced is not deleted.  It is moved aside, whole, under the
Studio's backups -- the export backup copies only the files it can rewrite, but
an update replaces everything, meshes and all, and that has to be
recoverable -- and the caller is told where it went.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import disk, games, modfolder, places
from .i18n import size_text, tr, trn

STAGING = ".modio-unpacking"
ASIDE = "replaced"
# Past these a zip is refused before anything of it is written: a zip bomb,
# or one of millions of entries, would otherwise fill the drive.  Far above
# any mod in the census -- the largest unpack to a few gigabytes and tens of
# thousands of files.
MOST_ENTRIES = 500_000
MOST_BYTES = 64 * 1024 ** 3
# A modinfo is a few kilobytes; one bigger than this is not read into memory.
MOST_MODINFO = 1024 * 1024


class InstallError(RuntimeError):
    pass


@dataclass
class InstallResult:
    """What :func:`install` did: the mod folders now in place, the folder that
    was moved aside to make room, and the entries it would not write."""

    placed: List[Path] = field(default_factory=list)
    set_aside: Optional[Path] = None
    skipped: List[str] = field(default_factory=list)
    # What became of the iModYourAnno settings of the folder this replaced
    # (a :class:`core.imya.Carried`), when it had any.
    carried: Optional[object] = None


def safe_path(name: str) -> Optional[str]:
    """Where a zip entry may go, relative to where it is unpacked, or None.

    Entries climbing out with ``..`` or naming a drive are refused; each part
    is then made a name Windows will hold, by :func:`core.disk.windows_name`.
    """
    unified = name.replace("\\", "/")
    parts = [part for part in unified.split("/") if part not in ("", ".")]
    if not parts or ".." in parts or re.match(r"^[A-Za-z]:", unified):
        return None
    return "/".join(disk.windows_name(part) or "_" for part in parts)


def is_mod_folder(folder: Path) -> bool:
    """A folder the game would load as a mod: its data, or a modinfo, at the top."""
    try:
        names = {entry.name.lower() for entry in Path(folder).iterdir()}
    except OSError:
        return False
    return "data" in names or bool(set(modfolder.MODINFO) & names)


def is_loose_data(folder: Path) -> bool:
    """A mod's own ``data`` tree sitting in the mods folder as if it were a
    mod: what a zip with no folder of its own leaves when it is extracted
    straight into ``mods``.  Anno 1800's loader lists it as a mod called
    ``data`` and runs nothing inside it -- ``tools/loose_data_probe.py`` asked
    the game (Sept 2026): a loose ``config`` tree was listed and its op never
    ran, beside a proper mod whose op did."""
    folder = Path(folder)
    return folder.name.lower() == "data" and (folder / "config").is_dir()


# How :func:`unpack` lays a zip out, as a number.  A zip mirror
# (:mod:`core.zipped`) is kept under it, and one made under another is
# unpacked again: bump it in the change that makes a zip come out of it
# differently, or mods are read from a tree this code would not have made.
UNPACK_VERSION = 1


def unpack(archive: Path, into: Path) -> List[str]:
    """Extract ``archive`` under ``into``, skipping what must not be written.

    Returns the names of the entries skipped.  Directories are made as their
    files need them, so an empty folder in the zip does not come across --
    the game has no use for one.

    What it says it unpacks to is added up first, and a zip of more entries
    or bytes than any mod has (:data:`MOST_ENTRIES`, :data:`MOST_BYTES`), or
    of more than the drive has free, raises :class:`InstallError` with
    nothing written.  Python's zipfile stops each entry at the size the zip
    declares for it, so the sum is what is written.
    """
    skipped: List[str] = []
    with zipfile.ZipFile(archive) as zipped:
        _measure(zipped.infolist(), into)
        for info in zipped.infolist():
            if info.is_dir():
                continue
            target = safe_path(info.filename)
            if target is None:
                skipped.append(info.filename)
                continue
            if info.flag_bits & 0x1:
                skipped.append(info.filename)  # encrypted
                continue
            # A mod's tree is deep before a library and a staging folder are
            # put in front of it: past 260 characters Windows has to be asked
            # the long way, or the entry is not written and the zip fails.
            path = into / target
            os.makedirs(disk.reachable(path.parent), exist_ok=True)
            with zipped.open(info) as source, open(disk.reachable(path), "wb") as out:
                shutil.copyfileobj(source, out)
    return skipped


def _measure(infos, into: Path) -> None:
    """Refuse a zip that would unpack to more than a mod could be, or more
    than the drive ``into`` is on has free."""
    if len(infos) > MOST_ENTRIES:
        raise InstallError(trn(
            "the zip holds %(n)s entry, more than any mod has, so it was not unpacked",
            "the zip holds %(n)s entries, more than any mod has, so it was not unpacked",
            len(infos),
        ))
    total = sum(info.file_size for info in infos if not info.is_dir())
    if total > MOST_BYTES:
        raise InstallError(tr(
            "the zip unpacks to %s, more than any mod is, so it was not unpacked",
            size_text(total),
        ))
    free = disk.free_space(into)
    if free is not None and total > free:
        raise InstallError(tr(
            "the zip unpacks to %(size)s and the drive has %(free)s free, so it was not unpacked",
            size=size_text(total), free=size_text(free),
        ))


def mods_in(unpacked: Path) -> List[Path]:
    """The mod folders an unpacked zip holds, in the shape the game installs.

    The unpacked root itself when the zip was the mod's own contents; else
    each folder at the top that is a mod; and the mods inside a top-level
    folder that is not one -- a box of mods (:func:`_mods_of`).
    """
    unpacked = Path(unpacked)

    def inside(folder: tuple) -> dict:
        try:
            return {entry.name: entry.is_dir() for entry in unpacked.joinpath(*folder).iterdir()}
        except OSError:
            return {}

    return [unpacked.joinpath(*folder) for folder in _mods_of(inside)]


def _mods_of(inside) -> List[tuple]:
    """The folder of each mod in an unpacked zip, as a tuple of names, given
    ``inside(folder)``: each name directly in that folder, and whether it is a
    folder.  One rule for a zip on disk (:func:`mods_in`) and one read by its
    entry names (:func:`peek`).

    A box is walked down to each mod in it, however deep, as Anno 1800's
    loader walks a mods folder (A11-144: it loaded a mod three folders below
    its top folder).  Directly in the box, a folder with data or a modinfo is
    a mod; below that only a modinfo makes one, since the game does not load
    a nested folder that has data and no modinfo (P7-32).  A mod is not looked
    inside: what it bundles travels with it.  Anno 117 was not tried, and is
    read the same.
    """
    top = inside(())
    if _is_mod(top):
        return [()]
    found: List[tuple] = []
    for name in _folders(top):
        held = inside((name,))
        if _is_mod(held):
            found.append((name,))
        else:
            found.extend(_boxed(inside, (name,), held))
    return found


def _boxed(inside, box: tuple, held: dict) -> List[tuple]:
    found: List[tuple] = []
    for name in _folders(held):
        folder = (*box, name)
        names = inside(folder)
        if _has_modinfo(names) or (len(folder) == 2 and _is_mod(names)):
            found.append(folder)
        else:
            found.extend(_boxed(inside, folder, names))
    return found


def _folders(names: dict) -> List[str]:
    return sorted((name for name, is_dir in names.items() if is_dir), key=str.lower)


@dataclass
class Peeked:
    """One mod a zip holds, read without unpacking it: the folder it is in
    -- "" when the zip is the mod's own contents -- and what its modinfo says
    of it."""

    folder: str = ""
    mod_name: str = ""
    mod_id: str = ""
    version: str = ""
    creator: str = ""
    # Which game its own layout says it is for, or "" where nothing says:
    # each game keeps a mod's assets.xml patch at a place of its own -- see
    # core.modops.game_for_patch, which is this for a mod on disk.
    game: str = ""


def peek(archive: Path) -> List[Peeked]:
    """The mods ``archive`` holds, as :func:`mods_in` would find them once it
    was unpacked, with nothing written.

    For a zip nobody has vouched for -- one dropped on the window, not one
    mod.io named -- so that what it would install can be said before any of
    it is.  Entries :func:`unpack` would skip are left out here too.  A file
    that is not a zip, and a zip with no mod in it, raise
    :class:`InstallError`.
    """
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = {}
            for info in zipped.infolist():
                target = None if info.is_dir() or info.flag_bits & 0x1 else safe_path(info.filename)
                if target is not None:
                    entries[tuple(target.split("/"))] = info
            found = [
                _peeked(zipped, entries, folder) for folder in _mods_among(list(entries))
            ]
    except (zipfile.BadZipFile, OSError) as err:
        raise InstallError(tr("the file could not be read as a zip")) from err
    if not found:
        raise InstallError(tr("the zip holds no mod: no data folder and no modinfo in it"))
    return found


def _is_mod(inside: dict) -> bool:
    names = {name.lower() for name in inside}
    return "data" in names or bool(set(modfolder.MODINFO) & names)


def _has_modinfo(inside: dict) -> bool:
    return bool(set(modfolder.MODINFO) & {name.lower() for name in inside})


def _mods_among(paths) -> List[tuple]:
    """:func:`mods_in`, of a zip's entry names: the folder of each mod.

    The names are put in a tree once, so walking a box down asks nothing of
    every entry again.  Case is Windows' -- two spellings are one folder,
    named as the first entry spells it."""
    tree: dict = {}
    for parts in paths:
        node = tree
        for part in parts:
            node = node.setdefault(part.lower(), (part, {}))[1]

    def inside(folder: tuple) -> dict:
        node = tree
        for part in folder:
            node = node[part.lower()][1]
        return {name: bool(held) for name, held in node.values()}

    return _mods_of(inside)


def _peeked(zipped: zipfile.ZipFile, entries: dict, folder: tuple) -> Peeked:
    lowered = {tuple(part.lower() for part in parts): info for parts, info in entries.items()}
    at = tuple(part.lower() for part in folder)
    meta: dict = {}
    for name in modfolder.MODINFO:
        info = lowered.get((*at, name))
        if info is not None and info.file_size <= MOST_MODINFO:
            meta = modfolder.metadata_from(modfolder.decode_modinfo(zipped.read(info))[0])
            break
    games_found = set()
    for key, game in games.GAMES.items():
        patch = tuple(part.lower() for part in game.asset_relpath.parts)
        if (*at, *patch) in lowered:
            games_found.add(key)
    return Peeked(
        game=games_found.pop() if len(games_found) == 1 else "",
        folder=folder[-1] if folder else "",
        mod_name=str(meta.get("mod_name") or ""),
        mod_id=str(meta.get("mod_id") or ""),
        version=str(meta.get("version") or ""),
        creator=str(meta.get("creator") or ""),
    )


def set_aside(folder: Path) -> Path:
    """Move a mod folder out of the mods folder, whole, and say where.

    Under the backups, in a folder for the moment it happened; a mod is not
    small enough to copy on the off chance, but a move on the same drive is a
    rename and costs nothing.  Never back into the mods folder, and never
    with a leading ``-`` where the game would read it as a switched-off mod.
    """
    folder = Path(folder)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    home = places.backups_dir() / ASIDE / stamp
    home.mkdir(parents=True, exist_ok=True)
    target = home / folder.name
    counter = 1
    while target.exists():
        counter += 1
        target = home / ("%s (%d)" % (folder.name, counter))
    shutil.move(str(folder), str(target))
    return target


def install(
    archive: Path,
    mods_folder: Path,
    replace: Optional[Path] = None,
    name: str = "",
) -> InstallResult:
    """Put the mods in ``archive`` into ``mods_folder``.

    ``replace`` is the folder this download supersedes, if any: it is moved
    aside first, and when the zip holds exactly one mod that mod takes the old
    folder's name -- the name is what Anno 1800 keys load order on, and a
    switched-off mark on it is the user's to keep.  ``name`` is what to call a
    mod whose zip has no folder for it, when nothing is being replaced.

    A folder already at a name a new mod wants, other than ``replace``, is
    moved aside too: this is a download the user asked for, and the copy in
    the way is the same mod as far as the game is concerned.

    ``name`` comes from outside -- a mod.io mod's name, which anyone
    publishing can choose -- so it is made one safe folder name first
    (:func:`core.disk.safe_folder_name`): ``..\\..\\Startup`` would otherwise
    have moved a folder of the user's aside and put the mod in its place.
    And nothing is moved to, or out of the way of, a folder that is not
    directly in ``mods_folder``.
    """
    archive, mods_folder = Path(archive), Path(mods_folder)
    mods_folder.mkdir(parents=True, exist_ok=True)
    staging = mods_folder / ("%s-%d" % (STAGING, os.getpid()))
    _remove(staging)
    staging.mkdir()
    result = InstallResult()
    try:
        result.skipped = unpack(archive, staging)
        found = mods_in(staging)
        if not found:
            raise InstallError(tr("the zip holds no mod: no data folder and no modinfo in it"))
        if replace is not None:
            replace = Path(replace)
            if not disk.directly_in(replace, mods_folder):
                raise InstallError(tr(
                    "%s cannot be a folder's name, so nothing was installed", replace.name))
            if replace.is_dir():
                result.set_aside = set_aside(replace)
        for source in found:
            if source == staging:
                wanted = replace.name if replace is not None else (
                    disk.safe_folder_name(name, "")
                    or disk.safe_folder_name(archive.stem, "mod"))
            elif replace is not None and len(found) == 1:
                wanted = replace.name
            else:
                wanted = source.name
            target = mods_folder / wanted
            if not disk.directly_in(target, mods_folder):
                raise InstallError(tr(
                    "%s cannot be a folder's name, so nothing was installed", wanted))
            if target.exists() and (replace is None or target != replace):
                aside = set_aside(target)
                if result.set_aside is None:
                    result.set_aside = aside
            shutil.move(str(source), str(target))
            result.placed.append(target)
            # The root itself was the mod; nothing else is in the staging
            # folder to move, and it has just moved.
            if source == staging:
                break
        return result
    finally:
        _remove(staging)


def bare(folder: Path) -> str:
    """The name the game keys on, without Anno 1800's switched-off mark."""
    return modfolder.bare_name(Path(folder).name)


def _remove(path: Path) -> None:
    if path.exists():
        # The long way too: what was unpacked deeper than Windows reaches
        # would otherwise be left where it stood, with nothing said.
        shutil.rmtree(disk.reachable(path), ignore_errors=True)
