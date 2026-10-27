"""Count the ModOps in a folder of mods by what each one works out at load.

    python tools/op_counts.py --folder C:/bench/mods-150
    python tools/op_counts.py --census C:/census --game anno-117-pax-romana
    python tools/op_counts.py --census ... --game anno-1800 --mods 150 --json counts.json

Section 4 of docs/proposals/ops-as-written-proposal.md counts the bench
folder's ModOps by which of their target, payload and condition are
expressions, and those counts were taken by a script that was never kept.
Issue 131 asks for the same counts of Anno 117 before the reader is built on
them, so this is that script, kept: what it counts is what
``modops.describe`` says of each op, the description the Mod changes panel
draws, so the two cannot drift apart.

**What it reads.**  ``--folder`` is a mods folder, each mod in it and each mod
bundled inside one.  ``--census`` is a census from ``modio_fetch.py``, each
unit's ``files`` read the same way; ``--mods`` takes the first so many units by
mod.io id, which is how ``bench.py`` builds its folder.  Every ``assets.xml``
patch is parsed as a mods-folder reading parses it, includes and groups and
all, and each op is described.  Nothing is decided: no game files are needed,
and no condition is answered.

**What it counts.**

* every ModOp, by which of its properties are expressions -- none, the
  condition, the target, both, the payload, or any other mix;
* each property alone, split by kind: a search across assets or a pattern
  inside named ones; a copy, a computed value or flags; a question about the
  document, the loadout or a setting;
* the ops inside a group, how deep, and how many of those are constant in
  everything of their own;
* the groups, and what else sits among the ops: a tweak tool's markup, an
  ``<Asset>`` written straight under ``<ModOps>``, a group carried whole;
* the ops that make assets, whose payload is an asset and not a change to
  one.

An op that ships switched off (``Skip``), by its own or by that of a group it
sits in, is counted with the rest, as section 4 counted it, and counted again
on its own line.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modfolder, modop, modops  # noqa: E402

# The census keeps each game's units under the name mod.io gives the game.
CENSUS_GAMES = ("anno-1800", "anno-117-pax-romana")

# The rows of section 4's first table, in its order.
MIXES = (
    ("none", "None"),
    ("condition", "Condition"),
    ("target", "Target"),
    ("target+condition", "Target and condition"),
    ("payload", "Payload"),
    ("other", "Any other pair, or all three"),
)


def mix(described: modop.Described) -> str:
    """Which row of section 4's table an op falls in."""
    held = [
        name for name, there in (
            ("target", described.pattern),
            ("payload", described.computed),
            ("condition", described.conditional),
        ) if there
    ]
    if not held:
        return "none"
    if held == ["condition"]:
        return "condition"
    if held == ["target"]:
        return "target"
    if held == ["target", "condition"]:
        return "target+condition"
    if held == ["payload"]:
        return "payload"
    return "other"


def _makes_assets(op: modops.SourceOp) -> bool:
    """Whether what the op applies is an asset, and not a change to one."""
    return any(
        getattr(element, "tag", None) == "Asset" for element in op.payload
    )


def _verbatim_kind(op: modops.SourceOp) -> str:
    """What a piece carried among the ops is, by its element."""
    text = op.verbatim.lstrip()
    if text.startswith("<Asset"):
        return "asset"
    if text[:6].lower() == "<group":
        return "group carried whole"
    return "tweak tool's markup"


def mods_in(folder: Path) -> List[Path]:
    """Each mod in a mods folder, and each mod bundled inside one."""
    return modfolder.mod_folders(folder)


def ops_of(mod: Path) -> Iterable[modops.SourceOp]:
    """Every op of one mod's ``assets.xml`` patches, as a reading parses
    them, includes read where they are named."""
    patches, extras, _metadata = modops.scan_folder(mod)
    included: List[Path] = []
    for path in patches:
        root, _repairs = modops.read_patch(path)
        if root is None:
            continue
        shown = path.relative_to(mod).as_posix()
        yield from modops.parse_ops(
            root, extras, shown, source=path, base=mod, taken=included,
            game=modops.game_for_patch(Path(shown)),
        )


class Counts:
    """What has been counted so far."""

    def __init__(self) -> None:
        self.mods = 0
        self.modops = 0
        self.by_mix: Counter = Counter()
        self.target: Counter = Counter()
        self.payload: Counter = Counter()
        self.condition: Counter = Counter()
        self.skipped = 0
        self.in_scope = 0
        self.in_scope_constant = 0
        self.deepest = 0
        self.scopes = 0
        self.carried: Counter = Counter()
        self.makes_assets = 0
        self.by_game: Counter = Counter()
        # The groups switched off: a group is read before the ops inside it.
        self._off: set = set()

    def add(self, op: modops.SourceOp) -> None:
        if op.shelved:
            # The Studio's own op, switched off in a comment: not the mod's.
            return
        if op.scope:
            self.scopes += 1
            if modops.describe(op).skip:
                self._off.add(op.scope)
            return
        if op.verbatim:
            # An Anno 117 <Asset> under <ModOps> makes an asset too, and is
            # counted here alone: it is no op, and counted with the ops that
            # make assets it was a share of a total it is not in.
            self.carried[_verbatim_kind(op)] += 1
            return
        described = modops.describe(op)
        self.modops += 1
        self.by_game[op.game or "unknown"] += 1
        self.by_mix[mix(described)] += 1
        if described.target:
            self.target[described.target] += 1
        if described.payload:
            self.payload[described.payload] += 1
        if described.condition:
            self.condition[described.condition] += 1
        if described.skip or self._off.intersection(op.scopes):
            self.skipped += 1
        if op.scopes:
            self.in_scope += 1
            self.deepest = max(self.deepest, len(op.scopes))
            if described.constant:
                self.in_scope_constant += 1
        if _makes_assets(op):
            self.makes_assets += 1

    def as_dict(self) -> Dict[str, object]:
        return {
            "mods": self.mods,
            "modops": self.modops,
            "by_game": dict(self.by_game),
            "by_mix": {key: self.by_mix.get(key, 0) for key, _label in MIXES},
            "target": dict(self.target),
            "payload": dict(self.payload),
            "condition": dict(self.condition),
            "skipped": self.skipped,
            "in_scope": self.in_scope,
            "in_scope_constant": self.in_scope_constant,
            "deepest": self.deepest,
            "scopes": self.scopes,
            "carried": dict(self.carried),
            "makes_assets": self.makes_assets,
        }


def _share(part: int, whole: int) -> str:
    return "%.1f%%" % (100.0 * part / whole) if whole else "-"


def report(counts: Counts) -> str:
    """The counts, laid out as section 4 lays them out."""
    total = counts.modops
    lines = [
        "%d ModOps in %d mods (%s)." % (
            total, counts.mods,
            ", ".join("%s %d" % item for item in sorted(counts.by_game.items())),
        ),
        "",
        "| Expressions | Ops | Share |",
        "|---|---|---|",
    ]
    for key, label in MIXES:
        found = counts.by_mix.get(key, 0)
        lines.append("| %s | %d | %s |" % (label, found, _share(found, total)))
    lines.append("")

    def each(title: str, counter: Counter, names: Dict[str, str]) -> None:
        parts = ", ".join(
            "%d %s" % (counter.get(key, 0), label) for key, label in names.items()
        )
        lines.append("- %s: %d in all. %s." % (title, sum(counter.values()), parts))

    each("Target", counts.target, {
        modop.TARGET_SEARCH: "search across assets",
        modop.TARGET_PATTERN: "pattern inside named assets",
    })
    each("Condition", counts.condition, {
        modop.ASKS_DOCUMENT: "about the document",
        modop.ASKS_LOADOUT: "about which mods are loaded",
        modop.ASKS_SETTING: "about the mod's settings",
    })
    each("Payload", counts.payload, {
        modop.PAYLOAD_COPY: "copy",
        modop.PAYLOAD_COMPUTED: "computed",
        modop.PAYLOAD_FLAGS: "flags",
    })
    lines.append(
        "- In a scope: %d ops (%s), up to %d deep, %d of them constant in "
        "everything of their own." % (
            counts.in_scope, _share(counts.in_scope, total), counts.deepest,
            counts.in_scope_constant,
        )
    )
    carried = ", ".join(
        "%d %s" % (found, what) for what, found in sorted(counts.carried.items())
    )
    lines.append(
        "- Beside the ModOps: %d scopes%s." % (
            counts.scopes, ("; carried, " + carried) if carried else "",
        )
    )
    lines.append(
        "- Making assets: %d ops (%s)." % (
            counts.makes_assets, _share(counts.makes_assets, total),
        )
    )
    lines.append(
        "- Shipped switched off (Skip): %d ops." % counts.skipped
    )
    return "\n".join(lines)


def census_mods(census: Path, game: str, limit: Optional[int]) -> List[Path]:
    """The mods of a census's units for one game, the first ``limit`` units
    by mod.io id where it is given."""
    import modio_roundtrip

    found: List[Path] = []
    for unit in modio_roundtrip.first_units(census / game, limit):
        # As the census itself reads a unit: a zip that held the mod's own
        # contents at its top is that mod, and not its ``data`` folder.
        folders, _roots = modio_roundtrip.folders_of(unit / "files")
        found.extend(folders)
    return found


def count(mods: Iterable[Path]) -> Counts:
    counts = Counts()
    for mod in mods:
        counts.mods += 1
        for op in ops_of(mod):
            counts.add(op)
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--folder", type=Path, help="a mods folder")
    where.add_argument("--census", type=Path, help="a census folder from modio_fetch.py")
    parser.add_argument("--game", choices=CENSUS_GAMES, default=CENSUS_GAMES[0],
                        help="census: which game's units (default %(default)s)")
    parser.add_argument("--mods", type=int, help="census: only the first so many units by id")
    parser.add_argument("--json", type=Path, help="also write the counts here")
    args = parser.parse_args(argv)

    if args.folder is not None:
        mods = mods_in(args.folder)
    else:
        mods = census_mods(args.census, args.game, args.mods)
    counts = count(mods)
    print(report(counts))
    if args.json is not None:
        args.json.write_text(json.dumps(counts.as_dict(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
