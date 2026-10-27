"""Your patch exported to share, with your edits to its mod in it where they
can be.

At Play your edits to somebody else's mod go into its generated copy
(:mod:`core.edits`): their op switched off with ``Skip``, yours after it.
That cannot travel.  Whoever gets your patch has the mod as its author
shipped it, and nothing switches their op off there.  So an export of the
patch -- a folder elsewhere, a zip, an update on mod.io -- turns each edit it
can into an op of the patch's own, at the top of the patch, which loads after
the mod, and lists the rest as left out (docs/proposals/patches-proposal.md,
section 9 and phase 5).  Play writes the patch without them, as before.

An edit is turned where running yours after theirs comes to what running
yours in place of theirs does:

* an op of yours placed among theirs goes as it is, and runs after the
  whole of their mod rather than between two of its ops;
* an op of theirs typed over goes as yours, where yours is a ``replace`` or a
  ``merge`` aimed where theirs was -- the same path, at the same assets or at
  more of them -- asked under the same condition, with its value written
  down, and covering all theirs did: a replace covers a replace, a merge or
  an add at the same node, and a merge covers a merge whose every field it
  sets too;
* a value typed over inside what their op adds -- an asset, or entries of a
  list -- goes as a merge of that value once theirs has added it: on the
  asset by its GUID, or on the list's entry by what it holds
  (``Item[Product='1010200']``), never by its place in the list;
* where a tweak tool sets values in their op, only the values you changed
  go, as merges, and only where none of them is a value the tool sets.

An op that reads one of their mod's settings goes with the setting named in
full, ``$their-mod.hard`` for ``$hard``, which Anno 117 reads from anywhere
as their mod's (:func:`qualified`). Anno 1800 has no such names, so there it
is left out.

Everything else is left out, each with why: an op taken out or switched off,
which a patch that loads after cannot stop; one typed over as something
that adds or removes; one whose search, condition or value would work
something out from what their mod has left by then; a change to one of their
groups or ``<Include>`` lines; an op or a group a tweak tool switches on and
off.  Inside a group of theirs with a condition,
the op goes in a group with the same condition.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from lxml import etree

from . import disk, edits, games, generator, held, imya, modop, paths, splicing
from .changes import Loadout, Op
from .i18n import tr_noop

WHY_TAKEN_OUT = tr_noop("Taken out or switched off. A patch loads after their mod, so it cannot stop their op.")
WHY_GROUP = tr_noop("A change to one of their groups or included files, which a patch cannot make.")
WHY_SEVERAL = tr_noop("Several of their ops changed at once, which a patch cannot match one by one.")
WHY_REPEAT = tr_noop("It is in a group of theirs that runs more than once, which a patch cannot match.")
WHY_KIND = tr_noop("Yours adds or removes, and after theirs it would do that on top of what theirs did.")
WHY_THEIRS = tr_noop("Theirs adds or removes, and a patch cannot take that back.")
WHY_SEARCH = tr_noop("Yours is aimed elsewhere than theirs, so theirs would still change what yours leaves out.")
WHY_CONDITION = tr_noop("Yours runs under another condition than theirs, which a patch cannot match.")
WHY_ASKS = tr_noop("A condition looks at the game's files, which their mod has changed by the time a patch runs.")
WHY_SETTING = tr_noop("It reads a setting of their mod, which a patch does not have.")
WHY_WORKED_OUT = tr_noop("Yours works its value out, and after theirs it would start from what theirs left.")
WHY_TESTS = tr_noop("Its search tests what their op sets, so after theirs it may find other nodes.")
WHY_COVER = tr_noop("Yours does not set everything theirs sets, so some of theirs would stay.")
WHY_TOOL = tr_noop("A tweak tool switches their op, and an op in your patch would not follow it.")
WHY_KNOB = tr_noop("A tweak tool sets the value you changed, and your patch would set it over the tool's.")
WHY_UNREAD = tr_noop("It could not be read as XML.")
WHY_ENTRY = tr_noop("It changes a list entry a patch could find only by its place in the list.")
WHY_OFF = tr_noop("It is in a group of theirs that is switched off, so it does nothing in game.")
WHY_LATER = tr_noop("A later op of theirs changes the same thing, and yours would run after it instead of before.")

_PREDICATE = re.compile(r"\[([^\]]*)\]")
_NAME = re.compile(r"[\w.\-]+")
# A condition that asks only whether one asset is there, or is not.
_ASSET_ONLY = re.compile(r"\A\s*!?\s*@\s*(\d+)\s*\Z")
# What in a file of ModOps makes or names an asset: a GUID written as a
# value, which is how an asset added says which it is, and the GUID a
# removal is aimed at.
_GUID_VALUE = re.compile(r"<GUID>\s*(\d+)\s*</GUID>")
_REMOVAL = re.compile(r"<ModOp\b[^>]*\bType\s*=\s*[\"']remove[\"'][^>]*>", re.I)
_GUID_ATTRIBUTE = re.compile(r"\bGUID\s*=\s*[\"']([^\"']*)[\"']")
# How many groups and <Include> lines deep an edit is looked for: a file that
# includes itself goes no further.
_DEEPEST = 32


@dataclass
class About:
    """What is known of their mod besides a run of edits: the game, the knobs
    a tweak tool offers on its ops by the ``ModOpID`` each names, the assets
    it adds or removes by GUID, the base game's assets, by GUID, as
    elements (None where there is no game to ask), and its ModID, which a
    patch names its settings by (:func:`qualified`)."""

    game: str = games.ANNO_1800
    knobs: Dict[str, List[Any]] = field(default_factory=dict)
    made: frozenset = frozenset()
    base: Optional[Callable[[str], Any]] = None
    mod_id: str = ""


@dataclass
class Shared:
    """What an export of a patch makes of your edits to its mod: each run of
    them (as :attr:`core.edits.Edited.hunks` has it) turned, with ``"ops"``
    the ops written into the patch for it, or left out, with ``"why"``.
    ``shipped`` is the mod read with nothing of yours in it, and ``trouble``
    why the edits could not be worked out at all."""

    origin: str = ""
    turned: List[dict] = field(default_factory=list)
    left: List[dict] = field(default_factory=list)
    shipped: Optional[Loadout] = None
    trouble: str = ""


def work_out(loadout: Loadout, patch: str, folder, index) -> Optional[Shared]:
    """Your edits to the mod ``patch`` is made for, as an export of it carries
    them.  ``folder`` is that mod's copy in the library, as
    :func:`core.edits.work_out` takes it.  None for anything but a patch."""
    layer = loadout.layer(patch)
    if layer is None or not layer.patches:
        return None
    shared = Shared(origin=layer.patches)
    # The patch's own: the loadout's own mod's edits to the same mod hold in
    # this loadout alone (section 5.3 of the projects and loadouts proposal).
    if not edits.has_edits(loadout, layer.patches, patch):
        return shared
    folder = Path(folder)
    done = edits.work_out(loadout, layer.patches, folder, index, keeper=patch)
    if done.trouble:
        shared.trouble = done.trouble
        return shared
    shared.shipped = done.shipped
    theirs = loadout.layer(layer.patches)
    about = About(
        game=loadout.game, knobs=_knobs(folder), made=frozenset(_made(folder, done.files)),
        base=_base(index), mod_id=theirs.mod_id if theirs is not None else "",
    )
    order: Optional[List[Tuple[str, int, str]]] = None
    pending = list(done.hunks)
    while pending:
        hunk = pending.pop(0)
        inside = opened(hunk, folder, loadout.game)
        if inside is not None:
            pending[:0] = inside
            continue
        chain = [_head(text) for text in hunk["within"]]
        for edit in one_by_one(hunk):
            ops, why = turn(edit["skipped"], edit["inserted"], chain, about)
            if ops and not why:
                if order is None:
                    order = in_order(folder, loadout.game)
                if _overtaken(ops, _after(order, edit), loadout.game):
                    why = WHY_LATER
            if why:
                shared.left.append({**edit, "why": why})
            elif ops:
                shared.turned.append({**edit, "ops": ops})
    return shared


def one_by_one(hunk: dict) -> List[dict]:
    """A run of edits as each one: an op of yours with the op of theirs it
    stands in place of -- the same op, as :func:`core.held.same_op` knows it
    across versions, or else the next one left -- each op of theirs with
    none as taken out, and each of yours with none as placed among theirs.
    Two edits side by side come as one run, and each is turned or left out
    on its own.  Ops of theirs a writing of the mod wrote its own way come
    as one run that is no ModOp, and stay one."""
    skipped, inserted = list(hunk["skipped"]), list(hunk["inserted"])
    lines = list(hunk.get("lines") or [hunk["line"]] * len(skipped))
    offsets = list(hunk.get("offsets") or [0])
    whole = [splicing.element(text) for text in skipped + inserted]
    if any(found is None or not _is_op(found) for found in whole):
        return [hunk]
    partner: Dict[int, int] = {}
    for at, theirs in enumerate(skipped):
        match = next((other for other, yours in enumerate(inserted)
                      if other not in partner.values() and held.same_op(theirs, yours)), None)
        if match is not None:
            partner[at] = match
    alone = [other for other in range(len(inserted)) if other not in partner.values()]
    for at in range(len(skipped)):
        if at not in partner and alone:
            partner[at] = alone.pop(0)
    found: List[dict] = []
    for at, theirs in enumerate(skipped):
        mine = [inserted[partner[at]]] if at in partner else []
        found.append({**hunk, "line": lines[at], "offsets": [offsets[min(at, len(offsets) - 1)]],
                      "skipped": [theirs], "inserted": mine})
    found.extend({**hunk, "skipped": [], "inserted": [inserted[other]]} for other in alone)
    return found


def view(loadout: Loadout, patch: str, shared: Optional[Shared]) -> Loadout:
    """The patch as an export writes it: :func:`core.generator.for_mod`, with
    the ops of ``shared`` at the top of it, each saying what it stands for."""
    exported = generator.for_mod(loadout, patch)
    if shared is None or not shared.turned:
        return exported
    theirs = loadout.layer(shared.origin)
    name = theirs.label if theirs is not None else shared.origin
    first = min(
        [op.order for op in exported.ops]
        + [change.order for change in exported.changes.values()]
        + [entry.order for entry in exported.unread],
        default=0,
    )
    made = [(hunk, text) for hunk in shared.turned for text in hunk["ops"]]
    exported.ops = [
        Op(
            source=text, origin=patch, order=first - len(made) + number, authored=True,
            note=_note(hunk, name),
        )
        for number, (hunk, text) in enumerate(made)
    ] + exported.ops
    return exported


def _note(hunk: dict, name: str) -> str:
    where = {"mod": name, "file": hunk["file"].rsplit("/", 1)[-1], "line": hunk["line"]}
    if hunk["skipped"]:
        return "%s: in place of %s's ModOp at %s line %s, which runs first" % (
            splicing.MARK, where["mod"], where["file"], where["line"])
    return "%s: placed in %s at %s line %s, and run after the whole of it" % (
        splicing.MARK, where["mod"], where["file"], where["line"])


# -- one run of edits -----------------------------------------------------------------
def turn(skipped: List[str], inserted: List[str], chain: List[Dict[str, str]],
         about: About) -> Tuple[List[str], str]:
    """``(the ops a patch writes for a run of edits, "")``, or ``([], why)``
    where it cannot.  ``skipped`` are their ops the run switches off,
    ``inserted`` what goes in after them, and ``chain`` the attributes of
    each group or ``<Include>`` of theirs it stands in, outermost first."""
    game = about.game
    conditions = []
    for attrs in chain:
        if modop.is_skipped(attrs):
            return [], WHY_OFF
        if any(name.lower() == "maxrepeat" for name in attrs):
            return [], WHY_REPEAT
        named = modop.name_of(attrs)
        if named and named in about.knobs:
            # A group a tool names is one it switches on and off.
            return [], WHY_TOOL
        condition = _condition(attrs)
        if condition:
            why = _asks(condition, about)
            if why:
                return [], why
            conditions.append(qualified(condition, about) or condition)
    if not skipped:
        placed = []
        for text in inserted:
            yours = splicing.element(text)
            if yours is None:
                return [], WHY_UNREAD
            # Its own conditions, and those of everything in a group of yours,
            # are asked after their mod as a group of theirs is.
            asked = [_condition(_attrs(node, game)) for node in _heads(yours)]
            for condition in asked:
                why = _asks(condition, about) if condition else ""
                if why:
                    return [], why
            if any(modop.asks_a_setting(condition) for condition in asked) or modop.reads_a_setting([yours]):
                in_full = _qualified_op(text, about)
                if in_full is None:
                    return [], WHY_SETTING
                text = in_full
            placed.append(text)
        return [_wrapped(text, conditions) for text in placed], ""
    if not inserted:
        return [], WHY_TAKEN_OUT
    whole = [splicing.element(text) for text in skipped + inserted]
    if any(found is not None and not _is_op(found) for found in whole):
        return [], WHY_GROUP
    if len(skipped) != len(inserted):
        return [], WHY_SEVERAL
    written: List[str] = []
    for theirs_text, yours_text in zip(skipped, inserted, strict=True):
        theirs, yours = splicing.element(theirs_text), splicing.element(yours_text)
        if theirs is None or yours is None:
            return [], WHY_UNREAD
        if not (_is_op(theirs) and _is_op(yours)):
            return [], WHY_GROUP
        if splicing.same(theirs_text, yours_text):
            # Written another way, and theirs: it runs in their mod.
            continue
        ops, why = _instead(theirs, yours, yours_text, about)
        if why:
            return [], why
        written.extend(_wrapped(text, conditions) for text in ops)
    return written, ""


def _instead(theirs, yours, yours_text: str, about: About) -> Tuple[List[str], str]:
    """``(what a patch writes, "")`` where running it after ``theirs`` comes
    to running ``yours`` in its place -- ``yours`` as it is, or the values
    you changed as merges -- and ``([], why)`` where it does not."""
    game = about.game
    first, second = _attrs(theirs, game), _attrs(yours, game)
    knobs = about.knobs.get(modop.name_of(first), []) if modop.name_of(first) else []
    if any(knob.is_toggle for knob in knobs):
        return [], WHY_TOOL
    condition = _condition(second)
    if condition.strip() != _condition(first).strip():
        return [], WHY_CONDITION
    if condition:
        why = _asks(condition, about)
        if why:
            return [], why
    if modop.payload_kind(second, _children(yours)):
        return [], WHY_WORKED_OUT
    # Read by a patch, a setting of theirs is named in full: whatever the op
    # comes to below, it asks the same of their mod's settings as theirs did.
    asked = [qualified(condition, about) or condition] if modop.asks_a_setting(condition) else []
    if asked:
        yours_text = _qualified_op(yours_text, about) or yours_text
    kind, guid, path = modop.type_and_target(second, game)
    was, guid_was, path_was = modop.type_and_target(first, game)
    if kind in _ADDS and was == kind and (guid, path) == (guid_was, path_was):
        ops, why, touched = _narrowed(theirs, yours, kind, guid, path, about)
    else:
        why = _covers(theirs, yours, first, game)
        if why or not knobs:
            return ([], why) if why else ([yours_text], "")
        # The whole of yours would set every value the tool sets as well:
        # only what you changed goes.
        ops, why, touched = _narrowed(theirs, yours, kind, guid, path, about)
        if why == WHY_KIND:
            why = WHY_TOOL
    if why:
        return [], why
    if any(_clashes(theirs, knob, leaf, where) for knob in knobs for leaf, where in touched):
        return [], WHY_KNOB
    # The values written as merges say nothing of a condition: theirs held
    # under it, and so do they.
    return [_wrapped(text, asked) for text in ops], ""


def _covers(theirs, yours, first: Dict[str, str], game: str) -> str:
    """Why ``yours``, a replace or a merge, run after ``theirs`` does not
    do all theirs did; "" where it does."""
    kind, guid, path = modop.type_and_target(_attrs(yours, game), game)
    was, guid_was, path_was = modop.type_and_target(first, game)
    if kind not in (modop.OP_REPLACE, modop.OP_MERGE):
        return WHY_KIND
    if was not in (modop.OP_REPLACE, modop.OP_MERGE, modop.OP_ADD):
        return WHY_THEIRS
    assets, assets_were = set(modop.guids(guid)), set(modop.guids(guid_was))
    if path != path_was or bool(assets) != bool(assets_were) or not assets_were <= assets:
        return WHY_SEARCH
    if _tests(path_was, theirs) or (was == modop.OP_REPLACE and not _keeps_its_name(path_was, theirs)):
        return WHY_TESTS
    if kind == modop.OP_REPLACE:
        return ""
    if was != modop.OP_MERGE or "Content" in first:
        return WHY_COVER
    fields, fields_were = _fields(_children(yours)), _fields(_children(theirs))
    if fields is None or fields_were is None or not fields_were <= fields:
        return WHY_COVER
    return ""


# What adds, as against what sets: an op of these typed over is turned only
# where what changed is a value inside what it adds.
_ADDS = (modop.OP_ADD, modop.OP_ADD_NEXT, modop.OP_ADD_PREV)


def _narrowed(theirs, yours, kind: str, guid: str, path: str, about: About) -> Tuple[List[str], str, list]:
    """``(merges, "", [(their value, the path it is merged at)])``: the values
    ``yours`` has otherwise than ``theirs``, each merged where theirs leaves
    it -- one merge for each node a value changed under -- or ``([], why,
    [])`` where that cannot be said.  Only where nothing but values changed.
    The path is worked out on their op, so a list entry is found by what
    it holds once theirs has run, whatever value of it you changed."""
    changed = _changed(_children(theirs), _children(yours))
    # Nothing found changed is a change to text no element holds: what an
    # add puts at the end of a value, ``;Colony01``, which a merge cannot say.
    if not changed or (theirs.text or "").strip() != (yours.text or "").strip():
        return [], WHY_KIND, []
    # Each op written, by (its type, GUID and path): the values it sets, and
    # (the node its path starts at, the rest of it, the node it finds) in
    # their op's tree.
    written: Dict[Tuple[str, str, str], List[tuple]] = {}
    found: Dict[Tuple[str, str, str], Tuple[Any, str, Any]] = {}
    touched = []
    for leaf, mine in changed:
        number, where, why, (top, rest) = _aim_at(leaf, theirs, kind, guid, path, about)
        if why:
            return [], why, []
        aimed = leaf.getparent()
        if leaf.tag == paths.LIST_ENTRY:
            # The loader merges no ``<Item>`` into another, as it does a
            # field: one holding a value is replaced where it stands.
            made = paths.path_for(leaf.getparent(), leaf)
            if made.positional:
                return [], WHY_ENTRY, []
            where, rest, aimed = where.rstrip("/") + made.path, rest + made.path, leaf
            key = ("replace", number, where)
        else:
            key = ("merge", number, where)
        written.setdefault(key, []).append((leaf, mine))
        found.setdefault(key, (top, rest, aimed))
        touched.append((leaf, where))
    # The ops run one after another, each finding its node by what it holds
    # then: one that changes what another finds an entry by runs after it.
    twin = deepcopy(theirs)
    twins = dict(zip(map(id, theirs.iter()), twin.iter(), strict=True))

    def finds(key) -> bool:
        top, rest, aimed = found[key]
        return paths.resolve(twins[id(top)], rest) == [twins[id(aimed)]]

    def sets(key, values: List[str]) -> None:
        for (leaf, _mine), value in zip(written[key], values, strict=True):
            twins[id(leaf)].text = value

    pending, ops = sorted(written, key=lambda key: key[0] == "replace"), []
    while pending:
        ready = None
        for key in pending:
            if not finds(key):
                continue
            was = [twins[id(leaf)].text for leaf, _mine in written[key]]
            sets(key, [mine.text for _leaf, mine in written[key]])
            if all(finds(other) for other in pending if other != key):
                ready = key
                break
            sets(key, was)
        if ready is None:
            return [], WHY_ENTRY, []
        pending.remove(ready)
        ops.append('<ModOp Type="%s" GUID="%s" Path="%s">%s</ModOp>' % (
            ready[0], _attribute(ready[1]), _attribute(ready[2]),
            "".join(etree.tostring(mine, encoding="unicode", with_tail=False) for _leaf, mine in written[ready])))
    return ops, "", touched


def _aim_at(leaf, op, kind: str, guid: str, path: str, about: About) -> Tuple[str, str, str, tuple]:
    """``(the GUID, the path, "", (top, rest))`` a merge setting ``leaf`` is
    aimed at once ``op`` has run -- the path ending in ``rest``, which finds
    the node from ``top`` in ``op``'s tree -- or ``("", "", why, (None,
    ""))``."""
    parent = leaf.getparent()
    nowhere = (None, "")
    asset = next((node for node in leaf.iterancestors() if node.tag == "Asset"), None)
    if asset is not None:
        # In an asset theirs adds: the asset is theirs, every entry of it.
        number = (asset.findtext("Values/Standard/GUID") or "").strip()
        if parent is asset or leaf is asset.find("Values/Standard/GUID"):
            return "", "", WHY_KIND, nowhere
        made = paths.path_for(asset, parent)
        if not number or made.positional or not made.path.startswith("/Values"):
            return "", "", WHY_ENTRY, nowhere
        return number, made.path, "", (asset, made.path)
    if not modop.guids(guid):
        return "", "", WHY_ENTRY, nowhere
    top = op
    if kind == modop.OP_REPLACE:
        # What theirs put in the place of the node its path names.
        top = next((node for node in (parent, *parent.iterancestors()) if node.getparent() is op), None)
        if top is None:
            return "", "", WHY_KIND, nowhere
        made = paths.path_for(top, parent)
        where = path.rstrip("/") + made.path
    elif kind == modop.OP_MERGE:
        made = paths.path_for(op, parent)
        where = path.rstrip("/") + made.path
    else:
        # Entries theirs adds beside what the asset already has: found by what
        # each holds, which the base game's own entries must not hold too.
        anchor = path if kind == modop.OP_ADD else _parent_path(path)
        made = paths.path_for(op, parent, keyed=True)
        where = (anchor or "").rstrip("/") + made.path
        if anchor is None or not _new_there(guid, anchor, where, about):
            return "", "", WHY_ENTRY, nowhere
    if made.positional:
        return "", "", WHY_ENTRY, nowhere
    return guid, where, "", (top, made.path)


def _new_there(guid: str, anchor: str, where: str, about: About) -> bool:
    """Whether, in each asset ``guid`` names as the base game has it, the
    node ``anchor`` names is there once and nothing is at ``where`` yet."""
    if about.base is None:
        return False
    for number in modop.guids(guid):
        try:
            root = about.base(number)
        except Exception:  # noqa: BLE001 - an index that cannot answer knows nothing
            root = None
        if root is None or len(paths.resolve(root, anchor)) != 1 or paths.resolve(root, where):
            return False
    return True


def _parent_path(path: str) -> Optional[str]:
    """``path`` less its last step, predicates and all; None for a path of
    one step."""
    depth = 0
    for at in range(len(path.rstrip("/")) - 1, -1, -1):
        char = path[at]
        if char == "]":
            depth += 1
        elif char == "[":
            depth -= 1
        elif char == "/" and depth == 0:
            return path[:at] if at else None
    return None


def _clashes(op, knob, leaf, where: str) -> bool:
    """Whether a knob of a tweak tool sets ``leaf``, holds it, or sets what
    the entry it is in is found by."""
    # The elements themselves, not their id(): lxml frees a proxy no one
    # holds, and the next one made can take its address.
    above = set(leaf.iterancestors())
    for node in imya.reached(op, knob):
        if node is leaf or node in above:
            return True
        holder = node.getparent()
        if holder is not None and holder in above and "[%s=" % node.tag in where:
            return True
    return False


def _changed(ones: list, others: list) -> Optional[List[tuple]]:
    """``(their element, yours)`` for each value ``others`` holds otherwise
    than ``ones``; None where more than values differ."""
    if len(ones) != len(others):
        return None
    found: List[tuple] = []
    for one, other in zip(ones, others, strict=True):
        if (one.tag != other.tag or dict(one.attrib) != dict(other.attrib)
                or (one.tail or "").strip() != (other.tail or "").strip()):
            return None
        inside, inside_other = _children(one), _children(other)
        if inside or inside_other:
            deeper = _changed(inside, inside_other)
            if deeper is None:
                return None
            found.extend(deeper)
        elif (one.text or "").strip() != (other.text or "").strip():
            found.append((one, other))
    return found


def _attribute(value: str) -> str:
    return escape(value, {'"': "&quot;"})


# -- reading an op ----------------------------------------------------------------------
def _is_op(element) -> bool:
    return isinstance(element.tag, str) and element.tag.lower() == "modop"


def _attrs(element, game: str) -> Dict[str, str]:
    attrs = {str(name): str(value) for name, value in element.attrib.items()}
    return modop.as_117_reads(attrs) if game == games.ANNO_117 else attrs


def _condition(attrs: Dict[str, str]) -> str:
    return next((value for name, value in attrs.items() if name.lower() == "condition"), "")


def _asks(condition: str, about: About) -> str:
    """Why a condition asked after their mod is not the one asked in it:
    one that reads the game's files may be answered otherwise once their mod
    has run -- unless all it asks is whether an asset is there, and their
    mod neither adds nor removes that asset. One that reads their mod's
    settings is asked the same only where the patch can name them in full."""
    if modop.asks_a_setting(condition) and qualified(condition, about) is None:
        return WHY_SETTING
    if modop.asks(condition, about.game) != modop.ASKS_DOCUMENT:
        return ""
    asset = _ASSET_ONLY.match(condition)
    if asset is not None and asset.group(1) not in about.made:
        return ""
    return WHY_ASKS


# Where a ModOp names a setting: a condition, on an op or a group, and the
# value a ``<ModValue>`` inserts.
_NAMES_SETTINGS = re.compile(r"(\b(?:Condition|Insert)\s*=\s*)(?:" r'"([^"]*)"' r"|'([^']*)')", re.I)


def qualified(text: str, about: About) -> Optional[str]:
    """``text`` -- a condition, or what a ``ModValue`` inserts -- with each
    of their mod's settings it names by its bare name named in full.

    Anno 117's loader reads a bare ``$range`` as the option of the mod whose
    op it is, and ``$their-mod.range`` as that mod's wherever the op is
    (``ReplaceStaticVariables``). So an op of theirs moved into a patch asks
    the same thing only as ``$their-mod.range``. None where that cannot be
    said: Anno 1800's loader has no such names, and a mod with no ModID
    cannot be named. ``text`` naming no setting by its bare name comes back
    as it is."""
    if not any("." not in name for name in modop.SETTING.findall(text or "")):
        return text
    if about.game != games.ANNO_117 or not about.mod_id.strip():
        return None
    prefix = about.mod_id.strip()
    return modop.SETTING.sub(
        lambda found: found.group(0) if "." in found.group(1) else "$%s.%s" % (prefix, found.group(1)),
        text,
    )


def _qualified_op(text: str, about: About) -> Optional[str]:
    """An op as written, with every setting its conditions and inserted
    values name by its bare name named in full (:func:`qualified`); None
    where one cannot be."""
    refused = False

    def named(found) -> str:
        nonlocal refused
        quote = '"' if found.group(2) is not None else "'"
        value = found.group(2) if found.group(2) is not None else found.group(3)
        said = qualified(value, about)
        if said is None:
            refused = True
            return found.group(0)
        return "%s%s%s%s" % (found.group(1), quote, said, quote)

    written = _NAMES_SETTINGS.sub(named, text)
    return None if refused else written


def _children(element) -> list:
    return [child for child in element if isinstance(child.tag, str)]


def _heads(element) -> list:
    """An op, or a group and every op, group and ``<Include>`` in it: what
    can carry a condition."""
    return [node for node in element.iter()
            if isinstance(node.tag, str) and node.tag.lower() in ("modop", "group", "include")]


def _tests(path: str, theirs) -> bool:
    """Whether the path's search tests a field their op sets."""
    asked = " ".join(_PREDICATE.findall(path))
    if not asked:
        return False
    named = {node.tag for node in theirs.iter() if isinstance(node.tag, str)} - {theirs.tag}
    return bool(named & set(_NAME.findall(asked)))


def _keeps_its_name(path: str, theirs) -> bool:
    """Whether what their replace puts at the path is still found by it."""
    last = _PREDICATE.sub("", path.rstrip("/").rsplit("/", 1)[-1]).strip()
    if not _NAME.fullmatch(last):
        return False
    return last in {child.tag for child in _children(theirs)}


def _fields(elements, above: tuple = ()) -> Optional[set]:
    """Every field a merge's payload sets, by its path from the top; None
    where two siblings go by the same name, and which is which is the
    loader's to say."""
    found: set = set()
    seen = []
    for node in elements:
        key = node.tag if node.tag != modop.MOD_ITEM else (node.tag, tuple(sorted(node.attrib.items())))
        if key in seen:
            return None
        seen.append(key)
        inside = [child for child in _children(node) if child.tag != modop.MOD_VALUE]
        if not inside:
            found.add((*above, key))
            continue
        deeper = _fields(inside, (*above, key))
        if deeper is None:
            return None
        found |= deeper
    return found


def _wrapped(text: str, conditions: List[str]) -> str:
    for condition in reversed(conditions):
        text = '<Group Condition="%s">\n%s\n</Group>' % (_attribute(condition), text)
    return text


# -- where their op stands --------------------------------------------------------------
def _head(text: str) -> Dict[str, str]:
    """The attributes of a group's or an ``<Include>``'s own tag."""
    text = text.rstrip()
    closed = text if text.endswith("/>") else text[:-1] + "/>"
    found = splicing.element(closed)
    return {str(name): str(value) for name, value in found.attrib.items()} if found is not None else {}


def _made(folder: Path, files) -> set:
    """The assets their mod may add or remove, by GUID: every GUID one of
    its files of ModOps writes as a value, and every one it removes from."""
    found: set = set()
    for rel in files:
        try:
            text = splicing.decode((folder / rel).read_bytes())[0]
        except OSError:
            continue
        found.update(_GUID_VALUE.findall(text))
        for removal in _REMOVAL.findall(text):
            for named in _GUID_ATTRIBUTE.findall(removal):
                found.update(modop.guids(named))
    return found


def _knobs(folder: Path) -> Dict[str, List[Any]]:
    """The knobs a tweak tool offers on the mod's ops, by the ``ModOpID``
    each names."""
    try:
        tweaks = imya.read(folder)
    except OSError:
        return {}
    found: Dict[str, List[Any]] = {}
    for knob in tweaks.exposes if tweaks else ():
        found.setdefault(knob.mod_op_id, []).append(knob)
    return found


def _base(index) -> Optional[Callable[[str], Any]]:
    """The base game's assets by GUID, as an index of them has them."""
    return getattr(index, "asset_element", None) if index is not None else None


def opened(hunk: dict, folder: Path, game: str) -> Optional[List[dict]]:
    """An edit inside a file of theirs that an ``<Include>`` names, which a
    writing of the mod wrote out in full -- their ``<Include>`` skipped and
    the file's ops, one of them yours, put after it -- as the edits it is
    to that file's own ops, each in the ``<Include>`` and in whatever it
    stands in.  Several ``<Include>`` lines side by side are written out as
    one run, and are opened together.  None for any other run of edits, and
    where a file is not there to read."""
    skipped = hunk["skipped"]
    if not skipped or len(hunk["within"]) > _DEEPEST:
        return None
    spec = games.get(game)
    patch = Path(spec.asset_relpath).as_posix()
    # Each file's ModOps, one after another as the run has them: where each
    # starts in the whole, its name, its text, and where its ModOps start.
    parts: List[Tuple[int, str, str, int, str]] = []
    whole = "<ModOps>"
    for line in skipped:
        include = splicing.element(line)
        if include is None or not isinstance(include.tag, str) or include.tag.lower() != "include":
            return None
        named = generator.file_named(line, "" if hunk["file"].lower() == patch else hunk["file"], spec)
        if not named:
            return None
        named = disk.as_spelled(folder, named)
        try:
            text = splicing.decode((folder / named).read_bytes())[0]
        except OSError:
            return None
        scanned = splicing.scan(text)
        if scanned is None:
            return None
        closing = text.rfind("</")
        body = text[scanned.head_end:closing if closing > scanned.head_end else len(text)]
        parts.append((len(whole), named, text, scanned.head_end, line))
        whole += body
    whole += "</ModOps>"
    theirs = splicing.scan(whole)
    inserted = _unwrapped(hunk["inserted"], _head(skipped[0])) if len(skipped) == 1 else hunk["inserted"]
    ours = splicing.scan("<ModOps>%s</ModOps>" % "".join(inserted))
    if theirs is None or ours is None:
        return None

    def part_of(offset: int):
        return next(part for part in reversed(parts) if part[0] <= offset)

    def in_file(offset: int) -> Tuple[str, int]:
        begins, _named, text, top, _line = part_of(offset)
        local = offset - begins + top
        return text, local

    found = []
    for inner in splicing.hunks(theirs, ours):
        nodes = [splice.node for splice in inner if splice.kind == "skip" and splice.node is not None]
        first = nodes[0].start if nodes else inner[0].at
        _begins, named, _text, _top, line = part_of(first)
        found.append({
            "file": named,
            "line": splicing.line_of(*in_file(first)),
            "within": [*hunk["within"], line,
                       *(whole[group.start:group.head_end] for group in splicing.around(theirs, first))],
            "skipped": [whole[node.start:node.end] for node in nodes],
            "lines": [splicing.line_of(*in_file(node.start)) for node in nodes],
            "offsets": [in_file(node.start)[1] for node in nodes] or [in_file(first)[1]],
            "inserted": [splice.text for splice in inner if splice.kind == "insert"],
        })
    return found


def _unwrapped(inserted: List[str], include: Dict[str, str]) -> List[str]:
    """What a writing put in place of an ``<Include>``, less the group it
    writes an ``<Include>``'s condition as."""
    asked = {name: value for name, value in include.items() if name.lower() != "file"}
    if len(inserted) != 1 or not asked:
        return inserted
    scanned = splicing.scan("<ModOps>%s</ModOps>" % inserted[0])
    if scanned is None or len(scanned.nodes) != 1 or not scanned.nodes[0].is_group:
        return inserted
    group = scanned.nodes[0]
    if _head(scanned.text[group.start:group.head_end]) != asked:
        return inserted
    return [scanned.text[node.start:node.end] for node in group.children]


# -- what runs after it -----------------------------------------------------------------


def in_order(folder: Path, game: str) -> List[Tuple[str, int, str]]:
    """Every op of their mod that runs, in the order it runs, each with the
    file it is in and where: ``(file, offset, text)``.  Each ``<Include>``
    is followed into the file it names, where it stands."""
    spec = games.get(game)
    found: List[Tuple[str, int, str]] = []

    def walk(rel: str, seen: frozenset) -> None:
        if rel in seen or len(seen) > _DEEPEST:
            return
        try:
            text = splicing.decode((folder / rel).read_bytes())[0]
        except OSError:
            return
        scanned = splicing.scan(text)
        if scanned is None:
            return

        def each(nodes) -> None:
            for node in nodes:
                if splicing.is_skipped(text, node):
                    continue
                if node.is_group:
                    each(node.children)
                elif node.tag.lower() == "include":
                    named = generator.file_named(text[node.start:node.end], rel, spec)
                    if named:
                        walk(disk.as_spelled(folder, named), seen | {rel})
                else:
                    found.append((rel, node.start, text[node.start:node.end]))

        each(scanned.nodes)

    walk(disk.as_spelled(folder, Path(spec.asset_relpath).as_posix()), frozenset())
    return found


def _after(order: List[Tuple[str, int, str]], edit: dict) -> List[str]:
    """Their ops that run after the place of ``edit``: after the op of
    theirs it is made to, or after where an op of yours was put."""
    offset = (edit.get("offsets") or [0])[0]
    at = next((number for number, (rel, start, _text) in enumerate(order)
               if rel == edit["file"] and start >= offset), None)
    if at is None:
        at = 1 + max((number for number, (rel, _start, _text) in enumerate(order) if rel == edit["file"]),
                     default=len(order) - 1)
    elif edit["skipped"]:
        at += 1
    return [text for _rel, _start, text in order[at:]]


def _overtaken(ops: List[str], later: List[str], game: str) -> bool:
    """Whether one of their ``later`` ops meets one of ``ops``: touches a node
    one of ``ops`` touches, or one above it or below it (:func:`_touches`)."""
    mine = [touch for text in ops for touch in _touches(text, game)]
    return any(_meet(one, other) for text in later for other in _touches(text, game) for one in mine)


@dataclass(frozen=True)
class Touch:
    """A node an op changes, or adds: in the assets ``guids`` names (every
    asset where it names none), down ``steps`` from the asset -- each step a
    name and what its predicate says the node holds, ``{field: value}``, or
    None where it says nothing that can be read.  ``floating``: the steps
    may start anywhere in the asset, as a ``//`` search's do.  No steps is
    the whole asset.  ``deep``: everything in the node is touched too, as a
    replace's is, where a merge touches a value and nothing in it.
    ``inside``: the names of what is in it, where the op says (None where
    it does not), which is all a search could find there."""

    guids: frozenset
    steps: Tuple[Tuple[str, Optional[Dict[str, str]]], ...]
    floating: bool = False
    deep: bool = True
    inside: Optional[frozenset] = None


# Anything an op touches: what an op that cannot be read is taken to touch.
_EVERYTHING = Touch(frozenset(), (), True)
# A predicate that says what one field holds: ``Product='1010200'``, with the
# field as deep as ``VectorElement/InheritedIndex``.
_HOLDS = re.compile(r"""\A\s*([\w.\-]+(?:/[\w.\-]+)*)\s*=\s*(?:'([^']*)'|"([^"]*)")\s*\Z""")
_STEP = re.compile(r"\A([\w.\-]+|\*)((?:\[[^\[\]]*\])*)\Z")


def _touches(text: str, game: str) -> List[Touch]:
    """Every node an op, or each op in a group, changes or adds.

    * a ``replace`` or a ``remove``: the node its path names, and everything
      in it;
    * a ``merge``: each value its payload sets, down to it -- and down to a
      list entry, every entry of that list, since the loader's way with an
      ``<Item>`` merged is not a field's;
    * an ``add``: each node it puts in the node its path names, an entry
      with what it holds; ``addNextSibling`` and ``addPrevSibling`` the same
      beside it;
    * whatever adds a whole asset: that asset.

    An op it cannot read touches everything."""
    element = splicing.element(text)
    if element is None:
        return [_EVERYTHING]
    ops = [node for node in element.iter() if isinstance(node.tag, str) and node.tag.lower() == "modop"]
    found: List[Touch] = []
    for op in ops:
        found.extend(_touched_by(op, game))
    return found if ops else [_EVERYTHING]


def _touched_by(op, game: str) -> List[Touch]:
    attrs = _attrs(op, game)
    kind, guid, path = modop.type_and_target(attrs, game)
    named = frozenset(modop.guids(guid))
    payload = _children(op)
    assets = [node for node in payload if node.tag == "Asset"]
    if assets:
        made = frozenset((node.findtext("Values/Standard/GUID") or "").strip() for node in assets)
        return [Touch(made - {""}, ())] if "" not in made else [_EVERYTHING]
    if kind in (modop.OP_ADD_NEXT, modop.OP_ADD_PREV):
        path = _parent_path(path) or ""
    parsed = _steps(path)
    if parsed is None or not kind:
        return [Touch(named, (), not named)]
    steps, floating = parsed
    if kind == modop.OP_REPLACE:
        return [Touch(named, steps, floating, inside=_names_in(payload))]
    if kind == modop.OP_REMOVE or not payload:
        return [Touch(named, steps, floating)]
    if kind == modop.OP_MERGE:
        return [Touch(named, steps + below, floating, deep=below[-1][0] == paths.LIST_ENTRY)
                for below in _set_by(payload)]
    # What an add puts in: each node, an entry by what it holds.
    return [Touch(named, (*steps, (node.tag, _held(node))), floating, inside=_names_in(_children(node)))
            for node in payload]


def _names_in(nodes: list) -> frozenset:
    return frozenset(inner.tag for node in nodes for inner in node.iter() if isinstance(inner.tag, str))


def _set_by(payload: list, above: tuple = ()) -> List[tuple]:
    """The steps down to each value a merge's payload sets."""
    found: List[tuple] = []
    for node in payload:
        if node.tag == paths.LIST_ENTRY:
            found.append((*above, (node.tag, None)))
            continue
        inside = [child for child in _children(node) if child.tag != modop.MOD_VALUE]
        step = (*above, (node.tag, {}))
        found.extend(_set_by(inside, step) if inside else [step])
    return found


def _held(node) -> Dict[str, str]:
    """What an entry holds, by field: every value directly in it, and in
    what is directly in it."""
    held: Dict[str, str] = {}
    for child in _children(node):
        inside = _children(child)
        if not inside:
            held[child.tag] = (child.text or "").strip()
        for grandchild in inside:
            if not _children(grandchild):
                held["%s/%s" % (child.tag, grandchild.tag)] = (grandchild.text or "").strip()
    return held


def _steps(path: str) -> Optional[Tuple[tuple, bool]]:
    """``(the steps, floating)`` of an op's path; None for one that cannot
    be read as steps -- a function, a union, a way back up."""
    text = path.strip()
    floating = text.startswith("//")
    parts = _split(text.strip("/"))
    if parts is None:
        return None
    steps = []
    for part in parts:
        if part == "":
            # ``a//b``: anything may stand between.
            return None
        matched = _STEP.match(part)
        if matched is None:
            return None
        said = re.findall(r"\[([^\[\]]*)\]", matched.group(2))
        holds: Optional[Dict[str, str]] = {}
        for predicate in said:
            pair = _HOLDS.match(predicate)
            if pair is None or holds is None:
                holds = None
                continue
            holds[pair.group(1)] = pair.group(2) if pair.group(2) is not None else pair.group(3)
        steps.append((matched.group(1), holds))
    if floating and steps and steps[0][0] == "Asset":
        # A search for assets: what follows is in the asset.
        steps = steps[1:]
    elif floating and any(name == "Asset" for name, _holds in steps):
        return None
    return tuple(steps), floating


def _split(text: str) -> Optional[List[str]]:
    """``text`` cut at each ``/`` outside a predicate."""
    parts, depth, run = [], 0, ""
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        if char == "/" and depth == 0:
            parts.append(run)
            run = ""
            continue
        if depth == 0 and (char in "|()" or (char == "." and run in ("", "."))):
            return None
        run += char
    parts.append(run)
    return parts if text else []


def _meet(one: Touch, other: Touch) -> bool:
    """Whether two touches may be of one node, or one in the other."""
    if one.guids and other.guids and not one.guids & other.guids:
        return False
    if not one.floating and not other.floating:
        return _along(one, one.steps, other, other.steps)
    if one.floating and other.floating:
        return any(_along(one, one.steps[at:], other, other.steps) for at in range(len(one.steps))) or any(
            _along(one, one.steps, other, other.steps[at:]) for at in range(len(other.steps))
        ) or not one.steps or not other.steps
    fixed, loose = (other, one) if one.floating else (one, other)
    # A search may start at any step of the other's path, or under its end
    # where the other touches all that is there and it finds its first step.
    beneath = fixed.deep and (not loose.steps or fixed.inside is None or loose.steps[0][0] in fixed.inside)
    return beneath or any(_along(fixed, fixed.steps[at:], loose, loose.steps) for at in range(len(fixed.steps)))


def _along(one: Touch, steps: tuple, other: Touch, other_steps: tuple) -> bool:
    """Whether ``steps`` and ``other_steps``, from the same node, may lead
    to one node, or one to a node in the other's -- which only one that
    touches all of it has."""
    # The steps the two share: the shorter one's, the rest below it.
    for (name, holds), (other_name, other_holds) in zip(steps, other_steps, strict=False):
        if name != other_name and "*" not in (name, other_name):
            return False
        if holds and other_holds and any(
            field in other_holds and other_holds[field] != value for field, value in holds.items()
        ):
            return False
    if len(steps) < len(other_steps):
        return one.deep
    if len(other_steps) < len(steps):
        return other.deep
    return True


