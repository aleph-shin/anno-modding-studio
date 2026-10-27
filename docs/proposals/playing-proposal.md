# Playing a set of mods: a proposal

What the third way of working is for, now that the library and collections
exist — and how somebody with years of mods in the game's folders gets to a
state where the Studio is the only thing that manages them. Written against the
Start tab as PR 19 left it, `core/truth.py` and `core/library.py`, the four
folders each game loads from, iModYourAnno's source and what it leaves in a mod
folder, and the mods folders of the machine this was written on.

**Recommendation in one paragraph.** Aim 3 has turned into a catch-all because
two separate things were folded into it: *where a list of mods came from* (what
is installed, a collection, whatever got fetched along the way) and *whether the
project claims to be what the game loads*. Split them. The **library** is what
you have; a **loadout** is a project that says what you play, and it is complete
by definition — what it has on loads, and nothing else does. At most one
loadout is **live**, and **Play** is the one button that makes a project live
and starts the game; opening a project writes nothing. Main list, a collection
tried on its own, a Requiem-style list kept apart from everything: all
loadouts, differing in name and in how they were started. Aim 3 becomes *Play a
set of mods*, its buttons are how a loadout starts, and forking is how you try
something without touching what you have. Around that: mod options become one
thing across both games — 117's `active-options.jsonc` and 1800's iModYourAnno
tweaks, which the Studio currently reads correctly and then loses on the first
update — and converting an existing mods folder into the library becomes one
guided flow, proven afterwards against the game's own loader log.

**Status (1 October 2026).** Phases 1 to 6 are built. Phase 6 changed with its
probe: the Studio does not unsubscribe for you; it lists what to unsubscribe in
the game's own mod browser (`core/unsubscribe.py`). Names below that have moved
on: `loadout.write_game_options` is `switches.write_game_options`; *Make this
loadout live…* is *Make this live (without starting the game)…*; Fork is *Copy
to a separate loadout…* in the profile picker; `exclusive` is
`ModProject.loadout`; and the Studio reads and writes `active-options.jsonc`,
which section 6 says nothing reads. Every project played on Anno 117 shares the
one Studio profile.

**Phase 1, as first built.** Phase 1 is built: `core/imya.py`, 117's `active-options.jsonc`
read on every folder read and single-mod import, iModYourAnno's knobs offered
as mod options and read at other values from memory, their answers carried
across an update, and *Mod options* as the one name. The README describes them
as they are. Three things differ from section 6 or were learnt building it:
the export was checked against iModYourAnno's own output on all ten tweaked
mod folders on this machine — 130 generated files and 151 originals, all equal
— so the idempotence question in section 9 is answered and no pristine copy is
needed; a mod bundled inside another has knobs of its own, which travel with
their host when it is updated; and the machine has twenty-eight folders with
knobs and about a hundred answers set, not the two mods section 6 names, since
the first search for the markup missed them.

Phase 2 is built too: aim 3 is *Play a set of mods* with **What I play now…**
and **A collection…**, the latter asking *Start from* when a loadout is live;
**Fork this loadout…** is on the project card's header and in the File menu;
`ModProject.loadout` replaces `exclusive` and its checkbox, written by the card
and read from older projects saved either way; and the fourth card leads with
*Live: …*, says *Making it live changes N* or *Matches*, and is orange only for
drift on the live loadout. Two things run ahead of section 4, because the
header needs them before Play exists: the **live pointer** (`live.json` in the
library) is introduced here, set by *Switch installed mods to match…* — which
for a loadout now reads **Make this loadout live…** — and by switching the
grid's rows until nothing is left to change; and the header's wording says
*making it live* rather than *Play*, which phase 3 renames. Mods the game's own
browser holds are not counted as changes or drift, since nothing can switch
them.

Phase 3 is built: **Play** (beside the tabs, on the project card, F5) and
**Make this live…** (the fourth card and the File menu) run section 4's steps
in order, from one confirm; `core/launch.py` refuses while `Anno1800.exe` or
`Anno117.exe` runs and starts the game through `steam://rungameid/` with the id
read from the manifest (916440 and 3274580 on this machine, as expected), or
`uplay://launch/` with the id from the launcher's registry entry for the
folder; `loadout.write_game_options` writes `active-options.jsonc`; the
library copies get the live project's iModYourAnno settings. Decided while
building it: every project is complete when played, so a non-loadout 117
project gets its own profile too and the `loadout` flag is now intent only
(the card, the fork button, the wording); the grid's per-row switch buttons
and the immediate write of *Switch off a collection's mods* are gone, since
nothing reaches the mods folder until Play; the project's own built mod is
counted as the project's by the fourth card; a mod whose iModYourAnno settings
differ but whose folder is the user's own is named, not rewritten; and a
project from before settings were read is never written back to defaults. The
Export tab refuses a mods folder as its destination, pointing at Play, and
gained **Export as a zip**. Not yet: the Ubisoft Connect path is untested for
want of an install, and nothing checks afterwards that the game did what was
asked — that is phase 4.

Phase 4 is built: `core/loaderlog.py` reads both loaders' logs — 1800's
`Load … (version, ModID) from …` lines, 117's `Enabled mod:` lines (the set the
switches decide, which a mod with nothing to patch may have without a `Load`
line) and its `Option:` lines — and sets them beside the live project on the
fourth card, and after Play in the status bar, found by a watch that reads
only the log every few seconds for half an hour. Section 9's open question is
answered from the logs themselves: a log is new when its first line's time is
later than the moment the project was made live, which the pointer now
records, and finished at *Loading complete* (1800) or *Patching completed*
(117); 1800 took 67 seconds from its first line to its last on this machine.
Learnt building it: a nested log line counts for the mod its ModID names,
because the loader keeps one copy per ModID and a host that ships a copy of
itself inside its own folder is loaded from inside. Against this machine's
logs it found a `data` folder in 1800's mods folder that the loader loads as a
mod, and a 117 profile edited after the game last started. Not seen yet: the
exact line 117 writes when it refuses a ModID, so that reason is attached only
if the line names the mod.

What phase 4 found, followed up. The 117 profile edit was the Studio's own —
an install listed new mods on in the live profile, and the pre-Play switch
edited it line by line, so the player's `default.txt` was being rewritten.
Neither happens now: installs write no profile (the folder read after one is
told which mods arrived, so they come in on, and keeps the project's own
switches for the rest), and Play writes a single **Studio profile**,
`Anno Modding Studio.txt`, on every Play, with the project in its
`DisplayName` — output, not a place anything lives. Play also records a
fingerprint of the mods folder as it left it and when it started the game,
so a log is judged by where it came from: Play's own start (graded), a start
elsewhere with the folder unchanged (graded the same — the Steam-next-day
case, which ignoring would switch the check off most of the time), or a
folder changed since Play (said, not graded). The `data` folder is a mod's
data tree extracted straight into `mods`; the inventory now counts that shape
and the card names it, and `tools/loose_data_probe.py` asks the game whether
such a tree's ops run. It was run (25 Sep): the control's op ran, the loose
tree's did not, and an empty folder was listed too — Anno 1800's loader lists
every folder in `mods` and runs only what is under `data/` or beside a
modinfo. So every such folder is inert: counted and named, and kept out of the
grid, Play, adoption, folder reads and the log check.

Phase 5 is built: `core/convert.py` and the survey dialog. The checklist
replaces the *managed* line; *Move them into the library* routes through the
survey; **What I play now…** offers it once when the library has never been
used. Differences from section 7, and what was learnt: the conversion refuses
to start from a loadout that is not exactly what the game loads now, and
offers to read the folder in first — making a drifted loadout live afterwards
would switch mods, and the whole point is to change where the bytes are and
nothing else (a test caught the first version doing exactly that). The
snapshot is written into the library as `conversions/<date>.json` rather than
kept only as a project. `library.adopt` now links under the bare name and
unlinks at once for a `-`-marked folder, and moves across drives by copy,
compare, delete. Working copies with `.git` are left where they are rather
than turned into references: they already sit in the mods folder, where a
link to themselves means nothing. On this machine's Anno 1800 folders the
survey finds 72 mods — 58 of the user's own and 14 only in the cache; of the
20 mods in the cache, 6 are shadowed by the user's own copies — iModYourAnno's
six profiles, and
190 MB to copy against 237 GB free. It has not been run for real here:
converting this machine's folders is the user's call.

**Backups, and taking the conversion back** (after phase 6, before the
user's first real run). The premise *backups guard what you had before the
Studio touched it* had not been closed: saves rotated at twenty per mods
folder, shared by exports and Play's profile and options copies, so the
pre-Studio copy could be pushed out; the conversion took no backup and could
not be undone — *Put the library's mods back* leaves the library, it does not
restore: off mods come back on, cache copies become extra folders, set-aside
duplicates stay aside. Now: the first copy of every file is kept for good
and saves can be pinned (`core/backup.py`); the conversion journals every
step and pins what making it live may rewrite, and *Undo the conversion to
the library* reverses it by renames (`convert.undo`); and a full copy of the
folders it moves out of can be kept first, ticked on a first conversion.
Rehearsed on a copy of the real Anno 1800 folder: convert, then undo, left
every file byte for byte as it was — after the rehearsal first found a phase
5 bug, a duplicate set aside after the cache copy's link was made at its
path, which set the link aside instead (three mods, left loading from the
cache). Duplicates now go first.

Phase 6 is built, and **turned round by its probe**: `core/unsubscribe.py`,
the list, the checklist's last row, and `tools/unsubscribe_probe.py`. Step 5
had the Studio unsubscribe, signed in, one button after a confirm. It does
not, because the client does not act on it:

- **The probe, 26 Sep, Anno 1800.** Unsubscribed through the API from *Fam's
  High-Contrast Range Indicator Colors* at 12:49 and started the game: the
  client fetched the account's subscriptions — its `user.json` list went from
  empty to 18 ids, the 19 less this one — rewrote `state.json`, and still
  listed the account among the mod's holders; the folder stayed. Its list had
  been empty, so a second run, with *Improved Trader Contracts*, which was on
  it: unsubscribed at 12:59, the game started, the list went to 17 without
  it, and again the copy stayed held for the account. mod.io did not get
  either subscription pushed back. An unsubscribe made outside the game drops
  the subscription on every machine and frees nothing on this one, and leaves
  a copy the browser no longer shows as subscribed.
- So the dialog is **the list to work through in the game's browser**, read
  from the game's own files, with no sign-in and no network: per copy,
  *unsubscribe in game* (the game's account has it on the client's list),
  *unsubscribed elsewhere; kept* (held for the account, off the list
  — subscribe again in game, then unsubscribe there), or *kept for another
  account* (held only for an account that is not the game's). The
  account-wide warning stays: it is as true in game.
- The row counts cache copies **of mods the library holds**, not the cache as
  a whole: a mod only the cache has is the first row's.
- The client's state file records, per mod, every account that subscribed on
  this machine (`SubscriptionCount`) and keeps the folder while any does: two
  mods here are held only by an account that is not the game's, and are still
  installed. Of the 21 mods in the 1800 cache, 17 are held by both accounts,
  so the list says a shared copy may stay while the other account holds it.
- `modio.Client` keeps `subscriptions`, `unsubscribe` and `subscribe`; the
  probe uses them, the Studio does not.

- **The third run, in game.** *Improved Trader Contracts* subscribed again,
  then unsubscribed in the game's own browser: the client dropped the account
  from its holders at once — and kept the folder, held for the second account.
  So a copy another account holds stays, and the list says so. On this
  machine that account holds 19 of the 21 copies, so unsubscribing as the
  game's account alone frees almost nothing here. And that account no longer
  exists: it is the user's first, merged into the Steam-made one and closed
  on 15 Oct 2024 (another mod.io game on this machine last wrote its record
  that day). It can never sign in to let go, so the client keeps its 19
  copies for good. The list says so for any copy another account holds, and
  `tools/unsubscribe_probe.py --reset` asks whether setting the game's mod.io
  folder aside, so the client starts it afresh from the signed-in account's
  subscriptions, clears them.
- **The reset, run after the conversion** (library copies in front of all
  21): it does. The client rebuilt the folder with 17 mods, every one held
  by the signed-in account alone, and left out the 4 held only by the dead
  account. It is the one way out for a merged or closed account's copies,
  and safe only when the game loads nothing from the cache, which is what
  the conversion makes true. Built in: **Start the mod browser's folder
  afresh…** on the unsubscribe list, offered when another account holds a
  copy and able only while the game loads nothing from the cache and is
  not running (`core.unsubscribe.afresh`, `start_afresh`). The folder goes
  to `backups/modio-resets/<date>/` with a note of what it held.
- **The client's saved list of subscriptions is not a snapshot.** It read 0,
  18, 17 and 0 again on the one day, while the account had 17 throughout: it
  is filled after a start that fetched the subscriptions (opening the mod
  browser does) and written back empty otherwise. An empty list is taken to
  say nothing, so a copy is only called *unsubscribed elsewhere* against a
  filled one.
- **Anno 117 has no in-game browser.** `GameProfile.in_game_browser` says so;
  for 117 the list of copies is empty, the cache and unsubscribe rows never
  appear, and the survey's hint and the conversion offer say nothing of a mod
  browser.

Still not seen: a copy held by one account only going when that account
unsubscribes in game. Both such copies here are ones the game loads from the
cache, so trying it waits on the conversion. Until then the list says the
client *should* remove its copies, and the row, computed from disk, is what
shows whether it did.

---

## 1. The problem, precisely

**Aim 3 means three things.** The card says *Everything you have installed,
read as one stack*. But since the collections work, the same card's project is
routinely one of:

- the user's whole mod list, as the game loads it — the card's original intent;
- a mod.io collection being tried, which is not everything they have, and may
  or may not be meant to replace it;
- whatever the Studio happened to fetch through *Get a mod*, beside mods
  installed by hand that the user is not thinking about.

**The fact that would tell them apart exists, and is hidden.** A project that
is the whole of what the game loads is `ModProject.exclusive`, set from a box on
the fourth card named for its mechanism — *Switch off every installed mod this
project does not mention* — rather than for what it means. And the grid beside
it does not read it: `Truth.differ` counts every stray whether or not the
project is exclusive, and `SourcePanel.set_truth` paints the *Not in this
project / Game loads* cell as a warning either way. A project that never claimed
to be the whole list is told *58 differ* in orange. The README calls those
strays *a note, not a fault*; the card treats them as a fault.

**The end state has no road to it.** The library proposal said the ideal is
for the Studio to manage everything. What a user has to do to get there —
empty the mod.io cache and the hand-installed folders into the library, without
losing a mod, a switch or a setting on the way — is three buttons in two places
with no order between them, and one step (the cache) with no button at all.

**Settings the game runs on are invisible, or lost.** On 117, the answers to
mods' own options live in a file the Studio does not read, so it reads those
mods at their defaults while the game runs them at the player's values. On
1800, iModYourAnno writes its tweaks into the mod's folder; the Studio reads
them correctly, does not know they are the player's, and throws them away the
first time it updates the mod.

---

## 2. The library and loadouts

Three nouns, and each of the user's intents is one of them or an operation on
one.

- **The library is everything you have.** Not a project: an inventory — what is
  there, which loadouts use each mod, and in time *remove from the library*.
  Keeping mods you are not playing *right now* is what it is for.
- **A loadout is a project, complete by definition.** When it is live, what it
  has on loads and nothing else does. That is `exclusive` made the rule rather
  than a box.
- **At most one loadout is live** — its links are the ones in the mods folder.
  That is a fact about the machine, not a mode of the project: the library
  records which project it last made live, the way 117's `profile.txt` points at
  one profile.

| The user means | In this model |
|---|---|
| *What I play right now* | a loadout — usually the live one |
| *A collection I'm trying, in the Studio only* | a new loadout, opened, never made live |
| *…in the game, on its own* | a new loadout started from the collection |
| *…in the game, on top of what I play* | a fork of the live loadout, with the collection added |
| *…adopted for good* | *Add a collection to this loadout…* on the main one |
| *A total overhaul kept apart from everything* | its own loadout; switching is relinking, and nothing bleeds between them |
| *Everything I own* | the library, not a loadout |

The differences between these are real, and they are differences of **intent**,
which operations express better than a stored flag: start from nothing, start
from a copy, add a collection to one that exists. *On top of what I play* in
particular is a fork, not an overlay: the main loadout stays exactly as it was
while the experiment runs, which is the property the kept-apart overhaul case
needs, and there is no second live loadout to reason about.

**Forking has to be obvious.** A **Fork this loadout…** button on the project
card, beside the name; a *Start from* choice every time a loadout is made —
*nothing*, *a copy of* the live loadout, *a collection*; and the live loadout's
name shown wherever the user would otherwise wonder what they are playing: the
project card, the fourth card's header, the title of the Play button's
confirm.

**What becomes of `exclusive`.** The checkbox goes; a loadout is complete by
definition. The machinery stays, because it is what "complete" means for a mod
that is not yet in the library — a `-` on its folder for 1800, a `#` in the
profile for 117 — and the transition from a hand-managed folder to a library
one lasts as long as the user wants it to.

---

## 3. Aim 3: *Play a set of mods*

The card keeps being one-time setup, and stops promising *everything*:

> **Play a set of mods.** A loadout: the mods you play, as one stack in load
> order, with the clashes between them visible. What it has on is what the game
> loads when you press Play, and nothing else. Keep several, and fork one to try
> something without touching it.
>
> [What I play now…] [A collection…]

- **What I play now…** is today's read, into a new loadout named for the game.
  It is how the main loadout comes into being, and — once — where the
  conversion flow of section 7 starts.
- **A collection…** opens the collection search, and the plan's install ends in
  a new loadout named for the collection, holding its mods, with the rest of
  the folder listed unticked beneath them as the folder read already does. The
  *Start from* choice is asked first: *nothing* tries the collection on its own;
  *a copy of Main* is the collection on top of what you play.
- A copy of another loadout is **Fork this loadout…**, which is Save As with a
  name, and needs no button on the card.

`set_up_as(AIM_MANAGE)` writes the one fact this card writes — that the project
is a loadout — as `set_up_as(AIM_MAKE)` writes `is_the_mod`. Nothing is a mode;
every menu entry stays where it was.

**Every project is complete when played, not only loadouts.** A project for
writing a mod of your own, played, loads that mod and whatever else the project
has on — which with nothing else loaded is the mod in isolation, exactly what an
author testing it wants. So *complete when played* is one rule, and no project
needs a flag to say which kind it is. What aim 3 adds is intent: its project is
a set of mods to play, not a mod.

**Aim 2's patch reaches the main loadout through the library.** Tweaking a
downloaded mod and then wanting that tweak in the game you play is the one
place the rule creates friction. The answer: a patch, once built, is a library
entry like any other, and the main loadout adds it like any other. Aim 2 is
where the tweak is worked on in isolation.

### The fourth card reads the loadout

The grid stays — what the project says of each installed mod, against what the
disk says — and its meaning sharpens: **the off-diagonal is what Play would
change.**

- The header says which loadout is live: *Live: Main loadout* or *Live: nothing
  the Studio made*. If the open project is not the live one, it says so under the
  header — *Play makes this one live; the game currently loads Main loadout (41
  mods)*.
- *65 differ* becomes *Play changes 65*, or *Matches*. For the live project,
  a non-zero count is drift since it was applied — something installed outside
  the Studio — and is a warning; for any other project it is a preview, and is
  not.
- **Bring them into the project** stays: it is how strays join a loadout. The
  row buttons that switch mods on and off fold into Play and *Make this live*
  (section 4).
- The *managed* line becomes the conversion checklist of section 7.

---

## 4. Play

One button, on the project card and in the toolbar, meaning: **make this
project live, then start the game.** In order:

1. **Refuse if the game is running.** Swapping links under a running game
   changes nothing it has loaded and leaves the folder describing something the
   game is not doing.
2. **Validate** the project's own mods and patches as Export does today, and
   warn rather than block.
3. **Build** the project's own mods and patches into the library. Builds into
   the library need no backup — the project's history is the record, as the
   library proposal settled.
4. **Make the links match**: a link for every library mod the project has on,
   none for the rest. For mods not in the library, write the game's own switch
   as *Switch installed mods to match* does for an exclusive project today.
5. **117:** write the project's profile and point `profile.txt` at it, as phase
   3 of the loadouts proposal does; write `active-options.jsonc` from the
   project's answers (section 6).
6. **1800:** write the live loadout's iModYourAnno settings into the library
   copies of the mods that have them (section 6).
7. **Say what cannot be guaranteed.** On 1800, mods the game's own browser
   installed load whatever the links say; they are named in the confirm, with
   the conversion checklist's button beside them.
8. **Launch** through the store rather than the executable, whose launcher DRM
   often refuses a direct start: `steam://rungameid/<appid>` for Steam — 916440
   for Anno 1800, 3274580 for Anno 117, read from the `appmanifest` beside the
   install rather than hard-coded — and Ubisoft Connect's own URI where the
   install is theirs. The install path says which.

**Make this live** is the same without step 8, for someone who starts the game
another way. *Switch installed mods to match…* becomes it.

**Opening a project writes nothing.** Not the links, not the profile, not the
options. Otherwise opening a collection to look at it would wreck the live
loadout, and so would opening a mod of your own to read it.

**Nothing syncs while you edit.** Ticking a mod in the changes panel moves the
fourth card's counts, over the folders as last looked at, and changes nothing
on disk until Play.

### The Export tab becomes publishing

Play builds the project's own mods into the library, so writing them to the
mods folder stops being the Export tab's job. What is left is what the tab is
genuinely for: a zip, a folder somewhere else, an update to mod.io. The mod to
export, the preview and validation stay; *Export this mod* into the mods folder
goes, with a line pointing at Play.

---

## 5. The loader log check

Both games' loaders write down what they loaded — 1800 under `log/`, 117 under
`mods/`, both already read by `loadout.logged_order` — and both log the
junction's own path, so a library mod reads as its folder name. After Play, the
Studio waits for the log to be rewritten and compares it with the loadout:

> The game loaded 41 mods; all 41 are Main loadout's.

or names what differs — a mod the loadout has on that did not load (with the
loader's own reason, when it gives one: 117 refuses a ModID with capitals), a
mod that loaded that the loadout does not mention. That turns *what you see is
what the game gets* from a claim into a check, from a file the Studio already
reads.

117's log also lists every option it applied (`Option:
gz2k2-custom-walls-and-gates.walls_nomaintenancecosts: true`, then *31 mod
options found*), so for 117 the check covers options as well as mods.

Shown in the status bar and on the fourth card until the next Play; a
difference is a warning with the mods named. Not a dialog: the user has started
a game.

---

## 6. Mod options, in both games

Three things got blurred together in the discussion that produced this
proposal, and the Studio's own wording is partly why.

| | 117 options | 1800 iModYourAnno tweaks | Studio rules |
|---|---|---|---|
| Who defines it | the mod's author, `Options` in `modinfo.json` | the mod's author, `<ImyaExpose>` beside ops tagged with a `ModOpID` | the user in the Studio, or a mod's search-shaped op read in |
| Who applies it | the game's loader, at start-up: ops read `$modid.option` in a `Condition` or `ModValue` | iModYourAnno, before the game starts, by rewriting the mod's files | the Studio when reading; exported as a ModOp the loader runs |
| Where the answer lives | `Documents\Anno 117 - Pax Romana\mods\active-options.jsonc`, one file for the game | the mod's own folder, plus iModYourAnno's JSON | the project, and the exported mod |
| The mod's files touched | no | yes | no |

The first two are one idea — knobs an author built in — implemented twice,
because 1800's loader has no variables and 117's built them in; the loader's own
documentation calls 117's options *comparable to iModYourAnno Tweaks*. Rules are
not that idea: a rule is an edit aimed at every asset of a shape. They meet only
in that an op whose value depends on an option is kept as a rule, and changing
the option changes what the rule comes to.

**One name.** The Studio calls options both *Mod options…* and *Edit tweakable
values…*, and aim 2 is `AIM_TWEAK` internally. The UI says **Mod options**
everywhere, for both games; "tweak" stays out of user-facing text. And the rule
of thumb goes on screen where both are offered: *use a mod option where the
author offers one — it survives the mod's updates and stays in the range the
author meant; use a rule or a patch for what they did not offer.*

### Anno 117: read and write `active-options.jsonc`

There is no screen in the game for these. The loader's documentation says of
the file *for now you have to create it yourself*; players' values come from a
third-party manager (on this machine, Taludas' Anno 117 Mod Manager, whose
settings are dated the same day as the file). But the loader reads it — it is
the loader's documented input, keyed by ModID, holding only what differs from a
mod's defaults:

```jsonc
{
  "powerful-flagship": { "num_flagship_archers": "0", "num_flagship_scorpions": "4" },
  "taludas-increasedvillaslots": { "multiplier": "1.5" },
  "gz2k2-custom-walls-and-gates": { "walls_nomaintenancecosts": "true" }
}
```

- **Read it when a folder is read in**, so each mod is read at the values the
  game runs, and `ModLayer.options` starts as the player's answers rather than
  the author's defaults. Today the Studio reads those mods at their defaults —
  a gap in the fourth card's truth that predates this proposal.
- **Write it on Play**, from the live loadout's answers, keeping the file's
  comments and any entry for a mod the loadout does not mention.
- **Notice drift.** If another tool changes it, the next check says the file no
  longer matches the live loadout.

### Anno 1800: iModYourAnno, carried forward

iModYourAnno is the manager serious 1800 players use, so *what happens to my
tweaks?* needs a complete answer. What it does, from its source
(`TweakerFile.Export`, `TweakRepository`):

- An author marks ops with a `ModOpID` and describes each knob with an
  `<ImyaExpose>` — an ID, a description, a kind (`SkipToggle`, `Enum`, a range,
  a toggle), and a path inside the op. `<ImyaTweaks>` titles a section.
- Setting a value **rewrites the mod**: every exposed op gets `Skip="1"` in the
  original file (a `SkipToggle` that is on stays unskipped), the edited copies
  are written to a sibling `<file>.imyatweak.include.xml`, and an `<Include>` for
  it is appended to the original.
- The values are also kept in `<Anno 1800>\.imya\tweaks\<folder name>.json`, so
  iModYourAnno can put them back when the folder is replaced.

On this machine, *The Wholesome Hacienda Overhaul* carries six tweak files and
*The perfect specialists & items pack* 124.

**What the Studio does with such a mod today.** Read with the Studio's reader,
the Hacienda mod gives 646 ops, 50 of them from the `.imyatweak` files, followed
through the added `<Include>`; the 50 originals with `Skip="1"` are read as
switched off; the 77 `<ImyaExpose>`, 8 `<ImyaTweaks>` and 20 `<Group ModOpID>`
are carried untouched. So **the reading is right**: values and conflicts are
the ones the game has. What is wrong is everything after reading:

1. **The player's settings read as the author's content.** Tier 1 decree at
   *Curious*, spice farm cycle 60: nothing marks them as choices, and nothing
   offers them as options.
2. **An update loses them, silently.** *Download and update*, or a collection
   replacing an older copy, installs a fresh folder and sets the old one aside.
   iModYourAnno re-applies from its JSON; the Studio does not, and the game goes
   back to the author's defaults without a word. The worst of the five.
3. **Two loadouts cannot differ**, because the answer lives in the one folder.
4. **iModYourAnno's record is keyed by folder name.** This machine has both
   `[Seal] The Wholesome Hacienda Overhaul.json` and `[Addon] The Wholesome
   Hacienda Overhaul (Taludas).json` — one mod, renamed once, two records.
5. **Moving into the library keeps them**, being files in the folder. The one
   thing that already works.

**What it should do.** Treat iModYourAnno markup as mod options, exactly as
117's are — one *Mod options…* dialog for both games.

- **Read the declarations.** Each `<ImyaExpose>` maps onto `settings.Option`:
  `ExposeID` its name, `Description` its label, `Kind` its kind, the enum's
  values its values, `InitState` its default. `<ImyaTweaks Title>` heads its
  section, as a dummy heading option does for 117.
- **Read the current values from the folder.** The Skip flags and the generated
  ops are what the game runs, so they win. iModYourAnno's JSON names them, and
  is the fallback when a folder has been reset. Values land on
  `ModLayer.options`, so they belong to the loadout, not the folder.
- **Apply them the way iModYourAnno does.** Changing a value re-reads the mod
  with the expose applied in memory — the same edit `TweakExporter` makes — so
  the changes list shows what the setting does before anything is written.
- **Write them on Play, in iModYourAnno's format.** One loadout is live at a
  time, so Play can rewrite the Skip flags and the `.imyatweak.include.xml` for
  the live loadout's values; the library copy is the Studio's to rewrite. The
  output is what iModYourAnno would write, so the two tools agree about the
  files. That is what makes problem 3 go away.
- **Re-apply after every update.** An update installs the new file, then writes
  the loadout's values into it. A value whose expose the author has renamed or
  removed is named, never dropped silently. That is what makes problem 2 go
  away, and it is the first thing to build.
- **Leave iModYourAnno's JSON alone.** It is another tool's state. A player who
  opens iModYourAnno afterwards may see stale values there; the folder, which is
  what the game reads, is right.

---

## 7. Converting to the library

The machinery mostly exists — `library.adopt`, *Move them into the library*,
*Bring them into the project*. What is missing is an order, handling for
duplicates, the mod.io cache, the player's settings, and a way to prove
afterwards that nothing changed. One flow, started from **What I play now…**
the first time, and from the fourth card's checklist any time after.

**0. Before anything.**

- The game is closed.
- Other managers are named if their traces are found — iModYourAnno's `.imya`
  folder beside the 1800 install, Taludas' manager under `AppData\Roaming`. Their
  profiles switch mods by renaming folders and would fight the Studio's links; after the conversion
  they should not be used to switch mods. Said once, plainly.
- Free space. A move within one drive is a rename; copying out of the mod.io
  cache, or into a library on another drive, is not.

**1. The survey (read-only).** One table of every installed mod, grouped by
where it lives:

- *Your folders* — the install's `mods\` and the one under Documents.
- *The game's mod browser* — the mod.io cache.
- *The game's own* — 117's `.ubi` (section 8). Shown, never moved.

Each row: name, ModID, version, on or off, whether it is on mod.io (so the
update check covers it afterwards), whether it has **iModYourAnno settings** or
117 options, and what will happen to it. Everything is ticked; unticking leaves
a mod where it is.

**Duplicates collapse to one row per ModID.** This is what the current move
does not handle, and real folders are full of it: of the nineteen mods in this
machine's 1800 cache, eighteen are shadowed by hand-installed copies of the same
ModID. The copy the game loads goes into the library; the others are set aside
into a dated folder, never deleted, or — for the cache — unsubscribed at step 5.

**2. The snapshot.** Before anything moves, the main loadout is made from the
state *as it is*: every mod, on or off, and every setting — 117's
`active-options.jsonc`, 1800's iModYourAnno values. It is the "before" the rest
of the flow is checked against.

**Existing profiles become loadouts.** iModYourAnno keeps its profiles in
`<Anno 1800>\.imya\profiles\*.imyaprofile`, each a plain list of the folder
names that are on; this machine has six. 117 keeps its own in
`mods\profiles\`. The survey offers each as a loadout, matched to ModIDs
through the inventory. *Your six iModYourAnno profiles are now six loadouts* is
the sentence most likely to convince a serious 1800 player.

**3. The move.**

- Your own folders move into the library, junctioned back. **Fix first:**
  `library.adopt` keeps the `-` mark in the junction's name, so a switched-off mod
  becomes a `-`-named link rather than no link. In this model the off state
  belongs to the loadout, so the move strips the mark and records the mod as off.
- The mod.io cache is **copied**, not moved — the folder is the game client's —
  with the mod's mod.io id, file and version taken from `state.json`, so the
  library knows it exactly.
- A folder holding `.git` is somebody's working copy: listed apart, and offered
  *manage it where it is* (a reference) rather than moved out from under their
  editor.
- Across drives, `shutil.move` copies and then deletes, and a failure part-way
  leaves half a mod. Across drives the flow copies, compares, and only then
  deletes; and when the library has never been used, it suggests the game's
  drive before starting.
- Then the main loadout is made live: links for what is on, none for what is off.

For 1800, a link in `mods\` beats the cache's copy of the same ModID, so the
game changes over at once and a mod the Studio could not switch becomes one it
can. For 117 the profile names mods by ID wherever they sit.

**4. The proof.** Play, and the log check of section 5: *The game loaded the
same 41 mods as before*. It proves the conversion changed where the bytes live
and nothing else.

**5. Unsubscribing.** Last, because it is the only step that touches the
user's account. Signed in, one button after a confirm that lists the mods and
says that **a subscription is account-wide** — unsubscribing removes the mod on
every machine the player uses, including one without the Studio. Not signed in,
a list to work through in game. The checklist row stays until the cache is
actually empty. This departs from the collections proposal's *never subscribe,
never follow*, and deliberately: that rule was against acting on the account
unasked, and this is asked, per mod, to remove a copy the player no longer
wants. Until they unsubscribe, the cache copy is a dormant duplicate the game's
browser keeps updating, and the checklist says so.

**Afterwards.** The mods folders hold links and `.ubi`. New mods arrive through
*Get a mod*; options are set in the Studio; *Put the library's mods back* is
still the way out.

### The checklist on the fourth card

The *managed* line under the grid becomes rows, computed from disk like the
grid and never remembered, each with a count and one button, each gone when it
reaches zero:

1. *4 installed by the game's mod browser* — **Copy them into the library**, then
   unsubscribe.
2. *37 in your folders* — **Move them into the library**.
3. *12 in the library that no loadout mentions* — **Bring them into a loadout**.

Done, it is one line: *Everything the game loads comes from the Studio's
library.* Every button opens the survey filtered to its rows, so there is one
conversion flow, not two; today's all-at-once *Move them into the library*
routes through it.

---

## 8. Decisions taken once

- **The ownership rule narrows; it does not go.** *Write what the game reads,
  never own the folder* protected mods from a tool that could not put them back.
  With the library it reads: *the Studio owns the links it made and the library
  copies it holds, and never a folder of the user's own.* Inside that line, a
  project making the links match itself is exactly right.
- **One live loadout.** Fork rather than overlay.
- **Opening writes nothing; Play writes everything.** No syncing on edit, no
  syncing on open, no syncing while the game runs.
- **`.ubi` is part of the game.** It exists only for 117, at
  `<install>\mods\.ubi`, and holds Ubisoft's own shipped mods — on this machine
  *Free Farmfields*, *Hot Reload*, *Infinite Material* and *Infinite Money*,
  all commented out in the live profile. Nothing to unsubscribe from and nothing
  to move. It stays in the grid, since the profile switches it and somebody may
  want *Infinite Money* on; it leaves the checklist's arithmetic entirely, so
  *everything is the library's* can be true.
- **Another tool's state is read, never written.** iModYourAnno's JSON and
  profiles, Taludas' manager's settings, the mod.io cache's `state.json`.
- **The account is touched only when asked, per mod, with the consequence
  said** — and in the end not by the Studio at all: the game's client does
  not act on an unsubscribe made outside the game (phase 6), so the Studio
  says what to unsubscribe from and the player does it in game.

---

## 9. What was checked, and what is not known

Checked, on this machine and in the code:

- `Truth.differ` counts strays regardless of `exclusive`, and `set_truth`
  colours that cell as a warning regardless.
- `library.adopt` keeps the folder's name, `-` included, for the junction.
- 117's `.ubi` holds four first-party mods; 1800 has none.
- 117's `active-options.jsonc` exists, is keyed by ModID, and the loader log
  lists each value it applied followed by *31 mod options found*. No Studio code
  reads it.
- iModYourAnno's tweak mechanism, from its source: Skip flags on the original,
  a generated `.imyatweak.include.xml`, an appended `<Include>`, values kept in
  `.imya\tweaks\<folder name>.json`; profiles in `.imya\profiles` as lists of
  folder names.
- The Studio's reader over a tweaked mod: 646 ops, 50 from the tweak files, 50
  skipped originals, all markup carried.
- Steam app ids from the `appmanifest` files: 916440 and 3274580.

Not known, and each wants one look before the phase that depends on it:

- **What the mod.io client does on unsubscribe** — whether it deletes the cache
  folder, when, and whether it re-downloads if `state.json` and the account
  disagree. Answered by the probe, 26 Sep: through the API it keeps the copy
  and does not push the subscription back; in the game's own browser it drops
  the account's hold at once and keeps the folder while another account holds
  it; and a fresh start of its folder clears every other account's holds,
  rebuilding from the signed-in account's subscriptions. Still unseen: a copy
  only the game's account holds going when it unsubscribes in game. 117 has
  no in-game browser.
- **Whether regenerating iModYourAnno's output over an already-tweaked folder is
  idempotent**, as its own re-export suggests — or whether the Studio needs the
  untweaked original kept. Decides whether the library keeps a pristine copy.
- **Whether exporting a loaded mod back to its own folder** writes the
  `.imyatweak` ops back into their own file. A narrow path — aim 2 patches
  rather than edits — but a test.
- **Whether 117 also honours a `-` prefix or an `activation.json`**, as a
  summary of the loader's documentation on profiles suggested. If so, it changes
  how the Studio switches a 117 mod that is not in the library.
- **Whether 117 re-reads `profile.txt` only at start-up** — still open from the
  loadouts proposal; Play makes it moot for the Studio's own launches.
- **How long after launch the loader log is rewritten**, and how to tell the
  new log from the last one — its first line's timestamp, most likely.
- **Ubisoft Connect's launch URI and game ids**, for installs that are not
  Steam's.

---

## 10. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 1 | Mod options as one thing: the name; 117's `active-options.jsonc` read on a folder read; iModYourAnno declarations and values read; **iModYourAnno values re-applied after an update** | nothing new; the idempotence check |
| 2 | The fourth card reads the loadout: *Play changes N*, live header, warnings only for drift; aim 3 retitled with its two buttons; *Fork this loadout…* and *Start from* | nothing new |
| 3 | Play and *Make this live*: the live pointer, links, 117 profile and options written, iModYourAnno output written, launch through the store; the Export tab re-scoped to publishing | 1, 2 |
| 4 | The loader log check after Play | 3 |
| 5 | The conversion flow: survey, duplicates, snapshot, profiles as loadouts, the `adopt` fix, copying out of the cache, the checklist | 3, 4 |
| 6 | Unsubscribing, signed in | 5; the unsubscribe probe |

Phase 1 first because problem 2 of section 6 loses a player's settings today,
with no word said; the rest of phase 1 is reading files that already exist.
Phase 2 is small and is most of the clarity the aim is missing. Phase 3 is the
real work and the point of the library. Phase 4 is small once 3 exists, and
phase 5 leans on it — a conversion without its proof is a leap of faith. Phase
6 waits on the one probe nobody has run.
