"""Refuse a push that carries anything on your own list of private terms.

    python tools/private_scan.py < refs          as tools/hooks/pre-push runs it
    python tools/private_scan.py --range A..B    the commits in a range
    python tools/private_scan.py --file PATH     one file, or - for what comes in

The repository is public, and a push is the moment that matters: whatever it
carries can be copied before anything is taken back. ``standards_check``'s
``user-path`` rule finds paths into a user's folder, which anybody's checks may
look for. What is private to you -- your user name, an address, an account
number, a folder of your own -- can't be listed in a public file, since the
list would publish it. So the list is yours, kept outside the repository: a
text file with one term a line, matched without regard to case, ``re:`` before
a regular expression, and ``#`` before a comment. Point at it once with

    git config --global studio.privateTerms <path>

or with ``STUDIO_PRIVATE_TERMS``. With neither, the scan says so and passes.

For each commit a push would add, it reads the author and committer, the
message, the name of every file changed, and every line added. Any match is
printed with the commit and where it is, and the push is refused. Text can't
be read off a picture, so every image a commit adds or changes is listed for
you to look at; that alone refuses nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg"}
NOTHING = "0" * 40


def list_path() -> Path | None:
    """Where the list of terms is, or None when there is none."""
    named = os.environ.get("STUDIO_PRIVATE_TERMS", "")
    if not named:
        named = _git("config", "--get", "studio.privateTerms", check=False).strip()
    return Path(os.path.expanduser(named)) if named else None


def terms(text: str) -> list:
    """The patterns a list of terms says, one a line."""
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            found.append(re.compile(line[3:], re.I))
        else:
            found.append(re.compile(re.escape(line), re.I))
    return found


def matches(patterns, text: str) -> list:
    """The patterns found in a text, by what each found."""
    return [match.group() for pattern in patterns for match in pattern.finditer(text)]


def _git(*args, check: bool = True) -> str:
    done = subprocess.run(["git", *args], capture_output=True, check=check)
    return done.stdout.decode("utf-8", errors="replace")


def pushed(lines) -> list:
    """The commits a push would add, from what git hands the pre-push hook:
    one line a ref, ``<local ref> <local sha> <remote ref> <remote sha>``."""
    commits = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == NOTHING:
            continue
        local, remote = parts[1], parts[3]
        if remote == NOTHING:
            listed = _git("rev-list", local, "--not", "--remotes")
        else:
            listed = _git("rev-list", "%s..%s" % (remote, local))
        commits += [sha for sha in listed.split() if sha not in commits]
    return commits


def scan_commit(sha: str, patterns) -> tuple:
    """What a commit carries that is on the list, and the images it adds or
    changes, as ``(hits, images)``; each hit is ``(where, what)``."""
    hits = []
    said = _git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
    hits += [("the author, committer or message", found) for found in matches(patterns, said)]
    diff = _git("show", "--format=", "--no-color", "--no-ext-diff", "-U0",
                "--diff-merges=first-parent", sha)
    path = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            hits += [("the file name " + path, found) for found in matches(patterns, path)]
        elif line.startswith("+"):
            hits += [(path, found) for found in matches(patterns, line[1:])]
    changed = _git("show", "--format=", "--name-only", "--diff-filter=AMR",
                   "--diff-merges=first-parent", sha)
    images = [name for name in changed.splitlines() if Path(name).suffix.lower() in IMAGES]
    return hits, images


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--range", help="the commits in A..B")
    parser.add_argument("--file", help="one file, or - for what comes in")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    where = list_path()
    if where is None:
        print("private_scan: no list of private terms (git config studio.privateTerms), "
              "so nothing was looked for", file=sys.stderr)
        return 0
    try:
        patterns = terms(where.read_text(encoding="utf-8"))
    except OSError as error:
        print("private_scan: can't read the list of private terms, %s: %s" % (where, error),
              file=sys.stderr)
        return 1

    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(
            encoding="utf-8", errors="replace")
        found = matches(patterns, text)
        for what in found:
            print("private_scan: %s holds %r" % (args.file, what), file=sys.stderr)
        return 1 if found else 0

    commits = _git("rev-list", args.range).split() if args.range else pushed(sys.stdin)
    refused = False
    for sha in commits:
        hits, images = scan_commit(sha, patterns)
        for where_, what in hits:
            print("private_scan: %s, %s: %r is on your list" % (sha[:8], where_, what),
                  file=sys.stderr)
            refused = True
        for name in images:
            print("private_scan: %s adds or changes the image %s: look at it before it "
                  "is public" % (sha[:8], name), file=sys.stderr)
    if refused:
        print("private_scan: the push is refused. Rewrite those commits, or push with "
              "--no-verify if a match is not private after all.", file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
