"""What the mods that will load say about each other, before the game says it.

Every modinfo can name mods it needs, mods it does not work with and mods it
replaces -- Anno 1800 in top-level lists, Anno 117 in a ``Dependencies``
object.  The loader answers the first two with a line in its log once the
game has started ("… is missing a dependency: …"), which is too late to do
anything about.  This asks the same questions of what Play is about to make
live, where the answer can still change what happens.

The third is not a warning at all.  Both loaders drop a mod another loaded
mod lists as deprecated (``HandleDeprecation`` in the loader's source, the
same in both games): it is not loaded, and 117 also reads a requirement of
the old ID as a requirement of its replacement.  So that is said as what it
is -- a mod that will not load.

Mods are matched by ModID without regard to case, as everywhere else in the
Studio; the loaders compare exactly, and no requirement in the mod.io census
differs from the ModID it names only by case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from . import modfolder
from .games import ANNO_117
from .i18n import join_list, tr, tr_noop, trn

# Where each game's modinfo keeps each list.
_KEYS_1800 = {
    "require": "ModDependencies",
    "optional": "OptionalDependencies",
    "incompatible": "IncompatibleIds",
    "deprecate": "DeprecateIds",
}
_KEYS_117 = {
    "require": "Require",
    "optional": "Optional",
    "incompatible": "Incompatible",
    "deprecate": "Deprecate",
}

# What turns up in these lists that is not a ModID: a template's placeholder
# left in (``ModId``, 28 times in the census), an empty entry, ``null``
# written as a string. The loader would report each as missing, which tells
# the person about to play nothing they can act on.
_NOT_AN_ID = {"", "null", "none", "modid"}


@dataclass
class Links:
    """What one modinfo says about other mods, by ModID."""

    require: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)
    incompatible: List[str] = field(default_factory=list)
    deprecate: List[str] = field(default_factory=list)


def links(declared, game: str) -> Links:
    """The four lists in ``declared`` (a raw modinfo), junk left out."""
    found = Links()
    if not isinstance(declared, dict):
        return found
    if game == ANNO_117:
        source, keys = declared.get("Dependencies"), _KEYS_117
    else:
        source, keys = declared, _KEYS_1800
    if not isinstance(source, dict):
        return found
    for kind, key in keys.items():
        values = source.get(key)
        if not isinstance(values, list):
            continue
        setattr(found, kind, [value for value in (_as_id(one) for one in values) if value])
    return found


def _as_id(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    # A download link where a ModID belongs: *shared-pools-and-definitions.zip*
    # on GitHub, twice in the census. Not something the loader can find.
    if value.lower() in _NOT_AN_ID or "://" in value:
        return ""
    return value


# -- what one modinfo says of the mod itself ------------------------------------
_DIFFICULTY = {
    "cheat": tr_noop("a cheat"),
    "easier": tr_noop("makes the game easier"),
    "unchanged": tr_noop("balanced like the base game"),
    "harder": tr_noop("makes the game harder"),
}


def setup(declared) -> List[str]:
    """What a mod says about the game it belongs in, in words: Anno 117's
    ``Difficulty`` and ``GameSetup``.  Only what it actually says -- a key it
    leaves out has a default, and a default is not the author telling you
    anything."""
    if not isinstance(declared, dict):
        return []
    said = []
    difficulty = declared.get("Difficulty")
    if isinstance(difficulty, str) and difficulty.strip():
        known = _DIFFICULTY.get(difficulty.strip().lower())
        said.append(tr(known) if known else tr("difficulty: %s", difficulty.strip()))
    game = declared.get("GameSetup")
    if isinstance(game, dict):
        if game.get("RequiresNewGame") is True:
            said.append(tr("needs a new game"))
        if game.get("SafeToRemove") is True:
            said.append(tr("can be taken out of a save"))
        elif game.get("SafeToRemove") is False:
            said.append(tr("not safe to take out of a save"))
        if game.get("Multiplayer") is False:
            said.append(tr("not for multiplayer"))
        if game.get("Campaign") is False:
            said.append(tr("not for the campaign"))
    return said


# -- the check ------------------------------------------------------------------
def _replaced(old: str, new: str) -> str:
    return tr("%(old)s will not load: %(new)s replaces it.", old=old, new=new)


def _missing(mod: str, needs: str, off: bool) -> str:
    if off:
        return tr("%(mod)s needs %(needs)s, which is switched off.", mod=mod, needs=needs)
    return tr("%(mod)s needs %(needs)s, which is not installed.", mod=mod, needs=needs)


def _clash(mod: str, other: str) -> str:
    return tr("%(mod)s does not work with %(other)s, and both will load.", mod=mod, other=other)


@dataclass
class Entry:
    """One mod that will load: how it is matched, called, and what it says."""

    key: str
    name: str
    declared: dict = field(default_factory=dict)
    # The ModID as written, for telling apart two mods with one name: a
    # shared piece's old and new versions are often both "Quay System Full
    # Corners", and one replacing the other has to say which is which.
    ident: str = ""


@dataclass
class Check:
    """What the mods that will load say about each other."""

    game: str = ""
    # (mod, what it needs, whether that is installed and switched off)
    missing: List[Tuple[str, str, bool]] = field(default_factory=list)
    # (mod, the mod it says it does not work with) -- each pair once.
    clashes: List[Tuple[str, str]] = field(default_factory=list)
    # (the mod that will not load, the mod that replaces it)
    replaced: List[Tuple[str, str]] = field(default_factory=list)
    # By lowercased key: which of the lines below are about that mod.
    about: Dict[str, List[str]] = field(default_factory=dict)
    # The keys of the mods that will not load because another replaces them.
    dropped: set = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.clashes or self.replaced)

    def lines(self) -> List[str]:
        """One sentence a problem, replacements first: those are certain."""
        return (
            [_replaced(*pair) for pair in self.replaced]
            + [_missing(mod, needs, off) for mod, needs, off in self.missing]
            + [_clash(*pair) for pair in self.clashes]
        )

    def note(self) -> str:
        """What the Studio cannot tell, when it matters: 117 lets a mod
        require a DLC by the same list, and which DLCs are owned is not
        something a mods folder says."""
        if self.game == ANNO_117 and any(not off for _mod, _needs, off in self.missing):
            return tr(
                "Anno 117 mods can list a DLC as a requirement too. If one "
                "of those names a DLC you own, it is not a problem."
            )
        return ""

    def describe(self) -> str:
        """For the card: the one sentence, or how many of each kind there are.

        The sentences themselves go in a list of their own: on a big loadout
        they were most of the card, and the card is for seeing whether there
        is anything to look at."""
        said = self.lines()
        if not said:
            return ""
        heading = tr("What these mods say about each other:")
        if len(said) == 1:
            text = heading + "\n• " + said[0]
        else:
            # Translators: the heading, then how many mods each kind of
            # finding is about, as a list: "…: 2 mods need one that will
            # not load and 1 mod loads beside one it does not work with."
            text = tr("%(heading)s %(counts)s.", heading=heading, counts=self.counted())
        note = self.note()
        return text + ("\n" + note if note else "")

    def counted(self) -> str:
        """How many mods each kind of finding is about, as a list for a
        sentence: "2 mods need one that will not load and 1 mod …"."""
        counts = []
        dropped = len({old for old, _new in self.replaced})
        if dropped:
            counts.append(trn("%(n)s mod will not load because another replaces it",
                              "%(n)s mods will not load because others replace them", dropped))
        needing = len({mod for mod, _needs, _off in self.missing})
        if needing:
            counts.append(trn("%(n)s mod needs one that will not load",
                              "%(n)s mods need one that will not load", needing))
        clashing = len({mod for mod, _other in self.clashes})
        if clashing:
            counts.append(trn("%(n)s mod loads beside one it does not work with",
                              "%(n)s mods load beside one they do not work with", clashing))
        return join_list(counts)


def check(entries: Iterable[Entry], game: str, known: Optional[Dict[str, str]] = None) -> Check:
    """Check ``entries`` -- what will load -- against what each declares.

    ``known`` names mods that are installed but will not load, by key, so a
    requirement of one can say it is only switched off.
    """
    known = known or {}
    result = Check(game=game)
    loading: Dict[str, Entry] = {}
    for entry in entries:
        loading.setdefault(entry.key, entry)
    declared = {key: links(entry.declared, game) for key, entry in loading.items()}
    shared: Dict[str, List[Entry]] = {}
    for entry in loading.values():
        shared.setdefault(entry.name.lower(), []).append(entry)
    label = {
        key: "%s (%s)" % (entry.name, entry.ident or key)
        if len(shared[entry.name.lower()]) > 1 else entry.name
        for key, entry in loading.items()
    }

    # Deprecation first: it decides what is really there.
    replacer = _replacers(declared)
    dropped = {key for key in loading if key in replacer}
    result.dropped = set(dropped)
    for key in sorted(dropped, key=lambda one: loading[one].name.lower()):
        new = replacer[key]
        result.replaced.append((label[key], label[new]))
        _about(result, (key, new), _replaced(*result.replaced[-1]))
    there = {key for key in loading if key not in dropped}
    # What a requirement is met by: a mod that loads, or one that replaces
    # the mod it names and loads itself.
    met = set(there) | {old for old, new in replacer.items() if new in there}

    def name_of(key: str, written: str) -> str:
        if key in loading:
            return label[key]
        return known.get(key) or written

    seen_pairs = set()
    for key in sorted(there, key=lambda one: loading[one].name.lower()):
        mod = label[key]
        said = declared[key]
        for needs in said.require:
            wanted = needs.lower()
            if wanted in met or wanted == key:
                continue
            row = (mod, name_of(wanted, needs), wanted in known)
            result.missing.append(row)
            _about(result, (key,), _missing(*row))
        for other in said.incompatible:
            other_key = other.lower()
            if other_key not in there or other_key == key:
                continue
            pair = frozenset((key, other_key))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            clashing = (mod, label[other_key])
            result.clashes.append(clashing)
            _about(result, (key, other_key), _clash(*clashing))
    return result


def _about(result: Check, keys, sentence: str) -> None:
    for key in keys:
        result.about.setdefault(key, []).append(sentence)


def _replacers(declared: Dict[str, Links]) -> Dict[str, str]:
    """Old key -> the key of the mod that deprecates it, among mods that are on.

    The loader drops every mod whose ID one of these lists -- from every mod
    it adds, a dropped one included. A mod listing its own ID would drop
    itself; none in the census does, and it is read as the slip it would be.
    """
    replacer: Dict[str, str] = {}
    for key, said in declared.items():
        for old in said.deprecate:
            old = old.lower()
            if old != key:
                replacer.setdefault(old, key)
    return replacer


def replaced_origins(mods, game: str) -> Dict[str, str]:
    """Which of ``mods`` the game will not load because another replaces it:
    ``{origin: the replacing mod's origin}``.

    ``mods`` is ``(origin, key, declared)`` for every mod switched on -- a
    loadout's layers or a folder read's mods, whichever is being asked about.
    """
    mods = list(mods)
    origin_of: Dict[str, str] = {}
    said: Dict[str, Links] = {}
    for origin, key, declared in mods:
        origin_of.setdefault(key, origin)
        said.setdefault(key, links(declared, game))
    replacer = _replacers(said)
    return {
        origin: origin_of[replacer[key]]
        for origin, key, _declared in mods
        if key in replacer and origin_of[replacer[key]] != origin
    }


def replaced_layers(loadout) -> Dict[str, str]:
    """``{origin: replacing origin}`` among the loadout's mods that are on --
    what :meth:`core.changes.Loadout.not_loading` leaves out with the
    switched-off ones. Derived from the switches, not stored: switch the
    replacing mod off and the old one loads again, as it does in game."""
    return replaced_origins(
        ((layer.origin, layer_key(layer), layer.declared) for layer in switched_on(loadout)),
        loadout.game,
    )


# -- what will load once Play has run ------------------------------------------
def layer_key(layer) -> str:
    return modfolder.mod_key(layer.mod_id, modfolder.folder_name(layer.origin))


def switched_on(loadout) -> list:
    """The loadout's layers that are on: their own switch, a bundled one's
    host's, and for a piece several mods carry, any of theirs -- see
    :meth:`core.changes.Loadout.loading_copies`."""
    off = loadout.switched_off()
    return [layer for layer in loadout.layers if layer.origin not in off]


def copies_elsewhere(loadout) -> list:
    """``(layer, the copy that loads)`` for each mod the game now loads from
    a copy other than the one the Studio read: its host is off and another
    mod carrying it is on, or one carrying a newer copy has been switched on."""
    copies = loadout.loading_copies()
    return [
        (layer, copies[layer.origin])
        for layer in loadout.layers
        if copies.get(layer.origin) not in ("", layer.origin)
    ]


def same_version(loadout, layer, loading: str) -> bool:
    """Whether the copy that loads declares the version the one read does."""
    from . import versioning

    have, now = layer.version or "", loadout.copy_version(loading)
    return bool(have and now) and (
        versioning.as_the_loader_reads(have) == versioning.as_the_loader_reads(now)
    )


def about_copy(loadout, layer, loading: str) -> List[str]:
    """What to know about a mod read from one copy and loaded from another:
    whose copy loads, whether it is the version the rows are of, and what that
    means for your patch to it or your edits in it."""
    carrier = loadout.carrier_of(loading)
    where = carrier.label if carrier is not None else loading
    read = loadout.host_layer(layer.origin)
    read_in = read.label if read is not None else layer.origin
    have, now = layer.version or "", loadout.copy_version(loading)
    if same_version(loadout, layer, loading):
        lines = [tr(
            "The game loads the copy %(carrier)s carries, the same version "
            "(%(version)s) as the copy in %(read)s that these rows were read from.",
            carrier=where, version=now, read=read_in,
        )]
    elif now:
        lines = [tr(
            "The game loads the copy %(carrier)s carries, version %(now)s. These rows "
            "are of version %(have)s, the copy in %(read)s, and may not be what that "
            "copy does: reading the mods folder again reads the one that loads.",
            carrier=where, now=now, have=have or "?", read=read_in,
        )]
    else:
        lines = [tr(
            "The game loads the copy %(carrier)s carries, whose version the Studio "
            "did not record. These rows are of the copy in %(read)s: reading the "
            "mods folder again reads the one that loads.",
            carrier=where, read=read_in,
        )]
    patch = loadout.patch_for(layer.origin)
    if patch is not None and not same_version(loadout, layer, loading):
        lines.append(tr(
            "%(patch)s follows it to that copy, and was written against version "
            "%(have)s.", patch=patch.label, have=have or "?",
        ))
    elif patch is None and loadout.authored_of(layer.origin):
        lines.append(tr(
            "Your edits to it are in the copy in %s, which the game is not "
            "loading, so they do not reach the game.", read_in,
        ))
    return lines


def copies_worth_knowing(loadout) -> List[str]:
    """For Play: each mod whose loading copy is not the one read, where that
    changes what reaches the game -- another version, or edits left behind."""
    found = []
    for layer, loading in copies_elsewhere(loadout):
        # Edits made in the copy read stay in it whatever the other's version.
        left_behind = loadout.patch_for(layer.origin) is None and bool(
            loadout.authored_of(layer.origin)
        )
        if same_version(loadout, layer, loading) and not left_behind:
            continue
        found.append("%s: %s" % (layer.label, " ".join(about_copy(loadout, layer, loading))))
    return found


def after_play(loadout, found) -> Tuple[List[Entry], Dict[str, str]]:
    """What the game will load once Play has made ``loadout`` live, and the
    names of what is installed and will not.

    Every loadout is complete when it is played: what it has on loads, with
    what is bundled inside it, and nothing else -- except what Play cannot
    switch, the game's own browser's copies in Anno 1800 (``found``, a
    :class:`core.truth.Truth`, knows which), which load whatever the loadout
    says.
    """
    entries: List[Entry] = [
        Entry(layer_key(layer), layer.label, dict(layer.declared or {}), layer.mod_id or layer.origin)
        for layer in switched_on(loadout)
    ]
    installed = list(getattr(found, "installed", ()) or ()) if found is not None else []
    staying = list(found.unswitchable_others) if found is not None else []
    for mod in staying:
        entries.append(Entry(mod.installed.key, mod.name, dict(mod.installed.declared or {}),
                             mod.installed.mod_id or mod.installed.folder_name))
        for inner in installed:
            if inner.bundled and inner.folder != mod.installed.folder and inner.folder.is_relative_to(mod.installed.folder):
                entries.append(Entry(inner.key, inner.name or inner.folder_name,
                                     dict(inner.declared or {}), inner.mod_id or inner.folder_name))
    loading = {entry.key for entry in entries}
    known: Dict[str, str] = {}
    for layer in loadout.layers:
        key = layer_key(layer)
        if key and key not in loading:
            known.setdefault(key, layer.label)
    for mod in installed:
        key = mod.key
        if key and key not in loading and not mod.inert:
            known.setdefault(key, mod.name or mod.folder_name)
    return entries, known


def for_loadout(loadout, found) -> Check:
    entries, known = after_play(loadout, found)
    return check(entries, loadout.game, known)
