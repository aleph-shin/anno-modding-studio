"""ModOps typed for one asset: kept as typed, and read for what they come to.

The XML view's pane for the mod being edited holds that mod's ModOps for one
asset (:mod:`.xmlview`).  What is typed there is what the mod holds from
then on: each op is kept as it was typed (:class:`~.changes.Op`), and read
by the reader a downloaded mod goes through (:mod:`.modops`), against the
asset as it stands when this mod's ops for it run, to find the changes it
comes to -- :func:`read`, then :func:`apply`.  Nothing is written another
way.

**What is typed is the whole of it.**  The pane shows every op of the mod
that is this asset's alone, so an op taken out of the text is an op taken
out of the mod, and an empty pane is a mod that leaves the asset alone.

**A new op goes where it was typed, among the ops the pane holds.**  The
pane holds the mod's ops that reach one asset, which is part of its file, so
a place typed there is a place between two of those and not a line of the
file: the new op goes straight after the one typed before it, or straight
before the one typed after it where it was typed first.  After the whole of
a group, where the op before it sits in one -- unless the op after it sits
in that group too, when it goes between them and the group is written round
it in two parts.  With nothing in the pane to go by, it goes after the mod's
other ops.

**Only what moved, moves.**  Which op is which is told by its text
(:func:`_store`).  One the text still has is left exactly as it is: its
place in the mod's file, its note, its switch -- and what it comes to.  It
is not read again (:attr:`Read.standing`): that was read where the mod
stands among the others, and read from one asset's pane an op that copies a
value from elsewhere, or is aimed at an asset the mod adds in a group, came
to nothing and was carried unread from the first Apply on.  An op typed over takes the
place of the one it was typed over.  Only a new op is placed anew, after the
mod's ops.  A change the Studio wrote is in the pane as the op it is written
as, and typed back as that op it is still that change.

**The asset can be typed too.**  The other pane holds the asset as the mod
leaves it, and typing there says what the asset should be and not how:
:func:`read_asset` works out the changes that come to, by reading a replace
of each node that differs for what it comes to, and they are the Studio's
own.  Where an op
somebody wrote reaches the asset they are laid over it, since an op cannot
be taken half away; where everything the mod does to the asset is the
Studio's own changes, the typed asset is the whole of them.

**An op that names this asset and no other, in no group, is the asset's.**
Carried because the reader could not follow it, it is still in the pane and
still typed over there.

**An op of the mod that reaches the asset and is not its own is in the pane
as well** (:func:`.xmlview.beyond_ops`): a search, an op naming several
assets, one in a group.  Left as it is in the text, it is left as it is in
the mod, and is not read: what it comes to was read where the mod stands,
for every asset it reaches, and nothing typed here knows more
(:attr:`Read.standing`).  Typed over, it is read and takes the place of the
one it was typed over: its place in the file, its group, its uid.  Taken
out of the text, it is taken out of the mod, for every asset it reached.
An op typed here that does not reach this asset is applied, and from then on
is a row of the mod's in the Mod changes panel and not in the pane.

**A search is typed on its own too.**  It is no one asset's, so the pattern
editor holds it, one op at a time: :func:`read_one` reads it as :func:`read`
does with no asset in front of it, and :func:`store_one` puts it in the mod,
in the place of the op it was opened from where there was one -- its group
too, and where that group does not run, coming to nothing.

**An op of a loaded mod taken out here stays out.**  Typed out of the pane or
typed over, it is still in the mod's file, and the loadout remembers that it
was taken out so that the mod read again does not bring it back
(:attr:`~.changes.Loadout.taken_out`).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from dataclasses import replace as _replace
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from . import (
    build,
    effective,
    keptops,
    loadercheck,
    modop,
    modops,
    reader,
    recording,
)
from . import paths as pathmod
from .changes import OP_REMOVE, Change, Loadout, Op, Unread
from .generator import SPAN_CHANGES
from .xmlview import (
    beyond_ops,
    beyond_unread,
    is_own,
    names_only,
    op_text,
    own_changes,
    own_ops,
    own_unread,
    run_text,
    unread_reaching,
)
from .xmlview import (
    laid_out as xmlview_laid_out,
)

# Where the ops are said to be from, in a reason that names a file.
WHERE = "the XML view"


@dataclass
class Read:
    """What some typed ModOps came to, with nothing applied yet."""

    # Not well-formed, or not ModOps: why, and where the reader stopped.
    error: str = ""
    line: int = 0
    column: int = 0
    # How many ops were typed, and how many of them the asset already
    # answers to, so that they come to nothing.
    ops: int = 0
    settled: int = 0
    changes: List[Change] = field(default_factory=list)
    # The ops kept as written and the ops carried unread, each of them the
    # asset's own -- it names this asset and no other -- or reaching beyond.
    own_ops: List[Op] = field(default_factory=list)
    own_unread: List[Unread] = field(default_factory=list)
    reaching: List[Op] = field(default_factory=list)
    unread: List[Unread] = field(default_factory=list)
    # What all those ops come to against the assets loaded now.
    answers: List[Change] = field(default_factory=list)
    # The ops of the mod in the pane that the text has as the mod has them,
    # the asset's own and the ones reaching beyond it, each with the note
    # the text gives it; and the ones carried unread.  Not read: they stay
    # what they are.
    standing: List[Tuple[Op, str]] = field(default_factory=list)
    standing_unread: List[Unread] = field(default_factory=list)
    # Where in the typed text each op is, counted from 0: for one that was
    # read, by the number the reading gave it; for one standing, by which
    # of the mod's it is (its ``id``).  What a new op is placed by.
    typed_at: Dict[int, int] = field(default_factory=dict)
    standing_at: Dict[int, int] = field(default_factory=dict)
    # Assets other than the one the pane is about that the ops change.
    elsewhere: List[str] = field(default_factory=list)
    # A typed asset that is XML, and still not something to apply: why, as
    # the reader said it or as one of the reasons below.
    refused: str = ""
    # And the part of it the reader's reason is about, as a ModOp path: the
    # list it could not match entry for entry, or the node handed to it.
    refused_at: str = ""
    # What the reader could not read field by field, in the order typed:
    # ``(ModOp path of the node it is about, its reason)``.
    coarse: List[Tuple[str, str]] = field(default_factory=list)
    # A typed asset read as what was typed over the asset as it stood, and
    # not as everything the mod does to it: ``changes`` are then to be laid
    # over the mod's own, and none of the mod's is to be taken away.
    over: bool = False
    # Typed ModOps read with every op kept as written: each is in
    # ``own_ops`` or ``reaching`` with what it comes to in ``answers``, and
    # none is in ``changes``.  See :func:`_store`.
    written: bool = False
    # A typed asset: how many entries it newly takes out that the game reads
    # as absent already (:class:`~.recording.Inert`).  Each is written as the
    # removal it is, and may change nothing in game.
    unnoticed: int = 0
    # Text was typed that is no op's and no field's: between two ops, or
    # between the fields of a node.  Nothing is read from it, so it is in
    # nothing the mod holds, and gone when the pane is written again.
    stray: bool = False
    # Typed ModOps that are XML, and still not applied: text stands inside
    # one of them beside the elements it holds (:func:`_loose`).  This is the
    # first such run of text, cut short, and ``line`` is where it starts.
    loose: str = ""


# Why a typed asset is refused before the reader sees it.
NOT_AN_ASSET = "not-an-asset"        # the outermost element is not <Asset>
ANOTHER_GUID = "another-guid"        # its GUID is not the asset's
OUTSIDE_VALUES = "outside-values"    # something beside <Values> was changed
NO_VALUES = "no-values"              # it has no <Values>
# And why what is typed for an op on its own is.
NOT_ONE_OP = "not-one-op"            # it is not one ModOp and nothing else


@dataclass
class Applied:
    """What putting a :class:`Read` into the mod did."""

    kept: int = 0
    retuned: int = 0
    added: int = 0
    dropped: int = 0
    # Ops kept as written that are in the pane now, and how many of them
    # were not there before; the same for ops carried unread.
    kept_written: int = 0
    kept_written_new: int = 0
    carried: int = 0
    carried_new: int = 0
    # Ops that went in kept as written, or carried, reaching beyond the asset.
    reaching: int = 0
    unread: int = 0
    # How many of ``reaching`` are a group's own tag and no ModOp: a group
    # typed with one op in it goes in as two, and is one ModOp to whoever
    # typed it.
    groups: int = 0
    settled: int = 0
    elsewhere: List[str] = field(default_factory=list)
    # Why each unread op was not read, as the reader said it.
    why_unread: List[str] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        """Whether the mod says anything it did not say before."""
        return bool(
            self.retuned or self.added or self.dropped or self.reaching or self.unread
            or self.kept_written_new or self.carried_new
        )


def _handed(workspace, guid: str, origin: str, own: List[Change], with_kept: bool = False,
            made_here: bool = False, leaving=()):
    """``guid`` as the ops being typed find it, or None where they make it.

    The asset as the mods before ``origin`` leave it, with what ``origin``
    does to it by its kept ops: an op typed over a kept op's answer is the
    exception to that op, and is read against what the op left.

    ``with_kept`` leaves in what the mod's ops marked to be kept as written
    do as well.  Typed ModOps are read without it, since those ops are among
    them; a typed asset is read with it, since all it can say is what the
    mod's narrow changes come to on top of everything else.

    ``made_here`` is for an asset ``origin`` itself adds, read with the op
    that adds it left out of ``own``: the asset as that op makes it, with the
    rest, and not None.

    ``leaving`` is the uids of other ops of the mod's to leave out: ones
    that reach beyond the asset and were typed over or out, whose answers
    what is typed in their place is not read on top of.
    """
    loadout = workspace.loadout
    # Whether the mod makes an asset under the GUID itself, the game's
    # finding that one or not: then it is handed nothing, or that one.
    creator = workspace.creator_for(guid, origin)
    if creator is not None and not made_here:
        return None
    base = effective.parse_payload(creator) if creator is not None else workspace.base_element(guid)
    if base is None:
        return None
    rank = loadout.layer_rank(origin)
    kept = set() if with_kept else {
        op.uid for op in own_ops(loadout, guid, origin)
    }
    kept |= set(leaving)
    mine = {change.key for change in own} | {
        change.key for change in workspace.changes_for(guid)
        if change.origin == origin and change.op_uid in kept
    }
    earlier = [
        change for change in workspace.changes_for(guid)
        if loadout.layer_rank(change.origin) < rank
        or (change.origin == origin and change.key not in mine)
    ]
    if not earlier:
        return workspace.base_asset_element(guid)
    return effective.build(
        base, earlier, guid, is_new=creator is not None or workspace.is_new_asset(guid),
        game=loadout.game,
    ).as_the_game_reads_it()


def _parsed(text: str):
    """``(root, error, line, column)`` for typed text: a ``<ModOps>``
    document, or ops with nothing around them.

    The place is counted in the text as it was typed, as the pane counts
    its lines: the reader sees it without the blank lines before it, and
    with a line put before it where the ops had no document around them."""
    lead = _lines_before(text)
    text = (text or "").strip()
    if not text:
        return etree.Element("ModOps"), "", 0, 0
    offset = 0
    if "<ModOps" not in text:
        # Ops with no document around them: the same thing, less typing.
        text = "<ModOps>\n%s\n</ModOps>" % text
        offset = 1
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        return (None, *_not_xml(exc, lead - offset))
    if root.tag != "ModOps":
        return None, "the outermost element is <%s>, not <ModOps>" % root.tag, 1 + lead, 1
    return root, "", 0, 0


def _lines_before(text: str) -> int:
    """How many lines of ``text`` come before anything is written in it."""
    text = text or ""
    return text[:len(text) - len(text.lstrip())].count("\n")


def _not_xml(exc, shift: int):
    """``(error, line, column)`` for text the reader stopped in, the lines
    counted ``shift`` further down than the reader counted them."""
    line, column = exc.position
    # lxml ends its message with the place; it is said once, by the caller.
    said = re.sub(r",? line \d+, column \d+$", "", exc.msg or str(exc))
    # It can name another line inside the message, where the tag left open
    # is: that one is the reader's count as well.
    said = re.sub(
        r"\bline (\d+)\b",
        lambda found: "line %d" % max(1, int(found.group(1)) + shift),
        said,
    )
    return said, max(1, line + shift), column


def read(workspace, guid: str, origin: str, text: str) -> Read:
    """Read ``text`` as ModOps of ``origin``, typed for ``guid``.

    Nothing is applied.  The reading is set up as an import of the mod is:
    at the settings the mod was read at, with every loaded mod named as
    there, and searches answered by the game's files.
    """
    guid = str(guid)
    loadout = workspace.loadout
    root, stopped = as_typed(loadout, origin, text)
    if stopped is not None:
        return stopped
    own = own_changes(workspace, guid, origin)
    ops = _typed_ops(loadout, origin, root)
    in_pane = own_ops(loadout, guid, origin) + beyond_ops(workspace, guid, origin)
    ops, standing, standing_unread, standing_at, read_at = _less_standing(
        ops, in_pane,
        own_unread(loadout, guid, origin) + beyond_unread(workspace, guid, origin),
    )
    still = {id(held) for held, _note in standing}
    # What is read is read against the asset with the ops that stand as
    # they were, and without the ones typed over or out.
    leaving = [op.uid for op in in_pane if id(op) not in still]
    # Where the mod makes the asset with an op that is not being read here
    # -- standing as it was, or not in the pane at all -- what is typed is
    # read against the asset as that op makes it.
    creator = workspace.creator_for(guid, origin)
    made_here = (
        creator is not None and bool(creator.op_uid)
        and creator.op_uid not in leaving
    )
    handed = _handed(
        workspace, guid, origin, own, with_kept=True, leaving=leaving, made_here=made_here
    )
    found = _read(workspace, guid, origin, root, handed, ops=ops)
    found.ops += len(standing) + len(standing_unread)
    found.standing, found.standing_unread = standing, standing_unread
    found.standing_at = standing_at
    # The reading numbers each op it comes to, and an op it did not come to
    # keeps the -1 it was handed with.
    found.typed_at = {op.order: at for op, at in zip(ops, read_at, strict=True) if op.order >= 0}
    found.stray = between_ops(root)
    return found


def _typed_ops(loadout: Loadout, origin: str, root) -> List[modops.SourceOp]:
    ops = modops.parse_ops(root, [], WHERE, game=loadout.game)
    for op in ops:
        op.mod = origin
        # Until the reader numbers it.
        op.order = -1
    return ops


def _less_standing(ops, held: List[Op], carried: List[Unread]):
    """``(to read, standing, standing unread, where each standing one is,
    where each to read is)``: the typed ``ops`` less each that is one of
    the mod's ops in the pane, ``held`` or ``carried`` unread, as the mod
    has it.  Each of those is matched once, and only by an op typed in no
    group: one typed into a group is another op than the one that was not.
    Where is its place in the typed text, counted from 0: by ``id`` for a
    standing one, and in a list beside the ones to read.
    """
    left, left_unread = list(held), list(carried)
    to_read: list = []
    read_at: List[int] = []
    standing: List[Tuple[Op, str]] = []
    standing_unread: List[Unread] = []
    standing_at: Dict[int, int] = {}
    for at, op in enumerate(ops):
        if op.scope or op.scopes or op.shelved:
            to_read.append(op)
            read_at.append(at)
            continue
        said = op.verbatim or modops.render_op(op)
        same = next((one for one in left if keptops.same_op(one.source, said)), None)
        if same is not None:
            left.remove(same)
            standing.append((same, op.comment or ""))
            standing_at[id(same)] = at
            continue
        entry = next((one for one in left_unread if keptops.same_op(one.source, said)), None)
        if entry is not None:
            left_unread.remove(entry)
            standing_unread.append(entry)
            standing_at[id(entry)] = at
            continue
        to_read.append(op)
        read_at.append(at)
    return to_read, standing, standing_unread, standing_at, read_at


def as_typed(loadout: Loadout, origin: str, text: str):
    """``(root, None)`` for typed ModOps there is something to read from, or
    ``(None, why not)``: not XML, or with text standing loose in an op."""
    root, error, line, column = _parsed(text)
    if root is None:
        return None, Read(error=error, line=line, column=column)
    loose = _loose(root, loadout, origin)
    if loose:
        node, said, after = loose[0]
        return None, Read(
            loose=_shortened(said), line=_line_of(node, said, after) + line_shift(text)
        )
    return root, None


def line_shift(text: str) -> int:
    """How many lines further down a line of typed ModOps is in the text as
    it was typed than the reader counted it: the reader saw it without the
    blank lines before it, and with a line put before it where the ops had
    no document around them."""
    return _lines_before(text) - (0 if "<ModOps" in (text or "") else 1)


def not_xml(text: str) -> Optional[Read]:
    """Why ``text`` is not ModOps to read, with where the reader stopped, or
    None for text that is: asked of what is typed as it is typed, since it
    costs no look at the game's files."""
    root, error, line, column = _parsed(text)
    return Read(error=error, line=line, column=column) if root is None else None


def in_idle_group(loadout: Loadout, op: Optional[Op]) -> bool:
    """Whether ``op`` sits in a group that does not run as things stand, so
    that it comes to nothing today whatever it says."""
    if op is None:
        return False
    groups = (loadout.op(uid) for uid in op.within)
    return any(group is not None and group.idle for group in groups)


def read_one(workspace, origin: str, text: str) -> Read:
    """Read ``text`` as one ModOp of ``origin`` that is no one asset's.

    :func:`read` with no asset in front of it, so the op is in ``reaching``
    with what it comes to in ``answers``, or in ``unread``.  Its search is
    answered by the game's files and by the assets the loaded mods add, which
    an op that goes out as a search covers too.  Anything but one ModOp is
    refused (``NOT_ONE_OP``): a group, several ops, an asset written bare.
    ``ops`` then says how many elements were typed, and ``refused_at`` the
    name of the one there is.
    """
    root, stopped = as_typed(workspace.loadout, origin, text)
    if stopped is not None:
        return stopped
    typed = _elements(root)
    if len(typed) != 1 or typed[0].tag != "ModOp":
        alone = typed[0].tag if len(typed) == 1 else ""
        return Read(refused=NOT_ONE_OP, ops=len(typed), refused_at=alone)
    found = _read(workspace, "", origin, root, None, invented=workspace.invented_assets())
    found.reaching = found.own_ops + found.reaching
    found.unread = found.own_unread + found.unread
    found.own_ops, found.own_unread = [], []
    found.stray = between_ops(root)
    return found


def store_one(workspace, origin: str, found: Read, over: Optional[Op] = None) -> Op:
    """Put the op :func:`read_one` read into the mod, with what it comes to.

    ``over`` is the op it was opened from: the new one takes its place in
    the mod's file and in the groups that one sits in, its switch and its
    name here, so what was said of that one -- an exception to it, a row
    left folded -- is said of this.  With none it goes after the mod's ops.
    The caller records the step and redraws.

    It was read on its own, as though it ran.  In a group that does not run
    as things stand it does not, so it goes in as the reader leaves any op of
    such a group: the op it is, coming to nothing today.
    """
    loadout: Loadout = workspace.loadout
    record = found.reaching[0]
    answers = [change for change in found.answers if change.op_uid == record.uid]
    if over is not None:
        if in_idle_group(loadout, over):
            answers, record.matched = [], []
        record.uid = over.uid
        record.order = over.order
        record.within = list(over.within)
        record.active = over.active
        record.note = record.note or over.note
        loadout.drop_op(over.uid)
    else:
        record.order = loadout.after_last_op(origin)
    # Typed here: the user's, with what it comes to.
    record.authored = True
    loadout.set_op(record)
    for answer in answers:
        answer.op_uid = record.uid
        answer.order = record.order
        answer.active = record.active
        answer.authored = True
        loadout.set_change(answer)
    return record


def _text_between(node) -> List[str]:
    """The text ``node`` has of its own around what is in it."""
    said = [node.text] + [child.tail for child in node]
    return [text.strip() for text in said if text and text.strip()]


def between_ops(node) -> bool:
    """Whether text stands between the ops in ``node``, or between those of
    a group in it.  What is inside a ModOp is another matter: :func:`_loose`."""
    return bool(_text_between(node)) or any(
        between_ops(child) for child in node
        if isinstance(child.tag, str) and child.tag.lower() == "group"
    )


def _is_mark(node) -> bool:
    """Whether ``node`` stands for a value worked out as the op is run: text
    beside one is part of that value."""
    return node.tag in (modop.MOD_OP_CONTENT, modop.MOD_VALUE)


def _text_runs(node, inside: bool) -> list:
    """The text standing loose in ``node``, a ModOp or an element one holds,
    as ``(node, the text, whether it comes after that node)``: text before a
    first child is given with the node it is in.

    Text is a value where it is all an element holds, or where it stands
    beside nothing but marks.  Beside elements it is nothing: the reader makes
    no change of it.  And directly inside the ModOp what follows a child, a
    comment or a mark too, is given whatever it stands beside: an op typed
    here is held to less than one read from a mod's files, which is carried
    with such text in it (:func:`_loose`).

    But for an op that holds nothing but ``<ModOpContent/>``: the text around
    a copy marked straight inside its op is run together with the copy
    (:func:`~.reader._copied_as_text`), so it is the op's value on either
    side, and what the reader cannot make of such an op it says of the op.
    """
    found = []
    elements = _elements(node)
    mixed = any(not _is_mark(child) for child in elements)
    copies = bool(elements) and all(child.tag == modop.MOD_OP_CONTENT for child in elements)
    if mixed and node.text and node.text.strip():
        found.append((node, node.text, False))
    for child in node:
        if (mixed or not (inside or copies)) and child.tail and child.tail.strip():
            found.append((child, child.tail, True))
    for child in _elements(node):
        found.extend(_text_runs(child, True))
    return found


def _loose(root, loadout: Loadout, origin: str) -> list:
    """The loose text in the ModOps typed under ``root`` (:func:`_text_runs`).

    Less what the mod holds already.  An op read from a mod's files is
    carried as written, loose text in an element of it and all, and is in
    the pane like that: it is not for whoever types beside it to clear up
    before anything can be applied.
    """
    found = [(op, _text_runs(op, False)) for op in root.iter("ModOp")]
    found = [(op, runs) for op, runs in found if runs]
    if not found:
        return []
    held = set()
    sources = [op.source for op in loadout.ops if op.origin == origin]
    sources += [entry.source for entry in loadout.unread if entry.origin == origin]
    for source in sources:
        try:
            op = etree.fromstring((source or "").encode("utf-8"))
        except etree.XMLSyntaxError:
            continue
        runs = _text_runs(op, False) if op.tag == "ModOp" else []
        if runs:
            held.add(_with_runs(op, runs))
    return [
        run for op, runs in found if _with_runs(op, runs) not in held for run in runs
    ]


def _with_runs(op, runs: list) -> tuple:
    """An op and its loose text, as two spellings of one op agree on them."""
    source = etree.tostring(op, encoding="unicode", with_tail=False)
    return keptops.canonical_op(source), tuple(text.strip() for _node, text, _after in runs)


def _line_of(node, said: str, after: bool) -> int:
    """The line a run of loose text starts on, in the text the reader saw."""
    line = node.sourceline or 1
    if after:
        # As many lines on as the node before it is written over.
        line += etree.tostring(node, encoding="unicode", with_tail=False).count("\n")
    return line + said[:len(said) - len(said.lstrip())].count("\n")


def _shortened(text: str, most: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[:most - 1] + "\u2026"


def _between_fields(asset) -> set:
    """The text standing between the fields of the nodes of ``asset``: a
    node with fields in it has no value of its own."""
    return {
        text for node in asset.iter() if isinstance(node.tag, str) and _elements(node)
        for text in _text_between(node)
    }


def read_as(workspace, origin: str, ops, view, keep_inert: Optional[bool] = None,
            invented=()):
    """The reader's report of ``ops`` read as ``origin``'s, against ``view``
    -- the game's files, or anything answering as they do.

    Set up as an import of the mod is: at the settings the mod was read at,
    with every loaded mod named as there.  ``keep_inert`` and ``invented``
    are :func:`_read`'s.  ``ops`` in the report are ``origin``'s alone.
    """
    if invented:
        view = keptops.WithAssets(view, dict(invented))
    asked = keptops.reading_of(workspace.loadout, origin)
    if keep_inert is not None:
        asked["keep_inert"] = keep_inert
    report = reader.read_ops(
        ops, view, search=keptops.searching(view, ops, invented), **asked
    )
    report.ops = [record for record in report.ops if record.origin == origin]
    return report


def _read(workspace, guid: str, origin: str, root, handed,
          keep_inert: Optional[bool] = None, invented=(), ops=None) -> Read:
    """The ops under ``root`` read as ``origin``'s, with ``guid`` as
    ``handed`` -- or as the game's files have it, for None.  ``ops`` is
    those ops already read out of it, where some are not to be read.

    ``keep_inert`` says whether a removal the game would not notice is
    written down (:class:`~.recording.Inert`), whatever the mod was read with.
    ``invented`` is assets the game's files do not have, as ``(guid,
    element)``: a search is answered by them as well.
    """
    loadout = workspace.loadout
    if ops is None:
        ops = _typed_ops(loadout, origin, root)
    found = Read(ops=len(ops), written=True)
    if not ops:
        return found

    index = workspace.index
    view = keptops.WithAssets(index, {guid: handed}) if handed is not None else index
    report = read_as(workspace, origin, ops, view, keep_inert, invented)
    # A kept op's answers are the op's, and go in with it: only the rest is
    # matched against what the mod already says.
    answering = {record.uid for record in report.ops}
    found.answers = [change for change in report.changes if change.op_uid in answering]
    found.changes = [change for change in report.changes if change.op_uid not in answering]
    for record in report.ops:
        mine = is_own(record, guid, loadout.game)
        (found.own_ops if mine else found.reaching).append(record)
    for entry in report.unread(origin):
        (found.own_unread if names_only(entry.source, guid, loadout.game)
         else found.unread).append(entry)
    # An op kept as written is not one that came to nothing, even where the
    # asset already reads its way.
    kept_ops = {keptops.canonical_op(record.source) for record in report.ops}
    found.settled = sum(
        1 for result in report.results
        if result.status == modops.SETTLED
        and keptops.canonical_op(modops.render_op(result.op)) not in kept_ops
    )
    found.coarse = [
        (result.coarse_at or result.op.path, result.coarse)
        for result in report.results if result.coarse
    ]
    found.elsewhere = sorted({
        change.guid for change in found.changes
        if change.guid != guid and not change.creates_asset
    })
    return found


# ------------------------------------------------------------ a typed asset
def _same_tree(one, other) -> bool:
    return loadercheck.canonical(one) == loadercheck.canonical(other)


def _elements(node) -> list:
    return [child for child in node if isinstance(child.tag, str)]


def _differing(have, typed, out: list) -> None:
    """The smallest nodes at which ``typed`` parts from ``have``, as
    ``(node of have, node of typed)`` pairs added to ``out``.

    Only what differs is handed to the reader.  An asset is mostly lists
    padded with empty entries nothing tells apart, and a replace of the whole
    asset asks the reader to match every one of them: it cannot, and says the
    whole asset would have to be swapped -- for a change to one number
    somewhere else.

    A node whose fields are each there once is gone into, field by field.  A
    list -- a node with a name repeated among its children -- is handed over
    whole as soon as one entry differs, since which entry is which is the
    reader's to decide, by key.  So is a node that gained or lost a field.
    """
    if _same_tree(have, typed):
        return
    mine, theirs = _elements(have), _elements(typed)
    names = [child.tag for child in mine]
    if mine and names == [child.tag for child in theirs] and len(set(names)) == len(names):
        for one, other in zip(mine, theirs, strict=True):
            _differing(one, other, out)
        return
    out.append((have, typed))


def same_xml(one: str, other: str) -> bool:
    """Whether two pieces of XML are one tree, whatever their layout."""
    try:
        return _same_tree(
            etree.fromstring((one or "").strip().encode("utf-8")),
            etree.fromstring((other or "").strip().encode("utf-8")),
        )
    except etree.XMLSyntaxError:
        return False


def read_asset(workspace, guid: str, origin: str, text: str,
               whole: Optional[bool] = None) -> Read:
    """Read ``text`` as ``guid`` the way ``origin`` should leave it.

    Nothing is applied.  What comes back is narrow changes: the reading of a
    replace of each node that differs, which is what somebody who pastes an
    asset and changes it inline has written, less everything they left alone
    (:func:`_differing`).  So it decides what that reading decides: a list
    entry is matched by its key and not its place, and a change with no
    narrow form is refused with the reader's reason.

    In one thing it does not read as a mod's wide replace is read.  That
    replace leaves out the placeholder entries its author did not retype,
    and those are not taken for removals (:class:`~.recording.Inert`).  Here
    every entry was on show, so one that is gone was taken out by hand: it
    is a removal somebody asked for, written like the one *Remove from
    asset* writes in the Fields view, and counted in ``unnoticed`` so that
    it can be said the game may not notice.

    Differs from what, is the question, and there are two answers.

    *From the asset as the mod's other ops leave it* (``whole``): the changes
    are then everything the mod's narrow ops should come to, and are matched
    against the ones it has, so that a value typed back to what it was takes
    its change away and an entry the mod added and you took out is an
    addition gone.  That needs the asset as shown to come back as the
    changes the mod has, which it does where they are plain -- and does not
    for about half the assets of mods read from disk, whose changes were
    read from ops no reading of the asset arrives at again.

    *From the asset as it is shown* (``over``, set on what comes back): only
    what was typed, as an edit in the tree is, laid over the mod's own
    changes.  It always holds, and it takes none of the mod's away -- so an
    entry the mod added and you took out is its addition, then a removal.

    ``whole`` left unsaid picks the first where it holds -- and, read with
    every op kept as written, only where everything the mod does to the
    asset is the Studio's own changes.  An op somebody wrote cannot be taken
    half away, so where one reaches the asset what is typed is laid over it.

    The mod's ops that are not narrow changes -- its patterns and the other
    ops it keeps as written, the ones it carries unread -- are not the asset
    pane's to change, and come back as they are.

    An asset ``origin`` itself adds has no ops to work out: the typed asset
    is the one it adds.
    """
    guid = str(guid)
    loadout = workspace.loadout
    try:
        typed = etree.fromstring((text or "").strip().encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        said, line, column = _not_xml(exc, _lines_before(text))
        return Read(error=said, line=line, column=column)

    own = own_changes(workspace, guid, origin)
    found = Read(
        ops=1,
        own_ops=own_ops(loadout, guid, origin),
        own_unread=own_unread(loadout, guid, origin),
    )
    if typed.tag != "Asset":
        found.refused = NOT_AN_ASSET
        return found
    if (typed.findtext("Values/Standard/GUID") or "").strip() != guid:
        found.refused = ANOTHER_GUID
        return found

    now = workspace.effective_asset(guid, upto=origin).as_the_game_reads_it()
    creator = workspace.creator_for(guid, origin)
    adds_it = creator is not None
    # The op that adds it is the pane's only where it is a change of the
    # mod's own. Added by a pattern, or by any op kept as written, it is that
    # op's, and part of what the asset is read against like the rest.
    if adds_it and creator.key in {change.key for change in own}:
        own = [change for change in own if change.key != creator.key]
        after = [
            change for change in workspace.changes_for(guid)
            if change.origin == origin and change.key != creator.key
        ]
        adds = effective.payload_elements(creator)
        single = len(adds) == 1 and adds[0].tag == "Asset"
        if not after and single:
            if _same_tree(typed, now):
                # Nothing typed: the op that adds it is as it was.
                found.changes = [creator]
                return found
            if _same_tree(adds[0], now):
                # Nothing but the op that adds it, shown as that op writes
                # it: the typed asset is the asset the op adds, template
                # and all.
                found.changes = [
                    _replace(creator, payload=etree.tostring(typed, encoding="unicode"))
                ]
                return found
        # The op that adds it stays as it is, and what is typed is read as
        # changes after it. Where the mod changes the asset after adding it,
        # by narrow changes or by a kept op, typed into the op what those do
        # would be in it twice.
        found.changes = [creator]
        if not after or not single:
            # Not shown as the op writes it, or the op adds more than this
            # asset: only what was typed, laid over.
            whole = False

    if whole is None and _an_op_reaches(workspace, guid, origin, now):
        whole = False
    if whole is None:
        # As a whole wherever the asset, untouched, comes back as the mod's
        # own changes.
        whole = asset_reads_back(workspace, guid, origin, xmlview_laid_out(now))
    values = typed.find("Values")
    if values is None:
        found.refused = NO_VALUES
        return found
    beside = [child for child in typed if isinstance(child.tag, str) and child is not values]
    was = [child for child in now if isinstance(child.tag, str) and child.tag != "Values"]
    if len(beside) != len(was) or not all(_same_tree(a, b) for a, b in zip(beside, was, strict=True)):
        found.refused = OUTSIDE_VALUES
        return found

    # A replace of each node that differs, for the reader to narrow: from
    # the asset as the mod's other ops leave it, or from the asset as shown.
    if whole:
        handed = _handed(workspace, guid, origin, own, with_kept=True, made_here=adds_it)
    else:
        handed = now
        found.over = True
        found.changes = []
    there = handed.find("Values") if handed is not None else None
    if there is None:
        found.refused = NO_VALUES
        return found
    parted: list = []
    _differing(there, values, parted)
    # From here on what is typed is read for its changes, and no change is
    # read from text between a node's fields.  (An asset the mod adds, typed
    # as the op that adds it, went back above with that text in it.)  Text
    # the asset as shown had there already is not what was typed.
    found.stray = bool(_between_fields(typed) - _between_fields(now))
    root = etree.Element("ModOps")
    for have, wanted in parted:
        op = etree.SubElement(
            root, "ModOp", Type="replace", GUID=guid,
            Path=pathmod.path_for(handed, have).path,
        )
        op.append(copy.deepcopy(wanted))
    found.ops = len(parted)
    if not parted:
        return found
    read_as = _read(workspace, guid, origin, root, handed, keep_inert=True)
    if read_as.coarse:
        # No field-by-field form: what is shown would swap the whole node,
        # and a wide replace of an asset is the thing this exists to avoid.
        found.refused_at, found.refused = read_as.coarse[0]
        return found
    if read_as.unread or read_as.own_unread:
        entry = (read_as.unread + read_as.own_unread)[0]
        aimed = modops.read_op(entry.source, game=loadout.game)
        found.refused_at, found.refused = (aimed.path if aimed is not None else ""), entry.why
        return found
    short = [record for record in read_as.own_ops + read_as.reaching if record.caveat]
    if short:
        # The changes, applied, do not come to the asset typed.
        found.refused_at, found.refused = short[0].expression, short[0].caveat
        return found
    # Each replace is read as the op it is, and what it comes to is the
    # typed asset's changes: the Studio's own, under no op.
    read_as.changes = [_replace(change, op_uid="") for change in read_as.answers]
    found.changes = found.changes + read_as.changes
    found.elsewhere = sorted({
        change.guid for change in read_as.changes
        if change.guid != guid and not change.creates_asset
    })
    found.unnoticed = _unnoticed(workspace, handed, read_as.changes, own)
    return found


def _unnoticed(workspace, handed, changes: List[Change], own: List[Change]) -> int:
    """How many of ``changes`` take an entry out of ``handed`` that the game
    reads as absent already, and are not removals the mod has among ``own``."""
    inert = recording.Inert(workspace.index)
    held = {_shape(change) for change in own}
    found = 0
    for change in changes:
        if change.op != OP_REMOVE or _shape(change) in held:
            continue
        nodes = pathmod.resolve(handed, change.path)
        if len(nodes) == 1 and inert(nodes[0]):
            found += 1
    return found


def line_typed(text: str, path: str) -> int:
    """The line of ``text``, a typed asset, that the node at ModOp ``path``
    starts on, counted from 1; 0 where the path finds none in it."""
    try:
        root = etree.fromstring((text or "").encode("utf-8"))
    except etree.XMLSyntaxError:
        return 0
    nodes = pathmod.resolve(root, path) if path else []
    return (nodes[0].sourceline or 0) if nodes else 0


def _an_op_reaches(workspace, guid: str, origin: str, now) -> bool:
    """Whether an op ``origin`` has as written does something to ``guid``, or
    one it carries unread is aimed at it."""
    loadout = workspace.loadout
    if any(
        change.origin == origin and change.op_uid for change in workspace.changes_for(guid)
    ):
        return True
    return bool(unread_reaching(loadout, guid, now, [origin]))


def _moves(plan) -> bool:
    return bool(plan.retuned or plan.added or plan.dropped)


def asset_reads_back(workspace, guid: str, origin: str, text: str) -> bool:
    """Whether ``text`` -- the asset as ``origin`` leaves it, as the Studio
    wrote it -- reads as exactly the narrow changes ``origin`` has for it:
    whether a typed asset can be read as a whole."""
    found = read_asset(workspace, guid, origin, text, whole=True)
    if found.error or found.refused or found.elsewhere:
        return False
    return not _moves(_plan(own_changes(workspace, guid, origin), found.changes))


def can_type_asset(workspace, guid: str, origin: str) -> bool:
    """Whether ``guid`` as ``origin`` leaves it is an asset to type over:
    there, and known by the GUID it is asked for by."""
    now = workspace.effective_asset(guid, upto=origin).as_the_game_reads_it()
    return now is not None and (now.findtext("Values/Standard/GUID") or "").strip() == str(guid)


def lay_over(workspace, guid: str, origin: str, found: Read) -> Applied:
    """Put changes read *over* the asset as shown into ``origin``, as edits
    made in the tree are: each after the mod's own ops, and one aimed where
    a change of the mod's is aimed is that change retuned.  None is taken
    away."""
    loadout: Loadout = workspace.loadout
    done = Applied(settled=found.settled, elsewhere=list(found.elsewhere))
    for change in found.changes:
        prior = loadout.changes.get(change.key)
        change.authored = True
        if (prior is not None and prior.origin == origin and not prior.op_uid
                and change.sets is not None and prior.sets is not None):
            # The value before is the one the mod's own change replaced, so
            # that typing it back takes the change away.
            change.old_value = prior.old_value
            change.note = prior.note
            done.retuned += 1
        else:
            done.added += 1
        loadout.set_change(loadout.place(change))
    return done


def read_asset_and_apply(
    workspace, guid: str, origin: str, text: str,
) -> Tuple[Read, Optional[Applied]]:
    """:func:`read_asset` ``text``, and put it into the mod where it read."""
    found = read_asset(workspace, guid, origin, text)
    if found.error or found.refused:
        return found, None
    if found.over:
        return found, lay_over(workspace, guid, origin, found)
    return found, apply(workspace, guid, origin, found, notes=False)


# ------------------------------------------------------ what moved, and no more
def _shape(change: Change) -> Tuple:
    """What a change is written as: two changes of one shape are one op."""
    return (
        change.guid, change.path, change.op, change.tag, change.new_value,
        loadercheck.canonical_fragment(change.payload or ""),
    )


def _slot(change: Change) -> Tuple:
    """Where a change is aimed, whatever it puts there."""
    return (change.guid, change.path, change.op, change.tag)


@dataclass
class _Plan:
    # (the mod's change, the op typed for it), where they are one op.
    kept: List[Tuple[Change, Change]] = field(default_factory=list)
    retuned: List[Tuple[Change, Change]] = field(default_factory=list)
    added: List[Change] = field(default_factory=list)
    dropped: List[Change] = field(default_factory=list)


def _plan(own: List[Change], typed: List[Change]) -> _Plan:
    """Match what was typed to what the mod has: the same op is kept, an op
    aimed where one was aimed is that one retuned, and the rest is new or
    gone."""
    plan = _Plan()
    waiting: Dict[Tuple, List[Change]] = {}
    for change in own:
        waiting.setdefault(_shape(change), []).append(change)
    unmatched = []
    for change in typed:
        same = waiting.get(_shape(change))
        if same:
            plan.kept.append((same.pop(0), change))
        else:
            unmatched.append(change)
    left: Dict[Tuple, List[Change]] = {}
    for held in waiting.values():
        for change in held:
            left.setdefault(_slot(change), []).append(change)
    for change in unmatched:
        there = left.get(_slot(change))
        if there:
            plan.retuned.append((there.pop(0), change))
        else:
            plan.added.append(change)
    plan.dropped = [change for held in left.values() for change in held]
    return plan


def _paired(have, typed, same) -> Tuple[list, list, list]:
    """``(kept, new, gone)``: each of ``typed`` matched to one of ``have`` it
    is the ``same`` as, in order."""
    left = list(have)
    kept, new = [], []
    for one in typed:
        match = next((held for held in left if same(held, one)), None)
        if match is None:
            new.append(one)
        else:
            left.remove(match)
            kept.append(match)
    return kept, new, left


def _pairs(have, typed, same) -> Tuple[list, list, list]:
    """As :func:`_paired`, with each match as ``(held, typed)``."""
    left = list(have)
    pairs, new = [], []
    for one in typed:
        match = next((held for held in left if same(held, one)), None)
        if match is None:
            new.append(one)
        else:
            left.remove(match)
            pairs.append((match, one))
    return pairs, new, left


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _same_source(one, other) -> bool:
    return keptops.same_op(one.source, other.source)


def _aim(source: str) -> Tuple:
    """Where an op as written is aimed and how, whatever it puts there."""
    op = modops.read_op(source)
    return (op.kind, tuple(op.guids), op.path) if op is not None else (source,)


def apply(
    workspace, guid: str, origin: str, found: Read, was: Optional[Read] = None,
    notes: bool = True,
) -> Applied:
    """Make ``origin`` say about ``guid`` what ``found`` read, moving only
    what differs.  The caller records the step and redraws.

    ``was`` is the reading of the text the typing started from.  It is what
    says a note was typed over where the op under it was not: a note is
    compared with the note that text read as, since one the Studio wrote out
    does not always read back letter for letter as the one it holds.

    ``notes`` off leaves every note as it is: a typed asset has nowhere to
    say one, so a change it retunes keeps the note it had.
    """
    if found.written:
        return _store(workspace, guid, origin, found, was)
    loadout: Loadout = workspace.loadout
    plan = _plan(own_changes(workspace, guid, origin), found.changes)
    done = Applied(settled=found.settled, elsewhere=list(found.elsewhere))

    # The ops kept as written and carried unread that are the asset's own:
    # the ones the text still has stay as they are, the ones it has not go --
    # first, so that a narrow op typed where a kept one was takes its place.
    had_ops, new_ops, gone_ops = _paired(
        own_ops(loadout, guid, origin), found.own_ops, _same_source)
    had_unread, new_unread, gone_unread = _paired(
        own_unread(loadout, guid, origin), found.own_unread, _same_source)
    for op in gone_ops:
        loadout.drop_op(op.uid)
    loadout.drop_unread(gone_unread)
    done.kept_written = len(had_ops) + len(new_ops)
    done.kept_written_new = len(new_ops) + len(gone_ops)
    done.carried = len(had_unread) + len(new_unread)
    done.carried_new = len(new_unread) + len(gone_unread)
    done.why_unread = [entry.why for entry in new_unread + found.unread]

    written = set()
    # And the new ones go in before the narrow ops, with what they come to:
    # a narrow op typed over a kept op's answer is then the exception to it.
    for op in new_ops + found.reaching:
        op.order = loadout.after_last_op(origin)
        loadout.set_op(op)
        for answer in found.answers:
            if answer.op_uid == op.uid:
                answer.order = op.order
                loadout.set_change(answer)
                written.add(answer.key)
    done.reaching = len(found.reaching)
    done.groups = sum(1 for op in found.reaching if op.scope)
    read_as: Dict[Tuple, List[str]] = {}
    for change in (was.changes if was is not None else ()):
        read_as.setdefault(_shape(change), []).append(change.note)
    for old, new in plan.kept:
        before = read_as.get(_shape(new))
        if was is not None and before and new.note != before.pop(0):
            # The same op under another note. Set on the change itself: a
            # note is no part of what the workspace knows a change by, and a
            # copy put in its place would leave the old one being shown.
            old.note = new.note
            done.retuned += 1
        else:
            done.kept += 1
    for old, new in plan.retuned:
        # The same op saying something else: it keeps its place and what it
        # is tied to, and is the user's from here on.
        loadout.set_change(_replace(
            old, new_value=new.new_value, payload=new.payload,
            summary=new.summary, note=new.note if notes else old.note, authored=True,
        ))
        written.add(old.key)
        done.retuned += 1
    for change in plan.added:
        change.authored = True
        placed = loadout.place(change)
        loadout.set_change(placed)
        written.add(placed.key)
        done.added += 1
    for change in plan.dropped:
        # Not where something typed has just gone under the same key: a
        # change is known by where it is aimed, and that one is the new op.
        if change.key not in written:
            loadout.remove_key(change.key)
        done.dropped += 1

    for entry in new_unread + found.unread:
        entry.order = loadout.after_last_op(origin)
        loadout.unread.append(entry)
    done.unread = len(found.unread)
    return done


def _store(workspace, guid: str, origin: str, found: Read, was: Optional[Read]) -> Applied:
    """:func:`apply` for ModOps read with every op kept as written: what was
    typed is what the mod holds, and nothing is written another way.

    Which op is which is told by its text.  One the text still has stays as
    it is: its place, its note, its switch, its uid.  That goes for a change
    the Studio wrote as well, which the pane shows as the op it is written
    as: typed back as that op, it is still that change.  An op typed over --
    aimed where one that is gone was aimed -- takes that one's place.  The
    rest is new, placed after the mod's ops, or gone.

    The same goes for the mod's ops that reach beyond the asset, which the
    pane holds too: one the text has as it was is not touched
    (:attr:`Read.standing`), one typed over is the op it was in the place
    and the group it had, and one the text has not is taken out of the mod.
    """
    guid = str(guid)
    loadout: Loadout = workspace.loadout
    done = Applied(elsewhere=list(found.elsewhere))
    # Before anything moves: which they are is told by what the mod holds.
    beyond = beyond_ops(workspace, guid, origin)
    beside = beyond_unread(workspace, guid, origin)
    # Where in the typed text each thing read is, by which it is: the
    # reading's number for it is its place in the file from here on.
    typed_at = {
        id(one): found.typed_at[one.order]
        for one in found.own_ops + found.reaching + found.own_unread + found.unread
        if one.order in found.typed_at
    }
    # And what stands at each place of the text that the mod holds already:
    # what a new op is put after, or before.
    anchors: Dict[int, Any] = {}

    def anchor(typed, held) -> None:
        if id(typed) in typed_at:
            anchors[typed_at[id(typed)]] = held

    for held, _note in found.standing:
        anchors[found.standing_at[id(held)]] = held
    for entry in found.standing_unread:
        anchors[found.standing_at[id(entry)]] = entry
    answers: Dict[str, List[Change]] = {}
    for change in found.answers:
        answers.setdefault(change.op_uid, []).append(change)
    # What each op's note read as in the text the typing started from.
    read_as: Dict[Tuple, List[str]] = {}
    for record in (was.own_ops + was.reaching if was is not None else ()):
        read_as.setdefault(keptops.canonical_op(record.source), []).append(record.note)

    def renoted(record: Op, held) -> None:
        before = read_as.get(keptops.canonical_op(record.source))
        if before and record.note != before.pop(0):
            held.note = record.note
            done.retuned += 1
        else:
            done.kept += 1

    # The ops that stand as they were, under the note the text gives each:
    # compared with the note the text it started from gave.
    noted = {id(held): note for held, note in (was.standing if was is not None else ())}
    for held, note in found.standing:
        if was is not None and id(held) in noted and _flat(note) != _flat(noted[id(held)]):
            held.note = _flat(note)
            done.retuned += 1
        else:
            done.kept += 1
    still = {id(held) for held, _note in found.standing}
    pairs, new, gone = _pairs(
        [op for op in own_ops(loadout, guid, origin) if id(op) not in still],
        found.own_ops, _same_source,
    )
    for held, typed in pairs:
        renoted(typed, held)
        anchor(typed, held)
    gone_beyond = [op for op in beyond if id(op) not in still]
    idle = {op.uid for op in gone_beyond if in_idle_group(loadout, op)}
    # The Studio's own changes, each shown as an op: one typed back as it
    # was is that change still.
    authored = list(own_changes(workspace, guid, origin))
    shown = {change.key: op_text(loadout, origin, change.key) for change in authored}
    fresh: List[Op] = []
    for record in new:
        same = next(
            (
                change for change in authored
                if shown[change.key] and keptops.same_op(shown[change.key], record.source)
            ),
            None,
        )
        if same is None:
            fresh.append(record)
        else:
            authored.remove(same)
            renoted(record, same)
            anchor(record, same)
    # And several of them shown as the one op they are written as: two
    # assets added beside the same one.  Typed back as it was, that op was
    # matched to neither change, so both were taken out and it went in as an
    # op typed here -- with whatever was typed beside it, at every Apply.
    for run, text in _runs(loadout, origin, authored):
        whole = next((one for one in fresh if keptops.same_op(text, one.source)), None)
        if whole is not None:
            fresh.remove(whole)
            for change in run:
                authored.remove(change)
            renoted(whole, run[0])
            anchor(whole, run[0])
    # What players see each asset the mod adds called is on the change that
    # adds it and in no op: one whose op is typed over keeps its name.
    names = {
        effective.new_asset_guid(change): change.in_game_name
        for change in authored if change.creates_asset and change.in_game_name
    }

    def stands(entry) -> bool:
        return any(entry is held for held in found.standing_unread)

    had_unread, new_unread, gone_unread = _paired(
        [entry for entry in own_unread(loadout, guid, origin) if not stands(entry)],
        found.own_unread, _same_source)
    standing_own = sum(1 for entry in own_unread(loadout, guid, origin) if stands(entry))
    gone_unread = gone_unread + [entry for entry in beside if not stands(entry)]
    places = {op.uid: op.order for op in gone}
    for op in gone + gone_beyond:
        loadout.drop_op(op.uid)
    loadout.drop_unread(gone_unread)
    done.carried = standing_own + len(had_unread) + len(new_unread)
    done.carried_new = len(new_unread) + len(gone_unread)
    done.why_unread = [entry.why for entry in new_unread + found.unread]

    written = set()
    left_pane = 0
    new_here: List[tuple] = []

    # Ops that took the place of a change of the Studio's own, which has
    # no file of its own to hand on.
    placed: List[Op] = []

    def put(record: Op, mine: List[Change]) -> None:
        # Typed here: the user's, with what it comes to.
        record.authored = True
        loadout.set_op(record)
        for answer in mine:
            answer.order = record.order
            answer.authored = True
            if answer.creates_asset and not answer.in_game_name:
                answer.in_game_name = names.get(effective.new_asset_guid(answer), "")
            loadout.set_change(answer)
            written.add(answer.key)

    for record in fresh + found.reaching:
        mine = answers.get(record.uid, [])
        over = None
        if record in fresh:
            aim = _aim(record.source)
            over = next((op for op in gone if _aim(op.source) == aim), None)
        was_beyond = None
        if over is None and not record.scope and not record.within:
            aim = _aim(record.source)
            was_beyond = next((op for op in gone_beyond if _aim(op.source) == aim), None)
        if over is not None:
            gone.remove(over)
            record.order = places[over.uid]
            record.file = over.file
            done.retuned += 1
        elif was_beyond is not None:
            # Typed over an op that reaches beyond the asset: the op it was,
            # where it stood and in the group it sat in.  In one that does
            # not run it comes to nothing, as the reader leaves any op of
            # such a group: it was read here as though it ran.
            gone_beyond.remove(was_beyond)
            if was_beyond.uid in idle:
                mine, record.matched = [], []
            for answer in mine:
                answer.op_uid = was_beyond.uid
            record.uid = was_beyond.uid
            record.order = was_beyond.order
            record.file = was_beyond.file
            record.within = list(was_beyond.within)
            record.note = record.note or was_beyond.note
            done.retuned += 1
        else:
            under = None
            if record in fresh and len(mine) == 1:
                under = next((change for change in authored if _slot(change) == _slot(mine[0])), None)
            if under is not None:
                # Typed over a change of the Studio's: the op has its place.
                authored.remove(under)
                record.order = under.order
                loadout.remove_key(under.key)
                placed.append(record)
                done.retuned += 1
            else:
                if record in fresh:
                    done.added += 1
                elif not record.scope and (
                    guid in record.matched or any(answer.guid == guid for answer in mine)
                ):
                    # It reaches this asset, so it is in the pane from here on.
                    done.added += 1
                else:
                    left_pane += 1
                # New: placed once everything it could stand beside is.
                new_here.append((typed_at.get(id(record)), record, mine))
                continue
        put(record, mine)
        anchor(record, record)
    # What went in and is not the pane's to hold: it does not reach this
    # asset, or it is a group's own tag.
    done.reaching = left_pane
    done.groups = sum(1 for op in found.reaching if op.scope)
    for change in authored:
        # Not where something typed has just gone under the same key.
        if change.key not in written:
            loadout.remove_key(change.key)
    done.dropped = len(gone) + len(gone_beyond) + len(authored)

    # What is new goes where it was typed among the rest, in the order it
    # was typed: see _room_at.
    new_here += [(typed_at.get(id(entry)), entry, None) for entry in new_unread + found.unread]
    last = max((at for at, _thing, _mine in new_here if at is not None), default=0)
    for at, thing, mine in sorted(
        new_here, key=lambda one: last + 1 if one[0] is None else one[0]
    ):
        thing.order = _room_at(loadout, origin, anchors, at)
        if mine is None:
            loadout.unread.append(thing)
        else:
            put(thing, mine)
        if at is not None:
            anchors[at] = thing
        # In the file of what it now stands after.
        loadout.file_new_things(origin, [thing])
    loadout.file_new_things(origin, placed)
    # Of those carried that are not the asset's own, the ones that are not
    # in the pane from here on: aimed at other assets, or by a search.
    done.unread = sum(
        1 for entry in found.unread
        if guid not in (getattr(modops.read_op(entry.source, game=loadout.game), "guids", ()) or ())
    )
    return done


def _file_span(loadout: Loadout, origin: str, thing) -> Tuple[int, int]:
    """``(first, last)``: the places in ``origin``'s file ``thing`` takes
    up, or the whole of the outermost group it sits in."""
    within = list(getattr(thing, "within", None) or ())
    if not within:
        return thing.order, thing.order
    outer = within[0]
    group = loadout.op(outer)
    inside = [
        one.order for held in (loadout.ops, loadout.unread) for one in held
        if one.origin == origin and outer in one.within
    ]
    first = group.order if group is not None else thing.order
    return min(first, thing.order), max([thing.order, *inside])


def _room_at(loadout: Loadout, origin: str, anchors: Dict[int, Any],
             at: Optional[int]) -> int:
    """The place in ``origin``'s file for a new op typed at ``at`` in a pane
    of some of its ops, with room made for it there.

    ``anchors`` is what the mod holds at each place of the typed text.  The
    op goes straight after the nearest one typed before it -- after the
    whole of that one's group, unless the next one typed after it is in the
    group too, when it goes between the two.  Typed before them all, it goes
    straight before the first, or that one's group.  With nothing to go by
    it goes after the mod's other ops.
    """
    earlier = [one for one in anchors if at is not None and one < at]
    later = [one for one in anchors if at is not None and one > at]
    if earlier:
        before = anchors[max(earlier)]
        after = _file_span(loadout, origin, before)[1]
        if later and anchors[min(later)].order <= after:
            # Both in the one group: between them, and the group is written
            # round it in two parts.
            after = before.order
    elif later:
        after = _file_span(loadout, origin, anchors[min(later)])[0] - 1
    else:
        return loadout.after_last_op(origin)
    moved = loadout.make_room(origin, after)
    for place, thing in anchors.items():
        if id(thing) in moved:
            anchors[place] = moved[id(thing)]
    return after + 1


def _runs(loadout: Loadout, origin: str, changes: List[Change]) -> List[Tuple[List[Change], str]]:
    """The ones among ``changes`` the Studio writes together as one op, each
    run with that op: nodes put beside the same node, as the pane shows them.

    Which they are is the generator's to say, by the changes it writes each
    op for: grouped here by where they were aimed alone, two such ops beside
    one node were taken for one run that no one op is written for."""
    by_key = {change.key: change for change in changes}
    written = build.fragment_written(loadout, origin, list(by_key))
    found = []
    for span in written.spans:
        if span.kind != SPAN_CHANGES or span.off or len(span.changes) < 2:
            continue
        run = [by_key[change.key] for change in span.changes if change.key in by_key]
        text = run_text(loadout, origin, [change.key for change in run])
        if len(run) == len(span.changes) and text:
            found.append((run, text))
    return found


def read_and_apply(
    workspace, guid: str, origin: str, text: str, written: str = "",
) -> Tuple[Read, Optional[Applied]]:
    """:func:`read` ``text``, and :func:`apply` it where it read.
    ``written`` is the text the typing started from, where there was any."""
    found = read(workspace, guid, origin, text)
    if found.error or found.loose:
        return found, None
    return found, apply_read(workspace, guid, origin, found, written)


def apply_read(workspace, guid: str, origin: str, found: Read, written: str = "") -> Applied:
    """:func:`apply` what :func:`read` read, where ``written`` is the text
    the typing started from: for whoever asks something between the two."""
    was = read(workspace, guid, origin, written) if written.strip() else None
    if was is not None and (was.error or was.loose):
        was = None
    return apply(workspace, guid, origin, found, was)


@dataclass
class Reach:
    """Which assets what was typed in a pane of ModOps is about, told from
    the text alone, before anything is read."""

    # Every asset an op typed in, typed over or taken out names, or answered
    # to where it was a search the mod held; the pane's own asset with them.
    assets: List[str] = field(default_factory=list)
    # One of them is a search typed here: which assets it finds is not known
    # until it is read.
    search: bool = False


def _told_by(op: modops.SourceOp) -> tuple:
    """What tells one typed op from another, as two spellings of it agree."""
    if op.verbatim:
        return ("as written", _flat(op.verbatim), op.shelved, len(op.scopes))
    return ("op", *keptops.canonical_op(modops.render_op(op)), op.shelved, len(op.scopes))


def _typed_apart(loadout: Loadout, origin: str, text: str, written: str):
    """``(new, gone)``: the ops ``text`` holds that ``written`` does not,
    and the ones ``written`` holds that ``text`` does not; None where either
    is not ModOps to read."""
    root, stopped = as_typed(loadout, origin, text)
    was_root, was_stopped = as_typed(loadout, origin, written)
    if stopped is not None or was_stopped is not None:
        return None
    typed = _typed_ops(loadout, origin, root)
    before = _typed_ops(loadout, origin, was_root)
    left = [_told_by(op) for op in before]
    new = []
    for op in typed:
        told = _told_by(op)
        if told in left:
            left.remove(told)
        else:
            new.append(op)
    gone = []
    still = [_told_by(op) for op in typed]
    for op in before:
        told = _told_by(op)
        if told in still:
            still.remove(told)
        else:
            gone.append(op)
    return new, gone


@dataclass
class Going:
    """One op typed in a pane of ModOps that is not the pane's asset's own:
    where it goes, told from what it says."""

    # The lines of the typed text it starts and ends on, counted from 1.
    line: int = 0
    last: int = 0
    # The assets it names; none for a search.
    assets: List[str] = field(default_factory=list)
    search: bool = False


def goes(workspace, guid: str, origin: str, text: str, written: str = "") -> Optional[List[Going]]:
    """Each op typed into ``text`` since the pane was handed ``written``
    that goes somewhere other than to ``guid`` alone, in the order typed;
    None where either is not ModOps to read.  Nothing is read: an op names
    its assets, or it is a search.  An op the pane was handed is not here,
    whatever it reaches: it is drawn for what it was read as.
    """
    guid = str(guid)
    moved = _typed_apart(workspace.loadout, origin, text, written)
    if moved is None:
        return None
    shift = line_shift(text)
    lines = text.split("\n")

    def ends(first: int) -> int:
        # A ModOp holds no other, so it ends where the next one is closed.
        for at in range(first, len(lines) + 1):
            if "</ModOp>" in lines[at - 1]:
                return at
            if at > first and "<ModOp" in lines[at - 1]:
                break
        return first

    found = []
    for op in moved[0]:
        if op.scope or op.verbatim:
            continue
        named = list(dict.fromkeys(str(one) for one in op.guids))
        if named == [guid] or not (named or (op.kind and op.path)):
            continue
        first = op.line + shift
        found.append(Going(line=first, last=ends(first), assets=named, search=not named))
    return found


def reach(workspace, guid: str, origin: str, text: str, written: str = "") -> Optional[Reach]:
    """Which assets the difference between ``written``, the text a pane was
    handed, and ``text``, what it holds now, is about; None where either is
    not ModOps to read.  Nothing is read: an op says which assets it names,
    and one the mod held knows which it answered to.
    """
    guid = str(guid)
    moved = _typed_apart(workspace.loadout, origin, text, written)
    if moved is None:
        return None
    new, gone = moved
    found = Reach()
    assets: set = set()
    held = beyond_ops(workspace, guid, origin) if gone else []
    for op in gone:
        if op.scope:
            continue
        if op.guids:
            assets.update(str(one) for one in op.guids)
            continue
        said = op.verbatim or modops.render_op(op)
        was_op = next((one for one in held if keptops.same_op(one.source, said)), None)
        if was_op is not None:
            assets.update(str(one) for one in was_op.matched)
    for op in new:
        if op.scope:
            continue
        if op.guids:
            assets.update(str(one) for one in op.guids)
        elif op.kind and op.path:
            found.search = True
    if assets or found.search:
        assets.add(guid)
    found.assets = sorted(assets)
    return found
