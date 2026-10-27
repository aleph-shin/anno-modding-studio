"""Read every mod, write it back out, read that again, and compare.

The Studio's claim about a mod it has read is that exporting it gives the game
the same thing the mod did.  The README measured that on the mods installed on
one machine; this measures it on as many as there are -- the census fetched by
``tools/modio_fetch.py``, or any mods folder.

Each unit (one unpacked zip, or a whole mods folder) is read the way
*Read a mods folder* reads it, at the settings that dialog opens with: one
session, its layers put in place, kept ops and changes taken in.  Every mod in it
that changes anything is written out the way the Export tab writes it, at the
settings *it* opens with, and the export is read again.  That happens twice:

``rules``
    What the Studio exports.  Kept ops go out as they were written, and an op
    that could not be read is carried exactly as written, where it sat.  This
    is the mode a unit's status is about, because it is what the Export tab
    writes.
``action``
    Every op that can be written narrow is (:func:`narrowable`), and the rest
    stay the ops they are.  What *Write as narrow ModOps* makes of the whole
    mod, and so its test.  The loader runs on it only on ``--loader-action``.

Two comparisons, because they answer different questions:

``changes``
    The two change sets, change for change.  Strict: a mod whose export splits
    one edit into two reads as different even when the game ends up identical.
``effect``
    Every asset either side touches, built the way the Studio shows it and
    compared as the game would be handed it.  This is the one that matters.

A round trip used to cover only what was read: an op the Studio could not read
was left out of the export, so it could not come back different.  It is carried
now, so there is a third comparison:

``carried``
    The ops kept as written -- the ones the Studio read and the ones it could
    not -- looked for, op for op, in the file that was written.  One that is not
    there is a mod that does less in game than the one that was opened,
    whatever the effect comparison says: the Studio never applies an op it
    could not read, so ``effect`` cannot see it go.  Whether the second reading
    makes the same thing of each is counted too, and is a lesser matter.

And a fourth, because a mod is more than its ModOps: ``modinfo`` compares each
exported ``modinfo.json`` with the one the mod shipped, key for key.

What the reading set aside without calling it an op -- a ``<Group>`` with a
condition, tweak-tool markup -- is in none of these.  It is counted under
``extras``, and the loader comparison below is what shows its cost.

A mod is read with every op kept as written (see
docs/proposals/ops-as-written-proposal.md), so nothing is rewritten and the
tallies above measure a rewrite that does not happen.  Two questions take
their place:

``as_written``
    Every op and group of each mod, laid out in file order with how deep in
    groups it sits, against the same of its export.  Expected to be the same
    of everything the Studio reads.  It is the mod's own file that is laid
    out and not what the Studio made of it, so an op the reading dropped
    without a word shows here where ``carried`` cannot see it.
the reading
    ``game_reading`` below: what the Studio shows against what the loader
    does.  The figure that matters most, since the reading is now the only
    thing the Studio works out.

    python tools/modio_roundtrip.py --census C:\\path\\to\\census
    python tools/modio_roundtrip.py --census ... --game anno-1800 --mod 3211479
    python tools/modio_roundtrip.py --folder "C:\\...\\Anno 1800\\mods" --game anno1800 --out C:\\somewhere

The checkbox on *Read a mods folder* changes what is read, and has a flag
here (``--keep-inert``) so a handful of units can be put through a setting
other than the default.  A census
is run at the defaults.

Both of those go through the Studio's own reading, so a misunderstanding its
reader and its writer share would come back identical.  ``--loader`` adds the
game's side: each unit is handed to the mod loader itself (``xmltest``; see
:mod:`modio_loader`) as shipped and as each export, and compared three ways:

``game_reading``
    The Studio's reading against the loader on the mod: how much of the mod
    the Studio understands.
``game_writing``
    The Studio's reading against the loader on the export *of what was read*:
    whether the ModOps the Studio writes do in game what it shows them doing.
    A difference here is a bug in the writing, whatever the reading missed.
    The carried ops are left out of the export this is measured on, since the
    Studio shows nothing for them by design -- for a unit with any, that is one
    more loader run.
``game_roundtrip``
    The loader on the mod against the loader on the export as the app writes
    it, carried ops and all: the whole trip, and the promise the export makes.

The loader is most of a census's time, and memory, not cores, is what limits
it: an Anno 1800 run holds the whole 284 MB file, up to 1.5 GB a process, so
``--loader-jobs`` runs at once share out slots. So each stack goes through it in
one run (:func:`modio_loader.apply_whole`) rather than a run per mod; what it
answers is kept in a cache beside the census (``--loader-cache``,
``--no-loader-cache``) under a digest of exactly what it was given, so a mod as
shipped is run once for every commit after, and so is an export that comes out
the same; the action's export goes through it only on ``--loader-action``; and
units start longest first, by the times a timings file beside the census keeps
(``--timings``), so the big bundles do not come up last and run alone.

Keep ``--out`` short.  Exports are written under it at the mod's own nested
folder names, and the deepest mod in the census already reaches 236
characters below ``C:\\rt`` -- Windows refuses a path of
260, and a unit that hits it is recorded as an error rather than a result.

Each unit runs in a process of its own with a time limit, so one mod that
takes forever or crashes costs that unit and not the run.  Results land one
JSON file per unit under ``<out>/<game>/results/``, a run picks up where the
last one stopped, and ``summary.txt`` beside them says what they add up to.
A unit that does not come back identical keeps its exports under
``<out>/<game>/work/<unit>/``, with only the XML and JSON of what the mod
shipped carried along (``--carry-all`` for the rest), so a run is the size of
the patches it compares and not of the mods' textures.  And a run starts by
deleting the runs beside it that are more than a week old (:func:`prune_runs`,
``--prune-days``), keeping the newest and any marked ``keep``.

One run at a time on a machine (:class:`RunLock`): a second started while one
is going refuses and says which, or with ``--wait`` starts when that one has
ended.  A run of both games, with no ``--game``, holds its place for both.

And the units running at once are held to the memory there is (``--memory``,
:func:`_next_that_fits`).  Most take next to nothing and a dozen take
gigabytes -- their own process, apart from the loader's -- and those are the
longest, so longest first started them all together.  What each took at its
most is kept beside its time (``<game>.memory.json``), and a unit whose share
does not fit beside what is running waits while smaller ones go ahead.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import difflib
import gc
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
for _where in (REPO, Path(__file__).resolve().parent):
    if str(_where) not in sys.path:
        sys.path.insert(0, str(_where))

import modio_loader  # noqa: E402
from lxml import etree  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    disk,
    games,
    generator,
    keptops,
    loaderlog,
    modfolder,
    modops,
    modzip,
    places,
    recording,
    repeats,
)
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import Loadout  # noqa: E402
from anno_modding_studio.core.loadercheck import (  # noqa: E402,F401 -- prepared, pretty: the tests reach them here
    Views,
    canonical_fragment,
    compare_views,
    mark,
    prepared,
    pretty,
    read_folders,
    strip_inert,
)
from anno_modding_studio.core.workspace import Workspace  # noqa: E402

# The census folder names the fetcher uses, and the game each one is.
CENSUS_GAMES = {"anno-1800": games.ANNO_1800, "anno-117-pax-romana": games.ANNO_117}
# The mode the Export tab writes, and so the one a unit's status is about.
APP = "rules"
# The second export: see narrowable().
ACTION = "action"
# The two exports a unit is put through.
MODES = (APP, ACTION)
# A third export, made only when a unit has carried ops and the loader is on:
# the ``rules`` mode without them, which is what ``game_writing`` is measured on.
READ_ONLY = "read"
# An asset nothing can find by its GUID any more: removed, or stripped of it.
EXAMPLES = 8
DIFF_LINES = 40
REASONS = 15
TIMEOUT = 900
# An Anno 1800 xmltest run peaks at about 1.5 GB.
LOADER_JOBS = 8
RESULTS = "results"
WORK = "work"
# What an export carries of what its mod shipped: only what the reading and
# the loader can see, which is the files xmltest is handed.  A mod's textures
# and models are most of its size and nothing here compares them, and every
# unit that does not come back identical keeps its exports -- three copies of
# the mod each, which made a census run 5.4 GB.
CARRIED = tuple(modio_loader.COPIED)
# A run beside this one that has had nothing written to it for this many days
# is deleted when this one starts: see prune_runs().
PRUNE_DAYS = 7
# A file of this name at the top of a run keeps it, however old.
KEEP = "keep"
# How often a run told to ``--wait`` looks whether the one ahead has ended.
WAIT = 5.0
# What a unit never run before is taken to need, in megabytes: nine in ten
# need less, and the few that need gigabytes are known after one run.
UNKNOWN_MB = 150


@dataclasses.dataclass
class How:
    """The checkboxes: how the mods are read.

    The defaults are the app's own -- what *Read a mods folder* opens with --
    and a census is only ever run at them.
    """

    keep_inert: bool = False

    def changed(self) -> Dict[str, bool]:
        return {
            name: value
            for name, value in dataclasses.asdict(self).items()
            if value != getattr(How(), name)
        }


# ------------------------------------------------------------------ reading
def base_assets(game: str) -> Path:
    """Where the Studio extracted this game's assets.xml."""
    profile = games.get(game)
    safe = re.sub(r'[<>:"/\\|?*]', "_", profile.label)
    return places.extracted_dir() / safe / profile.asset_relpath


def open_index(source: Path) -> index_mod.AssetIndex:
    db = index_mod.cache_path_for(source)
    if not db.exists():
        index_mod.build_index(source, db)
    return index_mod.AssetIndex(db, source)


def folders_of(files: Path) -> Tuple[List[Path], List[Path]]:
    """The mod folders in one unpacked zip, and the root their names hang off.

    Most zips hold a mod folder; some hold the mod's own contents at the top,
    in which case the zip's root is the mod. And some hold a folder that is not
    a mod but a box of them -- *More Trade Contracts* ships two mods inside one
    folder with no data of its own -- which a player installs by taking the
    mods out of it, so they are read as those mods. Read as one, the Studio
    found both patches but the loader was handed a folder with none, and ran
    nothing at all.

    Which folders those are is the app's own zip install's answer
    (:func:`modzip.mods_in`), so the census reads what a player installing
    the zip through the Studio would have, each with the mods bundled in it.
    A box is a root of its own: a mod taken out of it is named, and switched
    off, by its own folder, not the box's.

    Except where that would give two of the zip's mods one name. A zip
    offering variants (``Option A/Mod``, ``Option B/Mod``) holds two mods of
    one folder name, which the install could not both put in place: named by
    their folders alone they were one mod twice over and the round trip
    failed. So a box holding a mod whose name another mod of the zip shares
    is no root, and its mods keep the box in their names, as two mods.
    """
    mods = modzip.mods_in(files)
    if mods == [files]:
        return [files, *modfolder.bundled_in(files)], [files.parent]
    found: List[Path] = []
    for mod in mods:
        for each in [mod, *modfolder.bundled_in(mod)]:
            if each not in found:
                found.append(each)
    # The name the game keys on; and Windows folds case.
    def key(mod: Path) -> str:
        return modfolder.bare_name(mod.name).casefold()

    named = Counter(key(mod) for mod in mods)
    shared = {mod.parent for mod in mods if named[key(mod)] > 1}
    boxes = sorted({mod.parent for mod in mods} - {files} - shared)
    return found, [files, *boxes]


def read(folders, roots, index, game: str, logged=None, how: Optional[How] = None):
    """A session read, and the loadout the mods-folder import would make of it."""
    how = how or How()
    return read_folders(folders, roots, index, game, logged=logged, keep_inert=how.keep_inert)


def export(
    loadout: Loadout,
    dest: Path,
    mode: str = APP,
    how: Optional[How] = None,
    sources: Optional[dict] = None,
    carry_all: bool = False,
) -> List[str]:
    """Write every mod that changes anything, the way the Export tab does.

    ``rules`` is that and nothing else.  ``read`` leaves out the ops carried
    because they could not be read.  ``action`` is ``rules`` with the ops
    that can be written narrow written so: see :func:`narrowable`.

    ``sources`` maps each origin to the folder it was read out of, and the
    export carries what that mod ships besides its patch -- which is what the
    Export tab does, so it is what this has to do to be measuring the same
    thing.  It matters to the loader in one place: an op carried as written
    may hold an ``<Include>``, and the file it names now travels with it.
    Only the files in :data:`CARRIED` travel, unless ``carry_all``.
    """
    how = how or How()
    sources = sources or {}
    held_ops, kept_unread = loadout.ops, loadout.unread
    if mode == ACTION:
        gone = narrowable(loadout)
        loadout.ops = [op for op in held_ops if op.uid not in gone]
    if mode not in (APP, ACTION):
        loadout.unread = []
    try:
        # The Export tab's own test of what there is to write: see
        # ``OutputPanel.writable_origins``.  Nothing here is switched off and
        # nothing is a patch, which are the other two things it asks.
        origins = [
            origin
            for origin in loadout.origins(loaded=True)
            if generator.writes_anything(generator.for_mod(loadout, origin))
        ]
        for origin in origins:
            exported = generator.for_mod(loadout, origin)
            # As the app's export writes it: the patch, and each file it
            # includes.
            whole = generator.written(exported, files=True)
            dest.mkdir(parents=True, exist_ok=True)
            generator.write_mod_folder(
                exported,
                dest,
                # The same text where the mod is its patch alone.
                whole.text if whole.files
                else generator.build_modops_xml(exported),
                folder_name=generator.folder_for(loadout, origin),
                carry_from=sources.get(origin),
                carry_only=None if carry_all else CARRIED,
                files=whole.files,
                leave=generator.kept_by_mods_inside(loadout, origin),
            )
    finally:
        loadout.ops, loadout.unread = held_ops, kept_unread
    return origins


# ---------------------------------------------------------------- comparing
# The comparison helpers are the app's own now: see core.loadercheck.


def signature(change) -> tuple:
    """What a change does, without the identity it was given when it was read."""
    return (
        change.origin,
        change.guid,
        change.path,
        change.op,
        change.tag,
        (change.old_value or "").strip(),
        (change.new_value or "").strip(),
        canonical_fragment(change.payload),
        bool(change.positional),
    )


def describe(sig: tuple) -> str:
    origin, guid, path, op, tag, _old, new, payload, _positional = sig
    what = new or payload
    return "%s | %s %s %s | %s" % (origin, guid, op, path or tag, what[:140])


def compare_changes(before: Loadout, after: Loadout) -> Tuple[Counter, Counter]:
    one = Counter(signature(change) for change in before.active())
    two = Counter(signature(change) for change in after.active())
    return one - two, two - one


def carried(loadout: Loadout) -> Counter:
    """Every op a loadout keeps as written: what could not be read, and the rest."""
    found: Counter = Counter()
    for entry in loadout.unread:
        found[("unread", entry.origin, canonical_fragment(entry.source))] += 1
    for op in loadout.ops:
        if op.includes or op.aside:
            # A line that names a file, or a comment of the file's, and no
            # op: ``as_written`` has it, where it stands.
            continue
        kind = "rule" if op.active else "rule, off"
        found[(kind, op.origin, canonical_fragment(op.source))] += 1
    return found


def narrowable(loadout: Loadout) -> set:
    """The uids of the ops *Write as narrow ModOps* takes when it is asked of
    every mod: what the ``action`` export leaves to their changes.

    The app's own answer (:func:`keptops.narrowings`), so that what is
    measured is the action and not a guess at it.  An op that is its one
    change already is among them: the action on a mod passes those over as
    having nothing to gain, and written narrow they are the plainest test of
    the Studio's own writing there is.
    """
    return {found.op.uid for found in keptops.narrowings(loadout) if found.possible}


def laid_out(folder: Path) -> List[Tuple[str, int, str]]:
    """Every op and group of a mod in file order: which of the mod's files
    it is in, how deep in groups it sits, and what it says.  A group is its
    own tag with nothing in it, and what is in it follows one deeper.

    Parsed as the reader parses, so that an ``<Include>`` is followed on both
    sides of a comparison; nothing of what the reader then makes of an op is
    in it.
    """
    return [
        # A comment of the file's is what it says, which the canonical form
        # of a fragment leaves out.
        (file.lower(), depth,
         " ".join(text.split()) if text.lstrip().startswith("<!--") else canonical_fragment(text))
        for file, depth, text in modops.laid_out(Path(folder))
    ]


def compare_as_written(reads, exported) -> Dict[str, Any]:
    """Each mod's ops as its file has them against its export's.

    ``reads`` is the session's mods and ``exported`` the folder each was
    written to, by origin (None for one not written).  A mod is ``same``,
    ``moved`` -- every op there and the order or the depth not -- or
    ``differs``.
    """
    found: Dict[str, Any] = {
        "mods": 0, "ops": 0, "same": 0, "moved": 0, "differs": 0, "missing": 0, "extra": 0,
        "examples": [],
    }
    for mod in reads:
        before = laid_out(Path(mod.folder))
        if not before:
            continue
        there = exported.get(mod.origin)
        after = laid_out(there) if there is not None and there.is_dir() else []
        gone, new = Counter(before) - Counter(after), Counter(after) - Counter(before)
        state = "same" if before == after else "differs" if gone or new else "moved"
        found["mods"] += 1
        found["ops"] += len(before)
        found[state] += 1
        found["missing"] += sum(gone.values())
        found["extra"] += sum(new.values())
        for sign, some in (("-", gone), ("+", new)):
            for (file, depth, text), count in list(some.items())[:2]:
                if len(found["examples"]) < EXAMPLES:
                    found["examples"].append(
                        "%s | %s %d at depth %d in %s | %s"
                        % (mod.origin, sign, count, depth, file or "the patch", text[:200])
                    )
        if state == "moved" and len(found["examples"]) < EXAMPLES:
            at = next(i for i, pair in enumerate(zip(before, after, strict=True)) if pair[0] != pair[1])
            found["examples"].append(
                "%s | moved, first at op %d | %s" % (mod.origin, at + 1, before[at][2][:200])
            )
    return found


def written_ops(patch: Path, seen: Optional[set] = None) -> Counter:
    """Everything standing under ``<ModOps>`` in a written patch: what stands
    directly under it, and for a group also its own tag with nothing in it
    and each thing inside it, since a group kept as a group with ops in it is
    carried as those.  And the same of each file it includes, since an
    export writes a mod as its files."""
    found: Counter = Counter()
    seen = set() if seen is None else seen
    seen.add(Path(patch).resolve())
    try:
        # Opened the long way round: a file a mod keeps three folders down
        # is past what Windows opens by its plain name under a census's
        # work folder, and the main patch never was.
        root = etree.parse(
            str(disk.reachable(Path(patch))),
            etree.XMLParser(huge_tree=True, resolve_entities=False),
        ).getroot()
    except (OSError, etree.XMLSyntaxError):
        return found

    def take(parent) -> None:
        for node in parent:
            if not isinstance(node.tag, str):
                continue
            found[canonical_fragment(etree.tostring(node, encoding="unicode", with_tail=False))] += 1
            if modops.is_include(node) and modops.include_file(node):
                try:
                    named = modops.include_target(
                        modops.include_file(node), Path(patch), modops.mod_root(Path(patch))
                    )
                except (OSError, ValueError):
                    continue
                if os.path.isfile(disk.reachable(named)) and named not in seen:
                    found.update(written_ops(named, seen))
            if node.tag.lower() == "group":
                bare = etree.Element(node.tag, dict(node.attrib))
                found[canonical_fragment(etree.tostring(bare, encoding="unicode"))] += 1
                take(node)

    take(root)
    return found


def compare_carried(before: Loadout, after: Loadout, written: Dict[str, Counter]) -> Dict[str, Any]:
    """The ops kept as written: whether each is in the file, and how it read again.

    ``written`` is what each exported mod's patch holds, by origin.  ``missing``
    counts the carried ops that are not in it, and is what makes a unit differ.
    ``became`` is the second reading: an unread op it *could* read, a kept op
    that came back as plain changes because beside the ops now round it its narrow
    form adds up.  Worth seeing, and not a loss: the op is in the file.
    """
    lost: Counter = Counter()
    left = {origin: Counter(ops) for origin, ops in written.items()}
    for (kind, origin, text), count in carried(before).items():
        if kind == "rule, off":
            continue  # switched off here, so meant to be left out
        have = left.get(origin, Counter())
        short = count - min(count, have[text])
        have[text] -= min(count, have[text])
        if short:
            lost[(kind, origin, text)] += short
    one, two = carried(before), carried(after)
    gone, extra = one - two, two - one
    became: Counter = Counter()
    for kind, origin, text in gone:
        others = [k for (k, o, t) in extra if o == origin and t == text]
        became["%s -> %s" % (kind, others[0] if others else "read as changes, or not at all")] += gone[
            (kind, origin, text)
        ]
    return {
        "kept": sum(one.values()) - sum(lost.values()),
        "missing": sum(lost.values()),
        "reread_differently": sum(gone.values()),
        "became": dict(became),
        "missing_examples": ["%s | %s | %s" % (k, o, t[:200]) for k, o, t in list(lost)[:EXAMPLES]],
        "reread_examples": ["%s | %s | %s" % (k, o, t[:200]) for k, o, t in list(gone)[:EXAMPLES]],
    }


def _modinfo(folder: Path) -> Tuple[Optional[dict], str]:
    """A mod's modinfo as its author's keys and values, and the file it came from."""
    for name in modfolder.MODINFO:
        path = folder / name
        if path.is_file():
            return modfolder.read_metadata(path).get("declared"), name
    return None, ""


def _flat(value, prefix: str = "") -> Dict[str, Any]:
    """``{"ModName": {"English": "x"}}`` as ``{"ModName.English": "x"}``."""
    if isinstance(value, dict) and value:
        found: Dict[str, Any] = {}
        for key, inner in value.items():
            found.update(_flat(inner, "%s.%s" % (prefix, key) if prefix else str(key)))
        return found
    return {prefix: value}


def compare_modinfo(original: Path, exported: Path) -> Dict[str, Any]:
    """What an export did to one mod's modinfo, key by key.

    ``modinfo.jsonc`` is read as the JSON it is.  The export is always written
    as ``modinfo.json``, which is said rather than counted as a change.
    """
    before, name = _modinfo(original)
    after, _name = _modinfo(exported)
    if before is None:
        # None shipped, or one that does not parse: either way there was
        # nothing of the author's to keep.
        return {"state": "created" if after is not None else "none"}
    if after is None:
        return {"state": "lost"}
    one, two = _flat(before), _flat(after)
    found: Dict[str, Any] = {
        "state": "kept",
        "renamed": name != "modinfo.json",
        "lost": sorted(key for key in one if key not in two),
        "added": sorted(key for key in two if key not in one),
        "changed": sorted(key for key in one if key in two and one[key] != two[key]),
    }
    if found["lost"] or found["added"] or found["changed"]:
        found["state"] = "changed"
        found["examples"] = [
            "%s: %r -> %r" % (key, one.get(key, "(absent)"), two.get(key, "(absent)"))
            for key in (found["lost"] + found["added"] + found["changed"])[:EXAMPLES]
        ]
    return found


def compare_effect(
    index, before: Loadout, after: Loadout, seen: Optional[dict] = None
) -> Tuple[int, int, List[dict]]:
    """How many assets the game would be handed differently, and a few of them.

    Two counts.  The first compares the assets the way the Studio itself decides
    two readings mean the same thing: with the list entries the game reads as
    absent -- a cost with no amount, an item pool entry with no weight -- taken
    out, since one reading keeping such an entry and the other removing it is
    no difference in game.  The second is strict, so that an ``Inert`` judgement
    that is wrong still shows up somewhere.

    ``seen`` keeps what ``before`` made of each asset, for a loadout compared
    with more than one export of itself: a unit's two modes share most of
    their assets, and building one is most of the comparison.
    """
    old, new = Workspace(index, before), Workspace(index, after)
    inert = recording.Inert(index)
    guids = {c.guid for c in before.active()} | {c.guid for c in after.active()}
    # Neither loadout moves while it is compared, so each is fingerprinted once
    # rather than once per asset -- which, asset by asset over a mod touching
    # eighteen thousand of them, was most of an hour.
    with old.steady(), new.steady():
        return _compare_effect(old, new, inert, guids, {} if seen is None else seen)


def _effect(workspace, inert, guid) -> Tuple[Optional[str], Optional[str]]:
    """An asset as a loadout leaves it: exactly, and with what the game reads
    as absent taken out."""
    element = workspace.effective_asset(guid).as_the_game_reads_it()
    exact = pretty(element)
    if element is None or not strip_inert(element, inert):
        return exact, exact
    return exact, pretty(element)


def _compare_effect(old, new, inert, guids, seen) -> Tuple[int, int, List[dict]]:
    count, strict, examples = 0, 0, []
    for guid in sorted(guids, key=lambda g: (len(g), g)):
        if guid not in seen:
            seen[guid] = tuple(mark(text) for text in _effect(old, inert, guid))
        exact, second = _effect(new, inert, guid)
        if seen[guid][0] != mark(exact):
            strict += 1
        if seen[guid][1] == mark(second):
            continue
        count += 1
        if len(examples) < EXAMPLES:
            first = _effect(old, inert, guid)[1]
            diff = difflib.unified_diff(
                (first or "").splitlines(), (second or "").splitlines(),
                "read", "exported and read again", n=1, lineterm="",
            )
            examples.append({"guid": guid, "diff": list(diff)[:DIFF_LINES]})
    return count, strict, examples


@dataclasses.dataclass
class LoaderSetup:
    """The game's loader, the file it patches, and how many may run at once.

    ``cache``: where :func:`modio_loader.apply_whole` keeps what it has been
    asked before. ``action``: whether the action's export goes through the
    loader too -- a quarter of the loader's time, so only on asking.
    """

    exe: Path
    target: Path
    slot: Any = None
    cache: Optional[Path] = None
    action: bool = False


def _loaded(index, assets: Dict[str, Any]):
    """What the loader left each asset as: its own output where it changed it."""
    return lambda guid: assets[guid] if guid in assets else index.asset_element(guid)


def _applied(applied: modio_loader.Applied) -> Dict[str, Any]:
    return {
        "runs": applied.runs,
        "seconds": applied.seconds,
        "changed": len(applied.assets),
        "failures": applied.failures[:EXAMPLES],
        "log": dict(applied.log.most_common(REASONS)),
        "options_written": applied.options_written,
        "options_unknown": applied.options_unknown[:EXAMPLES],
        "cached": applied.cached,
        "fallback": list(applied.fallback),
    }


def _normal(text: str) -> str:
    """A reason with its particulars taken out, so like reasons count together."""
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "'...'", str(text))
    return re.sub(r"\d+", "#", text)[:160]


def _extra_kind(extra) -> str:
    values = [str(v) for v in dataclasses.asdict(extra).values()]
    first = re.sub(r"<(\w+)[^>]*>", r"<\1>", values[0]) if values else ""
    return _normal("%s: %s" % (first, values[-1] if len(values) > 1 else ""))


# -------------------------------------------------------------- one unit
def run_unit(
    unit: Dict[str, Any],
    index,
    game: str,
    work: Path,
    keep: bool = False,
    loader: Optional[LoaderSetup] = None,
    how: Optional[How] = None,
    carry_all: bool = False,
) -> Dict[str, Any]:
    started = time.monotonic()
    how = how or How()
    result: Dict[str, Any] = {"unit": unit["id"], "name": unit.get("name"), "game": game}
    if how.changed():
        result["how"] = how.changed()
    if "files" in unit:
        folders, roots = folders_of(Path(unit["files"]))
    else:
        folders, roots = modfolder.mod_folders(Path(unit["folder"])), [Path(unit["folder"])]

    session, loadout = read(folders, roots, index, game, unit.get("logged"), how)
    result["reads"] = "written"
    results = [r for mod in session.reads for r in mod.report.results]
    blocked = Counter(_normal(r.reason) for r in results if r.status == modops.BLOCKED)
    extras = Counter(_extra_kind(e) for mod in session.reads for e in mod.report.extras)
    result.update(
        mods=len(session.reads),
        skipped=[[str(folder), why] for folder, why in session.skipped][:EXAMPLES],
        duplicates=len(session.duplicates),
        ops=len(results),
        imported=sum(1 for r in results if r.status == modops.IMPORTED),
        settled=sum(1 for r in results if r.status == modops.SETTLED),
        blocked=sum(blocked.values()),
        blocked_reasons=dict(blocked.most_common(REASONS)),
        extras=dict(extras.most_common(REASONS)),
        changes=len(loadout.active()),
        rules=len(loadout.ops),
        rules_whole=len(keptops.exportable(loadout)[0]),
        rules_kept_as_ops=sum(1 for op in loadout.ops if op.kept),
        rules_off=sum(1 for op in loadout.ops if not op.active),
        modes={},
    )
    # What the changes panel warns about: assets under a GUID something else
    # has, which the game can reach only one of. A survey of it, for free.
    taken = repeats.find(loadout, index)
    result["repeats"] = {
        "taken": sum(1 for repeat in taken if not repeat.harmless),
        "tidied": sum(1 for repeat in taken if repeat.tidied),
        "same": sum(1 for repeat in taken if repeat.same and not repeat.tidied),
        "apart": sum(1 for repeat in taken if repeat.apart and not repeat.harmless),
        "examples": [
            "%s: %s%s -> %s" % (
                repeat.guid,
                " / ".join(copy.name for copy in repeat.copies),
                " (and the base game's %s)" % repeat.shipped if repeat.shipped else "",
                "in two top-level groups, so which is found depends on the ops before"
                if repeat.apart else "the game finds %s" % repeat.found,
            )
            for repeat in taken if not repeat.harmless
        ][:EXAMPLES],
    }
    if not results:
        result["status"] = "no-patch"
        result["seconds"] = round(time.monotonic() - started, 1)
        return result

    order = [mod.origin for mod in session.reads]
    folders_by_origin = {mod.origin: Path(mod.folder) for mod in session.reads}
    folder = work / unit["id"]
    # Which mods a ``#ModID`` condition finds loaded, as the Studio's reading
    # counted them and the app's own loader check tells the loader: inside
    # xmltest every one is false otherwise. The export is the same stack, so
    # it is told the same.
    present = frozenset(
        value.lower()
        for mod in session.reads
        for value in (mod.report.metadata.get("mod_id", ""), mod.origin)
        if value
    )
    if loader is not None:
        inert = recording.Inert(index)
        original = modio_loader.apply_whole(
            loader.exe, loader.target, [mod.folder for mod in session.reads], roots[0], game,
            loader.slot, present=present, cache=loader.cache,
        )
        studio = Workspace(index, loadout)
        # Each is compared three times over, and neither moves: the loadout
        # is as it was read from here on, and the loader has answered.
        shows = Views(lambda guid: studio.effective_asset(guid).as_the_game_reads_it(), inert)
        did = Views(_loaded(index, original.assets), inert)
        with studio.steady():
            reading = compare_views(
                set(original.assets) | {change.guid for change in loadout.active()},
                shows,
                did,
                inert,
                ("the Studio's reading", "the loader, on the mod"),
            )
        result["loader"] = {
            "build": loader.exe.name,
            "original": _applied(original),
            "reading": reading,
        }

    def stack_of(dest: Path, written) -> List[Path]:
        return [
            dest / generator.folder_for(loadout, origin)
            for origin in order
            if origin in written and (dest / generator.folder_for(loadout, origin)).is_dir()
        ]

    def loaded(dest: Path, written) -> modio_loader.Applied:
        assert loader is not None
        exported = stack_of(dest, written)
        if not exported:
            return modio_loader.Applied()
        return modio_loader.apply_whole(
            loader.exe, loader.target, exported, dest, game, loader.slot, present=present,
            cache=loader.cache,
        )

    result["narrowable"] = len(narrowable(loadout))
    effects: dict = {}
    for mode in MODES:
        dest = folder / mode
        shutil.rmtree(dest, ignore_errors=True)
        written = export(loadout, dest, mode, how, folders_by_origin, carry_all)
        again_folders = modfolder.mod_folders(dest) if dest.is_dir() else []
        again_session, again = read(again_folders, [dest], index, game, logged=order, how=how)
        missing, extra = compare_changes(loadout, again)
        effect, strict, examples = compare_effect(index, loadout, again, effects)
        result["modes"][mode] = {
            "exported": len(written),
            "reread_changes": len(again.active()),
            "reread_blocked": sum(mod.unread for mod in again_session.reads),
            "order_kept": [mod.origin for mod in again_session.reads if mod.origin in order]
            == [origin for origin in order if origin in set(written)],
            "missing": sum(missing.values()),
            "extra": sum(extra.values()),
            "missing_examples": [describe(s) for s in list(missing)[:EXAMPLES]],
            "extra_examples": [describe(s) for s in list(extra)[:EXAMPLES]],
            "effect_assets": effect,
            "effect_assets_strict": strict,
            "effect_examples": examples,
        }
        if mode == APP:
            # The mod's own file against the export's, op for op.
            result["as_written"] = compare_as_written(
                session.reads,
                {
                    origin: dest / generator.folder_for(loadout, origin) if origin in written else None
                    for origin in order
                },
            )
            # Only here: the other mode leaves the carried ops out on purpose.
            relpath = games.get(game).asset_relpath
            result["carried"] = compare_carried(
                loadout,
                again,
                {
                    origin: written_ops(dest / generator.folder_for(loadout, origin) / relpath)
                    for origin in written
                },
            )
            # A mod with ops that the export still leaves out. There should be
            # none: one with nothing but carried ops used to be passed over,
            # because the Export tab only wrote a mod that had changes.
            result["left_out"] = [
                mod.origin
                for mod in session.reads
                if mod.origin not in written
                and (mod.unread or any(op.origin == mod.origin for op in loadout.ops))
            ][:EXAMPLES]
            infos = Counter()
            keys: Counter = Counter()
            info_examples: List[str] = []
            for origin in written:
                found = compare_modinfo(
                    folders_by_origin[origin], dest / generator.folder_for(loadout, origin)
                )
                infos[found["state"]] += 1
                if found.get("renamed"):
                    infos["modinfo.jsonc written back as modinfo.json"] += 1
                for kind in ("lost", "added", "changed"):
                    keys.update("%s %s" % (kind, key) for key in found.get(kind, []))
                for example in found.get("examples", []):
                    if len(info_examples) < EXAMPLES:
                        info_examples.append("%s | %s" % (origin, example))
            result["modinfo"] = {"mods": dict(infos), "keys": dict(keys), "examples": info_examples}
        if loader is not None and (mode == APP or loader.action):
            applied = loaded(dest, written)
            summary = _applied(applied)
            does = Views(_loaded(index, applied.assets), inert)
            summary.update(
                compare_views(
                    set(original.assets) | set(applied.assets),
                    did,
                    does,
                    inert,
                    ("the loader, on the mod", "the loader, on the export"),
                )
            )
            summary["log_added"] = dict((applied.log - original.log).most_common(REASONS))
            # The export against what the Studio says it does: a reading gap makes
            # the export differ from the mod without the export being wrong, so
            # only this one is about the writing. Measured on an export without
            # the carried ops, because the Studio shows nothing for those.
            shown = applied
            if mode == APP and loadout.unread:
                bare = folder / READ_ONLY
                shutil.rmtree(bare, ignore_errors=True)
                shown = loaded(bare, export(loadout, bare, READ_ONLY, how, folders_by_origin, carry_all))
                summary["writing_failures"] = shown.failures[:EXAMPLES]
                does = Views(_loaded(index, shown.assets), inert)
            # The action's export has the carried ops in it, as the app's
            # would, and the Studio shows nothing for those: where there are
            # any, what it is measured by is the loader on the mod, above.
            if not (mode == ACTION and loadout.unread):
                with studio.steady():
                    summary["writing"] = compare_views(
                        set(shown.assets) | {change.guid for change in loadout.active()},
                        shows,
                        does,
                        inert,
                        ("the Studio's reading", "the loader, on the export of what was read"),
                    )
            result["loader"][mode] = summary
        # Nothing asks for this mode's reading or its loader answers again, and
        # for a mod touching eighteen thousand assets each is most of a
        # gigabyte: kept until the names were next given, the two modes' stood
        # in memory together, and a unit that needs 3 GB held 4.
        again_session = again = applied = shown = does = summary = None
        gc.collect()

    app, other = result["modes"][APP], result["modes"][ACTION]
    lost = result["carried"]["missing"]
    wrote = result["as_written"]
    # An op of the mod that is not in the export as it was is a difference
    # whatever the Studio makes of the two.
    if app["effect_assets"] or lost or wrote["differs"]:
        result["status"] = "differs"
    elif (
        app["missing"] or app["extra"] or result["carried"]["reread_differently"]
        or wrote["moved"]
    ):
        result["status"] = "same-effect"
    else:
        result["status"] = "identical"
    result[ACTION] = (
        "differs" if other["effect_assets"]
        else "same-effect" if other["missing"] or other["extra"]
        else "identical"
    )
    if "loader" in result:
        found = result["loader"]
        measured = [mode for mode in MODES if mode in found]
        broken = bool(found["original"]["failures"])
        result["game_reading"] = "failed" if broken else ("differs" if found["reading"]["assets"] else "same")
        if broken or any(found[mode]["failures"] or found[mode].get("writing_failures") for mode in measured):
            result["game_writing"] = result["game_roundtrip"] = "failed"
        else:
            result["game_writing"] = (
                "differs"
                if any((found[mode].get("writing") or {}).get("assets") for mode in measured)
                else "same"
            )
            result["game_roundtrip"] = "differs" if found[APP]["assets"] else "same"
        if ACTION in found:
            # The loader on the mod against the loader on the mod written
            # narrow where it can be.
            result["game_action"] = (
                "failed" if broken or found[ACTION]["failures"]
                else "differs" if found[ACTION]["assets"] else "same"
            )
    settled = all(
        result.get(key, "same") == "same" for key in ("game_writing", "game_roundtrip", "game_action")
    )
    if result["status"] == "identical" and result[ACTION] == "identical" and settled and not keep:
        shutil.rmtree(folder, ignore_errors=True)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _child(unit, game, source, work, keep, result_path, loader=None, how=None, carry_all=False) -> None:
    try:
        index = open_index(Path(source))
        result = run_unit(unit, index, game, Path(work), keep, loader, how, carry_all)
    except Exception:  # noqa: BLE001 -- a unit that breaks is a result, not the end of the run
        result = {
            "unit": unit["id"],
            "name": unit.get("name"),
            "game": game,
            "status": "error",
            "error": traceback.format_exc()[-6000:],
        }
    _write_json(Path(result_path), result)


# ---------------------------------------------------------------- old runs
def _results_of(folder: Path) -> List[Path]:
    """The results folders of a run: ``<game>/results`` for each game, in the
    names a census or a mods folder is run under.  None, for anything else."""
    names = set(CENSUS_GAMES) | {games.ANNO_1800, games.ANNO_117}
    with contextlib.suppress(OSError):
        return [child / RESULTS for child in folder.iterdir()
                if child.name in names and (child / RESULTS).is_dir()]
    return []


def _last_written(results: List[Path]) -> float:
    """When anything was last written to a run: its newest result or summary."""
    newest = 0.0
    for folder in results:
        for path in [*folder.glob("*.json"), folder.parent / "summary.txt"]:
            with contextlib.suppress(OSError):
                newest = max(newest, path.stat().st_mtime)
    return newest


def prune_runs(run: Path, days: float, now: Optional[float] = None) -> List[Path]:
    """Delete the runs beside ``run`` that are more than ``days`` old.  Which
    ones it deleted.

    Each run is a folder of its own beside the others (``rt-<commit>`` by
    habit), and they piled up: two hundred gigabytes before exports stopped
    carrying textures, and half a gigabyte a run after.  A run here is a
    folder with a ``<game>/results`` folder in it, which is the shape this
    harness writes and nothing else beside it has -- not the census, the
    loader cache or a frozen source.  Its age is the last time a result was
    written to it, so a run still going is never old.

    Kept whatever their age: the newest of them, which is the baseline the
    run starting now will be compared with, and any with a file called
    ``keep`` (or ``keep.txt``) at its top.  Deleted outright and not to the Recycle Bin, which
    would free nothing.
    """
    run = run.resolve()
    now = time.time() if now is None else now
    found = []
    with contextlib.suppress(OSError):
        for folder in run.parent.iterdir():
            if not folder.is_dir() or folder.resolve() == run:
                continue
            results = _results_of(folder)
            if results:
                found.append((_last_written(results), folder))
    found.sort(reverse=True)
    gone = []
    for written, folder in found[1:]:
        if now - written <= days * 86400:
            continue
        with contextlib.suppress(OSError):
            if any(path.stem.lower() == KEEP for path in folder.iterdir() if path.is_file()):
                continue
        shutil.rmtree(disk.reachable(folder), ignore_errors=True)
        if not folder.exists():
            gone.append(folder)
    return gone


# ---------------------------------------------------------------- the run
def first_units(folder: Path, limit: Optional[int] = None) -> List[Path]:
    """The folders of one game's census units that hold files, by mod.io id,
    the first ``limit`` where it is given: what "the first N mods of the
    census" means, for the tools that take a part of it (``bench``,
    ``op_counts``).  What a unit holds is :func:`folders_of` its ``files``."""
    units = sorted(
        (unit for unit in folder.iterdir() if (unit / "files").is_dir()),
        key=lambda unit: int(unit.name) if unit.name.isdigit() else 0,
    ) if folder.is_dir() else []
    return units if limit is None else units[:limit]


def census_units(folder: Path) -> List[Dict[str, Any]]:
    units = []
    for mod in sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name):
        files = mod / "files"
        if not files.is_dir():
            continue
        name = mod.name
        with contextlib.suppress(OSError, ValueError):
            name = json.loads((mod / "manifest.json").read_text(encoding="utf-8")).get("name") or name
        units.append({"id": mod.name, "name": name, "files": str(files)})
    return units


def summarise(results_dir: Path, label: str) -> str:
    results = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    statuses = Counter(r.get("status") for r in results)
    read = [r for r in results if r.get("status") in ("identical", "same-effect", "differs")]
    fully = [r for r in read if not r.get("blocked")]
    reasons: Counter = Counter()
    extras: Counter = Counter()
    for r in results:
        reasons.update(r.get("blocked_reasons") or {})
        extras.update(r.get("extras") or {})
    ops = sum(r.get("ops", 0) for r in results)
    blocked = sum(r.get("blocked", 0) for r in results)

    hows = Counter(json.dumps(r.get("how") or {}, sort_keys=True) for r in results)
    lines = [
        label,
        "  units: %d  (%s)" % (len(results), ", ".join("%s %d" % kv for kv in statuses.most_common())),
        "  ops read: %d, of which the Studio could not read %d (%.1f%%)"
        % (ops, blocked, 100.0 * blocked / ops if ops else 0),
        "  units read with nothing left out: %d of %d" % (len(fully), len(read)),
    ]
    if set(hows) != {"{}"}:
        lines.append("  NOT ALL AT THE APP'S DEFAULTS: %s" % ", ".join("%s x%d" % kv for kv in hows.most_common()))
    written = [r for r in read if r.get("reads") == "written"]
    if written:
        lines.extend(_summarise_written(written, len(read)))
    lines.append("  units whose export the Studio itself reads differently (as the app writes it):")
    for r in sorted(
        (r for r in results if r.get("status") == "differs"),
        key=lambda r: -(r["modes"][APP]["effect_assets"] + r.get("carried", {}).get("missing", 0)),
    ):
        lines.append(
            "    %-10s %s  (%d assets, %d carried ops lost)"
            % (r["unit"], r.get("name"), r["modes"][APP]["effect_assets"],
               r.get("carried", {}).get("missing", 0))
        )

    kept = sum(r.get("carried", {}).get("kept", 0) for r in read)
    gone = sum(r.get("carried", {}).get("missing", 0) for r in read)
    became: Counter = Counter()
    for r in read:
        became.update(r.get("carried", {}).get("became") or {})
    lines.append(
        "  ops carried as written (unread, and read): %d are in the file that was written, %d are not"
        % (kept, gone)
    )
    if became:
        lines.append("  and of those in the file, read differently the second time:")
    for what, n in became.most_common(10):
        lines.append("    %6d  %s" % (n, what))
    left_out = [r for r in read if r.get("left_out")]
    if left_out:
        lines.append(
            "  units with a mod the export leaves out, though it has ops (no changes to show, so"
            " nothing to write): %d" % len(left_out)
        )
        for r in left_out[:20]:
            lines.append("    %-10s %s  (%s)" % (r["unit"], r.get("name"), "; ".join(r["left_out"][:3])))
    infos: Counter = Counter()
    keys: Counter = Counter()
    for r in read:
        infos.update((r.get("modinfo") or {}).get("mods") or {})
        keys.update((r.get("modinfo") or {}).get("keys") or {})
    lines.append("  modinfo of each exported mod: %s" % ", ".join("%s %d" % kv for kv in infos.most_common()))
    for key, n in keys.most_common(25):
        lines.append("    %6d  %s" % (n, key))
    for label_, found in (("errors", "error"), ("timed out", "timeout"), ("crashed", "crashed")):
        hit = [r for r in results if r.get("status") == found]
        if hit:
            lines.append("  %s: %s" % (label_, ", ".join("%s %s" % (r["unit"], r.get("name")) for r in hit[:30])))
    loaded = [r for r in results if "game_roundtrip" in r]
    if loaded:
        lines.append("  the game's own loader, on %d units:" % len(loaded))
        for key, said in (
            ("game_reading", "the Studio's reading, against the loader on the mod"),
            ("game_writing", "what the Studio says its export does, against the loader on it"),
            ("game_roundtrip", "the loader on the export, against the loader on the mod"),
        ):
            counted = Counter(r[key] for r in loaded)
            lines.append("    %s: %s" % (said, ", ".join("%s %d" % kv for kv in counted.most_common())))
        both = Counter(
            "reading %s, round trip %s" % (r["game_reading"], r["game_roundtrip"]) for r in loaded
        )
        lines.append("    together: %s" % ", ".join("%s %d" % kv for kv in both.most_common()))
        lines.append("  units whose export does in game something other than the mod did:")
        for r in sorted(
            (r for r in loaded if r["game_roundtrip"] == "differs"),
            key=lambda r: -r["loader"][APP]["assets"],
        )[:60]:
            lines.append(
                "    %-10s %s  (%d assets; reading %s, writing %s; %d unread, %d set aside)"
                % (r["unit"], r.get("name"), r["loader"][APP]["assets"], r["game_reading"],
                   r["game_writing"], r.get("blocked", 0), sum((r.get("extras") or {}).values()))
            )
        lines.append("  units whose export does in game something other than what the Studio shows:")
        for r in sorted(
            (r for r in loaded if r["game_writing"] == "differs"),
            key=lambda r: -max(_writing(r, mode) for mode in MODES),
        )[:60]:
            lines.append(
                "    %-10s %s  (%s)"
                % (r["unit"], r.get("name"),
                   ", ".join("%s: %d assets" % (mode, _writing(r, mode))
                             for mode in MODES if "writing" in (r["loader"].get(mode) or {})))
            )
        lines.append("  units the Studio reads least like the loader does:")
        for r in sorted(
            (r for r in loaded if r["game_reading"] == "differs"),
            key=lambda r: -r["loader"]["reading"]["assets"],
        )[:30]:
            lines.append("    %-10s %s  (%d assets)" % (r["unit"], r.get("name"), r["loader"]["reading"]["assets"]))
        said: Counter = Counter()
        added: Counter = Counter()
        for r in loaded:
            said.update(r["loader"]["original"]["log"])
            for mode in MODES:
                added.update((r["loader"].get(mode) or {}).get("log_added") or {})
        lines.append("  what the loader said about the mods as shipped:")
        for message, n in said.most_common(20):
            lines.append("    %6d  %s" % (n, message))
        lines.append("  what it said about the exports and not about the mods:")
        for message, n in added.most_common(15):
            lines.append("    %6d  %s" % (n, message))
    taking = [r for r in results if (r.get("repeats") or {}).get("taken")]
    lines.append("  units that create an asset under a GUID already taken: %d (%d GUIDs; "
                 "%d more tidied by the mod itself, %d the same asset each time)" % (
        len(taking), sum(r["repeats"]["taken"] for r in taking),
        sum((r.get("repeats") or {}).get("tidied", 0) for r in results),
        sum((r.get("repeats") or {}).get("same", 0) for r in results)))
    for r in sorted(taking, key=lambda r: -r["repeats"]["taken"])[:30]:
        lines.append("    %-10s %s  (%d: %s)" % (
            r["unit"], r.get("name"), r["repeats"]["taken"], r["repeats"]["examples"][0][:120]))
    lines.append("  why ops could not be read:")
    for reason, n in reasons.most_common(30):
        lines.append("    %6d  %s" % (n, reason))
    lines.append("  what else was found and left alone:")
    for kind, n in extras.most_common(30):
        lines.append("    %6d  %s" % (n, kind))
    return "\n".join(lines)


def _writing(result, mode: str) -> int:
    """How many assets a mode's export leaves other than as the Studio shows."""
    return ((result["loader"].get(mode) or {}).get("writing") or {}).get("assets", 0)


def _summarise_written(results, of: int) -> List[str]:
    """The questions asked of units read with every op kept as written."""
    lines = ["  read with every op kept as written: %d of %d units" % (len(results), of)]
    mods = Counter()
    for r in results:
        for key in ("mods", "ops", "same", "moved", "differs", "missing", "extra"):
            mods[key] += (r.get("as_written") or {}).get(key, 0)
    lines.append(
        "  ops as written, each mod's file against its export's: %d mods and %d ops and groups; "
        "%d mods the same, %d with an op moved, %d that differ (%d ops not in the export, %d only in it)"
        % (mods["mods"], mods["ops"], mods["same"], mods["moved"], mods["differs"],
           mods["missing"], mods["extra"])
    )
    for r in sorted(
        (r for r in results if (r.get("as_written") or {}).get("differs") or (r.get("as_written") or {}).get("moved")),
        key=lambda r: -(r["as_written"]["missing"] + r["as_written"]["extra"]),
    )[:40]:
        found = r["as_written"]
        lines.append(
            "    %-10s %s  (%d not in the export, %d only in it, %d mods with an op moved: %s)"
            % (r["unit"], r.get("name"), found["missing"], found["extra"], found["moved"],
               (found["examples"] or [""])[0][:140])
        )
    took = sum(r.get("narrowable", 0) for r in results)
    kept = sum(r.get("rules", 0) for r in results) - took
    acted = Counter(r.get(ACTION) for r in results)
    lines.append(
        "  written narrow where it can be (%d ops; %d stay as written), as the Studio reads the result: %s"
        % (took, kept, ", ".join("%s %d" % kv for kv in acted.most_common()))
    )
    for r in sorted(
        (r for r in results if r.get(ACTION) == "differs"),
        key=lambda r: -r["modes"][ACTION]["effect_assets"],
    )[:40]:
        lines.append(
            "    %-10s %s  (%d assets; %d of its %d ops written narrow)"
            % (r["unit"], r.get("name"), r["modes"][ACTION]["effect_assets"],
               r.get("narrowable", 0), r.get("rules", 0))
        )
    loaded = [r for r in results if "game_action" in r]
    if loaded:
        counted = Counter(r["game_action"] for r in loaded)
        lines.append(
            "  and the loader on that, against the loader on the mod: %s"
            % ", ".join("%s %d" % kv for kv in counted.most_common())
        )
        for r in sorted(
            (r for r in loaded if r["game_action"] == "differs"),
            key=lambda r: -r["loader"][ACTION]["assets"],
        )[:40]:
            lines.append(
                "    %-10s %s  (%d assets)" % (r["unit"], r.get("name"), r["loader"][ACTION]["assets"])
            )
    return lines


class Slots:
    """The loader runs allowed at once, shared by every unit's process.

    A semaphore alone leaks: a unit killed for running out of time while its
    xmltest runs never gives its slot back, and once ``--loader-jobs`` of them
    had, every unit after waited out its hour for a slot that was not coming.
    The Anno 1800 census of 27 Sep 2026 lost its last eighty units that way.
    So each slot says which process holds it, and :meth:`free` hands back what
    a killed one held.

    And each process waiting for one says so (:meth:`waiting`): the wait is
    for other units' loader runs, and is not counted against its own time.
    ``waiters`` is how many can wait at once, the units run at a time.
    """

    def __init__(self, context, n: int, waiters: int = 64):
        self._semaphore = context.BoundedSemaphore(n)
        self._holders = context.Array("i", n, lock=False)
        self._waiting = context.Array("i", max(waiters, 1), lock=False)
        self._lock = context.Lock()

    def __enter__(self):
        pid = os.getpid()
        self._mark(pid, True)
        try:
            self._semaphore.acquire()
        finally:
            self._mark(pid, False)
        with self._lock:
            cell = list(self._holders).index(0)
            self._holders[cell] = pid
        return self

    def _mark(self, pid: int, waiting: bool) -> None:
        with self._lock:
            held = list(self._waiting)
            if waiting and 0 in held:
                self._waiting[held.index(0)] = pid
            elif not waiting and pid in held:
                self._waiting[held.index(pid)] = 0

    def waiting(self, pid: int) -> bool:
        """Whether ``pid`` is waiting for a slot now."""
        return pid in list(self._waiting)

    def __exit__(self, *exc):
        with self._lock:
            pid = os.getpid()
            for cell in range(len(self._holders)):
                if self._holders[cell] == pid:
                    self._holders[cell] = 0
                    break
        self._semaphore.release()

    def free(self, pid: int) -> int:
        """Give back what ``pid``, now dead, held. How many it held."""
        freed = 0
        with self._lock:
            for cell in range(len(self._holders)):
                if self._holders[cell] == pid:
                    self._holders[cell] = 0
                    self._semaphore.release()
                    freed += 1
            for cell in range(len(self._waiting)):
                if self._waiting[cell] == pid:
                    self._waiting[cell] = 0
        return freed


class RunLock:
    """One run of this harness on the machine at a time.

    Two at once share out nothing: each starts its own ``--jobs`` units and
    its own ``--loader-jobs`` loaders, so the memory one run was sized for is
    asked for twice and both crawl -- and both write the one timings file,
    which ended the second of two runs on 11 Oct 2026 at unit 181 of 854.

    A byte of the file is locked for as long as the run's process lives, and
    the system lets go of it when the process ends however it ends, so a run
    that is killed leaves nothing to clear up.  The byte is far past what the
    file holds, which is who has it: that stays readable by a run turned away.
    """

    _BYTE = 1 << 30

    def __init__(self, path: Path):
        self.path = Path(path)
        self._file: Optional[int] = None

    def take(self, who: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Take the lock and write ``who`` in it: None.  Or, where another
        run has it, what that run wrote."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
        try:
            self._lock(file, True)
        except OSError:
            holder: Dict[str, Any] = {}
            with contextlib.suppress(OSError, ValueError):
                os.lseek(file, 0, os.SEEK_SET)
                holder = json.loads(os.read(file, 65536).decode("utf-8"))
            os.close(file)
            return holder if isinstance(holder, dict) else {}
        os.lseek(file, 0, os.SEEK_SET)
        os.ftruncate(file, 0)
        os.write(file, json.dumps(who, ensure_ascii=False).encode("utf-8"))
        self._file = file
        return None

    def release(self) -> None:
        if self._file is None:
            return
        with contextlib.suppress(OSError):
            os.ftruncate(self._file, 0)
            self._lock(self._file, False)
        os.close(self._file)
        self._file = None

    def _lock(self, file: int, on: bool) -> None:
        os.lseek(file, self._BYTE, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file, msvcrt.LK_NBLCK if on else msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, (fcntl.LOCK_EX | fcntl.LOCK_NB) if on else fcntl.LOCK_UN)


def default_lock() -> Path:
    """Where the lock is: one place for the machine, whichever copy of the
    code a run is started from and whichever census it reads."""
    return places.local_app_data() / places.NAME / "census.lock"


def _holder(holder: Dict[str, Any]) -> str:
    return "%s, started %s (process %s)" % (
        holder.get("out") or "another run", holder.get("started") or "?", holder.get("pid") or "?"
    )


def _size(unit) -> int:
    """Bytes of XML a unit has: what it is judged by before it has a time."""
    folder = Path(unit.get("files") or unit.get("folder") or ".")
    return sum(path.stat().st_size for path in folder.rglob("*.xml") if path.is_file())


def _longest_first(todo, results_dir: Path, timings: Dict[str, float]) -> List[Dict[str, Any]]:
    """The units in the order that ends soonest: the longest first.

    In the order the census lists them, the big bundles came up at the end,
    and the run's last hours were three of them and a pool of idle workers.
    Each unit is judged by its last time -- the timings file, or a result
    already in the folder -- and one never timed goes ahead of all of them,
    biggest first, since nothing says it is quick.
    """
    def last_time(unit) -> Optional[float]:
        if unit["id"] in timings:
            return timings[unit["id"]]
        with contextlib.suppress(OSError, ValueError):
            found = json.loads((results_dir / ("%s.json" % unit["id"])).read_text(encoding="utf-8"))
            return float(found.get("seconds") or 0)
        return None

    known = {unit["id"]: last_time(unit) for unit in todo}
    untimed = sorted((u for u in todo if known[u["id"]] is None), key=lambda u: -_size(u))
    timed = sorted((u for u in todo if known[u["id"]] is not None), key=lambda u: -known[u["id"]])
    return untimed + timed


def _within_time(running: float, waited: float, timeout: float) -> bool:
    """Whether a unit that has been ``running`` so long, ``waited`` of it for
    a loader slot, is within its ``timeout``: the limit is on its own work."""
    return running - waited <= timeout


def _peak_memory(process) -> Optional[int]:
    """The most memory a unit's process has had to itself, in megabytes: what
    it committed, which stays the same number on a machine short of memory
    where what it holds in RAM does not.  None where that cannot be asked --
    anywhere but Windows.  The loader's runs are not in it: those are the
    ``--loader-jobs`` to count.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    asked = ctypes.WinDLL("kernel32").K32GetProcessMemoryInfo
    asked.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    try:
        if not asked(process.sentinel, ctypes.byref(counters), counters.cb):
            return None
    except (OSError, ValueError):
        return None
    return round(counters.PeakPagefileUsage / 2**20)


def _free_memory() -> Optional[int]:
    """The memory nothing on the machine is using now, in megabytes; None
    where that cannot be asked -- anywhere but Windows."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong)
            for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                         "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")
        ]

    status = Status(ctypes.sizeof(Status))
    if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return round(status.ullAvailPhys / 2**20)


def _next_that_fits(todo, needs: Dict[str, int], held: float, budget: Optional[float]) -> Optional[int]:
    """Which of ``todo`` to start next, by its place in it: the first whose
    memory fits beside what is running.  None for none of them yet.

    ``needs`` is what each unit's process took at its most the last time it
    ran, ``held`` the sum of that over the units running now, and ``budget``
    what they may add up to.  The units are longest first and the longest are
    the ones that take gigabytes, so in their own order a census began by
    starting every one of those together -- 15 GB of them for Anno 1800, on a
    machine with 12 free.  One that does not fit waits and the next that does
    goes ahead of it, which keeps the workers busy on the hundreds that take
    next to nothing.

    A unit starts whatever it needs when nothing else is running.  One that
    needs more than the whole budget is waited for and not passed over: it
    can only ever start alone, and with small units started ahead of it for
    as long as there were any it would come last.
    """
    if not todo:
        return None
    if budget is None or held <= 0:
        return 0

    def need(unit) -> int:
        return needs.get(unit["id"], UNKNOWN_MB)

    if need(todo[0]) >= budget:
        return None
    for place, unit in enumerate(todo):
        if held + need(unit) <= budget:
            return place
    return None


def _kill(process) -> None:
    """The unit's process and whatever it started: an xmltest left running
    holds its gigabyte and a half until it finishes on its own."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, check=False)
    process.terminate()
    process.join()


def run(
    units,
    game: str,
    source: Path,
    out: Path,
    jobs: int,
    timeout: float,
    redo: bool,
    keep: bool,
    loader_exe: Optional[Path] = None,
    loader_jobs: int = LOADER_JOBS,
    how: Optional[How] = None,
    loader_cache: Optional[Path] = None,
    loader_action: bool = False,
    timings_file: Optional[Path] = None,
    carry_all: bool = False,
    memory_budget: Optional[float] = None,
) -> None:
    results_dir, work = out / RESULTS, out / WORK
    results_dir.mkdir(parents=True, exist_ok=True)
    todo = [u for u in units if redo or not (results_dir / ("%s.json" % u["id"])).exists()]
    timings: Dict[str, float] = {}
    if timings_file is not None and timings_file.is_file():
        with contextlib.suppress(OSError, ValueError):
            timings = json.loads(timings_file.read_text(encoding="utf-8"))
    todo = _longest_first(todo, results_dir, timings)
    # What each unit's process took at its most, beside its time.  A file of
    # its own: frozen copies of this harness from before it share the timings
    # file, and read every value in it as a number of seconds.
    memory_file = timings_file.with_suffix(".memory.json") if timings_file is not None else None
    memory: Dict[str, int] = {}
    if memory_file is not None and memory_file.is_file():
        with contextlib.suppress(OSError, ValueError):
            memory = json.loads(memory_file.read_text(encoding="utf-8"))
    print("%d units, %d to run, %d at a time, longest first" % (len(units), len(todo), jobs), flush=True)
    context = multiprocessing.get_context("spawn")
    # Shared by every unit's process: memory, not cores, is what limits loader runs.
    slot = Slots(context, loader_jobs, jobs) if loader_exe else None
    loader = (
        LoaderSetup(Path(loader_exe), Path(source), slot, loader_cache, loader_action)
        if loader_exe else None
    )
    running: Dict[str, Tuple[Any, float, Dict[str, Any]]] = {}
    # How long each running unit has stood waiting for a loader slot, and
    # when that was last looked at: the wait is other units' loader runs, and
    # counted in, a unit that had only waited was written down as a timeout.
    waited: Dict[str, float] = {}
    looked = time.monotonic()
    done = 0
    # How long a worker stood idle with units left, for want of memory.
    held_back = 0.0
    try:
        while todo or running:
            while todo and len(running) < jobs:
                held = sum(memory.get(unit_id, UNKNOWN_MB) for unit_id in running)
                place = _next_that_fits(todo, memory, held, memory_budget)
                if place is None:
                    held_back += time.monotonic() - looked
                    break
                unit = todo.pop(place)
                target = results_dir / ("%s.json" % unit["id"])
                target.unlink(missing_ok=True)
                process = context.Process(
                    target=_child,
                    args=(unit, game, str(source), str(work), keep, str(target), loader, how, carry_all),
                    daemon=True,
                )
                process.start()
                running[unit["id"]] = (process, time.monotonic(), unit)
            now = time.monotonic()
            for unit_id, (process, began, unit) in list(running.items()):
                target = results_dir / ("%s.json" % unit_id)
                if slot is not None and slot.waiting(process.pid):
                    waited[unit_id] = waited.get(unit_id, 0.0) + (now - looked)
                if process.is_alive():
                    if _within_time(now - began, waited.get(unit_id, 0.0), timeout):
                        continue
                    peak = _peak_memory(process)
                    _kill(process)
                    if slot is not None:
                        slot.free(process.pid)
                    # Half an export, of a unit with no result to read it by.
                    shutil.rmtree(work / unit_id, ignore_errors=True)
                    _write_json(target, {"unit": unit_id, "name": unit.get("name"), "game": game,
                                         "status": "timeout", "seconds": timeout})
                else:
                    peak = _peak_memory(process)
                    process.join()
                    if slot is not None:
                        slot.free(process.pid)
                    if not target.exists():
                        _write_json(target, {"unit": unit_id, "name": unit.get("name"), "game": game,
                                             "status": "crashed", "exitcode": process.exitcode})
                del running[unit_id]
                waited.pop(unit_id, None)
                done += 1
                try:
                    r = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    r = {"status": "unreadable"}
                if timings_file is not None and r.get("seconds"):
                    timings[unit_id] = r["seconds"]
                    # Only the order of the next run hangs on it: not worth a
                    # run, which a file held open by something else once cost.
                    with contextlib.suppress(OSError):
                        _write_json(timings_file, timings)
                if memory_file is not None and peak:
                    memory[unit_id] = peak
                    with contextlib.suppress(OSError):
                        _write_json(memory_file, memory)
                print(
                    "  [%d/%d] %-11s %6.0fs  ops %5s  unread %4s  %s"
                    % (done, done + len(todo) + len(running), r.get("status"),
                       r.get("seconds") or 0, r.get("ops", "-"), r.get("blocked", "-"), unit.get("name")),
                    flush=True,
                )
            looked = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        for process, _began, _unit in running.values():
            _kill(process)
        print("\nstopped; run again to carry on from here")
        raise
    if held_back >= 1:
        print("units waited %.0f s in all for memory: the limit is %.1f GB at once (--memory)"
              % (held_back, (memory_budget or 0) / 1024), flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--census", type=Path, help="a census folder from modio_fetch.py")
    where.add_argument("--folder", type=Path, help="a mods folder, read as one stack")
    parser.add_argument("--game", help="census: anno-1800 / anno-117-pax-romana (default both); "
                                       "folder: anno1800 / anno117")
    parser.add_argument("--out", type=Path, help="where results go (default: beside the census)")
    parser.add_argument("--assets", type=Path, help="the base assets.xml (default: the Studio's extract)")
    parser.add_argument("--mod", action="append", help="only this unit id; repeatable")
    parser.add_argument("--limit", type=int, help="at most this many units")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    parser.add_argument("--timeout", type=float, help="seconds per unit (default %d, twice that with --loader)" % TIMEOUT)
    parser.add_argument("--redo", action="store_true", help="run units that already have a result")
    parser.add_argument("--keep", action="store_true", help="keep the exports of identical units too")
    parser.add_argument("--prune-days", type=float, default=PRUNE_DAYS,
                        help="first delete the runs beside --out that nothing has been written to "
                             "for this many days, keeping the newest and any holding a file "
                             "called 'keep' (default %(default)s; 0 deletes none)")
    parser.add_argument("--carry-all", action="store_true",
                        help="exports carry every file the mod ships, as the Export tab's do, "
                             "not only the XML and JSON the comparisons read")
    parser.add_argument("--loader", action="store_true",
                        help="also apply every unit with the game's own mod loader (xmltest)")
    parser.add_argument("--xmltest-dir", type=Path,
                        help="folder holding the xmltest builds (default: the newest Anno Modding Tools extension)")
    parser.add_argument("--loader-jobs", type=int, default=LOADER_JOBS,
                        help="xmltest runs at once (default %d)" % LOADER_JOBS)
    parser.add_argument("--loader-cache", type=Path,
                        help="where the loader's answers are kept between runs "
                             "(default: loader-cache beside the census)")
    parser.add_argument("--no-loader-cache", action="store_true", help="run the loader on everything afresh")
    parser.add_argument("--loader-action", action="store_true",
                        help="put the second export, what Write as narrow ModOps makes of "
                             "each mod, through the loader too")
    parser.add_argument("--timings", type=Path,
                        help="each unit's last time, to start the longest first "
                             "(default: timings beside the census)")
    parser.add_argument("--memory", type=float,
                        help="gigabytes the units running at once may add up to, each counted "
                             "as what it took at its most the last time (default: what is free "
                             "when the run starts; 0 for no limit)")
    parser.add_argument("--wait", action="store_true",
                        help="where another run of this harness is going, wait for it to end "
                             "and then start, rather than refuse")
    parser.add_argument("--lock", type=Path,
                        help="the file that says a run is going (default: one for the machine)")
    boxes = parser.add_argument_group(
        "the checkboxes", "each off its default; for a few units, never a census")
    boxes.add_argument("--keep-inert", action="store_true",
                       help="reading: keep removals of entries the game already reads as absent")
    args = parser.parse_args(argv)
    how = How(keep_inert=args.keep_inert)
    timeout = args.timeout or (TIMEOUT * 2 if args.loader else TIMEOUT)
    # Mod names come in every script there is, and a Windows console that cannot
    # print one would otherwise end the run on the first Chinese title.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if args.folder is not None:
        if args.game not in (games.ANNO_1800, games.ANNO_117) or args.out is None:
            parser.error("--folder needs --game anno1800|anno117 and --out")
        plan = [(args.game, args.out / args.game, [{
            "id": re.sub(r"\W+", "_", args.folder.name) or "folder",
            "name": str(args.folder),
            "folder": str(args.folder),
            "logged": loaderlog.logged_order(args.game) or None,
        }])]
    else:
        names = [args.game] if args.game else [n for n in CENSUS_GAMES if (args.census / n).is_dir()]
        unknown = [n for n in names if n not in CENSUS_GAMES]
        if unknown:
            parser.error("unknown census game: %s" % ", ".join(unknown))
        # A mistyped --census holds none of them, and an empty plan crashed
        # the run or ended it as if it had worked.
        if not names:
            parser.error("the census %s holds no game's folder (%s)" % (
                args.census, " or ".join(CENSUS_GAMES)))
        missing = [n for n in names if not (args.census / n).is_dir()]
        if missing:
            parser.error("the census %s has no %s folder" % (args.census, ", ".join(missing)))
        out = args.out or args.census.resolve().parent / "roundtrip"
        plan = [(CENSUS_GAMES[n], out / n, census_units(args.census / n)) for n in names]

    for _game, out_dir, _units in plan:
        if REPO in out_dir.resolve().parents or out_dir.resolve() == REPO:
            print("refusing: %s is inside the repository" % out_dir, file=sys.stderr)
            return 2

    lock = RunLock(args.lock or default_lock())
    me = {
        "pid": os.getpid(),
        "out": str(plan[0][1].parent) if plan else "",
        "games": [game for game, _out, _units in plan],
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    holder = lock.take(me)
    if holder is not None and not args.wait:
        print("refusing: another run is going -- %s. Start this one when it has ended, or "
              "pass --wait to have it start then." % _holder(holder), file=sys.stderr)
        return 3
    said = None
    while holder is not None:
        if _holder(holder) != said:
            said = _holder(holder)
            print("waiting for another run to end: %s" % said, flush=True)
        time.sleep(WAIT)
        holder = lock.take(me)
    try:
        return _run_plan(args, plan, how, timeout)
    finally:
        lock.release()


def _run_plan(args, plan, how: How, timeout: float) -> int:
    if args.prune_days > 0:
        for gone in prune_runs(plan[0][1].parent, args.prune_days):
            print("deleted %s: nothing written to it for %g days" % (gone, args.prune_days))

    for game, out_dir, units in plan:
        if args.mod:
            units = [u for u in units if u["id"] in set(args.mod)]
        if args.limit:
            units = units[: args.limit]
        source = args.assets or base_assets(game)
        if not source.is_file():
            print("no base assets.xml for %s at %s" % (game, source), file=sys.stderr)
            return 1
        open_index(source).close()  # built once here, not by every unit at once
        print("%s: %s" % (games.get(game).label, source))
        loader_exe = None
        if args.loader:
            build = games.get(game).xmltest_build
            loader_exe = args.xmltest_dir / build if args.xmltest_dir else modio_loader.find_xmltest(game)
            if loader_exe is None or not loader_exe.is_file():
                print("no %s for %s: install the Anno Modding Tools extension or pass --xmltest-dir"
                      % (build, games.get(game).label), file=sys.stderr)
                return 1
            print("loader: %s, %d at a time" % (loader_exe, args.loader_jobs))
        try:
            beside = args.census.resolve().parent if args.census else None
            cache = None
            if loader_exe and not args.no_loader_cache:
                cache = args.loader_cache or (beside / "loader-cache" if beside else None)
            timings = args.timings or (beside / "timings" / ("%s.json" % out_dir.name) if beside else None)
            # Asked now and not once for the run: the second game starts on
            # the machine as the first left it.
            budget = _free_memory() if args.memory is None else args.memory * 1024 or None
            if budget is not None:
                print("memory: units running at once add up to at most %.1f GB" % (budget / 1024))
            run(units, game, source, out_dir, args.jobs, timeout, args.redo, args.keep,
                loader_exe, args.loader_jobs, how, cache, args.loader_action, timings, args.carry_all,
                budget)
        except KeyboardInterrupt:
            return 130
        report = summarise(out_dir / RESULTS, "%s (%s)" % (games.get(game).label, out_dir))
        (out_dir / "summary.txt").write_text(report + "\n", encoding="utf-8")
        print("\n" + report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
