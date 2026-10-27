# XML view: a proposal

How the asset editor comes to show, and take, XML: the asset as each mod finds
it, the ModOps each mod writes about it, and two places to type — your mod's
ModOps, and the asset as your mod should leave it. Written against the editor
as it stands after *Show XML…* (`ui/xml_view.py`, `core/build.py`), the reader
in `core/modops.py`, `core/effective.py`, `core/workspace.py` and the design
standards, and against a small experiment recorded in section 3.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (5 October 2026).** Phase 1 is built: the Fields and XML switch,
the tabs with both panes, find, Ctrl+F and Ctrl+P, and the jump between a
field and its XML (`core/xmlview.py`, `ui/asset_xml.py`). Every pane is
read-only. Where it differs from the text below: the tab of the mod changes
go to carries the pencil already, though nothing can be typed in it yet; a
later mod that writes over yours is marked on its tab and in the asset, with
the fields named under the panes; and what counts as written over is the
conflicts list's to say, so a restated value is not marked.

Phase 2 is built too: the ModOps pane of the mod changes go to is typed in,
with Apply, Revert, the line that says what was read, one step of undo and
the question on leaving (`core/typedops.py`). Where it differs from the text
below: Apply moves only what differs from what the mod has, so an op left
alone keeps its place; a pane is typed in only where its own text reads back
as the changes it was written from, which is asked as it is shown, and 932
of 940 sampled did; and Tab indents, with Esc and then Tab to leave the pane,
since Ctrl+Tab belongs to the window's own tabs. Open question 1 is settled as guessed: an op aimed at a GUID is
read against that one asset.

Phase 3 is built: `KeepAsWritten="1"` on an op, honoured by the reader
wherever a mod is read and written back with the op. Where it differs from
the text below: an op that names this asset and no other stays in the pane
whether it is narrow changes, marked to be kept as written or carried
unread, so a marked op is typed over where it was typed; an op the reader
keeps for its own reasons stays beside the pane, because read alone it
comes to something else. A change's row in the Mod changes panel offers
**Keep as written**. The kept op's row, and each change under it, offers
the same entry ticked, which takes the mark off. Each is the same as typing
the attribute and applying.

Phase 4 is built, and with it all four: the asset pane of the mod changes go
to is typed in. Where it differs from the text below: only the nodes that
differ are put to the reader, not a replace of the whole asset, which the
padding in real assets made the reader refuse; a change with no narrow form
is refused with the reader's reason and not written as a swap of the whole
list; a change beside `<Values>` is refused; where the asset as shown does
not come back as the changes the mod has, what is typed is laid over them as
an edit in the tree is, and none is taken away; and `MAX_CHANGES_PER_OP` now
counts for each differing node and not for the asset. Open question 3 was
settled as recommended, a placeholder taken out is not written, and then
turned round after the hands-on baseline of 9 October 2026: the entry came
back as though nothing had been typed, while *Remove from asset* in Fields
wrote the removal. A placeholder taken out by hand is written, and the line
says the game may not notice.

**Recommendation in one paragraph.** The asset panel gets a view switch,
**Fields** and **XML**. XML view shows one asset as a strip of tabs in load
order: the base game, then each mod that reaches the asset. A mod's tab holds
two panes side by side: the **ModOps** that mod writes about this asset, and
the **asset** as that mod leaves it. So reading the tabs left to right is
watching the asset being patched, and the last tab's asset is what the game
loads. Every tab is read-only but yours, where both panes can be typed in.
Typing ModOps puts them through the reader that reads a downloaded mod, and
an op can be marked to go out exactly as typed. Typing in the asset pane — or
pasting a whole asset there and changing it inline — has the Studio work out
the narrow ModOps that make the same change, and the ModOps pane shows them.
Nothing new is invented to do either: the reader already turns a wide replace
into narrow changes, and the generator already writes one asset's part of a
mod. Build it in four phases — read-only, ModOps typed, keep as written, asset
typed — each usable when it lands.

---

## 1. The problem

A power user reads the game's `assets.xml` and writes XPath by hand. What they
ask of the Studio is "what is this doing, literally?" The answer exists, in
two places that are both off to one side:

- the Export tab shows the whole mod's document, for every asset at once
- *Show XML…* on a row of the changes list shows that row's ModOps in a dialog

Both are a check made after the work. Neither is a place to work. The asset
panel, where the work happens, shows a tree of fields: a faithful picture of
the asset, and no picture at all of the ops that make it so. Someone who
thinks in ModOps has to translate every edit into a click and then go and look
at whether the click wrote what they meant.

The reference point is StarCraft II's data editor. With its XML view on, the
pane beside the object list shows what the selected mod says about the
selected object. A tab per dependency walks from the base game to the open
mod, and only the open mod's tab can be typed in.

## 2. Where that model fits Anno and where it does not

SC2's XML is shaped like the object. A mod's entry for a weapon is a partial
weapon, merged over the one below. So "what this mod says about this object"
is a region of a file, and editing it is editing the file.

ModOps are not shaped like the asset.

- One asset is reached by several ops, and their order matters.
- One op can reach many assets. The Studio keeps such an op as a **rule**
  (`core/changes.py`, `Rule`), written back as itself.
- Some ops the Studio carries without reading them (`Unread`).
- A mod's file is not what the Studio holds. It holds its reading of the
  file, and writes that reading back out.

So here "what this mod says about this asset" is a question asked of the
mod's ops, not a place in its file. `build.fragment_xml(project, origin, keys,
rules, unread)` already answers it, for *Show XML…*.

And "XML" means two different things, where SC2 has one:

| | What it is | Who is used to it |
|---|---|---|
| **Asset XML** | The asset, as the game ships it or as it stands after some mods. | Anyone who has read `assets.xml`. |
| **ModOps XML** | What one mod does to it. | Anyone who has written a mod by hand. |

The proposal shows both, and says which is which on every tab.

One more difference decides the tab order. In SC2 the open mod is on top and
wins. Here the mod you write into can sit anywhere in load order, and a mod
that loads after it can overwrite its work. The editor already knows this
(`EditorPanel._later_claims`), and XML view has to show it.

## 3. What was measured

Six ops typed by hand were read by `reader.Translator` against the small
index the import tests use (`tests/support.py`, `IMPORT_ASSETS`), put into an
empty project, and written by `generator.build_modops_xml`.

| Typed | Read as | Written back as |
|---|---|---|
| A `merge` on `/Values/Building` setting two fields | 2 changes | Two `replace` ops, one per field. |
| A `replace` of `/Values/Building`, whole node retyped, one field different | 1 change | One `replace` of `Radius`. |
| A `replace` of `/Values`: the whole asset pasted, one value changed, one cost changed, one cost entry added, one product entry left out | 4 changes | A `remove` of `Item[Product='2002']`, two `replace` ops, one `add` to `Costs`. |
| A `merge` aimed at a shape, `//Values[Building/BuildingType='Farm']/Building` | 1 change and a rule | The op, exactly as typed. |
| An `add` of a list entry, then a `remove` of the same entry | 0 changes | Nothing: the second undoes the first. |
| A `replace` with `Condition="#SomeOtherMod"` | not read | (see below) |

Three things follow.

**The asset pane costs little.** The third row is the beginner's habit the
Studio has always wanted to put right: copy the asset, change it inline, ship
a wide replace. The reader already turns that into the four narrow ops that
do the same thing. Typing in the asset pane is that reading, with the replace
made up on the user's behalf.

**The Studio rewrites what you type.** The first row comes back as two ops
where one was typed. That is the Studio's rule (design notes, *Why one ModOp
per change*) and it is right for stacking. It is also a surprise to someone
who typed the op on purpose. Section 6 deals with it.

**The entry point matters.** The last row failed because the experiment drove
the translator bare. `reader.analyse` tells it which mods are present, and
then such an op is kept as written. Typed ops must go through a reading that
is set up the way an import is, or they will be read worse than the same ops
in a downloaded file.

## 4. What it looks like

The asset panel's header gains a two-way switch, **Fields** and **XML**. The
search box, *Changes go to:*, the asset's name and the back and forward
buttons stay where they are and mean what they mean now.

```
 Search…                                   Changes go to: [ Better Bricks ▾ ] ?
 Brick Factory  ·  GUID 1010283                                [ Fields | XML ]
 ┌─────────────┬──────────────────┬─────────────────┬─────────────┐
 │ 🔒 Base game │ 🔒 Harbour Tweaks │ ✎ Better Bricks │ 🔒 Rebalance │
 └─────────────┴──────────────────┴─────────────────┴─────────────┘
 ┌ ModOps Better Bricks writes ────────┬ The asset after Better Bricks ─────┐
 │ <ModOp Type="replace" GUID="1010283"│ <Asset>                            │
 │        Path="/Values/…/CycleTime">  │   <Template>FactoryBuilding7</…    │
 │   <CycleTime>15</CycleTime>         │   <Values>                         │
 │ </ModOp>                            │     …                              │
 │                                     │     <CycleTime>15</CycleTime>      │
 ├─────────────────────────────────────┴────────────────────────────────────┤
 │ Also reaching this asset: 1 rule of Better Bricks   [ Open rule… ]       │
 │ Read as 1 change.                                    [ Revert ] [ Apply ]│
 └──────────────────────────────────────────────────────────────────────────┘
```

### The tabs

In load order, left to right.

| Tab | Left pane | Right pane | Typed in? |
|---|---|---|---|
| **Base game** | — | Asset XML, as the loaded game files have it. | No |
| A mod that reaches this asset | The ModOps the Studio writes for it, for this asset. | The asset after that mod: what it hands to the next. | No |
| **Your mod** — the one *Changes go to:* names | Its ModOps for this asset. | The asset after your mod. | Yes, both |

- Every mod tab has the same two panes, yours or not. The right pane of one
  tab is what the left pane of the next is applied to
  (`workspace.effective_asset(guid, upto=origin)`), so the strip reads as the
  patching itself, one mod at a time.
- A mod that does not reach the asset has no tab. Your mod always has one.
- A tab right of yours belongs to a mod that loads after it. Where that mod
  sets a field yours also sets, its tab carries the conflict glyph and the
  field is marked in both tabs' asset panes.
- Choosing another mod in *Changes go to:* moves which tab can be typed in.
  That is the existing control and the existing rule; XML view adds nothing
  to it.
- A read-only mod tab is that mod's ModOps **as the Studio writes them**, the
  same text *Show XML…* gives. The mod's own file may bundle them
  differently. The pane's heading says which it is, as the dialog's does.
- A rule or a carried op that reaches the asset belongs to more than this
  asset, so it is not in the typed text. It is listed under the panes, with
  a button to where it is edited.

### There is no Result tab

The first draft had one. With an asset pane on every tab it has nothing left
to show: a rule runs in its own mod's turn, at the place its op sits in that
mod's file, so what it does to this asset is already in that mod's tab. The
last tab's asset pane **is** the asset after every loaded mod, and its
heading says so: *The asset after Rebalance — what the game loads*.

Two things can make the game's asset differ from that pane, and both are
said on the pane, not left for a tab to imply:

- **An op the Studio carries without reading reaches this asset.** The Studio
  cannot show what it does. Fields view already warns of this
  (`EditorPanel._unread_reaching`); the same line appears under the pane of
  the mod that carries the op, naming the op.
- **The loader check found a difference here.** Where
  `loadercheck.Verdict.differing` names this asset, the last pane says that
  the loader's copy came out different, and offers the comparison the Export
  tab already has.

### Which tab can be typed in

Shown, not said. Three signals, so none of them is colour alone (standards,
section 8):

- **The surface.** A pane that takes typing is drawn as a text field is: the
  field background, a caret, the focus ring. A read-only pane sits on the
  panel's own background with no caret. Its text is not dimmed: it is there
  to be read.
- **The glyph on the tab.** A pencil on yours, a lock on the rest, drawn as
  SVG like every other image. The sketch above stands in for them.
- **The bar.** *Apply* and *Revert* exist only under your tab.

The words are still there for a screen reader: each pane's accessible name
says whose XML it is and whether it can be edited.

### Fields view keeps its link

A row of the Fields tree gains **Show in XML view**: it switches the view and
puts the cursor on the op that writes that field. The reverse, from an op to
its field, is the same jump the other way. That is the link between a click
and what the click wrote, without a dialog in between.

## 5. Typing: one rule for both panes

Text being typed is not XML most of the time, so nothing is read while you
type.

- A pane that has been typed in is marked as edited, and **Apply** and
  **Revert** light up. Ctrl+Enter applies.
- **Apply** reads the pane. If it is not well-formed, the line and column
  are shown, the cursor goes there, and nothing changes.
- If it reads, your mod's changes for this asset are replaced by what was
  read, both panes are written again from the project, and the line under
  them says what happened: *Read as 4 changes.*
- One Apply is one step of undo (`core/undo.py`), in the same history as an
  edit in Fields view. The changes list updates as it does for any edit.
- Typing in one pane locks the other until Apply or Revert. Two edited
  panes would be two answers to one question.
- Leaving the asset with a pane edited asks: **Apply**, **Discard**, or
  stay.

### Finding text, and what Ctrl+F means

A pane of XML needs find-in-text: a bar under the pane, Enter and F3 for the
next match, Shift+F3 for the one before, Esc to close. Anyone who has used a
text editor will press Ctrl+F for it.

Ctrl+F is taken. It is *Focus search* in the View menu, the search for an
asset (`MainWindow._focus_search`). Having it find text in one view and
search for an asset in the other would make the key depend on a switch, so
it changes in both:

| Key | Does | Now |
|---|---|---|
| **Ctrl+F** | Finds inside the asset in front of you. In XML view, the find bar of the pane with the focus. In Fields view, the *Filter fields* box, which has no key today. | Searches for an asset. |
| **Ctrl+P** | Searches for an asset. Renamed *Search for an asset* in the menu. | Unused. |

Ctrl+P is the go-to-file key of the editor most hand-written mods are made
in, and the Studio prints nothing. This is the one part of the proposal that
changes a habit outside XML view, so it goes out with phase 1, on its own
line in the release notes, with the README's shortcut table and the
shortcuts dialog.

### The ModOps pane

Apply puts the typed text through the reader an import uses, against the
asset as it stands when your mod loads (`workspace.asset_before`). Each op
ends up as one of the three things an imported op can be, and the line under
the panes counts them:

- **narrow changes**, the usual case
- **kept as written**: a search, a value worked out while patching, a
  condition on which mods are loaded. It becomes a rule and moves to the
  list under the panes.
- **carried**: an op the reader cannot follow. It goes out as typed, and the
  line says why it was not read.

An op that names another asset is applied to that asset, and the line says
so and names it. Refusing it would make the pane stricter than the file it
stands for.

### The asset pane

Apply compares the typed asset with the asset as it stands when your mod
loads, and writes the narrow ops that turn one into the other. It is the
reading of a wide replace (`recording.diff`), so it inherits what that reading
already decides:

- A list entry is matched by its key where the list has one
  (`Item[Ingredient='1010']`), so reordering text is not a change.
- Leaving out a placeholder entry — a cost of nothing — changes nothing in
  game and is not written (design notes, *Removals a wide replace only
  implied*). The line says how many were left unwritten.
- Where a list gives the reader nothing to match entries on, the change is
  written as one swap of the whole list, and the line says the op is wide
  and why.
- `MAX_CHANGES_PER_OP` (200) applies. Past it, nothing is applied and the
  line says how many changes the text came to.

This pane is less literal than the rest of the Studio: you say what you want
and are not shown the XPath until after. The pane beside it is what makes
that acceptable. Paste, change, Apply, and the ops that did it are on the
left to read.

## 6. The Studio rewrites what you type

After Apply the ModOps pane is written again by the generator. A typed merge
of two fields comes back as two replaces. A positional path comes back keyed.
Comments come back as the Studio writes them, with a comment typed above an
op kept as that change's note.

This is the same thing that happens to a downloaded mod, and the design notes
give the reason: narrow ops stack and wide ones do not. Three things make it
honest here.

1. **It is said.** The line under the panes reads *Read as 2 changes. Each is
   written as its own ModOp.* with a `?` that gives the reason.
2. **It is stable.** Applying text the Studio wrote must change nothing.
   Phase 2 does not ship until that holds: for every asset of every mod in
   the census, the asset's fragment read back gives the changes it was
   written from.
3. **There is a way to say no.** An op can be marked **Keep as written**. It
   then goes out exactly as typed, as an op that already does for the
   reader's own reasons (`Rule.kept`), with its changes shown underneath it
   in the list for reading. The `?` says what it costs: a wide op claims
   every field in the node it names.

### Keep as written has to survive the file

The mark is no use if it lasts only as long as the project. A mod is
exported, published, downloaded and read in again, by its author after an
update or by someone else's Studio, and each of those readings would narrow
the op again. So the mark is written into the mod's file and honoured when
the file is read.

It does not work that way today. An op the reader keeps for its own reasons
is exported under a comment that says *kept as written* and why
(`generator.build_modops_xml`). Reading that file back, the comment becomes
the op's note and the op is judged afresh. For the reader's own reasons that
is harmless, because the same reason is found again. For a choice a person
made there is nothing to find.

Two places the mark could go: a comment above the op, or an attribute on
the `<ModOp>`. The attribute is the one to use.

- **It travels with the op.** A comment is beside the op, and a hand edit or
  another tool can part them.
- **It adds no third reader of comments.** The reader already takes the
  comment above an op as its note, and the generator writes comments there
  of its own. A mark in the same place would be one more thing reading
  that text for meaning.
- **Both games run such an op as they run it without.** Measured in the
  games on 4 October 2026, with a probe mod of marker ops
  (both logs kept, outside the repository).

| Asked of each game | Anno 117 | Anno 1800 |
|---|---|---|
| `KeepAsWritten="1"` on a replace: last attribute, first attribute, spelled with hyphens, with an empty value | runs | runs |
| The same on a merge, an add, a remove, an addNextSibling, a search with no GUID, an op with a `Condition` | runs | runs |
| A plain op inside `<Group KeepAsWritten="1">` | runs | runs |
| The shorthand form, `<ModOp GUID=… Replace=…>`, attribute last and first | runs | not asked: the game does not read the form, attribute or not (*Unknown ModOp*) |
| `studio:keep="1"`, the namespace declared on `<ModOps>` | runs | runs |
| What the loader's log says about the attribute | nothing | nothing |

Both xmltest builds had said the same beforehand. One start of each game, so
this is what they did, not proof of what they always do; nothing in it
suggests otherwise.

So the mark is `KeepAsWritten="1"` on the op: a plain name, since a prefixed
one buys nothing here and makes the reader handle a namespace. An op kept as
written is rendered with its own attributes already (`modops.render_op`), so
the mark goes out with it and nothing new writes it. Reading it is one name
added to `KNOWN_ATTRS` and one more reason in `Translator.keeps`, which is
where every other reason to keep an op is decided. That covers every place a
mod is read: an import, a mods folder, a re-read at other settings, and
Apply in this pane. Typing the attribute by hand sets the mark, which is
what somebody who works in XML would expect.

What other tools make of the attribute — a linter, another mod manager — was
not tested.

It applies to an op the reader can read. An op it cannot read is carried as
written already, and stays so.

## 7. Against the design standards

**Is it a mode?** Section 1 of the standards rules out an easy mode and an
advanced mode. This is neither. Both views edit the same mod through the same
target box, the same undo history and the same changes list, and every menu
entry stays where it is. Fields is not the beginner's view: it is the better
one for finding a field by name. The switch is remembered between sessions.

**Labels.** *Fields* and *XML* on the switch. Pane headings name the mod:
*ModOps Better Bricks writes*, *The asset after Better Bricks*. No heading
says "read-only": the surface and the glyph do (section 4). The buttons
are *Apply* and *Revert*; the status line uses the same verb, *Applied 4
changes to Brick Factory.*

**Help.** One `?` on the switch says what each view is. One on the panes'
status line says why the text is rewritten.

**Empty states.** With no asset chosen, XML view shows what Fields view shows
now. A mod's ModOps pane with nothing in it says, in one sentence, that this
mod does not change the asset yet and that either pane can be typed in.

**Look and access.** `XmlHighlighter` and `theme.monospace` are reused, so
the colours are the scheme's tokens already. Every pane has an accessible
name that says whose XML it is. Tab leaves a text pane with Ctrl+Tab, as it
must in any editor that accepts a tab. The tab strip is reachable and
switchable by keyboard.

**German.** About thirty new strings. *ModOp* and *XML* are glossary terms
already; *XML view* joins them.

## 8. Phases

Each ends in something that works alone.

**Phase 1 — read it.** The switch, the tab strip with both panes on every
mod's tab, all read-only and kept current as Fields view edits. Find in a
pane, and Ctrl+F and Ctrl+P as section 5 has them. *Show in XML view* from a
field. A `core/` function that answers "which mods reach this asset, in load
order, and with which changes, rules and carried ops", so the panel asks one
question and owns no logic. Small to medium: it is `fragment_xml` and
`effective_asset(guid, upto=…)` per mod, laid out, plus the two notes on the
last pane.

**Phase 2 — type ModOps.** Apply and Revert, the reading, the status line,
undo, the prompt on leaving. Before the pane is made editable: the census
check of section 6, and a way to read one asset's ops in the stack without
reading the stack again (section 9, first question). Medium.

**Phase 3 — keep as written.** The mark on an op, the attribute that carries
it in the file, the reader honouring it everywhere a mod is read, and how
the changes list shows it. Medium. It follows phase 2 directly because
without it the ModOps pane rewrites everything a power user types.

**Phase 4 — type the asset.** The asset pane becomes editable. Mostly the
wording of what was left unwritten and why, and tests over the asset shapes
the reader finds hard: lists without keys, entries that differ only by
position, an asset your own mod creates. Small to medium.

## 9. What is not settled

1. **Reading one asset's ops in place.** `analyse_in_stack` reads every mod
   up to the one asked about, which is right for re-reading a mod and far too
   slow for a keypress on Apply. A typed fragment aimed at one GUID needs
   only that asset as it stands, which `asset_before` gives. A typed search
   needs the whole document. The likely answer is to read GUID-aimed ops
   against the one asset and hand a search to the path the rule dialog
   already uses; it has to be tried before it is promised.
2. **Order among the mod's other ops.** Typed ops keep their order among
   themselves. Where they sit against the mod's ops for other assets is
   decided by `ModProject.place`, as for any change made here. For a mod
   written in the Studio that has never mattered. For a downloaded mod being
   adjusted, whose ops have places in a file, it may.
3. **A placeholder removed on purpose.** In an imported wide replace a
   missing placeholder is an accident, and leaving it unwritten is right. In
   the asset pane somebody deleted the line. The game still does nothing
   with it, so the recommendation is to treat it the same and say so; the
   alternative is to write it because it was asked for.
4. **A loaded mod's own text.** A read-only mod tab shows the Studio's
   writing of that mod. Showing the lines of its file instead would need the
   reader to keep each op's source and line, which it has while reading and
   does not store.
5. **Very large assets.** Some assets run to thousands of lines. The panes
   should hold up; it is to be measured in phase 1 against the largest asset
   in each game, with the highlighter on.

## 10. What this leaves alone

- **The whole file.** XML view is about one asset. Typing in the mod's whole
  document is a different feature with a different risk: every asset at once
  and no tree to check against. The Export tab goes on showing it read-only.
- **Other files.** Texts, templates and the other patches a mod can carry
  are not shown.
- **The changes list.** It gains nothing and loses nothing. *Show XML…*
  stays: it answers for a row, a rule or a whole mod, which this does not.
- **Who wins.** The tabs show load order and where a later mod overwrites
  yours. Deciding what to do about it stays with the conflicts dialog and
  with making a patch.
