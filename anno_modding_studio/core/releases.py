"""What changed since the last release, and which version that calls for.

The version is the author's, but what a version *means* is not a matter of
taste: a release that takes away an asset saves were built with is a
different kind of release from one that retunes a number, and players read
the version to know which.  The Studio knows exactly what changed since the
last release -- the change set is what it is made of -- so it can say which
kind this is, and why, and leave the author to agree.

**The last release** is the checkpoint taken when the mod was last sent to
mod.io from here, when there is one.  Otherwise it is the last export, which
the Export tab is for: it writes what goes to somebody else.  Play's builds
are for testing and take no checkpoint.  Both are in the history of the
mod's project (:meth:`.changes.Loadout.history_of`), so a release made in
another loadout that has the mod counts here too.

**The rules**, first match wins:

1. *Major* -- an asset this mod created is gone, or is there under another
   GUID.  Saves that built it lose it, and other mods that refer to it break.
2. *Minor* -- anything new or anything taken away: a new asset, a node added
   to or removed from an asset, a change to an asset the mod did not touch
   before, a change taken out or switched off.
3. *Patch* -- everything else, which is values changed on assets the mod
   already changed.

A heuristic, and said to be one.  A value can break a save and an asset can
be one nobody built.  It errs one way only: it may call a patch a minor, but
it should never call a change that breaks saves a patch.

What it cannot see: anything the Studio did not author.  A checkpoint holds
the changes made here, so a mod of yours read back in is compared on what was
done to it since, not on what it shipped with.

See ``docs/proposals/versioning-proposal.md``, section 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import versioning
from .changes import (
    KIND_EXPORT,
    KIND_PUBLISH,
    STRUCTURAL_OPS,
    Change,
    Checkpoint,
    Loadout,
)
from .i18n import number, tr, tr_noop, trn
from .versioning import MAJOR, MINOR, PATCH

# How many names a reason lists before it says "and N more".
_NAMED = 3

# The kind of release, as the Export tab's line and its question name it.
_KIND = {
    MAJOR: tr_noop("a major change"),
    MINOR: tr_noop("a minor change"),
    PATCH: tr_noop("a patch change"),
}
_KIND_SHORT = {
    MAJOR: tr_noop("Major change"),
    MINOR: tr_noop("Minor change"),
    PATCH: tr_noop("Patch change"),
}


@dataclass
class Release:
    """The last release: what it was called, and the checkpoint it left."""

    version: str
    checkpoint: Checkpoint
    published: bool


@dataclass
class Suggestion:
    """Which kind of release the work since the last one is, and why."""

    since: str  # the version of the last release
    level: Optional[str]  # MAJOR, MINOR, PATCH, or None: nothing changed
    reasons: List[str] = field(default_factory=list)  # most serious first
    current: str = ""  # the version as it stands now
    # For the changelog: new assets and removed ones by name, and how many
    # values changed on how many assets.
    added: List[str] = field(default_factory=list)
    gone: List[str] = field(default_factory=list)
    retuned: int = 0
    retuned_assets: List[str] = field(default_factory=list)
    newly_changed: List[str] = field(default_factory=list)
    reshaped: int = 0
    taken_out: int = 0

    @property
    def version(self) -> str:
        """The version this calls for, or "" when there is none to give."""
        if self.level is None:
            return ""
        return versioning.bump(self.since, self.level) or ""

    @property
    def covered(self) -> bool:
        """Whether the version as it stands is already enough for it.

        Past the suggestion counts: going further than the Studio would is
        the author's call.  A version that does not order is not judged.
        """
        if self.level is None or not self.version:
            return True
        if not versioning.key(self.current) or not versioning.key(self.version):
            return True
        return self.met

    @property
    def met(self) -> bool:
        """Whether the version as it stands was compared and is enough.

        What the lines saying "covers it" go by: ``covered`` is also true of
        a version not judged -- empty while it is retyped, or without
        numbers -- and that is not one to name as meeting the change.
        """
        if self.level is None or not self.version:
            return False
        if not versioning.key(self.current) or not versioning.key(self.version):
            return False
        return versioning.trimmed(self.current) >= versioning.trimmed(self.version)


def last_release(loadout: Loadout, origin: Optional[str] = None) -> Optional[Release]:
    """The release of ``origin`` -- the mod the loadout releases unless
    named -- that the next one is measured from, or None before the first.
    Looked for in that mod's project history."""
    if origin is None:
        origin = loadout.home or ""
    history = loadout.history_of(origin)
    sent = loadout.published or {}
    if sent.get("checkpoint"):
        for checkpoint in reversed(history):
            if checkpoint.timestamp == sent["checkpoint"]:
                return Release(sent.get("version") or checkpoint.version, checkpoint, True)
    for checkpoint in reversed(history):
        if checkpoint.kind in (KIND_EXPORT, KIND_PUBLISH):
            return Release(checkpoint.version, checkpoint, checkpoint.kind == KIND_PUBLISH)
    return None


def suggest(loadout: Loadout, origin: Optional[str] = None) -> Optional[Suggestion]:
    """What the work on ``origin`` since the last release calls for.

    ``origin`` defaults to the mod the loadout releases.  None before the
    first release, when there is nothing to measure from and the first
    version is whatever the author types.
    """
    if origin is None:
        origin = loadout.home or ""
    release = last_release(loadout, origin)
    if release is None:
        return None
    before = release.checkpoint.as_changes(origin)
    after = [c for c in loadout.authored() if c.origin == origin]
    found = classify(before, after)
    found.since = release.version
    found.current = loadout.version_of(origin)
    return found


def classify(before: List[Change], after: List[Change]) -> Suggestion:
    """The kind of release that goes from ``before`` to ``after``.

    Only what is switched on counts on either side: a change switched off is
    not in the mod.
    """
    was = {c.key: c for c in before if c.active}
    now = {c.key: c for c in after if c.active}
    made_before = _created(was.values())
    made_now = _created(now.values())
    touched = {c.guid for c in was.values()} | set(made_before)

    gone = [name for guid, name in made_before.items() if guid not in made_now]
    added = [name for guid, name in made_now.items() if guid not in made_before]
    # Edits to an asset that is new since the last release are part of adding
    # it, and already said by naming it.
    fresh = set(made_now) - set(made_before)

    ordinary_was = {k: c for k, c in was.items() if not c.creates_asset}
    ordinary_now = {
        k: c for k, c in now.items() if not c.creates_asset and c.guid not in fresh
    }
    structural = [
        c for k, c in ordinary_now.items() if k not in ordinary_was and c.op in STRUCTURAL_OPS
    ] + [
        c for k, c in ordinary_was.items() if k not in ordinary_now and c.op in STRUCTURAL_OPS
    ]
    new_assets = sorted({
        _label(c)
        for k, c in ordinary_now.items()
        if k not in ordinary_was and c.guid not in touched and c.op not in STRUCTURAL_OPS
    })
    taken_out = [
        c for k, c in ordinary_was.items() if k not in ordinary_now and c.op not in STRUCTURAL_OPS
    ]
    retuned = [
        c for k, c in ordinary_now.items()
        if c.op not in STRUCTURAL_OPS and c.guid in touched and (
            k not in ordinary_was or ordinary_was[k].new_value != c.new_value
        )
    ] + [
        # A payload edited in place: the same addition, carrying something else.
        c for k, c in now.items()
        if k in was and c.op in STRUCTURAL_OPS and was[k].payload != c.payload
        and not (c.creates_asset and _payload_guid(c) != _payload_guid(was[k]))
    ]

    reasons: List[str] = []
    level: Optional[str] = None
    if gone:
        level = MAJOR
        reasons.append(trn(
            "An asset this mod added is gone: %(names)s. Saves that built it "
            "lose it, and mods that refer to it break.",
            "%(n)s assets this mod added are gone: %(names)s. Saves that built "
            "them lose them, and mods that refer to them break.",
            len(gone), names=_names(gone),
        ))
    minor = []
    if added:
        minor.append(trn("%(n)s new asset: %(names)s.", "%(n)s new assets: %(names)s.",
                         len(added), names=_names(added)))
    if structural:
        minor.append(trn("%(n)s node added to or taken out of assets.",
                         "%(n)s nodes added to or taken out of assets.", len(structural)))
    if new_assets:
        minor.append(trn("Changes to %(n)s asset it did not change before: %(names)s.",
                         "Changes to %(n)s assets it did not change before: %(names)s.",
                         len(new_assets), names=_names(new_assets)))
    if taken_out:
        minor.append(trn("%(n)s change taken out or switched off.",
                         "%(n)s changes taken out or switched off.", len(taken_out)))
    if minor:
        level = level or MINOR
        reasons.extend(minor)
    if retuned:
        level = level or PATCH
        assets = sorted({_label(c) for c in retuned})
        if len(assets) == 1:
            reasons.append(trn("%(n)s value changed on 1 asset: %(names)s.",
                               "%(n)s values changed on 1 asset: %(names)s.",
                               len(retuned), names=_names(assets)))
        else:
            reasons.append(trn("%(n)s value changed on %(assets)s assets: %(names)s.",
                               "%(n)s values changed on %(assets)s assets: %(names)s.",
                               len(retuned), assets=number(len(assets)), names=_names(assets)))
    return Suggestion(
        since="",
        level=level,
        reasons=reasons,
        added=added,
        gone=gone,
        retuned=len(retuned),
        retuned_assets=sorted({_label(c) for c in retuned}),
        newly_changed=new_assets,
        reshaped=len(structural),
        taken_out=len(taken_out),
    )


def headline(found: Suggestion) -> str:
    """One line for the Export tab: the kind, the version, and whether it is met."""
    if found.level is None:
        return tr("No changes since %s.", found.since)
    said = {
        "since": found.since,
        "kind": tr(_KIND[found.level]),
        "meaning": tr(versioning.MEANING[found.level]),
    }
    wanted = found.version
    if not wanted:
        return tr("Since %(since)s: %(kind)s (%(meaning)s).", **said)
    if found.met:
        return tr("Since %(since)s: %(kind)s (%(meaning)s). %(current)s covers it.",
                  current=found.current, **said)
    return tr("Since %(since)s: %(kind)s (%(meaning)s), so the next version is %(version)s.",
              version=wanted, **said)


def short(found: Suggestion) -> str:
    """The same, short enough to sit under the version field.

    The Export tab's left column is already as tall as a laptop screen, so the
    line there says the kind and whether the version meets it; the reasons go
    in its tooltip and in full on the question publishing asks.
    """
    if found.level is None:
        return tr("No changes since %s.", found.since)
    kind = tr(_KIND_SHORT[found.level])
    if found.met:
        return tr("%(kind)s since %(since)s. %(current)s covers it.",
                  kind=kind, since=found.since, current=found.current)
    return tr("%(kind)s since %(since)s (%(meaning)s).", kind=kind, since=found.since,
              meaning=tr(versioning.MEANING[found.level]))


def changelog(found: Optional[Suggestion]) -> str:
    """A first draft of the changelog, from the same comparison.

    Plain lines, to be edited: it can say what changed, never why.
    """
    if found is None or found.level is None:
        return ""
    lines = []
    if found.added:
        lines.append("Added: %s." % ", ".join(found.added))
    if found.gone:
        lines.append("Removed: %s." % ", ".join(found.gone))
    if found.newly_changed:
        lines.append("Now also changes: %s." % ", ".join(found.newly_changed))
    if found.reshaped:
        lines.append(
            "Added or removed %s in assets." % _count(found.reshaped, "entry", "entries")
        )
    if found.retuned:
        lines.append(
            "Changed %s on %s."
            % (_count(found.retuned, "value"), ", ".join(found.retuned_assets))
        )
    if found.taken_out:
        lines.append("Took out %s." % _count(found.taken_out, "change"))
    return "\n".join(lines)


def _created(changes) -> Dict[str, str]:
    """GUID -> name of every asset these changes create."""
    out = {}
    for change in changes:
        if change.creates_asset:
            guid = _payload_guid(change)
            if guid:
                out[guid] = "%s (%s)" % (_payload_name(change), guid)
    return out


def _label(change: Change) -> str:
    """What to call the asset a change is about."""
    if change.creates_asset:
        return _payload_name(change)
    return change.asset_label or change.guid


def _payload_guid(change: Change) -> str:
    from .generator import payload_guid

    return payload_guid(change.payload)


def _payload_name(change: Change) -> str:
    """The new asset's own name, which its payload carries; the label else."""
    try:
        from lxml import etree

        root = etree.fromstring(change.payload.encode("utf-8"))
        name = (root.findtext("Values/Standard/Name") or "").strip()
    except Exception:  # noqa: BLE001 - a payload that does not parse still has a label
        name = ""
    return name or change.summary or change.asset_label or "an asset"


def _count(number: int, noun: str, plural: str = "") -> str:
    return "%d %s" % (number, noun if number == 1 else (plural or noun + "s"))


def _names(names: List[str]) -> str:
    shown = ", ".join(names[:_NAMED])
    if len(names) > _NAMED:
        shown = trn("%(names)s and %(n)s more", "%(names)s and %(n)s more",
                    len(names) - _NAMED, names=shown)
    return shown
