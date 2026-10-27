# mod.io integration: a proposal

What it would take to put mod.io inside the Studio, feature by feature, and the
order to do it in. Written against the mod.io REST API v1 documentation, the
two community mod managers that already talk to mod.io, the Studio's own census
tools (which have been calling the same API with a personal key since the
structure census), the mod.io files the games leave on this machine, and a
probe run against both games' API settings.

**Recommendation in one paragraph.** Build the update check first, the
download-and-import dialog second, and publishing third. The update check is
the smallest of the three, needs no login (an API key is enough), and reuses
almost everything the Studio already has: the loaded mod stack, the backups an
export takes, and the mod.io state files and loader logs the games write. The
download dialog is the biggest and is worth it, but it is the same download code
the update check needs, grown a search box. Publishing is last because it is the
one that can go wrong in public: neither game curates, so a published file is
live the moment it uploads.

Everything section 5 asked has now been answered except one thing, and the
answers did not change the order. They made the first feature better founded
and the third one riskier.

**Status (1 October 2026).** As below, with two things that have moved on: the
Studio now reads Anno 1800's mod.io cache too, where sections 1 and 6 say it
does not; and the account is reached through *Settings…* or a dialog's *Set up
mod.io account…*, not a *File › mod.io account…* item.

**Phases 0 to 3 are built:** the key and the sign-in, the update check
with *Download and update*, *Get a mod from mod.io…*, and *Publish update…*
on the Export tab. The README describes each as it is. Phase 4 has been
decided against, for the reasons in section 6.

---

## 1. What mod.io actually offers

### Two credentials, not one

| | API key | OAuth 2 access token |
|---|---|---|
| Gets you | read: browse, search, resolve download links | everything: subscribe, rate, add a mod, upload a file |
| Where from | `mod.io/me/access`, one per account | an email code flow the Studio runs itself |
| Limits | 60 requests a minute | 120 a minute, 60 of them writes |
| Lifetime | until revoked | one year by default (`date_expires` can shorten it) |

The token comes from two calls: `POST /oauth/emailrequest` with the API key and
an email address sends a short code to that address; `POST /oauth/emailexchange`
with the code returns `access_token` and `date_expires`. No browser handoff, no
password: the code is typed into the Studio. The Anno 117 Mod Manager (Taludas)
does exactly this: the user pastes their personal API key, then signs in by
email code, and only the one-year token is kept.

Every request, key or token, is a user's own. The Studio cannot ship a key: keys
tied to a game belong to Ubisoft, and a key tied to a person is that person's.
So the first step of any of these features is a field where the user pastes
their key, which is what both community managers do, and what
`tools/modio_list.py` has done since the census, with the key taken from
`MODIO_API_KEY`.

### Two obligations that come with authenticating users

- **Terms dialog before the first sign-in.** `GET /authenticate/terms` returns
  the text and the button labels; the exchange call then carries
  `terms_agreed=true`. Without it a new account gets `403` (error 11074). This
  is a mod.io requirement for anything that creates a session, not a nicety.
- **API terms.** Keep the key secure, stay under the limits, no scraping, and
  "include mod.io's branding at all times you are using the mod.io API". A
  "mod.io" wordmark on the dialog and a link to each mod's page covers it.

### The endpoints each feature needs

| Feature | Calls | Needs |
|---|---|---|
| Update check | `GET /games/{g}/mods?id-in=…` (100 ids a page) | key |
| Browse / search | `GET /games/{g}/mods?_q=…&tags=…&_sort=…` | key |
| Download | `GET …/files/{f}/download` → expiring CDN link, MD5 in the modfile | key |
| Publish an update | `POST /games/{g}/mods/{m}/files` (zip, `version`, `changelog`, `active`) | token |
| Create a mod | `POST /games/{g}/mods` (`name`, `summary`, `logo` required, `tags[]`) | token |
| Who am I | `GET /me`, `GET /me/mods` | token |

Newer games refuse `api.mod.io` for their mods and want `g-{id}.modapi.io`;
the census client already does that, and it is the client to lift. Anno 1800 is
game 4169 and Anno 117 is 11358, worth putting on `GameProfile` next to the
categories.

The probe settles the read half: both games are `OPEN` and `DOWNLOADS`, and
neither sets `AUTHORISED_DOWNLOADS` or `PAID_DOWNLOADS`. So a personal API key
downloads a mod file directly, with no token and no ownership check. The update
check and the download dialog therefore never need a sign-in at all, which is
most of why they come first.

### What is already on this machine

The game's own mod browser keeps its state under
`C:\Users\Public\mod.io\<game id>\`:

```
4169\metadata\state.json           19 mods the 1800 browser installed, each with
                                   its full mod.io profile: id, name, modfile id,
                                   modfile version, date_updated, PathOnDisk
4169\mods\3031538\[Gameplay] Extended Agriculture Modules (Kurila)\
11358\metadata\state.json          empty: the 117 in-game browser is "planned
                                   for 2027" per the Anno Union
```

The root path is in `%LOCALAPPDATA%\mod.io\globalsettings.json`
(`RootLocalStoragePath`). `core.loadout.mod_paths` hard-codes
`C:/Users/Public/mod.io/11358/mods`; it should read that file instead, and the
same code serves the update check.

**The 1800 cache is a real mods folder, and the Studio does not read it.** The
loader log says so outright, with an absolute path where every other mod gets a
relative one:

```
Load [Building] Nate's Windmill (Jakob) (2.0.1, jakob_Nates_Windmill)
  from C:\Users\Public\mod.io\4169\mods\3229975\[Building] Nate's Windmill (Jakob)
```

236 of the 240 load lines say `from mods\`; two name the cache. So
`mod_paths` must list `4169/mods` for 1800 the way it lists `11358/mods` for
117, or a reading whose whole claim is that it shows the game you actually play
leaves out whatever the in-game browser installed.

Three things follow from how that folder is shaped, and each of them is a design
decision rather than a detail:

- **The cache has an extra level.** It is `mods/<mod id>/<mod folder>/`, not
  `mods/<mod folder>/`. `logged_order` already matches the absolute line and
  takes everything after the first `mods` segment, so it credits that mod as
  `3229975/[Building] Nate's Windmill (Jakob)` — the numeric directory read as
  a host and the real mod as one bundled inside it. Harmless for ordering, since
  the pair still sorts as a unit, but the numeric level is not a mod and should
  be recognised as the mod.io id it is rather than carried around as a folder
  name.
- **The log hands over a mod.io id for free.** That absolute path contains the
  id. For any mod the game loaded out of the cache, the Studio can read the id
  off the log without a single request, and without `state.json`.
- **The same mod is often installed twice.** Nineteen mods sit in the cache and
  exactly one of them is loaded from it. The rest are superseded by copies in
  the installation's own mods folder — *Extended Agriculture Modules* is
  `[Gameplay] …` in the cache and `[Seal] …` in `mods\`, one ModID, two folders,
  two names. The loader keeps one copy per ModID, which the Studio already
  models. What that means for the update check is in 3.1: the mod.io id belongs
  to the **ModID**, not to the folder.

On this machine `Documents\Anno 1800\mods` is empty, so `from mods\` is the
installation's folder. Both are already in `mod_paths`; the cache is the one
missing.

### What the community managers do

- **Anno 117 Mod Manager** (Taludas; Python/Tk): personal API key pasted in, email
  code sign-in, one-year token kept under `%APPDATA%`, downloads through the
  API with dependencies fetched first, a background update check for subscribed
  mods at startup, presets that map to the game's `mods/profiles/`.
- **iModYourAnno** (anno-mods; C#, Anno 1800): API key in Settings › mod.io
  Integration for the mod browser and collections; queued downloads with pause
  and cancel.

Neither publishes. Both are managers, not authoring tools, so that is not
evidence the API forbids it; the API documents `Add Mod` and `Add Modfile` for
exactly this, and mod.io's own engine plugins upload through them.

---

## 2. Holding the key sensitively

Two secrets: the API key (every feature) and the access token (publishing).
Neither may go where the Studio's other state goes.

- **Not the settings INI.** `QSettings` is plain text and the file is copied
  into bug reports.
- **Not the project file.** Projects live in Documents and get shared.
- **Not exports, modinfos, logs, or error text.** The census client already
  redacts the key out of every error message; keep that.

The census tools take the key from `MODIO_API_KEY`, which is right for a
command line and wrong for a window: nobody sets an environment variable to use
a desktop app, and one set for the Studio would be readable by everything else
the user runs. So the app needs a store of its own.

**Recommendation: DPAPI, through `ctypes`, no new dependency.** Windows'
`CryptProtectData` encrypts a blob so that only the same Windows user on the
same machine can read it back; with an application-specific entropy string, not
even another DPAPI-using program running as that user can decrypt it without
knowing the string. Verified here from stdlib Python: a 31-byte key wraps to
294 bytes, and the wrong entropy is refused. Keep the blob at
`%LOCALAPPDATA%\AnnoModdingStudio\modio\credentials.bin`, machine-local, next
to the index cache, not in Documents where it would be synced and backed up,
with a JSON body `{"api_key": …, "token": …, "token_expires": …, "email": …}`
inside the encryption.

What this protects against: another account on the machine, a copied file, a
backup, a synced Documents folder. What it does not: software running as you.
That is the same standing as a browser's cookie store, and it is honest to say
so in the README rather than imply more.

The Windows Credential Manager is the alternative (also reachable from
`ctypes`, and visible to the user in Control Panel). Its per-entry blob limit
is a few kilobytes, which a mod.io token may brush against, so the DPAPI file
is the safer default. The whole thing is a 60-line `core/secrets.py` with a
fake for tests.

The rules around it matter as much as the storage:

- **One place to enter it:** `File › mod.io account…`, the way
  `Your creator name…` is one place. Paste the key, with a link to
  `mod.io/me/access`. Sign in by email only appears once a feature needs a
  token, and shows the terms dialog first.
- **Test isolation.** Route the path through `places` so `ANNO_STUDIO_HOME`
  and the suite's isolation keep tests away from the real file, exactly as
  settings and projects are kept.
- **Forget** deletes the file and calls the logout endpoint. Show the key
  masked; never put it in a message.
- **Nothing on startup.** No request is made until a click asks for one. The
  Studio has made one network call in its life (the community GUID ranges, on a
  menu click) and that policy should hold.

---

## 3. Feature by feature

### 3.1 Check installed mods for updates: first

**What it does.** With a mods folder loaded, one click asks mod.io about every
mod in it and says, per row: *1.6.3 installed · 1.7.0 on mod.io, 3 Sep*.
A menu entry (`File › Check mod.io for updates…`) and a column in the
*Load installed mods* table, since that table is already "what the game is doing
with this mod tonight".

**Why first.** It needs a key and nothing else, because neither game requires a
token to read or download; it is one request per hundred mods (a 65-mod folder
is one call); and the identification problem is mostly solved by files already
on disk.

**Which mod.io mod is this folder?** In order, exact answers first:

1. The loader log loaded it out of the cache, so the absolute path it recorded
   carries the mod id. No request, no state file.
2. The folder sits under the mod.io cache: its parent's name is the mod id, and
   `state.json` gives the installed modfile id and version.
3. The project already remembers a link, from an earlier check, a download
   through the Studio, or a *Link…* the user made. Stored as `modio_id`, saved
   with the project, not secret.
4. Match by name: mod.io `name` against the modinfo's `ModName`, with the
   `[Category]` stripped from a folder name. The nineteen cached mods here all
   match exactly that way. A match by name is offered, not assumed: the row
   says *matched by name* and *Link…* lets the user pick from a search if it
   guessed wrong. Unmatched rows say so and stop.

**The id belongs to the ModID, not the folder.** This is what the cache's
nineteen-versus-one taught. Most mods in the cache are not the copy the game
loads; a manually installed copy of the same ModID wins instead, under a
different folder name and often a different category prefix. If the Studio
attached the mod.io id to a folder it would learn nothing about the copy that
actually runs. Attached to the ModID, one cache record answers for every copy of
that mod on the machine — and the answer is useful precisely for the manually
installed copy, which is the one with no mod.io machinery behind it.

**What counts as "newer".** Version strings are typed by authors and are not
reliably comparable. Prefer the modfile id when the cache has it; otherwise
compare version strings, and when they differ but do not order, say *a
different version is on mod.io* rather than *newer*. `date_updated` on the mod
against the folder's newest file is a last resort and is labelled as one.

**Then what.** At minimum *Open on mod.io*. *Download and update* is the
download machinery of 3.2: fetch the zip, back it up through the existing export
backup, replace the folder, re-read. Patches are the reason this is safe, and
the point should be made on screen: a patch of yours loads after the mod, so the
update does not take your changes with it.

**The part nobody else can do.** After the folder is re-read, the Studio knows
every field the update changed and whether your patch still applies, because
that is what reading a mod *is* here. *This update changed 12 fields; 2 of them
your patch also sets* is a sentence no manager can write, and it is nearly free
once the re-read happens.

**Pieces.** `core/modio.py` (client lifted from `tools/modio_list.py`: pacing,
`retry-after`, redaction, cancellation, `User-Agent: AnnoModdingStudio/…`),
`core/modio_cache.py` (read `globalsettings.json` and `state.json`), the
`mod_paths` fix and the mod id read off the log, `modio_id` on `ModLayer`, an
`UpdatesWorker` on the `ModsWorker` pattern, one dialog. Tests against a fake
transport, as `tests/test_modio_fetch.py` does.

### 3.2 Get a mod from mod.io, download and import in one click: second

**Scope.** Start with a *Get a mod from mod.io…* dialog rather than a browser: a
search box, a result list (name, author, downloads, last updated, version,
size), and one button. The button downloads the zip, unpacks it into the game's
mods folder, backs up anything it replaces, and hands the folder to the path
*Import changes from a mod* already walks, which lands in the asset editor with
the mod read. That is the existing *Change a mod you downloaded* aim with the
download bolted on the front, and it is the whole feature for most sessions.

A full browser (thumbnails, tags, sort by popularity, paging, dependencies,
collections) is the same dialog with more rows and more columns, and can grow
from this one. Thumbnails come from the CDN, not the API, so they do not count
against the sixty a minute; cache them under the index cache.

**Download.** `tools/modio_fetch.py` already resolves the download link,
follows the CDN redirect, checks MD5 against the modfile, refuses files whose
virus scan flagged them, and keeps zip members from escaping their folder
(`safe_path`). `tools/modio_roundtrip.folders_of` knows the three shapes a mod
zip comes in: a mod folder, the mod's contents at the root, or a box holding
several mods. All of that moves into `core/`.

**Where the files go.** The game's mods folder, `Documents\Anno 1800\mods` or
`Documents\Anno 117 - Pax Romana\mods`, under the zip's own folder name, since
that name is what 1800 keys load order on. Never into the mod.io cache: that
folder belongs to the game's own client and its `state.json`, and a folder
written there that the state file does not know about is a folder the game may
delete.

**Do not subscribe, and the reason is stronger than it looked.** Subscribing
was going to be rejected for tidiness; it turns out not to work for delivery at
all. Subscribing to an 1800 mod on the mod.io website does not put a file on
disk and the game does not load it. Only the in-game browser does that: subscribe
there, close the browser, and the mod is downloaded into
`C:\Users\Public\mod.io\4169\mods` and the game reloads its mods. An API
subscribe is the same act as the website one, so it would deliver nothing now
and quietly deliver a second copy later, the first time the user opens the
in-game browser. Download the file and leave subscriptions alone.

**Dependencies.** mod.io flags them and has an endpoint listing them; both games
are set to `opt out`, so a mod may declare them and most do not. Anno modinfos
also name them in `Dependencies.Require`. Fetch them first, as the 117 manager
does, but in a second pass; the first version can list them and stop.

**Effort.** The largest of the three: a dialog with a live search, a worker
with progress and cancel, thumbnails, unpacking, and the handoff into the
import path. The client and the download are shared with 3.1, so most of what
is new here is UI.

### 3.3 Publish from the Export tab: third

**Neither game curates, so there is no safety net.** Both are
`curation: none` — an uploaded file is live the moment it finishes uploading.
There is no moderation queue to catch a mistake, no draft state between the
click and the public mod page. That single fact shapes the whole feature: the
confirm dialog is not a formality, it is the only thing between a misclick and a
published file, and it should say exactly what is being sent, to which mod, as
whom, and that it goes live immediately.

**Two acts that look like one.** Creating a mod on mod.io wants a name, a
summary, a description, tags, and a logo image the API insists on: 16:9, at
least 512 by 288, at most 8 MB. The website walks it in four steps — mod
profile, media, dependencies, files. That is a form the website does better,
once. Publishing an *update* wants a zip, a version and a changelog, and that is
repetitive and mechanical: exactly what an authoring tool should do. So:

1. **Link this project to a mod on mod.io.** Once, on the Export tab: pick from
   `GET /me/mods` (or paste the mod's URL). The first publish happens on the
   website, with *Open mod.io to create it* as a button. The link
   (`modio_mod_id`) is saved in the project.
2. **Publish an update.** Export as now, zip the exported `[Category] Name/`
   folder as its top-level entry (the shape most mods on mod.io ship in),
   and `POST …/files` with the version from the Export tab, a changelog box, and
   `active=true`. Confirm first with exactly what will be sent and as whom
   (`GET /me` username). Refuse when the version equals the file already live.

*Create on mod.io* from inside the Studio can come later; a logo picker and a
summary box are not hard, but they are not the part that hurts.

**The Ubisoft link is real, and only for 117.** Confirmed by hand on both games'
*Add mod* page. Anno 1800 opens the mod profile form straight away. Anno 117
opens a mod.io dialog instead: *"A Ubisoft account is required in order to add
mods"*, with nothing behind it but a **Link your Ubisoft account** button.

Three consequences, none of them obvious from the API alone:

- **The Studio cannot check first.** `GET /me` returns id, username, avatar,
  join and online dates and a profile URL. There is no field for linked or
  connected accounts, so there is no way to ask whether the Ubisoft link exists
  before trying.
- **The game's own settings do not predict it.** Both games report
  `submission: all — anyone may add mods`. For 117 that is true of mod.io and
  false of the game. So the flag cannot be used to decide whether to offer the
  publish button.
- **Therefore: attempt, then translate.** Publish, and turn a refusal into
  *link your Ubisoft account on mod.io, then try again* with the link, rather
  than an error code. For 117 it is worth saying so up front too, on the link
  step, since it is a one-time errand on the website and finding out after
  writing a changelog is worse.

**Tags the Studio could fill in.** mod.io's `Type` group is what
`GameProfile.categories` mirrors, and both games match exactly (see section 4).
Two more are derivable rather than typed: 117 offers `Features: Customizable`,
which is true of exactly the mods that declare `Options` in their modinfo — and
reading those options is something the Studio already does. 117 also offers a
`Difficulty` group whose values sit beside the `Difficulty` field the Studio
already writes into a 117 modinfo. Proposing those two on publish costs nothing
and is more than the website does unprompted.

**Effort.** Medium once the client and credentials exist: the email sign-in,
the terms dialog, a multipart-form encoder (the stdlib has none; about forty
lines), zipping, and one confirm dialog. Nothing about it is hard; all of it
must be careful, since a wrong click publishes to a live page.

---

## 4. Cross-cutting decisions

- **One client, in core, stdlib only.** `urllib`, no `requests`; the app has two
  dependencies and this does not justify a third. Paced to the key's limit,
  honours `retry-after`, redacts the secret from every error, takes a cancel
  flag, runs on a `QThread`. Every request identifies the app in `User-Agent`.
- **Every feature degrades to "not checked".** No network without a click; no
  feature breaks when offline; the update column is simply empty.
- **Tests never touch the network.** Fake transport, as the census tests do;
  fake secret store; the credential path under the same isolation as settings.
- **Say what is sent.** A README section: the API key on every request, the
  mod zip on publish, your email during sign-in, nothing else; mod.io sees your
  address and your downloads count.
- **The categories are right, and they are one group of four.**
  `GameProfile.categories` was mirrored by hand and the probe confirms it
  matches mod.io's `Type` group exactly, same values and same order, for both
  games. Two caveats now visible. `Type` is a checkbox group, so a mod on mod.io
  may carry several types while a modinfo `Category` is one string; the Studio
  can only ever set one, and should not clear the others. And `Type` is one of
  three or four groups per game — 1800 adds `Region` and `Language`, 117 adds
  `Difficulty`, `Features` and `Language` — which the Studio models not at all.
  Checking the list against `GET /games/{g}` on demand would keep it honest as
  mod.io changes it.

---

## 5. What was checked, and the one thing still unknown

`tools/modio_probe.py` read, for both games, the game-level settings a
third-party tool depends on, and with `--me` also asked who the token belongs to
and what mods it owns. It answered its question once and was removed in Stage 5
of `docs/proposals/consolidation-proposal.md` (decision 5.5); what it found is
kept here, and git has the script. What it found:

| | Anno 1800 | Anno 117 |
|---|---|---|
| game id | 4169 | 11358 |
| api access | `OPEN` + `DOWNLOADS` | `OPEN` + `DOWNLOADS` |
| curation | none, live at once | none, live at once |
| submission | anyone may add mods | anyone may add mods |
| dependencies | opt out | opt out |
| mods listed | 858 | 374 |
| `Type` tags | matches `games.py` | matches `games.py` |
| other tag groups | Region, Language | Difficulty, Features, Language |

Four things that settles, each of which moved a decision above: a personal key
downloads directly, with no token and no ownership check (3.1, 3.2); nothing is
curated, so publishing is instantly public and the confirm dialog carries the
whole weight (3.3); the hand-mirrored categories are correct but are one group
of several (4); and mod.io's `submission` flag says nothing about Ubisoft's gate
(3.3).

Two questions the probe could not answer were settled by hand instead:

- **The Ubisoft link.** Required to add a mod for 117, not for 1800, checked on
  both games' *Add mod* page. Handled in 3.3.
- **The 1800 mod.io cache.** The game does load from it, and the loader log
  proves it with an absolute path. A website subscribe alone delivers nothing;
  the in-game browser is what downloads the file, on closing. Handled in
  *What is already on this machine* and in 3.2.

**Still unknown, and it only affects phase 3.** Whether `POST /games/11358/mods`
and `POST …/files` refuse an unlinked account the way the website does, or
whether the gate is only in the web interface. The design above does not depend
on the answer, since it attempts and translates the refusal either way, but the
wording of that message does. Worth one test on an unlinked account, adding a
mod with `visible=0` and deleting it afterwards. The probe also has not yet been
run with `--me`, so the email sign-in producing a working token is assumed
rather than observed; that is phase 3's first task, not a blocker for phases 1
and 2.

---

## 6. Order of work

| Phase | Delivers | Depends on |
|---|---|---|
| 0 | `core/modio.py`, `core/secrets.py`, `File › mod.io account…` | nothing |
| 1 | `mod_paths` reads the 1800 cache and `globalsettings.json`; check for updates: matching by ModID, the column, *Open on mod.io* | 0 |
| 2 | *Get a mod from mod.io…*: search, download, unpack, import; *Download and update* in phase 1 | 0 |
| 3 | Email sign-in and terms; link project to a mod.io mod; *Publish update* | 0 |
| 4 | ~~Thumbnails, tags, sorting, dependencies; *Create on mod.io*~~ | not built |

Phase 0 is small and is mostly moving code out of `tools/`. Phase 1 is the
first thing a user sees and is a good first release; it now also fixes a gap
that exists whether or not any of this is built, since the Studio does not
currently read a mods folder the game loads. Phase 2 is the most work and the
most used. Phase 3 no longer waits on a probe, only on care.

**Phase 4 is not going to be built, and the decision is a design one rather
than a shortage of time.** The two halves fail for different reasons.

A full browser inside the Studio -- thumbnails, tags, sorting, paging -- is a
great deal of work to make feel good, and a web browser already does all of it
better: a list to scan, a page per mod with its images and comments, tabs for
the ones worth a second look, translation for a description in another
language. The community's one attempt at an integrated browser shows the
ceiling. What the Studio has instead is a search box, and it turns out to be
the better shape: it streams no images, so it is instant, and it nudges the
workflow into *find it in the browser, paste its name into the Studio*, which
is shorter than *download it by hand, open the import dialog, find the folder*
and is the whole of what an authoring tool needs from a browser.

Creating a mod from inside the Studio fails on a different point. It would
have to know exactly what mod.io requires of a new mod -- which fields, which
constraints on the logo, which tags -- in order to put those fields in a form,
and that is exactly the part of mod.io's surface most likely to change. Sending
a new file to a mod that exists is a stable, narrow contract; creating one is
a form whose shape belongs to the website. The Studio keeps to the narrow one.

Two pieces of phase 4 were worth having and are built: the search box takes
a pasted mod.io page address, looked up by the slug the address carries, so
the browser workflow is one paste; and a mod's declared dependencies are
listed before anything is fetched, with the ones already loaded marked, and
fetched first if wanted. Nothing else on the list.
