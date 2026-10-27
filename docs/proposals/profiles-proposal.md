# One mod list, several profiles: a proposal

How a loadout comes to hold more than one set of switches — so that the
profiles somebody keeps in iModYourAnno, or in Anno 117's own `profiles`
folder, stop being separate copies of one mod list that drift apart. Written
against the playing proposal as built through phase 6, `core/changes.py`,
`core/truth.py`, `core/library.py`, the conversion in `core/convert.py`, and
the six loadouts the conversion made on the machine this was written on.

**Status (1 October 2026).** All four phases are built, as are folding the old
copies in and the collection-provenance fix: `Profile` and `choose_profile` in
`core/changes.py`, the picker on the Start tab, forks and collections as
profiles, and *Fold loadouts…* (`core/folding.py`). Section 8's "nothing shows
profile names yet" was true at phase 1.

**Recommendation in one paragraph.** A loadout keeps one mod list — its mods,
their changes and rules, the edits made to them, their options — and any
number of named **profiles**, each nothing but which of those mods are on. One
profile is *chosen*; it is what the card compares, what Play makes live and
what a switch flipped in the changes panel edits. Everything else exists once,
so a mod that updates is read in once and every profile has the new version.
The conversion makes a loadout's profiles out of the profiles it finds, rather
than a loadout per profile, and the six loadouts it has already made here can
be folded back into the one they were copied from. Separate loadouts stay what
they are for: lists that are meant to grow apart.

---

## 1. The problem

The conversion offers every iModYourAnno profile, and every 117 profile, as a
loadout — and a loadout is a project. So it copies the project once per
profile. On this machine that is six copies of the main Anno 1800 loadout,
16.7 MB each; *Profile1* and *Profile2* differ in whether 13 of their 134 mods
are on, and in nothing else, byte for byte.

The size is not the problem. The drift is. Each copy holds its own reading of
every mod. When a mod updates, the loadout it is read into has the new
version; the other six go on holding the old reading, and nothing says so.
An edit made to a mod in one — a changed value, a rule — exists in that one.
Re-reading the mods folder into one leaves the others as they were. The
copies were never meant to differ in anything but switches, and nothing keeps
them from differing in everything else.

That is also how the tools the profiles come from see them. iModYourAnno's
`.imyaprofile` is a list of folder names that are on; 117's profile is a list
of ModIDs, `#` meaning off. Both are one mod list, switched differently.

## 2. What is shared and what is the profile's

| | Where it lives |
|---|---|
| The mods (layers), their folders, versions, mod.io ids, load order | the loadout |
| What each mod changes, its rules, what could not be read | the loadout |
| Edits made to a mod in the Studio, and the loadout's own mod | the loadout |
| Mod options — 117's `active-options.jsonc`, iModYourAnno's values | the loadout |
| Which collection a mod came with | the loadout |
| Version history | the loadout (as now: authored changes only) |
| **Which mods are on** | **the profile** |

Options are the loadout's because they are the game's: 117 has one options
file, and iModYourAnno writes its values into the mod folder, not into its
profiles. A profile that wanted a mod set differently would have to rewrite
the folder on every switch — and a list that differs in more than switches is
the case for a separate loadout.

## 3. The model

`ModProject` gains `profiles: {name: {origin: bool}}` and `profile: str`, the
chosen one. **`ModLayer.active` stays exactly what it is** — the switch the
Studio reads — and is, from now on, the working copy of the chosen profile.
Choosing another profile writes the layers' switches back into the one being
left and reads the new one's into the layers. That is the whole of the
switch: the places that read `layer.active` — twenty-seven, across the truth,
Play's plan, the changes panel, the loader-log check, the install plan, undo,
the workspace and the conversion — go on reading it, and are never asked which
profile.

Saved as a small map per profile beside the layers. A mod a profile does not
mention is off in it: a mod arrives in one profile, on, and in the others,
off. A project from before this reads as one profile, called *Main*, as a
new loadout's first profile is.
At least one profile always exists; the last cannot be deleted.

## 4. What changes, piece by piece

**The project card.** Beside the loadout's name, the chosen profile, with a
picker: its profiles, *New profile…*, *Rename…*, *Delete…*. Choosing one with
unsaved switch changes keeps them — they are the profile's until saved, like
any other edit.

**The fourth card** compares the chosen profile: *Playing Profile2 changes 3*.
The live header names both: *Live: Anno 1800 loadout — Profile1*. Choosing a
profile that is not live says what Play would change, before anything is
written.

**Play and Make this live** make the chosen profile live. The live record
(`live.json`) gains the profile's name beside the project's; the fingerprint
and the launch time are as they are. The loader-log check grades the live
profile. For 117 the Studio's one profile is written with the DisplayName
*Anno 1800 loadout — Profile1*, so the game's own mod menu says which.

**The changes panel.** A switch flipped there edits the chosen profile, and
undo takes it back, as now. A mod switched off is drawn switched off; its
changes and edits stay, because other profiles use them.

**Recents.** One entry per loadout. The live one comes first and says which
profile: *Anno 1800 loadout — what the game loads (Profile1)*.

**Forking.** *Fork this loadout…* asks one question: *A new profile in this
loadout* — the same mods, switched as the chosen profile is, for trying a
different set — or *A separate loadout* — a copy that owns its mods, for
something that will grow apart. The first is the default; the second is what
fork does today.

**A collection.** *Start from* with *what I play* copies the live loadout
today. With profiles it asks the fork's question: *A new profile in this
loadout* — the collection's mods join the mod list, on in the new profile
and off in the rest — or *A separate loadout*, a copy isolated from what
you play. The profile is the default, because it is easy to take back (see
*Taking a collection out*, below) and does not drift if you keep it; the
separate loadout is there for somebody who wants the experiment walled off
entirely, and accepts that a copy kept is a copy that drifts. *Nothing*
still makes a separate loadout of the collection alone — shares nothing
with what you play, so there is nothing to drift.

What makes the profile safe is that the loadout knows, per mod, **what
brought it** — a collection, or nothing, because you had it — and **every
collection that lists it**. That much is built already, ahead of profiles
(see section 9): the mods list's *From* column says *Jakob's* for a mod the
collection brought and, dimmer, *also in Jakob's* for one of yours it
lists; the changes panel's tooltip says the same.

**Taking a collection out.** An action on the collection, not on a profile:
the mods it brought leave the mod list, and every profile; the ones it lists
that you had stay, as yours. Their library copies, if no other loadout
mentions them, are offered for deletion, listed. Switching a collection's
mods off or on is the lighter step, and acts on the chosen profile only.

**Reading the mods folder in again** keeps every profile's switches. A mod it
finds for the first time comes in on in the chosen profile and off in the
others; a mod gone from the folder goes from every profile.

**Switching off a collection's mods** acts on the chosen profile, and
touches only the mods the collection brought; the ones it lists that you
had before it are asked about, and stay on unless you say.

**The conversion.** Profiles found — iModYourAnno's lists, 117's own — become
profiles of the loadout being converted, matched as now (folder names for
iModYourAnno, ModIDs for 117). *Your six iModYourAnno profiles are now six
profiles of this loadout.* The survey's box says so.

## 5. The six that already exist

A one-time **Fold loadouts into this one…** for the loadouts the conversion has
already made: offered for those whose mods are exactly this loadout's —
the same layers, the same changes, the same rules — and differing only in
switches, which is what a copy made by the conversion is until someone edits
it. Each becomes a profile named as the loadout was; its file is set aside
into the backups, not deleted; if it was live, the live record follows it to
its profile. A loadout that has grown apart — a mod of its own, an edit, a
different reading — is not offered, and is said to be separate, with what
differs.

## 6. What it costs

- `ModProject` and its file format: two fields, one migration, the switch
  between profiles. Undo has to know a profile switch is not an edit to the
  change set.
- The live record and everything that reads it: the card's header, the
  loader-log check, recents, fork and collection starts.
- The project card's picker, and the fork dialog's one question.
- The conversion's profile step and *Fold loadouts into this one*.
- 117's profile DisplayName.

What does not change: the truth, Play's plan and the switches it writes, the
library, conflicts, the changes panel's drawing, the loader log's parsing — all
of it reads `layer.active`.

## 7. Decisions

- **The word — decided: *profile*.** What both tools call these, and what
  somebody arriving from either will look for. It also names the files 117
  reads, one of which the Studio writes; that file stays out of sight, as it
  is now.
- **A collection on top of what you play — decided: asked, profile by
  default.** A separate loadout isolates it and drifts if kept; a profile
  does not drift and is safe once the loadout tells what a collection
  brought from what it only lists, and can take it out again. So both are
  offered, the profile first.
- **Options per profile.** Left out, above, on purpose. If it turns out a
  player wants a profile with a mod set differently, that is a separate
  loadout today and could be a later step.
- **History.** Snapshots hold authored changes and would go on doing so;
  switches have never been versioned, and profiles would not be either. Worth
  saying on the history dialog, since a profile is exactly the thing somebody
  might expect a version to include.

## 8. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 1 | The model: `profiles`, `profile`, the switch between them, the file format and its migration, undo | nothing new |
| 2 | The project card's picker; the fourth card, Play, *Make this live*, the live record, the loader-log check and recents by profile; 117's DisplayName | 1 |
| 3 | Fork's question, and a collection started from what you play asking it; *Taking a collection out*; reading the folder in keeping every profile's switches | 2 |
| 4 | The conversion making profiles; *Fold loadouts into this one* for the ones it already made | 2 |

Phase 1 changes nothing anybody sees, and is the one that has to be exactly
right: every later phase leans on `layer.active` being the chosen profile's
switches and nothing else. Phase 4 is what removes the drift on this machine.

### Phase 1, as built

- **`Profile`** is a name and a map of switches, told apart by identity
  rather than contents. `ModProject.profiles` is a list of them, in the
  order made; `ModProject.profile` names the chosen one. The chosen
  profile's switches are the layers', and its own map is brought up to
  date only when it is left, so everything asks `switches_of(name)`, never
  the map.
- **Choosing** (`choose_profile`) merges the layers' switches into the
  profile being left and reads the new one's into the layers; a mod it
  does not mention is off. `add_profile` (switched as the chosen profile
  is, or as another says, and not chosen), `rename_profile` and
  `delete_profile` (never the last; the chosen one can go, and the first
  of the rest is chosen) complete it. Names are needed and unique,
  ignoring case.
- **The file** gains `profile` and `profiles`, each profile holding loaded
  mods only. Holding one profile it is still stamped **format 2**, which
  an older build reads whole, since the layers carry the chosen switches.
  Holding two or more it is **format 3**, which an older build refuses
  rather than write back without the rest. On reading, the layers win over
  the chosen profile's entry, in case an older build switched a mod since.
- **A project from before profiles** is one profile. *(Built first as named
  after its file, which put the loadout's name twice side by side — "Anno
  1800 loadout (2) — live" in the picker beside "Anno 1800 loadout (2)".
  Changed after phase 4: a new loadout's first profile, and an old
  project's one profile, are called **Main** (`DEFAULT_PROFILE`). A profile
  already named keeps its name.)*
- **Undo.** Choosing a profile is not a step. A step keeps the profile it
  was made in, as the object, so a rename does not lose it. Undoing a
  switch after choosing another profile takes back only what that step
  moved, in the profile it was made in, leaving the chosen one alone. A mod
  unloaded in one profile and brought back by undo in another returns to
  each with the switch that profile had for it. Edits to the change set
  are the loadout's, and undo as before whichever profile is chosen.
- **Left for phase 2:** a profile's name counting as unsaved work
  (`typed_details`), and whether choosing a profile marks the project
  changed. Both are questions for the picker. (Decided: both do. See
  phase 2.)
- **Left for phases 3 and 4:** a separate loadout made by copying (a fork,
  a collection started from what you play, the conversion's loadout per
  profile) carries the original's profile names, so a fork of *Anno 1800
  loadout* holds a profile of that name. Nothing shows profile names yet;
  those three flows are the ones phases 3 and 4 rework.

### Phase 2, as built

- **The picker** is a list on the project card's header, beside the
  loadout's name, because the card is folded whenever a project is open.
  It holds the profiles, the live one marked *— live*, then *New
  profile…*, *Rename this profile…* and *Delete this profile…*. It is shown
  for a loadout with a file. *New profile* offers a name nobody has
  (*Profile 3*), is switched as the chosen profile is, and is chosen. A
  loadout never saved names its first profile after itself when it gets a
  second.
- **Unsaved work.** Making, renaming, deleting and choosing a profile all
  count: `typed_details` holds the profiles' names and the chosen one, so
  choosing one and choosing back again is not unsaved. A mod switch on its
  own was still not unsaved work after phase 2, as before profiles; it is
  from phase 3 (below).
- **The fourth card** says *Playing Profile2 changes 1* and *Play makes
  Profile2 live* for a loadout of several profiles, and *Live: Anno 1800
  loadout — Profile1* for the live one. A loadout of one profile reads
  exactly as before. The header is *Live: this loadout — Profile2* when
  the chosen profile is the live one. Choosing a profile says in the
  status bar what Play would change.
- **The live record** (`live.json`) gains `profile`, written as the
  project's file names it. A rename not yet saved is followed when the file
  is written (`_wrote_project`), and not before, so a rename not kept
  leaves the record naming what the file does. Play before saving a rename
  records the saved name, which the save then moves on. A record from
  before profiles names none and means the first profile. The profile the
  game loads cannot be deleted; the dialog says to make another live first.
  `is_live` means this loadout and this profile, so the loader-log check
  grades the live profile while it is chosen.
- **Recents** say *— what the game loads (Profile2)* when the live
  profile's name is not the loadout's own.
- **117's DisplayName** is *Roman Set — Legion* for a loadout of several
  profiles, and the loadout's name alone for one.
- **Undo** of a switch made in another profile says so: *Undid switching
  X off, in Profile1.*

### Phase 3, as built

- **Switches are unsaved work.** `typed_details` holds, per loaded mod,
  whether each profile has it on, compared as the layers are: only for mods
  both sides have, so a folder read that adds mods is still not work.
  Switched and switched back is nothing. The prompt on leaving says *Which
  mods this project has on, or its details, have changed*.
- **Fork's question.** *Fork this loadout…* asks *A new profile* (the
  default, which is *New profile…*) or *A separate loadout* (the fork as
  it was). A separate copy renames a profile named after the original to
  the copy's name, which settles phase 1's note about copies carrying the
  original's profile names for the fork. A collection started as a
  separate copy does the same.
- **A collection on top of what you play.** *Start from* has three
  answers: *On its own*, *As a new profile of Main* (the default) and *As
  a separate copy of Main*. It is now asked before the card's *Start
  fresh?*, so the profile answer clears nothing. It opens the live loadout
  (asking about unsaved work first, if another project is open), installs
  the collection, and adds a profile named for it (numbered if the name is
  taken). The profile is switched as the live profile is, with every mod
  the collection lists on, including ones you had and the live profile has
  off. The folder read that follows keeps those switches and brings the
  new mods in on. They are off in every other profile, and the live record
  is untouched. The loadout is left unsaved, not marked as a blank page as
  a new loadout is.
- **Take a collection out…** (File ▸ Mods). It picks among the collections
  that brought a loaded mod, then lists what goes, what stays as yours, and
  how many of your edits go with them. The mods leave the loadout, and so
  every profile, and `forget_collection` clears the collection from
  `provenance`, `listed` and the layers. It is one undo step. Then the
  library copies of the mods taken out that no other project file names
  (this one's own file excepted, since it is about to stop naming them) are
  offered for deletion. `library.remove_copy` deletes only a copy inside
  the library, linked or switched off, never a reference or a real folder.
  Kept, they are strays that the next Play switches off.
- **Reading the folder in** (`ModProject.read_into`). When this loadout
  is live under a profile other than the chosen one, the folder's switches
  go to the live profile, and the chosen one keeps its own for every mod it
  had. Otherwise the folder is the chosen profile's say, as always. A read
  after an install keeps the chosen switches, as before. **One departure
  from section 4, kept by decision:** a mod found for the first time takes
  the folder's word in the chosen profile, rather than always coming in on.
  A read is an observation, and a mod installed switched off was not asked
  to be on.
  Other profiles are untouched; a mod new to them is off in them, and a mod
  gone from the folder leaves every profile when the file is written.

### Phase 4, as built

- **The conversion makes profiles.** Each found profile the survey leaves
  ticked becomes a profile of the loadout being converted, named as it was
  (numbered if the name is taken). Its switches come from
  `convert.profile_switches`, which reads them without touching the layers;
  a bundled mod switches with its host. The loadout's own profile, which is
  what the game loads, stays chosen and live. The loadout is saved with
  them, because the conversion's record now lists `profiles` by name rather
  than `made` files, and undoing reads them back out.
- **Undoing the conversion** deletes those profiles, from the open loadout
  or from its file, but never the last one. A record from before
  (with `made` files) still sets those files aside as it did.
- **Fold loadouts into this one…** (File menu, for a saved loadout). It
  surveys every other loadout file for this game (`core/folding.py`) and
  compares the mod list off the JSON, each entry read and written back
  through the model so a file older than a field compares by meaning. The
  comparison covers the layers (all but `active`, `order` and
  `from_collection`), the changes, the rules and what could not be read.
  - A copy identical in all of those is offered, ticked. Any other is
    listed as separate with what differs (*1 mod of its own*, *3 mods read
    differently*, *12 changes differ*, *its rules differ*) and cannot be
    ticked.
  - Each one folded becomes a profile named after its file. A file with
    profiles of its own brings each, as *File — Profile*.
  - Its file moves to `backups/folded-loadouts/<date>/`, and is not deleted.
  - If it was live, the record moves to this loadout, naming the profile it
    became (for a file with several profiles, the one the record named, or
    its first).
  - This loadout is saved with them, so the record and the file agree.
  - With nothing foldable, it says so and lists the separate ones.
- `_autosave` now does the same profile bookkeeping as every other write
  of the project (naming an unnamed first profile, the live record
  following renames), which phase 2 had missed.

### After phase 4: the first real fold

On this machine, *Fold* found none of the six copies foldable into *Anno
1800 loadout (2)*, though nothing had changed but Play. Two causes:

- **The conversion never saved the loadout it converted.** It saved before
  moving anything, then pointed its layers at the library's links in memory
  only. A folder is not typed work, so closing the window lost it without a
  prompt. The six copies had been written after the update, so they named
  the links, while the loadout still named the mod browser's cache for 14
  mods. Now the conversion saves the loadout after moving its folders.
  Opening a loadout whose file still names moved folders brings it up to
  date from the records of the conversions not undone
  (`convert.moved_folders`) and saves it.
- **Nine rules with no expression.** Opening a project drops such rules,
  so the copies, opened and saved once, had none, while the loadout's file,
  never saved since, still did. Folding now skips them as opening does, and
  no longer compares a layer's `folder`: where a mod is installed is not
  the mod list. Checked against the real files: all six copies are now
  foldable, and the original *Anno 1800 loadout* stays separate (24,484
  changes differ).

The project card's header also lost its Fork button, which had squeezed the
loadout's name to one letter. The profile picker ends with *Copy to a
separate loadout…* beside *New profile…*, the two halves of a fork. The
button is in the card's body, and *File ▸ Fork this loadout…* still asks.
*File ▸ Mods ▸ Make this live (without starting the game)…* is always
there, for starting the game some other way; the fourth card's button shows
only while the folder differs, and Play's tooltip now points to both.

## 9. Built ahead of it: what a collection brought

Found while writing section 4: installing a collection marked **every** mod
it lists as having come with it, including the ones already installed — and
the project kept one collection per mod, the last one — so *Switch off a
collection's mods* switched off mods that were yours before the collection
arrived. Fixed on its own, since it is wrong without profiles too:

- The install plan records what it **brought** — installed where nothing
  was — apart from what it updated or found; `ModProject.provenance` holds
  only those, and a new `ModProject.listed` every collection that lists each
  mod, brought or not.
- *Switch off a collection's mods* switches off what it brought, and asks
  about the ones it lists that you had — no by default.
- The mods list's *From* column and the changes panel's tooltip say *came
  with* and *also in* apart.

Projects from before keep one name per folder for both kinds; there is no
telling them apart after the fact, so they read as brought, as they did.

### Before the PR

- **Main.** The first profile of a new loadout, and the one profile of a
  project from before profiles, is called *Main* rather than after the
  file. A loadout of one profile is made live with no profile in the
  record, which means its first, so the card and the recents list say the
  loadout's name alone and never "— Main" after it.
- **The fingerprint** that tells a later game start from one made after
  the folder changed counts each mod once, on if any copy of it is. It
  counted every copy, so the mod browser dropping copies the library
  shadows (the probe's fresh start dropped 4) read as *changed outside the
  Studio* while the grid matched. A fingerprint taken the old way still
  counts, so no loadout made live before is said to have changed for the
  new way of counting alone. A loadout whose stored fingerprint was taken
  before such a change, as on this machine, reads as changed until its next
  Play or *Make this live*.
