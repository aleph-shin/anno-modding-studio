# PR checklist

What to go through before opening a PR, and what a reviewer looks at. Nothing here is
enforced beyond what CI, the hook and the suite already check. It is a reference.

Everything here can be done by whoever wrote the change, without waiting on anyone.
What needs a person to look, such as the renders and the screenshots in the README and the guide, is
done once per release instead, on the release branch: see
[release-checklist.md](release-checklist.md).

The PR description starts from
[`.github/pull_request_template.md`](../../.github/pull_request_template.md), which
carries the short form. The rules behind each line are in
[code-standards.md](code-standards.md), [design-standards.md](design-standards.md)
and [translation.md](translation.md).

## For the contributor

### Every PR

- [ ] The title says what is true after the PR. Where it carries several changes, the
      title names the main one or what they share.
- [ ] The description lists every change the PR carries. For each it says what was
      wrong or missing, what changed, and what was measured or tried.
- [ ] The description says what AI assistance the change had, if any: which model,
      what it did, and which of the levels in
      [AI_DISCLOSURE.md](../../AI_DISCLOSURE.md) it was.
- [ ] `python tools/check.py` passes: the linters' counts, the design and standards
      rules, and the suite. If the suite ran without the games or the loader
      installed, the description says so.
- [ ] The **check** runs on the PR pass. They run the same `tools/check.py`, without
      the games or the loader, and the run's summary lists the tests that skipped
      for it ([development.md](../development.md#ci)). A green CI run does not
      stand in for the line above.
- [ ] A count that went down is ratcheted (`python tools/check.py --ratchet`), and the
      lower number is in the PR.
- [ ] No count was raised, and no waiver was added to get past a rule. A new waiver
      has its reason on the line.
- [ ] A bug fix has a test that fails without it. New behaviour has a test.
- [ ] The documents that own the changed facts are updated: the user guide for what a
      person sees, the design notes for why, the architecture map for modules and
      owners (code-standards.md, section 9).
- [ ] Nothing from a game install and no key is in the diff.

### If it adds or moves code

- [ ] `core/` still has no Qt, and the new code is in the module that owns the
      question (architecture.md, section 2).
- [ ] A new module or tool is in architecture.md, section 4.
- [ ] A new shared helper is in a leaf, and no leaf imports more than it did.
- [ ] Names use the words the screen uses.

### If it changes the interface

- [ ] The checklist in [design-standards.md](design-standards.md), section 15, is
      done. It covers text, look and keyboard.
- [ ] `python tools/untranslated_widgets.py` is clean.
- [ ] A new dialog has a factory in `tests/design_dialogs.py`, so the window walk
      and the renders reach it.

### If it changes a string

- [ ] `python tools/translations.py update` has run and the catalog is committed.
- [ ] The German is redone for every new or changed sentence, under
      [translation.md](translation.md). The hook fails until it is.
- [ ] A new term is in the glossary (design-standards.md, section 14) and in the
      German terms table.

### If it changes what the Studio keeps

- [ ] The stamp of what changed is bumped, or the description says why no released
      build would misread it ([code-standards.md](code-standards.md), section 11).
- [ ] Text the Studio writes into mods to recognise later is added to, not
      reworded, and a new marker is pinned in `tests/test_contracts.py`.
- [ ] A new JSON record is read and written through `core/records.py`.

### If it changes how mods are read, written or loaded

- [ ] `READER_VERSION` in `core/reader.py` is bumped where a mod now reads
      differently.
- [ ] The description says which games it was tried against.
- [ ] Where the change could alter what an export writes, the census was run and the
      description gives the result beside the last baseline.
- [ ] Where the answer rests on what the game's loader does, the description says how
      that was established: the loader's reference, its source, `xmltest`, or the game.
- [ ] Where it changes something a person waits for, `tools/bench.py` was run.

## For the reviewer

CI has already checked what a program can, short of the tests that need the games or
the loader. Spend the review on what it cannot.

- **Is it in the right place?** One question, one owner. A second answer to a question
  that already has an owner is the most common way the code drifts.
- **Does `ui/` decide anything?** A dialog that works out a folder or reads a file is
  doing `core/`'s work.
- **Is the test a test of the change?** It should fail without the change, and it
  should fail for the reason the PR gives.
- **Does the text read as one piece of writing?** Labels name the action, the voice is
  the Studio's, and every term is the glossary's. Read the German as well where you
  can.
- **Is each waiver's reason true?** A reason that restates the rule ("broad except on
  purpose") is not one.
- **Was a count ratcheted, or raised?** CI refuses a raised count. A new ruff
  rule switched off in `pyproject.toml` is the same thing by another route.
- **Do the documents still describe the code?** Look for a number, a module name or a
  step in the user guide or the README that the PR made untrue.
- **Was `--no-verify` used?** If the description says so, check that the reason is
  gone by the time the PR merges.

A finding the review does not fix in the PR is written down where it will be found
again, as an issue.

### A PR from someone else

A contributor may not have both games and the loader, and the Studio's look is not
theirs to approve. On a PR that neither the maintainer nor one of their agents wrote:

- [ ] The suite was run here, with both games and the loader installed.
- [ ] Where it changes a screen, the screens it touches were looked at in the renders
      from `python tools/render_themes.py <folder>`, the German ones as well.
- [ ] Where it changes how mods are read, written or loaded, the census was run here.
