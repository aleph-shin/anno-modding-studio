"""Readings that have gone stale: which they are, and reading them again.

What is under an op is what it came to when its mod was last read, against
the document the mods before it left.  A mod arriving before it, one going,
or one read again at other settings changes that document, and nothing in
the reading says so: its ops go on showing what they came to before.  What
is exported is each op as written, so a stale reading is only ever what is
shown -- and this is what notices, says so on the rows, and puts it right.

Three steps, each its own function so the middle one can run off the thread
the loadout lives on:

- :func:`check` measures what loads now against what loaded when it last
  looked, and marks each mod whose reading that could have moved
  (``ModLayer.stale``).  :func:`reached` says which of a marked mod's ops.
- :func:`read_again` reads the marked mods again where they stand in the
  stack, from a :func:`snapshot` of it.
- :func:`take` puts the new readings in place of the old ones, keeping what
  was done to each mod here, and says what moved.

Whether a mod is switched on is not part of it: a stack is read with every
mod in it, on or off, and always has been.

**An edit made here is a change in what loads as well** (:func:`edited`).  An
op typed, switched off, moved or taken out is an op every later one may find
things differently for: the ops of its own mod from there on, and every mod
that loads after it.  Those are marked and read again like the rest.  A mod
that has been edited here is read from what the loadout writes for it and
not from its folder, where the edit is not (:func:`written_here`), and so is
the mod written here, which has no folder at all.  Its reading is put in
place by :func:`modfile.take_reading`, which knows each op of that text for
the op the loadout holds.

**So is another Studio.**  A reading is what this Studio's reader made of a
mod, and a Studio that reads a construct differently would go on showing the
old reading of every saved loadout.  A loadout keeps the
``reader.READER_VERSION`` its readings were made under, and :func:`check`
marks every mod of a loadout saved under another.

**And so is a mod's own folder.**  A mod updated on disk, or built again by
another loadout, is not the mod that was read, and nothing in the stack says
so.  :func:`follow` compares what each mod's modinfo says now with what it
said when it was read: its version, and for a copy the Studio built its build
number, which moves where the version does not.  It reads a file from every
mod's folder, so it is not part of :func:`check`: the window asks for it when
a loadout is opened and before Play.  A mod that moved is read again from its
folder, keeping what was done to it here, like any other mod in doubt.

**And so is a project.**  A mod of yours is kept in a project of its own,
which another loadout may have changed since this one read it: it is opened
as it is now and held (:mod:`.readback`).  :func:`follow_projects` marks it
and every mod after it, and it is read from its project's ModOps where it
stands here, which then take the place of what this loadout read before.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import (
    builds,
    edits,
    inside,
    modfile,
    modfolder,
    modinfo,
    patching,
    readback,
    reader,
)
from . import projects as project_files
from .changes import is_own_mod
from .i18n import tr

# Why a reading may be out of date; each goes with the mod it is about.
ADDED = "added"      # a mod loads before it that did not when it was read
CHANGED = "changed"  # a mod before it was read again: other settings, or imported again
GONE = "gone"        # a mod that loaded before it no longer does
MOVED = "moved"      # the mods before it load in another order
ALONE = "alone"      # it was read on its own, with mods loading before it
EDITED = "edited"    # a mod before it, or it itself, was edited here
READER = "reader"    # it was read by a Studio that read mods differently
UPDATED = "updated"  # a mod before it, or it itself, holds another version on disk
RESTORED = "restored"  # restoring the loadout put it back, or set back its settings
PROJECT = "project"  # a mod before it, or it itself, was changed in its project by another loadout
LIBRARY = "library"  # a mod before it, or it itself, is read from its library build: its project is missing
KEPT = "kept"        # a mod before it, or it itself, holds edits a project another loadout changed keeps


def stack(loadout) -> List[List[str]]:
    """What a reading of the loadout's mods reads, in order: each mod with a
    folder, and the settings it is read at.

    Nothing else of a mod: its name and version are yours to edit on the
    Export tab where the mod is yours, and typing there is not a change in
    what loads.
    """
    return [
        [layer.origin, json.dumps(layer.options or {}, sort_keys=True, default=str)]
        for layer in sorted(loadout.layers, key=lambda layer: layer.order)
        if layer.folder
    ]


def _every(loadout) -> tuple:
    """Every mod's record, the loaded mods' and then the own mod's, made or
    not: where each keeps why its reading is in doubt."""
    return (*loadout.layers, loadout.own)


def _mark(layer, kind: str, about: str, anyway: bool = False) -> bool:
    # A patch of yours is never in doubt: what it holds is what you wrote
    # here, and its folder is only where that was last written out.  Unless
    # it is read from that folder (``anyway``), its project being missing.
    if (layer.patches and not anyway) or [kind, about] in layer.stale:
        return False
    layer.stale.append([kind, about])
    return True


def check(loadout) -> List[str]:
    """Mark the mods whose reading a change in what loads could have moved;
    the origins newly marked.

    Measured against the stack as it was the last time this looked, which it
    then takes as it is now.  The first look at a loadout marks nothing:
    there is nothing to measure against.

    Before that, the reader: a loadout whose readings were made under
    another ``reader.READER_VERSION`` -- saved by a Studio that read mods
    differently, or before the number was kept -- has every mod it read
    from a folder marked (:func:`read_by_another`).
    """
    marked = read_by_another(loadout)
    now = stack(loadout)
    was = loadout.read_stack
    loadout.read_stack = now
    if was is None or was == now:
        return marked
    before = {entry[0]: entry[1:] for entry in was}
    order = [entry[0] for entry in now]
    here = {entry[0]: entry[1:] for entry in now}
    # Where in today's order each change is felt from, and what it was.
    felt = []
    for place, origin in enumerate(order):
        if origin not in before:
            felt.append((place + 1, ADDED, origin))
        elif before[origin] != here[origin]:
            felt.append((place + 1, CHANGED, origin))
    for place, entry in enumerate(was):
        if entry[0] in here:
            continue
        # After whichever of the mods that were before it is still here.
        ahead = [order.index(other[0]) for other in was[:place] if other[0] in here]
        felt.append((max(ahead) + 1 if ahead else 0, GONE, entry[0]))
    stayed_was = [entry[0] for entry in was if entry[0] in here]
    stayed_now = [origin for origin in order if origin in before]
    for old, new in zip(stayed_was, stayed_now, strict=True):
        if old != new:
            felt.append((order.index(new), MOVED, ""))
            break
    layers = {layer.origin: layer for layer in loadout.layers}
    for place, origin in enumerate(order):
        layer = layers[origin]
        for start, kind, about in felt:
            if place >= start and _mark(layer, kind, about) and origin not in marked:
                marked.append(origin)
    return marked


def read_by_another(loadout) -> List[str]:
    """Where the loadout's readings were made under another
    ``reader.READER_VERSION`` than this Studio's, mark every mod it read
    from a folder, and take this Studio's number as the loadout's; the
    origins newly marked.

    The number moves at once, before anything is read again: what is in
    doubt is held by the marks, which are saved with the loadout, so a
    reading that never finishes is asked for again the next time.  A
    loadout made in this run only takes the number.
    """
    was = loadout.read_by
    loadout.read_by = reader.READER_VERSION
    if was is None or was == reader.READER_VERSION:
        return []
    return [
        entry[0] for entry in stack(loadout)
        if _mark(loadout.layer(entry[0]), READER, "")
    ]


def follow(loadout) -> List[str]:
    """Mark each mod whose folder holds another version or build than the
    one it was read at (:func:`builds.updated_on_disk`), and every mod after it;
    the origins newly marked.

    Each updated mod takes what its folder's modinfo says at once, before
    it is read again (:func:`_follow`): what is in doubt is held by the
    marks, which are saved with the loadout, so a reading that never
    finishes is asked for again the next time.  Its reading is then taken
    from its folder, whatever was done to it here (:func:`snapshot`), and
    :func:`take` keeps what was.

    A mod whose folder is gone is left as it is: there is nothing to
    compare, and nothing to read it again from.
    """
    order = [entry[0] for entry in stack(loadout)]
    marked: List[str] = []
    for place, origin in enumerate(order):
        layer = loadout.layer(origin)
        folder = Path(layer.folder)
        # A mod of yours whose project is missing is read from its library
        # build (:func:`follow_projects`), and followed as that.
        standing = _stands_in(loadout, origin)
        if (layer.patches and not standing) or not folder.is_dir():
            continue
        if loadout.held(origin) == project_files.MOVED:
            # Read from its project, which says what it is now.
            continue
        if origin in loadout.projects and not standing:
            # Nor is a mod of yours with a project read from its build: that
            # build moving -- Update from this loadout or another -- is no
            # update, and the folder's ops are the project's own written
            # out.  Read as the mod's, beside the ops and ModOps the project
            # holds, the next save wrote each of them into it twice (R3-40,
            # hands-on run 3).
            continue
        if not builds.updated_on_disk(loadout, layer, folder):
            continue
        _follow(loadout, layer, modfolder.metadata_of(folder), theirs=standing)
        for later in order[place:]:
            newly = _mark(loadout.layer(later), UPDATED, origin, anyway=standing and later == origin)
            if newly and later not in marked:
                marked.append(later)
    return marked


def _stands_in(loadout, origin: str) -> bool:
    """Whether ``origin`` is read from its library build in place of its
    project, which is missing: a loaded mod of yours, or the loadout's own
    mod once a build of it is found (:func:`follow_projects`)."""
    if is_own_mod(origin):
        ref = loadout.projects.get("")
        return ref is not None and bool(ref.standing_in) and loadout.held("") == project_files.MISSING
    return loadout.held(origin) == project_files.MISSING


def read_project_now(loadout, origin: str, index) -> None:
    """Read the mod of yours at ``origin`` from its project at once, where
    it stands in the stack, and let go of its hold: for a patch just taken
    up from a project made in another loadout
    (:func:`.readback.join_patch`), which an edit is about to go into.  The
    mods after it stay marked, for the reading in the background."""
    follow_projects(loadout)
    taken = snapshot(loadout, asked=[origin])
    take(loadout, read_again(taken, index))


def follow_projects(loadout, library=None) -> List[str]:
    """Mark each mod of yours whose project another loadout changed since
    this one read it (:func:`.readback.moved`), and every mod after it; the
    origins newly marked.

    And each whose project is missing and that has a build in ``library``,
    or a folder it was read from (:func:`.readback.stand_in`): it is read
    from that folder like anybody else's mod, until its project is found.

    The mod itself is marked whatever it is, a patch included: what is in
    doubt is not what the mods before it leave, but the whole of what it
    holds here.  Its hold is let go of once its reading is taken
    (:func:`take`), so a reading that never finishes is asked for again
    the next time, as the marks are.
    """
    marked: List[str] = []

    def mark(origin: str, kind: str) -> None:
        marks = _marks(loadout, origin)
        if [kind, origin] not in marks:
            marks.append([kind, origin])
            if origin not in marked:
                marked.append(origin)
        rank = loadout.layer_rank(origin)
        after = [entry[0] for entry in stack(loadout)] + ([""] if loadout.has_own_mod() else [])
        for later in after:
            if later == origin or loadout.layer_rank(later) <= rank:
                continue
            record = loadout.mod(later)
            newly = record is not None and _mark(record, kind, origin)
            if newly and later not in marked:
                marked.append(later)

    for origin in readback.moved(loadout):
        mark(origin, PROJECT)
        # Changed in another loadout, what it keeps inside somebody else's
        # mod may have moved with it, and this loadout's reading of that mod
        # is the one it had before: read again from its folder, an op taken
        # out there is found by its text and taken out here too
        # (:mod:`.held`), and one put back there comes back.  Not for one
        # just taken up here, which put back what it keeps as it came in.
        ref = loadout.projects.get(origin)
        if ref is not None and ref.revision != ref.read_at:
            for target in _kept_inside(loadout, origin):
                mark(target, KEPT)
    for layer in loadout.layers:
        if [LIBRARY, layer.origin] in layer.stale and not _stands_in(loadout, layer.origin):
            # Found again: read from its project, and not from its build.
            layer.stale.remove([LIBRARY, layer.origin])
        ref = loadout.projects.get(layer.origin)
        folder = readback.stand_in(loadout, layer.origin, library)
        if folder is None or ref.standing_in == str(folder):
            continue
        # Like anybody else's mod: what its folder's modinfo says.  And at a
        # revision its project never had, so that one found again where it
        # was is read back as well.
        _follow(loadout, layer, modfolder.metadata_of(folder), theirs=True)
        layer.folder = ref.standing_in = str(folder)
        layer.mod_id = layer.mod_id or ref.mod_id
        ref.read_at = -1
        readback.settle(loadout, layer.origin)
        mark(layer.origin, LIBRARY)
    # The loadout's own mod, the same way: its details, which went with its
    # project, are what its build's modinfo says, and it is read from that
    # build where it stands.
    ref = loadout.projects.get("")
    if ref is not None:
        if [LIBRARY, ""] in loadout.own.stale and not _stands_in(loadout, ""):
            loadout.own.stale.remove([LIBRARY, ""])
        folder = readback.stand_in(loadout, "", library)
        if folder is not None and ref.standing_in != str(folder):
            _follow_own(loadout, modfolder.metadata_of(folder), ref)
            ref.standing_in = str(folder)
            ref.read_at = -1
            readback.settle(loadout, "")
            mark("", LIBRARY)
    return marked


def _follow_own(loadout, now: dict, ref) -> None:
    """The loadout's own mod's details as its build's modinfo says them
    ``now``: what :func:`_follow` takes for a mod of yours read from its
    library build."""
    loadout.own.name = now.get("mod_name") or loadout.own.name
    loadout.own.version = now.get("version") or loadout.own.version
    loadout.own.creator = now.get("creator") or loadout.own.creator
    loadout.own.description = now.get("description") or loadout.own.description
    loadout.own.category = now.get("category") or loadout.own.category
    loadout.own.mod_id = now.get("mod_id") or ref.mod_id or loadout.own.mod_id


def _kept_inside(loadout, keeper: str) -> List[str]:
    """The loaded mods of somebody else's that ``keeper``, a mod of yours,
    keeps edits inside, or that it patches: the ones what its project
    keeps can have moved in."""
    theirs = inside.theirs(loadout)
    record = loadout.mod(keeper)
    found = {record.patches} if record is not None and record.patches else set()
    found |= {op.origin for op in loadout.ops if op.kept_by == keeper}
    found |= {change.origin for change in loadout.changes.values() if change.kept_by == keeper}
    found |= {entry[0] for entry in loadout.taken_out if inside.keeper_of(entry) == keeper}
    found |= {held.get("origin", "") for held in loadout.held_edits if held.get("kept_by") == keeper}
    return sorted(found & theirs, key=loadout.layer_rank)


def kept_again(loadout) -> List[str]:
    """The mods marked to be read from their folders again, for edits kept
    inside them by a project another loadout changed (:func:`follow_projects`)."""
    return [layer.origin for layer in loadout.layers if [KEPT, layer.origin] in layer.stale]


def standing_in(loadout) -> List[str]:
    """The mods marked to be read from their library build, their project
    being missing (:func:`follow_projects`)."""
    found = [""] if [LIBRARY, ""] in loadout.own.stale and _stands_in(loadout, "") else []
    return found + [layer.origin for layer in loadout.layers if [LIBRARY, layer.origin] in layer.stale]


def read_back(loadout) -> List[str]:
    """The mods marked to be read from their project (:func:`follow_projects`)."""
    found = [""] if [PROJECT, ""] in loadout.own.stale else []
    return found + [layer.origin for layer in loadout.layers if [PROJECT, layer.origin] in layer.stale]


def _follow(loadout, layer, now: dict, theirs: bool = False) -> None:
    """Take what ``layer``'s folder's modinfo says ``now`` as what it was
    read at, so that it is compared with that from here on.

    Somebody else's mod takes all of it, and so does a mod of yours read
    from its library build (``theirs``).  A mod of yours keeps its modinfo,
    which is the loadout's to write, edits made here and all: only its
    version and the Studio's record of its build move, and the version shown
    only where it had not been edited here.
    """
    declared = dict(now.get("declared") or {})
    if not theirs and patching.is_mine(loadout, layer):
        kept = dict(layer.declared or {})
        if layer.version.strip() == modfolder.declared_version(kept):
            layer.version = str(now.get("version") or "")
        for key in ("Version", modinfo.STUDIO_KEY):
            if key in declared:
                kept[key] = declared[key]
            else:
                kept.pop(key, None)
        layer.declared = kept
        return
    layer.name = now.get("mod_name") or layer.origin
    layer.version = now.get("version", "")
    layer.creator = now.get("creator", "")
    layer.description = now.get("description", "")
    layer.category = now.get("category", "")
    layer.mod_id = now.get("mod_id", "")
    layer.load_after = list(now.get("load_after") or [])
    layer.offered = now["options"].to_dict() if now.get("options") else {}
    layer.declared = declared


def read_alone(loadout, origin: str) -> bool:
    """Say that ``origin`` was just read on its own, as importing one mod
    reads it; whether that leaves its reading in doubt, which it does where
    anything loads before it.

    A mod that was here already and has been imported again -- an update of
    it, say -- leaves the mods after it in doubt as well, and nothing in the
    stack shows that it is not the mod it was.  One that is new is seen by
    :func:`check`.
    """
    layer = loadout.layer(origin)
    if layer is None or not layer.folder:
        return False
    layer.stale = []
    order = [entry[0] for entry in stack(loadout)]
    if origin not in order:
        return False
    place = order.index(origin)
    if any(entry[0] == origin for entry in loadout.read_stack or []):
        for later in order[place + 1:]:
            _mark(loadout.layer(later), CHANGED, origin)
    return place > 0 and _mark(layer, ALONE, origin)


def put_back(loadout, layer, again: bool = True) -> bool:
    """Say that restoring the loadout's list (:mod:`.histories`) put
    ``layer`` back, or set it back to settings it was read at then; whether
    that leaves it to read again from its folder.

    A mod put back (``again`` False) is only its record: what its folder's
    modinfo says is taken first, as :func:`follow` takes it, and its
    reading is all to come.
    """
    folder = Path(layer.folder)
    if not again and folder.is_dir():
        _follow(loadout, layer, modfolder.metadata_of(folder))
    return _mark(layer, RESTORED, layer.origin)


def _marks(loadout, origin: str) -> list:
    """Where it is written why ``origin``'s reading is in doubt, to add to
    or clear; for a mod that is not here, a list nothing else holds."""
    record = loadout.mod(origin)
    return record.stale if record is not None else []


def _mark_edited(marks: list, about: str, at: Optional[int] = None) -> None:
    """``at`` is for the mod that was edited itself: the place in its file
    it is read again from, the earliest of them where it is marked twice."""
    for why in marks:
        if why[0] == EDITED and why[1] == about and (len(why) == 3) == (at is not None):
            if at is not None:
                why[2] = str(min(int(why[2]), at))
            return
    marks.append([EDITED, about] if at is None else [EDITED, about, str(at)])


def edited(loadout, reach: Dict[str, int], assets=None) -> None:
    """Say that what the loadout writes for some mods has changed: ``reach``
    is each of them with the place in its file of the first thing that did,
    as a step of undo says it (:attr:`.undo.Step.reach`).  ``assets`` is the
    assets what changed is about, for each mod where that can be said
    (:attr:`.undo.Step.assets`): :func:`reached` goes by it.

    Each is marked from that place on, where it has an op there or after
    it, and so is every mod that loads after it and has an op at all.  A mod
    with none has nothing to be read again: the changes the Studio writes
    for it say what they do, whatever they find.
    """
    # A mod that is not here any more was not edited: it went, and that is
    # :func:`check`'s to see.
    reach = {
        origin: at for origin, at in reach.items()
        if is_own_mod(origin) or loadout.layer(origin) is not None
    }
    if not reach:
        return
    loadout.edits += 1
    # What was known of edits nothing is in doubt for any more is forgotten.
    about = {
        why[1] for origin in waiting(loadout)
        for why in _marks(loadout, origin) if why[0] == EDITED
    }
    loadout.edit_reach = {
        origin: found for origin, found in loadout.edit_reach.items() if origin in about
    }
    for origin in reach:
        found = (assets or {}).get(origin)
        if found is None or (origin in about and origin not in loadout.edit_reach):
            # Not known for this edit, or for one before it that is still
            # being read for.
            loadout.edit_reach[origin] = None
        elif loadout.edit_reach.get(origin, set()) is not None:
            loadout.edit_reach[origin] = loadout.edit_reach.get(origin, set()) | set(found)
    last: Dict[str, int] = {}
    for held in (loadout.ops, loadout.unread):
        for one in held:
            last[one.origin] = max(last.get(one.origin, one.order), one.order)
    ranks = {
        origin: loadout.layer_rank(origin)
        for origin in last if is_own_mod(origin) or loadout.layer(origin) is not None
    }
    for origin, at in reach.items():
        layer = loadout.layer(origin)
        if layer is not None:
            layer.edited = True
        if origin in ranks and last[origin] >= at:
            _mark_edited(_marks(loadout, origin), origin, at)
        rank = loadout.layer_rank(origin)
        for other, there in ranks.items():
            if other != origin and there > rank:
                _mark_edited(_marks(loadout, other), origin)


def only_edits(loadout, origins) -> bool:
    """Whether each of ``origins`` is in doubt for edits made here and for
    nothing else: no mod came, went or was read from its folder again."""
    return all(
        why[0] == EDITED
        for origin in origins for why in _marks(loadout, origin)
    )


def written_here(loadout) -> Set[str]:
    """The mods a reading of the stack reads from what the loadout writes
    for them, and not from a folder: the mod written here and any other
    with no folder, and each loaded mod that was edited here.

    Edited is what :func:`edited` was told of it, or what shows
    (:func:`.edits.edited`): an op or a change of yours in it, one switched
    off, an op taken out.  Of the rest a mod with nothing in it is left out;
    one with every op taken out is not, since its folder would bring them
    back for the mods after it.
    """
    found = {""}
    for layer in loadout.layers:
        if layer.edited or not layer.folder:
            found.add(layer.origin)
    there: Set[str] = set()
    for held in (loadout.changes.values(), loadout.ops, loadout.unread):
        there.update(one.origin for one in held)
    return {
        origin for origin in (found & there) | edits.edited(loadout)
        if is_own_mod(origin) or loadout.layer(origin) is not None
    }


def settle(loadout, origins=None) -> None:
    """Take the readings of ``origins``, or of every mod, as fresh: they
    have just been read where they stand."""
    for record in _every(loadout):
        if origins is None or record.origin in origins:
            record.stale = []
    if origins is None:
        loadout.read_stack = stack(loadout)


def waiting(loadout) -> List[str]:
    """The mods whose reading is marked, in the order they load."""
    marked = [record.origin for record in _every(loadout) if record.stale]
    return sorted(marked, key=loadout.layer_rank)


def first_reading(loadout, origin: str) -> bool:
    """Whether the loadout has not read ``origin``'s project yet: just added
    to it from another loadout, or found again where it was missing.  Its
    reading back was said to be another loadout's change to it, which it
    was not (R3-5, R3-42, hands-on run 3)."""
    ref = loadout.projects.get(origin)
    return ref is not None and ref.read_at < 0


def why(loadout, layer) -> List[str]:
    """Each reason a mod's reading is marked, as a sentence."""
    def name(origin: str) -> str:
        return loadout.label_of(origin) or "My Anno Mod"

    out = []
    for kind, about in ((why[0], why[1]) for why in layer.stale):
        if kind == ADDED:
            out.append(tr("%s loads before it now.", name(about)))
        elif kind == CHANGED:
            out.append(tr("%s, which loads before it, has been read again.", name(about)))
        elif kind == GONE:
            out.append(tr("%s no longer loads before it.", name(about)))
        elif kind == MOVED:
            out.append(tr("The mods before it load in another order."))
        elif kind == ALONE:
            out.append(tr("It was read on its own, and other mods load before it."))
        elif kind == EDITED and about == layer.origin:
            out.append(tr("It was edited here, and its later ModOps may find something else."))
        elif kind == EDITED:
            out.append(tr("%s, which loads before it, was edited here.", name(about)))
        elif kind == UPDATED and about == layer.origin:
            out.append(tr("Its folder holds another version or build of it than the one read."))
        elif kind == UPDATED:
            out.append(tr("%s, which loads before it, has been updated on disk.", name(about)))
        elif kind == RESTORED:
            out.append(tr("Restoring the loadout put it back as it was then, "
                          "and it is read again from its folder."))
        elif kind == PROJECT and about == layer.origin and first_reading(loadout, about):
            out.append(tr("It is read from its project, which this loadout has not read yet."))
        elif kind == PROJECT and about == layer.origin:
            out.append(tr("Its project was changed in another loadout since this one read it."))
        elif kind == PROJECT:
            out.append(tr("%s, which loads before it, was changed in its project by another loadout.",
                          name(about)))
        elif kind == KEPT and about == layer.origin:
            out.append(tr("A mod of yours keeps edits inside it, and its project was changed "
                          "in another loadout since this one read it."))
        elif kind == KEPT:
            out.append(tr("%s, which loads before it, holds edits a mod of yours keeps, and "
                          "that mod's project was changed in another loadout.", name(about)))
        elif kind == LIBRARY and about == layer.origin:
            out.append(tr("Its project is missing, and the library's build of it is read instead."))
        elif kind == LIBRARY:
            out.append(tr("%s, which loads before it, is read from the library's build of it now.",
                          name(about)))
        elif kind == READER:
            out.append(tr("It was read by another version of the Studio, "
                          "which reads mods differently."))
    return out


def updated(loadout, origins=None) -> List[str]:
    """The mods, of ``origins`` or of all, marked for an update to their own
    folder (:func:`follow`), in the order they load."""
    return [
        layer.origin for layer in sorted(loadout.layers, key=lambda layer: layer.order)
        if [UPDATED, layer.origin] in layer.stale
        and (origins is None or layer.origin in origins)
    ]


def reached(loadout) -> Dict[str, Set[str]]:
    """Which ops of each marked mod the change could reach, by the mod.

    Every op of the mod where what the mods before it did is not known any
    more: one that has gone, one read again at other settings or edited
    here, an order that changed, or the mod itself read alone.  Where the
    mod was edited itself and nothing else, its ops from that place on.
    Where the only change is edits to mods before it, and the assets those
    were about are known (``Loadout.edit_reach``), the ops such an edit
    can touch, as for a mod that is new.  Where the only change is mods
    that were not there before, it is the ops a new mod can touch: every
    pattern, whose reach may have grown; an op with a change at an asset one
    of those mods changes; and an op that comes to nothing today, which may
    have been aimed at something that is there now.  A group is never
    marked: what is under it is the ops in it.
    """
    marked = {record.origin: record.stale for record in _every(loadout) if record.stale}
    if not marked:
        return {}
    present = {layer.origin for layer in loadout.layers}
    narrow: Dict[str, Set[str]] = {}
    # A mod in doubt only for an edit to itself: from where it was edited.
    since: Dict[str, int] = {}
    for origin, marks in marked.items():
        if all(why[0] == ADDED and why[1] in present for why in marks):
            narrow[origin] = {why[1] for why in marks}
        elif all(why[0] == EDITED and why[1] == origin and len(why) == 3 for why in marks):
            since[origin] = min(int(why[2]) for why in marks)
    arrived = {about for causes in narrow.values() for about in causes}
    touched: Dict[str, Set[str]] = {origin: set() for origin in arrived}
    # A mod in doubt only for edits to mods before it, where the assets
    # those were about are known: the ops that could meet one of them.
    for origin, marks in marked.items():
        if origin in narrow or origin in since:
            continue
        if all(
            why[0] == EDITED and why[1] != origin
            and loadout.edit_reach.get(why[1]) is not None
            for why in marks
        ):
            narrow[origin] = {why[1] for why in marks}
            for why in marks:
                touched[why[1]] = set(loadout.edit_reach[why[1]])
    under: Dict[str, Set[str]] = {}
    for change in loadout.changes.values():
        if change.origin in arrived and not change.authored:
            touched[change.origin].add(change.guid)
        if change.origin in narrow and change.op_uid:
            under.setdefault(change.op_uid, set()).add(change.guid)
    out: Dict[str, Set[str]] = {origin: set() for origin in marked}
    for op in loadout.ops:
        if op.origin not in marked or op.scope:
            continue
        if op.origin in since:
            if op.order >= since[op.origin]:
                out[op.origin].add(op.uid)
            continue
        if op.origin not in narrow:
            out[op.origin].add(op.uid)
            continue
        guids = under.get(op.uid)
        if op.searches or not guids or any(
            guids & touched[about] for about in narrow[op.origin]
        ):
            out[op.origin].add(op.uid)
    return out


# ------------------------------------------------------------ reading again
@dataclass
class Snapshot:
    """As much of a loadout as reading its stack asks for, held apart from
    it: the reading runs on another thread, and the loadout goes on being
    worked in meanwhile."""

    game: str = ""
    layers: list = field(default_factory=list)
    # What was marked and what the stack was when this was taken: a reading
    # of it is an answer to that question and no other.
    asked: List[str] = field(default_factory=list)
    stack: List[List[str]] = field(default_factory=list)
    # The file of each mod that is read from what the loadout writes for it
    # (:func:`written_here`), as it was written then; and how many edits
    # the loadout had seen.
    texts: Dict[str, str] = field(default_factory=dict)
    edits: int = 0

    def layer(self, origin: str):
        for layer in self.layers:
            if layer.origin == origin:
                return layer
        return None


def snapshot(loadout, asked: Optional[List[str]] = None, from_folder=(),
             library_dir=None) -> Snapshot:
    """The loadout's stack as a reading of it needs it, for the mods whose
    reading is marked -- or for ``asked``, where somebody asks for others.

    ``from_folder`` is mods to read from their folders though they were
    edited here: one whose folder is being read again on purpose.  A mod
    updated on disk is always one (:func:`follow`): its folder is what
    moved, and what was done to it here is kept by :func:`take`.

    ``library_dir``, the library's folder for the game: a library mod with
    no link in the mods folder is read where the library keeps it
    (:func:`.library.kept_at`), as it was when it was read in.
    """
    from . import library as library_mod
    # A mod read from its project is read as the project has it, whatever
    # its folder holds: the project is the mod, and the folder a build of it.
    projected = set(read_back(loadout))
    from_folder = (
        set(from_folder) | set(updated(loadout)) | set(standing_in(loadout)) | set(kept_again(loadout))
    ) - projected
    layers = []
    for layer in sorted(loadout.layers, key=lambda layer: loadout.layer_rank(layer.origin)):
        held = copy.copy(layer)
        held.options = dict(layer.options)
        held.read_as = dict(layer.read_as)
        if library_dir is not None and layer.folder and not Path(layer.folder).is_dir():
            kept = library_mod.kept_at(library_dir, layer.folder)
            if kept is not None:
                held.folder = str(kept)
        layers.append(held)
    asked = waiting(loadout) if asked is None else list(asked)
    texts: Dict[str, str] = {}
    if asked:
        # As far as the last mod asked for: nothing after it is read.
        last = max(loadout.layer_rank(origin) for origin in asked)
        for origin in (written_here(loadout) | projected) - from_folder:
            if loadout.layer_rank(origin) <= last:
                texts[origin] = (
                    readback.shown(loadout, origin) if origin in projected
                    else modfile.shown(loadout, origin)
                ).text
    standing = "" in from_folder and _stands_in(loadout, "")
    if "" in texts or standing:
        # The mod written here is no loaded mod: it stands in the stack as
        # one for this reading, where it would load -- read from its library
        # build where its project is missing.
        own = loadout.own_layer()
        own.stale = []
        if standing:
            own.folder = loadout.projects[""].standing_in
        rank = loadout.layer_rank("")
        before = [layer for layer in layers if loadout.layer_rank(layer.origin) < rank]
        own.order = before[-1].order + 0.5 if before else min(
            (layer.order for layer in layers), default=1
        ) - 1
        layers.insert(len(before), own)
    return Snapshot(
        game=loadout.game, layers=layers, asked=asked, stack=stack(loadout),
        texts=texts, edits=loadout.edits,
    )


def read_at(loadout, origin: str, index, chosen: Dict[str, str], logged=None,
            progress=None, translating=None, library_dir=None):
    """``origin`` read from its folder at the settings ``chosen``, where it
    stands in the stack: :func:`reader.analyse_in_stack`, with each mod
    before it that was edited here read as the loadout writes it.

    The mod itself is read from its folder whatever was done to it here.
    That is what reading it at other settings is, and whoever asks for it
    has been told so.  Raises ValueError as that function does.
    ``library_dir`` is as :func:`snapshot` has it.
    """
    taken = snapshot(loadout, asked=[origin], from_folder=[origin], library_dir=library_dir)
    written = {
        other: modfile.ops_of_text(taken, other, text) for other, text in taken.texts.items()
    }
    return reader.analyse_in_stack(
        taken, origin, index, chosen, logged=logged, progress=progress,
        translating=translating, written=written,
    )


def still_asked(loadout, taken: Snapshot) -> bool:
    """Whether a reading of ``taken`` still answers what the loadout asks."""
    return (
        stack(loadout) == taken.stack
        and waiting(loadout) == taken.asked
        and loadout.edits == taken.edits
    )


@dataclass
class Answer:
    """What reading a mod again found, and none of the reader's working.

    The reader's own report holds every op as it was parsed and every tree
    it built on the way, which nobody asks for once the mod is read:
    :func:`take` wants the ops, the changes and the ops left unread.  Those
    are plain data, so an answer can be handed from wherever the reading
    was done, and the working goes when the reading ends.
    """

    ops: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    entries: list = field(default_factory=list)

    @classmethod
    def of(cls, report, origin: str) -> Answer:
        return cls(
            ops=list(report.ops), changes=list(report.changes),
            entries=report.unread(origin),
        )

    def unread(self, origin: str = "") -> list:
        """The ops that could not be read, as the reader's report says them
        (:meth:`.modops.ImportReport.unread`): made for the mod already."""
        return self.entries


def read_again(taken: Snapshot, index, logged=None, progress=None, translating=None,
               searching=None) -> dict:
    """Each marked mod's reading where it stands in the stack, by origin:
    an :class:`Answer`.

    One pass over the stack for all of them that were read the same way,
    which is nearly always all of them.  A mod whose folder is gone is not
    in the answer, unless it is one read from what the loadout writes for
    it: the answer for such a mod is a :class:`modfile.Reread`.

    ``searching`` is told where the sweep for the mods' searches has got
    to, as the first reading of a folder is: the one long stretch in which
    ``progress`` says nothing.
    """
    ways: Dict[str, List[str]] = {}
    for origin in taken.asked:
        layer = taken.layer(origin)
        if layer is None:
            continue
        if origin not in taken.texts and not (layer.folder and Path(layer.folder).is_dir()):
            continue
        ways.setdefault(json.dumps(layer.read_as, sort_keys=True), []).append(origin)
    out: dict = {}
    for origins in ways.values():
        # Read into its ops for each pass: a reading numbers the ops it is
        # handed, and by those numbers each is known afterwards.
        written = {
            origin: modfile.ops_of_text(taken, origin, text)
            for origin, text in taken.texts.items()
        }
        found = reader.analyse_stack(
            taken, origins, index, logged=logged, progress=progress, translating=translating,
            written=written, searching=searching,
        )
        for origin, report in found.items():
            answer = Answer.of(report, origin)
            out[origin] = answer if origin not in written else modfile.Reread(
                text=taken.texts[origin], report=answer,
                orders=[op.order for op in written[origin]],
            )
    return out


@dataclass
class Moved:
    """What reading a mod again changed in what its ops come to."""

    origin: str
    # The ops that come to something else than they did, new ones included.
    ops: Set[str] = field(default_factory=set)
    # And how many ops the mod no longer has, which have no row to mark.
    gone: int = 0
    # Read from its project, which another loadout had changed
    # (:mod:`.readback`); and how many edits made here, never saved into
    # that project, were given up for it.
    project: bool = False
    given_up: int = 0
    # Read from its project for the first time here, rather than because
    # another loadout changed it (:func:`first_reading`).
    first: bool = False


def _comes_to(loadout, origins: Set[str]) -> Dict[str, Dict[str, frozenset]]:
    """What each op of the mods named comes to: by mod, then by op."""
    out: Dict[str, Dict[str, set]] = {origin: {} for origin in origins}
    for op in loadout.ops:
        if op.origin in out:
            out[op.origin][op.uid] = set()
    for change in loadout.changes.values():
        found = out.get(change.origin, {}).get(change.op_uid)
        if found is not None:
            found.add((change.guid, change.path, change.op, change.new_value, change.payload))
    return {
        origin: {uid: frozenset(found) for uid, found in ops.items()}
        for origin, ops in out.items()
    }


def take(loadout, reports: dict) -> List[Moved]:
    """Put each mod's reading in place of the one the loadout holds, and say
    what moved in each; a mod no longer here is passed over.

    What was done to a mod here is kept: an op read again is the op that
    was here, so an edit laid over one of its answers is still the exception
    to it; a change or an op of yours is left where it is; an op taken out
    here stays out; and an op or a change switched off, or given a note, is
    switched off and has its note still.

    All of them at once, and each pass over the loadout made once: a change
    at the front of a full mods folder is every mod after it read again.

    A mod read from what the loadout writes for it (:func:`written_here`)
    is taken a mod at a time, by :func:`modfile.take_reading`: every op of
    that text is one the loadout holds, yours among them, and each is read
    again as the op it is.  So is a mod read from its project, by
    :func:`.readback.take`, which lets go of its hold.
    """
    origins = [
        origin for origin in reports
        if loadout.layer(origin) is not None
        or (is_own_mod(origin) and (isinstance(reports[origin], modfile.Reread) or _stands_in(loadout, "")))
    ]
    if not origins:
        return []
    before = _comes_to(loadout, set(origins))
    from_text = [origin for origin in origins if isinstance(reports[origin], modfile.Reread)]
    # Read from its library build: nothing of what was read of its project
    # stays, what was made here included, since none of it is in that build
    # or could be saved into the project.
    built = [origin for origin in standing_in(loadout) if origin in origins and origin not in from_text]
    for origin in built:
        for change in loadout.changes_of(origin):
            loadout.changes.pop(change.key, None)
    _take_read(loadout, {
        origin: reports[origin] for origin in origins if origin not in from_text
    })
    projected = set(read_back(loadout))
    first = {origin for origin in projected if first_reading(loadout, origin)}
    given_up: Dict[str, int] = {}
    for origin in built:
        readback.settle(loadout, origin)
    for origin in from_text:
        if origin in projected:
            taken = readback.take(loadout, origin, reports[origin])
            if taken is not None:
                given_up[origin] = taken
        else:
            modfile.take_reading(loadout, origin, reports[origin])
        _marks(loadout, origin).clear()
    after = _comes_to(loadout, set(origins))
    return [
        Moved(
            origin=origin,
            ops={uid for uid, found in after[origin].items() if before[origin].get(uid) != found},
            gone=len(set(before[origin]) - set(after[origin])),
            project=origin in given_up,
            given_up=given_up.get(origin, 0),
            first=origin in first,
        )
        for origin in origins
    ]


def _take_read(loadout, reports: dict) -> None:
    """:func:`take` for the mods read from their folders."""
    from . import held as held_edits

    origins = list(reports)
    wanted = set(origins)
    if not wanted:
        return
    stocks = held_edits.stock(loadout, origins)
    # Where each op of yours stands among theirs, by text, before the new
    # reading gives theirs the places their files have.
    places = inside.keep_places(loadout, wanted)
    ops_were = {op.uid: op for op in loadout.ops if op.origin in wanted}
    changes_were = {
        key: change for key, change in loadout.changes.items()
        if change.origin in wanted and not change.authored
    }
    ops, changes, unread = [], [], []
    for origin in origins:
        report = reports[origin]
        ops.extend(report.ops)
        changes.extend(report.changes)
        unread.extend(report.unread(origin))
        _marks(loadout, origin).clear()
    # Every op read, before taking the ones taken out here out of the list.
    fresh = held_edits.ops_read(ops, unread)
    loadout.as_before(ops, changes, unread)
    # What was read of them before goes; the mods themselves stay where they
    # are, with what is yours in them.
    for key in changes_were:
        loadout.changes.pop(key, None)
    loadout.ops = [op for op in loadout.ops if op.origin not in wanted or op.authored]
    loadout.unread = [entry for entry in loadout.unread if entry.origin not in wanted]
    for op in ops:
        was = ops_were.get(op.uid)
        if was is not None:
            op.active = was.active
            op.kept_by = was.kept_by
            op.note = was.note or op.note
        loadout.set_op(op)
    inside.put_places_back(loadout, places)
    # An op kept switched off is off with all it comes to: a change it comes
    # to now that it did not before came in on, and was written again as an
    # op of yours after their op switched off (R3-23, hands-on run 3).
    off = {op.uid for op in ops if not op.active}
    for change in changes:
        held = changes_were.get(change.key)
        if held is not None:
            change.active = held.active
            change.note = held.note or change.note
        elif change.op_uid in off:
            change.active = False
    loadout.take_read(changes)
    loadout.unread = loadout.unread + unread
    # Each op of theirs an edit of yours was made to, looked for again: a
    # mod changed on disk may have changed one.
    held_edits.settle(loadout, stocks, fresh)
    # Counted as landed: less whatever of them was taken out here.
    loadout.count_unread(wanted)


def give_up(loadout, origins) -> None:
    """Leave the readings of ``origins`` as they are and stop saying they
    are in doubt: there is no folder to read them from again."""
    for record in _every(loadout):
        if record.origin in origins:
            record.stale = []
