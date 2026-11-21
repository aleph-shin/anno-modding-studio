# Ledger

What was noticed and not fixed: a standard a change fell short of, a rough edge, a
smell, a reviewer's doubt. Each gets one short row here instead of a line in a PR that
nobody reads again. Work that someone is waiting on is an issue instead.

The rules a change may not break are the absolute rules in
[pr-checklist.md](standards/pr-checklist.md). Everything else the standards ask for
is a matter of judgement. A change that falls short of one and does not fix it says
so here.

## How it is kept

- **A row is added** by the PR that noticed it, which names it under *Verified ›
  Ledger*. Its ID is the next number after the highest used, ever: `L` and the
  number. *Added* is the date. *Where* is a file, a screen or a module. *What* is one
  sentence.
- **Impact** is who would notice:
  - `user`: a person using the Studio;
  - `maintainer`: someone changing the code;
  - `cosmetic`: nobody much.
- **A row is deleted when it is fixed**, by the PR that fixes it, which names it. Git
  keeps what it said.
- **Once per release, every row is triaged**
  ([release-checklist.md](standards/release-checklist.md)): it is fixed, made an
  issue (the row is deleted and the issue says where it came from), dropped, or kept.
  Keeping it adds the release's version to *Kept*.
  - `python tools/ledger.py --release` fails the release workflow while a row's
    *Kept* does not name the version being released.
  - A row may be kept at two releases. At the third it is fixed, made an issue, or
    dropped: `tools/standards_check.py` fails on a row kept three times.
- **`python tools/check.py` prints the count** on every run, and `python
  tools/ledger.py` prints it alone.

## Rows

| ID | Added | Impact | Where | What | Kept |
|---|---|---|---|---|---|
| L1 | 2026-11-21 | maintainer | `docs/standards/design-standards.md`, section 15 | The checklist does not say which of its lines `tools/design_check.py` holds, so a reader cannot tell what is left to look at by hand. | - |
