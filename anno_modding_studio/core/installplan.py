"""Here is a list of mods: which of them are installed, and what fetching the
rest would take.

Two things end at this question.  A collection on mod.io is a list of mods
somebody curated; a loadout somebody else made is a record of the mods it was
built on.  Either way the answer is a **plan** -- a row per mod wanted, saying
whether it is here already, whether mod.io has it, and how big it is -- and a
**queue** that fetches the rows that were ticked, one at a time, and says what
became of each.

Three things shape both.

*What is installed is asked of the disk, every time.*  Not of a loaded
reading, which may be of another folder or of none, and never of a cached
answer: a plan that is wrong about what is installed offers to install it
again.  Every folder the game loads from is looked in -- the one being
installed into, the one under Documents, and mod.io's own download cache --
and a mod is matched by its ModID before its folder name, because the same
mod is routinely installed twice under two names (see :mod:`core.updates`).

*A collection is forty mods and a gigabyte.*  So the queue is not one fetch
that works or does not.  One bad mod does not lose the others; stopping
leaves what landed in place, and the next plan -- computed from disk again --
offers only what is still missing.

*Nothing already there is replaced without being asked.*  A mod that is
installed but older is listed as that and left unticked: bringing mods up to
date is the update check's job, and a side effect of installing something
else is the wrong way to find out a mod you had was swapped.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import (
    disk,
    library,
    modfolder,
    modio,
    modio_cache,
    modzip,
    switches,
    updates,
    zipped,
)
from .games import ANNO_1800
from .i18n import number, size_text, tr, trn

# Where zips sit while they are fetched: inside the mods folder, so the move
# into place is a rename, and dot-named, so nothing reads it as a mod.
DOWNLOADS = ".modio-downloads"

# What the plan says about one wanted mod.
MISSING = "missing"  # not installed, and mod.io has a file for it
CURRENT = "current"  # installed, and it is the file mod.io has now
OLDER = "older"  # installed, and mod.io has a newer file
INSTALLED = "installed"  # installed; how it compares cannot be said
GONE = "gone"  # not installed, and mod.io has no file for it any more
FLAGGED = "flagged"  # mod.io's scan flagged its file; never fetched
UNFOUND = "unfound"  # not installed, and no mod on mod.io could be matched to it

# What became of one row of the queue.
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
NOT_ATTEMPTED = "not attempted"


# -- what is on disk ------------------------------------------------------------
@dataclass
class FolderMod:
    """One mod in a mods folder, on or off, as its own modinfo describes it.

    A library mod switched off by taking its link away is still one, counted
    where the link was. Not "installed": the library holds more than any
    folder shows."""

    folder: Path
    name: str
    mod_id: str = ""
    version: str = ""
    # Sitting in mod.io's download cache, which belongs to the game's own
    # browser: never written to from here.
    in_cache: bool = False
    # Inside another mod rather than a folder of its own; replacing it means
    # replacing its host.
    bundled: bool = False
    modio_id: int = 0
    modfile_id: int = 0
    # What the folder is to the Studio's library: a junction to a copy in it,
    # a junction to a folder it references, or "" for a folder simply there.
    kind: str = ""
    # For a library mod, whether its junction is there.  Off is no link: the
    # folder is the library's still, and the game does not see it.
    linked: bool = True
    # A folder Anno 1800's loader lists as a mod and loads nothing from: it
    # lists every folder in the mods folder, and runs only what sits under a
    # ``data`` folder or beside a modinfo.  Counted so the Studio can say what
    # the loader will list, and kept out of everything that is about mods.
    inert: bool = False
    # And of those, a mod's own data tree with no folder of its own -- see
    # :func:`core.modzip.is_loose_data` -- which is a mod that would work if
    # it were put in one.
    loose: bool = False
    # Its modinfo as its author wrote it, read here anyway: what it says it
    # needs, clashes with and replaces is checked before Play -- see
    # :mod:`core.dependencies`.
    declared: dict = field(default_factory=dict)
    # Read out of an Anno 1800 zip, through its mirror: the folder is the
    # Studio's copy, and the zip is the mod -- see :mod:`core.zipped`.
    zipped: bool = False

    @property
    def folder_name(self) -> str:
        return modfolder.bare_name(self.folder.name)

    @property
    def key(self) -> str:
        return modfolder.mod_key(self.mod_id, self.folder.name)


def bundled_path(parts, in_cache: bool) -> bool:
    """Whether a folder this far below a mods folder is inside another mod."""
    return len(parts) > (2 if in_cache else 1)


def inventory(
    game: str, folder, links: Optional[Dict[str, int]] = None, library_dir=None,
) -> List[FolderMod]:
    """Every mod in every folder the game loads from, given one of them.

    ``links`` is what is already known about which mod.io mod a ModID is --
    lower-cased ModID to mod.io id -- from a loadout that remembers.  The
    download cache says the same of its own mods by where they sit.
    ``library_dir`` is the Studio's library for this game, whose junctions
    are told from folders; the default one when not given.
    """
    links = {str(key).lower(): int(value) for key, value in (links or {}).items() if value}
    library_dir = Path(library_dir) if library_dir else library.game_dir(game)
    known = library.registry(library_dir)
    cache_root = modio_cache.mods_dir(game)
    records = {record.path.name: record for record in modio_cache.installed(game)}
    found: List[FolderMod] = []
    # Numbered cache folders that are a mod themselves: see below.
    direct = set()
    for root in switches.mod_paths(game, folder):
        in_cache = cache_root is not None and root == cache_root
        from_zip = zipped.is_mirrored(root)
        for path in modfolder.mod_folders(root):
            parts = path.relative_to(root).parts
            # A mods folder holds other things -- 117's profiles, the loader's
            # log -- and a folder with neither data nor a modinfo is one.
            inert = (
                game == ANNO_1800 and not in_cache and not bundled_path(parts, in_cache)
                and not modzip.is_mod_folder(path) and not modfolder.bundled_in(path)
            )
            if not modzip.is_mod_folder(path) and not inert:
                continue
            meta = modfolder.metadata_of(path)
            if in_cache:
                # ``mods/<mod.io id>/<mod folder>``: the numbered level is not
                # a mod, and the folder inside it is not bundled in anything.
                # Unless the mod's own files sit in the numbered level --
                # *Building storage improvements* is shipped that way, the
                # game loads it, and leaving it out left it installed and
                # unseen: missing from its loadout, and never converted.
                if len(parts) == 1:
                    direct.add(parts[0])
                    bundled = False
                else:
                    bundled = len(parts) > 2 or parts[0] in direct
            else:
                bundled = len(parts) > 1
            mod = FolderMod(
                folder=path,
                name=str(meta.get("mod_name") or "") or modfolder.bare_name(path.name),
                mod_id=str(meta.get("mod_id") or ""),
                version=str(meta.get("version") or ""),
                in_cache=in_cache,
                bundled=bundled,
                inert=inert,
                loose=inert and modzip.is_loose_data(path),
                declared=dict(meta.get("declared") or {}),
                zipped=from_zip,
            )
            if not in_cache and not bundled and not from_zip:
                mod.kind = library.kind_of(path, library_dir)
                if mod.kind == library.COPY:
                    # The Studio installed it, and wrote down as what.
                    target = library.source_of(path, library_dir)
                    facts = known.get(target.name if target is not None else "", {})
                    mod.modio_id = int(facts.get("modio_id") or 0)
                    mod.modfile_id = int(facts.get("modfile_id") or 0)
                    mod.version = mod.version or str(facts.get("version") or "")
            if in_cache and not bundled and parts[0].isdigit():
                mod.modio_id = int(parts[0])
                record = records.get(parts[0])
                if record is not None:
                    mod.modfile_id = record.modfile_id
                    mod.version = mod.version or record.version
            found.append(mod)
    # A library mod switched off by having its link taken away is installed
    # still -- the library holds it -- and off.  Listed as that, where the
    # link was, so the truth and the plan know it is here.
    for name, entry in library.unlinked(library_dir).items():
        target = Path(entry.get("target") or "")
        if not target.is_dir():
            continue
        meta = modfolder.metadata_of(target)
        facts = known.get(target.name, {}) if entry.get("kind") == library.COPY else {}
        found.append(FolderMod(
            folder=Path(entry.get("mods_folder") or "") / name,
            name=str(meta.get("mod_name") or "") or modfolder.bare_name(name),
            mod_id=str(meta.get("mod_id") or ""),
            version=str(meta.get("version") or "") or str(facts.get("version") or ""),
            kind=str(entry.get("kind") or library.COPY),
            linked=False,
            modio_id=int(facts.get("modio_id") or 0),
            modfile_id=int(facts.get("modfile_id") or 0),
            declared=dict(meta.get("declared") or {}),
        ))
    # The id belongs to the ModID, not the folder: what the cache or a loadout
    # says of one copy is true of every copy.
    for mod in found:
        if mod.modio_id and mod.mod_id:
            links.setdefault(mod.mod_id.lower(), mod.modio_id)
    for mod in found:
        if not mod.modio_id and mod.mod_id:
            mod.modio_id = links.get(mod.mod_id.lower(), 0)
    return found


# -- what is wanted -------------------------------------------------------------
@dataclass
class Wanted:
    """One mod somebody's list names, by whatever the list knows of it."""

    name: str
    modio_id: int = 0
    # mod.io's side: its current file.  None until asked, or when it has none.
    summary: Optional[modio.ModSummary] = None
    # What a loadout knows and a collection does not.
    mod_id: str = ""
    folder_name: str = ""
    version: str = ""
    active: bool = True
    origin: str = ""


def wanted_from_mods(mods: Iterable[modio.ModSummary]) -> List[Wanted]:
    return [Wanted(name=mod.name, modio_id=mod.id, summary=mod) for mod in mods]


def wanted_from_loadout(loadout) -> List[Wanted]:
    """The mods a loadout was built on, as far as somebody else could get them.

    Not the mod the loadout is, and not its patches, forks or other mods of
    yours (:meth:`Loadout.yours`): those are what the loadout itself writes,
    and exporting is how they get to a mods folder.  Not a mod bundled inside
    another either -- it arrives with its host.
    """
    found: List[Wanted] = []
    for layer in loadout.layers:
        if loadout.yours(layer):
            continue
        if modfolder.is_bundled(layer.origin):
            continue
        found.append(
            Wanted(
                name=layer.name or modfolder.folder_name(layer.origin),
                modio_id=int(layer.modio_id or 0) or modfolder.cache_id(layer.origin),
                mod_id=layer.mod_id or "",
                folder_name=modfolder.folder_name(layer.origin),
                version=layer.version or "",
                active=bool(layer.active),
                origin=layer.origin,
            )
        )
    return found


def copies_of(wanted: Wanted, in_folders: List[FolderMod]) -> List[FolderMod]:
    """Every copy of a wanted mod in the mods folders, the one the game loads first.

    Matched by what is surest first -- its mod.io id, its ModID, its folder's
    name, then what it is called -- and then widened to every copy sharing a
    ModID with one of those, since a second copy under another folder name is
    the same mod to the game.
    """
    found = [
        mod for mod in in_folders
        if (wanted.modio_id and mod.modio_id == wanted.modio_id)
        or (wanted.mod_id and mod.mod_id.lower() == wanted.mod_id.lower())
    ]
    if not found:
        found = [
            mod for mod in in_folders
            if (wanted.folder_name and not mod.bundled
                and mod.folder_name.casefold() == wanted.folder_name.casefold())
            or updates.same_name(mod.name, wanted.name)
            or updates.same_name(mod.folder_name, wanted.name)
        ]
    ids = {mod.mod_id.lower() for mod in found if mod.mod_id}
    for mod in in_folders:
        if mod.mod_id and mod.mod_id.lower() in ids and mod not in found:
            found.append(mod)
    loads = modfolder.loading_copy(found)
    return [loads] + [mod for mod in found if mod is not loads] if loads is not None else found


def missing(wanted: List[Wanted], in_folders: List[FolderMod]) -> List[Wanted]:
    """Which of these no mods folder has, on or off -- asked of the disk alone."""
    return [one for one in wanted if not copies_of(one, in_folders)]


# -- asking mod.io about a loadout's mods -----------------------------------------
Progress = Callable[[int, int, str], None]


def resolve(
    client: modio.Client,
    game_id: int,
    wanted: List[Wanted],
    progress: Optional[Progress] = None,
) -> Dict[str, int]:
    """Find each of these on mod.io, and return what was learned by name.

    One request a hundred for the ones whose mod.io id is known, then one a
    mod for the rest -- looked up by name, and taken only when the name is
    the same, or near enough (:func:`updates.called`).  ``{origin: mod.io
    id}`` for those, so a loadout can remember and not ask twice.
    """
    say = progress or (lambda done, total, text: None)
    unknown = [one for one in wanted if not one.modio_id]
    total = len(unknown) + 1
    say(0, total, trn("Asking mod.io about %(n)s mod…", "Asking mod.io about %(n)s mods…", len(wanted)))
    latest = client.mods_by_ids(game_id, [one.modio_id for one in wanted if one.modio_id])
    learned: Dict[str, int] = {}
    for done, one in enumerate(unknown, start=1):
        name = updates.strip_category(one.name)
        say(done, total, tr("Looking for %s on mod.io…", name or one.name))
        if not name:
            continue
        candidate = updates.called(client.search(game_id, name), name)
        if candidate is not None:
            one.modio_id = candidate.id
            latest[candidate.id] = candidate
            if one.origin:
                learned[one.origin] = candidate.id
    for one in wanted:
        one.summary = latest.get(one.modio_id) if one.modio_id else None
    say(total, total, "")
    return learned


# -- where a list of wanted mods comes from ----------------------------------------
@dataclass
class Gathered:
    """A list of wanted mods, what should be said about it before anything is
    fetched, and what asking taught about which mod.io mod is which."""

    wanted: List[Wanted] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    learned: Dict[str, int] = field(default_factory=dict)


# ``gather(client, say, stop, installed)``: asked again whenever the folder
# being installed into changes, so each holds on to what it has already asked
# mod.io and asks only for what is new.
Gather = Callable[[modio.Client, Progress, Callable[[], bool], List[FolderMod]], Gathered]


def collection_source(game_id: int, collection: modio.CollectionSummary) -> Gather:
    """The mods of a mod.io collection: one request a hundred, asked once."""
    held: List[List[Wanted]] = []

    def gather(client, say, stop, installed) -> Gathered:
        if not held:
            say(0, 0, tr("Asking mod.io what is in %s…", collection.name))
            held.append(wanted_from_mods(client.collection_mods(game_id, collection.id)))
        notes = []
        if collection.incomplete_mods:
            notes.append(trn(
                "mod.io marks this collection incomplete: %(n)s of its mods is no longer "
                "there, and cannot be installed by anybody.",
                "mod.io marks this collection incomplete: %(n)s of its mods are no longer "
                "there, and cannot be installed by anybody.",
                collection.incomplete_mods,
            ))
        elif collection.incomplete:
            notes.append(tr(
                "mod.io marks this collection incomplete: some of its mods are no longer "
                "there, and cannot be installed by anybody."
            ))
        return Gathered(held[0], notes)

    return gather


def loadout_source(game_id: int, loadout) -> Gather:
    """The mods a loadout was built on.  Only the ones not installed are
    looked for on mod.io -- the rest need nothing from it."""
    wanted = wanted_from_loadout(loadout)
    learned: Dict[str, int] = {}
    asked: set = set()

    def gather(client, say, stop, installed) -> Gathered:
        absent = [one for one in missing(wanted, installed) if id(one) not in asked]
        if absent:
            learned.update(resolve(client, game_id, absent, say))
            asked.update(id(one) for one in absent)
        notes = []
        configured = [
            layer.name or layer.origin for layer in loadout.layers
            if layer.options and any(one.origin == layer.origin for one in wanted)
        ]
        if configured:
            names = ", ".join(configured[:6]) + ("…" if len(configured) > 6 else "")
            notes.append(trn(
                "%(n)s of these mods has settings of its own, and this "
                "loadout was read at particular ones. The Studio reads them "
                "the same way. The game's own mod menu still has to be set "
                "to match: %(names)s.",
                "%(n)s of these mods have settings of their own, and this "
                "loadout was read at particular ones. The Studio reads them "
                "the same way. The game's own mod menu still has to be set "
                "to match: %(names)s.",
                len(configured), names=names,
            ))
        return Gathered(wanted, notes, dict(learned))

    return gather


# -- the plan ---------------------------------------------------------------------
@dataclass
class Row:
    """One wanted mod, what is known of it here, and whether to fetch it."""

    wanted: Wanted
    status: str
    copies: List[FolderMod] = field(default_factory=list)
    note: str = ""
    # The folder a fetch supersedes, for a mod that is installed but older.
    replace: Optional[Path] = None
    # Why this row cannot be fetched, or "" if it can.
    why_not: str = ""
    fetch: bool = False

    @property
    def name(self) -> str:
        summary = self.wanted.summary
        return (summary.name if summary is not None else "") or self.wanted.name

    @property
    def fetchable(self) -> bool:
        return not self.why_not

    @property
    def size(self) -> int:
        return self.wanted.summary.filesize if self.wanted.summary is not None else 0

    @property
    def unpacked(self) -> int:
        summary = self.wanted.summary
        if summary is None:
            return 0
        return summary.filesize_uncompressed or summary.filesize


@dataclass
class Plan:
    rows: List[Row] = field(default_factory=list)

    def chosen(self) -> List[Row]:
        return [row for row in self.rows if row.fetch and row.fetchable]

    def count(self, *statuses: str) -> int:
        return sum(1 for row in self.rows if row.status in statuses)

    @property
    def download_size(self) -> int:
        return sum(row.size for row in self.chosen())

    @property
    def unpacked_size(self) -> int:
        return sum(row.unpacked for row in self.chosen())

    @property
    def room_needed(self) -> int:
        """What has to be free: everything unpacked, and the biggest zip beside
        it -- the zips come and go one at a time."""
        chosen = self.chosen()
        return self.unpacked_size + max((row.size for row in chosen), default=0)

    def describe(self) -> str:
        """The sentence under the table: what is here, and what fetching costs."""
        total = len(self.rows)
        here = self.count(CURRENT, OLDER, INSTALLED)
        chosen = self.chosen()
        if not total:
            return tr("There are no mods in it.")
        if here:
            said = trn(
                "%(n)s of these %(total)s mods is already installed.",
                "%(n)s of these %(total)s mods are already installed.",
                here, total=number(total),
            )
        else:
            said = trn(
                "This mod is not installed.",
                "None of these %(n)s mods is installed.",
                total,
            )
        sentences = [said]
        if chosen:
            # "The other 15" only when that is what is ticked: every mod that
            # is not here, and none that is.
            others = here and len(chosen) == total - here and not any(row.copies for row in chosen)
            size, room = size_text(self.download_size), size_text(self.unpacked_size)
            if others:
                sentences.append(trn(
                    "Installing the other %(n)s will download %(size)s and use about %(room)s.",
                    "Installing the other %(n)s will download %(size)s and use about %(room)s.",
                    len(chosen), size=size, room=room,
                ))
            else:
                sentences.append(trn(
                    "Installing the %(n)s ticked will download %(size)s and use about %(room)s.",
                    "Installing the %(n)s ticked will download %(size)s and use about %(room)s.",
                    len(chosen), size=size, room=room,
                ))
        elif here == total:
            sentences = [trn(
                "This mod is already installed.",
                "All %(n)s of these mods are already installed.",
                total,
            )]
        else:
            sentences.append(tr("No mod is ticked to install."))
        cannot = self.count(GONE, FLAGGED, UNFOUND)
        if cannot:
            sentences.append(trn(
                "%(n)s cannot be installed from here.", "%(n)s cannot be installed from here.", cannot,
            ))
        return " ".join(sentences)


def build(wanted: List[Wanted], in_folders: List[FolderMod]) -> Plan:
    """The plan for ``wanted`` against what is on disk.

    A mod whose ``summary`` is set is compared with mod.io's current file; one
    without is only said to be here or not.  Where the list remembers a
    version -- a loadout does -- a difference from it is said too, since that
    is the version whatever was built on these mods was built against.
    """
    rows: List[Row] = []
    for one in wanted:
        copies = copies_of(one, in_folders)
        rows.append(_installed_row(one, copies) if copies else _absent_row(one))
    return Plan(rows)


def _absent_row(one: Wanted) -> Row:
    summary = one.summary
    row = Row(wanted=one, status=MISSING)
    if summary is None:
        row.status = UNFOUND
        row.why_not = tr("No mod on mod.io could be matched to it.")
        if one.mod_id:
            row.note = tr("not installed, and not found on mod.io (ModID %s)", one.mod_id)
        else:
            row.note = tr("not installed, and not found on mod.io")
    elif not summary.has_file:
        row.status = GONE
        row.why_not = tr("mod.io has no file for it.")
        row.note = tr("not installed, and mod.io has no file for it")
    elif summary.virus_positive:
        row.status = FLAGGED
        row.why_not = tr("mod.io's scan flagged this file, so the Studio will not download it.")
        row.note = tr("mod.io's scan flagged its file")
    else:
        # A note is a list of facts, each whole, set one after another.
        said = [tr("not installed")]
        if one.version and summary.version and one.version.strip() != summary.version.strip():
            said.append(tr(
                "mod.io has %(version)s, this was built against %(built)s",
                version=summary.version, built=one.version,
            ))
        # A mod the list itself has switched off is not needed to play it.
        row.fetch = one.active
        if not one.active:
            said.append(tr("switched off in the loadout"))
        row.note = "; ".join(said)
    return row


def _installed_row(one: Wanted, copies: List[FolderMod]) -> Row:
    running = copies[0]
    row = Row(wanted=one, status=INSTALLED, copies=copies)
    # Translators: what the table says of a mod that is installed.
    said = [tr("installed (%s)", running.version) if running.version else tr("installed")]
    if one.summary is not None:
        verdict = updates.compare(
            updates.Match(
                origin=one.origin,
                name=one.name,
                mod_id=running.mod_id,
                installed_version=running.version,
                modio_id=one.summary.id,
                installed_file=running.modfile_id if (running.in_cache or running.kind == library.COPY) else 0,
            ),
            one.summary,
        )
        if verdict.status == updates.CURRENT:
            row.status = CURRENT
        elif verdict.status == updates.UPDATE:
            row.status = OLDER
            said.append(tr("mod.io has %s", one.summary.version))
            row.replace, row.why_not = _replaceable(running, one.summary)
        elif verdict.status in (updates.AHEAD, updates.DIFFERENT):
            said.append(tr("mod.io has %s", one.summary.version))
    if row.status != OLDER:
        row.why_not = tr("It is already installed.")
    if one.version and running.version and one.version.strip() != running.version.strip():
        said.append(tr("this was built against %s", one.version))
    alone = [mod for mod in copies if not mod.bundled]
    if len(alone) > 1:
        if any(mod.in_cache for mod in alone) and not all(mod.in_cache for mod in alone):
            said.append(tr("installed twice: by hand, and by the game's own mod browser"))
        else:
            said.append(trn("installed %(n)s times", "installed %(n)s times", len(alone)))
    row.note = "; ".join(said)
    return row


def _replaceable(running: FolderMod, summary: modio.ModSummary):
    """``(folder to replace, why not)`` for an installed mod mod.io has newer."""
    if not summary.has_file:
        return None, tr("mod.io has no file for it.")
    if summary.virus_positive:
        return None, tr("mod.io's scan flagged this file, so the Studio will not download it.")
    if running.in_cache:
        return None, tr("The game's own mod browser installed this copy. Update it there.")
    if running.bundled:
        return None, tr("This mod is bundled inside another. Update the mod it sits in.")
    return running.folder, ""


# -- room for it ------------------------------------------------------------------
def short_of_room(plan: Plan, folder) -> str:
    """Why there is not room for what is ticked, or "" when there is."""
    free = disk.free_space(folder)
    if free is None or plan.room_needed <= free:
        return ""
    return tr(
        "This needs about %(needed)s free and %(drive)s has %(free)s.",
        needed=size_text(plan.room_needed), drive=Path(folder).anchor or folder,
        free=size_text(free),
    )


# -- the queue --------------------------------------------------------------------
class _Saying:
    """A progress line for one mod of a queue, which knows the mod's label
    and how far through the queue's bytes it starts."""

    def __init__(self, label: str, say: Progress, before: int, total: int) -> None:
        self.label, self._say, self._before, self._total = label, say, before, total

    def __call__(self, text: str) -> None:
        self._say(self._before, self._total, text)


@dataclass
class Outcome:
    row: Row
    status: str = NOT_ATTEMPTED
    message: str = ""
    installed: Optional[modzip.InstallResult] = None


def fetch(
    client: modio.Client,
    game_id: int,
    rows: List[Row],
    mods_folder,
    *,
    library_dir,
    game: str = "",
    chunks: modio.Chunks = modio.stream,
    progress: Optional[Progress] = None,
    stop: Callable[[], bool] = lambda: False,
    settled: Optional[Callable[[int, Outcome], None]] = None,
) -> List[Outcome]:
    """Fetch and install ``rows`` one at a time, and say what became of each.

    Each lands in ``library_dir`` -- the Studio's library for this game --
    with a junction into ``mods_folder`` where the folder would have been.

    Never raises for one mod's sake: a failed download, a zip with no mod in
    it, a file mod.io has flagged since the plan was made, are each that
    row's outcome and the queue goes on.  Being asked to stop is not a
    failure either -- whatever landed stays, and the rest come back as not
    attempted.  ``settled`` is told each outcome as it happens.

    The link in the listing is used as it came.  It lasts hours and a queue
    does not; asking mod.io again for every mod would spend a request apiece
    against sixty a minute to learn nothing.  Only a download that fails is
    asked about again, once, in case it was the link.
    """
    say = progress or (lambda done, total, text: None)
    mods_folder = Path(mods_folder)
    outcomes = [Outcome(row) for row in rows]
    total = sum(row.size for row in rows)
    before = 0
    scratch = mods_folder / DOWNLOADS
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        for position, outcome in enumerate(outcomes):
            if stop():
                break
            row = outcome.row
            label = tr(
                "%(name)s (%(at)s of %(total)s)",
                name=row.name, at=number(position + 1), total=number(len(rows)),
            ) if len(rows) > 1 else row.name
            saying = _Saying(label, say, before, total)
            try:
                _fetch_one(
                    client, game_id, outcome, mods_folder, library_dir, game, scratch, chunks, stop,
                    lambda done, _size, label=label, before=before: say(
                        before + done, total, tr("Downloading %s…", label)
                    ),
                    saying,
                )
            except modio.Cancelled:
                outcome.status, outcome.message = NOT_ATTEMPTED, ""
                break
            except (modio.ModioError, modzip.InstallError, OSError) as err:
                outcome.status, outcome.message = FAILED, str(err)
            before += row.size
            if settled is not None:
                settled(position, outcome)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return outcomes


def _fetch_one(client, game_id, outcome, mods_folder, library_dir, game, scratch, chunks, stop, downloading, saying) -> None:
    row = outcome.row
    summary = row.wanted.summary
    if summary is None or not summary.has_file:
        outcome.status, outcome.message = SKIPPED, tr("mod.io has no file for it")
        return
    if summary.virus_positive:
        outcome.status, outcome.message = SKIPPED, tr("mod.io's scan flagged its file")
        return
    try:
        summary, outcome.installed = fetch_and_install(
            client, game_id, summary, scratch, library_dir, mods_folder,
            replace=row.replace, name=updates.strip_category(summary.name) or summary.name,
            chunks=chunks, progress=downloading, stop=stop, saying=saying, label=saying.label,
        )
    except Unavailable as gone:
        outcome.status, outcome.message = SKIPPED, str(gone)
        return
    row.wanted.summary = summary
    outcome.status, outcome.message = DONE, unchecked(summary)


class Unavailable(modio.ModioError):
    """mod.io has no file for a mod, or its scan has flagged the one it has."""


def current_file(client, game_id: int, summary: modio.ModSummary) -> modio.ModSummary:
    """What mod.io says of ``summary``'s mod now; :class:`Unavailable` when
    that is no file to fetch."""
    fresh = client.mod(game_id, summary.id)
    if fresh is None or not fresh.has_file:
        raise Unavailable(tr("%s has no file on mod.io any more", summary.name))
    if fresh.virus_positive:
        raise Unavailable(tr("mod.io's scan flagged %s's file", summary.name))
    return fresh


def fetch_and_install(
    client,
    game_id: int,
    summary: modio.ModSummary,
    scratch: Path,
    library_dir,
    mods_folder,
    *,
    replace: Optional[Path] = None,
    name: str = "",
    ask_first: bool = False,
    chunks: modio.Chunks = modio.stream,
    progress=None,
    stop: Callable[[], bool] = lambda: False,
    saying: Optional[Callable[[str], None]] = None,
    label: str = "",
) -> tuple:
    """Download a mod's file, checked against the size and checksum mod.io
    gives for it, and install it into the library with a link into
    ``mods_folder``: ``(the summary it was fetched as, the install result)``.
    One mod.io gives no checksum for is installed unchecked, which the
    caller says (:func:`unchecked`).

    ``ask_first`` asks mod.io for the mod's current file before anything --
    for a dialog acting on a listing that may be hours old.  Either way a
    download that fails is asked about again, once, in case it was the link:
    the link in a listing lasts hours and a queue does not.  Raises
    :class:`Unavailable` when mod.io has no file to give, or has flagged it,
    and ``modio.Cancelled`` when ``stop`` says so.  ``replace``, ``name``: see
    :func:`core.library.install`.  ``label`` names the mod in what
    ``saying`` is told; its name, unless said.
    """
    say = saying or (lambda text: None)
    label = label or summary.name
    if ask_first:
        say(tr("Asking mod.io for %s's current file…", label))
        summary = current_file(client, game_id, summary)
    Path(scratch).mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        archive = Path(scratch) / (modzip.safe_path(summary.filename) or "mod.zip").split("/")[-1]
        try:
            modio.download(
                summary.download_url, archive, chunks=chunks, progress=progress,
                cancelled=stop, md5=summary.md5, size=summary.filesize,
            )
            break
        except modio.DownloadError:
            if attempt == 2:
                raise
            say(tr("Asking mod.io for %s's current file…", label))
            summary = current_file(client, game_id, summary)
    say(tr("Unpacking %s…", label))
    try:
        installed = library.install(
            archive, library_dir, mods_folder, replace=replace, name=name, facts=facts(summary),
        )
    finally:
        archive.unlink(missing_ok=True)
    return summary, installed


def unchecked(summary: modio.ModSummary) -> str:
    """What to say of a mod installed from mod.io whose file came with no
    checksum, so the download could not be checked against it.  It is
    installed all the same -- the link is https, and the size is checked
    where mod.io gives one -- and this is said wherever the install is.  "" for one that was
    checked (security review S7, N5)."""
    if summary.md5:
        return ""
    return tr("mod.io gave no checksum for %s's file, so the download "
              "couldn't be checked.", summary.name)


def facts(summary: modio.ModSummary) -> dict:
    """What a mod was installed as, for the library's registry."""
    return {
        "modio_id": summary.id, "modfile_id": summary.modfile_id,
        "version": summary.version, "name": summary.name,
        "source": library.MODIO,
    }


def describe_outcomes(outcomes: List[Outcome]) -> str:
    """What the queue did, in a sentence that does not call stopping a failure."""
    done = sum(1 for outcome in outcomes if outcome.status == DONE)
    failed = sum(1 for outcome in outcomes if outcome.status == FAILED)
    skipped = sum(1 for outcome in outcomes if outcome.status == SKIPPED)
    left = sum(1 for outcome in outcomes if outcome.status == NOT_ATTEMPTED)
    said = [trn(
        "%(n)s of %(total)s installed.", "%(n)s of %(total)s installed.", done,
        total=number(len(outcomes)),
    )]
    if failed:
        said.append(trn(
            "%(n)s could not be installed. The row shows why.",
            "%(n)s could not be installed. The row shows why.", failed,
        ))
    if skipped:
        said.append(trn("%(n)s skipped.", "%(n)s skipped.", skipped))
    if left:
        said.append(trn(
            "Stopped with %(n)s still on mod.io. It will be offered again next time.",
            "Stopped with %(n)s still on mod.io. They will be offered again next time.",
            left,
        ))
    return " ".join(said)
