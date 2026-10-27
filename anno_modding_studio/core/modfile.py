"""A mod's file as text: what the file tab shows, and what typing there comes to.

The XML view holds one asset's ModOps (:mod:`.typedops`), so an op that is no
one asset's had nowhere to be typed: a pattern, an op naming several assets,
an op in a group, the group itself.  And the order the ops run in, which is
the order of the file, could not be changed at all.  This is the file of one
mod, whole: :func:`shown` is its text as the Studio writes it, with which op
is on which lines, and :func:`apply` makes the mod say what was typed there.

**The text is the file.**  An op that is switched off is the comment it is
written as.

**Which op is which is told by its text**, as it is in the XML view.  The
text the typing started from and the text typed are each read into their ops,
and the two runs are laid side by side.  An op both have is the op the mod
holds: it keeps its uid, so an edit that is an exception to it is still one,
and where it is a change the Studio wrote it is that change still.  An op
only the typed text has is new, and goes where it was typed.  An op only the
other has is taken out, with what it came to.  A group is its tags.

**Only what changed is read.**  An op the text still has, in the groups it
was in, keeps what it came to: that was read where the mod stands among the
others, and nothing typed here knows more.  A new op, one typed over, and one
moved into or out of a group are read as the XML view reads what is typed
there: against the game's files as the mods before this one leave them, with
what the rest of this mod does.  That is close enough to write by.

What this does not do is read again what the change reaches: the ops of this
mod that run after it, and the mods that load after this one.  Each of them
may find what it looks for differently.  That is :mod:`.stale`'s: it marks
them, reads the stack again in the background with this mod as its text
(:func:`ops_of_text`), and hands each such mod's reading to
:func:`take_reading`, which knows every op of the text for the op the mod
holds, the way :func:`apply` does.
"""

from __future__ import annotations

import bisect
import difflib
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from . import (
    build,
    effective,
    games,
    generator,
    keptops,
    loadercheck,
    modops,
    reader,
    typedops,
)
from .changes import SWITCHED_OFF_MARK, Change, Loadout, Op, Unread, switched_on
from .generator import (
    SPAN_ASIDE,
    SPAN_GROUP,
    SPAN_GROUP_END,
    SPAN_INCLUDE,
    SPAN_OP,
)

# Why typed text is refused before it is read, beside typedops' own reasons.
INCLUDE_WITHOUT_A_FOLDER = "include-without-a-folder"
# An <Include> whose file's ops are in the text, and which is not a line
# that names a file kept as a file: on a condition, in a group, or naming
# a file outside the mod.
INCLUDE_NOT_ALONE = "include-not-alone"
# Two lines that name one file.
INCLUDE_TWICE = "include-twice"
# A new line names a file of the mod's that cannot be read.
INCLUDE_UNREADABLE = "include-unreadable"
# An <Include> on a condition, in a group or naming a file outside the mod,
# whose file the reading cannot follow from the mod's folder: it is not
# there, or it is not a path.  Passed by, the line would be gone.
INCLUDE_NOT_FOLLOWED = "include-not-followed"


@dataclass
class Item:
    """One thing in a mod's file as it is shown: an op, a group's opening or
    closing tag, a ModOp the Studio writes for changes of its own, or an op
    carried unread.  ``first`` and ``last`` are its lines, counted from 1."""

    kind: str
    first: int = 0
    last: int = 0
    op: Optional[Op] = None
    unread: Optional[Unread] = None
    changes: Tuple[Change, ...] = ()
    # Written inside a comment, switched off.
    off: bool = False
    # How many groups it sits in.
    depth: int = 0
    # Which of the mod's files it is in: "" for its patch.
    file: str = ""
    # How many blank lines stand before it.
    gap: int = 1


class Text(str):
    """A mod's file as text, with which of its lines are which of the mod's
    other files (:class:`~.generator.Region`): the text is all a reader of
    it is handed, and the files are no part of what it says."""

    regions: tuple = ()

    def __new__(cls, text: str = "", regions=()):
        made = super().__new__(cls, text)
        made.regions = tuple(regions)
        return made


@dataclass
class Shown:
    """One mod's file as the file tab shows it."""

    origin: str = ""
    text: str = ""
    items: List[Item] = field(default_factory=list)
    # The lines that are each of the mod's other files: see Text.
    regions: List[generator.Region] = field(default_factory=list)
    _firsts: List[int] = field(default_factory=list, repr=False, compare=False)
    _lines: List[str] = field(default_factory=list, repr=False, compare=False)

    def opening(self, item: Item) -> str:
        """The tag ``item`` opens with, as it is written, on one line: what
        stands for it in an outline of the file.

        Not the note above an op, nor the comment a switched-off one is
        written in, nor a comment of the author's before an op carried
        unread.  A tag written over several lines is joined with a space
        for each break.  An item with no tag is its first line.
        """
        if not self._lines:
            self._lines = self.text.split("\n")
        lines = self._lines[max(0, item.first - 1):item.last]
        held = False
        tag: List[str] = []
        quote = ""
        for line in lines:
            said = line.strip()
            if not tag:
                if held:
                    held = "-->" not in said
                    continue
                if said.startswith("<!--"):
                    # The switched-off comment opens on a line of its own,
                    # and the op is inside it.
                    held = "-->" not in said and SWITCHED_OFF_MARK not in said
                    continue
                if not said.startswith("<"):
                    continue
            for at, char in enumerate(said):
                if quote:
                    quote = "" if char == quote else quote
                elif char in "\"'":
                    quote = char
                elif char == ">":
                    tag.append(said[:at + 1])
                    break
            else:
                tag.append(said)
                continue
            break
        found = " ".join(tag) or next((line.strip() for line in lines if line.strip()), "")
        # Inside the comment a dash after a dash is written as its entity.
        return found.replace("&#45;", "-") if item.off else found

    def item_at(self, line: int) -> Optional[Item]:
        """What ``line`` is part of, or None for a line between two things."""
        if len(self._firsts) != len(self.items):
            self._firsts = [item.first for item in self.items]
        at = bisect.bisect_right(self._firsts, line) - 1
        if at < 0:
            return None
        item = self.items[at]
        return item if item.first <= line <= item.last else None

    def line_of_op(self, uid: str) -> int:
        """The line the op or group ``uid`` starts on; 0 where it is not in
        the file as an op of its own."""
        for item in self.items:
            if item.op is not None and item.op.uid == uid and item.kind != SPAN_GROUP_END:
                return item.first
        return 0

    def line_of_change(self, key) -> int:
        """The line the ModOp written for the change ``key`` starts on; 0
        where the change is written as part of an op kept as written."""
        key = tuple(key)
        for item in self.items:
            if any(change.key == key for change in item.changes):
                return item.first
        return 0

    def line_of_unread(self, entry: Unread) -> int:
        for item in self.items:
            if item.unread is entry:
                return item.first
        return 0


def shown(loadout: Loadout, origin: str) -> Shown:
    """``origin``'s file as the Studio writes it.

    One text, whatever files the mod keeps: each ``<Include>`` line that
    names one is in it, and the file's ops follow the line
    (:attr:`Shown.regions`)."""
    return shown_of(generator.for_mod(loadout, origin), origin)


def shown_of(exported: Loadout, origin: str, as_held=frozenset()) -> Shown:
    """:func:`shown` for ``exported``, one mod on its own as
    :func:`.generator.for_mod` makes one: for a text of the mod that is not
    the one its changes write (:func:`.readback.shown`).  ``as_held``: see
    :func:`.generator.written`."""
    whole = build.written_inline(exported, as_held=as_held)
    found = Shown(origin=origin, regions=list(whole.regions))
    found.text = Text(whole.text, found.regions)
    depth = 0
    for span in whole.spans:
        if span.kind == SPAN_GROUP_END:
            depth = max(0, depth - 1)
        found.items.append(Item(
            kind=span.kind, first=span.first, last=span.last,
            op=span.op, unread=span.unread, changes=span.changes, off=span.off,
            depth=depth, file=span.file,
            gap=span.op.gap if span.op is not None and span.kind != SPAN_GROUP_END
            else span.unread.gap if span.unread is not None else 1,
        ))
        if span.kind == SPAN_GROUP:
            depth += 1
    return found


# ------------------------------------------------------------ reading the text
class _Before:
    """What the ops of a mod that are read anew start from: the game's files
    as the mods before it leave them, with what the rest of the mod does --
    all of it but ``leaving_out``, the changes of what is being read again
    or taken out.  Everything else is asked of the index."""

    def __init__(self, workspace, origin: str, leaving_out=()):
        self._workspace = workspace
        self._index = workspace.index
        self._origin = origin
        self._rank = workspace.loadout.layer_rank(origin)
        self._leaving_out = set(leaving_out)
        self._held: Dict[str, Any] = {}

    def _counts(self, change: Change) -> bool:
        if change.origin == self._origin:
            return change.key not in self._leaving_out
        return self._workspace.loadout.layer_rank(change.origin) < self._rank

    def asset_element(self, guid: str):
        guid = str(guid)
        if guid not in self._held:
            self._held[guid] = self._asset(guid)
        return self._held[guid]

    def _asset(self, guid: str):
        workspace = self._workspace
        # Of the assets under the GUID, the one found once what counts has
        # run: a copy made by a later mod, or by an op being read again, is
        # not there yet.
        creator, there = workspace.found_among(guid, self._counts)
        if not there:
            return None
        standing = [change for change in workspace.changes_for(guid) if self._counts(change)]
        if creator is None:
            base = self._index.asset_element(guid)
            if not standing:
                return base
        else:
            if creator not in standing:
                standing.append(creator)
            base = effective.parse_payload(creator)
        if base is None:
            return None
        return effective.build(
            base, standing, guid, is_new=creator is not None, game=workspace.loadout.game
        ).as_the_game_reads_it()

    def made_before(self, paths) -> List[tuple]:
        """``(guid, element)`` for each asset that is there and not the
        game's own, of those one of the searches ``paths`` finds: a search
        is answered by those as well as by the game's own.

        Found first among the assets as the whole stack leaves them, which
        the workspace holds ready: making each as it stands for this mod is
        an asset built apiece, and a full mods folder adds thousands.
        """
        if not paths:
            return []
        there = {
            effective.new_asset_guid(change)
            for change in self._workspace.creator_changes() if self._counts(change)
        }
        found = []
        for guid, element in self._workspace.invented_assets(among=there):
            if not any(modops.matches_anything(element, path) for path in paths):
                continue
            as_it_stands = self.asset_element(guid)
            if as_it_stands is not None:
                found.append((guid, as_it_stands))
        return found

    def __getattr__(self, name: str):
        return getattr(self._index, name)


def _folder(loadout: Loadout, origin: str) -> Optional[Path]:
    layer = loadout.layer(origin)
    if layer is not None and layer.folder and Path(layer.folder).is_dir():
        return Path(layer.folder)
    return None


def _ops_in(
    loadout: Loadout, origin: str, root, file: str = "", inline: Optional[list] = None,
    taken: Optional[list] = None,
) -> List[modops.SourceOp]:
    """The ops under ``root``, as ``file`` of ``origin`` would be read ("",
    its patch): an ``<Include>`` is followed from where that file is.
    ``inline`` and ``taken``: see :func:`.modops.parse_ops`."""
    base = _folder(loadout, origin)
    source = None
    if base is not None:
        source = base / (file or games.get(loadout.game).asset_relpath)
    ops = modops.parse_ops(
        root, [], typedops.WHERE, source=source, base=base, game=loadout.game,
        taken=taken, inline=inline, asides=True,
    )
    for op in ops:
        op.mod = origin
        # Until the reader numbers it; one inside a group that is not read
        # is never numbered.
        op.order = -1
        op.file = op.file or file
    return ops


@dataclass
class _Read:
    """A text of a mod's files, read a file at a time."""

    # Every op in the order the game runs them, each saying its file.
    ops: List[modops.SourceOp] = field(default_factory=list)
    # Why it is not something to apply, where it is not.
    stopped: Optional[typedops.Read] = None
    stray: bool = False
    # Files whose line is in the text for the first time: the ones the
    # mod's folder had, with how many ops were read out of each, and the
    # ones it had not.
    read_in: Dict[str, int] = field(default_factory=dict)
    made: List[str] = field(default_factory=list)


_INCLUDE_AT = re.compile(r"<\s*include\b", re.I)
# How much of two texts is laid line against line to find one in the other:
# the lines of the one times the lines of the other.  Past it, only what the
# two start and end with is taken for the same.
_MOST_TO_LAY = 4_000_000


def regions_in(was: Shown, text: str) -> List[generator.Region]:
    """Where the regions of ``was`` are in ``text``, told from the two
    texts alone: for whoever did not hold the text while it was typed in,
    or lost hold of it because all of it was put in at once.

    Each region goes with the lines of the old text that the new one still
    has.  The line that names it has to be one of them; its lines run from
    there to the last line of its own that is still there, so what was
    typed among them is the file's, and what was typed after the last of
    them is not.
    """
    old, new = was.text.split("\n"), text.split("\n")
    if old == new or not was.regions:
        return list(was.regions)
    # The lines both start with and both end with are the same lines.  Only
    # what is between is laid side by side, which for a file of 256,000
    # lines with one op typed into it is the op and not the file: all of
    # it, line against line, took five minutes.
    most = min(len(old), len(new))
    head = 0
    while head < most and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < most - head and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]:
        tail += 1
    between_old, between_new = old[head:len(old) - tail], new[head:len(new) - tail]
    blocks = []
    if between_old and between_new and len(between_old) * len(between_new) <= _MOST_TO_LAY:
        blocks = [
            (head + block.a, head + block.b, block.size) for block in
            difflib.SequenceMatcher(
                None, between_old, between_new, autojunk=False
            ).get_matching_blocks() if block.size
        ]
    grew = len(new) - len(old)

    def now(line: int) -> Optional[int]:
        at = line - 1
        if at < head:
            return line
        if at >= len(old) - tail:
            return line + grew
        for was_at, now_at, size in blocks:
            if was_at <= at < was_at + size:
                return now_at + (at - was_at) + 1
        return None

    found = []
    for region in was.regions:
        named = now(region.line)
        if named is None:
            continue
        first = now(region.first) or named + 1
        last = next(
            (at for at in (now(line) for line in range(region.last, region.first - 1, -1))
             if at is not None),
            first,
        )
        found.append(replace(region, line=named, first=first, last=max(first, last)))
    return found


def _nested(regions, lines: List[str]) -> List[generator.Region]:
    """The regions that are each a run of lines inside the one before it or
    apart from it, with a line that opens an ``<Include>`` standing in the
    file around it.  The lines of one that is not are that file's."""
    kept = [
        region for region in regions
        if 1 <= region.line < region.first <= region.last <= len(lines)
    ]
    while True:
        kept.sort(key=lambda region: (region.first, -region.last))
        parent: Dict[int, Optional[generator.Region]] = {}
        opened: List[generator.Region] = []
        bad = None
        for region in kept:
            while opened and opened[-1].last < region.first:
                opened.pop()
            around = opened[-1] if opened else None
            inside = around is None or region.last <= around.last
            # The line that names it is in the file around it, and in no
            # other file that one holds.
            named_here = (
                (around is None or around.first <= region.line <= around.last)
                and not any(
                    other is not region and other is not around
                    and parent.get(id(other), 0) is around
                    and other.first <= region.line <= other.last
                    for other in kept
                )
                and _INCLUDE_AT.match(lines[region.line - 1].lstrip()) is not None
            )
            if not inside or not named_here:
                bad = region
                break
            parent[id(region)] = around
            opened.append(region)
        if bad is None:
            return kept
        kept.remove(bad)


def _read(loadout: Loadout, origin: str, text: str, regions=(), typed: bool = True) -> _Read:
    """Read ``text`` as ``origin``'s files: cut at the edges of ``regions``
    and each file read on its own, as the game will read it.

    So an element opened in one file and closed in another is an element
    left open in the first, and the text is refused where that is.  The ops
    come back in the one order they run in, each file's after the line that
    names it.

    ``typed`` off is for text the Studio wrote and nobody typed in: it is
    not asked whether text stands loose in an op, which is a question about
    the mod as the loadout holds it.
    """
    found = _Read()
    lines = text.split("\n")
    kept = _nested(list(regions), lines)
    number = {id(region): at for at, region in enumerate(kept)}
    # Whose each line is: the innermost region it is in, or the patch.
    owner: List[Optional[generator.Region]] = [None] * (len(lines) + 1)
    for region in kept:
        for line in range(region.first, region.last + 1):
            owner[line] = region
    own: Dict[Optional[int], List[int]] = {None: []}
    for region in kept:
        own[id(region)] = []
    for line in range(1, len(lines) + 1):
        own[id(owner[line]) if owner[line] is not None else None].append(line)
    marks = {region.line: number[id(region)] for region in kept}
    game = games.get(loadout.game)
    named: Dict[str, int] = {}
    base = _folder(loadout, origin)
    kept_already = {
        op.includes.lower() for op in getattr(loadout, "ops", ())
        if op.origin == origin and op.includes
    }

    def refuse(why: str, line: int) -> None:
        if found.stopped is None:
            found.stopped = typedops.Read(refused=why, line=line)

    def read_file(key: Optional[int], file: str) -> List[modops.SourceOp]:
        at_lines = own[key]
        said = []
        for line in at_lines:
            written = lines[line - 1]
            if line in marks:
                # Which region is this line's, for the reading: in a copy.
                mark = ' %s="%d"' % (modops.REGION, marks[line])
                opens = _INCLUDE_AT.search(written)
                if opens is not None:
                    written = written[:opens.end()] + mark + written[opens.end():]
            said.append(written)
        whole = "\n".join(said)
        if not whole.strip():
            return []

        def whole_line(counted: int) -> int:
            return at_lines[min(max(counted, 1), len(at_lines)) - 1]

        if typed:
            root, stopped = typedops.as_typed(loadout, origin, whole)
            if stopped is not None:
                if found.stopped is None:
                    found.stopped = replace(stopped, line=whole_line(stopped.line))
                return []
            found.stray = found.stray or typedops.between_ops(root)
        else:
            body = whole.strip()
            if "<ModOps" not in body:
                body = "<ModOps>%s</ModOps>" % body
            try:
                root = etree.fromstring(body.encode("utf-8"))
            except etree.XMLSyntaxError as exc:
                raise ValueError(
                    "what is written for %s is not ModOps to read: %s"
                    % (origin or "this mod", exc)
                ) from exc
        told: list = []
        taken: list = []
        ops = _ops_in(loadout, origin, root, file, told, taken)
        carried = {op.line for op in ops if op.verbatim and op.path.startswith("<Include")}
        lines_of = {
            node.sourceline: node for node in root.iter() if modops.is_include(node)
        }

        def passed_by(at: int) -> bool:
            """Whether the reading passed by the <Include> on this line of
            the text, its file not followed and the line not carried: it is
            not there, or is not a path, or the line is switched off."""
            node = lines_of.get(at)
            if base is None or node is None or at in carried:
                return False
            try:
                target = modops.include_target(
                    modops.include_file(node), base / (file or game.asset_relpath), base
                )
            except (OSError, ValueError):
                return True
            return target not in taken
        if key is not None and ops:
            # A file's lines start with the blank one its heading is drawn
            # on, and the reading starts where anything is written: the
            # blank lines before its first op are the rest of them.
            lead = next(
                (at for at, written in enumerate(said) if written.strip()), len(said)
            )
            ops[0].gap = min(modops.MOST_BLANK_LINES, max(0, lead - 1))
        shift = typedops.line_shift(whole)
        after: Dict[int, List[modops.SourceOp]] = {}
        for one in told:
            line = whole_line(one["line"] + shift)
            op = one["op"]
            if op is None:
                if one["region"] is not None:
                    refuse(INCLUDE_NOT_ALONE, line)
                elif base is None:
                    refuse(INCLUDE_WITHOUT_A_FOLDER, line)
                elif passed_by(one["line"]):
                    refuse(INCLUDE_NOT_FOLLOWED, line)
                continue
            name = op.attrs.get(modops.INCLUDES) or generator.file_named(
                op.verbatim, file, game
            )
            if (
                not name or name.startswith("..")
                or name.lower() == Path(game.asset_relpath).as_posix().lower()
            ):
                refuse(INCLUDE_NOT_ALONE, line)
                continue
            if name.lower() in named:
                refuse(INCLUDE_TWICE, line)
                continue
            named[name.lower()] = line
            op.attrs = {modops.INCLUDES: name}
            if one["region"] is not None:
                after[id(op)] = read_file(id(kept[int(one["region"])]), name)
                continue
            # A line typed here.  Its file's ops come in with it where the
            # mod's folder has the file, and it is a new file where not.
            if name.lower() in kept_already:
                # A file the mod keeps already, whose lines the text no
                # longer says: they are in the text somewhere, and read
                # from the folder as well they would be there twice.
                continue
            there = base / name if base is not None else None
            if there is None or not there.is_file():
                found.made.append(name)
                continue
            its_root, _repairs = modops.read_patch(there)
            if its_root is None:
                refuse(INCLUDE_UNREADABLE, line)
                continue
            inside = modops.parse_ops(
                its_root, [], typedops.WHERE, source=there, base=base, game=loadout.game,
                asides=True,
            )
            for held in inside:
                held.mod, held.order = origin, -1
                held.file = held.file or name
            after[id(op)] = inside
            found.read_in[name] = sum(1 for held in inside if not held.attrs.get(modops.INCLUDES))
        spliced: List[modops.SourceOp] = []
        for op in ops:
            spliced.append(op)
            spliced.extend(after.get(id(op), ()))
        return spliced

    found.ops = read_file(None, "")
    return found


_OPENS = re.compile(r"\s*<\s*([^\s/>]+)")


def _as_it_was(loadout: Loadout, origin: str, was: Shown):
    """``(ops, what each is)``: the ops of a file as it was shown, each with
    the thing of the mod's it is part of.

    Read a thing at a time, from the lines the writer said it is on, and
    not out of the whole text by the line the parser gives each: past line
    65,535 that is only near the line an element starts on, and an op of
    *Decorations*, a file of 256,000 lines, was taken for the one before it.
    """
    lines = was.text.split("\n")
    ops: List[modops.SourceOp] = []
    held: List[Item] = []
    inside: List[str] = []
    for item in was.items:
        if item.kind == SPAN_GROUP_END:
            if inside:
                inside.pop()
            continue
        said = "\n".join(lines[item.first - 1:item.last])
        if item.kind == SPAN_GROUP and item.off:
            # Its tag in a comment of its own, switched off: read out of the
            # comment, and the group it makes is off.  The tag goes where
            # the comment opened, directly under its note: a line left
            # between them makes the note a comment standing alone.
            head, mark, body = said.partition("<!-- %s:" % SWITCHED_OFF_MARK)
            if mark:
                said = head + switched_on(body.rsplit("-->", 1)[0]).strip()
        if item.kind == SPAN_GROUP:
            # Its opening tag alone: closed, to be read as the group it is.
            named = _OPENS.match(said.rsplit("-->", 1)[-1])
            said += "</%s>" % (named.group(1) if named else "Group")
        try:
            root = etree.fromstring(("<ModOps>%s</ModOps>" % said).encode("utf-8"))
        except etree.XMLSyntaxError:
            root = None
        # A line that names a file is the line, and nothing of its file.
        told: Optional[list] = [] if item.kind == SPAN_INCLUDE else None
        found = _ops_in(loadout, origin, root, item.file, told) if root is not None else []
        for at, op in enumerate(found):
            if item.kind == SPAN_GROUP and item.off and op.scope:
                op.shelved = True
            op.scopes = (*inside, *op.scopes)
            op.file = item.file
            # Read alone, it has nothing before it to count.
            op.gap = item.gap if at == 0 else op.gap
            if item.kind == SPAN_INCLUDE and item.op is not None:
                op.attrs = {modops.INCLUDES: item.op.includes}
            ops.append(op)
            held.append(item)
        if item.kind == SPAN_GROUP:
            inside.append(next((op.scope for op in found if op.scope), "") or "?")
    return ops, held


def read_as(loadout: Loadout, origin: str, was: Shown) -> List[Optional[Item]]:
    """The thing of the mod's each op of ``was`` is part of, in the order a
    reading of it is handed them: what the number the reading gives each
    (:attr:`Reread.orders`) comes back to."""
    return _as_it_was(loadout, origin, was)[1]


def _has_a_folder(loadout: Loadout, origin: str) -> bool:
    layer = loadout.layer(origin)
    return layer is not None and bool(layer.folder) and Path(layer.folder).is_dir()


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _said(op: modops.SourceOp) -> tuple:
    """What an op says, as two spellings of it agree: what tells one op from
    another."""
    if op.scope:
        return ("group", _flat(modops.opening(op.verbatim)))
    named = op.attrs.get(modops.INCLUDES)
    if op.verbatim and named:
        # A line that names a file is the file it names, however it spells
        # it: moved into another file it names it from the top of the mod
        # (core.moving), and is the same line.
        return ("include", named.lower(), _flat(_unnamed(op.verbatim)))
    if op.verbatim:
        return ("as written", _flat(op.verbatim))
    return ("op", *keptops.canonical_op(modops.render_op(op)))


def _unnamed(tag: str) -> str:
    """An ``<Include>``'s tag without the file it names."""
    try:
        node = etree.fromstring(tag.encode("utf-8"))
    except etree.XMLSyntaxError:
        return tag
    for name in list(node.attrib):
        if name.lower() == "file":
            del node.attrib[name]
    return etree.tostring(node, encoding="unicode", with_tail=False)


def _aim(op: modops.SourceOp) -> tuple:
    """Where an op is aimed and how, whatever it puts there."""
    if op.scope:
        return ("group",)
    if op.verbatim:
        return ("as written", op.path)
    return (op.kind, tuple(op.guids), op.path)


def _whole(ops: List[modops.SourceOp]) -> List[tuple]:
    """Everything a run of ops says, each with the groups it sits in, its
    switch and its note: two runs that agree on this are one file."""
    groups = {op.scope: _said(op) for op in ops if op.scope}
    return [
        (
            _said(op), tuple(groups.get(scope, scope) for scope in op.scopes),
            op.shelved, _flat(op.comment),
        )
        for op in ops
    ]


def _matched(was: List[tuple], typed: List[tuple], aims_was, aims_typed):
    """``(same, over)``: for each typed op that the old text has, where it
    was; and for each typed over one that is gone, which.

    In order first, as two versions of a file are compared.  Then an op that
    is in both and not at the same place has been moved, and is that op
    still.  What is left of the typed ones, where it stands in the place of
    old ones and is aimed where one of them was, is typed over that one.
    """
    same: Dict[int, int] = {}
    gaps = []
    matcher = difflib.SequenceMatcher(None, was, typed, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            for step in range(a1 - a0):
                same[b0 + step] = a0 + step
        elif tag == "replace":
            gaps.append((range(a0, a1), range(b0, b1)))
    left_was = [at for at in range(len(was)) if at not in set(same.values())]
    for at in range(len(typed)):
        if at in same:
            continue
        there = next((old for old in left_was if was[old] == typed[at]), None)
        if there is not None:
            left_was.remove(there)
            same[at] = there
    over: Dict[int, int] = {}
    free = set(left_was)
    for olds, news in gaps:
        for at in news:
            if at in same:
                continue
            there = next(
                (old for old in olds if old in free and aims_was[old] == aims_typed[at]), None
            )
            if there is not None:
                free.discard(there)
                over[at] = there
    return same, over


@dataclass
class Applied:
    """What putting typed text into a mod did."""

    # Ops the text still has, left as they were.
    kept: int = 0
    # Typed over, switched, or under another note.
    changed: int = 0
    added: int = 0
    dropped: int = 0
    # Ops that could not be read, as the text has them now, and why each.
    unread: int = 0
    why_unread: List[str] = field(default_factory=list)
    # Group tags typed in, typed over or taken out: counted apart, since a
    # group is no ModOp to whoever typed it.
    groups: int = 0
    # And so are the lines that name a file, and the comments that are no
    # op's note.
    lines: int = 0
    comments: int = 0
    # The blank lines between things are other than they were.
    spaced: bool = False
    # The ops are the same ones in another order, or in other groups, or
    # in other files.
    rearranged: bool = False
    # Files whose line is in the text for the first time: the ones the
    # mod's folder had, with how many ops came in with each, and the new.
    read_in: Dict[str, int] = field(default_factory=dict)
    made: List[str] = field(default_factory=list)
    # Files whose line is gone, so that their ops are in the file above.
    joined: List[str] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        """Whether the mod says anything it did not say before."""
        return bool(
            self.changed or self.added or self.dropped or self.groups or self.lines
            or self.comments or self.spaced or self.rearranged
        )


def _thing(item: Item) -> tuple:
    """Which thing of the mod's an item of its file is part of: an op or a
    group is one thing however many times its tag is written."""
    if item.op is not None:
        return ("op", item.op.uid)
    if item.unread is not None:
        return ("unread", id(item.unread))
    return ("changes", id(item))


def _shape(change: Change) -> tuple:
    """What a change is written as, whatever it is known by."""
    return (
        change.guid, change.path, change.op, change.tag, change.new_value,
        loadercheck.canonical_fragment(change.payload or ""),
    )


class _Plan:
    """The text a mod's file was and the text typed, side by side: which of
    the typed ops is which of the mod's, and which are to be read."""

    def __init__(self, was: Shown, before, held, typed, again: bool = False):
        self.was, self.before, self.typed = was, before, typed
        # Nothing was typed: the text is the mod's own, read again whole.
        self.again = again
        self.said_before, self.said_now = _whole(before), _whole(typed)
        self.same: Dict[int, int]
        self.over: Dict[int, int]
        if again:
            self.same, self.over = {at: at for at in range(len(typed))}, {}
        else:
            self.same, self.over = _matched(
                [said[0] for said in self.said_before], [said[0] for said in self.said_now],
                [_aim(op) for op in before], [_aim(op) for op in typed],
            )
        # Where in the typed text each op of the old text is, if it is.
        self.now_at = {there: at for at, there in self.same.items()}
        # What each op of the old text is, of what the mod holds.  Several
        # of them are one thing where a group is kept whole, or is written
        # in two parts: the first stands for it.
        self.held: List[Item] = list(held)
        self.leads: Dict[tuple, Tuple[int, Item]] = {}
        self.parts: Dict[tuple, List[int]] = {}
        for at, item in enumerate(self.held):
            if item is not None:
                self.leads.setdefault(_thing(item), (at, item))
                self.parts.setdefault(_thing(item), []).append(at)
        self.scope_at = {op.scope: at for at, op in enumerate(typed) if op.scope}
        # The groups kept whole that say something else inside than they
        # did: where each stands in the typed text.
        self.rewritten: set = set()
        self.fresh = self._to_read()
        # The groups read only so that what is in them is read in them.
        self.context = {
            self.scope_at[scope]
            for at in self.fresh for scope in typed[at].scopes
            if scope in self.scope_at and self.scope_at[scope] not in self.fresh
        }
        self.read_order = sorted(self.fresh | self.context)

    def standing(self, at: Optional[int]) -> Optional[Item]:
        """The thing the old op ``at`` stands for, where it is its first."""
        item = self.held[at] if at is not None else None
        return item if item is not None and self.leads[_thing(item)][0] == at else None

    def _to_read(self) -> set:
        """The typed ops whose reading is taken anew: one the old text has
        not, and one that is in other groups than it was."""
        typed = self.typed
        if self.again:
            # Every op and every op carried unread.  A change the Studio
            # wrote says what it does whatever it finds, and stays as it is.
            return {
                at for at, item in enumerate(self.held)
                if item is not None and (item.op is not None or item.unread is not None)
            }
        fresh = set()
        for at in range(len(typed)):
            there = self.same.get(at)
            if there is None or self.said_now[at][1] != self.said_before[there][1]:
                fresh.add(at)
        # A group kept whole is one thing, read whole: where anything in it
        # is other than it was, all of it is read again.
        inside: Dict[str, List[int]] = {}
        for at, op in enumerate(typed):
            for scope in op.scopes:
                inside.setdefault(scope, []).append(at)
        for thing, (lead, item) in self.leads.items():
            parts = self.parts[thing]
            head = self.now_at.get(lead)
            # Not a group kept as a group whose tag is written twice, round
            # something that stands in the middle of it: each of those is a
            # tag, and what is in it is each an op of its own.
            if item.kind != SPAN_OP or len(parts) < 2 or head is None or not typed[head].scope:
                continue
            now = inside.get(typed[head].scope, [])
            if len(now) != len(parts) - 1 or any(
                self.same.get(one) != there
                or self._in_its_group(self.said_now[one], typed[head])
                != self._in_its_group(self.said_before[there], self.before[lead])
                for one, there in zip(now, parts[1:], strict=True)
            ):
                self.rewritten.add(head)
            if head in self.rewritten or head in fresh or any(one in fresh for one in now):
                fresh.add(head)
                fresh.update(now)
        return fresh

    @staticmethod
    def _in_its_group(said: tuple, group: modops.SourceOp) -> tuple:
        """What an op inside ``group`` says, whatever ``group`` is in."""
        return (said[0], said[1][len(group.scopes) + 1:], *said[2:])

    def still_part(self, at: int) -> bool:
        """Whether the typed op ``at``, which the old text has as a later
        part of a thing, is a part of that thing still.  A group's tag
        written twice is; an op of a group kept whole is while it stands in
        that group, and out of it is an op of its own."""
        part = self.held[self.same[at]]
        if part.kind != SPAN_OP:
            return True
        head = self.now_at.get(self.leads[_thing(part)][0])
        return head is not None and self.typed[head].scope in self.typed[at].scopes

    def as_it_was(self, thing: tuple) -> bool:
        """Whether a thing of the mod's is in the typed text and not read
        again: it stays exactly what it is."""
        at = self.now_at.get(self.leads[thing][0])
        return at is not None and at not in self.fresh

    def leaving(self, loadout: Loadout, origin: str) -> set:
        """The changes of what is read again or taken out: what the ops read
        anew are read without."""
        under: Dict[str, List[Change]] = {}
        for change in loadout.changes_of(origin):
            if change.op_uid:
                under.setdefault(change.op_uid, []).append(change)
        out: set = set()
        for thing, (_lead, item) in self.leads.items():
            if self.as_it_was(thing):
                continue
            held = under.get(item.op.uid, ()) if item.op is not None else item.changes
            out.update(change.key for change in held)
        return out


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BARE_GROUP = re.compile(r"<Group\s*/?>")


def bare_groups(text: str) -> int:
    """How many ``<Group>`` tags in ``text`` have nothing on them: a bracket
    to the loader, read straight through, which the file the Studio writes
    has no place for (:func:`.modops._hand_on_the_note`)."""
    return len(_BARE_GROUP.findall(_COMMENT.sub("", text or "")))


def apply(
    workspace, origin: str, text: str, was: Shown, regions=None,
) -> Tuple[typedops.Read, Optional[Applied]]:
    """Make ``origin`` say what ``text`` says, where ``was`` is the text the
    typing started from; what was read, and what that did -- None where the
    text is not something to apply, and the reading says why.

    ``regions`` are the lines of ``text`` that are each of the mod's other
    files, as whoever held the text while it was typed in has them now
    (:attr:`Shown.regions` when it was shown).  Left out, they are worked
    out from the two texts (:func:`regions_in`).

    The caller records the step and redraws.
    """
    loadout: Loadout = workspace.loadout
    read = _read(
        loadout, origin, text, regions_in(was, text) if regions is None else regions
    )
    if read.stopped is not None:
        return read.stopped, None
    typed = read.ops
    found = typedops.Read(ops=len(typed), written=True)
    found.stray = read.stray
    before, held = _as_it_was(loadout, origin, was)
    plan = _Plan(was, before, held, typed)
    if plan.said_before == plan.said_now and (
        [(op.file, op.gap) for op in before] == [(op.file, op.gap) for op in typed]
    ):
        return found, Applied(kept=len(typed))
    # Every op where it was, in the file and the groups it was in: only the
    # blank lines between them are other than they were.
    only_spaced = plan.said_before == plan.said_now and (
        [op.file for op in before] == [op.file for op in typed]
    )

    report = None
    if plan.read_order:
        view = _Before(workspace, origin, plan.leaving(loadout, origin))
        asked = [typed[at] for at in plan.read_order]
        report = typedops.read_as(
            workspace, origin, asked, view,
            invented=view.made_before(reader.search_paths(asked)),
        )
        found.answers = list(report.changes)
    # A file no line names any more, some op of which the text still has.
    named = {
        op.attrs[modops.INCLUDES].lower() for op in typed if op.attrs.get(modops.INCLUDES)
    }
    joined = list(dict.fromkeys(
        before[there].file for there in plan.same.values()
        if before[there].file and before[there].file.lower() not in named
    ))
    done = _store(loadout, origin, plan, report)
    if only_spaced:
        done.rearranged = False
    done.read_in, done.made, done.joined = dict(read.read_in), list(read.made), joined
    return found, done


@dataclass
class Reread:
    """A mod read where it stands in the stack, from the text the loadout
    wrote for it: what it found (a :class:`.stale.Answer`, the reader's
    report without its working), and the number the reading gave each
    op of the text, in the order :func:`ops_of_text` has them (-1 for one it
    did not come to)."""

    text: str
    report: Any
    orders: List[int] = field(default_factory=list)


def ops_of_text(loadout, origin: str, text: str) -> List[modops.SourceOp]:
    """The ops of ``origin``'s file as :func:`shown` wrote it, for a reading
    of the stack to read in the mod's place.

    ``loadout`` is asked for the mod's folder and the game, so a copy of
    its layers does (:class:`.stale.Snapshot`).  Raises ValueError for text
    that is not a file of ModOps, which nothing the Studio wrote is.
    """
    # Not as typed text is taken in (typedops.as_typed): nobody typed it,
    # and whether text stands loose in an op is asked of the mod as the
    # loadout holds it, which a copy of its layers is not.
    if not text.strip():
        return []
    return _read(loadout, origin, text, getattr(text, "regions", ()), typed=False).ops


def take_reading(loadout: Loadout, origin: str, reread: Reread, was: Optional[Shown] = None) -> bool:
    """Put a reading of ``origin``'s text in place of what its ops came to;
    whether it was, which it is not where the mod writes something else by
    now than the text that was read.

    Every op is the op it was: its uid, its switch and its note are kept,
    and so is whether it is yours.  What moves is what each comes to, and
    whether it could be read at all.  The changes the Studio wrote itself
    are not touched.

    ``was`` is the text that was read, where it is not the one the mod's
    changes write (:func:`shown`).
    """
    was = was if was is not None else shown(loadout, origin)
    if was.text != reread.text:
        return False
    before, held = _as_it_was(loadout, origin, was)
    if len(before) != len(reread.orders):
        return False
    typed = [replace(op, order=order) for op, order in zip(before, reread.orders, strict=True)]
    said = {
        change.key: (change.active, change.note)
        for change in loadout.changes.values()
        if change.origin == origin and change.op_uid
    }
    _store(loadout, origin, _Plan(was, before, held, typed, again=True), reread.report)
    for change in loadout.changes.values():
        if change.origin == origin and change.op_uid and change.key in said:
            active, note = said[change.key]
            change.active = active
            change.note = note or change.note
    return True


def _store(loadout: Loadout, origin: str, plan: _Plan, report) -> Applied:
    """Move a mod to what a :class:`_Plan` says of it: each op the text
    still has stays the op it is, where it stands now; each op read anew
    goes in with what it came to; and what the text has not is taken out."""
    done = Applied()
    typed, before, same = plan.typed, plan.before, plan.same
    fresh = plan.fresh

    # The reading numbers what it was handed from 0: back to where each
    # stands in the typed text.
    records: Dict[int, Op] = {}
    entries: Dict[int, Unread] = {}
    answers: Dict[str, List[Change]] = {}
    if report is not None:
        at_of = {
            typed[at].order: at for at in plan.read_order if typed[at].order >= 0
        }
        records = {at_of[record.order]: record for record in report.ops if record.order in at_of}
        entries = {
            at_of[entry.order]: entry for entry in report.unread(origin) if entry.order in at_of
        }
        for change in report.changes:
            if change.op_uid:
                answers.setdefault(change.op_uid, []).append(change)
    under: Dict[str, List[Change]] = {}
    for change in loadout.changes_of(origin):
        if change.op_uid:
            under.setdefault(change.op_uid, []).append(change)

    # Which of the mod's each typed op is, and what there is to say of it.
    now: Dict[str, str] = {}      # the reading's uid -> the uid it is held under
    inherits: Dict[int, Op] = {}  # a typed op read anew -> the op it still is
    typed_over: set = set()
    parts_over: set = set()       # old ops inside a group kept whole, typed over
    parts_of: set = set()         # typed ops that are a later part of a thing
    stays: List[tuple] = []       # (where, the typed op, the old one, the thing)
    group_uid: Dict[str, str] = {}
    for at, op in enumerate(typed):
        there = same.get(at)
        item = plan.standing(there)
        record = records.get(at)
        old = before[there] if there is not None else None
        if at in plan.context:
            # Read only for what is in it: it is the group it was.
            part = plan.held[there] if there is not None else None
            if record is not None and part is not None and part.op is not None:
                now[record.uid] = part.op.uid
        if at not in fresh:
            part = plan.held[there] if there is not None else None
            if op.scope and part is not None and part.op is not None:
                group_uid[op.scope] = part.op.uid
            if item is None or old is None:
                continue
            if _flat(op.comment) != _flat(old.comment) or op.shelved != old.shelved:
                done.changed += 1
            else:
                done.kept += 1
            stays.append((at, op, old, item))
            continue
        if there is None:
            over = plan.standing(plan.over.get(at))
            if over is not None and over.op is not None and record is not None:
                # It keeps the uid of the op it was typed over, so what was
                # said of that one is said of this.
                now[record.uid] = over.op.uid
                typed_over.add(_thing(over))
                counted = "changed"
            elif over is None and plan.over.get(at) is not None:
                # Typed over an op inside a group kept whole, which is read
                # again with it.
                parts_over.add(plan.over[at])
                counted = "changed"
            else:
                counted = "added"
            if op.scope:
                done.groups += 1
            elif op.attrs.get(modops.INCLUDES):
                done.lines += 1
            elif op.attrs.get(modops.ASIDE):
                done.comments += 1
            else:
                setattr(done, counted, getattr(done, counted) + 1)
        elif old is not None and (item is not None or not plan.still_part(at)):
            # The op it was, in other groups: read again, under its uid.
            # One that stood in a group kept whole had none of its own, and
            # is an op from here on: it was taken for a part of that group
            # still, and went out of the mod with what it came to.
            if item is not None and item.op is not None and record is not None:
                now[record.uid] = item.op.uid
                inherits[at] = item.op
            if _flat(op.comment) != _flat(old.comment) or op.shelved != old.shelved:
                done.changed += 1
            else:
                done.kept += 1
        elif old is not None:
            # A later part of a thing written in parts, as a group is round
            # something that stands in the middle of it: the thing it is,
            # and no op of its own.
            part = plan.held[there]
            if record is not None and part is not None and part.op is not None:
                now[record.uid] = part.op.uid
                parts_of.add(at)
            if part is not None and part.kind == SPAN_OP and (
                _flat(op.comment) != _flat(old.comment) or op.shelved != old.shelved
            ):
                done.changed += 1
        if op.scope and record is not None:
            group_uid[op.scope] = now.get(record.uid, record.uid)

    # What players see each asset the mod adds called is on the change that
    # adds it and in no op: one whose op is read again keeps its name.
    names = {
        effective.new_asset_guid(change): change.in_game_name
        for change in loadout.changes_of(origin)
        if change.creates_asset and change.in_game_name
    }

    # What the old text had and the typed text has not is taken out for
    # good, which a loaded mod's own op is remembered as.  What is read
    # again gives way to its reading, and nothing is remembered of that.
    place = next(
        (at for at, op in enumerate(loadout.ops) if op.origin == origin), len(loadout.ops)
    )
    unread_place = next(
        (at for at, entry in enumerate(loadout.unread) if entry.origin == origin),
        len(loadout.unread),
    )
    # What each op read again came to before, by what it is written as: an
    # answer that is read as it was is that change still, under its key.
    known: Dict[tuple, List[str]] = {}
    taken_out: List[str] = []
    replaced: set = set()
    unread_gone: List[Unread] = []
    unread_replaced: List[Unread] = []
    for thing, (lead, item) in plan.leads.items():
        if plan.as_it_was(thing):
            continue
        gone = lead not in plan.now_at
        # A group kept whole is counted as the text has it: its tags, and
        # each thing in it that the typed text has not.
        whole = plan.parts[thing][1:] if item.kind == SPAN_OP else ()
        for there in whole:
            if there in plan.now_at or there in parts_over:
                continue
            if before[there].scope:
                done.groups += 1
            elif before[there].attrs.get(modops.ASIDE):
                done.comments += 1
            else:
                done.dropped += 1
        if gone and thing not in typed_over:
            if item.kind == SPAN_GROUP or whole:
                done.groups += 1
            elif item.kind == SPAN_INCLUDE:
                done.lines += 1
            elif item.kind == SPAN_ASIDE:
                done.comments += 1
            else:
                done.dropped += 1
        if item.unread is not None:
            (unread_gone if gone else unread_replaced).append(item.unread)
        elif item.op is None:
            for change in item.changes:
                loadout.remove_key(change.key)
        elif gone:
            taken_out.append(item.op.uid)
        else:
            if plan.now_at[lead] in plan.rewritten:
                # As one typed over: the group the mod's folder has is not
                # to come back beside this one.
                taken_out.append(item.op.uid)
            else:
                replaced.add(item.op.uid)
            for change in under.get(item.op.uid, ()):
                loadout.changes.pop(change.key, None)
                if change.uid:
                    known.setdefault((item.op.uid, *_shape(change)), []).append(change.uid)
    loadout.drop_ops(taken_out)
    loadout.drop_unread(unread_gone)
    if replaced:
        loadout.ops = [op for op in loadout.ops if op.uid not in replaced]
    if unread_replaced:
        lost = {id(entry) for entry in unread_replaced}
        loadout.unread = [entry for entry in loadout.unread if id(entry) not in lost]

    def within(op) -> List[str]:
        return [group_uid[scope] for scope in op.scopes if scope in group_uid]

    # What stays, where it stands now.  The Studio's own changes typed one
    # after another for one asset are one block of the file, and share its
    # place.
    last: Optional[tuple] = None
    restated: Dict[int, Unread] = {}
    for at, op, old, item in stays:
        noted = _flat(op.comment) != _flat(old.comment)
        if item.unread is not None:
            # As another entry and not this one moved: an undo step knows an
            # op carried unread by which one it is, and one moved where it
            # stands would stay moved when the step is taken back.
            entry = item.unread
            done.spaced = done.spaced or entry.gap != op.gap
            moved = replace(
                entry, order=at, within=within(op), file=op.file, gap=op.gap,
                note=_flat(op.comment) if noted else entry.note,
            )
            if moved != entry:
                restated[id(entry)] = moved
        elif item.op is not None:
            held = item.op
            held.order = at
            held.within = within(op)
            # The file its lines are in now, which a cut and a paste moves.
            held.file = op.file
            done.spaced = done.spaced or held.gap != op.gap
            held.gap = op.gap
            for change in under.get(held.uid, ()):
                change.order = at
            if op.shelved != old.shelved:
                held.active = not op.shelved
                for change in under.get(held.uid, ()):
                    change.active = held.active
            if noted:
                held.note = _flat(op.comment)
        else:
            guid = item.changes[0].guid
            order = last[2] if last is not None and last[:2] == (at - 1, guid) else at
            last = (at, guid, order)
            for change in item.changes:
                change.active = not op.shelved
                change.order = order
            if noted:
                # On the one whose note the text shows: the last that has one.
                shown_on = next(
                    (change for change in reversed(item.changes) if change.note),
                    item.changes[-1],
                )
                shown_on.note = _flat(op.comment)

    # What was read anew, with what it came to.
    made: List[Op] = []
    for at in sorted(records):
        if at not in fresh or at in parts_of:
            continue
        record = records[at]
        mine = answers.get(record.uid, [])
        was_op = inherits.get(at)
        # One the mod had is whose it was.  Anything else was typed here,
        # and is in no mod's folder to be read again.
        record.authored = (
            was_op.authored if was_op is not None and at not in plan.rewritten else True
        )
        if plan.again and was_op is None:
            # Carried unread until now, and read this time: a loaded mod's
            # own op, as its folder read again would have it.
            record.authored = not _has_a_folder(loadout, origin)
        if plan.again and was_op is not None:
            record.active = was_op.active
            record.kept_by = was_op.kept_by
        if was_op is not None:
            # Spelled as the mod has it, and not as the text lays it out: it
            # is by this that an op read from the mod's folder again is
            # known for the one held, and for one taken out.  Not a group
            # kept whole that says something else inside: its text is all
            # the mod has of what is in it.
            if at not in plan.rewritten:
                record.source = was_op.source
            if _flat(typed[at].comment) == _flat(before[same[at]].comment):
                # A note the Studio wrote out does not always read back
                # letter for letter as the one it holds.
                record.note = was_op.note
        record.uid = now.get(record.uid, record.uid)
        record.order = at
        record.within = within(typed[at])
        made.append(record)
        for answer in mine:
            answer.op_uid = record.uid
            answer.order = at
            answer.authored = record.authored
            if not record.active:
                # Off with its op, as switching the op off left them: on, they
                # were written again as ops of yours after theirs (R3-23).
                answer.active = False
            before_it = known.get((record.uid, *_shape(answer)))
            if before_it and (answer.uid or plan.again):
                answer.uid = before_it.pop(0)
            standing = loadout.changes.get(answer.key)
            if plan.again and standing is not None and standing.authored and (
                standing.op_uid != record.uid
            ):
                # An edit of yours laid over what the op comes to: yours is
                # what the mod says there, as it is when a mod's folder is
                # read again (:meth:`Loadout.take_read`).
                continue
            if standing is not None and standing.op_uid != record.uid:
                # Something else of the mod's is aimed where this lands: a
                # change of the Studio's own, or what another op comes to.
                # Read in one run with it, the reader would have told the
                # two apart; set over it, that one would be gone from the
                # mod, and from its file.
                answer.uid = uuid.uuid4().hex[:12]
            if answer.creates_asset and not answer.in_game_name:
                answer.in_game_name = names.get(effective.new_asset_guid(answer), "")
            loadout.set_change(answer)
    carried: List[Unread] = []
    for at in sorted(entries):
        if at not in fresh:
            continue
        entry = entries[at]
        entry.order = at
        entry.within = within(typed[at])
        old_item = plan.standing(same.get(at))
        if old_item is not None and old_item.unread is not None:
            entry.source = old_item.unread.source
        carried.append(entry)

    # The mod's ops where they were among every mod's, in the order typed.
    mine_now = sorted(
        [op for op in loadout.ops if op.origin == origin] + made, key=lambda op: op.order
    )
    others = [op for op in loadout.ops if op.origin != origin]
    place = min(place, len(others))
    loadout.ops = others[:place] + mine_now + others[place:]
    unread_now = sorted(
        [
            restated.get(id(entry), entry)
            for entry in loadout.unread if entry.origin == origin
        ] + carried,
        key=lambda entry: entry.order,
    )
    rest = [entry for entry in loadout.unread if entry.origin != origin]
    unread_place = min(unread_place, len(rest))
    loadout.unread = rest[:unread_place] + unread_now + rest[unread_place:]
    done.why_unread = [entry.why for entry in unread_now if not entry.markup]
    done.unread = len(done.why_unread)
    loadout.count_unread({origin})

    if not (
        done.changed or done.added or done.dropped or done.groups or done.lines
        or done.comments
    ):
        # Every op is one the mod had, as it had it: only where they stand,
        # or which groups they are in, is other than it was.
        done.rearranged = True
    return done
