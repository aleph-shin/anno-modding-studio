# Collections: a proposal

Downloading a curated set of mods without thinking about them one at a time.
Written against mod.io's collections API as it answered a probe of both Anno
games today, the Studio's existing mod.io code (phases 0 to 3 of
[the mod.io proposal](modio-integration-proposal.md), all built), and what a
saved project already records about the mods it was built on.

**Recommendation in one paragraph.** Build one dialog, not two features. Both
ideas — a mod.io collection and somebody else's project — end at the same
question: *here is a list of mods; which of them do you already have, and shall
I fetch the rest?* Answer that question in one widget with a plan, a queue and a
per-row outcome, and then give it two ways in. Do mod.io collections first,
because the API hands over the whole list with download links in one request and
nothing new has to be invented to identify a mod. Do the project prompt second,
because it is the same dialog opened from `load_project` and the harder half is
matching, which `core/updates.py` already does. The thing worth building either
of them for is what happens after the install: the Studio reads the mods it just
put down and says where they fight. No mod manager can do that, and it is nearly
free here.

**Status (1 October 2026).** Phases A to C are built, as below; phase D, a
project's mod list copied or saved as a setup, is not. `core/collections.py`
(section 8) was never made: the plan and queue are `core/installplan.py`, and
the collection calls are in `core/modio.py`. Two things below have moved on
since: Play now writes a project's load order and on/off to the mods folder, so
*Switch installed mods to match…* is gone; and whether a mod's options can be
written back is answered -- Play writes Anno 117's `active-options.jsonc` and
1800's iModYourAnno settings. The dialog is at *File › Mods › Get a mod from
mod.io…*, and `loadout.write` is `switches.write`.

**As first built.** Phases A to C are built: `core/installplan.py` is the plan and the
queue, `ui/install_plan_dialog.py` the one dialog, *Get a mod from mod.io…* has
the **Collections** switch, and opening a project offers the mods it is missing.
The README describes each as it is. Three things differ from what is written
below, all on the side of doing less: *installed twice* is a note on a row
rather than a status of its own, since it is true of rows that are current and
rows that are older alike; **Stop** abandons the download in hand rather than
finishing it, because the mod in hand may be half a gigabyte; and a project's
load order and on/off are not written back to the mods folder — a mod the
project has switched off is left unticked instead, and *Switch installed mods
to match…* remains the way to make the folder match. Fetching the version a project
was built against, rather than the current one, is not built either: the
difference is said on the row. Phase D is untouched.

---

## 1. Two things are called a collection, and they answer different questions

| | a mod.io collection | a saved project |
|---|---|---|
| Is | a list of mod ids somebody curated | a workspace, with every mod it read recorded in it |
| Answers | *give me a good set of mods to play with* | *put me in the configuration this person was in* |
| Carries | ids, and nothing else | id, ModID, folder, version, load order, on/off, the mod's own settings, how it was read, and the changes themselves |
| Versions | always the current file | the version it was built against |
| Reach | 195 of them exist, in the game's own browser | one file, sent to one person |
| Made by | anyone, on the website | you, by working |

They are not competitors. The collection is the one with reach and the project
is the one that says more; the collection is a *source* and the project is a
*record*. The Studio should take both and land them in the same place.

What that place is, precisely: a **plan**. A list of mods wanted, each with one
of six answers — not installed, installed and current, installed but older,
installed twice, no longer on mod.io, flagged by the scan — a total size, and a
button. The plan is the whole feature. Everything else is where the list of
wanted mods came from.

---

## 2. What the probe found: collections are real, cheap, and bigger than expected

`GET /games/{g}/collections` answers `200` to a plain API key, for both games.
No sign-in, no token, no ownership check — the same standing the mod endpoints
have, and for the same reason: both games are `OPEN` + `DOWNLOADS`.

| | Anno 1800 (4169) | Anno 117 (11358) |
|---|---|---|
| collections listed | 160 | 35 |
| mods per collection, median | 37 | 27 |
| largest | 307 mods | 113 mods |
| download size, median | 1.24 GB | 0.21 GB |
| largest | 8.80 GB | 3.32 GB |
| over 100 mods | 10 | 2 |
| `incomplete` | 3 | 5 |
| `limit_collection_mods` / `min` | 100 / 3 | 100 / 3 |

Five things follow, and each one is a design decision rather than a detail.

**The list comes with its files attached.** `GET /games/{g}/collections/{c}/mods`
returns full **Mod Objects** — the same shape `core.modio.summarise` already
parses — each with its `modfile`, its version, its `filesize`, its md5 and a
live `binary_url`. So the whole install plan for a 37-mod collection costs one
request, not 37. That is the single fact that makes this feature small.

**"One click" would be a lie.** The median 1800 collection is 37 mods and 1.24 GB
compressed, 8.8 GB at the top of the range. That is minutes of downloading and
gigabytes on disk, and it will be interrupted. The queue has to be resumable,
has to survive one bad mod, and has to say its size before it starts. A dialog
that says *26 mods, 1.4 GB* and then shows a row per mod is honest; a progress
bar labelled *Installing collection…* is not.

**The cap is not a cap.** Both games declare `limit_collection_mods: 100` and
collections of 109, 113, 155 and 307 mods are listed anyway. Page the mods
endpoint; do not size an array from the game object.

**mod.io says when a collection is broken.** Eight of the 195 carry
`incomplete: true`, and the collection's `stats` carry an `incomplete` count.
That reads as *some of its mods are no longer here*, and it should be shown as
that, before the fetch: *this collection is incomplete — 3 of its mods are no
longer on mod.io*. Do not silently install 34 of 37.

**A collection follows HEAD.** The mods endpoint returns each mod's *current*
file. Nothing is pinned. Install the same collection twice a month apart and you
get two different sets of versions, with no way to ask for the older one. This
is the limitation that the project half exists to fix, and it should be said on
screen once rather than discovered: *a collection is a list of mods, not of
versions; you get whatever is current today.*

**Two more small findings.** `_q=` and `name_id=` both filter collections, so the
paste-a-URL workflow the Studio already has for mods works here with one new
branch — `core.modio.parse_page` accepts `/g/<game>/m/<mod>` and needs
`/g/<game>/c/<collection>`, which is the real page address
(`https://mod.io/g/anno-1800/c/jakobs-collection` serves *Jakob's Collection
collection for Anno 1800*). And the collection object carries no `profile_url`,
so *Open on mod.io* has to build that address from `game_name_id` and `name_id`
rather than read it.

**There is no collection archive.** mod.io does not hand over a collection as
one zip. It is N downloads and N unpacks, which is why section 3 is mostly about
a queue.

---

## 3. The plan, and the queue under it

### What the plan decides, before anything is fetched

For each mod.io id in the wanted list, the Studio already knows how to ask
whether it is installed — `core.updates.identify` climbs the ladder that the
last proposal built: the loader log's absolute path, the mod.io cache's
`state.json`, a link the project remembers, then a name match. Run it once for
the loaded mods and the plan is a lookup, not a search.

| The row says | Because | The button offers |
|---|---|---|
| not installed | no copy of that ModID anywhere | fetch |
| current | installed modfile is mod.io's current one | nothing, ticked |
| older | a newer file is on mod.io | fetch, replacing |
| installed twice | in `mods\` and in the mod.io cache both | nothing, with a note |
| gone | the collection names it; mod.io no longer lists it | nothing, greyed |
| flagged | `virus_positive` on the current file | never; skipped with a reason |

The "installed twice" row is not an edge case invented for tidiness — it is what
this machine's own mods folder looks like. Nineteen mods sit in the 1800 mod.io
cache and exactly one of them is the copy the game loads; the rest are
superseded by hand-installed copies of the same ModID under different folder
names. A plan that matched on folders would offer to install eighteen mods that
are already there. Matching on ModID, which is what `ModLayer.modio_id` is
deliberately attached to, gets it right.

The plan's footer is the sentence that matters: **"22 of these 37 are already
installed. Fetching the other 15 will download 640 MB and use about 1.1 GB."**
Free space is checked against the uncompressed figure before the button is
enabled.

### The queue

`ModioGetDialog._fetch_all` is already this loop — it exists to fetch a mod's
dependencies before the mod — and it is about twenty-five lines: ask mod.io for
each mod's current file, download with the md5 and size it was promised, unpack
through `core.modzip.install`, move on. Three things have to change when it
moves into core and is pointed at 37 mods instead of 3.

- **One bad mod must not lose the other 36.** Today an exception aborts the
  batch and the `finally` deletes the scratch folder. A queue records an outcome
  per row and carries on: *installed*, *skipped (flagged)*, *failed (checksum)*,
  *not attempted*.
- **Stopping is a resting state, not a failure.** Cancel finishes the mod in
  flight, leaves everything already installed installed, and says
  *12 of 37 installed; the rest are still on mod.io*. Reopening the dialog
  re-plans and offers the 25 — which needs no new state anywhere, because the
  plan is computed from disk every time.
- **Do not re-ask for every download link.** The listing's `binary_url`s are
  live and expire within hours; a 37-mod queue finishes well inside that. Use
  them, and re-fetch the single mod only when one is refused. That keeps a
  37-mod install to about two requests' worth of listing instead of 37 against a
  60-a-minute key.

Everything the existing code already refuses, it keeps refusing: a flagged file
is never downloaded, `modzip.safe_path` keeps zip members inside their folder,
`modzip.set_aside` moves aside anything replaced, and nothing is ever written
into `C:\Users\Public\mod.io\<game>\mods` — that folder belongs to the game's own
browser and its `state.json`, and 37 folders it does not know about is 37
folders it may delete.

### Where it goes in the UI

`File › Get a mod from mod.io…` already has a search box that takes a name or a
pasted address. Give it a **Mods / Collections** switch rather than a second
menu entry: `_q` hits both endpoints the same way, a pasted address resolves
through the same `parse_page`, and a user who pastes a collection link into the
mod search should get the collection rather than nothing. Choosing a collection
swaps the single-mod panel for the plan table. One dialog, one search box, two
kinds of result.

---

## 4. Somebody else's project

### The prompt already has a precedent

Opening a project that names a base file asks *"This project was built against:
… Load it now?"* and opens the project anyway if you say no. A project whose
layers name mods you do not have should ask the same question at the same
moment, in the same words:

> **This project uses 9 mods.** 6 are installed; 3 are not.
> [Fetch the 3 from mod.io] [Open it as it is]

Declining has to keep working, and it does, for a reason worth stating: a saved
project carries every change every layer made *inline*. It opens, reads and
displays correctly with not one of those mods on disk. What you lose by
declining is the game, not the Studio — the project is complete as a document
and incomplete as an installation. The prompt should say so, because it is the
difference between a nag and an offer.

### Matching a layer to a mod.io mod

`ModLayer.modio_id` is filled where something said so: the loader log, the
download cache, an update check, a *Link…*. In a project made by someone who
used those features, most layers arrive with an id and the plan is immediate.
Where it is zero, `updates.check` already falls back to a name search — one
request per unmatched mod, the same cost it pays today — and an unmatched layer
lands in the plan as *could not be found on mod.io*, with `mod_id` and the
folder name shown so the user can go looking by hand. A project made against
mods that are not on mod.io at all is a normal thing and must read as one, not
as an error.

### What a project carries that a collection cannot

This is the argument for doing this half at all, and it is four things:

- **The versions it was built against.** `ModLayer.version` is what was read.
  So the plan can say *built against 1.6.3; mod.io has 1.7.0* and offer either —
  which the mod.io half structurally cannot, and which matters most for exactly
  the project being shared, since the author's patch was written against those
  versions.
- **Load order and on/off.** `ModLayer.order` and `ModLayer.active`, and
  `core.loadout.write` already knows how to switch a mod on or off in each
  game's own way — 1800's `-` prefix, 117's profile file. So a project can
  restore a loadout, not just an install list. A mod.io collection has no
  concept of either.
- **Each mod's own settings.** `ModLayer.options` records what a configurable
  mod was set to. A configurable mod does different things at different
  settings, so this is part of what the shared setup *is*. See the honest
  caveat in section 6.
- **How each mod was read.** `ModLayer.read_as` exists precisely so that
  re-reading a mod reproduces the same change set. It is the field that makes
  the next idea work.

### Sharing a setup without sharing a novel

A project with a mods folder loaded carries the changes of every mod in it,
which is what makes it self-contained and also what makes it large. For sharing
a *set-up*, those payloads are dead weight: once the mods are installed, the
Studio can reproduce them exactly, because `read_as` and `options` say how.

So: **Save a copy as a setup** — the same `.annoproj.json`, with each layer
reduced to its identity (`origin`, `name`, `mod_id`, `modio_id`, `version`,
`order`, `active`, `options`, `read_as`, `load_after`) and its changes dropped,
keeping the project's own mod and rules whole. Opening one prompts for the
mods, fetches them, reads them with the settings recorded, and is then byte for
byte the project that was saved. That is a small file, it is the thing people
actually want to send each other, and it needs no new format — only a flag on
save and a re-read on open.

Worth measuring before committing to it: how big a real project with a 65-mod
folder loaded actually is. If it is 300 KB the thin version is a nicety; if it
is 30 MB it is the point.

---

## 5. The part nobody else can do

A mod manager installs 37 mods and hopes. The Studio installs 37 mods, reads
them, and answers the question the game never will:

> **37 mods installed.** They claim 4,180 fields between them. 9 fields are
> claimed by two mods; in 6 of those the later mod silently wins.

`core/conflicts.py` already computes exactly this, and already knows the
distinction that makes it useful rather than noisy: two mods editing different
parts of one asset is what narrow ModOps are *for* and is not a warning, while
two mods setting the same field to different values is one mod having no effect.
It already discounts the intended cases — a patch, or a mod whose modinfo
declares it loads after the other.

This costs nothing new. Reading a mods folder is what the app does; the
collection install ends by re-reading the folder anyway, because that is how the
new mods appear in the stack. The conflict report is the last line of a dialog
that was going to run that code regardless.

It is also the honest answer to *why is this in a modding tool rather than a mod
manager*. The download is table stakes and two community managers already do it.
Telling you that three mods in the collection you just installed are fighting
over the price of a Schnapps Distillery is not something either of them can do.

---

## 6. Decisions taken once

- **Never subscribe, never follow.** The last proposal settled this for mods and
  the reasoning is stronger for collections: an API subscribe puts nothing on
  disk for Anno, and would quietly deliver a second copy of all 37 the first time
  the in-game browser is opened. Following a collection is the same class of act
  against the user's account. Download the files; leave mod.io's account state
  alone. This also keeps the whole feature on an API key, with no sign-in.
- **Nothing on startup.** No request until a click asks for one, and the project
  prompt counts as the click only after the user says yes — opening a project
  must not phone mod.io on its own.
- **The folder is named in plain text, always.** A 1.4 GB install into the wrong
  mods folder is the worst thing this feature can do, and the dialog already has
  the folder picker `ModioGetDialog` uses.
- **Free space is checked first**, against `filesize_uncompressed`.
- **The plan is computed from disk every time**, never cached. A cached plan is a
  wrong answer about what is installed.
- **Branding stays.** mod.io's terms require their mark wherever their API is
  used; `ui/modio_brand.py` already carries it, and the collection panel links to
  the collection's page and to each mod's.
- **Tests never touch the network.** Fake transport, as the existing mod.io tests
  do. A collection's mods endpoint is a fixture like any other.

---

## 7. What was checked, and the one thing that is not known

Checked today, read-only, against both games with a personal key:

- `GET /games/{g}/collections` — `200` on a key alone, both games, 160 and 35.
- `GET /games/{g}/collections/{c}` — `200`; fields as in section 2; no
  `profile_url`, no archive.
- `GET /games/{g}/collections/{c}/mods` — `200`; full Mod Objects with `modfile`
  and a live `binary_url`; `result_total` above `limit_collection_mods`.
- `_q=` and `name_id=` filter collections.
- `https://mod.io/g/anno-1800/c/jakobs-collection` is the page address.
- The size, count and `incomplete` figures in section 2, over all 195.

Not checked, and only affecting section 4:

**Whether a mod's chosen options can be written back to the game at all.**
`core/settings.py` reads what a mod declares and the Studio answers those
questions *for its own reading* — that is what `ModLayer.options` is. Where the
game keeps the answers the player gave in its own mod menu, and whether that is
a file a tool may write, has not been looked at. Until it is, restoring a shared
project's options should set how the Studio reads those mods and say plainly
that the game's own mod menu still has to be set to match, with the list to
copy. Pretending otherwise would be the one dishonest thing in the feature.

---

## 8. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| A | `core/collections.py`: list, search, fetch one, fetch its mods, page it; `parse_page` accepts `/c/`; a `Plan` over a list of mod.io ids against what is installed | nothing new |
| B | The plan table and the resumable queue; `_fetch_all` moved to core and grown per-row outcomes; the **Collections** switch in the get dialog | A |
| C | `load_project` notices layers whose mods are missing and opens the same dialog; matching by `modio_id`, then by name; load order and on/off restored through `loadout.write` | B |
| D | *Save a copy as a setup*; *Copy this project's mod list* as mod.io addresses, for pasting into the website's own collection builder | C |

Phase A is pure and testable and is most of a day. Phase B is where the work is,
and it is UI work: a table with six states, a queue that survives being stopped,
and a size line. Phase C is small if B is built right — it is the same dialog
with a different list handed to it — and its cost is all in matching, which is
borrowed. Phase D is worth doing only once C shows that people send each other
projects.

**Not going to be built**, for the same reason the last proposal declined
*Create on mod.io*:

- **Creating or editing a mod.io collection from the Studio.** It is a form —
  name, summary, logo, category, tags, a minimum of three mods — whose shape
  belongs to the website and will change. Sending files to something that exists
  is a narrow stable contract; making the thing is not. *Copy this project's mod
  list* in phase D covers the real need in four lines of clipboard code.
- **A collection browser with thumbnails, tags and sorting.** Same argument as
  before, and stronger: mod.io's own collection pages show the mod list with
  images, and a search box plus a pasted address gets you there in one step.
- **Following, rating or subscribing.** Section 6.
