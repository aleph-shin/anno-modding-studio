# What the mod loader does that the Studio does not: a proposal

A list of everything the mod loader's reference
([jakobharder.github.io/anno-mod-loader](https://jakobharder.github.io/anno-mod-loader/))
describes that the Studio does not handle, with what closing each gap would
mean and roughly what it would cost. Written against all seventeen pages of that
site as they stood on 26 September 2026, the code at `37fe12c`, and the mod.io
census — 857 Anno 1800 mods and 371 Anno 117
ones. Every usage count below comes from that census. Every claim about what the
Studio does today was either read in the code or run against it.

**Recommendation in one paragraph.** Most of what is missing is not missing in a
way that breaks anything. The Studio carries what it cannot read, so an export
never drops a text patch, an InfoTip patch or a Lua file. What the gaps cost is
*sight*: the Studio says what a mod does and which mod wins, and for everything
outside `assets.xml` it does not know. So spend in order of how many mods a gap
hides and how cheaply it can be seen:

- **First, fix the two places where the Studio gives a wrong answer rather than
  none:** the modinfo key names and conditions that combine ModIDs. Both are now
  fixed.
- **Then the cheap, visible things:** what a mod's modinfo says it needs or
  clashes with, and that a mod runs Lua at all.
- **Then text patches.** They are the largest gap by far: half of all mods ship
  one, and they are what gives a mod's new assets their names.
- **InfoTips should be shown, not modelled.** Their base file is binary.
- **CFG/FC patches only need recognising.** Nineteen mods use them, all on the
  same world-map file.
- **Lua should be seen and connected to, never written here.**

**Status (1 October 2026).** All three wrong answers found so far are fixed.
Every gap in sections 3 and 10 is handled, and the cross-check against the
user's own copy of the loader (section 11) is built: `core/xmltest.py` and
`core/loadercheck.py`. Still open: whether Anno 117 treats `Require` as a
load-after (section 6.3), the reading differences left on the 1800 loadout, and
a bug report for the `xmltest3` crash. Where sections 8 and 11 say nothing is
built yet, they were written before it was; `loadout.py` is `switches.py`.

- New Anno 1800 modinfos use the dependency keys the loader reads, and older
  Studio-made ones are put right on export (`37fe12c`, section 6.1).
- Anno 117 conditions that combine ModIDs are answered as the loader answers
  them (`1df75fc`, section 5.1).
- A mod another mod replaces is no longer read as loading (`f6b2ac5`, section
  6.7). Building the pre-Play warnings turned this one up.

The pre-Play warnings are built too (`c73806b`, sections 6.2 and 6.5), and so
are Lua tier 1 (`d8f05a3`, section 7.2), reading mods' text patches for names
(`bfb0c44`, section 4.1), the small ModOp fixes (`52d5628`, sections 5.2, 5.6
and 6.4), the loader's own load order (`ccb44ad`, 6.4), recognising InfoTip
patches (`e24d8d1`, 4.2), the loader log's per-op warnings (`d7cc4bb`),
in-game names for the assets the Studio creates (`0f8a299`, 4.1), and Lua tier
2 (`505e23e`, 7.2).

And the rest, as asked for: `activation.json` (`d2fbb9a`, 6.6), a new mod's
`Difficulty` and `GameSetup` (`26f1884`, 6.5), templates, model file patches and
link files recognised (`2ce419e`, 4.3, 4.4, 5.7), zipped Anno 1800 mods
(`5f162be`, 6.6), `//` search conditions (`77004f5`, 5.5), `MaxRepeat`
(`c285b7c`, 5.4) and `ModValue` copies from a path (`9714cae`, 5.3).

Two decisions in section 8 are taken: how many languages a name goes into,
and how loudly to mark a script mod. The third, the FileDB decoder, is taken
too: none for now (section 8). Shipping the loader's own tools with the Studio
is decided against; using the copy in an installed Anno Modding Tools extension,
when there is one, is the recommended direction and is written up in section 11.
Everything in the order of work (section 10) is done.

Since the first draft, the loader's own source (MIT-licensed,
`github.com/jakobharder/anno-mod-loader`) has been read where the docs left a
question. Where it settled one, the section says so.

---

## 1. How this was measured

- **The docs.** Every page in the site's `sitemap.xml`, read both rendered and
  from the markdown in `github.com/jakobharder/anno-mod-loader/docs`. The site
  has no changelog; the closest thing is its *Changes with Anno 117* page.
- **The census.** Usage was counted from each mod's `manifest.json` (every file
  in its zip) and from the extracted XML and modinfo files. A figure like "67
  mods" means 67 mod.io entries. A single entry can bundle several mods.
- **The Studio.** The code was read, then a scratch script ran the doubtful
  cases through the real `parse_ops` → `Translator` pipeline, and
  `build_modinfo`, against the test suite's small `assets.xml`. Its results are
  quoted where they matter.
- **Base game files.** These were read straight out of the installed games'
  archives through `core/rda.py`, to see which ones are text and which are
  binary.

Sizes: **S** is a day or two, **M** about a week, **L** several weeks and
**XL** more than that. They are guesses meant for ranking items against each
other, not promises.

## 2. The shape of the Studio, and why it sets the cost

The Studio models one game file. `assets.xml` is indexed by GUID. A mod's ModOps
are read into narrow changes to fields (`core/modops.py`), those changes are
laid over the game to show the asset as the game will load it
(`core/effective.py`), and narrow ModOps are written back out
(`core/generator.py`, `core/games.py`). Anything the pipeline cannot reduce to
narrow changes is either *kept as written* or *carried*: emitted verbatim in its
original position. Every other file in a mod is listed in the import dialog's
*Not ModOps* tab and copied through on export (`generator.carried_files`).

So any loader feature can be supported at one of three levels:

1. **Recognise.** Say what the file or construct is and what it does to the
   game, in words. This is cheap for anything. It is also what the Studio
   already does for `.cfg` models, bundled mods and the like.
2. **Read.** Work out its effect on the game data, so it shows in the effective
   view and in *which mod wins*. For another game file, that needs the base
   version of that file, an index for it and a view of it. Each one is close to
   a copy of what exists for `assets.xml`.
3. **Write.** Author it in the Studio. Only worth doing where people would
   otherwise hand-write XML the Studio already understands.

Level 1 costs about the same for every gap. Levels 2 and 3 differ widely, and
most of what follows is about which level each gap deserves.

## 3. Summary

| Gap | Census use | Today | Proposed | Size |
|---|---|---|---|---|
| modinfo dependency key names (1800) | 541 / 190 mods use the right names | **fixed** in `37fe12c` | — | done |
| Conditions combining ModIDs (117) | 1 mod (2 more in 1800, where the loader does not support it) | **fixed** in `1df75fc` | — | done |
| `texts_*.xml` patches | 449 (1800), 125 (117) | **read** for names in `bfb0c44`; names **written** for new assets in `0f8a299` | — | done |
| Dependencies, incompatibilities, deprecations | 172 (1800), 30 (117) list at least one | **built** in `c73806b` | — | done |
| A replaced (deprecated) mod read as loading | 190 (1800), 12 (117) mods deprecate something | **fixed** in `f6b2ac5` | — | done |
| Lua via modinfo `Scripts` (117) | 12 mods | **recognised** in `d8f05a3`; **connected** (tier 2) in `505e23e` | — | done |
| Lua via `ActionExecuteScript` assets | 31 mods, 1,051 references | **resolved** in `d8f05a3` | — | done |
| InfoTip patches (`export.bin.xml`) | 67 (1800), 1 (117) | **recognised** in `e24d8d1` | no decoder (section 8) | — |
| `ModValue Merge`/`Remove` flags (117) | none yet | **settled** in `52d5628` | — | done |
| `ModValue Insert` of a path, `ModValueContent`, `SkipParent` (117) | none yet | a path copy **read** in `9714cae`; wrappers carried | — | done |
| `MaxRepeat` loop groups (117) | 13 groups, 3 mods | **read** in `c285b7c` | — | done |
| `//search` conditions | 735 in 84 mods (1800) | **answered** in `77004f5` | — | done |
| `AllowNoMatch` | 508 uses (1800) | **known** in `52d5628` | — | done |
| Late-phase LoadAfter (`"*"` plus other IDs) | — | **fixed** in `52d5628` | — | done |
| `activation.json` (1800) | none seen | **read** in `d2fbb9a` | — | done |
| Mods loaded from `.zip` (1800) | none seen | **read** through a mirror in `5f162be` | — | done |
| `Difficulty`, `GameSetup` (117) | 361 / 271 mods | **shown** in tooltips (`c73806b`); **set** for your mods in `26f1884` | — | done |
| `templates.xml` patches | 118 (1800), 19 (117) | **recognised** in `2ce419e` | read, if asked | — |
| CFG/FC patches (`.cfg.xml`, `.fc.xml`, 1800) | 1 + 18 mods | **recognised** in `2ce419e` | — | done |
| Link files (`.lnk`, 1800) | 6 mods | **recognised** in `2ce419e` | — | done |
| Other game XML (`camera.xml`) | 1 mod per game | listed, carried | nothing | — |

In the *Today* column, *blocked* means the op is not read into changes and goes
back out as written. No data is lost; the effect is simply not shown.

## 4. Other files the loader patches

### 4.1 Texts

**Use:** `texts_*.xml` in 449 of the 857 Anno 1800 mods (12,912 files, since most
ship all eleven languages) and in 125 of the 371 Anno 117 mods.

**Today:** the game's own texts are loaded for display names
(`AssetIndex.load_texts`, keyed by `<GUID>` in 1800 and `<LineId>` in 117). A
mod's text patches are listed as "a ModOps patch for another game file" and
never read. As a result:

- An asset a mod adds shows under its internal `Standard/Name`, never its
  in-game name.
- Two mods renaming the same thing do not show as a conflict.
- An asset created in the Studio has no way to get a name. In 1800 that name is a
  `<GUID>` text entry; in 117 it is a `<LineId>` entry plus an `<OasisId>`
  reference in the asset.

**Read (M).** Nearly every real text patch has one shape:
`<ModOp Add="/TextExport/Texts[1]">` (or `Type="add" Path="/TextExport/Texts"`)
with `<Text><GUID|LineId/><Text/></Text>` children. The work is:

- Read that shape, layered in load order over the base texts for the chosen
  language.
- Anything else in a text patch stays *recognised*.
- The browser and the effective view show the result, and a text two mods both
  set shows as a conflict.

The index already has a `texts` table. What is new is laying mod entries over it
and letting a folder read refresh it.

**Read, as built** (`bfb0c44`, `core/texts.py`). The census showed four shapes,
not one:

- entries added to the list;
- an entry's text replaced by GUID or LineId;
- text added to the end of an entry;
- a whole entry replaced or merged.

All four are read, through the existing ModOps parser, so short forms,
includes and groups come for free. So is a condition asking which mods are
loaded. That covers 2,506 of the 2,659 Anno 1800 census ops and 1,090 of the
1,095 Anno 117 ones.

- **Language.** The one the game's texts were loaded in, kept in the index's
  meta with those texts. English when none are.
- **Which mods.** The mods that load, in load order: switched on, and not
  replaced (6.7).
- **Caching.** Files are held by path and mtime. A switch flipped rebuilds the
  names in milliseconds: on the machine's Anno 1800 folder, 0.15 s to read the
  91 patches of 164 mods (958 entries) and 0.02 s to rebuild.
- **The order a name is taken in:**
  1. a mod's text for the asset's own GUID;
  2. embedded text;
  3. a mod's text at its LineID/OasisId;
  4. the game's text there;
  5. the internal name.

  Embedded text still beats any LineID lookup, for the Schnapps reason in the
  README.
- **Assets a mod adds** are named the same way, and by their embedded
  `LocaText`, which was ignored before. Both kinds are searchable by these
  names, through a temp table of the index's connection. Nothing a mod says is
  written into the cache.
- **Conflicts** are said, not listed in a view of their own. The editor says
  *named by* the mod whose text shows, and *also named differently by* any
  other loaded mod. Most apparent clashes in the census were copies of one
  shared piece at different versions, which one-per-ModID already settles.
- **Not changed:** a change's stored `asset_label` (the conflicts list, the
  history) keeps the name it was read under.

**Write (M).** A name field on *New asset…* and on an asset's `Standard` section
that writes the text entry for the project's languages. In 117, pick a LineId
from the mod's own GUID range, which the docs recommend matching to the asset's
GUID. The language question is decided (section 8): one name field, written into
every language file the game has.

**Write, as built** (`0f8a299`), for the assets the Studio creates. Renaming a
shipped asset is not in it.

- **Where you give the name:** *New asset…* has an **In-game name** field.
  **In-game name…** in the editor changes it later.
- **Where it is kept:** on the change that creates the asset, so it is saved,
  undone and exported with that change.
- **What export writes:** the name, into every language file the game ships:
  13 for Anno 1800 and 12 for Anno 117, read off the archives.
  - **Anno 1800** is keyed by the asset's GUID. The asset needs nothing, as the
    census mods show (*Diagonal Residences* copies an asset and names it by its
    GUID alone).
  - **Anno 117** is keyed by a LineId equal to the asset's GUID, and the asset
    gets `<Text><OasisId>` with the same number, as 6,007 census assets do.
- **A mod that already ships a texts file** keeps it. The Studio's entries go in
  one marked block at its end, which the next export replaces.

### 4.2 InfoTips

**Use:** `data/infotips/export.bin.xml` in 67 Anno 1800 mods (194 files, plus 164
`.include.xml` files under `infotips/`) and in 1 Anno 117 mod (the Community Bug
Fix Mod). Most of the 1800 use comes from shared pieces bundled over and over:
Serp's `shared_TT_MenuInputOutputAmounts`, `TooltipBugFixes` and the
`shared-pools-and-definitions` family.

**Today:** listed and carried.

**The cost is the base file.** `export.bin` is binary FileDB in both games,
confirmed from the archives:

- 1800's copy in `data0.rda` is FileDB version 1.
- 117's copy in `infotips.rda` has a different header.

The loader turns it into XML before patching. The docs point at FileDBReader's
`FileFormats/infotip.xml` for 1800's shape, and say 117's shape is new: compact
attributes, hidden counts and `TextId` OasisIds. They also say it is "not part
of FileDBReader".

- **Recognise (S–M).** The patch files are ordinary ModOps, and `parse_ops`
  reads them without knowing what they target. List each patch's targets by
  InfoTip GUID (`//InfoTipData[Guid='…']`, or 117's `GUID=`/`@guid` lookup),
  check that the XML is well formed, and show which mods touch the same InfoTip
  GUID. The last is a real conflict signal, given how many mods bundle the same
  shared tooltip fixes.

  **As built** (`e24d8d1`, `core/infotips.py`). Each op is sorted three ways:
  - one naming an InfoTip changes it: `//InfoTipData[Guid='…']` at the start of
    its path, or a `GUID=` / `@guid` lookup;
  - one whose body is whole `<InfoTipData>` entries adds them;
  - anything else searches every InfoTip.

  1,204 of the 1,256 Anno 1800 census ops name or add theirs, and none of the
  194 files failed to read. Where it shows:
  - the changes panel's tooltip for each mod gives what its patch does, and
    which of its InfoTips another loaded mod patches too, a shared piece's
    copies counted once;
  - the mods-folder dialog's tooltip gives what it does;
  - the import dialog calls the file *an InfoTip patch*.

  On the machine's Anno 1800 folder, 22 loaded mods patch InfoTips, and two
  InfoTips are patched by more than one mod. `25000020` is patched by seven.
  The one Anno 117 patch in the census is an empty `<ModOps>`. Well-formedness
  is checked the loader's way, with lenient parsing that recovers what it can,
  and only a file that yields nothing is reported.
- **Read (L, per game).** A FileDB decoder plus the infotip format description,
  so the patched InfoTip can be shown. There are two FileDB versions and two XML
  shapes. **Decided: not for now** (section 8): the broken-patch check it
  would have given comes from the loader's log instead.

  One fact bears on the cost. The loader has its own reader,
  `libs/xml-operations/src/xml_filedb_reader.cc`: 654 lines, MIT-licensed,
  written for InfoTips (it restores `InfoTipCount`, `ChildCount` and the
  visibility enumerations). A Python port of that is a better-defined job than
  reverse-engineering FileDBReader's format files, provided it covers the game
  in question. Which FileDB versions it reads has not been checked.

  The docs' warning that FileDBReader "may not match" is about that separate
  tool, not this reader. For Anno 117 the loader converts InfoTips into a more
  compact XML of its own, and FileDBReader does not reproduce it. This reader
  *is* that conversion: xmltest goes through it (`XmlAutoSerializer`), and it
  writes the shape the docs describe, turning `ElementType` into a `Type`
  attribute and reading `TextId`. `stable/anno1800` has its own copy for 1800's
  format.

  What is left is version drift. The public repository was last changed in
  November 2025, and the loader inside the game may be newer. That can be
  measured: a port's output for each game's `export.bin` should equal what
  xmltest writes for it, which the census tooling already runs.
- **Write (XL).** An InfoTip is a small UI program made of containers,
  visibility conditions and text references, not a set of fields. Not proposed.

### 4.3 CFG and FC patches — recognised in `2ce419e`

**Use:** the docs describe this for Anno 1800 only. The census has 18 mods with
a `.fc.xml` and 1 with a `.cfg.xml`, and **every one** patches
`data/graphics/ui/3d_objects/world_map/world_map_01`. These are map mods adding
sessions to the world map. The 282 mods that ship whole `.cfg` files (and 202
with `.fc`, 277 with `.ifo`) are replacing models outright. That is not
patching, and it is already carried.

**Base files are text.** The sampled `.cfg`, `.fc` and `.ifo` files are plain
XML in both games' archives, so reading one needs no decoder.

- **Recognise (S).** Say "patches `world_map_01.fc`: adds N dummy groups" rather
  than listing the file among replaced files. Mention that `$auto` ids are
  assigned at load time.
- **Read (M), if ever.** Worth it only if two world-map mods clashing turns out
  to be a common complaint. Several of the 18 are alternatives to each other
  (MAUG's six *More Sessions* variants), so that may well be the one conflict
  worth showing, but nothing so far says it is asked for.

**Recognised** (`2ce419e`, `core/sidefiles.py`). The import dialog lists a
`.cfg.xml` or `.fc.xml` as *a model file patch*: which file, how many ops adding
how many entries, and that its `$auto` ids are given out when the game loads it.
On the census: *patches world_map_01.fc: 2 ops adding 97 entries*.

### 4.4 templates.xml — recognised in `2ce419e`

**Use:** 118 Anno 1800 mods (179 files) and 19 Anno 117 mods. The docs'
`Template=` lookup (`//Template[Name='…']`) lives here. REVIEW.md P7-47 found all
148 census uses of it in `templates.xml`.

**Today:** the base `templates.xml` is read for the field schema only. A mod's
patch to it is listed and carried, and REVIEW.md records that as deliberate.

- **Recognise (S).** Name the templates a patch adds or changes.
- **Read (M).** Lay mod patches over the schema, so that assets built on a mod's
  new template get the right fields in the editor. That is the one practical
  payoff, and it only matters for patches of another mod's content.

**Recognised** (`2ce419e`). *A templates.xml patch*, with the templates it adds
and changes by name. A template placed beside another is not a change to that
one, and a search naming no template is shown by its path. Across the census,
all but 5 of the 198 files are said this way; the 5 have no ops, or none that
can be made out. Reading them into the schema is still open, if asked for.

### 4.5 Other game XML

`data/base/config/game/camera.xml` (Serp's *Zoom Out/In Further*) and its 1800
counterpart are the only non-asset, non-text XML patched in the census. Most
other XML under `config/export/` is include files, which are already followed.
Nothing proposed.

## 5. Inside assets.xml

What the scratch run showed for each construct, with the size to read it
properly.

### 5.1 Conditions that combine ModIDs — fixed in `1df75fc`

Since Anno 117, the loader turns each `#mod-id` into `true()` or `false()`, so
IDs combine with XPath: `#mod-a or #mod-b`, `#mod-a and not(#mod-b)`.
`condition_holds` took everything after the `#` as a single ModID. So when a
whole mods folder was read, with both mods present:

- `#mod-a or #mod-b` → **False**: `"mod-a or #mod-b"` is not a ModID.
- `!#mod-a and !#mod-b` → **True**.

Both answers were the opposite of the loader's. A single-mod import has no
present set and correctly came back unanswerable.

Only three conditions in the whole census do this:

- One in an Anno 117 mod: *City Watch City Skins*, 5560170.
- Two in an Anno 1800 mod: *Watering Enbesa*, 4446332.

**What the two loaders do**, from their source rather than the docs:

- **Anno 117** (`ReplaceStaticVariables` on the main branch). Every token
  matching `[$#][a-zA-Z_][\w\-.]*` becomes `true()` or `false()` for a mod, or
  the option's value for a setting. What is left is evaluated as XPath and read
  as a boolean. A leading `!` is taken off first and negates the whole answer,
  so `!#mod-a or #mod-b` means "neither", and a `!` further in is not XPath at
  all.
- **Anno 1800** (the `stable/anno1800` branch). Everything after a leading `#`
  is looked up, exactly, as one ModID. `#Desalination or #irrigation_well_kurila`
  asks after a mod by that whole name, so *Watering Enbesa*'s two ops never run.
  The Studio already said so, and still does.

**As built.** For Anno 117, a condition whose names are joined by an operator
(`and`, `or`, `not`, brackets, comparisons) is evaluated the loader's way, with
lxml. It is not answered when:

- there is no mods folder;
- a setting is undeclared, or holds anything but a yes, a no or a number;
- a path into the game's files is mixed in;
- there is a `!` inside.

A single name, `#vegetables-and-cheese` included, is read as before. With no
game known, a combination starting with `#` is not answered. Groups and
conditional includes now carry their game, so the same reading applies to them.
A 117 condition that names a mod anywhere goes back out as asking which mods are
loaded.

**Left as it was:** both loaders look ModIDs up case-sensitively (a `std::set`
and a `std::map`), while the Studio's present set is lowercased. Whether that
ever matters depends on the case of the IDs the game registers, which is not
known (section 9).

### 5.2 `ModValue Merge` and `Remove` — settled in `52d5628`

`<ModValue Merge="Moderate"/>` adds flags to a semicolon list if they are
missing, and `Remove=` takes them out (117). They used to reach
`computed_value`, which has only `Insert` to go on, and stop at "an empty
expression".

**As built**, they follow the loader's `MergeFlags`:

- split on `;`, with empty pieces dropped;
- matched exactly;
- an added flag goes at the end if it is missing, and a removed one's first
  occurrence comes out;
- several in one field apply in order.

What the list starts from depends on the op:

- **A merge** starts from the game's list. `RecursiveMerge` copies the
  `ModValue` into the game's field, so `MergeFlags` reads the game's value.
- **Anything else** starts from the mod's own copy of the field.

Left unsettled, such an op is kept as written with a reason about flags, not
about the mod's settings. No census mod uses them yet; the loader's branch
adding them (`devel/fix-modvalue`) is from November 2025.

### 5.3 `ModValue Insert` of a path, `ModValueContent`, `SkipParent` — a path copy read in `9714cae`

`Insert="@1000/Standard/GUID/text()"` copies from another asset, `Insert="../…"`
copies from the insertion point, and `ModValueContent` / `SkipParent` wrap
copied nodes (117). Today they fail with "is not an expression the Studio can
work out". The Studio already resolves `@guid/path` when it settles a
`Content=` attribute, so the lookup exists. What is new is the relative form and
the wrapper expansion.

**As built** (`9714cae`). Measured first: no census mod uses any of these —
1,022 `ModValue Insert`s in 50 Anno 117 mods, every one arithmetic. So the part
with one clear meaning is read, and the list-building part is not:
- **Read:** `Insert="@guid/path"`, from the asset as the mods ahead left it, and
  `Insert="../…"`, each `../` climbing from the ModValue's field through the
  op's body into the target. `/text()` or a field puts in text; a section puts
  in the section.
- **Carried, saying why:** a copy wrapped in `ModValueContent` (with or without
  `SkipParent`), and one finding nothing or several nodes.

### 5.4 `MaxRepeat` loop groups — read in `c285b7c`

`<Group Condition="…" MaxRepeat="N">` repeats until the condition stops matching
(117), according to the docs and the loader's source. The README's measurement
says it runs on to N; section 9 has the discrepancy, and it needs settling
first. Today the whole group is carried. Reading it means re-running the group's
ops against the working tree, checking the condition each time. `Translator`
works one op at a time, and a loop needs it to go round again.

**Settled, then built** (`c285b7c`). The discrepancy went the docs' way. With
the loader's own `xmltest3`:
- removing the first of five items under `MaxRepeat="10"` ran five times;
- adding while fewer than three ran three times;
- a cap of two stopped at two, without a warning;
- Anno 1800's `xmltest` ran the group once.

The README's old row was wrong and is corrected. No new loop was needed in the
`Translator`: the rounds are read as groups nested one inside the next, so a
round whose condition fails ends the rest, exactly as the loop does. The group
goes back out once, as written, with every round's changes hanging off it.
With no game known, or more than 1,000 rounds, it is still carried whole.

On the census's three mods, nothing is carried for it any more:
- *All Items visible at Traders* has no condition, so all 999 rounds run: a slot
  for each of seven traders each time.
- *super-specialists* runs 50 rounds in each of its ten files.

An early version read the rounds straight through and collapsed identical
ones into a single change; keeping the group whole is what gets all 500 of the
latter.

### 5.5 `//search` conditions — answered in `77004f5`

`Condition="//Values[Standard/GUID='1000']"` and similar searches across all
assets come back unanswerable. The index can answer most of them the way
`Path` searches are already answered (`search_in`), but not all: a condition
over the entire document can be slow and has to be bounded, as path searches
are at 5,000 assets.

**As built** (`77004f5`). The census has 735 of these, in 84 Anno 1800 mods and
none in Anno 117. Most ask whether another mod's asset is there, or what an
earlier op left behind, so the base file alone would answer them wrongly. They
are answered from the document as it stands when the op runs, as search ops
already are:
- the shipped assets no mod has touched yet, from the batch sweep, which now
  takes conditions' searches too;
- every asset as the mods ahead left it.

A condition holds when it finds anything: nodes, text, or `true()`. It stays
unanswered for `~//`, whose meaning to the loader is not known. On the
installed Anno 1800 mods, all 34 such conditions in 14 mods were unanswered
before, and all are answered now.

### 5.6 `AllowNoMatch` — known in `52d5628`

It only silences the loader's "no match" warning. An op rebuilt as narrow
changes aims at a known node, so dropping the attribute there does no harm. It
should be known, not reported as ignored. Add it to `KNOWN_ATTRS`, keep it on
ops that go out as written, and drop it silently from narrow ones.

**As built.** It is in `KNOWN_ATTRS`. One more thing followed from what it
means: an op carrying it that matches nothing used to be *blocked*, flagged as
something to look at. Its author said matching nothing is fine, so now it is
*settled* and kept as written, since with other mods it may match. The loader
reads the attribute as present or not (`pugih::attrib`), so
`AllowNoMatch="0"` counts too. There are 508 uses in the Anno 1800 census,
most in the shared pools piece.

### 5.7 Also noted

- **Link files** (`.lnk`, 1800). A text file whose content names another path.
  Six mods use them, one with 82,333 of them (island variants). They are
  carried correctly today. Recognising them (S) would stop a mod looking like
  82,000 unexplained files. **Recognised** in `2ce419e`: *N link files*, with
  one as the example of what it reads, where they used to be listed as files
  replacing the game's.
- **The 117 "Asset ModOp workaround"** (`<ModOp Add="/AssetList/Groups[last()]">`).
  The docs require it once before any bare `<Asset>`. The Studio writes 117 new
  assets as `addNextSibling`, not bare `<Asset>`, so it does not need the
  workaround. If that ever changes, it will. **The game does not need it
  either**, measured in Anno 117 on 4 Oct 2026: a bare `<Asset>` in a mod loaded
  first, with no group added, was in the file afterwards. `xmltest3` does need
  it, so the loader check and the census apply every Anno 117 stack on top of it
  (`core.xmltest._end_group`).

## 6. modinfo and load order

### 6.1 Dependency key names (1800) — fixed

New Anno 1800 modinfos said `"Dependencies": []` and `"DeprecatedIds": []`. The
loader reads `ModDependencies` (541 census mods) and `DeprecateIds` (190); the
Studio's spellings turn up in 0 and 1 mods. Fixed in `37fe12c`, which also
renames the two keys in place when a modinfo the Studio wrote earlier goes back
out. It does so only in Anno 1800, only for `Dependencies` as a list, and never
where the loader's own spelling is there as well.

### 6.2 What a mod says it needs — built in `c73806b`

The loader warns in its log when a required mod is missing or an incompatible
one is active. The Studio now says the same before Play, when it can still be
acted on.

The keys:

- **1800:** `ModDependencies`, `OptionalDependencies`, `IncompatibleIds`,
  `DeprecateIds`.
- **117:** the `Dependencies` object's `Require`, `Optional`, `Incompatible`
  and `Deprecate`.

Most mods carry these keys, but mostly as empty lists: 172 of the 857 Anno 1800
mods and 30 of the 371 Anno 117 mods name at least one mod in them.

**What the loader's source added** (`mod_loading_order.cc`, the same in both
games):

- A deprecated mod is not warned about. It is **dropped**: `HandleDeprecation`
  skips any mod whose ID another loaded mod lists.
- In 117, a dependency on the old ID becomes one on its replacement.
- Require and Incompatible are handled in the game's own code, which is not
  public. Their wording comes from the machine's Anno 1800 log: "*X* is missing
  a dependency: *id*".

**The census added** that the lists hold things that are not ModIDs: the
template's `ModId` placeholder (28 times), empty strings, `"null"`, and GitHub
download links in `ModDependencies`. These are left out. 117's `Require` also
names DLCs (`"DLC 1"`, `PropheciesOfAsh`), and the Studio cannot know which are
owned.

**As built** (`core/dependencies.py`):

- **What is checked** is what Play would leave loading: the project's mods that
  are on, what is bundled in them, and what Play cannot switch (1800's browser
  copies).
- **What it says:**
  - *A needs B, which is switched off / is not installed*;
  - *A does not work with B, and both will load*, once per pair;
  - *B will not load: A replaces it*.

  Two mods with one name are told apart by ModID. A missing 117 requirement
  carries a note that it may name a DLC.
- **Where it shows:**
  - on the fourth card, orange only on the live project;
  - in Play's confirm, under *Worth knowing first*;
  - in the tooltip of each mod concerned.

  Already live with nothing to switch, Play still starts without a confirm, and
  the card is where it is said.
- **Checked against the machine's Anno 1800 folder.** It finds exactly the two
  missing dependencies the last loader log reports, and one replaced mod (a
  shared piece's 1.0, replaced by 2.2 under a new ModID) that the log confirms
  never loaded.

### 6.3 `Require` counted as load-after (117) — to check

`read_metadata` (`modops.py:4209`) adds `Dependencies.Require` to the mod's
load-after list: "the point of requiring one is to patch what it leaves behind".
The docs only say `LoadAfter` orders, and that `Require` warns. Where the loader
log exists it wins anyway (`loadout.order`). The difference shows only for mods
the log does not name. One real log with a mod that requires, but does not
load after, a mod sorting later would settle it.

### 6.4 Late phase — other IDs dropped (S)

`"*"` in LoadAfter puts a mod in the late phase, and late mods can still order
among themselves: `["*", "post-a"]` loads after post-a. `loadout.order` moves a
`"*"` mod to the end and discards its other entries
(`loadout.py:505-509`, `wanted = set(); break`), so two late mods fall back to
name order. Keep the other entries and sort the late group the way the normal
one is sorted. The loader's source confirms it: late mods go into a second
dependency graph of their own (`loadLast`), sorted by the same rules.

**As built** (`52d5628`). A late mod keeps its other entries, and the late group
is sorted after the normal one. The source also says the reverse: "a mod
without a `*` cannot load after a mod with a `*`". A normal mod naming a late
one used to be moved behind the whole late group. Now that entry does nothing
for it.

**Found on the way, then fixed** (`ccb44ad`). The loader's own sort was not
the Studio's. `DependencyGraph::Sort` does a depth-first walk:

- mods that declare dependencies are placed first, each after what it needs;
- then every mod that declares none, in name order.

The Studio placed each mod at the earliest point its dependencies allowed, and
kept name order wherever it could.

**Measured before changing it**, against the machine's Anno 1800 loader log
(131 mods), with the order worked out as if there were no log:

| | longest run agreeing with the log | pairs the other way round |
|---|---|---|
| the Studio's old sort | 52 of 131 | 3,083 of 8,515 |
| the loader's sort, folder names lower-cased | 90 | 1,290 |
| the loader's sort, each mod's own folder name as written | **131** | **0** |

So there were two differences, and both are fixed:

- **The sort itself.** `order` now follows `ModLoadingOrder::Sort`: a normal
  graph and a late one, each walked depth first, the late one in ModID order. A
  list naming a mod that is not installed still counts as declaring something,
  because the loader asks whether the list is empty.
- **The base key for Anno 1800.** It was lower-cased folder names, with a
  bundled mod sorted under its host's name. Now it is each mod's own folder name
  as written.

With both, the log is reproduced exactly. Anno 117 gets the same sort. Its base
key (category and name, as written) was already measured against a 16-mod log,
but no 117 log with dependencies has been checked (section 9).

### 6.5 `Difficulty` and `GameSetup` (117) — shown in `c73806b`, set in `26f1884`

Of the 371 Anno 117 mods, 361 declare a `Difficulty` (cheat, easier, unchanged,
harder) and 271 a `GameSetup` (RequiresNewGame, SafeToRemove, Multiplayer,
Campaign). They answer exactly the questions someone assembling a loadout asks:

- "Will this break my save?"
- "Is this a cheat?"
- "Can I take it out again?"

Show them in the mod list and on the mod's card, and let a project's new mod set
them, not just default them.

**As built:** each mod's tooltip, in the changes panel and in the mods-folder
dialog, says them in words: *a cheat · needs a new game · not safe to take out
of a save*. It says only what the modinfo states, since a key left out has a
default, and a default is not the author telling you anything. Still to do:
letting a project's own new mod set them (S).

**Built** (`26f1884`, laid out in `e1ba1c7`). The Export tab's details, for Anno
117, have a *Difficulty* choice of the loader's four and four *Game setup* boxes:
- **Your own mod:** kept on the project.
- **A loaded mod of yours:** written into its modinfo, keeping anything else its
  `GameSetup` holds.
- **Somebody else's:** shown as written, including a difficulty the game does
  not know (the census has `Easy`, `normal`, `Gameplay`).

A new mod still starts at `unchanged` and safe to remove. That differs from the
loader's default of false, but 218 of the 272 census mods that say anything say
true.

### 6.6 Anno 1800: `activation.json` and zipped mods — read in `d2fbb9a` and `5f162be`

- **`activation.json`.** `mods/activation.json` with `{"disabledIds": [...]}` is
  the loader's third way to switch an 1800 mod off, after the `-` prefix and
  (in 117) profiles. The Studio reads only the prefix, so a mod disabled this
  way shows as on. Read it (S). Whether to write it too depends on whether any
  mod manager in use relies on it, which is not known.
- **Zipped mods.** The 1800 loader reads mods straight from `.zip` files in the
  mods folder, with three layouts. The Studio's folder read does not open zips,
  so such mods are invisible to the loadout and to conflicts. Reading them in
  place (M) can reuse `core/modzip.py`'s layout detection. The docs warn that
  island files misbehave from zips, which is worth passing on.

**Both built.**
- **`activation.json`** (`d2fbb9a`). The file is read in the installation's mods
  folder and the one under Documents.
  - A listed mod is off, matched by ModID or else folder name, without case. A
    bundled mod goes with its host.
  - Switching one on takes its entry out of the file, keeping the rest, since
    renaming the folder could not switch it on.
  - Switching off is still the `-`.
  - The public 1800 loader source predates the feature, so this follows the
    docs.
- **Zipped mods** (`5f162be`, `core/zipped.py`). Each zip is unpacked into a
  mirror in the Studio's cache, never into the game's folders. The mirror is
  named as the zip is, `-` and all, refreshed when the zip changes, and read as
  one more place mods load from.
  - A zipped mod comes in as `Big Map.zip` or `Box.zip/Mod A`, with everything a
    folder gets.
  - Switching one renames the zip, and the mirror follows.
  - It is never exported back out: a folder beside the zip would load twice.
  - The library conversion leaves zips alone.
  - The tooltip passes on the island warning.

### 6.7 A replaced mod was read as loading — fixed in `f6b2ac5`

Both loaders drop a mod that another loaded mod lists in `Deprecate` /
`DeprecateIds` (6.2). The pre-Play check said so. Everything else in the Studio
still read the mod as loading:

- a mods folder read in kept it as a layer;
- its changes showed in the editor and took part in *which mod wins*.

On the machine's Anno 1800 folder that is *Quay System Full Corners* 1.0,
bundled in *Extended Arctic*. The log shows only 2.2 loading. Use is not rare:
190 Anno 1800 mods and 12 Anno 117 mods deprecate something, mostly shared
pieces renamed between versions.

**Why not drop it at read time**, as `_one_per_mod_id` drops extra copies of a
ModID: a folder read takes in every mod, on or off, and the switches are applied
afterwards. The loader drops a mod only while its replacer is **on**. A read-time
drop would be wrong whenever the replacer is switched off, and would stay wrong
after the switch changed.

**As built**, "replaced" is derived like a switch:

- **`ModProject.replaced()`** works it out from the layers' own modinfo and the
  switches as they are.
- **`not_loading()`** adds it to the switched-off mods for `active()`, which the
  editor, conflicts and the generated ops go through.
- **Its own switch stays on**, and `switched_off()` is unchanged. Writing a mods
  folder back out still writes the mod, since nothing about it is off.
- **In the changes panel** its row is struck through and says which mod replaces
  it. A switch flipped repaints every mod row.
- **The mods-folder dialog** says *replaced* in its *In game* column.
- **A patch of it** is inert, as for a mod that is off.
- **The loader-log check** does not call it missing.

One rule (`dependencies.replaced_origins`) serves all of these and the pre-Play
check. It is not applied to the fourth card's grid, which is about switches: a
replaced mod is on, and Play cannot change that. The card's own line already
says it will not load.

## 7. Lua

### 7.1 Two ways a mod runs Lua

1. **From an asset.** An `ActionExecuteScript` asset whose `<ScriptFileName>`
   names a `.lua` file in the mod, such as `data/scripts/gamespeed_2.lua`, run
   when a trigger fires. There are 1,051 such references in 31 mods: 29 in Anno
   1800 and 2 in Anno 117. This is long established, not experimental. The 1800
   users are mostly shared libraries:
   - Serp's `shared_EventOnGameLoaded_Serp`, `shared_OncePerSessionPerSaveLoad_Serp`
     and `shared_LuaLight`, bundled into Kurila's and Taludas's mods.
   - The UI Scale mods.
   - The coop and campaign mods.
2. **From the modinfo** (Anno 117, "Experimental: … Expect changes with the next
   versions."). A `Scripts` block lists module files and gives Lua snippets for
   four moments: `Init` (after mod loading), `Load` (a game loaded), `Unload`
   and `Tick`. Twelve of the 371 Anno 117 mods use it:
   - Ubisoft's own `infinite-money-ubi` and `infinite-material-ubi` examples.
   - SirLocksley's suite: Goods Finder, Ship Finder, Rename Manager, Harbor
     Arrivals and Specialist Management.
   - Commuter Pier, Anno Companion, Infinite Resources, Goods Amount, and a
     script-testing utility.

   Ship Finder uses both ways at once.

What the census shows about the modinfo form, which the docs do not:

- **Key case.** Every one of the twelve writes `Scripts`, `Modules`, `Init`…
  capitalised, while the docs show `scripts` and `modules`. Anything reading
  them must ignore case.
- **Module paths** are relative to the mod folder: `scripts/commuter-pier.lua`
  is `commuter-pier/scripts/commuter-pier.lua` in the zip.
- **`require`** takes the file's stem: `require("goods-finder")` for
  `goodsfinder/goods-finder.lua`. Two mods shipping modules with the same stem
  would therefore collide.
- **Globals.** `Init` conventionally assigns a global
  (`CommuterPier = require(...)`). The docs warn to "use unique names to not
  clash with other mods", so two mods assigning the same global is a real
  failure.

The docs document no game API at all.

### 7.2 What the Studio should do

Writing Lua belongs in VS Code, where Anno Modding Tools already lives. The
Studio's part is the one thing an editor cannot do: see a mod's scripts
alongside every other mod in the loadout.

- **Tier 1, recognise (S).**
  - A mod's card and its import get a *Scripts* section listing the modules,
    whether each file is there, and the four hooks with their snippets. Mark the
    mod as running code.
  - That mark is the point. A script mod can change anything at runtime, and
    none of it shows in the Studio's asset changes or its *which mod wins*
    answers. The Studio should say where its picture stops, not imply
    completeness.
  - Resolve every `ScriptFileName` to the file it names, in the mod or the game,
    and flag missing ones in the reference panel.
  - Offer *Open in editor*, with the existing program picker.

  **As built** (`d8f05a3`, `core/scripts.py`). Two things came out differently
  from the plan:
  - A `ScriptFileName` is resolved in the editor's *Refers to* column, not the
    reference panel. The reference panel is a graph of GUID links.
  - Files open in whatever the system opens `.lua` with, not the game-launch
    program picker, which is for starting the game.

  What it does:
  - **The changes panel.** A script mod's row says **(runs scripts)** beside its
    name, where the row's other marks go. Put after the change count, it pushed
    that count out of a narrow column.
    - The tooltip lists each module and whether its file is there, each hook
      with its snippet (an unknown hook too), and the files its assets name.
      It also says that none of what they do is in the changes shown.
    - The right-click menu has **Open a script**.
  - **The editor.** A `ScriptFileName` names the mod supplying the file: the
    last in load order that loads and has it, since mods' files lie over one
    another. A double-click opens it. Where no loaded mod has it, the field says
    so without calling the file missing: the game's own file list is not kept,
    so it may be one of the game's scripts.
  - **The mods-folder dialog's** tooltip says what a mod runs.
  - **Checked on the census.** All twelve 117 `Scripts` blocks parse, and every
    module path resolves to a file in its mod's zip, relative to the folder the
    modinfo sits in. One writes `./goods-amount.lua`.
  - **Not done, by decision:** a "runs scripts" note in *which mod wins*
    answers (section 8).
- **Tier 2, connect (S–M).**
  - Across the loadout, flag two mods whose modules share a stem, or whose
    `Init` assigns the same global. Parse only the `X = require("y")` form the
    docs and all twelve mods use; anything else is shown, not judged. This
    belongs in the conflicts view.
  - Let a project's own mod edit its `Scripts` block like any other modinfo
    field.
  - Scaffold a module from the docs' template, with a unique global name
    suggested from the ModID.
  **As built** (`505e23e`, `core/scripts.py`, `ui/scripts_dialog.py`). Three
  things came out differently from the plan:
  - **Globals are read from any `Name = …` statement** in a hook, not only the
    `X = require("y")` form. SirLocksley's mods also keep a handle as
    `SirLocksleyPC03 = SpecialistFinderRC.PC03`, a global all the same, and
    they number those handles 01–04, apparently to keep them from clashing.
    `local` and `A.b = …` are still not globals.
  - **A mod of your own needs somewhere for its Lua.** It has no folder until it
    is exported, so the project keeps a *scripts folder* for it. The Studio asks
    for it the first time you make a module, and export copies each listed
    module from there into the mod. A loaded mod's modules are in its own
    folder.
  - **A mod of your own that is only Lua** used to count as having nothing to
    write, so Play and *Export your changes* would have left it out. It now
    counts as a mod.

  What it does:
  - **The clash check.** Two loading mods that ship a module of the same file
    name (compared without case), or whose hooks set the same global, are
    listed under *Scripts sharing a name* in *Mods reaching for the same
    thing…*. They are counted on the changes panel's conflicts button and said
    in each mod's tooltip. Two copies that are the same file byte for byte are
    listed greyed and not counted. The census has one real case:
    - **Ship Finder and Harbor Arrivals** each ship their own
      `parchment-controls-consumer.lua` (566 and 282 bytes).
    - **No global clashes**, across all ten census `Scripts` blocks loaded
      together.
  - **Editing the block.** The Export tab's details have a **Scripts** row for
    Anno 117, and **Edit…** for any mod whose details are yours.
    - The dialog lists the modules (**New module…**, **Add a file…**, **Remove**,
      **Open**) and has a field for each hook.
    - A hook the Studio does not know is kept as a field of its own. Keys keep
      their author's spelling and order.
    - A clash with another loading mod is said as you type.
    - Edits count as unsaved work, like the other details; they are not undo
      steps, as the other details are not.
  - **Scaffolding.** **New module…** suggests a file name and a global from the
    ModID (`commuter-pier` gives `commuter-pier.lua` and `CommuterPier`, the
    census convention), numbered past anything a loading mod uses. It then:
    - writes `scripts/<name>.lua` from the docs' template, with a function for
      each hook ticked;
    - adds the module under Modules;
    - adds `Name = require("name")` to Init and `Name:Hook()` to each hook,
      after what they already run.

    The file is written on OK, so Cancel leaves nothing. A file already there
    is listed and never overwritten.
  - **Not done:** a note in the pre-Play confirmation. Field clashes are not
    there either; the conflicts button is where both are said.
- **Tier 3, stay out.** No Lua parsing beyond that one line, no running, no API
  help, no debugging. The API is undocumented and the feature says to expect
  changes.

### 7.3 Protecting against the feature changing

The modinfo `Scripts` block already survives export: `_over_declared` keeps
every key the Studio does not own, and the scratch run confirmed a Commuter
Pier-shaped block comes back unchanged. A test now pins that for both games
(`tests/test_scripts.py`).

Everything tier 1 and 2 read should tolerate absence and ignore case. An unknown
hook, say a future `Save`, is shown as a snippet like the others.

## 8. Decisions

Taken:

- **Which languages a Studio-made name is written in** (4.1). One field,
  written into every language file the game has, with per-language editing
  later if asked for. The alternatives were English only or one field per
  language. Most census mods with texts ship a file for every language, since a
  missing entry shows as a raw id in game.
- **How loudly to mark a script mod** (7.2). As built in `d8f05a3`:
  **(runs scripts)** beside the mod's name in the changes panel, and a *Runs
  scripts* section in its tooltip. Nothing in *which mod wins* answers. Decided
  after looking at it built. Clashes between script mods wait for tier 2.

- **Whether to build a FileDB decoder** (4.2). **No, for now** (option A).

  Once explained and measured, the one concrete thing a decoder would give,
  catching tooltip patches a game update has broken, turned out to be already
  in the loader's log. Every op that matches nothing gets a `No matching node`
  line there, which the Studio used to throw away. It now keeps them, for
  every file a mod patches (`d7cc4bb`, 6.2).

  The rest a decoder would give is weaker than it sounds:
  - the merged tooltip as raw structure, not as the tooltip you see;
  - a name for each InfoTip GUID.

  What was measured on the way:
  - The loader's own `xmltest`, bundled with the Anno Modding Tools VS Code
    extension, decodes both games' `export.bin`: Anno 1800's 993 InfoTips
    (4 MB of XML) and Anno 117's (2.7 MB), each in about 0.1 s.
  - It applies a mod's `export.bin.xml` and reports every op that matches
    nothing. All 154 InfoTip ops of the machine's 22 tooltip mods apply
    cleanly, and a deliberately broken one is caught.
  - So if merged tooltips are ever wanted, running the installed `xmltest`
    (option B) is the cheap way in. Porting the two readers to Python
    (option C) is the expensive one.

- **Whether to ship the loader's own tools with the Studio** (option D).
  **No.** Instead, the recommended direction is to find the user's installed
  Anno Modding Tools extension and use its copy of the loader when it is
  there, as a second opinion the Studio never depends on. The design is in
  section 11, and it is built.

## 9. What was checked, and what is not known

Checked:

- The census counts above, from manifests and extracted files.
- That `.cfg`, `.fc` and `.ifo` are plain XML in both games' archives and
  `export.bin` is binary in both.
- The behaviour of the constructs in section 5 and of `condition_holds` with a
  present set, through the real pipeline.
- That a `Scripts` block survives export.
- That `ScriptFileName` paths and `Scripts` module paths are relative to the mod
  folder in the census mods.
- `MaxRepeat`, with the loader's own `xmltest3` and `xmltest` (5.4): again while
  its condition holds, at most N times, silently at N; once in Anno 1800.

Not known:

- Whether the Anno 117 loader patches `.cfg`/`.fc` at all. The docs say 1800,
  and no 117 mod tries.
- Whether `Require` orders a 117 mod (6.3). It matters more now that the sort
  follows the loader's: a mod that declares anything goes first, so reading
  `Require` as load-after moves a mod with only a `Require` to the front.
- Whether the new sort reproduces an Anno 117 log where mods declare
  load-after. Only Anno 1800's has been checked (6.4).
- Whether any 1800 mod manager writes `activation.json` (6.6).
- How the 117 loader resolves a `require` when two mods ship the same stem.
  The tier 2 clash check assumes Lua's own rule, one module per name, and says
  "likely" rather than "will". It is not academic: *Ship Finder* and *Harbor
  Arrivals* ship different files under one name. Loading both and checking
  which `parchment-controls-consumer` each gets would settle it.
- The case of the ModIDs the game registers for `#` conditions. Both loaders
  compare exactly and the Studio compares lowercased (5.1). A mod asking after
  `#Shared-Pools` when the ID is `shared-pools` is read as present here and is
  absent in game.
- How the 1800 loader matches `activation.json` entries: exactly or without
  case, and what a zipped mod's "folder name" is to it. The Studio compares
  without case, and uses the zip's name.
- Where the 1800 loader puts a zipped mod in load order, and how its log names
  one. The Studio sorts it by its zip's name, and a log, when there is one,
  wins.
- What `~//` means in a condition. It is left unanswered.
- Whether some 1800 `.fc` files are stored compressed. Every one sampled was
  text, but the loader has an `.fc` reader of its own (`xml_fc_reader.cc`), as
  FileDBReader does, which suggests some are not.
- Which FileDB versions the loader's `xml_filedb_reader.cc` reads. Moot for
  now: no decoder is being built (section 8), and the installed `xmltest` reads
  both games' current files.
- How the game's own code words Require and Incompatible for Anno 117, and
  whether its 1800 code also reads a requirement of a deprecated ID as met by
  the replacement. The pre-Play check assumes it does, as 117's does (6.2).

## 10. Order of work

1. **Done:** modinfo key names (`37fe12c`).
2. **Done:** conditions combining ModIDs (`1df75fc`).
3. **Done:** before Play (`c73806b`): dependency, incompatibility and
   deprecation warnings, plus `Difficulty` and `GameSetup` in the tooltips.
4. **Done:** a replaced mod left out of the reading (`f6b2ac5`).
5. **Done:** Lua tier 1 and `ScriptFileName` references (`d8f05a3`), with the
   test that pins `Scripts` through export. The marking as built is decided
   (section 8).
6. **Done:** text patches, read for names (`bfb0c44`).
7. **Done:** small ModOp fixes (`52d5628`): flag merges, `AllowNoMatch`,
   late-phase order. Then the loader's own sort, which reproduces the Anno 1800
   log exactly (`ccb44ad`, 6.4).
8. **Done:** InfoTip patches, recognised, with the InfoTips more than one mod
   patches (`e24d8d1`).
9. **Done:** no FileDB decoder (section 8). Instead the loader log's per-op
   warnings are kept and shown with each mod (`d7cc4bb`).
10. **Done:** in-game names for the assets the Studio creates (`0f8a299`).
11. **Done:** Lua tier 2 (`505e23e`): clashes between script mods, editing a
    mod's `Scripts` block, and scaffolding a module.
12. **Done, as asked for:**
    - `activation.json` (`d2fbb9a`);
    - `Difficulty` and `GameSetup` (`26f1884`, `e1ba1c7`);
    - templates, CFG/FC and link files recognised (`2ce419e`);
    - zipped 1800 mods (`5f162be`);
    - `//search` conditions (`77004f5`);
    - `MaxRepeat` (`c285b7c`);
    - `ModValue` path copies (`9714cae`).

## 11. To look into later

### The loader's own tools: found in the extension, not shipped

Filed while deciding against a FileDB decoder (section 8). The point is not
InfoTips but what else the tools would open up.

**Decided (27 September):** the Studio does not ship copies of the tools. The
recommended direction is to find the user's installed Anno Modding Tools
extension and use its copy of the loader when it is there. It is built (see
*Built*, below); the rest of this section is the case for it, the design, and
what was settled. The inventory and the arguments about shipping come after it, kept as
they were written.

#### Why

Two benefits, both substantial:
- **Confidence up front.** Before an export or a Play, the user can know that
  the loader agrees with the Studio's reading, or exactly where it does not.
  Today that is only known after the game has started, from its log (6.2).
- **A guard against drift.** The Studio's reading rules were measured against
  the loader, once. When the loader changes, nothing notices. This session's
  `MaxRepeat` work is the example: the README had the construct wrong, and
  only running the loader's own `xmltest3` caught it (5.4).

#### The design: a second opinion, never a dependency

The awkward part is that the extension will often not be there, so everything
has to work without it. Three rules keep that manageable:

- **The Studio's own reading stays in charge.** Editing, the change list,
  conflicts and export are built on it, as now. The loader only checks it. No
  feature needs the extension to work; some only say more when it is there.
- **One module owns it.** Finding the tool, running it, working around its
  quirks and reading its output live in one place in `core/`. Everything else
  asks it one question: is a loader check available, and what did it say.
- **One place in the UI says it.** Something like *Checked by the game's
  loader: agrees*, *…differs on 3 assets*, or *…not available*, with one quiet
  hint, once, that installing the extension adds the check.

The tests already follow the same pattern: the loader tests skip themselves
when the extension is not installed.

#### What exists already

Most of the machinery is written, for the census, in `tools/modio_loader.py`
and `tools/modio_roundtrip.py`. It:
- finds the newest installed extension under `~/.vscode/extensions`;
- runs a stack of mods through `xmltest`, one at a time on top of the ones
  ahead, and reads back each asset before and after;
- compares that with the Studio's reading and writing of the same mods.

It also carries three workarounds, each found by measuring:
- **Which build:** `xmltest3` for Anno 117, because `xmltest2` does not add an
  `<Asset>` written straight under `<ModOps>`.
- **Where `-p` looks:** a stub at Anno 1800's patch path, so that a mod applied
  first in an Anno 117 stack is found at all.
- **Mod options:** each mod's option values written in, since `xmltest` has no
  way to be given them and reads every one as `false()`.

These must move with the code. The work is mostly promoting this into `core/`
and deciding where it shows, not writing it.

#### Where it pays off, most valuable first

1. **A check at Export and Play.** Run the mod being exported or played
   through `xmltest` against the base file, and compare with what the Studio
   says it does.
   - **For one mod:** seconds, so it can run whenever the mod changes, in the
     background.
   - **For a whole loadout:** slow. Each mod is applied on top of every mod
     ahead of it, and the census needed a one-hour timeout for the largest
     Anno 1800 bundles. So that is an explicit action, run as a background job.
   - **Ops that would match nothing:** the same run reports them. That is the
     log's "No matching node" warning (6.2), known before Play instead of after.
2. **Drift probes.** A small set of test patches shipped with the Studio, each
   pinning one reading rule, such as `MaxRepeat`, flag merges or combined
   conditions. They are rerun whenever a new extension version is found, and
   any disagreement is said.
3. **What unread ops actually do.** For an op the Studio carries unread, the
   loader can say which assets it changes and how, read-only. That fills the
   Studio's largest remaining blind spot without the Studio having to read the
   op itself.
4. **InfoTips, `.cfg` and `.fc`.** A merged InfoTip (4.2, option B) and a patched
   world map (4.3) come almost free.

#### Caveats

- **It is the extension's copy of the loader, not necessarily the game's.**
  All three builds print the same banner ("modloader GU17.1"), and the game's
  own loader log records no version. So "agrees" means "agrees with the loader
  version the extension ships", and the UI should name that version.
- **Where to look.** VS Code keeps extensions in `~/.vscode/extensions`, VS Code
  Insiders in `~/.vscode-insiders/extensions`, VSCodium in
  `~/.vscode-oss/extensions`, and Cursor in `~/.cursor/extensions`. Search all
  of them, take the newest, and allow a path to be set by hand.
- **Windows only.** The tools are Windows executables. The Studio only runs one
  it finds inside the extension's own folder, never one a mod or a path in a
  file points at.
- **The extension can update under the Studio.** Its version has to be read
  each time, and a check made with an older version said to be from that
  version. That is also what triggers the drift probes.

#### First step

The Export and Play check for a single mod, plus the drift probes, as one
piece (S–M):
- **Promote the code.** Move the finder, the stack runner and the comparison
  from `tools/` into `core/`.
- **Search more places.** Add the other editors' extension folders and a manual
  path setting.
- **Show the verdict.** Put it on the Export tab and in the Play confirmation.
- **Ship the probes.** A first set of probes, from the constructs this proposal
  measured.

Unread-op explanation and whole-loadout checks come after it.

**Built** (`9266e2b`):
- **Where the code lives.** `core/xmltest.py` finds and runs the loader, moved
  from `tools/modio_loader.py`, which now only re-exports it for the census.
  `core/loadercheck.py` holds the comparison (moved from
  `tools/modio_roundtrip.py`), the single-mod check and the probes.
- **Where it looks.** It searches VS Code, VS Code Insiders, VSCodium and Cursor,
  takes the newest copy, and *Settings › The game's loader* can name another
  folder. The version it names is the extension's, with the build's own banner
  ("modloader GU17.1").
- **Where it shows.** The Export tab gets a line under *Validation*, with the
  differences in its tooltip. The check runs in the background after an edit
  settles, and only when what would be written has changed. Play reuses a fresh
  verdict or asks, puts a disagreement among the things worth knowing first, and
  never stops for one.
- **The probes.** Sixteen probes, eight per game, run when a new extension
  version turns up and on demand from Settings. A probe the loader does nothing
  with is not counted as agreeing; that guard caught two of the first set, which
  copied between assets that were already the same.
- **In the tests.** `ANNO_STUDIO_LOADER=none` turns discovery off, and the test
  suite sets it, so a machine with the extension does not run the loader behind
  every window.

What was measured on the way:
- **Speed.** One Anno 1800 run is 1.6 s against the 289 MB file, an Anno 117
  run 0.4 s.
- **The first design was too slow.** Its stack of mods ahead grew to 35 for a
  large mod, and the census runner applies each mod in its own run with every
  earlier one first, so that took minutes. The check now does it in one run,
  with all 34 applied first (`only_last`): 2 s for a small mod, under 80 s for
  the largest.
- **Warnings name each mod by its folder.** A run's warnings include those of
  the mods stood on, such as a missing include. The check keeps only the
  checked mod's own.
- **Probes:** all sixteen agree with Anno Modding Tools 2.3.1.
- **The installed Anno 1800 mods:** of 11 checked, 7 agree, 2 of them while
  noting the assets their unread ops change. The 4 that differ are the four
  largest:
  - *Item Sockets*: 199 of 1,254 assets;
  - *Pools and Definitions*: 2 of 537;
  - *Trade Union Rebalance*: 32 of 280;
  - *Vendors only sell common Items*: 4 of 18.

  Traced in one case: *Vendors* replaces a whole `RewardPool`, the loader drops
  an item a mod ahead of it added, and the Studio still shows that item.

**Why those four differed** (`a168663`, `65ab4af`): three reading gaps in the
Studio, and four ways the check compared unlike things. Each was traced to its
op and measured with both games' `xmltest`.

In the Studio's reading, now put right and pinned in
`tests/test_loader_agreement.py`:
- **A removal written without saying which entry** (*Vendors*). The removal of
  an entry an earlier mod added came out as `ItemsPool/Item`, which is every
  item. The replace's removals of the shipped entries had gone first, so it was
  the last one left. It is now keyed against the list as the op found it.
- **A merge set a second field beside an empty one** (*Trade Union*, on
  specialists that ship an empty `<FactoryUpgrade/>`). Both loaders merge into
  what is there:
  - an empty field takes the body's fields;
  - a field holding text keeps it, with the fields after it;
  - text merged into a section goes after its children.

  An older test's example relied on the misreading and was replaced.
- **A replace with several nodes** (*Trade Union*). The loader puts them in last
  first: X, Y, Z comes out Z, Y, X. `add` and the sibling ops keep the author's
  order.

In the check:
- **What a mod reads** (*Item Sockets*). It copies socket counts from the town
  hall, which a mod ahead raises. The stack now includes the mods ahead that
  change what a mod reads, and what those read in turn: 52 mods ahead instead
  of 34.
- **`#ModID` conditions** (*Pools and Definitions*). Inside `xmltest` every one
  is false: it hands the loader an empty table. The check writes in what the
  Studio's reading answered:
  - Anno 117: `true()` or `false()`;
  - Anno 1800: `//AssetList` or its negation, since that loader takes `true()`
    for false.
- **Only the mod's own work** (*Pools*). An asset the loader left alone used to
  be compared with the base game's copy. It is now compared with the Studio's
  picture of the mods underneath, and one the loader was handed differently is
  counted apart. New assets count under their own GUID, not the one they are
  placed beside.
- **What cannot be compared** (*Trade Union*).
  - It and *Extended Agriculture Modules*, by one author, add 17 of the same
    GUIDs, and the game finds only one of each. These are named, not judged.
  - An op the Studio will not read gives six guild house items the GUID 1. A
    group kept whole now records where its unread ops land (`Rule.unread_at`),
    and those assets are counted as that op's.

After a fresh read, all eleven agree. *Trade Union Rebalance* agrees on 293
assets, with its shared GUIDs and retired items reported beside the verdict.

#### The census, with the loader (27 Sep)

The full census round trip (`tools/modio_roundtrip.py --loader`, all 1,218
units) at `3729fd2`, against the last full run on 19 Sep:

| Anno 1800, 854 units | 19 Sep | 27 Sep |
|---|---|---|
| export does in game what the mod does | 672 | 796 |
| export does something else | 124 | 17 |
| Studio reads the mod as the loader does | 554 | 627 |
| Studio reads it differently | 242 | 186 |
| assets the reading and the loader disagree on | 6,419 | 2,906 |
| timed out | 13 | 0 |

Anno 117 was re-extracted on 23 Sep, so it is measured against a run on the
same files: unchanged but for the two fixes below. Three of its mods crash
`xmltest3` (extension 2.3.1, the newest) against the new files, whatever the
Studio does.

The census found two more things to put right:

- **An `AllowNoMatch` op naming several assets** was written once for every
  asset it missed, and once more for itself. *S.L.O.P. - Attributes* halves
  Belief in 25 assets, and the game halved it three times over. It now goes
  out once.
- **A search condition answered from a picture with holes in it.** *BT Quests
  Instead Preferred Goods* fills a pool by copying a list, which the Studio
  cannot read, then adds quests only if that pool has entries. The Studio
  answered "no" from its empty pool, and the export left the quests out in
  game. A search that names an asset an unread op has aimed at is now
  unanswered, and the op goes out as written. One that names no asset is still
  answered: holding all of those back left 157 more ops unread, most of them
  answered rightly.

Those eight were then put right, each for its own cause:

- An edit to an asset a kept op made landed in that op's payload and as a
  change of its own, so the Studio showed it twice (*AI Buffed* ×2, *Military
  Attention - Legendary AI*).
- An addition to the asset itself, at an empty path, was dropped from the
  Studio's view (*Orchard Rework*'s `<Template>`).
- *Hackner's Mantained Needs* (×2) moves needs up a list by copying them in
  and removing the second of each; in game the export had every need twice.
  A `Content=` copy of several nodes is now read (both loaders copy them all,
  measured), a removal after an unread op on that asset goes out as written,
  and an exact twin is keyed by its position.
- A search that counts positions across assets is said as unread rather than
  as matching nothing (*A_Modified_Ornaments_Tab*).

#### Two assets under one GUID

The rest were mods creating an asset under a GUID something already has. The
loader finds the one **first in the file** -- not the first made -- and each
`addNextSibling` lands straight after the asset it names, a later one nearer
it (measured, both games). Two things were built on that:

- **A warning when a mod is read** (`core.repeats`): the mod's row is marked,
  its tooltip names the asset the game finds, the conflicts button counts
  them, and *Mods reaching for the same thing…* lists them. Identical copies
  and a mod removing all but the last itself are said but not counted.
- **Against every mod on this PC**, not only the loaded ones: a mod imported
  on its own is only ever played beside others. The New asset dialog's scan
  of every mod the game can load (from `main`) now names every owner of each
  GUID, and runs in the background once a project is open and after each
  import; the loaded mods, what they bundle and other copies of them are left
  out. Which copy the game would find is not said: that needs a load order the
  project does not have. *Item Overhaul (Kurila)* read alone shows the 17
  GUIDs its *Trade Union Rebalance* shares with *Extended Agriculture
  Modules* -- names in one, items in the other.
- **The reading follows it.** A later op aimed at such a GUID edits the copy
  first in the file; a mod's copy of a shipped asset no longer takes over from
  it, so an edit meant for the game's own asset stops going into a copy
  nothing reaches.

Across the census: 24 mods take 59 GUIDs twice (32 in Anno 1800, 27 in Anno
117), almost all by accident -- *Local Department* gives two decree buffs one
GUID; *Cultural Expeditions* reuses its botanical sub-mod's; *Kurilas
Modlist* has a shared description under a *New World Docklands* trigger's.
Another 96 are identical copies and 10 are tidied by the mod itself. The
census diff shows only the last asset of a GUID, so it cannot score this; the
tests and the loader measurement do.

The census itself now takes minutes: 23 for Anno 1800 from cold, 8 with the
loader's answers cached, where it took five to eight hours. Each stack goes
through the loader once instead of once per mod, what it answers is cached by
exactly what it was given, the narrow export is measured only on asking, and
the longest units start first.

#### Settled (28 September)

- **What a disagreement does: warn, never block.** The loader is the
  extension's copy, which may be newer or older than the game's.
- **When the check runs: at Export and at Play.** It already ran only while the
  Export tab is showing (after an edit settles, and only when what would be
  written has changed) and at Play's confirm; that stays.
- **Can a disagreement be acted on?** The worry was a verdict saying *differs*
  with no way to find what causes it. It named each asset and showed a diff in
  a tooltip, but not the op. Now each differing asset is a *Validation* row
  naming the fields where the two part and the mod's changes on those fields,
  and a double-click opens the change (`loadercheck._parted`, `_behind`). A
  change is named when its path and a differing field lie one inside the other.
  Where none does -- the field was reached by a copy or a search -- every
  change to the asset is named, and where the mod has none on the asset, the
  row says a copy or a search reached it. Narrowing further, to one op, would
  mean running the loader again with ops left out; not needed yet.
- **An extension that trails the game.** The Marketplace's newest Anno Modding
  Tools is 2.3.1, from 21 Sep 2025, a year older than the Anno 117 files the
  `xmltest3` crashes are against. Nothing the Studio can update, but it can
  notice when an update arrives: once a day, with the extension installed, it
  asks the Marketplace (naming only the extension) and says so on the status
  bar, in Settings, and beside a check the loader could not make.
- **Telling the extension's author** stayed open here; it is settled below.

#### Put right (28 September)

- ***Industrial Cities*** lost a menu entry in its export, in game as well as
  on re-reading. Its compact menus add an `<Item>` to one menu, copy it into
  another with a several-node `Content=`, then remove it from the first. The
  removal folded into the addition, which came out empty -- right for what the
  first menu ends up as, wrong for the copy in between, which goes out as
  written and runs against the menu as it was then. A copy going out as
  written now freezes the payloads it reads from, as a rule reaching in
  already did, and later edits are ops of their own. Also: an addition the mod
  took every node of back out itself (a placeholder `<Item>` added and
  removed) is no longer a change.

  The census at `146e003`, against `9c650fc` on the same game files: Anno 117
  unchanged, unit for unit. In Anno 1800, *Industrial Cities* round-trips
  identically, and so do seven more units that had only the same effect; the
  changes a re-read of the export no longer finds fell from 782 to 32, almost
  all of them those empty additions; the loader's numbers are unchanged but
  for *Industrial Cities*' one asset. One unit went the other way, *Kurilas
  Modlist*, from identical to the same effect: an op there replaces a list
  entry together with the key it is found by, and an addition inside it is
  filed under the old key on the first read and the new one on the second.
  Both are right for the order they run in, and the game gets the same.
- **Before Play** says GUIDs taken twice among what it is about to load, on the
  fourth card and in the confirm. The confirm's *Worth knowing first* is now
  grouped by kind; past eight findings, each kind is a count and its first two,
  and *Show Details* has the rest. A bundle's row in the changes panel counts
  what its sub-mods take twice.
- **The scan of every mod on this PC, in the live app**: a copy of the Anno 1800
  loadout (134 mods, 12,963 changes) opened in a real window with the real
  mods folder and library. The scan ran in the background in 0.9 s (its cache
  warm), found 1,308
  GUIDs made by more than one mod on the PC, and marked *Military Attention -
  Ships*, whose 8853940 *Buildable Salvagers* in the library also makes. It also
  turned up *Extended Agriculture Modules*' farm and animal modules both making
  1500300934, which its folded row did not show -- hence the bundle count above.

#### Built (28 September, later)

- **What an unread op does** (`loadercheck.explain_unread`). The mod goes
  through the loader as Export writes it, on the mods it builds on, then once
  more for each unread op left out; what differs is that op's effect in game.
  Shown in the unread ops dialog, per mod on asking, kept for the session.
  Tweak markup carried with the ops is not asked about. Measured on the Anno
  1800 loadout: *Easy Seed Search*'s seven ops all do nothing there -- they
  unlock seeds from mods that loadout does not have -- and *Industrialized Low
  Tier Production*'s unread groups change one and six assets. About 10-15 s an
  op under a stack of twenty mods.
- **The stack a check stands on** now counts a mod that creates an asset by
  the new asset's GUID too, not only the one it is placed beside, so a mod
  editing another mod's new assets stands on it.
- **The whole loadout at once** (`loadercheck.check_loadout`, *Check › Check
  the whole loadout with the game's loader…*). One `apply_whole` run of every
  loading mod, then every asset compared with the Studio's picture, each
  difference named with its mods and changes. It asks first, saying it takes a
  while: 46 s for 114 Anno 1800 mods. Measured on the user's loadout project:
  248 of 2,982 assets differ; a fresh read of the same mods folder through the
  census harness (`--folder`) gives 228 -- so the check measures real reading
  gaps in that loadout, not its own noise. The first version said 485: mods
  updated or renamed on disk since the project read them (*Item Sockets* read
  at 1.6.2, 1.7.1 on disk; *New World Docklands* renamed) were run by the
  loader against the Studio's reading of the old files. Those are now left out
  of both sides and named, with a pointer to reading the folder again.

#### Settled

- **Telling the extension's author** is not a permission question: finding a
  separately installed extension and running its tools locally is fine, and
  only bundling them would be. What is left is a courtesy -- the Studio relies
  on `external/` and `xmltest`'s command line, neither promised -- and a bug
  report for the `xmltest3` crash.
- **Play with nothing to switch** goes straight ahead, as it does; the card
  says what is worth knowing.

#### Which build for Anno 117, looked at again

The Studio chose `xmltest3` for Anno 117 by measuring: `xmltest2` does not add
an `<Asset>` written directly under `<ModOps>`. Reading the extension's own
code since (`dist/extension.js`, 2.3.1) adds what that left out:
- **The extension runs `xmltest2` for Anno 117** (`GameVersion.Anno8`) and
  `xmltest` for Anno 1800. `xmltest3.exe` and `_xmltest.exe` ship in
  `external/` but nothing calls them.
- **The loader's docs** (`doc/modloader-changes.md`, `devel/117-changes`)
  describe only the `<Assets><Asset/></Assets>` form, which both builds add.
  The bare form is not documented, but 211 files in 105 of the 364 Anno 117
  census mods use it (7,224 assets). Those mods presumably work in game, so
  `xmltest3` looks closer to the game there.
- **The three crashes are `xmltest3`'s alone.** `xmltest2` patches all three
  mods (56, 19 and 21 assets). Two crash `xmltest3` as published. The third
  crashes only once the Studio writes the mod's option values in: three
  `ModValue`s in one added asset. A fourth, tiny crash turned up on the way: a
  `ModValue Insert="self::node()"` in an added asset overflows `xmltest3`'s
  stack. Repros and a draft issue for `anno-mods/vscode-anno` are kept outside
  the repository.

So neither build matches the game on everything. **Built:** the Studio keeps
`xmltest3` for Anno 117, and a run it crashes on (a Windows exception code,
not a loader error) is run again with `xmltest2` from the same extension
folder (`xmltest.FALLBACK`), in the whole-stack run and mod by mod alike. The
answer records which runs it took, the cache format moved to 2 so crashes kept
under 1 are asked again, and every verdict built on it says so. Rerunning the
three census units: all three now have an answer, and their round trips are
the same. Their readings differ from `xmltest2` on 1, 1 and 13 assets, and
each traced is `xmltest2`'s: a `ModValue` in an asset being added left as
written (*Villa & Officium*), and a field merged with `self::node() * 1.5`
left empty where the Studio has 45 (*Simplified Raw Production*). So the
sentence a fallback verdict carries names both.

#### Still open

- The 228-248 differences on the Anno 1800 loadout are reading gaps to work
  through; the first rows are *The perfect specialists & items pack* setting
  `LogisticNode/TransporterCountMax` on airship hangars, which the Studio shows
  as an empty `LogisticNode`.
- Filing the `xmltest3` issue (drafted, see above).

#### Before the decision

As written before the direction above was taken.

**What exists.** The Anno Modding Tools VS Code extension (2.3.1 on this
machine) ships, in `external/`:

- `xmltest.exe`, `xmltest2.exe`, `xmltest3.exe`: the loader's patching code on
  the command line. Use `xmltest` for Anno 1800 and `xmltest3` for Anno 117;
  `xmltest2` does not add a bare `<Asset>` (see `tools/modio_loader.py`).
  About 0.6 MB each.
- `FileDBReader`, a .NET program with its format files: the community's
  FileDB converter.
- `AnnoFCConverter.exe`, for `.fc` files.
- `annotex.exe` and `texconv.exe`, for textures (`.dds`).
- `rdm4-bin.exe`, for models (`.rdm`).

The extension's `LICENSE.txt` gives a licence per tool. The extension itself,
texconv and rdm4 are MIT; the others were not read yet. The loader's source,
which `xmltest` is built from, is MIT too.

**What bundling `xmltest` alone could do.** The Studio uses it today only in
`tools/`, for the census.

- **Ask the loader, not the Studio's reading of it.** Check an export against
  what the game would actually do before it is written. The census does this
  with `tools/modio_loader.py`, and it is how most of the reading rules in
  `core/modops.py` were measured.
- **Say which ops would match nothing, before Play**, for every mod, instead of
  after the game has started. That is 6.2's check done in advance.
- **Show a merged InfoTip** (4.2, option B) and patched `.cfg` / `.fc` files
  (4.3), without a decoder of our own.
- **Read `templates.xml` patches as the loader does** (4.4).

**What the other tools could add** is further from what the Studio does now:
previewing textures, and knowing what a model or feedback file a mod replaces
is.

**Against:**
- The tools are Windows-only binaries, so the Studio would stop being pure
  Python there.
- They have to be kept in step with the loader and the extension's releases,
  and the right build chosen per game.
- They add a launch-a-process dependency, which may trouble anti-virus on some
  machines.
- Finding them in an installed extension, as the census tooling already does,
  gets most of the benefit without shipping anything. That is worth weighing
  first.

**To settle first:**
- the remaining licences;
- whether the extension author would rather the Studio found the tools than
  shipped them;
- which of the uses above is wanted enough to justify it.
