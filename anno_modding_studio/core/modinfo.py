"""What a mod's ``modinfo.json`` says, as an export of the Studio writes it.

Each game wants its own shape -- Anno 1800's ``ModDependencies`` and
``DeprecateIds``, Anno 117's ``"Anno": 8``, ``Dependencies`` object,
``Difficulty`` and ``GameSetup`` -- and a mod read from a folder brings its
author's own file, which is the base the Studio writes its few fields over.
The Studio's block in it says which mod a patch is for, which build this is,
and what game data it was built against.

Kept apart from :mod:`core.games`, which says what each game *is*.  Writing a
modinfo needs a build's record and a ModID made out of a name, and while it
lived in ``games`` every module that asked a game a question sat in one import
cycle with the exporter (P8-25).
"""

from __future__ import annotations

import copy
import json

from .. import __version__
from . import games
from .games import ANNO_117, ANNO_1800, GAMES, slugify


def build(loadout) -> str:
    """The modinfo an export of ``loadout`` writes: its game's shape, written
    over the mod's own file where it has one."""
    if games.get(loadout.game).key == ANNO_117:
        fresh = _modinfo_117(loadout)
    else:
        fresh = _modinfo_1800(loadout)
    return _over_declared(loadout, fresh)


def own_mod_id(loadout) -> str:
    """The ModID a mod written here goes out under in Anno 117: the field
    as typed, or made out of the name where it is blank."""
    return (loadout.own.mod_id or "").strip() or GAMES[ANNO_117].usable_mod_id(loadout.own.name)


def written_mod_id(loadout) -> str:
    """The ModID an export of ``loadout`` would actually put in its modinfo.

    Not the field as typed: a blank one is filled in from the name, and a
    loaded mod's own file can carry an ID of its author's.  Reading it back
    off the modinfo the export would write is the one answer that cannot
    disagree with the file.
    """
    made = json.loads(build(loadout))
    return str(made.get("ModID") or "")


# What the Studio actually decides about a mod. Everything else in a modinfo is
# the author's and is carried through untouched -- including keys no version of
# the Studio has heard of, since being unrecognised is the strongest reason not
# to delete something.
OWNED_FIELDS = (
    "Version",
    "ModID",
    "Category",
    "ModName",
    "Description",
    "CreatorName",
    "LoadAfterIds",
)

# Where a patch records, in its own modinfo, which mod it is a patch *for*.
# Neither game defines this and neither loader reads it -- an unrecognised key
# is ignored -- but the Studio does, and without it a patch read back out of a
# mods folder is just a mod that happens to load after another one. It is only
# safe to write because unknown keys now survive a round trip; see
# :func:`_over_declared`.
STUDIO_KEY = "AnnoModdingStudio"
# The keys of that block, and of what it says the mod was built against.
# Read back by every later Studio from mods on other people's machines, so
# each is added and never renamed or taken away: ``tests/test_contracts.py``
# pins the words.
PATCHES_KEY = "patches"
NEEDS_KEY = "needs"
BUILD_KEY = "build"
BUILT_AGAINST_KEY = "builtAgainst"
STUDIO_VERSION_KEY = "studio"
AGAINST_GAME = "game"
AGAINST_UPDATE = "update"
AGAINST_GAME_VERSION = "gameVersion"
AGAINST_ARCHIVE = "archive"
AGAINST_ASSETS = "assets"
# How the assets' fingerprint is written there: the hash it is, then it.
ASSETS_HASH = "sha1:"


def studio_block(loadout) -> dict:
    """What the Studio has to say in a modinfo, under :data:`STUDIO_KEY`.

    Which mod a patch is for, and for a mod of yours the build it is, the
    game data it was built against (see :mod:`core.builds`) and the version
    of the Studio that wrote it.  Empty for somebody else's mod written back
    out, whose modinfo is left as it was.

    The block's keys are read back by every later Studio, from mods on other
    people's machines: a key is added, and never renamed or taken away.

    The Studio's version goes with the build and only with it.  A build's
    digest is taken with no build set (:func:`core.builds.plan`), which
    leaves it out: a new Studio writing the same mod is the same build.
    """
    block = {}
    if loadout.own.patches:
        block[PATCHES_KEY] = loadout.own.patches
        if loadout.own.needs:
            block[NEEDS_KEY] = list(loadout.own.needs)
    build = getattr(loadout, "build", 0)
    if build:
        block[BUILD_KEY] = build
        block[BUILT_AGAINST_KEY] = built_against(loadout)
        block[STUDIO_VERSION_KEY] = __version__
    return block


def built_against(loadout) -> dict:
    """The game data a mod was built against, as its modinfo records it.

    Nothing with a path in it: this goes into a file people download.
    """
    out = {AGAINST_GAME: games.get(loadout.game).label}
    if getattr(loadout, "base_update", ""):
        out[AGAINST_UPDATE] = loadout.base_update
    if getattr(loadout, "base_game_version", ""):
        out[AGAINST_GAME_VERSION] = loadout.base_game_version
    if getattr(loadout, "base_archive", ""):
        out[AGAINST_ARCHIVE] = loadout.base_archive
    if getattr(loadout, "base_fingerprint", ""):
        out[AGAINST_ASSETS] = ASSETS_HASH + loadout.base_fingerprint
    return out


def declared_needs_of(declared) -> list:
    """The other ModIDs a patch's modinfo says it needs, besides the one it
    patches."""
    mark = (declared or {}).get(STUDIO_KEY)
    if isinstance(mark, dict) and isinstance(mark.get(NEEDS_KEY), list):
        return [str(value).strip() for value in mark[NEEDS_KEY] if str(value).strip()]
    return []


def declared_patch_of(declared) -> str:
    """The ModID this modinfo says it patches, or "" if it does not say."""
    mark = (declared or {}).get(STUDIO_KEY)
    if isinstance(mark, dict):
        return str(mark.get(PATCHES_KEY) or "").strip()
    return ""


def as_written(loadout) -> dict:
    """The modinfo the mod ``loadout`` writes out (its own mod: the view
    :func:`.generator.for_mod` makes of a mod) has as its author wrote it;
    empty for a mod the Studio writes whole, which has none."""
    record = loadout.own
    return {} if record.written_whole else record.declared


def _over_declared(loadout, fresh: str) -> str:
    """The generated modinfo written *into* the mod's own, where it has one.

    A mod read from a folder brings its whole modinfo with it.  Rebuilding one
    from the six fields the Studio models used to publish the rest as empty --
    ``IncompatibleIds: []`` over an author's real list, ``LoadAfterIds`` gone
    altogether -- so exporting a loaded mods folder silently unpicked its load
    order.  Of 107 real modinfos here, 55 declared something that would have
    been lost.

    So the author's file is the base and only the fields the Studio owns are
    written over it.  A mod of your own has no such file and is generated whole.
    """
    declared = as_written(loadout)
    if not declared:
        return fresh
    merged = copy.deepcopy(declared)
    if loadout.game == ANNO_1800:
        merged = _respelled_1800(merged)
    made = json.loads(fresh)
    # What the loadout says each field is now, without the stand-ins a fresh
    # modinfo needs -- "Gameplay", "1.0", a ModID made out of the name.
    current = {
        "Version": loadout.own.version,
        "ModID": loadout.own.mod_id,
        "Category": loadout.own.category,
        "ModName": "" if loadout.own.named_after_folder else loadout.own.name,
        "Description": loadout.own.description,
        "CreatorName": loadout.own.creator,
    }
    for field in OWNED_FIELDS:
        if field not in made:
            continue
        theirs, ours = merged.get(field), made[field]
        if field in current and (current[field] or "").strip() == _english(theirs):
            # Nothing about it has changed here, so how its author wrote it
            # stands: a plain string stays one, and a field they never wrote
            # stays unwritten. Of the modinfos exported from the mod.io census,
            # a third came back with a ``CreatorName: ""`` they never had, and
            # some with a Category of "Gameplay" -- which in Anno 117 is part
            # of where a mod loads.
            continue
        if isinstance(theirs, str) and isinstance(ours, dict):
            merged[field] = str(ours.get("English") or "")
        elif isinstance(theirs, dict) and isinstance(ours, dict):
            # A localised field: the Studio only ever knows the English of it,
            # and 98 of the 107 modinfos here carry between one and eleven other
            # languages. Replacing the whole object would delete all of them, so
            # only the language the Studio can speak is written.
            theirs = dict(theirs)
            theirs.update(ours)
            merged[field] = theirs
        else:
            merged[field] = ours
    # A patch's declaration is the one thing that must win outright.
    if loadout.own.patches:
        _declare_load_after(merged, loadout)
    # The Studio's block is its own, so it is written whole, not merged: a
    # build number from the last export has no business surviving this one.
    block = studio_block(loadout)
    if block:
        merged[STUDIO_KEY] = block
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


# Two keys the Studio used to write into every new Anno 1800 modinfo under names
# the loader does not read. It reads ``ModDependencies`` (541 of the census's
# 857 mods) and ``DeprecateIds`` (190); ``Dependencies`` as a list turns up in
# none of them and ``DeprecatedIds`` in one. Filled in, either list did nothing.
_MISSPELLED_1800 = {"Dependencies": "ModDependencies", "DeprecatedIds": "DeprecateIds"}


def _respelled_1800(merged: dict) -> dict:
    """An Anno 1800 modinfo with the Studio's old misspellings put right.

    Only where the loader's own spelling is not there as well: with both, which
    list the author meant is not something to guess. ``Dependencies`` is only
    the old spelling as a list -- as an object it is Anno 117's, and not ours
    to touch. Each key keeps its place, so the file reads as it did.
    """
    renames = {}
    for old, new in _MISSPELLED_1800.items():
        if old not in merged or new in merged:
            continue
        if old == "Dependencies" and not isinstance(merged[old], list):
            continue
        renames[old] = new
    if not renames:
        return merged
    return {renames.get(key, key): value for key, value in merged.items()}


def declares_a_name(declared) -> bool:
    """Whether a mod's own modinfo says what the mod is called."""
    return bool(declared_field(declared, "ModName"))


def declared_field(declared, field: str) -> str:
    """What a mod's own modinfo says ``field`` is, in English; "" for nothing."""
    return _english((declared or {}).get(field))


def _english(value) -> str:
    """A modinfo field as the Studio reads it: its English, or all it says."""
    if isinstance(value, dict):
        return str(value.get("English") or "").strip()
    return str(value or "").strip()


def _declare_load_after(merged: dict, loadout) -> None:
    """Put "loads after the mods I patch, and needs them" into whichever
    shape this file uses -- and the star with it, where one is a late one."""
    depends = merged.get("Dependencies")
    if isinstance(depends, dict):
        after = [str(v) for v in (depends.get("LoadAfter") or [])]
        depends["LoadAfter"] = after + [v for v in _load_after(loadout) if v not in after]
        needs = [str(v) for v in (depends.get("Require") or [])]
        depends["Require"] = needs + [v for v in _needed(loadout) if v not in needs]
        return
    after = [str(v) for v in (merged.get("LoadAfterIds") or [])]
    merged["LoadAfterIds"] = after + [v for v in _load_after(loadout) if v not in after]
    needs = [str(v) for v in (merged.get("ModDependencies") or [])]
    merged["ModDependencies"] = needs + [v for v in _needed(loadout) if v not in needs]


def _needed(loadout) -> list:
    """The mods a patch needs, by ModID: the one it patches, then the rest.
    A patch declares them as dependencies, so that the loader says, where one
    is missing, that the patch is running without it."""
    if not loadout.own.patches:
        return []
    found = [loadout.own.patches]
    found += [value for value in loadout.own.needs if value not in found]
    return found


def _load_after(loadout) -> list:
    """What a mod made here says it loads after: the mods it patches, and for
    a loadout's own mod everything -- see ``Loadout.own_load_after``."""
    named = list(loadout.own.load_after)
    named += [value for value in _needed(loadout) if value not in named]
    return named


def _modinfo_1800(loadout) -> str:
    data: dict = {
        "Version": loadout.own.version or "1.0",
        "ModID": (loadout.own.mod_id.strip() or slugify(loadout.own.name)),
        "Category": {"English": loadout.own.category or "Gameplay"},
        "ModName": {"English": loadout.own.name or "My Anno Mod"},
        "Description": {"English": loadout.own.description or ""},
        "CreatorName": loadout.own.creator or "",
        "KnownIssues": {"English": ""},
        "ModDependencies": _needed(loadout),
        "IncompatibleIds": [],
        "DeprecateIds": [],
    }
    after = _load_after(loadout)
    if after:
        # For a patch, folder-name order already puts it after; saying so as
        # well costs nothing, is what the mod loader actually reads, and
        # survives somebody renaming the folder. For a loadout's own mod
        # nothing else puts it last.
        data["LoadAfterIds"] = after
    block = studio_block(loadout)
    if block:
        data[STUDIO_KEY] = block
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# Anno 117's Difficulty, in the loader's words; it prints an error for any
# other, and the modinfo must have one.
DIFFICULTIES = ("cheat", "easier", "unchanged", "harder")
# GameSetup's keys, and what the loader takes each to be when a modinfo
# leaves it out -- its docs' defaults.
SETUP_DEFAULTS = {"RequiresNewGame": False, "SafeToRemove": False, "Multiplayer": True, "Campaign": True}
# What a mod made here says until its author says otherwise. SafeToRemove is
# the one that differs from the loader's default: most of what the Studio
# writes is narrow value changes, which leave nothing in a save, and 218 of
# the 272 census mods that say anything say the same.
NEW_MOD_SETUP = dict(SETUP_DEFAULTS, SafeToRemove=True)


def setup_of(loadout, origin: str = ""):
    """``(difficulty, {GameSetup key: bool})`` for a mod in ``loadout``, from
    its record's ``declared`` -- with each key it leaves out at what the
    mod takes it to be: a loaded mod what the loader would, and the own mod
    what a mod made here starts as (:data:`NEW_MOD_SETUP`, "unchanged")."""
    record = loadout.mod(origin)
    made_here = record is not None and record is loadout.own
    declared = (record.declared if record is not None else None) or {}
    said = declared.get("GameSetup")
    flags = dict(NEW_MOD_SETUP if made_here else SETUP_DEFAULTS)
    if isinstance(said, dict):
        flags.update({k: v for k, v in said.items() if k in flags and isinstance(v, bool)})
    difficulty = declared.get("Difficulty")
    difficulty = difficulty.strip() if isinstance(difficulty, str) else ""
    return (difficulty or "unchanged") if made_here else difficulty, flags


def set_setup(loadout, origin: str, difficulty: str, flags: dict) -> None:
    """Put a mod's Difficulty and GameSetup into its record's ``declared``
    -- where anything else its GameSetup holds stays, and the keys keep
    their places."""
    layer = loadout.mod(origin)
    if layer is None:
        return
    declared = dict(layer.declared or {})
    declared["Difficulty"] = difficulty
    said = declared.get("GameSetup")
    said = dict(said) if isinstance(said, dict) else {}
    for key in SETUP_DEFAULTS:
        if key in flags:
            said[key] = bool(flags[key])
    declared["GameSetup"] = said
    layer.declared = declared


def _modinfo_117(loadout) -> str:
    data = {
        "Version": loadout.own.version or "1.0",
        "Anno": 8,
        # Lowercase, because the loader refuses anything else: a mod called
        # "Faster Lumber" used to be filled in as "Faster-Lumber", which the
        # game would not load.  What was typed into the field is not touched
        # here -- that is checked before export, where it can be said why.
        "ModID": own_mod_id(loadout),
        "Category": {"English": loadout.own.category or "Gameplay"},
        "ModName": {"English": loadout.own.name or "My Anno Mod"},
        "Description": {"English": loadout.own.description or ""},
        "CreatorName": loadout.own.creator or "",
        "Difficulty": setup_of(loadout)[0],
        "GameSetup": setup_of(loadout)[1],
        "Dependencies": {
            "Require": _needed(loadout),
            "Optional": [],
            "LoadAfter": _load_after(loadout),
            "Deprecate": [],
            "Incompatible": [],
        },
    }
    # Its Lua, for a mod written here that has any. A loaded mod's is in its
    # own modinfo, and :func:`_over_declared` keeps it.
    if getattr(loadout, "scripts", None):
        data["Scripts"] = loadout.scripts
    # And what it lets its players decide: the loader reads each option's
    # default from here, and a mod manager the rest.
    if getattr(loadout, "options", None):
        data["Options"] = loadout.options
    block = studio_block(loadout)
    if block:
        data[STUDIO_KEY] = block
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
