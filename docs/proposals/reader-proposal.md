# The reader built on what an op is: a proposal

How the reader in `core/modops.py` is rebuilt as the nest that section 4 of
the [ops-as-written proposal](ops-as-written-proposal.md) sets out, and how
the rest of the Studio comes to ask one description of an op instead of
working parts of it out for itself.
Issue 131
asked for the reader. The scope here is wider, as asked when the work began:
everything in the repository that does not treat an op as a target, a
payload, a condition and a type.

Written on 10 October 2026, against `main` at `a6b7d5a`. The sources were:

- a reading of the reader;
- every module that reads or writes an op, in `core/`, `ui/` and `tools/`;
- the tests;
- the loaders' own source:
  [jakobharder/anno-mod-loader](https://github.com/jakobharder/anno-mod-loader),
  `main` at `5a4464d` for Anno 117 and `stable/anno1800` at `8326b7c` for
  Anno 1800, file `libs/xml-operations/src/xml_operations.cc` in both.

Paths are relative to `anno_modding_studio/` unless they start with
`tests/`, `tools/` or `docs/`.

**Status (10 October 2026).** Phases 1 to 3 are built, but for one row.
`tools/op_counts.py` takes section 4's counts, and `core/modop` is the one
description of an op that the reader and the rest of the Studio ask. Four of
the five rows that differ are put right, and the fifth, Anno 117's merge
matching names in any case, in phase 4. Phase 2 was run against the census
of both games with the loader, and no unit changed status; the counts were
taken for both games with it, Anno 117's 25,502 ops in 430 mods among them.
The narrow reading is gone (phase 3): the asset pane reads a replace of each
node that differs as the op it is, and takes the changes under it. Phase 4
has begun with scopes as a tree: the reader enters a group once and runs what
is inside it there, and `MaxRepeat` is a loop, its number read as the loader
reads it. An op runs from one starting point at a time, nothing asked of it
again for each. What a type does to a node is in one place,
`core/applying.py`, ported from the loader's `Apply` line by line, and the
reader's merges, its replay and the asset the Studio shows all go through it.
`read_ops` and `read_one` are the one way into the reader: a mod read alone,
a mods folder, the ops typed for one asset, a pattern op put to chosen assets
and an op the Studio wrote switched off are all read through them.
`modops.py` is split in three: `modops` (the files, the ops and what each
is), `reader` (the nest) and `recording` (the diff, the keying and the
payloads), and the core import cycle is three modules, down from four.
Folding is what is left: tried and taken back, see section 5.

## 1. What is there

### The reader

`modops.py` is 7,854 lines. `Translator` is 2,780 of them.

- **It applies a type by working out a diff.** `_edits_for` turns an op into
  a list of `_Edit`s. `_apply` then changes the working tree and records the
  `Change` in the same act. No step applies an op to a node by its type, as
  the loader does. Recording and applying are one thing.
- **The nest is not the code's shape.**
  - The ops are one flat stream. A group's "stop" is every later op asking
    the group's state again (`Translator.run`, `_state_of`).
  - A list of GUIDs is split by `_run_each` calling `_run` again for each
    one, so every check made of the op is made again for every asset.
  - Type is switched on in about eight places, among them `_run`,
    `_run_one`, `_edits_for`, `_wanted`, `_apply`, `_change` and `_replay`.
- **The description is not what the reader runs from.** `describe(op)` is
  read only by the screen. The reader asks its own checks instead:
  - `_has_computed`, in five places;
  - `_asks_the_loadout` and `_asks_a_setting`;
  - `_targets_otherwise` and `_reaches_other_assets`;
  - the `searched` flag, which is set as the op is run and read in sixteen
    places.
- **About 600 lines serve only the narrow reading.** They are:
  - `_check_equivalence` and `_reproduces`, with the small translator it
    runs;
  - `_keep_what_lodged_there` and `OpResult.lodged`;
  - `_is_the_op` and `touched`;
  - the fifteen branches on `narrow`.
- **About 800 more serve both readings.** The diff and its keying (`_diff`,
  `_Sheet`, `_Span`, `_keyed_as_it_stands`, `_usable_key`) are what a narrow
  change is. The as-written reading still needs them, since it records an op
  as the changes it comes to.

### Everything else

At least fifteen places outside the reader work out a property of an op for
themselves, with a regular expression or a fresh `read_op`.

| Property worked out | Where |
|---|---|
| Which assets an op names | `changes._names_alone`, `xmlview.names_only`, `xmlview.unread_reaching`, `infotips._targets`, `loadercheck._AIMED` |
| Its type | `keptops.claims_more`, `keptops._op_attrs`, `typedops._aim`, `sidefiles._kind`, `imya._type_of`, and `ui/changes_dock.py`, which tells a group by `startswith("<Group")` |
| What its payload is | `xmlview.creates_only`, `typedops._is_mark`, `texts.read` (which looks for `Content`), `ui/pattern_dialog.py` (which looks for `Insert`) |
| What its condition asks | `texts._holds`, `xmltest.substitute` |
| Its path | `loadercheck._OP_PATH`, `discovery._aims_at_identity`, `repeats` |

- **The type has three tables:** `modops.CLASSIC_TYPES` and
  `SHORTHAND_ATTRS`, `changes.OP_*`, and `games.Game._SHORTHAND`.
- **Some of these answers already disagree with each other.** `Op.searches`
  is set from "has no GUID". `described().target` is set from "no GUID, or
  `Property=` or `Template=`". So an Anno 117 op with `Property=` and a
  `GUID` is a search to one of them and not to the other.

## 2. What the loader does

Section 4 of the ops-as-written proposal gave the nest as the reader assumed
it, and asked for two things to be checked against the loader's source:

- whether an op's `Condition` is asked once for each GUID;
- where the two games part.

Both are answered below. The nest as the source has it:

```
for each element in the file, in order:
    if it has Skip, whatever its value: pass it by
    if it is a Group or an Include:
        if its condition does not hold, asked of the document: pass it by
        do: run what is inside, in order
        while (Anno 117) MaxRepeat allows another round and the condition still holds
    for each starting point: the document, or each GUID in the list as written
        if Condition does not hold, asked from here: go on to the next
        if Content: fetch it once, from here; if it finds nothing: go on to the next
        for each node Path finds from here:
            copy the payload in by Type
            (Anno 117) work each ModValue out where its copy lands
```

- **An op's `Condition` is asked once for each GUID, from that asset.** A
  GUID list becomes one operation per GUID before anything is run (117
  L924–928; 1800 L725–729), and each operation builds its own condition from
  its own GUID (117 L568; 1800 L245). So the reader's assumption holds.
- **`Content` comes before `Path`.** It is fetched once for each starting
  point (117 L814–823; 1800 L593–626). The same copies go to every node the
  path finds.
- **A `ModValue` is worked out once for each node it lands in** (117
  L859–880, `ResolveModValue` L1021–1087). `Insert` is read from the parent
  of the node it lands in.
- **A group's condition is the document's.** A group is built with no GUID
  (117 L941; 1800 L736), and it is asked once before anything inside runs
  (117 L788). `MaxRepeat` runs the group, then asks again; the value is held
  to 1 to 1,000 (117 L570–571, L793–800).

### Rule by rule

What the Studio does was read in the code and, where the table says so, run
against it. "Agrees" means the Studio reads the op as the source says the
game does, or carries it unread where it does not.

| Rule | Anno 117 | Anno 1800 | The Studio | |
|---|---|---|---|---|
| `Skip` switches off an op or a group whatever its value | L912 | L714 | A group: whatever its value (`_group_scope`). An op: only `1`, `true` or `yes` (`is_skipped`). Run: `Skip="0"` and `Skip=""` on an op are read as on | **Differs** |
| A GUID list is split on the comma, nothing trimmed | L925, `str::split` | L726, `StrSplit` | `SourceOp.guids`. `infotips._targets` trims | Agrees, but `infotips` **differs** |
| `Condition` is asked once for each GUID, from that asset | L568, L788 | L245, L580 | `_run_one`, for each GUID | Agrees |
| A condition's path is relative to the asset unless it starts `//` or `@` | L197–208 | Relative only with a leading `/` or `~` (L186–200) | `/…` and `~/…` are relative. A path with no slash (`Values/X`) is not answered and the op is carried (run) | Agrees |
| The short form reads its condition from `<Values>` | L339–341, L485 | Has no short form | `from_values` | Agrees |
| `#` and `$` in a condition | Replaced in the text wherever they stand; an unknown one is `false()` (L215–262) | Only a whole condition `#ModID` (L182–186) | `combines`, `_combined_holds` | Agrees |
| `Path` starting `//` or `@` leaves the GUID aside | L197–203 | Kept: `//X` is searched inside the asset (L223–226) | `_as_117_reads` drops the GUID for `//`. A 1800 op with a GUID and `//Radius` is read inside the asset (run) | Agrees |
| `Content` is fetched once per starting point, before `Path` | L814–823 | L593–626 | `_content_payload`, once per asset, before the target | Agrees |
| `Content`'s path is relative as a condition's is | L575 | L249 | `_content_payload` resolves `@GUID`, `//` and the op's own asset itself | To check |
| `ModValue` is worked out where each copy lands | L1021–1087 | Has none | `freeze_computed`, for each node | Agrees |
| A group's condition is the document's, asked once | L788, L941 | L580, L736 | `in_document`, once on the way in | Agrees |
| `MaxRepeat` runs the group, then asks again, at most 1,000 times | L570–571, L793–800 | Runs once | `_rounds`, and a loop in `Translator._walk_scope` | Agrees |
| An `Include` is a group, condition and all | L460–462, L945–962 | L740–758 | A file included on a condition is a scope | Agrees |
| Types | `add`, `assets`, `addAfter`, `addBefore`, `addNextSibling`, `addPrevSibling`, `remove`, `replace`, `merge`, in any case (L489–520) | `add`, `addNextSibling`, `addPrevSibling`, `remove`, `replace`, `merge` (L372–401) | `CLASSIC_TYPES` also takes `append` and `prepend`, and does not know `addAfter`, `addBefore` or `assets`. Run: `Type="append"` is read as `addNextSibling`, and `Type="addAfter"` is carried unread | **Differs** |
| Short form | `Add`, `Append`, `Prepend`, `Remove`, `Replace`, `Merge` (L530–547) | None | `SHORTHAND_ATTRS` also takes `AddNextSibling` and `AddPrevSibling`, and reads the short form for either game. Run: an Anno 1800 op written `Replace="…"` is read as a replace | **Differs** |
| An `add` with no path, GUID, property or template adds assets | L550–552, at `/AssetList/Groups[last()]/Group[last()]/Assets[last()]` (L561–562) | Has none | `_adds_a_block`, at `BLOCK_PATH` | To check |
| An added node with a `BaseAssetGUID` goes in after its base asset | L1240–1264 | Appended where the path says | `_beside`, `_place_beside` | To check |
| Merge matches a child by its name and its place among those of that name, and appends what it does not find | L1096–1232 | L797–851 | `effective._ensure` | Agrees |
| Merge matches names in any case | Yes (`pugih::equals`) | No (`strcmp`, L807) | As each loader does (`applying.named`), since phase 4 | Agrees |
| `ModItem` | L1130–1211 | Ignored | Read in 117, carried in 1800 | Agrees |
| `AllowNoMatch` counts if it is there at all | L569 | L246 | `_allows_no_match` | Agrees |
| `ModOpID` | Nothing to the loader | Nothing | A label for a tweak tool | Agrees |
| `Property="X"` aims at every `X` under an asset's `<Values>` | L348–350 | Has none | `_property_path` | Agrees |
| `Template="X"` aims at a template | L357–369 | Has `Template` | Only `_targets_otherwise` knows it; not in `KNOWN_ATTRS` | To check |
| `.[cond]` at the head of a path is `self::node()[cond]` | L371–375 | Has none | Not rewritten; the path does not parse, and the op is carried | To check |

"Run" means the op was read with `Translator` against the suite's small
assets file (`tests/support.py`, `IMPORT_ASSETS`), on 10 October 2026.

### Where the source is not the last word

- **The Anno 117 game-side loader is not public.** It was taken out of `main`
  in `0be06c9`. It is the part that reads a modinfo and its options and
  passes in which ModIDs are loaded and what each setting holds. Every rule
  above is the engine's, which the game and `xmltest` both use.
- **The public Anno 1800 loader never passes in the loaded ModIDs.**
  `libs/external-file-loader/src/mod_manager.cc` on `stable/anno1800` calls
  `Apply(game_xml)` with none (L494), so in that source
  `Condition="#ModID"` could never match. Either the DLL players have is built
  from other source, or this is a bug. The game is the judge here, not the
  source.
- **Which engine an `xmltest` build carries is not known.** The census runs
  the `xmltest.exe` and `xmltest3.exe` that ship with the *Anno Modding Tools*
  extension. If the Anno 1800 one is built from `main`, it accepts the short
  form, and the census would agree with the Studio about an op the game
  passes by. Each row marked **Differs** wants a probe in `loadercheck`,
  run against both `xmltest` builds and once in each game, before the Studio
  is changed to match.

## 3. One description of an op

An op is parsed once, where `_read_op` parses it today, into the four parts
and what sits beside them. Each part says whether it is written down or
worked out at load, and holds what it is worked out from:

| Part | What it holds |
|---|---|
| **Type** | One of a fixed list, named once for both games, with whether this game's loader knows it. The list replaces `CLASSIC_TYPES`, `SHORTHAND_ATTRS`, `changes.OP_*` and `games.Game._SHORTHAND`, and `games` keeps only how each game writes it. |
| **Target** | The starting points: the document, a list of GUIDs as written, a property, or a template. The path from them, and whether it is a search, a pattern inside the assets it names, or a name. |
| **Payload** | Literal; a copy, with the `Content` path; computed, with each `ModValue` expression; or flags. Whether it makes assets. |
| **Condition** | Its text, whether it is negated, what it asks (the document, the loadout, a setting), and where its path starts, by the rules of section 2. |
| **Beside the patch** | `Skip`, `AllowNoMatch` and `ModOpID`, each as the loader reads it: whether the attribute is there. |
| **Scopes** | The groups and conditional includes around it, outermost first. |

`Described` is what the screen reads of this, and stays as a view of it.

Every place in section 1's table asks the description. So do the reader's own
checks, which go: the five `_has_computed`, `_asks_the_loadout`,
`_asks_a_setting`, `_targets_otherwise`, `_reaches_other_assets` and
`searched`. `Op.searches` is read from the description.

`Op.kept` has meant "why it could not be narrowed" since 9 October. It gets a
name that says so.

The rows marked **Differs** are put right here, each with a test that fails
without the fix and, for each, a probe as section 2 says. The two fixes that
show a change the game does not make come first: `Skip="0"` on an op, and the
types and short forms the game passes by.

## 4. The narrow reading

The narrow reading (`Translator(narrow=True)`, `Reading(narrow=True)`) has one
user left: the asset pane of the XML view. Typed text there is turned into
`replace` ops (`typedops.read_asset`), which are read narrow to find the
fewest changes that make the asset say what was typed. *Write as narrow
ModOps* does not read anything again. It uses what is already under the op.

The asset pane does not need ops to do that. It has the asset as it stands
and the asset as typed, and the diff between them is the changes. The diff is
already in `modops` (`_diff`, `_pair_by_key`, the keying), and section 5
gives it a module of its own.

Once the pane asks the diff for its changes, the following go, about 600
lines:

- `narrow=`, `reads_narrow` and `Reading.narrow`;
- `settle_computed` and `settle_copies`, as fields and in `ModLayer.read_as`;
- `_check_equivalence`, `_reproduces`, `_keep_what_lodged_there`,
  `OpResult.lodged`, `_is_the_op` and `touched`;
- `KeepAsWritten`, which `keeps` still reads;
- the census's narrow mode, with its `--settle-*` flags.

The 64 tests marked `narrow_reading` (52 in `tests/test_import.py`) each test
either the asset pane or narrowing. The first are pointed at the pane. The
second go, as 43 did in phase 3 of the ops-as-written proposal.

The ops-as-written proposal left the narrow reading's future open (its
section 11). This proposal ends it.

## 5. The reader as the nest

### Three parts in place of one

The nest of section 2 becomes the reader's shape, with each part in one place:

1. **Scopes as a tree.** A group is entered once, and what is inside it is
   run inside it. A group that does not hold passes everything in it by, so
   nothing inside has to ask again. `MaxRepeat` becomes a loop, where it is
   rounds of nested scopes today.
2. **A starting point at a time.** Each GUID is its own starting point,
   as the loader has it, and nothing is asked of the op again for each.
3. **Condition, then `Content`, then path.** For each starting point, in the
   loader's order, by the rules of section 2 for the game reading it.
4. **Applied by type, in one place.** The payload is copied into the
   document by type, the way `Apply` does it (117 L851–884): append, insert
   after, insert before, replace, remove, and the merge of L1089–1232. Then
   each `ModValue` is worked out where its copy lands.

Recording what the op came to is a second step, apart from the first.
Today's `_apply` does both. The changes under an op are worked out from the
document before and after the op runs, by the diff and keying the reader
already has. That separation is what lets the first part be read beside the
loader's `Apply`, line for line, and checked there.

### Reading one op

`read_one(op, document, game)` becomes a public answer: what one op does to a
given document, and the changes it comes to. It replaces three readings that
each build a whole `Translator` today:

- `typedops._read`, for the ops typed for one asset;
- `keptops.expand`, which runs one pattern op against chosen assets;
- the stale reading, where it reads only the ops reached.

As built, it is `read_ops(ops, document, ...)`, with `read_one` the same for a
single op, and the game is each op's own. The stale reading already went
through `analyse_all`, which reads through `read_ops` now, as `analyse` does;
the fourth caller was the op the Studio wrote switched off, read aside
against the shipped file.

### Folding

An op that edits what its own mod has just added is shown inside that
addition (`OpResult.folded`, `Op.folded`). The ops-as-written proposal tried
to give such ops changes of their own. That showed five assets wrongly,
because the paths into the added nodes did not hold. With the document
applied before anything is recorded, an added node is in the document when
the next op is recorded, so its path can be keyed as any other.

Tried on 10 October 2026 and taken back. Each edit to what an earlier op of
the mod added was made a change of its own, keyed against the document as it
stood, with the addition's payload frozen before it. Against `main` with the
loader, the census then read five mods that had agreed with the loader as
making what the loader does not: *Early Game (Kurila)*, *Pineapple Luxury
Good*, *OP Tax Royalty Item Pack*, *Less OP Taxes Item Pack* and
*Orchidelirium*. Two examples are a `<ModuleIrrigation/>` on 1010271, and
extra `Item` entries on 120055 and 502043. Those five are the cases a second
try has to read right first. (Two mods that crashed in the same run,
*Mayan Treasures* and *More Festival Types*, crashed without folding too: a
whole-node swap hung off a neighbour the same op took out, fixed apart.)

### The module

`modops.py` is split, without growing the import cycle:

- `modops`: finding and reading patch files, the op and its description;
- the reader: the nest;
- recording: the diff, the keying and the sheets.

Each is named in `docs/architecture.md`.

As built, the reader keeps `analyse`, `analyse_all` and the rest that read a
mod or a folder, since they run the nest; `modops` keeps `Reading`, how a mod
is read, because finding its files asks it. For the cycle not to grow,
`modops` no longer imports `generator`: how an op is switched off
(`switched_off`, `switched_on`) and the Studio's name moved to `changes`,
beside the mark they write.

## 6. Phases

Each phase is one or more PRs, and leaves `main` working.

1. **This proposal, and `tools/op_counts.py`.** The Anno 117 counts are
   taken with it, and section 4 of the ops-as-written proposal gets them in
   its status line.
2. **One description of an op** (section 3). Probably two PRs: the model,
   the reader's checks and the fixes first, then everything outside the
   reader.
3. **The narrow reading leaves** (section 4).
4. **The nest** (section 5), in this order:
   - scopes as a tree;
   - a starting point at a time, with condition and `Content`;
   - applying apart from recording;
   - folding;
   - `read_one`, and its three callers;
   - the split.

The comments, docstrings and test names that still say "rule" of a kept op,
some six hundred, are put right in whichever phase touches them. None of them
is changed as a sweep.

## 7. The check

The issue sets the census as the check: the asset as the Studio shows it
against the asset as the game's loader leaves it, unit by unit, before and
after (`game_reading` in `tools/modio_roundtrip.py`). No unit should move,
except where a fix in phase 2 says which units it moves and why.

- **Phases 2 to 4 change the reading, so each wants a census run** of both
  games before it merges, against the run of `main` before it.
- **The census needs `xmltest` and the extracted game files.** They are on
  the Windows machine the census was taken on. The suite runs anywhere, and
  so does the half of the harness that does not ask the loader.
- **Phase 4 is also read beside the loader's source**, with each step of the
  nest naming the lines of `Apply` it follows.
- **`tools/bench.py` is run for phases 2 and 4**, since both change how a
  reading is made. Reading the bench folder went from 183 s to 88 s when the
  replay went; the nest should not give that back.

## 8. What is not settled

1. **The rows marked To check** in section 2. Each wants reading in the code
   and a probe before it is called one way or the other.
2. **`#ModID` in Anno 1800.** Whether the game answers it at all, given the
   public source. The game decides.
3. **Which engine each `xmltest` build carries.** If the census's Anno 1800
   build is not the game's, then for the short form, and anything else the
   two engines differ on, the census is not the check it is taken for.
4. **Where `MaxRepeat` stops**, when it is a loop and not rounds unrolled.
   Answered in phase 4's first step: where the loader does, after the round
   whose end finds the condition no longer holds, or at the number. Each
   round's changes are still its own, held under the loop's rule.
5. **Whether recording from before and after keeps every path the census
   holds today.** The keying is what keeps four census units right
   (`_check_the_reading`, phase 2 of the ops-as-written proposal). If a
   reading recorded apart cannot key a path the diff keys today, that phase
   waits.
