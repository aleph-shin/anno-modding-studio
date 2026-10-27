"""A mod's version: what the author calls a release, and how to move it on.

The version is the one number players see -- in the modinfo, on mod.io, in a
mod manager's update column -- and it is the author's.  The Studio never moves
it on its own.  What it offers is the three moves authors already make by
hand, spelled so that each says what it is for:

* **major**: something saves or other mods rely on has broken;
* **minor**: something new, nothing broken;
* **patch**: the same things, tuned or fixed.

``MAJOR.MINOR.PATCH`` because that is what most mods already write and what
:func:`key` already orders correctly.  :func:`key` is also how every other
part of the Studio compares two versions: which copy of a mod is newer, whether
mod.io has moved on, whether a release is behind -- by way of :func:`trimmed`
wherever the question is whether one is newer or enough, so ``1.0`` and
``1.0.0`` are one version.  A version that is not
numbers -- ``v2``, ``1.4b``, a date -- is left exactly as typed; the moves are
simply not offered until the author chooses to switch, and :func:`adopted`
is what they would switch to.

See ``docs/proposals/versioning-proposal.md``, section 2.1.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .i18n import tr_noop

MAJOR = "major"
MINOR = "minor"
PATCH = "patch"
LEVELS = (MAJOR, MINOR, PATCH)

# What each move is called and what it is for, in the words the Export tab uses.
NAME = {
    # Translators: the three parts of a version number, MAJOR.MINOR.PATCH.
    MAJOR: tr_noop("Major", context="version part"),
    MINOR: tr_noop("Minor", context="version part"),
    PATCH: tr_noop("Patch", context="version part"),
}
MEANING = {
    MAJOR: tr_noop("breaks saves or other mods"),
    MINOR: tr_noop("adds something"),
    PATCH: tr_noop("tunes or fixes"),
}

_NUMBERED = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_NUMBERS = re.compile(r"\d+")


def key(version) -> Tuple[int, ...]:
    """A version to order by: every number in it, in order.

    ``1.10`` after ``1.9``, ``v2`` after ``1.4b``, whatever else the version
    is spelled with.  ``()`` for a version with no numbers, which orders
    before any that has them; a caller that must not judge such a version
    asks whether the key is empty.
    """
    return tuple(int(piece) for piece in _NUMBERS.findall(str(version or "")))


def trimmed(version) -> Tuple[int, ...]:
    """:func:`key` without the zeros at its end, so ``1.3`` and ``1.3.0`` are
    the same version.  For asking whether one version is enough for, or
    newer than, another; ``()`` for ``0.0`` as for no numbers, so a caller
    that must not judge a version without numbers asks :func:`key`.
    """
    numbers = list(key(version))
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


_LEADING_DIGITS = re.compile(r"[0-9]+")


def as_the_loader_reads(version) -> Tuple[int, int, int]:
    """``(major, minor, patch)`` the way the game's mod loader reads a version
    when it has two copies of one mod to choose between.

    From its source (``ModLoadingOrder::Version``, the same in both games):
    split at the dots, empty parts dropped, and the leading digits of the
    first three parts read as unsigned numbers -- anything else, or a number
    too big, is 0.  So ``1.4b`` is ``(1, 4, 0)``, a fourth part counts for
    nothing, and ``v2`` is ``(0, 0, 0)``, which :func:`key` would call 2.
    Only for that choice; everything else orders versions by :func:`key`.
    """
    numbers = []
    for piece in [piece for piece in str(version or "").split(".") if piece][:3]:
        found = _LEADING_DIGITS.match(piece)
        value = int(found.group()) if found else 0
        numbers.append(value if value <= 0xFFFFFFFF else 0)
    numbers += [0] * (3 - len(numbers))
    return numbers[0], numbers[1], numbers[2]


def parse(version: str) -> Optional[Tuple[int, int, int]]:
    """``(major, minor, patch)``, or None for a version that is not numbered.

    One to three numbers separated by dots and nothing else.  Fewer than three
    are read as zeros, so ``1.0`` is ``(1, 0, 0)``.
    """
    found = _NUMBERED.match((version or "").strip())
    if not found:
        return None
    return tuple(int(part or 0) for part in found.groups())  # type: ignore[return-value]


def is_numbered(version: str) -> bool:
    return parse(version) is not None


def bump(version: str, level: str) -> Optional[str]:
    """``version`` moved on by one ``level``, always written with three parts.

    None for a version that is not numbered.  ``1.0`` becomes ``1.0.1``,
    ``1.1.0`` or ``2.0.0``: the first move is also where a two-part version
    takes on its third.
    """
    parts = parse(version)
    if parts is None:
        return None
    major, minor, patch = parts
    if level == MAJOR:
        return "%d.0.0" % (major + 1)
    if level == MINOR:
        return "%d.%d.0" % (major, minor + 1)
    if level == PATCH:
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError("unknown level %r" % level)


def adopted(version: str) -> str:
    """What a version that is not numbered becomes, if its author switches.

    Its own numbers, where it has any -- ``v2`` is ``2.0.0``, ``1.4b`` is
    ``1.4.0`` -- so switching never moves a mod backwards; ``1.0.0`` where it
    has none.
    """
    numbers = list(key(version))[:3]
    if not numbers:
        return "1.0.0"
    numbers += [0] * (3 - len(numbers))
    return "%d.%d.%d" % tuple(numbers)


def is_behind(version: str, live: str) -> bool:
    """Whether ``version`` cannot follow ``live`` on mod.io.

    The same version again, or one that orders before it.  Versions that do
    not order -- one of them not numbers at all -- are not behind: the
    author may have their own scheme, and saying no to it would be a guess.
    """
    version, live = (version or "").strip(), (live or "").strip()
    if not live:
        return False
    if version.casefold() == live.casefold():
        return True
    ours, theirs = key(version), key(live)
    return bool(ours) and bool(theirs) and ours < theirs


def next_after(version: str, live: str) -> Optional[str]:
    """The version to publish instead of one that is behind ``live``.

    The patch after whichever of the two is further on, so an author who
    forgot to bump gets the next number rather than a refusal.  None when
    neither has numbers to go on.
    """
    keys = [found for found in (key(version), key(live)) if found]
    if not keys:
        return None
    numbers = list(max(keys))[:3]
    numbers += [0] * (3 - len(numbers))
    suggestion = bump("%d.%d.%d" % tuple(numbers), PATCH)
    if suggestion is None or is_behind(suggestion, live):
        return None
    return suggestion
