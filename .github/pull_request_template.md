<!-- The full checklist, with the reason for each line, is docs/standards/pr-checklist.md. -->

## What and why

<!-- What was wrong or missing, what changed, and what was measured or tried.
     Where the PR carries several changes, list each one. -->

## AI assistance

<!-- Which model or models, and what they did. One of: none, ai-assisted,
     ai-generated, autonomous (see AI_DISCLOSURE.md). -->

## Checked

- [ ] `python tools/check.py` passes (linters' counts, design and standards rules, the suite)
- [ ] The suite ran with both games and the loader installed, or this says what was missing:
- [ ] The **check** runs on this PR pass (they skip what needs the games or the loader)
- [ ] Tests cover the change, and a bug fix has a test that fails without it
- [ ] Documents that own the changed facts are updated (README, design notes, architecture map)
- [ ] No count raised, no waiver without a reason, `--no-verify` not used (or this says why)

## If it changes the interface or a string

<!-- Delete this section if it does neither. -->

- [ ] The checklist in docs/standards/design-standards.md, section 15, is done
- [ ] `python tools/translations.py update` has run and the German is redone
- [ ] A new dialog has a factory in `tests/design_dialogs.py`

## If it changes how mods are read, written or loaded

<!-- Delete this section if it does not. -->

- [ ] Games it was tried against:
- [ ] Census or bench result, where an export or a wait could change:
