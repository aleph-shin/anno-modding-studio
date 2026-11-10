"""The asset as it will be, not as it shipped.

Once a mod can add and remove nodes, showing the pristine base asset is no longer
enough: you cannot add a field underneath a field you just added, because as far
as the base game is concerned it does not exist.  So the Studio applies its own
pending ModOps to a copy of the asset and works against *that*.

Every node in the result carries an :class:`Origin` saying where it came from,
and that provenance decides what a further edit means:

``base``
    Shipped with the game.  Editing it emits a new ModOp, as before.

``added`` / ``new-asset``
    This mod put it there.  Editing it must **not** emit a second ModOp against
    the base game -- the node does not exist there.  Instead the edit is folded
    back into the payload of the change that introduced it, so adding a field
    under a field you added stays one ``add`` with a richer payload.

``set``
    Another mod made the field and an ensure of this one set it.  The value is
    the ensure's, so an edit goes into its payload as it does for an addition;
    the field is not, so it is not called one.

``removed``
    Struck out rather than deleted, so the removal can be undone.

Payload positions are recorded as child-index paths ("0.2" is the payload root's
first child's third child).  Indices rather than tags because a payload may hold
several entries with the same name.

Origins are keyed by the node itself, held alongside its origin.  Not by tree
path: an insertion beside a node shifts every following sibling's path, which
would silently re-point origins recorded earlier at their new neighbours -- a
list a mod reorders would come back with the wrong entries struck through.  And
not by ``id(node)`` alone: lxml hands out a fresh proxy once the old one is
collected, so keeping the element is what makes its identity stable.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from lxml import etree

from . import applying, modop, paths
from .changes import (
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
    Change,
    emit_order,
)
from .i18n import tr, trn

PROV_BASE = "base"
PROV_EDITED = "edited"
PROV_ADDED = "added"
# A field another mod made, set by an ensure of this one: there because of
# that mod, holding what this one says.
PROV_SET = "set"
PROV_REMOVED = "removed"
PROV_NEW_ASSET = "new-asset"

# Stands in for the document element a run of sibling nodes does not have.
WRAPPER = "_"

# Provenances whose nodes live inside a change payload rather than the base game.
OWNED_BY_MOD = (PROV_ADDED, PROV_SET, PROV_NEW_ASSET)


@dataclass(frozen=True)
class Origin:
    """Where one node in the effective tree came from."""

    provenance: str
    change_key: Optional[tuple] = None
    payload_path: str = ""

    @property
    def is_mod_owned(self) -> bool:
        """True when edits belong in a payload instead of a new ModOp."""
        return self.provenance in OWNED_BY_MOD

    @property
    def is_removed(self) -> bool:
        return self.provenance == PROV_REMOVED


# ---------------------------------------------------------------- payload paths
def payload_child_path(parent_path: str, index: int) -> str:
    return "%s.%d" % (parent_path, index) if parent_path else str(index)


def payload_node(root, path: str):
    """Resolve a child-index path inside a payload element."""
    node = root
    if not path:
        return node
    for step in path.split("."):
        try:
            node = node[int(step)]
        except (ValueError, IndexError):
            return None
    return node


def payload_parent(root, path: str):
    """``(parent, index)`` for a payload path, or ``(None, -1)`` for the root."""
    if not path:
        return None, -1
    steps = path.split(".")
    parent = payload_node(root, ".".join(steps[:-1]))
    try:
        return parent, int(steps[-1])
    except ValueError:
        return None, -1


def _normalise(root) -> None:
    """Drop whitespace-only text so pretty printing re-indents from scratch."""
    for node in root.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None


def _serialise(root) -> str:
    _normalise(root)
    if root.tag == WRAPPER:
        # The wrapper is ours, not the mod's: a run of siblings is written out
        # as the siblings themselves, which is what goes inside a ModOp.
        return "\n".join(
            etree.tostring(child, pretty_print=True).decode().strip()
            for child in root
            if isinstance(child.tag, str)
        )
    return etree.tostring(root, pretty_print=True).decode().strip()


def parse_payload(change: Change):
    return parse_payload_text(change.payload)


def parse_payload_text(text: str):
    """A payload as one element, wrapping a run of siblings if that is what it is.

    Most payloads are a single node.  One insertion can place several, though --
    a list entry moved as a group, or an imported ``Append`` carrying four
    items -- and a run of siblings is not a document, so it gets a wrapper the
    rest of this module knows to look through.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return etree.fromstring(text.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        pass
    try:
        return etree.fromstring(("<%s>%s</%s>" % (WRAPPER, text, WRAPPER)).encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return None


def parse_block(text: str):
    """``text`` as the one element a paste puts in, its indentation dropped;
    ``None`` where it is not XML, or is a run of several."""
    root = parse_payload_text(text)
    if root is None or root.tag == WRAPPER:
        return None
    _normalise(root)
    return root


def payload_elements(change: Change):
    """The top-level nodes of a payload: one, or the whole run."""
    root = parse_payload(change)
    if root is None:
        return []
    if root.tag == WRAPPER:
        return [child for child in root if isinstance(child.tag, str)]
    return [root]


def payload_fields(change: Change):
    """``(tag, value)`` for each leaf in a payload, in document order."""
    root = parse_payload(change)
    if root is None:
        return []
    out = []
    for node in root.iter():
        if not isinstance(node.tag, str) or len(node):
            continue
        out.append((node.tag, (node.text or "").strip()))
    return out


def change_summary(change: Change, name=None) -> str:
    """One short line for the changes list.

    Additions name the field *and* its value.  The raw payload is no good here:
    ``<SecondPartyRelevant>0</SecondPartyRelevant>`` spends most of a narrow
    column repeating the tag, which XML requires and a reader does not.

    ``name``, when given, is asked what a GUID in the payload is called.  A
    multi-field addition is otherwise described only by its container -- *"+
    Item (2 fields)"* says an item was added to a list and nothing whatever
    about which item -- and in practice the thing that identifies it is the one
    reference inside it, which reads as *Marble* rather than as ``1010196``.
    """
    if change.creates_asset:
        return creation_summary(change, name)
    if change.op not in (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV, OP_MERGE):
        return change.describe_change()
    fields = payload_fields(change)
    if len(fields) == 1 and fields[0][1]:
        return "+ %s = %s" % fields[0]
    entries = _list_entries(change)
    if change.tag and len(entries) > 1:
        # A whole list, as a merge no narrow op can name an entry of is
        # shown: named by its first entry's good, the row read as that one
        # entry added -- "+ Costs Denarii (10 fields)" for a list of seven
        # (H2-30). How many entries it holds is what there is to say.
        # Translators: a whole list added in the changes list: "+ Costs
        # (7 entries)".
        return trn("+ %(tag)s (%(n)s entry)", "+ %(tag)s (%(n)s entries)",
                   len(entries), tag=change.tag)
    if len(fields) > 1:
        told = _identifying(fields, name)
        if change.tag and told:
            # Translators: an addition in the changes list: the element
            # added, what it is about, and how many fields it holds -- "+ Item
            # Marble (2 fields)".
            return trn("+ %(tag)s %(what)s (%(n)s field)",
                       "+ %(tag)s %(what)s (%(n)s fields)",
                       len(fields), tag=change.tag, what=told)
        if change.tag:
            return trn("+ %(tag)s (%(n)s field)", "+ %(tag)s (%(n)s fields)",
                       len(fields), tag=change.tag)
        if told:
            return trn("+ node %(what)s (%(n)s field)",
                       "+ node %(what)s (%(n)s fields)", len(fields), what=told)
        return trn("+ node (%(n)s field)", "+ node (%(n)s fields)", len(fields))
    return change.describe_change()


def _list_entries(change: Change) -> list:
    """The entries of a payload that is a list -- its children, two or more,
    every one of them the same element -- or [] for one that is not."""
    root = parse_payload(change)
    if root is None or root.tag == WRAPPER:
        return []
    children = [child for child in root if isinstance(child.tag, str)]
    if len(children) > 1 and len({child.tag for child in children}) == 1:
        return children
    return []


def _identifying(fields, name) -> str:
    """What a multi-field payload is *about*, as far as its GUIDs can say.

    The first value the caller recognises as an asset, which for the payloads
    that reach this -- a cost entry, an item in a pool, a production input --
    is the entry's subject: the good, the item, the building.  Everything else
    in them is amounts and flags, which say nothing until you know what of.
    """
    if name is None:
        return ""
    for _tag, value in fields:
        if value.isdigit():
            found = name(value)
            if found:
                return found
    return ""


def creation_summary(change: Change, name=None) -> str:
    """What a new-asset op is making, for the changes list.

    "+ new asset" was true of every one of them.  What distinguishes them is
    what kind of asset it is -- a Product, a Building, a ResidenceUpgrade -- or,
    when it names another asset to inherit from instead of a template, which
    asset it follows.

    In the New asset dialog's own words.  The one that inherits was called a
    copy here, while the dialog's "Copy its values once" makes the other
    kind: every value written out under the original's template, with
    nothing left to say where they came from.  That one reads as a new asset
    of its template, which is all its op says.
    """
    root = parse_payload(change)
    if root is None:
        return tr("+ new asset")
    template = (root.findtext("Template") or "").strip()
    if template:
        # Translators: a new asset in the changes list; %s is its template,
        # such as Product or ResidenceUpgrade.
        return tr("+ new %s", template)
    base = (root.findtext("BaseAssetGUID") or "").strip()
    if base:
        told = name(base) if name is not None else ""
        # Translators: a new asset in the changes list that inherits from
        # another, as "Follow the original" in the New asset dialog makes
        # one; %s is the asset it follows.
        return tr("+ follows %s", told or base)
    return tr("+ new asset")


def describe_payload(change: Change) -> str:
    """A readable rendering of an addition, for a tooltip."""
    root = parse_payload(change)
    if root is None:
        return change.payload
    if root.tag == WRAPPER:
        return "\n".join(describe_element(child) for child in root)
    return describe_element(root)


def describe_element(root) -> str:
    lines = []

    def walk(node, depth):
        if not isinstance(node.tag, str):
            return
        pad = "    " * depth
        if len(node):
            lines.append("%s%s" % (pad, node.tag))
            for child in node:
                walk(child, depth + 1)
        else:
            text = (node.text or "").strip()
            lines.append("%s%s%s" % (pad, node.tag, (" = " + text) if text else ""))

    walk(root, 0)
    return "\n".join(lines)


def new_asset_guid(change: Change) -> str:
    """The GUID of the asset a change creates, or ""."""
    if not change.creates_asset:
        return ""
    return _guid_in(change.payload)


def payload_guid(payload: str) -> str:
    """The GUID of the asset ``payload`` is, or "" -- for a payload held apart
    from its change, as a fingerprint holds one."""
    return _guid_in(payload or "")


@lru_cache(maxsize=1 << 16)
def _guid_in(payload: str) -> str:
    """The GUID of the asset ``payload`` is, kept for each text asked of.

    The grouping of the change list asks it of every change that creates an
    asset, at every edit: with 417 mods loaded, 22,680 payloads read as XML
    again for one value typed.  The answer is the text's alone.
    """
    root = parse_payload_text(payload)
    if root is None:
        return ""
    return (root.findtext("Values/Standard/GUID") or "").strip()


def new_asset_name(change: Change) -> str:
    root = parse_payload(change)
    if root is None:
        return ""
    return (root.findtext("Values/Standard/Name") or "").strip()


def new_asset_text(change: Change) -> tuple:
    """``(embedded, key)`` for a created asset's name: the English text baked
    into it, and the LineID (1800) or OasisId (117) its name is looked up by
    -- the same two things the index reads off a shipped asset."""
    root = parse_payload(change)
    if root is None:
        return "", ""
    embedded = (root.findtext("Values/Text/LocaText/English/Text") or "").strip()
    key = (
        root.findtext("Values/Text/LineID") or root.findtext("Values/Text/OasisId") or ""
    ).strip()
    return embedded, key


def new_asset_own_template(change: Change) -> str:
    """The template a new asset names, or "" for one that inherits instead."""
    root = parse_payload(change)
    if root is None:
        return ""
    return (root.findtext("Template") or "").strip()


def new_asset_template(change: Change) -> str:
    """What a new asset's Template column says: its template, or the asset
    it inherits from when it names none."""
    root = parse_payload(change)
    if root is None:
        return ""
    template = (root.findtext("Template") or "").strip()
    if template:
        return template
    base = (root.findtext("BaseAssetGUID") or "").strip()
    # Translators: shown in the Template column for a new asset that copies
    # another; %s is the GUID of the asset it copies.
    return tr("inherits %s", base) if base else ""


# ---------------------------------------------------------------- the effective tree
class EffectiveAsset:
    """A base asset with this mod's pending changes applied."""

    def __init__(self, element, origins: Dict[int, tuple], guid: str, is_new: bool,
                 applied: Optional[Dict[tuple, int]] = None,
                 targets: Optional[Dict[tuple, Any]] = None,
                 run: Optional[_Running] = None):
        self.element = element
        self._origins = origins
        self.guid = guid
        self.is_new = is_new
        # Each change, by key, numbered in the order it was applied: what had
        # run before it is what the game had by then.
        self.applied: Dict[tuple, int] = applied if applied is not None else {}
        # The node each change's path found, where it found one.
        self.targets: Dict[tuple, Any] = targets if targets is not None else {}
        run = run if run is not None else _Running()
        # What each change's path found as it ran: how many nodes, and the
        # text of the one where it found one -- what the game hands it.
        self.seen: Dict[tuple, Tuple[int, Optional[str]]] = run.seen
        # The text a node had before a mod's first change to it, by node and
        # mod: what that mod was handed (:meth:`handed_form`).
        self._handed: Dict[Tuple[int, str], str] = run.handed

    def origin(self, node) -> Origin:
        if node is None:
            return Origin(PROV_BASE)
        found = self._origins.get(id(node))
        return found[1] if found else Origin(PROV_BASE)

    def __bool__(self) -> bool:
        return self.element is not None

    def gone_before(self, key=None) -> Callable[[object], bool]:
        """Whether a node is gone from the game's asset by the time the change
        with ``key`` runs: struck out by a change applied before it.  With no
        key, a change made now, which runs after every one of them.

        A removed node stays in this tree and is gone from the game's, so a
        position the game counts is counted without it: with the fifth of six
        entries removed, the sixth is ``Item[5]`` to an op that runs after.
        """
        limit = self.applied.get(key, len(self.applied)) if key is not None else len(self.applied)

        def gone(node) -> bool:
            origin = self.origin(node)
            if not origin.is_removed:
                return False
            return origin.change_key is None or self.applied.get(origin.change_key, -1) < limit

        return gone

    def handed_form(self, node, origin: str) -> str:
        """``node`` and everything in it as it was handed to ``origin``'s
        ops, as one line: what an entry a path finds by a count is known
        again by (:attr:`.changes.Change.entry`).

        Not as the tree holds it now: the mod's own edits to the entry are
        taken back out, so typing a new value into it, or taking a field out,
        is no reason to think the path finds another entry.  What the mod
        added is left out, what it set is said without its value, and what
        a mod before it removed is not there.
        """
        def form(one) -> str:
            known = self.origin(one)
            by = known.change_key[0] if known.change_key else None
            if by == origin and known.provenance == PROV_ADDED:
                return ""
            if known.is_removed and by != origin:
                return ""
            text = self._handed.get((id(one), origin), one.text or "")
            if by == origin and known.provenance == PROV_SET:
                text = ""
            said = "".join("%s=%s;" % pair for pair in sorted(one.attrib.items()))
            inner = "".join(form(child) for child in one if isinstance(child.tag, str))
            return "<%s%s>%s%s</%s>" % (one.tag, " " + said if said else "", text.strip(), inner, one.tag)

        return form(node)

    def gone_for(self, node) -> Callable[[object], bool]:
        """:meth:`gone_before` for a path to ``node``: the change that struck
        it out, if one did, else one made now."""
        origin = self.origin(node)
        return self.gone_before(origin.change_key if origin.is_removed else None)

    def as_the_game_reads_it(self):
        """A plain copy with the struck-out nodes actually gone.

        The tree itself keeps a removed node, struck through, so the removal can
        be undone and so the editor can show what used to be there.  Anything
        asking what the *game* will be handed has to see it deleted -- otherwise
        a path that should match one node matches two.
        """
        if self.element is None:
            return None
        doomed = [
            node
            for node in self.element.iter()
            if isinstance(node.tag, str) and self.origin(node).is_removed
        ]
        for node in doomed:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        return self.element


def _remember(origins, element, origin: Origin) -> None:
    """File an origin under the node itself.

    Not under its position: a sibling insertion shifts every following node's
    path, so a positional key silently re-points origins recorded earlier at
    their new neighbours -- the list a mod reorders would come back with the
    wrong entries struck through.  Not under ``id(element)`` alone either: lxml
    hands out a fresh proxy once the old one is collected, so the element is
    kept alongside its origin to hold that proxy alive.
    """
    origins[id(element)] = (element, origin)


def _known(origins, element) -> Optional[Origin]:
    found = origins.get(id(element))
    return found[1] if found else None


def _mark_subtree(element, origins, provenance, change_key, payload_path) -> None:
    _remember(origins, element, Origin(provenance, change_key, payload_path))
    for index, child in enumerate(element):
        if isinstance(child.tag, str):
            _mark_subtree(
                child,
                origins,
                provenance,
                change_key,
                payload_child_path(payload_path, index),
            )


def build(base_element, changes: List[Change], guid: str, is_new: bool = False, game: str = ""):
    """Apply ``changes`` to ``base_element`` and record where everything came from.

    ``changes`` should be the changes for this one asset, in the order the
    loadout holds them.  They are applied as the mods' files run them: mod by
    mod, op by op, and one op's changes in the order they are written
    (:func:`changes.emit_order`), and merged by ``game``'s loader's rules
    (:func:`applying.merge`).
    """
    if base_element is None:
        return EffectiveAsset(None, {}, guid, is_new)

    element = copy.deepcopy(base_element)
    origins: Dict[int, tuple] = {}
    _mark_subtree(element, origins, PROV_NEW_ASSET if is_new else PROV_BASE, None, "")

    if is_new:
        # The whole asset is one payload; the creating change owns every node.
        creator = next(
            (c for c in changes if c.creates_asset), None
        )
        if creator is not None:
            _mark_subtree(element, origins, PROV_NEW_ASSET, creator.key, "")

    # Mod by mod, in the load order the changes arrive in; within a mod op by op,
    # in the order its file runs them; and only within one op by kind. Sorted by
    # kind alone, a later mod's removal ran before an earlier mod's addition and
    # missed it -- *New Docklands* takes out an entry the gate mod appends, and
    # the Studio went on showing it -- and a later op's edit to an entry, found
    # by the key an earlier op gave it, ran before that key was there.
    layer: Dict[str, int] = {}
    blocks: Dict[tuple, List[Change]] = {}
    for change in changes:
        at = layer.setdefault(change.origin, len(layer))
        blocks.setdefault((at, change.order), []).append(change)
    applied: Dict[tuple, int] = {}
    targets: Dict[tuple, Any] = {}
    run = _Running()
    for where in sorted(blocks):
        # Within the op as the writer writes them, which is what the game is
        # handed: one order, asked for here and not kept as a second.
        for change in emit_order(blocks[where]):
            if change.creates_asset:
                continue  # the asset itself, already the tree we are building
            applied[change.key] = len(applied)
            target = _apply(element, origins, change, game, run)
            if target is not None:
                targets[change.key] = target

    return EffectiveAsset(element, origins, guid, is_new, applied, targets, run)


@dataclass
class _Running:
    """What :func:`build` keeps as it goes, change by change."""

    # What the changes so far removed, each the outermost of what one took out.
    struck: List = field(default_factory=list)
    # Change key -> (how many nodes its path found, the text of the one).
    seen: Dict[tuple, Tuple[int, Optional[str]]] = field(default_factory=dict)
    # (node id, mod) -> its text before that mod's first change to it.
    handed: Dict[Tuple[int, str], str] = field(default_factory=dict)


@contextmanager
def without(nodes) -> Iterator[None]:
    """The tree with ``nodes`` taken out of it for a while, and put back
    where each was, text after it and all, however the block is left.

    What a removal struck out is still in the tree and is not in the game's,
    and a path is asked of the game's: ``Item[5]`` there is the fifth entry
    that is left, which XPath can only count with the others out of the way.
    """
    taken = []
    for node in nodes:
        parent = node.getparent()
        if parent is None:
            continue
        taken.append((parent, parent.index(node), node))
        parent.remove(node)
    try:
        yield
    finally:
        # Last out, first back: each goes back to the place it was taken
        # from, which is where it is again once the ones after it are back.
        for parent, at, node in reversed(taken):
            parent.insert(at, node)


def outermost(element, gone: Callable[[object], bool]) -> List:
    """The nodes under ``element`` that ``gone`` says are gone, but for the
    ones that go with a node above them that is."""
    found = []
    stack = [child for child in element if isinstance(child.tag, str)]
    while stack:
        node = stack.pop()
        if gone(node):
            found.append(node)
            continue
        stack.extend(child for child in node if isinstance(child.tag, str))
    return found


def resolve_as_the_game_has_it(element, path: str, gone: Callable[[object], bool]) -> List:
    """What ``path`` finds once the nodes ``gone`` says are gone are, which
    is what the game finds: a position counts only what is left."""
    with without(outermost(element, gone)):
        return [node for node in _resolve(element, path) if not gone(node)]


_POSITION = paths.COUNTED_STEP


def counts(path: str) -> bool:
    """Whether ``path`` picks some entry on its way by a count."""
    return any(
        _POSITION.match(step) for step in modop.split_outside((path or "").strip("/"), "/")
    )


def _steps(path: str) -> Optional[List[str]]:
    """``path``'s steps from the asset down, or ``None`` for one that is not
    a plain walk down from it.  A slash inside a predicate is no step."""
    if not path.startswith("/") or path.startswith("//"):
        return None
    steps = modop.split_outside(path[1:], "/")
    return steps if all(steps) else None


def repointed(eff: EffectiveAsset, change: Change, gone: Callable[[object], bool]):
    """The path ``change`` has to take to go on finding what it finds now,
    once what is gone by the time it runs is what ``gone`` says -- or
    ``None`` where its own path still finds it, or no path can be had.

    A removal before it, put in or taken back, moves every entry after it in
    the game's list by one, and a path that names an entry by its position
    names another one then.  Only the positions are counted again: a step
    that names an entry by what is in it is left as it is, since what is in
    it here is what the changes leave, and the change may be the one that
    sets it -- ``Item[B='9']/B`` for the edit that makes ``B`` 9 finds
    nothing in the game, which runs it on the 2 that was there.
    """
    target = eff.targets.get(change.key)
    if eff.element is None or target is None or not change.path:
        return None
    if gone(target) or any(gone(above) for above in target.iterancestors()):
        return None  # what it is aimed at is gone itself then
    found = resolve_as_the_game_has_it(eff.element, change.path, gone)
    if len(found) == 1 and found[0] is target:
        return None
    walked = _walk(eff, change.path, target)
    if walked is None:
        return None
    steps, chain = walked
    positional = False
    for at, (step, node) in enumerate(zip(steps, chain, strict=True)):
        matched = _POSITION.match(step)
        if matched is None or matched.group(1) != node.tag:
            continue
        steps[at] = recounted(node, matched.group(2) or "", gone)
        positional = True
    path = "/" + "/".join(steps)
    found = resolve_as_the_game_has_it(eff.element, path, gone)
    if path == change.path or len(found) != 1 or found[0] is not target:
        return None
    return paths.Generated(path, positional, paths.POSITIONAL_WARNING if positional else "")


def _walk(eff: EffectiveAsset, path: str, target) -> Optional[Tuple[List[str], List]]:
    """``path``'s steps beside the node each step comes to on the way down
    to ``target``, or ``None`` for a path that is not a plain walk down to it."""
    steps = _steps(path)
    chain = [target, *target.iterancestors()]
    if steps is None or eff.element not in chain:
        return None
    chain = list(reversed(chain[: chain.index(eff.element)]))
    if len(chain) != len(steps):
        return None
    return steps, chain


def counted_entry(eff: EffectiveAsset, path: str, target) -> Optional[Tuple[int, Any, List[str]]]:
    """Where ``path`` finds a list entry by a count on its way to ``target``:
    ``(step, entry, steps)`` for the last such step, or ``None``."""
    walked = _walk(eff, path, target)
    if walked is None:
        return None
    steps, chain = walked
    for at in reversed(range(len(steps))):
        matched = _POSITION.match(steps[at])
        if matched is not None and matched.group(1) == chain[at].tag:
            return at, chain[at], steps
    return None


@dataclass(frozen=True)
class EntryCheck:
    """A change whose path finds another entry than the one it was made
    against: that entry as it is (:meth:`EffectiveAsset.handed_form`), and
    the path to the one it was made against, where exactly one is left."""

    now: str
    repoint: str = ""


def check_entry(eff: EffectiveAsset, change: Change) -> Optional[EntryCheck]:
    """Whether ``change``'s path still finds, by its count, the entry it was
    made against: ``None`` where it does, or there is nothing to ask.

    A count is taken in one list.  A patch that adds a slot, or a mod before
    this one that takes out an entry like it, moves what the count finds
    without a word: the path still finds one entry, and the wrong one.
    """
    if not change.entry or eff.element is None:
        return None
    target = eff.targets.get(change.key)
    if target is None:
        return None  # it finds nothing: said where what it finds is counted
    counted = counted_entry(eff, change.path, target)
    if counted is None:
        return None
    at, entry, steps = counted
    now = eff.handed_form(entry, change.origin)
    if now == change.entry:
        return None
    gone = eff.gone_before(change.key)
    like = [
        other for other in entry.getparent()
        if getattr(other, "tag", None) == entry.tag and not gone(other)
        and eff.handed_form(other, change.origin) == change.entry
    ]
    repoint = ""
    if len(like) == 1:
        step, _counted = paths.step_for(like[0], gone=gone)
        path = "/" + "/".join([*steps[:at], step, *steps[at + 1:]])
        hits = resolve_as_the_game_has_it(eff.element, path, gone)
        if len(hits) == 1 and (hits[0] is like[0] or like[0] in hits[0].iterancestors()):
            repoint = path
    return EntryCheck(now, repoint)


def recounted(node, narrowed: str, gone: Callable[[object], bool]) -> str:
    """The counting step to ``node`` once what is gone is what ``gone``
    says: its place among the entries ``narrowed`` picks out of its parent
    (all of its name, for ``""``) that are left."""
    parent = node.getparent()
    try:
        alike = parent.xpath(node.tag + narrowed) if narrowed else list(parent)
    except etree.XPathEvalError:
        alike = []
    left = [
        child for child in alike
        if getattr(child, "tag", None) == node.tag and (child is node or not gone(child))
    ]
    if node not in left:
        return ""
    return "%s%s[%d]" % (node.tag, narrowed, left.index(node) + 1)


def _resolve(element, path: str):
    if not path:
        return []
    try:
        return element.xpath("." + path if path.startswith("/") else path)
    except etree.XPathEvalError:
        return []


def _apply(element, origins, change: Change, game: str, run: _Running):
    """Apply one change to the tree, and hand back the node its path found,
    or ``None`` where it found none or did not look.  ``run.struck`` is what
    the changes before it removed, each the outermost of what one took out;
    a removal adds what it takes out to it.  What it found goes in
    ``run.seen``."""
    # An empty path is the asset itself. Two ops arrive that way and they are
    # opposites: ``addNextSibling`` invents the asset (handled by the caller,
    # which is building that very tree), and ``remove`` deletes it outright --
    # which XPath allows and real mods do. That second one used to fall through
    # _resolve's "no path, no matches" and be dropped on the floor, so a mod
    # that deletes an asset was listed as deleting it and then shown as having
    # changed nothing.
    if change.op == OP_REMOVE and not change.path:
        _mark_removed(element, origins, change.key)
        return None

    def gone(node) -> bool:
        return (origin := _known(origins, node)) is not None and origin.is_removed

    # A removed node stays in the tree, struck through, so it can be shown and
    # undone -- but the game no longer has it, so no path finds it. *Pin Button
    # Preset* swaps four regions whole: each new region goes in beside the one
    # swapped just before, and that anchor matched the old copy as well as the
    # new, so two regions vanished from the asset as shown.
    # Nor does it count in a position: with the fifth of six entries removed,
    # ``Item[6]`` is past the end of the game's list and ``Item[5]`` is the
    # sixth.  Counted with the struck entry in, a second removal was shown
    # taking out the entry the game never reached.
    # And ``add`` goes into it: *Orchard Rework* merges ``<Template>`` in at
    # the asset's root to turn an orchard into a farm, and the export wrote it
    # while the Studio showed the asset without it.
    if change.op in (OP_ADD, OP_MERGE) and not change.path:
        matches = [element]
    else:
        with without(run.struck):
            matches = [node for node in _resolve(element, change.path) if not gone(node)]
    run.seen[change.key] = (len(matches), matches[0].text or "" if len(matches) == 1 else None)
    if len(matches) != 1:
        return None  # unresolvable changes are reported by the verifier, not here
    target = matches[0]

    if change.op == OP_REPLACE:
        run.handed.setdefault((id(target), change.origin), target.text or "")
        target.text = change.new_value
        previous = _known(origins, target)
        # Editing a node this mod added stays owned by the addition.
        if previous is None or not previous.is_mod_owned:
            _remember(origins, target, Origin(PROV_EDITED, change.key))

    elif change.op == OP_REMOVE:
        _mark_removed(target, origins, change.key)
        run.struck.append(target)

    elif change.op in (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV, OP_MERGE):
        payload = parse_payload(change)
        if payload is None:
            return target
        # A payload may be a single element or a wrapper holding several.
        wrapped = payload.tag == WRAPPER
        content = [node for node in payload if isinstance(node.tag, str)] if wrapped else [payload]

        def noted(what, node, where) -> None:
            # Where in the payload it came from, as payload paths are written:
            # a wrapper's own nodes numbered, a single payload node the root.
            steps = where if wrapped else where[1:]
            path = ".".join(str(step) for step in steps)
            if what == applying.ADDED:
                _mark_subtree(node, origins, PROV_ADDED, change.key, path)
            else:
                # A value that is there -- another mod's, by the time this one
                # loads -- is set, and is this change's from then on: an edit
                # to it belongs in the payload, since the payload is what says
                # what the field holds.
                _remember(origins, node, Origin(PROV_SET, change.key, path))

        # What a removal took out stays in the tree, struck through, and a
        # merge passes it by as the game does.
        applying.apply(change.op, target, content, game, noted=noted, gone=gone)
    return target


def _mark_removed(element, origins, key) -> None:
    """``element`` and all under it, removed by the change with ``key``.

    The removal's key and not the one the node had: whose doing a removed
    node is, is the mod that removed it.  Kept from before, it named the mod
    that had added or set the node, or for a node the game ships nobody, and
    the editor then said the mod being edited had removed it.
    """
    previous = _known(origins, element)
    _remember(
        origins,
        element,
        Origin(PROV_REMOVED, key, previous.payload_path if previous else ""),
    )
    for child in element:
        if isinstance(child.tag, str):
            _mark_removed(child, origins, key)


# ---------------------------------------------------------------- payload edits
def set_payload_text(change: Change, payload_path: str, text: str) -> bool:
    """Edit a value inside a change's payload.  Returns True if it changed."""
    root = parse_payload(change)
    if root is None:
        return False
    node = payload_node(root, payload_path)
    if node is None:
        return False
    node.text = text
    change.payload = _serialise(root)
    return True


def add_to_payload(change: Change, payload_path: str, element) -> bool:
    """Append a new element inside a change's payload."""
    root = parse_payload(change)
    if root is None:
        return False
    node = payload_node(root, payload_path)
    if node is None:
        return False
    node.append(copy.deepcopy(element))
    change.payload = _serialise(root)
    return True


def remove_from_payload(change: Change, payload_path: str) -> str:
    """Delete a node inside a payload.

    Returns ``"removed"``, ``"empty"`` when the payload root itself went (so the
    caller should drop the whole change), or ``"failed"``.
    """
    root = parse_payload(change)
    if root is None:
        return "failed"
    if not payload_path:
        return "empty"
    parent, index = payload_parent(root, payload_path)
    if parent is None or index < 0 or index >= len(parent):
        return "failed"
    if parent is root and root.tag == WRAPPER and len(root) == 1:
        return "empty"  # the last of a run; the change has nothing left to do
    del parent[index]
    change.payload = _serialise(root)
    return "removed"
