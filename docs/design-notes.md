# Design notes

Why the Studio works the way it does, and how it came to. The
[user guide](guide.md) says what to click and what you will see. This
is what used to sit beside that in it, when the guide was the README: the reasons for each choice, the alternatives
turned down, what was measured against the mod.io census, and what the Studio did
before it did what it does now. It was moved here, not dropped (decision 7 of
[the consolidation proposal](proposals/consolidation-proposal.md)). Until October 2026
what the Studio opens was called a project; it is a loadout now, by the projects and
loadouts proposal, and these notes keep the word they were written with.

The sections are by subject. They no longer follow the guide's order: the guide
goes from general to specific for somebody using the Studio, and this goes feature by
feature. Each guide section links to the sections here that explain it:

| In the guide | Here |
|---|---|
| [Getting started](guide.md#getting-started) | [Getting assets.xml](#getting-assetsxml), [Three ways to use it](#three-ways-to-use-it) |
| [Playing a set of mods](guide.md#playing-a-set-of-mods) | [Loading every mod you have](#loading-every-mod-you-have), [mod.io](#modio) |
| [Changing a mod you downloaded](guide.md#changing-a-mod-you-downloaded) | [Importing someone else's mod](#importing-someone-elses-mod), [Your own mod is one of them](#your-own-mod-is-one-of-them) |
| [Writing your own mod](guide.md#writing-your-own-mod) | [The steps](#the-steps), [Editing beyond single values](#editing-beyond-single-values), [Grouping and switching changes off](#grouping-and-switching-changes-off), [Projects and history](#projects-and-history) |
| [How the Studio reads and writes mods](guide.md#how-the-studio-reads-and-writes-mods) | [Supported games](#supported-games), [How display names resolve](#how-display-names-resolve), [Why one ModOp per change](#why-one-modop-per-change), [Importing someone else's mod](#importing-someone-elses-mod) |

For how the code is put together, see [architecture.md](architecture.md). For running
the tests, see [development.md](development.md).

---

## Running it

### Appearance

They used to only look it in the
source: a stylesheet that names a font family overrides `setFont` outright, so every
`QFont("Consolas")` in the app drew in Segoe UI. The monospaced face is now something
the stylesheet itself applies, to anything marked for it.

### Panels in windows of their own

Everything the Studio shows was inside one window, so a second monitor was no help:
stretched over two screens, the splitter puts its divider wherever the proportions
land. The Mod changes panel can be moved to a window of its own
([the proposal](proposals/detached-windows-proposal.md), phase 1), and so can the
References tab (phase 2), the Mod file tab (phase 3) and the Asset editor
(phase 4).

- **It is the one panel, moved.** It is taken out of the main window's layout into a
  plain window and put back when that window is closed. A second copy would be a
  second selection and a second list to redraw. A `QDockWidget` is what the panel
  used to be, and a dock keeps the width it was given when the window is resized.
- **The window belongs to the main window.** That keeps it above the main window,
  minimises it with it and keeps it out of the taskbar. On one monitor it covers part
  of the main window, and the answer there is to put it back.
- **It answers the main window's shortcuts by holding the same actions.** Otherwise
  Ctrl+Z after unticking a change over there does nothing. Both ways the proposal
  left open were tried (12 October 2026). A tool window is handed its parent's
  shortcuts by Qt, and its title bar can be given a maximise button, but maximised
  it covered the taskbar: 2560 by 1448 on a screen with 1400 to give. An ordinary
  window with the actions added answered every key, each once, and a text box there
  still kept Ctrl+Z for its own text.
- **The button is in the panel's own top row,** beside the filter, and not beside
  the "Mod changes" title as the proposal had it. The title stays in the main
  window, so a button there could not be the way back. In the panel it goes where
  the panel goes, and a tab's panel will carry its own the same way.
- **Out comes before folded.** Out, the panel is shown whole and F9 is greyed.
  Taking it out opens it, so it comes back open: a strip would look nothing like the
  window just closed.
- **A window on a screen that has gone is not opened.** Opened on whatever screen is
  left it would land on top of the main window, unasked. The panel stays in the main
  window and is still remembered as out, and where, so that a laptop back on its
  dock has it out again. Moving it by hand ends that.
- **Two rules stand in for "which tab is in front".** A panel is *showing* when it
  is the tab in front or is out (`MainWindow.showing`), and `open_tab` brings a
  panel forward wherever it is, with the keyboard. From a panel's window, going to a
  tab brings the main window forward, since a key pressed there would otherwise act
  on a window behind it.
- **A tab whose panel is out stays in the strip,** with a page that says where the
  panel is and a button to move it back. The tabs are known by their place, and a
  tab that vanished would take the obvious way back with it. The page is swapped in
  with the tabs told nothing, so which tab is in front, which are open and what
  each says do not move.
- **A shut tab cannot be taken out, and one that is out can always be moved back.**
  At launch a panel that was out is put out whatever the gate, since the gate is
  shut at every launch until a project is open.

#### The editor in a window of its own

Taking out the file or the graph already puts either beside the editor. Taking out
the editor adds the choice of which window keeps the menus, the status bar and Mod
changes.

- **Almost nothing was written for it.** Everything that ends in the editor goes
  through `open_tab`, and Undo asks `showing`, so a search, a double-click on a
  change and *Edit this asset* bring its window forward, and Undo shows the asset of
  the step undone whichever tab the main window is on. Nobody is pulled away by
  that: the editor is beside whatever they are looking at.
- **The Edit menu is filled from the editor wherever it is.** It asks the editor's
  tree for its selected row each time it opens, and the tree keeps its row when the
  keyboard goes to the main window's menu bar.
- **The graph's rules go by whether the two are in different windows,** and not by
  the graph being out. With the editor out and the graph in its tab, the graph
  follows while its tab is in front and on arrival otherwise, the pin is offered and
  the graph has its own history, as with the graph out. With both out, moving one
  back leaves them apart.
- **Nothing is kept for an editor that is out.** What the graph moved to is kept
  for the editor's tab to open when it is next come to. *References…* for another
  asset moves the graph before its tab is in front, so with the editor out that was
  kept, with no tab to come to. It would have waited until the editor was moved
  back and its tab come to, and moved it then.
- **Taken out, the editor catches up with the graph,** as its tab does when it is
  come to, and as the graph does when it is taken out. Moved back, nothing moves,
  and the graph is still owed the assets opened while its tab was behind another.

#### The mod's file beside the editor

- **Out, the file is drawn again at every change.** Nothing was changed to make it
  so: the tab already draws its file now unless it is hidden, and on being shown
  otherwise (`FilePanel.mark_stale`), and a panel in a window of its own is never
  hidden.
- **No wait before it is drawn.** The proposal asked what that costs on each edit
  made beside it. On the bench project (12 October 2026, 417 mods and 32,359
  changes, medians of three), drawing the longest mod's file takes 193 ms. An edit
  holds the window for 3,882 ms with the file not showing, 3,698 ms with the file
  changes go to showing, and 3,578 ms with the longest mod's file showing. The
  drawing is under the run-to-run difference of the edit itself, so the file is
  drawn at each edit and not a moment after the last.
- **An Apply in the file redraws the asset the editor has open.** It did not. The
  editor went on showing the asset as it was, and still did when its tab was come
  back to. Nobody saw the two together, so it had not shown. Side by side it is the
  old value beside the text that sets the new one.
- **Ctrl+F finds where the keyboard is,** of the file and the editor. With it in
  neither, as in the Mod changes panel, it goes by the main window's tab, as before.
- **Text typed in both at once is kept in both.** Each already keeps what was typed
  when the mod moves under it, and says so beside Apply (`FilePanel.refresh`,
  `AssetXmlView._hand_over`). Side by side that is seen more, and is now tested in
  both directions.

The bench could not read its folder, and had not been able to since the mods folder
dialog began to read on **Read mods** and not as it opens: the dialog was accepted
with nothing read, and every step after was timed on an empty project. It presses
the button now. Its two Play steps still answer in under a millisecond, with a
"Finishing background work" dialog refused before each, and are not to be believed.

#### The graph beside the editor

In one window only one of the two shows, and each catches up with the other when its
tab is next come to. That is on purpose: building the editor's tree for every asset
clicked through in the graph would make each click wait. With the graph in a window
of its own both show, and "when it is next come to" says nothing.

- **The graph follows the editor as it moves.** Drawing the graph is the cheap half.
- **The editor does not follow the graph.** It moves on the acts that already mean
  "edit this": a double-click, Ctrl+Enter, *Edit this asset*. With the editor's tab
  not in front it still catches up on arrival, as before.
- **A pin holds the graph.** *Pin to this asset* is a checkbox with the graph's
  others. It is offered while the graph is in another window than the editor's
  (whichever of them is out), and
  not, as the proposal had it, while both are showing: that would take the pin away,
  and let the graph go, each time another tab was looked at. Moved back, the graph
  is the editor's other view again and the pin goes. It is not remembered between
  launches: a graph that does not follow, on the first asset of a session, reads as
  broken.
- **Moving the graph back moves neither of them.** It went to the editor's asset at
  first, on the grounds that the two are one view again. Pinned on one asset with a
  neighbour open in the editor, the graph then jumped as its window was put back,
  and the window was the only thing that had been touched. It stays where it was,
  and each follows the other from the next asset either is moved to.
- **The graph steps through a history of its own while it is out.** The two share
  one history so that back in either takes both back, which fits two views of which
  one shows. Side by side, a step back in the graph would move a history the editor
  is standing on to an asset it does not have open. The graph's own history starts
  from the asset it is on, and it takes up the shared one again when it is moved
  back. This is what the proposal suggested and left open (its question 2).

### Where the Studio keeps what it makes

Two places, by what is kept. What you make (projects, exports, backups) is under
`Documents\AnnoModdingStudio`, because that is the folder a backup tool such as
OneDrive looks in. What can be had again (the files extracted from the game, the
indexes built from them) is under `%LOCALAPPDATA%\AnnoModdingStudio`, which nothing
syncs. On the machine this was decided on, Documents held 39 MB of projects beside
1.8 GB of backups and 371 MB of extracted game files: the part worth backing up was the
smallest, and the extracts were moved out for it. Backups stayed. Most of their size is
copies made because somebody asked for one, and those belong with what is yours.

Projects were not moved beside the library, though that was asked. A project file holds
the change set and its history, so with it alone the mod can be exported again; the
rest can be read from the game or downloaded again. That makes the project the one file
that cannot be had again, and the local app data is hidden, not backed up and what
people delete to clear an app's data.

The library is under the local app data too, and not under Documents, for two reasons.
Files On Demand can turn a mod behind a link into a placeholder the game cannot read,
and a library runs to gigabytes: 2.5 GB here, against the 5 GB a free OneDrive account
holds. The game's own folder was turned down as well. It goes with an uninstall made to
free space, and the library outlives the game where it is now, the uninstall taking only
the links. The local app data is not wiped by Windows' own cleanup; what it lacks is a
backup and a way onto a new PC, which is why the library records what it would take to
get each copy back (see [The library](#the-library)).

**Everything in one folder** is there for somebody who wants all the Studio writes in
one place: a folder called `AnnoModdingStudioData` beside the Studio, or any folder
`ANNO_STUDIO_FOLDER` names, holds the settings, projects, extracts, cache, library and
mod.io key. Making the folder is the whole of opting in, and nothing is moved into it.
It is not the default, because Windows keeps the two kinds of file apart for the
reasons above, and a program for other people follows that unless told otherwise. A
variable naming one of the Studio's folders still wins over it, which is how the test
suite stays out of a developer's own; the suite sets `ANNO_STUDIO_FOLDER` to `none` for
the same reason. A second launch over the same folders hands over to the Studio
already open there (`ui/one_studio.py`), so two never write the same settings and
library at once.

## Getting assets.xml

This is worth automating because doing it manually is a trap.

### Reading the game once, not every time

Extraction is the slow half of loading Anno 1800, and it was being paid every
time. The asset index has always been cached between launches — but it was keyed on
the extracted file's *timestamp*, and extracting again writes a new file with a new
timestamp, so the cache never hit for the one path that matters. It is keyed on the
file's contents now — a hash of the whole file, about a fifth of a second on the 289 MB
one — so an extraction that produces the same bytes reuses the index it already has, and
any edit at all, even one that leaves the file the same length, does not.

An index that can never be used again is deleted after the next successful build: one
built under an older cache format, or a half-built one left by a run that was killed.
Indexes for other files are kept, because which of those you still want is not the
Studio's to guess.

That leaves the extraction itself, and a game is only patched every few weeks. Each
extraction writes down what it took and what the game looked like at the time: which
archive each file came from, and the name, size and timestamp of **every** `.rda` in
the installation. A patch rewrites archives, so comparing that list against the
installation now answers "is what I extracted still what the game ships?" in a few
`stat` calls — and the user never has to know when a rebuild is due, because the
question is answered for them:

It used to be a single un-highlighted button on a card
that folds away as soon as anything is loaded, which is to say it was not a pathway at
all.

#### One game is never read into the other's folder

Moving the extracts' default out of Documents broke switching games. *Extract to*
keeps a folder you chose and replaces one it only suggested, and it told the two apart
by comparing the field with today's defaults. A destination remembered from before the
move still held the old default for Anno 1800, matched no default, and was taken for a
choice: Anno 117 was read into Anno 1800's folder and remembered as there. A folder
holds one read stamp, the last game's, so **Use these files** on Anno 117 opened the
files already open and only loaded their texts again.

A folder named for a game is a suggestion now wherever it is, and never where the other
game's files go. In a folder of your own the other game goes beside it; in either of the
Studio's places it is today's default. A game's last read is a stamp that says it is that
game's, looked for where it was remembered, then at the default, then where the default
used to be, so a read made before the move is still found.

### What the Export tab still asks

It used to be
editable, which let the mod's name and the project's name disagree silently.

Before this change, only the project's own mod
could be edited here, so a mod of yours opened for direct editing had every field locked:
a loaded mod was taken to be somebody else's. Whether a mod is yours is decided once per
mod, not after every keystroke. It is partly decided by the creator name and ModID, which
are two of the fields you might be retyping, and re-checking after each letter locked the box
one letter into the change.

### Who you publish as

It used to be asked exactly once, at the moment it first
mattered, pre-filled with your Windows username, before anyone had used the Studio
enough to know what any of that meant.

It was also one name. A mod you had published under another one was somebody else's,
and the only way to change it directly was *Make your own copy…*: a fork, with
*(my copy)* on its name, a ModID of its own, and no mod.io page to publish to. The
Studio can't tell your old name from a stranger's, so you say it: *Also published as*
in Settings, or *Carry on as mine* when the copy is offered. The other names are a fact
about you, kept in the settings beside the author name and never in a loadout. New
mods take the author name, and a mod by another of your names keeps the one it has,
so an update goes out under the name its players know.

It used to open the last one for you.

### The other tabs wait for the Start tab

Loading an assets.xml and clicking straight into the asset editor used to land you
somewhere that looks like it works and does nothing: no project, no mod to write into,
no way of working chosen.

### Importing one mod starts where the mods are

It used to open already pointed at the mod you imported
last — filled in but not read, so the Import button sat dead with nothing saying why, and
picking the same folder again was what woke it up.

## Supported games

Both dialects were taken from real, currently-shipping mods rather than guessed.

## How display names resolve

On a real, current Anno 1800 install,
a Schnapps product's `LineID` (`5357`) has been reassigned to an unrelated "Remove"
button label, while the product's own embedded text still correctly says "Schnapps"
— so the embedded copy is the one to trust when both exist.

### What mods call things

Half the mods on
mod.io ship one (449 of 857 for Anno 1800, 125 of 371 for Anno 117).

## Three ways to use it

It used to say
*in use*, and other things turned on it: which export button was picked out, whether your
own mod was offered beside a patch, whether renaming the project renamed the mod. That was
half a mode, and the half that was wrong: pick *write a mod of your own*, load somebody's
mod to check against, patch it, and the card went on claiming you were writing your own
while the Studio behaved as though you were not, or the other way round. It cannot be
inferred either — loading extra mods to check compatibility, switching most of a list off
to watch three of them, turning your patch on a mod into a fork of it all look, in what the
project holds, like a change of job, and none of them is. So each of those places now reads
what it actually needs instead: the [export button](#writing-them-back-out) follows what
is loaded, and the project is renamed with its mod because the card said the project is
that mod — the one fact a card writes down.

Your own mod used to be left out
while empty, under some cards and not others, and every such rule was one more thing to
know before the lists could be read; an empty row at least says that the mod exists and
that it is empty. What it does not do any more is exist before anything made it. A project
that has done nothing holds nothing: there used to be a *My Anno Mod* in every project from
the start, which then stood beside the patch when you read a downloaded mod in to change —
a mod nobody decided to make.

It used to keep saying *Changing a downloaded mod*, and
went on treating the mod as somebody else's wherever the way of working was consulted.
Projects saved like that are recognised when they are opened.

It was already the thing this aim was for; asking somebody to find *Changes
go to*, pick the mod there, and then find *Patch this mod…* was asking them to assemble
what they had already said they wanted.

The card used to be *Run your whole mod list* and to promise "everything you have
installed". Once collections existed its project was routinely something else — a
collection being tried, or whatever a download fetched along the way — and the one fact
that told them apart was a checkbox on the fourth card named for its mechanism. Where a
list came from is now which button started it; that it is a set to play is what the card
writes, as the first card writes that the project *is* the mod.

There used to be
a third that kept the mods loaded and only relabelled the work, for checking compatibility;
but a card is a setup, and a setup laid over whatever happened to be loaded is not one.

## The steps

### Finding an asset

The list **floats over** the editor instead of sitting in its layout, which is not just
tidiness: an inline list pushes the tree down while it is open and lets it spring back
when it closes, so the row under your cursor is not the row you were about to click.

There is
no group facet: both games nest `<Group>` elements (18,077 of them in Anno 1800) but
never name any of them, so grouping carries no information.

A click opens the asset, which was
the only thing this list could do — so the commonest thing anybody wants out of it, the
GUID, had to be got by opening the asset and finding the number again in the field
editor.

### Editing

- The note area has a fixed height sized for its longest message, so
  switching between a one-line and a two-line note never shifts the table above it —
  which otherwise moved the very row just clicked out from under the cursor.
- The `▸` marker
  and full-strength name are what used to be greyed out, which read as "you cannot pick
  this" — the opposite of true for a container, which is exactly as selectable as anything
  else here.

The loaded asset tree is never mutated — it stays as the pristine base-game state. That
matters: paths are generated against original values, so editing (say) a `<Product>` that
another entry's path keys off can't silently corrupt a different ModOp.

#### Undo and redo

Both panels edit the same mod, and a Ctrl+Z that means
something different depending on which one has the focus is worse than none.

A step holds only what moved, not a copy of the project: with a mods folder open there are
seven thousand changes, and a step that switched one off is one change's worth of memory.
Finding what moved is one pass over the change set against the copies taken last time.

Leaving an undo step
behind that half-reverses a folder read would be worse than having none.

### References

What makes a graph possible anyway is measuring each *field* across the whole
shipped file rather than guessing at each value: `Cost/Costs/Item/Ingredient` resolves to
a real asset every single time it appears, `Health/DestructionWarScore` essentially never
does, and the difference is stark.

- One stray click on a node
  otherwise meant hunting the asset you were on back out of a column of twenty.
- Each used to keep
  its own idea of which asset that was, so the graph went on showing whatever it last
  drew, and only the graph could go back.
- The arrows are drawn rather than typed. A `←` in the body font is a hairline, and
  from arm's length it read as a dash.
- That list resolves its rows once, when it opens: looking each asset up while filtering
  meant a few hundred database queries *per keystroke* — about 90ms each on a real hop,
  for answers that cannot change while the dialog is open — which made typing into it
  feel broken. Filtering is pure string matching now (2ms over 365 rows), and debounced
  like the app's other filters.
- The graph is the part of the app people explore rather than
  read, and it was the one step where what you could do was only ever whatever the mouse
  happened to do to what was under it — a click re-centres, a double-click edits, and the
  GUID on a box could not be copied at all without opening the asset first.
- `ReferencePanel.menu_for()` builds it apart from
  showing it, the way the *Mod changes* panel does, so what it offers can be tested
  without a modal menu on screen.

### Exporting the mod

534 of the 854 Anno 1800 mods on mod.io ship something besides those
two, so an export that wrote only them was a broken copy of most real mods —
though never when writing back over the mod's own folder, where those files
already sit and nothing is copied at all.

#### What the folder is called

A mod read from a folder kept that folder's name on export, renamed or not. The name
is what the game keys a mod on in Anno 1800, and it can carry a number that puts the
mod in the load order, so a tidied `[Category] Name` would have moved it. But renaming
a mod on the Start tab then changed only its modinfo. The Export tab picks where
exports go, not what each is called, so nothing there could name the folder or the zip
either: *Shrine Rework* renamed *Flavourful Shrine Mechanics* still exported as
`[Gameplay] Shrine Rework [Aleph]`.

So an export follows the name once it has been changed here
(`generator.export_folder_for`). A mod is renamed when its name, or a category its
modinfo declares, isn't what its modinfo says, or for a mod with no modinfo, when its
name isn't its folder's. Left alone, it keeps the folder it came in, number and all.
Play doesn't follow it: the mods folder holds the mod under its old name, and Play's
switches and link are keyed on it, so building the new name there would load the mod
twice. A mod bundled inside another keeps its place inside its host.

#### What the game's own loader makes of it

- *Easy Seed Search*'s seven unread ops unlock seeds that other mods add —
  none of them in the loadout it was asked in, so all seven do nothing there.

What the check is careful about, each learned from a mod that read as disagreeing when it
did not:

- **What a mod reads, not only what it changes.** It stands on the mods ahead that change
  an asset the mod changes *or reads* — copies with `Content=` or a `ModValue`, or asks a
  condition of — and on what those read in turn. *Item Sockets* copies each building's
  socket count from the town hall's, which *Item Buildings add additional Slot* raises.
- **Which mods are loaded.** `xmltest` tells a condition no mod at all is loaded, so every
  `#ModID` is false in it. The check writes in what the Studio's reading answered:
  `true()`/`false()` for Anno 117, and for Anno 1800, whose loader takes `true()` for
  false, a condition that finds the file's root or not.
- **Only this mod's own work.** An asset the loader left alone is held against the
  Studio's picture of the mods underneath; one the loader was handed differently from
  that picture is counted apart, as being about those mods. A new asset counts under its
  own GUID, not the one it is placed beside.
- **What cannot be compared.** An asset the mod adds under a GUID another loaded mod adds
  too is named, not judged — *Trade Union Rebalance* and *Extended Agriculture Modules*,
  by one author, both add seventeen of the same GUIDs, and the game finds only one of
  each. An asset an op the Studio carries unread is aimed at is counted as changed by
  that op — including one inside a group that goes out whole, whose rule now remembers
  where its unread ops land.
- **Where the build is not the game.** `xmltest3` adds an `<Asset>` written straight
  under `<ModOps>` only where the file's last top-level group has an `<Assets>` of its
  own, and Anno 117's has not; the game adds it all the same, which a probe mod loaded
  first showed in the game itself. *Easy Seed Search*, *Buildable Residences* and
  sixty-five more of the mods on mod.io read as differing from the loader by exactly
  the assets they add. So for Anno 117 every stack is applied on top of an empty group
  at the end of the file — the snippet the loader's documentation has a mod add first —
  after which the build does what the game does.

And what it found in the Studio's own reading, each put right and pinned:

- **An entry another mod added, removed by a whole-list replace,** was written as
  `ItemsPool/Item` — every item — because the replace's removals of the shipped entries
  had gone first and it was the last left. Its path is now keyed the way a shipped
  entry's is, against the list as the op found it. (*Vendors only sell common Items* over
  *Military Attention*.)
- **A merge into an empty field** set a second one beside it where one side had children
  and the other none. Both loaders always merge into what is there: an empty field takes
  the body's fields, one holding text keeps it and takes them after it, and text merged
  into a section goes after its children. (*Trade Union Rebalance* on specialists that
  ship an empty `<FactoryUpgrade/>`.)
- **A replace with several nodes** puts them in last first, in both games — `X, Y, Z` comes
  out `Z, Y, X` — where `add` and the sibling ops keep the author's order. The Studio now
  does the same.

On the installed Anno 1800 mods this was written against, the four that differed —
*Item Sockets*, *Pools and Definitions*, *Trade Union Rebalance*, *Vendors only sell
common Items* — agree now, and so do the seven that agreed before. *Trade Union
Rebalance* agrees on its 293 assets, with its seventeen shared GUIDs and the six guild
house items its unread op gives the GUID 1 said beside it.

- It used to default to the mods folder, so that exporting
  installed the mod; that is what Play is now, done properly.

## Editing beyond single values

Every dialect above was read off real, currently-shipping mods rather than
guessed. `addNextSibling` keeps the classic attribute form in both games because
that is what the published Anno 117 mods do for it, even though they use the
shorthand everywhere else — and it is the only form with a Path-less variant,
which is how a new asset anchors onto an existing one.

### The asset as it will be

Two consequences worth spelling out, because both looked like bugs before they were
handled:

- Without this, anything you added as a container was a dead end.
- **Nothing rebuilds the tree on a payload edit**, so a value edited inside an addition
  has to refresh its own row — otherwise the **Refers to** column keeps describing the
  asset the field used to point at.
- Without an explicit check, the previous tree just stayed on screen, fully
  editable, describing an asset that no longer existed.

One invariant falls out of this: **verification must use the shipped asset, never the
effective one.** `Workspace.asset_element` hands back the tree with this mod applied,
which is right for the editor and wrong for checking a change — comparing a replace to
the value it just wrote makes every edit look like it has drifted. `verify_changes` asks
for `base_asset_element` for exactly that reason.

### The same asset as XML

The tree is a picture of the result and no picture of how it came about.
Somebody who reads the game's `assets.xml` and writes XPath by hand asks which
ops, of which mod, in which order, and until now the answer was off to one
side: the whole document on the Export tab, or *Show XML…* on a row. The XML
view puts it where the work is. It is the first phase of
[the XML view proposal](proposals/xml-view-proposal.md), which has the
reasoning. Three things about it are worth knowing when changing it.

- **It is not a second reading.** A tab's ModOps are `build.fragment_xml` for
  that mod's changes to the asset, the text *Show XML…* gives for those rows.
  Its asset is `Workspace.effective_asset` stopped at that mod, the tree the
  editor draws. What a later mod writes over is `conflicts.find`'s to say. So
  the XML view, the tree and the changes list cannot disagree, and a mod that
  restates another's value is not marked here when the list does not count it.
- **A rule is not in a tab's text.** Neither is an op carried unread. Each
  belongs to more than this asset, and the text of a tab is what that mod says
  about this asset alone. They are counted under the panes and shown on
  asking. That is the line the next phase needs, when the text can be typed
  in: what is typed must be this asset's and nothing else's.
- **It is filled when it is looked at.** Drawing an asset for a 400-mod
  project is slow enough already, so the XML view behind the tree is only
  marked as stale, and read when it comes to the front. Measured 5 Oct: the
  busiest asset of the 411-mod bench project, 453 changes of 134 mods, comes
  to 136 tabs in 1.0 s. The largest asset of each game as text (5,242 lines
  in Anno 117, 3,078 in Anno 1800, picked by its size in the index) goes
  into a pane with the colouring on in 0.05 s.

- **What a mod changed is marked by comparing two assets, not by reading
  its ops.** A bar in the strip beside the asset, and shading, mark the
  lines that are not as the tab before left them
  (`xmlview.changed_lines`). The first picture of the view with a real mod
  in it showed why it is needed: to see what an op did, the path had to be
  walked by eye. Reading the ops for it would mark what narrow changes say
  and miss a rule's work and a kept op's, which are in the asset and not in
  the tab's text. Two trees compared marks them all, and cannot disagree
  with the asset shown. A list is gone into entry by entry, so one number
  in a list of forty is one line. An entry taken out leaves no line to
  mark, so the list's own line is marked. An asset the mod adds has no
  marks: all of it is that mod's. The caret on an op takes the other pane
  to the node it sets, which is the same question asked from the other
  side.
- **The marks are held by cursors.** A line number goes stale with the
  first line typed above it, and the asset pane is typed in.
- **Fields and XML are one switch, not two radio buttons.** Radio buttons
  beside the name read as a small option of the asset. The switch changes
  everything under it, so it is drawn as one control with the half on show
  pressed in.
- **A click does not draw the focus mark.** The first picture of the two
  radio buttons had an underline under *XML*, which was the focus mark and
  read as a second selection beside tabs that underline what is current.
  Qt gives a clicked control the focus and a style sheet cannot tell that
  focus from the keyboard's, so the application marks a button, checkbox,
  radio button or tab that got it from a click
  (`theme.keep_focus_marks_for_the_keyboard`), and the style sheet draws a
  marked one as it is without the focus. A key pressed on it takes the
  mark off. Reached by Tab, a shortcut or the Studio itself, a control
  draws its focus as before; fields and lists always do, since there the
  mark says where typing goes. It is the reverse of the obvious rule, mark
  what the keyboard reached, on purpose: a control nobody marked draws its
  focus, so nothing that forgets the filter loses it.
- **A pane held shut says so where it is.** Typing in one pane shuts the
  other, since two typed panes would be two answers. Seen cold, a pane that
  goes grey at the first key looks broken. So its heading gains *Updates on
  Apply*, its tooltip says what opens it, and the line beside Apply says it
  once, without talking over a refusal already there.
- **The strip scrolls, and names are not cut short.** Cut to fit, a strip
  of mods is a strip of `[Gameplay] …`, since mods share their first words.
  Rendered with 17 mods on one asset at 1500 and at 1100 wide.

There is no tab for the result. With the asset on every mod's tab, the last
tab's asset is the result, and a rule's work is in the tab of the mod it
belongs to, because it runs in that mod's turn.

Ctrl+F changed with it. Find was the search for an asset, which is not what
the key means in a pane of text. A key that depended on which view was in
front would be worse than either meaning, so it finds inside the open asset
in both views, and the search for an asset is Ctrl+P. With no asset open
there is nothing to find in, and Ctrl+F still opens the search.

#### Typing ModOps

The pane of the mod changes go to is typed in, and **Apply** makes the mod
say what was typed. The text is never stored. The Studio holds changes and
writes a mod from them, so what is typed is read by the reader a downloaded
mod goes through, and the mod's changes are moved to match
(`core/typedops.py`). Four decisions, each made because the obvious thing
went wrong.

- **Only what moved, moves.** The obvious Apply takes out the asset's changes
  and puts in what was read. That sends every op to the end of the mod's
  file as an edit made here, though only one value was typed over, and a
  downloaded mod's ops have places that mean something. So what was read is
  matched to what the mod has: the same op is left exactly as it is, an op
  aimed where one was aimed is that change retuned in place, and only the
  rest is new or gone. A new op is placed as an edit in the tree would be,
  which is also what makes one typed over a rule's answer the exception to
  that rule.
- **A pane is typed in only if its text reads back.** Typing nothing and
  applying must change nothing. That holds when the ModOps the Studio wrote
  for an asset read as the changes they were written from, and it is asked
  each time the pane is shown (`typedops.reads_back`). Where they do not,
  the pane is shown and not typed in, and says so. Measured 5 Oct on a
  seeded sample of 3,000 of the 12,368 mod-and-asset pairs in the 411-mod
  bench project: 2,060 had nothing of their own, only rule answers; of the
  940 with ops of their own, 932 read back and 8 did not, in four mods. The
  eight are ops that come back as a rule, or as fewer or other ops. A read
  takes 0.2 to 0.35 s on that project.
- **The asset is read as the ops find it.** That is the asset as the mods
  before leave it, with what this mod's rules do to it. Read against the
  game's own asset, an op typed over a rule's answer recorded the wrong
  value as the one it replaced.
- **A comment typed above an op is its note,** as it is in a mod read from
  disk. That needs the Studio's own comments to be told from a person's:
  when this was built a heading over each asset's ModOps was written afresh
  each time, and so was a description over every op. They open with
  `:AMS:`, and the reader passes over them. Until they did, each Apply
  turned them into notes about themselves, and this pane took only
  `<!-- note: … -->` for a note.

An op that names this asset and no other is the asset's where its text alone
says what becomes of it: narrow changes, an op marked to be kept as written,
or one the reader could not follow and carries. All three are in the pane and
typed over there. A search, or an op that names several assets, was applied
and left the pane: it was then one of the mod's ops that reach beyond the
asset, which the pane did not hold until phase 2 of the
[file tab proposal](proposals/file-tab-proposal.md).

**The pane holds the ops that reach beyond the asset too.** They had no place
to be typed but the whole file: a pattern, an op naming several assets, an op
in a group. With every op kept as typed, one that reaches 41 assets is text
with a reading under it, as the asset's own is, so it can stand in the pane
(`xmlview.beyond_ops`, `Turn.beyond`). What it needs that the asset's own
does not is to be seen for what it is, since typing over it from one asset's
tab changes the others.

- **It is drawn, not commented.** Its lines are on a tinted band with its
  badge in the strip, under a heading the pane draws on the blank line above
  it. A comment in the text could be read past, typed over, or applied as a
  note. The heading and the band are held by cursors, as the marks are, so
  they stay with the op while the text above it is typed in.
- **Left as it is, no op in the pane is read**, the asset's own included.
  `typedops.read` takes each out of what is read before reading
  (`Read.standing`). What it comes to was read where the mod stands, and a
  reading from one asset's pane knows less. For an op that reaches beyond
  the asset it would find the other assets as the game's files have them.
  For the asset's own this was already going wrong, before this phase and
  without anybody seeing it: every op in the pane was read again at each
  Apply, and one the pane could not read was carried unread from then on and
  remembered as taken out, whatever had been typed. An op that copies a
  value another mod leaves (*Early Game*), and every op for an asset the
  mod adds inside a group (*Require All Needs*, 25 ops), were lost that way.
  What is typed is read against the asset with the ops that stand, and
  without the ones typed over or out. It also means a pattern in the pane
  costs no search at Apply.
- **What an unchanged op comes to after a changed one is the background
  reading's.** The pane used to read its ops in a row, so an op left alone
  after one typed over showed what it came to at once. It shows what it came
  to before until the mod is read again from the changed op on, which every
  edit now starts (above). With that reading switched off it stays so.
- **Typed over, it is the op it was.** It keeps its uid, its place in the
  file and its group, as an op opened in the pattern editor does
  (`store_one`). In a group that does not run it comes to nothing, since it
  was read as though it ran. And it is read against the asset without what
  the old one did: a doubling typed over as a tripling comes to three times,
  and not to six.
- **Taken out of the text, it is taken out of the mod.** The pane's rule was
  already that what is typed is the whole of it, and an op in the pane that
  could not be taken out there would break it.
- **A new op goes where it was typed, among the ops the pane holds**
  (`typedops._room_at`). It used to go after the mod's other ops wherever
  it was typed, which was right while the pane held only the asset's own:
  after them all was the one place it could mean what was on screen. With
  the ops that reach beyond the asset in the pane as well, somebody types
  an op between two of them to have it run between them. The pane is part
  of the file, so that is not a line of the file. It is straight after the
  op typed before it, and room is made there
  (`Loadout.make_room`): of the places between the two, the one that
  needs nothing known of the ops the pane does not show.
  - After an op in a group it goes after the whole group, since it was typed
    with no group round it. Where the op after it is in that group too
    there is no such place, and the order typed is kept: it goes between
    them, in no group, and the writer writes the group round it in two
    parts, as it does for a change of the Studio's own that stands in one.
  - Typed before them all, it goes straight before the first, or before
    that one's group.
  - Every later thing of the mod moves one place on. An op carried unread
    is known to undo by which entry it is, so one that moves is another
    entry in the first one's place.
- **The button counts before it is clicked** (`typedops.reach`). The text
  the pane was handed and the text it holds are each read into their ops,
  with nothing run. An op that names its assets says how many; an op the mod
  held knows which it answered to. A search typed or changed cannot be
  counted without reading it, so the button says "Apply to a search", and
  Apply reads it and asks with the number. Reading at every pause to keep a
  number on the button was the alternative, and is a search of the game's
  files as each key is pressed.
- **A group's own tags are not in the pane.** A group holds ops that do not
  reach this asset, so its tags there would bracket text that is not all of
  it. Its op is shown as the op, with the group and its condition in the
  heading, whether the group runs or not.
- **Nor is a group the reader kept whole.** It is one op whose text is the
  group's. In the pane it read back as a group typed there, and took the
  place of the one the mod had, with every asset it reaches read again (15
  s for *All Items visible at Traders*). It is counted under the pane, as
  before, and typed over in the file tab.

Measured against the census (11 October 2026), with a script that takes up
to twelve assets of every mod, the ones an op reaching several assets
answered to first, and applies each one's pane as it stands. Nothing was
typed, so nothing may move, and the button has to say plain "Apply". All
2,675 panes of the 424 Anno 117 mods and all 14,098 panes of the 2,043 Anno
1800 mods come back as they were; 10,316 of them hold an op that reaches
beyond the asset, 17,224 such ops in all. The slowest Apply is 1.9 s
(*Noblesse oblige*), and 1.5 s in *GUID Explorer*, one ModOp for each of
18,057 assets.

The first run had 19 panes of Anno 117 that moved, and the three faults above
are what they were: the op that copies a value, the asset added in a group,
and the group kept whole. The first two were in the pane's Apply before this
phase. Not measured: a pane with something typed in it, which the tests
cover a case at a time.

**An op typed in the pane may go somewhere else, and nothing remembers where
it was typed** ([file tab proposal](proposals/file-tab-proposal.md), phase
3). An op for another asset typed on this asset's page is applied and leaves
the pane, and the pane does not keep it because it was typed there: what the
Studio shows would then depend on how you got there, and would not outlive a
restart. Three things make that easy to follow instead.

- **Before Apply the pane says where it goes** (`typedops.goes`). Which
  assets an op names is in its attributes, so it is told from the text as it
  is typed, with nothing read, as the button's count is. Only of an op typed
  in or over since the pane was filled: an op the pane was handed is drawn
  for what it was read as, under its heading.
  - An arrow in the strip is on the line the op starts on. The words are at
    the right-hand edge of the pane on the line it ends on. After the end of
    the opening line was tried first, and at half a window's width that is
    off the right of the pane for nearly every op, since the opening line
    holds the GUID and the path. At the right edge of the opening line it
    covered the path. The closing line is `</ModOp>` and has room.
  - On a line long enough to be covered, the words are not drawn while the
    caret is on it.
  - While the text is half typed and not XML, each mark stays with its line
    and is not taken away: it would otherwise blink at every tag opened.
  - The same sentences are the Apply button's accessible description, by
    line, since what the pane draws is not text.
- **After Apply the Mod changes panel selects what the step added or
  changed** (`undo.touched`, `ChangesPanel.show_rows`). It is asked of the
  step of undo and not of Apply, so it is the same for ModOps typed and for
  the asset typed. An op's row is selected and nothing under it; what only
  moved along the file to make room is not; at most 200 rows. Selecting a
  row opens nothing. A value set in Fields selects nothing: what it changed
  is in front of whoever set it.
- **Undoing an Apply puts the text back as typed** (`Step.typed`,
  `AssetXmlView.put_back`). The pane is filled from the mod as before, and
  the typed text is then put over it as one step of the pane's own undo that
  changes only the part that differs, so the headings stay with their ops.
  - *With another asset in front* (the proposal's open question 3), Undo
    goes to the asset the text was typed on, as it already did for a step
    about one asset; a step about several used to be undone where the editor
    stood. The text is put back only in the XML view with changes still
    going to that mod. In Fields, on another tab, or with something else
    typed, the Apply is undone and nothing is kept for later: Redo has it,
    and text held back to appear when the pane is next opened would be the
    remembering the section rules out.
  - *The text put back is dropped before the next step of undo or redo*
    where nobody typed in it since. Redone, it is the mod's. A step further
    back would otherwise be made under text nobody is typing, with the
    warning that the mod moved while you were typing, at every step.
  - It is text typed, so leaving the asset asks about it, as for any other.

An op the reader keeps for a reason of its own is beside the pane too, though
it names one asset. The first cut put every kept op of the asset in the pane,
and the read-back gate then shut those panes: such an op is kept because of
what the rest of its mod does (its narrow form does not add up there), and
read alone it comes to plain changes. On the same 3,000-pair sample, panes
that had been empty and typed in became shown and not typed in for dozens of
assets. With only marked ops in the pane the sample reads as it did before:
932 of 940.

**Typing the asset.** The other pane is the asset as the mod leaves it, and
typing there says what the asset should be and not how. It is the habit the
Studio set out to put right, made into a way of working: paste the asset,
change it inline, and get the narrow ModOps a wide replace should have been.
The reading is the reader's, as for a wide replace in a mod. What is the
XML view's own is deciding what to hand it.

- **Only the nodes that differ.** The first cut was one replace of the whole
  `<Values>`. The sample asset in the tests, untouched, was refused: assets
  are padded with empty entries nothing tells apart, the reader was asked to
  match every one, and said the whole asset would have to be swapped. So the
  typed asset is walked against the asset as the mod's other ops leave it,
  and a replace is made for each smallest node that differs
  (`typedops._differing`). A node whose fields are each there once is gone
  into. A list is handed over whole as soon as one entry differs, since
  which entry is which is the reader's to decide, by key. An untouched
  asset comes to no ops at all, and the reader is never asked.
- **A change with no narrow form is refused.** Where the reader would keep
  the replace as written (a list with entries it cannot tell apart), nothing
  is applied and its reason is on the line. The proposal had the list
  swapped whole; that would leave a wide op this pane could not take back
  out, so it is left to Fields or to a typed ModOp. The reason on the line
  is the reader's reason for having no narrow form, with the path of the
  list. Its reason for keeping such an op in a mod goes on to say that what
  is shown swaps the whole node, and here nothing is shown.
- **A placeholder taken out by hand is a removal.** The reader leaves out
  the removals a wide replace only implies (*Removals a wide replace only
  implied*, below), and the typed asset inherited that: the lines of a cost
  entry with no amount, deleted, came back on Apply. That rule is about
  what an author did not retype. In this pane every entry is on show, so
  one that is gone was taken out, and *Remove from asset* in Fields writes
  the same removal. So a typed asset is read with those removals kept, and
  the line counts the ones the game may not notice. Found by hand in the
  baseline of 9 October 2026, where the first answer had been to leave it
  unwritten and say so.
- **The mod's rules and kept ops are part of what the asset is read
  against.** A typed asset can only say what the mod's narrow changes come
  to on top of everything else, so those are all it moves.
- **Differs from what has two answers, and the asset pane uses both.** The
  better one is the asset as the mod's other ops leave it: what comes back
  is then everything the mod's narrow ops should come to, matched against
  the ones it has, so an entry the mod added and you took out is its
  addition gone. It needs the asset as shown to come back as the changes
  the mod has. Measured on the first 1,000 pairs of the same sample, it
  does for 678 of 681 assets the mod reaches by rules alone and for 199 of
  319 it has narrow changes to: a mod read from disk has changes no reading
  of the asset arrives at again, a field made sure of that reads back as a
  field added, for one. Shutting the pane for the rest, as the ModOps pane
  is shut, would have shut it for a third of the assets anybody edits. So
  where the first answer does not hold, what is typed is read from the
  asset as shown and laid over the mod's changes, which is what an edit in
  the tree is (`typedops.lay_over`). It always holds, and it takes none of
  the mod's changes away. With both, typing nothing changed nothing for
  995 of the 1,000; the other five are assets a mod has emptied, which
  have no GUID to be known by and are not offered for typing.
- **The op that adds an asset is typed over only where it is all there
  is.** Typed into it, what the mod's later changes or its rules do to the
  asset would be in it twice. And where the op is a rule's, or one kept as
  written, it is that op's and not the pane's: read as the pane's, typing
  nothing wrote the asset a second time. Both were found by putting every
  sampled asset through untouched, 28 and then 10 of them.
- **An asset the mod adds has no ops to work out.** The typed asset is the
  payload of the op that adds it.

**Keep as written.** `KeepAsWritten="1"` on an op has it written back as it
is. It is the answer to "the Studio rewrote what I typed", and it is the
reader's to honour (`Translator.keeps`), so it holds wherever a mod is read:
an import, a mods folder, a re-read, and Apply. The op is still read, and
goes in as a rule with its reason, as an op kept for the reader's own reasons
does. An attribute and not a comment, because it travels with the op and
adds no reader of comments; both games run an op carrying one they do not
know as they run it without (measured in game, 4 Oct 2026, with a probe of
marker ops). Marking an op takes the place of the narrow change it was,
under the same key and with the same value, which the workspace did not see
as a move until a change's rule became part of what it knows the change by. Text left unapplied belongs to no project, so anything that
would leave it behind asks first, and throwing the project away asks about
it before it asks about saving.

**What ops as written changed (9 October 2026).** The text *is* stored now.
Every op is kept as it was written, typed ones included
([the proposal](proposals/ops-as-written-proposal.md)), so three of the
things above are history. Apply no longer rewrites what was typed, and
"only what moved, moves" is a comparison of text: an op the text still has
is the op it was. The read-back gate is gone, since there is nothing to read
back, and the ModOps pane is always typed in. And `KeepAsWritten` means
nothing to the Studio any more: an op carrying it is kept like any other,
with the attribute, since its text is kept. What is said above of the typed
asset still holds. It is where the narrow reading is still used.

### The mod's file

The XML view is an index into a mod's ops by asset, and its pane takes typing
for the ops that are one asset's own. That left some things with nowhere to
be typed: a pattern, an op naming several assets, an op in a group, the
group, and the order they all run in. The cause is one thing. An op lives in
a file, at a place in it, and the Studio had no view of the file. The **Mod
file** tab is that view
([file tab proposal](proposals/file-tab-proposal.md), phase 1).

- **Whose file is shown is the tab's own to say, apart from where changes
  go.** As first built the tab held the file of the mod changes go to, and
  its picker was the Asset editor's. So reading what another mod says meant
  pointing changes at it, which asks whether to patch it, and making the
  patch showed the patch's empty file and not the mod. Looking is not a
  decision to change. The tab's picker now only picks the file
  (`FilePanel.looking`); it starts on the mod changes go to and follows
  that when it moves. A file changes do not go to is only shown. *Edit this
  file* is the one thing that points changes at it, through the Asset
  editor's picker, so the patch question has one place it is asked from.
  *Show in file* on another mod's row shows that file and moves nothing.

- **The writer says which lines each thing is on** (`generator.written`),
  for every op, group, heading and ModOp of the Studio's own. The tab, its
  outline and *Show in file* all go by that. The XML view still finds an op's
  line by searching the text for its opening tag.
- **Which op is which is told by its text**, as in the XML view. The text the
  typing started from and the text typed are each read into ops by the
  parser an import uses, and the two runs are compared as two versions of a
  file are (`modfile._matched`). An op in both is the op the mod holds, with
  its uid, so an edit that is an exception to it is still one. An op in both
  at another place has been moved, and is that op still. What is left of the
  typed ones is new, or typed over the old op that stood there and was aimed
  where it is aimed.
- **Only what changed is read**, as the XML view reads it: against the game's
  files as the earlier mods leave them, with what the rest of the mod does.
  Reading the whole text at every Apply was built first and taken out. It
  read every op apart from the stack, which knows less than the stack's own
  reading: where two mods set one field to the same value the stack has a
  change for each, and the second mod read apart has none. An Apply that
  changed one op then lost what ops nobody had touched came to (census unit
  5540293). An op left alone keeps the reading it had.
- **A change the Studio wrote stays that change** where its ModOp is typed
  back as it was. Its place is where it was typed. Changes typed one after
  another for one asset share a place, so they are one block, and within
  it the writer keeps its own order: removals first.
- **An answer does not land on another's key.** A change is known by where
  it is aimed. An op typed after a change of the Studio's and aimed at the
  same field came to a change under the same key, and set over it the first
  was gone from the mod and from its file. Read in one run the reader tells
  such two apart with a uid; read apart, the store does
  (`modfile._store`).
- **An op the text still has keeps its spelling** (`Op.source`), and not the
  layout the tab shows it in. It is by its spelling that an op read from the
  mod's folder again is known for the one held, and for one taken out here.
- **The headings over each asset's ModOps were drawn on the blank line above
  them,** not put in the text, where they could be typed over and left wrong.
  They are gone from the file now (*What the Studio no longer says in the
  file*), and the name beside each line says what the headings did.
- **An `<Include>` typed for a mod with no folder is refused.** The reader
  follows one from the file that names it and passes it by where there is no
  such file, so applied the line would be gone without a word. For a loaded
  mod it is read as an import reads it, and its file is kept as a file from
  then on (see *Ops kept in other files*), though the text shows its ops
  without the line until the tab draws files as regions.
- **The Mod changes panel folds to a strip.** It takes a third of the window
  on every tab, and editor, outline and panel side by side are tight at 1500
  wide. Folded, the strip is all the side is, and the splitter is remembered
  with the panel open so that it opens at the width it had.

Measured on the census (11 October 2026), by a script and not yet by the
harness: each mod's file is applied as shown, and nothing may move; then one
ModOp is typed at its end and applied, and each op the mod had has to be the
op it was, under the same uid, with the same changes under it. All 424 mods
of the 364 Anno 117 units, and all 2,043 mods of the 854 Anno 1800 units,
115,947 ops and groups between them (run again over the whole census on 11
October, once the tab had its own picker; the first run stopped at 300 Anno
1800 units). No Apply took half a second but four: *GUID Explorer*, one
ModOp for each of 18,057 assets, 5.3 s; *Noblesse oblige*, 3.0 s;
*Decorations*, 1.2 s; and *Ornament Mania*, 0.9 s.

The census found three faults on the way. Reading the whole text, above. A
group written in two parts, round something that stands in the middle of it,
was taken for a group kept whole and read again (the Kurila mods). And past
line 65,535 the parser gives an element a line only near the one it starts
on, so a group's tag was taken for the op under it and its ops fell out of
it (*Decorations*, a file of 256,000 lines): the ops of the old text are
read a thing at a time now, from the lines the writer said each is on.

**What an edit reaches is read again in the background.** An op typed,
switched off, moved or taken out is an op every later one may find things
differently for, and so is a value set in Fields. Until this was built
nothing was read again: the later ops of the mod and the mods after it went
on showing what they came to before, whichever view the edit was made in.

- **Each step of undo says which mods it changed, and from where in each**
  (`undo.Step.reach`): the place in the file of the first change, op or
  unread op that moved. The window hands that to `stale.edited` after every
  step it records, takes back or makes again. So there is one place that
  notices an edit, and it is the one that already knows what an edit is.
- **The mod is marked from that place on, and every mod after it.** A mod
  with no op at all is not marked: the changes the Studio writes say what
  they do whatever they find. So a value set in Fields at the end of the
  last mod, which is most edits, starts no reading.
- **A mod edited here is read from what the project writes for it**
  (`stale.written_here`), and so is the mod written here, which has no
  folder. Its folder does not have the edit. The stack is read as before,
  in one pass, with those mods' ops handed in (`reader.analyse_stack`,
  `written`).
- **Its reading is put back by the text** (`modfile.take_reading`). Every
  op of that text is an op the project holds, yours among them, and the
  writer says which. Each keeps its uid, its switch, its note and whose it
  is; what it comes to is replaced. A change the Studio wrote itself is not
  touched. An edit of yours laid over what an op comes to still wins, as
  it does when a folder is read again.
- **A reading counts the edits it answers for** (`Loadout.edits`). One
  that comes back after another edit is not taken, and the reading wanted
  now is started.
- **In a later mod, the rows marked are the ones the edit could reach.**
  Every op of every later mod said *being read again* after any edit, which
  on a full mods folder is thousands of rows for one value typed. A step of
  undo now says which assets it was about (`Step.assets`): those of the
  changes it moved, and those the ops it moved answered to. `stale.reached`
  marks the ops at one of them, every pattern, and every op that comes to
  nothing, as it does for a mod that is new. Where that cannot be said, a
  group moved or an op the Studio does not read, it is every op as before.
  It is the marking only: the reading is of the whole mod either way, and
  an op that moves unmarked still says *changed when read again*. Not
  saved: marks read back from a project file reach every op.
- **The reading starts half a second after the last edit**
  (`readings_controller.EDIT_MS`). Each edit used to start one at once,
  which writes out the text of every edited mod on the window's thread, for
  a reading the next edit stops. It is said to be running from the edit on,
  so the rows say so and Export and Play wait for it.
- **An edit draws the Mod changes panel once, and the reading that follows
  once more.** An Apply in the Mod file tab on a list of 68 mods held the
  window for four seconds, and for three more when the reading came in
  (measured 11 October 2026 on census unit 3230012, 6,532 changes; reported
  as ten and more on a player's own list). None of it was the Apply, which
  is a fifth of a second. The panel was drawn three times, and each drawing
  was slow for two reasons of its own.
  - *Drawn for the edit, then again for the marks the edit left.* The step
    is recorded before the rows are drawn now, so they are drawn with their
    marks (`MainWindow._on_edited`).
  - *Which GUIDs a mod elsewhere on this PC takes as well* was asked a GUID
    at a time against every loaded mod's folder, a path at a time: a million
    comparisons a drawing, two seconds of it. It is asked once for each
    folder (`repeats._outside`).
  - *The row of each asset a mod adds* was made again for every row that
    names one, at every grouping: 1,839 of them, each a parse of the asset
    and a look in the texts. Each is kept with what it was made from
    (`Workspace._new_row`).
  The same Apply now holds the window for one second, and for 0.7 s when
  the reading comes in. Both slow parts were in every redraw of the panel,
  so an edit in Fields on such a list is quicker by the same.
- **On a list of 417 mods an edit holds the window for a second, not two
  and a half.** Measured 12 October 2026 on the bench project (32,359
  changes), each step with nothing else running (`tools/bench.py --alone`,
  one run each way, the steps this does not touch within a few per cent of
  each other): an edit 2,097 ms to 700 ms, with the Export tab shown 2,625
  to 1,246, with the Mod file tab shown 2,291 to 915, with the longest
  mod's file shown 2,463 to 1,083. Two things, both in every drawing of
  the Mod changes panel.
  - *Whether there are several mods* was asked for every mod's row
    (`ChangesPanel._is_target`), and answered by putting every mod in load
    order and counting them (`Loadout.is_multi_mod`), with each mod's
    place found by going through the layers: 418 sorts of 417 mods, 2.3 of
    the 4.5 seconds of an edit timed with the reading running. It is
    counted, and no further than two. `origins` goes through the layers
    once.
  - *The GUID of the asset a change adds* was read out of its payload as
    XML each time the grouping asked, which is for every such change at
    every edit: 22,680 parses. It is kept for each payload
    (`effective._guid_in`).
- **What was still slow there.**
  - *A step taken while the reading runs takes about twice as long.* The
    reading is Python on a thread, so it shares the interpreter with the
    window, turn about. On this list it runs for 43 to 142 seconds after
    an edit, and the bench's default figures, which are what somebody
    working sees, had an edit at 3.2 to 3.9 seconds where it is 2.1 alone.
    That, and not a slower edit, is most of what the bench showed against
    the 2.0 seconds of Stage 4, which was measured before there was a
    reading. A thread's priority does not change whose turn it is. A
    process of its own does: see below.
  - *Play and Export wait for that reading,* by design: up to two minutes
    after an edit on this list.
  - *The first filter keystroke after an edit took five seconds, with the
    window not answering.* It makes the rows of every mod, 32,360 changes
    under 11,221 ops, to hide most of them. No one part of it is slow: it
    is the rows. It was 3.3 seconds at Stage 4, with plainer rows. See
    below: the rows are still made, and the window answers meanwhile.
- **The reading after an edit is done in a process of its own**
  (`core/reading_process.py`; the
  [proposal](proposals/separate-process-proposal.md) has what was measured
  for it). On a thread it took turns with the window for the interpreter,
  and a step taken meanwhile took about twice as long. Measured 13 October
  2026 on the bench project, medians of three rounds, in milliseconds the
  window is held:

  | | The reading on the thread | In a process | In a process, nothing else running (`--alone`) |
  |---|---|---|---|
  | Show the Export tab | 718 | 305 | 304 |
  | An edit, the Mod file tab shown | 2,600 | 1,110 | 1,107 |
  | An edit, the longest mod's file shown | 2,688 | 1,136 | 1,143 |
  | An edit, the Export tab shown | 1,555 | 1,425 | 1,387 |
  | An edit | 1,504 | 1,395 | 1,436 |
  | Open the project | 5,334 | 4,387 | 4,251 |

  With the process, a run with `--alone` and one without agree on every
  step. The plain edit moves least because no reading is running when the
  bench makes it: it is the edit that starts one. The reading itself ended
  54, 39 and 38 seconds after the round's edits, where on the thread it
  was 73, 47 and 39.
  - *A reading hands back what is taken of it and no more* (`stale.Answer`):
    each mod's ops, its changes and its unread ops. The reader's report
    holds every op as parsed and every tree built on the way, as lxml
    elements, and cannot be sent from one process to another. For all 404
    mods of this list read again the answer is 37 MB.
  - *A process for each reading, ended to stop it.* It starts in a third
    of a second, with nothing of Qt imported, and the gigabyte a reading
    of this list takes at its peak goes back to the system with it. A
    reading is stopped at once, wherever it is.
  - *Except in its search sweep, unless the window is closing.* The sweep
    of every asset for the searches not answered yet is 28 seconds on this
    list, and what it finds is kept on the index only once it is whole.
    Stopped half way at each edit, somebody who goes on editing would
    never see a reading finish. So the process says when a sweep starts
    and what it found, and a reading stopped between the two is ended when
    the answers are in. On the thread the sweep said nothing, and closing
    the window waited for it; it says where it is now
    (`reader.analyse_stack` passes `searching` on).
  - *The search answers go with the request and come back.* A reading of
    all 404 mods took 70 to 72 seconds in a process handed none, and 40 to
    41 handed the 548 the first had found: what the same reading takes in
    the window's own process on an index that has them. They are kept on
    the window's index (`AssetIndex.keep_searches`), and written beside
    its file for the next launch, keyed by `index.search_rule`.
  - *No reading runs while the index is closed, built again or written
    to* (`ReadingsController.hold`). The process has the index's file
    open, which on Windows stops it being replaced, and a sweep would hold
    it against the texts being loaded. The process opens it read-only, and
    is sent the names the loaded mods give things, which the window's
    index keeps in memory.
  - *It does not outlive the window.* Nothing is written to its standard
    input after the request, so that pipe closing means the window has
    gone, however it went, and the process ends itself.
  - *Where a process cannot be started, or dies, the reading is done on
    the thread* and the status bar says so once. Two in a row, and no more
    are tried that session. `ANNO_STUDIO_REREAD=thread` asks for the
    thread, which is how the tests about the reading run.
  - *The same answer.* Every unit of the census read again both ways, each
    from its folders and from the text the project writes, comes to the
    same in a process as here: 364 units of Anno 117 and 854 of Anno 1800.
    On the census's longest readings, 5 to 22 seconds, one there took
    from nothing to a sixth longer than the same one here with nothing
    beside it: the start and the sending.
  Not timed: any of it in a frozen build, where what a start costs may be
  another figure (section 11 of the proposal).
- **An edit holds the window for 0.8 seconds in the bench's first round
  and 1.4 to 1.6 in the second and third,** with nothing running beside
  it (`--alone`, 813, 1,600 and 1,436 ms). The earlier figures here were
  one round each. Not looked into.
- **A filter typed looks in the folded mods a few at a time.** It makes
  the rows of the mods not opened yet between the window's other work, a
  fifth of a second at a go, and filters each mod as its rows arrive
  (`ChangesPanel._start_filter`). The line under the list says how many
  mods are still to be looked in, and not that nothing matches while any
  are. Measured 12 October 2026 on the bench project, typing a filter
  after a redraw with no reading running, the same filter both ways, the
  first run of each left out as slower:
  - *The longest the window goes without answering* is 0.27 to 0.40
    seconds, where it was all of it: 3.4 to 3.5 seconds. One long mod's
    rows are made whole, which is what the longest is.
  - *All of it takes a quarter longer,* 4.1 to 4.4 seconds. The first
    match shows after about 0.8 seconds.
  - *A filter left on through an edit* shows what it showed 0.22 seconds
    after the redraw: the mods that matched last time are looked in first.
  Three things had to be got right.
  - *The view is held steady once a slice, not once a mod.* Making a mod's
    rows asks the view to take stock of every change first
    (`Workspace.steady`), 30 to 50 ms here. A mod at a time that was 407
    stock-takings, and the filter took 21 to 35 seconds in all. It is the
    quarter, and why a slice is not shorter: at a tenth of a second all of
    it took half as long again, and the longest wait was the same.
  - *Whatever acts on what the filter shows has it finish first:* every
    row asked for (`build_every_row`), and the rows to select after an
    Apply, in the mods they are in (`show_rows`). What the filter hides is
    not selected, and a mod not looked in yet is hidden.
  - *A redraw forgets the rows still to be looked under,* which go with
    the rest of the tree.
  Not done: making it take less in all. What would is not making a row to
  find out whether it matches, and a row's words are what it shows: the
  names and summaries that are most of the making.
- **The bench had stopped measuring two things.** It could not read its
  folder once the mods folder dialog read on **Read mods** and not as it
  opens, and timed an empty project. And Play waits for the reading in a
  dialog, which the bench refused like any other: Play gave up at once and
  was timed at under a millisecond. It presses the button, and lets the
  wait finish before it times Play. `--alone` waits before every step.
  A profile takes in every thread on Python 3.12 and later, the reading
  with the step, so one taken without `--alone` blames the step for the
  reading's work.
- **The first edit after a mod is switched off or on took twice as long
  as the others.** Seen 13 October 2026 as the bench's second and third
  rounds: an edit 813 ms in the first and 1,600 and 1,436 after, where
  the baselines above were one round each. Two things, found by profiling
  the call alone of the first round's edit and the second's
  (`--profile-nth`).
  - *Every asset's row was asked for again,* 10,083 queries, about a third
    of a second. The index holds each asset's row once looked up, the
    names the mods give are part of a row, and those names moving emptied
    all of them (`AssetIndex.set_mod_names`). A switch moves the names of
    the one mod, and the next drawing of the Mod changes panel paid for
    all the rest: the edit after it, since the switch itself only repaints
    the mod rows. Only the rows a moved entry names are let go now, by
    GUID or by the entry the asset's text is at. The same three rounds
    after: 801, 992 and 1,101 ms.
  - *The rest is the bench and not the Studio.* What is left of the rise,
    150 to 300 ms, is in every edit of the later rounds, whichever tab
    shows. The first mod row in the bench project is switched off, so the
    first round's "switch a mod off" does nothing (0.0 ms) and what
    follows it switches the mod on: the later rounds are of a project
    with one more mod loading.
  That showed what the project is. Of its 417 mods, 416 are switched
  off. Their folders in `mods-150` have the `-` in front that an earlier
  run's Play put there, most likely the run that timed an empty project,
  and the project was read from the folder as it stood. The one that
  loads is `[Gameplay] My Anno Mod`, which an earlier run's Play wrote
  there. Every figure in this section from 12 October on is of that
  project: rows for 32,359 changes, and next to nothing loading.
- **The bench's mods are on, and a run cannot switch them off for the
  next.** Each run works in a copy of the folder in its sandbox
  (`working_copy`), and a folder whose folders are not the ones it was
  built with is built again. "Switch a mod off" takes a mod that is on,
  and switches the same one back on. Built again on 14 October 2026: 416
  mods, all on, 32,350 changes (the 417th was the mod an earlier Play
  wrote). Three rounds with `--alone` (`mods-on-alone.json`), against the
  old project after the fix above (`rows-kept-alone.json`):
  - *An edit* 2,213 ms where it was 992, with the Export tab shown 2,098
    where it was 1,452, with the Mod file tab shown 1,836 where it was
    1,139. The rounds are within a sixth of each other: 2,092, 2,213 and
    2,390.
  - *Play* 3.7 seconds where it was 2.2, *switching a mod off* 0.63
    where it was 0.24, *opening the project* 10.3 seconds where it was
    4.2.
  - *The filter's longest hold* is where it was, 0.45 seconds, and
    showing a tab too; all of a filter takes 5.3 seconds where it took
    4.8, and clearing it 0.36 where it took 0.16.
  Every figure before this one, in this section and in the bench folder's
  older files, is of the project with its mods off. These are the ones
  to compare against.
- **A mod read again at other settings reads the mods before it the same
  way** (`stale.read_at`). That reading is the window's own, made at once
  and not in the background, and it went on reading every mod from its
  folder after the background reading had stopped: an op of the mod that
  looks at what an earlier, edited mod leaves came to what it would have
  without the edit. The mod itself is read from its folder whatever was
  done to it here. That is what was asked for, and the dialog says so
  first.

**An undo step puts back the mod it was made in, and no other.** A reading
taken used to end the undo history, as reading a folder does. Following
every edit, that would have ended it at every edit. So a reading asked for
by edits alone is taken as it is (`UndoHistory.absorb`) and the steps stay.
That needed a step to stop putting back every mod's ops: it held the whole
list, and taking one back after a reading would have put the later mods
back to what they came to before the reading, with their changes as the
reading left them. A step now puts back the ops, and the ops carried
unread, of the mods it moved them in (`Step.ops_of`, `Step.unread_of`).

- **What an op came to since goes with the op.** A step that takes an op
  out holds the changes under it as they were when it was made. Read again
  since, the op may have changes the step never saw. Those are taken out
  with it, or they would be left under no op and written as changes of
  their own.
- **Undo and redo are edits.** They mark what they reach like any other,
  so the later mods are read again after each. Between the undo and that
  reading, the later ops of the mod show what they came to with the edit
  still in place, and their rows say *being read again*. Export and Play
  wait for the reading, as they do for any.
- **A reading that follows a mod arriving, going or read from its folder
  still ends the history.** That is a reading of folders, and is undone by
  doing it again.

Measured against the census (11 October 2026), with a script that says
every mod of a unit was edited, so the whole stack is read from the text the
project writes and taken. Nothing was edited, so each mod should be the mod
it was: the same ops under the same uids, coming to the same changes, the
same ops carried unread, and the same file written. All 424 mods of the 364
Anno 117 units and all 2,043 mods of the 854 Anno 1800 units are. So a mod
read from what the Studio writes for it reads as its folder does, in every
mod there is to try it on.

The reading is as long as reading the stack was to begin with, on its
thread. The window's own part is short: writing the text of each edited
mod before the reading, and matching it to the mod after. For *GUID
Explorer*, 18,057 ModOps, that is 0.6 s and 3 s, with 13 s on the thread
between them. For the largest stack, 152 mods, all of it took 12 s.

Not measured: an edit followed by the reading, against the loader. The
tests do that for a few ops at a time, by writing the same edit into the
folders and reading those afresh. The harness census with the loader has
not been run on this.

### Where the field suggestions come from

- Verified exact: the 27 it lists for `FactoryBuilding7` are
  precisely the 27 a real factory has.
- Useful as a fallback, but not
  trusted over reality: Anno 117 declares `<PerUnitLengths>` for
  `Maintenance/Maintenances`, while every shipping entry there actually carries
  `Product` and `Amount`.

#### Telling a list from a node that has one child

A trireme's
`ShipModules/AdditionalHull` holds exactly one `<ModuleCount>`; offering to append
more of it produced duplicates of a field that has to be unique, and made *Add list
entry* and *Add field* two routes to the same result.

Assuming the obvious name would have been wrong for one of the two
games we support, which is the whole argument for reading it off the data.

### Deciding what is a link

The obvious approach is to judge each value: does the field's name mention "guid", is the
number big enough, does it resolve? That is enough to annotate one row, and it is what
the Studio did at first. It cannot carry a graph, because **roughly half of all plausible
integers in Anno 1800 are a valid GUID by coincidence** — there are 96,102 of them.

What works is judging the *field*, not the value: how often does this exact path resolve,
measured across every asset in the shipped file? That number turns out to be sharply
bimodal — a field either essentially always resolves or essentially never does:

| resolution rate per field | Anno 117 | Anno 1800 |
|---|---|---|
| always (100%) | 2,844 fields | 7,206 fields |
| never (0%) | 5,982 fields | 5,578 fields |
| ambiguous (5–95%) | 5% of values | 30% of values |

Checked against both shipped
games, that accepts every reference field tried — `Product`, `Ingredient`, `ItemLink`,
`ProvidedNeed`, `Good`, every `*GUID` — and **no** quantity field, including the ones
that defeat a name-based guess entirely:

| field | values | resolve | verdict |
|---|---|---|---|
| `DurationLanguageArray/*/DurationMaximum` | 79,442 | 67% | not a link — milliseconds |
| `Text/LineID` | 53,892 | 31% | not a link |
| `VectorElement/InheritedIndex` | 24,573 | 6% | not a link |
| `Cost/Costs/Item/Amount` | 11,994 | 41% | not a link |
| `FactoryInputs/Item/Product` | 5,233 | 100% | a link |

"No quantity field" was wrong, and a walk through the running Studio found it: every
building's `BuildModeRandomRotation` "referred to" a character called Visitor. The rate
counts how many times a field resolves, and a field that holds the same two numbers 757
times has been right twice. The rotation is 90 or 180 degrees; Anno 1800 has an asset
with each of those GUIDs, as it has for 72% of all three-digit numbers; so the field
resolves 756 times in 757.

What tells such a field from a link is in the same file. 90 and 180 are also values of
fields that are plainly not links, the ones under 25%, and 1010196 is a value of none of
them. So a field also has to be *telling*: at most half of the different GUIDs it holds
may be numbers the game uses as plain numbers elsewhere. Different ones and not how
often, which is the count the rate lacked. Where a field alone is not telling, the same
tag taken across the whole game may vouch for it: `Profile` under one notification holds
only 220, a plain number, and `Profile` across the game holds 103 different assets.

| of the fields the rate called links | Anno 117 | Anno 1800 |
|---|---|---|
| called links by the rate | 1,011 | 2,044 |
| not telling, so no longer links | 2 | 79 |
| of those, quantities | 2 | 78 |
| kept because the tag vouches | 2 | 5 |

The link lost is `Monument/PhaseCompletedNotification`: 14 ruins naming the notification
whose GUID is 18. Five fields of numbers are known to be kept still, 44 values in Anno
1800, because no plainly-not-a-link field happens to use their numbers: a
`NotificationPriority` of 1001 and one of 550, `CellSizeX`, `ShipSketchPosY` and
`UplayAction/BadgeID`. A field that is not telling is not offered as an uncertain link
either, which takes 769 more fields of quantities out of Anno 1800's uncertain view and
32 out of Anno 117's.

Both games' graphs are built during the same pass that indexes the assets: every integer
leaf is recorded as a candidate (whether it is a GUID cannot be known until every GUID has
been seen), then each field is scored once and the rows rebuilt keeping only what
survives. Scoring costs about three seconds of the sixteen Anno 1800's 289 MB file takes,
and both directions of a lookup come back in well under a millisecond.

### Links that do nothing

Whether that is meaningful is not knowable from the file: a missing `Amount` means zero,
but a missing `Probability` beside an asset-pool entry means *normal*, and nothing says
which default applies where. What *can* be measured is whether a path uses values at all:

| path | entries that set a value | a bare entry there means |
|---|---|---|
| `Cost/Costs/Item` | 53% | nothing is spent — a placeholder |
| `AssetPool/AssetList/Item` | 15% | membership, which is the whole point |
| `RewardPool/ItemsPool/Item` | 9% | likewise |
| `ConstructionCategory/BuildingList/Item` | 0% | a plain list of pointers |
| `FactoryBase/FactoryInputs/Item` | 100% | (never happens) |

That flags 5% of links in Anno 117 and leaves pointer lists alone.

Without that, a new factory hand-written with a minimal payload drew as an asset
connected to nothing at all.

### Creating a new asset

This replaces the old default of counting up from `1337000000`, which was neither
anybody's range nor inside the safe span at all.

This is the one part of GUID collision that is actually solvable locally:
a mod on someone else's machine is unreachable no matter what, but two of *your own*
mods picking the same "suggested" range is a real, avoidable failure mode, and it is
far cheaper to catch when you type the GUID than after a mod is built around it.

#### A copy that follows its original, and one that does not

A copy made with `BaseAssetGUID` inherits everything and lists only what differs, so it
is small and goes on following its original when a patch changes it. That is the
default because it is what the game's own assets do (556 of them in Anno 1800, 906 in
Anno 117). It is wrong for an asset that must not move when its original does, so
*Copy its values once* writes the original whole instead, with the new GUID and name in
place of its own.

What is copied is the asset as the editor shows it: the game's, with the mods that load
before the mod being edited and that mod's own changes. Copying the shipped asset would
have dropped edits that are on the screen. A copy of an asset that itself inherits keeps
its `BaseAssetGUID`, since that is what the original says. In Anno 117 an in-game name
points the copy's `OasisId` at its own GUID; without one it keeps the original's, and so
the original's name.

#### Its name in game

Using the asset's own GUID as its LineId is what the loader's docs advise, and what
6,007 of the assets Anno 117 mods on mod.io add do.

## Why one ModOp per change

Hand-written mods usually bundle all of an asset's edits into one big `replace`, because
typing them out individually is tedious. Generated output has no such excuse, so each
change gets its own narrowly-targeted ModOp:

```xml
<ModOp Type="replace" GUID="1010340" Path="/Values/Factory7/CycleTime">
  <CycleTime>15.00</CycleTime>
</ModOp>
<ModOp Type="replace" GUID="1010340" Path="/Values/Maintenance/Maintenances/Item[Product='1010365']/Amount">
  <Amount>9</Amount>
</ModOp>
```

A wide `replace` on `/Values/Factory7` would overwrite every other field in that node, so
any other mod touching the same node loses. Narrow ModOps only claim the values you
actually changed, and two mods editing different fields of the same asset stack cleanly.

### A field that is not there is made sure of, not added

Which op a change is written as follows from what happened to the node: a value that
is there is a `replace`, a list entry is an `add`, a node dropped is a `remove`. A
field the asset does not spell out -- most assets inherit most of theirs -- was an
`add` too, and that one was wrong:

```xml
<ModOp Type="merge" GUID="1010340" Path="/Values/ItemContainer">
  <SlotCount>3</SlotCount>
</ModOp>
```

An `add` appends. Two mods that each add `<SlotCount>` leave the asset with two. A
`merge` of the one field makes it where it is absent and sets it where another mod
made it first, which is what both authors meant. It is as narrow as the `add` was: it
names one field and claims nothing beside it.

This is also what a merge is mostly for. Of the changes the merges in the mods on
mod.io come to, 87% in Anno 1800 and 74% in Anno 117 make a field that is not there,
and a replace of one that is makes up most of the rest. Written back as `add`, 6,914
merge ops in 174 of the 854 Anno 1800 mods said less than their authors had.

So a change with the op `merge` is an *ensure*: aimed at the node the field belongs
in, carrying that field. Three things follow.

- **Only where the field is the one of its name.** A merge pairs same-named children
  by position, so a list entry stays an `add`, and so does a field named like the node
  it goes into, which a merge would read as naming its target twice.
- **Two mods making sure of one field are a clash**, as two replaces of it are, and so
  are an ensure and a later replace. Two additions never were, because nobody could
  tell them from two entries in a list.
- **The asset as it will be holds one field**, set by whichever mod came last, and an
  edit to it lands in that mod's payload. The row says *set by*, not *added by*: the
  field is there because of the mod that made it, and what the later mod owns is the
  value.

**Read in a stack, it is aimed at what the game ships.** A mod's merge that finds the
field there because an earlier mod made it used to be read as a `replace`, written at
a path only that other mod's work gives the asset. Exported, the mod did nothing
without the other one, where its author's merge makes the field. So it stays a merge,
and where the field sits inside a section another mod made, the merge is aimed at the
nearest node above it that the game ships and carries the sections on the way down:

```xml
<ModOp Type="merge" GUID="1010340" Path="/Values">
  <ItemContainer>
    <SlotCount>5</SlotCount>
  </ItemContainer>
</ModOp>
```

That is the same op with the other mod loaded or without it. It is not done where a
merge would not land on those nodes: one of them has a twin of its name, or nothing
above is the game's, as in an asset a mod made. There the path into the other mod's
work stays. A mod's own `replace` of such a field stays a `replace` too, since in its
own file it needs the field there just the same.

**A value two mods both set is a change of each.** A mod is read against the document
the mods before it leave, and a field that already reads as wanted takes no change.
That is right when the game's own file reads that way. When only another mod's work
does, it made what a mod was written back as depend on what was loaded beside it: the
second of two mods setting a radius to 25 came back without that op, and on its own
no longer set it. So such a value is the second mod's change as well, written against
the game's file -- a `replace` of the shipped value, or for a field the other mod made
the merge that makes it -- and the two are listed as agreeing.

Where the mod's own op is a `replace` of a field the game does not ship, there is no
shipped value for it to have replaced: it restates the other mod's. It is carried as
the `replace` it is, marked as restating, because the model otherwise takes a replace
whose two values match for an edit typed back to its original and drops it. The same
goes for a merge that cannot be aimed at what the game ships.

This holds for values. A list entry two mods both add, or both remove, is still read
in the stack: the second mod's op finds the document the first one left.

A merge is not the op for everything. It cannot remove, it cannot say where in a list
an entry goes, and among entries of one name it goes by position, not by what they
hold. A value that is there stays a `replace`, which says what it expected to find
and is checked against it.

### Narrow in what it writes, as broad as it means in whom it targets

That principle is about one axis of a ModOp and not the other, and the distinction is
the Studio's philosophy in one line. A ModOp has a **payload breadth** — one field, or
a whole node retyped — and a **target breadth** — one GUID, or every asset matching a
shape. Narrowing earned its keep on the first: the lazy replace that deletes fields it
never meant to, or claims a node so no other mod can touch it. It has nothing to say
against the second. *Every mine slot building produces an extra Obsidian* is a coherent
thing for a mod to mean, and it means it about a Cobalt mine some other mod adds two
months from now. A merge of one field aimed at every mine stacks with other mods
exactly as well as the same merge aimed at one GUID.

So a mod's [rules](#rules-an-edit-aimed-at-a-shape-rather-than-at-a-list-of-assets)
are first-class. A rule is the fact and its changes are today's answer, never the
other way round: the op goes out as itself while it is on, and the changes underneath
are what it comes to against the assets loaded now — shown for reading and for the
conflicts they take part in, not switched off one at a time. "All of these, except
that one" is the rule followed by an op that puts the one back, which is how the game
reads a mod's file too.

The same posture runs through everything the import does. The Studio's reading of a
mod is a reimplementation of the game's patcher that can explain itself: conditions
answered against the document as it stands when the op runs, settings substituted,
copies resolved, searches re-asked against assets other mods invent, load order from
the loader's own rules. That reading is the product. Narrowing is one output
transform applied where it loses nothing, and where the reading cannot follow an op
the op goes out as written and the list says so, rather than the mod losing it.

### Path predicates

Across the whole shipped file this
takes positional paths from 38% of repeated entries to 24% in Anno 117, and from 24% to
17% in Anno 1800. And `InheritedIndex` is a key rather than a position in disguise: it
held its value for 4,984 of 4,985 unchanged entries between Anno 1800's launch archive
and its current one.

## Importing someone else's mod

### Removals a wide replace only implied

Whether a bare entry is a placeholder or the whole point is not a matter of taste; it
is measured, by the same reading [nominal links](#links-that-do-nothing) already use:

| list | entries that set a value | so a bare entry is |
|---|---|---|
| `Cost/Costs/Item` | 53% (Anno 117), 36% (1800) | a placeholder — removing it is inert |
| `Maintenance/Maintenances/Item` | 90% | a placeholder |
| `ProductList/List/Item` | 0% | the entry itself — removing it is real |
| `Effect/Targets/Item` | 0% | the entry itself |
| `AssetPool/AssetList/Item` | 15% | the entry itself |

### Ops that say what they are for rather than naming it

Every search in the batch is answered in one sweep, because
reading the file is nearly all of the cost and each extra expression is nearly free:
**87 distinct expressions across 65 Anno 1800 mods, over 96,102 assets, in 4 seconds.**

Two things keep that quick. An asset is only parsed if its text contains every element
name the path's *steps* mention — read once per asset as a set, rather than scanned for
once per expression, which is the difference between four seconds and a minute. And a
predicate comparing against a literal can only hold if that literal is written down
somewhere in the asset, so `[../Template='RepairCrane']` rules out all but a handful
before anything is parsed. Both are one-sided: they can only ever admit an asset that
turns out not to match, never exclude one that does. A path that tests for something
being *absent* drops the second test entirely, since there a literal missing is the
whole point.

This is what most of the previously unreadable ModOps were. Six Anno 117 mods used to
come in empty — *Increased Trader Storage*, *Custom Walls and Gates*, *Easy Seed
Search*, *Intro removed*, *Efficient Celtic Charcoal*, *Efficient Eel Grabbers* — and
all six now read.

A mod that gives every item building a socket really does mean a thousand
assets, and a thousand rows tucked inside that mod's own group in the list is bloat
rather than a problem; the ceiling is only there so a runaway search cannot produce a
list nothing can draw.

### An add of assets to the list that holds one

`<ModOp Type="add" Path="//Assets[Asset/Values/Standard/GUID='130248']">` with
`<Asset>`s in it is the commonest way an Anno 1800 mod adds assets without
naming a GUID to put them beside: 618 ModOps in 94 units of the census,
*Charcoal Power Plant* and *Commuter pier* among them. Its path finds no
asset. It finds the list one stands in.

Read as a search for assets it answered to none, so the op's row said
"Asset", "0 assets" under the mark of a pattern, the assets it adds were
unknown to the Studio, and every later op aimed at one of them read as aimed
at nothing. The loader adds each: in the census the Studio's reading and the
loader's differed by exactly those assets.

It is read as the assets it adds (`Translator._adds_to_a_list`), the way a
block of assets under `/AssetList/Groups[last()]` is, and kept as written
for the same reason: the Studio's own new assets go in beside one that is
named. Not where an asset it holds has a GUID something already has, which
is left to the game as it is for a block; nor where it holds anything that
is no asset. Its row names what it adds and is no pattern's
(`Op.searches`, which a row now goes by and not by the op having no GUID).

### Two forms that look like nothing else

Reading the shipping mods turned up two shapes worth naming, because both parse
perfectly and mean something other than they appear to.

**The op name holds the path and the GUID stands alone.** `<ModOp GUID="5977"
Merge="EffectSource">` mixes the two dialects: the shorthand attribute carries the op
and the path, and the asset is a separate `GUID=`. Read as pure shorthand it is a
search for `EffectSource` anywhere in the game; read properly it is one building's
effect radius.

**A merge may name its target twice** — once in the path and once as the payload's root:

```xml
<ModOp GUID="5977" Merge="EffectSource">
  <EffectSource><RadiusDistance>16</RadiusDistance></EffectSource>
</ModOp>
```

Read literally that puts an `<EffectSource>` *inside* `<EffectSource>`. What it means is
that the payload is what the target should look like: the loader's "legacy merge", one
payload node named like its target (in any case in Anno 117). The loader takes it so
whatever the target holds (117 L1276–1281, 1800 L651–655), and so does the Studio. It
used to keep the literal reading where the target already has a child of that name, as
some shipped structures do (Anno 117 has 1,887 `<Objective>`s inside an `<Objective>`);
the loader does not, and `test_modio_loader.TestTheLoadersOwnApply` puts the two side
by side.

#### A list of GUIDs is split on the comma and nothing else

Measured for both games, with the space
before the comma, after it, and around a GUID standing on its own — every one of
them misses.

Reading it the way it is written rather than the way it is meant is the whole
point: the Studio used to trim, which read the mod as doing more than it does and
wrote an export that did it. Three mods on
mod.io write a GUID that way.

### Rules: an edit aimed at a shape rather than at a list of assets

On the
real Anno 117 folder, installing a wall mod that sorts before *Custom Walls and
Gates* moves three of its rules; putting them to the new wall takes 11 ms and
produces the ten changes the original mod would have made in game.

Across both real mods folders that was 498 rules
standing in for 1,446 narrow ModOps while a rule could still be held back; now
every rule that is on goes out as itself.

#### A rule goes back where its op was

Writing every search at the front of the file is tidy and wrong. A search that
ran *after* a named op of the same mod saw that op's work and did less because
of it — *-One Free Reroll* names nine traders and then says "every trader" —
so hoisting it to the top makes the edit twice, and for an addition twice means
two of the node. Left where the mod put it, the question does not arise.

That is why a change remembers which op it came from, and why the generated file
is grouped by op and then by asset rather than by asset alone: collapsing an
asset's changes into one block put a rule on the wrong side of half of them.
With a mod you are writing yourself every change came from nowhere in
particular, so the grouping is exactly what it always was.

Keeping the order is the single biggest thing here. On the mods installed here
it is the difference between **47** searches surviving as searches and **67**.

#### The rule is the fact; its changes are today's answer

That is a reversal of how it worked at first, and the old way is worth
recording because it was principled and wrong. A rule was treated as a cache of
narrow ModOps: it went out as itself only while every one of its answers was
still ticked, nothing had been reverted, the equivalence check had not thrown
its op out, and no other op's payload carried its work. Trip any of those and
it collapsed into one ModOp per asset. Each condition kept the change list
honest, and each did it by making the mod do less in game than the one that
was opened — a rule "narrowed" for an asset the author had unticked stopped
covering every building a later mod adds. Six rules in the two real folders
were stuck that way, four of them in *Item Sockets*, where the mod adds a
socket container to every building of a shape and then retunes a handful by
name. That is "all of these, except those", and it is now written exactly so:
the rule, then the named ops after it.

What each of those conditions became:

#### All of these, except this one

Sorting them alphabetically
silently changed what *Increased Trader Storage* does.

The measure of all of this is a round trip: read each real mod, write it back
out, read it again, and compare, against a baseline with the rules stripped so
the only variable is the export. Every mod in both folders comes back identical
either way, and the replay of ordinary ops around a rule is what keeps that
true — about 1.4 s on a 64-mod folder, and only for the assets an ordinary op
also reaches; where a rule is the only thing that touched an asset, writing it
where its op sat *is* the mod's own order and there is nothing to check.

#### Noticing when the answer has moved

That is
also what makes it quick — 1.3 s for the whole Anno 1800 folder, and five
milliseconds for the narrower question a single import asks.

#### The pattern editor is the op, and the rows it will have

A pattern was written in four boxes: search, kind, field, value. They held less
than a ModOp can say, so the dialog recomposed each op it opened from its own
boxes and checked the answer against what it was handed, and an op that came
back different was shown as written with only a Close button. That was most
ops of real mods: a condition, a second field, a sibling add. And the one hard
part of a pattern, its XPath, was typed raw into the first box all the same.

The dialog is now the op itself, typed in the pane the XML view types ModOps
in and read by the reader that view reads them with (`typedops.read_one`).
Under it are the rows the op will have in the Mod changes panel, drawn by the
code that draws them there (`ui/change_rows.py`), from a project that holds
that op and nothing else. Three things are the dialog's own: it holds one
ModOp and a search, since anything else is some asset's and typed in that
asset's XML view; its rows have no boxes and open nothing, since they are not
in the project yet; and a row's conflict with another mod is not marked, since
that is worked out over the whole project.

The rows are drawn on asking, not as the op is typed. Drawn live they went at
every keystroke that left the op unfinished, which is when somebody changing an
op is reading what the last one came to. Whether the text is XML is still said
as it is typed, since that needs no look at the game's files. OK is for the op
the rows are of: text typed since has to be previewed before it is saved.

A search inside a group is read on its own, as though its group ran, and where
the group does not run the rows say so. Saved, it keeps its place in the group
and is stored as the reader leaves any op of a group that does not run: the op
it is, with nothing under it. A group read again gets back the uid it had here
(`Loadout.as_before`), so the op goes on naming its group.

An op the dialog does not change is still shown as written, with the rows the
project has of it: a group, or an op that names its assets.

### Mods that have settings of their own

#### Anno 117: options its loader reads

Read against the shipped file alone that is unanswerable, which is why every op
in *Custom Walls and Gates* used to come in as "a condition I cannot check".

It used to be read at its defaults whatever the game was running, which
made the changes list — and the fourth card's count of what loads — describe a
game nobody was playing.

#### Anno 1800: iModYourAnno's tweaks

The machine this was written on has twenty-eight mod
folders with those knobs, and about a hundred of them set away from the author's
defaults — thirty-nine in *Item Overhaul* alone.

The export behind that is a port of iModYourAnno's
own, and on the mods it was checked against it writes the same thing, file for
file.

A mod's update is the author's fresh folder, which knows
nothing of the player's answers — iModYourAnno puts them back from its own
record, and until now the Studio simply lost them: the game went back to the
author's defaults with nothing said.

Read as that and nothing more, it was one row of a thousand
changes that hid the very things the options dialog names. A group switched off inside the one
that goes out whole used to be a rule of its own as well, and was written out
twice; switched on, iModYourAnno would have run it twice.

### Values a mod works out while patching

73 ModValues
in the Anno 117 mods on mod.io use `div`, and every one of them was unreadable
until the Studio learned the word. The *spelling of the answer* is the loader's
too, measured: `10 div 3` goes into the game's file as `3.333333333333333`, and
a value written back with the question still in it has to come to the same
string when the game works it out again.

A `ModValue` is read as Anno 117 itself takes it. The loader's docs work one
out under `Merge` and show it alone in its field, and the two Anno 117 builds
of xmltest part over nearly everything else: xmltest2, which the extension
itself runs, leaves a mark outside a merge in the file as written, and
xmltest3, which the Studio runs, works it out. For a day the Studio read no
such op, and none with text beside a mark, because one build's answer was not
to be taken over the other's. Then the game was asked. On 10 Oct 2026, in two
runs, mods of marker ops put 49 questions to Anno 117 about what a field holds
after an op with a mark in it: a marker is an op that matches nothing and
carries a `Condition`, and the loader logs each one whose condition holds
under the mod that ran it. The mods, the game's two logs, the readings of them
and what each build had predicted are kept outside the repository, in
`modvalue-test` (`cases.py` and `round2.py` say what was
asked, `game-result.txt` and `game-result-round2.txt` what the game answered,
`predicted.txt` and `predicted-round2.txt` what the builds said beforehand).
The game did what xmltest3 does on every one of the 49. Where xmltest2 and the
Anno 1800 build differ from it, they differ from the game.

What the game does, and the Studio now reads:

- Merged into a field the game has, the mark is the field's value: `2+3`
  gives 5, `self::node() * 2` twice what the field held, a copy from another
  asset what it copies and an option what it is set to, in an element of the
  op's body or straight inside the op. Text of the mod's own before the mark
  is dropped, which is what the shipped files' `0` placeholder relies on, and
  text after it is what the field ends up holding in place of the value:
  `1<ModValue Insert="2+3"/>7` leaves `7`, the mark with `7` after it leaves
  `7`, and the mark with `1` before it leaves `5`. The Studio had read the
  first as `15`.
- Under a replace of a field, an add, `addNextSibling`, `addPrevSibling` and
  their short forms, a mark alone in its field is worked out as well. So it is
  in an asset added whole, asked both in the file that adds it and from a mod
  loaded after it: 5 for `2+3`, the option, three times the option for
  `3 * $opt` on a line of its own, and the copy. That is the shape of 433 ops
  in seven Anno 117 mods on mod.io, new assets whose values come from the
  mod's options, and of three ops in an eighth that replace a section.

What the game leaves, which the Studio does not read, each with its reason:

- `self::node()` in a field the op puts into the file. There is no value there
  yet for it to be, and the game writes the text `NaN`. The Studio had read
  20, twice the game's own value. It could show `NaN` as the value, since that
  is what the field holds. It does not, because a field reading `NaN` is
  nothing an author means, and the reason says so where a value would not.
- A mark straight inside a replace, with no element around it. The field is
  gone afterwards. xmltest3 leaves the value as text loose in the section.
- Text beside the mark in a field the op puts in.
  `1<ModValue Insert="2+3"/>7` under a replace and in a new asset is left
  holding `17` with the mark still in it.
- Every mark after an empty element in an asset added whole. With
  `<Hollow />` among the asset's sections, before the one holding the marks,
  each mark is left in the file as written and its field has no value. The
  same asset with the empty element after the marks has its values. The first
  run's assets each began with one, which is why that run found no mark in a
  new asset worked out at all.

The game was asked about that empty element in one place. Where the line runs
was measured with xmltest3, since it had matched the game throughout
(`TestTheRealLoader` pins each case). The loader goes through what an op puts
in level by level, and at each level it stops at the first thing with nothing
inside it that has something after it. That is an empty element, written
`<X />` or `<X></X>`, with attributes or holding only layout or a comment, and
it is text, the `1` before a mark included. What follows at that level, and
everything inside what follows, is not looked at. One level up it goes on. So
an empty element inside an earlier section stops nothing outside that section,
one that is the last at its level stops nothing at all, and a mark before it
is worked out where one after it is not. An empty element in another op of the
file, in another asset of the same op, or beside the element holding the mark
straight inside the op is nothing to it. A field added with a path follows the
same line inside the element added, and so does what a merge puts in that the
game does not have: a merge starts on a section new to the game as an add
does, and a mark in a field the game has is merged whatever stands before it.
The Studio reads a value where the game or xmltest3 gives one and no op with a
mark behind that line.

None of the 695 ops on mod.io that work a value out is behind it. The seven
mods' assets carry their empty sections after the marks, or as the last thing
in an earlier section, so all 433 are read again, and the three replaces.

Not asked of the game, and still not read: flags (`Merge=` and `Remove=` in a
flags field) in an op that is not a merge, where xmltest2 leaves them and
xmltest3 merges `Merge` into nothing and leaves `Remove`. `Insert` naming a
field with no `/text()` under a merge, where both builds leave the field
holding the copied element and no value. A copy from beside where the value
lands, or of a whole node, in an op that is not a merge: xmltest3 looks for
the first from where the mark stands in what the op puts in, which is not
where the Studio looks. And text only after the mark in a field the op puts
in, which xmltest3 drops, giving the value. No mod on mod.io writes any of
them. A `<ModOpContent/>` is another matter from all of this: every build puts
the text before it, the copy and the text after it together (`147`), and it is
read so.

A `<ModOpContent/>` standing straight inside its op, not inside an element of
the op's body, was read as the addition of an element called `ModOpContent`.
The loader's docs show the mark inside an element only. Measured with all
three builds (10 Oct 2026, 73 ops over every op type that takes `Content=`),
they agree on every one. An `add` whose `Content` ends in `/text()` appends
the text before the mark, the copy and the text after it to what the field
says, once for each node copied, and that is read: it is the docs' adding of
text to a field, with the copy where the mark says. *Enable DLC11 Story*
writes `&lt;br/&gt;(<ModOpContent />)` so in its texts, eleven ops, the only
ones on mod.io. Everything else the builds agree on comes to text left loose:
mixed with a copied node inside a field, among the fields beside the target
under `addNextSibling` and `addPrevSibling`, in the target's place under a
`replace` (with the text after the mark first), after a section's fields
under an `add` to a section, and a second mark is left in the file. None of
that is in the docs or anything the Studio could show, so those ops are
carried as written and not read. A mark alone that stands for whole nodes
(thirteen ops in four mods) is read as it was, as an op with no body is, and
an op with elements of its own beside the mark is read by those.

No mod on mod.io uses
them yet; the loader learned them in November 2025.

Nothing on mod.io copies this way yet either.

Of
the 43 configurable Anno 117 mods on mod.io, an export read settled keeps 600
of the 1,434 places their ops read a setting, and for 13 of them none — which is
why a mod that stays itself is read unsettled unless you ask: the values are
still read and shown, and both kinds of op go back out as written. It used to be
on everywhere.

Unsettled ops used to be counted as **unread** instead, and the reason is worth
keeping: a `ModValue` read literally put an element where the text was, which is
not a shape a narrow ModOp has, so the field-by-field diff fell through to its
fallback and swapped the *whole enclosing section* — reading as the mod deleting
and retyping a section it never touched. `Increased Trader Storage` came in as
172 rows of that. Refusing the op was honest, but it also meant the editor showed
the shipped value for a field the mod changes, and the export dropped the op.
Reading it at today's value and keeping the op is right on both counts.

### Ops kept in other files

*Charcoal Power Plant* is seven of those and no ops at all, and **eighteen** of
the mods installed here are written that way — the `shared-pools-and-definitions`
convention especially. Read literally, most of a mods folder does very little.

**A file stays a file** ([file tab proposal](proposals/file-tab-proposal.md),
phase 4, the first part). An export used to write every op into the mod's
one `assets.xml`: the same ops in the same order, and a mod its author kept
in 20 files came back in one. 195 of the census's units have an `<Include>`,
3,180 lines of them.

- **Each op says which file it is in** (`Op.file`, `Unread.file`): its path
  from the top of the mod, and "" for the patch. The reader has always known
  it while reading (`SourceOp.where`) and did not keep it.
- **The line is kept as a thing of its own** (`Op.includes`), in its place
  among the ops of the file it stands in. It is an `Op` and not a list of
  its own beside them: an op is already saved, undone, read again, numbered
  and moved along the file to make room, and a new list would have needed
  each of those written for it. It is marked as a group's tag is
  (`Op.scope`), which every count of ModOps already leaves out, and it
  holds nothing: the ops of the file are the ones whose `file` it is. A
  file as a group round its ops was the other way to do it, and would have
  made every op of *Celtic Independence Overhaul* an op in a group to
  everything that asks.
- **The files are written only for an export** (`generator.written(files=True)`,
  `build.as_files`). The XML view and the loader check have the mod as one
  text with no `<Include>` line in it. The Mod file tab and the background
  reading have one text with the lines in it: see below.
- **Written apart only where that runs the same** (`generator._laid_in_files`).
  The game reads a file where its line stands, so the two agree only while
  each file's ops stand together straight after its line. The writer walks
  the mod in order and checks it; where it does not hold, the mod goes in
  one file as it always did. Nothing is written that would run in another
  order than the project holds.
- **A change of the Studio's own has no file.** It is written where it
  stands: in a file where it is between two of the file's ops, or is the
  exception to one. After a file's last op it is in the file above, and a
  value set in Fields, which goes after every op of the mod, is in the
  patch. Without that rule every edit to *Charcoal Power Plant* would land
  at the end of its seventh file.
- **Three kinds of file are still written into the file that names them**
  (`modops._as_a_file`): one on a condition, one inside a group, one in
  another mod's folder. A change of the Studio's own among the ops of a
  group is written with the group closed and opened again round it, and a
  file cannot be written in two parts. Another mod's file is not this
  mod's to write. Twelve of the 3,180 lines have a condition.
- **The line is written as its mod wrote it** where that still reaches the
  file, and from the top of the mod where it does not: the patch is written
  at the game's own path, whatever the mod called its own.
- **A new op typed in the XML view is in the file of the op it follows**
  (`Loadout.file_new_things`). That view shows one asset's ops and no
  files.
- **Taking a line out leaves its ops where they stand**, in the file above.
  That is the first of the proposal's two answers for a deleted line, and
  the one that changes nothing the game does.

**The Mod file tab shows the files as regions of one text** (the second
part of that phase).

- **One text, in the order the game runs it** (`generator.written(files=INLINE)`,
  `modfile.shown`). Each `<Include>` line that names a kept file is in the
  text and the file's ops follow it. That is the order the ops already had
  in the tab; the lines are what is new, and with them which lines are
  which file's (`Shown.regions`).
- **Nothing in the text marks a region.** No comment, no made-up element:
  the text holds the line the game reads, and the ops. The pane holds each
  region by three cursors (`FilePane._hold`): on the line that names the
  file, at the start of its first line, and on the line *after* its last.
  - The end is held by the line after it and not by the end of the last
    line. Held by the end of the last line, text put in over a selection
    that crossed the end became the file's, all of it. Held by the line
    after, a line typed at the end of the file's last line is before the
    cursor and is the file's, and anything typed from the next line on is
    not.
  - Every region has a line after it: the text ends with `</ModOps>`.
  - The first line of a region is a blank line the writer adds. The
    heading is drawn on it, and an empty file has it to type on.
- **Where the cursors cannot say, the two texts do** (`modfile.regions_in`).
  Select all and paste puts every cursor in one place. The regions are then
  found by laying the text as it was shown beside the text now, line for
  line: a region goes with the lines the new text still has, and its line
  has to be one of them. The same is used where nobody held the text while
  it was typed in, which is every caller but the pane.
- **Apply cuts the text at the regions' edges and reads each file on its
  own** (`modfile._read`), as the game will. An element opened in one file
  and closed in another is unclosed in the one, so the text is refused
  there before anything is read, and never reaches an export.
  - Which region a line's is, is told to the parser by a mark written into
    the line in the copy that is parsed (`modops.REGION`). By line number
    was the other way, and the parser's line numbers stop being true past
    line 65,535.
  - A line with a region is kept as the line and its file is not read from
    the mod's folder (`modops._line_alone`): the ops are in the text.
  - A line with no region is one typed here. Its file is read from the
    folder where the folder has it, and is a new file where not. Unless it
    names a file the mod already keeps: that file's ops are in the text
    somewhere, and read from the folder as well they would be there twice.
- **Which file an op is in is where its lines are.** An op the text still
  has keeps its uid and is not read again, wherever it was pasted; its
  `file` is set from the region it is in now.
- **A line on a condition or in a group cannot have a region.** Such a file
  is still written into the file that names it, with a change of the
  Studio's own inside it written with the group closed round it. Apply
  refuses and says to type the ops into a group.
- **Folding hides the lines and takes nothing out of the text.** Apply reads
  them, find goes into them, and going to a line in a folded file unfolds
  it. Which files are folded is kept by name while the mod has them.
- **The background reading reads the same text** (`modfile.ops_of_text`).
  The text it is handed says which lines are which file's (`modfile.Text`),
  since the text alone does not.

**A file's layout is kept** (the last part of that phase): the comments that
are no op's note, and the blank lines.

- **A comment that is no op's note is a thing of its own in its place**
  (`Op.aside`), as the `<Include>` line is, and for the same reason: an op
  is already saved, undone, numbered, moved along the file, and known by its
  text when a file is typed in. Its `source` is the comment as written,
  lines and all. It is marked as a group's tag is and left out of the list
  every count and every row is made from (`keptops.of`), so it is text in a
  file and nothing anywhere else.
- **The note is the comment directly above** (`modops._note_above`). It used
  to be the nearest comment above with no element between, blank lines or
  not. With a blank line between, a comment is a heading over a run of ops
  or a line somebody left for themselves, and written back as
  `<!-- note: … -->` straight above one op it said something its author
  had not. Now that such a comment has somewhere to be kept, it is kept.
  - A note is still written under the Studio's mark and on one line. That
    is what a note is in the Studio, typed in a field; a comment kept where
    it stands is written as it was.
- **Blank lines are a number on each thing** (`Op.gap`, `Unread.gap`): how
  many stood before it, or before its note. Not kept as text of their own,
  since they say nothing but how far apart two things are. Up to eight. One
  before anything the Studio writes itself.
  - Not kept: the blank lines before a group's closing tag and before the
    end of the file, and how an op's own lines are indented.
  - A few ops shown on their own (the XML view's ModOps pane, *Show XML*)
    are no file, and have one blank line before each: the pane draws what
    it says over an op on that line.
- **Only where a mod's file is read as a file** (`parse_ops(asides=True)`):
  a mod's folder, and the Mod file tab. ModOps typed for one asset in the
  XML view keep no comment as a thing of its own, as before.
- **Not inside a group that goes back out whole**, whose text holds its
  comments already.
- **In the file tab a blank line typed is a change**, and is said as one. It
  is not a rearrangement: every op is where it was.
- **A file's first blank line in the tab is the heading's**, and the reader
  starts where something is written, so the blank lines before a file's
  first op are counted from the text of its region.

### Ops a mod ships switched off

Reading those as things the mod does meant showing changes the game never makes. `ModOpID`, which names an op for
those tools, is read for the same reason: so it stops being reported as an
attribute the Studio ignored.

Between them, these two took the Anno 1800 folder from 1,170 ops in 65 files to
**4,560 ops in 465 files**, and from 4,853 changes to **7,465**.

### What is still not read

None of Anno 117's 104 ModOps, and 244 of Anno 1800's 4,560. Each is refused with
its reason rather than quietly dropped:

| | | example |
|---|---|---|
| 83 | **a path that matches nothing here** — usually an asset another mod adds, and one you have not installed. One carrying `AllowNoMatch` is no longer here: its author said matching nothing is fine, and the loader keeps quiet about it too, so it is kept as written with that as the reason — with other mods it may match. The loader asks only whether the attribute is there, so `AllowNoMatch="0"` counts as well. 508 of them in the Anno 1800 mods on mod.io, most in the shared pools piece | *Extended Agriculture Modules* extends pools that are not there |
| 81 | **narrow ops that do not add up** — the equivalence check refused the rebuild; since then [kept as the op with its changes still shown](#the-rule-is-the-fact-its-changes-are-todays-answer), so no longer in this table | *AI Everyone Hard AI* rewrites an AI trigger table whose entries have nothing but their position to tell them apart |
| 49 | **a condition it cannot answer** — the `~` form; since then read, because the loader's source strips the `~` and reads the rest against the op's own asset, so it is the bare relative path in an older spelling | *Extended Arctic* guards an addition with `!~/Values/ProductStorageList/…` |
| 26 | **a value copied from another asset**, with settling not asked for — since then [read at what it comes to and kept as written](#values-a-mod-works-out-while-patching), so no longer in this table | *Item Sockets (Kurila)* keeps twenty buildings' socket counts equal to the trade union's |
| 4 | **an asset your `assets.xml` does not have** | *Industrialized Low Tier Production* extends an item pool from *Extended Arctic* |
| 1 | **entries with nothing to match on** | *Items Adjusted* replaces a nine-entry list whose items differ only by order |

"Does not add up" is the check working: those rebuilds *were* produced, replayed,
found not to reproduce what the mod does, and thrown away rather than shipped.

A count is where that question starts rather than where it ends, so the ops
themselves are kept. On the Anno
1800 folder that is *244 ModOps not read, in 14 mods — most of them (51) in
Extended Arctic*, which is a far more useful thing to know than fourteen separate
counts scattered across fourteen tooltips.

#### A group with a condition is asked once, on the way in

Both used to be carried whole and shown as nothing, which
made the export right and left the editor well short of the mod: 1,896 such
groups in the Anno 1800 mods on mod.io alone.

The searches are swept over the shipped file once, with
every other search the mods make, so asking costs a lookup. 735 conditions in 84 Anno
1800 mods on mod.io search this way, and none in Anno 117; on the mods folder this was
written against, all 34 in 14 mods were unanswered before and are answered now.

Nothing in either census carries a `GUID=` or a `Path=` of its own, so there is
no such thing to read.

On real mods that is the difference between a list and most of a mod:
*Custom Villa and Guesthouse* went from 829 assets read differently from the
game's loader to none, *Specialist Management* from 153 unread ops to none.

#### Every asset that *has* a thing, rather than a list of assets

What the spelling means was measured, not guessed.

#### An entry found by what it holds, not by where it sits

161 of them in the Anno 117 mods on mod.io,
in 9 of them.

#### An op that could not be read is carried, not dropped

A `<Group>` with a condition used to be listed under *Not ModOps* and
left out of the export, taking every op inside it; an `<Asset>` written
straight under `<ModOps>` went the same way, and that is how Anno 117 mods add
their assets — 7,180 of them across a hundred of the mods on mod.io, whose
exports came back patching assets that were no longer there.

The other way an Anno 117 mod adds assets is one op that adds a `<Group>` of them
under `/AssetList/Groups[last()]`. It was carried from the start, as a search that
matched nothing, and so never read: *Black Tides* is one such op holding 518
assets, and showed as a mod with no changes. It is read as the bare `<Asset>` is,
each asset a new one and the op kept as written. Measured with xmltest3 and
xmltest2, the group lands after everything the game ships and a later op finds
its assets by GUID. A group repeating a GUID something already has is left a
search: after a plain group both builds edited the shipped asset, and after an
op had been at the copy's neighbour they edited the copy, so which one a later
op finds is the game's to answer.

That turned out to be a rule of the loader's and not a quirk of one case. Its
look-up by GUID walks the file in order, but starts with the top-level group its
last look-up landed in (`FindAsset`'s `last_search` in the loader's source). So
"the first in the file" is the asset found only among copies in one top-level
group. A probe mod in each game asked five cases of it, and both games answered
as the source reads: with the copy in a later group than the shipped asset, the
op after the one that added it edited the copy; with the copy in an earlier
group, an op after one aimed elsewhere edited the shipped asset. The Studio had
read both as the first in the file. Which group the last look-up landed in
depends on every op of every mod before, and an export that writes the same ops
another way changes it, so the Studio does not work it out: the index records
each asset's top-level group, and an op aimed at a GUID that is in two of them
is carried as written. In one group it is the first in the file, as before.
— the Export tab used to write only a mod that had
changes, which for nineteen Anno 117 mods on mod.io meant carrying every op
and then not writing the file.

A count in the
mod's tooltip said these existed; a row says which, and stops the list claiming
to be everything the mod does.

That used to be the other way
round: unread ops were left out by design, so that the file would never do more
than the change list showed. That kept the list honest by making the mod do
*less* in game than it did before it was opened — and for a mod of your own,
carried on from the folder it was published from, it overwrote the only copy.
Being unreadable is the strongest reason not to delete something.

### Ops that cannot be narrowed at all

The swap shown is a removal plus an insertion beside a *neighbour*, never beside the
node being removed: removals are written first (see below), so by the time the
insertion runs there would be nothing left to anchor on. That matters for a rule
switched off and on again in the list, whose changes are still replayed in that order
to show the asset.

### Ops run in order, and so does the output

The Anno 117 community bug-fix mod fixes product ordering by pulling entries out of a
list and re-inserting them elsewhere, each op guarded by "only if it is not there
already" — read against the pristine file, half of them look like they have already
been done.

*Harborlife* adds ninety
triggers in a single op and repeats a GUID among them by accident, which is the only
reason any of this was visible at all.

### The check that makes it trustworthy

That check has teeth: with the removals-first ordering removed, 59 of the bug-fix
mod's ops fail it.

On the mods installed here it holds end to end. Each is imported, exported as a mod,
read back in, and applied — and the result is identical to what the original mod
produces, on every asset it touches:

| mod | ModOps | becomes | left out |
|---|---|---|---|
| Anno 117 Community Bug Fix Mod | 137 | 317 changes across 63 assets | 10 already true of this `assets.xml` |
| Cheat Keybinds (Serp) | 2 | 16 changes | — |
| Unlock All Buildings | 6 | 5 new assets | 4 `Content=` ops |
| [Seal] Shrine Rework | 24 | 35 changes across 24 assets | 64 removals the game reads as absent |
| Advanced Game Start (Anno 1800) | 2 | 7 changes | — |

## Loading every mod you have

### Read as one stack, not one mod at a time

And it is not a detail. Read on its own, `Powerful Flagship` looks broken: seven of its
ModOps aim at parts of the flagship that the base game does not have, because
`117 - Immersive Flagship Mod` loads first and replaces the whole asset. Read as the
stack it belongs to, all twenty of its ModOps come through.

The Studio used to take all of that as one ModID, which is never installed, so such an
op read as not running whichever mods were loaded.

| | Anno 117 (17 mods) | Anno 1800 (65 mods) |
|---|---|---|
| ModOps read | 104 | 1,170 |
| become | 491 changes across 116 assets | 4,853 changes across 2,201 assets |
| could not be read | **0** | 61 |
| time to read the folder | 0.8 s | 11 s |
| fields two mods contest | 0 | 32 |

Every one of those 4,853 changes verifies against the document its own mod is handed.

#### Load order is not folder order

That was read off the loader's own log and reproduces it exactly for all sixteen mods it
had enabled — including the comparison being case-sensitive, which is what puts
`[Gameplay] Powerful Flagship` in front of `[Gameplay] [Seal] Shrine Rework`.

In the Anno 1800 folder this was written against, the first mod to run is
`[Seal] AI Everyone Hard AI (Serp)`, pulled to the front because `AI EnterSessions…`
names it in `LoadAfterIds`.

That reproduces the Anno 1800 log on this machine exactly: all 131 mods it names,
in its order, with no pair the other way round. The Studio used to keep base order
wherever it could and move a mod only as far as its list required. That agreed
with the log for 52 mods in a row, and it compared folder names in lower case,
which alone put 25 mods in the wrong place.

The Studio used to drop everything after the `*`, which put two late mods in name order.

Measured in Anno 1800 and Anno 117 on 3 Oct 2026, with eight test mods each carrying an
op that matches nothing, which the loader logs under the mod that ran it. Both games
loaded them in the order the Studio predicted, by the loader's listing and by the order
the ops ran: the mod naming a late mod without a star first, the three that declare
nothing in name order, then the late ones, with `["*", "loadlast-y-late"]` after
`loadlast-y-late` and the mod that named all three late mods last.

In Anno 1800 that last mod was 132nd of 140: eight late mods already in the folder,
which it did not name, loaded after it. In Anno 117 it was last of 22.

#### More than one mods folder

That last one was missing until the 1800 loader's own log gave it away: it records a mod
it loaded from there with the cache's absolute path,
`C:\Users\Public\mod.io\4169\mods\3229975\[Building] Nate's Windmill (Jakob)`, where the
other 236 lines in the same log say `from mods\`.

### Mods with mods inside them

Real ones do this constantly: 80 of the 145 mod folders in the folder this was written
against are bundled inside another, one mod carrying 26.

The loader's log has `[Addon] Extended Arctic (Kurila)` running eighth and one of its
bundled mods running third.

#### The same mod, bundled twice

Checked against every real case where two copies differed in version: the highest is the
one the loader used, every time.

#### A bundled mod's row is under its host, and has no box

The list used to give a bundled mod a row of its own at its place in load order,
beside the mods at the top, with a box like theirs. It read as a mod to switch off on
its own, and unticking one left the mods that need it short in the Studio's reading:
dependencies said it was missing, the stack was read without it. The game went on
loading it. Play cannot switch a bundled mod alone — Anno 1800 reads its switch off
the host's folder, and the Studio writes 117's profile the same way — so it refused,
and said so on every Play after.

So the row is under its host's and has no box. What a bundled mod answers to is
`Loadout.switch_of`: the outermost loaded mod it is inside, or its own switch where
it is inside none (a host with nothing of its own comes in as the mod inside it
alone). A bundled layer's own `active` decides nothing, and Play no longer asks for
it. Load order is still the game's: the row's second column says the position.

The mods-folder dialog lists it the same way. Its row is drawn straight below the
listed mod it is inside — the table's sort keeps it there, sorted among the others
below that host — and its tick is its host's, with no tick of its own to take away.
Leaving it out on its own was weighed and refused: the game loads it while the host
is on, so a project that read the host and not it would be reading a game nobody
plays, and the changes panel would have no switch to show for it anyway. The switch
it follows is `modfolder.switch_holder` over the listed rows, the rule
`switch_of` uses, so a mod inside a host that is not listed keeps its own tick.

#### Which copy loads moves with the switches

The copy of a shared piece the Studio reads is the one that loaded when the folder
was read. Switch that copy's host off with another carrier on and the game loads the
other copy, so the mod is still loading, a mod that needs it still has it, and a patch
to it — which names it by ModID — still applies. `Loadout.loading_copies` works the
copy out again from the switches, by the loader's own rule among the copies that are
on; between equal versions the one read is kept, since the reading already said the
loader came to it last. `switched_off` is every mod no copy of which is on, and
everything that asks what loads goes through it: dependencies, patches, replacement,
the options Play writes.

The rows stay those of the copy read, and the row says which copy loads. What it
cannot do is say what the other copy does without reading it, so the version of each
passed-over copy is kept at read time (`ModLayer.copy_versions`): equal versions are
taken as the same mod, and another version is said on the mod's row, on its patch's,
and in Play's question. So are your edits to a mod of your own left in the copy that
stopped loading: they are in that copy's files, and the other copy never had them.

#### Ops kept in other files are a different case

Checked on *Charcoal Power Plant*, which is seven includes and no ops of its own: read
as 37 changes, exported, and read back as the same 37.

### Where two mods reach for the same thing

On the Anno 1800 folder here that is 32 fields across four pairs:

```
17  Item Sockets (Kurila)                 <->  Item Overhaul (Kurila)
 6  Extended Arctic (Kurila)              <->  Better Luxury Goods
 5  The perfect specialists & items pack  <->  Faster Constructions (Kurila)
 4  Extended Arctic (Kurila)              <->  High-Contrast Range Indicator Colors
```

The last one is the kind you would never find by hand: both set the colour of the
street-overlay range highlight, and whichever loads later is the one you see.

#### Two assets under one GUID

The census found it three ways, none on purpose: *Local Department for Arctic Enbesa
Horai* gives two decree buffs one GUID in two compatibility sections that both always
run; *Cultural Expeditions* makes a research balancing asset under the GUID its bundled
botanical sub-mod gives a trip's name; *Kurilas Modlist* has a description asset in one
shared piece under the GUID of a trigger in *New World Docklands*.

Reading *Item Overhaul (Kurila)* in on its own finds the 17 GUIDs its bundled *Trade
Union Rebalance* gives names that *Extended Agriculture Modules* gives items.

#### Collisions a mod means to make

The two are worded differently on purpose. A patch is proof. A declared load-after is
strong evidence rather than proof: it is evidence about the mod, not about this
particular field. That is why the row is greyed rather than hidden.

#### A mod that deletes an asset outright

It used to stop there: the change's path is empty, an empty path resolved to nothing,
and a change that matches nothing is not applied — so the Studio listed the mod as
deleting the asset and went on showing that asset exactly as shipped.

#### Waiting, and being told what for

Reading a mods folder is two halves, and only the first was ever reported. The bar
filled as the mods were read and the file searched, then sat at 100% while every ModOp
was answered against the stack — ten seconds on a real Anno 1800 folder, with nothing
said. The second half now reports too: *working out the order the game loads them in*,
*turning 1,142 ModOps into changes* with a bar that moves, *checking what the mods do to
each other*, *sorting the changes into their mods*.

Accepting that dialog then took about as long again behind a wait cursor. That work is
five steps — clearing what was loaded, putting the mods in place, working out which are
your patches, taking in the changes, grouping them for the panel — and it now names the
one it is in.

The Generate tab used to re-verify **every** change in the project on arrival. With a
mods folder open that is six thousand changes from sixty mods, checked to draw a page
about one of them. It checks the mod being previewed, which is what the page is about,
and says it is checking before it goes quiet.

The reference graph had the same shape of problem. A change can add or remove a link, so
any asset with a change on it could not be trusted to the index and had to be walked
live — thousands of tree rebuilds to draw a dozen nodes. But a change can only alter a
link running through the part of the asset it touches: a retuned cycle time does not
move the good a factory consumes. Only assets whose changes reach the entry a link sits
in are walked now. The entry, not the field: whether an entry sets a value is what
[hiding links that do nothing](#links-that-do-nothing) turns on, so an `Amount` beside a
`Product` matters as much as the `Product`. Paths are compared with their predicates
stripped, because `Item[Product='1010017']` and `Item` are the same place.

#### Double-clicking a change

It used to fail silently three ways, all of which looked like a double-click that did
nothing: an asset not in the loaded assets.xml (a mod built against another game
version), a row the editor's own filters were hiding — selecting a hidden row shows
nothing at all — and a structural op, whose path names the container something was added
to or removed from rather than a row that is still there.

### Mods the game is not loading

A mod switched off is struck through in the changes list, and its tooltip says why. Its
row used to say "(switched off)" after the name as well; the strike-through already
says it, and the note made the longest names longer.

* **Anno 1800** … Seven of the sixty-four mods in the folder this was written against
  are switched off that way.

#### Play

It used to be *Switch installed mods to match…*, and only switched the mods the project
mentioned; the others were a checkbox's business.

The tempting design is for the Studio to own the mods folder outright and rebuild it
from the project on every export, making the project file the source of truth for the
loadout. It cannot: a mod is not only its ModOps. Most of a real mods folder is meshes,
textures and localisation the Studio never reads and could not put back, so rebuilding
the folder would delete all of it — and the backup that would make that survivable is
2.3 GB in the folder this was written against. Reading the game's own switch costs
nothing, loses nothing, and needs no backup: a mod that is off is off in the folder, in
the project, and in the game.

Before Play wrote a profile of its own, it edited the live 117 profile, and did so line
by line rather than regenerating it, so the profile kept the user's comments, their
ordering, their `DisplayName` and their `EnableNewMods` setting — and, more importantly,
every mod it listed that the Studio had never seen. Anno 117 loads from four folders at
once (`mods/.ubi` and `mods` inside the installation, one under Documents, and mod.io's
download cache), so an id in the profile with no folder in the one you opened is usually
a real mod living somewhere else; dropping it would switch that mod off. The code is still
there (`switches.write`), for when there is no mods folder to put the Studio's own profile
beside, which never happens to Play: it asks for a mods folder first.

#### What the mods say about each other

On the mods folder this was written against, the check found exactly the two missing
dependencies the loader's last log reported, and one replaced mod the log confirms was
never loaded.

*Safe to remove* is the one that differs from the loader's own default: most of what the
Studio writes is narrow value changes, which leave nothing in a save, and 218 of the 272
census mods that say say the same.

#### Mods that run scripts

The census has one real case: *Ship Finder* and *Harbor Arrivals* each ship their own
`parchment-controls-consumer.lua`.

#### Mods that patch tooltips

67 of the 857 Anno 1800 mods on mod.io ship one, most as shared pieces bundled over and
over — Serp's *Menu Input/Output Amounts* and *TooltipBugFixes*, the pools and
definitions.

1,204 of the 1,256 ops in the Anno 1800 patches on mod.io name or add theirs. On the
mods folder this was written against, 22 of the mods that load patch InfoTips, and two
InfoTips are patched by more than one — one of them by seven.

#### A mod's other patched files

- **Anno 1800 `.cfg.xml` and `.fc.xml` patches** … All nineteen on mod.io patch the
  world map, adding sessions to it.
- **Link files** (`.lnk`, Anno 1800) … One map mod ships 82,333 of them; they were
  listed as files replacing the game's, which they are not.

#### What the game will load right now

About a second and a half for a full folder.

There used to be a button a row, switching one cell's mods on or off; a row switched on
its own was a mods folder matching nothing anybody had asked for, so they folded into
the one act. There used to be a box on this card as well, *Switch off every installed
mod this project does not mention*, for the difference between a project that was the
whole list and one that was not. Every project is complete when played now, so the box
went; a loadout is what the third card makes, and a project saved with the box ticked,
or by *Run your whole mod list*, opens as one.

#### What the game actually loaded

Ignoring every start that was not Play's would switch the check off most of the time,
and a change something else made is exactly what is worth knowing about.

Tried against this machine's own logs, it found two things. An Anno 117 profile edited
after the game last started — by the Studio itself, as it turned out, in the days when
an install and a switch wrote into the live profile, which is the player's own as often
as not; neither does now. And a folder called `data` in Anno 1800's mods folder: a mod's
own `data` tree extracted straight into `mods` from a zip with no folder of its own,
which the loader lists as a mod called `data`. Whether it applied what was inside was
not in the log, which says nothing of ops that work, so `tools/loose_data_probe.py`
asked the game: a loose tree, a proper mod and an empty folder side by side, each
carrying an op aimed at a GUID nothing has. The proper mod's op ran; the loose tree's
did not; and the empty folder was listed too.

On this machine's Anno 1800 log that was 39 ops in 7 mods, 31 of them in *Town Hall
Rebalance* — one op aimed at a list of assets, failing once for each.

#### The library

It used to list the new mod as on there, which was an edit to somebody's profile outside
Play.

A mod's duplicates are set aside *before* the copy that stays is linked: a cache copy
that loads is linked under its own folder name, and your older copy of the same mod
often has exactly that name. The other way round, a rehearsal on a real Anno 1800 folder
set the new link aside instead of the duplicate, three times, and left those mods
loading from the cache.

It is renames, not copies: on a copy of the real Anno 1800 folder (58 moved, 14 copied,
7 set aside) it took under two seconds and left every file byte for byte as it was.

Models and textures compress well: the 2.4 GB Anno 1800 folder here comes to 0.99 GB in
about a minute, at deflate's fastest setting (54% of the size, where its default gets
52% in two thirds more time); extracted, every file is byte for byte as it was.

Why the Studio does not unsubscribe for you, when mod.io's API would let it:
`tools/unsubscribe_probe.py` tried it, twice. Unsubscribed through the API, the game's
client fetched the account's subscriptions at its next start, saw the mod gone from them
— it had been on the client's own saved list, and was not after — and still kept its
copy held for the account, folder and all. Unsubscribed in the game's own browser
instead, the client dropped the account's hold on the copy at once — and kept the
folder, because a second account held it too.

`tools/unsubscribe_probe.py --reset` tried it here first: the client came back with 17
mods, every one held by this account only, and left out the 4 only the closed account
had held.

A loadout can start from the library's own list (*From the library…* on the third
card). A library mod that is off has no link in the mods folder, so the folder read
never came to it: it is read where the library keeps it and named as its link names
it, because the link's name is the one Play looks up to put the link back. Nothing is
ticked to begin with. *What I play now…* is the start that takes the folder as it is;
this one is for a set that is not what the game loads, so the ticks are the choice and
each ticked mod comes in on. The read moves no link. That stays Play's.

The list is a list and no more: a name, a version, on or off, from one modinfo each.
It was the folder read's own table at first, which reads every mod in the library as a
stack before it shows a row, and on a full Anno 1800 library that was a wait before
there was anything to tick, for counts nobody choosing mods was asking about. The
picking comes first now, and the read is of the mods picked.

#### What the library knows of each copy, and what it could not get back

The library could be lost: a dead disk, a new PC, Windows installed again. What it
would take to get a copy back depends on how the copy arrived, so every copy's record
in `installed.json` says that (`source`): downloaded from mod.io, copied from the game
client's cache, unpacked from a zip, moved in from the mods folder, or built from a
project. A copy downloaded from mod.io also has the mod and file it was; a zip has its
file name and MD5.

A mod.io mod matched to a copy afterwards, by the check for updates or the install plan,
is written against the library's copy and not only into the project, with how it was
matched (`known_by`). The library outlives any one project, and the project only knows
the mods it has loaded. A match never overwrites what the Studio installed by id: that
is a fact, and a match is not. A zip's MD5 is what turns a match into a fact. mod.io
publishes the MD5 of each of its files, so a zip downloaded there by hand says which
mod it is and which of its files, and an update is then decided by comparing files,
not version strings. The current file is in hand already; an older one costs one
request for the mod's files.

*Settings › Library* lists the copies there is no second source of: a zip nothing
matched and a folder moved in from the mods folder. It was going to be one list. Run
against a real library first, 81 of its 96 copies were in it, nearly all from before
the library recorded where a copy came from, so a line saying "the only copy of 81 mods"
was true of almost none of them that anyone could act on. Those are counted apart now,
under *Not recorded*. Before counting, each copy with no mod.io mod is matched by its
ModID against what the game's own mod.io client installed, which needs no network. On
that library it identified 4. The rest wait for a check for updates, which covers only
the mods a project has loaded.

A copy that differs from what was downloaded, edited in place, is not told apart: the
library keeps no hash of the files it unpacked.

#### Backing up what could not be had again

An only copy is backed up whole, as a zip, with the other backups in Documents. The
backups the Studio already made could not stand in for it. They hold only the files the
Studio can write, a mod's ModOps, modinfo, texts and Lua: 4 MB of a 2.4 GB mods folder,
there to undo the Studio's own writes. Text alone gets back a mod that is all XML and
leaves any other without its models and textures, which in the game is a broken mod. On
the library this was measured against, 43 of the 80 copies with nothing recorded were all
text, but the rest were 2.1 GB of the 2.2 GB, and a backup that looks complete and
is not is worse than none.

So a zip dropped on the window is kept as it came, as it is installed: it is the exact
file, already compressed, and its MD5 can still say which mod.io file it was. A folder
moved in from the mods folder has no file to keep, so it is zipped, but only when **Back
up** is pressed in the list, since the large part of the backups is only ever made
because somebody asked. The zip has the mod's folder at its top, which is what a mod's
zip is: dropped on the window it is installed again, so getting a mod back needed
nothing new. A copy with a zip is listed apart, under *Backed up*, and is not counted as
an only copy. A mod.io install over it drops the record of the zip, which was of the
copy it replaced.

#### Generated copies

Play used to write the player's iModYourAnno settings into the library's copy of a
mod, through its link. So the library did not hold what the author shipped, and the
next update replaced the settings along with everything else. Now the settings go
into a generated copy beside the library, and the link points there
(docs/proposals/patches-proposal.md, phase 1).

Every folder of a copy is a real folder. The proposal had untouched subfolders as
junctions back into the library, which would have saved making a link for every
file, but whether either loader follows a junction *inside* a junctioned mod when it
walks the mod is not known, and a recursive walk often does not. A hard link is an
ordinary file to whatever reads it, so text is copied and everything else is linked.
Text is copied rather than linked because a tool that writes into a mod folder in
place, iModYourAnno among them, would otherwise write through into the library's
copy. The `generated` folder sits beside each game's folder in the library, under
the same root, so a hard link can always be made.

Measured on the census with `tools/generated_check.py` (16 October 2026): with
nothing written, every one of the 919 Anno 1800 and 396 Anno 117 mods came out file
for file and byte for byte the mod, 76,046 files copied and 36,295 linked in Anno
1800. With iModYourAnno knobs moved off their defaults (2,022 knobs in 75 mods), 72
copies read back exactly as set. The other three are five toggles set off that read
back at their defaults, and they do the same written straight into a copy of the
mod, without the generator: the port's reading of those toggles, not the copy.

A link to a generated copy is still the library's copy to everything that asks which
mod it is: the update check, the inventory, the registry, deleting it, putting it
back. They ask `library.source_of`, which reads the copy's `generated.json`. Two
places found on the way had to be told: the sweep for GUIDs taken on this PC reached
the mod twice, through its link and in the library, and counted it twice; and Play's
build of a mod you edited in place carried the mod's other files across from the
folder the link reaches, which was the generated copy, whose files are hard links to
the very files being written. It carries them from the library's copy now
(`build.carry_folder`).

#### Edits to other people's mods

Changing somebody else's mod used to mean Play writing the Studio's version of the
whole mod over the library's copy, which the next update replaced. Now the edits go
into the mod's generated copy as `Skip="1"` on their op and yours straight after it,
and every other byte of their files is theirs (docs/proposals/patches-proposal.md,
phase 2).

What the edits are is found by comparing writings, not by keeping records of them.
The project already holds the mod with your edits in it, and keeps them when the mod
is read again; written out, it is their mod with your edits. Compared straight with
the author's file, though, it differed in 245 of the bench's 416 mods with nothing
edited at all, almost all of it a bare `<Group>` the writing leaves out. Treating a
bare group as the bracket the loader takes it for left 6, every one an `<Include>`
the writing writes out in full. So the edits are found between two writings, the
project's and a fresh reading of the library's copy, and carried onto the author's
file through where each node of the fresh one stands in it: an op it writes as the
author did is skipped or put after as itself, and one in a run it writes another way
takes the run with it.

Comparing each writing with the author's file and leaving out what the fresh one
differed by was tried first. It took an op beside an inlined include with the
include, because the comparison makes one run of two differences with nothing equal
between them: with one op taken out of each mod, only 20 of 400 copies held exactly
that. Carried across from the fresh writing, 399 of 400 did, and 401 of 402 with one
op typed over; the one left each time is an edit inside an inlined include, which
takes the include with it as it should.

A guard for a mod with nothing that runs -- a box of mods with no patch of its own,
which cannot be read on its own -- first looked only at your version. Taking out the
only op of *Influence Removed* left your version with nothing in it, and the guard
wrote nothing: the op you took out would have gone on running. It looks at their
files as well now.

#### An update to a mod you edited

Each edit is tied to the op of theirs it was made to by that op's text, which is how
the project has always kept an op taken out from coming back when a mod is read again.
What of yours stands in its place is tied to it by its place in the file, kept with
the op taken out. So at an update each op is looked for in the new version: found,
the edit stands; changed or gone, the edit is held, switched off, until you have seen
their op as it was, as it is now, and yours. Holding rather than applying is the safe
way round: applied, an edit made to their old op would run beside their new one.

Writing the tests turned up two things. The reading of a mods folder -- the one that
follows *Download and update* -- did not carry an op's switch across at all, only the
background reading did, so an op of theirs you had switched off came back on at every
update. And the reading takes the ops you took out out of its own list as it lands, so
an op found exactly where it was looked gone unless what was read is counted first.

#### A loadout with a name

The project is the record, and a profile per project would be a pile of stale snapshots
that look like saved state and are not — and the loader's log is only a check against
something if what the game was pointed at is certain.

Earlier builds wrote one profile per project, `<project name>.txt`; those are left where
they are.

(It used to mark everything a collection lists as having come with it, and switch your
own mods off along with its.)

### Writing them back out

It used to be labelled *Previewing*, which named what it changed on screen rather than
what it decides — next to an export button that ignores it, that reads as decoration.

Doing that to eighty-five mods you never touched, in order to save the edits you made to
two, is a bad trade — and it was the button the Generate tab pointed at. On the mods
folder here:

```
                        mods written    ModOps carried unread
Export your changes…               2                 0
Rewrite every mod…                87               990
```

(Those 990 used to be *dropped*, which is what the count was first put there to say.)
*Rewrite every mod…* has since gone from the Export tab, and *Export your changes…* is
the **All the mods you changed** choice of its **Mod to export** box: see *…and the
buttons, the same day* under Notes.

All 60 of the Anno 1800 mods that have readable ModOps are rewritten in a quarter of a
second, as 6,849 ModOps standing for 7,465 changes.

#### Its modinfo is its author's file, not a form the Studio fills in

Rebuilding one from the six published the rest as empty — so exporting a whole mods
folder quietly unpicked its own load order.

That was measurable rather than theoretical. Of the 107 modinfos installed here,
**55 declared something the export would have deleted**: 23 a `LoadAfterIds`, 16 an
`IncompatibleIds`, 10 a `Dependencies`, 38 a `KnownIssues` — and 98 of them carry a
name or description in at least one of eleven other languages.

Round-tripping all 106 readable mods in the two real folders now loses nothing at all.

`tools/modio_roundtrip.py` compares every exported modinfo with the one the mod shipped,
key for key, so that stays measured.

### Putting a mod back the way it was

Backing up a whole mods folder would be absurd — the Anno 1800 one this was written
against is 2.4 GB, nearly all meshes, textures and localisation. In that same folder
those come to **4 MB, 0.17% of it**, which is small enough to keep twenty of. Copying
every mod's takes about a second and a half.

Saves used to keep only its name, and restoring one wrote it into the install's mods
folder, beside nothing that reads it; a save from before that says so, with where its
copy is, instead of guessing.

Exporting a copy of a real 117 mods folder and then restoring it leaves all 173
files byte-for-byte identical to the originals, with nothing added and nothing
missing.

### Your own mod is one of them

That matters beyond tidiness: the Studio recognises a mod as *yours* by its creator —
which is how it knows not to offer a patch for something you wrote — so a project
wearing another author's creator name made their mod read back as one of yours.

#### Editing one of the loaded mods itself

Editing a residence that two other mods had already been at used to write a ModOp
claiming a value neither of them ends on.

#### Patching a mod rather than editing it

That is the moment of intent — you have said which mod you mean and have not yet changed
anything — rather than up front for a whole mods folder, which would make a hundred and
fifty folders nobody asked for, or mid-edit, which is too late to be a choice.

It is tempting to treat *play a set of mods* as the committed user who does not need
warning, but commitment is a property of the person and the hazard is not: an update to
somebody's mod replaces their file whether you are trying the Studio out on one mod or
running your entire list through it. What the aims differ in is **scope** — one mod and
one patch, against the whole stack with its conflicts, load order and switches — not
safety.

#### A patch is drawn beside the mod it patches, not among the others

This needs no new level in the tree, because the patch is **already** immediately below
its mod — the ` (my patch)` suffix sorts it there and load order is folder order, which
is the same fact that makes the patch work at all.

#### A patch whose mod is switched off

A second checkbox to keep in step is a second checkbox to get out of step, and switching
one mod off should not have to ask a question about another.

#### How the Studio knows which mods are yours

Both need an answer to *who are you*, and that used to live in one place only —
the **Creator** box on the Generate tab, the last field on the last page. Until
somebody found that box it was blank, and blank is exactly what decides whether a
loaded mod counts as somebody else's. So the Studio could not tell your own mods
from anyone's, and nothing ever asked. Worse, the one thing that *did* fill the
field in was a bug: importing a mod copied its author's name onto your project,
at which point their mod read back as one you had written and was refused the
patch it needed.

That matters because `ModLayer.patches` is set when a patch is made and saved
with the project, while a *folder* read builds layers out of modinfo files. Open
your mods folder in a fresh project and the patch used to come back as an
ordinary mod that happens to load after another one — and since nothing then
claimed to patch that mod, the Studio would cheerfully offer to build it a second
patch. The marker settles it. For patches made before the marker existed there is
a fallback inference: it is yours, and it declares that it loads after exactly one
mod which is present. That is good evidence rather than proof — two mods of your
own where one really does depend on the other look identical — so it is only used
where the marker is absent.

## mod.io

### Checking your mods for updates

The dialog asks mod.io nothing until **Check** is pressed, and then only about the mods
ticked. It used to start checking every loaded mod as soon as it opened. Somebody who
opened it to see what it does, or to check two mods of four hundred, set off a check
of all four hundred, at mod.io's rate limit of about one a second for each mod looked up
by name. It has the ticks, the box over them and the filter box the other mod lists have.

Each answer fills in its row as it comes. Most of a check's time is the wait between
requests that keeps to the rate limit, not the Studio's own work, so drawing a row as
soon as it is answered costs nothing, and shows what the results look like long before
the last one. The rows stay where they are while answers land, since a row that moves
while you read it is worse than one in the wrong place. When the check ends they are
drawn with what wants doing first at the top, unless you chose a sort. **Stop** ends a
check between requests and keeps what was answered.

The folder this was written
against has nineteen mods in the cache and the game loads one of them from there; the
rest are superseded by copies installed by hand under other folder names — *Extended
Agriculture Modules* is `[Gameplay] …` in the cache and `[Seal] …` in `mods\`. Attached
to a folder, the cache's answer would say nothing about the copy that actually runs.
Attached to the ModID, one cache record answers for every copy on the machine.

### A sign-in that runs out

The email-code sign-in gives a token and a `date_expires`, a year ahead at most.
mod.io has no refresh for it: the refresh tokens in its documentation belong to the
OAuth login a website does with a client secret
([docs.mod.io, studio website login](https://docs.mod.io/authentication/studio-website-login)),
and the email exchange answers with the token and its day only
([REST reference, email exchange](https://docs.mod.io/restapiref/#exchange-security-code);
both checked 17 Oct 2026). So the Studio does not try to keep a sign-in alive. A
token past its day counts as no sign-in and is not sent. Two weeks before, the
account panel and the Export tab say when it runs out. A 401 on a write means mod.io
no longer takes it, whatever the day says: the token is removed from this PC there
and then, rather than kept and sent again, and the user is told to sign in again.

### A zip dropped on the window

A mod that is not on mod.io, or a version its author sent directly, arrives as a zip.
Dropped on the window it is installed the way a mod.io download is, into the library
with a link in the mods folder. Three things differ, because nobody has vouched for
the file:

- **It is read before it is unpacked.** mod.io names the mod, its version and the
  file. A dropped zip names nothing, so its entries and its modinfo are read in place
  and the question shows what it holds and what it replaces before anything is
  written.
- **A copy is matched by ModID.** An author's zip of a new version is often a folder
  with the version in its name. Matched by folder it would land beside the old copy,
  and the game would load one of the two by its own rule. Matched by ModID it takes
  the old copy's folder and link, so its place in the load order stays.
- **It is refused with no game files loaded.** A zip does not say which game it is
  for. It goes into the library of the game that is open, and with none open there is
  no library to name and no index to read the mod against.

Opening the mod as a project asks about the open project before the zip is unpacked,
so declining leaves nothing installed.

## Grouping and switching changes off

A flat list of changes was fine while a project was a dozen edits somebody typed one
at a time.

### Grouping

On 200 assets picked at random from Anno 117 it
finds 198 groups: it merges where there is evidence and otherwise leaves each asset
alone, which is the right way round for something that only decides how a list is
drawn. On the Anno 117 community bug-fix mod's 63 assets it finds two clusters, both
real — a field building with its module, twice.

### Reading a path

Both used to be written `Item(N)`,
which said neither which kind it was nor what the number counted.

### Switching changes off

Ticking a group walks every change under it, so the rest of the window is told once,
when the burst is over: unticking a 76-change group takes about 8 ms end to end.

The project file gains an `active` flag per change. A project saved before this
existed loads with everything on.

### What is switched off goes into the file, as comments

A change or rule switched off was left out of the export, so the file said
nothing of it: somebody reading the mod, or the Studio reading it back, could
not know it had been there. Each one is written where it would have been,
inside a comment.

That began as an output option, off unless asked for. Reading such a comment
back was then tied to narrowing: an op came back as plain changes where its
narrow form read the same, and a mod read in and mostly unticked would have
been a file that was mostly comments nobody had asked for. With every op
read as written it comes back as the op it was, and with the option off a
switched-off op was lost from the folder the mod is read back from. So the
option is gone and every export writes them
([file tab proposal](proposals/file-tab-proposal.md), phase 0). A mod with
everything in it switched off is written as a file of comments. Four things
about it.

- **The game reads the same file either way.** A comment is not an op. The
  ops written for the game are the same with them as without, which the
  census confirmed while it was an option: main with it merged
  (`rt-45eed73`, 5 Oct 2026) moved no unit of either game against the run
  before.
- **The comment opens with the Studio's own line**
  (`changes.SWITCHED_OFF_MARK`), which is how reading the file tells it
  from a comment the author wrote (`changes.switched_off`,
  `modops._comment_before`). Take that line and the closing one away and
  the op is there again. Two things cannot be said inside a comment. One
  is `--`, written `-&#45;`, the same text to whatever reads the op once it
  is uncommented. That is 37 ops of 137,000. The other is another comment,
  and one op in six of the mods on mod.io holds its own. Its ends are
  spelt the same way, `<!&#45;&#45; why &#45;&#45;>`, with every `&` in what
  it says written `&amp;`, and `changes.switched_on` undoes that before
  the op is read. So an op that holds comments comes back with them, and
  is not plain XML while it is off: a person uncommenting it by hand puts
  those dashes back. An op with no comment is written as it always was.
- **Read again, it comes back switched off, and as what it was.** It has an
  outcome of its own in the import summary; counted with the ops already
  true of the game's files, which it was at first, it was neither true nor
  brought in. And the reading is tested before the op is let go of: where
  the generator writes its changes as one ModOp that reads the same as the
  one in the file, they come back as plain changes, off. A search, a group,
  an op with a condition, or one aimed at what the mod itself adds stays the
  op, as a rule that is off.
- **What the Studio says in the file is marked as the Studio's.** Reading
  takes the nearest comment above an op for its note, and with a comment on
  each ModOp the nearest was the Studio's own: a change with no note came
  back with "Radius: 10 -> 25" for one, and gained a "note:" on every trip
  after. So the Studio's lines open with `:AMS:`
  (`changes.STUDIO_SAYS_MARK`) and a note is always written under `note:`.
  Reading passes over the first for the second. A comment with neither is
  one written by hand, and the note it always was. An export made before
  the mark is read by the wording of its headings.

### What the Studio no longer says in the file

An export opened with a header and wrote a line over every op. Both were
written for a Studio that made every op itself, and both are gone, with the
options that asked for them
([file tab proposal](proposals/file-tab-proposal.md), phase 0).

- **The header** gave the mod's name, creator and description, "Generated
  by" with the date, what the mod was built against, and how many ops of
  each kind. The modinfo has the first three, the build, and what the mod
  was built against. "Generated by" is not true of an op carried as
  written. The counts go stale the moment the file is typed in.
- **The line over each op** said why a kept op could not be narrowed, that
  an op searches, why an op could not be read, or what a narrow change was
  and is ("Radius: 10 -> 25"), with a warning where the check against the
  game's files failed. The first two were the narrow reading's reasons,
  which no longer decide anything. The Mod changes panel and the Export tab
  say the rest. The old value of a narrow change is the one thing nothing
  else in the file says, and it is in the project and on the screen.
- **The headings over each asset's ModOps** went later (18 October), and
  *Group ModOps by asset* with them. Since the Mod file tab names the asset
  beside each line that names one, as a link, a heading over each block said
  it again, and a pattern written as narrow ModOps came out as a heading
  over every one. The option was all that was left of the Studio writing
  only narrow changes. Ticked or not, it wrote the same ModOps in the same
  order: the changes of one op are written a block per asset whatever it
  says, since that is the block the reader replays and `emit_order` sorts.
  Unticked, it left out the headings and ran an op's blocks into one, and
  two assets' adds at one path went out as one ModOp aimed at the first.
  There is no grouping left to mark, so no comment came back for it.
- **A file written before reads as it did.** The reader goes on passing
  over `:AMS:` lines and the older exports' wording.
- **Nothing in the file carries the build number now.** It was stamped into
  the header's title once the build was known. The modinfo says it.

The switched-off option had made the Export tab's left column one row
taller, which is what showed that the column could not scroll. Short of
height, the layout took it out of the hint under the description, and in
German at 1500 by 950 the hint's last line was cut through. The column is
in a scroll area still.

## Projects and history

It has to be: the effective asset, the
conflicts and the load order are all statements about the whole stack, and none of them
can be made a mod at a time.

### Where the work lives, from the start

That is the point of doing it there: changes
that do not know where they belong are changes you have not really decided to make, and
the Studio's own answer used to be invisible — it slugified a home, kept it to itself,
and committed to it on your first export, so the one location you never chose was the
one you ended up with.

It used to come above them, on the
argument that a saved project answered the question the cards asked; but the cards are not
a question any more, they are the setting up.

- This is also where that name used to live: last field, last tab, after all
  the work.

These were called versions until mods had numbered versions of their own.

Checkpoints store the whole change set rather than a delta. A change is six short
strings, so even a long history stays a small file and reconstruction can't drift.

#### Two histories

A mod of yours is kept in a project, apart from the loadouts that have it, so its
history is kept there too: one loadout's history of a mod that three loadouts edit
would show a third of what happened to it, and restoring it would put back work the
other two had moved on from. The loadout keeps its own history, of what is its
own: the list, its order and switches, the settings each mod is read at, its
profiles, and which checkpoint each project stood at. *Restore, with each project as
it was then* is the loadout's restore and then each project's, so one restore never
does something the two cannot do on their own.

- **A project's restore names the loadouts it changes,** because it changes the mod
  in every one of them.
- **Build numbers, the mod.io link and what was published are never restored.**
  They record what happened outside the file. A project put back to before build 12
  that handed out build 12 again would give two different mods one number.
- **Each edit says which loadout it was made in,** noted when a checkpoint first
  holds it and kept while it says the same.
- **A mod taken out of the loadout since is read again from its folder.** The
  loadout's history holds what the list needs to find it, not what reading it found,
  which can always be read again.

#### What you opened, and what you did

- A version of 7,465 read-off-disk changes says nothing anybody wants to
  read, and restoring one would put somebody else's mod back to a state it was
  never in.
- Opening a folder and closing
  the app used to ask whether to save seven thousand changes that were already on
  disk.

They used to be two, and the difference
lost work: the mark meant "this differs from what is on disk", while the warning
meant that *and* "and at least one of the differences is a change you made". So
everything else a project holds — the mod's name, version, creator and
description, the mod.io links it has learned, which of your mods an edit lands in
— wore the mark and then went without a word. Typing a description on the Export
tab and closing the window lost the description, with the title bar having said
so the whole time in between.

Those details are now work in their own right (`Loadout.typed_details()`), and
a mods folder read in still is not: layers are compared only where both sides
have them, so a mod *appearing* is a read and a mod *changing* — renamed, or tied
to a mod.io id — is something somebody said. Two moments also count as the blank
page rather than as unsaved work, because nothing has been done yet: a card on the
Start tab settling what this project is, and the first write of a project that had
only been named. The second was its own small lie — the Studio wrote the whole
project to disk and went on wearing the mark over a file holding every word of it.

#### Reading the folder again keeps your work

This was the sharp edge. That threw away anything you had edited into those mods, silently.

## Notes

- **Re-indexing the file already open** (File → Re-index current assets.xml) closes the
  Studio's own read connection first: on Windows, a cache rebuild replacing that file
  fails with a `PermissionError` otherwise, since the OS will not let anything replace a
  file this same process still has open for reading. The rebuild never touches the
  existing cache file until it has fully succeeded, so if the rebuild itself fails, the
  Studio just reopens the same connection — nothing is lost.
- **A redraw with a mods folder loaded is bounded by what actually moved.** Every
  keystroke that lands re-groups the changes list, and with sixty mods open the naive
  version of that took seven seconds: it asked "is this a mod-made asset?" once per
  asset per mod and re-parsed every created asset's payload to answer, tens of millions
  of XML parses for one redraw. Four caches fixed it, each held against the thing that
  can actually invalidate it. **Which mod-made assets exist** is held until a change
  that creates one moves. **What points at an asset** is held per asset, so a value
  typed into one throws away that asset's answer and nobody else's — it used to throw
  away all of them. **Which assets belong together** is a property of the base game, so
  once worked out it is kept for as long as the app is open. And **the answer itself is
  bounded**: a link the shipped file does not have can only come from a change that
  writes the number down, so "what points at this?" reads the change set for
  candidates instead of walking every asset the session touches. Adding a change to a
  2,600-change session went from 7.3 s to 0.33 s, reverting one from 4.6 s to 0.25 s,
  and the first draw of the whole list from 356 s to under two.
- That
  used to be a mix: an editable combo with its own completer backed the template pickers
  and the category field, so that typing could filter a long list. It kept generating
  small inconsistencies with the plain reference control no matter how it was patched —
  a caret here, a highlighted-selection bar there — because it genuinely was a different
  kind of widget, one with a line edit, sitting next to ones without. Two things made it
  possible to drop entirely: mod.io defines a short, fixed category list per game and
  never accepts an arbitrary value, so that field never needed typing in the first place;
  and Qt's own type-ahead search (accumulate the last few keystrokes, jump to the first
  item whose name starts with them) reaches any of ~1,000 templates in a few keystrokes
  without a line edit at all — the same experience *Target game* already had, just
  applied to a much longer list.
- The radio button indicator is a shipped image, not a QSS-drawn circle. Qt Style Sheets
  render `border-radius` shapes with no antialiasing at 14px — invisible on the
  checkbox's barely-rounded square, visibly jagged for a true circle — so every state
  (unchecked, hover, checked, and each disabled) is painted once with a real antialiased
  painter and shipped as a PNG. The checkbox tick got the same treatment for a different
  reason: its *unchecked* state was still a CSS `border` box sitting next to the checked
  state's border-less `image:` box, and QSS adds `border` on top of a subcontrol's
  declared width/height — so the two states were genuinely different total sizes, and
  toggling one visibly nudged its label. Every checkbox state is a border-less image now,
  all the same size, for the same reason every radio state is.
- **A table cell keeps as much of its text as fits, never just an ellipsis.** With a
  global stylesheet active, a cell paints through `QStyleSheetStyle`, which computes its
  own eliding — and for a long enough string in a narrow enough column, that computation
  collapsed the text to a bare "…" instead of the leading characters that actually fit
  (the Add a field dialog's Field column, for a long field name). `ElideItemDelegate`
  (`ui/widgets.py`) elides against the column's real paint-time width instead, and is
  shared by every table that needs it — the Add a field dialog, and the *Mod changes*
  panel's Path and Change columns, which used to carry their own private copy of the
  same fix.
- **…and it keeps the *end* of the last word, rather than dropping the word whole.**
  The same cells could still read "Copy Pools AP …" for a mod called *Copy Pools AP
  ConstructionCategoryBuildings*, in a column with room for two thirds of the word it
  threw away. Two things had to be true at once, which is why the three columns beside
  it looked fine:
  - `QTableView` **word-wraps by default**, and these rows are one line tall. Together
    those are not "wrap": the style lays the text out wrapped, the word too long for the
    line moves to a second line nobody has the height to draw, and what is painted is
    the line before it plus an ellipsis. Nine of the app's eleven tables were on that
    default; the two that behaved were the two that had happened to say
    `setWordWrap(False)`, and nothing made that the rule. `single_line()`
    (`ui/widgets.py`) is the rule now, and installing an `ElideItemDelegate` applies it
    to the view it is given — eliding a cell's text *is* a statement that the row is one
    line tall. A test reads the UI sources and fails if a new table says neither.
  - The delegate's margin — how much of the column it assumed the style would not give
    the text — was a flat **ten pixels**, close to right and not right. The style takes
    the item rule's padding *and* a focus-frame margin of its own, so the text elided by
    the delegate came out a few pixels too wide and the style quietly elided it a second
    time. That second pass is the wrapping one. It asks the style for the real text rect
    now (`SE_ItemViewItemText`, less `PM_FocusFrameHMargin` + 1, which is what
    `QCommonStyle::viewItemDrawText` uses), so the elide in the delegate is the only one
    that happens — and a character stops being lost to the rounding even where nothing
    wrapped.

  Either fix alone removes the symptom; both are in, because a one-line row has no
  business wrapping whatever the margin says. Measured on the *ModOps that could not be
  read* dialog: the mod's name reached 44% of its column before and 95% after.

  The one table left wrapping on purpose is the *Load from game files* dialog's, whose
  rows are sized to their contents and number one or two — there a long path wraps onto
  a second line and the row grows to show it, which loses nothing.
- **Copy buttons say so.** *Copy* (next to the ModOp path) and *Copy XML* used to change
  the clipboard with no visible sign anything happened. `flash_button_feedback`
  (`ui/widgets.py`) relabels the button to "Copied!" and disables it for a moment, then
  restores it — the only feedback a click on a Copy button ever needs.
- **A wide window shares its extra width across more than one column.** A `QHeaderView`
  can only auto-grow one section at a time; the field editor's tree left that to "Refers
  to" alone, and the asset search results table left it to "Name" alone, so those two
  columns swallowed every extra pixel a wide window gave them while the columns actually
  holding the longer, more-often-truncated text — Value, Original, Internal name,
  Template — stayed pinned at their initial width. Both now use the same
  resize-and-redistribute approach already built for the *Mod changes* table
  (`fit_columns` in `ui/widgets.py`): Property (or GUID) sizes itself as
  before, and everything else splits whatever width is left over, proportionally, on
  every resize. The *Mod changes* panel itself — the one whose columns get elided most
  often — also claims a bigger share of a wide window now: the main splitter went from a
  3:1 to a 2:1 stretch between the tab area and the side panel.
- **No view scrolls sideways because a column was dragged.** Dragging a column's edge
  out pushed the columns after it past the right edge, and the field tree scrolled
  sideways to reach them. `fit_columns` holds a view's columns to its width: a column
  dragged wider takes the room from the columns after it, nearest first; one dragged
  narrower gives it to the next; the last column's right edge is the view's. A stretched
  column becomes one more that shares, so it can be dragged too. Every view with a column
  the mouse can drag has one (`rows_table` and `listing_table` install it); a view whose
  columns only size to their contents or stretch is left to Qt. The *Mod changes* tree
  has one too, with a floor for each of its two columns, and the share it remembers
  across sessions is read and set through `FitColumns.shares()` and `set_shares()`; it
  once had a version of its own, which read a drag of the Change column's right edge as
  a new share where every other view undoes it.
- `EditorPanel.populate_edit_menu()` shares the same
  menu-building logic as the tree's context menu (`_build_item_menu()`), so the two never
  drift apart.
- **A geometry test has to open the tab it measures.** Qt lays a page out when it is
  first shown, so on a tab nobody has opened every widget sits at `y=0` and every column
  keeps the width it was built with. Two tests measured the asset editor from the Start
  tab and quietly stopped meaning anything: "the Add field button is on the Filter row"
  was true of two widgets both at zero, and "should not be on New asset's row" was the
  half that failed. They open the editor tab first now. The check that catches this is
  the assertion that something *differs* — same-row and column-growth tests fail loudly
  at `y=0`, while a test asserting only a single number would have passed for years.

- **Selecting a container isn't a warning.** The field editor's "This is a container…"
  hint used to reuse the same "Warn" (yellow) styling as the genuinely cautionary
  positional-path warning, even though selecting a container is routine. It's plain hint
  text now, styled like every other hint in the app, and reserved for the one case that
  actually deserves it. The hint row also has a fixed height, sized for its longest
  possible message, so switching between "no hint", the container hint, and the
  positional warning no longer grows or shrinks the tree above it.
- **The status bar's own label no longer paints a visible box around its text.** The
  blanket `QWidget { background: ... }` rule that gives most of the app's dark background
  also applied to the status bar's `QLabel`, painting it a slightly darker rectangle than
  the status bar itself. `QStatusBar QLabel { background: transparent; }` lets it blend in.
- **A label that reflects live state elides instead of forcing its layout wider.** The
  *Export* page's "Export to:" path used to grow the whole Preview panel wider with
  every character typed into the mod name: a plain `QLabel`'s `minimumSizeHint` tracks its
  full, unwrapped text, so an ever-longer export folder name meant an ever-wider minimum
  width, with nothing to stop the layout (and the window) from being dragged out to keep
  showing all of it. `ElidingLabel` (`ui/widgets.py`) elides against whatever width it is
  actually given instead, keeping the untruncated string as the tooltip and available
  through `full_text()`; a fixed, content-independent `minimumSizeHint` of zero width is
  what actually stops the feedback loop, not just the eliding itself. The same fix went to
  the *Export* page's preview-path label and the *Game files* step's file-path
  labels, which had the identical, just less frequently triggered, problem.
- **An asset survives holding fields named after the file's own structure.** The index
  streams the file and prunes as it goes, watching for `<Asset>` and `<Group>` end events
  — but both are *also* ordinary field names inside an asset (`<Group>` in 19 Anno 117
  assets, `<Asset>` holding a GUID in lists like `UnlockAssets`), and a field's end event
  arrives before its asset's. Pruning on one cleared the field and deleted every sibling
  recorded before it, out of an asset still being read: silently truncated assets, and
  about 15,000 links blanked across the two games. Every asset in both shipped files now
  round-trips through the index byte for byte.
- Texts live inside the index cache, so
  re-indexing — after a game patch, or a Studio update that changes the cache format —
  used to drop every localised name with nothing on screen saying why.
- *New/Open/Save/Save as/Project history* now lead the menu, ahead of
  *Import/Load base assets.xml/Re-index/Load localisation texts* — what the mod *is* reads
  before where its data comes from. *Open base assets.xml…* is also renamed to
  *Load base assets.xml…*: "Open" implied something you'd edit directly, which this file
  never is. Ctrl+O moved with the rename, from that action to *Open project…*, since that's
  the one a user reaching for the standard shortcut actually means.
- **…and then it was grouped, because it had grown to twenty-two rows.** The File menu
  had accumulated three quite different kinds of thing — the project, the game data it is
  measured against, and the other people's mods loaded beside it — all listed flat, 658
  pixels of them, and on a cold start thirteen of the twenty-two were greyed out. A reader
  looking for one entry had to read the lot. The kinds are submenus now: **Game files**,
  **Mods** and **Check the loaded mods** (and **Settings**, since folded into a single
  *Settings…* dialog), with the project entries and *Exit* left at the top level where
  habit expects them. Twelve rows instead of
  twenty-two, and what is on screen is the *shape* of the menu rather than its contents.
  Nothing moved out of reach and nothing was dropped. Three things changed besides the
  nesting:
  - **Open recent** is new. The Start tab has always listed the projects you had open;
    the File menu is where a lifetime's habit says to look for them and it did not have
    them. It fills when it is opened, not when the window is built, because the list is
    read off the projects folder as well as out of settings — one saved since, or by this
    window a minute ago, would otherwise be missing.
  - ***Mods reaching for the same thing…*** is new to the menu. The conflicts list was
    reachable only from a button in the *Mod changes* panel that appears while something
    is contested and disappears when it is not, so somebody who had seen it once and
    wanted it again had nowhere to look.
  - **A submenu with nothing live in it is greyed out whole, with the reason on it.** A
    submenu is a promise that there is something behind it; left enabled, one whose every
    entry is unavailable makes somebody hover, read a list of greyed lines and come back
    out, every time they pass. `_sync_file_menu()` works it out from the entries
    themselves on `aboutToShow` rather than from a list of conditions, so gating an entry
    later cannot quietly leave a submenu promising something it no longer has. In
    practice only *Check the loaded mods* ever closes: there is always a game to load
    files from and always a backup that can be put back.
- **…and then the kinds became menus of their own (17 Oct).** The grouping held for
  File, but *Mods* had grown to seventeen rows of its own: getting mods in, taking them
  out, the library's conversion both ways, Play, backups and the mod.io update check —
  the entry most worth finding, fourteenth in the busiest submenu of the busiest menu.
  The menu bar is now **File · Edit · View · Mods · Game · Check · Help**: *Mods* is what
  is in the project, *Game* is what the game loads and the two directions between it and
  the project (Play and Make this live one way, *Match this project to the mods folder…*
  the other, with the backups and a **Library** submenu for the conversion), and *Check*
  is the old *Check the loaded mods*. File keeps the project, *Game files*, Settings and
  Exit. Two things went with it:
  - **One way to add mods.** *Import changes from a mod…*, *Import your installed
    mods…*, *Add mods to this project…* and *Install a mod from mod.io…* sat together,
    three of them saying "import", and which one a project wanted was the reader's to
    work out — while *Add mods to this project…* was already the answer, choosing the
    folder's list, the library's or the one-mod import from what the project is. It is
    *Mods › Add mods…* now, and whichever dialog it opens has **From mod.io…** and **From
    a zip…** at its foot (`widgets.add_detours`), so the mods not on this PC yet come in
    the same way as the ones that are. *Import your installed mods…* was never an
    addition — it reads the whole folder in again with the game's switches — and is
    *Game › Match this project to the mods folder…*, beside its opposite. The one-mod
    import stays where it is needed: **Import one mod…** and **Open one of yours…** on the
    Start tab, and *Add mods…* in a project that has read no folder.
  - ***Fold loadouts into this one…* is gone**, with `core.folding`. It took back the
    loadouts the conversion made one per profile before loadouts had profiles; with no
    saved projects to keep working until the Studio is public, it had nothing to fold.
  A menu of the bar's cannot be greyed and still say why — a bar entry has no tooltip,
  and greyed it cannot be opened to see what is in it — so one with nothing live opens
  on a greyed row saying why (`_top_menu`, `_sync_menus`). Submenus keep the old rule.
- **…and the buttons, the same day.** A button is for what is done often from where it
  sits; a menu is where a thing is looked for. Both ways round had drifted:
  - **Up from the menus.** *Check mod.io for updates…* and *Check the whole loadout…*
    are on the Start tab's fourth card, which is about what the game will load, beside
    *Make this live…*. They are the menu entries' own buttons (`MainWindow._mirror`), so
    whether they can be pressed is decided once. A mod's row in the Mod changes panel
    has **Check for an update…**, which opens the same dialog on that mod alone
    (`ModioUpdatesDialog(only=…)`). **Add mods…** is at the panel's foot: getting mods
    into the list is what the list is for, and it was only on its right-click menu.
  - **Down, or out.** *Rewrite every mod…* is gone: it wrote the Studio's reading of
    other people's mods in place of their files, which nobody sharing a mod wants, and
    Play already builds every mod into the library. *Revert all*, which throws every
    change away, left the panel's foot for its right-click menu, beside *Revert
    selected*. The Asset editor's *Expand all* and *Collapse*, side by side with one of
    them always doing nothing, are one button that says what a click does, as the
    panel's is. On the Export tab, *Export your changes…* is the **Mod to export** box's
    last choice, **All the mods you changed** (offered with two or more), and goes out
    through the one Export button; *Copy XML* and *Save assets.xml…* are behind an
    **XML** menu button, so the row is the two ways out that share a mod.
  - **Names that clashed.** The fourth card had *Add to project* (read the whole folder
    in with the game's switches) one row from *Add to project…* (add the mods the
    project lacks, keeping its switches): they are **Match the mods folder…**, as the
    Game menu's entry is, and **Add these mods…**, beside the sentence naming them.
    *Check again* was the card's re-read and the Export tab's loader button: they are
    **Re-read the mods folder** and **Ask the loader again**. Settings' *Check for
    updates*, about the VS Code extension, is **Check for extension updates**.
- **…and Settings, in four tabs.** The dialog was eight group boxes in one scroll: the
  mod.io key the second, the loader the seventh, and finding either meant reading past
  the rest. It is four tabs now, by whose each setting is: **You** (creator name,
  language, appearance), **mod.io**, **Game and library** (where the library is, what
  Play starts) and **Advanced** (the loader check, the GUID ranges). Each tab is
  scrolled on its own, for the reason the one page was. The loader box had two
  buttons that were actions rather than settings — run the reading-rule probes, ask
  the Marketplace for a newer extension — and they are entries at the foot of the
  Check menu now, greyed with the reason when no loader is found. Settings keeps what
  each last said. Run from the menu, each says how it came out in the status bar, even
  when all is well: run on their own in the background they spoke only when something
  was wrong, and a menu entry that ends in silence reads as one that did nothing. The
  "which version is newest" sentence moved from the dialog to `LoaderController.newest_said`,
  for the status bar and the dialog alike.
  - **Then five, the same week.** *You* mixed how the Studio looks with how your mods
    are made, and *mod.io* was one line and a button that opened the account dialog.
    The tabs are now **General** (language, appearance; first), **Authoring** (creator
    name, GUID ranges), **mod.io**, **Game and library** and **Advanced** (the loader
    check). The account dialog *is* the mod.io tab (`ModioAccountPanel`): every "Set
    up mod.io account…" button opens Settings there, and what it did is said in the
    status bar when Settings closes. That reverses the earlier call that the account
    was "too much to inline": it is a tab of its own now, not a line among others.
    Its buttons took new mnemonics, since a letter is the dialog's on every tab, and
    the tabs have none.
  - **The GUID range is a setting.** It was a choice only in the New asset dialog,
    kept on the project when it differed from the creator name's listing. Settings
    now names one per game (`Preferences.guid_range`), for a listing that spells your
    name another way. New asset starts on it (`new_asset_dialog.your_range`: Settings,
    then the creator name, then personal use), counts it as yours when warning about
    somebody else's range, and keeps a project's choice only where it differs from it.
    The sentence saying whose a range is (`describe_range`) is shared by both.
- **Right-click menus where there were none.** The Studio had exactly two — the field
  editor's tree and the *Mod changes* list — and both are good ones. Everywhere else, a
  row's actions were whatever buttons happened to be near it, which is fine when the
  button is under the list and not fine when the thing you want has no button at all.
  Five lists gained one, on the same principle the *Mod changes* menu was built on: an
  entry that does not apply is shown disabled with the reason rather than hidden, and the
  menu is built apart from being shown so what it offers can be tested. The five, and
  what each was missing:
  - **The reference graph** — see [References](#references). The one the graph most
    needed: no way to copy a GUID off a box at all.
  - **Search results** — see [Finding an asset](#finding-an-asset). Same gap: the GUID is
    in the third column and there was no way to get it into the clipboard.
  - **The Export tab's validation list.** A row names a change that would not apply, and
    reading it was all you could do: getting to the change meant finding it again by hand.
    Double-clicking a row now opens it in the editor — the same jump the *Mod changes*
    list makes — and the menu adds **Copy the path** and **Copy what it says**, for the
    case where the answer is not in the Studio at all and the row has to be quoted to
    whoever wrote the mod.
  - **ModOps that could not be read.** This dialog exists to be acted on *somewhere
    else*: the op has no narrow equivalent, and what you do about it is ask whoever wrote
    the mod. Getting the op out meant selecting the row, clicking into the pane below,
    selecting all of it by hand and copying — for the one thing the dialog is for.
    **Copy this ModOp**, **Copy why it was not read**, and **Copy all three** as one
    paste: which mod, which op, and why.
  - **Mods reaching for the same thing.** Opening the asset is what a double-click does
    and the hint says so; the menu says it where somebody looking for what a row can do
    would find it, and adds the field path — which is the whole content of a message to
    the two mods' authors, and is not on screen in full, because the column shows the
    field's tail and keeps the rest for the tooltip.
  - **Lately** on the Start tab, the sixth: one click opens a project, which left nowhere
    to ask the two things a list of files is expected to answer — where is it, and what
    is it called on disk. The row deliberately shows neither.

  Considered and left alone: the mod.io *updates* and *get* dialogs, the mods-folder
  dialog, project history, backups, and the asset and field pickers. Every one of those
  is a modal whose row action is a button sitting directly under the list it is about, so
  a menu there would duplicate a control already within a few pixels of the cursor
  without adding anything a row cannot otherwise do. A context menu earns its place by
  offering something otherwise unreachable, not by existing.
- **The Start tab's recent projects list eight.** The loadout the game loads, which is
  on top whatever was opened since, wears Play's ▶ in bold with a rule under it: on top
  with nothing said, it read as a sorting mistake.
- **Where everything is** is *Help ▸ Studio folders*, and a line of the Start tab's
  project card, beside who you publish as. The card had a line for the library alone,
  with **Open folder**: only the settings had said where it was, and the default is
  under AppData, which Explorer hides. But the library was one of four folders called
  AnnoModdingStudio, under three parents, and each of the others was named only where
  it was used, so whoever looked afterwards for where something went had nowhere to
  look. The dialog lists them all, each with a `?`, and the library keeps the first
  entry of *Game ▸ Library*. The settings moved beside the library, which made it
  three folders under two parents: what they hold is true of one PC, which a roaming
  copy is not.
- **References… is the last button of the editor's top row**, where *Edit this asset* is
  on the References tab, and the two are as wide as each other: to the graph and back is
  one place on the screen clicked twice.
- **The graph's menus and tooltips are kept small.** An asset's name in a menu entry is
  cut at 32 characters, in a tooltip at 40, a field's path from the middle at 50; a
  node's tooltip lists six links and an edge's three. A hub's name and links made one
  as wide as the screen.
- **Win+V opens beside the field.** The clipboard history, the emoji panel and
  Magnifier find the text cursor by the window's system caret. Qt makes an invisible one
  only while the input panel is asked for, which only a click on a field already focused
  did; `caret_follows_focus` (`ui/widgets.py`) asks for it whenever a field that takes
  text has the focus.
- **Every label is plain text unless the Studio asked for rich.** Qt reads a label's
  text as HTML when its first line looks like markup, and so does every message box and
  tooltip, since each is a label. Much of what the Studio shows is somebody else's: a
  mod's name, a modinfo option's label, a mod.io collection's summary. The security
  review before the beta (17 Oct 2026) had an option labelled
  `<img src="file://host/share/a.png">` make Qt fetch that file as the Options dialog
  opened, which for a UNC path is an SMB connection that can give away the user's
  password hash. Escaping each value where it is shown would have been a rule for
  hundreds of places. Instead `widgets.external_text_stays_plain` turns every label
  still in Qt's `AutoText` to plain as it is first polished, before it is drawn. A label
  whose text read as plain shows exactly as before. The Studio's own markup, about twenty
  labels, asks for it with `widgets.rich_label` or `show_rich`, with each value from
  outside through `html.escape`; the About box, which Qt builds, opens inside
  `widgets.studio_rich_text`.

## Tests

It used to live only in `conftest.py`,
which only pytest loads — so this very command ran the suite against the real home and
cache, writing "My Anno Mod" projects into Documents and indexes into the real cache.
