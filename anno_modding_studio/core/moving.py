"""Moving a ModOp in a mod's file, as the outline beside it drags one.

Moving an op to another place, or to another of the mod's files, was cutting
its lines out of the text and pasting them in again.  This is that edit, made
for whoever drags the op's row: :func:`moved` says what the text is with one
thing's lines taken from where they are and put where it was dropped, and
which lines are what in that text.  It applies nothing.  The text is typed,
as the cut and paste would have been, until Apply reads it -- and the op,
saying what it said, is the op it was there (:mod:`.modfile`).

**What moves with a thing** is its lines, and the blank lines before it.  An
op's lines hold its note and the comment it is written in when it is
switched off.  A group's are its tags and everything between them.  An
``<Include>``'s are the line and the lines of the file it names, which
follow it: the file goes where the line goes.  A comment of the file's own
that stands loose above an op is no part of it, and stays.

**Where it can go**: before any other thing, among the things of a group, a
file or the patch, or at the end of one of those.  Not into itself, and an
``<Include>`` not into a group, where the game would read its file and the
Studio could not keep it as a file.  Put first in what it goes into, right
after the line that opens it, it goes without the blank lines before it.

What moves into a group or out of one has the depth of its lines changed to
match.  An ``<Include>`` moved into another file names its file from the top
of the mod, which is the same file read from anywhere, and Apply takes it
for the same line.  Nothing else in the lines is touched.

Any row can be moved: an op, a group, a file's line, an op carried unread,
and a ModOp the Studio writes for changes of its own, which Apply puts where
it was moved to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from .generator import (
    INDENT,
    SPAN_ASIDE,
    SPAN_GROUP,
    SPAN_GROUP_END,
    SPAN_INCLUDE,
    Region,
)
from .modfile import Item, Shown, Text

_INCLUDE = re.compile(r"<\s*include\b", re.I)
# The file an <Include> names: what is before the name, its quote, the name,
# and the quote again.
_NAMED = re.compile(r"""(\bFile\s*=\s*(["']))(.*?)(\2)""", re.I)


def is_row(item: Item) -> bool:
    """Whether ``item`` has a row in the outline: everything but a group's
    closing tag, a comment of the file's own and a tweak tool's markup."""
    if item.kind in (SPAN_GROUP_END, SPAN_ASIDE):
        return False
    return not (item.unread is not None and item.unread.markup)


def can_move(shown: Shown, at: int) -> bool:
    """Whether the thing ``shown.items[at]`` can be moved: any that has a
    row.  A ModOp the Studio writes for changes of its own goes where it is
    put as well, and an op carried unread is carried there."""
    if not 0 <= at < len(shown.items):
        return False
    item = shown.items[at]
    if not is_row(item):
        return False
    return not (item.kind == SPAN_INCLUDE and _region_of(shown, at) is None)


def holds_things(shown: Shown, at: int) -> bool:
    """Whether things can be put into ``shown.items[at]``: a group's
    opening tag, or an ``<Include>`` whose file's lines follow it."""
    if not 0 <= at < len(shown.items):
        return False
    item = shown.items[at]
    if item.kind == SPAN_GROUP:
        return True
    return item.kind == SPAN_INCLUDE and _region_of(shown, at) is not None


def containers(shown: Shown) -> List[Optional[int]]:
    """For each item, the item it is inside: the opening tag of its group,
    the ``<Include>`` that names its file, or None for the patch.  A group's
    closing tag is where its opening tag is."""
    of_file: Dict[str, int] = {}
    for at, item in enumerate(shown.items):
        if item.kind == SPAN_INCLUDE and item.op is not None and item.op.includes:
            of_file.setdefault(item.op.includes, at)
    found: List[Optional[int]] = []
    open_groups: List[int] = []
    in_file: Optional[str] = None
    for item in shown.items:
        if item.file != in_file:
            # A group is its tags, and they are in one file.
            in_file = item.file
            open_groups = []
        if item.kind == SPAN_GROUP_END:
            if open_groups:
                open_groups.pop()
            found.append(open_groups[-1] if open_groups else of_file.get(item.file))
            continue
        found.append(open_groups[-1] if open_groups else of_file.get(item.file))
        if item.kind == SPAN_GROUP:
            open_groups.append(len(found) - 1)
    return found


def siblings(shown: Shown, into: Optional[int]) -> List[int]:
    """The things with a row directly inside ``into``, in order."""
    held = containers(shown)
    return [
        at for at, item in enumerate(shown.items)
        if held[at] == into and is_row(item)
    ]


def _region_of(shown: Shown, at: int) -> Optional[Region]:
    """The lines of the file the ``<Include>`` at ``at`` names."""
    item = shown.items[at]
    for region in shown.regions:
        if item.first <= region.line <= item.last:
            return region
    return None


def _end_of_group(shown: Shown, at: int) -> Optional[int]:
    """The closing tag of the group opened at ``at``."""
    group = shown.items[at].op
    uid = group.uid if group is not None else None
    for later in range(at + 1, len(shown.items)):
        item = shown.items[later]
        if item.kind == SPAN_GROUP_END and item.op is not None and item.op.uid == uid:
            return later
    return None


def extent(shown: Shown, at: int) -> Tuple[int, int]:
    """The first and last line of everything that moves with
    ``shown.items[at]``, but the blank lines before it."""
    item = shown.items[at]
    if item.kind == SPAN_GROUP:
        end = _end_of_group(shown, at)
        return item.first, shown.items[end].last if end is not None else item.last
    if item.kind == SPAN_INCLUDE:
        region = _region_of(shown, at)
        if region is not None:
            return item.first, max(item.last, region.last)
    return item.first, item.last


def _headings(shown: Shown) -> set:
    """The lines a heading is drawn on: each file's first, which is the
    file's and no thing's in it."""
    return {region.first for region in shown.regions}


def _blank_before(shown: Shown, lines: List[str], first: int) -> int:
    """How many blank lines stand right before line ``first`` that are the
    thing's own: not one a heading is drawn on."""
    headings = _headings(shown)
    count = 0
    line = first - 1
    while line >= 1 and not lines[line - 1].strip() and line not in headings:
        count += 1
        line -= 1
    return count


def _opening_line(shown: Shown, lines: List[str], into: Optional[int]) -> int:
    """The line that opens ``into``, after which its first thing goes: a
    group's tag, the heading of a file, or the patch's ``<ModOps>``."""
    if into is None:
        for number, line in enumerate(lines, 1):
            if line.strip().startswith("<ModOps"):
                return number
        return 0
    item = shown.items[into]
    if item.kind == SPAN_INCLUDE:
        region = _region_of(shown, into)
        return region.first if region is not None else item.last
    return item.last


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _depth_of(shown: Shown, into: Optional[int]) -> int:
    """How many groups a thing put into ``into`` sits in."""
    if into is None or shown.items[into].kind != SPAN_GROUP:
        return 0
    return shown.items[into].depth + 1


def _file_of(shown: Shown, into: Optional[int]) -> str:
    """Which of the mod's files a thing put into ``into`` is in."""
    if into is None:
        return ""
    item = shown.items[into]
    if item.kind == SPAN_INCLUDE and item.op is not None:
        return item.op.includes
    return item.file


def _inside(held: List[Optional[int]], at: Optional[int], outer: int) -> bool:
    """Whether ``at`` is ``outer`` or anywhere inside it."""
    while at is not None:
        if at == outer:
            return True
        at = held[at]
    return False


def can_go(shown: Shown, at: int, into: Optional[int], before: Optional[int] = None) -> bool:
    """Whether the thing at ``at`` can be put into ``into`` (None for the
    patch), before the thing at ``before`` there, or at its end."""
    if not can_move(shown, at):
        return False
    held = containers(shown)
    if into is not None and (not holds_things(shown, into) or _inside(held, into, at)):
        return False
    if before is not None and (
        not 0 <= before < len(shown.items) or held[before] != into
        or not is_row(shown.items[before])
    ):
        return False
    if shown.items[at].kind == SPAN_INCLUDE:
        # An <Include> with its file's ops under it stands in no group.
        place = into
        while place is not None:
            if shown.items[place].kind == SPAN_GROUP:
                return False
            place = held[place]
    return True


@dataclass
class Moved:
    """A mod's file with one thing moved in it: its text and which lines
    are what, as :class:`.modfile.Shown` has them, and the line the thing
    moved starts on now."""

    shown: Shown
    line: int


def moved(
    shown: Shown, at: int, into: Optional[int], before: Optional[int] = None,
) -> Optional[Moved]:
    """``shown`` with the thing at ``at`` put into ``into`` -- a group's
    opening tag, an ``<Include>`` for its file, or None for the patch --
    before the thing at ``before`` there, or at its end where that is None:
    the text that makes, which lines are what in it, and where the thing
    starts now.  None where it cannot go there (:func:`can_go`), or would
    stay where it is."""
    if not can_go(shown, at, into, before):
        return None
    lines = shown.text.split("\n")
    item = shown.items[at]
    first, last = extent(shown, at)
    gap = _blank_before(shown, lines, first)
    start = first - gap

    # Where it goes: before the line ``to``.
    if before is not None:
        target = shown.items[before]
        to = target.first - _blank_before(shown, lines, target.first)
        like = lines[target.first - 1]
    else:
        inside = siblings(shown, into)
        # Comments of the file's own count here: what is put at the end of
        # a file goes after the last line of it.
        held = containers(shown)
        every = [
            one for one, thing in enumerate(shown.items)
            if held[one] == into and thing.kind != SPAN_GROUP_END
        ]
        if every:
            to = max(extent(shown, every[-1])[1], shown.items[every[-1]].last) + 1
        else:
            to = _opening_line(shown, lines, into) + 1
        if inside:
            like = lines[shown.items[inside[-1]].first - 1]
        elif into is not None and shown.items[into].kind == SPAN_INCLUDE:
            # A file's ops stand where the line that names it does.
            like = lines[shown.items[into].last - 1]
        else:
            opened = _opening_line(shown, lines, into)
            like = _indent(lines[opened - 1] if opened else "") + INDENT
    if before == at:
        return None
    across = False
    if start <= to <= last + 1:
        if into == containers(shown)[at]:
            # Where it is already.
            return None
        # The end of a file and the place just after it are one place in
        # the text: nothing there says where a file ends.  It stays where
        # it is, in another file.
        to = start
        across = True
    keep_gap = to != _opening_line(shown, lines, into) + 1

    old_file = item.file
    new_file = _file_of(shown, into)
    # An <Include> naming its file from where it stood names it from the top
    # of the mod where it stands now: the same file, read from anywhere.
    renamed, renamed_at = "", 0
    if item.kind == SPAN_INCLUDE and new_file != old_file and item.op is not None:
        # Spelled as XML is, the way the Studio writes such a line.
        renamed = escape("/" + item.op.includes.lstrip("/"), {'"': "&quot;", "'": "&apos;"})
        # The line its File is on: the tag's own, or one after it where the
        # tag is written over several.
        tagged = False
        for number in range(item.first, item.last + 1):
            tagged = tagged or _INCLUDE.search(lines[number - 1]) is not None
            if tagged and _NAMED.search(lines[number - 1]):
                renamed_at = number
                break
    shift = len(_indent(like)) - len(_indent(lines[first - 1]))
    block = list(range(first if not keep_gap else start, last + 1))
    moving = set(block)

    def ordered(block: List[int]) -> List[int]:
        """The old lines in the order the text has them now, each by its
        number; 0 for a blank line that is new."""
        order: List[int] = []
        for number in range(1, len(lines) + 2):
            if number == to:
                order.extend(block)
            if number > len(lines):
                break
            if start <= number <= last:
                continue
            order.append(number)
        return order

    def written(order: List[int]) -> List[str]:
        text_lines = []
        for number in order:
            line = lines[number - 1] if number else ""
            if renamed_at and number == renamed_at:
                line = _NAMED.sub(
                    lambda found: found.group(1) + renamed + found.group(4), line, count=1,
                )
            if number in moving and shift and line.strip():
                if shift > 0:
                    line = " " * shift + line
                else:
                    taken = min(-shift, len(_indent(line)))
                    line = line[taken:]
            text_lines.append(line)
        return text_lines

    order = ordered(block)
    text_lines = written(order)
    added = 0
    if text_lines == lines:
        if not across:
            # Two things that say the same, the one put in the other's place.
            return None
        # Moved out of the end of a file, or into it, and the text the same:
        # a blank line before it is what tells the one from the other, and
        # what makes it a move to undo and to apply.
        added = 1
        order = ordered([0, *block])
        text_lines = written(order)
    now = {old: new for new, old in enumerate(order, 1) if old}

    # Which lines are what, as the text now has them.
    deeper = _depth_of(shown, into) - item.depth
    items = []
    for one in shown.items:
        if one.first in moving:
            changed = replace(
                one, first=now[one.first], last=now[one.last],
                depth=one.depth + deeper,
                file=new_file if one.file == old_file else one.file,
            )
            if one is item:
                changed.gap = (gap if keep_gap else 0) + added
            items.append(changed)
        else:
            items.append(replace(one, first=now[one.first], last=now[one.last]))
    items.sort(key=lambda one: one.first)

    regions = []
    for region in shown.regions:
        if region.line in moving:
            regions.append(replace(
                region, line=now[region.line], first=now[region.first], last=now[region.last],
                within=new_file if region.within == old_file else region.within,
            ))
            continue
        # Its heading's line is its own and stays, whatever is taken out.
        new_last = max(
            now[line] for line in range(region.first, region.last + 1)
            if line in now and line not in moving
        )
        if _goes_into(shown, region, into):
            new_last = max(new_last, now[last])
        regions.append(replace(
            region, line=now[region.line], first=now[region.first], last=new_last,
        ))
    regions = _depths(regions)
    found = Shown(origin=shown.origin, items=items, regions=regions)
    found.text = Text("\n".join(text_lines), regions)
    return Moved(found, now[item.first])


def _goes_into(shown: Shown, region: Region, into: Optional[int]) -> bool:
    """Whether what is put into ``into`` is in the file ``region`` is, or
    in a file inside it."""
    held = containers(shown)
    place = into
    while place is not None:
        thing = shown.items[place]
        if thing.kind == SPAN_INCLUDE and thing.op is not None and thing.op.includes == region.file:
            return True
        place = held[place]
    return False


def _depths(regions: List[Region]) -> List[Region]:
    """Each region's depth, from the file it stands in."""
    by_file = {region.file: region for region in regions}

    def depth(region: Region, seen=()) -> int:
        around = by_file.get(region.within)
        if around is None or around.file in seen:
            return 0
        return depth(around, (*seen, region.file)) + 1

    return [replace(region, depth=depth(region)) for region in regions]


def up(shown: Shown, at: int) -> Optional[Moved]:
    """The thing at ``at`` one place up: before the thing above it, or out
    of what it is in, before that."""
    held = containers(shown)
    into = held[at]
    among = siblings(shown, into)
    place = among.index(at) if at in among else -1
    if place > 0:
        return moved(shown, at, into, among[place - 1])
    if into is None:
        return None
    return moved(shown, at, held[into], into)


def down(shown: Shown, at: int) -> Optional[Moved]:
    """The thing at ``at`` one place down: after the thing below it, or out
    of what it is in, after that."""
    held = containers(shown)
    into = held[at]
    among = siblings(shown, into)
    place = among.index(at) if at in among else -1
    if 0 <= place < len(among) - 1:
        after = among[place + 2] if place + 2 < len(among) else None
        return moved(shown, at, into, after)
    if into is None:
        return None
    outer = siblings(shown, held[into])
    there = outer.index(into)
    after = outer[there + 1] if there + 1 < len(outer) else None
    return moved(shown, at, held[into], after)
