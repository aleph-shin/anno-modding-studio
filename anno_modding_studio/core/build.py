"""Writing a loadout's mods out as folders: one loop, for an export and for Play.

Each mod is its ModOps and its modinfo, in a folder named for it, with
whatever the mod shipped besides its patch carried across.  Into a mods
folder it goes into the Studio's library and is linked in, unless a real
folder of the user's own is already at that name, which is written over as it
always was; anywhere else it is written where it was asked.  A mod of your own
is numbered as it goes (:mod:`core.builds`).

The Export tab and Play both write through :func:`write`, and so does each
of the tab's buttons: one mod as a folder, one as a zip, yours, all of them.
They were copies of this loop, and the two for one mod wrote the preview as
it was last drawn where the others wrote the loadout.
"""

from __future__ import annotations

import ntpath
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

from . import builds, builtby, games, generator, library, modinfo, texts
from .changes import is_own_mod
from .i18n import tr


@dataclass
class Built:
    """What writing did: the folders the game sees, what could not be written
    and why, and whether a build number moved on -- an edit to the loadout."""

    written: List[Path] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    # The origin of each of ``written``, in step with it: ``failed`` names a
    # mod as it is shown, which is not how its history is kept.
    origins: List[str] = field(default_factory=list)
    numbered: bool = False


def modops_xml(exported) -> str:
    """``exported``'s ModOps document: the file an export writes, and what
    the Studio shows of it."""
    return generator.build_modops_xml(exported)


def written(exported) -> generator.Written:
    """:func:`modops_xml`, with which lines each op is on."""
    return generator.written(exported)


def as_files(exported) -> generator.Written:
    """``exported`` as an export writes it: its patch, and beside it each
    file the patch's ``<Include>`` lines name.  :func:`modops_xml` is the
    same ModOps in the same order, in one file."""
    return generator.written(exported, files=True)


def written_inline(exported, as_held=frozenset()) -> generator.Written:
    """``exported`` as one text that says which of its lines are each of
    the mod's files (:attr:`generator.Written.regions`): what the Mod file
    tab shows and types in.  Each ``<Include>`` line that names a file is
    in it, and the file's ops follow the line.  ``as_held``: see
    :func:`generator.written`."""
    return generator.written(exported, files=generator.INLINE, as_held=as_held)


def all_of(whole: generator.Written) -> str:
    """Every file of a written mod as one text, for telling one build from
    another: the patch, then each other file under its name."""
    return whole.text + "".join(
        "\n%s\n%s" % (name, text) for name, text in sorted(whole.files.items())
    )


def fragment_xml(loadout, origin: str, keys=(), ops=(), unread=()) -> str:
    """The part of ``origin``'s ModOps document that a few of its rows come to.

    ``keys`` are changes, ``ops`` are kept ops' uids and ``unread`` the positions
    of ops the Studio could not read.  Written by the same generator as the
    whole document, since what one change is written as depends on the ones
    beside it: a removal and an addition go out as one replace.

    One of a kept op's answers is written as the op, so asking for it gives
    the op.  A row that is switched off is in the file as a comment, and
    that is what it is shown as.
    """
    return fragment_written(loadout, origin, keys, ops, unread).text


def fragment_written(loadout, origin: str, keys=(), ops=(), unread=()) -> generator.Written:
    """:func:`fragment_xml`, with which lines each op is on."""
    view = fragment_view(loadout, origin, keys, ops, unread)
    # A few of the mod's ops and no file: one blank line before each, which
    # is also where the XML view draws what it says over an op.
    return generator.written(view, layout=False)


def fragment_view(loadout, origin: str, keys=(), ops=(), unread=()):
    """The mod :func:`fragment_xml` writes: ``origin`` as
    :func:`generator.for_mod` has it, holding only what those rows come to."""
    view = generator.for_mod(loadout, origin)
    keys = {tuple(key) for key in keys}
    ops = set(ops)
    held = {op.uid for op in view.ops}
    ops.update(
        change.op_uid
        for key, change in view.changes.items()
        if key in keys and change.op_uid in held
    )
    view.changes = {
        key: change
        for key, change in view.changes.items()
        if key in keys or change.op_uid in ops
    }
    view.ops = [op for op in view.ops if op.uid in ops]
    positions = set(unread)
    view.unread = [entry for entry in view.unread if entry.order in positions]
    return view


def source_folder(loadout, origin: str) -> Optional[Path]:
    """The folder a loaded mod was read out of, for what it ships besides
    its patch.  None for a mod being authored here, which shipped nothing,
    and for one whose folder is no longer where it was."""
    layer = loadout.layer(origin)
    if layer is None or not layer.folder:
        return None
    folder = Path(layer.folder)
    return folder if folder.is_dir() else None


def carry_folder(loadout, origin: str) -> Optional[Path]:
    """Where a build of a loaded mod copies what it ships from:
    :func:`source_folder`, or through a link to a generated copy of a library
    mod, the library's copy it was made of.  The generated copy's files are
    hard links to that one's, so carried across onto it they would be copied
    onto themselves; and what is the Studio's in it -- iModYourAnno's files,
    its manifest -- is Play's to write again, not part of the mod."""
    from . import generated

    folder = source_folder(loadout, origin)
    if folder is None:
        return None
    made_of = generated.source_of(folder)
    return made_of if made_of is not None and made_of.is_dir() else folder


def is_mods_folder(path) -> bool:
    """Whether ``path`` is a folder a game loads mods from: named ``mods``,
    as every one of them is."""
    return Path(path).name.lower() == "mods"


def destination(parent, folder_name: str, library_dir=None) -> Tuple[Path, Optional[Path]]:
    """``(where to write, where to link it)`` for one mod folder under
    ``parent``.  Into ``library_dir`` and linked into ``parent``, given a
    library and a mods folder -- unless a real folder of the user's is already
    at that name, which is written over."""
    parent = Path(parent)
    if library_dir is None or not is_mods_folder(parent):
        return parent, None
    at = parent / folder_name
    if at.exists() and not library.is_link(at):
        return parent, None
    return Path(library_dir), parent


def folders_written_over(loadout, origins, parent, folder_of=None) -> List[str]:
    """The folders under ``parent`` each of ``origins`` writes to, real ones
    only.  A link into the library is the Studio's own build, and the
    loadout's history is its record.  ``folder_of`` as for :func:`write`."""
    parent = Path(parent)
    folder_of = folder_of or generator.folder_for
    found: List[str] = []
    for origin in origins:
        name = folder_of(loadout, origin)
        at = parent / name
        if at.is_dir() and not library.is_link(at) and name not in found:
            found.append(name)
    return found


def own_files(files: Dict[str, str]) -> Dict[str, str]:
    """``files`` (:attr:`generator.Written.files`) less each whose path
    climbs out of the mod: it names nothing of the mod's own, and is not
    written.  What :func:`write` hands the folder, and :func:`writes` names."""
    kept = {}
    for named, text in files.items():
        path = PureWindowsPath(named)
        tidied = ntpath.normpath(str(path))
        if path.anchor or tidied == ".." or tidied.startswith("..\\"):
            continue
        kept[named] = text
    return kept


@dataclass
class Writes:
    """The files an export of one mod writes for itself, relative to its
    folder: its patch, the files of ModOps the patch includes, its modinfo,
    its texts files and its Lua (:attr:`all`); and apart from them what it
    copies across from the folder it was read out of (:attr:`carried`),
    which is the mod's own, and still where it came from -- but goes over
    whatever is at that name where the mod is written."""

    patch: Path
    included: List[Path]
    modinfo: Path
    texts: List[Path]
    scripts: List[Path]
    carried: List[Path] = field(default_factory=list)

    @property
    def all(self) -> List[Path]:
        return [self.patch, *self.included, self.modinfo, *self.texts, *self.scripts]

    @property
    def everything(self) -> List[Path]:
        """Every file the export puts in the folder, each once."""
        unique = {path.as_posix().lower(): path for path in [*self.carried, *self.all]}
        return list(unique.values())


def writes(loadout, origin: str, root=None) -> Writes:
    """What an export of ``origin`` writes (:class:`Writes`).  ``root`` is
    the folder it goes into, where a texts file holding only a block of the
    Studio's from before is written as well, and where what the mod ships is
    copied unless that is the folder it came from; without it, only the
    texts files the mod names assets in, and nothing carried."""
    from . import scripts

    exported = generator.for_mod(loadout, origin)
    # Each file an <Include> line of the mod names, as write() hands them to
    # the folder.  Read off the loadout, this named files the export does not
    # write, such as one whose path climbs out of the mod (A11-14).
    included = [Path(inside) for inside in own_files(as_files(exported).files)]
    # The texts files: an export writes the names of the assets the mod adds
    # into every language's, and takes a block of its own out of them where
    # the mod no longer names any -- which only the folder can say.
    if root is not None:
        named = texts.files_written(exported, Path(root))
    elif texts.names_of(exported):
        named = texts.files_written(exported, Path())
    else:
        named = []
    # The Lua the export copies in, for a mod the Studio writes whole; a
    # loaded mod's comes across with what it ships, below.
    block = scripts.declared(
        {"Scripts": scripts.block_of(exported, "")},
        exported.own.scripts_folder or None,
    ) if exported.own.written_whole else None
    lua = [Path(module.path) for module in block.modules] if block is not None else []
    # What write_mod_folder copies across: written over in a folder that is
    # not the one it came from, and left out of the backup that undoes the
    # export, which then kept the copies and lost what was there (A11-14).
    carried: List[Path] = []
    source = carry_folder(loadout, origin)
    if root is not None and source is not None:
        leave = generator.kept_by_mods_inside(loadout, origin)
        carried = [
            inside for inside in generator.carried_files(source, loadout.game)
            if inside.as_posix().lower() not in leave
            and not _same_file(source / inside, Path(root) / inside)
        ]
    return Writes(games.get(loadout.game).asset_relpath, included, Path("modinfo.json"), named, lua,
                  carried)


def _same_file(one: Path, other: Path) -> bool:
    """Whether copying ``one`` to ``other`` would copy a file onto itself,
    which write_mod_folder does not do."""
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return False


def back_up(loadout, origins, parent, into=None, label: str = "", folder_of=None):
    """Copy what writing ``origins`` under ``parent`` is about to write over,
    before it does: a :class:`core.backup.Kept`, or None when nothing would be
    written over or the copy could not be made.

    Only where a mod is already there -- writing into an empty folder
    overwrites nothing.  What goes into the copy is exactly what the Studio is
    able to overwrite, which in a real mods folder is a few megabytes out of a
    few gigabytes: every file :func:`writes` names, included files of ModOps
    as well -- a copy of the patch alone left ``parts/extra.xml``, which the
    same export rewrote, with nothing to put back (H2-11) -- and what it
    copies across from the folder the mod was read out of (A11-14).  See
    :mod:`core.backup`.  ``folder_of`` as for :func:`write`.
    """
    from . import backup

    folder_of = folder_of or generator.folder_for
    mods = folders_written_over(loadout, origins, parent, folder_of)
    if not mods:
        return None
    by_folder = {folder_of(loadout, origin): origin for origin in origins}
    try:
        return backup.take(
            Path(parent),
            mods,
            into=into,
            label=label,
            game=loadout.game,
            # What the write is about to put there: copied where it is there
            # already, and where it is not, recorded so that undoing it takes
            # back a file that was not there before rather than only
            # restoring ones that were.
            writing={
                name: writes(loadout, by_folder[name], Path(parent) / name).everything
                for name in mods if name in by_folder
            },
        )
    except OSError:
        return None


def write(loadout, origins, parent, library_dir=None, zipped: bool = False, views=None,
          folder_of=None, link: bool = True) -> Built:
    """Write each of ``origins`` as its own mod folder under ``parent``.

    ``library_dir``: the game's folder in the Studio's library, where a mod
    written into a mods folder goes (see :func:`destination`).  No backup is
    taken here -- the caller takes one first, with :func:`back_up`, where it
    wants one -- and no question is asked.

    ``zipped``: each as a zip of that folder in place of the folder, which is
    what :attr:`Built.written` then names.  A zip is a thing to hand on, so
    it is never the library's.

    ``views``: a mod, by origin, as it is to be written in place of
    :func:`generator.for_mod`'s -- a patch exported to share, with your
    edits to its mod in it (:func:`core.sharing.view`).

    ``folder_of``: what each folder is called, ``(loadout, origin)`` to a
    name -- :func:`generator.folder_for`, Play's, unless an export gives
    :func:`generator.export_folder_for`.

    ``link`` False: into the library and no link made, given a library and
    a mods folder -- the mod built for a loadout the game does not load
    (:mod:`.adding`).  A real folder of the user's at its name in the mods
    folder is then not written over: the mod is not built.
    """
    import contextlib
    import tempfile

    from . import publish

    done = Built()
    for origin in origins:
        exported = (views or {}).get(origin)
        if exported is None:
            exported = generator.for_mod(loadout, origin)
        whole = as_files(exported)
        xml = whole.text
        folder_name = (folder_of or generator.folder_for)(loadout, origin)
        carry_from = carry_folder(loadout, origin)
        # Numbered as it goes: an export, or Play's build -- testing is
        # exactly when two copies of the same version need telling apart.
        # Only kept once the folder or the zip is on disk, so one that fails
        # part way does not use up a number.
        numbered = builds.plan(loadout, origin, exported, all_of(whole), carry_from)
        try:
            with contextlib.ExitStack() as scratch:
                if zipped:
                    # The folder is written aside and only its zip is kept.
                    # A mod inside another is zipped as itself.
                    write_to = Path(scratch.enter_context(
                        tempfile.TemporaryDirectory(prefix="ams-zip-")
                    ))
                    link_into, folder_name = None, folder_name.split("/")[-1]
                else:
                    write_to, link_into = destination(parent, folder_name, library_dir)
                    if not link and link_into is None:
                        raise OSError(tr(
                            "%s in the mods folder is a folder of your own, not the "
                            "Studio's build, so the mod was not built", folder_name,
                        ))
                    if link_into is not None:
                        # One writer per library folder: the mod's project.
                        placed, moving, theirs = builtby.place(
                            loadout, origin, folder_name, write_to, reserve=True,
                        )
                        if theirs:
                            raise builtby.Stopped(builtby.stopped_why(
                                exported.own.name or origin, placed, theirs,
                            ))
                        if placed != folder_name:
                            folder_name = placed
                            write_to, link_into = destination(parent, folder_name, library_dir)
                        if moving:
                            library.move_copy(write_to, moving, folder_name, [link_into])
                    Path(write_to).mkdir(parents=True, exist_ok=True)
                root = generator.write_mod_folder(
                    exported, write_to, xml, folder_name=folder_name, carry_from=carry_from,
                    files=own_files(whole.files), leave=generator.kept_by_mods_inside(loadout, origin),
                )
                if zipped:
                    root = publish.zip_mod(root, parent)
                elif link_into is not None:
                    # The project's to write again, and the library says so,
                    # with what it wrote: whether the copy is behind the mod.
                    library.record_built(
                        write_to, root.name, project=builtby.project_of(loadout, origin),
                        mod_id=modinfo.written_mod_id(exported),
                        build=numbered.number if numbered is not None else 0,
                        digest=numbered.digest if numbered is not None else builds.content_digest(
                            exported, all_of(whole), carry_from,
                        ),
                    )
                    if link:
                        root = library.link(root, link_into, root.name).link
                    # Linked to what was just written: a generated copy made
                    # of what was there before is not the mod any more.
                    library.generate(write_to, root.name, {})
            done.written.append(root)
            done.origins.append(origin)
            settled = _project_settled(loadout, origin)
            moved = builds.record(loadout, numbered)
            done.numbered = moved or done.numbered
            if moved:
                _build_into_project(loadout, origin, settled)
        except OSError as exc:
            done.failed.append((exported.own.name or origin or "", str(exc)))
    return done


def _project_settled(loadout, origin: str) -> bool:
    """Whether the loadout holds nothing of ``origin``'s project that its
    file does not: what it was read or last saved as."""
    from . import projects as project_files

    ref = loadout.projects.get(origin)
    if ref is None or ref.held or not ref.digest:
        return False
    part = project_files.split(loadout.to_dict(), [origin], loadout.kept_aside).get(origin, {})
    return project_files.digest(part) == ref.digest


def _build_into_project(loadout, origin: str, settled: bool) -> None:
    """The build just recorded of ``origin``, into its project's file at
    once, and at the revision it is at.

    A build number is a fact about what was written, not work on the mod:
    no loadout has anything to read again for it.  Kept only in the loadout
    until its next save, a build Update made without that save was lost,
    and the next build took the same number again (R3-43, hands-on run 3);
    saved as a new revision, every other loadout that has the mod would
    read it again for nothing.  Not where another loadout saved the project
    since this one read it: that file is not this loadout's to write.
    ``settled``, from :func:`_project_settled` before the build, is whether
    the loadout's reading still matches the file then, so that the next
    save writes nothing either."""
    from . import projects as project_files
    from . import saving

    ref = loadout.projects.get(origin)
    held = loadout.builds.get(origin)
    if ref is None or ref.held or not isinstance(held, dict):
        return
    where = project_files.where(ref.path)
    try:
        project = project_files.Project.read(where)
    except project_files.ProjectError:
        return
    if project.revision != ref.read_at:
        return
    project.part["build"] = dict(held)
    try:
        saving.write_all({where: project.as_bytes()})
    except OSError:
        return
    if settled:
        # As :func:`.readback.settle` takes it: what the loadout holds of it
        # now is what the file holds.
        part = project_files.split(loadout.to_dict(), [origin], loadout.kept_aside).get(origin, {})
        ref.digest = project_files.digest(part)


def behind(loadout, origin: str, library_dir) -> Optional[bool]:
    """Whether the library's build of ``origin`` is not the mod as it is
    now: writing it again would write something else, comments aside
    (:func:`.builds.content_digest`).  None where that cannot be said: no
    build of it in the library, or one made before builds recorded what
    they wrote.  What *Update* on the Export tab is for."""
    held = library.built_digest(Path(library_dir), generator.folder_for(loadout, origin))
    if not held:
        return None
    exported: Any = generator.for_mod(loadout, origin)
    whole = as_files(exported)
    # Asked as a build asks it: with no build number in it yet.
    exported.build = 0
    return builds.content_digest(exported, all_of(whole), carry_folder(loadout, origin)) != held


# -- which mods there are to write ---------------------------------------------------
def writable(loadout) -> List[str]:
    """Every loaded mod that could be written back out right now.

    A mod you have switched off is left out rather than written thinner: its
    folder carries the "off" mark, and writing it back under its plain name
    would switch it on again.  A patch whose mod is off goes the same way --
    its ops were measured against what that mod leaves behind, so against a
    game without it they do not do nothing, they do something nobody chose.
    """
    from . import patching, zipped

    off = loadout.switched_off()
    inert = {
        layer.origin
        for layer in loadout.layers
        if patching.is_inert(loadout, layer)
        # Read out of a zip: a folder written beside it would be a second
        # copy the game loads, so it is never written back.
        or (layer.folder and zipped.is_mirrored(layer.folder))
    }
    # Every loaded mod, not only the ones with changes: a mod the Studio could
    # read none of has no changes and is all carried ops. And your own, which
    # is no layer: without a change it is not among the origins, and a mod of
    # yours that is only its Lua is still a mod.
    candidates = loadout.origins(loaded=True)
    if "" not in candidates and loadout.has_own_mod():
        candidates = candidates + [""]
    return [
        origin
        for origin in candidates
        if origin not in off
        and origin not in inert
        and generator.writes_anything(generator.for_mod(loadout, origin))
    ]


def mine(loadout) -> List[str]:
    """The mods here that are *yours*: the ones you actually wrote into.

    Which is the honest default for a whole mods folder, and what Play
    builds.  Somebody else's mod, untouched, has nothing to gain from being
    rewritten and something to lose: what goes back is the Studio's reading,
    and everything it could not read is not in that reading.  Your own mod and
    your patches exist nowhere else, so those are exactly what wants writing.
    """
    forks = {layer.origin for layer in loadout.layers if layer.fork_of}
    # An op typed into a mod is written into it as surely as a change: a
    # patch whose only work of its own was a typed group was never built,
    # and its ops never reached the game (R3-24, hands-on run 3).
    # Of a mod of yours only: an op typed over somebody else's goes into a
    # generated copy of theirs, which is never built (core.play).
    typed = {op.origin for op in loadout.ops if op.authored and builds.is_numbered(loadout, op.origin)}
    # A fork is in no mods folder until it is built: its files are a copy
    # beside the library (core.forking).  Nor is a mod of yours given a new
    # ModID, which is another mod with a folder of its own (core.builtby).
    return [origin for origin in writable(loadout)
            if is_own_mod(origin) or origin in forks or origin in typed
            or loadout.authored_of(origin)
            or generator.given_a_new_mod_id(loadout, origin)]
