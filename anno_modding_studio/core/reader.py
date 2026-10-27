"""Read a mod somebody else wrote and turn its ModOps into Studio changes.

The Studio's whole model is one narrow ModOp per thing changed, generated from
a base asset it can see.  A hand-written mod is under no such discipline: it
addresses assets by XPath, guards ops with conditions, replaces whole subtrees
in one go, and mixes both games' dialects freely.  Importing one is therefore
not a parse but a translation, and the honest parts of it are:

**Say what each op does against *this* assets.xml.**  A ``Condition`` is not a
property of the mod, it is a question about the file being patched, so it gets
answered here rather than carried around: an op whose condition does not hold
is not part of what the mod does to this game version, and one whose condition
does hold no longer needs it.

**Break coarse ops into narrow ones.**  A ``merge`` that sets four fields is
four changes -- a replace for each the asset has, and for each it has not a
merge of that one field, which makes it where it is absent and sets it where
another mod made it first.  A ``replace`` of a whole subtree is a diff against the shipped
subtree.  A path matching six nodes is six changes, each re-addressed by
:mod:`.paths` so it points at exactly one -- and the op itself is kept as
well, as written (:mod:`.keptops`), with those six hanging off it, so it can be
put to an asset that arrives later and written back out still asking.  That
is the point of importing at all: a mod rebuilt this way stacks with other
mods, and stops claiming the parts of an asset its author never meant to
change.  A ``Content=`` copy is read too, looked up in the file being patched.

**Keep the rest, and say so.**  What has no narrow equivalent -- a list diff
with nothing to match its entries on, a condition the Studio cannot answer --
is not dropped: it is recorded as unread, with why, and written back out
exactly as it arrived.  Every op ends up in the report as changes, as "already
true of your assets.xml", or as something read with a reason it could not be.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from lxml import etree

from . import (
    applying,
    effective,
    games,
    generator,
    imya,
    modfolder,
    modops,
    recording,
    settings,
    switches,
)
from . import paths as pathmod
from .changes import (
    IN_A_BLOCK,
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
    STRAIGHT,
    TOOL_MARKUP,
    Change,
    Op,
    emit_order,
    is_own_mod,
)
from .i18n import STORED, tr, tr_noop, trn
from .modop import (
    AT_ASSET,
    KNOWN_ATTRS,
    MOD_ITEM,
    MOD_OP_CONTENT,
    MOD_VALUE,
    SETTING,
    allows_no_match,
    asks_a_setting,
    asks_the_loadout,
    has_computed,
    is_flags,
    is_skipped,
    loader_knows_type,
    only_flags,
    reaches_other_assets,
    targets_otherwise,
)
from .modops import (
    BLOCKED,
    CARRIED,
    IMPORTED,
    NUMBER,
    SETTLED,
    SHELVED,
    ImportReport,
    OpResult,
    Reading,
    SourceOp,
    Unrepresentable,
    children,
    compiled_search,
    condition_holds,
    describe_op,
    dialects,
    matches_anything,
    opening,
    read_source,
    render_op,
    search_in,
)

# How the Studio reads mods, as a number of its own, apart from the app's
# version.  Bump it in the change that makes a mod read differently: what an
# op comes to, which ops are read and which are unread, how a condition, a
# search or a value worked out at load is answered -- here, in ``modops``,
# ``typedops``, ``keptops`` or ``recording``.  Not for a change to how the
# reading is shown, or to what an export writes.  A loadout keeps the number
# its readings were made under (``Loadout.read_by``), and a different one
# has every mod in it read again (:func:`core.stale.check`); the loader
# probes' verdict is kept under it too, and asked again when it moves.
READER_VERSION = 1

# One coarse op is allowed to become a lot of narrow ones -- a subtree replace
# legitimately does -- but past this it is no longer a refactor, and a change
# list nobody can review is no improvement on a ModOp nobody can review.
MAX_CHANGES_PER_OP = 200

# And one op may be *about* this many assets before the same is true of the
# breadth rather than the depth. Deliberately generous: a mod that gives every
# item building a socket really does mean a thousand assets, and a thousand rows
# tucked inside that mod's own group in the list is bloat rather than a problem.
# The ceiling is here so a runaway search cannot make a list nothing can draw.
MAX_ASSETS_PER_SEARCH = 5000

# Where an Anno 117 mod adds a group of new assets: the end of the file's one
# list of groups. The only such place read, because it is the one measured --
# see :meth:`Translator._adds_a_block`.
BLOCK_PATH = "/AssetList/Groups[last()]"
# The list of assets that holds the one named: where a mod adds assets of
# its own beside one the game ships.
_LIST_HOLDING = re.compile(
    r"""^//Assets\[\s*Asset/Values/Standard/GUID\s*=\s*(['"]?)(\d+)\1\s*\]$"""
)

# ------------------------------------------------------------------- the nest
# What became of a scope once its question was put -- see Translator._enter.
SCOPE_READ = "read"              # in no group, or in one that asks nothing
SCOPE_KEPT_ON = "kept, on"       # goes back out whole; runs today, so it is read
SCOPE_KEPT_OFF = "kept, off"     # goes back out whole; does not run today
SCOPE_CARRIED = "carried"        # could not be answered: carried, not read
# Read as written, a group is kept as a group with ops in it: each op inside
# is an op of its own, read where the group runs today and kept, coming to
# nothing, where it does not.
SCOPE_SPLIT_ON = "its own ops, on"
SCOPE_SPLIT_OFF = "its own ops, off"
_SCOPES_SPLIT = (SCOPE_SPLIT_ON, SCOPE_SPLIT_OFF)
# Inside these nothing is read, and nothing is said about what is in them: the
# scope is one thing, and it says so itself.
_SCOPES_NOT_READ = (SCOPE_KEPT_OFF, SCOPE_CARRIED)


@dataclass
class _Nest:
    """A scope, and everything inside it in file order: ops, and the scopes
    inside it as nests of their own."""

    scope: SourceOp
    inside: list = field(default_factory=list)


def _nest(ops) -> list:
    """The ops read out of a mod as the tree the loader runs: each op in
    file order, and in place of each scope a :class:`_Nest` holding it and
    what is inside it. An op is inside every scope its ``scopes`` names."""
    top: list = []
    opened: List[Tuple[str, list]] = [("", top)]
    for op in ops:
        while len(opened) > 1 and opened[-1][0] not in op.scopes:
            opened.pop()
        if op.scope:
            nest = _Nest(op)
            opened[-1][1].append(nest)
            opened.append((op.scope, nest.inside))
        else:
            opened[-1][1].append(op)
    return top


def _afresh(items) -> list:
    """``items`` of :func:`_nest` again, for a loop's next round: the same
    ops, each a copy of its own, since running an op marks it."""
    return [
        _Nest(replace(item.scope), _afresh(item.inside)) if isinstance(item, _Nest)
        else replace(item)
        for item in items
    ]


def _why_set_aside(op: SourceOp) -> Tuple[str, str]:
    """``(status, reason)`` for something that is carried rather than read."""
    if op.attrs.get("why"):
        return BLOCKED, op.attrs["why"]
    tag = op.path.strip("<>").split(" ")[0]
    if tag.lower() in TOOL_MARKUP:
        return CARRIED, tr_noop(
            "a tweak tool's markup, not a ModOp: kept where it was", context=STORED
        )
    if tag.lower() == "group":
        return BLOCKED, tr_noop(
            "a group with its own target or condition, which everything inside it "
            "shares: the Studio does not read those yet", context=STORED
        )
    if tag == "Asset":
        return BLOCKED, tr_noop(
            "an asset written straight under <ModOps>, which the loader adds to "
            "the game: the Studio does not read those yet", context=STORED
        )
    return BLOCKED, tr_noop(
        "<%s> is not a ModOp, and the Studio does not know what it does", context=STORED
    ) % tag


SPLIT_ON_COMMAS = tr_noop(
    "a list of GUIDs is split on the comma and nothing else, so a space beside "
    "one is part of what the game goes looking for", context=STORED
)


def _spaced(guid: str) -> bool:
    """Whether a GUID carries whitespace the loader will look for too."""
    return guid.strip() != guid


# A search that picks an asset out by its place among the others: a position
# or last() in a predicate on the Asset step itself.
_COUNTS_ASSETS = re.compile(r"//Asset(?:\[[^\]]*\])*\[\s*(?:\d+|[^\]]*(?:position|last)\(\))")
# The assets a search condition names outright: ``Standard/GUID='1500003632'``.
_NAMED_GUID = re.compile(r"""GUID\s*=\s*['"]?\s*(\d+)""")


def _merge_wanted(payload, target, game: str = ""):
    """What a merge wants the target to look like, as a node of its own tag.

    A mod may name the target twice -- once in the path and once as the
    payload's root: ``Merge="EffectSource"`` carrying ``<EffectSource>…``, or
    ``Merge="//MinEpilepsyWarningTime"`` carrying the value in a tag of the same
    name.  Then the payload *is* what the target should look like, rather than
    something to put inside it: the loader's "legacy merge" (117 L1276-1281,
    1800 L651-655), whatever the target holds, and its name compared as
    :func:`applying.named` compares one.

    Where the target is not the first of its name, see :func:`_lands_elsewhere`.
    """
    if applying.stands_for(payload, target.tag, game):
        want = recording.clean(payload[0])
        want.tag = target.tag
        return want
    wrapper = etree.Element(target.tag)
    for child in payload:
        wrapper.append(recording.clean(child))
    return wrapper


def _lands_elsewhere(op: SourceOp, targets) -> bool:
    """Whether a merge carrying one node named like its targets comes out
    otherwise than read target by target.

    The loader's source merges such a payload into the target's *parent*,
    where it pairs with the first child of that name, whichever node was aimed
    at -- measured with xmltest, both builds -- and its documentation shows
    only a target that is the one of its name.  *Mail 1840* merges an
    ``<Item>`` into every ``Item`` of a list, which by the source fills the
    first one over and over; read as each in turn, every entry came out the
    same.  The two readings agree where every target that is not the first
    of its name is left as it was, and so is the first, unless the op aims at
    it too: *All Modules 100 Range* adds a second ``ModuleBuildRadius`` and
    then merges the same value into both.  Where they do not agree, the op is
    left to the game.
    """
    payload = op.content if op.content else op.payload
    if not (len(payload) == 1 and isinstance(payload[0].tag, str)):
        return False
    tag = payload[0].tag
    worked_out = has_computed(payload)

    def changes(node) -> bool:
        if worked_out:
            return True
        merged = recording.clean(node)
        applying.merge(merged, payload, op.game)
        return etree.tostring(merged) != etree.tostring(recording.clean(node))

    for target in targets:
        parent = target.getparent()
        if not applying.named(target.tag, tag, op.game) or parent is None:
            continue
        first = next(
            child for child in parent if applying.named(child.tag, tag, op.game)
        )
        if first is target:
            continue
        if changes(target) or (not any(first is other for other in targets) and changes(first)):
            return True
    return False


def _by_number(guid: str):
    return (len(guid), guid)


def _resolve(asset, path: str) -> List:
    """Match a mod's path against an asset, in either game's convention.

    Anno 1800 writes ``/Values/Building/…`` and Anno 117 writes ``/Building/…``
    for the same node, and mods for 117 use both -- so rather than deciding
    which dialect a file is in, try the shorter reading first and take
    whichever actually lands on something.

    A leading slash is optional in a mod's ``Path``: ``Values/Cost`` and
    ``/Values/Cost`` are the same node to the game, and both spellings turn up
    in shipping mods -- often in the same file.
    """
    for candidate in dialects(path):
        if not candidate:
            return [asset]
        try:
            found = asset.xpath("." + candidate)
        except etree.XPathEvalError:
            continue
        found = [
            node for node in found if isinstance(getattr(node, "tag", None), str)
        ]
        if found:
            return found
    return []

# ------------------------------------------------------------- the translating
class Translator:
    """Turns a mod's ops into changes against one particular assets.xml.

    ``index`` is the loaded :class:`AssetIndex`, which is what makes any of this
    decidable at all: it names the shipped asset behind a GUID, and it knows how
    the base game uses each list -- which is how a removal that a wide replace
    only implied is told from one that was meant.

    ``keep_inert_removals`` writes those implied removals down anyway.  They are
    a faithful reading of the XPath and change nothing in game, so by default
    they are carried out on the working tree and not claimed by the mod.

    Every op somebody wrote is kept as it was written -- a
    :class:`~.changes.Op`, the op verbatim -- and the changes are what it
    comes to, held under it.  See ``docs/proposals/ops-as-written-proposal.md``
    and ``docs/proposals/reader-proposal.md``.
    """

    def __init__(self, index, keep_inert_removals: bool = False):
        self.index = index
        self.lookup = index.asset_element
        self.label = getattr(index, "display_name", None) or (
            lambda guid: "GUID " + guid
        )
        self.inert = None if keep_inert_removals else recording.Inert(index)
        # Answers "which assets does this path match?", when there is anything
        # to ask.  Set by analyse(); without it a search-targeted op is refused
        # rather than guessed at.
        # Set by the callers that have an index to search with; a translator
        # without one answers BLOCKED for search-targeted ops rather than
        # guessing. Untyped it inferred as plain None, which quietly killed
        # the `is None` checks that depend on it.
        self.search: Optional[Callable[..., Iterable[str]]] = None
        # And every op of a mod, kept as written: the op verbatim, and the
        # assets it answered to. That is what
        # lets the question be asked again later, and lets the mod be written
        # back out still asking it rather than frozen into today's answer.
        self.found: Dict[str, List[Op]] = {}
        self.sheets: Dict[str, recording.Sheet] = {}
        # Load order, when a stack of mods is read at once: mod -> position.
        self.order: Dict[str, int] = {}
        # The mods present in that stack, by declared ModID, so that a
        # ``Condition="#some-other-mod"`` can be answered instead of refused.
        self.present: set = set()
        # What each mod's own options are set to: mod -> {setting: value}.
        # A configurable mod's ops read these back, so without them half of what
        # it does cannot be decided at all.
        self.chosen: Dict[str, Dict[str, str]] = {}
        # What became of each conditioned scope, by its id, and the kept op
        # each one that goes back out whole is.
        self.scopes: Dict[str, str] = {}
        self.scope_ops: Dict[str, Op] = {}
        # What each group inside a kept one is called, for the rows the list
        # draws under its op -- see Change.within and Op.parts.
        self.scope_labels: Dict[str, str] = {}
        self._besides: Dict[str, str] = {}
        # How many loops are past their first round: a group inside one is
        # the same group again, and not another row under the loop's op.
        self._repeating = 0
        # Where run_all has got to: each mod's next number, and how many ops
        # of how many have been run, for its ``progress``.
        self._numbered: Dict[str, int] = {}
        self._total = self._done = 0
        self._progress: Optional[Callable[[int, int], None]] = None
        # Assets a mod creates without giving them a GUID: nothing can address
        # them afterwards, but their payloads still have to be written out.
        self.orphans: List[recording.Sheet] = []
        # How many assets have been made so far: the later of two put in after
        # one asset stands nearer it.
        self._made = 0
        # Where the ops the Studio could not read were aimed, so far in load
        # order. What the Studio has of those assets is not what the game has,
        # and a search condition that finds nothing there has not been answered.
        self.unread_at: Set[str] = set()
        # The top-level groups each GUID a mod has made an asset under is in,
        # the shipped asset's with them, and the GUIDs that are in more than
        # one: see :meth:`_stands_apart`.
        self._held_in: Dict[str, set] = {}
        self.apart: Set[str] = set()
        # How many groups mods have added at the end of the file.
        self._blocks = 0

    # -- assets, shipped and invented -----------------------------------
    def sheet(self, guid: str) -> Optional[recording.Sheet]:
        guid = str(guid)
        if guid in self.sheets:
            return self.sheets[guid]
        base = self.lookup(guid)
        if base is None:
            return None
        self.sheets[guid] = recording.Sheet(base)
        return self.sheets[guid]

    def _live(self, guid: str):
        sheet = self.sheet(guid)
        return sheet.working if sheet is not None else None

    def _search_holds(self, path: str) -> Optional[bool]:
        """Whether a search condition finds anything in the document as it
        stands now (:meth:`_found_now`)."""
        # One the sweep could not compile it also did not answer.
        if self.search is None or compiled_search(path) is None:
            return None
        try:
            found = self._found_now(path, any_will_do=True)
        except Exception:  # noqa: BLE001 - an unanswerable search is unanswered
            return None
        return None if found is None else bool(found)

    def _found_now(self, path: str, any_will_do: bool = False) -> Optional[List[str]]:
        """The GUIDs a search finds in the document as it stands now: a
        shipped asset no mod has touched yet that the batch search matched,
        or any asset as the mods ahead have left it.  ``None`` where that
        cannot be said.  Raises what the batch search raises.

        One join for a search condition and a search-targeted op, which had
        one each: the op's passed over an asset the search could not be read
        against, and answered where an unread op had been, so one search
        came to nothing as an op's target and was not answered as a condition.
        ``any_will_do`` stops at the first found.
        """
        if self.search is None:
            return None
        found = [guid for guid in self.search(path) if guid not in self.sheets]
        if found and any_will_do:
            return found
        for guid, sheet in self.sheets.items():
            hit = matches_anything(sheet.working, path)
            if hit is None:
                return None
            if hit:
                found.append(guid)
                if any_will_do:
                    return found
        if found:
            return found
        # Nothing, in the file as the Studio has it -- which is not the file as
        # the game has it wherever an op the Studio could not read has been.
        # *BT Quests Instead Preferred* fills a pool by copying a list, which
        # the Studio cannot read, and then adds quests only if that pool has
        # entries: answered from the empty pool, the quests were never added,
        # and the export left them out in game as well.
        #
        # Only for a search that names its assets. One that names none could
        # reach an unread op's asset as easily as not, and holding back every
        # such answer once anything at all was unread left 157 more census ops
        # unread, most of them answered rightly before.
        if self.unread_at & set(_NAMED_GUID.findall(path)):
            return None
        return found

    def _note_unread(self, result: OpResult) -> None:
        """Where an op the Studio could not read, all or part of, was aimed."""
        if (result.status == BLOCKED or result.partial) and result.op.guid and not result.op.searched:
            self.unread_at.update(result.op.guids)

    # -- the whole file -------------------------------------------------
    def run_all(self, ops, progress=None, stage=None) -> List[OpResult]:
        """Every op in load order, settling each mod's payloads as it ends.

        ``progress(position, total)`` is called as the ops are translated and
        ``stage(text)`` as the work changes shape -- this is the long half of
        reading a mods folder, and without them the caller can only say it is
        doing something.
        """
        # Numbered within their own mod as they run: what comes out the far
        # end is written as one file per mod, so a change's position is only
        # meaningful relative to the rest of its own, and a loop's later
        # rounds come after its first.
        self._numbered = {}
        self._total, self._done, self._progress = len(ops), 0, progress
        results: List[OpResult] = []
        running = None
        for item in _nest(ops):
            first = item.scope if isinstance(item, _Nest) else item
            if running is not None and first.mod != running:
                self._settle(running)
            running = first.mod
            self._walk([item], results)
        self._settle(running)
        if progress is not None:
            progress(len(ops), len(ops))
        # A field set twice by one mod can end up back where it started; that is
        # nothing to write down.
        for result in results:
            result.changes = [c for c in result.changes if not c.is_noop]
        self._check_the_reading(results)
        # Only now what the ops written switched off would come to: none of
        # it is something the mod does, so none of it was there to check.
        for result in results:
            if result.shelved:
                result.changes = list(result.shelved)
        self._not_its_own_words(results)
        # How many changes an op came to, counted last, once nothing more
        # is dropped as coming to nothing.
        kept: Dict[str, int] = {}
        for result in results:
            for change in result.changes:
                if change.op_uid:
                    kept[change.op_uid] = kept.get(change.op_uid, 0) + 1
        for found in self.found.values():
            for record in found:
                record.made = kept.get(record.uid, 0)
        for result in results:
            if result.record is not None and _says_what_its_op_says(result):
                result.record.plain = True
            if (
                result.record is not None
                and result.status == SETTLED
                and result.op.condition
                and not result.op.scope
                and not result.op.inside_kept
                and _did_not_run(result.reason)
            ):
                # Its own condition did not hold, for every asset it names:
                # what the row of a conditional op says of today.
                result.record.idle = True
        return results

    def _settle(self, origin) -> None:
        if origin is None:
            return
        for sheet in list(self.sheets.values()) + self.orphans:
            sheet.settle(origin)

    # -- the nest -------------------------------------------------------
    def _walk(self, items, results: List[OpResult]) -> None:
        """Run ``items`` of :func:`_nest`, in order, into ``results``."""
        for item in items:
            if isinstance(item, _Nest):
                self._walk_scope(item, results)
            else:
                self._visit(item, results)

    def _visit(self, op: SourceOp, results: List[OpResult]) -> None:
        op.order = self._numbered.get(op.mod, 0)
        self._numbered[op.mod] = op.order + 1
        if self._progress is not None and not self._done % 25:
            self._progress(min(self._done, self._total), self._total)
        self._done += 1
        if op.attrs.get(modops.INCLUDES):
            self._include_line(op)
            return
        if op.attrs.get(modops.ASIDE):
            self._aside(op)
            return
        result = self.run(op)
        # Nothing is said about what sits inside a scope that is not read:
        # the scope is one thing, and its own row says what.
        if result is not None:
            results.append(result)
            self._note_unread(result)

    def _include_line(self, op: SourceOp) -> None:
        """Keep an ``<Include>`` line whose file is kept as a file: in its
        place among the ops of the file it stands in, and no ModOp.  It
        does nothing of its own -- the file's ops are read where they
        stand, straight after it -- so nothing is said of it."""
        named = op.attrs[modops.INCLUDES]
        self.found.setdefault(op.mod, []).append(Op(
            expression=op.path,
            source=op.verbatim,
            # Translators: one line for an <Include> in a list of a mod's
            # ModOps. %s is the path of the file it names.
            summary=tr_noop("includes %s", context=STORED) % named,
            origin=op.mod,
            note=op.comment,
            order=op.order,
            file=op.file,
            gap=op.gap,
            includes=named,
            searches=False,
            scope=True,
        ))

    def _aside(self, op: SourceOp) -> None:
        """Keep a comment that is no op's note where it stands among the
        ops.  Not one inside a group that goes back out whole, which is in
        the group as it is written; nor in one that is not read."""
        state = self._state_of(op.scopes)
        if state in _SCOPES_NOT_READ or state == SCOPE_KEPT_ON:
            return
        self.found.setdefault(op.mod, []).append(Op(
            expression=op.path,
            source=op.verbatim,
            summary=tr_noop("a comment", context=STORED),
            origin=op.mod,
            order=op.order,
            file=op.file,
            gap=op.gap,
            aside=True,
            searches=False,
            scope=True,
            within=[
                self.scope_ops[scope].uid for scope in op.scopes
                if self.scopes.get(scope) in _SCOPES_SPLIT and scope in self.scope_ops
            ],
        ))

    def _walk_scope(self, nest: _Nest, results: List[OpResult]) -> None:
        """A scope, entered once, and what is inside it run inside it.

        One that does not run passes everything in it by, unasked. A loop
        (``MaxRepeat``, Anno 117) runs its ops again while its condition
        holds, at most as many rounds as it says -- as the loader does,
        measured with xmltest3: the first of five items removed five times
        under ``MaxRepeat="10"``, an addition made while fewer than three
        made three times, and a cap of two stopping at two. Each round is the
        ops again, read afresh against the document the round before left.
        """
        scope = nest.scope
        self._visit(scope, results)
        state = self._state_of((*scope.scopes, scope.scope))
        if state in _SCOPES_NOT_READ:
            return
        self._walk(nest.inside, results)
        if scope.rounds < 2 or state != SCOPE_KEPT_ON:
            return
        self._repeating += 1
        try:
            for _round in range(scope.rounds - 1):
                if not self._again(scope):
                    break
                self._walk(_afresh(nest.inside), results)
        finally:
            self._repeating -= 1

    def _again(self, scope: SourceOp) -> bool:
        """Whether a loop runs another round: its condition, asked again of
        the document as the last round left it."""
        if not scope.condition:
            return True
        held = condition_holds(
            scope.condition, self._live, "", self.present or None, self.chosen.get(scope.mod),
            scope.game, search=self._search_holds, in_document=True,
        )
        if held is None:
            record = self._scope_op((*scope.scopes, scope.scope))
            if record is not None:
                record.caveat = _also(
                    record.caveat,
                    tr_noop("a group inside it asks something the Studio cannot answer "
                            "(%s), so what that one does is not shown underneath",
                            context=STORED) % scope.condition,
                )
        return bool(held)

    def run(self, op: SourceOp) -> Optional[OpResult]:
        state = self._state_of(op.scopes)
        if op.scopes:
            op.within = tuple(
                self.scope_ops[scope].uid
                for scope in op.scopes
                if self.scopes.get(scope) in _SCOPES_SPLIT and scope in self.scope_ops
            )
        if op.scope:
            return self._enter(op, state)
        if state in _SCOPES_NOT_READ:
            return None
        if state == SCOPE_SPLIT_OFF:
            return self._idle(op)
        if state == SCOPE_KEPT_ON:
            if op.verbatim and not op.attrs.get("adds"):
                return None  # part of what the scope carries, and not an op
            op.inside_kept = True
        result = self._attempt(op)
        if state == SCOPE_KEPT_ON:
            self._held_by_its_scope(op, result)
        return result

    def _idle(self, op: SourceOp) -> OpResult:
        """An op inside a group that does not run today, read as written: it
        is not run, and it is still the op it is, coming to nothing."""
        if op.shelved:
            return self._shelved(op)
        why = tr_noop("the group it is in does not run today", context=STORED)
        if op.verbatim or not op.kind:
            return OpResult(op, CARRIED, why)
        result = OpResult(op, SETTLED, why)
        result.record = self._record(op, op.guids, kept=self.keeps(op))
        return result

    def _split(self, op: SourceOp, running: bool, kept: str) -> OpResult:
        """Keep a group as a group with ops in it -- see SCOPE_SPLIT_ON."""
        self.scopes[op.scope] = SCOPE_SPLIT_ON if running else SCOPE_SPLIT_OFF
        result = OpResult(op, SETTLED, _kept_as_written(kept))
        result.record = self._record(op, [], kept=kept)
        result.record.idle = not running
        self.scope_ops[op.scope] = result.record
        return result

    def _unchecked(self, op: SourceOp) -> str:
        """Why ``op``'s condition was not answered, where it can be said:
        a setting it reads that its mod does not declare. The loader reads
        such a name as false; a mod written here is usually one whose op came
        before the declaration, and this is the place that says so."""
        known = self.chosen.get(op.mod) or {}
        missing = [name for name in SETTING.findall(op.condition) if name not in known]
        if missing:
            return tr_noop(
                "its condition reads %s, which the mod does not declare as an option",
                context=STORED,
            ) % ", ".join("$" + name for name in dict.fromkeys(missing))
        return tr_noop("its condition could not be checked: %s", context=STORED) % op.condition

    def _enter_as_written(self, op: SourceOp, outer: str) -> OpResult:
        """:meth:`_enter` for a mod read as written: the same questions, and
        every answer but "cannot say" keeps the group as a group."""
        if outer == SCOPE_SPLIT_OFF:
            return self._split(op, False, tr_noop(
                "the group it is in does not run today", context=STORED
            ))
        if op.attrs.get("skip"):
            return self._split(op, False, tr_noop(
                "the mod ships this group switched off, so nothing in it runs",
                context=STORED,
            ))
        held = condition_holds(
            op.condition, self._live, "", self.present or None, self.chosen.get(op.mod),
            op.game, search=self._search_holds, in_document=True,
        ) if op.condition else True
        if held is None:
            self.scopes[op.scope] = SCOPE_CARRIED
            return OpResult(
                op,
                BLOCKED,
                op.attrs.get("why")
                or tr_noop("everything in this group runs only if %s, which is not a "
                           "question the Studio can answer, so none of it is read",
                           context=STORED) % op.condition,
            )
        label = op.attrs.get("label")
        asks = (
            tr_noop("which other mods are loaded", context=STORED)
            if asks_the_loadout(op.condition, op.game)
            else tr_noop("one of the mod's own settings", context=STORED)
            if asks_a_setting(op.condition)
            else ""
        )
        if asks:
            said = (
                tr_noop("everything in it runs only if %(condition)s, which depends "
                        "on %(asks)s", context=STORED)
                if held
                else tr_noop("everything in it runs only if %(condition)s, which depends "
                             "on %(asks)s, and as things stand now none of it does",
                             context=STORED)
            )
            kept = said % {"condition": op.condition, "asks": asks}
        elif label and not op.condition:
            kept = tr_noop("it is named %s, which a tweak tool uses to find it",
                           context=STORED) % label
        elif label and held:
            kept = tr_noop("it is named %(label)s, which a tweak tool uses to find "
                           "it, and everything in it runs only if %(condition)s",
                           context=STORED) % {"label": label, "condition": op.condition}
        elif label:
            kept = tr_noop("it is named %(label)s, which a tweak tool uses to find "
                           "it, and everything in it runs only if %(condition)s, "
                           "which it does not",
                           context=STORED) % {"label": label, "condition": op.condition}
        else:
            kept = (
                tr_noop("everything in it runs only if %s", context=STORED)
                if held
                else tr_noop("everything in it runs only if %s, and as things stand "
                             "now none of it does", context=STORED)
            ) % op.condition
        return self._split(op, bool(held), kept)

    def _attempt(self, op: SourceOp) -> OpResult:
        try:
            return self._run(op)
        except Unrepresentable as exc:
            return OpResult(op, BLOCKED, str(exc))

    # -- conditioned scopes ---------------------------------------------
    def _state_of(self, scopes) -> str:
        """What the scopes an op sits in, outermost first, come to for it."""
        found = [self.scopes.get(scope, SCOPE_READ) for scope in scopes]
        for state in found:
            if state in _SCOPES_NOT_READ:
                return state
        if SCOPE_SPLIT_OFF in found:
            return SCOPE_SPLIT_OFF
        if SCOPE_KEPT_ON in found:
            return SCOPE_KEPT_ON
        if SCOPE_SPLIT_ON in found:
            return SCOPE_SPLIT_ON
        return SCOPE_READ if found else ""

    def _scope_op(self, scopes: tuple) -> Optional[Op]:
        """The kept op the changes of an op inside ``scopes`` hang off, when one
        of them is a kept scope: the innermost such."""
        for scope in reversed(scopes):
            if scope in self.scope_ops:
                return self.scope_ops[scope]
        return None

    def _within(self, op: SourceOp, record: Op) -> tuple:
        """The groups ``op`` sits in inside ``record``'s own, outermost first."""
        names: List[str] = []
        started = False
        for scope in op.scopes:
            if self.scope_ops.get(scope) is record:
                started, names = True, []
                continue
            if started and scope in self.scope_labels:
                names.append(self.scope_labels[scope])
        return tuple(names)

    def _note_part(self, op: SourceOp, off: bool) -> None:
        """Record a group inside a kept one on that one's op."""
        record = self._scope_op(op.scopes)
        if record is None:
            return
        label = describe_op(op)
        self.scope_labels[op.scope] = label
        part = {"path": [*self._within(op, record), label], "off": off}
        named = op.attrs.get("label", "")
        if named.startswith("ModOpID="):
            # What a tweak tool finds it by, so its switch can name the row.
            part["id"] = named.split("=", 1)[1]
        record.parts.append(part)

    def _enter(self, op: SourceOp, outer: str) -> Optional[OpResult]:
        """Put a scope's question, once, the way the loader does on the way in.

        ``<Group Condition="…">`` and a file included on a condition are the
        same thing to the loader: it asks once, and everything inside runs or
        does not.  Asking per op instead would be wrong as well as slower -- the
        ops inside may change the very thing the question is about.

        A group is kept as a group with ops in it (:meth:`_enter_as_written`).
        A loop, and a group inside one that goes back out whole, are kept
        whole: every round is read inside it (:meth:`_walk_scope`), and an op
        inside is not one op's worth of changes but a round's.  One that
        cannot be answered is carried whole, unread, as it always was.
        """
        if outer in _SCOPES_NOT_READ:
            self.scopes[op.scope] = outer
            return None
        if op.shelved and outer != SCOPE_KEPT_ON:
            # A group the Studio wrote switched off, its two tags each in a
            # comment: a group kept as a group, that does not run, and off.
            result = self._split(op, False, tr_noop(
                "the Studio wrote it switched off", context=STORED
            ))
            result.status = SHELVED
            if result.record is not None:
                result.record.active = False
            return result
        repeats = op.attrs.get("repeat")
        if outer != SCOPE_KEPT_ON and not repeats:
            return self._enter_as_written(op, outer)
        if op.attrs.get("skip") and outer == SCOPE_KEPT_ON:
            # Switched off inside a group that already goes back out whole: it
            # goes out as part of that one, so it is a row under the group and
            # not a kept op of its own. It used to be both, and was written twice
            # -- two copies of one iModYourAnno switch, which switched on would
            # have run everything inside it twice.
            self.scopes[op.scope] = SCOPE_KEPT_OFF
            self._note_part(op, off=True)
            return None
        if op.attrs.get("skip"):
            # Any Skip at all switches a group off, whatever its value -- the
            # loader's rule, measured. Kept as written, like an op shipped the
            # same way: it is off today and a tweak tool may turn it on.
            self.scopes[op.scope] = SCOPE_KEPT_OFF
            kept = tr_noop("the mod ships this group switched off, so nothing in it runs",
                           context=STORED)
            result = OpResult(op, SETTLED, _kept_as_written(kept))
            result.record = self._record(op, [], kept=kept)
            self.scope_ops[op.scope] = result.record
            return result
        held = condition_holds(
            op.condition, self._live, "", self.present or None, self.chosen.get(op.mod),
            op.game, search=self._search_holds, in_document=True,
        ) if op.condition else True
        if outer == SCOPE_KEPT_ON:
            # Inside something that already goes out whole, this one included.
            # All there is to decide is whether what is in it is read.
            self.scopes[op.scope] = SCOPE_KEPT_ON if held else SCOPE_KEPT_OFF
            if not self._repeating:
                self._note_part(op, off=held is False)
            record = self._scope_op(op.scopes)
            if held is None and record is not None:
                record.caveat = _also(
                    record.caveat,
                    tr_noop("a group inside it asks something the Studio cannot answer "
                            "(%s), so what that one does is not shown underneath",
                            context=STORED) % op.condition,
                )
            return None
        if held is None:
            self.scopes[op.scope] = SCOPE_CARRIED
            return OpResult(
                op,
                BLOCKED,
                op.attrs.get("why")
                or tr_noop("everything in this group runs only if %s, which is not a "
                           "question the Studio can answer, so none of it is read",
                           context=STORED) % op.condition,
            )
        asks = (
            tr_noop("which other mods are loaded", context=STORED)
            if asks_the_loadout(op.condition, op.game)
            else tr_noop("one of the mod's own settings", context=STORED)
            if asks_a_setting(op.condition)
            else ""
        )
        if asks:
            self.scopes[op.scope] = SCOPE_KEPT_ON if held else SCOPE_KEPT_OFF
            # One whole sentence for each answer, rather than a tail glued on.
            said = (
                tr_noop("everything in it runs only if %(condition)s, which depends "
                        "on %(asks)s", context=STORED)
                if held
                else tr_noop("everything in it runs only if %(condition)s, which depends "
                             "on %(asks)s, and as things stand now none of it does",
                             context=STORED)
            )
            kept = said % {"condition": op.condition, "asks": asks}
            result = OpResult(op, SETTLED, _kept_as_written(kept))
            result.record = self._record(op, [], kept=kept)
            self.scope_ops[op.scope] = result.record
            return result
        # A loop (Anno 117): every round is read, inside this one, and the
        # group goes back out whole -- once, as its author wrote it, and not
        # as a round-by-round list of its ops.
        self.scopes[op.scope] = SCOPE_KEPT_ON if held else SCOPE_KEPT_OFF
        if not op.condition:
            kept = tr_noop("it runs %s times over, and every round is read",
                           context=STORED) % repeats
        elif held:
            kept = tr_noop("it runs again while %(condition)s holds, at most %(times)s "
                           "times, and every round is read", context=STORED) % {
                "condition": op.condition, "times": repeats,
            }
        else:
            kept = tr_noop("it runs again while %(condition)s holds, at most %(times)s "
                           "times, and it does not hold", context=STORED) % {
                "condition": op.condition, "times": repeats,
            }
        if op.attrs.get("label"):
            kept = "; ".join((
                tr_noop("it is named %s, which a tweak tool uses to find it",
                        context=STORED) % op.attrs["label"],
                kept,
            ))
        result = OpResult(op, SETTLED, _kept_as_written(kept))
        result.record = self._record(op, [], kept=kept)
        self.scope_ops[op.scope] = result.record
        return result

    def _adds_an_asset(self, op: SourceOp) -> Optional[OpResult]:
        """An ``<Asset>`` written straight under ``<ModOps>``: the mod adds it.

        Read as the new asset it is, so that the editor has it and every later
        op aimed at it finds it -- they used to read as "no asset N in your
        assets.xml", 196 of them in the Anno 117 census -- and kept as written,
        because that is a form the Studio does not write: its own new assets go
        in beside an existing one.  ``None`` where it cannot be read as one, and
        then it is carried as it was before.
        """
        try:
            element = etree.fromstring(op.verbatim.encode("utf-8"))
        except etree.XMLSyntaxError:
            return None
        guid = (element.findtext("Values/Standard/GUID") or "").strip()
        beside = self._beside((element.findtext("Template") or "").strip())
        if not guid or not beside:
            return None
        change = self._create(replace(op, guid=beside), element, group=end_group(self._blocks))
        change.at_the_end = STRAIGHT
        kept = tr_noop(
            "an asset written straight under <ModOps>, which is a way of adding one "
            "the Studio reads and does not write", context=STORED
        )
        result = OpResult(op, IMPORTED, _kept_as_written(kept), [change])
        result.record = self._record(op, [guid], kept=kept)
        change.op_uid = result.record.uid
        return result

    def _adds_a_block(self, op: SourceOp) -> Optional[OpResult]:
        """An add under ``/AssetList/Groups[last()]`` of a ``<Group>`` holding
        whole assets: the mod adds every one of them.

        The other way an Anno 117 mod adds assets, and the only one that does
        not depend on what the file's last group happens to hold. *Black Tides*
        adds its 518 so, in the one op that is its whole patch, and read as a
        search that found nothing it was a mod with no changes in it; the ops
        *Require All Needs* aims at the assets it adds this way read as "no
        asset N in your assets.xml".

        Read as :meth:`_adds_an_asset` reads one, and kept as written for the
        same reason: the Studio's own new assets go in beside an existing one.
        Measured with xmltest3 and xmltest2: the group lands after everything
        the game ships, however deep its assets sit in it, and a later op finds
        them by GUID and by search.

        ``None`` where it is not that, and then it is the search it was before.
        One of those is a block repeating a GUID something already has: which
        of the two a later op then finds is not a question of where they stand
        alone -- the builds edited the shipped one after a plain block and the
        block's own once another op had been at its neighbour -- so that is
        left for the game to answer.
        """
        if op.game != games.ANNO_117 or op.kind != OP_ADD or op.searched:
            return None
        if op.path.strip() != BLOCK_PATH or op.condition or op.content or op.text:
            return None
        if has_computed(op.payload):
            return None
        assets = _block_assets(op.payload)
        if not assets or len(assets) > MAX_ASSETS_PER_SEARCH:
            return None
        guids: List[str] = []
        beside = {}
        for element in assets:
            guid = (element.findtext("Values/Standard/GUID") or "").strip()
            found = self._beside((element.findtext("Template") or "").strip())
            if not guid or not found or guid in beside:
                return None
            if guid in self.sheets or self.lookup(guid) is not None:
                return None
            guids.append(guid)
            beside[guid] = found
        changes = []
        self._blocks += 1
        for spot, element in enumerate(assets):
            guid = guids[spot]
            changes.append(self._create(
                replace(op, guid=beside[guid]), element, spot, group=end_group(self._blocks)
            ))
            changes[-1].at_the_end = IN_A_BLOCK
            # Filed beside one of its own kind for the editor's sake; where it
            # stands is after everything shipped, and after any block before it.
            self.sheets[guid].place = (math.inf, self._made, spot)
        kept = tr_noop(
            "a group of assets added under %s, which is a way of adding them "
            "the Studio reads and does not write", context=STORED
        ) % BLOCK_PATH
        result = OpResult(op, IMPORTED, _kept_as_written(kept), changes)
        # It names no GUID and is no search: its path is where the assets go,
        # and what it adds is in it.  Taken for one, its row was offered to
        # the pattern editor.
        result.record = self._record(op, guids, kept=kept, searches=False)
        for change in changes:
            change.op_uid = result.record.uid
        return result

    def _adds_to_a_list(self, op: SourceOp) -> Optional[OpResult]:
        """An add of whole assets to the list that holds an asset it names:
        ``<ModOp Type="add" Path="//Assets[Asset/Values/Standard/GUID='130248']">``
        with ``<Asset>``s in it.  The mod adds every one of them.

        Its path finds no asset.  It finds the ``<Assets>`` list one stands
        in, and what it adds there are assets.  Read as a search for assets
        it answered to none, so it was a ModOp that came to nothing and the
        assets it adds were unknown: every later op aimed at one read as
        "no asset N in your assets.xml".  618 ModOps of the census are
        written so, in 94 of its units, and the loader adds each asset
        (measured across them: the Studio's reading differed from the
        loader's by exactly those assets).

        Read as :meth:`_adds_a_block` reads a block, and kept as written
        for the same reason.  The assets are filed after the one named,
        which is in the list they are added to; the loader puts them at its
        end, and which of the two it is shows only where a GUID is there
        twice, which is left to the game as it is for a block.

        ``None`` where it is not that, and then it is the search it was.
        """
        if op.kind != OP_ADD or op.searched or op.condition or op.content or op.text:
            return None
        named = _LIST_HOLDING.match(op.path.strip())
        if named is None or has_computed(op.payload):
            return None
        beside = named.group(2)
        if beside not in self.sheets and self.lookup(beside) is None:
            return None
        assets = list(op.payload)
        if not assets or len(assets) > MAX_ASSETS_PER_SEARCH:
            return None
        guids: List[str] = []
        for element in assets:
            guid = (element.findtext("Values/Standard/GUID") or "").strip()
            if element.tag != "Asset" or not guid or guid in guids:
                return None
            if guid in self.sheets or self.lookup(guid) is not None:
                return None
            guids.append(guid)
        changes = [
            self._create(replace(op, guid=beside), element, spot)
            for spot, element in enumerate(assets)
        ]
        kept = tr_noop(
            "assets added to the list that holds asset %s, which is a way of adding "
            "them the Studio reads and does not write", context=STORED
        ) % beside
        result = OpResult(op, IMPORTED, _kept_as_written(kept), changes)
        # No search: its path is where the assets go.  See _adds_a_block.
        result.record = self._record(op, guids, kept=kept, searches=False)
        for change in changes:
            change.op_uid = result.record.uid
        return result

    def _beside(self, template: str) -> str:
        """An asset for a new one to be filed beside: one of its own kind, or any.

        Only so the change has somewhere to stand in the editor -- assets are
        found by GUID, and this one goes back out as the ``<Asset>`` it was.
        """
        if template not in self._besides:
            search = getattr(self.index, "search", None)
            found = ""
            if search is not None:
                for wanted in ([template] if template else []) + [""]:
                    try:
                        rows = search("", template=wanted, limit=1)
                    except Exception:  # noqa: BLE001 -- an index that cannot say is not a crash
                        rows = []
                    if rows:
                        found = str(rows[0].guid)
                        break
            self._besides[template] = found
        return self._besides[template]

    def _held_by_its_scope(self, op: SourceOp, result: OpResult) -> None:
        """An op inside a scope that goes out whole: its changes hang off that."""
        record = self._scope_op(op.scopes)
        if record is None:
            return
        result.record = record
        within = self._within(op, record)
        for change in result.changes:
            change.op_uid = record.uid
            change.within = within
            if change.guid not in record.matched:
                record.matched.append(change.guid)
        if result.status == BLOCKED:
            record.caveat = _also(
                record.caveat,
                tr_noop("the Studio could not read everything inside it, so not all of "
                        "what it does is shown underneath", context=STORED),
            )
            for guid in (part.strip() for part in (op.guid or "").split(",")):
                if guid and guid not in record.unread_at:
                    record.unread_at.append(guid)

    def _run(self, op: SourceOp) -> OpResult:
        """One op: what is asked of the op itself, once, and then each place
        it starts from (:meth:`_starting_points`).

        Whether an op goes back out as itself is a question about the op, so it
        is not asked of each asset it names.  It used to be: an op naming five
        assets, with one of them read coarsely, had *that part* kept as an op
        of its own -- and a kept op's source is the op, all five GUIDs of it.
        So the op went out whole for the sake of one asset while the other four
        were written narrow as well, and got everything twice (*Hackner's Trade Union
        Redesign*: twenty-two items with a second ``AdditionalOutput``).
        """
        if op.shelved:
            return self._shelved(op)
        if op.verbatim and op.attrs.get("adds"):
            added = self._adds_an_asset(op)
            if added is not None:
                return added
        if op.verbatim:
            return OpResult(op, *_why_set_aside(op))
        if is_skipped(op.attrs):
            # Present in the file and switched off. Reading it as something the
            # mod does would show a change the game never makes -- but it is
            # still something the mod ships, for a tweak tool to switch on, so
            # it is kept as itself with nothing hanging off it.
            result = OpResult(
                op, SETTLED,
                tr_noop("the mod ships it switched off (Skip)", context=STORED),
            )
            if not op.searched:
                result.record = self._record(
                    op,
                    op.guids,
                    kept=tr_noop("the mod ships it switched off (Skip), so it does nothing "
                                 "until a tweak tool turns it on", context=STORED),
                )
            return result
        if not op.kind:
            named = op.attrs.get("Type") or ", ".join(sorted(op.attrs))
            if not named:
                # Said whole: "nothing" is the Studio's word, not the mod's.
                return OpResult(op, BLOCKED, tr_noop(
                    "nothing is not an op type the Studio reads", context=STORED
                ))
            if not loader_knows_type(op.attrs, op.game):
                # An "Unknown ModOp" to the game, which does nothing with it:
                # ``Type="append"``, or the short form in Anno 1800.
                return OpResult(op, BLOCKED, tr_noop(
                    "%s is not an op type the game reads, so the game passes it by",
                    context=STORED,
                ) % named)
            return OpResult(op, BLOCKED, tr_noop(
                "%s is not an op type the Studio reads", context=STORED
            ) % named)
        if recording.holds_a_mod_item(op) and op.kind != "merge":
            # <ModItem> is read under a merge and means nothing anywhere else:
            # under an add the loader puts a literal <ModItem> into the game's
            # data, measured. Read as an ordinary element, the op came in as
            # the addition of a node called ModItem and went back out as
            # exactly that -- true to the file, and only by accident.
            return OpResult(
                op,
                BLOCKED,
                tr_noop("it has a <ModItem> under a %s, where the loader "
                        "takes it for an ordinary element and not an entry "
                        "to find", context=STORED)
                % (op.kind or "op"),
            )
        if recording.holds_a_mod_item(op) and op.game == games.ANNO_1800:
            # The 1800 loader does not know what one is: the whole merge does
            # nothing at all, measured. Carried, so it goes on doing nothing.
            return OpResult(
                op,
                BLOCKED,
                tr_noop("it reaches a list entry with <ModItem>, which Anno 1800's loader "
                        "does not read: the op does nothing in game", context=STORED),
            )
        if op.kind not in ("merge", OP_REMOVE) and _has_flags(op.payload):
            return OpResult(op, BLOCKED, _FLAGS_OUTSIDE_A_MERGE)
        if not op.guid:
            added = self._adds_a_block(op) or self._adds_to_a_list(op)
            return added if added is not None else self._run_search(op)
        if not op.searched and reaches_other_assets(op.path):
            # ``/Values/Culture | //Values[Standard/GUID='1999002127']/Culture``:
            # the second half searches the whole file, and both loaders,
            # measured, change that asset as well as the one named. The Studio
            # reads an op one asset at a time, so *Hacienda Overhaul*'s second
            # asset went missing from the export; it goes back out as written.
            return OpResult(
                op, BLOCKED,
                tr_noop("part of its path searches the whole file (%s), so it reaches assets "
                        "besides the one it names", context=STORED) % op.path,
            )

        if not op.searched:
            twice = [guid for guid in op.guids if guid in self.apart]
            if twice:
                # Not read rather than read wrong: see _stands_apart.
                return OpResult(
                    op, BLOCKED,
                    tr_noop("asset %s is in the file twice by now, in two of its top-level "
                            "groups, and which of the two an op finds depends on the op "
                            "before it", context=STORED) % twice[0],
                )

        wanted = op.guids
        result = self._starting_points(op, wanted)
        if result.status == SETTLED and result.allowed_miss and not op.searched:
            # With what its work went into, where it found its node in one
            # asset and another op's addition holds what it did there.
            result.record = self._record(op, op.guids, 0, result.folded, kept=ALLOWED_MISS)
            return result
        if result.status == SETTLED and not op.searched:
            # Switched off by one of the mod's own settings, or by a mod that is
            # not loaded -- or on, and with nothing left to do. That is the
            # game's question to go on asking, so the op is kept as itself with
            # nothing hanging off it -- the way one shipped with Skip is --
            # rather than read once at today's answer and dropped from the mod
            # for good.
            if asks_a_setting(op.condition):
                result.record = self._record(
                    op,
                    op.guids,
                    kept=tr_noop("whether it runs is one of the mod's own settings, and "
                                 "at the setting it was read at it does nothing",
                                 context=STORED),
                )
                return result
            if asks_the_loadout(op.condition, op.game):
                result.record = self._record(
                    op,
                    op.guids,
                    kept=tr_noop("whether it runs depends on which other mods are loaded, "
                                 "and with the ones loaded now it does nothing",
                                 context=STORED),
                )
                return result
            # The op goes back out as itself, with nothing of its own to
            # show: what it does went into something this mod had just
            # added, or is already so. *Celtic Independence Overhaul* fills
            # two asset pools it adds with ``Content=`` copies of the game's
            # own, and exported without those ops the pools were empty.
            result.record = self._record(op, op.guids, 0, result.folded, kept=self.keeps(op))
            return result
        if op.searched or result.status != IMPORTED:
            return result
        kept = self.keeps(op)
        # What is shown under it that is not the op's own reading, said
        # whatever else is said of it: an op that works its value out *and*
        # is shown as a whole node swapped has the first for its reason.
        wide = []
        if result.coarse:
            wide.append(tr_noop(
                "the field-by-field reading cannot express it (%s), so what is "
                "shown swaps the whole node", context=STORED
            ) % result.coarse)
        if result.allowed_miss:
            # Some of the assets it names have what it is after and some do
            # not. As itself, once, it goes on asking of all of them. Once per
            # asset that missed, it went out three times over and *S.L.O.P. -
            # Attributes* halved its Belief values three times.
            wide.append(ALLOWED_MISS)
        if not kept and wide:
            kept = wide[0]
        # The changes are what it comes to today, and the op itself is what
        # goes back out.
        self._keep(result, wanted, kept).wide = "; ".join(wide)
        return result

    @staticmethod
    def _not_its_own_words(results) -> None:
        """Drop a note that is only what the Studio says of the change.

        An export made before the Studio marked its comments put ``Radius:
        10 -> 25`` above an op the way an author would, and read back it is
        the op's comment.  It says nothing the next export does not say
        again, so nothing is lost by it -- whoever wrote it.
        """
        for result in results:
            for change in result.changes:
                if change.note and change.note in generator.said_before_the_mark(change):
                    change.note = ""

    def _shelved(self, op: SourceOp) -> OpResult:
        """An op the Studio wrote switched off, back as it was and still off.

        It is not run here.  The ops after it patch a document it never
        touched, in the game and so in this reading.  What it would come to is
        asked of a translator of its own, against the shipped file.

        It comes back as a kept op that is off, holding the ModOp as written,
        with the answer hanging off it -- the same thing an op read on and then
        switched off is.  There the answer is for the list, as a kept op's
        changes always are, and nothing an export is written from.
        """
        made: List[Change] = []
        if not self._own(op):
            # Inside a group kept whole, where the group's text holds it as
            # written: off there, with nothing of its own to hang an answer
            # off.  The kept op is the group's, and is not switched off for it.
            result = OpResult(
                op, SHELVED, tr_noop("the Studio wrote it switched off", context=STORED)
            )
            result.record = self._record(op, [])
            return result
        if not op.verbatim:
            try:
                made = read_one(
                    replace(op, shelved=False), self.index,
                    keep_inert=self.inert is None, chosen=self.chosen,
                    present=self.present, search=self.search, order=self.order,
                ).changes
            except Exception:  # noqa: BLE001 - it is kept as written either way
                made = []
        result = OpResult(
            op, SHELVED, tr_noop("the Studio wrote it switched off", context=STORED)
        )
        searches = not op.verbatim and not op.guids
        record = self._record(
            op,
            list(dict.fromkeys(change.guid for change in made)) if searches else op.guids,
            kept="" if searches else tr_noop(
                "the Studio wrote it switched off, and it is back as the op it was",
                context=STORED,
            ),
        )
        record.active = False
        for change in made:
            change.active = False
            change.op_uid = record.uid
            change.order = op.order
        result.record = record
        result.shelved = made
        return result

    def _keep(self, result: OpResult, wanted, kept: str) -> Op:
        """Keep an op that names its assets as itself."""
        op = result.op
        record = self._record(op, list(wanted), result.partial, result.folded, kept=kept)
        result.record = record
        for change in result.changes:
            change.op_uid = record.uid
        if kept:
            result.reason = "; ".join(
                bit
                for bit in [tr_noop("kept as written, shown at what it comes to now: %s",
                                    context=STORED) % kept,
                            result.reason]
                if bit
            )
        return record

    def keeps(self, op: SourceOp) -> str:
        """What about an op its changes, today's answer, cannot say, or "".

        The op is still *read* -- the value it comes to today is what the
        editor shows -- and it goes back out as the op it was, so the game
        goes on working the value out.
        """
        if op.inside_kept:
            return tr_noop("it is inside a group that goes back out as written",
                           context=STORED)
        if "Content" in op.attrs:
            return tr_noop("it copies its value from another asset as it patches",
                           context=STORED)
        if has_computed(op.content or op.payload):
            if only_flags(op.content or op.payload):
                return tr_noop("it adds or takes out flags in a list as it patches",
                               context=STORED)
            return tr_noop("it works its value out from the mod's own settings as it "
                           "patches", context=STORED)
        if asks_a_setting(op.condition):
            return tr_noop("whether it runs is one of the mod's own settings",
                           context=STORED)
        if asks_the_loadout(op.condition, op.game):
            # Never settled, and there is no box for it. A condition about the
            # game's files is answered once because the files do not change
            # under a mod; which other mods are loaded does, and an op written
            # back without the question goes on running after the mod it asked
            # about has been taken out.
            return tr_noop("whether it runs depends on which other mods are loaded",
                           context=STORED)
        return ""

    def _content_payload(self, op: SourceOp) -> tuple:
        """What a ``Content=`` op copies, as the file reads right now.

        A value where it names a field, a whole section where it names one --
        the mod is saying "make this the same as that", and both kinds turn up.
        Either way it becomes the op's body, so the rest of the translation
        reads it as though the mod had written the value out.

        With it, the text the op adds where its ``<ModOpContent/>`` stands
        straight inside it (:func:`_copied_as_text`), and ``None`` for any
        other op.
        """
        text = (op.attrs.get("Content") or "").strip()
        expression = text[: -len("/text()")] if text.endswith("/text()") else text
        matched = AT_ASSET.match(expression)
        if matched:
            asset = self._live(matched.group(1))
            if asset is None:
                raise Uncomputable(
                    tr_noop("it copies from asset %s, which your assets.xml does not have",
                            context=STORED)
                    % matched.group(1)
                )
            found = _resolve(asset, matched.group(2) or "")
        elif expression.startswith("//") and self.search is not None:
            guids = list(self.search(expression))
            if len(guids) != 1:
                raise Uncomputable(
                    tr_noop("it copies from a search matching %d assets, so there is no "
                            "one value to settle on", context=STORED) % len(guids)
                )
            asset = self._live(guids[0])
            found = search_in(asset, expression) if asset is not None else []
        elif op.guid and not op.searched and expression.lstrip("~").startswith("/") \
                and not expression.lstrip("~").startswith("//"):
            # From the op's own asset, as its Path is read: the docs' "copying
            # local content in combination with a specific GUID", and the
            # older ``~/`` spelling of it. *Community Patch* copies each
            # item's set buff into its own costs this way, twenty ops' worth.
            # An op that searches has no asset of its own to start from.
            asset = self._live(op.guid)
            found = _resolve(asset, expression.lstrip("~")) if asset is not None else []
        else:
            raise Uncomputable(
                tr_noop("it copies from %s, which cannot be read", context=STORED) % text
            )
        found = [
            node for node in found if isinstance(getattr(node, "tag", None), str)
        ]
        if not found:
            raise Uncomputable(
                tr_noop("it copies from %s, which matches 0 nodes", context=STORED) % text
            )
        if len(found) == 1 and not len(found[0]) and not recording.text(found[0]):
            raise Uncomputable(
                tr_noop("it copies from %s, which holds nothing", context=STORED) % text
            )
        words = _copied_as_text(op, found, text.endswith("/text()"))
        if words is not None:
            return (), words
        placed = any(
            node.tag == MOD_OP_CONTENT or node.find(".//" + MOD_OP_CONTENT) is not None
            for node in op.payload
        )
        # Several matches are a longer payload, in the order they are found --
        # each in its own copy of the body, where the body has a place marked
        # for it. Both loaders, measured: *General Lifestyle* moves three needs
        # up a list with one copy, and *BT Quests Instead Preferred* fills a
        # pool with an <Item> for every good another pool lists.
        copied: List = []
        for node in found:
            if placed:
                # The op has a body of its own with a place marked for the
                # copy: <Item><Product><ModOpContent/></Product></Item>. What it
                # writes is that body, filled in -- not the copied node on its
                # own, which showed a <Radius> being added where the mod adds
                # the radius's *value* inside a node it names.
                copied.extend(_filled_in(op.payload, recording.clean(node), as_text=text.endswith("/text()")))
            else:
                copied.append(recording.clean(node))
        return tuple(copied), None

    def _insert_copies(self, wrapper, target, op: SourceOp) -> None:
        """Put in what each ``<ModValue Insert="…"/>`` naming a path copies
        (Anno 117), in place, before any arithmetic is worked out.

        Two kinds, both in the loader's docs and neither yet in a census mod:

        * ``Insert="@1500010200/Building/InfluencedVariationDirection"`` copies
          from another asset, as ``Content=`` does -- looked up in the asset
          as the mods ahead have left it.
        * ``Insert="../Standard/GUID/text()"`` copies from beside where the
          value lands: each ``../`` climbs from the ModValue's field, through
          the op's body and on into the asset it is aimed at.

        One ending in ``/text()`` puts in the text, and one landing on a
        section puts in the section.  One that finds nothing or several, that
        names a field without ``/text()``, or that wraps its copy in
        ``ModValueContent`` -- which builds lists, and ``SkipParent`` with it
        -- is not read, and the op goes back out as written.

        Text beside the mark, and what stands before it in something the op
        puts in new, count as they do for a value worked out: see
        :func:`freeze_computed`.
        """
        merging = op.kind == "merge"
        for node in list(wrapper.iter(MOD_VALUE)):
            text = (node.get("Insert") or "").strip()
            if not text.startswith(("@", "../", "./")):
                continue
            if len(node):
                raise Uncomputable(
                    tr_noop("it copies %s into a wrapper of its own (ModValueContent), "
                            "which the Studio does not read yet", context=STORED) % text
                )
            as_text = text.endswith("/text()")
            expression = text[: -len("/text()")] if as_text else text
            matched = AT_ASSET.match(expression)
            if matched:
                asset = self._live(matched.group(1))
                if asset is None:
                    raise Uncomputable(
                        tr_noop("it copies from asset %s, which your assets.xml does not "
                                "have", context=STORED)
                        % matched.group(1)
                    )
                found = _resolve(asset, matched.group(2) or "")
            else:
                found = _beside(wrapper, node, target, op.kind, expression)
            found = [hit for hit in found if isinstance(getattr(hit, "tag", None), str)]
            if len(found) != 1:
                raise Uncomputable(
                    tr_noop("it copies from %(source)s, which matches %(count)d nodes",
                            context=STORED) % {"source": text, "count": len(found)}
                )
            parent = node.getparent()
            if not as_text and not len(found[0]):
                # Read as the field's text until now.  Measured, both Anno 117
                # builds put the node where the mark is, as they do a section:
                # ``<Radius><Radius>4</Radius></Radius>``, a field holding an
                # element and no value.  The docs copy a value with
                # ``/text()``, and that is not one.
                raise Uncomputable(
                    tr_noop("it copies %s as a whole node into a field, which the loader "
                            "leaves holding that node and no value", context=STORED) % text
                )
            new = _put_in_new(wrapper, parent, target, merging)
            if new is not None:
                _worked_out_where_it_stands(node, new)
            if as_text:
                # Read as the text before, the copy and the text after run
                # together until now, which is what a ``<ModOpContent/>``
                # does and not what this does: see :func:`freeze_computed`.
                _settle_mark(node, recording.text(found[0]), new is None)
            elif _text_beside(node, before=True):
                raise Uncomputable(
                    tr_noop("it copies the section %s in beside text of its own, and what "
                            "the game makes of the two together is not known",
                            context=STORED) % text
                )
            else:
                copied = recording.clean(found[0])
                copied.tail = node.tail
                parent.replace(node, copied)

    def _payload(self, op: SourceOp, target):
        """The op's payload, with anything it computes settled if asked for."""
        if op.copied_text is not None:
            # Its body is the text it adds: the mark is nothing to add.
            return ()
        payload = op.content or op.payload
        if not has_computed(payload):
            return payload
        wrapper = etree.Element(effective.WRAPPER)
        for child in payload:
            wrapper.append(copy.deepcopy(child))
        try:
            if op.kind != "merge":
                _marks_outside_a_merge(wrapper, op.kind)
            self._insert_copies(wrapper, target, op)
            freeze_computed(wrapper, target, self.chosen.get(op.mod), merging=op.kind == "merge")
        except Uncomputable as exc:
            raise Unrepresentable(str(exc))
        if not len(wrapper) and (wrapper.text or "").strip():
            # The ModValue stood straight inside the op with no field around it,
            # ``<ModOp Merge="@140416/…/NormalGameSpeedFactor"><ModValue …/>``,
            # so what it comes to is the target's own value. Handed back as the
            # bare text it froze into, it was dropped: the merge had nothing in
            # it, fell back to swapping the whole node, and wrote back the value
            # the game already had.  Only a merge gets here: a mark straight
            # inside any other op is not read (:func:`_marks_outside_a_merge`).
            field = etree.Element(target.tag)
            field.text = wrapper.text.strip()
            return (field,)
        return tuple(wrapper)

    def _run_search(self, op: SourceOp) -> OpResult:
        """An op that says which assets it is for rather than naming them.

        ``//Values/Trader`` is a perfectly ordinary thing for a mod to write and
        an awkward thing to rebuild: which assets it means is a question about
        the whole file, not about one asset.  So the whole file is asked -- see
        :meth:`AssetIndex.find_matching` -- and the op then runs against each
        asset it turns out to be about, exactly as if it had named them.

        Which is also what makes the result better than the original: one op
        that claims every trader becomes one ModOp per trader, each addressed by
        GUID, so another mod editing a different trader no longer collides.
        """
        if not (op.path or "").strip() and not targets_otherwise(op.attrs):
            # Said as what is missing: "targets assets by search" told the
            # author nothing about an op that searches for nothing.
            return OpResult(op, BLOCKED, tr_noop(
                "it has neither a GUID nor a Path, so it names nothing to change",
                context=STORED,
            ))
        if _COUNTS_ASSETS.search(op.path):
            # Each asset is searched as a document of its own, where it is the
            # only one: ``//Asset[…GUID='1337505020'][position() < last()]``,
            # which *A_Modified_Ornaments_Tab* removes its own and other mods'
            # duplicates with, was read as matching nothing, and the Studio
            # showed the placeholder the game has removed.
            return OpResult(
                op, BLOCKED,
                tr_noop("it picks assets out by where they stand among others in the file "
                        "(%s), which the Studio does not read", context=STORED) % op.path,
            )
        each = replace(op, searched=True)
        if _bare_path(op.condition) and not targets_otherwise(op.attrs):
            # Asked once, of the whole document, as the loader asks it of an op
            # with no GUID -- not of each asset the search finds, where
            # ``/Values/…`` would start from that asset. *Lux et Industria*
            # guards three such ops with a bare path that finds nothing there.
            held = condition_holds(
                op.condition, self._live, "", self.present or None, self.chosen.get(op.mod),
                op.game, in_document=True,
            )
            if held is None:
                return OpResult(op, BLOCKED, self._unchecked(op))
            if not held:
                # Still the op it is, coming to nothing today.  Said on its
                # own kept op only: inside a group kept whole that is the
                # group's, and one op of it not running is not the group idle.
                record = self._record(op, [])
                if self._own(op):
                    record.idle = True
                result = OpResult(
                    op, SETTLED,
                    tr_noop("its condition does not hold by the time it runs", context=STORED),
                )
                result.record = record
                return result
            each = replace(each, condition="")
        if self.search is None:
            return OpResult(
                op,
                BLOCKED,
                tr_noop("targets assets by search, not by GUID, and the file "
                        "has not been searched", context=STORED),
            )
        try:
            # The search read the shipped file; by now the mods ahead of this
            # one may have made an asset match that did not, or stop matching
            # that did.
            wanted = self._found_now(op.path)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return OpResult(
                op, BLOCKED,
                tr_noop("its search could not be read: %s", context=STORED) % exc,
            )
        if wanted is None:
            return OpResult(
                op, BLOCKED,
                tr_noop("what its search finds could not be worked out from the file "
                        "as the Studio has it", context=STORED),
            )
        found = sorted(set(wanted), key=_by_number)
        if not wanted:
            # Still kept: a search that answers to nothing today is exactly
            # the one that starts answering when another mod adds an asset.
            result = OpResult(
                op, SETTLED, tr_noop("nothing in your assets.xml matches it", context=STORED)
            )
            result.record = self._record(op, found)
            return result
        if len(wanted) > MAX_ASSETS_PER_SEARCH:
            return OpResult(
                op,
                BLOCKED,
                tr_noop("it matches %d assets, which as separate ModOps is a change list "
                        "nobody could review", context=STORED) % len(wanted),
            )
        result = self._starting_points(each, found)
        if result.status == BLOCKED:
            # Not an Op: an Op says the Studio understood the ModOp and what it
            # comes to. An op it could not read is carried as an unread op
            # instead -- still written back out exactly as it arrived, but
            # shown for what it is.
            return result
        record = self._record(op, found, result.partial, result.folded)
        result.record = record
        for change in result.changes:
            change.op_uid = record.uid
        return result

    def _own(self, op: SourceOp) -> bool:
        """Whether the Op :meth:`_record` gives for ``op`` is the op's own.

        It is not for an op inside a group kept whole: that group's Op is
        what goes back out, and :meth:`_record` hands it over.  What is true of
        the one op -- it did not run, it is switched off -- is not to be
        written on it: a loop holding a working replace and a search whose
        condition did not hold was marked idle, with its change still under it.
        """
        return not (op.inside_kept and not op.scope and self._scope_op(op.scopes) is not None)

    def _record(self, op: SourceOp, found: List[str], unread: int = 0,
                folded=(), kept: str = "", searches: Optional[bool] = None) -> Op:
        """Keep an op as written, as an :class:`Op` of its own.

        The op verbatim rather than a summary of it, because an op that cannot
        be run again is only a note: the whole value of keeping a search is
        being able to put it to assets that were not there the first time.

        ``kept`` is why an op that names its assets is not settled into its
        changes -- it works its value out as it patches, so the op and not the
        value is what has to go back out.  See :meth:`keeps`.

        ``searches`` is whether it finds its assets by a search; where it is
        not said, an op that names none and is not kept verbatim does.

        For an op inside a group kept whole the group's Op is returned, with
        what the op found and what of it went unread added: see :meth:`_own`.
        """
        caveat = ""
        if unread:
            caveat = tr_noop(
                "the Studio could not read what it does to %d of the assets it "
                "matches, so those are not shown underneath", context=STORED
            ) % unread
        if not self._own(op):
            # Not an Op of its own: the scope it is in is what goes back out,
            # and a second Op for something inside it would write that twice.
            held = self._scope_op(op.scopes)
            assert held is not None
            for guid in found:
                if guid not in held.matched:
                    held.matched.append(guid)
            if caveat:
                # Said of the group, as it is of an op that stands alone: left
                # out, the group's row showed part of what it does as all of it.
                held.caveat = _also(held.caveat, caveat)
            return held
        # A group kept as a group with ops in it holds its own tag only: what
        # is inside it is each an op of its own.
        splitting = bool(op.scope) and self.scopes.get(op.scope) in _SCOPES_SPLIT
        label = op.attrs.get("label", "") if splitting else ""
        record = Op(
            expression=op.path,
            source=opening(op.verbatim) if splitting else render_op(op),
            summary=describe_op(op),
            origin=op.mod,
            note=op.comment,
            order=op.order,
            file=op.file,
            gap=op.gap,
            matched=list(found),
            caveat=caveat,
            folded=[list(key) for key in dict.fromkeys(folded)],
            kept=kept,
            searches=(not op.guid and not op.verbatim) if searches is None else searches,
            scope=splitting,
            within=list(op.within),
            tool={"id": label.split("=", 1)[1]} if label.startswith("ModOpID=") else {},
        )
        self.found.setdefault(op.mod, []).append(record)
        return record

    def _starting_points(self, op: SourceOp, guids: List[str]) -> OpResult:
        """An op run from each asset it names, one after the other, as the
        loader runs one: for each GUID as written, its condition, then its
        ``Content``, then its path (:meth:`_from`).

        Each asset gets its own answer -- the same edit can be new to one and
        already true of another -- and they are reported as the one ModOp they
        were written as. Nothing asked of the op itself is asked again here.
        """
        # Unless what it adds is a copy: ``Content=`` is its payload, fetched
        # for each asset. *General Lifestyle* moves three needs up a list by
        # copying them in ahead and removing the second of each; read as
        # empty, the copies never landed, the removals found only the one of
        # each and were dropped as already done, and in game every need was
        # there twice.
        if (
            op.kind in (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV)
            and not (op.payload or op.text)
            and "Content" not in op.attrs
        ):
            return OpResult(
                op, SETTLED, tr_noop("it has nothing in it to add", context=STORED)
            )
        if guids == [op.guid]:
            return self._from(op)
        parts = [self._from(replace(op, guid=guid)) for guid in guids]
        changes = [change for part in parts for change in part.changes]
        folded = tuple(guid for part in parts for guid in part.folded)
        # Coarse for one of them is coarse for the op: it is written once.
        coarse = next((part.coarse for part in parts if part.coarse), "")
        allowed_miss = any(part.allowed_miss for part in parts)
        notes = []
        unread = 0
        for status in (SETTLED, BLOCKED):
            named = [part.op.guid for part in parts if part.status == status]
            if status == BLOCKED:
                unread = len(named)
            if named and len(named) < len(parts):
                shown = ", ".join("'%s'" % guid if _spaced(guid) else guid for guid in named)
                # A whole sentence for each thing a note can say.
                if status == SETTLED:
                    note = tr_noop("already true of %s", context=STORED) % shown
                elif any(_spaced(guid) for guid in named):
                    # Which is not enough here: a GUID with a space beside it
                    # reads as one that is simply missing, and the asset is
                    # plainly there in the file.
                    note = tr_noop("not read for %(guids)s: %(why)s", context=STORED) % {
                        "guids": shown, "why": SPLIT_ON_COMMAS,
                    }
                else:
                    note = tr_noop("not read for %s", context=STORED) % shown
                notes.append(note)
        if changes:
            first = next(part for part in parts if part.changes)
            reason = "; ".join(
                bit for bit in [first.reason] + notes if bit
            )
            return OpResult(
                op, IMPORTED, reason, changes, unread, folded=folded, coarse=coarse,
                allowed_miss=allowed_miss,
            )
        if any(part.status == BLOCKED for part in parts):
            reasons = []
            for part in parts:
                if part.status == BLOCKED and part.reason not in reasons:
                    reasons.append(part.reason)
            return OpResult(op, BLOCKED, "; ".join(reasons))
        return OpResult(
            op, SETTLED, parts[0].reason if parts else "", folded=folded,
            allowed_miss=allowed_miss,
        )

    def _from(self, op: SourceOp) -> OpResult:
        """An op from one starting point, the asset ``op.guid`` names: its
        condition, then its ``Content``, then its path, in the loader's order
        (117 L805-830), and what it does to each node the path finds."""
        try:
            return self._run_one(op)
        except Unrepresentable as exc:
            return OpResult(op, BLOCKED, str(exc))

    def _run_one(self, op: SourceOp) -> OpResult:
        sheet = self.sheet(op.guid)
        if sheet is None:
            if _spaced(op.guid) and self.sheet(op.guid.strip()) is not None:
                # The mod's own slip, and one the game says nothing about:
                # worth naming, because the asset is plainly there in the file.
                return OpResult(
                    op,
                    BLOCKED,
                    tr_noop("%(why)s: it looks for an asset called "
                            "'%(guid)s' and finds none, so this op never "
                            "reaches asset %(asset)s",
                            context=STORED)
                    % {"why": SPLIT_ON_COMMAS, "guid": op.guid, "asset": op.guid.strip()},
                )
            # Quoted when it carries a space, so that the space can be seen:
            # *Easy Seed Search* names ``1742004000 ``, an asset this file does
            # not have either way, and unquoted the reason read as a typo of
            # its own.
            return OpResult(
                op,
                BLOCKED,
                tr_noop("there is no asset %s in your assets.xml", context=STORED)
                % ("'%s'" % op.guid if _spaced(op.guid) else op.guid),
            )

        # Where a bare path in it starts: the loader builds the condition
        # from the op's own GUID attribute, so an op naming its asset only as
        # ``Merge="@1234/…"`` has none, and the path is read in the document.
        named = bool((op.attrs.get("GUID") or "").strip())
        held = condition_holds(
            op.condition,
            self._live,
            op.guid,
            self.present or None,
            self.chosen.get(op.mod),
            op.game,
            search=self._search_holds,
            in_document=not named and not targets_otherwise(op.attrs),
            from_values=("Type" not in op.attrs) if named else None,
        )
        if held is None:
            return OpResult(op, BLOCKED, self._unchecked(op))
        if not held:
            return OpResult(
                op, SETTLED,
                tr_noop("its condition does not hold by the time it runs", context=STORED),
            )

        # After the condition, not before it: a mod guards a copy with "only if
        # there is anything to copy", and an op that does not run is settled
        # rather than something the Studio could not read.
        #
        # Always fetched, whatever the settle setting says: the setting decides
        # whether the op goes back out as the copy or as the value (see
        # :meth:`keeps`), not whether the editor gets to see what the copy
        # comes to. The same goes for a value computed from the mod's own
        # settings, which :meth:`_payload` settles below.
        if "Content" in op.attrs:
            try:
                content, words = self._content_payload(op)
            except Uncomputable as exc:
                return OpResult(op, BLOCKED, str(exc))
            op = replace(op, content=content, copied_text=words)

        targets = (
            search_in(sheet.working, op.path)
            if op.searched
            else _resolve(sheet.working, op.path)
        )
        if not targets:
            if op.kind == OP_REMOVE and op.guid in self.unread_at:
                # Absent from the Studio's picture, which is not the game's
                # here: an op the Studio could not read changed this asset
                # first, and may have put it there. Dropped as already done, a
                # removal the game carries out went missing from the export.
                return OpResult(
                    op, BLOCKED,
                    tr_noop("what it removes is not in asset %s as the Studio has it, but "
                            "an op the Studio could not read changed that asset first",
                            context=STORED) % op.guid,
                )
            if op.kind == OP_REMOVE:
                return OpResult(
                    op, SETTLED, tr_noop("what it removes is already absent", context=STORED)
                )
            if allows_no_match(op.attrs):
                # Its author said matching nothing is fine -- the loader stays
                # quiet about it too. Not a problem to look at, then; but not
                # nothing either: with other mods, or another game version, it
                # may match, so it goes back out as it was. That is decided in
                # :meth:`_run`, for the op: this may be one asset of several.
                return OpResult(
                    op, SETTLED,
                    tr_noop("it matches nothing here, which its author allows (AllowNoMatch)",
                            context=STORED),
                    allowed_miss=True,
                )
            # With no path it is the asset itself that matched nothing, and that
            # is said in words of the Studio's own, so it is a sentence of its own.
            return OpResult(
                op,
                BLOCKED,
                tr_noop("%(path)s matches nothing in asset %(guid)s", context=STORED)
                % {"path": op.path, "guid": op.guid}
                if op.path
                else tr_noop("the asset matches nothing in asset %s", context=STORED)
                % op.guid,
            )

        if op.copied_text is not None and not all(recording.is_leaf(target) for target in targets):
            # Measured, the text is left loose among the section's fields,
            # which is nothing the Studio has a place for.
            return OpResult(op, BLOCKED, _COPY_STRAIGHT_INSIDE)

        if op.kind == "merge" and _lands_elsewhere(op, targets):
            return OpResult(
                op, BLOCKED,
                tr_noop("it merges a <%s> into one that is not the first of that name where it "
                        "stands, which the loader's documentation does not cover",
                        context=STORED) % (op.content or op.payload)[0].tag,
            )

        edits: List[recording.Edit] = []
        creations: List = []
        coarse = coarse_at = ""
        for target in targets:
            # The diff walks the node as it goes, so a mismatch it only meets
            # halfway down leaves edits behind for the part it did manage.
            # Those have to go: keeping them alongside a swap of the whole node
            # means editing a node and replacing it in the same breath.
            mark = len(edits)
            try:
                self._edits_for(op, sheet, target, edits, creations)
            except Unrepresentable as exc:
                # The op says something the field-by-field diff cannot express.
                # Swapping the whole node still shows what it comes to -- but
                # it claims everything inside that node, so it is what is
                # shown, not what is written: the op is kept as itself.
                del edits[mark:]
                edits.extend(self._whole_node(op, sheet, target, exc, targets))
                if not coarse and exc.at is not None:
                    coarse_at = recording.path_in(sheet.working, exc.at)
                coarse = coarse or str(exc)

        # Every change is filed under the asset's GUID, so an op that changes
        # that GUID is not editing the asset: it is turning it into a different
        # one. *Industrial Cities* swaps two production chains' GUIDs through a
        # temporary "swap", and read as a plain value the Studio showed both
        # chains where they were while the export renamed them.
        identity = sheet.working.find("Values/Standard/GUID")
        if identity is not None and any(
            edit.node is identity
            and (edit.op == OP_REMOVE or (edit.op == OP_REPLACE and edit.new != recording.text(identity)))
            for edit in edits
        ):
            return OpResult(
                op,
                BLOCKED,
                tr_noop("changes asset %s's own GUID, and the Studio files every change "
                        "under an asset's GUID, so it cannot follow the asset to a new one",
                        context=STORED)
                % op.guid,
            )

        total = len(edits) + len(creations)
        if total > MAX_CHANGES_PER_OP:
            raise Unrepresentable(
                tr_noop("it would become %d separate changes, which is no longer a refactor",
                        context=STORED)
                % total
            )

        changes: List[Change] = []
        folded: List = []
        quiet_fold = False
        for edit in edits:
            made = self._apply(op, sheet, edit, changes, folded)
            if made is not None:
                changes.append(made)
            elif not edit.silent:
                quiet_fold = True
        for spot, element in enumerate(creations):
            changes.append(self._create(op, element, spot))
        quiet = sum(1 for edit in edits if edit.silent)
        if not changes:
            if quiet:
                return OpResult(
                    op,
                    SETTLED,
                    tr_noop("all it drops are %d entry(s) the game reads as absent already",
                            context=STORED)
                    % quiet,
                )
            if quiet_fold:
                return OpResult(
                    op,
                    SETTLED,
                    tr_noop("what it does went into the payload of a change this mod "
                            "already makes", context=STORED),
                    folded=tuple(folded),
                )
            return OpResult(
                op, SETTLED, tr_noop("your assets.xml already reads this way", context=STORED)
            )
        return OpResult(
            op,
            IMPORTED,
            self._note(op, bool(coarse), quiet),
            changes,
            folded=tuple(folded),
            coarse=coarse,
            coarse_at=coarse_at,
        )

    def _note(self, op: SourceOp, coarse: bool, quiet: int = 0) -> str:
        bits = []
        if quiet:
            # Both forms whole: "s" on the end is an English rule.
            bits.append(
                tr_noop("left out %d removal of entries the game reads as absent already",
                        context=STORED) % quiet
                if quiet == 1
                else tr_noop("left out %d removals of entries the game reads as absent "
                             "already", context=STORED) % quiet
            )
        if coarse:
            bits.append(
                tr_noop("could not be read field by field, so what is shown swaps the "
                        "whole node", context=STORED)
            )
        unknown = sorted(set(op.attrs) - KNOWN_ATTRS)
        if unknown:
            bits.append(
                tr_noop("ignored %s", context=STORED)
                % ", ".join("%s=" % name for name in unknown)
            )
        return "; ".join(bits)

    # -- what each op kind asks for -------------------------------------
    def _edits_for(self, op, sheet, target, edits, creations) -> None:
        if op.kind == OP_REMOVE:
            edits.append(recording.Edit(OP_REMOVE, target))
            return

        payload = self._payload(op, target)

        if op.kind == "merge":
            if not payload:
                raise Unrepresentable(
                    tr_noop("a merge with nothing to merge in", context=STORED)
                )
            if any(child.tag == MOD_ITEM for child in payload):
                if not all(child.tag == MOD_ITEM for child in payload):
                    raise Unrepresentable(
                        tr_noop("a merge that mixes <ModItem> with plain entries, which "
                                "are matched two different ways", context=STORED)
                    )
                recording.diff_mod_items(target, payload, op.game, edits)
                return
            wanted = _merge_wanted(payload, target, op.game)
            if recording.merges_by_position(wanted, target, op.game):
                # Same-named children are merged by position, entry for entry:
                # so the merge is carried out the way the loader carries it
                # out, and what is read is the difference it made. Removals
                # and all, none of them implied: an entry the merge overwrote
                # is gone because the mod wrote over it.
                merged = recording.clean(target)
                applying.merge(merged, payload, op.game)
                recording.diff(target, merged, edits, removals=True)
                return
            same: List = []
            recording.diff(target, wanted, edits, removals=False, same=same)
            self._agreed(op, sheet, same, edits, merging=True)
            return

        if op.kind == OP_REPLACE:
            if len(payload) != 1:
                raise Unrepresentable(
                    tr_noop("swaps one node for %d, which no single narrow op does",
                            context=STORED) % len(payload)
                )
            want = recording.clean(payload[0])
            if want.tag != target.tag:
                raise Unrepresentable(
                    tr_noop("swaps <%(old)s> for <%(new)s>, so there is no one field to "
                            "change", context=STORED)
                    % {"old": target.tag, "new": want.tag}
                )
            same = []
            recording.diff(target, want, edits, removals=True, inert=self.inert, same=same)
            self._agreed(op, sheet, same, edits, merging=False)
            return

        if op.kind in (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV):
            if not payload:
                # Adding text to a field that already says something appends to
                # it -- which is how mods extend the semicolon-separated lists
                # the game keeps in a single field.  As a narrow op that is not
                # an addition at all: it is the field's new value.
                words = op.text if op.copied_text is None else op.copied_text
                if op.kind == OP_ADD and words and recording.is_leaf(target):
                    edits.append(
                        recording.Edit(OP_REPLACE, target, new=recording.text(target) + words)
                    )
                    return
                raise Unrepresentable(
                    tr_noop("an addition with nothing to add", context=STORED)
                )
            # Whole <Asset>s placed next to an asset is how a mod introduces
            # one, and the Studio has its own kind of change for exactly that,
            # on whichever side of the anchor the mod put it. Assets are found
            # by GUID, but where a GUID repeats the game finds the first in the
            # file: *Merchants* puts two Marble Stores in before 1010315, and
            # written back as after it, the other one came first.
            if op.kind in (OP_ADD_NEXT, OP_ADD_PREV) and target is sheet.working:
                if all(child.tag == "Asset" for child in payload):
                    creations.extend(payload)
                    return
                raise Unrepresentable(
                    tr_noop("puts a <%s> beside the asset itself, which is a place only a "
                            "whole <Asset> can go", context=STORED) % payload[0].tag
                )
            cleaned = tuple(recording.clean(child) for child in payload)
            if op.kind == OP_ADD:
                # Appending is order-preserving on its own, so each entry can be
                # its own ModOp -- which is narrower, and lets one of them be
                # undone later without disturbing the rest.
                edits.extend(recording.Edit(OP_ADD, target, elements=(child,)) for child in cleaned)
            else:
                edits.append(recording.Edit(op.kind, target, elements=cleaned))
            return

        raise Unrepresentable(
            tr_noop("%s is not an op type the Studio reads", context=STORED) % op.kind
        )

    def _agreed(self, op, sheet, nodes, edits: List[recording.Edit], merging: bool) -> None:
        """The values an op sets that another mod had already set the same.

        A mod is read against the document the mods before it leave, and a
        field that reads as wanted there takes no change -- which is right
        when the game's own file reads that way, and wrong when only another
        mod's work does: what this mod was written back as then depended on
        what happened to be loaded beside it, and on its own it no longer set
        the value.  So such a field is this mod's change too, written against
        the game's file: a replace of the shipped value, or for a field the
        other mod made, the merge that makes it (see _anchored).

        Not for a field this mod set or made itself, earlier in its file.  And not
        from a merge that goes by position, where the body is carried out and
        the difference read: what it left alone is not told from what it set
        to the same.
        """
        for node in nodes:
            if sheet.value_change(node, op.mod) is not None:
                continue
            shipped = sheet.shipped(node)
            if shipped is not None:
                if recording.text(shipped) != recording.text(node):
                    edits.append(recording.Edit(OP_REPLACE, node, new=recording.text(node), agreed=True))
            elif sheet.owner(node, op.mod) is None:
                # A field another mod made. From a merge that can be aimed at
                # what the game ships, the merge; otherwise the replace it is
                # in the mod's own file, restating the value (Change.restated).
                edits.append(recording.Edit(
                    OP_REPLACE, node, new=recording.text(node), agreed=True,
                    ensure=merging and self._anchored(sheet, node, op.mod) is not None,
                ))

    def _anchored(self, sheet, node, origin: str):
        """``(anchor, the nodes from under it down to node)`` for a node that
        is in the asset only because another mod put it there, or ``None``.

        The anchor is the nearest node above it that the game ships.  A merge
        aimed there, carrying the sections on the way down, makes them where
        they are absent and goes into them where another mod made them: the
        same op whether or not that mod is loaded, where a path into its work
        matches nothing without it.

        ``None`` where a merge would not land on these nodes: one of them has
        a twin of its name, which a merge tells apart by position; or the
        first is named like the anchor, which a merge reads as the anchor
        named twice; or nothing above is the game's, as in an asset a mod
        made.  And where the mod ``origin`` made the node itself: its own
        work is there whenever it is, and a path into it is right.
        """
        if sheet.owner(node, origin) is not None:
            return None
        chain = []
        current = node
        while current is not None and sheet.shipped(current) is None:
            parent = current.getparent()
            if parent is None or len(parent.findall(current.tag)) != 1:
                return None
            chain.append(current)
            current = parent
        if current is None or not chain or chain[-1].tag == current.tag:
            return None
        if not pathmod.path_for(sheet.base, sheet.shipped(current)).path:
            return None  # the asset itself, which has no path to aim a merge at
        chain.reverse()
        return current, chain

    def _ensured_over(self, op, sheet, edit: recording.Edit) -> Optional[Change]:
        """The change for a merge setting a field another mod made, or ``None``
        where it is an ordinary replace after all."""
        if sheet.shipped(edit.node) is not None:
            return None
        found = self._anchored(sheet, edit.node, op.mod)
        if found is None:
            return None
        anchor, chain = found
        top, inner = recording.nested([member.tag for member in chain])
        inner.text = edit.new
        change = self._change(op, sheet, anchor, OP_MERGE, elements=[top])
        if change.op != OP_MERGE:
            return None
        change.payload = recording.serialise(top)
        return change

    def _whole_node(self, op, sheet, target, why, targets=()) -> List[recording.Edit]:
        """Swap ``target`` for what the op wants it to be, in one piece.

        Expressed as an insertion beside a *neighbour* plus a removal, never as
        an insertion beside the node being removed: the generated ModOps are
        written removals-first precisely so a path cannot match both the old
        node and its replacement, and that leaves nothing to anchor on here.
        """
        wanted = self._wanted(op, target)
        if not wanted:
            raise Unrepresentable(str(why))
        anchor, kind = self._anchor(target, [node for node in targets if node is not target])
        if anchor is None:
            raise Unrepresentable(
                tr_noop("%(why)s, and <%(tag)s> is the only place to put the replacement",
                        context=STORED)
                % {"why": why, "tag": target.tag}
            )
        # Removal first, matching the order the generated ModOps are written in.
        # It is also the only order in which the removal's own path can be read
        # off a tree that does not yet hold its replacement -- otherwise the two
        # are siblings, and the path has to say *which* one, positionally.
        return [recording.Edit(OP_REMOVE, target), recording.Edit(kind, anchor, elements=tuple(wanted))]

    def _wanted(self, op, target) -> list:
        """What the op wants standing where ``target`` does now."""
        if op.kind == OP_REPLACE:
            # In reverse: a replace that puts several nodes in one's place puts
            # them there last first, in both games' loaders -- X, Y, Z comes out
            # Z, Y, X, measured with xmltest, where add, addNextSibling and
            # addPrevSibling keep the author's order. *Trade Union Rebalance*
            # swaps one effect target for two, and the list order is the game's.
            return [recording.clean(child) for child in reversed(list(self._payload(op, target)))]
        if op.kind == "merge":
            payload = self._payload(op, target)
            if all(child.tag == MOD_ITEM for child in payload) and payload:
                # The same rules as the field-by-field reading, because this is
                # the same merge: shown coarsely only because no narrow op could
                # name the entry it lands on. Read the ordinary way, the
                # <ModItem> would go on show as an element to add, which is
                # what the game does with one only under an op that is not a
                # merge. *Sandarac Nursery as Plantation* has exactly this
                # shape -- its merge leaves two entries alike, so the reading
                # cannot be narrow, and it was shown holding a <ModItem>.
                return [recording.merged_items(target, payload, op.game)]
            merged = recording.clean(target)
            applying.merge(merged, payload, op.game)
            return [merged]
        return []

    def _anchor(self, target, swapped=()):
        """A sibling to hang the replacement off, and which side to put it on."""
        return recording.anchor_of(target, swapped)

    # -- carrying an edit out -------------------------------------------
    def _apply(
        self, op: SourceOp, sheet: recording.Sheet, edit: recording.Edit, made_here=(), folded=None,
    ) -> Optional[Change]:
        """Change the working tree, and say what ModOp that took -- if any.

        Editing something this op already added takes no ModOp of its own:
        the working tree *is* the payload of the change that added it, so the
        edit lands there simply by happening.
        """
        sheet.starting(
            op,
            edit.node,
            [element.tag for element in edit.elements if isinstance(element.tag, str)]
            if edit.op == OP_ADD
            else (),
        )
        owner = sheet.owner(edit.node, op.mod)
        if (
            owner is not None
            and not owner.change.op_uid
            and not any(change is owner.change for change in made_here)
        ):
            # An op reaching into what this mod added by a change that is no
            # op's. The op goes out as itself, so folding its work into that
            # payload made an addition twice in game (*Hackner's Trade Union
            # Redesign*). So the payload stays as it stood before this op came
            # to it, and this edit is a change of its own, after it, as it is
            # in the mod's file.
            owner.freeze()
            owner = None
        if (
            owner is not None
            and folded is not None
            and not any(change is owner.change for change in made_here)
        ):
            # An edit landing in the payload of an earlier op's change. Both
            # go out as themselves, and the payload carries the work as well;
            # the pairing is recorded, so that neither is written as its
            # changes without the other (:func:`.keptops.narrowings`).
            folded.append(owner.change.key)
        if edit.op == OP_REPLACE:
            before = recording.text(edit.node)
            edit.node.text = edit.new
            if owner is not None:
                return None
            # A field this mod has set already, by an earlier op.
            already = sheet.value_change(edit.node, op.mod)
            # A field another mod made, set by a merge: still a merge, aimed
            # at what the game ships, so it does the same without that mod.
            ensured = self._ensured_over(op, sheet, edit) if edit.ensure else None
            # An agreed value is a change from what the game ships, not from
            # what the other mod left: that is the value it replaces alone.
            change = ensured or self._change(
                op, sheet, edit.node, OP_REPLACE, new=edit.new,
                was=None if edit.agreed else before,
            )
            if edit.agreed and change.op == OP_REPLACE and change.old_value == change.new_value:
                # A field the game does not ship, so there is no shipped value
                # to have replaced: the op restates the other mod's.
                change.restated = True
            if already is not None:
                # But not when one of the two is a kept op's answer. A kept op
                # goes out as the op it is and its answers are not written, so
                # an ordinary op folded into one was never written at all -- a
                # search setting every factory's radius to 20, then an op
                # making this one 30, exported as the search alone -- and a
                # kept op folded into an ordinary change was written twice, once
                # as that change and once as itself. They are two ops on one
                # field, in the order the mod has them: a uid keeps their keys
                # apart, as it does for an exception typed into the editor.
                change.uid = uuid.uuid4().hex[:12]
            sheet.remember_value(edit.node, change)
            return change

        if edit.op == OP_REMOVE:
            removed: Optional[Change] = None
            if edit.silent:
                # Carried out on the working tree, so later ops read what the
                # mod really leaves behind -- but never written down as a ModOp.
                pass
            elif owner is not None and edit.node is sheet.working and edit.node.getparent() is None:
                # The whole of an asset this mod made earlier. Folding that
                # into the payload left an asset-creating op with an empty body
                # and nothing else -- which reads as tidy and is not what the
                # mod does. *City Variations* makes a scratch asset, copies
                # four lists out of it with Content=, and removes it again; the
                # copies go out as the ops they are, and exported beside an
                # empty creation they copied from an asset that was never
                # there. So the asset is still created, and removing it is an
                # op of its own, in its own place.
                removed = self._change(op, sheet, edit.node, OP_REMOVE)
                sheet.removals.append(removed)
            elif owner is not None:
                owner.drop(edit.node)
                if not owner.nodes:
                    sheet.release(owner)
            else:
                removed = self._change(op, sheet, edit.node, OP_REMOVE)
                sheet.removals.append(removed)
            applying.remove(edit.node)
            return removed

        clones: List = []
        applying.apply(
            edit.op, edit.node, edit.elements, op.game,
            noted=lambda _what, node, _where: clones.append(node),
        )

        if owner is not None:
            # Inserting beside a node of an existing run grows that run; going
            # inside one of its nodes is already covered by the payload.
            if edit.op != OP_ADD and owner.holds(edit.node):
                if not recording.reached_by_a_removal(sheet, owner.change, clones):
                    for clone in clones:
                        owner.add(clone)
                    return None
            else:
                return None
        # A field a merge makes inside a section another mod made: aimed at
        # what the game ships, the section carried along. See _anchored.
        found = (
            self._anchored(sheet, edit.node, op.mod)
            if edit.ensure and sheet.shipped(edit.node) is None
            else None
        )
        if found is not None:
            wrap = tuple(member.tag for member in found[1])
            change = self._change(
                op, sheet, found[0], OP_MERGE, elements=[etree.Element(wrap[0])]
            )
            if change.op == OP_MERGE:
                sheet.claim(change, clones).wrap = wrap
                return change
        change = self._change(
            op, sheet, edit.node, OP_MERGE if edit.ensure else edit.op, elements=clones
        )
        sheet.claim(change, clones)
        return change

    def _change(
        self, op, sheet, node, kind, new: str = "", elements=(), was=None
    ) -> Change:
        shipped = sheet.shipped(node)
        if shipped is not None:
            generated = pathmod.path_for(sheet.base, shipped)
            generated = replace(generated, path=recording.keyed_as_it_stands(sheet, generated.path, node, op))
            before = recording.text(shipped) if was is None else was
        else:
            # A node an earlier mod put there.  The game does have it by the
            # time this mod loads, so this is an ordinary ModOp -- just one
            # whose path is written against the document as it will be then,
            # rather than against the file as it shipped.
            generated = pathmod.path_for(sheet.working, node)
            # Keyed against the asset as the op found it, as a shipped node's
            # is. Written partway through the op's own edits, an entry an
            # earlier mod added can be the last of its kind left: *Vendors only
            # sell common Items* replaces a whole item pool, the removals of
            # the shipped entries went first, and the one *Military Attention*
            # added came out as ``ItemsPool/Item`` -- every item, not that one.
            generated = replace(generated, path=recording.keyed_as_it_stands(sheet, generated.path, node, op))
            before = recording.text(node) if was is None else was
        if kind == OP_MERGE and not generated.path:
            # Into the asset itself, which has no path to aim a merge at.
            kind = OP_ADD
        common = dict(
            guid=op.guid,
            asset_label=self.label(op.guid),
            path=generated.path,
            positional=generated.positional,
            note=op.comment,
            op=kind,
            origin=op.mod,
            order=op.order,
            # Read out of a mod folder, so reading it again produces it again.
            authored=False,
            search=op.path if op.searched else "",
        )
        if kind == OP_REPLACE:
            return Change(tag=node.tag, old_value=before, new_value=new, **common)
        if kind == OP_REMOVE:
            return Change(
                tag=node.tag,
                old_value=before,
                summary=tr_noop("remove %s", context=STORED) % node.tag,
                **common
            )
        verb = {
            OP_ADD: tr_noop("add %(what)s to %(where)s", context=STORED),
            OP_MERGE: tr_noop("set %(what)s in %(where)s", context=STORED),
            OP_ADD_NEXT: tr_noop("add %(what)s after %(where)s", context=STORED),
            OP_ADD_PREV: tr_noop("add %(what)s before %(where)s", context=STORED),
        }[kind]
        what = (
            elements[0].tag if len(elements) == 1
            else tr_noop("%d entries", context=STORED) % len(elements)
        )
        # The payload is filled in by recording.Sheet.settle, once nothing can edit it.
        return Change(
            tag=elements[0].tag, summary=verb % {"what": what, "where": node.tag}, **common
        )

    def _create(self, op: SourceOp, element, spot: int = 0, group: Any = None) -> Change:
        asset = recording.clean(element)
        guid = (asset.findtext("Values/Standard/GUID") or "").strip()
        name = (asset.findtext("Values/Standard/Name") or "").strip()
        unnamed = tr_noop("unnamed", context=STORED)
        before = op.kind == OP_ADD_PREV
        change = Change(
            guid=op.guid,
            asset_label=tr_noop("%s  (new asset)", context=STORED) % (name or guid or unnamed),
            path="",
            tag="Asset",
            op=OP_ADD_PREV if before else OP_ADD_NEXT,
            note=op.comment,
            origin=op.mod,
            order=op.order,
            spot=spot,
            authored=False,
            search=op.path if op.searched else "",
            summary=tr_noop("create asset %(guid)s (%(name)s)", context=STORED)
            % {"guid": guid, "name": name or unnamed},
        )
        # An asset the mod invents has no shipped form, so its whole tree is the
        # payload -- and later ops that target it edit that payload directly.
        sheet = recording.Sheet(asset, owned=True)
        sheet.claim(change, [sheet.working])
        self._made += 1
        sheet.place = self._place_beside(op.guid, (-self._made, spot), before)
        # In the group of the asset it is put beside, unless it was put
        # somewhere else and only filed beside one (``group``).
        sheet.group = group if group is not None else self._group_of(op.guid)
        if guid:
            self._stands_apart(guid, sheet)
            # A mod may introduce a GUID something already has -- *Harborlife*
            # repeats one by accident among ninety triggers, *Local
            # Department* gives two decree buffs one. What a later op aimed at
            # that GUID edits is the one *first in the file*, not the first
            # made: the loader's look-up takes the first match, and each
            # addNextSibling lands straight after the asset it names (both
            # measured, both games) -- among assets in one top-level group,
            # that is; see _stands_apart. The other still has to have its payload
            # written out, so it is kept aside rather than dropped -- without
            # its sheet that ModOp goes out empty.
            if self._stands_first(guid, sheet):
                current = self.sheets.get(guid)
                if current is not None:
                    # Out of reach from here on, but what it holds -- and what
                    # was added into it -- still has to be settled.
                    self.orphans.append(current)
                self.sheets[guid] = sheet
            else:
                self.orphans.append(sheet)
        else:
            self.orphans.append(sheet)
        return change

    def _position(self, guid: str):
        """Where a shipped asset stands in the file, or None if not known."""
        position = getattr(self.index, "position", None)
        try:
            return position(guid) if position is not None else None
        except Exception:  # noqa: BLE001 -- an index that cannot say knows nothing
            return None

    def _place_beside(self, anchor: str, here: tuple, before: bool = False):
        """Where an asset put in after ``anchor`` stands, as a key that sorts in
        file order: straight after it, and nearer it than one put there
        before (``here`` leads with the negated count of assets made so far).
        Put in *before* it, straight before it: after whatever stands between
        it and the asset ahead of it, the one put there before included.
        None where that cannot be worked out."""
        held = self.sheets.get(anchor)
        if before:
            # Before one a mod made has no key short of it to give.
            made = held is not None and held.place is not None
            shipped = None if made else self._position(anchor)
            return None if shipped is None else (shipped - 1, (math.inf, -here[0], here[1]))
        if held is not None and held.place is not None:
            return held.place + (here,)
        shipped = self._position(anchor)
        return None if shipped is None else (shipped, here)

    def _stands_first(self, guid: str, sheet: recording.Sheet) -> bool:
        """Whether ``sheet``, just made under ``guid``, is the asset of that
        GUID first in the file -- the one the loader finds."""
        current = self.sheets.get(guid)
        if current is not None and current.place is not None:
            if sheet.place is None:
                return False  # first made stays, where nothing says otherwise
            return sheet.place < current.place
        shipped = self._position(guid) if current is None or current.place is None else None
        if shipped is None:
            # Nothing else of that GUID -- or a shipped one standing who knows
            # where, which was the reading before places were known.
            return current is None or (current.place is None and self.lookup(guid) is None)
        return sheet.place is not None and sheet.place < (shipped,)

    def _group_of(self, guid: str):
        """Which of the file's top-level groups the asset found by ``guid`` is
        in: a number for a shipped one, the group of what it was put beside
        for one a mod made, and None where the index cannot say."""
        held = self.sheets.get(guid)
        if held is not None and held.group is not None:
            return held.group
        return self._group_of_shipped(guid)

    def _stands_apart(self, guid: str, sheet: recording.Sheet) -> None:
        """Note ``sheet``, just made under ``guid``, and whether that GUID is
        now in more than one of the file's top-level groups.

        "The first in the file" is what the loader finds only among assets
        in one top-level group.  Its look-up walks the file in order, but
        starts with the top-level group its last successful look-up landed
        in (``FindAsset``'s ``last_search`` in the loader's source).  So with
        the copy in a later group than the shipped asset, an op straight
        after the one that put it there edits the copy; and with the copy in
        an earlier group, an op after one aimed at the shipped asset's group
        edits the shipped one.  Measured in both games themselves, and in
        all three xmltest builds, five cases each.

        Which group the last look-up landed in is a question about every op
        of every mod that ran before, in the order the game ran them, and an
        export that writes the same ops another way changes the answer.  So
        it is not worked out: an op aimed at such a GUID is carried as
        written (:meth:`_run`).

        An asset written straight under ``<ModOps>`` is in the group at the
        end of the file, which is not known to be the shipped file's last.
        """
        held = self._held_in.get(guid)
        if held is None:
            held = self._held_in[guid] = set()
            if self.lookup(guid) is not None:
                held.add(self._group_of_shipped(guid))
        held.add(sheet.group)
        if len(held) > 1 and None not in held:
            self.apart.add(guid)

    def _group_of_shipped(self, guid: str):
        asked = getattr(self.index, "top_group", None)
        try:
            return asked(guid) if asked is not None else None
        except Exception:  # noqa: BLE001 -- an index that cannot say knows nothing
            return None

    # -- does the refactor still do what the mod did? --------------------
    def _reads_as(self, node) -> str:
        """One asset boiled down to what the game will read out of it.

        Not the literal XML: a bare cost entry and no entry at all mean the same
        thing, so a mod that drops one by omission and an import that leaves it
        alone have to compare equal.  Both sides go through the same reduction,
        so any difference that does mean something still shows.
        """
        clone = copy.deepcopy(node)
        recording.strip_inert(clone, self.inert)
        return recording.canonical(clone)

    def _as_the_game_would(self, changes: List[Change]) -> List[Change]:
        """One asset's changes in the order the game will apply them.

        Mod by mod in load order -- each mod's file is a separate patch, applied
        whole before the next one starts -- and within a mod in the order that
        mod's own file will be written: a block per op, at that op's place, and
        :func:`changes.emit_order` inside each block. Replayed as one block per
        mod, every removal ran before every insertion -- which the file does not
        do -- so an export that lost *New Docklands*' moved entry passed, and the
        honest one was refused.
        """
        blocks: Dict[str, Dict[int, List[Change]]] = {}
        for change in changes:
            blocks.setdefault(change.origin, {}).setdefault(change.order, []).append(change)
        out: List[Change] = []
        for origin in sorted(blocks, key=lambda name: self.order.get(name, 0)):
            for order in sorted(blocks[origin]):
                block = sorted(blocks[origin][order], key=lambda c: (c.path, c.op))
                out.extend(emit_order(block))
        return out

    def _replays_as_read(self, sheet, changes: List[Change], game: str) -> bool:
        replayed = copy.deepcopy(sheet.base)
        for change in self._as_the_game_would(changes):
            recording.replay(replayed, change, game)
        return self._reads_as(replayed) == self._reads_as(sheet.working)

    def _rekeyed(self, sheet, changes: List[Change], game: str) -> bool:
        """Give every list entry these changes find a key of its own, and ask again.

        A path is written against the asset as it stood when its op was read,
        and a step is left bare when its entry was the only one then. That stops
        being true once the file's own earlier ModOps add beside it: *Pizza
        Margherita* adds its item next to the one entry of a list three times,
        and ``Products/Item`` matched one entry, then two, then four -- seven
        items where the mod adds three.

        So each path is found again and keyed: in the asset as shipped where it
        names a node there, since by the second of those ops the bare path
        already matches two; otherwise in the asset as it stands at that step,
        for a node this mod put there. The keyed changes are kept only if the
        whole replay then comes out the way the mod did. A wrong guess costs
        nothing: it fails that replay, and the paths go back to what they were.
        """
        was = {id(change): change.path for change in changes}
        replayed = copy.deepcopy(sheet.base)
        for change in self._as_the_game_would(changes):
            if change.path:
                keyed = recording.keyed_path(sheet.base, change.path) or recording.keyed_path(replayed, change.path)
                if keyed:
                    change.path = keyed
            recording.replay(replayed, change, game)
        moved = any(change.path != was[id(change)] for change in changes)
        if moved and self._replays_as_read(sheet, changes, game):
            return True
        for change in changes:
            change.path = was[id(change)]
        return False

    def _check_the_reading(self, results: List[OpResult]) -> None:
        """See that each asset's changes, applied, come to the asset as read.

        For a mod read as written.  None of its changes is written, so none is
        on trial -- but they are what the asset is shown from, and a path
        written while an op was being read can find more than its node once
        the ops before it are applied too (:meth:`_rekeyed`).  So the changes
        are replayed, keyed again where that mends them, and where it does
        not the ops reaching the asset say that what is shown under them is
        short.  Without it the first census read as written showed four
        assets other than the game leaves them.
        """
        by_guid: Dict[str, List[Change]] = {}
        # Replayed by the rules of the game the ops were read for, as they were.
        game_of: Dict[str, str] = {}
        for result in results:
            for change in result.changes:
                if not change.creates_asset:
                    by_guid.setdefault(change.guid, []).append(change)
                    game_of.setdefault(change.guid, result.op.game)
        broken = {}
        for guid, changes in by_guid.items():
            sheet = self.sheets.get(guid)
            if sheet is None:
                continue
            game = game_of[guid]
            if self._replays_as_read(sheet, changes, game) or self._rekeyed(sheet, changes, game):
                continue
            broken[guid] = self.label(guid)
        if not broken:
            return
        for result in results:
            if result.record is None:
                continue
            hit = sorted({change.guid for change in result.changes} & set(broken))
            if not hit:
                continue
            # A wide pattern can miss on dozens: the first few say where to
            # look, and the count says how far it goes.
            named = ", ".join(broken[guid] for guid in hit[:_NAMED])
            if len(hit) > _NAMED:
                said = tr_noop(
                    "replayed as written it does not land on %(n)d of its assets "
                    "the way the changes shown say (%(some)s and others)",
                    context=STORED,
                ) % {"n": len(hit), "some": named}
            else:
                said = tr_noop("replayed as written it does not land on %s the "
                               "way the changes shown say", context=STORED) % named
            result.record.caveat = _also(result.record.caveat, said)

# --------------------------------------------------------------- reading a mod
def _adopt_names(report: ImportReport) -> None:
    """The in-game names an export of the Studio's wrote for the assets this
    mod adds, back on the changes that add them: see
    :func:`core.texts.adopt_names`."""
    from . import texts

    if report.root is not None and Path(report.root).is_dir():
        texts.adopt_names(report.changes, report.root)


def read_ops(
    ops,
    document,
    keep_inert: bool = False,
    chosen: Optional[Dict[str, Dict[str, str]]] = None,
    present: Iterable[str] = (),
    search: Any = None,
    order: Optional[Dict[str, int]] = None,
    progress=None,
    stage=None,
    mod_ids: Optional[Dict[str, str]] = None,
) -> ImportReport:
    """What ``ops`` do to ``document``, one after another as the loader runs
    them, and the changes they come to.

    ``document`` is an :class:`AssetIndex`, or anything answering as one: the
    assets the ops start from.  ``chosen`` is each mod's settings, by mod,
    and ``mod_ids`` each mod's ModID, so that ``$mod-id.option`` can be
    answered as well as ``$option`` (:func:`.settings.visible`);
    ``present`` the mods loaded beside them, by ModID or name, for an op that
    asks after one.  ``search`` answers which assets a search-targeted op is
    about: by default the document is swept for them once, for all the ops.
    ``keep_inert`` writes down a removal the game would not notice
    (:class:`recording.Inert`).  ``order`` is where each mod stands in the load order,
    by mod, for what is written back by mod; ``progress`` and ``stage`` are
    :meth:`Translator.run_all`'s.  The report holds a result for each op, and
    in ``ops`` the ops kept as written, every mod's.
    """
    ops = list(ops)
    translator = Translator(document, keep_inert)
    translator.search = searcher(document, ops) if search is None else search
    translator.chosen = settings.visible(chosen, mod_ids)
    translator.present = {str(value).lower() for value in present if value}
    translator.order = dict(order or {})
    report = ImportReport(results=translator.run_all(ops, progress=progress, stage=stage))
    report.ops = [record for found in translator.found.values() for record in found]
    return report


def read_one(op: SourceOp, document, **asked) -> ImportReport:
    """What one op does to ``document``, and the changes it comes to:
    :func:`read_ops` for a single op."""
    return read_ops([op], document, **asked)


def analyse(
    source,
    index,
    keep_inert_removals: bool = False,
    origin: str = "",
    reading: Optional[Reading] = None,
) -> ImportReport:
    """Read a mod at ``source`` and work out what importing it would mean.

    ``source`` is a mod folder or a ModOps file, ``index`` the loaded
    assets.xml.  Nothing is applied: the report is meant to be shown first,
    because a translation the author cannot check is not one they should have
    to trust.
    """
    source = Path(source)
    reading = reading or Reading()
    report, collected = read_source(source, origin, reading)
    if report.error:
        return report

    # One pass over every op in file order: they patch a document that is
    # changing as they go, so reading them apart from one another would answer
    # their conditions against a file no version of the game ever sees.
    #
    # Read on its own, the mod is the only one there, as it is in a mods
    # folder that holds nothing else: an op that asks after another mod
    # (``#ModID``) is answered "not loaded" and kept as written, with the
    # question, for the day it is. Left unanswered, it was listed among the
    # ops the Studio could not read, as a warning, though the Studio reads
    # it and only the loadout had no such mod to find.
    #
    # It is there by the names a stack knows it by (analyse_all): its ModID
    # and its name, which is its folder's only where it is given none. Its
    # folder's as well made a library mod kept under another name than its
    # link's present by that name read alone, and not in a stack.
    read = read_ops(
        collected, index, keep_inert_removals,
        chosen={origin: chosen_options(
            report.metadata, reading.chosen_for(origin, report.metadata)
        )},
        present=(report.metadata.get("mod_id", ""), origin or report.mod_name),
        mod_ids={origin: report.metadata.get("mod_id", "")},
    )
    report.results = read.results
    report.ops = [record for record in read.ops if record.origin == origin]
    _title_parts(report)
    _adopt_names(report)

    if not report.results and not report.error:
        report.error = tr("No file in %s patches assets.xml.", report.mod_name)
    return report

# ------------------------------------------------------- a whole mods folder
@dataclass
class ModRead:
    """One mod inside a session read, and what became of it."""

    origin: str
    folder: Path
    report: ImportReport
    order: int = 0

    @property
    def name(self) -> str:
        return self.report.metadata.get("mod_name") or self.origin

    @property
    def changes(self) -> List[Change]:
        return self.report.changes

    @property
    def unread(self) -> int:
        return self.report.count(BLOCKED)

    @property
    def ops(self) -> int:
        return len(self.report.results)


@dataclass
class SessionReport:
    """Every mod in a folder, read as the one stack the game will load."""

    reads: List[ModRead] = field(default_factory=list)
    skipped: List[Tuple[Path, str]] = field(default_factory=list)
    # (the copy passed over, the copy kept instead). Several mods bundle the
    # same shared piece, and the game loads each ModID once -- so the others are
    # not read, and saying which is the difference between that and losing them.
    duplicates: List[Tuple[Path, str]] = field(default_factory=list)
    # The version each passed-over copy declares, by its folder: which copy
    # loads moves with the switches, and this says whether the one that
    # comes to load is the one read.
    duplicate_versions: Dict[str, str] = field(default_factory=dict)

    @property
    def changes(self) -> List[Change]:
        return [change for read in self.reads for change in read.changes]

    @property
    def unread(self) -> int:
        return sum(read.unread for read in self.reads)

    def summary(self) -> str:
        # A tally rather than a sentence: each part stands on its own.
        bits = [trn(
            "%(n)s change from %(mods)s", "%(n)s changes from %(mods)s",
            len(self.changes),
            mods=trn("%(n)s mod", "%(n)s mods", len(self.reads)),
        )]
        if self.unread:
            bits.append(trn("%(n)s ModOp the Studio could not read",
                            "%(n)s ModOps the Studio could not read", self.unread))
        if self.skipped:
            bits.append(trn("%(n)s folder with no asset patch",
                            "%(n)s folders with no asset patch", len(self.skipped)))
        if self.duplicates:
            bits.append(trn("%(n)s bundled copy of a mod already loaded",
                            "%(n)s bundled copies of a mod already loaded",
                            len(self.duplicates)))
        return "; ".join(bits)


def copies_by_host(session: SessionReport, roots, unlinked=None) -> Dict[str, Dict[str, str]]:
    """Each host's bundled copies the game does not load from it: host origin
    -> {the passed-over copy's origin: the origin of the copy that loads}.

    A [Shared] piece is loaded once however many mods ship it, so only one copy
    is read and becomes a layer; this is what lets the others' hosts still say
    they carry it, and point at the copy that runs.
    """
    roots = [Path(root) for root in (roots or [])]
    found: Dict[str, Dict[str, str]] = {}
    for folder, kept in session.duplicates:
        passed = _origin_for(Path(folder), roots, unlinked)
        if modfolder.is_bundled(passed):
            found.setdefault(passed.rsplit("/", 1)[0], {})[passed] = kept
    return found


def copy_versions(session: SessionReport, roots, unlinked=None) -> Dict[str, str]:
    """The version each passed-over copy declares, by the origin
    :func:`copies_by_host` names it by."""
    roots = [Path(root) for root in (roots or [])]
    return {
        _origin_for(Path(folder), roots, unlinked): session.duplicate_versions.get(str(folder), "")
        for folder, _kept in session.duplicates
    }


def _title_parts(report: ImportReport) -> None:
    """Name the groups inside a mod's kept ops the way its options dialog does.

    A group iModYourAnno switches on and off is ``<Group ModOpID="Aaden">`` in
    the file and *Disable/Enable Changes of Aaden Issack, World-Famous Enbesan
    Chef* in the dialog; the row takes the second, so the list and the dialog
    name the same thing the same way.
    """
    if not report.tweaks:
        return
    # Every switch, by the group it switches. Authors copy and paste these, and
    # *Trade Union Rebalance* has one labelled for Angela that switches Aaden's
    # group -- so a group can have two, and another none at all. The first is
    # the one that decides, in iModYourAnno and here alike, so it names the
    # row; the rest are said, and so is having none.
    by_group: Dict[str, List[str]] = {}
    for expose in report.tweaks.exposes:
        if expose.kind == imya.SKIP_TOGGLE:
            by_group.setdefault(expose.mod_op_id, []).append(expose.expose_id)
    for op in report.ops:
        # A group kept as a group says it of itself; one kept whole, of each
        # group inside it.
        for part in ([op.tool] if op.tool else []) + list(op.parts):
            found = by_group.get(part.get("id", ""))
            if found:
                part["title"] = found[0]
                if len(found) > 1:
                    part["also"] = found[1:]
            elif part.get("id"):
                part["unswitched"] = True


def chosen_options(metadata, chosen=None) -> Dict[str, str]:
    """What a mod's own settings are set to, given what it declares."""
    offered = metadata.get("options") if metadata else None
    if offered is None:
        return dict(chosen or {})
    return offered.settled(chosen)


def searcher(index, ops, progress=None):
    """Answer "which assets match this path?" for every search in one batch.

    Every op that targets by search rather than by GUID is collected up front
    and the file is read once for the lot, because reading it is nearly all of
    the cost and each extra expression is nearly free.  Returns ``None`` when
    there is nothing to search for or nothing to search.
    """
    finder = getattr(index, "find_matching", None)
    if finder is None:
        return None
    paths = search_paths(ops)
    if not paths:
        return None
    found = finder(paths, progress=progress)
    return lambda path: found.get(path, ())


def search_paths(ops) -> List[str]:
    """Every search ``ops`` put to the whole of the game's files: what an op
    is aimed by, what a condition asks, and where a value is copied from."""
    # Every search, the ones that copy their value included: a copy is read at
    # what it comes to today, whatever the settle setting says about how it is
    # written back, so its targets have to be known like any other search's.
    # Leaving those out used to be what made the op unreadable.
    paths = [op.path for op in ops if op.kind and not op.guid and op.path]
    # And every search a condition makes -- an op's or a group's -- so that
    # answering it later costs a lookup, not a sweep of its own.
    for op in ops:
        asked = (op.condition or "").strip().lstrip("!").strip()
        if asked.startswith("//"):
            paths.append(asked)
    # A Content= op copies from somewhere, and that somewhere may be a search
    # too -- one sweep answers both kinds.
    for op in ops:
        content = (op.attrs.get("Content") or "").strip()
        if content.endswith("/text()"):
            content = content[: -len("/text()")]
        if content.startswith("//"):
            paths.append(content)
    return paths


def _under(folder: Path, unlinked):
    """``(name, parts)`` for a folder that is, or is inside, a library mod
    with no link in the mods folder: the name its link goes by, and where
    ``folder`` sits below it.  None for any other folder."""
    for kept, name in (unlinked or {}).items():
        try:
            return name, list(folder.relative_to(kept).parts)
        except ValueError:
            continue
    return None


def _marked_off(folder: Path, roots, unlinked=None) -> bool:
    """Whether the top-level folder ``folder`` is in, below whichever of
    ``roots`` holds it, carries the off mark -- which switches it off, and
    everything bundled in it.  A library mod with no link is off the
    library's way."""
    if _under(folder, unlinked) is not None:
        return True
    for root in sorted(roots, key=lambda path: len(path.parts), reverse=True):
        try:
            parts = folder.relative_to(root).parts
        except ValueError:
            continue
        if parts:
            return modfolder.is_marked_off(parts[0])
    return modfolder.is_marked_off(folder.name)


def _origin_for(folder: Path, roots, unlinked=None) -> str:
    """What to call a mod: where it lives, relative to the folder it lives in.

    A bare folder name will not do any more.  A mod bundled inside another is a
    mod of its own, and the same bundled mod turns up inside several hosts --
    nine copies of one shared piece in the folder this was written against -- so
    the name has to say which copy this is.  The "switched off" mark comes off
    the top-level part, because that is about the folder rather than the mod.

    A library mod with no link in the mods folder is read where the library
    keeps it, and named as its link would name it: ``unlinked`` is that folder
    -> the link's name.
    """
    kept = _under(folder, unlinked)
    if kept is not None:
        return "/".join([modfolder.bare_name(kept[0])] + kept[1])
    # Most specific first. A game may search a folder *and* a folder inside it
    # -- Anno 117 reads both ``mods`` and ``mods/.ubi`` -- and a mod in the
    # inner one is a mod of that folder, not one bundled inside a mod called
    # ".ubi": naming it the second way loses which switch it answers to.
    for root in sorted(roots, key=lambda path: len(path.parts), reverse=True):
        try:
            parts = list(folder.relative_to(root).parts)
        except ValueError:
            continue
        if parts:
            return "/".join([modfolder.bare_name(parts[0])] + parts[1:])
    return modfolder.bare_name(folder.name)


def _one_per_mod_id(found, session: SessionReport, logged=None, off=frozenset()):
    """Keep one copy of each ModID, the way the loader does.

    Mods bundle shared pieces, and several mods bundle the *same* piece: nine
    folders in one real mods directory declare ``shared-pools-and-definitions``,
    at four different versions.  The game loads it once -- its log had a hundred
    and eighteen mods and a hundred and eighteen distinct ModIDs -- and takes the
    highest version.  Reading all nine would apply that mod's ops nine times.

    Which copy is :func:`modfolder.loading_copy`'s answer: the one the log
    names, else the highest version, and between equals the last the loader
    comes to.  Of the copies that are on: ``off`` is the origins whose
    folder carries Anno 1800's off mark, which the game never comes to at
    all.  Where every copy is off, one is still read, by the same rule.
    """
    # A mod that declares a ModID is one copy among any others of it, its
    # ModID compared as the loader does (modfolder.loader_key); one that
    # declares none is only ever its own folder.
    groups: Dict[str, list] = {}
    for entry in found:
        mod_id = (entry[2].metadata.get("mod_id") or "").strip()
        if mod_id:
            groups.setdefault(mod_id, []).append(entry)
    named = set(logged or ())
    dropped = set()
    for copies in groups.values():
        if len(copies) < 2:
            continue
        kept = modfolder.loading_copy(
            [entry for entry in copies if entry[0] not in off] or copies,
            version=lambda entry: str(entry[2].metadata.get("version") or ""),
            in_cache=lambda entry: modfolder.cache_id(entry[0]) != 0,
            named=(lambda entry: entry[0] in named) if named else None,
        )
        if kept is None:
            continue
        for entry in copies:
            if entry is not kept:
                dropped.add(id(entry))
                session.duplicates.append((entry[1], kept[0]))
                session.duplicate_versions[str(entry[1])] = str(
                    entry[2].metadata.get("version") or ""
                )
    return [entry for entry in found if id(entry) not in dropped]


# What a mod with no ModID yet names its own settings by in full, where a
# reading has one for it: see :func:`analyse_stack`.
SETTINGS_ID = "settings_id"


def analyse_all(
    folders,
    index,
    keep_inert_removals: bool = False,
    progress=None,
    searching=None,
    reading: Optional[Reading] = None,
    game: str = "",
    logged=None,
    roots=None,
    stage=None,
    translating=None,
    order: Optional[List[str]] = None,
    present: Iterable[str] = (),
    unlinked=None,
    written: Optional[Dict[str, List[SourceOp]]] = None,
    without_folder: Iterable[Tuple[str, dict]] = (),
    declared_options: Optional[Dict[str, Any]] = None,
) -> SessionReport:
    """Read a stack of mods the way the game reads them: in order, as one run.

    Not each mod on its own.  The game applies every mod's ModOps to one
    document, in load order, so a mod that edits an asset another mod invents is
    perfectly ordinary -- and read separately it looks like an op aimed at an
    asset that does not exist.  Read together, each op is answered against the
    document as it will actually be when that op runs, which is the only reading
    that says what the game will do.

    Which means the order has to be right, and the order is not simply the order
    the folders were handed over in.  Every mod is read first and put in order
    afterwards, because what a mod's place depends on -- its category, its name,
    the mods it declares it must follow -- is inside the mod.  See
    :func:`core.switches.order`.

    ``order``, the origins in the order to read them, is for a caller that
    already knows it -- a loadout reading its own mods again, in the order it
    holds them (see :func:`analyse_in_stack`). ``present`` is the ModIDs and
    names of mods loaded besides these, for a caller reading only the front of
    a stack: a condition asking whether a later mod is there still finds it.
    ``unlinked`` is the library mods among ``folders`` with no link in the mods
    folder -- the folder each is kept in -> the name its link goes by -- which
    are read where they are kept, named by their links, and off.

    ``written`` is the ops of mods that are read from those and not from
    their folders, by mod: a mod edited in a loadout is what the loadout
    writes for it, and its folder does not have the edit.  Such a mod with
    no folder at all is named in ``without_folder``, with what a modinfo
    would say of it.  Both are for a loadout reading its own stack again
    (:func:`analyse_stack`), and so is ``declared_options``: what such a mod
    offers as the loadout has it, by mod (a :class:`.settings.ModOptions`),
    since its folder's modinfo does not have what was declared here.

    ``progress(position, total, name)`` is called as each mod is read.
    """
    session = SessionReport()
    reading = reading or Reading()
    folders = [Path(folder) for folder in folders]
    roots = [Path(root) for root in (roots or [])]
    unlinked = {Path(kept): str(name) for kept, name in (unlinked or {}).items()}

    found = []
    for position, folder in enumerate(folders):
        if progress is not None:
            progress(position, len(folders), folder.name)
        origin = _origin_for(folder, roots, unlinked)
        report, ops = read_source(folder, origin, reading)
        if written and origin in written:
            ops = written[origin]
            report.error = ""
            if declared_options and origin in declared_options:
                report.metadata["options"] = declared_options[origin]
        if report.error and not ops:
            # A mod with a modinfo and no asset patch -- a camera tweak, a
            # texture pack -- is a mod the game loads all the same, and one
            # the loadout has to know of or the fourth card will forever
            # count it as a stranger.  Read as a mod that changes nothing;
            # a folder with neither is not a mod, and is skipped as before.
            if not report.metadata.get("declared"):
                session.skipped.append((folder, report.error))
                continue
            report.error = ""
        found.append((origin, folder, report, ops))
    for origin, metadata in without_folder:
        found.append((
            origin, None,
            ImportReport(mod_name=str(metadata.get("mod_name") or ""), metadata=dict(metadata)),
            list((written or {}).get(origin, ())),
        ))

    if stage is not None:
        stage(tr("Working out the order the game loads them in…"))
    off = {
        origin for origin, folder, _r, _o in found
        if folder is not None and _marked_off(folder, roots, unlinked)
    }
    found = _one_per_mod_id(found, session, logged, off)
    ranked = order if order is not None else switches.order(
        game,
        {origin: report.metadata for origin, _f, report, _o in found},
        logged=logged,
    )
    where = {origin: index for index, origin in enumerate(ranked)}
    found.sort(key=lambda entry: where.get(entry[0], 0))

    collected: List[SourceOp] = []
    for position, (origin, folder, report, ops) in enumerate(found):
        session.reads.append(
            ModRead(origin=origin, folder=folder, report=report, order=position + 1)
        )
        collected.extend(ops)

    search = searcher(index, collected, progress=searching)
    if stage is not None:
        stage(trn("Turning %(n)s ModOp into changes…", "Turning %(n)s ModOps into changes…",
                  len(collected)))
    every = read_ops(
        collected, index, keep_inert_removals,
        chosen={
            read.origin: chosen_options(
                read.report.metadata,
                reading.chosen_for(read.origin, read.report.metadata),
            )
            for read in session.reads
        },
        present=[
            value
            for read in session.reads
            for value in (read.report.metadata.get("mod_id", ""), read.origin)
        ] + list(present),
        mod_ids={
            read.origin: read.report.metadata.get("mod_id") or read.report.metadata.get(SETTINGS_ID, "")
            for read in session.reads
        },
        search=search,
        order={read.origin: read.order for read in session.reads},
        progress=translating,
        stage=stage,
    )
    results = every.results
    if stage is not None:
        stage(tr("Sorting the changes into their mods…"))
    by_mod: Dict[str, List[OpResult]] = {read.origin: [] for read in session.reads}
    for result in results:
        by_mod.setdefault(result.op.mod, []).append(result)
    for read in session.reads:
        read.report.results = by_mod.get(read.origin, [])
        read.report.ops = [record for record in every.ops if record.origin == read.origin]
        _title_parts(read.report)
        _adopt_names(read.report)

    if progress is not None:
        progress(len(folders), len(folders), "")
    return session


def analyse_in_stack(
    loadout,
    origin: str,
    index,
    chosen: Dict[str, str],
    logged=None,
    progress=None,
    translating=None,
    written: Optional[Dict[str, List[SourceOp]]] = None,
) -> ImportReport:
    """One of a loadout's loaded mods read again, where it stands in its stack.

    Changing a mod's own settings means reading its ops again, and they have to
    be read as they were the first time: against the document the mods before
    it leave, not the game's file alone. Read alone, an op that edits what an
    earlier mod added could not be read, and a search answered against the
    shipped file found less; across the census, mods with settings read alone
    came back with other unread ops and other changes (P7-18). So the
    loadout's mods are read again as one stack, each at the settings it was
    read at and this one at ``chosen``, in the loadout's own order, and this
    mod's reading is what comes back. Only the mods up to it are read -- what
    comes after cannot change the document its ops are answered against -- but
    every loaded mod is named as there, since a condition can ask after a later
    one.

    ``written`` is :func:`analyse_stack`'s: the ops of the mods before it
    that are read from what the loadout writes for them.  See
    :func:`.stale.read_at`, which is who knows which those are.

    Raises ValueError when ``origin`` is not a loaded mod whose folder is
    still there, or when reading the stack does not find it under that name.
    """
    target = loadout.layer(origin)
    if target is None or not target.folder or not Path(target.folder).is_dir():
        raise ValueError("%s is not a loaded mod with a folder to read" % origin)
    found = analyse_stack(
        loadout, [origin], index, {origin: chosen}, logged=logged,
        progress=progress, translating=translating, written=written,
    )
    if origin not in found:
        raise ValueError("reading the stack again did not find %s" % origin)
    return found[origin]


def analyse_stack(
    loadout,
    origins,
    index,
    chosen: Optional[Dict[str, Dict[str, str]]] = None,
    logged=None,
    progress=None,
    translating=None,
    written: Optional[Dict[str, List[SourceOp]]] = None,
    searching=None,
) -> Dict[str, ImportReport]:
    """Several of a loadout's loaded mods read again in one pass over its
    stack: each one's reading, by origin.

    What :func:`analyse_in_stack` does for one, for as many as are asked
    for and at the cost of one: the stack is read once, as far as the last
    of them.  ``chosen`` is the settings to read a mod at where they are not
    the ones it was read at.  A mod named whose folder is gone, or that the
    reading does not find, is not in the answer.  All of them are read the
    way the first was (``read_as``).

    ``written`` is the ops of each mod that is read from what the loadout
    writes for it and not from its folder (:func:`analyse_all`).  Such a
    mod is part of the stack whether it has a folder or not.

    ``searching`` is :func:`analyse_all`'s: where the sweep for the mods'
    searches has got to.

    ``loadout`` is asked only for its layers and its game, so a copy of
    those does for a reading off the thread the loadout lives on.
    """
    written = written or {}

    def has_folder(layer) -> bool:
        return bool(layer.folder) and Path(layer.folder).is_dir()

    every = [
        layer for layer in sorted(loadout.layers, key=lambda layer: layer.order)
        if layer.origin in written or has_folder(layer)
    ]
    wanted = [layer for layer in every if layer.origin in set(origins)]
    if not wanted:
        return {}
    target = wanted[0]
    layers = every[: every.index(wanted[-1]) + 1]
    folders: List[Path] = []
    roots: List[Path] = []
    tops: List[Tuple[str, Path, Path]] = []
    without_folder: List[Tuple[str, dict]] = []
    for layer in layers:
        if not has_folder(layer):
            said = {"mod_id": layer.mod_id, "mod_name": layer.name}
            if is_own_mod(layer.origin) and not layer.mod_id and games.get(loadout.game).key == games.ANNO_117:
                # The mod written here goes out under a ModID made of its
                # name, and its ops that name a setting in full name that
                # one. Only for that: what loads, and which copy, is decided
                # by the ModID it has.
                said[SETTINGS_ID] = games.GAMES[games.ANNO_117].usable_mod_id(layer.name)
            if layer.offered:
                said["options"] = settings.from_dict(layer.offered)
            without_folder.append((layer.origin, said))
            continue
        folder = Path(layer.folder)
        # The folder the mod's name hangs off: one level up for each part of
        # it, so a bundled ``Host/bits`` and a switched-off ``-Host`` come
        # back under the names they have.
        top = folder
        for _part in layer.origin.split("/")[1:]:
            top = top.parent
        folders.append(folder)
        tops.append((layer.origin, folder, top))
        if top.parent not in roots:
            roots.append(top.parent)
    # The mod written here, read from the library's build of it where its
    # project is missing (core.stale): under its own name, "", and not the
    # build's folder's. And a library mod with no link, read where the
    # library keeps it: under its link's name, as it was read in. Named by
    # its folder, the stack did not find it, and it was read alone.
    unlinked = _named_apart(tops, roots)
    options ={layer.origin: dict(layer.options) for layer in layers if layer.options}
    for origin, picked in (chosen or {}).items():
        options[origin] = dict(picked)
    reading = Reading.as_recorded(target.read_as, options)
    session = analyse_all(
        folders,
        index,
        reading.keep_inert,
        progress=progress,
        searching=searching,
        translating=translating,
        reading=reading,
        game=loadout.game,
        logged=logged,
        roots=roots,
        order=[layer.origin for layer in layers],
        present=[value for layer in loadout.layers for value in (layer.mod_id, layer.origin)],
        unlinked=unlinked,
        written=written,
        without_folder=without_folder,
        declared_options={
            layer.origin: settings.from_dict(layer.offered)
            for layer in layers if layer.origin in written and layer.offered
        },
    )
    asked = {layer.origin for layer in wanted}
    return {read.origin: read.report for read in session.reads if read.origin in asked}


def _named_apart(tops, roots) -> Dict[str, str]:
    """The folders of mods a loadout knows by another name than where they
    are says -- each top-level mod folder, by its path, to the name the
    loadout has for it -- as :func:`analyse_all` takes ``unlinked``.

    ``tops`` is ``(origin, folder, top)`` for each mod read from a folder.
    Each folder is placed where it is, under the deepest of ``roots`` that
    holds it: never by counting the parts of its name, which need not be as
    deep.  A mod copied out of mod.io's folder into the library keeps the
    name it was read under there, ``3293951/[Gameplay] New Docklands``, and
    sits one folder down in the mods folder: counted, its top was the mods
    folder itself, every mod in it was read under the other name, none was
    found again, and a project waiting to be read back never was (R3-12,
    hands-on run 3)."""
    found: Dict[str, str] = {}
    for origin, folder, _top in tops:
        if _origin_for(folder, roots) == origin:
            continue
        under = None
        for root in sorted(roots, key=lambda path: len(path.parts), reverse=True):
            try:
                parts = list(folder.relative_to(root).parts)
            except ValueError:
                continue
            if parts:
                under = (root, parts)
                break
        if under is None:
            continue
        root, parts = under
        named = origin.split("/")
        # The parts below its top-level folder are the same both ways; the
        # rest of the name is what that folder goes by.
        kept = len(named) - len(parts) + 1
        if kept < 1 or named[kept:] != parts[1:]:
            continue
        found[str(root / parts[0])] = "/".join(named[:kept])
    return found


# --------------------------------------------------- values a mod computes
class Uncomputable(Exception):
    """A computed value the Studio cannot work out."""


def _arithmetic(text: str) -> str:
    """Evaluate a small numeric expression, or say it cannot be.

    Parsed rather than evaluated: this is a string out of somebody else's mod,
    and the only thing it is allowed to be is arithmetic over numbers.
    """
    import ast

    # Bound the input before the parser sees it. A real ModValue expression is
    # a few dozen characters; a pathological one costs the parser its stack or
    # its memory, and neither of those is an answer this function may give.
    if len(text) > MAX_EXPRESSION_CHARS:
        raise Uncomputable(
            tr_noop("an expression too long for the Studio to work out", context=STORED)
        )

    # XPath spells division and remainder as words, because `/` is already a
    # path. Python's parser is what reads the expression below, so they are
    # put into its spelling first -- with the same precedence, measured: the
    # loader reads `self::node() div 2 + 1` on 10 as 6. 73 ModValues in the
    # Anno 117 census use `div`, and every one of them was unreadable.
    text = _XPATH_OPERATORS.sub(lambda hit: {"div": "/", "mod": "%"}[hit.group(0)], text)

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise Uncomputable(
            tr_noop("%r is not an expression the Studio can work out", context=STORED) % text
        ) from None
    except RecursionError:
        # A long enough chain of operators overruns the stack inside the parser,
        # before any depth of ours is consulted. This is a string out of
        # somebody else's mod, so it has to be an answer rather than a crash.
        raise Uncomputable(
            tr_noop("%r nests deeper than the Studio will follow", context=STORED) % text
        ) from None

    def value(node, depth: int = 0):
        # `1+1+1+...` nests to the left once per term, so a long enough chain
        # would overflow the stack and leave a RecursionError -- which is not
        # one of the two answers this function is allowed to give.
        if depth > MAX_EXPRESSION_DEPTH:
            raise Uncomputable(
                tr_noop("%r nests deeper than the Studio will follow", context=STORED) % text
            )
        if isinstance(node, ast.Expression):
            return value(node.body, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inner = value(node.operand, depth + 1)
            return inner if isinstance(node.op, ast.UAdd) else -inner
        if isinstance(node, ast.BinOp):
            left, right = value(node.left, depth + 1), value(node.right, depth + 1)
            for kind, apply in (
                (ast.Add, lambda a, b: a + b),
                (ast.Sub, lambda a, b: a - b),
                (ast.Mult, lambda a, b: a * b),
                (ast.Div, lambda a, b: a / b if b else _no_dividing_by_zero(text)),
                (ast.Mod, lambda a, b: a % b if b else _no_dividing_by_zero(text)),
            ):
                if isinstance(node.op, kind):
                    return apply(left, right)
        raise Uncomputable(tr_noop("%r is not arithmetic", context=STORED) % text)

    found = value(tree)
    return _as_game_number(found, text)


# Deep enough for any expression a mod means, shallow enough to stay on the
# stack: the recursion is one frame per operator.
MAX_EXPRESSION_DEPTH = 200

# Generous next to `self::node() * $factor`, and small enough that no arrangement
# of it can exhaust the parser.
MAX_EXPRESSION_CHARS = 1000

# Significant digits kept when writing a computed result: the loader's own,
# measured. ``10 div 3`` goes into the game's file as ``3.333333333333333``
# and ``1 div 7`` as ``0.1428571428571428``, which is ``%.16g`` exactly.
COMPUTED_DIGITS = 16

# Decimal places for the fallback below, where the loader's own spelling would
# reach for an exponent and the game's files never do.
COMPUTED_PLACES = 10


def _as_game_number(found, text: str) -> str:
    """A computed result as the game's own files would spell it.

    This goes straight into the payload of an exported ModOp, so it has to be
    a number Anno can read -- and where the mod is written back with the
    question still in it, the game works the same value out again, so the two
    spellings had better agree.  ``%.16g`` is what the loader writes, measured
    across recurring decimals large and small.

    ``%g`` -- which this used first -- is wrong twice over: it keeps six
    *significant* digits, so ``123456.789`` came out as ``123457``, and past
    seven digits it switches to an exponent, so a perfectly ordinary
    ``1234567.25`` was written into a shipped mod as ``1.23457e+06``.  Sixteen
    digits is far enough out that no value in the game's own files reaches an
    exponent, and if one ever did, the fixed spelling below is still a number.
    """
    if isinstance(found, float):
        if found != found or found in (float("inf"), float("-inf")):
            raise Uncomputable(tr_noop("%r does not come to a number", context=STORED) % text)
        if found.is_integer():
            return str(int(found))
        spelled = "%.*g" % (COMPUTED_DIGITS, found)
        if "e" in spelled or "E" in spelled:
            spelled = "%.*f" % (COMPUTED_PLACES, found)
        trimmed = spelled.rstrip("0").rstrip(".") if "." in spelled else spelled
        return trimmed or "0"
    return str(found)


def computed_value(expression: str, target, chosen) -> str:
    """What a ``ModValue`` expression comes to, here and now.

    ``self::node()`` is the value the target already has, ``$name`` is one of
    the mod's own settings, and the rest is arithmetic over the two.
    """
    text = (expression or "").strip()
    if not text:
        raise Uncomputable(tr_noop("an empty expression", context=STORED))

    def setting(match):
        name = match.group(1)
        if not chosen or name not in chosen:
            raise Uncomputable(
                tr_noop("the mod's %s option is not declared", context=STORED) % name
            )
        return chosen[name]

    text = SETTING.sub(setting, text)
    if "self::node()" in text:
        was = recording.text(target) if target is not None else ""
        if not was:
            raise Uncomputable(
                tr_noop("it builds on a value the target does not have", context=STORED)
            )
        text = text.replace("self::node()", "(%s)" % was)
    if NUMBER.fullmatch(text.strip()):
        # Spelled as the loader spells it, which is as a number and not as the
        # text the option held: a default of "3.0" goes into the game as 3.
        return _as_game_number(float(text), text) if "." in text else text.strip()
    return _arithmetic(text)

# XPath's names for the two operators whose symbols mean something else in a
# path. Whole words only: a `div` inside a longer name is not an operator.
_XPATH_OPERATORS = re.compile(r"\b(?:div|mod)\b")


def _no_dividing_by_zero(text: str):
    """The loader writes ``Infinity``, which is not a number to put in a file.

    So the op is one the Studio could not read, and goes back out as itself --
    where the game will work out its own ``Infinity`` exactly as it did before.
    """
    raise Uncomputable(tr_noop("%r divides by zero", context=STORED) % text)


# Why an op whose ``<ModOpContent/>`` stands straight inside it is not read,
# where it is not the one shape :func:`_copied_as_text` reads.
_COPY_STRAIGHT_INSIDE = tr_noop(
    "it puts what it copies straight into the op, beside text or as text, where "
    "the loader's documentation does not say what comes of it", context=STORED
)


def _copied_as_text(op: SourceOp, found, as_text: bool) -> Optional[str]:
    """The text an op adds whose ``<ModOpContent/>`` stands straight inside
    it, not inside an element of its body: ``<ModOp Type="add" … Content="…
    /text()">(<ModOpContent/>)</ModOp>`` adds the copied text, in brackets,
    to what a field says.  *Enable DLC11 Story* writes its texts so.

    It was read as the addition of an element called ``ModOpContent``.  The
    loader's docs show the mark inside an element and say nothing of one
    standing in the op itself, with text or without.  Measured with xmltest
    (Anno Modding Tools 2.3.1, all three builds, 10 Oct 2026), an ``add``
    whose ``Content`` ends in ``/text()`` appends to the field the text
    before the mark, the copy and the text after it, once for each node
    copied, which is the docs' adding of text to a field with the copy put
    in where the mark says.  That one shape is read.

    The others the builds agree on as well, but none comes to anything the
    docs describe or the Studio has a place for: a copied node is mixed
    with the text inside the field, under ``addNextSibling`` and
    ``addPrevSibling`` the text is left loose among fields, a ``replace``
    leaves it in the field's place with the text after the mark first, and
    a second mark is left in the file as it is.  Those raise
    :class:`Uncomputable`, and the op is carried as written.

    ``None`` for an op with no such mark, and for one whose mark stands for
    whole nodes with no text beside it: there the nodes go where the mark
    is, as they always did (:func:`_filled_in`).  And for an op that has
    elements of its own beside the mark, which is read by those as it was:
    text among an op's elements is carried and given no meaning.
    """
    marks = [node for node in op.payload if node.tag == MOD_OP_CONTENT]
    if not marks or len(marks) != len(op.payload):
        return None
    beside = bool(op.text) or any((node.tail or "").strip() for node in op.inside)
    if not as_text and not beside:
        return None
    mark = op.inside[0] if len(op.inside) == 1 else None
    if op.kind != OP_ADD or not as_text or mark is None or len(mark) or mark.attrib:
        raise Uncomputable(_COPY_STRAIGHT_INSIDE)
    # Layout alone on either side is not text: the loader's parser drops it.
    before = op.raw_text if op.text else ""
    after = mark.tail if (mark.tail or "").strip() else ""
    return "".join(before + recording.text(node) + after for node in found)


def _filled_in(payload, copied, as_text: bool) -> tuple:
    """An op's body with every ``<ModOpContent/>`` replaced by what was copied."""
    wrapper = etree.Element(effective.WRAPPER)
    for child in payload:
        wrapper.append(copy.deepcopy(child))
    for mark in list(wrapper.iter(MOD_OP_CONTENT)):
        parent = mark.getparent()
        tail = mark.tail or ""
        if as_text:
            words = recording.text(copied) + tail
            before = mark.getprevious()
            if before is not None:
                before.tail = (before.tail or "") + words
            else:
                parent.text = (parent.text or "") + words
            parent.remove(mark)
        else:
            node = copy.deepcopy(copied)
            node.tail = tail
            parent.replace(mark, node)
    for node in wrapper.iter():
        # Layout around a mark that stood alone is not part of the value.
        if isinstance(node.tag, str) and not len(node) and node.text:
            node.text = node.text.strip()
    return tuple(wrapper)


def end_group(blocks: int) -> tuple:
    """The top-level group of an asset a mod adds at the end of the file,
    ``blocks`` being how many groups mods have added there by then: each
    block is a group of its own, and an asset written straight under
    ``<ModOps>`` is in the last of them, or in one before them all."""
    return ("end", blocks)


def end_groups(made: Iterable[Change]) -> Dict[str, tuple]:
    """:func:`end_group` for each of ``made`` that the reader has at the end
    of the file, by the change's uid: the created assets of the mods that
    load, in the order they load.

    For asking afterwards what :meth:`Translator._adds_an_asset` and
    :meth:`Translator._adds_a_block` decided as they read: each marks the
    changes it makes (:attr:`Change.at_the_end`), and the blocks are counted
    from those.  Counted from the kept ops instead, a block inside a group
    kept whole -- a ``MaxRepeat`` loop -- was the loop's op and no block, and
    a mod that does not load still had its blocks counted.
    """
    blocks = 0
    last = None
    found: Dict[str, tuple] = {}
    for change in made:
        if change.at_the_end == IN_A_BLOCK:
            block = (change.origin, change.op_uid, change.order)
            if block != last:
                blocks += 1
                last = block
            found[change.uid] = end_group(blocks)
        elif change.at_the_end == STRAIGHT:
            last = None
            found[change.uid] = end_group(blocks)
    return found


def _block_assets(payload) -> list:
    """The ``<Asset>``s in a payload shaped as the file's own groups are --
    ``<Group>`` holding ``<Groups>`` of more and ``<Assets>`` of assets -- in
    file order, or nothing where it holds anything else.

    And an ``<Asset>`` straight under its ``<Group>``, which the file's own are
    not: *Require All Needs* keeps a list of its own in one, for its other ops
    to fill and read, and both builds find it there by GUID."""
    found: list = []

    def group(node) -> bool:
        if node.tag != "Group":
            return False
        for child in children(node):
            if child.tag == "Asset":
                found.append(child)
            elif child.tag == "Groups":
                if not all(group(inner) for inner in children(child)):
                    return False
            elif child.tag == "Assets":
                inside = children(child)
                if not all(asset.tag == "Asset" for asset in inside):
                    return False
                found.extend(inside)
            else:
                return False
        return True

    return found if payload and all(group(node) for node in payload) else []


_CONDITION_FAILED = (
    tr_noop("its condition does not hold by the time it runs", context=STORED),
    tr_noop("its condition already holds, so it is no longer needed", context=STORED),
)


def _did_not_run(reason: str) -> bool:
    """Whether a settled op's reason is that its condition did not hold."""
    return any((reason or "").startswith(said) for said in _CONDITION_FAILED)


def _says_what_its_op_says(result: OpResult) -> bool:
    """Whether an op kept as written came to one change that says all the op
    says: the same kind of edit, to the node it names, in the one asset it
    names.  See :attr:`~.changes.Op.plain`."""
    op = result.op
    if op.verbatim or op.scope or op.inside_kept or op.searched or op.condition:
        return False
    if len(op.guids) != 1 or len(result.changes) != 1 or result.partial or result.coarse:
        return False
    record = result.record
    if record is None or record.kept or record.searches:
        # There is something to say of it, and its own row is where.
        return False
    change = result.changes[0]
    if change.op_uid != record.uid or change.within:
        return False
    if change.creates_asset:
        return True
    # Either way of writing the path: Anno 117's ``Replace="@1000/Building"``
    # leaves out the ``/Values`` the change's path has, and was never plain,
    # so each such op was a row over a row for its asset over its change.
    return (
        change.guid == op.guids[0]
        and change.op == op.kind
        and _names_it_as(change.path, op.path or "")
    )


# One step of a path, with what it is keyed by: ``Item[FunctionalEffect='99057']``.
_PATH_STEP = re.compile(r"/((?:[^/\[]|\[[^\]]*\])+)")


def _names_it_as(path: str, written: str) -> bool:
    """Whether ``written``, the path an op names its node by, names the node
    of a change at ``path``: the same path, or one that keys a step ``path``
    leaves bare and is the same everywhere else.

    A change's path is the Studio's: a list entry is keyed where it has a
    twin as its op runs, and bare where it has none.  The op's author may
    have keyed it all the same -- ``Remove="@82486/Building/
    FunctionalEffects/Item[FunctionalEffect='99057']"``, in a mod that adds a
    second entry later -- and the op is then still one with its change.  The
    change keeps the Studio's spelling: conflicts and the editor find a
    change by its path, and every change of a node has to be spelled one way.
    """
    path = path.rstrip("/")
    spellings = dialects(written)
    if path in spellings:
        return True
    mine = _PATH_STEP.findall(path)
    if "".join("/" + step for step in mine) != path:
        return False
    for spelling in spellings:
        theirs = _PATH_STEP.findall(spelling)
        if "".join("/" + step for step in theirs) != spelling or len(theirs) != len(mine):
            continue
        if all(
            one == other or ("[" not in one and other.startswith(one + "["))
            for one, other in zip(mine, theirs, strict=True)
        ):
            return True
    return False


def _kept_as_written(kept: str) -> str:
    """The reason an op that goes back out as itself gives, around what keeps it."""
    return tr_noop("kept as written: %s", context=STORED) % kept


# How many assets a caveat names before it counts the rest.
_NAMED = 3


def _also(said: str, more: str) -> str:
    """One more thing to say, once."""
    return said if more in (said or "") else "; ".join(bit for bit in (said, more) if bit)


def _bare_path(condition: str) -> bool:
    """Whether a condition is a path read from where the op starts --
    ``/Values/…`` or the older ``~/Values/…`` -- rather than a search, a
    named asset, a mod or a setting."""
    text = (condition or "").strip().lstrip("!").strip().lstrip("~").strip()
    return text.startswith("/") and not text.startswith("//")


ALLOWED_MISS = "; ".join((
    tr_noop("it matches nothing here, which its author allows (AllowNoMatch)",
            context=STORED),
    # Translators: follows "it matches nothing here, which its author allows
    # (AllowNoMatch)" -- with other mods loaded, it may match something.
    tr_noop("with other mods it may", context=STORED),
))


def freeze_computed(element, target, chosen, merging: bool = False) -> None:
    """Replace every ``ModValue`` under ``element`` with what it comes to.

    In place, on a copy the caller owns.  What a mark comes to is what Anno
    117 was seen to make of it (two runs of marker mods, 10 Oct 2026: "Values
    a mod works out while patching" in docs/design-notes.md says where they
    are kept), and xmltest3 gave the same answer to each of the 49 questions
    asked.  There are two cases.

    Merged into a field the game has, the mark is that field's value, and
    ``self::node()`` is what the field held.  Text of the mod's own before
    the mark is dropped, which is how the shipped files write a ``0`` so the
    field reads as a number with nothing put in.  Text after the mark is
    what the field ends up holding, in place of the value: ``1<ModValue
    Insert="2+3"/>7`` comes to ``7``.

    Put into the file new -- by a replace, an add, a whole new asset, or a
    merge of something the game does not have -- the mark is worked out
    where it stands, and less comes of it: see
    :func:`_worked_out_where_it_stands`.  There is no value there yet for
    ``self::node()`` to be, and the game writes ``NaN``.

    ``merging`` says the op is a merge, which decides which of the two it is
    and what flags are merged into -- see :func:`_freeze_flags`.
    """
    flagged = [node for node in element.iter(MOD_VALUE) if is_flags(node)]
    if flagged:
        _freeze_flags(element, target, flagged, merging)
    for node in list(element.iter(MOD_VALUE)):
        parent = node.getparent()
        if parent is None:
            raise Uncomputable(
                tr_noop("a computed value with nothing to put it in", context=STORED)
            )
        expression = node.get("Insert", "")
        new = _put_in_new(element, parent, target, merging)
        if new is not None:
            _worked_out_where_it_stands(node, new)
            if "self::node()" in expression:
                # Read as twice the game's value until now, 20 for a radius
                # of 10.  The field the op puts in holds only the mark.
                raise Uncomputable(
                    tr_noop("it builds on the field's own value (self::node()) in a field "
                            "it puts into the file, which has no value yet: the game "
                            "writes NaN there", context=STORED)
                )
            value = computed_value(expression, None, chosen)
        else:
            against = _matching_node(element, parent, target)
            if against is not None and len(against):
                # The mod writes a value where the game has a whole section. Even
                # the game can only be doing something odd here; what it is not
                # doing is setting a field, so there is no narrow op for it.
                raise Uncomputable(
                    tr_noop("it computes a value into <%s>, which is a section "
                            "and not a field", context=STORED) % parent.tag
                )
            value = computed_value(expression, against, chosen)
        _settle_mark(node, value, new is None)


def _settle_mark(mark, value: str, merged: bool) -> None:
    """Put what a mark comes to in its place: the field's value.

    ``merged`` says the field is one the game has, where text after the mark
    is what the field ends up holding and text before it is dropped.  In a
    field put in new the mark has to stand alone: text before it has stopped
    the loader already (:func:`_worked_out_where_it_stands`).  Text only
    after it the game was not asked about: xmltest3 drops the text and gives
    the value, and xmltest2 leaves both under an add or a replace.
    """
    parent = mark.getparent()
    after = _text_beside(mark)
    if after and not merged:
        raise Uncomputable(
            tr_noop("it works a value out with text of its own after it in a field it "
                    "puts into the file, and what the game makes of the two there is not "
                    "known", context=STORED)
        )
    parent.remove(mark)
    if len(parent):
        raise Uncomputable(
            tr_noop("a computed value beside other fields", context=STORED)
        )
    parent.text = after or value


def _put_in_new(wrapper, field, target, merging: bool):
    """The element of an op's body, at ``field`` or above it, that the op
    puts into the file new, and ``None`` where ``field`` is merged into one
    the game has.

    A replace, an add and the sibling ops put in every element they hold.  A
    merge puts in what the game does not have, and the topmost such element
    is where the loader starts on it as on any addition: measured with
    xmltest3, a mark in a section a merge adds is worked out or left exactly
    as it is in one an add adds.
    """
    chain = []
    while field is not None and field is not wrapper:
        chain.append(field)
        field = field.getparent()
    if not chain:
        # Straight inside the op: under a merge the target is the field.
        return None
    if not merging:
        return chain[-1]
    for node in reversed(chain):
        if _matching_node(wrapper, node, target) is None:
            return node
    return None


def _worked_out_where_it_stands(mark, new) -> None:
    """Raise :class:`Uncomputable` where the game leaves ``mark`` as written
    in ``new``, the element holding it that the op puts into the file new.

    The game was asked (10 Oct 2026, the second of the two runs
    :func:`freeze_computed` names): an asset added whole with an empty
    element among its sections,
    before the one holding the marks, comes out with every one of those
    marks still in the file and its field empty, and the same asset with
    the empty element after them has its values.  So does a field holding
    ``1<ModValue Insert="2+3"/>7``, under a replace and in a new asset: it
    is left as ``17`` with the mark in it.

    Where the line runs was measured with xmltest3, which gave the game's
    answer to all 49 of its questions (``TestTheRealLoader``).  The loader
    goes through what is put in level by level, and at each level stops at
    the first thing with nothing inside it that has something after it: an
    empty element, written ``<X />`` or ``<X></X>``, with attributes or
    holding only layout or a comment, or text.  What follows at that level,
    and everything inside what follows, is not looked at.  One level up it
    goes on.  So a mark is left where it, its field or any section around it
    has such a thing before it among its own siblings.  An empty element
    that is the last at its level stops nothing, nor does one in another
    op, in another asset of the same op, or beside the element put in.
    """
    node = mark
    while node is not new:
        parent = node.getparent()
        loose = bool((parent.text or "").strip())
        empty = None
        for earlier in node.itersiblings(preceding=True):
            if (earlier.tail or "").strip():
                loose = True
            if isinstance(earlier.tag, str) and (earlier.tag == MOD_VALUE or _holds_nothing(earlier)):
                empty = earlier
        if loose and node is mark:
            raise Uncomputable(
                tr_noop("it works a value out after text of its own in a field it puts "
                        "into the file, and there the game leaves the mark as written",
                        context=STORED)
            )
        if loose:
            raise Uncomputable(
                tr_noop("it works a value out after text standing loose among the "
                        "elements it puts into the file, and there the loader leaves the "
                        "mark as written", context=STORED)
            )
        if empty is not None:
            raise Uncomputable(
                tr_noop("it works a value out after the empty element <%s> in what it "
                        "puts into the file, and there the game leaves every such mark as "
                        "written and its field with no value", context=STORED)
                % empty.tag
            )
        node = parent


def _holds_nothing(element) -> bool:
    """Whether an element is empty as the loader reads it: no element inside
    it and no text but layout.  Comments are not read at all."""
    if (element.text or "").strip():
        return False
    return not any(
        isinstance(child.tag, str) or (child.tail or "").strip() for child in element
    )


def _marks_outside_a_merge(wrapper, kind: str) -> None:
    """Raise :class:`Uncomputable` for the marks in an op that is not a merge
    which come to no value, or to one the game was not asked about.

    A mark straight inside a replace, with no element around it: the game
    was asked, and the field is gone afterwards.  xmltest3 leaves the value
    as text loose in the section.  Straight inside an add it has no field
    to be the value of.

    A copy from another asset ending in ``/text()`` the game was asked
    about, and it is read.  One from beside where the value lands, and one
    of a whole node or section, it was not.  xmltest3 finds the first from
    where the mark stands in what the op puts in, which is not where the
    Studio looks, so neither is read.
    """
    for mark in wrapper.iter(MOD_VALUE):
        text = (mark.get("Insert") or "").strip()
        if text.startswith(("../", "./")) or (
            text.startswith("@") and not text.endswith("/text()")
        ):
            raise Uncomputable(
                tr_noop("it copies %s in an op that is not a merge, and what the game "
                        "does with such a copy there is not known", context=STORED) % text
            )
        if mark.getparent() is wrapper:
            if kind == OP_REPLACE:
                raise Uncomputable(
                    tr_noop("it replaces a field with a value worked out and no element "
                            "around it, which leaves the game with no such field",
                            context=STORED)
                )
            raise Uncomputable(
                tr_noop("it computes a value with no field to put it in", context=STORED)
            )


def _has_flags(elements) -> bool:
    """Whether a payload holds a ``ModValue`` that merges or removes flags."""
    return any(
        is_flags(node)
        for element in elements if isinstance(element.tag, str)
        for node in element.iter(MOD_VALUE)
    )


# Why an op that is not a merge and merges or removes flags is not read.  The
# loader's docs merge flags under ``Merge``.  Measured with the two Anno 117
# builds of xmltest (Anno Modding Tools 2.3.1, 10 Oct 2026), a ``Merge`` flag
# under a replace is left as written by xmltest2 and merged into nothing by
# xmltest3, and ``Remove`` is left as written by both, where the Studio read a
# replace as working on its own copy of the list.  The game was asked about
# values worked out (:func:`freeze_computed`) and not about flags, so the op
# is carried as written and nothing is shown for it.
_FLAGS_OUTSIDE_A_MERGE = tr_noop(
    "it adds or takes out flags in an op that is not a merge, and whether the "
    "game does that there is not known", context=STORED
)


def _text_beside(mark, before: bool = False) -> str:
    """The text standing after a ``<ModValue/>`` in the element that holds
    it, and with ``before`` the text standing before it too, layout aside.

    ``<Radius>1<ModValue Insert="2+3"/>7</Radius>`` was read as ``15``: the
    text before taken for a prefix, the text after dropped.  What the game
    makes of it depends on where the field goes: see :func:`_settle_mark`.
    """
    found = (mark.tail or "").strip()
    if before:
        earlier = mark.getprevious()
        found += ((mark.getparent().text if earlier is None else earlier.tail) or "").strip()
    return found


def _freeze_flags(element, target, flagged, merging: bool) -> None:
    """Settle ``<ModValue Merge="…"/>`` and ``Remove="…"`` (Anno 117): flags
    added to a ``;``-separated list if they are not in it, or taken out.

    As the loader's ``MergeFlags`` does it: split on ``;`` with empty pieces
    dropped, matched exactly, each added flag appended at the end, each removed
    one's first occurrence taken out, several in one field applied in order.
    What they apply to is the list the field holds when the loader gets there:
    for a merge, the game's -- the ``ModValue`` is copied into the game's field
    -- and for anything else, whatever the mod's own copy of the field says.
    """
    lists: Dict[int, tuple] = {}
    for node in flagged:
        parent = node.getparent()
        if parent is None:
            raise Uncomputable(tr_noop("flags with no field to go in", context=STORED))
        if any(
            sibling.tag == MOD_VALUE and not is_flags(sibling)
            for sibling in parent if isinstance(sibling.tag, str)
        ):
            raise Uncomputable(
                tr_noop("flags and a computed value in one field", context=STORED)
            )
        held = lists.get(id(parent))
        if held is None:
            against = _matching_node(element, parent, target)
            if against is not None and len(against):
                raise Uncomputable(
                    tr_noop("it merges flags into <%s>, which is a section "
                            "and not a field", context=STORED) % parent.tag
                )
            start = recording.text(against) if merging and against is not None else (parent.text or "")
            held = lists[id(parent)] = (parent, [flag for flag in start.split(";") if flag])
        flags = held[1]
        removing = node.get("Remove") is not None and node.get("Merge") is None
        for flag in (node.get("Remove") if removing else node.get("Merge")).split(";"):
            if not flag:
                continue
            if removing and flag in flags:
                flags.remove(flag)
            elif not removing and flag not in flags:
                flags.append(flag)
        parent.remove(node)
    for parent, flags in lists.values():
        if len(parent):
            raise Uncomputable(tr_noop("flags beside other fields", context=STORED))
        parent.text = ";".join(flags)


def _beside(wrapper, node, target, kind: str, expression: str) -> list:
    """What a relative ``ModValue Insert`` path finds: climbed from the
    ModValue's field, one level a ``../``, through the op's body -- ``wrapper``
    -- and on into the target document, then read down from there."""
    rest = expression
    ups = 0
    while rest.startswith(("../", "./")):
        if rest.startswith("../"):
            ups += 1
            rest = rest[3:]
        else:
            rest = rest[2:]
    if target is None:
        raise Uncomputable(
            tr_noop("it copies from beside a field the Studio cannot place", context=STORED)
        )
    here = node.getparent()
    while ups and here is not wrapper:
        here = here.getparent()
        ups -= 1
    if here is wrapper:
        # The body stands for the target's children -- or, where it repeats the
        # target's own name, or replaces it or sits beside it, for the target's
        # level.
        first = next((child for child in wrapper if isinstance(child.tag, str)), None)
        level = kind in (OP_REPLACE, OP_ADD_NEXT, OP_ADD_PREV) or (
            first is not None and first.tag == target.tag
        )
        context = target.getparent() if level else target
        while ups and context is not None:
            context = context.getparent()
            ups -= 1
    else:
        context = _matching_node(wrapper, here, target)
    if context is None:
        raise Uncomputable(
            tr_noop("it copies from beside a field the game does not have", context=STORED)
        )
    if not rest.strip("/"):
        return [context]
    try:
        return context.xpath(rest.rstrip("/"))
    except etree.XPathError:
        raise Uncomputable(
            tr_noop("it copies from %s, which cannot be read", context=STORED) % expression
        ) from None


def _matching_node(root, node, target):
    """The node in the target that a node in the payload stands for.

    A payload either mirrors the target's children or repeats the target's own
    name first, so both readings are tried -- the point is only to find the
    value ``self::node()`` refers to.
    """
    if target is None:
        return None
    steps = []
    while node is not None and node is not root:
        steps.append(node.tag)
        node = node.getparent()
    steps.reverse()
    if steps and steps[0] == target.tag:
        steps = steps[1:]
    return target if not steps else target.find("/".join(steps))
