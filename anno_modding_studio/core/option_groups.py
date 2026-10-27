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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Set, Tuple

from .generator import SPAN_CHANGES, SPAN_GROUP, SPAN_GROUP_END, SPAN_OP, SPAN_UNREAD


@dataclass(frozen=True)
class Run:
    """Lines of the mod's file, counted from 1, that go into one group:
    things that stand together in it, in the same group and the same file."""

    first: int
    last: int


def _spans(shown, picked: Iterable[Tuple[str, Any]]) -> List[Tuple[int, int, int, str]]:
    """``(first, last, depth, file)`` for each thing picked, whole: a group
    from its opening tag to its closing one."""
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
            found.append((item.first, last, item.depth, item.file))
        elif (
            item.kind == SPAN_CHANGES and any(change.key in wanted_changes for change in item.changes)
        ) or (
            item.kind == SPAN_UNREAD and item.unread is not None and item.unread.order in wanted_unread
        ):
            found.append((item.first, item.last, item.depth, item.file))
    found.sort()
    # Inside a group that is picked whole: that group's already.
    whole: List[Tuple[int, int, int, str]] = []
    for span in found:
        if whole and span[0] <= whole[-1][1]:
            continue
        whole.append(span)
    return whole


def runs(shown, picked: Iterable[Tuple[str, Any]]) -> List[Run]:
    """Where the picked things stand in ``shown`` (a :class:`.modfile.Shown`),
    as runs to wrap: things with nothing else of the file's between them,
    in one group and one file, are one run."""
    spans = _spans(shown, picked)
    found: List[Run] = []
    held = None
    for first, last, depth, file in spans:
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
