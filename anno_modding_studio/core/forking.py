"""Forks: somebody else's mod made into one of yours.

Somebody else's mod is never edited (section 5 of
``docs/proposals/projects-and-loadouts-proposal.md``).  A mod you want to
change directly is made into a **fork**: a mod of yours, with you as its
author and a ModID of its own, kept in a project of its own like any mod of
yours (section 5.4).

* **Its files are a copy of theirs,** beside the library
  (:func:`folder_for`): their ModOps, which the fork's project keeps from then
  on, and whatever else the mod ships, which a build of the fork carries
  across.  A file is a hard link to the library's where it can be, so a copy
  costs little space, and a text file is copied, as a generated copy has
  them (:mod:`.generated`).  The library's copy stays as its author shipped
  it, and an update to it no longer reaches the fork.
* **It is swapped in for the original** at the original's place in load
  order, with the original's settings: Anno 117 keeps them by ModID, and the
  fork's are written under its own.
* **It deprecates the original.**  Its modinfo lists the original's ModID in
  ``DeprecateIds`` (``Dependencies.Deprecate`` in Anno 117), so both games'
  loaders drop the original wherever the fork loads (:mod:`.dependencies`).
* **Your patch for the original** patches nothing once the original is
  dropped, so a fork can fold it in: its ops, and its edits inside the
  original's ops, become the fork's own, and the patch leaves the loadout.
  The patch's project is kept, since other loadouts may still use the
  original.  Declined, the patch stays where it is, patching nothing.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from . import build, disk, generated, modfolder
from .changes import Loadout, ModLayer, is_own_mod
from .games import ANNO_117, GAMES

# Appended to the original's name and folder for the fork's.
SUFFIX = " (my copy)"
# Beside the library's folder for each game, under the same root, as the
# generated copies are: one volume, so a hard link can be made.
FOLDER = "forks"


def root_for(library) -> Path:
    """Where the forks of the mods in ``library`` -- one game's folder in the
    library -- keep their files."""
    library = Path(library)
    return library.parent / FOLDER / library.name


def can_fork(loadout: Loadout, layer: Optional[ModLayer]) -> bool:
    """Whether ``layer`` can be made into a fork: somebody else's mod, loaded
    on its own and not bundled inside another, with none bundled inside it,
    since a copy of a shared piece would be one more copy the game chooses
    among."""
    from . import patching

    if layer is None or is_own_mod(layer.origin) or layer.patches or layer.fork_of:
        return False
    if patching.looks_mine(loadout, layer) or modfolder.is_bundled(layer.origin):
        return False
    inside = layer.origin + "/"
    return not any(other.origin.startswith(inside) for other in loadout.layers)


def deprecating(declared: dict, mod_id: str, game: str) -> dict:
    """``declared``, a modinfo, saying it deprecates ``mod_id`` as well as
    whatever it did."""
    out = copy.deepcopy(declared or {})
    if not mod_id:
        return out
    if game == ANNO_117:
        holder = out.get("Dependencies")
        if not isinstance(holder, dict):
            holder = out["Dependencies"] = {}
        key = "Deprecate"
    else:
        holder, key = out, "DeprecateIds"
    listed = holder.get(key)
    listed = [str(value) for value in listed] if isinstance(listed, list) else []
    if mod_id.lower() not in (value.strip().lower() for value in listed):
        listed.append(mod_id)
    holder[key] = listed
    return out


def new_mod_id(loadout: Loadout, layer: ModLayer) -> str:
    """A ModID of the fork's own, made of the original's and not one any
    loaded mod has."""
    game = GAMES.get(loadout.game, GAMES[ANNO_117] if loadout.game == ANNO_117 else None)
    base = "%s-%s" % (layer.mod_id or modfolder.folder_name(layer.origin), "my-copy")
    wanted = game.usable_mod_id(base) if game is not None else base
    taken = {(other.mod_id or "").lower() for other in loadout.layers} | {(loadout.own.mod_id or "").lower()}
    found, n = wanted, 2
    while found.lower() in taken:
        found = "%s-%d" % (wanted, n)
        n += 1
    return found


def folder_for(library, name: str) -> Path:
    """A folder for a fork called ``name`` that nothing is in yet."""
    root = root_for(library)
    stem = disk.safe_folder_name(name)
    found, n = root / stem, 2
    while found.exists():
        found = root / ("%s (%d)" % (stem, n))
        n += 1
    return found


def copy_files(loadout: Loadout, origin: str, library) -> Optional[Path]:
    """A copy of the folder ``origin`` was read from, for its fork: what the
    original ships, of the library's copy where the mod reaches the game
    through a generated one.  None where there is no folder to copy, or no
    library to put the copy beside."""
    source = build.carry_folder(loadout, origin)
    if source is None or library is None:
        return None
    layer = loadout.layer(origin)
    folder = folder_for(library, modfolder.folder_name(origin) + SUFFIX)
    made = generated.build(source, folder, {}, meta={"ModID": layer.mod_id if layer else ""})
    if made.wrong:
        raise OSError("the copy of %s is not what it was made of: %s" % (origin, ", ".join(made.wrong)))
    # A folder of its own from now on, and nothing the Studio builds again.
    generated.materialise(folder)
    return folder


def make(loadout: Loadout, origin: str, library=None, fold: bool = True) -> ModLayer:
    """Make the loaded mod ``origin`` into a fork, swapped in at its place:
    the fork's layer.  Everything the loadout holds of the original -- its
    ops, its changes, the ops that could not be read, and your edits to it,
    whichever mod of yours kept them -- is the fork's, and the fork's own
    edits now.  With ``fold``, your patch for the original is folded in too,
    and leaves the loadout (its project stays, for the loadouts that use
    it).  ``library`` is where the fork's copy of the original's files is
    made beside; without one it is read where the original was.
    """
    from . import patching

    original = loadout.layer(origin)
    if original is None or not can_fork(loadout, original):
        raise ValueError("%r cannot be made into a fork" % origin)
    name = patching.free_origin(loadout, modfolder.folder_name(origin) + SUFFIX)
    folder = copy_files(loadout, origin, library)
    fork = replace(
        original,
        origin=name,
        name=(original.name or origin) + SUFFIX,
        mod_id=new_mod_id(loadout, original),
        creator=loadout.own.creator,
        folder=str(folder) if folder is not None else original.folder,
        declared=deprecating(original.declared, original.mod_id, loadout.game),
        fork_of=original.mod_id or origin,
        modio_id=0,
        from_collection="",
        copies={},
        copy_versions={},
        stale=[],
        options=dict(original.options),
        offered=dict(original.offered),
        read_as=dict(original.read_as),
        load_after=list(original.load_after),
        needs=[],
    )
    patch = loadout.patch_for(origin) if fold else None
    _hand_over(loadout, origin, name)
    loadout.layers = [fork if layer.origin == origin else layer for layer in loadout.layers]
    # On or off in each profile as the original was.
    for profile in loadout.profiles:
        if origin in profile.switches:
            profile.switches[name] = profile.switches.pop(origin)
    if loadout.editing == origin:
        loadout.editing = name
    if patch is not None:
        fold_in(loadout, patch.origin, name)
    return fork


def fold_in(loadout: Loadout, patch: str, into: str) -> None:
    """Fold your patch ``patch`` into the mod of yours ``into``: its ops and
    changes, and its edits inside the mod it patched, become ``into``'s own,
    after what it has, as the patch loaded after it.  The patch leaves the
    loadout, and is no switch of its own any more; its project stays, for the
    loadouts that use it.  Done for a fork (:func:`make`), and for a mod
    that was yours all along under another name."""
    _hand_over(loadout, patch, into, after=True)
    loadout.layers = [layer for layer in loadout.layers if layer.origin != patch]
    loadout.projects.pop(patch, None)
    for profile in loadout.profiles:
        profile.switches.pop(patch, None)
    if loadout.editing == patch:
        loadout.editing = into


def _hand_over(loadout: Loadout, origin: str, into: str, after: bool = False) -> None:
    """Make everything the loadout holds of ``origin`` the fork ``into``'s:
    its ops and changes, what it could not read, what was taken out of it,
    its held edits, and the edits kept inside it (:mod:`.inside`), which are
    the fork's own now.  ``after``, for a patch folded in: what it holds goes
    after the fork's own, as it loaded after the original."""
    last = max((op.order for op in loadout.ops if op.origin == into), default=0)
    ops = []
    for op in loadout.ops:
        if op.origin == origin:
            # Yours, and in no mods folder to be read again until Play builds
            # the fork: work, as an op typed here is.
            op = replace(op, origin=into, kept_by=None, authored=True,
                         order=op.order + last if after else op.order)
        elif op.kept_by == origin:
            # An edit the patch kept inside the original, now the fork's own.
            op = replace(op, kept_by=None)
        ops.append(op)
    loadout.ops = ops
    # The Studio's ModOps for a patch's changes are written again for the
    # fork's at the end of the step (narrow.write), from its changes.
    loadout.narrow = [op for op in loadout.narrow if op.origin != origin]
    for change in list(loadout.changes.values()):
        if change.origin == origin or change.kept_by == origin:
            loadout.changes.pop(change.key, None)
            moved = replace(change, origin=into, kept_by=None, written_as="", authored=True)
            if change.origin == origin and after:
                moved.order = change.order + last
            loadout.changes[moved.key] = moved
    for entry in loadout.unread:
        if entry.origin == origin:
            entry.origin = into
    loadout.taken_out = [
        [into, *entry[1:4]] if entry[0] == origin else
        ([*entry[:4]] if len(entry) > 4 and entry[4] == origin else entry)
        for entry in loadout.taken_out
    ]
    held_edits = []
    for held in loadout.held_edits:
        if held.get("origin") == origin or held.get("kept_by") == origin:
            held = {key: value for key, value in held.items() if key != "kept_by"}
            held["origin"] = into
            # What of yours stands in their op's place, by its key, which
            # names the mod it is in.
            held["yours"] = [
                [kind, [into, *which[1:]] if kind == "change" and which and which[0] == origin
                 else which]
                for kind, which in held.get("yours") or ()
            ]
        held_edits.append(held)
    loadout.held_edits = held_edits


def patch_users(loadout: Loadout, origin: str, excluding=None) -> List[Path]:
    """The other loadouts that have your patch for ``origin``: the ones a
    fork folding it in leaves using it (:mod:`.filecache`)."""
    from . import filecache
    from . import projects as project_files

    patch = loadout.patch_for(origin)
    ref = loadout.projects.get(patch.origin) if patch is not None else None
    if ref is None:
        return []
    return filecache.loadouts_with_project(project_files.where(ref.path), loadout.game,
                                           excluding=excluding)
