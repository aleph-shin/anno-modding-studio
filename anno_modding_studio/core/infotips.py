"""Which InfoTips a mod patches, and which mods patch the same ones.

A mod changes the game's tooltips with ``data/infotips/export.bin.xml``, a
ModOps patch on ``export.bin``.  67 of the 857 Anno 1800 mods on mod.io ship
one, most of them as shared pieces bundled over and over -- Serp's
``shared_TT_MenuInputOutputAmounts``, ``TooltipBugFixes``, the pools and
definitions.  Anno 117 has one, and it is empty.

The Studio does not show what a patched tooltip comes to: the file it patches
is binary, and an InfoTip is a small program of containers, conditions and
text references rather than a set of fields (see the gaps proposal, 4.2).  It
does say which InfoTips each mod touches -- by GUID, nearly always written
straight into the op's path, ``//InfoTipData[Guid='500934']/…`` -- and so
which InfoTips two mods that load both patch, where the second one's ops run
against what the first left behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from lxml import etree

from . import disk
from .i18n import join_list, tr, trn
from .modfolder import INFOTIPS_PATCH as PATCH

# An op aimed at one InfoTip, or several: ``//InfoTipData[Guid='1']`` at the
# start of its path, whatever follows.
_KEYED = re.compile(r"^/{0,2}InfoTipData\[(?P<pred>[^\]]*)\]")
_GUID = re.compile(r"\bGuid\s*=\s*['\"]?(\d+)['\"]?", re.IGNORECASE)


@dataclass
class Patch:
    """What one InfoTip patch does, by InfoTip GUID."""

    # GUID -> how many ops change that InfoTip.
    changes: Dict[str, int] = field(default_factory=dict)
    # InfoTips it adds, whole.
    adds: List[str] = field(default_factory=list)
    # Ops aimed at no one InfoTip: a search across all of them.
    searches: int = 0
    ops: int = 0
    # Why the file could not be read, if it could not.
    error: str = ""

    @property
    def touched(self) -> List[str]:
        return list(dict.fromkeys(list(self.changes) + self.adds))


def patch_file(folder) -> Optional[Path]:
    if not folder:
        return None
    path = Path(folder) / PATCH
    return path if path.is_file() else None


def read(path: Path, game: str) -> Patch:
    """What the patch at ``path`` does, with its includes."""
    from . import modops

    found = Patch()
    try:
        root = etree.parse(str(path), etree.XMLParser(
            recover=True, huge_tree=True, resolve_entities="internal", no_network=True)).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        found.error = str(exc)
        return found
    if root is None:
        # Translators: follows "Its InfoTip patch could not be read:".
        found.error = tr("it holds no XML the loader could read")
        return found
    try:
        ops = modops.parse_ops(root, [], path.name, source=path, game=game)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        found.error = str(exc)
        return found
    for op in ops:
        if op.verbatim:
            continue  # a group's own marker; what is inside it comes as ops
        found.ops += 1
        if not op.kind:
            continue  # no type its game reads: the game passes it by
        entries = [
            (element.findtext("Guid") or "").strip()
            for element in op.payload
            if isinstance(getattr(element, "tag", None), str) and element.tag == "InfoTipData"
        ]
        if entries and all(entries):
            found.adds.extend(guid for guid in entries if guid not in found.adds)
            continue
        guids = _targets(op)
        if guids is None:
            found.searches += 1
            continue
        for guid in guids:
            found.changes[guid] = found.changes.get(guid, 0) + 1
    return found


def _targets(op) -> Optional[List[str]]:
    """The InfoTips an op names, by GUID; None for a search.

    Its ``GUID`` and its ``@GUID`` are as the reader reads them
    (:attr:`.modops.SourceOp.guids`), each looked for as written, as the
    loader looks for it: ``GUID="1, 2"`` names 1, and " 2" is no
    InfoTip's."""
    if op.guid:
        return [guid for guid in dict.fromkeys(op.guids) if guid == guid.strip()]
    keyed = _KEYED.match((op.path or "").strip())
    if keyed:
        return list(dict.fromkeys(_GUID.findall(keyed.group("pred"))))
    return None


_READ = disk.ReadCache(read)


def read_cached(path: Path, game: str) -> Patch:
    found = _READ.get(path, game)
    if found is None:
        # Translators: follows "Its InfoTip patch could not be read:".
        return Patch(error=tr("it could not be opened"))
    return found


@dataclass
class Overview:
    """Every InfoTip patch among the mods that load."""

    by_mod: Dict[str, Patch] = field(default_factory=dict)
    # InfoTip GUID -> the mods that patch it, by name, in load order: only
    # those patched by more than one.
    shared: Dict[str, List[str]] = field(default_factory=dict)

    def others(self, origin: str, label: str) -> Dict[str, List[str]]:
        """For one mod: each InfoTip it patches that another mod also does,
        and those other mods."""
        patch = self.by_mod.get(origin)
        if patch is None:
            return {}
        return {
            guid: [name for name in self.shared[guid] if name != label]
            for guid in patch.touched if guid in self.shared
        }


def overview(loadout) -> Overview:
    """The InfoTip patches of the mods that load: switched on, not replaced."""
    from . import dependencies

    found = Overview()
    gone = loadout.replaced()
    loading = sorted(
        (layer for layer in dependencies.switched_on(loadout)
         if layer.folder and layer.origin not in gone),
        key=lambda layer: layer.order,
    )
    patched_by: Dict[str, List[str]] = {}
    for layer in loading:
        path = patch_file(layer.folder)
        if path is None:
            continue
        patch = found.by_mod[layer.origin] = read_cached(path, loadout.game)
        for guid in patch.touched:
            names = patched_by.setdefault(guid, [])
            # By name: two copies of one shared piece are one mod to a reader.
            if layer.label not in names:
                names.append(layer.label)
    found.shared = {guid: names for guid, names in patched_by.items() if len(names) > 1}
    return found


def describe(patch: Patch, others: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """Lines for a mod's tooltip: what its InfoTip patch does."""
    if patch.error:
        return [tr("Its InfoTip patch could not be read: %s", patch.error)]
    if not patch.ops:
        return []
    bits = []
    if patch.changes:
        # Translators: each of these is an item of the list after "Patches the
        # game's tooltips:".
        bits.append(trn("changes %(n)s InfoTip", "changes %(n)s InfoTips", len(patch.changes)))
    if patch.adds:
        bits.append(trn("adds %(n)s InfoTip", "adds %(n)s InfoTips", len(patch.adds)))
    if patch.searches:
        bits.append(trn("has %(n)s op that searches every InfoTip",
                        "has %(n)s ops that search every InfoTip", patch.searches))
    lines = [tr("Patches the game's tooltips: %s. What they come to is not shown here.",
                ", ".join(bits))]
    for guid, names in sorted((others or {}).items(), key=lambda item: int(item[0]))[:6]:
        named = names[:3]
        if len(names) > 3:
            # Translators: the last of a list of mods, "A, B, C and 2 more".
            named = [*named, trn("%(n)s more", "%(n)s more", len(names) - 3, context="mods")]
        lines.append("  " + tr("InfoTip %(guid)s is patched by %(mods)s as well",
                               guid=guid, mods=join_list(named)))
    if others and len(others) > 6:
        lines.append("  " + trn("and %(n)s more InfoTip another mod patches too",
                                "and %(n)s more InfoTips another mod patches too",
                                len(others) - 6))
    return lines
