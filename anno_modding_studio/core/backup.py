"""Keep a copy of what the Studio is about to overwrite, so it can be undone.

The Studio does not rewrite a mod wholesale.  What it writes back is its own
*reading* of the mod -- narrow ModOps, one per field the mod really changes --
and that reading is checked, but it is a reading.  A mod nobody has tested this
against can come back subtly different: an op the Studio could not narrow, a
value it settled that the mod meant to compute, a search it answered against
today's assets.  Someone who notices their game behaving differently should be
able to put the mod back exactly as it shipped, without going and downloading it
again.

Backing up the whole mods folder to make that possible would be absurd -- the
one this was written against is 2.4 GB, nearly all of it meshes and textures.
But the Studio can only ever overwrite the files it reads from: the ModOp
patches and the files they include, the modinfo beside them, the texts files,
the Lua an export copies in, and (for Anno 117) the mod profile.  In that
same folder those come to **4 MB, 0.17% of it**.  So the copy is of exactly what
is at risk, and it is small enough to keep a run of them.

Each save is its own timestamped folder with a manifest, under the user's
documents rather than inside the mods folder -- a mods folder is inside the game
installation, which a reinstall or a "verify files" will happily empty.

**The first copy of every file is kept for good.**  Saves rotate -- twenty per
mods folder -- but a save holding the oldest copy of any file is never pruned,
so what a file was before the Studio first wrote it survives however many
exports and Plays come after.  That is at most one copy of everything the Studio
can overwrite: a few megabytes.  A save can also be *pinned* outright, as the
conversion's are, so undoing it can always find them.

**A save this build can't read is left alone.**  Each manifest says its
format and the Studio that wrote it.  One of a newer format, or of a shape
no Studio writes, is listed as kept, and neither pruned nor restored: which
of its files are first copies is a question only the Studio that wrote it
can answer, and getting it wrong deletes the one copy of a file as it was
before the Studio ever touched it.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .. import __version__
from . import disk, modfolder, records
from .i18n import tr, trn

MANIFEST = "backup.json"
# The manifest's format.  A build reads, prunes and restores only saves of
# a format up to its own: bump it in the change that gives a manifest a
# meaning an earlier build would misread.
FORMAT = 1

# How many saves to keep per mods folder. They are a few megabytes each, and the
# point of keeping several is being able to go back past the export that broke
# something rather than only past the last one.
KEEP = 20

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _plain(name: str) -> str:
    return _UNSAFE.sub("-", name)[-60:] or "file"


def home() -> Path:
    from . import places

    return places.backups_dir()


def slug(path) -> str:
    """A folder name standing for a mods folder, stable across runs.

    And across versions: every save is found by it, so a change to it would
    leave every earlier save where no Studio looks.  Pinned by
    ``tests/test_contracts.py``."""
    text = str(path).replace("\\", "/").strip("/")
    return _UNSAFE.sub("-", text).strip("-").lower()[-80:] or "mods"


def store_for(mods_folder, into=None) -> Path:
    return Path(into or home()) / slug(mods_folder)


def is_at_risk(relative: Path) -> bool:
    """Whether a file inside a mod folder is one the Studio can overwrite.

    Everything it reads ModOps out of, and the modinfo next to them.  Files
    belonging to a mod bundled inside this one count as well -- they are covered
    twice over when that mod is being exported too, and once when it is not,
    which is the way round that is worth being wrong.

    And a mod's texts files: an export writes the in-game names of the assets
    the mod adds into every language's, and takes its own block out of them
    again where the mod names none (:func:`core.texts.write_names`).
    """
    from . import modops, texts

    name = relative.name.lower()
    if name in modfolder.MODINFO:
        return True
    if texts.is_texts_file(relative):
        return True
    return modops.is_asset_patch(relative)


def at_risk(mod_folder) -> List[Path]:
    """The files inside one mod folder the Studio could overwrite, relative."""
    mod_folder = Path(mod_folder)
    if not mod_folder.is_dir():
        return []
    found = []
    for path in sorted(mod_folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(mod_folder)
        if is_at_risk(relative):
            found.append(relative)
    return found


@dataclass
class Kept:
    """One save: what was copied, from where, and when."""

    root: Path
    when: float = 0.0
    label: str = ""
    mods_folder: str = ""
    game: str = ""
    # {"mod": folder name ("" for a file in the mods folder itself),
    #  "path": path relative to that, "existed": whether there was one}
    files: List[dict] = field(default_factory=list)
    # Never pruned: a save something else depends on (see core.convert).
    pinned: bool = False
    # Kept because it holds the first copy of some file; worked out by
    # saves(), not stored.
    first: bool = False
    # The format its manifest says, and the Studio that wrote it ("" where
    # it does not say).
    format: int = FORMAT
    written_by: str = ""
    # Why this build can't use it, "" where it can: its manifest is of a
    # newer format, or of a shape no Studio writes.  Such a save is kept for
    # good, and not restored.
    refusal: str = ""

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.when))

    @property
    def mods(self) -> List[str]:
        seen = []
        for entry in self.files:
            if entry.get("mod") and entry["mod"] not in seen:
                seen.append(entry["mod"])
        return seen

    @property
    def bytes(self) -> int:
        return sum(
            path.stat().st_size for path in self.root.rglob("*") if path.is_file()
        )

    @property
    def kept_for_good(self) -> bool:
        return self.pinned or self.first or bool(self.refusal)

    def describe(self) -> str:
        if self.refusal:
            return tr("%(stamp)s: kept as it is, since this version of the "
                      "Studio can't read it", stamp=self.stamp)
        # The label is kept in English, as a key, and translated here.
        what = tr(
            "%(files)s from %(mods)s",
            files=trn("%(n)s file", "%(n)s files", len(self.files)),
            mods=trn("%(n)s mod", "%(n)s mods", len(self.mods)),
        )
        if self.label and self.kept_for_good:
            return tr("%(stamp)s: %(what)s, %(label)s (kept for good)",
                      stamp=self.stamp, what=what, label=tr(self.label))
        if self.label:
            return tr("%(stamp)s: %(what)s, %(label)s",
                      stamp=self.stamp, what=what, label=tr(self.label))
        if self.kept_for_good:
            return tr("%(stamp)s: %(what)s (kept for good)", stamp=self.stamp, what=what)
        return tr("%(stamp)s: %(what)s", stamp=self.stamp, what=what)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "written_by": __version__,
            "when": self.when,
            "label": self.label,
            "mods_folder": self.mods_folder,
            "game": self.game,
            "files": self.files,
            "pinned": self.pinned,
        }


def _where(kept: Kept, entry: dict) -> Tuple[Path, Path]:
    """``(the copy, where it came from)`` for one recorded file."""
    if entry.get("at") and not entry.get("mod"):
        return kept.root / entry["copy"], Path(entry["at"])
    inside = Path(entry["mod"]) / entry["path"] if entry.get("mod") else Path(entry["path"])
    return kept.root / entry["copy"], Path(kept.mods_folder) / inside


# What the Studio writes outside a mods folder, and so the only files a save
# may put back there: Anno 117's pointer and options file, in a mods folder,
# and its profiles, in that folder's ``profiles``.
_OUTSIDE = (".txt", ".jsonc")


def _may_write(kept: Kept, entry: dict, target: Path) -> bool:
    """Whether restoring may write (or remove) ``target``: inside the mods
    folder the save came from, or -- for a file the save recorded as living
    elsewhere -- one of the few kinds the Studio writes there, where it
    writes them, by the name it was saved under.  The manifest is a file on
    disk like any other, so this is checked, not assumed."""
    if _under(kept.mods_folder, target):
        return True
    if not entry.get("at") or entry.get("mod"):
        return False
    try:
        real = Path(target).resolve()
    except OSError:
        return False
    parents = [part.lower() for part in real.parent.parts[-2:]]
    in_mods = parents[-1:] == ["mods"] or parents == ["mods", "profiles"]
    return in_mods and real.suffix.lower() in _OUTSIDE and real.name == Path(entry.get("path", "")).name


def _under(root, path) -> bool:
    """Whether ``path`` really is inside ``root``, once both are resolved.

    Joining a path in pathlib does not contain it: an absolute right-hand side
    replaces the left outright, and ``..`` walks back out. Both shapes read as
    ordinary strings in the manifest, which is a file on disk like any other --
    so the promise restore() makes about staying inside the mods folder has to
    be checked rather than assumed.
    """
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def take(mods_folder, mods: Iterable[str], into=None, label: str = "",
         game: str = "", extra: Iterable = (), writing: Iterable = (),
         pinned: bool = False, creating: Iterable = ()) -> Optional[Kept]:
    """Copy everything the Studio could overwrite in ``mods``, before it does.

    ``extra`` is for files outside any mod folder that a write would touch --
    Anno 117's mod profile, which is one text file naming every mod that is on.

    ``writing`` names the paths, relative to each mod folder, the caller is about
    to write: one list for every mod, or a list per mod by its folder name.  The
    ones that are there already are copied with the rest, whatever their name --
    a file of ModOps a patch includes is no ``assets.xml``, and the export
    rewrites it all the same.  The ones that are not there yet are recorded with
    no copy, so that undoing the export *removes* them.  That is not tidiness: a
    mod carrying a ``modinfo.jsonc`` gets a ``modinfo.json`` written beside it,
    and putting the original back while leaving the new one there leaves the mod
    with two, which is a different mod again.

    ``creating`` is the same for files outside any mod folder: each one that
    is not there yet is recorded so that restoring removes it.  ``pinned``
    keeps the save for good.

    Returns None when there was nothing to record.
    """
    mods_folder = Path(mods_folder)
    mods = list(mods)  # walked twice: what is there, and what is about to be
    store = store_for(mods_folder, into)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # Two exports in the same second are perfectly possible -- one mod, then
    # the rest -- and the second must not land on top of the first.
    root = store / stamp
    number = 2
    while root.exists():
        root = store / ("%s-%d" % (stamp, number))
        number += 1
    kept = Kept(
        root=root, when=time.time(), label=label,
        mods_folder=str(mods_folder), game=game, pinned=pinned,
    )

    if isinstance(writing, dict):
        writes_in = {mod: [Path(relative) for relative in writing.get(mod, ())] for mod in mods}
    else:
        common = [Path(relative) for relative in writing]
        writes_in = dict.fromkeys(mods, common)
    wanted: List[Tuple[str, Path, Path]] = []
    for mod in mods:
        folder = mods_folder / mod
        risked = at_risk(folder)
        wanted.extend((mod, relative, folder / relative) for relative in risked)
        seen = {relative.as_posix().lower() for relative in risked}
        for relative in writes_in[mod]:
            source = folder / relative
            if relative.as_posix().lower() in seen or not source.is_file() or not _under(folder, source):
                continue
            seen.add(relative.as_posix().lower())
            wanted.append((mod, relative, source))
    for path in extra:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            wanted.append(("", path.relative_to(mods_folder), path))
        except ValueError:
            # A profile kept under the user's documents rather than beside the
            # mods -- Anno 117's usually is. Its name alone used to be kept,
            # which put it back into the mods folder beside nothing that reads
            # it: where it really lives is written down.
            wanted.append(("", Path(path.name), path))
    coming = []
    for mod in mods:
        for relative in writes_in[mod]:
            if not (mods_folder / mod / relative).exists():
                coming.append({"mod": mod, "path": relative.as_posix(),
                               "copy": "", "existed": False})
    for path in creating:
        path = Path(path)
        if path.exists():
            continue
        try:
            relative = path.relative_to(mods_folder)
            coming.append({"mod": "", "path": relative.as_posix(), "copy": "", "existed": False})
        except ValueError:
            coming.append({"mod": "", "path": path.name, "copy": "", "existed": False, "at": str(path)})
    if not wanted and not coming:
        return None

    root.mkdir(parents=True, exist_ok=True)
    for number, (mod, relative, source) in enumerate(wanted, start=1):
        # Flat, and numbered rather than nested. Mirroring the folders would be
        # tidier to browse and would break: a mod with a sub-mod inside it puts
        # two long names and two copies of data/config/export/main/asset in one
        # path, and Windows gives up at 260 characters -- on real mod names,
        # inside a real backup folder, that is not a hypothetical.
        copy = "%04d-%s" % (number, _plain(source.name))
        try:
            shutil.copy2(source, root / copy)
        except OSError:
            continue
        entry = {"mod": mod, "path": relative.as_posix(), "copy": copy, "existed": True}
        if not mod and not _under(mods_folder, source):
            entry["at"] = str(source)
        kept.files.append(entry)
    kept.files.extend(coming)
    if not kept.files:
        return None
    disk.write_json(root / MANIFEST, kept.to_dict())
    prune(mods_folder, into)
    return kept


def _number(raw) -> float:
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _shaped(files) -> bool:
    """Whether a manifest's ``files`` are entries as a Studio writes them:
    each a dict naming its path, and its copy where it has one, as text."""
    if not isinstance(files, list):
        return False
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return False
        if not all(isinstance(entry.get(key) or "", str) for key in ("mod", "copy", "at")):
            return False
    return True


def read(root) -> Optional[Kept]:
    """The save in ``root``, or None where it has no manifest to read.

    One whose manifest is of a newer format or of a shape no Studio writes
    comes back with its :attr:`Kept.refusal` set and none of its files: it
    is listed and kept, and nothing is worked out from what it holds.
    """
    root = Path(root)
    found = records.read(root / MANIFEST, FORMAT, keyed=False)
    raw = found.data
    if not raw:
        return None
    kept = Kept(
        root=root,
        when=_number(raw.get("when")),
        label=str(raw.get("label") or ""),
        mods_folder=str(raw.get("mods_folder") or ""),
        game=str(raw.get("game") or ""),
        pinned=bool(raw.get("pinned")),
        format=found.format,
        written_by=found.written_by,
        refusal=found.problem,
    )
    if not kept.refusal and not _shaped(raw.get("files") or []):
        kept.refusal = records.unreadable(root / MANIFEST)
    if not kept.refusal:
        kept.files = list(raw.get("files") or [])
    return kept


def _same_file(entry: dict) -> tuple:
    """What two entries share when they are copies of one file: the mod and
    the path in it, or for a file outside the mods folder where it lives.
    Its name alone is not it: two profiles of one name, in two places, were
    one file, and the first copy of the second could be pruned."""
    at = entry.get("at") or ""
    if at and not entry.get("mod"):
        return ("", os.path.normcase(os.path.normpath(at)))
    return (entry.get("mod") or "", entry.get("path") or "")


def saves(mods_folder, into=None) -> List[Kept]:
    """Every save for this mods folder, newest first, each marked ``first``
    when it holds the oldest copy of some file."""
    store = store_for(mods_folder, into)
    if not store.is_dir():
        return []
    found = []
    for path in store.iterdir():
        if not path.is_dir():
            continue
        kept = read(path)
        if kept is not None:
            found.append(kept)
    found.sort(key=lambda kept: kept.when)
    oldest: dict = {}
    for kept in found:
        for entry in kept.files:
            if entry.get("copy") and entry.get("existed", True):
                oldest.setdefault(_same_file(entry), kept)
    for kept in oldest.values():
        kept.first = True
    return found[::-1]


def prune(mods_folder, into=None, keep: int = KEEP) -> int:
    """Drop the oldest saves past ``keep``, never one kept for good -- pinned,
    or holding the first copy of some file.  Returns how many went."""
    found = [kept for kept in saves(mods_folder, into) if not kept.kept_for_good]
    gone = 0
    for kept in found[keep:]:
        try:
            shutil.rmtree(kept.root)
            gone += 1
        except OSError:
            continue
    return gone


def linked_now(kept: Kept) -> List[str]:
    """The save's mods whose folder in the mods folder is a link now: moved
    into the library since it was taken, by the conversion or by adopting
    it.  :func:`restore` leaves those alone."""
    from . import library

    return [mod for mod in kept.mods if library.is_link(Path(kept.mods_folder) / mod)]


def restore(kept: Kept, mods: Optional[Iterable[str]] = None) -> Tuple[int, List[str]]:
    """Put the saved files back.  Returns ``(how many, what went wrong)``.

    Only ever writes over files it has a copy of, and only inside the folder it
    copied them from -- nothing else in the mods folder is touched, which is the
    same promise the export it is undoing makes.  A save this build can't
    read is not restored at all.

    Nor is a mod that has moved into the library since (:func:`linked_now`).
    Its folder there is the mod as it was installed, which the library keeps
    and the Studio does not write into, and its link may stand for a
    generated copy that the next Play builds again over whatever is put
    back.  Undoing the conversion moves the folder back and restores the
    conversion's own saves as it goes (:func:`core.convert.undo`); putting
    the library's mods back makes it a folder again, and restorable here.
    """
    if kept.refusal:
        return 0, [kept.refusal]
    only = set(mods) if mods is not None else None
    put = 0
    failed: List[str] = []
    linked = set(linked_now(kept))
    told = set()
    for entry in kept.files:
        if only is not None and entry.get("mod") not in only:
            continue
        if entry.get("mod") in linked:
            # Said once for the mod, not once for each of its files.
            if entry["mod"] not in told:
                told.add(entry["mod"])
                failed.append(tr(
                    "%s: this mod is in the library now, behind a link, so it "
                    "is left as it is. Undoing the conversion to the library "
                    "moves it back and restores its files.", entry["mod"],
                ))
            continue
        source, target = _where(kept, entry)
        # This writes files and, just below, deletes them. Neither may happen
        # anywhere but the two folders this save is about.
        if not _may_write(kept, entry, target) or (entry.get("copy") and not _under(kept.root, source)):
            failed.append(tr(
                "%s: that is not inside the mods folder this was saved from",
                "/".join(part for part in (entry.get("mod"), entry.get("path", "?")) if part),
            ))
            continue
        if not entry.get("existed", True):
            # The export created this one. Undoing means it should not be there
            # -- a modinfo.json left beside a mod's own modinfo.jsonc is a mod
            # with two, which is neither what it shipped as nor what we wrote.
            try:
                if target.is_file():
                    target.unlink()
                    put += 1
            except OSError as exc:
                failed.append("%s: %s" % (entry["path"], exc))
            continue
        if not source.is_file():
            failed.append(tr("%s: the copy is missing", entry["path"]))
            continue
        if not entry.get("mod") and not entry.get("at") and not target.exists():
            # A save from before where a file outside the mods folder lives
            # was written down: its name alone would put it somewhere nothing
            # reads it. Said, with where the copy is, rather than guessed.
            failed.append(tr(
                "%(path)s: this save does not record where the file lives. "
                "Its copy is %(copy)s",
                path=entry["path"], copy=source,
            ))
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            failed.append("%s: %s" % (entry["path"], exc))
            continue
        put += 1
    return put, failed
