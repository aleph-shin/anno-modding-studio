# Ops as written: a proposal

What the Studio keeps of an op somebody wrote: the op, with what it comes to
held under it, where today it keeps what the op comes to and lets the op go.
And, since the op is then the thing the Studio holds, a way of saying what
an op is. Written against the reader in `core/modops.py`, the model in
`core/changes.py`, the writer in `core/generator.py` and `core/build.py`, the
typed panes in `core/typedops.py`, and the measurements recorded in
issue 128,
where the decision was argued and made.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (9 October 2026).** Agreed, and built: all five phases are done.
Every mod is read with every op kept as written, the switch that was built
behind is gone, an op can be written as narrow ModOps on asking, and a
reading that a change in what loads has left in doubt is marked and read
again. One thing was built beside it that is not in it: the status bar says
what is running in the background, and Export and Play wait for it (section
10, *Built beside it*). See *Phase 2 as it is being built* and
*Phase 3 as it is being built* in section 10, which also say where building
it has corrected this text. It comes before the tab for a mod's file
(issue 129),
which was written assuming the Studio stays as it is and has to be read again
once this is in.

## 1. The rule

> An op a person typed, or a mod shipped, is kept as it was written. What it
> comes to is worked out from it and held under it. A value set in Fields, or
> typed into the asset, is written as a narrow ModOp, because there the
> Studio is the author. Writing a kept op as narrow ModOps is something you
> ask for.

## 2. What happens today

The Studio reads every op down to narrow changes. It has to: Fields edits one
value at a time, and conflicts are found per field. What it then stores
depends on the op:

- An op that cannot be written narrow without losing something is kept as
  written, as a `Rule` with its changes under it (`Translator.keeps`, and
  the reasons in `Translator._run` and `_check_equivalence`).
- Any other op is stored as the changes it came to, and written back as one
  narrow ModOp for each. The text it was written as is let go.

Reading and writing are tied together by that second case, and they need not
be. Fields and the conflicts list need the reading. Only the file needs to
be decided.

Measured on the bench folder (the first 150 Anno 1800 mods of the mod.io
census, 419 mods as read, 12,620 ops, 9,068 of them imported):

| What happens to the op | Ops | Share |
|---|---|---|
| Kept as written, with its changes under it | 3,740 | 41% |
| Already one narrow op, so nothing differs | 3,188 | 35% |
| Rewritten as narrow ops | 2,140 | 24% |

So three ops in four are already as this proposal would have them. It is
about the fourth.

## 3. Why change it

### What narrowing was for, and how often it does that

A wide `replace` claims a whole node, so another mod touching the node loses.
Written as one op per field, two mods changing different fields work
together. That is the founding reason (design notes, *Why one ModOp per
change*), and it holds.

Of the 2,140 ops rewritten today:

| Kind of op | Ops | Share | Does narrowing help it stack? |
|---|---|---|---|
| An addition | 1,455 | 68% | No. An addition claims nothing |
| A merge | 317 | 15% | No. A merge touches only the fields it names |
| A replace of a whole node | 216 | 10% | Yes |
| A replace of one value | 84 | 4% | No. It is one value already |
| A removal | 68 | 3% | No |

The case narrowing was made for is 216 ops: 2.4% of what mods ship, in 58 of
419 mods. The rest are rewritten because an op naming several assets becomes
one per asset, an op adding several entries becomes one per entry, a merge of
several fields becomes a replace per field, and a path that is a pattern
becomes the entries it finds today.

### What it costs

- **The op stops meaning what its author meant.** `remove
  …/BuildingList/Item[City]` is written back as three removals of three named
  buildings. `replace …/ItemCraftingConfig/*/MaximumWorkforceTime` becomes
  five replaces, one for each rarity. The author's op covers an entry a later
  mod adds. The rewrite does not.
- **The reading is of one day's stack.** A merge of two list entries, read
  while the asset has both, is two narrow changes. A mod installed later and
  loading earlier takes one entry out. The Studio goes on writing two
  replaces, one of which now finds nothing; the author's merge would have
  been applied by the game to the list as it then was. Values are already
  read against what the game ships (design notes, *Read in a stack, it is
  aimed at what the game ships*). Lists and structure are not, and cannot be.
- **It does not survive a second reading.** Each loaded mod's file, written
  out and read again: 26% of 394 mods came back different, 10% of narrow
  changes and 18% of rules. Most of it is one cause, a keep-or-narrow
  decision that depends on the state of the assets and goes the other way
  the second time. (An upper bound: the harness may not have matched an
  import exactly.)
- **It needs machinery to hold it in.** The gates that shut a pane whose
  text does not read back, `KeepAsWritten`, the Apply that moves only what
  moved, and the op-by-op Apply the file tab would need. Each is there
  because the text typed and the changes stored are two things.

In the stack it was read in, the rewrite is faithful, and that is measured:
the game's loader gives the same result for the export as for the mod in all
812 Anno 1800 census units with a patch. The costs above are all about what
happens once the stack, or the reading, is not that one.

### What is given up

- **A wide replace typed or imported stays wide** until somebody asks. For a
  mod of your own this is the real loss, and section 8 is how it is made
  small: the Studio says which ops claim more than they change and offers to
  write them narrow.
- **A change under a wide op cannot be switched off or reverted alone.** It
  is the op that is on or off. This is how the changes under a kept op
  behave now.

## 4. What an op is

While every op was on its way to being narrow changes, the only thing asked
of one was how well it narrowed. An op that did not was a "rule", drawn in
one colour, whatever the reason. With the op kept, the Studio needs to be
able to say what it is.

### Four properties

| Property | What it is | Given as |
|---|---|---|
| **Target** | The nodes it lands on | `Path`, from the document's root, or from each asset `GUID` names |
| **Payload** | What is applied to each | The op's body, or `Content` |
| **Condition** | Whether it runs | `Condition` |
| **Type** | How the payload is applied | `Type`: one of a fixed list |

The type is always written down. Each of the other three is either a
**constant**, written down in the op, or an **expression**, worked out from
the document as the game loads it:

| | Constant | Expression |
|---|---|---|
| Target | One named node, or one in each of several named assets | A pattern: a search across assets, or inside an asset `Item[City]`, `*`, `or`, a position |
| Payload | Literal XML | Copied from elsewhere (`Content`), or computed (`<ModValue>`) |
| Condition | None | A question about the document, about which mods are loaded, or about the mod's settings |

Nothing in XPath, and nothing in the game's loader, makes "which asset" a
different question from "which node in it". `GUID` is a short way to a
starting point. The Studio is laid out by asset, so it says how far a target
reaches at both levels, "41 assets, one node in each" or "1 asset, 3
entries", and that is a matter of showing it, not of kind.

### How it runs

The properties are not all settled before the op is applied. They are worked
out in a nest, each where it stands:

```
for each scope around the op:          a <Group Condition=…>, asked once on the way in
    if its condition does not hold: stop
for each starting point:               the document, or each asset GUID names
    if Condition does not hold here: go on to the next
    for each node Path finds from here:
        work the payload out at this node
        apply it to the node, by Type
```

- **The document is the one being patched**, as every op before this one
  left it, of this mod and of the mods loaded before it. Where an op stands
  is an input to it.
- **A payload can depend on the node it lands on.** `<ModValue
  Insert="self::node() * $factor"/>` has no one value.
- **A scope's condition is the scope's.** A `<Group Condition=…>`, or a
  file included on a condition, asks once, and everything inside runs or
  does not. In Anno 117 `MaxRepeat` runs a group again while it holds.
- **Three attributes are beside the patch**: `Skip` (shipped switched off),
  `AllowNoMatch` (to find nothing is not a fault) and `ModOpID` (a name a
  tweak tool finds the op by).

To be checked against the loader's source before it is relied on: whether an
op's own `Condition` is asked once for each asset `GUID` names, which the
reader assumes, or once for the op.

### How many of each

The 10,962 ModOps of the bench folder, by which properties are expressions:

| Expressions | Ops | Share |
|---|---|---|
| None | 8,036 | 73.3% |
| Condition | 1,602 | 14.6% |
| Target | 983 | 9.0% |
| Target and condition | 148 | 1.4% |
| Payload | 126 | 1.1% |
| Any other pair, or all three | 67 | 0.6% |

- **Target**: 1,176 in all. 438 are searches across assets and 738 are
  patterns inside named assets.
- **Condition**: 1,791 in all. 1,636 ask about the document (`!~/Values/…`,
  "unless it is there already") and 155 about which mods are loaded.
- **Payload**: 193, every one a copy. Values computed from a mod's settings
  are an Anno 117 feature and are not in this sample.
- **In a scope**: 4,054 ops (37%) sit inside one, up to six deep, and 2,579
  of those are constant in everything of their own.
- Beside the ModOps, the files hold 575 scopes and 1,083 pieces of a tweak
  tool's markup, carried untouched.
- 3,011 ops (27%) make assets: the payload is an asset and not a change to
  one.

Where a target is sorted here by the shape of its path, the sorting is
rough. Its largest group, 375 paths with a predicate such as
`[Item/GUID='…']`, will hold some that name one node.

### What "rule" has been

Why ops are kept today, set against the table above:

- A target that is a search. The one thing the rule form can compose.
- A condition, the op's or a scope's. The largest group by far: 890 ops of
  one mod sit in a group that asks whether another mod is loaded.
- A payload that is a copy.
- A name for a tweak tool, or `Skip`.
- **Nothing at all**: 398 ops with every property constant, kept because
  their narrow form "did not add up". They are what narrowing left behind,
  and not a kind of op.

So the word has meant "not narrowed". It is retired. An op is described by
which of its properties are expressions, and the words are those:

| Property | Constant | Expression |
|---|---|---|
| Target | named | **pattern** |
| Payload | literal | **computed** |
| Condition | always | **conditional** |

An op is a *pattern op*, a *computed op*, a *conditional op*, any two, all
three, or none, in which case it needs no word. The words are a first
choice and want trying on the screen (section 12).

### What the Studio does with it

Each op is described once as it is read (`describe(op)`, in place of the
separate checks `_has_computed`, `_asks_the_loadout`, `_asks_a_setting`,
`op.searched` and the sentences they end up as), and the description is
held with it. For each expression it says the text and what it comes to
today:

- **Pattern**: "every asset with Template X: 41 today", which opens the
  list. Or "every entry with a City: 3 today".
- **Computed**: "copied from Bakery's SocketCount: 3 today".
- **Conditional**: "if Harbour Life is loaded: runs today", or "does not run
  today".

Section 6 is where it is shown.

## 5. The model

Little is new. The model has both things already:

- **A `Change`** with no op over it: one narrow ModOp the Studio writes.
  From now on this is only ever something the Studio authored.
- **A `Rule`**: an op as written (`source`), its place in the file
  (`order`), and its changes under it (`Change.rule_uid`). From now on this
  is every op that was read from a file or typed as an op. The class is
  renamed `Op`, and holds its description (section 4).

What changes is who decides. `Translator.keeps` stops asking whether an op
*must* be kept, because every op read is. Its reasons stay, as the answer to
a different question: **can this op be written narrow, if asked?** A copy
cannot, without losing the link. A wide replace of three fields can.

### Scopes are kept too

A `<Group>` whose condition is about the game's files is answered once
today, the ops inside it read, and the group dropped from the export. A
group asking about the loadout or the mod's settings is kept whole, as one
`Rule` with its `parts`. After this every scope is kept, as what it is:

- **A scope is a thing with ops in it**: its attributes as written, its
  condition, and the ops and scopes inside in order. Ops already know the
  scopes they sit in (`SourceOp.scopes`, `Change.within`).
- **Its condition is described as an op's is**, with whether it holds today.
- **A scope that does not run today still holds its ops**, each kept as
  written and coming to nothing.
- **Switching a scope off** switches off what is in it.

A third of ops sit in a scope, so this is not an edge. A file included on a
condition is a scope of the same kind; an unconditional `<Include>` is the
file tab proposal's.

### Who wrote it

| Where it came from | Kept as | Why |
|---|---|---|
| A mod's file, read in | The op | Its author wrote it |
| Typed in the XML view's ModOps pane, or the file tab | The op | You wrote it |
| A value set in Fields | A narrow change | The Studio writes the op |
| Typed in the XML view's asset pane | Narrow changes | You said what the asset should be. The Studio works out the ops |
| The form that composes a pattern op | The op it composes | As now |
| *Write as narrow ModOps*, asked of an op | Narrow changes | You asked |
| A mod folded into one of your own | Its ops | They are its author's. Narrowing is not offered as it comes in |

Folding a mod into your own wants a second look of its own: mods as
published take another mod in as a bundled mod or as an included file, and
the Studio should probably do the same. That belongs with the file tab's
included files and is not designed here.

### What follows from it

- **An edit to a field a kept op sets is an exception**, an op of its own
  after every op of the mod: "all of these, except this one". This is what
  happens today under a kept op (`ModProject.place`, `exception_to`), and
  the reader does the same for a mod's own later op. Where several ops of a
  mod reach the field there is still one exception, after all of them, and
  none of their texts is touched. That case wants a test of its own.
- **Reading again loses nothing.** What an op comes to is only ever worked
  out from its text.
- **The replay that tries the narrow form is not needed for a mod read in.**
  `_check_equivalence` replays the narrow ops the Studio would write and
  keeps as written any op whose narrow form does not add up. With no narrow
  form written there is nothing on trial. It becomes part of *Write as
  narrow ModOps*. It is described in the code as the slow part of reading a
  folder, so reading may get faster. Not measured.
  *As built:* half right. The trial goes, and reading the bench folder went
  from 183 s to 88 s. But the same replay also mended the paths of changes
  (`_rekeyed`), and the asset is shown from those changes. Without it four
  census units were shown other than the game leaves them. So each asset's
  changes are still replayed and keyed again where that mends them
  (`_check_the_reading`); what is gone is the replay of ops and the
  deciding.
- **Folding one op into another goes.** An op that edits what its own mod
  has just added is read into that addition's payload today, and kept apart
  again when the addition turns out to be kept (`OpResult.folded`,
  `lodged`). Each op stays itself. The asset still has to read as all of
  them leave it, which `effective.build` already does from the changes
  under each.
  *As built:* not yet, and not for nothing. Each op is kept and written as
  itself. But its *reading* is still folded into the addition it edits:
  written as a change of its own, with a path into a node that was not
  there a moment ago, it did not always find that node again, and a census
  read that way showed five assets wrongly. The op's row says "shown in the
  op it edits". Giving such an op its own changes wants paths into added
  nodes that hold, which is work of its own.
- **The two "settle" settings of a reading go.** `settle_computed` and
  `settle_copies` decide, as a mod is read, whether a computed payload is
  written back as what it came to. That is narrowing by another name,
  decided before anything has been looked at. It becomes part of the same
  action, asked of the op.

## 6. What it changes on screen

### The Mod changes panel

Under a mod, a row is an op with what it comes to under it, where today most
rows are changes. Left like that the list would be a level deeper for every
op, so:

- **An op that comes to one change, at the node it names, is one row.**
  That is most ops, and it reads as it does now.
- **An op that comes to several is a row with its changes under it.**
- **A scope is a row with its ops under it**, saying its condition and
  whether it holds today.
- **A change the Studio wrote is a row**, as now.

In place of one colour and a `#` for everything kept, a row carries a mark
for each expression in its op, with what it comes to today beside it:
"pattern · 41 assets", "computed · 3", "conditional · runs". Each mark is a
glyph and a word, not a colour alone. An op with no expression has no mark.

The checkbox on an op's row switches the op. The changes under it have none.

### The XML view

- **The ModOps pane shows the ops as written**, and Apply stores what was
  typed. It reads each op to find what it comes to, and says so; it does not
  write it another way. The line "Each is written as its own ModOp" goes.
- **The pane is never shut.** The gate asked whether the text read back as
  the changes it was written from (`typedops.reads_back`). The text is now
  what is stored, so there is nothing to read back.
- **`KeepAsWritten` goes**, with the two menu entries. It marked the
  exception that is now the rule. An op carrying the attribute is read as
  any other, and keeps it, since its text is kept; both games ignore it.
- **Apply still moves only what moved.** An op whose text is unchanged keeps
  its place, its note, its switch and its uid. That is a comparison of text
  now, and needs no reading.
- **The asset pane is as it is**, with one simplification. Typed text is
  read as the whole of what the mod does to the asset only where everything
  the mod does to it is the Studio's own changes. Where an op somebody wrote
  reaches the asset, what is typed is laid over it (`typedops.lay_over`),
  since an op cannot be taken half away.

### Fields

No change to what is typed or shown. A value set over a kept op's answer is
written as the exception after it. The row's menu says which op sets the
field and offers to show it.

## 7. Readings that have gone stale

What is under an op is what it came to when it was last read. A mod
installed since, loading earlier, can change that. Today nothing notices,
and the stale reading is what is exported. After this the stale reading is
only what is shown, and it is put right at once:

- **A change in what loads marks the readings it could reach**: the ops of
  later mods aimed at an asset an earlier mod now changes differently, and
  every pattern op, whose reach may have grown.
- **They are read again straight away**, a mod at a time, off the UI thread.
  Until a reading is fresh its rows say so, and the rows that moved are
  said once it is.

## 8. Writing an op narrow

One action, on an op or on every op of a mod: **Write as narrow ModOps**.
The op is replaced by the changes it comes to, which are from then on the
Studio's own. One step of undo.

- **Where it is offered.** On an op that can be written narrow: `keeps` has
  no reason against it and the replay of its narrow form adds up.
- **Where it is not, the row says why.**
- **What it warns of.** A pattern becomes today's answers, and a computed
  payload the value it came to. The action says how many ops it will write,
  and that a later mod's entries will not be covered.

And one piece of advice, so that the reason narrowing exists is not lost:

- **An op that claims more than it changes is marked.** A `replace` of a
  node that retypes fields it leaves as they were takes those fields from
  any other mod. The Mod changes panel counts them for a mod of your own
  ("3 ModOps replace more than they change") the way it counts ops not
  read, and each offers the action. Of the 216 wide replaces measured, 102
  change under three quarters of what they retype.

This is where the beginner's pasted wide replace ends up. It is kept as
pasted, and the Studio says it could be narrower and does it on one click.
The asset pane goes on working the narrow ops out without being asked,
because what is typed there is a value.

## 9. Speed

`tools/bench.py` with every op forced to be kept, medians of three runs.
Kept ops went from 1,430 to 7,469 and the project file from 46 MB to 61 MB.

| Step | Now | Every op kept |
|---|---|---|
| An edit | 2.1 s | 3.1 s |
| An edit, Export tab shown | 3.4 s | 4.7 s |
| Play, nothing to change | 3.5 s | 5.1 s |
| Refresh what loads | 0.6 s | 1.3 s |
| Switch a mod off | 0.6 s | 1.4 s |
| Open the busiest asset | 0.22 s | 0.36 s |
| A filter keystroke in Mod changes, until the list settles | 3.5 s | 11.4 s |

Startup, opening an untouched asset and showing the Export tab did not move.
These paths were tuned for about 1,400 kept ops, and nothing has been
profiled, so none of this is known to be inherent. It is still the first
thing to do: no later phase should ship slower than today. Forcing `keeps`
is a stand-in for this proposal and not the thing itself, so the figures are
a guide to where to look.

### After phase 1 (7 Oct)

Every step was profiled both ways. Two things accounted for the difference,
and neither was inherent.

- **Finding a kept op by its uid walked the whole list**
  (`ModProject.rule`), and it is asked once for every change under a kept op
  each time the Mod changes panel is drawn. It is now looked up. This was
  all of the difference in every step but one.
- **Clearing the Mod changes panel** took the view's list of open rows apart
  one row at a time. Once a filter has built every row, the next edit paid
  for that: 1.6 s today and 2.3 s with every op kept. The view is now reset
  before it is cleared.

Medians of three runs again, the first column as above:

| Step | Before, as now | After, as now | After, every op kept |
|---|---|---|---|
| An edit | 2.1 s | 2.1 s | 2.2 s |
| An edit, Export tab shown | 3.4 s | 2.4 s | 2.5 s |
| Play, nothing to change | 3.5 s | 3.5 s | 3.6 s |
| Refresh what loads | 0.6 s | 0.6 s | 0.6 s |
| Switch a mod off | 0.6 s | 0.6 s | 0.6 s |
| Open the busiest asset | 0.22 s | 0.22 s | 0.22 s |
| A filter keystroke, until the list settles | 3.5 s | 2.9 s | 3.2 s |
| Clear the filter | 0.39 s | 0.38 s | 0.44 s |
| Open the project | 7.7 s | 7.6 s | 7.9 s |

Every step with every op kept is within a tenth of what it was before, bar
clearing the filter, which is 50 ms over. What is left of the difference
between the last two columns is rows: 6,000 more kept ops are 6,000 more
rows in the panel, each above the one change it comes to. Phase 2 draws an
op of one change as one row, which takes those away again.

The two projects are not the same reading (411 mods and 31,281 changes
against 415 and 32,020), so a few percent between them is the projects and
not the code.

## 10. Phases

1. **Speed first.** *Done; see section 9.* Profile each slowed step with
   every op kept (`tools/bench.py --profile`), and bring each to within a
   tenth of today's figure. Nothing a user sees changes. The forced `keeps`
   stays a scratch patch until phase 2.
2. **The reader keeps every op and every scope, and describes each.**
   *Under way; see below.* `keeps` answers "can it be written narrow". The
   replay and the folding leave the reading of a mod. `Rule` becomes `Op`.
   The Mod changes panel draws an op of one change as one row, a scope as a
   row, and the marks in place of the one colour. The census harness asks
   its new questions (below). This is the phase that changes what an export
   of an edited mod holds, and it wants a full census run.
3. **Typing.** The ModOps pane stores what is typed; the gate,
   `KeepAsWritten` and its menu entries go; the asset pane lays over where
   an op somebody wrote reaches the asset.
4. **Write as narrow ModOps**, for an op and for a mod, with the mark on ops
   that claim more than they change. The settle settings go into it.
5. **Stale readings**: marked, and read again in the background.

Phase 2 without phase 4 would leave no way to narrow anything, so the two
ship together or phase 4 follows at once.

### Phase 2 as it is being built

**Behind a switch.** Everything downstream of the reader assumes some ops
are kept and the rest are narrow: the import dialogs, the XML view, the
panel's words, the census harness. Changed all at once it would be one very
large change; changed a piece at a time on `main` the Studio would be half
one thing and half the other in between. So the reader has both readings
(`Translator(narrow=…)`, `Reading.narrow`), and which one a mod is read with
where nobody says is one switch, `ANNO_STUDIO_OPS_AS_WRITTEN=1`, off until
phases 2 and 3 are both in. Each piece is merged with the switch off and
tried with it on. The last change of phase 3 turns it on and takes it out.
With it on, about 110 of the suite's 3,490 tests fail today, nearly all of them
tests of narrowing or of the XML view: that list is what is left.

**Done so far**

- *The reader.* Read as written, every op read from a file is kept, with
  what it comes to under it: one that changes nothing today, one whose
  condition does not hold, and a search that finds nothing included.
  Nothing is settled, whatever the two settings say, and the reason `keeps`
  gives is kept as why the op cannot be written narrow as it stands.
- *Groups.* A group the Studio reads is kept as a group with ops in it
  (`Rule.scope`): its own tag and nothing inside it, with each op and group
  inside an op of its own that says which groups it sits in
  (`Rule.within`). Where the group runs today they are read; where it does
  not (`Rule.idle`) each is kept and comes to nothing. An op the Studio
  cannot read says which group it is in as well, and the export puts
  everything back inside its group, in order. Two kinds are still kept
  whole, as one op with everything under it: a `MaxRepeat` loop, where an
  op inside is a round's worth of changes and not one op's, and a group
  whose question the Studio cannot answer, which is carried unread.
- *The description.* `modops.describe(op)`, and `described(text)` for an op
  held as text: target (named, a search, a pattern inside named assets),
  payload (literal, copied, computed, flags), condition (none, the document,
  the loadout, a setting), and `Skip`, `AllowNoMatch` and the tool's name.
  Nothing shows it yet.
- *The model.* An op says outright whether it searches (an empty reason
  used to say so), and whether it is *plain*: one change, at the node it
  names, in the one asset it names, with nothing else to say of it.
- *The panel.* A plain op is its change's row among the asset's others, as
  now, and the box on it switches the op. The same edit to several assets
  is the op's row with a row for each asset. An op kept for no reason of
  its own has no mark and is not counted as a rule. A group is a row with
  what is in it under it, saying whether it runs, and its box switches
  everything in it. Several ops of a mod that change nothing as things
  stand are one row to open.
- *Typing over a plain op's value* (item 7 of section 12, settled). The op
  is let go of for the edit, which takes its place in the file and its
  note: a narrow change the Studio writes, where the op was. Typing the
  game's value back takes the op out. Any other kept op still gets the
  exception after it.

**Measured, read as written**

| | Narrow, as now | As written |
|---|---|---|
| Reading the bench folder | 183 s | 88 s |
| Ops kept, of 416 mods | 1,486 | 7,318 |
| of them plain, drawn as one row | | 2,973 |
| Changes | 32,375 | 32,021 |
| Project file | 48 MB | 60 MB |

The census, both games, with the game's loader, against the last run of
`main` (`rt-647d6af`):

| | Before | As written |
|---|---|---|
| Anno 117: export read again is the same | 337 of 337 | 337 of 337 |
| Anno 117: the loader on the export against the loader on the mod | same in 335 | same in 337 |
| Anno 117: the Studio's reading against the loader's | same in 296 | same in 297 |
| Anno 1800: export read again is the same | 803 of 812, 9 the same in effect | 812 of 812 |
| Anno 1800: the loader on the export against the loader on the mod | same in 812 | same in 812 |
| Anno 1800: the Studio's reading against the loader's | same in 642 | same in 643 |
| Ops not read, both games | 1,687 | 1,687 |

With groups kept as groups, the same census again: every figure above the
same in both games, and 15,300 more ops kept as ops of their own (the ones
inside groups, 14,600 of them in Anno 1800). It found one more fault, fixed:
an op that appends text, written with the text on a line of its own, lost
the line break once it was written out as itself and not as part of its
group (three mods of one author).

Three things the first run found, all fixed above: the five assets shown
wrongly with ops unfolded, the four with no replay, and one mod (*Add
Collection*) whose export differed, because a value written over lines
with a comment on each was laid out afresh.

Steps of `tools/bench.py` are within a tenth of today's except an edit
(2.1 s to 2.4 s) and opening the busiest asset (0.22 s to 0.25 s). Not yet
profiled.

**What was left in phase 2, all of it now done**

- *A tweak tool's name on a group's row* (done, 8 October). A group kept
  whole names the groups inside it as the mod's options dialog does, and
  says which have no switch. A group kept as a group has the same facts
  (`Rule.tool`): its row takes the name, and its tooltip says what the
  group is called in the file, that *Mod options…* switches it, a second
  switch naming it, and a tag with no switch. That last is now said of a
  group that is on as well, which iModYourAnno switches off once the mod's
  options are set there; of a group inside one kept whole it was only said
  where the group was off already. The filter finds the row by either
  name. An op that is not a group and carries a `ModOpID` has the gear and
  no more: its knobs change its value, and the row should go on saying
  what it does.
- *The marks and the words* (done, 8 October, and not behind the switch:
  an op kept today wears them too). They ended up drawn and not written,
  which section 6 did not foresee (`ui/op_marks.py`):
  - Every row that is a ModOp starts with a **badge** for its type, filled
    and in the type's colour: add (green), add after and add before, remove
    (red), replace (blue), merge (slate), and brackets for a group. Dashed
    and hollow for an op its mod ships with `Skip`, which is then plainly
    not the same thing as an op switched off here.
  - A kept op with a row of its own has **four fixed places** after the
    badge, empty where they do not apply, so an op's shape is one
    silhouette wherever it turns up: a magnifying glass for a pattern, a
    calculator for a computed value, a diamond for a condition, a gear for
    an op that answers to the mod's own options. Filled says it comes to
    something today: the glass when it finds something, the diamond when
    its condition holds.
  - Beside them, in words, only what each comes to today ("2 assets", "1 of
    2 assets", "= 4", "runs"), and the row's own words leave out the type.
    The sentences are in the tooltip, and a `?` by the filter box shows
    every drawing with its words.
  - "Rule" is gone from the screen: the op the form composes is a *pattern*
    ("New pattern…"), and any other is a *ModOp kept as written*. The
    mod's row no longer counts rules. In German: Muster.
- *`Rule` becomes `Op`* (done, 8 October, and not behind the switch: it is
  names). In code, by name and not by sentence:
  - `changes.Rule` is `changes.Op`; a project has `ops`, `op(uid)`,
    `set_op`, `drop_op`, `ops_of`; a change has `op_uid`. Saved under
    `"ops"` and `"op_uid"`, and a project saved with `"rules"` and
    `"rule_uid"` still opens.
  - `core/rules.py` is `core/keptops.py`, beside `modops` and `typedops`.
    `RuleGroup` and `RulePart` are `OpGroup` and `OpPart`; a row's kind in
    the Mod changes panel is `"op"`.
  - What composes a search is named for what the screen calls it:
    `ui/pattern_dialog.py` (`PatternDialog`), `ui/patterns_controller.py`
    (`window.patterns`), `new_pattern`, `edit_pattern`, `check_patterns`.
  - Where `op` was somebody already, the op as parsed, the kept one is
    `record`: `OpResult.record`, and a local in seventeen functions, ten of
    them in `modops`. The ops of a typed pane that reach beyond its asset
    are `reaching` (`Read`, `Turn`, `Applied`), which had `ops` for a count.
  - Left as they were: `Change.rule`, the search a change came from, in
    words; the theme's `rule` colour; the census's `rules` mode and result
    keys, which old runs are compared by; and the sentences. Comments,
    docstrings and the names of tests still say "rule" of a kept op in some
    six hundred places. They are prose, each wants reading, and a pass that
    swapped the word would have made nonsense of the ones that set a rule
    against an op.
- *The import dialogs* (done, 8 October). Read as written:
  - In the dialog for one mod a row is the op itself, with the badge and
    marks the Mod changes panel draws. Every op the Studio read has a tick,
    the ones that change nothing today and the groups among them; ticked, it
    comes in as the op it is, with what it comes to under it. An op inside a
    group is set in under it: ticking it ticks its groups, since its
    condition is theirs, and a group's tick is the tick of everything in it.
    The button counts ModOps ("Import 7 ModOps"), a group not being one.
  - The words say what was read and not what it became: *Read*, *No change
    today*, *Runs today* or *Does not run today* for a group, a column
    *Comes to*, and a summary that counts ops and leaves groups out. The
    reader no longer says an op "became one change each".
  - The two settle boxes are not shown, in either dialog. *Keep inert
    removals* stays, with words of its own: the op goes out as written
    whatever is ticked, so the box is only about what is listed under it.
  - A mod whose ops all change nothing today can be imported, in both
    dialogs. It was refused for having no changes.
  - A mod imported a second time brings no op twice
    (`ModProject.as_before`): an op is the one already there where it is the
    same mod's, says the same and sits in the same groups, as a change has
    been matched to the one already there all along. Read narrow this
    mattered for the few ops kept; as written it is every op.
- *The census harness's new questions* (done, 9 October; below).

### Phase 3 as it is being built

Behind the same switch, until its last change turns it on.

**Done so far** (9 October)

- *The ModOps pane stores what is typed* (`typedops._store`). Which op is
  which is told by its text: one the text still has keeps its place, its
  note, its switch and its uid; one typed over, aimed where an op that is
  gone was aimed, takes that one's place; the rest is new, after the mod's
  ops, or gone with what it came to. A change of the Studio's own is in the
  pane as the op it is written as, and typed back as that op it is still
  that change; typed over, it becomes the op that was typed, where the
  change was.
- *The asset's own ops* (`xmlview.is_own`) are the ones that name it and no
  other and sit in no group. An op in a group is beside the pane with the
  searches: its condition is not in its text.
- *The pane is never shut.* `reads_back` has nothing to ask.
- *`KeepAsWritten` is not offered* in the Mod changes panel, and
  `mark_kept` and `unmark_kept` refuse.
- *The asset pane* works its narrow changes out as before, by a reading
  asked to rewrite whatever a mod is read with. Where an op somebody wrote
  reaches the asset, or one carried unread is aimed at it, what is typed is
  laid over.
- *An op typed here is work* (`Op.authored`), as an authored change is: it
  counts toward the unsaved mark though it come to nothing, and reading its
  mod's folder again leaves it. The pattern form's op is one too. Not yet
  counted where a mod's edits are counted for a prompt
  (`ModProject.authored_of`): that counts changes, and misses only an op
  of yours that comes to none.

**The switch turned on, and taken out** (9 October)

`modops.reads_narrow()` answers no, and reads no environment. It stays as
the one place that says which reading a mod is given where nobody says.

- *What went with it*: `ANNO_STUDIO_OPS_AS_WRITTEN`; the read-back gate
  (`typedops.reads_back`) and what the XML view said when it was shut;
  `mark_kept`, `unmark_kept` and the two *Keep as written* menu entries;
  the two settle boxes and their help; the narrow half of the import
  dialog, its "Imported", "Already true" and "Becomes"; and the README's
  account of all of them, with its section on `KeepAsWritten`.
- *The narrow reading stays*, asked for by name (`Translator(narrow=True)`,
  `Reading(narrow=True)`). It is what a typed asset's changes are worked
  out by and what *Write as narrow ModOps* will run. Its tests stay too:
  61 of them are marked as asking for it (`support.narrow_reading`), and 43
  that tested what the screen made of a narrowed mod are gone, the same
  things being tested of a mod read as written.
- *Found on the way, and fixed.*
  - A value typed over what a kept op sets recorded the game's value as
    what it was typed over, not what the op left.
  - Such an exception moved into a patch kept its tie to the op it had
    been beside.
  - Reading a mods folder again gave every op a new uid, which cut each
    exception loose from its op. An op read again is now the op that was
    there (`ModProject.as_before`), and the status line counts the edits
    of yours that were kept, where it had counted only the ones that
    stood where a mod's own change stood.
  - An op that adds an asset names the asset it is put beside, and so was
    no asset's own: typed over in the new asset's pane it left the pane.
    It is the new asset's own now (`xmlview.creates_only`).
  - The caret on a kept op in the ModOps pane showed nothing in the asset
    beside it.
  - *Show XML* on the row of an op drawn as its one change did not say the
    op was switched off.
- *Checked.* The census of both games run with nothing set
  (`rt-cd04a08`) against the last one run with the switch on
  (`rt-cfa1c17-w`): no unit moved in any field.
- *Not done.* The settle settings are still fields of a `Reading`, and
  still recorded with a mod (`read_as`): they are the narrow reading's,
  and go into the action with it in phase 4.

### Phase 4 as built

*Write as narrow ModOps* (9 October; `keptops.narrowing`,
`keptops.write_narrow`, and the Mod changes panel's menus).

- **The action.** On a kept op's row, and on a mod's row for every op of the
  mod that can be. It asks first, saying how many ops become how many
  changes, how many stay and what is given up, and is one step of undo. The
  op is dropped and the changes it came to stay where it was, the first with
  its note, as work of yours: its mod's folder still has the op. An edit
  that was the exception to the op is an edit after those changes.
- **Nothing is read again.** What an op comes to is under it already, read
  when the mod was read and replayed then (`_check_the_reading`). So "the
  replay of its narrow form adds up" is not a second check but the reading's
  own: an op whose reading is known to be short is not offered the action.
- **Where it is not offered, the entry says why**: a group, an op in one, a
  condition, `Skip`, an op switched off, one that changes nothing today, one
  with a change reverted or switched off from under it, one whose work is
  shown in the op it edits and that op, and a reading known to be short.
- **What it warns of**: a pattern becomes what it finds today; a value
  worked out as the game loads, or copied from another asset, becomes the
  value it comes to today; a condition about the game files, which holds
  today, is no longer asked. A condition about which mods are loaded, or
  about the mod's own settings, is a switch and is refused. This is where the two settle settings went, as
  the question they were.
- **An op that claims more than it changes is marked**, for a mod of yours
  (the project's own, or one whose creator is the project's): its row's
  tooltip says how many fields it replaces and leaves as they were
  (`keptops.claims_more`), and the mod's row counts such ops beside its
  unread ones. Counted as the values a `replace` types, less the changes it
  comes to.
- **An op that is its one change already** can be written narrow and has
  nothing to gain; the action on a mod passes it over.
- **The census's `action` export is this**, the app's own answer and no
  longer the harness's guess at it. It therefore narrows patterns and
  values worked out at load too, which the guess left alone.

*Not done.*

- `Reading.settle_computed` and `settle_copies` are still there. Nothing in
  the app sets them and nothing read as written asks them. They are knobs
  of the narrow reading, kept because some two dozen of its tests turn them; taking
  them out is taking those tests out, and wants deciding with the narrow
  reading's future (section 11).

*What the census found of it* (`rt-3830720`, both games, the first run with
the app's own answer). One fault, fixed: an op shown as a whole node swapped
was written narrow where it also worked its value out, since the reader gave
the second as its one reason for keeping it. *Cheaper and Faster Streets*
merges at `/Values` so, and the Studio read its own narrowed export as
another asset in 6 assets. The reader now says why what is under an op is
no narrow form of it apart from why it is kept (`Op.wide`), and the action
goes by that. That run also refused every op with a condition, which took
1,890 ops of one mod alone out of it; a condition about the game files that
holds is now given up with a warning, as the narrow reading always gave it
up. Otherwise as before: the loader on the narrowed mod differs from the
loader on the mod in 7 units of Anno 117 and 10 of Anno 1800, each one a
unit whose reading already differs.

Run again with both changes (`rt-1cc92de`): the action narrows 22,414 ops in
Anno 117 and 51,924 in Anno 1800 and leaves 8,833 and 24,139 as written. The
Studio reads every narrowed mod as it read the mod, in all 337 and 812
units. The loader differs in the same 7 and 10, and on the export as the
app writes it in none.

### Phase 5 as built

*Stale readings* (9 October; `core/stale.py`, `ui/readings_controller.py`,
`RereadWorker`).

- **What counts as a change in what loads.** The mods with a folder, in the
  order they load, each with the settings it is read at.
  The project keeps that list as it was when last looked at
  (`ModProject.read_stack`), and `stale.check` measures against it after
  every change to the project. So nothing has to say that it changed what
  loads: unloading, importing, a patch made, a setting changed and an undo
  of any of them are all seen the same way.
- **Whether a mod is switched on is not part of it.** A stack is read with
  every mod in it, on or off, and always was; a reading that left the
  switched-off mods out would be another reading, and another change.
- **What is marked.** Each mod after the change, with why
  (`ModLayer.stale`: a mod added, changed, gone, the order moved, or the
  mod itself read alone). Which of its ops, `stale.reached`: where the only
  change is mods that were not there before, the patterns, the ops with a
  change at an asset one of those mods changes, and the ops that come to
  nothing today; every op otherwise, because what a mod that has gone or
  been read again used to change is not known any more.
- **A mod imported on its own is marked too**, where anything loads before
  it: the import dialog reads it against the game alone. It is read again
  where it stands as soon as it is in.
- **Read again in one pass.** The stack is read once, as far as the last
  marked mod (`reader.analyse_stack`), on a thread of its own and from a
  copy of the stack. Not a mod at a time, as section 7 has it: each would
  read every mod before it again.
- **Taken only if it is still the question.** The answer comes back with the
  copy it was asked of; if the stack or what is marked has changed since, it
  is dropped and the reading started over. One that arrives while a dialog
  is open waits for the dialog.
- **What is kept.** `stale.take` leaves what was done to the mod here: an op
  read again has the uid it had (`as_before`), so an edit over one of its
  answers is still the exception to it; changes and ops of yours stay; an
  op or change switched off, or given a note, keeps both.
- **It ends the undo history**, as every other reading of a folder does. The
  one step this costs that was there before: unloading a mod with others
  after it can be undone only until they have been read again.
- **What the rows say.** *being read again* beside the mod and each op
  reached; *may be out of date* where the reading failed; *changed when
  read again* beside an op that comes to something else, until the next
  reading. The status bar counts the mods read and the ops that moved.
- **Saved with the project**, so a project closed in the middle is read
  again when it is opened.

*Not done.*

- A mod's files changing on disk behind the Studio is not noticed. One
  imported again is: the mods after it are marked.
- A patch of yours is never marked. What it holds is what was written here,
  and its folder is only where that was last written out.
- The pattern check that asks before extending a pattern
  (`patterns.check_patterns`) is still there beside this. It is about
  assets a mod of the project's own adds, which no folder reading sees.

### Built beside it: background work said, and waited for

Not in this proposal, and built with phase 5 because phase 5 made it
wanted (9 October; `ui/background.py`). Reading mods again behind the
window is the first background work that changes what a project holds, and
there was no telling, before doing something that matters, whether the
window had caught up.

- **The status bar says what is running.** Its right-hand end names what
  is running behind the window, with a moving bar, and reads *No background
  work running* when nothing is. Whoever starts a piece of work puts it on
  the list (`BackgroundTasks.begin`) and takes it off.
- **What is on the list.** The mods being read again, where a reading held
  for an open dialog still counts as running; the scan of the other mods
  on this PC; the game's loader asked about an export; and the loader's
  probes. Not the work that already runs behind a dialog of its own: the
  index being built, a mods folder being read.
- **Export and Play wait for it** (`BackgroundTasks.wait`): every export,
  publishing an update, and making a project live. A dialog says what is
  still running and the act goes ahead when nothing is. Cancelling it
  cancels the act and leaves the work running.
- **A reading is taken under that dialog.** It holds off under any other,
  which may be changing the project; this one is open only to wait for it.

*Not done.*

- The wait is for everything on the list, the scan and the probes
  included, though neither changes what an export writes.
- There is no going ahead regardless. Work that never finished would keep
  Export and Play waiting until the Studio was started again.

### What the census measures afterwards

Its "identical" and "same effect" tallies measure a rewrite that will no
longer happen. In their place:

- **Ops as written.** Every op and scope of a mod read and written back is
  what it was. Expected of everything the Studio reads.
- **The reading.** The asset as the Studio shows it against the asset as the
  game's loader leaves it. This is measured today (in Anno 117, the same in
  303 units and different in 34 at the last count) and becomes the figure
  that matters most, since the reading is now the only thing the Studio
  works out.
- **The action.** Every op that can be written narrow, written narrow, and
  the loader run on the result: today's round trip, kept as the test of
  *Write as narrow ModOps*.

*As built* (`tools/modio_roundtrip.py`). A unit says how it was read
(`reads`), and one read as written is asked these in place of the narrow
export:

- *Ops as written* (`as_written`) lays each mod's own files out, every op
  and group in order with how deep in groups it sits, and the same of its
  export, and compares the two. It is the mod's file that is laid out and
  not what the Studio made of it, so an op the reading lost without a word
  shows here, where the older count of carried ops could not see it. A mod
  is the same, has an op moved, or differs; a unit with a mod that differs
  is `differs` whatever else is true of it.
- *The reading* is `game_reading`, as it was.
- *The action* is a second export, `action`, in place of `narrow`: the ops
  `narrowable` picks are left to their changes and the rest stay. An op is
  picked where it is on, comes to something, has no reason of the reader's
  against it, no known gap in its reading, is no group and in none, and
  neither shows its work in another op's changes nor carries another's. The
  loader runs on it on `--loader-action`. The action itself will ask one
  thing more, that the narrow form replayed adds up; nothing asks that of
  a mod read as written, so this narrows more than the action will.

The first run (`rt-cfa1c17-w`, both games, the loader on both exports):

| | Anno 117 | Anno 1800 |
|---|---|---|
| Units with a patch | 337 | 812 |
| Mods, and their ops and groups | 397, 34,743 | 1,885, 80,409 |
| Mods whose export has every op as it was, where it was | 397 | 1,885 |
| The reading the same as the loader's, in units | 297 | 643 |
| The loader on the export the same as on the mod | 337 | 812 |
| Ops written narrow by the action, and ops that stay | 21,820, 9,427 | 51,849, 24,214 |
| The Studio reads the narrowed mod as it read the mod | 337 | 812 |
| The loader on the narrowed mod the same as on the mod | 331 | 802 |

Two thirds of all ops can be written narrow. Where that goes wrong in game,
6 units and 10, it is in every case a unit whose reading already differs
from the loader's: the action wrote down what the Studio believed, and the
Studio was wrong there. No unit read correctly came out differently. So
what the action has to be sure of is the reading and nothing of its own,
and the 16 are among the 209 the reading figure already counts. Three of
the six in Anno 117 are one value empty on one side and a number on the
other, which is the loader build dropping a computed value and not the
Studio. The Studio itself cannot tell any of the 16 apart: it reads each
narrowed mod as it read the mod.

## 11. Beyond this proposal: the reader built on the model

The reader today is an interpreter that was built to narrow. Working out
what an op does, deciding whether its narrow form can be trusted, folding
one op into another and replaying the result are woven through one another
in `core/modops.py`. Once narrowing is an action asked for, the reading is
only the nest in section 4: scopes, starting points, condition, target,
payload, apply. A reader written as that would be smaller, would be
checkable against the loader's source step for step, and could read one op
against a given document, which the background reading of section 7 and the
file tab both want and the reader does not offer cleanly now
(`typedops._read` sets up a whole `Translator` to read one asset's ops).

It is not in the phases. The time to judge it is after phase 2, when what
is left of the reader is known, with the census's reading figure as the
check that nothing moved.

## 12. What is not settled

1. **The words.** *Pattern*, *computed* and *conditional*, for an op and on
   its row. Whether they read well on the screen, in German, and beside one
   another on one row. And what the form that composes a pattern op is
   called once "rule" is gone.
2. **Whether an op's `Condition` is asked for each asset `GUID` names.** To
   be checked against the loader's source.
3. **Anno 117.** Section 4's counts are Anno 1800's. Computed payloads,
   `ModItem` and `MaxRepeat` are in Anno 117 and want the same count.
4. **An edit inside the op's text.** Where the value stands in the op as
   written, a Fields edit could change it there and write no exception. Left
   out: where several ops reach the field it would have to pick one, and 31%
   of the changes it could apply to come from ops naming several assets,
   where an exception is right in any case. Worth a look once mods have been
   edited this way for a while and it is known how fast they grow.
5. **Whether reading a folder gets faster** without the replay. *It does:
   183 s to 88 s on the bench folder.*
6. **The mark for a wide replace.** "Changes under three quarters of what it
   retypes" is a first guess at where advice becomes noise.
7. **An op the Studio itself wrote, read back from a file.** *Settled, 8
   October:* a plain op (one change, at the node it names) is edited in
   place. Exported and read again, a value set in Fields is an op somebody
   wrote, and without this a mod of your own carried on with from its
   folder would grow by an op for every value changed twice.
8. **Where an op's row goes.** Ops with a row of their own come first under
   a mod, in file order, and the assets after them, as rules do now. A mod
   of forty wide ops is then forty rows before the first asset, and what a
   mod does to one asset is in several places. Left as it is for now.
9. **Ops that change nothing today.** *Settled, 8 October:* folded under one
   row for each mod. 1,145 of the bench folder's 7,318 are such ops:
   already so, or a condition that does not hold.

## 13. What this leaves alone

- **Fields**, and everything the Studio writes for it.
- **Ops the Studio cannot read.** Carried as written now and still are.
- **Mods that are loaded and not touched.** They are not written today, and
  are not after.
- **The layout of a file**: comments between ops, blank lines, which file an
  op is in. This is about ops and scopes. The file tab proposal takes up the
  rest.
- **Projects saved by earlier builds.** Not a constraint before the Studio
  is released.
