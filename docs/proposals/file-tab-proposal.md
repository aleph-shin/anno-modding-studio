# A tab for a mod's file: a proposal

A place to type every op a mod has, in the file it is in and in the order it
runs: a fifth main tab holding the mod's file, and around it what the XML
view and the Mod changes panel need so that no op is left without one.

This was first written as
issue 129 on
7 October 2026, on the understanding that the Studio would go on narrowing the
ops it reads. It did not: the [ops-as-written proposal](ops-as-written-proposal.md)
was agreed and built, and the [reader proposal](reader-proposal.md) rebuilt
the reader on what an op is. The issue said, in its last section, what would
have to change if that happened. This is the proposal again with those
changes made, and checked against the code as it now is.

Written on 10 October 2026, against `main` at `4f116b1`, and revised on 11
October against `0e199e4`, after PR 150 split `core/modops.py` into
`modops`, `reader` and `recording`, with `applying` beside them.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (12 October 2026).** Approved. Phases 0 to 3 are built: the tab with its text, outline, lines, Apply, *Show in file* and the panel that folds; the background reading after an edit; the ops beyond the asset in the XML view's ModOps pane; and, for an op typed there that goes elsewhere, the marks before Apply, the selection after, and undo putting the text back. Phase 4 is built in three parts: each op knows its file and an export writes the files; the tab shows each file as a region of the one text; and comments and blank lines are kept where they stand. Left out of it: a region for a file on a condition, in a group or in another mod's folder, and a mod folded into yours. The drag in the outline was built on 24 October (#196). Section 9 says where what was built differs from what is written here.

## 1. What has changed since the issue

| The issue said | Now |
|---|---|
| Apply has to compare the typed text with the text the Studio wrote, op by op, and read only what changed, because reading a whole file again moved 26% of mods | What is typed is what is stored. Nothing is rewritten, so reading again loses nothing. The comparison op by op exists for one asset's ops (`typedops._store`), to keep each op's place, note, switch and uid, and not to protect its reading |
| The ModOps pane is shut for 8 of 940 sampled mod-and-asset pairs, and the file tab's Apply would replace that gate | The gate is gone (ops-as-written phase 3). The pane is never shut |
| An op is narrow, a rule, kept as written, unread or switched off | "Rule" is retired. An op has a type, and its target, payload and condition are each written down or worked out at load. The Mod changes panel draws that as a badge and four marks (`ui/op_marks.py`) |
| A reading gone stale is a caveat of the whole Studio, and the Export tab may or may not catch it | A change in what loads marks the readings it reaches, and they are read again in the background (`core/stale.py`). Export and Play wait for it |
| The writer has to record which lines each op came from | Still true. The XML view finds an op's line by searching for its opening tag (`xmlview._line_of_op`) |
| Groups are dropped from the export where the Studio answers them | Every group is kept as a group with its ops in it (`Op.scope`, `Op.within`), and so is a file included on a condition |
| Included files are read in place, and nothing records which file an op came from | Unchanged |

So of the issue's five parts, the second is simpler than it was, the third
and fourth stand as written, and the first and fifth stand with the changes
below. What the tab is for has not moved: there is still no view of a mod's
file, and so still ops that have nowhere to be typed.

## 2. The problem

The XML view is an index into a mod's ops by asset. The pane of the mod
changes go to takes typing for the ops that are that asset's own
(`xmlview.is_own`): an op that names this asset and no other, or adds it, and
sits in no group. Every other op of the mod that reaches the asset is counted
under the panes and read in *Show XML…*, which cannot be typed in.

That leaves these with no place to be typed:

- **An op naming several assets.** On its row in the Mod changes panel,
  *Edit this pattern…* is greyed with "It names its assets. Change it in the
  XML view." It is in no asset's pane.
- **A pattern op**, other than one the pattern form composed. The form edits
  only its own kind of search.
- **An op in a group, and the group itself.** The op's condition is the
  group's, and not in its text, so it is not the asset's own. A third of the
  ops mods ship sit in a group (ops-as-written, section 4).
- **An op the Studio cannot read** that names no asset, or several.
- **An op typed in the pane for another asset.** It is applied to that asset
  and leaves the pane. The line beside Apply says it is in the Mod changes
  panel.

And some things a mod's author arranges are about the file and not any one
op: the order ops run in, which file each is in, what is said in comments
between them. The Studio keeps the order and a comment directly above an op
(its note). It keeps nothing of which file an op is in, and no other
comment or blank line.

The cause is still one thing. An op lives in a file, at a place in it, and
the Studio has no editable view of the file.

## 3. What is proposed

1. **A fifth main tab, after Asset editor**, for the file of the mod
   changes go to: a text editor, with an outline of its ops and groups
   beside it.
2. **Ops that reach the asset and are not its own are shown in the XML
   view's ModOps pane**, drawn so they cannot be taken for the asset's own,
   with their reach stated.
3. **An op that goes somewhere else says so before Apply**, the Mod changes
   panel selects what Apply made, and undoing an Apply brings the typed text
   back.
4. **The Mod changes panel can be collapsed**, to give the width back.
5. **Included files are shown under the `<Include>` line that names them**,
   read, written and created as files of their own, and with them the
   layout of a file: comments and blank lines.

Not proposed: making the Export tab's preview editable. Export is a review
and commit step with a good deal else on it, and not somewhere to work for
an hour. And not proposed here: panels that detach into windows of their
own, which is a proposal of its own.

## 4. The file tab

**Which mod.** The mod changes go to, chosen by the same *Changes go to*
picker the Asset editor has, and moving with it. No tabs for other mods: a
loadout can hold over a hundred, and another mod's file is one change of the
picker away.

**The text.** The mod's file as the Studio writes it
(`generator.build_modops_xml`, for that mod), with the XML view's editor
behaviour: Tab indents, Esc then Tab leaves, Ctrl+F finds, Apply
(Ctrl+Enter) and Revert, and the question on leaving with something not
applied.

- **The Studio's own comments go, but for the asset headings.** Two kinds
  were written for a Studio that made every op itself, and are taken out of
  every export with the options that ask for them. This can go in before
  phase 1, on its own.
  - *The file header* (*Header comment*): the mod's name, creator and
    description, "Generated by" with the date, "Built against", and how many
    ops of each kind. The modinfo has the first three and what the mod was
    built against (`modinfo.built_against`). "Generated by" is not true of
    an op carried as written, which is why the Export preview already leaves
    the header out (`build.preview_xml`). The counts go stale the moment the
    file is typed in.
  - *What the Studio says of each op* (*Comment each ModOp*, the lines that
    open with `:AMS:`). Over a kept op it says why the op could not be
    narrowed, or that it searches; over an op not read, that the Studio
    could not read it; over a narrow change, what it was and is
    ("Radius: 10 -> 25"), and a warning where the check against the game's
    files failed. The first two are the narrow reading's reasons, which no
    longer decide anything. The Mod changes panel and the Export tab say the
    rest, and the old value of a narrow change is the one thing in a file
    that nothing else there says.
  - *The asset headings stay*, as part of *Group ModOps by asset*, which is
    what they head. They keep the `:AMS:` mark, so that a heading is never
    read back as the note of the op under it. The reader goes on passing
    over `:AMS:` lines, and the older exports' wording, so that a file
    written before is read as it was.
- **The asset headings are drawn, not text.** They are made again on every
  write. In the editor they are lines it draws between the lines of text, as
  the band in section 5 is, so they cannot be typed over and left wrong.
- **A comment above an op is its note**, as in the ModOps pane today.
- **A change the Studio wrote is shown as the op it is written as.** Typed
  back as it was, it is still that change. Typed over, it is the op that was
  typed, where the change was. This is the ModOps pane's rule
  (`typedops._store`).
- **An op switched off is the comment it is written as.** With *Comment out
  switched-off changes* on, the writer puts a switched-off op in a comment
  that opens with `switched off in Anno Modding Studio:`
  (`changes.switched_off`), and the reader takes such a comment back as the
  op, switched off (`modops._collect_shelved`). That option goes, and every
  export writes them. It came in off unless asked for (design notes, *What
  is switched off goes into the file, as comments*) while the reader
  narrowed, and reading such a comment back was then tied to narrowing: an
  op came back as plain changes where its narrow form read the same. Read
  as written it comes back as the op it was, and with the option off a
  switched-off op is lost from the folder the mod is read back from. The game reads the same file either way (the
  census, `rt-45eed73`, 5 October 2026). The tab shows the comment, and the
  outline and Mod changes panel draw the op as switched off beside it.
  Typing round it follows from that: taking the comment's
  first and last lines away switches the op on, and an op put inside a
  comment that opens with that line is switched off. The box in the Mod changes panel does the same
  without typing. Two things inside the comment are not as they are in the
  op: `--` is written `-&#45;`, and the op's own comments have their dashes
  written the same way. They are shown as written.
- **An op the Studio cannot read is text like any other**, marked in the
  gutter. Typed over, it is read again, which is how such an op is fixed.

**The outline.** Beside the editor, every op and group in file order, built
as a tree from the start, so that it keeps its shape when files are added in
phase 4. A group is a row with its ops under it, saying its condition and
whether it runs today, as in the Mod changes panel.

- Each row is drawn as the Mod changes panel draws an op: the badge for its
  type, the four places for pattern, computed, conditional and the mod's own
  options, and what each comes to today ("41 assets", "2 entries", "runs").
  One way of drawing an op, wherever it turns up.
- The words for its target are the ops-as-written proposal's: how far it
  reaches at both levels, "41 assets, one entry in each" or "1 asset, 3
  entries".
- Double-click goes to the op in the text. A row's menu opens an asset it
  reaches.
- It can be narrowed: to pattern, computed or conditional ops, to ops not
  read, to ops switched off, and to ops whose reading is being read again.
  Narrowed to ops not read it is the list of things to fix.

**How it differs from the Mod changes panel.**

| | Mod changes panel | File outline |
|---|---|---|
| Unit | An op, with what it comes to under it; a change of the Studio's own | An op, or a group |
| Order | By mod; ops with rows of their own first, then assets | File order |
| A pattern reaching 41 assets | An op row with what it comes to under it | One row |
| Job | Review, switch off, revert, write narrow | Find your place in the file |

The outline is for getting about and for seeing state. Switching off,
reverting and *Write as narrow ModOps* stay in the panel, besides the
switched-off comment that can be typed; with them the outline would be the
panel twice. The two link: every row in Mod changes
that is written as an op gains *Show in file*, and the outline's rows open
the assets they reach.

### Lines

The tab needs to know which lines each op, group and drawn heading is on:
for the outline, for *Show in file*, for the band of section 5, and for
Apply to tell one op's text from the next. The writer works this out as it
writes and hands it back with the text. The XML view's own way, searching
the text for an op's opening tag (`xmlview._line_of_op`), would move to the
same answer.

### What Apply does

What is typed is what the mod holds, as in the ModOps pane. Apply compares
the typed text with the text the tab was filled with, op by op, as
`typedops._store` does for one asset's ops, widened to the whole file:

- **An op whose text is unchanged stays exactly what it was**: its place,
  its note, its switch, its uid, and so any edit that is an exception to
  it.
- **An op typed over** takes the place of the one it was typed over.
- **A new op goes where it was typed**, between the ops either side of it.
  The ModOps pane places a new op after the mod's ops, which is right for a
  pane that shows one asset and wrong for a file, where where it stands is
  what it does.
- **An op left out is taken out**, with what it came to.
- **A group is its tags.** Typing a group's opening tag over changes its
  condition. Taking its tags out leaves its ops where they were, in no group.
- Apply is one step of undo.

Then the reading:

- **At once, the ops that changed are read** the way the ModOps pane reads
  them today (`typedops._read`): each named asset as the mods before this
  one leave it, and a search against the game's files. That is quick, and
  shows straight away what each typed op comes to, close enough to write
  by.
- **Then the mod is read again where it stands**, in the background, from
  the first op that changed to its last, and the mods after it. An op
  changed is an op every later op may find differently, so this is the
  stale reading of ops-as-written phase 5 with one more reason: *edited
  here*. Its rows say *being read again* until it is done, and Export and
  Play wait for it, as they do now.

That second step is new work. Today `stale.check` notices a change in which
mods load and at what settings (`stale.stack`). An edit made in the Studio is
not one, so nothing after the edited mod is marked, whether the edit was
typed in the XML view or set in Fields. And `stale.read_again` reads a mod
from its folder, where an edit made here is not yet. Reading a mod from its
ops as the project holds them, and marking the mods after an edit, are both
needed by this tab, and would put right the same gap in the views there
already are (section 10, question 1).

Reading the whole file at once on every Apply, which the issue measured at
up to two minutes for the largest file, is not needed: identity is by text,
so an op not typed in loses nothing by not being read until the background
gets to it.

## 5. Ops beyond the asset, in the XML view

An op of the mod that reaches the asset and is not its own is shown in the
ModOps pane of its mod's tab, in file order among the asset's own, and typed
over there. Editing it changes other assets too, so the pane draws its reach
and does not leave it to a comment that can be read past:

- **A band.** The op's lines are on a tinted band, with its badge and marks
  in the gutter.
- **A heading the pane draws above it**, not text and not editable: "Pattern
  · reaches this asset and 40 others", or "In a group · if Harbour Life is
  loaded · runs". It opens the list of assets, or goes to the group in the
  file tab.
- **Apply says it.** When what was typed changes an op that reaches other
  assets, the button reads "Apply to 41 assets" before it is clicked.

An op in a group is shown as the op; the group's own tags are not in the
pane, because the group holds ops that do not reach this asset. Its heading
names it and its condition, and the group is typed over in the file tab.

An op the Studio cannot aim at any asset has no asset to be shown under. It
is in the file tab only.

This is the change to the XML view that ops-as-written made easy. With every
op kept as typed, an op that reaches 41 assets is text with a reading under
it, as an asset's own op is. The band and the stated reach are still wanted,
because typing over it from one asset's tab is still dangerous.

Open: whether the count on the button is worked out as the text is typed or
at Apply (section 10, question 2).

## 6. Ops that go somewhere else

Nothing here remembers where an op was typed. That would make what the
Studio shows depend on how you got there, and would not outlive a restart.

- **Before Apply, the pane says where each op goes.** Which assets an op
  names is in its attributes (`modop`), so this is known as the text is
  typed, without running the reader. An op that is not this asset's own is
  marked in the gutter, with "goes to Bakery", "41 assets" or "a search".
  Several ops for several assets each carry their own mark.
- **After Apply, the Mod changes panel selects the rows Apply added or
  changed** and scrolls to them. Double-click on a row already opens its
  asset. The line beside Apply gives the count, as it does now.
- **Undoing an Apply puts the typed text back** as text typed and not
  applied. Apply is one step of undo already; today the pane is filled again
  from the mod and the text is gone. No second button. Open: what undo does
  while another asset is in front.
- **The file tab is the lasting answer.** Where an op went is always the
  same place: the file, in order.

## 7. The Mod changes panel collapses

The panel takes a third of the window on every tab (`MainWindow._build_ui`
sets the splitter 2:1, with a floor of 340 pixels under the panel). It
becomes collapsible to a narrow strip, with a shortcut, and the window
remembers which. The file tab is laid out on the understanding that it can
be: editor, outline and panel side by side are tight at 1500 wide.

## 8. Included files, and the file's layout

**Today.** An unconditional `<Include>` is read where it stands, includes can
nest (to eight deep, `MAX_INCLUDE_DEPTH`), and the ops come in as though
written in the one file. Each op read from an included file knows the file
while it is read (`SourceOp.where`), and the kept op (`Op`) does not keep it.
Export writes everything to the mod's `assets.xml`. A file included on a
condition is a scope (*Weather Maker*), written out as a `<Group>` with the
file's ops written in. One that cannot be read goes back out as the
`<Include>` it was (*Super Specialists*). Mods written this way include
*Charcoal Power Plant*, seven includes and no ops of its own, and *Celtic
Independence Overhaul*, with its ops in 20 included files.

**Proposed.** The `<Include>` line is shown as written, and the file it
names is shown under it:

- **One text, not editors in a stack.** The included file's text follows its
  `<Include>` line as a region the editor draws with a band and a header
  naming the file, and which folds. Selecting, finding, cutting and pasting
  work across files because it is one document.
- **Each file is still read as a file.** Apply cuts the text at the regions'
  edges and parses each file's text on its own, as the game will. An element
  opened in one file and closed in another is unclosed in the first and a
  stray end tag in the second, and Apply refuses with both lines named, the
  way it refuses any text that is not well-formed today. So a cut and paste
  that lands half an op on each side of a boundary is caught where it was
  typed, and never reaches an export that the game would refuse.
- **Nothing is made up.** No marker comment and no invented element stands
  for the boundary. It is structure the editor holds; the text holds the
  include line the game reads, and the ops.
- **Moving an op to another file** is cut and paste, or a drag in the
  outline.
- **Typing an `<Include>` line makes a region**: the file's, where the mod
  has it, and an empty one where it does not. That is how a mod with no
  includes gets one.
- **The outline is a tree** of files, groups and ops.
- **A file included on a condition** is a region and a group at once: its
  header says the condition and whether it runs, as a group's row does.
- **A file in another mod's folder** (the shared pools convention) is the
  same region, read-only, its header saying whose it is.
- **A nested include** is a region inside a region. Depth is a stripe at the
  left edge and not an indent, which would cost width at every level.

**The layout.** The ops-as-written proposal left the layout of a file to
this one: comments between ops, blank lines, which file an op is in. With a
file of the mod's own to show, they are kept with it. A comment that is not
directly above an op, and a blank line, are kept where they stand among the
ops, and written back there.

The model, the reader and the writer each change: every op, group and
change of the Studio's own gains the file it is in, the reader records it,
and export writes the files. A round trip then keeps an author's layout. The
census's *ops as written* question (`tools/modio_roundtrip.py`,
`as_written`) compares every op and group of a mod's own files with its
export, in order; it would compare the files as well.

**A mod folded into yours.** The ops-as-written proposal noted that mods as
published take another mod in as an included file, and that the Studio
should probably do the same when you fold a mod into your own. This is
where that would be built. It is not designed here.

To decide:

- **Deleting an `<Include>` line.** Whether the file's ops go with it or join
  the file above. Either way Apply says which before it does it.
- **One file included twice.** The reader reports the second today and does
  not read it. Shown as a read-only pointer to the first.
- **An `<Include>` inside a `<Group Condition=…>`.** The reader writes the
  file into the group today (*Subway Network*); the editor shows the group
  and the region together.
- **An include the reader cannot follow** goes back out as itself today. It
  stays a line with no region.

## 9. Phases

0. **The Studio's comments**, from every export (section 4). *Built.* The header
   and what it says of each op go, with their options; the asset headings
   stay; and switched-off ops are always written as comments, the option
   for it gone. Small, and on its own.
1. **The file tab** for the one `assets.xml`: the text with the writer's
   headings drawn and switched-off ops as their comments, the outline as a
   tree with groups, the lines each op is on, Apply op by op with a new op
   placed where it was typed, *Show in file*, and the collapsible Mod
   changes panel. With it, the background
   reading learns to read a mod from the project's ops and to mark the mods
   after an edit.
2. **Ops beyond the asset in the XML view's ModOps pane**, banded, with
   their reach and "Apply to N assets".
3. **Ops that go elsewhere**: marks before Apply, selection in Mod changes
   after, undo putting the text back.
4. **Included files and the layout**: read, written, shown as regions,
   created and renamed, with comments and blank lines kept.

**As built, the tab is as section 4 has it, but for four things.**

- *The tab picks whose file it shows, apart from where changes go.* Section 1 has
  it hold the file of the mod changes go to. Built that way, reading another mod's
  file meant pointing changes at it and answering whether to patch it. The tab's
  picker now only shows a file; one that changes do not go to is read and not
  typed in, and *Edit this file* is what points changes at it. *Show in file* on
  another mod's row no longer moves where changes go.

- *A group's tags and a ModOp are counted apart* in what Apply says, since a group
  is no ModOp to whoever typed it.
- *The background reading follows every edit, not only an Apply here.* A step of
  undo says which mods it changed and from where, so a value set in Fields, an
  Apply in the XML view, a switch in the Mod changes panel and each Undo and Redo
  mark what they reach as well. That closes the gap for the views there already
  were (section 10, question 1).
- *A step of undo puts back the mod it was made in and no other.* Section 4 does
  not say what becomes of undo. A reading taken used to end the history, which
  after every edit would have left none. A reading asked for by edits alone
  leaves the steps as they are, and a step no longer puts back the ops of mods
  it did not touch, which a reading may have moved since. The design notes say
  what that needed.

**Phase 2 as built** is as section 5 has it, with these choices where it left one.

- *A new op typed in the pane goes where it was typed among the ops the pane holds:*
  in the file, straight after the one typed before it. Section 5 has the ops beyond
  the asset "in file order among the asset's own", which is how they are shown, and
  does not say where a new one goes. The pane holds part of the file, so the place is
  relative to what it shows; an exact line is typed in the file tab. An op typed over
  one keeps that one's place and its group.
- *An op left as it is in the text is not read.* What it comes to was read where
  the mod stands, for every asset it reaches. So a pattern in the pane costs nothing
  at Apply unless it is the op that was typed over.
- *The count on the button* is as question 2 suggests: ops that name their assets are
  counted as the text is typed, with the assets an op the mod held answered to. A
  search typed or changed says "Apply to a search", is read at Apply, and is asked
  about with the number before anything changes.
- *The heading opens a menu* with *Show in file*, *Show its group in file* and the
  other assets, up to twelve. It is drawn and not a control, so the same menu is on
  its line's right-click, which the keyboard's menu key opens as well.
- *An op in a group is shown whether its group runs or not*, since it is an op of
  the mod for this asset either way. Its heading says which.
- *A group kept whole* by the reader is not in the pane. It is counted under it, as
  before, and typed over in the file tab.

**Phase 3 as built** is as section 6 has it, with these choices where it left one.

- *The words are on the line the op ends on,* at the right-hand edge of the pane, and
  the mark in the strip is on the line it starts on. The opening line holds the GUID
  and the path and has no room at half a window's width.
- *Only an op typed in or over is marked.* One the pane was handed has its heading.
- *The panel selects what the step of undo added or changed,* so the same holds for
  the asset typed and applied. An op's row is selected and not each change under it.
  The file tab's own Apply selects nothing: its ops are in front of whoever typed
  them.
- *Undo with another asset in front* (question 3) goes to the asset the text was typed
  on and puts it back there. Where the pane it was typed in is not on show, in Fields
  or with changes going to another mod, the Apply is undone and no text is kept:
  Redo has it.
- *Text put back and not typed in is dropped at the next step of undo or redo.*

**Phase 4 is built in parts.** The first is the model, the reader and the writer:
which file each op is in, and an export that writes the files. It is on its own
because it changes what is written, and is what the census is run for.

- *The file is on each op, and the `<Include>` line is an op-like thing in its
  place* (`Op.file`, `Op.includes`). Section 8 has "every op, group and change of
  the Studio's own gains the file it is in". A change of the Studio's own does not:
  it is written where it stands, and after a file's last op that is the file above.
- *The tab still shows one text with no `<Include>` line.* Regions are the next
  part. Until then an Apply there keeps each op in its file and puts the lines back.
- *A file on a condition, a file inside a group and a file in another mod's folder
  are written into the file that names them*, as every file was. Section 8 has each
  of them as a region; they wait for the tab to show one.
- *Deleting an `<Include>` line* (to decide, in section 8): the file's ops join the
  file above. Built so because it is the answer that changes nothing in game.
- *Where a file's ops do not stand together after its line, the mod is written as
  one file.* Not in section 8. Written apart they would run in another order.
- *The census compares the files as well*, as section 8 asks: `as_written` has
  which file each op is in.

The second part is the tab: section 8's regions, built as it has them, with these
choices.

- *A region's end is held by the line after it.* A line typed at the end of the
  file's last line is the file's; one typed on the next line is not.
- *Where all of the text is put in at once, the regions are found again* by laying
  the old text beside the new. Section 8 does not say what a paste over everything
  does.
- *The header is drawn on a blank line the writer puts first in each file*, which
  is also where an empty file is typed in.
- *Depth is a bar at the edge of the strip for each file*, up to three, and a tint
  laid once for each.
- *A file folds by a click on its header.* Its lines are hidden and still applied.
- *Moving an op to another file* is cut and paste, or a drag in the outline
  (#196, `core.moving`). The drag is the same edit: the text changes, as one step
  of the pane's undo, and nothing is applied. The rows are then of the text as it
  stands, so drags follow one another; once anything is typed they are refused,
  and the rows are of the text before it. A row moves with its note and the blank
  lines before it; a group with all of it; a line that names a file with the file,
  named from the top of the mod once it stands in another file. A loose comment
  above it stays. *Move up* and *Move down* (Alt+Up, Alt+Down) do it a place at a
  time, stepping over a group or a file whole and out of one at its ends. A
  file's line goes into no group, and a drop into a folded file unfolds it.
- *One file included twice* (to decide): Apply refuses a second line for a file.
  The reader still passes a second one by when it reads a folder.
- *A line with its file under it can only name a file of this mod.* With a
  condition, or in a group, Apply refuses: those files are still written into the
  file that names them, and have no region.

The third part is the layout, as section 8 has it: "a comment that is not directly
above an op, and a blank line, are kept where they stand among the ops, and written
back there".

- *"Directly above" is taken at its word.* A comment with a blank line between it
  and the op under it is the file's and not the op's. It was the op's note before.
- *A note is still written as a note*, under its mark and on one line.
- *Blank lines are counted before each thing, up to eight.* Before a group's closing
  tag and before the end of a file there is what the writer puts.
- *The census compares the comments as well*, each where it stands (`as_written`).

Not built: a region for a file on a condition, in a group, or in another mod's
folder; and a mod folded into yours, which section 8 leaves undesigned.

Reading the whole of what was typed at every Apply was tried first, for being the
simpler thing, and taken out again. It read every op against the game's files as
the earlier mods leave them, which is less than the reading of a stack knows: where
two mods set one field to the same value, the stack's reading has a change for
each, and the mod read apart from it has none for the second. So an Apply that
changed one op lost what ops nobody had touched came to (census unit 5540293, two
mods of one author on one reward pool). It also read every op of the file to
change one, which on *GUID Explorer*, one ModOp for each of 18,057 assets, is
every one of them. Section 4 had it right.

Phase 1 is the largest and the rest stand on it. Phases 1 and 4 change what
is read or written, and each wants a census run of both games with the
loader before it merges, which is taken on the Windows PC with the game
files (`tools/modio_roundtrip.py`). Phase 4 changes the model and the
export, and wants the bench run as well (`tools/bench.py`).

## 10. Open questions

1. **Marking the mods after an edit.** Phase 1 needs an Apply in the file
   tab to mark the mods that load after the edited one. Whether every edit
   should, a value set in Fields and an Apply in the XML view among them,
   since each changes what those mods find. Probably yes, and it is the same
   change. *Settled as built: every edit does (section 9).*
2. **"Apply to N assets": counted as typed, or at Apply.** An op that names
   its assets can be counted as it is typed, from its attributes. A pattern
   has to be read to be counted, which is a read on every pause. Suggested:
   named ops counted as typed; a pattern says "Apply to a search" and is
   counted at Apply, with a step to confirm. *Built as suggested (section 9).*
3. **What undoing an Apply does while another asset is in front.** *Settled as
   built: it goes to the asset the text was typed on (section 9).*
4. **What deleting an `<Include>` line does to its file's ops.**
5. **How deep includes nest in practice.** Not yet counted; one or two levels
   are expected. `tools/op_counts.py` reads includes where they are named
   and could count their depth in the same run, on the Windows PC.
6. **Other files a mod patches** (`templates.xml`, texts, model files). They
   are other documents and not includes, and are left out of this proposal.

## 11. What this leaves alone

- **What an op is, and how it is read.** The reader and the description of
  an op are the reader proposal's.
- **Fields**, and everything the Studio writes for it.
- **The Export tab**, which stays a review and commit step.
- **Mods that are loaded and not touched.** They are not written today, and
  are not after.
