"""Which numbers in some XML are GUIDs of assets, for the panes that show it.

A pane of ModOps or of an asset says the name of each asset a number in it
links to, beside the line, where it is no part of the text.  This finds the
numbers and says which field each is in; :class:`Namer` decides which of them
are links, by the rule the Fields view's *Refers to* column and the
References graph answer by (:func:`refs.is_link`), and names the asset.

The text is whatever is in the pane, typed half-way or not, so it is read as
tags and text with a pattern and never parsed: a pane that is not XML this
moment still has its numbers named.  What field a number is in is the path
the index keeps its evidence by -- ``/Values/Building`` and ``Radius`` for
the radius of a building -- worked out from the elements it is inside, and
for the payload of a ModOp from where the op aims.  Where that cannot be
told, the field's name across the whole game is asked instead
(:meth:`AssetIndex.reference_tag`), and then its name alone.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import modop, refs

# A tag, a comment and the like, or the text between them.  A tag left open
# is a "<" and then text: a pane being typed in has one more often than not.
_TOKEN = re.compile(
    r"""
    <!--.*?(?:-->|\Z)
    | <!\[CDATA\[.*?(?:\]\]>|\Z)
    | <\?.*?(?:\?>|\Z)
    | <![^>]*(?:>|\Z)
    | <(?P<close>/)?(?P<name>[A-Za-z_][\w.:-]*)
      (?P<attrs>(?:[^<>"']|"[^"]*"|'[^']*')*)(?P<end>/?)>
    | (?P<text>[^<]+)
    | <
    """,
    re.DOTALL | re.VERBOSE,
)
_ATTR = re.compile(r"""([A-Za-z_][\w.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# In an attribute: a number given as a field's value, ``[GUID='1010017']``
# or ``[Product=120008]``, and an asset a path starts from, ``@1010017/Cost``.
_COMPARED = re.compile(r"""([A-Za-z_][\w.-]*)\s*=\s*['"]?\s*([0-9]+)\b""")
_AT_ASSET = re.compile(r"@([0-9]+)\b")
_NUMBER = re.compile(r"[0-9]+")
_PREDICATE = re.compile(r"\[[^\[\]]*\]")
GUID = "GUID"
# Ops whose payload stands where the node they aim at stands, and not inside it.
_BESIDE = (modop.OP_REPLACE, modop.OP_ADD_NEXT, modop.OP_ADD_PREV)


@dataclass(frozen=True)
class Mention:
    """A number in the text that may be a GUID: where it is, counted in
    characters of the text, and the field it is the value of -- ``path``
    the element that holds that field, as the index keeps it, or "" where
    that cannot be told."""

    start: int
    end: int
    guid: str
    tag: str
    path: str = ""


@dataclass
class _Open:
    name: str
    # Where this element is, as the index has it ("Values", "Building"), or
    # None; and for a ModOp, where its payload goes.
    steps: Optional[List[str]]
    inside: Optional[List[str]] = None


def mentions(text: str) -> List[Mention]:
    """Every number in ``text`` that is shaped like a GUID, in order: the
    value of an element, and in an attribute, each one a ``GUID`` attribute
    names and each one a path compares a field with."""
    found: List[Mention] = []
    stack: List[_Open] = []
    for token in _TOKEN.finditer(text):
        name = token.group("name")
        if name is not None:
            if token.group("close"):
                for at in range(len(stack) - 1, -1, -1):
                    if stack[at].name == name:
                        del stack[at:]
                        break
                continue
            attrs = token.group("attrs") or ""
            _in_attributes(attrs, token.start("attrs"), found)
            opened = _opening(name, attrs, stack[-1] if stack else None)
            if not token.group("end"):
                stack.append(opened)
            continue
        value = token.group("text")
        if value is None or not stack:
            continue
        number = value.strip()
        # A larger number is an id of another kind (the same bound as
        # refs.numeric_leaves).
        if refs.fits_a_guid(number):
            start = token.start("text") + len(value) - len(value.lstrip())
            # The element holding the field: where it is, or for a ModOp,
            # where what it carries goes.
            holder = stack[-2] if len(stack) > 1 else None
            steps = None if holder is None else (
                holder.steps if holder.steps is not None else holder.inside
            )
            path = "/" + "/".join(steps) if steps else ""
            found.append(Mention(start, start + len(number), number, stack[-1].name, path))
    return found


def _opening(name: str, attrs: str, parent: Optional[_Open]) -> _Open:
    if name == "Values":
        steps: Optional[List[str]] = [name]
    elif parent is not None and parent.steps is not None:
        steps = [*parent.steps, name]
    elif parent is not None and parent.inside is not None:
        steps = [*parent.inside, name]
    else:
        steps = None
    opened = _Open(name, steps)
    if name == "ModOp":
        opened.inside = _aimed_inside(
            {one.group(1): one.group(2) if one.group(2) is not None else one.group(3)
             for one in _ATTR.finditer(attrs)}
        )
    return opened


def _aimed_inside(attrs: Dict[str, str]) -> Optional[List[str]]:
    """Where a ModOp's payload goes, from ``Values`` down, or None."""
    kind, path, _classic = modop.type_and_path(attrs)
    if not kind or not path:
        return None
    path = _AT_ASSET.sub("", path)
    while True:
        bare = _PREDICATE.sub("", path)
        if bare == path:
            break
        path = bare
    steps = [step.strip() for step in path.split("/") if step.strip() not in ("", ".")]
    if "Values" not in steps:
        return None
    steps = steps[len(steps) - 1 - steps[::-1].index("Values"):]
    if kind in _BESIDE:
        steps = steps[:-1]
    return steps if steps and all(re.fullmatch(r"[\w.-]+", step) for step in steps) else None


def _in_attributes(attrs: str, offset: int, found: List[Mention]) -> None:
    for one in _ATTR.finditer(attrs):
        name = one.group(1)
        group = 2 if one.group(2) is not None else 3
        value, at = one.group(group), offset + one.start(group)
        if GUID.lower() in name.lower():
            numbers = [(n.start(), n.group(), GUID) for n in _NUMBER.finditer(value)]
        else:
            numbers = [(n.start(2), n.group(2), n.group(1)) for n in _COMPARED.finditer(value)]
            numbers += [(n.start(1), n.group(1), GUID) for n in _AT_ASSET.finditer(value)]
            numbers.sort()
        for start, number, tag in numbers:
            if refs.fits_a_guid(number):
                found.append(Mention(at + start, at + start + len(number), number, tag))


class Namer:
    """Which of the numbers :func:`mentions` finds are links, and to what:
    asked of ``workspace``, and held, since a pane asks again each time it
    is typed in."""

    def __init__(self, workspace):
        self.workspace = workspace
        # Assets not to name: the one a view is of, which every op in it is
        # about and every line would say.
        self.skip: frozenset = frozenset()
        self._known: Dict[tuple, object] = {}

    def forget(self) -> None:
        """Ask again: the assets there are may not be the ones there were."""
        self._known.clear()

    def held(self):
        """The loadout taken as fixed while a whole text is named.

        Each new number asks the workspace, and each question looked at the
        whole loadout again: a long mod's file was 107 looks at 32,000
        changes, seconds of the file tab drawing it.
        """
        steady = getattr(self.workspace, "steady", None)
        return steady() if steady is not None else nullcontext()

    def row(self, mention: Mention):
        """The asset ``mention`` links to (an :class:`AssetRow`), or None."""
        if mention.guid in self.skip:
            return None
        if mention.tag == GUID and mention.path == "/Values/Standard":
            # An asset's own GUID: that it is itself is no news.
            return None
        key = (mention.path, mention.tag, mention.guid)
        if key not in self._known:
            self._known[key] = self._look_up(mention)
        return self._known[key]

    def _look_up(self, mention: Mention):
        workspace = self.workspace
        if workspace is None:
            return None
        index = getattr(workspace, "index", None)
        evidence = (0, 0, True)
        if index is not None and mention.path and hasattr(index, "reference_field"):
            evidence = index.reference_field(mention.path, mention.tag)
        if not evidence[0] and index is not None and hasattr(index, "reference_tag"):
            evidence = index.reference_tag(mention.tag)
        if not evidence[0]:
            # Nothing to go by but the field's name and the number's size.
            evidence = (0, 0, True)
        if not refs.is_link(mention.tag, mention.guid, *evidence):
            return None
        return workspace.asset_row(mention.guid)


def name_of(row) -> str:
    """What an asset is called beside a line: short, since the line is the
    thing being read.  Its whole label is in the tooltip."""
    return row.display or row.name or (GUID + " " + row.guid)
