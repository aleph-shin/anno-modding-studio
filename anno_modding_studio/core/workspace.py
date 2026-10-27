"""The index as the mod sees it: base game assets plus the ones this mod creates.

The SQLite index is a cache of one shipped file, shared between loadouts and keyed
by that file's hash -- so loadout data must never be written into it.  This wraps
it instead: base assets come from the index, mod-created assets from the loadout's
own change set, and callers get one consistent view of both.

Keeping the two distinguishable is the point rather than an implementation
detail.  Editing a base asset emits a ModOp against the base game; editing an
asset this mod invented has to be folded into the payload that creates it, since
there is nothing in the base game to patch.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from . import effective, refs, repeats
from .changes import OP_REMOVE, Change
from .index import AssetRow, ReferenceLink

# Any run of digits: what a GUID looks like in a change's value or payload.
_NUMBERS = re.compile(r"[0-9]+")
# "Item[Product='1010017']" -> "Item": a path that picks one entry out of a
# list, compared against the path a link runs through. See _changed_paths.
_PREDICATE = re.compile(r"\[[^\]]*\]")

# What Workspace._fingerprint returns: each asset's changes as they stand, and
# the changes that create an asset.
_Stamp = Tuple[Dict[str, tuple], tuple]


class Workspace:
    """A read-through view over the base index, overlaid with the loadout."""

    def __init__(self, index, loadout):
        self.index = index
        self.loadout = loadout
        # Walking an edited asset for its links means parsing and classifying
        # its whole tree, and "what points at this?" has to do that for every
        # asset the mod touches -- so asking it about n assets in a row used to
        # cost n*n walks. A hundred imported changes made that seconds, not
        # milliseconds. Held against a fingerprint of the change set rather
        # than invalidated by hand: a stale link is a wrong answer, and there
        # are too many places that can edit a loadout to trust a hook.
        self._live_cache: Dict[tuple, Any] = {}
        self._live_stamp: Optional[_Stamp] = None
        # And against the loaded mods' order and on/off state, and the GUIDs
        # the mods make, which the walks read without any change moving.
        self._live_layers: Optional[tuple] = None
        self._live_made: Optional[frozenset] = None
        # Every creating change by the GUID it makes, the one of each the game
        # finds, and which GUIDs have more than one asset under them -- with
        # the loaded mods' order and on/off state, which decide both.
        self._creator_cache: Optional[tuple] = None
        self._creator_layers: Optional[tuple] = None
        # The row of each asset a mod adds, with what it was made from: asked
        # for by every row of the Mod changes panel that names one, each
        # time the panel groups its changes, and each is a parse of the
        # asset and a look in the texts.  1,839 of them were most of a
        # second of every edit with 68 mods loaded.  With each, the template
        # the asset names itself: its row says "inherits 1000" for none.
        self._new_rows: Dict[str, tuple] = {}
        # Every active change, grouped by the asset it shapes. See _changes_by_guid.
        self._by_guid: Optional[Dict[str, List[Change]]] = None
        self._by_guid_layers: Optional[tuple] = None
        # A fingerprint of 2,500 changes is cheap but not free, and it is asked
        # for far more often than the change set actually moves. Held for the
        # length of one question so a single walk pays for it once.
        self._stamp_held = 0
        # The loaded mods' order and on/off state, taken once for a held
        # question as the fingerprint is. See _layers.
        self._held_layers: Optional[tuple] = None

    # -- mod-created assets -------------------------------------------------
    def _made(self) -> tuple:
        """``(every, shown, standing)``: ``{guid: [change]}`` for every
        creating change, ``{guid: change}`` for the one whose asset the game
        finds, and :func:`core.repeats.standing` for the GUIDs with more than
        one asset under them.

        Cached, because working it out means parsing the payload of every
        creating change, and "is this a mod-made asset?" is asked of every
        asset the reference graph walks past.  With a mods folder loaded that
        was tens of millions of payload parses for one redraw.  Which copy is
        found is worked out once for the lot, not once per asset.
        """
        self._sync()
        layers = self._layers()
        if self._creator_cache is None or layers != self._creator_layers:
            every: Dict[str, List[Change]] = {}
            for change in self.loadout.active():
                guid = effective.new_asset_guid(change)
                if guid:
                    every.setdefault(guid, []).append(change)
            standing = repeats.standing(self.loadout, self.index) if every else {}
            shown: Dict[str, Change] = {}
            for guid, changes in every.items():
                held = standing.get(guid)
                if held is None:
                    # One copy -- or a GUID a mod removes outright and puts
                    # back, where the one put back is the last.
                    shown[guid] = changes[-1]
                elif held.change is not None:
                    shown[guid] = held.change
                # Otherwise the game finds its own, and none of these.
            self._creator_cache = (every, shown, standing)
            self._creator_layers = layers
        return self._creator_cache

    def _creators(self) -> Dict[str, Change]:
        """``{guid: change}`` for every asset this session invents that the
        game finds: of two under one GUID the one :mod:`core.repeats` says it
        finds, and neither where that is the one it ships."""
        return self._made()[1]

    def creator_changes(self) -> List:
        """Every change that creates a whole new asset, found by the game or
        not."""
        return [change for changes in self._made()[0].values() for change in changes]

    def creator_for(self, guid: str, origin: Optional[str] = None):
        """The change that creates the asset the game finds by ``guid``: None
        where that is the one it ships, or no mod makes one.

        With ``origin`` it is a mod's question: the change of that mod's
        that makes an asset under ``guid`` -- the one found where that is the
        mod's, or else its first in the file.  Whether a mod is handed the
        asset, and where its own new asset's edits go, turn on whether it
        makes one, not on whether the game finds that one.
        """
        guid = str(guid)
        # One fingerprint for the questions, not one each.
        with self.steady():
            shown = self._creators().get(guid)
            if origin is None or (shown is not None and shown.origin == origin):
                return shown
            return next((change for change in self.creators_of(guid) if change.origin == origin), None)

    def creators_of(self, guid: str) -> List:
        """Every change that makes an asset under ``guid``: in the order they
        stand in the file, where there is more than one asset under it."""
        every, _shown, standing = self._made()
        held = standing.get(str(guid))
        return list(held.copies) if held is not None else list(every.get(str(guid), ()))

    def standing_of(self, guid: str):
        """:class:`core.repeats.Standing` for a GUID with more than one asset
        under it -- which one is shown, and what else there is -- or None."""
        return self._made()[2].get(str(guid))

    def found_among(self, guid: str, counts) -> tuple:
        """``(change, there)``: of the assets under ``guid`` once the changes
        ``counts`` keeps have run, the change that makes the one the game
        finds -- None for the shipped one, or where no mod makes one -- and
        whether there is one at all.  What a mod part way down the load order
        is handed: a copy a later mod makes is not there yet."""
        guid = str(guid)
        every, made, standing = self._made()
        held = standing.get(guid)
        copies = held.copies if held is not None else every.get(guid, ())
        if not copies:
            return None, True
        shown = made.get(guid)
        if shown is not None and counts(shown):
            return shown, True
        if held is not None and held.shipped:
            return None, True
        first = next((change for change in copies if counts(change)), None)
        return first, first is not None

    def is_new_asset(self, guid: str) -> bool:
        return str(guid) in self._creators()

    def new_asset_guids(self) -> frozenset:
        """Every GUID this session invents -- asked once, not once per asset."""
        return frozenset(self._creators())

    def invented_assets(self, among=None):
        """``(guid, element)`` for every asset this session makes up.

        No shipped index has heard of these, so anything asking a question of
        the whole game has to be handed them separately.  Held, because
        building them means an effective tree apiece and a mods folder can
        invent several hundred.
        """
        self._sync()
        key = ("invented",)
        if key not in self._live_cache:
            found = []
            # One fingerprint for the lot: each asset asked for its own, and
            # a whole Anno 1800 folder invents a thousand of them over thirteen
            # thousand changes -- twenty seconds the first time a pattern opened.
            with self.steady():
                for guid in self._creators():
                    element = self.asset_element(guid)
                    if element is not None:
                        found.append((guid, element))
            self._live_cache[key] = found
        made = self._live_cache[key]
        if among is None:
            return made
        wanted = {str(guid) for guid in among}
        return [pair for pair in made if pair[0] in wanted]

    def new_asset_rows(self) -> List[AssetRow]:
        return [self._new_row(guid, change) for guid, change in self._creators().items()]

    def _new_row(self, guid: str, change) -> AssetRow:
        """A created asset's row, named the way a shipped one is: a mod's text
        for its GUID, its embedded text, a text at its LineID/OasisId -- the
        mods' or the game's -- and only then its internal name."""
        # What a row is made from: the asset as written, the name given it
        # here, and the texts the mods bring, which are another object each
        # time they are read.  Those are held with the row and asked whether
        # they are the same object: held by id() alone, a later object could
        # land where they had been and be taken for them, and the row kept a
        # name the mods no longer give it.
        made_from = (change.payload, getattr(change, "in_game_name", ""))
        names = getattr(self.index, "mod_names", None)
        held = self._new_rows.get(str(guid))
        if held is not None and held[0] == made_from and held[3] is names:
            return held[1]
        row = self._make_new_row(guid, change)
        self._new_rows[str(guid)] = (
            made_from, row, effective.new_asset_own_template(change), names,
        )
        return row

    def _make_new_row(self, guid: str, change) -> AssetRow:
        name = effective.new_asset_name(change) or "(unnamed)"
        embedded, key = effective.new_asset_text(change)
        key = key or str(guid)
        display, named_by, also = name, "", ()
        naming = getattr(self.index, "name_for", None)
        given = (getattr(change, "in_game_name", "") or "").strip()
        if given:
            # Named here, for the mod being written: that is the name it ships.
            display = given
        elif naming is not None:
            display, named_by, also = naming(str(guid), embedded, key, "", name)
            if display == name and not named_by:
                display = self.index.base_text(key) or name
        elif embedded:
            display = embedded
        return AssetRow(
            guid=str(guid),
            name=name,
            display=display,
            template=effective.new_asset_template(change),
            group_path="",
            is_new=True,
            named_by=named_by,
            also=also,
        )

    # -- asset access -------------------------------------------------------
    def asset_row(self, guid: str) -> Optional[AssetRow]:
        change = self.creator_for(guid)
        if change is not None:
            return self._new_row(str(guid), change)
        return self.index.asset_row(guid) if self.index else None

    def base_element(self, guid: str):
        """The shipped asset, with none of this mod's changes applied."""
        change = self.creator_for(guid)
        if change is not None:
            return effective.parse_payload(change)
        return self.index.asset_element(guid) if self.index else None

    def effective_asset(self, guid: str, upto=None) -> effective.EffectiveAsset:
        """The asset as it will be once this mod is applied.

        ``upto`` stops at a mod: the base game plus every mod that loads at or
        before it, and nothing after.  That is the document that mod is handed
        and the one it leaves behind, which is what somebody editing *into*
        that mod has to be looking at -- the value a later mod goes on to
        overwrite is not the value this mod's ModOp is written against.
        """
        guid = str(guid)
        # One fingerprint for the three questions, not one each.
        with self.steady():
            is_new = self.is_new_asset(guid)
            base = self.base_element(guid)
            changes = self.changes_for(guid)
            if upto is not None:
                rank = self.loadout.layer_rank(upto)
                changes = [
                    change
                    for change in changes
                    if self.loadout.layer_rank(change.origin) <= rank
                ]
                # Of two under one GUID, the one found by then: a copy a later
                # mod makes, found once it loads, is not there yet. Where no
                # copy is, the asset is shown as it will be.
                held = self.standing_of(guid)
                if held is not None:
                    maker, there = self.found_among(
                        guid, lambda change: self.loadout.layer_rank(change.origin) <= rank
                    )
                    if there and maker is not self.creator_for(guid):
                        is_new = maker is not None
                        if is_new:
                            base = effective.parse_payload(maker)
                            changes = [*changes, maker]
                        else:
                            base = self.index.asset_element(guid) if self.index else None
        return effective.build(base, changes, guid, is_new=is_new, game=self.loadout.game)

    def asset_element(self, guid: str):
        """Kept for callers that only want a tree to read."""
        return self.effective_asset(guid).element

    def base_asset_element(self, guid: str):
        """The asset as the game ships it, with none of this mod applied.

        Verification has to use this one.  Checking a change against the
        effective tree checks it against its own result -- a replace would be
        compared to the value it just wrote and always look wrong.
        """
        base = self.index.asset_element(guid) if self.index else None
        if base is not None:
            return base
        # An asset this mod creates has no shipped form; its own payload is the
        # closest thing to one.
        return self.effective_asset(guid).element

    def asset_before(self, guid: str, origin: str):
        """The asset as it stands when ``origin`` loads.

        Base game plus every mod ahead of it in load order.  A mod that edits
        something an earlier mod added is doing something perfectly ordinary,
        and checking its ModOps against the shipped file alone would call them
        broken -- they are only broken on their own.
        """
        guid = str(guid)
        rank = self.loadout.layer_rank(origin)
        earlier = [
            change
            for change in self.changes_for(guid)
            if self.loadout.layer_rank(change.origin) < rank
        ]
        if not earlier:
            return self.base_asset_element(guid)
        base = self.base_element(guid)
        if base is None:
            return None
        return effective.build(
            base, earlier, guid, is_new=self.is_new_asset(guid), game=self.loadout.game
        ).as_the_game_reads_it()

    def changes_for(self, guid: str) -> List:
        """Changes that shape this asset, including the one that creates it."""
        guid = str(guid)
        with self.steady():
            out = list(self._changes_by_guid().get(guid, ()))
            creator = self.creator_for(guid)
        if creator is not None and creator not in out:
            out.append(creator)
        return out

    def _changes_by_guid(self) -> Dict[str, List[Change]]:
        """``{guid: [change]}`` for every active change, in the order they apply.

        This used to be a scan of the whole change set per asset, which made
        anything asking about every asset quadratic: comparing 4,000 changes
        asset by asset took 147 s and 18,057 would take most of an hour (*GUID
        Explorer*, one ModOp per asset). Held against the same fingerprint as
        the walks, plus the loaded mods' order and on/off state -- those decide
        which changes are active and in what order without moving any change.
        """
        self._sync()
        layers = self._layers()
        if self._by_guid is None or layers != self._by_guid_layers:
            found: Dict[str, List[Change]] = {}
            for change in self.loadout.active():
                found.setdefault(change.guid, []).append(change)
            self._by_guid, self._by_guid_layers = found, layers
        return self._by_guid

    def display_name(self, guid: str) -> str:
        row = self.asset_row(guid)
        return row.label if row else ("GUID " + str(guid))

    def guid_exists(self, guid: str) -> bool:
        """True if anything -- base game or this mod -- already uses the GUID."""
        if self.is_new_asset(guid):
            return True
        return bool(self.index and self.index.guid_exists(guid))

    def guid_in_base_game(self, guid: str) -> bool:
        return bool(self.index and self.index.guid_exists(guid))

    # -- search -------------------------------------------------------------
    def search(self, query="", template="", group="", limit=400):
        rows = self.index.search(query, template=template, group=group, limit=limit)
        extra = self._matching_new_assets(query, template)
        # Assets this mod invented are few and highly relevant, so they lead.
        found = extra + [r for r in rows if r.guid not in {e.guid for e in extra}]
        # All but the asset whose GUID is what was typed: a new asset that
        # inherits from 1010266 says so in its Template column, matched on
        # that, and was listed above 1010266 itself.
        typed = (query or "").strip()
        if typed.isdigit():
            found.sort(key=lambda row: row.guid != typed)
        return found

    def total_matches(self, query="", template="", group="") -> int:
        base = self.index.total_matches(query, template=template, group=group)
        return base + len(self._matching_new_assets(query, template))

    def _matching_new_assets(self, query: str, template: str) -> List[AssetRow]:
        tokens = [t for t in (query or "").lower().split() if t]
        out = []
        for row in self.new_asset_rows():
            haystack = " ".join([row.guid, row.name, row.display, row.template]).lower()
            if tokens and not all(token in haystack for token in tokens):
                continue
            if template and row.template != template:
                continue
            out.append(row)
        return out

    # -- the reference graph ------------------------------------------------
    def _touched(self, guid: str) -> bool:
        """Whether this mod changes the asset, so its links must be recomputed."""
        return bool(self.changes_for(str(guid)))

    def _fingerprint(self) -> _Stamp:
        """What each asset's changes look like right now, asset by asset.

        Per asset rather than one number for the whole loadout: a value typed
        into one field moves that asset and nothing else, and the walks held for
        the other thousand are still perfectly good answers.  Rebuilding them
        all was several seconds of hang on every keystroke that landed.
        """
        found: dict = {}
        creators = []
        for change in self.loadout.changes.values():
            # Which op a change answers to is part of it: an op marked to
            # be kept as written takes the place of the narrow change it was,
            # under the same key and with the same value.
            found.setdefault(change.guid, []).append(
                (change.key, change.new_value, change.payload, change.active,
                 change.op_uid)
            )
            # An asset removed outright is one swapped, which decides which
            # of two under its GUID is shown as surely as the two do.
            if change.creates_asset or (change.op == OP_REMOVE and not change.path):
                creators.append((change.key, change.payload, change.active))
        return (
            {guid: tuple(sorted(rows)) for guid, rows in found.items()},
            tuple(sorted(creators)),
        )

    def _sync(self) -> None:
        """Drop the cached walks whose asset has moved under them.

        Checked once per question asked from outside rather than once per walk:
        the fingerprint is cheap but not free, and "what points at this?" asks
        for a walk of every asset the mod touches.
        """
        if self._stamp_held:
            return
        stamp = self._fingerprint()
        layers = self._layers()
        if stamp == self._live_stamp and layers == self._live_layers:
            return
        was_layers = self._live_layers
        if layers != was_layers:
            # Switching a mod on or off moves no change, but every answer held
            # here was read off the changes that are active.
            self._live_cache.clear()
        assets, creators = stamp
        was_assets, was_creators = self._live_stamp or ({}, None)
        moved = {
            guid
            for guid in set(assets) | set(was_assets)
            if assets.get(guid) != was_assets.get(guid)
        }
        for key in [key for key in self._live_cache if key[0] in moved]:
            del self._live_cache[key]
        if moved:
            # The bookkeeping read off the change set as a whole: which assets
            # this session touches, which ones name which, and which paths each
            # one's changes reach. All three are answers about the change set,
            # so any change moving makes all three stale.
            for key in (("touched",), ("mentions",), ("paths",)):
                self._live_cache.pop(key, None)
            # Which asset points at which can have changed, so the reverse map
            # is rebuilt -- but out of the per-asset walks that are still held,
            # which is a merge rather than a thousand tree walks.
            for key in [key for key in self._live_cache if key[0] == "reverse"]:
                del self._live_cache[key]
        if creators != was_creators:
            self._creator_cache = None
        self._by_guid = None
        self._live_stamp = stamp
        self._live_layers = layers
        if creators != was_creators or layers != was_layers:
            # A number is a link only while there is an asset under it, so
            # what every asset links to can move when another asset is made
            # or taken away -- with no change to the asset itself.
            # Held, so asking does not take the fingerprint just taken again.
            self._stamp_held += 1
            try:
                made = frozenset(self._creators())
            finally:
                self._stamp_held -= 1
                self._held_layers = None
            if made != self._live_made:
                for key in [
                    key for key in self._live_cache
                    if len(key) == 3 or key[1:] == ("read",)
                ]:
                    del self._live_cache[key]
            self._live_made = made

    def _layers(self) -> tuple:
        """The loaded mods' order and on/off state, which the caches above
        are held against besides the fingerprint.

        Taken once for a held question rather than once per look: "is this
        asset one a mod makes?" is asked 36,000 times by the References graph
        on Coins, and on 416 mods taking this each time was a second of it.
        """
        if self._stamp_held and self._held_layers is not None:
            return self._held_layers
        layers = tuple(
            (layer.origin, layer.order, layer.active) for layer in self.loadout.layers
        )
        if self._stamp_held:
            self._held_layers = layers
        return layers

    def forget_change_objects(self) -> None:
        """Let go of the change objects held between questions.

        The walks are keyed on what the changes say, which is right for an edit.
        Undo puts back a copy that says the same thing as the object it
        replaces, so the fingerprint does not move -- and the lookups that hold
        the objects themselves would go on answering with the ones undo took
        away.  Both are cheap to rebuild.
        """
        self._by_guid = None
        self._creator_cache = None

    @contextmanager
    def steady(self):
        """Take the change set as fixed for the length of a block of work.

        Grouping a whole mods folder asks thousands of questions about a loadout
        that nothing can be editing meanwhile -- the panel is drawing it. Only
        for read-only work: an edit made inside the block will not be noticed.
        """
        self._sync()
        self._stamp_held += 1
        try:
            yield self
        finally:
            self._stamp_held -= 1
            if not self._stamp_held:
                self._held_layers = None

    def _live_links(self, guid: str, confident_only: bool, with_nominal: bool = True):
        key = (str(guid), confident_only, with_nominal)
        if key not in self._live_cache:
            self._live_cache[key] = self._walk_links(guid, confident_only, with_nominal)
        return self._live_cache[key]

    def _walk_links(self, guid: str, confident_only: bool, with_nominal: bool = True):
        """Outgoing links read off the asset as this mod leaves it.

        The indexed graph describes the shipped file.  For an asset the mod
        creates or edits that is the wrong answer -- a GUID this mod just typed
        in is a link the base game never had -- so those are walked directly,
        classified against the same per-field evidence the index was built on.

        Kept or dropped by the two settings the way the index answers: the
        links the settings keep, then one per field and asset named, the
        first in the asset.
        """
        links = []
        seen = set()
        for link in self._read_links(guid):
            if confident_only and not link.confident:
                continue
            if link.nominal and not with_nominal:
                continue
            key = (link.path, link.tag, link.guid)
            if key in seen:
                continue
            seen.add(key)
            links.append(link)
        return links

    def _read_links(self, guid: str):
        """Every link in the asset as this mod leaves it, whatever the
        settings: what :meth:`_walk_links` keeps its answers from.

        Held under the asset's GUID, so a change to the asset lets it go with
        the answers made from it -- and let go too when the GUIDs the mods
        make move, since a number names an asset only while one is under it
        (see :meth:`_sync`).  Read once rather than once per setting:
        the References graph asks three settings of the same assets to say
        how many links it is hiding, and reading the tree is the cost --
        Coins on a mods folder is a thousand assets that name it.

        Against what the game will read, not what the editor draws: the editor
        keeps a removed node struck through so the removal can be undone, and a
        link inside one of those is a link the game no longer follows.
        """
        key = (str(guid), "read")
        if key not in self._live_cache:
            self._live_cache[key] = self._links_in(guid)
        return self._live_cache[key]

    def _links_in(self, guid: str) -> list:
        element = self.effective_asset(guid).as_the_game_reads_it()
        if element is None or self.index is None:
            return []
        values = element.find("Values")
        if values is None:
            return []
        links = []
        for path, tag, value, carries in refs.numeric_leaves(values):
            if value == str(guid):
                continue
            evidence = self.index.reference_field(path, tag)
            # The rule the editor's Refers-to column answers by, and the index
            # build keeps the shipped file's links by: short of a confident
            # field, the field's name and the number's size have to agree.
            if not refs.is_link(tag, value, *evidence):
                continue
            if not self.guid_exists(value):
                continue
            # Naming an asset without setting anything only means something
            # where entries at this path normally do set something.
            entries, valued = self.index.reference_entries(path, tag)
            links.append(
                ReferenceLink(
                    guid=value,
                    path=path,
                    tag=tag,
                    confident=refs.is_confident(tag, *evidence),
                    nominal=not carries and refs.path_uses_values(entries, valued),
                )
            )
        return links

    def references_from(
        self, guid: str, confident_only: bool = True, with_nominal: bool = True
    ):
        """What this asset points at, as this mod leaves it.

        ``with_nominal=False`` drops links that name an asset without setting a
        value -- but that judgement is calibrated on the shipped file, where an
        entry in a list whose siblings all carry an amount and carries none is
        very likely noise.  An asset *this loadout invents* has no shipped
        siblings to be judged against: its payload is exactly what its author
        typed, and somebody who wrote a Product with no Amount meant that link.
        So those are never hidden, which also stops a newly created asset's
        graph coming up empty.
        """
        guid = str(guid)
        # Held still for the question: "is this asset one a mod makes?" takes
        # stock of every change, and is asked of each asset the answer reaches.
        with self.steady():
            if self.is_new_asset(guid) or self._touched(guid):
                return self._live_links(
                    guid, confident_only, with_nominal or self.is_new_asset(guid)
                )
            if self.index is None:
                return []
            return self.index.references_from(guid, confident_only, with_nominal)

    def references_to(
        self, guid: str, confident_only: bool = True, with_nominal: bool = True
    ):
        """What points at this asset -- the shipped file, plus this mod's own.

        ``with_nominal=False`` hides the same links :meth:`references_from`
        hides, so the two ends of a link always agree: never one from an asset
        this loadout invents, and never one an edit has since given a value.
        """
        guid = str(guid)
        # Held still for the question, as references_from() is: the suspects
        # below are a thousand assets on a mods folder, each asked about.
        with self.steady():
            links = []
            if self.index is not None and not self.is_new_asset(guid):
                # The shipped answer is asked for whole, name-only links included:
                # an edit can give one of those a value, and a source the index had
                # already left out would never be among the suspects below.
                links = list(self.index.references_to(guid, confident_only, True))
            # An asset this session touched may have gained or lost links, and one
            # it invented is in no index at all -- but only a short list of assets
            # can possibly be involved, and walking the other thousand to find that
            # out is the difference between a redraw and a hang.
            touched = self._touched_guids()
            suspects = {
                link.guid
                for link in links
                if link.guid in touched and self._may_have_moved(link.guid, link)
            }
            suspects |= self._mentions().get(guid, frozenset())
            suspects.discard(guid)
            links = [
                link
                for link in links
                if link.guid not in suspects and (with_nominal or not link.nominal)
            ]
            for source in sorted(suspects):
                for link in self._live_links(
                    source, confident_only, with_nominal or self.is_new_asset(source)
                ):
                    if link.guid == guid:
                        links.append(
                            ReferenceLink(
                                guid=source,
                                path=link.path,
                                tag=link.tag,
                                confident=link.confident,
                                nominal=link.nominal,
                            )
                        )
            return links

    def _changed_paths(self):
        """``guid -> the ModOp paths its changes reach``, without predicates.

        A path with a predicate in it (``Item[Product='1010017']``) picks out
        one entry of a list; compared as text against the path a link travels
        through it would match nothing, so both sides are compared with the
        predicates taken off.  Erring towards matching is the safe direction:
        a false match costs one tree walk, a missed one loses a link.
        """
        key = ("paths",)
        if key not in self._live_cache:
            found: dict = {}
            for change in self.loadout.active():
                found.setdefault(str(change.guid), set()).add(
                    _PREDICATE.sub("", change.path or "")
                )
            self._live_cache[key] = found
        return self._live_cache[key]

    def _may_have_moved(self, source: str, link) -> bool:
        """Whether a change to ``source`` could have altered *this* link.

        The expensive half of "what points at this?" is re-walking every asset
        the session touches, because a change might have added or removed a
        link -- and on a whole mods folder that is thousands of tree walks for
        a graph of a dozen nodes.  But a change can only alter a link that runs
        through the part of the asset it touches.  A retuned production rate
        does not move the good it produces, so the index's answer still stands
        and the walk is not worth doing.

        Anything this session invents is walked regardless: it is in no index,
        so there is no shipped answer to keep.

        Measured against the entry the link sits in rather than the field
        itself, because a link's meaning depends on its neighbours: an entry
        naming a good and spending none of it is the "sets no value" case the
        graph can hide, so an ``Amount`` added beside the ``Product`` changes
        the answer as surely as the ``Product`` would.
        """
        if self.is_new_asset(source):
            return True
        paths = self._changed_paths().get(str(source))
        if not paths:
            return False
        entry = _PREDICATE.sub("", link.path or "")
        for path in paths:
            # A change with no path is the whole asset -- a removal, say -- and
            # everything in it is in question.
            if not path:
                return True
            if (
                path == entry
                or path.startswith(entry + "/")
                or entry.startswith(path + "/")
            ):
                return True
        return False

    def _touched_guids(self):
        """Every asset this session changes or invents."""
        key = ("touched",)
        if key not in self._live_cache:
            found = set(self._made()[0])
            found.update(str(change.guid) for change in self.loadout.active())
            self._live_cache[key] = found
        return self._live_cache[key]

    def _mentions(self):
        """``guid -> the assets whose own changes name it``.

        A link the shipped file does not have can only come from a change that
        writes the number down, so this is the complete list of assets whose
        links may have *gained* a target -- read straight off the change set,
        without building a single tree.  Together with the assets the index
        already knows point at the target, it bounds "what points at this?" to
        a handful of walks instead of one per asset the session touches.
        """
        key = ("mentions",)
        if key not in self._live_cache:
            found: dict = {}
            creators = {
                change.key: guid for guid, made in self._made()[0].items() for change in made
            }
            for change in self.loadout.active():
                source = creators.get(change.key) or str(change.guid)
                for token in _NUMBERS.findall(
                    "%s %s" % (change.new_value, change.payload)
                ):
                    found.setdefault(token, set()).add(source)
            self._live_cache[key] = found
        return self._live_cache[key]

    def shipped_references_to(self, guid: str):
        """What points at this asset in the file the game ships.

        Deliberately not the live answer.  Which assets *belong* to which is a
        property of the base game, not of the edit in progress -- a mod that
        retunes a value does not change what a buff is attached to -- and the
        live answer costs a walk of every asset the mod touches, which is not a
        price worth paying for a question whose answer has not moved.
        """
        if self.index is None or self.is_new_asset(guid):
            return []
        return self.index.references_to(str(guid), True, False)

    def reference_entries(self, path: str, tag: str):
        return self.index.reference_entries(path, tag) if self.index else (0, 0)

    def reference_field(self, path: str, tag: str):
        return self.index.reference_field(path, tag) if self.index else (0, 0, False)

    def reference_fanout(self, path: str, tag: str) -> float:
        return self.index.reference_fanout(path, tag) if self.index else 0.0

    # -- passthroughs -------------------------------------------------------
    @property
    def schema(self):
        return self.index.schema

    @property
    def asset_count(self) -> int:
        return self.index.asset_count if self.index else 0

    @property
    def text_count(self) -> int:
        return self.index.text_count if self.index else 0

    def template_counts(self):
        return self.index.template_counts() if self.index else []

    def template_counts_for(self, query: str = ""):
        """The index's counts, with the assets this mod creates counted in.

        The search lists those, so a count from the game's file alone left
        the Template list without the template of a row shown beside it.

        Each under the template it names, and one that inherits from another
        asset names none: its row says "inherits 1000" where the template
        would be, which is not a template to offer.  A shipped asset that
        inherits is counted under none either.
        """
        if not self.index:
            return []
        counts = dict(self.index.template_counts_for(query))
        for row in self._matching_new_assets(query, ""):
            # Held with the row, which was made or found held just now.
            own = self._new_rows[row.guid][2]
            if own:
                counts[own] = counts.get(own, 0) + 1
        return sorted(counts.items())

    def templates(self):
        return self.index.templates() if self.index else []

    def group_paths(self):
        return self.index.group_paths() if self.index else []

    def asset_xml(self, guid: str):
        return self.index.asset_xml(guid) if self.index else None
