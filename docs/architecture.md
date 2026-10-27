# How the Studio is put together

For someone changing the code. The rule the codebase is organised around is
*one question, one owner*: for every question the Studio asks, one module
answers it, and every other module asks that module. Section 2 names the
owners; section 3 where the UI's words are in the code, and the words only
the code uses; section 4 what each module is for.

This is the map Pass 8 (`docs/proposals/consolidation-proposal.md`) worked
toward, and with its stages done it describes the code as it is. Where a
question is still answered in more than one place, the table says where else,
and why.

---

## 1. Layers

```
app.py ─▶ ui/ ─▶ core/        tools/ and tests/ may import either
```

- **`core/` has no Qt.** Nothing under it imports PySide, so everything the
  Studio decides can be run and tested without a window. (Checked 29 Sep:
  none does.)
- **`ui/` asks, `core/` answers.** A dialog gathers what the user wants and
  shows what came back; it does not work out folders, parse loadout files or
  plan what Play does. Stage 2.6 moved Play into `core.play` and Stage 3 cut
  the window into controllers. Two pieces of reading are still done in
  `ui/`: `library_controller` reads a conversion's record, and
  `loadout_files._loadout_game` reads a loadout's game off the head of its
  file. Which mods other loadouts name moved to `core.filecache` (21 Oct).
- **The leaves stay leaves.** Six core modules import no other core module:
  `disk`, `paths`, `places`, `po`, `schema`, `settings`. `i18n` imports only
  `po`, and `refs`, `rda`, `modio` and `versioning` only `i18n`;
  `sidefiles` imports only `i18n` and `modop`, `modfolder` only `disk`,
  `settings` and `versioning`, and `records` only `disk` and `i18n`.
  `games` needs `places` for each game's folder under Documents, which is why
  that is `games.documents_dir` and not a function of `places`; `games` itself
  imports nothing else of core's but `i18n`. `modop`, what one ModOp is,
  imports only `games`, so that `changes`, which the reader imports, can ask
  it too, and `applying`, what an op's type does to a node, imports only
  `games` and `modop`, so that `effective` can apply ops as the reader does. `tools/standards_check.py` holds
  each of these to that list, and holds the two rules above and the one
  below. New shared helpers belong at this level, so
  anything can use them without joining a cycle -- the Studio's cache folder
  is `places.cache_dir` for that reason, not something of `index`'s.
- **No private names across modules.** A `_helper` another module needs is
  promoted and named for what it does. Stage 2.7 removed the last exceptions;
  the tests may still reach a module's privates to pin them.
- **`modops` is about ModOps as written; the reader is `reader`.** Reading
  a mod folder is `modfolder`'s (2.3, which took `modops` from twelve
  importers). `modops` finds a mod's patch files, reads its ops out of them
  and says what each is. `reader` runs them against a document as the loader
  does, and `recording` writes down what that comes to as changes. Everything
  else reads ops through `reader.read_ops` and `reader.read_one`, never by
  setting up a `Translator` of its own. There is still a 3-module import
  cycle, `generator, keptops, reader`: the reader and the writer of ModOps
  asking each other. `reader` checks a reading against how `generator`
  orders and writes it, `generator` asks `keptops` what each kept op writes,
  and `keptops` reads with `reader`. It exists only through imports made
  inside functions; module-level imports alone have none. It was fourteen
  modules until writing a modinfo left `games` for `modinfo` and the cache
  folder left `index` for `places` (P8-25), and four until the reader left
  `modops` and `modops` stopped asking `generator` how an op is switched off.

---

## 2. One question, one owner

| Question | Owner | Also answered today in (removed by) |
|---|---|---|
| What is in this mod folder: modinfo, ModID, version, dependencies, bundled mods | `modfolder` | — |
| Is this origin bundled, and what is its host | `modfolder.is_bundled`, `host_of`, `cache_id`, `folder_name` | — |
| What one mod is known by: its ModID, else its folder's bare name; and every name a loadout's mod is matched to an installed one by | `modfolder.mod_key`; `modfolder.match_keys` | — |
| Which loadouts have a mod, and which mods any loadout names | `filecache.loadouts_naming` and `filecache.mentioned_keys`, over the loadout files, and which of them are sets of mods to play (`filecache.loadouts_to_play`); with the window's own loadout as it stands, saved or not, `LibraryController.mentioned_keys` | — |
| Which project keeps a mod of yours, by its ModID, which is your patch of a mod, and which loadouts have that project | `filecache.project_keeping`, `filecache.patch_project_for` and `filecache.loadouts_with_project`, over the project and loadout files; making a patch asks the second and takes the project up (`PatchesController.joined`, `readback.join_patch`); *Open one of yours…*, a mod of yours read in to change and one made a project in *Currently editing* ask them before a second project is made, and take the project up read back (`PatchesController._kept_already`, `_take_up`, `readback.join`); and a loadout whose project is not where it was finds it again by them as it is opened (`Loadout.load`) | — |
| What a project another loadout changed comes to in this one | `readback`: its ModOps read where the mod stands here, in the background through `stale` (`stale.follow_projects`, on opening and before Play), and its hold let go of | — |
| Which mods are in the mods folders, and where | `installplan.inventory`, a list of `FolderMod` | `switches.read` takes the folders its caller has read -- Play's inventory, the mods-folder dialog's reading -- and lists them itself only for a caller that has read nothing (P8-29). `discovery.mods_on_this_pc` asks a wider, cheaper question on purpose -- every mod on the PC, library copies in no mods folder too, in 22 ms where the inventory takes 0.6 s -- for the new-asset dialog's GUID check. The inventory is not cached across actions, on purpose: the folder is the user's too (P8-71). `convert.survey` is a view already, through `truth` |
| Which of a folder's mods are on | `switches` | — |
| Which copy of a ModID loads | `modfolder.loading_copy`: the copy the loader's log names; else the loader's own rule -- the highest version as it reads versions, and between equals the last copy it comes to, walking the mod.io cache first | — |
| What will load, beside what the loadout says | `truth` | — |
| What a mod's zip holds, and which library copy it would replace | `modzip.peek` for the zip; `library.arriving` for the copy: the one with its ModID, else the one in its folder | — |
| Which project built a library copy, and whether a build may write into it | `library.record_built` and `library.built_by`, in the registry beside `source: built`; `builtby.place` decides for every build into a mods folder (`build.write`), and `builtby.stopped` for Play's question (`play.plan`) | — |
| How two versions compare | `versioning.key`; whether one is newer than, or enough for, another, with `1.0` and `1.0.0` one version, `versioning.trimmed`; for which copy loads, the loader's own reading, `versioning.as_the_loader_reads` | — |
| Facts about a game | `games.Game` | — |
| Where things are on disk: Documents, LOCALAPPDATA, each game's own folder | `places`; a game's folder under Documents, `games.documents_dir` | — |
| Which folders the Studio uses: the library, the mods folder | `library.root` and `switches` in core; for the window, its panels and the dialogs it opens, `ui.where`; every one of them somebody may look for, as *Help › Studio folders* lists them, `ui.where.Where.places` | — |
| Whether a path in a loadout or project file may be used | as the file is read: a path on another machine or a device's, `disk.remote_or_device`; a project's, `projects.trusted_path`; a mod's name, `changes.is_folder_path`. As the window takes the loadout up: its mods folder, `ui.where.Where.knows_mods_folder`, asked about otherwise (`MainWindow.check_named_folders`); each mod's folder, `changes.keep_folders_inside` over `ui.where.Where.folder_roots` | — |
| Which version the Studio is | `anno_modding_studio.__version__`, which `pyproject.toml` reads; how it reads mods, as a number of its own, `reader.READER_VERSION` | — |
| Which version of the Studio has used its folders, and whether this one may start over them | `dataversion`, over each of `places.data_folders`; asked as the Studio starts, by `ui.one_studio.may_use_data`; the version before an update, `dataversion.last_used_by`, asked before it | — |
| Whether a newer Studio is out | `studio_release.check`, against GitHub's `releases/latest`; when it is asked, and the note that says so, `ui.studio_release_controller`. A mod's newer version on mod.io is `updates`' | — |
| Whether one of the Studio's records may be written back, and what it holds | `records.read` and `records.write`, under each owner's format: `library.RECORD_FORMAT`, `convert.JOURNAL_FORMAT`, `saving.JOURNAL_FORMAT`, `gameversion.REMEMBERED_FORMAT`; `library.check_records` before anything moves into the library | — |
| What the loader logged: which mods, in what order, from where | `loaderlog` | — |
| Reading and writing the Studio's JSON state, and the game's switch files | `disk`: written whole or not at all, read back as a default when missing or wrong | — |
| Saving several files at once, all of them or none, and settling a save the last Studio left half done | `saving.write_all`, through a journal in the Studio's own folder, which can take a file away as well as write one; `saving.recover`, asked as the Studio starts by `ui.one_studio.finish_saves`. A loadout's save goes through it | — |
| Writing a loadout's mods out, and how | `build`, with the loadout's `build_options` | — |
| Play: what to do, and doing it | `play`; the window asks and reports | — |
| Download, verify, install | `installplan.fetch_and_install` | — |
| Does this value name a GUID | `refs` | — |
| Is this value in this field shown as a link: the editor's Refers-to column, an edited asset's walk, the shipped file's index | `refs.is_link`; for the index's whole fields, `refs.least_inferred_guid` | — |
| Which numbers in a pane of XML name an asset, and which | `guidnames.mentions` finds them and the field each is in; `guidnames.Namer` asks `refs.is_link` | — |
| Which mods reach one asset, what each writes for it and leaves it as | `xmlview.turns`; which ModOps the Studio could not read are aimed at it, `xmlview.unread_reaching` | — |
| Whether a mod's reading still stands, after a change in what loads before it, or in the Studio that read it | `stale.check`, and `stale.reached` for which of its ops | — |
| Which mods an edit made here changed, and from where in each | `undo.Step.reach`; what that leaves in doubt, `stale.edited` | — |
| Whether a mod is read from its folder or from what the loadout writes for it | `stale.written_here`; a mod's folder read again at other settings, among mods read that way, `stale.read_at` | — |
| Whether the loadout holds a mod other than as its files have it | `edits.has_edits`; for every mod at once, `edits.edited`, which `stale.written_here` asks; whether one op or change is an edit, `edits.is_edit` | — |
| Which ops of a mod reach an asset and are not its own | `xmlview.beyond_ops` and `xmlview.beyond_unread`; how far what was typed in the pane reaches, before it is read, `typedops.reach`, and where each op typed there goes, `typedops.goes` | — |
| What typed ModOps come to, and what of a mod moves to match them | `typedops.read` and `typedops.apply`; for one op that is no one asset's, a pattern, `typedops.read_one` and `typedops.store_one`; which of a mod's ops are one asset's own, `xmlview.is_own` | — |
| What a mod's file is as text, which op is on which lines, and what of the mod moves to match text typed there | `modfile.shown` and `modfile.apply`; the lines themselves, `generator.written` | — |
| What of a file's layout is kept: the comments that are no op's note, and the blank lines | `modops._note_above` and `modops._stands_alone` say which comment is which; `Op.aside` and `Op.gap`, set by the reader where a file is read as a file (`parse_ops(asides=True)`), written by `generator.written` | — |
| Which lines of a mod's file as shown are each of its other files, and reading such a text a file at a time | `Shown.regions` from `generator.written(files=INLINE)`; `modfile._read`, and `modfile.regions_in` where nobody held the text while it was typed in; the pane's hold on them, `FilePane.regions` | — |
| Which of a mod's files an op is in, and whether the mod can be written as its files | `Op.file` and `Op.includes`, set by the reader from `modops._as_a_file`; `generator._laid_in_files`, asked by `generator.written(files=True)`, which only an export does (`build.as_files`) | — |
| How one of a mod's ops is read again after the import: at which settings, beside which mods, and what its searches are asked of | `keptops.reading_of` and `keptops.searching`, for a pattern put to an asset that has arrived (`keptops.expand`) and for typed ModOps alike (`typedops.read_as`) | — |
| In what order one op's changes to an asset are applied | `changes.emit_order`: the writer writes a block in it, the reader replays in it to check a reading, and `effective.build` applies in it for the asset shown | — |
| Which tabs are open | the tab gate (`MainWindow.open_tab`) | — |
| Whether the graph follows the editor, and the editor the graph | `MainWindow._on_editor_opened` and `_on_graph_moved`: now, on arrival or not at all, by whether each is showing and whether the graph is pinned (`ReferencePanel.pinned`); whether the pin is offered and the graph has a history of its own, `MainWindow._graph_and_editor_apart` | — |
| Whether a panel is in view, and putting one in front of whoever asked | `MainWindow.showing`, the tab in front or a panel in a window of its own, and `MainWindow.open_tab`; which panels are out, and where, `ui.panel_windows` | `MainWindow.mark_output_dirty` and `_on_tab_changed` ask which tab is in front, of Start and Export, which cannot be out; `FilePanel.mark_stale` asks whether it is hidden itself, which is the same thing for a page of the tabs and for a panel in a window of its own; `MainWindow._find_in_asset` asks where the keyboard is |

---

## 3. The words

Where the user sees a word, the code uses the same word; a type only the code
sees is named for what it is (decision 5.1). Each word means one thing.

What the words on the screen mean is written once, in the
[glossary](standards/design-standards.md#14-glossary), and not again here.
This section says where each of them is in the code, and defines the words
only the code and these documents use.

### The glossary's words, in the code

| Word | In the code |
|---|---|
| **ModOp** | Read by `modops` and `reader`, written by `generator`. How each game writes one is its `games.Game`'s to say. |
| **mod**, **ModID** | `modfolder` reads one mod's folder: a `modinfo.json`, a `data` tree, or both. The ModID is declared in the modinfo. |
| **mods folder** | Each game has several: the one in its install (Anno 117 also reads `mods/.ubi` there), the one under Documents, and mod.io's download cache. |
| **load order** | `switches` works it out from the loader's rules. |
| **game files** | Extracted from the `.rda` archives by `gamefiles` and `rda`, and indexed by `index`. |
| **loadout** | `changes.Loadout`, saved as one `.annoload` file: what the Studio opens. Its **aim** says what it was set up for: *make* a mod of your own, *tweak* somebody else's for yourself, or *manage* a set of mods to play. `Loadout.own` is its own mod's record, a `ModLayer` with origin `""` kept apart from the loaded mods in `Loadout.layers`; `Loadout.mod(origin)` is any mod's. Its `loads_last`, which the third card sets, says that its own mod is the patch over its whole list. |
| **your mod** | **The mod** is the one a loadout writes, when it writes one. Its origin is `""`. |
| **project** | `projects`, one `.annoproj` file per mod of yours, in `places.projects_dir()`: the loadout's own mod, a patch, and a mod of yours it carries on with (`Loadout.project_origins`). The window still holds one `Loadout`: `Loadout.save` splits each project's part out of what it writes (`projects.split`) and `Loadout.load` joins it back (`projects.join`). `Loadout.projects` holds a `projects.Ref` for each, with the revision the loadout read. A project's file is named for its mod and moves when the mod is renamed (`projects.path_for`), with every loadout that refers to it pointed there in the same save (`projects.refer`, `changes._followed_in`). |
| **author** | A mod's `Creator`: `Loadout.creator` for your mod, `ModLayer.creator` for a loaded one. The code keeps the modinfo's name for the field. `patching.is_mine` compares it with yours. |
| **kept op** | `changes.Op`: a ModOp held as written, beside the changes it currently comes to. A **pattern** is one whose target is a search (`//Values/Trader`); `keptops` asks it again. |
| **patch** | `patching`. |
| **fork** | A loadout's: `loadout_files.LoadoutFiles.fork_loadout` makes one, as a file of its own or a profile of it. |
| **your own copy** | `forking`. The code calls it a fork, and the word stays there: a mod's own copy is `ModLayer.fork_of`, `forking.make`. |
| **profile** | `changes.Profile`. A loadout has at least one; the chosen one is what its layers' on and off hold. |
| **Play** | `play` makes a loadout live: it writes its switches into the mods folders (links, `-` marks, a profile file) and its mods' options and tweaks, then starts the game. |
| **library** | `library`, one per game. The **link** each mods folder holds is a directory junction, or a symbolic link where there are none (`library.is_link`, `make_link`); switching a library mod off takes its link away. A mod's files are read and written by its own spelling (`disk.as_spelled`). Off Windows, and under Wine, `proton` says when a game Steam runs through Proton cannot see the library; `wine` says when the Wine the Studio runs under makes no junctions. |
| **generated copy** | `generated`: built by `generated.build`, pointed at by `library.generate`. Your edits to other people's mods are kept by a mod of yours (`inside`: your patch for theirs, or the loadout's own mod; `Op.kept_by`, `Change.kept_by`, the fifth item of a `taken_out` entry, a held edit's `kept_by`), which keeps them in its project's `inside` part (`projects.split`, `projects.join`); they are worked out by `edits`, the patch's and then the own mod's, and written into their files by `splicing`; an update that changes an op you edited holds the edit (`held`), decided in `held_dialog`. An export of a patch carries those of its own edits that can be ops of its own (`sharing`, `sharing.view`, over `inside.only_kept_by`), and the Export tab lists the rest. A link to one still stands for the library's copy: ask `library.source_of`, not `library.target_of`, which mod a link is. |
| **collection** | `Loadout.listed` says which collections list each folder's mod. |
| **Install** | The **install result** is what unpacking a mod's zip did (`modzip.InstallResult`), or the same into the library with its links (`library.LibraryInstallResult`). |
| **Export**, **Publish** | `build` and `generator` write the mod folder; `publish` sends it. |

### Words only the code uses

**The game's side**

- **Game** (`games.Game`) — the facts about Anno 1800 or Anno 117: where its
  assets live, how its ModOps are written, what mod.io calls it. Not a
  profile.
- **Asset**, **GUID** — an `<Asset>` in `assets.xml`, and the number the game
  finds it by.
- **Switches** — how the game records on the disk which mods are on: Anno
  1800 by a `-` in front of a folder name, Anno 117 by a **profile file**
  (`switches.ProfileFile`), `mods/profiles/<name>.txt`, with
  `mods/profile.txt` naming the live one. `switches.FolderSwitches` is one
  folder's switches as read.
- **Loader**, **loader log** — the game's mod loader, and what it wrote about
  what it loaded the last time the game started (`loaderlog`). `xmltest.exe`
  is the loader's patching code on the command line, shipped with the *Anno
  Modding Tools* extension.

**Three words for three questions** about a mod on this machine, each kept to
its own:

- **installed** — on this machine at all: in the library or in a mods folder;
- **in the mods folder** — reached by that folder's switches: a
  **`FolderMod`**, on or off, including a library mod whose link was taken
  away, counted where the link was;
- **on** — switched on.

Not "active": half of what a mods folder holds is off.

**The work**

- **Layer** (`changes.ModLayer`) — one mod read into a loadout from elsewhere.
- **Origin** — a layer's identity, carried by every change of that mod: its
  folder name; `<mod.io id>/<folder>` for a mod in the mod.io cache; and
  `<host>/<folder>` for a **bundled** mod, one that lives inside another mod's
  folder, its **host**. A bundled mod switches with its host
  (`Loadout.switch_of`), and a piece several hosts carry loads while any copy
  of it is on (`Loadout.loading_copies`).
- **Change** (`changes.Change`) — one edit to one node, which becomes exactly
  one ModOp. Made here, or read out of a mod.
- **Unread op** — a ModOp the Studio could not read as changes, carried as
  the mod wrote it.
- **Mod options** (`settings`) — what a modinfo declares configurable.
  **Tweaks** (`imya`) — iModYourAnno's options for an Anno 1800 mod.
- **Checkpoint** — a moment of one of the two **histories**, with a note
  (`histories`). A project's (`changes.Checkpoint`) is its mod's work then:
  its changes, ModOps and details, each edit with the loadout it was made
  in, and the edits it keeps inside somebody else's ops (`inside`); taken at every export and publish of the mod, by hand, and around a
  restore, and kept in the project file (`Loadout.mod_history`, a
  project's `history`). A loadout's (`changes.LoadoutCheckpoint`) is its
  list then: which mods, in which order, which on, their settings, its
  profiles, and the revision and checkpoint each project stood at; kept
  in the loadout file (`Loadout.history`). Neither restores build numbers,
  the mod.io link or what was published.
- **Release** — an export or publish checkpoint in the mod's project
  history; "since the last release" is measured from it (`releases`).
- **Version**, **build** — the version is the author's; the build is the
  Studio's count of outputs that differed (`builds`).

**Playing and where mods come from**

- **Live** — the loadout last made live for a game, recorded beside the
  library (`library.live`). At most one per game.
- **mod.io cache** (`modio_cache`) — where Anno 1800's in-game client
  downloads mods. A **`CachedMod`** is one entry in its records.
- **Conversion** (`convert`) — bringing everything the game loads into the
  library, once: the survey, a snapshot of what loads now, the move.
  **`convert.FoundProfile`** is a profile another tool keeps, found on the
  way.
- **Census** — the harness in `tools/modio_*` that reads every Anno mod on
  mod.io, writes it back out, and compares both with the loader.

---

## 4. The modules

Grouped by what they are about. `tools/check.py` fails when a module under
`core/` or `ui/` is missing here.

### The package's own

| Module | For |
|---|---|
| `__main__` | where every start goes: `--reading-process` to the reading's process before Qt is imported, `--self-test` to the self-test, anything else to the window. A built Studio's exe starts here too, through `tools/release/entry.py` |
| `app` | the application set up before any window (`start_application`), and the window's start: the lock, the hand-over to a Studio already open, the data version |
| `self_test` | the Studio checking itself with nobody watching: every module imported, its resources and catalogs found, the main window and the About box built off screen, one reading process answered; run on every release build |
| `__init__` | the version, written once, the repository, and `source_url`: where a document is read on GitHub as this Studio has it, at its release's tag once built |

### core/

**Shared ground** — no core imports, or only `i18n`

| Module | For |
|---|---|
| `i18n` | the Studio's own words in the reader's language; `po` reads and writes the catalogs |
| `keywords` | the terms the Studio uses in one sense only, and what each means: `[[patch]]` in a sentence, kept by `i18n.tr_kw` |
| `disk` | files written whole, JSON read back forgivingly, whether two paths are one or one is inside the other, and a long path spelled so Windows will open it |
| `records` | the Studio's own JSON records, each stamped with its format and the Studio that wrote it, and never written over where it can't be read or a newer Studio wrote it |
| `places` | the machine's folders, Documents and LOCALAPPDATA; where the Studio keeps what it makes, under one home `ANNO_STUDIO_HOME` moves, and Documents itself where `ANNO_STUDIO_DOCUMENTS` names another, as the suite does; what it can make again, the cache, the game's extracted files and the cache over the loadout files (`files_cache`); and the one folder that holds all of it for whoever asks, `one_folder`, which a built Studio takes only from `ANNO_STUDIO_FOLDER` and never from beside itself (`built`, `unused_beside`); the Studio's own folders, each once, `data_folders`; the settings file beside the library, `settings_file`, and the move of a former one from the roaming app data, `adopt_former_settings` |
| `paths` | a node as a ModOp XPath, and checking one still points at it |
| `schema` | what is allowed where, so the editor offers real choices |
| `settings` | a mod's own options, as its modinfo declares them |
| `refs` | which fields are links to another asset: judged by the field's own numbers, or guessed from its name where there are none yet |
| `secrets` | the mod.io key, wrapped for this Windows user |
| `rda` | reading the game's `.rda` archives |
| `po` | `.po` catalogs |

**A game and its files**

| Module | For |
|---|---|
| `games` | `Game`: per-game facts and ModOp dialect |
| `modinfo` | the `modinfo.json` an export writes: each game's shape, over the mod's own file, with the Studio's build block |
| `declaring` | a mod's own options declared here: what stands in the way of writing them, and which of them its ModOps already read |
| `option_groups` | some of a mod's ModOps put under one of its options: each run of them in the mod's file wrapped in `<Group Condition="$name">`, applied as typing in the Mod file tab is |
| `gamefiles` | finding and extracting the few files the Studio needs from an install |
| `gameversion` | which update of the game is installed |
| `discovery` | finding installs and already-extracted asset files |
| `launch` | starting the game, and knowing it is running |
| `index` | `assets.xml` streamed once into a searchable SQLite cache; what each search expression matched in it, kept beside it between launches |
| `workspace` | the index as the mod sees it: base assets plus the ones it creates |
| `effective` | an asset as it will be, with the loadout's ModOps applied |
| `xmlview` | one asset as XML, a mod at a time: the ModOps of each loaded mod that reach it, which of them are its own and which reach beyond it, the asset each leaves, and where a field is in that text |
| `typedops` | ModOps typed for one asset in the XML view, or one pattern typed in its editor: read as an import reads them, and put into the mod as the fewest changes that make it say what was typed |
| `guidnames` | the numbers in some XML, typed half-way or not, that are GUIDs of assets, and their names: what the XML panes say beside a line |
| `moving` | a thing of a mod's file moved, as the Mod file tab's outline drags one: the text with its lines taken out and put in elsewhere, and which lines are what in it |
| `modfile` | one mod's whole file as text for the Mod file tab: the text, which op is on which lines, and text typed there read in order and put into the mod with each op the text still has under the uid it had; and a reading of that text taken in the stack put back onto the ops the mod holds (`take_reading`) |
| `texts`, `infotips` | what mods call things, and which tooltips they patch |
| `guidranges` | the community's GUID ranges, and which one a new asset draws from |

**The work**

| Module | For |
|---|---|
| `changes` | the loadout: changes, kept ops, layers, profiles, checkpoints, and its file |
| `owners` | which file keeps each part of a loadout, declared on the field itself: the loadout, the project of the mod it describes, the project of the mod the loadout releases, each mod's project by entry, the keeper's project for edits inside somebody else's ops, or memory only; `projects` reads what it moves off these |
| `projects` | the project each mod of yours is kept in, apart from the loadouts: its part (its history among it) taken out of a loadout's file as it is saved and put back as it is opened, its revisions, and a project another loadout has changed since held from being written over; its file's name, which follows its mod's, and pointing a loadout file at where it went; and `Project`, one project file read, written and saved on its own, with no loadout open |
| `histories` | the two histories: the loadout's list at each checkpoint, what moved between two, and restoring it, with each project as it was then if asked; a project's restore, with a checkpoint before and after |
| `readback` | a project read back into a loadout: its ModOps as a text of the mod, read where the mod stands, the Studio's own ModOps come back as changes again (`Change.written_as`), and its hold let go of; and the library build read in a missing project's place, the loadout's own mod's included |
| `filecache` | a SQLite cache over the loadout and project files, in the local folder: the game each names and the mods in it, read again when a file's time or size changes, and built again from the files whenever it is deleted or cannot be used. Which loadouts have a mod, and which mods any names |
| `undo` | one undo history for everything a mod would write; each step says which mods it changed and from where, and puts back those mods and no other |
| `narrow` | the ModOps the Studio writes for what is set in Fields: the record of each mod of yours (the loadout's own mod, a patch, a carried-on mod), written for its changes at the end of every step, kept in its project, and what its export writes |
| `modop` | what one ModOp is: its type, how far its target reaches, what its payload and condition are, and what sits beside them |
| `applying` | what an op's type does to each node its path finds, ported from the loader's own `Apply` |
| `modops` | somebody else's ModOps as written: their files, their ops, nested as the loader nests them, and what each one is |
| `reader` | those ops run against a document as the loader runs them, and the changes they come to |
| `recording` | what a reading comes to, written down: the diff, each change keyed to a path that holds, and the payloads still being added to |
| `keptops` | ops kept as written: what each comes to and how it is drawn, and a pattern asked again when a mod adds an asset |
| `stale` | readings a change in what loads, an edit made here, a mod updated on disk, or a project changed by another loadout or missing has left in doubt: which mods and which of their ops, reading them again where they stand (a mod edited here from what the loadout writes for it, unless its folder is what moved; a project's mod from its ModOps), and putting the new readings in place |
| `reading_process` | that reading in a process of its own, both sides: the window's, which starts the process, sends it the request and waits for the answer, and the process's own `main`; the search answers handed over and back, and a reading stopped by ending its process |
| `generator` | changes as ModOp XML, and a mod folder with its modinfo |
| `play` | making a loadout the one the game loads: what that would do, and doing it |
| `build` | writing a loadout's mods out, the same for an export and for Play: its output option, the library or in place, numbered, backed up first |
| `builtby` | which project built each of the library's copies of a mod of yours: one project per library folder, the loadout's own mod renamed moving the folder its project built, a mod of yours given a new ModID moved to a folder of its own beside the old one before a build, and loadouts following a copy that moved, by its ModID and the name it had |
| `adding` | a mod or a patch of yours added to a loadout that is not open, or taken out of one: the loadout's file written whole, referring to the mod's project at a revision it never read, so it reads the mod when opened; a patch's mods brought along; several loadouts saved in one journal; and the one mod switched in the game where that loadout is live |
| `patching` | changing somebody else's mod through a patch of your own |
| `forking` | somebody else's mod made into one of yours: a copy of its files beside the library, a ModID of its own, deprecating the original, swapped in at its place with its settings, and your patch for it folded in on asking |
| `inside` | your edits inside somebody else's ops, and which mod of yours keeps each: your patch for their mod or the loadout's own mod, chosen in *Changes go to*, settled at the end of every step and kept in that mod's project; a patch's set aside while their mod is not loaded, and put back when it is read in again |
| `conflicts`, `repeats` | two mods reaching for the same field; two assets under one GUID |
| `dependencies` | what the mods that will load say about each other |
| `grouping` | a change list grouped into things a player would recognise |
| `scripts`, `sidefiles` | what a mod runs as Lua; what its other patched files do |
| `imya` | iModYourAnno's tweaks for Anno 1800 mods |
| `versioning`, `releases`, `builds` | a mod's version, what changed since the last release, its build number, and whether a mod's folder still holds the copy that was read |
| `backup` | a copy of what the Studio is about to overwrite |
| `saving` | a save of several files that is all or nothing: each written beside itself, a journal, then each swapped in, and every one put back where one fails; a save the last Studio left half done completed or undone as the next starts |
| `dataversion` | which version of the Studio has used its folders, in each folder's `data-version.json`, and whether this one may start over them |
| `studio_release` | whether a newer version of the Studio itself is out, asked of GitHub's releases with nothing but the request; never a download |

**The machine: mods folders, the library, mod.io**

| Module | For |
|---|---|
| `switches` | which mods a folder has on, in each game's notation; the load order the loader's rules give |
| `loaderlog` | what the loader says it loaded, in what order and from where, beside what Play asked for |
| `truth` | what will load now, beside what the loadout says |
| `modfolder` | one mod folder: its modinfo, the mods bundled in it, its name without the off mark |
| `installplan` | the inventory of `FolderMod`s, and a plan to fetch what a list wants |
| `library` | the Studio's library, its links, and the live record |
| `proton`, `wine` | whether a game run through Proton can see the library; whether the Studio runs under Wine, whether that Wine makes junctions, and the Linux paths it gives |
| `packaged` | whether Windows keeps the Studio's own folder in a packaged app's private copy of the local app data, where the game can't see the library; asked as the Studio starts |
| `modzip`, `zipped` | putting a mod's zip into a folder, and what a zip holds before it is unpacked; Anno 1800 mods read out of zips |
| `convert` | bringing everything into the library once |
| `modio`, `modio_cache` | the mod.io client; the in-game client's records |
| `updates` | which mod.io mod each loaded mod is, and whether it has moved on |
| `publish`, `unsubscribe` | sending a new version; letting go of the in-game client's copies |
| `xmltest`, `loadercheck` | the game's own loader asked, as a second opinion |

### ui/

**The window**

| Module | For |
|---|---|
| `main_window` | wires the tabs together over one loadout, and still reads mods into it and unloads them |
| `where` | where the window's game, mods folder and library are: one answer to each; and every place the Studio uses that somebody may look for, `places` |
| `prompts` | every question the window asks you, which tests answer instead of patching the window |
| `navigation` | where you have been |
| `panel_windows` | the panels that are in windows of their own: taking one out of the main window and putting it back, the button and the action that do it, the page a tab keeps while its panel is out, the main window's shortcuts answered there, and which were out, and where, between launches |

**The controllers the window holds**, which do the rest of its work

| Module | For |
|---|---|
| `preferences` | what the window remembers about you, and the Settings dialog |
| `loadout_files` | the loadout as a file: its path and name, saving and autosave, renaming and forking, recents, and what is unsaved |
| `start_controller` | the Start tab and what you are here to do |
| `game_files_controller` | the game's own files: its assets.xml built into an index and handed to every tab, its texts, extracting them from the game, and remembering what was read |
| `patches_controller` | patches over somebody else's mod, the three ways on when one of its ops would be changed in place (your patch, the loadout's own mod; `way_on`), and a mod of your own |
| `patterns_controller` | writing a pattern, and checking the loadout's patterns against the game's files |
| `background` | what is running behind the window, and the corner of the status bar that says so |
| `readings_controller` | stale readings: looking for them after every change and every step of undo, and in every mod's folder when a loadout is opened and before Play, having them read again in a process of their own, taking the answer if it is still the question, and holding readings off the index while it is closed, built again or written to |
| `live_controller` | Play, and which loadout and which of its profiles is live |
| `library_controller` | the conversion and taking it back, managing a folder in place, installing a mod zip dropped on the window, adding library mods to the open loadout, and which library mods no loadout mentions |
| `modio_controller` | everything the window does with mod.io: getting, updating, collections, the account, and leaving the game's own mod browser |
| `loader_controller` | the game's own loader asked as a second opinion: where it is, its probes, a newer extension, the whole-loadout check |
| `studio_release_controller` | whether a newer Studio is out, asked once a day behind the window and from *Help › Check for updates*, and the status bar's note of it, or of the version just updated from |

**The tabs and the panel**

| Module | For |
|---|---|
| `source_panel` | the Start tab |
| `editor_panel`, `browser_panel` | the Asset editor tab: the tree, and the search beside it |
| `asset_xml` | the Asset editor's XML view: a tab for the base game and for each mod that reaches the asset, and finding text in it; its pane, which both tabs use, names beside each line the assets the line's numbers link to |
| `file_panel` | the Mod file tab: one mod's file with a heading drawn over each of its other files and an outline of its ops beside it, whose rows are dragged to move them, typed in and applied where it is the mod changes go to and only shown otherwise; which mod's is the tab's own choice |
| `reference_panel` | the References tab |
| `output_panel` | the Export tab |
| `changes_dock` | the Mod changes panel, beside all of them |
| `change_rows` | the rows of a change list (a ModOp, an asset, a change) and how each is drawn: for the panel, for the pattern editor's rows of the one op being typed, and for the Mod file tab's outline |

**The dialogs**, each named for what it does

| About | Modules |
|---|---|
| Game files | `import_dialog` |
| Editing | `add_field_dialog`, `asset_picker`, `new_asset_dialog`, `pattern_dialog`, `scripts_dialog`, `options_editor_dialog`, `option_group_dialog` |
| Loaded mods | `mod_import_dialog`, `mods_folder_dialog`, `mod_options_dialog`, `unload_dialog`, `unread_dialog`, `conflicts_dialog`, `needs_dialog`, `loadout_check_dialog` |
| Your other loadouts | `add_to_loadout_dialog` (the Export tab's *Add to loadout…*) |
| The library and backups | `convert_dialog`, `backup_dialog` |
| The two histories, the loadout's and a project's | `history_dialog` |
| mod.io | `modio_account_panel` (Settings' mod.io tab), `modio_get_dialog`, `install_plan_dialog`, `modio_updates_dialog`, `modio_publish_dialog`, `unsubscribe_dialog` |
| The Studio itself | `settings_dialog`, `only_copies_dialog`, `shortcuts_dialog`, `about_dialog` (its version, licence and links, and the versions a bug report needs), `folders_dialog` (every folder the Studio uses, in one list) |

**Shared pieces**

| Module | For |
|---|---|
| `widgets` | small controls that behave the same way everywhere |
| `workers` | background threads |
| `modio_task_dialog` | a dialog running mod.io jobs on one of them, with progress and Stop |
| `xml_view` | ModOps XML shown: its colouring, and a dialog holding some to read |
| `language` | which language the Studio speaks, chosen as it starts |
| `one_studio` | one Studio open over one set of its folders, a second launch handing over to the first and told which version it is, and a Studio older than the data it finds starting over none of it; a save the last one left half done settled before anything opens; a built Studio's word about a data folder beside it that it does not use |
| `modio_brand` | mod.io's mark, on every window that talks to mod.io |

**How the Studio looks.** The rules all three follow are in
`docs/standards/design-standards.md`.

| Module | For |
|---|---|
| `theme` | the colour schemes, the drawing styles and the style sheet built from them. The only module with a colour value in it |
| `appearance` | which theme and interface size are in effect, from the settings and from Windows |
| `op_marks` | what a ModOp is, drawn for the Mod changes panel: a badge for its type and a strip of marks for what is worked out at load |
| `help_tip` | the `?` beside a label, a keyword's meaning beside it (`KeywordTips`), and the filter that keeps tooltips from timing out |

### tools/

**The gate.** [development.md](development.md) says how to run it, and the
rules it holds are in `docs/standards/`.

| Tool | For |
|---|---|
| `check.py` | the gate itself: ruff, mypy, vulture, the suite, and the two checks below |
| `format_fixtures.py` | writes a file of every format the Studio keeps into `tests/fixtures/formats/<version>/`, for the release checklist; `tests/test_formats.py` reads each with every later build |
| `design_check.py` | what the design standards rule out in the source |
| `standards_check.py` | what this document, `docs/standards/translation.md` and `docs/development.md` rule out |
| `private_scan.py` | the pre-push hook's scan of the commits a push adds, for the terms on a list kept outside the repository |
| `duplicate_check.py` | the lines of the application and its tools that are in a run written somewhere else as well, by file; `check.py` holds each file's count from growing |

**Translations** (`docs/standards/translation.md`)

| Tool | For |
|---|---|
| `translations.py` | keeps the catalogs in step with the code |
| `untranslated_widgets.py` | lists the English a built window still shows |

**The look**

| Tool | For |
|---|---|
| `render_themes.py` | renders the Studio under every theme for review, and compares the colour schemes with the approved renders in `docs/design-review/` |
| `make_icon.py` | draws the app icon |

**The release** ([development.md](development.md#making-a-release))

| Tool | For |
|---|---|
| `build_release.py` | builds the Windows release: checks it is built from `requirements-release.txt`, freezes the one-folder program from `release/studio.spec`, fails on any Qt module or plugin that must not ship, runs the built Studio's self-test, adds the notices of THIRD-PARTY-NOTICES.md, section 5, and zips it with its checksum; `check-tag` holds a pushed tag to the version. `.github/workflows/release.yml` runs it on a tag |
| `release/` | what the build is made from beside the package: `studio.spec`, PyInstaller's recipe; `entry.py`, the exe's script, which hands over to `__main__`; the zip's `README.txt`; and `licenses/`, the licence texts no installed package carries |
| `fresh_machine.py` | puts a machine back to before the Studio, to test a first run on it: takes away everything the Studio keeps (archived unless told to delete), then leaves the games' mods folders clean or filled with a copy of a set of mods. Standard library only, so it does not depend on the version being tested |

**Measuring against real mods**

| Tool | For |
|---|---|
| `modio_fetch`, `modio_roundtrip`, `modio_loader` | the census |
| `op_counts.py` | counts the ModOps in a folder of mods by which of their target, payload and condition are worked out at load (section 4 of `docs/proposals/ops-as-written-proposal.md`) |
| `generated_check.py` | builds a generated copy of every census mod, with nothing written and with its iModYourAnno settings moved, and compares each with the mod (phase 1 of `docs/proposals/patches-proposal.md`) |
| `bench.py` | times what a user waits for, against a large mods folder built from the census (Stage 4 of `docs/proposals/consolidation-proposal.md`) |
| `make_sample_assets.py` | writes the sample game files |
| `*_probe.py` | each answered one question about a game once, and says which in its docstring |

**Tests that hold the standards.** They are in `tests/`, and listed here
because the tools above are half of the same check.

| Test | For |
|---|---|
| `tests/test_design.py` | the design standards' checks on built widgets and on the colour schemes |
| `tests/design_dialogs.py` | how each dialog is built, for that test |
| `tests/test_accessibility.py` | the keyboard, focus and status mechanisms |
