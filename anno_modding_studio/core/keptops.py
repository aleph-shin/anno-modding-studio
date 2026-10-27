"""Ops kept as written (:class:`~.changes.Op`): what each comes to, how it is
drawn, and asking it again.

This was ``core.rules``, from when the one op kept was an edit aimed at a
shape rather than at a list of assets.  Every op of a mod is kept as written
now, and a pattern is still the plainest case for doing it, so it is the case
explained here.

A mod may say *every asset shaped like this* rather than naming what it means:
``Path="//Values/Trader"`` is one ModOp about twenty-seven traders.  The Studio
reads that by asking the file and rebuilding it as one narrow ModOp per asset,
which is what makes it stackable, switchable and reviewable.

But which assets a search answers to is not fixed.  Install a mod that adds a
twenty-eighth trader and the original op covers it in game; a rebuild frozen
into the twenty-seven does not.  So the op is kept as well as its answer -- a
:class:`~.changes.Op` holding the ModOp verbatim, with the changes it came to
hanging off it -- and that turns out to be worth three separate things:

**It can be asked again.**  When another mod invents an asset the search would
cover, the op is run against that asset and the missing changes appear.  The
previous arrangement could only notice the gap and suggest re-reading the mod.

**It can be written back out as itself.**  A mod's pattern exports as the one
search ModOp it arrived as, so a round trip through the Studio leaves it still
targeting by shape -- covering assets from mods the Studio has never seen,
which no rebuilt list of GUIDs can do.

**It can be authored.**  "Every building matching this: set that field" is a
perfectly reasonable thing to want, and it is one row rather than four hundred.

The op is the fact and its changes are today's answer.  An op that is on goes
out as it is; its changes are what it comes to against the assets loaded now,
shown for reading and for the conflicts they take part in, and they are not
switched off one at a time.  "All of these, except that one" is the op
followed by one that puts the one back -- which is how the game reads a mod's
file too.  Where the Studio's per-asset reading of an op could not follow it,
the op says so in a caveat and goes out unchanged: the reading is for the
editor, and the export is the op.

It goes back out *where it was*, not at the front of the file.  That sounds
like a detail and is most of what makes this work: a search that ran after a
named op of the same mod saw that op's work and did less because of it, so
hoisting it to the top made the edit twice.
"""

from __future__ import annotations

import copy
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import replace as _replace
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

from lxml import etree

from . import effective, games, modinfo, modop, modops, reader, settings
from .changes import emit_order, is_own_mod
from .i18n import number, tr, tr_stored, trn
from .modops import matches_anything, read_op

if TYPE_CHECKING:
    from .changes import Op


# --------------------------------------------------------------- reading them
@dataclass(frozen=True)
class View:
    """One kept op and everything it currently comes to."""

    op: Op
    changes: tuple
    # Whether it goes out as the op it is. Only two things stop that: the op
    # is switched off, or it was read from a loadout that kept only the search.
    whole: bool = False
    reason: str = ""
    # What the reader should know about the changes underneath: they are the
    # Studio's answer, and this is where that answer is known to be short.
    caveat: str = ""

    @property
    def origin(self) -> str:
        return self.op.origin

    @property
    def expression(self) -> str:
        return self.op.expression

    @property
    def uid(self) -> str:
        return self.op.uid

    @property
    def matched(self) -> tuple:
        return tuple(self.op.matched)

    @property
    def assets(self) -> Tuple[str, ...]:
        """The assets it actually changed."""
        seen: List[str] = []
        for change in self.changes:
            if change.guid not in seen:
                seen.append(change.guid)
        return tuple(seen)

    @property
    def active(self) -> int:
        return sum(1 for change in self.changes if change.active)

    def caption(self) -> str:
        if self.op.kept and not self.op.searches:
            # It names its assets rather than searching for them, so "of N
            # matching" would be a count of nothing.
            return trn("%(n)s change, kept as the op itself",
                       "%(n)s changes, kept as the op itself", len(self.changes))
        if not self.op.searches:
            # Kept as every op is, for no reason of its own to give.
            if not self.changes and self.op.folded:
                # It edits what an earlier op of the mod added, and the
                # reading has its work there.
                return tr("shown in the op it edits")
            if not self.changes:
                return tr("changes nothing as things stand")
            return trn("%(n)s change", "%(n)s changes", len(self.changes))
        matched = len(self.matched) or len(self.assets)
        # Translators: the second half of "5 changes across 3 of 10 matching
        # assets"; the count is of the assets the op's pattern matches.
        across = trn("%(some)s of %(n)s matching asset", "%(some)s of %(n)s matching assets",
                     matched, some=number(len(self.assets)))
        return trn("%(n)s change across %(assets)s", "%(n)s changes across %(assets)s",
                   len(self.changes), assets=across)


def of(loadout, origin=None) -> List[View]:
    """Every op the loaded mods keep as written, with what it came to.

    One pass over the change set rather than one per op: this is asked for
    while the changes panel is drawn, a mods folder is thousands of changes,
    and asking each op separately was a sort of the whole set apiece.
    """
    by_kept: Dict[str, List] = {}
    for change in loadout.ordered():
        if change.op_uid:
            by_kept.setdefault(change.op_uid, []).append(change)
    found = []
    for op in loadout.ops:
        if origin is not None and op.origin != origin:
            continue
        if op.aside:
            # A comment of the file, kept where it stands: no op.
            continue
        changes = tuple(by_kept.get(op.uid, ()))
        whole, reason = _whole(op)
        caveat = _caveat(op, changes)
        found.append(View(op, changes, whole, reason, caveat))
    # Deliberately unsorted: ops are held in the order they were read, which
    # is load order and then file order, and that order is load-bearing. Two
    # searches in one mod can hit the same node, and the second one's answer
    # depends on what the first one did -- so sorting them alphabetically for
    # the sake of a tidy list would quietly change what the mod does.
    return found


def _whole(op) -> Tuple[bool, str]:
    """Whether an op goes out as it was written.

    It does unless it is off.  The changes underneath are not a condition: an
    op is one thing the mod does, and the answers the Studio worked out for
    it today do not get a vote on whether the mod still does it.
    """
    if not op.can_expand:
        return False, tr("read from a loadout that kept only the search itself")
    if not op.active:
        # Translators: why an op kept as written does not go out: it is off.
        return False, tr("switched off", context="op")
    return True, ""


def _caveat(op, changes) -> str:
    """Where the changes underneath fall short of what the op does, if known.

    None of these changes what is written out.  They are for the reader of the
    list, who is looking at the Studio's answer and should know where it is
    not the whole one.

    An asset the mod itself invents is not one of them.  The reading runs the
    mod's ops in file order, so a search only ever answers with an asset that
    exists by the time it runs -- which is exactly what the game does with it.
    """
    notes = []
    if op.caveat:
        notes.append(tr_stored(op.caveat))
    # An op that is off has all its answers off with it, which is not short.
    off = sum(1 for change in changes if not change.active) if op.active else 0
    if off:
        notes.append(trn(
            "%(n)s of its changes is marked off, which a ModOp kept as written cannot honour: "
            "it applies to every asset it matches or to none",
            "%(n)s of its changes are marked off, which a ModOp kept as written cannot honour: "
            "it applies to every asset it matches or to none",
            off,
        ))
    if len(changes) != op.made:
        missing = op.made - len(changes)
        if missing > 0:
            notes.append(trn(
                "%(n)s of its changes has been reverted from the list, which "
                "does not take the asset out of the ModOp",
                "%(n)s of its changes have been reverted from the list, which "
                "does not take the asset out of the ModOp",
                missing,
            ))
        else:
            notes.append(tr("it has picked up changes since it was read"))
    # The op's own caveat was written down in English when it was read, and
    # is kept that way in the loadout file; what is worked out here is not.
    return "; ".join(notes)


# What a kept op's row is marked with: one for each of its target, payload
# and condition that is worked out as the game loads, and one for an op the
# mod ships switched off.  See section 4 of the ops-as-written proposal.
MARK_PATTERN = "pattern"
MARK_COMPUTED = "computed"
MARK_CONDITIONAL = "conditional"
MARK_OFF = "off"


@dataclass(frozen=True)
class Mark:
    """One thing there is to say of an op: what it comes to today, said in as
    few words as will do beside the drawing that says which thing it is
    (``""`` where there is nothing to add), and a sentence for the tooltip."""

    kind: str
    said: str
    tip: str
    # Whether ``said`` already counts what the op comes to, so that a count
    # of its changes beside it would say it twice.
    counts: bool = False


def marks(view, game: str = "") -> List[Mark]:
    """The marks for an op's row, in the order they are drawn."""
    op = view.op
    if not op.source:
        return []
    found = modops.described(op.source, game)
    out: List[Mark] = []
    # Not an op whose path is where assets it adds go: it names no GUID and
    # finds no asset, and is no pattern (``Op.searches``).
    if found.target == modop.TARGET_SEARCH and op.searches:
        matched = len(view.matched) or len(view.assets)
        changed = len(view.assets)
        out.append(Mark(
            MARK_PATTERN,
            trn("%(n)s asset", "%(n)s assets", matched)
            if changed == matched
            # Translators: a pattern that matches %(n)s assets and changes
            # %(some)s of them: the others are already as it would leave them.
            else trn("%(some)s of %(n)s asset", "%(some)s of %(n)s assets",
                     matched, some=number(changed)),
            # The pattern itself is not in it: the row's tooltip has it, cut
            # to fit, and beside the count it would be most of a screen.
            tr("Its target is a pattern, matched as the game loads, so it also "
               "covers assets other mods add."),
            True,
        ))
    elif found.target == modop.TARGET_PATTERN:
        out.append(Mark(
            MARK_PATTERN,
            trn("%(n)s match", "%(n)s matches", len(view.changes)),
            tr("Its path is a pattern inside the assets it names. What it "
               "finds there is worked out as the game loads."),
            True,
        ))
    if found.payload:
        values = {change.new_value for change in view.changes if change.new_value}
        one = len(values) == 1 and len(view.changes) == 1
        # Translators: beside a ModOp whose value is worked out as the game
        # loads: what it comes to today, as in "= 40".
        said = tr("= %s", next(iter(values))) if one else ""
        tip = {
            modop.PAYLOAD_COPY: tr(
                "Its value is copied from another place in the game's files as "
                "the game loads."
            ),
            modop.PAYLOAD_FLAGS: tr(
                "It adds or takes out flags in a list as the game loads."
            ),
        }.get(found.payload, tr(
            "Its value is worked out as the game loads, from the value already "
            "there or from the mod's own settings."
        ))
        out.append(Mark(MARK_COMPUTED, said, tip, one))
    if found.condition:
        runs = None
        if op.idle:
            runs = False
        elif view.changes or (op.scope and not op.idle):
            runs = True
        # Translators: of a ModOp with a condition, as things stand today.
        said = "" if runs is None else tr("runs") if runs else tr("does not run")
        tip = {
            modop.ASKS_LOADOUT: tr("It runs only if another mod is, or is not, loaded."),
            modop.ASKS_SETTING: tr("Whether it runs is one of the mod's own settings."),
        }.get(found.condition, tr(
            "It runs only if the game's files, as the mods before it leave "
            "them, are a certain way."
        ))
        out.append(Mark(MARK_CONDITIONAL, said, tip, runs is False))
    if found.skip:
        out.append(Mark(
            MARK_OFF,
            tr("off in the mod's file"),
            tr("The mod ships it switched off (Skip). A tweak tool may switch it on."),
            True,
        ))
    return out


def shape(view, game: str = "") -> Tuple[str, bool, Tuple[int, int, int, int]]:
    """An op as it is drawn: ``(type, shipped off, slots)``.

    The slots are target, payload, condition and options, each 0 where the
    op writes that down, and otherwise 1 -- or, of the first and third, 2
    where it comes to nothing today (a pattern that finds nothing, a
    condition that does not hold) and, of the third, 3 where the Studio
    cannot say whether it held.  See :mod:`ui.op_marks`, which draws it.
    """
    op = view.op
    found = modops.described(op.source, game)
    target = 0
    if found.target and (op.searches or found.target != modop.TARGET_SEARCH):
        target = 1 if (view.changes or (found.target == modop.TARGET_SEARCH and view.matched)) else 2
    condition = 0
    if found.condition:
        if op.idle:
            condition = 2
        elif view.changes or (op.scope and not op.idle):
            condition = 1
        else:
            condition = 3
    return (
        found.kind,
        found.skip,
        (target, 1 if found.payload else 0, condition, 1 if found.options else 0),
    )


def stands_alone(view) -> bool:
    """Whether an op is drawn as the one change it comes to, and not as a
    row of its own over it: it was read as saying what that change says
    (:attr:`~.changes.Op.plain`), and that one change is still what is
    under it."""
    op = view.op
    return bool(
        op.plain
        and not op.parts
        and len(view.changes) == 1
        and not view.changes[0].within
    )


def clipped(text: str, most: int = 120) -> str:
    """A path or a pattern cut to fit a tooltip, from the middle: where it
    starts and the field it ends at are what tell it from the next one."""
    text = " ".join((text or "").split())
    if len(text) <= most:
        return text
    head = (most - 1) // 2
    return text[:head].rstrip() + "…" + text[len(text) - (most - 1 - head):].lstrip()


def describe(loadout, change) -> str:
    """One line for a change's tooltip, or "" when it came out of no search."""
    if not change.search:
        return ""
    op = loadout.op(change.op_uid) if change.op_uid else None
    matched = op.matched if op is not None else ()
    search = clipped(change.search)
    if matched:
        return trn("One of %(n)s asset matching %(search)s",
                   "One of %(n)s assets matching %(search)s",
                   len(matched), search=search)
    return tr("From a search: %s", search)


def exportable(loadout, origin=None) -> Tuple[List, set]:
    """``(ops to write out as themselves, the change keys they stand for)``.

    The seam the generator asks about: a whole op is written back as the one
    ModOp it came from and its changes are left out, because writing both would
    apply the edit twice.
    """
    emit: list = []
    covered: set = set()
    for view in of(loadout, origin):
        if not view.whole:
            continue
        emit.append(view.op)
        covered.update(change.key for change in view.changes)
    return emit, covered


# ------------------------------------------------- writing one narrow, on asking
@dataclass(frozen=True)
class Narrowing:
    """What *Write as narrow ModOps* would make of one kept op.

    ``refused`` is why it cannot be done, in words for the row, and "" where
    it can.  ``warnings`` are what doing it gives up, each a sentence: the op
    is replaced by what it comes to today, and some ops come to more
    tomorrow.  ``plain`` is an op that says no more than its one change does,
    so that there is nothing to gain: it can be done, and is not worth
    offering for a whole mod.
    """

    op: Any
    changes: tuple = ()
    refused: str = ""
    warnings: tuple = ()
    plain: bool = False

    @property
    def possible(self) -> bool:
        return not self.refused


def _hosts(loadout) -> set:
    """The uids of the ops whose changes carry another op's work: one that
    edited what they had just added is read into their payload."""
    owner = {tuple(change.key): change.op_uid for change in loadout.changes.values()}
    return {owner.get(tuple(key)) for op in loadout.ops for key in op.folded} - {None, ""}


def narrowing(loadout, view, hosts=None) -> Narrowing:
    """Whether the op of ``view`` can be written as the narrow ModOps it
    comes to, and what that would give up.

    It can where what is listed under it is the whole of what it does, and
    will go on being: the reading replayed adds up, nothing of it has been
    reverted or switched off apart, and nothing but its own text decides
    whether and where it runs.  A group, an op in one, a condition and a
    ``Skip`` are all a question asked as the game loads, which a narrow ModOp
    of the Studio's has nowhere to put.

    A pattern and a value worked out as the game loads can be written narrow
    and lose something by it, which is said and not refused: this is where
    the two "settle" settings of a reading went.
    """
    op = view.op
    changes = tuple(view.changes)
    found = modops.described(op.source, loadout.game)
    hosts = _hosts(loadout) if hosts is None else hosts

    def no(why: str) -> Narrowing:
        return Narrowing(op, changes, refused=why)

    if op.scope:
        return no(tr("it is a group, and what is in it is each an op of its own"))
    if not op.can_expand:
        return no(tr("the loadout holds only its search, and not the ModOp it was"))
    if not op.active:
        return no(tr("it is switched off"))
    if op.within:
        return no(tr("it is in a group, whose condition a narrow ModOp would not have"))
    if found.skip:
        return no(tr("its mod ships it switched off (Skip), for a tweak tool to switch on"))
    if found.condition and found.condition != modop.ASKS_DOCUMENT:
        # Which mods are loaded, or one of the mod's own settings: a switch,
        # and the op is what it switches.
        return no(tr("it runs on a condition, which a narrow ModOp would not have"))
    if op.wide:
        return no(tr_stored(op.wide))
    if op.folded:
        return no(tr("what it does is shown in the op it edits, and not under it"))
    if op.uid in hosts:
        return no(tr("another op of the mod edits what it adds, and is shown in it"))
    if op.unread_at or op.caveat:
        return no(tr("the Studio's reading of it is known to be short"))
    if not changes:
        return no(tr("it changes nothing as things stand, so there is nothing to write"))
    if view.caveat:
        return no(view.caveat)
    if op.kept and not (found.payload or found.target):
        # The reader's own reason, which is none of the ones above.
        return no(tr_stored(op.kept))

    warnings = []
    if found.condition:
        warnings.append(tr(
            "It runs only where a condition about the game files holds, which "
            "it does today. Written narrow it runs whatever they say."
        ))
    if found.target:
        assets = len({change.guid for change in changes})
        warnings.append(trn(
            "It finds what it changes as the game loads. Written narrow it is the "
            "%(n)s asset it finds today, and nothing a mod adds later.",
            "It finds what it changes as the game loads. Written narrow it is the "
            "%(n)s assets it finds today, and nothing a mod adds later.",
            assets,
        ) if found.target == modop.TARGET_SEARCH else tr(
            "It finds the entries it changes as the game loads. Written narrow it "
            "is the ones it finds today, and not one another mod adds."
        ))
    if found.payload == modop.PAYLOAD_COPY:
        warnings.append(tr(
            "It copies its value from another asset. Written narrow it is the value "
            "that asset has today, and stops following it."
        ))
    elif found.payload:
        warnings.append(tr(
            "It works its value out as the game loads. Written narrow it is the "
            "value it comes to today, and stops following the mod's settings."
        ))
    return Narrowing(op, changes, warnings=tuple(warnings), plain=stands_alone(view))


def narrowings(loadout, origin=None) -> List[Narrowing]:
    """:func:`narrowing` for every kept op of ``origin``, or of every mod."""
    hosts = _hosts(loadout)
    return [narrowing(loadout, view, hosts) for view in of(loadout, origin)]


def _as_written(changes) -> list:
    """One op's ``changes`` in the order an export writes them: asset by
    asset as the list has them, and each asset's as its ModOps go out.  The
    list's own order is by path, and a removal is written before a value set
    whatever their paths, so the first listed was not the first written."""
    by_asset: Dict[str, list] = {}
    for change in changes:
        by_asset.setdefault(change.guid, []).append(change)
    return [change for block in by_asset.values() for change in emit_order(block)]


def write_narrow(loadout, uids) -> Tuple[int, int]:
    """Replace each of the ops ``uids`` by the changes it comes to, which are
    the Studio's own from then on: ``(ops written narrow, changes they are
    now)``.  One it cannot be done to is left as it is.

    Each change stays where its op was in the mod's file, the first of them
    written takes the op's note, and all of them are work of yours, as the
    op was not: its mod's folder still has the op.  An edit that was the
    exception to the op is an edit after those changes, and nothing's
    exception.
    """
    wanted = set(uids)
    done = made = 0
    for found in narrowings(loadout):
        op = found.op
        if op.uid not in wanted or not found.possible:
            continue
        said = ""
        for at, change in enumerate(_as_written(loadout.changes_by_op(op.uid))):
            change.op_uid = ""
            change.search = ""
            change.order = op.order
            change.authored = True
            if not at:
                said = change.note = change.note or op.note
            elif change.note == said:
                # The reader gives each change of a commented op that
                # comment, which was one comment above one ModOp.
                change.note = ""
            made += 1
        for change in loadout.changes.values():
            if change.exception_of == op.uid:
                change.exception_of = ""
        loadout.drop_op(op.uid, with_changes=False)
        done += 1
    return done, made


# One step in, as ops are laid out when they are read.
_STEP = "  "


def _located(loadout, change, payload_path: str):
    """``(op, its text read, the node there)`` for the node at
    ``payload_path`` of what ``change`` adds, found in the op it is an
    answer of where that op writes it down; ``None`` where it does not.

    Found only where the op names that one asset, since an edit made in an
    op that names several would be every one's, and only where what the
    answer adds is in the op as it is written -- once and as it reads.
    """
    op = loadout.op(change.op_uid) if change.op_uid else None
    if op is None or op.scope or op.searches or not op.source or op.matched != [change.guid]:
        return None
    if any(other.guid != change.guid for other in loadout.changes_by_op(op.uid)):
        return None
    payload = effective.parse_payload(change)
    if payload is None:
        return None
    steps = [int(step) for step in payload_path.split(".")] if payload_path else []
    top = payload
    if payload.tag == effective.WRAPPER:
        if not steps or not 0 <= steps[0] < len(payload):
            return None
        top, steps = payload[steps[0]], steps[1:]
    # The rest of the way down, as places among elements: an op's text has
    # its own comments, and an answer's payload need not.
    node, down = top, []
    for step in steps:
        inside = list(node)
        if not 0 <= step < len(inside) or not isinstance(inside[step].tag, str):
            return None
        down.append(sum(1 for one in inside[:step] if isinstance(one.tag, str)))
        node = inside[step]
    try:
        written = etree.fromstring(op.source.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return None
    found = [one for one in written.iter() if one is not written and _same_node(one, top)]
    if len(found) != 1:
        return None
    there = found[0]
    for step in down:
        there = _elements(there)[step]
    return op, written, there


def _elements(node) -> list:
    return [one for one in node if isinstance(one.tag, str)]


def _steps(written, node) -> List[int]:
    """Where ``node`` is in ``written``: from the top down, its place among
    the elements of the one above it at each step."""
    steps: List[int] = []
    while node is not written:
        parent = node.getparent()
        steps.insert(0, _elements(parent).index(node))
        node = parent
    return steps


def type_into(loadout, change, payload_path: str, value: str) -> bool:
    """Type ``value`` over the field at ``payload_path`` of what ``change``
    adds, in the op it is an answer of, and say whether that was done.

    The field is the op's, written down in it, so a value typed over it is
    the op retuned and not an exception to it: the op keeps its place in its
    mod's file and the groups it sits in (:meth:`.changes.Loadout.typed_into`),
    and the answer's payload says what the op now does.  Where the op does
    not write it down (:func:`_located`) it is not done, and the edit is
    left to the caller.
    """
    found = _located(loadout, change, payload_path)
    if found is None:
        return False
    op, written, node = found
    if len(node) or not isinstance(node.tag, str):
        return False
    was = (node.text or "").strip()
    source = modop.typed_over(op.source, _steps(written, node), was, value)
    if source is None or not effective.set_payload_text(change, payload_path, value):
        return False
    _typed(loadout, change, op, source, modop.summary_typed_over(op.summary, was, value))
    return True


def add_into(loadout, change, payload_path: str, element) -> bool:
    """Add ``element`` under the node at ``payload_path`` of what ``change``
    adds, in the op it is an answer of, and say whether that was done: as
    :func:`type_into` types a value there, and laid out as the nodes beside
    it are."""
    found = _located(loadout, change, payload_path)
    if found is None:
        return False
    op, written, node = found
    if (node.text or "").strip():
        return False
    added = copy.deepcopy(element)
    _lay_in(node, added)
    if not effective.add_to_payload(change, payload_path, element):
        return False
    _typed(loadout, change, op, etree.tostring(written, encoding="unicode"))
    return True


def take_out_of(loadout, change, payload_path: str) -> bool:
    """Take the node at ``payload_path`` of what ``change`` adds out of the
    op it is an answer of, and say whether that was done: as
    :func:`type_into` types a value there.  The whole of what ``change``
    adds goes with the change.  Not done where it is all the op has: then
    it is the op that goes, as the caller has it go."""
    found = _located(loadout, change, payload_path)
    if found is None:
        return False
    op, written, node = found
    _lay_out(node)
    if not _elements(written):
        return False
    gone = effective.remove_from_payload(change, payload_path)
    if gone == "empty":
        loadout.remove_key(change.key)
    elif gone != "removed":
        return False
    _typed(loadout, change, op, etree.tostring(written, encoding="unicode"))
    return True


def _typed(loadout, change, op, source: str, summary: str = "") -> None:
    """The op put back as ``source``, with ``change`` its answer still."""
    typed = loadout.typed_into(op, source, summary)
    change.authored = True
    change.active = typed.active


def _lay_in(parent, node) -> None:
    """Put ``node`` last in ``parent``, laid out as the nodes beside it
    are: on a line of its own at their indent, with what is in it laid out
    under it, where they are each on one, and on theirs where they are not."""
    inside = list(parent)
    if inside:
        before = inside[-2].tail if len(inside) > 1 else parent.text
    else:
        above = parent.getprevious()
        before = None if parent.getparent() is None else (
            above.tail if above is not None else parent.getparent().text
        )
    if "\n" not in (before or ""):
        for one in node.iter():
            if len(one) and not (one.text or "").strip():
                one.text = None
            if not (one.tail or "").strip():
                one.tail = None
        parent.append(node)
        return
    if inside:
        last = inside[-1]
        node.tail, last.tail = last.tail, before
        indent = before.rsplit("\n", 1)[-1]
    else:
        # The first thing in it: a step in from the line it is on.
        outer = before.rsplit("\n", 1)[-1]
        indent = outer + _STEP
        parent.text, node.tail = "\n" + indent, "\n" + outer
    if len(node):
        etree.indent(node, space=_STEP, level=len(indent) // len(_STEP))
    parent.append(node)


def _lay_out(node) -> None:
    """Take ``node`` out of its parent, and the line it stood on with it."""
    parent = node.getparent()
    before = node.getprevious()
    if before is not None:
        before.tail = node.tail
    else:
        parent.text = node.tail
    parent.remove(node)


def _same_node(one, other) -> bool:
    """Whether two elements say the same, their comments and layout aside."""
    if not isinstance(one.tag, str) or one.tag != other.tag or dict(one.attrib) != dict(other.attrib):
        return False
    if (one.text or "").strip() != (other.text or "").strip():
        return False
    mine = [node for node in one if isinstance(node.tag, str)]
    theirs = [node for node in other if isinstance(node.tag, str)]
    return len(mine) == len(theirs) and all(map(_same_node, mine, theirs))


def claims_more(view, game: str = "") -> int:
    """How many fields a ``replace`` retypes and leaves as they were, in the
    asset it does that most to; 0 for any other op.

    A replace of a node claims all of it: a field it types again with the
    value it had is taken from every other mod that changes it.  Counted as
    the values its body holds, less the changes it comes to in an asset.
    """
    op = view.op
    if op.scope or not view.changes:
        return 0
    read = modops.read_op(op.source, game=game)
    if read is None or read.kind != "replace":
        return 0
    try:
        node = etree.fromstring(op.source.strip().encode("utf-8"))
    except etree.XMLSyntaxError:
        return 0
    typed = sum(
        1 for leaf in node.iter()
        if isinstance(leaf.tag, str) and leaf is not node and not len(leaf)
    )
    if typed < 2:
        return 0
    by_asset: Dict[str, int] = {}
    for change in view.changes:
        by_asset[change.guid] = by_asset.get(change.guid, 0) + 1
    return max(0, typed - min(by_asset.values()))


# ------------------------------------------------------------- running them
class WithAssets:
    """The index, plus a handful of assets it has never heard of.

    Running an op against an asset another mod invented means handing the
    translator something the shipped file does not contain.  Everything else it
    asks -- how the base game uses a list, what a field is worth -- is still a
    question for the real index, so this stands in front rather than replacing.
    """

    def __init__(self, index, extra, labels=None):
        self._index = index
        self._extra = {str(guid): element for guid, element in dict(extra).items()}
        self._labels = dict(labels or {})

    def asset_element(self, guid: str):
        found = self._extra.get(str(guid))
        if found is not None:
            return found
        return self._index.asset_element(guid)

    def display_name(self, guid: str) -> str:
        if str(guid) in self._labels:
            return self._labels[str(guid)]
        return self._index.display_name(guid)

    def __getattr__(self, name):
        return getattr(self._index, name)


def reading_of(loadout, origin: str) -> Dict[str, Any]:
    """What reading one of ``origin``'s ops again takes from the loadout it is
    in, as :func:`reader.read_ops` takes them: the settings the mod was read
    at, the mods loaded beside it, and whether its inert removals were kept.

    One answer for every op read after the import -- a pattern put to an asset
    that has arrived, an op typed in the XML view -- so that neither reads the
    mod differently from the other, or from how it was read in.
    """
    layer = loadout.layer(origin)
    reading = modops.Reading.as_recorded(layer.read_as if layer is not None else None)
    # At the settings each mod was read with, not at none: a configurable
    # mod's ops read those back, and asking one of them again with the
    # answers thrown away would make it do something the rest does not.
    # Every loaded mod's, since an op can name another's in full.
    chosen = {one.origin: _settled(one) for one in loadout.layers}
    mod_ids = {one.origin: one.mod_id for one in loadout.layers}
    if layer is not None:
        chosen[origin] = _settled(layer)
    elif is_own_mod(origin):
        # The mod written here, at the defaults of what it declares: what
        # the game runs it at until a player says otherwise.
        chosen[""] = settings.read(settings.block_of(loadout, "")).defaults()
        mod_ids[""] = (
            modinfo.own_mod_id(loadout) if loadout.game == games.ANNO_117 else loadout.own.mod_id
        )
    else:
        chosen[origin] = {}
    return {
        "keep_inert": reading.keep_inert,
        "chosen": chosen,
        "mod_ids": mod_ids,
        "present": [
            value for one in loadout.layers for value in (one.mod_id, one.origin)
        ] + [loadout.own.mod_id, loadout.own.name],
    }


def _settled(layer) -> Dict[str, str]:
    """What a loaded mod's settings are set to: each option it declares at
    its default, under what was chosen. A mod left at its defaults has
    nothing chosen, and read with nothing an op that asks one of them found
    it undeclared."""
    chosen = dict(layer.options)
    if layer.offered:
        chosen = settings.from_dict(layer.offered).settled(chosen)
    return chosen


def searching(view, ops, invented=()):
    """Which assets a path matches, for every search ``ops`` make: of
    ``view``, swept once for the lot, and of ``invented`` -- assets the
    game's files do not have, as ``(guid, element)``.  None where there is
    nothing to ask and nothing invented."""
    swept = reader.searcher(view, ops)
    if not invented:
        return swept

    def search(path):
        found = list(swept(path)) if swept is not None else []
        seen = set(found)
        found.extend(
            made for made, element in invented
            if made not in seen and matches_anything(element, path)
        )
        return found

    return search


def expand(
    loadout,
    index,
    record,
    guids,
    assets=None,
    options=None,
):
    """Run an op against particular assets and return the changes it makes.

    Read in the loadout's game, and as the mod it belongs to was read
    (:func:`reading_of`).  ``assets`` supplies elements for GUIDs the index
    does not have -- the ones another mod invented -- as ``{guid: element}``.
    ``options`` stands in for the settings the mod was read at.
    """
    guids = [str(guid) for guid in guids]
    if not guids:
        return [], tr("it matches nothing")
    op = read_op(record.source, game=loadout.game)
    if op is None:
        return [], tr("its ModOp could not be read back")
    view = WithAssets(index, assets) if assets else index
    asked = reading_of(loadout, record.origin)
    if options is not None:
        asked["chosen"] = {record.origin: dict(options)}

    # Which assets it is for is already decided by the caller: this is the op
    # being put to a named set, not a fresh sweep of the file.  That is the
    # only question answered so.  What else the op asks -- the search in its
    # condition, the place a Content= copies from -- is asked of the file and
    # of what the session invents, as it was when the mod was read: answered
    # with the named set, a condition held or failed by which assets arrived.
    # Swept for as an op that names its asset, so not for its own path.
    op = _replace(op, mod=record.origin, guid="")
    others = searching(view, [_replace(op, guid=guids[0])], list((assets or {}).items()))

    def search(path):
        if path == op.path:
            return list(guids)
        return list(others(path)) if others is not None else []

    results = reader.read_one(op, view, search=search, **asked).results

    made = []
    for result in results:
        for change in result.changes:
            change.op_uid = record.uid
            change.search = record.expression
            made.append(change)
    reason = "; ".join(result.reason for result in results if result.reason)
    return made, reason


def same_op(one: str, other: str) -> bool:
    """Whether two ModOp sources say the same thing.

    Compared after reading rather than as text, because the same op has several
    spellings: ``Merge="//X"`` and ``Type="merge" Path="//X"`` are one op.
    """
    left, right = read_op(one), read_op(other)
    if left is None or right is None:
        return left is right
    return (
        _op_attrs(left) == _op_attrs(right)
        and (left.text or "").strip() == (right.text or "").strip()
        and _inside(left) == _inside(right)
    )


def canonical_op(source: str) -> tuple:
    """A ModOp source as something two spellings of one op agree on: what
    :func:`same_op` compares, for a set to hold."""
    op = read_op(source)
    if op is None:
        return (source,)
    return (
        tuple(sorted(_op_attrs(op).items())),
        (op.text or "").strip(),
        tuple(repr(part) for part in _inside(op)),
    )


def _op_attrs(op) -> dict:
    """A ModOp's own attributes, in one spelling.

    Every way of naming the target collapses to ``Type``/``GUID``/``Path``,
    the asset as the reader takes it, from ``GUID=`` or the head of the path;
    everything else -- ``Condition``, ``Content``, and anything the Studio has
    never heard of -- is compared as written, because being unrecognised is the
    strongest reason not to throw it away.
    """
    from .modop import SHORTHAND_ATTRS

    found = {
        name: value
        for name, value in op.attrs.items()
        if name not in SHORTHAND_ATTRS and name not in ("Type", "GUID", "Path")
    }
    found["Type"] = op.kind
    # An op of a type not read names no asset as read: its GUID as written.
    guid = op.guid or op.attrs.get("GUID")
    if guid:
        found["GUID"] = guid
    found["Path"] = op.path
    return found


def _inside(op) -> list:
    """What an op holds, as two spellings of it agree: each thing directly
    inside it, with the text that follows it.

    Its comments too, as those deeper in always were, and that text: both
    are in the op as it is written back, and an op recomposed without them
    is not that op.
    """
    return _with_tails(op.inside or op.payload)


def _with_tails(nodes) -> list:
    """Each of these with the text that follows it, where that is more than
    the file's layout."""
    return [(_shape(node), (node.tail or "").strip()) for node in nodes]


def _shape(node):
    """A payload element as a comparable tree, whitespace aside.

    With the text after each thing inside it, at every depth.  That text is
    in the op as written, and the game can make a value of it:
    ``<Copied><ModOpContent/>5</Copied>`` is not ``<Copied><ModOpContent/>
    </Copied>``.  Left out, an op edited only there was the op it had been.
    """
    return (
        node.tag,
        dict(node.attrib),
        (node.text or "").strip(),
        _with_tails(node),
    )


def extend(
    loadout,
    index,
    view,
    among=None,
    options=None,
):
    """Put every pattern to the assets it does not yet cover, and keep the answers.

    This is the whole point of storing the op rather than only the search: a
    mod that says "every trader" starts covering a trader another mod invented,
    the way it would have in game, instead of only being reported as out of
    date.

    Returns ``(changes added, [(op, guids it now covers)])``.
    """
    drifts = drifted(loadout, view, among)
    if not drifts:
        return [], []

    steady = getattr(view, "steady", None)
    made: List = []
    covered: List[Tuple[object, List[str]]] = []
    for drift in drifts:
        op = loadout.op(drift.uid)
        if op is None or not op.can_expand or not drift.arrived:
            continue
        # Held steady for one op's reading and no longer: the changes it
        # comes to are set after, so that a mod later in the order reads
        # the asset with them.
        with steady() if steady is not None else nullcontext():
            assets = {}
            for guid in drift.arrived:
                element = _as_this_mod_sees_it(view, guid, op.origin)
                if element is not None:
                    assets[guid] = element
            if not assets:
                continue
            changes, _reason = expand(
                loadout, index, op, list(assets), assets=assets, options=options
            )
        for change in changes:
            # An op made here is the user's with what it comes to.
            change.authored = op.authored
            loadout.set_change(change)
        op.matched = sorted(
            set(op.matched) | set(assets), key=lambda guid: (len(guid), guid)
        )
        op.made += len(changes)
        made.extend(changes)
        covered.append((op, sorted(assets)))
    return made, covered


def _as_this_mod_sees_it(view, guid: str, origin: str):
    """An asset as it will read by the time ``origin`` loads.

    Not as the whole stack leaves it: a pattern belonging to a mod halfway down
    the load order is answered by the document that mod is handed, the same
    reading the import used.
    """
    before = getattr(view, "asset_before", None)
    if before is not None and origin:
        found = before(guid, origin)
        if found is not None:
            return found
    element = getattr(view, "asset_element", None)
    return element(guid) if element is not None else None


# ------------------------------------------------ noticing that it has moved
@dataclass(frozen=True)
class Drift:
    """A pattern whose answer has moved since it was last asked."""

    origin: str
    uid: str
    expression: str
    arrived: tuple  # assets it would match now and did not then
    gone: tuple     # assets it matched then and would not now

    @property
    def matters(self) -> bool:
        return bool(self.arrived or self.gone)

    def describe(self, name) -> str:
        mod = name(self.origin)
        if self.arrived and self.gone:
            # Translators: counts of assets a pattern's search expression now
            # matches that it did not, and matched that are gone.
            return tr("%(mod)s: %(search)s now matches %(new)s new and %(gone)s gone",
                      mod=mod, search=self.expression,
                      new=number(len(self.arrived)), gone=number(len(self.gone)))
        if self.arrived:
            # Translators: a count of assets a pattern's search expression now
            # matches that it did not before.
            return trn("%(mod)s: %(search)s now matches %(n)s new",
                       "%(mod)s: %(search)s now matches %(n)s new",
                       len(self.arrived), mod=mod, search=self.expression)
        # Translators: a count of assets a pattern's search expression matched
        # that are no longer there.
        return trn("%(mod)s: %(search)s now matches %(n)s gone",
                   "%(mod)s: %(search)s now matches %(n)s gone",
                   len(self.gone), mod=mod, search=self.expression)


def drifted(loadout, view, among=None) -> List[Drift]:
    """Which patterns would answer differently now than when they were asked.

    Against the assets *this session* invents, and only those.  The shipped file
    cannot change while it is open, so a search whose answer has moved has moved
    because a mod put a new asset in front of it -- which is exactly the case a
    frozen rebuild misses and the original op would not have.  That also makes
    this cheap: no sweep, just the handful of assets the loaded mods make up.

    ``view`` is the workspace, which is what knows them.  ``among`` narrows the
    question to particular invented assets -- after a mod arrives, the only ones
    that can have moved anything are the ones it brought.
    """
    if not loadout.ops:
        return []

    steady = getattr(view, "steady", None)
    with steady() if steady is not None else nullcontext():
        return _against(loadout, view, among)


def _against(loadout, view, among) -> List[Drift]:
    made = list(view.invented_assets(among))
    # Which mod invented each of them, and where that sits in load order: an
    # asset only counts against a search that would have run *after* it was
    # there. A mod loading later invents nothing the earlier one could have
    # covered, in the game any more than here.
    born = {}
    for guid, _element in made:
        maker = view.creator_for(guid)
        if maker is not None:
            born[guid] = loadout.layer_rank(maker.origin)

    hits: Dict[str, set] = {}
    found = []
    for op in loadout.ops:
        if not op.searches:
            # It names its assets, so no new asset can be one of them.
            continue
        expression = op.expression
        if expression not in hits:
            hits[expression] = {
                guid for guid, element in made if matches_anything(element, expression)
            }
        before = set(op.matched)
        rank = loadout.layer_rank(op.origin)
        arrived = {
            guid
            for guid in hits[expression] - before
            if guid in born and born[guid] < rank
        }
        drift = Drift(
            origin=op.origin,
            uid=op.uid,
            expression=expression,
            arrived=tuple(sorted(arrived)),
            # Gone means gone from the session entirely: a mod that was loaded
            # when this was read and is not loaded now.
            gone=()
            if among is not None
            else tuple(sorted(guid for guid in before if not view.guid_exists(guid))),
        )
        if drift.matters:
            found.append(drift)
    found.sort(key=lambda drift: (drift.origin.lower(), drift.expression))
    return found


def summarise(drifts: Sequence[Drift], name) -> str:
    """A sentence for the status bar, or "" when nothing has moved."""
    if not drifts:
        return ""
    mods = {drift.origin for drift in drifts}
    arrived = {guid for drift in drifts for guid in drift.arrived}
    ops = len(drifts)
    # Every drift is a pattern of one mod, so several mods means several patterns.
    if arrived:
        assets = trn("%(n)s asset", "%(n)s assets", len(arrived))
        if len(mods) == 1:
            return trn(
                "%(n)s pattern in %(mods)s mod now matches %(assets)s it did not "
                "cover when you loaded it.",
                "%(n)s patterns in %(mods)s mod now match %(assets)s they did not "
                "cover when you loaded it.",
                ops, mods=number(len(mods)), assets=assets,
            )
        return trn(
            "%(n)s pattern in %(mods)s mods now matches %(assets)s it did not "
            "cover when you loaded them.",
            "%(n)s patterns in %(mods)s mods now match %(assets)s they did not "
            "cover when you loaded them.",
            ops, mods=number(len(mods)), assets=assets,
        )
    if len(mods) == 1:
        return trn(
            "%(n)s pattern in %(mods)s mod no longer matches what it did when you "
            "loaded it.",
            "%(n)s patterns in %(mods)s mod no longer match what they did when you "
            "loaded it.",
            ops, mods=number(len(mods)),
        )
    return trn(
        "%(n)s pattern in %(mods)s mods no longer matches what it did when you "
        "loaded them.",
        "%(n)s patterns in %(mods)s mods no longer match what they did when you "
        "loaded them.",
        ops, mods=number(len(mods)),
    )
