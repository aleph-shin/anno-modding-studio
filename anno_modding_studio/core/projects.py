"""Projects: the file each mod of yours is kept in, apart from the loadouts.

A loadout is what the Studio opens: which mods, in which order, which of them
are on, and what each comes to.  A mod of yours -- the loadout's own mod, a
patch, a mod of yours read back in to carry on with -- is also kept in a file
of its own, its **project**, which every loadout that has the mod refers to
(``docs/proposals/projects-and-loadouts-proposal.md``, section 4).  The
project holds the mod as written: its details, its ModOps, its history
(its work at each checkpoint, :class:`~.changes.Checkpoint`), and the facts
about it that do not depend on which loadout it is in -- its builds, its
mod.io page, its last publish.  The loadout keeps what it works out: every
change, read off those ModOps in its own load order.

The window still holds one :class:`~.changes.Loadout` with everything in it.
A project is how a mod of yours is kept on the disk, not a second object the
rest of the Studio passes around: saving takes each project's part out of
what the loadout would write (:func:`split`), and opening puts it back
(:func:`join`) before the loadout is read.  So nothing that reads a loadout
has to learn where each part came from.  Which part is a project's is
declared on the loadout's fields themselves (:mod:`.owners`).

What acts on a project with no loadout open -- the cache, taking one up into
a loadout, a restore bringing back a mod no loadout has, a rename written
straight into its file, and *Add to loadout…* to come -- reads and writes it
as a :class:`Project`, without a loadout at all.

A project's file is named after its mod (:func:`new_path`) and follows it
when the mod is renamed: the save that writes the new name moves the file
(:func:`path_for`), and points every loadout that refers to it at where it
went, in the same save (:func:`refer`).

Each project counts its saves (``revision``), and a loadout remembers which
revision its readings were made from (:attr:`Ref.read_at`).  A project
another loadout has changed since is not written from this one, which would
put this loadout's older reading back over that work: it is held
(:attr:`Ref.held`) until it is read again.

Works on the files' JSON only, so that :mod:`.changes` can use it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .. import __version__
from . import disk, games, modfolder, owners, places, records, saving
from .i18n import tr

PROJECT_SUFFIX = ".annoproj"
# What a project file says it is, so one renamed by hand is still told for
# what it is (section 12).
KIND = "project"
# 2: the loadout's own mod's details are named as a layer's are, its modinfo
# blocks inside ``declared`` (docs/proposals/own-mod-layer-proposal.md,
# phase 5).
PROJECT_FORMAT = 2
# The oldest format this build reads: 0.1.0's, the first release's.  A
# project of an older one is a test build's, which no release has to read,
# and is refused rather than read by shape.
OLDEST_PROJECT_FORMAT = 2


# What a project keeps of its mod's record is what the record's fields say
# their project keeps (:mod:`.owners`): read off the schema, which
# :mod:`.changes` declares, each time it is asked for.
def layer_details() -> tuple:
    """A mod's details, which its project keeps: the loadout's own mod's,
    a patch's or a carried-on mod's.  Not ``patches`` or ``needs``: those
    name other mods by where they are in this loadout."""
    return owners.names("layer", owners.DETAIL)


def release_facts() -> tuple:
    """What is known of the mod a loadout publishes, which a loadout file
    keeps at its top without saying which mod: it is the carried-on mod's
    where there is one, and the loadout's own mod's otherwise."""
    return owners.names("loadout", owners.RELEASE)

# The keys a project file has, besides its part.
_HEAD = ("format", "kind", "written_by", "game", "mod_id", "created_at", "revision")
# And the keys of its part this build reads, at its top and inside the two
# that are records of their own.  Anything else is a newer Studio's, kept as
# it was (:attr:`Ref.kept`).
# What a part keeps the edits its mod keeps inside somebody else's ops
# under (section 5.3, :mod:`.inside`).
INSIDE = "inside"
# Which mod a patch's project patches, by every name it is matched by
# (:func:`.modfolder.match_keys`), so that a loadout making a patch of that
# mod finds this one rather than making a second (:mod:`.filecache`).
PATCHES = "patches"
_PART = ("details", "releases", "ops", "narrow", "unread", "build", "history", INSIDE, PATCHES)
# The collections of a loadout file whose entries are each one mod's, and go
# to its project (:data:`.owners.PER_MOD`): lists by each entry's origin, and
# maps by the origin they are keyed by, under the name the part gives them.
BY_ORIGIN = ("ops", "narrow", "unread")
KEYED_BY_ORIGIN = {"builds": "build", "mod_history": "history"}
# And those whose entries a mod of yours keeps inside somebody else's ops
# go to its project under :data:`INSIDE` (:data:`.owners.INSIDE`).
KEPT_INSIDE = ("ops", "changes", "taken_out", "held_edits")
# What a part's :data:`INSIDE` holds that this build reads: those, and the
# ops of theirs it switched off, each said as :data:`OFF_SAYS`.  Anything
# else in it is a newer Studio's, and so is anything else an entry of
# ``off`` says: kept as it was (:func:`unknown_in`).
OFF = "off"
INSIDE_KEYS = (OFF, *KEPT_INSIDE)
OFF_SAYS = ("origin", "uid", "source")

# Why a project is not written from a loadout.
MOVED = "moved"
MISSING = "missing"
UNREADABLE = "unreadable"


class ProjectError(Exception):
    """A project that cannot be read or written, said as a sentence."""


@dataclass
class Ref:
    """How a loadout refers to the project of one of its mods."""

    # Where the project is: relative to the Studio's folder when it is in
    # it, so the folder can move and take its projects with it.
    path: str
    mod_id: str = ""
    # The revision the loadout's readings of the mod were made from.
    read_at: int = 0
    # The revision the project was at when it was last read or written: the
    # one a project held MOVED is read again at (:mod:`.readback`).  Not
    # saved.
    revision: int = 0
    # Where the project was looked for and was not, where it was found again
    # elsewhere by its ModID when the loadout was opened.  Not saved.
    lost: str = ""
    # For one that is missing: the library build the mod is read from in its
    # place (:func:`.readback.stand_in`).  Not saved.
    standing_in: str = ""
    # The part as it was last read or written, so a save writes the project
    # only when its part moved.  Not saved.
    digest: str = ""
    # Why the project is not to be written from this loadout: MOVED,
    # MISSING or UNREADABLE.  Not saved.
    held: str = ""
    # What the project file held that this build doesn't read, written back
    # as it was (:func:`unknown_in`).  Not saved in the loadout.
    kept: Dict[str, Any] = field(default_factory=dict)
    # What the loadout's reference held that this build doesn't read: a
    # newer Studio's keys, written back as they were (code standards,
    # section 11).
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, origin: str) -> dict:
        out = {"origin": origin, "path": self.path, "mod_id": self.mod_id, "read_at": self.read_at}
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> Ref:
        read_at = raw.get("read_at", 0)
        return cls(
            path=str(raw.get("path") or ""),
            mod_id=str(raw.get("mod_id") or ""),
            read_at=read_at if isinstance(read_at, int) and not isinstance(read_at, bool) else 0,
            extra={key: value for key, value in raw.items() if key not in _REF_KEYS},
        )


# The keys of a loadout's reference to a project that this build reads.
_REF_KEYS = ("origin", "path", "mod_id", "read_at")


def where(path_text: str) -> Path:
    """The project a reference names, wherever the Studio's folder is now.
    One on another machine, or a device's, is spelt as a file that cannot be
    there, so nothing that is handed it asks Windows about the machine
    (:func:`.disk.remote_or_device`)."""
    if disk.remote_or_device(path_text):
        return places.projects_dir() / _NOWHERE / disk.safe_folder_name(
            Path(str(path_text).replace("\\", "/")).name, "project")
    path = Path(path_text)
    return path if path.is_absolute() else places.studio_home() / path


# A folder no Windows file can be in: ``<`` is not allowed in a name.
_NOWHERE = "<elsewhere>"


def trusted_path(path_text: str) -> Optional[Path]:
    """The project a reference names, where it may be read from: inside the
    Studio's folder.  None for one anywhere else, which a loadout file
    somebody sent can name as easily as its own: it is looked for by its
    ModID instead (security review N1)."""
    if not path_text or disk.remote_or_device(path_text):
        return None
    path = where(path_text)
    if any(disk.inside(path, folder) for folder in (places.studio_home(), places.projects_dir())):
        return path
    return None


def as_written(path) -> str:
    """``path`` as a loadout writes it: relative to the Studio's folder when
    it is inside it."""
    path = Path(path)
    home = places.studio_home()
    if disk.inside(path, home):
        try:
            return path.relative_to(home).as_posix()
        except ValueError:
            pass
    return str(path)


def new_path(name: str, taken: Iterable[Path] = (), folder: Optional[Path] = None) -> Path:
    """A project file for a mod called ``name`` that is nobody else's yet,
    in ``folder`` (the projects folder unless named)."""
    folder = Path(folder) if folder is not None else places.projects_dir()
    stem = disk.safe_folder_name(name, "Anno Mod")
    spelt = {os.path.normcase(str(path)) for path in taken}
    n = 1
    while True:
        path = folder / ("%s%s" % (stem if n == 1 else "%s (%d)" % (stem, n), PROJECT_SUFFIX))
        if not path.exists() and os.path.normcase(str(path)) not in spelt:
            return path
        n += 1


def named_for(path, name: str) -> bool:
    """Whether the project file at ``path`` has the name :func:`new_path`
    gives a mod called ``name``: that name, or it numbered.  Without
    regard to case, as Windows names a file: a rename that only changes
    the case does not move it."""
    path = Path(path)
    stem = disk.safe_folder_name(name, "Anno Mod").casefold()
    if path.suffix.casefold() != PROJECT_SUFFIX:
        return False
    have = path.stem.casefold()
    return have == stem or re.fullmatch(r"%s \(\d+\)" % re.escape(stem), have) is not None


def path_for(path, name: str, taken: Iterable[Path] = ()) -> Path:
    """Where the project at ``path`` is kept for a mod called ``name``: the
    file's name follows the mod's.  Where it is, where it is named for it
    already (:func:`named_for`); otherwise a new name in the same folder,
    numbered where it is taken, as :func:`new_path` numbers one."""
    path = Path(path)
    return path if named_for(path, name) else new_path(name, taken, folder=path.parent)


@dataclass(frozen=True)
class Move:
    """A project that went from one file to another as its mod was
    renamed, as loadouts write both paths (:func:`as_written`)."""

    before: str
    after: str
    # Where the save changed the mod's name and nothing else of its
    # project: the revision it was at and the one written.  A loadout that
    # had read it at the first takes the second as read, since nothing its
    # readings were made from moved.
    renamed: Optional[Tuple[int, int]] = None


def refer(data: dict, moves: Iterable[Move]) -> bool:
    """Point ``data``, a loadout file as read, at each project in ``moves``
    where it is now: its references, with their revision where only the
    name moved (:attr:`Move.renamed`), and the checkpoints of its history,
    which bring a mod back from its project.  Whether anything in it
    changed."""
    by_key = {key_of(move.before): move for move in moves}
    changed = False
    for raw in data.get("projects") or ():
        move = by_key.get(key_of(raw["path"])) if isinstance(raw, dict) and isinstance(raw.get("path"), str) else None
        if move is None:
            continue
        if raw["path"] != move.after:
            raw["path"] = move.after
            changed = True
        if move.renamed is not None and raw.get("read_at") == move.renamed[0]:
            raw["read_at"] = move.renamed[1]
            changed = True
    for checkpoint in data.get("history") or ():
        held = checkpoint.get("projects") if isinstance(checkpoint, dict) else None
        for entry in held.values() if isinstance(held, dict) else ():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
                continue
            move = by_key.get(key_of(entry["path"]))
            if move is not None and entry["path"] != move.after:
                entry["path"] = move.after
                changed = True
    return changed


def digest(part: dict) -> str:
    return hashlib.sha1(json.dumps(part, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# -- the parts of a loadout file ------------------------------------------------
def _records(data: dict, layers: dict) -> dict:
    """Each mod's record in a loadout file as read, by origin: its layers',
    and the own mod's under ""."""
    own = data.get("own")
    return {**layers, "": own} if isinstance(own, dict) else dict(layers)


def releases_of(data: dict) -> str:
    """The mod a loadout's release facts are about: its carried-on mod's
    origin where it has one, its own mod's ("") otherwise."""
    return str(data.get("home") or "")


def split(data: dict, origins: Iterable[str], aside: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """Take the part of each of ``origins`` out of ``data``, a loadout as
    :meth:`~.changes.Loadout.to_dict` writes it, and return them by origin.

    What is left in ``data`` is the loadout's: its list, its switches, and
    what it works out -- every change among it, as the reading of the
    ModOps it was made from.  ``aside`` is what a patch keeps inside a mod
    this loadout does not have loaded (:attr:`~.changes.Loadout.kept_aside`),
    which goes into its part all the same: unloading somebody else's mod
    here takes nothing out of your patch for it.
    """
    origins = list(dict.fromkeys(origins))
    wanted = set(origins)
    parts: Dict[str, dict] = {origin: {} for origin in origins}
    layers = {layer.get("origin"): layer for layer in data.get("layers") or () if isinstance(layer, dict)}
    records = _records(data, layers)
    for origin in origins:
        part = parts[origin]
        if origin in records:
            layer = records[origin]
            part["details"] = {key: layer.pop(key) for key in layer_details() if key in layer}
        if origin in layers:
            layer = layers[origin]
            patched = str(layer.get("patches") or "")
            if patched:
                theirs = layers.get(patched) or {}
                part[PATCHES] = sorted(modfolder.match_keys(theirs.get("mod_id"), patched))
    releases = releases_of(data)
    if releases in wanted:
        parts[releases]["releases"] = {key: data.pop(key) for key in release_facts() if key in data}
    for key in BY_ORIGIN:
        kept: List[Any] = []
        for entry in data.get(key) or ():
            whose = entry.get("origin", "") if isinstance(entry, dict) else None
            if isinstance(whose, str) and whose in wanted:
                parts[whose].setdefault(key, []).append(entry)
            else:
                kept.append(entry)
        if key == "narrow" and not kept:
            # Written only where there are some (:meth:`.changes.Loadout.to_dict`).
            data.pop(key, None)
        elif key in data:
            data[key] = kept
    builds = data.get("builds")
    if isinstance(builds, dict):
        for origin in origins:
            if origin in builds:
                parts[origin]["build"] = builds.pop(origin)
    # Its history: the mod's work at each checkpoint (section 9).
    histories = data.get("mod_history")
    if isinstance(histories, dict):
        for origin in origins:
            if histories.get(origin):
                parts[origin]["history"] = histories.pop(origin)
        if not any(histories.values()):
            # Written only where there is some (:meth:`.changes.Loadout.to_dict`).
            data.pop("mod_history")
    # And the edits each keeps inside somebody else's ops (section 5.3).
    _split_inside(data, wanted, parts)
    _keep_aside(data, parts, aside or {})
    return parts


def join(data: dict, parts: Dict[str, dict]) -> Dict[str, dict]:
    """Put each project's part back into ``data``, a loadout file as read:
    what :func:`split` took out of it.  What a project keeps inside a mod
    the loadout does not have loaded is not put back, and comes back by
    its keeper, for :attr:`~.changes.Loadout.kept_aside`."""
    parts, aside = _set_aside(data, parts)
    _join_inside(data, parts)
    layers = {layer.get("origin"): layer for layer in data.get("layers") or () if isinstance(layer, dict)}
    records = _records(data, layers)
    releases = releases_of(data)
    for origin, part in parts.items():
        details = part.get("details")
        if isinstance(details, dict) and origin in records:
            records[origin].update({key: value for key, value in details.items() if key in layer_details()})
        if origin == releases and isinstance(part.get("releases"), dict):
            data.update({key: value for key, value in part["releases"].items() if key in release_facts()})
        for key in BY_ORIGIN:
            entries = [entry for entry in part.get(key) or () if isinstance(entry, dict)]
            for entry in entries:
                entry["origin"] = origin
            if entries:
                data[key] = list(data.get(key) or ()) + entries
        if isinstance(part.get("build"), dict):
            builds = data.get("builds")
            data["builds"] = dict(builds) if isinstance(builds, dict) else {}
            data["builds"][origin] = part["build"]
        if isinstance(part.get("history"), list) and part["history"]:
            histories = data.get("mod_history")
            data["mod_history"] = dict(histories) if isinstance(histories, dict) else {}
            data["mod_history"][origin] = part["history"]
    return aside


# -- edits inside somebody else's ops ------------------------------------------------------------------
def target_of(entry: Any) -> str:
    """The mod of somebody else's an edit kept inside its ops is about:
    an op's, a change's or a held edit's ``origin``, a ``taken_out``
    entry's first item."""
    if isinstance(entry, (list, tuple)):
        return str(entry[0]) if entry else ""
    return str(entry.get("origin", "")) if isinstance(entry, dict) else ""


def _loaded(data: dict) -> set:
    return {layer.get("origin") for layer in data.get("layers") or () if isinstance(layer, dict)}


def _set_aside(data: dict, parts: Dict[str, dict]):
    """``parts`` without what each keeps inside a mod ``data`` does not
    have loaded, and that, by keeper.  What this build doesn't read is
    neither: it goes back into the file from :attr:`Ref.kept`."""
    loaded = _loaded(data)
    aside: Dict[str, dict] = {}
    present: Dict[str, dict] = {}
    for origin, part in parts.items():
        inside = part.get(INSIDE)
        if not isinstance(inside, dict):
            present[origin] = part
            continue
        here: Dict[str, list] = {}
        for key, entries in inside.items():
            if key not in INSIDE_KEYS:
                continue
            for entry in entries if isinstance(entries, list) else ():
                into = here if target_of(entry) in loaded else aside.setdefault(origin, {})
                into.setdefault(key, []).append(entry)
        present[origin] = {**part, INSIDE: here}
    return present, aside


def _keep_aside(data: dict, parts: Dict[str, dict], aside: Dict[str, dict]) -> None:
    """Add to each part what its keeper keeps inside a mod set aside: one
    ``data`` does not have loaded, or has loaded with none of that keeper's
    edits in it yet."""
    if not aside:
        return
    loaded = _loaded(data)
    for keeper, kept in aside.items():
        if keeper not in parts:
            continue
        here = parts[keeper].get(INSIDE) or {}
        about = {target_of(entry) for entries in here.values() for entry in entries}
        for key, entries in kept.items():
            if key not in INSIDE_KEYS:
                # A newer Studio's, set aside whole by a reading of the
                # project (:mod:`.readback`): kept by :attr:`Ref.kept`.
                continue
            for entry in entries:
                target = target_of(entry)
                if target in loaded and target in about:
                    continue
                parts[keeper].setdefault(INSIDE, {}).setdefault(key, []).append(copy.deepcopy(entry))


def inside_of(data: dict, keeper: str, aside: Optional[Dict[str, dict]] = None) -> dict:
    """What ``keeper`` keeps inside somebody else's ops, as its part holds
    it under :data:`INSIDE`: taken out of ``data``, which has a loadout
    file's ``layers``, ``ops``, ``changes``, ``taken_out`` and
    ``held_edits``, with what it set aside (``aside``) added, as
    :func:`split` does.  For a checkpoint of its history
    (:meth:`.changes.Loadout.kept_inside`)."""
    parts: Dict[str, dict] = {keeper: {}}
    _split_inside(data, [keeper], parts)
    _keep_aside(data, parts, aside or {})
    return parts[keeper].get(INSIDE) or {}


# Where an op of yours among somebody else's stands, by text: kept beside
# it in a project (:func:`_split_inside`) and in what a patch sets aside
# (:meth:`.changes.Loadout.set_aside_kept`), since the uids a reading gives
# their ops are that loadout's alone.
AFTER = "after"
IN = "in"


def _of(op: Any, name: str, default: Any = None) -> Any:
    """A field of an op, as a loadout file has it or as the loadout does."""
    return op.get(name, default) if isinstance(op, dict) else getattr(op, name, default)


def _writing_order(ops: List[Any], origin: str) -> List[int]:
    """The places in ``ops`` of ``origin``'s, in the order its file is
    written in: by their place in it, and where two say one place, as the
    list has them (:func:`.generator` sorts so)."""
    return sorted(
        (at for at, op in enumerate(ops) if _of(op, "origin", "") == origin),
        key=lambda at: (_of(ops[at], "order", 0) or 0, at),
    )


def placed_in(ops: List[Any], at: int) -> dict:
    """Where the op of yours at ``at`` in ``ops`` stands among its mod's,
    by text: after which op of theirs, as its text and how many before it
    say the same (None where it comes first), and in the groups whose
    opening tags say what :data:`IN` lists.  And that op's uid, by which
    a reading that gave it the same one knows it was this reading."""
    me = ops[at]
    origin = _of(me, "origin", "")
    order = _writing_order(ops, origin)
    said = {_of(op, "uid", ""): _of(op, "source", "") for op in ops if _of(op, "origin", "") == origin}
    after = None
    theirs = [place for place in order[:order.index(at)] if not _of(ops[place], "authored", False)]
    if theirs:
        source = _of(ops[theirs[-1]], "source", "")
        after = {"source": source, "uid": _of(ops[theirs[-1]], "uid", ""),
                 "nth": sum(1 for place in theirs[:-1] if _of(ops[place], "source", "") == source)}
    return {AFTER: after, IN: [said.get(uid, "") for uid in _of(me, "within", None) or ()]}


def put_among(ops: List[Any], op: Any, where: dict, again: bool = False) -> bool:
    """Put ``op``, one of yours, into ``ops`` where ``where`` says
    (:func:`placed_in`): straight after the op of theirs it followed, and
    after any of yours put there before it, before the next of theirs, and
    in the groups of its mod that open as its did.  Its place in the file
    stays where that still sorts it there, and is that op's otherwise.
    Whether it was put; ``ops`` is left as it was where it was not.

    Not where the op of theirs it followed is still the one of that uid and
    its groups are all there: the uids are this reading's, and where it
    stood among the loadout's ops says it exactly -- unless ``again``, for
    a reading that kept the uids and gave their ops new places.
    """
    origin = _of(op, "origin", "")
    order = _writing_order(ops, origin)
    theirs = [at for at in order if not _of(ops[at], "authored", False)]
    after = where.get(AFTER)
    known = {_of(ops[at], "uid", ""): at for at in theirs}
    if not again and all(uid in known for uid in _of(op, "within", None) or ()):
        if after is None:
            return False
        found = known.get(after.get("uid"))
        if found is not None and _of(ops[found], "source", "") == after.get("source"):
            return False
    if after is None:
        if not theirs:
            return False
        anchor, at = None, theirs[0]
        high = _of(ops[at], "order", 0) or 0
        # Right in front of it, so a place it shares with it still sorts
        # first.
        top = high + 1
    else:
        same = [at for at in theirs if _of(ops[at], "source", "") == after.get("source")]
        if not same:
            return False
        nth = after.get("nth") if isinstance(after.get("nth"), int) else 0
        anchor = same[min(nth, len(same) - 1)]
        following = theirs[theirs.index(anchor) + 1:]
        top = (_of(ops[following[0]], "order", 0) or 0) if following else None
        at = anchor + 1
        # After those of yours already put after it.
        while (at < len(ops) and _of(ops[at], "origin", "") == origin
               and _of(ops[at], "authored", False)):
            at += 1
    # Its place stays where it still sorts between what is in front of it
    # here and the next of theirs; otherwise it shares the place of what is
    # in front of it, which it follows in the list.
    before = ops[at - 1] if at > 0 and _of(ops[at - 1], "origin", "") == origin else None
    low = (_of(before, "order", 0) or 0) if before is not None else None
    mine = _of(op, "order", 0) or 0
    if (low is None or low <= mine) and (top is None or mine < top):
        place = mine
    elif low is not None:
        place = low
    else:
        place = top - 1
    groups = {_of(ops[at_], "uid", ""): _of(ops[at_], "source", "") for at_ in theirs}
    within = _groups_named(ops, theirs, groups, anchor, where.get(IN) or [])
    _set(op, "order", place)
    if within is not None:
        _set(op, "within", within)
    ops.insert(min(at, len(ops)), op)
    return True


def _groups_named(ops, theirs, groups, anchor, named) -> Optional[List[str]]:
    """The uids of the groups of the mod whose opening tags are ``named``,
    outermost first: the ones round ``anchor``, or it, where they open so,
    and the first that does otherwise.  None where one is not there."""
    around: List[str] = []
    if anchor is not None:
        around = [*(_of(ops[anchor], "within", None) or ()), _of(ops[anchor], "uid", "")]
    found: List[str] = []
    for source in named:
        chosen = next((uid for uid in around if groups.get(uid) == source and uid not in found), None)
        if chosen is None:
            chosen = next((_of(ops[at], "uid", "") for at in theirs
                           if _of(ops[at], "source", "") == source
                           and _of(ops[at], "uid", "") not in found), None)
        if chosen is None:
            return None
        found.append(chosen)
    return found


def moved_along(moves: Dict[Tuple[str, int], int], uids: Set[str], changes: Iterable[Any],
                taken_out: List[Any], keeper: Optional[str] = None) -> None:
    """Move what stood at the place of each op of yours that
    :func:`put_among` put somewhere else to where it went: ``moves`` is
    each one's mod and old place, to its new place.  Its own changes (an
    ``op_uid`` in ``uids``), a change of yours made at that place, and the
    op of theirs taken out there, which it was typed over: :mod:`.held`
    knows what of yours stands in an op's place by its place.  Of
    ``keeper``'s alone, given one, as one project's places are its own."""
    if not moves:
        return
    for change in changes:
        if keeper is not None and _of(change, "kept_by") != keeper:
            continue
        new = moves.get((_of(change, "origin", ""), _of(change, "order", 0) or 0))
        if new is not None and (_of(change, "op_uid", "") in uids
                                or (_of(change, "authored", False) and not _of(change, "op_uid", ""))):
            _set(change, "order", new)
    for at, entry in enumerate(taken_out):
        if not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[3], int):
            continue
        if keeper is not None and (len(entry) < 5 or entry[4] != keeper):
            continue
        new = moves.get((str(entry[0]), entry[3]))
        if new is not None:
            taken_out[at] = [*entry[:3], new, *entry[4:]]


def _set(op: Any, name: str, value: Any) -> None:
    if isinstance(op, dict):
        op[name] = value
    else:
        setattr(op, name, value)


def _split_inside(data: dict, wanted: Iterable[str], parts: Dict[str, dict]) -> None:
    """Take each edit inside somebody else's ops out of ``data``, a loadout
    file, into the part of the project that keeps it, under
    :data:`INSIDE` (:mod:`.inside`).  For :func:`split`, after it has taken
    out the projects' own ops.

    An op of yours goes with where it stood among the loadout's ops
    (``at``), and among theirs by text (:func:`placed_in`), for another
    loadout whose reading of their mod gave them other uids.  An op of
    theirs switched off is named, not taken: the op is the loadout's
    reading of their mod, and only that it is off is the project's.
    """
    wanted = set(wanted)

    def keeper(entry: Any) -> Optional[str]:
        found = _keeper_of(entry)
        if found is None or found not in wanted:
            return None
        origin = entry[0] if isinstance(entry, (list, tuple)) else entry.get("origin", "")
        return None if origin in wanted else found

    def into(origin: str, key: str, entry: Any) -> None:
        parts[origin].setdefault(INSIDE, {}).setdefault(key, []).append(entry)

    ops = data.get("ops")
    if isinstance(ops, list):
        left: List[Any] = []
        held = [entry if isinstance(entry, dict) else {} for entry in ops]
        for at, entry in enumerate(ops):
            whose = keeper(entry) if isinstance(entry, dict) else None
            if whose is None:
                left.append(entry)
            elif entry.get("authored"):
                into(whose, "ops", {**entry, "at": at, **placed_in(held, at)})
            else:
                into(whose, "off", {"origin": entry.get("origin", ""), "uid": entry.get("uid", ""),
                                    "source": entry.get("source", "")})
                left.append(entry)
        data["ops"] = left
    for key in ("changes", "taken_out", "held_edits"):
        entries = data.get(key)
        if not isinstance(entries, list):
            continue
        left = []
        for entry in entries:
            whose = keeper(entry) if isinstance(entry, (dict, list)) else None
            if whose is None:
                left.append(entry)
            else:
                into(whose, key, entry)
        data[key] = left


def _join_inside(data: dict, parts: Dict[str, dict]) -> None:
    """Put back into ``data``, a loadout file as read, the edits each
    project keeps inside somebody else's ops: what :func:`_split_inside`
    took out.  For :func:`join`, before the projects' own ops go back.

    An op of theirs the project has switched off is switched off where the
    loadout has it, by its uid or else by its text, and one the loadout
    has as switched off by that project, which the project no longer has,
    is switched on again: the project is what says.  A list in ``data``
    that is not a list is left as it is, for reading it to refuse.
    """
    held: Dict[str, List[Any]] = {}
    for key in KEPT_INSIDE:
        found = data.get(key, [])
        if isinstance(found, list):
            held[key] = list(found)
    ops, changes = held.get("ops", []), held.get("changes", [])
    mine = set(parts)
    placed = []
    for origin, part in parts.items():
        inside = part.get(INSIDE)
        inside = inside if isinstance(inside, dict) else {}
        off = [entry for entry in inside.get("off") or () if isinstance(entry, dict)]
        # Each op the project has off is found before any is switched on, so
        # that one found by its text is the one the loadout already has off
        # for it where there is one, not an earlier op of the same text.
        chosen: List[dict] = []
        for record in off:
            same = [entry for entry in ops if isinstance(entry, dict)
                    and entry.get("origin", "") == record.get("origin", "")
                    and not any(entry is one for one in chosen)]
            found = next((entry for entry in same if entry.get("uid") == record.get("uid")), None)
            if found is None:
                by_text = [entry for entry in same if entry.get("source") == record.get("source")
                           and not entry.get("authored")]
                found = next((entry for entry in by_text if entry.get("kept_by") == origin
                              and not entry.get("active", True)), None) or next(
                    (entry for entry in by_text if entry.get("active", True)), None)
            if found is not None:
                chosen.append(found)
        for entry in ops:
            if (isinstance(entry, dict) and entry.get("kept_by") == origin
                    and entry.get("origin", "") not in mine and not entry.get("authored")
                    and not entry.get("active", True) and not any(entry is one for one in chosen)):
                _switch_op(entry, changes, True, None)
        for found in chosen:
            _switch_op(found, changes, False, origin)
        for entry in inside.get("ops") or ():
            if isinstance(entry, dict):
                at = entry.get("at")
                placed.append((at if isinstance(at, int) and at >= 0 else len(ops),
                               {**{key: value for key, value in entry.items() if key not in _PLACES},
                                "kept_by": origin}, entry))
        for entry in inside.get("changes") or ():
            if isinstance(entry, dict):
                changes.append({**entry, "kept_by": origin})
        for entry in inside.get("taken_out") or ():
            if isinstance(entry, list) and len(entry) >= 3 and "taken_out" in held:
                held["taken_out"].append(
                    [*entry[:3], entry[3] if len(entry) > 3 else 0, origin, *entry[5:]])
        for entry in inside.get("held_edits") or ():
            if isinstance(entry, dict) and "held_edits" in held:
                held["held_edits"].append({**entry, "kept_by": origin})
    # Each among their ops by text, where it says so (:func:`put_among`);
    # else where it stood, every project's together and the first first, so
    # each goes back among the others as they all were.
    moves: Dict[str, Dict[Tuple[str, int], int]] = {}
    uids: Set[str] = set()
    for at, entry, kept in sorted(placed, key=lambda one: one[0]):
        was = entry.get("order", 0) or 0
        if AFTER in kept and put_among(ops, entry, kept):
            moves.setdefault(entry["kept_by"], {})[(entry.get("origin", ""), was)] = entry["order"]
            uids.add(entry.get("uid", ""))
            continue
        ops.insert(min(at, len(ops)), entry)
    for keeper, moved in moves.items():
        moved_along(moved, uids, changes, held.get("taken_out", []), keeper)
    for key, entries in held.items():
        if entries or key in data:
            data[key] = entries


# What an op set aside or kept in a project carries besides the op.
_PLACES = ("at", AFTER, IN)


def as_op(entry: dict) -> dict:
    """An op of yours kept inside somebody else's, as an op: without where
    it stood (:data:`_PLACES`)."""
    return {key: value for key, value in entry.items() if key not in _PLACES}


def _switch_op(entry: dict, changes: List[Any], on: bool, keeper: Optional[str]) -> None:
    """Switch one op of a loadout file on or off, with what it comes to."""
    entry["active"] = on
    if keeper is None:
        entry.pop("kept_by", None)
    else:
        entry["kept_by"] = keeper
    uid = entry.get("uid")
    for change in changes:
        if isinstance(change, dict) and uid and change.get("op_uid") == uid:
            change["active"] = on


def _keeper_of(entry: Any) -> Optional[str]:
    """Who keeps an edit as a loadout file has it: an op's or a change's
    ``kept_by``, or an entry of ``taken_out``'s fifth item."""
    if isinstance(entry, (list, tuple)):
        kept = entry[4] if len(entry) > 4 else None
    else:
        kept = entry.get("kept_by")
    return kept if isinstance(kept, str) else None


# -- the files -----------------------------------------------------------------
def file_of(part: dict, mod_id: str, revision: int, created_at: Optional[float] = None,
            kept: Optional[Dict[str, Any]] = None, game: str = "") -> dict:
    """The whole of a project file holding ``part``, with what a newer
    Studio wrote into it that this build doesn't read (:func:`unknown_in`)."""
    kept = kept or {}
    content = dict(part)
    for key, inside in kept.items():
        if key and key != _KEPT_OFF and inside:
            content[key] = {**inside, **(content.get(key) or {})}
    for key, value in (kept.get("") or {}).items():
        content.setdefault(key, value)
    if kept.get(_KEPT_OFF) and isinstance(content.get(INSIDE), dict):
        content[INSIDE] = {**content[INSIDE], OFF: _with_off_kept(content[INSIDE].get(OFF), kept[_KEPT_OFF])}
    return {
        "format": PROJECT_FORMAT,
        "kind": KIND,
        "written_by": __version__,
        # Which game the mod is for: a project is found by its ModID, and
        # the same ModID can be a mod for each game.
        "game": game,
        "mod_id": mod_id,
        "created_at": created_at if created_at is not None else time.time(),
        "revision": revision,
        **content,
    }


def unknown_in(part: dict) -> Dict[str, Any]:
    """What a project file's part holds that this build doesn't read: at
    its top under "", and inside its details, releases and :data:`INSIDE`
    by their names.  And what each op of theirs it switched off says
    besides :data:`OFF_SAYS`, as a list under :data:`_KEPT_OFF` of the op's
    :data:`OFF_SAYS` and the rest (:func:`_with_off_kept`)."""
    found: Dict[str, Any] = {}
    top = {key: value for key, value in part.items() if key not in _PART}
    if top:
        found[""] = top
    for key, known in {"details": layer_details(), "releases": release_facts(), INSIDE: INSIDE_KEYS}.items():
        inside = part.get(key)
        if isinstance(inside, dict):
            extra = {name: value for name, value in inside.items() if name not in known}
            if extra:
                found[key] = extra
    inside = part.get(INSIDE)
    off = inside.get(OFF) if isinstance(inside, dict) else None
    said = [({key: entry.get(key, "") for key in OFF_SAYS},
             {key: value for key, value in entry.items() if key not in OFF_SAYS})
            for entry in off if isinstance(entry, dict)] if isinstance(off, list) else []
    if any(rest for _op, rest in said):
        found[_KEPT_OFF] = [[op, rest] for op, rest in said if rest]
    return found


# Where :func:`unknown_in` keeps what entries of ``off`` say besides
# :data:`OFF_SAYS`: no name a part's key can have.
_KEPT_OFF = "inside/off"


def _with_off_kept(off: Any, kept: List[list]) -> Any:
    """``off``, a part's ops of theirs switched off as this build writes
    them, with what each said besides when it was read (``kept``), found by
    its mod and its uid, or else its text: a reading in another loadout
    gives their ops other uids."""
    if not isinstance(off, list):
        return off
    left = [pair for pair in kept if isinstance(pair, list) and len(pair) == 2]
    out = []
    for entry in off:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        same = [pair for pair in left if pair[0].get("origin") == entry.get("origin")]
        found = next((pair for pair in same if pair[0].get("uid") == entry.get("uid")), None) or next(
            (pair for pair in same if pair[0].get("source") == entry.get("source")), None)
        if found is None:
            out.append(entry)
            continue
        left.remove(found)
        out.append({**entry, **{key: value for key, value in found[1].items() if key not in entry}})
    return out


def read(path) -> dict:
    """The project file at ``path``, or ProjectError saying why not."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectError(tr("%s is not there.", str(path))) from None
    except OSError as exc:
        raise ProjectError(tr("%(file)s could not be opened: %(error)s", file=str(path), error=exc)) from None
    except ValueError:
        raise ProjectError(tr("%s is not readable as a project: the file is not valid JSON.", str(path))) from None
    if not isinstance(data, dict) or data.get("kind") != KIND:
        raise ProjectError(tr("%s is not a project file.", str(path)))
    saved = data.get("format")
    if not isinstance(saved, int) or isinstance(saved, bool):
        raise ProjectError(tr(
            "%s is not readable as a project: it does not say which format it "
            "is in, as every project the Studio saves does.", str(path),
        ))
    if saved > PROJECT_FORMAT:
        raise ProjectError(tr(
            "%(file)s was saved by a newer version of the Studio (project "
            "format %(saved)s, and this build reads %(reads)s).",
            file=str(path), saved=saved, reads=PROJECT_FORMAT,
        ))
    if saved < OLDEST_PROJECT_FORMAT:
        raise ProjectError(tr(
            "%(file)s was saved by a test build of the Studio from before its "
            "first release (project format %(saved)s), which this version does "
            "not read.", file=str(path), saved=saved,
        ))
    return data


@dataclass
class Project:
    """One project file, on its own: a mod of yours as its project keeps it,
    read and written without a loadout that has it.

    ``part`` is what the file holds of the mod, as :func:`split` makes it:
    its details and release facts, its ModOps, the ones the Studio wrote and
    the ones carried unread, its build, its history, the edits it keeps
    inside somebody else's ops, and which mod a patch patches.  The rest is
    the file's head: which mod, for which game, at which revision.  ``kept``
    is what the file held that this build doesn't read, written back as it
    was (:func:`unknown_in`).
    """

    path: Path
    part: dict = field(default_factory=dict)
    mod_id: str = ""
    game: str = ""
    revision: int = 0
    created_at: Optional[float] = None
    kept: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def read(cls, path) -> Project:
        """The project at ``path``, or ProjectError saying why not."""
        content = read(path)
        part = part_in(content)
        _drop_remote_paths(part)
        created = content.get("created_at")
        return cls(
            path=Path(path), part=part, mod_id=str(content.get("mod_id") or ""),
            game=str(content.get("game") or ""), revision=revision_of(content),
            created_at=float(created) if isinstance(created, (int, float))
            and not isinstance(created, bool) else None,
            kept=unknown_in(part),
        )

    @property
    def details(self) -> dict:
        found = self.part.get("details")
        return found if isinstance(found, dict) else {}

    @property
    def releases(self) -> dict:
        found = self.part.get("releases")
        return found if isinstance(found, dict) else {}

    @property
    def build(self) -> int:
        """The last build it records of its mod; 0 for none."""
        return build_of(self.part)

    @property
    def history(self) -> List[dict]:
        """Its checkpoints, oldest first, as the file has them."""
        return [raw for raw in self.part.get("history") or () if isinstance(raw, dict)]

    @property
    def inside(self) -> dict:
        """What it keeps inside somebody else's ops (:data:`INSIDE`)."""
        found = self.part.get(INSIDE)
        return found if isinstance(found, dict) else {}

    @property
    def patches(self) -> List[str]:
        """For a patch, every name the mod it patches is matched by."""
        found = self.part.get(PATCHES)
        return [str(key) for key in found if key] if isinstance(found, list) else []

    def entries(self, key: str, origin: str) -> List[dict]:
        """Its ops, the Studio's ModOps or the ops carried unread -- ``key``
        "ops", "narrow" or "unread" -- each as the mod at ``origin``'s."""
        return [{**raw, "origin": origin} for raw in self.part.get(key) or () if isinstance(raw, dict)]

    def name(self, origin: str) -> str:
        """What its mod is called, where it is the mod at ``origin``."""
        return name_of(self.part, origin)

    @property
    def goes_out_as(self) -> str:
        """The ModID its mod goes out under: the one it keeps, or, where its
        Mod ID was left blank, the one an export makes of its name
        (:func:`.modinfo.own_mod_id`).  Found only by the one it keeps, a
        mod exported with a blank Mod ID and read in again was offered a
        second project (R3-14, hands-on run 3).  "" for neither."""
        if self.mod_id.strip():
            return self.mod_id.strip()
        name = str(self.details.get("name") or "").strip()
        if not name:
            return ""
        game = games.GAMES.get(self.game)
        return game.usable_mod_id(name) if game is not None else games.slugify(name)

    def as_file(self) -> dict:
        """The whole of its file, at its revision."""
        return file_of(self.part, self.mod_id, self.revision, created_at=self.created_at,
                       kept=self.kept, game=self.game)

    def as_bytes(self) -> bytes:
        return disk.json_bytes(self.as_file())

    def save(self) -> int:
        """Write it on its own, as its next revision, and say which.

        Refused with ProjectError where its file has moved past the revision
        it was read at: another loadout saved it since, and writing this
        over that would lose what that loadout did."""
        now = on_disk_revision(self.path) if self.path.exists() else None
        if now is not None and now != self.revision:
            raise ProjectError(tr(
                "%s was changed by another loadout since it was read, so it is not written over.",
                str(self.path),
            ))
        self.revision += 1
        # Another version's file is kept beside it first, as a loadout is.
        records.keep_other_version(self.path)
        saving.write_all({self.path: self.as_bytes()})
        return self.revision


def part_in(content: dict) -> dict:
    """The part of a loadout a project file holds: all but its head."""
    return {key: value for key, value in content.items() if key not in _HEAD}


def _drop_remote_paths(part: dict) -> None:
    """Forget the one path a project holds, its mod's scripts folder, where
    it is on another machine or a device's: a project can come from somebody
    else, and nothing is to ask Windows about it (security review N1)."""
    details = part.get("details")
    if isinstance(details, dict) and disk.remote_or_device(details.get("scripts_folder")):
        details["scripts_folder"] = ""


def revision_of(content: dict) -> int:
    revision = content.get("revision", 0)
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else 0


def on_disk_revision(path) -> Optional[int]:
    """The revision the project at ``path`` is at now, or None where it
    cannot be read."""
    try:
        return revision_of(read(path))
    except ProjectError:
        return None


def key_of(path) -> str:
    """What a loadout's reference to the project at ``path`` is matched by,
    wherever the Studio's folder is spelt from (:mod:`.filecache`)."""
    return "project:" + os.path.normcase(os.path.abspath(str(where(str(path))))).lower()


def build_of(content: dict) -> int:
    """The last build a project file records of its mod; 0 for none."""
    build = content.get("build")
    number = build.get("build", 0) if isinstance(build, dict) else 0
    return number if isinstance(number, int) and not isinstance(number, bool) else 0


def named(part: dict, name: Any) -> dict:
    """``part`` with its mod called ``name``, or with no name where it is
    None: to tell a part whose name alone moved."""
    details = {key: value for key, value in (part.get("details") or {}).items() if key != "name"}
    if name is not None:
        details["name"] = name
    return {**part, "details": details}


def name_of(part: dict, origin: str) -> str:
    """What a project's mod is called, for its file's name and for saying
    which project a message is about."""
    details = part.get("details") or {}
    return str(details.get("name") or origin or "Anno Mod")
