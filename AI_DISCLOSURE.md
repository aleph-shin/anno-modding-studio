---
disclosure-default: ai-generated
models-used:
  - claude-opus-5-5
  - claude-opus-5
  - claude-fable-5-1
  - claude-sonnet-5-5
  - claude-sonnet-5
providers:
  - Anthropic
scope: |
  Almost all of the application code, tests, tools and documentation were
  written by Claude working as a coding agent, directed by the project's
  developer. The developer chose what to build, set the design and the rules
  the code follows, tested the Studio by hand and in the game, and decided
  what was merged. The code itself has had little line-by-line human review.
  The German translation is AI-generated and has not been reviewed by a
  native speaker.
last-updated: 2026-10-26
---

# AI disclosure

This repository follows the
[ai-disclosure convention](https://github.com/ggfevans/ai-disclosure). The block
above is the machine-readable part. No file carries a header of its own, so the
default applies to all of it.

## In plain words

Anno Modding Studio was built by one developer working with Claude, Anthropic's AI
model. Claude wrote almost all of the code, the tests and the documentation, and did
much of the research behind the design. The developer decided what the Studio should
do and how it should look and behave, tried it by hand and in the game, and decided
what went in.

Most of the code has not been read line by line by a person. What holds it instead:

- An automated suite of over 6,000 tests, linters and project rules, run on every
  change ([docs/development.md](docs/development.md)).
- Reviews of the code by separate AI passes, each finding recorded and settled.
- A census that reads hundreds of mods published on mod.io and checks the Studio's
  reading of each against the game's own mod loader.
- Hands-on testing by the developer, in the Studio and in the game.

Building software this way is new. If you would rather not run software made like
this, that is a fair choice, and this page is here so you can make it. The Studio
comes with no warranty ([LICENSE](LICENSE)), and it backs up what it changes in your
mods folder before it changes it.

## Contributing

A pull request says what AI assistance it had, if any, in its description. A new file
written by hand, or largely rewritten by hand, can say so with a header of its own:

```python
# SPDX-AI-Disclosure: none
```
