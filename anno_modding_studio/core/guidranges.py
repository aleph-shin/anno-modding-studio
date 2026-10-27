"""The community's GUID ranges, and which one a new asset should draw from.

Ubisoft gave modders one *safe* span of GUIDs -- 1,337,471,142 up to the
signed 32-bit ceiling -- and the community keeps a public register of who
has claimed which slice of it, at https://github.com/anno-mods/GuidRanges.
Two mods that both pick "a big number" collide in the player's game, where
nothing can tell them apart; two mods that each stay inside their own listed
range never can.  So the register is the real collision check, and the local
one (other mods in your folder, the base game) is only the part of it that
can be verified from here.

The register is a Markdown table per game, read as the repository serves it.
None of it ships with the Studio.  A snapshot of each table used to be
bundled, but the register carries no licence, so a copy could not be passed
on.  It is fetched instead: the first time a New asset dialog needs it
(:func:`fetch_if_missing`), and whenever *Update now* in Settings is pressed
(:func:`refresh_all`).  The copy is kept beside the indexes.

Until a copy has been fetched, as on a first try with no connection, only
the numbers below are known: the safe span and the register's personal-use
range.  Numbers are facts, not the register's own text, so they can be kept
here.  New assets then draw from personal use, which is safe for anybody,
and the dialog says the register could not be reached.  A failed fetch is
not tried again until the Studio is restarted or *Update now* is pressed, so
a dialog opened again does not wait on the network again.

Who you are decides where your GUIDs come from.  A creator name that matches
a listed owner gets that owner's range; anybody else gets the register's own
*Personal use* range, which is set aside for exactly that.  The old default,
counting up from 1,337,000,000, was neither: it sat *below* the safe span,
in numbers nobody has reserved and Ubisoft has not promised to leave alone.
"""

from __future__ import annotations

import http.client
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import disk, games, places
from .i18n import tr

# The span Ubisoft has said it will never use.  Anything below it is either the
# base game's own, or a pre-announcement range an old mod keeps for its saves.
SAFE_START = 1_337_471_142
SAFE_END = 2_147_483_647

# Where a register came from, as ``Register.source`` says it.
DOWNLOADED = "downloaded"
# No copy has been fetched yet.
NOT_FETCHED = "none"
# No copy has been fetched, and fetching one failed this session.
UNREACHABLE = "unreachable"

# Where the register lives, per game folder in the repository.
REPOSITORY = "https://github.com/anno-mods/GuidRanges"
_RAW = "https://raw.githubusercontent.com/anno-mods/GuidRanges/main/%s/guids.md"
_REPO_FOLDER = {games.ANNO_1800: "1800", games.ANNO_117: "117"}

# Owner cells that are not somebody's name.
_PERSONAL = "personal use"
_RESERVED_WORDS = ("reserved", "range start", "range end")

_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


@dataclass(frozen=True)
class GuidRange:
    """One row of the register: ``owner`` holds ``start``..``end`` inclusive."""

    owner: str
    start: int
    end: int

    @property
    def personal(self) -> bool:
        return self.owner.strip("*_ ").lower() == _PERSONAL

    @property
    def reserved(self) -> bool:
        """Set aside by the register itself rather than claimed by a modder."""
        lowered = self.owner.strip("*_ ").lower()
        return any(word in lowered for word in _RESERVED_WORDS)

    @property
    def safe(self) -> bool:
        return self.start >= SAFE_START

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    def __contains__(self, guid) -> bool:
        try:
            return self.start <= int(guid) <= self.end
        except (TypeError, ValueError):
            return False

    @property
    def label(self) -> str:
        return tr(
            "%(owner)s  (%(start)s to %(end)s)",
            owner=self.owner, start=dotted(self.start), end=dotted(self.end),
        )

    @property
    def aliases(self) -> List[str]:
        """The names this row answers to, as a creator would type one.

        The register writes ``Sabular/Sabunata`` for one person with two
        names, and ``Taludas (Noblesse oblige pop levels)`` for a range
        reserved for one mod, so a slash splits and a parenthesis is dropped.
        """
        bare = re.sub(r"\(.*?\)", "", self.owner)
        return [_normalise(part) for part in bare.split("/") if _normalise(part)]


def dotted(number: int) -> str:
    return f"{number:,}".replace(",", ".")


# The register's own *Personal use* range, the same in both games' tables.
# What new assets draw from while no copy of the register has been fetched.
PERSONAL_USE = GuidRange("Personal use", 2_001_000_000, 2_001_009_999)


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().strip("*_").strip()).casefold()


def _number(cell: str) -> Optional[int]:
    """``1.337.471.142`` -> 1337471142; the register's typos included.

    One row reads ``1701.099.999`` where ``1.701.099.999`` was meant, so the
    separators are simply dropped rather than validated.
    """
    digits = re.sub(r"[.,\s]", "", cell.strip())
    return int(digits) if digits.isdigit() else None


def parse(text: str) -> List[GuidRange]:
    """Every range a ``guids.md`` declares, in the order the file lists them.

    Rows that only mark the table's own edges (``range start``, ``range
    end``) carry one number or none, and are left out; a row's header or
    separator has no numbers at all.
    """
    found: List[GuidRange] = []
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if len(cells) < 3:
            continue
        owner = cells[0].strip().strip("*_").strip()
        start, end = _number(cells[1]), _number(cells[2])
        if not owner or start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        found.append(GuidRange(owner=owner, start=start, end=end))
    return found


class Register:
    """The ranges known for one game, and the questions asked of them."""

    def __init__(self, ranges: Sequence[GuidRange], source: str = ""):
        self.ranges: List[GuidRange] = list(ranges)
        # Where these came from, for the dialog to say: a fetched copy
        # (DOWNLOADED), or none yet (NOT_FETCHED, UNREACHABLE).
        self.source = source

    @property
    def missing(self) -> bool:
        """No copy of the register has been fetched, so only the
        personal-use range is known and no creator name can be matched."""
        return self.source in (NOT_FETCHED, UNREACHABLE)

    def personal(self) -> Optional[GuidRange]:
        for item in self.ranges:
            if item.personal:
                return item
        return None

    def for_creator(self, creator: str) -> List[GuidRange]:
        """The ranges a creator name has claimed, best first.

        Best is the first the register lists inside the safe span: many
        long-standing authors also hold an old pre-announcement range that
        they keep only for their savegames, and nobody should start new work
        in one of those.
        """
        wanted = _normalise(creator)
        if not wanted:
            return []
        mine = [
            item
            for item in self.ranges
            if not item.personal and not item.reserved and wanted in item.aliases
        ]
        return sorted(mine, key=lambda item: (not item.safe, item.start))

    def listed_as(self, names) -> Tuple[str, List[GuidRange]]:
        """The first of ``names`` the register lists, with its ranges best
        first: your author name, then the others you have published under,
        in their order.  ``("", [])`` where none is listed."""
        for name in names:
            found = self.for_creator(name)
            if found:
                return name.strip(), found
        return "", []

    def owners_of(self, guid) -> List[GuidRange]:
        """Every listed range a GUID falls in (they do overlap in places)."""
        return [item for item in self.ranges if guid in item]

    def named(self, owner: str) -> List[GuidRange]:
        """The ranges listed under exactly this owner cell."""
        return [item for item in self.ranges if item.owner == owner]

    def claimable(self) -> List[GuidRange]:
        """Ranges a loadout could draw from: real people's, plus personal use.

        The register lists *Personal use* twice, once in its own section and
        once in place among the public ranges; it is offered once.
        """
        out: List[GuidRange] = []
        for item in self.ranges:
            if item.reserved or item in out:
                continue
            out.append(item)
        return out

    @staticmethod
    def free_guid(
        ranges: Sequence[GuidRange], taken: Callable[[str], bool]
    ) -> Optional[int]:
        """The lowest GUID in ``ranges`` that ``taken`` does not object to.

        Walked in the order given, so the caller's best range is tried
        first.  None only when every number in every range is spoken for.
        """
        for item in ranges:
            for candidate in range(item.start, item.end + 1):
                if not taken(str(candidate)):
                    return candidate
        return None


# -- where the register is read from -----------------------------------------
# Games whose register could not be fetched this session, by game key.
_UNREACHABLE: Set[str] = set()


def downloaded_path(game_key: str) -> Path:
    """Where a fetched copy is kept: beside the built indexes."""
    return places.cache_dir() / "guidranges" / ("%s.md" % games.get(game_key).key)


def load(game_key: str) -> Register:
    """The register for a game, as last fetched.  Never touches the network.

    With no copy fetched, it is the personal-use range alone, marked
    :attr:`Register.missing`.  So is a copy that holds no ranges at all, such
    as a server error page saved as if it were the file: read as a register,
    it would say nothing is claimed.
    """
    key = games.get(game_key).key
    try:
        ranges = parse(downloaded_path(game_key).read_text(encoding="utf-8"))
    except OSError:
        ranges = []
    if ranges:
        return Register(ranges, source=DOWNLOADED)
    source = UNREACHABLE if key in _UNREACHABLE else NOT_FETCHED
    return Register([PERSONAL_USE], source=source)


def fetch_if_missing(game_key: str, timeout: float = 10.0) -> Register:
    """The register for a game, fetched first when no copy has been yet.

    Called as a New asset dialog opens, which is where the register is first
    needed.  When the fetch fails, the register is :func:`load`'s missing one,
    marked :data:`UNREACHABLE`, and it is not tried again this session:
    offline, every New asset dialog would otherwise wait on the network
    first.  *Update now* (:func:`refresh`) always tries.
    """
    register = load(game_key)
    if not register.missing or register.source == UNREACHABLE:
        return register
    try:
        return refresh(game_key, timeout=timeout)
    # An answer cut short is an http.client error, not an OSError (A10-38).
    except (OSError, ValueError, http.client.HTTPException):
        _UNREACHABLE.add(games.get(game_key).key)
        return load(game_key)


def refresh(game_key: str, timeout: float = 15.0) -> Register:
    """Download the register afresh and keep it for next time.

    Raises ``OSError`` (which ``urllib.error.URLError`` is) when it cannot,
    or ``ValueError`` when what came back is not a register, and in either
    case leaves whatever was kept before untouched.
    """
    folder = _REPO_FOLDER.get(games.get(game_key).key)
    if folder is None:
        raise ValueError("no community register is kept for %s" % game_key)
    url = _RAW % folder
    with urllib.request.urlopen(url, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    ranges = parse(text)
    if not ranges:
        raise ValueError(tr("%s did not read as a table of GUID ranges", url))
    target = downloaded_path(game_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    disk.write_text(target, text)
    _UNREACHABLE.discard(games.get(game_key).key)
    return Register(ranges, source=DOWNLOADED)


def refresh_all(timeout: float = 15.0) -> Tuple[Dict[str, Register], Dict[str, str]]:
    """Refresh every game's register.

    Returns what was fetched and, per game that could not be, why -- one
    game's failure should not stop the other's from being kept.
    """
    fetched: Dict[str, Register] = {}
    failed: Dict[str, str] = {}
    for key in _REPO_FOLDER:
        try:
            fetched[key] = refresh(key, timeout=timeout)
        # An answer cut short is an http.client error, not an OSError (A10-38).
        except (OSError, ValueError, http.client.HTTPException) as exc:
            failed[key] = str(exc)
    return fetched, failed
