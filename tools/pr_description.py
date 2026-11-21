"""Check a PR's description against the template's headings.

    python tools/pr_description.py <file>   a description written to a file
    python tools/pr_description.py          the one in PR_BODY, as CI runs it

``.github/workflows/pr-description.yml`` runs this on every PR and on every
edit of its description, so a PR fails while what the template asks for is
missing, and passes again once the description is put right. What each part
asks for, and why, is ``docs/standards/pr-checklist.md``.

What it holds:

- every heading of :data:`HEADINGS` is there, with something under it beside
  the template's comments;
- *AI assistance* names one of the levels of ``AI_DISCLOSURE.md``;
- every line of :data:`VERIFIED` is under *Verified* and says something;
- *Exceptions* says ``none``, or each exception names who allowed it.

A PR whose author is a bot (``PR_AUTHOR`` ends with ``[bot]``: Dependabot)
has the description its bot writes, and is not checked.

Standard library only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List

HEADINGS = ("What and why", "AI assistance", "Verified", "Exceptions")
LEVELS = ("none", "ai-assisted", "ai-generated", "autonomous")
VERIFIED = ("Suite", "In the Studio", "In the game", "Ledger")

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)


def sections(body: str) -> Dict[str, str]:
    """Each ``## `` heading's text, the template's comments taken out."""
    text = _COMMENT.sub("", body.replace("\r\n", "\n"))
    found = {}
    matches = list(_HEADING.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        found.setdefault(match.group(1).strip(), text[match.end():end].strip())
    return found


def problems(body: str) -> List[str]:
    """What the description is missing, in words; empty when nothing is."""
    if not (body or "").strip():
        return ["the description is empty: start from .github/pull_request_template.md"]
    found = sections(body)
    said = []
    for heading in HEADINGS:
        if heading not in found:
            said.append('no "## %s" heading' % heading)
        elif not found[heading]:
            said.append('nothing under "## %s"' % heading)
    levels = "|".join(re.escape(level) for level in LEVELS)
    if found.get("AI assistance") and not re.search(r"(?<![\w-])(%s)(?![\w-])" % levels,
                                                      found["AI assistance"], re.I):
        said.append('"AI assistance" names none of the levels: %s' % ", ".join(LEVELS))
    verified = found.get("Verified", "")
    for label in VERIFIED:
        line = re.search(r"^\s*[-*]\s*\*\*%s:\*\*(.*)$" % re.escape(label), verified, re.M)
        if line is None:
            said.append('no "**%s:**" line under "Verified"' % label)
        elif not line.group(1).strip():
            said.append('"**%s:**" under "Verified" says nothing' % label)
    # Exceptions are list items; text after them, such as a footer a tool
    # adds to every description, is not one.
    exceptions = found.get("Exceptions", "")
    lines = [line.strip() for line in exceptions.splitlines() if line.strip()]
    items = [line for line in lines if re.match(r"[-*]\s", line)]
    if lines and not re.fullmatch(r"none\.?", lines[0], re.I):
        if not items:
            said.append('"Exceptions" is neither "None." nor a list of exceptions')
        for line in items:
            if not re.search(r"allowed by", line, re.I):
                said.append('an exception names nobody who allowed it ("allowed by"): %s'
                            % line[:80])
    return said


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # What it quotes from a description may be anything, whatever the console takes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.environ.get("PR_AUTHOR", "").endswith("[bot]"):
        print("written by %s: not checked" % os.environ["PR_AUTHOR"])
        return 0
    body = Path(argv[0]).read_text(encoding="utf-8") if argv else os.environ.get("PR_BODY", "")
    said = problems(body)
    if not said:
        print("the description has what the template asks for")
        return 0
    print("the description is missing what docs/standards/pr-checklist.md asks for:")
    print("\n".join("  " + item for item in said))
    if os.environ.get("GITHUB_ACTIONS"):
        for item in said:
            print("::error title=PR description::%s" % item)
    return 1


if __name__ == "__main__":
    sys.exit(main())
