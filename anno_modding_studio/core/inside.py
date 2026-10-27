"""Edits of yours inside somebody else's ops, and which mod of yours keeps each.

An edit can go inside somebody else's op: an op of theirs typed over or
taken out, one switched off, one of yours put among theirs.  Play writes a
generated copy of their mod with those edits in it (:mod:`.edits`), and the
library's copy stays as its author shipped it.  Their mod is never where
the edits are kept: they belong to a mod of yours, and *Changes go to* says
which (``docs/proposals/projects-and-loadouts-proposal.md``, section 5.3):

* **your patch for their mod** keeps an edit that should hold wherever you
  use the mod, so its project carries it into every loadout that has it;
* **the loadout's own mod** keeps one that should hold in that loadout only.

The edits stay where they are in the loadout: under their mod's origin, in
its ops and its changes, in :attr:`~.changes.Loadout.taken_out` and
:attr:`~.changes.Loadout.held_edits`, which is what the generated copy is
written from.  What is added is the keeper, ``kept_by``: a patch's origin,
or ``""`` for the loadout's own mod.  :func:`assign` settles it for the
edits a step made, at the step's end; saving and opening take
the edits out of a loadout file into their keeper's project, and put them
back (:func:`.projects.split`, :func:`.projects.join`).

Where both keep edits to one mod, the patch's come first and then the own
mod's, as the two mods load (:func:`in_load_order`).

Unloading their mod takes nothing out of your patch: what it keeps inside
that mod is set aside (:meth:`.changes.Loadout.set_aside_kept`), its
project goes on keeping it,
and reading the mod in again puts it back (:func:`take_back`).  What the
loadout's own mod kept inside it goes with the mod, as an unload always
took this loadout's changes to a mod with it.

They are the keeper's work, so its history holds them: each checkpoint of
it has them as its project does (:attr:`.changes.Checkpoint.inside`), and
restoring one puts them back (:func:`restore`).

"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Dict, List, Optional, Set, Tuple

from . import patching
from .changes import MADE_IN, OWN, Loadout, Op, change_from_dict, inside_says
from .projects import AFTER, as_op, moved_along, placed_in, put_among, target_of


def keeper_of(item: Any) -> Optional[str]:
    """Who keeps one edit: an op, a change, an entry of ``taken_out`` or of
    ``held_edits``.  None where nobody has said yet."""
    if isinstance(item, (list, tuple)):
        kept = item[4] if len(item) > 4 else None
    elif isinstance(item, dict):
        kept = item.get("kept_by")
    else:
        kept = getattr(item, "kept_by", None)
    return kept if isinstance(kept, str) else None


def kept(item: Any, keeper: Optional[str], undecided: str = OWN) -> bool:
    """Whether ``item`` is kept by ``keeper``; every keeper's for None.  An
    edit nobody has said of yet is ``undecided``'s: who the step's end will
    make its keeper (:func:`keeper_for`)."""
    if keeper is None:
        return True
    found = keeper_of(item)
    return (undecided if found is None else found) == keeper


def theirs(loadout: Loadout) -> Set[str]:
    """The loaded mods that are somebody else's: whose edits are inside
    their ops, and kept by a mod of yours."""
    mine = set(loadout.project_origins())
    return {
        layer.origin for layer in loadout.layers
        if layer.origin not in mine and not layer.patches and not patching.is_mine(loadout, layer)
    }


def keeper_for(loadout: Loadout, origin: str) -> str:
    """Which mod of yours keeps an edit made now inside ``origin``'s ops:
    your patch for it where that is what changes go to, and the loadout's
    own mod otherwise."""
    patch = loadout.patch_for(origin)
    if patch is not None and loadout.editing_origin() == patch.origin:
        return patch.origin
    return OWN


def assign(loadout: Loadout) -> bool:
    """Say who keeps each edit inside somebody else's ops that nobody has
    said of yet, and forget it of an op of theirs switched back on; whether
    anything moved.  Run where every step ends, so the keeper is the one
    *Changes go to* chose while the edit was made."""
    origins = theirs(loadout)
    moved = _owned_outright(loadout, origins)
    if not origins:
        return moved
    keepers = {origin: keeper_for(loadout, origin) for origin in origins}
    used: Set[str] = set()
    of_op: Dict[str, Optional[str]] = {}
    for op in loadout.ops:
        if op.origin not in origins:
            continue
        if op.authored or not op.active:
            if op.kept_by is None:
                op.kept_by = keepers[op.origin]
                moved = True
            used.add(op.kept_by)
        elif op.kept_by is not None:
            op.kept_by = None
            moved = True
        if op.authored:
            of_op[op.uid] = op.kept_by
    for change in loadout.changes.values():
        if change.origin not in origins:
            continue
        if change.op_uid:
            # An op's answer: the op's, and of an op of theirs nobody's.
            wanted = of_op.get(change.op_uid)
        elif change.authored:
            wanted = change.kept_by if change.kept_by is not None else keepers[change.origin]
        else:
            wanted = None
        if change.kept_by != wanted:
            change.kept_by = wanted
            moved = True
        if wanted is not None:
            used.add(wanted)
    taken = []
    for entry in loadout.taken_out:
        if entry[0] in origins and keeper_of(entry) is None:
            entry = [*entry[:3], entry[3] if len(entry) > 3 else 0, keepers[entry[0]], *entry[5:]]
            moved = True
        if entry[0] in origins:
            used.add(keeper_of(entry) or OWN)
        taken.append(entry)
    if moved:
        loadout.taken_out = taken
    held = []
    for record in loadout.held_edits:
        origin = record.get("origin", "")
        if origin in origins and keeper_of(record) is None:
            record = {**record, "kept_by": keepers[origin]}
            moved = True
        if origin in origins:
            used.add(keeper_of(record) or OWN)
        held.append(record)
    if moved:
        loadout.held_edits = held
    if OWN in used and not loadout.own_mod:
        # The own mod keeps them, so it exists, and has a project.
        loadout.own_mod = True
        moved = True
    return moved


# -- one keeper's alone ---------------------------------------------------------
def _owned_outright(loadout: Loadout, origins: Set[str]) -> bool:
    """Forget the keeper of an edit to a mod that is yours now -- made a
    project, or forked -- and so no edit inside somebody else's ops; whether
    any was."""
    loaded = {layer.origin for layer in loadout.layers}
    yours = loaded - origins
    moved = False
    for op in loadout.ops:
        if op.kept_by is not None and op.origin in yours:
            op.kept_by = None
            moved = True
    for change in loadout.changes.values():
        if change.kept_by is not None and change.origin in yours:
            change.kept_by = None
            moved = True
    if any(entry[0] in yours and len(entry) > 4 for entry in loadout.taken_out):
        loadout.taken_out = [[*entry[:4]] if entry[0] in yours and len(entry) > 4 else entry
                             for entry in loadout.taken_out]
        moved = True
    if any(held.get("origin") in yours and "kept_by" in held for held in loadout.held_edits):
        loadout.held_edits = [
            {key: value for key, value in held.items() if key != "kept_by"}
            if held.get("origin") in yours else held
            for held in loadout.held_edits
        ]
        moved = True
    return moved


def only_kept_by(loadout: Loadout, origin: str, keeper: str) -> Loadout:
    """``loadout`` with only ``keeper``'s edits inside ``origin``'s ops: the
    other keepers' undone, their ops put back and switched on again, and
    their own ops and changes left out.  A copy; the loadout is untouched.

    What an export of a patch works its edits out from: the loadout's own
    mod's edits to the same mod hold in this loadout alone, and are no part
    of the patch.
    """
    undecided = keeper_for(loadout, origin)
    view = copy.copy(loadout)
    dropped: Set[str] = set()
    switched_on: Set[str] = set()
    ops = []
    for op in loadout.ops:
        if op.origin == origin and (op.authored or not op.active) and not kept(op, keeper, undecided):
            if op.authored:
                dropped.add(op.uid)
                continue
            op = replace(op, active=True, kept_by=None)
            switched_on.add(op.uid)
        ops.append(op)
    taken = []
    for entry in loadout.taken_out:
        if entry[0] == origin and not kept(entry, keeper, undecided):
            # Put back where it stood, as the mod's file has it.
            ops.append(Op(
                source=entry[1], origin=origin, within=list(entry[2]),
                order=int(entry[3]) if len(entry) > 3 else 0,
            ))
            continue
        taken.append(entry)
    changes = {}
    for key, change in loadout.changes.items():
        if change.origin == origin:
            if change.op_uid in dropped:
                continue
            if change.authored and not change.op_uid and not kept(change, keeper, undecided):
                continue
            if change.op_uid in switched_on:
                change = replace(change, active=True)
        changes[key] = change
    view.ops = ops
    view.changes = changes
    view.taken_out = taken
    view.held_edits = [
        record for record in loadout.held_edits
        if record.get("origin") != origin or kept(record, keeper, undecided)
    ]
    return view


def take_back(loadout: Loadout, origins) -> int:
    """After the mods ``origins`` were read in: put back what each patch
    keeps inside them, set aside when they were unloaded or when the
    loadout was opened without them
    (:meth:`.changes.Loadout.set_aside_kept`).  How many edits
    came back.

    An op of the patch's goes back where it stood among the loadout's ops;
    an op of theirs it switched off is switched off again, found by its
    uid or else by its text; one it took out comes out of what was read.
    """
    origins = {origin for origin in origins if loadout.layer(origin) is not None}
    back = 0
    for keeper in list(loadout.kept_aside):
        kept = loadout.kept_aside[keeper]
        if loadout.layer(keeper) is None:
            continue
        placed: List[tuple] = []
        for key in _PUT_BACK:
            left = []
            for entry in kept.get(key) or ():
                if target_of(entry) not in origins:
                    left.append(entry)
                    continue
                _put_back(loadout, keeper, key, entry, placed)
                back += 1
            if key not in kept:
                continue
            if left:
                kept[key] = left
            else:
                del kept[key]
        _moved_along(loadout, keeper, placed)
        if not kept:
            del loadout.kept_aside[keeper]
    if back:
        loadout._op_memo = ()
    return back


def _moved_along(loadout: Loadout, keeper: Optional[str], placed: List[tuple]) -> None:
    """What stood at the place of each op of yours put among theirs by text
    goes along with it (:func:`.projects.moved_along`): ``placed`` is each
    one's mod, old place, new place and uid."""
    if not placed:
        return
    moved_along({(origin, was): now for origin, was, now, _uid in placed},
                {uid for *_place, uid in placed}, loadout.changes.values(), loadout.taken_out, keeper)


def keep_places(loadout: Loadout, origins) -> List[Tuple[Op, dict]]:
    """Where each op of yours inside ``origins``'s ops stands among theirs,
    by text (:func:`.projects.placed_in`), in the order their files are
    written in: taken before a reading of them gives their ops other places,
    for :func:`put_places_back`."""
    origins = set(origins)
    found = []
    for at, op in enumerate(loadout.ops):
        if op.origin in origins and op.authored and not op.aside:
            found.append(((op.order, at), op, placed_in(loadout.ops, at)))
    return [(op, where) for _rank, op, where in sorted(found, key=lambda one: one[0])]


def put_places_back(loadout: Loadout, kept: List[Tuple[Op, dict]]) -> None:
    """After a reading of their mods: put each op of yours ``kept`` says
    where (:func:`keep_places`) back after the op of theirs it followed, by
    its text, with what stood at its place moving along.  The reading gave
    their ops the places their files have, and appended them, and ties
    with yours went the wrong way round.  One whose op of theirs is not
    there any more stays where it was."""
    placed: List[tuple] = []
    for op, where in kept:
        at = next((at for at, held in enumerate(loadout.ops) if held is op), None)
        if at is None:
            continue
        was = op.order
        del loadout.ops[at]
        if put_among(loadout.ops, op, where, again=True):
            placed.append((op.origin, was, op.order, op.uid))
        else:
            loadout.ops.insert(at, op)
    loadout._op_memo = ()
    _moved_along(loadout, None, placed)


# What :func:`_put_back` puts back first: an op of theirs taken out, then
# one switched off, then each of yours where it stood among what is left.
_PUT_BACK = ("taken_out", "off", "ops", "changes", "held_edits")


def restore(loadout: Loadout, keeper: str, kept: dict) -> Set[str]:
    """Put what ``keeper``, a mod of yours, keeps inside somebody else's ops
    back to ``kept``, as a checkpoint of its history has it
    (:attr:`.changes.Checkpoint.inside`): what it keeps now is undone, and
    what it kept then is put back where it stood.  Inside a mod not loaded
    here a patch's is set aside for its project (:attr:`.changes.Loadout.kept_aside`),
    and the own mod's is not put back, as unloading the mod took it.  The
    mods loaded here whose ops moved, to be read again; none where it keeps
    what it kept then.
    """
    now = loadout.kept_inside(keeper)
    if inside_says(now) == inside_says(kept):
        return set()
    loaded = {layer.origin for layer in loadout.layers}
    moved = {
        target_of(entry) for held in (now, kept)
        for entries in held.values() for entry in entries
    } & loaded
    loadout.undo_kept(keeper)
    placed: List[tuple] = []
    for key in _PUT_BACK:
        for entry in kept.get(key) or ():
            entry = copy.deepcopy(entry)
            if isinstance(entry, dict):
                entry.pop(MADE_IN, None)
            if target_of(entry) in loaded:
                _put_back(loadout, keeper, key, entry, placed)
            elif keeper:
                loadout.kept_aside.setdefault(keeper, {}).setdefault(key, []).append(entry)
    _moved_along(loadout, keeper, placed)
    loadout._op_memo = ()
    return moved


def _put_back(loadout: Loadout, keeper: str, key: str, entry: Any,
              placed: Optional[List[tuple]] = None) -> None:
    """One edit set aside, back in the loadout, kept by ``keeper``; an op of
    yours put among theirs by text is added to ``placed``, given it, for
    :func:`_moved_along`."""
    if key == "ops":
        op = Op.from_dict(as_op(entry))
        op.kept_by = keeper
        was = op.order
        if AFTER in entry and put_among(loadout.ops, op, entry):
            # Among their ops by text: this reading's uids are not the ones
            # it was set aside under.
            if placed is not None:
                placed.append((op.origin, was, op.order, op.uid))
            return
        at = entry.get("at")
        loadout.ops.insert(min(at, len(loadout.ops)) if isinstance(at, int) and at >= 0
                           else len(loadout.ops), op)
    elif key == "off":
        theirs = [op for op in loadout.ops if op.origin == entry.get("origin") and not op.authored]
        found = next((op for op in theirs if op.uid == entry.get("uid")), None) or next(
            (op for op in theirs if op.source == entry.get("source") and op.active), None)
        if found is not None:
            _switch(loadout, found, False, keeper)
    elif key == "changes":
        change = change_from_dict(entry)
        if change is not None:
            change.kept_by = keeper
            loadout.changes[change.key] = change
    elif key == "taken_out":
        # Taken out of their file, which the reading has just put back in.
        read = next((op for op in loadout.ops if op.origin == entry[0]
                     and op.source == entry[1] and not op.authored), None)
        if read is not None:
            loadout.ops.remove(read)
            for change_key in [k for k, change in loadout.changes.items() if change.op_uid == read.uid]:
                del loadout.changes[change_key]
        loadout.taken_out.append([*entry[:4], keeper, *entry[5:]])
    elif key == "held_edits":
        loadout.held_edits.append({**entry, "kept_by": keeper})


def _switch(loadout: Loadout, op: Op, on: bool, keeper: Optional[str]) -> None:
    op.active = on
    op.kept_by = keeper
    for change in loadout.changes.values():
        if change.op_uid == op.uid:
            change.active = on


def in_load_order(ops: List[Any]) -> List[Any]:
    """``ops`` with the ops of yours that stand at one place among theirs
    in the order their keepers load: a patch's, then the loadout's own
    mod's, which loads after it.  Everything else stays where it is."""
    places: Dict[int, List[int]] = {}
    for at, op in enumerate(ops):
        if getattr(op, "authored", False) and keeper_of(op) is not None:
            places.setdefault(op.order, []).append(at)
    out = list(ops)
    for spots in places.values():
        if len(spots) < 2:
            continue
        mine = sorted((ops[at] for at in spots), key=lambda op: keeper_of(op) == OWN)
        for at, op in zip(spots, mine, strict=True):
            out[at] = op
    return out
