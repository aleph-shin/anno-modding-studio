"""The ledger, ``docs/ledger.md``: what was noticed and not fixed, one row each.

    python tools/ledger.py              how many rows, by impact, and the oldest
    python tools/ledger.py --release    fails while a row has not been triaged
                                        at the release being made

A standard a change fell short of, a rough edge, a smell: each is a row of the
ledger's table, not a line in a PR that nobody reads again. What keeps the
ledger from being a place things go to be forgotten is held here:

- ``problems`` is the ``ledger`` rule of ``tools/standards_check.py``: a row
  that is not well formed, an ID used twice, an impact that is not one of
  ``IMPACTS``, and a row kept at more than ``MOST_KEPT`` releases. A row is
  fixed, made an issue or dropped by its third release; it is not kept again.
- ``--release`` is a step of ``.github/workflows/release.yml``: a tag is not
  built while a row's *Kept* does not name the version being released. Every
  row is looked at once per release, and keeping it is written down.
- ``tools/check.py`` prints the summary on every run, so the count is seen.

Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "ledger.md"
VERSION_FILE = ROOT / "anno_modding_studio" / "__init__.py"

HEADER = ("ID", "Added", "Impact", "Where", "What", "Kept")
# Who would notice: a person using the Studio, someone changing its code, or
# nobody much.
IMPACTS = ("user", "maintainer", "cosmetic")
# The releases a row may be kept at. At the next it is fixed, made an issue,
# or dropped.
MOST_KEPT = 2
NOT_YET = "-"

_ID = re.compile(r"^L([1-9]\d*)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELEASE = re.compile(r"^\d+\.\d+\.\d+$")
_CELL = re.compile(r"(?<!\\)\|")
_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)


@dataclass(frozen=True)
class Row:
    id: str
    added: str
    impact: str
    where: str
    what: str
    kept: Tuple[str, ...]
    line: int


def _cells(line: str) -> List[str]:
    inner = line.strip()
    inner = inner[1:-1] if inner.endswith("|") else inner[1:]
    return [cell.strip() for cell in _CELL.split(inner)]


def read(text: str) -> Tuple[List[Row], List[Tuple[int, str]]]:
    """The table's rows, and what is wrong with it as ``(line, said)``.
    The table is the one whose header is :data:`HEADER`."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip().startswith("|") and tuple(_cells(line)) == HEADER), None)
    if start is None:
        return [], [(1, "has no table headed | %s |" % " | ".join(HEADER))]
    rows, problems = [], []
    for i in range(start + 2, len(lines)):
        line = lines[i]
        if not line.lstrip().startswith("|"):
            break
        number = i + 1
        cells = _cells(line)
        if len(cells) != len(HEADER):
            problems.append((number, "has %d cells, not %d" % (len(cells), len(HEADER))))
            continue
        ident, added, impact, where, what, kept = cells
        said = []
        if not _ID.match(ident):
            said.append("ID %r is not L and a number" % ident)
        if not _DATE.match(added):
            said.append("Added %r is not a date, YYYY-MM-DD" % added)
        if impact not in IMPACTS:
            said.append("Impact %r is not one of %s" % (impact, ", ".join(IMPACTS)))
        if not where or not what:
            said.append("says nothing under Where or What")
        releases = () if kept in ("", NOT_YET) else tuple(part.strip() for part in kept.split(","))
        if any(not _RELEASE.match(release) for release in releases):
            said.append("Kept %r is not %s or releases, 0.2.0, 0.3.0" % (kept, NOT_YET))
        elif len(releases) > MOST_KEPT:
            said.append("kept at %d releases: fix it, make it an issue, or drop it" % len(releases))
        problems += [(number, "%s %s" % (ident, item)) for item in said]
        rows.append(Row(ident, added, impact, where, what, releases, number))
    seen = {}
    for row in rows:
        if row.id in seen:
            problems.append((row.line, "%s is used on line %d as well" % (row.id, seen[row.id])))
        seen.setdefault(row.id, row.line)
    return rows, problems


def problems(text: str) -> List[Tuple[int, str]]:
    """What the ``ledger`` rule of ``tools/standards_check.py`` finds."""
    return read(text)[1]


def untriaged(text: str, version: str) -> List[Row]:
    """The rows not looked at for ``version``: their *Kept* does not name it."""
    return [row for row in read(text)[0] if version not in row.kept]


def summary(text: str) -> str:
    """One line: how many rows, by impact, and when the oldest was added."""
    rows = read(text)[0]
    if not rows:
        return "nothing open"
    counts = ", ".join("%d %s" % (sum(row.impact == impact for row in rows), impact)
                       for impact in IMPACTS if any(row.impact == impact for row in rows))
    return "%d open (%s), the oldest added %s" % (len(rows), counts, min(row.added for row in rows))


def version() -> str:
    found = _VERSION.search(VERSION_FILE.read_text(encoding="utf-8"))
    return found.group(1) if found else ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--release", action="store_true",
                        help="fail while a row's Kept does not name the version being released")
    parser.add_argument("--ledger", type=Path, default=LEDGER, help=argparse.SUPPRESS)
    parser.add_argument("--version", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    text = args.ledger.read_text(encoding="utf-8")
    print("ledger: " + summary(text))
    wrong = problems(text)
    for line, said in wrong:
        print("  docs/ledger.md:%d: %s" % (line, said))
    if not args.release:
        return 1 if wrong else 0
    releasing = args.version or version()
    left = untriaged(text, releasing)
    if left:
        print("not triaged for %s (fix it, make it an issue, drop it, or add %s to its Kept):"
              % (releasing, releasing))
        print("\n".join("  %s, line %d: %s" % (row.id, row.line, row.what) for row in left))
    return 1 if wrong or left else 0


if __name__ == "__main__":
    sys.exit(main())
