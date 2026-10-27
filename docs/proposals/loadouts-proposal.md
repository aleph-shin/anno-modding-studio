# Loadouts: a proposal

What the game will actually load, versus what a project says — and switching
between sets of mods without the mods folder becoming a pile. Written against
`core/loadout.py` as it stands, the way each game records a switched-off mod,
what the collections work left behind, and Mod Organizer as the shape people
reach for when they say "keep my mods separate".

**Recommendation in one paragraph.** The Mod Organizer idea that transfers is
the *profile*, not the virtual filesystem — and Anno 117 already ships one.
Skyrim needed a hooking DLL because loose files override whole; Anno's ModOps
merge, so mods coexist by design, and the only files that still override whole
are the meshes and textures the Studio never reads. So: do not get between the
game and its folders. Instead, first show the truth — *what will the game load
right now* — beside what the project says, with a button to make either match
the other; then let a project declare that it *is* the whole loadout, so
switching to it switches everything else off; then give loadouts names, as
117 profiles and as 1800 rename sets. Keeping the mods themselves in a library
outside the mods folder is the one genuinely MO-shaped thing, and it waits on a
single test.

**Status (1 October 2026).** Built, then superseded by
[playing](playing-proposal.md) and [profiles](profiles-proposal.md); read it as
the record of how the Studio got there, not as how it works. Since then Play
switches off every mod a project does not mention, for every project, so
`ModProject.exclusive` is `ModProject.loadout` and only says what the project
is for; Anno 117 gets one shared Studio profile, *Anno Modding Studio*, not one
per project; a loadout's named sets of switches are `Profile`s inside it;
*Switch installed mods to match…*, the *whole mod list* box and *Switch them
off* are gone, Play and *Make this live (without starting the game)…* in their
place; `core/loadout.py` is `core/switches.py`; and `ModLayer.from_collection`
holds the collection's name.

**As first built.** Phases 1 to 3 are built: `core/truth.py`, the Start tab's fourth
card with the sentence and the *whole mod list* box, `ModProject.exclusive`,
a sync that for an exclusive 117 project writes a profile of the project's own
and points `profile.txt` at it (and for 1800, or a project that is not
exclusive, edits the switches as before), `ModLayer.from_collection` filled
from what the project remembers of each install, and *Switch off a
collection's mods…*. The README describes them as they are. Three things
differ from what is written below: the cache line is shown on the card and
repeated in the confirm dialog; the box lives on the truth card beside the
count it changes the meaning of; and provenance is kept by collection *name*
rather than id, since the name is what a menu can show and a project never
needs to ask mod.io about it again. Whether 117 reads `profile.txt` only at
launch is still not known, so the wording says *when it next starts*. Phase 4a
is built as section 6 describes it: `core/library.py`, the queue and both
mod.io dialogs installing into the library, an export into a mods folder
landing there and linked in, *Put the library's mods back*, and the library's
location in the settings. Phase 4b is built too: *Move them into the library* on the
fourth card, over every mod that is the user's own, and *Manage a folder of
your own in the mods folder…* for a reference. Two things differ from section
6: an export written over a real folder of the user's own stays theirs rather
than moving to the library, since nothing was installed; and *keep them where
they are* is not an option on the move — a folder already in the mods folder
has nowhere else to be kept — but a menu entry that links a folder from
anywhere else, which is the case a reference was for. Phase 4c is built as
well: a library mod's switch is its link, taken away to switch it off and put
back to switch it on, from the fourth card and from the sync alike; the
library keeps a record of every link it made, so an unlinked mod is
installed-and-off to the truth view and *Put them back* materialises it with
the rest. One thing differs from section 6: phase 3's 117 profile is not
rewritten to list library mods only — an unlinked mod is simply absent from
the folder, so the profile written from the inventory never mentions it, and
nothing had to change. Every phase of this proposal is now built.

---

## 1. The problem, precisely

Three things can each claim to be "your mods", and after the collections work
they drift apart faster than before:

| | says | governed by |
|---|---|---|
| the project | which mods were read in, and which of them are on (`ModLayer.active`) | the user, in the Studio |
| the mods folder(s) | which mods are installed | whoever put them there: the user, the Studio, the game's own browser |
| the game, tonight | which of those it will load | 1800: a `-` on the folder name; 117: the live profile |

A project has never claimed to be everything. Read a folder in, install a
collection, read another, and the project holds twenty-six mods while the disk
holds sixty-three; *Switch installed mods to match…* writes the twenty-six
switches and says nothing about the other thirty-seven. Try three collections
in three projects and all three sets are on at once, because installing is
additive and nothing ever asked what should be *off*.

The Studio's line, from `loadout.py`'s docstring, is right and should hold:
*write what the game reads, never own the folder*. A mod is not only its
ModOps; most of a real mods folder is meshes, textures and localisation the
Studio cannot put back, so rebuilding the folder from a project would delete
them. Everything below is built on that line.

---

## 2. What each game already gives you

The two games are unequal here, and it shapes everything after.

**Anno 117 has profiles.** `mods/profiles/<name>.txt` lists mods by ModID, a
`#` in front switches one off, `mods/profile.txt` names the live one with
`ActiveProfile <name>`, and a profile with its `enablenewmods` line commented
out switches off every mod it does not list — a frozen whitelist, which the
shipped `how-to.txt` suggests for exactly this. A profile governs *all four*
folders 117 loads from, the mod.io download cache included, because it names
mods by ID rather than by where they sit. `loadout.read` and `loadout.write`
already read and rewrite it line by line, keeping the user's comments and the
mods listed from other folders.

**Anno 1800 has a folder-name prefix.** A folder whose name starts with `-` is
skipped. No pointer file, no whitelist, no way to say "only these". And the
mod.io cache (`C:\Users\Public\mod.io\4169\mods`) is the game's browser's: its
`state.json` records each mod's `PathOnDisk`, so a folder renamed there is a
folder the client no longer knows about. The loader keeps one copy per ModID,
so a cache mod is loaded unless a copy of the same ModID sits in `mods\`.
**For 1800 there is a source of mods the Studio can report on but cannot
switch**, and the honest thing is to say so on screen: *on, and the game's own
browser's to switch — unsubscribe in game*.

Both loaders also find a `modinfo.json` nested inside a mod folder and load it
as a mod of its own, with its own place in load order. Which rules out the
tempting fix of a subfolder per collection inside `mods/`: it would work, and
it would silently reorder everything.

---

## 3. The truth view: first

**What it is.** One sentence, computed from disk, shown where the project is
shown:

> Your game will load **63 mods** right now. This project has **26** of them on.
> **37 others** are on that this project does not mention.

with two buttons: **Bring them into the project** (read the folder in, so the
project says what the game does) and **Switch them off** (write the game's
switches, so the game does what the project says). And, where it applies, a
third line the buttons cannot fix:

> 4 of them the game's own mod browser installed; the Studio cannot switch
> those. Unsubscribe in game.

**Where it lives.** On the Start tab, under the project card, since that tab is
"where am I and what is loaded"; and in the status bar after anything that
changes the answer: opening a project, installing a collection, *Get a mod*,
*Switch installed mods to match*, the update check's *Download and update*.
Not on a timer and not on a file watcher: the answer moves when the Studio
moves it or when the user opens a project, and a watcher over a folder the
game's browser also writes to is a stream of half-states.

**What it costs.** `installplan.inventory` walks every folder the game loads
from — 193 mod folders in 1.7 s on this machine — and `loadout.read` says which
are on. Both exist. The sentence is a set comparison over ModIDs (folder name
where a mod declares none), matched the way the collections plan matches:
ModID first, since the same mod is routinely installed twice under two names.
Under two seconds, behind a wait cursor, at the moments listed above.

**The pieces.**

- `core/truth.py` (or a section of `loadout.py`): `Truth` — for a game and a
  folder, every installed mod with `on`, `where` (which of the folders), and
  `in_project`; and `describe()` for the sentence. Pure; tests against
  temporary folders as `test_installplan.py` does.
- The three sets it needs are already named: the project's active layers, the
  disk's switched-on mods, and the intersection by ModID. *Others on* is the
  interesting set; *project on but not installed* is the collections plan's
  job and links to it; *project on but switched off on disk* is what *Switch
  installed mods to match* already handles and links to that.
- **Bring them into the project** is `load_installed_mods` — the existing
  read, which reads everything and so makes the project say what the disk
  does. **Switch them off** is `loadout.write` with the strays wanted off,
  behind the same confirm dialog `sync_loadout` uses, listing what moves, with
  the same profile backup for 117.
- A card widget on the Start tab: sentence, the two buttons, the cache line.
  `SourcePanel` already has a project card with a status line to sit under.

**What it does not do.** It never switches anything without the confirm dialog
that lists every folder about to be renamed or every ID about to be
commented out. It never touches the mod.io cache. It does not run on startup
before a project is open, because there is nothing to compare against.

---

## 4. A project that is the whole loadout: second

One project fact, `exclusive: bool`, default off, set from the project card
(*This project is my whole mod list*), meaning **everything not in me is off**.

- *Switch installed mods to match…* then also switches off every installed
  mod the project does not list. For 117 that is the profile with
  `enablenewmods` commented out and the strays listed with `#`; for 1800 it is
  renaming the strays. Both are what the user would do by hand and both undo
  by doing them again.
- The truth view's *others on* line becomes a warning rather than a note, and
  its *Switch them off* is the same act as sync.
- Reading a folder into an exclusive project keeps it exclusive: the read is
  the truth, the project then says what should change.
- A mod the project has *switched off* stays installed and off, as now. A mod
  the game's browser installed for 1800 is listed under the cache line, as
  the one thing the flag cannot deliver.

This is Mod Organizer's profile semantics — activate X and only X loads — with
no machinery beyond the switches the games already read.

---

## 5. Named loadouts: third

**117: a profile per project, and the pointer.** *Switch installed mods to
match…* on an exclusive project writes `mods/profiles/<project name>.txt` —
a fresh file the Studio owns, with a `displayname` line — and points
`profile.txt` at it. The user's own profiles are never rewritten; switching
projects switches the game's live profile; every mod stays installed. Three
collections in three projects are three profiles, one pointer flip apart, and
the game's own mod menu shows the same names. Read back, `active_profile`
already follows the pointer, so the truth view says which profile is live.

**1800: a rename set.** No pointer exists, so a loadout is only its set of
`-` marks. Same buttons, same confirm, less native: switching applies the
renames, and the truth view says which project the folder currently matches,
if any. Two hundred renames take a moment and undo by renaming back.

**Provenance.** `ModLayer.from_collection: int` (mod.io collection id) and a
matching note on the row, set by the collections plan when it installs. Then
a project can offer *switch off everything from Jakob's Collection*, and the
truth view can say where a stray came from rather than only that it is one.

---

## 6. The library

The genuinely Mod Organizer-shaped design, and the one that answers the
worry in section 1 outright rather than reporting on it: what the Studio
installs does not go into the mods folder at all. It goes into a **library**
of the Studio's own, and the mods folder holds a **directory junction** to
each mod the active loadout wants, named as the mod folder would be. No DLL,
no administrator rights (junctions need neither, unlike symbolic links), load
order untouched because the name is the name. A mod that is off has no
junction, which is the whole of what a casual user sees: the folder holds what
loads. Switching a loadout is a few dozen junctions deleted and made.

It waited on one question — **does each game load a mod through a junction?**
— and `tools/junction_probe.py` answered it on 23 September 2026, with a
probe mod in a library folder and a junction to it in each game's own `mods\`:

| | loaded? | the log names |
|---|---|---|
| Anno 1800 | yes — `Load [Misc] Junction Probe (Studio) (1.0, studio_junction_probe) from mods\[Test] Junction Probe (Studio)` | the junction's own path |
| Anno 117 | yes — `Load "[Misc] Junction Probe (Studio)" studio-junction-probe@1.0 (from "mods/[Test] Junction Probe (Studio)")` | the junction's own path |

Both loaders traverse the junction, and both log the path they walked rather
than the folder it resolves to. That is the better of the two yeses:
`logged_order`, the update check and the inventory all see the folder name
they already key on, and nothing has to learn to see through links. A junction
needs no administrator rights, which is why it was the link tried.

Two things the probe turned up on the way, neither about junctions. 117
refuses a mod outright whose ModID has anything but lowercase letters, digits
and dashes — and the Studio passes a typed ModID through as-is, which is now a
task of its own. And 117 writes back into the live profile: a listed ID it
cannot resolve gets `# not installed` appended to its line, so a profile is
not only read by the game but annotated by it, which phase 3's writer has to
expect when it rewrites one.

### What is in the library, and what is not

**Entries are copies or references.** A *copy* is a mod the Studio put there
itself: a collection's mods, a mod fetched from mod.io, an export of the
project's own mod. A *reference* is a folder the user owns -- a mod author's
git checkout, edited in VS Code with the anno-mods plugin -- that the Studio
manages in place: the junction in `mods\` points at that folder directly, and
the library records only that it does. Both are managed identically; the
difference is who owns the bytes. The reference is what makes the library fit
the Studio's own audience: copying an author's working copy would be exactly
wrong, since they would edit the checkout and the game would load the stale
copy.

**Reading a folder in never moves anything.** *Import your installed mods* is
an observation, and the Studio's credibility on mods folders rests on the
rule in `loadout.py` -- write what the game reads, never own the folder. The
moment a read can also relocate what it read, it becomes something a cautious
user fears, and the truth view stops being a neutral referee. So adoption is
an offer made *after* the read, with the count in it: beside *Bring them into
the project* on the fourth card, **Move them into the library** -- copy by
default, *keep them where they are* as the reference option -- listing what
moves and where. The nudge is deliberate: the ideal way to use the Studio is
to let it manage everything, and every mod outside the library is evidence
that it does not yet. It just should not become true by surprise.

**The library must be leavable.** Real folders, copyable by hand; and **Put
them back**, which turns every copy's junction into a real folder and drops
every reference's junction, so the Studio can be uninstalled without the game
losing its mods. One README sentence says the other half: delete the library
and the game loads nothing the Studio installed.

**Two folders are the game's, not gaps.** `mods/.ubi` is Ubisoft's and a
patch rewrites it; the mod.io download cache is the game client's, keyed by
path in its `state.json`, so a folder moved or renamed there is one the client
re-downloads or discards. Neither is something the Studio cannot *yet* own --
they would break under any tool that did. Report, with the reason, as the
truth view does now.

**Not in Documents.** `studio_home()` is `Documents\AnnoModdingStudio`, which
on Windows 11 Home is routinely synced by OneDrive; a library of gigabytes
there is uploaded, and Files On Demand can dehydrate a mod behind a junction
into a placeholder the game cannot read. mod.io's own client uses
`C:\Users\Public` for exactly this reason. The library defaults to
`%LOCALAPPDATA%\AnnoModdingStudio\library\<game>\`, with a picker, and the
game's drive is the sensible choice. Junctions cross volumes.

**Versions are folders.** Two projects wanting two versions of one mod are two
library folders under two names, as the game itself keys them. Said, not
solved.

### What it costs, honestly

The update check, the collections plan and the truth view key on folder names
and the loader logs the junction's path, so none of them has to change to see
a library mod as installed; `modzip.install` targets the library and a
junction is made after. Exports into the library need no backup: the project's
history is the record of the Studio's own work, which is what the backups were
never really for. Backups stay for writes into folders the user owns. What is
left: anyone opening the mods folder sees links, and a library mod that is off
is invisible to the other managers -- which is their gap once the Studio does
what they do.

### Order

- **4a** -- the library, and what the Studio installs landing in it: the
  collections queue, *Get a mod from mod.io*, *Download and update*, and an
  export of the project's own mod, each writing a real folder to the library
  and a junction into the mods folder. The inventory says which installed
  mods are the library's. *Put them back*, so 4a is leavable from the start.
- **4b** -- *Move them into the library* on the fourth card, copy or
  reference, listing what moves.
- **4c** -- a loadout switch as a junction set: an exclusive project's off
  mods have no junction, and phase 3's 117 profile lists library mods only.

---

## 7. What was checked, and what is not known

Checked, in the code and on this machine:

- 117's profile mechanism: `POINTER_FILE`, `PROFILE_DIR`, `NEW_MODS_LINE`,
  read and rewritten by `loadout.read_profile` / `rewrite_profile`, governing
  every folder `mod_paths` lists.
- 1800's `-` prefix, and that the cache's `state.json` keys on `PathOnDisk`.
- The cost of an inventory: 193 folders, 41 in the cache, 107 bundled, 1.7 s.
- That both loaders load nested modinfo folders as mods (`modops.bundled_in`,
  and the loader logs the census read).

Checked since, by starting the games: both load a mod through a directory
junction and log the junction's own path (section 6).

Not known:

- **Whether 117 re-reads `profile.txt` between sessions only**, or on the mod
  menu opening too. It affects only the wording after a switch — *restart the
  game* or not — and one look at the game answers it.

---

## 8. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 1 | `core/truth.py`; the Start-tab card and the status-bar sentence; *Bring them into the project* and *Switch them off* over existing reads and writes | nothing new |
| 2 | `ModProject.exclusive`; sync switches strays off; the read keeps the flag | 1 |
| 3 | 117: a profile per project and the pointer flip; 1800: the rename set; `ModLayer.from_collection` | 2 |
| 4 | The library on junctions | 3; the junction test has passed |

Phase 1 is a day and is the whole of the worry in section 1 made visible.
Phase 2 is small once 1 exists — it is one flag and one more set in the sync.
Phase 3 is the switcher, and for 117 it is mostly writing one file and one
line. Phase 4's test has passed for both games, so it is only a question of
whether people turn out to keep more mods than they play — and of the costs
listed in section 6, which the test did not make smaller.
