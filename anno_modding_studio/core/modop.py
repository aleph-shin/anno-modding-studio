"""What one ModOp is: its type, target, payload and condition.

An op has a target, a payload, a condition and a type.  The type is written
down; each of the other three is either written down too or worked out from
the document as the game loads it (section 4 of
docs/proposals/ops-as-written-proposal.md).  This module says which, for an
op's attributes and body as written, and nothing more: it reads no file and
runs no op.  :mod:`.modops` reads ModOps files and works out what each op
comes to; everything that only needs to know what an op *is* asks here.

Before this module the answers were spread over :mod:`.modops`, where the
reader's private checks gave them, and every module that could not import
the reader -- :mod:`.changes` among them, which the reader imports -- worked
its own out again with a regular expression.  A leaf, so that any module can
ask it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from . import games

# -- the type --------------------------------------------------------------------
# The kinds of ModOp the Studio reads and writes. "replace" edits one
# existing value; the rest are structural, and carry an XML payload rather
# than a scalar.
OP_REPLACE = "replace"
OP_ADD = "add"                    # append the payload inside the node at Path
OP_ADD_NEXT = "addNextSibling"    # insert the payload after the node at Path
OP_ADD_PREV = "addPrevSibling"    # insert the payload before the node at Path
OP_REMOVE = "remove"              # delete the node at Path
# Make sure the node at Path holds the payload's field: made if it is not
# there, set if it is. The one op for a field an asset does not spell out --
# see :attr:`.changes.Change.ensured`.
OP_MERGE = "merge"

# How a ModOp names what it is.  The classic form spells it in Type=, which
# both loaders read in any case; the short form uses the op name as the
# attribute holding the target, and only Anno 117 has it.  Each game's own
# list, from its loader's ReadType (``xml_operations.cc``): a type a game
# does not list is an "Unknown ModOp" to it, and the op does nothing.
CLASSIC_TYPES = {
    "replace": OP_REPLACE,
    "add": OP_ADD,
    "remove": OP_REMOVE,
    "merge": OP_MERGE,
    "addnextsibling": OP_ADD_NEXT,
    "addprevsibling": OP_ADD_PREV,
}
_CLASSIC_117 = {
    **CLASSIC_TYPES,
    "addafter": OP_ADD_NEXT,
    "addbefore": OP_ADD_PREV,
}
# Types a loader knows that the Studio does not read, and carries as written:
# Anno 117's ``Type="assets"``, which adds assets where the file keeps them.
_KNOWN_UNREAD = {games.ANNO_117: {"assets"}}

# Append/Prepend are sibling insertions, not "add inside, at one end".  Every
# one of them in the shipping mods targets a list *entry* while every Add
# targets the list itself -- and appending inside an <Item> would nest an
# <Item> in an <Item>, which is not a shape any of those lists have.
SHORTHAND_ATTRS = {
    "Replace": OP_REPLACE,
    "Add": OP_ADD,
    "Remove": OP_REMOVE,
    "Merge": OP_MERGE,
    "Append": OP_ADD_NEXT,
    "Prepend": OP_ADD_PREV,
}


def _classic(game: str) -> Dict[str, str]:
    # A game not known reads what either game reads.
    return CLASSIC_TYPES if game == games.ANNO_1800 else _CLASSIC_117


def _has_short_form(game: str) -> bool:
    return game != games.ANNO_1800


def loader_knows_type(attrs: Dict[str, str], game: str) -> bool:
    """Whether ``game``'s loader knows the type the op spells, read by the
    Studio or not."""
    declared = (attrs.get("Type") or "").strip().lower()
    if declared:
        return declared in _classic(game) or declared in _KNOWN_UNREAD.get(game, ())
    return _has_short_form(game) and any(name in attrs for name in SHORTHAND_ATTRS)


KNOWN_ATTRS = set(SHORTHAND_ATTRS) | {
    "Type",
    "GUID",
    "Path",
    "Condition",
    "Content",
    # Names the op for tools that switch it on and off; nothing to us.
    "ModOpID",
    "Skip",
    # Anno 117's "every asset that has this property": read as the search it
    # is, and folded into the op's path -- see :func:`property_path`.
    "Property",
    # Only quietens the loader's "no matching node" warning. An op rebuilt as
    # narrow changes aims at a node that is there, so it has no use for it; an
    # op that goes back out as written keeps it, with all its attributes.
    "AllowNoMatch",
    # The Studio's own mark, from when an op could be rewritten as narrow
    # changes, for one that was to go back out as written. Every op does
    # now, so it means nothing; an op carrying it is read as one without.
    "KeepAsWritten",
}

# Attribute names the Anno 117 loader reads in any case -- ``merge=``, ``MERGE=``,
# ``type=``, ``path=``, ``condition=`` all work in xmltest's 117 build, where the
# 1800 build ignores every one of them. ``guid=`` is ignored by both, so GUID is
# not among them.
_ANY_CASE_117 = {
    name.lower(): name for name in (*SHORTHAND_ATTRS, "Type", "Path", "Condition")
}


def as_117_reads(attrs: Dict[str, str]) -> Dict[str, str]:
    """An op's attributes the way the Anno 117 loader takes them.

    Two differences from 1800, each measured against the loader rather than
    read about. Op attribute names match in any case -- *Obsidian Weapon
    Extended* writes ``merge="@30456/Standard"``. And a path starting ``//``
    searches the whole file whatever GUID the op names -- *Pizza Margherita*
    gives three ops ``GUID='106338'`` and adds its item to ten other assets --
    so the GUID goes, and the op is the search the game runs. Written back out
    without it, it means the same thing to either game.
    """
    out = dict(attrs)
    for name, value in attrs.items():
        canonical = _ANY_CASE_117.get(name.lower())
        if canonical and canonical != name and canonical not in attrs:
            del out[name]
            out[canonical] = value
    path = out.get("Path") or next((out[name] for name in SHORTHAND_ATTRS if name in out), "")
    if (path or "").strip().startswith("//"):
        out.pop("GUID", None)
    return out


def type_and_path(attrs: Dict[str, str], game: str = "") -> Tuple[str, str, bool]:
    """``(kind, path, classic)``: the op's type as the Studio names it, the
    path it gives, and whether it spells its type in ``Type=``.

    ``kind`` is "" for a type ``game`` does not read, or the Studio does not
    (:func:`loader_knows_type` says which), or an op that names no type at
    all.  The path is as written, stripped, and may still carry the
    ``@GUID`` it starts from (:data:`AT_ASSET`).
    """
    declared = attrs.get("Type")
    if declared:
        kind = _classic(game).get(declared.strip().lower(), "")
        return kind, (attrs.get("Path") or "").strip(), True
    if _has_short_form(game):
        for name, mapped in SHORTHAND_ATTRS.items():
            if name in attrs:
                return mapped, (attrs[name] or "").strip(), False
    return "", "", False


# -- the target -------------------------------------------------------------------
TARGET_SEARCH = "search"         # across assets: the op names no GUID
TARGET_PATTERN = "pattern"       # inside the assets it names: Item[City], *, a position

# A path that starts from an asset by its GUID, ``@1234/Cost``: the GUID,
# and the rest.
AT_ASSET = re.compile(r"^@([0-9]+)(/.*)?$")


def guids(text: str) -> List[str]:
    """The assets a ``GUID`` attribute names.

    Split on the comma and nothing else, which is all the loader does:
    ``GUID="500005, 500145"`` has it look for an asset whose GUID is
    `` 500145``, space and all, and find none.  Measured for both games, and
    the loaders' source says the same (``str::split`` and ``StrSplit``; see
    section 2 of docs/proposals/reader-proposal.md).
    """
    return [part for part in (text or "").split(",") if part]


def property_path(prop: str, path: str) -> str:
    """Where ``Property="X"`` aims, written as a path.

    Anno 117 lets an op target every asset that *has* a property rather than
    naming the assets: ``<ModOp Property="ModuleOwner"
    Merge="self::node()[FarmType='PlantFarm']">`` is every ``<ModuleOwner>``
    whose ``FarmType`` is ``PlantFarm``.  All of this was measured with
    xmltest rather than read anywhere:

    * the property is looked for as a child of an asset's ``<Values>`` and
      nowhere deeper -- ``Property="Item"`` matches none of the ``<Item>``s
      inside a list;
    * what is left of the expression is relative to that node, with or
      without a ``self::node()`` in front of it;
    * an absolute ``//`` path in its place wins outright, and the property is
      then only a hint;
    * with nothing to aim, the op does nothing;
    * and Anno 1800's loader does nothing with one whatever it says.

    Which makes it a search, and the Studio has those: the op goes back out as
    itself and covers assets other mods add, exactly as ``//Values/…`` does.
    """
    rest = (path or "").strip()
    if rest.startswith("//"):
        return rest
    if not rest:
        return ""
    base = "//Values/%s" % prop
    if rest.startswith("self::node()"):
        rest = rest[len("self::node()") :]
    if not rest:
        return base
    return base + rest if rest.startswith("[") else "%s/%s" % (base, rest.lstrip("/"))


def branches(path: str) -> List[str]:
    """The parts of an XPath union, split on the ``|`` that are not inside a
    predicate or a quoted string."""
    parts, depth, quote, start = [], 0, "", 0
    for at, char in enumerate(path):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(path[start:at])
            start = at + 1
    parts.append(path[start:])
    return [part.strip() for part in parts]


def reaches_other_assets(path: str) -> bool:
    """Whether a union in an op's path has a part that searches the whole
    file, as ``… | //Values[Standard/GUID='1234']/…`` does."""
    parts = branches(path or "")
    return len(parts) > 1 and any(part.startswith("//") for part in parts)


def targets_otherwise(attrs: Dict[str, str]) -> bool:
    """Whether an op aims by ``Property=`` or ``Template=``, which the loader
    builds its condition from instead of a GUID."""
    return any(name.lower() in ("property", "template") for name in attrs)


# One step of a path that names its node: a tag, or a tag picked out by the
# literal value of one field.
_NAMING_STEP = re.compile(
    r"""^[A-Za-z_][\w.-]*(\[\s*[A-Za-z_][\w./-]*\s*=\s*('[^']*'|"[^"]*"|\d+)\s*\])?$"""
)


def names_its_node(path: str) -> bool:
    """Whether a path inside an asset can only ever find the one node: plain
    steps down from the asset, each a tag or an entry picked out by a value.

    Anything else -- ``*``, ``//``, a position, a function, ``or``, an entry
    picked out by merely having a field -- is a pattern: what it finds
    depends on what is there when the game loads the op.  Read strictly, so
    a path this cannot follow counts as a pattern and not as a name.
    """
    inner = (path or "").strip()
    if not inner.strip("/"):
        return True
    if inner.startswith("//") or len(branches(inner)) > 1:
        return False
    return all(_NAMING_STEP.match(step.strip()) for step in inner.strip("/").split("/"))


def reach(guid: str, path: str) -> str:
    """How far an op's target reaches: "" where it names its node in the
    assets it names, else :data:`TARGET_SEARCH` or :data:`TARGET_PATTERN`.

    ``guid`` and ``path`` are the op's as read, its ``@GUID`` taken out of
    the path.

    A GUID wins over ``Property=`` and ``Template=``: both loaders log an
    error over an op that gives a GUID and either of them, then make one
    operation for each GUID and pass the other by (``GetXmlOperations`` in
    ``xml_operations.cc``)."""
    if not guid:
        return TARGET_SEARCH
    if reaches_other_assets(path) or not names_its_node(path):
        return TARGET_PATTERN
    return ""


def type_and_target(attrs: Dict[str, str], game: str = "") -> Tuple[str, str, str]:
    """``(kind, guid, path)``: the op's type, the GUID it names and the path
    from that asset, the ``@GUID`` taken out of it.

    ``attrs`` are the op's as the game reads them, through
    :func:`as_117_reads` for Anno 117.  ``guid`` is "" for an op that
    searches, and as written otherwise, spaces and commas and all -- see
    :func:`guids`.
    """
    kind, path, classic = type_and_path(attrs, game)
    # The two forms mix: ``<ModOp GUID="5977" Merge="EffectSource">`` puts
    # the op name and the path in the shorthand attribute and the asset in a
    # GUID of its own. Not stripped: a space the author left around a GUID is
    # part of what the loader goes looking for.
    guid = (attrs.get("GUID") or "") if (classic or kind) else ""

    # Anno 117 only: the loader there knows Property=, and Anno 1800's does
    # nothing with one. A GUID beside it is an error the loader says out loud
    # ("only one of GUID, Property or Template"), and it goes with the GUID.
    prop = next((attrs[name] for name in attrs if name.lower() == "property"), "")
    if prop.strip() and not guid and game == games.ANNO_117:
        path = property_path(prop.strip(), path)

    # Either form may carry the GUID inside the path, as "@1234/...".
    matched = AT_ASSET.match(path)
    if matched:
        guid = matched.group(1)
        path = matched.group(2) or ""
        # Anno 117 takes ``Add="@1001/"`` and ``Add="@1001"`` -- and the same
        # for merge -- as the asset's ``Values``, measured with its loader.
        # *GUID Explorer* adds to seventeen thousand assets that way.
        if game == games.ANNO_117 and path in ("", "/") and kind in (OP_ADD, OP_MERGE):
            path = "/Values"
    return kind, guid, path


def assets_named(attrs: Dict[str, str], game: str = "") -> List[str]:
    """The assets an op names, in ``GUID=`` or at the head of its path, as
    the game reads its attributes; none for an op that searches."""
    if game == games.ANNO_117:
        attrs = as_117_reads(attrs)
    return guids(type_and_target(attrs, game)[1])


# -- the payload ------------------------------------------------------------------
PAYLOAD_COPY = "copy"            # Content=
PAYLOAD_COMPUTED = "computed"    # <ModValue Insert=…>
PAYLOAD_FLAGS = "flags"          # <ModValue Merge=…> or Remove=

# What a mod writes where a value would go, when the value is not fixed:
# ``<ModValue Insert="self::node() * $gatecostfactor"/>``.
MOD_VALUE = "ModValue"
# And its way of naming one entry of a list inside a payload, by what it holds.
MOD_ITEM = "ModItem"
# Where a ``Content=`` op's body says the copy goes.
MOD_OP_CONTENT = "ModOpContent"

# A setting, named in a condition or a value: the mod's own by its bare name,
# or any loaded mod's as ``$mod-id.option`` -- the dot is part of the name, as
# it is in the loader's own pattern (:data:`LOADER_VARIABLE`).
SETTING = re.compile(r"\$([A-Za-z_][\w\-.]*)")


def has_computed(elements) -> bool:
    """Whether a payload holds a ``ModValue`` anywhere."""
    return any(
        node.tag == MOD_VALUE or node.find(".//" + MOD_VALUE) is not None
        for node in elements
        if isinstance(node.tag, str)
    )


def is_flags(node) -> bool:
    """A ``<ModValue Merge="A;B"/>`` or ``Remove=`` -- flags in a list, not a
    value worked out."""
    return node.get("Insert") is None and (
        node.get("Merge") is not None or node.get("Remove") is not None
    )


def only_flags(elements) -> bool:
    """Whether every ``ModValue`` in a payload merges or removes flags."""
    found = [
        node for element in elements if isinstance(element.tag, str)
        for node in element.iter(MOD_VALUE)
    ]
    return bool(found) and all(is_flags(node) for node in found)


def reads_a_setting(elements) -> bool:
    """Whether a payload works a value out from one of the mod's settings."""
    for element in elements:
        if not isinstance(getattr(element, "tag", None), str):
            continue
        for node in element.iter(MOD_VALUE):
            if any(SETTING.search(value or "") for value in node.attrib.values()):
                return True
    return False


def payload_kind(attrs: Dict[str, str], elements) -> str:
    """What an op's payload is: "" where it is literal XML, else
    :data:`PAYLOAD_COPY`, :data:`PAYLOAD_COMPUTED` or :data:`PAYLOAD_FLAGS`."""
    if "Content" in attrs:
        return PAYLOAD_COPY
    if has_computed(elements):
        return PAYLOAD_FLAGS if only_flags(elements) else PAYLOAD_COMPUTED
    return ""


# -- the condition ----------------------------------------------------------------
ASKS_DOCUMENT = "document"       # about the game's files as the mods have left them
ASKS_LOADOUT = "loadout"         # about which other mods are loaded
ASKS_SETTING = "setting"         # about one of the mod's own settings

# How Anno 117's loader reads a condition that names mods or settings: every
# ``#mod-id`` and ``$option`` in it becomes ``true()``, ``false()`` or the
# option's value, and what is left is evaluated as XPath, so they combine --
# ``#mod-a or #mod-b``, ``#mod-a and not(#mod-b)``. The pattern is the loader's
# own (``ReplaceStaticVariables``). Anno 1800's loader does none of this: it
# looks up everything after a leading ``#`` as one ModID, spaces and all.
LOADER_VARIABLE = re.compile(r"[$#][a-zA-Z_][\w\-.]*")
# What makes a condition a combination rather than a single name: an operator
# left over once the names are taken out. ``#vegetables-and-cheese`` is one
# name, ``and`` and all.
_COMBINES = re.compile(r"\b(?:and|or|not)\b|[()=<>]")


def combines(text: str) -> bool:
    """Whether a condition naming mods or settings joins more than one thing."""
    return bool(
        LOADER_VARIABLE.search(text)
        and _COMBINES.search(LOADER_VARIABLE.sub(" ", text))
    )


def asks_the_loadout(condition: str, game: str) -> bool:
    """Whether a condition asks which other mods are loaded: ``#ModID``.

    Anywhere in it, in Anno 117, where ``$setting and #mod-a`` asks it too.
    """
    text = (condition or "").strip().lstrip("!~ ")
    if text.startswith("#"):
        return True
    return game == games.ANNO_117 and combines(text) and any(
        name.startswith("#") for name in LOADER_VARIABLE.findall(text)
    )


def asks_a_setting(condition: str) -> bool:
    """Whether a condition reads one of the mod's own settings."""
    return bool(SETTING.search(condition or ""))


def asks(condition: str, game: str) -> str:
    """What a condition asks: "" for none, else :data:`ASKS_LOADOUT`,
    :data:`ASKS_SETTING` or :data:`ASKS_DOCUMENT`."""
    if not (condition or "").strip():
        return ""
    if asks_the_loadout(condition, game):
        return ASKS_LOADOUT
    if asks_a_setting(condition):
        return ASKS_SETTING
    return ASKS_DOCUMENT


# -- beside the patch -------------------------------------------------------------
# ``Skip`` switches an op off. Mods built for the Imya tweak tool ship
# hundreds of ops that way -- present in the file, off until something turns
# them on -- so reading them as things the mod does would be showing changes
# the game never makes.
def is_skipped(node_or_attrs) -> bool:
    """Whether an op carries ``Skip``, which switches it off whatever its
    value: ``Skip="0"`` and ``Skip=""`` too.

    Both loaders ask whether the attribute is there, before they look at
    what the node is (``GetXmlOperations`` in ``xml_operations.cc``), so an
    op is read as a group is -- and for a group it was measured, in both
    games.  The name as written: neither loader reads ``skip=``."""
    if node_or_attrs is None:
        return False
    return "Skip" in getattr(node_or_attrs, "attrib", node_or_attrs)


def allows_no_match(attrs: Dict[str, str]) -> bool:
    """``AllowNoMatch`` there at all: the loader asks only whether the
    attribute is present (``pugih::attrib``), so ``AllowNoMatch="0"`` allows
    it as well, as ``Skip="0"`` skips (:func:`is_skipped`)."""
    return any(key.lower() == "allownomatch" for key in attrs)


# What a <Group> may carry besides a condition and still be a group the Studio
# reads.  Measured against the loader for both games: a name is nothing to it,
# and any Skip at all switches the group off.  ``MaxRepeat`` is not a label but
# a loop: the reader runs it in rounds (:mod:`.modops`).
GROUP_LABELS = ("modopid", "name")
GROUP_KNOWN = (*GROUP_LABELS, "condition", "skip", "maxrepeat")


def name_of(attrs: Dict[str, str]) -> str:
    """The name a tweak tool finds an op by, ``ModOpID`` or ``Name``."""
    name = ""
    for attr, value in attrs.items():
        if attr.lower() in GROUP_LABELS:
            name = str(value)
    return name


# -- a value typed over -----------------------------------------------------------
# What an op's text holds that is no tag, blanked out at the same length, so
# that a tag is looked for only where it is one.
_NOT_A_TAG = re.compile(r"<!--.*?-->|<\?.*?\?>", re.S)


def typed_over(source: str, steps: Sequence[int], was: str, value: str) -> Optional[str]:
    """``source``, an op as written, with ``value`` typed over the ``was``
    of the field ``steps`` leads to: from the op down, each step the place
    of an element among the elements of the one above it.  ``None`` where
    that is no field the op writes down as ``was``.

    Typed over in the text, and not written out again from a reading of
    it: everything else in it -- its comments, its spacing, how its
    attributes are quoted -- is its author's and stays so.
    """
    if "<![CDATA[" in source:
        return None
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return None
    node = root
    for step in steps:
        inside = list(node)
        if not 0 <= step < len(inside):
            return None
        node = inside[step]
    if node is root or len(node) or (node.text or "").strip() != was:
        return None
    named = list(root.iter(node.tag))
    opening = re.compile(
        r"<%s(?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*\s*(/?)>" % re.escape(node.tag)
    )
    found = list(opening.finditer(_NOT_A_TAG.sub(lambda hit: " " * len(hit.group(0)), source)))
    if len(found) != len(named):
        return None
    held = found[next(at for at, one in enumerate(named) if one is node)]
    if held.group(1):
        # ``<Tag/>``: opened and closed round what is typed in it.
        return "%s>%s</%s>%s" % (
            source[held.start(): held.end() - 2].rstrip(), escape(value), node.tag,
            source[held.end():],
        )
    ends = source.index("<", held.end())
    text = source[held.end(): ends]
    lead = text[: len(text) - len(text.lstrip())] if text.strip() else ""
    trail = text[len(text.rstrip()):] if text.strip() else ""
    return source[: held.end()] + lead + escape(value) + trail + source[ends:]


def summary_typed_over(summary: str, was: str, value: str) -> str:
    """An op's line for the list, "replace Amount = 5000", with ``value``
    typed over the ``was`` it ends on."""
    return summary[: -len(was)] + value if summary.endswith("= %s" % was) else summary


# -- the description --------------------------------------------------------------
@dataclass(frozen=True)
class Described:
    """Which of an op's properties are worked out at load, and how.

    Each is "" where the op writes it down: assets and a node it names, a
    literal body, no condition.  The three flags are beside the patch: the
    op ships switched off, may match nothing, or carries a name a tweak tool
    finds it by.
    """

    target: str = ""
    payload: str = ""
    condition: str = ""
    skip: bool = False
    allow_no_match: bool = False
    name: str = ""
    # How the payload is applied: the op's type as the reader names it
    # (``add``, ``addNextSibling``, ``remove``, ``replace``, ``merge`` ...),
    # ``group`` for a group, "" for anything else.
    kind: str = ""
    # It answers to the mod's own options: a setting is read in its condition
    # or its value, or it carries the name a tweak tool switches it by.
    options: bool = False

    @property
    def pattern(self) -> bool:
        return bool(self.target)

    @property
    def computed(self) -> bool:
        return bool(self.payload)

    @property
    def conditional(self) -> bool:
        return bool(self.condition)

    @property
    def constant(self) -> bool:
        """Whether everything of its own is written down in it."""
        return not (self.target or self.payload or self.condition)
