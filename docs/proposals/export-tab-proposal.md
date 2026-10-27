# The Export tab's three ways out: a proposal

A mod written in the Studio can go three places: into the game you play, onto
mod.io, or into a folder or zip to share some other way. The Export tab offers
all three, but leads with the one used least, makes the one used most a detour
through two other loadouts, and leaves the first publish to mod.io to be
guessed at. This proposes one block under the preview with a row for each
way out, each saying where the mod stands and offering the next step.

Written on 19 October 2026, against `main` at `8424d6a`. Rewritten on 24
October against `main` at `4a5fe3e`, on top of the
[projects and loadouts](projects-and-loadouts-proposal.md) work, which section
4 of the first version waited on.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (24 October 2026).** Phases 1 to 4 built: the block, its three
rows, the guided steps to mod.io and publishing without the export folder;
then *Add to loadout…*, a mod added to or taken out of a loadout that is not
open, with the live one's one switch and a patch's mods; then *Update*,
which says when the other loadouts' build is behind and builds it again,
and *Play in Main*, on a menu beside Play rather than in its question; then
*What I play now…* in *Add to loadout…*, for somebody with no loadout of
the mods they play. What each left for later is said in section 10. Phase
5 to come. The
projects and loadouts proposal is built but for its phase 10, *Add to loadout…*, which is
this proposal's section 5. Its section 13 set out what that row becomes;
this version works it out against the code as it now stands. The first
version's "main loadout", worked out from a hidden flag and from which file
was live last, is gone, and so are three of its seven questions (section
11). This version's five were answered the same day, one of them with a
test in the game (section 11), and none is open.

## 1. The problem

The tab is mostly about a mod of yours: one written here, or one of yours
read back in to carry on with. For it, each of the three ways out has a gap.

**Into the game you play.** Saving changes nothing in the game. Export
refuses a mods folder on purpose (`OutputPanel.choose_destination`,
`set_default_output_dir`): writing there is Play's job, because Play builds
into the library and keeps the switches in step, and a folder dropped beside
that is a copy nothing keeps in step. But Play makes *this loadout* what the
game loads, and a loadout made by *Write a mod of your own* holds that mod
and nothing else. That is right for testing, and the
[playing proposal](playing-proposal.md) chose it deliberately ("every project
is complete when played"). It is wrong for "I made a mod, now I want it in
my game". Play's question (`Prompts.confirm_live`) offers *Play* or
*Cancel*, and *Play* switches off everything else.

The way to it today is to press Play on the mod, open the loadout you play,
*Add mods…* from the library, pick the mod and press Play again. Nothing on
the Export tab mentions it, and the tab's own help (`EXPORT_HELP`) points
only at Play. The second Start card, *Change a mod you downloaded*, still
tells you to "export to your mods folder", which Export refuses.

**Onto mod.io.** Once a mod is linked to a page on mod.io, *Publish
update…* works well. But it is halfway down the left column, under the
details, in a group box that looks like more details. Before the link, the
box says "Not linked to a mod on mod.io yet". *Link to mod.io…* then lists
your mods on mod.io, which for a mod just written here is a list without it.
The only hint about what to do is in the Link dialog's ? tip: "A mod that
isn't on mod.io yet has to be created on the website first." No button takes
you there. The [mod.io proposal](modio-integration-proposal.md) planned one
(*Open mod.io to create it*), but it was not built.

**As files.** *Export this mod* writes a folder to the destination shown
above it. *Export as a zip* writes a zip. *XML* copies or saves the ModOps.
*Export this mod* is the tab's highlighted button. Once somebody is used to
the Studio, it is the least common of the three: most of the time they want
the mod in their own game or on mod.io, and the Studio can do both of those
itself.

## 2. What changed underneath since the first version

The first version's section 4 rested on one file per loadout holding its own
copy of each mod, so adding a mod to a loadout that was not open meant
reading the mod into that file, and a mod in two files could be written over
by either. The projects and loadouts work changed what this proposal stands
on:

- **A mod of yours is kept in a project, and a loadout refers to it**
  (`core/projects.py`, the loadout file's `"projects"`). Adding the mod to
  another loadout is writing a reference into that loadout's file. Nothing
  is copied, and nothing has to be read at the time: a loadout reads a
  project whose revision it has not read when it is opened and before Play,
  as it does a project another loadout changed (`readback`,
  `stale.follow_projects`). The first version's question of whether to read
  the mod in without showing the loadout, or to switch to it, is gone.
- **The cache knows which loadouts have a mod**
  (`filecache.loadouts_with_project`), from the files on disk, whichever
  loadout is open. The status can name them, and the list can tick them.
- **A mod's builds are its project's, and each library folder has one
  writer** (`core/builtby.py`): the project that built it. Every loadout
  that has the mod links that one folder, so building the mod again updates
  it in every one of them. A build into a folder another project built is
  stopped, and names that project.
- **The release facts are the released mod's**: its mod.io link and what
  was published are kept in its project, so the *On mod.io* row says the
  same in every loadout that has the mod. A copy or a fork of a loadout makes
  its own mod a new mod, with none of the first one's link or releases
  (`Loadout.own_mod_anew`).
- **The loadout's own mod is a record like any loaded mod's**
  ([own mod as a layer](own-mod-layer-proposal.md)), and its project names
  its details as a layer's project does. So the project of the mod *Write a
  mod of your own* makes, the own mod of that loadout, can be a loaded mod
  of yours in another loadout, read through the same project.
- **The Start card taken is a recollection, not a mode** (`Loadout.aim`).
  What decided the rows by "aim" in the first version is decided here by
  what the mod picked is: the mod the loadout is (`is_the_mod`), a mod of
  yours it carries on with (`home`), a patch, or the loadout's own mod on a
  loadout made to play (its record's `loads_last`). Section 8 has the table.
- **A mod renamed moves its library folder** with its link and record, and a
  loadout that has it takes the new name when next opened, matched by ModID
  (section 7 of that proposal). The first version's question about a renamed
  mod is answered by that.

## 3. What is proposed

- One block under the preview, in place of the *Export to:* row and the
  button row: **In your game**, **On mod.io**, **As files**. Each row has a
  one-line status and one button.
- **In your game** is *Add to loadout…*: your loadouts for this game, each
  with a tick box, ticked where it has the mod. Ticking one adds the mod to
  it, without switching anything else off; unticking takes it out. Once the
  mod is in one, *Update* builds it again for all of them (section 5).
- **On mod.io** has one button whatever the state, and walks through what is
  missing: signing in, linking, creating the mod on the website (section 6).
- **As files** is a menu: folder, zip, XML (section 7).
- Play's question gets a third answer for a mod that other loadouts have:
  *Play in Main* (5.6).
- The mod.io group box leaves the left column.

## 4. The block

```
 Mod:  [ Pax Romana Tweaks                         ▾ ]  ?
 Preview of your mod: data/base/config/export/assets.xml        ?  Refresh
 ┌──────────────────────────────────────────────────────────────────────┐
 │ <ModOps> …                                                           │
 └──────────────────────────────────────────────────────────────────────┘
 In your game   In Main and Test.                        [ Add to loadout… ]
 On mod.io      Not on mod.io yet.                       [ Publish to mod.io… ]
 As files       Exports go to Documents\AnnoModding\…    [ Export ▾ ]
```

The status is the point, as on the Start tab's step cards (`StepCard`):
somebody coming back to the tab wants to know whether the mod is in their
game, whether mod.io has the latest, and where the last export went, without
reading three explanations.

Each row's status reads like this:

| Row | States |
|---|---|
| In your game | *In no loadout but this one.* · *In Main and Test.* · *In Main and Test, built before your last changes.* · *In Main, which is what the game loads.* · a reason it cannot be done: no game files, no mods folder, the mod's folder built by another project (`builtby`) |
| On mod.io | *Not signed in.* · *Not on mod.io yet.* · *Linked to “Name”. 1.2.0 is live.* · *Linked to “Name”. 1.3.0 is live: published from here on 12 Oct.* · the sign-in running out, as `_refresh_modio` says it now |
| As files | *Exports go to …* with *Change…* · *Last exported to … on 12 Oct.* |

The loadouts are named by their files' names, as the window's title names
the open one. The open loadout is never in the list: the mod is in it
already, which is why the tab is showing it.

**One highlighted button, by the mod.** For a mod of yours or a patch, the
*In your game* button: it is the step taken most often. For a loadout's own
mod on a loadout made to play, none: Play in the tab bar is that loadout's
main action already.

The mod box's caption, *Mod to export:*, becomes *Mod:*, because it now
decides what all three rows are about.

## 5. In your game

### 5.1 Which mods can be added

- **A mod of yours**: the mod a loadout *is* (`is_the_mod`, what *Write a mod
  of your own* makes), a mod of yours a loadout carries on with (`home`), a
  fork, and any other loaded mod a project keeps (`ModLayer.kept`).
- **A patch of yours**, with the mods it needs (5.5).
- **Not a loadout's own mod anywhere else**: the patch over a loadout's
  whole list, or the own mod beside a mod of yours it carries on with. It is
  that loadout's, made for its list, and used only there.

That last rule is section 13 of the projects and loadouts proposal, which
says "a loadout's own mod is not offered". Read as written, it would leave
out the mod *Write a mod of your own* makes, which is that loadout's own mod
too; but there the loadout is the mod, and adding it elsewhere is the point.
So the rule here is by `is_the_mod`, settled on 24 October (section 11).

A mod with no project yet (a loadout never saved) is built and referred to
by the project its next save makes (`builtby.project_of`, `reserve`), so
*Add to loadout…* saves first, saying so.

### 5.2 *Add to loadout…*

A dialog, not a menu: the list can be long, and each line says something.

```
 Add Pax Romana Tweaks to:
 [x] Main                    what the game loads now
 [ ] Test
 [ ] Mod list 2
 ? Each loadout reads it the next time it is opened. The one the game
   loads now gets it at once, and nothing else in it is switched.
                                                  [ OK ]  [ Cancel ]
```

- The loadouts are this game's, from the cache, less the open one, ticked
  where the loadout has the mod's project already.
- The live loadout (`library.live`) says so.
- OK adds the mod to each loadout newly ticked (5.3), and takes it out of
  each one unticked (5.4), all in one save through the journal
  (`core/saving.py`).

### 5.3 Adding a mod to a loadout

1. **Build the mod** into its library folder, as Play builds it: from this
   loadout, which has it read. That is its project's folder, and a folder
   another project built stops the whole add, as Play's does.
2. **Refer to it** in the loadout's file: a layer for its library folder,
   marked as kept by a project, switched on in the loadout's chosen profile,
   where the game sorts it among the rest, and listed off in its other
   profiles; and a reference to its project,
   at a revision the loadout has not read. The file is read and written
   whole (`Loadout.load`, `Loadout.save`) without being shown; nothing in it
   is read again from the game files now.
3. **The loadout reads it** the next time it is opened, and before its next
   Play: its ModOps where the mod stands in that loadout's load order, as a
   project another loadout changed is read (`readback`). Until then the
   file has the mod and not its reading, which nothing but that loadout's
   own screen asks for.
4. **If the loadout is live,** the mod is switched on in the game at once:
   linked into the mods folder and, in Anno 117, its line written into the
   Studio's profile, and nothing else moves. That is Play's question with
   one line (`confirm_live`). It is only done where the mods folder is as
   that loadout's Play left it (`truth.fingerprint`); where it is not, the
   question says to open that loadout and press Play, and the add is made
   without it.

Step 4 does not make the loadout live by its plan (`play.plan`): a plan is
worked out from the loadout's readings, which an unopened loadout's file
does not have, and making a loadout live from its file alone would leave
its own edits unbuilt. Switching one mod on beside what that loadout's last
Play left is all that is needed, and all that is done.

### 5.4 Taking a mod out of a loadout

Unticking takes the mod's layer and its project's reference out of that
loadout's file, and, where the loadout is live, switches the mod off in the
game. The project is not touched, nor any other loadout. The question names
what goes.

### 5.5 A patch

A patch is inert where the mods it needs are not all on (`patching.is_inert`):
it loads after them, and without them does nothing. The dialog says, beside
each loadout ticked, which of those mods it lacks, and ticking it adds them
as well: they are in the library already, since the patch was made for them
there. One the loadout has switched off is said too, and switched on with
the patch.

### 5.6 After that: *Update*, and Play's question

Once the mod is in another loadout, the status says whether that build is
the mod as it is now (`builds.stale`), and the button gains *Update*
beside *Add to loadout…*: it builds the mod again into its library folder.
Every loadout that has the mod links that folder, so where one is live the
game has the new build at once, and each reads the project again when it is
next opened. No switch moves. That keeps edit, update, play cheap.

Play's question (`confirm_live`) gets a third answer, for a mod of yours or
a patch that other loadouts have:

- **Play on its own**: today's Play, renamed to say what it does. The text
  adds: "Main is kept as it is. Playing Main puts it back."
- **Play in Main**: builds the mod (*Update*), then saves this loadout,
  opens Main and plays it. Main is the live loadout where it has the mod,
  otherwise the loadout that has it; with several, the button is a menu of
  them. This switches the window to Main, which is where somebody going to
  play it wants to be, and lets Main's own Play build its own edits (5.3).

Where no other loadout has the mod, the question stays as it is.

### 5.7 No loadout but this one

A new user who has never played a set of mods has no loadout but the ones
*Write a mod of your own* made, each holding its one mod. The dialog then
offers *A loadout of what you play now, with this mod in it*: **What I play
now…** followed by 5.3. That runs the library conversion, which moves every
installed mod into the library and asks its own questions, and the dialog
says so before it starts.

That is a lot to take on just to try your own mod. The way in that needs no
loadout is **As files**: the zip, installed by hand or by another mod
manager. The Studio writes only a profile it manages, so as not to get in
the way of other tools, and does not link a mod into the mods folder outside
a loadout either.

*Play on its own* is not a way in beside what somebody plays. In Anno 117 it
writes the Studio's profile with `EnableNewMods` commented out and makes it
the live one, and in both games it switches off every mod the loadout does
not list (`play.plan`'s `strays`). What somebody played before stops loading
until they switch back. That is right for testing, which is why it is
renamed (5.6).

## 6. On mod.io

One button, *Publish to mod.io…*, that does whatever comes next. It is shown
only for the mod the loadout releases (`_publishable`, as now): the mod of
yours it carries on with, or else its own mod. A patch of yours, or the own
mod beside a mod you carry on with, is not published from here.

1. **Not signed in.** Opens the sign-in (File ▸ Settings…), then carries
   on.
2. **Signed in, not linked.** Lists your mods on mod.io (`Client.my_mods`).
   - One whose name or name ID matches the mod: *This looks like
     “Name” on mod.io. Link it?*
   - Otherwise the list, as `LinkDialog` shows it now, with a second way
     out: **It's new: create it on mod.io**. That opens the game's *Add mod*
     page in the browser and copies the name and description for pasting.
     For Anno 117 it says first that mod.io asks for a linked Ubisoft account
     (the mod.io proposal, *The Ubisoft link is real, and only for 117*). The
     dialog stays open with *I've created it*, which lists your mods again and
     links the new one.
3. **Linked.** The button reads *Publish 1.3.0…*, and `publish_update` runs
   as now.

The link and what was published are the mod's project's, so linking the
mod in one loadout links it in every loadout that has it, and "published
from here" means from any of them. `_publishable`'s docstring still calls
the link the loadout's, and is corrected with this.

**Publishing stops writing an export folder.** `publish_update` calls
`export_mod`, so every publish also writes the folder at the export
destination and asks before replacing it. With exporting no longer the main
action, publishing builds into the scratch folder it zips from, and nothing
else.

**Creating the mod from inside the Studio comes later.** The API can do it
(`POST /games/{g}/mods`, with `name`, `summary` and a `logo` of 16:9, at least
512 by 288). That is a logo picker and a summary box. The mod.io proposal
left it for later because "they are not the part that hurts". The website
step above is the part that hurts now, and it fixes that without the open
question the mod.io proposal left: whether the API turns away an account
with no Ubisoft link for 117 the way the website does.

## 7. As files

*Export ▾* is a menu:

- **Folder…** — today's *Export this mod*, to the destination.
- **Zip…** — today's *Export as a zip*.
- **Copy XML** and **Save assets.xml…** — today's *XML* menu.

The destination and *Change…* move into the row's status. Nothing else about
exporting changes: overwrite questions, backups, the "left out" notes, and
an export into a mods folder stopped where another project built that
folder (`builtby`).

## 8. Which mod the tab is about

| The mod picked | In your game | On mod.io | As files |
|---|---|---|---|
| **The mod the loadout is**, or **a mod of yours it carries on with** | *Add to loadout…* / *Update*, highlighted | shown | menu |
| **A patch of yours** | *Add to loadout…*, with the mods it needs (5.5), highlighted | hidden: not what this loadout releases | menu |
| **The loadout's own mod, on a loadout made to play** | *This is what the game loads* or *Make it live* (`sync_loadout`), not highlighted | hidden | menu, with *All the mods you changed* |
| **The loadout's own mod beside a mod you carry on with** | *Only in this loadout*: not offered (5.1) | hidden | menu |

Cases to keep working:

- **Other mods loaded to check compatibility** beside the mod a loadout is.
  *Add to loadout…* takes only your mod, not them. *Play on its own* plays
  them all, as now.
- **Several mods in the mod box.** The rows are about the mod picked. *All
  the mods you changed* hides *On mod.io*, and *In your game* adds each of
  them that can be added.
- **A held project** (missing, unreadable, or changed in another loadout and
  not read again yet: `Loadout.held`). Nothing is built from a held project,
  so *In your game* and *On mod.io* say why and wait, as its details do.
- **No game files, no mods folder, the game running.** The row is disabled
  and says why. The refusal reads as Play's does.

## 9. What moves elsewhere

- The mod.io group box on the left (`modio_box`) goes. Its text becomes the
  *On mod.io* row's status, with the sign-in notes in its tooltip.
- `EXPORT_HELP` is written again for the block: the three ways out, and Play
  as testing on its own.
- `choose_destination`'s message for a mods folder points to *Add to
  loadout…*, not only to Play.
- The second Start card's "export to your mods folder", which Export
  refuses, becomes "add it to what you play".
- The README's Export tab screenshot (`docs/images/export_granite.png`) and
  its caption follow the block.

## 10. Phases

1. **The block, and the rows that need nothing new.** The three rows, the
   *Export ▾* menu, the mod box caption, the mod.io box gone. *On mod.io* with
   the guided steps of section 6 and publishing without the export folder.
   *In your game* shows its status from the cache, and its button opens the
   loadout picked with a note saying what to do.

   *As built:*
   - **In your game.** *Add to loadout* is a menu of this game's other
     loadouts from the cache (`filecache.loadouts`), ticked where they have
     the mod's project, the live one named. Picking one opens it and says
     how to add the mod there: *Add mods…* in the Mod changes panel, then
     the mod from the library. A mod with no copy its project built in the
     library is played here once first, and the menu says so instead of
     opening anything. The status names the loadouts that have the mod and
     which of them the game loads. Of the reasons it cannot be done, it says
     two: a held project, and the mod's library folder built by another
     project (`builtby.theirs`). No game files, no mods folder and the game
     running wait for phase 2, which is the first to write anything.
   - **Built before your last changes** is left to phase 3, with *Update*.
     `builds.stale`, which 5.6 names, is not that: it says a build was made
     on other game data than now. What says a build is behind the mod's
     changes is phase 3's to work out, and it did (below).
   - **The loadout's own mod on a loadout made to play:** *This loadout is
     what the game loads* or *This loadout is not what the game loads now*,
     with *Make it live…* (`sync_loadout`) for the second, not highlighted.
     Beside a mod carried on, and for somebody else's mod: *Only in this
     loadout*, with no button. With every mod you changed picked: *Pick one
     mod to see where it is*; adding each of them is phase 2's.
   - **On mod.io.** One button, *Publish to mod.io…*, and *Publish 1.3.0…*
     once linked and signed in. Not signed in, it opens Settings on the
     mod.io tab and carries on once you are. Not linked, it lists your mods,
     offering first the one whose name is the mod's, or whose name ID is made
     of its name or its ModID (`publish.name_id_of`); then it publishes. The
     link dialog has *It's new: create it on mod.io*, which opens
     `https://mod.io/g/<game>/m/add` (the website's own route for it, found
     in its scripts) and copies the name and description, and *I've created
     it*, which lists your mods again and picks the one new since. For Anno
     117 it says the Ubisoft account first. The status says *Linked to mod.io
     mod 9*, not the page's name and live version: the tab is drawn far more
     often than mod.io should be asked, and the link keeps no name.
   - **Publishing** builds the mod into a scratch folder and zips that:
     numbered as a build, as an export's was, and with no *Exported*
     checkpoint, since nothing was exported.
   - **As files.** The status is the destination, with *Change…*; *Last
     exported to … on 12 Oct.* is not built. The menu reads *Folder*, *Zip*,
     *Copy XML*, *Save assets.xml…*, and *Folders* with every mod you changed
     picked: without an ellipsis on the first two, which ask nothing first.
   - **Section 9:** the mod.io box, `EXPORT_HELP`, `choose_destination`'s
     message, the second Start card and the README's text are done. The
     README's screenshot is not taken again yet.
2. ***Add to loadout…*** (5.2 to 5.5): the projects and loadouts proposal's
   phase 10. A loadout written to without being opened, the reference read
   when it is, the live one's one switch, taking a mod out, a patch's mods.

   *As built* (`core/adding.py`, `ui/add_to_loadout_dialog.py`):
   - **The reference read when the loadout opens,** proved first
     (`tests/test_adding.py`): the mod goes in as joining a project puts it
     in (`readback.take_in`, and `join_patch` for a patch), held as a moved
     project is, and saved at a revision no project has
     (`adding.NEVER_READ`, -1). Opened, the loadout reads it where it
     stands, and the mods after it over it, as it reads a project another
     loadout changed. Its save writes nothing over the project.
   - **Where it goes:** a layer named after the mod's library folder
     (`generator.folder_for`), its folder that loadout's mods folder (its
     own where it has one), kept by its project, and placed where the game
     sorts it (`set_layer`).
   - **A loadout that has the mod from its folder,** kept by no project --
     one that had it before a loadout carried on with it and made the
     project -- takes the project up when ticked, as making the mod a
     project there would (`adding.from_folder`, `readback.join`): found by
     the ModID the mod builds with (`adding.built_id`), and read from the
     project when next opened. The dialog says *has it from its folder*
     beside it, and the row's status names it. One with edits of its own to
     the mod is refused, and the mod made yours there instead.
   - **A mod carried on and given a new ModID is another mod** to the game
     (`generator.given_a_new_mod_id`: it builds with another ModID than its
     folder declared), and is not taken up for the old one. It goes into a
     folder of its own, `[Category] Name`, beside the old one, which stays
     the old mod's (settled with the maintainer, 24 October). Before Play
     and before *Add to loadout…* build anything, the mod is moved to that
     name throughout its loadout (`builtby.own_folders`, over
     `builtby.rename`), and takes the place in load order the new name
     gives it. Only its name moves: it is still read from the folder it
     was carried on from, which holds what it ships besides its patch.
     Pointed at its new folder, empty until built, its first build carried
     none of that over (found the same day: a readme left behind). Play
     then builds and links the new folder, and switches the
     old one off, as any mod the loadout does not have; it builds such a mod
     even with no edit of yours in it, since it is in no mods folder until
     built (`build.mine`). An export goes under the new name too. Added to a
     loadout that has the old mod, it goes in beside it, and taking the old
     one out of that loadout is the user's. A loadout with something else in
     the folder it builds into is still refused, naming both ModIDs.
     Found in the hands-on run of 24 October, where ticking such a loadout
     was refused, after the mod had been built: every loadout is now loaded
     and changed in hand before anything is built or written, so a refusal
     leaves everything as it was.
   - **One save:** every loadout ticked and unticked is written in one
     journal (`adding.save_all`, over `Loadout.to_save`, which `save` now
     uses). This loadout is saved first, and the mod built into the
     library without a link (`build.write(..., link=False)`, which refuses
     a folder of the user's own at the mod's name in the mods folder).
   - **A patch's mods** are copied from this loadout's layers, marked to be
     read from their folders when the loadout opens (`stale.UPDATED` on
     themselves), and one switched off is switched on. The dialog says
     which each loadout lacks from the cache's names
     (`filecache.loadouts_naming`); one it has switched off is said after,
     not in the list, since the cache keeps no switches.
   - **The live one** (`adding.switch_live`): asked as Play's question with
     one line, and done only where the folder matches the live record's
     fingerprint and the game is not running. The link is put back
     (`library.relink`) or made, or taken away; for Anno 117 the line is
     written into the Studio's profile, refused where the live profile is
     the user's own. The live record then takes the folder as it is.
   - **Not taken out:** the mod a loadout is, or carries on with.
   - Phase 1's menu, which opened a loadout with a note, is gone with it.
3. ***Update* and Play's question** (5.6).

   *As built:*
   - **Built before your last changes.** The library's record of a copy it
     built now keeps what the build wrote, as the build number's digest
     does (`builds.content_digest`: the ModOps and modinfo without their
     comments, and the files the mod carries). It is kept for every build,
     numbered or not, since a fork or a kept mod has no number
     (`library.DIGEST`, `record_built`, `built_digest`). The copy is behind
     where writing the mod now would write another digest (`build.behind`).
     A copy built before the record kept one cannot be told, and is said
     neither way. Each file the mod carries is hashed once per size and
     time written, as the tab asks each time it is drawn.
   - **The row.** Where other loadouts have the mod and it is behind:
     *In Main. The build they have is from before your last changes.* The
     *Update* button stands beside *Add to loadout…* once another loadout
     has the mod. It is highlighted in that button's place while behind,
     and shut while not.
   - ***Update*** saves this loadout first, as *Add to loadout…* does, moves
     a mod given a new ModID to its own folder, and builds the mod into its
     library folder without a link (`OutputPanel.update_build`). Every
     loadout that has it links that folder, so the game has the new build
     at once where one of them is live, and the status line says so.
     Nothing is switched, so the live record's fingerprint, which covers
     switches only, still matches. While the game runs and a live loadout
     has the mod, it is refused, as Play is.
   - **Where Play plays is a menu on Play, not a third answer.** Built
     first as section 5.6 has it, the answer made Play's question longer
     still, and asked again at every Play. The maintainer's verdict, on
     trying it on 24 October, was a menu on the button with a short ?
     beside it instead.
     - For a loadout that is a mod (`Loadout.stands_for`) that other
       loadouts have, by its project, a ▾ beside Play in the tab bar opens
       a menu: *On its own*, today's Play, and *In Main* for each such
       loadout, the one the game loads first and named so.
     - The choice is kept for the loadout in the settings, and makes the
       button *Play in Main*, its name cut short to fit the tab bar. F5
       and *Game ▸ Play* follow it.
     - *Play in Main* runs *Update*, opens Main and presses its Play,
       which asks Main's own question (`LiveController.play_in`). Play's
       question itself is as it was.
     - A loadout picked that no longer has the mod is not played in: Play
       asks the files cache afresh, and plays this loadout.
     - Not offered on a loadout made to play: a patch of yours there is
       played with that loadout's list, which is what Play already does.
4. **No loadout but this one** (5.7): *What I play now…*.

   *As built:*
   - **When it is offered:** where none of this game's other loadouts was
     set up as a set of mods to play (`Loadout.aim`, *Play a set of mods*).
     The files cache says so, keeping each loadout's aim among its
     mentions as `aim:manage`, which no mod's name can be
     (`filecache.AIM_OF`, `loadouts_to_play`; the cache's schema 4, so an
     older cache is built again). A loadout of a mod of yours, or a patch's
     workspace, is not one. The dialog then says so under the list, and
     has *What I play now…* beside OK; the loadouts it lists can still be
     ticked.
   - **What it does** (`OutputPanel.add_to_what_i_play`,
     `StartController.play_now_with`): this loadout is saved, as for any
     add, then the Start tab's *What I play now…* runs as it does from the
     card, with its own questions: what to do with the mods loaded, and the
     offer to move the mods into the library. The loadout it makes is
     saved, the mod is added to it by both files (`adding.add_by_files`:
     built into the library without a link, and both written in one save,
     the source for its build number), and it is opened again to read the
     mod. The status line says to press Play.
   - **Not switched in the game at once,** as 5.3's step 4 does for a
     live loadout: the loadout has only just been made, and Play, which
     the status line points at, asks its own question and builds the rest.
   - A question turned down along the way leaves the mod's loadout open
     and nothing added.
5. **Later: creating the mod on mod.io from the Studio** (section 6).

Phase 2 is the one with something new to prove: that a loadout opened with a reference it never read reads it as it
reads a project changed elsewhere. That is the first thing it tests.

## 11. Settled

The first version's questions, settled by the projects and loadouts work:

| The first version's question | Settled by |
|---|---|
| Adding to a loadout that is not open: read the mod in without showing it, or switch to it? | Neither: the loadout refers to the mod's project and reads it when opened (section 2, 5.3) |
| Which loadout once the mod has been played on its own | The ones the user ticked; the cache keeps them, and no "main loadout" is worked out (5.2) |
| A renamed mod: does the loadout's entry follow it? | Yes: renaming moves the library folder, and a loadout takes the new name when next opened, matched by ModID (section 2) |

This version's, answered on 24 October:

| The question | Answer |
|---|---|
| A loadout's own mod: offered where the loadout is the mod (`is_the_mod`), against the letter of section 13 of the projects and loadouts proposal? | Yes, that is the rule (5.1). That section now says so |
| No loadout but this one: offer *A loadout of what you play now* with its conversion? | Yes (5.7). Without a loadout, the zip is the way in, by hand or by another mod manager. The Studio writes no profile it does not manage |
| Profiles: on in the chosen profile only, or in all of a loadout's? | The chosen one only, listed off in the rest (5.3) |
| The highlighted button: *In your game*, or *Publish* once linked? | *In your game*, as proposed (section 4) |
| What Anno 117's loader does with a mod in the folder that the live profile does not list | Tested in the game: it writes the mod into the live profile by that profile's `EnableNewMods` line. On, as Ubisoft ships it: the mod is written in on, and loads. Commented out: it is written in commented out, and does not load. The Studio's reading of a profile already takes an unlisted mod by that line (`tests/test_switches.py`); writing it in is the loader's |

## 12. Open questions

None. A way in that only links the mod into the mods folder, writes no
profile and says first whether the live profile will load it was weighed
after the test above, and left out: the conversion and the zip are enough
(5.7).

## 13. What this leaves alone

- Play, and what *Play on its own* does.
- The library, the conversion, and the rule that the Studio owns only the
  links it made and the copies it holds.
- Projects and loadouts: what each keeps, how they are saved, read again and
  restored. *Add to loadout…* writes a loadout's file as a save does, and
  the loadout reads the reference as it reads any project.
- How a mod is built, written, zipped or checked; the validation column; the
  loader check.
- Publishing an update once linked: its checks, its question, what it sends.
- The details column, apart from the mod.io box leaving it.
