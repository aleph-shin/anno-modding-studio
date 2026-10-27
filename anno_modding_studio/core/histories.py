"""The loadout's history, beside each project's: two histories.

A loadout and the projects of its mods are kept apart
(``docs/proposals/projects-and-loadouts-proposal.md``, section 9), and so is
what each did over time.  Each history records only what its file keeps, and
each is seen and restored on its own:

- **A project's history** is its mod's: its changes, ModOps and details at
  each checkpoint, each edit noted with the loadout it was made in
  (:class:`.changes.Checkpoint`, :meth:`.changes.Loadout.checkpoint`).  It is
  kept in the project file.  Checkpoints come at an export and a publish of
  the mod, by hand, and around a restore, each only where the work moved
  since the last.
- **A loadout's history** is its list (:class:`.changes.LoadoutCheckpoint`):
  which mods, in which order, which of them are on, the settings each is read
  at, and its profiles.  Each checkpoint also records which revision of each
  project the loadout had, and the checkpoint of that project it stood at:
  a project whose work has moved since its last checkpoint takes one with
  the loadout's (:func:`take`).

**Restoring.**  A project's restore puts its mod's work back in every
loadout that has it (:func:`restore_mod`).  A loadout's puts its list back
(:func:`restore`): the mods added since are taken out, a mod taken out since
is put back and read again from its folder, and each takes the place, the
switch and the settings it had.  *With each project as it was then*, each
project's ordinary restore follows, one after another.  A project of yours
taken out since comes back only so: what it holds is its project's.

**Never restored:** build numbers, the mod.io link and the record of what
was published.  They are facts about what happened outside the file, and a
project restored to before build 12 must not hand out build 12 again.

Works on a :class:`.changes.Loadout`, with no Qt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import filecache, inside, readback, stale
from . import projects as project_files
from .changes import (
    KIND_EXPORT,
    KIND_PUBLISH,
    Checkpoint,
    Loadout,
    LoadoutCheckpoint,
    ModLayer,
    Profile,
    is_own_mod,
)
from .i18n import STORED, tr_noop

# What of a mod's layer the loadout's history keeps: enough to put it back
# and read it again, and none of what its reading found.
_ENTRY = (
    "origin", "name", "folder", "category", "version", "creator", "mod_id", "modio_id",
    "order", "options", "read_as", "from_collection", "patches", "needs", "active",
    "load_after",
)

# The labels the Studio gives the checkpoints it takes, kept English in the
# files and shown translated.
BEFORE = tr_noop("Before restoring %s", context=STORED)
AFTER = tr_noop("Restored %s", context=STORED)
WITH_LOADOUT = tr_noop("%s, with the loadout", context=STORED)

# Why a mod the loadout had then is not put back.
YOURS = "yours"      # a mod of yours, which comes back only with its project
GONE = "gone"        # its folder, or its project, is not there any more
UNKNOWN = "unknown"  # its project has no record of the checkpoint it stood at


def entry_of(layer: ModLayer) -> dict:
    """``layer`` as the loadout's history keeps it."""
    held = layer.to_dict()
    return {key: held[key] for key in _ENTRY if key in held}


def now(loadout: Loadout, label: str = "", kind: str = "") -> LoadoutCheckpoint:
    """The loadout's list as a checkpoint would hold it now."""
    projects: Dict[str, dict] = {}
    for origin in loadout.project_origins():
        ref = loadout.projects.get(origin)
        latest = loadout.latest_checkpoint_of(origin)
        record = loadout.mod(origin)
        projects[origin] = {
            "path": ref.path if ref is not None else "",
            "mod_id": record.mod_id if record is not None else "",
            "revision": ref.read_at if ref is not None else 0,
            "checkpoint": latest.timestamp if latest is not None else 0.0,
        }
    return LoadoutCheckpoint(
        timestamp=time.time(),
        label=label,
        kind=kind,
        mods=[entry_of(layer) for layer in sorted(loadout.layers, key=lambda layer: layer.order)],
        profile=loadout.chosen_profile().name,
        profiles=loadout._profiles_to_save(),
        projects=projects,
    )


def take(loadout: Loadout, label: str, kind: str = "", taken_in: str = "",
         label_of: Optional[Callable[[str], str]] = None) -> LoadoutCheckpoint:
    """Record the loadout's list as a new checkpoint of its history.

    Each project whose work has moved since its own last checkpoint takes
    one first, labelled with this one's -- or with ``label_of`` its mod,
    where given -- so that the project as it was then is a checkpoint of its
    own to go back to.  A project held from here is left: nothing of it
    moved here.  An export's label names the version and build of the mod
    exported, which another mod's project took as its own: "build 1" in a
    project built three times (R3-35, hands-on run 3).
    """
    for origin in loadout.project_origins():
        if not loadout.held(origin) and loadout.changed_since_checkpoint(origin):
            own = label_of(origin) if label_of is not None else label
            loadout.checkpoint(WITH_LOADOUT % own, origin=origin, loadout=taken_in)
    entry = now(loadout, label, kind)
    loadout.history.append(entry)
    return entry


def take_release(loadout: Loadout, label: str, kind: str, taken_in: str = "",
                 label_of: Optional[Callable[[str], str]] = None) -> LoadoutCheckpoint:
    """The checkpoint of the list an export or a publish (``kind``) stands
    at: a new one where it moved since the last (:func:`moved`), and
    otherwise the last.  A publish of what an export recorded makes that one
    the publish, as a project's does
    (:meth:`.changes.Loadout.release_checkpoint`)."""
    if moved(loadout):
        return take(loadout, label, kind, taken_in, label_of)
    latest = loadout.history[-1]
    if latest.kind == KIND_EXPORT and kind == KIND_PUBLISH:
        latest.kind, latest.label = kind, label
    return latest


def moved(loadout: Loadout) -> bool:
    """Whether a checkpoint of the list taken now would hold anything its
    last one does not: the list itself, or a project of its whose work moved
    since its own last checkpoint, which takes one with it (:func:`take`).
    Always before the first."""
    if not loadout.history:
        return True
    if any(not loadout.held(origin) and loadout.worth_a_checkpoint(origin)
           for origin in loadout.project_origins()):
        return True
    return changed_since_checkpoint(loadout)


def changed_since_checkpoint(loadout: Loadout) -> bool:
    """Whether the loadout's list, or which checkpoint of its projects it
    stands at, has moved since its last checkpoint."""
    if not loadout.history:
        return True
    return not compare(loadout.history[-1], now(loadout)).is_empty


# -- what moved between two checkpoints ------------------------------------------
@dataclass
class ListDiff:
    """What is different about a loadout's list, from one checkpoint to
    another.  Each mod is its entry (:func:`entry_of`), as the later side
    has it where both do."""

    added: List[dict] = field(default_factory=list)
    gone: List[dict] = field(default_factory=list)
    on: List[dict] = field(default_factory=list)
    off: List[dict] = field(default_factory=list)
    moved: List[dict] = field(default_factory=list)
    settings: List[dict] = field(default_factory=list)
    profiles_added: List[str] = field(default_factory=list)
    profiles_gone: List[str] = field(default_factory=list)
    # The profiles, other than the one chosen on both sides, that switch a
    # mod both sides have otherwise.
    profiles_switched: List[str] = field(default_factory=list)
    # The chosen profile, before and after, where it differs.
    chosen: Optional[Tuple[str, str]] = None
    # Each project that stands at another checkpoint: its origin, and the
    # revision and checkpoint timestamp before and after.
    projects: List[Tuple[str, dict, dict]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.added or self.gone or self.on or self.off or self.moved or self.settings
            or self.profiles_added or self.profiles_gone or self.profiles_switched
            or self.chosen or self.projects
        )


def label_of(entry: dict) -> str:
    return str(entry.get("name") or entry.get("origin") or "")


def compare(before: Optional[LoadoutCheckpoint], after: LoadoutCheckpoint) -> ListDiff:
    """What goes from ``before`` -- None for an empty loadout -- to ``after``."""
    diff = ListDiff()
    was = {mod.get("origin"): mod for mod in (before.mods if before is not None else [])}
    now_ = {mod.get("origin"): mod for mod in after.mods}
    diff.added = [mod for origin, mod in now_.items() if origin not in was]
    diff.gone = [mod for origin, mod in was.items() if origin not in now_]
    for origin, mod in now_.items():
        prior = was.get(origin)
        if prior is None:
            continue
        on = bool(mod.get("active", True))
        if on != bool(prior.get("active", True)):
            (diff.on if on else diff.off).append(mod)
        if (mod.get("options") or {}, mod.get("read_as") or {}) != (
                prior.get("options") or {}, prior.get("read_as") or {}):
            diff.settings.append(mod)
    # Placed elsewhere among the mods both have: mods arriving or going are
    # not a move of the rest.
    stayed_was = [mod.get("origin") for mod in (before.mods if before is not None else [])
                  if mod.get("origin") in now_]
    stayed_now = [mod.get("origin") for mod in after.mods if mod.get("origin") in was]
    kept_place = _in_order(stayed_was, stayed_now) if stayed_was != stayed_now else set(stayed_now)
    diff.moved = [now_[origin] for origin in stayed_now if origin not in kept_place]
    switches_was = {p.get("name"): p.get("switches") or {}
                    for p in (before.profiles if before is not None else [])}
    switches_now = {p.get("name"): p.get("switches") or {} for p in after.profiles}
    diff.profiles_added = [str(name) for name in switches_now if name not in switches_was]
    diff.profiles_gone = [str(name) for name in switches_was if name not in switches_now]
    if before is not None and before.profile != after.profile:
        diff.chosen = (before.profile, after.profile)
    # Another profile's switches are the list's as much as the chosen one's,
    # whose are the mods' own on and off above.  Of the mods both sides have:
    # a mod added or gone is not a switch.
    chosen_both = after.profile if before is not None and before.profile == after.profile else None
    diff.profiles_switched = [
        str(name) for name, switches in switches_now.items()
        if name in switches_was and name != chosen_both and any(
            bool(switches.get(origin, False)) != bool(switches_was[name].get(origin, False))
            for origin in stayed_now)
    ]
    # By the checkpoint each stands at, not the revision of its file: every
    # save writes a new revision, and the list then moved after each one
    # with nothing in it moved. Work that moved is a checkpoint of its own
    # before a checkpoint of the list is taken (:func:`take`).
    held_was = before.projects if before is not None else {}
    for origin, held in after.projects.items():
        prior = held_was.get(origin, {})
        if origin not in held_was or prior.get("checkpoint") != held.get("checkpoint"):
            diff.projects.append((origin, prior, held))
    return diff


def _in_order(was: list, now_: list) -> set:
    """The mods of ``now_`` that keep their order among the rest: a longest
    run common to both, in order.  The others are the ones moved -- not
    every mod one move shifted by a place."""
    longest = [[0] * (len(now_) + 1) for _ in range(len(was) + 1)]
    for i in range(len(was) - 1, -1, -1):
        for j in range(len(now_) - 1, -1, -1):
            longest[i][j] = (longest[i + 1][j + 1] + 1 if was[i] == now_[j]
                             else max(longest[i + 1][j], longest[i][j + 1]))
    kept, i, j = set(), 0, 0
    while i < len(was) and j < len(now_):
        if was[i] == now_[j]:
            kept.add(now_[j])
            i, j = i + 1, j + 1
        elif longest[i + 1][j] >= longest[i][j + 1]:
            i += 1
        else:
            j += 1
    return kept


# -- restoring -------------------------------------------------------------------
@dataclass
class Plan:
    """What restoring a loadout's checkpoint changes, or changed."""

    diff: ListDiff
    # The mods it had then that are not put back, each with why: YOURS,
    # GONE or UNKNOWN.
    not_back: List[Tuple[dict, str]] = field(default_factory=list)
    # With each project as it was then: the origins whose project is
    # restored, and those that cannot be, each with why.
    projects: List[str] = field(default_factory=list)
    projects_not: List[Tuple[str, str]] = field(default_factory=list)


def plan(loadout: Loadout, target: LoadoutCheckpoint, with_projects: bool = False) -> Plan:
    """What :func:`restore` would change."""
    found = Plan(compare(now(loadout), target))
    for mod in found.diff.added:
        origin = str(mod.get("origin") or "")
        held = target.projects.get(origin)
        if held is not None:
            why = _project_back(held, loadout.game) if with_projects else YOURS
            if why:
                found.not_back.append((mod, why))
        elif not Path(str(mod.get("folder") or "")).is_dir():
            found.not_back.append((mod, GONE))
    if with_projects:
        back = {str(mod.get("origin") or "") for mod, _why in found.not_back}
        for origin, held in target.projects.items():
            if origin in back:
                continue
            here = is_own_mod(origin) or loadout.layer(origin) is not None or any(
                mod.get("origin") == origin for mod in found.diff.added)
            if not here:
                continue
            then = _checkpoint_in(_history_for(loadout, origin, held), held)
            if loadout.held(origin):
                found.projects_not.append((origin, GONE))
            elif then is None:
                found.projects_not.append((origin, UNKNOWN))
            elif ((not is_own_mod(origin) and loadout.layer(origin) is None)
                  or then.timestamp != _latest_timestamp(loadout, origin)
                  or loadout.changed_since_checkpoint(origin)):
                # One that stands at that checkpoint still has nothing to
                # restore.
                found.projects.append(origin)
    return found


def restore(loadout: Loadout, target: LoadoutCheckpoint, with_projects: bool = False,
            taken_in: str = "") -> Plan:
    """Put the loadout's list back as it was at ``target``, and with
    ``with_projects`` each of its projects as it was then; what it did.

    Around it, as around any restore, a checkpoint of the list as it was
    and one of what it is now: the first takes one of each project that
    moved since its own last (:func:`take`), so nothing is lost.
    """
    done = plan(loadout, target, with_projects)
    # A project taken out goes with work its own checkpoints may not hold,
    # though the list be as it was: the first checkpoint takes it too.
    taken_out = {str(mod.get("origin") or "") for mod in done.diff.gone}
    if changed_since_checkpoint(loadout) or any(
            origin in taken_out and not loadout.held(origin) and loadout.changed_since_checkpoint(origin)
            for origin in loadout.project_origins()):
        take(loadout, BEFORE % target.when, taken_in=taken_in)
    wanted = {str(mod.get("origin") or ""): mod for mod in target.mods}
    for mod in done.diff.gone:
        loadout.drop_layer(str(mod.get("origin") or ""))
    left_out = {str(mod.get("origin") or "") for mod, _why in done.not_back}
    for mod in done.diff.added:
        origin = str(mod.get("origin") or "")
        if origin in left_out:
            continue
        if origin in target.projects:
            _bring_back(loadout, mod, target.projects[origin])
        else:
            layer = ModLayer.from_dict(mod)
            loadout.set_layer(layer)
            stale.put_back(loadout, layer, again=False)
    for layer in loadout.layers:
        then = wanted.get(layer.origin)
        if then is None:
            continue
        layer.active = bool(then.get("active", True))
        layer.order = int(then.get("order") or 0)
        options, read_as = dict(then.get("options") or {}), dict(then.get("read_as") or {})
        if (layer.options, layer.read_as) != (options, read_as):
            layer.options, layer.read_as = options, read_as
            stale.put_back(loadout, layer)
    loadout.renumber_layers()
    _profiles_back(loadout, target)
    for origin in done.projects:
        held = target.projects[origin]
        checkpoint = _checkpoint_in(loadout.history_of(origin), held)
        if checkpoint is not None:
            restore_mod(loadout, origin, checkpoint, taken_in)
    if moved(loadout):
        take(loadout, AFTER % target.when, taken_in=taken_in)
    return done


def restore_mod(loadout: Loadout, origin: str, checkpoint: Checkpoint, taken_in: str = "") -> None:
    """Restore the project of ``origin`` to ``checkpoint`` of its history,
    as an ordinary restore: a checkpoint of its work first where it moved
    since its last, so nothing is lost, and one of what it is after.

    With its work, what it keeps inside somebody else's ops
    (:func:`.inside.restore`): each mod whose ops that moved is read again,
    as after an edit to it."""
    if loadout.changed_since_checkpoint(origin):
        loadout.checkpoint(BEFORE % checkpoint.when, origin=origin, loadout=taken_in)
    loadout.restore(checkpoint, origin)
    moved = inside.restore(loadout, origin, checkpoint.inside)
    if moved:
        stale.edited(loadout, dict.fromkeys(moved, 0))
    # Restored to the work it had at its last, it already has that one.
    if loadout.changed_since_checkpoint(origin):
        loadout.checkpoint(AFTER % checkpoint.when, origin=origin, loadout=taken_in)


def _profiles_back(loadout: Loadout, target: LoadoutCheckpoint) -> None:
    """The profiles as ``target`` had them, the chosen one's switches the
    layers' own as they were then."""
    loaded = {layer.origin for layer in loadout.layers}
    profiles = [
        Profile(str(raw.get("name") or ""), {
            str(origin): bool(on) for origin, on in (raw.get("switches") or {}).items()
            if origin in loaded
        })
        for raw in target.profiles if raw.get("name")
    ]
    if not profiles:
        return
    loadout.profiles = profiles
    loadout.profile = target.profile
    chosen = loadout.chosen_profile()
    chosen.switches = loadout.live_switches()


def _history_for(loadout: Loadout, origin: str, held: dict) -> List[Checkpoint]:
    """The history of the project of ``origin``: the one the loadout holds,
    or for a project it no longer has, the one in its file."""
    if is_own_mod(origin) or loadout.layer(origin) is not None:
        return loadout.history_of(origin)
    project = _project_file(held, loadout.game)
    if project is None:
        return []
    return [Checkpoint.from_dict(raw) for raw in project.history]


def _latest_timestamp(loadout: Loadout, origin: str) -> float:
    latest = loadout.latest_checkpoint_of(origin)
    return latest.timestamp if latest is not None else 0.0


def _checkpoint_in(history: List[Checkpoint], held: dict) -> Optional[Checkpoint]:
    for checkpoint in history:
        if checkpoint.timestamp == held.get("checkpoint"):
            return checkpoint
    return None


def _project_file(held: dict, game: str) -> Optional[project_files.Project]:
    """The project a checkpoint names: where it says, or where it is not
    there, the one that keeps its ModID now.  A project follows its mod's
    name to another file, and a loadout that no longer has the mod is not
    pointed there (:func:`.projects.refer`)."""
    path = str(held.get("path") or "")
    if not path:
        return None
    # Read where it says only inside the Studio's folder, as the projects
    # a loadout refers to are: a checkpoint is in the loadout's file.
    where = project_files.trusted_path(path)
    try:
        if where is not None:
            return project_files.Project.read(where)
    except project_files.ProjectError:
        pass
    mod_id = str(held.get("mod_id") or "")
    if (where is not None and where.exists()) or not mod_id:
        return None
    found = filecache.project_keeping(mod_id, game)
    try:
        return project_files.Project.read(found) if found is not None else None
    except project_files.ProjectError:
        return None


def _project_back(held: dict, game: str) -> str:
    """Why the project ``held`` names cannot bring its mod back, or ""."""
    project = _project_file(held, game)
    if project is None:
        return GONE
    if not any(raw.get("timestamp") == held.get("checkpoint") for raw in project.history):
        return UNKNOWN
    return ""


def _bring_back(loadout: Loadout, mod: dict, held: dict) -> None:
    """Put back a mod of yours the loadout had then, from its project as it
    is now: its details, its ModOps, its builds, its history and what it
    keeps inside somebody else's ops, with the loadout referring to it again
    at the revision it is at (:func:`.readback.take_in`).  Its work as it
    was then is that project's restore, which follows."""
    project = _project_file(held, loadout.game)
    if project is None:
        return
    layer = ModLayer.from_dict(mod)
    loadout.set_layer(layer)
    readback.take_in(loadout, layer, project, held="")
