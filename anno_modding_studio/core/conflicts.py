"""Where two loaded mods reach for the same thing.

With a whole mods folder open, the question worth answering is not "does this
mod work" but "do these mods work *together*".  The game gives no answer: it
applies every ModOp in load order and whatever was written last is what you get,
silently.  Two mods retuning the same building's cost do not warn anybody; one
of them simply has no effect.

That is decidable here, because an imported mod is no longer a wide replace of
half an asset -- it is a list of the individual nodes it claims.  Two mods clash
when they claim the same node, or when one removes a node the other edits inside
of.  Anything else is two mods editing different parts of the same asset, which
narrow ModOps are precisely designed to let them do, and which is not worth a
warning.

Only switched-on changes count.  Turning one mod's change off is how a clash
gets settled, so a clash has to stop being reported once it is.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Sequence, Tuple

from .changes import OP_REMOVE
from .i18n import join_list, number, tr, trn

# What the later mod does to the earlier one.
OVERWRITES = "overwrites"   # both set the same field, to different values
AGREES = "agrees"           # both set the same field, to the same value
REMOVES = "removes"         # one deletes a node the other edits inside
# The later mod overrides the earlier one *on purpose*: it is that mod's patch,
# or it declares in its own modinfo that it loads after it. Two mods reaching
# for one field is only a problem when neither knew about the other, and a mod
# that names another in its load order plainly did. On a loadout the loadout's
# own mod is the same thing over the whole list: the changes you make to the mods
# you play, laid over all of them. So this is still listed,
# because you may well want to know which mods touch a value, but it is not
# counted among the things that want looking at.
INTENDED = "intended"


def _covers(outer: str, inner: str) -> bool:
    """Whether a node at ``outer`` contains the one at ``inner``."""
    return inner == outer or inner.startswith(outer + "/")


@dataclass(frozen=True)
class Clash:
    """One node that more than one mod claims."""

    guid: str
    label: str
    path: str
    kind: str
    changes: tuple  # in load order, so the last one is the one that wins

    @property
    def origins(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for change in self.changes:
            if change.origin not in seen:
                seen.append(change.origin)
        return tuple(seen)

    @property
    def winner(self):
        return self.changes[-1]

    @property
    def is_harmless(self) -> bool:
        return self.kind in (AGREES, INTENDED)

    # Why the override is by design, when it is: "patch", "loadout" or "declared".
    expected: str = ""

    def describe(self, name) -> str:
        """One line, with ``name(origin)`` supplying readable mod names."""
        others = [name(origin) for origin in self.origins[:-1]]
        last = name(self.origins[-1])
        if self.kind == AGREES:
            return tr("%s set this to the same value", join_list([*others, last]))
        if self.kind == INTENDED:
            if self.expected == "patch":
                return tr("%(last)s is your patch for %(others)s, and "
                          "overriding it is its job", last=last, others=join_list(others))
            if self.expected == "loadout":
                return tr("%(last)s holds your changes to this loadout, and "
                          "overriding %(others)s is its job", last=last, others=join_list(others))
            return tr("%(last)s declares it loads after %(others)s, so it means "
                      "to win here", last=last, others=join_list(others))
        if self.kind == REMOVES:
            return tr("%(last)s removes this, undoing what %(others)s did here",
                      last=last, others=join_list(others))
        if not others[1:]:
            return tr("%(all)s both set this, and %(last)s wins",
                      all=join_list([*others, last]), last=last)
        return tr("%(all)s all set this, and %(last)s wins",
                  all=join_list([*others, last]), last=last)


def _claimed(change) -> str:
    """The node a change is about: the field an ensure makes sure of, which
    is inside the node its op is aimed at, and otherwise its path."""
    if change.ensured:
        held = change.sets
        return held[0] if held is not None else change.ensured
    return change.path


def _clash_between(earlier, later) -> str:
    """How two changes to overlapping nodes interact, or "" if they do not."""
    if earlier.origin == later.origin:
        return ""
    if _claimed(earlier) == _claimed(later):
        # A replace and an ensure of the same field set it just the same: the
        # later one finds the field there and writes over it.
        one, other = earlier.sets, later.sets
        if one is not None and other is not None:
            return AGREES if one[1] == other[1] else OVERWRITES
        if later.op == OP_REMOVE and earlier.op != OP_REMOVE:
            return REMOVES
        # Two additions at the same anchor both happen; nothing is lost.
        return ""
    # Different paths: only a removal reaches beyond its own node, by taking
    # everything under it with it -- and only backwards.  A mod that removes a
    # node an *earlier* mod edited undoes that work; one that removes a node a
    # *later* mod goes on to edit has already run by then, and that later mod
    # was read against the document the removal leaves behind.
    if later.op == OP_REMOVE and _covers(later.path, _claimed(earlier)):
        return REMOVES
    return ""


def find(loadout) -> List[Clash]:
    """Every node that two or more switched-on mods claim, in load order."""
    if not loadout.is_multi_mod:
        return []
    by_asset: Dict[str, List] = {}
    for change in loadout.active():
        by_asset.setdefault(change.guid, []).append(change)

    found: List[Clash] = []
    for guid, changes in by_asset.items():
        if len({change.origin for change in changes}) < 2:
            continue
        # Keyed by the node that is actually contested: for a removal that
        # swallows an edit, that is the node being removed.
        grouped: Dict[Tuple[str, str], List] = {}
        for position, earlier in enumerate(changes):
            for later in changes[position + 1:]:
                kind = _clash_between(earlier, later)
                if not kind:
                    continue
                one, other = _claimed(earlier), _claimed(later)
                outer = one if len(one) <= len(other) else other
                bucket = grouped.setdefault((outer, kind), [])
                held = {change.key for change in bucket}
                for change in (earlier, later):
                    if change.key not in held:
                        bucket.append(change)
        for (path, kind), involved in grouped.items():
            clash = Clash(
                guid=guid,
                label=involved[0].asset_label,
                path=path,
                kind=kind,
                changes=tuple(involved),
            )
            why = "" if kind == AGREES else _by_design(loadout, clash.origins)
            if why:
                clash = replace(clash, kind=INTENDED, expected=why)
            found.append(clash)
    found.sort(key=lambda clash: (clash.label.lower(), clash.path))
    return found


def _by_design(loadout, origins) -> str:
    """Why the last of ``origins`` overriding the earlier ones is deliberate.

    ``"patch"`` when the winner is one of their patches, ``"loadout"`` when it
    is a loadout's own mod, ``"declared"`` when it names one of them in its own
    load order, and ``""`` when neither -- which is the ordinary case of two mods
    that have never heard of each other.

    A loadout's own mod is a patch for the whole list. A mod you are writing to
    publish is not: a mod loaded beside it is there to check the two work
    together, and a field both set is what that check is for.

    A patch is proof: overriding the mod it patches is the entire reason it
    exists.  A declared ``LoadAfterIds`` is strong evidence rather than proof --
    the author knew that mod might be there and chose to come after it -- but it
    is evidence about the mod, not about this particular field, which is why
    this greys the row rather than hiding it.
    """
    if not origins[-1]:
        return "loadout" if loadout.own.loads_last else ""
    winner = loadout.layer(origins[-1])
    if winner is None:
        return ""
    earlier = set(origins[:-1])
    if any(origin in earlier for origin in loadout.needed(winner)):
        return "patch"
    named = {}
    for origin in earlier:
        layer = loadout.layer(origin)
        for key in (layer.mod_id, layer.origin) if layer is not None else ():
            if key:
                named[str(key).strip().lower()] = origin
    for declared in winner.load_after:
        if str(declared).strip().lower() in named:
            return "declared"
    return ""


def by_change(loadout) -> Dict[tuple, Clash]:
    """Which clash, if any, each change is caught up in -- for painting rows."""
    out: Dict[tuple, Clash] = {}
    for clash in find(loadout):
        for change in clash.changes:
            # A change in two clashes is shown under the one that costs it
            # something, which is never the one both mods agree on.
            if change.key not in out or out[change.key].is_harmless:
                out[change.key] = clash
    return out


def between(loadout) -> Dict[Tuple[str, str], int]:
    """How many nodes each pair of mods contests, worst pair first."""
    tally: Dict[Tuple[str, str], int] = {}
    for clash in find(loadout):
        if clash.is_harmless:
            continue
        origins = clash.origins
        for position, one in enumerate(origins):
            for other in origins[position + 1:]:
                tally[(one, other)] = tally.get((one, other), 0) + 1
    return dict(
        sorted(tally.items(), key=lambda item: (-item[1], item[0]))
    )


def summarise(loadout, name, shared=None, repeated=None) -> str:
    """A sentence for the changes panel, or "" when nothing is contested.

    ``shared`` is the loadout's script clashes (:func:`core.scripts.clashes`),
    when the caller has them; they are asked for here otherwise.  Two script
    mods claiming one module name are contested just as two mods setting one
    field are, and the same dialog lists both. ``repeated`` is the GUIDs taken
    twice (:func:`core.repeats.find`), which the same dialog lists as well."""
    from . import repeats, scripts

    clashes = [clash for clash in find(loadout) if not clash.is_harmless]
    shared = scripts.clashes(loadout) if shared is None else shared
    names, guids = scripts.summarise(shared), repeats.summarise(repeated or [])
    if names and guids:
        # Translators: two counts of problems, as "1 script name shared" and
        # "2 GUIDs taken twice", on the changes panel's conflicts button.
        said = tr("%(scripts)s, %(guids)s", scripts=names, guids=guids)
    else:
        said = names or guids
    if not clashes:
        return said
    mods = {origin for clash in clashes for origin in clash.origins}
    if said:
        return trn("%(n)s field contested by %(mods)s mods, %(also)s",
                   "%(n)s fields contested by %(mods)s mods, %(also)s",
                   len(clashes), mods=number(len(mods)), also=said)
    return trn("%(n)s field contested by %(mods)s mods",
               "%(n)s fields contested by %(mods)s mods",
               len(clashes), mods=number(len(mods)))


def touching(loadout, guid: str) -> Sequence[str]:
    """Which mods change one asset, in load order."""
    seen: List[str] = []
    for change in loadout.active():
        if change.guid == guid and change.origin not in seen:
            seen.append(change.origin)
    return seen
