# Repository settings

The GitHub settings the repository has. None of them is in a file the repository
carries, so nothing here is applied by a check or a script: each is switched on by
hand, by the owner, and this is the list to check them against.

Each setting says where it is on GitHub and what it is for. CI itself is described in
[development.md](../development.md#ci).

## The owner's account

Under **Settings > Emails**:

- [ ] **Keep my email addresses private.** GitHub then gives the address
      `<id>+<user>@users.noreply.github.com`, and uses it for what is done on the
      website, such as merging a PR.
- [ ] **Block command line pushes that expose my email.** A push of a commit that
      carries the private address is refused.

The owner's commits are written with the noreply address (`git config user.email`), as
author and as committer, and the block makes sure none brings the private address in.
A clone whose `user.email` is the private address has every push refused.

## Branches

Under **Settings > Rules > Rulesets**, one ruleset for `main` and `release/**`, the
branches the [branch rules](../development.md#branches) name:

- [ ] **Require status checks to pass**, naming both jobs of
      `.github/workflows/check.yml`: `check.py on Python 3.10` and
      `check.py on Python 3.13`. A check that is not required can be merged over.
- [ ] Leave **Require branches to be up to date before merging** off. It runs every
      open PR again after each merge, and the merge into `main` runs the check anyway.
- [ ] **Block force pushes.** A release is a tag on its branch, and a branch whose
      history can be rewritten leaves the tag pointing at something else.
- [ ] **Restrict deletions.** A release branch is kept, like its tag, after the next
      one is cut. The owner's bypass covers pull requests only, so it deletes nothing.
- [ ] **Require a pull request before merging**, with no approvals required. One
      maintainer cannot approve their own PR, and an approval required of nobody else
      would leave every PR waiting.

Whether the owner has to use a PR as well: the code standards say that work reaches
`main` through a PR ([code-standards.md](code-standards.md), section 10), and a direct
push would not run the check before it lands. So the owner is not exempt from the PR
itself. The owner is on the ruleset's bypass list for **pull requests only**: a PR can
still be merged when the check is broken for a reason outside the change, such as a
runner image that changed, while nothing reaches `main` without one.

## Security

Under **Settings > Advanced Security** (named **Code security** on some accounts):

- [ ] **Dependency graph**, which the two below need.
- [ ] **Dependabot alerts**, for a dependency with a published vulnerability.
- [ ] **Dependabot security updates**, which opens the PR that fixes one. The weekly
      updates are [`.github/dependabot.yml`](../../.github/dependabot.yml)'s, and need
      nothing switched on.
- [ ] **Secret scanning**, which looks for keys and tokens in what is pushed.
- [ ] **Push protection**, which refuses the push that carries one. The mod.io key the
      Studio asks for is the user's, and none is ever in the repository
      (code-standards.md, section 10).
- [ ] **Private vulnerability reporting**, so a security problem can be reported to the
      owner without being made public in an issue first.

## Actions

Under **Settings > Actions > General**:

- [ ] **Workflow permissions: Read repository contents and packages permissions.** The
      token every workflow gets can then read and not write. `check.yml` asks for
      `contents: read` itself as well, and a workflow that has to write, such as a
      release that publishes the zip on a tag, asks for it in its own job.
- [ ] **Allow GitHub Actions to create and approve pull requests**: off.
- [ ] **Actions permissions: Allow actions created by GitHub**, and no others until a
      workflow needs one. The workflows use only `actions/*`, each pinned to a commit.
- [ ] **Require approval for first-time contributors** for workflows run from a fork,
      as GitHub has it by default. The check runs with a read-only token and no
      secrets, so a fork's PR can do nothing with it but use the runner.
