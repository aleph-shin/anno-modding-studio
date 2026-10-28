# Development

How to run the tests and the checks, and where the code is described. The user guide
is [guide.md](guide.md).

## The code

[architecture.md](architecture.md) says what each module is for, which module answers
each question the Studio asks, and the words the code shares with the screen.
`tools/check.py` fails when a module is missing from it.

The package is `anno_modding_studio/`: `core/` has no Qt in it, and `ui/` is the
PySide6 app. Requires Python 3.10 or newer, `PySide6-Essentials` and `lxml`
(`requirements.txt`). Essentials, not the whole of `PySide6`: the Studio uses Qt's
Core, Gui, Widgets, Svg and Network, which are all in it, and the add-ons would bring
Qt Virtual Keyboard (GPL-only) and Qt PDF into the built Studio. A `PySide6` already
installed includes Essentials.

Other documents:

- [design-notes.md](design-notes.md): why the Studio works the way it does, and what
  was measured.
- [standards/](standards/README.md): the rules a change follows, and how each is
  held. [code-standards.md](standards/code-standards.md) is for the code,
  [design-standards.md](standards/design-standards.md) for text, help, colour,
  scaling and accessibility, [translation.md](standards/translation.md) for how the
  German is kept, and [pr-checklist.md](standards/pr-checklist.md) for what a PR is
  checked against.
- [proposals/](proposals/README.md): the plan each larger feature was built from.
- [credits.md](credits.md): the tools, projects and designs the Studio builds on.
  Add an entry when a change learns from, ports or runs someone else's work.
- [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md): the licences of what the
  Studio contains and runs on, and what the frozen zip must carry. Code ported from
  another project gets its notice there, and a line in the module's docstring
  pointing to it. The Studio is under [GPL-3.0-or-later](../LICENSE).

Comments and documents cite findings by an ID such as `P8-42`, `A6-7` or `R3-14`
("hands-on run 3"). They come from the review passes, sample audits and hands-on runs
before the first release, whose records are kept privately. An issue or PR number
written before then, such as `#196`, is the earlier private repository's, not this
one's. The comment beside each says what went wrong, so the code doesn't depend on
the record. New findings are filed as issues.

## Tests

```bash
python -m unittest discover -s tests
```

`python -m pytest` runs the same suite. Under either one the suite keeps out of your
own files: `tests/_isolation.py`, imported first by every test module, points the
Studio's home, settings, index cache and the game's mod.io download cache at temporary
folders. The suite makes a settings file of its own whatever `ANNO_STUDIO_SETTINGS`
says, and leaves one named from outside untouched. It also sets `ANNO_STUDIO_FOLDER`
to `none`, so an `AnnoModdingStudioData` folder you keep beside your checkout is not
used.

`ANNO_STUDIO_TEST_ORDER=<a number>` runs the tests under pytest in an order shuffled
by that number: the classes, and the tests inside each. `tools/check.py` picks a new
number each time and says which, so a test that passes only after another has run
fails there sooner or later, and the number runs it that way again.

`tools/check.py` shares the suite among several processes with pytest-xdist: as many
as the machine has processors, up to sixteen, or as `ANNO_STUDIO_TEST_WORKERS` says
(`1` runs it in one). Each process is handed whole classes in the shuffled order, and
writes down the tests it runs. When one fails, the check names that process and the
file it wrote. `ANNO_STUDIO_TEST_ORDER=<that file>` then runs what it ran, in its
order, in one process. Every process of the suite makes its own temporary folders.

The UI tests drive the real widgets offscreen, so they cover the click path: search,
open an asset, edit a value, remove it from the changes panel, generate XML. The
archive tests build real V2.2 archives in a temporary folder.

Some tests run against the installed games and the games' own mod loader, which is the
Anno Modding Tools extension for VS Code. Anyone changing the Studio needs both games
and the extension. Without them those tests fail and say what is missing. CI has
neither, and there they skip instead and are listed: see [CI](#ci).

## Before a change goes in

```bash
python tools/check.py
```

It runs ruff, mypy on `core/`, vulture, the count of code written twice
(`tools/duplicate_check.py`), the design checks, the standards checks and the suite. It needs the `dev` extras (`pip install -e .[dev]`). It fails if any ruff
rule's count, any file's mypy count or any file's count of names vulture finds unused
has grown past what `tools/check_baseline.json` records. Those counts are not at zero, and the check
guards the direction. A recorded count may not be higher than the main branch has it.
A file git sees as moved keeps the count it had under its old path.
The design rules and the standards rules allow no finding at all.

The standards rules are the ones the documents state and no linter looks for.
`python tools/standards_check.py` lists what it finds, and its docstring lists the
rules:

- `core/` imports no Qt and nothing of `ui/`, a leaf of `core/` stays a leaf, and no
  module uses another's `_private` name ([architecture.md](architecture.md), section 1).
- Every test module imports `_isolation` before the package.
- A `# noqa: BLE001` gives its reason on the same line.
- `requirements.txt` and `pyproject.toml` list the same dependencies.
- A relative link in a document leads to a file.
- The catalogs are in step with the code, no English reaches Qt without `tr()`, and
  every entry is translated ([translation.md](standards/translation.md)).
- No tracked file names a real user's folder. A path into `C:\Users` uses `Public`,
  or a placeholder such as `me` or `someone`.

[code-standards.md](standards/code-standards.md) has every rule, with the ones only a
reviewer can check, and [pr-checklist.md](standards/pr-checklist.md) is the list to go
through before a PR is opened.

- `--quick` skips the suite.
- `--ratchet` writes lower counts back once a change has brought them down.

A pre-push hook runs the quick half on every push, so a broken count or rule shows
in seconds rather than when CI gets to it. It is early feedback, and CI is what
decides. It reads the working tree, so commit first: it says so when there are
uncommitted changes. It is off until it is switched on, once per clone:

```bash
git config core.hooksPath tools/hooks
```

Before the counts, the hook runs `tools/private_scan.py` over the commits being
pushed. It looks for the terms on a list of your own: your user name, an address, a
folder of yours. The list can't be in the repository, since that would publish what
it lists, so keep it elsewhere, one term a line, and point at it once:

```bash
git config --global studio.privateTerms <path to your list>
```

A match refuses the push, and every image the push adds is listed for you to look
at. With no list it looks for nothing.

A change to the UI also follows the checklist at the end of
[design-standards.md](standards/design-standards.md). The renders are looked at once
per release, but rendering a screen sooner helps whenever seeing it would:

```bash
python tools/render_themes.py <folder>
```

A change to a string runs `python tools/translations.py update` and redoes the German
for it. See [translation.md](standards/translation.md).

## CI

[`.github/workflows/check.yml`](../.github/workflows/check.yml) runs
`python tools/check.py --ci` on every PR and on every push to `main` and to a
`release/` branch. It runs on Windows, the one platform the Studio is for, once on
Python 3.10, the oldest the Studio supports, and once on 3.13. It installs
`requirements.txt` and the `dev` extras, whose tools are pinned in `pyproject.toml` so
that CI counts with the same ruff and mypy as the baseline was counted with.
Dependabot proposes each new release of them and of the actions, once a week
([`.github/dependabot.yml`](../.github/dependabot.yml)).

A PR shows the result as its **check** status, one for each Python. A run's page,
under the repository's **Actions** tab, has the log and a summary: each check with
what it found and whether it passed, the tests that did not run and why, and what CI
never runs. A failing check is also an annotation at the top of the page. CI is the
authority on whether a change passes. The hook and a run of `tools/check.py` at home
are the same checks, sooner.

`--ci` differs from a plain run in three ways:

- A test that needs a game or the loader skips, where elsewhere it fails. The summary
  lists each one under what it needed.
- A failing check is said as an annotation, and the summary goes to the job's page.
- The recorded counts not compared with the main branch's fail, which on a copy with
  no repository they do not. The workflow checks out the whole history for this.

What CI cannot check, so it stays with the contributor or the release:

- **The tests that need the games or the loader.** About 115 of the suite's 5,200
  (counted 17 Oct). A PR says whether its suite ran with them (the
  [PR checklist](standards/pr-checklist.md)), and the release branch runs them (the
  [release checklist](standards/release-checklist.md)).
- **The census and the bench**, which need the game's files and a mod.io snapshot,
  and take hours. A PR that could change an export or a wait runs them, and every
  release does.
- **The renders and the look**, which need a person, once per release.
- **The Studio against the games themselves**: loading an export in the game, and a
  run by hand. Once per release.

The release has a workflow of its own,
[`.github/workflows/release.yml`](../.github/workflows/release.yml), run by a pushed
tag: see [Making a release](#making-a-release).

How the repository is set up around this, branch protection among it, is in
[repository-settings.md](standards/repository-settings.md).

## Branches

- **`main` is where work goes in.** Every PR targets it, and it passes
  `python tools/check.py` at every merge. A project of several PRs can gather them on a
  branch of its own first, which merges into `main` when the project is done.
- **A release starts as a branch cut from `main`,** `release/<version>`. Only fixes go
  on it. The [release checklist](standards/release-checklist.md) is done there, while
  work carries on in `main`.
- **The release is a tag on that branch.** The branch is then merged back into `main`,
  so its fixes are in the next release. It takes fixes to that release until the next
  release branch is cut, and is kept after that.

A ruleset on GitHub holds `main` and the release branches to this: nothing reaches
them but through a PR whose check passed, and none of them can be rewritten or deleted
([repository-settings.md](standards/repository-settings.md)).

## Making a release

A release is a tag on its release branch ([Branches](#branches)), and the tag builds
it. Everything here is done by hand except the build, which
[`.github/workflows/release.yml`](../.github/workflows/release.yml) does when the tag
is pushed.

1. **The number.** On `main`, set `__version__` in `anno_modding_studio/__init__.py`
   to the release's number, `<major>.<minor>.<patch>`, in a PR of its own. It is the
   only place the number is written.
2. **The branch.** Cut `release/<version>` from `main`, open the draft PR into `main`
   and go through the [release checklist](standards/release-checklist.md) on the
   branch. Fixes go into the branch.
3. **A trial build**, on the branch, when the checklist asks for one:

   ```bash
   py -3.13 -m venv C:\venvs\ams-release
   C:\venvs\ams-release\Scripts\python -m pip install --require-hashes --no-deps -r requirements-release.txt
   C:\venvs\ams-release\Scripts\python tools/build_release.py
   ```

   It freezes the Studio into `dist/AnnoModdingStudio/`, runs its self-test, and
   makes `dist/AnnoModdingStudio-<version>-windows-x64.zip` with its `.sha256` and the
   draft's text in `dist/release-notes.md`. It must be Python 3.13, the one whose
   notices `tools/release/licenses/` holds, in an environment with nothing but the
   lock: it stops when `PySide6-Addons` is installed, PySide6 does not import, or
   a package differs from the lock. Uninstalling `PySide6-Addons` from an
   environment deletes `PySide6/__init__.py`, which every PySide6 wheel installs,
   so force-reinstall `PySide6-Essentials` after it. Keep the environment's path short, as above, since Windows' 260-character
   limit cuts PySide6's deepest files. `--unlocked` and `--allow-missing-notices`
   make a build that is not for release.
4. **The tag.** On the release branch, once the checklist is done:

   ```bash
   git tag -a v<version> -m "Anno Modding Studio <version>"
   git push origin v<version>
   ```

   The workflow checks that the tag names `__version__`, installs the lock with its
   hashes on `windows-latest`, freezes, runs the self-test, packages, and creates a
   **draft** release titled *Anno Modding Studio &lt;version&gt; (beta)* with the zip
   and its `.sha256` attached. A tag that does not name the version stops it before
   anything is built.
5. **The draft.** Open it under **Releases**. Download the zip, check its digest
   against the `.sha256`, unzip it, and start it once in a Windows account that has
   never run the Studio, as the checklist says. Write *What changed* into the
   text the workflow left. Then **Publish release**. Leave **Set as a pre-release**
   off: the update check reads `releases/latest`, which skips pre-releases.
6. **After.** Merge the release branch into `main`, as the checklist says.

The update check finds a release by its tag, `v<version>`, and the zip by its name,
`AnnoModdingStudio-<version>-windows-x64.zip`. Neither changes without changing the
check.

### The build's inputs

`requirements-release.txt` pins everything the release is built from, PyInstaller and
every runtime dependency, each with its hashes. It is compiled from
`requirements-release.in`, which takes the Studio's own `requirements.txt` and adds
PyInstaller. Dependabot proposes each new version in it, hashes included. To refresh
it by hand, in an environment with `pip-tools`, on Python 3.13:

```bash
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url --output-file=requirements-release.txt requirements-release.in
```

Add `-P <package>==<version>` to move one package and leave the rest. A new Qt also
needs Qt's own third-party notices in `tools/release/licenses/Qt/` for that version
(THIRD-PARTY-NOTICES.md, section 5), and a new Python minor version needs
`tools/release/licenses/Python-incorporated.txt` taken from that version's
documentation; the build refuses to package without them. It also reads the Qt
version the notices' `README.txt` names, and refuses one that isn't the Qt it freezes.

What the folder holds is decided in `tools/build_release.py`: the Qt plugins kept, the
modules left out, and the files that fail the build if they are there. After the
first build of a new PySide6, list the folder and check it against
THIRD-PARTY-NOTICES.md, section 5.

### Signing

The zip's exe isn't signed, so SmartScreen warns on every new release until enough
people have run it. Signing would remove the warning. Nothing is set up yet. The
options, for when it is worth it:

- **SignPath Foundation**, free code signing for open-source projects. The certificate
  is SignPath's, the signing runs from the release workflow, and the project must meet
  its conditions (an OSI licence, a public repository, a reviewed build). The
  recommendation.
- **Azure Trusted Signing** (Artifact Signing), Microsoft's own service, for a monthly
  fee. Its certificates are trusted by SmartScreen sooner than an ordinary one, but
  individuals need an identity it can verify, and its availability by country varies.
- **An OV certificate** from a certificate authority, now held on a hardware token or
  in a cloud HSM. It costs the most to keep and still builds reputation slowly.

Whichever it is, the exe and `_internal`'s DLLs of our own are signed in the workflow,
after the self-test and before the zip.

## Trying it without the game files

```bash
python tools/make_sample_assets.py sample
```

Then open `sample/assets.xml` in the app (**Choose a file…** on the Start tab), and
optionally load `sample/texts_english.xml` as the localisation file.

## Where a game's details live

Anno 117's tooling is young. If the game rejects a generated file, check the dialect
in `core/games.py` first, then the modinfo in `core/modinfo.py`.

## Things worth knowing about the internals

- **The index.** The first load of a 300 MB `assets.xml` takes about a minute. After
  that it opens from a cache in `%LOCALAPPDATA%\AnnoModdingStudio\cache\`, keyed on
  the file's path, size and timestamp. Indexing streams the file, so memory stays
  flat. The index holds the game's file plus the localisation and schema files loaded
  into it. Nothing a loadout edits is written to it.
- **Archives.** Encrypted archive blocks (a few exist in Anno 1800's graphics
  archives) are skipped. Nothing the Studio needs is in one.
- **`merge`** is how a field the asset does not spell out is written: one field,
  merged into the node it belongs in. The editor's *Add field* writes it, and so does
  the import for a field a mod's own merge makes. A list entry is still an `add`. See
  [design-notes.md](design-notes.md#a-field-that-is-not-there-is-made-sure-of-not-added).
- **Combo boxes** are plain, non-editable `QComboBox`es everywhere. The Template
  filter narrows to the templates among the current search results once there is a
  query.
