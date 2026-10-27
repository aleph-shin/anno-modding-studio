# Mod versioning: a proposal

How the Studio could number the mods it builds, with as little typing as
possible from the author, and what it should record alongside the number. This
proposal is based on the Studio's current code: the free-text version field, the
publish checks, the update checker's comparison, the history snapshots, and what
an index read already knows about the game data.

**Recommendation in one paragraph.** Record three things, but only one of them
in the version players see.

- **The public version.** `MAJOR.MINOR.PATCH`, owned by the author. The Studio
  suggests the next one from what changed since the last publish.
- **A build number.** It counts every export or Play build whose output actually
  differs. It lives in the project and in the Studio's own block of the modinfo,
  never in `Version`.
- **The game data the mod was built against.** An exact fingerprint of the
  assets.xml, plus a readable game-update label if one can be found. This is
  recorded as data, not put into the version string.

Build the build number first, since it is mechanical and everything else
leans on it. Build the bump controls second and the suggestion third. The
readable game label waits on an investigation that should start now, alongside
the other work.

**Status.** All five phases are done. Section 1, *what exists today*, is the
code before them.

- **Phase 0.** The game names its update in its own log, so the readable label
  will be built (section 4).
- **Phase 1.** Build numbers, the assets fingerprint, the modinfo block and the
  checkpoints rename are in `core/builds.py`, and the README describes them.
- **Phase 2.** The version controls are built: `core/versioning.py`, *Bump*
  and the build label beside the Version field, the `published` record with a
  *Published* checkpoint, and publish offering the next number instead of
  refusing.
- **Phase 3.** The suggestion is built in `core/releases.py`: the last
  release, the classification with its reasons, the short line under the
  version with *Use*, the question on publishing, and the changelog draft.
- **Phase 4.** The game data is watched: `core/gameversion.py` (the log
  reader, moved out of the probe) names the update, each build remembers the
  data it was made on, and the status bar, the build label and a *Built on*
  column in the mods-folder dialog say when that is not the data loaded now.

---

## 1. What exists today

| | Where | What it does |
|---|---|---|
| The version | `ModProject.version`, `changes.py:700`, default `"1.0"` | Free text, edited on the Export tab (`output_panel.py:171`). It becomes modinfo `Version` and the mod.io file's version. |
| Publish checks | `publish.plan`, `publish.py:76` | Refuses a blank version, the live version, and anything older than live. Nothing is recorded after a publish succeeds. |
| Comparison | `updates.version_key`, `updates.py:106` | Reads the runs of digits as a tuple of integers. Anything else is *different*, not *newer*. |
| History | `Snapshot`, `changes.py:577` | A snapshot is taken on every export as `"Exported v%s"`, and by hand. The UI calls these "versions". |
| Built against | `base_archive`, `base_assets`, `generator.base_description` | Stored as the archive name and asset count, and written only into the header comment. |
| Game data identity | `index.fingerprint`, `index.py:226` | A full SHA-1 of assets.xml. It is used as the cache filename and then thrown away. |
| Game patched? | `gamefiles.read_state`, `gamefiles.py:291` | Detects that the RDAs changed since the last read, by name, size and modified time. |

Three consequences:

- The Studio already computes the exact fingerprint it needs and discards it.
- It already knows when the game has been patched.
- It never remembers what it published.

**Collections are out of scope.** A mod.io collection pins no versions
(`modio.CollectionSummary`); it installs each mod's current file on the day. So
there is nothing to number. A local record of which files a collection resolved
to would be a different feature.

---

## 2. The three things, and where each lives

### 2.1 The public version: `MAJOR.MINOR.PATCH`

This is what goes into modinfo `Version` and onto mod.io, and it is what
players compare. Three numbers, because that is what `version_key` already
orders correctly and what most modders already write.

What each part means has to be in Anno's terms, not a library's:

| Bump | Means | Examples |
|---|---|---|
| Major | Something saves or other mods rely on has broken | An asset this mod created is gone or has a new GUID; the mod now needs a new game; the ModID a patch targets changed |
| Minor | Something new, nothing broken | A new asset; ops on assets the mod did not touch before; changes switched off or removed |
| Patch | The same things, tuned | Only values changed, on assets already touched |

The author owns the number. The Studio suggests (section 3) and never bumps the
public version on its own, with one exception: publishing with an unbumped
version offers the next patch number instead of refusing. The refusal at
`publish.py:100` exists because the one common mistake is forgetting to bump.
Offering the fix is kinder than naming it.

As built, the next number is the patch after whichever of the two is further
on: the tab's version or the live one. So `1.2` against a live `1.10` offers
`1.10.1`, not `1.2.1`, which would still be behind. The question is asked
*before* the export, because the modinfo and header written by the export have
to carry the version that is actually sent. A version with nothing to count on
(`beta` after `beta`) still meets the old refusal. `publish.plan` keeps the
refusal as the check that holds whatever the UI does.

**Versions that are not numbers.** Imported mods carry `v2`, `1.4b`, dates. If
the field does not read as one to three dot-separated numbers, the *Bump* menu
offers no moves. Instead it gives a one-line reason and a *Use numbered versions*
action, which keeps the numbers the version has (`v2` becomes `2.0.0`, `1.4b`
becomes `1.4.0`), so switching never moves a mod backwards. The
Studio never rewrites an author's scheme unasked. A two-part `1.0` stays two
parts until the first bump, which pads it: patch gives `1.0.1`, minor gives
`1.1.0`.

### 2.2 The build number: automatic, private, only counts real change

A counter per mod of yours, kept in the project's `builds` record (origin to
`{"build": n, "digest": …}`, with `""` for the mod authored here). It goes up by
one each time the Studio writes that mod out **and the output differs from the
last build**. Re-exporting identical content does not count, so a build number
always means different output.

As built, the counter is kept per mod rather than once per project. A project
can hold your own mod, one of yours read back in, and several patches, and each
is released on its own.

- **What counts as a build.** Every path that writes the project's own mod:
  `export_mod`, `_write_out`, `export_zip`, and Play's `build_into`. Play
  especially, since that is the edit-and-test loop, and the build number is
  what tells two test copies in the mods folder apart. `_save_xml` (assets.xml
  alone) does not count; it is not a mod.
- **What is hashed.** Three things:
  - the generated assets.xml **with every comment removed**;
  - the modinfo **without the build stamp**, plus what it says the mod was
    built against;
  - the contents of the carried files.

  Removing only the header would not have been enough. The preview that
  *Export this mod* writes carries verification warnings as comments. An
  export from the mods list does not, and either can have comments switched
  off. So switching between Export and Play would have bumped the build with
  nothing changed.

  The stamp has to be left out because it contains the build number, which is
  decided after the hash. The order is: hash; compare with the recorded
  digest; plan the number; write; record it only once the folder is on disk,
  so a failed export does not use up a number.

  A new assets fingerprint is a new build, because a mod rebuilt against a
  patched game is a different build of it.
- **Where it is shown.** In the modinfo's Studio block; in the header
  comment's title line (`MyMod  v1.2.0, build 57`), rewritten at write time by
  `generator.stamp_build`; and in the checkpoint label
  (`Exported v1.2.0, build 57`). Later, in the Studio's own views of a mods
  folder and the update checker (phase 4). Never in `Version`. Players would see it jump from 1.0.3 to 1.0.61 and learn
  nothing.
- **Only the author's mods.** That means the mod authored here, one of yours
  read back in (`home`), and your patches. Other people's mods, exported as
  part of a stack, are not stamped: their modinfo is theirs, and
  `_over_declared` already guards that.
- **Carrying on.** A mod of yours read back in with no build record picks up
  from the `build` in its own modinfo, so its numbering continues instead of
  restarting at 1.

### 2.3 What it was built against: exact fingerprint first, readable label if possible

- **The exact part.** Always available. Keep `index.fingerprint`'s SHA-1 on the
  project as `base_fingerprint` when assets.xml is read, alongside the existing
  `base_archive` and `base_assets`. Write it at build time. It answers "is this
  the same game data" with certainty, which a label cannot.
- **The readable part.** For example `Anno 117 2.1.0`, read from the
  game's own log (section 4 found the source). When the log is stale because
  the game was updated and not started since, fall back to what is honest: the
  archive name and the date the Studio first saw this fingerprint, e.g. *game
  data of 14 Sep 2026*.

It goes in three places:

- the modinfo's Studio block;
- the header comment's existing *Built against* line;
- a line appended to the mod.io changelog, visible and deletable in the publish
  dialog.

It never goes into the version string. Anno players are moved to the latest game
update automatically, so a mod has one target at a time and no parallel
branches. Embedding the game version in the number, as Minecraft mods do, only
pays off when there are parallel branches.

**The payoff is a warning, not a label.** With the fingerprint recorded, the
Studio can say two things:

- On opening a project: *the game's data has changed since this mod was last
  built; check it.* `gamefiles.read_state` already detects the patch; this ties
  it to a specific mod.
- In the mods-folder view: *built against older game data*, for every
  Studio-built mod, not only the author's own. This is something no mod manager
  can currently say.

### 2.4 The modinfo block

It extends the existing `STUDIO_KEY` block (`games.py:220`), which survives a
round trip, and which both loaders ignore:

```json
"AnnoModdingStudio": {
  "patches": "…",
  "build": 57,
  "builtAgainst": {
    "game": "Anno 117",
    "update": "2.1.0",
    "gameVersion": "2.1.0.1711091.299885",
    "archive": "…",
    "assets": "sha1:…"
  }
}
```

`update` and `gameVersion` are absent when unknown. No paths, ever, for the same reason
`base_description` avoids them. `declared_patch_of` keeps reading only
`patches`; a small `declared_build_of` reads the rest.

### 2.5 The project remembers what it published

A new `published` record on `ModProject`:

- `version`
- `build`
- `when`
- mod.io `modfile_id`
- the timestamp of the snapshot taken at that moment

It is written after `publish.send` succeeds; today nothing is recorded. It is
the baseline for the suggestion in section 3.

For projects with no publish record, the baseline is the most recent
`Exported vX` snapshot whose version differs from the current one. If there is
none, there is no suggestion, and the first release is whatever the author
types.

All new project fields (`builds`, `base_fingerprint`, and later `published`)
are added the way fields always are: a default in `load`, and no
format bump (`changes.py:1684`).

---

## 3. The assisted part: suggesting the bump

**The diff.** `diff_changes` (`changes.py:616`) already compares two change sets
into added, removed and modified. Take the baseline snapshot's changes and the
current `authored()` changes, and classify the diff with the rules below. The
first match wins.

1. **Major.** A change that `creates_asset` in the baseline has no counterpart
   now, or its payload's GUID (`generator._payload_guid`) is different. Also
   major: a patch whose `patches` target changed.
2. **Minor.** Anything added with `creates_asset`. Any structural op (in
   `STRUCTURAL_OPS`) added or removed. Any change on a GUID the baseline did not
   touch. Any change switched off or removed.
3. **Patch.** Everything else, which means `OP_REPLACE` values modified on
   assets already touched.

**Shown with its reasons, and never applied silently.** Next to the version
field, and at the top of the publish dialog:

*Suggested 2.0.0: 1 asset this mod added is gone (Roman Bakery, 1500012345).
Saves that built it will lose it.*

The author accepts it with one click, or picks another level.

The rules are a heuristic, and the text should say so. A value change can
break a save, and a removed asset can be one nobody built. The rules are
conservative in one direction only: they may call a patch a minor, but they
should never call a save-breaking change a patch.

**Later, from the same diff: a changelog draft.** *Added: Roman Bakery.
Changed: 14 values on 6 assets.* This fills the publish dialog's changelog box
as editable text.

**Not yet: Anno 117's `GameSetup`.** `RequiresNewGame` and `SafeToRemove` are
hardcoded (`games.py:362`) and not editable. Once they are, flipping either is a
major bump by rule 1. Until then they stay out of the heuristic.

### As built

`core/releases.py` departs from the sketch above in a few places.

- **Its own comparison, not `diff_changes`.** That compares values and
  switches only, so a new asset whose GUID changed inside the same change
  would pass unseen. The classifier compares the sets of created GUIDs
  directly, and only switched-on changes on either side.
- **Edits to an asset new since the release are part of adding it.** They
  are not also counted as "changes to an asset it did not change before".
- **A `patches` target changing is not a rule.** Only the mod the project
  releases (its own, or `home`) gets a suggestion, and a patch is never that
  mod.
- **The last release.** It is the checkpoint the `published` record points
  at; failing that, the last checkpoint of kind export or publish.
  Checkpoints now carry a `kind`, and old ones labelled "Exported v…" are read
  as exports. They also record `release_version`, the home mod's version
  when there is one: a mod of yours read back in was being checkpointed under
  the project's unrelated "1.0".
- **Where the reasons show.** The Export tab's left column is already as
  tall as a laptop screen, so the line under the version is short:
  *Minor change since 1.2.0 (adds something).*, with **Use 1.3.0**, and the
  reasons in its tooltip. The full reasons are on the question publishing
  asks (*Publish as 1.3.0* / *Keep 1.2.1* / Cancel) whenever the version is
  below the suggestion. The confirm dialog carries the one-line verdict.
- **The changelog draft covers every kind.** New and removed assets by
  name, assets newly changed, entries added or removed, values retuned, and
  changes taken out.
- **What it cannot see.** Only authored changes are in a checkpoint, so a
  mod of yours read back in is judged on what was done to it here. An asset
  it shipped with and you deleted is not seen.

---

## 4. Investigation: can the Studio read the game version?

An earlier session concluded there was no easy way to tell which game update is
installed. That conclusion is not written down anywhere found for this proposal:
neither the docs nor a search of past session transcripts turned it up. So it is
worth doing once, carefully, with the answer recorded here so it is not asked a
third time.

**The question, precisely.** For each game (1800, 117) and each storefront
(Steam, Ubisoft Connect), is there a source that is:

- (a) readable without running the game;
- (b) different after every game update;
- (c) mappable to the name players use, such as *GU 3*?

(a) and (b) are enough for the warning in 2.3. (c) is what makes a label.

**The candidates, cheapest first:**

1. **The executable's version resource.** `Anno1800.exe` and `Anno117.exe`,
   already located by `launch.install_for`. Read with `GetFileVersionInfoW`
   through `ctypes`, stdlib only. Check:
   - whether the resource exists;
   - whether it is a marketing version or an internal build;
   - and above all whether it changes between updates. A file version stuck at
     `1.0.0.0` is the likeliest way this candidate fails.
2. **Steam's `appmanifest_<appid>.acf` `buildid`.** It sits next to the
   `libraryfolders.vdf` that `discovery.py` already reads, and it changes with
   every Steam update. It is numeric, so it satisfies (b) but not (c) without a
   hand-kept table. It is Steam-only, so check whether Ubisoft Connect keeps
   anything comparable in its install data or registry keys.
3. **What the game says about itself.** Check whether the main menu shows a
   version string. If it does, find where it comes from: a text in the texts
   files, a string in the executable, or the game's own log files. Also read the
   first lines of the loader logs the Studio already parses. Of all the
   candidates, only this one can satisfy (c) without a table.
4. **The game data.** The RDA names, sizes and times in
   `gamefiles.archive_marks` already satisfy (a) and (b); that is how
   `read_state` detects a patch. For 1800 the archive name is roughly the patch
   level. None of them satisfies (c).
5. **The fallback that always works:** the assets.xml fingerprint, plus the date
   the Studio first saw it. It is exact but opaque. If nothing satisfies (c), a
   small table mapping a fingerprint to a label would work: it would be shipped
   with the Studio and could be corrected by users. That turns a readable label
   into a maintenance job, and the table should be weighed as exactly that.

**How to answer (b) honestly.** One observation cannot show that a value
changes. For 117, which is still being updated, record the candidates now and
again after the next update. For 1800, compare against an older install or the
Steam depot history if either is at hand. A `tools/game_version_probe.py`, on
the same lines as the census's probes, would print every candidate for every
install `find_game_installs` finds, so repeating the check is one command.

**Output.** A table appended to this section, with one row per candidate and a
column per game and storefront, marked against (a), (b) and (c), and a verdict.
The *label* field of 2.3 is built only if some candidate satisfies all three for
at least the storefronts most players use.

### Findings, 28 Sep 2026

`tools/game_version_probe.py` was run against both Steam installs on this
machine. Re-run it with `--history` after the next update.

The question has a good answer, and the earlier verdict probably came from
looking at the executable and the data files, which do not carry the public
name. **The game writes it to its own log on every run.** Near the top of each
`Documents\<game>\log\Anno*_LOGFILE_*.log` there is a line like these two:

```
BuildString: [Build:2026-08-28 09:11:47] Anno8 Steam GoldmasterSteam Win64 Retail v2.1.0.1711091.299885 //Anno8/Anno117_v2_1
BuildString: Developer build - for internal use only! [Build: 2024-11-11 14:14:36] Anno7 Steam Goldmaster Win64 Release-Static v18.4.1412158.165273 main
```

The components before the long build number are the name the patch notes use.
`2.1.0` is Anno Union's *Anno 117: Pax Romana – Patch 2.1*, and `18.4` is
*Anno 1800: Game Update 18.4*. Every retail 1800 run calls itself a "developer
build"; that is noise.

The logs are not pruned, so they are also a history:

- **Anno 117:** 107 runs since January 2026, six build changes: 1.3, 1.6.1,
  2.0, back to 1.6.1, 2.0, then 2.1.0.
- **Anno 1800:** 611 runs since November 2023, four build changes: 18.1 to
  18.4.

On 20 August 117 went from 2.0 back to 1.6.1 and returned the next day. That
is most likely a switch to Steam's legacy branch and back. So the label must be
taken from the last run, not assumed to only ever go up.

| Candidate | (a) readable without running | (b) changes every update | (c) public name | Notes |
|---|---|---|---|---|
| Executable version resource | yes | **unverified** | no | 117: `26.7155.4.37741`; 1800: `21.35902.2.34201`. No visible relation to 2.1.0 or 18.4, and only one reading of each exists, so (b) is unproven. |
| Steam `buildid` | yes, Steam only | yes | no | 117: `24988830`; 1800: `16367353`. `LastUpdated` matches the executable's write time to the minute. |
| **Game log `BuildString`** | yes, **as of the last run** | yes, seen 6 and 4 times | **yes** | Also gives the build date and branch. Log file names contain the machine name, so they must never be copied anywhere. |
| Archive marks | yes | yes | no | Already used by `gamefiles.read_state`. |
| assets.xml fingerprint | yes, once read | yes | no | Exact; the fallback. |
| Loader log (`mods/mod-loader.log`) | yes | n/a | no | No version line at all. |

**Verdict: build the label.** Take it from the newest game log's
`BuildString` and guard it with a staleness check. If the executable or the
archives were written after that run started, the game has been updated and
not started since. In that case the label is unknown ("start the game once to
record which update this is"), and the fingerprint still stands in. The probe
already applies this check, and both installs here pass it.

Recorded in the modinfo block (2.4), `label` is `2.1.0` and a new `build`
field under `builtAgainst` keeps the full `2.1.0.1711091.299885`.

**Still unverified:**

- **Ubisoft Connect installs.** None on this machine. Presumably they log to
  the same Documents folder with a different store word, but that has not been
  seen. The parser reads the store word rather than assuming it.
- **Whether the exe resource changes between updates.** It does not matter to
  the verdict. Check it in passing after the next 117 update.

---

## 5. Two renames that stop the word "version" meaning two things

History snapshots are currently "versions". The history dialog calls them that,
and so do its buttons and hint text, the *Save a version now…* and *Restore
this version* actions, and the restore prompt at `main_window.py:5786`. Once
versions are numbered, *Restore this version* next to a `1.2.0` becomes
ambiguous. Rename snapshots to **checkpoints**:

- *Save a checkpoint now…*
- *Restore this checkpoint*
- *A checkpoint is recorded every time you build the mod.*

Keep the version and build in each row's label. This touches UI strings only,
not the project format; `Snapshot` keeps its name in code.

---

## 6. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 0 | ~~Game-version investigation~~: done, `tools/game_version_probe.py` and the findings in section 4; re-run after 117's next update | nothing |
| 1 | ~~Build number~~: done. `builds` and `base_fingerprint` on the project; the comment-free hash on every build path including Play; the modinfo block; the build in the header and checkpoint labels; the checkpoints rename | nothing |
| 2 | ~~Version controls~~: done. `core/versioning.py` (parse, bump, adopt, compare with live); the *Bump* menu and the build label beside the version field; the `published` record and its checkpoint; publish offers the next patch before exporting | 1 |
| 3 | ~~Suggestion~~: done. `core/releases.py`; the line under the version with *Use*; the question on publishing; the changelog draft; checkpoint kinds | 2, for the baseline |
| 4 | ~~Built-against~~: done. The update label from the game's log, remembered per fingerprint; each build's data in its record; the status bar, *build N · older game data*, and the mods-folder *Built on* column | 1; 0 for the label |

### Phase 4 as built

- **Where the label comes from.** The newest log is only asked when the
  loaded assets.xml is the last extraction from the game *and* that
  extraction still matches the game's archives (`gamefiles.read_state` says
  current), and only while the log is newer than the game's executable and
  archives. What is installed says nothing about an older extraction. What
  is learnt is kept in `extracted/game-updates.json`, fingerprint to update,
  so data seen once keeps its name.
- **The modinfo keys** are `update` and `gameVersion` under `builtAgainst`,
  first sketched as `label` and `build`; `build` already means the
  Studio's count one level up.
- **The build record** keeps `assets` and `update` beside the number.
  Writing the same output again on new data (possible with no modinfo
  written) moves them on without a new number: it has been checked against
  that data.
- **On opening.** A dialog would stop every project opened after an update.
  So the persistent part of the status bar says it instead, and the Export
  tab keeps saying it beside the build until the mod is built again.
- **The mods-folder view** is *Import your installed mods*, which gained a
  *Built on* column at the end rather than changing what the existing
  columns mean. It compares only mods whose modinfo names the same game.

Phase 1 is the most mechanical and changes nothing players see except the
modinfo block. Its risk is the hash: every build path has to produce the same
hash for the same output, and tests should assert exactly that, with the header
switched on and off. Phase 3 is the one that needs judgement. Its rules should
arrive with a test per rule, built from real before-and-after projects rather
than invented ones. Phase 4's warnings are the part that makes recording the
game data worth doing, and they work with or without phase 0's label.

**Decisions this proposal makes that are worth a second look:**

- **Play counts as a build.** The alternative is only exports. That would make
  build numbers smaller, but would leave two test copies of the same version in
  a mods folder indistinguishable.
- **Publish offers the next patch rather than refusing.** The dialog shows it
  and the author can change it, so nothing goes live unseen.
- **The build number never appears in `Version`.** Not even as `1.2.0+57`.
  `version_key` would order that correctly, but players would see it wherever
  a mod's version is shown, on mod.io and in the mod managers.
