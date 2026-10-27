"""The loadout as a file: where it is kept and what it is called, saving
and autosave, renaming and forking it, the loadouts opened lately, and
whether anything is left unsaved.

``core.changes.Loadout`` is what a loadout holds and how it is written;
this is the window's side of it -- which file, which name, when it is
written, what to say -- held as ``window.files`` (Stage 3.3 of
docs/proposals/consolidation-proposal.md).  Opening a loadout, starting a
new one and switching games stay with the window: each rebuilds every tab
around the loadout, which is the window's wiring.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import builds, disk, filecache, generator, histories, library
from ..core import projects as project_files
from ..core.changes import (
    KIND_EXPORT,
    LOADOUT_SUFFIX,
    LoadoutError,
    list_patch_name,
    name_in_file,
)
from ..core.changes import loadout_name as file_loadout_name
from ..core.i18n import STORED, tr, tr_noop
from .history_dialog import HistoryDialog
from .prompts import FORK_PROFILE, FORK_SEPARATE
from .widgets import ask, wrappable_path


def recent_rows(recent, but=None, live: str = "", live_profile: str = "") -> list:
    """What each of the ``recent`` loadouts is listed as: its name and its
    file, for File ▸ Open recent and for the Start tab's list alike.

    ``but`` is a loadout to leave out: the open one, where it is named
    beside the list already.  ``live`` is the file of the loadout the game
    loads, which is said of it, with ``live_profile`` where that is worth
    naming.

    The two lists each had this to themselves and did not agree: the Start
    tab cut the suffix out of the middle of a name, and the menu did not say
    which one the game loads.
    """
    rows = []
    for other in recent:
        if but is not None and str(other) == str(but):
            continue
        name = file_loadout_name(other)
        is_live = bool(live) and os.path.normcase(str(other)) == os.path.normcase(str(live))
        if is_live and live_profile:
            name = tr("%(loadout)s: what the game loads (%(profile)s)",
                      loadout=name, profile=live_profile)
        elif is_live:
            name = tr("%s: what the game loads", name)
        rows.append((name, str(other)))
    return rows


class LoadoutFiles:
    """The window's files, moved off it; ``window`` is the MainWindow."""

    RECENT_KEY = "recent_loadouts"
    # More than are shown: the list is of one game's loadouts.
    RECENT_MAX = 12
    # How many to offer on the Start tab, and how far back through the folder
    # to look for them.
    RECENT_SHOWN = 8
    RECENT_SCANNED = 80

    def __init__(self, window) -> None:
        self.window = window
        # The loadout's typed details as last saved: see unsaved_work.
        self._saved_details = None
        # And every change and op it held then, or that a folder read has
        # brought since: see unsaved_work and took_reading.
        self._saved_standing: dict = {}
        # A save that waits for the loadout's projects to be let go of:
        # see save_when_free.
        self._save_when_free = False

    def loadouts_dir(self) -> Path:
        from ..core import places

        return places.loadouts_dir()

    def loadout_home(self, title: str, own_is_taken: bool = False) -> Path:
        """Where a loadout called ``title`` would live.

        Named rather than slugified: this is shown to the user now, as the space
        they are working in, so it should read like the name they gave it.  A
        name already taken by another loadout gets a number, and the loadout's
        own current file does not count as taken -- renaming something to what
        it is already called is not a clash -- unless ``own_is_taken``: a fork
        is a second file, so one under its own name, in any letter case, would
        be written over the loadout it came from, written yet or not.
        """
        folder = self.loadouts_dir()
        stem = generator.safe_folder_name(title, "Anno Mod")
        candidate = folder / (stem + LOADOUT_SUFFIX)

        def taken(path: Path) -> bool:
            # A Windows path equals its own name in any letter case.
            if path == self.window.loadout_path:
                return own_is_taken
            return path.exists()

        n = 2
        while taken(candidate):
            candidate = folder / ("%s (%d)%s" % (stem, n, LOADOUT_SUFFIX))
            n += 1
        return candidate

    def default_loadout_path(self) -> Path:
        """Where a loadout with no home of its own would go."""
        return self.loadout_home(self.window.loadout.own.name or "Anno Mod")

    @staticmethod
    def name_of(path) -> str:
        """What a loadout file is called -- see :func:`core.changes.loadout_name`."""
        return file_loadout_name(path)

    def loadout_name(self) -> str:
        """What this loadout is called, read off where it lives."""
        if self.window.loadout_path is None:
            return ""
        return self.name_of(self.window.loadout_path)

    def name_loadout(self, title: str) -> Path:
        """Settle where this loadout lives, without writing it yet.

        Called the moment a way of working is chosen, because that is the moment
        there is a loadout to place: work that does not know where it belongs is
        work you have not really decided to do.  Nothing is written -- somebody
        who opens a card to see what is behind it and quits should not leave a
        file behind -- but from here on the answer exists, is on screen, and
        Ctrl+S has somewhere to go instead of a dialog.
        """
        was = self.loadout_name()
        self.window.loadout_path = self.loadout_home(title)
        self.window.on_disk = False
        self.name_list_patch(was)
        self.window.start.refresh_source_panel()
        self.window.update_title()
        return self.window.loadout_path

    def list_patch_follows(self, was: str = "") -> bool:
        """Whether the loadout's own mod is named after the loadout, the
        patch over its whole list that nobody has named (:meth:`name_list_patch`):
        the rename box says so, where it said no mod inside was renamed
        (R3-47, hands-on run 3)."""
        from ..core import games

        loadout = self.window.loadout
        if not loadout.own.loads_last or loadout.stands_for() is not None:
            return False
        unnamed = {"", "My Anno Mod", "%s mod list patch" % games.get(loadout.game).label}
        if was:
            unnamed.add(list_patch_name(was))
        return loadout.own.name in unnamed

    def name_list_patch(self, was: str = "") -> bool:
        """Name the loadout's own mod after the loadout, where it is the patch
        over the loadout's whole list and nobody has named it: still the
        name a new mod starts with, the one the loadout ``was`` gave it, or
        the one every list patch had before it took its loadout's.  Whether
        it was named.  Two loadouts' patches are two mods, which build into
        two folders under two ModIDs (:func:`core.changes.list_patch_name`)."""
        loadout = self.window.loadout
        name = self.loadout_name()
        if not name or not self.list_patch_follows(was) or loadout.own.name == list_patch_name(name):
            return False
        loadout.own.name = list_patch_name(name)
        self.window.output.load_loadout_fields()
        # Its row in the Mod changes panel was drawn under the name it had a
        # moment ago, and so was its tab over an asset in the XML view.
        self.window.changes_panel.refresh()
        self.window.file_panel.mark_stale()
        return True

    def ensure_loadout_file(self) -> None:
        """Write the loadout the first time there is something to put in it."""
        if self.window.loadout_path is None or self.window.on_disk:
            return
        try:
            self.window.loadout_path.parent.mkdir(parents=True, exist_ok=True)
            self.follow_projects(self.window.loadout.save(self.window.loadout_path))
        except (OSError, LoadoutError):
            # Not worth interrupting an edit for; saving properly will say so.
            return
        self.window.live.wrote_loadout(self.window.loadout_path)
        self.window.on_disk = True
        self.remember_recent(self.window.loadout_path)
        # What was just written is the whole loadout, so nothing is left over
        # -- which the window used to go on claiming, wearing the unsaved mark
        # over a file holding every word of what it was marking.
        self.mark_saved()
        self.window.update_title()
        # And its card, which said "(new)" and where it would be written.
        self.window.start.refresh_source_panel()

    def save_loadout(self, ask: bool = False) -> bool:
        """Save to the loadout's own file, asking for a location if needed."""
        path = self.window.loadout_path
        was = None
        if ask or path is None:
            start = str(path) if path else str(self.default_loadout_path())
            chosen, _f = QFileDialog.getSaveFileName(
                self.window, tr("Save loadout"), start, tr("Anno Modding Studio loadouts (*.annoload)")
            )
            if not chosen:
                return False
            path = Path(chosen)
            if self.window.on_disk and not disk.same_file(path, self.window.loadout_path):
                # Another file now: its own mod is its own.
                was = self.window.loadout.projects.get("")
                self.window.loadout.own_project_anew()
        try:
            self.follow_projects(self.window.loadout.save(path))
        except (OSError, LoadoutError) as exc:
            QMessageBox.critical(self.window, tr("Could not save"), str(exc))
            return False
        now = self.window.loadout.projects.get("")
        if was is not None and now is not None:
            # The own mod is saved under another name, and what its project
            # built goes with it: the copy carries on building it, and the
            # file it was saved from is stopped if it tries.
            with contextlib.suppress(OSError):
                library.follow_project(self.window.where.library_dir(), was.path, now.path)
        self.window.live.wrote_loadout(path)
        self.window.loadout_path = path
        self.window.on_disk = True
        self.mark_saved()
        self.remember_recent(path)
        self.window.settings.setValue("last_loadout_dir", str(path.parent))
        self.window.status_label.say(tr("Loadout saved to %s", path), path)
        self.window.update_title()
        # Its card said "(new)", and where it would be written, after it was
        # (R3-7, hands-on run 3).
        self.window.start.refresh_source_panel()
        return True

    def save_when_free(self) -> None:
        """Save what is no typed work -- a mod following where a conversion
        or a rename moved its folder -- at once, or, while a project of the
        loadout is held, once it is not: the save would be refused for that
        project, and opening a loadout another one had renamed a mod in
        said so in a box (R3-16, hands-on run 3).  Its read-back lets go of
        it moments later (:meth:`took_projects`)."""
        loadout = self.window.loadout
        if any(loadout.held(origin) for origin in loadout.project_origins()):
            self.mark_unsaved()
            self._save_when_free = True
            return
        self.autosave()

    def follow_projects(self, moved) -> None:
        """What the library built from each project a save moved to follow
        its mod's name goes with it (:meth:`Loadout.save`): ``moved`` is
        each one's path before, to its path now."""
        for before, after in moved.items():
            # A label, not worth failing a save over.
            with contextlib.suppress(OSError):
                library.follow_project(self.window.where.library_dir(), before, after)

    def autosave(self) -> None:
        """Persist without prompting, choosing a home the first time."""
        if self.window.loadout_path is None:
            self.window.loadout_path = self.default_loadout_path()
        first = not self.window.on_disk
        try:
            self.window.loadout_path.parent.mkdir(parents=True, exist_ok=True)
            self.follow_projects(self.window.loadout.save(self.window.loadout_path))
        except (OSError, LoadoutError) as exc:
            QMessageBox.warning(
                self.window,
                tr("Could not save the loadout"),
                "%s\n\n%s" % (wrappable_path(self.window.loadout_path), exc),
            )
            return
        self.window.live.wrote_loadout(self.window.loadout_path)
        self.window.on_disk = True
        self.remember_recent(self.window.loadout_path)
        self.mark_saved()
        self.window.update_title()
        if first:
            # Its card said "(new)" until something else redrew it (R3-7).
            self.window.start.refresh_source_panel()

    def mark_saved(self) -> None:
        """The loadout on disk and the loadout in hand now agree."""
        self.window.dirty = False
        self._save_when_free = False
        self._saved_details = self.window.loadout.typed_details()
        self._saved_standing = self.window.loadout.standing()

    def took_reading(self, origins) -> None:
        """The mods at ``origins`` were just read from their folders.

        What a reading brings is not work, and neither is what it no longer
        brings: a mod updated on disk has fewer ops because its author took
        some out.  So what those mods hold now is what there is to take away
        from here on.  Whether each is on, and its note, stay as they were
        saved: a reading keeps a switch you flipped, and it is still flipped.

        What you made inside one of those mods is kept by the reading and is
        not the reading's: filed here with the rest, it stopped being counted
        as unsaved the moment its mod's folder was read.
        """
        origins = set(origins)
        now = self.window.loadout.standing()
        yours = self._yours()
        kept = {
            key: was for key, was in self._saved_standing.items()
            if key[0] not in origins or key in now
        }
        for key, state in now.items():
            if key[0] in origins and key not in yours:
                kept.setdefault(key, state)
        self._saved_standing = kept

    def took_projects(self, origins) -> None:
        """The mods at ``origins`` were just read back from their projects,
        which another loadout changed (:mod:`core.readback`).

        What they hold now is what is on disk, made here or not: it is what
        their projects say, and none of it is work of this loadout's.  What
        they no longer hold was not taken away here either.
        """
        origins = set(origins)
        if not origins:
            return
        now = self.window.loadout.standing()
        kept = {key: was for key, was in self._saved_standing.items() if key[0] not in origins}
        kept.update((key, state) for key, state in now.items() if key[0] in origins)
        self._saved_standing = kept
        loadout = self.window.loadout
        if self._save_when_free and not any(
                loadout.held(origin) for origin in loadout.project_origins()):
            self._save_when_free = False
            if self.window.on_disk:
                self.autosave()

    def _yours(self) -> set:
        """The keys of the changes and ops made here, whichever mod they
        are in: what no reading brought."""
        loadout = self.window.loadout
        yours = {change.key for change in loadout.changes.values() if change.authored}
        yours.update(op.key for op in loadout.ops if op.authored)
        return yours

    def read_again(self, origins, before) -> None:
        """The mods at ``origins`` were just read again from what the
        loadout writes for them, after an edit; ``before`` is what the
        loadout held until then (:meth:`Loadout.standing`).

        What an op of a loaded mod comes to now is no more work than it was
        when its folder was read, and what it no longer comes to was not
        taken away by anybody.  What an op of yours comes to is work, saved
        or not as it was.
        """
        origins = set(origins)
        if not origins:
            return
        loadout = self.window.loadout
        now = loadout.standing()
        yours = self._yours()
        kept = dict(self._saved_standing)
        for key in before:
            if key[0] in origins and key not in now:
                kept.pop(key, None)
        for key, state in now.items():
            if key[0] in origins and key not in before and key not in yours:
                kept.setdefault(key, state)
        self._saved_standing = kept

    def taken_away(self) -> int:
        """How many changes and ops the loadout held have been taken out or
        switched since it was saved: see :meth:`Loadout.taken_since`."""
        return self.window.loadout.taken_since(self._saved_standing)

    def notes_edited(self) -> int:
        """How many changes and ops the loadout held, and that were not made
        here, have another note than when it was saved: see
        :meth:`Loadout.noted_since`."""
        return self.window.loadout.noted_since(self._saved_standing)

    def made_unsaved(self) -> int:
        """How many changes made here are new, or say something else, since
        the loadout was saved: see :meth:`Loadout.made_since`.  All of
        them, for a loadout that has never been saved."""
        return self.window.loadout.made_since(self._saved_standing)

    def mark_unsaved(self) -> None:
        """The loadout in hand has moved on from the one on disk.

        The one way the mark goes on, as :meth:`mark_saved` is the one way it
        comes off: the title's ``*`` and the question on the way out both read
        it, through :meth:`unsaved_work`.

        And the title is drawn again here, not by each caller: a dozen of
        them left it to the next thing that drew it, and closing asked about
        unsaved work under a title with no ``*`` on it.
        """
        self.window.dirty = True
        self.window.update_title()

    def unsaved_work(self) -> bool:
        """Whether closing now would lose something worth keeping.

        One question, asked in the two places that have to agree: the ``*`` in
        the title bar, and the prompt on the way out.  They used to be two
        different questions.  The mark meant "this differs from what is on
        disk"; the prompt meant that *and* "and at least one of the
        differences is a change you made".  Everything else a loadout holds --
        the mod's name, version, creator and description, its mod.io links,
        which of your mods an edit lands in -- wore the mark and then went
        without a word.  Typing a description on the Export tab and closing
        the window lost the description, and the title bar had said so for the
        whole time in between.

        What is *not* work, still: a mods folder read in and nothing else
        done.  Those thousands of changes are on disk already and the menu
        entry that read them will read them again, so they neither raise the
        mark nor stop you leaving -- which is the judgement the prompt has
        always made, now made once and in one place.

        Taking one of those changes *out* is work, though, and so is
        switching one off.  The folder still has it, so nothing but the
        loadout says it is gone: a revert of eighteen changes read from the
        mod's own folder left no mark, and closing the window put all
        eighteen back without a word.  The same for a change of your own that
        was saved and then reverted, where it was the last one.

        And a note typed on one of them is work of the same kind: the folder
        does not have the note.
        """
        if not self.window.dirty:
            return False
        return (
            bool(self.window.loadout.authored_count)
            or self.window.loadout.details_moved(self._saved_details)
            or bool(self.taken_away())
            or bool(self.notes_edited())
        )

    def confirm_discard(self) -> bool:
        """Returns True if it is safe to throw the current loadout away."""
        # ModOps typed in the XML view and not applied are in no loadout yet:
        # first whether they are to be, then whether the loadout is saved.
        if not self.window.editor.settle_typed():
            return False
        # And what was typed in the mod's file.
        if not self.window.file_panel.settle_typed():
            return False
        if not self.unsaved_work():
            return True
        # What the question says is not saved has to be what is not: the
        # changes made or typed again since the save, and not every change
        # made here, which with ten saved and one more made said eleven.
        # And of the details, which kind: a mod's own are named beside any
        # count, the switches where there is none, and what merely follows
        # from other work (which mod an edit lands in) not at all.
        answer = self.window.prompts.ask_save_first(
            self.made_unsaved(), self.taken_away(),
            self.window.loadout.switches_moved(self._saved_details),
            self.window.loadout.mod_details_moved(self._saved_details),
            self.notes_edited(),
            projects=self.window.loadout.projects_to_save(),
        )
        if answer == "save":
            return self.save_loadout()
        return answer == "discard"

    def ask_rename_loadout(self) -> bool:
        """Start tab ▸ Rename…: call this loadout something else."""
        if self.window.loadout_path is None:
            return False
        was = self.loadout_name()
        name, ok = self.window.prompts.ask_loadout_name(
            was, mine=self.window.loadout.stands_for() is not None,
            patch_follows=self.list_patch_follows(was),
        )
        return self.rename_loadout(name) if ok else False

    def rename_loadout(self, title: str) -> bool:
        """Call this loadout something else, moving its file if it has one.

        Where the loadout *is* a mod -- see :meth:`Loadout.stands_for` --
        its name is the mod's name and the two are kept in step.  Otherwise it
        is a workspace holding somebody else's mods, and naming it says nothing
        about what is inside.

        A rename is complete in itself.  The file moves at once, so the mod's
        new name is written into it at once too, and nothing else is: the
        name used to wait in the unsaved loadout, and Discard on the way out
        left a file called the new name holding a mod called the old one.
        """
        title = (title or "").strip()
        if not title:
            return False
        target = self.loadout_home(title)
        was_path = self.window.loadout_path
        # Windows paths compare without case: Main to main is the same file,
        # and still a rename.
        if target == was_path and target.name == was_path.name:
            return False
        if self.window.on_disk and self.window.loadout_path is not None and self.window.loadout_path.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                self.window.loadout_path.rename(target)
            except OSError as exc:
                QMessageBox.warning(
                    self.window, tr("Could not rename the loadout"),
                    "%s\n\n%s" % (wrappable_path(target), exc),
                )
                return False
        if self.window.loadout_path is not None:
            # A label, not worth failing a rename over.
            with contextlib.suppress(OSError):
                library.follow_live(self.window.where.library_dir(), self.window.loadout_path, target)
            self.window.live.follow_rename(self.window.loadout_path, target)
        self.window.loadout_path = target
        if self.window.on_disk:
            # Under its new name first: the old, a case away from it, is the
            # same file to Windows, and the list kept showing it (R3-7).
            self.remember_recent(target)
        was_patch = self.window.loadout.own.name
        if self.window.loadout.stands_for() is not None:
            # The mod the loadout is: the one of yours it carries on with, or
            # its own.
            home = self.window.loadout.home_layer()
            was = home.name if home is not None else self.window.loadout.own.name
            if home is not None:
                home.name = title
            else:
                self.window.loadout.own.name = title
            self.window.output.load_loadout_fields()
            # The mod's row in the Mod changes panel has its name on it, and
            # so does its tab over an asset open in the XML view.
            self.window.changes_panel.refresh()
            self.window.file_panel.mark_stale()
            self.window.editor.refresh_from_loadout()
            if not self._write_name(target, home.origin if home is not None else "", title, was):
                # No file yet, or one that could not be written: the name
                # goes in with everything else, the next time it is saved.
                self.mark_unsaved()
        elif self.name_list_patch(self.name_of(was_path) if was_path is not None else ""):
            # The patch over its list was named after it, and is again.
            self.window.editor.refresh_from_loadout()
            if not self._write_name(target, "", self.window.loadout.own.name, was_patch):
                self.mark_unsaved()
        self.window.start.refresh_source_panel()
        self.window.update_title()
        return True

    def _write_name(self, path, origin: str, title: str, was: str) -> bool:
        """Write the new name of the mod at ``origin``, called ``was`` until
        now, into the loadout's file, and nothing else of what is unsaved;
        whether it is there now.  Its project goes to a file named for it."""
        if not self.window.on_disk:
            return False
        try:
            move = name_in_file(path, origin, title)
        except (OSError, ValueError):
            return False
        if move is not None:
            # The project holds the name now, at a revision this loadout
            # wrote, in a file named for it: the next save writes it again
            # only where more moved.
            self.window.loadout.name_saved(origin, was, move)
            self.follow_projects({move.before: move.after} if move.before != move.after else {})
        # The name is saved, so it is not what the unsaved mark is about:
        # whatever else was unsaved still is, and nothing else is now.
        self._saved_details = self.window.loadout.details_named(
            self._saved_details, origin, title,
        )
        return True

    def fork_loadout(self, name: str = "", kind: str = "") -> bool:
        """A copy of this loadout under a new name, opened in its place.

        Save As, with a name and a sentence: the copy is where something gets
        tried -- a collection on top of what you play, a different set for a
        playthrough -- and the loadout it came from stays exactly as it was
        last saved, and stays live if it was.  Nothing on disk moves; the
        fork is live only once it is made so.
        """
        if self.window.loadout_path is None:
            return False
        was = self.loadout_name()
        if not kind:
            kind = self.window.prompts.ask_fork_kind(was, self.window.live.profile_shown())
        if kind == FORK_PROFILE:
            return self.window.live.new_profile(name)
        if kind != FORK_SEPARATE:
            return False
        if not name:
            name = self.window.prompts.ask_fork_name(was)
        name = (name or "").strip()
        if not name:
            return False
        target = self.loadout_home(name, own_is_taken=True)
        unsaved = self.window.dirty and self.window.on_disk
        # A fork is of something: one never written is written first, or the
        # copy would be all there is.
        self.ensure_loadout_file()
        # The copy's own profile, if it was named after the loadout it came
        # from, is named after the copy -- a fork of Main holding a profile
        # called Main would say it was Main.
        renamed = self.rename_copied_profile(was, self.name_of(target))
        # The fork's own mod is the fork's, a mod of its own with a ModID of
        # its own; the patches stay shared, since a patch says how a mod
        # should be wherever it is used.
        own = self.window.loadout.own_mod_anew(self.name_of(target), came_from=was)
        try:
            self.follow_projects(self.window.loadout.save(target))
        except (OSError, LoadoutError) as exc:
            self.window.loadout.own_mod_back(own)
            if renamed:
                self.window.loadout.rename_profile(self.name_of(target), was)
            QMessageBox.critical(self.window, tr("Could not fork the loadout"), str(exc))
            return False
        live = disk.same_file(self.window.live.live_loadout(), self.window.loadout_path)
        self.window.loadout_path = target
        self.window.profile_renames.clear()
        self.window.on_disk = True
        self.mark_saved()
        self.remember_recent(target)
        self.window.settings.setValue("last_loadout_dir", str(target.parent))
        if live:
            said = tr("Forked %(was)s as %(fork)s. The original is untouched and "
                      "is still what the game loads.", was=was, fork=self.name_of(target))
        else:
            said = tr("Forked %(was)s as %(fork)s. The original is untouched.",
                      was=was, fork=self.name_of(target))
        if unsaved:
            said = tr("%s What you had not saved is in the fork only.", said)
        self.window.status_label.setText(said)
        # Its own mod is named after it now (Loadout.own_mod_anew).
        self.window.output.load_loadout_fields()
        self.window.editor.refresh_target()
        self.window.refresh_truth(say=False)
        self.window.start.refresh_source_panel()
        self.window.update_title()
        return True

    def rename_copied_profile(self, was: str, now: str) -> bool:
        """Name the copy's profile called ``was`` -- the loadout it was copied
        from -- ``now``; whether it did."""
        if not was or not now or was == now or self.window.loadout.find_profile(was) is None:
            return False
        try:
            self.window.loadout.rename_profile(was, now)
        except ValueError:
            return False
        return True

    def recent_loadouts(self, game: str = ""):
        """The loadouts worked on lately, newest first, that still exist.

        Read off the loadouts folder as well as the settings.  It used to be
        only what this machine's settings remembered, which meant a cleared
        settings file emptied it and a loadout saved by any other route never
        appeared -- an empty list beside a folder with a hundred loadouts in
        it, with nothing on screen to say what the list was of.

        ``game`` limits it to loadouts built against that game's assets, which
        is the only kind that can be opened without swapping the loaded files
        out from under everything.
        """
        candidates = self._remembered()
        try:
            candidates += sorted(
                self.loadouts_dir().glob("*" + LOADOUT_SUFFIX),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[: self.RECENT_SCANNED]
        except OSError:
            pass

        dated = []
        seen = set()
        for path in candidates:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                when = path.stat().st_mtime
            except OSError:
                continue  # gone since it was remembered
            if game and self._loadout_game(path) not in ("", game):
                continue
            dated.append((when, path))
        dated.sort(reverse=True)
        found = [path for _when, path in dated]
        # The loadout the game loads comes first, whatever was saved after it:
        # a conversion writes a loadout for each of your profiles once the one
        # you play is made live, and those used to push it off the list.
        live = self.window.live.live_for(game)
        if live is not None:
            found = [live] + [path for path in found if not disk.same_file(path, live)]
        return found[: self.RECENT_SHOWN]

    def recent_rows(self, game: str = "") -> list:
        """:meth:`recent_loadouts` as File ▸ Open recent lists them: see
        :func:`recent_rows`."""
        return recent_rows(
            self.recent_loadouts(game),
            live=str(self.window.live.live_for(game) or ""),
            live_profile=self.window.live.live_profile_shown(game),
        )

    def _loadout_game(self, path) -> str:
        """Which game a loadout file was built against, read cheaply.

        Off the head of the file rather than by loading it: a loadout holding a
        whole mods folder is megabytes of changes, and this runs over every
        loadout in the folder to draw a list of eight.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                head = handle.read(8192)
        except OSError:
            return ""
        found = re.search(r'"game"\s*:\s*"([^"]*)"', head)
        return found.group(1) if found else ""

    def _remembered(self) -> list:
        """The loadouts the settings remember, newest first: there or not."""
        raw = self.window.settings.value(self.RECENT_KEY) or []
        if isinstance(raw, str):
            raw = [raw]
        return [Path(str(entry)) for entry in raw]

    def _remember(self, first=None) -> None:
        """Write what the settings remember: ``first``, then what they held
        that is still there, as many as they are to hold.

        From what they held, and not from :meth:`recent_loadouts`: that is
        the ones that are shown, of one game, so nothing past them was ever
        kept, and a loadout outside the loadouts folder -- which nothing but
        the settings knows of -- was forgotten after a few newer ones.
        """
        kept = [] if first is None else [str(first)]
        seen = {entry.lower() for entry in kept}
        for other in self._remembered():
            if str(other).lower() in seen or not other.exists():
                continue
            seen.add(str(other).lower())
            kept.append(str(other))
        self.window.settings.setValue(self.RECENT_KEY, kept[: self.RECENT_MAX])

    def remember_recent(self, path) -> None:
        self._remember(path)

    def open_recent(self, path) -> bool:
        """Open a loadout by path, the way the Start tab's list does."""
        path = Path(path)
        if not path.exists():
            QMessageBox.information(
                self.window,
                tr("That loadout has gone"),
                tr("%s is no longer there, so it has been taken off the list.",
                   wrappable_path(path)),
            )
            self._remember()
            self.window.start.refresh_source_panel()
            return False
        return self.window.load_loadout(path)

    def show_history(self) -> None:
        """The two histories: the loadout's, and the project of the mod in
        question -- the Export tab's, when that tab is open, or the one
        *Changes go to* names."""
        window = self.window
        if window.tabs.currentWidget() is window.output:
            origin = window.output.current_origin()
        else:
            origin = window.loadout.editing_origin()
        dialog = HistoryDialog(window.loadout, window, origin=origin,
                               loadout_name=self.loadout_name(), having=self.loadouts_having)
        dialog.exec()
        if dialog.restored:
            # A restore takes its own copy of what it replaced, in the history.
            self.window.forget_undo()
            # Before the rows are drawn: a mod put back is read again.
            self.window.readings.check()
            self.window.changes_panel.refresh()
            self.window.file_panel.mark_stale()
            self.window.editor.refresh_from_loadout()
            self.window.mark_output_dirty()
            self.window.start.refresh_source_panel()
        if dialog.checkpoint_taken or dialog.restored:
            self.mark_unsaved()
            self.autosave()
            self.window.update_title()
        if dialog.mod_restored:
            # A loadout's list restored alone leaves every mod's work as it was.
            self._offer_export_after_restore()

    def loadouts_having(self, origin: str) -> list:
        """The other loadouts that have the project of ``origin``, by name,
        from the files cache: a restore of it changes the mod in each."""
        ref = self.window.loadout.projects.get(origin)
        if ref is None:
            return []
        return [
            self.name_of(path) for path in filecache.loadouts_with_project(
                project_files.where(ref.path), self.window.loadout.game,
                excluding=self.window.loadout_path,
            )
        ]

    def _offer_export_after_restore(self) -> None:
        """Restoring only changes the working set; the mod on disk is untouched."""
        target = self.window.output.export_target()
        answer = ask(
            self.window,
            tr("Export the restored checkpoint?"),
            tr("The change set has been rolled back, but the exported mod on "
               "disk still holds what it had before.\n\nExport now to %s?",
               wrappable_path(target)),
            tr("&Export now"),
            cancel=tr("&Not now"),
        )
        if answer:
            self.window.output.export_mod(confirm_overwrite=False)

    def on_mod_exported(self, folder: str, origin=None) -> None:
        """Exporting is a milestone, so record a checkpoint of the mod that
        was written, ``origin``, in its project's history and one of the
        loadout's list, and save the loadout.  Each only where it moved
        since its last: the same mod exported again is the same release.

        The checkpoint gives the mod's version with its build: another mod
        picked on the Export tab has a number of its own.  A list is several
        mods written at once, each of which takes a checkpoint of its own,
        and the loadout's is labelled as None's: an export that was not of
        one mod, which is of the mod the loadout releases.
        """
        loadout = self.window.loadout
        mods = list(origin) if isinstance(origin, (list, tuple)) else None
        if origin is None or mods is not None:
            origin = loadout.home or ""
        name = self.loadout_name()
        for each in mods if mods is not None else [origin]:
            if loadout.held(each):
                # Its project is not written from here, so neither is a
                # checkpoint of it: the save would be refused for it.
                continue
            loadout.release_checkpoint(self._exported_label(each), KIND_EXPORT, origin=each,
                                       loadout=name)
        histories.take_release(loadout, self._exported_label(origin), KIND_EXPORT, taken_in=name,
                               label_of=self._exported_label)
        # An export is a release, and what the next one is measured from.
        self.window.output.show_suggestion()
        self.autosave()
        self.window.status_label.say(tr(
            "Exported to %(folder)s   ·   loadout saved to %(loadout)s",
            folder=folder, loadout=self.window.loadout_path,
        ), folder)

    def _exported_label(self, origin: str) -> str:
        """What an export of ``origin`` is called in the history: its
        version, and its build where it has one."""
        loadout = self.window.loadout
        version = loadout.version_of(origin) or "1.0"
        build = builds.number(loadout, origin)
        # Kept in the history as it is, and shown translated there.
        return (
            tr_noop("Exported v%(version)s, build %(build)d", context=STORED)
            % {"version": version, "build": build}
            if build else
            tr_noop("Exported v%s", context=STORED) % version
        )

    def on_loadout_edited(self) -> None:
        self.mark_unsaved()
        self.ensure_loadout_file()
        self.window.update_title()
