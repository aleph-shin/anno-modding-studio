"""ModOps as their authors wrote them: finding a mod's patch files, reading
them into ops, and what each op is.

An op is a type, a target, a payload and a condition (:mod:`.modop` says what
each attribute means).  This module finds the files a mod patches the game
with, reads every op out of them, nested as the loader nests them in groups
and includes, and says what each one is: as text for the list, and as the
paths and conditions it asks of a document.  What an op *does* to a document
is :mod:`.reader`'s, and what that comes to, written down as changes,
:mod:`.recording`'s.
"""

from __future__ import annotations

import copy
import itertools
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from . import (
    games,
    generated,
    imya,
    modfolder,
    settings,
    sidefiles,
    texts,
)
from .changes import (
    APP_NAME,
    NOTE_MARK,
    OP_ADD,
    OP_REMOVE,
    STUDIO_SAYS_MARK,
    SWITCHED_OFF_MARK,
    Change,
    Op,
    Unread,
    switched_on,
)
from .i18n import STORED, tr, tr_noop, trn
from .index import found_anything, in_one_asset
from .modop import (
    AT_ASSET,
    GROUP_KNOWN,
    LOADER_VARIABLE,
    MOD_VALUE,
    Described,
    allows_no_match,
    as_117_reads,
    asks,
    asks_a_setting,
    combines,
    guids,
    is_flags,
    is_skipped,
    name_of,
    payload_kind,
    reach,
    reads_a_setting,
    type_and_target,
)

# How far a chain of one file including another may run before it is taken
# to be a mistake. Real mods nest two deep at most.
MAX_INCLUDE_DEPTH = 8

# Outcomes for one source op.
IMPORTED = "imported"
SETTLED = "settled"      # nothing to do: your assets.xml already reads this way
BLOCKED = "blocked"      # a real ModOp feature with no narrow equivalent
# In the file, not a ModOp, and nothing the game's loader acts on: the markup a
# tweak tool reads.  Not an op the Studio failed to read, so it is not counted
# as one -- but it goes back out where it was, like everything else it carries.
CARRIED = "carried"
# An op the Studio itself wrote switched off, back as it was and still off.
# Not SETTLED: nothing was asked of the game's files, and a count of ops
# "already true" is no place for something its author turned off.
SHELVED = "shelved"

# The first step of an absolute path, where it plainly names an element.
_FIRST_STEP = re.compile(r"^/\s*([A-Za-z_][\w.-]*)\s*(?=[\[/]|$)")


class Unrepresentable(Exception):
    """This op does something the Studio's narrow ops cannot express."""

    # The node of the working tree it is about, where it is about one: the
    # list, somewhere under the op's target, whose entries could not be told
    # apart.  A typed asset is refused with its path.
    at: Any = None



# --------------------------------------------------------------- what was read
@dataclass
class SourceOp:
    """One ``<ModOp>`` exactly as the mod wrote it."""

    kind: str = ""
    guid: str = ""
    path: str = ""
    condition: str = ""
    payload: tuple = ()
    # Everything directly inside it, in file order: the payload, and with it
    # the author's comments.  The reading goes by ``payload``; the op written
    # back (:func:`render_op`) goes by this, so that a comment between two
    # elements, and text after one, are still in what the mod says.
    inside: tuple = ()
    # An op's body can be text rather than elements: ``<ModOp Type="add" …>
    # ;Mod1</ModOp>`` appends to what the target field already says.
    text: str = ""
    # The same, as written: the loader appends it whitespace and all, and a
    # texts patch adding `` (1x1)`` to a name means the space.
    raw_text: str = ""
    attrs: dict = field(default_factory=dict)
    line: int = 0
    where: str = ""
    comment: str = ""
    # The mod this op came from, when a whole folder of them is read at once.
    mod: str = ""
    # Where it sat in that mod's file. ModOps patch a document that is changing
    # underneath them, so this is not decoration: it is what says whether a
    # search saw another op's work or came before it.
    order: int = 0
    # Set on the copy made for each asset a search-targeted op turns out to hit,
    # so its path is still read the way the search read it.
    searched: bool = False
    # What a ``Content=`` op copies, once it has been looked up -- only when the
    # author asked for those to be settled. It stands in for the body the op
    # does not have, so everything downstream reads it as an ordinary payload.
    content: tuple = ()
    # And the text such an op adds, where its ``<ModOpContent/>`` stands
    # straight inside it with the op's own text round it: ``>(<ModOpContent/>)
    # </ModOp>`` adds the copied text in brackets to what a field says.  See
    # :func:`_copied_as_text`.
    copied_text: Optional[str] = None
    # Set when this is not a ``<ModOp>`` at all but something else that sat
    # among them -- a ``<Group>`` with a condition, an ``<Asset>`` written
    # straight under ``<ModOps>``, a tweak tool's markup: the element exactly as
    # written.  It is never read, only carried, and it has a place in the
    # numbering for the same reason an unread op does.
    verbatim: str = ""
    # A ``<Group Condition="…">``, or a file that is only included on a
    # condition, is a *scope*: the loader asks its question once, on the way in,
    # and everything inside runs or does not.  ``scope`` is set on the op that
    # stands for the scope itself -- whose ``verbatim`` is the whole of it and
    # whose ``condition`` is its question -- and ``scopes`` on every op inside
    # one, outermost first.
    scope: str = ""
    scopes: tuple = ()
    # How many times the loader runs a scope: ``MaxRepeat="N"`` in Anno 117,
    # again while its condition holds and at most N times. Read as a loop,
    # in :meth:`.reader.Translator._walk_scope`, and written as the one group it is.
    rounds: int = 1
    # Set as it is run: inside a scope that goes back out as written, so that
    # whatever could not be read in there is carried by the scope and not again.
    inside_kept: bool = False
    # The file of its mod it was read out of, where that is one an
    # ``<Include>`` names and the export writes again: its path from the top
    # of the mod.  "" for the mod's own patch, and for a file whose ops are
    # written into the one that names it -- see :func:`_as_a_file`.
    file: str = ""
    # How many blank lines stood before it in its file, before the comment
    # that is its note where it has one.
    gap: int = 1
    # Set as it is run, read as written: the groups it sits in that are kept
    # as groups with ops in them, by the uid of each one's op, outermost first.
    within: tuple = ()
    # Read out of a comment the Studio wrote it into, switched off: it comes
    # back as the op it was and does nothing -- see :func:`_collect_shelved`.
    shelved: bool = False
    # Which game's loader will read this op. The two do not always agree about
    # what a file means -- <ModItem> is read by one and ignored by the other --
    # and where they differ the op has to be read as the game that will run it
    # reads it, not as the file looks.
    game: str = ""

    @property
    def guids(self) -> List[str]:
        """The assets this op targets.

        A ``GUID`` attribute may name several, comma separated -- one op that
        makes the same edit to each of them.  Mods use it constantly for things
        like "every warehouse", and reading it as a single odd-looking GUID made
        those ops look like they aimed at an asset that does not exist.

        Split on the comma and nothing else, which is all the loader does:
        ``GUID="500005, 500145"`` has it look for an asset whose GUID is
        `` 500145``, space and all, and find none.  Measured for both games.
        *Unlock Coal Mine at 1 Worker* is written exactly that way and reaches
        one of its two assets in game; trimming read it as reaching both, and
        wrote an export that did.
        """
        return guids(self.guid)

    def label(self) -> str:
        if self.verbatim:
            return self.path
        kind = self.kind or "?"
        # An op with no path names the asset itself, and says so in words --
        # a variant of its own for each spelling, so the words can be
        # translated where the label is shown.
        if not self.path and self.guid:
            return tr_noop("%(kind)s @%(guid)s(the asset itself)", context=STORED) % {
                "kind": kind, "guid": self.guid,
            }
        if not self.path:
            return tr_noop("%s (the asset itself)", context=STORED) % kind
        target = self.path
        if self.guid and not target.startswith(("/", "(")):
            target = "/" + target
        return "%s %s%s" % (kind, ("@" + self.guid) if self.guid else "", target)


@dataclass
class Extra:
    """Something in the mod that is not an asset ModOp."""

    what: str
    where: str
    detail: str = ""


@dataclass
class OpResult:
    op: SourceOp
    status: str
    reason: str = ""
    changes: List[Change] = field(default_factory=list)
    # How many of the assets a multi-asset op was about could not be read. It
    # still counts as imported -- most of it came through -- but a search that
    # is only three-quarters understood cannot be written back out as itself.
    partial: int = 0
    # The Op this op was written down as, so that an op the equivalence
    # check throws out afterwards can take its Op with it.
    record: Optional[Op] = None
    # The changes whose payloads this op's edits went into rather than becoming
    # changes of their own -- it edited something this mod had already added,
    # and the working tree *is* that change's payload. The edit is not lost,
    # but it only exists inside those other changes.
    folded: tuple = ()

    # What an op written switched off would come to were it on: its changes,
    # each of them off.  Apart from ``changes`` until the reading is checked,
    # because that check replays what the mod does, and this it does not do.
    shelved: List[Change] = field(default_factory=list)
    # Why the field-by-field reading could not express the op, when it could
    # not: the changes then swap a whole node, which claims everything inside
    # it, so the op is kept as itself and the swap is only what is shown.
    coarse: str = ""
    # The ModOp path of the node that reason is about, where the reader knows
    # of one: the list whose entries it could not tell apart, which may be
    # well under the node the op is aimed at.
    coarse_at: str = ""
    # It matched nothing in some asset, and its author allows that
    # (AllowNoMatch). Whether that keeps it as written is a question about the
    # op, asked once in :meth:`.reader.Translator._run`, not of each asset it names.
    allowed_miss: bool = False


@dataclass
class ImportReport:
    """Everything the import found, ready to show before anything is applied."""

    results: List[OpResult] = field(default_factory=list)
    extras: List[Extra] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    patch_files: List[Path] = field(default_factory=list)
    mod_name: str = ""
    root: Optional[Path] = None
    error: str = ""
    # Every op of this mod, kept as written: the op verbatim and the assets
    # it answered to when the mod was read.
    ops: List[Op] = field(default_factory=list)
    # What the mod offers through iModYourAnno, and what its folder is set to.
    tweaks: Optional[imya.Tweaks] = None
    # The answers to the mod's own options it was read at, where anything
    # answered them: the user, the game's own options file, or the settings
    # iModYourAnno left in the folder.  Empty is the author's defaults.
    options_read: Dict[str, str] = field(default_factory=dict)

    @property
    def changes(self) -> List[Change]:
        out: List[Change] = []
        for result in self.results:
            out.extend(result.changes)
        return out

    @property
    def game_hint(self) -> Optional[str]:
        """Which game this mod's own files say it is for, if that is knowable.

        Independent of anything the mod claims about itself -- see
        :func:`game_for_patch`.  ``None`` when there is nothing to go on (a
        loose file with no game-shaped folders above it) or when the patch
        files disagree, which real mods do not do.
        """
        found = set()
        for path in self.patch_files:
            try:
                relative = path.relative_to(self.root) if self.root else path
            except ValueError:
                relative = path
            game = game_for_patch(relative)
            if game:
                found.add(game)
        return found.pop() if len(found) == 1 else None

    def unread(self, origin: str = "") -> List[Unread]:
        """Every op that could not be read, as written and with its reason.

        A count of these already travels with the mod; this is what makes the
        count answerable.  What a reader wants to know is which op, out of
        which mod, and what stopped it -- and none of that survives in a
        change list, because the whole point is that these produced none.
        """
        return [
            Unread(
                origin=origin,
                source=render_op(result.op),
                label=result.op.label(),
                why=result.reason,
                where=tr_noop("%(file)s line %(line)d", context=STORED)
                % {"file": result.op.where, "line": result.op.line}
                if result.op.where
                else "",
                note=result.op.comment,
                order=result.op.order,
                file=result.op.file,
                gap=result.op.gap,
                within=list(result.op.within),
            )
            for result in self.results
            if result.status in (BLOCKED, CARRIED) and not result.op.inside_kept
        ]

    def count(self, status: str) -> int:
        return sum(1 for result in self.results if result.status == status)

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.results:
            return tr("No asset ModOps found.")
        # A tally rather than a sentence: each part stands on its own.
        bits = [trn(
            "%(n)s change from %(ops)s", "%(n)s changes from %(ops)s",
            len(self.changes),
            ops=trn("%(n)s ModOp", "%(n)s ModOps", self.count(IMPORTED)),
        )]
        if self.count(SETTLED):
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s already true of your assets.xml",
                            "%(n)s already true of your assets.xml",
                            self.count(SETTLED)))
        if self.count(SHELVED):
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s switched off", "%(n)s switched off",
                            self.count(SHELVED)))
        if self.count(BLOCKED):
            # Translators: a count of ModOps.
            bits.append(trn("%(n)s need a look", "%(n)s need a look",
                            self.count(BLOCKED)))
        return "; ".join(bits)


# ------------------------------------------------------------------ the reading
# What an export made before ``STUDIO_SAYS_MARK`` wrote above an op with
# nothing to mark it, where the words are the Studio's and no author's: the
# heading of an asset's block, why an op was kept as itself, a warning.  Put
# to a comment as it stands in the file, spacing and all.  What such an export
# said of a single change has no words of its own to go by, and is known
# another way: see ``reader.Translator._not_its_own_words``.
_SAID_BEFORE_THE_MARK = re.compile(
    r"^ .*  \|  GUID \d+ $"
    r"|^ .*  \|  kept as written: "
    r"|^ .*  \|  every asset matching "
    r"|^ kept exactly as written: %s could not read it \("
    r"|^ WARNING: " % re.escape(APP_NAME),
    re.S,
)


def _comment_before(node) -> str:
    """The comment just above a ModOp, which is usually what it is for.

    Not an op the Studio wrote switched off, which is a comment only to keep
    the game from reading it.  It stands between as the op it is: what is
    above it was said of it, and not of the op below.

    And not what the Studio said of the op itself, which is written again
    from the op: taken for a note it would go out twice, and then be read as
    a note about a note.  Those are passed over for the note beside them,
    which comes back without the mark it was written under.
    """
    said = []
    for comment in _notes_above(node):
        text = " ".join((comment.text or "").split())
        said.append(text[len(NOTE_MARK):].strip() if text.startswith(NOTE_MARK) else text)
    return "\n".join(said)


def _is_note(comment) -> bool:
    """Whether a comment is a note the Studio wrote, under its mark."""
    return " ".join((comment.text or "").split()).startswith(NOTE_MARK)


def _notes_above(node) -> list:
    """The comments that are the note of ``node``, the top one first: the
    one :func:`_note_above` finds, and where that is a note the Studio
    wrote, each of its notes stacked directly above it, with no blank line
    or anything else between.  A note of several lines is written a
    comment to a line (``generator._notes``): a ModOp the Studio writes
    for several changes, under the note of each.  A comment somebody wrote
    is the one directly above, as it always was."""
    comment = _note_above(node)
    if comment is None:
        return []
    found = [comment]
    while _is_note(found[0]):
        previous = found[0].getprevious()
        if (
            not isinstance(previous, etree._Comment)
            or (previous.tail or "").count("\n") > 1
            or not _is_note(previous)
            or not _says_something(previous)
        ):
            break
        found.insert(0, previous)
    return found


def _says_something(comment) -> bool:
    """Whether a comment is somebody's words, and not what the Studio used
    to write: its heading over an asset's ModOps, or a line over an op."""
    written = comment.text or ""
    text = " ".join(written.split())
    return bool(
        text
        and text != STUDIO_SAYS_MARK
        and not text.startswith(STUDIO_SAYS_MARK + " ")
        and not _SAID_BEFORE_THE_MARK.match(written)
    )


def _is_shelved(comment) -> bool:
    return (comment.text or "").strip().startswith(SWITCHED_OFF_MARK)


def _note_above(node):
    """The comment that is the note of ``node``: the one directly above it
    that says something, with no element, no switched-off op and no blank
    line between.

    With a blank line between, it is a comment of the file's and not of
    the op: a heading over a run of ops, a line somebody left for
    themselves.  That one is kept where it stands (:func:`_stands_alone`).
    """
    previous = node.getprevious()
    while previous is not None:
        if (previous.tail or "").count("\n") > 1:
            return None
        if isinstance(previous, etree._Comment):
            if _is_shelved(previous):
                return None
            if _says_something(previous):
                return previous
        elif isinstance(previous.tag, str):
            return None
        previous = previous.getprevious()
    return None


def _stands_alone(comment) -> bool:
    """Whether a comment that says something is nobody's note: it is not
    the one directly above the next thing in the file.  It is kept where it
    stands (:data:`ASIDE`)."""
    if _is_shelved(comment) or not _says_something(comment):
        return False
    following = comment.getnext()
    while following is not None:
        if isinstance(following.tag, str) or (
            isinstance(following, etree._Comment) and _is_shelved(following)
        ):
            return not any(note is comment for note in _notes_above(following))
        following = following.getnext()
    return True


def _gap_before(node) -> int:
    """How many blank lines stand before ``node``, or before the comment
    that is its note."""
    notes = _notes_above(node) if not isinstance(node, etree._Comment) else []
    first = notes[0] if notes else node
    previous = first.getprevious()
    parent = first.getparent()
    before = previous.tail if previous is not None else (parent.text if parent is not None else "")
    return min(MOST_BLANK_LINES, max(0, (before or "").count("\n") - 1))


def _hand_on_the_note(node, ops, start: int, where: str, reading) -> None:
    """The comment directly above something that is read straight through
    -- a bare ``<Group>``, a file whose ops are written into the file that
    names it -- is the note of the first thing in it, where that has none.
    Where it has, or is no op, the comment is kept where it stands.

    It was dropped: nothing stands for a bare group once it is read, so
    there was nothing for it to be the note of.  The export has the first
    thing where the group was, with the comment directly above it, so this
    is also what reading the export back makes of it.
    """
    comments = _notes_above(node)
    if not comments:
        return
    first = ops[start] if len(ops) > start else None
    if first is not None and not first.comment and not first.attrs.get(ASIDE):
        first.comment = _comment_before(node)
        return
    if not reading.asides:
        return
    for at, comment in enumerate(comments):
        aside = _set_aside(comment, where, "<!-- -->")
        aside.comment = ""
        aside.attrs = {ASIDE: "1"}
        ops.insert(start + at, aside)


# No more blank lines than this are kept before anything.
MOST_BLANK_LINES = 8
# On the op that stands for a comment kept where it stands, in ``attrs``.
ASIDE = "aside"


def children(node) -> tuple:
    return tuple(child for child in node if isinstance(child.tag, str))


def parse_ops(
    root, extras: List[Extra], where: str, source=None, base=None, taken=None, game=None,
    overrides=None, inline: Optional[list] = None, asides: bool = False,
) -> List[SourceOp]:
    """Collect the ``<ModOp>``s under ``root``, flagging everything else.

    ``source`` is the file ``root`` was read from, which is what makes
    ``<Include>`` answerable: a mod may keep its ops in files of its own and
    have assets.xml do nothing but name them, and without ``source`` there is
    nothing to resolve those names against.  ``base`` is the mod folder, used
    only to describe where an op came from; ``taken`` collects the files that
    were pulled in, for the caller to report.

    ``inline``, a list: the text is one file of a mod shown with its other
    files' ops beside it (:mod:`.modfile`), so an ``<Include>`` line that
    names a file kept as a file is kept as the line and its file is not
    read from the mod's folder.  The list is given one entry for each
    ``<Include>`` met: see :func:`_line_alone`.

    ``asides``: a comment that is no op's note is kept, as something that
    takes a place among the ops (:data:`ASIDE`).  For a mod's own files,
    read from its folder or typed as a file; not for ModOps typed for one
    asset, where a comment has no file to stand in.
    """
    ops: List[SourceOp] = []
    _collect(
        root,
        ops,
        extras,
        where,
        _Reading(source=source, base=base, taken=taken if taken is not None else [],
                 seen=set(), game=game, overrides=overrides, inline=inline,
                 asides=asides),
        0,
    )
    return ops


@dataclass
class _Reading:
    """What a run of :func:`parse_ops` needs to follow an ``<Include>``."""

    source: Optional[Path] = None
    base: Optional[Path] = None
    taken: List = field(default_factory=list)
    seen: set = field(default_factory=set)
    # How many groups deep the reading stands, in this file and the ones
    # that named it: what a group holds is written into the group.
    grouped: int = 0
    # See :func:`parse_ops`.  Not handed on to the reading of a file that
    # is followed, which is read as a mod's folder is.
    inline: Optional[list] = None
    asides: bool = False
    # Which game's loader the file is for, when its place in the mod says so:
    # the two read some spellings differently. None reads them as 1800 does.
    game: Optional[str] = None
    # Files to read in place of what is on disk, by resolved path: a mod read
    # at iModYourAnno settings other than the ones its folder holds.
    overrides: Optional[Dict[Path, bytes]] = None

    def describe(self, path: Path) -> str:
        if not self.base:
            return path.name
        # Included files arrive resolved, and a base that is not can be the same
        # folder spelled differently -- a Windows 8.3 short name, for one.
        for base in (Path(self.base), Path(self.base).resolve()):
            try:
                return path.relative_to(base).as_posix()
            except ValueError:
                continue
        return path.name


def read_op(text: str, where: str = "a kept op", game: str = "") -> Optional[SourceOp]:
    """One ``<ModOp>`` back from the XML a kept op stores it as, read as
    ``game`` reads it -- or as either game would, where it is not given.

    The same reader the import uses, so an op kept as its own source text and an
    op read out of a mod are the same thing by the time anything runs them.
    """
    try:
        node = etree.fromstring((text or "").strip().encode("utf-8"))
    except etree.XMLSyntaxError:
        return None
    if node.tag != "ModOp":
        return None
    # The payload elements are proxies into this tree, and lxml keeps a
    # document alive for as long as any of its proxies is held -- so the op
    # carries its own source with it and nothing here has to.
    return _read_op(node, where, game or None)


def render_op(op: SourceOp) -> str:
    """A ModOp back to the XML it was written as.

    Its own attributes, verbatim: an op that is re-emitted as anything but
    what the mod said would be a rewrite pretending to be a copy.
    """
    if op.verbatim:
        return op.verbatim
    parts = ["<ModOp"]
    for name, value in op.attrs.items():
        parts.append(" %s=%s" % (name, quoteattr(value)))
    parts.append(">")
    # Its own text as written, where it has any: the loader appends it
    # whitespace and all, so ``;Africa`` on a line of its own is not
    # ``;Africa``.  (Three census mods of one author write region lists so.)
    body = escape(op.raw_text or op.text) if op.text else ""
    # And what follows a child, where it is more than layout: after a
    # ``<ModOpContent/>`` it is part of the value the op comes to, and
    # anywhere else it is what the author wrote and the game's to make
    # something of.  A comment is carried the same way.  Indentation is not:
    # an op says the same however it was laid out.
    for child in op.inside or op.payload:
        body += etree.tostring(child, encoding="unicode", with_tail=False)
        if child.tail and child.tail.strip():
            body += escape(child.tail)
    return "".join(parts) + body + "</ModOp>"


def _computes(child) -> str:
    """The expression a payload node works its value out from, if it does.

    ``<Limit><ModValue Insert="$traderstorage"/></Limit>`` is one element deep
    and holds no text, so everything that reads a payload for its value finds
    nothing there.  What it has instead is this.
    """
    if len(child) != 1:
        return ""
    inner = child[0]
    if inner.tag != MOD_VALUE:
        return ""
    if is_flags(inner):
        said = "Remove" if inner.get("Merge") is None else "Merge"
        return "%s flags %s" % (said.lower(), inner.get(said))[:40]
    return " ".join((inner.get("Insert") or "").split())[:40]


def describe_op(op: SourceOp) -> str:
    """One line saying what an op does, for its row in the list."""
    if op.scope:
        # What a group carries, said as that: a condition, a tweak tool's name
        # for it, or a switch that has it off. "Only if" with nothing after it
        # was what a group carrying only a name used to be called.
        condition = " ".join((op.condition or "").split())[:60]
        label = op.attrs.get("label", "")
        named = label.split("=", 1)[-1] if label else ""
        if op.attrs.get("skip"):
            return (
                tr_noop("the group %s, switched off", context=STORED) % named if named
                else tr_noop("a group of ops, switched off", context=STORED)
            )
        repeats = op.attrs.get("repeat")
        if repeats:
            if condition:
                # Translators: a MaxRepeat group, run again while its condition
                # holds; %(times)s is the most rounds its author allows.
                said = tr_noop("a group of ops, again while %(condition)s, at most "
                               "%(times)s times", context=STORED) % {
                    "condition": condition, "times": repeats,
                }
            else:
                said = tr_noop("a group of ops, %s times over", context=STORED) % repeats
            if named:
                return tr_noop("the group %(name)s: %(what)s", context=STORED) % {
                    "name": named, "what": said,
                }
            return said
        if named and condition:
            return tr_noop("the group %(name)s, only if %(condition)s", context=STORED) % {
                "name": named, "condition": condition,
            }
        if named:
            return tr_noop("the group %s", context=STORED) % named
        return tr_noop("a group of ops, only if %s", context=STORED) % condition
    if op.verbatim and op.attrs.get("adds"):
        return tr_noop("add an asset", context=STORED)
    kind = op.kind or "?"
    if op.kind == OP_REMOVE:
        return tr_noop("remove it", context=STORED)
    copied = " ".join((op.attrs.get("Content") or "").split())
    if copied and not op.payload and not (op.text or "").strip():
        # The op has no body of its own: what it writes is whatever the other
        # asset says, and naming that is the whole of what the op does.
        return tr_noop("%(kind)s, copied from %(source)s", context=STORED) % {
            "kind": kind, "source": copied[:60],
        }
    named = [child.tag for child in op.payload]
    if op.text and not named:
        return "%s %s" % (kind, " ".join(op.text.split())[:40])
    if len(named) == 1:
        child = op.payload[0]
        value = (child.text or "").strip()
        if value and not len(child):
            return "%s %s = %s" % (kind, child.tag, value)
        # A value the mod works out while patching is still a value, and saying
        # only "merge Limit" of one hides the entire point of the op -- which on
        # a configurable mod is every op it has.
        computed = _computes(child)
        if computed:
            return "%s %s = %s" % (kind, child.tag, computed)
        return "%s %s" % (kind, child.tag)
    if named:
        return tr_noop("%(kind)s %(count)d fields", context=STORED) % {
            "kind": kind, "count": len(named),
        }
    return kind


# -- what an op is ------------------------------------------------------------------
# Each of an op's target, payload and condition is either written down in it
# or worked out from the document as the game loads it.  Which of them are
# worked out is what there is to say of an op beyond what it does: see
# section 4 of docs/proposals/ops-as-written-proposal.md.
def describe(op: SourceOp) -> Described:
    """What there is to say of ``op`` beyond what it does."""
    if op.scope:
        label = op.attrs.get("label", "")
        return Described(
            condition=asks(op.condition, op.game),
            skip=bool(op.attrs.get("skip")),
            name=label.split("=", 1)[-1] if label else "",
            kind="group",
            options=bool(label) or asks_a_setting(op.condition),
        )
    if op.verbatim:
        return Described(kind=OP_ADD if op.attrs.get("adds") else "")
    elements = op.content or op.payload
    name = name_of(op.attrs)
    return Described(
        target=reach(op.guid, op.path),
        payload=payload_kind(op.attrs, elements),
        condition=asks(op.condition, op.game),
        skip=is_skipped(op.attrs),
        allow_no_match=allows_no_match(op.attrs),
        name=name,
        kind=op.kind,
        options=bool(name) or asks_a_setting(op.condition) or reads_a_setting(elements),
    )


@lru_cache(maxsize=32768)
def described(source: str, game: str = "") -> Described:
    """:func:`describe` for an op held as its text, as a kept op holds it.

    Remembered by the text: a list of several thousand ops asks this of each
    every time it is drawn, and the answer is the text's alone.
    """
    try:
        node = etree.fromstring((source or "").strip().encode("utf-8"))
    except etree.XMLSyntaxError:
        return Described()
    if not isinstance(node.tag, str):
        return Described()
    if node.tag == "ModOp":
        return describe(_read_op(node, "a kept op", game))
    if adds_an_asset(node, game):
        return Described(kind=OP_ADD)
    if node.tag.lower() == "group":
        carries = _group_scope(node) or {}
        scope = SourceOp(
            verbatim=source, scope="-", condition=carries.get("condition", ""), game=game,
            attrs={name: value for name, value in carries.items() if name != "condition"},
        )
        return describe(scope)
    return Described()


def _collect(parent, ops, extras, where, reading=None, depth=0) -> None:
    _collect_into(parent, ops, extras, where, reading or _Reading(), depth)


# The most rounds of a MaxRepeat group: the loader holds what it says to 1 to
# 1,000 (117 L570-571). The census's largest is 999, in *Creative Mode*.
MAX_ROUNDS = 1000
# The number at the start of a MaxRepeat, as ``std::from_chars`` reads one: no
# space or plus sign before it, and anything after it passed over.
_LEADING_NUMBER = re.compile(r"-?\d+")


def _group_scope(node) -> Optional[Dict[str, str]]:
    """Whether a ``<Group>`` is one the Studio reads, and what it is.

    A bare group is a bracket and reads straight through; this is about one
    that carries something.  Three of the four things a group carries in the
    mods on mod.io are read:

    ``Condition``
        the scope it always was -- see :meth:`.reader.Translator._enter`.
    ``ModOpID``, ``Name``
        a label, for a tweak tool to find the group by.  Nothing at all to the
        loader, measured: the ops inside run exactly as they would bare.  But
        it has to go back out carrying its label, or the tool that keys on it
        has nothing to find, so the group is kept as written and what is inside
        is read and hangs off it.  473 such groups in the Anno 1800 census, in
        41 mods, holding 1,473 ModOps that used to be unread.
    ``Skip``
        switched off.  *Any* ``Skip`` switches a group off, whatever its value:
        ``Skip="0"`` and ``Skip=""`` skip it too, which is the loader's own
        rule and not a guess.
    ``MaxRepeat``
        Anno 117 runs the group again while its condition still holds, at most
        that many times -- see :func:`_rounds`.

    Anything else -- an attribute nobody here has seen -- is carried whole as
    before.
    """
    names = [name.lower() for name in node.attrib]
    if not names or any(name not in GROUP_KNOWN for name in names):
        return None
    found = {}
    for name, value in node.attrib.items():
        low = name.lower()
        if low == "condition":
            found["condition"] = (value or "").strip()
        elif low == "skip":
            found["skip"] = "1"
        elif low == "maxrepeat":
            # As written: the loader reads the number from its very start.
            found["repeat"] = value or ""
        else:
            found["label"] = "%s=%s" % (name, (value or "").strip())
    return found


def _collect_shelved(comment, ops, where, reading) -> None:
    """The ops inside a comment the Studio wrote them into, switched off.

    Each takes its place among the mod's ops, marked: it is not run, and it
    comes back as what it was -- see :meth:`.reader.Translator._shelved`.  A comment
    that will not read as XML is a comment, whatever it starts with.
    """
    body = (comment.text or "").strip()[len(SWITCHED_OFF_MARK):].lstrip(":")
    # The op's own comments first: they are written there as no XML is.
    body = switched_on(body)
    try:
        held = etree.fromstring(("<ModOps>%s</ModOps>" % body).encode("utf-8"))
    except etree.XMLSyntaxError:
        return
    said = _comment_before(comment)
    for node in held:
        if not isinstance(node.tag, str):
            continue
        if node.tag == "ModOp":
            op = _read_op(node, where, reading.game)
        else:
            op = _set_aside(node, where, "<%s>" % node.tag)
        op.shelved = True
        op.comment = said
        op.line = comment.sourceline or 0
        ops.append(op)


def _collect_into(parent, ops, extras, where, reading, depth) -> None:
    # Where the ops each node came to start, for the blank lines before it.
    came_to: list = []
    first = len(ops)
    try:
        _collect_each(parent, ops, extras, where, reading, depth, came_to)
    finally:
        came_to.append((len(ops), None))
        for (start, node), (end, _next) in itertools.pairwise(came_to):
            if end > start:
                # The first thing it came to is what stands where it stood.
                ops[start].gap = _gap_before(node)
        for above, op in itertools.pairwise(ops[max(0, first - 1):]):
            if (
                above.attrs.get(ASIDE) and not op.attrs.get(ASIDE) and not op.comment
                and not op.gap and above.scopes == op.scopes and above.file == op.file
            ):
                # A comment that is nobody's note, with something straight
                # under it that has none: the last line of one file and the
                # first op of the next, where both are written into the file
                # that names them.  Written so, the comment would be read
                # back as that op's note, so a blank line is kept between.
                op.gap = 1


def _switched_off_tag(comment) -> Tuple[str, Optional[Any]]:
    """What a comment the Studio wrote switched off holds, where it is one
    tag of a group alone: ``("open", the group with nothing in it)`` or
    ``("close", None)``; ``("", None)`` for anything else."""
    if not isinstance(comment, etree._Comment) or not _is_shelved(comment):
        return "", None
    body = switched_on(
        (comment.text or "").strip()[len(SWITCHED_OFF_MARK):].lstrip(":")
    ).strip()
    if re.fullmatch(r"</\s*[Gg][Rr][Oo][Uu][Pp]\s*>", body):
        return "close", None
    named = re.match(r"<\s*([Gg][Rr][Oo][Uu][Pp])[\s>]", body)
    if named is None or body.endswith("/>"):
        return "", None
    try:
        held = etree.fromstring(
            ("<ModOps>%s</%s></ModOps>" % (body, named.group(1))).encode("utf-8")
        )
    except etree.XMLSyntaxError:
        return "", None
    found = [node for node in held if isinstance(node.tag, str)]
    if len(found) != 1 or len(found[0]) or (found[0].text or "").strip():
        return "", None
    return "open", found[0]


def _switched_off_groups(parent, reading) -> list:
    """The groups among ``parent``'s children that the Studio wrote switched
    off, put back together as groups: each tag of one is written in a
    switched-off comment of its own, with what is inside it between them
    (see :func:`.generator.written`).  Each is put in the place of its
    opening comment with what was between the two inside it, and the
    groups made are handed back.  Only the outermost here: one inside is
    found when what is inside is read.  A tag without its other half, or a
    group that is not read as a group, stays the comment it is."""
    children = list(parent)
    pairs = []
    stack: List[Tuple[int, Any]] = []
    for at, node in enumerate(children):
        kind, group = _switched_off_tag(node)
        if kind == "open":
            stack.append((at, group))
        elif kind == "close" and stack:
            opened_at, group = stack.pop()
            if not stack:
                pairs.append((opened_at, at, group))
    made = []
    for opened_at, closed_at, group in pairs:
        carries = _group_scope(group) if group.attrib else None
        if carries is None or _rounds(carries.pop("repeat", None), reading.game) is None:
            continue
        opener, closer = children[opened_at], children[closed_at]
        group.text = opener.tail
        group.sourceline = opener.sourceline
        parent.replace(opener, group)
        for node in children[opened_at + 1:closed_at]:
            group.append(node)
        group.tail = closer.tail
        parent.remove(closer)
        made.append(group)
    return made


def _collect_each(parent, ops, extras, where, reading, depth, came_to) -> None:
    off = _switched_off_groups(parent, reading)
    for node in parent:
        came_to.append((len(ops), node))
        if not isinstance(node.tag, str):
            if isinstance(node, etree._Comment) and (node.text or "").strip().startswith(
                SWITCHED_OFF_MARK
            ):
                _collect_shelved(node, ops, where, reading)
            elif reading.asides and isinstance(node, etree._Comment) and _stands_alone(node):
                aside = _set_aside(node, where, "<!-- -->")
                aside.comment = ""
                aside.attrs = {ASIDE: "1"}
                ops.append(aside)
            continue
        if node.tag == "ModOp":
            ops.append(_read_op(node, where, reading.game))
        elif is_include(node):
            _include(node, ops, extras, where, reading, depth)
        # Both games' loaders take <group> for <Group>, condition and all.
        elif node.tag.lower() == "group":
            # A bare <Group> is only a bracket around ops, so its contents read
            # straight through.  One carrying attributes shares a target or a
            # condition with everything inside it, and flattening that would
            # change what those ops mean.
            read = group_reading(node, reading.game) if node.attrib else None
            if read is not None:
                carries, rounds = read
                scope_at = len(ops)
                reading.grouped += 1
                try:
                    _add_scope(node, carries, rounds, ops, extras, where, reading, depth)
                finally:
                    reading.grouped -= 1
                if any(node is group for group in off):
                    ops[scope_at].shelved = True
                continue
            if node.attrib:
                shown = "<Group %s>" % " ".join(sorted(node.attrib))
                extras.append(
                    Extra(
                        shown,
                        _line(where, node.sourceline or 0),
                        trn("a group the Studio does not read, so the %(n)s "
                            "op inside it was not read either",
                            "a group the Studio does not read, so the %(n)s "
                            "ops inside it were not read either",
                            len(children(node))),
                    )
                )
                ops.append(_set_aside(
                    _standing_alone(node, reading, depth), where, shown, written=node))
                continue
            start = len(ops)
            reading.grouped += 1
            try:
                _collect_into(node, ops, extras, where, reading, depth)
            finally:
                reading.grouped -= 1
            _hand_on_the_note(node, ops, start, where, reading)
        elif adds_an_asset(node, reading.game):
            added = _set_aside(node, where, "<Asset>")
            added.attrs = {"adds": "an asset"}
            ops.append(added)
        else:
            extras.append(
                Extra(
                    "<%s>" % node.tag,
                    _line(where, node.sourceline or 0),
                    tr("not a ModOp, so it was left alone"),
                )
            )
            ops.append(_set_aside(node, where, "<%s>" % node.tag))


def group_reading(node, game: str) -> Optional[Tuple[Dict[str, str], int]]:
    """How the Studio reads a ``<Group>`` that carries something: what it
    carries, as :func:`_group_scope` says, and the rounds it runs
    (:func:`_rounds`) -- or None for one it carries whole, unread."""
    carries = _group_scope(node)
    if carries is None:
        return None
    rounds = _rounds(carries.pop("repeat", None), game)
    return None if rounds is None else (carries, rounds)


def adds_an_asset(node, game: str) -> bool:
    """Whether ``node`` is an ``<Asset>`` the loader adds as it stands.

    How an Anno 117 mod adds an asset: written straight under ``<ModOps>``,
    no op round it. Measured -- xmltest3 adds it, and a hundred of the mods on
    mod.io rely on that -- and only for 117: what Anno 1800's loader makes of
    one is not known, so there it stays something that is carried and not
    read."""
    return node.tag == "Asset" and game == games.ANNO_117


def _line(where: str, line: int) -> str:
    """Where in a mod's files something is, for the reader."""
    # Translators: a place in a mod's files: "assets.xml line 12".
    return tr("%(file)s line %(line)s", file=where, line=line)


def _rounds(value: Optional[str], game: str) -> Optional[int]:
    """How many rounds of a group to run: 1 for an ordinary one, N for
    ``MaxRepeat="N"`` in Anno 117, and ``None`` for one to carry whole.

    Measured with the loader's own xmltest3 rather than taken from the docs,
    since the two had been read differently: the group runs again while its
    condition holds, and stops at N without a word -- a removal of the first of
    five items under ``MaxRepeat="10"`` ran five times, an addition while
    fewer than three ran three, and a cap of two stopped at two.  Anno 1800's
    loader runs it once, whatever it says, which xmltest measured too.  With
    no game known, which of the two it is is not known either, so it is
    carried whole.

    The number is read as Anno 117's loader reads it (117 L570-571, and
    ``fromchars`` in its ``str_helper.cc``): the digits it starts with, held
    to 1 to 1,000, and 1 where it does not start with any or they are more
    than a number holds.
    """
    if value is None or game == games.ANNO_1800:
        return 1
    if game != games.ANNO_117:
        return None
    found = _LEADING_NUMBER.match(value)
    number = int(found.group()) if found is not None else 1
    if not -2 ** 31 <= number < 2 ** 31:
        return 1
    return min(max(number, 1), MAX_ROUNDS)


def _add_scope(node, carries, rounds, ops, extras, where, reading, depth) -> None:
    """A group the Studio reads, as a scope: the op that stands for it, and
    then its ops, each knowing it is inside.

    The op that stands for it holds the whole of it, for when it has to go
    back out as written. A ``MaxRepeat`` group is one scope like any other,
    its ops read once here and run as many rounds as the loader would by
    :meth:`.reader.Translator._walk_scope`.
    """
    shown = "<Group %s>" % " ".join(sorted(node.attrib))
    written = _standing_alone(node, reading, depth)
    repeat = next((name for name in node.attrib if name.lower() == "maxrepeat"), None)
    looped = repeat is not None and reading.game == games.ANNO_117
    scope = _set_aside(written, where, shown, written=node)
    scope.scope = uuid.uuid4().hex[:12]
    scope.condition = carries.get("condition", "")
    scope.game = reading.game or ""
    scope.attrs = {name: value for name, value in carries.items() if name != "condition"}
    if looped:
        scope.attrs["repeat"] = node.get(repeat) or ""
        scope.rounds = rounds
    ops.append(scope)
    inside = len(ops)
    _collect_into(node, ops, extras, where, reading, depth)
    for op in ops[inside:]:
        op.scopes = (scope.scope,) + tuple(op.scopes)


def _set_aside(node, where: str, shown: str, why: str = "", written=None) -> SourceOp:
    """Something among the ModOps that is not one, kept so it can go back.

    Being listed under *Not ModOps* used to be all that happened to these, and
    an export was written without them.  For a tweak tool's markup that broke
    the tool; for a ``<Group>`` with a condition it dropped every op inside;
    and for an ``<Asset>`` written straight under ``<ModOps>`` -- which is how
    Anno 117 mods add assets, and a hundred of the mods on mod.io do -- it
    dropped the assets the rest of the mod is about.  None of them is read
    here either.  But not being read is the strongest reason not to delete
    something, so each takes a place among the ops and goes back out in it.

    ``written`` is the element as it stands in the file, when ``node`` is one
    made from it to go out instead: its line and the comment above it are
    the written one's, since one made apart has neither.
    """
    written = node if written is None else written
    return SourceOp(
        path=shown,
        line=written.sourceline or 0,
        where=where,
        comment=_comment_before(written),
        verbatim=etree.tostring(node, encoding="unicode", with_tail=False),
        # Never written: a verbatim op is rendered as its element. Only a place
        # to say why, where the element alone does not.
        attrs={"why": why} if why else {},
    )


# -- following an <Include> ---------------------------------------------------------
# As the loader follows one (``GetXmlOperations`` in its source): the element
# is ``Include`` in any case, as every element name it compares is, and the
# file is its ``File`` attribute exactly as spelled, which pugixml looks up
# as written.  The path is from the top of the mod when it starts with a
# slash, and from the including file's folder otherwise.
def is_include(node) -> bool:
    """Whether ``node`` is an ``<Include>``, spelled any way the loader takes."""
    return isinstance(node.tag, str) and node.tag.lower() == "include"


def include_file(node) -> str:
    """The file an ``<Include>`` names, as written."""
    return (node.get("File") or "").strip()


def include_target(named: str, source: Path, mod_root: Path) -> Path:
    """Where the file ``named`` by an ``<Include>`` in ``source`` is, resolved.

    Raises ``OSError`` or ``ValueError`` for a name that is not a path.
    """
    spelled = named.replace("\\", "/")
    if spelled.startswith("/"):
        return (Path(mod_root) / spelled.lstrip("/")).resolve()
    return (Path(source).parent / spelled).resolve()


def _include(node, ops, extras, where, reading: _Reading, depth: int) -> None:
    """Read the ops out of the file an ``<Include>`` names, in its place.

    A mod is allowed to keep its ModOps in files of its own and have the
    assets.xml the game looks for do nothing but list them -- *Charcoal Power
    Plant* is seven includes and no ops at all.  Read literally that mod does
    nothing, which is not a reading anybody wants.

    The path is relative to the file doing the including -- unless it starts
    with a slash, which is "from the top of this mod":
    ``File="/data/config/export/main/asset/prices.include.xml"``.  Joined to a
    folder as it stood, that is a path from the top of the *drive*, so the file
    was reported as not there and everything in it went unread: all but one op
    of *BT Balanced Profits*, and 87 includes across eleven of the Anno 1800
    mods on mod.io.

    Where the ops land
    matters as much as that they do: they are spliced in at the point of the
    ``<Include>``, because a mod's ops patch a document that is changing as they
    go and the order they run in is the order the file lists them.
    """
    spot = _line(where, node.sourceline or 0)
    named = include_file(node)
    if reading.inline is not None and _line_alone(node, named, ops, where, reading):
        return
    if is_skipped(node):
        extras.append(
            Extra(
                "<Include %s>" % named,
                spot,
                tr("the mod ships it switched off, so its ops are not read"),
            )
        )
        return
    if not named:
        extras.append(Extra("<Include>", spot, tr("names no file")))
        return
    if reading.source is None:
        extras.append(
            Extra("<Include %s>" % named, spot, tr("nothing to resolve the path against"))
        )
        return
    if depth >= MAX_INCLUDE_DEPTH:
        extras.append(
            Extra("<Include %s>" % named, spot,
                  tr("includes nested more than %s deep", MAX_INCLUDE_DEPTH))
        )
        return

    try:
        target = include_target(named, reading.source, _mod_root(reading.source, reading.base))
    except (OSError, ValueError):
        extras.append(Extra("<Include %s>" % named, spot, tr("is not a path")))
        return
    if target in reading.seen:
        extras.append(
            Extra("<Include %s>" % named, spot, tr("was already included, so "
                                                   "not read twice"))
        )
        return
    if not _is_there(target, reading.overrides):
        extras.append(
            Extra("<Include %s>" % named, spot, tr("the file it names is not there"))
        )
        return
    root, repairs = read_patch(target, reading.overrides)
    if root is None:
        extras.append(
            Extra("<Include %s>" % named, spot,
                  tr("unreadable, or not a ModOps document"))
        )
        kept = _kept_include(node, target, _mod_root(reading.source, reading.base))
        if kept is not None:
            ops.append(_set_aside(kept, where, "<Include %s>" % named, tr_noop(
                "the Studio could not read the file it names, so it goes back out as the "
                "<Include> it was, for the game to read", context=STORED), written=node))
        return
    if repairs:
        extras.append(_repaired("<Include %s>" % named, reading.describe(target), repairs))

    reading.seen.add(target)
    reading.taken.append(target)
    inner = _Reading(
        source=target, base=reading.base, taken=reading.taken, seen=reading.seen,
        game=reading.game, overrides=reading.overrides, grouped=reading.grouped,
        asides=reading.asides,
    )
    condition = next(
        (value for name, value in node.attrib.items() if name.lower() == "condition"), ""
    ).strip()
    if condition:
        inner.grouped += 1
        # The loader includes the file only if the condition holds, which makes
        # it a <Group Condition="…"> around the file's ops under another name.
        # The condition was not looked at, so the file was read as though it
        # were always included: *Weather Maker* keeps its patches for four other
        # mods in files of their own, each included on ``#ThatMod``, and all
        # four were read as things it does. It is carried the way such a group
        # is -- and as one, the file's ops written into it, so that the export
        # stands on its own without the file it named.
        group = etree.Element("Group")
        group.set("Condition", condition)
        # Written in from a reading of its own: what it marks as seen is only
        # for putting the text together, and the ops are still to be gathered.
        apart = _Reading(
            source=target, base=reading.base, taken=[], seen=set(reading.seen),
            game=reading.game, overrides=reading.overrides,
        )
        for child in _inlined(root, apart, depth + 1):
            group.append(child)
        scope = _set_aside(group, where, "<Group Condition>", written=node)
        scope.scope = uuid.uuid4().hex[:12]
        scope.condition = condition
        scope.game = reading.game or ""
        ops.append(scope)
        inside = len(ops)
        _collect_into(root, ops, extras, reading.describe(target), inner, depth + 1)
        for op in ops[inside:]:
            op.scopes = (scope.scope,) + tuple(op.scopes)
        return
    kept = _as_a_file(node, target, reading)
    start = len(ops)
    if kept:
        # The line itself, in the file it stands in: it takes a place among
        # the ops, and the file it names is written again from the ops
        # that say they are in it.
        line = _set_aside(node, where, "<Include %s>" % named)
        line.attrs = {INCLUDES: kept}
        ops.append(line)
    inside = len(ops)
    _collect_into(root, ops, extras, reading.describe(target), inner, depth + 1)
    if kept:
        for op in ops[inside:]:
            # One a file inside this one names has said so already.
            op.file = op.file or kept
    else:
        _hand_on_the_note(node, ops, start, where, reading)


# On the op that stands for an ``<Include>`` line whose file is kept as a
# file: the file it names, by its path from the top of the mod.
INCLUDES = "includes"
# Written into such a line, in a copy of a text that is about to be read, by
# whoever holds the file's ops beside it: which of the regions it holds is
# this line's.  Never in anything shown or written.
REGION = "ams-region"


def _line_alone(node, named: str, ops, where: str, reading: _Reading) -> bool:
    """Keep an ``<Include>`` as the line it is, where the text being read
    has its file's ops beside it; whether there is nothing more to do.

    ``reading.inline`` is told of every ``<Include>`` met: ``region``, the
    mark the line carried, and ``op``, the op that stands for the line where
    it is one whose file is kept as a file (:func:`_as_a_file`).  For such a
    line ``INCLUDES`` is the file where the text has a file of its own to
    go by, and "" where only the line can say.

    A line that is not of that kind -- on a condition, in a group, naming
    another mod's file -- is followed as it always was, unless it carries a
    mark: its file's ops are in the text, and read from the folder as well
    they would be there twice.
    """
    region = node.attrib.pop(REGION, None)
    told = {"region": region, "line": node.sourceline or 0, "op": None}
    if reading.inline is not None:
        reading.inline.append(told)
    alone = bool(named) and not reading.grouped and all(name == "File" for name in node.attrib)
    name = ""
    if alone and reading.source is not None:
        try:
            target = include_target(named, reading.source, _mod_root(reading.source, reading.base))
        except (OSError, ValueError):
            target = None
        name = _as_a_file(node, target, reading) if target is not None else ""
        alone = bool(name)
        if (
            alone and region is None and target is not None
            and _is_there(target, reading.overrides)
            and read_patch(target, reading.overrides)[0] is None
        ):
            # A file the mod has and the Studio cannot read is left to the
            # game, as it is when the folder is read: the line is carried
            # unread, and nothing is written over the file.
            alone = False
    if not alone:
        return region is not None
    line = _set_aside(node, where, "<Include %s>" % named)
    line.attrs = {INCLUDES: name}
    ops.append(line)
    told["op"] = line
    return True


def _as_a_file(node, target: Path, reading: _Reading) -> str:
    """The path from the top of the mod of a file an ``<Include>`` names,
    where the file is kept as a file; "" where its ops are written into the
    file that names it, as every file's were.

    Kept: a file of the mod's own, named by a line that stands in no group
    and asks nothing more than to be read.  One in another mod's folder is
    not this mod's to write; and inside a group, or on a condition, a change
    of the Studio's own among the file's ops is written with the group
    closed round it, which a file cannot be.
    """
    if reading.grouped or reading.source is None:
        return ""
    if any(name != "File" for name in node.attrib):
        return ""
    try:
        root = Path(_mod_root(reading.source, reading.base)).resolve()
        rooted = Path(target).relative_to(root).as_posix()
    except (OSError, ValueError):
        return ""
    return rooted


def _kept_include(node, target: Path, mod_root: Path):
    """An ``<Include>`` whose file could not be read, to go back out as itself.

    Dropped, the export lost everything in the file while the game reads it:
    *Super Specialists* has stray text before the XML declaration of one of
    its eleven files, which the loader's parser passes over and this one
    refuses. Whether the game's parser does too is the game's business, so
    the file is left to it -- carried with the rest of the mod, and named
    from the top of the mod, which reaches it from wherever the patch that
    holds this is written. ``None`` for a file outside the mod.
    """
    try:
        rooted = Path(target).relative_to(Path(mod_root).resolve())
    except ValueError:
        return None
    kept = copy.deepcopy(node)
    kept.tail = None
    for name in list(kept.attrib):
        if name == "File":
            kept.set(name, "/" + rooted.as_posix())
    return kept


def _standing_alone(group, reading: _Reading, depth: int):
    """A group as it has to be written to stand on its own: includes written in.

    *Subway Network* wraps an ``<Include>`` in ``<Group Condition="!#ThatMod">``.
    Kept as written, the group went out still naming the file -- which an export
    does not write, so out of the mod's own folder everything in it was gone.
    """
    if group.find(".//Include") is None or reading.source is None:
        return group
    apart = _Reading(
        source=reading.source, base=reading.base, taken=[], seen=set(reading.seen),
        game=reading.game, overrides=reading.overrides,
    )
    alone = etree.Element(group.tag)
    for name, value in group.attrib.items():
        alone.set(name, value)
    alone.extend(_inlined(group, apart, depth))
    return alone


def _inlined(root, reading: _Reading, depth: int) -> list:
    """The elements of an included file, with the files *it* includes written in.

    For a file that is carried rather than read: what it includes is named
    relative to it, and would name nothing from wherever it is carried to.
    One that cannot be found is left as the ``<Include>`` it was.
    """
    found = []
    for node in root:
        if not isinstance(node.tag, str):
            if isinstance(node, etree._Comment):
                # What the file says besides its ops goes with them.
                found.append(copy.deepcopy(node))
            continue
        named = include_file(node)
        if node.tag.lower() == "group" and node.find(".//Include") is not None:
            # A group inside it may include a file too, at any depth.
            inner = etree.Element(node.tag)
            for name, value in node.attrib.items():
                inner.set(name, value)
            inner.extend(_inlined(node, reading, depth))
            found.append(inner)
            continue
        if (
            not is_include(node) or not named or depth >= MAX_INCLUDE_DEPTH or is_skipped(node)
            or reading.source is None
        ):
            found.append(copy.deepcopy(node))
            continue
        try:
            target = include_target(named, reading.source, _mod_root(reading.source, reading.base))
        except (OSError, ValueError):
            target = None
        inner_root = (
            read_patch(target, reading.overrides)[0]
            if target is not None and _is_there(target, reading.overrides) else None
        )
        if inner_root is None or target in reading.seen:
            found.append(copy.deepcopy(node))
            continue
        reading.seen.add(target)
        reading.taken.append(target)
        inner = _Reading(
            source=target, base=reading.base, taken=reading.taken, seen=reading.seen,
            game=reading.game, overrides=reading.overrides,
        )
        children = _inlined(inner_root, inner, depth + 1)
        condition = next(
            (value for name, value in node.attrib.items() if name.lower() == "condition"), ""
        ).strip()
        if condition:
            group = etree.Element("Group")
            group.set("Condition", condition)
            group.extend(children)
            children = [group]
        found.extend(children)
    return found


def mod_root(source: Path) -> Path:
    """The top of the mod a patch at ``source`` is in, going by its path."""
    return _mod_root(source, None)


def _mod_root(source: Path, base: Optional[Path]) -> Path:
    """The folder a rooted ``<Include>`` in ``source`` is rooted in: the top
    of the mod.

    ``base``, the folder that was read, when a folder was.  A patch read on
    its own has only its path to go by, and the top of its mod is whatever
    holds the nearest ``data`` above it.
    """
    source = Path(source)
    if base is not None and Path(base) != source.parent:
        return Path(base)
    for folder in source.parents:
        if folder.name.lower() == "data":
            return folder.parent
    return source.parent


def _read_op(node, where: str, game: Optional[str] = None) -> SourceOp:
    attrs = dict(node.attrib)
    if game == games.ANNO_117:
        attrs = as_117_reads(attrs)
    kind, guid, path = type_and_target(attrs, game or "")
    return SourceOp(
        kind=kind,
        guid=guid,
        path=path,
        condition=(attrs.get("Condition") or "").strip(),
        payload=children(node),
        inside=tuple(node),
        text=(node.text or "").strip(),
        raw_text=node.text or "",
        attrs=attrs,
        line=node.sourceline or 0,
        where=where,
        comment=_comment_before(node),
        game=game or "",
    )


# -------------------------------------------------------------- path resolution
# Compiled search expressions, held because the same handful are asked of
# thousands of assets: compiling one costs more than running it.
_COMPILED: Dict[str, object] = {}
MAX_COMPILED = 2000


def compiled_search(path: str):
    """One mod's search expression, compiled once and kept: as it reads for
    one asset on its own (:func:`.index.in_one_asset`), which is how
    ``AssetIndex.find_matching`` sweeps the file for it."""
    if path not in _COMPILED:
        if len(_COMPILED) >= MAX_COMPILED:
            _COMPILED.clear()
        try:
            etree.XPath(path)
            _COMPILED[path] = etree.XPath(" | ".join(in_one_asset(path)))
        except etree.XPathError:
            _COMPILED[path] = None
    return _COMPILED[path]


def search_in(asset, path: str) -> List:
    """The elements a search-targeting path finds in one asset.

    Against the asset as its own document, which is what makes ``//Asset[…]``
    and ``//Values/Trader`` mean here what they mean when the game evaluates
    them over the whole file.

    What an op is carried out on.  Whether the search is about the asset at
    all is :func:`matches_anything`, which is also what the sweep of the
    whole file asks: asked by this instead, a search finding text matched an
    asset no mod had touched and not one a mod had.
    """
    expression = compiled_search(path)
    if expression is None:
        return []
    try:
        found = expression(asset.getroottree())
    except etree.XPathError:
        return []
    if not isinstance(found, list):
        # A yes-or-no or a number (``//Missing or 1``): no set of nodes, so
        # nothing an op is carried out on.
        return []
    return [node for node in found if isinstance(getattr(node, "tag", None), str)]


def matches_anything(asset, path: str) -> Optional[bool]:
    """Whether a search -- an op's or a condition's -- finds anything in one
    asset, the way the loader decides it (:func:`.index.found_anything`).
    ``None`` for one that cannot be read."""
    expression = compiled_search(path)
    if expression is None:
        return None
    try:
        found = expression(asset.getroottree())
    except etree.XPathError:
        return None
    return found_anything(found)


def dialects(path: str) -> List[str]:
    """The readings :func:`_resolve` tries for a path into an asset, the
    shorter one first; ``[""]`` for the asset itself."""
    path = path.rstrip("/")
    if not path:
        return [""]
    if not path.startswith("/"):
        path = "/" + path
    if path == "/Values" or path.startswith("/Values/"):
        return [path]
    return ["/Values" + path, path]


def _finds(node, path: str) -> bool:
    """Whether a condition's path finds anything from ``node``, the way the
    loader's docs put it: a non-empty list or ``true()``.

    Text and attributes are a list as much as elements are, which
    :func:`_resolve` -- finding nodes to change -- rightly leaves out.
    *Community Patch* guards 20 ops over 44 items with ``…/SetBuff/text()``;
    read as finding nothing, not one of the 59 the game's file answers yes
    to ran.  A path that cannot be read finds
    nothing, as the loader logs it and moves on.
    """
    path = path.rstrip("/")
    if not path:
        return True
    try:
        found = node.xpath("." + path if path.startswith("/") else "./" + path)
    except etree.XPathError:
        return False
    if isinstance(found, list):
        return bool(found)
    if isinstance(found, float):
        return found == found and found != 0
    return bool(found)


# What may be left once those are replaced for the Studio to work it out:
# logic and comparisons over constants. Anything else reaches into the game's
# files, and the two mixed in one condition are not answered here.
_CONSTANT_LOGIC = re.compile(
    r"(?:\s+|true\(\)|false\(\)|not\b|and\b|or\b|[()]|!=|<=|>=|[=<>]"
    r"|-?[0-9]+(?:\.[0-9]+)?)*"
)
def _combined_holds(text: str, present, chosen) -> Optional[bool]:
    """Anno 117's answer to a condition that combines mods and settings.

    ``None`` wherever a part cannot be answered: no mods folder to ask about
    mods, a setting the mod does not declare or holds as anything but a yes, a
    no or a number, or a path into the game's files alongside them.
    """
    unanswered = False

    def value(match) -> str:
        nonlocal unanswered
        token = match.group(0)
        name = token[1:]
        if token[0] == "#":
            if present is None:
                unanswered = True
                return ""
            return "true()" if name.lower() in present else "false()"
        if not chosen or name not in chosen:
            unanswered = True
            return ""
        held = str(chosen[name]).strip()
        if held.lower() in ("true", "false"):
            return held.lower() + "()"
        if NUMBER.fullmatch(held):
            return held
        unanswered = True
        return ""

    expression = LOADER_VARIABLE.sub(value, text)
    if unanswered or not _CONSTANT_LOGIC.fullmatch(expression):
        return None
    try:
        answer = etree.XPath(expression)(etree.Element("condition"))
    except etree.XPathError:
        return None
    return answer if isinstance(answer, bool) else None


def condition_holds(
    condition: str, lookup, guid: str = "", present=None, chosen=None, game: str = "",
    search=None, in_document: bool = False, from_values: Optional[bool] = None,
) -> Optional[bool]:
    """Whether a ``Condition`` is true of the assets.xml being imported against.

    A condition may name its own asset (``@1234/Cost``, read from the
    asset's ``<Values>``) or, far more often, be a bare path -- which the game reads against the very asset the op
    is aimed at, exactly as ``Path`` is.  Answering only the first form left
    every op with the ordinary kind unread.

    ``#some-mod`` asks whether another mod is installed, which is a question
    about the whole mods folder rather than about assets.xml.  ``present`` is
    the set of mods loaded alongside this one; without it the question has no
    answer here, and passing an empty set is the way to say "these are all of
    them, and it is not among them".

    ``$some-setting`` asks what the mod's own options are set to -- a question
    about the mod rather than about the game, answered from ``chosen``, which is
    what its ``modinfo.json`` declares unless somebody has said otherwise.
    ``$mod-id.some-setting`` names it in full, the mod's own or another
    loaded mod's, and ``chosen`` holds those under the full name
    (:func:`.settings.visible`).

    ``~/Values/…`` is the older spelling of the bare relative path: the loader
    strips the ``~`` and reads what follows against the op's own asset, exactly
    as it does a path with no prefix at all (its source marks the form as one
    to deprecate).  Forty-nine of the Anno 1800 ops installed here still use
    it, so it is read the same way.

    Anno 117 lets those two combine, ``#mod-a or #mod-b``, and a leading ``!``
    then turns the whole of it round; see :data:`LOADER_VARIABLE`.  Anno 1800
    takes everything after the ``#`` as one ModID, so the same text asks after
    a mod called ``mod-a or #mod-b``, which is never there.  Read as one ModID
    in 117 it came out the other way round, whichever mods were loaded.  Where
    the game is not known, a combination is not answered.

    ``//Values[Standard/GUID='1000']`` searches the whole document, and holds if
    anything at all matches -- the loader's "a non-empty list".  735 conditions
    in 84 Anno 1800 mods on mod.io are this kind, most asking whether another
    mod's asset is there or what an earlier op left behind, so the answer has
    to be the document as it stands when the op runs.  ``search(path)`` gives
    that, from the caller that knows it; without one it is not answered.

    ``in_document`` says there is no asset for a bare path to start from -- a
    ``<Group>``'s condition, or an op that searches rather than naming a GUID
    -- so ``/Values/…`` is an absolute path in the whole document, as XPath
    reads one.  The top of assets.xml is ``<AssetList>``, so it finds nothing:
    *Dockland Redux* guards a group with ``/Values[Building/…]``, which read
    that way never runs, and its twin with the ``!`` always does.

    ``from_values`` says where a bare path on an op starts, when the caller
    knows the op: ``False`` for a ``Type=`` op, from its ``<Asset>``, and
    ``True`` for one in the short form (``GUID="…" Add="…"``), from its
    ``<Values>`` -- the docs' "the Condition path skips /Values/", and every
    xmltest build, measured. Left ``None``, both are tried. *Production Works*
    asks ``!~/ExpeditionFeature/…`` of a ``Type=`` op: from the asset that finds
    nothing, so the op always runs, and tried from ``<Values>`` as well it
    found what an earlier mod had added and the op was read as not running.

    ``None`` means it could not be answered, which is not the same as false: an
    op we cannot decide about is one we must not silently drop.
    """
    text = (condition or "").strip()
    if not text:
        return True
    negated = text.startswith("!")
    if negated:
        text = text[1:].strip()
    relative = text.startswith("~")
    if relative:
        text = text[1:].strip()
    if game == games.ANNO_117 and combines(text):
        held = _combined_holds(text, present, chosen)
        if held is None:
            return None
        return (not held) if negated else held
    if text.startswith("#"):
        if game != games.ANNO_1800 and combines(text):
            # No game, no telling which of the two readings is meant.
            return None
        if present is None:
            return None
        found = text[1:].strip().lower() in present
        return (not found) if negated else found
    if text.startswith("$"):
        name = text[1:].strip()
        if not chosen or name not in chosen:
            return None
        held = settings.is_on(chosen[name])
        return (not held) if negated else held
    if text.startswith("//"):
        # ``~//`` would be a search from the op's own asset; what the loader
        # makes of that is not known, so it stays unanswered.
        if search is None or relative:
            return None
        found = search(text)
        if found is None:
            return None
        return (not found) if negated else bool(found)
    matched = AT_ASSET.match(text)
    if matched:
        path = (matched.group(2) or "").rstrip("/")
        asset = lookup(matched.group(1))
        # From the asset's ``<Values>``, as the docs spell it out -- and as
        # xmltest reads it -- so ``@1234/Values/Cost`` finds nothing.
        values = asset.find("Values") if asset is not None else None
        found = values is not None and _finds(values, path)
        if found and path.startswith("//"):
            # The docs make ``@1234`` short for
            # ``//Values[Standard/GUID='1234']``, so ``@1234//Item`` searches
            # the asset; every xmltest build finds nothing with it at all, its
            # short cut for a GUID reading the ``//`` from the top of the
            # document. Where the search finds nothing the two agree. Where it
            # finds something they do not, and which the game does is not
            # known, so the op goes back out as written and the game decides.
            return None
    elif guid and relative and not text:
        # ``~`` alone is the op's own asset, there or not: every xmltest
        # build, measured. *Industrial Cities* guards an op with it.
        found = lookup(guid) is not None
    elif in_document and text.startswith("/") and not text.startswith("//"):
        top = _FIRST_STEP.match(text)
        if top is None or top.group(1) == "AssetList":
            return None
        found = False
    elif guid and text.startswith("/") and not text.startswith("//"):
        asset = lookup(guid)
        if asset is None:
            found = False
        elif from_values is None:
            found = any(_finds(asset, candidate) for candidate in dialects(text))
        else:
            start = asset.find("Values") if from_values else asset
            found = start is not None and _finds(start, text)
    else:
        return None
    return (not found) if negated else found


# ------------------------------------------------------------- reading a folder
# Files that are plainly documentation or packaging rather than game data, by
# extension.  A file without one is among them whatever it is called
# (:func:`_kind_of_file`).
SIDE_FILES = {".txt", ".md", ".png", ".jpg", ".jpeg", ".json", ".jsonc", ".log"}

# A mod's own XML is small; anything this size is not a hand-written patch and
# is not worth parsing to find out.
MAX_PATCH_BYTES = 32 * 1024 * 1024


def asset_patch_relpaths():
    """The place each game expects a mod's assets.xml, longest first."""
    return sorted(
        (game.asset_relpath for game in games.GAMES.values()),
        key=lambda p: len(p.parts),
        reverse=True,
    )


def is_asset_patch(relative: Path) -> bool:
    parts = [part.lower() for part in relative.parts]
    for candidate in asset_patch_relpaths():
        wanted = [part.lower() for part in candidate.parts]
        if parts[-len(wanted):] == wanted:
            return True
    return False


def _at_the_top(relative: Path) -> bool:
    """Whether a patch sits where a mod's own does: at the top of the mod,
    exactly at either game's path, which is the one place the loader looks."""
    parts = [part.lower() for part in relative.parts]
    return any(parts == [part.lower() for part in candidate.parts]
               for candidate in asset_patch_relpaths())


def game_for_patch(relative: Path) -> Optional[str]:
    """Which game a ModOp patch file's own path says it belongs to.

    Each game keeps its assets.xml at a different, fixed place inside a mod --
    ``data/config/export/main/asset/assets.xml`` for 1800,
    ``data/base/config/export/assets.xml`` for 117 -- and every real mod's patch
    sits exactly where its game expects it, independent of anything the mod
    claims about itself.  That is the same structural signal
    :func:`games.detect_from_path` uses for the base file; this is its mirror
    for a mod's own patch.
    """
    parts = [part.lower() for part in relative.parts]
    for key, game in games.GAMES.items():
        wanted = [part.lower() for part in game.asset_relpath.parts]
        if parts[-len(wanted):] == wanted:
            return key
    return None


def _modops_root(path: Path):
    """The document root if this file is a ModOps patch, else None."""
    return read_patch(path)[0]


# What the game's mod loader reads in a patch that a strict XML parser refuses.
# Each was tried against the loader itself (xmltest, both games' builds), and
# across the 20,000 XML files in the mod.io census 126 patches need one: 96 read
# cleanly once repaired this way, 29 only as far as their broken structure
# allows -- which is also what the loader does, applying the ops it got through
# -- and one is empty.
_DECLARED_ENCODING = re.compile(
    rb"""^(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
_DECLARATION_ENCODING = re.compile(rb"""(<\?xml[^>]*?encoding\s*=\s*["'])[^"']+(["'])""", re.IGNORECASE)
_COMMENT = re.compile(rb"<!--(.*?)-->", re.DOTALL)
_BARE_AMPERSAND = re.compile(rb"&(?!#?[A-Za-z0-9]+;)")
_QUOTED = re.compile(rb"""("[^"]*"|'[^']*')""")
# Only entities a file defines itself, and no network, said rather than
# left to lxml's defaults.
_RECOVERING = etree.XMLParser(recover=True, resolve_entities="internal", no_network=True)


# What opens with ``<`` and is not a tag with attributes, and where each ends.
_NOT_A_TAG = ((b"<!--", b"-->"), (b"<![CDATA[", b"]]>"), (b"<?", b"?>"), (b"<!", b">"), (b"</", b">"))


def _escape_inside_attributes(text: bytes) -> bytes:
    """``<`` inside attribute values, escaped.

    A condition comparing numbers is written ``Radius < 11`` by more than one
    mod.  Tags are walked with their quotes rather than matched as ``<…>``,
    because the ``<`` being repaired is exactly what breaks that.

    A comment is passed over to its ``-->`` and not to its first ``>``: ended
    there, what was left of ``<!-- a > b <- don't -->`` was taken for a tag,
    its apostrophe for a quote that ran to the end of the file, and the read
    came back with no ops.  And a ``>`` in a value is left alone, being
    legal there: escaped too, a file repaired for something else was said to
    have had a ``<`` inside an attribute.
    """
    out, i = bytearray(), 0
    while i < len(text):
        start = text.find(b"<", i)
        if start < 0:
            out += text[i:]
            break
        out += text[i:start]
        closes = next((close for opens, close in _NOT_A_TAG if text.startswith(opens, start)), None)
        if closes is not None:
            end = text.find(closes, start)
            end = len(text) if end < 0 else end + len(closes)
            out += text[start:end]
            i = end
            continue
        end, quote = start + 1, None
        while end < len(text):
            char = text[end : end + 1]
            if quote:
                if char == quote:
                    quote = None
            elif char in (b'"', b"'"):
                quote = char
            elif char == b">":
                break
            end += 1
        out += _QUOTED.sub(
            lambda m: m.group(0)[:1]
            + m.group(0)[1:-1].replace(b"<", b"&lt;")
            + m.group(0)[:1],
            text[start : end + 1],
        )
        i = end + 1
    return bytes(out)


def _read_leniently(raw: bytes):
    """A patch a strict parser refused, read the way the loader reads it."""
    repairs: List[str] = []
    declared = _DECLARED_ENCODING.match(raw)
    encoding = declared.group(1).decode("ascii", "replace") if declared else "utf-8"
    try:
        decoded = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        decoded = raw.decode("utf-8", errors="replace")
        repairs.append(tr("text that is not in the encoding it says it is")
                       if declared and encoding.lower().replace("-", "") != "utf8"
                       else tr("text that is not UTF-8"))
    text = decoded.encode("utf-8")
    if declared:
        text = _DECLARATION_ENCODING.sub(rb"\1utf-8\2", text, count=1)

    def comment(match):
        inner = re.sub(rb"-(?=-)", b"- ", match.group(1))
        return b"<!--" + (inner + b" " if inner.endswith(b"-") else inner) + b"-->"

    for label, repair in (
        (tr("'--' inside a comment"), lambda t: _COMMENT.sub(comment, t)),
        (tr("a bare '&'"), lambda t: _BARE_AMPERSAND.sub(b"&amp;", t)),
        (tr("'<' inside an attribute"), _escape_inside_attributes),
    ):
        fixed = repair(text)
        if fixed != text:
            repairs.append(label)
            text = fixed
    try:
        return etree.fromstring(text), repairs
    except etree.XMLSyntaxError:
        pass
    try:
        root = etree.fromstring(text, _RECOVERING)
    except etree.XMLSyntaxError:
        return None, repairs
    if root is None:
        return None, repairs
    return root, [*repairs, tr("a broken structure, read as far as it goes")]


def _is_there(path: Path, overrides=None) -> bool:
    """Whether a file can be read: on disk, or standing in for one in memory."""
    if overrides and _resolved(path) in overrides:
        return True
    return path.is_file()


def _resolved(path: Path) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        return Path(path)


def read_patch(path: Path, overrides=None):
    """``(root, repairs)``: the ModOps root, or None, and what reading it took.

    ``repairs`` is empty for a well-formed file.  Otherwise it names each thing
    the game reads and a strict parser does not, so the mod can say its patch
    was read that way rather than quietly being different.  ``overrides`` is
    files to read in place of what is on disk -- see :class:`_Reading`.
    """
    raw = overrides.get(_resolved(path)) if overrides else None
    if raw is None:
        try:
            if path.stat().st_size > MAX_PATCH_BYTES:
                return None, []
            raw = path.read_bytes()
        except OSError:
            return None, []
    try:
        root, repairs = etree.fromstring(raw), []
    except etree.XMLSyntaxError:
        root, repairs = _read_leniently(raw)
    if root is None or root.tag != "ModOps":
        return None, []
    return root, repairs


def _repaired(what: str, where: str, repairs: List[str]) -> Extra:
    return Extra(
        what,
        where,
        tr("not well-formed XML, read the way the game's loader reads it: %s",
           ", ".join(repairs)),
    )


def _other_patch() -> str:
    """What the folder scan calls a ModOps file that is not an assets patch."""
    return tr("a ModOps patch for another game file")


def _read_for_names(relative: Path) -> bool:
    """Whether a file in a mod is a texts patch read for its names: one
    :func:`.texts.patch_file` finds, at either game's gui folder at the top of
    the mod, matched without regard to case as Windows matches it."""
    parent = [part.lower() for part in relative.parent.parts]
    return texts.is_texts_file(relative) and any(
        parent == [part.lower() for part in game.gui_relpath.parts]
        for game in games.GAMES.values()
    )


def _kind_of_file(path: Path) -> Tuple[str, bool]:
    """What a file is grouped by, and whether it is shipped alongside the mod
    and not in place of a game file.

    Its extension, lower case, where it has one.  A README or a LICENSE has
    none and ``.gitignore`` or ``.DS_Store`` is nothing else -- ``Path.suffix``
    reads a leading dot as part of the name, and is empty for all four -- and
    no file of either game is named so: such a file is alongside, the one that
    is all extension as a kind of its own.
    """
    suffix = path.suffix.lower()
    if suffix:
        return suffix, suffix in SIDE_FILES
    return (path.name if path.name.startswith(".") else ""), True


def scan_folder(root: Path) -> Tuple[List[Path], List[Extra], dict]:
    """Sort a mod folder into asset patches, everything else, and its modinfo.

    "Everything else" is deliberately not silent.  A mod that also ships a text
    patch, a replaced building mesh, or a second mod bundled inside it has been
    only partly imported once the asset ModOps are in, and the parts left
    behind are exactly what an author would otherwise discover in game.
    """
    root = Path(root)
    patches: List[Path] = []
    extras: List[Extra] = []
    metadata: dict = {}
    grouped: Dict[Tuple[str, bool], List[str]] = {}
    bundled = modfolder.bundled_in(root)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        shown = relative.as_posix()

        # ".jsonc" is JSON with comments, which is what the community's editor
        # extension writes and what the 117 loader takes.
        if path.name.lower() in modfolder.MODINFO:
            if relative.parent == Path("."):
                metadata = modfolder.read_metadata(path)
                if not modfolder.modinfo_text(path)[1]:
                    extras.append(
                        Extra(
                            tr("a modinfo that is not UTF-8"),
                            shown,
                            tr("read with what would not decode replaced: its ID "
                               "and load order came through, its names in other "
                               "scripts did not, and exporting the mod writes them "
                               "back as read"),
                        )
                    )
            else:
                extras.append(
                    Extra(
                        tr("a mod bundled inside this one"),
                        shown,
                        tr("read as a mod of its own, with its own place in load order"),
                    )
                )
            continue

        # A file of a mod bundled in here belongs to that mod, which is a mod
        # in its own right and is read as one -- its texts patch and its
        # replaced files too, and not only its assets.xml. Folding its ops into
        # this mod's file would apply them twice: once here, and once where the
        # game loads that mod itself.
        if any(path.is_relative_to(inside) for inside in bundled):
            continue

        if is_asset_patch(relative):
            if _at_the_top(relative):
                patches.append(path)
                continue
            # Deeper, in a folder with a data tree and no modinfo of its own:
            # not a mod, and not part of this one. Anno 1800 loads it neither
            # way -- measured in game, P7-32: its loader log names no such
            # folder, and an op in one never runs -- and Anno 117's docs have a
            # nested mod carry a modinfo. Read as this mod's, it went into the
            # export's own patch, and the game ran what it never had.
            extras.append(
                Extra(
                    tr("a patch the game does not load"),
                    shown,
                    tr("it sits in a folder with a data tree and no modinfo of its own, "
                       "which the game loads neither as a mod nor as part of this one, "
                       "so its ModOps are not read"),
                )
            )
            continue

        if path.suffix.lower() == ".xml" and _modops_root(path) is not None:
            if _read_for_names(relative):
                # What the mod calls things: read, for names, in the language
                # the game's own texts are loaded in -- see core.texts.
                extras.append(
                    Extra(tr("a localisation patch"), shown,
                          tr("read for the names it gives, in the language the game's "
                             "texts are loaded in"))
                )
                continue
            if shown.lower() == modfolder.INFOTIPS_PATCH:
                # The game's tooltips: listed with the InfoTips they touch,
                # not read for what those come to -- see core.infotips.
                extras.append(
                    Extra(tr("an InfoTip patch"), shown,
                          tr("the InfoTips it touches are listed with the "
                             "mod, and what they come to is not read"))
                )
                continue
            if path.name.lower() == "templates.xml":
                # The game's list of templates: named, not read -- see
                # core.sidefiles.
                extras.append(
                    Extra(tr("a templates.xml patch"), shown, sidefiles.templates(path))
                )
                continue
            if path.name.lower().endswith((".cfg.xml", ".fc.xml")):
                # Anno 1800's model files, patched rather than replaced: in the
                # census, always the world map, adding sessions to it.
                extras.append(
                    Extra(tr("a model file patch"), shown, sidefiles.model_patch(path))
                )
                continue
            extras.append(
                Extra(_other_patch(), shown,
                      tr("the Studio only models assets.xml and the names in texts files"))
            )
            continue

        grouped.setdefault(_kind_of_file(path), []).append(shown)

    for (suffix, alongside), found in sorted(grouped.items(), key=lambda item: -len(item[1])):
        where = found[0] if len(found) == 1 else trn(
            "%(file)s and %(n)s more", "%(file)s and %(n)s more",
            len(found) - 1, file=found[0],
        )
        if suffix == ".lnk":
            # Anno 1800's link files: a path standing for another file.
            extras.append(
                Extra(trn("%(n)s link file", "%(n)s link files", len(found)),
                      where, sidefiles.links(root, found))
            )
            continue
        detail = (
            tr("shipped alongside the mod")
            if alongside
            else tr("replaces a game file outright and does not patch it")
        )
        if suffix:
            what = trn("%(n)s %(suffix)s file", "%(n)s %(suffix)s files",
                       len(found), suffix=suffix)
        else:
            what = trn("%(n)s file with no extension", "%(n)s files with no extension",
                       len(found))
        extras.append(Extra(what, where, detail))
    return patches, extras, metadata


def read_source(
    source: Path, origin: str = "", reading: Optional[Reading] = None,
) -> Tuple[ImportReport, List[SourceOp]]:
    """Everything a mod folder or patch file has to say, without deciding yet.

    Split out from :func:`.reader.analyse` because a session reads many mods and runs
    them as one stream: the reading is per mod, the deciding is not.

    ``reading`` says what the mod's own options are set to.  For most mods
    that only matters later, when their conditions are answered; for a mod
    with iModYourAnno settings it decides which files are read at all, since
    those settings are files -- so a mod asked for at settings other than the
    ones its folder holds is read from the files iModYourAnno would write for
    them, rendered in memory, and nothing on disk changes.
    """
    # Through a link to a generated copy, the author's files: the copy's Skips
    # and settings are the Studio's, written there from a loadout's edits.
    source = generated.author_folder(source)
    report = ImportReport(root=source)
    overrides = None

    if source.is_dir():
        report.mod_name = source.name
        report.patch_files, report.extras, report.metadata = scan_folder(source)
        if not report.patch_files:
            report.error = tr("No assets.xml patch in %s.", source.name)
            return report, []
        report.tweaks = imya.read(source)
        if report.tweaks:
            offered = report.tweaks.options()
            declared = report.metadata.get("options")
            report.metadata["options"] = (
                settings.ModOptions(list(declared.options) + list(offered.options))
                if declared is not None else offered
            )
        chosen = reading.chosen_for(origin, report.metadata) if reading is not None else None
        if chosen:
            report.options_read = dict(chosen)
        elif report.tweaks:
            report.options_read = report.tweaks.chosen()
        if report.tweaks and chosen:
            wanted = dict(report.tweaks.current)
            wanted.update(
                (name, str(value)) for name, value in chosen.items()
                if name in wanted
            )
            if wanted != report.tweaks.current:
                overrides = imya.render(source, report.tweaks, wanted)
    elif source.is_file():
        report.mod_name = source.parent.name
        report.patch_files = [source]
    else:
        report.error = tr("%s is not there.", source)
        return report, []

    collected: List[SourceOp] = []
    base = source if source.is_dir() else source.parent
    included: List[Path] = []
    for path in report.patch_files:
        try:
            shown = path.relative_to(base).as_posix()
        except ValueError:
            shown = path.name
        root, repairs = read_patch(path, overrides)
        if root is None:
            report.extras.append(
                Extra(tr("an assets.xml that is not a ModOps patch"), shown,
                      tr("unreadable, or a whole assets file and not a patch"))
            )
            continue
        if repairs:
            report.extras.append(_repaired(tr("an assets.xml"), shown, repairs))
        # By where the file is, not by what it is shown as: a patch read on
        # its own is shown by its name alone, and its folders are what say
        # which game it is for.
        for op in parse_ops(
            root, report.extras, shown, source=path, base=base, taken=included,
            game=game_for_patch(path), overrides=overrides, asides=True,
        ):
            op.mod = origin
            collected.append(op)

    # Files pulled in by an <Include> were read as part of the file that named
    # them, so they go on the list after it rather than being read again.
    for path in included:
        if path not in report.patch_files:
            report.patch_files.append(path)

    # The folder scan met those files before anything named them, and listed
    # each as a patch for a game file the Studio does not model. Read after
    # all, it is not something left behind: *Celtic Independence Overhaul*
    # keeps its ops in 20 include files, and all 20 were listed as not read
    # beside the changes read out of them. One no <Include> named stays.
    read = {
        path.relative_to(_resolved(base)).as_posix()
        for path in included if path.is_relative_to(_resolved(base))
    }
    if read:
        report.extras = [
            extra for extra in report.extras
            if not (extra.what == _other_patch() and extra.where in read)
        ]

    return report, collected


# --------------------------------------------------------- how a mod is read
@dataclass
class Reading:
    """How to read a mod: the answers only its author or its user can give.

    ``options`` is what its own settings are set to.  Every op is read --
    the value it comes to today is worked out and shown, because that is
    what the game does with it -- and kept as the op it is, so a value it
    works out as it patches goes on being worked out by the game.
    """

    options: Dict[str, Dict[str, str]] = field(default_factory=dict)
    keep_inert: bool = False
    # What the game itself has the options set to, by lower-case ModID: Anno
    # 117's active-options.jsonc (see :func:`core.switches.game_options`).  The
    # answer for a mod nobody has answered here, so a mod is read at the
    # values the game runs rather than at its author's defaults.
    game_options: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def chosen_for(self, origin: str, metadata=None) -> Optional[Dict[str, str]]:
        """What a mod's options are set to: as answered here, else as the
        game has them, else None -- its defaults, or for a mod with
        iModYourAnno settings, whatever its folder holds."""
        if self.options.get(origin):
            return self.options[origin]
        mod_id = str((metadata or {}).get("mod_id") or "").strip().lower()
        if mod_id and self.game_options.get(mod_id):
            return self.game_options[mod_id]
        return None

    def recorded(self) -> Dict[str, bool]:
        """The choice, to keep with a mod that was read this way."""
        return {"keep_inert": self.keep_inert}

    @classmethod
    def as_recorded(cls, recorded, options=None) -> "Reading":
        """The reading a mod came in under, to read it the same way again.
        The two settings the narrow reading recorded beside it mean nothing
        now, and are passed by."""
        recorded = dict(recorded or {})
        return cls(
            options=dict(options or {}),
            keep_inert=bool(recorded.get("keep_inert", False)),
        )


NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def laid_out(folder: Path) -> List[Tuple[str, int, str]]:
    """Every op and group of the mod at ``folder`` in file order, as written:
    which of the mod's files each is in (:attr:`SourceOp.file`), how deep in
    groups it sits, and its text.  A group is its own tag with nothing in
    it; what is in it follows, one deeper.  An ``<Include>`` line whose file
    is kept as a file is there by the file it names, however it spells it.

    Only parsed -- an ``<Include>`` followed, nothing read -- so it is what
    the mod's files say and none of what the reader makes of it, which is
    what an export read as written is held against.
    """
    _report, ops = read_source(Path(folder), "", Reading())
    return [
        (
            op.file, len(op.scopes),
            '<Include File="/%s"/>' % op.attrs[INCLUDES] if op.attrs.get(INCLUDES)
            else opening(op.verbatim) if op.scope else render_op(op),
        )
        for op in ops
    ]


def opening(group: str) -> str:
    """A group's own tag, as written, with nothing inside it."""
    try:
        node = etree.fromstring((group or "").strip().encode("utf-8"))
    except etree.XMLSyntaxError:
        return group
    for child in list(node):
        node.remove(child)
    node.text = None
    return etree.tostring(node, encoding="unicode", with_tail=False)
