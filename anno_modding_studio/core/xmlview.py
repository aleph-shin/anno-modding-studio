"""One asset as XML, a mod at a time: what each writes and what it leaves.

The asset editor's tree shows an asset as the mod being written into leaves
it.  That is a picture of the result, and no picture of how it came about:
which ops, of which mod, in which order.  The XML view shows that, and this
is what it asks.  For one asset, in load order: the asset as the game ships
it, then a :class:`Turn` for every loaded mod that reaches it -- the ModOps
that mod writes for it, as the Studio writes them, and the asset as that mod
hands it on.  The last turn's asset is what the game loads.

Nothing here is a second reading of anything.  The ModOps are
:func:`build.fragment_xml`'s, the same text *Show XML* gives for those rows;
the asset is :meth:`Workspace.effective_asset` stopped at that mod, the tree
the editor draws.  So the two views cannot disagree about what a mod does.

A search, and any other op that names more than this asset, belongs to more
than one asset.  An op that names this asset and no other, and sits in no
group, is the asset's own (:func:`is_own`): its text alone says what becomes
of it.  Those are :attr:`Turn.own_ops`, with the Studio's own changes and
the ops carried unread (:attr:`Turn.own_unread`).  An op in a group is the
group's as much as the asset's -- its condition is not in its text.

**The turn's ModOps hold both** (:attr:`Turn.modops`): the asset's own, and
among them in file order every other op of the mod that reaches it
(:func:`beyond_ops`, :func:`beyond_unread`).  Which lines are which is
:attr:`Turn.beyond`, so that whoever shows the text can draw an op that
reaches other assets as one that does: typed over there, it changes them
too.  A group's own tags are not in it, since a group holds ops that do not
reach this asset; an op of a group is there as the op, and the group is
typed over in the mod's file.
"""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from . import build, conflicts, modops
from . import paths as pathmod
from .changes import Change, Loadout, Op, Unread


@dataclass
class Beyond:
    """An op in a turn's ModOps that is not the asset's own: the lines it
    is on, counted from 1, and which op of the mod it is -- one kept, or one
    carried unread."""

    first: int
    last: int
    op: Optional[Op] = None
    unread: Optional[Unread] = None


@dataclass
class Turn:
    """One mod's turn at an asset, or the game's own before any of them."""

    # Which mod: its origin, "" for the loadout's own, None for the game.
    origin: Optional[str]
    # Its ModOps that reach this asset, in its file's order and less any
    # group's own tags: "" where it has none.  What is typed over.
    modops: str = ""
    # Those of them that are not the asset's own, with their lines.
    beyond: List[Beyond] = field(default_factory=list)
    # Every ModOp of its that reaches this asset, the ones that reach others
    # too among them, as it writes them and in its file's order.  What a turn
    # that is only read shows: the split above is for typing, and a reader
    # wants all of what the mod does here.
    whole: str = ""
    # The asset once it has run, laid out; "" where there is none yet --
    # before the mod that creates it -- or none any more.
    asset: str = ""
    # That asset as a tree, for finding a field's line and a line's field.
    element: Any = None
    # The mod changes go to, and whether this one loads after it.
    editing: bool = False
    later: bool = False
    # Its ops marked to be kept as written and its ops carried unread that
    # name this asset and no other: in ``modops``, with the changes.
    own_ops: List[Op] = field(default_factory=list)
    own_unread: List[Unread] = field(default_factory=list)
    # Its other kept ops and unread ops that reach this asset, and others too.
    reaching: List[Op] = field(default_factory=list)
    unread: List[Unread] = field(default_factory=list)
    # Those of ``reaching`` with no change here any more: it was reverted
    # from the list, and the op is written all the same.
    reverted: List[Op] = field(default_factory=list)
    # A later mod's turn: the fields it writes that the mod being edited
    # writes too, by ModOp path.  The editing mod's own turn: the same
    # fields, every later mod's together.
    contested: List[str] = field(default_factory=list)
    # The last turn: its asset is what the game loads.
    last: bool = False
    # The lines of ``asset``, counted from 1, that are not as the turn before
    # left them: what this mod did, whichever of its ops did it.
    changed: List[int] = field(default_factory=list)

    @property
    def is_game(self) -> bool:
        return self.origin is None


def laid_out(element) -> str:
    """An asset as text, two spaces a level; "" for none."""
    if element is None:
        return ""
    tree = copy.deepcopy(element)
    etree.indent(tree, space="  ")
    return etree.tostring(tree, encoding="unicode").rstrip() + "\n"


# ------------------------------------------------ what a turn left different
def _kids(node) -> list:
    return [child for child in node if isinstance(child.tag, str)]


def _key(node) -> Tuple:
    """A node as a value: two nodes with one key are one tree, however each
    is laid out."""
    return (
        node.tag, (node.text or "").strip(), tuple(sorted(node.attrib.items())),
        tuple(_key(child) for child in _kids(node)),
    )


def _parted(before, after, out: list) -> None:
    """Where ``after`` parts from ``before``, as ``(node of after, whole)``
    added to ``out``: the node with all that is in it, or its opening line
    alone where what differs is that something under it is gone.

    A list is gone into entry by entry, so one changed number in one entry
    is one line and not the list.  Entries added are theirs whole.
    """
    mine, theirs = _kids(before), _kids(after)
    if not mine and not theirs:
        out.append((after, True))
        return
    names = [child.tag for child in mine]
    found = len(out)
    if names == [child.tag for child in theirs] and len(set(names)) == len(names):
        # Fields each there once: each against the one of its name.
        for one, other in zip(mine, theirs, strict=True):
            if _key(one) != _key(other):
                _parted(one, other, out)
    else:
        # A list is matched by what its entries hold and never by their
        # place, however many there are: with one taken out and one added it
        # is as long as it was, and every entry between the two has moved up
        # one without the mod having touched it.
        have, now = [_key(child) for child in mine], [_key(child) for child in theirs]
        steps = difflib.SequenceMatcher(None, have, now, autojunk=False).get_opcodes()
        for step, a1, a2, b1, b2 in steps:
            if step == "equal":
                continue
            if step == "replace" and a2 - a1 == b2 - b1 and all(
                mine[a1 + at].tag == theirs[b1 + at].tag for at in range(a2 - a1)
            ):
                for at in range(a2 - a1):
                    _parted(mine[a1 + at], theirs[b1 + at], out)
            elif b2 > b1:
                out.extend((child, True) for child in theirs[b1:b2])
            else:
                out.append((after, False))
    if len(out) == found:
        # What is under it is as it was, so it is its own line that differs.
        out.append((after, False))


def changed_lines(before, element, text: str) -> List[int]:
    """The lines of ``text`` -- ``element`` laid out -- that are not as
    ``before`` has them, counted from 1.  None where there was no asset
    before, or is none now: an asset a mod adds is all that mod's."""
    if before is None or element is None or not text or _key(before) == _key(element):
        return []
    parted: list = []
    _parted(before, element, parted)
    numbered = _numbered(text)
    if numbered is None:
        return []
    tree = etree.ElementTree(element)
    lines: set = set()
    for node, whole in parted:
        where = tree.getelementpath(node)
        twin = numbered if where == "." else numbered.find(where)
        if twin is None or twin.sourceline is None:
            continue
        run = etree.tostring(twin, with_tail=False).count(b"\n") if whole else 0
        lines.update(range(twin.sourceline, twin.sourceline + run + 1))
    return sorted(lines)


def _written_over(loadout: Loadout, guid: str, editing: str) -> Dict[str, List[str]]:
    """The nodes of ``guid`` that the mod being edited writes and a later mod
    writes over: later mod -> their ModOp paths.

    What counts is :mod:`conflicts`' to say, so this view and the changes
    list cannot disagree: two mods setting a field to one value is not a
    clash, nor is a patch overriding the mod it patches.
    """
    found: Dict[str, List[str]] = {}
    for clash in conflicts.find(loadout):
        if clash.guid != guid or clash.is_harmless or editing not in clash.origins:
            continue
        behind = clash.origins[clash.origins.index(editing) + 1:]
        for origin in behind:
            found.setdefault(origin, []).append(clash.path)
    return found


def alone(loadout: Loadout, origin: str, changes) -> List[Change]:
    """Those of ``changes`` -- one mod's, about one asset -- that are that
    asset's alone: not the answers of a kept op of the mod's, which are
    written as the op."""
    held = {op.uid for op in loadout.ops if op.origin == origin}
    return [change for change in changes if change.op_uid not in held]


def own_changes(workspace, guid: str, origin: str) -> List[Change]:
    """The changes a turn's ModOps are written from: ``origin``'s, about
    ``guid`` alone."""
    return alone(workspace.loadout, origin, [
        change for change in workspace.changes_for(str(guid)) if change.origin == origin
    ])


def names_only(source: str, guid: str, game: str = "") -> bool:
    """Whether a ModOp, as written, names ``guid`` and no other asset, as
    ``game`` reads it."""
    op = modops.read_op(source, game=game)
    return op is not None and op.guids == [str(guid)]


def creates_only(source: str, guid: str) -> bool:
    """Whether a ModOp, as written, adds the asset ``guid`` and nothing else:
    its body is that one ``<Asset>``.  Such an op names the asset it is put
    beside, and is still the new asset's own."""
    try:
        node = etree.fromstring((source or "").strip().encode("utf-8"))
    except etree.XMLSyntaxError:
        return False
    inside = [child for child in node if isinstance(child.tag, str)]
    return (
        len(inside) == 1 and inside[0].tag == "Asset"
        and (inside[0].findtext("Values/Standard/GUID") or "").strip() == str(guid)
    )


def is_own(op: Op, guid: str, game: str = "") -> bool:
    """Whether a kept op is ``guid``'s own: one whose text alone says what
    becomes of it, so that it stands in the turn's ModOps and is typed over
    there.  It names this asset and no other, or adds it and nothing else,
    and is no group and in none.
    """
    return not op.scope and not op.within and (
        names_only(op.source, guid, game) or creates_only(op.source, guid)
    )


def op_text(loadout: Loadout, origin: str, key: Tuple) -> str:
    """The op the Studio writes for the change ``key``, alone and as it
    stands in a turn's ModOps; "" where it is not written as one."""
    return run_text(loadout, origin, [key])


def run_text(loadout: Loadout, origin: str, keys) -> str:
    """The one op the Studio writes for the changes ``keys`` together; ""
    where it writes them as more than one.  Nodes put beside the same node
    are written as one op (``core.generator._side_by_side``), and that op is
    what a turn's ModOps show for all of them."""
    try:
        root = etree.fromstring(build.fragment_xml(loadout, origin, list(keys)).encode("utf-8"))
    except etree.XMLSyntaxError:
        return ""
    ops = [child for child in root if isinstance(child.tag, str)]
    return etree.tostring(ops[0], encoding="unicode", with_tail=False) if len(ops) == 1 else ""


def own_ops(loadout: Loadout, guid: str, origin: str) -> List[Op]:
    """The kept ops of ``origin`` that are ``guid``'s alone (:func:`is_own`),
    in the order they are written."""
    return sorted(
        (
            op for op in loadout.ops
            if op.origin == origin and op.active and is_own(op, guid, loadout.game)
        ),
        key=lambda op: op.order,
    )


def own_unread(loadout: Loadout, guid: str, origin: str) -> List[Unread]:
    """The ops of ``origin`` carried unread that are ``guid``'s alone."""
    return [
        entry for entry in loadout.unread
        if entry.origin == origin and names_only(entry.source, guid, loadout.game)
    ]


def _is_one_op(source: str) -> bool:
    """Whether an op as written is one ``<ModOp>``, and not a group the
    reader kept whole."""
    return (source or "").lstrip().startswith("<ModOp")


def beyond_ops(workspace, guid: str, origin: str) -> List[Op]:
    """The ops of ``origin`` that reach ``guid`` and are not its own, in the
    order they are written: one that answered to it when it was read, with
    a change about it or without -- reverted here, or in a group that does
    not run.  No group's own tag, no group kept whole, which is typed over
    in the mod's file with the ops it holds, and nothing switched off.
    """
    guid = str(guid)
    loadout = workspace.loadout
    with workspace.steady():
        at = {
            change.op_uid for change in workspace.changes_for(guid)
            if change.origin == origin and change.op_uid
        }
    return sorted(
        (
            op for op in loadout.ops
            if op.origin == origin and op.active and not op.scope
            and (op.uid in at or guid in op.matched)
            and _is_one_op(op.source)
            and not is_own(op, guid, loadout.game)
        ),
        key=lambda op: op.order,
    )


_CONDITION = re.compile(r"""\bCondition\s*=\s*(["'])(.*?)\1""", re.S)


def group_condition(loadout: Loadout, op: Op) -> str:
    """What the group ``op`` sits in asks, as its mod wrote it: the
    innermost one's ``Condition``, or "" where it has none or ``op`` is in
    no group."""
    group = loadout.op(op.within[-1]) if op.within else None
    asked = _CONDITION.search(modops.opening(group.source)) if group is not None else None
    return " ".join(asked.group(2).split()) if asked is not None else ""


def beyond_unread(workspace, guid: str, origin: str) -> List[Unread]:
    """The ops of ``origin`` carried unread that are aimed at ``guid`` and
    not at it alone."""
    guid = str(guid)
    loadout = workspace.loadout
    with workspace.steady():
        whole = workspace.effective_asset(guid).as_the_game_reads_it()
    return [
        entry for entry in unread_reaching(loadout, guid, whole, [origin])
        if not names_only(entry.source, guid, loadout.game)
    ]


def unread_reaching(
    loadout: Loadout, guid: str, root, origins=None, upto=None,
) -> List[Unread]:
    """The ops carried unread that are aimed at ``guid``: of ``origins``, or
    of every mod the game loads -- up to ``upto`` in load order where it is
    given, as :func:`reverted_reaching` is asked.

    Ops aimed by GUID are matched by it; a search is put to ``root``, the
    asset as it stands, which is what it would be put to in game.
    """
    found = []
    off = loadout.not_loading() if origins is None else ()
    rank = loadout.layer_rank(upto) if upto is not None else None
    for entry in loadout.unread:
        if entry.origin in off or (origins is not None and entry.origin not in origins):
            continue
        if rank is not None and loadout.layer_rank(entry.origin) > rank:
            continue
        op = modops.read_op(entry.source, game=loadout.game)
        if op is None:
            continue
        if op.guids:
            if guid in op.guids:
                found.append(entry)
        elif op.path and root is not None:
            try:
                if modops.matches_anything(root, op.path):
                    found.append(entry)
            except Exception:  # noqa: BLE001 - an op that would not search is not aimed here
                continue
    return found


def _reverted(loadout: Loadout, guid: str, changes, off) -> Dict[str, List[Op]]:
    """The ops that answered to ``guid`` and have no change about it now,
    by the mod they are of.

    Reverting a change does not take its asset out of an op that names
    others too, or finds its assets by searching: the op is what is written,
    so it does to this asset in game what it did.  With the change went the
    only thing that said so here, and the XML view called the asset one the
    mod does not change.  Told as the changes list tells it
    (:func:`keptops._caveat`): by an op that came to more changes than it
    has left.
    """
    left: Dict[str, int] = {}
    for change in loadout.changes.values():
        if change.op_uid:
            left[change.op_uid] = left.get(change.op_uid, 0) + 1
    here = {change.op_uid for change in changes}
    found: Dict[str, List[Op]] = {}
    for op in loadout.ops:
        if (
            op.active and not op.scope and op.origin not in off
            and op.uid not in here and guid in op.matched
            and op.made > left.get(op.uid, 0)
        ):
            found.setdefault(op.origin, []).append(op)
    return found


def _undone(reverted: Dict[str, List[Op]], guid: str, origin: str, game: str) -> List[Op]:
    """Those of ``reverted`` a turn of ``origin`` names: an op that is the
    asset's own stands in the turn's ModOps, and is not one beside them."""
    return [op for op in reverted.get(origin, []) if not is_own(op, guid, game)]


def reverted_reaching(workspace, guid: str, upto: str) -> List[Op]:
    """The ops, of every mod loaded up to ``upto``, that still reach ``guid``
    with their change to it reverted: what those mods' turns name as
    ``reverted``, in load order, for a view that shows the asset and not the
    turns.

    Every mod up to that one, as :func:`unread_reaching` is asked: the asset
    such a view shows is what all of them leave, so an earlier mod's op is as
    much missing from it as one of ``upto``'s own.
    """
    guid = str(guid)
    loadout = workspace.loadout
    with workspace.steady():
        changes = workspace.changes_for(guid)
    reverted = _reverted(loadout, guid, changes, loadout.not_loading())
    rank = loadout.layer_rank(upto)
    found: List[Op] = []
    for origin in sorted(reverted, key=loadout.layer_rank):
        if loadout.layer_rank(origin) <= rank:
            found += _undone(reverted, guid, origin, loadout.game)
    return found


def turns(workspace, guid: str, editing: str = "") -> List[Turn]:
    """``guid`` as XML: the game's asset, then a turn for each mod reaching it.

    A mod reaches the asset when a change of its is about it, a kept op of
    its answers to it, or an op of its carried unread is aimed at it.  The mod
    being edited into (``editing``) has a turn whether it reaches it or not:
    it is where the next change goes.  Switched-off mods and changes are left
    out, as they are from the asset.
    """
    guid = str(guid)
    loadout = workspace.loadout
    with workspace.steady():
        changes = workspace.changes_for(guid)
        is_new = workspace.is_new_asset(guid)
        shipped = None if is_new else workspace.base_element(guid)
        whole = workspace.effective_asset(guid).as_the_game_reads_it()

        by_origin: Dict[str, List[Change]] = {}
        for change in changes:
            by_origin.setdefault(change.origin, []).append(change)
        shadow: Dict[str, List[Unread]] = {}
        for entry in unread_reaching(loadout, guid, whole):
            shadow.setdefault(entry.origin, []).append(entry)
        off = loadout.not_loading()
        # A kept op that comes to nothing now is still an op of its mod's.
        keeping = {
            op.origin for op in loadout.ops
            if op.active and op.origin not in off and is_own(op, guid, loadout.game)
        }
        # And so is one that answered to it when it was read, as
        # beyond_ops counts it: one in a group that does not run has no
        # change here, and its mod had a turn only while being edited.
        answering = {
            op.origin for op in loadout.ops
            if op.active and op.origin not in off and not op.scope
            and guid in op.matched and _is_one_op(op.source)
        }

        reverted = _reverted(loadout, guid, changes, off)

        origins = sorted(
            set(by_origin) | set(shadow) | keeping | answering | set(reverted) | {editing},
            key=loadout.layer_rank,
        )
        rank = loadout.layer_rank(editing)
        over = _written_over(loadout, guid, editing)

        found = [Turn(origin=None, asset=laid_out(shipped), element=shipped)]
        for origin in origins:
            its_ops = {op.uid: op for op in loadout.ops if op.origin == origin}
            theirs = by_origin.get(origin, [])
            keys = [change.key for change in alone(loadout, origin, theirs)]
            # What applying the pane compares the typed text against
            # (typedops.apply), so that the two agree: for the mod being
            # edited while it is not loading as well, whose ops nothing above
            # counts, and which typing its pane back would otherwise drop.
            kept = own_ops(loadout, guid, origin)
            carried = own_unread(loadout, guid, origin)
            answered = []
            for change in theirs:
                op = its_ops.get(change.op_uid)
                if op is not None and op not in answered and op not in kept:
                    answered.append(op)
            undone = _undone(reverted, guid, origin, loadout.game)
            answered += undone
            element = workspace.effective_asset(guid, upto=origin).as_the_game_reads_it()
            beside = [entry for entry in shadow.get(origin, []) if entry not in carried]
            beyond = beyond_ops(workspace, guid, origin)
            # One with no change here, in a group that does not run, is in
            # the pane typed in: the one only read has it too.
            answered += [op for op in beyond if op not in answered]
            typed_over = build.fragment_written(
                loadout, origin, keys,
                [op.uid for op in kept + beyond],
                [entry.order for entry in carried + beside],
            ) if keys or kept or beyond or carried or beside else None
            turn = Turn(
                origin=origin,
                whole=build.fragment_xml(
                    loadout, origin, keys,
                    _with_groups(kept + answered),
                    [entry.order for entry in carried + beside],
                ) if keys or kept or answered or carried or beside else "",
                modops=typed_over.text if typed_over is not None else "",
                beyond=[
                    Beyond(span.first, span.last, span.op, span.unread)
                    for span in (typed_over.spans if typed_over is not None else ())
                    if any(span.op is op for op in beyond)
                    or any(span.unread is entry for entry in beside)
                ],
                asset=laid_out(element),
                element=element,
                editing=origin == editing,
                later=loadout.layer_rank(origin) > rank,
                own_ops=kept,
                own_unread=carried,
                reaching=answered,
                unread=beside,
                reverted=undone,
            )
            if turn.later:
                turn.contested = sorted(set(over.get(origin, ())))
            turn.changed = changed_lines(found[-1].element, element, turn.asset)
            found.append(turn)

        for turn in found:
            if turn.editing:
                turn.contested = sorted({
                    path for later in found if later.later for path in later.contested
                })
        found[-1].last = True
    return found


def also_xml(loadout: Loadout, turn: Turn) -> str:
    """The kept ops and unread ops of ``turn``'s mod that reach the asset, as
    that mod writes them."""
    if turn.origin is None or not (turn.reaching or turn.unread):
        return ""
    return build.fragment_xml(
        loadout, turn.origin, (),
        _with_groups(turn.reaching), [entry.order for entry in turn.unread],
    )


def _with_groups(ops) -> List[str]:
    """The uids of ``ops`` and of every group one of them sits in: written
    without its group an op looks as though it always ran."""
    found: List[str] = []
    for op in ops:
        for uid in [*op.within, op.uid]:
            if uid not in found:
                found.append(uid)
    return found


# ------------------------------------------------- a field's line, and back
def _numbered(text: str):
    """``text`` parsed again, so each element knows the line it starts on."""
    try:
        return etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None


def line_of(turn: Turn, path: str) -> Optional[int]:
    """The line of ``turn.asset`` the node at ModOp ``path`` starts on,
    counted from 1; None where the path finds nothing there."""
    if turn.element is None or not turn.asset:
        return None
    nodes = pathmod.resolve(turn.element, path)
    if not nodes or not isinstance(getattr(nodes[0], "tag", None), str):
        return None
    numbered = _numbered(turn.asset)
    if numbered is None:
        return None
    where = etree.ElementTree(turn.element).getelementpath(nodes[0])
    twin = numbered if where == "." else numbered.find(where)
    return twin.sourceline if twin is not None else None


def path_at(turn: Turn, line: int) -> Optional[str]:
    """The ModOp path of the node ``line`` of ``turn.asset`` belongs to: the
    one that starts there, else the nearest one starting above it."""
    if turn.element is None or not turn.asset:
        return None
    numbered = _numbered(turn.asset)
    if numbered is None:
        return None
    best = None
    for node in numbered.iter():
        if not isinstance(node.tag, str) or node.sourceline is None:
            continue
        if node.sourceline <= line and (best is None or node.sourceline >= best.sourceline):
            best = node
    if best is None:
        return None
    where = etree.ElementTree(numbered).getelementpath(best)
    twin = turn.element if where == "." else turn.element.find(where)
    if twin is None:
        return None
    try:
        return pathmod.path_for(turn.element, twin).path
    except ValueError:
        return None


def line_for(turn: Turn, change: Change) -> Optional[int]:
    """The line of ``turn.asset`` a change of that turn's lands on: the node
    it sets, or the one it adds to where what it adds has no line of its own
    to be told by."""
    paths = [change.path]
    if change.tag and change.payload and not change.path.endswith("/" + change.tag):
        paths.insert(0, change.path.rstrip("/") + "/" + change.tag)
    for path in paths:
        try:
            line = line_of(turn, path)
        except Exception:  # noqa: BLE001 - a path that will not resolve has no line
            line = None
        if line is not None:
            return line
    return None


def op_line(loadout: Loadout, turn: Turn, key: Tuple) -> Optional[int]:
    """The line of ``turn.modops`` the op for change ``key`` starts on, counted
    from 1; None where it is not written there as an op of its own."""
    if turn.origin is None or not turn.modops:
        return None
    return _line_of_op(turn.modops, build.fragment_xml(loadout, turn.origin, [key]))


def kept_line(loadout: Loadout, turn: Turn, uid: str) -> Optional[int]:
    """The same for an op kept as written that is the turn's own, by its uid."""
    if turn.origin is None or not turn.modops:
        return None
    return _line_of_op(turn.modops, build.fragment_xml(loadout, turn.origin, (), [uid]))


def shown_op_line(loadout: Loadout, turn: Turn, change: Change) -> Optional[int]:
    """The line the op ``change`` comes of starts on, counted from 1, in the
    ModOps ``turn`` shows: ``modops`` on the tab typed in, ``whole`` on one
    only read.  A kept op's change is written as the op (``build.fragment_xml``);
    an op kept as written is asked for by its uid where that finds nothing."""
    text = turn.modops if turn.editing else turn.whole
    if turn.origin is None or not text:
        return None
    line = _line_of_op(text, build.fragment_xml(loadout, turn.origin, [change.key]))
    if line is None and change.op_uid:
        line = _line_of_op(text, build.fragment_xml(loadout, turn.origin, (), [change.op_uid]))
    return line


def _line_of_op(text: str, alone: str) -> Optional[int]:
    """The line of ``text`` the first op of ``alone`` starts on, counted from
    1.  Of several lines that open it alike -- one merge after another into
    the same node -- the one followed furthest by the lines of ``alone``."""
    written = [line.strip() for line in alone.splitlines()]
    start = next((at for at, line in enumerate(written) if line.startswith("<ModOp ")), None)
    if start is None:
        return None
    written = written[start:]
    lines = [line.strip() for line in text.splitlines()]
    best, longest = None, 0
    for at, line in enumerate(lines):
        if line != written[0]:
            continue
        run = 1
        while run < len(written) and at + run < len(lines) and lines[at + run] == written[run]:
            run += 1
        if run > longest:
            best, longest = at + 1, run
    return best
