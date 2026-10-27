"""Turning a node into a ModOp path, and checking one still points at it.

A ModOp addresses what it changes with an XPath, and the whole value of
generating them is that each one picks out *exactly* the node the change is
about -- see "One ModOp per change" in the user guide.  Two things follow, and
they are the whole of this module:

*Addressing*   repeated list entries are the norm in Anno's files, so a path
               through one has to say which entry it means.  A *content*
               predicate (``Item[Product='1010365']``) survives the list being
               reordered by a patch or another mod; a position (``Tag[2]``)
               does not, and is used only where an entry holds nothing that
               identifies it.  Those are flagged all the way out to the
               generated file, because a reordering silently retargets them.

*Checking*     a path written earlier is only as good as the file it was
               written against.  Before anything is exported, every change is
               re-resolved: a path that now matches nothing, or that matches
               several nodes, or whose value has drifted, is a ModOp that
               would quietly do nothing (or far too much) in game.
"""

from __future__ import annotations

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

POSITIONAL_WARNING = (
    "This entry holds nothing that identifies it, so the path picks it by "
    "position — positional paths break if a game patch or another mod reorders "
    "the list."
)


@dataclass(frozen=True)
class Generated:
    """A path, and what had to be given up to write it."""

    path: str
    positional: bool = False
    warning: str = ""


def _children(elem) -> List:
    return [child for child in elem if isinstance(child.tag, str)]


def value_of(elem, tag: str) -> str:
    child = elem.find(tag)
    if child is None or len(_children(child)):
        return ""
    return (child.text or "").strip()


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


def _key_predicate(elem, siblings: List) -> str:
    """``[Product='1010365']`` for this entry, or "" if nothing identifies it.

    Unique among its siblings, not merely present: a predicate that matches two
    entries addresses neither, which is the one thing a generated path may
    never do.
    """
    tried = key_candidates(elem)

    for tag in tried:
        value = value_of(elem, tag)
        if not value:
            continue
        if any(
            other is not elem and value_of(other, tag) == value
            for other in siblings
        ):
            continue
        return "[%s=%s]" % (tag, quote(value))
    return ""


def path_for(root, node, keyed: bool = False) -> Generated:
    """The ModOp path addressing ``node`` inside the asset ``root``.

    Written against the asset element, which is what a ModOp's ``Path`` is
    relative to: the first step is usually ``Values``.

    A step only needs a predicate when its siblings share its name -- today.
    ``keyed`` writes one for every list entry even when it is the only one, for
    a file whose own earlier ModOps will have added siblings by the time this
    one runs: *Pizza Margherita* adds its item beside the one entry of a list
    three times over, and ``Products/Item`` matched two entries on the second
    and four on the third.  Only a content key, never a position.

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
        parent = current.getparent()
        if parent is None:
            raise ValueError("%r is not inside the asset it was asked about" % node.tag)
        siblings = [child for child in _children(parent) if child.tag == current.tag]
        step = current.tag
        if len(siblings) > 1 or (keyed and current.tag == LIST_ENTRY):
            predicate = _key_predicate(current, siblings)
            if predicate:
                step += predicate
            elif len(siblings) > 1:
                step += "[%d]" % (siblings.index(current) + 1)
                positional = True
        steps.append(step)
        current = parent
    steps.reverse()
    return Generated(
        "/" + "/".join(steps) if steps else "",
        positional,
        POSITIONAL_WARNING if positional else "",
    )


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
