# Consolidation: a proposal

How to take the Studio from "many features, each built well on its own" to one
codebase whose pieces fit: where every question the app asks has one place
that answers it, and a reader can find that place. It is based on a survey of
the whole repository on 29 Sep 2026, made in five strands (core
responsibilities, the UI layer, the test suite run and timed, app performance,
and documentation against code). Anchors are relative to
`anno_modding_studio/` unless they start with `tests/`, `docs/` or `README`.

**Recommendation in one paragraph.** Organise the pass around a single rule:
*one question, one owner*. The survey's most important result is not that the
code is large; it is that several questions — which mods are installed, which
copy of a mod loads, where the mods folder and the library are, whether an
origin is a bundled mod — are each answered in three to six places, the
answers have begun to disagree, and the disagreements are already bugs. So
settle the map of questions to owners first, consolidate `core/` bottom-up
against it, then cut `MainWindow` along the seams that the consolidated core
makes obvious, and only then tune performance and prune tests, because both
get much simpler once there is one inventory to cache and one seam to test at.
Before any of it, spend a small stage on making the work cheap: the test suite
leaks windows and spends 43% of its time on that, and nothing gates ruff or
mypy, both of which have more than doubled since Pass 1.

**Status.** Stage 0 is done (REVIEW.md, Pass 8: P8-1 to P8-7). Stage 1 is
done: the renames, and the map in `docs/architecture.md`, approved on 29 Sep
(P8-8 to P8-10). Stage 2 is done (P8-11 to P8-41): no private name crosses
a module boundary, and mypy on `core/` is at 1. Stage 3 is done (P8-42 to
P8-66): one resolver for places, the questions on one object, ten
controllers, six shared UI helpers and the tidy-ups took the window from
7,248 lines to 2,018, and the feature freeze on `main_window.py` is over.
Stage 4 is done (P8-67 to P8-74): `tools/bench.py` times the app against a
large mods folder, and on it an edit went from 26 s to 2, Play from 18 s to
4, opening an asset from 19 s to a quarter of one. Stage 5 is done (P8-75
to P8-78): the suite isolates itself, requires the game and the loader, and
is pruned. Stage 6 is done (P8-79, P8-80): true status lines, docstrings that
match the code, and the README split into the guide and docs/design-notes.md.
Every stage of the proposal is done. Section 5 lists the decisions, all settled.

---

## 1. What the survey found

### The numbers since the last review (Pass 7, 20 Sep)

| | at Pass 4–7 | now |
|---|---|---|
| `ui/main_window.py` | 2,776 lines, 101 methods, 14 sections | **7,617 lines, 265 methods, 25 sections** |
| `core/modops.py` | — | 6,130 lines, imported by 12 core modules |
| core + ui source | ~23k lines | ~45k lines |
| tests | 1,282 | **2,789**, all passing, 265 s |
| ruff findings | 59 | **132** |
| mypy findings in `core/` | 32 | **169** in 24 files |
| README | — | **4,689 lines** |

Roughly 200 commits and ten features (mod.io, collections, loadouts, playing,
profiles, the library, versioning, GUID ranges, loader gaps, German) landed in
nine days, mostly on separate branches. Each is coherent inside itself. The
drift is at the joins.

### The headline: the same answer computed in several places has started to disagree

Three confirmed by reading, one suspected:

- **B1 `core/patching.py:86` `is_bundled` is `"/" in origin`.** A mod.io-cache
  origin is `"<id>/<folder>"`, so a mod installed by the in-game client is
  treated as bundled inside a host, and edits aimed at it are pushed into a
  patch. `dependencies._parts` (`core/dependencies.py:327`) exempts the numeric
  first part; so do truth and convert, and those two differ again from
  `_parts` on `"<id>/A/B"`. Six inline copies of "is this origin bundled".
- **B2 `ui/main_window.py:5132` and `:7275`** call `library.game_dir(game)`
  without the chosen root, so with a custom library location the live-profile
  and live-project lookups read the default folder. `_library_dir()` (`:4852`)
  does it right; three dialogs fall back to the default too.
- **B3 "which copy of a ModID loads" has three answers.** `core/truth.py:616`
  ranks by version then hand-installed; `core/installplan.py:306` adds
  not-bundled; `core/modops.py:5570` `_better` puts the copy named in the log
  first and ranks versions with its own parser (`_version_rank`, versus
  `updates.version_key`). For two copies of equal version, one bundled, the
  card and the install plan can name different winners.
- **B4 (suspect) five resolvers for "which mods folder".** `_live_folder`
  (`:3269`), `_truth_folder` (`:4922`), `_install_folder` (`:6246`),
  `_install_target` (`:6907`, accepts a stale path from another machine) and
  `_mods_folder` (`:3996`). Play writes to the first; the card reads the second.

That is why this proposal puts consolidation before polish: each extra copy of
an answer is a place for the next bug of this kind.

### Designs the Studio has outgrown

- **`modops.py` is secretly the "mod folder" layer.** 12 core modules import
  it, mostly lazily, not for ModOps but for `read_metadata`, `bundled_in` and
  `mod_folders`. That one fact creates most of a 15-module import cycle
  (changes → dependencies → loadout → modops → changes; generator → rules →
  modops → generator) and makes `versioning`, a pure semver module, pull in
  the ModOps importer via `updates.version_key`.
- **Play lives in the window.** `ui/main_window.py:3281-3736` plans and
  carries out Play: switching links, writing the 117 profile, options and
  tweaks, merging `Written` results by hand. It builds through
  `output.build_into` and reads the *Export tab's checkboxes*
  (`opt_comments` and the others), so Play's output depends on widget state on
  another tab. `output_panel.py:2219 _write_out` and `:2320 build_into` are
  near-identical build loops.
- **`MainWindow` is now a tangle.** Pass 4 judged it "a decomposition map, not
  a pile", and at the time that was right. Today, headings no longer describe
  their contents ("the File menu's submenus" holds 13 patching methods; "who
  you are" is two-thirds mod.io; "the library" is an empty heading), 22 of 25
  sections touch `project`, 72 imports sit inside functions, 10 attributes are
  created lazily with `getattr(self, "_x", None)`, and `settings_dialog.py`
  calls 22 `self.window.*` methods — an interface with no object behind it.
- **Vocabulary has forked.** "Profile" is four types (`changes.Profile`,
  `loadout.Profile`, `convert.FoundProfile`, `games.GameProfile`); "Installed"
  is four (`modio_cache`, `modzip`, `library`, `installplan.InstalledMod`);
  "loadout" is both the on-disk state module and a project flag; "collection"
  is both a mod.io collection and `project.listed`; the code says "snapshot"
  where the UI says "checkpoint".
- **Superseded code still standing.** `core/references.py` (overtaken by
  `refs.py`, kept as a fallback in one place), `core/folding.py` (a one-off
  migration), `convert.apply_profile`, `loadout.out_of_order`,
  `loadout.pointed_at`, `patching.without_a_patch`, `modops._only_a_condition`,
  `discovery.ASSET_RELPATHS`, `MainWindow.confirm_move_in`,
  `BrowserPanel = AssetSearchBar`, `grouping._Owners`, `self.probeResults`.
  Grep-confirmed as test-only or uncalled at survey time; re-verify at removal.

The full finding list with anchors is Appendix A.

---

## 2. The rule, and the map it produces

**One question, one owner.** For every question the Studio asks, name the one
module that answers it; every other module asks that module. The map below is
the target, and Stage 1 writes it up as `docs/architecture.md`, where it
replaces the README's hand-kept module tree (which is already missing 20 core
modules).

| Question | Answered today in | Proposed owner |
|---|---|---|
| What is in this mod folder: modinfo, ModID, version, dependencies, bundled mods | `modops.read_metadata` + 5 wrapper loops (library, installplan, xmltest, updates, imya) + `dependencies.links` re-parsing | new `core/mods.py` (name open), lifted out of `modops` |
| Is this origin bundled, and what is its host | 6 inline copies (B1) | one function in `core/mods.py` |
| Which mods are on this PC, where | `installplan.inventory`, `discovery.mods_on_this_pc`, `loadout.read`, `modops.mod_folders`, `convert.survey` | `installplan.inventory` as the one snapshot; the others become views of it |
| Which copy of a ModID loads | truth, installplan, modops (B3) | one function, one tie-break |
| How versions compare | `updates.version_key`, `modops._version_rank`, `xmltest.version_tuple`, `xmltest._version` | `core/versioning.py` |
| Facts about a game | `games.py`, plus `loadout.DOCUMENTS`, `launch.EXES`, `xmltest.BUILDS`, `texts._GUI` | `games.GameProfile` |
| Where things are on disk: Documents, LOCALAPPDATA, library root, mods folder | 4 core builders, 5 UI resolvers (B2, B4) | `core/places.py`, plus one UI-side resolver |
| What the loader logged | `loadout._LOADED`/`_logged_paths` and `loaderlog` | `loaderlog` |
| Reading and writing the Studio's JSON state | ~20 ad hoc try/`json.loads`/except blocks; project save not atomic (`changes.py:1845`) | one small helper with atomic write |
| Play: what to do, and doing it | `MainWindow` 3281–3736 | core (`core/play.py` or similar); the window asks and reports |
| Download, verify, install | `modio_get_dialog:543`, `modio_updates_dialog:350` (no retry), `installplan:758` | `installplan` |
| Does this value name a GUID | `references.names_a_guid` ≡ `refs.names_a_guid` | `refs` |
| Which tabs are open | the gate plus 3 navigation side effects (P4-2) | the gate |

---

## 3. The stages

Seven stages, in dependency order. Each is one or more PRs. Every step is
behaviour-preserving except the ones marked **changes behaviour**, which get
their own PR and a census run.

### Stage 0 — make the work cheap and safe (small, do first)

Everything later is verified by running the suite hundreds of times, so this
pays for itself inside Stage 2.

- **Stop the suite leaking windows.** Onboarding window tests end with
  `close()` and never `deleteLater`. Before `TestMonospaceSurvivesTheStylesheet`
  there are **64,144 live widgets and 136 MainWindows**, and every
  `theme.apply()` re-polishes all of them. That is why one test takes 65.8 s
  and five restyle tests take 74 s in the file but 1.4 s alone. Share
  `test_ui._flush_deletes` and put deleteLater + flush into every window
  teardown. Estimated suite time goes from 265 s to about 150 s. Worth
  checking whether this is also the cause of the old P0-1 timer flake.
- **One `tests/support.py`.** Test files currently import helpers from each
  other (`from test_truth import _Folders` in 5 files, `from test_import
  import SessionCase` in 4, and more), and several helpers are copied instead:
  `_reverse` 5 times, temporary mods-folder bases about 12 times, zip builders
  7, fake mod.io servers 4, and the MainWindow harness about 35 times. Stages 2
  and 3 move code the tests patch, so a shared harness first makes those moves
  a one-place edit.
- **A gate.** One command that runs ruff, mypy on `core/`, and the suite, and
  records the baseline (ruff 132, mypy 169). Stages must not make it worse,
  and Stage 2 should bring mypy back under Pass 2's 32. Local only while the
  repository is private (decision 5.4).
- **Fix B1 and B2 now, with regression tests.** They are small and
  user-visible, and pinning them with tests before Stage 2 dissolves their
  sites means the consolidation can't quietly reintroduce them.
- **Fix the README's wrong directions** (the File › Settings submenu that no
  longer exists, "Check ›" menus, "Generate tab", "Import from game files",
  and the rest in A.6). Users are being misdirected today, and the fix takes
  about half an hour. The rest of the docs wait for Stage 6.

### Stage 1 — the map, and the renames (small)

- `docs/architecture.md`: the table in section 2, the layering (core has no
  Qt; which core modules are leaves), and the Studio's nouns, each defined
  once.
- The renames settled in decision 5.1, one commit each, before anything moves:
  every later diff then reads in the new words. None touches the save format.
- Output: one short document you approve. Stages 2 and 3 are measured against
  it.

### Stage 2 — consolidate `core/` bottom-up (large)

Leaves first, so each step only changes callers that already exist.

1. **Small shared utilities.** JSON read/write with an atomic write (project
   save, `library/live.json` written in five places, the game-owned profile,
   pointer and options files). Containment (`_inside` ×3, `_within`),
   `same_file` ×2, `read_cached` ×2, `free_space` ×2, and version comparison
   into `versioning`, which cuts the versioning → updates → modops edge.
   Retire `references.py` into `refs.py`.
2. **Game facts** into `games.py`, and paths into `places.py`. The Documents
   path is currently built in 4 places, the Ubisoft registry walk in 2, and
   the LOCALAPPDATA fallback in 4.
3. **Lift the mod-folder layer out of `modops`.** Move `read_metadata`,
   `bundled_in`, `mod_folders`, the modinfo/jsonc lookup, the mod key and the
   origin parsing into a new module, so `modops` is about ModOps again. That
   means `imya._bundled` (a self-declared copy) goes, and the import cycle
   shrinks; measure it before and after.
4. **One inventory, one "which copy loads".** **Changes behaviour.** Decide
   the tie-break (decision 5.2), make truth, installplan and modops ask the
   same function, and turn `mods_on_this_pc`, `loadout.read`'s listing and
   `convert.survey` into views of the one snapshot. Oracle: a census run from
   the frozen copy, before and after.
5. **Log parsing** into `loaderlog`. Include-following written once (it is in
   `modops` and in `discovery._read_with_includes`).
6. **Play into core.** Build options become a value saved with the project
   (header, comments, grouping), not the Export tab's checkboxes; *Write
   modinfo.json* goes (decision 5.3). Merge `_write_out` and `build_into`,
   and have the three download→verify→install copies call `installplan`.
   **Changes behaviour:** the options now survive a restart, and a modinfo
   is always written.
7. **Clean-up.** Promote or stop importing private helpers across modules
   (`modops._strip_comments`, `_read_patch`, `generator._payload_guid`,
   `library._write_links`, `installplan._facts`, and others). Delete the dead
   code from section 1.

Exit: `modops` is only imported for ModOps; no `_private` imports cross
module boundaries; mypy on `core/` is back to 32 or fewer.

### Stage 3 — cut `MainWindow` along the new seams (large)

1. **One resolver for places** on the window: mods folder, library dir and
   game, with everything else asking it. This dissolves B4 and B2's class of
   bug.
2. **Move the 35 `ask_*`/`confirm_*`/`report_*` hooks onto a `Prompts`
   object** that tests replace. Tests currently patch them on the window
   (`tests/test_library.py:259,477`), which is the main thing standing in the
   way of extraction.
3. **Extract controllers**, in order of independence:
   - Preferences (the interface `settings_dialog` already uses)
   - mod.io (get, updates, collections, account)
   - the loader check
   - library and conversion
   - live and Play (now thin, after 2.6)
   - project files (path, dirty, autosave, recents, rename, fork)

   Target: the window keeps construction, tabs, navigation and wiring, in
   about 2,500 lines.
4. **Shared UI helpers.**
   - a `busy_cursor()` context manager (29 hand-rolled blocks, two of which
     can leave the cursor stuck)
   - `open_folder()` ×5
   - list formatting ("• and N more" ×13)
   - size formatting ×3
   - the game-is-running guard (4 copies; possibly missing from
     `put_library_back`, `take_collection_out` and `manage_own_folder`)
   - one progress-worker dialog base (install_plan and modio_get are near
     copies, and only one has the 32-bit overflow fix)
5. **Small tidy-ups.**
   - make the tab gate the only thing that opens tabs (P4-2)
   - declare lazy attributes in `__init__`
   - set `_dirty` through one method, not 43 assignments
   - fix `_save_xml`'s unguarded write

Verify each extraction: the suite passes, collected test IDs are unchanged
where only classes moved, and a real launch (detached) looks right.

### Stage 4 — performance, measured (medium)

This comes after consolidation because the fixes depend on it: after 2.4
there is one snapshot to cache and invalidate, where today there are five
listings behind 27 `refresh_truth` callers.

- **First, a benchmark script** against a large mods folder (a copy of the
  census one). It times startup, `refresh_truth`, opening an asset, an edit
  through to the changes panel refresh, and a Play click. None of the items
  below has been timed yet; all were found by reading.
- **Candidates**, most likely user-visible first:
  - Every mods-folder check walks the whole folder, including two `rglob`s
    per mod, on the UI thread. One Play click does it 3–4 times. Fix: cache
    the snapshot, invalidate it on explicit events (Play, install, convert,
    a folder watcher), and move it off the UI thread.
  - `Workspace._sync` fingerprints every change on each `asset_row`/
    `creator_for` call. The editor calls it per leaf row, and the reference
    graph per node. Fix: a generation counter on `ModProject`.
  - Each edit rebuilds the whole changes tree and recomputes dependencies,
    scripts clashes (byte-comparing script files), repeats and infotips.
    `conflicts.find` (quadratic within an asset) runs twice, and again on
    every filter keystroke.
  - Startup hashes `assets.xml` three times, stats every `.rda` six or more
    times, and runs `xmltest.exe` synchronously (20 s timeout).
  - The Export tab re-verifies and rebuilds the XML up to three times per
    edit while it is shown.
  - Caches that never shrink: `loadercheck._READ_CACHE`,
    `Workspace._live_cache`, `index._matched`.

### Stage 5 — the tests as subject (medium)

- **Move tests to the new seams.** Once Play, the inventory and the places
  resolver are in core, many window-level onboarding and library tests can
  become core tests. That makes them faster, and clearer about what they
  pin. This is the largest remaining lever on suite time after Stage 0.
- **Prune** (conservative; each needs a look first):
  - `hasattr` checks that removed attributes are gone (`tests/test_ui.py:481`,
    `:2474`)
  - two identical `_RADIO`/`_CHECK` tests (`tests/test_widgets.py:218,251`)
  - category tuples compared against a copy of the constant
    (`tests/test_games.py:308,314`)
  - the source-grepping lint posing as a test (`tests/test_widgets.py:~175`)
  - `tools/modio_probe.py` and its tests (decision 5.5)
- **Split `TestRulesAsAThingOfTheirOwn`** (51 tests; natural seams at
  `tests/test_import.py:3531` and `:3653`).
- **Require the real game and loader** (decision 5.5). The tests that read an
  installed game or run `xmltest` fail, naming what is missing, where today
  they skip. `test_rda.TestRealInstall` scans the install once rather than
  twice.
- **Fix `_isolation.py`** so an externally set `ANNO_STUDIO_SETTINGS` does not
  silently switch off the per-test reset. Today that produces 7 false
  failures.

### Stage 6 — documentation, rebuilt around the audiences (medium)

- **Split the README by reader.** About 40–45% of it is how-to. About 35% is
  rationale and census measurement, and about 24% is framed as history ("used
  to" appears on 65 lines). Proposed shape:
  - README: the user guide
  - `docs/architecture.md` (from Stage 1): for someone changing the code
  - `docs/design-notes.md`: the *why*, which is valuable and should not be
    lost
  - history: git, REVIEW.md and the proposals, not the guide
- **Cover what is missing:** the German UI and language setting (not
  mentioned at all), the consolidated Settings dialog, and the install-plan
  dialog. Stop hard-coding the test count.
- **Proposals get a status line that is true.**
  - `loadouts-proposal.md` is superseded by playing and profiles on
    `exclusive`, per-project profiles and switching by `-`; say so at the top.
  - `modloader-gaps-proposal.md` says both "nothing is built yet" (§8, §11)
    and "Built".
  - `collections-proposal.md` names `core/collections.py`, which was built as
    `installplan.py`, and a menu item that is gone.
- **Stale module docstrings:** `ui/source_panel.py:18`, `core/modops.py:22`,
  `core/convert.py:30`, `core/changes.py:3`, and probably `core/loadout.py`.
- **REVIEW.md:** add Pass 8 for this work, and close findings that are stale
  (P5-2 was split; P5-3's zlib and XPath branches are covered; P7-42 is
  superseded; P7-20 is partly addressed). Refresh drifted anchors, and carry
  the still-open ones (P1-4, P2-3, P3-3, P3-4, P4-2, P7-26) into the stage
  that touches their file.

---

## 4. How to run it

- **Order and parallelism.** Stage 0 blocks everything. Stage 1 is a document
  and a conversation. Stage 2's steps are sequential, and so are Stage 3's,
  but Stage 3.4 (UI helpers) and Stage 6's README split can run alongside
  Stage 2. Stage 4 starts with its benchmark script, which can be written any
  time.
- **Coordinate with feature work.** Stage 3 rewrites the file every feature
  branch touches, so `main_window.py` is frozen for features from Stage 2.6
  until Stage 3 ends (decision 5.6).
- **Verification per PR:**
  - the suite passes
  - the gate is no worse
  - where tests only move, `pytest --collect-only -q` IDs are unchanged
    except the class segment
  - for the steps that change behaviour, a census run from the frozen copy
    with the ledger compared
- **Record it** in REVIEW.md as Pass 8, with the same ID and severity scheme,
  so the reasoning outlives the PRs.
- **Relative size.** Stages 0, 1 and 6-quick-fixes are small (a day or less
  between them). Stages 2 and 3 are the bulk, each several PRs. Stages 4, 5
  and 6 are medium.

---

## 5. Decisions

All settled on 29 Sep.

1. **The words.** Where the user sees a word, the code uses the same word;
   a type only the code sees is named for what it is. No rename touches the
   save format.

   | Now | Becomes | Because |
   |---|---|---|
   | `changes.Profile` | stays | The user's profile: named switches over one loadout's mods. |
   | `loadout.Profile` | `ProfileFile` | Anno 117's `mods/profiles/<name>.txt`; Play writes a profile into one. |
   | `convert.FoundProfile` | stays | A profile another tool keeps, found during conversion. |
   | `games.GameProfile` | `Game` | Not a profile: the facts about one game. |
   | `installplan.InstalledMod` | `FolderMod` | One mod in a mods folder, on or off — including a library mod switched off by taking its link away, counted where the link was. Not "installed" (the library holds more than any folder shows) and not "active" (half of them are off). |
   | `modio_cache.Installed` | `CachedMod` | An entry in the in-game client's own records. |
   | `modzip.Installed` | `InstallResult` | What unpacking a zip did. |
   | `library.Installed` | `LibraryInstallResult` | The same, into the library, with its links. |
   | `core/loadout.py` | `core/switches.py` | The game's on-disk switches, load order and log; nothing in it is about a loadout. `ModProject.loadout` keeps the name. |
   | "snapshot" in code | "checkpoint" | What the UI has always called it. The file's key is `history` either way. |

   "Collection" needs nothing: it always means a mod.io collection, and
   `project.listed` is which collections list a mod.

   With `FolderMod` there are three words for three questions, and each is
   kept to its own: a mod is *installed* when it is on this machine at all
   (the library or a mods folder), *in the mods folder* when that folder's
   switches reach it, and *on* when they are switched on.
2. **Which copy of a ModID loads (B3).** The copy the loader's log names,
   then the highest version. How a copy got there (by hand, the mod.io
   cache, bundled in a host) plays no part. Where neither decides, the
   order the loader walks folders does; Stage 2.4 settles that against the
   census rather than assuming it.
3. **The Export options.** Play honours them, and they become project
   settings instead of widgets that reset to all-on at every launch.
   *Write modinfo.json* is removed: it dates from when an export replaced an
   author's modinfo with a generated one, and since `games._over_declared`
   the author's file is the base and only the Studio's six fields are
   written over it. Without a modinfo, Play's mod has no ModID, which Anno
   117's profile, load-after order and the live check all key on. Left are
   the three that shape only the XML: header, comments, grouping.
4. **CI.** Local only while the repository is private: `tools/check.py`
   before a change goes in.
5. **Tests that need the game and the loader.** Required, not skipped.
   Anyone changing the Studio should have both games and the Anno Modding
   Tools extension installed, and a test that cannot find them fails and
   says what is missing. `tools/modio_probe.py` and its twelve tests go; its
   findings are in `modio-integration-proposal.md`. The census harness's
   tests stay, since Stage 2.4 depends on it.
6. **Feature freeze on `main_window.py`.** Frozen from the first Stage 2
   step that moves code out of it (2.6, Play) until Stage 3 ends.
7. **README history.** Moved to `docs/design-notes.md`, not dropped.

---

## Appendix A — findings, by kind

### A.1 Bugs and likely bugs
B1–B4 above. Also:
- `_save_xml` writes without an `OSError` guard and records an "Exported"
  checkpoint (`ui/output_panel.py:2034`, suspect).
- The busy cursor can stick on non-`ModioError` exceptions
  (`ui/modio_account_dialog.py:225`, `ui/output_panel.py:931`, suspect).
- The game-running guard may be missing in three places (suspect).
- The in-code join `loaderlog._named` uses a raw `", "`, which is an i18n gap
  (`core/loaderlog.py:599`).

### A.2 Duplicated logic (core)
- Modinfo-lookup wrappers ×5: `library.mod_id_of:499`,
  `installplan._modinfo:219` (imported privately by `loadercheck:1129`),
  `xmltest.option_values:276`, `updates.cache_index:121`, `imya._bundled:282`.
- `english()` at `modops:4914` and `games._english:370`.
- The mod key `(mod_id or folder).lower()` at `loadout:126`, `:586`,
  `dependencies:333`, `:337` and `truth:534`.
- `texts.read_cached:179` is the same as `infotips.read_cached:120`, and
  `texts:112` and `infotips:71` share a parse prelude.
- `convert.free_space:293` and `installplan.free_space:650`.
- Containment helpers: `library._inside:119`, `updates._inside:167`,
  `dependencies._inside:392` and `discovery._within:571`.
- `library.same_file:274` and `launch._normal:125`.
- `loaderlog._named:599` and `sidefiles._listed:133`.
- GUID-in-payload extraction ×4: `generator._payload_guid:238`,
  `repeats._guid_of:135`, and regexes at `loadercheck:417` and `xmltest:679`.
- `loadout.pointed_at:859` repeats `active_profile:212`.

### A.3 Business logic in the UI
- Play: `main_window.py:3281-3736`.
- The conversion record is parsed by hand at `:5722-5736`, duplicating
  `convert.py:641/666/718`.
- `_mentioned_keys` parses project JSON (`:6131`), and `_project_game` reads
  the game with a regex (`:7278`), as `folding.py:167` also does.
- Layer-folder remapping ×4: `:3598`, `:5418`, `:5748`, `:5682`.
- Mods-folder read folding at `:2727-2752`.
- Documents mods path built by hand at `:6257` and `:6915`.

### A.4 Performance (all found by reading; none timed)
See Stage 4. Anchors:
- `core/truth.py:538` → `installplan.inventory:113` →
  `modops.bundled_in:5354`
- `core/workspace.py:299-356`
- `ui/changes_dock.py:583-626`, `:1531`
- `core/conflicts.py:133-143`
- startup: `ui/main_window.py:1808-1829`, `core/gamefiles.py:241`,
  `core/xmltest.py:179`
- `ui/output_panel.py:1313-1444`

### A.5 Tests
- Timing:
  - 265 s for 2,789 tests
  - `test_onboarding` 123 s for 228 tests
  - the slowest single test, 65.8 s, is a victim of the window leak
- Duplicated helpers and cross-imports: listed in Stage 0.
- Prune candidates: listed in Stage 5.

### A.6 Documentation drift
README:
- L36: View › Text size is now in Settings.
- L192, L1140, L1153, L3674, L4441: the File › Settings submenu is gone.
- L51: "Import from game files" is now "Load from game files".
- L461: Unload is under File › Mods.
- L769, L779: "Check ›" is now under File › Check the loaded mods.
- L3470: "Previewing" is now "Mod to export".
- L3470, L3610, L3639, L4228: "Generate tab" is now Export.
- L3468 contradicts L342.
- L4624, L4671: the test count.
- L4503-4622: the module tree is missing 20 core modules, 9 ui modules and 5
  tools.

Proposals and docstrings: as in Stage 6.
