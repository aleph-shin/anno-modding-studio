"""Undo and redo for what a mod would write out.

One history for every place that can change it: a value typed in the asset
editor, a field added or removed there, a change ticked off or reverted in the
changes panel, a whole mod switched off or unloaded.  Two histories -- one per
panel -- would make Ctrl+Z mean something different depending on where the
focus happens to be, when both panels are editing the same mod.

A step is recorded as what moved rather than as a snapshot of everything.  A
loadout holding a mods folder has seven thousand changes in it, and a step that
switched one of them off is one change's worth of memory, not seven thousand.
The comparison that finds what moved is a pass over the change set against
frozen copies taken the last time -- about as quick as the grouping the changes
panel already does on every edit.

Every step ends with the ModOps the Studio writes for the loadout's own mod
written for what the step did (:func:`.narrow.write`): those are the mod's
record, and a step moves them as it moves the changes they are written for,
and puts both back together.

What is deliberately *not* a step: reading mods in, re-reading one at other
settings, making a patch, restoring a version from history.  Those read disk or
rearrange which mod a change belongs to, and are undone by doing them again --
so they end the history rather than join it (:meth:`UndoHistory.reset`),
instead of leaving an undo behind that half-reverses a folder read.

Nor is choosing another profile.  It rewrites every layer's switch, and none
of that is an edit: the switches were the other profile's all along.  What a
switch step does is kept with the profile it was made in, so that undoing it
after choosing another takes it back *there*, rather than flipping the same
mod in whichever profile happens to be chosen now.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import inside, narrow
from .changes import Change, ChangeKey, Loadout, ModLayer, Op, Profile
from .i18n import tr, tr_stored, trn

# How far back it goes. Each step holds only what moved, so this is about how
# far anybody plausibly wants to walk back, not about memory.
LIMIT = 200


def _frozen_op(op: Op) -> Op:
    """A copy of an op that nothing else holds, lists included."""
    return Op(
        **{
            **vars(op),
            "matched": list(op.matched),
            "unread_at": list(op.unread_at),
            "folded": [list(key) for key in op.folded],
            "within": list(op.within),
            "tool": dict(op.tool),
        }
    )


def _lower(reach: Dict[str, int], origin: str, order: int) -> None:
    reach[origin] = min(reach.get(origin, order), order)


def _moved_ops(was: List[Op], now: List[Op]) -> Dict[str, int]:
    """The mods whose ops are not what they were, each with the place in
    its file of the first that is not."""
    before = {op.uid: op for op in was}
    reach: Dict[str, int] = {}
    seen = set()
    for op in now:
        seen.add(op.uid)
        old = before.get(op.uid)
        if old is None or vars(old) != vars(op):
            for one in (old, op):
                if one is not None:
                    _lower(reach, one.origin, one.order)
    for op in was:
        if op.uid not in seen:
            _lower(reach, op.origin, op.order)
    return reach


def _reordered(places) -> set:
    """The mods some of whose ops or changes now stand in another order
    among each other, from ``(mod, place before, place now)`` for each that
    is there both times.

    Making room moves every later one on by the same amount, which keeps
    their order: an op or change of such a mod that only moved along does
    what it did.  Two swapped do not, as what they add to an asset comes in
    the other order."""
    by_mod: Dict[str, Dict[int, set]] = {}
    for origin, was, now in places:
        by_mod.setdefault(origin, {}).setdefault(was, set()).add(now)
    reordered = set()
    for origin, places_of in by_mod.items():
        last = None
        for was in sorted(places_of):
            now = places_of[was]
            if len(now) > 1 or (last is not None and min(now) <= last):
                reordered.add(origin)
                break
            (last,) = now
    return reordered


def _assets_of_ops(
    was: List[Op], now: List[Op], assets: Dict[str, set], wide: set, reordered: set
) -> None:
    """Add to ``assets`` the assets each op that is not what it was answered
    to, before and now, by mod; and to ``wide`` the mods where one reaches
    further than that: a group, which reaches whatever the ops in it do, or
    one the Studio's reading of is known to be short -- a group kept whole
    with ops inside it that could not be read, say.

    An op that only moved along its file to make room is not among them:
    it does what it did -- unless its mod is among ``reordered``, where it
    may now stand before one it stood after."""

    def add(one: Op) -> None:
        if one.scope or one.unread_at or one.caveat:
            wide.add(one.origin)
        assets.setdefault(one.origin, set()).update(str(guid) for guid in one.matched)

    before = {op.uid: op for op in was}
    seen = set()
    for op in now:
        seen.add(op.uid)
        old = before.get(op.uid)
        if old is None:
            add(op)
        elif (vars(old) != vars(op) and op.origin in reordered) or (
            _but_for_its_place(old) != _but_for_its_place(op)
        ):
            add(old)
            add(op)
    for op in was:
        if op.uid not in seen:
            add(op)


def _moved_unread(was: list, now: list) -> Dict[str, int]:
    """:func:`_moved_ops` for the ops carried unread, which are known by
    which entry each is."""
    reach: Dict[str, int] = {}
    for these, those in ((was, now), (now, was)):
        held = {id(entry) for entry in those}
        for entry in these:
            if id(entry) not in held:
                _lower(reach, entry.origin, entry.order)
    return reach


def _moved_mods(before: List[Op], after: List[Op]) -> frozenset:
    """The mods whose ops are not the same in ``after`` as in ``before``."""
    def by_mod(ops: List[Op]) -> Dict[str, list]:
        held: Dict[str, list] = {}
        for op in ops:
            held.setdefault(op.origin, []).append(vars(op))
        return held

    was, now = by_mod(before), by_mod(after)
    return frozenset(origin for origin in set(was) | set(now) if was.get(origin) != now.get(origin))


def _of_its_mods(target: list, live: list, mods) -> list:
    """``target`` for the mods in ``mods`` and ``live`` for every other one,
    each mod where ``target`` has it."""
    held: Dict[str, list] = {}
    for one in live:
        if one.origin not in mods:
            held.setdefault(one.origin, []).append(one)
    out: list = []
    placed = set()
    for one in target:
        if one.origin in mods:
            out.append(one)
        elif one.origin not in placed:
            placed.add(one.origin)
            out.extend(held.get(one.origin, ()))
    for origin, ones in held.items():
        if origin not in placed:
            out.extend(ones)
    return out


@dataclass
class Step:
    """One undoable act: each thing it moved, as it was and as it became.

    ``changes`` maps a change's key to ``(before, after)``, either of which is
    ``None`` where the change did not exist.  The other fields are ``None``
    unless the step moved them.  Everything held here is a copy nobody else
    holds, and is never modified -- putting one back puts back a copy of it.

    ``ops`` and ``unread`` are every mod's, and are put back for the mods the
    step moved them in and no other (``ops_of``, ``unread_of``).  A mod the
    step did not touch may have been read again since it was made
    (:mod:`.stale`), and what it holds now is later than anything here.
    """

    label: str = ""
    changes: Dict[ChangeKey, Tuple[Optional[Change], Optional[Change]]] = field(
        default_factory=dict
    )
    ops: Optional[Tuple[List[Op], List[Op]]] = None
    # The ModOps the Studio writes for your mods (Loadout.narrow), where
    # any moved: all of them, as they were and as they became, and the mods
    # whose moved.
    narrow: Optional[Tuple[List[Op], List[Op]]] = None
    narrow_of: frozenset = frozenset()
    # The loaded mods themselves, where one arrived or went; and which of them
    # were switched on, where that moved.
    layers: Optional[Tuple[List[ModLayer], List[ModLayer]]] = None
    switches: Optional[Tuple[Dict[str, bool], Dict[str, bool]]] = None
    unread: Optional[Tuple[list, list]] = None
    # Which ops of loaded mods are remembered as taken out here.
    taken_out: Optional[Tuple[list, list]] = None
    # Which edits an update left waiting to be settled (core.held).
    held_edits: Optional[Tuple[list, list]] = None
    # The profile the step was made in: whose switches ``switches`` are, and
    # whose a mod that ``layers`` brings back had on. The object, not its
    # name, so a rename does not lose it.
    profile: Optional[Profile] = None
    # The mods whose ops, and whose ops carried unread, the step moved.
    ops_of: frozenset = frozenset()
    unread_of: frozenset = frozenset()
    # Each mod the step changed what is written for, with the place in its
    # file of the first thing that moved: -1 for all of it.
    reach: Dict[str, int] = field(default_factory=dict)
    # And the assets what it changed is about, by mod, where that can be
    # said; a mod of ``reach`` that is not here is one where it cannot: a
    # group moved, or an op the Studio does not read.
    assets: Dict[str, frozenset] = field(default_factory=dict)
    # For an Apply in the XML view: what was typed, as ``(which pane, the
    # asset, the mod, the text)``.  Undoing it puts the text back in that
    # pane as text typed and not applied.  Set by the window, and no part of
    # what the step moved.
    typed: Optional[Tuple[str, str, str, str]] = None

    @property
    def is_empty(self) -> bool:
        return not self.changes and all(
            part is None
            for part in (self.ops, self.narrow, self.layers, self.switches, self.unread,
                         self.taken_out, self.held_edits)
        )

    def asset(self) -> str:
        """The GUID of the one asset the step is about, or "" for several.

        So that undoing an edit made to some other asset can show that asset,
        rather than change something off screen.
        """
        from . import effective

        guids = set()
        for before, after in self.changes.values():
            change = after or before
            if change is None:
                continue
            guids.add(
                effective.new_asset_guid(change) if change.creates_asset else change.guid
            )
        return guids.pop() if len(guids) == 1 else ""


def _but_for_its_place(thing) -> dict:
    return {**vars(thing), "order": 0}


def touched(step: Step) -> Tuple[set, set, set]:
    """What ``step`` added or changed, as the rows of a list of the mod's
    changes know them: ``(the keys of changes, the uids of ops, (mod, place)
    for each op carried unread)``.  Not what it took away, and not what only
    moved along the file to make room for something new.
    """
    keys = {
        key for key, (before, after) in step.changes.items()
        if after is not None
        and (before is None or _but_for_its_place(before) != _but_for_its_place(after))
    }
    uids: set = set()
    if step.ops is not None:
        was = {op.uid: op for op in step.ops[0]}
        for op in step.ops[1]:
            held = was.get(op.uid)
            if op.origin in step.ops_of and (
                held is None or (held.source, held.active) != (op.source, op.active)
            ):
                uids.add(op.uid)
    unread: set = set()
    if step.unread is not None:
        had = [(entry.origin, entry.source) for entry in step.unread[0]]
        for entry in step.unread[1]:
            said = (entry.origin, entry.source)
            if said in had:
                had.remove(said)
            elif entry.origin in step.unread_of:
                unread.add((entry.origin, entry.order))
    return keys, uids, unread


def _about(change: Change) -> set:
    """The assets a change is about: the one it is aimed at, and the one it
    makes where it makes one."""
    from . import effective

    found = {str(change.guid)}
    if change.creates_asset:
        found.add(str(effective.new_asset_guid(change)))
    return found


def _named(change: Change) -> str:
    what = change.tag or tr_stored(change.summary) or tr("a field")
    if not change.asset_label:
        return what
    # Translators: a field on an asset, as in "Undo the change to Amount on
    # Farmer Residence".
    return tr("%(what)s on %(asset)s", what=what, asset=tr_stored(change.asset_label))


def _only_switched(before: Change, after: Change) -> bool:
    return {**vars(before), "active": True} == {**vars(after), "active": True}


def held_in(loadout: Loadout, step: Step) -> List[str]:
    """The mods of yours whose project is held from this loadout
    (:meth:`.changes.Loadout.held`) that ``step`` changed the work of: their
    own changes and ops, the ModOps the Studio writes for them, and an edit
    one keeps inside somebody else's ops made or changed.  None of it can be
    saved into a project held so, and what is read of it when the project
    is found again takes its place.

    Not a mod of somebody else's unloaded, or an edit of theirs reverted,
    that takes with it what one of them kept inside it: that is no work
    added to the held mod.
    """
    held = {origin for origin, ref in loadout.projects.items() if ref.held}
    if not held:
        return []
    found = set()
    for before, after in step.changes.values():
        for change in (before, after):
            if change is not None and change.origin in held:
                found.add(change.origin)
        if after is not None and after.kept_by in held:
            found.add(after.kept_by)
    if step.ops is not None:
        was = {op.uid: op for op in step.ops[0] if op.origin in step.ops_of}
        now = {op.uid: op for op in step.ops[1] if op.origin in step.ops_of}
        for uid in set(was) | set(now):
            op_was, op_now = was.get(uid), now.get(uid)
            if op_was == op_now:
                continue
            for op in (op_was, op_now):
                if op is not None and op.origin in held:
                    found.add(op.origin)
            if op_now is not None and op_now.kept_by in held:
                found.add(op_now.kept_by)
    if step.narrow is not None:
        found |= set(step.narrow_of) & held
    if step.taken_out is not None:
        new = [entry for entry in step.taken_out[1] if entry not in step.taken_out[0]]
        found |= {entry[0] for entry in new if entry[0] in held}
        found |= {entry[4] for entry in new if len(entry) > 4 and entry[4] in held}
    return sorted(found, key=loadout.layer_rank)


def describe(loadout: Loadout, step: Step) -> str:
    """What the step did, to finish "Undo …" and "Redo …" with."""
    pairs = list(step.changes.values())

    # A mod unloaded with nothing in it moves no change, only the mod.
    if step.layers is not None and not pairs:
        had, has = step.layers
        gone = [layer for layer in had if not any(layer is x for x in has)]
        if len(gone) == 1:
            # Translators: finishes "Undo …" in the Edit menu.
            return tr("unloading %s", gone[0].label)
        if gone:
            # Translators: finishes "Undo …" in the Edit menu.
            return trn("unloading one mod", "unloading %(n)s mods", len(gone))

    if step.switches is not None and not pairs:
        was, now = step.switches
        moved = [o for o in now if o in was and was[o] != now[o]]
        if len(moved) == 1:
            layer = loadout.layer(moved[0])
            name = layer.label if layer is not None else moved[0]
            if now[moved[0]]:
                # Translators: finishes "Undo …" in the Edit menu.
                return tr("switching %s on", name)
            # Translators: finishes "Undo …" in the Edit menu.
            return tr("switching %s off", name)
        if moved:
            # Translators: finishes "Undo …" in the Edit menu.
            return trn("switching one mod", "switching %(n)s mods", len(moved))

    if pairs:
        count = len(pairs)
        switched = [
            (b, a) for b, a in pairs if b is not None and a is not None and _only_switched(b, a)
        ]
        if len(switched) == count:
            states = {a.active for _b, a in switched}
            if len(states) > 1:
                # Translators: finishes "Undo …" in the Edit menu.
                return trn("switching one change on and off",
                           "switching %(n)s changes on and off", count)
            on = states.pop()
            if count == 1:
                name = _named(switched[0][1])
                # Translators: finishes "Undo …" in the Edit menu.
                return tr("switching %s on", name) if on else tr("switching %s off", name)
            if on:
                # Translators: finishes "Undo …" in the Edit menu.
                return trn("switching one change on", "switching %(n)s changes on", count)
            # Translators: finishes "Undo …" in the Edit menu.
            return trn("switching one change off", "switching %(n)s changes off", count)
        reverted = [b for b, a in pairs if a is None and b is not None]
        if len(reverted) == count:
            if count == 1:
                # Translators: finishes "Undo …" in the Edit menu.
                return tr("reverting %s", _named(reverted[0]))
            # Translators: finishes "Undo …" in the Edit menu.
            return trn("reverting one change", "reverting %(n)s changes", count)
        before, after = pairs[0]
        if count == 1 and after is not None:
            if before is None and after.creates_asset:
                # Translators: finishes "Undo …" in the Edit menu.
                return tr("creating a new asset")
            # Translators: finishes "Undo …" in the Edit menu.
            return tr("the change to %s", _named(after))
        # Translators: finishes "Undo …" in the Edit menu.
        return trn("one change", "%(n)s changes", count)

    if step.ops is not None:
        # Translators: finishes "Undo …" in the Edit menu.
        return tr("the ModOp change")
    # Translators: finishes "Undo …" in the Edit menu.
    return tr("the last change")


class UndoHistory:
    """The steps behind Edit ▸ Undo and Edit ▸ Redo, for one loadout.

    Nothing is recorded on its own: whoever changes the loadout says when an
    act is finished by calling :meth:`commit`, and whatever moved since the
    last call is that act.  That is what lets a burst -- ticking a group box
    walks every change under it one at a time -- come back as one step.
    """

    def __init__(self, loadout: Loadout, limit: int = LIMIT):
        self.loadout = loadout
        self.limit = limit
        self._done: List[Step] = []
        self._undone: List[Step] = []
        self._rebase()

    # -- what it looked like last time -------------------------------------
    def _rebase(self) -> None:
        loadout = self.loadout
        # A loadout taken on as it is: one saved before the Studio kept its
        # ModOps has its written here, and that is no step.  So has who keeps
        # each edit inside somebody else's ops.
        inside.assign(loadout)
        narrow.write(loadout)
        self._changes = {key: copy.copy(c) for key, c in loadout.changes.items()}
        self._ops = [_frozen_op(op) for op in loadout.ops]
        self._narrow = [_frozen_op(op) for op in loadout.narrow]
        self._layers = list(loadout.layers)
        self._switches = {layer.origin: layer.active for layer in loadout.layers}
        self._unread = list(loadout.unread)
        self._taken_out = list(loadout.taken_out)
        self._held_edits = [dict(held) for held in loadout.held_edits]
        self._profile = loadout.chosen_profile()

    def _scan(self, label: str = "") -> Step:
        """What moved since last time, taking now as the new last time."""
        loadout = self.loadout
        # The act is over: what it did to the own mod's changes is written as
        # its ModOps before anything is compared, so the step holds both, and
        # each edit it made inside somebody else's ops has its keeper.
        inside.assign(loadout)
        narrow.write(loadout)
        step = Step(label, profile=self._profile)
        assets: Dict[str, set] = {}
        wide: set = set()
        # Another profile chosen since last time: every switch that differs
        # is that profile's, not a step. Taken as the new last time, as is.
        chosen = loadout.chosen_profile()
        if chosen is not self._profile:
            self._profile = step.profile = chosen
            self._switches = {layer.origin: layer.active for layer in loadout.layers}

        base = self._changes
        live = loadout.changes
        ops = loadout.ops
        # Whose ops or changes stand in another order among each other now,
        # rather than only moved along to make room: see _reordered.
        had = {op.uid: op for op in self._ops}
        reordered = _reordered(
            [
                (change.origin, base[key].order, change.order)
                for key, change in live.items()
                if key in base and base[key].origin == change.origin
            ]
            + [
                (op.origin, had[op.uid].order, op.order)
                for op in ops
                if op.uid in had and had[op.uid].origin == op.origin
            ]
        )
        now = {}
        for key, change in live.items():
            frozen = base.get(key)
            # Compared rather than trusted: a payload edit changes the change
            # object in place, so the same object can say something new.
            if frozen is not None and frozen.__dict__ == change.__dict__:
                now[key] = frozen
            else:
                fresh = copy.copy(change)
                now[key] = fresh
                step.changes[key] = (frozen, fresh)
                along = (
                    frozen is not None
                    and fresh.origin not in reordered
                    and _but_for_its_place(frozen) == _but_for_its_place(fresh)
                )
                for one in (frozen, fresh):
                    if one is not None:
                        _lower(step.reach, one.origin, one.order)
                        if not along:
                            assets.setdefault(one.origin, set()).update(_about(one))
        for key, frozen in base.items():
            if key not in live:
                step.changes[key] = (frozen, None)
                _lower(step.reach, frozen.origin, frozen.order)
                assets.setdefault(frozen.origin, set()).update(_about(frozen))
        self._changes = now

        if len(ops) != len(self._ops) or any(
            vars(a) != vars(b) for a, b in zip(ops, self._ops, strict=True)
        ):
            kept = [_frozen_op(op) for op in ops]
            moved = _moved_ops(self._ops, kept)
            # The same ops, each mod's in the order it had them: only where
            # one mod's stand among another's is other, which is nothing.
            if moved:
                step.ops = (self._ops, kept)
                step.ops_of = frozenset(moved)
                for origin, order in moved.items():
                    _lower(step.reach, origin, order)
                _assets_of_ops(self._ops, kept, assets, wide, reordered)
            self._ops = kept

        written = loadout.narrow
        if len(written) != len(self._narrow) or any(
            vars(a) != vars(b) for a, b in zip(written, self._narrow, strict=True)
        ):
            kept = [_frozen_op(op) for op in written]
            step.narrow = (self._narrow, kept)
            step.narrow_of = _moved_mods(self._narrow, kept)
            self._narrow = kept

        # The layers by identity: a mod's details can be edited on the Export
        # tab, and that is not an act on the change set. Only its arriving,
        # going and switch are.
        layers = list(loadout.layers)
        if len(layers) != len(self._layers) or any(
            a is not b for a, b in zip(layers, self._layers, strict=True)
        ):
            step.layers = (self._layers, layers)
            self._layers = layers
        switches = {layer.origin: layer.active for layer in layers}
        if switches != self._switches:
            step.switches = (self._switches, switches)
            self._switches = switches

        unread = list(loadout.unread)
        if len(unread) != len(self._unread) or any(
            a is not b for a, b in zip(unread, self._unread, strict=True)
        ):
            moved = _moved_unread(self._unread, unread)
            if moved:
                step.unread = (self._unread, unread)
                step.unread_of = frozenset(moved)
                # What an op the Studio could not read does is not known.
                wide.update(moved)
                for origin, order in moved.items():
                    _lower(step.reach, origin, order)
            self._unread = unread
        taken_out = list(loadout.taken_out)
        if taken_out != self._taken_out:
            step.taken_out = (self._taken_out, taken_out)
            for these, those in ((self._taken_out, taken_out), (taken_out, self._taken_out)):
                for taken in these:
                    if taken not in those:
                        # All of it, unless the op that went says where.
                        step.reach.setdefault(taken[0], -1)
            self._taken_out = taken_out
        held_edits = [dict(held) for held in loadout.held_edits]
        if held_edits != self._held_edits:
            step.held_edits = (self._held_edits, held_edits)
            self._held_edits = held_edits
        step.assets = {
            origin: frozenset(assets.get(origin, ()))
            for origin in step.reach if origin not in wide
        }
        return step

    # -- recording ----------------------------------------------------------
    def commit(self, label: str = "") -> Optional[Step]:
        """Record whatever moved since last time as one step, if anything did."""
        step = self._scan(label)
        if step.is_empty:
            return None
        if not step.label:
            step.label = describe(self.loadout, step)
        self._done.append(step)
        del self._done[: -self.limit]
        # A new act after stepping back is a new branch; the steps it replaces
        # cannot be redone on top of it.
        self._undone.clear()
        return step

    def take_back(self) -> Optional[Step]:
        """Undo the last step and forget it, so no Redo makes it again: for
        a step that should not have been made (:func:`held_in`)."""
        step = self.undo()
        if step is not None:
            self._undone.pop()
        return step

    def reset(self) -> None:
        """Forget every step, and take the loadout as it is now as the start."""
        self._done.clear()
        self._undone.clear()
        self._rebase()

    def absorb(self) -> None:
        """Take whatever moved since last time as no step at all, and keep
        every step there is.

        For mods read again after an edit (:mod:`.stale`): what they come to
        now is nobody's act to take back, and the steps before it still say
        what each did to the mod it was made in.
        """
        self._scan()

    # -- walking it ----------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def last_step(self) -> Optional[Step]:
        """The step Undo would take back, or None."""
        return self._done[-1] if self._done else None

    @property
    def undo_label(self) -> str:
        return self._done[-1].label if self._done else ""

    @property
    def redo_label(self) -> str:
        return self._undone[-1].label if self._undone else ""

    def undo(self) -> Optional[Step]:
        if not self._done:
            return None
        # Anything that moved without being recorded is kept as it is, rather
        # than being folded into -- and reversed along with -- this step.
        self._scan()
        step = self._done.pop()
        self._apply(step, backwards=True)
        self._undone.append(step)
        return step

    def redo(self) -> Optional[Step]:
        if not self._undone:
            return None
        self._scan()
        step = self._undone.pop()
        self._apply(step, backwards=False)
        self._done.append(step)
        return step

    def _apply(self, step: Step, backwards: bool) -> None:
        loadout = self.loadout
        pick = 0 if backwards else 1
        for key, pair in step.changes.items():
            target = pair[pick]
            loadout.changes.pop(key, None)
            if target is None:
                self._changes.pop(key, None)
            else:
                loadout.changes[key] = copy.copy(target)
                self._changes[key] = target
        if step.ops is not None:
            # Of the mods the step moved them in.  Every other mod keeps the
            # ops it has now, which undo and redo have just taken as they are.
            ops = _of_its_mods(step.ops[pick], self._ops, step.ops_of)
            back = {op.uid for op in ops if op.origin in step.ops_of}
            going = {
                op.uid for op in self._ops
                if op.origin in step.ops_of and op.uid not in back
            }
            loadout.ops = [_frozen_op(op) for op in ops]
            self._ops = ops
            if going:
                # What an op that goes came to goes with it.  The step holds
                # that as it was when it was made, and the mod may have been
                # read again since: an answer the step never saw would be
                # left under no op, and written out as a change of its own.
                for key in [
                    key for key, change in loadout.changes.items()
                    if change.op_uid in going and key not in step.changes
                ]:
                    del loadout.changes[key]
                    self._changes.pop(key, None)
        if step.narrow is not None:
            # The mods the step moved, and every other as it is now: they
            # are written again for each step, and a step another mod took
            # since is not this one's to take back.
            put = _of_its_mods(step.narrow[pick], loadout.narrow, step.narrow_of)
            loadout.narrow = [_frozen_op(op) for op in put]
            self._narrow = list(loadout.narrow)
        chosen = loadout.chosen_profile()
        # The profile the step was made in, when that is not the one chosen.
        elsewhere = step.profile if step.profile is not chosen else None
        if step.layers is not None:
            was = {id(layer) for layer in loadout.layers}
            loadout.layers = list(step.layers[pick])
            self._layers = list(loadout.layers)
            if elsewhere is not None:
                # A mod coming back that went while another profile was
                # chosen carries that profile's switch: it goes back there,
                # and here the mod is as this profile last had it.
                for layer in loadout.layers:
                    if id(layer) not in was:
                        elsewhere.switches[layer.origin] = layer.active
                        layer.active = chosen.switches.get(layer.origin, False)
        if step.switches is not None:
            switched = step.switches[pick]
            if elsewhere is not None:
                # The profile it was made in, which is not the one chosen --
                # and only what the step moved: the rest of that profile is
                # as it was left, which may be later than the step.
                other = step.switches[1 - pick]
                elsewhere.switches.update(
                    {origin: on for origin, on in switched.items() if other.get(origin) != on}
                )
            else:
                for layer in loadout.layers:
                    if layer.origin in switched:
                        layer.active = switched[layer.origin]
        self._switches = {layer.origin: layer.active for layer in loadout.layers}
        if step.unread is not None:
            loadout.unread = _of_its_mods(step.unread[pick], loadout.unread, step.unread_of)
            self._unread = list(loadout.unread)
        if step.taken_out is not None:
            loadout.taken_out = list(step.taken_out[pick])
            self._taken_out = list(loadout.taken_out)
        if step.held_edits is not None:
            loadout.held_edits = [dict(held) for held in step.held_edits[pick]]
            self._held_edits = [dict(held) for held in loadout.held_edits]
