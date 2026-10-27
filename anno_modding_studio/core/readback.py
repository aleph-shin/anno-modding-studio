"""A project read back into a loadout: its ModOps become the mod's changes.

A loadout keeps what each mod of yours comes to in it, as changes, beside
the revision of the mod's project they were read from
(:attr:`.projects.Ref.read_at`).  A project another loadout has changed
since is opened as it is now -- its ops, the ModOps the Studio wrote for
each edit (:mod:`.narrow`) and the ops carried unread -- and held
(:attr:`.projects.Ref.held`): the changes beside them are this loadout's
older reading.  Here they are read again, where the mod stands in this
loadout's load order, and take the older reading's place.  Section 8 of
the projects-and-loadouts proposal, its phase 6b.

**Read in the background, as a stale reading is.**  :func:`.stale.follow_projects`
marks the mod and every mod after it; :func:`.stale.snapshot` hands the
reader this mod's ModOps as its text (:func:`shown`); and :func:`.stale.take`
gives the reading to :func:`take`.  The reading is the one the Mod file tab's
text gets after an edit (:func:`.modfile.take_reading`), so an op kept as
written, a group, a file an ``<Include>`` names and an op carried unread are
each read as the op they are.

**The Studio's own ModOps are read as kept ops, and come back as changes.**
In the text they stand where the export writes them: each among the mod's
ops by its place (``order``), after the kept op of the same place, and in
the order :attr:`.changes.Loadout.narrow` has them.  Read so, each is an op
with its answers.  Those answers are then made what an edit in Fields is: a
change of the mod's own, made here, no op's answer, switched and noted as
its ModOp is, and tied to it by :attr:`.changes.Change.written_as`, so the
next :func:`.narrow.write` knows each ModOp for the one it was and keeps its
uid.  An answer laid over one of a kept op's is the exception to it, as one
typed in Fields is.  Its old value is what the mods before it leave there
in this loadout, which is the point of reading it here.

**Unless that would rewrite it.**  The reader reads a whole node of an
asset's identity swapped as the fields that moved in it, and a ModOp aimed
at something no mod before it makes here comes to nothing.  Changes made of
those would be written as another ModOp, or as none.  Such a ModOp is kept
as the op it is, an op of the mod's own as one typed in the Mod file tab
is, with its uid, its note and its switch: a read-back never rewrites what
the project holds.

**What this loadout did to the mod and never saved into its project is
given up.**  It could not be saved there: the project was held.  Those are
the changes made in Fields that no ModOp was written for, and they are
counted, for the window to say how many.  They are not laid back over what
the project holds now: that is the other loadout's later work, and a change
read by a Studio of another version can look the same as one of them.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from . import builtby, effective, games, generator, inside, modfile, narrow, patching
from . import library as library_mod
from . import projects as project_files
from .changes import (
    OP_REPLACE,
    Change,
    Checkpoint,
    Loadout,
    ModLayer,
    Op,
    Unread,
    emit_order,
    is_own_mod,
)


def moved(loadout: Loadout) -> List[str]:
    """The mods of yours whose project another loadout changed since this
    one read it, and which are still to be read again."""
    return [
        origin for origin, ref in loadout.projects.items()
        if ref.held == project_files.MOVED and (is_own_mod(origin) or loadout.layer(origin) is not None)
    ]


def shown(loadout: Loadout, origin: str) -> modfile.Shown:
    """``origin``'s file as its project holds it: its ops, the ModOps the
    Studio wrote for it each written as an op among them, and the ops
    carried unread.  Nothing of the changes this loadout read of it before."""
    view = generator.for_mod(loadout, origin)
    view.changes = {}
    # After the kept ops, so that one of the same place goes first, as the
    # export writes a block of changes after the op it follows.
    view.ops = view.ops + [copy.copy(op) for op in view.narrow]
    # Each as it is held, as the export writes it, so that what is read is
    # what was written.
    held = frozenset(op.uid for op in view.narrow)
    view.narrow = []
    return modfile.shown_of(view, origin, as_held=held)


def take(loadout: Loadout, origin: str, reread: modfile.Reread) -> Optional[int]:
    """Put a reading of ``origin``'s project (:func:`shown`) in place of the
    changes this loadout held of it, and let go of its hold; how many edits
    made here and not saved into the project were given up.  None where the
    project's text is another by now than the one read: nothing is taken.
    """
    was = shown(loadout, origin)
    if was.text != reread.text:
        return None
    ref = loadout.projects[origin]
    written = {op.uid: op for op in loadout.narrow if op.origin == origin}
    items = modfile.read_as(loadout, origin, was)
    mine = loadout.changes_of(origin)
    given_up = sum(
        1 for change in mine if change.authored and not change.op_uid and not change.written_as
    )
    # What players see an asset the mod adds called is on the change that
    # adds it, which is this loadout's: kept by the asset.
    names = {
        effective.new_asset_guid(change): change.in_game_name
        for change in mine if change.creates_asset and change.in_game_name
    }
    for change in mine:
        loadout.changes.pop(change.key, None)
    if not modfile.take_reading(loadout, origin, reread, was=was):
        return None
    # One of the Studio's that could not be read here -- aimed at an asset
    # no mod before it makes in this loadout -- comes back carried unread,
    # which has no uid: it is put back as the op it is.
    unread: Dict[int, Op] = {}
    for entry in loadout.unread:
        item = items[entry.order] if entry.origin == origin and 0 <= entry.order < len(items) else None
        if item is not None and item.op is not None and item.op.uid in written:
            unread[id(entry)] = item.op
    loadout.unread = [entry for entry in loadout.unread if id(entry) not in unread]
    # Each thing where the project has it: the reading numbers them by
    # where they are in the text.
    places = _places(items)
    for op in loadout.ops:
        if op.origin == origin and op.order in places:
            op.order = places[op.order]
    for entry in loadout.unread:
        if entry.origin == origin and entry.order in places:
            entry.order = places[entry.order]
    if unread:
        _put_back(loadout, origin, [replace(op, authored=True, made=0, matched=[]) for op in unread.values()])
        loadout.count_unread({origin})
    orders = {op.uid: op.order for op in loadout.ops if op.origin == origin}
    answers: Dict[str, List[Change]] = {uid: [] for uid in written}
    for change in loadout.changes_of(origin):
        if change.op_uid in written:
            answers[change.op_uid].append(change)
        elif change.op_uid:
            change.order = orders.get(change.op_uid, change.order)
    # One whose changes would be written as another ModOp, or as none, is
    # kept as the op it is.
    game = games.get(loadout.game)
    kept = {uid for uid, under in answers.items() if not _written_again(written[uid], under, game)}
    loadout.ops = [op for op in loadout.ops if op.uid not in written or op.uid in kept]
    loadout.narrow = [op for op in loadout.narrow if op.uid not in kept]
    for uid, under in answers.items():
        if uid in kept:
            continue
        record = written[uid]
        notes = record.note.split("\n") if record.note else []
        for at, change in enumerate(under):
            change.op_uid = ""
            change.search = ""
            change.authored = True
            change.written_as = uid
            change.order = record.order
            change.active = record.active
            # A ModOp for several changes is written under the note of each.
            change.note = notes[at] if len(notes) == len(under) else (notes[0] if notes and at == 0 else "")
            if change.uid and change.op == OP_REPLACE:
                answer = loadout.changes.get((origin, change.guid, change.path, change.op, ""))
                if answer is not None and answer.op_uid:
                    change.exception_of = answer.op_uid
    for change in loadout.changes_of(origin):
        if change.creates_asset and not change.in_game_name:
            change.in_game_name = names.get(effective.new_asset_guid(change), "")
    ref.held = ""
    ref.read_at = ref.revision
    narrow.write(loadout)
    settle(loadout, origin)
    return given_up


def join_patch(loadout: Loadout, theirs: str, project, name: str = "") -> ModLayer:
    """Make your patch of the loaded mod ``theirs`` by taking up the project
    of the patch you made of it elsewhere, at ``project``, rather than a
    second patch of the same mod (section 5.1 of the projects and loadouts
    proposal: a patch is how a mod should have been, wherever it is used).

    The patch is put in the loadout as :func:`.patching.make` puts one, and
    what its project holds is taken into it: its details, its ModOps, its
    builds and history, and what it keeps inside their mod.  Then it is
    held as a project another loadout changed is (:data:`.projects.MOVED`),
    so the next reading reads its ModOps where it stands in this loadout
    (:func:`.stale.follow_projects`).  ``name`` is its folder's name, where
    the project has built one; :func:`.patching.suggest_name` otherwise.
    Raises :class:`.projects.ProjectError` where the project cannot be read.
    """
    found = project_files.Project.read(project)
    layer = patching.make(loadout, theirs, name)
    take_in(loadout, layer, found)
    return layer


def join(loadout: Loadout, origin: str, project) -> int:
    """Take up the project at ``project`` for ``origin``, a mod of yours
    loaded here from its folder, which that project keeps already: one
    project to a ModID (section 4.6 of the projects and loadouts proposal).
    For a mod with your name on it made a project here, and for one you
    carry on with.  How many edits made here to it were given up.

    The project is what the mod is, as when a loadout that has it opens: what
    this loadout read of the folder goes, and what the project holds is taken
    in as :func:`join_patch` takes it, then read where the mod stands here.
    So the next save writes nothing over the project that this loadout did
    not do to it.  What was done to the mod here before is given up, as
    :func:`take` gives up what a held project never got.  Its modinfo as
    read stays the folder's, which is what says whether the folder moves
    (:func:`.builds.updated_on_disk`).  Raises
    :class:`.projects.ProjectError` where the project cannot be read.
    """
    found = project_files.Project.read(project)
    layer = loadout.layer(origin)
    if layer is None:
        raise project_files.ProjectError(str(project))
    given_up = (
        sum(1 for op in loadout.ops if op.origin == origin and op.authored)
        + sum(1 for change in loadout.changes_of(origin) if change.authored and not change.op_uid)
        + sum(1 for entry in loadout.taken_out if entry[0] == origin)
        + sum(1 for held in loadout.held_edits if held.get("origin") == origin)
    )
    for change in loadout.changes_of(origin):
        loadout.changes.pop(change.key, None)
    loadout.ops = [op for op in loadout.ops if op.origin != origin]
    loadout.narrow = [op for op in loadout.narrow if op.origin != origin]
    loadout.unread = [entry for entry in loadout.unread if entry.origin != origin]
    loadout.taken_out = [entry for entry in loadout.taken_out if entry[0] != origin]
    loadout.held_edits = [held for held in loadout.held_edits if held.get("origin") != origin]
    loadout._op_memo = ()
    take_in(loadout, layer, found, keep=("declared",))
    return given_up


def take_in(loadout: Loadout, layer: ModLayer, project: project_files.Project, keep=(),
            held: str = project_files.MOVED) -> None:
    """What ``project`` holds, into ``layer`` and the loadout: its details
    but those ``keep`` names, its release facts where it is the mod the
    loadout carries on with, its ModOps, the Studio's and the ones carried
    unread, its build, its history, and what it keeps inside somebody else's
    ops; with the loadout referring to it at the revision it is at.

    ``held`` :data:`.projects.MOVED` has it read where the mod stands, as a
    project another loadout changed is (:func:`.stale.follow_projects`): for
    :func:`join_patch` and :func:`join`.  "" takes it as read, for a restore
    that puts a mod back to be restored itself next (:mod:`.histories`)."""
    origin = layer.origin
    details = project.details
    for key in project_files.layer_details():
        if key in details and key not in keep:
            setattr(layer, key, details[key])
    if origin == loadout.home:
        # What is known of its releases, which the loadout keeps at its top
        # for the mod it carries on with.
        for key in project_files.release_facts():
            if key in project.releases:
                setattr(loadout, key, copy.deepcopy(project.releases[key]))
    for raw in project.entries("ops", origin):
        loadout.set_op(Op.from_dict(raw))
    loadout.narrow = loadout.narrow + [Op.from_dict(raw) for raw in project.entries("narrow", origin)]
    loadout.unread.extend(Unread.from_dict(raw) for raw in project.entries("unread", origin))
    loadout.count_unread({origin})
    if isinstance(project.part.get("build"), dict):
        loadout.builds[origin] = dict(project.part["build"])
    history = [Checkpoint.from_dict(raw) for raw in project.history]
    if history:
        loadout.mod_history[origin] = history
    # What a newer Studio keeps there goes back into the file from the
    # reference's ``kept``, not from here.
    kept_inside = {key: entries for key, entries in project.inside.items() if key in project_files.INSIDE_KEYS}
    if kept_inside:
        loadout.kept_aside[origin] = copy.deepcopy(kept_inside)
        inside.take_back(loadout, {other.origin for other in loadout.layers})
    loadout.projects[origin] = project_files.Ref(
        project_files.as_written(project.path), mod_id=project.mod_id,
        read_at=project.revision, revision=project.revision, held=held, kept=project.kept,
    )
    settle(loadout, origin)


def settle(loadout: Loadout, origin: str) -> None:
    """Take what the loadout holds of ``origin``'s project now as what it
    was read as, so that a save writes the project only when something
    moves in it from here on -- and, where it is held, does not refuse to
    save the rest for it."""
    ref = loadout.projects[origin]
    ref.digest = project_files.digest(
        project_files.split(loadout.to_dict(), [origin], loadout.kept_aside).get(origin, {})
    )


# -- a project that cannot be found -------------------------------------------
def stand_in(loadout: Loadout, origin: str, library: Optional[Path] = None) -> Optional[Path]:
    """The library's build of a mod of yours whose project is missing
    (section 8 of the proposal): the folder it was read from where it has
    one, else the copy in ``library`` with its ModID and the folder name it
    is built under (:func:`.generator.folder_for`), which is what a reading
    knows it by.  Not one the registry says another project built, which is
    still there (:func:`.builtby.theirs`): that is the build of that
    project's mod, with the same ModID.  For the loadout's own mod, which
    has no folder of its own, the copy the library's registry says its
    project built (:func:`.library.built_from`), or else the one copy with
    its ModID that no other project built.  None where there is none.

    The mod is then read from that folder like anybody else's
    (:func:`.stale.follow_projects`), and is not edited: its project is
    held, so nothing goes into it, until it is found again -- by its ModID
    (:mod:`.changes`), or where it was -- when the loadout is next opened.
    """
    ref = loadout.projects.get(origin)
    layer = loadout.layer(origin)
    if ref is None or ref.held != project_files.MISSING or (not is_own_mod(origin) and layer is None):
        return None
    if layer is not None and layer.folder and Path(layer.folder).is_dir():
        return Path(layer.folder)
    if library is None:
        return None
    if is_own_mod(origin):
        built = library_mod.built_from(library, ref.path)
        if built:
            return Path(library) / built[0]
    wanted = ref.mod_id.strip().lower()
    if not wanted:
        return None
    if is_own_mod(origin):
        # Its name went with its project, so its folder's name cannot say.
        found = [
            mod for mod in library_mod.catalogue(library)
            if mod.mod_id.strip().lower() == wanted
            and not builtby.theirs(library, mod.target.name, ref.path)
        ]
        return found[0].target if len(found) == 1 else None
    named = generator.folder_for(loadout, origin).split("/")[-1].lower()
    found = [
        mod for mod in library_mod.catalogue(library)
        if mod.mod_id.strip().lower() == wanted and mod.target.name.lower() == named
        and not builtby.theirs(library, mod.target.name, ref.path)
    ]
    return found[0].target if found else None


def _written_again(record: Op, under: List[Change], game) -> bool:
    """Whether the changes read out of one of the Studio's ModOps are
    written as that ModOp again, letter for letter (:func:`.narrow.write`).

    Nearly always.  Not where the reader reads it more narrowly than it is
    written -- a node of an asset's identity swapped whole comes back as the
    fields that moved in it -- nor where it comes to nothing here, aimed at
    something no mod before it makes in this loadout.
    """
    if not under:
        return False
    ops = generator.written_for(emit_order(under), game)
    return (
        len(ops) == 1 and len(ops[0][0]) == len(under)
        and "\n".join(ops[0][2])[len(generator.INDENT):] == record.source
    )


def _put_back(loadout: Loadout, origin: str, ops: List[Op]) -> None:
    """Put ``ops`` among ``origin``'s, where their places have them."""
    first = next((at for at, op in enumerate(loadout.ops) if op.origin == origin), len(loadout.ops))
    others = [op for op in loadout.ops if op.origin != origin]
    mine = sorted([op for op in loadout.ops if op.origin == origin] + ops, key=lambda op: op.order)
    first = min(first, len(others))
    loadout.ops = others[:first] + mine + others[first:]


def _places(items) -> Dict[int, int]:
    """Where each thing of the text stands in the project, by where it is
    in the text (:func:`.modfile.read_as`): what the reading numbers it."""
    places: Dict[int, int] = {}
    for at, item in enumerate(items):
        if item is None:
            continue
        if item.op is not None:
            places[at] = item.op.order
        elif item.unread is not None:
            places[at] = item.unread.order
    return places
