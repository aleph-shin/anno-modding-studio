"""Where what the Studio installs lives: a library of its own, junctioned in.

A mod the Studio fetched from mod.io, a collection's forty, an export of the
loadout's own mod -- none of them go into the game's mods folder as folders.
Each is a real folder in the library, and the mods folder holds a **directory
junction** to it under the name the mod folder would have had.  The game
walks the junction (both loaders do, and both log the junction's own path,
which is why nothing else here had to change), load order is unaffected
because the name is the name, and a mod that is off is simply not linked.
What the folder shows is what loads.

Two kinds of entry.  A *copy* is what the Studio put in the library itself.
A *reference* is a folder the user owns -- a mod author's checkout, edited
elsewhere -- that the Studio manages in place: the junction points straight
at it, and the library records only that it does, in ``references.json``.
Copying an author's working copy would be exactly wrong, since they would
edit the checkout and the game would load the stale copy.

Two rules the rest of the Studio keeps.  Reading a folder in never moves
anything into the library: adoption is an offer made afterwards, with the
count in it (see :mod:`core.truth`).  And the library must be leavable:
:func:`put_back` turns every copy's junction into the real folder again and
drops every reference's, so the Studio can be uninstalled without the game
losing its mods.

Not under Documents.  On Windows 11 Home that folder is routinely synced by
OneDrive, and Files On Demand can dehydrate a mod behind a junction into a
placeholder the game cannot read; mod.io's own client uses ``Users\\Public``
for the same reason.  The default is under the local app data, and
``ANNO_STUDIO_LIBRARY`` names another -- the tests, and a user who wants it
on the game's drive.  Junctions cross volumes.  Somebody who keeps all the
Studio writes in one folder (:func:`core.places.one_folder`) has it there,
and has been told what a synced folder does to it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import disk, games, generated, imya, modfolder, modzip, places, records, wine
from .i18n import tr, trn

ENV_LIBRARY = "ANNO_STUDIO_LIBRARY"
# The format of the library's records below, each stamped with it (see
# :mod:`core.records`).  Bump it in the change that gives one of them a
# meaning an earlier build would misread: that build then writes none of
# them back.  The registry is what a lost library's copies could be had
# again from, so it is never written from a misreading.
RECORD_FORMAT = 1
REFERENCES = "references.json"
# What each copy was installed as: folder name -> how it came to be here
# (``source``, one of the kinds below), and what is known of it for that:
# mod.io id, modfile id, version and mod.io name for one mod.io named; the
# zip's name and hash for one unpacked from a zip on disk.  The Studio put it
# there, so it knows exactly what it is; without this the plan could only
# match by name, and half a collection's names differ from their modinfos'.
# It is also all there is to say how a copy could be had again, were the
# library lost: one with a mod.io id from mod.io, a built one from its
# project, and a zip nobody matched or a folder moved in from nowhere --
# see :func:`only_copies`.
REGISTRY = "installed.json"
# How a copy came to be in the library.
MODIO = "modio"      # downloaded from mod.io by the Studio
CACHE = "cache"      # copied out of the game client's mod.io cache
ZIP = "zip"          # unpacked from a zip on disk
ADOPTED = "adopted"  # a folder of the mods folder's, moved in
BUILT = "built"      # written by the Studio out of a loadout
# What a built copy's entry says of the project it was built from: the
# project's path as a loadout writes it, and the ModID and build number the
# copy was built as -- one writer per library folder, the mod's project --
# and what it wrote, comments aside (core.builds.content_digest), which says
# whether the copy is still the mod as it is now (core.build.behind).
# Kept here and never in the modinfo, which goes with the mod to mod.io and
# to other people: a path on this PC has no business there.  ``was`` is the
# names the copy was built under before a rename moved it, oldest first.
# See docs/proposals/projects-and-loadouts-proposal.md, section 7.
PROJECT = "project"
MOD_ID = "mod_id"
BUILD = "build"
DIGEST = "digest"
BUILT_AS = (PROJECT, MOD_ID, BUILD, DIGEST)
WAS = "was"
# What a mod.io id in the registry is known by, where the Studio did not
# install the copy as that mod: ``known_by`` is how the id was matched (the
# kinds of :mod:`core.updates`), and absent for a copy mod.io named.
KNOWN_BY = "known_by"
# What only one way of arriving says, and another way of arriving unsays.
_OF_A_ZIP = ("zip", "zip_md5")
# Where a copy there is no second source of is kept, as a zip: the zip it was
# unpacked from, kept as it came, or the folder zipped on request.  See
# :func:`keep_zip` and :func:`back_up`.
BACKUP = "backup"
BACKUPS = "library"
# Every junction the library has made, by the link's name: what it points at,
# which kind, which mods folder, and whether it is there now.  A library
# mod's switch is its link -- off is no link -- and this is what brings the
# link back.
LINKS = "links.json"
# Which loadout the Studio last made live: see :func:`live`.
LIVE = "live.json"


# -- where ----------------------------------------------------------------------
def root(chosen: str = "") -> Path:
    """The library's root: the environment's, else the one chosen in the
    settings, else in the folder the Studio keeps for itself."""
    named = os.environ.get(ENV_LIBRARY, "").strip()
    if named:
        return Path(named)
    if chosen and chosen.strip():
        return Path(chosen.strip())
    return places.local_home() / "library"


def game_dir(game: str, chosen: str = "") -> Path:
    return root(chosen) / games.get(game).key


# -- links ------------------------------------------------------------------------
# What the Studio makes in a mods folder: a junction on Windows, which needs
# no rights a user may lack, and a symbolic link where there are no
# junctions.  Either one, made by whoever, is a link: the convention is "a
# mod in the mods folder that is a link points at its source", whatever kind
# of link (docs/proposals/patches-proposal.md, section 7).
_JUNCTIONS = os.name == "nt"


def is_link(path) -> bool:
    """Whether ``path`` is a link to a folder: a junction or a symbolic link."""
    return is_junction(path) or os.path.islink(path)


def make_link(link: Path, target: Path) -> None:
    """Link ``link`` to the folder ``target``: a junction where there are
    junctions, else a symbolic link, by ``target``'s whole path."""
    if _JUNCTIONS:
        make_junction(link, target)
    else:
        os.symlink(str(Path(target).absolute()), str(link), target_is_directory=True)


def remove_link(link: Path) -> None:
    """The link alone; what it points at is untouched."""
    if _JUNCTIONS or is_junction(link):
        # A folder's symbolic link on Windows goes as a junction does.
        remove_junction(link)
    else:
        os.unlink(link)


def is_junction(path) -> bool:
    """Whether ``path`` is a junction.  Asked of the reparse tag, as Python
    3.12's ``Path.is_junction`` does: that method is not there before it."""
    try:
        found = os.lstat(path)
    except (OSError, ValueError):
        return False
    # Off Windows there is no such tag, and no junction.
    tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    if tag is None or getattr(found, "st_reparse_tag", 0) != tag:
        return False
    # Wine gives every Linux mount point the tag too, a folder that points
    # nowhere: only one whose target can be read is a junction.
    try:
        os.readlink(str(path))
    except (OSError, ValueError):
        return False
    return True


def target_of(path) -> Optional[Path]:
    """Where a link points, without the ``\\\\?\\`` Windows puts in front, and
    for a symbolic link written relative, from where it stands."""
    if not is_link(path):
        return None
    try:
        raw = os.readlink(str(path))
    except OSError:
        return None
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    pointed = Path(raw)
    if not pointed.is_absolute():
        pointed = Path(os.path.normpath(Path(path).parent / pointed))
    return pointed


def make_junction(link: Path, target: Path) -> None:
    import _winapi  # Windows only, as junctions are

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as error:
        # A Wine before 10.19 makes none, and its own words say only that
        # the request is not supported.
        note = wine.junctions_note()
        if note:
            raise OSError(error.errno, note, str(link)) from error
        raise


def remove_junction(link: Path) -> None:
    """The junction alone; what it points at is untouched."""
    Path(link).rmdir()


def holds(path, library: Path) -> bool:
    """Whether ``path`` -- through whatever junctions -- is inside the
    library, or is a generated copy of one of its mods: either way the
    Studio's own, and so the Studio's to rewrite."""
    return disk.inside(path, library) or disk.inside(path, generated.root_for(library))


def source_of(path, library: Path) -> Optional[Path]:
    """The folder a link stands for: where it points, or for a link to a
    generated copy of a library mod, the library copy it was made from.
    None for anything that is not a junction.  Ask this, not
    :func:`target_of`, wherever the question is which mod a link is."""
    target = target_of(path)
    if target is not None and generated.is_generated(target, library):
        return generated.source_of(target) or target
    return target


# -- what the library knows -----------------------------------------------------------
COPY = "copy"
REFERENCE = "reference"


def kind_of(installed_folder, library: Path) -> str:
    """What an installed mod folder is to the library: a junction to a copy in
    it, a junction to a folder it references, or "" for a folder that is
    simply there."""
    target = source_of(installed_folder, library)
    if target is None:
        return ""
    if disk.inside(target, library):
        return COPY
    return REFERENCE if str(target) in references(library).values() else ""


# What each of the records keyed by name holds per entry: a dict of facts,
# or for a reference the folder it stands for.
_ENTRY = {REGISTRY: dict, LINKS: dict, REFERENCES: str}


def _read(library: Path, name: str) -> records.Record:
    return records.read(Path(library) / name, RECORD_FORMAT, keyed=name != LIVE,
                        entry=_ENTRY.get(name))


def _write(library: Path, name: str, held: dict) -> None:
    """One of the library's records written back, unless the one there may
    not be: see :func:`core.records.write`."""
    records.write(Path(library) / name, held, RECORD_FORMAT, keyed=name != LIVE,
                  entry=_ENTRY.get(name))


def _to_change(library: Path, name: str) -> Dict[str, dict]:
    """A record's entries, read to be changed and written back: raises
    :class:`core.records.RecordError` before anything is changed, where the
    record could not be written back after."""
    found = _read(library, name)
    if found.problem:
        raise records.RecordError(found.problem)
    return {key: dict(value) for key, value in found.data.items()}


def check_records(library: Path) -> None:
    """Raise :class:`core.records.RecordError` where one of the library's
    records may not be written back.

    Asked before anything is moved into the library or linked from it.  The
    move would otherwise be done and its record refused after it, leaving a
    copy nothing says the origin of.
    """
    for name in (REGISTRY, LINKS, REFERENCES):
        found = _read(library, name)
        if found.problem:
            raise records.RecordError(found.problem)


def references(library: Path) -> Dict[str, str]:
    """Name -> the folder it stands for, for every reference recorded."""
    return dict(_read(library, REFERENCES).data)


def _write_references(library: Path, held: Dict[str, str]) -> None:
    _write(library, REFERENCES, held)


def registry(library: Path) -> Dict[str, dict]:
    """Folder name -> what the Studio installed there, for every copy it did."""
    return {key: dict(value) for key, value in _read(library, REGISTRY).data.items()}


def record(library: Path, folder_name: str, forget=(), **facts) -> None:
    """Remember what a copy was installed as; zero and empty facts are not
    kept.  ``forget`` names facts that stopped being true with this."""
    held = _to_change(library, REGISTRY)
    entry = held.get(folder_name, {})
    for name in forget:
        entry.pop(name, None)
    entry.update({k: v for k, v in facts.items() if v})
    if entry:
        held[folder_name] = entry
    _write(library, REGISTRY, held)


def copy_name(library: Path, folder) -> str:
    """The name the registry knows a copy by, for the place it is installed
    at -- its link in the mods folder, there or taken away -- or "" for
    anything that is not a copy in the library."""
    library = Path(library)
    target = source_of(folder, library)
    if target is None:
        entry = links(library).get(Path(folder).name) or {}
        if entry.get("kind", COPY) != COPY or not entry.get("target"):
            return ""
        target = Path(entry["target"])
    return target.name if disk.inside(target, library) else ""


def learn(library: Path, folder_name: str, modio_id: int, how: str) -> bool:
    """Remember which mod.io mod a copy turned out to be, and how that was
    decided, for a copy the Studio did not install as it.  Whether anything
    was written.

    What the Studio installed by id is never overwritten: that is a fact,
    and this is a match.  A match is replaced by a later one.
    """
    if not folder_name or not modio_id or not (Path(library) / folder_name).is_dir():
        return False
    entry = registry(library).get(folder_name, {})
    installed_as = int(entry.get("modio_id") or 0)
    if installed_as and not entry.get(KNOWN_BY):
        return False
    if installed_as == int(modio_id) and entry.get(KNOWN_BY) == how:
        return False
    record(library, folder_name, modio_id=int(modio_id), **{KNOWN_BY: how})
    return True


# -- which project built a copy -----------------------------------------------------
def record_built(library: Path, folder_name: str, project: str = "", mod_id: str = "",
                 build: int = 0, digest: str = "") -> None:
    """Remember that the Studio built the copy ``folder_name``, and from
    which project: the project's path as a loadout writes it
    (:func:`core.projects.as_written`), the ModID and the build number it
    was built as, and the digest of what it wrote.  What an earlier build
    said of them goes; the rest of what is known of the copy stays."""
    record(library, folder_name, forget=BUILT_AS, source=BUILT,
           **{PROJECT: project, MOD_ID: mod_id, BUILD: build, DIGEST: digest})


def built_digest(library: Path, folder_name: str) -> str:
    """The digest of what the Studio last built into the copy
    ``folder_name`` (:func:`record_built`), or "" for a copy it did not
    build, or built before builds recorded it."""
    entry = registry(library).get(folder_name) or {}
    return str(entry.get(DIGEST) or "") if entry.get("source") == BUILT else ""


def built_by(library: Path, folder_name: str) -> str:
    """The project that built the copy ``folder_name``, as a loadout names
    it, or "" for a copy no project is recorded as having built."""
    entry = registry(library).get(folder_name) or {}
    return str(entry.get(PROJECT) or "") if entry.get("source") == BUILT else ""


def built_from(library: Path, project: str) -> List[str]:
    """The copies the library holds that ``project`` built, by name."""
    from . import projects

    if not project:
        return []
    key = projects.key_of(project)
    return [
        name for name, entry in registry(library).items()
        if entry.get("source") == BUILT and entry.get(PROJECT)
        and projects.key_of(str(entry[PROJECT])) == key and (Path(library) / name).is_dir()
    ]


def follow_project(library: Path, before: str, after: str) -> None:
    """A project saved under another name takes the copies it built with
    it, as :func:`follow_live` follows a loadout that moved."""
    from . import projects

    if not before or not after:
        return
    key = projects.key_of(before)
    held = _to_change(library, REGISTRY)
    moved = False
    for entry in held.values():
        if entry.get("source") == BUILT and entry.get(PROJECT) and projects.key_of(str(entry[PROJECT])) == key:
            entry[PROJECT] = after
            moved = True
    if moved:
        _write(library, REGISTRY, held)


def move_copy(library: Path, before: str, after: str, mods_folders=()) -> Path:
    """Move the library's copy ``before`` to ``after``, with its record and
    every link to it, which takes the new name where it had the old one:
    a mod of yours renamed, whose folder goes with the name rather than a
    second one being built beside it.  Where the copy is now.

    The links are the ones the library recorded, and the one called
    ``before`` in each of ``mods_folders``, which is how a build links what
    it wrote.

    Raises OSError, with nothing moved, where there is no copy called
    ``before``, or ``after`` is taken or cannot be a folder's name.
    """
    library = Path(library)
    old, new = library / before, library / after
    if not old.is_dir() or not disk.directly_in(new, library):
        raise OSError(tr("%s is not a copy in the library, so it was not moved", before))
    if new.exists() or is_link(new):
        raise OSError(tr("%(after)s is in the library already, so %(before)s was not moved there",
                         after=after, before=before))
    # Before anything moves: a copy moved with its record refused would be
    # a copy nothing says the origin of.
    check_records(library)
    held = links_to_change(library)
    # Each link to it: where it is, its name in the record or None, and
    # whether it is there to be made again.
    moving: List[Tuple[Path, Optional[str], bool]] = [
        (Path(entry.get("mods_folder") or "") / name, name, bool(entry.get("linked", True)))
        for name, entry in held.items()
        if entry.get("kind", COPY) == COPY and entry.get("target")
        and disk.same_file(entry["target"], old)
    ]
    for folder in mods_folders:
        at = Path(folder) / before
        if is_link(at) and disk.same_file(source_of(at, library), old) and not any(
                disk.same_file(at.parent, there.parent) and at.name == there.name
                for there, _name, _linked in moving):
            moving.append((at, None, True))
    for at, _name, _linked in moving:
        if is_link(at):
            remove_link(at)
    try:
        # What was generated of the old copy was of it under its old name.
        generated.remove(generated.folder_for(library, before))
        old.rename(new)
    except OSError:
        for at, _name, linked in moving:
            if linked and not at.exists():
                with contextlib.suppress(OSError):
                    make_link(at, old)
        raise
    for at, name, linked in moving:
        called = after if at.name == before else at.name
        if linked:
            link(new, at.parent, called)
        if name is not None:
            entry = held.pop(name)
            entry["target"] = str(new)
            entry.pop("generated", None)
            held[called] = entry
    write_links(library, held)
    kept = _to_change(library, REGISTRY)
    entry = kept.pop(before, {})
    entry[WAS] = [*(name for name in entry.get(WAS) or () if name != before), before]
    kept[after] = entry
    _write(library, REGISTRY, kept)
    return new


def links(library: Path) -> Dict[str, dict]:
    """Link name -> ``{target, kind, mods_folder, linked}`` for every junction
    the library has made and not put back."""
    return {key: dict(value) for key, value in _read(library, LINKS).data.items()}


def links_to_change(library: Path) -> Dict[str, dict]:
    """:func:`links`, read to be changed and handed to :func:`write_links`:
    raises :class:`core.records.RecordError` first where they could not be
    written back."""
    return _to_change(library, LINKS)


def write_links(library: Path, held: Dict[str, dict]) -> None:
    _write(library, LINKS, held)


def note_link(library: Path, linked: "Linked", kind: str) -> None:
    held = links_to_change(library)
    held[linked.link.name] = {
        "target": str(linked.target), "kind": kind,
        "mods_folder": str(linked.link.parent), "linked": True,
    }
    write_links(library, held)


def _link_record(library: Path, name: str):
    """``(every link the library made, the one called name, the folder it
    stands for)``, or None when the library never made one by that name or
    its folder is gone.  Raises :class:`core.records.RecordError` where the
    record could not be written back once the link was changed."""
    held = links_to_change(Path(library))
    entry = held.get(name)
    if entry is None or not entry.get("target") or not Path(entry["target"]).is_dir():
        return None
    return held, entry, Path(entry["target"])


def point(library: Path, name: str, folder: Optional[Path] = None) -> Optional[Path]:
    """Point the library's link ``name`` at ``folder``, a generated copy of
    its mod, or with None back at the library's own copy.  Where the link is
    -- switched off, it stays off and is put back pointing there -- or None
    when the library never made one by that name or its copy is gone."""
    library = Path(library)
    found = _link_record(library, name)
    if found is None:
        return None
    held, entry, target = found
    pointed = Path(folder) if folder is not None else target
    at = Path(entry.get("mods_folder") or "") / name
    if is_link(at) and not disk.same_file(target_of(at), pointed):
        remove_link(at)
        make_link(at, pointed)
    if folder is not None:
        entry["generated"] = str(folder)
    else:
        entry.pop("generated", None)
    write_links(library, held)
    return at


def generate(library: Path, name: str, written: Dict[str, bytes], edits=()) -> Optional[generated.Made]:
    """Point the library's link ``name`` at a generated copy of its mod with
    ``written`` over it -- each file by its path from the top of the mod --
    or, with nothing written, back at the library's own copy, the generated
    one deleted.  What building did, or None when there was nothing to build
    or the link is not the library's.  A copy that does not check out is not
    pointed at: see :attr:`core.generated.Made.wrong`."""
    library = Path(library)
    entry = links(library).get(name)
    if entry is None or entry.get("kind", COPY) != COPY or not entry.get("target"):
        return None
    source = Path(entry["target"])
    into = generated.folder_for(library, source.name)
    if not written:
        point(library, name, None)
        generated.remove(into)
        return None
    made = generated.build(
        source, into, written, edits=edits,
        meta={key: value for key, value in modfolder.metadata_of(source).items()
              if key in ("mod_id", "mod_name", "version") and value},
    )
    if not made.wrong:
        point(library, name, into)
    return made


def unlink(library: Path, link: Path) -> bool:
    """Switch a library mod off: remove its junction, remembering it.  False
    when ``link`` is not a junction the library made."""
    library, link = Path(library), Path(link)
    held = links_to_change(library)
    entry = held.get(link.name)
    if entry is None or not is_link(link):
        return False
    remove_link(link)
    entry["linked"] = False
    entry["mods_folder"] = str(link.parent)
    write_links(library, held)
    return True


def relink(library: Path, name: str) -> Optional[Path]:
    """Switch a library mod back on: the junction where it was.  None when
    the library never made one by that name, or its target is gone."""
    library = Path(library)
    found = _link_record(library, name)
    if found is None:
        return None
    held, entry, target = found
    # Pointing at the generated copy it pointed at when it was taken away,
    # where there is one: that is the mod as it was switched off.
    made = Path(entry["generated"]) if entry.get("generated") else None
    if made is not None and made.is_dir():
        target = made
    at = Path(entry.get("mods_folder") or "") / name
    if not is_link(at):
        if at.exists():
            return None  # something of the user's has taken the name
        make_link(at, target)
    entry["linked"] = True
    write_links(library, held)
    return at


def unlinked(library: Path) -> Dict[str, dict]:
    """The links the library has taken away and can put back."""
    return {name: entry for name, entry in links(library).items() if not entry.get("linked", True)}


@dataclass
class Held:
    """One mod the library holds or stands for: the name its link goes by,
    where that link is or would be, the folder it points at, which kind, and
    whether the link is there now."""

    name: str
    link: Path
    target: Path
    kind: str = COPY
    linked: bool = True
    # What its modinfo says of it: only :func:`catalogue` reads that.
    mod_name: str = ""
    mod_id: str = ""
    version: str = ""
    # And what the registry says of a copy: how it arrived, which mod.io mod
    # it is, and the zip it was unpacked from.
    source: str = ""
    modio_id: int = 0
    zip: str = ""
    # The zip it is backed up as, where it is and is still there.
    backup: str = ""


def held(library: Path) -> List[Held]:
    """Every mod the library holds or stands for, switched on or off, by
    name.  One whose folder is gone is left out: there is nothing to read."""
    found = []
    for name, entry in links(library).items():
        target = Path(entry.get("target") or "")
        if not entry.get("target") or not target.is_dir():
            continue
        at = Path(entry.get("mods_folder") or "") / name
        found.append(Held(
            name=name, link=at, target=target,
            kind=str(entry.get("kind") or COPY), linked=is_link(at),
        ))
    return sorted(found, key=lambda mod: modfolder.bare_name(mod.name).lower())


def catalogue(library: Path) -> List[Held]:
    """:func:`held`, with what each mod is called, its ModID and its version:
    its modinfo's, and for a copy with none the version it was installed as.
    A modinfo apiece and nothing else is read, so it is a list at once."""
    known = registry(library)
    found = held(library)
    for mod in found:
        meta = modfolder.metadata_of(mod.target)
        facts = known.get(mod.target.name, {}) if mod.kind == COPY else {}
        mod.mod_name = str(meta.get("mod_name") or "")
        mod.mod_id = str(meta.get("mod_id") or "")
        mod.version = str(meta.get("version") or "") or str(facts.get("version") or "")
        mod.source = str(facts.get("source") or "")
        mod.modio_id = int(facts.get("modio_id") or 0)
        mod.zip = str(facts.get("zip") or "")
        mod.backup = backup_of(facts)
    return found


def only_copies(library: Path) -> List[Held]:
    """The copies there is no second source of: lost with the library, they
    could not be had again.

    Not one mod.io named or has been matched to, which is downloaded again,
    and not one a loadout wrote, which is written again.  What is left is a
    zip nobody matched and a folder moved in from the mods folder.  A copy
    from before how a copy arrived was recorded is not one either: nothing
    can be said of it (see :func:`unrecorded`).  A reference is not a copy:
    its folder is the user's own and elsewhere.
    """
    return [
        mod for mod in catalogue(library)
        if mod.kind == COPY and not mod.modio_id and mod.source and mod.source != BUILT
        and not mod.backup
    ]


def unrecorded(library: Path) -> List[Held]:
    """The copies nothing is recorded about: no mod.io mod and no way of
    arriving.  They came into the library before how a copy arrived was
    written down, and whether one could be had again is not known -- it may
    be on mod.io, or be the build of a loadout.  Counted apart from
    :func:`only_copies`, which it would otherwise swamp: a library from
    before then is mostly these."""
    return [
        mod for mod in catalogue(library)
        if mod.kind == COPY and not mod.modio_id and not mod.source and not mod.backup
    ]


def backed_up(library: Path) -> List[Held]:
    """The copies that would be :func:`only_copies` or :func:`unrecorded`
    but for a zip of them in the backups: had again by dropping that zip on
    the window."""
    return [
        mod for mod in catalogue(library)
        if mod.kind == COPY and not mod.modio_id and mod.source != BUILT and mod.backup
    ]


# -- backing up what could not be had again ------------------------------------------
def backup_dir(library: Path) -> Path:
    """Where the zips of one game's library copies are kept: with the
    backups, under Documents, since they are the one part of the library
    that is not got again (see :func:`only_copies`)."""
    return places.backups_dir() / BACKUPS / Path(library).name


def backup_of(entry: dict) -> str:
    """The zip a registry entry says its copy is backed up as, or "" where
    it says none or the zip is gone."""
    found = str(entry.get(BACKUP) or "")
    return found if found and Path(found).is_file() else ""


def _free_name(folder: Path, stem: str, suffix: str = ".zip") -> Path:
    target = folder / (stem + suffix)
    counter = 2
    while target.exists():
        target = folder / ("%s (%d)%s" % (stem, counter, suffix))
        counter += 1
    return target


def keep_zip(library: Path, archive, md5: str = "") -> Path:
    """Keep a zip from disk with the backups, as it came: the file the copy
    was unpacked from, and the one thing that says which file of mod.io's
    it was (see :func:`install_zip`).  A zip of the same name and contents
    kept already is that one; the same name with other contents is kept
    beside it."""
    archive = Path(archive)
    into = backup_dir(library)
    into.mkdir(parents=True, exist_ok=True)
    md5 = md5 or md5_of(archive)
    target = into / archive.name
    counter = 2
    while target.exists() and md5_of(target) != md5:
        target = into / ("%s (%d)%s" % (archive.stem, counter, archive.suffix))
        counter += 1
    if not target.exists():
        shutil.copy2(str(archive), str(target))
    return target


def back_up(library: Path, folder_name: str, progress=None, stop=None) -> Optional[Path]:
    """Zip a library copy whole into :func:`backup_dir`, and remember it.

    The mod's own folder is the zip's top folder, which is what a zip of a
    mod is: dropped on the window, it is installed again.  Compressed at the
    fastest level, which on models and textures gets nearly what the
    default does in two thirds of the time.  Written beside its name first
    and given the name at the end, so a backup stopped part way leaves no
    zip that looks whole.  ``progress(done, total)`` is told the bytes;
    ``stop()`` true ends it between files, with nothing kept, and None is
    returned.
    """
    library = Path(library)
    source = library / folder_name
    files: List[Path] = []
    for root, _dirs, names in os.walk(source):
        files.extend(Path(root) / name for name in names)
    total = sum(path.stat().st_size for path in files)
    into = backup_dir(library)
    into.mkdir(parents=True, exist_ok=True)
    target = _free_name(into, "%s %s" % (folder_name, time.strftime("%Y-%m-%d")))
    partial = target.with_name(target.name + ".partial")
    done = 0
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zipped:
            for path in files:
                if stop is not None and stop():
                    raise InterruptedError
                zipped.write(path, str(Path(folder_name) / path.relative_to(source)))
                done += path.stat().st_size
                if progress is not None:
                    progress(done, total)
    except InterruptedError:
        partial.unlink(missing_ok=True)
        return None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    record(library, folder_name, **{BACKUP: str(target)})
    return target


def shelved(library: Path, names) -> List[Held]:
    """The library's mods with no link in the mods folder, of the ones whose
    link goes by one of ``names``, bare.  A read of the folder does not find
    them: a loadout that has them, switched off, names them to it."""
    names = {modfolder.bare_name(str(name)) for name in names}
    return [mod for mod in held(library)
            if not mod.linked and modfolder.bare_name(mod.name) in names]


def kept_at(library: Path, folder) -> Optional[Path]:
    """Where the library keeps the mod whose link is, or would be, at
    ``folder`` -- or ``folder`` inside it, for a mod bundled in one -- by
    the record of the link it made there.  None where it made none, or its
    copy is gone.

    A library mod with no link in the mods folder is read where the library
    keeps it, and its layer names where Play puts the link back, which is
    no folder until then: read by that, it could not be read again, nor its
    options asked (R3-46, hands-on run 3)."""
    folder = Path(folder)
    known = links(library)
    for top in (folder, *folder.parents):
        entry = known.get(top.name)
        if not entry or not entry.get("target"):
            continue
        if os.path.normcase(os.path.abspath(str(entry.get("mods_folder") or ""))) != \
                os.path.normcase(os.path.abspath(str(top.parent))):
            continue
        kept = Path(entry["target"]).joinpath(*folder.relative_to(top).parts)
        return kept if kept.is_dir() else None
    return None


def reading_folder(library: Optional[Path], folder) -> Optional[Path]:
    """The folder a loaded mod is read from: ``folder`` where it is there,
    else where the library keeps it (:func:`kept_at`), else None."""
    if not folder:
        return None
    if Path(folder).is_dir():
        return Path(folder)
    return kept_at(library, folder) if library is not None else None


def links_into(library: Path) -> Optional[Path]:
    """The mods folder the library's links are in: the one most of them name,
    of those that are there.  None for a library that has linked nothing."""
    counted: Dict[str, int] = {}
    for mod in held(library):
        if mod.link.parent.is_dir():
            counted[str(mod.link.parent)] = counted.get(str(mod.link.parent), 0) + 1
    return Path(max(counted, key=lambda folder: counted[folder])) if counted else None


def forget(library: Path, folder_name: str) -> None:
    held = _to_change(library, REGISTRY)
    if held.pop(folder_name, None) is not None:
        _write(library, REGISTRY, held)


def remove_copy(library: Path, link: Path) -> bool:
    """Delete a copy the library holds, and its junction in the mods folder --
    for a mod taken out that nothing else uses.  The link may be switched off
    already, and so gone; what it pointed at is remembered.  False, touching
    nothing, for anything that is not a copy in the library: a reference is
    the user's own folder, and a real folder is theirs too."""
    library, link = Path(library), Path(link)
    held = links_to_change(library)
    entry = held.get(link.name) or {}
    target = source_of(link, library) or (Path(entry["target"]) if entry.get("target") else None)
    if target is None or not disk.inside(target, library) or not target.is_dir():
        return False
    # Before anything goes: a copy deleted with its record refused would be
    # a record of nothing.
    check_records(library)
    if is_link(link):
        remove_link(link)
    generated.remove(generated.folder_for(library, target.name))
    shutil.rmtree(target)
    forget(library, target.name)
    if held.pop(link.name, None) is not None:
        write_links(library, held)
    return True


# -- which loadout is live --------------------------------------------------------------
def live(library: Path) -> str:
    """The loadout the Studio last made live for this game, or "".

    A fact about the machine, not about any loadout: the mods folder holds one
    set of switches, so at most one loadout is what the game loads -- the way
    117's ``profile.txt`` names one profile.  Kept beside the library rather
    than in the loadouts, because it is the library's links that it describes
    and no loadout should have to be opened to learn which one it is.
    """
    return str(live_record(library).get("loadout") or "")


def set_live(library: Path, loadout, at: Optional[float] = None, fingerprint: str = "",
             profile: str = "") -> None:
    """Remember ``loadout`` (its file) as the one the game loads now, and
    ``profile``, which of its profiles; when -- a loader log older than that
    is an answer about something else -- and ``fingerprint``, what the mods
    folder was once Play had written it (see :func:`core.truth.fingerprint`),
    so a later look can tell whether it is still as Play left it."""
    import time

    _write(library, LIVE, {
        "loadout": str(loadout), "profile": str(profile),
        "at": time.time() if at is None else at,
        "fingerprint": fingerprint, "launched": 0,
    })


def live_profile(library: Path) -> str:
    """Which profile of the live loadout the game loads, by name, or "" for
    a record written before loadouts had profiles -- whose loadout had one,
    and so means its first."""
    return str(live_record(library).get("profile") or "")


def follow_live_profile(library: Path, loadout, before: str, after: str) -> None:
    """A live profile renamed, and its loadout saved with the new name: the
    record follows it, as it follows a renamed loadout."""
    held = live_record(library)
    if (
        held
        and disk.same_file(held.get("loadout") or "", loadout)
        and str(held.get("profile") or "") == before
    ):
        held["profile"] = after
        _write(library, LIVE, held)


def clear_live(library: Path) -> None:
    """Forget which loadout is live: after a conversion is undone, none of
    the loadouts describes the folder."""
    with contextlib.suppress(OSError):
        (Path(library) / LIVE).unlink()


def live_record(library: Path) -> dict:
    """Everything the pointer says, or {}."""
    return dict(_read(library, LIVE).data)


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def live_since(library: Path) -> float:
    """When the live loadout was made live, or 0 for a pointer that does
    not say."""
    return _number(live_record(library).get("at"))


def launched_at(library: Path) -> float:
    """When Play last started the game for the live loadout, or 0."""
    return _number(live_record(library).get("launched"))


def note_launched(library: Path, at: Optional[float] = None) -> None:
    """Play has just started the game for the live loadout."""
    import time

    held = live_record(library)
    if not held:
        return
    held["launched"] = time.time() if at is None else at
    _write(library, LIVE, held)


def follow_live(library: Path, before, after) -> None:
    """A loadout that moved takes being live with it: renaming the loadout
    the game loads does not make it load nothing."""
    if disk.same_file(live(library), before):
        held = live_record(library)
        held["loadout"] = str(after)
        _write(library, LIVE, held)


# -- putting a mod there, and a junction to it -----------------------------------------
@dataclass
class Linked:
    """What linking did: the junction now in the mods folder, the folder it
    points at, and a real folder that was in the way and was set aside."""

    link: Path
    target: Path
    set_aside: Optional[Path] = None


def link(target: Path, mods_folder: Path, name: str = "") -> Linked:
    """Link ``mods_folder/<name>`` to ``target``, which is a mod folder.

    A junction already at that name is replaced; a real folder there is set
    aside first (see :func:`core.modzip.set_aside`) -- this is a mod the user
    asked for, and the folder in the way is the same mod as far as the game is
    concerned.  A switched-off ``-`` copy of the same name is left where it
    is: it is off, and its being there is the user's.

    Nothing is set aside or linked at a name that does not land directly in
    ``mods_folder`` (:func:`core.disk.directly_in`): the name may have come
    from a mod.io mod's, which anyone publishing chooses.
    """
    target = Path(target)
    mods_folder = Path(mods_folder)
    mods_folder.mkdir(parents=True, exist_ok=True)
    at = mods_folder / (name or target.name)
    if not disk.directly_in(at, mods_folder):
        raise OSError(tr("%s cannot be a folder's name, so it was not linked", name or target.name))
    done = Linked(link=at, target=target)
    # Asked before anything at the name is moved: the junction is made last,
    # and a folder set aside for a link that cannot be made was a mod gone
    # from the mods folder.
    if not target.is_dir():
        raise OSError(tr("%s is not there, so it was not linked", target))
    if disk.same_file(at, target):
        if is_link(at):
            # The junction itself was chosen: what it stands for is where it
            # points, and a record of the junction would point at itself.
            done.target = target_of(at) or target
            return done
        raise OSError(tr("%s is in the mods folder already, so it was not linked", at))
    was = target_of(at)
    if is_link(at):
        remove_link(at)
    elif at.exists():
        done.set_aside = modzip.set_aside(at)
    try:
        make_link(at, target)
    except OSError:
        with contextlib.suppress(OSError):
            if was is not None:
                make_link(at, was)
            elif done.set_aside is not None:
                shutil.move(str(done.set_aside), str(at))
        raise
    return done


@dataclass
class LibraryInstallResult(modzip.InstallResult):
    """What installing into the library did: :class:`core.modzip.InstallResult`
    for the library's side, and the junctions made in the mods folder."""

    linked: List[Linked] = field(default_factory=list)


PREVIOUS = "previous"
# What of a version is kept once it is replaced: what can be read and
# compared.  Its models and textures are most of its size and nothing a
# comparison of ModOps looks at.
_KEPT_OF_A_VERSION = (".xml", ".json", ".jsonc", ".txt", ".lua")


def previous_dir(library: Path) -> Path:
    """Where the version of each library mod an update replaced is kept:
    beside the library's folder for the game, as its generated copies are."""
    library = Path(library)
    return library.parent / PREVIOUS / library.name


def previous_of(library: Path, name: str) -> Optional[Path]:
    """The version of the library's copy ``name`` that the last update
    replaced, as text, or None."""
    found = previous_dir(library) / name
    return found if found.is_dir() else None


def keep_previous(library: Path, folder: Path) -> Optional[Path]:
    """Keep the text of a library copy about to be replaced: its ModOps, its
    modinfo and the rest of what can be read, under its own name.  The one
    kept before is replaced; one version back is what an update is
    compared with.  Where it went, or None for nothing to keep."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    into = previous_dir(library) / folder.name
    if into.exists():
        shutil.rmtree(into)
    kept = 0
    for top, _dirs, names in os.walk(folder):
        for one in names:
            path = Path(top) / one
            if path.suffix.lower() not in _KEPT_OF_A_VERSION:
                continue
            goes = into / path.relative_to(folder)
            goes.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, goes)
            kept += 1
    return into if kept else None


def install(
    archive, library: Path, mods_folder, replace: Optional[Path] = None, name: str = "",
    *, facts: Optional[dict] = None,
) -> LibraryInstallResult:
    """Unpack ``archive`` into the library and junction each mod it holds into
    ``mods_folder`` -- what :func:`core.modzip.install` does, with the folder
    landing here instead and a link where the folder would have been.

    ``replace`` is the installed folder this supersedes.  A junction into
    the library is replaced in place, target and all; a real folder is set
    aside by the link, and the new mod takes its name exactly -- the name is
    what Anno 1800 keys load order on, and a switched-off mark on it is the
    user's to keep.

    ``facts`` is what the mod was installed as -- ``modio_id``, ``modfile_id``,
    ``version``, ``name`` -- kept in the registry against each folder placed.

    No profile is written.  Anno 117's live one used to be told the mod was
    on, which wrote into whichever profile was live -- the player's own, as
    often as not -- outside Play.  Whether a mod is on is the loadout's to
    say and Play's to write; the folder read after an install is told which
    mods just arrived, so they come into the loadout on.
    """
    library, mods_folder = Path(library), Path(mods_folder)
    check_records(library)
    # The library's copy being replaced, where the link to it points at a
    # generated copy of it as much as where it points at the copy itself.
    old_target = source_of(replace, library) if replace is not None else None
    # The player's iModYourAnno settings live in the folder the game loads,
    # and the author's fresh one knows nothing of them: read them before
    # anything moves, and write them into a generated copy of the new one
    # after.  See core.imya.carry.
    old_folder = target_of(replace) if replace is not None else None
    if old_folder is None and replace is not None and Path(replace).is_dir():
        old_folder = Path(replace)
    before = imya.snapshot(old_folder) if old_folder is not None else imya.Snapshot()
    if old_target is not None and disk.inside(old_target, library):
        # The version being replaced, kept as text: what an update changed
        # can be shown against it, and the version it leaves behind is not
        # simply gone.
        with contextlib.suppress(OSError):
            keep_previous(library, old_target)
        done = modzip.install(archive, library, replace=old_target, name=name)
    else:
        done = modzip.install(archive, library, name=(replace.name if replace is not None else name))
    result = LibraryInstallResult(placed=done.placed, set_aside=done.set_aside, skipped=done.skipped)
    # Worked out against the fresh copy and written into a generated copy of
    # it once it is linked: the library's copy stays as its author shipped it.
    carried = imya.carry(before, done.placed[0], write_them=False) if (
        before and len(done.placed) == 1) else None
    result.carried = carried
    for placed in done.placed:
        wanted = placed.name
        if replace is not None and len(done.placed) == 1:
            wanted = Path(replace).name
        linked = link(placed, mods_folder, wanted)
        note_link(library, linked, COPY)
        result.linked.append(linked)
        if linked.set_aside is not None and result.set_aside is None:
            result.set_aside = linked.set_aside
        if facts:
            # Named by mod.io now, whatever it was matched to or unpacked
            # from before.
            named = bool(facts.get("modio_id"))
            # A backup was of the copy this replaces, and mod.io has this one.
            record(library, placed.name,
                   forget=(KNOWN_BY, *_OF_A_ZIP, BACKUP) if named else (), **facts)
    # A generated copy of the copy just replaced was made of the old one.
    for placed in done.placed:
        generated.remove(generated.folder_for(library, placed.name))
    if carried is not None and carried.kept and result.linked:
        name = result.linked[0].link.name
        try:
            tweaks = imya.read(done.placed[0], bundled=True)
            written = imya.changed_files(done.placed[0], tweaks, carried.kept) if tweaks else {}
            made = generate(library, name, written, edits=[{
                "kind": "iModYourAnno settings", "mod": name, "settings": dict(carried.kept),
            }])
            if made is not None and made.wrong:
                carried.error = tr("its generated copy did not come out as it should")
        except OSError as exc:
            carried.error = str(exc)
    return result


# -- a zip from disk -------------------------------------------------------------
def confirm(library: Path, folder_name: str, modio_id: int, modfile_id: int) -> bool:
    """Remember that a copy is this file of this mod.io mod, by the hash of
    the zip it was unpacked from: as sure as having downloaded it, so it is
    no longer only a match.  Whether anything was written."""
    if not folder_name or not modio_id or not modfile_id:
        return False
    if not (Path(library) / folder_name).is_dir():
        return False
    entry = registry(library).get(folder_name, {})
    same = (int(entry.get("modio_id") or 0), int(entry.get("modfile_id") or 0)) == (
        int(modio_id), int(modfile_id))
    if same and not entry.get(KNOWN_BY):
        return False
    record(library, folder_name, forget=(KNOWN_BY,),
           modio_id=int(modio_id), modfile_id=int(modfile_id))
    return True


def md5_of(path) -> str:
    """The MD5 of a file, which is the hash mod.io publishes of each of its
    own: a zip downloaded from there by hand can be told by it."""
    # An identity to compare, not a secret.
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Arriving:
    """One mod a zip on disk would put in the library: the folder it lands
    under, what its modinfo says of it, and the copy it takes the place of."""

    archive: Path
    name: str
    mod_name: str = ""
    mod_id: str = ""
    version: str = ""
    creator: str = ""
    # The game its own layout says it is for: see core.modzip.Peeked.game.
    game: str = ""
    replaces: Optional[Held] = None

    @property
    def label(self) -> str:
        return self.mod_name or modfolder.bare_name(self.name)


def arriving(archive, library: Path) -> List[Arriving]:
    """What :func:`install_zip` of ``archive`` would put in the library, with
    nothing written: see :func:`core.modzip.peek`, whose errors these are.

    A copy is replaced when it declares the ModID the arriving mod does --
    the game keeps one copy of a ModID, so a second folder is the same mod
    twice -- or sits in the folder the new one lands in.  Only a zip of one
    mod is matched by ModID: a box's mods land under their own names.  A
    reference is never replaced, being a folder of the user's own.
    """
    archive = Path(archive)
    peeked = modzip.peek(archive)
    copies = [mod for mod in catalogue(library) if mod.kind == COPY]
    found = []
    for mod in peeked:
        name = mod.folder or archive.stem
        old = None
        if mod.mod_id and len(peeked) == 1:
            old = next((copy for copy in copies if copy.mod_id.lower() == mod.mod_id.lower()), None)
        if old is None:
            old = next((copy for copy in copies if copy.target.name.lower() == name.lower()), None)
        found.append(Arriving(
            archive=archive, name=name, mod_name=mod.mod_name, mod_id=mod.mod_id,
            version=mod.version, creator=mod.creator, game=mod.game, replaces=old,
        ))
    return found


def install_zip(archive, library: Path, mods_folder) -> LibraryInstallResult:
    """:func:`install` for a zip on disk, which nobody has said what it is:
    the copy :func:`arriving` says it replaces is replaced, and each mod is
    recorded by what its own modinfo says, with the zip's name and hash --
    all that could later say which file of whose it was -- and the zip is
    kept with the backups, as it came (:func:`keep_zip`): nobody else may
    have it to give again.

    A copy being replaced that is switched off is linked again first: the
    mod was asked for, and comes in on, as any install does.
    """
    library = Path(library)
    found = arriving(archive, library)
    replace = None
    if len(found) == 1 and found[0].replaces is not None:
        old = found[0].replaces
        replace = old.link if old.linked else relink(library, old.name)
    done = install(archive, library, mods_folder, replace=replace)
    try:
        md5 = md5_of(archive)
    except OSError:
        md5 = ""
    try:
        kept = str(keep_zip(library, archive, md5))
    except OSError:
        kept = ""  # installed all the same; it is listed as an only copy
    held = _to_change(library, REGISTRY)
    for placed in done.placed:
        # Whatever mod.io file this folder was installed as, it is not that
        # file now; the mod.io mod it is stays known, for the next update.
        entry = held.get(placed.name, {})
        entry.pop("modfile_id", None)
        entry["version"] = str(modfolder.metadata_of(placed).get("version") or "")
        entry["source"] = ZIP
        entry["zip"] = Path(archive).name
        if md5:
            entry["zip_md5"] = md5
        else:
            entry.pop("zip_md5", None)
        if kept:
            entry[BACKUP] = kept
        else:
            entry.pop(BACKUP, None)
        held[placed.name] = entry
    _write(library, REGISTRY, held)
    return done


def mod_id_of(folder) -> str:
    """The ModID a mod folder declares, lower-cased, or ""."""
    return str(modfolder.metadata_of(folder).get("mod_id") or "").strip().lower()


def adopt(library: Path, mod_folder: Path, mods_folder: Optional[Path] = None) -> Linked:
    """Move a real mod folder into the library and junction it back where it
    was, under its bare name.

    A library mod's switch is its link, so a folder switched off by Anno
    1800's ``-`` mark comes in off the library's way: linked under the bare
    name and the link taken away at once, remembered, ready for Play to put
    back.  It used to keep the mark in the junction's name, which left a
    ``-``-named link standing for a switch the library does not use.

    On one drive the move is a rename.  Across drives it copies, checks the
    copy came out whole, and only then deletes -- a move that fails part way
    must not leave half a mod behind.
    """
    library, mod_folder = Path(library), Path(mod_folder)
    check_records(library)
    library.mkdir(parents=True, exist_ok=True)
    bare = modzip.bare(mod_folder)
    target = library / bare
    counter = 2
    while target.exists():
        target = library / ("%s (%d)" % (bare, counter))
        counter += 1
    move_whole(mod_folder, target)
    linked = link(target, mods_folder or mod_folder.parent, bare)
    note_link(library, linked, COPY)
    # Moved, so this is the only copy there is: said, for whoever asks what
    # the library could not get back.
    record(
        library, target.name, source=ADOPTED,
        version=str(modfolder.metadata_of(target).get("version") or ""),
    )
    if modfolder.is_marked_off(mod_folder.name):
        unlink(library, linked.link)
    return linked


def move_whole(source: Path, target: Path) -> None:
    """Move a folder, never leaving half of it anywhere."""
    try:
        os.rename(str(source), str(target))
        return
    except OSError:
        pass  # another drive, most often
    shutil.copytree(str(source), str(target))
    if _files_of(target) != _files_of(source):
        shutil.rmtree(str(target), ignore_errors=True)
        raise OSError(tr("the copy of %s did not come out whole, so no folder was moved", source.name))
    shutil.rmtree(str(source))


def _files_of(folder: Path) -> dict:
    found = {}
    for path in Path(folder).rglob("*"):
        try:
            if path.is_file():
                found[str(path.relative_to(folder))] = path.stat().st_size
        except OSError:
            continue
    return found


def refer(library: Path, folder: Path, mods_folder: Path, name: str = "") -> Linked:
    """Manage ``folder`` where it is: junction it into the mods folder and
    record that the library stands for it."""
    library, folder = Path(library), Path(folder)
    check_records(library)
    linked = link(folder, mods_folder, name or folder.name)
    held = references(library)
    held[linked.link.name] = str(linked.target)
    _write_references(library, held)
    note_link(library, linked, REFERENCE)
    return linked


# -- leaving ---------------------------------------------------------------------------
@dataclass
class PutBack:
    """What leaving did: copies that are real folders again, references whose
    junctions are gone, and what could not be done."""

    materialised: List[Path] = field(default_factory=list)
    unlinked: List[Path] = field(default_factory=list)
    failed: List[tuple] = field(default_factory=list)

    def describe(self) -> str:
        bits = []
        if self.materialised:
            bits.append(trn(
                "%(n)s mod is a real folder in the mods folder again",
                "%(n)s mods are real folders in the mods folder again",
                len(self.materialised),
            ))
        if self.unlinked:
            bits.append(trn(
                "%(n)s link to a folder of your own was removed",
                "%(n)s links to folders of your own were removed",
                len(self.unlinked),
            ))
        if self.failed:
            bits.append(trn(
                "%(n)s could not be put back", "%(n)s could not be put back", len(self.failed),
            ))
        return ("; ".join(bits) + ".") if bits else tr("No mod in the mods "
                                                       "folder came from the "
                                                       "library.")


def linked_in(mods_folders, library: Path) -> List[Path]:
    """Every junction in these folders that is the library's, copy or reference."""
    found = []
    for folder in mods_folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if kind_of(entry, library):
                found.append(entry)
    return found


def _materialise(library: Path, pointed: Path, at: Path) -> None:
    """Move what a link pointed at into its place as a real folder.  A
    generated copy goes in, as the mod the game was loading, and the library
    copy it was made from goes; its files are the copy's own, being links to
    the same files or copies of them."""
    source = generated.source_of(pointed) if generated.is_generated(pointed, library) else None
    shutil.move(str(pointed), str(at))
    if source is not None:
        generated.materialise(at)
        if disk.inside(source, library) and source.is_dir():
            shutil.rmtree(source)
        forget(library, source.name)
    else:
        forget(library, pointed.name)


def put_back(mods_folders, library: Path) -> PutBack:
    """Make the mods folder whole without the library.

    Every junction to a copy becomes the folder it pointed at, moved into the
    junction's place; every junction to a reference is removed, the folder
    it stood for being the user's and staying where it is.
    """
    library = Path(library)
    check_records(library)
    done = PutBack()
    held = references(library)
    # A mod switched off by having its link taken away is put back as a
    # folder where the link was, so nothing the library holds is left behind.
    for name, entry in unlinked(library).items():
        at = Path(entry.get("mods_folder") or "") / name
        target = Path(entry.get("target") or "")
        made = Path(entry["generated"]) if entry.get("generated") else None
        try:
            if entry.get("kind") == COPY and target.is_dir() and not at.exists():
                _materialise(library, made if made is not None and made.is_dir() else target, at)
                done.materialised.append(at)
            else:
                held.pop(name, None)
                done.unlinked.append(at)
        except OSError as exc:
            done.failed.append((name, str(exc)))
    for link in linked_in(mods_folders, library):
        kind = kind_of(link, library)
        pointed = target_of(link)
        try:
            remove_link(link)
            if kind == COPY and pointed is not None:
                _materialise(library, pointed, link)
                done.materialised.append(link)
            else:
                held.pop(link.name, None)
                done.unlinked.append(link)
        except OSError as exc:
            done.failed.append((link.name, str(exc)))
    if held != references(library):
        _write_references(library, held)
    if done.materialised or done.unlinked:
        write_links(library, {})
    return done
