"""A mod folder: what it declares, what is bundled inside it, what it is called.

The layer underneath everything the Studio does with somebody else's mod.
Reading its ModOps (:mod:`core.modops`), listing a mods folder
(:mod:`core.installplan`), matching it to mod.io (:mod:`core.updates`),
asking the loader about it (:mod:`core.xmltest`) all start from the same
three questions -- what does its modinfo say, which folders inside it are mods
of their own, and what is its name once Anno 1800's "switched off" mark is
taken away -- and each is answered here once.

Kept below the rest on purpose: it imports nothing but :mod:`core.disk`,
:mod:`core.settings` and :mod:`core.versioning`, so any module can ask it
without joining the import cycle the ModOps reader sits in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from . import disk, settings, versioning

# The two names a modinfo goes by: ``.jsonc`` is JSON with comments, which the
# community's editor extension writes and the Anno 117 loader takes.
MODINFO = ("modinfo.json", "modinfo.jsonc")

# What Anno 1800 puts in front of a folder name to mean "skip this".
OFF_MARK = "-"

# Where in a mod its patch to the game's tooltips is (core.infotips reads it).
# Here and not in infotips, which imports modops: modops needs it to sort a
# folder's files, and importing infotips for it would make the two a cycle.
INFOTIPS_PATCH = "data/infotips/export.bin.xml"


# -- its name ---------------------------------------------------------------------
def is_marked_off(name: str) -> bool:
    return name.startswith(OFF_MARK)


def bare_name(name: str) -> str:
    """The folder name with any "switched off" mark taken back off.

    Used for load order as well as for display: where a mod sits in the stack is
    a property of the mod, not of whether it happens to be on right now, and a
    ``-`` sorts in front of every letter -- so ordering by the raw name would put
    every switched-off mod at the very front of a folder it is not even in.
    """
    return name.lstrip(OFF_MARK) if is_marked_off(name) else name


def mod_key(mod_id: str, folder: str) -> str:
    """What one mod is known by wherever it turns up: its ModID, or for a mod
    that declares none its folder's bare name; lower-cased."""
    return (mod_id or bare_name(folder)).strip().lower()


def loader_key(mod_id, folder: str) -> str:
    """What the game keeps one copy of: the ModID exactly as written, case and
    all -- ``ModLoadingOrder::AddMod`` keys its map by it (P8-30) -- or for a
    mod that declares none, its folder's bare name as :func:`mod_key` has it.

    :func:`mod_key` lower-cases, and is right to for telling which mod is
    which; this is only for which of a mod's copies loads.  Spaces around a
    ModID are still taken off: whether the game trims them is not in the
    loader's source, and of the six census ModIDs that carry them none is also
    written without.
    """
    mod_id = (mod_id or "").strip()
    return mod_id if mod_id else bare_name(folder).strip().lower()


def match_keys(mod_id, origin) -> set:
    """Every name a loadout's mod is matched to an installed one by: its
    ModID, and the bare name of the last folder in its origin; lower-cased."""
    found = set()
    if mod_id:
        found.add(str(mod_id).strip().lower())
    if origin:
        found.add(bare_name(str(origin).split("/")[-1]).lower())
    return found


# -- its modinfo ------------------------------------------------------------------
def modinfo_of(folder) -> Optional[Path]:
    """The folder's own modinfo, ``modinfo.json`` before ``modinfo.jsonc``;
    None for a folder without one."""
    for name in MODINFO:
        path = Path(folder) / name
        if path.is_file():
            return path
    return None


def metadata_of(folder) -> dict:
    """:func:`read_metadata` of the folder's own modinfo; {} without one."""
    path = modinfo_of(folder)
    return read_metadata(path) if path is not None else {}


def modinfo_text(path: Path) -> Tuple[Optional[str], bool]:
    """A modinfo's text, and whether it was UTF-8.

    Not every mod's is: eleven on mod.io are saved in a Chinese codepage. Read
    strictly, one raised out of the middle of reading a whole mods folder and
    took every other mod in it down too. What the Studio decides from a modinfo
    -- its ID, version and what it loads after -- is plain ASCII in all of them,
    so bytes that do not decode become U+FFFD and the rest is read; the second
    value says that happened, since names in other scripts are what it costs.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None, True
    return decode_modinfo(raw)


def decode_modinfo(raw: bytes) -> Tuple[str, bool]:
    """A modinfo's bytes as text, and whether they were UTF-8: see
    :func:`modinfo_text`."""
    try:
        return raw.decode("utf-8-sig"), True
    except UnicodeDecodeError:
        return raw.decode("utf-8-sig", errors="replace"), False


def read_metadata(path: Path) -> dict:
    """Whatever a modinfo.json says about the mod, in the loadout's own terms."""
    text, _utf8 = modinfo_text(path)
    if text is None:
        return {}
    return metadata_from(text)


def metadata_from(text: str) -> dict:
    """:func:`read_metadata` of a modinfo's text, for one that is not a file
    on disk yet: the modinfo of a mod still in its zip."""
    try:
        raw = json.loads(text)
    except ValueError:
        try:
            raw = json.loads(disk.strip_comments(text))
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}

    found: Dict[str, Any] = {
        key: value
        for key, value in (
            ("mod_name", _english(raw.get("ModName"))),
            ("creator", _english(raw.get("CreatorName"))),
            ("description", _english(raw.get("Description"))),
            ("category", _english(raw.get("Category"))),
            ("version", _english(raw.get("Version"))),
            ("mod_id", _english(raw.get("ModID"))),
        )
        if value
    }
    # Which mods this one has to come after. Both games let a mod say so, and
    # both spell it differently; the loader honours it over its own sort order,
    # so this is part of where the mod sits rather than a note about it.
    after = raw.get("LoadAfterIds")
    if not isinstance(after, list):
        after = []
    depends = raw.get("Dependencies")
    if isinstance(depends, dict):
        # Requiring a mod means loading after it: the point of requiring one is
        # to patch what it leaves behind.
        for named in ("Require", "LoadAfter"):
            after = after + list(depends.get(named) or [])
    load_after = [str(value).strip() for value in after if str(value).strip()]
    if load_after:
        found["load_after"] = load_after

    # What the mod lets you decide. Its ModOps read these back, so without them
    # a configurable mod is a mod whose ops cannot be answered at all.
    offered = settings.read(raw.get("Options"))
    if len(offered):
        found["options"] = offered

    # And the file exactly as its author wrote it. The Studio models six of its
    # fields and a mod's modinfo carries a dozen -- what it is incompatible
    # with, what it deprecates, what it has to load after, what the author
    # warns about. Writing the mod back out from the six would quietly delete
    # the rest, so the whole thing is kept and the six are overwritten into it.
    # See :func:`core.modinfo.build`.
    found["declared"] = raw
    return found


def _english(value) -> str:
    if isinstance(value, dict):
        return str(value.get("English") or "").strip()
    return str(value or "").strip()


def declared_version(declared) -> str:
    """The version a modinfo as its author wrote it says, as
    :func:`metadata_from` reads it; "" where it says none."""
    return _english(declared.get("Version")) if isinstance(declared, dict) else ""


# -- what is inside it ------------------------------------------------------------
MODINFO_NAMES = frozenset({"modinfo.json", "modinfo.jsonc"})


def bundled_in(folder: Path) -> List[Path]:
    """Mods bundled inside ``folder``, which the game loads in their own right.

    A folder carrying its own ``modinfo.json`` is a mod, wherever it sits.  Real
    mods bundle a great many: one Anno 1800 mod ships twenty-six, usually shared
    pieces several mods depend on.  The loader finds each of them and loads it
    separately -- with its own place in load order, which is routinely nowhere
    near its host's.
    """
    folder = Path(folder)
    found = []
    # One walk for both names, not a glob each: every mods-folder read asks
    # this of every mod, and two recursive globs apiece were most of a read.
    # Matched without regard to case, as Windows -- and so the game -- does.
    top = str(folder)
    for here, dirs, files in os.walk(folder):
        # Dot-folders are tooling, and nothing inside one is a mod.
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        if here != top and any(name.lower() in MODINFO_NAMES for name in files):
            found.append(Path(here))
    # A mod bundled inside a bundled mod belongs to the inner one; keeping only
    # the outermost at each point would lose it, so they all stand on their own
    # and ownership is settled per file in scan_folder().
    return sorted(found)


def mod_folders(root: Path) -> List[Path]:
    """Every folder under ``root`` the game would load as a mod.

    Which is each folder directly inside it, plus any folder nested inside one
    of those that carries a modinfo of its own -- because that is a mod too, and
    the game loads it separately rather than as part of whatever it is sitting
    in.  Dot-folders are tooling (``.vscode`` and the like), never mods.

    The top level is sorted by the name without Anno 1800's "switched off" mark,
    because that is the mod's real position: a ``-`` sorts in front of every
    letter, so ordering by the raw name would stack every switched-off mod at
    the very front of a folder the game is not even reading them from.  Real
    load order is worked out later and is not this -- see
    :func:`core.switches.order`.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    tops = sorted(
        (path for path in root.iterdir()
         if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: bare_name(path.name).lower(),
    )
    found = []
    for top in tops:
        found.append(top)
        found.extend(bundled_in(top))
    return found


# -- which copy loads -------------------------------------------------------------
Copy = TypeVar("Copy")


def loading_copy(
    copies: Sequence[Copy],
    version: Callable[[Copy], str] = lambda copy: getattr(copy, "version", ""),
    in_cache: Callable[[Copy], bool] = lambda copy: bool(getattr(copy, "in_cache", False)),
    named: Optional[Callable[[Copy], bool]] = None,
) -> Optional[Copy]:
    """Of the copies of one mod -- one ModID -- the one the game loads.

    The one its loader's log names, where there is a log and it names one:
    that is what happened, not what should.  Otherwise what the loader does
    (``ModLoadingOrder::AddMod``, the same in both games): it takes the copies
    one at a time and a copy replaces the one it holds unless that one's
    version, read :func:`versioning.as_the_loader_reads`, is higher.  So the
    highest version loads, and between equals the last copy it comes to.  How
    a copy got there -- by hand, mod.io's cache, bundled in a host -- plays no
    part.

    ``copies`` come in the order the Studio lists them: the mods folders, then
    the mod.io cache.  The game comes to the cache first.  That was measured,
    not assumed: against Anno 1800's own log on the machine this was written
    on, it names the copy this picks in all 38 ModIDs that had more than one,
    where each of the three rules it replaced was wrong about five.  Anno 117
    has no ModID twice there or anywhere in the census, so its order is taken
    to be the same.
    """
    if named is not None:
        for copy in copies:
            if named(copy):
                return copy
    best = None
    for copy in [one for one in copies if in_cache(one)] + [one for one in copies if not in_cache(one)]:
        if best is None or not (
            versioning.as_the_loader_reads(version(best)) > versioning.as_the_loader_reads(version(copy))
        ):
            best = copy
    return best


# -- what an origin says ----------------------------------------------------------
# A layer's origin is where its mod lives, below the mods folder it was read
# from: ``<folder>`` for a mod of its own; ``<host>/<folder>`` for one bundled
# inside another, deeper for one bundled inside a bundled one; and
# ``<mod.io id>/<folder>`` for a mod the game's own client installed, whose
# numbered folder is where it was downloaded to and not a host.  So a bundled
# mod inside a downloaded one is ``<mod.io id>/<host>/<folder>``.
def _split(origin: str) -> Tuple[List[str], int]:
    """The origin's parts, and how many of them name its top-level mod."""
    parts = (origin or "").split("/")
    return parts, 2 if len(parts) > 1 and parts[0].isdigit() else 1


def is_bundled(origin: str) -> bool:
    """Whether the mod at ``origin`` lives inside another mod's folder."""
    parts, top = _split(origin)
    return len(parts) > top


def host_of(origin: str) -> str:
    """The origin of the top-level mod ``origin`` is bundled in -- the one
    whose switch it answers to -- or ``origin`` itself for a mod that is not
    bundled."""
    parts, top = _split(origin)
    return "/".join(parts[:top])


def switch_holder(origin: str, loaded) -> str:
    """The origin among ``loaded`` whose switch the mod at ``origin`` answers
    to: the outermost of them it is inside, or its own -- or "" where it is
    none of them and inside none.  See :meth:`Loadout.switch_of`."""
    place = host_of(origin)
    if place in loaded:
        return place
    for part in origin[len(place):].split("/")[1:]:
        place += "/" + part
        if place in loaded:
            return place
    return ""


def listed_host(origin: str, loaded) -> str:
    """The origin among ``loaded`` that the mod at ``origin`` is straight
    inside: the innermost of them it is bundled in, or "" for none."""
    top = host_of(origin)
    place = origin
    while place != top and "/" in place:
        place = place.rsplit("/", 1)[0]
        if place in loaded:
            return place
    return ""


def cache_id(origin: str) -> int:
    """The mod.io id of the download folder ``origin`` sits in; 0 for a mod
    the game's client did not install."""
    parts, top = _split(origin)
    return int(parts[0]) if top == 2 else 0


def folder_name(origin: str) -> str:
    """The name of the mod's own folder: the last part of its origin."""
    return (origin or "").split("/")[-1]
