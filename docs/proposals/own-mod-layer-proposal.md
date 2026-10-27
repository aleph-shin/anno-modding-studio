# The loadout's own mod as a layer: a proposal

*Status, 24 October 2026: built, all six phases (the record:
`Loadout.own`, `mod()` and `mods()`; asking one way; the modinfo blocks in
`own.declared`; the one-mod view; the formats, loadout 6 and project 2,
with the old names gone; and saying the own mod out loud -- each with what
it left for later said there). The open questions of section 10 stand.
Written against main at #310 with #311 on top (each field says which file
keeps it, and `projects.Project`), which it builds on.*

Every mod in a loadout is a `ModLayer` in `Loadout.layers`, filed under its
origin, except one: the loadout's own mod. Its origin is the empty string, and
what it is -- its name, version, ModID and the rest -- is the top level of
`Loadout` itself. This proposes giving it a `ModLayer` of its own, so that a
mod's record is one shape whichever mod it is, and the code that asks about a
mod asks one way.

It is a refactor: nothing on screen changes, and every phase leaves the suite
as it was.

## 1. What is there now

The own mod's record is spread over the loadout:

| What | Where it is for a loaded mod | Where it is for the own mod |
|---|---|---|
| Its details: name, version, author, description, category, ModID | `layer.name`, `layer.version`, … | `loadout.mod_name`, `loadout.version`, … |
| Its modinfo blocks: scripts, options, difficulty, game setup | `layer.declared`, its modinfo as written | `loadout.scripts`, `loadout.options`, `loadout.difficulty`, `loadout.game_setup` |
| Where its Lua lives | its folder | `loadout.scripts_folder` |
| Its GUID range | none | `loadout.guid_range` |
| Whether it loads after everything | `layer.load_after` | `loadout.own_mod_loads_last`, worked out into `own_load_after()` |
| Why its reading is in doubt | `layer.stale` | `loadout.own_stale` |
| Whether it exists | it is in `layers` | `loadout.own_mod`, or any change or op with origin `""` (`has_own_mod()`) |
| Where it loads | `layer.order` | `_own_rank()`, worked out from its name and category among the others |
| A `ModLayer` to hand to code that wants one | the layer | `own_layer()`, a new one built each time and thrown away |

And `Loadout` does a third job besides being the loadout and the own mod: a
throwaway one-mod loadout that stands for a single mod being written out
(`generator.for_mod`). That is why it carries `patches`, `needs`,
`load_after` and `declared`, which are never saved (#311 declares them
`MEMORY`): they are a `ModLayer`'s fields, copied onto the loadout so that the
code writing a mod can ask the loadout what it is.

## 2. The problem

- **Two ways to get at a mod.** Code that wants "the mod at this origin" first
  asks whether it is the own mod, then reads the layer or the loadout:
  `holder = loadout.layer(origin) if origin else loadout`. The field names
  differ too (`name` and `mod_name`), so the project file's details do as well
  (`projects.name_of`, `own_details()` and `layer_details()`).
- **Parallel state.** Reading marks, rank and existence each have an own-mod
  copy beside the layers' (`own_stale`, `_own_rank`, `own_mod`), and code
  that walks the mods has to remember both.
- **Bugs hide in falsy checks.** `""` is false, so a check written for named
  mods skips the own mod without saying so: `if self.editing and
  self.held(self.editing)` let edits into a held own mod until #310.
- **Places that cannot say it.** The reader names a mod after its folder, and
  no folder is called `""`: reading the own mod from its library build (#310)
  needed a way round in `reader.analyse_stack`.
- **Three jobs in one class.** Of `Loadout`'s 59 fields, 13 are the own mod's
  details and 4 more are the view's; #311 made that visible, since each now
  says which file keeps it.

None of it is wrong today. It is what makes each change to how mods are kept
cost more than it should, and it is where bugs of the #310 kind come from.

## 3. What is proposed

**`Loadout.own`: a `ModLayer` that is the own mod's record**, with origin
`""`, always there (as the own mod's details are always there now), and not
in `layers`. Everything in the right-hand column of section 1 moves onto it:

- its details are the layer's fields: `own.name`, `own.version`, …;
- its modinfo blocks go into `own.declared`, where a loaded mod's are, and
  `scripts.block_of`, `settings.block_of` and `modinfo.setup_of` lose their
  own-mod branch;
- `own.stale` replaces `own_stale`;
- `own.load_after` stays empty, as the own mod declares nothing of the kind
  itself: what it loads after on a loadout (everything) is worked out when
  asked, by `own_load_after()`, as now, and written at export;
- three fields join `ModLayer`, because a mod written here can have them and
  a mod read from a folder does not: `scripts_folder`, `guid_range` and
  `loads_last` (today's `own_mod_loads_last`).

**`Loadout.mod(origin)`**: the record of any mod, `own` for `""` and the
layer otherwise. It replaces the holder switch. `layer(origin)` stays as it
is, the loaded mods only, so `loadout.layer(origin) is None` keeps meaning
"not loaded here".

**`Loadout.mods()`**: every mod's record, the own mod's among them, in load
order. For the code that walks every mod and today adds the own mod by hand.

**The view becomes "a loadout whose own mod is this one"**: `for_mod` sets
`view.own` to a copy of the layer (origin `""`), and the four view-only fields
on `Loadout` go. Writing a mod's modinfo then asks one record, whichever mod
it is (`modinfo.build`).

**What stays as it is:**

- the own mod's origin, `""` (section 4.1);
- the release facts, `modio_mod_id` and `published`, on the loadout: they are
  the facts of the mod the loadout releases, which is the carried-on mod
  where there is one (section 4.5);
- existence, `has_own_mod()`: the record is always there, and whether the own
  mod is *made* stays the flag plus "has a change or op" (section 4.4);
- `builds[""]`, `mod_history[""]`, `projects[""]` and `kept_aside`, keyed by
  origin as they are.

## 4. The choices

### 4.1 The origin stays `""`

A new origin that is never empty (`":own"`, which no Windows folder can be
called) would end the falsy-check bugs at the root. It is left out:

- **`""` is the default of every edit.** `Change.origin`, `Op.origin` and
  `Unread.origin` default to `""`, and 170 of the 445 places that make one
  rely on it (code and tests). Each would have to name the own mod.
- **Files key edits by origin.** Every change and op of the own mod in a
  loadout or project file says `"origin": ""`; a new one is a migration of
  every file, not just the own mod's record.
- **What the falsy checks need is an explicit test, not another value.** One
  name for `""` (`inside.OWN` today) with `is_own(origin)` beside it, and the
  checks that mean "the own mod" saying so (phase 6), get most of the benefit
  at a fraction of the cost.

It can be done later, on its own, once phases 1 to 6 have made every place
that means the own mod say so.

### 4.2 Not in `layers`

The own mod could be one more entry in `layers`. It is not: about 160 places
walk `layers`, and nearly all of them mean "the mods read in from
elsewhere" -- reading folders, the library, switches, the mods folder,
conflicts, collections, profiles, unloading. Putting the own mod among them
would change what each of those does, all at once. Kept apart, `layers` means
what it means now, and `mods()` is there for the few that want every mod.

### 4.3 `layer("")` stays `None`

`mod("")` is the own mod; `layer("")` is not. A great many places use
`loadout.layer(origin) is None` to mean "not loaded", and some of them are
reached with `""`. Making `layer("")` return the own mod would change those
silently.

### 4.4 Always there

The own mod's details exist before the mod is made: a new loadout shows "My
Anno Mod" on the Export tab. So `own` is never `None`, and code never asks.
`has_own_mod()` goes on saying whether it is made.

### 4.5 The release facts stay on the loadout

`modio_mod_id` and `published` are the facts of whichever mod the loadout
releases (`projects.releases_of`). Moving them onto that mod's record is
tidier, but `ModLayer.modio_id` already means something else for every
loaded mod (which mod on mod.io it is, as found), and merging the two is a
question of its own. Left for later.

### 4.6 The modinfo blocks go into `declared`

Scripts, options, difficulty and game setup are modinfo blocks. A loaded mod
keeps them in `layer.declared`, its modinfo as written, and the three
modules that read and set them already do it there for a loaded mod. The own
mod's go there too, and those modules lose a branch each. Two things to
carry over exactly:

- a new mod starts with `modinfo.NEW_MOD_SETUP` (safe to remove), not the
  loader's defaults (`SETUP_DEFAULTS`), and with its difficulty "unchanged";
  `modinfo.setup_of` supplies both for the own mod today, so `own.declared`
  is seeded with them where the own mod is made;
- a loaded mod's `offered` (the options it offers, parsed) is read off its
  folder; the own mod's is worked out from its `Options` block, as
  `own_layer()` does now, whenever that block is set.

## 5. What moves

| Today | Becomes |
|---|---|
| `loadout.mod_name` | `loadout.own.name` |
| `loadout.creator`, `description`, `category`, `version`, `mod_id` | the same names on `loadout.own` |
| `loadout.scripts`, `options`, `difficulty`, `game_setup` | blocks of `loadout.own.declared` (`Scripts`, `Options`, `Difficulty`, `GameSetup`) |
| `loadout.scripts_folder` | `loadout.own.scripts_folder` (new on `ModLayer`) |
| `loadout.guid_range` | `loadout.own.guid_range` (new on `ModLayer`) |
| `loadout.own_mod_loads_last` | `loadout.own.loads_last` (new on `ModLayer`) |
| `loadout.own_stale` | `loadout.own.stale` |
| `loadout.own_layer()` | `loadout.own` |
| `layer = loadout.layer(origin) if origin else …` / `holder = … if origin else loadout` | `loadout.mod(origin)` |
| `loadout.layer_rank("")`, `_own_rank()` | unchanged in what they work out; `mods()` orders by it |
| `Loadout.patches`, `needs`, `load_after`, `declared` (view only) | gone: the view's `own` has them |
| `projects.own_details()` / `layer_details()`, `name_of`'s two keys | one list, `layer_details()`, and one key, `name` |

On `ModLayer`, `owners` declares the three new fields `DETAIL`, so they go to
the project like the rest of a mod's details. On `Loadout`, the `DETAIL`
fields go, and `own` is declared as `layers` is: the loadout file keeps the
record, and `projects.split` takes its `DETAIL` fields out into the project.

## 6. The format change

- **The loadout file**: the own mod's record is one entry, `"own": {…}`,
  shaped as a layer is, instead of keys at the top. Its details are taken out
  into its project as a layer's are, so what stays in the loadout file is
  what stays of any layer: its origin, why its reading is in doubt, whether
  it was edited. `LOADOUT_FORMAT` goes from 5 to 6.
- **The own mod's project**: its details are a layer's, under the same
  names, with the modinfo blocks inside `declared`. `PROJECT_FORMAT` goes
  from 1 to 2. A patch's or a carried-on mod's project is unchanged.
- **Files from before.** 0.1.0 is not tagged yet, and `tests/fixtures/formats/0.1.0`
  has been regenerated as the format moved (most recently in #304). If this
  lands before 0.1.0 ships, the fixtures are made again and nothing else is
  owed (code standards, section 11, applies from 0.1.0 on). If 0.1.0 ships
  first, phase 5 also needs a migration: `Loadout.load` and `Project.read`
  read format 5 and 1 into the new shape, and the 0.1.0 fixtures stay as they
  are and keep passing.

## 7. Where the special cases are

A sweep of `core/`, `ui/` and `tools/` on 23 October, by kind. Counts are of
sites, a few of which do two things; places where `""` means something else
(an empty GUID list, a blank label) are left out.

| Kind | Sites | Most of them in | What the phases do with them |
|---|---|---|---|
| 1. Holder switch: the loadout or a layer, by whether origin is `""` | 59, of which 25 only name the mod | `loadercheck`, `scripts`, `changes`, `generator`, `editor_panel` | phase 2: `mod(origin)`; the 25 name helpers become `loadout.mod(origin).label` |
| 2. Name mapping and own-only details | 34 | `changes`, `output_panel`, `generator`, `keptops` | phase 1 (details), phase 3 (blocks) |
| 3. Existence: `own_mod`, `has_own_mod()`, `""` added to lists of origins | 38 | `changes`, `editor_panel`, `main_window`, `build` | unchanged in meaning; `own_layer()` callers move in phase 2 |
| 4. Load order: `_own_rank`, `own_load_after`, `own_mod_loads_last` | 23, and 13 files that only call `layer_rank` | `changes`, `scripts`, `loadercheck` | `_own_rank` reads `own`; `mods()` for the places that append the own mod last by hand |
| 5. Readings: `own_stale`, the stand-in layer in `snapshot`, `written_here` | 33, 26 of them in `stale` | `stale`, `reader`, `narrow` | phase 2: `own.stale`, and `snapshot` uses `own` |
| 6. Falsy checks and `""` defaults that mean the own mod | 62 | `changes`, `changes_dock`, `output_panel`, `editor_panel`, `inside`, `asset_xml` | phase 6 |
| 7. UI that shows the own mod its own way | 13 areas | `output_panel`, `changes_dock`, `editor_panel`, `loadout_files`, `start_controller` | phase 2, through `mod(origin)`; nothing on screen changes |
| 8. `Loadout` as a one-mod view | 27 | `modinfo`, `generator`, `build` | phase 4 |
| 9. Saving and the formats | 24 | `changes` (`to_dict`, `load`, `name_in_file`), `projects` | phase 5 |
| 10. Other: no switch or profile entry for it, the loadout history leaving it out, undo not tracking its details | 9 areas | `main_window`, `play`, `histories`, `undo` | left as they are: each is right for a mod that is not loaded from anywhere |

What the sweep says about the choices:

- **Keeping it out of `layers` (4.2) is borne out.** Besides the 160 loops,
  about 30 places read `loadout.layers` being empty, or its length, as "no
  other mods are loaded" (the Start cards, the title bar, the library), and
  `Loadout.load` drops a layer whose origin is `""`. Each would change if the
  own mod were in the list; none does as proposed.
- **Two of the falsy checks are conflations**, which phase 6 has to split
  rather than rename: `held.of(loadout, "")` means *every* mod's held edits,
  not the own mod's, as does a blank origin in the held-edits dialog; and in
  the XML view, `turn.origin or ""` turns the base game's tab (origin `None`)
  into the own mod at four places, which is harmless only if typing never
  reaches them on that tab.
- **`generator.for_mod` falls back to the own mod for an origin it does not
  know**, so a mod unloaded under it exports as the own mod. Phase 4 makes
  that a refusal.

Found on the way, not part of this:

- **`builtby.rename` fails in any loadout with a checkpoint of its list.** It
  walks `loadout.history` as if it held a mod's checkpoints (`.changes`,
  `.ops`), which it has not since the two histories (#300), and raises
  `AttributeError`; confirmed against main's code. It also moves a renamed
  mod's builds and project but not its own history (`mod_history`). A fix of
  its own.
- **`build.writes` lists the Lua files from the view's own mod**, which for a
  carried-on mod's export, whose scripts are in its `declared`, may be none.
  Not checked; phase 3 makes the question go away.

## 8. Phases

Each phase is a PR of its own that leaves `tools/check.py` passing, and the
first four change no file format.

1. **The record.** `Loadout.own` and `mod()` and `mods()`, and the three new
   `ModLayer` fields. The own mod's details (`mod_name` to `mod_id`,
   `scripts_folder`, `guid_range`, `own_mod_loads_last`) and `own_stale` move
   onto `own`, and the old names stay as properties that read and write it,
   so every caller works unchanged; `to_dict` and `load` still write and read
   the same top-level keys. Not the four modinfo blocks: `Loadout.options` is
   the own mod's `Options` block, and `ModLayer.options` is something else
   (the settings a mod is read at), so those wait for phase 3. The 92 places
   that build a `Loadout` with the own mod's details as keywords (most of
   them tests) are rewritten to pass `own=` (a small helper makes that
   short). `own_layer()` builds its layer from `own`, with what it offers and
   loads after worked out as now, until phase 2 moves its callers to `own`.
   Since the details are no longer `Loadout` fields, `projects.own_details()`
   names them from `own`'s `DETAIL` fields under the keys the files still use
   (`mod_name` for `name`, `own_mod_loads_last` for `loads_last`) until phase
   5 makes them one. Tested: every field round-trips as before
   (`tests/test_owners.py` included); the format fixtures read unchanged.
2. **Asking one way.** The holder switches and name mappings become
   `mod(origin)` and the layer's names, module by module (core first, then
   ui). `own_stale` callers move to `own.stale`, and the own-mod branches in
   `stale` that build a stand-in layer use `own`. The properties are still
   there for whatever is not moved yet.

   *As built:* the switches that read a detail of the loadout or of a layer
   ask `mod(origin)`, and the name helpers ask `label_of(origin)`, which
   names a mod not loaded here by its origin; each keeps its own words for
   an own mod nobody has named. Where one took an origin not loaded here for
   the own mod, it now says so, as `loadout.mod(origin) or loadout.own`,
   for phase 4 to settle. `stale` keeps every mod's marks on its record.
   Three things wait for a later phase: `own_layer()` stays as the record
   with where it loads and what it offers worked out, which an export, the
   change list's sections, the scripts' owner and a reading of the stack
   each want as a copy (phase 4); `details_of` and `_set_details`, since the
   own mod's details are still named as the files name them (phase 5); and
   `keptops`' own-mod branch, which reads the `Options` block (phase 3).
   The checks that only ask whether a mod is loaded, or where its folder is
   (`loadout.layer(origin) if origin else None`), are left: they are not
   about the own mod, and phase 6 says so.
3. **The blocks into `declared`.** Scripts, options, difficulty and game
   setup move into `own.declared`; `scripts`, `settings` and `modinfo` lose
   their own-mod branches; `NEW_MOD_SETUP` and `offered` are carried over as
   section 4.6 says. The Export tab and the scripts dialog read and set the
   own mod the way they already do a carried-on mod.

   *As built:* the four blocks are in `own.declared` under the modinfo's
   keys, `Loadout.scripts`, `options`, `difficulty` and `game_setup` are
   properties over them, and the files still name them so
   (`projects.OWN_BLOCKS_AS_SAVED`). `scripts.block_of`, `set_block` and
   `folder_of`, `settings.block_of` and `set_block`, and
   `modinfo.setup_of` and `set_setup` ask `mod(origin)` and read
   `declared` for every mod. One departure: `own.declared` is not seeded
   with `NEW_MOD_SETUP` and "unchanged". Each block is held as the field
   held it, an empty one left out, because a file read and written back
   has to come back whole (`tests/test_formats.py`), and a 0.1.0 file's own
   mod says `"difficulty": ""` and `"game_setup": {}`. So `setup_of` keeps
   one rule instead of a branch: what a mod leaves out is what the loader
   takes it to be, and for a mod made here what a new mod starts as.
   `own.offered` follows the `Options` block whenever it is set, and
   `own_layer()` works it out again and gives no `declared`, since an
   export takes a layer with one for a modinfo its author wrote. The export
   of a loadout with every block set, and with none, was compared before
   and after, for both games, the own mod and a carried-on one: the same.
4. **The view.** `generator.for_mod` sets `view.own`; `Loadout.patches`,
   `needs`, `load_after` and `declared` go, and so do the own-mod branch of
   `for_mod` and the attribute it sets that is no field
   (`named_after_folder`, which becomes one on `ModLayer`, declared
   `MEMORY`). `modinfo.build` reads one record. An origin `for_mod` does not
   know is refused rather than taken for the own mod.

   *As built:* `for_mod` builds the mod's record and hands it the view as
   `view.own`: a patch's `patches` and `needs` by ModID and its star, the own
   mod's `load_after` (everything, on a loadout) and its blocks, and a loaded
   mod's modinfo as its author wrote it in `declared`. What phase 3 put in
   `declared` made one more fact necessary: whether that is a modinfo to
   write over or the blocks of a mod the Studio writes whole.
   `ModLayer.written_whole` says it, declared `MEMORY` and always so for
   `Loadout.own`, and `modinfo.as_written` reads it. With a loaded mod's
   `Scripts` now in its view, the two checks that count a mod's Lua as
   something to write (`generator.writes_for_the_game`, `build.writes`) ask
   it too: an export copies Lua in only for a mod written whole, and a
   loaded mod's comes across with what it ships, which answers section 7's
   note on `build.writes`. Unasked, a loaded mod that is only Lua would have
   become one to write back out (`build.writable`). The fallbacks phase 2
   marked as `loadout.mod(origin) or loadout.own` are settled to match the
   refusal: a mod not loaded here is named by its origin, has no ModID, and
   the Export tab shows and writes no details for one gone since it was
   picked. The probe of phase 3, widened to a patch, a mod with no modinfo
   and what an export writes and decides, gives the same on main and here.
   One attribute set on the view that is no field is left:
   `builds.plan`'s `exported.build`.
5. **The format.** `"own"` in the loadout file, the own project's details
   under a layer's names, `LOADOUT_FORMAT` 6 and `PROJECT_FORMAT` 2;
   `projects.split`, `join`, `own_details()` and `name_of` lose their
   own-mod branches; `owners` declarations moved. The fixtures made again,
   or the migration of section 6. The properties of phase 1 go, and with them
   the old names.

   *As built:* a loadout file has `"own"`, written as a layer is, and its
   project takes the own mod's details by `layer_details()`, the one list,
   so `split` and `join` treat it as one more record, under `""`. A
   checkpoint holds every mod's details by a layer's names; what it leaves
   out is the ModID, and a loaded mod's modinfo as read but not the own
   mod's blocks, which are its work (`_kept_out`, by `written_whole`).
   `ONE_PROFILE_FORMAT` goes: a loadout with one profile used to say 2 so
   that older builds would read it, and every loadout now keeps its own mod
   where no build before 6 looks. 0.1.0 is not tagged, so the fixtures are
   made again and no migration is owed; files from before are read all the
   same, by one small translation (`projects.as_layer_details`), since the
   maintainer's own loadouts and projects are in the old shape and a
   loadout opened without its own mod's details would write the defaults
   back over them: a loadout of 5 or older (its top-level keys, into
   `"own"`), a project of 1 (as it is read), and a checkpoint taken in
   either (as it is restored). `name_in_file` writes either shape. The
   properties of phase 1 are gone, and their 375 uses read the record; a
   property that raised was left in their place while the suite ran, so
   that a use the rewrite missed failed instead of setting a stray
   attribute. Importing a mod's details into the loadout set every key of
   its modinfo on the loadout by name, of which only six were the own mod's
   details; it now sets those six on the record. What stays:
   `Loadout.scripts`, `options`, `difficulty` and `game_setup`, phase 3's
   accessors of the own mod's blocks, which name no file's keys now.
6. **Saying "the own mod" out loud.** `inside.OWN` moves to `changes` as
   the one name for `""`, with `is_own(origin)` beside it, and every falsy
   check that means the own mod is written as one (section 7, kind 6). The
   two conflations of section 7 are split: "every mod" becomes `None`, and
   the base game's tab never turns into the own mod. The rest of the falsy
   checks, which mean "no origin given", are left, each now plainly not about
   the own mod.

   *As built:* `changes.OWN` is the one name, and `inside` takes it from
   there. The question is `is_own_mod(origin)`, not `is_own`: `xmlview`
   already has an `is_own`, whether an op names only its own asset, and
   `typedops` imports it. Of 252 places that read an origin as true or
   false, about 70 that meant the own mod now ask `is_own_mod`, or `OWN` where
   a list holds origins. 35 more were `loadout.layer(origin) if origin else
   None`, "a loaded mod's layer, none for the own mod", which is
   `loadout.layer(origin)` itself, since no layer has the origin `""`.
   The two conflations are split: `held.of`, the held-edits dialog and
   `review_held_edits` take None for every mod; the XML view's
   `turn.origin or ""` is gone from its four places, and the base game's
   tab (None) is refused where it was coerced, though typing never reaches
   that tab. The rest are left, each meaning something else: the mods of
   `truth` and the loader's log, where "" is "not in this loadout"; Qt item
   data, where None is "nothing there"; and the fallbacks of a name shown,
   such as `origin or tr("your mod")`. Found on the way: a group of changes
   in the changes panel takes "" for "its changes are not all of one mod"
   (`_one_origin`), the own mod's origin as well. A group never spans two
   mods' sections, so the two never meet; `_origin_of`, which walked past
   every "" to tell them apart, now stops at the first origin a row gives.

Phases 2 and 3 can be split further by module without changing the order.
Phase 6 can run beside 2 to 4, since it touches conditions and not fields.

## 9. Risks

- **A field read in a place nobody moved.** Phase 1's properties keep that
  working through phases 2 to 4; phase 5 removes them, and the suite finds
  what is left (a removed attribute fails loudly).
- **Copies.** A shallow `copy.copy(loadout)` shares `own` with the original.
  One place makes one (`inside.only_kept_by`), and it sets no details; phase
  1 adds a test that a copy's details are its own wherever one is made.
- **Load order.** `_own_rank` goes on working out the own mod's place from its
  name and category; it reads them from `own` instead. `mods()` is new, and
  is tested against `layer_rank` for every mod.
- **The modinfo the Studio writes.** Phase 3 changes where the own mod's
  blocks come from, not what is written: the export of a loadout with every
  block set is compared byte for byte before and after.

## 10. Open questions

1. Should the release facts move onto the released mod's record, with
   `ModLayer.modio_id` and the loadout's `modio_mod_id` made one (section
   4.5)?
2. Is the origin worth changing from `""` once phase 6 is done (section 4.1)?
3. `guid_range` and `loads_last` are only ever set on the own mod. Are they
   fields of a mod, as proposed, or of the loadout?
