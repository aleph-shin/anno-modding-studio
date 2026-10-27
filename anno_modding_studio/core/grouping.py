"""Bundling a change list into the things a player would recognise.

Sorting changes by asset is easy and already half of it.  The harder half is
that an asset is not a thing.  A shrine in Anno 117 is a building asset, a
second building asset for the other culture's version of it, an Effect the two
share, a Buff that Effect applies, and a pool that lists them -- five assets for
one object you can point at on screen.  Editing "the shrine" means touching
several of them, and a flat list by asset scatters that work.

There is no field that says which assets belong together, so this reads it off
the reference graph:

**Ownership.**  An asset that only two or three other assets refer to exists for
those assets.  A Buff named by exactly one Effect is that Effect's buff; Money,
named by thousands, is nobody's.

**Catalogues do not own.**  Being listed in an asset pool or a construction menu
is not ownership, and the difference is measurable: a pool names fifty assets
through one field, while ``Building/FunctionalEffects/Item/FunctionalEffect``
names one or two.  So a link only counts when its *field* is narrow -- see
:meth:`AssetIndex.reference_fanout`.

**One asset may stand between two.**  A mod that changes a building and the buff
underneath it usually leaves the Effect in the middle alone, so the walk is
allowed to pass through assets the mod does not touch.  Two hops, because that
covers building → effect → buff and going further gained almost nothing on the
shipped files.

The result is deliberately timid.  On 200 assets picked at random from Anno 117
it finds 198 groups -- it merges when there is evidence and otherwise leaves
each asset alone, which is the right way round for something that only decides
how a list is drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Sequence, Set, Tuple

from . import effective
from .changes import is_own_mod
from .i18n import tr, trn

if TYPE_CHECKING:
    from .changes import ModLayer
    from .keptops import View

# A link only counts as ownership if a source names this few assets through the
# field it travels.  Four sits well clear of the narrow fields (a building names
# one or two functional effects) and well below the catalogues (asset pools and
# construction categories run to dozens).
FANOUT_MAX = 4.0

# An asset with more referrers than this is shared infrastructure, not something
# that belongs to anything: a population level, a good, an icon.
OWNERS_MAX = 3

# How many un-touched assets a chain is allowed to pass through.
BRIDGE_HOPS = 2


def asset_guid(change) -> str:
    """The asset a change is about, which is not always ``change.guid``.

    A change that creates an asset is anchored on a *neighbouring* one, so its
    own GUID names where it is inserted rather than what it is.
    """
    if change.creates_asset:
        return effective.new_asset_guid(change) or change.guid
    return change.guid


@dataclass(frozen=True)
class AssetChanges:
    """One asset's changes."""

    guid: str
    label: str
    changes: tuple

    @property
    def active(self) -> int:
        return sum(1 for change in self.changes if change.active)


@dataclass(frozen=True)
class ChangeGroup:
    """One or more assets that belong together, and everything done to them."""

    lead: str
    label: str
    assets: tuple

    @property
    def is_cluster(self) -> bool:
        return len(self.assets) > 1

    @property
    def changes(self) -> List:
        out = []
        for asset in self.assets:
            out.extend(asset.changes)
        return out

    @property
    def active(self) -> int:
        return sum(asset.active for asset in self.assets)

    def caption(self) -> str:
        """What the group is, past its lead asset's name."""
        total = len(self.changes)
        changes = trn("%(n)s change", "%(n)s changes", total)
        if not self.is_cluster:
            return changes
        assets = trn("%(n)s asset", "%(n)s assets", len(self.assets))
        return tr(", ", context="between items of a list").join((assets, changes))


class Owners:
    """Who each asset belongs to, as far as the reference graph can say.

    Worth holding on to between redraws, and split in two so that it can be.
    For a shipped asset the answer is a property of the base game: it cannot
    move while the app is open, so it is cached for good.  For an asset a mod
    invents there is no shipped graph to read, so the answer comes from the
    session and has to be dropped whenever the session moves.

    A lookup that fails is neither: it is answered "no owners" until the next
    redraw and then asked again, so one bad moment is not remembered as a fact
    about the asset for the rest of the session.
    """

    def __init__(self, view, invented=()):
        self.view = view
        self.invented = set(invented)
        self._shipped: Dict[str, Tuple[str, ...]] = {}
        self._live: Dict[str, Tuple[str, ...]] = {}
        self._failed: Set[str] = set()

    def refresh(self, invented) -> None:
        """Take a new set of mod-made assets, keeping the shipped answers."""
        self.invented = set(invented)
        self._live.clear()
        self._failed.clear()

    def of(self, guid: str) -> Tuple[str, ...]:
        made_up = guid in self.invented
        cache = self._live if made_up else self._shipped
        if guid in cache:
            return cache[guid]
        if guid in self._failed:
            return ()
        found: Tuple[str, ...] = ()
        try:
            links = (
                self.view.references_to(guid, True, False)
                if made_up
                else self.view.shipped_references_to(guid)
            )
        except Exception:  # noqa: BLE001 - grouping must never break the panel
            self._failed.add(guid)
            return ()
        names = set()
        for link in links:
            if link.guid == guid:
                continue
            if self.view.reference_fanout(link.path, link.tag) > FANOUT_MAX:
                continue  # a catalogue entry, not an owner
            names.add(link.guid)
        if 1 <= len(names) <= OWNERS_MAX:
            found = tuple(sorted(names))
        cache[guid] = found
        return found


def _connect(guids: Sequence[str], owners: Owners) -> Dict[str, str]:
    """Union assets that own one another, possibly across an untouched asset."""
    parent = {guid: guid for guid in guids}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    members = set(guids)
    for guid in guids:
        seen = {guid}
        frontier = list(owners.of(guid))
        for _hop in range(BRIDGE_HOPS):
            beyond: List[str] = []
            for other in frontier:
                if other in seen:
                    continue
                seen.add(other)
                if other in members:
                    left, right = find(guid), find(other)
                    if left != right:
                        parent[left] = right
                else:
                    beyond.extend(owners.of(other))
            frontier = beyond
    return {guid: find(guid) for guid in guids}


def _lead(members: Sequence[str], owners: Owners) -> str:
    """The asset to name a cluster after: the one the others hang off.

    Reach rather than "has no owner": a cluster can easily have two members
    nobody in it owns -- the Roman and Celtic versions of a shrine, say -- and
    the useful name is whichever of them the rest of the group descends from.
    """
    def reach(start: str) -> int:
        seen = {start}
        frontier = [start]
        while frontier:
            node = frontier.pop()
            for other in members:
                if other in seen:
                    continue
                if node in owners.of(other):
                    seen.add(other)
                    frontier.append(other)
        return len(seen)

    return max(members, key=lambda guid: (reach(guid), -len(owners.of(guid)), guid))


def _cluster_name(view, members: Sequence[str], lead: AssetChanges) -> str:
    """What to call assets grouped together, without passing one off as all.

    The assets making up one thing in game usually share its name -- a
    shrine's building, the other culture's building and the buff are all
    *Shrine of Epona* -- and then that name is the group's.  Where they do not,
    the group is named for the asset the rest hang off and says there is more
    to it, so the row never reads as though one asset were the whole group.
    """
    def shown(guid: str) -> str:
        row = view.asset_row(guid) if hasattr(view, "asset_row") else None
        return ((row.display or row.name) if row is not None else "").strip()

    names = {shown(guid) for guid in members}
    if len(names) == 1 and "" not in names:
        return names.pop()
    # Translators: a group in the changes list named after its lead asset,
    # and how many other assets belong with it: "Shrine of Epona + 2 related".
    return trn("%(name)s + %(n)s related", "%(name)s + %(n)s related",
               len(members) - 1, name=shown(lead.guid) or lead.label)


@dataclass(frozen=True)
class OpPart:
    """A group inside an op that is a group kept whole, and what is in it.

    *Trade Union Rebalance* is one group holding one group per specialist, and
    iModYourAnno's switches are those inner groups; read as one op of a
    thousand changes, the list hid the very things its options dialog names.
    """

    label: str
    off: bool                # switched off: it comes to no changes today
    groups: tuple = ()       # ChangeGroups directly in this group
    parts: tuple = ()        # OpParts inside it
    # What the mod's options call it, where a tweak tool switches it: the row
    # shows this, so the list and the options dialog name it the same way.
    title: str = ""
    # Other switches that name this group too -- an author's slip, which
    # leaves them doing nothing -- and whether the mod tags it for a tweak
    # tool with no switch at all, which a tweak tool then leaves off.
    also: tuple = ()
    unswitched: bool = False

    @property
    def shown(self) -> str:
        return self.title or self.label

    @property
    def changes(self) -> List:
        out = [change for group in self.groups for change in group.changes]
        for part in self.parts:
            out.extend(part.changes)
        return out

    @property
    def assets(self) -> int:
        return sum(len(group.assets) for group in self.groups) + sum(
            part.assets for part in self.parts
        )

    def caption(self) -> str:
        if self.off:
            return tr("switched off, so nothing in it runs")
        return trn("%(n)s change", "%(n)s changes", len(self.changes))


@dataclass(frozen=True)
class OpGroup:
    """One kept op and the changes it currently comes to, grouped by asset.

    Its own level of the list rather than four hundred rows scattered through
    the assets: an op is one thing the mod does, and reading it as one row that
    says *every wall matching this, +3 elevation* is the whole point of having
    kept it.  An op that is a group kept whole, holding groups of its own, has
    those as a level of their own too -- see :class:`OpPart`.
    """

    view: View
    groups: tuple            # ChangeGroups, as for anything else
    parts: tuple = ()        # OpParts, drawn above the groups
    # For a group kept as a group with ops in it (Op.scope): the ops and
    # groups inside that have a row of their own, in file order.  The ones
    # that are one change each are among ``groups``, as they are under a mod.
    inside: tuple = ()

    @property
    def uid(self) -> str:
        return self.view.uid

    @property
    def changes(self) -> List:
        out = [change for group in self.groups for change in group.changes]
        for part in self.parts:
            out.extend(part.changes)
        for held in self.inside:
            out.extend(held.changes)
        return out

    @property
    def assets(self) -> int:
        return len({asset_guid(change) for change in self.changes})

    @property
    def active(self) -> int:
        return sum(1 for change in self.changes if change.active)

    def every(self):
        """This and every op group inside it, outermost first."""
        yield self
        for held in self.inside:
            yield from held.every()

    def label(self) -> str:
        tool = getattr(self.view.op, "tool", None) or {}
        return tool.get("title") or self.view.op.summary or self.view.expression

    def caption(self) -> str:
        if getattr(self.view.op, "scope", False):
            if self.view.op.idle:
                return tr("does not run as things stand")
            return trn("%(n)s change", "%(n)s changes", len(self.changes))
        return self.view.caption()


@dataclass(frozen=True)
class ModSection:
    """One loaded mod and everything it changes.

    The outermost level of the list, drawn for a single mod as well: the list
    reads the same way whether one mod is open or sixty.
    """

    layer: ModLayer
    groups: tuple            # ChangeGroups, as for a single mod
    ops: tuple = ()        # OpGroups, drawn above them
    # The ops the Studio could not read, drawn below them: each is one thing
    # the mod does that the rows above do not show, and an export carries it
    # exactly as written -- so it is a row of its own, greyed, rather than a
    # count in a tooltip.
    unread: tuple = ()       # Unread entries, in file order

    @property
    def origin(self) -> str:
        return self.layer.origin

    @property
    def label(self) -> str:
        return self.layer.label

    @property
    def changes(self) -> List:
        out = [change for group in self.groups for change in group.changes]
        for op in self.ops:
            out.extend(op.changes)
        return out

    @property
    def active(self) -> int:
        return sum(group.active for group in self.groups) + sum(
            op.active for op in self.ops
        )

    def caption(self) -> str:
        total = len(self.changes)
        # Each asset once, however many of the mod's ops reach it.
        assets = len({asset_guid(change) for change in self.changes})
        # A tally rather than a sentence: each count stands on its own.
        bits = [
            trn("%(n)s asset", "%(n)s assets", assets),
            trn("%(n)s change", "%(n)s changes", total),
        ]
        if self.unread:
            # Translators: a count of ModOps the Studio could not read, as in
            # "5 assets, 12 changes, 2 unread".
            bits.append(trn("%(n)s unread", "%(n)s unread", len(self.unread)))
        return tr(", ", context="between items of a list").join(bits)


def owners_for(view, held=None):
    """The ownership cache to use, reusing one the caller is keeping.

    Which assets belong to which is a property of the base game, so an answer
    once worked out stays true for as long as the app is open.  Handing the same
    cache back in is what makes a redraw after one typed value cost nothing.

    A cache kept from another view is not reused: it asks the view it was made
    with, and after the game files are adopted again that one's index is closed.
    """
    if not _graph(view):
        return None
    if held is None or held.view is not view:
        return Owners(view, _invented(view))
    held.refresh(_invented(view))
    return held


def by_mod(loadout, view=None, owners=None) -> List[ModSection]:
    """The change list as a stack of mods, each grouped by asset.

    In load order, so the list reads the way the game applies it: what is
    further down wins where two mods reach for the same thing.
    """
    from . import keptops

    sections = []
    # One owners cache for the whole folder, not one per mod: sixty mods asking
    # the same question about the same shared assets is the difference between
    # a redraw and a coffee break.
    owners = owners_for(view, owners)
    # And one reading of the kept ops, for the same reason.
    by_origin: Dict[str, List] = {}
    for entry in keptops.of(loadout):
        by_origin.setdefault(entry.origin, []).append(entry)
    # Every loaded mod, not only the ones that have changed something. A mod
    # that is loaded is a mod you can unload, re-read at other settings or put
    # a patch in front of, and this list is where those are asked for -- so one
    # that has brought nothing yet, a patch just made among them, still needs
    # its row. And origin "", the loadout's own mod, on the same terms: once
    # it has been made it has a row, and an empty row says both that it is
    # there and that nothing is in it. It used to be left out while empty, as
    # a heading over nothing, and then left out under some of the Start
    # tab's cards and not others; every such rule was one more thing to know
    # before the list could be read. What decides it now is only whether the
    # mod exists -- see :attr:`Loadout.own_mod`.
    listed = set(loadout.origins(loaded=True)) | set(by_origin)
    if loadout.has_own_mod():
        listed.add("")
    # Every change put in order once, then dealt out by mod.  Asking each mod
    # for its own share sorted all of them again: a folder of 400 mods and
    # 31,000 changes sorted 13 million keys to draw the list once.
    in_order: Dict[str, List] = {}
    for change in loadout.ordered():
        in_order.setdefault(change.origin, []).append(change)
    for origin in sorted(listed, key=loadout.layer_rank):
        layer = loadout.own_layer() if is_own_mod(origin) else loadout.layer(origin)
        if layer is None:
            layer = loadout.own_layer().__class__(origin=origin, name=origin)
        op_groups, rest = split(
            loadout, view, origin, owners, views=by_origin.get(origin, ()),
            ordered=in_order.get(origin, []),
        )
        unread = sorted(loadout.not_read(origin), key=lambda entry: entry.order)
        sections.append(
            ModSection(layer, tuple(rest), tuple(op_groups), tuple(unread))
        )
    return sections


def split(loadout, view=None, origin=None, owners=None, views=None, ordered=None):
    """``(op groups, everything else)`` for one mod.

    A change that came out of a kept op is drawn under that op rather than among
    the assets, so the list says what the mod does -- one row per op -- rather
    than repeating the same edit under four hundred buildings.

    ``ordered`` is this mod's changes in :meth:`Loadout.ordered` order, when
    the caller has them already.
    """
    if ordered is None:
        ordered = [
            change for change in loadout.ordered()
            if origin is None or change.origin == origin
        ]
    from . import keptops

    if views is None:
        views = keptops.of(loadout, origin)

    op_groups: list = []
    claimed: set = set()
    # A group kept as a group with ops in it has what is inside it under its
    # row: collected here by the group, innermost first, and made into rows
    # once all of a group's own are known.
    scopes = {entry.uid for entry in views if getattr(entry.op, "scope", False)}
    held: Dict[str, list] = {}      # group uid -> OpGroups inside it
    loose: Dict[str, list] = {}     # group uid -> changes of one-row ops inside it

    def home(entry):
        """The innermost group ``entry`` sits in that has a row here, or ""."""
        for uid in reversed(getattr(entry.op, "within", None) or []):
            if uid in scopes:
                return uid
        return ""

    for entry in reversed(list(views)):
        inside = home(entry)
        if entry.uid in scopes:
            changes = loose.pop(entry.uid, [])
            claimed.update(change.key for change in changes)
            made = OpGroup(
                entry,
                tuple(build(loadout, view, origin=origin, owners=owners, changes=changes)),
                (),
                tuple(reversed(held.pop(entry.uid, []))),
            )
        elif keptops.stands_alone(entry):
            # An op that is one change, at the node it names: its row is that
            # change's, among the asset's others, as it always was.  With
            # every op kept as written that is most of them, and a row for
            # the op over a row for its asset over a row for its change
            # would say one thing three times.
            if inside:
                loose.setdefault(inside, []).extend(entry.changes)
            continue
        else:
            changes = [change for change in entry.changes]
            claimed.update(change.key for change in changes)
            declared = getattr(entry.op, "parts", None) or []
            if not declared and not any(change.within for change in changes):
                made = OpGroup(
                    entry,
                    tuple(build(loadout, view, origin=origin, owners=owners,
                                changes=changes)),
                )
            else:
                groups, parts = _parts(loadout, view, origin, owners, declared, changes)
                made = OpGroup(entry, groups, parts)
        if inside:
            held.setdefault(inside, []).append(made)
        else:
            op_groups.append(made)
    op_groups.reverse()
    rest = build(
        loadout,
        view,
        origin=origin,
        owners=owners,
        changes=[change for change in ordered if change.key not in claimed],
    )
    return op_groups, rest


def _parts(loadout, view, origin, owners, declared, changes):
    """``(groups, parts)`` for an op holding groups of its own: the changes in
    none of them grouped by asset, and a :class:`OpPart` per group, nested as
    the groups are, in the order the mod declares them.

    A change naming a group the op does not list -- a loadout saved before
    the groups were recorded, say -- still gets a row for that group, so no
    change is left out of the list.
    """
    by_path: Dict[tuple, List] = {}
    for change in changes:
        by_path.setdefault(tuple(change.within), []).append(change)
    off: Dict[tuple, bool] = {}
    titles: Dict[tuple, dict] = {}
    known: List[tuple] = []
    for part in declared:
        path = tuple(str(name) for name in (part.get("path") or ()))
        if not path:
            continue
        off[path] = bool(part.get("off"))
        titles[path] = part
        known.append(path)
    known.extend(path for path in by_path if path)
    # Every group a deeper one sits in is a row as well.
    every: List[tuple] = []
    for path in known:
        for depth in range(1, len(path) + 1):
            if path[:depth] not in every:
                every.append(path[:depth])

    def grouped(path):
        return tuple(build(loadout, view, origin=origin, owners=owners,
                           changes=by_path.get(path, [])))

    def make(prefix):
        inside = tuple(
            make(path) for path in every
            if len(path) == len(prefix) + 1 and path[: len(prefix)] == prefix
        )
        said = titles.get(prefix, {})
        return OpPart(
            prefix[-1], off.get(prefix, False), grouped(prefix), inside,
            str(said.get("title") or ""),
            tuple(str(name) for name in (said.get("also") or ())),
            bool(said.get("unswitched")),
        )

    top = tuple(make(path) for path in every if len(path) == 1)
    return grouped(()), top


def _graph(view) -> bool:
    """Whether this view can answer "what points at this?" at all."""
    return view is not None and hasattr(view, "shipped_references_to")


def _invented(view) -> frozenset:
    """The GUIDs this session makes up, asked once rather than per asset."""
    known = getattr(view, "new_asset_guids", None)
    return known() if known is not None else frozenset()


def build(loadout, view=None, origin=None, owners=None, changes=None) -> List[ChangeGroup]:
    """Group a loadout's changes: by asset, and then by what belongs together.

    ``view`` is a workspace or index -- anything that can name an asset and
    answer the reference graph.  Without one this still groups by asset, which
    is the part that needs no evidence.

    ``origin`` narrows it to one mod's changes.
    """
    buckets: Dict[str, List] = {}
    for change in loadout.ordered() if changes is None else changes:
        if origin is not None and change.origin != origin:
            continue
        buckets.setdefault(asset_guid(change), []).append(change)
    if not buckets:
        return []

    def label_for(guid: str, changes) -> str:
        if view is not None:
            row = view.asset_row(guid)
            if row is not None:
                return row.label
        return changes[0].asset_label or ("GUID " + guid)

    assets = {
        guid: AssetChanges(guid, label_for(guid, changes), tuple(changes))
        for guid, changes in buckets.items()
    }

    order = list(buckets)
    if not _graph(view):
        roots = {guid: guid for guid in order}
        owners = None
    else:
        if owners is None:
            owners = Owners(view, _invented(view))
        roots = _connect(order, owners)

    clusters: Dict[str, List[str]] = {}
    for guid in order:
        clusters.setdefault(roots[guid], []).append(guid)

    groups = []
    for members in clusters.values():
        lead = members[0] if owners is None or len(members) == 1 else _lead(members, owners)
        rest = sorted(
            (guid for guid in members if guid != lead),
            key=lambda guid: assets[guid].label.lower(),
        )
        ordered = [assets[lead]] + [assets[guid] for guid in rest]
        label = assets[lead].label
        if len(members) > 1:
            label = _cluster_name(view, members, assets[lead])
        groups.append(ChangeGroup(lead, label, tuple(ordered)))

    groups.sort(key=lambda group: (group.label.lower(), group.lead))
    return groups
