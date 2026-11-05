"""Some of a mod's ModOps put under one of its options.

Anno 117 reads a mod's options back in its ModOps, and the simplest use of
one is a switch over part of the mod: ``<Group Condition="$name">`` around
what the option turns on, and a toggle the player can turn off.  This is
that group made from what was picked in the Mod changes panel: the mod's
file as the Mod file tab shows it, with each run of the picked ModOps
wrapped -- one group where they stand together, one for each run where
they do not -- and then applied as typing in the tab is
(:func:`.modfile.apply`), so that it is one step to undo and is read the
way anything typed there is.

What a pick is, is a whole thing in the file: an op or a group kept as
written, a ModOp the Studio writes for changes of its own (with every change
it holds), or an op carried unread.  The pick of something inside a group
that is picked too is the group's.

**An option that has its group already** has what is picked put in there
(:func:`planned`): moved into that group, as dragging its row there in the
Mod file tab's outline does (:mod:`.moving`).  Only what has no such group
beside it -- in the same file, in the same group as it -- gets one of its
own.  A change made after the group was is written at the end of the file,
so for one made last the group is the one before it, with nothing between.
What is gone past to get into a group runs on the other side of what was
moved then: :attr:`Plan.passed` counts it, for the caller to say.  What is
under the option already, in its group, is left where it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Collection, Dict, Iterable, List, Optional, Set, Tuple

from . import moving
from .generator import SPAN_CHANGES, SPAN_GROUP, SPAN_GROUP_END, SPAN_OP, SPAN_UNREAD

# The opening tag of a group that asks one thing and nothing else of it:
# the group this puts things under, as it writes it.
_ASKS_ONE = re.compile(r"""\A<Group\s+Condition\s*=\s*(["'])(.*?)\1\s*>\Z""", re.S)


@dataclass(frozen=True)
class Run:
    """Lines of the mod's file, counted from 1, that go into one group:
    things that stand together in it, in the same group and the same file."""

    first: int
    last: int


def _picked(shown, picked: Iterable[Tuple[str, Any]]) -> List[int]:
    """Where in ``shown.items`` each thing picked is, in file order: the
    outermost of them, a group whole with what is in it."""
    wanted_ops: Set[str] = set()
    wanted_changes: Set[tuple] = set()
    wanted_unread: Set[int] = set()
    for kind, which in picked:
        if kind == "op":
            wanted_ops.add(which)
        elif kind == "change":
            wanted_changes.add(tuple(which))
        elif kind == "unread":
            wanted_unread.add(which)
    found = []
    items = shown.items
    for at, item in enumerate(items):
        if item.kind in (SPAN_OP, SPAN_GROUP) and item.op is not None and item.op.uid in wanted_ops:
            last = item.last
            if item.kind == SPAN_GROUP:
                end = next(
                    (other for other in items[at + 1:]
                     if other.kind == SPAN_GROUP_END and other.depth == item.depth),
                    None,
                )
                if end is None:
                    continue
                last = end.last
            found.append((item.first, last, at))
        elif (
            item.kind == SPAN_CHANGES and any(change.key in wanted_changes for change in item.changes)
        ) or (
            item.kind == SPAN_UNREAD and item.unread is not None and item.unread.order in wanted_unread
        ):
            found.append((item.first, item.last, at))
    found.sort()
    # Inside a group that is picked whole: that group's already.
    whole: List[Tuple[int, int, int]] = []
    for span in found:
        if whole and span[0] <= whole[-1][1]:
            continue
        whole.append(span)
    return [at for _first, _last, at in whole]


def _spans(shown, at: Iterable[int]) -> List[Tuple[int, int, int, str]]:
    """``(first, last, depth, file)`` for each thing at ``at``, whole: a
    group from its opening tag to its closing one."""
    found = []
    for one in at:
        item = shown.items[one]
        last = moving.extent(shown, one)[1] if item.kind == SPAN_GROUP else item.last
        found.append((item.first, last, item.depth, item.file))
    return sorted(found)


def runs(shown, picked: Iterable[Tuple[str, Any]]) -> List[Run]:
    """Where the picked things stand in ``shown`` (a :class:`.modfile.Shown`),
    as runs to wrap: things with nothing else of the file's between them,
    in one group and one file, are one run."""
    return _runs(shown, _picked(shown, picked))


def _runs(shown, at: Iterable[int]) -> List[Run]:
    """The things at ``at`` in ``shown.items``, as :func:`runs` has them."""
    found: List[Run] = []
    held = None
    for first, last, depth, file in _spans(shown, at):
        if held is not None and held[2:] == (depth, file) and not any(
            held[1] < item.first < first for item in shown.items
        ):
            held = (held[0], last, depth, file)
            continue
        if held is not None:
            found.append(Run(held[0], held[1]))
        held = (first, last, depth, file)
    if held is not None:
        found.append(Run(held[0], held[1]))
    return found


def wrapped(text: str, found: List[Run], name: str) -> str:
    """``text`` with each run in ``<Group Condition="$name">``, at the
    run's own indent, and what is in it two spaces further in.  ``name``
    is as the condition reads it: ``mod-id.option`` in full."""
    lines = text.split("\n")
    for run in sorted(found, key=lambda run: run.first, reverse=True):
        inside = lines[run.first - 1:run.last]
        indent = next(
            (line[:len(line) - len(line.lstrip())] for line in inside if line.strip()), ""
        )
        lines[run.first - 1:run.last] = (
            ['%s<Group Condition="$%s">' % (indent, name)]
            + [("  " + line) if line.strip() else line for line in inside]
            + ["%s</Group>" % indent]
        )
    return "\n".join(lines)


# -- into the group an option has already ----------------------------------------
def spellings(name: str, mod_id: str = "") -> Set[str]:
    """What a condition that is the option ``name`` alone says: ``$name``,
    and ``$mod-id.name`` -- the loader reads the two as one."""
    found = {"$" + name}
    if mod_id:
        found.add("$%s.%s" % (mod_id, name))
    return found


def asks_only(shown, at: int, spelled: Collection[str]) -> bool:
    """Whether ``shown.items[at]`` opens a group, switched on, whose one
    attribute is a condition that is one of ``spelled``."""
    item = shown.items[at]
    if item.kind != SPAN_GROUP or item.off:
        return False
    found = _ASKS_ONE.match(shown.opening(item))
    return found is not None and " ".join(found.group(2).split()) in spelled


def _key(item) -> tuple:
    """What tells a thing of the file from the others, wherever it is moved:
    its op, its changes or the op carried unread."""
    return (
        item.kind,
        item.op.uid if item.op is not None else None,
        item.unread.order if item.unread is not None else None,
        tuple(change.key for change in item.changes),
    )


def _index(shown, key: tuple) -> Optional[int]:
    return next((at for at, item in enumerate(shown.items) if _key(item) == key), None)


@dataclass
class Plan:
    """What putting things under an option comes to: ``shown``, the file
    with what goes into the option's groups already moved there, and the
    runs of it still to wrap in a group of their own (:func:`wrapped`)."""

    shown: Any
    left: List[Run] = field(default_factory=list)
    # How many things picked went into a group the option had.
    joined: int = 0
    # How many other things they went past to get there.
    passed: int = 0
    # How many were under it already, in such a group, and stay.
    already: int = 0

    @property
    def nothing_to_do(self) -> bool:
        return not self.joined and not self.left


def planned(shown, picked: Iterable[Tuple[str, Any]], spelled: Collection[str] = ()) -> Plan:
    """The picked things put under the option ``spelled`` is
    (:func:`spellings`): each into the group of it that stands among the
    same things it does, where there is one -- the one it gets to past the
    fewest others, and the one before it where two do -- and the rest left
    to wrap.  With ``spelled`` empty, every one of them is left to wrap."""
    at = _picked(shown, picked)
    if not spelled:
        return Plan(shown, _runs(shown, at))
    held = moving.containers(shown)
    under = {one for one in range(len(shown.items)) if asks_only(shown, one, spelled)}
    chosen = set(at)
    plan = Plan(shown)
    # Each thing, and the group it goes into: the group's key, and whether
    # it goes at the group's end (a group before it) or its start.
    moves: List[Tuple[tuple, tuple, bool]] = []
    left: List[tuple] = []
    passed: Set[tuple] = set()
    for one in at:
        # The option's group itself is under it already.
        place = one if one in under else held[one]
        while place is not None and place not in under:
            place = held[place]
        if place is not None:
            plan.already += 1
            continue
        among = moving.siblings(shown, held[one])
        mine = among.index(one) if one in among else -1
        best = None
        for there, other_at in enumerate(among):
            if other_at not in under or mine < 0:
                continue
            low, high = sorted((there, mine))
            gone = [other for other in among[low + 1:high] if other not in chosen]
            # Fewest gone past; the group before it of two that are as near.
            rank = (len(gone), there > mine, abs(there - mine))
            if best is None or rank < best[0]:
                best = (rank, other_at, there < mine, gone)
        if best is None:
            left.append(_key(shown.items[one]))
            continue
        _rank, into_at, at_end, gone = best
        moves.append((_key(shown.items[one]), _key(shown.items[into_at]), at_end))
        passed.update(_key(shown.items[other]) for other in gone)
    # Into a group at its start, in file order each before the first of what
    # it had: so they stand in the order they did.
    firsts: Dict[tuple, Optional[tuple]] = {}
    now = shown
    for thing, group, at_end in moves:
        at_now, into = _index(now, thing), _index(now, group)
        if at_now is None or into is None:
            left.append(thing)
            continue
        before = None
        if not at_end:
            if group not in firsts:
                inside = moving.siblings(now, into)
                firsts[group] = _key(now.items[inside[0]]) if inside else None
            first = firsts[group]
            before = _index(now, first) if first is not None else None
        made = moving.moved(now, at_now, into, before)
        if made is None:
            left.append(thing)
            continue
        now = made.shown
        plan.joined += 1
    plan.shown = now
    plan.passed = len(passed)
    plan.left = _runs(now, sorted(
        found for found in (_index(now, thing) for thing in left) if found is not None
    ))
    return plan
