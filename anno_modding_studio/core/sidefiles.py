"""What a mod's other patched files do, in a sentence each.

Besides ``assets.xml`` and its texts, a mod can patch the game's template
list (``templates.xml``, 118 Anno 1800 mods and 19 Anno 117 ones in the census),
Anno 1800's ``.cfg`` and ``.fc`` model files (``world_map_01.fc.xml``, which
all nineteen that do it use to add sessions to the world map), and make one
game path read another with a link file (``.lnk``, six mods, one of them with
82,333 island variants).  None of it is modelled, and all of it is carried on
export.  Listed as "a ModOps patch for another game file" or "N .lnk files
that replace a game file", a mod looked like it did something it does not,
or like 82,000 unexplained files; this says what each actually is.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from lxml import etree

from . import modop
from .i18n import join_list, tr, trn

_NAMED = re.compile(r"""Name\s*=\s*['"]([^'"]+)['"]""")
# The ops that place something beside the node they find rather than change it.
_BESIDE = (modop.OP_ADD_NEXT, modop.OP_ADD_PREV)


def _ops(path: Path):
    try:
        tree = etree.parse(str(path), etree.XMLParser(
            recover=True, remove_comments=True, resolve_entities="internal", no_network=True))
    except (OSError, etree.XMLSyntaxError):
        return []
    return tree.getroot().iter("ModOp") if tree.getroot() is not None else []


def templates(path: Path) -> str:
    """What a ``templates.xml`` patch does: the templates it adds and the ones
    it changes, by name.  A search it makes is named by the names it looks
    for; one that names none is shown by the search itself."""
    added: List[str] = []
    changed: List[str] = []
    searches: List[str] = []
    total = 0
    for op in _ops(path):
        total += 1
        # Which game a templates.xml is for, its place does not say: read as
        # either game reads it.
        kind, aimed, _classic = modop.type_and_path(dict(op.attrib))
        for template in op.iterchildren("Template"):
            name = (template.findtext("Name") or "").strip()
            if name and name not in added:
                added.append(name)
        if kind in _BESIDE:
            continue  # it names where the new one goes, not what it changes
        names = []
        if op.get("Template"):
            names.append(op.get("Template").strip())
        names += _NAMED.findall(aimed)
        if not names and kind:
            searches.append(aimed or "?")
        for name in names:
            # One it adds itself is its own business, not a change to the game's.
            if name not in changed and name not in added:
                changed.append(name)
    bits = []
    if added:
        # Translators: said of a templates.xml patch; %s names templates.
        bits.append(tr("adds %s", _listed(added)))
    if changed:
        # Translators: said of a templates.xml patch; %s names templates.
        bits.append(tr("changes %s", _listed(changed)))
    if len(searches) > 1:
        bits.append(trn(
            "changes whatever %(search)s finds (and %(n)s more search)",
            "changes whatever %(search)s finds (and %(n)s more searches)",
            len(searches) - 1, search=searches[0],
        ))
    elif searches:
        bits.append(tr("changes whatever %s finds", searches[0]))
    if not total:
        return tr("has no ops")
    return "; ".join(bits) or tr("no ops the Studio could make out")


def model_patch(path: Path) -> str:
    """What a ``.cfg.xml`` or ``.fc.xml`` patch does to the file it names."""
    target = Path(path).name[:-4]
    entries = 0
    ops = 0
    auto = False
    for op in _ops(path):
        ops += 1
        entries += sum(1 for child in op if isinstance(child.tag, str))
        auto = auto or any((node.text or "").strip().startswith("$auto") for node in op.iter())
    said = trn(
        "patches %(file)s: %(n)s op adding %(entries)s",
        "patches %(file)s: %(n)s ops adding %(entries)s",
        ops, file=target, entries=trn("%(n)s entry", "%(n)s entries", entries),
    )
    if auto:
        said += "; " + tr("its $auto ids are given out when the game loads it")
    return said


def links(root: Path, found: List[str]) -> str:
    """What a mod's link files do, with one of them as the example."""
    for shown in found:
        try:
            target = (Path(root) / shown).read_text(encoding="utf-8-sig", errors="replace").strip()
        except OSError:
            continue
        if target:
            return tr(
                "each has the game read the file it names in its place, and "
                "%(link)s reads %(target)s", link=shown[:-4], target=target.splitlines()[0].strip(),
            )
    return tr("each has the game read the file it names in its place")


def _listed(names: List[str], most: int = 4) -> str:
    if len(names) <= most:
        return join_list(names)
    return trn("%(names)s and %(n)s more", "%(names)s and %(n)s more",
               len(names) - most, names=", ".join(names[:most]))
