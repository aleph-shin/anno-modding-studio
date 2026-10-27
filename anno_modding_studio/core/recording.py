"""What a reading comes to, written down as changes.

An op the reader runs leaves its target different from what it was.  This is
how that difference is written down: the diff of a node against what it was
(:func:`diff`), each change keyed to a path that names one node and holds
(:class:`Sheet`), the payload of a change while the reading is still adding to
it, and a change replayed against a document to check it lands where it says.
"""

from __future__ import annotations

import bisect
import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from . import (
    applying,
    effective,
    games,
    refs,
)
from . import paths as pathmod
from .changes import (
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
    Change,
)
from .i18n import STORED, tr_noop
from .modop import (
    MOD_ITEM,
)
from .modops import (
    Unrepresentable,
)


# ------------------------------------------------------------------ the diffing
def is_leaf(node) -> bool:
    return not any(isinstance(child.tag, str) for child in node)


def text(node) -> str:
    return (node.text or "").strip()


def _by_tag(node) -> Dict[str, List]:
    groups: Dict[str, List] = {}
    for child in node:
        if isinstance(child.tag, str):
            groups.setdefault(child.tag, []).append(child)
    return groups


def _shared_keys(mine: List, theirs: List):
    """Every child value that might tell these entries apart, best first.

    Deliberately the same order :func:`paths.key_candidates` walks, so a list
    is paired up by whatever would address a row in it -- including the key a
    wrapper holds one level down, which is how Anno 117 writes them.
    """
    entries = list(mine) + list(theirs)
    seen = set()
    for key in pathmod.KEY_CHILDREN:
        seen.add(key)
        yield key
    for entry in entries:
        for child in entry:
            if not isinstance(child.tag, str) or child.tag in seen:
                continue
            if len(child) == 0 and (child.text or "").strip():
                seen.add(child.tag)
                yield child.tag
    for entry in entries:
        for child in entry:
            if not isinstance(child.tag, str):
                continue
            for grandchild in child:
                if not isinstance(grandchild.tag, str):
                    continue
                nested = "%s/%s" % (child.tag, grandchild.tag)
                if nested in seen:
                    continue
                if len(grandchild) == 0 and (grandchild.text or "").strip():
                    seen.add(nested)
                    yield nested


def _pair_by_key(mine: List, theirs: List) -> List[Tuple[Optional[object], Optional[object]]]:
    """Line up two lists of same-tagged entries by whatever identifies them.

    Repeated siblings are the norm in Anno's lists, and position is no way to
    match them: the mod is often *reordering* the list, so matching by position
    would rewrite every entry instead of moving one.  An entry is matched by the
    child value that identifies it, exactly as :func:`paths.path_for` addresses
    one.
    """
    for key in _shared_keys(mine, theirs):
        mine_keyed = {(entry.findtext(key) or "").strip(): entry for entry in mine}
        theirs_keyed = {(entry.findtext(key) or "").strip(): entry for entry in theirs}
        if len(mine_keyed) != len(mine) or len(theirs_keyed) != len(theirs):
            continue
        if "" in mine_keyed or "" in theirs_keyed:
            continue
        pairs = [(entry, theirs_keyed.get(value)) for value, entry in mine_keyed.items()]
        pairs.extend(
            (None, entry)
            for value, entry in theirs_keyed.items()
            if value not in mine_keyed
        )
        return pairs
    stuck = Unrepresentable(
        tr_noop("its %(count)d <%(tag)s> entries have nothing unique to match them on",
                context=STORED)
        % {"count": max(len(mine), len(theirs)), "tag": (mine or theirs)[0].tag}
    )
    stuck.at = mine[0].getparent() if mine else None
    raise stuck


@dataclass
class Edit:
    """One narrow edit, in terms of the live working tree.

    ``elements`` is a list because a single insertion can place a run of
    siblings, and that run has to stay one change: four ModOps all inserting
    after the same node each land immediately after it, which reverses them.

    A ``silent`` edit still happens to the working tree -- so that later ops
    read the document the mod really leaves behind -- but produces no ModOp,
    because the game would not notice it either way.
    """

    op: str
    # An lxml element: the node in the working tree to aim at. `Any` rather
    # than `object` because lxml ships no types this build can see, and
    # `object` does not mean "untyped" -- it means "has no attributes", which
    # made every use of this field look like a mistake.
    node: Any
    elements: tuple = ()    # what to put there, for the additions
    new: str = ""
    silent: bool = False
    # An addition a merge made: the field was not there, so the merge put it
    # there -- and would have set it had it been.  Carried out as the addition
    # it is and written as the merge it was; see :attr:`Change.ensured`.
    ensure: bool = False
    # A value the op gives a field that already reads that way only because
    # another mod set it. Nothing changes in the stack and it is this mod's
    # change all the same; see Translator._agreed.
    agreed: bool = False


class Inert:
    """Whether deleting a list entry is something the game would notice.

    A cost list names goods and gives amounts, and the shipped file leaves the
    amount off a good that costs nothing: 893 of Anno 117's cost entries are
    bare like that, and 1763 of Anno 1800's.  Such an entry and no entry at all
    come to the same thing, so removing one changes nothing.

    That matters here because a mod written as one wide ``replace`` of a list
    says, in XPath terms, "these are now the only entries" -- and read strictly,
    that deletes every bare entry the author simply did not bother to retype.
    The Seal shrine mod produces 69 such deletions and means none of them.

    Whether a bare entry is a placeholder or the whole point is not a judgement
    call: it is measured, by the same reading :mod:`.refs` already uses to tell
    a link that spends something from one that only names an asset.  Cost
    entries set an amount 53% of the time, so a bare one is a placeholder.
    Product-list and effect-target entries never set anything, so a bare one is
    the entry -- and removing it is a real removal.
    """

    def __init__(self, view):
        self.view = view
        # Both of these are one SQL query each, and this runs over every node of
        # every asset a mod touches.
        self._paths = {}
        self._fields = {}

    def _uses_values(self, path: str, tag: str) -> bool:
        key = (path, tag)
        if key not in self._paths:
            entries, valued = self.view.reference_entries(path, tag)
            self._paths[key] = refs.path_uses_values(entries, valued)
        return self._paths[key]

    def _is_link(self, path: str, tag: str) -> bool:
        key = (path, tag)
        if key not in self._fields:
            self._fields[key] = self.view.is_reference_field(path, tag)
        return self._fields[key]

    def __call__(self, node) -> bool:
        if self.view is None or node.getparent() is None or not len(node):
            return False
        path = plain_path(node)
        if not path:
            return False
        pointer = None
        for child in node.iter():
            if not isinstance(child.tag, str) or child is node or len(child):
                continue
            if pointer is None and self._is_link(path, child.tag):
                pointer = child.tag
                continue
            if refs.has_content(child):
                return False  # it sets something, so dropping it is a real edit
        if pointer is None:
            return False  # not a pointer entry at all
        return self._uses_values(path, pointer)


def plain_path(node) -> str:
    """``/Values/Cost/Costs/Item``: where a node sits, with no predicates.

    The addressing the reference index is keyed by, as opposed to the ModOp
    path :mod:`.paths` generates, which identifies one particular entry.
    """
    steps = []
    while node is not None and isinstance(node.tag, str) and node.tag != "Asset":
        steps.append(node.tag)
        node = node.getparent()
    steps.reverse()
    return ("/" + "/".join(steps)) if steps else ""


def path_in(root, node) -> str:
    """The ModOp path of ``node`` in the asset ``root``; "" where it is not
    in it."""
    try:
        return pathmod.path_for(root, node).path
    except ValueError:
        return ""


def anchor_of(target, swapped=()):
    """A sibling to hang a replacement for ``target`` off, and which side it goes.

    ``swapped`` are the other nodes the same op swaps whole, which are gone by
    the time this replacement goes in, so none of them is an anchor. A run of
    them hangs off what follows it, each put before it in turn, which keeps
    their order. Hung off a node already taken out, a replacement went nowhere,
    and the reader had nothing to show for it.
    """
    previous = target.getprevious()
    while previous is not None and not isinstance(previous.tag, str):
        previous = previous.getprevious()
    if previous is not None and not any(previous is node for node in swapped):
        return previous, OP_ADD_NEXT
    following = target.getnext()
    while following is not None and (
        not isinstance(following.tag, str) or any(following is node for node in swapped)
    ):
        following = following.getnext()
    if following is not None:
        return following, OP_ADD_PREV
    parent = target.getparent()
    return (parent, OP_ADD) if parent is not None else (None, "")


def _moved(existing: List, entries: List, partner: Dict[int, object]) -> set:
    """Which of the entries a replace keeps it also moves, as ``id``s of its own.

    No narrow op moves an entry, so a replace that puts the ones it keeps in a
    new order has each moved entry taken out and put back where the mod has it.
    Everything in the longest run that is already in order stays where it is,
    which moves as few entries as possible and leaves them as anchors for the
    rest -- no neighbour outside the list is needed, and the list's other
    entries are still edited field by field.
    """
    at = {id(entry): position for position, entry in enumerate(existing)}
    kept = [(at[id(partner[id(entry)])], id(entry)) for entry in entries if id(entry) in partner]
    tails: List[int] = []
    ends: List[int] = []
    before: Dict[int, Optional[int]] = {}
    for index, (position, _entry) in enumerate(kept):
        slot = bisect.bisect_left(tails, position)
        if slot == len(tails):
            tails.append(position)
            ends.append(index)
        else:
            tails[slot] = position
            ends[slot] = index
        before[index] = ends[slot - 1] if slot else None
    stay = set()
    step = ends[-1] if ends else None
    while step is not None:
        stay.add(kept[step][1])
        step = before[step]
    return {entry for _position, entry in kept if entry not in stay}


def _placed(base, tag: str, entries: List, partner: Dict[int, Any]) -> List[Edit]:
    """The additions a replace makes to one list, each where the mod puts it.

    Appending was right only for entries that come after everything the list
    keeps. *[Hadleys] StartInseln* retypes a fertility set with new entries
    between the shipped ones, and appending them left the Studio showing, and
    exporting, a list in an order the mod never wrote.

    So a run of new entries goes in beside the kept entry before it -- or
    before the kept entry after it, when it leads the list -- as one addition,
    since several ModOps inserting beside the same node land in reverse. A run
    that ends the list is still appended entry by entry, which is the same place
    and the narrowest way to say it.
    """
    edits: List[Edit] = []
    run: List = []
    last = None
    for entry in entries:
        kept = partner.get(id(entry))
        if kept is None:
            run.append(entry)
            continue
        if run:
            if last is not None:
                edits.append(Edit(OP_ADD_NEXT, last, elements=tuple(run)))
            else:
                edits.append(Edit(OP_ADD_PREV, kept, elements=tuple(run)))
            run = []
        last = kept
    if run:
        leaving = {id(node) for node in partner.values()}
        after = []
        if last is not None:
            sibling = last.getnext()
            while sibling is not None:
                # What the replace drops of this list is gone by the time the
                # additions land, so it does not count as standing in the way.
                if isinstance(sibling.tag, str) and not (
                    sibling.tag == tag and id(sibling) not in leaving
                ):
                    after.append(sibling)
                sibling = sibling.getnext()
        if last is None or not after:
            edits.extend(Edit(OP_ADD, base, elements=(entry,)) for entry in run)
        else:
            edits.append(Edit(OP_ADD_NEXT, last, elements=tuple(run)))
    return edits


def diff(
    base, want, edits: List[Edit], removals: bool, inert=None, same=None, paired=None
) -> None:
    """Work out what it would take to make ``base`` read like ``want``.

    ``inert`` names the removals the diff should carry out but not write down --
    entries a wide replace drops by omission and the game never reads.

    ``same`` collects the fields that hold a value and already read as wanted,
    for a caller that can tell the game's own value from another mod's.

    A replace (``removals``) also keeps each list in the mod's order: entries it
    keeps in a new order are moved, and new ones go beside their neighbours --
    see :func:`_moved` and :func:`_placed`. Those insertions come last, after
    everything inside the list has been changed, so the entry one is placed
    beside is addressed the way it reads by then: *Free Ornaments* strips the
    Ingredient from cost entries it also inserts beside, and a path keyed on
    that Ingredient matched nothing once the removals had run.

    ``paired`` is ``(base child, want child)`` for each of ``base``'s children,
    for a caller that knows which is which: they are then not matched by key,
    at this level only. See :func:`diff_mod_items`.
    """
    base_leaf, want_leaf = is_leaf(base), is_leaf(want)
    if base_leaf and want_leaf:
        if text(base) != text(want):
            # Out of a merge, which would have made the field had it not been
            # there: where another mod made it, that is still what to write.
            edits.append(Edit(OP_REPLACE, base, new=text(want), ensure=not removals))
        elif same is not None and text(want):
            same.append(base)
        return
    # An empty element is not really a value; assets are full of containers
    # that ship empty and are filled in by a mod.  A leaf with text on one side
    # and children on the other is a genuine shape change.
    if base_leaf and text(base):
        raise Unrepresentable(
            tr_noop("<%s> holds a value in your assets.xml and a container in the mod",
                    context=STORED) % base.tag
        )
    if want_leaf and text(want):
        raise Unrepresentable(
            tr_noop("<%s> holds a container in your assets.xml and a value in the mod",
                    context=STORED) % base.tag
        )

    mine = _by_tag(base)
    theirs = _by_tag(want)
    placements: List[Edit] = []
    known = None if paired is None else {id(want_child): base_child for base_child, want_child in paired}
    for tag, entries in theirs.items():
        existing = mine.get(tag, [])
        if known is not None:
            pairs = [(known.get(id(entry)), entry) for entry in entries]
            met = {id(base_child) for base_child, _entry in pairs if base_child is not None}
            pairs.extend((base_child, None) for base_child in existing if id(base_child) not in met)
        elif len(entries) == 1 and len(existing) <= 1:
            pairs = [(existing[0] if existing else None, entries[0])]
        elif not existing:
            pairs = [(None, entry) for entry in entries]
        else:
            pairs = _pair_by_key(existing, entries)
        moving: set = set()
        if removals:
            partner = {
                id(want_child): base_child
                for base_child, want_child in pairs
                if base_child is not None and want_child is not None
            }
            moving = _moved(existing, entries, partner)
            for entry in moving:
                del partner[entry]
            placements.extend(_placed(base, tag, entries, partner))
        for base_child, want_child in pairs:
            if want_child is not None and id(want_child) in moving:
                # Taken out here and put back by the placements, in one piece.
                edits.append(Edit(OP_REMOVE, base_child))
            elif base_child is None:
                if not removals:
                    # Only a merge comes this way, and only where no name is
                    # met twice, so the field is the one of its name: made
                    # here, and set wherever something else made it first.
                    # Not one named like the node it goes into: a merge
                    # carrying that alone is read as naming its target twice
                    # (see _merge_wanted), and written back as a merge it
                    # would empty its fields into the node instead.
                    edits.append(Edit(
                        OP_ADD, base, elements=(want_child,),
                        ensure=want_child.tag != base.tag,
                    ))
            elif want_child is None:
                if removals:
                    edits.append(
                        Edit(OP_REMOVE, base_child, silent=bool(inert and inert(base_child)))
                    )
            else:
                diff(base_child, want_child, edits, removals, inert, same)
    if removals:
        for tag, entries in mine.items():
            if tag not in theirs:
                edits.extend(
                    Edit(OP_REMOVE, entry, silent=bool(inert and inert(entry)))
                    for entry in entries
                )
    edits.extend(placements)


# ---------------------------------------------------- the changes as they are made
class _Span:
    """The nodes one change put into the working tree, still live.

    A change's payload is not serialised until every op has run, so an edit
    that lands inside something this import added needs no ModOp of its own --
    it simply happens, and shows up in the payload because the payload *is*
    those nodes.  A span rather than a single node because one insertion can
    place a run of siblings, and a later op may well insert another one into
    the middle of that run.
    """

    def __init__(self, change: Change, nodes):
        self.change = change
        self.nodes = list(nodes)
        # Frozen once the mod that made it has finished. Later mods edit these
        # nodes through ModOps of their own, and must not be written into this
        # mod's payload -- that would ship somebody else's work inside it.
        self.settled = False
        # The payload as it stood when a kept op of the same mod first reached
        # inside these nodes. A kept op goes out as the op it is, so what it does
        # here must not be in this payload as well -- an addition would be made
        # twice -- and must not be missing from it either: an op that replaces
        # an entry needs the entry there to replace. So from that moment the
        # payload is what it was, and everything after is somebody else's op.
        self.frozen: Optional[str] = None
        # The sections another mod made that the nodes sit inside, outermost
        # first, for an ensure aimed past them at a node the game ships: the
        # payload is the nodes inside those sections. See Translator._anchored.
        self.wrap: tuple = ()

    def holds(self, node) -> bool:
        return any(member is node for member in self.nodes)

    def add(self, node) -> None:
        self.nodes.append(node)

    def drop(self, node) -> None:
        self.nodes = [member for member in self.nodes if member is not node]

    def freeze(self) -> None:
        if self.frozen is None:
            self.frozen = self.payload()

    def payload(self) -> str:
        if self.frozen is not None:
            return self.frozen
        parent = self.nodes[0].getparent() if self.nodes else None
        ordered = self.nodes
        if parent is not None:
            positions = {id(child): index for index, child in enumerate(parent)}
            ordered = sorted(self.nodes, key=lambda node: positions.get(id(node), 0))
        if self.wrap:
            top, inner = nested(self.wrap)
            for node in ordered:
                inner.append(copy.deepcopy(node))
            return serialise(top)
        return "\n".join(serialise(copy.deepcopy(node)) for node in ordered)


def nested(tags):
    """``(outermost, innermost)`` of empty elements nested by ``tags``."""
    top = inner = etree.Element(tags[0])
    for tag in tags[1:]:
        inner = etree.SubElement(inner, tag)
    return top, inner


_STEP = re.compile(r"/((?:[^/\[]|\[[^\]]*\])+)")
_KEYED_STEP = re.compile(r"^([\w.-]+)\[([\w./-]+)=(?:'([^']*)'|\"([^\"]*)\")\]$")


def _leaf_fields(entry, depth: int = 3) -> List[str]:
    """Every field inside ``entry`` that holds a value, as a relative path, nearest first."""
    found: List[str] = []
    level = [(entry, "")]
    for _ in range(depth):
        below = []
        for node, prefix in level:
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                name = prefix + child.tag
                if len([c for c in child if isinstance(c.tag, str)]):
                    below.append((child, name + "/"))
                elif name not in found:
                    found.append(name)
        level = below
    return found


def _leaf_text(entry, field: str) -> Optional[str]:
    """The value ``field`` holds inside ``entry``, or None if it holds no plain value."""
    found = entry.find(field)
    if found is None or len([c for c in found if isinstance(c.tag, str)]):
        return None
    return (found.text or "").strip()


def _picks_out(entry, pairs) -> bool:
    """Whether ``entry`` is the only sibling of its kind with every ``(field, value)``."""
    parent = entry.getparent()
    if parent is None or not pairs or not all(value for _key, value in pairs):
        return False
    return not any(
        other is not entry
        and other.tag == entry.tag
        and all(_leaf_text(other, key) == value for key, value in pairs)
        for other in parent
    )


def _usable_key(
    sheet: Sheet, entry, key: str, node, op, own_key: Optional[str], itself: bool = True
) -> bool:
    """Whether ``key`` can find ``entry`` for a change ``op`` makes to ``node``.

    Not once this op has taken the field away, nor if the op changes it: one op's
    changes are written as one block, and a key naming a value the block removes
    or replaces finds nothing once that part has run. Two exceptions: the key
    the path already had, and -- unless ``itself`` is false -- the very field
    this change is about. A change finds its own field by the value it replaces,
    and that replacement is written last (:func:`changes.after_their_entry`);
    two-field keys pass ``itself=False``, since nothing there reorders them.
    """
    child = entry.find(key)
    if child is None:
        return False
    if child is node:
        return itself or key == own_key
    if any(child is up for up in node.iterancestors()):
        return key == own_key
    recorded = sheet.values.get(id(child))
    if recorded is not None and recorded[0] is child and (
        recorded[1].origin, recorded[1].order
    ) == (op.mod, op.order):
        return key == own_key
    return True


def keyed_as_it_stands(sheet: Sheet, path: str, node, op) -> str:
    """``path`` with each list entry found by a key that picks it out when ``op`` starts.

    A shipped node's path is written from the file as shipped, so an entry is
    found by the key it shipped with. By the time ``op`` runs that key may be
    gone -- an earlier op replaced it: the *shared pools* bundle turns
    ``Item[GUID='11404']`` into its own pool and removes that entry later -- or
    no longer unique: *Serp's tooltip fixes* add a buff entry sharing a shipped
    one's InfoTip, then change the shipped entry's icon. Either way the step is
    keyed again: by its old key if that still picks the entry out, else the
    first other field that does, else two fields together.

    Judged against the asset as the op found it (:meth:`Sheet.as_the_op_found`),
    never partway through the op's own edits. Partway, *Creative Mode* emptied a
    cost list and keyed one entry by an Amount whose twin the op had already
    removed, and *Harbour Service Overhaul* keyed an entry by the very Product it
    was adding.
    """
    steps = _STEP.findall(path)
    chain = []
    current = node
    while current is not None and current is not sheet.working:
        chain.append(current)
        current = current.getparent()
    chain.reverse()
    if not steps or len(steps) != len(chain):
        return path
    out = []
    changed = False
    for step, entry in zip(steps, chain, strict=True):
        keyed = _KEYED_STEP.match(step)
        before = sheet.as_the_op_found(entry)
        if (keyed is None and "[" in step) or before is None or before.getparent() is None:
            out.append(step)
            continue
        tag = keyed.group(1) if keyed else step
        twins = [
            other for other in before.getparent()
            if other is not before and other.tag == before.tag
        ]
        if keyed is None and not twins:
            out.append(step)
            continue
        own = keyed.group(2) if keyed else None
        chosen = None
        keys = ([own] if own else []) + [k for k in pathmod.key_candidates(before) if k != own]
        for key in keys:
            value = _leaf_text(before, key)
            if (
                value
                and _usable_key(sheet, entry, key, node, op, own)
                and _picks_out(before, [(key, value)])
            ):
                chosen = "%s[%s=%s]" % (tag, key, pathmod.quote(value))
                break
        if chosen is None:
            usable = []
            for key in _leaf_fields(before):
                value = _leaf_text(before, key)
                if (
                    value
                    and not any(mark in value for mark in "[]'\"")
                    and _usable_key(sheet, entry, key, node, op, None, itself=False)
                ):
                    usable.append((key, value))
            for position, first in enumerate(usable):
                for second in usable[position + 1:]:
                    if _picks_out(before, [first, second]):
                        chosen = "%s[%s=%s and %s=%s]" % (
                            tag, first[0], pathmod.quote(first[1]),
                            second[0], pathmod.quote(second[1]),
                        )
                        break
                if chosen is not None:
                    break
        if chosen is None:
            # Nothing tells it from a twin: an exact copy. Then which of them
            # it is, as the mod itself says -- *General Lifestyle* copies three
            # needs up the list and removes ``Item[Product='535'][2]``, and
            # keyed by the product alone the removal named both and the
            # Studio showed neither gone.
            try:
                alike = before.getparent().xpath("./" + step)
            except etree.XPathError:
                alike = []
            if len(alike) > 1 and any(other is before for other in alike):
                position = next(n for n, other in enumerate(alike) if other is before)
                chosen = "%s[%d]" % (step, position + 1)
        if chosen is None or chosen == step:
            out.append(step)
            continue
        out.append(chosen)
        changed = True
    return "/" + "/".join(out) if changed else path


def reached_by_a_removal(sheet: Sheet, earlier: Change, clones) -> bool:
    """Whether a removal the mod made after ``earlier`` matches one of ``clones``.

    Folded into ``earlier``'s payload, the clones are written before that
    removal, and it takes them too. *New Docklands* moves an entry by removing
    it and inserting it again beside one of its own new entries; folded into
    that earlier insertion, the moved entry was gone in game.
    """
    for removal in sheet.removals:
        if removal.origin != earlier.origin or removal.order <= earlier.order:
            continue
        found = pathmod.resolve(sheet.working, removal.path)
        if any(node is clone for node in found for clone in clones):
            return True
    return False


class Sheet:
    """One asset kept in two versions: as shipped, and as the mod leaves it.

    ModOps apply in file order to a document that is changing underneath them,
    which is why this exists.  A condition asks about the asset *at that point*
    -- the bug-fix mod pulls entries out of a list and re-inserts them
    elsewhere, each guarded by "only if it is not there" -- so reading every op
    against the pristine file would decide half of them were already done.

    The shipped tree is what changes are addressed against, since that is what
    a ModOp patches.  ``pairs`` remembers which shipped node each working node
    mirrors; a working node with no shipped counterpart was put there by this
    import, and edits to it belong in the payload that introduced it rather
    than in a ModOp against a node the game does not have.
    """

    def __init__(self, base, owned: bool = False):
        self.base = base
        self.working = copy.deepcopy(base)
        # Where an asset a mod made stands in the file, as a key that sorts in
        # file order (see Translator._place_beside); None for a shipped one.
        self.place: Optional[tuple] = None
        # Which of the file's top-level groups an asset a mod made is in (see
        # Translator._group_of); None for a shipped one, or where not known.
        self.group: Any = None
        self.pairs: Dict[int, tuple] = {}
        self.spans: List[_Span] = []
        # The value change one mod has already made to a node, so a second edit
        # of the same field updates it instead of colliding with it.
        self.values: Dict[int, tuple] = {}
        # The removals written down so far. An insertion is not folded into an
        # earlier run of the same mod if one of these, made in between, would
        # match it -- see reached_by_a_removal.
        self.removals: List[Change] = []
        # The lists the op being translated edits, as it found them: copied
        # before that op's first edit to each, so keys are chosen from them and
        # never from the op's own work half done. See starting().
        self._found_by: Optional[tuple] = None
        self._found: Dict[int, tuple] = {}
        # Keyed by id(), so each key holds the nodes it names: an lxml element
        # nobody holds is freed, and reached again it is a new proxy -- under a
        # new id, or under one another node had. A parent this import added is
        # held by nothing else, and its list was copied again mid-op or not, as
        # memory fell: the census read one op two ways from run to run.
        self._copied: Dict[tuple, tuple] = {}
        self._held: list = []
        if owned:
            return
        shipped = [node for node in base.iter() if isinstance(node.tag, str)]
        mirror = [node for node in self.working.iter() if isinstance(node.tag, str)]
        for working, original in zip(mirror, shipped):
            self.pairs[id(working)] = (working, original)

    def starting(self, op, node, inserting=()) -> None:
        """Keep what a key for an edit to ``node`` could be chosen from, as ``op`` found it.

        Only the lists on the way down to ``node``: at each level, the siblings
        sharing that step's name, copied the first time this op touches them --
        before its edit, so what the op removes is still there and what it adds
        is not. And for an insertion, what ``node`` already holds of the kind
        being inserted. A level with no twins needs no key and is not copied,
        unless the file shipped with twins there, when the entry alone is.

        A copy of the whole asset per op did the same job, and made the census
        run 1.6 times as long.
        """
        key = (op.mod, op.order, id(op))
        if self._found_by != key:
            self._found_by = key
            self._found = {}
            self._copied = {}
            self._held = []
        levels = []
        current = node
        while current is not None and current is not self.working:
            parent = current.getparent()
            if parent is None:
                break
            levels.append((parent, current.tag, current))
            current = parent
        levels.extend((node, tag, None) for tag in inserting)
        for parent, tag, member in levels:
            if (id(parent), tag) in self._copied:
                continue
            same = [child for child in parent if child.tag == tag]
            if member is not None and len(same) < 2:
                if (id(parent), tag, id(member)) in self._copied:
                    continue
                shipped = self.shipped(member)
                above = shipped.getparent() if shipped is not None else None
                if above is None or len([c for c in above if c.tag == tag]) < 2:
                    self._copied[(id(parent), tag)] = (parent,)
                    continue
                self._copied[(id(parent), tag, id(member))] = (parent, member)
                same = [member]
            else:
                self._copied[(id(parent), tag)] = (parent,)
            if not same:
                continue
            holder = etree.Element("held")
            for child in same:
                twin = copy.deepcopy(child)
                holder.append(twin)
                self._found[id(child)] = (child, twin)
            self._held.append(holder)

    def as_the_op_found(self, node):
        """``node`` as it was when the current op started, or None if the op added it."""
        found = self._found.get(id(node))
        return found[1] if found is not None and found[0] is node else None

    def shipped(self, node):
        """The node in the shipped asset this one mirrors, or None."""
        found = self.pairs.get(id(node))
        return found[1] if found else None

    def owner(self, node, origin: Optional[str] = None) -> Optional[_Span]:
        """The span this node belongs to, directly or as a descendant.

        ``origin`` restricts it to one mod's spans.  Folding an edit into the
        payload that introduced the node is only right within a mod: across
        mods the node does exist by the time the second one loads, so its edit
        is a ModOp of its own -- and quietly writing it into the first mod's
        payload would move it into somebody else's file.
        """
        while node is not None:
            for span in self.spans:
                if span.holds(node) and (
                    origin is None or span.change.origin == origin
                ):
                    return span
            node = node.getparent()
        return None

    def value_change(self, node, origin: str):
        found = self.values.get(id(node))
        if found is None:
            return None
        held, change = found
        return change if held is node and change.origin == origin else None

    def remember_value(self, node, change: Change) -> None:
        self.values[id(node)] = (node, change)

    def claim(self, change: Change, nodes) -> _Span:
        span = _Span(change, nodes)
        self.spans.append(span)
        return span

    def release(self, span: _Span) -> None:
        self.spans = [other for other in self.spans if other is not span]

    def settle(self, origin=None) -> None:
        """Freeze a mod's payloads now that none of its ops can touch them.

        Per mod rather than at the very end: within a mod, an edit to something
        it added belongs in the payload that added it, and that is why payloads
        are held live.  Across mods it does not -- the node exists by the time
        the next mod loads, so its edits are its own ModOps.
        """
        for span in self.spans:
            if span.settled or (origin is not None and span.change.origin != origin):
                continue
            span.change.payload = span.payload()
            span.settled = True


def keyed_path(tree, path: str) -> str:
    """``path`` rewritten with every list entry keyed, if it names one node in ``tree``."""
    try:
        found = [
            node for node in tree.xpath("." + path)
            if isinstance(getattr(node, "tag", None), str)
        ]
    except etree.XPathEvalError:
        return ""
    if len(found) != 1:
        return ""
    keyed = pathmod.path_for(tree, found[0], keyed=True)
    return keyed.path if keyed.path and not keyed.positional else ""


def replay(root, change: Change, game: str = "") -> None:
    """Apply one change to a tree the way ``game`` applies its ModOp."""
    try:
        matches = root.xpath("." + change.path) if change.path else [root]
    except etree.XPathEvalError:
        return
    matches = [n for n in matches if isinstance(getattr(n, "tag", None), str)]
    payload = effective.payload_elements(change)
    for node in matches:
        if change.op == OP_REPLACE:
            node.text = change.new_value
            continue
        if change.op == OP_REMOVE:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
            continue
        applying.apply(change.op, node, payload, game)


def strip_inert(root, inert) -> None:
    """Drop the entries the game reads as absent, so two trees can be compared
    by what they mean rather than by what they say."""
    if inert is None:
        return
    for node in [n for n in root.iter() if isinstance(n.tag, str)]:
        parent = node.getparent()
        if parent is not None and inert(node):
            parent.remove(node)


def canonical(node) -> str:
    clone = copy.deepcopy(node)
    for element in clone.iter():
        if not isinstance(element.tag, str):
            continue
        element.text = (element.text or "").strip() or None
        element.tail = None
    return etree.tostring(clone).decode()


def holds_a_mod_item(op) -> bool:
    """Whether a ``<ModItem>`` is anywhere in what this op carries."""
    return any(
        node.tag == MOD_ITEM or node.find(".//" + MOD_ITEM) is not None
        for node in op.payload
    )


def merged_items(target, payload, game: str):
    """``target`` as a merge of ``<ModItem>`` entries leaves it.

    Anno 117's answer to a merge being positional (:func:`applying.merge`): a
    ``<ModItem>`` names the field to find its entry by, so a mod can patch the
    entry it means rather than the one that happens to sit in that place.
    Found, the entry is merged into by name. Not found, a new ``<Item>`` goes
    at the end, or beside the entry ``Append=``/``Prepend=`` names. Measured
    with xmltest; see ``test_modio_loader``. The Anno 1800 loader has no idea
    what one is, and the reader does not come this way for it.

    What the loader warns of is not read: it is a mod to fix rather than to
    read, and the warning is all it leaves to say what happened.
    """
    return _merged_items(target, payload, game)[0]


def diff_mod_items(target, payload, game: str, edits: List[Edit]) -> None:
    """The edits a merge of ``<ModItem>`` entries comes to, as :func:`diff`
    reads them.

    The list's entries are matched by which entry each one is, not by key: a
    ``<ModItem>`` merges into the entry it finds and adds the ones it does not,
    and neither moves or drops one, so there is no reordering for a key to see
    through. A key is what failed: the Fishing Hut's costs hold Ingredient
    2176 twice once a mod has added a second one, and a merge into 2174 read
    as the whole list replaced -- or, where the Amounts happened to differ,
    as 2174 taken out and put back, paired by the very field it changed.

    Only where every ``<ModItem>`` finds one entry at most, before and after:
    one whose key is met twice lands on the first of them, which no path
    written for the edit says, so the op is kept as written.
    """
    merged, pairs = _merged_items(target, payload, game)
    if not all(_hits_one(root, payload) for root in (target, merged)):
        stuck = Unrepresentable(
            tr_noop("a <ModItem> whose key is met twice in the list, so it merges into "
                    "the first of them, which no path can say", context=STORED)
        )
        stuck.at = target
        raise stuck
    diff(target, merged, edits, removals=True, paired=pairs)


def _hits_one(root, payload) -> bool:
    """Whether no ``<ModItem>`` of ``payload`` finds more than one entry in
    ``root``."""
    for item in payload:
        query = applying.mod_item_query(item) if item.tag == MOD_ITEM else None
        if query is None:
            return False
        try:
            hits = [hit for hit in root.xpath(query) if isinstance(getattr(hit, "tag", None), str)]
        except etree.XPathError:
            return False
        if len(hits) > 1:
            return False
    return True


def _merged_items(target, payload, game: str):
    """``(merged, pairs)``: :func:`merged_items`, and each of ``target``'s
    children with the one it became in ``merged``."""
    merged = clean(target)
    pairs = list(zip(
        [child for child in target if isinstance(child.tag, str)],
        [child for child in merged if isinstance(child.tag, str)],
        strict=True,
    ))
    before = _attributed(merged)
    warned = applying.apply(OP_MERGE, merged, payload, game)
    for warning in warned:
        if warning == applying.NO_MERGE_KEY or warning.startswith("ModItem \""):
            raise Unrepresentable(
                tr_noop("a <ModItem> that says nothing the Studio can match an entry by",
                        context=STORED)
            )
        side = next((side for side in ("Append", "Prepend")
                     if warning.startswith("ModItem %s=" % side)), "")
        if side:
            # The loader says so in its log and leaves a <ModItem> sitting in
            # the game's data.
            raise Unrepresentable(
                tr_noop("a <ModItem> whose %s= names an entry that is not there",
                        context=STORED) % side
            )
    if _attributed(merged) != before:
        # The loader leaves the Append= attribute on the entry it merged into
        # -- a stray attribute in the game's own data. Reproducing that is not
        # something a narrow op can do.
        raise Unrepresentable(
            tr_noop("a <ModItem> that both finds its entry and says where to put "
                    "it, which the loader leaves an attribute behind for",
                    context=STORED)
        )
    return merged, pairs


def _attributed(root) -> list:
    """The attributes anywhere under ``root``, each with its element's tag."""
    return sorted(
        (node.tag, sorted(node.attrib.items()))
        for node in root.iter() if isinstance(node.tag, str) and node.attrib
    )


def merges_by_position(want, base, game: str = "") -> bool:
    """Whether merging ``want`` into ``base`` meets same-named siblings
    anywhere, or for Anno 117 a name it matches only in another case.

    Where every child has a name of its own, merging by name and merging by
    position are the same thing, and the field-by-field reading is right.
    Where a name is matched in any case, the field-by-field reading, which
    goes by the name as written, is not: the merge is carried out instead.
    """
    named = [child.tag for child in want if isinstance(child.tag, str)]
    for tag in set(named):
        if named.count(tag) > 1 or len(base.findall(tag)) > 1:
            return True
        if game != games.ANNO_1800 and base.find(tag) is None and any(
            applying.named(child.tag, tag) for child in base
        ):
            return True
    for child in want:
        if not isinstance(child.tag, str) or is_leaf(child):
            continue
        existing = base.find(child.tag)
        if existing is not None and merges_by_position(child, existing, game):
            return True
    return False


def clean(element):
    """A detached copy with the author's comments dropped.

    Without the element's tail: that is the text *after* it, which belongs to
    its parent. *Fast Move-In* closes a field with ``</MoveRandomMs>>``, so the
    tail is a stray ``>``, and serialising it along with the element handed the
    parser one element and then text past the end -- a crash out of the middle
    of reading the whole folder, for a character the game's loader ignores.
    """
    clone = etree.fromstring(etree.tostring(element, with_tail=False))
    _drop_comments(clone)
    return clone


def _drop_comments(root) -> None:
    """Comments out, and with each of them the blank text beside it.

    The loader's parser makes a node of a run of text only when something in
    it is not whitespace, so a comment sitting alone on a line leaves nothing
    behind.  Dropping the comment and keeping the line it sat on folded that
    indentation into the value before it: *Add Collection* binds a key to a
    hundred console commands with a comment between each group, and every one
    of those comments came back as a blank line inside the command.  Measured
    against the loader for both games.
    """
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        for child in list(parent):
            if not isinstance(child, etree._Comment):
                continue
            # What is before the comment and what is after it are two runs to
            # the loader, and one to lxml the moment the comment is gone.
            index = parent.index(child)
            before = parent if index == 0 else parent[index - 1]
            field = "text" if index == 0 else "tail"
            run = getattr(before, field) or ""
            tail = child.tail or ""
            parent.remove(child)
            joined = (run if run.strip() else "") + (tail if tail.strip() else "")
            setattr(before, field, joined or None)


def serialise(element) -> str:
    for node in element.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    return etree.tostring(element, pretty_print=True).decode().strip()
