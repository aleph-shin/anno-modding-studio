"""Edits to somebody else's mod, written into their file in the loader's own
terms: ``Skip`` on the author's op, and yours inserted after it.

The loadout holds somebody else's mod as its author shipped it with your
edits in it -- ops typed over, taken out, switched off, ops of yours placed
among theirs -- and writing the mod out gives the file that says so
(:func:`core.build.as_files`).  The game must not load that file in place of
theirs: it is the Studio's writing of the whole mod, and it would replace the
author's copy.  So the two are compared, ModOp by ModOp, and where they
differ the author's file is changed in two ways only
(docs/proposals/patches-proposal.md, section 5):

* ``Skip="1"`` is added to an op of theirs that the loadout does not have,
  which switches it off in the loader's own terms and leaves it where it
  was, as written;
* what the loadout has instead is inserted straight after it, in the same
  group and the same file, so it runs where theirs ran -- or, for an op of
  yours with no op of theirs before it, at the top of its group.

Everything else is their bytes, untouched: the file is never parsed and
written back, so the difference between their file and the one the game
loads is exactly as big as the edits.  Each inserted op comes after a
comment saying it is the Studio's.

Two ops are the same where they say the same thing: compared as XML with
whitespace, comments, attribute order and the ``Skip`` flag left out, so a
writing of the mod that lays an op out differently is not taken for an edit.
A group is compared by its own tag, and the ops in it one by one.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lxml import etree

# The elements a ModOps file runs, by name without case: what can be
# switched off and what an edit can be.
RUNS = frozenset({"modop", "group", "include"})
MARK = "Anno Modding Studio"

_TOKEN = re.compile(
    r"<!--.*?-->"
    r"|<!\[CDATA\[.*?\]\]>"
    r"|<\?.*?\?>"
    r"|<!DOCTYPE[^>]*>"
    r"|</\s*([\w:.\-]+)\s*>"
    r"|<([\w:.\-]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>",
    re.S,
)
_SKIP = re.compile(r"\sSkip\s*=", re.I)


@dataclass
class Node:
    """One element a ModOps file runs, where it stands in the text."""

    tag: str
    start: int
    head_end: int
    end: int = -1
    children: List[Node] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.tag.lower() == "group"


@dataclass
class Scanned:
    """A ModOps file's text and the elements it runs, at the top and inside
    each group: ``head_end`` is where the root's own tag ends."""

    text: str
    nodes: List[Node] = field(default_factory=list)
    head_end: int = 0


def scan(text: str) -> Optional[Scanned]:
    """The elements a ModOps file runs, with where each stands; None for a
    text with no root element.  Tolerant: a file the loader reads, it reads
    too, and an unclosed element ends with the file."""
    found = Scanned(text=text)
    # [name, node or None, list its op-level children go into, or None]
    stack: List[list] = []
    root_seen = False
    for match in _TOKEN.finditer(text):
        closing, opening = match.group(1), match.group(2)
        if closing is not None:
            while stack:
                name, node, _kids = stack.pop()
                if node is not None:
                    node.end = match.end()
                if name.lower() == closing.lower():
                    break
            continue
        if opening is None:
            continue
        closed = match.group(4) == "/"
        if not stack:
            if root_seen:
                continue
            root_seen = True
            found.head_end = match.end()
            if not closed:
                stack.append([opening, None, found.nodes])
            continue
        holder = stack[-1][2]
        node = None
        if holder is not None and opening.lower() == "group" and not match.group(3).strip():
            # A bare group is a bracket: the loader runs what is in it where
            # it stands, and a writing of the mod may leave it out.  What is
            # in it is compared as though it stood outside.
            if not closed:
                stack.append([opening, None, holder])
            continue
        if holder is not None and opening.lower() in RUNS:
            node = Node(tag=opening, start=match.start(), head_end=match.end(),
                        end=match.end() if closed else -1)
            holder.append(node)
        if not closed:
            stack.append([opening, node, node.children if node is not None and node.is_group else None])
    if not root_seen:
        return None
    for _name, node, _kids in stack:
        if node is not None and node.end < 0:
            node.end = len(text)
    return found


# -- what an op says ------------------------------------------------------------------
_PARSER = etree.XMLParser(remove_blank_text=True, remove_comments=True, resolve_entities=False,
                          huge_tree=True, recover=False)


def _canonical(element) -> bytes:
    for name in list(element.attrib):
        if name.lower() == "skip":
            del element.attrib[name]
    for node in element.iter():
        if isinstance(node.tag, str):
            node.text = node.text.strip() if node.text else None
        node.tail = None
    return etree.tostring(element, method="c14n")


def signature(text: str, node: Node, whole: bool = False) -> str:
    """What a node says, for telling it apart from another: the whole of an op
    or an Include, the tag of a group -- or with ``whole``, a group and
    everything in it."""
    if node.is_group and not whole:
        head = text[node.start:node.head_end]
        piece = head[:-2] + "/>" if head.endswith("/>") else head[:-1] + "/>"
    else:
        piece = text[node.start:node.end]
    try:
        made = _canonical(etree.fromstring(piece.encode("utf-8", "surrogateescape"), _PARSER))
    except (etree.XMLSyntaxError, ValueError):
        made = " ".join(piece.split()).encode("utf-8", "surrogateescape")
    return node.tag.lower() + ":" + hashlib.sha1(made, usedforsecurity=False).hexdigest()


def is_skipped(text: str, node: Node) -> bool:
    return bool(_SKIP.search(text[node.start:node.head_end]))


def element(text: str):
    """One op or group, written out, as an element; None where it is not
    well-formed XML."""
    try:
        return etree.fromstring(text.encode("utf-8", "surrogateescape"), _PARSER)
    except (etree.XMLSyntaxError, ValueError):
        return None


def same(one: str, other: str) -> bool:
    """Whether two ops written out say the same thing, as :func:`signature`
    compares them."""
    first, second = element(one), element(other)
    if first is None or second is None:
        return " ".join(one.split()) == " ".join(other.split())
    return _canonical(first) == _canonical(second)


def around(scanned: Scanned, offset: int) -> List[Node]:
    """The groups ``offset`` stands inside, outermost first."""
    found: List[Node] = []
    nodes = scanned.nodes
    while True:
        inside = next((node for node in nodes
                       if node.is_group and node.head_end <= offset < node.end), None)
        if inside is None:
            return found
        found.append(inside)
        nodes = inside.children


# -- comparing ------------------------------------------------------------------------
@dataclass
class Splice:
    """One thing done to the author's file: ``skip`` the node at ``node``, or
    insert ``text`` after the offset ``at`` (``node`` the op it follows, or
    None at the top of a group)."""

    kind: str  # "skip" or "insert"
    at: int
    node: Optional[Node] = None
    text: str = ""
    # For an insert: whether it takes the place of an op of theirs skipped
    # just before it.  For a skip: whether nothing takes its place.
    instead: bool = False
    alone: bool = False


def compare(theirs: Scanned, ours: Scanned) -> List[Splice]:
    """What turns ``theirs`` into ``ours``, op by op: see the module."""
    return [splice for hunk in hunks(theirs, ours) for splice in hunk]


def hunks(theirs: Scanned, ours: Scanned) -> List[List[Splice]]:
    """:func:`compare`, as the runs it is made of: each the ops of theirs
    skipped in one place and what goes in after them, which stand or fall
    together."""
    found: List[List[Splice]] = []
    _compare(theirs, theirs.nodes, theirs.head_end, ours, ours.nodes, found)
    return found


def _compare(theirs: Scanned, have: List[Node], top: int,
             ours: Scanned, want: List[Node], found: List[List[Splice]]) -> None:
    left = [signature(theirs.text, node) for node in have]
    right = [signature(ours.text, node) for node in want]
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for kind, first, last, other_first, other_last in matcher.get_opcodes():
        if kind == "equal":
            for one, other in zip(have[first:last], want[other_first:other_last], strict=True):
                if one.is_group:
                    _compare(theirs, one.children, one.head_end, ours, other.children, found)
            continue
        hunk = [
            Splice("skip", node.head_end, node, alone=other_first == other_last)
            for node in have[first:last] if not is_skipped(theirs.text, node)
        ]
        if other_first < other_last:
            after = have[last - 1] if last > first else (have[first - 1] if first > 0 else None)
            at = after.end if after is not None else top
            hunk.extend(
                Splice("insert", at, after, ours.text[node.start:node.end], instead=last > first)
                for node in want[other_first:other_last]
            )
        if hunk:
            found.append(hunk)


# -- an edit, carried from one writing onto the author's file --------------------------
def edit_hunks(theirs: Scanned, before: Scanned, after: Scanned) -> List[List[Splice]]:
    """What turns ``before`` into ``after``, done to ``theirs``.

    ``before`` and ``after`` are two writings of the same mod, without your
    edits and with them, and ``theirs`` the author's file they were read
    from.  Compared straight with the author's file, a writing differs where
    it only writes the mod another way -- an ``<Include>`` written out in
    full -- and an edit beside such a place would take the place with it.
    So the edits are found between the two writings, and carried onto the
    author's file through where each node of ``before`` stands in it: one
    that is the author's node exactly is skipped, or put after, as itself;
    one in a run the writing wrote its own way takes the whole run with it,
    the author's run skipped and the run as edited put after it.
    """
    found: List[List[Splice]] = []
    _translate(theirs, theirs.nodes, theirs.head_end, before, before.nodes, after, after.nodes, found)
    return found


def _translate(theirs: Scanned, have: List[Node], top: int,
               before: Scanned, was: List[Node],
               after: Scanned, now: List[Node], found: List[List[Splice]]) -> None:
    sig_have = [signature(theirs.text, node) for node in have]
    sig_was = [signature(before.text, node) for node in was]
    sig_now = [signature(after.text, node) for node in now]
    same: dict = {}       # node of before -> the author's node it is
    runs: list = []       # (theirs from, to, before from, to): written another way
    run_of: dict = {}     # node of before -> its run
    for kind, first, last, other_first, other_last in difflib.SequenceMatcher(
            None, sig_have, sig_was, autojunk=False).get_opcodes():
        if kind == "equal":
            for step in range(last - first):
                same[other_first + step] = first + step
            continue
        runs.append((first, last, other_first, other_last))
        for at in range(other_first, other_last):
            run_of[at] = len(runs) - 1
    kept: dict = {}       # node of before -> the node of after it still is
    changes: list = []    # (before from, to, after from, to)
    for kind, first, last, other_first, other_last in difflib.SequenceMatcher(
            None, sig_was, sig_now, autojunk=False).get_opcodes():
        if kind == "equal":
            for step in range(last - first):
                at, other_at = first + step, other_first + step
                one, other = was[at], now[other_at]
                if one.is_group and at in same:
                    theirs_node = have[same[at]]
                    _translate(theirs, theirs_node.children, theirs_node.head_end,
                               before, one.children, after, other.children, found)
                elif one.is_group and (signature(before.text, one, whole=True)
                                       != signature(after.text, other, whole=True)):
                    # A group the writing wrote its own way, changed inside:
                    # it goes with the run it is in, as any edit there does.
                    changes.append((at, at + 1, other_at, other_at + 1))
                    continue
                kept[at] = other_at
            continue
        changes.append((first, last, other_first, other_last))

    def touched(change) -> set:
        first, last = change[0], change[1]
        hit = {run_of[at] for at in range(first, last) if at in run_of}
        if first == last and first - 1 in run_of and first in run_of and run_of[first - 1] == run_of[first]:
            hit.add(run_of[first])
        return hit

    # Edits that share a run stand or fall with it, and so with each other.
    clusters: List[Tuple[set, list]] = []
    for change in changes:
        hit = touched(change)
        joined = [cluster for cluster in clusters if cluster[0] & hit]
        merged: Tuple[set, list] = (set(hit), [change])
        for cluster in joined:
            merged[0].update(cluster[0])
            merged[1].extend(cluster[1])
            clusters.remove(cluster)
        clusters.append(merged)

    def theirs_after(at: int) -> Optional[Node]:
        """The author's node something that follows node ``at`` of before
        goes after: that node itself, or the end of the run it is in."""
        if at < 0:
            return None
        if at in same:
            return have[same[at]]
        first, last, _b1, _b2 = runs[run_of[at]]
        if last > first:
            return have[last - 1]
        return have[first - 1] if first > 0 else None

    def inserting(anchor: Optional[Node], nodes: List[Node], instead: bool) -> List[Splice]:
        at = anchor.end if anchor is not None else top
        return [Splice("insert", at, anchor, after.text[node.start:node.end], instead=instead)
                for node in nodes]

    for hit, edits in clusters:
        edits.sort()
        if not hit:
            for first, last, other_first, other_last in edits:
                gone = [have[same[at]] for at in range(first, last) if at in same]
                hunk = [Splice("skip", node.head_end, node, alone=other_first == other_last)
                        for node in gone if not is_skipped(theirs.text, node)]
                anchor = theirs_after(last - 1)
                hunk.extend(inserting(anchor, now[other_first:other_last], instead=bool(gone)))
                if hunk:
                    found.append(hunk)
            continue
        low = min([runs[k][2] for k in hit] + [edit[0] for edit in edits])
        high = max([runs[k][3] for k in hit] + [edit[1] for edit in edits])
        gone_at = sorted(
            {at for k in hit for at in range(runs[k][0], runs[k][1])}
            | {same[at] for at in range(low, high) if at in same}
        )
        gone = [have[at] for at in gone_at]
        put: List[Node] = []
        starts = {edit[0]: edit for edit in edits}
        at = low
        while at < high or at in starts:
            if at in starts:
                _first, last, other_first, other_last = starts.pop(at)
                put.extend(now[other_first:other_last])
                if last > at:
                    at = last
                    continue
            if at < high and at in kept:
                put.append(now[kept[at]])
            at += 1
        anchor = have[gone_at[-1]] if gone_at else theirs_after(low - 1)
        hunk = [Splice("skip", node.head_end, node, alone=not put)
                for node in gone if not is_skipped(theirs.text, node)]
        hunk.extend(inserting(anchor, put, instead=bool(gone)))
        if hunk:
            found.append(hunk)


# -- writing --------------------------------------------------------------------------
def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _indent(text: str, offset: int) -> str:
    line = text.rfind("\n", 0, offset) + 1
    lead = text[line:offset]
    return lead if not lead.strip() else ""


def _comment(words: str) -> str:
    return "<!-- %s: %s -->" % (MARK, words.replace("--", "- -"))


def comment_block(words: str) -> str:
    """``words`` as an XML comment, made safe to be one."""
    return "<!-- %s -->" % words.replace("--", "- -")


def apply(text: str, splices: List[Splice]) -> str:
    """``text`` with ``splices`` made, and every other byte as it was."""
    newline = _newline(text)
    pieces: List[Tuple[int, int, str]] = []
    for order, splice in enumerate(splices):
        node = splice.node
        if splice.kind == "skip" and node is not None:
            head = text[node.start:node.head_end]
            at = node.head_end - (2 if head.endswith("/>") else 1)
            while at > node.start and text[at - 1].isspace():
                at -= 1
            pieces.append((at, order, ' Skip="1"'))
            continue
        indent = _indent(text, node.start) if node is not None else "    "
        words = ("in place of the ModOp before this, which is switched off with Skip"
                 if splice.instead else "added here")
        pieces.append((splice.at, order, newline + indent + _comment(words) + newline + indent + splice.text))
    for order, splice in enumerate(splices):
        node = splice.node
        if splice.kind == "skip" and splice.alone and node is not None:
            indent = _indent(text, node.start)
            pieces.append((node.end, len(splices) + order, newline + indent + _comment(
                "the ModOp before this is switched off with Skip")))
    pieces.sort(key=lambda piece: (piece[0], piece[1]))
    out, last = [], 0
    for at, _order, words in pieces:
        out.append(text[last:at])
        out.append(words)
        last = at
    out.append(text[last:])
    return "".join(out)


def decode(raw: bytes) -> Tuple[str, bytes]:
    """A file's text, and the byte-order mark it began with, so that writing
    it back gives every byte that was not changed exactly as it was."""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    return raw[len(bom):].decode("utf-8", "surrogateescape"), bom


def encode(text: str, bom: bytes = b"") -> bytes:
    return bom + text.encode("utf-8", "surrogateescape")


def line_of(text: str, offset: int) -> int:
    """The line ``offset`` is on, from 1."""
    return text.count("\n", 0, offset) + 1
