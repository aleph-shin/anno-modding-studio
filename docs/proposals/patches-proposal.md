# Changing somebody else's mod: a proposal

Your changes to a mod you installed, kept in one place, surviving the
author's next update — including changes to ops that search, compute or ask
a condition, which a patch mod cannot reach today. Written against the
patch code as it stands (`core/patching.py`, `ui/patches_controller.py`),
the library and Play (`core/library.py`, `core/build.py`, `core/play.py`),
and a conversation on 13 to 15 October 2026 that started from one question
about *Edit pattern*.

Written on 15 October 2026, against `main` at `edc476b`.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Recommendation in one paragraph.** Treat the mods folder as a build
output. The library keeps every mod exactly as its author shipped it and is
never written to. Your changes to a mod live in its patch, in the project.
At Play, the Studio writes a **generated copy** of each mod a patch edits —
the author's files with your edits spliced in — into a folder beside the
library, and links that into the mods folder in place of the library copy.
The game's own loader still does all the patching, so nothing depends on
the Studio's reading being right. A patch stays one thing to the user — the
changes that only make sense while a mod is in effect — and holds two kinds
of entry: edits to the author's ops, applied in the generated copy, and ops
of your own, written to the patch's own folder as now. Each edit is
anchored to the op it changes by a hash and carries that op's text, so an
update that moves the op is followed silently and one that changes it is
shown as a three-way comparison. A patch can need more than one mod.

**Status (16 October 2026).** Phases 0 and 1 are built. Phase 0: *Edit
pattern* on somebody else's pattern writes it in your patch, or changes it in
their file only where their mod is the one changes go to or you choose that
when asked; *Write as narrow ModOps* is greyed for such a mod. Phase 1:
generated copies (`core/generated.py`), in a `generated` folder beside each
game's library folder, and iModYourAnno settings written into them by Play
and by an update, never into the library's copy. Two things differ from what
is written below: every folder of a generated copy is a real folder, with no
junction inside it (section 5, *The folder*), and the check after building
compares bytes, not readings (section 5, *Checked every time*). Questions 1,
2 and 6 of section 12 are answered there.

Phase 2 is built: your edits to somebody else's library mod -- an op typed
over, taken out or switched off, an op of yours placed among theirs -- go into
its generated copy as `Skip` on their op and yours after it, every other byte
theirs (`core/edits.py`, `core/splicing.py`), and the mod is never built over
the library's copy. Picking such a mod in *Currently editing* asks nothing, and
*Edit pattern* and *Write as narrow ModOps* change it directly. Four things
differ from what is written below, each said where it is: the edits are not
records of their own but the project's existing hold on a mod read again
(section 6, *As built*); the panel marks the mod's row and its patch's row
rather than drawing two groups (section 4, *As built*); no patch is made on
the first change, and a bundled mod is still changed through a patch (section
4, *What goes away*); and somebody else's mod in a folder of your own is still
built where it is.

Phase 3 is built: when a mod you edited is read again -- after an update, or
any read of the folder -- each op of theirs an edit of yours was made to is
looked for in what was read (`core/held.py`). Found, the edit stands; found
elsewhere, it follows and that is said; changed or gone, the edit is held --
switched off, so the game gets their new version -- and the three, theirs as
it was, theirs now and yours, are shown side by side to decide
(`ui/held_dialog.py`). An update keeps the replaced version's text one
version back. Three things differ from section 6: an edit is tied to its op
by the op's text and its place in the file, not by a hash; the previous
version is kept but nothing shows a whole-mod comparison with it yet; and
the advice on an op whose matches moved is not built.

Phase 4 is built: a patch keeps the mod it was made for and a list of the
others it needs (`ModLayer.needs`). It loads after all of them, is inert
while any is off, unloaded or replaced, and its modinfo declares them all to
load after and as dependencies, with its own block listing the rest so that
a folder read finds them again. Its row names them all, each other one's row
says it is patched there too, and its menu adds and takes away mods it
needs. One thing differs from section 4: the patch's row stays under the mod
it was made for, wherever that is in load order, rather than after the last
mod it needs.

Phase 5 is built: an export of a patch -- a folder, a zip, an update on
mod.io, *Copy XML*, *Save assets.xml* -- carries each edit to the mod it was
made for that can be an op of the patch's own, at the top of the patch, and
the preview lists the rest, each with why (`core/sharing.py`). The export
says how many it left out. Play writes the patch without them, as before.
What is turned goes past the first two rows of the table in section 1 in
three ways and stops short of them in three. A value typed over inside an
asset their op adds goes as a merge on that asset, by its GUID. One inside a
list entry goes as a merge on that entry, found by what it held once their op
had run (`Item[Product='1010200']`), never by its place in the list, and for
an entry their op adds to an asset already there, only where the base game's
own entries hold no such key. And where a tweak tool's knobs set values in
their op, only the values you changed go, as merges, unless one is a value a
knob sets; an op or a group a knob switches on and off is left out. A wider search is
turned only where it is more GUIDs on the same path, not by asking which
assets a search matches. An edit is left out where a later op of their mod
changes the same thing, since the patch would run after that op rather than
before it. Whether two ops change the same thing is asked of the nodes each
touches, by GUID and path down to each value it sets or entry it adds: a list
entry keyed otherwise, or a field beside it, is not the same thing, and a
node replaced or removed is everything in it. And one under a condition that reads the game's files is left out,
unless all the condition asks is whether an asset is there that their mod
neither adds nor removes. Only edits to the mod the patch was made for are
carried, not to the other mods it needs. The loader's check on the Export tab
checks the patch as an export writes it, on top of their mod as shipped,
while Play's checks the patch Play writes, on top of the generated copy.

Phase 6 is built as far as it can be without Linux to try it on: the link
convention, the spelling of a mod's files, and a check that the game can see
the library. A link is a junction or a symbolic link wherever the Studio asks
(`library.is_link`), and where there are no junctions the library links mods
in with symbolic links, by the target's whole path. A mod's files are read
and written by the mod's own spelling of them (`disk.as_spelled`). That
mended a fault on Windows too: in a mod spelling its folders `Data/Config`,
the edits were keyed by the Studio's spelling, so the generated copy kept
the author's file unedited, failed its own check, and was not used. And off
Windows, Play says when the game, run through Proton, could not see the
library: when it is not under the home folder, Steam's own folders, a Steam
library or a folder the game's launch options give the container
(`core/proton.py`). Nothing of this has been run on Linux. The rest of
running there -- starting the game through Steam, keeping secrets, finding
the game's folders under Proton -- is not part of it, and the Wine questions
of section 7 are still open.

---

## 1. The problem

### *Edit pattern* writes into the author's file

`PatternDialog` takes the mod the op came from, not the mod changes go to
(`ui/pattern_dialog.py:142`), and `typedops.store_one` drops the original op
and puts the new one in its place in that mod. Nothing on that route asks
whether the mod is yours: not the menu entry (`ui/changes_dock.py:1970`),
not the double-click on a pattern's row (`ui/changes_dock.py:2199`), not
`PatternsController.edit_pattern`. So with a patch made and chosen as the
target, double-clicking one of the author's patterns still edits the
author's file. For a bundled mod it goes round
`patching.edits_need_a_patch`, the rule that changes to one always go to a
patch. The XML view already shows another mod's ops read-only in this
situation; *Edit pattern* is the odd one out.

### A patch cannot change an op that works something out

A patch loads after its mod, so it only ever sees the document the mod
leaves. It can add to what the mod did; it can never change the mod's ops.
What the Studio offers today is a value typed over one of the mod's answers,
which becomes a narrow change by GUID in the patch, and *New pattern*, which
lets you write a search of your own by hand. Neither is tied to the op it
answers. How well an edit to the author's op can be said as "run this
afterwards" depends on the edit:

| Edit to their op | As a patch | Survives their update? |
|---|---|---|
| A different fixed value, same search | Your own op, same search, your value | Yes, and it still follows the search |
| A wider search | Your own op for the extra matches | Yes |
| A narrower search, or stopping a conditional op that ran | The excluded assets put back to their earlier values, by GUID | Only as measured against the version read |
| An add or a remove | Counter-ops: remove what was added, add back what was removed | The same: frozen |
| A computed or copied value | Only the result is visible afterwards, so fixed values | The same: frozen |
| Making a conditional op run that did not | A copy of the op in the patch | Yes, but a copy, not theirs |

Even the first row has a catch: the same search can match differently once
their op has run, where the op changes what the search tests.

`core/patching.py` describes a patch as the community's answer to an update
taking your work, and says it "may need a tweak where the author moved
something". For the last four rows it is not a tweak; the patch was never
able to say the thing.

### Editing in place is overwritten, and the library copy is not as shipped

Declining the patch keeps your edits in the author's mod. Play then builds
that mod (`build.mine`, since it has changes of yours) into the library
under its own folder name (`generator.folder_for`), over the copy that was
installed there, and records it as built. The next update replaces that
folder. The library copy is rewritten for a second reason too: Play writes
the live loadout's iModYourAnno settings into the library copies of the
mods that have them (playing proposal, section 6). So "the library holds
what the author shipped" is not true today, and nothing keeps your edits
apart from the author's file.

---

## 2. What this stands on

- **The library and its links** (`core/library.py`). Every mod the Studio
  installs is a real folder in the library and a directory junction in the
  mods folder. `links.json` records every junction the library made; a
  *reference* is a junction to a folder the user owns, managed in place;
  `put_back` turns every link into a real folder again. Both loaders walk a
  junction and log the junction's own path (loadouts proposal, section 6).
- **Play builds** (`core/play.py`, `core/build.py`). Play already writes
  your own mods and patches into the library and links them in. This
  proposal adds one more kind of thing it writes.
- **Every op is kept as written** (ops-as-written proposal), with its source
  text (`Op.source`), the groups it sits in (`Op.within`) and the file it is
  in. That is what an anchor is made from.
- **`Skip`**, which both loaders honour on an op or a group whatever its
  value (`modop.is_skipped`, measured for groups in both games). It is the
  author-facing way to switch an op off, and the generator's only way of
  taking one of theirs out.
- **iModYourAnno**, which already rewrites mods this way: an exposed op gets
  `Skip="1"`, its edited copy goes in a sibling include file, and an
  `<Include>` is appended (playing proposal, section 6). The Studio writes
  those settings itself at Play. A generated copy is that technique made
  general, and the settings become one more kind of edit.
- **The patch** (`core/patching.py`): `ModLayer.patches`, the
  `AnnoModdingStudio` key in the patch's modinfo that marks it, `is_inert`
  for a patch whose mod is off, unloaded or replaced, and `adopt` for edits
  made before the patch existed.
- **A second opinion**: the reader, which reads any mod folder, and the
  loader check (`core/loadercheck.py`) that Play and the Export tab already
  ask about the mods they build.

---

## 3. The idea: the mods folder is a build output

Three things are kept, and each can be looked at:

| | What it is | Written by |
|---|---|---|
| **The library** | Every mod as its author shipped it, and the folders of mods you wrote: your own mods and the patches' own folders | Installs and updates; Play and Export for what you wrote, as now; never an edit to somebody else's mod |
| **The project** | Your patches: edits to the author's ops, and ops of your own | You, in the Studio |
| **The output folder** | Generated copies of other people's mods, and nothing else | Play, from the two above; disposable |

The output folder sits beside the library, under the same root. The mods
folder holds links only: to the library for a mod nothing edits and for
one you wrote, to the output folder for a generated copy, and to the user's
own folder for a reference.

The output folder holds nothing anybody wrote. Everything in it can be
deleted and is rebuilt at the next Play, and nothing in it is somewhere to
edit. A mod you wrote is never there: its folder is a real mod you can
open, edit by hand and take with you if you stop using the Studio, and it
is kept apart from anything temporary so that there is never a question of
which copy is the real one.

A generated copy is the author's mod with your edits spliced into its files,
under the same folder name and with the same modinfo, so the game sees the
same mod — the same ModID, version and place in load order. The game's own
loader then does the patching, searches, conditions and computed values
included, exactly as it would for the mod as shipped. The Studio never
predicts what the game will do with an op; it only changes which ops are
there.

That is the line between this and the alternative it grew out of, a single
mod the Studio computes from the whole stack (section 10).

---

## 4. Patches, redefined

### A patch is defined by what it needs

A patch is *your changes that only make sense while a mod is in effect*.
What it touches does not define it. Take an influence nerf you installed,
and a buff of your own to Schnapps income to make the early game playable
without spending influence. The buff touches nothing of the nerf's, and it
belongs to the nerf: with the nerf switched off the buff has no reason to
be there. The user decides that coupling, and the Studio cannot work it
out. So a patch stays one thing to the user, in one place: its row under
the mod it patches, which is where it is drawn today.

### Two kinds of entry inside it

| | Edits to their ops | Your own ops |
|---|---|---|
| What | A changed value, search, condition or payload; one of their ops switched off; one of yours put at a place in their file | Anything that runs after their mod: the Schnapps buff, an exception to one of their answers, a new pattern |
| Written at Play | Into the generated copy of their mod | Into the patch's own folder, which loads after their mod |
| An update can | Fail to find the op, or find it changed (section 6) | Only drift in meaning |
| Without the Studio | Not on its own (section 9) | Loads like any mod |

The changes list shows the patch as one row with two groups under it, or a
mark on each row: the user sees which kind an entry is, because the two
behave differently on an update and when published, without the patch
splitting into two things.

Your own ops go to a folder of their own rather than being appended to the
generated copy for two reasons. The loader's log keeps attributing them to
your patch, so an error in your buff is not reported against the author's
mod. And that folder is exactly the part of the patch someone without the
Studio can load.

*As built (phase 2).* The changes list marks rather than groups: a mod's
row says how many edits of yours it holds, and its patch's row, drawn straight
after it, says they are there too (`ChangesPanel._edited_words`). The edits
themselves are rows under the mod, where its ops are, which is where they are
changed. Two groups under the patch would have drawn each edited op twice,
under the mod and under the patch, with two checkboxes to keep in step.

### What it needs, and what it loads after

Today `ModLayer.patches` is one ModID doing two jobs: which mod must be in
effect, and where the patch sits in load order. Split them:

| | Needs | Loads after |
|---|---|---|
| A patch today | its mod | its mod |
| A patch of two mods | both | both |
| A loadout's own mod (the mod list patch) | nothing | everything |

A patch is inert when any mod it needs is off, unloaded or replaced
(`is_inert`, over a list). The mod list patch is the general case with an
empty list, and never goes inert. The modinfo's `AnnoModdingStudio` block
holds a list where it holds one ModID now; the loader's own `LoadAfterIds`
(1800) and `Dependencies.LoadAfter` (117) already take lists. Published, a
patch declares what it needs as `ModDependencies` (1800) or `Require`
(117), which the loader answers with a line in its log when one is missing.

A patch of several mods is drawn after the last of them in load order, and
each of the others' rows says it is patched there too ("also patched by…"),
so everything coupled to a mod shows under that mod.

*As built (phase 4).* The patch keeps `patches`, the mod it was made for,
and adds `needs`, the rest; `ModProject.needed` is both, the first first.
Its row stays under the mod it was made for, where a patch's row has always
been drawn: placed under the last of them, it would sit under a mod it was
not made for, and rows a patch of one mod draws under its mod would move.
The other mods' rows say "also patched by …" instead. Each published patch
now declares what it needs as dependencies too -- the mod it was made for
as well, which a patch of one mod did not before.

An entry moves between your own mod and a patch, and between patches, by
hand. Coupling is a judgement and it changes: decide the Schnapps buff is
right whatever else is loaded and it belongs in your own mod.

### What goes away

*As built (phase 2),* not all of it went. The question is gone for a mod in
the library, whose edits now survive an update; for somebody else's mod in a
folder of your own it is still asked, since Play still writes that folder.
The first change to a mod makes no patch: the edits are the mod's, and its
row counts them, so a patch would be an empty folder until you want an op of
your own after their mod. And a bundled mod is still changed through a patch:
the edits would go into the generated copy of the host they were read from,
and when another host's copy of the same mod is the one the game loads, they
would not run -- generating the copy that wins the loader's dedupe is the
fix, and is not built.

- **Editing somebody else's mod in place.** With the library copy never
  written, every change to somebody else's mod is an entry in a patch. The
  question *Patch it or change the mod?* (`offer_a_patch_for`, and the
  declined set behind it) was there because a patch could not do
  everything an edit in place could. Now it can, so the first change to a
  mod makes its patch, as a bundled mod's does today.
- **The bundled special case.** `edits_need_a_patch` exists because an edit
  written into a bundled copy is lost when its host updates or another copy
  wins the loader's dedupe. An edit in a patch is anchored to the mod by
  ModID and applied to whichever copy wins, by generating that copy's host
  (section 5 keeps the host cheap to generate).
- **The *Edit pattern* hole.** Editing one of their patterns records an
  edit in the patch. It is no longer a way to write into their file, because
  nothing is.
- **`adopt`'s re-reading.** Moving edits into a patch no longer means
  reading the mod's folder again to give the mod its own values back: the
  library copy was never changed, so edits to their ops simply change owner.

### Edits that need several mods

An entry could in principle need its own list — this change only while
mods A and B are both on — and generation could drop the entries whose
needs are not met at Play. It is left out: it cannot survive publishing to
anyone without the Studio, and the per-patch version matches how people
describe the coupling. Clusters — "these mods go together, switch them as
one" — answer a different question, and belong on loadouts and collections
(section 10).

---

## 5. The generator

### Two operations

Everything an edit does is made of two things done to the author's text:

1. **Add `Skip="1"` to one of their ops or groups.** This switches it off
   in the loader's own terms. The op stays in place, as written, visible.
2. **Insert text after one of their ops or groups**, or at the top or end
   of one of their files.

A changed op is both: theirs switched off, yours inserted straight after
it, in the same group and the same file, so it runs where theirs ran. A
switched-off op is the first alone. An op of yours placed in their file is
the second alone. Switching on one of theirs that shipped with `Skip` is a
copy without it, inserted after. A group's condition changed is the group
switched off and a copy inserted with the new condition.

That is the whole generator, and it is short enough to explain in a
paragraph (section 8). iModYourAnno's settings are the same two operations,
and move here from the library copies.

### Splice the text, never write it out again

The generator copies the author's bytes and changes only the spans it
splices. It never parses a file and serialises it back, which would change
quoting and whitespace everywhere and make any comparison with the shipped
file noise. Given the same inputs it writes byte-identical output, so "why
is this file like this?" always has the same answer.

### A comment at each splice

Every inserted block opens with a comment naming the edit and the patch
that put it there:

```xml
<!-- Anno Modding Studio: edit 3f2a91 of "Influence Nerf (my patch)".
     The op above is switched off by it; this is the op in its place. -->
```

The game ignores comments, and someone reading the generated file in the
mods folder sees why it differs. Text that goes into a comment is made safe
for one (`generator._comment_safe` does this already).

### The folder

A link points at one whole folder, and the game reads the mod through it,
so a generated copy has to contain everything the mod ships, including its
models and textures. Nothing has to be copied to do that:

- **A subfolder with no changed files in it** is a junction into the
  library copy. That is usually `data/graphics` and most of a mod.
- **The folders on the way down to a changed file** are real folders.
- **A text file beside a changed one** is copied: it is small, and a tool
  that writes into a mod folder in place (iModYourAnno) must not write
  through into the library.
- **A binary file beside a changed one** is a hard link, which costs
  nothing when the output folder and the library are on one volume, as
  they are by default. A copy where they are not.

A generated copy is then a few kilobytes of XML and some links, and
building one is quick in time as well as in space. Generated copies are kept
between Plays by the hash of what they were built from — the library copy's
hash and the patch's edits — so switching projects back and forth rebuilds
nothing.

*As built (phase 1).* Every folder of a generated copy is a real folder:
each text file of the author's is copied, and every other file is a hard
link to the library's, or a copy where a link cannot be made. No junction is
made inside the copy, so question 1 of section 12 never has to be answered:
a hard link is an ordinary file to anything that reads it. What it costs is
a copy of the mod's text, which in the census is most of a mod's bytes, and
small beside the models and textures real mods ship. The `generated` folder
sits beside each game's folder in the library, under the same root, so the
two are on one volume and a hard link can always be made. Whether a copy is
current is decided by a fingerprint of the author's files (name, size, time)
and of what is written over them, kept in its manifest.

### The manifest

Each folder the generator writes holds a small file, `generated.json`,
saying what it is:

- the tool that wrote it, and the version of the convention;
- the source mod: ModID, version, folder in the library, and the hash of
  each file the generator read;
- each edit applied: its id, the patch it is in, its anchor (section 6),
  and the file and line range it was spliced at.

That is enough for anybody to build the same folder by hand, or for another
tool to see what it is looking at (section 7).

### Checked every time

*As built (phase 1),* the check is of bytes: every file of the author's is
in the copy and is the library's file or the same bytes, every file the
Studio wrote is what it wrote, and nothing else is there but the manifest
(`generated.check`). A copy that fails it is not linked in, and Play says
which mod. That is all phase 1 needs, since nothing it writes is an edit to
an op. Phase 2's edits will want the reading below as well.

After generating, the Studio reads the generated copy with its own reader
and compares that with what the changes view says it should be: their ops,
with the patch's edits in place. Where the two differ, that mod is not made
live, and Play's question says which edit did not land and why. The loader
check Play already runs on the mods it builds runs on generated copies as
well. The promise that the game reads what the Studio shows then rests on a
check made each time, not on the generator having no bugs.

---

## 6. Anchors, hashes and updates

*As built (phase 2).* The edits are not records with anchors of their own.
The project already holds somebody else's mod as its files have it with your
edits in it -- an op taken out is remembered by its text
(`ModProject.taken_out`), one typed over or added is an op of yours with its
place, and a mod read again keeps both (`ModProject.as_before`) -- so what an
edit is anchored to is the op's own text, as it always was. Play compares two
writings of the mod, the project's and a fresh reading of the library's copy
with nothing of yours in it, and carries what differs onto the author's file
through where each op of the fresh writing stands in it
(`splicing.edit_hunks`): an op the writing writes as the author did is
skipped or put after as itself, and one in a run it writes another way -- an
`<Include>` written out in full -- takes the run with it. What follows is
what phase 3 adds on top of that: the op's text kept with the edit for the
three-way view, and what to do when an update changes it.

*As built (phase 3).* No hash: the op's own text is the anchor, as phase 2
left it, and its place in the file -- now kept with each op taken out
(`ModProject.taken_out`) -- ties to it what of yours stands in its place.
Before a new reading lands, every op of theirs an edit is made to is listed
with that (`held.stock`); after, each is looked for (`held.settle`). The
table below is what it does, with "changed" meaning an op of the new version
with the same `ModOpID`, or with none the same type aimed at the same GUID
and path, that says something else. A held edit is in
`ModProject.held_edits`, saved with the project and part of undo, until
**Use mine on the new version** (`held.apply_mine`) or **Keep theirs, drop
mine** (`held.keep_theirs`). The read of a mods folder used not to carry an
op's switch across at all, so an op of theirs switched off came back on at
every update; it does now, as the background reading always did.

### Two levels

- **The mod is the cheap gate.** If the library copy is the version the
  edits were recorded against, by mod.io file id or by the hash of its
  files, there is nothing to look for. `installed.json` already records the
  file id, version and zip hash of each copy.
- **The op is the anchor.** Each edit records the hash of the op's
  normalised text — whitespace, comments and attribute order not counted —
  the op's `ModOpID` where it has one, the file it is in, and its place
  among identical ops in that file, since the same op can appear twice.
- **A second hash covers what the op sits in**: the attributes of each
  group around it, and the condition on the include that brings in its
  file. The op's text can stay the same while the condition around it
  changes.

### The edit carries the op it was made against

Each edit stores the full text of the author's op as it was when the edit
was made, not only its hash. It is small, it makes every edit readable on
its own, and it is what the comparison below needs even if the old version
of the mod is gone.

### What an update can do to an edit

| Found in the new version | What happens |
|---|---|
| The op, unchanged, where it was | Applied; nothing said |
| The op, unchanged, somewhere else | Applied where it now is; nothing said |
| The op, unchanged, with the groups or include around it changed | Applied, and said once |
| An op with the same `ModOpID`, or at the same place with the same target, but different | Held until you look: their old op, their new op and your edit, side by side |
| Nothing like it | Held until you look: their old op, your edit, and where it stood |

An edit held is not applied, and the mod is generated without it. It is
never dropped: it stays in the patch, marked, until you accept, rewrite or
remove it.

### When this is asked

At the update, not at Play. Updates come through the Studio — *Download
and update*, a collection, an install over an older copy — so the Studio
re-applies a patch's edits as the new version lands, and asks about any it
holds then. Play never surprises anyone with a half-applied mod.

### The previous version is kept

An install with `replace` replaces the library copy in place today. Keep
the outgoing version's modinfo and XML files, which are small, under the
library, so the Studio can show what an update changed across the whole
mod, not only at your edits. Keeping whole previous versions, for going
back to one, costs the space of the large mods and is left to a setting.

### Same op, different matches

An op whose text did not change can still land somewhere else: an earlier
op of the author's changed, or the game updated. A hash cannot see that.
The Studio's reading can, as it already says when a pattern's matches have
moved (`keptops.drifted`). Here it is advice — said, never a reason to hold
the edit — so a reading that is wrong costs a notice and nothing else.

---

## 7. Links as a convention

### Anything in the mods folder that is not a link is not managed

Everything the Studio puts in a mods folder is a link, whether to a mod as
shipped or to a generated one. So the test for "something else put this
here" is simply: a real folder. The Studio warns, and works around it, as
it does now: reading a folder never moves it into the library, adoption is
offered afterwards, and an edit to an unmanaged mod waits until it is
adopted.

### Where it points says what it is

- **Into the library**: the mod as shipped, or a mod you wrote.
- **Into the output folder**: generated, and temporary; the folder's
  `generated.json` says from what.
- **Anywhere else**: a folder somebody manages in place, such as a mod
  author's checkout.

### Not only the Studio's

Nothing in this is the Studio's alone. A link means *this is not the
source; look at what it points at*, and the manifest says what that is. Any
other tool that wants to manage mods the same way can read and write the
same things, and the README section that describes them (section 8) is the
specification for it. `links.json` stays as the Studio's own record of the
links it made, so that it can tell its links from a mod author's
hand-made one.

### Linux and Proton

The Studio runs on Windows only today: junctions, launching and secrets all
check for it. Should it run on Linux, or under Wine beside a Proton game,
these are the differences that matter:

- **A link there is a symlink.** The convention is worded as "a link
  (junction or symlink)", not as NTFS junctions.
- **Proton's container sees only some of the disk.** A Proton game runs
  inside Steam's runtime container, which sees the home folder and Steam's
  library folders. A symlink to a library elsewhere can be dangling from
  the game's side while looking fine from outside. The library has to be
  somewhere the container sees, or added to Steam's extra mounts, and the
  Studio has to check that and say so.
- **Symlinks can point at single files and be relative**, without the
  rights Windows asks for file symlinks. The folder scheme in section 5 gets
  simpler.
- **Case.** A Linux file system tells `Data` from `data`; Wine pretends it
  does not. A generated copy keeps the author's casing exactly.
- **Across the Wine boundary it is not known** whether a Unix symlink looks
  to a Windows tool like a junction, or what a junction made by Windows code
  under Wine becomes on disk. Tested before anything is promised.

*As built (phase 6).* The convention's test is `library.is_link`: a junction
or a symbolic link to a folder, whoever made it, so a symbolic link another
tool puts in a mods folder on Windows is a link too. `make_link` makes a
junction on Windows and a symbolic link elsewhere, by the target's whole
path rather than a relative one, which a Proton container resolves the same
way as the Studio does. `target_of` reads a relative one from where it
stands. Each file of a mod is read and written by the mod's own spelling of
its path (`disk.as_spelled`), so a generated copy has the author's names and
no file beside them that differs only in case. `proton.unseen` is what Play
says when the library is outside what the container is known to share: the
home folder, Steam's own folders, each Steam library, and the folders the
game's launch options add in `STEAM_COMPAT_MOUNTS` or
`PRESSURE_VESSEL_FILESYSTEMS_RW`, read from each Steam user's
`localconfig.vdf`. What else a container may share, `/mnt` or `/media` say,
is not assumed, so a library there is said to be unseen unless one of those
names it.

---

## 8. Literal means literal

The design standards say anything that writes outside the project shows
where before it writes, and that labels use the real terms. Generation keeps
both. What is new is that the Studio shows your edits beside the author's
op while the game reads a file the Studio wrote. That is acceptable only if
the generated file is a third thing you can open, and never something
hidden behind the view.

The three layers the standards prescribe:

1. **The surface.** A mod that is generated says so wherever mods are
   listed. Play's question lists which mods are linked as shipped and which
   are generated, with how many edits each, before anything is written.
2. **One step away.** A `?` beside that mark: your edits to this mod's
   ModOps are written into a copy of it, kept beside the library, and built
   again from the mod as shipped every time.
3. **The full story.** A README section: the two operations, the folder
   scheme, the manifest's fields and the link convention. The same section
   is the specification another tool would implement.

The surface that is not literal is narrow. Your own ops go to the game
exactly as you wrote them, in the patch's own folder. Only edits to
somebody else's ops are applied somewhere other than where they are shown,
and that is the case where literal behaviour is what loses your work today.

Three things make the generated file easy to follow from the outside: the
comment at each splice (section 5); the author's ops staying where they were
with one attribute added, so a diff with the shipped file is exactly as big
as the edits; and line numbers. The loader's log reports errors against the
generated file, and with splices only, lines move just after each one, so
the Studio can map a logged line back to the author's file or to the edit.

---

## 9. The Export tab's preview

### What it shows today

The preview is one mod, chosen in the box above it: its ModOps file, and
after it each file the patch's `<Include>` lines name (`OutputPanel.refresh`,
`build.as_files`). It is what Export writes for that mod, and the copy and
save buttons write the same text. A patch is one entry in the box and one
self-contained file.

### What a patch is now

A patch's own ops still make one self-contained file in its own folder. Its
edits to the author's ops do not: they are splices in one or more
generated copies, each in somebody else's file.

### Proposed

The preview stays *what is written for the mod in the box*, and for a patch
it comes in two parts:

1. **The patch's own folder**: its ModOps file and included files, exactly
   as now. This is literal, and it is what both Play and an export write.
2. **One block for each mod it edits**, headed *Edits to Influence Nerf —
   written into its generated copy at Play*. Each edit is shown as the
   splice: the author's op as shipped, with the line it is on in their file
   and the `Skip` the edit adds, then the text inserted after it, comment
   included, with a few lines of their file around it for context. Not the
   whole of their file, which can be tens of thousands of lines.

Validation follows the same split: the patch's own changes are verified
against the base file as now, and each edit is checked against the anchor
in the library copy — found, moved, or held (section 6).

A generated copy is not an entry in the box. It is not something you
export; it is Play's output. It is opened from its mod's row, read-only, as
the folder it is, or in the Mod file tab if that is given a view of it
(section 14, question 3).

### Exporting a patch to share

A zip, a folder elsewhere, an update on mod.io: what goes out is the
patch's own folder. Its edits to the author's ops cannot travel in it,
because they are changes to a file the recipient has as shipped. Until
there is more (phase 5), the block for each mod says so in its heading:
*not in an export of this patch — applied by the Studio at Play*. Later,
an export turns each edit it can into ops of the patch's own, after the
mod — the first two rows of the table in section 1 — and the block shows
those ops in place of the splice, with the edits it could not turn listed
under it as left out.

The same holds for *Copy XML* and *Save assets.xml…*: they write the
patch's own ModOps.

*As built (phase 5).* The preview of a patch is what an export writes: its
own folder, with each edit that can be turned at the top of it as an op of
its own, under a note saying which op of theirs it stands in for. The block
for the mod says how many were turned, and lists each edit left out at its
line in their file, with why, their op and yours. A turned edit's splice is
not shown, and neither is the patch as Play writes it, without them. The
rules, and what each edit left out is left out for, are in `core/sharing.py`.
The tab's check with the game's loader is of the same patch, laid on their
mod as its author shipped it, with the Studio's reading of that mod
underneath it (`loadercheck.prepare`, `view`, `folders` and `shipped`): what
the two must come to is the Studio's reading of their mod with your edits
in it.

---

## 10. Choices weighed and not taken

- **One mod computed by the Studio.** Apply every ticked mod with the
  Studio's own patcher, compare the result with the base file, and write a
  single mod of narrow ops that gives the same result. It needs no links,
  and any syntax the Studio liked could be compiled away. It is set aside
  because the Studio's reading becomes the game's: in the first run of the
  ops-as-written measure, the Studio's reading matched the loader's in 643
  of 812 Anno 1800 units and 297 of 337 for Anno 117, and every mismatch
  would be a mod behaving differently in game. It also goes stale when the
  game is started without the Studio after a game patch; it makes every
  mod one mod to the loader's log; and mods are more than their
  `assets.xml`. Generation keeps what made the idea attractive — the
  folder is an output, so syntax of the Studio's can be turned into the
  loader's — and keeps the loader doing the work.
- **A patch split into two things for the user.** Technically clean, and
  wrong for the coupling in section 4: the nerf and the buff belong
  together whatever kind of change each one is.
- **Clusters in place of patches of several mods.** A cluster switches its
  mods on and off together, both ways. The coupling a patch expresses runs
  one way: the buff needs the nerf, and the nerf does not need the buff. A
  cluster is a real need of a different kind — a difficulty pack, a set to
  publish as a mod.io collection — and can be added on loadouts and
  collections later without touching patches.
- **Appending an include, as iModYourAnno does.** Its edited ops go at the
  end of the file, after everything else of the author's, which changes
  when they run. Inserting straight after the switched-off op keeps the
  order.
- **Editing the author's op where it stands.** Fewer lines, but the
  author's op is then gone from the generated file and the diff is a
  rewrite rather than an addition. Switching it off and inserting after it
  keeps both in view.
- **Asking the loader to skip another mod's op by `ModOpID`.** Not known
  to exist in either game, and with generation not needed.

---

## 11. A frozen build

Little changes. Junctions are made through `_winapi`, which is part of
Python and goes into a frozen build with it; hard links are `os.link`. The
output folder is under the Studio's own folder, as the library is, not
beside the executable. The README section of section 8 ships with the
Studio as the rest of the README does.

---

## 12. What was checked, and what is not known

Checked, in the code:

- The *Edit pattern* route and the places it is opened from (section 1).
- That Play builds a mod you edited in place into the library under its own
  folder name (`build.mine`, `build.write`, `generator.folder_for`), and
  writes iModYourAnno settings into library copies (`core/play.py`).
- That an install with `replace` replaces the library copy in place
  (`library.install`).
- That both loaders honour `Skip` on an op or a group whatever its value
  (`modop.is_skipped`, which cites the loader's source and the measurement
  for groups).
- That a missing dependency is a line in the loader's log, not a refusal
  (`core/dependencies.py`).
- That the Studio runs on Windows only (`library.is_junction`,
  `core/launch.py`, `core/secrets.py`).

Measured for phase 2 (16 October 2026), on the bench's 416 Anno 1800 mods,
each read afresh from its folder against the game's files: unedited, not one
mod's generated copy held anything. With one op of each typed over, 401 of
402 copies held exactly that op switched off and the new one after it; with
one op taken out, 399 of 400 held exactly that op switched off. The other
one each is *High Life Souvenirs*, whose op is in a file it includes with a
`ModOpID`, which the Studio writes out in full: the include is switched off
and the file's ops, with the edit, written after it, as designed. Fourteen
mods have no op to edit.

Measured for phase 5 (16 October 2026), on the same 416 mods. Each op with a
value in it that is not a GUID had its first such value changed, as somebody
tuning it would, and the export's rules were asked what becomes of it. Of
8,629 such ops found in their files, 3,873 are turned, 1,319 of them values
in list entries found by what they hold. Of the rest, 2,663 are in ops or
groups a tweak tool switches on and off, 1,279 are under a condition that
reads the game's files, 443 are changed again by a later op of theirs, 225
are in list entries nothing tells from the next, 69 are in ops whose search
tests what the op sets, 40 are values a tweak tool sets, and 37 are merges
that set less than theirs did. Before the later-op test compared the paths
each op touches, it compared the names of the fields each names, and held
back 1,474. Then through the game's loader (xmltest 2.3.1): for 84
turned edits, a third of them in list entries, the mod's generated copy with
the edit spliced in and the mod as shipped with the exported patch after it
left every asset the same in 84. Once the later-op test compared paths, 85
of 85 more were the same, 60 of them edits the field-name test had held back.
Earlier runs found four faults, each fixed
before the last: a turned op undone by a later op of theirs, an op in a group
they ship switched off, text an add appends to a value, which was dropped,
and a value held in an `<Item>`, which the loader does not merge into. The
Export tab's own check, run the same way on 12 exported patches, agreed on all
12, and compared the asset the patch changes in 11; in the twelfth the
Studio's and the loader's readings of the mods underneath already part there,
which the check counts apart, as it does for any mod.

Not known, and each answered by a test before the phase that needs it:

1. **Whether both loaders follow a junction inside a junctioned folder.**
   They follow the outer one. The folder scheme in section 5 needs the
   inner one. If either does not, the fallback is hard links throughout,
   which needs one volume. *Not needed:* phase 1 makes hard links
   throughout, with the library and its generated copies on one volume by
   construction (section 5, *The folder*).
2. **Whether a generated copy with no edits loads exactly as the mod
   does**, across the census. It should, being the same bytes; the census
   says so for every unit, with the loader, before any edit is generated.
   *Answered on 16 October 2026,* by asking the question the loader would
   be answering: whether the copy holds the same files with the same bytes.
   `tools/generated_check.py` builds a copy of every mod in the census with
   nothing written and compares it with the mod, file by file, apart from
   the generator's own check. Anno 117: 396 mods in 364 units, 19,855
   files (11,819 copied, 8,036 linked), every copy the mod exactly, in
   129 s. Anno 1800: 919 mods in 854 units, 112,341 files (76,046 copied, 36,295 linked), every copy the mod exactly, in 858 s. It does the same for every mod that offers
   iModYourAnno knobs, with each knob moved off its default where it has
   another value: the copy must read back as set to exactly that, and every
   file it did not write must be the author's. Of the 82 Anno 1800 mods that offer knobs, 75 have one with another value to take, and 2,022 knobs were moved. 72 read back exactly as set. In three, five toggles set off read back at their defaults: one in *PC Time as Ingame Time*, three in *Subway Network*, one in *Powerplants*. Written straight into a copy of the mod, as Play wrote settings before phase 1, the same five read back the same way, so that is the Studio's port of iModYourAnno reading those toggles, not the generator: every file of those copies is what was written.
3. **What saves and multiplayer check of a mod.** A generated copy keeps
   the mod's ModID and version, so a save that records mods by ID should
   not notice. Whether either game compares the files themselves — in
   multiplayer especially — is not known.
4. **Whether the Anno 117 loader's log gives line numbers**, which the
   mapping in section 8 relies on for that game.
5. **The Wine questions** in section 7, if the Studio is ever run there.
6. **Whether the op switch and *Write as narrow ModOps* on somebody else's
   op write into their mod** as *Edit pattern* does. Answered on 16 October
   2026. *Write as narrow ModOps* did: it turns the op into changes of the
   mod's own, and Play writes the mod back with them. Phase 0 guards it with
   *Edit pattern*. The op switch is a different fault. Play builds only the
   mods with changes of yours in them (`build.mine`), so switching off one of
   somebody else's ops does not reach the game at all, unless that mod also
   has an edit of yours, when the mod is written back with the op as a
   comment. Phase 2 is the answer to both halves: a switched-off op is an
   edit, `Skip` in the generated copy.

---

## 13. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 0 | *Edit pattern* on somebody else's op, while edits are still made in place: opened only for your own mods and patches, the mod changes go to, or a mod you choose to change directly when asked; otherwise it writes it in the patch as a pattern of the patch's own. *Write narrow* greyed for the same mods. **Built** | nothing |
| 1 | The output folder, and the generator with no edits: links, copies and hard links as in section 5, `generated.json`, the check after generating. iModYourAnno settings written into generated copies, so library copies stay as shipped. Measured on the census: every unit loads the same through a generated copy. **Built** | questions 1 and 2 of section 12 |
| 2 | Edits to the author's ops: the edit with its anchor and the op's text, recorded in the patch from *Edit pattern*, the XML view, the op switch and typing over an op; the two groups in the patch's row; the first change to a mod making its patch; the preview's blocks. The bundled special case and the patch-or-change question go. **When this lands, remind the user to make a new project to test with as their default** (section 14, question 4). **Built**, with the differences the status line names | 1 |
| 3 | Updates: the edits re-applied as a new version lands, the table in section 6, the three-way view, the previous version's XML kept. **Built**, with the differences the status line names | 2 |
| 4 | Needs and loads-after as lists: patches of several mods, drawn after the last, named under each. **Built**, with the difference the status line names | 2 |
| 5 | A patch exported to share: edits turned into ops of the patch's own where they can be, the rest listed as left out. **Built**, with the differences the status line names | 2 |
| 6 | Linux and Proton, if the Studio goes there. **Built** as far as Windows can test it: links as junctions or symbolic links, a mod's files by its own spelling, and Play's check that a Proton game can see the library. The rest of running on Linux is not | 1; the Wine questions |

Phase 0 is small and stands on its own: it closes the hole while the rest
is built. Phase 1 changes nothing a user sees except that library copies
stop being rewritten, and it is where the census proves the generator.
Phase 2 is the substance. Phases 3 to 5 each stand on 2 and on nothing
else.

---

## 14. Questions settled, and open

Settled on 15 October 2026:

1. **Your own mods and patches' folders stay out of the output folder.**
   A mod author who starts something in the Studio and then leaves it must
   never have to wonder where the hand-editable folder of their mod is, as
   against a project file they cannot read by eye. So the output folder
   holds only generated copies, which are temporary and rebuilt at will;
   what you wrote stays a real folder, where it is now (section 3). The
   library is therefore "as shipped, or yours", not "as shipped" alone.
   Related, and not settled here: Play writes your own mod's folder in the
   library again at every Play, and as far as the code shows nothing
   notices a hand edit made there in between, so one would be written over
   without a word. That bears on the same promise and wants a look of its
   own.
2. **Edits belong to the project.** Profiles stay a light convenience: sets
   of switches over one mod list, with nothing of their own to edit.
3. **The Mod file tab for somebody else's mod** — their file with the
   patch's edits in place, and typing over one of their ops recording an
   edit — is wanted, and is a proposal of its own.
4. **Projects saved before this need nothing.** Edits made in place to
   somebody else's mod are not moved into patches: the Studio is not
   released, and old projects are not a constraint. When phase 2 lands,
   the user is to be reminded to make a new project to test with as their
   default, since their present one holds edits made in place.

Open:

5. **The words.** *Generated copy*, *edit* and *output folder* need entries
   in the glossary (design standards, section 14), and one of them may
   already mean something else there. *Generated copy* is in it since
   phase 1 (German *die generierte Kopie*), and the output folder is called
   `generated` on disk and in the README. *Edit* waits for phase 2, which
   is the first to show one.
6. **How many previous versions to keep** beyond the last one's XML.
