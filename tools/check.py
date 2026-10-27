"""Check the repository before a change goes in: ruff, mypy on core, vulture, the suite.

    python tools/check.py                  everything
    python tools/check.py --quick          everything but the suite, in seconds
    python tools/check.py --ratchet        lower the recorded counts to today's
    python tools/check.py --ci             everything, as GitHub Actions runs it

None of the three is at zero, and getting there is not a precondition for
anything. What this guards is the direction. ``check_baseline.json`` records
how many findings there are -- ruff's by rule, mypy's by file, and by file
the names vulture finds nothing using -- and a check fails when any one of
those counts grows. Counting by rule and by file, not in
total, is the point: fixing three findings must not pay for introducing a
new one somewhere else. When a change brings a count down, ``--ratchet``
writes the lower number back so it cannot creep up again.

This exists because nothing checked either: between Pass 1 and the start of
the consolidation, ruff went from 59 findings to 132 and mypy on core from 32
to 169 without anyone deciding that.

It also checks that ``docs/architecture.md`` names every module under
``core/`` and ``ui/``. The README's hand-kept module tree fell twenty core
modules behind without anyone noticing; the map it was replaced with should
not. And it holds the largest import cycle in ``core/`` to its recorded size
the same way as the counts: fourteen modules were in one until P8-25, four
after, and nothing has to decide that it may grow again for it to.

The design rules of ``tools/design_check.py`` were held the same way, by
rule, until each reached zero. They have no count in the baseline now: one
finding fails, and it is printed. The window walk's one remaining count,
under ``"walk"``, is ``tests/test_design.py``'s to hold and to lower. The map
check also covers the design stage's own files: the tools, the test and the
standards document have to be named in ``docs/architecture.md``.

``tools/standards_check.py`` holds what the other documents rule out and no
linter looks for -- Qt in ``core/``, a leaf that stopped being one, a private
name used across modules, a catalog out of step with the code -- the same
way: one finding fails. The map check covers ``tools/`` as well.

``tools/duplicate_check.py`` counts the lines that are in a run written
somewhere else as well, by file, and that count is held like ruff's: a file's
may not grow. It is under ``"duplicates"`` in the baseline.

The recorded counts are themselves checked against the main branch's. A count
may be lower than main's and may not be higher, so raising a number in
``check_baseline.json`` does not get a new finding past the hook.

The suite is run in a shuffled order, a new one each time, and the line that
says how it went says by what number: ``ANNO_STUDIO_TEST_ORDER=<number>`` runs
it that way again, and ``ANNO_STUDIO_TEST_ORDER=written`` in the order the
files are written. A test that passes only after another has run fails here
sooner or later, which is the point: see ``tests/conftest.py``.

It is shared among several processes (pytest-xdist), as many as the machine
has processors up to sixteen, or as ``ANNO_STUDIO_TEST_WORKERS`` says; ``1``
runs it in one, as it was run until it took eight minutes. Each process is
handed whole classes in the shuffled order and writes down the tests it runs,
so where one fails the line says which process it was and how to run what
that process ran, in its order, in one: the number alone no longer says what
ran before a test.

The checks before the suite run side by side as well: ruff, mypy and vulture
in processes of their own while the rest are worked out here.

The suite runs with the environment it would get from a fresh shell.
``tests/_isolation.py`` gives it a settings file of its own whatever
``ANNO_STUDIO_SETTINGS`` says, so a value left set by a scratch script neither
leaks into it nor is written to.

``--ci`` is how ``.github/workflows/check.yml`` runs it. CI has neither game
nor the loader, and a test that needs one fails without it (decision 5.5 of
the consolidation proposal); under ``--ci`` it skips instead, and the job's
summary lists every such skip by what it needed, beside each check's result
and what CI never runs. The summary goes where ``GITHUB_STEP_SUMMARY`` names,
or to the screen. A failing check is also said as an annotation, and the
recorded counts not compared with the main branch's is a failure there, where
on a copy with no repository it is not.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import design_check
import duplicate_check
import standards_check

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("check_baseline.json")
ARCHITECTURE = ROOT / "docs" / "architecture.md"
# What the design stage added, each of which the map has to name in backticks.
# What tests/conftest.py shuffles the suite by.
TEST_ORDER = "ANNO_STUDIO_TEST_ORDER"
# How many processes share the suite, and where each writes down what it ran
# (tests/conftest.py). Past sixteen a run was no faster on a 32-thread machine:
# every process is a Qt application of its own, and a class is not split.
TEST_WORKERS = "ANNO_STUDIO_TEST_WORKERS"
TEST_RECORD = "ANNO_STUDIO_TEST_RECORD"
MOST_WORKERS = 16
DESIGN_FILES = ("design_check", "render_themes", "test_design", "design-standards.md")
# Set for the suite under --ci: a test needing the games or the loader then
# skips, and its reason ends with NOT_HERE. Both are tests/support.py's.
SKIP_MISSING = "ANNO_STUDIO_SKIP_MISSING"
NOT_HERE = "which this machine does not have"
# What CI never runs, whatever the suite does, said in every summary so that a
# green run is not read as more than it is. docs/development.md says who runs
# each instead.
NEVER_IN_CI = (
    "the census, which exports every mod of a mod.io snapshot and compares (tools/modio_roundtrip.py)",
    "the bench (tools/bench.py)",
    "the renders, which a person looks at (tools/render_themes.py)",
    "the Studio against the games themselves",
)


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", **kwargs,
    )


def ruff_counts() -> Counter:
    """ruff's findings over the repository, by rule."""
    done = _run([sys.executable, "-m", "ruff", "check", "--output-format", "json", "."])
    try:
        found = json.loads(done.stdout or "[]")
    except ValueError:
        sys.exit("ruff did not answer in JSON:\n" + done.stdout + done.stderr)
    return Counter(item.get("code") or "syntax" for item in found)


_MYPY_LINE = re.compile(r"^(?P<file>[^:]+\.py):\d+(?::\d+)?: error:")


def mypy_counts() -> Counter:
    """mypy's errors over the package (core only -- pyproject says why), by file."""
    done = _run([sys.executable, "-m", "mypy"])
    counts: Counter = Counter()
    for line in done.stdout.splitlines():
        match = _MYPY_LINE.match(line)
        if match:
            counts[match.group("file").replace("\\", "/")] += 1
    if done.returncode not in (0, 1) or (done.returncode == 1 and not counts):
        sys.exit("mypy failed to run:\n" + done.stdout + done.stderr)
    return counts


_VULTURE_LINE = re.compile(r"^(?P<file>.+?\.py):\d+: (?P<what>.+) \(\d+% confidence")
# Qt's names are camelCase and the Studio's own are not, so a camelCase method
# nothing calls is an override Qt calls: paintEvent, closeEvent, sizeHint.
_QT_OVERRIDE = re.compile(r"unused (?:method|function) '[a-z]+[A-Z]\w*'")


def vulture_findings() -> dict:
    """What vulture finds nothing using in the package and the tools, as its
    own lines, by file. The tests are read, so a name only they use counts as
    used, and nothing in them is counted: a test class is named by nobody."""
    done = _run([sys.executable, "-m", "vulture"])
    if done.returncode not in (0, 3):
        sys.exit("vulture failed to run (pip install vulture):\n" + done.stdout + done.stderr)
    found: dict = {}
    for line in done.stdout.splitlines():
        match = _VULTURE_LINE.match(line)
        if not match:
            continue
        name = match.group("file").replace("\\", "/")
        if name.startswith("tests/") or _QT_OVERRIDE.match(match.group("what")):
            continue
        found.setdefault(name, []).append(line.replace("\\", "/"))
    return found


def compare(name: str, now: Counter, recorded: dict) -> list:
    """Every bucket that grew, as lines to print."""
    worse = []
    for key in sorted(set(now) | set(recorded)):
        was, is_ = int(recorded.get(key, 0)), now.get(key, 0)
        if is_ > was:
            worse.append("  %s %s: %d -> %d" % (name, key, was, is_))
    return worse


def unmapped_modules() -> list:
    """Modules under core/ and ui/ that docs/architecture.md does not name."""
    text = ARCHITECTURE.read_text(encoding="utf-8") if ARCHITECTURE.is_file() else ""
    named = set(re.findall(r"`(\w+)`", text))
    missing = []
    for layer in ("core", "ui"):
        for path in sorted((ROOT / "anno_modding_studio" / layer).glob("*.py")):
            if path.stem != "__init__" and path.stem not in named:
                missing.append("%s/%s" % (layer, path.name))
    return missing


def unmapped_tools() -> list:
    """Tools that docs/architecture.md does not name. The probes are named
    there together, as ``*_probe.py``."""
    text = ARCHITECTURE.read_text(encoding="utf-8") if ARCHITECTURE.is_file() else ""
    named = set(re.findall(r"`(\w+)(?:\.py)?`", text))
    probes = "`*_probe.py`" in text
    return [
        "tools/%s" % path.name for path in sorted((ROOT / "tools").glob("*.py"))
        if path.stem not in named and not (probes and path.stem.endswith("_probe"))
    ]


def unmapped_design_files() -> list:
    """The design tools, test and standards document that docs/architecture.md
    does not name. A name counts inside a longer backticked path, and
    ``test_design_check`` does not stand in for ``test_design``."""
    text = ARCHITECTURE.read_text(encoding="utf-8") if ARCHITECTURE.is_file() else ""
    return [
        name for name in DESIGN_FILES
        if not re.search(r"`[^`\n]*(?<![\w-])%s(?![\w-])[^`\n]*`" % re.escape(name), text)
    ]


def main_base() -> str | None:
    """The commit where this branch left the main branch; None where git
    cannot say, as in a copy of the files with no repository."""
    for main in ("origin/main", "main"):
        base = _run(["git", "merge-base", "HEAD", main])
        if base.returncode == 0:
            return base.stdout.strip()
    return None


def main_baseline(base: str | None) -> dict | None:
    """The counts recorded at ``base``; None where there are none to read."""
    if base is None:
        return None
    shown = _run(["git", "show", "%s:tools/%s" % (base, BASELINE.name)])
    try:
        return json.loads(shown.stdout) if shown.returncode == 0 else None
    except ValueError:
        return None


def renamed_since(base: str | None) -> dict:
    """Each file moved since ``base``, old path to new, as git tells a move.

    Counts kept by file follow the file: a module renamed with twelve
    duplicated lines in it has the twelve it had, not twelve new ones.
    """
    if base is None:
        return {}
    moved = _run(["git", "diff", "-M", "--name-status", base])
    renames = {}
    for line in moved.stdout.splitlines():
        status, *paths = line.split("\t")
        if status.startswith("R") and len(paths) == 2:
            renames[paths[0]] = paths[1]
    return renames


def raised(recorded: dict, before: dict, renames: dict | None = None) -> list:
    """Every recorded count that is higher than ``before`` has it, as lines to
    print. ``renames`` maps a moved file's old path to its new one."""
    worse = []
    for kind in ("ruff", "mypy", "vulture", "walk", "duplicates"):
        if kind not in before:
            # A count the main branch does not keep yet is being introduced.
            continue
        was = {(renames or {}).get(key, key): count for key, count in before[kind].items()}
        now = recorded.get(kind, {})
        for key in sorted(now):
            if int(now[key]) > int(was.get(key, 0)):
                worse.append("  %s %s: %d -> %d" % (kind, key, was.get(key, 0), now[key]))
    if recorded.get("cycle", 0) > before.get("cycle", recorded.get("cycle", 0)):
        worse.append("  core import cycle: %d -> %d" % (before["cycle"], recorded["cycle"]))
    return worse


def _written(recorded: dict) -> None:
    BASELINE.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8", newline="\n")


def core_cycle() -> list:
    """The largest set of core modules that import one another, round and
    round -- imports made inside functions included, as they are still
    edges a change can close -- sorted; [] where there is no cycle."""
    core = ROOT / "anno_modding_studio" / "core"
    names = {path.stem for path in core.glob("*.py") if path.stem != "__init__"}
    graph = {}
    for name in names:
        tree = ast.parse((core / (name + ".py")).read_text(encoding="utf-8"))
        edges = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module is None:
                    edges.update(alias.name for alias in node.names if alias.name in names)
                elif node.module.split(".")[0] in names:
                    edges.add(node.module.split(".")[0])
        graph[name] = edges - {name}
    # Tarjan's strongly connected components, without recursion.
    index: dict = {}
    low: dict = {}
    on_stack: set = set()
    stack: list = []
    largest: list = []
    counter = 0
    for root in sorted(names):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph[child]))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 and len(component) > len(largest):
                    largest = sorted(component)
    return largest


def suite_workers(order: str) -> int:
    """How many processes the suite is shared among: one where ``order``
    is one process's record, which is one process's to run again."""
    if os.path.isfile(order):
        return 1
    named = os.environ.get(TEST_WORKERS, "").strip()
    if named.isdigit() and int(named) > 0:
        return int(named)
    return min(os.cpu_count() or 1, MOST_WORKERS)


def replays(record) -> list:
    """For each process of a parallel run that had a test fail, a line saying
    which failed and how to run what that process ran, in its order."""
    lines = []
    for failed in sorted(Path(record).glob("*.failed")):
        names = failed.read_text(encoding="utf-8").split()
        lines.append("  %s failed %s; %s=%s python -m pytest tests runs what it ran, in its order"
                     % (failed.stem, ", ".join(names), TEST_ORDER, failed.with_suffix(".txt")))
    return lines


def run_suite(ci: bool = False):
    """Run the suite: whether it passed, the line that says how, and under
    ``ci`` the JUnit report pytest wrote, which :func:`suite_report` reads."""
    env = dict(os.environ)
    # An order of its own each time, unless one was asked for.
    order = env.get(TEST_ORDER, "").strip() or str(random.randrange(10_000, 100_000))
    env[TEST_ORDER] = order
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
    workers, record = suite_workers(order), None
    if workers > 1 and importlib.util.find_spec("xdist") is None:
        print("suite: pytest-xdist is not installed (pip install -e .[dev]), so it runs in one process")
        workers = 1
    if workers > 1:
        record = Path(tempfile.mkdtemp(prefix="ams-order-"))
        env[TEST_RECORD] = str(record)
        # A class to a process at a time, so what it sets up once is set up once.
        command += ["-n", str(workers), "--dist", "loadscope"]
    report = None
    if ci:
        env[SKIP_MISSING] = "1"
        report = Path(tempfile.mkdtemp(prefix="ams-check-")) / "suite.xml"
        # And the slowest tests, for a run slower than it should be.
        command += ["--junitxml", str(report), "--durations", "15"]
    started = time.monotonic()
    done = subprocess.run(command, cwd=ROOT, env=env)
    if order == "written":
        how = "the files are written in"
    elif os.path.isfile(order):
        how = "one process ran, from %s" % order
    else:
        how = "shuffled by %s (%s=%s runs it that way again)" % (order, TEST_ORDER, order)
    said = "%s in %.0f s%s, in the order %s" % (
        "passed" if done.returncode == 0 else "FAILED", time.monotonic() - started,
        " on %d processes" % workers if workers > 1 else "", how,
    )
    print("suite: " + said)
    if record is not None:
        if done.returncode == 0:
            shutil.rmtree(record, ignore_errors=True)
        else:
            print("\n".join(replays(record)))
    return done.returncode == 0, said, report


def suite_report(report) -> dict:
    """What a JUnit report of the suite says: how many tests ran, failed and
    were skipped, and the skips by reason, each with its tests. ``missing``
    has the ones skipped for want of the games or the loader, by what they
    needed; ``other`` every other skip. Empty where there is no report."""
    from xml.etree import ElementTree

    found: dict = {"tests": 0, "failed": 0, "skipped": 0, "missing": {}, "other": {}}
    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, TypeError, ElementTree.ParseError):
        return found
    for case in root.iter("testcase"):
        found["tests"] += 1
        if case.find("failure") is not None or case.find("error") is not None:
            found["failed"] += 1
        skipped = case.find("skipped")
        if skipped is None:
            continue
        found["skipped"] += 1
        reason = (skipped.get("message") or "").strip()
        # pytest puts where the skip was raised in front of what it said.
        reason = re.sub(r"^\S+:\d+: ", "", reason)
        name = "%s.%s" % (case.get("classname", ""), case.get("name", ""))
        if reason.startswith("needs ") and reason.endswith(NOT_HERE):
            needed = reason[len("needs "):-len(NOT_HERE)].rstrip(", ")
            found["missing"].setdefault(needed, []).append(name)
        else:
            found["other"].setdefault(reason or "no reason given", []).append(name)
    return found


def summary(checks, suite=None) -> str:
    """The job summary, in Markdown: each check and its result, then what the
    suite did not run here and why, then what CI never runs. ``checks`` is
    ``(name, what it found, passed)``; ``suite`` is :func:`suite_report`'s,
    or None where the suite was not run."""
    lines = [
        "## tools/check.py on Python %s" % sys.version.split()[0],
        "",
        "| Check | What it found | Result |",
        "|---|---|---|",
    ]
    lines += ["| %s | %s | %s |" % (name, found.replace("|", "\\|"), "passed" if ok else "**FAILED**")
              for name, found, ok in checks]
    if suite is not None:
        lines += ["", "### Not run here, for want of the games or the loader", ""]
        if suite["missing"]:
            lines += ["%d tests skipped. A run with both games and the loader installed "
                      "runs them: the contributor's, or the release checklist's." % sum(
                          len(names) for names in suite["missing"].values()), ""]
            lines += ["| Needs | Tests |", "|---|---|"]
            lines += ["| %s | %d |" % (needed, len(names)) for needed, names in sorted(suite["missing"].items())]
            for needed, names in sorted(suite["missing"].items()):
                lines += ["", "<details><summary>Needing %s</summary>" % needed, ""]
                lines += ["- `%s`" % name for name in sorted(names)]
                lines += ["", "</details>"]
        else:
            lines.append("None: every test that needs them ran.")
        if suite["other"]:
            lines += ["", "### Skipped for another reason", ""]
            lines += ["- %s: %d" % (reason, len(names)) for reason, names in sorted(suite["other"].items())]
    lines += ["", "### Never run in CI", ""]
    lines += ["- " + what for what in NEVER_IN_CI]
    return "\n".join(lines) + "\n"


def write_summary(text: str) -> None:
    """Add ``text`` to the job's summary where Actions names one, else print it."""
    target = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not target:
        print(text)
        return
    with open(target, "a", encoding="utf-8") as summary_file:
        summary_file.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true", help="everything but the suite")
    parser.add_argument("--ratchet", action="store_true",
                        help="write today's counts where they are lower than recorded")
    parser.add_argument("--ci", action="store_true",
                        help="as GitHub Actions runs it: tests needing the games or the loader "
                             "skip, and a summary says which")
    args = parser.parse_args()

    recorded = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else {}
    # ruff, mypy and vulture are processes of their own: they run while the
    # rest is worked out here.
    with ThreadPoolExecutor(max_workers=3) as pool:
        ruff_found, mypy_found = pool.submit(ruff_counts), pool.submit(mypy_counts)
        vulture_found = pool.submit(vulture_findings)
        cycle = core_cycle()
        duplicates = duplicate_check.counts()
        design = design_check.failures()
        standards = standards_check.failures()
        ruff, mypy, unused = ruff_found.result(), mypy_found.result(), vulture_found.result()
    vulture = Counter({name: len(lines) for name, lines in unused.items()})
    print("ruff: %d findings (recorded %d)" % (sum(ruff.values()), sum(recorded.get("ruff", {}).values())))
    print("mypy: %d errors (recorded %d)" % (sum(mypy.values()), sum(recorded.get("mypy", {}).values())))
    print("vulture: %d unused names (recorded %d)" % (sum(vulture.values()), sum(recorded.get("vulture", {}).values())))
    print("core import cycle: %d modules (recorded %d)" % (len(cycle), recorded.get("cycle", len(cycle))))
    print("written twice: %d lines (recorded %d)"
          % (sum(duplicates.values()), sum(recorded.get("duplicates", dict(duplicates)).values())))
    print("design: %d findings (none allowed)" % len(design))
    print("standards: %d findings (none allowed)" % len(standards))

    if args.ratchet:
        lowered = {
            "ruff": {k: min(v, ruff.get(k, 0)) for k, v in recorded.get("ruff", dict(ruff)).items()},
            "mypy": {k: min(v, mypy.get(k, 0)) for k, v in recorded.get("mypy", dict(mypy)).items()},
            "vulture": {k: min(v, vulture.get(k, 0)) for k, v in recorded.get("vulture", dict(vulture)).items()},
            # Recorded as it is found the first time, and only lowered after.
            "duplicates": {k: min(v, duplicates.get(k, 0))
                           for k, v in recorded.get("duplicates", dict(duplicates)).items()},
        }
        for kind in lowered:
            lowered[kind] = {k: v for k, v in sorted(lowered[kind].items()) if v}
        lowered["cycle"] = min(len(cycle), recorded.get("cycle", len(cycle)))
        # The window walk's count is tests/test_design.py's to lower.
        if "walk" in recorded:
            lowered["walk"] = recorded["walk"]
        _written(lowered)
        print("recorded counts lowered where they could be")
        return 0

    ruff_worse = compare("ruff", ruff, recorded.get("ruff", {}))
    mypy_worse = compare("mypy", mypy, recorded.get("mypy", {}))
    vulture_worse = []
    for name in sorted(vulture):
        if vulture[name] > int(recorded.get("vulture", {}).get(name, 0)):
            vulture_worse.append("  vulture %s: %d -> %d, of:" % (name, recorded.get("vulture", {}).get(name, 0), vulture[name]))
            vulture_worse += ["    " + line for line in unused[name]]
    duplicates_worse = []
    if "duplicates" in recorded:
        duplicates_worse = compare("written twice", duplicates, recorded["duplicates"])
        if duplicates_worse:
            duplicates_worse.append("    python tools/duplicate_check.py --show <file> says which lines, and where the other copy is")
    cycle_worse = []
    if "cycle" in recorded and len(cycle) > recorded["cycle"]:
        cycle_worse.append("  core import cycle: %d -> %d modules: %s"
                           % (recorded["cycle"], len(cycle), ", ".join(cycle)))
    worse = ruff_worse + mypy_worse + vulture_worse + duplicates_worse + cycle_worse
    if worse:
        print("worse than recorded:")
        print("\n".join(worse))
    if design:
        print("design rules allow none (docs/standards/design-standards.md, section 13):")
        print("\n".join(design))
    if standards:
        print("standards allow none (python tools/standards_check.py says which rule is which):")
        print("\n".join(standards))
    unmapped = unmapped_modules() + unmapped_tools() + unmapped_design_files()
    if unmapped:
        print("not in docs/architecture.md: " + ", ".join(unmapped))
    base = main_base()
    before = main_baseline(base)
    lifted = raised(recorded, before, renamed_since(base)) if before is not None else []
    if lifted:
        print("recorded counts raised above the main branch's (%s):" % BASELINE.name)
        print("\n".join(lifted))
    if before is None and args.ci:
        # In CI the comparison is the point; a clone too shallow to make it
        # would pass a raised count without a word.
        print("recorded counts not compared with the main branch's: git found no main branch "
              "(in Actions, check out with fetch-depth: 0)")
    passed = not worse and not unmapped and not design and not standards and not lifted
    passed = passed and not (before is None and args.ci)
    suite = None
    if not args.quick:
        suite_passed, suite_said, report = run_suite(ci=args.ci)
        passed = suite_passed and passed
        if args.ci:
            suite = suite_report(report)
    if args.ci:
        checks = [
            ("ruff", "%d findings (recorded %d)" % (sum(ruff.values()), sum(recorded.get("ruff", {}).values())),
             not ruff_worse),
            ("mypy on core", "%d errors (recorded %d)" % (sum(mypy.values()), sum(recorded.get("mypy", {}).values())),
             not mypy_worse),
            ("vulture", "%d unused names (recorded %d)"
             % (sum(vulture.values()), sum(recorded.get("vulture", {}).values())), not vulture_worse),
            ("core import cycle", "%d modules (recorded %d)" % (len(cycle), recorded.get("cycle", len(cycle))),
             not cycle_worse),
            ("written twice", "%d lines (recorded %d)"
             % (sum(duplicates.values()), sum(recorded.get("duplicates", dict(duplicates)).values())),
             not duplicates_worse),
            ("design rules", "%d findings (none allowed)" % len(design), not design),
            ("standards rules", "%d findings (none allowed)" % len(standards), not standards),
            ("architecture map", "%d not named" % len(unmapped), not unmapped),
            ("counts against main", "not compared: no main branch" if before is None
             else "%d raised" % len(lifted), before is not None and not lifted),
        ]
        if not args.quick:
            checks.append(("the suite", "%d tests, %d failed, %d skipped; %s" % (
                suite["tests"], suite["failed"], suite["skipped"], suite_said), suite_passed))
        for name, found, ok in checks:
            if not ok:
                # An annotation on the run's page, so the failure is seen without the log.
                print("::error title=check.py: %s::%s" % (name, found))
        write_summary(summary(checks, suite))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
