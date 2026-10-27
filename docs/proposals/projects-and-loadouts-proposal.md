# Projects and loadouts: a proposal

A mod can be in several of the Studio's files at once. Your own mod is in the
file you write it in, and in every loadout you add its build to. Each of
those files holds its own copy of the mod, and each can write the mod into
the library. Nothing says which of them the mod belongs to, so two files can
overwrite each other's work without a word, and one can go on showing a mod
as it was several builds ago.

The cause is that one file does two jobs. It is the **loadout** you have
open: which game files, which mods in which order, which of them are on. And
it is where **each mod of yours** is kept: its ModOps, its details, its
builds, its mod.io page. This proposes splitting them:

- The Studio opens one **loadout** at a time, and a loadout is the only thing
  it opens. Every set of mods is a loadout, even one with a single mod in it,
  because it is what the game loads when you press Play.
- Each mod of yours is kept in a **project** of its own. Every loadout that
  has the mod refers to its project, and an edit to the mod goes there,
  whichever loadout it is made in.
- Everybody else's mod is kept in the library and is never edited. What you
  want different about it goes into a project of yours: a patch, a loadout's
  own mod, or a fork.
- Saving writes the loadout and every project that changed, all or nothing.
  A cache that can always be rebuilt answers the questions that cross files,
  such as which loadouts have a mod.

Written on 20 October 2026 as *Who owns a mod*, against `main` at `8424d6a`,
and rewritten twice on 21 October after three reviews. It came out of the
[Export tab proposal](export-tab-proposal.md), whose section 4 (adding your
mod to the game you play) depends on it.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (24 October 2026).** Proposed. Phase 1, the rename, is built, and
phase 2, the journal (section 6): `core/saving.py`, through which a loadout is
saved, and a Studio settles at start any save the last one left half done;
nothing saves several files through it yet. So is phase 9 as far as it goes
before the split: the screen says "author" for who made a mod, and *kept in
My Mod's project* waits for projects. So is phase 4a, the first half of the
split's 4.2 done inside today's single file: the loadout's own mod keeps the
ModOps the Studio writes for each edit as its record (`Loadout.narrow`,
`core/narrow.py`), written at the end of every step, undone with its changes,
saved, and what its export writes. Its changes are still made with them
rather than read out of them, and where they stand is still worked out at
export; both are for 4b, the split itself, with the patches' and a carried-on
mod's edits. So is 6a, the half of phase 6 that needs no projects: a mod
whose folder holds another version or build than the loadout read is read
again when the loadout is opened and before Play (`stale.follow`), which puts
section 2.2 right for today's files. So is phase 3, the cache, over the
loadout files (`core/filecache.py`); the warning for a loadout opened from
outside the Studio's folders is not. And the first part of phase 4, the split
itself, is built as 4b-1, projects on disk (`core/projects.py`): the
loadout's own mod, each patch and a carried-on mod are each kept in a
`.annoproj` file in `projects/`, with their details, ModOps, builds and
release facts, and the loadout file names them with the revision its
readings were made from. Saving writes the loadout and each project whose
part moved, through the journal; the unsaved question names them. A project
another loadout changed since is opened as it is now and held: no edit goes
into it from here and no save writes over it, until phase 6b reads it again.
Save as, Fork and a copy of the live loadout make the copy's own mod a
project of its own. The window still holds one `Loadout`, so the split is in
the files and not yet in the types. So is 4b-2: the ModOps the Studio
writes for each edit (4a's `Loadout.narrow`) are written for a patch's and a
carried-on mod's edits as for the loadout's own mod's, so each project keeps
its mod's edits as ModOps; undo puts back the ModOps of the mods a step
moved and no others, and a held mod's are left as they were read. So is
4b-3, as far as *Open one of yours…* goes: the cache reads the projects too
(`filecache.project_keeping`, `filecache.loadouts_with_project`), and a mod
of yours whose ModID a project keeps already is not made a second project.
Where loadouts have that project, one of them is offered to open instead,
saying so where the folder is an older build than the project's last; where
none has it, the loadout carries on in it. The same goes for a mod of yours
read in by *Change a mod you downloaded*, and a mod with your name made a
project in *Currently editing* takes up the project its ModID has, whichever
loadouts have it. A project taken up so is read back, not pointed at: what
the loadout read of the folder goes, and the project's ModOps, details,
builds, history and edits inside other mods are read where the mod stands
(`readback.join`), so its save writes nothing over the project, and what
was done to the mod there before is given up and said. One patch to a
mod as well: a patch's project says which mod it patches, and a loadout
making a patch of a mod you have patched elsewhere takes up that patch's
project (`filecache.patch_project_for`, `readback.join_patch`), read where it
stands in this loadout at once, under the folder name it was built as, and
says which loadouts have it. A fork's own mod, and a copy's of the live
loadout, is a mod of its own: its own project, a name after the copy's and a
ModID made from it, and none of the first mod's builds, mod.io page or
publish record (`Loadout.own_mod_anew`). And the patch over a loadout's
whole list is named after the loadout, "Main patch", rather than after the
game, so two loadouts' patches never build into one folder under one ModID;
a rename of the loadout takes it along, and a name you gave it stays
(`changes.list_patch_name`, `LoadoutFiles.name_list_patch`). So is phase 5, section 7
(`core/builtby.py`): the library's registry records, beside `source: built`,
the project a copy was built from, as a loadout writes its path, and the ModID
and build number it was built as, never in the modinfo. A build into a folder
another project built, by Play or by an export into a mods folder, is stopped
and names that project, and Play's question says so; a folder whose project
is gone is nobody's. A mod of yours with no project yet names the one its
next save will make. The loadout's own mod renamed moves the folder its
project built, with its link and record, which keeps the names it had; a
loaded mod of yours keeps its folder in a mods folder, where the loadout
knows it by that name, and goes out under its new name only where it is
exported to share. A loadout opened after a mod of it moved takes the new
name, matched by ModID, and by the name it had where the ModID went with the
name, and is saved at once. Save as takes the registry entry to the copy's
own project; a fork and a copy of the live loadout leave it with the first,
and build under a name and ModID of their own.
So is 6b, the half of phase 6
that waited on the split (`core/readback.py`): a project another loadout
changed is read again when the loadout is opened and before Play, its
ModOps where the mod stands in this loadout's load order, in the background
as a stale reading is; the ModOps the Studio wrote for each edit come back
as that mod's changes, each written as its ModOp, and one whose changes
would be written as another ModOp is kept as the op it is; then its hold is
let go of. Edits this loadout made to it and never saved into it are given
up, and counted. A project that is not where it was is looked for by its
ModID; one that cannot be found is said to be missing, naming the path, and
a mod of yours is then read from the library's build of it, not to be
edited, followed by its build number, until the project is found again. So
is the loadout's own mod: the build its project made, by the library's
registry, or else the one copy with its ModID, under the name and ModID
that build's modinfo says, since those went with the project
(`readback.stand_in`, `stale.follow_projects`). An edit to a mod of yours
whose project is held -- missing, unreadable, or changed in another loadout
and not read again yet -- or one such a mod keeps inside somebody else's
ops, is taken back as it is made and said, since no project could keep it
(`undo.held_in`); so are its details on the Export tab. Joining a
loadout to a project another already has is built for a patch (above): the
project is taken into the loadout and read back.
So is phase 7, the two
histories (section 9, `core/histories.py`): each mod of yours keeps its
history in its project, each edit noted with the loadout it was made in, and
the loadout keeps the history of its list, with which checkpoint of each
project it stood at. A project's restore names the loadouts it changes; a
loadout's puts its list back, reading a mod taken out since again from its
folder, and *Restore, with each project as it was then* restores each
project after it. Build numbers, the mod.io link and what was published are
never restored, and "since the last release" is measured against the
project's history.
So is phase 8, somebody else's
mod is never edited (section 5). *Currently editing* offers mods of yours
only, each saying its reach: *Your patch for Their Mod: wherever you use Their
Mod* and *This loadout's own mod: only in this loadout*, with *A new patch
for…* for a mod of theirs that has none. Changing one of their ops in place
asks the three ways on, unless where it goes is known already. An edit
inside their ops is kept by your patch or by the loadout's own mod, whichever
changes went to (`Op.kept_by`, `Change.kept_by`, a fifth item on a `taken_out`
entry, a held edit's `kept_by`; `core/inside.py`), and the keeper's project
keeps it under `inside` (`projects.split`, `projects.join`). Play's generated
copy holds the patch's edits and then the own mod's, and the patch's export
works out its own alone. Making a patch moves nothing into it. A fork
(`core/forking.py`) is a copy of the original's files beside the library,
with a ModID of its own and you as its author, deprecating the original,
swapped in at its place with its settings; it says what it means first and
offers to fold your patch in, naming the loadouts that keep using the patch.
A mod with your name on it that no project keeps (`ModLayer.kept` unset) is
read like anybody else's: *Open one of yours…* and *Currently editing* offer
to make it a project. A patch's edits are tied to their ops by text, since
the uids a reading gives their ops are one loadout's alone: a loadout that
reads their mod for itself finds an op the patch switched off by its text;
an op it put among theirs goes after the op of theirs it followed and into
the groups that open as its did (`projects.placed_in`, `put_among`), where
the project is opened, joined or put back, and again whenever their mod is
read again (`inside.keep_places`); and where the patch's project was
changed in another loadout, the mod it patches is read again from its
folder, so an op it took out there is taken out here and one it put back
comes back (`stale.KEPT`). Unloading their mod takes nothing out
of your patch: what it keeps inside that mod is set aside, its project goes
on keeping it, and reading the mod in again puts it back
(`Loadout.set_aside_kept`, `inside.take_back`); what the loadout's own mod kept
inside it goes with the mod, as before. A project's history holds those
edits as its project does: each checkpoint has what the mod keeps inside
their ops, and a restore puts them back and reads those mods again
(`Checkpoint.inside`, `inside.restore`). So is a project's file following
its mod's name (4.7): renaming the mod moves its project to a file named for
it, in the same save as the name, and every loadout that refers to it is
pointed there in that save, without reading the project again
(`projects.path_for`, `projects.refer`); the journal takes the old file away
(`saving.write_all`). Nothing else is built. Two things keep
their old shape until a later phase gives them
somewhere to go: the `loadout` flag is `own_mod_loads_last`, a fact about the
loadout's own mod that its project will keep after the split, and *Add mods*
still asks it which list to open on an empty loadout; and the line the Studio
writes into Anno 117's profile is pinned, so it still says project. Section
15 lists what the reviews settled, the three questions that were open among
them; none is open now. The behaviour in section 2 was traced in the code,
not run.

**The words in this proposal.** *Project* and *loadout* mean what they will
mean once this is built: a project keeps one mod of yours, and a loadout is
what the Studio opens. What the Studio opens today is called *today's
project file* (`changes.ModProject`, saved as `.annoproj.json`), which this
proposal splits into the two. *Author* is who made a mod; which file keeps
a mod has no word of its own (section 11).

## 1. What today's project file holds

**Everything readable about every mod in it.** Each loaded mod is a
`ModLayer`: its modinfo as its author wrote it (`declared`), the folder it was
read from, its version and options. Beside the layers are every op the
Studio read, as written and with what each came to (`ops`), the ones it could
not read, carried as text (`unread`), and every change, whether made here or
read out of a mod (`changes`, with `Change.authored` telling which). It does
not hold a mod's other files: textures, Lua, anything the Studio does not
read. Those stay in the folder, and a build copies them across from there
(`build.carry_folder`).

**The file's own mod is the file.** Its details are the file's own fields
(`mod_name`, `creator`, `version`, `mod_id`, `scripts`, `guid_range`, and so
on), and its changes and ops are the ones whose origin is "". A patch, or a
mod of yours read back in, is a layer with its details on the layer.

**Facts about one mod are kept per file.** Build numbers (`builds`, by
origin), the mod.io link (`modio_mod_id`) and the last publish (`published`)
are fields of the file, not of the mod.

**History and undo are per file.** A checkpoint holds the whole file's
changes and ops (`Checkpoint`). Undo records what moved in a step rather than
a snapshot (`core/undo.py`), with one history for the file.

**One Studio, one file.** One Studio runs over one set of folders
(`ui/one_studio.py`), and it has one file open.

**The library holds one folder per name.** Its records (`installed.json`,
`links.json`) are keyed by folder name, not by ModID (`library.registry`,
`library.links`). A build of yours is recorded as `source: built`
(`build.write`) and nothing more: not which file built it. Your own mod's
folder is named `[Category] Name` (`generator.folder_for`), so renaming it
builds a second copy beside the first, and two mods with the same name and
category write the same folder.

**A loadout is a saved flag.** `ModProject.loadout`, written by the Start
tab's third card (`set_up_as`) and carried by a fork. Nothing changes it
afterwards. The Export tab proposal's "main loadout" was the live file if it
had this flag, else the last live one that did: a flag the user cannot see
combined with a history the user cannot see. This proposal replaces it.

## 2. The problem

Take two of today's project files:

- **A** writes a mod of your own. Play built it into the library as
  `[Gameplay] My Mod`.
- **B** is a loadout. It added that library copy through *Add mods ▸ From
  the library*, and so holds its own copy of the mod.

### 2.1 Editing the mod in B overwrites A's build

1. B takes the mod for yours, because it has your ModID or your author name
   (`patching.is_mine`). So an edit goes into the mod itself. It does not go
   into a patch or a generated copy: those are for other people's mods
   (`play.generated_copies`).
2. B's Play builds every mod of yours with an edit (`build.mine`). It finds
   the mod's link in the mods folder and writes into the library folder
   behind it (`build.destination`). That is the folder A built.
3. A never hears of it. A's next Play or export writes A's version back over
   B's edits.
4. B's history goes on saying its edits are there. The game has them only
   until A builds again.

### 2.2 Rebuilding the mod in A leaves B behind

- The check for stale readings (`core/stale.py`) marks a mod when what loads
  before it changes, or when the reader changes. It does not look at whether
  the mod's own folder changed.
- The only folder comparison is a version check inside the loader check
  (`loadercheck._updated_since_read`). A rebuild under the same version
  string gets past it.
- If B has no edits to the mod, the game loads A's latest build, but B shows,
  checks conflicts against and validates the old copy. If B has any edit,
  B's next Play also undoes everything A built since B read it.

### 2.3 One file doing two jobs

Both of those come from one file being both the loadout and the place the
mods in it are kept. So do these:

- A and B each count builds of the same mod, in their own `builds`.
- Only the file the mod was linked from knows its mod.io page.
- An earlier draft had B write its edits into A's file. That raised questions
  with no good answer while A's file is also a loadout: when B may write into
  it, whether A should say so, and which loadout an edit is measured in.

None of this needs the Export tab proposal. 2.1 and 2.2 can happen today,
through *Add mods ▸ From the library* and an edit.

## 3. What is proposed

1. **Loadouts and projects** (section 4). The Studio opens a loadout and
   nothing else. Each mod of yours has a project. A loadout refers to
   projects and to library mods, and works out what they come to.
2. **Two kinds of mod** (section 5). A mod of yours is kept in its project.
   Everybody else's is kept in the library and is never edited.
3. **Saving is all or nothing** (section 6), through a journal.
4. **The library records which project built a copy** (section 7). A mod is
   known by its ModID.
5. **Loadouts follow their mods** (section 8).
6. **Two histories** (section 9): a project's and a loadout's.
7. **A cache for questions across files** (section 10), and the Studio's
   folders as the rule.
8. **Author on screen, and no word for which file keeps a mod** (section 11).
9. **The rename** (section 12): today's project file becomes the loadout in
   the code as well, before anything else moves.

## 4. Loadouts and projects

### 4.1 What each holds

| In today's project file | Goes to |
|---|---|
| `mod_name`, `creator`, `description`, `category`, `version`, `mod_id`, `scripts`, `scripts_folder`, `difficulty`, `game_setup`, `guid_range` | the project |
| a patch's or a mod of yours' details on its `ModLayer` | that mod's project |
| the ModOps of a mod of yours, written here or typed, with each one's note, switch and identity; its `unread` ops | its project, as ModOps (4.2) |
| `taken_out`, `held_edits`, and edits inside somebody else's ops | your patch's project for that mod (5.3) |
| `builds`, `modio_mod_id`, `published` | the project |
| `history` | split: a project's history and a loadout's (section 9) |
| `game`, `base_*` (the game files read against) | the loadout |
| `layers` | the loadout, as references with their readings (4.3, 4.5) |
| every `Change`, made here or read out of a mod; `read_stack`, `read_by`, `own_stale` | the loadout: they are worked out (4.2) |
| `profiles`, `profile`, `mods_folder`, `provenance`, `listed`, `editing` | the loadout |
| `loadout`, `aim`, `is_the_mod`, `own_mod`, `home` | gone or replaced (4.6, section 12) |

### 4.2 A project holds nothing a loadout works out

A project keeps its mod as written, and nothing that depends on which other
mods are loaded or in which order. So it keeps ModOps, not changes. A
`Change` records the value it replaced (`old_value`), and that value is
whatever the mods before it left there: it is a reading, and readings are a
loadout's.

**An edit in the asset editor becomes a ModOp as it is made.** Today a
change is the record and its ModOp is written from it at export. Here the
ModOp is the record, as a ModOp typed in the XML view or the Mod file tab
already is, and each loadout works out the change it comes to. This is the
largest change to how edits are kept, and it follows the
[ops-as-written proposal](ops-as-written-proposal.md)'s direction: what is
typed is what is stored.

**What a project keeps that names other mods** is still its own, because it
is what the mod says, not what a loadout works out:

- what it declares about other mods: which mod it patches, which it needs,
  which it loads after;
- a patch's edits inside the patched mod's ops, each tied to the op it edits
  by that op's text, as `core/held.py` follows them across updates.

### 4.3 How a loadout refers to a mod

- **A mod of yours** by its project's path and its ModID. A project that has
  moved is found again by ModID through the cache (section 10). One the
  Studio moved, as its mod was renamed, is pointed at where it went (4.7).
- **Anybody else's mod** by its library folder and its ModID, as a layer does
  now.

### 4.4 Opening, editing and saving

- **Only loadouts open.** *File ▸ Open* and opening from Explorer take a
  loadout. A project is reached through a loadout that has it, or added to a
  loadout through the Studio (*Add mods*, the Export tab's *Add to
  loadout…*).
- **Opening a loadout opens the projects it refers to** along with it. They
  are part of the session, not separate things the user opened.
- **An edit goes to the project of its mod**, whichever loadout it is typed
  in. Where it goes is chosen in *Changes go to*, as now.
- **One undo history for the open loadout.** A step can move things in
  several projects, and is undone as one. Undo records what moved rather than
  a snapshot, so this needs it to record moves among ModOps as well as among
  changes (4.2).
- **Save writes the loadout and every project that changed**, all or nothing
  (section 6). The unsaved-work question lists the projects it will write.
- **Only one loadout is open at a time**, as only one file is now. Two
  loadouts never have a project open at once.

### 4.5 Readings belong to the loadout

A loadout reads each project's ModOps in its own load order and keeps what
they come to, as today's project file keeps its readings, beside the version
of the project they were read from. There is no "the loadout a mod was
written in". The one a mod is tested in on its own is a loadout with nothing
else in it.

### 4.6 What the Start cards make

- **Write a mod of your own:** a project, and a loadout with only that mod in
  it, to test it in. The card's advice, "keep each mod in its own project",
  becomes simply true.
- **Change a mod you downloaded:** the library mod, a project for your patch,
  and a loadout with both.
- **Play a set of mods:** a loadout. Its own mod, the patch over its whole
  list, is a project like any other, used only by that loadout.
- **Open one of yours…** reads a mod folder of yours. **One project per
  ModID:** where a project for that ModID exists, it is not made again. The
  Studio offers the loadouts that already have that project, and says so
  where the folder differs from the project's last build, which usually
  means an older export. Where none exists, it makes the project and a
  loadout with only that mod in it.

What today's `aim`, `is_the_mod`, `own_mod` and `home` say is then said by
which files exist: a loadout, and the projects it refers to.

### 4.7 A project's file is named for its mod

A project is `projects/<its mod's name>.annoproj`, numbered where another
project has that name (`projects.new_path`). It follows the mod's name.
Whatever writes a new name into the project moves the file, in the same
folder and the same save, to the name a new project would get
(`projects.path_for`). There are two ways to write one:

- **The rename on the Start tab**, where the loadout is the mod or the patch
  over its list was named after it. It writes the name at once, and nothing
  else that is unsaved (`changes.name_in_file`), so the file moves at once.
- **The name field on the Export tab**, for a patch or a mod of yours the
  loadout is not. The name is a detail like any other and goes in with the
  next save, so the file moves at that save.

A save also moves a project whose file is named for a name other than its
mod's. It moves the file as it is, at the revision it is at, so a file from
before this rule is put right by the next save of a loadout that has it. A
new name that differs only in case, or the same name numbered, leaves the
file where it is.

The same save points every loadout that refers to the project at the new
file: its reference, and its history's checkpoints, which bring a mod back
from its project (`projects.refer`). These are the loadouts the cache finds,
in the loadouts folder and beside the loadout saved
(`filecache.loadouts_with_project`). The journal writes the new file and
takes the old one away (section 6), so the move happens with the rest of
the save or not at all. A move alone changes no revision, so no loadout
reads the project again because of it. A revision that changed nothing but
the name counts as read by a loadout that had read the one before
(`projects.Move.renamed`). A loadout the cache does not look in, or one that
no longer has the mod but has it in a checkpoint, finds the project by its
ModID (4.3), as it would one moved by hand. The library's record of what
the project built follows it (section 7).

## 5. Two kinds of mod

| The mod | Where it is kept |
|---|---|
| A mod written here | its project |
| A patch: your changes to somebody else's mod | its project, like a mod written here (5.1) |
| A loadout's own mod, the patch over its whole list | its project, used only by that loadout |
| A fork: somebody else's mod made into one of yours | its project, with a ModID of its own (5.4) |
| A mod downloaded from mod.io, or installed from a zip | the library, as its author shipped it, never edited (5.2) |
| A folder of your own the library refers to (a checkout you edit elsewhere) | that folder, edited where it is |
| A mod with your name on it and no project | nowhere yet (5.5) |

### 5.1 A patch is a project like any other

A patch says how somebody else's mod should have been, in general: wherever
you use that mod, not only in the loadout the patch was made in. So it has a
project of its own, and every loadout that wants it refers to it. Making a
patch from inside a loadout makes its project.

A change that only makes sense in one loadout, because of what else is in it,
belongs in that loadout's own mod instead. *Changes go to* says the reach of
each choice:

- *Your patch for Their Mod: wherever you use Their Mod*
- *This loadout's own mod: only in this loadout*

### 5.2 Somebody else's mod is never edited

A mod you did not write is read, shown, checked and played, and that is all.
Trying to change one of its ops in place offers three ways on:

- **Put the change in your patch for it**: everywhere you use the mod.
- **Put it in this loadout's own mod**: only here.
- **Make your own copy of it** (5.4): a fork, which you then edit directly.

### 5.3 Edits inside their ops belong to a mod of yours

Today an edit can go inside somebody else's op: type over it, take it out,
switch it off. Play writes a generated copy where their op is switched off
with `Skip` and yours follows (`core/edits.py`, from the
[patches proposal](patches-proposal.md), phase 2). That keeps an edit that an
op of your own after theirs cannot say.

The mechanism stays; who keeps the edits moves. They belong to a mod of
yours, never to their mod, and *Changes go to* chooses which, with the same
reach as any other change (5.1):

- **Your patch for their mod** keeps an edit that should hold wherever you
  use the mod. Play writes the generated copy from the patch in every
  loadout that uses it. An export of the patch carries the edits that can be
  ops of its own and lists the rest, as now.
- **A loadout's own mod** keeps an edit that should hold in that loadout
  only. Play writes the generated copy from it in that loadout alone, and
  where both keep edits to one mod, one generated copy holds the patch's
  edits and then the loadout's own mod's, in that order, as their mods load.

### 5.4 Forks

A mod you want to change directly is made into a **fork**: a project of
yours, with you as its author and a new ModID. Before anything happens, the
choice says what it means:

- It is your copy now. Its author's updates no longer reach it.
- You should not share it without its author's permission.
- The library's copy, as shipped, is kept.
- *Anno 1800 only:* mods that need the original will say it is missing (see
  below).

Then:

- **It is swapped in for the original in the open loadout,** at the
  original's place in load order, with the original's mod options carried
  across (Anno 117 keeps them by ModID).
- **The fork deprecates the original.** Its modinfo lists the original's
  ModID in `DeprecateIds`, so both games' loaders drop the original whenever
  the fork loads (`HandleDeprecation`; see `core/dependencies.py`). In Anno
  117 a mod that needs the original then counts as needing the fork. Anno
  1800's loader does not carry a dependency across, so there those mods
  report a missing dependency, which is what the choice says first.
- **A patch you already have for the original** patches nothing once the
  original is dropped, so forking offers to fold it into the fork: its ops
  and its edits inside the original's ops are written into the fork's own
  ModOps, and the patch is taken out of the open loadout. The patch's
  project is kept, since other loadouts may still use the original; the
  choice names them, from the cache. Declined, the patch stays where it is,
  patching nothing while the fork is in the loadout.

### 5.5 A mod of yours with no project

A mod with your author name or your ModID on it that no project keeps: one
you wrote before the Studio, or whose project is gone. Opening it says that
nothing keeps it and offers to make a project, which keeps it from then on.
Until then it is read like anybody else's mod.

## 6. Saving is all or nothing

Each file is already written whole or not at all: `disk.write_bytes` writes a
`.part` file beside it and moves it over. What a save across several files
adds is a journal, so a failure between files is put right rather than left
half done.

1. **Write** every file the save changes as its `.part`, flushed to disk. If
   any fails, delete the `.part` files: nothing has changed.
2. **Commit:** write the journal, listing for each file its target, its
   `.part` and an `.old` name for what is there now. The journal existing is
   the point after which the save happens.
3. **Swap:** for each file, move the target to `.old`, then the `.part` to the
   target. A move that fails is tried a few times, since antivirus or a sync
   tool can hold a file open for a moment. If it still fails, every `.old`
   is moved back, and the save is undone.
4. **Finish:** delete the journal and the `.old` files.

**Recovery.** A Studio that finds a journal when it starts, under the
one-Studio lock and before it opens anything, completes or undoes the save
the journal describes. Nothing else writes these files meanwhile.

It completes the save where every file can still go forward, its `.part`
there or in place already, and undoes it where one cannot. That also tells
an undo that stopped half way from a swap that did, so the journal is never
written twice. The journals are kept in the Studio's local folder
(`places.local_home`, under `saving/`), which no sync tool reaches. A save
of one file needs no journal: moving one `.part` over its target is one step
already.

A save can also take a file away, as when a project follows its mod's name
to another file (4.7). That file has no `.part`. The swap moves it to its
`.old` and finishing deletes it, so an undo puts it back the same way it
puts back a file that was written over.

Windows has transactional NTFS, which would do this in the file system, but
Microsoft has deprecated it and advises against it.

## 7. The library records which project built a copy

- **A built copy records its project.** The registry entry gets the project's
  path beside `source: built`, and the ModID and build number it was built
  as. A project saved under another name takes the entry with it, as
  `library.follow_live` follows the live file now, and so does one moved to
  follow its mod's name (4.7).
- **This is kept in the library's records, not in the mod.** The modinfo's
  Studio block travels with the mod to mod.io and to other people. A path on
  this PC has no business there.
- **One writer per library folder:** the mod's project. A loadout's Play
  builds each of its projects that has moved on since its last build, into
  that project's folder. A build already records a digest of what it wrote,
  and `builds.plan` compares it with what writing now would be.
- **A mod is known by its ModID.** Its folder name is where it is put. Once
  the registry knows which project built a folder, renaming the mod moves
  that folder and its links instead of building a second one. Loadouts that
  refer to it take the new name when next opened, matched by ModID as
  `modfolder.match_keys` matches now.
- **Two projects cannot build one folder.** A project that would build into a
  folder another project built is stopped, and told which project that is.

## 8. Loadouts follow their mods

- **A project is read again when it has changed** since the loadout read it:
  the loadout keeps the version of the project each reading was made from
  (4.5), and compares it when opened.
- **Anybody else's mod is read again when its version moves.** The loadout
  keeps the modinfo it read (`ModLayer.declared`), and compares the version
  with the folder's when it is opened and before Play. One that has moved is
  marked stale and read again in the background, keeping what was done to it
  here (`stale.read_again`, `stale.take`). That costs a reading for every
  updated mod in every loadout that has it, and the cost is accepted.
- **A project that cannot be found** is said to be missing first, naming the
  path it was looked for at. Then the loadout falls back to the library's
  build of it, read like anybody else's mod and not editable, followed by the
  build number the Studio writes into its modinfo (`builds.declared_build`),
  until the project is found again (by ModID, section 10) or 5.5 makes a new
  one.

## 9. Two histories

Each history records only what its file keeps, and each can be seen and
restored on its own.

- **A project's history** is its mod's: its ModOps and details at each
  checkpoint, with each edit's loadout noted, and the edits it keeps inside
  somebody else's ops (section 5.3). Checkpoints come at an export and a
  publish, by hand, and around a restore, as now.
- **A loadout's history** is its list: which mods, in which order, which on,
  its profiles and options. Each checkpoint also records which version of
  each project the loadout had then.
- **Restoring a loadout can restore its projects too:** *Restore, with each
  project as it was then*. That is the loadout's restore first, then each
  project's, one after another, each an ordinary restore.
- **Restoring a project changes it in every loadout that has it,** so the
  question names those loadouts, from the cache.
- **Never restored:** build numbers, the mod.io link and the record of what
  was published. They are facts about what happened outside the file.
  Restoring a project to before build 12 must not hand out build 12 again.

## 10. The cache, and the Studio's folders

**Files are the truth; a cache answers across them.** Loadouts and projects
stay files, which can be read, compared and handed to somebody else, and
whose formats the tests pin (`tests/fixtures/formats`). Questions across
files go to a SQLite cache in the Studio's local folder (`places.local_home`),
the way the asset index is a cache over `assets.xml`:

- which loadouts have this mod, for *Add to loadout…*, the Export tab's
  status, and the question before a project is restored;
- which project keeps this ModID, for one that has moved (4.3) and for *Open
  one of yours…* (4.6);
- which library mods nothing uses, which `LibraryController.mentioned_keys`
  works out now by reading every file.

Each file's entry is read again when the file's time or size changes. The
cache can be deleted at any time and is built again from the files.

**Not in Documents.** Loadouts and projects live under
`Documents\AnnoModding` (`places.studio_home`), which OneDrive routinely
syncs, and a SQLite file synced while it is written can be ruined. The
library was moved out of Documents for the same reason. The local folder is
not synced.

**The Studio's folders are the rule.** `projects/` today becomes `loadouts/`
and `projects/`, and the cache reads both. A loadout opened from anywhere
else is opened with a warning that nothing else in the Studio can see it: it
is not offered as somewhere to add a mod, and the mods only it has can look
unused.

## 11. Author on screen, and no word for which file keeps a mod

Two ideas are easy to mix up: who made a mod, and which file keeps it.

- **Who made a mod is its author.** Prose on screen says "author", as most
  of it already does, and the glossary
  ([design standards](../standards/design-standards.md), section 14) has a
  row for it. "Creator" is left only where the screen names the modinfo's
  field itself (`Creator`), and the code keeps the field's name
  (`Loadout.creator`, `ModLayer.creator`).
- **Which file keeps a mod has no word of its own,** on screen or in the
  code. Every mod is kept in one of two places: a mod of yours in its
  project, anybody else's in the library. So the screen names the place: *kept
  in My Mod's project*, *the library's copy*. The code does the same:
  `project_of(mod)` gives the project that keeps a mod of yours and nothing
  for anybody else's, and a docstring says "the project that keeps it". The
  names in section 12 make this read naturally, since a mod of yours and its
  project are one thing.
- **Neither "owner" nor "source" is used for it.** "Owner" reads as a
  person, so it competes with "author", and the code already uses it for
  other things: the module that answers a question
  ([architecture](../architecture.md), section 2), a GUID range's owner.
  "Source" fits as an analogy, a project being to a library build what
  source is to a binary, but the library already uses it for where a copy
  came from: a registry entry's `source` (`built`, `zip`, `adopted`), and
  `library.source_of` for the copy a generated one was made from. Section
  7's entry would hold two of them.

## 12. The rename

The architecture says the code uses the word the user sees, and each word
means one thing ([architecture](../architecture.md), section 3). Today's
project file becomes the loadout, and "project" takes a new meaning. So the
code is renamed first, as its own phase, and the split is then written in
the right words from the start.

**Types and names.**

- `changes.ModProject` is the loadout: it becomes `Loadout`. That is 686 uses
  in 85 files, tests and `tools/` among them (the census tools import core
  names). Variables, the window's `project`, and `ui/project_files.py` follow:
  about 9,500 uses of the word, most of them mechanical.
- The new type for a project takes the name `Project` in a later commit, so no
  commit has one name meaning two things.

**The `loadout` flag goes.** Every loadout is a loadout, so
`ModProject.loadout`, `truth`'s `loadout` and the loader check's
`view.loadout` mean nothing. What they decide moves:

- that a loadout's own mod loads last and after everything: a fact about that
  mod, which its project keeps;
- "this loadout" against "this project" in what Play and the Start tab say:
  one wording;
- *Fork this loadout…*: for every loadout;
- the Start tab's status, and what *Add mods* opens on an empty loadout.

**A clash the other way.** `switches.FolderSwitches` is held as `loadout` in
`ui/mods_folder_dialog.py`, and `play.Plan.current` is commented "the
loadout as read". Both are what a mods folder has switched on, which is not a
loadout. They become `switches`.

**Files the Studio writes.**

- **Suffixes.** `.annoload` for a loadout and `.annoproj` for a project: one
  extension each, so neither is taken for the other or for today's
  `.annoproj.json`. They stay plain JSON, indented, readable in any editor;
  their first keys say their format and their kind, so a file renamed by
  hand is still told for what it is. Windows goes by the last extension
  alone, which is why `json` is not in either: `.annoload.json` could never
  be told from any JSON file, and `.json.annoload`, once the Studio is the
  program for it, is shown as `Main.json`. Only `.annoload` is ever
  registered, since only loadouts open (4.4). The release is a zip with no
  installer, so nothing registers it unasked: the Studio may offer to, for
  the user alone, and *Open with* works meanwhile. Where Windows names the
  type it says *Anno Modding Studio loadout (JSON)*, and the README says how
  to tell an editor these files are JSON.
- **Folders.** `projects/` becomes `loadouts/` and `projects/` (section 10).
- **`live.json`** records the live loadout: its `project` key becomes
  `loadout`.
- **Settings** such as `last_project_dir` and the recent list follow.
- **The pinned formats** under `tests/fixtures/formats` follow.

**Words people read.**

- The glossary's rows for *project*, *loadout* and *your mod* are written
  again: today it says a project "is one workspace, not one mod", the
  opposite of what it will mean. A row for who made a mod is added:
  *creator* in the rename, *author* since the fourth review (section 11).
- The architecture's rows for *project*, *loadout*, *Live*, *Checkpoint* and
  *history* follow.
- The German translation has 174 entries with "Projekt" and 207 with
  "Loadout". Each is read again, since most of today's "project" becomes
  "loadout".
- The README follows. The proposals stay as written, but for their status
  lines.

## 13. What it gives the Export tab

The Export tab is about a mod, and a mod of yours now has a project. Its
build numbers, its mod.io link and its last publish are the project's, so
they are the same in whichever loadout the tab is opened.

The Export tab proposal's *In your game* row no longer needs a main loadout:

- **Add to loadout…** lists your loadouts for this game, from the cache, with
  a tick box each, ticked where a loadout already has the mod.
- **The status names loadouts the user chose:** *In Main and Test loadout.*
- **Update** builds the project into the library. Every loadout that has the
  mod reads it again when next opened (section 8).
- A loadout's own mod is not offered: it belongs to its loadout. *But where
  the loadout is the mod (`is_the_mod`), what* Write a mod of your own
  *makes, it is offered: adding it elsewhere is the point (the Export tab
  proposal, 5.1, settled 24 October).*

Whether ticking the live loadout also makes it live again, and Play's third
answer, stay in the Export tab proposal, which is rewritten on top of this.

## 14. Phases

1. **The rename** (section 12): today's project file becomes `Loadout` in
   the code, files and words, and the `loadout` flag goes. Nothing is split
   yet.
2. **The journal** (section 6). A save of one file through it changes
   nothing, and every later phase saves with it.
3. **The cache** (section 10), over the loadout files, replacing the scan in
   `mentioned_keys`.
4. **The split** (section 4): projects, references, ModOps as the record,
   readings kept by version, the Start cards, *Open one of yours…*. The
   largest phase, so cut in two. **4a**: the own mod's ModOps as its record,
   inside today's single file, which needs neither the journal nor the cache
   and is the riskiest part, done first. **4b**: the rest, the split itself.
5. **The library records which project built a copy** (section 7). Built.
6. **Loadouts follow their mods** (section 8), in two halves. **6a**, built,
   needs no projects: anybody else's mod is read again when its version or
   the Studio's build number in its modinfo moves, and so is a mod of yours
   read from a folder, keeping what was done to it here. **6b** waits on the
   split: a project read again when it has changed, and one that cannot be
   found.
7. **Two histories** (section 9).
8. **Somebody else's mod is never edited** (section 5): *Changes go to* with
   its reach, the three ways on, edits inside their ops in your patch or a
   loadout's own mod, forks with the offer to fold your patch in, and a mod
   of yours with no project.
9. **Author on screen** (section 11).
10. **The Export tab's *Add to loadout…*** (section 13), in the Export tab
    proposal. *Built (24 October) as that proposal's phase 2*: a mod of yours
    added to a loadout that is not open refers to its project at a revision
    the loadout never read, and the loadout reads it when opened
    (`core/adding.py`). A loadout's own mod is offered where the loadout is
    the mod, as section 13 now says.

Files saved before this need no migration: nothing is released yet.

## 15. Settled

**20 October, first review:**

| Question | Settled |
|---|---|
| Who keeps a patch | It is a mod of yours, with a project of its own: a patch is how a mod should have been everywhere. A change for one loadout goes in that loadout's own mod (5.1) |
| Whether somebody else's mod can be edited in place | No. Change it through a patch or a loadout's own mod, or fork it, which says what that means (5.2, 5.4) |
| Where an edit made in another file than the mod's goes | To the mod's project (4.4) |
| A loadout's own mod | The loadout's, used only there (5) |
| What a stopped edit says | The three ways on (5.2) |
| A mod you wrote before the Studio | Opening it offers to make it a project (5.5) |
| Files outside the Studio's folders | The folders are the rule; anything else opens with a warning (10) |
| Reading other people's mods again when their version moves | Yes; the cost is accepted (8) |

**21 October, second review:**

| Question | Settled |
|---|---|
| Whether the file the Studio has open must be the file that keeps each mod | No. The Studio opens a loadout; each mod of yours has a project (4) |
| Whether a save across several files can be all or nothing | Yes, through a journal (6) |
| Whether mods could be kept as database queries | No. An op is XPath over a document, depends on order, conditions and includes, and adds to lists that have no keys; the [ops-as-written proposal](ops-as-written-proposal.md) keeps every op as its author wrote it because rewriting them narrow changed what 26% of mods did. The asset index is SQLite, but holds each asset as compressed XML beside a searchable summary, not as fields a query could update |
| Whether loadouts and projects should be kept in a database | No. Files stay the truth, readable and comparable; a cache that can be rebuilt answers questions across them, outside the synced Documents folder (10) |

**21 October, third review:**

| Question | Settled |
|---|---|
| Edits inside somebody else's ops | They belong to your patch's project, never to their mod (5.3) |
| Making somebody else's mod yours | A fork, with a new ModID, swapped in for the original in the open loadout. It deprecates the original, so the loaders drop it (5.4) |
| Names | *Project* for the file keeping a mod of yours; *loadout* for what the Studio opens, even with one mod in it (section 12) |
| What the Studio opens | Only loadouts. A project is reached through a loadout, or added to one; *Open one of yours…* makes a loadout for the mod, or offers the loadouts that have it. One project per ModID (4.4, 4.6) |
| What a project keeps | Nothing a loadout works out. ModOps, not changes; changes are worked out by each loadout. What the mod declares about other mods, and a patch's edits inside the patched mod's ops, are the mod's own (4.2) |
| History | Two, each restored on its own; a loadout's restore can restore its projects too, loadout first. Build numbers and publish records are never restored, and a project's restore names the loadouts it changes (9) |
| A project that cannot be found | Said to be missing first, then the library's build of it, not editable (8) |
| The proposal's title | *Projects and loadouts*: its own name should not teach "owner" |
| Suffixes | `.annoload` and `.annoproj`, plain JSON inside, and only `.annoload` registered (section 12) |

**21 October, fourth review,** answering the three questions the third left
open:

| Question | Settled |
|---|---|
| Edits inside somebody else's ops, for one loadout only | A loadout's own mod can keep them, and Play writes the generated copy from it in that loadout alone (5.3) |
| A fork of a mod you already patch | Forking offers to fold your patch for the original into the fork (5.4) |
| Who made a mod, and which file keeps it | "Author" on screen, not "creator", except where the screen names the modinfo's `Creator` field. Which file keeps a mod has no word of its own: not "owner", which reads as a person, and not "source", which the library already uses for where a copy came from (11) |

## 16. Open questions

None. The three left open by the third review are settled in section 15.

## 17. What this leaves alone

- The library's layout, its links and the rule that the Studio owns only the
  links it made and the copies it holds.
- Generated copies as the way Play gets your changes into somebody else's
  mod without touching the library's copy; only who keeps those changes moves
  (5.3).
- Play, profiles, and what a loadout does when it is live.
- The reader, and ModOps kept as written; only where readings are kept, and
  when a mod is read again, change.
- One Studio at a time over one set of folders.
