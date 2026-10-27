"""Which mod on mod.io each loaded mod is, and whether mod.io has a newer file.

Two questions, and the first is the hard one.  A folder in a mods directory
says nothing about where it came from; the answer has to be found somewhere
else, and there are four somewheres, taken in order of how sure they are:

1. **The loader's log.**  Anno 1800 records a mod it loaded from the mod.io
   cache with the cache's absolute path, and that path has the mod.io id in it.
2. **The cache's own state file.**  Every mod the in-game browser installed is
   listed there with its id and the file that was downloaded.
3. **The loadout.**  A mod matched before, by any of these or by hand, keeps
   its id in the loadout file.
4. **Its name.**  mod.io is asked for mods called this, and one whose name
   matches -- category prefix, spacing and an author's name in brackets
   aside (:func:`called`) -- is offered as a match, and said to be one by
   name rather than by anything firmer.

The id belongs to the ModID, not the folder.  The mods folder this was written
against has nineteen mods in the cache and the game loads one of them from
there: the rest are superseded by copies installed by hand under other folder
names.  Attached to a folder, the cache's answer would say nothing about the
copy that actually runs.  Attached to the ModID, it answers for every copy.

The second question is easier but not trivial: versions are strings authors
type.  Where the cache says which file is installed, files are compared.  Where
only versions are known, they are compared as numbers when they read as
numbers, and otherwise the honest answer is *different*, not *newer*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional

from . import disk, library, modfolder, modio, modio_cache, versioning
from .i18n import trn

# How a match was made, surest first.
LOG = "log"
CACHE = "cache"
LOADOUT = "loadout"
NAME = "name"
# Not how the mod was found but how it was made sure of: the zip the copy
# was unpacked from is one of the mod's files.
HASH = "hash"

# What the check concluded about a mod.
UPDATE = "update"  # mod.io has a newer file
CURRENT = "current"  # the installed file is the current one
AHEAD = "ahead"  # installed is newer than mod.io's current file
DIFFERENT = "different"  # the versions differ and do not order
UNKNOWN = "unknown"  # matched, but nothing to compare
UNMATCHED = "unmatched"  # no idea which mod.io mod this is
MISSING = "missing"  # matched by id, but mod.io no longer lists it

_CATEGORY = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
# The author's name after a mod's: "Early Build On Water (CheapOrange)".
_AUTHOR = re.compile(r"\s*\([^()]*\)\s*$")
# A version that is only numbers and dots, which 1.0 and 1.0.0 both are.
_DOTTED = re.compile(r"^\d+(?:\.\d+)*$")


@dataclass
class Match:
    """One loaded mod, and which mod.io mod it has been decided to be."""

    origin: str
    name: str
    mod_id: str  # the ModID its modinfo declares, possibly ""
    installed_version: str = ""
    modio_id: int = 0
    how: str = ""
    # The modfile the cache says is installed, when this copy is the cache's
    # -- or the one the zip it was unpacked from turned out to be.
    installed_file: int = 0
    # The MD5 of the zip a library copy was unpacked from, where it was one.
    zip_md5: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.modio_id)


@dataclass
class Verdict:
    """What the check found for one mod, ready to be shown."""

    origin: str
    name: str
    status: str
    how: str = ""
    modio_id: int = 0
    installed_version: str = ""
    latest_version: str = ""
    latest_file: int = 0
    # Which of the mod's files is installed, where that is known.
    installed_file: int = 0
    date_updated: int = 0
    url: str = ""
    modio_name: str = ""
    # mod.io's side of the comparison, whole: a download needs its file.
    latest: Optional[modio.ModSummary] = None


# -- names ---------------------------------------------------------------------
def strip_category(name: str) -> str:
    """A mod's name without the ``[Category]`` prefixes folder names carry."""
    return _CATEGORY.sub("", name or "").strip()


def same_name(one: str, other: str) -> bool:
    """Whether two names are the same mod's, category prefixes aside."""
    return " ".join(strip_category(one).casefold().split()) == " ".join(
        strip_category(other).casefold().split()
    )


def _letters(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def named_alike(one: str, other: str) -> bool:
    """Whether two names not written the same are still the same mod's:
    spaces and punctuation aside -- a folder's "Hexagon Maptemplate
    (Taludas)" of mod.io's "Hexagon Map Template (Taludas)" -- or the
    author's name in brackets after one and not the other, as a folder named
    "Early Build On Water (CheapOrange)" is of mod.io's "Early Build On
    Water".  Two different authors in brackets are two different mods."""
    one, other = strip_category(one), strip_category(other)
    if _letters(one) and _letters(one) == _letters(other):
        return True
    bare_one, bare_other = _AUTHOR.sub("", one), _AUTHOR.sub("", other)
    if (bare_one != one) == (bare_other != other):
        return False
    return bool(_letters(bare_one)) and _letters(bare_one) == _letters(bare_other)


def called(candidates: Iterable[modio.ModSummary], wanted: str) -> Optional[modio.ModSummary]:
    """The one of mod.io's ``candidates`` that is the mod called ``wanted``:
    of the same name, or else of a name :func:`named_alike` -- the folder
    names mods are installed under by hand are not always mod.io's."""
    candidates = list(candidates)
    return next((one for one in candidates if same_name(one.name, wanted)), None) or next(
        (one for one in candidates if named_alike(one.name, wanted)), None
    )


# -- the cache, by ModID --------------------------------------------------------
def cache_index(game: str, at=None) -> Dict[str, modio_cache.CachedMod]:
    """Lower-cased ModID -> the cache record for it, for every mod the game's
    own client installed.  Bundled mods count: a shared piece inside a cached
    mod is the same shared piece wherever else it is installed."""
    index: Dict[str, modio_cache.CachedMod] = {}
    for record in modio_cache.installed(game, at):
        for folder in modfolder.bundled_in(record.path):
            declared = str(modfolder.metadata_of(folder).get("mod_id") or "").strip()
            if declared:
                index.setdefault(declared.lower(), record)
    return index


def learn_from_cache(library_dir, game: str, at=None) -> int:
    """Write down, against each copy in the library no mod.io mod is known
    for, the mod.io mod the game's own client installed under the same ModID
    -- what a check for updates would match it by, learned without asking
    mod.io anything.  How many copies were.

    For a library filled before the library recorded where its copies came
    from: what it can say of them now, without a network.
    """
    index = cache_index(game, at)
    if not index:
        return 0
    learned = 0
    for mod in library.catalogue(library_dir):
        if mod.kind != library.COPY or mod.modio_id or not mod.mod_id:
            continue
        record = index.get(mod.mod_id.lower())
        if record is not None and library.learn(library_dir, mod.target.name, record.mod_id, CACHE):
            learned += 1
    return learned


# -- deciding which mod is which ------------------------------------------------
def identify(
    layers: Iterable,
    logged: Dict[str, int],
    cache: Dict[str, modio_cache.CachedMod],
    library_dir=None,
) -> List[Match]:
    """A :class:`Match` per loaded mod, from what is known without asking mod.io.

    ``library_dir`` is the Studio's library, which wrote down which mod.io
    file each copy in it was installed from (``library.registry``).  The
    install plan compares files by that; without it here, a mod the Studio
    installed was compared by version text alone, and the two disagreed when
    mod.io replaced a file under the same version.
    """
    installed = library.registry(library_dir) if library_dir is not None else {}
    by_id = {record.mod_id: record for record in cache.values()}
    found: List[Match] = []
    for layer in layers:
        match = Match(
            origin=layer.origin,
            name=layer.name or layer.origin,
            mod_id=layer.mod_id or "",
            installed_version=layer.version or "",
        )
        record = cache.get((layer.mod_id or "").lower())
        if layer.origin in logged:
            match.modio_id, match.how = logged[layer.origin], LOG
            record = by_id.get(match.modio_id, record)
        elif record is not None:
            match.modio_id, match.how = record.mod_id, CACHE
        elif getattr(layer, "modio_id", 0):
            match.modio_id, match.how = int(layer.modio_id), LOADOUT
        # The cache knows which *file* it installed, but only of its own copy:
        # a copy of the same ModID installed by hand is whatever version its
        # modinfo says, and files cannot be compared for it.
        if record is not None and disk.inside(getattr(layer, "folder", ""), record.path):
            match.installed_file = record.modfile_id
            if not match.installed_version:
                match.installed_version = record.version
        facts = _installed_as(layer, library_dir, installed)
        if facts:
            # The library's own copy, and what the Studio installed it as.
            theirs = int(facts.get("modio_id") or 0)
            if theirs and not match.modio_id:
                match.modio_id, match.how = theirs, LOADOUT
            if theirs == match.modio_id and not match.installed_file:
                match.installed_file = int(facts.get("modfile_id") or 0)
                if not match.installed_version:
                    match.installed_version = str(facts.get("version") or "")
        found.append(match)
    return found


def _installed_as(layer, library_dir, installed: Dict[str, dict]) -> dict:
    """What the library wrote down for the copy ``layer`` is read from: its
    folder in the library, or the junction to it in the mods folder."""
    folder = getattr(layer, "folder", "")
    if not folder or not installed or library_dir is None:
        return {}
    # By where the folder is, and not by its name: a folder of the same name
    # somewhere else -- the game's own download of the mod -- is not the copy.
    if library.holds(folder, library_dir):
        # Through a link to a generated copy as much as to the copy itself.
        target = library.source_of(folder, library_dir)
        name = (target or Path(folder)).name
    elif not Path(folder).exists():
        # Its link taken away: where Play would put it back.
        name = library.copy_name(library_dir, folder)
    else:
        name = ""
    return installed.get(name, {})


# -- comparing ------------------------------------------------------------------
def compare(match: Match, latest: Optional[modio.ModSummary]) -> Verdict:
    verdict = Verdict(
        origin=match.origin,
        name=match.name,
        status=UNMATCHED,
        how=match.how,
        modio_id=match.modio_id,
        installed_version=match.installed_version,
        installed_file=match.installed_file,
    )
    if not match.matched:
        return verdict
    if latest is None:
        verdict.status = MISSING
        return verdict
    verdict.latest = latest
    verdict.latest_version = latest.version
    verdict.latest_file = latest.modfile_id
    verdict.date_updated = latest.date_updated
    verdict.url = latest.url
    verdict.modio_name = latest.name
    if match.installed_file and latest.modfile_id:
        verdict.status = CURRENT if match.installed_file == latest.modfile_id else UPDATE
        return verdict
    mine, theirs = match.installed_version.strip(), latest.version.strip()
    if not mine or not theirs:
        verdict.status = UNKNOWN
    elif mine.casefold() == theirs.casefold():
        verdict.status = CURRENT
    else:
        # Trimmed, as releases and the Studio's own update check compare:
        # an installed 1.0 is mod.io's 1.0.0, not one behind it.
        ours, latest_key = versioning.trimmed(mine), versioning.trimmed(theirs)
        if not versioning.key(mine) or not versioning.key(theirs):
            verdict.status = DIFFERENT
        elif ours < latest_key:
            verdict.status = UPDATE
        elif ours > latest_key:
            verdict.status = AHEAD
        elif _DOTTED.match(mine) and _DOTTED.match(theirs):
            verdict.status = CURRENT
        else:
            verdict.status = DIFFERENT
    return verdict


# -- the whole check -----------------------------------------------------------
Progress = Callable[[int, int, str], None]


def check(
    client: modio.Client,
    game_id: int,
    matches: List[Match],
    progress: Optional[Progress] = None,
) -> List[Verdict]:
    """Ask mod.io about every match, and try the unmatched ones by name: a
    verdict per match, in their order.  See :func:`check_each`."""
    found = {verdict.origin: verdict for verdict in check_each(client, game_id, matches, progress)}
    return [found[match.origin] for match in matches]


def check_each(
    client: modio.Client,
    game_id: int,
    matches: List[Match],
    progress: Optional[Progress] = None,
) -> Iterator[Verdict]:
    """:func:`check`, a verdict at a time, each as soon as it is decided.

    One request per hundred matched mods, which decides every one of them at
    once -- each made sure of by its zip first, where it has one -- then one
    per unmatched mod: the name search is the expensive half, and
    ``progress`` counts it.  So the matched mods come first, and the rest in
    their order as each is looked up.
    """
    say = progress or (lambda done, total, name: None)
    unmatched = [match for match in matches if not match.matched]
    total = len(unmatched) + 1
    # The first step says what it is doing in full; the ones after it name
    # the mod being looked up.
    say(0, total, trn(
        "Asking mod.io about %(n)s matched mod…", "Asking mod.io about %(n)s matched mods…",
        len(matches) - len(unmatched),
    ))
    latest = client.mods_by_ids(game_id, [m.modio_id for m in matches if m.matched])
    for match in matches:
        if match.matched:
            tell_by_hash(client, game_id, match, latest.get(match.modio_id))
            yield compare(match, latest.get(match.modio_id))
    for done, match in enumerate(unmatched, start=1):
        wanted = strip_category(match.name)
        say(done, total, wanted)
        candidate = called(client.search(game_id, wanted), wanted) if wanted else None
        if candidate is not None:
            match.modio_id, match.how = candidate.id, NAME
            latest[candidate.id] = candidate
        tell_by_hash(client, game_id, match, latest.get(match.modio_id))
        yield compare(match, latest.get(match.modio_id))
    say(total, total, "")


def tell_by_hash(client: modio.Client, game_id: int, match: Match,
                 latest: Optional[modio.ModSummary]) -> None:
    """Make sure of a match by the zip the copy was unpacked from, where it
    was unpacked from one: mod.io publishes the MD5 of each of its files.

    A zip that is one of the mod's files settles two things a name cannot --
    that this is the mod, and which of its files is installed, so "is there
    an update" is the files compared and not two version strings.  The
    current file is in hand already; an older one costs a request for the
    mod's files.  A zip that is none of them leaves the match as it was: a
    repacked or edited zip of the right mod is not evidence of the wrong one.
    """
    if not match.zip_md5 or not match.matched or match.installed_file or latest is None:
        return
    if latest.md5 and latest.md5 == match.zip_md5.lower():
        found = latest.modfile_id
    else:
        try:
            found = client.file_with_md5(game_id, match.modio_id, match.zip_md5)
        except modio.Cancelled:
            raise
        except modio.ModioError:
            return  # the check goes on without it
    if found:
        match.installed_file, match.how = found, HASH
