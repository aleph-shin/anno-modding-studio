# PR checklist

What a PR holds before it is opened, and what a reviewer looks at. It is short on
purpose. What it asks for is either absolute or a matter of judgement:

- **The absolute rules** below are kept by every PR. Only a maintainer can let a PR
  break one, and the PR says so under *Exceptions*, with who allowed it and their
  words.
- **Everything else** the standards ask for is judgement. Where a change falls short
  of a standard and does not fix it, that goes in the [ledger](../ledger.md), one row,
  and is triaged once per release. It does not go in a PR description to be forgotten.

The description starts from
[`.github/pull_request_template.md`](../../.github/pull_request_template.md).
`tools/pr_description.py` fails the PR while a heading is missing or a line under
*Verified* says nothing. It runs on every PR and again on every edit of the
description. What a person has to check by hand once per release is
[release-checklist.md](release-checklist.md).

## The description

- **What and why.** What was wrong or missing, what changed, and what was measured or
  tried, for each change the PR carries. Where the answer rests on what the game's
  loader does, say how that was established: the loader's reference, its source,
  `xmltest`, or the game.
- **AI assistance.** Which model or models and what they did, and the level from
  [AI_DISCLOSURE.md](../../AI_DISCLOSURE.md).
- **Verified**, one line each:
  - **Suite:** the result of `python tools/check.py`, and whether both games and the
    loader were installed.
  - **In the Studio:** see [below](#verified-in-the-studio).
  - **In the game:** see [below](#verified-in-the-game).
  - **Ledger:** the rows the PR adds or closes, or none.
- **Exceptions.** `None.`, or each absolute rule broken, with the maintainer who
  allowed it.

## The absolute rules

Most of these are held by a check. The rest are held by review.

| Rule | Held by |
|---|---|
| `python tools/check.py` passes, and the PR's **check** runs pass. | CI, required to merge |
| No count is raised and no rule is switched off, in `check_baseline.json` or `pyproject.toml`. A new waiver has its reason on the line. A count that went down is ratcheted (`--ratchet`). | `check.py`; the ratchet by review |
| `--no-verify` is not used. | review |
| `core/` has no Qt, and a leaf imports no more than it did ([architecture.md](../architecture.md), section 1). | `standards_check.py` |
| A new module or tool is named in [architecture.md](../architecture.md), section 4. | `check.py` |
| A bug fix has a test that fails without it. New behaviour has a test. | review |
| A changed string is in the catalog, and its German is redone under [translation.md](translation.md). | the hook |
| A new dialog has a factory in `tests/design_dialogs.py`. | review |
| The documents that own the changed facts are updated: the user guide for what a person sees, the design notes for why, the architecture map for modules ([code-standards.md](code-standards.md), section 9). | review |
| What the Studio keeps: the stamp of what changed is bumped, or the PR says why no released build would misread it; text written into mods to recognise later is added to, never reworded; a new JSON record goes through `core/records.py` ([code-standards.md](code-standards.md), section 11). | `tests/test_formats.py`, `tests/test_contracts.py`, review |
| `READER_VERSION` in `core/reader.py` is bumped where a mod now reads differently. | review |
| Where a change could alter what an export writes, the census was run and the PR gives the result beside the last baseline. Where it changes something a person waits for, `tools/bench.py` was run. | review |
| Nothing from a game install, no key, and no term from the private list is in the diff. | the hook, `private_scan.py` |
| The *In the Studio* line is honest: what it says was seen, was seen. | review |

## Verified in the Studio

Before a PR is opened, the change is tried in the running Studio, or the PR says in one
sentence why looking would show nothing different.

- **Write the check before the change.** Write down the steps and what should be seen
  before making the change, then do them in the built change and write what was seen.
  A check written afterwards bends towards whatever happened.
- **A skip names what a person could see.** "No screen shows anything different: the
  change is to the census tool" is a reason. "The tests cover it" is not one, for
  anything a person can see.
- **Where it runs.** The maintainer's agents run it by computer use on the test machine
  that `CLAUDE.md` names, and nowhere else. A contributor runs it on their own
  machine.
- **What it does not cover.** Nothing past Play's question: starting the game, mod.io
  and the Steam and Ubisoft accounts are left alone. What needs the game is the next
  section.
- **It is per change.** The hands-on runs, which try everything together for hours,
  still happen before a release.

## Verified in the game

Some changes can only be known to work in the game. The maintainer runs those, so each
one is made to take a few minutes.

- **When it is worth it.** When the answer rests on what the game does and the loader's
  reference, its source and `xmltest` don't settle it, or when an export writes
  something the game has not loaded before. Not when the census shows the export
  writes the same bytes, and not for a change that is very unlikely to break. Say
  which under *In the game*.
- **A test pack.** `python tools/ingame_pack.py new <folder>` starts one: the probe
  mods, `pack.json` with the questions and the answers expected, and a `README.txt`
  that says what to do and how long it takes. Prefer questions the loader's log
  answers (a marker ModOp that matches nothing, logged under the mod that ran it) over
  ones that need play, since reaching the main menu is then enough. Where play is
  needed, say which cheats get there. Packs go in the companion repository's
  `in-game/` folder.
- **It does not hold the merge.** The PR says *Pending:* and the pack's folder. The
  release does not go out while a pack is pending
  ([release-checklist.md](release-checklist.md)).

## For the reviewer

CI has already checked what a program can. Spend the review on what it cannot, and
put what is not fixed in the PR in the [ledger](../ledger.md):

- **Is it in the right place?** One question, one owner. A second answer to a question
  that already has an owner is the most common way the code drifts. Does `ui/` decide
  something that is `core/`'s?
- **Is the test a test of the change?** It should fail without the change, for the
  reason the PR gives.
- **Does the text read as one piece of writing?** Labels name the action, the voice is
  the Studio's, and every term is the glossary's
  ([design-standards.md](design-standards.md)).
- **Is each waiver's reason true?** A reason that restates the rule ("broad except on
  purpose") is not one.
- **Do the documents still describe the code?** Look for a number, a module name or a
  step in the guide or the README that the PR made untrue.

### A PR from someone else

A contributor may not have both games and the loader, and the Studio's look is not
theirs to approve. On a PR that neither the maintainer nor one of their agents wrote:

- The suite is run here, with both games and the loader installed.
- The *In the Studio* check is done again here.
- Where it changes a screen, the renders from `python tools/render_themes.py <folder>`
  are looked at, the German ones as well.
- Where it changes how mods are read, written or loaded, the census is run here.
