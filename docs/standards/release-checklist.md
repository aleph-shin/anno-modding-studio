# Release checklist

What is checked once per release, on the release branch, before the tag. These are the
checks that need a person to look, or that take long enough that once a release is
the right rate for them. Every PR has already been through the
[PR checklist](pr-checklist.md). How the branches work is in
[development.md](../development.md#branches).

How a release is numbered, tagged, built and published is in
[development.md, "Making a release"](../development.md#making-a-release). This list
is what is checked on the way.

## When the branch is cut

- [ ] `main` passes `python tools/check.py` with both games and the loader installed.
- [ ] A draft PR from the release branch into `main` is open, with this checklist as
      its description. The results below are recorded in it.

## On the release branch

Fixes for what these checks find are PRs into the release branch, not into `main`.

### The look

- [ ] `python tools/render_themes.py <folder> --compare`: every picture that differs
      from its reference differs in a way this release meant.
- [ ] `python tools/render_themes.py <folder> --every` was looked at: every scheme,
      Flat and Compact, at 100% and 200%. German on Granite was looked at for text
      that is cut off, since German runs about 30% longer.
- [ ] Once the look is approved, the reference renders in
      `docs/design-review/renders/` are replaced with this release's. The next
      release's `--compare` then shows what changed since this one.
- [ ] The screenshots in `docs/images/`, which the README and the user guide show,
      still show what the Studio shows.
- [ ] The README still says what this release offers, and claims nothing it does
      not do.

### Using it

- [ ] The main tasks were done by hand in the Studio as it will ship: start a loadout
      from game files, edit an asset, export, and load the export in the game. Some of
      it was done from the keyboard alone.
- [ ] It was tried against both games, with the loader installed.

### Mods

- [ ] The census was run, and the result is in the PR beside the last release's.
- [ ] `tools/bench.py` was run, and the figures are in the PR beside the last
      release's.

### Licence, notices and credits

- [ ] [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md) still matches the frozen
      zip. List the zip's folder and check it against section 5: every licence text
      named there is in `licenses/`, `SOURCES.txt` names the versions shipped, and
      no `Qt6VirtualKeyboard*`, `Qt6Pdf*`, `Qt6Quick*` or `Qt6Qml*` file is in it.
- [ ] A dependency added, removed or moved to a new major version since the last
      release has its row in section 3 of the notices.
- [ ] Code ported from another project since the last release has its notice in
      THIRD-PARTY-NOTICES.md and a line pointing to it in the module's docstring.
- [ ] [credits.md](../credits.md) was read through. No entry is still marked
      *(to confirm)*, and what this release learned from or runs has an entry.
- [ ] The user guide's *What the Studio connects to* still lists every place the
      Studio contacts. `git grep -nE "urlopen|careful_get" anno_modding_studio`
      finds no other, beside mod.io's own client.
- [ ] The repository has GitHub's private vulnerability reporting switched on, which
      [SECURITY.md](../../SECURITY.md) sends reporters to.

### What was left open

- [ ] Every issue meant for this release is settled, or moved to the next one.

### The zip

- [ ] A trial build from the lock (`python tools/build_release.py`, development.md)
      passed its self-test, and the Studio it built was started once by hand on a
      PC that has never run the Studio from source.
- [ ] The user guide's *Install and run*, the README's *Install*, and the zip's
      `README.txt` still describe what SmartScreen shows for an unsigned exe, and how
      to get past it.

### What it keeps

- [ ] `__version__` in `anno_modding_studio/__init__.py` is this release's number.
- [ ] A file of every format the release build writes is saved into
      `tests/fixtures/formats/<release>/` with `python tools/format_fixtures.py`:
      the loadouts and their projects, the library's records, a backup manifest, a
      conversion journal, a save's journal, `game-updates.json` and
      `data-version.json`. `tests/test_formats.py` reads them with every later
      build, and fails while the release build's own differ from the formats it
      writes ([code-standards.md](code-standards.md), section 11).
- [ ] Where this release writes mods differently, the release notes say that mods
      exported with it get a new build number once.

## After the tag

- [ ] The workflow run that made the draft passed its *Self-test* step, on the build
      the draft carries.
- [ ] The draft's zip matches its `.sha256`, and its notices match the bundle: unzip
      it and check its root and `licenses/` against THIRD-PARTY-NOTICES.md, section
      5, with `SOURCES.txt` naming the versions in `requirements-release.txt`.
- [ ] The release on GitHub links the tagged source, which the GPL asks for beside
      the frozen zip.
- [ ] The draft is titled *Anno Modding Studio &lt;version&gt; (beta)*, says what
      changed, and is published as a release, not a pre-release.
- [ ] The release is published, not left a draft, and is the newest: the Studio's
      check for a newer version asks GitHub's `releases/latest`, which skips drafts
      and pre-releases, so until then no Studio hears of it. Its page
      (`releases/tag/v<version>`) holds the notes that the note after an update
      links to.

- [ ] The release branch's PR into `main` is merged, so its fixes are in the next
      release.
- [ ] The branch is left in place. It takes fixes to this release until the next
      release branch is cut, and is kept after that.
