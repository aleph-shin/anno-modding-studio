"""Which of your installed mods the game is actually loading.

A mods folder is not a flat pile of mods.  Some of them are switched off, and
both games record that on disk -- in their own way, and in a form that outlives
anything the Studio does:

* **Anno 1800** ignores a folder whose name starts with ``-``.  Renaming
  ``[Seal] Charcoal Power Plant`` to ``-[Seal] Charcoal Power Plant`` switches
  it off, and is what the community's mod managers do.  Its loader has a
  second switch as well: ``activation.json``, beside the mods or in the mods
  folder under Documents, lists by ModID the mods that are off whatever their
  folders are called.
* **Anno 117** keeps a *profile*: ``mods/profiles/<name>.txt`` lists mods by
  ModID, one per line, with ``#`` in front of the ones that are off.
  ``mods/profile.txt`` names which profile is live.  The game ships a
  ``how-to.txt`` next to them saying exactly this.

Which matters more than it looks.  The obvious way to let somebody manage a
whole mod list here would be for the Studio to own the mods folder outright and
rebuild it on every export -- but a mod is not only its ModOps.  Most of a real
mods folder is meshes, textures and localisation that the Studio never reads and
could not put back; rebuilding the folder from the loadout would delete all of
it.

Reading the game's own switch instead costs nothing and cannot lose anything: a
mod that is off is off in the folder, in the loadout, and in the game, and it is
still sitting there when you switch it back on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import disk, games, modio_cache
from .games import ANNO_117, ANNO_1800
from .i18n import join_list, number, tr, trn
from .modfolder import OFF_MARK, bare_name, is_marked_off, switch_holder

# Anno 117's profile files, relative to a mods folder.
POINTER_FILE = "profile.txt"
PROFILE_DIR = "profiles"
DEFAULT_PROFILE = "default"

# The line in a profile that says whether a mod nobody has listed is on.
NEW_MODS_LINE = "enablenewmods"

_ACTIVE_PROFILE = re.compile(r"^\s*ActiveProfile\s+(.+?)\s*$", re.IGNORECASE)
# What may not be inside one line of a profile: a line break, or any other
# control character.
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")

# -- Anno 1800: the folder name is the switch ----------------------------------
def marked(name: str, on: bool) -> str:
    """``name`` written the way the folder should be for a mod that is ``on``."""
    bare = bare_name(name)
    return bare if on else OFF_MARK + bare


# -- Anno 1800: activation.json, the loader's other switch -----------------------
# ``mods/activation.json`` holding ``{"disabledIds": [...]}``, in the
# installation's mods folder or the one under Documents: each entry a ModID,
# or for a mod that declares none its folder name. A mod listed there is off
# whatever its folder is called, and a mod bundled in another has no switch
# of its own -- it goes with its host. The loader's docs describe it; no mod
# manager on this machine writes one, so it is read, and written only to
# switch a mod back on, which renaming its folder could not do.
ACTIVATION_FILE = "activation.json"


def activation_files(game: str, folder) -> List[Path]:
    """The activation files the game reads, given one of its mods folders."""
    if game != ANNO_1800:
        return []
    found = []
    for root in (Path(folder) if folder else None, documents_mods(game)):
        if root is None:
            continue
        path = root / ACTIVATION_FILE
        if path.is_file() and path not in found:
            found.append(path)
    return found


def _disabled_in(path: Path) -> List[str]:
    listed = disk.read_json(path, {}).get("disabledIds")
    return [str(entry).strip() for entry in listed or [] if str(entry).strip()]


def disabled_ids(game: str, folder) -> Dict[str, Path]:
    """Every ModID or folder name an activation file switches off, lowercased,
    to the file that does it."""
    found: Dict[str, Path] = {}
    for path in activation_files(game, folder):
        for entry in _disabled_in(path):
            found.setdefault(entry.lower(), path)
    return found


def activation_key(name: str, mod_id: str = "") -> str:
    """What an activation file would call a mod: its ModID, or its folder's
    name without the off mark. ``name`` may be a bundled mod's path; its host
    answers for it, so the caller passes the host's."""
    return (mod_id or bare_name(name.split("/")[0])).strip().lower()


def enable_in_activation(path: Path, keys) -> bool:
    """Take a mod out of an activation file's ``disabledIds`` -- any entry
    that is one of ``keys``, lowercased -- keeping everything else in it.
    Whether it was there to take out."""
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    listed = data.get("disabledIds") or []
    kept = [entry for entry in listed if str(entry).strip().lower() not in keys]
    if len(kept) == len(listed):
        return False
    data["disabledIds"] = kept
    disk.write_json(path, data)
    return True


# -- Anno 117: a profile lists the mods by id ----------------------------------
@dataclass
class ProfileFile:
    """One Anno 117 profile file, ``mods/profiles/<name>.txt``: what it says, and
    where. Play writes the user's profile (``changes.Profile``) into one."""

    path: Path
    on: Dict[str, bool] = field(default_factory=dict)  # lowercase ModID -> on
    # Mods nobody has listed. Commented out, the game switches new mods off,
    # which is how the shipped how-to suggests you freeze a loadout.
    new_mods_on: bool = True
    # Every line as read, so writing it back keeps the comments, the ordering
    # and the display name the user gave it.
    lines: List[str] = field(default_factory=list)
    # What the game wrote after an id, when it did: it annotates a listed mod
    # it could not find with ``# not installed`` on the same line. Lowercase
    # ModID -> the note, without its hash.
    notes: Dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.stem


def read_profile(path) -> ProfileFile:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    profile = ProfileFile(path=Path(path), lines=list(lines))
    for line in lines:
        body = line.strip()
        if not body or body.startswith("##"):
            continue  # "## ..." is prose, not a switched-off mod
        off = body.startswith("#")
        body = body.lstrip("#").strip()
        if not body:
            continue
        first = body.split()[0].lower()
        if first == NEW_MODS_LINE:
            profile.new_mods_on = not off
            continue
        if first == "displayname":
            continue
        # The id is the first word. The game itself writes after it -- a mod
        # it could not find gets "# not installed" on the same line -- and
        # a line read whole would be an id nobody has.
        mod_id, note = _split_note(body)
        profile.on[mod_id] = not off
        if note:
            profile.notes[mod_id] = note
    return profile


def _split_note(body: str) -> tuple:
    """``("mod-id", "not installed")`` from a profile line's body, the note
    being whatever follows the id, without its hash; ``""`` when nothing does."""
    head, _hash, tail = body.partition("#")
    words = head.split()
    if not words:
        return "", ""
    # Something after the id and before any hash is still not part of the id.
    return words[0].lower(), (" ".join(words[1:]) + " " + tail.strip()).strip()


def active_profile(folder) -> Optional[Path]:
    """The profile file a mods folder's ``profile.txt`` points at."""
    candidate = Path(folder) / PROFILE_DIR / (pointed_at(folder) + ".txt")
    return candidate if candidate.is_file() else None


def documents_mods(game: str) -> Optional[Path]:
    """The game's per-user mods folder, which is a second place mods live.

    Anno 117 loads from several places at once -- ``mods/.ubi`` and ``mods``
    inside the installation, this one, and mod.io's download cache -- and keeps
    the profile here rather than beside the mods.  So a folder being read may
    have no profile of its own while still being governed by one.
    """
    home = games.documents_dir(game)
    if home is None:
        return None
    candidate = home / "mods"
    return candidate if candidate.is_dir() else None


# Where Anno 117's loader reads the answers to mods' own options, in the mods
# folder under Documents.  There is no screen in the game for them: the loader's
# documentation says to write the file yourself, and in practice a mod manager
# does.  Keyed by ModID, holding only what differs from each mod's defaults.
OPTIONS_FILE = "active-options.jsonc"


def options_file(game: str) -> Optional[Path]:
    """Where the game keeps the answers to mods' own options, if it has one."""
    if game != ANNO_117:
        return None
    documents = documents_mods(game)
    return documents / OPTIONS_FILE if documents is not None else None


def game_options(game: str, at: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """What the game has each mod's options set to, by lower-case ModID.

    Read so the Studio reads a mod at the values the game runs rather than at
    its author's defaults.  An absent or unreadable file is nothing set: the
    loader then runs every mod at its defaults too.  ``at`` is the file, for a
    test; otherwise it is found.
    """
    path = Path(at) if at is not None else options_file(game)
    if path is None:
        return {}
    raw = disk.read_json(path, {}, comments=True)
    found: Dict[str, Dict[str, str]] = {}
    for mod_id, values in raw.items():
        if not isinstance(values, dict):
            continue
        found[str(mod_id).strip().lower()] = {
            str(name): _option_text(value) for name, value in values.items()
        }
    return found


def write_game_options(game: str, answers: Dict[str, Dict[str, str]], at: Optional[Path] = None) -> Optional[Path]:
    """Put ``answers`` -- ModID to the options that differ from its defaults --
    into the file the game reads, and return it; None when nothing changed or
    the game has no such file.

    Only the mods named in ``answers`` are touched: one with nothing but
    defaults loses its entry, as the loader expects, and every entry for a
    mod the loadout does not mention stays exactly as it was.  So does a
    comment block at the top of the file.  The rest is written the way the
    managers that write it do -- two-space JSON, values as text.
    """
    import json

    path = Path(at) if at is not None else options_file(game)
    if path is None:
        return None
    text = ""
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            return None
    try:
        held = json.loads(disk.strip_comments(text)) if text.strip() else {}
    except ValueError:
        return None  # somebody else's file, and not one it can safely rewrite
    if not isinstance(held, dict):
        return None
    by_key = {str(key).strip().lower(): key for key in held}
    after = dict(held)
    for mod_id, values in answers.items():
        key = by_key.get(mod_id.strip().lower(), mod_id)
        if values:
            after[key] = {str(name): str(value) for name, value in values.items()}
        else:
            after.pop(key, None)
    if after == held:
        return None
    header = text[: text.find("{")] if "{" in text else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    disk.write_text(path, header + json.dumps(after, indent=2, ensure_ascii=False) + "\n")
    return path


def _option_text(value) -> str:
    """An answer as the loader reads it: text, with JSON's own true and false
    spelt the way a modinfo spells them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def mod_paths(game: str, folder) -> List[Path]:
    """Every folder the game loads mods from, given one of them.

    Anno 117 searches four places at once and merges them into a single ordered
    list -- ``mods/.ubi`` and ``mods`` inside the installation, one under
    Documents, and mod.io's download cache -- so reading only the folder you
    pointed at leaves mods out of a reading whose whole claim is that it shows
    the game you actually play.  Its own loader log names all four.  1800 has
    three: the installation's folder, the one under Documents, and the same
    mod.io cache -- its loader log names a mod from there with an absolute
    path where every other mod is ``from mods\\``.

    Only folders that exist come back, the one asked for always first.
    """
    found = [Path(folder)] if folder else []
    for candidate in (
        Path(folder) / ".ubi" if folder and game == ANNO_117 else None,
        documents_mods(game),
        modio_cache.mods_dir(game),
    ):
        if candidate is None or not candidate.is_dir():
            continue
        if not any(candidate == seen for seen in found):
            found.append(candidate)
    # Anno 1800 also reads mods out of zips in its own folders (not mod.io's
    # cache, which holds them unpacked): each such folder's zips come in as
    # a mirror of them, read beside it -- see core.zipped.
    if game == ANNO_1800:
        from . import zipped

        cache = modio_cache.mods_dir(game)
        for root in list(found):
            if cache is not None and root == cache:
                continue
            mirror = zipped.sync(game, root)
            if mirror is not None:
                found.insert(found.index(root) + 1, mirror)
    return found


def profile_for(folder, game: str) -> Optional[Path]:
    """Where the profile governing ``folder`` lives, if there is one."""
    if game != ANNO_117:
        return None
    if folder:
        found = active_profile(folder)
        if found is not None:
            return found
    documents = documents_mods(game)
    return active_profile(documents) if documents is not None else None


# -- and in what order they are applied ----------------------------------------
# A mod that declares this loads after everything else, rather than after one
# named mod. Real: [Map] Easy Seed Search (Taludas) declares it.
AFTER_EVERYTHING = "*"


def declared_after(meta) -> List[str]:
    """The mods this one says it must load after, by their ModID.

    1800 spells it ``LoadAfterIds``; 117 uses ``Dependencies.LoadAfter`` and
    ``Dependencies.Require`` -- requiring a mod means loading after it, since
    the point is to patch what it leaves behind.
    """
    return [str(value) for value in (meta or {}).get("load_after") or []]


def base_key(game: str, name: str, meta=None):
    """Where a mod sits before anything it declares is taken into account.

    Anno 1800 goes by each mod's own folder name, as written: measured against
    its loader's log, lower-casing it put 25 of 131 mods out of place.  **Anno
    117 does not**: its loader sorts by
    ``"[Category] ModName"`` out of the modinfo, so a mod's folder name has no
    bearing on when it loads.  That was read off the loader's own log, which
    this key reproduces exactly for all sixteen mods it had enabled -- including
    the case-sensitive comparison, which is what puts ``[Gameplay] Powerful
    Flagship`` in front of ``[Gameplay] [Seal] Shrine Rework``.
    """
    if game != ANNO_117:
        # The mod's own folder -- a bundled one's, not its host's -- compared
        # as written, capitals before ``[`` before lower case.
        return bare_name(name.split("/")[-1])
    meta = meta or {}
    return "[%s] %s" % (
        meta.get("category") or "Mod",
        meta.get("mod_name") or meta.get("mod_id") or name,
    )


def order(game: str, mods: Dict[str, dict], logged=None) -> List[str]:
    """``mods`` (name -> its metadata) in the order the game applies them.

    ``logged`` is what the loader recorded last time the game ran, from
    :func:`logged_order`.  Where it covers a mod, it wins outright -- it is what
    happened rather than what should happen.  A mod it does not name (installed
    since, or switched on since) falls back to the rule below and takes its place
    among the mods the log does cover.

    Failing that, the loader's own sort, from its source -- see
    :func:`_loader_sort`: the base order is whatever that game sorts by, and
    then every mod that declares a mod to load after is placed first, each
    straight after what it needs, and the rest follow in base order.

    It used to be "each mod at the earliest place its list allows", which kept
    base order wherever it could. The loader does not: on the Anno 1800 folder
    this was written against, that agreed with its log for 52 mods in a row out
    of 131. The loader's sort, with each mod's own folder name compared as
    written for the base order, reproduces the log exactly -- all 131, no pair
    the other way round.
    """
    ranked = sorted(mods, key=lambda name: base_key(game, name, mods.get(name)))
    if logged:
        # Anchor on the log where it speaks, and slot the rest in around it by
        # the rule -- keeping each unlogged mod next to the logged mod it would
        # have sorted beside.
        seen = {name: index for index, name in enumerate(logged) if name in mods}
        if seen:
            # Starting below the first logged position, so a mod the log does
            # not name that sorts in front of everything it does name lands in
            # front rather than behind the first one.
            at = -1.0
            placed = {}
            for name in ranked:
                if name in seen:
                    at = float(seen[name])
                else:
                    at += 1e-6
                placed[name] = at
            return sorted(ranked, key=lambda name: (placed[name], base_key(game, name, mods.get(name))))
    return _loader_sort(game, mods, ranked)


def loader_id(game: str, name: str, meta) -> str:
    """What the loader knows a mod by: its ModID, or -- Anno 1800 only, for a
    mod that declares none -- its folder's name."""
    mod_id = ((meta or {}).get("mod_id") or "").strip()
    if mod_id or game == ANNO_117:
        return mod_id
    return bare_name(name.split("/")[-1])


def last_of_all(game: str, mods: Dict[str, dict]) -> List[str]:
    """The load-after list that puts a mod after every one of ``mods``.

    The star, which is the late phase and so after every mod without one --
    and then each of ``mods`` that is late itself, by what the loader knows it
    by.  Late mods order among themselves, in ModID order unless one names
    another, so the star alone would leave this mod in front of any late mod
    whose ID sorts after its own.
    """
    named = [AFTER_EVERYTHING]
    held = {AFTER_EVERYTHING}
    for name, meta in mods.items():
        if AFTER_EVERYTHING not in (value.strip() for value in declared_after(meta)):
            continue
        key = loader_id(game, name, meta)
        if key and key.lower() not in held:
            held.add(key.lower())
            named.append(key)
    return named


def _loader_sort(game: str, mods: Dict[str, dict], ranked: List[str]) -> List[str]:
    """The loader's own sort, ``ModLoadingOrder::Sort``, where there is no log.

    Two graphs, one for the late phase -- a ``*`` in a mod's load-after list --
    and one for the rest, and each sorted by ``DependencyGraph::Sort``: a
    depth-first walk that places every mod which *declares* anything first,
    each straight after what it needs, and then every mod that declares
    nothing, in base order. So a mod another mod names is pulled to the front
    with it. The normal graph takes mods in base order; the late one in ModID
    order, since the loader walks its map of mods keyed by ModID for those.

    A dependency is by ModID, matched without regard to case as everywhere in
    the Studio. One the graph has no mod for is walked past, and still counts
    as the mod declaring something -- the loader tests the list, not what it
    finds. A normal mod naming a late one gets nothing from it: "a mod without
    a ``*`` cannot load after a mod with a ``*``".
    """
    names_of: Dict[str, List[str]] = {}
    declared: Dict[str, List[str]] = {}
    late: List[str] = []
    for name in ranked:
        meta = mods.get(name)
        key = loader_id(game, name, meta).lower() or ("\0" + name)
        names_of.setdefault(key, []).append(name)
        if key in declared:
            continue
        wanted = [value.strip().lower() for value in declared_after(meta) if value.strip()]
        declared[key] = wanted
        if AFTER_EVERYTHING in wanted:
            late.append(key)
    late_set = set(late)
    normal = [key for key in dict.fromkeys(
        loader_id(game, name, mods.get(name)).lower() or ("\0" + name) for name in ranked
    ) if key not in late_set]
    late.sort(key=lambda key: loader_id(game, names_of[key][0], mods.get(names_of[key][0])) or key)

    out: List[str] = []
    for packages, keep in ((normal, set(normal)), (late, late_set)):
        graph = {key: declared[key] for key in packages}
        for key in _dependency_sort(packages, graph):
            if key in keep:
                out.extend(names_of[key])
    return out


def _dependency_sort(packages: List[str], graph: Dict[str, List[str]]) -> List[str]:
    """``DependencyGraph::Sort``: those with dependencies first, then the rest,
    each walked depth first so what it needs comes before it. Iterative, and
    each node once, so a cycle ends where the walk first comes back round."""
    visited: set = set()
    out: List[str] = []

    def walk(start: str) -> None:
        visited.add(start)
        stack = [(start, iter(graph.get(start, ())))]
        while stack:
            node, needs = stack[-1]
            for dep in needs:
                if dep not in visited:
                    visited.add(dep)
                    stack.append((dep, iter(graph.get(dep, ()))))
                    break
            else:
                stack.pop()
                out.append(node)

    for with_deps in (True, False):
        for key in packages:
            if key not in visited and bool(graph.get(key)) == with_deps:
                walk(key)
    return out


# -- what a folder's mods are set to -------------------------------------------
@dataclass
class FolderSwitches:
    """Which of a folder's mods are switched on, and where that is recorded."""

    game: str
    folder: Path
    on: Dict[str, bool] = field(default_factory=dict)  # mod -> switched on
    # Mod -> the folder it is actually in, which is not the same string: an
    # Anno 1800 mod that is switched off sits in a folder with a "-" in front,
    # and it is still the same mod.  Everything else here -- the loadout, load
    # order, a change's origin -- names the mod; only this names the folder.
    # A full path, because a game may load from several folders at once.
    folders: Dict[str, Path] = field(default_factory=dict)
    profile: Optional[Path] = None
    new_mods_on: bool = True
    # Mods the profile lists that are not in this folder. Anno 117 loads from
    # several folders at once, so these are usually real mods living elsewhere
    # rather than stale entries, and writing the profile back must not drop them.
    elsewhere: List[str] = field(default_factory=list)
    # Anno 1800 mods an activation file switches off: mod -> that file.
    disabled: Dict[str, Path] = field(default_factory=dict)

    @property
    def off(self) -> List[str]:
        return sorted(name for name, on in self.on.items() if not on)

    def is_on(self, name: str) -> bool:
        return self.on.get(name, True)

    def describe(self) -> str:
        if not self.on:
            return ""
        off = len(self.off)
        if not off:
            return trn("All %(n)s switched on.", "All %(n)s switched on.", len(self.on))
        if self.profile is not None:
            return trn(
                "%(n)s of %(total)s switched off by %(profile)s.",
                "%(n)s of %(total)s switched off by %(profile)s.",
                off, total=number(len(self.on)), profile=self.profile.stem,
            )
        return trn(
            "%(n)s of %(total)s switched off.", "%(n)s of %(total)s switched off.",
            off, total=number(len(self.on)),
        )


def tops_of(game: str, folder, paths) -> List[Path]:
    """The top-level folder each of ``paths`` sits in, in whichever of the
    folders the game loads mods from holds it -- what listing those folders
    would find, for mods a caller has already read.  In the order given, once
    each."""
    # Deepest first: Anno 117's ``mods/.ubi`` is a folder of mods inside one.
    roots = sorted(mod_paths(game, folder), key=lambda root: len(root.parts), reverse=True)
    found: List[Path] = []
    for path in paths:
        path = Path(path)
        for root in roots:
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                continue
            if parts:
                top = root / parts[0]
                if top not in found:
                    found.append(top)
            break
    return found


def read(folder, game: str, ids: Optional[Dict[str, str]] = None, names=None,
         tops=None) -> FolderSwitches:
    """What ``folder``'s mods are switched to, as the game would read it.

    ``ids`` maps folder name to the ModID that folder declares, which is what a
    117 profile keys on.  Without it 117 can only answer for ids it can match, so
    callers that have already read the modinfos should pass it.

    ``names`` says which of them are mods at all, by the name the rest of the
    Studio knows the mod by.  A mods directory holds other things -- ``data``
    overrides, the loader's own log, a ``how-to.txt`` -- and a caller that has
    just read the folder knows which of them turned out to be mods far better
    than a second guess from here would.

    ``tops`` are the folders at the top of the mods folders that a caller has
    just read the mods out of -- :func:`tops_of` -- so they are not listed
    again here: Play has the inventory, the mods-folder dialog its reading.
    Without them the folders are listed, for whoever has read nothing.
    """
    folder = Path(folder)
    found: dict = {}
    if tops is None:
        tops = [
            entry
            for root in mod_paths(game, folder)
            for entry in root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    for entry in tops:
        found.setdefault(bare_name(Path(entry).name), Path(entry))
    folders = {mod: found[mod] for mod in sorted(found, key=str.lower)}
    if names is not None:
        # A name may be a path: a mod bundled inside another is a mod of its
        # own, and the switch it answers to is its host's -- the game skips the
        # whole tree, so what is inside never gets as far as being asked.
        wanted = {}
        for name in sorted(names, key=str.lower):
            top = name.split("/")[0]
            if top not in folders:
                continue
            wanted[name] = folders[top].joinpath(*name.split("/")[1:])
        folders = wanted
        inherits = {name: name.split("/")[0] for name in folders}
    else:
        inherits = {name: name for name in folders}

    if game != ANNO_117:
        listed = disabled_ids(game, folder)
        disabled = {}
        for mod in folders:
            host = inherits[mod]
            key = activation_key(host, (ids or {}).get(host, ""))
            if key in listed:
                disabled[mod] = listed[key]
        return FolderSwitches(
            game=game,
            folder=folder,
            on={
                mod: not is_marked_off(found[inherits[mod]].name) and mod not in disabled
                for mod in folders
                if inherits[mod] in found
            },
            folders=folders,
            disabled=disabled,
        )

    path = profile_for(folder, game)
    if path is None:
        return FolderSwitches(
            game=game,
            folder=folder,
            on=dict.fromkeys(folders, True),
            folders=folders,
        )
    profile = read_profile(path)
    declared = {mod: ((ids or {}).get(mod) or "").lower() for mod in folders}
    said = {
        mod: profile.on.get(mod_id, profile.new_mods_on) if mod_id
        else profile.new_mods_on
        for mod, mod_id in declared.items()
    }
    # A bundled mod is on exactly when its host is: the outermost mod here it
    # is inside, which for one in the mod.io cache is below the numbered
    # folder.  The game does not read the bundled mod's own line -- listed
    # off, or not listed under a frozen profile, it loaded with its host all
    # the same (D1, 16 Oct 2026).  Inside no mod here, its line is its own.
    on = {mod: said[switch_holder(mod, said) or mod] for mod in said}
    known = set(declared.values())
    return FolderSwitches(
        game=game,
        folder=folder,
        on=on,
        folders=folders,
        profile=path,
        new_mods_on=profile.new_mods_on,
        elsewhere=sorted(mod_id for mod_id in profile.on if mod_id not in known),
    )


# -- a profile of the loadout's own -----------------------------------------------
# The line that says the Studio wrote a profile, and so may write it again.  A
# profile without it is the user's, whatever it is called, and is left alone.
# Frozen from 0.1.0 (tests/test_contracts.py).  The wording test builds wrote
# before it ("...from the project it plays.") is not read: such a profile is
# still the Studio's by its name (:func:`named_as_studios`), which every
# caller asks as well.
STUDIO_LINE = "## Written by Anno Modding Studio; every Play rewrites it from the loadout it plays."
# The one profile the Studio writes, whatever loadout is played: output, not a
# place anything lives.  The loadout is the record; its DisplayName says which.
STUDIO_PROFILE = "Anno Modding Studio"
_PROFILE_HEAD = (
    "## Download mods at: https://mod.io/g/anno-117-pax-romana",
    "##",
    "## Disable a mod:",
    "## - Add a single `#` before it.",
    "## Enable a mod:",
    "## - Remove the `#` before it.",
    "##",
    "## Disable mods not listed:",
    "## - Add `#` before `EnableNewMods` to automatically disable new mods.",
)


def pointer_folder(folder, game: str) -> Optional[Path]:
    """The folder whose ``profile.txt`` the game reads: the one asked about if
    it has one, else the one under Documents, which is where 117 keeps it."""
    if game != ANNO_117:
        return None
    folder = Path(folder) if folder else None
    if folder is not None and (folder / POINTER_FILE).is_file():
        return folder
    documents = documents_mods(game)
    if documents is not None:
        return documents
    return folder


def profile_name(title: str) -> str:
    """A file name for a profile called ``title``: what a folder name would
    be (:func:`core.disk.safe_folder_name`), with no ``#``, which starts a
    comment in the pointer file that names it."""
    return disk.safe_folder_name((title or "").replace("#", "_"), "loadout")


def pointed_at(folder) -> str:
    """The name the pointer names, or the default when it names nothing."""
    pointer = Path(folder) / POINTER_FILE
    if not pointer.is_file():
        return DEFAULT_PROFILE
    for line in pointer.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("#"):
            continue
        found = _ACTIVE_PROFILE.match(line)
        if found:
            return found.group(1)
    return DEFAULT_PROFILE


def is_studios(path) -> bool:
    """Whether a profile file is one the Studio wrote, and so may rewrite."""
    try:
        return STUDIO_LINE in Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def named_as_studios(path) -> bool:
    """Whether a profile file has the name the Studio writes its own under,
    with or without the number :func:`write_profile` gives it.

    Not :func:`is_studios`.  The line that marks a profile as the Studio's
    does not survive the file being written again by something else: on one
    machine ``Anno Modding Studio.txt`` came back from a game session with
    the game's own header and no such line (seen 4 Oct 2026).  For asking
    whether a profile is one of the user's own to offer as theirs, the name
    is the better witness -- and :func:`write_profile` rewrites a file of
    that name, rather than numbering a new one beside it every Play.
    """
    return re.fullmatch(re.escape(STUDIO_PROFILE) + r"( \(\d+\))?", Path(path).stem, re.IGNORECASE) is not None


def write_profile(folder, name: str, display: str, on: Dict[str, bool], frozen: bool) -> Path:
    """Write ``mods/profiles/<name>.txt`` whole, as the Studio's own.

    ``on`` is lowercase ModID to switch.  ``frozen`` comments ``EnableNewMods``
    out, so a mod the profile does not list is off -- what a loadout means.
    A file of that name the Studio did not write is not touched: the name
    gets a number, the way a loadout's home does.  One with the Studio's own
    name counts as written by it, marked or not (:func:`named_as_studios`).
    """
    profiles = Path(folder) / PROFILE_DIR
    profiles.mkdir(parents=True, exist_ok=True)
    path = profiles / (name + ".txt")
    n = 2
    while path.exists() and not (is_studios(path) or named_as_studios(path)):
        path = profiles / ("%s (%d).txt" % (name, n))
        n += 1
    lines = list(_PROFILE_HEAD)
    lines.append(STUDIO_LINE)
    lines.append(("# " if frozen else "") + "EnableNewMods")
    # Each is one line of the file, and a ModID comes from a mod's own
    # modinfo: one holding a line break would write lines of its own --
    # ``EnableNewMods`` back on, or a mod the loadout leaves off.
    lines.append("DisplayName %s" % _CONTROL.sub(" ", display))
    lines.append("")
    for mod_id in sorted(on):
        if _CONTROL.search(mod_id):
            continue
        lines.append(mod_id if on[mod_id] else "# " + mod_id)
    disk.write_text(path, "\n".join(lines) + "\n")
    return path


def point_at(folder, name: str) -> Path:
    """Make ``name`` the live profile, keeping the pointer's own comments."""
    pointer = Path(folder) / POINTER_FILE
    lines = (
        pointer.read_text(encoding="utf-8", errors="replace").splitlines()
        if pointer.is_file() else [
            '## Change the value after "ActiveProfile " to configure which mod '
            "profile will be loaded by the game on startup.",
        ]
    )
    out, done = [], False
    for line in lines:
        if not line.lstrip().startswith("#") and _ACTIVE_PROFILE.match(line):
            if not done:
                out.append("ActiveProfile %s" % name)
                done = True
            continue
        out.append(line)
    if not done:
        out.append("ActiveProfile %s" % name)
    disk.write_text(pointer, "\n".join(out) + "\n")
    return pointer


# -- writing it back -----------------------------------------------------------
@dataclass
class Written:
    """What switching mods on and off actually did."""

    # (mod, the folder it now lives in) -- so a caller holding a path to the old
    # folder can follow it rather than going looking for one that has moved.
    renamed: List[tuple] = field(default_factory=list)
    # (mod, its link) -- a library mod switched by taking its link away or
    # putting it back.  The same shape as ``renamed`` so a caller following
    # paths can follow both, but not a rename, and not described as one.
    linked: List[tuple] = field(default_factory=list)
    profile: Optional[Path] = None
    listed: int = 0
    failed: List[tuple] = field(default_factory=list)  # (name, why)
    # (mod, the activation file it was taken out of) -- switched on by that.
    unlisted: List[tuple] = field(default_factory=list)

    @property
    def moved(self) -> List[tuple]:
        """Every (mod, where it now is), whichever way its switch moved."""
        return self.renamed + self.linked

    def merge(self, other: Written) -> Written:
        """Fold ``other`` -- a later pass of switching -- into this one,
        every field of it; the profile written first stays."""
        self.renamed += other.renamed
        self.linked += other.linked
        self.listed += other.listed
        self.failed += other.failed
        self.unlisted += other.unlisted
        self.profile = self.profile or other.profile
        return self

    @property
    def changed(self) -> int:
        return len(self.renamed) + len(self.linked) + self.listed + len(self.unlisted)

    def describe(self) -> str:
        if not self.changed and not self.failed:
            return tr("No changes to make. Your mods folder already matches.")
        bits = []
        if self.renamed:
            bits.append(trn("renamed %(n)s folder", "renamed %(n)s folders", len(self.renamed)))
        if self.linked:
            bits.append(trn(
                "switched %(n)s library mod by its link",
                "switched %(n)s library mods by their links",
                len(self.linked),
            ))
        if self.listed and self.profile:
            bits.append(trn(
                "wrote %(n)s mod into %(profile)s", "wrote %(n)s mods into %(profile)s",
                self.listed, profile=self.profile.name,
            ))
        elif self.listed:
            bits.append(trn(
                "wrote %(n)s mod into the profile", "wrote %(n)s mods into the profile",
                self.listed,
            ))
        if self.unlisted:
            bits.append(trn(
                "took %(n)s mod out of activation.json",
                "took %(n)s mods out of activation.json",
                len(self.unlisted),
            ))
        if self.failed:
            bits.append(trn(
                "%(n)s could not be changed", "%(n)s could not be changed", len(self.failed),
            ))
        # After a colon: a list of what was done, not a sentence of it.
        if not self.changed:
            return tr("Your mods folder was not switched: %s.", ", ".join(bits))
        return tr("Switched your mods folder to match: %s.", ", ".join(bits))


def plan(current: FolderSwitches, wanted: Dict[str, bool]) -> Dict[str, bool]:
    """The mods whose switch would actually move, given what is wanted."""
    return {
        name: on
        for name, on in wanted.items()
        if name in current.on and current.on[name] != on
    }


def rewrite_profile(profile: ProfileFile, on: Dict[str, bool]) -> str:
    """The profile file's text with ``on`` (by lowercase ModID) applied.

    Rewritten line by line rather than regenerated: a profile is a file the user
    owns, with their own comments, their own ordering and a display name -- and
    an id the Studio has never heard of is one of the mods living in another of
    the folders 117 loads from, so dropping it would switch that mod off.
    """
    out = []
    seen = set()
    for line in profile.lines:
        body = line.strip()
        bare = body.lstrip("#").strip()
        key, note = _split_note(bare)
        if (
            not bare
            or body.startswith("##")
            or key in (NEW_MODS_LINE, "displayname")
            or key not in on
        ):
            out.append(line)
            continue
        seen.add(key)
        # The game's own note on the line stays with it: it is the game's to
        # write and the game's to take back, next time it reads the file.
        rewritten = key + ("  # " + note if note else "")
        out.append(rewritten if on[key] else "# " + rewritten)
    for key in sorted(set(on) - seen):
        # A line of its own, as in write_profile: none from a ModID that
        # would make more than one.
        if _CONTROL.search(key):
            continue
        out.append(key if on[key] else "# " + key)
    return "\n".join(out) + "\n"


def write(current: FolderSwitches, wanted: Dict[str, bool], ids=None) -> Written:
    """Switch ``current.folder``'s mods to ``wanted``, the way the game reads it.

    Nothing is deleted and nothing is rebuilt: for 1800 this renames folders, for
    117 it rewrites one text file.  Both are exactly what you would do by hand,
    and both are undone by doing them again.
    """
    moving = plan(current, wanted)
    written = Written()
    if not moving:
        return written

    if current.game == ANNO_117:
        # The host :func:`read` answered for it with; one with none here --
        # a mod.io cache mod below its numbered folder -- has a line of its own.
        hosts = {mod: switch_holder(mod, current.on) or mod for mod in moving}
    else:
        hosts = {mod: mod.split("/")[0] for mod in moving}
    inside = [mod for mod in moving if hosts[mod] != mod]
    for mod in inside:
        host = hosts[mod]
        # Both games read a bundled mod's switch off its host -- 1800 off its
        # folder, 117 off the host's line, never the bundled mod's own -- so a
        # host moving the same way in this call is the bundled mod switched,
        # and nothing to report.
        if moving.get(host) != moving[mod]:
            written.failed.append(
                (mod, tr("it is bundled inside %s, and switches with it", host))
            )
        moving.pop(mod)
    if not moving:
        return written

    if current.game != ANNO_117:
        for mod, on in sorted(moving.items()):
            source = Path(current.folders.get(mod) or (current.folder / mod))
            listing = current.disabled.get(mod)
            if on and listing is not None:
                # Off by an activation file: renaming the folder would leave it
                # off, so the entry comes out of the file.
                keys = {activation_key(mod, (ids or {}).get(mod, "")), activation_key(mod)}
                try:
                    if enable_in_activation(listing, keys):
                        written.unlisted.append((mod, listing))
                    else:
                        written.failed.append(
                            (mod, tr("%s lists it by another name", listing.name))
                        )
                        continue
                except (OSError, ValueError) as exc:
                    written.failed.append((mod, "%s: %s" % (listing.name, exc)))
                    continue
            from . import zipped

            real = zipped.zip_for(source) if zipped.is_mirrored(source) else None
            if real is not None:
                # A zipped mod: its switch is the zip's name, and the mirror
                # read in its place follows it.
                zip_target = real.parent / marked(real.name, on)
                if zip_target == real:
                    continue
                if zip_target.exists():
                    written.failed.append((mod, tr("%s is already there", zip_target.name)))
                    continue
                try:
                    real.rename(zip_target)
                except OSError as exc:
                    written.failed.append((mod, str(exc)))
                    continue
                written.renamed.append(
                    (mod, zipped.moved(real, zip_target) or source.parent / marked(source.name, on))
                )
                continue
            target = source.parent / marked(source.name, on)
            if source == target:
                continue
            if target.exists():
                written.failed.append((mod, tr("%s is already there", target.name)))
                continue
            try:
                source.rename(target)
            except OSError as exc:
                written.failed.append((mod, str(exc)))
                continue
            written.renamed.append((mod, target))
        return written

    if current.profile is None:
        written.failed.append(("", tr("there is no mod profile to write to")))
        return written
    def id_of(name):
        return ((ids or {}).get(name) or "").strip().lower()

    by_id = {}
    for name, on in moving.items():
        mod_id = id_of(name)
        if not mod_id:
            written.failed.append((name, tr("declares no ModID for the profile to name")))
            continue
        # One line switches every mod declaring its ModID -- a mod.io cache
        # copy and a local copy, say -- so one wanted the other way stops it.
        # A bundled mod's own line is not read, and so is no say.
        apart = sorted(
            other for other in current.on
            if other != name and id_of(other) == mod_id
            and switch_holder(other, current.on) in ("", other)
            and wanted.get(other, current.on[other]) != on
        )
        if apart:
            written.failed.append((name, trn(
                "%(others)s declares the same ModID, and the profile switches both with one line",
                "%(others)s declare the same ModID, and the profile switches them all with one line",
                len(apart), others=join_list(apart),
            )))
            continue
        by_id[mod_id] = on
    if not by_id:
        return written
    profile = read_profile(current.profile)
    try:
        disk.write_text(current.profile, rewrite_profile(profile, by_id))
    except OSError as exc:
        written.failed.append((current.profile.name, str(exc)))
        return written
    written.profile = current.profile
    written.listed = len(by_id)
    return written
