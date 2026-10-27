"""Assets created under a GUID something else already has.

The game finds an asset by its GUID, and when two share one it finds only
the first: the loader's own look-up takes the first match (measured for both
games), so every later op aimed at that GUID edits the first, and whatever
refers to it gets the first. The second is in the file and nothing can reach
it.

The first, that is, where both are in one of the file's top-level groups.
The loader's look-up starts with the top-level group its last look-up landed
in, so of two in different groups it finds whichever the op before it was
nearer (measured in both games; :meth:`core.reader.Translator._stands_apart`).
Which one that is, is not said for those (:attr:`Repeat.apart`).

The census found it three ways, none of them on purpose:

- **one mod, twice.** *Local Department for Arctic Enbesa Horai* gives two
  different decree buffs the same GUID, one in each of two compatibility
  sections that both always run.
- **two mods by one author.** *Cultural Expeditions* makes a research
  balancing asset under the GUID its bundled botanical sub-mod gives a trip's
  name.
- **two mods by two.** *Trade Union Rebalance* and *Extended Agriculture
  Modules* add 17 of the same GUIDs.

And two that do no harm. Kurila's shared sub-mods each carry the same
description asset -- *Recipe_Desc_Timber* in three sawmill pieces -- so the
same GUID comes in several times, each copy the same as the others: which
one the game finds makes no difference, and that is said rather than warned
about. And *A_Modified_Ornaments_Tab* adds a placeholder and then
its real asset under one GUID, and removes all but the last copy -- its own
and any other mod's -- with ``//Asset[...GUID='…'][position() < last()]``.
That tidies up after itself, and is said as such rather than warned about.

And a mod on this PC that is not in the loadout at all takes a GUID too, if
it is ever played beside it: a mod imported on its own is only ever loaded
with others. Those come from :func:`core.discovery.guid_owners` -- every mod
the game can load and the library holds -- leaving out the ones the loadout
has loaded, what is bundled inside them, and other copies of them on disk.
Which of the two the game would find depends on a load order the loadout
does not have, so that is not said.

A GUID the base game already has is taken as well: the shipped asset is the
first. Unless a mod that loads removes an asset of that GUID outright --
taking one out to put another in its place is how a mod swaps an asset, and
what is left is then anyone's guess from here -- in which case nothing is
said about that GUID at all.
"""

from __future__ import annotations

import contextlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import effective, reader
from .changes import OP_ADD_PREV, OP_REMOVE, Change
from .i18n import join_list, tr, trn

# A created asset's own GUID, read off its payload without parsing it: the
# changes panel asks after every edit, and a whole mods folder creates
# thousands of assets.
_OWN_GUID = re.compile(r"<Standard>\s*<GUID>\s*(\d+)\s*</GUID>")

# An op that removes every copy of a GUID but the last.
_TIDY = re.compile(
    r"""//Asset\[\s*Values/Standard/GUID\s*=\s*['"](\d+)['"]\s*\]"""
    r"""\[\s*position\(\)\s*(?:<|&lt;)\s*last\(\)\s*\]"""
)


@dataclass(frozen=True)
class Copy:
    origin: str  # "" for your own mod
    name: str


@dataclass(frozen=True)
class Repeat:
    """One GUID, and every asset created under it, in the order they stand in
    the file."""

    guid: str
    copies: Tuple[Copy, ...]
    # The shipped asset's name, when the game already has one under it.
    shipped: str = ""
    # A mod that loads removes every copy but the last, once they are all made.
    tidied: bool = False
    # A copy was put in ahead of the shipped asset, so it is the one found.
    copy_first: bool = False
    # Every copy the same asset, so which one is found makes no difference.
    same: bool = False
    # Mods on this PC, not in the loadout, that create it as well.
    elsewhere: Tuple[str, ...] = ()
    # The copies are in more than one of the file's top-level groups, so
    # the one found is not simply the first in the file.
    apart: bool = False
    # Every copy in the loadout the same asset. ``same`` as well, unless the
    # game ships one or a mod elsewhere on this PC makes one: that mod does
    # not load with the loadout, so playing it the copies are still no harm.
    alike: bool = False

    @property
    def harmless(self) -> bool:
        return self.tidied or self.same

    @property
    def origins(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(copy.origin for copy in self.copies))

    @property
    def found(self) -> str:
        """The name of the asset the game finds by this GUID: the first in
        the file, unless a mod removes all but the last."""
        if self.tidied:
            return self.copies[-1].name
        if self.shipped and not self.copy_first:
            return tr("the base game's %s", self.shipped)
        return self.copies[0].name


def _tidies(loadout) -> Dict[str, List[tuple]]:
    """For each GUID a loading mod removes all but the last copy of, where
    in load order each op that does so runs."""
    off = loadout.not_loading()
    held = [(entry.origin, entry.order, entry.source) for entry in loadout.unread]
    held += [(op.origin, op.order, op.source) for op in loadout.ops if op.active]
    out: Dict[str, List[tuple]] = {}
    for origin, order, source in held:
        if origin in off:
            continue
        for guid in _TIDY.findall(source or ""):
            out.setdefault(guid, []).append((loadout.layer_rank(origin), order))
    return out


def _left(loadout, changes: list, tidies: Sequence[tuple]) -> list:
    """Of ``changes``, in file order, the copies still there once every op
    of ``tidies`` has run: each removes all but the last of the copies made
    before it, and none made after.  Taken as tidied wherever any of them
    ran, the last copy was named as the one found when the op ran first and
    removed nothing, or when a later mod made another copy."""
    left = list(changes)
    for at in sorted(tidies):
        before = [change for change in left if (loadout.layer_rank(change.origin), change.order) < at]
        for change in before[:-1]:
            left.remove(change)
    return left


_END = float("inf")


_LAYOUT = re.compile(r">\s+<")


def _alike(changes) -> bool:
    """Whether every one of ``changes`` makes the very same asset -- as the
    game reads it, so layout between the tags does not count."""
    return len({_LAYOUT.sub("><", (change.payload or "").strip()) for change in changes}) == 1


def _guid_of(change) -> str:
    found = _OWN_GUID.search(change.payload or "")
    return found.group(1) if found else effective.new_asset_guid(change)


def _shipped_at(index, guid: str):
    if index is None:
        return None
    try:
        return index.position(guid)
    except Exception:  # noqa: BLE001 -- an index that cannot answer knows nothing
        return None


def _places(creations: list, ranks: Dict[int, tuple], index) -> Dict[int, tuple]:
    """Where each created asset lands in the file, as a key that sorts in file
    order -- which is what decides the one the loader finds.

    ``addNextSibling`` puts an asset straight after the one it names, so it
    sits where that one does, and a second put beside the same one lands
    between them: the later op is the nearer (measured, both games). One
    beside an asset another mod made sits where that one does, and so on up.
    ``addPrevSibling`` puts it straight before the one it names, after
    anything already put there. Where it cannot be worked out -- nothing to
    stand beside -- it goes last.
    """
    by_guid: Dict[str, list] = {}
    for change in creations:
        by_guid.setdefault(_guid_of(change), []).append(change)
    ops = sorted({(ranks[id(change)], change.order) for change in creations})
    number = {op: n for n, op in enumerate(ops)}
    placed: Dict[int, tuple] = {}

    def place(change, seen) -> tuple:
        if id(change) in placed:
            return placed[id(change)]
        here = (-number[(ranks[id(change)], change.order)], change.spot)
        anchor = change.guid
        shipped = _shipped_at(index, anchor)
        if change.op == OP_ADD_PREV:
            where = (
                (shipped - 1, (math.inf, -here[0], here[1])) if shipped is not None
                and anchor not in by_guid else (_END, here)
            )
        elif shipped is not None:
            where = (shipped, here)
        elif anchor in by_guid and id(change) not in seen:
            # Beside what a mod made: the first of that GUID, as the loader
            # finds it -- which is its own question, answered the same way.
            first = min(
                (place(other, seen | {id(change)}) for other in by_guid[anchor] if other is not change),
                default=(_END,),
            )
            where = first + (here,)
        else:
            where = (_END, here)
        placed[id(change)] = where
        return where

    for change in creations:
        place(change, frozenset())
    return placed


def _groups(loadout, creations: list, index) -> Dict[int, object]:
    """Which of the file's top-level groups each created asset is in: the
    group of the asset it is put beside, or the end of the file for one
    written straight under ``<ModOps>`` or added there in a group, which
    is the reader's to say (:func:`core.reader.end_groups`). None where it
    cannot be said -- an index that does not know, or nothing known to
    stand beside."""
    asked = getattr(index, "top_group", None)
    at_the_end = reader.end_groups(sorted(
        creations, key=lambda change: (loadout.layer_rank(change.origin), change.order, change.spot)
    ))
    by_guid: Dict[str, list] = {}
    for change in creations:
        by_guid.setdefault(_guid_of(change), []).append(change)
    found: Dict[int, object] = {}

    def group(change, seen):
        if id(change) in found:
            return found[id(change)]
        where: object = at_the_end.get(change.uid)
        if where is None and asked is not None:
            try:
                where = asked(change.guid)
            except Exception:  # noqa: BLE001 -- an index that cannot answer knows nothing
                where = None
            if where is None and id(change) not in seen:
                # Beside what a mod made: in that one's group.
                for other in by_guid.get(change.guid, ()):
                    if other is not change:
                        where = group(other, seen | {id(change)})
                        break
        found[id(change)] = where
        return where

    for change in creations:
        group(change, frozenset())
    return found


def _apart(groups: Dict[int, object], changes: list, index, guid: str, shipped: bool) -> bool:
    held = {groups.get(id(change)) for change in changes}
    if shipped:
        asked = getattr(index, "top_group", None)
        try:
            held.add(asked(guid) if asked is not None else None)
        except Exception:  # noqa: BLE001 -- an index that cannot answer knows nothing
            held.add(None)
    return len(held) > 1 and None not in held


def said_apart() -> str:
    """What is said in place of which asset the game finds, for a GUID whose
    copies are in different top-level groups."""
    return tr("the game finds only one of them, and which one depends on the ops run before")


def _resolved(path) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        return Path(path)


def outside(loadout, owners) -> Dict[str, Tuple[str, ...]]:
    """Of ``owners`` (``discovery.guid_owners``), the mods not in the loadout:
    not one it loads, nothing bundled inside one, no other copy of one."""
    off = loadout.not_loading()
    loading = [layer for layer in loadout.layers if layer.active and layer.origin not in off]
    folders = {_resolved(layer.folder) for layer in loading if layer.folder}
    ids = {str(layer.mod_id or "").strip().lower() for layer in loading} - {""}
    if loadout.has_own_mod():
        ids.add(str(loadout.own.mod_id or "").strip().lower())
    # A mod of yours as it builds: a blank Mod ID is filled in from its
    # name, and its build in the library, which the game loads, is no other
    # mod's.  It was "also made by" itself there (R3-4, hands-on run 3).
    from . import generator, modinfo

    for origin in {*loadout.project_origins(), *loadout.projects}:
        if loadout.mod(origin) is None or (not origin and not loadout.has_own_mod()):
            continue
        with contextlib.suppress(LookupError, ValueError):
            ids.add(modinfo.written_mod_id(generator.for_mod(loadout, origin)).strip().lower())
    ids.discard("")
    # Asked once for each folder a mod is in, and not once for each GUID it
    # makes: a mods folder's worth of GUIDs against every loaded mod's
    # folder, a path at a time, was two seconds of every redraw of the Mod
    # changes panel with 68 mods loaded.
    inside: Dict[Path, bool] = {}

    def ours(owner) -> bool:
        if owner.mod_id and owner.mod_id in ids:
            return True
        root = owner.root
        found = inside.get(root)
        if found is None:
            found = inside[root] = root in folders or any(
                parent in folders for parent in root.parents
            )
        return found

    out = {}
    for guid, held in owners.items():
        # One mod in two places -- the mods folder and mod.io's downloads --
        # is one mod: named where it is found first.
        seen, others = set(), []
        for owner in held:
            key = owner.mod_id or owner.name.lower()
            if ours(owner) or key in seen:
                continue
            seen.add(key)
            others.append(owner.label)
        if others:
            out[guid] = tuple(others)
    return out


@dataclass(frozen=True)
class _Laid:
    """One GUID's created assets, in the order they stand in the file."""

    guid: str
    changes: list
    # Where the shipped asset stands, or None for one the game has not.
    shipped_at: object
    # A copy was put in ahead of the shipped asset.
    copy_first: bool
    apart: bool
    # An op of a loading mod removes all but the last copy after every one
    # is made.  ``changes`` are then all the copies made; otherwise only
    # those an earlier such op leaves.
    tidied: bool = False


def _laid_out(loadout, index, keep: Callable[[str, list, object], bool]) -> List[_Laid]:
    """Each GUID a loading mod creates and none removes outright, of those
    ``keep(guid, changes, shipped_at)`` keeps, with its copies put in file
    order. Where each one stands is worked out for every created asset at
    once -- one is put beside another -- and only when one is kept."""
    made: Dict[str, list] = {}
    removed = set()
    creations: list = []
    ranks: Dict[int, tuple] = {}
    for change in loadout.active():
        if change.op == OP_REMOVE and not change.path:
            removed.add(change.guid)
        if not change.creates_asset:
            continue
        guid = _guid_of(change)
        if guid:
            made.setdefault(guid, []).append(change)
            creations.append(change)
            ranks[id(change)] = loadout.layer_rank(change.origin)
    kept = []
    for guid, changes in made.items():
        if guid in removed:
            continue
        shipped_at = _shipped_at(index, guid)
        if keep(guid, changes, shipped_at):
            kept.append((guid, changes, shipped_at))
    if not kept:
        return []
    places = _places(creations, ranks, index)
    groups = _groups(loadout, creations, index)
    tidies = _tidies(loadout)
    laid = []
    for guid, changes, shipped_at in kept:
        changes.sort(key=lambda change: places[id(change)])
        at = tidies.get(guid, ())
        tidied = bool(at) and max(at) > max(
            (loadout.layer_rank(change.origin), change.order) for change in changes
        )
        if not tidied:
            changes = _left(loadout, changes, at)
        # The shipped one stands where it shipped; a copy put in ahead of it
        # is what the loader finds instead.
        ahead = shipped_at is not None and places[id(changes[0])] < (shipped_at,)
        laid.append(_Laid(
            guid, changes, shipped_at, ahead,
            _apart(groups, changes, index, guid, shipped_at is not None), tidied,
        ))
    return laid


def find(loadout, index=None, elsewhere=None) -> List[Repeat]:
    """Every GUID that more than one created asset has, or that the base game
    has and a mod creates again -- among the mods that load, in file order --
    or that a mod on this PC outside the loadout creates as well.

    ``index``, the base game's, is what knows the shipped assets and where
    each stands. ``elsewhere`` is :func:`core.discovery.guid_owners` over the
    mods on this PC, when there is one to hand."""
    not_ours = outside(loadout, elsewhere) if elsewhere else {}
    laid = _laid_out(
        loadout, index,
        lambda guid, changes, shipped_at: len(changes) > 1 or shipped_at is not None or guid in not_ours,
    )
    found: List[Repeat] = []
    for one in laid:
        guid, changes = one.guid, one.changes
        shipped = ""
        if one.shipped_at is not None:
            element = index.asset_element(guid)
            shipped = ((element.findtext("Values/Standard/Name") or "").strip() if element is not None else "") or tr("unnamed")
        others = not_ours.get(guid, ())
        copies = tuple(
            Copy(change.origin, effective.new_asset_name(change) or tr("unnamed")) for change in changes
        )
        alike = _alike(changes)
        found.append(Repeat(
            guid, copies, shipped, tidied=one.tidied, copy_first=one.copy_first,
            same=not shipped and not others and alike,
            elsewhere=others, apart=one.apart, alike=alike,
        ))
    found.sort(key=lambda repeat: (repeat.harmless, len(repeat.guid), repeat.guid))
    return found


@dataclass(frozen=True)
class Standing:
    """Of the assets under one GUID, the one the game finds -- which is the
    one the asset view shows, and the one every op aimed at the GUID edits."""

    # The creating changes, in the order their assets stand in the file.
    copies: tuple
    # The change whose asset is found, or None where it is the shipped one.
    change: Optional[Change] = None
    # The game ships one as well.
    shipped: bool = False
    # The copies are in different top-level groups, so which one is found
    # depends on the op before: ``change`` is the first in the file, shown
    # for want of an answer, and the view says so.
    apart: bool = False

    @property
    def others(self) -> int:
        """How many assets under the GUID are not the one shown."""
        return len(self.copies) + self.shipped - 1


def standing(loadout, index=None) -> Dict[str, Standing]:
    """``{guid: Standing}`` for every GUID with more than one asset under it
    -- two created, or one created and one shipped -- among the mods that
    load. A GUID a mod removes outright is left out: taking one out to put
    another in is how an asset is swapped, and the one put in is shown.

    Asked once for the whole loadout, not once per asset: where each copy
    stands is worked out for all of them together."""
    out: Dict[str, Standing] = {}
    for one in _laid_out(
        loadout, index, lambda guid, changes, shipped_at: len(changes) > 1 or shipped_at is not None,
    ):
        shipped = one.shipped_at is not None
        if one.tidied:
            found = one.changes[-1]
        elif shipped and not one.copy_first:
            found = None
        else:
            found = one.changes[0]
        out[one.guid] = Standing(
            tuple(one.changes), found, shipped, apart=one.apart and not one.tidied,
        )
    return out


def _within(one: str, origin: str) -> bool:
    """Whether ``one`` is ``origin`` or a mod bundled inside it: a bundle's
    row stands for its sub-mods while it is folded, and two of them taking
    one GUID (*Extended Agriculture Modules*' farm and animal modules) is the
    bundle's to say."""
    return one == origin or bool(origin) and one.startswith(origin + "/")


def of(found: List[Repeat], origin: str) -> List[Repeat]:
    return [repeat for repeat in found if any(_within(one, origin) for one in repeat.origins)]


def describe(found: List[Repeat], origin: str, name: Callable[[str], str], most: int = 5) -> List[str]:
    """What one mod creates under a GUID something else has, for its tooltip.

    ``name(origin)`` is what a mod is called."""
    mine = of(found, origin)
    lines = []
    for repeat in mine[:most]:
        ours = [copy.name for copy in repeat.copies if _within(copy.origin, origin)]
        others = [name(other) for other in repeat.origins if not _within(other, origin)]
        if repeat.shipped:
            others.insert(0, tr("the base game (%s)", repeat.shipped))
        names = " / ".join(dict.fromkeys(ours))
        where = []
        if len(ours) > 1:
            where.append(tr("twice in this mod"))
        if others:
            where.append(tr("also in %s", join_list(others)))
        if repeat.elsewhere and len(repeat.copies) == 1 and not repeat.shipped:
            lines.append("  " + tr(
                "GUID %(guid)s (%(names)s): also made by %(others)s, on this "
                "PC and not in this loadout. If both load, the game finds "
                "only one of them",
                guid=repeat.guid, names=names, others=join_list(repeat.elsewhere),
            ))
            continue
        if repeat.elsewhere:
            # Translators: the last of a list of where else a GUID is used,
            # after "twice in this mod" or "also in <mods>".
            where.append(tr("and on this PC by %s", join_list(repeat.elsewhere)))
        if repeat.tidied:
            ending = tr("a mod removes all but the last")
        elif repeat.same:
            ending = tr("the same asset each time, so no harm done")
        elif repeat.apart:
            ending = said_apart()
        else:
            ending = tr("the game finds only %s", repeat.found)
        lines.append("  " + tr(
            "GUID %(guid)s (%(names)s): %(where)s, %(ending)s",
            guid=repeat.guid, names=names, where=", ".join(where), ending=ending,
        ))
    if len(mine) > most:
        lines.append("  " + trn("and %(n)s more", "and %(n)s more", len(mine) - most))
    if lines:
        lines.insert(0, tr("Creates assets under a GUID already taken:"))
    return lines


def mark(found: List[Repeat], origin: str) -> str:
    """For the mod's row: how many of its GUIDs are taken twice, or ""."""
    real = [repeat for repeat in of(found, origin) if not repeat.harmless]
    if not real:
        return ""
    return trn("%(n)s GUID taken twice", "%(n)s GUIDs taken twice", len(real))


def playing(found: List[Repeat]) -> List[Repeat]:
    """The ones that matter when the loadout is played: two of its loading
    mods under one GUID, or one of them and the base game. A mod elsewhere on
    this PC does not load with it -- Play switches off what the loadout does
    not have on -- and an identical or self-tidied copy is no harm.

    The same for ``find`` with ``elsewhere`` as without: identical copies a
    mod elsewhere makes differently are not ``same``, but are still no harm
    when played, so the conflicts dialog can tell the panel's count apart
    from the overview card's (A11-32)."""
    return [
        repeat for repeat in found
        if not repeat.harmless and not (repeat.alike and not repeat.shipped)
        and (len(repeat.copies) > 1 or repeat.shipped)
    ]


def before_play(found: List[Repeat], name: Callable[[str], str], most: int = 4) -> List[str]:
    """One sentence each for Before Play: who creates the GUID, and which
    asset the game finds.

    One sentence for each set of mods, not each GUID: *Trade Union
    Rebalance* and *Extended Agriculture Modules* share 17, which is one
    thing to know about two mods rather than seventeen."""
    groups: Dict[tuple, List[Repeat]] = {}
    for repeat in playing(found):
        who = tuple(dict.fromkeys(name(copy.origin) for copy in repeat.copies))
        groups.setdefault((bool(repeat.shipped), who), []).append(repeat)
    lines = []
    for (shipped, who), held in groups.items():
        mods = join_list(list(who))
        # One whose copies are in different groups is said the way several
        # are: only one is found, and not which.
        if len(held) == 1 and not held[0].apart:
            values = {"guid": held[0].guid, "mods": mods, "found": held[0].found}
            if shipped:
                lines.append(tr("GUID %(guid)s is created by the base game "
                                "and %(mods)s. The game finds only "
                                "%(found)s.", **values))
            elif len(who) == 1:
                lines.append(tr("GUID %(guid)s is created twice by %(mods)s. "
                                "The game finds only %(found)s.", **values))
            else:
                lines.append(tr("GUID %(guid)s is created by %(mods)s. The "
                                "game finds only %(found)s.",
                                **values))
            continue
        guids = [repeat.guid for repeat in held]
        listed = ", ".join(guids[:most]) + (", …" if len(guids) > most else "")
        if shipped:
            lines.append(trn("%(n)s GUID is created by the base game and "
                             "%(mods)s (%(guids)s). The game finds only one "
                             "of each.",
                             "%(n)s GUIDs are created by the base game and "
                             "%(mods)s (%(guids)s). The game finds only one "
                             "of each.", len(held), mods=mods, guids=listed))
        elif len(who) == 1:
            lines.append(trn("%(n)s GUID is created twice by %(mods)s "
                             "(%(guids)s). The game finds only one of each.",
                             "%(n)s GUIDs are each created twice by %(mods)s "
                             "(%(guids)s). The game finds only one of each.", len(held), mods=mods, guids=listed))
        else:
            lines.append(trn("%(n)s GUID is created by %(mods)s (%(guids)s). "
                             "The game finds only one of each.",
                             "%(n)s GUIDs are created by %(mods)s "
                             "(%(guids)s). The game finds only one of each.", len(held), mods=mods, guids=listed))
    return lines


def summarise(found: List[Repeat]) -> str:
    """A phrase for the changes panel's conflicts line, or ""."""
    real = [repeat for repeat in found if not repeat.harmless]
    if not real:
        return ""
    return trn("%(n)s GUID taken twice", "%(n)s GUIDs taken twice", len(real))
