# Working on Anno Modding Studio

How a session that changes the Studio runs. The rules a change keeps are
[docs/standards/pr-checklist.md](docs/standards/pr-checklist.md), and the tests and
checks are [docs/development.md](docs/development.md). This file is the order to do
things in. Facts about one machine, such as which one is the test machine, are in
`CLAUDE.local.md` beside it, which git ignores.

## At the start of a session

1. **Know the machine.** Run `hostname`. Computer use happens only on the test machine
   that `CLAUDE.local.md` names. If this is not that machine, there is no
   `CLAUDE.local.md`, or the test machine cannot be reached, stop and ask the
   maintainer to fix that. Do not run a planned check on another machine, and do not
   skip it.
2. **Ask for permissions early.** In one go, before any long work, request
   computer-use access to everything the session's checks may touch: the Studio's
   window, Explorer, a text editor. The maintainer may not be watching hours in. A
   prompt that goes unanswered is a no: note it, carry on with other work, and list it
   at the end.
3. **Know what may be written online.** Pushing a branch and opening or editing its PR
   on GitHub is fine. Ask before anything else that writes online: mod.io, Steam,
   Ubisoft Connect, GitHub's settings, any other service. By computer use, never start
   a game, and never touch mod.io, Steam or Ubisoft accounts. A game started on one PC
   affects the account on every PC.
4. **Work in a worktree**, not in the main checkout, which another tool may switch
   under you.

## For each change

1. **Write the Studio check before the change**: the steps, and what should be seen.
2. **Decide about the game.** Can it only be known to work in the game, and is it
   likely enough to break to be worth the maintainer's time? If so, make a test pack
   with `python tools/ingame_pack.py new`. Prefer questions the loader's log answers
   over ones that need play. Commit the pack in the companion repository's
   `in-game/<name>`. The PR says *Pending* and names that folder.
3. **Make the change**, with its tests.
4. **Before opening the PR:**
   - `python tools/check.py` passes, with both games and the loader where the machine
     has them.
   - The Studio check is done by computer use on the test machine, and the PR says
     what was seen. Or the PR says in one sentence why looking would show nothing
     different.
   - What the change fell short of and did not fix has a row in
     [docs/ledger.md](docs/ledger.md). A row it fixed is deleted.
   - The description follows
     [the template](.github/pull_request_template.md), and
     `python tools/pr_description.py <file>` passes on it.
5. **An absolute rule** of the PR checklist is broken only when the maintainer has said
   so in chat. Quote their words under *Exceptions*.
