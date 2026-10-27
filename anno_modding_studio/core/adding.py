"""A mod of yours added to a loadout that is not open, or taken out of one.

The Export tab's *Add to loadout…* (docs/proposals/export-tab-proposal.md,
section 5, and the projects and loadouts proposal's phase 10).  The other
loadout's file is read and written whole (:meth:`.changes.Loadout.load`,
:meth:`~.changes.Loadout.to_save`) without being shown, and nothing in it is
read again from the game files now.

**The mod goes in as its project, never read.**  A loaded mod of yours kept
by its project, with what the project holds taken in as joining one takes
it (:func:`.readback.take_in`, :func:`.readback.join_patch`), and the
loadout's reference to the project at a revision no project has
(:data:`NEVER_READ`).  So the loadout reads it the next time it is opened,
and before its next Play, where the mod stands in its load order: as it
reads a project another loadout changed (:func:`.stale.follow_projects`).
Until then the file has the mod and not its reading, which nothing but that
loadout's own screen asks for.  A save of the file writes nothing over the
project: the reference is held as a moved one is, and nothing in its part
moved.

**A patch brings the mods it needs** (:func:`needed`): the mod it patches
and the others it needs, from the library, where the loadout lacks them --
each to be read from its folder when the loadout is opened, as a mod updated
on disk is (:data:`.stale.UPDATED`) -- and switched on where the loadout has
it switched off.

**Where the loadout is what the game loads,** :func:`switch_live` switches
the one mod on or off in the game at once and moves nothing else: its link
in the mods folder and, for Anno 117, its line in the Studio's profile.
Only where the mods folder is as that loadout's Play left it
(:func:`.truth.fingerprint`): anything else is that loadout's own Play to
work out, from its readings.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

from . import (
    build,
    disk,
    games,
    generator,
    library,
    modfolder,
    modinfo,
    readback,
    saving,
    stale,
    switches,
    truth,
)
from . import projects as project_files
from .changes import Loadout, ModLayer, is_own_mod
from .i18n import tr, trn

# The revision a loadout is said to have read a project at that it has
# never read: no project has it, so the loadout reads it when opened.
NEVER_READ = -1


class AddError(ValueError):
    """Why a mod cannot be added to, or taken out of, a loadout: a sentence."""


@dataclass
class Added:
    """What adding a mod to a loadout did there: the mod's origin in it, the
    mods it needs that came with it, and those switched on for it, by name;
    and whether it had the mod from its folder already, and now reads it
    from its project (:func:`from_folder`)."""

    origin: str
    brought: List[str] = field(default_factory=list)
    switched_on: List[str] = field(default_factory=list)
    taken_up: bool = False


def project_of(source: Loadout, origin: str) -> Path:
    """The file of the project ``origin`` is kept in.  Raises AddError for a
    mod with none yet: the loadout is saved first."""
    ref = source.projects.get(origin)
    if ref is None:
        raise AddError(tr("Save this loadout first: the mod's project is made by its first save."))
    return project_files.where(ref.path)


def needed(source: Loadout, origin: str) -> List[str]:
    """The mods ``origin`` needs in a loadout, by their origins in
    ``source``: for a patch, the mod it patches and the others it needs
    (:attr:`.changes.ModLayer.needs`); nothing for any other mod."""
    layer = source.layer(origin)
    if layer is None or not layer.patches:
        return []
    return [layer.patches] + [other for other in layer.needs if other != layer.patches]


def having(target: Loadout, project) -> Optional[str]:
    """The origin of the mod ``target`` keeps in the project at
    ``project``, or None where it has none."""
    key = project_files.key_of(project)
    for origin, ref in target.projects.items():
        if project_files.key_of(ref.path) == key:
            return origin
    return None


def found_in(target: Loadout, layer: ModLayer) -> Optional[ModLayer]:
    """``target``'s copy of the mod ``layer`` is, by its ModID or its
    folder's name (:func:`.modfolder.match_keys`); None for none."""
    keys = modfolder.match_keys(layer.mod_id, layer.origin)
    for mine in target.layers:
        if modfolder.match_keys(mine.mod_id, mine.origin) & keys:
            return mine
    return None


def built_id(source: Loadout, origin: str) -> str:
    """The ModID ``origin`` of ``source`` builds with, lower-cased: the one
    its modinfo will declare (:func:`.modinfo.written_mod_id`), which is what
    the game knows it by.  Not the one its folder declares: a mod carried on
    and given a new ModID is another mod to the game."""
    return modinfo.written_mod_id(generator.for_mod(source, origin)).strip().lower()


def keys_of(source: Loadout, origin: str) -> set:
    """What a loadout that has ``origin`` of ``source`` from its folder knows
    it by, as the files cache keeps it (:func:`.modfolder.match_keys`): the
    ModID it builds with."""
    return modfolder.match_keys(built_id(source, origin), "")


def from_folder(target: Loadout, source: Loadout, origin: str) -> Optional[ModLayer]:
    """``target``'s copy of ``origin``, a mod of yours, where it has the mod
    read from its folder and kept by no project: a loadout that had the mod
    before its project was made (section 5.5 of the projects and loadouts
    proposal).  Adding the mod there takes the project up for it, as making
    a project of it there would (:func:`.readback.join`).  Found by the
    ModID the mod builds with (:func:`built_id`); None for none."""
    wanted = built_id(source, origin)
    for there in target.layers:
        if there.origin in target.projects or target.yours(there):
            continue
        if wanted and (there.mod_id or "").strip().lower() == wanted:
            return there
    return None


def add(target: Loadout, source: Loadout, origin: str, mods_folder) -> Added:
    """Put ``origin``, a mod or a patch of yours in ``source``, into
    ``target``, a loadout read from its file, with the mods it needs; what
    was done.  Switched on in its chosen profile and, a mod no other profile
    mentions being off there, off in the rest.  Nothing is written: the
    caller saves ``target``.  Raises AddError where it cannot be done, and
    :class:`.projects.ProjectError` where the project cannot be read."""
    project = project_of(source, origin)
    # Where that loadout plays from: its own mods folder, where it has one.
    mods_folder = target.mods_folder or mods_folder
    already = having(target, project)
    if already is not None:
        return Added(already)
    name = generator.folder_for(source, origin).split("/")[-1]
    done = Added("")
    patched, needs = "", []
    patch = source.layer(origin)
    for other in needed(source, origin):
        theirs = source.layer(other)
        if theirs is None:
            continue
        there = found_in(target, theirs)
        if there is None:
            there = _bring(target, theirs, mods_folder)
            done.brought.append(there.label)
        elif not there.active:
            there.active = True
            done.switched_on.append(there.label)
        if patch is not None and other == patch.patches:
            patched = there.origin
        else:
            needs.append(there.origin)
    folder_copy = None if patched else from_folder(target, source, origin)
    if patched:
        layer = readback.join_patch(target, patched, project, name=name)
        layer.needs = needs
    elif folder_copy is not None:
        # Its project, and not a second copy beside it: what the loadout read
        # of the folder goes, and the project is read in its place.
        if folder_copy.origin != target.home:
            target.set_layer(replace(folder_copy, kept=True))
        given_up = readback.join(target, folder_copy.origin, project)
        if given_up:
            raise AddError(trn(
                "It has the mod from its folder, with %(n)s edit of its own to it. "
                "Open it, and make the mod yours there in Currently editing.",
                "It has the mod from its folder, with %(n)s edits of its own to it. "
                "Open it, and make the mod yours there in Currently editing.",
                given_up,
            ))
        joined = target.layer(folder_copy.origin)
        if joined is None:
            raise AddError(tr("It has the mod from its folder, and it could not be taken up."))
        layer = joined
        done.taken_up = True
    else:
        clash = target.layer(name)
        if clash is not None:
            # The folder the mod builds into holds another mod there: one of
            # another ModID, such as the mod before it was given a new one.
            raise AddError(tr(
                "It has %(mod)s in %(folder)s, the folder this mod builds into, with the "
                "ModID %(theirs)s. This mod builds with %(ours)s, so to the game it is "
                "another mod, and the two cannot share the folder.",
                mod=clash.label, folder=clash.origin, theirs=clash.mod_id or "?",
                ours=built_id(source, origin),
            ))
        layer = ModLayer(origin=name, folder=str(Path(mods_folder) / name), kept=True)
        target.set_layer(layer)
        readback.take_in(target, layer, project_files.Project.read(project))
    layer.active = True
    done.origin = layer.origin
    target.projects[layer.origin].read_at = NEVER_READ
    readback.settle(target, layer.origin)
    return done


def _bring(target: Loadout, theirs: ModLayer, mods_folder) -> ModLayer:
    """Somebody else's mod a patch needs, put into ``target`` as
    ``theirs`` is in the loadout the patch came from, to be read from its
    folder when ``target`` is opened."""
    layer = replace(
        ModLayer.from_dict(theirs.to_dict()),
        folder=theirs.folder or str(Path(mods_folder) / theirs.origin),
        order=0, active=True, unread=0, edited=False, from_collection="",
        stale=[[stale.UPDATED, theirs.origin]],
    )
    target.set_layer(layer)
    return layer


def take_out(target: Loadout, project) -> str:
    """Take the mod ``target`` keeps in the project at ``project`` out of
    it, the project untouched; its name, or "" where ``target`` has none.
    Nothing is written: the caller saves ``target``.  Raises AddError for
    the mod the loadout is, or carries on with."""
    origin = having(target, project)
    if origin is None:
        return ""
    if is_own_mod(origin) or origin == target.home:
        raise AddError(tr("It is the mod that loadout is, so it cannot be taken out of it."))
    record = target.mod(origin)
    name = record.label if record is not None else origin
    target.drop_layer(origin)
    target.narrow = [op for op in target.narrow if op.origin != origin]
    for kept in (target.projects, target.builds, target.mod_history, target.kept_aside):
        kept.pop(origin, None)
    for profile in target.profiles:
        profile.switches.pop(origin, None)
    if target.editing == origin:
        target.editing = ""
    return name


def add_by_files(target_path, source_path, origin: str, mods_folder, library_dir) -> Added:
    """Add ``origin`` of the loadout at ``source_path`` to the loadout at
    ``target_path``, neither of them open: built into the library without a
    link, and both written in one save -- the source for the build number
    its build took.  For *What I play now…* (section 5.7 of the Export tab
    proposal), where the loadout it goes into has only just been made.
    Raises AddError, OSError or LoadoutError, writing nothing of either."""
    source, target = Loadout.load(Path(source_path)), Loadout.load(Path(target_path))
    done = add(target, source, origin, target.mods_folder or mods_folder)
    built = build.write(source, [origin], mods_folder, library_dir, link=False)
    if built.failed:
        raise AddError("\n\n".join(why for _name, why in built.failed))
    save_all({Path(target_path): target, Path(source_path): source})
    return done


def save_all(loadouts: Dict[Path, Loadout]) -> None:
    """Write each of ``loadouts`` to its file, all or none of them, in one
    journal (:mod:`.saving`).  Raises LoadoutError, writing nothing, as a
    save does."""
    pending = [loadout.to_save(path) for path, loadout in loadouts.items()]
    files: Dict[Path, Optional[bytes]] = {}
    for one in pending:
        files.update(one.files)
    saving.write_all(files)
    for one in pending:
        one.written()


# -- the loadout the game loads ------------------------------------------------
def is_live(path, library_dir) -> bool:
    """Whether the loadout at ``path`` is what the game loads now."""
    held = library.live(Path(library_dir)) if library_dir else ""
    return bool(held) and disk.same_file(held, path)


def as_play_left_it(target: Loadout, mods_folder, library_dir) -> bool:
    """Whether the mods folder is as the last Play of the loadout that is
    live left it: nothing switched since, by the game or anybody."""
    stamp = str(library.live_record(Path(library_dir)).get("fingerprint") or "")
    if not stamp:
        return False
    try:
        found = truth.read(target, mods_folder, library_dir=library_dir)
    except OSError:
        return False
    return stamp in (truth.fingerprint(found), truth.fingerprint_before(found))


def switch_of(target: Loadout, origin: str) -> tuple:
    """``(its folder's name, its ModID)``: what switches ``origin`` of
    ``target`` in the game, for :func:`switch_live`.  Asked before the mod
    is taken out of ``target``."""
    record = target.mod(origin)
    mod_id = (record.mod_id if record is not None else "").strip().lower()
    if not mod_id and record is not None:
        mod_id = modinfo.written_mod_id(generator.for_mod(target, origin)).strip().lower()
    return origin.split("/")[-1], mod_id


def switched_already(target: Loadout, switch: tuple, on: bool, mods_folder) -> bool:
    """Whether the mod ``switch`` names is ``on`` in the game already, or
    off: for Anno 1800, by its link in the mods folder, which is all that
    switches a library mod there.  Never for Anno 117, whose profile says
    too.  Taking out a mod never switched on, the Studio asked to switch it
    off (R3-3, hands-on run 3)."""
    if target.game != games.ANNO_1800:
        return False
    return os.path.lexists(Path(mods_folder) / switch[0]) == on


def switch_live(target: Loadout, path, switch: tuple, on: bool, mods_folder, library_dir) -> str:
    """Switch one mod of ``target``, the loadout at ``path`` and the one the
    game loads, on or off in the game, and nothing else: its link in the
    mods folder and, for Anno 117, its line in the Studio's profile.
    ``switch`` is what switches it (:func:`switch_of`).  Then the live
    record takes the folder as it is now.  "" where it is done; otherwise
    why not, and nothing moved."""
    mods_folder, library_dir = Path(mods_folder), Path(library_dir)
    name, mod_id = switch
    profile = None
    if target.game == games.ANNO_117:
        pointer = switches.pointer_folder(mods_folder, target.game)
        profile = switches.active_profile(pointer) if pointer is not None else None
        if profile is None or not (switches.is_studios(profile) or switches.named_as_studios(profile)):
            return tr("The game's live profile is not the Studio's.")
        if not mod_id:
            return tr("%s declares no ModID for the profile to name.", name)
    link = mods_folder / name
    try:
        if on:
            if not library.is_link(link):
                put = library.relink(library_dir, name)
                if put is None:
                    library.link(library_dir / name, mods_folder, name)
        elif library.is_link(link) and not library.unlink(library_dir, link):
            library.remove_link(link)
        if profile is not None:
            disk.write_text(profile, switches.rewrite_profile(switches.read_profile(profile), {mod_id: on}))
    except OSError as exc:
        return str(exc)
    with contextlib.suppress(OSError):
        found = truth.read(target, mods_folder, library_dir=library_dir)
        held = library.live_record(library_dir)
        library.set_live(library_dir, path, fingerprint=truth.fingerprint(found),
                         profile=str(held.get("profile") or ""))
    return ""
