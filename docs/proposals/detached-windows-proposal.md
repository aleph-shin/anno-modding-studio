# Panels in windows of their own: a proposal

The Mod changes panel and three of the main tabs, Asset editor, Mod file
and References, can each be taken out of the main window into a window of
its own, so that somebody with a second monitor can keep any of them in view
there while the rest has the whole of the first.

The [file tab proposal](file-tab-proposal.md) left this out as "a proposal
of its own" (its section 3). This is it.

Written on 11 October 2026, against `main` at `a071f44`, for the Mod changes
panel and the References tab. Revised on 12 October against `569d68e`, with
the file tab built: the Mod file tab and the Asset editor are added, the
rules are written for any panel and not for each, and what the panel's fold
means for a panel that is out is decided (section 9).

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (13 October 2026).** Built; all four phases. Phase 4, the Asset editor: its tab keeps the page, everything that ends in the editor brings its window forward, Undo shows the asset of the step undone, and the Edit menu is filled from the editor where it is. The graph's rules hold with the editor out and the graph in, and go by whether the two are in different windows; nothing is kept for an editor that is out to open on arrival, since it is never arrived at. Still wanted: the look on two real monitors, with one unplugged between launches (section 10). Phases 1 to 3 (phase 3: the Mod file tab, kept current beside the editor with no wait, since the bench put drawing the longest file at 193 ms against an edit of about 3.7 s; Ctrl+F by where the keyboard is; and an Apply in the file redrawing the editor's asset, which it had not). In full, phases 1 and 2: the window, with the Mod changes panel in it, the View entry, the button and Shift+F9, the two rules of section 5, the fold, and what is remembered; and the References tab, with the page its tab keeps, the graph and the editor following each other side by side, the pin, and the graph's own history. Where it differs from what is written here: the menu is *View › Separate window*; the button is in the panel's own top row, and not beside the Mod changes title, which stays in the main window; the shortcuts are answered by an ordinary window that holds the main window's actions, a tool window having covered the taskbar when maximised (open question 1); a panel whose screen has gone stays remembered as out until it is moved by hand; the pin is offered while the graph is in another window than the editor's, and not only while both show, so that looking at another tab does not take it away; and the graph has a history of its own while it is out (open question 2, as suggested).

## 1. What has changed since it was written

| It said | Now |
|---|---|
| Two panels: Mod changes and References | Four: those, the Mod file tab and the Asset editor |
| The file tab's collapse is planned, and is to be decided with this once the file tab is built | Built. The panel folds to a strip at the window's edge (*View › Mod changes panel*, F9), in place of the entry that hid it. Decided in section 9 |
| The graph follows the editor "while the graph is in its own window" | Each of the two follows the other while both are showing, whichever of them is out |
| Two phases | Four |

## 2. The problem

The main window is one splitter: the tabs on the left and the Mod changes
panel on the right (`MainWindow._build_ui`), set 2:1 with a floor of 340
pixels under the panel. Everything the Studio shows is inside it.

- **Things that are about one another are never seen together.** The graph
  and the editor are about one asset, and each follows the other. The Mod
  file tab holds the file the editor's edits land in, and *Show in file*
  goes from one to the other. Each is a tab beside the others, so only one
  is ever in view.
- **Mod changes costs the tabs a third of the width.** The XML view and the
  Mod file tab feel it most: long lines, and the file tab's outline beside
  them. The panel folds to a strip, which gives the width back and takes
  the list away.
- **A second monitor is no help.** The window can be stretched over two
  screens, and the splitter then puts its divider wherever the proportions
  land, which is not where the screens meet.

## 3. What is proposed

1. **Four panels can each be moved to a window of its own:** Mod changes,
   Asset editor, Mod file and References. Start and Export cannot: one is
   where a session begins and the other is a step to finish on, and neither
   is something to keep beside the work.
2. **In or out is the one distinction, and the same for all four.** A tab
   that is out stays in the strip, with a page saying where it is and a
   button to bring it back. Mod changes out gives the tabs the splitter's
   whole width.
3. **A panel is kept up to date while it is showing, and catches up when it
   is next shown.** In a tab that is what happens today. In a window of its
   own a panel is always showing.
4. **Going to a panel brings it to the front, wherever it is.**
5. **The graph and the editor each follow the other while both are
   showing**: the graph as the editor moves, the editor only when asked to.
   A pin holds the graph where it is.
6. **The windows belong to the main window.** They stay above it, are
   minimised and restored with it, have no entry of their own in the
   taskbar, and take the main window's shortcuts.
7. **Which panels are out, and where, is remembered**, and a window whose
   screen is gone at the next launch comes back in the main window.

Not proposed: dragging a tab or a panel out by its title, a window for
Start or Export, a second view of anything (the XML view beside Fields),
and any change to what a panel shows.

## 4. How a panel is moved

**The same widget, in another window.** The panel is taken out of the main
window's layout and put in a plain top-level window, and put back when that
window is closed. It is the one `ChangesPanel`, `EditorPanel`, `FilePanel`
and `ReferencePanel` throughout, so everything the main window holds on to
(`self.editor`, `self.changes_panel` and the rest, and their signals) goes
on working, and there is no second copy to keep in step.

Not a `QDockWidget`. The Mod changes panel was one, and was taken out
because a dock keeps the width it was given when the window is resized; and
a dock has nothing to say about a tab. Not a second instance of a panel
either: the change list holds a good deal of state worked out once a
rebuild, the editor and the file tab each hold text that has been typed and
not applied, and two of any of them would be two things to keep in step.

**One small class for all four.** A window that holds a panel, knows where
the panel goes back to, saves and restores its own geometry under a key,
and puts the panel back on close.

**Getting it out and back.**

- *View › In a window of its own ›* with an entry for each of the four,
  each checkable and each with a shortcut. The shortcuts dialog finds them
  as it finds the rest (`ui/shortcuts_dialog.py`).
- A button on the panel itself: beside the "Mod changes" title, and in the
  top row of each of the three tabs. In a window of its own the same button
  puts the panel back.
- Closing the window puts the panel back. It does not hide it: a panel that
  is neither in the main window nor on screen has no way back that anybody
  would find.

**The page a tab leaves behind.** One line and one button: the panel is in
a window of its own, and *Bring it back*. The tab is not removed, for two
reasons: the tabs are known by position throughout the window (`TAB_EDIT`,
`TAB_FILE`, `TAB_REFS`, `TAB_GENERATE`), and a tab that vanished would take
the obvious way back with it. A tab's gate still holds: a tab that cannot
be used cannot be taken out, and a panel that is out when its gate shuts
shows what its tab would.

**Belonging to the main window.** The window is created with the main
window as its parent, which on Windows makes it an owned window: above its
owner, minimised with it, absent from the taskbar and from Alt+Tab. On a
second monitor that is what is wanted. On one monitor it means the panel's
window always covers part of the main window, and the answer there is to
put it back.

**Closing the Studio** closes them, after the question about unsaved work
and not before: a refusal leaves every window where it was.

## 5. Two rules in place of the tab's position

The main window asks "is this the tab in front?" to decide whether a panel
is worth redrawing, and changes tab to put a panel in front of somebody.
Both stop being true of a panel that is out. Each becomes one rule, held in
one place, and nothing below is decided per panel.

**Showing.** A panel is showing when it is the tab in front, or is in a
window of its own. The tests of the tab's position that mean this become
that question: the graph following the editor
(`MainWindow._on_editor_opened`), Undo moving the editor to the asset of the
step undone (`undo_last`), and *Find in asset* choosing the file tab's find
bar over the editor's (`_find_in_asset`). `FilePanel.mark_stale` asks
it already, of itself: it draws the file now unless the panel is hidden,
and on being shown otherwise.

**Bringing to the front.** `MainWindow.open_tab` is the one way anything
but a click on the tab strip changes tab. For a panel that is out it
raises that panel's window and gives it the keyboard, and leaves the main
window's tab alone. So *References for this asset…*, *Show in file*, a
double-click on a change, Ctrl+P and the rest go where the panel is with no
change of their own. A click on the tab of a panel that is out shows the
page of section 4 and brings the panel's window forward.

## 6. Shortcuts

Every menu action is the main window's, and a shortcut is answered by the
window that has the keyboard. Left alone, Ctrl+Z after unticking a change in
the detached list would do nothing, and so would Ctrl+S, Ctrl+P, F5 and the
rest.

The detached windows answer the main window's shortcuts. There are two ways,
and phase 1 settles which by trying them:

- **A tool window.** Qt hands a tool window's keys to its parent's shortcuts.
  A tool window on Windows has a narrow title bar and no maximise button
  unless asked for one, and maximising on the second monitor is the common
  case. Whether the hint for the button and the passing-on both hold needs
  checking in the real window.
- **An ordinary window with the main window's actions added to it.** A
  shortcut then works in whichever window is in front. Certain to work, and
  one list to keep.

Shortcuts that belong to a panel go with it and need nothing: Alt+← and
Alt+→ in the graph and the editor, and Apply and Find in the XML view and
the file tab, are each scoped to their panel.

**The menus stay in the main window.** A window of a panel's own has no
menu bar. The Edit menu is filled from the row selected in the asset editor
each time it opens (`_populate_edit_menu`), and goes on being, wherever the
editor is.

**Ctrl+F** finds in whichever of the editor and the file tab has the
keyboard, and in the editor where neither has.

## 7. The four panels

### Mod changes

The panel is as it is today, with the window's title in place of the
"Mod changes" label above it.

- **The splitter gives the width back.** The panel's side of it goes,
  strip and all, and the width it had is kept for when the panel returns.
- **Going to a change.** A double-click on a row opens the asset at the
  field, in the editor, wherever the editor is.
- **Dialogs opened from the panel** (*Show XML…*, the right-click menu) are
  the panel's children already, and so open over the window the panel is
  in. Dialogs the main window opens for it (conflicts, a pattern, a mod's
  options) open over the main window.
- **What the status bar says** about something done in a panel that is out
  is said in the main window. It is one line for the Studio and stays so.

### References

- **The graph follows the editor, as the editor moves**, while both are
  showing. Opening an asset in the editor moves the graph to it.
- **The editor does not follow the graph.** Clicking a node moves the graph
  and leaves the editor where it is. The editor moves on the acts that
  already mean "edit this": a double-click, Ctrl+Enter, or *Edit this
  asset* (`ReferencePanel.assetActivated`). Today the editor follows on
  arrival, because building its tree for every asset clicked through would
  make each click wait; with both showing there is no arrival.
- **A pin stops the graph following.** A checkable button in the graph's
  top row, shown while the graph and the editor are both showing. Pinned,
  the graph stays on its asset whatever the editor opens, so one asset's
  surroundings can be kept in view while its neighbours are edited.
  Unpinning moves the graph to the editor's asset. The pin is not
  remembered between launches.
- **With only one of them showing, nothing changes**: each follows the
  other on arrival, and there is no pin.
- **The trail.** The editor and the graph share one history
  (`ui/navigation.py`, `AssetTrail`), so that Back in either takes both
  back. That fits two views of which only one shows. With both showing and
  the editor no longer following the graph, a Back in the graph would move
  a history the editor is also standing on. Suggested: while both are
  showing the graph steps through a history of its own, which
  `ReferencePanel` already keeps when it is not handed one, and takes up
  the shared one again when they are not. To settle in phase 2 (section
  12).

### Mod file

- **It stays current.** Out of its tab the panel is never hidden, so
  `mark_stale` draws the file again at every change to the project: an edit
  in Fields, an Apply in the XML view, a switch in Mod changes, Undo.
  Nothing needs changing for that to be so.
- **What that costs is to be measured.** Drawing the file is writing the
  whole of the mod's text, once for every edit made in the editor beside
  it. On a long mod that may be a wait on each edit that there is not
  today. The bench run of phase 3 says; if it is one, the file is drawn
  again a moment after the last edit and not at each.
- **Typing in two places.** The file tab and the XML view each hold text
  typed and not applied, and side by side both can at once. What happens is
  what happens today, and is seen more: an Apply in one moves the mod under
  the other, which keeps what was typed and says the mod changed while it
  was being typed in (`FilePanel.refresh`). Changing the mod that changes
  go to still asks about text typed in the file first
  (`EditorPanel.settle_elsewhere`).
- ***Show in file*** brings the file's window forward at the line.

### Asset editor

- **It is redrawn at every change already**, whichever tab is in front
  (`EditorPanel.refresh_from_project`), and its XML view waits on which of
  Fields and XML is shown and not on the tab. Nothing about keeping it
  current changes.
- **The search goes with it.** The result list floats over the editor
  itself (`ui/editor_panel.py`, `attach_overlay`), not over the window.
- **Everything that ends in the editor brings its window forward**: Ctrl+P,
  a double-click on a change, *Edit this asset*, the Start tab's way in.
  That is the second rule of section 5 and nothing more.
- **Undo moves it more often.** Undo opens the asset of the step undone
  only when the editor is in front, so that undoing a switch from the
  Export tab does not pull anybody away from it. An editor that is out is
  always showing, and so always moves to the asset. Nobody is pulled away:
  the editor is beside whatever they are looking at.
- **What it adds.** Taking out the file tab or the graph already puts
  either beside the editor. Taking out the editor adds the choice of which
  window keeps the menus, the status bar and Mod changes, and an answer to
  why this tab alone could not be moved.

## 8. Remembering

Kept in the settings beside the main window's own geometry
(`MainWindow.closeEvent`): for each panel, whether it was out, and its
window's geometry.

- **At launch** a panel that was out is put out again, once the main window
  is up.
- **A screen that has gone.** The remembered geometry is used only where it
  lies on a screen that is there. Where it does not, the panel opens in the
  main window, and its geometry is kept for the day the screen is back.
  Opening the window on whatever screen is left would put it on top of the
  main window, unasked.
- **A window remembered as maximised** keeps a size worth restoring to, as
  the main window does (`MainWindow._keep_room_under_maximised`).

## 9. The fold, and a panel that is out

The Mod changes panel folds to a strip at the window's edge and opens again
(*View › Mod changes panel*, F9, the strip itself). That took the place of
the entry that hid the panel, so in the main window the panel has two
states, open and folded.

In or out is a separate thing and comes first:

- **Out, the panel is shown whole.** A window of the panel's own has no
  fold.
- **The fold's control is greyed while the panel is out**, in the menu and
  by its key. The strip is not in the main window to be clicked.
- **Taking the panel out opens it, and it comes back open.** The other
  course is to keep the fold as it was and come back to it. Then a panel
  folded, taken out and brought back returns as a strip: the window that
  was just closed is gone and nothing in the main window looks like it.
  Closing the window says the panel is wanted here, and it is shown here.
  The same holds for a panel that comes back because its screen has gone.

So the panel is in one of three states, open, folded and out, and what is
remembered between launches is one of the three.

## 10. Phases

1. **The window, and Mod changes in it.** The class that holds a panel; the
   View entries, the button and the shortcut; the two rules of section 5,
   with the tests of the tab's position moved onto them; the main window's
   shortcuts answered in a panel's window (section 6, settled here);
   geometry and the state remembered, with the fallback for a missing
   screen; the fold; closing. Tests in `tests/test_ui.py`: out and back
   leaves the panel wired and the splitter as it was, a refused close
   leaves the window open, a geometry off every screen opens the panel in
   the main window, a folded panel taken out comes back open.
2. **References.** The page left in the tab; each following the other while
   both show; the pin; the trail. Tests: each rule of following with the
   graph in and out, and that with one showing it is as today.
3. **Mod file.** The page; *Show in file* and Ctrl+F going to the right
   window; text typed in the file and in the XML view at once. The bench
   run (`tools/bench.py`) for an edit made with the file showing, and the
   wait before drawing if it calls for one.
4. **Asset editor.** The page; everything that ends in the editor bringing
   it forward; Undo; the Edit menu filled from an editor in another window.
   It then holds with the editor out and the graph in, which phase 2 built
   the other way round.

Phase 1 carries the rules the others stand on, and is the largest. None of
the four changes what is read or written, so none wants a census run. All
want a look on two real monitors, with a monitor unplugged between
launches, which no test stands in for.

## 11. What this leaves alone

- **What any panel shows**, and everything in it.
- **Start and Export**, which stay in the main window.
- **The main window's layout** when nothing is out.
- **Dialogs.** They open over the window that asked for them, as now.
- **One monitor.** Nothing changes for somebody who never takes a panel
  out.

## 12. Open questions

1. **Which way the shortcuts are answered** (section 6): a tool window, or
   the actions added to an ordinary one. Tried in phase 1.
2. **The graph's history while both show** (section 7): its own, as
   suggested, or the shared one with Back in the graph moving the editor
   too.
3. **Whether the pin is remembered** between launches. Suggested: no. A
   graph that does not follow, on the first asset opened in a new session,
   reads as broken.
4. **Whether the file is drawn at each edit or a moment after the last**
   (section 7). The bench run of phase 3 says.
5. **A main window with every tab out.** Start, Export and three pages that
   each say their panel is elsewhere. Nothing is wrong with it and nothing
   stops it. Whether it wants saying anywhere is to be seen in use.
6. **What else wants a window.** The Export tab's preview and the file
   tab's outline have been named. The XML view beside Fields is one editor
   shown two ways and not a panel to move, and would be a proposal of its
   own.
