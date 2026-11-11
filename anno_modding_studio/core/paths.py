"""Turning a node into a ModOp path, and checking one still points at it.

A ModOp addresses what it changes with an XPath, and the whole value of
generating them is that each one picks out *exactly* the node the change is
about -- see "One ModOp per change" in the user guide.  Two things follow, and
they are the whole of this module:

*Addressing*   repeated list entries are the norm in Anno's files, so a path
               through one has to say which entry it means.  A *content*
               predicate (``Item[Product='1010365']``) survives the list being
               reordered by a patch or another mod; a count does not, and is
               used only where entries alike hold nothing that tells them
               apart: ``Item[Fertility='31361'][2]``, the second of those, or
               ``Tag[2]`` where an entry holds no value at all.  Those are
               flagged all the way out to the generated file, because adding
               or removing one alike silently retargets them.

*Checking*     a path written earlier is only as good as the file it was
               written against.  Before anything is exported, every change is
               re-resolved: a path that now matches nothing, or that matches
               several nodes, or whose value has drifted, is a ModOp that
               would quietly do nothing (or far too much) in game.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lxml import etree

# Tried first when choosing what identifies one entry in a list.  It is a
# preference, not a rule: the key can be any child that happens to be unique --
# ``Product`` in a cost list, ``Attribute`` in an expedition list -- and a fixed
# list would always be missing some list's real key, so anything not named here
# is still tried afterwards, in the order the entry itself writes its children.
#
# :func:`core.recording._pair_by_key` walks the same order to line up two versions
# of a list, so an entry is matched by whatever addresses it.
#
# The search does not stop at direct children: see :func:`key_candidates`.
KEY_CHILDREN = (
    "GUID",
    "Product",
    "Ingredient",
    "Good",
    "Item",
    "Name",
    "Attribute",
    "Key",
    "Type",
)

# What a list's entries are called: the step ``path_for(..., keyed=True)`` keys.
LIST_ENTRY = "Item"

# A step that picks an entry by a count: ``Item[2]``, or ``Item[Fertility='31361'][2]``
# counting among the entries alike (:func:`step_for`).  Groups: the tag, what
# narrows the count where something does, the count.
COUNTED_STEP = re.compile(r"\A([^\[\]/]+)(\[.*\])?\[(\d+)\]\Z")

POSITIONAL_WARNING = (
    "Nothing in this entry tells it apart from the others, so the path picks "
    "it by a count -- positional paths break if a game patch or another mod "
    "adds or removes an entry like it."
)


@dataclass(frozen=True)
class Generated:
    """A path, and what had to be given up to write it."""

    path: str
    positional: bool = False
    warning: str = ""


def _children(elem) -> List:
    return [child for child in elem if isinstance(child.tag, str)]


def value_of(elem, tag: str, text=None) -> str:
    """What ``elem``'s field ``tag`` holds, "" where it holds fields instead.
    ``text`` reads a node's text where the tree's own is not the one to go
    by (:func:`path_for`)."""
    child = elem.find(tag)
    if child is None or len(_children(child)):
        return ""
    return ((text(child) if text is not None else child.text) or "").strip()


def quote(value: str) -> str:
    """A literal XPath 1.0 can actually hold.

    There is no escaping inside an XPath string literal, so a value containing
    an apostrophe has to be written in double quotes instead of quoted "safely"
    into something that no longer matches.

    A value holding *both* kinds of quote cannot be written as any single
    literal, and wrapping it in either one produces an expression that is not
    merely wrong but malformed -- it parses as far as the second quote and then
    matches nothing at all.  ``concat()`` is the one construct XPath 1.0 offers
    for this, so the apostrophes are spliced in as their own arguments.
    """
    if "'" not in value:
        return "'%s'" % value
    if '"' not in value:
        return '"%s"' % value
    parts: List[str] = []
    run = ""
    for char in value:
        if char == "'":
            if run:
                parts.append("'%s'" % run)
                run = ""
            parts.append("\"'\"")
        else:
            run += char
    if run:
        parts.append("'%s'" % run)
    return "concat(%s)" % ", ".join(parts)


def key_candidates(elem) -> List[str]:
    """What might identify ``elem``, best first.

    Direct children come first -- the preferred tags, then whatever else the
    entry writes -- so an entry that has always been addressed by one of them
    goes on being addressed by it.  Only once those are exhausted does this
    look a level deeper, because some lists keep their key inside a wrapper:
    Anno 117 writes ``<VectorElement><InheritedIndex>`` and nothing at the top
    level of the entry tells one row from the next.
    """
    tried = list(KEY_CHILDREN)
    for child in _children(elem):
        if child.tag not in tried:
            tried.append(child.tag)
    for child in _children(elem):
        for grandchild in _children(child):
            nested = "%s/%s" % (child.tag, grandchild.tag)
            if nested not in tried:
                tried.append(nested)
    return tried


def _key_predicate(elem, siblings: List, text=None) -> str:
    """``[Product='1010365']`` for this entry, or "" if nothing identifies it.

    Unique among its siblings, not merely present: a predicate that matches two
    entries addresses neither, which is the one thing a generated path may
    never do.
    """
    tried = key_candidates(elem)

    for tag in tried:
        value = value_of(elem, tag, text)
        if not value:
            continue
        if any(
            other is not elem and value_of(other, tag, text) == value
            for other in siblings
        ):
            continue
        return "[%s=%s]" % (tag, quote(value))
    return ""


def path_for(root, node, keyed: bool = False, gone=None, text=None) -> Generated:
    """The ModOp path addressing ``node`` inside the asset ``root``.

    Written against the asset element, which is what a ModOp's ``Path`` is
    relative to: the first step is usually ``Values``.

    A step only needs a predicate when its siblings share its name -- today.
    ``keyed`` writes one for every list entry even when it is the only one, for
    a file whose own earlier ModOps will have added siblings by the time this
    one runs: *Pizza Margherita* adds its item beside the one entry of a list
    three times over, and ``Products/Item`` matched two entries on the second
    and four on the third.  Only a content key, never a position.

    ``gone`` says which nodes of ``root`` the game no longer has when the op
    runs: struck out in a tree that still shows them, by a removal before it.
    Those are no siblings to count or to tell apart from.  With the fifth of
    six entries removed, the sixth is ``Item[5]``; written ``Item[6]`` it was
    past the end of the list by then and removed nothing.

    ``text`` reads a field's value as the op finds it, where the tree holds
    what the op's own changes make of it.  An op replaces an entry's key
    after everything else it does through that entry: with the first of two
    pools of 2001 given a new one, the second is still the second 2001 to
    the op, not the only one.

    The empty path is ``root`` itself (see :func:`resolve`), so a ``node`` that
    is not inside ``root`` at all raises :class:`ValueError` rather than being
    given one: written down, it would be a ModOp aimed at the whole asset.
    """
    if node is None:
        raise ValueError("there is no node to write a path for")
    steps: List[str] = []
    positional = False
    current = node
    while current is not root:
        if current.getparent() is None:
            raise ValueError("%r is not inside the asset it was asked about" % node.tag)
        step, counted = step_for(current, keyed=keyed, gone=gone, text=text)
        positional = positional or counted
        steps.append(step)
        current = current.getparent()
    steps.reverse()
    return Generated(
        "/" + "/".join(steps) if steps else "",
        positional,
        POSITIONAL_WARNING if positional else "",
    )


def step_for(node, keyed: bool = False, gone=None, text=None) -> Tuple[str, bool]:
    """The one step of a path that picks ``node`` out of its parent, and
    whether it picks it by a count: see :func:`path_for`.

    Where no field tells the entry apart, it is counted among the entries
    that look like it in the field the fewest share -- the second entry with
    pool 31361, ``Item[Fertility='31361'][2]`` -- rather than among every
    entry in the list.  A fertility set lists one pool twice, and a slot the
    game adds anywhere else in it moves no entry of that pool.  Only an entry
    with no field to go by at all is counted in the whole list.
    """
    siblings = [child for child in _children(node.getparent()) if child.tag == node.tag]
    if len(siblings) <= 1 and not (keyed and node.tag == LIST_ENTRY):
        return node.tag, False
    predicate = _key_predicate(node, siblings, text)
    if predicate:
        return node.tag + predicate, False
    if len(siblings) <= 1:
        return node.tag, False
    # A key is told apart from the struck entries too: with the removal
    # before it switched off, a key two entries share matches both.  The
    # count is of what is left: the game counts only that.
    tag = _fewest_alike(node, siblings, text)
    alike = siblings if tag is None else [
        child for child in siblings if value_of(child, tag, text) == value_of(node, tag, text)
    ]
    left = [child for child in alike if child is node or gone is None or not gone(child)]
    # Even where it is the one left: a bare step would find every entry the
    # struck ones are again, switched off.
    narrowed = "" if tag is None else "[%s=%s]" % (tag, quote(value_of(node, tag, text)))
    return "%s%s[%d]" % (node.tag, narrowed, left.index(node) + 1), True


def _fewest_alike(elem, siblings: List, text=None) -> Optional[str]:
    """The field of ``elem`` the fewest of its ``siblings`` share the value
    of, or ``None`` where it holds no field with a value.  Of two as few,
    the one :func:`key_candidates` tries first."""
    best: Optional[str] = None
    fewest = 0
    for tag in key_candidates(elem):
        value = value_of(elem, tag, text)
        if not value:
            continue
        alike = sum(1 for other in siblings if value_of(other, tag, text) == value)
        if best is None or alike < fewest:
            best, fewest = tag, alike
    return best


def resolve(root, path: str) -> List:
    """Every node in ``root`` that ``path`` selects.

    An empty path is the asset itself -- which is how the Studio spells a
    ModOp aimed at the whole asset, since ``/`` on its own is not a legal
    XPath expression.  A leading slash is optional: both spellings turn up in
    shipping mods, often in the same file.
    """
    text = (path or "").strip().rstrip("/")
    if not text:
        return [root]
    if not text.startswith("/"):
        text = "/" + text
    try:
        found = root.xpath("." + text)
    except etree.XPathEvalError:
        return []
    return [node for node in found if isinstance(getattr(node, "tag", None), str)]


def verify(root, path: str, expected: Optional[str]) -> Tuple[bool, str]:
    """Whether ``path`` still addresses one node holding ``expected``.

    ``(ok, message)``.  ``expected`` of ``None`` asks only that the path lands
    on exactly one node -- which is what an addition or a removal needs, where
    there is no previous value to have drifted.
    """
    matches = resolve(root, path)
    if not matches:
        return False, "path matches nothing in the source asset"
    if len(matches) > 1:
        return (
            False,
            "path matches %d nodes, so a ModOp would change them all"
            % len(matches),
        )
    if expected is None:
        return True, "ok"
    found = (matches[0].text or "").strip()
    if found != (expected or "").strip():
        return (
            False,
            "expected %r here, but the file says %r" % (expected, found),
        )
    return True, "ok"
