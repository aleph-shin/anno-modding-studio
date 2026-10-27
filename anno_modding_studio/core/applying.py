"""What an op does to each node its path finds, by its type: the loader's
``XmlOperation::Apply``, in one place.

Ported from the loaders' own source, ``libs/xml-operations/src/
xml_operations.cc`` of jakobharder/anno-mod-loader: ``main`` at ``5a4464d`` for
Anno 117 and ``stable/anno1800`` at ``8326b7c`` for Anno 1800. Each part names
the lines it follows, 117's first. ``docs/proposals/reader-proposal.md``,
section 5, says why this is apart from recording what an op comes to.
MIT-licensed upstream (© 2019 Alexander Guettler, 2022 Jakob Harder); see
THIRD-PARTY-NOTICES.md.

pugixml, which the loader reads with, keeps no run of text that is only
whitespace, and holds the text after an element as a node of its own. lxml
keeps both as the ``text`` and ``tail`` of elements, so here a run of
whitespace is no text at all, and an element taken out leaves the text after
it where it was.

Two things the loader does as it applies an op are left to the reader, which
has the document and the mod's settings they need: working each ``ModValue``
out where its copy lands (117 L1021-1087), which the reader does to the
payload first (``reader.freeze_computed``), and putting an added asset with a
``BaseAssetGUID`` in after its base (117 L1240-1264).
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from . import games
from .modop import (
    MOD_ITEM,
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
)

# What :func:`apply` tells its caller as it goes, for one that keeps track of
# where each node came from: a node it put in, copied from the payload, or a
# field whose value it set from one.  ``where`` is the place in the payload it
# came from, a child index at each level, the payload's own nodes counted
# first.
ADDED = "added"
SET = "set"
Noted = Callable[[str, Any, Tuple[int, ...]], None]

# What the loader warns of, as :func:`apply` says it.
NO_MERGE_KEY = "ModItem needs attribute 'Merge'"
NOT_AFTER = "%s is not allowed after ModItem"
NOT_BESIDE = 'ModItem %s="%s" not found'
NOT_FOUND = 'ModItem "%s" not found: %s'


def apply(
    kind: str,
    node,
    content: Sequence,
    game: str = "",
    noted: Optional[Noted] = None,
    gone: Optional[Callable[[Any], bool]] = None,
) -> List[str]:
    """Do to ``node`` what an op of type ``kind`` carrying ``content`` does
    to each node its path finds (117 L851-884, 1800 L649-678).

    ``content`` is the op's payload, elements in file order; nothing of it is
    changed.  ``gone`` names the nodes a merge is to pass by as no longer in
    the document, for a tree that keeps what was taken out on show.

    What comes back is what the loader would have warned of, in its words:
    an op it carried out only in part, or not as written.
    """
    warned: List[str] = []
    note = noted or _nothing
    parent = node.getparent()
    if kind == OP_REMOVE:
        if parent is not None:
            remove(node)
        return warned
    if kind == OP_MERGE:
        merge(node, content, game, note, gone, warned)
        return warned
    if kind == OP_ADD:
        for index, child in enumerate(content):
            note(ADDED, _append(node, _copy(child)), (index,))
        return warned
    if parent is None:
        warned.append("no parent to put a sibling in")
        return warned
    if kind == OP_ADD_NEXT:
        # Each after the one before it, so the payload keeps its order.
        after = node
        for index, child in enumerate(content):
            after = _after(after, _copy(child))
            note(ADDED, after, (index,))
    elif kind == OP_ADD_PREV:
        for index, child in enumerate(content):
            copied = _copy(child)
            node.addprevious(copied)
            note(ADDED, copied, (index,))
    elif kind == OP_REPLACE:
        # Each straight after the node it replaces, so the last ends up first:
        # X, Y, Z comes out Z, Y, X, as measured with xmltest in both games.
        for index, child in enumerate(content):
            note(ADDED, _after(node, _copy(child)), (index,))
        remove(node)
    else:
        warned.append("%s is not an op type" % kind)
    return warned


def merge(
    node,
    content: Sequence,
    game: str = "",
    noted: Optional[Noted] = None,
    gone: Optional[Callable[[Any], bool]] = None,
    warned: Optional[List[str]] = None,
) -> None:
    """``ModOpMerge`` (117 L1272-1285, 1800 L651-658).

    One element named like the node it goes into stands for that node: the
    "legacy merge", which goes in by way of the node's parent, as the first of
    its name there.  Anything else goes into the node itself.
    """
    if not content:
        return
    warned = [] if warned is None else warned
    note = noted or _nothing
    if stands_for(content, node.tag, game):
        parent = node.getparent()
        if parent is not None:
            _merge(parent, [(0, content[0])], game, note, gone, warned, ())
            return
        # An asset with no list above it, as the reader holds one: the node
        # is the first of its name, and the merge goes into it.
        _merge_attributes(node, content[0])
        _merge(node, _inside(content[0]), game, note, gone, warned, (0,))
        return
    _merge(node, list(enumerate(content)), game, note, gone, warned, ())


def remove(node) -> None:
    """Take ``node`` out, leaving the text after it where it was."""
    parent = node.getparent()
    if parent is None:
        return
    words = node.tail if node.tail and node.tail.strip() else ""
    previous = node.getprevious()
    parent.remove(node)
    if not words:
        return
    if previous is not None:
        previous.tail = _text_of(previous.tail) + words
    else:
        parent.text = _text_of(parent.text) + words


# -- RecursiveMerge -------------------------------------------------------
# The payload's nodes, each with its index among its parent's children, and a
# run of text as a str in place of an element.
_Item = Tuple[int, Any]


def _merge(root, items: List[_Item], game, note, gone, warned, where) -> None:
    """``RecursiveMerge`` (117 L1089-1232, 1800 L797-847): each of ``items``
    into ``root``, by name, and among nodes of one name by position.

    The *n*-th node of a name in the payload goes into the *n*-th of that name
    in ``root``, whatever either holds, and one past the end is appended.
    Anno 117 compares names in any case and reads ``<ModItem>``; Anno 1800
    compares them as written and takes a ``<ModItem>`` for any other node.
    """
    if any(not isinstance(item, str) for _index, item in items):
        # Text before the first element is passed over (117 L1110-1113).
        while items and isinstance(items[0][1], str):
            items = items[1:]
    reads_items = game != games.ANNO_1800
    counted: Dict[str, int] = {}
    indexing = True
    # The node the last step merged into, which a step that finds nothing
    # new merges into again, as the loader's own variable does.
    found = root
    for index, item in items:
        name = "" if isinstance(item, str) else item.tag
        place = counted.get(name, 0)
        counted[name] = place + 1
        here = (*where, index)
        append = True
        if reads_items and not isinstance(item, str) and item.attrib and named(
            item.tag, MOD_ITEM, game
        ):
            item = _copy(item)
            indexing = False
            found, append = _mod_item(root, item, found, warned)
            if not append:
                note(ADDED, found, here)
                continue
        elif not indexing:
            warned.append(NOT_AFTER % name)
            continue
        else:
            found = _nth(root, name, place, game, gone)
        if found is not None and isinstance(item, str):
            # Text sets the run it finds; what it sets is the value of the
            # payload's element it was in.
            _set_text(found, item)
            note(SET, root, where)
        elif found is not None:
            _merge_attributes(found, item)
            _merge(found, _inside(item), game, note, gone, warned, here)
        elif append:
            if isinstance(item, str):
                _append_text(root, item)
                note(SET, root, where)
            else:
                note(ADDED, _append(root, _copy(item)), here)


def _mod_item(root, item, found, warned):
    """``(node, append)`` for a ``<ModItem>`` (117 L1131-1199): the entry it
    finds, or where it went in when it found none, and whether it is still to
    be appended.  ``item`` is a copy, and loses the attributes the loader
    takes off it."""
    merge_by = _attribute(item, "Merge")
    if merge_by is None:
        warned.append(NO_MERGE_KEY)
        return found, True
    query = _item_query(item, merge_by)
    try:
        hits = [hit for hit in root.xpath(query) if isinstance(getattr(hit, "tag", None), str)]
        found = hits[0] if hits else None
    except etree.XPathError as exc:
        warned.append(NOT_FOUND % (query, exc))
    del item.attrib[merge_by]
    if found is not None:
        return found, True
    for side in ("Append", "Prepend"):
        named = _attribute(item, side)
        if named is None:
            continue
        query = "Item[%s]" % (item.get(named) or "")
        del item.attrib[named]
        try:
            hits = [h for h in root.xpath(query) if isinstance(getattr(h, "tag", None), str)]
        except etree.XPathError:
            hits = []
        if not hits:
            warned.append(NOT_BESIDE % (side, query))
            return None, True
        copied = _copy(item)
        copied.tag = "Item"
        if side == "Append":
            _after(hits[0], copied)
        else:
            hits[0].addprevious(copied)
        return copied, False
    item.tag = "Item"
    return None, True


def mod_item_query(item) -> Optional[str]:
    """The XPath, from the list, that a ``<ModItem>`` finds its entry by --
    ``Item[Ingredient='2174']`` -- or ``None`` for one with no ``Merge=``."""
    merge_by = _attribute(item, "Merge")
    return None if merge_by is None else _item_query(item, merge_by)


def _item_query(item, merge_by: str) -> str:
    wanted = item.get(merge_by) or ""
    if "=" not in wanted:
        key = _child(item, wanted)
        return "Item[%s='%s']" % (wanted, _first_text(key) if key is not None else "")
    return "Item[%s]" % wanted


def _nth(root, name: str, place: int, game, gone):
    """The ``place``-th child of ``root`` named ``name`` (117 L1094-1107), or
    for ``""`` the ``place``-th run of text in it; ``None`` past the end."""
    if not name:
        runs = _runs(root)
        return runs[place] if place < len(runs) else None
    met = 0
    for child in root:
        if not isinstance(child.tag, str) or not named(child.tag, name, game):
            continue
        if gone is not None and gone(child):
            continue
        if met == place:
            return child
        met += 1
    return None


class _Run:
    """A run of text in an element, as pugixml holds one: the element's own
    text, or the text after one of its children."""

    def __init__(self, owner, holder, field: str):
        self.owner = owner
        self.holder = holder
        self.field = field


def _runs(element) -> List[_Run]:
    found = []
    if _text_of(element.text):
        found.append(_Run(element, element, "text"))
    for child in element:
        if _text_of(child.tail):
            found.append(_Run(element, child, "tail"))
    return found


def _set_text(run, words: str) -> None:
    setattr(run.holder, run.field, words)


def _append_text(element, words: str) -> None:
    children = list(element)
    if children:
        last = children[-1]
        last.tail = _text_of(last.tail) + words
    else:
        element.text = _text_of(element.text) + words


def _merge_attributes(node, patch) -> None:
    """``MergeProperties`` (117 L1003-1015, 1800 L782-794): the payload's
    attributes, each set on the node."""
    for name, value in patch.attrib.items():
        node.set(name, value)


# -- small things ---------------------------------------------------------
def _nothing(_what, _node, _where) -> None:
    return None


def stands_for(content: Sequence, name: str, game: str = "") -> bool:
    """Whether ``content`` is the one element named like the node ``name``
    it is merged into, and so stands for that node: the loader's "legacy
    merge".  Asked here by :func:`merge` and by the reader, which reads such
    a payload as what the target should look like."""
    return len(content) == 1 and named(content[0].tag, name, game)


def named(tag, name: str, game: str = "") -> bool:
    """Whether a node called ``tag`` answers to ``name``: in any case in Anno
    117 (``pugih::equals``), as written in Anno 1800 (``strcmp``)."""
    if not isinstance(tag, str):
        return False
    if game == games.ANNO_1800:
        return tag == name
    return tag.lower() == name.lower()


def _inside(element) -> List[_Item]:
    """What is inside a payload element, as the loader has it: elements, and
    each run of text that is more than whitespace, in order."""
    items: List[_Item] = []
    if _text_of(element.text):
        items.append((0, element.text))
    for child in element:
        if isinstance(child.tag, str):
            items.append((element.index(child), child))
        if _text_of(child.tail):
            items.append((element.index(child), child.tail))
    return items


def _text_of(words) -> str:
    return words if words and words.strip() else ""


def _first_text(element) -> str:
    return _text_of(element.text)


def _attribute(element, name: str) -> Optional[str]:
    """The name an attribute is spelled with on ``element``, in any case."""
    return next((key for key in element.attrib if key.lower() == name.lower()), None)


def _child(element, name: str):
    return next(
        (child for child in element if isinstance(child.tag, str)
         and child.tag.lower() == name.lower()),
        None,
    )


def _copy(element):
    copied = copy.deepcopy(element)
    copied.tail = None
    return copied


def _append(parent, child):
    parent.append(child)
    return child


def _after(node, child):
    node.addnext(child)
    # lxml carries the node's own tail past what is put after it; pugixml has
    # that text as a node of its own, after the node, so it goes first.
    if node.tail and child.tail is None:
        child.tail, node.tail = node.tail, None
    return child
