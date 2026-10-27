"""Which project built each of the library's copies of a mod of yours.

A mod of yours is built into the library by Play and by an export into a
mods folder, and every loadout that has the mod can do either.  Before this,
nothing said which of them a library folder was the build of: a loadout
that took somebody's built mod for its own wrote over it, and the project it
came from wrote back over that at its next build, neither hearing of the
other (``docs/proposals/projects-and-loadouts-proposal.md``, section 2.1).

So the library's registry records, beside ``source: built``, the project
the copy was built from, with its ModID and build number
(:func:`core.library.record_built`).  Four rules follow, from section 7:

- **One writer per library folder: the mod's project.**  Building into a
  folder another project built is stopped, and says which project that is
  (:func:`place`).  A folder whose project is no longer there is nobody's.
- **A mod is known by its ModID; its folder name is where it is put.**  The
  loadout's own mod renamed moves the folder its project built, and the
  links to it, to the new name, where it used to build a second folder
  beside the first.  A loaded mod of yours keeps the folder it was built in
  -- the loadout knows it by that name, and Play builds it there -- and goes
  out under its new name only where it is exported to be shared.
- **A mod of yours given a new ModID is another mod,** and goes into a
  folder of its own, beside the one it was carried on from, which stays the
  old mod's (:func:`own_folders`).  Its folder name is its name, as the
  loadout's own mod's is.
- **Loadouts that have a moved mod follow it** when next opened, matched by
  ModID and the name it was built under before (:func:`follow`).
- **A project saved under another name takes its copies with it**
  (:func:`core.library.follow_project`).

A mod of yours with no project yet gets one at the loadout's next save; a
build before that save names the project the save will make
(:func:`project_of`).  If the loadout is never saved, the project named is
never written, and the folder is nobody's again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from . import generator, library, modfolder, projects
from .i18n import tr


def project_of(loadout, origin: str, reserve: bool = False) -> str:
    """The project ``origin`` is kept in, as a loadout writes its path, or
    "" for a mod kept in none.

    ``reserve``: one of the loadout's mods of yours with no project yet
    gets the path its next save will write it at, so a build before that
    save can say which project it was.
    """
    ref = loadout.projects.get(origin)
    if ref is not None:
        return ref.path
    if not reserve or origin not in loadout.project_origins():
        return ""
    record = loadout.mod(origin)
    name = (record.name if record is not None else "") or origin or "Anno Mod"
    taken = [projects.where(other.path) for other in loadout.projects.values()]
    ref = projects.Ref(projects.as_written(projects.new_path(name, taken)))
    loadout.projects[origin] = ref
    return ref.path


def same(one: str, other: str) -> bool:
    """Whether two projects, as loadouts write their paths, are one."""
    return bool(one and other) and projects.key_of(one) == projects.key_of(other)


def theirs(library_dir, folder_name: str, project: str) -> str:
    """The project that built ``folder_name`` in the library, where it is
    another than ``project`` and still there; "" where the folder is
    ``project``'s to build."""
    built = library.built_by(library_dir, folder_name)
    if not built or same(built, project) or not projects.where(built).is_file():
        return ""
    return built


def stopped_why(name: str, folder_name: str, project: str) -> str:
    """What is said of a mod not built because another project built its
    folder."""
    return tr(
        "%(mod)s was not built: %(folder)s in the library is the build of "
        "another project, %(project)s, and only that project builds it. "
        "Open a loadout that has that project to change the mod, or give "
        "this one another name.",
        mod=name or tr("your mod"), folder=folder_name, project=str(projects.where(project)),
    )


class Stopped(OSError):
    """A build into a folder another project built, with the sentence
    saying which."""


def place(loadout, origin: str, folder_name: str, library_dir,
          reserve: bool = False) -> Tuple[str, str, str]:
    """Where in the library ``origin`` is built, written as ``folder_name``:
    ``(the folder to build, the folder its project built under another name
    that is to move there first or "", the project that built the folder
    and stops this build or "")``.

    The loadout's own mod is the one whose folder name is its name: renamed,
    the folder its project built is moved to the new name.  A loaded mod is
    known in its loadout by its folder name, so it is built where its
    project built it, whatever an export would call it.
    """
    project = project_of(loadout, origin, reserve)
    moving = ""
    before = [name for name in library.built_from(library_dir, project) if name != folder_name]
    if len(before) == 1:
        if origin:
            if before[0] == generator.folder_for(loadout, origin):
                folder_name = before[0]
        elif not (Path(library_dir) / folder_name).exists():
            moving = before[0]
    return folder_name, moving, theirs(library_dir, folder_name, project)


def stopped(loadout, origins, parent, library_dir) -> List[Tuple[str, str]]:
    """Of ``origins``, the ones a build into the mods folder ``parent``
    would be stopped for, each with the sentence saying why."""
    from . import build

    found: List[Tuple[str, str]] = []
    if library_dir is None or not build.is_mods_folder(parent):
        return found
    for origin in origins:
        folder_name = generator.folder_for(loadout, origin)
        _write_to, link_into = build.destination(parent, folder_name, library_dir)
        if link_into is None:
            continue
        folder_name, _moving, project = place(loadout, origin, folder_name, library_dir)
        if project:
            name = generator.for_mod(loadout, origin).own.name or origin
            found.append((origin, stopped_why(name, folder_name, project)))
    return found


# -- a mod of yours given a new ModID ---------------------------------------------
def own_folder(loadout, origin: str) -> str:
    """The folder ``origin`` goes into, being a mod of yours given a new
    ModID (:func:`.generator.given_a_new_mod_id`): ``[Category] Name``, as
    the loadout's own mod's folder is named, and numbered where a loaded
    mod has that name.  "" where it stays where it is: not given a new
    ModID, or in that folder already."""
    if not generator.given_a_new_mod_id(loadout, origin):
        return ""
    wanted = generator.mod_folder_name(generator.for_mod(loadout, origin))
    if wanted.lower() == origin.lower():
        return ""
    taken = {layer.origin.lower() for layer in loadout.layers if layer.origin != origin}
    name, n = wanted, 2
    while name.lower() in taken:
        name = "%s %d" % (wanted, n)
        n += 1
    return name


def own_folders(loadout) -> List[Tuple[str, str]]:
    """Move each loaded mod of yours given a new ModID to a folder of its
    own (:func:`own_folder`), throughout ``loadout``: each as ``(the name it
    had, the name it has)``.  Before a build: Play then builds and links the
    new folder, and the old one -- the old mod, which the loadout no longer
    has -- is switched off as any mod it does not have is.  It takes the
    place in load order its new name gives it.

    Only its name moves.  It is still read from the folder it was carried
    on from, which holds what it ships besides its patch, and an update to
    that folder is an update to it.  Pointed at its new folder, which has
    nothing in it until built, its first build carried none of those files
    over: found in the hands-on run of 24 October, a readme left behind."""
    moved: List[Tuple[str, str]] = []
    for layer in list(loadout.layers):
        new = own_folder(loadout, layer.origin)
        if not new:
            continue
        old = layer.origin
        rename(loadout, old, new, layer.folder)
        moved_layer = loadout.layer(new)
        if moved_layer is not None:
            moved_layer.order = 0
        moved.append((old, new))
    if moved:
        loadout.renumber_layers()
    return moved


# -- loadouts follow a mod that moved ----------------------------------------------
def follow(loadout, library_dir) -> List[Tuple[str, str]]:
    """Bring the loaded mods of ``loadout`` whose built copy was moved to a
    new name since (:func:`core.library.move_copy`) under that name: each
    as ``(the name it had, the name it has)``.

    A mod is matched by its ModID, and where more than one copy has it, by
    the name it had among the names the copy was built under before.  A
    ModID the Studio made up from the mod's name went with the name, so a
    mod whose ModID no copy has is matched by the name it had alone.  Only
    one whose own folder is gone, with nothing in the library by its name,
    and no mod of that name loaded already.
    """
    library_dir = Path(library_dir)
    registry = library.registry(library_dir)
    moved = {
        name: entry for name, entry in registry.items()
        if entry.get("source") == library.BUILT and entry.get(library.WAS)
        and (library_dir / name).is_dir()
    }
    if not moved:
        return []
    linked = library.links(library_dir)
    done: List[Tuple[str, str]] = []
    for layer in list(loadout.layers):
        old = layer.origin
        if (
            not old or "/" in old
            or (layer.folder and Path(layer.folder).exists())
            or old in linked or (library_dir / old).exists()
            or any(other.origin.startswith(old + "/") for other in loadout.layers)
        ):
            continue
        wanted = (layer.mod_id or "").strip().lower()
        bare = modfolder.bare_name(old).lower()
        named = [
            name for name, entry in moved.items()
            if bare in {modfolder.bare_name(str(was)).lower() for was in entry[library.WAS]}
        ]
        found = [
            name for name, entry in moved.items()
            if wanted and str(entry.get(library.MOD_ID) or "").strip().lower() == wanted
        ]
        if len(found) > 1:
            found = [name for name in found if name in named]
        elif not found:
            found = named
        if len(found) != 1 or loadout.layer(found[0]) is not None:
            continue
        new = found[0]
        # Read from its link, where there is one: the library's record of
        # it, or the one a build made beside where the old one was.
        entry = linked.get(new) or {}
        at = Path(entry["mods_folder"]) / new if entry.get("mods_folder") else None
        if at is None and layer.folder and library.is_link(Path(layer.folder).parent / new):
            at = Path(layer.folder).parent / new
        rename(loadout, old, new, str(at if at is not None else library_dir / new))
        # And the ModID it was built as, where that went with the name.
        layer.mod_id = str(moved[new].get(library.MOD_ID) or layer.mod_id)
        done.append((old, new))
    return done


def rename(loadout, old: str, new: str, folder: str) -> None:
    """Call the loaded mod ``old`` ``new`` throughout ``loadout``, read from
    ``folder``: its layer, every change, op and switch of it, its history
    and the loadout's mentions of it, and wherever another mod names it.
    For a mod whose folder moved, which is a mod known by its folder's
    name."""

    def renamed(origin):
        return new if origin == old else origin

    def rekeyed(held: Dict) -> Dict:
        return {renamed(key): value for key, value in held.items()}

    def rewritten(entry) -> None:
        # An edit as a file holds it: a change's, an op's or a held edit's
        # dict, or a taken_out entry, its keeper fifth.
        if isinstance(entry, dict):
            for key in ("origin", "kept_by"):
                if entry.get(key) == old:
                    entry[key] = new
        elif isinstance(entry, list):
            if entry:
                entry[0] = renamed(entry[0])
            if len(entry) > 4 and entry[4]:
                entry[4] = renamed(entry[4])

    bare_old, bare_new = modfolder.bare_name(old), modfolder.bare_name(new)
    for layer in loadout.layers:
        if layer.origin == old:
            layer.origin, layer.folder = new, folder
        layer.patches = renamed(layer.patches)
        layer.needs = [renamed(origin) for origin in layer.needs]
        layer.copies = {renamed(key): renamed(value) for key, value in layer.copies.items()}
        layer.copy_versions = rekeyed(layer.copy_versions)
        layer.stale = [[why[0], renamed(why[1]), *why[2:]] for why in layer.stale]
    changes = list(loadout.changes.values())
    for change in changes:
        change.origin = renamed(change.origin)
        # And the mod of yours that keeps an edit inside somebody else's
        # ops (core.inside): a patch renamed keeps them still.
        if change.kept_by:
            change.kept_by = renamed(change.kept_by)
    loadout.changes = {change.key: change for change in changes}
    for op in [*loadout.ops, *loadout.narrow]:
        op.origin = renamed(op.origin)
        if op.kept_by:
            op.kept_by = renamed(op.kept_by)
    for entry in loadout.unread:
        entry.origin = renamed(entry.origin)
    for entry in [*loadout.taken_out, *loadout.held_edits]:
        rewritten(entry)
    # What each patch set aside inside a mod not loaded here, by its keeper.
    loadout.kept_aside = rekeyed(loadout.kept_aside)
    for kept in loadout.kept_aside.values():
        for entries in kept.values():
            for entry in entries:
                rewritten(entry)
    # Its history goes with it, and the loadout's knows it by the new name.
    loadout.mod_history = rekeyed(loadout.mod_history)
    for checkpoints in loadout.mod_history.values():
        for checkpoint in checkpoints:
            kept_inside = [entry for entries in checkpoint.inside.values() for entry in entries]
            for entry in [*checkpoint.changes, *checkpoint.ops, *checkpoint.narrow, *kept_inside]:
                rewritten(entry)
    for listed in loadout.history:
        for mod in listed.mods:
            if mod.get("origin") == old:
                mod["origin"], mod["folder"] = new, folder
            if "patches" in mod:
                mod["patches"] = renamed(mod["patches"])
            if "needs" in mod:
                mod["needs"] = [renamed(origin) for origin in mod["needs"]]
        listed.projects = rekeyed(listed.projects)
        for profile in listed.profiles:
            if isinstance(profile.get("switches"), dict):
                profile["switches"] = rekeyed(profile["switches"])
    loadout.editing = renamed(loadout.editing)
    loadout.home = renamed(loadout.home)
    loadout.builds = rekeyed(loadout.builds)
    loadout.projects = rekeyed(loadout.projects)
    loadout.edit_reach = rekeyed(loadout.edit_reach)
    for profile in loadout.profiles:
        profile.switches = rekeyed(profile.switches)
    if loadout.read_stack:
        loadout.read_stack = [[renamed(entry[0]), *entry[1:]] for entry in loadout.read_stack]
    loadout.own.stale = [[why[0], renamed(why[1]), *why[2:]] for why in loadout.own.stale]
    for held in (loadout.provenance, loadout.listed):
        if bare_old in held and bare_new not in held:
            held[bare_new] = held.pop(bare_old)
    if old in loadout.emptied:
        loadout.emptied.discard(old)
        loadout.emptied.add(new)
    # What was worked out of the ops under the old name.
    loadout._op_memo = ()
    loadout._narrow_memo = ()

