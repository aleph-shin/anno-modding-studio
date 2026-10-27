"""Count the code that is written twice: runs of the same lines in two places.

    python tools/duplicate_check.py            the count, by file
    python tools/duplicate_check.py --show     each run, and where its copies are
    python tools/duplicate_check.py --show anno_modding_studio/ui/output_panel.py

A rule written twice is two rules the day one copy is changed. The sample
audit's first full run found seven places where the copies had already come
apart (audit run 8): the guard, plan and write of an export
written three times and differing in what they report, a list of what to
forget on a new loadout kept twice and not the same. Its reviewers called 33
of 90 units "repeated elsewhere".

No linter here looks for it, so this does, and plainly: a run is ``RUN`` or
more lines in a row that are the same, once comments, blank lines and
indentation are taken away, as lines somewhere else in the application or its
tools. Lines that are only punctuation are passed over, so that a closing
bracket neither makes a run nor breaks one. Names are not made alike: code
copied and then renamed is not found, and that is the cost of never calling
two different things the same.

``tools/check.py`` holds the count as it holds ruff's: by file, the number of
lines that are in a run, and a file's number may not grow. It starts high and
getting it to zero is not the aim. What it stops is the next copy.

The tests are left out. A test's setting-up is repeated on purpose, so that
each can be read by itself.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
# The application and its tools.
WHERE = ("anno_modding_studio", "tools")
# How many lines in a row make a run. Five and fewer are mostly a layout's
# margins set the same way twice.
RUN = 6
# A line with fewer letters and digits than this says too little to count.
WORTH = 4


def files() -> List[Path]:
    found: List[Path] = []
    for top in WHERE:
        found += sorted(path for path in (ROOT / top).rglob("*.py") if "__pycache__" not in path.parts)
    return found


def lines_of(text: str) -> List[Tuple[int, str]]:
    """``(line number, the line as compared)`` for each line of ``text`` that
    is code and says enough: no comment, no docstring, no indentation, and
    no import, since two modules that need the same names say so alike."""
    kept: Dict[int, List[str]] = defaultdict(list)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        imports = {
            number
            for node in ast.walk(ast.parse(text))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for number in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        }
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return []
    before = tokenize.INDENT
    for token in tokens:
        if token.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                          tokenize.DEDENT, tokenize.ENDMARKER):
            if token.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                before = token.type
            continue
        if token.type == tokenize.STRING and before in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) \
                and not kept[token.start[0]]:
            # A string that is a statement by itself: a docstring.
            before = tokenize.STRING
            continue
        before = token.type
        if token.type == tokenize.STRING and token.start[0] != token.end[0]:
            # One that runs over lines counts once, where it starts.
            kept[token.start[0]].append(" ".join(token.string.split()))
            continue
        kept[token.start[0]].append(token.string)
    out = []
    for number in sorted(kept):
        if number in imports:
            continue
        line = " ".join(kept[number])
        if sum(char.isalnum() for char in line) >= WORTH:
            out.append((number, line))
    return out


def runs(texts: Dict[str, str]) -> Dict[str, List[Tuple[int, int, List[Tuple[str, int]]]]]:
    """``{file: [(first line, last line, [(other file, its first line)])]}``
    for every run of ``RUN`` lines or more that is somewhere else as well."""
    lined = {name: lines_of(text) for name, text in texts.items()}
    seen: Dict[tuple, List[Tuple[str, int]]] = defaultdict(list)
    for name, lines in lined.items():
        for at in range(len(lines) - RUN + 1):
            seen[tuple(line for _number, line in lines[at: at + RUN])].append((name, at))
    twice = {key: places for key, places in seen.items() if len(places) > 1}
    # Each line of a file that is in a window found twice, and where the
    # window's other copies start, both counted in compared lines.
    marked: Dict[str, Dict[int, set]] = defaultdict(lambda: defaultdict(set))
    for places in twice.values():
        for name, at in places:
            for other, there in places:
                if (other, there) == (name, at):
                    continue
                if other == name and abs(there - at) < RUN:
                    continue  # a window over itself: one run of repeated lines
                for step in range(RUN):
                    marked[name][at + step].add((other, there))
    found: Dict[str, List[Tuple[int, int, List[Tuple[str, int]]]]] = {}
    for name, hit in marked.items():
        lines = lined[name]
        out = []
        start = None
        last = -1
        for at in sorted(hit):
            if start is None or at != last + 1:
                if start is not None:
                    out.append(_run(lined, name, hit, start, last))
                start = at
            last = at
        if start is not None:
            out.append(_run(lined, name, hit, start, last))
        if out:
            found[name] = out
    return found


def _run(lined, name: str, hit, start: int, last: int):
    # Where each copy starts: the first place in each other file, and in
    # this one each place that is not just the line after the one before.
    # Places are compared-line indices until the end: measured in source
    # lines, a copy with comments or blank lines inside was named again.
    places = sorted({place for at in range(start, last + 1) for place in hit[at]})
    others: List[Tuple[str, int]] = []
    for other, there in places:
        if others and others[-1][0] == other and there - others[-1][1] <= (last - start) + RUN:
            continue
        others.append((other, there))
    lines = lined[name]
    return lines[start][0], lines[last][0], [(other, lined[other][there][0]) for other, there in others]


def read() -> Dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in files()
    }


def counts(texts: Dict[str, str] | None = None) -> Counter:
    """By file, how many of its lines are in a run that is somewhere else."""
    texts = read() if texts is None else texts
    lined = {name: {number for number, _line in lines_of(text)} for name, text in texts.items()}
    found: Counter = Counter()
    for name, held in runs(texts).items():
        found[name] = sum(
            sum(1 for number in lined[name] if first <= number <= last) for first, last, _others in held
        )
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--show", nargs="?", const="", metavar="FILE",
                        help="each run and where its copies are, for every file or one")
    args = parser.parse_args(argv)
    texts = read()
    if args.show is not None:
        for name, held in sorted(runs(texts).items()):
            if args.show and name != args.show.replace("\\", "/"):
                continue
            for first, last, others in held:
                print("%s:%d-%d  also at %s" % (
                    name, first, last, ", ".join("%s:%d" % place for place in others[:4]) or "itself"))
        return 0
    found = counts(texts)
    for name, number in sorted(found.items(), key=lambda entry: (-entry[1], entry[0])):
        print("%5d  %s" % (number, name))
    print("%5d  lines in a run of %d or more that is written somewhere else, in %d files"
          % (sum(found.values()), RUN, len(found)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
