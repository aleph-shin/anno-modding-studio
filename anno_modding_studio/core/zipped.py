"""Anno 1800 mods the game reads straight out of a ``.zip``.

The 1800 loader reads a zip in a mods folder as mods, in three shapes: the
mod's own files at the top, one mod folder inside, or a box of several.  Each
mod inside is a mod; a ``-`` in front of the zip's name switches all of it off.
Its docs add that island files misbehave from a zip.

The Studio reads folders, and everything downstream of reading -- scripts,
texts, InfoTips, a mod's other files -- looks at files on disk.  So instead of
teaching all of that about zips, each zip is unpacked into a *mirror*: a folder
in the Studio's cache named exactly as the zip is, ``-`` and all, kept beside
the others of its mods folder and refreshed only when the zip changes.  The
mirror is read as one more place the game loads mods from, so a zipped mod
comes in as ``Big Map.zip`` (or ``Box.zip/Mod A``) like any other.

What must not happen to a mirror is anything that writes: switching a zipped
mod renames the zip, not its mirror (:func:`core.switches.write` follows the
mirror back), and a zipped mod is never exported back out, since a folder
written beside the zip would be a second copy the game loads.  Nothing in the
game's own folders is written by reading.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import List, Optional

from . import disk, places
from .games import ANNO_1800

SUFFIX = ".zip"
# Beside the mirrored zips, dotted so no reading takes it for a mod: which
# mods folder the mirror is of, each zip's size and time as unpacked, and
# the ``modzip.UNPACK_VERSION`` they were unpacked under.
_SOURCE = ".source.json"


def _cache_root() -> Path:
    return places.cache_dir() / "zipped"


def zips_in(root) -> List[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir()
         if path.is_file() and path.suffix.lower() == SUFFIX and not path.name.startswith(".")),
        key=lambda path: path.name.lower(),
    )


def mirror_of(root) -> Path:
    """Where the zips of the mods folder ``root`` are mirrored."""
    key = os.path.normcase(os.path.abspath(str(root)))
    return _cache_root() / hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _stamp(path: Path) -> list:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _read_source(mirror: Path) -> dict:
    return disk.read_json(mirror / _SOURCE, {})


def _remove(folder: Path) -> None:
    # The long way: a zip's tree can run past the 260 characters Windows
    # opens unasked, and a mirror left half there is read as a mod of its
    # own -- one whose zip has gone, or with files its zip no longer has.
    shutil.rmtree(disk.reachable(folder), ignore_errors=True)


def sync(game: str, root) -> Optional[Path]:
    """The mirror of ``root``'s zips, brought up to date; None when the game
    does not read zips or ``root`` has none."""
    from . import modzip

    if game != ANNO_1800:
        return None
    root = Path(root)
    zips = zips_in(root)
    mirror = mirror_of(root)
    if not zips:
        if mirror.is_dir():
            _remove(mirror)
        return None
    source = _read_source(mirror)
    stamps = source.get("zips")
    if not isinstance(stamps, dict) or source.get("unpacked") != modzip.UNPACK_VERSION:
        # Unpacked by other code than this, the zips are unpacked again.
        stamps = {}
    mirror.mkdir(parents=True, exist_ok=True)
    wanted = {path.name for path in zips}
    for held in mirror.iterdir():
        if held.is_dir() and held.name not in wanted:
            _remove(held)
            stamps.pop(held.name, None)
    for path in zips:
        target = mirror / path.name
        try:
            stamp = _stamp(path)
        except OSError:
            continue
        if target.is_dir() and stamps.get(path.name) == stamp:
            continue
        _remove(target)
        try:
            modzip.unpack(path, target)
        except Exception:  # noqa: BLE001 -- a bad zip is left out, not a reading that fails
            _remove(target)
            stamps.pop(path.name, None)
            continue
        stamps[path.name] = stamp
    disk.write_json(mirror / _SOURCE, {
        "root": str(root), "zips": stamps, "unpacked": modzip.UNPACK_VERSION,
    }, indent=1)
    return mirror


def is_mirrored(path) -> bool:
    """Whether ``path`` is inside a mirror, and so stands for a zip."""
    try:
        Path(path).resolve().relative_to(_cache_root().resolve())
    except (ValueError, OSError):
        return False
    return True


def zip_for(path) -> Optional[Path]:
    """The zip a mirrored folder -- or anything inside one -- stands for."""
    path = Path(path)
    for mirror in (path, *path.parents):
        if (mirror / _SOURCE).is_file():
            root = _read_source(mirror).get("root")
            if not root:
                return None
            inside = path.relative_to(mirror).parts
            return Path(root) / inside[0] if inside else None
    return None


def moved(old_zip: Path, new_zip: Path) -> Optional[Path]:
    """Follow a zip renamed in its mods folder with its mirror, so it need not
    be unpacked again. The mirror folder it now has, if it had one."""
    mirror = mirror_of(old_zip.parent)
    source = _read_source(mirror)
    held = mirror / old_zip.name
    if not held.is_dir():
        return None
    target = mirror / new_zip.name
    try:
        held.rename(target)
    except OSError:
        return None
    stamps = source.get("zips")
    if not isinstance(stamps, dict):
        stamps = {}
    stamps.pop(old_zip.name, None)
    try:
        stamps[new_zip.name] = _stamp(new_zip)
    except OSError:
        pass
    source["zips"] = stamps
    disk.write_json(mirror / _SOURCE, source, indent=1)
    return target
