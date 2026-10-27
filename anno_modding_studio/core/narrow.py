"""The ModOps the Studio writes for what is set in Fields.

A value set in the asset editor, a field added or taken out there, an asset
made beside another: each is written as a narrow ModOp, because there the
Studio is the author (the ops-as-written proposal, section 1).  Those ModOps
are the record of each mod of yours -- the loadout's own mod, a patch, a mod
of yours the loadout carries on with -- and what its project keeps
(:mod:`.projects`).  What is exported is their text, and what is saved and
undone is them; the changes under each are what it comes to, as a read
mod's changes are what its ops come to.  Section 4.2 of the
projects-and-loadouts proposal: built for the loadout's own mod as its phase
4a, and for the patches and a carried-on mod as 4b-2.

**Where an edit becomes one.**  Fields, the Mod changes panel, the XML view
and the Mod file tab go on saying what was done as changes: a value is what
is typed there, and a change is how a value is said.  :func:`write` writes
the ModOps for them, and it runs where every act on a loadout ends -- each
step of the undo history, a step undone or redone, a loadout taken on -- so
that when an act is over, each change of the own mod has its ModOp and each
ModOp is what its changes say.

**What it writes is what the export would.**  The same grouping and the
same lines (:func:`.generator.written_for`): an op apiece, but for a node
of an asset's identity swapped whole and for nodes put side by side, which
are one ModOp for several changes.  Where they stand is as it always was:
by asset, assets by name, each asset's in the order the game has to apply
them (:func:`.changes.emit_order`).  The generator lays the file out from the
changes and writes each of the Studio's ModOps from its op
(:func:`.generator.written`), so a loadout no step has been taken on yet
-- read by a tool, made by a test -- writes the same file from its changes
alone.

**Kept apart from the ops as written** (:attr:`.changes.Loadout.ops`), in
:attr:`.changes.Loadout.narrow`.  An op kept as written is somebody's text:
its changes are its answers, are not switched one at a time, are read again
when a mod before them moves, and can be written narrow on asking.  None of
that is true of these, and everything that reads a kept op would take one of
these for it.

**Read out of them again** where a loadout's project moved on in another
loadout (section 8, phase 6b, :mod:`.readback`): there each ModOp is read
where the mod stands, and what it comes to is made its changes again,
written as it.  Everywhere else the changes are made with them, in one act,
and an old value is still what it was when the edit was made.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Tuple

from . import games, generator
from .changes import Change, Loadout, Op, emit_order, is_own_mod


def written_here(loadout: Loadout) -> List[Change]:
    """The changes the Studio writes ModOps for: every change made in a mod
    of yours that is no op's answer, an exception to one included.  Of the
    loadout's own mod every change is made here; of a patch or a carried-on
    mod, the ones that were not read out of its folder."""
    mine = set(loadout.project_origins())
    return [
        change for change in loadout.changes.values()
        if not change.op_uid and not loadout.held(change.origin) and (
            is_own_mod(change.origin) or (change.origin in mine and change.authored)
        )
    ]


def _said(part: List[Change]) -> tuple:
    """What a block's changes say that its ModOps are written from, in the
    order they come: what to know them again by when nothing of it moved."""
    return tuple(
        (change.key, change.asset_label, change.path, change.tag, change.new_value,
         change.payload, change.note, change.spot, change.written_as)
        for change in part
    )


def write(loadout: Loadout) -> bool:
    """Write the Studio's ModOps for the changes made in each mod of yours
    (:func:`written_here`); whether any of them moved.

    Each keeps its uid where one of the changes it is written for was
    written as it before, and is the same object where nothing about it
    moved, so a step that touched one asset records that asset's ModOps
    and no others.  One that nothing is written as any more goes.
    """
    game = games.get(loadout.game)
    mine = loadout.ordered(written_here(loadout))
    # By its place in the file and its asset, as the generator has them,
    # switched on and off apart: what is written for the game is worked out
    # from the ones that are on alone.
    # And by mod: each mod's are its own file's.
    blocks: Dict[Tuple[str, int, str], Tuple[List[Change], List[Change]]] = {}
    place: Dict[Tuple[str, str], int] = {}
    for change in [*(one for one in mine if one.active), *(one for one in mine if not one.active)]:
        place.setdefault((change.origin, change.guid), len(place))
    for change in mine:
        on, off = blocks.setdefault((change.origin, change.order, change.guid), ([], []))
        (on if change.active else off).append(change)

    held = {op.uid: op for op in loadout.narrow}
    claimed: set = set()
    now: List[Op] = []
    # What each block was written as last time, by what its changes said
    # then: a step moves an asset or two, and the rest are not written again.
    memo: Dict[tuple, Tuple[tuple, List[Op]]] = (
        loadout._narrow_memo[1] if loadout._narrow_memo[:1] == (game.key,) else {}
    )
    remembered: Dict[tuple, Tuple[tuple, List[Op]]] = {}
    for origin, order, guid in sorted(
        blocks, key=lambda where: (where[0], where[1], place[(where[0], where[2])]),
    ):
        for part, active in zip(blocks[(origin, order, guid)], (True, False), strict=True):
            if not part:
                continue
            hit = memo.get((origin, order, guid, active))
            if hit is not None and hit[0] == _said(part) and all(
                held.get(kept.uid) is kept and kept.uid not in claimed for kept in hit[1]
            ):
                now.extend(hit[1])
                claimed.update(kept.uid for kept in hit[1])
                remembered[(origin, order, guid, active)] = hit
                continue
            block: List[Op] = []
            for changes, notes, lines in generator.written_for(emit_order(part), game):
                uid = next(
                    (
                        change.written_as for change in changes
                        if change.written_as in held and change.written_as not in claimed
                    ),
                    "",
                ) or uuid.uuid4().hex[:12]
                claimed.add(uid)
                was = held.get(uid)
                op = Op(
                    # As it stands in the file, from its first character: the
                    # first line without the indent it is written at.
                    source="\n".join(lines)[len(generator.INDENT):],
                    origin=origin,
                    # One to a line: nodes put side by side are one ModOp,
                    # written under the note of each.
                    note="\n".join(notes),
                    order=order,
                    uid=uid,
                    active=active,
                    matched=[guid],
                    made=len(changes),
                    plain=len(changes) == 1,
                    authored=True,
                    extra=was.extra if was is not None else None,
                )
                if was is not None and vars(was) == vars(op):
                    op = was
                block.append(op)
                for change in changes:
                    if change.written_as != uid:
                        change.written_as = uid
            now.extend(block)
            remembered[(origin, order, guid, active)] = (_said(part), block)
    loadout._narrow_memo = (game.key, remembered)
    # A change that is not the Studio's to write any more -- an op's answer
    # now, or moved into somebody else's mod -- is written as nothing of its.
    written = {id(change) for change in mine}
    for change in loadout.changes.values():
        if change.written_as and id(change) not in written and not loadout.held(change.origin):
            change.written_as = ""
    # A mod whose project another loadout changed since this one read it is
    # held (:meth:`.changes.Loadout.held`): its ModOps are that project's as
    # it is now, and this loadout's older changes are not written over them.
    now.extend(op for op in loadout.narrow if loadout.held(op.origin))
    moved = len(now) != len(loadout.narrow) or any(
        one is not other for one, other in zip(now, loadout.narrow, strict=True)
    )
    if moved:
        loadout.narrow = now
    return moved
