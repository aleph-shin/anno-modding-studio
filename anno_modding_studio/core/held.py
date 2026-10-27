"""Edits of yours that an update left without the op they were made to.

Your edits to somebody else's mod are kept by the op of theirs each was made
to: one taken out is remembered by its text (:attr:`Loadout.taken_out`),
one typed over is that and what of yours stands in its place, one switched
off is that op, switched off.  When the author updates the mod and the new
version is read in, each of those ops is looked for again
(docs/proposals/patches-proposal.md, section 6):

* **where it was, unchanged** -- the edit applies, and nothing is said;
* **unchanged, somewhere else** -- in another group, say -- the edit
  applies where it now is, and that is said once;
* **changed** -- an op with the same ``ModOpID``, or the same type aimed at
  the same thing, says something else now -- the edit is **held**;
* **gone** -- nothing like it is left -- the edit is held too.

An edit held is not applied: what of yours stood in place of their op is
switched off, so the game gets their new version, and nothing is dropped.
It waits in :attr:`Loadout.held_edits` until you say what becomes of it,
with their op as it was, as it is now, and yours side by side
(:func:`apply_mine`, :func:`keep_theirs`).

:func:`stock` is taken before the new reading lands, while the ops it is
about are still the loadout's; :func:`settle` after.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from . import inside
from .changes import Loadout
from .modops import SourceOp, read_op

REPLACED = "replaced"        # typed over: theirs out, yours in its place
TAKEN_OUT = "taken out"
SWITCHED_OFF = "switched off"
CHANGED = "changed"
GONE = "gone"


def _modopid(op: SourceOp) -> str:
    return next((value for name, value in op.attrs.items() if name.lower() == "modopid"), "")


def same_op(one: str, other: str) -> bool:
    """Whether two writings of an op are the same op of the mod, one version
    to the next: the same ``ModOpID``, or -- with none -- the same type of op
    aimed at the same thing, by GUID and path.

    Read as the import reads them, so that Anno 117's ``Merge="//A"`` is the
    op ``Type="merge" Path="//A"`` is: a pattern over the tag missed that
    shorthand, and a ``>`` or an entity in a path."""
    a, b = read_op(one), read_op(other)
    if a is None or b is None:
        return False
    if _modopid(a) or _modopid(b):
        return _modopid(a) == _modopid(b)
    if not (a.guid or a.path):
        return False
    return (a.kind, a.guid, a.path) == (b.kind, b.guid, b.path)


# -- before ---------------------------------------------------------------------------
def stock(loadout: Loadout, origins: Iterable[str]) -> Dict[str, list]:
    """For each of ``origins``, every op of theirs an edit of yours is made
    to: ``{"kind", "source", "order", "yours": [...], "active": [...],
    "kept_by"}``, ``yours`` what stands in its place (``["op", uid]`` or
    ``["change", key]``), ``active`` whether each was on, and ``kept_by`` the
    mod of yours that keeps the edit (:mod:`.inside`)."""
    # A fork's ops are its own, and an update of the mod it was made of no
    # longer reaches it (core.forking).
    wanted = {origin for origin in origins
              if not getattr(loadout.layer(origin), "fork_of", "")}
    found: Dict[str, list] = {}
    for entry in loadout.taken_out:
        origin, source, _within, *order = entry
        if origin not in wanted:
            continue
        at = int(order[0]) if order else None
        yours: list = []
        active: list = []
        if at is not None:
            for op in loadout.ops:
                if op.origin == origin and op.authored and op.order == at and not op.scope:
                    yours.append(["op", op.uid])
                    active.append(op.active)
            for change in loadout.changes.values():
                if (change.origin == origin and change.authored and not change.op_uid
                        and change.order == at):
                    yours.append(["change", list(change.key)])
                    active.append(change.active)
        found.setdefault(origin, []).append({
            "kind": REPLACED if yours else TAKEN_OUT, "source": source,
            "order": at if at is not None else 0, "yours": yours, "active": active,
            "kept_by": inside.keeper_of(entry),
        })
    for op in loadout.ops:
        if op.origin in wanted and not op.authored and not op.active and op.source and not op.aside:
            found.setdefault(op.origin, []).append({
                "kind": SWITCHED_OFF, "source": op.source, "order": op.order,
                "yours": [], "active": [], "kept_by": op.kept_by,
            })
    return found


# -- after ----------------------------------------------------------------------------
@dataclass
class Settled:
    """What reading a new version did to the edits made to the old one."""

    held: List[dict] = field(default_factory=list)
    # Edits whose op was found somewhere else in the new version, and
    # applied there.
    moved: List[dict] = field(default_factory=list)


def settle(loadout: Loadout, stocks: Dict[str, list], fresh: Dict[str, List[str]]) -> Settled:
    """Look for each op of :func:`stock` in the mods as just read, with
    ``fresh`` the text of every op each of them was read with, and do as
    the module says: apply the edits whose op is there, hold the rest."""
    done = Settled()
    for origin, anchors in stocks.items():
        now = list(fresh.get(origin, ()))
        before = {anchor["source"] for anchor in anchors}
        theirs = [op for op in loadout.ops if op.origin == origin and not op.authored and op.source]
        # How many of each op the new version has that are not taken out:
        # the rest standing were taken out where they were, and the reading
        # could not find them where they are now.
        out = [anchor["source"] for anchor in anchors if anchor["kind"] != SWITCHED_OFF]
        for anchor in anchors:
            source = anchor["source"]
            standing = [op for op in theirs if op.source == source and loadout.op(op.uid) is not None]
            if anchor["kind"] == SWITCHED_OFF:
                if standing:
                    if all(op.active for op in standing):
                        _switch(loadout, standing[0].uid, False, anchor.get("kept_by"))
                    continue
            elif source in now:
                kept = max(0, now.count(source) - out.count(source))
                if len(standing) > kept:
                    # In the new version, but not where it was: taken out
                    # again where it now is.
                    _retake(loadout, origin, source, standing[-1].uid, anchor.get("kept_by"))
                    done.moved.append(anchor)
                continue
            candidates = [op for op in theirs if op.source not in before and same_op(source, op.source)]
            held = {
                "uid": uuid.uuid4().hex[:12], "origin": origin, "kind": anchor["kind"],
                "why": CHANGED if candidates else GONE, "was": source,
                "now": candidates[0].source if candidates else "",
                "now_uid": candidates[0].uid if candidates else "",
                "yours": anchor["yours"], "active": anchor["active"],
            }
            if anchor.get("kept_by") is not None:
                held["kept_by"] = anchor["kept_by"]
            for (kind, which), _on in zip(anchor["yours"], anchor["active"], strict=True):
                _switch_yours(loadout, kind, which, False)
            loadout.held_edits.append(held)
            done.held.append(held)
    return done


def _switch(loadout: Loadout, uid: str, on: bool, kept_by=None) -> None:
    """Switch an op on or off, with what it comes to; one of theirs
    switched off with the mod of yours that keeps that."""
    op = loadout.op(uid)
    if op is None:
        return
    op.active = on
    if not on and kept_by is not None:
        op.kept_by = kept_by
    for change in loadout.changes.values():
        if change.op_uid == uid:
            change.active = on


def _switch_yours(loadout: Loadout, kind: str, which, on: bool) -> None:
    if kind == "op":
        _switch(loadout, str(which), on)
        return
    change = loadout.changes.get(tuple(which))
    if change is not None:
        change.active = on


def _forget_taken_out(loadout: Loadout, origin: str, source: str) -> None:
    """Forget one op of ``origin`` remembered as taken out by its text."""
    for at, entry in enumerate(loadout.taken_out):
        if entry[0] == origin and entry[1] == source:
            loadout.taken_out = loadout.taken_out[:at] + loadout.taken_out[at + 1:]
            return


def _retake(loadout: Loadout, origin: str, source: str, uid: str, kept_by=None) -> None:
    """Take an op out again where it now stands: the old entry goes, and
    taking it out writes the new one, kept by the mod of yours that kept
    the old."""
    _forget_taken_out(loadout, origin, source)
    loadout.drop_op(uid)
    if kept_by is not None and loadout.taken_out and loadout.taken_out[-1][0] == origin:
        entry = loadout.taken_out[-1]
        loadout.taken_out = [*loadout.taken_out[:-1], [*entry[:4], kept_by, *entry[5:]]]


# -- deciding -------------------------------------------------------------------------
def of(loadout: Loadout, origin: Optional[str] = None) -> List[dict]:
    """The edits held back, of the mod ``origin`` or, with None, of every
    one."""
    return [held for held in loadout.held_edits if origin is None or held.get("origin") == origin]


def _done(loadout: Loadout, held: dict) -> None:
    loadout.held_edits = [one for one in loadout.held_edits if one.get("uid") != held.get("uid")]


def apply_mine(loadout: Loadout, held: dict) -> None:
    """Make a held edit to the new version what it was to the old: the new
    op taken out or switched off, and what of yours stood in place of the
    old one switched back on in place of the new.  For an op that is gone,
    yours stands on its own."""
    origin, now = held["origin"], held.get("now_uid") or ""
    if held["kind"] in (REPLACED, TAKEN_OUT):
        _forget_taken_out(loadout, origin, held["was"])
        if now and loadout.op(now) is not None:
            _retake(loadout, origin, "", now, held.get("kept_by"))
    elif held["kind"] == SWITCHED_OFF and now:
        _switch(loadout, now, False, held.get("kept_by"))
    for (kind, which), on in zip(held.get("yours") or (), held.get("active") or (), strict=False):
        _switch_yours(loadout, kind, which, bool(on))
    _done(loadout, held)


def keep_theirs(loadout: Loadout, held: dict) -> None:
    """Let the new version stand as its author wrote it, and drop what of
    yours was made to the old one."""
    origin = held["origin"]
    if held["kind"] in (REPLACED, TAKEN_OUT):
        _forget_taken_out(loadout, origin, held["was"])
    for kind, which in held.get("yours") or ():
        if kind == "op":
            loadout.ops = [op for op in loadout.ops if op.uid != which]
            for change in [c for c in loadout.changes.values() if c.op_uid == which]:
                loadout.changes.pop(change.key, None)
        else:
            loadout.changes.pop(tuple(which), None)
    _done(loadout, held)


def ops_read(ops, unread=()) -> Dict[str, List[str]]:
    """The text of every op a reading brought, by mod: :func:`settle`'s
    ``fresh``."""
    found: Dict[str, List[str]] = {}
    for one in [*ops, *unread]:
        if getattr(one, "source", ""):
            found.setdefault(one.origin, []).append(one.source)
    return found
