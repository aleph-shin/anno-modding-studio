# Code standards

The rules for the code, the tests, the tools and the documents. Follow them when you
change any of them. The rules for what a person sees are in
[design-standards.md](design-standards.md), and the rules for translatable text are in
[translation.md](translation.md).

Each rule says how it is held: by **the hook** (`python tools/check.py --quick`, on
every push), by **the suite** (`python tools/check.py`), or by **review**. CI runs the
hook's checks and the suite on every PR. [README.md](README.md) explains the three.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

## 1. Where code goes

[architecture.md](../architecture.md) is the map. Read its sections 1 and 2 before
adding a module or a function another module will call.

| Rule | Held by |
|---|---|
| `core/` imports no Qt and nothing from `ui/`. | hook |
| `ui/` asks and `core/` answers. A dialog gathers what you want and shows what came back. It does not work out folders, parse files or plan what Play does. | review |
| One question has one owner. Before writing a function that answers a question, look for the module that already answers it in architecture.md, section 2, and ask that module. | review |
| A leaf of `core/` stays a leaf. The leaves and what each may import are listed in `tools/standards_check.py` (`LEAVES`) and in architecture.md. Change both together, and only for a reason the PR states. | hook |
| The largest import cycle in `core/` does not grow. | hook |
| No module uses another module's `_private` name. A helper another module needs is made public and named for what it does. The tests may reach a private name to pin it. | hook |
| A new module under `core/` or `ui/`, and a new tool, is named in architecture.md, section 4, with what it is for. | hook |
| The code uses the word the screen uses. A type only the code sees is named for what it is. The screen's words are in the glossary in design-standards.md, section 14. Where each is in the code, and the words only the code uses, are in architecture.md, section 3. | review |

A shared helper belongs in a leaf, so anything can use it without joining a cycle.

## 2. Python

| Rule | Held by |
|---|---|
| The code runs on Python 3.10. | hook (ruff's `target-version`) |
| Every module starts with a docstring that says what the module is for. Where the module replaced something, it says what was wrong before. | review |
| Every module has `from __future__ import annotations`. | review |
| Functions in `core/` are annotated. mypy checks `core/` and not `ui/`. | hook |
| Text is built with `%`-formatting, and types are `typing.Dict`, `List` and `Optional`. Both are the convention the code already keeps. New code matches the module it is in. | review |
| Lines are wrapped by hand to 79 columns. Nothing reflows a file to fix line length. | review |
| Imports are sorted, with `anno_modding_studio` as first-party. | hook (ruff `I`) |
| camelCase is for overriding Qt. The Studio's own names are snake_case. | review |
| A comment says why, or what a constant is. It does not repeat the line below it. | review |

## 3. The linters and the counts

`tools/check_baseline.json` records ruff's findings by rule, mypy's errors by file,
vulture's unused names by file, the size of the core import cycle, and the window
walk's one count.

- **A count never grows.** The hook fails when one does. Fixing three findings does
  not pay for a new one elsewhere, because the counts are kept by rule and by file.
- **A count is never raised by hand.** The hook compares the recorded counts with the
  main branch's and fails when one is higher.
- **When a change brings a count down,** run `python tools/check.py --ratchet` and
  commit the lower number with the change.
- **Design rules and standards rules have no count.** One finding fails.

When vulture finds a new unused name, the name is one of three things:

1. Dead. Delete it.
2. Used only by something vulture cannot see, such as a name looked up by string. Use
   it where vulture can see it, or keep it and say why in the PR.
3. A Qt override. `tools/check.py` already leaves out camelCase methods.

### Waivers

A waiver is a comment that switches one rule off for one line. Each gives its reason
on the same line.

| Waiver | Form | Held by |
|---|---|---|
| A broad `except` | `# noqa: BLE001 - <reason>` | hook |
| A design rule | `# design: <reason>` | hook |
| English handed to Qt on purpose | `# i18n: keep - <reason>` | hook (the marker), review (the reason) |
| Any other ruff rule | `# noqa: <rule>`, with a reason unless the rule is `E402` or `F401` in a test's imports | review |
| A type error | `# type: ignore[<code>]`, never bare | hook (mypy's `warn_unused_ignores` finds the stale ones) |

A waiver is not a way to make the hook pass. If the rule is wrong for the case, say so
in the PR.

## 4. Errors

| Rule | Held by |
|---|---|
| Catch the exception you expect. A broad `except Exception` needs the waiver above, and its reason says why nothing narrower will do. | hook |
| An error shown to a person says what happened and what to do next, in that order. | review |
| Code matches on an exception's type or an attribute, never on its message. The message is translated. | review |
| A background worker reports a failure to its window. It does not raise into the thread. | review |

## 5. Files and state

| Rule | Held by |
|---|---|
| The Studio's own state and the game's switch files are written through `core/disk.py`, which writes a file whole or not at all. | review |
| A file the Studio keeps can be missing or wrong. Read it with `disk.read_json` and a default. | review |
| Where things are on disk is `core/places.py`'s to say, and for the window `ui/where.py`'s. No module builds a path under Documents or LOCALAPPDATA by hand. | review |
| Anything written to disk or sent anywhere stays English and stays ISO: mod files, loadout files, settings, file names, anything sent to mod.io. A mod made on a German PC is the same mod. | review |
| A folder the Studio reads from a new place gets an `ANNO_STUDIO_*` environment variable, and `tests/_isolation.py` points it at a temporary folder. | review |
| The Studio does not write another tool's settings or records. | review |

## 6. The interface

| Rule | Held by |
|---|---|
| A change to the UI follows [design-standards.md](design-standards.md) and its checklist in section 15. | hook (section 13's static rules), suite (the window walk), review (the rest) |
| Every sentence a person reads goes through `tr`, `trn` or `tr_noop`. | hook (`lint`), suite (`untranslated_widgets`) |
| The catalogs are in step with the code, and every entry is translated. After changing a string, run `python tools/translations.py update` and redo the German under [translation.md](translation.md). | hook |
| A question the main window asks goes through `ui/prompts.py`, so a test can answer it. A yes-or-no question is asked with `widgets.ask`. | hook (`QMessageBox` buttons), review |
| A label is plain text: the app makes every label still in `AutoText` plain before it is drawn (`widgets.external_text_stays_plain`). The Studio's own markup is shown with `widgets.rich_label` or `widgets.show_rich`, and every value from outside in it goes through `html.escape`. | review |
| A new dialog has a factory in `tests/design_dialogs.py`. | suite |

## 7. Tests

| Rule | Held by |
|---|---|
| Tests are `unittest.TestCase` classes. `python -m unittest discover -s tests` and `python -m pytest` run the same suite, so a test uses nothing only pytest has. | review |
| Every test module imports `_isolation` before it imports the package. | hook |
| What two test modules share goes in `tests/support.py`. A test module does not import from another test module. | review |
| A test never opens a window a person has to close. Answer a prompt with `mock.patch.object(window.prompts, ...)`, and mock a dialog's `exec()` where a test would reach it. | review |
| A test reads and writes only temporary folders. | review |
| A test's name says what is true: `test_leaves_a_leaf_within_its_list`. | review |
| A fix for a bug comes with a test that fails without the fix. | review |
| A new rule in `tools/design_check.py` or `tools/standards_check.py` gets source that breaks it and source that keeps it. | review |
| A deprecation warning raised inside the package fails the test. | suite (under pytest) |

Some tests need both games and the *Anno Modding Tools* extension installed. Without
them those tests fail and say what is missing. A PR says so if the suite was run
without them.

## 8. Tools

| Rule | Held by |
|---|---|
| A tool's docstring says what it is for and how to run it. A probe's docstring says which question it answered. | review |
| A tool is named in architecture.md. | hook |
| A tool that builds a window sets up the suite's isolation first. | review |
| A tool uses the package's public names, as any module does. | hook |

## 9. Documents

Update the document that owns the fact, in the same PR as the code.

| When you change | Update |
|---|---|
| What the Studio does for the person using it | `docs/guide.md`, the user guide |
| Why it works that way, or a measurement | `docs/design-notes.md` |
| A module, an owner of a question, or one of the shared words | `docs/architecture.md` |
| A rule | The document in `docs/standards/` that states it, and the check that holds it |
| How to run the tests or the checks | `docs/development.md` |

- A proposal in `docs/proposals/` is kept as written. Update its status line and the
  table in `docs/proposals/README.md`, not its text.
- A link between documents is relative, and leads to a file. The hook checks it.
- A number in a document that the code can change is either checked or dated
  ("checked 29 Sep").

## 10. Commits and PRs

| Rule | Held by |
|---|---|
| A PR may carry several changes, related or not. Its description lists each one, so a reviewer can tell them apart and none goes in unmentioned. | review |
| The subject line is a sentence that says what is true after the change: "Install from mod.io names the library". Where a PR carries several changes, it names the main one or what they share. | review |
| The body says what was wrong or missing, then what changed, for each change. It names what was measured. | review |
| Work happens on a branch, and reaches `main` through a PR. | review |
| The hook passes. `git push --no-verify` is for a push that cannot pass yet, and the PR says why it was used. | review |
| Nothing from a game install, no extracted game data and no key or token is committed. `.gitignore` covers the usual places. | review |

[pr-checklist.md](pr-checklist.md) is the list to go through before opening a PR.

## 11. What the Studio keeps, from 0.1.0

From the first public release, 0.1.0, what people have saved survives an update,
and going back a version doesn't damage it. Before that release an old loadout was
not something a change had to allow for. These rules apply to every change from
0.1.0 on.

Five numbers say what wrote something:

| Number | Where | Bumped by |
|---|---|---|
| The Studio's version | `__version__` in `anno_modding_studio/__init__.py` | each release |
| A file's own stamp | `LOADOUT_FORMAT` in `core/changes.py`, `PROJECT_FORMAT` in `core/projects.py`, `FORMAT` in `core/backup.py`, `RECORD_FORMAT` in `core/library.py`, `JOURNAL_FORMAT` in `core/convert.py` and in `core/saving.py`, `REMEMBERED_FORMAT` in `core/gameversion.py` for `game-updates.json`, `SCHEMA_VERSION` in `core/index.py` and in `core/filecache.py`, and a cache's key: `UNPACK_VERSION` in `core/modzip.py` for the zip mirrors, `EXTRACTED_VERSION` in `core/gamefiles.py` for the extracted files, `CONVENTION` in `core/generated.py` for the generated copies. Two notes are written and read by nothing: `FULL_COPY_FORMAT` in `core/convert.py` (`full-copy.json`) and `AFRESH_FORMAT` in `core/unsubscribe.py` (`afresh.json`) | a change to what that file means |
| The data version | `DATA_VERSION` in `core/dataversion.py`, kept in each of the Studio's folders as `data-version.json` | a change an earlier build would harm by reading alone, before its own stamp stops it writing: a folder laid out differently, or a record whose new meaning an earlier build would act on |
| The reader | `READER_VERSION` in `core/reader.py` | any change to how mods are read |
| A build | `builds`, per mod | the mod's output, never the Studio's version |

| Rule | Held by |
|---|---|
| The Studio's version is written once. `pyproject.toml`, the About box, the User-Agent and every stamp below read `__version__`. | suite |
| Every file the Studio keeps carries a stamp: `"format"` in JSON, `meta.schema_version` in SQLite, a version in a cache's key. Each of the Studio's folders has `data-version.json`, with the newest data version and Studio version that used it. | review |
| A JSON record the Studio reads, changes and writes back is read and written through `core/records.py`, which stamps it with `"format"` and `"written_by"`. | suite (`tests/test_migrations.py`), review (a new record) |
| A change to what a kept file means bumps its stamp. A field added is not a change of meaning where every released build keeps keys it doesn't know and writes them back. A loadout does, from 0.1.0: every key it doesn't know, at the top of the file and in each entry (a change, layer, op, unread op, checkpoint, profile, build, publish record, reference to a project), is written back as it was. So does a project file: at its top, in its details, its release facts and `inside`, on each op of theirs it has `off`, and in each entry of its ops and history. So does a library record, entry by entry. No reader keeps a name back for a file of before 0.1.0, so a later build is free to use any name. A field added whose value an older build's edits would leave saying something untrue is still a change of meaning. | suite (that they are kept), review (the bump) |
| A build refuses a stamp newer than it reads, and says which version wrote it. It never writes over such a file, and never prunes or restores from one. A folder of a newer data version is not started over at all. | suite |
| A record that can't be read, or holds an entry of a shape no Studio writes, is never written over and never read as empty. The write is refused with `records.RecordError`, whose sentence says what to do. Code that moves something and then records it asks first (`library.check_records`), so nothing is moved that its record can't say. | suite |
| Undoing anything from a record checks the whole record first and refuses a step it doesn't know, before doing any of it (`convert.cannot_undo`). | suite |
| A backup whose manifest this build can't read is listed as kept, and never pruned or restored. Which save holds the first copy of a file is worked out by the mod and path, or for a file outside the mods folder by where it lives. | suite |
| Reading an older stamp goes through one migration function per step, called on load. No other code reads the old shape. Nothing of before 0.1.0 is read: a loadout or project that says no format, or one older than `OLDEST_LOADOUT_FORMAT` or `OLDEST_PROJECT_FORMAT` (0.1.0's), is refused with a sentence. | review, suite (the refusal) |
| Before the first write that upgrades a file somebody made, a copy of the old file is kept beside it, once. A loadout or a project another version of the Studio wrote is kept as `<name>.annoload.<version>.bak` or `<name>.annoproj.<version>.bak` (`records.keep_other_version`), whichever way the versions go, by every writer of either. A record of an older format is kept as `<name>.format<n>.bak` (`records.keep_before_upgrade`). | suite |
| A cache is rebuilt, never migrated. Its key includes its schema and, where it comes from reading mods, `READER_VERSION`, or the version of the code that made it (`UNPACK_VERSION`, `EXTRACTED_VERSION`). Pruning removes only older caches, never a newer Studio's. | suite (the index, the zip mirrors, the extracted files), review (the rest) |
| `READER_VERSION` is bumped in the change that makes a mod read differently. A loadout's readings (`Loadout.read_by`) and the loader probes' verdict are kept under it, and another number has them made again. | suite (that they are made again), review (the bump) |
| Text the Studio writes into mods or game files to recognise later is frozen: the markers, the keys of the modinfo block, the keys of a manifest. Add, never reword or remove. Each is written from a constant, never spelled inline, and `tests/test_contracts.py` pins its words. It pins the names the Studio finds its own things by too: the record files and `live.json`'s keys, a backup store's `slug`, the asset fingerprint, `data-version.json` and its keys, a project file's suffix and `kind`, the `loadouts` and `projects` folders, `settings.ini` and its `library_dir`, and `generated.json`'s `source`. | suite (the words), review (that a new one is pinned) |
| A stamp the Studio writes into a mod is left out of its build digest, so a new Studio alone gives no mod a new build. | suite |
| A change to how the Studio writes a mod gives every numbered mod a new build at its next export, with nothing of the mod changed: see below. The release notes say so. | review |
| Each release's files are read by every later build: `tests/test_formats.py` reads every folder in `tests/fixtures/formats/<release>/`, checks that nothing in it is lost and that the next format is refused. A format change that fails it needs a migration, not a new fixture. While a build's `__version__` is the newest release there, each of its fixtures says the format the build writes. | suite |
| Each release saves a file of every format from the release build into `tests/fixtures/formats/<release>/`, with `python tools/format_fixtures.py`: loadouts and their projects (for both games, with the work a user's files hold), the library's records, a conversion's record, a save's journal, a backup's manifest, `game-updates.json` and `data-version.json`. A new format, or a new part of one, is added to the tool in the change that adds it. | [release checklist](release-checklist.md) |

**Build numbers follow the Studio's output.** `builds.content_digest` hashes what
an export writes: the ModOps less their comments, with each line's trailing space
and the blank lines dropped; the modinfo as the Studio writes it, less the build
and the Studio's version; what it was built against; and the files the mod carries.
So the Studio writing the same mod differently is a new build of it: attributes in
another order, another indent, an entity spelled another way, a new modinfo field,
ModOps grouped or ordered differently. Every numbered mod then gets a new build at
its next export. That is a release-note item ("mods exported with this version get
a new build number once"), never something to do quietly.

A digest that left the Studio's formatting out would hash a canonical form instead:
the patch parsed and written as C14N 2.0 (`xml.etree.ElementTree.canonicalize`,
comments dropped, whitespace-only text between elements dropped and other text
kept as it is), and the modinfo parsed and dumped with sorted keys and no indent,
the Studio's block left out. That would take formatting out of it, though not a
change in which ModOps the Studio writes for the same mod. Changing the digest is
itself one new build for every mod, so it is best done in the release that would
bump them anyway.
