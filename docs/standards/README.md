# Standards

The rules a change to the Studio follows, and how each is held.

| Document | What it covers |
|---|---|
| [code-standards.md](code-standards.md) | The code: layers, Python, linters, errors, files, tests, tools, documents, commits, and what the Studio keeps across versions. |
| [design-standards.md](design-standards.md) | Everything a person reads, sees or operates: text, voice, help, colour, scaling, accessibility, the glossary. |
| [translation.md](translation.md) | Marking text for translation, what stays English, and the decisions for the German. |
| [pr-checklist.md](pr-checklist.md) | What a contributor checks before opening a PR, and what a reviewer looks at. |
| [release-checklist.md](release-checklist.md) | What is checked once per release, on the release branch: the renders, a run by hand, the census and the bench. |
| [repository-settings.md](repository-settings.md) | The GitHub settings the public repository is to have: branch protection, secret scanning, Dependabot, email privacy. |

The PR description starts from
[`.github/pull_request_template.md`](../../.github/pull_request_template.md), which is
the short form of the checklist.

## How a rule is held

Each rule in these documents is held in one of three ways, and the documents say which.

- **The hook.** `python tools/check.py --quick` runs on every push and fails on a
  finding. It takes a few seconds. [development.md](../development.md) says how to
  switch it on.
- **The suite.** `python tools/check.py` runs the hook's checks and then the tests.
  Run it before a PR is opened.

CI runs both on every PR, and it is what decides: the hook and the suite at home are
the same checks, sooner. The hook is switched on per clone, so a rule "held by the
hook" is held by CI where the hook is off.
- **Review.** A person checks it, with the PR checklist, or once per release with the
  release checklist. A rule stays here when no check can decide it: whether a label
  names its action, whether a module is the right owner for a question.

A rule that is held by review and keeps being missed is a candidate for a check. A new
check goes into `tools/standards_check.py` or `tools/design_check.py`, with a test
that breaks it and a test that keeps it.

## Related documents

These describe the Studio. They are not rules, but the rules refer to them.

- [architecture.md](../architecture.md): what each module is for, and which module
  answers each question.
- [development.md](../development.md): running the tests and the checks.
- [design-notes.md](../design-notes.md): why the Studio works the way it does.
