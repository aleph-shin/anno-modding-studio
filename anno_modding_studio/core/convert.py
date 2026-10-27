"""Bringing everything the game loads into the Studio's library, once.

Somebody with years of mods has them in three places: folders of their own
in the game's ``mods`` folder and the one under Documents, the game's own
mod.io cache, and -- for Anno 117 -- Ubisoft's ``.ubi``.  The end state is
that the mods folders hold links into the library and nothing else, so every
switch is the Studio's to set and every Play is certain.  Getting there
without losing a mod, a switch or a setting is this module:

1. **The survey** (read-only): one row per ModID, the copy the game loads
   and what becomes of it, the other copies and what becomes of them.
2. **The snapshot**: what the game loads now, and every switch and setting,
   written down before anything moves -- the "before" the proof is checked
   against.
3. **The move**: folders of the user's own into the library, junctioned back
   under their bare name (off is no link, not a ``-``); the cache *copied*,
   since those folders are the game client's; working copies left where
   they are; duplicates the game does not load set aside, never deleted.
4. **The proof** is Play and the loader's log, elsewhere.

And it can be taken back.  Every move, copy and set-aside is written into the
conversion's record as it happens, and before anything moves the files making
the loadout live will rewrite -- 117's profiles and options, each moved mod's
patches -- are copied into pinned backups.  :func:`undo` reverses all of it by
renaming folders back, no copy of gigabytes involved: the mods folders are as
they were, the library holds none of it, and the Studio can be met as if for
the first time.  For the first real run there is also :func:`full_copy`: the
whole of the folders the conversion moves out of, kept in the backups for good.

Existing profiles -- iModYourAnno's ``.imyaprofile`` lists, 117's
``mods/profiles`` -- are offered as profiles of the loadout: its mods,
switched as the profile says, one pick away in the profile picker.  (A
conversion from before the Studio had profiles made a loadout of each; undoing
one still finds those.)
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Set

from . import backup, disk, library, modfolder, modio_cache, modzip, records, switches
from .games import ANNO_117, ANNO_1800
from .i18n import tr, tr_noop, trn

if TYPE_CHECKING:
    from .installplan import FolderMod

# Where a mod lives now.  The labels are translated where they are shown.
YOURS, CACHE, UBI, LIBRARY, ZIPPED = "yours", "cache", "ubi", "library", "zipped"
WHERE = {
    YOURS: tr_noop("Your folders"),
    CACHE: tr_noop("The game's mod browser"),
    UBI: tr_noop("The game's own"),
    LIBRARY: tr_noop("Already the library's"),
    ZIPPED: tr_noop("Zips in your mods folder"),
}

# What happens to one installed folder.
MOVE = "move"            # into the library, a link where it was
COPY = "copy"            # out of the cache into the library, the cache left
WORKING = "working"      # somebody's working copy: left where it is
SET_ASIDE = "aside"      # a duplicate the game does not load: set aside
LEAVE_CACHE = "cache"    # a cache duplicate: left for unsubscribing
NOTHING = "nothing"      # the library's already, or Ubisoft's
LEFT_ZIPPED = "zip"      # read out of a zip: the zip is left where it is

FATES = {
    MOVE: tr_noop("moved into the library"),
    COPY: tr_noop("copied into the library"),
    WORKING: tr_noop("a working copy (it has .git): left where it is"),
    SET_ASIDE: tr_noop("set aside, not deleted"),
    LEAVE_CACHE: tr_noop("left in the cache, to unsubscribe from"),
    NOTHING: "",
    LEFT_ZIPPED: tr_noop("a zip: left where it is"),
}
# What is said of a kept copy whose fate is NOTHING, by where it is.
STAYS = {
    UBI: tr_noop("Ubisoft's, stays"),
    LIBRARY: tr_noop("already in the library"),
}


@dataclass
class Copy:
    """One installed folder of a mod."""

    installed: FolderMod
    on: bool
    loads: bool
    where: str
    git: bool = False
    fate: str = NOTHING

    @property
    def folder(self) -> Path:
        return Path(self.installed.folder)


@dataclass
class Row:
    """One mod -- one ModID -- however many copies of it are installed."""

    key: str
    name: str
    mod_id: str
    version: str
    kept: Copy
    copies: List[Copy] = field(default_factory=list)
    modio_id: int = 0
    settings: str = ""

    @property
    def on(self) -> bool:
        return self.kept.on

    @property
    def where(self) -> str:
        return self.kept.where

    @property
    def others(self) -> List[Copy]:
        return [copy for copy in self.copies if copy is not self.kept]

    @property
    def to_do(self) -> bool:
        """Whether converting this mod would do anything."""
        return any(copy.fate in (MOVE, COPY, SET_ASIDE) for copy in self.copies)

    def describe(self) -> str:
        """What will happen to it, in a few words."""
        said = [tr(FATES[self.kept.fate] or STAYS[self.kept.where])]
        aside = [copy for copy in self.others if copy.fate == SET_ASIDE]
        cached = [copy for copy in self.others if copy.fate == LEAVE_CACHE]
        if aside:
            said.append(trn("%(n)s other copy set aside", "%(n)s other copies set aside", len(aside)))
        if cached:
            said.append(tr("the cache's copy left to unsubscribe from"))
        return "; ".join(said)


@dataclass
class Survey:
    game: str
    rows: List[Row] = field(default_factory=list)
    # Folders the loader lists and loads nothing from: said, never moved.
    inert: List[str] = field(default_factory=list)

    def of(self, where: str) -> List[Row]:
        return [row for row in self.rows if row.where == where]

    @property
    def to_do(self) -> List[Row]:
        return [row for row in self.rows if row.to_do]


def _where(installed) -> str:
    # What is read out of a zip is the Studio's copy of it; the zip is the mod.
    if getattr(installed, "zipped", False):
        return ZIPPED
    if installed.kind:
        return LIBRARY
    if installed.in_cache:
        return CACHE
    if Path(installed.folder).parent.name.lower() == ".ubi":
        return UBI
    return YOURS


def survey(found, settings: Optional[Dict[str, str]] = None) -> Survey:
    """The survey of ``found`` (a :class:`core.truth.Truth` read from disk).

    ``settings`` is the settings column, by row key, when the caller has
    worked it out (see :func:`settings_of`); left out, it is blank.
    """
    result = Survey(game=found.game, inert=[mod.folder.name for mod in getattr(found, "inert", ())])
    by_key: Dict[str, List[Copy]] = {}
    order: List[str] = []
    for mod in found.mods:
        where = _where(mod.installed)
        copy = Copy(
            installed=mod.installed, on=mod.on, loads=mod.loads, where=where,
            git=where == YOURS and (Path(mod.installed.folder) / ".git").exists(),
        )
        if mod.key not in by_key:
            order.append(mod.key)
        by_key.setdefault(mod.key, []).append(copy)
    for key in order:
        copies = by_key[key]
        kept = next((copy for copy in copies if copy.loads), None) or _best(copies)
        for copy in copies:
            copy.fate = _fate(copy, copy is kept)
        installed = kept.installed
        result.rows.append(Row(
            key=key,
            name=installed.name or installed.folder_name,
            mod_id=installed.mod_id,
            version=installed.version,
            kept=kept,
            copies=copies,
            modio_id=next((copy.installed.modio_id for copy in copies if copy.installed.modio_id), 0),
            settings=(settings or {}).get(key, ""),
        ))
    result.rows.sort(key=lambda row: (list(WHERE).index(row.where), row.name.lower()))
    return result


def _best(copies: List[Copy]) -> Copy:
    """Among copies none of which loads, the one to keep: the library's, then
    one of the user's own, then the cache's."""
    rank = {LIBRARY: 0, YOURS: 1, CACHE: 2, UBI: 3, ZIPPED: 4}
    return sorted(copies, key=lambda copy: rank.get(copy.where, 9))[0]


def _fate(copy: Copy, kept: bool) -> str:
    if copy.where == ZIPPED:
        return LEFT_ZIPPED
    if copy.where in (LIBRARY, UBI):
        return NOTHING
    if copy.where == YOURS and copy.git:
        return WORKING
    if kept:
        return MOVE if copy.where == YOURS else COPY
    return SET_ASIDE if copy.where == YOURS else LEAVE_CACHE


def settings_of(found, game_options: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, str]:
    """The settings column: which mods carry iModYourAnno settings (1800) or
    have options answered in the game's options file (117) -- the things a
    player most fears losing, said so they can see they travel."""
    column: Dict[str, str] = {}
    for mod in found.mods:
        if found.game == ANNO_117:
            answers = (game_options or {}).get((mod.installed.mod_id or "").lower())
            if answers:
                column[mod.key] = tr("options set")
        elif _offers_tweaks(Path(mod.installed.folder)):
            column[mod.key] = "iModYourAnno"
    return column


def _offers_tweaks(folder: Path) -> bool:
    """Whether a mod's folder has iModYourAnno knobs -- a byte search, not a
    read: the survey asks it of every mod at once."""
    try:
        for path in folder.rglob("*.xml"):
            try:
                if b"<ImyaExpose" in path.read_bytes():
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


# -- before anything -------------------------------------------------------------------
def other_managers(game: str, install: Optional[Path]) -> List[str]:
    """Other tools whose traces are here, each with how the two work side by
    side once mods are in the library.  Said once, as a fact about the files
    both write: which tool to use is the reader's to decide (see "Other
    tools" in docs/standards/design-standards.md)."""
    found = []
    if game == ANNO_1800 and install is not None and (Path(install) / ".imya").is_dir():
        found.append(tr(
            "iModYourAnno is installed. It switches a mod by renaming its "
            "folder, and the Studio switches a converted mod by its link. The "
            "fourth card shows when the mods folder has changed since Play. "
            "iModYourAnno's profiles are offered below as profiles of this "
            "loadout."
        ))
    roaming = os.environ.get("APPDATA", "")
    if game == ANNO_117 and roaming and (Path(roaming) / "Anno 117 Mod Manager").is_dir():
        found.append(tr(
            "Taludas' Anno 117 Mod Manager is installed. It and Play write "
            "the same profile and options files, so the game loads what the "
            "last one to write set. The fourth card shows when the mods "
            "folder has changed since Play."
        ))
    return found


def space_needed(rows: Iterable[Row], library_dir: Path) -> int:
    """Bytes the library's drive needs for these rows: every copy out of the
    cache, and every move from another drive -- a move on one drive is a
    rename and needs nothing."""
    need = 0
    for row in rows:
        for copy in row.copies:
            if copy.fate == COPY or (copy.fate == MOVE and not same_drive(copy.folder, library_dir)):
                need += disk.folder_size(copy.folder)
    return need


def same_drive(one: Path, other: Path) -> bool:
    def drive(path: Path) -> str:
        path = Path(path)
        while not path.exists() and path.parent != path:
            path = path.parent
        try:
            return os.path.splitdrive(str(path.resolve()))[0].lower()
        except OSError:
            return os.path.splitdrive(str(path))[0].lower()

    return drive(one) == drive(other)


# -- the snapshot ----------------------------------------------------------------------
# The format of a conversion's record, the snapshot with its journal, which
# is stamped with it (see :mod:`core.records`).  Bump it in the change that
# gives the record a meaning an earlier build would misread, a step of a new
# kind among them: that build then neither writes it nor undoes it.
JOURNAL_FORMAT = 1
# The kinds of step a journal holds, and so the only ones :func:`undo` takes
# back: anything else is a newer Studio's, and is not guessed at.
STEPS = ("move", "copy", "aside")


def snapshot(found, game_options=None) -> dict:
    """What the game loads now, and every switch and setting, as a record."""
    return {
        "when": time.time(),
        "game": found.game,
        "loading": sorted(mod.key for mod in found.loading),
        "names": {mod.key: mod.name for mod in found.mods},
        "on": {mod.key: bool(mod.on) for mod in found.mods},
        "options": dict(game_options or {}),
    }


def save_snapshot(record: dict, library_dir: Path) -> Path:
    """Kept in the library, beside what it describes."""
    folder = Path(library_dir) / "conversions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%s.json" % time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(record.get("when"))))
    write_record(path, record)
    return path


def compare(record: dict, found) -> tuple:
    """``(the same, loads now and did not, loaded before and does not)`` --
    names -- between the snapshot and what the game loads now."""
    before = set(record.get("loading") or [])
    now = {mod.key for mod in found.loading}
    names = dict(record.get("names") or {})
    names.update({mod.key: mod.name for mod in found.mods})
    return (
        sorted(names.get(key, key) for key in before & now),
        sorted(names.get(key, key) for key in now - before),
        sorted(names.get(key, key) for key in before - now),
    )


# -- existing profiles, as loadouts -------------------------------------------------------
@dataclass
class FoundProfile:
    name: str
    source: str               # "iModYourAnno" or "Anno 117"
    path: Path
    on: Set[str] = field(default_factory=set)   # lower-case folder names or ModIDs
    by_mod_id: bool = False
    # 117: whether a mod the profile does not list is on.
    unlisted_on: bool = False
    # Whether the loadout being converted has a profile of this name already,
    # set by whoever has the loadout: offered all the same, and not ticked.
    here: bool = False


def profiles(game: str, install: Optional[Path], mods_folder: Optional[Path]) -> List[FoundProfile]:
    """Every profile another tool or the game keeps, that could be a loadout."""
    found: List[FoundProfile] = []
    if game == ANNO_1800 and install is not None:
        folder = Path(install) / ".imya" / "profiles"
        for path in sorted(folder.glob("*.imyaprofile")) if folder.is_dir() else []:
            try:
                names = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
            except OSError:
                continue
            found.append(FoundProfile(
                name=path.stem, source="iModYourAnno", path=path,
                on={modfolder.bare_name(name).lower() for name in names if name},
            ))
    if game == ANNO_117:
        seen: Set[str] = set()
        for base in (mods_folder, switches.documents_mods(game)):
            if not base:
                continue
            folder = Path(base) / switches.PROFILE_DIR
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.txt")):
                if (path.name.lower().endswith(".bak.txt") or switches.is_studios(path)
                        or switches.named_as_studios(path) or str(path).lower() in seen):
                    continue
                seen.add(str(path).lower())
                try:
                    read = switches.read_profile(path)
                except OSError:
                    continue
                found.append(FoundProfile(
                    name=path.stem, source="Anno 117", path=path, by_mod_id=True,
                    on={mod_id for mod_id, on in read.on.items() if on},
                    unlisted_on=read.new_mods_on,
                ))
    return found


def profile_switches(loadout, found: FoundProfile):
    """Which of ``loadout``'s mods ``found`` has on, by origin, without
    touching them; and the names it lists that match no mod here.  A mod
    bundled inside another switches with its host, and keeps the switch it
    has where it has no host here."""
    matched: Set[str] = set()
    listed_117: Set[str] = set()
    if found.by_mod_id:
        try:
            listed_117 = set(switches.read_profile(found.path).on)
        except OSError:
            listed_117 = set()
    switched: Dict[str, bool] = {}
    bundled = []
    for layer in loadout.layers:
        if modfolder.is_bundled(layer.origin):
            bundled.append(layer)
            continue
        if found.by_mod_id:
            key = (layer.mod_id or "").strip().lower()
            on = key in found.on if key in listed_117 else found.unlisted_on
        else:
            key = modfolder.bare_name(modfolder.folder_name(layer.origin)).lower()
            on = key in found.on
        switched[layer.origin] = on
        if on and key in found.on:
            matched.add(key)
    for layer in bundled:
        switched[layer.origin] = switched.get(modfolder.host_of(layer.origin), layer.active)
    return switched, sorted(found.on - matched)


# -- the move ------------------------------------------------------------------------------
@dataclass
class Converted:
    moved: List[str] = field(default_factory=list)
    copied: List[str] = field(default_factory=list)
    set_aside: List[tuple] = field(default_factory=list)   # (name, where it went)
    left: List[str] = field(default_factory=list)          # working copies
    failed: List[tuple] = field(default_factory=list)      # (name, why)
    # Where each folder moved or copied is now reached from: old path -> the
    # link's, so a loadout holding the old path can follow it.
    paths: Dict[str, str] = field(default_factory=dict)
    # Every step, in the order it was done, for undo():
    # {"op": "move" | "copy" | "aside", "from": ..., "to": ..., "link": ...}
    steps: List[dict] = field(default_factory=list)

    def describe(self) -> str:
        bits = []
        if self.moved:
            bits.append(trn(
                "moved %(n)s into the library", "moved %(n)s into the library", len(self.moved),
            ))
        if self.copied:
            bits.append(trn(
                "copied %(n)s out of the game's mod browser",
                "copied %(n)s out of the game's mod browser", len(self.copied),
            ))
        if self.set_aside:
            bits.append(trn(
                "set %(n)s duplicate aside", "set %(n)s duplicates aside", len(self.set_aside),
            ))
        if self.left:
            bits.append(trn(
                "left %(n)s working copy where it is", "left %(n)s working copies where they are",
                len(self.left),
            ))
        if self.failed:
            bits.append(trn("%(n)s could not be done", "%(n)s could not be done", len(self.failed)))
        # After a colon: a list of what was done, not a sentence of it.
        return tr("Converted: %s.", ", ".join(bits)) if bits else tr("No mods to convert.")


def convert(rows: Iterable[Row], library_dir: Path, mods_folder: Path, game: str) -> Converted:
    """Do what the survey says for ``rows``: each mod's duplicates set aside
    first, then the copy that loads.

    That order is not tidiness.  A cache copy that loads is linked under its
    own folder name, and the user's older copy of the same mod often has
    exactly that name: set aside after the link was made, "the duplicate at
    that path" was the link, and the link went into the backups -- leaving
    the mod loading from the cache, where the Studio cannot switch it.  (A
    rehearsal on a real Anno 1800 folder found three.)"""
    library_dir, mods_folder = Path(library_dir), Path(mods_folder)
    done = Converted()
    records = {record.mod_id: record for record in modio_cache.installed(game)}
    for row in rows:
        others = [copy for copy in row.others if copy.fate == SET_ASIDE]
        for copy in [*others, row.kept, *(copy for copy in row.others if copy not in others)]:
            try:
                if copy.fate == MOVE:
                    linked = library.adopt(library_dir, copy.folder)
                    done.moved.append(row.name)
                    done.paths[str(copy.folder)] = str(linked.link)
                    done.steps.append({"op": "move", "from": str(copy.folder),
                                       "to": str(linked.target), "link": str(linked.link)})
                    _note_displaced(done, linked)
                elif copy.fate == COPY:
                    linked = _copy_in(copy, row, library_dir, mods_folder, records)
                    done.paths[str(copy.folder)] = str(linked.link)
                    done.copied.append(row.name)
                    done.steps.append({"op": "copy", "from": str(copy.folder),
                                       "to": str(linked.target), "link": str(linked.link)})
                    _note_displaced(done, linked)
                elif copy.fate == SET_ASIDE:
                    if library.is_link(copy.folder):
                        continue  # never the library's own link
                    aside = modzip.set_aside(copy.folder)
                    done.set_aside.append((row.name, aside))
                    done.steps.append({"op": "aside", "from": str(copy.folder), "to": str(aside)})
                elif copy.fate == WORKING:
                    done.left.append(row.name)
            except OSError as exc:
                done.failed.append((row.name, str(exc)))
    return done


def _note_displaced(done: Converted, linked) -> None:
    """A real folder that was in the way of a link was set aside by it."""
    if linked.set_aside is not None:
        done.steps.append({"op": "aside", "from": str(linked.link), "to": str(linked.set_aside)})


def _copy_in(copy: Copy, row: Row, library_dir: Path, mods_folder: Path, records):
    """Out of the game client's cache into the library, the cache untouched:
    those folders are the client's, and it keeps its own list of them."""
    name = copy.installed.folder_name
    if name.isdigit():
        # A mod whose files sit in the cache's numbered folder itself: linked
        # in under its own name, not as a number.
        from . import generator

        name = generator.safe_folder_name(copy.installed.name or row.name, name)
    target = library_dir / name
    counter = 2
    while target.exists():
        target = library_dir / ("%s (%d)" % (name, counter))
        counter += 1
    library_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(copy.folder), str(target))
    if disk.folder_size(target) != disk.folder_size(copy.folder):
        shutil.rmtree(str(target), ignore_errors=True)
        raise OSError(tr("the copy did not come out the same size as the cache's"))
    linked = library.link(target, mods_folder, name)
    library.note_link(library_dir, linked, library.COPY)
    modio_id = row.modio_id
    record = records.get(modio_id)
    library.record(
        library_dir, target.name, modio_id=modio_id,
        modfile_id=record.modfile_id if record is not None else 0,
        version=(record.version if record is not None else "") or row.version,
        name=(record.name if record is not None else "") or row.name,
        source=library.CACHE,
    )
    return linked


# -- before the move: what undoing needs ------------------------------------------------
# A backup's label: kept in English on disk, translated where it is shown.
BEFORE = tr_noop("before converting")


def pin_before(game: str, mods_folder: Path, rows: Iterable[Row], into=None) -> List[str]:
    """Pinned backups of everything the conversion, and making its loadout
    live, may rewrite -- so :func:`undo` can put it back.  Their folders.

    Each moved mod's patches and modinfo (iModYourAnno writes into those);
    for Anno 117 the pointer file, the profile it names and the Studio's own
    profile, which Play is about to write, and the options file.
    """
    roots: List[str] = []
    moving: Dict[Path, List[str]] = {}
    for row in rows:
        for copy in row.copies:
            if copy.fate == MOVE:
                moving.setdefault(copy.folder.parent, []).append(copy.folder.name)
    for parent, names in moving.items():
        kept = backup.take(parent, names, into=into, label=BEFORE, game=game, pinned=True)
        if kept is not None:
            roots.append(str(kept.root))
    if game == ANNO_117:
        pointer = switches.pointer_folder(Path(mods_folder), game)
        if pointer is not None:
            pointer = Path(pointer)
            live = switches.active_profile(pointer)
            there = [path for path in (pointer / switches.POINTER_FILE, live) if path is not None and path.is_file()]
            kept = backup.take(
                pointer, [], into=into, label=BEFORE, game=game, pinned=True, extra=there,
                creating=[pointer / switches.POINTER_FILE,
                          pointer / switches.PROFILE_DIR / (switches.STUDIO_PROFILE + ".txt")],
            )
            if kept is not None:
                roots.append(str(kept.root))
        options = switches.options_file(game)
        if options is not None:
            kept = backup.take(
                options.parent, [], into=into, label=BEFORE, game=game, pinned=True,
                extra=[options] if options.is_file() else [], creating=[options],
            )
            if kept is not None:
                roots.append(str(kept.root))
    return roots


def write_record(path: Path, record: dict) -> None:
    """A conversion's record written whole, unless the one there may not be
    written over (:func:`core.records.write`)."""
    records.write(path, record, JOURNAL_FORMAT, keyed=False)


def read_record(path: Path) -> records.Record:
    """A conversion's record as read, and whether it may be written back."""
    return records.read(path, JOURNAL_FORMAT, keyed=False)


def cannot_undo(found: records.Record) -> str:
    """Why the conversion ``found`` records can't be taken back by this
    build, or "" where it can.

    Asked of the whole journal before any of it is undone.  A step of a kind
    this build doesn't know was undone as a folder moved back, and a step
    missing what it moved stopped the undo half way.
    """
    if found.problem:
        return found.problem
    journal = found.data.get("journal")
    steps = journal.get("steps") if isinstance(journal, dict) else None
    if not isinstance(steps, list):
        return records.unreadable(found.path)
    for step in steps:
        if not isinstance(step, dict):
            return records.unreadable(found.path)
        if step.get("op") not in STEPS:
            return tr(
                "This conversion's record has a step this version of the "
                "Studio doesn't know how to undo (%(step)s), so nothing was "
                "undone. Undo it with the version that converted the folder, "
                "or a later one.", step=str(step.get("op") or "?"),
            )
        ends = (step.get("from"), step.get("to"))
        if not all(isinstance(end, str) and end for end in ends) or not isinstance(
                step.get("link") or "", str):
            return records.unreadable(found.path)
    return ""


def moved_folders(library_dir: Path, loadout) -> Dict[str, str]:
    """Where the conversions not undone moved the folders of ``loadout``'s
    mods: the folder a layer was read from, to its link now -- every record
    that names this loadout, oldest first, one after another."""
    folder = Path(library_dir) / "conversions"
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return {}
    moved: Dict[str, str] = {}
    for path in paths:
        record = read_record(path).data
        if not record or record.get("undone"):
            continue
        journal = record.get("journal") or {}
        if not journal.get("loadout") or not disk.same_file(journal["loadout"], loadout):
            continue
        for old, new in (journal.get("paths") or {}).items():
            for before, after in list(moved.items()):
                if after == old:
                    moved[before] = new
            moved.setdefault(old, new)
    return {old: new for old, new in moved.items() if old != new}


def last_undoable(library_dir: Path) -> Optional[Path]:
    """The newest conversion record that can still be undone, or None."""
    folder = Path(library_dir) / "conversions"
    try:
        paths = sorted(folder.glob("*.json"), reverse=True)
    except OSError:
        return None
    for path in paths:
        # One a newer Studio wrote is found all the same, and undoing it is
        # refused: passing it over would undo an older one under it.
        record = read_record(path).data
        if record.get("journal") and not record.get("undone"):
            return path
    return None


@dataclass
class Undone:
    put_back: int = 0          # folders moved back where they were
    removed: int = 0           # the library's copies of the game browser's mods
    restored: int = 0          # files put back from the pinned backups
    failed: List[tuple] = field(default_factory=list)

    def describe(self) -> str:
        bits = []
        if self.put_back:
            bits.append(trn(
                "%(n)s folder back where it was", "%(n)s folders back where they were", self.put_back,
            ))
        if self.removed:
            bits.append(trn(
                "%(n)s copy of the game's mod browser's mods taken out of the library",
                "%(n)s copies of the game's mod browser's mods taken out of the library",
                self.removed,
            ))
        if self.restored:
            bits.append(trn(
                "%(n)s file put back as it was", "%(n)s files put back as they were", self.restored,
            ))
        said = tr("Undone: %s.", "; ".join(bits)) if bits else tr("No "
                                                                  "conversion "
                                                                  "needed "
                                                                  "undoing.")
        if self.failed:
            said = " ".join([said, trn(
                "%(n)s step could not be undone: %(steps)s.",
                "%(n)s steps could not be undone: %(steps)s.",
                len(self.failed), steps="; ".join("%s (%s)" % pair for pair in self.failed[:6]),
            )])
        return said


def undo(record_path: Path, library_dir: Path) -> Undone:
    """Take a conversion back, from its record.

    The links it made go first, so every name in the mods folders is free;
    then its steps in reverse -- the library's copies of cache mods deleted
    (they were copies; the cache never lost them), every moved or set-aside
    folder moved back to exactly where and as what it was, ``-`` and all --
    then the pinned backups restored.  A folder whose old place has since
    been taken is left where it is and said, never written over.

    Raises :class:`core.records.RecordError`, undoing nothing, where
    :func:`cannot_undo` says why it can't be done.
    """
    record_path, library_dir = Path(record_path), Path(library_dir)
    found = read_record(record_path)
    why = cannot_undo(found)
    if why:
        raise records.RecordError(why)
    record = found.data
    journal = record.get("journal") or {}
    steps = list(journal.get("steps") or [])
    done = Undone()
    held = library.links_to_change(library_dir)
    for step in steps:
        link = Path(step["link"]) if step.get("link") else None
        if link is None:
            continue
        target = library.source_of(link, library_dir) if library.is_link(link) else None
        if target is not None and disk.same_file(target, step.get("to")):
            try:
                library.remove_link(link)
            except OSError as exc:
                done.failed.append((link.name, str(exc)))
                continue
        held.pop(link.name, None)
    library.write_links(library_dir, held)
    for step in reversed(steps):
        source, back = Path(step["to"]), Path(step["from"])
        try:
            if step["op"] == "copy":
                if source.is_dir() and library.holds(source, library_dir):
                    shutil.rmtree(str(source))
                library.forget(library_dir, source.name)
                done.removed += 1
                continue
            if back.exists() or library.is_link(back):
                done.failed.append((back.name, tr("something is at its old place now")))
                continue
            if not source.is_dir():
                done.failed.append((back.name, tr("it is no longer at %s", source)))
                continue
            back.parent.mkdir(parents=True, exist_ok=True)
            library.move_whole(source, back)
            if step["op"] == "move":
                library.forget(library_dir, source.name)
            done.put_back += 1
        except OSError as exc:
            done.failed.append((back.name, str(exc)))
    # A mod that could not go back is not written into: whatever stands at its
    # old place now is somebody else's.
    blocked = {name for name, _why in done.failed}
    for root in journal.get("backups") or []:
        kept = backup.read(root)
        if kept is None:
            done.failed.append((Path(root).name, tr("the backup is missing")))
            continue
        put, failed = backup.restore(kept, mods={entry.get("mod") or "" for entry in kept.files} - blocked)
        done.restored += put
        # Translators: stands where a mod's name would, for a step that was a backup's.
        done.failed.extend((tr("backup"), why) for why in failed)
    record["undone"] = time.time()
    write_record(record_path, record)
    return done


# -- a full copy, for the first real run ---------------------------------------------------
FULL = "full-copies"


def folders_to_copy(rows: Iterable[Row]) -> List[Path]:
    """The mods folders the conversion moves anything out of."""
    found: List[Path] = []
    for row in rows:
        for copy in row.copies:
            if copy.fate in (MOVE, SET_ASIDE) and copy.folder.parent not in found:
                found.append(copy.folder.parent)
    return found


def _files_to_copy(folders: Iterable[Path]):
    """``(folder, file)`` for every file under ``folders``, not following the
    library's links: what they point at is the library's, not the folder's."""
    for folder in folders:
        for top, dirs, files in os.walk(folder):
            dirs[:] = [name for name in dirs if not library.is_link(Path(top) / name)]
            for name in files:
                yield Path(folder), Path(top) / name


def size_of(folders: Iterable[Path]) -> int:
    total = 0
    for _folder, path in _files_to_copy(folders):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _long(path: Path) -> str:
    """``path`` in the form Windows lets past 260 characters."""
    text = str(Path(path).absolute())
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


class Stopped(Exception):
    """Whoever asked for the copy stopped it; nothing is left of it."""


# The format of a full copy's note, which nothing reads but a person.
FULL_COPY_FORMAT = 1
# Compressed, and lightly: a real Anno 1800 mods folder zips from 2.4 GB to
# about 1 GB, and deflate's fastest setting gets 54% where its default gets
# 52% in two thirds more time -- time spent watching a bar before converting.
FULL_LEVEL = 1


def full_copy(folders: Iterable[Path], into: Optional[Path] = None, progress=None, stopped=None) -> Path:
    """The whole of ``folders``, zipped into the backups for good.  Where.

    One zip per folder, with a note of where each came from, kept apart from
    the rotating saves.  Putting one back is extracting it, by hand, which is
    the point of a copy nobody's code has to be right for.  The files go in
    as they are -- models and textures, mostly, which is why it is a zip and
    not a folder: 2.4 GB of them take about 1 GB.
    """
    import zipfile

    from . import places

    folders = [Path(folder) for folder in folders]
    home = Path(into) if into is not None else places.backups_dir() / FULL
    root = home / time.strftime("%Y-%m-%d_%H-%M-%S")
    counter = 2
    while root.exists():
        root = home / ("%s (%d)" % (time.strftime("%Y-%m-%d_%H-%M-%S"), counter))
        counter += 1
    total = size_of(folders)
    copied = 0
    made = {folder: root / ("%d-%s.zip" % (number, folder.name or "mods"))
            for number, folder in enumerate(folders, 1)}
    root.mkdir(parents=True, exist_ok=True)
    try:
        for folder in folders:
            with zipfile.ZipFile(_long(made[folder]), "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=FULL_LEVEL, allowZip64=True) as archive:
                for _folder, path in _files_to_copy([folder]):
                    if stopped is not None and stopped():
                        raise Stopped()
                    relative = path.relative_to(folder)
                    entry = zipfile.ZipInfo.from_file(_long(path), relative.as_posix())
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    with open(_long(path), "rb") as source, archive.open(entry, "w", force_zip64=True) as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
                    copied += entry.file_size
                    if progress is not None:
                        progress(copied, total, relative.parts[0])
    except BaseException:
        shutil.rmtree(_long(root), ignore_errors=True)
        raise
    records.write(root / "full-copy.json", {
        "when": time.time(), "bytes": copied,
        "folders": [{"source": str(folder), "copy": str(made[folder]),
                     "zipped": made[folder].stat().st_size} for folder in folders],
    }, FULL_COPY_FORMAT, keyed=False)
    return root
